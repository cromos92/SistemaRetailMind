"""
La evidencia de requerimientos pasa a un storage propio (DigitalOcean Spaces).

NO mueve ni un archivo y NO cambia la columna: `storage` es configuración del
campo en Python, la BD sigue guardando la misma ruta relativa. Las fotos
existentes se siguen leyendo desde donde estén hasta que se suban con
`subir_fotos_requerimientos_spaces`.

El `storage` es un callable (`app.storage_backends.storage_evidencias`), no una
instancia: así la migración no congela credenciales ni el endpoint, y en un
entorno sin las env vars SPACES_* resuelve al storage por defecto.
"""
from django.db import migrations, models

import app.storage_backends


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0210_requerimiento_origen_cantidad_factura'),
    ]

    operations = [
        migrations.AlterField(
            model_name='fotorequerimiento',
            name='imagen',
            field=models.ImageField(
                help_text='Foto del producto o problema',
                storage=app.storage_backends.storage_evidencias,
                upload_to='requerimientos/fotos/%Y/%m/%d/',
            ),
        ),
    ]
