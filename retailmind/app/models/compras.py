from django.db import models
from django.utils import timezone
from .organizacion import Empresa, Sucursal
from .catalogo import Producto_Talla, TIPO_TALLA_CHOICES
from .dte import Dte, Dte_Productos, ESTADO_RECEPCION_PRODUCTO_CHOICES, TIPO_PROBLEMA_CHOICES, TIPO_SOLUCION_CHOICES, ESTADO_SOLICITUD_CHOICES

class Compras(models.Model):
    # Estados de la compra
    ESTADO_CHOICES = [
        ('ACTIVA', 'Activa'),
        ('COMPLETADA', 'Completada'),
        ('ELIMINADA', 'Eliminada'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    empresa =   models.ForeignKey(Empresa,   on_delete=models.CASCADE)
    nombre=   models.CharField(max_length=200)
    correlativo = models.IntegerField()
    responsable=   models.CharField(max_length=50)
    temporada=   models.CharField(max_length=50)
    fecha =   models.DateField( auto_now=True)
    fechaInicioTemporada =   models.DateField(null=True,blank=True)
    fechaTerminoTemporada =   models.DateField(null=True,blank=True)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='ACTIVA')
    fecha_eliminacion = models.DateTimeField(null=True, blank=True)
    eliminado_por = models.CharField(max_length=100, null=True, blank=True)
    tipo = models.CharField(max_length=50, default='inicial', choices=[
        ('inicial', 'Compra Inicial'),
        ('reposicion', 'Reposición'),
        ('urgente', 'Urgente'),
    ])
    generada_por_sistema = models.BooleanField(default=False,
        help_text='True si fue generada por el motor de predicción')
    fecha_envio_proveedor = models.DateField(null=True, blank=True)
    fecha_entrega_estimada = models.DateField(null=True, blank=True,
        help_text='Calculada: fecha_envio + Empresa.lead_time_dias')
    fecha_entrega_real = models.DateField(null=True, blank=True)
    
    def __str__(self):
        return f"Compras   {self.nombre} - {self.temporada}"
    
    @property
    def esta_eliminada(self):
        return self.estado == 'ELIMINADA'
class Compras_Producto(models.Model):
    compras =   models.ForeignKey(Compras,   on_delete=models.CASCADE)
    nombre=   models.CharField(max_length=200)
    descripcion=   models.CharField(max_length=200,null=True,blank=True)
    atributo1=   models.CharField(max_length=200)
    atributo2=   models.CharField(max_length=200)
    atributo3=   models.CharField(max_length=200)
    atributo4=   models.CharField(max_length=200)
    tipo_talla = models.CharField(max_length=5, choices=TIPO_TALLA_CHOICES, default='CL')
    costo = models.IntegerField()
    precioSugerido = models.IntegerField()
    fecha =   models.DateField( auto_now=True)

    es_reposicion = models.BooleanField(
        default=False,
        verbose_name='Es reposición',
        help_text='True = reposición de stock existente, False = producto nuevo',
    )
    precio_anterior = models.IntegerField(
        null=True, blank=True,
        verbose_name='Precio anterior',
        help_text='Precio de venta vigente antes de esta compra (solo aplica en reposiciones)',
    )
    precio_nuevo = models.IntegerField(
        null=True, blank=True,
        verbose_name='Precio nuevo',
        help_text='Precio de venta que traerá esta compra (si difiere del anterior)',
    )

    def __str__(self):
        return f"Compras_Producto   {self.nombre} - {self.compras}"

    @property
    def precio_cambio(self):
        if self.precio_anterior and self.precio_nuevo and self.precio_anterior != self.precio_nuevo:
            return self.precio_nuevo - self.precio_anterior
        return 0
class Compras_Producto_Talla(models.Model):
    compra_producto =   models.ForeignKey(Compras_Producto,   on_delete=models.CASCADE)
    stock=   models.IntegerField()
    talla=   models.CharField(max_length=50)
    producto_talla = models.ForeignKey(
        Producto_Talla, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='items_compra',
        help_text='FK al producto real del sistema. Se vincula al recepcionar.',
    )
    unidades_recibidas = models.IntegerField(default=0)
    estado_item = models.CharField(max_length=50, default='pendiente', choices=[
        ('pendiente', 'Pendiente'),
        ('recibido_parcial', 'Recibido Parcial'),
        ('recibido_completo', 'Recibido Completo'),
        ('cancelado', 'Cancelado'),
    ])

    def __str__(self):
        return f"Compras_Producto_Talla   {self.compra_producto} - {self.stock}"

    @property
    def unidades_en_transito(self):
        if self.compra_producto.compras.estado in ['ACTIVA']:
            return max(0, self.stock - self.unidades_recibidas)
        return 0
class Productos_Recepcionados(models.Model):
    """
    Modelo unificado para recepciones de productos.
    Sirve para:
    - Recepciones de compras (compra_producto_talla)
    - Recepciones de traspasos internos (dte + dte_producto)
    """
    # Para compras (legacy - mantener compatibilidad)
    compra_producto_talla = models.ForeignKey(
        Compras_Producto_Talla, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        help_text="Para recepciones de compras"
    )
    
    # Para traspasos internos (nuevo)
    dte = models.ForeignKey(
        Dte, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='recepciones',
        help_text="DTE de traspaso interno"
    )
    dte_producto = models.ForeignKey(
        Dte_Productos, 
        on_delete=models.CASCADE, 
        null=True, 
        blank=True,
        related_name='recepcion',
        help_text="Producto específico del DTE"
    )
    
    # Común para ambos
    producto_talla = models.ForeignKey(Producto_Talla, on_delete=models.CASCADE, null=True, blank=True)
    
    # Cantidades
    stockArribado = models.IntegerField(help_text="Cantidad recepcionada (nombre legacy)")
    cantidad_esperada = models.IntegerField(default=0, help_text="Cantidad original esperada")
    cantidad_danada = models.IntegerField(default=0, help_text="Cantidad con daños")
    cantidad_faltante = models.IntegerField(default=0, help_text="Cantidad que no llegó")
    
    # Estado de recepción (nuevo)
    estado = models.CharField(
        max_length=30,
        choices=ESTADO_RECEPCION_PRODUCTO_CHOICES,
        default='RECEPCIONADO_OK',
        help_text="Estado de la recepción"
    )
    observaciones = models.TextField(blank=True, null=True, help_text="Observaciones o problemas")
    
    # Reposición / precio
    es_reposicion = models.BooleanField(
        default=False,
        verbose_name='Es reposición',
        help_text='Hereda de Compras_Producto al momento de recepcionar',
    )
    precio_anterior = models.IntegerField(
        null=True, blank=True,
        verbose_name='Precio anterior al momento de recepción',
    )
    precio_nuevo = models.IntegerField(
        null=True, blank=True,
        verbose_name='Precio nuevo tras recepción',
    )
    sucursal_destino = models.ForeignKey(
        Sucursal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recepciones_destino',
        verbose_name='Sucursal destino',
        help_text='Sucursal donde se recepcionó el producto',
    )

    # Auditoría
    fecha = models.DateField(auto_now=True)
    fecha_recepcion = models.DateTimeField(null=True, blank=True)
    recepcionado_por = models.CharField(max_length=100, blank=True, null=True)
    fecha_regularizacion = models.DateTimeField(null=True, blank=True)
    regularizado_por = models.CharField(max_length=100, blank=True, null=True)
    
    # ✅ AGREGADO: Trazabilidad completa - vincular recepción con movimiento
    movimiento_ingreso = models.ForeignKey(
        'Movimientos_Producto',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recepciones_asociadas',
        help_text="Movimiento de ingreso generado por esta recepción"
    )
    
    class Meta:
        verbose_name = 'Producto Recepcionado'
        verbose_name_plural = 'Productos Recepcionados'
        indexes = [
            models.Index(fields=['dte', 'estado']),
            models.Index(fields=['estado']),
            models.Index(fields=['fecha']),
        ]
    
    def __str__(self):
        if self.dte:
            return f"Recepción DTE #{self.dte.numero_documento} - {self.producto_talla.sku if self.producto_talla else 'N/A'}"
        return f"Recepción Compra - {self.compra_producto_talla} - {self.stockArribado}"
    
    @property
    def tiene_problemas(self):
        """Indica si este producto tiene problemas en la recepción"""
        return self.estado in ['RECEPCIONADO_PARCIAL', 'RECEPCIONADO_DANADO', 'FALTANTE', 'EN_REGULARIZACION']
    
    @property
    def esta_ok(self):
        """Indica si el producto fue recepcionado correctamente"""
        return self.estado == 'RECEPCIONADO_OK' and self.stockArribado == self.cantidad_esperada
    
    @property
    def es_recepcion_traspaso(self):
        """Indica si es una recepción de traspaso interno"""
        return self.dte is not None and self.dte_producto is not None
    
    @property
    def es_recepcion_compra(self):
        """Indica si es una recepción de compra"""
        return self.compra_producto_talla is not None


class Solicitud_Regularizacion(models.Model):
    """
    Modelo para gestionar solicitudes de regularización entre empresas diferentes.
    El RECEPTOR crea la solicitud, el EMISOR la aprueba y ejecuta.
    """
    # Identificación
    numero_solicitud = models.CharField(
        max_length=20, 
        unique=True,
        help_text="Número único de la solicitud (ej: SOL-001)"
    )
    fecha_solicitud = models.DateTimeField(
        auto_now_add=True,
        help_text="Fecha y hora de creación de la solicitud"
    )
    
    # Relaciones con DTE y Producto
    dte_original = models.ForeignKey(
        Dte,
        on_delete=models.CASCADE,
        related_name='solicitudes_regularizacion',
        help_text="DTE original que tiene el problema"
    )
    producto_recepcionado = models.ForeignKey(
        Productos_Recepcionados,
        on_delete=models.CASCADE,
        related_name='solicitudes',
        help_text="Producto recepcionado con problema"
    )
    
    # Partes involucradas
    sucursal_solicitante = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='solicitudes_enviadas',
        help_text="Sucursal que SOLICITA (receptor del DTE original)"
    )
    sucursal_emisora = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='solicitudes_recibidas',
        help_text="Sucursal que debe APROBAR (emisor del DTE original)"
    )
    usuario_solicita = models.CharField(
        max_length=100,
        help_text="Usuario que crea la solicitud"
    )
    
    # Problema detectado
    tipo_problema = models.CharField(
        max_length=50,
        choices=TIPO_PROBLEMA_CHOICES,
        help_text="Tipo de problema detectado"
    )
    cantidad_problema = models.IntegerField(
        help_text="Cantidad de unidades con problema"
    )
    descripcion_problema = models.TextField(
        help_text="Descripción detallada del problema"
    )
    evidencia_foto = models.FileField(
        upload_to='evidencias_problemas/',
        null=True,
        blank=True,
        help_text="Foto de evidencia (opcional)"
    )
    
    # Solución solicitada por RECEPTOR
    tipo_solucion_solicitada = models.CharField(
        max_length=50,
        choices=TIPO_SOLUCION_CHOICES,
        help_text="Tipo de solución que solicita el receptor"
    )
    
    # Para caso de CAMBIO_PRODUCTO o REENVIO
    producto_cambio_solicitado = models.ForeignKey(
        'Producto_Talla',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_como_reemplazo',
        help_text="Producto que solicita como reemplazo (para CAMBIO_PRODUCTO)"
    )
    cantidad_cambio_solicitada = models.IntegerField(
        null=True,
        blank=True,
        help_text="Cantidad solicitada del producto de cambio"
    )
    
    # Respuesta del EMISOR
    estado = models.CharField(
        max_length=50,
        choices=ESTADO_SOLICITUD_CHOICES,
        default='PENDIENTE',
        help_text="Estado actual de la solicitud"
    )
    
    fecha_revision = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que el emisor revisó la solicitud"
    )
    usuario_revisa = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Usuario del emisor que revisa"
    )
    decision_emisor = models.TextField(
        null=True,
        blank=True,
        help_text="Observaciones del emisor sobre su decisión"
    )
    
    # Solución alternativa propuesta/aprobada por EMISOR
    tipo_solucion_aprobada = models.CharField(
        max_length=50,
        choices=TIPO_SOLUCION_CHOICES,
        null=True,
        blank=True,
        help_text="Tipo de solución finalmente aprobada (puede diferir de la solicitada)"
    )
    producto_cambio_aprobado = models.ForeignKey(
        'Producto_Talla',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_aprobadas',
        help_text="Producto finalmente aprobado (puede diferir del solicitado)"
    )
    cantidad_cambio_aprobada = models.IntegerField(
        null=True,
        blank=True,
        help_text="Cantidad finalmente aprobada"
    )
    
    # Ejecución de la solución
    fecha_ejecucion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que se ejecutó la solución (emisión de documentos)"
    )
    dte_solucion = models.ForeignKey(
        Dte,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='es_solucion_de',
        help_text="DTE emitido como solución (NC o nuevo DTE con producto de cambio)"
    )
    nota_credito = models.ForeignKey(
        Dte,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='nc_de_solicitud',
        help_text="Nota de crédito emitida (si aplica)"
    )
    
    # Confirmación del RECEPTOR
    fecha_confirmacion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que el receptor confirmó la solución"
    )
    usuario_confirma = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Usuario que confirmó la recepción de la solución"
    )
    conformidad = models.BooleanField(
        null=True,
        blank=True,
        help_text="Si el receptor quedó conforme con la solución"
    )
    observaciones_finales = models.TextField(
        null=True,
        blank=True,
        help_text="Observaciones finales del receptor"
    )
    
    class Meta:
        db_table = 'solicitudes_regularizacion'
        verbose_name = 'Solicitud de Regularización'
        verbose_name_plural = 'Solicitudes de Regularización'
        ordering = ['-fecha_solicitud']
        indexes = [
            models.Index(fields=['sucursal_emisora', 'estado']),
            models.Index(fields=['sucursal_solicitante', 'estado']),
            models.Index(fields=['estado']),
            models.Index(fields=['fecha_solicitud']),
            models.Index(fields=['numero_solicitud']),
        ]
    
    def __str__(self):
        return f"Solicitud #{self.numero_solicitud} - {self.sucursal_solicitante.alias} → {self.sucursal_emisora.alias}"
    
    @property
    def esta_pendiente(self):
        """Indica si la solicitud está pendiente de revisión"""
        return self.estado in ['PENDIENTE', 'EN_REVISION']
    
    @property
    def puede_ejecutarse(self):
        """Indica si la solicitud puede ejecutarse"""
        return self.estado == 'APROBADA'
    
    @property
    def esta_completada(self):
        """Indica si todo el ciclo está completo"""
        return self.estado == 'COMPLETADA'
    
    @property
    def dias_pendiente(self):
        """Días que lleva pendiente la solicitud"""
        if self.esta_pendiente:
            from django.utils import timezone
            return (timezone.now() - self.fecha_solicitud).days
        return 0
    
    @property
    def producto_original_info(self):
        """Información del producto original con problema"""
        if self.producto_recepcionado and self.producto_recepcionado.producto_talla:
            return {
                'sku': self.producto_recepcionado.producto_talla.sku,
                'nombre': self.producto_recepcionado.producto_talla.producto.articulo if self.producto_recepcionado.producto_talla.producto else 'N/A',
                'talla': self.producto_recepcionado.producto_talla.talla,
            }
        return None
    
    @property
    def producto_solucion_info(self):
        """Información del producto de solución"""
        producto = self.producto_cambio_aprobado or self.producto_cambio_solicitado
        if producto:
            return {
                'sku': producto.sku,
                'nombre': producto.producto.articulo if producto.producto else 'N/A',
                'talla': producto.talla,
                'stock_disponible': producto.stock,
            }
        return None

