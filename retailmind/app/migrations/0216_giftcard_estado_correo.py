from django.db import migrations, models


class Migration(migrations.Migration):
    """Estado de ENTREGA del correo con el código de la gift card.

    `correo_enviado_en` solo dice "lo mandamos"; estos campos dicen si el
    mensaje llegó al buzón, si lo abrieron o si rebotó, según los eventos que
    reporta el proveedor de correo (webhook de MailerSend).
    """

    dependencies = [
        ('app', '0215_giftcard_seguimiento_correo'),
    ]

    operations = [
        migrations.AddField(
            model_name='giftcard',
            name='correo_estado',
            field=models.CharField(
                choices=[
                    ('SIN_ENVIAR', 'Sin enviar'),
                    ('ENVIADO', 'Enviado (en camino)'),
                    ('ENTREGADO', 'Entregado en el buzón'),
                    ('ABIERTO', 'Abierto por el destinatario'),
                    ('REBOTADO', 'Rebotado (no llegó)'),
                    ('SPAM', 'Marcado como spam'),
                    ('FALLIDO', 'Falló el envío'),
                    ('CONFIRMADO_MANUAL', 'Entrega confirmada a mano'),
                ],
                db_index=True,
                default='SIN_ENVIAR',
                help_text='¿Llegó el código al destinatario? (lo actualiza el webhook del proveedor)',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='giftcard',
            name='correo_estado_en',
            field=models.DateTimeField(
                blank=True,
                help_text='Cuándo se registró el último estado de entrega',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='giftcard',
            name='correo_estado_detalle',
            field=models.CharField(
                blank=True,
                help_text='Motivo del rebote / detalle informado por el proveedor',
                max_length=255,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='giftcard',
            name='correo_message_id',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Message-ID del último envío (para rastrearlo en el proveedor)',
                max_length=255,
                null=True,
            ),
        ),
    ]
