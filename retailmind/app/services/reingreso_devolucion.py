"""
Reingreso a inventario de lo que el cliente devuelve al acreditar una venta.

Lo usan la Nota de Crédito de Gestión DTE (`anular_factura_dte`) y Eliminar
documento (`eliminar_documento_venta`). Resuelve tres problemas que tenían
esos flujos (verificación del 26-09-2026):

1. **Cambio previo.** El documento dice que se vendió la talla L, pero si esa
   unidad se cambió después por M en Cambios y Devoluciones, L ya volvió al
   inventario al aprobar el cambio y lo que el cliente trae de vuelta es M.
   Reingresar L otra vez la duplicaba (18 → 19) y M nunca volvía.
2. **Acreditación previa.** Las unidades que otra NC ya devolvió no vuelven a
   entrar (una NC del saldo tras una NC parcial devolvía 3 de 2 vendidas).
3. **Lote FIFO.** Todo reingreso pasa por `inventario_service.ingresar`:
   stock plano + lote + kardex en la misma transacción. Antes subía el stock
   sin crear lote y el costeo del margen quedaba descuadrado.

Reparto de unidades. Cada unidad vendida de una talla es un "puesto" que hoy
ocupa algo: la misma talla (si nunca se cambió), el producto que se entregó
en su lugar (siguiendo cambios encadenados) o nada (si se devolvió sin
reemplazo). Los puestos se ordenan: primero las unidades nunca cambiadas,
después las cambiadas en el orden de los cambios. Las NC consumen puestos en
ese orden; así una segunda NC sobre la misma talla sigue donde quedó la
anterior. Cuando la línea tiene unidades cambiadas y sin cambiar a la vez el
documento no dice cuál trae el cliente: se asume la no cambiada primero y la
respuesta lo avisa.
"""
import logging

from django.db.models import Sum

from app.models import (
    CambioDevolucionDetalle,
    Dte_Productos,
    Producto_Talla,
    Ticket,
    Ticket_Productos,
)
from app.services import inventario_service

logger = logging.getLogger('app')

# Cambios que ya movieron stock (reingresaron lo devuelto y entregaron el
# reemplazo). Mismo criterio que ESTADOS_CAMBIO_CON_TICKET_NUEVO.
ESTADOS_CAMBIO_EJECUTADOS = (
    'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE', 'COMPLETADO',
)

_PROFUNDIDAD_MAXIMA = 10


def tickets_de_venta_dte(dte):
    """Tickets POS de los que salió el documento (vacío si no se puede saber).

    Si hay más de un candidato y no se pueden distinguir, devuelve vacío: sin
    ticket el reparto se comporta como antes (reingresa la talla del
    documento), que es mejor que leer los cambios de otra venta.
    """
    if dte is None or not dte.sucursal_id:
        return []
    referencias = dte.referencias or ''
    if 'TICKET-' in referencias:
        try:
            correlativo = referencias.split('TICKET-')[1].strip().split()[0]
            correlativo = ''.join(c for c in correlativo if c.isdigit())
        except IndexError:
            correlativo = ''
        if correlativo:
            por_referencia = list(Ticket.objects.filter(
                sucursal_id=dte.sucursal_id, correlativo=int(correlativo)))
            if len(por_referencia) == 1:
                return por_referencia

    tickets = []
    if dte.numero_documento:
        from app.utils_ventas import tipo_ticket_contradice_dte
        candidatos = [
            t for t in Ticket.objects.filter(
                sucursal_id=dte.sucursal_id, folio_dte=dte.numero_documento,
            ).order_by('id')
            if not tipo_ticket_contradice_dte(t.tipo_dte, dte.tipo_documento)
        ]
        if len(candidatos) == 1:
            tickets = candidatos

    # Ticket "de referencia" que crea Cambios y Devoluciones cuando el cambio
    # se hace sobre un DTE sin ticket POS (crear_cambio_devolucion).
    if not tickets and dte.numero_documento:
        referencia = list(Ticket.objects.filter(
            sucursal_id=dte.sucursal_id,
            observaciones__icontains=f'para DTE #{dte.numero_documento} -',
        ).order_by('id')[:2])
        if len(referencia) == 1:
            tickets = referencia
    return tickets


