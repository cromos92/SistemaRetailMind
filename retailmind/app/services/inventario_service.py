"""
Servicio único de inventario.

Objetivo: que ningún flujo mueva stock sin mover, en la misma transacción,
los tres registros que deben permanecer sincronizados:
  1. Producto_Talla.stock          (stock plano — fuente de verdad operativa)
  2. LoteProducto                  (capa FIFO de costeo)
  3. Movimientos_Producto          (kardex)

Estado de adopción:
- `consumir_lotes_fifo` lo usa `emitir_dte` (ventas externas y traspasos por
  DTE) para cerrar el drift stock↔lotes que generaba ese flujo.
- `ingresar` / `egresar` son las primitivas a las que deben migrar el resto
  de los llamadores (traspasos vía modelo Traspaso, reingresos de cambios y
  NC, fallbacks de venta, despacho diferido de cotizaciones). No usarlas a
  medias: o el flujo completo pasa por aquí, o no se mezcla.
"""
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from app.models import (
    CONCEPTO_MOVIMIENTO_CHOICES,
    LoteProducto,
    Movimientos_Producto,
    Producto_Talla,
)

logger = logging.getLogger('app')

CONCEPTOS_VALIDOS = {choice[0] for choice in CONCEPTO_MOVIMIENTO_CHOICES}


def _sucursal_duena(producto_talla):
    """Sucursal dueña del SKU (producto.sucursal). Es el valor por defecto de
    sucursal_origen (egresos) y sucursal_destino (ingresos): los reportes de
    existencias cortan por esas columnas y un kardex con la sucursal en NULL
    desaparece de 'Ventas' / 'Traspasos salida' (auditoría 29-09, N4)."""
    producto = getattr(producto_talla, 'producto', None)
    return getattr(producto, 'sucursal', None) if producto else None


def _nombre_responsable(responsable):
    """`Usuario.__str__` devuelve 'Nombre (username)' y el CharField del kardex
    es de 50: si llega un usuario se guarda su username, si no el texto."""
    if not responsable:
        return 'Sistema'
    username = getattr(responsable, 'username', None)
    return str(username) if username else str(responsable)


def consumir_lotes_fifo(producto_talla, cantidad, usar_lock=True, detalle=None):
    """Consume hasta `cantidad` unidades de los lotes FIFO disponibles.

    No toca stock plano ni kardex (eso es responsabilidad del llamador o de
    `egresar`). Devuelve (consumido, faltante): si la capa de lotes está
    incompleta, consume lo que haya y reporta el resto — el llamador decide
    si eso es un error o queda para reconciliación.

    `usar_lock=True` requiere transacción abierta (select_for_update).
    `detalle`: lista opcional a la que se agrega un dict por lote consumido
    (mismo formato que `views.consumir_stock_fifo`, para `persistir_costeo_fifo`).
    """
    if cantidad <= 0:
        return 0, 0

    lotes = LoteProducto.objects.filter(
        producto_talla=producto_talla,
        activo=True,
        agotado=False,
        cantidad_disponible__gt=0,
    ).order_by('fecha_ingreso', 'id')
    if usar_lock:
        lotes = lotes.select_for_update()

    pendiente = cantidad
    for lote in lotes:
        if pendiente <= 0:
            break
        consumo = min(pendiente, lote.cantidad_disponible)
        lote.cantidad_disponible -= consumo
        if lote.cantidad_disponible <= 0:
            lote.agotado = True
        lote.save(update_fields=['cantidad_disponible', 'agotado', 'updated_at'])
        pendiente -= consumo
        if detalle is not None:
            detalle.append({
                'lote_id': lote.id,
                'cantidad_consumida': consumo,
                'costo_unitario': lote.costo_unitario,
                'costo_total': consumo * lote.costo_unitario,
                'fecha_ingreso_lote': lote.fecha_ingreso,
                'dte_origen': lote.dte.numero_documento if lote.dte_id else None,
            })

    consumido = cantidad - pendiente
    if pendiente > 0:
        logger.warning(
            "inventario_service: lotes FIFO insuficientes sku=%s pedido=%s consumido=%s",
            producto_talla.sku, cantidad, consumido,
        )
    return consumido, pendiente


