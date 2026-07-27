from django.db import models
from django.utils import timezone
from django.conf import settings
from .organizacion import Empresa, Sucursal, Vendedor
from .catalogo import Producto_Talla

class Cotizacion(models.Model):
    correlativo=   models.IntegerField()
    vendedor =   models.ForeignKey(Vendedor,   on_delete=models.CASCADE)
    empresa =   models.ForeignKey(Empresa,   on_delete=models.CASCADE)
    sucursal =   models.ForeignKey(Sucursal,   on_delete=models.CASCADE)
    estado=   models.CharField(max_length=50)
    estadoPago=   models.CharField(max_length=50)
    responsable=   models.CharField(max_length=50)
    fechaCreacion =   models.DateField( auto_now=True)
    def __str__(self):
        return f"Cotizacion   {self.correlativo} - {self.estadoPago}"
class Cotizacion_Detalle(models.Model):
    cotizacion =   models.ForeignKey(Cotizacion,   on_delete=models.CASCADE)
    descripcion =  models.CharField(max_length=100)
    producto_talla =   models.ForeignKey(Producto_Talla,   on_delete=models.CASCADE, null=True, blank=True)
    stock=   models.IntegerField()
    costo=   models.IntegerField()
    sobreprecio=   models.IntegerField()
    precio=   models.IntegerField()
    def __str__(self):
        return f"Cotizacion_Detalle {self.descripcion} - Cotización #{self.cotizacion.correlativo}"
 
