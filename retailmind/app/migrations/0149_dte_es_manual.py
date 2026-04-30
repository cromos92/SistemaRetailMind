# Generated manually on 2026-04-30
#
# Agrega el campo booleano `es_manual` al modelo `Dte` para distinguir
# los DTEs creados manualmente desde Gestión de Documentos (sin
# productos, solo cabecera + un Dte_Detalle_Pago) de los DTEs
# normales emitidos por el sistema. Se usa para mostrar un badge
# "MANUAL" en el listado y, eventualmente, para excluirlos de
# reportes específicos. No afecta a los filtros existentes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0148_reparacion_stock_historico'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte',
            name='es_manual',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'DTE creado manualmente desde Gestión de Documentos '
                    '(sin productos, solo cabecera + pago)'
                ),
            ),
        ),
    ]
