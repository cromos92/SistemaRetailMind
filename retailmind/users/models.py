from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
import uuid
import re

class Usuario(AbstractUser):
    """
    Modelo de Usuario personalizado para RetailMind
    Migrado desde olagreetings_user_management
    """
    # Definición de roles
    ROLES = [
        ('administrador', 'Administrador'),
        ('jefe_local', 'Jefe Local'),
        ('cajero', 'Cajero'),
        ('vendedor', 'Vendedor'),
    ]
    
    # Campos básicos
    rut = models.CharField(max_length=12, unique=True, null=True, blank=True, verbose_name="RUT")
    telefono = models.CharField(max_length=15, null=True, blank=True, verbose_name="Teléfono")
    direccion = models.TextField(null=True, blank=True, verbose_name="Dirección")
    fecha_nacimiento = models.DateField(null=True, blank=True, verbose_name="Fecha de Nacimiento")
    
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
        if self.is_superuser:
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
