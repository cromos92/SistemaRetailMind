from django.db import models
from django.conf import settings
from django.core.validators import RegexValidator
from django.utils import timezone
import re

# Validación de RUT chileno
def validar_rut_chileno(rut):
    """Valida formato de RUT chileno"""
    if not rut:
        return False
    
    # Limpiar formato
    rut = rut.replace('.', '').replace('-', '').upper()
    
    if len(rut) < 2:
        return False
    
    # Separar número y dígito verificador
    numero = rut[:-1]
    dv = rut[-1]
    
    # Validar que el número sea numérico
    if not numero.isdigit():
        return False
    
    # Calcular dígito verificador
    suma = 0
    multiplicador = 2
    
    for digito in reversed(numero):
        suma += int(digito) * multiplicador
        multiplicador = multiplicador + 1 if multiplicador < 7 else 2
    
    resto = suma % 11
    dv_calculado = 11 - resto
    
    if dv_calculado == 11:
        dv_calculado = '0'
    elif dv_calculado == 10:
        dv_calculado = 'K'
    else:
        dv_calculado = str(dv_calculado)
    
    return dv == dv_calculado

class Empresa(models.Model):
    """Modelo mejorado para gestión de empresas"""
    
    # === DATOS BÁSICOS ===
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    rut = models.CharField(
        max_length=20, 
        verbose_name="RUT",
        validators=[
            RegexValidator(
                regex=r'^(\d{1,2}\.\d{3}\.\d{3}-[\dkK]|\d{7,8}-[\dkK])$',
                message='Formato de RUT inválido. Use formato: 12345678-9 o 12.345.678-9'
            )
        ]
    )
    nombre_fantasia = models.CharField(max_length=255, verbose_name="Nombre de Fantasía")
    razon_social = models.CharField(max_length=255, verbose_name="Razón Social")
    giro = models.CharField(max_length=255, verbose_name="Giro Comercial")
    
    # === DIRECCIÓN ===
    direccion = models.CharField(max_length=255, verbose_name="Dirección")
    comuna = models.CharField(max_length=100, verbose_name="Comuna")
    ciudad = models.CharField(max_length=100, verbose_name="Ciudad")
    region = models.CharField(max_length=100, verbose_name="Región", blank=True, null=True)
    codigo_postal = models.CharField(max_length=10, verbose_name="Código Postal", blank=True, null=True)
    
    # === CONTACTO ===
    telefono = models.CharField(max_length=20, verbose_name="Teléfono", blank=True, null=True)
    email = models.EmailField(verbose_name="Email", blank=True, null=True)
    sitio_web = models.URLField(verbose_name="Sitio Web", blank=True, null=True)
    
    # === TIPO DE EMPRESA ===
    TIPO_EMPRESA_CHOICES = [
        ('CLIENTE', 'Cliente'),
        ('PROVEEDOR', 'Proveedor'),
        ('CLIENTE_PROVEEDOR', 'Cliente y Proveedor'),
    ]
    tipo_empresa = models.CharField(
        max_length=20, 
        choices=TIPO_EMPRESA_CHOICES, 
        default='CLIENTE',
        verbose_name="Tipo de Empresa"
    )
    
    # === DATOS FISCALES ===
    esProveedor = models.BooleanField(default=False, verbose_name="Es Proveedor")
    correoVendedor = models.CharField(max_length=100, verbose_name="Correo Vendedor", blank=True, null=True)
    correoIntercambio = models.CharField(max_length=100, verbose_name="Correo Intercambio", blank=True, null=True)
    correoAdministrador = models.CharField(max_length=100, verbose_name="Correo Administrador", blank=True, null=True)
    
    # === DATOS ADICIONALES ===
    fecha_creacion = models.DateField(auto_now_add=True, verbose_name="Fecha de Creación")
    fecha_modificacion = models.DateTimeField(auto_now=True, verbose_name="Fecha de Modificación")
    activo = models.BooleanField(default=True, verbose_name="Activo")
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True, verbose_name="Observaciones")
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='empresas_creadas',
        verbose_name="Creado por"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='empresas_modificadas',
        verbose_name="Modificado por"
    )
    
    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ['nombre']
        indexes = [
            models.Index(fields=['nombre']),
            models.Index(fields=['rut']),
            models.Index(fields=['tipo_empresa']),
            models.Index(fields=['activo']),
        ]
    
    def __str__(self):
        return f"{self.nombre} ({self.rut})"
    
    def clean(self):
        """Validación personalizada"""
        from django.core.exceptions import ValidationError
        
        # Validar RUT
        if self.rut and not validar_rut_chileno(self.rut):
            raise ValidationError({'rut': 'RUT inválido'})
        
        # Validar que el RUT sea único
        if Empresa.objects.filter(rut=self.rut).exclude(id=self.id).exists():
            raise ValidationError({'rut': 'Este RUT ya está registrado'})
    
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
    
    @property
    def es_cliente(self):
        """Indica si la empresa es cliente"""
        return self.tipo_empresa in ['CLIENTE', 'CLIENTE_PROVEEDOR']
    
    @property
    def es_proveedor(self):
        """Indica si la empresa es proveedor"""
        return self.tipo_empresa in ['PROVEEDOR', 'CLIENTE_PROVEEDOR']

