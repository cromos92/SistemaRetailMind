"""Rol 'maestro' (dueño del sistema, acceso total).

Escrita a mano. Solo cambia los choices del campo: sin efecto en la base.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0008_usuario_pin_autorizacion'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usuario',
            name='rol',
            field=models.CharField(choices=[('maestro', 'Maestro'), ('administrador', 'Administrador'), ('administracion', 'Administración'), ('jefe_local', 'Jefe Local'), ('cajero', 'Cajero'), ('vendedor', 'Vendedor')], default='vendedor', max_length=50, verbose_name='Rol'),
        ),
    ]
