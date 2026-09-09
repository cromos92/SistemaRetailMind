"""Guarda el Nº de operación NUMÉRICO de Mercado Pago junto al id de la Orders API.

`TransaccionMercadoPago.payment_id` guarda el id que devuelve la Orders API:
un ULID con prefijo PAY (``PAY01M1S0Y7D27DTM81ZE8SWMKGBC``). Ese identificador
NO aparece en el panel, la app ni los reportes de liquidación de Mercado Pago,
donde la misma operación es un número (``177422093000``) — así que el voucher
que quedaba en la venta no servía para cruzar contra la cartola.

Este campo agrega el numérico sin perder el de la Orders API. Se llena solo en
los cobros nuevos (webhook de topic=payment) y hacia atrás con
``python manage.py backfill_payment_id_mp``.

Solo agrega una columna nullable/blank con índice: no toca datos existentes.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0227_conciliacion_mp_menu_documentos'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaccionmercadopago',
            name='payment_id_mp',
            field=models.CharField(
                blank=True, db_index=True, max_length=40,
                help_text='Nº de operación de Mercado Pago (el del panel/app)',
            ),
        ),
    ]
