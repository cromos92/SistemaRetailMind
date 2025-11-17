from django.contrib import admin
from .models import (
    Solicitud_Regularizacion, 
    Productos_Recepcionados,
    CambioPrecioPendiente,
    NotificacionCambioPrecio,
    HistorialCambioPrecio,
    Requerimiento,
    FotoRequerimiento,
    HistorialRequerimiento
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


# ========== ADMINISTRACIÓN DE REQUERIMIENTOS ==========

class FotoRequerimientoInline(admin.TabularInline):
    model = FotoRequerimiento
    extra = 1
    max_num = 5
    fields = ['imagen', 'descripcion', 'orden']
    readonly_fields = ['fecha_subida', 'usuario']


class HistorialRequerimientoInline(admin.TabularInline):
    model = HistorialRequerimiento
    extra = 0
    fields = ['accion', 'estado_anterior', 'estado_nuevo', 'comentario', 'usuario', 'fecha']
    readonly_fields = ['fecha', 'usuario']
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Requerimiento)
class RequerimientoAdmin(admin.ModelAdmin):
    list_display = [
        'numero_requerimiento', 'tipo', 'estado', 'sucursal', 'cliente_nombre',
        'sku', 'prioridad', 'dias_transcurridos_display', 'fecha_creacion'
    ]
    list_filter = ['estado', 'tipo', 'prioridad', 'sucursal', 'fecha_creacion']
    search_fields = [
        'numero_requerimiento', 'sku', 'cliente_nombre', 'cliente_rut',
        'numero_boleta', 'motivo'
    ]
    readonly_fields = [
        'numero_requerimiento', 'fecha_creacion', 'fecha_actualizacion',
        'dias_transcurridos', 'cantidad_fotos'
    ]
    inlines = [FotoRequerimientoInline, HistorialRequerimientoInline]
    date_hierarchy = 'fecha_creacion'
    
    fieldsets = (
        ('Información del Requerimiento', {
            'fields': ('numero_requerimiento', 'tipo', 'estado', 'prioridad')
        }),
        ('Sucursal y Usuarios', {
            'fields': ('sucursal', 'usuario_creador', 'usuario_gestor')
        }),
        ('Información del Producto', {
            'fields': ('producto_talla', 'sku', 'nombre_producto')
        }),
        ('Documento de Venta', {
            'fields': ('tipo_documento', 'numero_boleta', 'fecha_compra')
        }),
        ('Información del Cliente', {
            'fields': ('cliente_nombre', 'cliente_rut', 'cliente_telefono', 'cliente_email')
        }),
        ('Descripción', {
            'fields': ('motivo', 'descripcion_problema')
        }),
        ('Proveedor', {
            'fields': (
                'proveedor', 'correo_enviado_proveedor', 'fecha_envio_proveedor',
                'respuesta_proveedor', 'fecha_respuesta_proveedor'
            )
        }),
        ('Resolución', {
            'fields': ('resolucion', 'fecha_resolucion')
        }),
        ('Estadísticas', {
            'fields': ('dias_transcurridos', 'cantidad_fotos', 'fecha_creacion', 'fecha_actualizacion'),
            'classes': ('collapse',)
        }),
    )
    
    def dias_transcurridos_display(self, obj):
        dias = obj.dias_transcurridos
        if dias > 7:
            return f'{dias} días ⚠️'
        elif dias > 3:
            return f'{dias} días ⏰'
        return f'{dias} días'
    dias_transcurridos_display.short_description = 'Días'


@admin.register(FotoRequerimiento)
class FotoRequerimientoAdmin(admin.ModelAdmin):
    list_display = ['id', 'requerimiento', 'orden', 'descripcion', 'fecha_subida', 'usuario']
    list_filter = ['fecha_subida']
    search_fields = ['requerimiento__numero_requerimiento', 'descripcion']
    readonly_fields = ['fecha_subida']


@admin.register(HistorialRequerimiento)
class HistorialRequerimientoAdmin(admin.ModelAdmin):
    list_display = ['requerimiento', 'accion', 'estado_anterior', 'estado_nuevo', 'usuario', 'fecha']
    list_filter = ['accion', 'fecha']
    search_fields = ['requerimiento__numero_requerimiento', 'accion', 'comentario']
    readonly_fields = ['fecha']
    
    def has_add_permission(self, request):
        # No permitir crear manualmente (se crea automáticamente)
        return False