class Sucursal(models.Model):
    """Modelo mejorado para sucursales"""
    
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='sucursales', verbose_name="Empresa")
    alias = models.CharField(max_length=100, verbose_name="Alias")
    nombre = models.CharField(max_length=200, verbose_name="Nombre", blank=True, null=True)
    direccion = models.CharField(max_length=255, verbose_name="Dirección")
    comuna = models.CharField(max_length=100, verbose_name="Comuna", blank=True, null=True)
    ciudad = models.CharField(max_length=100, verbose_name="Ciudad", blank=True, null=True)
    
    # === CONTACTO ===
    telefono = models.CharField(max_length=20, verbose_name="Teléfono", blank=True, null=True)
    email = models.EmailField(verbose_name="Email", blank=True, null=True)
    
    # === ESTADO ===
    activa = models.BooleanField(default=True, verbose_name="Activa")
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Sucursal"
        verbose_name_plural = "Sucursales"
        ordering = ['empresa', 'alias']
        unique_together = ['empresa', 'alias']
    
    def __str__(self):
        return f"{self.empresa.nombre} - {self.alias}"

class ContactoEmpresa(models.Model):
    """Contactos asociados a empresas"""
    
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='contactos', verbose_name="Empresa")
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    cargo = models.CharField(max_length=100, verbose_name="Cargo", blank=True, null=True)
    email = models.EmailField(verbose_name="Email", blank=True, null=True)
    telefono = models.CharField(max_length=20, verbose_name="Teléfono", blank=True, null=True)
    celular = models.CharField(max_length=20, verbose_name="Celular", blank=True, null=True)
    
    # === TIPO DE CONTACTO ===
    TIPO_CONTACTO_CHOICES = [
        ('PRINCIPAL', 'Contacto Principal'),
        ('COMPRAS', 'Compras'),
        ('VENTAS', 'Ventas'),
        ('ADMINISTRACION', 'Administración'),
        ('FINANZAS', 'Finanzas'),
        ('TECNICO', 'Técnico'),
        ('OTRO', 'Otro'),
    ]
    tipo_contacto = models.CharField(
        max_length=20, 
        choices=TIPO_CONTACTO_CHOICES, 
        default='PRINCIPAL',
        verbose_name="Tipo de Contacto"
    )
    
    # === ESTADO ===
    activo = models.BooleanField(default=True, verbose_name="Activo")
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Contacto de Empresa"
        verbose_name_plural = "Contactos de Empresa"
        ordering = ['empresa', 'tipo_contacto', 'nombre']
    
    def __str__(self):
        return f"{self.empresa.nombre} - {self.nombre} ({self.get_tipo_contacto_display()})"

class Cliente(models.Model):
    """Modelo específico para clientes individuales"""
    
    # === DATOS PERSONALES ===
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    apellido = models.CharField(max_length=100, verbose_name="Apellido")
    rut = models.CharField(
        max_length=20, 
        verbose_name="RUT",
        validators=[
            RegexValidator(
                regex=r'^(\d{1,2}\.\d{3}\.\d{3}-[\dkK]|\d{7,8}-[\dkK])$',
                message='Formato de RUT inválido. Use formato: 12345678-9 o 12.345.678-9'
            )
        ],
        blank=True, null=True
    )
    
    # === CONTACTO ===
    email = models.EmailField(verbose_name="Email", blank=True, null=True)
    telefono = models.CharField(max_length=20, verbose_name="Teléfono", blank=True, null=True)
    celular = models.CharField(max_length=20, verbose_name="Celular", blank=True, null=True)
    
    # === DIRECCIÓN ===
    direccion = models.CharField(max_length=255, verbose_name="Dirección", blank=True, null=True)
    comuna = models.CharField(max_length=100, verbose_name="Comuna", blank=True, null=True)
    ciudad = models.CharField(max_length=100, verbose_name="Ciudad", blank=True, null=True)
    
    # === DATOS ADICIONALES ===
    fecha_nacimiento = models.DateField(verbose_name="Fecha de Nacimiento", blank=True, null=True)
    genero = models.CharField(
        max_length=10, 
        choices=[('M', 'Masculino'), ('F', 'Femenino'), ('O', 'Otro')],
        verbose_name="Género",
        blank=True, null=True
    )
    
    # === CLASIFICACIÓN ===
    TIPO_CLIENTE_CHOICES = [
        ('INDIVIDUAL', 'Cliente Individual'),
        ('EMPRESARIAL', 'Cliente Empresarial'),
        ('MAYORISTA', 'Mayorista'),
        ('DISTRIBUIDOR', 'Distribuidor'),
    ]
    tipo_cliente = models.CharField(
        max_length=20, 
        choices=TIPO_CLIENTE_CHOICES, 
        default='INDIVIDUAL',
        verbose_name="Tipo de Cliente"
    )
    
    # === EMPRESA ASOCIADA ===
    empresa = models.ForeignKey(
        Empresa, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='clientes',
        verbose_name="Empresa Asociada"
    )
    
    # === ESTADO ===
    activo = models.BooleanField(default=True, verbose_name="Activo")
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True, verbose_name="Observaciones")
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='clientes_creados',
        verbose_name="Creado por"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='clientes_modificados',
        verbose_name="Modificado por"
    )
    
    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['apellido', 'nombre']
        indexes = [
            models.Index(fields=['nombre', 'apellido']),
            models.Index(fields=['rut']),
            models.Index(fields=['email']),
            models.Index(fields=['tipo_cliente']),
            models.Index(fields=['activo']),
        ]
    
    def __str__(self):
        return f"{self.apellido}, {self.nombre}"
    
    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}"
    
    def clean(self):
        """Validación personalizada"""
        from django.core.exceptions import ValidationError
        
        # Validar RUT si se proporciona
        if self.rut and not validar_rut_chileno(self.rut):
            raise ValidationError({'rut': 'RUT inválido'})
        
        # Validar que el RUT sea único si se proporciona
        if self.rut and Cliente.objects.filter(rut=self.rut).exclude(id=self.id).exists():
            raise ValidationError({'rut': 'Este RUT ya está registrado'})
    
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

