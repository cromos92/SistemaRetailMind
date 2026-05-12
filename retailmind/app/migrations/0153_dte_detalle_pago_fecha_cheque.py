# Generated manually on 2026-05-12
# Agrega fecha_cheque a Dte_Detalle_Pago para registrar la fecha de pago
# del cheque y recordar al usuario cuándo depositarlo.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0152_dte_limbo_nc_devolucion'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte_detalle_pago',
            name='fecha_cheque',
            field=models.DateField(
                null=True,
                blank=True,
                help_text="Fecha del cheque (para depósito). Aplica solo cuando metodo_pago='Cheque'."
            ),
        ),
    ]
