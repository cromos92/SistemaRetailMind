from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import re

class Empresa(models.Model):
    """
    Modelo mejorado de Empresa para Olagreetings
    """
    # Información básica
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    rut = models.CharField(max_length=20, unique=True, verbose_name="RUT")
    nombre_fantasia = models.CharField(max_length=255, blank=True, verbose_name="Nombre de Fantasía")
    razon_social = models.CharField(max_length=255, blank=True, verbose_name="Razón Social")
    giro = models.CharField(max_length=255, blank=True, verbose_name="Giro")
    
    # Dirección
    direccion = models.CharField(max_length=255, blank=True, verbose_name="Dirección")
    comuna = models.CharField(max_length=100, blank=True, verbose_name="Comuna")
    ciudad = models.CharField(max_length=100, blank=True, verbose_name="Ciudad")
    region = models.CharField(max_length=100, blank=True, verbose_name="Región")
    codigo_postal = models.CharField(max_length=10, blank=True, verbose_name="Código Postal")
    
    # Contacto
    telefono = models.CharField(max_length=20, blank=True, verbose_name="Teléfono")
    fax = models.CharField(max_length=20, blank=True, verbose_name="Fax")
    sitio_web = models.URLField(blank=True, verbose_name="Sitio Web")
    
    # Correos electrónicos
    correo_vendedor = models.EmailField(blank=True, verbose_name="Correo Vendedor")
    correo_intercambio = models.EmailField(blank=True, verbose_name="Correo Intercambio")
    correo_administrador = models.EmailField(blank=True, verbose_name="Correo Administrador")
    correo_facturacion = models.EmailField(blank=True, verbose_name="Correo Facturación")
    
    # Clasificación
    TIPO_EMPRESA_CHOICES = [
        ('CLIENTE', 'Cliente'),
        ('PROVEEDOR', 'Proveedor'),
        ('CLIENTE_PROVEEDOR', 'Cliente y Proveedor'),
        ('OTRO', 'Otro'),
    ]
    tipo_empresa = models.CharField(
        max_length=20, 
        choices=TIPO_EMPRESA_CHOICES, 
        default='CLIENTE',
        verbose_name="Tipo de Empresa"
    )
    
    # Estado y fechas
    es_activa = models.BooleanField(default=True, verbose_name="Empresa Activa")
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    fecha_ultima_actualizacion = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")
    
    # Información adicional
    observaciones = models.TextField(blank=True, verbose_name="Observaciones")
    notas_internas = models.TextField(blank=True, verbose_name="Notas Internas")
    
    # Campos de facturación
    condicion_pago = models.CharField(max_length=50, blank=True, verbose_name="Condición de Pago")
    dias_credito = models.IntegerField(default=0, verbose_name="Días de Crédito")
    limite_credito = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0, 
        verbose_name="Límite de Crédito"
    )
    
    # Campos de contacto principal
    contacto_principal = models.CharField(max_length=100, blank=True, verbose_name="Contacto Principal")
    cargo_contacto = models.CharField(max_length=100, blank=True, verbose_name="Cargo del Contacto")
    telefono_contacto = models.CharField(max_length=20, blank=True, verbose_name="Teléfono del Contacto")
    email_contacto = models.EmailField(blank=True, verbose_name="Email del Contacto")
    
    class Meta:
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"
        ordering = ['nombre']
        indexes = [
            models.Index(fields=['nombre']),
            models.Index(fields=['rut']),
            models.Index(fields=['tipo_empresa']),
            models.Index(fields=['es_activa']),
        ]
    
    def __str__(self):
        return f"{self.nombre} ({self.rut})"
    
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
    
    def es_cliente(self):
        """Verifica si la empresa es cliente"""
        return self.tipo_empresa in ['CLIENTE', 'CLIENTE_PROVEEDOR']
    
    def es_proveedor(self):
        """Verifica si la empresa es proveedor"""
        return self.tipo_empresa in ['PROVEEDOR', 'CLIENTE_PROVEEDOR']
    
    def get_direccion_completa(self):
        """Obtiene la dirección completa formateada"""
        partes = []
        if self.direccion:
            partes.append(self.direccion)
        if self.comuna:
            partes.append(self.comuna)
        if self.ciudad:
            partes.append(self.ciudad)
        if self.region:
            partes.append(self.region)
        if self.codigo_postal:
            partes.append(self.codigo_postal)
        
        return ", ".join(partes) if partes else "Sin dirección"
    
    def get_contacto_principal(self):
        """Obtiene la información del contacto principal"""
        if self.contacto_principal:
            return {
                'nombre': self.contacto_principal,
                'cargo': self.cargo_contacto,
                'telefono': self.telefono_contacto,
                'email': self.email_contacto
            }
        return None

