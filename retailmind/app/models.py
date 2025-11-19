from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.conf import settings

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
    
    # ✅ Campos para facturación electrónica (Acepta)
    acteco = models.CharField(max_length=20, blank=True, null=True, verbose_name='Código Acteco', help_text='Código de actividad económica del SII')
    contacto1 = models.CharField(max_length=100, blank=True, null=True, verbose_name='Contacto 1', help_text='Teléfono o email de contacto principal')
    contacto2 = models.CharField(max_length=100, blank=True, null=True, verbose_name='Contacto 2', help_text='Teléfono o email de contacto secundario')

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

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
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
    sucursales = models.ManyToManyField(Sucursal, related_name='vendedores', blank=True, verbose_name='Sucursales asignadas')
    
    def __str__(self):
        return self.nombre
    
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
        """Retorna el número actual del correlativo"""
        return self.inicio
    
    @property
    def disponibles(self):
        """Retorna la cantidad de números disponibles"""
        return max(0, self.termino - self.inicio + 1)
    
    @property
    def consumidos(self):
        """Retorna la cantidad de números consumidos (asumiendo que empezó en 1)"""
        return max(0, self.inicio - 1)
    
    @property
    def total_rango(self):
        """Retorna el total del rango"""
        return self.termino
    
    @property
    def porcentaje_consumo(self):
        """Retorna el porcentaje de consumo"""
        if self.total_rango > 0:
            return (self.consumidos / self.total_rango) * 100
        return 0
    
    @property
    def estado(self):
        """Retorna el estado del correlativo"""
        if self.disponibles <= 0:
            return 'agotado'
        elif self.disponibles <= 100:
            return 'critico'
        else:
            return 'activo'
    
    def puede_emitir(self):
        """Verifica si se puede emitir un documento con este correlativo"""
        return self.inicio <= self.termino
    
    def obtener_siguiente_numero(self):
        """Obtiene el siguiente número y actualiza el correlativo"""
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
    ('TRASPASO', 'Traspaso'),
    ('AJUSTE', 'Ajuste de Inventario'),
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
    ('EN_REGULARIZACION', 'En Regularización'),
    ('RECHAZADO', 'Rechazado'),
    ('ANULADO', 'Anulado'),
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
]

# Nuevos choices para Solicitudes de Regularización
TIPO_PROBLEMA_CHOICES = [
    ('FALTANTE', 'Faltante'),
    ('DANADO', 'Dañado'),
    ('PARCIAL', 'Recepción Parcial'),
    ('INCORRECTO', 'Producto Incorrecto'),
]

