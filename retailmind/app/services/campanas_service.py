"""
Servicio de campañas de liquidación — aplicación y reversión de precios.

Aislado de las vistas para poder reusarse desde el scheduler (cierre
automático de campañas vencidas) y desde el POS (validación NxM). Todo lo
que escribe precios es atómico y deja auditoría en HistorialCambioPrecio.

Reglas:
  - PORCENTAJE / PRECIO_FIJO: al activar bajan Producto.precioventa (+lotes
    activos) con piso opcional en costo×1.1; al cerrar se restaura el precio
    original SOLO si nadie lo cambió a mano en el intertanto (si difiere, el
    item queda en CONFLICTO y NO se pisa el precio manual).
  - NXM: no toca precios; el beneficio se materializa como línea gratis en
    el POS (ver views_modulo_campanas_liquidacion.obtener_promos_activas y el
    validador de payload de abajo).
"""
import logging
from math import floor

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.models import (
    CampanaLiquidacion, CampanaLiquidacionProducto, HistorialCambioPrecio,
    LoteProducto,
)

logger = logging.getLogger('app')

PISO_COSTO_FACTOR = 1.1  # mismo criterio que modificacion_masiva


def _precio_liquidacion(campana, producto):
    """Precio final de un item %/fijo, respetando el piso de costo si aplica.

    Devuelve (precio_final, clamp_aplicado). None para NXM (no cambia precio).
    """
    base = producto.precioventa or 0
    if campana.tipo_regla == 'PORCENTAJE':
        pct = float(campana.valor_porcentaje or 0)
        nuevo = int(round(base * (1 - pct / 100.0)))
    elif campana.tipo_regla == 'PRECIO_FIJO':
        nuevo = int(campana.valor_precio_fijo or 0)
    else:  # NXM
        return None, False
    clamp = False
    if campana.respetar_piso_costo:
        piso = int((producto.costo or 0) * PISO_COSTO_FACTOR)
        if nuevo < piso:
            nuevo, clamp = piso, True
    return max(nuevo, 0), clamp


def _registrar_historial(producto, precio_ant, precio_nuevo, motivo, tipo,
                         usuario=None, ip=None, lotes=0, tallas=0):
    diferencia = precio_nuevo - precio_ant
    pct = (diferencia / precio_ant * 100) if precio_ant > 0 else 0
    HistorialCambioPrecio.objects.create(
        producto=producto, precio_anterior=precio_ant, precio_nuevo=precio_nuevo,
        diferencia=diferencia, porcentaje_cambio=pct, motivo=motivo,
        tipo_cambio=tipo, usuario=usuario, ip_address=ip,
        tallas_afectadas=tallas, lotes_afectados=lotes,
    )


@transaction.atomic
def aplicar_precios_campana(campana, usuario=None, ip=None):
    """Activa la campaña: aplica la regla de precio y marca items activos.

    Devuelve dict con contadores. Idempotente-seguro: reevalúa contra el
    precio actual de cada producto (re-snapshot), no contra un valor viejo.
    """
    if campana.estado not in ('BORRADOR', 'ACTIVA'):
        raise ValueError(f'La campaña #{campana.pk} no se puede activar (estado {campana.estado}).')

    aplicados = nxm = clamps = 0
    items = (campana.items.select_related('producto', 'producto__sucursal')
             .exclude(estado='EXCLUIDO'))
    for item in items:
        producto = item.producto
        precio_ant = producto.precioventa or 0
        item.precio_original = precio_ant

        if campana.tipo_regla == 'NXM':
            item.precio_liquidacion = None
            item.estado = 'APLICADO'
            item.activo = True
            item.fecha_aplicacion = timezone.now()
            item.save(update_fields=['precio_original', 'precio_liquidacion',
                                     'estado', 'activo', 'fecha_aplicacion'])
            nxm += 1
            continue

        nuevo, clamp = _precio_liquidacion(campana, producto)
        if clamp:
            clamps += 1

        if nuevo != precio_ant:
            producto.precioventa = nuevo
            producto.save(update_fields=['precioventa'])
            lotes = LoteProducto.objects.filter(
                producto_talla__producto=producto,
                cantidad_disponible__gt=0, activo=True,
            ).update(precio_venta_unitario=nuevo)
            _registrar_historial(
                producto, precio_ant, nuevo,
                motivo=f'Campaña #{campana.pk} {campana.nombre}',
                tipo='CAMPANA_LIQUIDACION', usuario=usuario, ip=ip,
                lotes=lotes, tallas=producto.producto_talla.count(),
            )

        item.precio_liquidacion = nuevo
        item.estado = 'APLICADO'
        item.activo = True
        item.fecha_aplicacion = timezone.now()
        item.save(update_fields=['precio_original', 'precio_liquidacion',
                                 'estado', 'activo', 'fecha_aplicacion'])
        aplicados += 1

    campana.estado = 'ACTIVA'
    campana.fecha_activacion = timezone.now()
    campana.save(update_fields=['estado', 'fecha_activacion'])
    logger.info('Campaña #%s activada: %s precios aplicados, %s NxM, %s en piso de costo',
                campana.pk, aplicados, nxm, clamps)
    return {'aplicados': aplicados, 'nxm': nxm, 'clamps': clamps}


