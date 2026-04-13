from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0127_sobrante_support'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte',
            name='tipo_precio_externo',
            field=models.CharField(
                blank=True,
                choices=[
                    ('COSTO', 'Solo Costo'),
                    ('SOBREPRECIO', 'Costo + Sobreprecio'),
                    ('CUSTOM_PCT', 'Porcentaje Custom'),
                ],
                help_text='Modo de precio usado en despacho externo',
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='dte',
            name='porcentaje_custom',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Porcentaje de margen custom sobre el costo (solo si tipo_precio_externo=CUSTOM_PCT)',
                max_digits=5,
                null=True,
            ),
        ),
    ]
