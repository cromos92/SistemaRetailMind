from django.contrib import admin
from .models import (
    Solicitud_Regularizacion, 
    Productos_Recepcionados,
    CambioPrecioPendiente,
    NotificacionCambioPrecio,
    HistorialCambioPrecio,
    Requerimiento,
    FotoRequerimiento,
    HistorialRequerimiento,
    Vendedor,
    Empresa,
    Sucursal,
    EmpresaUser,
    ContactoEmpresa,
    Cliente,
    Proveedor,
    LogEmpresa,
    LogCliente,
    Producto,
)

# Importar admins de sincronización desktop
from .admin_sync import *

# Register your models here.

# ========== ADMINISTRACIÓN DE EMPRESA Y SUCURSALES ==========

@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ['razon_social', 'rut', 'giro', 'esProveedor']
    list_filter = ['esProveedor', 'acteco']
    search_fields = ['razon_social', 'rut', 'giro', 'nombre']
    
    fieldsets = (
        ('Datos Principales', {
            'fields': ('nombre', 'nombre_fantasia', 'razon_social', 'rut', 'giro', 'acteco', 'esProveedor')
        }),
        ('Ubicación', {
            'fields': ('direccion', 'comuna', 'ciudad')
        }),
        ('Contactos', {
            'fields': ('correoVendedor', 'correoIntercambio', 'correoAdministrador', 'contacto1', 'contacto2')
        }),
    )


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ['alias', 'empresa', 'direccion', 'tipo_sucursal', 'es_centro_distribucion']
    list_filter = ['empresa', 'tipo_sucursal', 'es_centro_distribucion']
    search_fields = ['alias', 'direccion']
    
    fieldsets = (
        ('Identificación', {
            'fields': ('alias', 'empresa')
        }),
        ('Ubicación', {
            'fields': ('direccion',)
        }),
        ('Configuración', {
            'fields': ('tipo_sucursal', 'es_centro_distribucion', 'margen_sobreprecio_default')
        }),
    )


@admin.register(Vendedor)
class VendedorAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'rut', 'codigo_vendedor', 'empresa', 'correo', 'comision', 'activo']
    list_filter = ['empresa', 'activo', 'sucursales']
    search_fields = ['nombre', 'rut', 'codigo_vendedor', 'correo']
    filter_horizontal = ['sucursales']  # Widget para seleccionar múltiples sucursales
    
    fieldsets = (
        ('Datos Personales', {
            'fields': ('nombre', 'rut', 'codigo_vendedor', 'fecha_nacimiento', 'correo')
        }),
        ('Empresa y Sucursales', {
            'fields': ('empresa', 'sucursales')
        }),
        ('Configuración', {
            'fields': ('comision', 'activo')
        }),
    )


@admin.register(EmpresaUser)
class EmpresaUserAdmin(admin.ModelAdmin):
    """
    ⭐ AQUÍ ASIGNAS EMPRESA Y SUCURSAL A UN USUARIO
    
    💡 TIP: Es más fácil editar el USUARIO directamente en:
       /admin/users/usuario/ → Editar usuario → Sección "Sucursales Asignadas"
    """
    list_display = ['user', 'empresa', 'sucursal', 'status', 'active', 'get_user_rol']
    list_filter = ['empresa', 'sucursal', 'status', 'active']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'empresa__nombre', 'sucursal__alias']
    list_editable = ['status', 'active']  # Editar directamente en la lista
    actions = ['activar_seleccionados', 'desactivar_seleccionados', 'marcar_como_activo']
    
    fieldsets = (
        ('Usuario', {
            'fields': ('user',)
        }),
        ('Empresa y Sucursal', {
            'fields': ('empresa', 'sucursal'),
            'description': '⭐ Asigna la empresa y sucursal al usuario'
        }),
        ('Estado', {
            'fields': ('status', 'active'),
            'description': 'status=habilitado, active=sucursal actual del usuario'
        }),
        ('Márgenes', {
            'fields': ('margenSobreprecio', 'margenPrecioVenta'),
            'classes': ('collapse',)
        }),
    )
    
    def get_user_rol(self, obj):
        return getattr(obj.user, 'rol', 'N/A')
    get_user_rol.short_description = 'Rol'
    
    @admin.action(description='✅ Activar acceso (status=True)')
    def activar_seleccionados(self, request, queryset):
        updated = queryset.update(status=True)
        self.message_user(request, f'{updated} acceso(s) activado(s)')
    
    @admin.action(description='❌ Desactivar acceso (status=False)')
    def desactivar_seleccionados(self, request, queryset):
        updated = queryset.update(status=False)
        self.message_user(request, f'{updated} acceso(s) desactivado(s)')
    
    @admin.action(description='⭐ Marcar como sucursal activa (active=True)')
    def marcar_como_activo(self, request, queryset):
        # Solo permitir uno activo por usuario
        for eu in queryset:
            EmpresaUser.objects.filter(user=eu.user).update(active=False)
            eu.active = True
            eu.save()
        self.message_user(request, f'{queryset.count()} sucursal(es) marcada(s) como activa(s)')

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
        return False


