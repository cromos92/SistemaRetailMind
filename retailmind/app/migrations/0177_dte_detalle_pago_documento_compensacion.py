import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0176_credenciales_ecommerce_verificacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte_detalle_pago',
            name='documento_compensacion',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='compensaciones_emitidas',
                to='app.dte',
                help_text=(
                    'DTE emitido (factura de venta a este proveedor) usado como instrumento de '
                    'compensación de la factura de compra. Null si es compensación mismo-proveedor '
                    'o registro manual.'
                ),
            ),
        ),
    ]
