import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0213_documentocompralegacy'),
    ]

    operations = [
        # Ámbito de canje por empresa: null = válida en todas las empresas
        # (las tarjetas ya emitidas quedan globales, comportamiento histórico).
        migrations.AddField(
            model_name='giftcard',
            name='empresa',
            field=models.ForeignKey(
                blank=True,
                help_text='Ámbito de canje (null = válida en todas las empresas)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='giftcards',
                to='app.empresa',
            ),
        ),
        # Bucket Gift Card en el snapshot del arqueo (medio NO efectivo).
        migrations.AddField(
            model_name='arqueocaja',
            name='total_giftcard_teorico',
            field=models.IntegerField(default=0),
        ),
        # Nuevo tipo de movimiento ENVIO_CORREO (cambio de choices: no-op en BD).
        migrations.AlterField(
            model_name='movimientogiftcard',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('EMISION', 'Emisión'),
                    ('CARGA', 'Carga / Recarga'),
                    ('CONSUMO', 'Consumo (pago de venta)'),
                    ('ANULACION', 'Anulación'),
                    ('AJUSTE', 'Ajuste manual'),
                    ('REVERSA', 'Reversa (devolución/anulación de venta)'),
                    ('BLOQUEO', 'Bloqueo'),
                    ('DESBLOQUEO', 'Desbloqueo'),
                    ('ENVIO_CORREO', 'Envío de código por correo'),
                ],
                max_length=20,
            ),
        ),
    ]