TIPO_SOLUCION_CHOICES = [
    ('NOTA_CREDITO', 'Nota de Crédito'),
    ('REENVIO', 'Reenvío del mismo producto'),
    ('CAMBIO_PRODUCTO', 'Cambio por otro producto'),
    ('AJUSTE_CANTIDAD', 'Ajustar solo cantidad'),
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
METODO_PAGO_TICKET_CHOICES = [
    ('EFECTIVO', 'Efectivo'),
    ('TARJETA_DEBITO', 'Tarjeta Débito'),
    ('TARJETA_CREDITO', 'Tarjeta Crédito'),
    ('TRANSFERENCIA', 'Transferencia'),
    ('CHEQUE', 'Cheque'),
    ('OTRO', 'Otro'),
    ('TBK_POS_INTEGRADO', 'Transbank POS Integrado'),
    ('TBK_MANUAL', 'Transbank Manual'),
    ('TBK_DEBITO_POS', 'Transbank Débito POS'),
    ('TBK_CREDITO_POS', 'Transbank Crédito POS'),
    ('TBK_PREPAGO_POS', 'Transbank Prepago POS'),
    ('TARJETA_COMERCIAL', 'Tarjeta Comercial'),
    ('VENTA_INTERNET', 'Venta por Internet'),
    ('ORDEN_COMPRA', 'Orden de Compra'),
    ('CREDITO_TRABAJADOR', 'Crédito Trabajador'),
    ('CREDITO_EXTERNO', 'Crédito Externo'),
    ('CONVENIO', 'Convenio'),
    ('MULTIPLE', 'Pagos Combinados'),
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

    def __str__(self):
        return f"DTE {self.numero_documento} - {self.tipo_documento}"
    
    def es_misma_empresa_check(self):
        """Verifica si emisor y receptor son la misma empresa"""
        return self.emisor_id == self.receptor_id if self.receptor else False
    
    def requiere_nota_credito_check(self):
        """Determina si requiere NC para regularización (empresas diferentes)"""
        return not self.es_misma_empresa_check() and self.tipo_transaccion == 'TRASPASO'
class Dte_Detalle_Pago(models.Model):
    dte = models.ForeignKey(Dte, related_name='dte_asociado', on_delete=models.PROTECT)
    metodo_pago = models.CharField(max_length=100 )
    tipo_tarjeta =   models.CharField(max_length=100,null=True)
    voucher =  models.CharField(max_length=50,null=True)
    monto = models.IntegerField()
    notas = models.TextField(blank=True, null=True)
    
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
    sku = models.BigIntegerField()  # Cambiado a BigIntegerField para soportar codigo_asociado de MySQL
    stock =   models.IntegerField( )
    talla =   models.CharField(max_length=50)
  
    def __str__(self):
        return f"Producto_Talla {self.sku} - {self.stock}"
    
    def stock_sucursal(self, sucursal_id):
        """
        Calcula el stock disponible en una sucursal específica
        
        SISTEMA HÍBRIDO (compatible con migración de datos legacy):
        - Si existen movimientos: Calcula desde movimientos (sistema nuevo)
        - Si NO hay movimientos: Usa campo 'stock' directo (datos migrados/legacy)
        
        Esto permite migrar datos históricos sin crear millones de movimientos
        """
        from django.db.models import Sum, Q
        
        # Verificar si hay movimientos para esta talla
        tiene_movimientos = self.movimientos_productos_talla.exists()
        
        if tiene_movimientos:
            # SISTEMA NUEVO: Calcular desde movimientos
            # Sumar ingresos a esta sucursal (movimientos donde sucursal_destino = sucursal_id)
            ingresos = self.movimientos_productos_talla.filter(
                Q(sucursal_destino_id=sucursal_id) &
                (Q(tipo_movimiento='INGRESO') | Q(concepto='TRASPASO_ENTRADA')) &
                Q(estado='COMPLETADO')
            ).aggregate(total=Sum('cantidad'))['total'] or 0
            
            # Sumar egresos desde esta sucursal (movimientos donde sucursal_origen = sucursal_id)
            egresos = self.movimientos_productos_talla.filter(
                Q(sucursal_origen_id=sucursal_id) &
                (Q(tipo_movimiento='EGRESO') | Q(concepto='TRASPASO_SALIDA')) &
                Q(estado='COMPLETADO')
            ).aggregate(total=Sum('cantidad'))['total'] or 0
            
            # El stock en sucursal es ingresos + egresos (egresos son negativos)
            stock_calculado = ingresos + egresos
            
            return max(0, stock_calculado)  # No permitir stock negativo
        else:
            # SISTEMA LEGACY: Intentar obtener stock desde StockSucursal si existe
            try:
                from .models import StockSucursal
                stock_registro = StockSucursal.objects.filter(
                    producto_talla=self,
                    sucursal_id=sucursal_id
                ).first()
                
                if stock_registro:
                    return max(0, stock_registro.cantidad)
            except (ImportError, AttributeError):
                # Si no existe el modelo StockSucursal, continuar con la lógica legacy
                pass
            
            # Fallback: Usar campo stock directo (datos migrados/legacy)
            # Si el producto pertenece a esta sucursal, retornar su stock
            if self.producto.sucursal_id == sucursal_id:
                return max(0, self.stock)
            else:
                # ⚠️ ADVERTENCIA: En modo legacy sin movimientos ni tabla StockSucursal,
                # no podemos determinar el stock por sucursal con precisión.
                # Retornamos 0 para evitar inconsistencias.
                # SOLUCIÓN: Migrar a sistema de movimientos o crear registros en StockSucursal
                return 0  # No hay stock en esta sucursal
    
    def stock_total(self):
        """
        Calcula el stock total en todas las sucursales
        """
        from django.db.models import Sum
        
        # Sumar todos los ingresos
        ingresos = self.movimientos_productos_talla.filter(
            tipo_movimiento='INGRESO',
            estado='COMPLETADO'
        ).aggregate(total=Sum('cantidad'))['total'] or 0
        
        # Sumar todos los egresos (son negativos)
        egresos = self.movimientos_productos_talla.filter(
            tipo_movimiento='EGRESO',
            estado='COMPLETADO'
        ).aggregate(total=Sum('cantidad'))['total'] or 0
        
        stock_calculado = ingresos + egresos
        
        return max(0, stock_calculado)
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
    modulo_origen = models.CharField(max_length=20, default='VENTA_PUBLICO', choices=[
        ('VENTA_PUBLICO', 'Venta al Público'),
        ('VENTA_MAYORISTA', 'Venta Mayorista'),
        ('POS', 'Punto de Venta'),
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
    
    # Auditoría
    fecha = models.DateField(auto_now=True)
    fecha_recepcion = models.DateTimeField(null=True, blank=True)
    recepcionado_por = models.CharField(max_length=100, blank=True, null=True)
    fecha_regularizacion = models.DateTimeField(null=True, blank=True)
    regularizado_por = models.CharField(max_length=100, blank=True, null=True)
    
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


# ========== MODELO PARA ARQUEO DE CAJA ==========

ESTADO_ARQUEO_CHOICES = [
    ('ABIERTO', 'En Proceso'),
    ('CERRADO', 'Finalizado'),
    ('CON_DIFERENCIAS', 'Con Diferencias'),
    ('REVISADO', 'Revisado por Supervisor'),
]

class ArqueoCaja(models.Model):
    """
    Modelo para registrar arqueos de caja diarios
    Guarda los mismos totales que se calculan en la cuadratura
    """
    # === INFORMACIÓN BÁSICA ===
    fecha_arqueo = models.DateField()
    sucursal = models.ForeignKey(Sucursal, on_delete=models.CASCADE, related_name='arqueos_caja')
    usuario_responsable = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='arqueos_realizados')
    
    # === TOTALES TEÓRICOS (CALCULADOS AUTOMÁTICAMENTE) ===
    # Tarjetas Comerciales (solo Hites)
    total_hites_teorico = models.IntegerField(default=0)
    total_tarjetas_comerciales_teorico = models.IntegerField(default=0)
    
    # Efectivo
    total_efectivo_teorico = models.IntegerField(default=0)
    
    # Venta Internet (Falabella, Paris, Ripley, MercadoPago, Klap)
    total_falabella_teorico = models.IntegerField(default=0)
    total_paris_teorico = models.IntegerField(default=0)
    total_ripley_teorico = models.IntegerField(default=0)
    total_mercadopago_teorico = models.IntegerField(default=0)
    total_klap_teorico = models.IntegerField(default=0)
    total_venta_internet_teorico = models.IntegerField(default=0)
    
    # Otros métodos
    total_tarjeta_debito_teorico = models.IntegerField(default=0)
    total_tarjeta_credito_teorico = models.IntegerField(default=0)
    total_transbank_teorico = models.IntegerField(default=0)
    total_transferencia_teorico = models.IntegerField(default=0)
    total_cheque_teorico = models.IntegerField(default=0)
    total_convenio_teorico = models.IntegerField(default=0)
    total_credito_trabajador_teorico = models.IntegerField(default=0)
    
    # Documentos
    total_tickets_teorico = models.IntegerField(default=0)
    total_boletas_electronicas_teorico = models.IntegerField(default=0)
    total_facturas_teorico = models.IntegerField(default=0)
    total_facturas_exentas_teorico = models.IntegerField(default=0)
    total_notas_credito_teorico = models.IntegerField(default=0)
    
    # Cantidades de documentos
    cantidad_tickets = models.IntegerField(default=0)
    cantidad_boletas_electronicas = models.IntegerField(default=0)
    cantidad_facturas = models.IntegerField(default=0)
    cantidad_facturas_exentas = models.IntegerField(default=0)
    
    # Total general
    venta_total_teorica = models.IntegerField(default=0)
    
    # === CONTEO FÍSICO (SOLO EFECTIVO) ===
    # Billetes
    billetes_20000 = models.IntegerField(default=0)
    billetes_10000 = models.IntegerField(default=0)
    billetes_5000 = models.IntegerField(default=0)
    billetes_2000 = models.IntegerField(default=0)
    billetes_1000 = models.IntegerField(default=0)
    
    # Monedas
    monedas_500 = models.IntegerField(default=0)
    monedas_100 = models.IntegerField(default=0)
    monedas_50 = models.IntegerField(default=0)
    monedas_10 = models.IntegerField(default=0)
    monedas_5 = models.IntegerField(default=0)
    monedas_1 = models.IntegerField(default=0)
    
    # Total físico calculado
    total_efectivo_fisico = models.IntegerField(default=0)
    
    # === DIFERENCIAS ===
    diferencia_efectivo = models.IntegerField(default=0)  # físico - teórico
    
    # === CIERRE POS (TRANSBANK) ===
    cierre_pos_fisico = models.IntegerField(default=0, help_text="Monto real del cierre de máquina POS")
    numero_lote_pos = models.CharField(max_length=50, blank=True, help_text="Número de lote del cierre POS")
    diferencia_transbank = models.IntegerField(default=0, help_text="Diferencia entre cierre POS físico y teórico")
    
    # === CONTROL Y ESTADO ===
    estado = models.CharField(max_length=20, choices=ESTADO_ARQUEO_CHOICES, default='ABIERTO')
    observaciones = models.TextField(blank=True, null=True)
    observaciones_diferencia = models.TextField(blank=True, null=True)
    
    # === SUPERVISIÓN ===
    supervisor_revision = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='arqueos_supervisados'
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)
    observaciones_supervisor = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_cierre = models.DateTimeField(null=True, blank=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_arqueo', '-fecha_creacion']
        unique_together = ['fecha_arqueo', 'sucursal']  # Un arqueo por día por sucursal
        verbose_name = 'Arqueo de Caja'
        verbose_name_plural = 'Arqueos de Caja'
        indexes = [
            models.Index(fields=['fecha_arqueo', 'sucursal']),
            models.Index(fields=['estado', 'fecha_arqueo']),
            models.Index(fields=['diferencia_efectivo']),
        ]
    
    def __str__(self):
        return f"Arqueo {self.fecha_arqueo} - {self.sucursal.alias} - {self.get_estado_display()}"
    
    def save(self, *args, **kwargs):
        # Calcular total físico automáticamente
        self.total_efectivo_fisico = (
            (self.billetes_20000 * 20000) +
            (self.billetes_10000 * 10000) +
            (self.billetes_5000 * 5000) +
            (self.billetes_2000 * 2000) +
            (self.billetes_1000 * 1000) +
            (self.monedas_500 * 500) +
            (self.monedas_100 * 100) +
            (self.monedas_50 * 50) +
            (self.monedas_10 * 10) +
            (self.monedas_5 * 5) +
            (self.monedas_1 * 1)
        )
        
        # Calcular diferencia
        self.diferencia_efectivo = self.total_efectivo_fisico - self.total_efectivo_teorico
        
        # Auto-determinar estado
        if self.estado == 'ABIERTO' and self.fecha_cierre:
            if self.diferencia_efectivo == 0:
                self.estado = 'CERRADO'
            else:
                self.estado = 'CON_DIFERENCIAS'
        
        super().save(*args, **kwargs)
    
    @property
    def tiene_diferencias(self):
        """Retorna True si hay diferencias en efectivo"""
        return self.diferencia_efectivo != 0
    
    @property
    def diferencia_absoluta(self):
        """Retorna el valor absoluto de la diferencia"""
        return abs(self.diferencia_efectivo)
    
    @property
    def tipo_diferencia(self):
        """Retorna si es sobrante o faltante"""
        if self.diferencia_efectivo > 0:
            return 'SOBRANTE'
        elif self.diferencia_efectivo < 0:
            return 'FALTANTE'
        else:
            return 'EXACTO'
    
    @property
    def porcentaje_diferencia(self):
        """Calcula el porcentaje de diferencia respecto al teórico"""
        if self.total_efectivo_teorico == 0:
            return 0
        return (self.diferencia_absoluta / self.total_efectivo_teorico) * 100
    
    @property
    def requiere_supervision(self):
        """Determina si requiere supervisión (diferencia > $1000 o > 1%)"""
        return self.diferencia_absoluta > 1000 or self.porcentaje_diferencia > 1.0
    
    @property
    def total_depositos(self):
        """Calcula el total de depósitos bancarios realizados"""
        return sum([d.monto for d in self.depositos.all()])
    
    @property
    def efectivo_en_caja(self):
        """Calcula el efectivo que realmente queda en caja (después de depósitos)"""
        return self.total_efectivo_fisico - self.total_depositos
    
    @property
    def diferencia_efectivo_real(self):
        """Diferencia de efectivo considerando depósitos: (Efectivo en caja - Teórico)"""
        return self.efectivo_en_caja - self.total_efectivo_teorico
    
    @property
    def diferencia_total_real(self):
        """Diferencia total considerando efectivo en caja + diferencia POS"""
        return self.diferencia_efectivo_real + self.diferencia_transbank


# ========== MODELO PARA DEPÓSITOS BANCARIOS ==========

BANCO_CHOICES = [
    ('ESTADO', 'BancoEstado'),
    ('CHILE', 'Banco de Chile'),
    ('SANTANDER', 'Santander'),
    ('BCI', 'BCI'),
    ('SCOTIABANK', 'Scotiabank'),
    ('ITAU', 'Itaú'),
    ('SECURITY', 'Banco Security'),
    ('FALABELLA', 'Banco Falabella'),
    ('RIPLEY', 'Banco Ripley'),
    ('OTRO', 'Otro'),
]

class DepositoBancario(models.Model):
    """
    Modelo simple para registrar depósitos bancarios realizados
    Relacionado con el arqueo de caja del día
    """
    # === RELACIÓN CON ARQUEO ===
    arqueo = models.ForeignKey(
        ArqueoCaja, 
        on_delete=models.CASCADE, 
        related_name='depositos',
        help_text="Arqueo de caja al que pertenece este depósito"
    )
    
    # === DATOS DEL DEPÓSITO ===
    fecha_deposito = models.DateField(
        help_text="Fecha en que se realizó el depósito bancario"
    )
    monto = models.IntegerField(
        default=0,
        help_text="Monto depositado en pesos chilenos"
    )
    banco = models.CharField(
        max_length=20, 
        choices=BANCO_CHOICES,
        default='ESTADO',
        help_text="Banco donde se realizó el depósito"
    )
    numero_comprobante = models.CharField(
        max_length=50, 
        blank=True,
        help_text="Número del comprobante bancario (opcional)"
    )
    observaciones = models.TextField(
        blank=True,
        help_text="Observaciones adicionales sobre el depósito"
    )
    
    # === METADATOS ===
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.PROTECT,
        help_text="Usuario que registró el depósito"
    )
    fecha_registro = models.DateTimeField(
        auto_now_add=True,
        help_text="Fecha y hora en que se registró el depósito"
    )
    
    class Meta:
        db_table = 'deposito_bancario'
        ordering = ['-fecha_deposito']
        verbose_name = 'Depósito Bancario'
        verbose_name_plural = 'Depósitos Bancarios'
    
    def __str__(self):
        return f"Depósito {self.fecha_deposito} - {self.get_banco_display()} - ${self.monto:,}"


# ========== MÓDULO DE CRÉDITOS A TRABAJADORES ==========

ESTADO_CREDITO_CHOICES = [
    ('PENDIENTE', 'Pendiente de Aprobación'),
    ('APROBADO', 'Aprobado'),
    ('ACTIVO', 'Activo'),
    ('PAGADO', 'Pagado Completamente'),
    ('VENCIDO', 'Vencido'),
    ('CANCELADO', 'Cancelado'),
    ('RECHAZADO', 'Rechazado'),
]

TIPO_CREDITO_CHOICES = [
    ('ANTICIPO_SUELDO', 'Anticipo de Sueldo'),
    ('PRESTAMO_EMPRESA', 'Préstamo de Empresa'),
    ('CREDITO_COMPRA', 'Crédito para Compra'),
    ('EMERGENCIA', 'Crédito de Emergencia'),
    ('OTRO', 'Otro'),
]

class CreditoTrabajador(models.Model):
    """
    Modelo para gestionar créditos otorgados a trabajadores/vendedores
    """
    # === RELACIONES ===
    trabajador = models.ForeignKey(
        Vendedor, 
        on_delete=models.CASCADE, 
        related_name='creditos_recibidos',
        help_text="Trabajador/Vendedor que recibe el crédito"
    )
    empresa_origen = models.ForeignKey(
        Empresa, 
        on_delete=models.CASCADE, 
        related_name='creditos_otorgados',
        help_text="Empresa que otorga el crédito"
    )
    sucursal = models.ForeignKey(
        Sucursal, 
        on_delete=models.CASCADE, 
        related_name='creditos_sucursal',
        help_text="Sucursal donde se otorga el crédito"
    )
    
    # === DATOS DEL CRÉDITO ===
    numero_credito = models.CharField(max_length=50, unique=True, help_text="Número único del crédito")
    tipo_credito = models.CharField(max_length=20, choices=TIPO_CREDITO_CHOICES, default='PRESTAMO_EMPRESA')
    monto_solicitado = models.DecimalField(max_digits=12, decimal_places=2, help_text="Monto solicitado")
    monto_aprobado = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Monto aprobado")
    monto_pagado = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Monto pagado hasta la fecha")
    
    # === FECHAS ===
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    fecha_vencimiento = models.DateField(help_text="Fecha límite para pago")
    fecha_primer_pago = models.DateField(null=True, blank=True, help_text="Fecha del primer pago programado")
    
    # === ESTADO Y AUTORIZACIÓN ===
    estado = models.CharField(max_length=20, choices=ESTADO_CREDITO_CHOICES, default='PENDIENTE')
    autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='creditos_autorizados',
        help_text="Usuario que autorizó el crédito"
    )
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='creditos_solicitados',
        help_text="Usuario que registró la solicitud"
    )
    
    # === CONDICIONES DEL CRÉDITO ===
    tasa_interes = models.DecimalField(
        max_digits=5, decimal_places=2, 
        default=0, 
        help_text="Tasa de interés mensual (%)"
    )
    numero_cuotas = models.IntegerField(default=1, help_text="Número de cuotas para el pago")
    valor_cuota = models.DecimalField(
        max_digits=12, decimal_places=2, 
        null=True, blank=True,
        help_text="Valor de cada cuota (calculado automáticamente)"
    )
    
    # === OBSERVACIONES Y JUSTIFICACIÓN ===
    motivo_solicitud = models.TextField(help_text="Motivo o justificación del crédito")
    observaciones_solicitud = models.TextField(blank=True, null=True)
    observaciones_aprobacion = models.TextField(blank=True, null=True)
    observaciones_rechazo = models.TextField(blank=True, null=True)
    
    # === GARANTÍAS ===
    requiere_aval = models.BooleanField(default=False)
    aval_nombre = models.CharField(max_length=200, blank=True, null=True)
    aval_rut = models.CharField(max_length=20, blank=True, null=True)
    aval_telefono = models.CharField(max_length=20, blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_solicitud']
        verbose_name = 'Crédito de Trabajador'
        verbose_name_plural = 'Créditos de Trabajadores'
        indexes = [
            models.Index(fields=['numero_credito']),
            models.Index(fields=['trabajador', 'estado']),
            models.Index(fields=['empresa_origen', 'fecha_solicitud']),
            models.Index(fields=['estado', 'fecha_vencimiento']),
        ]
    
    def __str__(self):
        return f"Crédito {self.numero_credito} - {self.trabajador.nombre} - ${self.monto_aprobado or self.monto_solicitado:,}"
    
    def save(self, *args, **kwargs):
        # Generar número de crédito si no existe
        if not self.numero_credito:
            from django.utils import timezone
            from django.db import transaction, IntegrityError
            
            max_intentos = 10
            for intento in range(max_intentos):
                try:
                    with transaction.atomic():
                        fecha = timezone.now()
                        
                        # Buscar el último crédito del año para esta empresa
                        # Usar select_for_update() para bloquear y evitar race conditions
                        ultimo_credito = CreditoTrabajador.objects.filter(
                            empresa_origen=self.empresa_origen,
                            numero_credito__startswith=f"CR-{fecha.year}"
                        ).select_for_update().order_by('-numero_credito').first()
                        
                        if ultimo_credito:
                            try:
                                # Extraer el número del último crédito (formato: CR-2025-0001)
                                ultimo_num = int(ultimo_credito.numero_credito.split('-')[-1])
                                nuevo_numero = ultimo_num + 1
                            except (ValueError, IndexError):
                                # Si hay error al parsear, buscar siguiente disponible
                                nuevo_numero = 1
                        else:
                            nuevo_numero = 1
                        
                        # Verificar que no exista (doble check)
                        while CreditoTrabajador.objects.filter(
                            numero_credito=f"CR-{fecha.year}-{nuevo_numero:04d}"
                        ).exists():
                            nuevo_numero += 1
                            if nuevo_numero > 9999:
                                raise ValueError(f"No hay números disponibles para el año {fecha.year}")
                        
                        self.numero_credito = f"CR-{fecha.year}-{nuevo_numero:04d}"
                        
                        # Calcular valor de cuota si está aprobado
                        if self.estado == 'APROBADO' and self.monto_aprobado and self.numero_cuotas > 0:
                            if self.tasa_interes > 0:
                                # Cálculo con interés compuesto
                                tasa_mensual = float(self.tasa_interes) / 100
                                factor = (1 + tasa_mensual) ** self.numero_cuotas
                                self.valor_cuota = (float(self.monto_aprobado) * tasa_mensual * factor) / (factor - 1)
                            else:
                                # Sin interés
                                self.valor_cuota = float(self.monto_aprobado) / self.numero_cuotas
                        
                        super().save(*args, **kwargs)
                        break  # Si llegó aquí, el save fue exitoso
                        
                except IntegrityError as e:
                    if 'numero_credito' in str(e) and intento < max_intentos - 1:
                        # Si el error es por número duplicado, reintentar
                        continue
                    else:
                        # Si es otro error o ya no hay más intentos, lanzar la excepción
                        raise
        else:
            # Si ya tiene numero_credito, solo calcular cuota si es necesario
            if self.estado == 'APROBADO' and self.monto_aprobado and self.numero_cuotas > 0:
                if self.tasa_interes > 0:
                    tasa_mensual = float(self.tasa_interes) / 100
                    factor = (1 + tasa_mensual) ** self.numero_cuotas
                    self.valor_cuota = (float(self.monto_aprobado) * tasa_mensual * factor) / (factor - 1)
                else:
                    self.valor_cuota = float(self.monto_aprobado) / self.numero_cuotas
            
            super().save(*args, **kwargs)
    
    @property
    def saldo_pendiente(self):
        """Saldo pendiente de pago"""
        monto_base = self.monto_aprobado or self.monto_solicitado
        return float(monto_base) - float(self.monto_pagado)
    
    @property
    def porcentaje_pagado(self):
        """Porcentaje pagado del crédito"""
        monto_base = self.monto_aprobado or self.monto_solicitado
        if monto_base > 0:
            return (float(self.monto_pagado) / float(monto_base)) * 100
        return 0
    
    @property
    def esta_vencido(self):
        """Verifica si el crédito está vencido"""
        from django.utils import timezone
        return (
            self.estado in ['ACTIVO', 'APROBADO'] and 
            self.fecha_vencimiento < timezone.now().date() and
            self.saldo_pendiente > 0
        )
    
    @property
    def dias_para_vencimiento(self):
        """Días restantes para el vencimiento"""
        from django.utils import timezone
        if self.fecha_vencimiento:
            delta = self.fecha_vencimiento - timezone.now().date()
            return delta.days
        return None
    
    def aprobar_credito(self, usuario_autorizador, monto_aprobado=None, observaciones=None):
        """Aprobar el crédito"""
        from django.utils import timezone
        
        self.estado = 'APROBADO'
        self.autorizado_por = usuario_autorizador
        self.fecha_aprobacion = timezone.now()
        self.monto_aprobado = monto_aprobado or self.monto_solicitado
        
        if observaciones:
            self.observaciones_aprobacion = observaciones
        
        self.save()
    
    def rechazar_credito(self, usuario_autorizador, motivo_rechazo):
        """Rechazar el crédito"""
        self.estado = 'RECHAZADO'
        self.autorizado_por = usuario_autorizador
        self.observaciones_rechazo = motivo_rechazo
        self.save()
    
    def activar_credito(self):
        """Activar el crédito (cuando se entrega el dinero)"""
        if self.estado == 'APROBADO':
            self.estado = 'ACTIVO'
            self.save()


class PagoCreditoTrabajador(models.Model):
    """
    Modelo para registrar pagos/abonos a créditos de trabajadores
    """
    # === RELACIONES ===
    credito = models.ForeignKey(
        CreditoTrabajador, 
        on_delete=models.CASCADE, 
        related_name='pagos'
    )
    
    # === DATOS DEL PAGO ===
    numero_pago = models.CharField(max_length=50, help_text="Número del pago/abono")
    monto_pago = models.DecimalField(max_digits=12, decimal_places=2)
    fecha_pago = models.DateField()
    metodo_pago = models.CharField(
        max_length=50, 
        choices=METODO_PAGO_TICKET_CHOICES,
        default='EFECTIVO'
    )
    
    # === DETALLES DEL PAGO ===
    numero_cuota = models.IntegerField(null=True, blank=True, help_text="Número de cuota si aplica")
    es_pago_total = models.BooleanField(default=False, help_text="Si es el pago total del crédito")
    referencia_pago = models.CharField(max_length=100, blank=True, null=True, help_text="Referencia del pago (voucher, etc.)")
    
    # === RESPONSABLES ===
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        related_name='pagos_credito_registrados'
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_pago', '-created_at']
        verbose_name = 'Pago de Crédito'
        verbose_name_plural = 'Pagos de Créditos'
        indexes = [
            models.Index(fields=['credito', 'fecha_pago']),
            models.Index(fields=['numero_pago']),
        ]
    
    def __str__(self):
        return f"Pago {self.numero_pago} - ${self.monto_pago:,} - {self.credito.numero_credito}"
    
    def save(self, *args, **kwargs):
        # Generar número de pago si no existe
        if not self.numero_pago:
            ultimo_numero = PagoCreditoTrabajador.objects.filter(
                credito=self.credito
            ).count()
            self.numero_pago = f"{self.credito.numero_credito}-P{ultimo_numero + 1:02d}"
        
        super().save(*args, **kwargs)
        
        # Actualizar monto pagado en el crédito
        total_pagado = self.credito.pagos.aggregate(
            total=models.Sum('monto_pago')
        )['total'] or 0
        
        self.credito.monto_pagado = total_pagado
        
        # Actualizar estado del crédito
        if self.credito.saldo_pendiente <= 0:
            self.credito.estado = 'PAGADO'
        elif self.credito.estado == 'APROBADO':
            self.credito.estado = 'ACTIVO'
        
        self.credito.save()


class FirmaCreditoTrabajador(models.Model):
    """
    Modelo para manejar firmas digitales de créditos
    """
    # === RELACIONES ===
    credito = models.OneToOneField(
        CreditoTrabajador, 
        on_delete=models.CASCADE, 
        related_name='firma'
    )
    
    # === DATOS DE LA FIRMA ===
    firmado_por_trabajador = models.BooleanField(default=False)
    fecha_firma_trabajador = models.DateTimeField(null=True, blank=True)
    firma_trabajador_data = models.TextField(blank=True, null=True, help_text="Datos de la firma digital del trabajador")
    
    firmado_por_autorizador = models.BooleanField(default=False)
    fecha_firma_autorizador = models.DateTimeField(null=True, blank=True)
    firma_autorizador_data = models.TextField(blank=True, null=True, help_text="Datos de la firma digital del autorizador")
    
    # === DATOS DEL AVAL (SI APLICA) ===
    firmado_por_aval = models.BooleanField(default=False)
    fecha_firma_aval = models.DateTimeField(null=True, blank=True)
    firma_aval_data = models.TextField(blank=True, null=True, help_text="Datos de la firma digital del aval")
    
    # === METADATA ===
    ip_firma_trabajador = models.GenericIPAddressField(null=True, blank=True)
    ip_firma_autorizador = models.GenericIPAddressField(null=True, blank=True)
    ip_firma_aval = models.GenericIPAddressField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Firma de Crédito'
        verbose_name_plural = 'Firmas de Créditos'
    
    def __str__(self):
        return f"Firmas - {self.credito.numero_credito}"
    
    @property
    def esta_completamente_firmado(self):
        """Verifica si todas las firmas requeridas están completas"""
        firmas_requeridas = [self.firmado_por_trabajador, self.firmado_por_autorizador]
        
        if self.credito.requiere_aval:
            firmas_requeridas.append(self.firmado_por_aval)
        
        return all(firmas_requeridas)
    
    def registrar_firma_trabajador(self, firma_data, ip_address=None):
        """Registrar firma del trabajador"""
        from django.utils import timezone
        
        self.firmado_por_trabajador = True
        self.fecha_firma_trabajador = timezone.now()
        self.firma_trabajador_data = firma_data
        self.ip_firma_trabajador = ip_address
        self.save()
    
    def registrar_firma_autorizador(self, firma_data, ip_address=None):
        """Registrar firma del autorizador"""
        from django.utils import timezone
        
        self.firmado_por_autorizador = True
        self.fecha_firma_autorizador = timezone.now()
        self.firma_autorizador_data = firma_data
        self.ip_firma_autorizador = ip_address
        self.save()
    
    def registrar_firma_aval(self, firma_data, ip_address=None):
        """Registrar firma del aval"""
        from django.utils import timezone
        
        self.firmado_por_aval = True
        self.fecha_firma_aval = timezone.now()
        self.firma_aval_data = firma_data
        self.ip_firma_aval = ip_address
        self.save()


# ========== MÓDULO POS TRANSBANK ==========

TIPO_POS_CHOICES = [
    ('VERIFONE_VX520', 'Verifone VX520'),
    ('INGENICO_3500', 'Ingenico 3500'),
    ('INGENICO_DESK', 'Ingenico DESK'),
    ('OTRO', 'Otro'),
]

ESTADO_TRANSACCION_POS_CHOICES = [
    ('INICIADA', 'Iniciada'),
    ('ESPERANDO_TARJETA', 'Esperando Tarjeta'),
    ('PROCESANDO', 'Procesando'),
    ('APROBADA', 'Aprobada'),
    ('RECHAZADA', 'Rechazada'),
    ('ANULADA', 'Anulada'),
    ('ERROR', 'Error'),
    ('TIMEOUT', 'Timeout'),
]

TIPO_TARJETA_CHOICES = [
    ('DEBITO', 'Débito'),
    ('CREDITO', 'Crédito'),
    ('PREPAGO', 'Prepago'),
    ('DESCONOCIDO', 'Desconocido'),
]

class ConfiguracionPOS(models.Model):
    """
    Configuración de terminales POS Transbank por sucursal
    """
    # === RELACIONES ===
    sucursal = models.ForeignKey(
        Sucursal, 
        on_delete=models.CASCADE, 
        related_name='configuraciones_pos'
    )
    
    # === DATOS DEL TERMINAL ===
    nombre = models.CharField(max_length=100, help_text="Nombre identificativo del terminal")
    tipo_pos = models.CharField(max_length=20, choices=TIPO_POS_CHOICES)
    puerto_conexion = models.CharField(
        max_length=20, 
        help_text="Puerto de conexión (COM1, /dev/ttyUSB0, etc.)"
    )
    velocidad_conexion = models.IntegerField(
        default=115200, 
        help_text="Velocidad de conexión en bps"
    )
    
    # === CONFIGURACIÓN ===
    activo = models.BooleanField(default=True)
    es_principal = models.BooleanField(
        default=False, 
        help_text="Terminal principal para esta sucursal"
    )
    timeout_conexion = models.IntegerField(
        default=30, 
        help_text="Timeout de conexión en segundos"
    )
    
    # === INFORMACIÓN TÉCNICA ===
    numero_serie = models.CharField(max_length=50, blank=True, null=True)
    version_firmware = models.CharField(max_length=20, blank=True, null=True)
    ultima_conexion = models.DateTimeField(null=True, blank=True)
    estado_conexion = models.CharField(
        max_length=20, 
        choices=[
            ('CONECTADO', 'Conectado'),
            ('DESCONECTADO', 'Desconectado'),
            ('DETECTADO', 'Detectado Automáticamente'),
            ('ERROR', 'Error'),
            ('NO_PROBADO', 'No Probado'),
        ],
        default='NO_PROBADO'
    )
    
    # === OBSERVACIONES ===
    observaciones = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['sucursal', 'nombre']
        unique_together = ['sucursal', 'nombre']
        verbose_name = 'Configuración POS'
        verbose_name_plural = 'Configuraciones POS'
        indexes = [
            models.Index(fields=['sucursal', 'activo']),
            models.Index(fields=['tipo_pos', 'activo']),
        ]
    
    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_pos_display()}) - {self.sucursal.alias}"
    
    def save(self, *args, **kwargs):
        # Si se marca como principal, desmarcar otros principales en la misma sucursal
        if self.es_principal:
            ConfiguracionPOS.objects.filter(
                sucursal=self.sucursal, 
                es_principal=True
            ).exclude(id=self.id).update(es_principal=False)
        
        super().save(*args, **kwargs)


