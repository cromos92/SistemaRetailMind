"""
Views para autenticación y configuración de App Desktop
========================================================
"""

import jwt
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.db import transaction

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken

from app.models import Sucursal, Vendedor
from app.models_sync import DispositivoAutorizado, RefreshTokenDesktop, SyncLog

from .serializers import (
    DesktopLoginSerializer,
    DesktopRefreshSerializer,
    SucursalConfigSerializer,
    VendedorSerializer,
    DispositivoSerializer,
    LoginResponseSerializer,
    SyncStatusSerializer,
)
from .permissions import IsAuthorizedDevice, IsMinimumAppVersion
from .throttling import DesktopAuthRateThrottle


class DesktopLoginView(APIView):
    """
    POST /api/v1/desktop/login/
    
    Autentica un dispositivo desktop y retorna tokens JWT.
    
    Input:
        - username: str
        - password: str
        - device_id: UUID
        - sucursal_id: int
        - device_name: str (opcional)
        - sistema_operativo: str (opcional)
        - version_app: str (opcional)
    
    Output:
        - token: JWT access token
        - refresh_token: token de refresco
        - expires_at: fecha de expiración
        - sucursal: datos de la sucursal
        - vendedor: datos del vendedor (si existe)
        - dispositivo: datos del dispositivo
    """
    
    authentication_classes = []  # Sin autenticación requerida
    permission_classes = [AllowAny]
    throttle_classes = [DesktopAuthRateThrottle]  # anti fuerza-bruta

    def post(self, request):
        serializer = DesktopLoginSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(
                {'error': 'validation_error', 'details': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user = serializer.validated_data['user']
        sucursal = serializer.validated_data['sucursal']
        
        # Si no se envía device_id, generar uno automáticamente
        device_id = serializer.validated_data.get('device_id')
        if not device_id:
            device_id = uuid.uuid4()
        
        device_name = serializer.validated_data.get('device_name', f'Dispositivo {str(device_id)[:8]}')
        
        with transaction.atomic():
            # Buscar o crear dispositivo autorizado
            dispositivo, created = DispositivoAutorizado.objects.get_or_create(
                device_id=device_id,
                defaults={
                    'sucursal': sucursal,
                    'usuario': user,
                    'nombre': device_name,
                    'sistema_operativo': serializer.validated_data.get('sistema_operativo'),
                    'version_app': serializer.validated_data.get('version_app'),
                }
            )
            
            if not created:
                # Actualizar información del dispositivo existente
                dispositivo.usuario = user
                dispositivo.sucursal = sucursal
                dispositivo.nombre = device_name
                if serializer.validated_data.get('sistema_operativo'):
                    dispositivo.sistema_operativo = serializer.validated_data['sistema_operativo']
                if serializer.validated_data.get('version_app'):
                    dispositivo.version_app = serializer.validated_data['version_app']
                
                # Verificar que esté activo
                if not dispositivo.esta_activo():
                    return Response(
                        {'error': 'device_inactive', 'detail': f'Dispositivo {dispositivo.estado.lower()}'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            
            dispositivo.registrar_acceso()
            
            # Generar tokens JWT
            refresh = RefreshToken.for_user(user)
            
            # Agregar claims personalizados
            refresh['device_id'] = str(device_id)
            refresh['sucursal_id'] = sucursal.id
            refresh['rol'] = getattr(user, 'rol', 'vendedor')
            
            access_token = str(refresh.access_token)
            
            # Calcular expiración (7 días para desktop)
            expires_at = timezone.now() + timedelta(days=7)
            
            # Crear refresh token en BD para tracking
            refresh_token_obj, refresh_token_plain = RefreshTokenDesktop.crear_token(
                dispositivo=dispositivo,
                usuario=user,
                dias_expiracion=30,
                ip=self._get_client_ip(request),
                user_agent=request.headers.get('User-Agent')
            )
            
            # Buscar vendedor asociado al usuario (si existe)
            vendedor = None
            try:
                vendedor = Vendedor.objects.filter(
                    correo=user.email,
                    activo=True
                ).first()
                if not vendedor:
                    # Buscar por RUT si el usuario tiene
                    if hasattr(user, 'rut') and user.rut:
                        vendedor = Vendedor.objects.filter(
                            rut=user.rut,
                            activo=True
                        ).first()
            except Exception:
                pass
            
            # Registrar en log
            SyncLog.objects.create(
                tipo='status_check',
                dispositivo=dispositivo,
                sucursal=sucursal,
                usuario=user,
                estado='COMPLETADO',
                exitoso=True,
                detalles={'action': 'login', 'device_created': created}
            )
        
        response_data = {
            # === Campos principales que espera NEXO POS ===
            'token': access_token,
            'refresh_token': refresh_token_plain,
            'expires_at': expires_at.isoformat(),
            'user_id': user.id,
            'user_name': user.get_full_name() or user.username,
            'sucursal_id': sucursal.id,
            'sucursal_nombre': sucursal.alias,
            'device_id': str(dispositivo.device_id),
            
            # === Datos extendidos ===
            'sucursal': SucursalConfigSerializer(sucursal).data,
            'vendedor': VendedorSerializer(vendedor).data if vendedor else None,
            'vendedor_id': vendedor.id if vendedor else None,
            'vendedor_nombre': vendedor.nombre if vendedor else user.get_full_name(),
            'dispositivo': DispositivoSerializer(dispositivo).data,
            'server_time': timezone.now().isoformat(),
            
            # === Configuración para la app ===
            'config': {
                'max_tickets_offline': dispositivo.max_tickets_offline,
                'permite_venta_sin_stock': False,
                'max_descuento_porcentaje': 50,
            }
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class DesktopRefreshView(APIView):
    """
    POST /api/v1/desktop/refresh/
    
    Refresca el access token usando el refresh token.
    Implementa rotación de tokens para seguridad.
    
    Input:
        - refresh_token: str
    
    Output:
        - token: nuevo JWT access token
        - refresh_token: nuevo refresh token (rotación)
        - expires_at: nueva fecha de expiración
    """
    
    authentication_classes = []  # Sin autenticación requerida
    permission_classes = [AllowAny]
    
    def post(self, request):
        serializer = DesktopRefreshSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(
                {'error': 'validation_error', 'details': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        refresh_token_plain = serializer.validated_data['refresh_token']
        
        # Validar y rotar token
        nuevo_token, nuevo_token_plain, error = RefreshTokenDesktop.validar_y_rotar(
            refresh_token_plain
        )
        
        if error:
            return Response(
                {'error': 'token_invalid', 'detail': error},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        # Generar nuevo access token JWT
        refresh = RefreshToken.for_user(nuevo_token.usuario)
        refresh['device_id'] = str(nuevo_token.dispositivo.device_id)
        refresh['sucursal_id'] = nuevo_token.dispositivo.sucursal_id
        refresh['rol'] = getattr(nuevo_token.usuario, 'rol', 'vendedor')
        
        access_token = str(refresh.access_token)
        expires_at = timezone.now() + timedelta(days=7)
        
        return Response({
            'token': access_token,
            'refresh_token': nuevo_token_plain,
            'expires_at': expires_at.isoformat(),
        }, status=status.HTTP_200_OK)


class DesktopLogoutView(APIView):
    """
    POST /api/v1/desktop/logout/
    
    Invalida el token y refresh token del dispositivo.
    """
    
    permission_classes = [IsAuthenticated, IsAuthorizedDevice]
    
    def post(self, request):
        dispositivo = request.dispositivo
        
        # Revocar todos los refresh tokens del dispositivo
        RefreshTokenDesktop.objects.filter(
            dispositivo=dispositivo,
            revocado=False
        ).update(revocado=True)
        
        # Registrar logout
        SyncLog.objects.create(
            tipo='status_check',
            dispositivo=dispositivo,
            sucursal=dispositivo.sucursal,
            usuario=request.user,
            estado='COMPLETADO',
            exitoso=True,
            detalles={'action': 'logout'}
        )
        
        return Response({
            'success': True,
            'message': 'Sesión cerrada correctamente'
        }, status=status.HTTP_200_OK)


class SucursalConfigView(APIView):
    """
    GET /api/v1/desktop/sucursal/
    
    Obtiene la configuración completa de la sucursal del dispositivo.
    """
    
    permission_classes = [IsAuthenticated, IsAuthorizedDevice]
    
    def get(self, request):
        dispositivo = request.dispositivo
        sucursal = dispositivo.sucursal
        
        # Obtener configuración extendida
        config_data = SucursalConfigSerializer(sucursal).data
        
        # Agregar configuración adicional
        config_data['configuracion'] = {
            'max_tickets_offline': dispositivo.max_tickets_offline,
            'ultima_sincronizacion': dispositivo.ultima_sincronizacion,
            # Agregar más configuraciones según necesites
        }
        
        return Response(config_data, status=status.HTTP_200_OK)


class SyncStatusView(APIView):
    """
    GET /api/v1/sync/status/
    
    Obtiene el estado de sincronización y información del servidor.
    No requiere autenticación completa (útil para health checks).
    """
    
    authentication_classes = []  # Sin autenticación requerida
    permission_classes = [AllowAny]
    
    def get(self, request):
        device_id = request.headers.get('X-Device-ID')
        last_sync = None
        pending_updates = {}
        
        if device_id:
            try:
                dispositivo = DispositivoAutorizado.objects.get(device_id=device_id)
                last_sync = dispositivo.ultima_sincronizacion
                
                # Calcular actualizaciones pendientes
                from app.models import Producto, Categoria
                
                if last_sync:
                    pending_updates = {
                        'productos': Producto.objects.filter(
                            sucursal=dispositivo.sucursal
                        ).count(),  # TODO: filtrar por updated_at > last_sync
                        'categorias': 0,
                    }
            except DispositivoAutorizado.DoesNotExist:
                pass
        
        response_data = {
            'server_time': timezone.now().isoformat(),
            'last_sync': last_sync.isoformat() if last_sync else None,
            'pending_updates': pending_updates,
            'version_app_minima': '1.0.0',
            'message': 'Servidor operativo'
        }
        
        return Response(response_data, status=status.HTTP_200_OK)


class HealthCheckView(APIView):
    """
    GET /api/v1/health/
    
    Health check básico del servidor.
    """
    
    authentication_classes = []  # Sin autenticación requerida
    permission_classes = [AllowAny]
    
    def get(self, request):
        from django.db import connection
        
        # Verificar conexión a BD
        db_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            db_ok = False
        
        return Response({
            'status': 'healthy' if db_ok else 'degraded',
            'database': 'connected' if db_ok else 'disconnected',
            'timestamp': timezone.now().isoformat(),
            'version': '1.0.0',
        }, status=status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE)


class SucursalesDisponiblesView(APIView):
    """
    POST /api/v1/desktop/sucursales/
    
    Obtiene las sucursales disponibles para un usuario (antes del login completo).
    Útil para mostrar selector de sucursales en la app.
    
    Input:
        - username: str
        - password: str
    
    Output:
        - sucursales: [{id, nombre, direccion, es_default}]
        - user_name: str
    """
    
    authentication_classes = []  # Sin autenticación requerida
    permission_classes = [AllowAny]
    throttle_classes = [DesktopAuthRateThrottle]  # anti fuerza-bruta

    def post(self, request):
        from django.contrib.auth import authenticate
        from app.models import EmpresaUser, Vendedor
        
        username = request.data.get('username')
        password = request.data.get('password')
        
        if not username or not password:
            return Response({
                'error': 'validation_error',
                'detail': 'Username y password requeridos'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Autenticar
        user = authenticate(username=username, password=password)
        if not user:
            return Response({
                'error': 'auth_error',
                'detail': 'Credenciales inválidas'
            }, status=status.HTTP_401_UNAUTHORIZED)
        
        sucursales = []
        sucursal_default_id = None
        
        # 1. Buscar en EmpresaUser
        empresa_users = EmpresaUser.objects.filter(
            user=user,
            status=True
        ).select_related('sucursal', 'empresa')
        
        for eu in empresa_users:
            if eu.sucursal:
                sucursales.append({
                    'id': eu.sucursal.id,
                    'nombre': eu.sucursal.alias,
                    'direccion': eu.sucursal.direccion,
                    'empresa': eu.empresa.nombre if eu.empresa else None,
                    'es_default': eu.active,  # Si está marcado como activo
                })
                if eu.active:
                    sucursal_default_id = eu.sucursal.id
        
        # 2. Buscar en Vendedor (sucursales asignadas)
        vendedor = Vendedor.objects.filter(
            correo=user.email,
            activo=True
        ).prefetch_related('sucursales').first()
        
        if vendedor:
            for suc in vendedor.sucursales.all():
                # Evitar duplicados
                if not any(s['id'] == suc.id for s in sucursales):
                    sucursales.append({
                        'id': suc.id,
                        'nombre': suc.alias,
                        'direccion': suc.direccion,
                        'empresa': suc.empresa.nombre if suc.empresa else None,
                        'es_default': False,
                    })
        
        # 3. Si es superuser/admin, mostrar todas las sucursales
        if getattr(user, 'rol', '') == 'administrador':
            from app.models import Sucursal
            todas = Sucursal.objects.all().select_related('empresa')
            for suc in todas:
                if not any(s['id'] == suc.id for s in sucursales):
                    sucursales.append({
                        'id': suc.id,
                        'nombre': suc.alias,
                        'direccion': suc.direccion,
                        'empresa': suc.empresa.nombre if suc.empresa else None,
                        'es_default': False,
                    })
        
        # Si solo hay una sucursal, marcarla como default
        if len(sucursales) == 1:
            sucursales[0]['es_default'] = True
            sucursal_default_id = sucursales[0]['id']
        
        return Response({
            'user_id': user.id,
            'user_name': user.get_full_name() or user.username,
            'sucursales': sucursales,
            'sucursal_default_id': sucursal_default_id,
            'total': len(sucursales),
        }, status=status.HTTP_200_OK)
