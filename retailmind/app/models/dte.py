from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal, Vendedor
from .catalogo import Producto_Talla

TIPO_DOCUMENTO_CHOICES = [
    ('FACTURA ELECTRONICA', 'Factura Electrónica'),
    ('BOLETA ELECTRONICA', 'Boleta Electrónica'),
    ('BOLETA PAPEL', 'Boleta Papel'),
    ('GUIA', 'Guía de Despacho'),
    ('NOTA DE PEDIDO', 'Nota de Pedido'),
    ('NOTA DE CREDITO', 'Nota de Crédito'),
    ('NOTA DE DEBITO', 'Nota de Débito'),
    ('FACTURA EXENTA', 'Factura Exenta'),
    ('COTIZACION', 'Cotización'),
    ('COMPRA', 'Compra'),
    ('TICKET', 'Ticket'),
    ('TICKET CAMBIO', 'Ticket de Cambio'),
    ('TRASPASO', 'Traspaso'),
    ('AJUSTE', 'Ajuste de Inventario'),
    ('AJUSTE TRASPASO', 'Ajuste de Traspaso (emisor)'),
]

ESTADO_PAGO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('PAGADO', 'Pagado'),
    ('VENCIDO', 'Vencido'),
]

ESTADO_DTE_CHOICES = [
    ('EMITIDO', 'Emitido'),
    ('ACEPTADO', 'Aceptado'),  # Mantener por compatibilidad
    ('RECEPCIONADO_COMPLETO', 'Recepcionado Completo'),
    ('RECEPCIONADO_PARCIAL', 'Recepcionado Parcial'),
    ('RECEPCIONADO_SOBRANTE', 'Recepcionado con Sobrantes'),
    ('EN_REGULARIZACION', 'En Regularización'),
    ('RECHAZADO', 'Rechazado'),
    ('ANULADO', 'Anulado'),
    ('CANCELADO', 'Cancelado'),
]

ESTADO_RECEPCION_PRODUCTO_CHOICES = [
    ('PENDIENTE', 'Pendiente de Recepción'),
    ('RECEPCIONADO_OK', 'Recepcionado OK'),
    ('RECEPCIONADO_PARCIAL', 'Recepcionado Parcial'),
    ('RECEPCIONADO_DANADO', 'Recepcionado con Daños'),
    ('FALTANTE', 'Faltante'),
    ('EN_REGULARIZACION', 'En Regularización'),
    ('EN_SOLICITUD_REGULARIZACION', 'En Solicitud de Regularización'),  # NUEVO
    ('REGULARIZADO', 'Regularizado'),
    ('RECEPCIONADO_SOBRANTE', 'Recepcionado con Sobrantes'),
    ('SOBRANTE_PENDIENTE', 'Sobrante Pendiente de Decisión'),
]

# Nuevos choices para Solicitudes de Regularización
TIPO_PROBLEMA_CHOICES = [
    ('FALTANTE', 'Faltante'),
    ('DANADO', 'Dañado'),
    ('PARCIAL', 'Recepción Parcial'),
    ('INCORRECTO', 'Producto Incorrecto'),
    ('SOBRANTE', 'Sobrante'),
]

TIPO_SOLUCION_CHOICES = [
    ('NOTA_CREDITO', 'Nota de Crédito'),
    ('REENVIO', 'Reenvío del mismo producto'),
    ('CAMBIO_PRODUCTO', 'Cambio por otro producto'),
    ('AJUSTE_CANTIDAD', 'Ajustar solo cantidad'),
    ('DEVOLUCION_SOBRANTE', 'Devolución de Sobrante a Origen'),
    ('ACEPTAR_SOBRANTE', 'Aceptar Sobrante en Inventario'),
]

ESTADO_SOLICITUD_CHOICES = [
    ('PENDIENTE', 'Pendiente de Revisión'),
    ('EN_REVISION', 'En Revisión por Emisor'),
    ('APROBADA', 'Aprobada'),
    ('RECHAZADA', 'Rechazada'),
    ('EJECUTADA', 'Solución Ejecutada'),
    ('COMPLETADA', 'Completada y Confirmada'),
    ('CANCELADA', 'Cancelada'),
]

