from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.conf import settings
from .organizacion import Sucursal, Vendedor
from .catalogo import Producto_Talla


METODO_PAGO_TICKET_CHOICES = [
    ('EFECTIVO', 'Efectivo'),
    # ⚠️ TARJETA_DEBITO y TARJETA_CREDITO son genéricos (datos históricos/migrados)
    # Para nuevas transacciones usar TBK_DEBITO_POS y TBK_CREDITO_POS
    ('TARJETA_DEBITO', 'Tarjeta Débito'),
    ('TARJETA_CREDITO', 'Tarjeta Crédito'),
    ('TRANSFERENCIA', 'Transferencia'),
    ('CHEQUE', 'Cheque'),
    ('OTRO', 'Otro'),
    # Métodos Transbank (usar estos para nuevas transacciones)
    ('TBK_POS_INTEGRADO', 'Transbank POS Integrado'),
    ('TBK_MANUAL', 'Transbank Manual'),
    ('TBK_DEBITO_POS', 'Transbank Débito POS'),
    ('TBK_CREDITO_POS', 'Transbank Crédito POS'),
    ('TBK_PREPAGO_POS', 'Transbank Prepago POS'),
    # Otros métodos de pago
    ('TARJETA_COMERCIAL', 'Tarjeta Comercial'),
    ('VENTA_INTERNET', 'Venta por Internet'),
    ('ORDEN_COMPRA', 'Orden de Compra'),
    ('CREDITO_TRABAJADOR', 'Crédito Trabajador'),
    ('CREDITO_EXTERNO', 'Crédito Externo'),
    ('CONVENIO', 'Convenio'),
    ('MULTIPLE', 'Pagos Combinados'),
]

# ========== CONSTANTES PARA MOVIMIENTOS ==========
TIPO_MOVIMIENTO_CHOICES = [
    ('INGRESO', 'Ingreso'),
    ('EGRESO', 'Egreso'),
    ('TRASPASO', 'Traspaso'),
    ('AJUSTE', 'Ajuste'),
    ('DEVOLUCION', 'Devolución'),
    ('PERDIDA', 'Pérdida'),
    ('DONACION', 'Donación'),
]

CONCEPTO_MOVIMIENTO_CHOICES = [
    # === INGRESOS ===
    ('INGRESO_INICIAL', 'Ingreso Inicial'),
    ('INGRESO_MANUAL', 'Ingreso Manual'),  # ✅ AGREGADO: Para creación manual de productos
    ('RECEPCION_COMPRA', 'Recepción de Compra'),
    ('REPOSICION_STOCK', 'Reposición de Stock'),  # ✅ AGREGADO: Para reposiciones
    ('DEVOLUCION_CLIENTE', 'Devolución de Cliente'),
    ('TRASPASO_ENTRADA', 'Traspaso Entrada'),
    ('REGULARIZACION_TRASPASO', 'Regularización de Traspaso'),
    ('AJUSTE_POSITIVO', 'Ajuste Positivo'),
    ('DONACION_RECIBIDA', 'Donación Recibida'),
    
    # === EGRESOS ===
    ('VENTA_PUBLICO', 'Venta al Público'),
    ('VENTA_MAYORISTA', 'Venta Mayorista'),
    ('TRASPASO_SALIDA', 'Traspaso Salida'),
    ('AJUSTE_NEGATIVO', 'Ajuste Negativo'),
    ('PERDIDA_ROBO', 'Pérdida por Robo'),
    ('PERDIDA_DETERIORO', 'Pérdida por Deterioro'),
    ('DONACION_ENTREGADA', 'Donación Entregada'),
    ('DEVOLUCION_PROVEEDOR', 'Devolución a Proveedor'),
    
    # === TRASPASOS ===
    ('TRASPASO_SUCURSAL', 'Traspaso entre Sucursales'),
    ('TRASPASO_BODEGA', 'Traspaso a Bodega'),
    ('TRASPASO_VITRINA', 'Traspaso a Vitrina'),
    ('CAMBIO_PRODUCTO_SALIDA', 'Cambio de Producto (Salida)'),
    ('CAMBIO_PRODUCTO_ENTRADA', 'Cambio de Producto (Entrada)'),
    
    # === CORRECCIONES ===
    ('CORRECCION_STOCK', 'Corrección de Stock'),  # ✅ AGREGADO: Para corregir errores
    ('ANULACION_REGULARIZACION', 'Anulación de Regularización'),  # ✅ AGREGADO
    
    # === AJUSTES DE INVENTARIO FÍSICO ===
    ('AJUSTE_INVENTARIO_ENTRADA', 'Ajuste Inventario - Entrada (Sobrante)'),
    ('AJUSTE_INVENTARIO_SALIDA', 'Ajuste Inventario - Salida (Faltante)'),
    
    # === DESPACHO DIFERIDO ===
    ('DESPACHO_COTIZACION', 'Despacho de Cotización'),
]

