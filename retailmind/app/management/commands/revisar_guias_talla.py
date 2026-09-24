"""
Revisa (y con --apply crea o ajusta) las guías de talla que usan las facturas
de compra de compras/facturas/*.json antes de cargarlas con
cargar_productos_factura.

Qué revisa, por cada guía nombrada en "guias_talla" del JSON:
  - Que exista para la marca. Si no existe y hay una tabla de referencia para
    esa marca y nombre (_REFERENCIA, copiada de las guías Nike que había en la
    BD en mayo de 2026), la crea con esa tabla.
  - Guía INFANTIL: que las tallas juveniles lleven la Y en la columna US
    ('1.5' → '1.5Y'). Las de bebé ya llevan la C ('11C').
  - Que estén todas las tallas US que traen las facturas. Si falta una y la
    referencia la tiene, la agrega; si no, lo informa (no inventa
    equivalencias CL/CM).

Las tallas de los productos que YA usan la guía no se tocan: el ajuste solo
cambia la guía. Se informa cuántos productos la usan, porque las guías también
salen por la API externa (ecommerce).

Uso (desde retailmind/):
    python manage.py revisar_guias_talla "compras/facturas/EQUINOX_*.json"
    python manage.py revisar_guias_talla "compras/facturas/EQUINOX_*.json" --apply
"""
import glob
import json
import re
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max

from app.models import AtributoOpcion, GuiaTalla, GuiaTallaItem, GuiaTallaProducto, Producto

_RE_TALLA = re.compile(r'^(\d+(?:[.,]\d+)?)\s*([CY]?)$')

# Tablas de referencia: (orden, cl, us, eu, uk, br, cm). Copiadas de las guías
# NIKE HOMBRE / MUJER / INFANTIL de la BD (mayo 2026), con la Y agregada a las
# tallas juveniles de INFANTIL.
_REFERENCIA = {
    ('NIKE', 'NIKE HOMBRE'): [
        (0, '36', '4', '', '', '', '22.5'), (1, '36.5', '4.5', '', '', '', '23'),
        (2, '37', '5', '', '', '', '23.5'), (3, '37.5', '5.5', '', '', '', '24'),
        (4, '38', '6', '', '', '', '24'), (5, '38.5', '6.5', '', '', '', '24.5'),
        (6, '39', '7', '', '', '', '25'), (7, '39.5', '7.5', '', '', '', '25.5'),
        (8, '40', '8', '', '', '', '26'), (9, '40.5', '8.5', '', '', '', '26.5'),
        (10, '41', '9', '', '', '', '27'), (11, '41.5', '9.5', '', '', '', '27.5'),
        (12, '42', '10', '', '', '', '28'), (13, '42.5', '10.5', '', '', '', '28.5'),
        (14, '43', '11', '', '', '', '29'), (15, '43.5', '11.5', '', '', '', '29.5'),
        (16, '44', '12', '', '', '', '30'), (17, '44.5', '12.5', '', '', '', '30.5'),
        (18, '45', '13', '', '', '', '31'), (19, '45.5', '13.5', '', '', '', '31.5'),
        (20, '46', '14', '', '', '', '32'), (21, '46.5', '14.5', '', '', '', '32.5'),
    ],
    ('NIKE', 'NIKE MUJER'): [
        (0, '35', '5', '', '', '', '22'), (1, '35.5', '5.5', '', '', '', '22.5'),
        (2, '36', '6', '', '', '', '23'), (3, '36.5', '6.5', '', '', '', '23.5'),
        (4, '37', '7', '', '', '', '24'), (5, '37.5', '7.5', '', '', '', '24.5'),
        (6, '38', '8', '', '', '', '25'), (7, '38.5', '8.5', '', '', '', '25.5'),
        (8, '39', '9', '', '', '', '26'), (9, '39.5', '9.5', '', '', '', '26.5'),
        (10, '40', '10', '', '', '', '27'), (11, '40.5', '10.5', '', '', '', '27.5'),
        (12, '41', '11', '', '', '', '28'),
    ],
    ('NIKE', 'NIKE INFANTIL'): [
        (0, '16', '2C', '', '', '', '8'), (1, '17.5', '3C', '', '', '', '9'),
        (2, '18.5', '4C', '', '', '', '10'), (3, '20', '5C', '', '', '', '11'),
        (4, '21', '6C', '', '', '', '12'), (5, '22.5', '7C', '', '', '', '13'),
        (6, '24', '8C', '', '', '', '14'), (7, '25', '9C', '', '', '', '15'),
        (8, '26', '10C', '', '', '', '16'), (9, '26.5', '10.5C', '', '', '', '16.5'),
        (10, '27', '11C', '', '', '', '17'), (11, '27.5', '11.5C', '', '', '', '17.5'),
        (12, '28.5', '12C', '', '', '', '18'), (13, '29', '12.5C', '', '', '', '18.5'),
        (14, '30', '13C', '', '', '', '19'), (15, '30.5', '13.5C', '', '', '', '19.5'),
        (16, '31', '1Y', '', '', '', '20'), (17, '32', '1.5Y', '', '', '', '20.5'),
        (18, '32.5', '2Y', '', '', '', '21'), (19, '33', '2.5Y', '', '', '', '21.5'),
        (20, '34', '3Y', '', '', '', '22'), (21, '34.5', '3.5Y', '', '', '', '22.5'),
        (22, '35', '4Y', '', '', '', '23'), (23, '35.5', '4.5Y', '', '', '', '23.5'),
        (24, '36', '5Y', '', '', '', '24'), (25, '37', '5.5Y', '', '', '', '24.5'),
        (26, '37.5', '6Y', '', '', '', '25'), (27, '38', '6.5Y', '', '', '', '25.5'),
        (28, '38.5', '7Y', '', '', '', '26'),
    ],
}


