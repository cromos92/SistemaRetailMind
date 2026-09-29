"""
Lee una factura de compra en PDF con Claude y deja un JSON por factura en
compras/facturas/, listo para la vista previa de cargar_productos_factura.

No escribe en la base: solo crea los archivos JSON. Revisa en la salida (y en
el campo "_revisar" de cada archivo / línea) lo que no coincidió entre
lecturas, lo que no cuadra y los precios a mano dudosos.

La lógica vive en app/services/carga_factura/lectura.py. Necesita la clave del
proveedor del modelo: ANTHROPIC_API_KEY para Claude (default), OPENAI_API_KEY,
GEMINI_API_KEY… para otros (ver app/utils_ia.py).

Uso (desde retailmind/):
    python manage.py leer_factura_pdf "C:/ruta/Factura.pdf" --sucursal EDEL
    python manage.py leer_factura_pdf "C:/ruta/Factura.pdf" --sucursal EDEL --marca NIKE --lecturas 1
    python manage.py leer_factura_pdf "C:/ruta/Factura.pdf" --sucursal EDEL --modelo gemini:gemini-3.8-flash
Luego:
    python manage.py cargar_productos_factura compras/facturas/EQUINOX_148763.json
"""
import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from app.models import Sucursal
from app.services.carga_factura import lectura as svc_lectura
from app.services.carga_factura.facturas import ErrorCarga
from app.services.carga_factura.precios import fmt


def _slug(texto):
    palabra = (str(texto or '').strip().split() or ['PROVEEDOR'])[0]
    return re.sub(r'[^A-Z0-9]', '', palabra.upper()) or 'PROVEEDOR'


class Command(BaseCommand):
    help = 'Lee una factura PDF con Claude y deja un JSON por factura en compras/facturas/.'

    def add_arguments(self, parser):
        parser.add_argument('pdf', help='Ruta del PDF de la factura (escaneo o PDF del SII)')
        parser.add_argument('--sucursal', required=True, help='Bodega donde entra la mercadería (alias, p.ej. EDEL)')
        parser.add_argument('--marca', default=None, help='Marca (si no, la que lea en la factura)')
        parser.add_argument('--lecturas', type=int, default=2,
                            help='Lecturas independientes que se comparan (default 2)')
        parser.add_argument('--modelo', default=None,
                            help='Lector: claude-opus-5 (default), openai:gpt-5.4-mini, '
                                 'gemini:gemini-3.8-flash… o una cadena de respaldo "a,b"')
        parser.add_argument('--salida', default='compras/facturas',
                            help='Carpeta de los JSON (default compras/facturas)')
        parser.add_argument('--sobrescribir', action='store_true',
                            help='Reemplazar un JSON que ya exista con el mismo nombre')

    def handle(self, *args, **opts):
        ruta = Path(opts['pdf'])
        if not ruta.exists():
            raise CommandError(f'No existe el archivo {ruta}')
        if not Sucursal.objects.filter(alias__iexact=opts['sucursal']).exists():
            raise CommandError(f'No existe la sucursal {opts["sucursal"]!r}')
        salida = Path(opts['salida'])
        salida.mkdir(parents=True, exist_ok=True)

        modelo = opts['modelo'] or svc_lectura.modelo_lectura()
        self.stdout.write(f'Leyendo {ruta.name} con {modelo} '
                          f'({opts["lecturas"]} lectura(s))… puede tardar unos minutos.')
        svc_lectura.uso_iniciar()
        try:
            leido = svc_lectura.leer_pdf(ruta.read_bytes(), marca=opts['marca'],
                                         lecturas=opts['lecturas'], modelo=modelo)
        except ErrorCarga as exc:
            raise CommandError(str(exc))
        uso = svc_lectura.uso_actual()
        modelo = leido.get('modelo') or modelo
        self.stdout.write(f'Leída con {modelo}: {uso.get("llamadas", 0)} llamada(s), '
                          f'≈ US${uso.get("costo_usd", 0):.2f}.')

        consolidada = svc_lectura.combinar_lecturas(leido['lecturas'])
        facturas = consolidada.get('facturas', [])
        if not facturas:
            raise CommandError('No se encontró ninguna factura en el documento.')
        self.stdout.write(f'Documento leído como {leido["modo"]}: {len(facturas)} factura(s).')

        for factura in facturas:
            datos = svc_lectura.a_json_de_carga(
                factura, opts['sucursal'].upper(), marca=opts['marca'],
                fuente=f'{ruta.name}, leída con {modelo} ({opts["lecturas"]} lectura(s))')
            destino = salida / f'{_slug(factura.get("proveedor_nombre"))}_{factura["folio"]}.json'
            if destino.exists() and not opts['sobrescribir']:
                destino = destino.with_name(destino.stem + '_leida.json')
            destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding='utf-8')
            self._imprimir(factura, datos, destino)

        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            'Revisa los "_revisar" de cada archivo y después corre la vista previa: '
            'python manage.py cargar_productos_factura "<archivo.json>"'))

    def _imprimir(self, factura, datos, destino):
        w = self.stdout.write
        lineas = datos['lineas']
        unidades = sum(sum(l['tallas'].values()) for l in lineas)
        neto = sum(l['importe'] or 0 for l in lineas)
        w('')
        w(self.style.MIGRATE_HEADING(
            f'══ {factura.get("tipo_documento")} N° {factura["folio"]} · {factura.get("proveedor_nombre")} '
            f'({factura["proveedor_rut"]}) · {factura["fecha_emision"]} · marca {datos["marca"] or "?"}'))
        w(f'  {len(lineas)} línea(s) · {unidades} unidades (factura: {factura.get("total_unidades")}) · '
          f'neto ${fmt(neto)} (factura: ${fmt(factura.get("total_neto") or 0)})')
        a_mano = sum(1 for l in lineas if l['precioventa'])
        if a_mano:
            w(f'  precio de venta a mano en {a_mano} línea(s)')
        for problema in datos['_revisar']:
            w(self.style.ERROR(f'  ✗ {problema}'))
        for l in lineas:
            if l.get('_revisar') or l.get('_precio_duda'):
                detalle = ' · '.join(filter(None, [l.get('_revisar'), l.get('_precio_duda') and
                                                   f'precio a mano dudoso: {l["_precio_duda"]}']))
                w(self.style.WARNING(f'  ! {l["articulo"]}: {detalle}'))
        w(self.style.SUCCESS(f'  → {destino}'))
