"""
Vistas de la API externa de RetailMind — contrato v1 con AllConnected.

Endpoints (montados bajo /api/ en urls.py raíz del proyecto):
  GET /api/skus/?rut_empresa=XXXXXXXX-X
  GET /api/articulos/{articulo_codigo}/tallas/?rut_empresa=XXXXXXXX-X
  GET /api/stock/movimientos/?rut_empresa=XXXXXXXX-X[&fecha_desde=YYYY-MM-DD]
  GET /api/guias-talla/?rut_empresa=XXXXXXXX-X
  GET /api/health/                          (sin autenticación)

Autenticación soportada:
  Authorization: Bearer {key}   (contrato oficial)
  X-Api-Key: {key}              (legacy, sigue funcionando)

Estructura de respuesta para /api/skus/ y /api/articulos/.../tallas/:
  Cada entrada en 'data' representa un PRODUCTO ÚNICO (articulo+marca+color+genero+categoria),
  con sus tallas anidadas y stock por sucursal. Un producto = una fila Shopify.
"""

import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

from django.utils import timezone

from app.models import Producto_Talla, Producto, GuiaTalla, GuiaTallaItem, Sucursal
from app.models.dte import Dte, Dte_Detalle_Pago, Dte_Productos
from app.models.ventas import Ticket, TicketDetallePago
from app.utils_ventas import canal_desde_plataforma_pago, es_metodo_pago_internet

from .authentication import ApiKeyAuthentication, ApiKeyPermission
from .serializers import ProductoExternalSerializer, agrupar_por_producto

logger = logging.getLogger(__name__)

# Anti-stampede para /api/skus/:
#   LOCK_TIMEOUT  — vida del lock que toma el worker que recalcula (segundos).
#                   Debe superar el peor tiempo de cálculo del catálogo.
#   STALE_TIMEOUT — vida de la copia "stale" que se sirve mientras un worker
#                   recalcula (más larga que la caché fresca de 15 min).
LOCK_TIMEOUT = 120
STALE_TIMEOUT = 3600

# Columnas compartidas para el queryset plano (una fila por sku × sucursal).
# NOTA: no incluir campos que no se usen en agrupar_por_producto() para
#       evitar JOINs innecesarios (ej. empresa__nombre_fantasia eliminado).
_VALUES_FIELDS = (
    'sku',
    'talla',
    'stock',
    'producto__articulo',
    'producto__descripcion',
    'producto__atributo1__valor',   # marca
    'producto__atributo2__valor',   # color
    'producto__atributo3__valor',   # género
    'producto__categoria__nombre',
    'producto__costo',
    'producto__precioventa',
    'producto__precioSugerido',
    'producto__sucursal__alias',
    'producto__sucursal__empresa_id',  # para resolver foto_portada_url
    'producto__guia_talla_id',
    'producto__tipo_talla',
)


def _build_qs(rut: str):
    """
    Queryset base: una fila por (Producto_Talla × Sucursal).

    Sin ORDER BY deliberado: agrupar_por_producto() agrupa en memoria
    con dicts y no depende del orden de entrada.  Evitar ORDER BY
    sobre columnas multi-tabla (articulo, atributo2__valor, alias)
    elimina un filesort costoso sobre tablas grandes.
    """
    return (
        Producto_Talla.objects
        .filter(producto__sucursal__empresa__rut=rut)
        .values(*_VALUES_FIELDS)
    )


# ──────────────────────────────────────────────
# Endpoint 1 — Productos/SKUs por empresa
# GET /api/skus/?rut_empresa=XXXXXXXX-X
# ──────────────────────────────────────────────

class SkusPorEmpresaView(APIView):
    """
    Retorna todos los productos de la empresa agrupados por
    (articulo, marca, color, genero, categoria), con tallas anidadas
    y stock por sucursal en cada talla.

    Un entry = un producto Shopify.
    Reemplaza: GET /obtenerSkusPorEmpresa (holdingtebes.cl)
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request):
        from django.core.cache import cache
        from app.services.realsport_imagenes_service import resolver_fotos_portada_bulk

        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cache de 15 min: el catálogo casi no cambia en ese rango y el endpoint
        # es pesado (~19K productos). Esto absorbe el polling agresivo de
        # AllConected.
        cache_key = f'external_skus_v1:{rut}'
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"[external/skus] rut={rut} -> CACHE HIT")
            return Response(cached)

        # Anti-stampede: cuando expira la caché de 15 min, varias requests
        # concurrentes (reintentos 1/3, 2/3 + polling) caen en MISS a la vez.
        # Sin esto, TODAS recalcularían el catálogo completo en paralelo y
        # saturarían la BD. Solo un worker toma el lock y recalcula; el resto
        # sirve la copia "stale" (válida 1h) en vez de recalcular.
        stale_key = f'{cache_key}:stale'
        lock_key = f'{cache_key}:lock'
        got_lock = cache.add(lock_key, '1', timeout=LOCK_TIMEOUT)
        if not got_lock:
            stale = cache.get(stale_key)
            if stale is not None:
                logger.info(f"[external/skus] rut={rut} -> MISS, lock tomado -> STALE")
                return Response(stale)
            # Cold start sin copia stale y con el lock ya tomado por otro worker:
            # no podemos devolver vacío, así que recalculamos igual. Caso raro
            # (primer arranque bajo carga concurrente).
            logger.info(f"[external/skus] rut={rut} -> MISS, sin stale -> recalcula igual")

        try:
            logger.info(f"[external/skus] rut={rut} -> CACHE MISS, calculando")
            rows = list(_build_qs(rut))

            # Resolver TODAS las portadas en 1-2 queries (antes: 1-2 por
            # producto → ~38K queries). Todas las filas comparten empresa_id
            # (filtradas por el mismo rut).
            articulos = {r.get('producto__articulo') for r in rows}
            empresa_id = next(
                (r.get('producto__sucursal__empresa_id') for r in rows
                 if r.get('producto__sucursal__empresa_id')),
                None,
            )
            fotos_map = resolver_fotos_portada_bulk(articulos, empresa_id)

            productos = agrupar_por_producto(rows, fotos_map=fotos_map)
            serializer = ProductoExternalSerializer(productos, many=True)
            logger.info(f"[external/skus] rut={rut} -> {len(productos)} productos ({len(rows)} filas raw)")
            response_data = {
                'success': True,
                'data': serializer.data,
                'total': len(productos),
                'error': None,
            }
            cache.set(cache_key, response_data, timeout=900)         # 15 min (fresca)
            cache.set(stale_key, response_data, timeout=STALE_TIMEOUT)  # 1h (respaldo)
            return Response(response_data)
        finally:
            if got_lock:
                cache.delete(lock_key)


# ──────────────────────────────────────────────
# Endpoint 2 — Tallas por artículo
# GET /api/articulos/{articulo_codigo}/tallas/?rut_empresa=XXXXXXXX-X
# ──────────────────────────────────────────────

class TallasPorArticuloView(APIView):
    """
    Retorna los productos que comparten el mismo código de artículo,
    agrupados por (articulo, marca, color, genero, categoria), con tallas.

    Útil cuando AllConnected necesita refrescar un artículo específico.
    Reemplaza: GET /consultaTallasEcommerce (holdingtebes.cl)
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request, articulo_codigo: str):
        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(f"[external/tallas] articulo={articulo_codigo} rut={rut}")
        rows = list(_build_qs(rut).filter(producto__articulo=articulo_codigo))
        if not rows:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': f'Artículo {articulo_codigo!r} no encontrado para rut {rut}.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        productos = agrupar_por_producto(rows)
        serializer = ProductoExternalSerializer(productos, many=True)
        logger.info(f"[external/tallas] articulo={articulo_codigo} → {len(productos)} productos")
        return Response({
            'success': True,
            'data': serializer.data,
            'total': len(productos),
            'error': None,
        })


# ──────────────────────────────────────────────
# Endpoint 3 — Stock incremental
# GET /api/stock/movimientos/?rut_empresa=XXXXXXXX-X[&fecha_desde=YYYY-MM-DD]
# ──────────────────────────────────────────────

