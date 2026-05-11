# Generated manually on 2026-05-11
#
# Soporte para el flujo "Limbo Inbox" del emisor:
#
# 1. Agrega a `Dte` los campos `requiere_devolucion_fisica` y
#    `fecha_confirmacion_devolucion`. Aplican al DTE hijo (NC/AJUSTE POST)
#    cuando el emisor optó por "devolver mercadería al origen": la NC
#    queda emitida pero los Movimientos_Producto se crean en estado
#    PENDIENTE hasta que el receptor confirme el despacho físico.
#
# 2. Agrega a `Solicitud_Regularizacion` el campo `devolver_stock` para
#    persistir la decisión del emisor cuando emite NC desde una solicitud
#    abierta.
#
# 3. Agrega dos conceptos nuevos a `Movimientos_Producto.concepto`:
#       - DEVOLUCION_NC_PENDIENTE_DESPACHO: NC con devolución física
#         pendiente; movimiento en estado PENDIENTE hasta confirmación.
#       - SOBRANTE_ABSORBIDO_ORIGEN: NC sin devolución; egreso en destino,
#         origen asume la baja.

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
    ('DEVOLUCION_NC_PENDIENTE_DESPACHO', 'Devolución NC - Pendiente despacho físico desde destino'),
    ('SOBRANTE_ABSORBIDO_ORIGEN', 'NC sin devolución - mercadería queda en destino'),
    ('REPARACION_STOCK_HISTORICO', 'Reparación Stock Histórico (NC sin movimientos)'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0151_permiso_dte_editar_vendedor'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte',
            name='requiere_devolucion_fisica',
            field=models.BooleanField(
                default=False,
                help_text='DTE hijo cuya NC requiere despacho físico desde destino para mover el stock al origen',
            ),
        ),
        migrations.AddField(
            model_name='dte',
            name='fecha_confirmacion_devolucion',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text='Fecha en que el receptor confirmó el despacho físico de la devolución',
            ),
        ),
        migrations.AddField(
            model_name='solicitud_regularizacion',
            name='devolver_stock',
            field=models.BooleanField(
                null=True,
                blank=True,
                help_text='Decisión del emisor al emitir NC: True=devolver mercadería al origen; False=queda en destino (origen asume baja); None=no aplica',
            ),
        ),
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
