"""
RetailMind Assistant - Models
=============================
Modelos para persistir conversaciones y feedback del asistente.
"""

from django.db import models
from django.conf import settings
from django.utils import timezone


class ConversacionAsistente(models.Model):
    """
    Modelo para almacenar sesiones de conversación con el asistente.
    """
    
    # === RELACIONES ===
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversaciones_asistente',
        help_text="Usuario que inició la conversación"
    )
    
    # === IDENTIFICACIÓN ===
    session_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="ID único de la sesión"
    )
    
    # === ESTADO ===
    activa = models.BooleanField(
        default=True,
        help_text="Si la conversación está activa"
    )
    
    # === MÉTRICAS ===
    total_mensajes = models.IntegerField(
        default=0,
        help_text="Total de mensajes en la conversación"
    )
    total_tool_calls = models.IntegerField(
        default=0,
        help_text="Total de llamadas a herramientas"
    )
    
    # === METADATA ===
    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_ultimo_mensaje = models.DateTimeField(auto_now=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Conversación del Asistente"
        verbose_name_plural = "Conversaciones del Asistente"
        ordering = ['-fecha_ultimo_mensaje']
        indexes = [
            models.Index(fields=['usuario', 'activa']),
            models.Index(fields=['session_id']),
            models.Index(fields=['fecha_inicio']),
        ]
    
    def __str__(self):
        return f"Conversación {self.session_id} - {self.usuario.username}"


class MensajeAsistente(models.Model):
    """
    Modelo para almacenar mensajes individuales de una conversación.
    """
    
    ROLES = [
        ('user', 'Usuario'),
        ('assistant', 'Asistente'),
        ('system', 'Sistema'),
    ]
    
    # === RELACIONES ===
    conversacion = models.ForeignKey(
        ConversacionAsistente,
        on_delete=models.CASCADE,
        related_name='mensajes',
        help_text="Conversación a la que pertenece"
    )
    
    # === CONTENIDO ===
    rol = models.CharField(
        max_length=20,
        choices=ROLES,
        help_text="Rol del mensaje"
    )
    contenido = models.TextField(
        help_text="Contenido del mensaje"
    )
    
    # === METADATA DE TOOLS ===
    tools_usadas = models.JSONField(
        null=True,
        blank=True,
        help_text="Lista de herramientas usadas en este mensaje"
    )
    
    # === TIMESTAMPS ===
    timestamp = models.DateTimeField(auto_now_add=True)
    tiempo_respuesta_ms = models.IntegerField(
        null=True,
        blank=True,
        help_text="Tiempo de respuesta en milisegundos"
    )
    
    class Meta:
        verbose_name = "Mensaje del Asistente"
        verbose_name_plural = "Mensajes del Asistente"
        ordering = ['timestamp']
        indexes = [
            models.Index(fields=['conversacion', 'timestamp']),
            models.Index(fields=['rol', 'timestamp']),
        ]
    
    def __str__(self):
        preview = self.contenido[:50] + "..." if len(self.contenido) > 50 else self.contenido
        return f"[{self.rol}] {preview}"


class FeedbackAsistente(models.Model):
    """
    Modelo para almacenar feedback de los usuarios sobre las respuestas.
    """
    
    RATING_CHOICES = [
        (1, '👎 Muy malo'),
        (2, '😕 Malo'),
        (3, '😐 Regular'),
        (4, '🙂 Bueno'),
        (5, '👍 Excelente'),
    ]
    
    TIPO_FEEDBACK = [
        ('UTIL', 'Respuesta útil'),
        ('NO_UTIL', 'Respuesta no útil'),
        ('INCORRECTO', 'Información incorrecta'),
        ('INCOMPLETO', 'Información incompleta'),
        ('ERROR', 'Error del sistema'),
        ('SUGERENCIA', 'Sugerencia de mejora'),
        ('OTRO', 'Otro'),
    ]
    
    # === RELACIONES ===
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedback_asistente'
    )
    mensaje = models.ForeignKey(
        MensajeAsistente,
        on_delete=models.CASCADE,
        related_name='feedback',
        null=True,
        blank=True,
        help_text="Mensaje específico evaluado"
    )
    conversacion = models.ForeignKey(
        ConversacionAsistente,
        on_delete=models.CASCADE,
        related_name='feedback',
        null=True,
        blank=True,
        help_text="Conversación evaluada"
    )
    
    # === CALIFICACIÓN ===
    rating = models.IntegerField(
        choices=RATING_CHOICES,
        help_text="Calificación del 1 al 5"
    )
    tipo_feedback = models.CharField(
        max_length=20,
        choices=TIPO_FEEDBACK,
        default='UTIL',
        help_text="Tipo de feedback"
    )
    
    # === COMENTARIO ===
    comentario = models.TextField(
        blank=True,
        null=True,
        help_text="Comentario adicional del usuario"
    )
    
    # === CONTEXTO ===
    pregunta_usuario = models.TextField(
        blank=True,
        null=True,
        help_text="Pregunta que generó la respuesta evaluada"
    )
    respuesta_asistente = models.TextField(
        blank=True,
        null=True,
        help_text="Respuesta del asistente evaluada"
    )
    
    # === METADATA ===
    fecha = models.DateTimeField(auto_now_add=True)
    revisado = models.BooleanField(
        default=False,
        help_text="Si el feedback ha sido revisado por el equipo"
    )
    notas_revision = models.TextField(
        blank=True,
        null=True,
        help_text="Notas del equipo sobre el feedback"
    )
    
    class Meta:
        verbose_name = "Feedback del Asistente"
        verbose_name_plural = "Feedback del Asistente"
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['usuario', 'fecha']),
            models.Index(fields=['rating', 'fecha']),
            models.Index(fields=['tipo_feedback', 'revisado']),
        ]
    
    def __str__(self):
        return f"Feedback {self.get_rating_display()} - {self.usuario.username}"
    
    @property
    def es_positivo(self):
        """Indica si el feedback es positivo (rating >= 4)"""
        return self.rating >= 4
    
    @property
    def es_negativo(self):
        """Indica si el feedback es negativo (rating <= 2)"""
        return self.rating <= 2