@transaction.atomic
def revertir_precios_campana(campana, usuario=None, ip=None,
                             motivo='Cierre de campaña', nuevo_estado='FINALIZADA'):
    """Cierra la campaña restaurando precios originales.

    Si el precio actual difiere del precio de liquidación (alguien lo cambió
    a mano durante la campaña) NO se pisa: el item queda en CONFLICTO. NxM se
    revierte sin tocar precio.
    """
    revertidos = conflictos = nxm = 0
    items = campana.items.select_related('producto').filter(estado='APLICADO')
    for item in items:
        producto = item.producto

        if campana.tipo_regla == 'NXM':
            item.estado = 'REVERTIDO'
            item.activo = False
            item.fecha_reversion = timezone.now()
            item.save(update_fields=['estado', 'activo', 'fecha_reversion'])
            nxm += 1
            continue

        precio_actual = producto.precioventa or 0
        if item.precio_liquidacion is not None and precio_actual != item.precio_liquidacion:
            # Cambio manual durante la campaña: respetar el precio actual.
            item.estado = 'CONFLICTO'
            item.activo = False
            item.observaciones = (
                f'No revertido: precio actual ${precio_actual} ≠ precio de '
                f'campaña ${item.precio_liquidacion}. Se respetó el cambio manual.')
            item.save(update_fields=['estado', 'activo', 'observaciones'])
            conflictos += 1
            logger.warning('Campaña #%s item producto=%s en CONFLICTO (precio manual %s)',
                           campana.pk, producto.pk, precio_actual)
            continue

        if precio_actual != item.precio_original:
            producto.precioventa = item.precio_original
            producto.save(update_fields=['precioventa'])
            lotes = LoteProducto.objects.filter(
                producto_talla__producto=producto,
                cantidad_disponible__gt=0, activo=True,
            ).update(precio_venta_unitario=item.precio_original)
            _registrar_historial(
                producto, precio_actual, item.precio_original,
                motivo=f'{motivo} — campaña #{campana.pk}',
                tipo='REVERSION_CAMPANA', usuario=usuario, ip=ip,
                lotes=lotes, tallas=producto.producto_talla.count(),
            )
        item.estado = 'REVERTIDO'
        item.activo = False
        item.fecha_reversion = timezone.now()
        item.save(update_fields=['estado', 'activo', 'fecha_reversion'])
        revertidos += 1

    campana.estado = nuevo_estado
    campana.cerrado_por = usuario
    campana.fecha_cierre = timezone.now()
    campana.save(update_fields=['estado', 'cerrado_por', 'fecha_cierre'])
    logger.info('Campaña #%s cerrada (%s): %s revertidos, %s conflictos, %s NxM',
                campana.pk, nuevo_estado, revertidos, conflictos, nxm)
    return {'revertidos': revertidos, 'conflictos': conflictos, 'nxm': nxm}


def cerrar_campanas_vencidas():
    """Cierra (revierte) campañas ACTIVAS cuya fecha_fin ya pasó. Idempotente
    (la reversión solo actúa sobre items APLICADO). Para el scheduler."""
    ahora = timezone.now()
    vencidas = CampanaLiquidacion.objects.filter(
        estado='ACTIVA', fecha_fin__isnull=False, fecha_fin__lte=ahora,
    )
    cerradas = 0
    for campana in vencidas:
        try:
            revertir_precios_campana(campana, usuario=None,
                                     motivo='Cierre automático por vencimiento')
            cerradas += 1
        except Exception:
            logger.exception('Error cerrando campaña vencida #%s', campana.pk)
    return cerradas