class Proveedor(models.Model):
    """Modelo específico para proveedores"""
    
    empresa = models.OneToOneField(Empresa, on_delete=models.CASCADE, related_name='proveedor', verbose_name="Empresa")
    
    # === DATOS ESPECÍFICOS DE PROVEEDOR ===
    codigo_proveedor = models.CharField(max_length=50, verbose_name="Código de Proveedor", unique=True)
    categoria = models.CharField(max_length=100, verbose_name="Categoría", blank=True, null=True)
    
    # === CONDICIONES COMERCIALES ===
    dias_credito = models.IntegerField(default=30, verbose_name="Días de Crédito")
    descuento_porcentaje = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0, 
        verbose_name="Descuento (%)"
    )
    
    # === EVALUACIÓN ===
    calificacion = models.IntegerField(
        choices=[(i, i) for i in range(1, 6)],
        verbose_name="Calificación",
        blank=True, null=True
    )
    observaciones_evaluacion = models.TextField(blank=True, null=True, verbose_name="Observaciones de Evaluación")
    
    # === ESTADO ===
    activo = models.BooleanField(default=True, verbose_name="Activo")
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ['empresa__nombre']
    
    def __str__(self):
        return f"{self.empresa.nombre} ({self.codigo_proveedor})"

class LogEmpresa(models.Model):
    """Log de actividades de empresas"""
    
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='logs', verbose_name="Empresa")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    
    # === DATOS DEL LOG ===
    accion = models.CharField(max_length=50, verbose_name="Acción")
    descripcion = models.TextField(verbose_name="Descripción")
    datos_anteriores = models.JSONField(blank=True, null=True, verbose_name="Datos Anteriores")
    datos_nuevos = models.JSONField(blank=True, null=True, verbose_name="Datos Nuevos")
    
    # === METADATA ===
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="Dirección IP")
    user_agent = models.TextField(blank=True, null=True, verbose_name="User Agent")
    
    class Meta:
        verbose_name = "Log de Empresa"
        verbose_name_plural = "Logs de Empresa"
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['empresa', 'fecha']),
            models.Index(fields=['accion', 'fecha']),
            models.Index(fields=['usuario', 'fecha']),
        ]
    
    def __str__(self):
        return f"{self.empresa.nombre} - {self.accion} - {self.fecha}"

class LogCliente(models.Model):
    """Log de actividades de clientes"""
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='logs', verbose_name="Cliente")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Usuario")
    
    # === DATOS DEL LOG ===
    accion = models.CharField(max_length=50, verbose_name="Acción")
    descripcion = models.TextField(verbose_name="Descripción")
    datos_anteriores = models.JSONField(blank=True, null=True, verbose_name="Datos Anteriores")
    datos_nuevos = models.JSONField(blank=True, null=True, verbose_name="Datos Nuevos")
    
    # === METADATA ===
    fecha = models.DateTimeField(auto_now_add=True, verbose_name="Fecha")
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name="Dirección IP")
    user_agent = models.TextField(blank=True, null=True, verbose_name="User Agent")
    
    class Meta:
        verbose_name = "Log de Cliente"
        verbose_name_plural = "Logs de Cliente"
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['cliente', 'fecha']),
            models.Index(fields=['accion', 'fecha']),
            models.Index(fields=['usuario', 'fecha']),
        ]
    
    def __str__(self):
        return f"{self.cliente.nombre_completo} - {self.accion} - {self.fecha}" 