ESTADO_MOVIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('PENDIENTE_RECEPCION', 'Pendiente de Recepción'),
    ('APROBADO', 'Aprobado'),
    ('RECHAZADO', 'Rechazado'),
    ('ANULADO', 'Anulado'),
    ('COMPLETADO', 'Completado'),
]

ESTADO_TICKET_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('PAGADO', 'Pagado'),
    ('ANULADO', 'Anulado'),
    ('DEVUELTO', 'Devuelto'),
]

# ========== MODELOS MEJORADOS ==========

class Ticket(models.Model):
    vendedor = models.ForeignKey(Vendedor, related_name='vendedor_ticket', on_delete=models.CASCADE)
    sucursal = models.ForeignKey(Sucursal, related_name='sucursal_ticket', on_delete=models.CASCADE)
    correlativo = models.IntegerField()
    estado = models.CharField(max_length=20, choices=ESTADO_TICKET_CHOICES, default='PENDIENTE')
    subTotal = models.IntegerField()
    descuento = models.IntegerField(null=True, blank=True)
    total = models.IntegerField()
    fecha = models.DateField(auto_now=True)
    hora = models.TimeField(auto_now=True)
    responsable = models.CharField(max_length=50)
    
    # === NUEVOS CAMPOS ===
    cliente_nombre = models.CharField(max_length=200, blank=True, null=True)
    cliente_rut = models.CharField(max_length=20, blank=True, null=True)
    cliente_email = models.EmailField(blank=True, null=True)
    cliente_telefono = models.CharField(max_length=20, blank=True, null=True)
    cliente_giro = models.CharField(max_length=255, blank=True, null=True)
    cliente_comuna = models.CharField(max_length=100, blank=True, null=True)
    cliente_ciudad = models.CharField(max_length=100, blank=True, null=True)
    cliente_direccion = models.CharField(max_length=255, blank=True, null=True)
    cliente_telefono_secundario = models.CharField(max_length=20, blank=True, null=True)
    cliente_email_facturacion = models.EmailField(blank=True, null=True)
    
    # === MÉTODOS DE PAGO ===
    metodo_pago = models.CharField(max_length=50, default='EFECTIVO', choices=METODO_PAGO_TICKET_CHOICES)
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    observaciones_adicionales = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # === CLASIFICACIÓN ===
    modulo_origen = models.CharField(max_length=30, default='VENTA_PUBLICO', choices=[
        ('VENTA_PUBLICO', 'Venta al Público'),
        ('VENTA_MAYORISTA', 'Venta Mayorista'),
        ('POS', 'Punto de Venta'),
        ('CAMBIO_DEVOLUCION', 'Cambio/Devolución'),
        ('ECOMMERCE', 'Venta Ecommerce'),
    ])
    
    # ✅ CAMPOS PARA FACTURACIÓN ELECTRÓNICA (Acepta)
    tipo_dte = models.CharField(max_length=30, blank=True, null=True, choices=[
        ('TICKET', 'Ticket (sin DTE)'),
        ('BOLETA', 'Boleta Manual'),
        ('BOLETA_ELECTRONICA', 'Boleta Electrónica - 39'),
        ('FACTURA_ELECTRONICA', 'Factura Electrónica - 33'),
        ('FACTURA_EXENTA', 'Factura Exenta - 34'),
        ('TICKET_COBRO_CAMBIO', 'Ticket Cobro Cambio'),
        ('TICKET_DEVOLUCION', 'Ticket Devolución'),
        ('TICKET_CAMBIO_DIRECTO', 'Ticket Cambio Directo'),
    ], default='TICKET', verbose_name='Tipo de Documento')
    
    folio_dte = models.IntegerField(blank=True, null=True, verbose_name='Folio DTE', help_text='Folio del documento electrónico generado')
    
    # Referencia a documento comercial (Orden Compra, Guía, etc.) - NO para anular
    referencia_tipo = models.CharField(max_length=10, blank=True, null=True, verbose_name='Tipo Referencia', help_text='801=OC, 52=Guía, 803=Contrato')
    referencia_folio = models.CharField(max_length=50, blank=True, null=True, verbose_name='Folio Referencia', help_text='Número de la OC, Guía, etc.')
    referencia_fecha = models.DateField(blank=True, null=True, verbose_name='Fecha Referencia')
    
    # Estado de facturación electrónica
    dte_generado = models.BooleanField(default=False, verbose_name='DTE Generado')
    dte_fecha_generacion = models.DateTimeField(blank=True, null=True, verbose_name='Fecha Generación DTE')
    dte_xml_path = models.CharField(max_length=500, blank=True, null=True, verbose_name='Ruta XML')
    dte_pdf_url = models.CharField(max_length=500, blank=True, null=True, verbose_name='URL PDF')
    
    # === CAMPOS PARA SINCRONIZACIÓN DESKTOP (POS FÍSICO) ===
    local_id = models.UUIDField(
        null=True, blank=True, 
        unique=True, 
        db_index=True,
        verbose_name='ID Local Desktop',
        help_text='UUID generado en app desktop para sincronización offline'
    )
    device_id = models.UUIDField(
        null=True, blank=True,
        verbose_name='ID Dispositivo',
        help_text='UUID del dispositivo que creó el ticket'
    )
    synced_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Sincronizado el',
        help_text='Fecha/hora de sincronización con servidor'
    )
    created_offline = models.BooleanField(
        default=False,
        verbose_name='Creado Offline',
        help_text='True si fue creado sin conexión en app desktop'
    )
    requiere_revision = models.BooleanField(
        default=False,
        verbose_name='Requiere Revisión',
        help_text='True si hubo conflictos de stock durante sync'
    )
    notas_sync = models.TextField(
        blank=True, null=True,
        verbose_name='Notas de Sincronización',
        help_text='Notas sobre problemas durante sincronización'
    )
    
    class Meta:
        ordering = ['-fecha', '-hora']
        unique_together = ['sucursal', 'correlativo']
    
    def __str__(self):
        return f"Ticket {self.correlativo} - {self.sucursal} - ${self.total:,}"

    @property
    def total_pagado(self):
        return self.pagos.aggregate(total=Sum('monto'))['total'] or 0

    @property
    def saldo_por_pagar(self):
        saldo = (self.total or 0) - self.total_pagado
        return saldo if saldo > 0 else 0

