import logging

from app.models import Empresa
from django.db import transaction

logger = logging.getLogger('app')


def get_empresa_actual(request):
    empresa_id = request.session.get('idEmpresaActual')
    if empresa_id:
        try:
            return Empresa.objects.get(id=empresa_id)
        except Empresa.DoesNotExist:
            return None
    return None


def generar_numero_solicitud():
    """
    Genera un número único para solicitudes de regularización
    Formato: SOL-YYYYMM-NNNNN
    """
    from app.models import Solicitud_Regularizacion
    from django.utils import timezone
    import re
    
    hoy = timezone.now()
    prefijo = f"SOL-{hoy.strftime('%Y%m')}-"
    
    ultima_solicitud = Solicitud_Regularizacion.objects.filter(
        numero_solicitud__startswith=prefijo
    ).order_by('-numero_solicitud').first()
    
    if ultima_solicitud:
        match = re.search(r'-(\d+)$', ultima_solicitud.numero_solicitud)
        if match:
            ultimo_numero = int(match.group(1))
            nuevo_numero = ultimo_numero + 1
        else:
            nuevo_numero = 1
    else:
        nuevo_numero = 1
    
    return f"{prefijo}{nuevo_numero:05d}"


def notificar_nueva_solicitud(solicitud):
    """
    Notifica al emisor sobre una nueva solicitud de regularización.
    TODO: Implementar notificación por email o sistema interno.
    """
    logger.info(
        "Nueva solicitud #%s de %s",
        solicitud.numero_solicitud,
        solicitud.sucursal_solicitante.alias,
    )


def notificar_solicitud_aprobada(solicitud):
    """
    Notifica al receptor que su solicitud fue aprobada.
    TODO: Implementar notificación por email.
    """
    logger.info("Solicitud #%s aprobada", solicitud.numero_solicitud)


def notificar_solucion_ejecutada(solicitud):
    """
    Notifica al receptor que la solución fue ejecutada.
    TODO: Implementar notificación por email.
    """
    logger.info("Solución ejecutada para solicitud #%s", solicitud.numero_solicitud)
