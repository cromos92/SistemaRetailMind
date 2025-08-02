from django.utils.deprecation import MiddlewareMixin
from django.contrib.auth import get_user
from django.utils import timezone
from .models import LogAcceso
import logging

logger = logging.getLogger(__name__)

class LogAccesoMiddleware(MiddlewareMixin):
    """
    Middleware para registrar automáticamente los accesos de usuarios
    """
    
    def process_request(self, request):
        """Procesar cada request y registrar el acceso"""
        # Solo registrar accesos autenticados
        if request.user.is_authenticated:
            try:
                # Obtener información del request
                ip_address = self.get_client_ip(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')
                
                # Registrar el acceso
                LogAcceso.objects.create(
                    usuario=request.user,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    exito=True
                )
                
                # Actualizar último acceso del usuario
                request.user.actualizar_ultimo_acceso()
                
            except Exception as e:
                logger.error(f"Error al registrar log de acceso: {e}")
    
    def process_exception(self, request, exception):
        """Procesar excepciones y registrar accesos fallidos"""
        if request.user.is_authenticated:
            try:
                ip_address = self.get_client_ip(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')
                
                LogAcceso.objects.create(
                    usuario=request.user,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    exito=False
                )
            except Exception as e:
                logger.error(f"Error al registrar log de excepción: {e}")
    
    def get_client_ip(self, request):
        """Obtener la IP real del cliente"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

class SeguridadMiddleware(MiddlewareMixin):
    """
    Middleware para medidas de seguridad adicionales
    """
    
    def process_request(self, request):
        """Aplicar medidas de seguridad"""
        # Headers de seguridad
        response = self.get_response(request)
        
        # Prevenir clickjacking
        response['X-Frame-Options'] = 'DENY'
        
        # Prevenir MIME type sniffing
        response['X-Content-Type-Options'] = 'nosniff'
        
        # Prevenir XSS
        response['X-XSS-Protection'] = '1; mode=block'
        
        # Referrer Policy
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Content Security Policy (básico)
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response['Content-Security-Policy'] = csp
        
        return response

class BloqueoIntentosFallidosMiddleware(MiddlewareMixin):
    """
    Middleware para bloquear usuarios después de múltiples intentos fallidos
    """
    
    def process_request(self, request):
        """Verificar intentos fallidos de login"""
        if request.path == '/login/' and request.method == 'POST':
            ip_address = self.get_client_ip(request)
            
            # Verificar intentos fallidos recientes
            intentos_recientes = LogAcceso.objects.filter(
                ip_address=ip_address,
                exito=False,
                fecha_acceso__gte=timezone.now() - timezone.timedelta(minutes=15)
            ).count()
            
            # Si hay más de 5 intentos fallidos, bloquear temporalmente
            if intentos_recientes >= 5:
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden(
                    "Demasiados intentos fallidos. Intenta nuevamente en 15 minutos."
                )
    
    def get_client_ip(self, request):
        """Obtener la IP real del cliente"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

class AuditoriaMiddleware(MiddlewareMixin):
    """
    Middleware para auditoría de acciones importantes
    """
    
    def process_request(self, request):
        """Registrar acciones importantes"""
        if request.user.is_authenticated:
            # Lista de acciones importantes a auditar
            acciones_importantes = [
                '/usuarios/crear/',
                '/usuarios/gestionar/',
                '/usuarios/eliminar/',
                '/usuarios/resetear-password/',
                '/admin/usuarios/usuario/add/',
                '/admin/usuarios/usuario/',
            ]
            
            if any(accion in request.path for accion in acciones_importantes):
                try:
                    logger.info(
                        f"Acción importante: {request.user.username} - {request.method} {request.path} - IP: {self.get_client_ip(request)}"
                    )
                except Exception as e:
                    logger.error(f"Error al registrar auditoría: {e}")
    
    def get_client_ip(self, request):
        """Obtener la IP real del cliente"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip 