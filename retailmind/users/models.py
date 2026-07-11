from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
import uuid
import re
import os

def user_profile_photo_path(instance, filename):
    """Genera la ruta para guardar las fotos de perfil"""
    ext = filename.split('.')[-1]
    filename = f"perfil_{instance.id}_{timezone.now().strftime('%Y%m%d%H%M%S')}.{ext}"
    return os.path.join('usuarios', 'fotos_perfil', filename)

class Usuario(AbstractUser):
    """
    Modelo de Usuario personalizado para RetailMind
    Migrado desde olagreetings_user_management
    """
    # Definición de roles
    # Nota: is_superuser es un campo separado de Django, no un rol
    ROLES = [
        ('administrador', 'Administrador'),
        ('administracion', 'Administración'),
        ('jefe_local', 'Jefe Local'),
        ('cajero', 'Cajero'),
        ('vendedor', 'Vendedor'),
    ]
    
    # Campos básicos
    rut = models.CharField(max_length=12, unique=True, null=True, blank=True, verbose_name="RUT")
    telefono = models.CharField(max_length=15, null=True, blank=True, verbose_name="Teléfono")
    direccion = models.TextField(null=True, blank=True, verbose_name="Dirección")
    fecha_nacimiento = models.DateField(null=True, blank=True, verbose_name="Fecha de Nacimiento")
    
    # Foto de perfil
    foto_perfil = models.ImageField(
        upload_to=user_profile_photo_path,
        null=True,
        blank=True,
        verbose_name="Foto de Perfil",
        help_text="Imagen de perfil del usuario"
    )
    
    # Campos de empresa
    empresa = models.CharField(max_length=100, null=True, blank=True, verbose_name="Empresa")
    cargo = models.CharField(max_length=100, null=True, blank=True, verbose_name="Cargo")
    departamento = models.CharField(max_length=100, null=True, blank=True, verbose_name="Departamento")
    rol = models.CharField(max_length=50, choices=ROLES, default='vendedor', verbose_name="Rol")
    
    # Campos de estado
    es_activo = models.BooleanField(default=True, verbose_name="Usuario Activo")
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    fecha_ultimo_acceso = models.DateTimeField(null=True, blank=True, verbose_name="Último Acceso")
    
    # Campos de seguridad
    token_reset_password = models.UUIDField(default=uuid.uuid4, editable=False)
    fecha_token_reset = models.DateTimeField(null=True, blank=True)
    
    # Campos de autenticación 2FA
    requiere_2fa = models.BooleanField(default=False, verbose_name="Requiere Autenticación en 2 Pasos")
    codigo_2fa = models.CharField(max_length=6, null=True, blank=True, verbose_name="Código 2FA Temporal")
    fecha_codigo_2fa = models.DateTimeField(null=True, blank=True, verbose_name="Fecha Generación Código 2FA")
    
    # Campo para forzar cambio de contraseña
    requiere_cambio_password = models.BooleanField(default=False, verbose_name="Requiere Cambio de Contraseña")

    # PIN de autorización para administradores (cambios fuera de plazo y otras operaciones especiales)
    pin_autorizacion = models.CharField(
        max_length=128, null=True, blank=True,
        verbose_name="PIN de Autorización (hasheado)",
        help_text="PIN de 6 dígitos para autorizar operaciones especiales. Solo administradores/jefes."
    )
    pin_autorizacion_actualizado = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Última Actualización del PIN"
    )

    # Campos de permisos
    puede_crear_usuarios = models.BooleanField(default=False, verbose_name="Puede Crear Usuarios")
    puede_editar_usuarios = models.BooleanField(default=False, verbose_name="Puede Editar Usuarios")
    puede_eliminar_usuarios = models.BooleanField(default=False, verbose_name="Puede Eliminar Usuarios")
    
    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        ordering = ['username']
    
    def __str__(self):
        return f"{self.get_full_name()} ({self.username})"
    
    def get_full_name(self):
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.username
    
    def validar_rut(self):
        """Valida el RUT chileno"""
        if not self.rut:
            return False, "RUT no proporcionado"
        
        try:
            # Limpiar el RUT de puntos y guiones
            rut_limpio = re.sub(r'[.-]', '', self.rut.upper())
            
            # Verificar formato básico
            if not re.match(r'^\d{7,8}[0-9K]$', rut_limpio):
                return False, "El RUT debe tener 7 u 8 dígitos seguidos de un dígito verificador (0-9 o K)"
            
            # Separar número y dígito verificador
            numero = rut_limpio[:-1]
            dv = rut_limpio[-1]
            
            # Calcular dígito verificador
            suma = 0
            multiplicador = 2
            
            for digito in reversed(numero):
                suma += int(digito) * multiplicador
                multiplicador = multiplicador + 1 if multiplicador < 7 else 2
            
            # Calcular dígito verificador esperado
            resto = suma % 11
            dv_esperado = 11 - resto if resto != 0 else 0
            
            # Convertir a string
            if dv_esperado == 10:
                dv_esperado = 'K'
            else:
                dv_esperado = str(dv_esperado)
            
            # Comparar
            if dv == dv_esperado:
                return True, ""
            else:
                return False, f"El dígito verificador es incorrecto. Debería ser {dv_esperado}"
                
        except Exception as e:
            return False, f"Error al validar RUT: {str(e)}"
    
    def generar_token_reset(self):
        """Genera un nuevo token para reset de contraseña"""
        self.token_reset_password = uuid.uuid4()
        self.fecha_token_reset = timezone.now()
        self.save()
        return self.token_reset_password
    
    def token_valido(self, horas_expiracion=24):
        """Verifica si el token de reset es válido"""
        if not self.fecha_token_reset:
            return False
        
        tiempo_expiracion = timezone.now() - timezone.timedelta(hours=horas_expiracion)
        return self.fecha_token_reset > tiempo_expiracion
    
    def actualizar_ultimo_acceso(self):
        """Actualiza la fecha del último acceso"""
        self.fecha_ultimo_acceso = timezone.now()
        self.save(update_fields=['fecha_ultimo_acceso'])
    
    def tiene_permiso_usuarios(self, permiso):
        """Verifica si el usuario tiene un permiso específico de gestión de usuarios"""
        if self.rol == 'administrador':
            return True

        permisos = {
            'crear': self.puede_crear_usuarios,
            'editar': self.puede_editar_usuarios,
            'eliminar': self.puede_eliminar_usuarios,
        }
        
        return permisos.get(permiso, False)
    
    def generar_codigo_2fa(self):
        """Genera un código 2FA de 6 dígitos"""
        import random
        codigo = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        self.codigo_2fa = codigo
        self.fecha_codigo_2fa = timezone.now()
        self.save(update_fields=['codigo_2fa', 'fecha_codigo_2fa'])
        return codigo
    
    def validar_codigo_2fa(self, codigo, minutos_expiracion=10):
        """Valida el código 2FA"""
        if not self.codigo_2fa or not self.fecha_codigo_2fa:
            return False
        
        # Verificar expiración
        tiempo_expiracion = timezone.now() - timezone.timedelta(minutes=minutos_expiracion)
        if self.fecha_codigo_2fa < tiempo_expiracion:
            return False
        
        # Verificar código
        return self.codigo_2fa == codigo
    
    def generar_password_temporal(self):
        """
        Genera una contraseña temporal de 6 dígitos numéricos.
        Establece que el usuario debe cambiar la contraseña al iniciar sesión.
        
        Returns:
            str: Contraseña temporal de 6 dígitos
        """
        import random
        # Generar 6 dígitos numéricos
        password_temporal = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        
        # Establecer la contraseña
        self.set_password(password_temporal)
        
        # Marcar que requiere cambio de contraseña
        self.requiere_cambio_password = True
        
        # Actualizar fecha del token
        self.fecha_token_reset = timezone.now()
        
        self.save()
        
        return password_temporal
    
    # ===== PIN de Autorización (solo admins) =====
    ROLES_CON_PIN = ('administrador', 'administracion', 'jefe_local')

    def puede_tener_pin_autorizacion(self):
        """Solo administradores/jefes pueden configurar un PIN de autorización."""
        return self.rol in self.ROLES_CON_PIN

    def set_pin_autorizacion(self, pin):
        """Hashea y guarda el PIN. Valida que sean 6 dígitos numéricos y que el rol lo permita."""
        from django.contrib.auth.hashers import make_password
        if not self.puede_tener_pin_autorizacion():
            raise ValueError('Solo administradores o jefes de local pueden tener PIN de autorización')
        if not pin or not re.match(r'^\d{6}$', str(pin)):
            raise ValueError('El PIN debe tener exactamente 6 dígitos numéricos')
        self.pin_autorizacion = make_password(str(pin))
        self.pin_autorizacion_actualizado = timezone.now()
        self.save(update_fields=['pin_autorizacion', 'pin_autorizacion_actualizado'])

    def quitar_pin_autorizacion(self):
        """Elimina el PIN del usuario."""
        self.pin_autorizacion = None
        self.pin_autorizacion_actualizado = timezone.now()
        self.save(update_fields=['pin_autorizacion', 'pin_autorizacion_actualizado'])

    def verificar_pin_autorizacion(self, pin):
        """Verifica si el PIN provisto coincide con el almacenado."""
        from django.contrib.auth.hashers import check_password
        if not self.pin_autorizacion or not pin:
            return False
        return check_password(str(pin), self.pin_autorizacion)

    @classmethod
    def buscar_admin_por_pin(cls, pin):
        """
        Busca un administrador activo cuyo PIN coincida.
        Retorna el primer Usuario que matchea o None.
        El PIN debe ser de exactamente 6 dígitos.
        """
        if not pin or not re.match(r'^\d{6}$', str(pin)):
            return None
        candidatos = cls.objects.filter(
            is_active=True,
            es_activo=True,
            rol__in=cls.ROLES_CON_PIN,
        ).exclude(pin_autorizacion__isnull=True).exclude(pin_autorizacion='')
        for admin in candidatos:
            if admin.verificar_pin_autorizacion(pin):
                return admin
        return None

    def get_iniciales(self):
        """Retorna las iniciales del usuario (máximo 2 letras)"""
        if self.first_name and self.last_name:
            return f"{self.first_name[0]}{self.last_name[0]}".upper()
        elif self.first_name:
            return self.first_name[:2].upper()
        elif self.username:
            return self.username[:2].upper()
        return "U"
    
    def get_foto_url(self):
        """Retorna la URL de la foto de perfil o None"""
        if self.foto_perfil:
            return self.foto_perfil.url
        return None