class StockMovimientosView(APIView):
    """
    Retorna stock actual por SKU×sucursal (formato plano minimal).
    Si 'fecha_desde' se envía y Producto_Talla tiene updated_at, filtra
    solo los SKUs modificados desde esa fecha; caso contrario devuelve
    el snapshot completo actual.

    Este endpoint no necesita la agrupación por producto —
    AllConnected solo necesita {sku, sucursales} para actualizar stock.
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request):
        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fecha_desde = request.query_params.get('fecha_desde', '')

        logger.info(f"[external/stock/movimientos] rut={rut} fecha_desde={fecha_desde or 'N/A'}")
        qs = Producto_Talla.objects.filter(producto__sucursal__empresa__rut=rut)

        if fecha_desde and hasattr(Producto_Talla, 'updated_at'):
            try:
                from datetime import datetime
                dt = datetime.strptime(fecha_desde, '%Y-%m-%d')
                qs = qs.filter(updated_at__date__gte=dt.date())
                logger.info(f"[external/stock/movimientos] filtrando por updated_at >= {fecha_desde}")
            except (ValueError, TypeError):
                logger.warning(f"[external/stock/movimientos] fecha_desde inválida: {fecha_desde}, devolviendo snapshot completo")

        rows = list(qs.values('sku', 'stock', 'producto__sucursal__alias'))

        grupos: dict = {}
        for row in rows:
            k = str(row['sku'])
            if k not in grupos:
                grupos[k] = {'sku': k, 'sucursales': []}
            grupos[k]['sucursales'].append({
                'nombre': row.get('producto__sucursal__alias', '') or '',
                'stock':  int(row.get('stock', 0) or 0),
            })

        data = list(grupos.values())
        logger.info(f"[external/stock/movimientos] rut={rut} → {len(data)} SKUs")
        return Response({'success': True, 'data': data, 'total': len(data), 'error': None})


# ──────────────────────────────────────────────
# Endpoint 3b — Stock filtrado por SKUs (con sucursal_id)
# GET /api/stock/por-skus/?rut_empresa=XX-X&skus=A,B,C
# ──────────────────────────────────────────────

class StockPorSkusView(APIView):
    """
    Retorna stock por SKU × sucursal incluyendo `sucursal_id`. Filtrable por
    una lista de SKUs para evitar overfetch. Usado por AllConnected cuando
    resuelve dinámicamente a qué sucursal enviar un pedido.

    Soporta dos modos:
      * GET  /api/stock/por-skus/?rut_empresa=X[&skus=A,B,C]  (legacy)
      * POST /api/stock/por-skus/  body={"rut_empresa": "X", "skus": [...]}

    El POST permite listas de SKUs de cualquier tamaño sin chocar con el
    límite de URL del servidor (~4 KB para Request Line). El cliente
    AllConnected debe usarlo cuando la lista es grande (miles de SKUs en una
    sola request en vez de cientos de GET troceados).

    SKUs no-numéricos en la lista se IGNORAN (RetailMind almacena `sku` como
    BigIntegerField) y se reportan en `skus_invalidos` cuando los haya.

    Respuesta:
    {
      "success": true,
      "data": [
        {
          "sku": "4810070",
          "sucursales": [
            {"id": 12, "alias": "PAO1", "stock": 5},
            {"id": 13, "alias": "PAO2", "stock": 2}
          ]
        }
      ],
      "total": 1,
      "skus_invalidos": ["4775856-792BF6", ...],   # solo si hubo
      "error": null
    }
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    # ── Helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _parse_skus(raw_skus):
        """Acepta lista|str CSV. Separa válidos (numéricos) e inválidos.

        RetailMind almacena `sku` como BigIntegerField; SKUs con guiones u
        otros caracteres no existen en este lado. Filtrarlos antes del query
        evita el cast implícito string→bigint y permite reportarlos.
        """
        if isinstance(raw_skus, str):
            items = [s.strip() for s in raw_skus.split(',') if s.strip()]
        elif isinstance(raw_skus, (list, tuple)):
            items = [str(s).strip() for s in raw_skus if str(s).strip()]
        else:
            items = []
        validos, invalidos = [], []
        for s in items:
            if s.isdigit():
                validos.append(int(s))
            else:
                invalidos.append(s)
        return validos, invalidos

    def _build_response(self, rut, skus_validos, skus_invalidos):
        qs = Producto_Talla.objects.filter(producto__sucursal__empresa__rut=rut)
        if skus_validos:
            qs = qs.filter(sku__in=skus_validos)

        rows = list(qs.values(
            'sku', 'stock',
            'producto__sucursal__id',
            'producto__sucursal__alias',
        ))

        grupos: dict = {}
        for row in rows:
            k = str(row['sku'])
            if k not in grupos:
                grupos[k] = {'sku': k, 'sucursales': []}
            grupos[k]['sucursales'].append({
                'id': row.get('producto__sucursal__id'),
                'alias': row.get('producto__sucursal__alias', '') or '',
                'stock': int(row.get('stock', 0) or 0),
            })

        data = list(grupos.values())
        logger.info(
            "[external/stock/por-skus] rut=%s validos=%d invalidos=%d -> %d SKUs",
            rut, len(skus_validos), len(skus_invalidos), len(data),
        )

        payload = {'success': True, 'data': data, 'total': len(data), 'error': None}
        if skus_invalidos:
            payload['skus_invalidos'] = skus_invalidos[:50]  # acotado por log
            payload['skus_invalidos_total'] = len(skus_invalidos)
        return Response(payload)

    # ── GET (legacy, soporta query string) ───────────────────────────────
    def get(self, request):
        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skus_validos, skus_invalidos = self._parse_skus(
            request.query_params.get('skus', '')
        )
        return self._build_response(rut, skus_validos, skus_invalidos)

    # ── POST (preferido para listas grandes, sin límite de URL) ──────────
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        rut = str(body.get('rut_empresa') or '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'rut_empresa es obligatorio en el body JSON.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skus_validos, skus_invalidos = self._parse_skus(body.get('skus') or [])
        return self._build_response(rut, skus_validos, skus_invalidos)


# ──────────────────────────────────────────────
# Endpoint 3c — Stock GLOBAL por empresa (suma de todas las sucursales)
# GET /api/stock/global/?rut_empresa=XX-X[&skus=A,B,C]
# ──────────────────────────────────────────────

class StockGlobalView(APIView):
    """
    Retorna stock TOTAL por SKU sumando TODAS las sucursales de la empresa.

    Soporta dos modos:
      * GET  /api/stock/global/?rut_empresa=X[&skus=A,B,C]  (legacy)
      * POST /api/stock/global/  body={"rut_empresa": "X", "skus": [...]}

    El POST permite listas de SKUs de cualquier tamaño sin chocar con el
    límite de URL del servidor (4094 bytes para Request Line). El cliente
    AllConnected lo usa cuando la lista supera ~400 SKUs.

    SKUs no-numéricos en la lista se IGNORAN silenciosamente (RetailMind
    almacena `sku` como IntegerField; SKUs con guiones u otros caracteres
    no existen en este lado y devolverlos no sirve).

    Respuesta:
    {
      "success": true,
      "data": [{"sku": "4810070", "stock_total": 15}, ...],
      "total": N,
      "rut_empresa": "76104936-4",
      "skus_invalidos": ["4775856-792BF6", ...],   # solo si hubo
      "error": null
    }
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    # ── Helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _parse_skus(raw_skus):
        """Acepta lista|str CSV. Separa válidos (numéricos) e inválidos."""
        if isinstance(raw_skus, str):
            items = [s.strip() for s in raw_skus.split(',') if s.strip()]
        elif isinstance(raw_skus, (list, tuple)):
            items = [str(s).strip() for s in raw_skus if str(s).strip()]
        else:
            items = []
        validos, invalidos = [], []
        for s in items:
            if s.isdigit():
                validos.append(int(s))
            else:
                invalidos.append(s)
        return validos, invalidos

    def _build_response(self, rut, skus_validos, skus_invalidos):
        from django.db.models import Sum

        qs = Producto_Talla.objects.filter(producto__sucursal__empresa__rut=rut)
        if skus_validos:
            qs = qs.filter(sku__in=skus_validos)

        aggregated = (
            qs.values('sku')
              .annotate(stock_total=Sum('stock'))
              .order_by('sku')
        )

        data = [
            {'sku': str(row['sku']), 'stock_total': int(row['stock_total'] or 0)}
            for row in aggregated
        ]

        logger.info(
            "[external/stock/global] rut=%s validos=%d invalidos=%d -> %d SKUs",
            rut, len(skus_validos), len(skus_invalidos), len(data),
        )

        payload = {
            'success': True,
            'data': data,
            'total': len(data),
            'rut_empresa': rut,
            'error': None,
        }
        if skus_invalidos:
            payload['skus_invalidos'] = skus_invalidos[:50]  # acotado por log
            payload['skus_invalidos_total'] = len(skus_invalidos)
        return Response(payload)

    # ── GET (legacy, soporta query string) ───────────────────────────────
    def get(self, request):
        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skus_validos, skus_invalidos = self._parse_skus(
            request.query_params.get('skus', '')
        )
        return self._build_response(rut, skus_validos, skus_invalidos)

    # ── POST (preferido para listas grandes, sin límite de URL) ──────────
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        rut = str(body.get('rut_empresa') or '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'rut_empresa es obligatorio en el body JSON.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        skus_validos, skus_invalidos = self._parse_skus(body.get('skus') or [])
        return self._build_response(rut, skus_validos, skus_invalidos)


# ──────────────────────────────────────────────
# Endpoint 4 — Health check (sin auth)
# GET /api/health/
# ──────────────────────────────────────────────

class HealthCheckExternalView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok'})


# ──────────────────────────────────────────────
# Endpoint 6 — Sucursales por empresa
# GET /api/sucursales/?rut_empresa=XXXXXXXX-X
# ──────────────────────────────────────────────

def _normalizar_rut(rut: str) -> str:
    return (rut or '').replace('.', '').replace(' ', '').upper().strip()


class SucursalesPorEmpresaView(APIView):
    """
    Lista las sucursales activas de la empresa indicada por rut_empresa.

    Usado por VicentAllEcommercesConected para que el operador elija
    a qué sucursal enviar los pedidos de cada Canal, garantizando que
    la sucursal elegida pertenezca a la empresa del canal.
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request):
        rut = _normalizar_rut(request.query_params.get('rut_empresa', ''))
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(f"[external/sucursales] rut={rut}")
        sucursales = (
            Sucursal.objects
            .filter(empresa__rut__isnull=False, activa=True)
            .select_related('empresa')
        )
        data = [
            {
                'id': s.id,
                'alias': s.alias,
                'nombre': s.nombre,
                'tipo_sucursal': getattr(s, 'tipo_sucursal', '') or '',
                'direccion': s.direccion or '',
                'empresa_rut': s.empresa.rut,
                'empresa_nombre': s.empresa.nombre,
            }
            for s in sucursales
            if _normalizar_rut(s.empresa.rut) == rut
        ]
        logger.info(f"[external/sucursales] rut={rut} → {len(data)} sucursales")
        return Response({
            'success': True,
            'data': data,
            'total': len(data),
            'error': None,
        })


