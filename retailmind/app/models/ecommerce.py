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
    # Ecommerces propios Django: AllConnected manda el código del tipo_marketplace
    # en MAYÚSCULAS (REALSPORT = realsport.cl, PAOLA = calzadospaola.cl).
    ('REALSPORT', 'Realsport'),
    ('PAOLA', 'Paola'),
    ('OTRO', 'Otro'),
]

ESTADO_PEDIDO_ECOMMERCE_CHOICES = [
    ('PENDIENTE', 'Pendiente de Facturar'),
    ('FACTURADO', 'Facturado'),
    ('CANCELADO', 'Cancelado'),
    ('ERROR', 'Error'),
]

SUB_ESTADO_PEDIDO_CHOICES = [
    # Sub-estados de PENDIENTE
    ('RECIBIDO', 'Recibido'),
    ('ASIGNADO', 'Asignado a Sucursal'),
    ('EN_PREPARACION', 'En Preparación'),
    ('LISTO_DESPACHO', 'Listo para Despacho'),
    # La tienda fue a buscar el producto y NO estaba (quiebre real de stock, no
    # un error del sistema): el pedido sigue PENDIENTE pero sale del flujo de
    # picking y se le reporta la incidencia a AllConnected, que decide qué hacer
    # (reasignar, sustituir o cancelar con el cliente).
    ('SIN_STOCK', 'Sin stock en tienda'),
    # Sub-estados de FACTURADO
    ('FACTURADO_OK', 'Facturado OK'),
    # El canal lo reporta ENVIADO/ENTREGADO sin boleta del módulo: la venta ya
    # se documentó POR CONCEPTO fuera de acá (decisión de negocio 04-ago). El
    # sync de estados lo cierra con este sub-estado para que salga de la cola
    # sin inflar "Facturado OK" ni perder el rastro de que no tiene DTE propio.
    ('FACTURADO_EXTERNO', 'Facturado por Concepto (externo)'),
    # Sub-estados de CANCELADO
    ('CANCELADO_CLIENTE', 'Cancelado por Cliente'),
    ('CANCELADO_SIN_STOCK', 'Cancelado por Sin Stock'),
    # Sub-estados de ERROR
    ('ERROR_STOCK', 'Error de Stock'),
    ('ERROR_DTE', 'Error al Generar DTE'),
]

PRIORIDAD_PEDIDO_CHOICES = [
    (0, 'Normal'),
    (1, 'Alta'),
    (2, 'Urgente'),
]

TIPO_EVENTO_HISTORIAL_CHOICES = [
    ('CAMBIO_ESTADO', 'Cambio de estado'),
    ('REASIGNACION', 'Reasignación de sucursal'),
    ('FACTURACION', 'Facturación'),
    ('ERROR', 'Error'),
]

MOTIVO_REASIGNACION_CHOICES = [
    ('SIN_STOCK', 'Sin stock en sucursal'),
    ('MANUAL', 'Reasignación manual'),
    ('DISTRIBUCION_AUTO', 'Distribución automática'),
]

# Transiciones válidas de sub-estado
# SIN_STOCK se alcanza desde cualquier etapa de picking (el quiebre se descubre
# tanto al asignar como con la guía en la mano) y solo vuelve a ASIGNADO, que es
# el punto desde donde se re-imprime la guía. Marcarlo/reactivarlo va por
# `api_marcar_sin_stock` (avisa a AllConnected), no por el cambio de sub-estado
# genérico: por eso NO se lista como destino en las otras entradas.
TRANSICIONES_SUB_ESTADO = {
    'RECIBIDO': ['ASIGNADO'],
    'ASIGNADO': ['EN_PREPARACION', 'RECIBIDO'],
    'EN_PREPARACION': ['LISTO_DESPACHO', 'ASIGNADO'],
    'LISTO_DESPACHO': ['EN_PREPARACION'],
    'SIN_STOCK': ['ASIGNADO'],
}

