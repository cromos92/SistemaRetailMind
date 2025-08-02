from django.db import models
from django.contrib.auth.models import User

class Empresa(models.Model):
    nombre = models.CharField(max_length=100)
    rut = models.CharField(max_length=20)  
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

    def __str__(self):
        return self.nombre

class Sucursal(models.Model):
    alias = models.CharField(max_length=100)
    direccion = models.CharField(max_length=100)
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    def __str__(self):
        return self.alias
class EmpresaUser(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, null=True, blank=True)

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.BooleanField(default=True) 
    active = models.BooleanField(default=False) 
    margenSobreprecio = models.IntegerField( null=True, blank=True) 
    margenPrecioVenta = models.IntegerField( null=True, blank=True) 
    def __str__(self):
        return f"{self.empresa} - {self.user} ({self.status})"


class Vendedor(models.Model):
    codigo_vendedor = models.CharField(max_length=100)
    rut = models.CharField(max_length=100,null=True)
    nombre = models.CharField(max_length=100,null=True)
    comision = models.DecimalField(max_digits=5, decimal_places=2, default=0) 
    fecha_nacimiento = models.DateField(null=True)
    correo = models.CharField(max_length=100,null=True)
    def __str__(self):
        return self.nombre


class Correlativo(models.Model):
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE)
    tipo_dte = models.CharField(max_length=50)
    inicio = models.IntegerField()
    termino = models.IntegerField()
    fecha_actualizacion = models.DateField(null=True)
    alias = models.CharField(max_length=100)
    responsable =   models.CharField(max_length=50)
    def __str__(self):
        return self.inicio
TIPO_DOCUMENTO_CHOICES = [
    ('FACTURA ELECTRONICA', 'Factura Electronica'),
    ('BOLETA ELECTRONICA', 'Boleta Electronica'),
    ('GUIA', 'Guía de Despacho'),
    ('NOTA DE PEDIDO', 'Nota de Pedido'),
    ('NOTA DE CREDITO', 'Nota de Credito'),
    ('NOTA DE DEBITO', 'Nota de debito'),
    ('FACTURA EXENTA', 'Factura Exenta'),
    ('COTIZACION', 'Cotizacion'),
 
]

ESTADO_PAGO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('PAGADO', 'Pagado'),
    ('VENCIDO', 'Vencido'),
]

ESTADO_DTE_CHOICES = [
    ('EMITIDO', 'Emitido'),
    ('RECHAZADO', 'Rechazado'),
    ('ACEPTADO', 'Aceptado'),
     ('ANULADO', 'Anulado'),
]
TIPO_TALLA_CHOICES = [
    ('CL', 'CL'),
    ('US', 'US'),
    ('EU', 'EU'),
    ('UK', 'UK'),
    ('BR', 'BR'),
    ('CM', 'CM'),
]

 

class Dte(models.Model):
    emisor = models.ForeignKey(Empresa, related_name='empresa_origen', on_delete=models.CASCADE)
    receptor = models.ForeignKey(Empresa, related_name='empresa_destino', on_delete=models.CASCADE,null=True,blank=True)
    numero_documento =  models.IntegerField()
    tipo_documento = models.CharField(max_length=20, choices=TIPO_DOCUMENTO_CHOICES)
    monto_con_iva = models.DecimalField(max_digits=12, decimal_places=2)
    monto_neto = models.DecimalField(max_digits=12, decimal_places=2)
    estado_pago = models.CharField(max_length=20, choices=ESTADO_PAGO_CHOICES)
    estado_dte = models.CharField(max_length=20, choices=ESTADO_DTE_CHOICES)
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
    tipo_transaccion = models.CharField(max_length=10, choices=[('COMPRA', 'Compra'), ('VENTA', 'Venta')])
    referencias = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"DTE {self.numero_documento} - {self.tipo_documento}"
class Dte_Detalle_Pago(models.Model):
    dte = models.ForeignKey(Dte, related_name='dte_asociado', on_delete=models.PROTECT)
    metodo_pago = models.CharField(max_length=100 )
    tipo_tarjeta =   models.CharField(max_length=100,null=True)
    voucher =  models.CharField(max_length=50,null=True)
    monto = models.IntegerField()
    
    def __str__(self):
        return f"Dte_Detalle_Pago {self.metodo_pago} - {self.monto}"
class Productos_Atributos(models.Model):
    nombre = models.CharField(max_length=100)
    descripcion = models.CharField(max_length=250)
    fecha_actualizacion = models.DateField(auto_now=True)
    def __str__(self):
        return f"Productos_Atributos {self.nombre} - {self.descripcion}"
