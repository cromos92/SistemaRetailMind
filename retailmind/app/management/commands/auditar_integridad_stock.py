"""
Auditoría de integridad del inventario — COMANDO DE SOLO LECTURA.

Este comando NO escribe NADA en la base de datos: no crea, no actualiza y no
borra ningún registro. Solo ejecuta consultas agregadas y muestra un informe.
Puede correrse con total seguridad contra producción.

Ejecuta los seis chequeos que la auditoría del 25-jul-2026 midió a mano y que
hoy nadie vigila de forma continua:

  1. kardex-stock          SUM(Movimientos_Producto.cantidad) != Producto_Talla.stock
  2. ventas-sin-movimiento Tickets PAGADOS sin ningún movimiento de stock
  3. movimientos-huerfanos Movimientos de venta sin ticket ni DTE que los respalde
  4. traspasos-sin-recibir DTE de traspaso con salida y sin la entrada correspondiente
  5. stock-lotes           Stock plano que no cuadra con los lotes FIFO (stock sin costo)
  6. recepciones-sin-mov   Productos_Recepcionados.movimiento_ingreso en NULL

Uso:
    python manage.py auditar_integridad_stock
    python manage.py auditar_integridad_stock --desde 2026-01-01 --hasta 2026-07-31
    python manage.py auditar_integridad_stock --sucursal 7
    python manage.py auditar_integridad_stock --sucursal 3,5,7
    python manage.py auditar_integridad_stock --solo kardex-stock,stock-lotes
    python manage.py auditar_integridad_stock --json C:/tmp/integridad.json

Notas de alcance:
  - Los chequeos 1 y 5 son FOTOS ACUMULADAS del inventario: comparan el saldo
    histórico completo contra el stock actual, así que IGNORAN --desde/--hasta
    (recortarlos por fecha rompería la invariante que miden).
  - Los chequeos 2, 3, 4 y 6 sí respetan el rango de fechas.
  - En los movimientos se usa el campo `fecha` (fecha real del kardex) y NO
    `created_at`, porque en las filas migradas desde el sistema legacy
    `created_at` es la fecha de la migración, no la del hecho económico.
  - En los tickets se usa `created_at`, porque `Ticket.fecha` es `auto_now`
    (se pisa en cada actualización) y no sirve para analítica.
"""
import json
import logging
import textwrap
from datetime import datetime, time as dtime

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Exists, F, Min, Max, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from app.models import (
    LoteProducto,
    Movimientos_Producto,
    Producto_Talla,
    Productos_Recepcionados,
    Sucursal,
    Ticket,
    Ticket_Productos,
)

logger = logging.getLogger('app')

# ---------------------------------------------------------------------------
# Constantes de dominio
# ---------------------------------------------------------------------------

# Conceptos que representan una venta al público / mayorista (egresos de venta).
CONCEPTOS_VENTA = [
    'VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA',
    # DESPACHO_COTIZACION: entrega diferida de una cotización facturada y
    # cobrada. Es venta (ver app/constants_kardex.py). Esta lista es una
    # copia local: sin agregarlo acá, este módulo seguiría sin contarlo.
    'DESPACHO_COTIZACION',
]

# Conceptos que dan por RECIBIDA la mercadería de un traspaso.
CONCEPTOS_ENTRADA_TRASPASO = [
    'TRASPASO_ENTRADA',
    'REGULARIZACION_TRASPASO',
    'SOBRANTE_INGRESO',
]

# Días que se considera razonable que un traspaso esté en tránsito antes de
# tratarlo como mercadería perdida.
DIAS_TRANSITO_TOLERADOS = 7

# Semáforo
OK = 'OK'
REVISAR = 'REVISAR'
CRITICO = 'CRITICO'

MARCA_SEMAFORO = {
    OK: '[   OK    ]',
    REVISAR: '[ REVISAR ]',
    CRITICO: '[ CRITICO ]',
}

# Umbrales del semáforo, centralizados para que se puedan ajustar sin bucear
# en la lógica de cada chequeo.
UMBRALES = {
    'kardex_stock_pct_revisar': 0.5,      # % de SKUs descuadrados
    'kardex_stock_pct_critico': 5.0,
    'ventas_sin_mov_critico': 25,         # tickets pagados sin movimiento
    'movimientos_huerfanos_critico': 500,
    'traspasos_unidades_critico': 200,    # unidades despachadas sin recibir
    'stock_lotes_unidades_critico': 1000,  # unidades vendibles sin costo FIFO
    'recepciones_pct_critico': 50.0,      # % de recepciones sin movimiento
}

SEPARADOR = '=' * 88
SUB = '-' * 88


def _miles(valor):
    """Formatea un entero con separador de miles chileno (1.234.567)."""
    try:
        return f'{int(valor):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return str(valor)


def _pesos(valor):
    return f'${_miles(round(valor or 0))}'


def _pct(parte, total):
    if not total:
        return 0.0
    return round(parte * 100.0 / total, 2)


class _Peores:
    """
    Acumulador acotado: guarda solo los N casos de mayor impacto sin
    materializar en memoria el resultado completo de la consulta.
    """

    def __init__(self, limite):
        self.limite = max(1, limite)
        self.items = []  # [(score, payload)]

    def agregar(self, score, payload):
        if len(self.items) < self.limite:
            self.items.append((score, payload))
            self.items.sort(key=lambda x: x[0], reverse=True)
            return
        if score > self.items[-1][0]:
            self.items[-1] = (score, payload)
            self.items.sort(key=lambda x: x[0], reverse=True)

    def payloads(self):
        return [p for _, p in self.items]


