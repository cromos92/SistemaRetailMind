"""
Admin para modelos de sincronización Desktop
============================================
"""

from django.contrib import admin
from django.utils.html import format_html

from .models_sync import (
    DispositivoAutorizado,
    RefreshTokenDesktop,
    SyncLog,
    CuadraturaCaja,
    MovimientoCaja,
)


@admin.register(DispositivoAutorizado)
class DispositivoAutorizadoAdmin(admin.ModelAdmin):
    """Admin para dispositivos autorizados."""
    
    list_display = [
        'nombre',
        'device_id_corto',
        'sucursal',
        'usuario',
        'estado_badge',
        'version_app',
        'ultimo_acceso',
        'ultima_sincronizacion',
    ]
    list_filter = ['estado', 'activo', 'sucursal', 'created_at']
    search_fields = ['nombre', 'device_id', 'usuario__username', 'sucursal__alias']
    readonly_fields = ['device_id', 'created_at', 'updated_at', 'ultimo_acceso', 'ultima_sincronizacion']
    
    fieldsets = (
        ('Identificación', {
            'fields': ('device_id', 'nombre', 'descripcion')
        }),
        ('Asignación', {
            'fields': ('sucursal', 'usuario')
        }),
        ('Estado', {
            'fields': ('estado', 'activo')
        }),
        ('Información Técnica', {
            'fields': ('sistema_operativo', 'version_app', 'max_tickets_offline')
        }),
        ('Fechas', {
            'fields': ('ultimo_acceso', 'ultima_sincronizacion', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def device_id_corto(self, obj):
        return str(obj.device_id)[:8] + '...'
    device_id_corto.short_description = 'Device ID'
    
    def estado_badge(self, obj):
        colores = {
            'ACTIVO': 'green',
            'SUSPENDIDO': 'orange',
            'REVOCADO': 'red',
        }
        color = colores.get(obj.estado, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px;">{}</span>',
            color, obj.estado
        )
    estado_badge.short_description = 'Estado'
    
    actions = ['suspender_dispositivos', 'activar_dispositivos', 'revocar_dispositivos']
    
    def suspender_dispositivos(self, request, queryset):
        queryset.update(estado='SUSPENDIDO', activo=False)
        self.message_user(request, f'{queryset.count()} dispositivos suspendidos')
    suspender_dispositivos.short_description = 'Suspender dispositivos seleccionados'
    
    def activar_dispositivos(self, request, queryset):
        queryset.exclude(estado='REVOCADO').update(estado='ACTIVO', activo=True)
        self.message_user(request, f'{queryset.count()} dispositivos activados')
    activar_dispositivos.short_description = 'Activar dispositivos seleccionados'
    
    def revocar_dispositivos(self, request, queryset):
        for dispositivo in queryset:
            dispositivo.revocar(motivo='Revocado desde admin')
        self.message_user(request, f'{queryset.count()} dispositivos revocados')
    revocar_dispositivos.short_description = 'Revocar dispositivos seleccionados'


@admin.register(RefreshTokenDesktop)
class RefreshTokenDesktopAdmin(admin.ModelAdmin):
    """Admin para tokens de refresco."""
    
    list_display = [
        'id_corto',
        'dispositivo',
        'usuario',
        'estado_badge',
        'created_at',
        'expires_at',
    ]
    list_filter = ['revocado', 'utilizado', 'created_at']
    search_fields = ['dispositivo__nombre', 'usuario__username']
    readonly_fields = ['id', 'token_hash', 'familia_id', 'created_at', 'used_at']
    
    def id_corto(self, obj):
        return str(obj.id)[:8] + '...'
    id_corto.short_description = 'ID'
    
    def estado_badge(self, obj):
        if obj.revocado:
            return format_html('<span style="color: red;">✗ Revocado</span>')
        elif obj.utilizado:
            return format_html('<span style="color: orange;">↺ Usado</span>')
        else:
            return format_html('<span style="color: green;">✓ Activo</span>')
    estado_badge.short_description = 'Estado'
    
    actions = ['revocar_tokens']
    
    def revocar_tokens(self, request, queryset):
        queryset.update(revocado=True)
        self.message_user(request, f'{queryset.count()} tokens revocados')
    revocar_tokens.short_description = 'Revocar tokens seleccionados'


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    """Admin para logs de sincronización."""
    
    list_display = [
        'tipo',
        'dispositivo',
        'sucursal',
        'estado_badge',
        'registros_resumen',
        'timestamp_inicio',
        'duracion_ms',
    ]
    list_filter = ['tipo', 'estado', 'exitoso', 'sucursal', 'timestamp_inicio']
    search_fields = ['dispositivo__nombre', 'sucursal__alias', 'error_mensaje']
    readonly_fields = [
        'id', 'timestamp_inicio', 'timestamp_fin', 'duracion_ms',
        'detalles', 'error_mensaje'
    ]
    
    def estado_badge(self, obj):
        colores = {
            'INICIADO': 'blue',
            'EN_PROCESO': 'orange',
            'COMPLETADO': 'green',
            'FALLIDO': 'red',
            'PARCIAL': 'yellow',
        }
        color = colores.get(obj.estado, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px;">{}</span>',
            color, obj.estado
        )
    estado_badge.short_description = 'Estado'
    
    def registros_resumen(self, obj):
        return f'{obj.registros_procesados}/{obj.registros_enviados} ({obj.registros_fallidos} fallidos)'
    registros_resumen.short_description = 'Registros'
    
    def has_add_permission(self, request):
        return False  # Logs son solo de lectura
    
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CuadraturaCaja)
class CuadraturaCajaAdmin(admin.ModelAdmin):
    """Admin para cuadraturas de caja."""
    
    list_display = [
        'id_corto',
        'sucursal',
        'vendedor',
        'fecha_apertura',
        'estado_badge',
        'diferencia_badge',
        'cantidad_tickets',
    ]
    list_filter = ['estado', 'sucursal', 'fecha_apertura']
    search_fields = ['sucursal__alias', 'vendedor__nombre']
    readonly_fields = ['id', 'local_id', 'synced_at', 'created_at']
    
    fieldsets = (
        ('Identificación', {
            'fields': ('id', 'local_id', 'sucursal', 'vendedor', 'dispositivo')
        }),
        ('Apertura/Cierre', {
            'fields': ('fecha_apertura', 'monto_apertura', 'fecha_cierre', 'estado')
        }),
        ('Montos Esperados', {
            'fields': (
                'efectivo_esperado', 'tarjeta_debito_esperado',
                'tarjeta_credito_esperado', 'transferencia_esperado', 'otros_esperado'
            )
        }),
        ('Montos Contados', {
            'fields': (
                'efectivo_contado', 'tarjeta_debito_contado',
                'tarjeta_credito_contado', 'transferencia_contado', 'otros_contado'
            )
        }),
        ('Diferencias', {
            'fields': ('diferencia_efectivo', 'diferencia_total')
        }),
        ('Documentos', {
            'fields': ('cantidad_tickets', 'cantidad_boletas', 'cantidad_facturas')
        }),
        ('Movimientos', {
            'fields': ('total_ingresos', 'total_egresos', 'total_retiros')
        }),
        ('Observaciones', {
            'fields': ('observaciones',)
        }),
        ('Sincronización', {
            'fields': ('synced_at', 'created_at'),
            'classes': ('collapse',)
        }),
    )
    
    def id_corto(self, obj):
        return str(obj.id)[:8] + '...'
    id_corto.short_description = 'ID'
    
    def estado_badge(self, obj):
        colores = {
            'ABIERTA': 'blue',
            'CERRADA': 'orange',
            'CUADRADA': 'green',
            'DESCUADRADA': 'red',
        }
        color = colores.get(obj.estado, 'gray')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px;">{}</span>',
            color, obj.estado
        )
    estado_badge.short_description = 'Estado'
    
    def diferencia_badge(self, obj):
        if obj.diferencia_total == 0:
            return format_html('<span style="color: green;">$0</span>')
        elif obj.diferencia_total > 0:
            return format_html('<span style="color: blue;">+${:,}</span>', obj.diferencia_total)
        else:
            return format_html('<span style="color: red;">-${:,}</span>', abs(obj.diferencia_total))
    diferencia_badge.short_description = 'Diferencia'


@admin.register(MovimientoCaja)
class MovimientoCajaAdmin(admin.ModelAdmin):
    """Admin para movimientos de caja."""
    
    list_display = [
        'tipo',
        'monto_formateado',
        'concepto',
        'sucursal',
        'fecha_hora',
    ]
    list_filter = ['tipo', 'sucursal', 'fecha_hora']
    search_fields = ['concepto', 'sucursal__alias']
    readonly_fields = ['id', 'local_id', 'synced_at', 'created_at']
    
    def monto_formateado(self, obj):
        if obj.tipo in ['EGRESO', 'RETIRO']:
            return format_html('<span style="color: red;">-${:,}</span>', obj.monto)
        return format_html('<span style="color: green;">+${:,}</span>', obj.monto)
    monto_formateado.short_description = 'Monto'
