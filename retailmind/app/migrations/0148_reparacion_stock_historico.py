# Generated manually on 2026-04-22
#
# Agrega el concepto `REPARACION_STOCK_HISTORICO` al campo
# `Movimientos_Producto.concepto`. Se usa en el endpoint
# `reparar_stock` y en el script `_reparar_ncs_historicas.py` para
# distinguir los movimientos de stock generados por el barrido
# retroactivo sobre NCs emitidas antes del fix unificado, de los
# ingresos naturales (compras, devoluciones en flujo normal, etc.).

from django.db import migrations, models


CONCEPTO_MOVIMIENTO_CHOICES = [
    ('INGRESO_INICIAL', 'Ingreso Inicial'),
    ('INGRESO_MANUAL', 'Ingreso Manual'),
    ('RECEPCION_COMPRA', 'Recepción de Compra'),
    ('REPOSICION_STOCK', 'Reposición de Stock'),
    ('DEVOLUCION_CLIENTE', 'Devolución de Cliente'),
    ('TRASPASO_ENTRADA', 'Traspaso Entrada'),
    ('REGULARIZACION_TRASPASO', 'Regularización de Traspaso'),
    ('AJUSTE_POSITIVO', 'Ajuste Positivo'),
    ('DONACION_RECIBIDA', 'Donación Recibida'),
    ('VENTA_PUBLICO', 'Venta al Público'),
    ('VENTA_MAYORISTA', 'Venta Mayorista'),
    ('TRASPASO_SALIDA', 'Traspaso Salida'),
    ('AJUSTE_NEGATIVO', 'Ajuste Negativo'),
    ('PERDIDA_ROBO', 'Pérdida por Robo'),
    ('PERDIDA_DETERIORO', 'Pérdida por Deterioro'),
    ('DONACION_ENTREGADA', 'Donación Entregada'),
    ('DEVOLUCION_PROVEEDOR', 'Devolución a Proveedor'),
    ('TRASPASO_SUCURSAL', 'Traspaso entre Sucursales'),
    ('TRASPASO_BODEGA', 'Traspaso a Bodega'),
    ('TRASPASO_VITRINA', 'Traspaso a Vitrina'),
    ('CAMBIO_PRODUCTO_SALIDA', 'Cambio de Producto (Salida)'),
    ('CAMBIO_PRODUCTO_ENTRADA', 'Cambio de Producto (Entrada)'),
    ('CORRECCION_STOCK', 'Corrección de Stock'),
    ('ANULACION_REGULARIZACION', 'Anulación de Regularización'),
    ('ANULACION', 'Anulación de DTE'),
    ('CANCELACION', 'Cancelación de DTE'),
    ('AJUSTE_INVENTARIO_ENTRADA', 'Ajuste Inventario - Entrada (Sobrante)'),
    ('AJUSTE_INVENTARIO_SALIDA', 'Ajuste Inventario - Salida (Faltante)'),
    ('DESPACHO_COTIZACION', 'Despacho de Cotización'),
    ('RECEPCION_SOBRANTE', 'Recepción de Sobrante (Pendiente Decisión)'),
    ('SOBRANTE_INGRESO', 'Sobrante Aceptado - Ingreso a Inventario'),
    ('SOBRANTE_DEVUELTO', 'Sobrante Devuelto a Origen'),
    ('DEVOLUCION_NC', 'Devolución por Nota de Crédito'),
    ('DEVOLUCION_NC_POST_RECEPCION', 'Devolución NC tras recepción'),
    ('REPARACION_STOCK_HISTORICO', 'Reparación Stock Histórico (NC sin movimientos)'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0147_historial_cambio_folio_dte'),
    ]

    operations = [
        migrations.AlterField(
            model_name='movimientos_producto',
            name='concepto',
            field=models.CharField(
                choices=CONCEPTO_MOVIMIENTO_CHOICES,
                default='INGRESO_INICIAL',
                max_length=50,
            ),
        ),
    ]
