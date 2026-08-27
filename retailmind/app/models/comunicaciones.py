"""
Bitácora de correo saliente del ERP.

Hasta ahora cada módulo mandaba su correo con `EmailMultiAlternatives` y no
guardaba nada: si el mensaje no salía, el rastro quedaba solo en
`logs/errors.log`. En agosto-2026 una cuenta impaga del relay dejó mudo al
sistema completo (OTP de login, recuperación de contraseña, gift cards,
cotizaciones y requerimientos) y nadie se enteró hasta que un usuario reclamó.

`EnvioCorreo` es la fila que responde "¿salió, llegó, lo abrieron, rebotó?"
para cualquier módulo. `GiftCard` ya tiene sus propios campos `correo_*` (que
siguen funcionando); la idea es que los módulos nuevos usen esta tabla y que
gift cards migre después.

El `token` cumple tres funciones a la vez:
  - identifica el píxel de apertura   → /app/c/a/<token>.png
  - identifica el enlace del proveedor → /app/c/r/<token>/
  - viaja en el Reply-To con plus-addressing (buzon+<token>@dominio) para
    poder pegar la respuesta en la ficha correcta.
"""
import uuid

from django.db import models
from django.utils import timezone
from django.conf import settings


# ========== CONSTANTES ==========

# Qué módulo originó el correo. Sirve para que UN solo webhook sepa a quién
# avisarle el evento sin tener que adivinar por el asunto.
MODULO_CORREO_CHOICES = [
    ('REQUERIMIENTO', 'Requerimiento a proveedor'),
    ('GIFTCARD', 'Gift card'),
    ('COTIZACION', 'Cotización'),
    ('OTP', 'Código de verificación'),
    ('PASSWORD', 'Recuperación de contraseña'),
    ('OTRO', 'Otro'),
]

# Escalera de evidencia, de lo que solo sabemos nosotros a lo que prueba algo:
#   ENVIADO    el relay aceptó el mensaje (no dice nada del destinatario)
#   ENTREGADO  el servidor del destinatario lo aceptó   → verificable
#   ABIERTO    se cargaron las imágenes                 → INDICATIVO, no prueba
#   CLICK      alguien hizo clic en el enlace           → evidencia fuerte
#   RESPONDIDO contestó                                 → prueba
ESTADO_ENVIO_CORREO_CHOICES = [
    ('ENVIADO', 'Enviado (aceptado por el servidor)'),
    ('ENTREGADO', 'Entregado en el buzón'),
    ('ABIERTO', 'Abierto por el destinatario'),
    ('CLICK', 'Hizo clic en el enlace'),
    ('RESPONDIDO', 'Respondió'),
    ('REBOTADO', 'Rebotado (no llegó)'),
    ('SPAM', 'Marcado como spam'),
    ('FALLIDO', 'Falló el envío'),
]

# Los eventos del proveedor NO llegan en orden garantizado: un `delivered`
# demorado no puede pisar un `opened` que ya entró. Mismo criterio que
# `_PRIORIDAD_ESTADO_CORREO` de gift cards, que ya está probado en producción.
PRIORIDAD_ESTADO_ENVIO = {
    'ENVIADO': 1,
    'ENTREGADO': 2,
    'ABIERTO': 3,
    'CLICK': 4,
    'RESPONDIDO': 5,
    # Los problemas mandan sobre todo lo demás: si rebotó, rebotó, aunque
    # después llegue un evento viejo diciendo que se abrió.
    'SPAM': 8,
    'REBOTADO': 9,
    'FALLIDO': 9,
}

# Estados en los que el correo NO llegó a destino y hay que actuar.
ESTADOS_ENVIO_PROBLEMA = ('REBOTADO', 'SPAM', 'FALLIDO')

# Estados que confirman que el mensaje llegó al buzón del destinatario.
ESTADOS_ENVIO_OK = ('ENTREGADO', 'ABIERTO', 'CLICK', 'RESPONDIDO')


def nuevo_token_correo():
    """Token opaco del envío. uuid4 = 122 bits: no se adivina por fuerza bruta."""
    return uuid.uuid4().hex


# ========== MODELOS ==========

