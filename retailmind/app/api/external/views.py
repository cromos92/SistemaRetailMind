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
# Endpoint 3b — Stock filtrado por SKUs (con sucursal_id)
# GET /api/stock/por-skus/?rut_empresa=XX-X&skus=A,B,C
# ──────────────────────────────────────────────

class StockPorSkusView(APIView):
    """
    Retorna stock por SKU × sucursal incluyendo `sucursal_id`. Filtrable por
    una lista de SKUs para evitar overfetch. Usado por AllConnected cuando
    resuelve dinámicamente a qué sucursal enviar un pedido.

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
      "total": 1
    }
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

        skus_raw = request.query_params.get('skus', '').strip()
        skus_list: list[str] = [s.strip() for s in skus_raw.split(',') if s.strip()]

        qs = Producto_Talla.objects.filter(
            producto__sucursal__empresa__rut=rut,
        )
        if skus_list:
            qs = qs.filter(sku__in=skus_list)

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
            f"[external/stock/por-skus] rut={rut} skus={len(skus_list)} → {len(data)} SKUs"
        )
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

class PreciosActualesView(APIView):
    """
    Retorna precios y costos actuales de todos los SKUs de la empresa.
    Formato ligero optimizado para que AllConnected detecte cambios de precio
    sin necesidad de traer el catálogo completo.

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
                "sucursal": "PAO1"
            }
        ],
        "total": 123,
        "timestamp": "2026-04-15T12:00:00Z"
    }
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

        logger.info(f"[external/precios-actuales] rut={rut}")
        rows = list(
            Producto_Talla.objects
            .filter(producto__sucursal__empresa__rut=rut)
            .values(
                'sku',
                'producto__articulo',
                'producto__costo',
                'producto__precioventa',
                'producto__precioSugerido',
                'producto__sucursal__alias',
            )
        )

        data = []
        for row in rows:
            data.append({
                'codigo_sku': str(row['sku']),
                'articulo': row.get('producto__articulo', ''),
                'precio_venta': int(row.get('producto__precioventa', 0) or 0),
                'precio_costo': int(row.get('producto__costo', 0) or 0),
                'precio_sugerido': int(row.get('producto__precioSugerido', 0) or 0),
                'sucursal': row.get('producto__sucursal__alias', '') or '',
            })

        logger.info(f"[external/precios-actuales] rut={rut} → {len(data)} SKUs")
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
    Retorna productos/SKUs creados o modificados recientemente.
    Usado por AllConnected para detectar nueva mercadería sin un full sync.

    Si `desde` se omite, devuelve los últimos 7 días.
    Filtra por Producto.fecha_creacion cuando existe, o hace fallback
    a los productos actuales.
    """
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [ApiKeyPermission]

    def get(self, request):
        from datetime import timedelta

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

        qs = Producto.objects.filter(sucursal__empresa__rut=rut)
        if hasattr(Producto, 'fecha_creacion'):
            qs = qs.filter(fecha_creacion__date__gte=desde)

        rows = list(
            Producto_Talla.objects
            .filter(producto__in=qs)
            .values(
                'sku', 'talla', 'stock',
                'producto__articulo',
                'producto__descripcion',
                'producto__atributo1__valor',
                'producto__atributo2__valor',
                'producto__costo',
                'producto__precioventa',
                'producto__sucursal__alias',
            )
        )

        productos = agrupar_por_producto(rows)
        serializer = ProductoExternalSerializer(productos, many=True)

        logger.info(f"[external/novedades] rut={rut} desde={desde} → {len(productos)} productos")
        return Response({
            'success': True,
            'data': serializer.data,
            'total': len(productos),
            'desde': str(desde),
            'timestamp': timezone.now().isoformat(),
            'error': None,
        })