class Command(BaseCommand):
    help = (
        'AUDITORÍA DE INTEGRIDAD DEL INVENTARIO — SOLO LECTURA. '
        'No escribe absolutamente nada en la base de datos. Compara kardex vs '
        'stock, ventas sin movimiento, movimientos de venta huérfanos, traspasos '
        'despachados y nunca recibidos, stock plano vs lotes FIFO y el enlace '
        'compra<->kardex. Salida en consola con semáforo por chequeo y volcado '
        'opcional a JSON.'
    )

    # Es un diagnóstico de solo lectura: debe poder ejecutarse aunque otro
    # módulo del proyecto esté temporalmente roto (justamente para diagnosticar).
    requires_system_checks = []

    CHEQUEOS = [
        'kardex-stock',
        'ventas-sin-movimiento',
        'movimientos-huerfanos',
        'traspasos-sin-recibir',
        'stock-lotes',
        'recepciones-sin-mov',
    ]

    # ------------------------------------------------------------------
    # CLI
    # ------------------------------------------------------------------
    def add_arguments(self, parser):
        parser.add_argument(
            '--sucursal',
            type=str,
            default=None,
            help='ID de sucursal a auditar. Acepta varios separados por coma (ej: 3,5,7). '
                 'Por defecto audita todas.',
        )
        parser.add_argument(
            '--desde',
            type=str,
            default=None,
            help='Fecha inicial YYYY-MM-DD para los chequeos con rango. '
                 'Por defecto: 1 de enero del año en curso.',
        )
        parser.add_argument(
            '--hasta',
            type=str,
            default=None,
            help='Fecha final YYYY-MM-DD (inclusive) para los chequeos con rango. '
                 'Por defecto: hoy.',
        )
        parser.add_argument(
            '--solo',
            type=str,
            default=None,
            help='Ejecuta solo el/los chequeos indicados, separados por coma. '
                 'Opciones: ' + ', '.join(self.CHEQUEOS),
        )
        parser.add_argument(
            '--json',
            type=str,
            default=None,
            dest='json_path',
            help='Ruta de archivo donde volcar el resultado completo en JSON.',
        )
        parser.add_argument(
            '--muestra',
            type=int,
            default=10,
            help='Cantidad máxima de casos de ejemplo por chequeo (default 10).',
        )

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        hoy = timezone.localdate()

        desde = self._parse_fecha(options.get('desde')) or hoy.replace(month=1, day=1)
        hasta = self._parse_fecha(options.get('hasta')) or hoy
        if desde > hasta:
            raise CommandError('--desde no puede ser posterior a --hasta.')

        sucursal_ids = self._parse_sucursales(options.get('sucursal'))
        muestra = max(1, min(int(options.get('muestra') or 10), 100))

        solicitados = self._parse_solo(options.get('solo'))

        ctx = {
            'desde': desde,
            'hasta': hasta,
            'desde_dt': timezone.make_aware(datetime.combine(desde, dtime.min)),
            'hasta_dt': timezone.make_aware(datetime.combine(hasta, dtime.max)),
            'sucursal_ids': sucursal_ids,
            'muestra': muestra,
        }

        self._encabezado(ctx)

        registro = {
            'kardex-stock': self._chequeo_kardex_vs_stock,
            'ventas-sin-movimiento': self._chequeo_ventas_sin_movimiento,
            'movimientos-huerfanos': self._chequeo_movimientos_huerfanos,
            'traspasos-sin-recibir': self._chequeo_traspasos_sin_recibir,
            'stock-lotes': self._chequeo_stock_vs_lotes,
            'recepciones-sin-mov': self._chequeo_recepciones_sin_movimiento,
        }

        resultados = []
        for clave in solicitados:
            inicio = timezone.now()
            try:
                resultado = registro[clave](ctx)
            except Exception as exc:  # noqa: BLE001 - un chequeo caído no debe tumbar el informe
                logger.exception('auditar_integridad_stock: falló el chequeo %s', clave)
                resultado = {
                    'clave': clave,
                    'titulo': clave,
                    'que_mide': '(no disponible)',
                    'error': str(exc),
                    'semaforo': CRITICO,
                    'casos': None,
                }
                self.stdout.write(self.style.ERROR(f'  ERROR ejecutando {clave}: {exc}'))
            resultado['segundos'] = round((timezone.now() - inicio).total_seconds(), 1)
            resultados.append(resultado)
            self._imprimir_resultado(resultado)

        self._resumen_final(resultados)

        json_path = options.get('json_path')
        if json_path:
            self._volcar_json(json_path, ctx, resultados)

    # ------------------------------------------------------------------
    # Parsers / helpers de CLI
    # ------------------------------------------------------------------
    def _parse_fecha(self, texto):
        if not texto:
            return None
        try:
            return datetime.strptime(texto.strip(), '%Y-%m-%d').date()
        except ValueError:
            raise CommandError(f'Fecha inválida: "{texto}". Formato esperado YYYY-MM-DD.')

    def _parse_sucursales(self, texto):
        if not texto:
            return []
        ids = []
        for parte in str(texto).split(','):
            parte = parte.strip()
            if not parte:
                continue
            try:
                ids.append(int(parte))
            except ValueError:
                raise CommandError(f'--sucursal espera IDs numéricos; recibí "{parte}".')
        existentes = set(
            Sucursal.objects.filter(id__in=ids).values_list('id', flat=True)
        )
        faltantes = [i for i in ids if i not in existentes]
        if faltantes:
            raise CommandError(
                'No existen las sucursales: ' + ', '.join(str(i) for i in faltantes)
            )
        return ids

    def _parse_solo(self, texto):
        if not texto:
            return list(self.CHEQUEOS)
        pedidos = [p.strip().lower() for p in str(texto).split(',') if p.strip()]
        invalidos = [p for p in pedidos if p not in self.CHEQUEOS]
        if invalidos:
            raise CommandError(
                'Chequeo desconocido: ' + ', '.join(invalidos) +
                '. Opciones válidas: ' + ', '.join(self.CHEQUEOS)
            )
        # Respetar el orden canónico aunque el usuario los pase desordenados
        return [c for c in self.CHEQUEOS if c in pedidos]

    def _alias_sucursales(self, ids):
        if not ids:
            return 'TODAS'
        alias = list(
            Sucursal.objects.filter(id__in=ids).order_by('alias').values_list('alias', flat=True)
        )
        return ', '.join(alias)

    def _mapa_sucursales(self):
        return dict(Sucursal.objects.values_list('id', 'alias'))

    # ------------------------------------------------------------------
    # Presentación
    # ------------------------------------------------------------------
    def _encabezado(self, ctx):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(SEPARADOR))
        self.stdout.write(self.style.SUCCESS('  AUDITORÍA DE INTEGRIDAD DEL INVENTARIO — SOLO LECTURA'))
        self.stdout.write(self.style.SUCCESS(SEPARADOR))
        self.stdout.write(f"  Generado          : {timezone.localtime().strftime('%d-%m-%Y %H:%M:%S')}")
        self.stdout.write(f"  Rango de fechas   : {ctx['desde']} a {ctx['hasta']} (chequeos 2,3,4,6)")
        self.stdout.write(f"  Sucursales        : {self._alias_sucursales(ctx['sucursal_ids'])}")
        self.stdout.write(f"  Muestra por caso  : hasta {ctx['muestra']} filas")
        self.stdout.write('  Este comando NO modifica datos: solo ejecuta consultas de lectura.')
        self.stdout.write('')

    def _imprimir_resultado(self, r):
        self.stdout.write('')
        self.stdout.write(SEPARADOR)
        marca = MARCA_SEMAFORO.get(r.get('semaforo'), '[   ?     ]')
        titulo = f"{marca}  {r.get('titulo', r['clave'])}"
        estilo = (
            self.style.SUCCESS if r.get('semaforo') == OK
            else self.style.WARNING if r.get('semaforo') == REVISAR
            else self.style.ERROR
        )
        self.stdout.write(estilo(titulo))
        self.stdout.write(SEPARADOR)
        self._parrafo('Qué mide ', r.get('que_mide', ''))
        if r.get('alcance'):
            self._parrafo('Alcance  ', r['alcance'])
        if r.get('error'):
            self.stdout.write(self.style.ERROR(f"  ERROR    : {r['error']}"))
            return

        for etiqueta, valor in r.get('metricas', []):
            self.stdout.write(f'  {etiqueta:<34}: {valor}')

        desglose = r.get('desglose') or []
        if desglose:
            self.stdout.write('')
            self.stdout.write(f"  {r.get('desglose_titulo', 'Desglose')}:")
            cab = r.get('desglose_columnas') or []
            if cab:
                self.stdout.write('    ' + ''.join(f'{c:>{w}}' for c, w in cab))
                self.stdout.write('    ' + SUB[:sum(w for _, w in cab)])
            for fila in desglose:
                self.stdout.write('    ' + fila)

        muestra = r.get('muestra') or []
        if muestra:
            self.stdout.write('')
            self.stdout.write(f"  Muestra ({len(muestra)} de {_miles(r.get('casos') or 0)} casos):")
            cab = r.get('muestra_columnas') or []
            if cab:
                self.stdout.write('    ' + ''.join(f'{c:>{w}}' for c, w in cab))
                self.stdout.write('    ' + SUB[:sum(w for _, w in cab)])
            for fila in r.get('muestra_lineas') or []:
                self.stdout.write('    ' + fila)

        if r.get('veredicto'):
            self.stdout.write('')
            self._parrafo('Veredicto', r['veredicto'])
        self.stdout.write(f"  (tiempo: {r.get('segundos', 0)} s)")

    def _parrafo(self, etiqueta, texto, ancho=76):
        """Imprime `etiqueta: texto` ajustado al ancho de consola."""
        lineas = textwrap.wrap(str(texto), width=ancho) or ['']
        self.stdout.write(f'  {etiqueta}: {lineas[0]}')
        for extra in lineas[1:]:
            self.stdout.write(f"  {' ' * len(etiqueta)}  {extra}")

    def _resumen_final(self, resultados):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(SEPARADOR))
        self.stdout.write(self.style.SUCCESS('  SEMÁFORO FINAL'))
        self.stdout.write(self.style.SUCCESS(SEPARADOR))
        self.stdout.write(f"  {'ESTADO':<12}{'CHEQUEO':<26}{'CASOS':>12}{'UNIDADES':>14}{'PESOS':>16}")
        self.stdout.write('  ' + SUB[:80])
        for r in resultados:
            casos = _miles(r.get('casos')) if r.get('casos') is not None else '-'
            unidades = _miles(r['unidades']) if r.get('unidades') is not None else '-'
            pesos = _pesos(r['pesos']) if r.get('pesos') is not None else '-'
            marca = MARCA_SEMAFORO.get(r.get('semaforo'), '[   ?     ]')
            linea = f"  {marca:<12}{r['clave']:<26}{casos:>12}{unidades:>14}{pesos:>16}"
            estilo = (
                self.style.SUCCESS if r.get('semaforo') == OK
                else self.style.WARNING if r.get('semaforo') == REVISAR
                else self.style.ERROR
            )
            self.stdout.write(estilo(linea))
        self.stdout.write('')
        criticos = [r for r in resultados if r.get('semaforo') == CRITICO]
        revisar = [r for r in resultados if r.get('semaforo') == REVISAR]
        if criticos:
            for linea in textwrap.wrap(
                f"{len(criticos)} chequeo(s) en CRÍTICO: " +
                ', '.join(r['clave'] for r in criticos), width=84,
            ):
                self.stdout.write(self.style.ERROR(f'  {linea}'))
        if revisar:
            for linea in textwrap.wrap(
                f"{len(revisar)} chequeo(s) para REVISAR: " +
                ', '.join(r['clave'] for r in revisar), width=84,
            ):
                self.stdout.write(self.style.WARNING(f'  {linea}'))
        if not criticos and not revisar:
            self.stdout.write(self.style.SUCCESS('  Todos los chequeos en OK.'))
        self.stdout.write('')
        self.stdout.write('  Recordatorio: este comando es de diagnóstico. NO corrigió nada.')
        self.stdout.write('')

    def _volcar_json(self, ruta, ctx, resultados):
        payload = {
            'generado_en': timezone.localtime().isoformat(),
            'solo_lectura': True,
            'parametros': {
                'desde': str(ctx['desde']),
                'hasta': str(ctx['hasta']),
                'sucursal_ids': ctx['sucursal_ids'],
                'sucursales': self._alias_sucursales(ctx['sucursal_ids']),
                'muestra': ctx['muestra'],
            },
            'chequeos': [
                {k: v for k, v in r.items()
                 if k not in ('muestra_lineas', 'desglose', 'desglose_columnas', 'muestra_columnas')}
                for r in resultados
            ],
        }
        try:
            with open(ruta, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
        except OSError as exc:
            raise CommandError(f'No pude escribir el JSON en "{ruta}": {exc}')
        self.stdout.write(self.style.SUCCESS(f'  JSON escrito en: {ruta}'))
        self.stdout.write('')

    # ------------------------------------------------------------------
    # CHEQUEO 1 — kardex vs stock
    # ------------------------------------------------------------------
    def _chequeo_kardex_vs_stock(self, ctx):
        """
        Invariante: para cada Producto_Talla, la suma de todos sus movimientos
        de kardex debe ser igual al stock actual. Es acumulativo: no admite
        recorte por fechas.
        """
        base = Producto_Talla.objects.all()
        if ctx['sucursal_ids']:
            base = base.filter(producto__sucursal_id__in=ctx['sucursal_ids'])

        total_skus = base.count()
        totales = base.aggregate(stock=Coalesce(Sum('stock'), Value(0)))
        stock_total = totales['stock'] or 0

        mov_scope = Movimientos_Producto.objects.all()
        if ctx['sucursal_ids']:
            mov_scope = mov_scope.filter(
                ProductoTalla__producto__sucursal_id__in=ctx['sucursal_ids']
            )
        kardex_total = mov_scope.aggregate(t=Coalesce(Sum('cantidad'), Value(0)))['t'] or 0

        # Una sola consulta: LEFT JOIN + GROUP BY + HAVING. Devuelve únicamente
        # los SKUs descuadrados, no el catálogo completo.
        qs = (
            base.values('id', 'sku', 'stock', 'producto__sucursal_id', 'producto__costo')
            .annotate(kardex=Coalesce(Sum('movimientos_productos_talla__cantidad'), Value(0)))
            .exclude(kardex=F('stock'))
        )

        casos = 0
        faltan = 0     # stock > kardex: el kardex no explica el stock
        sobran = 0     # kardex > stock: se movió más de lo que el stock refleja
        plata = 0
        por_sucursal = {}
        peores = _Peores(ctx['muestra'])

        for fila in qs.iterator(chunk_size=5000):
            casos += 1
            delta = (fila['stock'] or 0) - (fila['kardex'] or 0)
            if delta > 0:
                faltan += delta
            else:
                sobran += -delta
            plata += abs(delta) * (fila['producto__costo'] or 0)
            acc = por_sucursal.setdefault(fila['producto__sucursal_id'], [0, 0])
            acc[0] += 1
            acc[1] += delta
            peores.agregar(abs(delta), {
                'producto_talla_id': fila['id'],
                'sku': fila['sku'],
                'stock': fila['stock'],
                'kardex': fila['kardex'],
                'delta': delta,
            })

        muestra = self._hidratar_producto_talla(peores.payloads())

        pct = _pct(casos, total_skus)
        if casos == 0:
            semaforo = OK
        elif pct >= UMBRALES['kardex_stock_pct_critico']:
            semaforo = CRITICO
        elif pct >= UMBRALES['kardex_stock_pct_revisar']:
            semaforo = REVISAR
        else:
            semaforo = REVISAR

        alias = self._mapa_sucursales()
        desglose = []
        for suc_id, (n, delta) in sorted(por_sucursal.items(), key=lambda x: -abs(x[1][1])):
            nombre = (alias.get(suc_id) or f'#{suc_id}')[:9]
            desglose.append(f'{nombre:>10}{_miles(n):>12}{_miles(delta):>14}')

        return {
            'clave': 'kardex-stock',
            'titulo': '1. El kardex no cuadra con el stock',
            'que_mide': 'SKUs donde SUM(Movimientos_Producto.cantidad) != Producto_Talla.stock.',
            'alcance': 'Acumulado histórico completo (ignora --desde/--hasta por diseño).',
            'casos': casos,
            'unidades': faltan - sobran,
            'pesos': plata,
            'semaforo': semaforo,
            'metricas': [
                ('SKUs analizados', _miles(total_skus)),
                ('SKUs descuadrados', f'{_miles(casos)}  ({pct}%)'),
                ('Stock declarado (total)', f'{_miles(stock_total)} u'),
                ('Kardex acumulado (total)', f'{_miles(kardex_total)} u'),
                ('Delta global stock - kardex', f'{_miles(stock_total - kardex_total)} u'),
                ('Unidades sin respaldo en kardex', f'{_miles(faltan)} u'),
                ('Unidades movidas de más', f'{_miles(sobran)} u'),
                ('Magnitud valorizada (|delta| x costo)', _pesos(plata)),
            ],
            'desglose_titulo': 'Descuadre por sucursal',
            'desglose_columnas': [('SUC', 10), ('SKUs', 12), ('DELTA u', 14)],
            'desglose': desglose,
            'muestra': muestra,
            'muestra_columnas': [
                ('SKU', 12), ('SUC', 8), ('TALLA', 8), ('STOCK', 8),
                ('KARDEX', 9), ('DELTA', 9), ('  ARTICULO', 26),
            ],
            'muestra_lineas': [
                f"{str(m['sku']):>12}{m['sucursal']:>8}{str(m['talla'])[:8]:>8}"
                f"{_miles(m['stock']):>8}{_miles(m['kardex']):>9}{_miles(m['delta']):>9}"
                f"  {(m['articulo'] or '')[:24]}"
                for m in muestra
            ],
            'veredicto': (
                'El kardex es la única fuente auditable del inventario: mientras no cuadre, '
                'ningún reporte de costo o rotación es defendible. Revisar primero los SKUs '
                'de la muestra (mayor |delta|). OJO: la cifra en pesos es la MAGNITUD del '
                'descuadre (suma de |delta| x costo), no una pérdida contable.'
                if casos else 'Kardex y stock cuadran en todos los SKUs del alcance.'
            ),
        }

    def _hidratar_producto_talla(self, payloads):
        """Trae artículo/talla/sucursal solo para los casos de la muestra."""
        if not payloads:
            return []
        ids = [p['producto_talla_id'] for p in payloads]
        datos = {
            pt.id: pt
            for pt in Producto_Talla.objects
            .filter(id__in=ids)
            .select_related('producto', 'producto__sucursal')
        }
        salida = []
        for p in payloads:
            pt = datos.get(p['producto_talla_id'])
            producto = getattr(pt, 'producto', None)
            sucursal = getattr(producto, 'sucursal', None)
            item = dict(p)
            item['articulo'] = getattr(producto, 'articulo', '?')
            item['descripcion'] = getattr(producto, 'descripcion', '')
            item['talla'] = getattr(pt, 'talla', '?')
            item['sucursal'] = getattr(sucursal, 'alias', '?')
            salida.append(item)
        return salida

    # ------------------------------------------------------------------
    # CHEQUEO 2 — tickets pagados sin movimiento de stock
    # ------------------------------------------------------------------
    def _chequeo_ventas_sin_movimiento(self, ctx):
        """
        Tickets en estado PAGADO que no tienen NINGÚN Movimientos_Producto
        asociado: se cobró la venta pero el inventario nunca se descontó.
        """
        movs = Movimientos_Producto.objects.filter(ticket_id=OuterRef('pk'))
        lineas_reales = Ticket_Productos.objects.filter(
            idTicket=OuterRef('pk'),
            ProductoTalla__isnull=False,
            es_pendiente_despacho=False,
            stock__gt=0,
        )

        base = Ticket.objects.filter(
            estado='PAGADO',
            created_at__gte=ctx['desde_dt'],
            created_at__lte=ctx['hasta_dt'],
        )
        if ctx['sucursal_ids']:
            base = base.filter(sucursal_id__in=ctx['sucursal_ids'])

        pagados = base.count()

        sin_mov = base.annotate(
            tiene_mov=Exists(movs),
            tiene_lineas=Exists(lineas_reales),
        ).filter(tiene_mov=False)

        agg = sin_mov.aggregate(n=Count('id'), monto=Coalesce(Sum('total'), Value(0)))
        casos = agg['n'] or 0
        monto = agg['monto'] or 0

        agg_real = sin_mov.filter(tiene_lineas=True).aggregate(
            n=Count('id'), monto=Coalesce(Sum('total'), Value(0))
        )
        casos_reales = agg_real['n'] or 0
        monto_real = agg_real['monto'] or 0

        unidades = (
            Ticket_Productos.objects
            .filter(idTicket__in=sin_mov.values('id'), ProductoTalla__isnull=False)
            .aggregate(u=Coalesce(Sum('stock'), Value(0)))['u'] or 0
        )

        por_sucursal = list(
            sin_mov.values('sucursal__alias')
            .annotate(n=Count('id'), monto=Coalesce(Sum('total'), Value(0)))
            .order_by('-monto')
        )
        por_modulo = list(
            sin_mov.values('modulo_origen')
            .annotate(n=Count('id'))
            .order_by('-n')
        )

        muestra = list(
            sin_mov.order_by('-total')
            .values('id', 'correlativo', 'sucursal__alias', 'created_at',
                    'total', 'modulo_origen', 'estado', 'responsable',
                    'tipo_dte', 'folio_dte')[:ctx['muestra']]
        )
        for m in muestra:
            m['fecha'] = m['created_at'].astimezone(timezone.get_current_timezone()).strftime('%d-%m-%Y %H:%M')
            m.pop('created_at', None)

        if casos == 0:
            semaforo = OK
        elif casos >= UMBRALES['ventas_sin_mov_critico']:
            semaforo = CRITICO
        else:
            semaforo = REVISAR

        return {
            'clave': 'ventas-sin-movimiento',
            'titulo': '2. Ventas cobradas que nunca descontaron stock',
            'que_mide': 'Tickets PAGADOS sin ningún Movimientos_Producto asociado.',
            'alcance': f"Tickets creados entre {ctx['desde']} y {ctx['hasta']} (por created_at).",
            'casos': casos,
            'unidades': unidades,
            'pesos': monto,
            'semaforo': semaforo,
            'metricas': [
                ('Tickets PAGADOS en el rango', _miles(pagados)),
                ('Tickets sin movimiento', f'{_miles(casos)}  ({_pct(casos, pagados)}%)'),
                ('Monto involucrado', _pesos(monto)),
                ('Con líneas de producto real', f'{_miles(casos_reales)} tickets / {_pesos(monto_real)}'),
                ('Unidades vendidas no descontadas', f'{_miles(unidades)} u'),
            ],
            'desglose_titulo': 'Por sucursal / por módulo de origen',
            'desglose_columnas': [('SUCURSAL', 12), ('TICKETS', 10), ('MONTO', 16)],
            'desglose': (
                [f"{(d['sucursal__alias'] or '-')[:11]:>12}{_miles(d['n']):>10}{_pesos(d['monto']):>16}"
                 for d in por_sucursal]
                + ['']
                + [f"{(d['modulo_origen'] or '-')[:18]:>20}{_miles(d['n']):>10}" for d in por_modulo]
            ),
            'muestra': muestra,
            'muestra_columnas': [
                ('TICKET', 10), ('CORREL', 9), ('SUC', 8), ('FECHA', 18),
                ('TOTAL', 12), ('MODULO', 20),
            ],
            'muestra_lineas': [
                f"{m['id']:>10}{m['correlativo']:>9}{(m['sucursal__alias'] or '-'):>8}"
                f"{m['fecha']:>18}{_pesos(m['total']):>12}{(m['modulo_origen'] or '-')[:19]:>20}"
                for m in muestra
            ],
            'veredicto': (
                'Cada uno de estos tickets es plata cobrada con inventario intacto: el stock del '
                'sistema está inflado en esas unidades. Encaja con la falta de atomicidad del '
                'cobro (registrar_pagos_ticket). Los de módulo CAMBIO_DEVOLUCION pueden ser '
                'legítimos (canje sin ítems); priorizar los que tienen líneas de producto real.'
                if casos else 'Todos los tickets pagados del rango movieron stock.'
            ),
        }

    # ------------------------------------------------------------------
    # CHEQUEO 3 — movimientos de venta huérfanos
    # ------------------------------------------------------------------
    def _chequeo_movimientos_huerfanos(self, ctx):
        """
        El espejo del chequeo 2: movimientos de venta que descontaron stock
        sin ticket ni DTE que los respalde documentalmente.
        """
        base = Movimientos_Producto.objects.filter(
            concepto__in=CONCEPTOS_VENTA,
            ticket__isnull=True,
            dte__isnull=True,
            fecha__gte=ctx['desde'],
            fecha__lte=ctx['hasta'],
        )
        if ctx['sucursal_ids']:
            base = base.filter(
                Q(sucursal_origen_id__in=ctx['sucursal_ids'])
                | Q(sucursal_destino_id__in=ctx['sucursal_ids'])
            )

        total_venta = Movimientos_Producto.objects.filter(
            concepto__in=CONCEPTOS_VENTA,
            fecha__gte=ctx['desde'],
            fecha__lte=ctx['hasta'],
        )
        if ctx['sucursal_ids']:
            total_venta = total_venta.filter(
                Q(sucursal_origen_id__in=ctx['sucursal_ids'])
                | Q(sucursal_destino_id__in=ctx['sucursal_ids'])
            )
        total_venta_n = total_venta.count()

        agg = base.aggregate(
            n=Count('id'),
            u=Coalesce(Sum('cantidad'), Value(0)),
            plata=Coalesce(Sum(F('cantidad') * F('costo')), Value(0)),
        )
        casos = agg['n'] or 0
        unidades = abs(agg['u'] or 0)
        plata = abs(agg['plata'] or 0)

        por_mes = list(
            base.annotate(mes=TruncMonth('fecha'))
            .values('mes')
            .annotate(n=Count('id'), u=Coalesce(Sum('cantidad'), Value(0)))
            .order_by('mes')
        )
        por_sucursal = list(
            base.values('sucursal_origen__alias')
            .annotate(n=Count('id'), u=Coalesce(Sum('cantidad'), Value(0)))
            .order_by('-n')[:15]
        )

        muestra = list(
            base.order_by('cantidad')
            .values('id', 'ProductoTalla__sku', 'ProductoTalla__talla',
                    'ProductoTalla__producto__articulo',
                    'sucursal_origen__alias', 'fecha', 'cantidad',
                    'concepto', 'responsable')[:ctx['muestra']]
        )

        if casos == 0:
            semaforo = OK
        elif casos >= UMBRALES['movimientos_huerfanos_critico']:
            semaforo = CRITICO
        else:
            semaforo = REVISAR

        return {
            'clave': 'movimientos-huerfanos',
            'titulo': '3. Movimientos de venta sin ticket ni DTE',
            'que_mide': (
                'Movimientos con concepto de venta (' + ', '.join(CONCEPTOS_VENTA) +
                ') cuyos campos ticket y dte están ambos en NULL.'
            ),
            'alcance': f"Movimientos con fecha entre {ctx['desde']} y {ctx['hasta']}.",
            'casos': casos,
            'unidades': unidades,
            'pesos': plata,
            'semaforo': semaforo,
            'metricas': [
                ('Movimientos de venta en el rango', _miles(total_venta_n)),
                ('Sin respaldo documental', f'{_miles(casos)}  ({_pct(casos, total_venta_n)}%)'),
                ('Unidades sin respaldo', f'{_miles(unidades)} u'),
                ('Costo involucrado', _pesos(plata)),
            ],
            'desglose_titulo': 'Por mes / por sucursal de origen',
            'desglose_columnas': [('MES', 12), ('MOVS', 10), ('UNIDADES', 12)],
            'desglose': (
                [f"{d['mes'].strftime('%Y-%m'):>12}{_miles(d['n']):>10}{_miles(d['u']):>12}"
                 for d in por_mes if d['mes']]
                + ['']
                + [f"{(d['sucursal_origen__alias'] or '-')[:11]:>12}{_miles(d['n']):>10}{_miles(d['u']):>12}"
                   for d in por_sucursal]
            ),
            'muestra': muestra,
            'muestra_columnas': [
                ('MOV', 10), ('SKU', 12), ('SUC', 8), ('FECHA', 12),
                ('CANT', 7), ('  ARTICULO', 26),
            ],
            'muestra_lineas': [
                f"{m['id']:>10}{str(m['ProductoTalla__sku']):>12}"
                f"{(m['sucursal_origen__alias'] or '-'):>8}{m['fecha'].strftime('%d-%m-%Y'):>12}"
                f"{m['cantidad']:>7}  {(m['ProductoTalla__producto__articulo'] or '')[:24]}"
                for m in muestra
            ],
            'veredicto': (
                'Stock que salió sin documento asociado. Si se concentran en los meses de la '
                'sincronización con el sistema legacy es deuda histórica; si aparecen en meses '
                'recientes es un flujo vivo que está descontando stock sin dejar rastro.'
                if casos else 'Todos los movimientos de venta del rango tienen ticket o DTE.'
            ),
        }

    # ------------------------------------------------------------------
    # CHEQUEO 4 — traspasos despachados y nunca recibidos
    # ------------------------------------------------------------------
    def _chequeo_traspasos_sin_recibir(self, ctx):
        """
        Por cada DTE de traspaso: unidades que salieron (TRASPASO_SALIDA)
        contra unidades que entraron (TRASPASO_ENTRADA / REGULARIZACION /
        SOBRANTE_INGRESO). La diferencia es mercadería en tránsito eterno.
        """
        base = Movimientos_Producto.objects.filter(
            dte__isnull=False,
            dte__tipo_transaccion='TRASPASO',
            dte__descartado=False,
            dte__fecha_emision__gte=ctx['desde'],
            dte__fecha_emision__lte=ctx['hasta'],
        )
        if ctx['sucursal_ids']:
            base = base.filter(
                Q(sucursal_origen_id__in=ctx['sucursal_ids'])
                | Q(sucursal_destino_id__in=ctx['sucursal_ids'])
            )

        qs = (
            base.values(
                'dte_id', 'dte__numero_documento', 'dte__tipo_documento',
                'dte__fecha_emision', 'dte__estado_dte',
            )
            .annotate(
                salida=Coalesce(Sum('cantidad', filter=Q(concepto='TRASPASO_SALIDA')), Value(0)),
                entrada=Coalesce(
                    Sum('cantidad', filter=Q(concepto__in=CONCEPTOS_ENTRADA_TRASPASO)), Value(0)
                ),
                valor_salida=Coalesce(
                    Sum(F('cantidad') * F('costo'), filter=Q(concepto='TRASPASO_SALIDA')), Value(0)
                ),
                origen=Min('sucursal_origen__alias'),
                destino=Max('sucursal_destino__alias'),
            )
        )

        hoy = timezone.localdate()
        dtes_con_salida = 0
        sin_entrada = 0
        parciales = 0
        unidades_pendientes = 0
        plata = 0
        casos_antiguos = 0
        unidades_antiguas = 0
        peores = _Peores(ctx['muestra'])

        for fila in qs.iterator(chunk_size=2000):
            despachado = -(fila['salida'] or 0)
            if despachado <= 0:
                continue
            dtes_con_salida += 1
            recibido = fila['entrada'] or 0
            if recibido >= despachado:
                continue
            falta = despachado - recibido
            unidades_pendientes += falta
            costo_unitario = abs(fila['valor_salida'] or 0) / despachado if despachado else 0
            valor = falta * costo_unitario
            plata += valor
            if recibido == 0:
                sin_entrada += 1
            else:
                parciales += 1
            emision = fila['dte__fecha_emision']
            dias = (hoy - emision).days if emision else None
            if dias is not None and dias > DIAS_TRANSITO_TOLERADOS:
                casos_antiguos += 1
                unidades_antiguas += falta
            peores.agregar(falta, {
                'dte_id': fila['dte_id'],
                'numero_documento': fila['dte__numero_documento'],
                'tipo_documento': fila['dte__tipo_documento'],
                'fecha_emision': emision,
                'dias_en_transito': dias,
                'estado_dte': fila['dte__estado_dte'],
                'origen': fila['origen'] or '-',
                'destino': fila['destino'] or '-',
                'despachado': despachado,
                'recibido': recibido,
                'pendiente': falta,
                'valor_pendiente': round(valor),
            })

        casos = sin_entrada + parciales
        muestra = peores.payloads()

        # Un despacho de ayer todavía puede estar en camino: el semáforo se
        # dispara por lo que lleva más de DIAS_TRANSITO_TOLERADOS sin llegar.
        if casos == 0:
            semaforo = OK
        elif unidades_antiguas >= UMBRALES['traspasos_unidades_critico']:
            semaforo = CRITICO
        else:
            semaforo = REVISAR

        return {
            'clave': 'traspasos-sin-recibir',
            'titulo': '4. Mercadería despachada que nadie recibió',
            'que_mide': (
                'DTE de traspaso donde las unidades con TRASPASO_SALIDA superan a las '
                'recibidas (TRASPASO_ENTRADA / REGULARIZACION_TRASPASO / SOBRANTE_INGRESO).'
            ),
            'alcance': f"DTE de traspaso emitidos entre {ctx['desde']} y {ctx['hasta']}.",
            'casos': casos,
            'unidades': unidades_pendientes,
            'pesos': round(plata),
            'semaforo': semaforo,
            'metricas': [
                ('DTE de traspaso con salida', _miles(dtes_con_salida)),
                ('Sin ninguna entrada', _miles(sin_entrada)),
                ('Recibidos parcialmente', _miles(parciales)),
                ('Unidades en tránsito eterno', f'{_miles(unidades_pendientes)} u'),
                (f'Pendientes hace más de {DIAS_TRANSITO_TOLERADOS} días',
                 f'{_miles(casos_antiguos)} DTE / {_miles(unidades_antiguas)} u'),
                ('Valorizado al costo de salida', _pesos(plata)),
            ],
            'muestra': muestra,
            'muestra_columnas': [
                ('DTE', 9), ('TIPO', 7), ('FECHA', 12), ('DIAS', 6), ('ORIGEN', 9),
                ('DESTINO', 9), ('SALIO', 8), ('LLEGO', 8), ('FALTA', 8), ('  ESTADO', 24),
            ],
            'muestra_lineas': [
                f"{m['numero_documento']:>9}{str(m['tipo_documento'])[:6]:>7}"
                f"{m['fecha_emision'].strftime('%d-%m-%Y') if m['fecha_emision'] else '-':>12}"
                f"{('-' if m['dias_en_transito'] is None else m['dias_en_transito']):>6}"
                f"{str(m['origen'])[:8]:>9}{str(m['destino'])[:8]:>9}"
                f"{_miles(m['despachado']):>8}{_miles(m['recibido']):>8}{_miles(m['pendiente']):>8}"
                f"  {str(m['estado_dte'])[:22]}"
                for m in muestra
            ],
            'veredicto': (
                'Ninguna pantalla del módulo de existencias expone estos casos: la mercadería '
                'salió del origen, no entró al destino y el sistema la da por movida. Los DTE '
                f'de los últimos {DIAS_TRANSITO_TOLERADOS} días pueden estar legítimamente en '
                'camino; los más antiguos con estado EMITIDO y cero entradas son los urgentes.'
                if casos else 'Todos los traspasos del rango tienen la entrada completa.'
            ),
        }

    # ------------------------------------------------------------------
    # CHEQUEO 5 — stock plano vs lotes FIFO
    # ------------------------------------------------------------------
    def _chequeo_stock_vs_lotes(self, ctx):
        """
        Producto_Talla.stock contra la suma de LoteProducto.cantidad_disponible
        de los lotes activos. El exceso de stock sobre lotes es mercadería
        vendible SIN costo FIFO conocido.
        """
        base = Producto_Talla.objects.all()
        if ctx['sucursal_ids']:
            base = base.filter(producto__sucursal_id__in=ctx['sucursal_ids'])

        qs = (
            base.values('id', 'sku', 'stock', 'producto__sucursal_id', 'producto__costo')
            .annotate(
                lotes=Coalesce(
                    Sum('lotes__cantidad_disponible', filter=Q(lotes__activo=True)), Value(0)
                )
            )
            .exclude(lotes=F('stock'))
        )

        casos = 0
        sin_costo = 0     # stock > lotes
        lotes_sobrantes = 0  # lotes > stock
        plata = 0
        por_sucursal = {}
        peores = _Peores(ctx['muestra'])

        for fila in qs.iterator(chunk_size=5000):
            casos += 1
            delta = (fila['stock'] or 0) - (fila['lotes'] or 0)
            if delta > 0:
                sin_costo += delta
                plata += delta * (fila['producto__costo'] or 0)
            else:
                lotes_sobrantes += -delta
            acc = por_sucursal.setdefault(fila['producto__sucursal_id'], [0, 0])
            acc[0] += 1
            acc[1] += delta
            peores.agregar(abs(delta), {
                'producto_talla_id': fila['id'],
                'sku': fila['sku'],
                'stock': fila['stock'],
                'kardex': fila['lotes'],   # reutiliza la clave para el formateo
                'delta': delta,
            })

        muestra = self._hidratar_producto_talla(peores.payloads())

        lotes_activos = LoteProducto.objects.filter(activo=True)
        if ctx['sucursal_ids']:
            lotes_activos = lotes_activos.filter(
                producto_talla__producto__sucursal_id__in=ctx['sucursal_ids']
            )
        total_lotes = lotes_activos.aggregate(
            u=Coalesce(Sum('cantidad_disponible'), Value(0))
        )['u'] or 0

        if casos == 0:
            semaforo = OK
        elif sin_costo >= UMBRALES['stock_lotes_unidades_critico']:
            semaforo = CRITICO
        else:
            semaforo = REVISAR

        alias = self._mapa_sucursales()
        desglose = [
            f"{(alias.get(s) or f'#{s}')[:9]:>10}{_miles(v[0]):>12}{_miles(v[1]):>14}"
            for s, v in sorted(por_sucursal.items(), key=lambda x: -x[1][1])
        ]

        return {
            'clave': 'stock-lotes',
            'titulo': '5. Stock vendible sin costo FIFO',
            'que_mide': (
                'SKUs donde Producto_Talla.stock != suma de LoteProducto.cantidad_disponible '
                '(lotes activos).'
            ),
            'alcance': 'Foto del inventario actual (ignora --desde/--hasta por diseño).',
            'casos': casos,
            'unidades': sin_costo,
            'pesos': plata,
            'semaforo': semaforo,
            'metricas': [
                ('SKUs descuadrados vs lotes', _miles(casos)),
                ('Unidades en lotes activos', f'{_miles(total_lotes)} u'),
                ('Stock SIN costo FIFO', f'{_miles(sin_costo)} u'),
                ('Lotes que sobran sobre el stock', f'{_miles(lotes_sobrantes)} u'),
                ('Valorizado al costo del producto', _pesos(plata)),
            ],
            'desglose_titulo': 'Unidades sin costo por sucursal (delta = stock - lotes)',
            'desglose_columnas': [('SUC', 10), ('SKUs', 12), ('DELTA u', 14)],
            'desglose': desglose,
            'muestra': muestra,
            'muestra_columnas': [
                ('SKU', 12), ('SUC', 8), ('TALLA', 8), ('STOCK', 8),
                ('LOTES', 9), ('DELTA', 9), ('  ARTICULO', 26),
            ],
            'muestra_lineas': [
                f"{str(m['sku']):>12}{m['sucursal']:>8}{str(m['talla'])[:8]:>8}"
                f"{_miles(m['stock']):>8}{_miles(m['kardex']):>9}{_miles(m['delta']):>9}"
                f"  {(m['articulo'] or '')[:24]}"
                for m in muestra
            ],
            'veredicto': (
                'Estas unidades se pueden vender pero el sistema no sabe cuánto costaron: el '
                'margen de esas ventas es inauditable y contamina aging de capital y plan de '
                'liquidación. La causa conocida es la recepción de traspaso, que escribe stock '
                'sin crear lote.'
                if casos else 'El stock plano cuadra con los lotes FIFO en todo el alcance.'
            ),
        }

    # ------------------------------------------------------------------
    # CHEQUEO 6 — enlace compra <-> kardex
    # ------------------------------------------------------------------
    def _chequeo_recepciones_sin_movimiento(self, ctx):
        """
        Productos_Recepcionados.movimiento_ingreso en NULL: no se puede ir de
        una recepción (compra o traspaso) al movimiento de kardex que generó.
        """
        rango = (
            Q(fecha_recepcion__gte=ctx['desde_dt'], fecha_recepcion__lte=ctx['hasta_dt'])
            | Q(fecha_recepcion__isnull=True, fecha__gte=ctx['desde'], fecha__lte=ctx['hasta'])
        )
        base = Productos_Recepcionados.objects.filter(rango)
        if ctx['sucursal_ids']:
            base = base.filter(
                Q(sucursal_destino_id__in=ctx['sucursal_ids'])
                | Q(producto_talla__producto__sucursal_id__in=ctx['sucursal_ids'])
            )

        total = base.count()
        huerfanas = base.filter(movimiento_ingreso__isnull=True, es_historica=False)

        agg = huerfanas.aggregate(
            n=Count('id'),
            u=Coalesce(Sum('stockArribado'), Value(0)),
            compras=Count('id', filter=Q(compra_producto_talla__isnull=False)),
            traspasos=Count('id', filter=Q(dte__isnull=False)),
            plata=Coalesce(
                Sum(F('stockArribado') * F('producto_talla__producto__costo')), Value(0)
            ),
        )
        casos = agg['n'] or 0
        unidades = agg['u'] or 0
        plata = agg['plata'] or 0

        por_estado = list(
            huerfanas.values('estado').annotate(n=Count('id')).order_by('-n')[:12]
        )

        muestra = list(
            huerfanas.order_by('-stockArribado')
            .values('id', 'estado', 'stockArribado', 'fecha', 'fecha_recepcion',
                    'producto_talla__sku', 'producto_talla__producto__articulo',
                    'dte__numero_documento', 'sucursal_destino__alias',
                    'producto_talla__producto__sucursal__alias',
                    'recepcionado_por')[:ctx['muestra']]
        )
        for m in muestra:
            # sucursal_destino quedó NULL en casi todas las filas: caer a la
            # bodega dueña del SKU para que la muestra sea investigable.
            m['sucursal'] = (
                m['sucursal_destino__alias']
                or m['producto_talla__producto__sucursal__alias']
                or '-'
            )

        pct = _pct(casos, total)
        if casos == 0:
            semaforo = OK
        elif pct >= UMBRALES['recepciones_pct_critico']:
            semaforo = CRITICO
        else:
            semaforo = REVISAR

        return {
            'clave': 'recepciones-sin-mov',
            'titulo': '6. El eslabón compra <-> kardex está roto',
            'que_mide': 'Productos_Recepcionados con movimiento_ingreso en NULL (no históricas).',
            'alcance': (
                f"Recepciones entre {ctx['desde']} y {ctx['hasta']} "
                '(por fecha_recepcion; si es NULL, por fecha).'
            ),
            'casos': casos,
            'unidades': unidades,
            'pesos': plata,
            'semaforo': semaforo,
            'metricas': [
                ('Recepciones en el rango', _miles(total)),
                ('Sin movimiento asociado', f'{_miles(casos)}  ({pct}%)'),
                ('Con línea de compra / con DTE',
                 f"{_miles(agg['compras'])} / {_miles(agg['traspasos'])} (pueden solaparse)"),
                ('Unidades sin trazabilidad', f'{_miles(unidades)} u'),
                ('Valorizado al costo del producto', _pesos(plata)),
            ],
            'desglose_titulo': 'Por estado de recepción',
            'desglose_columnas': [('ESTADO', 26), ('FILAS', 10)],
            'desglose': [
                f"{(d['estado'] or '-')[:25]:>26}{_miles(d['n']):>10}" for d in por_estado
            ],
            'muestra': muestra,
            'muestra_columnas': [
                ('RECEP', 9), ('DTE', 9), ('SKU', 12), ('SUC', 8),
                ('CANT', 7), ('  ESTADO', 24),
            ],
            'muestra_lineas': [
                f"{m['id']:>9}{str(m['dte__numero_documento'] or '-'):>9}"
                f"{str(m['producto_talla__sku'] or '-'):>12}"
                f"{m['sucursal'][:7]:>8}"
                f"{_miles(m['stockArribado']):>7}  {str(m['estado'])[:22]}"
                for m in muestra
            ],
            'veredicto': (
                'Sin este enlace no se puede ir de una factura de compra al movimiento que '
                'ingresó la mercadería: la trazabilidad compra -> kardex no existe. Es un dato '
                'de auditoría faltante, no un descuadre de unidades.'
                if casos else 'Todas las recepciones del rango tienen su movimiento asociado.'
            ),
        }