class EstadisticasAsistente(models.Model):
    """
    Modelo para almacenar estadísticas diarias del asistente.
    """
    
    # === PERÍODO ===
    fecha = models.DateField(
        unique=True,
        help_text="Fecha de las estadísticas"
    )
    
    # === MÉTRICAS DE USO ===
    total_conversaciones = models.IntegerField(default=0)
    total_mensajes = models.IntegerField(default=0)
    total_usuarios_unicos = models.IntegerField(default=0)
    
    # === MÉTRICAS DE HERRAMIENTAS ===
    total_tool_calls = models.IntegerField(default=0)
    tools_mas_usadas = models.JSONField(
        default=dict,
        help_text="Conteo de uso por herramienta"
    )
    
    # === MÉTRICAS DE RENDIMIENTO ===
    tiempo_respuesta_promedio_ms = models.IntegerField(default=0)
    tasa_exito = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=100.0,
        help_text="Porcentaje de respuestas exitosas"
    )
    
    # === MÉTRICAS DE FEEDBACK ===
    total_feedback = models.IntegerField(default=0)
    rating_promedio = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        null=True,
        blank=True
    )
    feedback_positivo = models.IntegerField(default=0)
    feedback_negativo = models.IntegerField(default=0)
    
    # === METADATA ===
    actualizado = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Estadísticas del Asistente"
        verbose_name_plural = "Estadísticas del Asistente"
        ordering = ['-fecha']
    
    def __str__(self):
        return f"Estadísticas {self.fecha}"
    
    @classmethod
    def actualizar_estadisticas_hoy(cls):
        """Actualiza las estadísticas del día actual"""
        from django.db.models import Avg, Count
        
        hoy = timezone.now().date()
        
        # Obtener o crear registro de hoy
        stats, created = cls.objects.get_or_create(fecha=hoy)
        
        # Conversaciones de hoy
        conversaciones = ConversacionAsistente.objects.filter(
            fecha_inicio__date=hoy
        )
        
        stats.total_conversaciones = conversaciones.count()
        stats.total_usuarios_unicos = conversaciones.values('usuario').distinct().count()
        
        # Mensajes de hoy
        mensajes = MensajeAsistente.objects.filter(
            timestamp__date=hoy
        )
        stats.total_mensajes = mensajes.count()
        
        # Tiempo de respuesta promedio
        tiempo_promedio = mensajes.filter(
            tiempo_respuesta_ms__isnull=False
        ).aggregate(promedio=Avg('tiempo_respuesta_ms'))
        stats.tiempo_respuesta_promedio_ms = int(tiempo_promedio['promedio'] or 0)
        
        # Feedback de hoy
        feedback = FeedbackAsistente.objects.filter(
            fecha__date=hoy
        )
        stats.total_feedback = feedback.count()
        
        rating_promedio = feedback.aggregate(promedio=Avg('rating'))
        stats.rating_promedio = rating_promedio['promedio']
        
        stats.feedback_positivo = feedback.filter(rating__gte=4).count()
        stats.feedback_negativo = feedback.filter(rating__lte=2).count()
        
        stats.save()
        
        return stats
