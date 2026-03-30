from django.db import models
from django.utils import timezone
from django.conf import settings
from .base import RUT_REGEX_VALIDATOR, validar_rut_chileno


class Empresa(models.Model):
    TIPO_EMPRESA_CHOICES = [
        ('CLIENTE', 'Cliente'),
        ('PROVEEDOR', 'Proveedor'),
        ('CLIENTE_PROVEEDOR', 'Cliente y Proveedor'),
    ]

    nombre = models.CharField(max_length=100)
    rut = models.CharField(max_length=20, validators=[RUT_REGEX_VALIDATOR])
    nombre_fantasia = models.CharField(max_length=255)
    razon_social = models.CharField(max_length=255)
    giro = models.CharField(max_length=255)
    direccion = models.CharField(max_length=255)
    comuna = models.CharField(max_length=100)
    ciudad = models.CharField(max_length=100)
    esProveedor = models.BooleanField(default=False)
    correoVendedor = models.CharField(max_length=100)
    correoIntercambio = models.CharField(max_length=100)
    correoAdministrador = models.CharField(max_length=100)
    acteco = models.CharField(max_length=20, blank=True, null=True, verbose_name='Código Acteco', help_text='Código de actividad económica del SII')
    contacto1 = models.CharField(max_length=100, blank=True, null=True, verbose_name='Contacto 1', help_text='Teléfono o email de contacto principal')
    contacto2 = models.CharField(max_length=100, blank=True, null=True, verbose_name='Contacto 2', help_text='Teléfono o email de contacto secundario')

    # --- New fields (all nullable to preserve existing data) ---
    region = models.CharField(max_length=100, blank=True, null=True, verbose_name='Región')
    codigo_postal = models.CharField(max_length=10, blank=True, null=True, verbose_name='Código Postal')
    telefono = models.CharField(max_length=20, blank=True, null=True, verbose_name='Teléfono')
    email = models.EmailField(blank=True, null=True, verbose_name='Email')
    sitio_web = models.URLField(blank=True, null=True, verbose_name='Sitio Web')
    tipo_empresa = models.CharField(
        max_length=20,
        choices=TIPO_EMPRESA_CHOICES,
        default='CLIENTE',
        verbose_name='Tipo de Empresa',
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')
    observaciones = models.TextField(blank=True, null=True, verbose_name='Observaciones')
    lead_time_dias = models.IntegerField(
        default=21,
        verbose_name='Lead Time (días)',
        help_text='Días promedio desde orden de compra hasta entrega en depósito',
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='empresas_creadas_app',
        verbose_name='Creado por',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='empresas_modificadas_app',
        verbose_name='Modificado por',
    )

    class Meta:
        verbose_name = 'Empresa'
        verbose_name_plural = 'Empresas'
        indexes = [
            models.Index(fields=['nombre']),
            models.Index(fields=['rut']),
            models.Index(fields=['tipo_empresa']),
            models.Index(fields=['activo']),
        ]

    def __str__(self):
        return self.nombre

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.rut and not validar_rut_chileno(self.rut):
            raise ValidationError({'rut': 'RUT inválido'})

    @property
    def es_cliente(self):
        return self.tipo_empresa in ['CLIENTE', 'CLIENTE_PROVEEDOR']

    @property
    def es_proveedor_tipo(self):
        return self.tipo_empresa in ['PROVEEDOR', 'CLIENTE_PROVEEDOR']


class Sucursal(models.Model):
    TIPO_SUCURSAL_CHOICES = [
        ('CENTRO_DISTRIBUCION', 'Centro de Distribución (Compradora)'),
        ('VENDEDORA', 'Sucursal Vendedora'),
        ('MIXTA', 'Mixta (Compra y Vende)'),
    ]

    alias = models.CharField(max_length=100)
    direccion = models.CharField(max_length=100)
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name='sucursales_app')
    tipo_sucursal = models.CharField(
        max_length=20,
        choices=TIPO_SUCURSAL_CHOICES,
        default='VENDEDORA',
        verbose_name='Tipo de Sucursal',
        help_text='CENTRO_DISTRIBUCION = Sucursal que compra a proveedores (ej: EDEL, GILD). VENDEDORA = Solo vende, recibe mercadería del CD.'
    )
    es_centro_distribucion = models.BooleanField(
        default=False,
        verbose_name='¿Es Centro de Distribución?',
        help_text='Marcar si esta sucursal compra directamente a proveedores externos y despacha a otras sucursales'
    )
    margen_sobreprecio_default = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name='Margen Sobreprecio %',
        help_text='Porcentaje de sobreprecio que aplica al despachar a otras sucursales'
    )
    nombre_impresora_boleta = models.CharField(
        max_length=100,
        default='boleta',
        verbose_name='Impresora Boleta Electrónica',
        help_text='Nombre de la impresora en Acepta para Boletas Electrónicas'
    )
    nombre_impresora_factura = models.CharField(
        max_length=100,
        default='factura',
        verbose_name='Impresora Factura Electrónica',
        help_text='Nombre de la impresora en Acepta para Facturas Electrónicas'
    )

    # --- QZ Tray: impresión térmica silenciosa ---
    usar_qz_tray = models.BooleanField(
        default=False,
        verbose_name='Usar QZ Tray',
        help_text='Si está activo usa QZ Tray (impresión silenciosa). Si no, usa window.print()'
    )
    nombre_impresora_termica = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        default='EPSON TM-T20II',
        verbose_name='Impresora Térmica (QZ Tray)',
        help_text='Nombre exacto como aparece en Windows → Dispositivos e impresoras'
    )

    # --- New fields (all nullable to preserve existing data) ---
    nombre = models.CharField(max_length=200, blank=True, null=True, verbose_name='Nombre Completo')
    comuna = models.CharField(max_length=100, blank=True, null=True, verbose_name='Comuna')
    ciudad = models.CharField(max_length=100, blank=True, null=True, verbose_name='Ciudad')
    telefono = models.CharField(max_length=20, blank=True, null=True, verbose_name='Teléfono')
    email = models.EmailField(blank=True, null=True, verbose_name='Email')
    activa = models.BooleanField(default=True, verbose_name='Activa')
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        verbose_name = "Sucursal"
        verbose_name_plural = "Sucursales"
        ordering = ['alias']
        unique_together = ['empresa', 'alias']

    def __str__(self):
        return self.alias

    @property
    def es_compradora(self):
        return self.es_centro_distribucion or self.tipo_sucursal == 'CENTRO_DISTRIBUCION'

    @property
    def es_solo_vendedora(self):
        return not self.es_centro_distribucion and self.tipo_sucursal == 'VENDEDORA'


