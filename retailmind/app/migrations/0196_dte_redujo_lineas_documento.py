from django.db import migrations, models


class Migration(migrations.Migration):
    """Marca en la NC/ajuste si ya descontó sus unidades del documento afectado.

    Sin este dato la pantalla de recepción no puede saber si el total del DTE
    ya viene neteado: las NC emitidas desde Gestión DTE ("por monto") no tocan
    las líneas del original, así que la recepción pedía —e ingresaba a stock—
    unidades que ya tenían nota de crédito.

    El backfill de los documentos históricos va aparte, en el comando
    `backfill_nc_redujo_lineas` (que se puede correr en seco con --dry-run).
    """

    dependencies = [
        ('app', '0195_cambiodevolucion_ajuste_diferencia'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte',
            name='redujo_lineas_documento',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Solo para NC/ajustes: True si este documento ya descontó '
                    'sus unidades de las líneas del documento afectado.'
                ),
            ),
        ),
    ]