class Dte(models.Model):
    emisor = models.ForeignKey(Empresa, related_name='empresa_origen', on_delete=models.CASCADE)
    receptor = models.ForeignKey(Empresa, related_name='empresa_destino', on_delete=models.CASCADE,null=True,blank=True)
    numero_documento =  models.IntegerField()
    tipo_documento = models.CharField(max_length=20, choices=TIPO_DOCUMENTO_CHOICES)
    monto_con_iva = models.DecimalField(max_digits=12, decimal_places=2)
    monto_neto = models.DecimalField(max_digits=12, decimal_places=2)
    estado_pago = models.CharField(max_length=20, choices=ESTADO_PAGO_CHOICES)
    estado_dte = models.CharField(max_length=30, choices=ESTADO_DTE_CHOICES)  # Aumentado para nuevos estados
    responsable = models.CharField(max_length=100)   
    fecha_emision = models.DateField()
    fecha_vencimiento = models.DateField()
    diasCredito = models.IntegerField()
    bultos = models.IntegerField()
    unidades_productos = models.IntegerField()
    vendedor = models.ForeignKey(Vendedor, related_name='vendedor_dte', on_delete=models.SET_NULL, null=True, blank=True)
    descuento = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_recepcion = models.DateField(null=True, blank=True)
    hora = models.TimeField(null=True, blank=True)
    tipo_transaccion = models.CharField(max_length=15, choices=[
        ('COMPRA', 'Compra'),
        ('VENTA', 'Venta'),
        ('VENTA_PUBLICO', 'Venta al Público'),
        ('TRASPASO', 'Traspaso')
    ])
    referencias = models.TextField(blank=True, null=True)
    
    # Campos para Notas de Crédito
    es_nota_credito = models.BooleanField(default=False, help_text="Indica si es una Nota de Crédito")
    documento_afectado = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='notas_credito_relacionadas',
        help_text="DTE original que se está corrigiendo (solo para NC)"
    )
    motivo_nc = models.TextField(
        blank=True, 
        null=True,
        help_text="Motivo de la Nota de Crédito"
    )
    documento_padre = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documentos_hijos',
        help_text="Documento previo (cotización/guía) al que regulariza esta factura"
    )
    
    # Campo para motivo de rechazo
    motivo_rechazo = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Motivo del rechazo del DTE (máximo 100 caracteres)"
    )

    # === LIMBO / NC CON DEVOLUCIÓN FÍSICA PENDIENTE ===
    # Aplican al DTE hijo (NC / AJUSTE TRASPASO POST) cuando el emisor optó
    # por "devolver mercadería al origen": la NC documental queda emitida,
    # pero los Movimientos_Producto se crean en estado PENDIENTE hasta que
    # el receptor confirme el despacho físico.
    requiere_devolucion_fisica = models.BooleanField(
        default=False,
        help_text="DTE hijo cuya NC requiere despacho físico desde destino para mover el stock al origen"
    )
    fecha_confirmacion_devolucion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que el receptor confirmó el despacho físico de la devolución"
    )

    # === CAMPOS PARA SOFT DELETE / DESCARTE ===
    descartado = models.BooleanField(
        default=False,
        help_text="DTE marcado como descartado (no se muestra en listados pero permanece en DB)"
    )
    fecha_descarte = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que se descartó el DTE"
    )
    descartado_por = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Usuario que descartó el DTE"
    )
    motivo_descarte = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text="Motivo por el cual se descartó"
    )

    # === DTE MANUAL (CUADRATURA) ===
    # True cuando se creó manualmente desde Gestión de Documentos para
    # registrar una venta que ocurrió fuera del sistema (boleta papel
    # emitida a mano, factura externa, etc.). Estos DTEs no tienen
    # productos asociados ni movimientos de stock; solo cabecera + un
    # `Dte_Detalle_Pago` para que figuren en cuadratura y reportes.
    es_manual = models.BooleanField(
        default=False,
        help_text="DTE creado manualmente desde Gestión de Documentos (sin productos, solo cabecera + pago)"
    )

    # === COMPRA POR CONCEPTO ===
    # True cuando la factura de COMPRA se registra "por concepto": solo la
    # cabecera Dte (cuenta por pagar), SIN líneas de producto (Dte_Productos)
    # y SIN ingreso de stock. Es stock-neutral. Permite distinguir en reportes
    # y dashboard las compras no inventariables de las itemizadas/recepcionadas,
    # sin inferirlo en runtime por Count(dte_productos)==0.
    es_por_concepto = models.BooleanField(
        default=False,
        help_text="Compra registrada por concepto: solo cabecera financiera, sin productos ni stock"
    )

    # === COMPROBANTE DE PAGO ENVIADO POR CORREO ===
    # Se setean cuando se envía el "Comprobante de Pago" al proveedor por correo
    # desde Gestión de DTEs de Compras (solo facturas pagadas).
    comprobante_enviado_en = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha/hora del último envío del comprobante de pago al proveedor por correo"
    )
    comprobante_enviado_a = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Correo al que se envió el último comprobante de pago"
    )

    # === CAMPOS PARA MODO DE PRECIO EN DESPACHO EXTERNO ===
    TIPO_PRECIO_EXTERNO_CHOICES = [
        ('COSTO', 'Solo Costo'),
        ('SOBREPRECIO', 'Costo + Sobreprecio'),
        ('CUSTOM_PCT', 'Porcentaje Custom'),
        ('PRECIO_VENTA', 'Precio de Venta'),
    ]
    tipo_precio_externo = models.CharField(
        max_length=20,
        choices=TIPO_PRECIO_EXTERNO_CHOICES,
        null=True,
        blank=True,
        help_text="Modo de precio usado en despacho externo"
    )
    porcentaje_custom = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Porcentaje de margen custom sobre el costo (solo si tipo_precio_externo=CUSTOM_PCT)"
    )

    # === REFERENCIA COMERCIAL OPCIONAL (Orden de Compra / Nota de Pedido) ===
    # Referencia estructurada SII que viaja en el TXT (TpoDocRef/FolioRef/FchRef).
    # Espejo del patrón de Ticket.referencia_tipo/folio/fecha.
    referencia_tipo = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        help_text="Código SII del documento referenciado: 801=Orden de Compra, 802=Nota de Pedido"
    )
    referencia_folio = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Folio/Número del documento referenciado (ej. N° de orden de compra)"
    )
    referencia_fecha = models.DateField(
        null=True,
        blank=True,
        help_text="Fecha del documento referenciado"
    )

    # Transiciones de estado permitidas: origen -> {destinos válidos}
    TRANSICIONES_VALIDAS = {
        'EMITIDO': {'RECEPCIONADO_COMPLETO', 'RECEPCIONADO_PARCIAL', 'RECEPCIONADO_SOBRANTE', 'RECHAZADO', 'ANULADO', 'CANCELADO'},
        'ACEPTADO': {'RECEPCIONADO_COMPLETO', 'RECEPCIONADO_PARCIAL', 'RECEPCIONADO_SOBRANTE', 'RECHAZADO', 'ANULADO', 'CANCELADO'},
        'RECEPCIONADO_PARCIAL': {'EN_REGULARIZACION', 'RECEPCIONADO_COMPLETO'},
        'RECEPCIONADO_SOBRANTE': {'EN_REGULARIZACION', 'RECEPCIONADO_COMPLETO'},
        'EN_REGULARIZACION': {'RECEPCIONADO_COMPLETO'},
        'RECHAZADO': {'EMITIDO'},
        'RECEPCIONADO_COMPLETO': set(),
        'ANULADO': set(),
        'CANCELADO': set(),
    }

    def clean(self):
        super().clean()
        if not self.pk:
            return
        from django.core.exceptions import ValidationError
        try:
            old = Dte.objects.only('estado_dte').get(pk=self.pk)
        except Dte.DoesNotExist:
            return
        if old.estado_dte == self.estado_dte:
            return
        destinos = self.TRANSICIONES_VALIDAS.get(old.estado_dte, set())
        if self.estado_dte not in destinos:
            raise ValidationError({
                'estado_dte': (
                    f'Transición inválida: {old.estado_dte} → {self.estado_dte}. '
                    f'Transiciones permitidas desde {old.estado_dte}: {destinos or "ninguna"}.'
                )
            })

    def __str__(self):
        return f"DTE {self.numero_documento} - {self.tipo_documento}"
    
    def es_misma_empresa_check(self):
        """Verifica si emisor y receptor son la misma empresa"""
        return self.emisor_id == self.receptor_id if self.receptor else False
    
    def calcular_totales(self):
        """Recalcula MntNeto/IVA/MntTotal desde detalle y descuentos/recargos globales."""
        from decimal import Decimal

        suma_items = Decimal(0)
        for p in self.dte_productos.all():
            if p.monto_item:
                suma_items += Decimal(p.monto_item)
            else:
                monto_linea = Decimal(p.precio * p.stock) - Decimal(p.descuento_monto or 0)
                suma_items += monto_linea

        for dr in self.descuentos_recargos.all():
            if dr.ind_exe_dr == 2:
                continue
            valor = dr.valor_dr
            if dr.tpo_valor == '%':
                valor = suma_items * dr.valor_dr / Decimal('100')
            if dr.tpo_mov == 'D':
                suma_items -= valor
            else:
                suma_items += valor

        es_boleta = self.tipo_documento in ('BOLETA ELECTRONICA', 'BOLETA EXENTA')
        if es_boleta:
            self.monto_con_iva = int(suma_items)
            self.monto_neto = int((suma_items / Decimal('1.19')).quantize(Decimal('1')))
        else:
            self.monto_neto = int(suma_items)
            iva = int((suma_items * Decimal('0.19')).quantize(Decimal('1')))
            self.monto_con_iva = self.monto_neto + iva

    def requiere_nota_credito_check(self):
        """
        Determina si requiere NC para regularización.
        Solo requiere NC si:
        1. Son empresas diferentes (traspaso entre empresas)
        2. El documento tiene valor monetario (FACTURA o BOLETA)
        
        GUÍAS DE DESPACHO no requieren NC porque no tienen valor monetario.
        """
        # Si es misma empresa, no requiere NC (es ajuste interno)
        if self.es_misma_empresa_check():
            return False
        
        # Si no es traspaso, no aplica
        if self.tipo_transaccion != 'TRASPASO':
            return False
        
        # Solo documentos con valor monetario requieren NC
        documentos_con_valor = ['FACTURA ELECTRONICA', 'FACTURA', 'BOLETA ELECTRONICA', 'BOLETA']
        return self.tipo_documento in documentos_con_valor

    class Meta:
        ordering = ['-fecha_emision', '-id']
        indexes = [
            # Listado de documentos de ventas (listar_documentos_ventas)
            models.Index(fields=['sucursal', '-fecha_emision'], name='dte_suc_fecha_idx'),
            # Listado filtrado por tipo de transacción (VENTA / VENTA_PUBLICO)
            models.Index(
                fields=['sucursal', 'tipo_transaccion', '-fecha_emision'],
                name='dte_suc_trans_fecha_idx',
            ),
            # Filtro por estado del DTE
            models.Index(
                fields=['sucursal', 'estado_dte', '-fecha_emision'],
                name='dte_suc_estado_fecha_idx',
            ),
            # Búsqueda / orden por tipo de documento
            models.Index(
                fields=['sucursal', 'tipo_documento', '-fecha_emision'],
                name='dte_suc_tipo_fecha_idx',
            ),
            # Conteos por receptor (búsqueda por cliente)
            models.Index(fields=['receptor'], name='dte_receptor_idx'),
        ]


