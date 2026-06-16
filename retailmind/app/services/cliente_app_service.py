"""
Servicio de autenticación de la app móvil de CLIENTES finales (function-based,
estilo `fidelizacion_service.py`).

Una sola fuente de verdad para: vincular la cuenta de un cliente del CRM,
solicitar/verificar OTP, emitir y refrescar tokens, y enviar el OTP por email.

Decisiones clave (ver `app/models/cliente_app.py` y el plan):
- Login SIN contraseña: RUT + OTP de un solo uso.
- Registro = SOLO VINCULACIÓN: el `Cliente` debe existir en el CRM (creado en
  caja/POS). La app NO crea clientes nuevos.
- Anti-enumeración de RUT: `solicitar_otp`/`vincular` nunca revelan si el RUT
  existe; responden siempre de forma genérica.
- JWT para un `Cliente` que NO es el AUTH_USER_MODEL: el access es un
  `AccessToken` de SimpleJWT con claims propios (`tipo='cliente'`, `cliente_id`)
  y SIN `user_id`, de modo que `JWTAuthentication` de staff jamás lo resuelve. El
  refresh es un UUID opaco con hash en BD (`RefreshTokenClienteApp`), revocable.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from rest_framework_simplejwt.tokens import AccessToken

from app.models import (
    CuentaClienteApp,
    CodigoOTPCliente,
    RefreshTokenClienteApp,
)
from app.services import fidelizacion_service

logger = logging.getLogger('app')


class ClienteAppError(Exception):
    """Error de negocio de la app de clientes (OTP inválido, cuenta bloqueada…)."""


# Mensaje genérico único para no filtrar si un RUT existe o no.
MSG_OTP_ENVIADO = 'Si el RUT está registrado, te enviamos un código de acceso.'
MSG_NO_VINCULABLE = ('No pudimos vincular tu cuenta. Si ya compraste con nosotros, '
                     'acércate a la tienda para registrar tus datos.')


# ========== CUENTA ==========

def get_or_create_cuenta_app(cliente):
    """Obtiene (o crea) la CuentaClienteApp del cliente. Devuelve (cuenta, creada)."""
    return CuentaClienteApp.objects.get_or_create(cliente=cliente)


# ========== OTP ==========

def solicitar_otp(rut, *, canal='EMAIL', ip=None):
    """
    Solicita un OTP para un cliente EXISTENTE. Anti-enumeración: responde siempre
    el mismo mensaje genérico, exista o no el cliente o tenga o no canal.

    Crea la CuentaClienteApp si el cliente existe (vinculación implícita) pero NO
    crea clientes nuevos.
    """
    cliente = fidelizacion_service.resolver_cliente_por_rut(rut)
    if not cliente:
        logger.info("Solicitud OTP RUT no encontrado (respuesta genérica)")
        return {'mensaje': MSG_OTP_ENVIADO}

    cuenta, _ = get_or_create_cuenta_app(cliente)
    if cuenta.esta_bloqueada():
        logger.info("Solicitud OTP cuenta bloqueada cliente=%s", cliente.id)
        return {'mensaje': MSG_OTP_ENVIADO}

    destino = _destino_para_canal(cliente, canal)
    if not destino:
        # Cliente sin email/celular: no se puede enviar; respuesta genérica igual.
        logger.info("Solicitud OTP sin canal %s para cliente=%s", canal, cliente.id)
        return {'mensaje': MSG_OTP_ENVIADO}

    otp, codigo_plano = CodigoOTPCliente.crear_para_cliente(
        cliente, canal=canal, destino=destino, ip=ip,
    )
    enviar_otp(cliente, codigo_plano, canal=canal, destino=destino)
    logger.info("OTP emitido cliente=%s canal=%s", cliente.id, canal)
    return {'mensaje': MSG_OTP_ENVIADO}


def verificar_otp(rut, codigo, *, ip=None, user_agent=None):
    """
    Verifica el OTP y, si es válido, emite los tokens.
    Devuelve dict con access/refresh/expires_at/cliente.
    Lanza ClienteAppError genérico si falla (no distingue RUT de código).
    """
    cliente = fidelizacion_service.resolver_cliente_por_rut(rut)
    if not cliente:
        raise ClienteAppError('Código inválido o expirado.')

    cuenta, _ = get_or_create_cuenta_app(cliente)
    if cuenta.esta_bloqueada():
        raise ClienteAppError('Cuenta temporalmente bloqueada. Intenta más tarde.')

    ok, error = CodigoOTPCliente.validar(cliente, codigo)
    if not ok:
        cuenta.registrar_fallo_login()
        raise ClienteAppError(error or 'Código inválido o expirado.')

    cuenta.registrar_login()
    # El canal del OTP recién usado confirma el dato de contacto.
    _marcar_canal_verificado(cuenta, codigo)

    tokens = emitir_tokens(cliente, cuenta, ip=ip, user_agent=user_agent)
    logger.info("Login OTP exitoso cliente=%s", cliente.id)
    return tokens


# ========== TOKENS ==========

def emitir_tokens(cliente, cuenta, *, ip=None, user_agent=None):
    """
    Construye el access token (SimpleJWT con claims propios, SIN user_id) y el
    refresh opaco (RefreshTokenClienteApp). Devuelve el payload listo para la API.
    """
    access = AccessToken()
    access['tipo'] = 'cliente'
    access['cliente_id'] = cliente.id
    access['cuenta_app_id'] = cuenta.id

    _, refresh_plano = RefreshTokenClienteApp.crear_token(
        cuenta,
        dias_expiracion=settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].days,
        ip=ip,
        user_agent=user_agent,
    )

    expires_at = timezone.now() + settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']
    return {
        'access': str(access),
        'refresh': refresh_plano,
        'expires_at': expires_at.isoformat(),
        'cliente': {
            'id': cliente.id,
            'nombre_completo': cliente.nombre_completo,
            'rut': cliente.rut,
            'email': cliente.email,
        },
    }


def refrescar_tokens(refresh_plano, *, ip=None):
    """
    Rota el refresh token y emite un access nuevo.
    Devuelve dict access/refresh/expires_at. Lanza ClienteAppError si falla.
    """
    nuevo_rt, nuevo_plano, error = RefreshTokenClienteApp.validar_y_rotar(
        refresh_plano, ip=ip,
    )
    if error:
        raise ClienteAppError(error)

    cuenta = nuevo_rt.cuenta
    cliente = cuenta.cliente
    access = AccessToken()
    access['tipo'] = 'cliente'
    access['cliente_id'] = cliente.id
    access['cuenta_app_id'] = cuenta.id

    expires_at = timezone.now() + settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']
    return {
        'access': str(access),
        'refresh': nuevo_plano,
        'expires_at': expires_at.isoformat(),
    }


def logout(refresh_plano):
    """Revoca la familia del refresh token (cierra la sesión). Silencioso si no existe."""
    if not refresh_plano:
        return
    token_hash = RefreshTokenClienteApp.hash_token(refresh_plano)
    rt = RefreshTokenClienteApp.objects.filter(token_hash=token_hash).first()
    if rt:
        rt.revocar_familia()


# ========== ENVÍO DEL OTP ==========

def _destino_para_canal(cliente, canal):
    if canal == 'EMAIL':
        return (cliente.email or '').strip()
    if canal == 'SMS':
        return fidelizacion_service.normalizar_celular(cliente.celular)
    return ''


def _marcar_canal_verificado(cuenta, codigo):
    """Marca como verificado el canal del último OTP usado por el cliente."""
    otp = (
        CodigoOTPCliente.objects
        .filter(cliente=cuenta.cliente, usado=True)
        .order_by('-used_at')
        .first()
    )
    if not otp:
        return
    if otp.canal == 'EMAIL' and not cuenta.email_verificado:
        cuenta.email_verificado = True
        cuenta.save(update_fields=['email_verificado', 'updated_at'])
    elif otp.canal == 'SMS' and not cuenta.celular_verificado:
        cuenta.celular_verificado = True
        cuenta.save(update_fields=['celular_verificado', 'updated_at'])


def enviar_otp(cliente, codigo, *, canal='EMAIL', destino=''):
    """Dispatcher de envío de OTP por canal. Email implementado; SMS = stub futuro."""
    if canal == 'EMAIL':
        return enviar_otp_email(destino or cliente.email, codigo)
    if canal == 'SMS':
        return enviar_otp_sms(destino, codigo)
    logger.warning("Canal de OTP desconocido: %s", canal)
    return False


def enviar_otp_email(email, codigo):
    """Envía el OTP por email con el backend SMTP ya configurado."""
    if not email:
        return False
    minutos = getattr(settings, 'OTP_EXPIRACION_MINUTOS', 10)
    asunto = 'Tu código de acceso'
    cuerpo = (
        f"Tu código de acceso es: {codigo}\n\n"
        f"Vence en {minutos} minutos. Si no solicitaste este código, ignora este mensaje."
    )
    try:
        send_mail(
            asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, [email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Fallo enviando OTP por email")
        return False


def enviar_otp_sms(celular, codigo):
    """
    Stub de envío por SMS. Punto de inserción para un proveedor chileno (Twilio,
    Redvoiss, etc.) usando credenciales vía os.environ. No implementado aún.
    """
    logger.warning("Envío de OTP por SMS no implementado (celular=%s)", bool(celular))
    return False