class Cotizacion_Empresa(models.Model):
    """
    Modelo para gestionar cotizaciones a empresas
    """
    # === ESTADOS DE COTIZACIÓN ===
    ESTADO_VIGENTE = 'VIGENTE'
    ESTADO_VENCIDA = 'VENCIDA'
    ESTADO_FACTURADA = 'FACTURADA'
    ESTADO_ANULADA = 'ANULADA'
    
    ESTADOS_COTIZACION = [
        (ESTADO_VIGENTE, 'Vigente'),
        (ESTADO_VENCIDA, 'Vencida'),
        (ESTADO_FACTURADA, 'Facturada'),
        (ESTADO_ANULADA, 'Anulada'),
    ]
    
    # === RELACIONES ===
    sucursal = models.ForeignKey(
        Sucursal, 
        on_delete=models.CASCADE,
        related_name='cotizaciones'
    )
    cliente = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE,
        related_name='cotizaciones_recibidas',
        help_text="Cliente/Empresa que recibe la cotización"
    )
    vendedor = models.ForeignKey(
        Vendedor, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='cotizaciones'
    )
    usuario_creador = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='cotizaciones_creadas'
    )
    
    # === INFORMACIÓN DE LA COTIZACIÓN ===
    numero_cotizacion = models.CharField(
        max_length=20, 
        unique=True,
        help_text="Número único de cotización"
    )
    fecha_emision = models.DateField(
        default=timezone.now,
        help_text="Fecha de emisión de la cotización"
    )
    fecha_validez = models.DateField(
        help_text="Fecha hasta la cual la cotización es válida"
    )
    dias_validez = models.IntegerField(
        default=30,
        help_text="Días de validez de la cotización"
    )
    
    # === DESCRIPCIÓN Y DETALLES ===
    descripcion = models.TextField(
        blank=True, null=True,
        help_text="Descripción general de la cotización"
    )
    observaciones = models.TextField(
        blank=True, null=True,
        help_text="Observaciones, condiciones de pago, notas adicionales"
    )
    
    # === MONTOS ===
    subtotal = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Subtotal de la cotización sin impuestos"
    )
    descuento = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Descuento aplicado"
    )
    impuesto = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Impuestos (IVA)"
    )
    total = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Total de la cotización"
    )
    
    # === ESTADO Y FACTURACIÓN ===
    estado = models.CharField(
        max_length=20, 
        choices=ESTADOS_COTIZACION, 
        default=ESTADO_VIGENTE
    )
    facturada = models.BooleanField(
        default=False,
        help_text="Indica si la cotización fue convertida en factura"
    )
    numero_factura = models.CharField(
        max_length=20,
        blank=True, null=True,
        help_text="Número de factura si fue facturada"
    )
    dte = models.ForeignKey(
        'Dte',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cotizaciones',
        help_text=(
            "Documento tributario emitido al facturar. `numero_factura` guarda "
            "solo el número, que se repite entre tipos de DTE y sucursales: "
            "esta FK es la que identifica el documento de verdad."
        )
    )
    fecha_facturacion = models.DateTimeField(
        blank=True, null=True,
        help_text="Fecha en que se facturó"
    )

    # === DESPACHO DIFERIDO ===
    DESPACHO_PENDIENTE   = 'PENDIENTE'
    DESPACHO_PARCIAL     = 'PARCIAL'
    DESPACHO_COMPLETADO  = 'COMPLETADO'
    ESTADO_DESPACHO_CHOICES = [
        ('PENDIENTE',  'Pendiente de Despacho'),
        ('PARCIAL',    'Despacho Parcial'),
        ('COMPLETADO', 'Despacho Completado'),
    ]
    estado_despacho = models.CharField(
        max_length=20,
        choices=ESTADO_DESPACHO_CHOICES,
        null=True, blank=True,
        help_text="Estado del despacho cuando la cotización tiene ítems sin SKU al facturar"
    )

    # === VALIDACIÓN DE DESPACHO (OK del Administrador) ===
    # Cierre formal del despacho diferido: un usuario con permiso
    # `gestion_cotizaciones.puede_aprobar` confirma que las unidades
    # facturadas coinciden con las despachadas (salidas de stock).
    despacho_validado = models.BooleanField(
        default=False,
        help_text="True cuando un administrador dio el OK final al despacho (cuadratura facturado vs despachado)"
    )
    despacho_validado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='despachos_cotizacion_validados',
        help_text="Usuario que validó el despacho completado"
    )
    fecha_validacion_despacho = models.DateTimeField(
        blank=True, null=True,
        help_text="Fecha/hora en que se validó el despacho"
    )


    # === ARCHIVOS ADJUNTOS ===
    archivo_pdf = models.FileField(
        upload_to='cotizaciones/pdfs/', 
        blank=True, null=True,
        help_text="PDF de la cotización"
    )
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    anulada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cotizaciones_anuladas'
    )
    fecha_anulacion = models.DateTimeField(
        blank=True, null=True
    )
    motivo_anulacion = models.TextField(
        blank=True, null=True
    )
    
    class Meta:
        ordering = ['-fecha_emision', '-numero_cotizacion']
        verbose_name = 'Cotización'
        verbose_name_plural = 'Cotizaciones'
        indexes = [
            models.Index(fields=['numero_cotizacion']),
            models.Index(fields=['sucursal', 'fecha_emision']),
            models.Index(fields=['cliente', 'estado']),
            models.Index(fields=['fecha_validez', 'estado']),
        ]
    
    def __str__(self):
        return f"Cotización {self.numero_cotizacion} - {self.cliente.nombre}"
    
    def save(self, *args, **kwargs):
        # Calcular fecha de validez si no está definida
        if not self.fecha_validez and self.fecha_emision and self.dias_validez:
            from datetime import timedelta
            self.fecha_validez = self.fecha_emision + timedelta(days=self.dias_validez)
        
        # Actualizar estado según validez
        if self.fecha_validez and not self.facturada and self.estado == self.ESTADO_VIGENTE:
            if self.fecha_validez < timezone.localdate():
                self.estado = self.ESTADO_VENCIDA
        
        super().save(*args, **kwargs)
    
    @property
    def esta_vigente(self):
        """Verifica si la cotización está vigente"""
        return (
            self.estado == self.ESTADO_VIGENTE and 
            self.fecha_validez >= timezone.localdate() and 
            not self.facturada
        )
    
    @property
    def dias_restantes(self):
        """Calcula los días restantes de validez"""
        if self.fecha_validez:
            delta = self.fecha_validez - timezone.localdate()
            return delta.days
        return 0
    
    @property
    def porcentaje_vigencia(self):
        """Calcula el porcentaje de vigencia restante"""
        if self.dias_validez > 0:
            dias_transcurridos = (timezone.localdate() - self.fecha_emision).days
            return max(0, min(100, ((self.dias_validez - dias_transcurridos) / self.dias_validez) * 100))
        return 0
    
    def calcular_totales(self):
        """
        Calcula los totales de la cotización basándose en sus items.
        IMPORTANTE: Los precios unitarios ya INCLUYEN IVA (19%).
        Por lo tanto, debemos calcular el neto y el IVA desde el total con IVA.

        El desglose se hace sobre el total YA DESCONTADO y el IVA se deriva por
        diferencia (`total - neto`), igual que `generar_dte_desde_ticket`. Así
        `subtotal + impuesto == total` siempre. Antes el neto y el IVA se
        calculaban sobre el bruto pre-descuento y el total post-descuento, así
        que con descuento el desglose del PDF no sumaba el total.
        """
        from decimal import Decimal, ROUND_HALF_UP

        items = self.items.all()
        # total_bruto = suma de (cantidad * precio_unitario) - El precio YA incluye IVA
        total_bruto = sum((item.subtotal for item in items), Decimal('0'))

        total_final = total_bruto - (self.descuento or Decimal('0'))

        # Neto desde el total con IVA: neto = total / 1.19
        self.subtotal = (total_final / Decimal('1.19')).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP
        )
        # IVA por diferencia: evita el doble redondeo que descuadraba el desglose.
        self.impuesto = total_final - self.subtotal
        self.total = total_final

        self.save(update_fields=['subtotal', 'impuesto', 'total', 'updated_at'])
    
    def anular(self, usuario, motivo=""):
        """Anula la cotización"""
        self.estado = self.ESTADO_ANULADA
        self.anulada_por = usuario
        self.fecha_anulacion = timezone.now()
        self.motivo_anulacion = motivo
        self.save()
    
    def marcar_como_facturada(self, numero_factura, tiene_pendientes=False, dte=None):
        """
        Marca la cotización como facturada, opcionalmente con despacho pendiente.

        `dte` es el documento tributario realmente emitido: guardarlo permite
        enlazar después los movimientos de despacho diferido al documento y
        completar las líneas del DTE que quedaron sin SKU.
        """
        self.facturada = True
        self.estado = self.ESTADO_FACTURADA
        self.numero_factura = numero_factura
        self.fecha_facturacion = timezone.now()
        if dte is not None:
            self.dte = dte
        if tiene_pendientes:
            self.estado_despacho = self.DESPACHO_PENDIENTE
        else:
            self.estado_despacho = self.DESPACHO_COMPLETADO
        self.save()

    # ------------------------------------------------------------------
    # Cuadratura por UNIDADES (facturado vs despachado)
    #
    # La factura siempre cubre el 100% de lo cotizado; el despacho puede ir
    # saliendo por partes. Antes el estado se calculaba contando ÍTEMS con
    # flag pendiente, así que un despacho parcial (facturado 5, sacado 2)
    # cerraba el ítem y el descuadre quedaba invisible. Ahora todo se mide
    # en unidades.
    # ------------------------------------------------------------------

    @property
    def unidades_facturadas(self):
        """Total de unidades cubiertas por la factura (todos los ítems)."""
        from django.db.models import Sum
        return self.items.aggregate(t=Sum('cantidad'))['t'] or 0

    @property
    def unidades_pendientes_despacho(self):
        """Unidades facturadas que aún no tienen salida de stock."""
        return sum(item.unidades_pendientes_despacho for item in self.items.all())

    @property
    def unidades_despachadas(self):
        """Unidades con salida de stock (al facturar o post-factura)."""
        return self.unidades_facturadas - self.unidades_pendientes_despacho

    @property
    def despacho_cuadrado(self):
        """True si facturado == despachado (en unidades)."""
        return self.facturada and self.unidades_pendientes_despacho == 0

    def actualizar_estado_despacho(self):
        """Recalcula estado_despacho por UNIDADES pendientes (no por ítems)."""
        if not self.facturada:
            return
        pendientes = self.unidades_pendientes_despacho
        if pendientes == 0:
            self.estado_despacho = self.DESPACHO_COMPLETADO
        elif pendientes < self.unidades_facturadas:
            self.estado_despacho = self.DESPACHO_PARCIAL
        else:
            self.estado_despacho = self.DESPACHO_PENDIENTE
        self.save(update_fields=['estado_despacho'])

    def invalidar_validacion_despacho(self):
        """Limpia el OK del administrador (ej. al revertir un despacho).

        Devuelve True si había una validación vigente (para que el llamador
        registre el historial con contexto)."""
        if not self.despacho_validado:
            return False
        self.despacho_validado = False
        self.despacho_validado_por = None
        self.fecha_validacion_despacho = None
        self.save(update_fields=[
            'despacho_validado', 'despacho_validado_por', 'fecha_validacion_despacho',
        ])
        return True