class Dte_Detalle_Pago(models.Model):
    dte = models.ForeignKey(Dte, related_name='dte_asociado', on_delete=models.PROTECT)
    metodo_pago = models.CharField(max_length=100 )
    tipo_tarjeta =   models.CharField(max_length=100,null=True)
    voucher =  models.CharField(max_length=50,null=True)
    monto = models.IntegerField()
    notas = models.TextField(blank=True, null=True)
    fecha_pago = models.DateField(
        null=True, blank=True,
        help_text="Fecha en que se realizó el pago. Aplica a todos los métodos. Permite fechas pasadas (retroactivos) y futuras (cheques a fecha)."
    )
    # FK al DTE emitido (factura de venta a este proveedor) usado como instrumento de
    # compensación. Apunta a Dte, NO a Dte_Detalle_Pago. Null para compensaciones
    # mismo-proveedor o registros manuales (DTE emitido fuera del sistema).
    documento_compensacion = models.ForeignKey(
        Dte, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='compensaciones_emitidas',
        help_text="DTE emitido (factura de venta a este proveedor) usado como instrumento de "
                  "compensación de la factura de compra. Null si es compensación mismo-proveedor "
                  "o registro manual."
    )

    def __str__(self):
        return f"Dte_Detalle_Pago {self.metodo_pago} - {self.monto}"