class TransaccionPOS(models.Model):
    """
    Registro de transacciones POS Transbank para auditoría y seguimiento
    """
    # === RELACIONES ===
    configuracion_pos = models.ForeignKey(
        ConfiguracionPOS, 
        on_delete=models.CASCADE, 
        related_name='transacciones'
    )
    ticket = models.ForeignKey(
        Ticket, 
        on_delete=models.CASCADE, 
        related_name='transacciones_pos',
        null=True, blank=True
    )
    detalle_pago = models.ForeignKey(
        TicketDetallePago, 
        on_delete=models.CASCADE, 
        related_name='transaccion_pos',
        null=True, blank=True
    )
    
    # === DATOS DE LA TRANSACCIÓN ===
    ticket_pos = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Ticket único generado para la transacción POS"
    )
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    tipo_transaccion = models.CharField(
        max_length=20, 
        choices=[
            ('VENTA', 'Venta'),
            ('ANULACION', 'Anulación'),
            ('DEVOLUCION', 'Devolución'),
        ],
        default='VENTA'
    )
    
    # === ESTADO DE LA TRANSACCIÓN ===
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_TRANSACCION_POS_CHOICES, 
        default='INICIADA'
    )
    fecha_inicio = models.DateTimeField(auto_now_add=True)
    fecha_completada = models.DateTimeField(null=True, blank=True)
    
    # === RESPUESTA DEL POS ===
    codigo_respuesta = models.CharField(max_length=10, blank=True, null=True)
    mensaje_respuesta = models.CharField(max_length=200, blank=True, null=True)
    codigo_autorizacion = models.CharField(max_length=20, blank=True, null=True)
    
    # === DATOS DE LA TARJETA ===
    tipo_tarjeta = models.CharField(
        max_length=20, 
        choices=TIPO_TARJETA_CHOICES, 
        blank=True, null=True
    )
    ultimos_4_digitos = models.CharField(max_length=4, blank=True, null=True)
    nombre_tarjeta = models.CharField(max_length=50, blank=True, null=True)  # VISA, MASTERCARD, etc.
    
    # === DATOS TÉCNICOS ===
    numero_operacion = models.CharField(max_length=20, blank=True, null=True)
    numero_cuotas = models.IntegerField(default=1)
    codigo_comercio = models.CharField(max_length=20, blank=True, null=True)
    terminal_id = models.CharField(max_length=20, blank=True, null=True)
    
    # === DATOS DE AUDITORÍA ===
    ip_origen = models.GenericIPAddressField(null=True, blank=True)
    usuario_operador = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='transacciones_pos_operadas'
    )
    
    # === OBSERVACIONES Y ERRORES ===
    observaciones = models.TextField(blank=True, null=True)
    error_detalle = models.TextField(blank=True, null=True)
    
    # === METADATA ===
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-fecha_inicio']
        verbose_name = 'Transacción POS'
        verbose_name_plural = 'Transacciones POS'
        indexes = [
            models.Index(fields=['ticket_pos']),
            models.Index(fields=['configuracion_pos', 'fecha_inicio']),
            models.Index(fields=['estado', 'fecha_inicio']),
            models.Index(fields=['codigo_autorizacion']),
            models.Index(fields=['ticket', 'estado']),
        ]
    
    def __str__(self):
        return f"POS {self.ticket_pos} - {self.get_estado_display()} - ${self.monto:,}"
    
    def save(self, *args, **kwargs):
        # Generar ticket_pos si no existe
        if not self.ticket_pos:
            from django.utils import timezone
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            ultimo_numero = TransaccionPOS.objects.filter(
                configuracion_pos=self.configuracion_pos,
                fecha_inicio__date=timezone.now().date()
            ).count()
            self.ticket_pos = f"POS-{self.configuracion_pos.sucursal.id}-{timestamp}-{ultimo_numero + 1:03d}"
        
        # Actualizar fecha_completada si el estado cambió a completado
        if self.estado in ['APROBADA', 'RECHAZADA', 'ANULADA', 'ERROR'] and not self.fecha_completada:
            from django.utils import timezone
            self.fecha_completada = timezone.now()
        
        super().save(*args, **kwargs)
    
    @property
    def duracion_transaccion(self):
        """Duración de la transacción en segundos"""
        if self.fecha_completada:
            return (self.fecha_completada - self.fecha_inicio).total_seconds()
        return None
    
    @property
    def es_exitosa(self):
        """Verifica si la transacción fue exitosa"""
        return self.estado == 'APROBADA' and self.codigo_autorizacion
    
    @property
    def puede_anular(self):
        """Verifica si la transacción puede ser anulada"""
        return (
            self.estado == 'APROBADA' and 
            self.codigo_autorizacion and 
            self.fecha_completada and
            (timezone.now() - self.fecha_completada).days == 0  # Solo el mismo día
        )


