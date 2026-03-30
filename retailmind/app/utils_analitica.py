"""
utils_analitica.py
==================
Helpers para excluir productos irrelevantes de dashboards, KPIs y predicciones.

Estrategia de doble capa:
  1. Filtro automático: solo SKUs con stock > 0 O con movimiento en los últimos 2 años.
     Elimina el "cementerio digital" (~150k SKUs históricos de Laravel sin actividad).
  2. Flag manual: Producto.excluir_de_analitica = True para casos de negocio específicos
     (exhibición, consignación, catálogo retirado).
"""

from django.db.models import Q


# Año mínimo de movimiento para considerar un SKU "activo"
ANIO_ACTIVIDAD_MINIMO = 2024


def productos_activos_qs(sucursal_id=None):
    """
    Retorna un queryset de Producto_Talla que incluye solo SKUs relevantes para analitica:
      - tienen stock > 0, O
      - tuvieron algún movimiento COMPLETADO desde ANIO_ACTIVIDAD_MINIMO

    Excluye además los que tengan Producto.excluir_de_analitica = True.

    Args:
        sucursal_id: si se indica, filtra adicionalmente por Producto.sucursal_id
    """
    from app.models import Producto_Talla, Movimientos_Producto

    ids_con_actividad = (
        Movimientos_Producto.objects
        .filter(fecha__year__gte=ANIO_ACTIVIDAD_MINIMO, estado='COMPLETADO')
        .values_list('ProductoTalla_id', flat=True)
        .distinct()
    )

    qs = Producto_Talla.objects.filter(
        Q(stock__gt=0) | Q(id__in=ids_con_actividad),
        producto__excluir_de_analitica=False,
    )

    if sucursal_id:
        qs = qs.filter(producto__sucursal_id=sucursal_id)

    return qs


def ids_producto_talla_activos(sucursal_id=None):
    """
    Retorna un queryset de IDs de Producto_Talla activos (para usar en __in=).
    Más eficiente que cargar todos los objetos.
    """
    return productos_activos_qs(sucursal_id=sucursal_id).values_list('id', flat=True)


def filtro_movimientos_activos(sucursal_id=None):
    """
    Retorna un dict Q-compatible para filtrar Movimientos_Producto por SKUs activos.
    Uso:  Movimientos_Producto.objects.filter(**filtro_movimientos_activos(), ...)
    """
    ids = ids_producto_talla_activos(sucursal_id=sucursal_id)
    return {'ProductoTalla_id__in': ids}
