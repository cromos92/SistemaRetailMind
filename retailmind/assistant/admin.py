"""
RetailMind Assistant - Admin
============================
Configuración del panel de administración para el asistente.
"""

from django.contrib import admin
from .models import (
    ConversacionAsistente,
    MensajeAsistente,
    FeedbackAsistente,
    EstadisticasAsistente
)


@admin.register(ConversacionAsistente)
class ConversacionAsistenteAdmin(admin.ModelAdmin):
    list_display = ['session_id', 'usuario', 'activa', 'total_mensajes', 'fecha_inicio', 'fecha_ultimo_mensaje']
    list_filter = ['activa', 'fecha_inicio']
    search_fields = ['session_id', 'usuario__username', 'usuario__email']
    readonly_fields = ['session_id', 'fecha_inicio', 'fecha_ultimo_mensaje']
    date_hierarchy = 'fecha_inicio'


@admin.register(MensajeAsistente)
class MensajeAsistenteAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversacion', 'rol', 'contenido_corto', 'tiempo_respuesta_ms', 'timestamp']
    list_filter = ['rol', 'timestamp']
    search_fields = ['contenido', 'conversacion__session_id']
    readonly_fields = ['timestamp']
    date_hierarchy = 'timestamp'
    
    def contenido_corto(self, obj):
        return obj.contenido[:100] + "..." if len(obj.contenido) > 100 else obj.contenido
    contenido_corto.short_description = "Contenido"


@admin.register(FeedbackAsistente)
class FeedbackAsistenteAdmin(admin.ModelAdmin):
    list_display = ['id', 'usuario', 'rating', 'tipo_feedback', 'revisado', 'fecha']
    list_filter = ['rating', 'tipo_feedback', 'revisado', 'fecha']
    search_fields = ['usuario__username', 'comentario', 'pregunta_usuario']
    readonly_fields = ['fecha']
    date_hierarchy = 'fecha'
    
    actions = ['marcar_como_revisado']
    
    def marcar_como_revisado(self, request, queryset):
        queryset.update(revisado=True)
    marcar_como_revisado.short_description = "Marcar como revisado"


@admin.register(EstadisticasAsistente)
class EstadisticasAsistenteAdmin(admin.ModelAdmin):
    list_display = [
        'fecha', 'total_conversaciones', 'total_mensajes', 
        'total_usuarios_unicos', 'rating_promedio', 'tiempo_respuesta_promedio_ms'
    ]
    readonly_fields = ['fecha', 'actualizado']
    date_hierarchy = 'fecha'
