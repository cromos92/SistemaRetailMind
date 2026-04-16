"""
Adds fecha_creacion and fecha_actualizacion to Producto for incremental queries
and NovedadesView filtering.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0135_producto_talla_updated_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto",
            name="fecha_creacion",
            field=models.DateTimeField(auto_now_add=True, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="producto",
            name="fecha_actualizacion",
            field=models.DateTimeField(auto_now=True, null=True, blank=True),
        ),
    ]
