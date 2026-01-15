"""
Serializers para sincronización bidireccional App Desktop
=========================================================
"""

from rest_framework import serializers
from django.utils import timezone
from django.db import transaction

from app.models import (
    Producto, Producto_Talla, Categoria, Vendedor, Sucursal,
    Ticket, Ticket_Productos, TicketDetallePago, TicketReferencia,
    METODO_PAGO_TICKET_CHOICES
)
from app.models_sync import CuadraturaCaja, MovimientoCaja


# =============================================================================
# SERIALIZERS PARA DESCARGA (Server → Desktop)
# =============================================================================

class ProductoTallaSyncSerializer(serializers.ModelSerializer):
    """
    Serializer para variantes/tallas de producto.
    """
    codigo_barra = serializers.IntegerField(source='sku')
    
    class Meta:
        model = Producto_Talla
        fields = [
            'id',
            'sku',
            'codigo_barra',
            'talla',
            'stock',
        ]


class ProductoSyncSerializer(serializers.ModelSerializer):
    """
    Serializer para productos en sincronización.
    """
    codigo = serializers.CharField(source='articulo')
    nombre = serializers.CharField(source='descripcion')
    precio = serializers.IntegerField(source='precioventa')
    precio_oferta = serializers.IntegerField(source='precioSugerido', allow_null=True)
    categoria_id = serializers.IntegerField(source='categoria.id', allow_null=True)
    categoria_nombre = serializers.CharField(source='categoria.nombre', allow_null=True)
    marca = serializers.SerializerMethodField()
    color = serializers.SerializerMethodField()
    variantes = ProductoTallaSyncSerializer(source='producto_talla', many=True, read_only=True)
    
    class Meta:
        model = Producto
        fields = [
            'id',
            'codigo',
            'nombre',
            'precio',
            'precio_oferta',
            'costo',
            'categoria_id',
            'categoria_nombre',
            'marca',
            'color',
            'tipo_talla',
            'variantes',
        ]
    
    def get_marca(self, obj):
        if obj.atributo1:
            return obj.atributo1.valor
        return None
    
    def get_color(self, obj):
        if obj.atributo2:
            return obj.atributo2.valor
        return None


class CategoriaSyncSerializer(serializers.ModelSerializer):
    """
    Serializer para categorías.
    """
    padre_id = serializers.IntegerField(source='padre.id', allow_null=True)
    
    class Meta:
        model = Categoria
        fields = [
            'id',
            'nombre',
            'padre_id',
        ]


class VendedorSyncSerializer(serializers.ModelSerializer):
    """
    Serializer para vendedores autorizados.
    """
    class Meta:
        model = Vendedor
        fields = [
            'id',
            'codigo_vendedor',
            'nombre',
            'rut',
            'correo',
            'comision',
            'activo',
        ]


class ConfiguracionSyncSerializer(serializers.Serializer):
    """
    Serializer para configuración de sucursal/sistema.
    """
    sucursal_id = serializers.IntegerField()
    sucursal_nombre = serializers.CharField()
    sucursal_direccion = serializers.CharField()
    
    empresa_nombre = serializers.CharField()
    empresa_rut = serializers.CharField()
    empresa_razon_social = serializers.CharField()
    empresa_giro = serializers.CharField()
    empresa_direccion = serializers.CharField()
    empresa_comuna = serializers.CharField()
    empresa_ciudad = serializers.CharField()
    
    # Configuración de impresora/POS
    impresora_tipo = serializers.CharField(allow_null=True, required=False)
    impresora_puerto = serializers.CharField(allow_null=True, required=False)
    pos_tipo = serializers.CharField(allow_null=True, required=False)
    pos_puerto = serializers.CharField(allow_null=True, required=False)
    
    # Configuración de documentos
    correlativo_ticket_actual = serializers.IntegerField()
    correlativo_boleta_actual = serializers.IntegerField(allow_null=True, required=False)
    
    # Políticas
    max_descuento_porcentaje = serializers.IntegerField(default=50)
    permite_venta_sin_stock = serializers.BooleanField(default=False)
    requiere_cliente_factura = serializers.BooleanField(default=True)


# =============================================================================
# SERIALIZERS PARA SUBIDA (Desktop → Server)
# =============================================================================

class TicketItemUploadSerializer(serializers.Serializer):
    """
    Serializer para items de ticket (subida).
    """
    producto_talla_id = serializers.IntegerField(
        help_text='ID del Producto_Talla'
    )
    cantidad = serializers.IntegerField(
        min_value=1,
        help_text='Cantidad vendida'
    )
    precio_unitario = serializers.IntegerField(
        help_text='Precio unitario en pesos'
    )
    precio_original = serializers.IntegerField(
        required=False,
        default=0,
        help_text='Precio original antes de descuento'
    )
    descuento_unitario = serializers.IntegerField(
        default=0,
        help_text='Descuento por unidad en pesos'
    )
    porcentaje_descuento = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text='Porcentaje de descuento aplicado'
    )
    subtotal = serializers.IntegerField(
        help_text='Subtotal de la línea'
    )


