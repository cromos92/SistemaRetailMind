"""
Identidad y autenticación de CLIENTES FINALES para la app móvil de fidelización.

Hoy `Cliente` (en `crm.py`) es un registro CRM puro sin password ni identidad:
lo crean el staff y el POS, y vive en el hot path del cobro. Para no contaminarlo,
el estado de la app de clientes se aísla en modelos propios (mismo patrón que
`CuentaPuntos` es OneToOne con `Cliente`, y que `RefreshTokenDesktop` aísla la
sesión del `Usuario` staff en `models_sync.py`).

Login SIN contraseña: RUT + OTP de un solo uso por email (SMS futuro). Los tres
modelos son:
- `CuentaClienteApp`: OneToOne con Cliente; estado de la app (verificación,
  último login, bloqueo). Actúa como el "user-like" en `request.user`.
- `CodigoOTPCliente`: códigos OTP (se guarda el HASH, nunca el código en claro),
  un solo uso, expiración corta, contador de intentos.
- `RefreshTokenClienteApp`: refresh tokens opacos con rotación + detección de
  reúso. Calcado de `RefreshTokenDesktop` pero ligado a `CuentaClienteApp`.

Esta es la base que la app Flutter "Paola" / puntos.realsport.cl consume vía la
API REST en `app/api/cliente/`.
"""
import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .crm import Cliente


# ========== CONSTANTES ==========

CANAL_OTP_CHOICES = [
    ('EMAIL', 'Email'),
    ('SMS', 'SMS'),
]

# Defaults; se pueden sobreescribir desde settings (ver helpers abajo).
OTP_EXPIRACION_MINUTOS_DEFAULT = 10
MAX_INTENTOS_OTP_DEFAULT = 5


def _otp_expiracion_minutos():
    return getattr(settings, 'OTP_EXPIRACION_MINUTOS', OTP_EXPIRACION_MINUTOS_DEFAULT)


def _max_intentos_otp():
    return getattr(settings, 'MAX_INTENTOS_OTP', MAX_INTENTOS_OTP_DEFAULT)


class CuentaClienteApp(models.Model):
    """
    Estado de la app móvil para un Cliente. Una cuenta por Cliente.

    Expone las properties `is_authenticated`/`is_active`/`is_anonymous` para poder
    actuar como `request.user` (la `ClienteJWTAuthentication` la devuelve desde
    `get_user`). La mayoría de los `Cliente` nunca tendrá app: el OneToOne es
    opcional (`hasattr(cliente, 'cuenta_app')`).
    """

    cliente = models.OneToOneField(
        Cliente,
        on_delete=models.CASCADE,
        related_name='cuenta_app',
    )
    email_verificado = models.BooleanField(default=False)
    celular_verificado = models.BooleanField(default=False)
    activa = models.BooleanField(default=True, db_index=True)

    ultimo_login = models.DateTimeField(null=True, blank=True)
    fecha_alta = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Bloqueo por intentos de login fallidos (anti fuerza bruta).
    intentos_fallidos = models.IntegerField(default=0)
    bloqueada_hasta = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Cuenta de Cliente (App)'
        verbose_name_plural = 'Cuentas de Cliente (App)'
        ordering = ['-fecha_alta']

    def __str__(self):
        estado = 'activa' if self.activa else 'inactiva'
        return f"{self.cliente.nombre_completo} ({estado})"

    # --- Interfaz "user-like" para DRF / request.user ---
    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        return self.activa and self.cliente.activo

    def esta_bloqueada(self):
        return bool(self.bloqueada_hasta and self.bloqueada_hasta > timezone.now())

    def registrar_login(self):
        """Resetea el contador de fallidos y marca el último login."""
        self.ultimo_login = timezone.now()
        self.intentos_fallidos = 0
        self.bloqueada_hasta = None
        self.save(update_fields=['ultimo_login', 'intentos_fallidos',
                                 'bloqueada_hasta', 'updated_at'])

    def registrar_fallo_login(self):
        """Suma un intento fallido y bloquea la cuenta tras MAX_LOGIN_FALLIDOS."""
        max_fallidos = getattr(settings, 'MAX_LOGIN_FALLIDOS', 5)
        minutos_bloqueo = getattr(settings, 'BLOQUEO_LOGIN_MINUTOS', 15)
        self.intentos_fallidos = (self.intentos_fallidos or 0) + 1
        if self.intentos_fallidos >= max_fallidos:
            self.bloqueada_hasta = timezone.now() + timezone.timedelta(minutes=minutos_bloqueo)
        self.save(update_fields=['intentos_fallidos', 'bloqueada_hasta', 'updated_at'])