class LogPOS(models.Model):
    """
    Log detallado de comunicación con terminales POS para debugging
    """
    # === RELACIONES ===
    configuracion_pos = models.ForeignKey(
        ConfiguracionPOS, 
        on_delete=models.CASCADE, 
        related_name='logs'
    )
    transaccion_pos = models.ForeignKey(
        TransaccionPOS, 
        on_delete=models.CASCADE, 
        related_name='logs',
        null=True, blank=True
    )
    
    # === DATOS DEL LOG ===
    tipo_evento = models.CharField(
        max_length=20, 
        choices=[
            ('CONEXION', 'Conexión'),
            ('DESCONEXION', 'Desconexión'),
            ('COMANDO_ENVIADO', 'Comando Enviado'),
            ('RESPUESTA_RECIBIDA', 'Respuesta Recibida'),
            ('ERROR', 'Error'),
            ('TIMEOUT', 'Timeout'),
            ('INFO', 'Información'),
        ]
    )
    mensaje = models.TextField()
    datos_tecnicos = models.JSONField(blank=True, null=True)  # Para datos estructurados
    
    # === METADATA ===
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Log POS'
        verbose_name_plural = 'Logs POS'
        indexes = [
            models.Index(fields=['configuracion_pos', 'timestamp']),
            models.Index(fields=['tipo_evento', 'timestamp']),
            models.Index(fields=['transaccion_pos', 'timestamp']),
        ]
    
    def __str__(self):
        return f"{self.get_tipo_evento_display()} - {self.configuracion_pos.nombre} - {self.timestamp.strftime('%H:%M:%S')}"


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
        help_text="Producto del ticket original que se cambia/devuelve"
    )
    cantidad_original = models.IntegerField(
        help_text="Cantidad del producto original a cambiar/devolver"
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
        help_text="Precio unitario original"
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
            ('RECHAZADO', 'Rechazado'),
            ('COMPLETADO', 'Completado'),
            ('CANCELADO', 'Cancelado'),
            ('MODIFICADO', 'Modificado'),
            ('PAGO_PROCESADO', 'Pago Procesado'),
            ('PRODUCTO_EVALUADO', 'Producto Evaluado'),
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


# ========== MODELOS PARA COTIZACIONES ==========

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
    fecha_facturacion = models.DateTimeField(
        blank=True, null=True,
        help_text="Fecha en que se facturó"
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
            from datetime import date
            if self.fecha_validez < date.today():
                self.estado = self.ESTADO_VENCIDA
        
        super().save(*args, **kwargs)
    
    @property
    def esta_vigente(self):
        """Verifica si la cotización está vigente"""
        from datetime import date
        return (
            self.estado == self.ESTADO_VIGENTE and 
            self.fecha_validez >= date.today() and 
            not self.facturada
        )
    
    @property
    def dias_restantes(self):
        """Calcula los días restantes de validez"""
        from datetime import date
        if self.fecha_validez:
            delta = self.fecha_validez - date.today()
            return delta.days
        return 0
    
    @property
    def porcentaje_vigencia(self):
        """Calcula el porcentaje de vigencia restante"""
        from datetime import date
        if self.dias_validez > 0:
            dias_transcurridos = (date.today() - self.fecha_emision).days
            return max(0, min(100, ((self.dias_validez - dias_transcurridos) / self.dias_validez) * 100))
        return 0
    
    def calcular_totales(self):
        """Calcula los totales de la cotización basándose en sus items"""
        items = self.items.all()
        self.subtotal = sum(item.subtotal for item in items)
        # Calcular IVA (19% en Chile)
        self.impuesto = self.subtotal * 0.19
        self.total = self.subtotal + self.impuesto - self.descuento
        self.save()
    
    def anular(self, usuario, motivo=""):
        """Anula la cotización"""
        self.estado = self.ESTADO_ANULADA
        self.anulada_por = usuario
        self.fecha_anulacion = timezone.now()
        self.motivo_anulacion = motivo
        self.save()
    
    def marcar_como_facturada(self, numero_factura):
        """Marca la cotización como facturada"""
        self.facturada = True
        self.estado = self.ESTADO_FACTURADA
        self.numero_factura = numero_factura
        self.fecha_facturacion = timezone.now()
        self.save()


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
    
    def save(self, *args, **kwargs):
        # Calcular subtotal
        subtotal_antes_descuento = self.cantidad * self.precio_unitario
        
        # Aplicar descuento
        if self.descuento_porcentaje > 0:
            self.descuento_monto = subtotal_antes_descuento * (self.descuento_porcentaje / 100)
        
        self.subtotal = subtotal_antes_descuento - self.descuento_monto
        
        # Obtener stock si hay producto existente
        if self.producto_existente and not self.es_producto_pendiente:
            # Aquí podrías calcular el stock real desde el inventario
            # Por ahora dejamos el valor que se asigne manualmente
            pass
        
        super().save(*args, **kwargs)
        
        # Recalcular totales de la cotización
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
        if self.producto_existente and self.producto_existente.producto:
            return str(self.producto_existente.producto.sku)
        return self.sku_producto_pendiente or "N/A"


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


# ========== SISTEMA DE APROBACIÓN DE CAMBIOS DE PRECIOS ==========

ESTADO_CAMBIO_PRECIO_CHOICES = [
    ('PENDIENTE', 'Pendiente de Revisión'),
    ('REVISADO', 'Revisado'),
    ('APROBADO', 'Aprobado'),
    ('RECHAZADO', 'Rechazado'),
    ('APLICADO', 'Aplicado'),
    ('CANCELADO', 'Cancelado'),
]

TIPO_CAMBIO_PRECIO_CHOICES = [
    ('INDIVIDUAL', 'Cambio Individual'),
    ('MASIVO', 'Cambio Masivo'),
    ('SINCRONIZACION', 'Sincronización Multi-Sucursal'),
    ('RECOMENDACION', 'Por Recomendación del Sistema'),
]


class CambioPrecioPendiente(models.Model):
    """
    Modelo para almacenar cambios de precios pendientes de aprobación
    Permite workflow: Proponer → Revisar → Aprobar/Rechazar → Aplicar
    """
    # === RELACIONES ===
    producto_talla = models.ForeignKey(
        Producto_Talla,
        on_delete=models.CASCADE,
        related_name='cambios_precio_pendientes'
    )
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='cambios_precio_pendientes',
        help_text="Sucursal afectada por el cambio"
    )
    
    # === DATOS DEL CAMBIO ===
    precio_anterior = models.IntegerField(
        help_text="Precio antes del cambio"
    )
    precio_nuevo = models.IntegerField(
        help_text="Precio propuesto"
    )
    diferencia = models.IntegerField(
        help_text="Diferencia en pesos (nuevo - anterior)"
    )
    porcentaje_cambio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Porcentaje de cambio"
    )
    
    # === TIPO Y ESTADO ===
    tipo_cambio = models.CharField(
        max_length=20,
        choices=TIPO_CAMBIO_PRECIO_CHOICES,
        default='INDIVIDUAL'
    )
    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CAMBIO_PRECIO_CHOICES,
        default='PENDIENTE'
    )
    
    # === JUSTIFICACIÓN ===
    motivo = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo del cambio de precio"
    )
    recomendacion_sistema = models.JSONField(
        blank=True,
        null=True,
        help_text="Datos de la recomendación del sistema (si aplica)"
    )
    
    # === USUARIOS INVOLUCRADOS ===
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='cambios_precio_creados',
        help_text="Usuario que propuso el cambio"
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cambios_precio_revisados',
        help_text="Usuario que revisó el cambio"
    )
    aprobado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cambios_precio_aprobados',
        help_text="Usuario que aprobó/rechazó el cambio"
    )
    
    # === FECHAS ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_revision = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que fue revisado"
    )
    fecha_aprobacion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que fue aprobado/rechazado"
    )
    fecha_aplicacion = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha en que se aplicó el cambio"
    )
    fecha_vencimiento = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha límite para aprobar/aplicar"
    )
    
    # === OBSERVACIONES ===
    observaciones_revision = models.TextField(
        blank=True,
        null=True,
        help_text="Observaciones al revisar"
    )
    observaciones_aprobacion = models.TextField(
        blank=True,
        null=True,
        help_text="Observaciones al aprobar/rechazar"
    )
    
    # === METADATA ===
    notificado = models.BooleanField(
        default=False,
        help_text="Si se notificó a la sucursal"
    )
    prioridad = models.CharField(
        max_length=10,
        choices=[
            ('BAJA', 'Baja'),
            ('MEDIA', 'Media'),
            ('ALTA', 'Alta'),
            ('URGENTE', 'Urgente'),
        ],
        default='MEDIA'
    )
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Cambio de Precio Pendiente'
        verbose_name_plural = 'Cambios de Precios Pendientes'
        indexes = [
            models.Index(fields=['estado', 'sucursal']),
            models.Index(fields=['fecha_creacion']),
        ]
    
    def __str__(self):
        return f"{self.producto_talla.sku} - {self.get_estado_display()} - {self.sucursal.alias}"
    
    @property
    def dias_pendiente(self):
        """Calcula cuántos días lleva pendiente"""
        if self.estado in ['APLICADO', 'CANCELADO', 'RECHAZADO']:
            return 0
        return (timezone.now() - self.fecha_creacion).days
    
    @property
    def esta_vencido(self):
        """Verifica si el cambio está vencido"""
        if not self.fecha_vencimiento:
            return False
        return timezone.now() > self.fecha_vencimiento and self.estado == 'PENDIENTE'
    
    @property
    def requiere_atencion(self):
        """Determina si requiere atención urgente"""
        return self.prioridad in ['ALTA', 'URGENTE'] or self.dias_pendiente > 7


