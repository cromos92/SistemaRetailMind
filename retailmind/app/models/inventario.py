from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal
from .catalogo import Producto_Talla
from .ventas import (
    TIPO_MOVIMIENTO_CHOICES, CONCEPTO_MOVIMIENTO_CHOICES,
    ESTADO_MOVIMIENTO_CHOICES, Ticket,
)
from .dte import Dte


def django_date_today():
    return timezone.localdate()


def django_time_now():
    return timezone.localtime().time()


class Traspaso(models.Model):
    # === RELACIONES ===
    sucursal_origen = models.ForeignKey(Sucursal, related_name='traspasos_origen', on_delete=models.CASCADE)
    sucursal_destino = models.ForeignKey(Sucursal, related_name='traspasos_destino', on_delete=models.CASCADE)
    
    # === DATOS DEL TRASPASO ===
    numero_traspaso = models.IntegerField()
    fecha_solicitud = models.DateField(auto_now_add=True)
    fecha_aprobacion = models.DateField(null=True, blank=True)
    fecha_recepcion = models.DateField(null=True, blank=True)
    
    # === ESTADOS ===
    estado = models.CharField(max_length=20, choices=[
        ('PENDIENTE', 'Pendiente'),
        ('APROBADO', 'Aprobado'),
        ('EN_TRANSITO', 'En Tránsito'),
        ('RECIBIDO', 'Recibido'),
        ('RECHAZADO', 'Rechazado'),
        ('ANULADO', 'Anulado'),
    ], default='PENDIENTE')
    
    # === RESPONSABLES ===
    solicitante = models.CharField(max_length=50)
    aprobador = models.CharField(max_length=50, null=True, blank=True)
    receptor = models.CharField(max_length=50, null=True, blank=True)
    
    # === OBSERVACIONES ===
    observaciones_solicitud = models.TextField(blank=True, null=True)
    observaciones_aprobacion = models.TextField(blank=True, null=True)
    observaciones_recepcion = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_solicitud']
        unique_together = ['sucursal_origen', 'numero_traspaso']
    
    def __str__(self):
        return f"Traspaso {self.numero_traspaso} - {self.sucursal_origen} → {self.sucursal_destino}"

class Traspaso_Detalle(models.Model):
    traspaso = models.ForeignKey(Traspaso, related_name='detalles', on_delete=models.CASCADE)
    producto_talla = models.ForeignKey(Producto_Talla, on_delete=models.CASCADE)
    cantidad_solicitada = models.IntegerField()
    cantidad_aprobada = models.IntegerField(null=True, blank=True)
    cantidad_enviada = models.IntegerField(null=True, blank=True)
    cantidad_recibida = models.IntegerField(null=True, blank=True)
    
    # === PRECIOS ===
    costo = models.IntegerField()  # Costo original del proveedor (EDEL/GILD)
    sobreprecio = models.IntegerField(default=0, verbose_name='Sobreprecio CD')  # Margen del Centro de Distribución
    costo_destino = models.IntegerField(default=0, verbose_name='Costo para Destino')  # costo + sobreprecio = costo real para sucursal destino
    precio_venta = models.IntegerField()
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    class Meta:
        unique_together = ['traspaso', 'producto_talla']
    
    def __str__(self):
        return f"Detalle {self.producto_talla} - {self.cantidad_solicitada} unidades"
    
    def save(self, *args, **kwargs):
        # Auto-calcular costo_destino si no está definido
        if self.costo_destino == 0 and self.costo > 0:
            self.costo_destino = self.costo + self.sobreprecio
        super().save(*args, **kwargs)
    
    @property
    def margen_cd_porcentaje(self):
        """Retorna el porcentaje de margen del Centro de Distribución"""
        if self.costo > 0:
            return round((self.sobreprecio / self.costo) * 100, 2)
        return 0

# ========== MODELO PARA AJUSTES DE INVENTARIO ==========

