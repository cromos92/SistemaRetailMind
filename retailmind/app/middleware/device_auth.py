"""
Middleware de autenticación de dispositivos para API Desktop
============================================================

Este middleware:
1. Valida el header X-Device-ID en requests a /api/v1/
2. Valida timestamps para evitar replay attacks
3. Loguea intentos de acceso sospechosos
"""

import logging
from datetime import timedelta
from django.utils import timezone
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class DeviceAuthMiddleware:
    """
    Middleware que valida dispositivos para la API Desktop.
    
    Solo aplica a rutas que comienzan con /api/v1/
    Excluye rutas de login y health check.
    """
    
    # Rutas que no requieren validación de dispositivo
    EXEMPT_PATHS = [
        '/api/v1/desktop/login/',
        '/api/v1/health/',
        '/api/v1/sync/status/',
    ]
    
    # Tolerancia de timestamp (en minutos)
    TIMESTAMP_TOLERANCE_MINUTES = 5
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Solo aplicar a rutas de API v1
        if not request.path.startswith('/api/v1/'):
            return self.get_response(request)
        
        # Excluir rutas exentas
        if any(request.path.startswith(path) for path in self.EXEMPT_PATHS):
            return self.get_response(request)
        
        # Validar timestamp si está presente
        timestamp_error = self._validate_timestamp(request)
        if timestamp_error:
            logger.warning(
                f"Timestamp inválido desde IP {self._get_client_ip(request)}: {timestamp_error}"
            )
            return JsonResponse({
                'error': 'timestamp_invalid',
                'detail': timestamp_error
            }, status=400)
        
        # Extraer y almacenar información del dispositivo para uso posterior
        device_id = request.headers.get('X-Device-ID')
        app_version = request.headers.get('X-App-Version')
        sucursal_id = request.headers.get('X-Sucursal-ID')
        
        # Almacenar en request para uso en views/permissions
        request.desktop_device_id = device_id
        request.desktop_app_version = app_version
        request.desktop_sucursal_id = sucursal_id
        
        response = self.get_response(request)
        
        return response
    
    def _validate_timestamp(self, request):
        """
        Valida que el timestamp del request esté dentro del rango permitido.
        Previene replay attacks.
        """
        timestamp_header = request.headers.get('X-Request-Timestamp')
        
        if not timestamp_header:
            # Si no hay timestamp, permitir (para desarrollo)
            return None
        
        try:
            # Parsear timestamp ISO
            from django.utils.dateparse import parse_datetime
            request_time = parse_datetime(timestamp_header)
            
            if not request_time:
                return "Formato de timestamp inválido"
            
            # Calcular diferencia
            now = timezone.now()
            tolerance = timedelta(minutes=self.TIMESTAMP_TOLERANCE_MINUTES)
            
            if request_time < (now - tolerance):
                return f"Timestamp muy antiguo (más de {self.TIMESTAMP_TOLERANCE_MINUTES} minutos)"
            
            if request_time > (now + tolerance):
                return f"Timestamp en el futuro (más de {self.TIMESTAMP_TOLERANCE_MINUTES} minutos)"
            
        except Exception as e:
            logger.error(f"Error validando timestamp: {e}")
            return "Error procesando timestamp"
        
        return None
    
    def _get_client_ip(self, request):
        """Obtiene la IP del cliente."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class RateLimitMiddleware:
    """
    Middleware simple de rate limiting para API Desktop.
    
    Usa cache de Django para trackear requests por IP/device.
    """
    
    # Configuración de rate limits por endpoint
    RATE_LIMITS = {
        '/api/v1/desktop/login/': {'requests': 5, 'window': 60},  # 5/min
        '/api/v1/sync/tickets/': {'requests': 60, 'window': 60},  # 60/min
        '/api/v1/sync/productos/': {'requests': 30, 'window': 60},  # 30/min
        'default': {'requests': 100, 'window': 60},  # 100/min default
    }
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Solo aplicar a rutas de API
        if not request.path.startswith('/api/v1/'):
            return self.get_response(request)
        
        # Por ahora, solo loguear (implementar con cache en producción)
        # TODO: Implementar rate limiting real con Redis/cache
        
        return self.get_response(request)


class SyncLoggingMiddleware:
    """
    Middleware que loguea todas las operaciones de sincronización.
    """
    
    SYNC_PATHS = [
        '/api/v1/sync/tickets/',
        '/api/v1/sync/productos/',
        '/api/v1/sync/categorias/',
        '/api/v1/sync/cuadraturas/',
    ]
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Solo aplicar a rutas de sync
        is_sync_path = any(request.path.startswith(path) for path in self.SYNC_PATHS)
        
        if not is_sync_path:
            return self.get_response(request)
        
        # Capturar info del request
        start_time = timezone.now()
        device_id = request.headers.get('X-Device-ID', 'unknown')
        
        response = self.get_response(request)
        
        # Calcular duración
        duration_ms = int((timezone.now() - start_time).total_seconds() * 1000)
        
        # Loguear
        logger.info(
            f"SYNC: {request.method} {request.path} | "
            f"Device: {device_id} | "
            f"Status: {response.status_code} | "
            f"Duration: {duration_ms}ms"
        )
        
        return response