class Cotizacion_Empresa_Detalle(models.Model):
    """
    Detalle de items de la cotización
    Permite asociar productos existentes o productos pendientes de crear
    """
    # === RELACIONES ===
    cotizacion = models.ForeignKey(
        Cotizacion_Empresa, 
        on_delete=models.CASCADE,
        related_name='items'
    )
    
    # === PRODUCTO ASOCIADO (OPCIONAL) ===
    producto_existente = models.ForeignKey(
        'Producto_Talla', 
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cotizaciones_asociadas',
        help_text="Producto existente en inventario"
    )
    
    # === INFORMACIÓN DEL PRODUCTO PENDIENTE ===
    # Estos campos se usan si el producto aún no existe
    es_producto_pendiente = models.BooleanField(
        default=False,
        help_text="Indica si el producto aún no está creado en el sistema"
    )
    nombre_producto_pendiente = models.CharField(
        max_length=255, 
        blank=True, null=True,
        help_text="Nombre del producto pendiente"
    )
    descripcion_producto_pendiente = models.TextField(
        blank=True, null=True,
        help_text="Descripción del producto pendiente"
    )
    sku_producto_pendiente = models.CharField(
        max_length=100, 
        blank=True, null=True,
        help_text="SKU esperado del producto pendiente"
    )
    
    # === INFORMACIÓN DEL ITEM ===
    numero_linea = models.IntegerField(
        help_text="Número de línea del item"
    )
    descripcion = models.TextField(
        help_text="Descripción del item en la cotización"
    )
    cantidad = models.IntegerField(
        default=1,
        help_text="Cantidad cotizada"
    )
    precio_unitario = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Precio unitario comprometido en la cotización"
    )
    
    # === CÁLCULOS ===
    descuento_porcentaje = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0,
        help_text="Porcentaje de descuento aplicado"
    )
    descuento_monto = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0,
        help_text="Monto de descuento"
    )
    subtotal = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Subtotal del item (cantidad * precio - descuento)"
    )
    
    # === STOCK Y DISPONIBILIDAD ===
    stock_disponible = models.IntegerField(
        default=0,
        help_text="Stock disponible al momento de la cotización"
    )
    fecha_llegada_estimada = models.DateField(
        blank=True, null=True,
        help_text="Fecha estimada de llegada si es producto pendiente"
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(
        blank=True, null=True,
        help_text="Observaciones específicas del item"
    )
    
    # === TRAZABILIDAD POST-FACTURA (Despacho Diferido) ===
    sku_asignado_post_factura = models.BooleanField(
        default=False,
        help_text="True cuando un ítem pendiente recibe SKU después de facturar"
    )
    fecha_asignacion_sku = models.DateTimeField(
        null=True, blank=True,
        help_text="Fecha en que se asignó el SKU post-factura"
    )
    usuario_asignacion_sku = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='items_sku_asignados',
        help_text="Usuario que realizó la asignación de SKU post-factura"
    )

    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['cotizacion', 'numero_linea']
        verbose_name = 'Detalle de Cotización'
        verbose_name_plural = 'Detalles de Cotización'
        indexes = [
            models.Index(fields=['cotizacion', 'numero_linea']),
            models.Index(fields=['producto_existente']),
            models.Index(fields=['es_producto_pendiente']),
        ]
    
    def __str__(self):
        if self.producto_existente:
            producto_nombre = getattr(self.producto_existente, 'producto', None)
            if producto_nombre:
                return f"{self.cotizacion.numero_cotizacion} - {producto_nombre.articulo}"
        return f"{self.cotizacion.numero_cotizacion} - {self.descripcion[:50]}"
    
    def save(self, *args, recalcular_cotizacion=True, **kwargs):
        """
        Guarda el ítem y, por defecto, recalcula los totales de la cotización.

        `recalcular_cotizacion=False` para cargas masivas: recalcular por ítem
        es O(n²) — cada save recorre TODOS los ítems y escribe la cotización.
        Crear/editar una cotización de 30 líneas disparaba ~900 iteraciones y 30
        UPDATE extra. Los llamadores masivos llaman `calcular_totales()` una vez
        al final.
        """
        from decimal import Decimal
        # Calcular subtotal
        subtotal_antes_descuento = Decimal(str(self.cantidad)) * self.precio_unitario

        # Aplicar descuento
        if self.descuento_porcentaje and self.descuento_porcentaje > 0:
            self.descuento_monto = subtotal_antes_descuento * (self.descuento_porcentaje / Decimal('100'))
        else:
            self.descuento_monto = Decimal('0')

        self.subtotal = subtotal_antes_descuento - self.descuento_monto

        # Obtener stock si hay producto existente
        if self.producto_existente and not self.es_producto_pendiente:
            # Aquí podrías calcular el stock real desde el inventario
            # Por ahora dejamos el valor que se asigne manualmente
            pass

        super().save(*args, **kwargs)

        # Recalcular totales de la cotización
        if recalcular_cotizacion:
            self.cotizacion.calcular_totales()
    
    @property
    def tiene_stock_suficiente(self):
        """Verifica si hay stock suficiente"""
        if self.es_producto_pendiente:
            return False
        if self.producto_existente:
            return self.stock_disponible >= self.cantidad
        return False
    
    @property
    def precio_total(self):
        """Precio total del item"""
        return self.subtotal
    
    @property
    def nombre_producto(self):
        """Retorna el nombre del producto (existente o pendiente)"""
        if self.producto_existente and self.producto_existente.producto:
            return self.producto_existente.producto.articulo
        return self.nombre_producto_pendiente or "Producto sin nombre"
    
    @property
    def sku_producto(self):
        """Retorna el SKU del producto (existente o pendiente)"""
        if self.producto_existente:
            # El SKU está en Producto_Talla, no en Producto
            return str(self.producto_existente.sku)
        return self.sku_producto_pendiente or "N/A"

    # --- Cuadratura por unidades (despacho diferido) ---

    @property
    def nacio_pendiente(self):
        """True si el ítem se facturó SIN SKU (despacho diferido).

        Tras completarse el despacho `es_producto_pendiente` vuelve a False,
        pero `sku_asignado_post_factura` queda True, así que la condición
        sigue siendo detectable."""
        return self.es_producto_pendiente or self.sku_asignado_post_factura

    @property
    def unidades_despachadas_post_factura(self):
        """Unidades ya despachadas después de facturar (suma de asignaciones)."""
        from django.db.models import Sum
        return (
            self.skus_asociados.filter(asignado_post_factura=True)
            .aggregate(t=Sum('cantidad'))['t'] or 0
        )

    @property
    def unidades_cubiertas_al_facturar(self):
        """Unidades que sí tenían SKU al facturar (salieron con el ticket).

        Se mide sobre las filas de `skus_asociados` NO marcadas como
        post-factura, que es lo único que respalda una salida de stock junto
        con la factura.

        Compatibilidad: un ítem con `producto_existente` pero SIN ninguna fila
        de SKU es el enlace viejo (un solo SKU por línea, antes de que existiera
        Cotizacion_Empresa_Detalle_SKU). En ese caso se asume cobertura total,
        que es lo que asumía el código anterior: inventar unidades pendientes
        sobre datos históricos reabriría despachos ya cerrados.
        """
        if self.nacio_pendiente:
            # Nació sin SKU: por definición no salió nada con el ticket.
            return 0
        filas = [s for s in self.skus_asociados.all() if not s.asignado_post_factura]
        if not filas:
            return self.cantidad if self.producto_existente_id else 0
        return sum(s.cantidad or 0 for s in filas)

    @property
    def unidades_pendientes_despacho(self):
        """Unidades facturadas sin salida de stock todavía.

        Antes: si el ítem no nació pendiente devolvía 0 sin mirar CUÁNTAS
        unidades respaldaban realmente los SKUs. Un ítem de 5 unidades cuyos
        SKUs sólo cubrían 1 daba 0 pendientes y las 4 restantes quedaban
        invisibles para el despacho diferido y para la cuadratura (caso real en
        producción: COT-202607-0001, línea de 5 uds con 1 sola respaldada).

        Ahora: facturado − cubierto al facturar − despachado post-factura.
        """
        return max(
            0,
            self.cantidad
            - self.unidades_cubiertas_al_facturar
            - self.unidades_despachadas_post_factura
        )


