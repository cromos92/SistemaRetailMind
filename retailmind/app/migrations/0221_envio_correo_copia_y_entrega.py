"""Agrega a EnvioCorreo los dos campos que faltaban en la tabla real.

Por qué existe esta migración en vez de estar dentro de 0219: los campos se
sumaron al modelo DESPUÉS de que 0219 ya se había aplicado. Editar una
migración aplicada deja el estado de migraciones afirmando que las columnas
existen mientras la tabla no las tiene, y `makemigrations` no genera nada
porque compara modelos contra ESTADO, no contra la base. El síntoma es
`ProgrammingError: column app_envio_correo.es_copia_control does not exist`.

  - es_copia_control: la copia-resumen interna comparte modulo+objeto_id con el
    correo al proveedor. Sin distinguirlas, la ficha mostraba el estado de
    entrega de la copia y no el del correo que importa.
  - entregado_en: con solo `estado_en` la línea de tiempo mentiría, poniendo la
    hora del último evento en todos los pasos anteriores.

Solo agrega columnas anulables/con default: no reescribe ninguna fila.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0220_rename_app_envio_c_modulo_9c1f3d_idx_app_envio_c_modulo_822c48_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='enviocorreo',
            name='es_copia_control',
            field=models.BooleanField(
                db_index=True, default=False,
                help_text=('Copia interna de control, no el correo al destinatario '
                           'real. Se registra para detectar sus fallos, pero no es la '
                           'que cuenta para el seguimiento del caso.')),
        ),
        migrations.AddField(
            model_name='enviocorreo',
            name='entregado_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