class EnvioCorreo(models.Model):
    """Un correo saliente y todo lo que se supo de él después de mandarlo."""

    token = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        default=nuevo_token_correo,
        help_text="Identificador público del envío (píxel, enlace y Reply-To)",
    )

    # === A QUÉ SE REFIERE ===
    modulo = models.CharField(
        max_length=20,
        choices=MODULO_CORREO_CHOICES,
        default='OTRO',
        db_index=True,
        help_text="Módulo que originó el correo",
    )
    objeto_id = models.IntegerField(
        blank=True,
        null=True,
        db_index=True,
        help_text="ID del objeto del módulo (ej. Requerimiento.id)",
    )

    # === EL MENSAJE ===
    destinatario = models.CharField(max_length=200, db_index=True)
    cc = models.CharField(max_length=500, blank=True, default='')
    reply_to = models.CharField(max_length=500, blank=True, default='')
    from_email = models.CharField(max_length=200, blank=True, default='')
    asunto = models.CharField(max_length=300, blank=True, default='')
    adjuntos = models.PositiveSmallIntegerField(
        default=0,
        help_text="Cantidad de archivos adjuntos que viajaron",
    )
    es_copia_control = models.BooleanField(
        default=False,
        db_index=True,
        help_text=("Copia interna de control, no el correo al destinatario "
                   "real. Se registra para detectar sus fallos, pero no es la "
                   "que cuenta para el seguimiento del caso."),
    )

    # === IDENTIFICADORES PARA CORRELACIONAR EVENTOS ===
    message_id = models.CharField(
        max_length=255, blank=True, default='', db_index=True,
        help_text="Message-ID que genera Python (cabecera del mensaje)",
    )
    proveedor_message_id = models.CharField(
        max_length=120, blank=True, default='', db_index=True,
        help_text=("ID que devuelve el relay en el 250 final "
                   "('Message queued as ...'): es el que viaja en los webhooks"),
    )

    # === CUÁNDO Y QUIÉN ===
    enviado_en = models.DateTimeField(blank=True, null=True, db_index=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='correos_enviados',
    )

    # === ESTADO DE ENTREGA ===
    estado = models.CharField(
        max_length=15,
        choices=ESTADO_ENVIO_CORREO_CHOICES,
        default='ENVIADO',
        db_index=True,
    )
    estado_en = models.DateTimeField(blank=True, null=True)
    estado_detalle = models.CharField(max_length=255, blank=True, default='')
    error = models.TextField(
        blank=True, default='',
        help_text="Detalle del fallo cuando el estado es FALLIDO",
    )

    # === INTERACCIÓN DEL DESTINATARIO ===
    # Cada hito guarda su propia fecha: con solo `estado_en` la línea de tiempo
    # de la ficha mentiría (mostraría la hora del último evento en todos los
    # pasos anteriores).
    entregado_en = models.DateTimeField(blank=True, null=True)
    aperturas = models.PositiveIntegerField(default=0)
    abierto_en = models.DateTimeField(blank=True, null=True)
    clicks = models.PositiveIntegerField(default=0)
    click_en = models.DateTimeField(blank=True, null=True)
    ultima_ip = models.GenericIPAddressField(blank=True, null=True)
    ultimo_user_agent = models.CharField(max_length=300, blank=True, default='')

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'app_envio_correo'
        verbose_name = 'Envío de correo'
        verbose_name_plural = 'Envíos de correo'
        ordering = ['-creado_en']
        indexes = [
            models.Index(fields=['modulo', 'objeto_id']),
            models.Index(fields=['estado', 'enviado_en']),
        ]

    def __str__(self):
        return f'{self.get_modulo_display()} → {self.destinatario} ({self.estado})'

    # ---- comportamiento ----

    def registrar_estado(self, nuevo, detalle='', cuando=None):
        """Avanza el estado respetando la prioridad; devuelve True si cambió.

        Un evento fuera de orden (el `delivered` que llega después del
        `opened`) NO retrocede el estado.
        """
        if nuevo not in PRIORIDAD_ESTADO_ENVIO:
            return False
        if PRIORIDAD_ESTADO_ENVIO[nuevo] < PRIORIDAD_ESTADO_ENVIO.get(self.estado, 0):
            return False
        self.estado = nuevo
        self.estado_en = cuando or timezone.now()
        if detalle:
            self.estado_detalle = detalle[:255]
        return True

    @property
    def hubo_problema(self):
        return self.estado in ESTADOS_ENVIO_PROBLEMA

    @property
    def llego(self):
        """¿Hay confirmación de que entró al buzón del destinatario?"""
        return self.estado in ESTADOS_ENVIO_OK

    @property
    def dias_sin_respuesta(self):
        if not self.enviado_en or self.estado == 'RESPONDIDO':
            return None
        return (timezone.now() - self.enviado_en).days


class RespuestaCorreo(models.Model):
    """Respuesta del destinatario, capturada del buzón e imputada al envío."""

    envio = models.ForeignKey(
        EnvioCorreo,
        on_delete=models.CASCADE,
        related_name='respuestas',
    )
    remitente = models.CharField(max_length=200, db_index=True)
    asunto = models.CharField(max_length=300, blank=True, default='')
    cuerpo = models.TextField(blank=True, default='')
    recibido_en = models.DateTimeField(db_index=True)

    message_id = models.CharField(
        max_length=255, blank=True, default='', db_index=True,
        help_text="Message-ID de la respuesta (evita procesarla dos veces)",
    )
    in_reply_to = models.CharField(max_length=255, blank=True, default='')
    adjuntos = models.JSONField(
        default=list, blank=True,
        help_text="[{nombre, tipo, tamano}] de los archivos que traía",
    )

    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'app_respuesta_correo'
        verbose_name = 'Respuesta de correo'
        verbose_name_plural = 'Respuestas de correo'
        ordering = ['-recibido_en']
        constraints = [
            # El poller puede leer el mismo mensaje dos veces (reintento, o el
            # buzón marcado como no leído a mano): sin esto la respuesta se
            # duplicaba en la ficha.
            models.UniqueConstraint(
                fields=['envio', 'message_id'],
                condition=~models.Q(message_id=''),
                name='uniq_respuesta_por_message_id',
            ),
        ]

    def __str__(self):
        return f'Respuesta de {self.remitente} ({self.recibido_en:%d-%m-%Y %H:%M})'
