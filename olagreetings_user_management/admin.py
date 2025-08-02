from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import Usuario, LogAcceso

@admin.register(Usuario)
class UsuarioAdmin(BaseUserAdmin):
    """
    Configuración del admin para el modelo Usuario personalizado
    """
    list_display = [
        'username', 'get_full_name_display', 'email', 'rut', 'empresa', 
        'es_activo', 'get_permisos_display', 'fecha_creacion', 'ultimo_acceso_display'
    ]
    list_filter = [
        'es_activo', 'is_staff', 'is_superuser', 'puede_crear_usuarios',
        'puede_editar_usuarios', 'puede_eliminar_usuarios', 'fecha_creacion',
        'empresa', 'departamento'
    ]
    search_fields = [
        'username', 'first_name', 'last_name', 'email', 'rut', 
        'empresa', 'cargo', 'departamento'
    ]
    ordering = ['username']
    list_per_page = 25
    
    # Campos para crear/editar usuario
    fieldsets = (
        (None, {
            'fields': ('username', 'password')
        }),
        ('Información Personal', {
            'fields': ('first_name', 'last_name', 'email', 'rut', 'telefono', 'fecha_nacimiento')
        }),
        ('Información de Empresa', {
            'fields': ('empresa', 'cargo', 'departamento', 'direccion'),
            'classes': ('collapse',)
        }),
        ('Permisos', {
            'fields': (
                'is_active', 'is_staff', 'is_superuser', 'es_activo',
                'puede_crear_usuarios', 'puede_editar_usuarios', 'puede_eliminar_usuarios'
            ),
        }),
        ('Grupos y Permisos', {
            'fields': ('groups', 'user_permissions'),
            'classes': ('collapse',)
        }),
        ('Fechas Importantes', {
            'fields': ('last_login', 'fecha_creacion', 'fecha_ultimo_acceso'),
            'classes': ('collapse',)
        }),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'username', 'email', 'password1', 'password2',
                'first_name', 'last_name', 'rut', 'empresa'
            ),
        }),
    )
    
    readonly_fields = ['fecha_creacion', 'fecha_ultimo_acceso', 'token_reset_password']
    
    def get_full_name_display(self, obj):
        """Mostrar nombre completo con enlace al perfil"""
        if obj.first_name and obj.last_name:
            return format_html(
                '<a href="{}">{}</a>',
                reverse('admin:usuarios_usuario_change', args=[obj.id]),
                f"{obj.first_name} {obj.last_name}"
            )
        return format_html(
            '<a href="{}">{}</a>',
            reverse('admin:usuarios_usuario_change', args=[obj.id]),
            obj.username
        )
    get_full_name_display.short_description = 'Nombre Completo'
    get_full_name_display.admin_order_field = 'first_name'
    
    def get_permisos_display(self, obj):
        """Mostrar permisos como badges"""
        permisos = []
        if obj.is_superuser:
            permisos.append('<span style="background: #dc3545; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">Super</span>')
        if obj.is_staff:
            permisos.append('<span style="background: #fd7e14; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">Staff</span>')
        if obj.puede_crear_usuarios:
            permisos.append('<span style="background: #28a745; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">Crear</span>')
        if obj.puede_editar_usuarios:
            permisos.append('<span style="background: #17a2b8; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">Editar</span>')
        if obj.puede_eliminar_usuarios:
            permisos.append('<span style="background: #6f42c1; color: white; padding: 2px 6px; border-radius: 3px; font-size: 10px;">Eliminar</span>')
        
        return mark_safe(' '.join(permisos)) if permisos else '-'
    get_permisos_display.short_description = 'Permisos'
    
    def ultimo_acceso_display(self, obj):
        """Mostrar último acceso con formato"""
        if obj.fecha_ultimo_acceso:
            return obj.fecha_ultimo_acceso.strftime('%d/%m/%Y %H:%M')
        return 'Nunca'
    ultimo_acceso_display.short_description = 'Último Acceso'
    ultimo_acceso_display.admin_order_field = 'fecha_ultimo_acceso'
    
    def get_queryset(self, request):
        """Optimizar consultas"""
        return super().get_queryset(request).select_related()
    
    def save_model(self, request, obj, form, change):
        """Personalizar guardado del modelo"""
        if not change:  # Es un nuevo usuario
            # Generar contraseña temporal si no se proporciona
            if not obj.password or obj.password.startswith('pbkdf2_sha256$'):
                from django.utils.crypto import get_random_string
                temp_password = get_random_string(12)
                obj.set_password(temp_password)
                # Aquí podrías enviar el correo con las credenciales
                self.message_user(
                    request, 
                    f'Usuario creado con contraseña temporal: {temp_password}',
                    level='WARNING'
                )
        
        super().save_model(request, obj, form, change)
    
    actions = ['activar_usuarios', 'desactivar_usuarios', 'resetear_passwords']
    
    def activar_usuarios(self, request, queryset):
        """Acción para activar usuarios seleccionados"""
        updated = queryset.update(es_activo=True, is_active=True)
        self.message_user(
            request,
            f'{updated} usuario(s) activado(s) exitosamente.'
        )
    activar_usuarios.short_description = "Activar usuarios seleccionados"
    
    def desactivar_usuarios(self, request, queryset):
        """Acción para desactivar usuarios seleccionados"""
        # No permitir desactivar superusuarios
        superusers = queryset.filter(is_superuser=True)
        if superusers.exists():
            self.message_user(
                request,
                f'No se pueden desactivar superusuarios: {", ".join(superusers.values_list("username", flat=True))}',
                level='ERROR'
            )
            return
        
        updated = queryset.update(es_activo=False, is_active=False)
        self.message_user(
            request,
            f'{updated} usuario(s) desactivado(s) exitosamente.'
        )
    desactivar_usuarios.short_description = "Desactivar usuarios seleccionados"
    
    def resetear_passwords(self, request, queryset):
        """Acción para resetear contraseñas"""
        from django.utils.crypto import get_random_string
        
        for usuario in queryset:
            temp_password = get_random_string(12)
            usuario.set_password(temp_password)
            usuario.save()
            # Aquí podrías enviar el correo con la nueva contraseña
        
        self.message_user(
            request,
            f'Contraseñas reseteadas para {queryset.count()} usuario(s). Se han enviado por correo.',
            level='SUCCESS'
        )
    resetear_passwords.short_description = "Resetear contraseñas de usuarios seleccionados"

