from django.db import migrations, models


class Migration(migrations.Migration):
    """Seguimiento del envío del código por correo (denormalizado en GiftCard).

    El ledger ya registra cada envío como fila ENVIO_CORREO; estos campos
    responden de un vistazo "¿a quién y cuándo se envió esta tarjeta?" en el
    listado, el detalle y el Excel, sin recorrer los movimientos.
    """

    dependencies = [
        ('app', '0214_giftcard_empresa_arqueo_giftcard_teorico'),
    ]

    operations = [
        migrations.AddField(
            model_name='giftcard',
            name='correo_enviado_a',
            field=models.CharField(
                blank=True,
                help_text='Último destinatario al que se envió el código',
                max_length=200,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='giftcard',
            name='correo_enviado_en',
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text='Fecha/hora del último envío aceptado por el servidor de correo',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='giftcard',
            name='correo_envios',
            field=models.IntegerField(
                default=0,
                help_text='Cuántas veces se envió el código (reenvíos incluidos)',
            ),
        ),
        migrations.AddField(
            model_name='giftcard',
            name='correo_message_id',
            field=models.CharField(
                blank=True,
                help_text='Message-ID del último envío (para rastrearlo en el proveedor)',
                max_length=255,
                null=True,
            ),
        ),
    ]