# ------------------------------------------------------- motor NxM (POS)
def validar_promos_nxm_payload(productos_payload, sucursal, ahora=None):
    """Valida las líneas promo NxM de un ticket contra las campañas en BD.

    productos_payload: lista de dicts con al menos producto_talla_id/sku,
      cantidad y (para líneas promo) promo_campana_id + es_promo_nxm/gratis.
    Devuelve {'ok': bool, 'errores': [...], 'lineas_ok': {idx: campana_id}}.

    Es una función PURA de (payload, estado BD): no escribe nada, así puede
    reejecutarse en cada sync sin efectos dobles (registrar_pagos_ticket no
    es atómico). Verifica: campaña ACTIVA + vigente + sucursal en su M2M +
    producto en items activos + que las unidades gratis no excedan
    floor(q_total/n)*(n-m) por producto.
    """
    ahora = ahora or timezone.now()
    errores = []
    lineas_ok = {}

    # Resolver producto_id por línea (desde sku o producto_talla) y agrupar.
    from app.models import Producto_Talla
    ids_pt = [p.get('producto_talla_id') for p in productos_payload if p.get('producto_talla_id')]
    skus = [p.get('sku') for p in productos_payload if p.get('sku') and not p.get('producto_talla_id')]
    pt_map = {}
    if ids_pt:
        pt_map.update({pt.id: pt.producto_id for pt in
                       Producto_Talla.objects.filter(id__in=ids_pt).only('id', 'producto_id')})
    sku_map = {}
    if skus:
        sku_map.update({pt.sku: pt.producto_id for pt in
                        Producto_Talla.objects.filter(sku__in=skus,
                                                      producto__sucursal=sucursal).only('sku', 'producto_id')})

    def prod_de(linea):
        if linea.get('producto_talla_id'):
            return pt_map.get(linea['producto_talla_id'])
        if linea.get('sku'):
            return sku_map.get(linea['sku'])
        return None

    # Cantidades totales y gratis por producto.
    total_por_prod, gratis_por_prod, campana_por_prod = {}, {}, {}
    for idx, linea in enumerate(productos_payload):
        pid = prod_de(linea)
        if pid is None:
            continue
        cant = int(linea.get('cantidad') or 0)
        es_gratis = bool(linea.get('es_promo_nxm') or linea.get('gratis'))
        total_por_prod[pid] = total_por_prod.get(pid, 0) + cant
        if es_gratis:
            gratis_por_prod[pid] = gratis_por_prod.get(pid, 0) + cant
            campana_por_prod[pid] = linea.get('promo_campana_id')
            lineas_ok[idx] = linea.get('promo_campana_id')

    if not gratis_por_prod:
        return {'ok': True, 'errores': [], 'lineas_ok': {}}

    # Campañas NxM vigentes en la sucursal, indexadas por producto.
    items = CampanaLiquidacionProducto.objects.filter(
        activo=True, producto_id__in=list(gratis_por_prod.keys()),
        campana__estado='ACTIVA', campana__tipo_regla='NXM',
        campana__fecha_inicio__lte=ahora, campana__sucursales=sucursal,
    ).filter(
        Q(campana__fecha_fin__isnull=True) | Q(campana__fecha_fin__gte=ahora)
    ).select_related('campana')
    promo_por_prod = {it.producto_id: it.campana for it in items}

    for pid, gratis in gratis_por_prod.items():
        campana = promo_por_prod.get(pid)
        if campana is None:
            errores.append({'producto_id': pid,
                            'error': 'Sin campaña NxM vigente para este producto/sucursal.'})
            continue
        n, m = int(campana.nxm_n or 0), int(campana.nxm_m or 0)
        if n <= m or m < 1:
            errores.append({'producto_id': pid, 'error': f'Regla NxM inválida ({n}x{m}).'})
            continue
        q = total_por_prod.get(pid, 0)
        max_gratis = floor(q / n) * (n - m)
        if gratis > max_gratis:
            errores.append({'producto_id': pid,
                            'error': f'Unidades gratis ({gratis}) exceden el máximo {max_gratis} para {n}x{m} con {q} unidades.'})

    return {'ok': not errores, 'errores': errores, 'lineas_ok': lineas_ok}
