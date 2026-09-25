"""
Revisa (y con --apply crea o ajusta) las guías de talla que usan las facturas
de compra de compras/facturas/*.json antes de cargarlas con
cargar_productos_factura.

Qué revisa, por cada guía de la factura ("guias_talla" del JSON o del perfil
de la marca):
  - Que exista para la marca. Si no existe y el perfil de la marca tiene una
    tabla de referencia para ese nombre (Nike: copiadas de las guías que había
    en la BD en mayo de 2026), la crea con esa tabla.
  - Guía INFANTIL: que las tallas juveniles lleven la Y en la columna US
    ('1.5' → '1.5Y'). Las de bebé ya llevan la C ('11C').
  - Que estén todas las tallas US que traen las facturas. Si falta una y la
    referencia la tiene, la agrega; si no, lo informa (no inventa
    equivalencias CL/CM).

Las tallas de los productos que YA usan la guía no se tocan: el ajuste solo
cambia la guía. Se informa cuántos productos la usan, porque las guías también
salen por la API externa (ecommerce).

La lógica vive en app/services/carga_factura/guias.py.

Uso (desde retailmind/):
    python manage.py revisar_guias_talla "compras/facturas/EQUINOX_*.json"
    python manage.py revisar_guias_talla "compras/facturas/EQUINOX_*.json" --apply
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.services.carga_factura import guias as svc_guias
from app.services.carga_factura.facturas import ErrorCarga, archivos_de_patrones


class Command(BaseCommand):
    help = ('Revisa las guías de talla que usan las facturas JSON; con --apply las '
            'crea o ajusta (Y en juveniles, tallas faltantes).')

    def add_arguments(self, parser):
        parser.add_argument('archivos', nargs='+', help='JSON de facturas (acepta comodines)')
        parser.add_argument('--apply', action='store_true', help='Escribe los cambios')

    def handle(self, *args, **opts):
        try:
            rutas = archivos_de_patrones(opts['archivos'])
        except ErrorCarga as exc:
            raise CommandError(str(exc))

        requeridas = svc_guias.guias_requeridas(svc_guias.leer_facturas(rutas))
        if not requeridas:
            raise CommandError('Los JSON no tienen "guias_talla": no hay guías que revisar.')

        planes = [svc_guias.planificar_guia(marca, req)
                  for (marca, _n), req in sorted(requeridas.items())]
        for plan in planes:
            self._imprimir(plan)

        errores = [p for p in planes if p['errores']]
        cambios = [p for p in planes if svc_guias.tiene_cambios(p)]
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
                self.stdout.write(self.style.SUCCESS(svc_guias.aplicar_guia(plan)))
        self.stdout.write(self.style.SUCCESS(f'Listo: {len(cambios)} guía(s) creadas o ajustadas.'))

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