class AjusteInventario(models.Model):
    # === RELACIONES ===
    sucursal = models.ForeignKey(Sucursal, related_name='ajustes_inventario', on_delete=models.CASCADE)
    
    # === DATOS DEL AJUSTE ===
    numero_ajuste = models.IntegerField()
    fecha_ajuste = models.DateField(auto_now_add=True)
    tipo_ajuste = models.CharField(max_length=20, choices=[
        ('POSITIVO', 'Ajuste Positivo'),
        ('NEGATIVO', 'Ajuste Negativo'),
        ('INVENTARIO_FISICO', 'Inventario Físico'),
    ])
    
    # === ESTADOS ===
    estado = models.CharField(max_length=20, choices=[
        ('PENDIENTE', 'Pendiente'),
        ('APROBADO', 'Aprobado'),
        ('RECHAZADO', 'Rechazado'),
        ('COMPLETADO', 'Completado'),
    ], default='PENDIENTE')
    
    # === RESPONSABLES ===
    solicitante = models.CharField(max_length=50)
    aprobador = models.CharField(max_length=50, null=True, blank=True)
    
    # === OBSERVACIONES ===
    motivo = models.TextField()
    observaciones = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_ajuste']
        unique_together = ['sucursal', 'numero_ajuste']
    
    def __str__(self):
        return f"Ajuste {self.numero_ajuste} - {self.tipo_ajuste} - {self.sucursal}"

class AjusteInventario_Detalle(models.Model):
    ajuste = models.ForeignKey(AjusteInventario, related_name='detalles', on_delete=models.CASCADE)
    producto_talla = models.ForeignKey(Producto_Talla, on_delete=models.CASCADE)
    stock_sistema = models.IntegerField()  # Stock según el sistema
    stock_fisico = models.IntegerField()   # Stock contado físicamente
    diferencia = models.IntegerField()     # stock_fisico - stock_sistema
    costo = models.IntegerField()
    precio_venta = models.IntegerField()
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    class Meta:
        unique_together = ['ajuste', 'producto_talla']
    
    def __str__(self):
        return f"Detalle {self.producto_talla} - Diferencia: {self.diferencia}"

