"""
Señales de Django para la aplicación app.
Se encargan de crear notificaciones automáticamente cuando ocurren ciertos eventos.
"""
import logging

from django.db.models import F
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    Dte, NotificacionDTE, Ticket, Ticket_Productos, TicketDetallePago,
    Movimientos_Producto, Compras_Producto_Talla,
    ArqueoCaja, CambioDevolucion, DepositoBancario,
)
from .models.predicciones import PendienteReevaluacion, StockInicialTemporada
from .cache_utils import invalidate_ventas_cache

logger = logging.getLogger('app')


@receiver(post_save, sender=Dte)
def gestionar_notificacion_dte(sender, instance, created, **kwargs):
    """
    Gestiona las notificaciones de DTEs:
    1. Crea una notificación cuando se emite un DTE nuevo a otra empresa
    2. Elimina/marca como procesada la notificación cuando el DTE se recepciona
    """
    if created:
        if instance.estado_dte != 'EMITIDO':
            return
        
        if not instance.receptor:
            return
        
        if instance.emisor_id == instance.receptor_id:
            return
        
        try:
            NotificacionDTE.crear_notificacion_dte_emitido(instance)
        except Exception:
            logger.exception("Error al crear notificación de DTE #%s", instance.numero_documento)
    else:
        if instance.estado_dte != 'EMITIDO':
            try:
                NotificacionDTE.objects.filter(dte=instance).delete()
            except Exception:
                logger.exception("Error al eliminar notificaciones de DTE #%s", instance.numero_documento)


# ──────────────────────────────────────────────────────────────
#  Señales del motor predictivo (patrón mark-then-evaluate)
# ──────────────────────────────────────────────────────────────

@receiver(post_save, sender=Ticket_Productos)
def marcar_para_reevaluacion(sender, instance, created, **kwargs):
    """Solo marca el producto para reevaluación — NO calcula nada pesado."""
    if not created:
        return
    try:
        producto = instance.ProductoTalla.producto
        for tipo in ('velocidad', 'quiebre_talle'):
            PendienteReevaluacion.objects.update_or_create(
                producto=producto,
                tipo=tipo,
                procesado=False,
                defaults={'fecha_marcado': timezone.now()},
            )
    except Exception:
        logger.exception("Error al marcar reevaluación para venta %s", instance.pk)


@receiver(post_save, sender=Movimientos_Producto)
def notificar_stock_a_allconnected(sender, instance, created, **kwargs):
    """
    Cada vez que se registra un movimiento de stock (venta, devolución,
    ajuste, recepción), notifica a AllConnected con el nuevo stock del SKU.
    La notificación va en un thread daemon — no bloquea la caja.

    Deriva ``rut_empresa`` desde la cadena
    ``Producto_Talla → Producto → Sucursal → Empresa.rut`` para que
    AllConnected resuelva el canal RetailMind correcto vía
    ``(tipo_marketplace=RETAILMIND, rut_empresa, ACTIVO)``. Esto soporta
    nativamente RetailMind multi-empresa (un mismo POS atendiendo varias
    organizaciones) sin hardcodear ``ALLCONNECTED_CANAL_ORIGEN_ID``.
    """
    if not created:
        return
    pt = instance.ProductoTalla
    if not pt:
        return
    try:
        # Resolver RUT con varios fallbacks defensivos para no romper
        # la venta si la cadena de FKs tuviera algún None.
        rut = None
        try:
            producto = pt.producto
            sucursal = getattr(producto, 'sucursal', None) if producto else None
            empresa = getattr(sucursal, 'empresa', None) if sucursal else None
            rut = getattr(empresa, 'rut', None) if empresa else None
        except Exception:
            rut = None

        from .stock_notifier import notificar_cambio_stock
        notificar_cambio_stock(
            sku=str(pt.sku),
            nuevo_stock=int(pt.stock or 0),
            rut_empresa=rut,
        )
    except Exception:
        # Nunca romper la venta por un fallo de notificación
        logger.exception(
            "Error notificando stock a AllConnected para SKU %s",
            getattr(pt, 'sku', '?'),
        )