def crear_lote(producto_talla, cantidad, costo_unitario=0, sobreprecio_unitario=0,
               precio_venta_unitario=0, dte=None, movimiento=None,
               fecha_ingreso=None, observaciones=None,
               numero_lote=None, fecha_vencimiento=None):
    """Crea un lote FIFO. `fecha_ingreso` explícita cuando el ingreso es
    retroactivo (reingresos de NC/cambios deben conservar la antigüedad real,
    no la fecha del proceso — lección de la migración)."""
    campos = dict(
        producto_talla=producto_talla,
        dte=dte,
        movimiento=movimiento,
        cantidad_inicial=cantidad,
        cantidad_disponible=cantidad,
        costo_unitario=costo_unitario or 0,
        sobreprecio_unitario=sobreprecio_unitario or 0,
        precio_venta_unitario=precio_venta_unitario or 0,
        observaciones=observaciones or '',
    )
    if fecha_ingreso:
        campos['fecha_ingreso'] = fecha_ingreso
    if numero_lote:
        campos['numero_lote'] = numero_lote
    if fecha_vencimiento:
        campos['fecha_vencimiento'] = fecha_vencimiento
    return LoteProducto.objects.create(**campos)


# Alias para poder llamar la función desde `ingresar`, cuyo kwarg `crear_lote`
# (flag) le hace sombra dentro del cuerpo.
_nuevo_lote = crear_lote


def _validar(concepto, cantidad):
    if concepto not in CONCEPTOS_VALIDOS:
        raise ValueError(f'Concepto de movimiento no declarado: {concepto!r}')
    if cantidad <= 0:
        raise ValueError('La cantidad debe ser positiva (el signo lo pone la operación)')


def ingresar(producto_talla, cantidad, concepto, responsable,
             sucursal_destino=None, sucursal_origen=None, dte=None, ticket=None,
             costo_unitario=0, sobreprecio_unitario=0, precio_unitario=0,
             observaciones=None, referencia_externa=None, fecha_ingreso_lote=None,
             crear_lote=True, numero_lote=None, fecha_vencimiento=None):
    """Ingreso de inventario: stock plano (F()) + lote FIFO + kardex, atómico.

    Valores por defecto (auditoría 29-09, N4): si no se pasa `sucursal_destino`
    se usa la sucursal dueña del SKU; si costo/sobreprecio/precio vienen en
    0/None se toman de la ficha del producto, para que ni el kardex ni el lote
    FIFO nazcan con costo 0 (margen inflado al vender).

    `crear_lote=False` es para llamadores que ya crearon el lote a mano y solo
    lo enlazan al movimiento devuelto (si no, quedarían dos lotes).
    """
    _validar(concepto, cantidad)
    with transaction.atomic():
        pt = (
            Producto_Talla.objects.select_for_update()
            .select_related('producto__sucursal')
            .get(id=producto_talla.id)
        )
        producto = pt.producto
        sucursal_destino = sucursal_destino or _sucursal_duena(pt)
        costo_unitario = costo_unitario or getattr(producto, 'costo', 0) or 0
        sobreprecio_unitario = sobreprecio_unitario or getattr(producto, 'sobreprecio', 0) or 0
        precio_unitario = precio_unitario or getattr(producto, 'precioventa', 0) or 0
        Producto_Talla.objects.filter(id=pt.id).update(stock=F('stock') + cantidad)
        movimiento = Movimientos_Producto.objects.create(
            ProductoTalla=pt,
            dte=dte,
            ticket=ticket,
            sucursal_origen=sucursal_origen,
            sucursal_destino=sucursal_destino,
            cantidad=cantidad,
            costo=int(costo_unitario or 0),
            sobreprecio=int(sobreprecio_unitario or 0),
            precio=int(precio_unitario or 0),
            concepto=concepto,
            estado='COMPLETADO',
            responsable=_nombre_responsable(responsable),
            observaciones=observaciones or '',
            referencia_externa=referencia_externa or '',
        )
        if crear_lote:
            _nuevo_lote(
                pt, cantidad,
                costo_unitario=costo_unitario,
                sobreprecio_unitario=sobreprecio_unitario,
                precio_venta_unitario=precio_unitario,
                dte=dte, movimiento=movimiento,
                fecha_ingreso=fecha_ingreso_lote,
                observaciones=observaciones,
                numero_lote=numero_lote,
                fecha_vencimiento=fecha_vencimiento,
            )
    producto_talla.refresh_from_db(fields=['stock'])
    return movimiento


