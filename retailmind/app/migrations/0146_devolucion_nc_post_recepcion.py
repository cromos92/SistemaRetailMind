# Generated manually on 2026-04-22
#
# Agrega el concepto `DEVOLUCION_NC_POST_RECEPCION` al campo
# `Movimientos_Producto.concepto`. Se usa en el endpoint unificado
# `ajustar_traspaso_api` para distinguir los movimientos de stock
# generados cuando el emisor emite NC/Ajuste sobre un traspaso que el
# destino ya recepcionó, de los generados antes de la recepción
# (`DEVOLUCION_NC`).

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
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0145_permiso_descargar_txt_dte'),
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
