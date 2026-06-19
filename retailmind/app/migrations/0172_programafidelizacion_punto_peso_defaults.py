# Generated manually for the "1 punto = $1" loyalty strategy.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0171_canjevale'),
    ]

    operations = [
        migrations.AlterField(
            model_name='programafidelizacion',
            name='puntos_por_monto',
            field=models.IntegerField(
                default=10,
                help_text='Puntos otorgados por cada `monto_base_acumulacion` pesos',
            ),
        ),
        migrations.AlterField(
            model_name='programafidelizacion',
            name='valor_punto_en_pesos',
            field=models.IntegerField(
                default=1,
                help_text='Cuántos pesos de descuento vale 1 punto al canjear',
            ),
        ),
        migrations.AlterField(
            model_name='programafidelizacion',
            name='minimo_canje_puntos',
            field=models.IntegerField(
                default=500,
                help_text='Puntos mínimos para poder canjear',
            ),
        ),
        migrations.AlterField(
            model_name='programafidelizacion',
            name='puntos_bienvenida',
            field=models.IntegerField(
                default=200,
                help_text='Puntos al crear la cuenta del cliente (0 = sin bono)',
            ),
        ),
    ]
