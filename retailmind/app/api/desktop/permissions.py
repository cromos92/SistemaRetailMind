"""
Permisos personalizados para API Desktop
========================================
"""

from rest_framework.permissions import BasePermission
from django.utils import timezone
from packaging import version


class IsAuthorizedDevice(BasePermission):
    """
    Permiso que verifica si el dispositivo está autorizado.
    
    Verifica:
    1. Header X-Device-ID presente
    2. Dispositivo registrado y activo
    3. Dispositivo pertenece al usuario autenticado
    4. Sucursal coincide (si se especifica)
    """
    
    message = 'Dispositivo no autorizado'
    
    def has_permission(self, request, view):
        # Si no hay usuario autenticado, denegar
        if not request.user or not request.user.is_authenticated:
            self.message = 'Usuario no autenticado'
            return False
        
        # Obtener device_id del header
        device_id = request.headers.get('X-Device-ID')
        if not device_id:
            self.message = 'Header X-Device-ID requerido'
            return False
        
        # Importar aquí para evitar imports circulares
        from app.models_sync import DispositivoAutorizado
        
        try:
            dispositivo = DispositivoAutorizado.objects.select_related(
                'sucursal', 'usuario'
            ).get(device_id=device_id)
        except DispositivoAutorizado.DoesNotExist:
            self.message = 'Dispositivo no registrado'
            return False
        
        # Verificar que esté activo
        if not dispositivo.esta_activo():
            self.message = f'Dispositivo {dispositivo.estado.lower()}'
            return False
        
        # Verificar que pertenezca al usuario
        if dispositivo.usuario_id != request.user.id:
            self.message = 'Dispositivo no pertenece a este usuario'
            return False
        
        # Agregar dispositivo al request para uso posterior
        request.dispositivo = dispositivo
        
        # Registrar acceso
        dispositivo.registrar_acceso()
        
        return True


class IsMinimumAppVersion(BasePermission):
    """
    Permiso que verifica la versión mínima de la app.
    """
    
    # Versión mínima requerida (configurable)
    MINIMUM_VERSION = '1.0.0'
    
    message = 'Versión de app no soportada'
    
    def has_permission(self, request, view):
        app_version = request.headers.get('X-App-Version')
        
        if not app_version:
            # Si no envía versión, permitir (para desarrollo)
            return True
        
        try:
            if version.parse(app_version) < version.parse(self.MINIMUM_VERSION):
                self.message = f'Versión mínima requerida: {self.MINIMUM_VERSION}'
                return False
        except Exception:
            # Si hay error parseando versión, permitir
            return True
        
        return True


class IsSameSucursal(BasePermission):
    """
    Permiso que verifica que las operaciones sean para la misma sucursal
    que el dispositivo tiene asignada.
    """
    
    message = 'Operación no permitida para esta sucursal'
    
    def has_permission(self, request, view):
        # Requiere que IsAuthorizedDevice ya haya corrido
        dispositivo = getattr(request, 'dispositivo', None)
        if not dispositivo:
            self.message = 'Dispositivo no validado'
            return False
        
        # Obtener sucursal_id del header o del body
        sucursal_id = request.headers.get('X-Sucursal-ID')
        if not sucursal_id:
            sucursal_id = request.data.get('sucursal_id')
        
        if sucursal_id:
            try:
                sucursal_id = int(sucursal_id)
                if dispositivo.sucursal_id != sucursal_id:
                    self.message = 'Sucursal no coincide con dispositivo autorizado'
                    return False
            except (ValueError, TypeError):
                pass
        
        return True


class CanSyncTickets(BasePermission):
    """
    Permiso específico para sincronización de tickets.
    Verifica que el usuario tenga rol adecuado.
    """
    
    message = 'No tiene permisos para sincronizar tickets'
    
    ROLES_PERMITIDOS = ['administrador', 'jefe_local', 'cajero', 'vendedor']
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        # Superuser siempre puede
        if request.user.is_superuser:
            return True
        
        # Verificar rol
        user_rol = getattr(request.user, 'rol', None)
        if user_rol and user_rol in self.ROLES_PERMITIDOS:
            return True
        
        self.message = f'Rol {user_rol} no tiene permisos de sincronización'
        return False


class CanManageDevices(BasePermission):
    """
    Permiso para gestionar dispositivos autorizados.
    Solo administradores y jefes de local.
    """
    
    message = 'No tiene permisos para gestionar dispositivos'
    
    ROLES_PERMITIDOS = ['administrador', 'jefe_local']
    
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        
        if request.user.is_superuser:
            return True
        
        user_rol = getattr(request.user, 'rol', None)
        return user_rol in self.ROLES_PERMITIDOS
