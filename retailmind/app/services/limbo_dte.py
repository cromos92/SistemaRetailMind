"""
Helpers compartidos para el flujo "Limbo Inbox" de DTEs de TRASPASO.

Centraliza la lógica de creación / completado / absorción de los
Movimientos_Producto generados cuando el emisor emite una NC (o AJUSTE
TRASPASO POST) sobre un traspaso que ya pasó por recepción.

Existen 3 modos según la decisión del emisor:

- 'crear_pendiente': La NC se emite con devolución física pendiente.
  Crea movimientos en estado PENDIENTE con concepto
  DEVOLUCION_NC_PENDIENTE_DESPACHO. NO mueve stock todavía. El receptor
  debe luego despachar físicamente y llamar a
  `confirmar_devolucion_fisica_api` para completarlo.

- 'completar_pendiente': El receptor confirmó el despacho físico. Toma
  los movimientos PENDIENTE existentes, los transforma a
  DEVOLUCION_NC_POST_RECEPCION + COMPLETADO y mueve el stock
  efectivamente (egreso destino + ingreso origen).

- 'absorber_sin_retorno': El emisor emitió NC sin devolución (el destino
  se queda con la mercadería). Crea UN solo egreso en destino con
  concepto SOBRANTE_ABSORBIDO_ORIGEN. El stock baja en destino y el
  origen NO recupera nada (asume contablemente la baja).

Los 3 modos son usados desde `ajustar_dte_emisor_api` y
`confirmar_devolucion_fisica_api` en views.py.
"""
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger('app')


def _fecha_hora_despacho(dte_original, sku=None):
    """(fecha, hora) del TRASPASO_SALIDA del SKU en el traspaso original.

    Es la antigüedad real de las unidades que vuelven al origen: el lote que
    las repone debe conservarla (mismo criterio que `_fecha_salida_traspaso`
    en views.py, que no se importa desde acá para no cerrar un ciclo).
    """
    if dte_original is None:
        return None, None
    from app.models import Movimientos_Producto
    qs = Movimientos_Producto.objects.filter(dte=dte_original, concepto='TRASPASO_SALIDA')
    fila = None
    if sku:
        fila = (qs.filter(ProductoTalla__sku=sku)
                .order_by('fecha', 'hora', 'id').values_list('fecha', 'hora').first())
    if fila is None:
        fila = qs.order_by('fecha', 'hora', 'id').values_list('fecha', 'hora').first()
    if fila is None or not fila[0]:
        return getattr(dte_original, 'fecha_emision', None), None
    return fila[0], fila[1]


def _sincronizar_lotes_devolucion(*, talla_destino, talla_origen, cantidad,
                                  dte_original, dte_hijo, movimiento_ingreso=None,
                                  costo=0, sobreprecio=0, precio=0, observaciones=''):
    """Capa FIFO de una devolución post-recepción destino → origen.

    El stock plano y el kardex ya se mueven en este módulo con update(F()) y
    las filas DEVOLUCION_NC_POST_RECEPCION / SOBRANTE_ABSORBIDO_ORIGEN; sin
    esto los lotes no se tocaban y quedaba drift ±N en las dos sucursales
    (auditoría R-01). Se conservan las filas actuales y sólo se agrega la
    capa de lotes: consumir FIFO en el destino y, si hay origen, crear el lote
    de reposición con la antigüedad del despacho original.

    Best-effort en SAVEPOINT (mismo criterio que `_reponer_lote_traspaso`):
    un fallo de lotes nunca tumba la transacción de stock.
    """
    if cantidad <= 0 or talla_destino is None:
        return None
    from datetime import datetime as _dt, time as _time
    from app.models import LoteProducto
    from app.services.inventario_service import consumir_lotes_fifo, crear_lote
    try:
        with transaction.atomic():
            consumir_lotes_fifo(talla_destino, cantidad)
            if talla_origen is None:
                return None
            lote = crear_lote(
                talla_origen, cantidad,
                costo_unitario=int(costo or 0),
                sobreprecio_unitario=int(sobreprecio or 0),
                precio_venta_unitario=int(precio or 0),
                dte=dte_hijo, movimiento=movimiento_ingreso,
                observaciones=observaciones,
            )
            # fecha_ingreso es auto_now_add: se fija con update() después.
            fecha, hora = _fecha_hora_despacho(dte_original, getattr(talla_origen, 'sku', None))
            if fecha:
                fecha_ingreso = _dt.combine(fecha, hora or _time(0, 0))
                if timezone.is_naive(fecha_ingreso):
                    fecha_ingreso = timezone.make_aware(fecha_ingreso)
                LoteProducto.objects.filter(id=lote.id).update(fecha_ingreso=fecha_ingreso)
            return lote
    except Exception:
        logger.warning(
            "limbo_dte: no se pudo sincronizar la capa FIFO (destino=%s origen=%s cantidad=%s)",
            getattr(talla_destino, 'id', None), getattr(talla_origen, 'id', None), cantidad,
            exc_info=True,
        )
        return None


