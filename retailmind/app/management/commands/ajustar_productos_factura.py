"""
Corrige, después de cargar_productos_factura, el precio de venta y el color de
los productos de las facturas (compras/facturas/*.json).

Precio de venta — nunca baja nada (salvo --precio explícito):
  - Si una ficha BAJÓ de precio en la carga (HistorialCambioPrecio desde el día
    de la carga), se devuelve a su precio anterior, el más caro.
  - Líneas con precio por regla (sin precio a mano en el JSON): si quedaron bajo
    costo × factor piso (--factor; default el del perfil de la marca, 1,9 en
    Nike; redondeo ...990), suben a ese piso. --sin-x19 lo omite.
  - --precio ARTICULO=PRECIO fija el precio exacto (p.ej. las lecturas a mano
    dudosas). Es lo único que puede bajar un precio.
  Se aplica a la ficha de la bodega del JSON y a la misma variante en otras
  bodegas (mismo artículo + marca/color/género/categoría), con historial,
  lotes FIFO activos y aviso a las tiendas cuyo precio cambia.

Color (--colores JSON): solo en las fichas CREADAS en la carga que siguen con
el color del JSON (MULTI). Las que ya existían no se tocan. Actualiza también
el texto de color de su Compras_Producto.

La lógica vive en app/services/carga_factura/ajustes.py.

Sin --apply NO escribe nada: muestra la vista previa.

Uso (desde retailmind/):
    python manage.py ajustar_productos_factura "compras/facturas/EQUINOX_*.json" --colores compras/facturas/colores_nike_20260924.json
    python manage.py ajustar_productos_factura "compras/facturas/EQUINOX_*.json" --colores compras/facturas/colores_nike_20260924.json --precio DZ2795-606=109990 --apply
"""
import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Sucursal
from app.services.carga_factura import ajustes as svc_ajustes
from app.services.carga_factura.facturas import ErrorCarga, archivos_de_patrones, resolver_usuario
from app.services.carga_factura.precios import fmt
from app.utils_producto_match import normalizar_articulo


class Command(BaseCommand):
    help = ('Corrige precio de venta (sube lo que bajó en la carga, regla ×1,9) y color '
            'de los productos de facturas cargadas. Vista previa por defecto; --apply escribe.')

    def add_arguments(self, parser):
        parser.add_argument('archivos', nargs='+', help='JSON de facturas (acepta comodines)')
        parser.add_argument('--colores', default=None, help='JSON {"colores": {ARTICULO: [COLOR, ...]}}')
        parser.add_argument('--precio', action='append', default=[], metavar='ARTICULO=PRECIO',
                            help='Precio de venta exacto para un artículo (repetible)')
        parser.add_argument('--factor', type=Decimal, default=None,
                            help='Piso de venta para líneas con precio por regla '
                                 '(default: perfil de la marca, 1.9)')
        parser.add_argument('--sin-x19', action='store_true', help='No subir líneas por regla a costo × factor')
        parser.add_argument('--usuario', default=None, help="username del cambio (default: 'sistema' o superusuario)")
        parser.add_argument('--apply', action='store_true', help='Escribe (sin esto, vista previa)')

    def handle(self, *args, **opts):
        self.opts = opts
        try:
            self._handle(opts)
        except ErrorCarga as exc:
            raise CommandError(str(exc))

    def _handle(self, opts):
        rutas = archivos_de_patrones(opts['archivos'])

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
        self.opciones_color = svc_ajustes.opciones_de_color(colores)

        # Como antes: si el usuario pedido no existe, el cambio queda sin usuario.
        try:
            self.user = resolver_usuario(opts['usuario'])
        except ErrorCarga:
            self.user = None

        lineas, vistos = [], set()
        for ruta in rutas:
            data = json.loads(Path(ruta).read_text(encoding='utf-8'))
            dte = svc_ajustes.dte_de_factura(data, ruta)
            sucursal = Sucursal.objects.get(alias__iexact=data['sucursal'])
            desde = svc_ajustes.inicio_de_carga(dte, data, ruta)
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
                for texto in svc_ajustes.aplicar_ajuste(l, self.user):
                    self.stdout.write(texto)
        self.stdout.write(self.style.SUCCESS(f'\nListo: {len(cambios)} artículo(s) corregidos.'))

    def _planificar(self, art, linea, data, dte, sucursal, desde, override, color):
        return svc_ajustes.planificar_ajuste(art, linea, data, dte, sucursal, desde, override, color,
                                             self.opts, self.opciones_color)

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
            cab = (f'{l["art"]:<12} costo {fmt(l["costo"])} · venta {fmt(venta)} ({fuente}, '
                   f'×{venta / l["costo"]:.2f})' if f else l['art'])
            if l['x19']:
                cab += f' · ×{l["factor"]} = {fmt(l["x19"])}'
            estado = self.style.WARNING('CAMBIA ') if (l['precios'] or l['color']) else '  ok   '
            w(f'  {estado} {cab}  {l["desc"][:28]}')
            for p in l['precios']:
                antes = f' (antes de la carga: {fmt(p["antes"])})' if p['antes'] else ''
                w(self.style.WARNING(f'      precio {p["ficha"].sucursal.alias} #{p["ficha"].id}: '
                                     f'{fmt(p["actual"])} → {fmt(p["nuevo"])}{antes} · {p["motivo"]}'))
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