class NotificacionCambioPrecio(models.Model):
    """
    Modelo para notificaciones de cambios de precios
    """
    cambio_precio = models.ForeignKey(
        CambioPrecioPendiente,
        on_delete=models.CASCADE,
        related_name='notificaciones'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notificaciones_precio'
    )
    
    # === DATOS DE LA NOTIFICACIÓN ===
    tipo = models.CharField(
        max_length=20,
        choices=[
            ('NUEVA', 'Nuevo Cambio Propuesto'),
            ('REVISION', 'Cambio Revisado'),
            ('APROBACION', 'Cambio Aprobado'),
            ('RECHAZO', 'Cambio Rechazado'),
            ('APLICACION', 'Cambio Aplicado'),
            ('VENCIMIENTO', 'Cambio Próximo a Vencer'),
        ]
    )
    mensaje = models.TextField()
    
    # === ESTADO ===
    leida = models.BooleanField(default=False)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_lectura = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Notificación de Cambio de Precio'
        verbose_name_plural = 'Notificaciones de Cambios de Precios'
    
    def __str__(self):
        return f"{self.usuario.username} - {self.get_tipo_display()}"
    
    def marcar_leida(self):
        """Marca la notificación como leída"""
        if not self.leida:
            self.leida = True
            self.fecha_lectura = timezone.now()
            self.save()


