from django.db import models
from django.conf import settings
from .base import RUT_REGEX_VALIDATOR, validar_rut_chileno


class ContactoEmpresa(models.Model):
    TIPO_CONTACTO_CHOICES = [
        ('PRINCIPAL', 'Contacto Principal'),
        ('COMPRAS', 'Compras'),
        ('VENTAS', 'Ventas'),
        ('ADMINISTRACION', 'Administración'),
        ('FINANZAS', 'Finanzas'),
        ('TECNICO', 'Técnico'),
        ('OTRO', 'Otro'),
    ]

    empresa = models.ForeignKey(
        'app.Empresa',
        on_delete=models.CASCADE,
        related_name='contactos_crm',
        verbose_name='Empresa',
    )
    nombre = models.CharField(max_length=100, verbose_name='Nombre')
    cargo = models.CharField(max_length=100, verbose_name='Cargo', blank=True, null=True)
    email = models.EmailField(verbose_name='Email', blank=True, null=True)
    telefono = models.CharField(max_length=20, verbose_name='Teléfono', blank=True, null=True)
    celular = models.CharField(max_length=20, verbose_name='Celular', blank=True, null=True)
    tipo_contacto = models.CharField(
        max_length=20,
        choices=TIPO_CONTACTO_CHOICES,
        default='PRINCIPAL',
        verbose_name='Tipo de Contacto',
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Contacto de Empresa'
        verbose_name_plural = 'Contactos de Empresa'
        ordering = ['empresa', 'tipo_contacto', 'nombre']

    def __str__(self):
        return f"{self.empresa.nombre} - {self.nombre} ({self.get_tipo_contacto_display()})"


TIPO_CLIENTE_CHOICES = [
    ('INDIVIDUAL', 'Cliente Individual'),
    ('EMPRESARIAL', 'Cliente Empresarial'),
    ('MAYORISTA', 'Mayorista'),
    ('DISTRIBUIDOR', 'Distribuidor'),
    ('EMPLEADO', 'Empleado / Trabajador Interno'),
    ('CREDITO_EXTERNO', 'Cliente con Crédito Externo'),
]

# Identificación del cliente. Un extranjero sin cédula chilena se identifica con
# pasaporte, así que el RUT no puede ser el único documento aceptado.
TIPO_DOCUMENTO_CLIENTE_CHOICES = [
    ('RUT', 'RUT'),
    ('PASAPORTE', 'Pasaporte'),
]


class Cliente(models.Model):
    TIPO_CLIENTE_CHOICES = TIPO_CLIENTE_CHOICES
    TIPO_DOCUMENTO_CHOICES = TIPO_DOCUMENTO_CLIENTE_CHOICES

    nombre = models.CharField(max_length=100, verbose_name='Nombre')
    apellido = models.CharField(max_length=100, verbose_name='Apellido')
    tipo_documento = models.CharField(
        max_length=15,
        choices=TIPO_DOCUMENTO_CLIENTE_CHOICES,
        default='RUT',
        verbose_name='Tipo de Documento',
        help_text='Documento con el que se identifica al cliente',
    )
    rut = models.CharField(
        max_length=20,
        verbose_name='RUT',
        validators=[RUT_REGEX_VALIDATOR],
        blank=True,
        null=True,
    )
    pasaporte = models.CharField(
        max_length=30,
        verbose_name='Pasaporte',
        blank=True,
        null=True,
        help_text='Número de pasaporte para clientes extranjeros sin RUT chileno',
    )
    pais_documento = models.CharField(
        max_length=60,
        verbose_name='País del Documento',
        blank=True,
        null=True,
    )
    email = models.EmailField(verbose_name='Email', blank=True, null=True)
    telefono = models.CharField(max_length=20, verbose_name='Teléfono', blank=True, null=True)
    celular = models.CharField(max_length=20, verbose_name='Celular', blank=True, null=True)
    direccion = models.CharField(max_length=255, verbose_name='Dirección', blank=True, null=True)
    comuna = models.CharField(max_length=100, verbose_name='Comuna', blank=True, null=True)
    ciudad = models.CharField(max_length=100, verbose_name='Ciudad', blank=True, null=True)
    fecha_nacimiento = models.DateField(verbose_name='Fecha de Nacimiento', blank=True, null=True)
    genero = models.CharField(
        max_length=10,
        choices=[('M', 'Masculino'), ('F', 'Femenino'), ('O', 'Otro')],
        verbose_name='Género',
        blank=True,
        null=True,
    )
    tipo_cliente = models.CharField(
        max_length=20,
        choices=TIPO_CLIENTE_CHOICES,
        default='INDIVIDUAL',
        verbose_name='Tipo de Cliente',
    )
    empresa = models.ForeignKey(
        'app.Empresa',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clientes_crm',
        verbose_name='Empresa Asociada',
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')
    observaciones = models.TextField(blank=True, null=True, verbose_name='Observaciones')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clientes_creados_app',
        verbose_name='Creado por',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='clientes_modificados_app',
        verbose_name='Modificado por',
    )

    class Meta:
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'
        ordering = ['apellido', 'nombre']
        indexes = [
            models.Index(fields=['nombre', 'apellido']),
            models.Index(fields=['rut']),
            models.Index(fields=['pasaporte'], name='app_cliente_pasapo_idx'),
            models.Index(fields=['email']),
            models.Index(fields=['tipo_cliente']),
            models.Index(fields=['activo']),
        ]

    def __str__(self):
        return f"{self.apellido}, {self.nombre}"

    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}"

    @property
    def numero_documento(self):
        """Número del documento con el que se identifica al cliente (sin etiqueta)."""
        if self.tipo_documento == 'PASAPORTE':
            return self.pasaporte or ''
        return self.rut or ''

    @property
    def documento_display(self):
        """Documento listo para mostrar: el RUT tal cual o 'Pasaporte <número>'."""
        if self.tipo_documento == 'PASAPORTE' and self.pasaporte:
            pais = f" ({self.pais_documento})" if self.pais_documento else ''
            return f"Pasaporte {self.pasaporte}{pais}"
        return self.rut or ''

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.rut and not validar_rut_chileno(self.rut):
            raise ValidationError({'rut': 'RUT inválido'})
        if self.tipo_documento == 'PASAPORTE' and not (self.pasaporte or '').strip():
            raise ValidationError({'pasaporte': 'Debe indicar el número de pasaporte'})


