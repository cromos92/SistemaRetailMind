"""
Vistas de la API externa de RetailMind — contrato v1 con AllConnected.

Endpoints (montados bajo /api/ en urls.py raíz del proyecto):
  GET /api/skus/?rut_empresa=XXXXXXXX-X
  GET /api/articulos/{articulo_codigo}/tallas/?rut_empresa=XXXXXXXX-X
  GET /api/stock/movimientos/?rut_empresa=XXXXXXXX-X[&fecha_desde=YYYY-MM-DD]
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

from app.models import Producto_Talla

from .authentication import ApiKeyAuthentication, ApiKeyPermission
from .serializers import ProductoExternalSerializer, agrupar_por_producto

logger = logging.getLogger(__name__)

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
        rut = request.query_params.get('rut_empresa', '').strip()
        if not rut:
            return Response(
                {'success': False, 'data': [], 'total': 0,
                 'error': 'El parámetro rut_empresa es obligatorio.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(f"[external/skus] rut={rut}")
        rows = list(_build_qs(rut))
        productos = agrupar_por_producto(rows)
        serializer = ProductoExternalSerializer(productos, many=True)
        logger.info(f"[external/skus] rut={rut} → {len(productos)} productos ({len(rows)} filas raw)")
        return Response({
            'success': True,
            'data': serializer.data,
            'total': len(productos),
            'error': None,
        })


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
    'fecha_desde' se acepta por contrato pero se ignora porque Producto_Talla
    no tiene updated_at; siempre devuelve el snapshot completo actual.

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
        if fecha_desde:
            logger.warning(
                f"[external/stock/movimientos] fecha_desde={fecha_desde} recibido pero ignorado "
                f"(Producto_Talla no tiene updated_at). Se devuelve snapshot completo."
            )

        logger.info(f"[external/stock/movimientos] rut={rut}")
        rows = list(
            Producto_Talla.objects
            .filter(producto__sucursal__empresa__rut=rut)
            .values('sku', 'stock', 'producto__sucursal__alias')
        )

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
# Endpoint 4 — Health check (sin auth)
# GET /api/health/
# ──────────────────────────────────────────────

class HealthCheckExternalView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok'})