class Dte_Incidencia(models.Model):
    """Incidencias asociadas a un DTE (problemas de mercadería, facturación, etc.)"""
    
    TIPO_INCIDENCIA_CHOICES = [
        ('FACTURACION', 'Error de Facturación'),
        ('MERCADERIA', 'Mercadería Faltante/Incompleta'),
        ('CALIDAD', 'Productos Dañados/Defectuosos'),
        ('DESCUENTOS', 'Descuentos Incorrectos'),
        ('DESCRIPCION', 'Error en Descripción'),
        ('OTRO', 'Otro')
    ]
    
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('EN_GESTION', 'En Gestión'),
        ('RESUELTO', 'Resuelto')
    ]
    
    dte = models.ForeignKey(Dte, related_name='incidencias', on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPO_INCIDENCIA_CHOICES)
    descripcion = models.TextField(help_text="Detalle del problema")
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default='PENDIENTE')
    fecha_registro = models.DateTimeField(auto_now_add=True)
    fecha_resolucion = models.DateTimeField(null=True, blank=True)
    notas_resolucion = models.TextField(blank=True, null=True, help_text="Cómo se resolvió la incidencia")
    
    class Meta:
        ordering = ['-fecha_registro']
        verbose_name = 'Incidencia DTE'
        verbose_name_plural = 'Incidencias DTE'
    
    def __str__(self):
        return f"Incidencia {self.get_tipo_display()} - DTE {self.dte.numero_documento} - {self.estado}"

