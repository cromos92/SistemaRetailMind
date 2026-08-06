"""Identificación del cliente por pasaporte además del RUT.

Un extranjero sin cédula chilena no tenía cómo quedar identificado en la ficha
(el único campo era ``rut``, con validador de RUT chileno), y por eso no se le
podía crear un crédito con un documento verificable. Se agregan:

- ``tipo_documento``: RUT (default, lo que ya existía) o PASAPORTE.
- ``pasaporte`` + ``pais_documento``: sólo se usan cuando el tipo es PASAPORTE.

Todas las fichas existentes quedan en ``tipo_documento='RUT'``, así que ningún
flujo que hoy lee ``cliente.rut`` cambia de comportamiento.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0206_permiso_fidelizacion_cupones'),
    ]

    operations = [
        migrations.AddField(
            model_name='cliente',
            name='tipo_documento',
            field=models.CharField(
                choices=[('RUT', 'RUT'), ('PASAPORTE', 'Pasaporte')],
                default='RUT',
                help_text='Documento con el que se identifica al cliente',
                max_length=15,
                verbose_name='Tipo de Documento',
            ),
        ),
        migrations.AddField(
            model_name='cliente',
            name='pasaporte',
            field=models.CharField(
                blank=True,
                help_text='Número de pasaporte para clientes extranjeros sin RUT chileno',
                max_length=30,
                null=True,
                verbose_name='Pasaporte',
            ),
        ),
        migrations.AddField(
            model_name='cliente',
            name='pais_documento',
            field=models.CharField(
                blank=True,
                max_length=60,
                null=True,
                verbose_name='País del Documento',
            ),
        ),
        migrations.AddIndex(
            model_name='cliente',
            index=models.Index(fields=['pasaporte'], name='app_cliente_pasapo_idx'),
        ),
    ]
