# Renombra Dte_Detalle_Pago.fecha_cheque -> fecha_pago.
# La semantica del campo pasa de "fecha en que se debe depositar el cheque"
# a "fecha en que se realizo el pago", valido para todos los metodos.
# Se permiten fechas pasadas (pagos retroactivos) y futuras (cheques a fecha).
# Los valores existentes se preservan tal cual: la operacion es un ALTER TABLE
# RENAME COLUMN, no destruye datos.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0153_dte_detalle_pago_fecha_cheque'),
    ]

    operations = [
        migrations.RenameField(
            model_name='dte_detalle_pago',
            old_name='fecha_cheque',
            new_name='fecha_pago',
        ),
        migrations.AlterField(
            model_name='dte_detalle_pago',
            name='fecha_pago',
            field=models.DateField(
                null=True,
                blank=True,
                help_text="Fecha en que se realizo el pago. Aplica a todos los metodos. Permite fechas pasadas (retroactivos) y futuras (cheques a fecha).",
            ),
        ),
    ]
