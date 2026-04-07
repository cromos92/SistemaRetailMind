"""
Agrega índices de base de datos para optimizar la API externa:

  - Producto.articulo: acelera el filtro en TallasPorArticuloView
    (.filter(producto__articulo=articulo_codigo)) que sin índice
    hace un full-scan de la tabla Producto.

  - Producto_Talla.sku: acelera StockMovimientosView que agrupa
    por sku sobre potencialmente cientos de miles de filas.

Los FK (Producto_Talla.producto_id, Producto.sucursal_id, etc.) ya
tienen índice automático por ser ForeignKey en Django; no se duplican.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0108_pedido_ecommerce_rut_empresa'),
    ]

    operations = [
        migrations.AlterField(
            model_name='producto',
            name='articulo',
            field=models.CharField(max_length=200, db_index=True),
        ),
        migrations.AlterField(
            model_name='producto_talla',
            name='sku',
            field=models.BigIntegerField(db_index=True),
        ),
    ]
