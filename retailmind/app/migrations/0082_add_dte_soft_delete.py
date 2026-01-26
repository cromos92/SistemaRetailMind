"""
Migración para agregar campos de soft delete al modelo Dte.
Permite descartar DTEs sin eliminarlos de la base de datos.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0081_add_compras_estado_soft_delete'),
    ]

    operations = [
        migrations.AddField(
            model_name='dte',
            name='descartado',
            field=models.BooleanField(
                default=False,
                help_text='DTE marcado como descartado (no se muestra en listados pero permanece en DB)'
            ),
        ),
        migrations.AddField(
            model_name='dte',
            name='fecha_descarte',
            field=models.DateTimeField(
                blank=True, 
                null=True,
                help_text='Fecha en que se descartó el DTE'
            ),
        ),
        migrations.AddField(
            model_name='dte',
            name='descartado_por',
            field=models.CharField(
                blank=True, 
                max_length=100, 
                null=True,
                help_text='Usuario que descartó el DTE'
            ),
        ),
        migrations.AddField(
            model_name='dte',
            name='motivo_descarte',
            field=models.CharField(
                blank=True, 
                max_length=200, 
                null=True,
                help_text='Motivo por el cual se descartó'
            ),
        ),
    ]