class AtributoOpcion(models.Model):
    atributo = models.ForeignKey(Productos_Atributos, related_name='opciones', on_delete=models.CASCADE)
    valor = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.atributo.nombre}: {self.valor}"

class ProductoAtributoValor(models.Model):
    producto = models.ForeignKey('Producto', on_delete=models.CASCADE, related_name='atributos')
    atributo = models.ForeignKey(Productos_Atributos, on_delete=models.CASCADE)
    opcion = models.ForeignKey(AtributoOpcion, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.producto.articulo} - {self.atributo.nombre}: {self.opcion.valor}"


class Categoria(models.Model):
    nombre = models.CharField(max_length=100)
    padre = models.ForeignKey(
        'self', null=True, blank=True,
        related_name='subcategorias',
        on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.nombre}"

    def es_raiz(self):
        return self.padre is None

class GuiaTalla(models.Model):
    marca = models.ForeignKey('AtributoOpcion', on_delete=models.CASCADE, related_name='guia_tallas')  # Cambiado
    nombre = models.CharField(max_length=100)
    orden = models.IntegerField(default=0)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    productos = models.ManyToManyField('Producto', through='GuiaTallaProducto', related_name='guias_de_talla', blank=True)

    def __str__(self):
        return f"{self.marca.valor} - {self.nombre}"




class GuiaTallaItem(models.Model):
    guia = models.ForeignKey(GuiaTalla, on_delete=models.CASCADE, related_name='items')
    cl = models.CharField(max_length=20, blank=True, null=True)
    us = models.CharField(max_length=20, blank=True, null=True)
    eu = models.CharField(max_length=20, blank=True, null=True)
    uk = models.CharField(max_length=20, blank=True, null=True)
    br = models.CharField(max_length=20, blank=True, null=True)
    cm = models.CharField(max_length=20, blank=True, null=True)
    orden = models.IntegerField(default=0)

    def __str__(self):
        return f"Item {self.cl or ''} / {self.us or ''} / {self.cm or ''}"


class GuiaTallaProducto(models.Model):
    guia = models.ForeignKey(GuiaTalla, on_delete=models.CASCADE)
    producto = models.ForeignKey('Producto', on_delete=models.CASCADE)
    fecha_asociacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('guia', 'producto')