class Cotizacion_Empresa_Detalle_SKU(models.Model):
    """
    SKUs asociados a cada item de cotización.
    Permite asociar múltiples productos (SKUs) a un solo item de cotización.
    Ejemplo: Item "Balón" cantidad 5 -> SKU1 (cant 4) + SKU2 (cant 1)
    """
    # === RELACIONES ===
    detalle = models.ForeignKey(
        Cotizacion_Empresa_Detalle,
        on_delete=models.CASCADE,
        related_name='skus_asociados'
    )
    producto_talla = models.ForeignKey(
        'Producto_Talla',
        on_delete=models.SET_NULL,
        null=True,
        related_name='cotizaciones_detalle_skus'
    )
    
    # === DATOS DEL SKU ===
    cantidad = models.IntegerField(
        default=1,
        help_text="Cantidad de este SKU asignada al item"
    )
    costo_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Costo unitario al momento de la cotización"
    )
    precio_unitario = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Precio de venta unitario"
    )
    asignado_post_factura = models.BooleanField(
        default=False,
        help_text=(
            "True cuando este SKU se asignó DESPUÉS de facturar (despacho "
            "diferido con salida de stock). Los SKUs asociados al crear la "
            "cotización quedan en False: sin esta marca la cuadratura "
            "facturado-vs-despachado sería imposible."
        )
    )

    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'SKU de Detalle de Cotización'
        verbose_name_plural = 'SKUs de Detalles de Cotización'
        ordering = ['detalle', 'id']
    
    def __str__(self):
        sku = self.producto_talla.sku if self.producto_talla else 'N/A'
        return f"Item #{self.detalle.numero_linea} - SKU {sku} x{self.cantidad}"
    
    @property
    def subtotal_costo(self):
        """Subtotal de costo para este SKU"""
        return self.cantidad * self.costo_unitario
    
    @property
    def subtotal_precio(self):
        """Subtotal de precio para este SKU"""
        return self.cantidad * self.precio_unitario
    
    @property
    def margen_porcentaje(self):
        """Porcentaje de margen para este SKU"""
        if self.precio_unitario > 0:
            return round(((self.precio_unitario - self.costo_unitario) / self.precio_unitario) * 100, 1)
        return 0


