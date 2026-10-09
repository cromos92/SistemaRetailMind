"""Toma de inventario: «Ajustar stock ya» con la toma abierta.

El Maestro puede llevar al stock lo ya contado sin cerrar la toma (los ecommerce
venden ese stock) y la tienda sigue contando y recontando. Para saber qué falta
mover, cada línea guarda lo que ya movió:

- TomaInventarioDetalle.diferencia_aplicada (NULL = línea anterior a este campo:
  vale `diferencia` si ajuste_aplicado y 0 si no, así que no hay que rellenar
  las tomas viejas).
- TomaInventarioLog.tipo_accion suma 'AJUSTE_ANTICIPADO' (solo choices).

Escrita a mano. Segura en caliente y en cualquier orden respecto del deploy:
la columna es NULL, así que el código anterior sigue insertando líneas sin ella.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0244_jefe_local_revisa_tomas'),
    ]

    operations = [
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='diferencia_aplicada',
            field=models.IntegerField(
                blank=True, null=True, verbose_name='Diferencia ya aplicada al stock',
                help_text='Suma de lo que los ajustes de esta línea ya movieron en el stock',
            ),
        ),
        migrations.AlterField(
            model_name='tomainventariolog',
            name='tipo_accion',
            field=models.CharField(choices=[
                ('CREACION', 'Creación'),
                ('INICIO_CONTEO', 'Inicio de Conteo'),
                ('REGISTRO_CONTEO', 'Registro de Conteo'),
                ('RECONTEO', 'Reconteo'),
                ('CAMBIO_ESTADO', 'Cambio de Estado'),
                ('ENVIO_APROBACION', 'Envío a Aprobación'),
                ('APROBACION', 'Aprobación'),
                ('RECHAZO', 'Rechazo'),
                ('APLICACION_AJUSTES', 'Aplicación de Ajustes'),
                ('AJUSTE_ANTICIPADO', 'Ajuste de stock con la toma abierta'),
                ('CANCELACION', 'Cancelación'),
                ('MODIFICACION', 'Modificación'),
            ], max_length=25),
        ),
    ]
