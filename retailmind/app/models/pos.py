from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal
from .ventas import Ticket, TicketDetallePago

# ========== MÓDULO POS TRANSBANK ==========

TIPO_POS_CHOICES = [
    ('VERIFONE_VX520', 'Verifone VX520'),
    ('INGENICO_3500', 'Ingenico 3500'),
    ('INGENICO_DESK', 'Ingenico DESK'),
    ('OTRO', 'Otro'),
]

ESTADO_TRANSACCION_POS_CHOICES = [
    ('INICIADA', 'Iniciada'),
    ('ESPERANDO_TARJETA', 'Esperando Tarjeta'),
    ('PROCESANDO', 'Procesando'),
    ('APROBADA', 'Aprobada'),
    ('RECHAZADA', 'Rechazada'),
    ('ANULADA', 'Anulada'),
    ('ERROR', 'Error'),
    ('TIMEOUT', 'Timeout'),
]

TIPO_TARJETA_CHOICES = [
    ('DEBITO', 'Débito'),
    ('CREDITO', 'Crédito'),
    ('PREPAGO', 'Prepago'),
    ('DESCONOCIDO', 'Desconocido'),
]

class ConfiguracionPOS(models.Model):
    """
    Configuración de terminales POS Transbank por sucursal
    """
    # === RELACIONES ===
    sucursal = models.ForeignKey(
        Sucursal, 
        on_delete=models.CASCADE, 
        related_name='configuraciones_pos'
    )
    
    # === DATOS DEL TERMINAL ===
    nombre = models.CharField(max_length=100, help_text="Nombre identificativo del terminal")
    tipo_pos = models.CharField(max_length=20, choices=TIPO_POS_CHOICES)
    puerto_conexion = models.CharField(
        max_length=20, 
        help_text="Puerto de conexión (COM1, /dev/ttyUSB0, etc.)"
    )
    velocidad_conexion = models.IntegerField(
        default=115200, 
        help_text="Velocidad de conexión en bps"
    )
    
    # === CONFIGURACIÓN ===
    activo = models.BooleanField(default=True)
    es_principal = models.BooleanField(
        default=False, 
        help_text="Terminal principal para esta sucursal"
    )
    timeout_conexion = models.IntegerField(
        default=30, 
        help_text="Timeout de conexión en segundos"
    )
    
    # === INFORMACIÓN TÉCNICA ===
    numero_serie = models.CharField(max_length=50, blank=True, null=True)
    version_firmware = models.CharField(max_length=20, blank=True, null=True)
    ultima_conexion = models.DateTimeField(null=True, blank=True)
    estado_conexion = models.CharField(
        max_length=20, 
        choices=[
            ('CONECTADO', 'Conectado'),
            ('DESCONECTADO', 'Desconectado'),
            ('DETECTADO', 'Detectado Automáticamente'),
            ('ERROR', 'Error'),
            ('NO_PROBADO', 'No Probado'),
        ],
        default='NO_PROBADO'
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['sucursal', 'nombre']
        unique_together = ['sucursal', 'nombre']
        verbose_name = 'Configuración POS'
        verbose_name_plural = 'Configuraciones POS'
        indexes = [
            models.Index(fields=['sucursal', 'activo']),
            models.Index(fields=['tipo_pos', 'activo']),
        ]
    
    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_pos_display()}) - {self.sucursal.alias}"
    
    def save(self, *args, **kwargs):
        # Si se marca como principal, desmarcar otros principales en la misma sucursal
        if self.es_principal:
            ConfiguracionPOS.objects.filter(
                sucursal=self.sucursal, 
                es_principal=True
            ).exclude(id=self.id).update(es_principal=False)
        
        super().save(*args, **kwargs)