class Dte_Productos(models.Model):
    dte =   models.ForeignKey(Dte, related_name='dte_productos', on_delete=models.CASCADE)
    productoTalla = models.ForeignKey(
        Producto_Talla,
        related_name='producto_talla_dte',
        on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    descripcion = models.CharField(max_length=255)
    costo = models.IntegerField(default=0)
    sobreprecio = models.IntegerField(default=0)
    precio = models.IntegerField()
    precio_unitario = models.IntegerField(default=0, help_text="Precio unitario neto (factura) o IVA-inclusive (boleta)")
    descuento_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, help_text="Porcentaje de descuento por item (solo factura)")
    descuento_monto = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True, help_text="Monto descuento total linea")
    monto_item = models.IntegerField(default=0, help_text="Monto final de la linea (PrcItem*QtyItem - DescuentoMonto)")
    stock = models.IntegerField()
    activo = models.BooleanField(default=True)
    es_pendiente_despacho = models.BooleanField(
        default=False,
        help_text="True cuando corresponde a un ítem de cotización sin SKU asignado"
    )

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.descuento_pct and not self.descuento_monto:
            raise ValidationError({'descuento_monto': 'Si descuento_pct tiene valor, descuento_monto es obligatorio.'})

    def __str__(self):
        if self.productoTalla:
            return f"Dte_Producto {self.dte} - {self.productoTalla}"
        return f"Dte_Producto {self.dte} - (manual) {self.descripcion[:40]}"

