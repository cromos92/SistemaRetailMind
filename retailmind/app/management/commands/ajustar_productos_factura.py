"""
Corrige, después de cargar_productos_factura, el precio de venta y el color de
los productos de las facturas (compras/facturas/*.json).

Precio de venta — nunca baja nada (salvo --precio explícito):
  - Si una ficha BAJÓ de precio en la carga (HistorialCambioPrecio desde el día
    de la carga), se devuelve a su precio anterior, el más caro.
  - Líneas con precio por regla (sin precio a mano en el JSON): si quedaron bajo
    costo × 1,9 (redondeo ...990), suben a costo × 1,9. --sin-x19 lo omite.
  - --precio ARTICULO=PRECIO fija el precio exacto (p.ej. las lecturas a mano
    dudosas). Es lo único que puede bajar un precio.
  Se aplica a la ficha de la bodega del JSON y a la misma variante en otras
  bodegas (mismo artículo + marca/color/género/categoría), con historial,
  lotes FIFO activos y aviso a las tiendas cuyo precio cambia.

Color (--colores JSON): solo en las fichas CREADAS en la carga que siguen con
el color del JSON (MULTI). Las que ya existían no se tocan. Actualiza también
el texto de color de su Compras_Producto.

Sin --apply NO escribe nada: muestra la vista previa.

Uso (desde retailmind/):
    python manage.py ajustar_productos_factura "compras/facturas/EQUINOX_*.json" --colores compras/facturas/colores_nike_20260924.json
    python manage.py ajustar_productos_factura "compras/facturas/EQUINOX_*.json" --colores compras/facturas/colores_nike_20260924.json --precio DZ2795-606=109990 --apply
"""
import glob
import json
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from app.management.commands.cargar_productos_factura import (
    _clave_marca, _fmt, _redondeo_js, _variantes_rut, redondear_990,
)
from app.models import (
    AtributoOpcion, Dte, HistorialCambioPrecio, LoteProducto, Movimientos_Producto,
    Producto, Productos_Recepcionados, Sucursal,
)
from app.utils_producto_match import normalizar_articulo, ordenar_por_reciente


def x19(costo, factor):
    return redondear_990(_redondeo_js(Decimal(costo) * factor))