class _Tenencia:
    """Calcula, por unidad vendida, qué tiene hoy el cliente en su lugar."""

    def __init__(self):
        self._por_linea = {}
        self._pools = {}

    def de_linea(self, linea, profundidad=0):
        """Lista con un elemento por unidad de la línea:
        (producto_talla_id | None, numero_cambio | None)."""
        if linea.id in self._por_linea:
            return self._por_linea[linea.id]
        unidades = max(0, int(linea.stock or 0))
        detalles = list(
            CambioDevolucionDetalle.objects.filter(
                producto_original=linea,
                cambio_devolucion__estado__in=ESTADOS_CAMBIO_EJECUTADOS,
                cantidad_original__gt=0,
            ).select_related('cambio_devolucion').order_by(
                'cambio_devolucion__fecha_ejecucion', 'cambio_devolucion_id', 'id',
            )
        )
        cambiadas = sum(d.cantidad_original for d in detalles)
        puestos = [(linea.ProductoTalla_id, None)] * max(0, unidades - cambiadas)
        for detalle in detalles:
            cambio = detalle.cambio_devolucion
            reemplazo = []
            if (detalle.producto_nuevo_id and (detalle.cantidad_nueva or 0) > 0
                    and profundidad < _PROFUNDIDAD_MAXIMA):
                pool = self._pool_reemplazo(cambio, detalle, profundidad)
                tomar = min(detalle.cantidad_original, detalle.cantidad_nueva, len(pool))
                reemplazo = [
                    (pt_id, numero or cambio.numero_operacion)
                    for pt_id, numero in pool[:tomar]
                ]
                del pool[:tomar]
            puestos.extend(reemplazo)
            # Unidades devueltas sin reemplazo: el cliente no tiene nada que
            # devolver por ellas.
            puestos.extend(
                [(None, cambio.numero_operacion)]
                * (detalle.cantidad_original - len(reemplazo))
            )
        # Con datos inconsistentes (dos cambios sobre la misma unidad, bug ya
        # cerrado) nunca se reparten más puestos que unidades vendidas.
        puestos = puestos[:unidades]
        self._por_linea[linea.id] = puestos
        return puestos

    def _pool_reemplazo(self, cambio, detalle, profundidad):
        """Puestos de la línea que entregó el cambio (compartidos entre los
        detalles del mismo cambio que entregaron el mismo SKU)."""
        clave = (cambio.id, detalle.producto_nuevo_id)
        if clave in self._pools:
            return self._pools[clave]
        linea_reemplazo = None
        if cambio.ticket_nuevo_id:
            linea_reemplazo = (
                Ticket_Productos.objects.filter(
                    idTicket_id=cambio.ticket_nuevo_id,
                    ProductoTalla_id=detalle.producto_nuevo_id,
                    precio__gt=0,
                ).order_by('id').first()
            )
        if linea_reemplazo is not None:
            pool = list(self.de_linea(linea_reemplazo, profundidad + 1))
        else:
            cantidad = sum(
                d.cantidad_nueva or 0 for d in cambio.detalles.filter(
                    producto_nuevo_id=detalle.producto_nuevo_id)
            )
            pool = [(detalle.producto_nuevo_id, None)] * cantidad
        self._pools[clave] = pool
        return pool


def unidades_acreditadas_por_talla(dte, excluir_nc_id=None):
    """{producto_talla_id: unidades que NC vigentes ya acreditaron sobre el DTE}."""
    qs = Dte_Productos.objects.filter(
        dte__documento_afectado_id=dte.id,
        dte__es_nota_credito=True,
        dte__estado_dte__in=['EMITIDO', 'ACEPTADO'],
        productoTalla_id__isnull=False,
    )
    if excluir_nc_id:
        qs = qs.exclude(dte_id=excluir_nc_id)
    return {
        row['productoTalla_id']: int(row['total'] or 0)
        for row in qs.values('productoTalla_id').annotate(total=Sum('stock'))
    }