class HistorialCambioPrecio(models.Model):
    """
    Modelo para registrar todos los cambios de precio
    Auditoría completa de quién, cuándo y por qué cambió cada precio
    """
    # === RELACIONES ===
    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name='historial_precios'
    )
    
    # === DATOS DEL CAMBIO ===
    precio_anterior = models.IntegerField(
        help_text="Precio antes del cambio"
    )
    precio_nuevo = models.IntegerField(
        help_text="Precio después del cambio"
    )
    diferencia = models.IntegerField(
        help_text="Diferencia en pesos"
    )
    porcentaje_cambio = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Porcentaje de cambio"
    )
    
    # === CONTEXTO ===
    motivo = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo del cambio"
    )
    tipo_cambio = models.CharField(
        max_length=50,
        choices=[
            ('MANUAL', 'Cambio Manual'),
            ('RECOMENDACION', 'Por Recomendación'),
            ('MASIVO', 'Cambio Masivo'),
            ('SINCRONIZACION', 'Sincronización'),
            ('APROBACION', 'Por Aprobación'),
        ],
        default='MANUAL'
    )
    
    # === USUARIO Y FECHA ===
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='cambios_precio_realizados',
        help_text="Usuario que realizó el cambio"
    )
    fecha_cambio = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        help_text="IP desde donde se realizó el cambio"
    )
    
    # === METADATA ===
    tallas_afectadas = models.IntegerField(
        default=0,
        help_text="Cantidad de tallas actualizadas"
    )
    lotes_afectados = models.IntegerField(
        default=0,
        help_text="Cantidad de lotes actualizados"
    )
    
    class Meta:
        ordering = ['-fecha_cambio']
        verbose_name = 'Historial de Cambio de Precio'
        verbose_name_plural = 'Historial de Cambios de Precios'
        indexes = [
            models.Index(fields=['producto', '-fecha_cambio']),
            models.Index(fields=['usuario', '-fecha_cambio']),
        ]
    
    def __str__(self):
        return f"{self.producto.articulo} - {self.precio_anterior} → {self.precio_nuevo} - {self.fecha_cambio.strftime('%d/%m/%Y')}"
    
    @property
    def hace_cuanto(self):
        """Retorna hace cuánto tiempo fue el cambio"""
        from django.utils.timesince import timesince
        return timesince(self.fecha_cambio)


# ========== MÓDULO DE REQUERIMIENTOS DE GARANTÍAS ==========

TIPO_REQUERIMIENTO_CHOICES = [
    ('GARANTIA', 'Garantía'),
    ('DEVOLUCION', 'Devolución'),
    ('CAMBIO', 'Cambio de Producto'),
    ('RECLAMO', 'Reclamo'),
    ('CONSULTA', 'Consulta Técnica'),
]

ESTADO_REQUERIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('ESPERANDO_RESPUESTA', 'Esperando Respuesta Proveedor'),
    ('APROBADO', 'Aprobado por Proveedor'),
    ('RECHAZADO', 'Rechazado por Proveedor'),
    ('CANCELADO', 'Cancelado'),
]