class Command(BaseCommand):
    help = ('Corrige precio de venta (sube lo que bajó en la carga, regla ×1,9) y color '
            'de los productos de facturas cargadas. Vista previa por defecto; --apply escribe.')

    def add_arguments(self, parser):
        parser.add_argument('archivos', nargs='+', help='JSON de facturas (acepta comodines)')
        parser.add_argument('--colores', default=None, help='JSON {"colores": {ARTICULO: [COLOR, ...]}}')
        parser.add_argument('--precio', action='append', default=[], metavar='ARTICULO=PRECIO',
                            help='Precio de venta exacto para un artículo (repetible)')
        parser.add_argument('--factor', type=Decimal, default=Decimal('1.9'),
                            help='Piso de venta para líneas con precio por regla (default 1.9)')
        parser.add_argument('--sin-x19', action='store_true', help='No subir líneas por regla a costo × factor')
        parser.add_argument('--usuario', default=None, help="username del cambio (default: 'sistema' o superusuario)")
        parser.add_argument('--apply', action='store_true', help='Escribe (sin esto, vista previa)')

    # ------------------------------------------------------------------ datos

    def handle(self, *args, **opts):
        self.opts = opts
        rutas = []
        for patron in opts['archivos']:
            encontrados = sorted(glob.glob(patron)) if any(c in patron for c in '*?[') else [patron]
            if not encontrados:
                raise CommandError(f'Ningún archivo calza con {patron}')
            rutas.extend(encontrados)

        overrides = {}
        for item in opts['precio']:
            if '=' not in item:
                raise CommandError(f'--precio debe ser ARTICULO=PRECIO: {item!r}')
            art, precio = item.split('=', 1)
            overrides[normalizar_articulo(art)] = int(precio.replace('.', '').strip())

        colores = {}
        if opts['colores']:
            data = json.loads(Path(opts['colores']).read_text(encoding='utf-8'))
            for art, v in (data.get('colores') or data).items():
                colores[normalizar_articulo(art)] = (v[0] if isinstance(v, list) else v).strip().upper()
        self.opciones_color = {}
        for valor in set(colores.values()):
            op = (AtributoOpcion.objects.filter(atributo__nombre__iexact='Color', valor__iexact=valor)
                  .order_by('id').first())
            if op is None:
                raise CommandError(f'No existe el color «{valor}» en el sistema')
            self.opciones_color[valor] = op

        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = (User.objects.filter(username=opts['usuario']).first() if opts['usuario'] else
                     User.objects.filter(username__iexact='sistema', is_active=True).first()
                     or User.objects.filter(is_superuser=True, is_active=True).order_by('id').first())

        lineas, vistos = [], set()
        for ruta in rutas:
            data = json.loads(Path(ruta).read_text(encoding='utf-8'))
            dte = self._dte(data, ruta)
            sucursal = Sucursal.objects.get(alias__iexact=data['sucursal'])
            carga = (Movimientos_Producto.objects.filter(dte=dte, concepto='INGRESO_MANUAL')
                     .aggregate(d=Min('fecha'))['d'])
            if carga is None:
                raise CommandError(f'{Path(ruta).name}: la factura {data["folio"]} no tiene carga '
                                   f'(ningún INGRESO_MANUAL): corre primero cargar_productos_factura')
            desde = timezone.make_aware(datetime.combine(carga, time.min))
            for linea in data['lineas']:
                art = normalizar_articulo(linea['articulo'])
                if art in vistos:
                    continue
                vistos.add(art)
                lineas.append(self._planificar(art, linea, data, dte, sucursal, desde,
                                               overrides.get(art), colores.get(art)))

        faltan = set(overrides) - vistos
        if faltan:
            raise CommandError(f'--precio para artículos que no están en los JSON: {", ".join(sorted(faltan))}')

        self._imprimir(lineas)
        cambios = [l for l in lineas if l['precios'] or l['color']]
        if not cambios:
            self.stdout.write(self.style.SUCCESS('\nNo hay nada que corregir.'))
            return
        if not opts['apply']:
            self.stdout.write(self.style.WARNING('\nVISTA PREVIA: no se escribió nada. Repite con --apply.'))
            return
        with transaction.atomic():
            for l in cambios:
                self._aplicar(l)
        self.stdout.write(self.style.SUCCESS(f'\nListo: {len(cambios)} artículo(s) corregidos.'))

    def _dte(self, data, ruta):
        if data.get('dte_id'):
            return Dte.objects.get(id=data['dte_id'])
        dtes = [d for d in Dte.objects.filter(numero_documento=data['folio'],
                                              emisor__rut__in=_variantes_rut(data['proveedor_rut']))
                if 'FACTURA' in str(d.tipo_documento or '').upper() and not d.es_nota_credito]
        if len(dtes) != 1:
            raise CommandError(f'{Path(ruta).name}: el folio {data["folio"]} calza con {len(dtes)} '
                               f'facturas; pon "dte_id" en el JSON')
        return dtes[0]

    # ------------------------------------------------------------------ plan

    def _planificar(self, art, linea, data, dte, sucursal, desde, override, color):
        marca = str(linea.get('marca') or data.get('marca') or '')
        todas = [f for f in ordenar_por_reciente(
                     Producto.objects.filter(articulo__icontains=art.split(' ')[0])
                     .select_related('sucursal', 'atributo1', 'atributo2'))
                 if normalizar_articulo(f.articulo) == art
                 and _clave_marca(getattr(f.atributo1, 'valor', '')) == _clave_marca(marca)]
        locales = [f for f in todas if f.sucursal_id == sucursal.id]
        if linea.get('ficha_id'):
            locales = [f for f in locales if f.id == int(linea['ficha_id'])]
        plan = {'art': art, 'folio': data['folio'], 'desc': linea.get('descripcion', ''),
                'costo': int(linea['costo']), 'a_mano': bool(linea.get('precioventa')),
                'duda': linea.get('_precio_duda'), 'precios': [], 'color': None,
                'errores': [], 'ficha': None, 'x19': None, 'motivo_x19': ''}
        if not locales:
            plan['errores'].append(f'no hay ficha en {sucursal.alias}')
            return plan
        ficha = plan['ficha'] = locales[0]
        ident = (ficha.atributo1_id, ficha.atributo2_id, ficha.atributo3_id, ficha.categoria_id)
        variantes = [ficha] + [f for f in todas if f.sucursal_id != sucursal.id and
                               (f.atributo1_id, f.atributo2_id, f.atributo3_id, f.categoria_id) == ident]
        plan['nueva'] = ficha.fecha_creacion is not None and ficha.fecha_creacion >= desde

        piso = 0
        if not plan['a_mano'] and not self.opts['sin_x19']:
            piso = plan['x19'] = x19(plan['costo'], self.opts['factor'])

        for f in variantes:
            actual = int(f.precioventa or 0)
            hist = list(HistorialCambioPrecio.objects.filter(
                producto=f, fecha_cambio__gte=desde, motivo__startswith='[PRECIO_VENTA]')
                .order_by('fecha_cambio', 'id').values_list('precio_anterior', flat=True)[:1])
            antes = int(hist[0]) if hist else None
            if override is not None:
                objetivo, motivo = override, 'precio indicado (--precio)'
            else:
                objetivo = max(actual, antes or 0, piso)
                motivos = []
                if antes and antes > actual and objetivo == antes:
                    motivos.append(f'bajó en la carga ({_fmt(antes)} → {_fmt(actual)}): vuelve al anterior')
                if piso and objetivo == piso and piso > max(actual, antes or 0):
                    motivos.append(f'bajo costo × {self.opts["factor"]}')
                motivo = '; '.join(motivos)
            if objetivo != actual:
                plan['precios'].append({'ficha': f, 'actual': actual, 'antes': antes,
                                        'nuevo': objetivo, 'motivo': motivo})

        if color:
            op = self.opciones_color[color]
            color_json = str(linea.get('color') or data.get('color') or '').upper()
            if not plan['nueva']:
                pass  # ya existía: se respeta su color
            elif ficha.atributo2_id == op.id:
                pass
            elif str(getattr(ficha.atributo2, 'valor', '')).upper() != color_json:
                plan['errores'].append(f'color actual «{ficha.atributo2.valor}» no es el del JSON '
                                       f'({color_json}): no se toca')
            else:
                choque = Producto.objects.filter(
                    sucursal=ficha.sucursal, atributo1_id=ficha.atributo1_id, atributo2=op,
                    atributo3_id=ficha.atributo3_id, categoria_id=ficha.categoria_id,
                ).exclude(id=ficha.id)
                choque = [c for c in choque if normalizar_articulo(c.articulo) == art]
                if choque:
                    plan['errores'].append(f'ya existe #{choque[0].id} con color {color}: no se cambia')
                else:
                    plan['color'] = (ficha.atributo2.valor, op)
        return plan

    # --------------------------------------------------------------- reporte

    def _imprimir(self, lineas):
        w = self.stdout.write
        folio = None
        for l in lineas:
            if l['folio'] != folio:
                folio = l['folio']
                w('')
                w(self.style.MIGRATE_HEADING(f'══ Factura {folio}'))
            f = l['ficha']
            venta = int(f.precioventa or 0) if f else 0
            fuente = 'a mano' if l['a_mano'] else 'regla'
            cab = (f'{l["art"]:<12} costo {_fmt(l["costo"])} · venta {_fmt(venta)} ({fuente}, '
                   f'×{venta / l["costo"]:.2f})' if f else l['art'])
            if l['x19']:
                cab += f' · ×{self.opts["factor"]} = {_fmt(l["x19"])}'
            estado = self.style.WARNING('CAMBIA ') if (l['precios'] or l['color']) else '  ok   '
            w(f'  {estado} {cab}  {l["desc"][:28]}')
            for p in l['precios']:
                antes = f' (antes de la carga: {_fmt(p["antes"])})' if p['antes'] else ''
                w(self.style.WARNING(f'      precio {p["ficha"].sucursal.alias} #{p["ficha"].id}: '
                                     f'{_fmt(p["actual"])} → {_fmt(p["nuevo"])}{antes} · {p["motivo"]}'))
            if l['color']:
                w(self.style.WARNING(f'      color: {l["color"][0]} → {l["color"][1].valor}'))
            if l['duda'] and not self.opts['precio']:
                w(f'      ? precio a mano dudoso: {l["duda"]} — si es otro, --precio {l["art"]}=PRECIO')
            for e in l['errores']:
                w(self.style.ERROR(f'      ✗ {e}'))
        n_precio = sum(len(l['precios']) for l in lineas)
        n_color = sum(1 for l in lineas if l['color'])
        w('')
        w(self.style.MIGRATE_HEADING(f'Resumen: {n_precio} ficha(s) cambian de precio · '
                                     f'{n_color} cambian de color · {len(lineas)} artículos revisados'))

    # ------------------------------------------------------------- escritura

    def _aplicar(self, l):
        from app.services.alertas_precio import alertar_precio_sucursal
        from app.services.historial_precios import registrar_cambios_precio

        origen = l['ficha'].sucursal.alias
        for p in l['precios']:
            f = Producto.objects.get(id=p['ficha'].id)
            anterior = int(f.precioventa or 0)
            f.precioventa = p['nuevo']
            f.precioSugerido = p['nuevo']
            f.save(update_fields=['precioventa', 'precioSugerido'])
            lotes = LoteProducto.objects.filter(producto_talla__producto=f, cantidad_disponible__gt=0,
                                                activo=True).update(precio_venta_unitario=p['nuevo'])
            registrar_cambios_precio(
                f, {'precioventa': anterior}, usuario=self.user,
                motivo=f'Corrección tras carga de factura {l["folio"]}: {p["motivo"]}',
                tipo_cambio='ACTUALIZACION_MANUAL', lotes_afectados=lotes)
            if f.sucursal_id != l['ficha'].sucursal_id:
                alertar_precio_sucursal(f, anterior, p['nuevo'], usuario=self.user, desde_alias=origen,
                                        origen='corrección carga factura', estado='APLICADO',
                                        motivo=f'Corrección de precio tras carga de factura {l["folio"]}')
            self.stdout.write(f'  {l["art"]} {f.sucursal.alias}: precio {_fmt(anterior)} → {_fmt(p["nuevo"])}')
        if l['color']:
            viejo, op = l['color']
            Producto.objects.filter(id=l['ficha'].id).update(atributo2=op)
            cps = (Productos_Recepcionados.objects.filter(producto_talla__producto_id=l['ficha'].id)
                   .values_list('compra_producto_talla__compra_producto_id', flat=True))
            from app.models import Compras_Producto
            Compras_Producto.objects.filter(id__in=[c for c in cps if c]).update(atributo2=op.valor)
            self.stdout.write(f'  {l["art"]}: color {viejo} → {op.valor}')