def egresar(producto_talla, cantidad, concepto, responsable,
            sucursal_origen=None, sucursal_destino=None, dte=None, ticket=None,
            precio_unitario=0, observaciones=None, referencia_externa=None,
            permitir_stock_insuficiente=False, consumir_lotes=True,
            devolver_lotes=False):
    """Egreso de inventario: valida stock, consume lotes FIFO, baja stock
    plano (F()) y escribe kardex — todo atómico y con lock de fila.

    `permitir_stock_insuficiente=True` es para flujos donde la venta ya
    ocurrió en el mundo físico (p.ej. sync offline, pedido ecommerce ya
    pagado) y el registro no puede rechazarse; deja stock negativo VISIBLE
    (nunca se recorta a 0: el kardex debe reflejar exactamente lo que bajó)
    y lo advierte en el log.

    `sucursal_origen` por defecto es la sucursal dueña del SKU (N4).
    `consumir_lotes=False`: el llamador ya bajó los lotes (no consumir dos veces).
    `devolver_lotes=True`: devuelve `(movimiento, lotes_utilizados)` con el
    detalle por lote para `persistir_costeo_fifo`; la firma por defecto sigue
    devolviendo solo el movimiento.
    """
    _validar(concepto, cantidad)
    lotes_utilizados = []
    with transaction.atomic():
        pt = (
            Producto_Talla.objects.select_for_update()
            .select_related('producto__sucursal')
            .get(id=producto_talla.id)
        )
        producto = pt.producto
        sucursal_origen = sucursal_origen or _sucursal_duena(pt)
        if pt.stock < cantidad and not permitir_stock_insuficiente:
            raise ValueError(
                f'Stock insuficiente para SKU {pt.sku}: disponible {pt.stock}, '
                f'solicitado {cantidad}'
            )
        if consumir_lotes:
            consumir_lotes_fifo(pt, cantidad, usar_lock=True, detalle=lotes_utilizados)
        Producto_Talla.objects.filter(id=pt.id).update(stock=F('stock') - cantidad)
        movimiento = Movimientos_Producto.objects.create(
            ProductoTalla=pt,
            dte=dte,
            ticket=ticket,
            sucursal_origen=sucursal_origen,
            sucursal_destino=sucursal_destino,
            cantidad=-cantidad,
            costo=int(getattr(producto, 'costo', 0) or 0),
            sobreprecio=int(getattr(producto, 'sobreprecio', 0) or 0),
            precio=int(precio_unitario or getattr(producto, 'precioventa', 0) or 0),
            concepto=concepto,
            estado='COMPLETADO',
            responsable=_nombre_responsable(responsable),
            observaciones=observaciones or '',
            referencia_externa=referencia_externa or '',
        )
        if pt.stock < cantidad:
            logger.warning(
                "inventario_service: egreso con stock insuficiente sku=%s "
                "stock=%s cantidad=%s (permitido por el flujo)",
                pt.sku, pt.stock, cantidad,
            )
    producto_talla.refresh_from_db(fields=['stock'])
    if devolver_lotes:
        return movimiento, lotes_utilizados
    return movimiento