class Ticket_Productos(models.Model):
    ProductoTalla = models.ForeignKey(
        Producto_Talla,
        related_name='ticket_productos_talla',
        on_delete=models.CASCADE,
        null=True, blank=True,   # Nullable para ítems manuales/pendientes de cotización
    )
    idTicket = models.ForeignKey(Ticket, related_name='ticket_productos', on_delete=models.CASCADE)
    stock = models.IntegerField()
    precio = models.IntegerField()  # Cambiado de CharField a IntegerField
    descuento_unitario = models.IntegerField(default=0)
    subtotal = models.IntegerField()
    
    # === NUEVOS CAMPOS ===
    precio_original = models.IntegerField(default=0)  # Precio antes de descuentos
    porcentaje_descuento = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    
    # === ITEM MANUAL / DESPACHO DIFERIDO ===
    descripcion_linea = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="Descripción para ítems sin SKU (despacho diferido o líneas manuales)"
    )
    es_pendiente_despacho = models.BooleanField(
        default=False,
        help_text="True cuando el ítem viene de una cotización sin SKU asignado"
    )
    cotizacion_detalle_id = models.IntegerField(
        null=True, blank=True,
        help_text="ID de Cotizacion_Empresa_Detalle si aplica"
    )
    
    # === CAMPOS FIFO ===
    costo_fifo = models.IntegerField(default=0)  # Costo calculado con FIFO
    lotes_utilizados = models.TextField(blank=True, null=True)  # JSON de lotes utilizados
    
    class Meta:
        # unique_together removido: ProductoTalla puede ser null (varios ítems manuales por ticket)
        pass
    
    def __str__(self):
        if self.ProductoTalla:
            return f"Ticket Producto {self.ProductoTalla} - {self.stock} unidades"
        return f"Ticket Producto (manual) {self.descripcion_linea} - {self.stock} unidades"