class TransaccionPOS(models.Model):
    """
    Registro de transacciones POS Transbank para auditoría y seguimiento
    """
    # === RELACIONES ===
    configuracion_pos = models.ForeignKey(
        ConfiguracionPOS, 
        on_delete=models.CASCADE, 
        related_name='transacciones'
    )
    ticket = models.ForeignKey(
        Ticket, 
        on_delete=models.CASCADE, 
        related_name='transacciones_pos',
        null=True, blank=True
    )
    detalle_pago = models.ForeignKey(
        TicketDetallePago, 
        on_delete=models.CASCADE, 
        related_name='transaccion_pos',
        null=True, blank=True
    )
    
    # === DATOS DE LA TRANSACCIÓN ===
    ticket_pos = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Ticket único generado para la transacción POS"
    )
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    tipo_transaccion = models.CharField(
        max_length=20, 
        choices=[
            ('VENTA', 'Venta'),
            ('ANULACION', 'Anulación'),
            ('DEVOLUCION', 'Devolución'),
        ],
        default='VENTA'
    )
    
    # === ESTADO DE LA TRANSACCIÓN ===
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_TRANSACCION_POS_CHOICES, 
        default='INICIADA'
    )
    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_completada = models.DateTimeField(null=True, blank=True)
    
    # === RESPUESTA DEL POS ===
    codigo_respuesta = models.CharField(max_length=10, blank=True, null=True)
    mensaje_respuesta = models.CharField(max_length=200, blank=True, null=True)
    codigo_autorizacion = models.CharField(max_length=20, blank=True, null=True)
    
    # === DATOS DE LA TARJETA ===
    tipo_tarjeta = models.CharField(
        max_length=20, 
        choices=TIPO_TARJETA_CHOICES, 
        blank=True, null=True
    )
    ultimos_4_digitos = models.CharField(max_length=4, blank=True, null=True)
    nombre_tarjeta = models.CharField(max_length=50, blank=True, null=True)  # VISA, MASTERCARD, etc.
    
    # === DATOS TÉCNICOS ===
    numero_operacion = models.CharField(max_length=20, blank=True, null=True)
    numero_cuotas = models.IntegerField(default=1)
    codigo_comercio = models.CharField(max_length=20, blank=True, null=True)
    terminal_id = models.CharField(max_length=20, blank=True, null=True)
    
    # === DATOS DE AUDITORÍA ===
    ip_origen = models.GenericIPAddressField(null=True, blank=True)
    usuario_operador = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='transacciones_pos_operadas'
    )
    
    # === OBSERVACIONES Y ERRORES ===
    observaciones = models.TextField(blank=True, null=True)
    error_detalle = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_inicio']
        verbose_name = 'Transacción POS'
        verbose_name_plural = 'Transacciones POS'
        indexes = [
            models.Index(fields=['ticket_pos']),
            models.Index(fields=['configuracion_pos', 'fecha_inicio']),
            models.Index(fields=['estado', 'fecha_inicio']),
            models.Index(fields=['codigo_autorizacion']),
            models.Index(fields=['ticket', 'estado']),
        ]
    
    def __str__(self):
        return f"POS {self.ticket_pos} - {self.get_estado_display()} - ${self.monto:,}"
    
    def save(self, *args, **kwargs):
        # Generar ticket_pos si no existe
        if not self.ticket_pos:
            from django.utils import timezone
            timestamp = timezone.localtime().strftime('%Y%m%d%H%M%S')
            ultimo_numero = TransaccionPOS.objects.filter(
                configuracion_pos=self.configuracion_pos,
                fecha_inicio__date=timezone.localdate()
            ).count()
            self.ticket_pos = f"POS-{self.configuracion_pos.sucursal.id}-{timestamp}-{ultimo_numero + 1:03d}"
        
        # Actualizar fecha_completada si el estado cambió a completado
        if self.estado in ['APROBADA', 'RECHAZADA', 'ANULADA', 'ERROR'] and not self.fecha_completada:
            from django.utils import timezone
            self.fecha_completada = timezone.now()
        
        super().save(*args, **kwargs)
    
    @property
    def duracion_transaccion(self):
        """Duración de la transacción en segundos"""
        if self.fecha_completada:
            return (self.fecha_completada - self.fecha_inicio).total_seconds()
        return None
    
    @property
    def es_exitosa(self):
        """Verifica si la transacción fue exitosa"""
        return self.estado == 'APROBADA' and self.codigo_autorizacion
    
    @property
    def puede_anular(self):
        """Verifica si la transacción puede ser anulada"""
        return (
            self.estado == 'APROBADA' and 
            self.codigo_autorizacion and 
            self.fecha_completada and
            (timezone.now() - self.fecha_completada).days == 0  # Solo el mismo día
        )