# Sub-estados en los que el pedido NO es trabajable por la tienda (ni guía de
# preparación ni facturación) hasta que se resuelva la incidencia.
SUB_ESTADOS_BLOQUEADOS_PICKING = ('SIN_STOCK',)


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
    numero_pedido_origen = models.CharField(
        max_length=50,
        blank=True,
        default='',
        db_index=True,
        verbose_name='N° Pedido (AllConnected)',
        help_text='Correlativo interno del pedido en AllConnected (ej. MP-000123)',
    )
    # Folio de despacho que AllConnected imprime en la etiqueta física
    # (ej. "PA3000198" = prefijo marketplace+canal + número con padding).
    # Llega vacío hasta que se imprime; en un pull posterior llega con valor.
    correlativo = models.CharField(
        max_length=50,
        blank=True,
        default='',
        db_index=True,
        verbose_name='Folio despacho',
        help_text='Folio de despacho de AllConnected impreso en la etiqueta (ej. PA3000198). Vacío hasta que se imprime.',
    )
    correlativo_numero = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name='Folio N°',
        help_text='Número crudo del correlativo (sin prefijo ni padding), para ordenar/buscar.',
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

    # Compra desde la app móvil de fidelización (puntos + dinero)
    coupon_code = models.CharField(
        max_length=50, blank=True, default='', db_index=True,
        verbose_name='Cupón',
        help_text='Código de cupón del pedido. PTS-<id> = cupón de puntos de la app móvil.',
    )
    from_app = models.BooleanField(
        default=False, db_index=True,
        verbose_name='Compra desde app',
        help_text='El pedido se originó en la app móvil de fidelización (acumula puntos por la parte en dinero).',
    )

    # Montos
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    descuento = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    impuestos = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Impuestos informados por el marketplace; trazabilidad, no base de recalculo.',
    )
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
    sub_estado = models.CharField(
        max_length=30,
        choices=SUB_ESTADO_PEDIDO_CHOICES,
        default='RECIBIDO',
        db_index=True,
        verbose_name='Sub-estado',
    )
    prioridad = models.IntegerField(
        choices=PRIORIDAD_PEDIDO_CHOICES,
        default=0,
        verbose_name='Prioridad',
    )

    # Último estado conocido del pedido EN EL CANAL (AllConnected), traído por
    # la sincronización de estados del botón "Traer pedidos". Es el estado
    # crudo de AC (PENDIENTE/CONFIRMADO/PAGADO/.../CANCELADO/DEVUELTO/
    # REEMBOLSADO), no el `estado` local. '' = nunca sincronizado (pedidos
    # anteriores a esta feature). La facturación bloquea cuando es
    # terminal-negativo o sigue PENDIENTE (sin pago confirmado en el canal).
    estado_canal = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name='Estado en el canal',
        help_text='Último Pedido.estado reportado por AllConnected (sync de estados)',
    )
    # AllConnected tiene `estado` y `estado_logistica` en PARALELO (sin máquina
    # de estados): un pedido despachado suele quedar estado='PREPARANDO' con
    # estado_logistica='ENVIADO'. Mirar solo `estado_canal` dejaba pedidos ya
    # enviados en la cola de la tienda (uno de 44 días, diagnóstico 05-ago).
    estado_logistica_canal = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name='Estado logístico en el canal',
        help_text='Último Pedido.estado_logistica reportado por AllConnected '
                  '(LISTO_ENVIO/LISTO_RETIRO = lo preparó la central).',
    )
    fecha_sync_estado_canal = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Última sync de estado canal',
    )

    # Quiebre de stock reportado por la tienda (sub_estado SIN_STOCK)
    sin_stock_motivo = models.CharField(
        max_length=255, blank=True, default='',
        verbose_name='Motivo sin stock',
        help_text='Lo que la tienda declaró al marcar el pedido sin stock.',
    )
    sin_stock_avisado_ac = models.BooleanField(
        default=False,
        verbose_name='Sin stock avisado a AllConnected',
        help_text='True si AllConnected confirmó la incidencia. False = el aviso '
                  'falló y hay que reintentarlo (el pedido igual quedó marcado acá).',
    )

    # Trazabilidad de asignación
    sucursal_original = models.ForeignKey(
        'app.Sucursal',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedidos_ecommerce_originales',
        verbose_name='Sucursal original',
        help_text='Primera sucursal asignada al recibir el pedido',
    )
    fecha_asignacion = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha asignación',
    )
    asignado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedidos_ecommerce_asignados',
        verbose_name='Asignado por',
    )

    # Trazabilidad de picking en tienda (guía de preparación)
    fecha_impresion_guia = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha impresión guía',
        help_text='Primera vez que la tienda imprimió la guía de preparación',
    )
    guia_impresa_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pedidos_ecommerce_guias_impresas',
        verbose_name='Guía impresa por',
    )
    fecha_inicio_preparacion = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha inicio preparación',
        help_text='Cuándo pasó a EN_PREPARACION (imprimir la guía lo marca)',
    )
    fecha_listo_despacho = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha listo despacho',
        help_text='Cuándo la tienda marcó LISTO_DESPACHO (fin del picking)',
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
            models.Index(fields=['sub_estado', 'fecha_recepcion']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['canal_origen', 'numero_pedido_canal'],
                name='uniq_pedido_ecom_canal_numero',
            ),
        ]

    def __str__(self):
        return f"RM-{self.numero_ticket_rm} | {self.canal_origen} | {self.cliente_nombre}"

    @property
    def esta_pendiente(self):
        return self.estado == 'PENDIENTE'

    def puede_transicionar_sub_estado(self, nuevo_sub_estado):
        """Verifica si la transición de sub-estado es válida."""
        permitidos = TRANSICIONES_SUB_ESTADO.get(self.sub_estado, [])
        return nuevo_sub_estado in permitidos

    @staticmethod
    def _minutos_entre(inicio, fin):
        """Minutos enteros entre dos datetimes; None si falta alguno."""
        if not inicio or not fin or fin < inicio:
            return None
        return int((fin - inicio).total_seconds() / 60)

    @property
    def minutos_reaccion(self):
        """T1: asignación → impresión de guía (cuánto tardó la tienda en reaccionar)."""
        return self._minutos_entre(self.fecha_asignacion, self.fecha_impresion_guia)

    @property
    def minutos_picking(self):
        """T2: inicio preparación → listo despacho (cuánto tardó el picking)."""
        return self._minutos_entre(self.fecha_inicio_preparacion, self.fecha_listo_despacho)

    @property
    def minutos_espera_factura(self):
        """T3: listo despacho → facturación."""
        return self._minutos_entre(self.fecha_listo_despacho, self.fecha_facturacion)


