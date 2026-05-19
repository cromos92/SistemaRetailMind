"""
Helper reutilizable para validar PIN de Autorización de Administrador
en operaciones especiales (cambios fuera de plazo, descuentos manuales,
anulaciones, etc.).

Uso típico en una vista:

    from app.utils_pin_autorizacion import autorizar_con_pin

    def mi_vista(request):
        data = json.loads(request.body)
        supervisor, error = autorizar_con_pin(
            request, data, operacion='DESCUENTO_MANUAL',
            descripcion='Descuento > 30% en venta #1234'
        )
        if error:
            return error  # JsonResponse con código y mensaje listo para devolver
        # supervisor es el Usuario admin que autorizó (ya con RegistroAutorizacion creado)
        ...
"""
import json
import logging

from django.http import JsonResponse

logger = logging.getLogger('app')


def autorizar_con_pin(request, data=None, operacion='ACCION_ESPECIAL', descripcion=''):
    """
    Valida el PIN de administrador presente en `data` (o en el body de `request`)
    y registra la autorización en RegistroAutorizacion.

    Args:
        request: HttpRequest. Se usa para obtener IP, usuario solicitante y sucursal.
        data: dict opcional con la clave 'supervisor_pin'. Si es None se intenta parsear request.body.
        operacion: str. Tipo de operación para auditoría (ej: 'DESCUENTO_MANUAL',
                   'ANULACION_VENTA', 'AJUSTE_STOCK', 'CAMBIO_FUERA_PLAZO').
        descripcion: str. Texto libre que describe la operación autorizada.

    Returns:
        tuple (supervisor, error_response):
            - Si OK: (Usuario_admin, None)
            - Si falla: (None, JsonResponse con success=False y mensaje)
    """
    if data is None:
        try:
            data = json.loads(request.body or '{}')
        except (ValueError, json.JSONDecodeError):
            return None, JsonResponse({
                'success': False,
                'error': 'Payload JSON inválido',
                'requiere_autorizacion': True,
            }, status=400)

    supervisor_pin = str(data.get('supervisor_pin', '') or '').strip()
    if not supervisor_pin:
        return None, JsonResponse({
            'success': False,
            'error': 'Se requiere PIN de Administrador para autorizar esta operación',
            'requiere_autorizacion': True,
        })

    # Buscar admin por PIN (usa el método del modelo Usuario)
    from users.models import Usuario as UsuarioModel
    supervisor = UsuarioModel.buscar_admin_por_pin(supervisor_pin)

    if not supervisor:
        logger.warning(
            'PIN de autorización inválido. operacion=%s solicitante=%s ip=%s',
            operacion, getattr(request.user, 'username', '?'),
            request.META.get('REMOTE_ADDR'),
        )
        return None, JsonResponse({
            'success': False,
            'error': 'PIN de Administrador inválido. Ingrese el PIN de 6 dígitos de un administrador autorizado.',
            'requiere_autorizacion': True,
        })

    # Crear registro de autorización para auditoría
    try:
        from .models import RegistroAutorizacion
        sucursal_solicitante = _resolver_sucursal_solicitante(request)
        sucursal_autorizador = _resolver_sucursal_supervisor(supervisor)
        es_cross_branch = bool(
            sucursal_autorizador and sucursal_solicitante
            and sucursal_autorizador.id != sucursal_solicitante.id
        )
        RegistroAutorizacion.objects.create(
            usuario_solicitante=request.user,
            usuario_autorizador=supervisor,
            tipo_operacion=operacion,
            descripcion=descripcion or operacion,
            ip_origen=request.META.get('REMOTE_ADDR'),
            exitoso=True,
            sucursal_solicitante=sucursal_solicitante,
            sucursal_autorizador=sucursal_autorizador,
            es_cross_branch=es_cross_branch,
            requiere_revision=es_cross_branch,
        )
    except Exception as e:
        # No bloqueamos la autorización si el registro falla; solo logueamos.
        logger.exception('No se pudo crear RegistroAutorizacion: %s', e)

    return supervisor, None


def _resolver_sucursal_solicitante(request):
    """Devuelve la Sucursal del usuario que solicita la autorización (desde sesión)."""
    sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    if not sucursal_id:
        return None
    try:
        from .models import Sucursal
        return Sucursal.objects.filter(id=sucursal_id).first()
    except Exception:
        return None


def _resolver_sucursal_supervisor(supervisor):
    """Devuelve la sucursal asociada al supervisor (perfil o atributo directo)."""
    try:
        perfil_sup = getattr(supervisor, 'perfil', None)
        if perfil_sup and getattr(perfil_sup, 'sucursal', None):
            return perfil_sup.sucursal
        return getattr(supervisor, 'sucursal', None)
    except Exception:
        return None