class LogPOS(models.Model):
    """
    Log detallado de comunicación con terminales POS para debugging
    """
    # === RELACIONES ===
    configuracion_pos = models.ForeignKey(
        ConfiguracionPOS, 
        on_delete=models.CASCADE, 
        related_name='logs'
    )
    transaccion_pos = models.ForeignKey(
        TransaccionPOS, 
        on_delete=models.CASCADE, 
        related_name='logs',
        null=True, blank=True
    )
    
    # === DATOS DEL LOG ===
    tipo_evento = models.CharField(
        max_length=20, 
        choices=[
            ('CONEXION', 'Conexión'),
            ('DESCONEXION', 'Desconexión'),
            ('COMANDO_ENVIADO', 'Comando Enviado'),
            ('RESPUESTA_RECIBIDA', 'Respuesta Recibida'),
            ('ERROR', 'Error'),
            ('TIMEOUT', 'Timeout'),
            ('INFO', 'Información'),
        ]
    )
    mensaje = models.TextField()
    datos_tecnicos = models.JSONField(blank=True, null=True)  # Para datos estructurados
    
    # === METADATA ===
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Log POS'
        verbose_name_plural = 'Logs POS'
        indexes = [
            models.Index(fields=['configuracion_pos', 'timestamp']),
            models.Index(fields=['tipo_evento', 'timestamp']),
            models.Index(fields=['transaccion_pos', 'timestamp']),
        ]
    
    def __str__(self):
        return f"{self.get_tipo_evento_display()} - {self.configuracion_pos.nombre} - {self.timestamp.strftime('%H:%M:%S')}"


# ========== MÓDULO MERCADO PAGO PRESENCIAL (QR / POINT vía API) ==========
# Modelos propios, separados de ConfiguracionPOS/TransaccionPOS (que son
# serial-céntricos de Transbank). El cobro MP es 100% server-side: Django
# habla con la API de MP, el navegador solo hace polling a Django.

MODO_MERCADOPAGO_CHOICES = [
    ('QR', 'QR dinámico'),
    ('POINT', 'Terminal Point'),
    ('AMBOS', 'QR y Point'),
]

CANAL_MP_CHOICES = [
    ('QR', 'QR dinámico'),
    ('POINT', 'Terminal Point'),
]

TIPO_TRANSACCION_MP_CHOICES = [
    ('VENTA', 'Venta'),
    ('DEVOLUCION', 'Devolución'),
]

ESTADO_TRANSACCION_MP_CHOICES = [
    ('CREADA', 'Creada'),
    ('PENDIENTE', 'Pendiente'),
    ('APROBADA', 'Aprobada'),
    ('RECHAZADA', 'Rechazada'),
    ('CANCELADA', 'Cancelada'),
    ('EXPIRADA', 'Expirada'),
    ('DEVUELTA', 'Devuelta'),
    ('CONTRACARGO', 'Contracargo'),
    ('ERROR', 'Error'),
]

# Estados desde los que ya no hay transición posible por webhook/polling
ESTADOS_FINALES_MP = {'RECHAZADA', 'CANCELADA', 'EXPIRADA', 'DEVUELTA', 'ERROR'}

ESTADO_RETIRO_MP_CHOICES = [
    ('PENDIENTE_CONCILIAR', 'Pendiente de conciliar'),
    ('CONCILIADO', 'Conciliado'),
    ('CON_DIFERENCIA', 'Con diferencia'),
]