class Movimientos_Producto(models.Model):
    # === RELACIONES ===
    dte = models.ForeignKey(Dte, related_name='dte_movimientos', on_delete=models.CASCADE, null=True, blank=True)
    ticket = models.ForeignKey('Ticket', related_name='ticket_movimientos', on_delete=models.CASCADE, null=True, blank=True)
    ProductoTalla = models.ForeignKey(Producto_Talla, related_name='movimientos_productos_talla', on_delete=models.CASCADE)
    sucursal_origen = models.ForeignKey(Sucursal, related_name='movimientos_origen', on_delete=models.CASCADE, null=True, blank=True)
    sucursal_destino = models.ForeignKey(Sucursal, related_name='movimientos_destino', on_delete=models.CASCADE, null=True, blank=True)
    
    # === DATOS DEL MOVIMIENTO ===
    cantidad = models.IntegerField(default=0)  # Cantidad movida (positiva para ingresos, negativa para egresos)
    costo = models.IntegerField(default=0)
    sobreprecio = models.IntegerField(default=0)
    precio = models.IntegerField(default=0)
    fecha = models.DateField(default=django_date_today, blank=True)
    hora = models.TimeField(default=django_time_now, blank=True)
    
    # === CONCEPTOS Y ESTADOS ===
    concepto = models.CharField(max_length=50, choices=CONCEPTO_MOVIMIENTO_CHOICES, default='INGRESO_INICIAL')
    tipo_movimiento = models.CharField(max_length=50, choices=TIPO_MOVIMIENTO_CHOICES, default='INGRESO')
    estado = models.CharField(max_length=20, choices=ESTADO_MOVIMIENTO_CHOICES, default='COMPLETADO')
    
    # === RESPONSABLES ===
    responsable = models.CharField(max_length=50, default='Sistema')
    aprobado_por = models.CharField(max_length=50, null=True, blank=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    referencia_externa = models.CharField(max_length=100, blank=True, null=True)  # Número de factura externa, etc.
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha', '-hora']
        indexes = [
            models.Index(fields=['fecha', 'tipo_movimiento']),
            models.Index(fields=['ProductoTalla', 'fecha']),
            models.Index(fields=['concepto', 'estado']),
            # ✅ OPTIMIZACIÓN: Índices para búsquedas por sucursal (carga inicial rápida)
            models.Index(fields=['sucursal_origen', 'fecha']),
            models.Index(fields=['sucursal_destino', 'fecha']),
            models.Index(fields=['-fecha', '-hora']),  # Índice para ordenamiento descendente
        ]
    
    def __str__(self):
        return f"Movimiento {self.tipo_movimiento} - {self.concepto} - {self.cantidad}"
    
    def save(self, *args, **kwargs):
        # Auto-determinar tipo de movimiento basado en el concepto
        if not self.tipo_movimiento:
            if self.concepto.startswith(('INGRESO_', 'RECEPCION_', 'DEVOLUCION_CLIENTE', 'TRASPASO_ENTRADA', 'AJUSTE_POSITIVO', 'DONACION_RECIBIDA')):
                self.tipo_movimiento = 'INGRESO'
            elif self.concepto.startswith(('VENTA_', 'TRASPASO_SALIDA', 'AJUSTE_NEGATIVO', 'PERDIDA_', 'DONACION_ENTREGADA', 'DEVOLUCION_PROVEEDOR')):
                self.tipo_movimiento = 'EGRESO'
            elif self.concepto.startswith('TRASPASO_'):
                self.tipo_movimiento = 'TRASPASO'
        
        # Auto-asignar fecha y hora si no están presentes
        if not self.fecha:
            from django.utils import timezone
            self.fecha = timezone.localdate()
        
        if not self.hora:
            from django.utils import timezone
            self.hora = timezone.localtime().time()
        
        super().save(*args, **kwargs)

# ========== MODELO PARA LOTES FIFO ==========

class LoteProducto(models.Model):
    """
    Modelo para implementar metodología FIFO (First In, First Out)
    Cada lote representa una entrada de inventario con su propio costo y fecha
    """
    # === RELACIONES ===
    producto_talla = models.ForeignKey(Producto_Talla, related_name='lotes', on_delete=models.CASCADE)
    dte = models.ForeignKey(Dte, related_name='lotes_producto', on_delete=models.CASCADE, null=True, blank=True)
    movimiento = models.ForeignKey(Movimientos_Producto, related_name='lotes_generados', on_delete=models.CASCADE, null=True, blank=True)
    
    # === DATOS DEL LOTE ===
    cantidad_inicial = models.IntegerField()  # Cantidad total del lote
    cantidad_disponible = models.IntegerField()  # Cantidad restante del lote
    costo_unitario = models.IntegerField()  # Costo por unidad del lote
    sobreprecio_unitario = models.IntegerField(default=0)  # Sobreprecio por unidad
    precio_venta_unitario = models.IntegerField()  # Precio de venta por unidad
    
    # === FECHAS ===
    fecha_ingreso = models.DateTimeField(auto_now_add=True)
    fecha_vencimiento = models.DateField(null=True, blank=True)  # Para productos perecederos
    
    # === ESTADOS ===
    activo = models.BooleanField(default=True)  # Si el lote está activo
    agotado = models.BooleanField(default=False)  # Si se agotó completamente
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    numero_lote = models.CharField(max_length=50, blank=True, null=True)  # Número de lote del proveedor
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['fecha_ingreso']  # Ordenar por fecha de ingreso para FIFO
        indexes = [
            models.Index(fields=['producto_talla', 'fecha_ingreso']),
            models.Index(fields=['agotado', 'activo']),
            models.Index(fields=['fecha_vencimiento']),
        ]
    
    def __str__(self):
        return f"Lote {self.id} - {self.producto_talla} - {self.cantidad_disponible}/{self.cantidad_inicial}"
    
    def save(self, *args, **kwargs):
        # Auto-marcar como agotado si no hay stock disponible
        if self.cantidad_disponible <= 0:
            self.agotado = True
        super().save(*args, **kwargs)
    
    @property
    def valor_disponible(self):
        """Valor total del stock disponible en este lote"""
        return self.cantidad_disponible * self.costo_unitario
    
    @property
    def porcentaje_consumido(self):
        """Porcentaje del lote que ha sido consumido"""
        if self.cantidad_inicial == 0:
            return 0
        return ((self.cantidad_inicial - self.cantidad_disponible) / self.cantidad_inicial) * 100

class TomaInventario(models.Model):
    """
    Modelo principal para gestionar tomas de inventario físico.
    Implementa mejores prácticas de logística:
    - Inventario por segmentos (marca, categoría, atributo)
    - Fecha de corte para congelamiento de datos
    - Análisis previo antes de aplicar ajustes
    - Procesamiento en lotes para grandes volúmenes
    """
    
    # === TIPOS DE INVENTARIO ===
    TIPO_INVENTARIO_CHOICES = [
        ('COMPLETO', 'Inventario Completo'),
        ('POR_MARCA', 'Por Marca'),
        ('POR_CATEGORIA', 'Por Categoría/Departamento'),
        ('POR_ATRIBUTO', 'Por Atributo'),
        ('SELECTIVO', 'Selectivo (Productos específicos)'),
        ('CICLICO', 'Cíclico (ABC)'),
        ('ALEATORIO', 'Aleatorio (Muestreo)'),
    ]
    
    # === ESTADOS DEL INVENTARIO ===
    ESTADO_CHOICES = [
        ('BORRADOR', 'Borrador'),
        ('EN_CONTEO', 'En Conteo'),
        ('CONTEO_FINALIZADO', 'Conteo Finalizado'),
        ('EN_REVISION', 'En Revisión'),
        ('PENDIENTE_APROBACION', 'Pendiente de Aprobación'),
        ('APROBADO', 'Aprobado'),
        ('APLICANDO', 'Aplicando Ajustes'),
        ('COMPLETADO', 'Completado'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    # === IDENTIFICACIÓN ===
    numero_inventario = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Número de Inventario',
        help_text='Identificador único del inventario'
    )
    nombre = models.CharField(
        max_length=200,
        verbose_name='Nombre/Descripción',
        help_text='Nombre descriptivo del inventario'
    )
    
    # === RELACIONES ===
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='tomas_inventario',
        verbose_name='Sucursal'
    )
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name='tomas_inventario',
        verbose_name='Empresa'
    )
    
    # === CONFIGURACIÓN DEL INVENTARIO ===
    tipo_inventario = models.CharField(
        max_length=20,
        choices=TIPO_INVENTARIO_CHOICES,
        default='COMPLETO',
        verbose_name='Tipo de Inventario'
    )
    
    # Filtros de segmentación (JSON para flexibilidad)
    filtros_aplicados = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Filtros Aplicados',
        help_text='Filtros JSON: {"marcas": [1,2,3], "categorias": [4,5], "atributos": {"color": "rojo"}}'
    )
    
    # === FECHA DE CORTE (Crítico para inventarios) ===
    fecha_corte = models.DateTimeField(
        verbose_name='Fecha de Corte',
        help_text='Momento exacto en que se congela el stock del sistema para comparación'
    )
    fecha_inicio_conteo = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha Inicio Conteo'
    )
    fecha_fin_conteo = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha Fin Conteo'
    )
    
    # === ESTADO Y PROGRESO ===
    estado = models.CharField(
        max_length=25,
        choices=ESTADO_CHOICES,
        default='BORRADOR',
        verbose_name='Estado'
    )
    progreso_conteo = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name='Progreso del Conteo (%)'
    )
    
    # === MÉTRICAS CALCULADAS (Snapshot para reportes) ===
    total_productos_esperados = models.IntegerField(
        default=0,
        verbose_name='Total Productos a Contar'
    )
    total_productos_contados = models.IntegerField(
        default=0,
        verbose_name='Total Productos Contados'
    )
    total_diferencias_positivas = models.IntegerField(
        default=0,
        verbose_name='Diferencias Positivas (Sobrantes)'
    )
    total_diferencias_negativas = models.IntegerField(
        default=0,
        verbose_name='Diferencias Negativas (Faltantes)'
    )
    valor_diferencias_positivas = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        verbose_name='Valor Sobrantes'
    )
    valor_diferencias_negativas = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        verbose_name='Valor Faltantes'
    )
    valor_inventario_sistema = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        verbose_name='Valor Inventario Sistema'
    )
    valor_inventario_fisico = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        verbose_name='Valor Inventario Físico'
    )
    
    # === RESPONSABLES ===
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='inventarios_creados',
        verbose_name='Creado Por'
    )
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventarios_aprobados',
        verbose_name='Aprobado Por'
    )
    fecha_aprobacion = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha de Aprobación'
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(
        blank=True,
        null=True,
        verbose_name='Observaciones'
    )
    motivo_cancelacion = models.TextField(
        blank=True,
        null=True,
        verbose_name='Motivo de Cancelación'
    )
    
    # === AUDITORÍA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Toma de Inventario'
        verbose_name_plural = 'Tomas de Inventario'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sucursal', 'estado']),
            models.Index(fields=['fecha_corte']),
            models.Index(fields=['numero_inventario']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f"{self.numero_inventario} - {self.sucursal.alias} ({self.get_estado_display()})"
    
    @classmethod
    def generar_numero_inventario(cls, sucursal):
        """Genera un número único de inventario"""
        fecha = timezone.localdate().strftime('%Y%m%d')
        count = cls.objects.filter(
            numero_inventario__startswith=f"INV-{sucursal.id}-{fecha}"
        ).count() + 1
        return f"INV-{sucursal.id}-{fecha}-{count:03d}"
    
    def calcular_metricas(self):
        """Recalcula las métricas del inventario"""
        from django.db.models import Sum, Count, Case, When, F
        from decimal import Decimal
        
        detalles = self.detalles.all()
        detalles_analisis = detalles.filter(excluir_de_analisis=False)
        
        self.total_productos_esperados = detalles_analisis.count()
        productos_contados = detalles_analisis.filter(contado=True).count()
        self.total_productos_contados = detalles_analisis.filter(contado=True).aggregate(
            total=Sum('stock_fisico')
        )['total'] or 0
        
        # Diferencias
        diferencias = detalles_analisis.filter(contado=True).aggregate(
            positivas=Sum(Case(
                When(diferencia__gt=0, then=F('diferencia')),
                default=0
            )),
            negativas=Sum(Case(
                When(diferencia__lt=0, then=F('diferencia')),
                default=0
            )),
            valor_positivas=Sum(Case(
                When(diferencia__gt=0, then=F('diferencia') * F('costo_unitario_sistema')),
                default=Decimal('0')
            )),
            valor_negativas=Sum(Case(
                When(diferencia__lt=0, then=F('diferencia') * F('costo_unitario_sistema')),
                default=Decimal('0')
            )),
            valor_sistema=Sum(F('stock_sistema_ajustado') * F('costo_unitario_sistema')),
            valor_fisico=Sum(F('stock_fisico') * F('costo_unitario_sistema'))
        )
        
        self.total_diferencias_positivas = diferencias['positivas'] or 0
        self.total_diferencias_negativas = abs(diferencias['negativas'] or 0)
        self.valor_diferencias_positivas = diferencias['valor_positivas'] or Decimal('0')
        self.valor_diferencias_negativas = abs(diferencias['valor_negativas'] or Decimal('0'))
        self.valor_inventario_sistema = diferencias['valor_sistema'] or Decimal('0')
        self.valor_inventario_fisico = diferencias['valor_fisico'] or Decimal('0')
        
        # Progreso
        if self.total_productos_esperados > 0:
            self.progreso_conteo = (productos_contados / self.total_productos_esperados) * 100
        
        self.save()
    
    def puede_aprobar(self):
        """Verifica si el inventario puede ser aprobado"""
        return (
            self.estado == 'PENDIENTE_APROBACION' and
            self.progreso_conteo == 100 and
            self.total_productos_contados == self.total_productos_esperados
        )


class TomaInventarioDetalle(models.Model):
    """
    Detalle de cada producto en la toma de inventario.
    Guarda el snapshot del sistema en fecha de corte y el conteo físico.
    """
    
    # === RELACIONES ===
    toma_inventario = models.ForeignKey(
        TomaInventario,
        on_delete=models.CASCADE,
        related_name='detalles',
        verbose_name='Toma de Inventario'
    )
    producto_talla = models.ForeignKey(
        'Producto_Talla',
        on_delete=models.CASCADE,
        related_name='inventarios_detalle',
        verbose_name='Producto/Talla'
    )
    
    # === DATOS DEL PRODUCTO (Snapshot en fecha de corte) ===
    sku = models.CharField(max_length=100, verbose_name='SKU')
    producto_nombre = models.CharField(max_length=255, verbose_name='Nombre Producto')
    talla_nombre = models.CharField(max_length=50, blank=True, null=True, verbose_name='Talla')
    marca_nombre = models.CharField(max_length=100, blank=True, null=True, verbose_name='Marca')
    categoria_nombre = models.CharField(max_length=100, blank=True, null=True, verbose_name='Categoría')
    
    # === STOCK DEL SISTEMA (Fecha de corte) ===
    stock_sistema = models.IntegerField(
        default=0,
        verbose_name='Stock Sistema',
        help_text='Stock según el sistema en la fecha de corte'
    )
    stock_movimientos_post_corte = models.IntegerField(
        default=0,
        verbose_name='Movimientos Post Corte',
        help_text='Suma neta de movimientos después de la fecha de corte'
    )
    stock_sistema_ajustado = models.IntegerField(
        default=0,
        verbose_name='Stock Sistema Ajustado',
        help_text='Stock sistema + movimientos post corte (base para comparar)'
    )
    costo_unitario_sistema = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name='Costo Unitario',
        help_text='Costo promedio FIFO en fecha de corte'
    )
    precio_venta_sistema = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        verbose_name='Precio Venta'
    )
    
    # === CONTEO FÍSICO ===
    excluir_de_analisis = models.BooleanField(
        default=False,
        verbose_name='Excluir de análisis',
        help_text='No considerar este producto en métricas y análisis'
    )
    stock_fisico = models.IntegerField(
        default=0,
        verbose_name='Stock Físico',
        help_text='Cantidad contada físicamente'
    )
    contado = models.BooleanField(
        default=False,
        verbose_name='¿Contado?'
    )
    fecha_conteo = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha de Conteo'
    )
    usuario_conteo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='conteos_realizados',
        verbose_name='Usuario que Contó'
    )
    
    # === DIFERENCIA CALCULADA ===
    diferencia = models.IntegerField(
        default=0,
        verbose_name='Diferencia',
        help_text='stock_fisico - stock_sistema (positivo=sobrante, negativo=faltante)'
    )
    
    # === RECONTEO (Para diferencias significativas) ===
    reconteo_requerido = models.BooleanField(
        default=False,
        verbose_name='Requiere Reconteo'
    )
    stock_reconteo = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='Stock Reconteo'
    )
    fecha_reconteo = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha Reconteo'
    )
    usuario_reconteo = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reconteos_realizados',
        verbose_name='Usuario que Recontó'
    )
    
    # === UBICACIÓN (Para inventarios físicos organizados) ===
    ubicacion = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Ubicación',
        help_text='Estante, pasillo, zona, etc.'
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(
        blank=True,
        null=True,
        verbose_name='Observaciones'
    )
    
    # === ESTADO DEL AJUSTE ===
    ajuste_aplicado = models.BooleanField(
        default=False,
        verbose_name='Ajuste Aplicado'
    )
    fecha_ajuste = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha de Ajuste'
    )
    
    class Meta:
        verbose_name = 'Detalle de Inventario'
        verbose_name_plural = 'Detalles de Inventario'
        unique_together = ['toma_inventario', 'producto_talla']
        ordering = ['producto_nombre', 'talla_nombre']
        indexes = [
            models.Index(fields=['toma_inventario', 'contado']),
            models.Index(fields=['toma_inventario', 'diferencia']),
            models.Index(fields=['sku']),
        ]
    
    def __str__(self):
        return f"{self.sku} - {self.producto_nombre} ({self.diferencia:+d})"
    
    def save(self, *args, **kwargs):
        # Calcular diferencia automáticamente
        if self.contado:
            base_stock = self.stock_sistema_ajustado if self.stock_sistema_ajustado is not None else self.stock_sistema
            self.diferencia = self.stock_fisico - base_stock
            
            # Marcar para reconteo si diferencia > 10% o > 5 unidades
            if abs(self.diferencia) > 5 or (base_stock > 0 and abs(self.diferencia) / base_stock > 0.1):
                if not self.reconteo_requerido and self.stock_reconteo is None:
                    self.reconteo_requerido = True
        
        super().save(*args, **kwargs)
    
    @property
    def porcentaje_diferencia(self):
        """Calcula el porcentaje de diferencia"""
        base_stock = self.stock_sistema_ajustado if self.stock_sistema_ajustado is not None else self.stock_sistema
        if base_stock > 0:
            return (self.diferencia / base_stock) * 100
        return 0 if self.diferencia == 0 else 100
    
    @property
    def valor_diferencia(self):
        """Calcula el valor monetario de la diferencia"""
        return self.diferencia * self.costo_unitario_sistema