class Requerimiento(models.Model):
    """
    Modelo para gestionar requerimientos de garantías, devoluciones y reclamos
    desde cualquier sucursal
    """
    # === INFORMACIÓN BÁSICA ===
    numero_requerimiento = models.CharField(
        max_length=50,
        unique=True,
        help_text="Número único de requerimiento (autogenerado)"
    )
    tipo = models.CharField(
        max_length=20,
        choices=TIPO_REQUERIMIENTO_CHOICES,
        default='GARANTIA'
    )
    estado = models.CharField(
        max_length=30,
        choices=ESTADO_REQUERIMIENTO_CHOICES,
        default='PENDIENTE'
    )
    
    # === SUCURSAL Y USUARIO ===
    sucursal = models.ForeignKey(
        Sucursal,
        on_delete=models.CASCADE,
        related_name='requerimientos',
        help_text="Sucursal que genera el requerimiento"
    )
    usuario_creador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='requerimientos_creados',
        help_text="Usuario que creó el requerimiento"
    )
    usuario_gestor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_gestionados',
        help_text="Usuario que gestiona/administra el requerimiento"
    )
    
    # === INFORMACIÓN DEL PRODUCTO ===
    producto_talla = models.ForeignKey(
        Producto_Talla,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos'
    )
    sku = models.CharField(
        max_length=100,
        help_text="SKU del producto"
    )
    nombre_producto = models.CharField(
        max_length=255,
        help_text="Nombre del producto"
    )
    
    # === DOCUMENTO DE VENTA ===
    numero_boleta = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Número de boleta o documento de venta"
    )
    tipo_documento = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Tipo de documento (Boleta, Factura, etc.)"
    )
    fecha_compra = models.DateField(
        blank=True,
        null=True,
        help_text="Fecha de compra del producto"
    )
    
    # === INFORMACIÓN DEL CLIENTE ===
    cliente_rut = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="RUT del cliente"
    )
    cliente_nombre = models.CharField(
        max_length=255,
        help_text="Nombre completo del cliente"
    )
    cliente_telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        help_text="Teléfono de contacto"
    )
    cliente_email = models.EmailField(
        blank=True,
        null=True,
        help_text="Email del cliente"
    )
    
    # === DESCRIPCIÓN DEL REQUERIMIENTO ===
    motivo = models.TextField(
        help_text="Descripción del motivo del requerimiento"
    )
    descripcion_problema = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción detallada del problema"
    )
    
    # === PROVEEDOR Y RESPUESTA ===
    proveedor = models.ForeignKey(
        Empresa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_como_proveedor',
        help_text="Proveedor al que se envía el requerimiento"
    )
    correo_enviado_proveedor = models.BooleanField(
        default=False,
        help_text="Indica si se envió correo al proveedor"
    )
    fecha_envio_proveedor = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha en que se envió al proveedor"
    )
    correo_proveedor_destino = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Email al que se envió el requerimiento"
    )
    intentos_envio = models.IntegerField(
        default=0,
        help_text="Número de veces que se ha enviado al proveedor"
    )
    ultimo_recordatorio = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha del último recordatorio enviado"
    )
    respuesta_proveedor = models.TextField(
        blank=True,
        null=True,
        help_text="Respuesta del proveedor"
    )
    fecha_respuesta_proveedor = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha de respuesta del proveedor"
    )
    decision_proveedor = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=[
            ('APROBADO', 'Aprobado'),
            ('RECHAZADO', 'Rechazado'),
            ('PARCIAL', 'Aprobado Parcial'),
        ],
        help_text="Decisión del proveedor"
    )
    asignado_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requerimientos_asignados',
        help_text="Usuario responsable de gestionar el requerimiento"
    )
    
    # === RESOLUCIÓN ===
    resolucion = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción de la resolución final"
    )
    motivo_resolucion = models.TextField(
        blank=True,
        null=True,
        help_text="Motivo visible al usuario de por qué se aprobó o rechazó"
    )
    fecha_resolucion = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Fecha de resolución del requerimiento"
    )
    
    # === FECHAS ===
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    
    # === PRIORIDAD ===
    prioridad = models.CharField(
        max_length=10,
        choices=[
            ('BAJA', 'Baja'),
            ('MEDIA', 'Media'),
            ('ALTA', 'Alta'),
            ('URGENTE', 'Urgente'),
        ],
        default='MEDIA'
    )
    
    class Meta:
        ordering = ['-fecha_creacion']
        verbose_name = 'Requerimiento'
        verbose_name_plural = 'Requerimientos'
        indexes = [
            models.Index(fields=['numero_requerimiento']),
            models.Index(fields=['estado', '-fecha_creacion']),
            models.Index(fields=['sucursal', '-fecha_creacion']),
            models.Index(fields=['sku']),
        ]
    
    def __str__(self):
        return f"{self.numero_requerimiento} - {self.get_tipo_display()} - {self.get_estado_display()}"
    
    def save(self, *args, **kwargs):
        """Genera número de requerimiento automáticamente"""
        if not self.numero_requerimiento:
            # Generar número correlativo: REQ-YYYYMMDD-XXXX
            from django.utils import timezone
            fecha_actual = timezone.now()
            prefijo = f"REQ-{fecha_actual.strftime('%Y%m%d')}"
            
            # Obtener el último número del día
            ultimo = Requerimiento.objects.filter(
                numero_requerimiento__startswith=prefijo
            ).order_by('-numero_requerimiento').first()
            
            if ultimo:
                # Extraer el número y sumar 1
                ultimo_num = int(ultimo.numero_requerimiento.split('-')[-1])
                nuevo_num = ultimo_num + 1
            else:
                nuevo_num = 1
            
            self.numero_requerimiento = f"{prefijo}-{nuevo_num:04d}"
        
        super().save(*args, **kwargs)
    
    @property
    def dias_transcurridos(self):
        """Calcula días desde la creación"""
        from django.utils import timezone
        delta = timezone.now() - self.fecha_creacion
        return delta.days
    
    @property
    def cantidad_fotos(self):
        """Retorna la cantidad de fotos adjuntas"""
        return self.fotos.count()
    
    @property
    def dias_sin_respuesta(self):
        """Calcula días sin respuesta del proveedor"""
        if not self.fecha_envio_proveedor:
            return 0
        if self.fecha_respuesta_proveedor:
            delta = self.fecha_respuesta_proveedor - self.fecha_envio_proveedor
        else:
            delta = timezone.now() - self.fecha_envio_proveedor
        return delta.days
    
    @property
    def requiere_recordatorio(self):
        """Indica si requiere enviar recordatorio al proveedor"""
        return (
            self.estado == 'ESPERANDO_RESPUESTA' and 
            self.dias_sin_respuesta > 7 and 
            not self.fecha_respuesta_proveedor
        )
    
    @property
    def nivel_urgencia(self):
        """Retorna nivel de urgencia según días transcurridos"""
        if self.estado in ['APROBADO', 'RECHAZADO', 'CANCELADO']:
            return 'CERRADO'
        
        dias = self.dias_transcurridos
        if self.estado == 'ESPERANDO_RESPUESTA':
            dias = self.dias_sin_respuesta
        
        if dias <= 3:
            return 'NORMAL'
        elif dias <= 7:
            return 'MEDIA'
        elif dias <= 14:
            return 'ALTA'
        else:
            return 'CRITICA'


class FotoRequerimiento(models.Model):
    """
    Modelo para almacenar fotos adjuntas a un requerimiento
    Permite hasta 5 fotos por requerimiento
    """
    requerimiento = models.ForeignKey(
        Requerimiento,
        on_delete=models.CASCADE,
        related_name='fotos'
    )
    imagen = models.ImageField(
        upload_to='requerimientos/fotos/%Y/%m/%d/',
        help_text="Foto del producto o problema"
    )
    descripcion = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Descripción de la foto"
    )
    orden = models.IntegerField(
        default=0,
        help_text="Orden de la foto (1-5)"
    )
    fecha_subida = models.DateTimeField(auto_now_add=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='fotos_requerimientos'
    )
    
    class Meta:
        ordering = ['orden', 'fecha_subida']
        verbose_name = 'Foto de Requerimiento'
        verbose_name_plural = 'Fotos de Requerimientos'
    
    def __str__(self):
        return f"Foto {self.orden} - {self.requerimiento.numero_requerimiento}"


class HistorialRequerimiento(models.Model):
    """
    Modelo para registrar el historial de cambios en un requerimiento
    """
    requerimiento = models.ForeignKey(
        Requerimiento,
        on_delete=models.CASCADE,
        related_name='historial'
    )
    accion = models.CharField(
        max_length=100,
        help_text="Tipo de acción realizada"
    )
    estado_anterior = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        help_text="Estado anterior del requerimiento"
    )
    estado_nuevo = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        help_text="Nuevo estado del requerimiento"
    )
    comentario = models.TextField(
        blank=True,
        null=True,
        help_text="Comentario o descripción de la acción"
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='acciones_requerimientos'
    )
    fecha = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Historial de Requerimiento'
        verbose_name_plural = 'Historial de Requerimientos'
    
    def __str__(self):
        return f"{self.requerimiento.numero_requerimiento} - {self.accion} - {self.fecha.strftime('%d/%m/%Y %H:%M')}"


# ========== SISTEMA DE PERMISOS POR ROL ==========

class ModuloSistema(models.Model):
    """
    Módulos principales del sistema (Dashboard, Ventas, Documentos, etc.)
    """
    codigo = models.CharField(
        max_length=50, 
        unique=True,
        help_text="Código único del módulo (ej: dashboard, ventas, compras)"
    )
    nombre = models.CharField(
        max_length=100,
        help_text="Nombre descriptivo del módulo"
    )
    descripcion = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción del módulo"
    )
    icono = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Clase CSS del ícono (ej: ri-dashboard-line)"
    )
    orden = models.IntegerField(
        default=0,
        help_text="Orden de visualización en el menú"
    )
    activo = models.BooleanField(
        default=True,
        help_text="Si el módulo está activo en el sistema"
    )
    
    class Meta:
        ordering = ['orden', 'nombre']
        verbose_name = 'Módulo del Sistema'
        verbose_name_plural = 'Módulos del Sistema'
    
    def __str__(self):
        return self.nombre


class OpcionMenu(models.Model):
    """
    Opciones individuales dentro de cada módulo
    """
    modulo = models.ForeignKey(
        ModuloSistema,
        on_delete=models.CASCADE,
        related_name='opciones'
    )
    codigo = models.CharField(
        max_length=50,
        unique=True,
        help_text="Código único de la opción (ej: dashboard_ventas, pos_dashboard)"
    )
    nombre = models.CharField(
        max_length=100,
        help_text="Nombre de la opción en el menú"
    )
    url_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="Nombre de la URL en Django (para reverse)"
    )
    url_path = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Path directo de la URL (ej: /app/pos-dashboard/)"
    )
    icono = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Clase CSS del ícono"
    )
    orden = models.IntegerField(
        default=0,
        help_text="Orden dentro del módulo"
    )
    es_submenu = models.BooleanField(
        default=False,
        help_text="Si es un submenú con opciones hijas"
    )
    padre = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='hijos',
        help_text="Opción padre si es submenú"
    )
    activo = models.BooleanField(
        default=True,
        help_text="Si la opción está activa"
    )
    
    class Meta:
        ordering = ['modulo', 'orden', 'nombre']
        verbose_name = 'Opción de Menú'
        verbose_name_plural = 'Opciones de Menú'
    
    def __str__(self):
        return f"{self.modulo.nombre} - {self.nombre}"


