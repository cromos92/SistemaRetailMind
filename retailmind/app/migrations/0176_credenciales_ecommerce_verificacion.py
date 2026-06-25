from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0175_descuento_fidelizacion_cuadratura'),
    ]

    operations = [
        migrations.AddField(
            model_name='credencialesecommerce',
            name='ultima_verif_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='credencialesecommerce',
            name='ultima_verif_resultado',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='credencialesecommerce',
            name='ultima_verif_detalle',
            field=models.TextField(
                blank=True, default='',
                help_text='JSON con la muestra de URLs muertas de la última verificación.',
            ),
        ),
    ]
