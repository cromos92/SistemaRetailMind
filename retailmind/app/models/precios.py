from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Sucursal
from .catalogo import Producto, Producto_Talla

class ParametroGlobal(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    valor_entero = models.IntegerField(default=0)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.nombre}: {self.valor_entero}"

ESTADO_CAMBIO_PRECIO_CHOICES = [
    ('PENDIENTE', 'Pendiente de Revisión'),
    ('REVISADO', 'Revisado'),
    ('APROBADO', 'Aprobado'),
    ('RECHAZADO', 'Rechazado'),
    ('APLICADO', 'Aplicado'),
    ('CANCELADO', 'Cancelado'),
]

TIPO_CAMBIO_PRECIO_CHOICES = [
    ('INDIVIDUAL', 'Cambio Individual'),
    ('MASIVO', 'Cambio Masivo'),
    ('SINCRONIZACION', 'Sincronización Multi-Sucursal'),
    ('RECOMENDACION', 'Por Recomendación del Sistema'),
]


class CambioPrecioPendiente(models.Model):
    """
    Modelo para almacenar cambios de precios pendientes de aprobación
    Permite workflow: Proponer → Revisar → Aprobar/Rechazar → Aplicar
    """
    # === RELACIONES ===
    producto_talla = models.ForeignKey(
        Producto_Talla,
        on_delete=models.CASCADE,
        related_name='cambios_precio_pendientes'
    )
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='cambios_precio_pendientes',
        help_text="Sucursal afectada por el cambio"
    )
    
    # === DATOS DEL CAMBIO ===
    precio_anterior = models.IntegerField(
        help_text="Precio antes del cambio"
    )
    precio_nuevo = models.IntegerField(
        help_text="Precio propuesto"
    )
    diferencia = models.IntegerField(
        help_text="Diferencia en pesos (nuevo - anterior)"
    )
    porcentaje_cambio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Porcentaje de cambio"
    )
    
    # === TIPO Y ESTADO ===
    tipo_cambio = models.CharField(
        max_length=20,
        choices=TIPO_CAMBIO_PRECIO_CHOICES,
        default='INDIVIDUAL'
    )
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CAMBIO_PRECIO_CHOICES,
        default='PENDIENTE'
    )
    
    # === JUSTIFICACIÓN ===
    motivo = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo del cambio de precio"
    )
    recomendacion_sistema = models.JSONField(
        blank=True,
        null=True,
        help_text="Datos de la recomendación del sistema (si aplica)"
    )
    
    # === USUARIOS INVOLUCRADOS ===
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='cambios_precio_creados',
        help_text="Usuario que propuso el cambio"
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cambios_precio_revisados',
        help_text="Usuario que revisó el cambio"
    )
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cambios_precio_aprobados',
        help_text="Usuario que aprobó/rechazó el cambio"
    )
    
    # === FECHAS ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_revision = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que fue revisado"
    )
    fecha_aprobacion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que fue aprobado/rechazado"
    )
    fecha_aplicacion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que se aplicó el cambio"
    )
    fecha_vencimiento = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha límite para aprobar/aplicar"
    )
    
    # === OBSERVACIONES ===
    observaciones_revision = models.TextField(
        blank=True,
        null=True,
        help_text="Observaciones al revisar"
    )
    observaciones_aprobacion = models.TextField(
        blank=True,
        null=True,
        help_text="Observaciones al aprobar/rechazar"
    )
    
    # === METADATA ===
    notificado = models.BooleanField(
        default=False,
        help_text="Si se notificó a la sucursal"
    )
    descartado = models.BooleanField(
        default=False,
        help_text="Si el usuario descartó/archivó este registro"
    )
    fecha_descarte = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que se descartó"
    )
    descartado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cambios_precio_descartados',
        help_text="Usuario que descartó el registro"
    )
    prioridad = models.CharField(
        max_length=10,
        choices=[
            ('BAJA', 'Baja'),
            ('MEDIA', 'Media'),
            ('ALTA', 'Alta'),
            ('URGENTE', 'Urgente'),
        ],
        default='MEDIA'
    )
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Cambio de Precio Pendiente'
        verbose_name_plural = 'Cambios de Precios Pendientes'
        indexes = [
            models.Index(fields=['estado', 'sucursal']),
            models.Index(fields=['fecha_creacion']),
        ]
    
    def __str__(self):
        return f"{self.producto_talla.sku} - {self.get_estado_display()} - {self.sucursal.alias}"
    
    @property
    def dias_pendiente(self):
        """Calcula cuántos días lleva pendiente"""
        if self.estado in ['APLICADO', 'CANCELADO', 'RECHAZADO']:
            return 0
        return (timezone.now() - self.fecha_creacion).days
    
    @property
    def esta_vencido(self):
        """Verifica si el cambio está vencido"""
        if not self.fecha_vencimiento:
            return False
        return timezone.now() > self.fecha_vencimiento and self.estado == 'PENDIENTE'
    
    @property
    def requiere_atencion(self):
        """Determina si requiere atención urgente"""
        return self.prioridad in ['ALTA', 'URGENTE'] or self.dias_pendiente > 7