class PermisoRol(models.Model):
    """
    Define qué roles tienen acceso a qué opciones del menú
    """
    ROLES_CHOICES = [
        ('administrador', 'Administrador'),
        ('jefe_local', 'Jefe Local'),
        ('cajero', 'Cajero'),
        ('vendedor', 'Vendedor'),
    ]
    
    rol = models.CharField(
        max_length=50,
        choices=ROLES_CHOICES,
        help_text="Rol de usuario"
    )
    opcion_menu = models.ForeignKey(
        OpcionMenu,
        on_delete=models.CASCADE,
        related_name='permisos'
    )
    puede_ver = models.BooleanField(
        default=True,
        help_text="Puede ver la opción en el menú"
    )
    puede_crear = models.BooleanField(
        default=False,
        help_text="Puede crear nuevos registros"
    )
    puede_editar = models.BooleanField(
        default=False,
        help_text="Puede editar registros existentes"
    )
    puede_eliminar = models.BooleanField(
        default=False,
        help_text="Puede eliminar registros"
    )
    puede_exportar = models.BooleanField(
        default=False,
        help_text="Puede exportar datos"
    )
    puede_aprobar = models.BooleanField(
        default=False,
        help_text="Puede aprobar operaciones (cambios de precio, devoluciones, etc.)"
    )
    limite_descuento_porcentaje = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Límite máximo de descuento en porcentaje (0-100) que puede aplicar este rol"
    )
    
    class Meta:
        unique_together = ('rol', 'opcion_menu')
        verbose_name = 'Permiso por Rol'
        verbose_name_plural = 'Permisos por Rol'
    
    def __str__(self):
        return f"{self.get_rol_display()} - {self.opcion_menu.nombre}"
    
    @classmethod
    def tiene_permiso(cls, usuario, codigo_opcion, tipo_permiso='puede_ver'):
        """
        Verifica si un usuario tiene un permiso específico para una opción
        
        Args:
            usuario: Instancia del usuario
            codigo_opcion: Código de la opción del menú
            tipo_permiso: puede_ver, puede_crear, puede_editar, puede_eliminar, puede_exportar, puede_aprobar
        
        Returns:
            bool: True si tiene permiso, False en caso contrario
        """
        # Superusuarios siempre tienen todos los permisos
        if usuario.is_superuser:
            return True
        
        try:
            opcion = OpcionMenu.objects.get(codigo=codigo_opcion, activo=True)
            permiso = cls.objects.filter(
                rol=usuario.rol,
                opcion_menu=opcion
            ).first()
            
            if permiso:
                return getattr(permiso, tipo_permiso, False)
            
            # Si no hay permiso definido, denegar acceso
            return False
        except OpcionMenu.DoesNotExist:
            return False
    
    @classmethod
    def opciones_disponibles_para_usuario(cls, usuario):
        """
        Retorna todas las opciones del menú disponibles para un usuario
        """
        if usuario.is_superuser:
            return OpcionMenu.objects.filter(activo=True)
        
        opciones_ids = cls.objects.filter(
            rol=usuario.rol,
            puede_ver=True,
            opcion_menu__activo=True
        ).values_list('opcion_menu_id', flat=True)
        
        return OpcionMenu.objects.filter(id__in=opciones_ids, activo=True)


class ConfiguracionPermisoGlobal(models.Model):
    """
    Configuración global de permisos y restricciones del sistema
    """
    clave = models.CharField(
        max_length=100,
        unique=True,
        help_text="Clave de configuración"
    )
    valor = models.TextField(
        help_text="Valor de la configuración"
    )
    descripcion = models.TextField(
        blank=True,
        null=True,
        help_text="Descripción de la configuración"
    )
    tipo_dato = models.CharField(
        max_length=20,
        choices=[
            ('string', 'Texto'),
            ('integer', 'Número Entero'),
            ('float', 'Número Decimal'),
            ('boolean', 'Booleano'),
            ('json', 'JSON'),
        ],
        default='string'
    )
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)
    usuario_modificacion = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='configs_modificadas'
    )
    
    class Meta:
        verbose_name = 'Configuración Global de Permisos'
        verbose_name_plural = 'Configuraciones Globales de Permisos'
    
    def __str__(self):
        return f"{self.clave}: {self.valor}"
    
    def get_valor(self):
        """Retorna el valor convertido según el tipo de dato"""
        import json
        
        if self.tipo_dato == 'boolean':
            return self.valor.lower() in ('true', '1', 'yes', 'si')
        elif self.tipo_dato == 'integer':
            return int(self.valor)
        elif self.tipo_dato == 'float':
            return float(self.valor)
        elif self.tipo_dato == 'json':
            return json.loads(self.valor)
        else:
            return self.valor


# ========== SISTEMA DE CÓDIGOS DE AUTORIZACIÓN DINÁMICOS ==========

class CodigoAutorizacionDinamico(models.Model):
    """
    Códigos dinámicos que cambian cada hora para autorizar operaciones críticas.
    Solo usuarios con rol 'administrador' o 'jefe_local' pueden generar y usar estos códigos.
    """
    codigo = models.CharField(max_length=6, unique=True, db_index=True, verbose_name='Código de 6 dígitos')
    fecha_hora_inicio = models.DateTimeField(verbose_name='Fecha y hora de inicio de validez')
    fecha_hora_fin = models.DateTimeField(verbose_name='Fecha y hora de fin de validez')
    creado_en = models.DateTimeField(auto_now_add=True)
    usado = models.BooleanField(default=False, verbose_name='¿Código usado?')
    activo = models.BooleanField(default=True, verbose_name='¿Código activo?')
    
    class Meta:
        verbose_name = 'Código de Autorización Dinámico'
        verbose_name_plural = 'Códigos de Autorización Dinámicos'
        ordering = ['-fecha_hora_inicio']
        indexes = [
            models.Index(fields=['codigo', 'activo']),
            models.Index(fields=['fecha_hora_inicio', 'fecha_hora_fin']),
        ]
    
    def __str__(self):
        return f"{self.codigo} - Válido: {self.fecha_hora_inicio.strftime('%H:%M')} a {self.fecha_hora_fin.strftime('%H:%M')}"
    
    def es_valido(self):
        """Verifica si el código está dentro del rango de tiempo válido"""
        import pytz
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        return self.activo and self.fecha_hora_inicio <= ahora <= self.fecha_hora_fin
    
    @classmethod
    def obtener_codigo_actual(cls):
        """Obtiene o crea el código válido para la hora actual"""
        import pytz
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        
        # Buscar código válido existente
        codigo_valido = cls.objects.filter(
            fecha_hora_inicio__lte=ahora,
            fecha_hora_fin__gte=ahora,
            activo=True
        ).first()
        
        if codigo_valido:
            return codigo_valido
        
        # Si no existe, generar uno nuevo
        return cls.generar_codigo_horario()
    
    @classmethod
    def generar_codigo_horario(cls):
        """Genera un código único para la hora actual"""
        import hashlib
        from django.conf import settings
        import pytz
        
        # Obtener la hora actual en la zona horaria de Chile
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        
        # Redondear a la hora actual (eliminar minutos, segundos)
        hora_inicio = ahora.replace(minute=0, second=0, microsecond=0)
        hora_fin = hora_inicio + timezone.timedelta(hours=1)
        
        # Verificar si ya existe un código para esta hora
        codigo_existente = cls.objects.filter(
            fecha_hora_inicio=hora_inicio,
            fecha_hora_fin=hora_fin
        ).first()
        
        if codigo_existente:
            return codigo_existente
        
        # Generar código único usando hash
        secret_key = getattr(settings, 'SECRET_KEY', 'retailmind-secret-2024')
        cadena_base = f"{hora_inicio.isoformat()}{secret_key}retailmind"
        hash_obj = hashlib.sha256(cadena_base.encode())
        codigo_hash = hash_obj.hexdigest()
        
        # Tomar los primeros 6 dígitos del hash (convertir a número)
        codigo_numerico = ''.join(filter(str.isdigit, codigo_hash))[:6]
        
        # Si no hay suficientes dígitos, complementar con parte del hash
        if len(codigo_numerico) < 6:
            codigo_numerico = (codigo_numerico + codigo_hash.replace('a', '1').replace('b', '2').replace('c', '3').replace('d', '4').replace('e', '5').replace('f', '6'))[:6]
        
        # Crear el código
        codigo_obj = cls.objects.create(
            codigo=codigo_numerico,
            fecha_hora_inicio=hora_inicio,
            fecha_hora_fin=hora_fin,
            activo=True
        )
        
        return codigo_obj
    
    @classmethod
    def validar_codigo(cls, codigo_ingresado):
        """
        Valida si un código ingresado es correcto para la hora actual
        
        Returns:
            tuple: (es_valido: bool, mensaje: str, codigo_obj: CodigoAutorizacionDinamico or None)
        """
        if not codigo_ingresado or len(str(codigo_ingresado).strip()) != 6:
            return False, 'El código debe tener 6 dígitos', None
        
        import pytz
        codigo_ingresado = str(codigo_ingresado).strip()
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        
        # Buscar el código
        codigo_obj = cls.objects.filter(
            codigo=codigo_ingresado,
            activo=True
        ).first()
        
        if not codigo_obj:
            return False, 'Código de autorización inválido', None
        
        # Verificar si está en el rango de tiempo válido
        if not codigo_obj.es_valido():
            return False, 'Código de autorización expirado', None
        
        return True, 'Código válido', codigo_obj


class RegistroAutorizacion(models.Model):
    """
    Auditoría de todas las autorizaciones realizadas con códigos dinámicos
    """
    TIPO_OPERACION_CHOICES = [
        ('APROBACION_CAMBIO', 'Aprobación de Cambio/Devolución'),
        ('DESCUENTO_ESPECIAL', 'Descuento Especial'),
        ('ANULACION_VENTA', 'Anulación de Venta'),
        ('AJUSTE_PRECIO', 'Ajuste de Precio'),
        ('OTRO', 'Otro'),
    ]
    
    codigo_usado = models.ForeignKey(
        CodigoAutorizacionDinamico,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Código usado'
    )
    usuario_solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='autorizaciones_solicitadas',
        verbose_name='Usuario que solicitó la autorización'
    )
    tipo_operacion = models.CharField(
        max_length=50,
        choices=TIPO_OPERACION_CHOICES,
        verbose_name='Tipo de operación'
    )
    descripcion = models.TextField(
        blank=True,
        verbose_name='Descripción de la operación'
    )
    fecha_hora = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Fecha y hora de la autorización'
    )
    ip_origen = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name='IP de origen'
    )
    datos_adicionales = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Datos adicionales'
    )
    exitoso = models.BooleanField(
        default=True,
        verbose_name='¿Autorización exitosa?'
    )
    
    # Referencia a la operación autorizada (opcional)
    cambio_devolucion = models.ForeignKey(
        'CambioDevolucion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='autorizaciones'
    )
    
    class Meta:
        verbose_name = 'Registro de Autorización'
        verbose_name_plural = 'Registros de Autorizaciones'
        ordering = ['-fecha_hora']
        indexes = [
            models.Index(fields=['-fecha_hora']),
            models.Index(fields=['usuario_solicitante', '-fecha_hora']),
            models.Index(fields=['tipo_operacion', '-fecha_hora']),
        ]
    
    def __str__(self):
        return f"{self.get_tipo_operacion_display()} - {self.fecha_hora.strftime('%d/%m/%Y %H:%M')} - {self.usuario_solicitante}"

        