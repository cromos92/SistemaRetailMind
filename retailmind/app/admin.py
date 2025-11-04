from django.contrib import admin
from .models import Solicitud_Regularizacion, Productos_Recepcionados

# Register your models here.

@admin.register(Solicitud_Regularizacion)
class SolicitudRegularizacionAdmin(admin.ModelAdmin):
    list_display = ['numero_solicitud', 'sucursal_solicitante', 'sucursal_emisora', 'tipo_problema', 'tipo_solucion_solicitada', 'estado', 'fecha_solicitud']
    list_filter = ['estado', 'tipo_problema', 'tipo_solucion_solicitada', 'fecha_solicitud']
    search_fields = ['numero_solicitud', 'descripcion_problema']
    readonly_fields = ['fecha_solicitud', 'fecha_revision', 'fecha_ejecucion', 'fecha_confirmacion']
    
    fieldsets = (
        ('Identificación', {
            'fields': ('numero_solicitud', 'fecha_solicitud', 'estado')
        }),
        ('Problema', {
            'fields': ('dte_original', 'producto_recepcionado', 'tipo_problema', 'cantidad_problema', 'descripcion_problema', 'evidencia_foto')
        }),
        ('Solicitante', {
            'fields': ('sucursal_solicitante', 'usuario_solicita', 'tipo_solucion_solicitada', 'producto_cambio_solicitado', 'cantidad_cambio_solicitada')
        }),
        ('Revisión del Emisor', {
            'fields': ('sucursal_emisora', 'fecha_revision', 'usuario_revisa', 'decision_emisor', 'tipo_solucion_aprobada', 'producto_cambio_aprobado', 'cantidad_cambio_aprobada')
        }),
        ('Ejecución', {
            'fields': ('fecha_ejecucion', 'dte_solucion', 'nota_credito')
        }),
        ('Confirmación', {
            'fields': ('fecha_confirmacion', 'usuario_confirma', 'conformidad', 'observaciones_finales')
        }),
    )
