from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import Usuario, LogAcceso

# Importar EmpresaUser para el inline
from app.models import EmpresaUser, Sucursal


class EmpresaUserInline(admin.TabularInline):
    """
    ⭐ INLINE PARA ASIGNAR SUCURSALES AL USUARIO
    Aparece directamente en el formulario de edición del usuario
    """
    model = EmpresaUser
    extra = 1  # Mostrar 1 fila vacía para agregar
    verbose_name = "Acceso a Sucursal"
    verbose_name_plural = "⭐ Sucursales Asignadas (Agregar aquí)"
    fields = ['empresa', 'sucursal', 'status', 'active']
    autocomplete_fields = ['empresa', 'sucursal']
    
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Filtra sucursales por empresa seleccionada"""
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Usuario)
class UsuarioAdmin(BaseUserAdmin):
    """
    Admin personalizado para el modelo Usuario
    ⭐ Incluye inline para asignar sucursales directamente
    """
    # Campos que se muestran en la lista
    list_display = [
        'username', 'get_full_name', 'email', 'rol', 'get_sucursales_count',
        'es_activo', 'is_staff', 'fecha_creacion'
    ]
    
    # Campos por los que se puede filtrar
    list_filter = [
        'rol', 'es_activo', 'is_staff', 'is_superuser', 'fecha_creacion',
        'puede_crear_usuarios', 'puede_editar_usuarios', 'puede_eliminar_usuarios'
    ]
    
    # Campos de búsqueda
    search_fields = ['username', 'first_name', 'last_name', 'email', 'rut', 'empresa']
    
    # Ordenamiento por defecto
    ordering = ['username']
    
    # ⭐ INLINE PARA SUCURSALES
    inlines = [EmpresaUserInline]
    
    # Configuración de fieldsets para el formulario de edición
    fieldsets = (
        ('Información Básica', {
            'fields': ('username', 'password', 'first_name', 'last_name', 'email')
        }),
        ('Información Personal', {
            'fields': ('rut', 'telefono', 'direccion', 'fecha_nacimiento')
        }),
        ('Información Laboral', {
            'fields': ('empresa', 'cargo', 'departamento', 'rol'),
            'description': '💡 El campo "empresa" es solo texto. Las sucursales reales se asignan abajo en "Sucursales Asignadas"'
        }),
        ('Permisos de Usuario', {
            'fields': ('puede_crear_usuarios', 'puede_editar_usuarios', 'puede_eliminar_usuarios'),
            'classes': ('collapse',)
        }),
        ('Permisos del Sistema', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
            'classes': ('collapse',)
        }),
        ('Estado', {
            'fields': ('es_activo', 'fecha_ultimo_acceso')
        }),
        ('Fechas Importantes', {
            'fields': ('last_login', 'date_joined', 'fecha_creacion'),
            'classes': ('collapse',)
        }),
    )
    
    # Campos de solo lectura
    readonly_fields = ['fecha_creacion', 'fecha_ultimo_acceso', 'last_login', 'date_joined']
    
    # Configuración para agregar usuario
    add_fieldsets = (
        ('Información Básica', {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2', 'first_name', 'last_name')
        }),
        ('Información Personal', {
            'fields': ('rut', 'telefono', 'empresa', 'cargo', 'rol')
        }),
        ('Permisos', {
            'fields': ('es_activo', 'is_staff', 'is_superuser')
        }),
    )
    
    def get_full_name(self, obj):
        """Obtiene el nombre completo del usuario"""
        return obj.get_full_name()
    get_full_name.short_description = 'Nombre Completo'
    
    def get_sucursales_count(self, obj):
        """Muestra cantidad de sucursales asignadas"""
        count = EmpresaUser.objects.filter(user=obj, status=True).count()
        if count == 0:
            return format_html('<span style="color: red;">⚠️ Sin sucursales</span>')
        return format_html('<span style="color: green;">✅ {} sucursal(es)</span>', count)
    get_sucursales_count.short_description = 'Sucursales'

@admin.register(LogAcceso)
class LogAccesoAdmin(admin.ModelAdmin):
    """
    Admin para el modelo LogAcceso
    """
    list_display = [
        'usuario', 'fecha_acceso', 'ip_address', 'exito', 'get_user_agent_short'
    ]
    
    list_filter = [
        'exito', 'fecha_acceso', 'usuario__es_activo'
    ]
    
    search_fields = [
        'usuario__username', 'usuario__first_name', 'usuario__last_name',
        'ip_address'
    ]
    
    readonly_fields = [
        'usuario', 'fecha_acceso', 'ip_address', 'user_agent', 'exito'
    ]
    
    ordering = ['-fecha_acceso']
    
    # No permitir agregar/editar/eliminar logs desde el admin
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def get_user_agent_short(self, obj):
        """Muestra una versión corta del user agent"""
        if obj.user_agent:
            return obj.user_agent[:50] + '...' if len(obj.user_agent) > 50 else obj.user_agent
        return 'N/A'
    get_user_agent_short.short_description = 'User Agent'