class NotificacionDTE(models.Model):
    """
    Modelo para notificaciones de DTEs recibidos pendientes de procesar.
    Se crea cuando una empresa emite un DTE hacia otra empresa,
    permitiendo al receptor saber que tiene documentos pendientes.
    """
    
    # === RELACIONES ===
    dte = models.ForeignKey(
        Dte,
        on_delete=models.CASCADE,
        related_name='notificaciones'
    )
    empresa_receptora = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name='notificaciones_dte_recibidas',
        help_text="Empresa que recibe la notificación (receptor del DTE)"
    )
    
    # === TIPO DE NOTIFICACIÓN ===
    TIPO_NOTIFICACION_CHOICES = [
        ('DTE_RECIBIDO', 'Nuevo DTE Recibido'),
        ('ACTUALIZACION_PENDIENTE', 'Actualización de Stock Pendiente'),
        ('RECEPCION_COMPLETADA', 'Recepción Completada'),
        ('REGULARIZACION_REQUERIDA', 'Regularización Requerida'),
        ('RECHAZO_DTE', 'DTE Rechazado por Destino'),
        ('VENCIMIENTO_PROXIMO', 'Vencimiento Próximo'),
        ('CORRECCION_RECEPCION', 'Corrección de Recepción por Emisor'),
    ]
    
    tipo = models.CharField(
        max_length=35,
        choices=TIPO_NOTIFICACION_CHOICES,
        default='DTE_RECIBIDO'
    )
    
    # === DATOS DE LA NOTIFICACIÓN ===
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    
    # === ESTADO ===
    leida = models.BooleanField(default=False)
    procesada = models.BooleanField(
        default=False,
        help_text="Indica si el DTE ya fue procesado/recepcionado"
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_lectura = models.DateTimeField(null=True, blank=True)
    fecha_procesamiento = models.DateTimeField(null=True, blank=True)
    
    # === METADATA ===
    usuario_que_proceso = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dtes_procesados'
    )
    
    # === SUCURSAL ESPECÍFICA (para filtrar por sucursal) ===
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notificaciones_dte',
        help_text="Sucursal específica que debe ver esta notificación"
    )
    
    # === DATOS ADICIONALES PARA REGULARIZACIÓN ===
    productos_problemas = models.IntegerField(
        default=0,
        help_text="Número de productos con problemas"
    )
    detalle_problemas = models.TextField(
        null=True,
        blank=True,
        help_text="Detalle de los problemas reportados"
    )
    sucursal_reportante = models.ForeignKey(
        Sucursal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notificaciones_enviadas',
        help_text="Sucursal que reporta el problema"
    )
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Notificación de DTE'
        verbose_name_plural = 'Notificaciones de DTEs'
        indexes = [
            models.Index(fields=['empresa_receptora', 'leida']),
            models.Index(fields=['empresa_receptora', 'procesada']),
            models.Index(fields=['sucursal', 'leida']),
            models.Index(fields=['-fecha_creacion']),
        ]
    
    def __str__(self):
        return f"DTE #{self.dte.numero_documento} - {self.empresa_receptora.nombre}"
    
    def marcar_leida(self):
        """Marca la notificación como leída"""
        if not self.leida:
            self.leida = True
            self.fecha_lectura = timezone.now()
            self.save()
    
    def marcar_procesada(self, usuario=None):
        """Marca la notificación como procesada"""
        if not self.procesada:
            self.procesada = True
            self.fecha_procesamiento = timezone.now()
            self.usuario_que_proceso = usuario
            self.save()
    
    @classmethod
    def crear_notificacion_dte_emitido(cls, dte):
        """
        Crea una notificación cuando se emite un DTE a otra empresa.
        """
        if not dte.receptor:
            return None
        
        # Solo crear si emisor != receptor
        if dte.emisor_id == dte.receptor_id:
            return None
        
        # Verificar si ya existe una notificación para este DTE
        if cls.objects.filter(dte=dte, empresa_receptora=dte.receptor).exists():
            return None
        
        return cls.objects.create(
            dte=dte,
            empresa_receptora=dte.receptor,
            tipo='DTE_RECIBIDO',
            titulo=f"Nuevo {dte.tipo_documento} #{dte.numero_documento}",
            mensaje=f"Has recibido un {dte.tipo_documento} #{dte.numero_documento} de {dte.emisor.nombre} por ${dte.monto_con_iva:,.0f}. Pendiente de actualizar stock."
        )
    
    @classmethod
    def crear_notificacion_regularizacion(cls, dte, productos_problemas, detalle_problemas, sucursal_reportante, usuario_reportante=None):
        """
        Crea una notificación a la bodega EMISORA cuando hay problemas en la recepción.
        Se usa para alertar que deben regularizar la mercadería enviada.
        
        Args:
            dte: El DTE que presenta problemas
            productos_problemas: Lista de productos con problemas (dictionaries)
            detalle_problemas: String con el detalle de los problemas
            sucursal_reportante: Sucursal que reporta el problema (la que recibió)
            usuario_reportante: Usuario que reportó el problema
        """
        if not dte.sucursal:
            return None
        
        # La notificación va a la sucursal EMISORA del DTE (quien envió la mercadería)
        sucursal_emisora = dte.sucursal
        empresa_emisora = dte.emisor
        
        # Contar productos con problemas
        total_problemas = len(productos_problemas) if isinstance(productos_problemas, list) else productos_problemas
        
        # Crear resumen de problemas para el mensaje
        resumen = []
        if isinstance(productos_problemas, list):
            for p in productos_problemas[:5]:  # Máximo 5 en el resumen
                estado = p.get('estado', 'PROBLEMA')
                sku = p.get('sku', '')
                descripcion = p.get('descripcion', '')[:30]
                resumen.append(f"• {sku}: {descripcion} ({estado})")
            if len(productos_problemas) > 5:
                resumen.append(f"• ... y {len(productos_problemas) - 5} más")
        
        detalle_str = "\n".join(resumen) if resumen else detalle_problemas
        
        titulo = f"⚠️ Regularización Requerida - DTE #{dte.numero_documento}"
        mensaje = (
            f"La sucursal {sucursal_reportante.alias if sucursal_reportante else 'Destino'} "
            f"ha reportado {total_problemas} producto(s) con problemas en el DTE #{dte.numero_documento}.\n\n"
            f"Problemas reportados:\n{detalle_str}\n\n"
            f"Por favor, revise y regularice la situación."
        )
        
        return cls.objects.create(
            dte=dte,
            empresa_receptora=empresa_emisora,  # La empresa emisora recibe la notificación
            sucursal=sucursal_emisora,  # La sucursal emisora específica
            sucursal_reportante=sucursal_reportante,
            tipo='REGULARIZACION_REQUERIDA',
            titulo=titulo,
            mensaje=mensaje,
            productos_problemas=total_problemas,
            detalle_problemas=detalle_problemas,
            usuario_que_proceso=usuario_reportante
        )

