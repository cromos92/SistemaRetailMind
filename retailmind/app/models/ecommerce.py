"""
Modelos Ecommerce
=================
Gestión de pedidos online recibidos desde VicentAllEcommercesConected
(Shopify, Paris, Ripley) para su facturación en SistemaRetailMind.
"""
from django.db import models
from django.conf import settings
from django.utils import timezone


CANAL_ECOMMERCE_CHOICES = [
    ('SHOPIFY', 'Shopify'),
    ('PARIS', 'Paris'),
    ('RIPLEY', 'Ripley'),
    ('WALMART', 'Walmart'),
    ('OTRO', 'Otro'),
]

ESTADO_PEDIDO_ECOMMERCE_CHOICES = [
    ('PENDIENTE', 'Pendiente de Facturar'),
    ('FACTURADO', 'Facturado'),
    ('CANCELADO', 'Cancelado'),
    ('ERROR', 'Error'),
]


class PedidoEcommerce(models.Model):
    """
    Registro de pedido online proveniente de VicentAllEcommercesConected.
    Permite al operador llegar a SistemaRetailMind y facturar directamente
    usando el numero_ticket_rm generado.
    """

    # Identificación
    numero_ticket_rm = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        verbose_name='N° Ticket RM',
        help_text='Número mostrado en la impresión del pedido externo',
    )
    numero_pedido_canal = models.CharField(
        max_length=255,
        db_index=True,
        verbose_name='N° Pedido Canal',
        help_text='ID/número del pedido en el marketplace de origen',
    )
    canal_origen = models.CharField(
        max_length=20,
        choices=CANAL_ECOMMERCE_CHOICES,
        db_index=True,
        verbose_name='Canal origen',
    )

    # Sucursal destino (para facturar)
    sucursal = models.ForeignKey(
        'app.Sucursal',
        on_delete=models.PROTECT,
        related_name='pedidos_ecommerce',
        verbose_name='Sucursal',
    )

    # RUT empresa del canal origen (sin puntos, con guión: ej. 76123456-7)
    # Permite filtrar qué pedidos puede ver cada empresa en el ERP
    rut_empresa = models.CharField(
        max_length=20,
        blank=True,
        default='',
        db_index=True,
        verbose_name='RUT empresa canal',
        help_text='RUT de la empresa del canal de origen (sin puntos, con guión)',
    )

    # Datos del cliente (snapshot)
    cliente_nombre = models.CharField(max_length=255, verbose_name='Cliente')
    cliente_email = models.EmailField(blank=True, verbose_name='Email')
    cliente_documento = models.CharField(
        max_length=20, blank=True, verbose_name='RUT/Doc.',
        help_text='RUT o documento para emisión de DTE',
    )

    # Montos
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    descuento = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    costo_envio = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Detalle de ítems (JSON snapshot de los productos del pedido)
    items = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Ítems',
        help_text='Lista de productos: [{sku, nombre, cantidad, precio_unitario, ...}]',
    )

    # Dirección de envío (referencia)
    direccion_envio = models.TextField(blank=True, verbose_name='Dirección envío')

    # Estado de facturación
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_PEDIDO_ECOMMERCE_CHOICES,
        default='PENDIENTE',
        db_index=True,
        verbose_name='Estado',
    )

    # Vínculo con documentos generados (se completa al facturar)
    ticket = models.ForeignKey(
        'app.Ticket',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedidos_ecommerce',
        verbose_name='Ticket generado',
    )
    dte = models.ForeignKey(
        'app.Dte',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedidos_ecommerce',
        verbose_name='DTE emitido',
    )

    # Notas internas
    notas = models.TextField(blank=True, verbose_name='Notas')
    error_detalle = models.TextField(blank=True, verbose_name='Detalle error')

    # Auditoría
    fecha_recepcion = models.DateTimeField(default=timezone.now, db_index=True, verbose_name='Fecha recepción')
    fecha_facturacion = models.DateTimeField(null=True, blank=True, verbose_name='Fecha facturación')
    recibido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedidos_ecommerce_recibidos',
        verbose_name='Recibido por (API)',
    )
    facturado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedidos_ecommerce_facturados',
        verbose_name='Facturado por',
    )

    class Meta:
        app_label = 'app'
        db_table = 'app_pedido_ecommerce'
        verbose_name = 'Pedido Ecommerce'
        verbose_name_plural = 'Pedidos Ecommerce'
        ordering = ['-fecha_recepcion']
        indexes = [
            models.Index(fields=['estado', 'fecha_recepcion']),
            models.Index(fields=['canal_origen', 'estado']),
            models.Index(fields=['sucursal', 'estado']),
        ]

    def __str__(self):
        return f"RM-{self.numero_ticket_rm} | {self.canal_origen} | {self.cliente_nombre}"

    @property
    def esta_pendiente(self):
        return self.estado == 'PENDIENTE'