def repartir(producto_talla_id, desde, cantidad, tickets, tenencia=None):
    """Qué reingresar por las unidades [desde, desde+cantidad) de la talla.

    Devuelve (partes, ambiguo):
      partes  = [(producto_talla_id | None, cantidad, numero_cambio | None)]
      ambiguo = la línea mezcla unidades cambiadas y sin cambiar
    `producto_talla_id=None` son unidades que el cliente ya devolvió en un
    cambio sin reemplazo: no se reingresan.
    """
    tenencia = tenencia or _Tenencia()
    puestos = []
    if tickets:
        lineas = Ticket_Productos.objects.filter(
            idTicket__in=tickets,
            ProductoTalla_id=producto_talla_id,
            stock__gt=0,
            precio__gte=0,
        ).order_by('id')
        for linea in lineas:
            puestos.extend(tenencia.de_linea(linea))

    tramo = puestos[desde:desde + cantidad]
    # Sin ticket o con menos puestos que unidades (documento sin ticket, datos
    # históricos): se reingresa la talla del documento, como siempre.
    tramo.extend([(producto_talla_id, None)] * (cantidad - len(tramo)))

    partes = []
    for pt_id, numero in tramo:
        if partes and partes[-1][0] == pt_id and partes[-1][2] == numero:
            partes[-1] = (pt_id, partes[-1][1] + 1, numero)
        else:
            partes.append((pt_id, 1, numero))

    # Ambiguo: la talla tiene unidades cambiadas y sin cambiar, y esta
    # devolución no se lleva todas las que quedan (el documento no dice cuál
    # trae el cliente).
    sin_cambiar = sum(1 for _, numero in puestos if numero is None)
    cambiadas = len(puestos) - sin_cambiar
    ambiguo = sin_cambiar > 0 and cambiadas > 0 and cantidad < len(puestos) - desde
    return partes, ambiguo


def reingresar(producto_talla_id, cantidad, concepto, responsable, *, dte=None,
               ticket=None, sucursal_destino=None, costo_unitario=0,
               sobreprecio_unitario=0, precio_unitario=0, observaciones='',
               referencia_externa=''):
    """Reingreso con stock plano + lote FIFO + kardex (una transacción)."""
    producto_talla = Producto_Talla.objects.get(id=producto_talla_id)
    return inventario_service.ingresar(
        producto_talla, int(cantidad), concepto, responsable,
        sucursal_destino=sucursal_destino,
        dte=dte,
        ticket=ticket,
        costo_unitario=int(costo_unitario or 0),
        sobreprecio_unitario=int(sobreprecio_unitario or 0),
        precio_unitario=int(precio_unitario or 0),
        observaciones=(observaciones or '')[:500],
        referencia_externa=(referencia_externa or '')[:100],
    )