class DteAlertaDescartada(models.Model):
    """Registro de alertas de DTE descartadas por usuario."""

    dte = models.ForeignKey(
        Dte,
        on_delete=models.CASCADE,
        related_name='alertas_descartadas'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='alertas_dte_descartadas'
    )
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='alertas_dte_descartadas'
    )
    fecha_descartada = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Alerta DTE Descartada'
        verbose_name_plural = 'Alertas DTE Descartadas'
        unique_together = ('dte', 'usuario')
        indexes = [
            models.Index(fields=['dte', 'usuario'], name='dte_alerta_dte_user_idx'),
            models.Index(fields=['usuario'], name='dte_alerta_usuario_idx'),
            models.Index(fields=['-fecha_descartada'], name='dte_alerta_fecha_idx'),
        ]

    def __str__(self):
        return f"Alerta DTE #{self.dte.numero_documento} descartada por {self.usuario}"


class DescuentoRecargo(models.Model):
    """Descuentos/recargos globales de un DTE (Tabla 3 Factura / Tabla 4 Boleta)."""

    TPO_MOV_CHOICES = [('D', 'Descuento'), ('R', 'Recargo')]
    TPO_VALOR_CHOICES = [('%', 'Porcentaje'), ('$', 'Monto')]
    IND_EXE_CHOICES = [(1, 'Exento/No afecto'), (2, 'No facturable')]

    dte = models.ForeignKey(Dte, related_name='descuentos_recargos', on_delete=models.CASCADE)
    nro_linea = models.PositiveIntegerField()
    tpo_mov = models.CharField(max_length=1, choices=TPO_MOV_CHOICES)
    glosa_dr = models.CharField(max_length=45)
    tpo_valor = models.CharField(max_length=1, choices=TPO_VALOR_CHOICES)
    valor_dr = models.DecimalField(max_digits=18, decimal_places=2)
    ind_exe_dr = models.IntegerField(null=True, blank=True, choices=IND_EXE_CHOICES)
    valor_dr_otra_mnda = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True, help_text="Solo facturas: valor en otra moneda")

    class Meta:
        ordering = ['nro_linea']
        unique_together = ('dte', 'nro_linea')
        verbose_name = 'Descuento/Recargo DTE'
        verbose_name_plural = 'Descuentos/Recargos DTE'

    def __str__(self):
        return f"{'Dcto' if self.tpo_mov == 'D' else 'Recargo'} {self.glosa_dr} {self.tpo_valor}{self.valor_dr}"


