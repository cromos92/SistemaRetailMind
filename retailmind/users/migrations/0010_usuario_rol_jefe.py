"""Rol 'jefe' (como Administrador, con menos permisos configurables).

Escrita a mano. Solo cambia los choices del campo: sin efecto en la base.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_usuario_rol_maestro'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usuario',
            name='rol',
            field=models.CharField(choices=[('maestro', 'Maestro'), ('administrador', 'Administrador'), ('jefe', 'Jefe'), ('administracion', 'Administración'), ('jefe_local', 'Jefe Local'), ('cajero', 'Cajero'), ('vendedor', 'Vendedor')], default='vendedor', max_length=50, verbose_name='Rol'),
        ),
    ]