class ReingresoVenta:
    """Reingresos de una devolución sobre un documento de venta.

    Lleva la cuenta de las unidades ya acreditadas por talla (NC previas más lo
    que va reingresando esta misma operación), así dos líneas de la misma
    talla o una NC tras otra siguen el reparto donde quedó.

    Uso:
        reingreso = ReingresoVenta(dte, excluir_nc_id=nc.id)
        for dp, cantidad in lineas:
            reingreso.reingresar(dp.productoTalla_id, cantidad, 'DEVOLUCION_NC', usuario, ...)
        respuesta['reingreso_stock'] = reingreso.resumen
    """

    def __init__(self, dte, excluir_nc_id=None, tickets=None):
        self.dte = dte
        self.tickets = tickets if tickets is not None else tickets_de_venta_dte(dte)
        self._tenencia = _Tenencia()
        self._acreditadas = (
            unidades_acreditadas_por_talla(dte, excluir_nc_id) if dte is not None else {}
        )
        self.resumen = {'reingresos': [], 'sin_reingreso': [], 'ambiguos': []}

    def ya_acreditadas(self, producto_talla_id):
        return self._acreditadas.get(producto_talla_id, 0)

    def pendientes(self, producto_talla_id, vendidas):
        """Unidades vendidas de la talla que ninguna devolución acreditó aún."""
        return max(0, int(vendidas or 0) - self.ya_acreditadas(producto_talla_id))

    def reingresar(self, producto_talla_id, cantidad, concepto, responsable, *,
                   dte_movimiento=None, ticket_movimiento=None, sucursal_destino=None,
                   costo_unitario=0, sobreprecio_unitario=0, precio_unitario=0,
                   observaciones='', referencia_externa=''):
        """Reingresa `cantidad` unidades vendidas de la talla, redirigidas a lo
        que el cliente tiene hoy. Devuelve las unidades que entraron."""
        cantidad = int(cantidad or 0)
        if cantidad <= 0 or not producto_talla_id:
            return 0
        desde = self.ya_acreditadas(producto_talla_id)
        partes, ambiguo = repartir(
            producto_talla_id, desde, cantidad, self.tickets, self._tenencia)
        self._acreditadas[producto_talla_id] = desde + cantidad
        if ambiguo:
            self.resumen['ambiguos'].append(producto_talla_id)

        entraron = 0
        for pt_id, unidades, numero_cambio in partes:
            if pt_id is None:
                self.resumen['sin_reingreso'].append({
                    'producto_talla_id': producto_talla_id,
                    'cantidad': unidades,
                    'cambio': numero_cambio,
                })
                continue
            es_redirigido = pt_id != producto_talla_id
            obs = observaciones
            if es_redirigido:
                sku_original = Producto_Talla.objects.filter(
                    id=producto_talla_id).values_list('sku', flat=True).first()
                obs = (f'{observaciones} — el SKU {sku_original} se había cambiado '
                       f'en {numero_cambio}: reingresa lo que se entregó en su lugar')
            movimiento = reingresar(
                pt_id, unidades, concepto, responsable,
                dte=dte_movimiento, ticket=ticket_movimiento,
                sucursal_destino=sucursal_destino,
                # El costo de la línea original solo vale para la misma talla.
                costo_unitario=(costo_unitario if not es_redirigido
                                else _costo_producto(pt_id)),
                sobreprecio_unitario=sobreprecio_unitario if not es_redirigido else 0,
                precio_unitario=precio_unitario,
                observaciones=obs,
                referencia_externa=referencia_externa,
            )
            entraron += unidades
            self.resumen['reingresos'].append({
                'producto_talla_id': pt_id,
                'sku': movimiento.ProductoTalla.sku,
                'cantidad': unidades,
                'cambio': numero_cambio if es_redirigido else None,
            })
        if self.resumen['sin_reingreso'] or any(r['cambio'] for r in self.resumen['reingresos']):
            logger.info(
                "Reingreso de devolucion con cambios previos dte=%s talla=%s desde=%s "
                "cantidad=%s resumen=%s",
                getattr(self.dte, 'id', None), producto_talla_id, desde, cantidad,
                self.resumen,
            )
        return entraron

    def avisos(self):
        """Textos para el operador (vacío si no hubo nada especial)."""
        avisos = []
        for r in self.resumen['reingresos']:
            if r['cambio']:
                avisos.append(
                    f"Reingresó {r['cantidad']} u. del SKU {r['sku']}: es lo que el "
                    f"cliente se llevó en el cambio {r['cambio']} (lo vendido "
                    f"originalmente ya había vuelto al inventario con ese cambio)."
                )
        for s in self.resumen['sin_reingreso']:
            avisos.append(
                f"{s['cantidad']} u. no reingresaron: se devolvieron sin reemplazo "
                f"en el cambio {s['cambio']}."
            )
        if self.resumen['ambiguos']:
            skus = list(Producto_Talla.objects.filter(
                id__in=self.resumen['ambiguos']).values_list('sku', flat=True))
            avisos.append(
                'La venta tiene unidades cambiadas y sin cambiar del SKU '
                f"{', '.join(str(s) for s in skus)}: se reingresó primero la talla "
                'vendida. Si el cliente devolvió el producto del cambio, corrija con '
                'un ajuste de inventario.'
            )
        return avisos


def _costo_producto(producto_talla_id):
    pt = Producto_Talla.objects.select_related('producto').filter(id=producto_talla_id).first()
    return int(getattr(pt.producto, 'costo', 0) or 0) if pt and pt.producto else 0