def _clave(talla):
    """(número, sufijo) de una talla US: '1.5Y' → ('1.5', 'Y'), '11C' → ('11', 'C')."""
    s = str(talla or '').strip().upper()
    m = _RE_TALLA.match(s)
    if not m:
        return (s, '')
    return (format(Decimal(m.group(1).replace(',', '.')).normalize(), 'f'), m.group(2))


def _clave_busqueda(talla):
    """Para buscar una talla en la guía: la C separa bebé de adulto; la Y no
    cambia la talla ('1.5Y' es la misma fila que '1.5')."""
    num, suf = _clave(talla)
    return (num, 'C' if suf == 'C' else '')


def _texto(numero, sufijo):
    return f'{numero}{sufijo}'


class Command(BaseCommand):
    help = ('Revisa las guías de talla que usan las facturas JSON; con --apply las '
            'crea o ajusta (Y en juveniles, tallas faltantes).')

    def add_arguments(self, parser):
        parser.add_argument('archivos', nargs='+', help='JSON de facturas (acepta comodines)')
        parser.add_argument('--apply', action='store_true', help='Escribe los cambios')

    def handle(self, *args, **opts):
        rutas = []
        for patron in opts['archivos']:
            encontrados = sorted(glob.glob(patron)) if any(c in patron for c in '*?[') else [patron]
            if not encontrados:
                raise CommandError(f'Ningún archivo calza con {patron}')
            rutas.extend(encontrados)

        # (marca, nombre de guía) → {'tipo': clave del JSON, 'tallas': {clave: texto factura}}
        requeridas = {}
        for ruta in rutas:
            data = json.loads(Path(ruta).read_text(encoding='utf-8'))
            guias = {str(k).upper(): v for k, v in (data.get('guias_talla') or {}).items()}
            if not guias:
                continue
            for linea in data['lineas']:
                marca = (linea.get('marca') or data.get('marca') or '').strip().upper()
                tallas = [str(t).strip().upper() for t in linea.get('tallas') or {}]
                genero = str(linea.get('genero') or '').upper()
                if linea.get('guia'):
                    tipo, nombre = 'LINEA', linea['guia']
                elif any(t[-1:] in ('C', 'Y') for t in tallas) or genero in ('NIÑO', 'NIÑA', 'NINO', 'NINA'):
                    tipo, nombre = 'INFANTIL', guias.get('INFANTIL')
                else:
                    tipo, nombre = genero, guias.get(genero) or guias.get('DEFAULT')
                if not nombre:
                    continue
                req = requeridas.setdefault((marca, nombre.strip().upper()),
                                            {'nombre': nombre.strip(), 'tipos': set(), 'tallas': {}})
                req['tipos'].add(tipo)
                for t in tallas:
                    req['tallas'].setdefault(_clave(t), t)

        if not requeridas:
            raise CommandError('Los JSON no tienen "guias_talla": no hay guías que revisar.')

        planes = [self._planificar(marca, req) for (marca, _n), req in sorted(requeridas.items())]
        for plan in planes:
            self._imprimir(plan)

        errores = [p for p in planes if p['errores']]
        cambios = [p for p in planes if p['crear'] or p['agregar_sufijo'] or p['agregar_filas']]
        self.stdout.write('')
        if errores:
            raise CommandError('Hay guías con ERROR (arriba); no se escribió nada.')
        if not cambios:
            self.stdout.write(self.style.SUCCESS('Las guías ya están bien: no hay nada que cambiar.'))
            return
        if not opts['apply']:
            self.stdout.write(self.style.WARNING(
                'VISTA PREVIA: no se escribió nada. Repite con --apply para crear/ajustar las guías.'))
            return
        with transaction.atomic():
            for plan in cambios:
                self._aplicar(plan)
        self.stdout.write(self.style.SUCCESS(f'Listo: {len(cambios)} guía(s) creadas o ajustadas.'))

    # ------------------------------------------------------------------ plan

    def _planificar(self, marca, req):
        nombre = req['nombre']
        plan = {'marca': marca, 'nombre': nombre, 'guia': None, 'crear': None,
                'agregar_sufijo': [], 'agregar_filas': [], 'faltan': [], 'errores': [],
                'avisos': [], 'es_infantil': 'INFANTIL' in req['tipos']}
        referencia = _REFERENCIA.get((marca, nombre.upper()))
        guias = list(GuiaTalla.objects.filter(marca__valor__iexact=marca, nombre__iexact=nombre)
                     .select_related('marca').order_by('id'))
        if len(guias) > 1:
            plan['errores'].append(f'hay {len(guias)} guías «{nombre}» para {marca} '
                                   f'(ids {", ".join(str(g.id) for g in guias)}): deja una sola')
            return plan
        if not guias:
            opcion = (AtributoOpcion.objects.filter(atributo__nombre__iexact='Marca', valor__iexact=marca)
                      .order_by('id').first())
            if opcion is None:
                plan['errores'].append(f'no existe la marca {marca}')
            elif referencia is None:
                otras = ', '.join(GuiaTalla.objects.filter(marca__valor__iexact=marca)
                                  .values_list('nombre', flat=True)) or 'ninguna'
                plan['errores'].append(f'no existe la guía «{nombre}» y no tengo tabla para crearla '
                                       f'(guías de {marca}: {otras})')
            else:
                plan['crear'] = (opcion, referencia)
                en_ref = {_clave_busqueda(r[2]) for r in referencia}
                plan['faltan'] = sorted(t for c, t in req['tallas'].items()
                                        if _clave_busqueda(t) not in en_ref)
                if plan['faltan']:
                    plan['errores'].append(f'la tabla para crearla no tiene estas tallas de las '
                                           f'facturas: {", ".join(plan["faltan"])}')
            return plan

        guia = plan['guia'] = guias[0]
        items = list(guia.items.order_by('orden', 'id'))
        plan['items'] = items
        plan['usan'] = (Producto.objects.filter(guia_talla=guia).count()
                        + GuiaTallaProducto.objects.filter(guia=guia).count())

        # Juveniles sin Y (solo en la guía INFANTIL).
        if plan['es_infantil']:
            for it in items:
                num, suf = _clave(it.us)
                if it.us and not suf and _RE_TALLA.match(str(it.us).strip()):
                    plan['agregar_sufijo'].append((it, _texto(num, 'Y')))

        # Tallas de las facturas que no están en la guía (C distingue bebé; Y no).
        en_guia = {_clave_busqueda(it.us) for it in items}
        ref_por_clave = {_clave_busqueda(fila[2]): fila for fila in referencia or []}
        for _c, texto in sorted(req['tallas'].items()):
            clave = _clave_busqueda(texto)
            if clave in en_guia:
                continue
            if clave in ref_por_clave:
                plan['agregar_filas'].append(ref_por_clave[clave])
            else:
                plan['faltan'].append(texto)
        if plan['faltan']:
            plan['errores'].append(f'tallas de las facturas que no están en la guía y no sé sus '
                                   f'equivalencias: {", ".join(plan["faltan"])} — agrégalas a mano')
        return plan

    # --------------------------------------------------------------- reporte

    def _imprimir(self, plan):
        w = self.stdout.write
        w('')
        titulo = f'Guía «{plan["nombre"]}» ({plan["marca"]})'
        if plan['guia'] is not None:
            g = plan['guia']
            w(self.style.MIGRATE_HEADING(f'{titulo} · id {g.id} · {len(plan["items"])} tallas · '
                                         f'usada por {plan["usan"]} producto(s)'))
            w('  hoy (US → CL): ' + '  '.join(f'{it.us}→{it.cl}' for it in plan['items']))
        elif plan['crear']:
            _op, ref = plan['crear']
            w(self.style.MIGRATE_HEADING(f'{titulo} · NO EXISTE → se crea con {len(ref)} tallas'))
            w('  (US → CL): ' + '  '.join(f'{r[2]}→{r[1]}' for r in ref))
        else:
            w(self.style.MIGRATE_HEADING(titulo))
        if plan['agregar_sufijo']:
            w(self.style.WARNING('  ! juveniles sin Y → se corrigen: '
                                 + '  '.join(f'{it.us}→{nuevo}' for it, nuevo in plan['agregar_sufijo'])))
            if plan.get('usan'):
                w(self.style.WARNING(f'  ! {plan["usan"]} producto(s) usan esta guía: sus tallas NO se '
                                     f'renombran (quedan como están); solo cambia la guía'))
        if plan['agregar_filas']:
            w(self.style.WARNING('  ! faltan tallas de las facturas → se agregan: '
                                 + '  '.join(f'{r[2]} (CL {r[1]})' for r in plan['agregar_filas'])))
        if (plan['guia'] is not None and not plan['agregar_sufijo'] and not plan['agregar_filas']
                and not plan['errores']):
            w(self.style.SUCCESS('  ✓ está bien: tiene todas las tallas de las facturas'
                                 + (' y las juveniles con Y' if plan['es_infantil'] else '')))
        for a in plan['avisos']:
            w(self.style.WARNING(f'  ! {a}'))
        for e in plan['errores']:
            w(self.style.ERROR(f'  ✗ {e}'))

    # ------------------------------------------------------------- escritura

    def _aplicar(self, plan):
        if plan['crear']:
            opcion, ref = plan['crear']
            orden = (GuiaTalla.objects.filter(marca=opcion).aggregate(m=Max('orden'))['m'] or 0) + 1
            guia = GuiaTalla.objects.create(marca=opcion, nombre=plan['nombre'], orden=orden)
            GuiaTallaItem.objects.bulk_create([
                GuiaTallaItem(guia=guia, orden=o, cl=cl, us=us, eu=eu, uk=uk, br=br, cm=cm)
                for o, cl, us, eu, uk, br, cm in ref])
            self.stdout.write(self.style.SUCCESS(f'  creada «{guia.nombre}» id {guia.id} ({len(ref)} tallas)'))
            return
        guia = plan['guia']
        for it, nuevo in plan['agregar_sufijo']:
            it.us = nuevo
            it.save(update_fields=['us'])
        if plan['agregar_filas']:
            base = (guia.items.aggregate(m=Max('orden'))['m'] or 0) + 1
            GuiaTallaItem.objects.bulk_create([
                GuiaTallaItem(guia=guia, orden=base + i, cl=cl, us=us, eu=eu, uk=uk, br=br, cm=cm)
                for i, (_o, cl, us, eu, uk, br, cm) in enumerate(plan['agregar_filas'])])
        self.stdout.write(self.style.SUCCESS(
            f'  ajustada «{guia.nombre}» id {guia.id}: {len(plan["agregar_sufijo"])} con Y, '
            f'{len(plan["agregar_filas"])} talla(s) agregadas'))