class EmpresaUser(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.BooleanField(default=True)
    active = models.BooleanField(default=False)
    margenSobreprecio = models.IntegerField(null=True, blank=True)
    margenPrecioVenta = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.empresa} - {self.user} ({self.status})"


class Vendedor(models.Model):
    codigo_vendedor = models.CharField(max_length=100)
    rut = models.CharField(max_length=100, null=True)
    nombre = models.CharField(max_length=100, null=True)
    comision = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fecha_nacimiento = models.DateField(null=True)
    correo = models.CharField(max_length=100, null=True)
    sucursales = models.ManyToManyField(Sucursal, related_name='vendedores', blank=True, verbose_name='Sucursales asignadas')
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vendedores',
        verbose_name='Empresa',
        help_text='Empresa a la que pertenece el vendedor'
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')

    def __str__(self):
        return self.nombre or self.codigo_vendedor

    @property
    def empresa_display(self):
        if self.empresa:
            return self.empresa.nombre
        primera_sucursal = self.sucursales.first()
        if primera_sucursal:
            return primera_sucursal.empresa.nombre
        return None

    class Meta:
        verbose_name = 'Vendedor'
        verbose_name_plural = 'Vendedores'


class Correlativo(models.Model):
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE)
    tipo_dte = models.CharField(max_length=50)
    inicio = models.IntegerField()
    termino = models.IntegerField()
    fecha_actualizacion = models.DateField(null=True)
    alias = models.CharField(max_length=100)
    responsable = models.CharField(max_length=50)

    def __str__(self):
        return f"{self.sucursal.alias} - {self.tipo_dte} ({self.inicio}-{self.termino})"

    @property
    def numero_actual(self):
        return self.inicio

    @property
    def disponibles(self):
        return max(0, self.termino - self.inicio + 1)

    @property
    def consumidos(self):
        return max(0, self.inicio - 1)

    @property
    def total_rango(self):
        return self.termino

    @property
    def porcentaje_consumo(self):
        if self.total_rango > 0:
            return (self.consumidos / self.total_rango) * 100
        return 0

    @property
    def estado(self):
        if self.disponibles <= 0:
            return 'agotado'
        elif self.disponibles <= 100:
            return 'critico'
        else:
            return 'activo'

    def puede_emitir(self):
        return self.inicio <= self.termino

    def obtener_siguiente_numero(self):
        if not self.puede_emitir():
            raise ValueError(f"Correlativo agotado para {self.tipo_dte} en {self.sucursal.alias}")
        numero_actual = self.inicio
        self.inicio += 1
        self.fecha_actualizacion = timezone.now().date()
        self.save()
        return numero_actual

    class Meta:
        unique_together = ['sucursal', 'tipo_dte']
        verbose_name = 'Correlativo'
        verbose_name_plural = 'Correlativos'