class PagoUploadSerializer(serializers.Serializer):
    """
    Serializer para pagos de ticket (subida).
    """
    TIPOS_PAGO = [choice[0] for choice in METODO_PAGO_TICKET_CHOICES]
    
    tipo = serializers.ChoiceField(
        choices=TIPOS_PAGO,
        help_text='Tipo de pago'
    )
    monto = serializers.IntegerField(
        help_text='Monto del pago'
    )
    tipo_tarjeta = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text='Tipo de tarjeta (si aplica)'
    )
    voucher = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=100,
        help_text='Número de voucher Transbank'
    )
    numero_orden_compra = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=100,
        help_text='Número de orden de compra'
    )
    notas = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text='Notas adicionales'
    )


class TicketUploadSerializer(serializers.Serializer):
    """
    Serializer para ticket completo (subida desde desktop).
    """
    local_id = serializers.UUIDField(
        help_text='UUID único generado en desktop'
    )
    created_at = serializers.DateTimeField(
        help_text='Fecha/hora de creación en desktop'
    )
    vendedor_id = serializers.IntegerField(
        help_text='ID del vendedor'
    )
    
    # Items
    items = TicketItemUploadSerializer(many=True)
    
    # Totales
    subtotal = serializers.IntegerField(
        help_text='Subtotal antes de descuentos'
    )
    descuento_total = serializers.IntegerField(
        default=0,
        help_text='Descuento total aplicado'
    )
    total = serializers.IntegerField(
        help_text='Total final'
    )
    
    # Pagos
    pagos = PagoUploadSerializer(many=True)
    
    # Tipo documento
    tipo_dte = serializers.ChoiceField(
        choices=[
            ('TICKET', 'Ticket'),
            ('BOLETA', 'Boleta Manual'),
            ('BOLETA_ELECTRONICA', 'Boleta Electrónica'),
            ('FACTURA_ELECTRONICA', 'Factura Electrónica'),
        ],
        default='TICKET'
    )
    
    # Cliente (opcional para facturas)
    cliente_nombre = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cliente_rut = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cliente_email = serializers.EmailField(required=False, allow_null=True, allow_blank=True)
    cliente_telefono = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cliente_direccion = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cliente_giro = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cliente_comuna = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    cliente_ciudad = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    
    # Metadatos
    observaciones = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    metodo_pago = serializers.CharField(default='EFECTIVO')
    
    def validate(self, data):
        """
        Validaciones del ticket.
        """
        # Validar que items no esté vacío
        if not data.get('items'):
            raise serializers.ValidationError({
                'items': 'El ticket debe tener al menos un item'
            })
        
        # Validar que pagos no esté vacío
        if not data.get('pagos'):
            raise serializers.ValidationError({
                'pagos': 'El ticket debe tener al menos un pago'
            })
        
        # Validar suma de items == subtotal
        items_total = sum(item['subtotal'] for item in data['items'])
        if items_total != data['subtotal']:
            raise serializers.ValidationError({
                'subtotal': f'Subtotal no coincide con suma de items ({items_total} vs {data["subtotal"]})'
            })
        
        # Validar suma de pagos >= total
        pagos_total = sum(pago['monto'] for pago in data['pagos'])
        if pagos_total < data['total']:
            raise serializers.ValidationError({
                'pagos': f'Suma de pagos ({pagos_total}) es menor que total ({data["total"]})'
            })
        
        # Validar vendedor existe
        try:
            vendedor = Vendedor.objects.get(id=data['vendedor_id'])
            data['vendedor'] = vendedor
        except Vendedor.DoesNotExist:
            raise serializers.ValidationError({
                'vendedor_id': 'Vendedor no encontrado'
            })
        
        # Validar productos existen
        for item in data['items']:
            try:
                producto_talla = Producto_Talla.objects.select_related('producto').get(
                    id=item['producto_talla_id']
                )
                item['producto_talla'] = producto_talla
            except Producto_Talla.DoesNotExist:
                raise serializers.ValidationError({
                    'items': f'Producto con ID {item["producto_talla_id"]} no encontrado'
                })
        
        return data


