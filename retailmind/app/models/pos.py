from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Sucursal
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
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            ultimo_numero = TransaccionPOS.objects.filter(
                configuracion_pos=self.configuracion_pos,
                fecha_inicio__date=timezone.now().date()
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
