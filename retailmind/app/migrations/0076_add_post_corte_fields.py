from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0075_historialimpresionetiqueta_detalleimpresionetiqueta'),
    ]

    operations = [
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='stock_movimientos_post_corte',
            field=models.IntegerField(
                default=0,
                help_text='Suma neta de movimientos después de la fecha de corte',
                verbose_name='Movimientos Post Corte'
            ),
        ),
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='stock_sistema_ajustado',
            field=models.IntegerField(
                default=0,
                help_text='Stock sistema + movimientos post corte (base para comparar)',
                verbose_name='Stock Sistema Ajustado'
            ),
        ),
    ]
