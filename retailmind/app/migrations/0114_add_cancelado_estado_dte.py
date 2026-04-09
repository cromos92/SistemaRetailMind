from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0113_add_dte_discount_fields_and_descuento_recargo'),
    ]

    operations = [
        # Add CANCELADO to Dte.estado_dte
        migrations.AlterField(
            model_name='dte',
            name='estado_dte',
            field=models.CharField(
                choices=[
                    ('EMITIDO', 'Emitido'),
                    ('ACEPTADO', 'Aceptado'),
                    ('RECEPCIONADO_COMPLETO', 'Recepcionado Completo'),
                    ('RECEPCIONADO_PARCIAL', 'Recepcionado Parcial'),
                    ('EN_REGULARIZACION', 'En Regularización'),
                    ('RECHAZADO', 'Rechazado'),
                    ('ANULADO', 'Anulado'),
                    ('CANCELADO', 'Cancelado'),
                ],
                max_length=30,
            ),
        ),
        # Add CANCELADO to Movimientos_Producto.estado
        migrations.AlterField(
            model_name='movimientos_producto',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente'),
                    ('PENDIENTE_RECEPCION', 'Pendiente de Recepción'),
                    ('APROBADO', 'Aprobado'),
                    ('RECHAZADO', 'Rechazado'),
                    ('ANULADO', 'Anulado'),
                    ('COMPLETADO', 'Completado'),
                    ('CANCELADO', 'Cancelado'),
                ],
                default='COMPLETADO',
                max_length=20,
            ),
        ),
        # Add ANULACION and CANCELACION to Movimientos_Producto.concepto
        migrations.AlterField(
            model_name='movimientos_producto',
            name='concepto',
            field=models.CharField(
                choices=[
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
                ],
                default='INGRESO_INICIAL',
                max_length=50,
            ),
        ),
    ]