def buscar_talla_en_sucursal(talla_origen, sucursal):
    """Encuentra el Producto_Talla equivalente (mismo SKU) en otra sucursal."""
    if talla_origen is None or sucursal is None:
        return None
    from app.models import Producto_Talla
    return (
        Producto_Talla.objects
        .select_for_update(of=('self',))
        .filter(sku=talla_origen.sku, producto__sucursal_id=sucursal.id)
        .first()
    )


def crear_movimientos_devolucion_pendiente(*, dte, dte_hijo, talla_origen,
                                            talla_destino, cantidad,
                                            sucursal_destino, costo, sobreprecio,
                                            precio, usuario, motivo_corto=''):
    """Modo 'crear_pendiente'.

    Crea DOS movimientos PENDIENTE (egreso en destino + ingreso en origen)
    sin modificar stock. Se asocian al `dte_hijo` (la NC recién creada)
    para que `confirmar_devolucion_fisica_api` los identifique y complete.

    No descuenta ni suma stock — solo crea el rastro pendiente.
    """
    from app.models import Movimientos_Producto

    obs_base = (
        f'NC #{dte_hijo.numero_documento} sobre DTE #{dte.numero_documento}: '
        f'devolución pendiente de despacho físico desde {sucursal_destino.alias}.'
        f' {motivo_corto}'
    )[:500]

    Movimientos_Producto.objects.create(
        dte=dte_hijo,
        ProductoTalla=talla_destino,
        sucursal_origen=sucursal_destino,
        sucursal_destino=None,
        cantidad=-cantidad,
        costo=costo, sobreprecio=sobreprecio, precio=precio,
        concepto='DEVOLUCION_NC_PENDIENTE_DESPACHO',
        tipo_movimiento='EGRESO',
        estado='PENDIENTE',
        responsable=usuario,
        observaciones=obs_base,
    )
    Movimientos_Producto.objects.create(
        dte=dte_hijo,
        ProductoTalla=talla_origen,
        sucursal_origen=None,
        sucursal_destino=dte.sucursal,
        cantidad=cantidad,
        costo=costo, sobreprecio=sobreprecio, precio=precio,
        concepto='DEVOLUCION_NC_PENDIENTE_DESPACHO',
        tipo_movimiento='INGRESO',
        estado='PENDIENTE',
        responsable=usuario,
        observaciones=obs_base,
    )