class Sucursal(models.Model):
    """
    Modelo de Sucursal para empresas
    """
    empresa = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='sucursales',
        verbose_name="Empresa"
    )
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    alias = models.CharField(max_length=100, blank=True, verbose_name="Alias")
    
    # Dirección
    direccion = models.CharField(max_length=255, verbose_name="Dirección")
    comuna = models.CharField(max_length=100, blank=True, verbose_name="Comuna")
    ciudad = models.CharField(max_length=100, blank=True, verbose_name="Ciudad")
    region = models.CharField(max_length=100, blank=True, verbose_name="Región")
    
    # Contacto
    telefono = models.CharField(max_length=20, blank=True, verbose_name="Teléfono")
    email = models.EmailField(blank=True, verbose_name="Email")
    
    # Estado
    es_activa = models.BooleanField(default=True, verbose_name="Sucursal Activa")
    es_principal = models.BooleanField(default=False, verbose_name="Sucursal Principal")
    
    # Información adicional
    observaciones = models.TextField(blank=True, verbose_name="Observaciones")
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    
    class Meta:
        verbose_name = "Sucursal"
        verbose_name_plural = "Sucursales"
        ordering = ['empresa', 'nombre']
        unique_together = ['empresa', 'nombre']
    
    def __str__(self):
        return f"{self.empresa.nombre} - {self.nombre}"
    
    def get_direccion_completa(self):
        """Obtiene la dirección completa formateada"""
        partes = []
        if self.direccion:
            partes.append(self.direccion)
        if self.comuna:
            partes.append(self.comuna)
        if self.ciudad:
            partes.append(self.ciudad)
        if self.region:
            partes.append(self.region)
        
        return ", ".join(partes) if partes else "Sin dirección"

class ContactoEmpresa(models.Model):
    """
    Modelo para contactos adicionales de empresas
    """
    empresa = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='contactos',
        verbose_name="Empresa"
    )
    nombre = models.CharField(max_length=100, verbose_name="Nombre")
    cargo = models.CharField(max_length=100, blank=True, verbose_name="Cargo")
    
    # Información de contacto
    telefono = models.CharField(max_length=20, blank=True, verbose_name="Teléfono")
    celular = models.CharField(max_length=20, blank=True, verbose_name="Celular")
    email = models.EmailField(blank=True, verbose_name="Email")
    
    # Información adicional
    departamento = models.CharField(max_length=100, blank=True, verbose_name="Departamento")
    es_contacto_principal = models.BooleanField(default=False, verbose_name="Contacto Principal")
    es_activo = models.BooleanField(default=True, verbose_name="Contacto Activo")
    
    # Fechas
    fecha_creacion = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Creación")
    fecha_ultima_actualizacion = models.DateTimeField(auto_now=True, verbose_name="Última Actualización")
    
    # Notas
    notas = models.TextField(blank=True, verbose_name="Notas")
    
    class Meta:
        verbose_name = "Contacto de Empresa"
        verbose_name_plural = "Contactos de Empresa"
        ordering = ['empresa', 'nombre']
    
    def __str__(self):
        return f"{self.empresa.nombre} - {self.nombre} ({self.cargo})"