# ========== MODELO PARA REFERENCIAS DE TICKETS (MÚLTIPLES REFERENCIAS) ==========

class TicketReferencia(models.Model):
    """Modelo para almacenar múltiples referencias de documentos comerciales en un ticket"""
    ticket = models.ForeignKey(Ticket, related_name='referencias', on_delete=models.CASCADE)
    tipo_documento = models.CharField(
        max_length=10,
        verbose_name='Tipo Documento',
        help_text='801=OC, 52=Guía, 803=Contrato, HES=Hoja Entrada Servicio'
    )
    folio = models.CharField(max_length=100, verbose_name='Folio/Número')
    fecha = models.DateField(verbose_name='Fecha Documento')
    observaciones = models.TextField(blank=True, null=True, verbose_name='Observaciones')
    creado_en = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['creado_en']
        verbose_name = 'Referencia de Ticket'
        verbose_name_plural = 'Referencias de Tickets'
    
    def __str__(self):
        return f"Ref {self.tipo_documento} - {self.folio} (Ticket {self.ticket.correlativo})"


class TicketDetallePago(models.Model):
    ticket = models.ForeignKey(Ticket, related_name='pagos', on_delete=models.CASCADE)
    metodo_pago = models.CharField(max_length=50, choices=METODO_PAGO_TICKET_CHOICES)
    tipo_tarjeta = models.CharField(max_length=100, null=True, blank=True)
    voucher = models.CharField(max_length=100, null=True, blank=True)
    numero_orden_compra = models.CharField(max_length=100, null=True, blank=True, help_text="Número de orden de compra del cliente")
    monto = models.IntegerField()
    notas = models.TextField(blank=True, null=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['creado_en']
        verbose_name = 'Detalle de Pago de Ticket'
        verbose_name_plural = 'Detalles de Pago de Tickets'

    def __str__(self):
        return f"Pago {self.get_metodo_pago_display()} - ${self.monto:,} (Ticket {self.ticket.correlativo})"


# ========== MÓDULO DE CAMBIOS Y DEVOLUCIONES ==========

TIPO_OPERACION_CAMBIO_CHOICES = [
    ('CAMBIO_SIMPLE', 'Cambio Simple'),
    ('CAMBIO_CON_DIFERENCIA', 'Cambio con Diferencia de Precio'),
    ('DEVOLUCION_TOTAL', 'Devolución Total'),
    ('DEVOLUCION_PARCIAL', 'Devolución Parcial'),
]

ESTADO_CAMBIO_CHOICES = [
    ('SOLICITADO', 'Solicitado'),
    ('EN_PROCESO', 'En Proceso'),
    ('APROBADO', 'Aprobado'),
    ('EJECUTADO', 'Ejecutado'),
    ('EJECUTADO_COBRO_PENDIENTE', 'Ejecutado - Cobro Pendiente'),
    ('COMPLETADO', 'Completado'),
    ('RECHAZADO', 'Rechazado'),
    ('CANCELADO', 'Cancelado'),
    ('REVERTIDO', 'Revertido'),
]

MOTIVO_CAMBIO_CHOICES = [
    ('TALLA_INCORRECTA', 'Talla Incorrecta'),
    ('COLOR_INCORRECTO', 'Color Incorrecto'),
    ('DEFECTO_PRODUCTO', 'Defecto en el Producto'),
    ('NO_SATISFACE', 'No Satisface Expectativas'),
    ('REGALO_NO_DESEADO', 'Regalo No Deseado'),
    ('CAMBIO_OPINION', 'Cambio de Opinión'),
    ('PRODUCTO_DAÑADO', 'Producto Dañado en Transporte'),
    ('OTRO', 'Otro Motivo'),
]

CONDICION_PRODUCTO_CHOICES = [
    ('PERFECTO', 'Perfecto Estado'),
    ('BUENO', 'Buen Estado'),
    ('REGULAR', 'Estado Regular'),
    ('DAÑADO', 'Dañado'),
    ('NO_APTO', 'No Apto para Cambio'),
]

class CambioDevolucion(models.Model):
    """
    Modelo principal para gestionar cambios y devoluciones
    """
    # === RELACIONES ===
    ticket_original = models.ForeignKey(
        Ticket, 
        on_delete=models.CASCADE, 
        related_name='cambios_devoluciones',
        help_text="Ticket original de la venta"
    )
    ticket_nuevo = models.ForeignKey(
        Ticket, 
        on_delete=models.SET_NULL, 
        related_name='cambios_generados',
        null=True, blank=True,
        help_text="Nuevo ticket generado por el cambio"
    )
    ticket_diferencia = models.ForeignKey(
        Ticket,
        on_delete=models.SET_NULL,
        related_name='cambios_diferencia',
        null=True, blank=True,
        help_text="Ticket de diferencia de precio (cuando cliente debe pagar más)"
    )
    sucursal = models.ForeignKey(
        Sucursal, 
        on_delete=models.CASCADE, 
        related_name='cambios_sucursal'
    )
    
    # === DATOS PRINCIPALES ===
    numero_operacion = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Número único de la operación de cambio"
    )
    tipo_operacion = models.CharField(
        max_length=30, 
        choices=TIPO_OPERACION_CAMBIO_CHOICES,
        help_text="Tipo de operación realizada"
    )
    estado = models.CharField(
        max_length=30, 
        choices=ESTADO_CAMBIO_CHOICES, 
        default='SOLICITADO'
    )
    
    # === FECHAS Y TIEMPOS ===
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    fecha_ejecucion = models.DateTimeField(null=True, blank=True, help_text="Fecha en que se ejecutó el cambio (movimientos de inventario)")
    fecha_pago_diferencia = models.DateTimeField(null=True, blank=True, help_text="Fecha en que se cobró la diferencia")
    fecha_completado = models.DateTimeField(null=True, blank=True)
    fecha_limite_cambio = models.DateField(
        help_text="Fecha límite para realizar el cambio"
    )
    
    # === MONTOS Y DIFERENCIAS ===
    monto_original = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Monto total del ticket original"
    )
    monto_nuevo = models.DecimalField(
        max_digits=12, decimal_places=2, 
        default=0,
        help_text="Monto del nuevo ticket (si aplica)"
    )
    diferencia_monto = models.DecimalField(
        max_digits=12, decimal_places=2, 
        default=0,
        help_text="Diferencia de precio (positivo: cliente paga, negativo: se devuelve)"
    )
    
    # === RESPONSABLES ===
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='cambios_solicitados',
        help_text="Usuario que registró la solicitud"
    )
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL,
        related_name='cambios_aprobados',
        null=True, blank=True,
        help_text="Usuario que aprobó el cambio"
    )
    
    # === OBSERVACIONES ===
    motivo_principal = models.CharField(
        max_length=30, 
        choices=MOTIVO_CAMBIO_CHOICES,
        help_text="Motivo principal del cambio"
    )
    observaciones_cliente = models.TextField(
        blank=True, null=True,
        help_text="Observaciones del cliente"
    )
    observaciones_vendedor = models.TextField(
        blank=True, null=True,
        help_text="Observaciones del vendedor"
    )
    observaciones_aprobacion = models.TextField(
        blank=True, null=True,
        help_text="Observaciones de la aprobación/rechazo"
    )
    
    # === POLÍTICAS Y VALIDACIONES ===
    requiere_autorizacion = models.BooleanField(
        default=False,
        help_text="Si requiere autorización especial"
    )
    autorizado_excepcion = models.BooleanField(
        default=False,
        help_text="Si fue autorizado como excepción a las políticas"
    )
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_solicitud']
        verbose_name = 'Cambio/Devolución'
        verbose_name_plural = 'Cambios y Devoluciones'
        indexes = [
            models.Index(fields=['numero_operacion']),
            models.Index(fields=['ticket_original', 'estado']),
            models.Index(fields=['sucursal', 'fecha_solicitud']),
            models.Index(fields=['estado', 'fecha_solicitud']),
            models.Index(fields=['tipo_operacion', 'fecha_solicitud']),
        ]
    
    def __str__(self):
        return f"Cambio {self.numero_operacion} - {self.get_tipo_operacion_display()} - {self.get_estado_display()}"
    
    def save(self, *args, **kwargs):
        # Generar número de operación si no existe
        if not self.numero_operacion:
            from django.utils import timezone
            fecha = timezone.now()
            ultimo_numero = CambioDevolucion.objects.filter(
                sucursal=self.sucursal,
                fecha_solicitud__year=fecha.year,
                fecha_solicitud__month=fecha.month
            ).count()
            self.numero_operacion = f"CD-{self.sucursal.id}-{fecha.strftime('%Y%m')}-{ultimo_numero + 1:04d}"
        
        # Calcular fecha límite si no existe (30 días desde la venta original)
        if not self.fecha_limite_cambio and self.ticket_original:
            from datetime import timedelta
            self.fecha_limite_cambio = self.ticket_original.fecha + timedelta(days=30)
        
        # Actualizar fechas según estado
        if self.estado == 'APROBADO' and not self.fecha_aprobacion:
            from django.utils import timezone
            self.fecha_aprobacion = timezone.now()
        elif self.estado == 'COMPLETADO' and not self.fecha_completado:
            from django.utils import timezone
            self.fecha_completado = timezone.now()
        
        super().save(*args, **kwargs)
    
    @property
    def dias_desde_venta(self):
        """Días transcurridos desde la venta original"""
        from django.utils import timezone
        if self.ticket_original:
            delta = timezone.now().date() - self.ticket_original.fecha
            return delta.days
        return 0
    
    @property
    def dentro_del_plazo(self):
        """Verifica si está dentro del plazo para cambios"""
        from django.utils import timezone
        return timezone.now().date() <= self.fecha_limite_cambio
    
    @property
    def puede_ejecutar(self):
        """Verifica si el cambio puede ser ejecutado"""
        return (
            self.estado == 'APROBADO' and 
            self.dentro_del_plazo and
            self.detalles.filter(condicion_producto__in=['PERFECTO', 'BUENO']).exists()
        )
    
    @property
    def puede_completar(self):
        """Verifica si el cambio puede ser completado (legacy - mantener compatibilidad)"""
        return self.puede_ejecutar
    
    @property
    def requiere_pago_adicional(self):
        """Verifica si requiere pago adicional"""
        return self.diferencia_monto > 0
    
    @property
    def genera_devolucion(self):
        """Verifica si genera devolución de dinero"""
        return self.diferencia_monto < 0
    
    @property
    def cobro_pendiente(self):
        """Verifica si hay un cobro de diferencia pendiente"""
        return (
            self.estado == 'EJECUTADO_COBRO_PENDIENTE' and
            self.ticket_diferencia is not None and
            self.ticket_diferencia.estado == 'PENDIENTE'
        )
    
    def aprobar_cambio(self, usuario_aprobador, observaciones=None):
        """Aprobar el cambio/devolución"""
        from django.utils import timezone
        
        self.estado = 'APROBADO'
        self.aprobado_por = usuario_aprobador
        self.fecha_aprobacion = timezone.now()
        
        if observaciones:
            self.observaciones_aprobacion = observaciones
        
        self.save()
    
    def rechazar_cambio(self, usuario_aprobador, motivo_rechazo):
        """Rechazar el cambio/devolución"""
        self.estado = 'RECHAZADO'
        self.aprobado_por = usuario_aprobador
        self.observaciones_aprobacion = motivo_rechazo
        self.save()
    
    def completar_cambio(self):
        """Marcar el cambio como completado"""
        if self.puede_completar:
            self.estado = 'COMPLETADO'
            self.save()
            return True
        return False