def aplicar_movimientos_devolucion_completados(*, dte_original, dte_hijo,
                                                talla_origen, talla_destino,
                                                cantidad, sucursal_destino,
                                                costo, sobreprecio, precio,
                                                usuario, observaciones=''):
    """Modo usado por `ajustar_dte_emisor_api` cuando NO hay diferimiento
    (devolver_stock=True pero el sistema decide aplicarlo inmediato — caso
    histórico) o como helper interno de `completar_pendiente`.

    Crea egreso en destino + ingreso en origen, ambos COMPLETADO, y
    actualiza stock con F() para concurrencia segura.
    """
    from app.models import Movimientos_Producto, Producto_Talla

    Producto_Talla.objects.filter(id=talla_destino.id).update(
        stock=F('stock') - cantidad
    )
    Movimientos_Producto.objects.create(
        dte=dte_hijo if dte_hijo else dte_original,
        ProductoTalla=talla_destino,
        sucursal_origen=sucursal_destino,
        sucursal_destino=None,
        cantidad=-cantidad,
        costo=costo, sobreprecio=sobreprecio, precio=precio,
        concepto='DEVOLUCION_NC_POST_RECEPCION',
        tipo_movimiento='EGRESO',
        estado='COMPLETADO',
        responsable=usuario,
        observaciones=(
            f'NC post-recepción DTE #{dte_original.numero_documento}: '
            f'salida desde {sucursal_destino.alias}. {observaciones}'
        )[:500],
    )

    Producto_Talla.objects.filter(id=talla_origen.id).update(
        stock=F('stock') + cantidad
    )
    ingreso = Movimientos_Producto.objects.create(
        dte=dte_hijo if dte_hijo else dte_original,
        ProductoTalla=talla_origen,
        sucursal_origen=None,
        sucursal_destino=dte_original.sucursal,
        cantidad=cantidad,
        costo=costo, sobreprecio=sobreprecio, precio=precio,
        concepto='DEVOLUCION_NC_POST_RECEPCION',
        tipo_movimiento='INGRESO',
        estado='COMPLETADO',
        responsable=usuario,
        observaciones=(
            f'NC post-recepción DTE #{dte_original.numero_documento}: '
            f'reingreso a {dte_original.sucursal.alias}. {observaciones}'
        )[:500],
    )
    _sincronizar_lotes_devolucion(
        talla_destino=talla_destino, talla_origen=talla_origen, cantidad=cantidad,
        dte_original=dte_original, dte_hijo=dte_hijo or dte_original,
        movimiento_ingreso=ingreso, costo=costo, sobreprecio=sobreprecio, precio=precio,
        observaciones=f'Devolución post-recepción DTE #{dte_original.numero_documento}',
    )


def absorber_sin_retorno(*, dte, dte_hijo, talla_destino, cantidad,
                          sucursal_destino, costo, sobreprecio, precio,
                          usuario, motivo_corto=''):
    """Modo 'absorber_sin_retorno'.

    El destino se queda con la mercadería. Crea UN solo movimiento EGRESO
    en destino para que el stock refleje la baja contable, pero NO
    incrementa stock en origen (el origen asume la pérdida).

    Aplicable cuando: emisor decide que no quiere recuperar mercadería
    (dañada, regalada, sobrante aceptado por destino).
    """
    from app.models import Movimientos_Producto, Producto_Talla

    Producto_Talla.objects.filter(id=talla_destino.id).update(
        stock=F('stock') - cantidad
    )
    Movimientos_Producto.objects.create(
        dte=dte_hijo,
        ProductoTalla=talla_destino,
        sucursal_origen=sucursal_destino,
        sucursal_destino=None,
        cantidad=-cantidad,
        costo=costo, sobreprecio=sobreprecio, precio=precio,
        concepto='SOBRANTE_ABSORBIDO_ORIGEN',
        tipo_movimiento='EGRESO',
        estado='COMPLETADO',
        responsable=usuario,
        observaciones=(
            f'NC sin devolución DTE #{dte.numero_documento}: '
            f'mercadería absorbida por {sucursal_destino.alias}. '
            f'Origen ({dte.sucursal.alias}) asume baja. {motivo_corto}'
        )[:500],
    )
    # El egreso baja el stock plano del destino: la capa FIFO también.
    _sincronizar_lotes_devolucion(
        talla_destino=talla_destino, talla_origen=None, cantidad=cantidad,
        dte_original=dte, dte_hijo=dte_hijo,
    )