# ──────────────────────────────────────────────
# Endpoint 5 — Guías de talla por empresa
# GET /api/guias-talla/?rut_empresa=XXXXXXXX-X
# ──────────────────────────────────────────────

class GuiasTallaExternalView(APIView):
    """
    Retorna todas las guías de talla asociadas a productos de la empresa,
    incluyendo los items de conversión y los códigos de artículo vinculados.

    AllConnected usa esta información para crear/sincronizar GuiaTallas
    en su modelo multi-sistema y asignarlas a los ProductoMaster importados.
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request):
        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(f"[external/guias-talla] rut={rut}")

        productos_empresa = Producto.objects.filter(
            sucursal__empresa__rut=rut,
        )

        guia_ids_directo = set(
            productos_empresa
            .filter(guia_talla__isnull=False)
            .values_list('guia_talla_id', flat=True)
        )

        from app.models import GuiaTallaProducto
        guia_ids_m2m = set(
            GuiaTallaProducto.objects
            .filter(producto__sucursal__empresa__rut=rut)
            .values_list('guia_id', flat=True)
        )

        all_guia_ids = guia_ids_directo | guia_ids_m2m
        if not all_guia_ids:
            logger.info(f"[external/guias-talla] rut={rut} → 0 guías")
            return Response({
                'success': True, 'data': [], 'total': 0, 'error': None,
            })

        guias = (
            GuiaTalla.objects
            .filter(id__in=all_guia_ids)
            .prefetch_related('items', 'productos_principales', 'productos')
        )

        data = []
        for guia in guias:
            items = list(
                guia.items.order_by('orden').values(
                    'cl', 'us', 'eu', 'uk', 'br', 'cm', 'orden',
                )
            )

            articulos_fk = set(
                guia.productos_principales
                .filter(sucursal__empresa__rut=rut)
                .values_list('articulo', flat=True)
            )
            articulos_m2m = set(
                guia.productos
                .filter(sucursal__empresa__rut=rut)
                .values_list('articulo', flat=True)
            )
            productos_articulos = sorted(articulos_fk | articulos_m2m)

            data.append({
                'id': guia.id,
                'nombre': guia.nombre,
                'marca': guia.marca.valor if guia.marca else '',
                'items': items,
                'productos_articulos': productos_articulos,
            })

        logger.info(f"[external/guias-talla] rut={rut} → {len(data)} guías")
        return Response({
            'success': True,
            'data': data,
            'total': len(data),
            'error': None,
        })


# ──────────────────────────────────────────────
# Endpoint 7 — Precios actuales por empresa
# GET /api/precios-actuales/?rut_empresa=XXXXXXXX-X
# ──────────────────────────────────────────────

# Conceptos de Movimientos_Producto que representan una RECEPCIÓN REAL de
# mercadería nueva (compra a proveedor / saldo de apertura / reposición).
# Se usan para la antigüedad FIFO en vez de tipo_movimiento='INGRESO', porque
# ese bucket también contiene traspasos internos (TRASPASO_ENTRADA) y, en datos
# migrados, ventas mal clasificadas (VENTA_MAYORISTA con tipo 'Ingreso' heredado
# del legacy). Filtrar por concepto da la antigüedad real del stock comprado,
# tanto en datos migrados (RECEPCION_COMPRA / INGRESO_INICIAL) como en los nuevos
# (INGRESO_INICIAL para recepción de compra, SOBRANTE_INGRESO, etc.).
CONCEPTOS_RECEPCION_STOCK = (
    'RECEPCION_COMPRA',
    'INGRESO_INICIAL',
    'INGRESO_MANUAL',
    'REPOSICION_STOCK',
    'SOBRANTE_INGRESO',
)

# El saldo de apertura de la migración Laravel se cargó como INGRESO_INICIAL
# pero con `fecha = fecha de la carga` (≈2026-01-22), NO la llegada real del
# stock (que el legacy no guardaba a nivel SKU). Marca: referencia_externa.
# Estos movimientos NO son recepciones reales y aplanan la antigüedad, así que
# se excluyen del cálculo de fechas; para esos SKU se usa `fecha_creacion`, que
# sí refleja la antigüedad real (corregida desde los movimientos migrados).
REF_SALDO_INICIAL_SINTETICO = 'MIGRACION_LARAVEL'


class PreciosActualesView(APIView):
    """
    Retorna precios, costos y última fecha de ingreso a nivel EMPRESA, una fila
    por SKU. Si el mismo SKU vive en múltiples sucursales (un Producto_Talla
    distinto por sucursal), se consolida con MAX de costo, precio_venta y
    precio_sugerido (regla de negocio: el SKU expuesto al ecommerce debe
    publicarse con el mayor precio/costo entre sucursales).

    Todas las fechas salen de Movimientos_Producto (historial real preservado en
    la migración legacy), NO de los lotes (que son sintéticos/consolidados y no
    sirven como referencia de antigüedad).

    IMPORTANTE: las recepciones se identifican por `concepto`
    (CONCEPTOS_RECEPCION_STOCK), NO por tipo_movimiento, porque en datos migrados
    el tipo no es confiable (hay traspasos internos y ventas marcados como
    INGRESO). Las ventas se detectan por concepto 'VENTA_*'.

    `ultima_fecha_ingreso`  = fecha de la ÚLTIMA recepción real del SKU
                              (concepto de recepción). Formato YYYY-MM-DD.
                              Se EXCLUYE el saldo de apertura sintético de la
                              migración (referencia_externa=MIGRACION_LARAVEL),
                              cuya fecha es la de la carga (~2026-01-22) y no la
                              recepción real.
                              FALLBACK: si el SKU no tiene recepción real (entró
                              solo por traspaso/ventas, o su único ingreso era el
                              saldo sintético), cae a `fecha_creacion`, que sí
                              refleja la antigüedad real (corregida desde los
                              movimientos migrados). La recepción real manda.

    `fecha_creacion`        = alta del PRODUCTO (no del SKU) más antigua entre
                              sucursales. Antigüedad del MODELO en catálogo.

    `stock_actual`          = unidades en mano del SKU (suma entre sucursales).

    `ultima_fecha_venta`    = fecha del último EGRESO/venta del SKU (o null).

    `fecha_antiguedad_stock`= FIFO: fecha de la entrada más vieja que TODAVÍA no
                              se vendió (se recorren los ingresos de más nuevo a
                              más viejo acumulando cantidad hasta cubrir el stock
                              actual). Es la antigüedad REAL del stock en góndola
                              — no se infla cuando hay reposiciones. Si tras
                              excluir el saldo sintético no quedan recepciones,
                              cae a `fecha_creacion`. null solo si no hay stock.

    `dias_antiguedad_stock` = days(today - fecha_antiguedad_stock). En datos
                              migrados sin recepción real cae a fecha_creacion
                              (antigüedad real). Informativo; el driver principal
                              de descuento sigue siendo `dias_sin_venta`.

    `dias_sin_venta`        = DRIVER PRINCIPAL de descuento. days(today - última
                              venta); si el SKU nunca vendió, days(today -
                              fecha_creacion). Identifica stock estancado de forma
                              fiable (las ventas migradas conservan fecha real).
                              AllConnected debe usar ESTE campo. null si no aplica.

    Nota de consolidación: todo se agrupa por SKU (pooled entre sucursales),
    coherente con un SKU = una fila de ecommerce.

    Respuesta:
    {
        "success": true,
        "data": [
            {
                "codigo_sku": "4810070",
                "articulo": "ART001",
                "precio_venta": 59990,
                "precio_costo": 25000,
                "precio_sugerido": 64990,
                "stock_actual": 12,
                "ultima_fecha_ingreso": "2025-09-15",
                "fecha_creacion": "2024-08-01",
                "ultima_fecha_venta": "2026-05-02",
                "fecha_antiguedad_stock": "2025-01-20",
                "dias_antiguedad_stock": 120,
                "dias_sin_venta": 18
            }
        ],
        "total": 123,
        "timestamp": "2026-05-20T12:00:00Z"
    }
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request):
        from django.db.models import Max
        from app.models import Movimientos_Producto

        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(f"[external/precios-actuales] rut={rut}")

        # ── 1. Precios (MAX), fecha_creacion (MIN) y stock (SUM) por SKU ──
        # 1 fila por (sku × sucursal); se consolida a 1 fila por SKU.
        consolidado: dict = {}
        pt_rows = (
            Producto_Talla.objects
            .filter(producto__sucursal__empresa__rut=rut)
            .values(
                'sku', 'stock',
                'producto__articulo', 'producto__costo',
                'producto__precioventa', 'producto__precioSugerido',
                'producto__fecha_creacion',
            )
        )
        for row in pt_rows.iterator(chunk_size=2000):
            sku = str(row['sku'])
            if not sku:
                continue

            costo = int(row.get('producto__costo', 0) or 0)
            precio_venta = int(row.get('producto__precioventa', 0) or 0)
            precio_sugerido = int(row.get('producto__precioSugerido', 0) or 0)
            stock = max(int(row.get('stock', 0) or 0), 0)
            fecha_creacion = row.get('producto__fecha_creacion')

            base = consolidado.get(sku)
            if base is None:
                consolidado[sku] = {
                    'codigo_sku': sku,
                    'articulo': row.get('producto__articulo', '') or '',
                    'precio_venta': precio_venta,
                    'precio_costo': costo,
                    'precio_sugerido': precio_sugerido,
                    'stock_actual': stock,
                    '_fecha_creacion': fecha_creacion,
                }
            else:
                base['precio_venta'] = max(base['precio_venta'], precio_venta)
                base['precio_costo'] = max(base['precio_costo'], costo)
                base['precio_sugerido'] = max(base['precio_sugerido'], precio_sugerido)
                base['stock_actual'] += stock
                # fecha_creacion: la MÁS ANTIGUA entre sucursales (alta original del SKU)
                if fecha_creacion and (
                    not base['_fecha_creacion'] or fecha_creacion < base['_fecha_creacion']
                ):
                    base['_fecha_creacion'] = fecha_creacion

        # Solo los SKU con stock > 0 son relevantes para antigüedad/descuento.
        skus_con_stock = [
            int(s) for s, b in consolidado.items() if b['stock_actual'] > 0
        ]

        # ── 2. Movimientos de INGRESO (fecha, cantidad) por SKU, para FIFO ──
        # Acotado a SKU con stock vía subquery server-side (menos filas).
        ingresos_por_sku: dict = {}
        if skus_con_stock:
            ingresos_qs = (
                Movimientos_Producto.objects
                .filter(
                    concepto__in=CONCEPTOS_RECEPCION_STOCK,
                    ProductoTalla__sku__in=skus_con_stock,
                    ProductoTalla__producto__sucursal__empresa__rut=rut,
                )
                # Excluir el saldo de apertura sintético de la migración: su fecha
                # es la de la carga (~2026-01-22), no la recepción real. Sin esto,
                # el bulk migrado quedaría aplanado a esa fecha.
                .exclude(referencia_externa=REF_SALDO_INICIAL_SINTETICO)
                .values('ProductoTalla__sku', 'fecha', 'cantidad')
                .order_by('ProductoTalla__sku', '-fecha')
            )
            for m in ingresos_qs.iterator(chunk_size=5000):
                ingresos_por_sku.setdefault(str(m['ProductoTalla__sku']), []).append(
                    (m['fecha'], int(m['cantidad'] or 0))
                )

        # ── 3. Última venta (EGRESO) por SKU ──
        ultima_venta_por_sku: dict = {}
        if skus_con_stock:
            for v in (
                Movimientos_Producto.objects
                .filter(
                    concepto__startswith='VENTA_',
                    ProductoTalla__sku__in=skus_con_stock,
                    ProductoTalla__producto__sucursal__empresa__rut=rut,
                )
                .values('ProductoTalla__sku')
                .annotate(ultima=Max('fecha'))
            ):
                ultima_venta_por_sku[str(v['ProductoTalla__sku'])] = v['ultima']

        # ── 4. Armar respuesta con la antigüedad FIFO del stock en mano ──
        hoy = timezone.localdate()
        data = []
        for sku, info in consolidado.items():
            fecha_creacion = info.pop('_fecha_creacion', None)
            stock = info['stock_actual']
            ingresos = ingresos_por_sku.get(sku, [])  # ya ordenado desc por fecha

            # Última recepción real (MAX) = primer elemento (orden desc).
            ultima_ingreso = ingresos[0][0] if ingresos else None

            # FIFO: por convención se vende primero lo más antiguo, así que el
            # stock en mano son las entradas más recientes. Recorremos los
            # ingresos de más nuevo a más viejo acumulando cantidad hasta cubrir
            # el stock actual; esa fecha es la del stock más viejo que aún queda
            # (= antigüedad real del stock, lo que importa para descuentos).
            fecha_antiguedad = None
            if stock > 0 and ingresos:
                acc = 0
                for fecha_mov, cant in ingresos:
                    acc += max(cant, 0)
                    if acc >= stock:
                        fecha_antiguedad = fecha_mov
                        break
                if fecha_antiguedad is None:
                    # Ingresos registrados < stock → usar el más antiguo disponible.
                    fecha_antiguedad = ingresos[-1][0]

            fecha_creacion_local = (
                timezone.localtime(fecha_creacion).date() if fecha_creacion else None
            )

            # Fallback: si el SKU no tiene recepción real (entró solo por traspaso o
            # ventas), usar la fecha de creación del producto como referencia de
            # antigüedad. El día exacto no es crítico para el descuento del ecommerce;
            # lo importante es no devolver null. La recepción real, si existe, manda.
            ultima_ingreso_efectiva = ultima_ingreso or fecha_creacion_local
            info['ultima_fecha_ingreso'] = (
                ultima_ingreso_efectiva.strftime('%Y-%m-%d') if ultima_ingreso_efectiva else None
            )
            info['fecha_creacion'] = (
                fecha_creacion_local.strftime('%Y-%m-%d') if fecha_creacion_local else None
            )
            ultima_venta = ultima_venta_por_sku.get(sku)
            info['ultima_fecha_venta'] = (
                ultima_venta.strftime('%Y-%m-%d') if ultima_venta else None
            )
            # Si el FIFO no arrojó fecha (no quedan recepciones reales tras excluir
            # el saldo sintético) pero hay stock, usar fecha_creacion como antigüedad
            # real en vez de devolver null o la fecha aplanada de la carga.
            fecha_antiguedad_efectiva = fecha_antiguedad or (
                fecha_creacion_local if stock > 0 else None
            )
            info['fecha_antiguedad_stock'] = (
                fecha_antiguedad_efectiva.strftime('%Y-%m-%d') if fecha_antiguedad_efectiva else None
            )
            info['dias_antiguedad_stock'] = (
                (hoy - fecha_antiguedad_efectiva).days if fecha_antiguedad_efectiva else None
            )

            # Driver PRINCIPAL de descuento: días sin vender. Es lo que la data
            # soporta de forma fiable (las ventas migradas conservan fecha real,
            # a diferencia de la antigüedad de stock, aplanada por el saldo de
            # apertura de la migración). Si nunca vendió, se mide el estancamiento
            # desde la fecha de creación del modelo.
            ref_estancamiento = ultima_venta or fecha_creacion_local
            info['dias_sin_venta'] = (
                (hoy - ref_estancamiento).days if ref_estancamiento else None
            )
            data.append(info)

        logger.info(f"[external/precios-actuales] rut={rut} → {len(data)} SKUs (consolidados)")
        return Response({
            'success': True,
            'data': data,
            'total': len(data),
            'timestamp': timezone.now().isoformat(),
            'error': None,
        })