# ========== CRM: CONTACTOS, CLIENTES, PROVEEDORES, LOGS ==========

@admin.register(ContactoEmpresa)
class ContactoEmpresaAdmin(admin.ModelAdmin):
    list_display = ['empresa', 'nombre', 'cargo', 'email', 'telefono', 'tipo_contacto', 'activo']
    list_filter = ['tipo_contacto', 'activo']
    search_fields = ['nombre', 'email', 'empresa__nombre']


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ['nombre_completo', 'rut', 'email', 'telefono', 'tipo_cliente', 'empresa', 'activo']
    list_filter = ['tipo_cliente', 'activo', 'genero']
    search_fields = ['nombre', 'apellido', 'rut', 'email']
    readonly_fields = ['created_at', 'updated_at']

    def nombre_completo(self, obj):
        return f"{obj.nombre} {obj.apellido}"
    nombre_completo.short_description = 'Nombre'


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ['empresa', 'codigo_proveedor', 'categoria', 'dias_credito', 'calificacion', 'activo']
    list_filter = ['activo', 'categoria', 'calificacion']
    search_fields = ['codigo_proveedor', 'empresa__nombre']


@admin.register(LogEmpresa)
class LogEmpresaAdmin(admin.ModelAdmin):
    list_display = ['empresa', 'accion', 'usuario', 'fecha', 'ip_address']
    list_filter = ['accion', 'fecha']
    search_fields = ['empresa__nombre', 'descripcion']
    readonly_fields = ['empresa', 'usuario', 'accion', 'descripcion', 'datos_anteriores', 'datos_nuevos', 'fecha', 'ip_address', 'user_agent']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ['articulo', 'sucursal', 'costo', 'precioventa', 'excluir_de_analitica']
    list_filter = ['excluir_de_analitica', 'sucursal', 'categoria']
    search_fields = ['articulo', 'descripcion']
    list_editable = ['excluir_de_analitica']
    fieldsets = (
        ('Datos Básicos', {
            'fields': ('articulo', 'descripcion', 'categoria', 'sucursal')
        }),
        ('Atributos', {
            'fields': ('atributo1', 'atributo2', 'atributo3', 'atributo4')
        }),
        ('Precios', {
            'fields': ('costo', 'sobreprecio', 'precioventa', 'precioSugerido')
        }),
        ('Analítica', {
            'fields': ('excluir_de_analitica',),
            'description': 'Marcado como "Excluir" → no aparece en dashboards, predicciones ni KPIs.'
        }),
    )


@admin.register(LogCliente)
class LogClienteAdmin(admin.ModelAdmin):
    list_display = ['cliente', 'accion', 'usuario', 'fecha', 'ip_address']
    list_filter = ['accion', 'fecha']
    search_fields = ['cliente__nombre', 'cliente__apellido', 'descripcion']
    readonly_fields = ['cliente', 'usuario', 'accion', 'descripcion', 'datos_anteriores', 'datos_nuevos', 'fecha', 'ip_address', 'user_agent']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ========== FIDELIZACIÓN: APP "MIS PUNTOS" ==========
from .models import DesafioPromo, Referido, DispositivoCliente


@admin.register(DesafioPromo)
class DesafioPromoAdmin(admin.ModelAdmin):
    """Crear/editar desafíos ("haz N compras y gana X pts") sin tocar código."""
    list_display = ['nombre', 'tipo', 'meta_valor', 'bono_puntos',
                    'fecha_inicio', 'fecha_fin', 'nivel_objetivo', 'activo']
    list_filter = ['activo', 'tipo', 'nivel_objetivo']
    search_fields = ['nombre']


@admin.register(Referido)
class ReferidoAdmin(admin.ModelAdmin):
    list_display = ['padrino', 'ahijado', 'estado', 'puntos_padrino',
                    'puntos_ahijado', 'created_at', 'pagado_at']
    list_filter = ['estado']
    search_fields = ['padrino__nombre', 'padrino__rut',
                     'ahijado__nombre', 'ahijado__rut']
    readonly_fields = ['created_at', 'pagado_at']


@admin.register(DispositivoCliente)
class DispositivoClienteAdmin(admin.ModelAdmin):
    list_display = ['cliente', 'plataforma', 'activo', 'last_seen', 'created_at']
    list_filter = ['plataforma', 'activo']
    search_fields = ['cliente__nombre', 'cliente__rut']
    readonly_fields = ['token', 'created_at', 'last_seen']