class LogAcceso(models.Model):
    """
    Modelo para registrar accesos de usuarios
    """
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, verbose_name="Usuario")
    fecha_acceso = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Acceso")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Dirección IP")
    user_agent = models.TextField(null=True, blank=True, verbose_name="User Agent")
    exito = models.BooleanField(default=True, verbose_name="Acceso Exitoso")
    
    class Meta:
        verbose_name = "Log de Acceso"
        verbose_name_plural = "Logs de Acceso"
        ordering = ['-fecha_acceso']
    
    def __str__(self):
        return f"{self.usuario.username} - {self.fecha_acceso}"


class SesionActiva(models.Model):
    """
    Modelo para rastrear sesiones activas de usuarios.
    Permite ver y cerrar sesiones desde cualquier dispositivo.
    """
    usuario = models.ForeignKey(
        Usuario, 
        on_delete=models.CASCADE, 
        related_name='sesiones_activas',
        verbose_name="Usuario"
    )
    session_key = models.CharField(
        max_length=40, 
        unique=True, 
        verbose_name="Clave de Sesión"
    )
    ip_address = models.GenericIPAddressField(
        null=True, 
        blank=True, 
        verbose_name="Dirección IP"
    )
    user_agent = models.TextField(
        null=True, 
        blank=True, 
        verbose_name="User Agent"
    )
    dispositivo = models.CharField(
        max_length=100, 
        null=True, 
        blank=True,
        verbose_name="Dispositivo"
    )
    navegador = models.CharField(
        max_length=100, 
        null=True, 
        blank=True,
        verbose_name="Navegador"
    )
    sistema_operativo = models.CharField(
        max_length=100, 
        null=True, 
        blank=True,
        verbose_name="Sistema Operativo"
    )
    ubicacion = models.CharField(
        max_length=200, 
        null=True, 
        blank=True,
        verbose_name="Ubicación Aproximada"
    )
    fecha_inicio = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Fecha de Inicio"
    )
    ultima_actividad = models.DateTimeField(
        auto_now=True, 
        verbose_name="Última Actividad"
    )
    es_actual = models.BooleanField(
        default=False, 
        verbose_name="Es Sesión Actual"
    )
    activa = models.BooleanField(
        default=True, 
        verbose_name="Sesión Activa"
    )
    
    class Meta:
        verbose_name = "Sesión Activa"
        verbose_name_plural = "Sesiones Activas"
        ordering = ['-ultima_actividad']
    
    def __str__(self):
        return f"{self.usuario.username} - {self.dispositivo or 'Desconocido'} - {self.fecha_inicio}"
    
    @classmethod
    def registrar_sesion(cls, request, usuario):
        """
        Registra una nueva sesión para el usuario.
        Parsea el user-agent para extraer información del dispositivo.
        """
        user_agent_string = request.META.get('HTTP_USER_AGENT', '')
        ip = cls.get_client_ip(request)
        
        # Intentar parsear user-agent con la biblioteca user-agents
        dispositivo = 'Desconocido'
        navegador = 'Desconocido'
        sistema_operativo = 'Desconocido'
        
        try:
            from user_agents import parse
            user_agent = parse(user_agent_string)
            dispositivo = user_agent.device.family or 'Desconocido'
            if user_agent.device.brand:
                dispositivo = f"{user_agent.device.brand} {user_agent.device.model or ''}"
            navegador = f"{user_agent.browser.family} {user_agent.browser.version_string}"
            sistema_operativo = f"{user_agent.os.family} {user_agent.os.version_string}"
        except ImportError:
            # Si user-agents no está instalado, parsear manualmente
            ua_lower = user_agent_string.lower()
            
            # Detectar navegador
            if 'chrome' in ua_lower and 'edg' not in ua_lower:
                navegador = 'Chrome'
            elif 'firefox' in ua_lower:
                navegador = 'Firefox'
            elif 'safari' in ua_lower and 'chrome' not in ua_lower:
                navegador = 'Safari'
            elif 'edg' in ua_lower:
                navegador = 'Microsoft Edge'
            elif 'opera' in ua_lower or 'opr' in ua_lower:
                navegador = 'Opera'
            
            # Detectar SO
            if 'windows' in ua_lower:
                sistema_operativo = 'Windows'
            elif 'mac os' in ua_lower or 'macos' in ua_lower:
                sistema_operativo = 'macOS'
            elif 'linux' in ua_lower:
                sistema_operativo = 'Linux'
            elif 'android' in ua_lower:
                sistema_operativo = 'Android'
            elif 'iphone' in ua_lower or 'ipad' in ua_lower:
                sistema_operativo = 'iOS'
            
            # Detectar dispositivo
            if 'mobile' in ua_lower or 'android' in ua_lower:
                dispositivo = 'Móvil'
            elif 'tablet' in ua_lower or 'ipad' in ua_lower:
                dispositivo = 'Tablet'
            else:
                dispositivo = 'Computador'
        except Exception:
            pass
        
        # Asegurar que la sesión tiene una key
        if not request.session.session_key:
            request.session.create()
        
        # Crear o actualizar sesión
        sesion, created = cls.objects.update_or_create(
            session_key=request.session.session_key,
            defaults={
                'usuario': usuario,
                'ip_address': ip,
                'user_agent': user_agent_string[:500] if user_agent_string else None,
                'dispositivo': dispositivo[:100] if dispositivo else None,
                'navegador': navegador[:100] if navegador else None,
                'sistema_operativo': sistema_operativo[:100] if sistema_operativo else None,
                'activa': True
            }
        )
        
        return sesion
    
    @staticmethod
    def get_client_ip(request):
        """Obtiene la IP real del cliente"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip
    
    def cerrar_sesion(self):
        """Marca la sesión como cerrada"""
        from django.contrib.sessions.models import Session
        
        self.activa = False
        self.save()
        
        # Eliminar la sesión de Django
        try:
            Session.objects.filter(session_key=self.session_key).delete()
        except:
            pass


class TokenResetPassword(models.Model):
    """
    Modelo para tokens de reseteo de contraseña por correo.
    Permite enviar links seguros para cambiar contraseña.
    """
    usuario = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name='tokens_reset',
        verbose_name="Usuario"
    )
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        verbose_name="Token"
    )
    fecha_creacion = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Fecha de Creación"
    )
    fecha_expiracion = models.DateTimeField(
        verbose_name="Fecha de Expiración"
    )
    usado = models.BooleanField(
        default=False,
        verbose_name="Token Usado"
    )
    ip_solicitud = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name="IP de Solicitud"
    )
    
    class Meta:
        verbose_name = "Token de Reset de Password"
        verbose_name_plural = "Tokens de Reset de Password"
        ordering = ['-fecha_creacion']
    
    def __str__(self):
        return f"{self.usuario.username} - {self.token}"
    
    def es_valido(self):
        """Verifica si el token es válido (no usado y no expirado)"""
        return not self.usado and self.fecha_expiracion > timezone.now()
    
    @classmethod
    def crear_token(cls, usuario, request=None, horas_validez=24):
        """Crea un nuevo token de reset para el usuario"""
        # Invalidar tokens anteriores
        cls.objects.filter(usuario=usuario, usado=False).update(usado=True)
        
        ip = None
        if request:
            ip = SesionActiva.get_client_ip(request)
        
        token = cls.objects.create(
            usuario=usuario,
            fecha_expiracion=timezone.now() + timezone.timedelta(hours=horas_validez),
            ip_solicitud=ip
        )
        
        return token
