"""
Señales de Django para la aplicación app.
Se encargan de crear notificaciones automáticamente cuando ocurren ciertos eventos.
"""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Dte, NotificacionDTE


@receiver(post_save, sender=Dte)
def gestionar_notificacion_dte(sender, instance, created, **kwargs):
    """
    Gestiona las notificaciones de DTEs:
    1. Crea una notificación cuando se emite un DTE nuevo a otra empresa
    2. Elimina/marca como procesada la notificación cuando el DTE se recepciona
    """
    if created:
        # === CREAR NOTIFICACIÓN PARA DTE NUEVO ===
        # Solo para DTEs nuevos que estén emitidos
        if instance.estado_dte != 'EMITIDO':
            return
        
        # Verificar que haya un receptor
        if not instance.receptor:
            return
        
        # Solo crear notificación si emisor != receptor
        if instance.emisor_id == instance.receptor_id:
            return
        
        # Crear la notificación
        try:
            NotificacionDTE.crear_notificacion_dte_emitido(instance)
        except Exception as e:
            print(f"Error al crear notificacion de DTE: {str(e)}")
    else:
        # === PROCESAR/ELIMINAR NOTIFICACIÓN CUANDO DTE CAMBIA DE ESTADO ===
        # Si el DTE ya no está en estado EMITIDO, eliminar las notificaciones
        if instance.estado_dte != 'EMITIDO':
            try:
                # Eliminar todas las notificaciones asociadas a este DTE
                NotificacionDTE.objects.filter(dte=instance).delete()
            except Exception as e:
                print(f"Error al eliminar notificaciones de DTE: {str(e)}")
