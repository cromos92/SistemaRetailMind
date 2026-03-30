from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal
from .catalogo import Producto_Talla

TIPO_REQUERIMIENTO_CHOICES = [
    ('GARANTIA', 'Garantia Producto'),
    ('DEVOLUCION', 'Procedimiento Devolucion Proveedor'),
    ('RECLAMO', 'Reclamo'),
    ('CONSULTA', 'Consulta Tecnica'),
    ('OTROS', 'Otros'),
]

ESTADO_REQUERIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('ESPERANDO_RESPUESTA', 'Esperando Respuesta Proveedor'),
    ('APROBADO', 'Aprobado por Proveedor'),
    ('RECHAZADO', 'Rechazado por Proveedor'),
    ('CANCELADO', 'Cancelado'),
]

class Requerimiento(models.Model):
    """
    Modelo para gestionar requerimientos de garantías, devoluciones y reclamos
    desde cualquier sucursal
    """
    # === INFORMACIÓN BÁSICA ===
    numero_requerimiento = models.CharField(
        max_length=50,
        unique=True,
        help_text="Número único de requerimiento (autogenerado)"
    )
    tipo = models.CharField(
        max_length=20,
        choices=TIPO_REQUERIMIENTO_CHOICES,
        default='GARANTIA'
    )
    estado = models.CharField(
        max_length=30,
        choices=ESTADO_REQUERIMIENTO_CHOICES,
        default='PENDIENTE'
    )
    
    # === SUCURSAL Y USUARIO ===
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='requerimientos',
        help_text="Sucursal que genera el requerimiento"
    )
    usuario_creador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='requerimientos_creados',
        help_text="Usuario que creó el requerimiento"
    )
    usuario_gestor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_gestionados',
        help_text="Usuario que gestiona/administra el requerimiento"
    )
    
    # === INFORMACIÓN DEL PRODUCTO ===
    producto_talla = models.ForeignKey(
        Producto_Talla,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos'
    )
    sku = models.CharField(
        max_length=100,
        help_text="SKU del producto"
    )
    nombre_producto = models.CharField(
        max_length=255,
        help_text="Nombre del producto"
    )
    
    # === DOCUMENTO DE VENTA ===
    numero_boleta = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Número de boleta o documento de venta"
    )
    tipo_documento = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Tipo de documento (Boleta, Factura, etc.)"
    )
    fecha_compra = models.DateField(
        blank=True,
        null=True,
        help_text="Fecha de compra del producto"
    )
    
    # === INFORMACIÓN DEL CLIENTE ===
    cliente_rut = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="RUT del cliente"
    )
    cliente_nombre = models.CharField(
        max_length=255,
        help_text="Nombre completo del cliente"
    )
    cliente_telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Teléfono de contacto"
    )
    cliente_email = models.EmailField(
        blank=True,
        null=True,
        help_text="Email del cliente"
    )
    
    # === DESCRIPCIÓN DEL REQUERIMIENTO ===
    motivo = models.TextField(
        help_text="Descripción del motivo del requerimiento"
    )
    descripcion_problema = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción detallada del problema"
    )
    
    # === PROVEEDOR Y RESPUESTA ===
    proveedor = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_como_proveedor',
        help_text="Proveedor al que se envía el requerimiento"
    )
    correo_enviado_proveedor = models.BooleanField(
        default=False,
        help_text="Indica si se envió correo al proveedor"
    )
    fecha_envio_proveedor = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha en que se envió al proveedor"
    )
    correo_proveedor_destino = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Email al que se envió el requerimiento"
    )
    intentos_envio = models.IntegerField(
        default=0,
        help_text="Número de veces que se ha enviado al proveedor"
    )
    ultimo_recordatorio = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha del último recordatorio enviado"
    )
    respuesta_proveedor = models.TextField(
        blank=True,
        null=True,
        help_text="Respuesta del proveedor"
    )
    fecha_respuesta_proveedor = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha de respuesta del proveedor"
    )
    decision_proveedor = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ('APROBADO', 'Aprobado'),
            ('RECHAZADO', 'Rechazado'),
            ('PARCIAL', 'Aprobado Parcial'),
        ],
        help_text="Decisión del proveedor"
    )
    asignado_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_asignados',
        help_text="Usuario responsable de gestionar el requerimiento"
    )
    
    # === RESOLUCIÓN ===
    resolucion = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción de la resolución final"
    )
    motivo_resolucion = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo visible al usuario de por qué se aprobó o rechazó"
    )
    fecha_resolucion = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha de resolución del requerimiento"
    )
    
    # === FECHAS ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    
    # === PRIORIDAD ===
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
        verbose_name = 'Requerimiento'
        verbose_name_plural = 'Requerimientos'
        indexes = [
            models.Index(fields=['numero_requerimiento']),
            models.Index(fields=['estado', '-fecha_creacion']),
            models.Index(fields=['sucursal', '-fecha_creacion']),
            models.Index(fields=['sku']),
        ]
    
    def __str__(self):
        return f"{self.numero_requerimiento} - {self.get_tipo_display()} - {self.get_estado_display()}"
    
    def save(self, *args, **kwargs):
        """Genera número de requerimiento automáticamente"""
        if not self.numero_requerimiento:
            # Generar número correlativo: REQ-YYYYMMDD-XXXX
            from django.utils import timezone
            fecha_actual = timezone.now()
            prefijo = f"REQ-{fecha_actual.strftime('%Y%m%d')}"
            
            # Obtener el último número del día
            ultimo = Requerimiento.objects.filter(
                numero_requerimiento__startswith=prefijo
            ).order_by('-numero_requerimiento').first()
            
            if ultimo:
                # Extraer el número y sumar 1
                ultimo_num = int(ultimo.numero_requerimiento.split('-')[-1])
                nuevo_num = ultimo_num + 1
            else:
                nuevo_num = 1
            
            self.numero_requerimiento = f"{prefijo}-{nuevo_num:04d}"
        
        super().save(*args, **kwargs)
    
    @property
    def dias_transcurridos(self):
        """Calcula días desde la creación"""
        from django.utils import timezone
        delta = timezone.now() - self.fecha_creacion
        return delta.days
    
    @property
    def cantidad_fotos(self):
        """Retorna la cantidad de fotos adjuntas"""
        return self.fotos.count()
    
    @property
    def dias_sin_respuesta(self):
        """Calcula días sin respuesta del proveedor"""
        if not self.fecha_envio_proveedor:
            return 0
        if self.fecha_respuesta_proveedor:
            delta = self.fecha_respuesta_proveedor - self.fecha_envio_proveedor
        else:
            delta = timezone.now() - self.fecha_envio_proveedor
        return delta.days
    
    @property
    def requiere_recordatorio(self):
        """Indica si requiere enviar recordatorio al proveedor"""
        return (
            self.estado == 'ESPERANDO_RESPUESTA' and 
            self.dias_sin_respuesta > 7 and 
            not self.fecha_respuesta_proveedor
        )
    
    @property
    def nivel_urgencia(self):
        """Retorna nivel de urgencia según días transcurridos"""
        if self.estado in ['APROBADO', 'RECHAZADO', 'CANCELADO']:
            return 'CERRADO'
        
        dias = self.dias_transcurridos
        if self.estado == 'ESPERANDO_RESPUESTA':
            dias = self.dias_sin_respuesta
        
        if dias <= 3:
            return 'NORMAL'
        elif dias <= 7:
            return 'MEDIA'
        elif dias <= 14:
            return 'ALTA'
        else:
            return 'CRITICA'