class Historial_Cotizacion(models.Model):
    """
    Historial de cambios y acciones en cotizaciones
    """
    # === RELACIONES ===
    cotizacion = models.ForeignKey(
        Cotizacion_Empresa, 
        on_delete=models.CASCADE,
        related_name='historial'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='acciones_cotizacion'
    )
    
    # === INFORMACIÓN DEL CAMBIO ===
    accion = models.CharField(
        max_length=50,
        choices=[
            ('CREADA', 'Cotización Creada'),
            ('MODIFICADA', 'Cotización Modificada'),
            ('ANULADA', 'Cotización Anulada'),
            ('FACTURADA', 'Convertida a Factura'),
            ('ENVIADA', 'Enviada al Cliente'),
            ('VENCIDA', 'Marcada como Vencida'),
            ('ITEM_AGREGADO', 'Item Agregado'),
            ('ITEM_MODIFICADO', 'Item Modificado'),
            ('ITEM_ELIMINADO', 'Item Eliminado'),
            ('SKU_ASIGNADO', 'SKU Asignado Post-Factura'),
            ('DESPACHO_COMPLETADO', 'Despacho Completado'),
            ('DESPACHO_VALIDADO', 'Despacho Validado (OK Admin)'),
        ]
    )
    descripcion = models.TextField(
        help_text="Descripción de la acción realizada"
    )
    datos_anteriores = models.JSONField(
        blank=True, null=True,
        help_text="Datos antes del cambio (JSON)"
    )
    datos_nuevos = models.JSONField(
        blank=True, null=True,
        help_text="Datos después del cambio (JSON)"
    )
    
    # === METADATA ===
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        blank=True, null=True,
        help_text="Dirección IP desde donde se realizó la acción"
    )
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Historial de Cotización'
        verbose_name_plural = 'Historiales de Cotización'
    
    def __str__(self):
        return f"{self.cotizacion.numero_cotizacion} - {self.accion} - {self.timestamp.strftime('%d/%m/%Y %H:%M')}"

