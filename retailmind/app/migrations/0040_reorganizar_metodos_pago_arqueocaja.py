# Generated manually on 2025-11-05
# Reorganización de métodos de pago en ArqueoCaja
# Tarjetas Comerciales: solo Hites
# Venta Internet: Falabella, Paris, Ripley, MercadoPago, Klap

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0039_arqueocaja_total_credito_trabajador_teorico'),
    ]

    operations = [
        # Eliminar campos antiguos de tarjetas comerciales (excepto Hites)
        migrations.RemoveField(
            model_name='arqueocaja',
            name='total_visa_mc_amex_teorico',
        ),
        migrations.RemoveField(
            model_name='arqueocaja',
            name='total_presto_teorico',
        ),
        migrations.RemoveField(
            model_name='arqueocaja',
            name='total_abcdin_teorico',
        ),
        migrations.RemoveField(
            model_name='arqueocaja',
            name='total_tricot_teorico',
        ),
        
        # Eliminar campos antiguos de venta internet
        migrations.RemoveField(
            model_name='arqueocaja',
            name='total_webpay_teorico',
        ),
        migrations.RemoveField(
            model_name='arqueocaja',
            name='total_mercadolibre_teorico',
        ),
        migrations.RemoveField(
            model_name='arqueocaja',
            name='total_transferencia_internet_teorico',
        ),
        
        # Agregar nuevo campo Klap
        migrations.AddField(
            model_name='arqueocaja',
            name='total_klap_teorico',
            field=models.IntegerField(default=0),
        ),
    ]