class Cliente(models.Model):
    """
    Modelo específico para clientes (extensión de Empresa)
    """
    empresa = models.OneToOneField(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='cliente',
        verbose_name="Empresa"
    )
    
    # Información específica del cliente
    TIPO_CLIENTE_CHOICES = [
        ('INDIVIDUAL', 'Individual'),
        ('EMPRESA', 'Empresa'),
        ('GUBERNAMENTAL', 'Gubernamental'),
        ('ONG', 'ONG'),
    ]
    tipo_cliente = models.CharField(
        max_length=20, 
        choices=TIPO_CLIENTE_CHOICES, 
        default='EMPRESA',
        verbose_name="Tipo de Cliente"
    )
    
    # Clasificación comercial
    CATEGORIA_CLIENTE_CHOICES = [
        ('A', 'Categoría A - Premium'),
        ('B', 'Categoría B - Regular'),
        ('C', 'Categoría C - Básico'),
        ('D', 'Categoría D - Nuevo'),
    ]
    categoria = models.CharField(
        max_length=1, 
        choices=CATEGORIA_CLIENTE_CHOICES, 
        default='D',
        verbose_name="Categoría"
    )
    
    # Información comercial
    vendedor_asignado = models.ForeignKey(
        'app.Vendedor',  # Asumiendo que existe el modelo Vendedor
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Vendedor Asignado"
    )
    
    # Historial comercial
    fecha_primer_compra = models.DateField(null=True, blank=True, verbose_name="Fecha Primera Compra")
    fecha_ultima_compra = models.DateField(null=True, blank=True, verbose_name="Fecha Última Compra")
    total_compras = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0, 
        verbose_name="Total de Compras"
    )
    numero_compras = models.IntegerField(default=0, verbose_name="Número de Compras")
    
    # Información de crédito
    limite_credito_cliente = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0, 
        verbose_name="Límite de Crédito"
    )
    saldo_actual = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0, 
        verbose_name="Saldo Actual"
    )
    
    # Estado del cliente
    ESTADO_CLIENTE_CHOICES = [
        ('ACTIVO', 'Activo'),
        ('INACTIVO', 'Inactivo'),
        ('SUSPENDIDO', 'Suspendido'),
        ('BLOQUEADO', 'Bloqueado'),
    ]
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_CLIENTE_CHOICES, 
        default='ACTIVO',
        verbose_name="Estado"
    )
    
    # Información adicional
    fuente_cliente = models.CharField(max_length=100, blank=True, verbose_name="Fuente del Cliente")
    referido_por = models.CharField(max_length=100, blank=True, verbose_name="Referido por")
    notas_comerciales = models.TextField(blank=True, verbose_name="Notas Comerciales")
    
    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['empresa__nombre']
    
    def __str__(self):
        return f"Cliente: {self.empresa.nombre}"
    
    def calcular_antiguedad(self):
        """Calcula la antigüedad del cliente en días"""
        if self.fecha_primer_compra:
            return (timezone.now().date() - self.fecha_primer_compra).days
        return 0
    
    def calcular_promedio_compra(self):
        """Calcula el promedio de compra"""
        if self.numero_compras > 0:
            return self.total_compras / self.numero_compras
        return 0
    
    def tiene_credito_disponible(self):
        """Verifica si tiene crédito disponible"""
        return self.saldo_actual < self.limite_credito_cliente
    
    def get_credito_disponible(self):
        """Obtiene el crédito disponible"""
        return max(0, self.limite_credito_cliente - self.saldo_actual)

