from django.contrib import admin
from .models import (
    Solicitud_Regularizacion, 
    Productos_Recepcionados,
    CambioPrecioPendiente,
    NotificacionCambioPrecio,
    HistorialCambioPrecio
)

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


@admin.register(CambioPrecioPendiente)
class CambioPrecioPendienteAdmin(admin.ModelAdmin):
    list_display = ['id', 'producto_nombre', 'sku', 'sucursal', 'precio_anterior', 'precio_nuevo', 'porcentaje_cambio', 'estado', 'prioridad', 'dias_pendiente', 'fecha_creacion']
    list_filter = ['estado', 'prioridad', 'tipo_cambio', 'sucursal', 'fecha_creacion']
    search_fields = ['producto_talla__sku', 'producto_talla__producto__articulo', 'motivo']
    readonly_fields = ['fecha_creacion', 'fecha_revision', 'fecha_aprobacion', 'fecha_aplicacion', 'diferencia', 'porcentaje_cambio']
    
    fieldsets = (
        ('Producto', {
            'fields': ('producto_talla', 'sucursal')
        }),
        ('Cambio de Precio', {
            'fields': ('precio_anterior', 'precio_nuevo', 'diferencia', 'porcentaje_cambio', 'tipo_cambio')
        }),
        ('Estado y Prioridad', {
            'fields': ('estado', 'prioridad', 'notificado')
        }),
        ('Justificación', {
            'fields': ('motivo', 'recomendacion_sistema')
        }),
        ('Usuarios', {
            'fields': ('creado_por', 'revisado_por', 'aprobado_por')
        }),
        ('Fechas', {
            'fields': ('fecha_creacion', 'fecha_revision', 'fecha_aprobacion', 'fecha_aplicacion', 'fecha_vencimiento')
        }),
        ('Observaciones', {
            'fields': ('observaciones_revision', 'observaciones_aprobacion')
        }),
    )
    
    def producto_nombre(self, obj):
        return obj.producto_talla.producto.articulo
    producto_nombre.short_description = 'Producto'
    
    def sku(self, obj):
        return obj.producto_talla.sku
    sku.short_description = 'SKU'
    
    def dias_pendiente(self, obj):
        dias = obj.dias_pendiente
        if dias > 7:
            return f'{dias} días ⚠️'
        return f'{dias} días'
    dias_pendiente.short_description = 'Días Pendiente'


@admin.register(NotificacionCambioPrecio)
class NotificacionCambioPrecioAdmin(admin.ModelAdmin):
    list_display = ['id', 'usuario', 'tipo', 'mensaje_corto', 'leida', 'fecha_creacion']
    list_filter = ['tipo', 'leida', 'fecha_creacion']
    search_fields = ['usuario__username', 'mensaje']
    readonly_fields = ['fecha_creacion', 'fecha_lectura']
    
    def mensaje_corto(self, obj):
        return obj.mensaje[:80] + '...' if len(obj.mensaje) > 80 else obj.mensaje
    mensaje_corto.short_description = 'Mensaje'


@admin.register(HistorialCambioPrecio)
class HistorialCambioPrecioAdmin(admin.ModelAdmin):
    list_display = ['id', 'producto_nombre', 'precio_anterior', 'precio_nuevo', 'porcentaje_cambio', 'tipo_cambio', 'usuario', 'fecha_cambio', 'tallas_afectadas']
    list_filter = ['tipo_cambio', 'fecha_cambio', 'usuario']
    search_fields = ['producto__articulo', 'motivo']
    readonly_fields = ['fecha_cambio', 'diferencia', 'porcentaje_cambio', 'ip_address']
    date_hierarchy = 'fecha_cambio'
    
    fieldsets = (
        ('Producto', {
            'fields': ('producto',)
        }),
        ('Cambio de Precio', {
            'fields': ('precio_anterior', 'precio_nuevo', 'diferencia', 'porcentaje_cambio', 'tipo_cambio')
        }),
        ('Contexto', {
            'fields': ('motivo',)
        }),
        ('Auditoría', {
            'fields': ('usuario', 'fecha_cambio', 'ip_address')
        }),
        ('Impacto', {
            'fields': ('tallas_afectadas', 'lotes_afectados')
        }),
    )
    
    def producto_nombre(self, obj):
        return obj.producto.articulo
    producto_nombre.short_description = 'Producto'
    
    def has_add_permission(self, request):
        # No permitir crear manualmente (se crea automáticamente)
        return False