class FotoRequerimiento(models.Model):
    """
    Modelo para almacenar fotos adjuntas a un requerimiento
    Permite hasta 5 fotos por requerimiento
    """
    requerimiento = models.ForeignKey(
        Requerimiento,
        on_delete=models.CASCADE,
        related_name='fotos'
    )
    imagen = models.ImageField(
        upload_to='requerimientos/fotos/%Y/%m/%d/',
        help_text="Foto del producto o problema"
    )
    descripcion = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Descripción de la foto"
    )
    orden = models.IntegerField(
        default=0,
        help_text="Orden de la foto (1-5)"
    )
    fecha_subida = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='fotos_requerimientos'
    )
    
    class Meta:
        ordering = ['orden', 'fecha_subida']
        verbose_name = 'Foto de Requerimiento'
        verbose_name_plural = 'Fotos de Requerimientos'
    
    def __str__(self):
        return f"Foto {self.orden} - {self.requerimiento.numero_requerimiento}"


class HistorialRequerimiento(models.Model):
    """
    Modelo para registrar el historial de cambios en un requerimiento
    """
    requerimiento = models.ForeignKey(
        Requerimiento,
        on_delete=models.CASCADE,
        related_name='historial'
    )
    accion = models.CharField(
        max_length=100,
        help_text="Tipo de acción realizada"
    )
    estado_anterior = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        help_text="Estado anterior del requerimiento"
    )
    estado_nuevo = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        help_text="Nuevo estado del requerimiento"
    )
    comentario = models.TextField(
        blank=True,
        null=True,
        help_text="Comentario o descripción de la acción"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='acciones_requerimientos'
    )
    fecha = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Historial de Requerimiento'
        verbose_name_plural = 'Historial de Requerimientos'
    
    def __str__(self):
        return f"{self.requerimiento.numero_requerimiento} - {self.accion} - {self.fecha.strftime('%d/%m/%Y %H:%M')}"