class Proveedor(models.Model):
    """
    Modelo específico para proveedores (extensión de Empresa)
    """
    empresa = models.OneToOneField(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='proveedor',
        verbose_name="Empresa"
    )
    
    # Información específica del proveedor
    TIPO_PROVEEDOR_CHOICES = [
        ('MATERIA_PRIMA', 'Materia Prima'),
        ('SERVICIOS', 'Servicios'),
        ('EQUIPAMIENTO', 'Equipamiento'),
        ('LOGISTICA', 'Logística'),
        ('OTRO', 'Otro'),
    ]
    tipo_proveedor = models.CharField(
        max_length=20, 
        choices=TIPO_PROVEEDOR_CHOICES, 
        default='OTRO',
        verbose_name="Tipo de Proveedor"
    )
    
    # Clasificación
    CATEGORIA_PROVEEDOR_CHOICES = [
        ('A', 'Categoría A - Estratégico'),
        ('B', 'Categoría B - Importante'),
        ('C', 'Categoría C - Regular'),
        ('D', 'Categoría D - Ocasional'),
    ]
    categoria = models.CharField(
        max_length=1, 
        choices=CATEGORIA_PROVEEDOR_CHOICES, 
        default='D',
        verbose_name="Categoría"
    )
    
    # Información comercial
    condiciones_pago = models.CharField(max_length=100, blank=True, verbose_name="Condiciones de Pago")
    dias_credito_proveedor = models.IntegerField(default=0, verbose_name="Días de Crédito")
    
    # Historial comercial
    fecha_primer_compra = models.DateField(null=True, blank=True, verbose_name="Fecha Primera Compra")
    fecha_ultima_compra = models.DateField(null=True, blank=True, verbose_name="Fecha Última Compra")
    total_compras = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0, 
        verbose_name="Total de Compras"
    )
    numero_compras = models.IntegerField(default=0, verbose_name="Número de Compras")
    
    # Evaluación del proveedor
    calificacion_calidad = models.DecimalField(
        max_digits=3, 
        decimal_places=1, 
        default=0, 
        verbose_name="Calificación Calidad (1-10)"
    )
    calificacion_entrega = models.DecimalField(
        max_digits=3, 
        decimal_places=1, 
        default=0, 
        verbose_name="Calificación Entrega (1-10)"
    )
    calificacion_precio = models.DecimalField(
        max_digits=3, 
        decimal_places=1, 
        default=0, 
        verbose_name="Calificación Precio (1-10)"
    )
    
    # Estado del proveedor
    ESTADO_PROVEEDOR_CHOICES = [
        ('ACTIVO', 'Activo'),
        ('INACTIVO', 'Inactivo'),
        ('SUSPENDIDO', 'Suspendido'),
        ('BLOQUEADO', 'Bloqueado'),
    ]
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_PROVEEDOR_CHOICES, 
        default='ACTIVO',
        verbose_name="Estado"
    )
    
    # Información adicional
    productos_servicios = models.TextField(blank=True, verbose_name="Productos/Servicios")
    certificaciones = models.TextField(blank=True, verbose_name="Certificaciones")
    notas_evaluacion = models.TextField(blank=True, verbose_name="Notas de Evaluación")
    
    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ['empresa__nombre']
    
    def __str__(self):
        return f"Proveedor: {self.empresa.nombre}"
    
    def calcular_antiguedad(self):
        """Calcula la antigüedad del proveedor en días"""
        if self.fecha_primer_compra:
            return (timezone.now().date() - self.fecha_primer_compra).days
        return 0
    
    def calcular_promedio_compra(self):
        """Calcula el promedio de compra"""
        if self.numero_compras > 0:
            return self.total_compras / self.numero_compras
        return 0
    
    def calcular_calificacion_promedio(self):
        """Calcula la calificación promedio"""
        calificaciones = [
            self.calificacion_calidad,
            self.calificacion_entrega,
            self.calificacion_precio
        ]
        calificaciones_validas = [c for c in calificaciones if c > 0]
        return sum(calificaciones_validas) / len(calificaciones_validas) if calificaciones_validas else 0

class LogEmpresa(models.Model):
    """
    Modelo para registrar logs de cambios en empresas
    """
    empresa = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='logs',
        verbose_name="Empresa"
    )
    usuario = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Usuario"
    )
    
    # Información del cambio
    TIPO_ACCION_CHOICES = [
        ('CREAR', 'Crear'),
        ('EDITAR', 'Editar'),
        ('ELIMINAR', 'Eliminar'),
        ('ACTIVAR', 'Activar'),
        ('DESACTIVAR', 'Desactivar'),
        ('CAMBIAR_TIPO', 'Cambiar Tipo'),
        ('CAMBIAR_CATEGORIA', 'Cambiar Categoría'),
    ]
    tipo_accion = models.CharField(
        max_length=20, 
        choices=TIPO_ACCION_CHOICES,
        verbose_name="Tipo de Acción"
    )
    
    # Detalles del cambio
    campo_modificado = models.CharField(max_length=100, blank=True, verbose_name="Campo Modificado")
    valor_anterior = models.TextField(blank=True, verbose_name="Valor Anterior")
    valor_nuevo = models.TextField(blank=True, verbose_name="Valor Nuevo")
    
    # Metadatos
    fecha_cambio = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Cambio")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Dirección IP")
    user_agent = models.TextField(blank=True, verbose_name="User Agent")
    observaciones = models.TextField(blank=True, verbose_name="Observaciones")
    
    class Meta:
        verbose_name = "Log de Empresa"
        verbose_name_plural = "Logs de Empresa"
        ordering = ['-fecha_cambio']
    
    def __str__(self):
        return f"{self.empresa.nombre} - {self.tipo_accion} - {self.fecha_cambio}" 