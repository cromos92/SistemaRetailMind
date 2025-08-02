from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import (
    Empresa, Sucursal, ContactoEmpresa, Cliente, 
    Proveedor, LogEmpresa, LogCliente
)

# ========== ADMIN PARA EMPRESAS ==========

class SucursalInline(admin.TabularInline):
    model = Sucursal
    extra = 1
    fields = ('alias', 'nombre', 'direccion', 'comuna', 'ciudad', 'telefono', 'email', 'activa')
    readonly_fields = ('created_at', 'updated_at')

class ContactoEmpresaInline(admin.TabularInline):
    model = ContactoEmpresa
    extra = 1
    fields = ('nombre', 'cargo', 'email', 'telefono', 'celular', 'tipo_contacto', 'activo')
    readonly_fields = ('created_at', 'updated_at')

class ClienteInline(admin.TabularInline):
    model = Cliente
    extra = 0
    fields = ('nombre', 'apellido', 'rut', 'email', 'telefono', 'tipo_cliente', 'activo')
    readonly_fields = ('created_at', 'updated_at')
    can_delete = False

@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = (
        'nombre', 'rut', 'tipo_empresa', 'ciudad', 'telefono', 
        'es_proveedor_display', 'activo', 'fecha_creacion'
    )
    list_filter = (
        'tipo_empresa', 'activo', 'esProveedor', 'ciudad', 
        'fecha_creacion', 'created_by'
    )
    search_fields = (
        'nombre', 'rut', 'nombre_fantasia', 'razon_social', 
        'giro', 'email', 'telefono'
    )
    readonly_fields = (
        'created_at', 'updated_at', 'created_by', 'updated_by',
        'es_cliente', 'es_proveedor'
    )
    fieldsets = (
        ('Información Básica', {
            'fields': ('nombre', 'rut', 'nombre_fantasia', 'razon_social', 'giro')
        }),
        ('Dirección', {
            'fields': ('direccion', 'comuna', 'ciudad', 'region', 'codigo_postal')
        }),
        ('Contacto', {
            'fields': ('telefono', 'email', 'sitio_web')
        }),
        ('Clasificación', {
            'fields': ('tipo_empresa', 'esProveedor')
        }),
        ('Datos Fiscales', {
            'fields': ('correoVendedor', 'correoIntercambio', 'correoAdministrador')
        }),
        ('Estado', {
            'fields': ('activo', 'observaciones')
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',)
        }),
    )
    inlines = [SucursalInline, ContactoEmpresaInline, ClienteInline]
    
    def es_proveedor_display(self, obj):
        if obj.esProveedor:
            return format_html('<span style="color: green;">✓</span>')
        return format_html('<span style="color: red;">✗</span>')
    es_proveedor_display.short_description = 'Es Proveedor'
    
    def save_model(self, request, obj, form, change):
        if not change:  # Si es una nueva empresa
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'alias', 'nombre', 'ciudad', 'telefono', 'activa')
    list_filter = ('activa', 'ciudad', 'empresa')
    search_fields = ('alias', 'nombre', 'direccion', 'empresa__nombre')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('empresa', 'alias', 'nombre')
        }),
        ('Dirección', {
            'fields': ('direccion', 'comuna', 'ciudad')
        }),
        ('Contacto', {
            'fields': ('telefono', 'email')
        }),
        ('Estado', {
            'fields': ('activa',)
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

@admin.register(ContactoEmpresa)
class ContactoEmpresaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'nombre', 'cargo', 'email', 'telefono', 'tipo_contacto', 'activo')
    list_filter = ('tipo_contacto', 'activo', 'empresa')
    search_fields = ('nombre', 'email', 'empresa__nombre')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('empresa', 'nombre', 'cargo', 'tipo_contacto')
        }),
        ('Contacto', {
            'fields': ('email', 'telefono', 'celular')
        }),
        ('Estado', {
            'fields': ('activo',)
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

# ========== ADMIN PARA CLIENTES ==========

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = (
        'nombre_completo', 'rut', 'email', 'telefono', 'tipo_cliente', 
        'empresa_display', 'activo', 'fecha_creacion'
    )
    list_filter = (
        'tipo_cliente', 'activo', 'genero', 'empresa', 
        'fecha_nacimiento', 'created_by'
    )
    search_fields = (
        'nombre', 'apellido', 'rut', 'email', 'telefono', 
        'celular', 'empresa__nombre'
    )
    readonly_fields = (
        'created_at', 'updated_at', 'created_by', 'updated_by',
        'nombre_completo'
    )
    fieldsets = (
        ('Información Personal', {
            'fields': ('nombre', 'apellido', 'rut', 'fecha_nacimiento', 'genero')
        }),
        ('Contacto', {
            'fields': ('email', 'telefono', 'celular')
        }),
        ('Dirección', {
            'fields': ('direccion', 'comuna', 'ciudad')
        }),
        ('Clasificación', {
            'fields': ('tipo_cliente', 'empresa')
        }),
        ('Estado', {
            'fields': ('activo', 'observaciones')
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',)
        }),
    )
    
    def empresa_display(self, obj):
        if obj.empresa:
            return format_html(
                '<a href="{}">{}</a>',
                reverse('admin:empresa_management_empresa_change', args=[obj.empresa.id]),
                obj.empresa.nombre
            )
        return '-'
    empresa_display.short_description = 'Empresa'
    
    def fecha_creacion(self, obj):
        return obj.created_at.strftime('%d/%m/%Y')
    fecha_creacion.short_description = 'Fecha Creación'
    
    def save_model(self, request, obj, form, change):
        if not change:  # Si es un nuevo cliente
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

# ========== ADMIN PARA PROVEEDORES ==========

@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'codigo_proveedor', 'categoria', 'dias_credito', 'calificacion', 'activo')
    list_filter = ('activo', 'categoria', 'calificacion')
    search_fields = ('codigo_proveedor', 'empresa__nombre', 'categoria')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Información Básica', {
            'fields': ('empresa', 'codigo_proveedor', 'categoria')
        }),
        ('Condiciones Comerciales', {
            'fields': ('dias_credito', 'descuento_porcentaje')
        }),
        ('Evaluación', {
            'fields': ('calificacion', 'observaciones_evaluacion')
        }),
        ('Estado', {
            'fields': ('activo',)
        }),
        ('Metadatos', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

# ========== ADMIN PARA LOGS ==========

@admin.register(LogEmpresa)
class LogEmpresaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'accion', 'usuario', 'fecha', 'ip_address')
    list_filter = ('accion', 'fecha', 'empresa', 'usuario')
    search_fields = ('empresa__nombre', 'descripcion', 'usuario__username')
    readonly_fields = ('empresa', 'usuario', 'accion', 'descripcion', 'datos_anteriores', 'datos_nuevos', 'fecha', 'ip_address', 'user_agent')
    
    fieldsets = (
        ('Información del Log', {
            'fields': ('empresa', 'usuario', 'accion', 'descripcion')
        }),
        ('Datos', {
            'fields': ('datos_anteriores', 'datos_nuevos')
        }),
        ('Metadatos', {
            'fields': ('fecha', 'ip_address', 'user_agent')
        }),
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(LogCliente)
class LogClienteAdmin(admin.ModelAdmin):
    list_display = ('cliente', 'accion', 'usuario', 'fecha', 'ip_address')
    list_filter = ('accion', 'fecha', 'cliente', 'usuario')
    search_fields = ('cliente__nombre', 'cliente__apellido', 'descripcion', 'usuario__username')
    readonly_fields = ('cliente', 'usuario', 'accion', 'descripcion', 'datos_anteriores', 'datos_nuevos', 'fecha', 'ip_address', 'user_agent')
    
    fieldsets = (
        ('Información del Log', {
            'fields': ('cliente', 'usuario', 'accion', 'descripcion')
        }),
        ('Datos', {
            'fields': ('datos_anteriores', 'datos_nuevos')
        }),
        ('Metadatos', {
            'fields': ('fecha', 'ip_address', 'user_agent')
        }),
    )
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False

# ========== CONFIGURACIÓN DEL SITIO ADMIN ==========

admin.site.site_header = "RetailMind - Administración"
admin.site.site_title = "RetailMind Admin"
admin.site.index_title = "Panel de Administración"

# Agrupar modelos en el admin
# admin.site.register(Sucursal, SucursalAdmin)
# admin.site.register(ContactoEmpresa, ContactoEmpresaAdmin)
# admin.site.register(Cliente, ClienteAdmin)
# admin.site.register(Proveedor, ProveedorAdmin)
# admin.site.register(LogEmpresa, LogEmpresaAdmin)
# admin.site.register(LogCliente, LogClienteAdmin) 