"""
Señales de Django para la aplicación app.
Se encargan de crear notificaciones automáticamente cuando ocurren ciertos eventos.
"""
import logging

from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    Dte, NotificacionDTE, Ticket_Productos,
    Movimientos_Producto, Compras_Producto_Talla,
)
from .models.predicciones import PendienteReevaluacion, StockInicialTemporada

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
                'fecha_primer_ingreso': instance.fecha or timezone.now().date(),
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