# ──────────────────────────────────────────────
# Endpoint 8 — Novedades (productos nuevos)
# GET /api/novedades/?rut_empresa=XXXXXXXX-X&desde=YYYY-MM-DD
# ──────────────────────────────────────────────

class NovedadesView(APIView):
    """
    Retorna productos/SKUs creados O MODIFICADOS recientemente.
    Usado por AllConnected para detectar nueva mercadería y cambios de
    precio/stock/costo sin hacer un full sync.

    Un producto aparece como novedad si cumple CUALQUIERA de:
      - Producto.fecha_creacion >= desde  (producto nuevo)
      - Producto.fecha_actualizacion >= desde  (precio/costo cambió, ej. recepción de compra)
      - Producto_Talla.updated_at >= desde  (stock cambió, ej. movimiento de inventario)

    Parámetros:
      rut_empresa (obligatorio)
      desde (YYYY-MM-DD, default: últimos 7 días)
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request):
        from datetime import timedelta
        from django.db.models import Q

        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        desde_str = request.query_params.get('desde', '').strip()
        if desde_str:
            try:
                from datetime import datetime
                desde = datetime.strptime(desde_str, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {'success': False, 'data': [], 'total': 0,
                     'error': 'Formato de fecha inválido. Use YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            desde = (timezone.now() - timedelta(days=7)).date()

        logger.info(f"[external/novedades] rut={rut} desde={desde}")

        # Productos de la empresa
        base_qs = Producto.objects.filter(sucursal__empresa__rut=rut)

        # 1. Productos creados o actualizados desde la fecha
        filtro_producto = Q()
        if hasattr(Producto, 'fecha_creacion'):
            filtro_producto |= Q(fecha_creacion__date__gte=desde)
        if hasattr(Producto, 'fecha_actualizacion'):
            filtro_producto |= Q(fecha_actualizacion__date__gte=desde)

        productos_tocados = base_qs.filter(filtro_producto)

        # 2. Productos cuyas tallas tuvieron movimiento de stock
        productos_con_stock_nuevo = set()
        if hasattr(Producto_Talla, 'updated_at'):
            tallas_tocadas = (
                Producto_Talla.objects
                .filter(
                    producto__sucursal__empresa__rut=rut,
                    updated_at__date__gte=desde,
                )
                .values_list('producto_id', flat=True)
                .distinct()
            )
            productos_con_stock_nuevo = set(tallas_tocadas)

        # Combinar: IDs de productos tocados por cualquier vía
        ids_tocados = set(productos_tocados.values_list('id', flat=True))
        ids_tocados |= productos_con_stock_nuevo
        qs_final = base_qs.filter(id__in=ids_tocados) if ids_tocados else base_qs.none()

        rows = list(
            Producto_Talla.objects
            .filter(producto__in=qs_final)
            .values(*_VALUES_FIELDS)
        )

        productos = agrupar_por_producto(rows)
        serializer = ProductoExternalSerializer(productos, many=True)

        logger.info(
            f"[external/novedades] rut={rut} desde={desde} → "
            f"{len(productos)} productos (creados/actualizados={len(ids_tocados)})"
        )
        return Response({
            'success': True,
            'data': serializer.data,
            'total': len(productos),
            'desde': str(desde),
            'timestamp': timezone.now().isoformat(),
            'error': None,
        })


# ──────────────────────────────────────────────
# Endpoint 10 — Movimientos de venta (Dte + líneas)
# GET /api/movimientos-ventas/?rut_empresa=...&fecha_desde=YYYY-MM-DD&fecha_hasta=YYYY-MM-DD
# ──────────────────────────────────────────────

class MovimientosVentasView(APIView):
    """
    Lista de movimientos de venta (líneas de DTEs emitidos) en un rango de
    fechas para una empresa. Diseñado como reemplazo de la API legacy de
    HoldingTebes ``/consultaProductosVentasInternet`` consumida por la
    pantalla de devoluciones de AllConnected.

    Query params:
      rut_empresa   (obligatorio)
      fecha_desde   YYYY-MM-DD (obligatorio)
      fecha_hasta   YYYY-MM-DD (opcional; si falta usa fecha_desde)
      sku           (opcional, búsqueda parcial en sku del Producto_Talla)
      boleta        (opcional, búsqueda parcial en numero_documento del Dte)
      marca         (opcional, exact-icontains sobre Producto.atributo1)
      alias         (opcional, exact sobre Sucursal.alias)
      tipo_documento (opcional, exact: BOLETA / FACTURA / NOTA_CREDITO / etc.)
      tipo_movimiento (opcional, 'Egreso' por defecto = ventas; 'Ingreso' = NC)
      limit         (opcional, default 5000)

    El response replica el shape de la API HT — el frontend de devoluciones
    (devoluciones.html) lo lee sin cambios:

      {
        "success": True,
        "fecha_desde": "...",
        "fecha_hasta": "...",
        "total_movimientos": int,
        "total_unidades": int,
        "resumen_por_marca": {marca: cantidad, ...},
        "resumen_por_bodega": {alias: cantidad, ...},
        "movimientos": [
          {
            "codigo_asociado": "<sku>",
            "articulo": "<descripcion del producto>",
            "marca": "<atributo1>",
            "alias": "<alias sucursal>",
            "sucursal": "<dirección sucursal>",
            "N_documento": <numero_documento>,
            "voucher": <ticket.correlativo o None>,
            "n_pedido": <ticket.correlativo o None>,  # alias frontend
            "tt": <stock>,        # cantidad
            "cantidad": <stock>,  # alias
            "precio": <precio>,
            "costo": <costo>,
            "fecha": "YYYY-MM-DD",
            "hora": "HH:MM:SS",
            "tipo_movimiento": "Egreso" | "Ingreso",
            "tipo_documento": "<tipo>",
            "concepto": "Venta" | "NC" | ...,
          },
          ...
        ]
      }
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    # Mapeo: tipo_documento del Dte → concepto/movimiento. NC = nota de crédito
    # (devolución/anulación) = Ingreso desde la perspectiva del stock.
    _TIPOS_INGRESO = {"NOTA_CREDITO", "NOTA_CREDITO_ELECTRONICA", "NC"}

    def get(self, request):
        from datetime import datetime, timedelta

        rut          = request.query_params.get('rut_empresa', '').strip()
        fecha_desde  = request.query_params.get('fecha_desde', '').strip()
        fecha_hasta  = request.query_params.get('fecha_hasta', '').strip() or fecha_desde
        sku_filt     = request.query_params.get('sku', '').strip()
        boleta_filt  = request.query_params.get('boleta', '').strip()
        marca_filt   = request.query_params.get('marca', '').strip()
        alias_filt   = request.query_params.get('alias', '').strip()
        tipo_doc     = request.query_params.get('tipo_documento', '').strip().upper()
        tipo_mov     = request.query_params.get('tipo_movimiento', '').strip()
        limit        = int(request.query_params.get('limit', 5000) or 5000)

        if not rut:
            return Response(
                {'success': False, 'error': 'rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not fecha_desde:
            return Response(
                {'success': False, 'error': 'fecha_desde es obligatorio (YYYY-MM-DD).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            d_from = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
            d_to   = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'success': False, 'error': 'Formato de fecha inválido. Usa YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if d_to < d_from:
            return Response(
                {'success': False, 'error': 'fecha_hasta debe ser >= fecha_desde.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Cap defensivo: el frontend ya valida ≤ 7 días, pero protegemos el
        # backend en caso de uso directo de la API.
        if (d_to - d_from) > timedelta(days=31):
            return Response(
                {'success': False, 'error': 'Rango máximo 31 días.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # === 1. Query base de DTEs en rango y empresa ===
        # Nos quedamos con DTEs que efectivamente generan movimiento de stock
        # (ventas y notas de crédito de venta). Ignoramos descartados.
        dtes_qs = Dte.objects.filter(
            emisor__rut=rut,
            fecha_emision__gte=d_from,
            fecha_emision__lte=d_to,
            descartado=False,
        ).exclude(estado_dte__in=['ANULADO', 'RECHAZADO'])

        if tipo_doc:
            dtes_qs = dtes_qs.filter(tipo_documento=tipo_doc)
        if boleta_filt:
            # numero_documento es IntegerField; usamos contains via cast a str
            try:
                num = int(boleta_filt)
                dtes_qs = dtes_qs.filter(numero_documento=num)
            except ValueError:
                # parcial: convertimos a string en Python
                dtes_qs = dtes_qs.extra(
                    where=["CAST(numero_documento AS TEXT) LIKE %s"],
                    params=[f"%{boleta_filt}%"],
                )
        if alias_filt:
            dtes_qs = dtes_qs.filter(sucursal__alias__iexact=alias_filt)

        # === 2. Líneas de los DTEs filtrados ===
        lineas_qs = (
            Dte_Productos.objects
            .filter(dte__in=dtes_qs, activo=True)
            .select_related(
                'dte', 'dte__sucursal',
                'productoTalla', 'productoTalla__producto',
                'productoTalla__producto__atributo1',
            )
        )

        if sku_filt:
            lineas_qs = lineas_qs.filter(productoTalla__sku__icontains=sku_filt)
        if marca_filt:
            lineas_qs = lineas_qs.filter(
                productoTalla__producto__atributo1__valor__iexact=marca_filt,
            )

        # === 3. Mapa Dte → ticket.correlativo (voucher) ===
        # Ticket no tiene FK directa a Dte: ambos comparten (sucursal,
        # correlativo) cuando dte_generado=True (folio_dte = numero_documento).
        # Construimos un mapa por (sucursal_id, numero_documento) → ticket.correlativo.
        dte_ids = list(dtes_qs.values_list('id', flat=True))
        voucher_map: dict = {}
        if dte_ids:
            dtes_lite = list(
                Dte.objects.filter(id__in=dte_ids).values(
                    'id', 'sucursal_id', 'numero_documento',
                )
            )
            # Buscar tickets cuyo folio_dte coincida con numero_documento
            folios = [d['numero_documento'] for d in dtes_lite if d['numero_documento']]
            tickets = Ticket.objects.filter(
                folio_dte__in=folios, dte_generado=True,
            ).values('sucursal_id', 'folio_dte', 'correlativo')
            t_map = {(t['sucursal_id'], t['folio_dte']): t['correlativo'] for t in tickets}
            for d in dtes_lite:
                key = (d['sucursal_id'], d['numero_documento'])
                if key in t_map:
                    voucher_map[d['id']] = t_map[key]

        # === 4. Construir respuesta ===
        movimientos: list = []
        resumen_marca: dict = {}
        resumen_bodega: dict = {}
        total_unidades = 0

        for ln in lineas_qs.iterator(chunk_size=500):
            if len(movimientos) >= limit:
                break

            dte = ln.dte
            pt = ln.productoTalla
            prod = pt.producto if pt else None
            suc = dte.sucursal

            sku = pt.sku if pt else ''
            descripcion = ln.descripcion or (prod.descripcion if prod else '')
            marca = ''
            if prod and prod.atributo1 and getattr(prod.atributo1, 'valor', None):
                marca = prod.atributo1.valor
            alias = suc.alias if suc else ''
            sucursal_nombre = suc.direccion if suc else ''

            es_ingreso = dte.tipo_documento in self._TIPOS_INGRESO
            tipo_movimiento_calc = 'Ingreso' if es_ingreso else 'Egreso'
            if tipo_mov and tipo_movimiento_calc != tipo_mov:
                continue

            cantidad = int(ln.stock or 0)
            voucher = voucher_map.get(dte.id)

            mov = {
                'codigo_asociado': sku,
                'articulo': descripcion,
                'marca': marca,
                'alias': alias,
                'sucursal': sucursal_nombre,
                'N_documento': dte.numero_documento,
                'voucher': voucher,
                'n_pedido': voucher,  # alias usado por el frontend
                'tt': cantidad,
                'cantidad': cantidad,
                'precio': int(ln.precio_unitario or ln.precio or 0),
                'costo': int(ln.costo or 0),
                'fecha': dte.fecha_emision.isoformat() if dte.fecha_emision else '',
                'hora': dte.hora.isoformat() if dte.hora else '',
                'tipo_movimiento': tipo_movimiento_calc,
                'tipo_documento': dte.tipo_documento,
                'concepto': 'Nota Crédito' if es_ingreso else 'Venta',
            }
            movimientos.append(mov)

            total_unidades += cantidad
            if marca:
                resumen_marca[marca] = resumen_marca.get(marca, 0) + cantidad
            if alias:
                resumen_bodega[alias] = resumen_bodega.get(alias, 0) + cantidad

        logger.info(
            f"[external/movimientos-ventas] rut={rut} {fecha_desde}→{fecha_hasta} "
            f"→ {len(movimientos)} movimientos, {total_unidades} unidades"
        )

        return Response({
            'success': True,
            'fecha_desde': fecha_desde,
            'fecha_hasta': fecha_hasta,
            'filtros': {
                'sku': sku_filt or None,
                'boleta': boleta_filt or None,
                'marca': marca_filt or None,
                'alias': alias_filt or None,
                'tipo_documento': tipo_doc or None,
                'tipo_movimiento': tipo_mov or None,
            },
            'total_movimientos': len(movimientos),
            'total_unidades': total_unidades,
            'resumen_por_marca': resumen_marca,
            'resumen_por_bodega': resumen_bodega,
            'movimientos': movimientos,
        })


# ──────────────────────────────────────────────
# Endpoint 11 — Ventas / documentos emitidos por día (conciliación AllConnected)
# GET /api/ventas/?rut_empresa=XXXXXXXX-X&fecha=YYYY-MM-DD
# ──────────────────────────────────────────────

class VentasView(APIView):
    """
    Documentos de venta emitidos (a nivel documento, no línea) para que
    AllConnected concilie diariamente sus pedidos impresos contra las
    boletas realmente emitidas, distinguiendo venta internet vs presencial:

      origen = "ecommerce"  → venta por INTERNET, por cualquiera de 3 señales
                              (misma definición que el Reporte de Ventas
                              Internet en views_modulo_reportes):
                                1. el DTE nació de un PedidoEcommerce
                                   (módulo /app/ecommerce/pedidos/), o
                                2. el Ticket tiene modulo_origen='ECOMMERCE', o
                                3. el documento tiene un pago "Venta por
                                   Internet" (boletas emitidas a mano por el
                                   POS dashboard con vendedor 1000, y TODA la
                                   data histórica importada de Laravel, que no
                                   tiene PedidoEcommerce ni Ticket ECOMMERCE).
      origen = "pos"        → venta presencial en tienda.

    El campo `via_emision` ('ecommerce' | 'pos') conserva la señal anterior
    (por qué módulo se facturó), y `plataforma_pago` trae la plataforma del
    pago internet ('Paris', 'Falabella', …) cuando existe.

    Query params:
      rut_empresa    (obligatorio)
      fecha          YYYY-MM-DD — boletas emitidas ese día
      fecha_desde /
      fecha_hasta    alternativa a `fecha` para rangos (máx 31 días)
                     * uno de los dos modos de fecha es obligatorio,
                       salvo que se entregue `referencia`
      origen         (opcional) 'ecommerce' | 'pos'
      referencia     (opcional) busca por referencia de pedido AllConnected:
                     numero_pedido_canal, folio de despacho (correlativo),
                     numero_ticket_rm o numero_pedido_origen
      tipo_documento (opcional) BOLETA_ELECTRONICA / BOLETA_PAPEL /
                     FACTURA_ELECTRONICA / FACTURA_EXENTA
      page / page_size  paginación (default 100, máx 500)

    Cada entrada de `data`:
      numero_documento, tipo_documento, fecha_emision (ISO con TZ Chile),
      monto_total, origen, via_emision, plataforma_pago, numero_ticket_rm,
      referencia_externa (numero_pedido_canal; en boletas POS/migradas cae al
      voucher del pago internet = N° de pedido del marketplace), folio_despacho
      (correlativo AllConnected impreso, ej. RE30005376), canal_origen (del
      pedido, o derivado de la plataforma del pago), anulada, nota_credito,
      sucursal (alias), sucursal_nombre, usuario_emisor.

    Los documentos ANULADOS se incluyen (anulada=true) — la conciliación
    necesita verlos. Solo se excluyen descartados y RECHAZADOS.
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    # Tipos de documento de venta que se listan (las NC no son filas:
    # aparecen en el campo `nota_credito` del documento afectado).
    _TIPOS_VENTA = [
        'BOLETA ELECTRONICA',
        'BOLETA PAPEL',
        'FACTURA ELECTRONICA',
        'FACTURA EXENTA',
    ]

    PAGE_SIZE_DEFAULT = 100
    PAGE_SIZE_MAX = 500

    @staticmethod
    def _norm_tipo(tipo: str) -> str:
        return (tipo or '').replace(' ', '_')

    def get(self, request):
        from datetime import datetime, timedelta, time as dt_time

        from django.db.models import Q

        from app.models import PedidoEcommerce

        rut         = request.query_params.get('rut_empresa', '').strip()
        fecha       = request.query_params.get('fecha', '').strip()
        fecha_desde = request.query_params.get('fecha_desde', '').strip()
        fecha_hasta = request.query_params.get('fecha_hasta', '').strip()
        origen_filt = request.query_params.get('origen', '').strip().lower()
        referencia  = request.query_params.get('referencia', '').strip()
        tipo_filt   = request.query_params.get('tipo_documento', '').strip().upper()

        def _error(msg):
            return Response(
                {'status': False, 'success': False, 'data': [], 'total': 0, 'error': msg},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not rut:
            return _error('El parámetro rut_empresa es obligatorio.')

        if origen_filt and origen_filt not in ('ecommerce', 'pos'):
            return _error("origen debe ser 'ecommerce' o 'pos'.")

        if tipo_filt and self._norm_tipo(tipo_filt) not in [
            self._norm_tipo(t) for t in self._TIPOS_VENTA
        ]:
            return _error(
                'tipo_documento inválido. Opciones: '
                + ', '.join(self._norm_tipo(t) for t in self._TIPOS_VENTA)
            )

        try:
            page = max(1, int(request.query_params.get('page', 1) or 1))
            page_size = int(request.query_params.get('page_size', self.PAGE_SIZE_DEFAULT)
                            or self.PAGE_SIZE_DEFAULT)
        except ValueError:
            return _error('page y page_size deben ser enteros.')
        page_size = min(max(1, page_size), self.PAGE_SIZE_MAX)

        # --- Resolución del rango de fechas ---
        d_from = d_to = None
        if fecha:
            fecha_desde = fecha_hasta = fecha
        elif fecha_desde and not fecha_hasta:
            fecha_hasta = fecha_desde

        if fecha_desde:
            try:
                d_from = datetime.strptime(fecha_desde, '%Y-%m-%d').date()
                d_to = datetime.strptime(fecha_hasta, '%Y-%m-%d').date()
            except ValueError:
                return _error('Formato de fecha inválido. Usa YYYY-MM-DD.')
            if d_to < d_from:
                return _error('fecha_hasta debe ser >= fecha_desde.')
            if (d_to - d_from) > timedelta(days=31):
                return _error('Rango máximo 31 días.')
        elif not referencia:
            return _error('Indica fecha=YYYY-MM-DD o fecha_desde/fecha_hasta (o una referencia).')

        # --- Query base de documentos de venta ---
        dtes_qs = (
            Dte.objects
            .filter(
                emisor__rut=rut,
                tipo_documento__in=self._TIPOS_VENTA,
                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
                descartado=False,
                es_nota_credito=False,
            )
            .exclude(estado_dte='RECHAZADO')
            .select_related('sucursal', 'vendedor')
        )
        if d_from:
            dtes_qs = dtes_qs.filter(fecha_emision__gte=d_from, fecha_emision__lte=d_to)
        if tipo_filt:
            dtes_qs = dtes_qs.filter(
                tipo_documento=self._norm_tipo(tipo_filt).replace('_', ' '),
            )

        if referencia:
            # La referencia identifica un pedido de AllConnected. Resuelve por
            # la vía ecommerce (PedidoEcommerce) o por el voucher del pago
            # "Venta por Internet" (boletas POS/migradas: ahí queda el N° de
            # pedido del marketplace — ver _crear_pago_ecommerce y el input
            # obligatorio de voucher en el POS).
            dte_ids_ref = set(
                PedidoEcommerce.objects
                .filter(dte__isnull=False)
                .filter(
                    Q(numero_pedido_canal__iexact=referencia)
                    | Q(correlativo__iexact=referencia)
                    | Q(numero_ticket_rm__iexact=referencia)
                    | Q(numero_pedido_origen__iexact=referencia)
                )
                .values_list('dte_id', flat=True)
            )
            # Solo el voucher de un pago "Venta por Internet" identifica un
            # pedido del marketplace. NO matchear vouchers de otros métodos:
            # el código de autorización TBK es numérico (se parece a un N° de
            # pedido) y la migración asigna vouchers sintéticos 'MIG-{id}' a
            # TODO pago — sin este guard, ?referencia=123456 podría cruzar una
            # boleta presencial ajena. Mismo criterio que es_metodo_pago_internet.
            dte_ids_ref.update(
                p['dte_id']
                for p in Dte_Detalle_Pago.objects
                    .filter(voucher__iexact=referencia)
                    .values('dte_id', 'metodo_pago')
                if es_metodo_pago_internet(p['metodo_pago'])
            )
            dtes_qs = dtes_qs.filter(id__in=dte_ids_ref)

        dtes = list(dtes_qs.order_by('fecha_emision', 'hora', 'id'))
        dte_ids = [d.id for d in dtes]

        # --- Mapa DTE → pedido ecommerce (origen + referencias AllConnected) ---
        pedido_map = {}
        if dte_ids:
            pedidos = (
                PedidoEcommerce.objects
                .filter(dte_id__in=dte_ids)
                .values(
                    'dte_id', 'numero_ticket_rm', 'numero_pedido_canal',
                    'numero_pedido_origen', 'correlativo', 'canal_origen',
                )
            )
            pedido_map = {p['dte_id']: p for p in pedidos}

        # --- Mapa DTE → ticket (modulo_origen + referencia manual del cajero) ---
        # Ticket no tiene FK a Dte: comparten (sucursal, folio) cuando
        # dte_generado=True (mismo criterio que MovimientosVentasView).
        ticket_map = {}
        if dte_ids:
            folios = [d.numero_documento for d in dtes if d.numero_documento]
            tickets = Ticket.objects.filter(
                folio_dte__in=folios, dte_generado=True,
            ).values('id', 'sucursal_id', 'folio_dte', 'modulo_origen', 'referencia_folio')
            t_map = {(t['sucursal_id'], t['folio_dte']): t for t in tickets}
            for d in dtes:
                t = t_map.get((d.sucursal_id, d.numero_documento))
                if t:
                    ticket_map[d.id] = t

        # --- Mapa DTE → pago "Venta por Internet" (vía POS / data migrada) ---
        # Las boletas internet emitidas a mano por el POS dashboard (vendedor
        # 1000) y toda la data histórica importada de Laravel NO tienen
        # PedidoEcommerce ni Ticket con modulo_origen='ECOMMERCE': su única
        # marca es el método de pago VENTA_INTERNET, con la plataforma en
        # tipo_tarjeta ('Paris', 'Falabella', …) y el N° de pedido del
        # marketplace en voucher. Misma definición de "venta internet" que el
        # reporte de views_modulo_reportes._construir_reporte_ventas_internet.
        pago_net_por_dte = {}
        if dte_ids:
            for p in (
                Dte_Detalle_Pago.objects
                .filter(dte_id__in=dte_ids)
                .values('dte_id', 'metodo_pago', 'tipo_tarjeta', 'voucher')
            ):
                if es_metodo_pago_internet(p['metodo_pago']):
                    pago_net_por_dte.setdefault(p['dte_id'], p)

        # Respaldo por Ticket: cubre DTEs cuyo detalle de pago no se copió o
        # se editó después de emitir (el pago del ticket es la fuente).
        pago_net_por_ticket = {}
        ticket_ids = [t['id'] for t in ticket_map.values()]
        if ticket_ids:
            for p in (
                TicketDetallePago.objects
                .filter(ticket_id__in=ticket_ids, metodo_pago='VENTA_INTERNET')
                .values('ticket_id', 'tipo_tarjeta', 'voucher')
            ):
                pago_net_por_ticket.setdefault(p['ticket_id'], p)

        # --- Mapa DTE → nota de crédito que lo afecta ---
        nc_map = {}
        if dte_ids:
            ncs = (
                Dte.objects
                .filter(documento_afectado_id__in=dte_ids, descartado=False)
                .filter(Q(es_nota_credito=True) | Q(tipo_documento='NOTA DE CREDITO'))
                .exclude(estado_dte='RECHAZADO')
                .order_by('fecha_emision', 'id')
                .values('documento_afectado_id', 'numero_documento')
            )
            for nc in ncs:
                # Si hay más de una NC, se reporta la primera emitida.
                nc_map.setdefault(nc['documento_afectado_id'], nc['numero_documento'])

        # --- Serialización ---
        tz = timezone.get_current_timezone()
        data = []
        for d in dtes:
            pedido = pedido_map.get(d.id)
            ticket = ticket_map.get(d.id)
            pago_net = pago_net_por_dte.get(d.id) or (
                pago_net_por_ticket.get(ticket['id']) if ticket else None
            )

            # Vía de emisión: facturado desde el módulo ecommerce vs a mano.
            via_ecommerce = bool(pedido) or (
                ticket is not None and ticket['modulo_origen'] == 'ECOMMERCE'
            )
            # origen = venta internet por CUALQUIERA de las 3 señales (pedido
            # ecommerce, ticket ECOMMERCE, o pago "Venta por Internet").
            es_ecommerce = via_ecommerce or pago_net is not None
            origen = 'ecommerce' if es_ecommerce else 'pos'
            if origen_filt and origen != origen_filt:
                continue

            nc_folio = nc_map.get(d.id)
            anulada = bool(nc_folio) or d.estado_dte in ('ANULADO', 'CANCELADO')

            fecha_dt = timezone.make_aware(
                datetime.combine(d.fecha_emision, d.hora or dt_time.min), tz,
            )

            referencia_externa = None
            if pedido:
                referencia_externa = pedido['numero_pedido_canal'] or None
            elif ticket and ticket['referencia_folio']:
                # Vía POS: folio tipeado por el cajero al emitir (si existe).
                referencia_externa = ticket['referencia_folio']
            elif pago_net and (pago_net.get('voucher') or '').strip():
                # Boleta POS/migrada: el voucher del pago internet guarda el
                # N° de pedido del marketplace.
                referencia_externa = pago_net['voucher'].strip()

            plataforma_pago = (pago_net.get('tipo_tarjeta') or '').strip() if pago_net else ''
            canal_origen = pedido['canal_origen'] if pedido else None
            if not canal_origen and plataforma_pago:
                canal_origen = canal_desde_plataforma_pago(plataforma_pago)

            data.append({
                'numero_documento': str(d.numero_documento),
                'tipo_documento': self._norm_tipo(d.tipo_documento),
                'fecha_emision': fecha_dt.isoformat(),
                'monto_total': int(d.monto_con_iva or 0),
                'origen': origen,
                'via_emision': 'ecommerce' if via_ecommerce else 'pos',
                'plataforma_pago': plataforma_pago or None,
                'numero_ticket_rm': pedido['numero_ticket_rm'] if pedido else None,
                'referencia_externa': referencia_externa,
                'folio_despacho': (pedido['correlativo'] or None) if pedido else None,
                'canal_origen': canal_origen,
                'anulada': anulada,
                'nota_credito': str(nc_folio) if nc_folio else None,
                'sucursal': d.sucursal.alias if d.sucursal else None,
                'sucursal_nombre': d.sucursal.nombre if d.sucursal else None,
                'usuario_emisor': d.responsable or (
                    d.vendedor.nombre if d.vendedor else None
                ),
            })

        total = len(data)
        inicio = (page - 1) * page_size
        data_page = data[inicio:inicio + page_size]

        logger.info(
            f"[external/ventas] rut={rut} {fecha_desde or 'ref'}→{fecha_hasta or 'ref'} "
            f"origen={origen_filt or '*'} referencia={referencia or '-'} "
            f"→ {total} documentos (page {page})"
        )

        return Response({
            'status': True,
            'success': True,
            'rut_empresa': rut,
            'fecha': fecha or None,
            'fecha_desde': fecha_desde or None,
            'fecha_hasta': fecha_hasta or None,
            'total': total,
            'page': page,
            'page_size': page_size,
            'data': data_page,
            'error': None,
        })