class NotificacionCambioPrecio(models.Model):
    """
    Modelo para notificaciones de cambios de precios
    """
    cambio_precio = models.ForeignKey(
        CambioPrecioPendiente,
        on_delete=models.CASCADE,
        related_name='notificaciones'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notificaciones_precio'
    )
    
    # === DATOS DE LA NOTIFICACIÓN ===
    tipo = models.CharField(
        max_length=20,
        choices=[
            ('NUEVA', 'Nuevo Cambio Propuesto'),
            ('REVISION', 'Cambio Revisado'),
            ('APROBACION', 'Cambio Aprobado'),
            ('RECHAZO', 'Cambio Rechazado'),
            ('APLICACION', 'Cambio Aplicado'),
            ('VENCIMIENTO', 'Cambio Próximo a Vencer'),
        ]
    )
    mensaje = models.TextField()
    
    # === ESTADO ===
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_lectura = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Notificación de Cambio de Precio'
        verbose_name_plural = 'Notificaciones de Cambios de Precios'
    
    def __str__(self):
        return f"{self.usuario.username} - {self.get_tipo_display()}"
    
    def marcar_leida(self):
        """Marca la notificación como leída"""
        if not self.leida:
            self.leida = True
            self.fecha_lectura = timezone.now()
            self.save()


class HistorialCambioPrecio(models.Model):
    """
    Modelo para registrar todos los cambios de precio
    Auditoría completa de quién, cuándo y por qué cambió cada precio
    """
    # === RELACIONES ===
    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name='historial_precios'
    )
    
    # === DATOS DEL CAMBIO ===
    precio_anterior = models.IntegerField(
        help_text="Precio antes del cambio"
    )
    precio_nuevo = models.IntegerField(
        help_text="Precio después del cambio"
    )
    diferencia = models.IntegerField(
        help_text="Diferencia en pesos"
    )
    porcentaje_cambio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Porcentaje de cambio"
    )
    
    # === CONTEXTO ===
    motivo = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo del cambio"
    )
    tipo_cambio = models.CharField(
        max_length=50,
        choices=[
            ('MANUAL', 'Cambio Manual'),
            ('RECOMENDACION', 'Por Recomendación'),
            ('MASIVO', 'Cambio Masivo'),
            ('SINCRONIZACION', 'Sincronización'),
            ('APROBACION', 'Por Aprobación'),
        ],
        default='MANUAL'
    )
    
    # === USUARIO Y FECHA ===
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='cambios_precio_realizados',
        help_text="Usuario que realizó el cambio"
    )
    fecha_cambio = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        help_text="IP desde donde se realizó el cambio"
    )
    
    # === METADATA ===
    tallas_afectadas = models.IntegerField(
        default=0,
        help_text="Cantidad de tallas actualizadas"
    )
    lotes_afectados = models.IntegerField(
        default=0,
        help_text="Cantidad de lotes actualizados"
    )
    
    class Meta:
        ordering = ['-fecha_cambio']
        verbose_name = 'Historial de Cambio de Precio'
        verbose_name_plural = 'Historial de Cambios de Precios'
        indexes = [
            models.Index(fields=['producto', '-fecha_cambio']),
            models.Index(fields=['usuario', '-fecha_cambio']),
        ]
    
    def __str__(self):
        return f"{self.producto.articulo} - {self.precio_anterior} → {self.precio_nuevo} - {self.fecha_cambio.strftime('%d/%m/%Y')}"
    
    @property
    def hace_cuanto(self):
        """Retorna hace cuánto tiempo fue el cambio"""
        from django.utils.timesince import timesince
        return timesince(self.fecha_cambio)