class CodigoOTPCliente(models.Model):
    """
    Código OTP de un solo uso para login del cliente. Se guarda el HASH (sha256)
    del código, nunca el código en claro (mismo criterio que `RefreshTokenDesktop`).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name='otps_app',
    )
    codigo_hash = models.CharField(max_length=64, db_index=True)
    canal = models.CharField(max_length=10, choices=CANAL_OTP_CHOICES, default='EMAIL')
    destino = models.CharField(
        max_length=255,
        help_text='Email/celular al que se envió (auditoría)',
    )

    expires_at = models.DateTimeField()
    intentos = models.IntegerField(default=0)
    usado = models.BooleanField(default=False)
    used_at = models.DateTimeField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Código OTP de Cliente'
        verbose_name_plural = 'Códigos OTP de Cliente'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['cliente', 'usado', 'expires_at']),
            models.Index(fields=['codigo_hash']),
        ]

    def __str__(self):
        estado = 'usado' if self.usado else ('expirado' if self.esta_expirado else 'vigente')
        return f"OTP {self.cliente_id} ({estado})"

    @property
    def esta_expirado(self):
        return self.expires_at < timezone.now()

    @staticmethod
    def hash_codigo(codigo: str) -> str:
        """Hash SHA256 del código de 6 dígitos."""
        return hashlib.sha256(str(codigo).encode()).hexdigest()

    @classmethod
    def crear_para_cliente(cls, cliente, *, canal='EMAIL', destino='', ip=None):
        """
        Invalida los OTP vigentes del cliente y crea uno nuevo de 6 dígitos.
        Devuelve (obj, codigo_plano). El código en claro solo se devuelve aquí
        (luego viaja por email/SMS); en BD queda solo el hash.
        """
        # Un solo código activo por cliente: marca como usados los previos.
        cls.objects.filter(cliente=cliente, usado=False).update(
            usado=True, used_at=timezone.now()
        )
        codigo_plano = f"{secrets.randbelow(900000) + 100000}"  # 100000–999999
        obj = cls.objects.create(
            cliente=cliente,
            codigo_hash=cls.hash_codigo(codigo_plano),
            canal=canal,
            destino=destino or '',
            expires_at=timezone.now() + timezone.timedelta(minutes=_otp_expiracion_minutos()),
            ip_address=ip,
        )
        return obj, codigo_plano

    @classmethod
    def validar(cls, cliente, codigo_plano):
        """
        Valida `codigo_plano` contra el último OTP vigente del cliente.
        Devuelve (ok: bool, error: str|None). Incrementa el contador de intentos;
        marca el código usado al validarlo o al agotar los intentos.
        """
        otp = (
            cls.objects
            .filter(cliente=cliente, usado=False)
            .order_by('-created_at')
            .first()
        )
        if not otp:
            return False, 'Código inválido o expirado.'
        if otp.esta_expirado:
            otp.usado = True
            otp.used_at = timezone.now()
            otp.save(update_fields=['usado', 'used_at'])
            return False, 'Código inválido o expirado.'

        otp.intentos += 1
        if otp.codigo_hash != cls.hash_codigo(codigo_plano):
            if otp.intentos >= _max_intentos_otp():
                otp.usado = True
                otp.used_at = timezone.now()
                otp.save(update_fields=['intentos', 'usado', 'used_at'])
                return False, 'Código inválido o expirado.'
            otp.save(update_fields=['intentos'])
            return False, 'Código inválido o expirado.'

        otp.usado = True
        otp.used_at = timezone.now()
        otp.save(update_fields=['intentos', 'usado', 'used_at'])
        return True, None


class RefreshTokenClienteApp(models.Model):
    """
    Refresh token opaco (UUID) de la app de clientes, con rotación y detección de
    reúso. Calcado de `RefreshTokenDesktop` (models_sync.py) pero ligado a
    `CuentaClienteApp` en vez de `DispositivoAutorizado`+`Usuario`. Da paridad de
    seguridad con el desktop sin acoplar el blacklist de SimpleJWT al user model.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta = models.ForeignKey(
        CuentaClienteApp,
        on_delete=models.CASCADE,
        related_name='refresh_tokens',
    )

    token_hash = models.CharField(max_length=128, unique=True)
    familia_id = models.UUIDField(
        default=uuid.uuid4,
        db_index=True,
        help_text='Todos los tokens de una misma sesión comparten familia',
    )

    revocado = models.BooleanField(default=False)
    utilizado = models.BooleanField(
        default=False,
        help_text='True si ya fue usado para generar un nuevo token',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = 'Refresh Token Cliente (App)'
        verbose_name_plural = 'Refresh Tokens Cliente (App)'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['familia_id']),
            models.Index(fields=['token_hash']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        estado = 'Revocado' if self.revocado else ('Usado' if self.utilizado else 'Activo')
        return f"RefreshTokenCliente {self.id} - {estado}"

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def crear_token(cls, cuenta, *, dias_expiracion=7, ip=None, user_agent=None,
                    familia_id=None):
        """Crea un refresh token nuevo. Devuelve (obj, token_plano)."""
        token_plano = str(uuid.uuid4())
        obj = cls.objects.create(
            cuenta=cuenta,
            token_hash=cls.hash_token(token_plano),
            familia_id=familia_id or uuid.uuid4(),
            expires_at=timezone.now() + timezone.timedelta(days=dias_expiracion),
            ip_address=ip,
            user_agent=user_agent,
        )
        return obj, token_plano

    @classmethod
    def validar_y_rotar(cls, token_plano, *, ip=None):
        """
        Valida un refresh token y emite uno nuevo (rotación). Ante reúso de un
        token ya usado, revoca toda la familia.
        Devuelve (nuevo_obj, nuevo_token_plano, error_mensaje).
        """
        token_hash = cls.hash_token(token_plano)
        try:
            rt = cls.objects.select_related('cuenta', 'cuenta__cliente').get(
                token_hash=token_hash
            )
        except cls.DoesNotExist:
            return None, None, 'Token inválido'

        if rt.revocado:
            return None, None, 'Token revocado'
        if rt.expires_at < timezone.now():
            return None, None, 'Token expirado'
        if not rt.cuenta.is_active:
            return None, None, 'Cuenta inhabilitada'

        # Detección de reúso: si ya se usó, alguien lo robó → revocar familia.
        if rt.utilizado:
            cls.objects.filter(familia_id=rt.familia_id).update(revocado=True)
            return None, None, 'Detección de reutilización de token - sesión invalidada'

        rt.utilizado = True
        rt.used_at = timezone.now()
        rt.save(update_fields=['utilizado', 'used_at'])

        nuevo, nuevo_plano = cls.crear_token(
            rt.cuenta, familia_id=rt.familia_id, ip=ip or rt.ip_address,
            user_agent=rt.user_agent,
        )
        return nuevo, nuevo_plano, None

    def revocar_familia(self):
        """Revoca todos los tokens de esta familia (logout / compromiso)."""
        RefreshTokenClienteApp.objects.filter(familia_id=self.familia_id).update(revocado=True)