class CambioDevolucionDetalle(models.Model):
    """
    Detalle de productos involucrados en cambios y devoluciones
    """
    # === RELACIONES ===
    cambio_devolucion = models.ForeignKey(
        CambioDevolucion, 
        on_delete=models.CASCADE, 
        related_name='detalles'
    )
    
    # === PRODUCTO ORIGINAL (A DEVOLVER/CAMBIAR) ===
    producto_original = models.ForeignKey(
        Ticket_Productos, 
        on_delete=models.CASCADE,
        related_name='cambios_como_original',
        null=True, blank=True,
        help_text="Producto del ticket original que se cambia/devuelve (NULL para productos adicionales)"
    )
    cantidad_original = models.IntegerField(
        default=0,
        help_text="Cantidad del producto original a cambiar/devolver (0 para productos adicionales)"
    )
    
    # === PRODUCTO NUEVO (SI ES CAMBIO) ===
    producto_nuevo = models.ForeignKey(
        Producto_Talla, 
        on_delete=models.CASCADE,
        related_name='cambios_como_nuevo',
        null=True, blank=True,
        help_text="Nuevo producto en caso de cambio"
    )
    cantidad_nueva = models.IntegerField(
        default=0,
        help_text="Cantidad del nuevo producto"
    )
    precio_nuevo = models.DecimalField(
        max_digits=10, decimal_places=2, 
        default=0,
        help_text="Precio del nuevo producto"
    )
    
    # === CONDICIÓN Y EVALUACIÓN ===
    condicion_producto = models.CharField(
        max_length=20, 
        choices=CONDICION_PRODUCTO_CHOICES,
        help_text="Condición del producto devuelto"
    )
    apto_para_venta = models.BooleanField(
        default=True,
        help_text="Si el producto devuelto está apto para venta"
    )
    
    # === DIFERENCIAS DE PRECIO ===
    precio_original_unitario = models.DecimalField(
        max_digits=10, decimal_places=2,
        default=0,
        help_text="Precio unitario original (0 para productos adicionales)"
    )
    diferencia_unitaria = models.DecimalField(
        max_digits=10, decimal_places=2, 
        default=0,
        help_text="Diferencia de precio por unidad"
    )
    diferencia_total = models.DecimalField(
        max_digits=10, decimal_places=2, 
        default=0,
        help_text="Diferencia total para esta línea"
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(
        blank=True, null=True,
        help_text="Observaciones específicas de este producto"
    )
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Detalle de Cambio/Devolución'
        verbose_name_plural = 'Detalles de Cambios/Devoluciones'
        indexes = [
            models.Index(fields=['cambio_devolucion', 'producto_original']),
            models.Index(fields=['condicion_producto', 'apto_para_venta']),
        ]
    
    def __str__(self):
        return f"Detalle {self.cambio_devolucion.numero_operacion} - {self.producto_original.ProductoTalla.producto.articulo}"
    
    def save(self, *args, **kwargs):
        # Calcular diferencias automáticamente
        if self.producto_nuevo and self.cantidad_nueva > 0:
            # Es un cambio
            precio_nuevo_total = self.precio_nuevo * self.cantidad_nueva
            precio_original_total = self.precio_original_unitario * self.cantidad_original
            self.diferencia_total = precio_nuevo_total - precio_original_total
            self.diferencia_unitaria = self.precio_nuevo - self.precio_original_unitario
        else:
            # Es una devolución
            self.diferencia_total = -self.precio_original_unitario * self.cantidad_original
            self.diferencia_unitaria = -self.precio_original_unitario
        
        super().save(*args, **kwargs)
    
    @property
    def es_cambio(self):
        """Verifica si es un cambio (tiene producto nuevo)"""
        return self.producto_nuevo is not None
    
    @property
    def es_devolucion(self):
        """Verifica si es una devolución (no tiene producto nuevo)"""
        return self.producto_nuevo is None
    
    @property
    def valor_original_total(self):
        """Valor total del producto original"""
        return self.precio_original_unitario * self.cantidad_original
    
    @property
    def valor_nuevo_total(self):
        """Valor total del producto nuevo"""
        if self.es_cambio:
            return self.precio_nuevo * self.cantidad_nueva
        return 0


class PagoCambioDevolucion(models.Model):
    """
    Pagos asociados a cambios y devoluciones
    """
    # === RELACIONES ===
    cambio_devolucion = models.ForeignKey(
        CambioDevolucion, 
        on_delete=models.CASCADE, 
        related_name='pagos'
    )
    
    # === DATOS DEL PAGO ===
    tipo_pago = models.CharField(
        max_length=20, 
        choices=[
            ('PAGO_DIFERENCIA', 'Pago de Diferencia'),
            ('DEVOLUCION_EFECTIVO', 'Devolución en Efectivo'),
            ('DEVOLUCION_TARJETA', 'Devolución a Tarjeta'),
            ('CREDITO_TIENDA', 'Crédito en Tienda'),
        ]
    )
    metodo_pago = models.CharField(
        max_length=50, 
        choices=METODO_PAGO_TICKET_CHOICES
    )
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    
    # === DETALLES ESPECÍFICOS ===
    referencia_pago = models.CharField(
        max_length=100, 
        blank=True, null=True,
        help_text="Referencia del pago (voucher, autorización, etc.)"
    )
    numero_autorizacion = models.CharField(
        max_length=50, 
        blank=True, null=True
    )
    
    # === RESPONSABLES ===
    procesado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='pagos_cambio_procesados'
    )
    
    # === FECHAS ===
    fecha_pago = models.DateTimeField(auto_now_add=True)
    fecha_vencimiento_credito = models.DateField(
        null=True, blank=True,
        help_text="Fecha de vencimiento si es crédito en tienda"
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-fecha_pago']
        verbose_name = 'Pago de Cambio/Devolución'
        verbose_name_plural = 'Pagos de Cambios/Devoluciones'
    
    def __str__(self):
        return f"Pago {self.get_tipo_pago_display()} - ${self.monto:,} - {self.cambio_devolucion.numero_operacion}"


class HistorialCambioDevolucion(models.Model):
    """
    Historial de cambios de estado y acciones en cambios/devoluciones
    """
    # === RELACIONES ===
    cambio_devolucion = models.ForeignKey(
        CambioDevolucion, 
        on_delete=models.CASCADE, 
        related_name='historial'
    )
    
    # === DATOS DEL CAMBIO ===
    accion = models.CharField(
        max_length=50,
        choices=[
            ('CREADO', 'Creado'),
            ('APROBADO', 'Aprobado'),
            ('APROBADO_Y_EJECUTADO', 'Aprobado y Ejecutado'),
            ('RECHAZADO', 'Rechazado'),
            ('EJECUTADO', 'Ejecutado'),
            ('EJECUTADO_COBRO_PENDIENTE', 'Ejecutado - Cobro Pendiente'),
            ('COMPLETADO', 'Completado'),
            ('CANCELADO', 'Cancelado'),
            ('MODIFICADO', 'Modificado'),
            ('PAGO_PROCESADO', 'Pago Procesado'),
            ('PRODUCTO_EVALUADO', 'Producto Evaluado'),
            ('REVERTIDO', 'Revertido'),
            ('COBRO_DIFERENCIA', 'Cobro de Diferencia'),
        ]
    )
    estado_anterior = models.CharField(max_length=20, blank=True, null=True)
    estado_nuevo = models.CharField(max_length=20, blank=True, null=True)
    
    # === RESPONSABLES ===
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='acciones_cambio_realizadas'
    )
    
    # === DETALLES ===
    descripcion = models.TextField(help_text="Descripción de la acción realizada")
    datos_adicionales = models.JSONField(
        blank=True, null=True,
        help_text="Datos adicionales en formato JSON"
    )
    
    # === METADATA ===
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Historial de Cambio/Devolución'
        verbose_name_plural = 'Historiales de Cambios/Devoluciones'
    
    def __str__(self):
        return f"{self.accion} - {self.cambio_devolucion.numero_operacion} - {self.timestamp.strftime('%d/%m/%Y %H:%M')}"
