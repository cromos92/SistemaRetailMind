"""
Requerimientos: origen (CLIENTE/STOCK), cantidad y respaldo de compra.

Todo lo nuevo es nullable o tiene default, así que no toca ni una fila
existente: los requerimientos ya creados quedan origen='CLIENTE' y cantidad=1,
que es exactamente lo que eran (1 unidad reclamada por un cliente).

`cliente_nombre` pasa a blank=True: sin eso no se puede registrar una garantía
por merma de bodega, donde no hay cliente final a quien pedirle el nombre.
Es un cambio de validación de Django, no de la columna (sigue NOT NULL con '').
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0209_campana_cupon_codigo_publico'),
    ]

    operations = [
        migrations.AddField(
            model_name='requerimiento',
            name='origen',
            field=models.CharField(
                choices=[
                    ('CLIENTE', 'Reclamo de un cliente'),
                    ('STOCK', 'Detectado en stock / bodega (sin cliente)'),
                ],
                default='CLIENTE',
                help_text=(
                    "CLIENTE: lo reclama un cliente final (lo ideal, hay documento y "
                    "persona detrás). STOCK: la tienda detecta la falla en mercadería "
                    "sin vender, por lo que no hay cliente al que exigirle datos."
                ),
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='cantidad',
            field=models.PositiveIntegerField(
                default=1,
                help_text='Unidades reclamadas. El proveedor necesita este dato para su NC.',
            ),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='dte_compra',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='requerimientos_garantia',
                to='app.dte',
                help_text='Factura de compra al proveedor, cuando está en el sistema',
            ),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='numero_factura_compra',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=50,
                help_text='N° de la factura de compra (permite tipearlo si no está en el sistema)',
            ),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='fecha_factura_compra',
            field=models.DateField(
                blank=True,
                null=True,
                help_text='Fecha de la factura de compra al proveedor',
            ),
        ),
        migrations.AlterField(
            model_name='requerimiento',
            name='cliente_nombre',
            field=models.CharField(
                blank=True,
                max_length=255,
                help_text="Nombre completo del cliente. Vacío solo si origen='STOCK'.",
            ),
        ),
    ]