class Proveedor(models.Model):
    empresa = models.OneToOneField(
        'app.Empresa',
        on_delete=models.CASCADE,
        related_name='proveedor_crm',
        verbose_name='Empresa',
    )
    codigo_proveedor = models.CharField(max_length=50, verbose_name='Código de Proveedor', unique=True)
    categoria = models.CharField(max_length=100, verbose_name='Categoría', blank=True, null=True)
    dias_credito = models.IntegerField(default=30, verbose_name='Días de Crédito')
    descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name='Descuento (%)',
    )
    calificacion = models.IntegerField(
        choices=[(i, i) for i in range(1, 6)],
        verbose_name='Calificación',
        blank=True,
        null=True,
    )
    observaciones_evaluacion = models.TextField(blank=True, null=True, verbose_name='Observaciones de Evaluación')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Proveedor'
        verbose_name_plural = 'Proveedores'
        ordering = ['empresa__nombre']

    def __str__(self):
        return f"{self.empresa.nombre} ({self.codigo_proveedor})"


class LogEmpresa(models.Model):
    empresa = models.ForeignKey(
        'app.Empresa',
        on_delete=models.CASCADE,
        related_name='logs_crm',
        verbose_name='Empresa',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_empresa_app',
        verbose_name='Usuario',
    )
    accion = models.CharField(max_length=50, verbose_name='Acción')
    descripcion = models.TextField(verbose_name='Descripción')
    datos_anteriores = models.JSONField(blank=True, null=True, verbose_name='Datos Anteriores')
    datos_nuevos = models.JSONField(blank=True, null=True, verbose_name='Datos Nuevos')
    fecha = models.DateTimeField(auto_now_add=True, verbose_name='Fecha')
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name='Dirección IP')
    user_agent = models.TextField(blank=True, null=True, verbose_name='User Agent')

    class Meta:
        verbose_name = 'Log de Empresa'
        verbose_name_plural = 'Logs de Empresa'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['empresa', 'fecha']),
            models.Index(fields=['accion', 'fecha']),
            models.Index(fields=['usuario', 'fecha']),
        ]

    def __str__(self):
        return f"{self.empresa.nombre} - {self.accion} - {self.fecha}"


class LogCliente(models.Model):
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name='logs_crm',
        verbose_name='Cliente',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_cliente_app',
        verbose_name='Usuario',
    )
    accion = models.CharField(max_length=50, verbose_name='Acción')
    descripcion = models.TextField(verbose_name='Descripción')
    datos_anteriores = models.JSONField(blank=True, null=True, verbose_name='Datos Anteriores')
    datos_nuevos = models.JSONField(blank=True, null=True, verbose_name='Datos Nuevos')
    fecha = models.DateTimeField(auto_now_add=True, verbose_name='Fecha')
    ip_address = models.GenericIPAddressField(blank=True, null=True, verbose_name='Dirección IP')
    user_agent = models.TextField(blank=True, null=True, verbose_name='User Agent')

    class Meta:
        verbose_name = 'Log de Cliente'
        verbose_name_plural = 'Logs de Cliente'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['cliente', 'fecha']),
            models.Index(fields=['accion', 'fecha']),
            models.Index(fields=['usuario', 'fecha']),
        ]

    def __str__(self):
        return f"{self.cliente.nombre_completo} - {self.accion} - {self.fecha}"
