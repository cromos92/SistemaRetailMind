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
from django.db.models import F


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
    Movimientos_Producto.objects.create(
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

        aplicados.append({'sku': sku, 'cantidad': cant_a_despachar,
                          'cantidad_pedida': cant_pedida})

    return {'aplicados': aplicados, 'no_aplicados': no_aplicados}