def completar_movimientos_pendientes(*, dte_hijo, mapping_cantidades, usuario):
    """Modo 'completar_pendiente'.

    Toma los Movimientos_Producto en estado PENDIENTE del `dte_hijo`,
    los marca COMPLETADO con concepto DEVOLUCION_NC_POST_RECEPCION y
    aplica el cambio de stock real.

    `mapping_cantidades` mapea producto_talla_id (de origen u
    indistintamente) → cantidad efectivamente despachada. Permite
    confirmaciones parciales: si el receptor pudo despachar menos de lo
    pedido, las restantes quedan PENDIENTE para que el emisor decida
    (emitir NC complementaria sin devolución, o reintentar).

    Devuelve resumen de lo aplicado y lo que quedó pendiente.
    """
    from app.models import Movimientos_Producto, Producto_Talla

    pendientes = list(
        Movimientos_Producto.objects
        .select_for_update()
        .filter(dte=dte_hijo,
                concepto='DEVOLUCION_NC_PENDIENTE_DESPACHO',
                estado='PENDIENTE')
        .select_related('ProductoTalla')
    )

    aplicados = []
    no_aplicados = []
    # Agrupar por ProductoTalla.sku para emparejar el par egreso-ingreso.
    pares_por_sku = {}
    for mov in pendientes:
        sku = mov.ProductoTalla.sku if mov.ProductoTalla else None
        if not sku:
            continue
        pares_por_sku.setdefault(sku, {'egreso': None, 'ingreso': None})
        slot = 'egreso' if mov.tipo_movimiento == 'EGRESO' else 'ingreso'
        pares_por_sku[sku][slot] = mov

    for sku, par in pares_por_sku.items():
        egreso = par['egreso']
        ingreso = par['ingreso']
        if not egreso or not ingreso:
            # Par incompleto: dejar como está, el emisor debe regularizar.
            no_aplicados.append({'sku': sku, 'motivo': 'par_incompleto'})
            continue

        cant_pedida = abs(egreso.cantidad)
        cant_a_despachar = mapping_cantidades.get(sku, cant_pedida)
        if cant_a_despachar <= 0:
            no_aplicados.append({'sku': sku, 'motivo': 'sin_despacho'})
            continue
        if cant_a_despachar > cant_pedida:
            cant_a_despachar = cant_pedida

        talla_destino = egreso.ProductoTalla
        talla_origen = ingreso.ProductoTalla
        stock_actual = int(
            Producto_Talla.objects.only('stock').get(id=talla_destino.id).stock or 0
        )
        if stock_actual < cant_a_despachar:
            no_aplicados.append({
                'sku': sku,
                'motivo': 'stock_insuficiente',
                'disponible': stock_actual,
                'solicitado': cant_a_despachar,
            })
            continue

        Producto_Talla.objects.filter(id=talla_destino.id).update(
            stock=F('stock') - cant_a_despachar
        )
        Producto_Talla.objects.filter(id=talla_origen.id).update(
            stock=F('stock') + cant_a_despachar
        )

        egreso.cantidad = -cant_a_despachar
        egreso.concepto = 'DEVOLUCION_NC_POST_RECEPCION'
        egreso.estado = 'COMPLETADO'
        egreso.observaciones = (
            (egreso.observaciones or '')
            + f' [Confirmado por {usuario}: -{cant_a_despachar} unidades]'
        )[:500]
        egreso.save(update_fields=['cantidad', 'concepto', 'estado', 'observaciones'])

        ingreso.cantidad = cant_a_despachar
        ingreso.concepto = 'DEVOLUCION_NC_POST_RECEPCION'
        ingreso.estado = 'COMPLETADO'
        ingreso.observaciones = (
            (ingreso.observaciones or '')
            + f' [Confirmado por {usuario}: +{cant_a_despachar} unidades]'
        )[:500]
        ingreso.save(update_fields=['cantidad', 'concepto', 'estado', 'observaciones'])

        # Lotes: consume FIFO en destino y repone en origen con la antigüedad
        # del despacho original (las filas de kardex de arriba se conservan).
        _sincronizar_lotes_devolucion(
            talla_destino=talla_destino, talla_origen=talla_origen,
            cantidad=cant_a_despachar,
            dte_original=getattr(dte_hijo, 'documento_afectado', None), dte_hijo=dte_hijo,
            movimiento_ingreso=ingreso,
            costo=ingreso.costo, sobreprecio=ingreso.sobreprecio, precio=ingreso.precio,
            observaciones=f'Devolución física confirmada NC #{dte_hijo.numero_documento}',
        )

        aplicados.append({'sku': sku, 'cantidad': cant_a_despachar,
                          'cantidad_pedida': cant_pedida})

    return {'aplicados': aplicados, 'no_aplicados': no_aplicados}