class TomaInventarioLog(models.Model):
    """
    Log de auditoría para cambios en la toma de inventario.
    Registra cada acción para trazabilidad completa.
    """
    
    TIPO_ACCION_CHOICES = [
        ('CREACION', 'Creación'),
        ('INICIO_CONTEO', 'Inicio de Conteo'),
        ('REGISTRO_CONTEO', 'Registro de Conteo'),
        ('RECONTEO', 'Reconteo'),
        ('CAMBIO_ESTADO', 'Cambio de Estado'),
        ('ENVIO_APROBACION', 'Envío a Aprobación'),
        ('APROBACION', 'Aprobación'),
        ('RECHAZO', 'Rechazo'),
        ('APLICACION_AJUSTES', 'Aplicación de Ajustes'),
        ('CANCELACION', 'Cancelación'),
        ('MODIFICACION', 'Modificación'),
    ]
    
    toma_inventario = models.ForeignKey(
        TomaInventario,
        on_delete=models.CASCADE,
        related_name='logs'
    )
    tipo_accion = models.CharField(max_length=25, choices=TIPO_ACCION_CHOICES)
    descripcion = models.TextField()
    datos_adicionales = models.JSONField(default=dict, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Log de Inventario'
        verbose_name_plural = 'Logs de Inventario'
    
    def __str__(self):
        return f"{self.toma_inventario.numero_inventario} - {self.get_tipo_accion_display()}"


class TareaAplicacionAjustes(models.Model):
    """
    Modelo para rastrear el progreso de la aplicación de ajustes de inventario
    en background. Permite al frontend hacer polling para mostrar progreso real.
    """
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('EN_PROCESO', 'En proceso'),
        ('COMPLETADO', 'Completado'),
        ('ERROR', 'Error'),
    ]

    inventario = models.OneToOneField(
        TomaInventario,
        on_delete=models.CASCADE,
        related_name='tarea_ajustes'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='PENDIENTE')
    total = models.IntegerField(default=0)
    procesados = models.IntegerField(default=0)
    errores = models.JSONField(default=list)
    iniciada_en = models.DateTimeField(null=True, blank=True)
    finalizada_en = models.DateTimeField(null=True, blank=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    class Meta:
        verbose_name = 'Tarea de Aplicación de Ajustes'
        verbose_name_plural = 'Tareas de Aplicación de Ajustes'

    def __str__(self):
        return f"Tarea ajustes {self.inventario.numero_inventario} - {self.get_estado_display()}"

    @property
    def porcentaje(self):
        if self.total and self.total > 0:
            return int(self.procesados / self.total * 100)
        return 0