class TicketsBatchUploadSerializer(serializers.Serializer):
    """
    Serializer para subida en lote de tickets.
    """
    tickets = TicketUploadSerializer(many=True)
    
    def validate(self, data):
        if not data.get('tickets'):
            raise serializers.ValidationError({
                'tickets': 'Debe enviar al menos un ticket'
            })
        
        # Verificar que no haya local_ids duplicados
        local_ids = [t['local_id'] for t in data['tickets']]
        if len(local_ids) != len(set(local_ids)):
            raise serializers.ValidationError({
                'tickets': 'Hay local_ids duplicados en el lote'
            })
        
        return data


class TicketSyncResultSerializer(serializers.Serializer):
    """
    Serializer para resultado de sync de un ticket.
    """
    local_id = serializers.UUIDField()
    server_id = serializers.IntegerField(allow_null=True)
    correlativo = serializers.IntegerField(allow_null=True)
    success = serializers.BooleanField()
    error = serializers.CharField(allow_null=True, required=False)
    warnings = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )


# =============================================================================
# SERIALIZERS PARA CUADRATURAS Y MOVIMIENTOS DE CAJA
# =============================================================================

class MovimientoCajaUploadSerializer(serializers.Serializer):
    """
    Serializer para movimientos de caja (subida).
    """
    local_id = serializers.UUIDField()
    tipo = serializers.ChoiceField(choices=[
        'APERTURA', 'INGRESO', 'EGRESO', 'RETIRO', 'DEVOLUCION', 'CIERRE'
    ])
    monto = serializers.IntegerField()
    concepto = serializers.CharField(max_length=200)
    fecha_hora = serializers.DateTimeField()
    vendedor_id = serializers.IntegerField()
    ticket_id = serializers.IntegerField(required=False, allow_null=True)
    observaciones = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class CuadraturaUploadSerializer(serializers.Serializer):
    """
    Serializer para cuadratura de caja (subida).
    """
    local_id = serializers.UUIDField()
    
    # Apertura
    fecha_apertura = serializers.DateTimeField()
    monto_apertura = serializers.IntegerField(default=0)
    
    # Cierre (opcional si aún está abierta)
    fecha_cierre = serializers.DateTimeField(required=False, allow_null=True)
    estado = serializers.ChoiceField(
        choices=['ABIERTA', 'CERRADA', 'CUADRADA', 'DESCUADRADA'],
        default='ABIERTA'
    )
    
    # Vendedor
    vendedor_id = serializers.IntegerField()
    
    # Montos esperados
    efectivo_esperado = serializers.IntegerField(default=0)
    tarjeta_debito_esperado = serializers.IntegerField(default=0)
    tarjeta_credito_esperado = serializers.IntegerField(default=0)
    transferencia_esperado = serializers.IntegerField(default=0)
    otros_esperado = serializers.IntegerField(default=0)
    
    # Montos contados
    efectivo_contado = serializers.IntegerField(default=0)
    tarjeta_debito_contado = serializers.IntegerField(default=0)
    tarjeta_credito_contado = serializers.IntegerField(default=0)
    transferencia_contado = serializers.IntegerField(default=0)
    otros_contado = serializers.IntegerField(default=0)
    
    # Conteo documentos
    cantidad_tickets = serializers.IntegerField(default=0)
    cantidad_boletas = serializers.IntegerField(default=0)
    cantidad_facturas = serializers.IntegerField(default=0)
    
    # Movimientos
    total_ingresos = serializers.IntegerField(default=0)
    total_egresos = serializers.IntegerField(default=0)
    total_retiros = serializers.IntegerField(default=0)
    
    # Movimientos detallados
    movimientos = MovimientoCajaUploadSerializer(many=True, required=False)
    
    # Observaciones
    observaciones = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class CuadraturasBatchUploadSerializer(serializers.Serializer):
    """
    Serializer para subida en lote de cuadraturas.
    """
    cuadraturas = CuadraturaUploadSerializer(many=True)


# =============================================================================
# SERIALIZERS PARA RESPUESTAS
# =============================================================================

class ProductosSyncResponseSerializer(serializers.Serializer):
    """
    Serializer para respuesta de sync de productos.
    """
    productos = ProductoSyncSerializer(many=True)
    deleted_ids = serializers.ListField(child=serializers.IntegerField())
    sync_timestamp = serializers.DateTimeField()
    total = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    has_more = serializers.BooleanField()


class TicketsSyncResponseSerializer(serializers.Serializer):
    """
    Serializer para respuesta de sync de tickets.
    """
    success = serializers.BooleanField()
    results = TicketSyncResultSerializer(many=True)
    total_enviados = serializers.IntegerField()
    total_procesados = serializers.IntegerField()
    total_fallidos = serializers.IntegerField()
    sync_timestamp = serializers.DateTimeField()