@receiver(post_save, sender=Movimientos_Producto)
def registrar_ingreso_stock(sender, instance, created, **kwargs):
    """Registra primer ingreso de temporada en StockInicialTemporada."""
    if not created:
        return
    if instance.concepto not in ('RECEPCION_COMPRA', 'INGRESO_INICIAL', 'REPOSICION_STOCK'):
        return
    if not instance.ProductoTalla:
        return
    try:
        producto = instance.ProductoTalla.producto
        if not producto.temporada or not producto.anio_temporada:
            return

        sit, was_created = StockInicialTemporada.objects.get_or_create(
            producto=producto,
            temporada=producto.temporada,
            anio=producto.anio_temporada,
            defaults={
                'fecha_primer_ingreso': instance.fecha or timezone.localdate(),
                'stock_inicial': instance.cantidad,
                'stock_total_ingresado': instance.cantidad,
            }
        )
        if not was_created:
            sit.stock_total_ingresado = F('stock_total_ingresado') + instance.cantidad
            sit.save(update_fields=['stock_total_ingresado'])
    except Exception:
        logger.exception("Error al registrar ingreso stock para movimiento %s", instance.pk)


@receiver(post_save, sender=Compras_Producto_Talla)
def recalcular_totales_compra(sender, instance, **kwargs):
    """Actualiza contadores en la compra padre cuando cambia un item."""
    try:
        compra = instance.compra_producto.compras
        compra.save()
    except Exception:
        logger.exception("Error al recalcular totales compra para item %s", instance.pk)


# ──────────────────────────────────────────────────────────────
#  Invalidación de cache del módulo ventas
# ──────────────────────────────────────────────────────────────
#
# Estrategia: ante cualquier cambio en entidades que impactan dashboards de
# ventas, se incrementa el contador de versión de la sucursal afectada (y
# el global para vistas multi-sucursal). Ver `app.cache_utils` para detalles.


def _invalidate_sucursal_cache(sucursal_id):
    """Invalida cache de `ventas` para una sucursal y el bucket global."""
    try:
        invalidate_ventas_cache(sucursal_id=sucursal_id, global_too=True)
    except Exception:
        # Nunca romper escrituras por un fallo de cache
        logger.exception("Error invalidando cache ventas para sucursal %s", sucursal_id)


@receiver(post_save, sender=Ticket)
@receiver(post_delete, sender=Ticket)
def _invalidate_on_ticket_change(sender, instance, **kwargs):
    _invalidate_sucursal_cache(getattr(instance, 'sucursal_id', None))


@receiver(post_save, sender=Dte)
@receiver(post_delete, sender=Dte)
def _invalidate_on_dte_change(sender, instance, **kwargs):
    _invalidate_sucursal_cache(getattr(instance, 'sucursal_id', None))


@receiver(post_save, sender=TicketDetallePago)
@receiver(post_delete, sender=TicketDetallePago)
def _invalidate_on_ticket_pago_change(sender, instance, **kwargs):
    ticket = getattr(instance, 'ticket', None)
    suc = getattr(ticket, 'sucursal_id', None) if ticket else None
    _invalidate_sucursal_cache(suc)


@receiver(post_save, sender=ArqueoCaja)
@receiver(post_delete, sender=ArqueoCaja)
def _invalidate_on_arqueo_change(sender, instance, **kwargs):
    _invalidate_sucursal_cache(getattr(instance, 'sucursal_id', None))


@receiver(post_save, sender=CambioDevolucion)
@receiver(post_delete, sender=CambioDevolucion)
def _invalidate_on_cambio_change(sender, instance, **kwargs):
    _invalidate_sucursal_cache(getattr(instance, 'sucursal_id', None))


@receiver(post_save, sender=DepositoBancario)
@receiver(post_delete, sender=DepositoBancario)
def _invalidate_on_deposito_change(sender, instance, **kwargs):
    arqueo = getattr(instance, 'arqueo', None)
    suc = getattr(arqueo, 'sucursal_id', None) if arqueo else None
    _invalidate_sucursal_cache(suc)

    # Recalcular cache denormalizado del arqueo afectado.
    # Esto mantiene actualizados los totales usados por `listar_arqueos` sin
    # necesidad de agregaciones runtime (y elimina los N+1 que quedaban).
    if arqueo is not None:
        try:
            arqueo.recalcular_cache_depositos(save=True)
        except Exception:
            logger.exception(
                "Error recalculando cache depositos para arqueo %s",
                getattr(arqueo, 'pk', None),
            )