class MercadoPagoCuenta(models.Model):
    """Cuenta Mercado Pago de una empresa/RUT (una por cadena).

    El access token y el webhook secret se guardan en BD **CIFRADOS en
    reposo** (Fernet — ver services/mp_credenciales.py; la clave vive en el
    entorno, nunca en la BD). Usar SIEMPRE ``set_access_token`` /
    ``set_webhook_secret`` para escribir y los ``get_*`` para leer — jamás
    asignar los campos ``*_cifrado`` a mano con texto plano.
    """
    empresa = models.OneToOneField(
        Empresa, on_delete=models.CASCADE, related_name='cuenta_mercadopago'
    )
    mp_user_id = models.CharField(max_length=30, blank=True, help_text="user_id (collector) de la cuenta MP")
    access_token_cifrado = models.TextField(blank=True, help_text="Access token CIFRADO — usar set_access_token()")
    webhook_secret_cifrado = models.TextField(blank=True, help_text="Secret de firma de webhooks CIFRADO — usar set_webhook_secret()")
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cuenta Mercado Pago'
        verbose_name_plural = 'Cuentas Mercado Pago'

    def __str__(self):
        return f"Cuenta MP {self.empresa.rut if self.empresa_id else '?'}"

    # --- credenciales (cifradas en reposo) ---
    def set_access_token(self, valor):
        from app.services.mp_credenciales import cifrar
        self.access_token_cifrado = cifrar((valor or '').strip())

    def get_access_token(self):
        from app.services.mp_credenciales import descifrar
        return descifrar(self.access_token_cifrado)

    def set_webhook_secret(self, valor):
        from app.services.mp_credenciales import cifrar
        self.webhook_secret_cifrado = cifrar((valor or '').strip())

    def get_webhook_secret(self):
        from app.services.mp_credenciales import descifrar
        return descifrar(self.webhook_secret_cifrado)


class MercadoPagoConfig(models.Model):
    """Configuración de cobro Mercado Pago por sucursal.

    Credenciales: se resuelven desde ``MercadoPagoCuenta`` (BD, cifradas) —
    por el FK ``cuenta`` o, si es NULL, por la empresa de la sucursal. Los
    campos ``token_env``/``webhook_secret_env`` quedan como FALLBACK legacy
    (nombre de env var) para entornos sin cuenta cargada. Por defecto todas
    las máquinas de la sucursal comparten esta config (una orden por cobro
    no colisiona entre máquinas).
    """
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.CASCADE, related_name='configuraciones_mercadopago'
    )
    cuenta = models.ForeignKey(
        MercadoPagoCuenta, on_delete=models.PROTECT, null=True, blank=True,
        related_name='configuraciones',
        help_text="Cuenta MP a usar; si es NULL se resuelve por la empresa de la sucursal",
    )
    nombre = models.CharField(max_length=100, default='Caja principal')
    habilitado = models.BooleanField(default=False)
    modo = models.CharField(max_length=10, choices=MODO_MERCADOPAGO_CHOICES, default='QR')
    es_principal = models.BooleanField(default=True)

    # Identificadores en Mercado Pago
    mp_user_id = models.CharField(max_length=30, blank=True, help_text="user_id (collector) de la cuenta MP")
    external_store_id = models.CharField(max_length=60, blank=True)
    store_id = models.CharField(max_length=60, blank=True)
    external_pos_id = models.CharField(max_length=60, blank=True)
    pos_id = models.CharField(max_length=60, blank=True)
    device_id = models.CharField(max_length=60, blank=True, help_text="Solo Point")

    # FALLBACK legacy: NOMBRES de variables de entorno (se usan solo si no
    # hay MercadoPagoCuenta resoluble para la sucursal)
    token_env = models.CharField(max_length=100, blank=True, help_text="Fallback: nombre de la env var con el access token")
    webhook_secret_env = models.CharField(max_length=100, blank=True, help_text="Fallback: nombre de la env var con el secret de firma")

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['sucursal', 'nombre']
        verbose_name = 'Configuración Mercado Pago'
        verbose_name_plural = 'Configuraciones Mercado Pago'

    def __str__(self):
        return f"MP {self.sucursal_id} - {self.nombre} ({self.modo})"


class RetiroMercadoPago(models.Model):
    """Transferencia de MP a la cuenta bancaria (retiro). Unidad de
    conciliación 1:1 con los abonos de la cartola; sus transacciones
    referenciadas cuelgan vía TransaccionMercadoPago.retiro."""
    config = models.ForeignKey(
        MercadoPagoConfig, on_delete=models.PROTECT, related_name='retiros'
    )
    withdrawal_id = models.CharField(max_length=60, unique=True)
    fecha = models.DateField()
    monto = models.IntegerField(help_text="Monto CLP transferido al banco")
    estado = models.CharField(
        max_length=20, choices=ESTADO_RETIRO_MP_CHOICES, default='PENDIENTE_CONCILIAR'
    )
    visto_en_cartola = models.BooleanField(default=False)
    detalle_diferencia = models.TextField(blank=True)
    raw_reporte = models.JSONField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Retiro Mercado Pago'
        verbose_name_plural = 'Retiros Mercado Pago'

    def __str__(self):
        return f"Retiro MP {self.withdrawal_id} - ${self.monto:,}".replace(',', '.')


