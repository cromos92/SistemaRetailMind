"""
Adds updated_at to Producto_Talla for incremental stock queries.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0134_codigo_autorizacion_por_usuario"),
    ]

    operations = [
        migrations.AddField(
            model_name="producto_talla",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, null=True, blank=True),
        ),
    ]