class HistorialCambioFolioDte(models.Model):
    """Bitácora de cambios de folio (numero_documento) sobre un DTE.

    Cada vez que un usuario con permiso edita el folio de un DTE se crea
    un registro con el folio anterior, el nuevo, el motivo y la lista de
    documentos cuyas referencias fueron actualizadas automáticamente
    (NCs, ajustes de traspaso, referencias textuales en otros DTEs).
    """

    dte = models.ForeignKey(
        Dte,
        on_delete=models.CASCADE,
        related_name='historial_cambios_folio',
        help_text='DTE al que se le cambió el folio',
    )
    folio_anterior = models.IntegerField(help_text='Número de documento previo al cambio')
    folio_nuevo = models.IntegerField(help_text='Número de documento después del cambio')
    motivo = models.TextField(help_text='Motivo declarado por el usuario')
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cambios_folio_dte',
    )
    usuario_nombre = models.CharField(
        max_length=150,
        blank=True,
        default='',
        help_text='Snapshot del usuario al momento del cambio (por si el user se borra)',
    )
    fecha_cambio = models.DateTimeField(auto_now_add=True)
    referencias_actualizadas = models.JSONField(
        default=list,
        blank=True,
        help_text='Lista de documentos relacionados cuyas referencias se actualizaron',
    )
    ip_cliente = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-fecha_cambio', '-id']
        verbose_name = 'Historial Cambio de Folio DTE'
        verbose_name_plural = 'Historial Cambios de Folio DTE'
        indexes = [
            models.Index(fields=['dte', '-fecha_cambio'], name='hist_folio_dte_fecha_idx'),
            models.Index(fields=['folio_anterior'], name='hist_folio_anterior_idx'),
            models.Index(fields=['folio_nuevo'], name='hist_folio_nuevo_idx'),
        ]

    def __str__(self):
        return f"DTE {self.dte_id}: folio {self.folio_anterior} → {self.folio_nuevo}"