class TransaccionMercadoPago(models.Model):
    """Log/estado de cada cobro o devolución Mercado Pago presencial.

    Es la fuente de verdad server-side: el guard de registrar_pagos_ticket
    exige una fila APROBADA no consumida antes de aceptar un pago MP_* de
    origen POS_INTEGRADO. Buscar tickets SIEMPRE por (sucursal, correlativo).
    """
    config = models.ForeignKey(
        MercadoPagoConfig, on_delete=models.PROTECT, related_name='transacciones'
    )
    sucursal = models.ForeignKey(
        Sucursal, on_delete=models.CASCADE, related_name='transacciones_mercadopago'
    )
    ticket = models.ForeignKey(
        Ticket, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transacciones_mercadopago'
    )
    detalle_pago = models.ForeignKey(
        TicketDetallePago, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transacciones_mercadopago'
    )
    correlativo_ticket = models.CharField(max_length=50, blank=True)

    tipo = models.CharField(max_length=12, choices=TIPO_TRANSACCION_MP_CHOICES, default='VENTA')
    canal = models.CharField(max_length=10, choices=CANAL_MP_CHOICES, default='QR')
    transaccion_origen = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='devoluciones', help_text="Venta original (solo devoluciones)"
    )

    external_reference = models.CharField(max_length=80, unique=True)
    order_id = models.CharField(max_length=60, blank=True)
    payment_id = models.CharField(max_length=60, blank=True)

    monto = models.IntegerField(help_text="Monto CLP")
    monto_neto = models.IntegerField(null=True, blank=True, help_text="Neto tras comisión MP")
    fee_mp = models.IntegerField(null=True, blank=True)
    installments = models.IntegerField(default=1)

    estado = models.CharField(max_length=15, choices=ESTADO_TRANSACCION_MP_CHOICES, default='CREADA')
    estado_detalle = models.CharField(max_length=120, blank=True)
    metodo_pago_mp = models.CharField(max_length=40, blank=True, help_text="debit_card / credit_card / account_money…")
    ultimos_4_digitos = models.CharField(max_length=4, blank=True)
    codigo_autorizacion = models.CharField(max_length=30, blank=True)

    money_release_date = models.DateTimeField(null=True, blank=True)
    consumida = models.BooleanField(default=False, help_text="Ya respalda un TicketDetallePago")
    retiro = models.ForeignKey(
        RetiroMercadoPago, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transacciones'
    )

    raw_response = models.JSONField(null=True, blank=True)
    webhook_recibido_en = models.DateTimeField(null=True, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transacciones_mercadopago'
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Transacción Mercado Pago'
        verbose_name_plural = 'Transacciones Mercado Pago'
        indexes = [
            models.Index(fields=['sucursal', 'correlativo_ticket'], name='mp_trx_suc_corr_idx'),
            models.Index(fields=['estado', 'creado_en'], name='mp_trx_estado_idx'),
        ]

    def __str__(self):
        return f"MP {self.tipo} {self.external_reference} - {self.estado}"

    @property
    def es_aprobada(self):
        return self.estado == 'APROBADA'

    @property
    def es_final(self):
        return self.estado in ESTADOS_FINALES_MP


class MercadoPagoWebhookEvento(models.Model):
    """Event log de notificaciones webhook de MP. Idempotencia por
    x-request-id: una re-entrega ya procesada responde 200 sin reprocesar."""
    request_id = models.CharField(max_length=80, unique=True)
    topic = models.CharField(max_length=40, blank=True)
    data_id = models.CharField(max_length=60, blank=True)
    firma_valida = models.BooleanField(default=False)
    procesado = models.BooleanField(default=False)
    payload = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    recibido_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recibido_en']
        verbose_name = 'Evento Webhook Mercado Pago'
        verbose_name_plural = 'Eventos Webhook Mercado Pago'

    def __str__(self):
        return f"WH {self.topic} {self.data_id} ({'ok' if self.procesado else 'pendiente'})"