class HistorialPedidoEcommerce(models.Model):
    """Registro append-only de cambios de estado, sub-estado y sucursal."""

    pedido = models.ForeignKey(
        PedidoEcommerce,
        on_delete=models.CASCADE,
        related_name='historial',
    )
    estado_anterior = models.CharField(max_length=20, blank=True)
    estado_nuevo = models.CharField(max_length=20, blank=True)
    sub_estado_anterior = models.CharField(max_length=30, blank=True)
    sub_estado_nuevo = models.CharField(max_length=30, blank=True)
    sucursal_anterior = models.ForeignKey(
        'app.Sucursal', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    sucursal_nueva = models.ForeignKey(
        'app.Sucursal', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    motivo = models.TextField(blank=True)
    tipo_evento = models.CharField(
        max_length=30,
        choices=TIPO_EVENTO_HISTORIAL_CHOICES,
        default='CAMBIO_ESTADO',
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'app'
        db_table = 'app_historial_pedido_ecommerce'
        ordering = ['-fecha']
        verbose_name = 'Historial Pedido Ecommerce'
        verbose_name_plural = 'Historial Pedidos Ecommerce'

    def __str__(self):
        return f"{self.pedido.numero_ticket_rm} | {self.tipo_evento} | {self.fecha:%d/%m %H:%M}"


class MetricaAsignacionPedido(models.Model):
    """Métricas de calidad de asignación para tracking y alertas."""

    pedido = models.ForeignKey(
        PedidoEcommerce,
        on_delete=models.CASCADE,
        related_name='metricas_asignacion',
    )
    sucursal_asignada = models.ForeignKey(
        'app.Sucursal',
        on_delete=models.CASCADE,
        related_name='metricas_asignacion_pedidos',
    )
    fue_reasignado = models.BooleanField(default=False)
    motivo_reasignacion = models.CharField(
        max_length=50, blank=True,
        choices=MOTIVO_REASIGNACION_CHOICES,
    )
    todos_items_con_stock = models.BooleanField(default=True)
    items_sin_stock = models.IntegerField(default=0)
    tiempo_procesamiento_min = models.IntegerField(
        null=True, blank=True,
        verbose_name='Tiempo procesamiento (min)',
        help_text='Minutos desde recepción hasta facturación',
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'app'
        db_table = 'app_metrica_asignacion_pedido'
        ordering = ['-fecha']
        verbose_name = 'Métrica Asignación Pedido'
        verbose_name_plural = 'Métricas Asignación Pedidos'
        indexes = [
            models.Index(fields=['sucursal_asignada', 'fecha']),
        ]

    def __str__(self):
        return f"{self.pedido.numero_ticket_rm} → {self.sucursal_asignada} | {'Reasignado' if self.fue_reasignado else 'Original'}"
