"""
Migración para agregar campos de estado y soft delete al modelo Compras.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0080_allow_null_producto_original_detalle'),
    ]

    operations = [
        migrations.AddField(
            model_name='compras',
            name='estado',
            field=models.CharField(
                choices=[
                    ('ACTIVA', 'Activa'),
                    ('COMPLETADA', 'Completada'),
                    ('ELIMINADA', 'Eliminada'),
                    ('CANCELADA', 'Cancelada'),
                ],
                default='ACTIVA',
                max_length=20
            ),
        ),
        migrations.AddField(
            model_name='compras',
            name='fecha_eliminacion',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='compras',
            name='eliminado_por',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
