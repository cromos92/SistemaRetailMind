"""
Serializers para autenticación y configuración de App Desktop
=============================================================
"""

from rest_framework import serializers
from django.contrib.auth import authenticate
from django.utils import timezone

from app.models import Sucursal, Vendedor, EmpresaUser
from app.models_sync import DispositivoAutorizado


class DesktopLoginSerializer(serializers.Serializer):
    """
    Serializer para login de app desktop.
    
    El sistema obtiene la sucursal de las siguientes formas:
    1. Si se envía sucursal_id: valida que el usuario tenga acceso via EmpresaUser
    2. Si NO se envía: obtiene la sucursal asignada al usuario en EmpresaUser
    """
    username = serializers.CharField(
        max_length=150,
        help_text='Nombre de usuario'
    )
    password = serializers.CharField(
        write_only=True,
        help_text='Contraseña'
    )
    device_id = serializers.UUIDField(
        required=False,  # Opcional - se genera automáticamente si no se envía
        allow_null=True,
        help_text='UUID único del dispositivo (opcional - se genera si no se envía)'
    )
    sucursal_id = serializers.IntegerField(
        required=False,  # Ahora es opcional
        allow_null=True,
        help_text='ID de la sucursal (opcional - se obtiene del usuario si no se envía)'
    )
    device_name = serializers.CharField(
        max_length=100,
        required=False,
        help_text='Nombre descriptivo del dispositivo (ej: Caja 1)'
    )
    sistema_operativo = serializers.CharField(
        max_length=100,
        required=False,
        help_text='Sistema operativo del dispositivo'
    )
    version_app = serializers.CharField(
        max_length=20,
        required=False,
        help_text='Versión de la app desktop'
    )
    
    def validate(self, data):
        username = data.get('username')
        password = data.get('password')
        sucursal_id = data.get('sucursal_id')
        
        # Autenticar usuario
        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError({
                'credentials': 'Credenciales inválidas'
            })
        
        if not user.is_active:
            raise serializers.ValidationError({
                'user': 'Usuario inactivo'
            })
        
        # === OBTENER SUCURSAL DEL USUARIO ===
        # Buscar en EmpresaUser la relación usuario-sucursal
        empresa_user = EmpresaUser.objects.filter(
            user=user,
            status=True  # Solo activos
        ).select_related('sucursal', 'empresa').first()
        
        sucursal = None
        
        if sucursal_id:
            # Si se envía sucursal_id, validar que exista y que el usuario tenga acceso
            try:
                sucursal = Sucursal.objects.get(id=sucursal_id)
            except Sucursal.DoesNotExist:
                raise serializers.ValidationError({
                    'sucursal_id': 'Sucursal no encontrada'
                })
            
            # Validar que el usuario tenga acceso a esta sucursal
            if empresa_user and empresa_user.sucursal:
                if empresa_user.sucursal_id != sucursal_id:
                    # Verificar si es superuser o admin
                    if getattr(user, 'rol', '') != 'administrador':
                        raise serializers.ValidationError({
                            'sucursal_id': f'No tienes acceso a esta sucursal. Tu sucursal es: {empresa_user.sucursal.alias}'
                        })
        else:
            # Si NO se envía sucursal_id, obtener la del usuario
            if empresa_user and empresa_user.sucursal:
                sucursal = empresa_user.sucursal
            else:
                # Buscar en Vendedor si tiene sucursales asignadas
                vendedor = Vendedor.objects.filter(
                    correo=user.email,
                    activo=True
                ).first()
                
                if vendedor:
                    sucursal = vendedor.sucursales.first()
                
                if not sucursal:
                    raise serializers.ValidationError({
                        'sucursal_id': 'Usuario no tiene sucursal asignada. Contacta al administrador.'
                    })
        
        data['user'] = user
        data['sucursal'] = sucursal
        data['empresa_user'] = empresa_user
        
        return data


class DesktopRefreshSerializer(serializers.Serializer):
    """
    Serializer para refresh de token.
    """
    refresh_token = serializers.CharField(
        help_text='Token de refresco'
    )


class SucursalConfigSerializer(serializers.ModelSerializer):
    """
    Serializer para configuración de sucursal.
    """
    empresa_nombre = serializers.CharField(source='empresa.nombre', read_only=True)
    empresa_rut = serializers.CharField(source='empresa.rut', read_only=True)
    empresa_razon_social = serializers.CharField(source='empresa.razon_social', read_only=True)
    empresa_giro = serializers.CharField(source='empresa.giro', read_only=True)
    empresa_direccion = serializers.CharField(source='empresa.direccion', read_only=True)
    empresa_comuna = serializers.CharField(source='empresa.comuna', read_only=True)
    empresa_ciudad = serializers.CharField(source='empresa.ciudad', read_only=True)
    
    class Meta:
        model = Sucursal
        fields = [
            'id',
            'alias',
            'direccion',
            'tipo_sucursal',
            'es_centro_distribucion',
            # Empresa
            'empresa_nombre',
            'empresa_rut',
            'empresa_razon_social',
            'empresa_giro',
            'empresa_direccion',
            'empresa_comuna',
            'empresa_ciudad',
        ]


class VendedorSerializer(serializers.ModelSerializer):
    """
    Serializer básico para vendedor.
    """
    class Meta:
        model = Vendedor
        fields = [
            'id',
            'codigo_vendedor',
            'nombre',
            'rut',
            'correo',
            'comision',
            'activo',
        ]


class DispositivoSerializer(serializers.ModelSerializer):
    """
    Serializer para dispositivo autorizado.
    """
    sucursal_nombre = serializers.CharField(source='sucursal.alias', read_only=True)
    
    class Meta:
        model = DispositivoAutorizado
        fields = [
            'device_id',
            'nombre',
            'sucursal',
            'sucursal_nombre',
            'estado',
            'activo',
            'version_app',
            'ultimo_acceso',
            'ultima_sincronizacion',
            'max_tickets_offline',
        ]
        read_only_fields = ['device_id', 'ultimo_acceso', 'ultima_sincronizacion']


class LoginResponseSerializer(serializers.Serializer):
    """
    Serializer para respuesta de login.
    """
    token = serializers.CharField(help_text='JWT Access Token')
    refresh_token = serializers.CharField(help_text='Refresh Token')
    expires_at = serializers.DateTimeField(help_text='Fecha de expiración del token')
    sucursal = SucursalConfigSerializer()
    vendedor = VendedorSerializer(allow_null=True)
    dispositivo = DispositivoSerializer()
    server_time = serializers.DateTimeField(help_text='Hora del servidor')


class SyncStatusSerializer(serializers.Serializer):
    """
    Serializer para estado de sincronización.
    """
    server_time = serializers.DateTimeField()
    last_sync = serializers.DateTimeField(allow_null=True)
    pending_updates = serializers.DictField()
    version_app_minima = serializers.CharField()
    message = serializers.CharField(required=False)