@admin.register(LogAcceso)
class LogAccesoAdmin(admin.ModelAdmin):
    """
    Configuración del admin para logs de acceso
    """
    list_display = [
        'usuario', 'fecha_acceso', 'ip_address', 'exito', 'user_agent_short'
    ]
    list_filter = [
        'exito', 'fecha_acceso', 'usuario'
    ]
    search_fields = [
        'usuario__username', 'usuario__first_name', 'usuario__last_name',
        'ip_address', 'user_agent'
    ]
    ordering = ['-fecha_acceso']
    list_per_page = 50
    readonly_fields = ['usuario', 'fecha_acceso', 'ip_address', 'user_agent', 'exito']
    
    fieldsets = (
        (None, {
            'fields': ('usuario', 'fecha_acceso', 'exito')
        }),
        ('Información de Conexión', {
            'fields': ('ip_address', 'user_agent'),
            'classes': ('collapse',)
        }),
    )
    
    def user_agent_short(self, obj):
        """Mostrar user agent truncado"""
        if obj.user_agent:
            return obj.user_agent[:50] + '...' if len(obj.user_agent) > 50 else obj.user_agent
        return '-'
    user_agent_short.short_description = 'User Agent'
    
    def has_add_permission(self, request):
        """No permitir crear logs manualmente"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """No permitir editar logs"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Permitir eliminar logs solo a superusuarios"""
        return request.user.is_superuser
    
    actions = ['limpiar_logs_antiguos']
    
    def limpiar_logs_antiguos(self, request, queryset):
        """Acción para limpiar logs antiguos"""
        from django.utils import timezone
        from datetime import timedelta
        
        # Eliminar logs de más de 90 días
        fecha_limite = timezone.now() - timedelta(days=90)
        logs_eliminados = LogAcceso.objects.filter(fecha_acceso__lt=fecha_limite).count()
        LogAcceso.objects.filter(fecha_acceso__lt=fecha_limite).delete()
        
        self.message_user(
            request,
            f'{logs_eliminados} logs antiguos eliminados exitosamente.'
        )
    limpiar_logs_antiguos.short_description = "Limpiar logs de más de 90 días"

# Personalizar el admin site
admin.site.site_header = "Administración de Olagreetings"
admin.site.site_title = "Admin Olagreetings"
admin.site.index_title = "Panel de Administración"

# Registrar modelos adicionales si es necesario
# admin.site.register(OtroModelo, OtroModeloAdmin) 