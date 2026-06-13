from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0161_dte_comprobante_enviado_a_dte_comprobante_enviado_en'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoecommerce',
            name='impuestos',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Impuestos informados por el marketplace; trazabilidad, no base de recalculo.',
                max_digits=12,
            ),
        ),
        migrations.AddConstraint(
            model_name='pedidoecommerce',
            constraint=models.UniqueConstraint(
                fields=('canal_origen', 'numero_pedido_canal'),
                name='uniq_pedido_ecom_canal_numero',
            ),
        ),
    ]