class Producto(models.Model):
    articulo      = models.CharField(max_length=200)
    descripcion   = models.CharField(max_length=250)

    # ──────────────────── CAMBIO CLAVE ────────────────────
    #  Ahora cada FK va a AtributoOpcion (Nike, Azul, Mujer…)
    atributo1 = models.ForeignKey(
        AtributoOpcion,                     # ← ya no Productos_Atributos
        related_name='productos_marca',     # usa el related_name que prefieras
        on_delete=models.CASCADE,
        null=True, blank=True               # opcional
    )
    atributo2 = models.ForeignKey(
        AtributoOpcion,
        related_name='productos_color',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    atributo3 = models.ForeignKey(
        AtributoOpcion,
        related_name='productos_genero',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    atributo4 = models.ForeignKey(
        AtributoOpcion,
        related_name='productos_otro',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    # ────────────────── FIN DE CAMBIOS ────────────────────

    categoria = models.ForeignKey(
        Categoria,
        related_name='categoria_productos',
        on_delete=models.CASCADE,
        null=True, blank=True
    )
    sucursal       = models.ForeignKey(Sucursal, related_name='sucursal_producto', on_delete=models.CASCADE)
    costo          = models.IntegerField()
    sobreprecio    = models.IntegerField()
    precioventa    = models.IntegerField()
    precioSugerido = models.IntegerField(null=True, blank=True)
    tipo_talla     = models.CharField(max_length=5, choices=TIPO_TALLA_CHOICES, default='CL')
    guia_talla = models.ForeignKey(
        'GuiaTalla',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='productos_principales'
    )
    def __str__(self):
        return f"Producto {self.articulo} - {self.precioventa}"


class Producto_Talla(models.Model):

    producto =   models.ForeignKey(Producto, related_name='producto_talla', on_delete=models.CASCADE)
    sku = models.IntegerField()
    stock =   models.IntegerField( )
    talla =   models.CharField(max_length=50)
  
    def __str__(self):
        return f"Producto_Talla {self.sku} - {self.stock}"
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
    ('RECEPCION_COMPRA', 'Recepción de Compra'),
    ('DEVOLUCION_CLIENTE', 'Devolución de Cliente'),
    ('TRASPASO_ENTRADA', 'Traspaso Entrada'),
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
]

ESTADO_MOVIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
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
    
    # === MÉTODOS DE PAGO ===
    metodo_pago = models.CharField(max_length=50, default='EFECTIVO', choices=[
        ('EFECTIVO', 'Efectivo'),
        ('TARJETA_DEBITO', 'Tarjeta Débito'),
        ('TARJETA_CREDITO', 'Tarjeta Crédito'),
        ('TRANSFERENCIA', 'Transferencia'),
        ('CHEQUE', 'Cheque'),
        ('OTRO', 'Otro'),
    ])
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha', '-hora']
        unique_together = ['sucursal', 'correlativo']
    
    def __str__(self):
        return f"Ticket {self.correlativo} - {self.sucursal} - ${self.total:,}"

class Ticket_Productos(models.Model):
    ProductoTalla = models.ForeignKey(Producto_Talla, related_name='ticket_productos_talla', on_delete=models.CASCADE)
    idTicket = models.ForeignKey(Ticket, related_name='ticket_productos', on_delete=models.CASCADE)
    stock = models.IntegerField()
    precio = models.IntegerField()  # Cambiado de CharField a IntegerField
    descuento_unitario = models.IntegerField(default=0)
    subtotal = models.IntegerField()
    
    # === NUEVOS CAMPOS ===
    precio_original = models.IntegerField(default=0)  # Precio antes de descuentos
    porcentaje_descuento = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    
    # === CAMPOS FIFO ===
    costo_fifo = models.IntegerField(default=0)  # Costo calculado con FIFO
    lotes_utilizados = models.TextField(blank=True, null=True)  # JSON de lotes utilizados
    
    class Meta:
        unique_together = ['ProductoTalla', 'idTicket']
    
    def __str__(self):
        return f"Ticket Producto {self.ProductoTalla} - {self.stock} unidades"

# ========== MODELO PARA TRASPASOS ==========

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
    costo = models.IntegerField()
    precio_venta = models.IntegerField()
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    class Meta:
        unique_together = ['traspaso', 'producto_talla']
    
    def __str__(self):
        return f"Detalle {self.producto_talla} - {self.cantidad_solicitada} unidades"

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
class Dte_Productos(models.Model):
    dte =   models.ForeignKey(Dte, related_name='dte_productos', on_delete=models.CASCADE)
    productoTalla =   models.ForeignKey(Producto_Talla, related_name='producto_talla_dte', on_delete=models.CASCADE)
    descripcion =   models.CharField(max_length=200)
    costo = models.IntegerField()
    sobreprecio =   models.IntegerField( )
    precio =   models.IntegerField( )
    stock =   models.IntegerField( )
    activo =   models.BooleanField(default=True)
  
    def __str__(self):
        return f"Dte_Producto   {self.dte} - {self.productoTalla}"
class Compras(models.Model):
    empresa =   models.ForeignKey(Empresa,   on_delete=models.CASCADE)
    nombre=   models.CharField(max_length=200)
    correlativo = models.IntegerField()
    responsable=   models.CharField(max_length=50)
    temporada=   models.CharField(max_length=50)
    fecha =   models.DateField( auto_now=True)
    fechaInicioTemporada =   models.DateField(null=True,blank=True)
    fechaTerminoTemporada =   models.DateField(null=True,blank=True)
    def __str__(self):
        return f"Compras   {self.nombre} - {self.temporada}"
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
    def __str__(self):
        return f"Compras_Producto   {self.nombre} - {self.compras}"
class Compras_Producto_Talla(models.Model):
    compra_producto =   models.ForeignKey(Compras_Producto,   on_delete=models.CASCADE)
    stock=   models.IntegerField()
    talla=   models.CharField(max_length=50)
    def __str__(self):
        return f"Compras_Producto_Talla   {self.compra_producto} - {self.stock}"
class Productos_Recepcionados(models.Model):
    compra_producto_talla =   models.ForeignKey(Compras_Producto_Talla,   on_delete=models.CASCADE)
    producto_talla = models.ForeignKey(Producto_Talla, on_delete=models.CASCADE, null=True, blank=True)

    dte = models.ForeignKey(Dte, on_delete=models.SET_NULL, null=True, blank=True, related_name='recepciones')  # <- Aquí va
    stockArribado=   models.IntegerField()
    fecha =   models.DateField( auto_now=True)
    def __str__(self):
        return f"Productos_Recepcionados   {self.compra_producto_talla} - {self.stockArribado}"
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
        return f"Cotizacion_Detalle   {self.correlativo} - {self.estadoPago}"
 
class ParametroGlobal(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    valor_entero = models.IntegerField(default=0)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.nombre}: {self.valor_entero}"

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
    fecha = models.DateField(auto_now=True)
    hora = models.TimeField(auto_now=True)
    
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
