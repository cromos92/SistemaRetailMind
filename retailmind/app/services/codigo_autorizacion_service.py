"""
Código de autorización de la barra superior como firma de Administrador/Maestro.

Es la ÚNICA credencial para que un Administrador o Maestro autorice una
operación especial sin iniciar sesión en esa caja. El PIN fijo de Mi Perfil se
eliminó (25-09-2026): el código dinámico rota cada hora y es de un solo uso,
así que no puede quedar anotado ni reutilizarse.

Lo usan:
  - Devolución de dinero directa (`/app/devolucion-garantia/`).
  - Aprobación de un cambio FUERA DE PLAZO (`/app/ventas/cambios-devoluciones/`).
  - Descuento a la diferencia de un ticket de cambio en el POS.

Uso típico en una vista:

    try:
        codigo_obj, autorizador = validar_codigo_supervisor(
            usuario=request.user, codigo=codigo, tipo_operacion='OTRO', ...)
    except CodigoAutorizacionError as e:
        return JsonResponse(e.as_json(), status=e.status)

    with transaction.atomic():
        consumir_codigo(codigo_obj)   # lock + marca usado; si algo falla, se revierte
        ...
"""
import logging
import re
from datetime import timedelta

from django.utils import timezone

from app.models import CodigoAutorizacionDinamico, RegistroAutorizacion, es_rol_administrador

logger = logging.getLogger('app')

MAX_INTENTOS_FALLIDOS = 5
VENTANA_INTENTOS_MINUTOS = 15


class CodigoAutorizacionError(Exception):
    """Código rechazado. `code` y `status` van tal cual a la respuesta JSON."""

    def __init__(self, mensaje, code='INVALID_AUTH_CODE', status=403, intentos_restantes=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.code = code
        self.status = status
        self.intentos_restantes = intentos_restantes

    def as_json(self):
        data = {'success': False, 'code': self.code, 'error': self.mensaje}
        if self.intentos_restantes is not None:
            data['intentos_restantes'] = self.intentos_restantes
        return data


def es_autorizador_admin(usuario):
    """Maestro o Administrador activo (Jefe incluido: es un Administrador acotado)."""
    return bool(
        usuario
        and usuario.is_active
        and getattr(usuario, 'es_activo', True)
        and es_rol_administrador(usuario)
    )


def intentos_fallidos_recientes(usuario, tipo_operacion, operacion=None):
    """Intentos fallidos del usuario en la ventana, acotados a UNA operación:
    fallar el código en el POS no debe bloquear el módulo de devoluciones.

    `operacion` acota además por `datos_adicionales.operacion`, para las
    operaciones que comparten un `tipo_operacion` genérico (OTRO también lo usa
    el permiso temporal de Cambios)."""
    desde = timezone.now() - timedelta(minutes=VENTANA_INTENTOS_MINUTOS)
    qs = RegistroAutorizacion.objects.filter(
        usuario_solicitante=usuario,
        tipo_operacion=tipo_operacion,
        exitoso=False,
        fecha_hora__gte=desde,
    )
    if operacion:
        qs = qs.filter(datos_adicionales__operacion=operacion)
    return qs.count()


def validar_codigo_supervisor(*, usuario, codigo, tipo_operacion, exige_admin=True,
                              ip=None, sucursal=None, cambio=None,
                              descripcion='', datos=None, operacion=None):
    """
    Valida el código SIN consumirlo. Devuelve (codigo_obj, autorizador).

    - Freno de fuerza bruta: MAX_INTENTOS_FALLIDOS por usuario y operación.
    - `exige_admin=True`: el dueño del código debe ser Maestro o Administrador.
      Con False solo se exige que sea un usuario activo (la vista decide el rol).
    - Cada rechazo queda en RegistroAutorizacion(exitoso=False) y cuenta para
      el freno. Con `operacion`, el rechazo lleva `datos_adicionales.operacion`
      y el freno cuenta solo esa operación.

    Lanza CodigoAutorizacionError.
    """
    codigo = str(codigo or '').strip()
    if not re.fullmatch(r'\d{6}', codigo):
        raise CodigoAutorizacionError(
            'El código de autorización debe tener 6 dígitos', code='AUTH_CODE_FORMAT', status=400,
        )

    fallidos = intentos_fallidos_recientes(usuario, tipo_operacion, operacion)
    if fallidos >= MAX_INTENTOS_FALLIDOS:
        raise CodigoAutorizacionError(
            f'Demasiados intentos con código incorrecto. Intente nuevamente en '
            f'{VENTANA_INTENTOS_MINUTOS} minutos.',
            code='AUTH_CODE_BLOCKED', status=429,
        )

    es_valido, mensaje, codigo_obj = CodigoAutorizacionDinamico.validar_codigo(codigo)
    autorizador = codigo_obj.generado_por if (es_valido and codigo_obj) else None

    rechazo = None
    if not es_valido or autorizador is None:
        rechazo = mensaje or 'Código de autorización inválido'
    elif not (autorizador.is_active and getattr(autorizador, 'es_activo', True)):
        rechazo = 'El código no pertenece a un usuario activo'
    elif exige_admin and not es_autorizador_admin(autorizador):
        rechazo = 'El código debe ser de un ADMINISTRADOR o MAESTRO'

    if rechazo:
        RegistroAutorizacion.objects.create(
            usuario_solicitante=usuario,
            usuario_autorizador=autorizador,
            tipo_operacion=tipo_operacion,
            descripcion=f'Código rechazado: {rechazo}. {descripcion}'.strip(),
            ip_origen=ip,
            exitoso=False,
            cambio_devolucion=cambio,
            sucursal_solicitante=sucursal,
            datos_adicionales={
                **(datos or {}),
                **({'operacion': operacion} if operacion else {}),
                'motivo': rechazo,
                'intentos_previos': fallidos,
            },
        )
        logger.warning(
            "Codigo de autorizacion rechazado op=%s usuario=%s motivo=%s intentos_previos=%s",
            tipo_operacion, getattr(usuario, 'username', None), rechazo, fallidos,
        )
        raise CodigoAutorizacionError(
            rechazo, intentos_restantes=max(0, MAX_INTENTOS_FALLIDOS - fallidos - 1),
        )

    return codigo_obj, autorizador


def consumir_codigo(codigo_obj):
    """Marca el código como usado bajo lock. Llamar DENTRO de transaction.atomic
    para que, si la operación falla después, el código no quede quemado."""
    bloqueado = CodigoAutorizacionDinamico.objects.select_for_update().get(id=codigo_obj.id)
    if not bloqueado.es_valido():
        raise CodigoAutorizacionError(
            'El código fue utilizado o venció antes de completar la operación. '
            'Pida uno nuevo.',
            code='AUTH_CODE_ALREADY_USED', status=409,
        )
    bloqueado.usado = True
    bloqueado.save(update_fields=['usado'])
    return bloqueado
