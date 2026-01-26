# Generated migration for foto_perfil, SesionActiva and TokenResetPassword

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_alter_usuario_rol'),
    ]

    operations = [
        # Agregar campo foto_perfil al modelo Usuario
        migrations.AddField(
            model_name='usuario',
            name='foto_perfil',
            field=models.ImageField(
                blank=True, 
                help_text='Imagen de perfil del usuario', 
                null=True, 
                upload_to='usuarios/fotos_perfil/', 
                verbose_name='Foto de Perfil'
            ),
        ),
        
        # Crear modelo SesionActiva
        migrations.CreateModel(
            name='SesionActiva',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(max_length=40, unique=True, verbose_name='Clave de Sesión')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='Dirección IP')),
                ('user_agent', models.TextField(blank=True, null=True, verbose_name='User Agent')),
                ('dispositivo', models.CharField(blank=True, max_length=100, null=True, verbose_name='Dispositivo')),
                ('navegador', models.CharField(blank=True, max_length=100, null=True, verbose_name='Navegador')),
                ('sistema_operativo', models.CharField(blank=True, max_length=100, null=True, verbose_name='Sistema Operativo')),
                ('ubicacion', models.CharField(blank=True, max_length=200, null=True, verbose_name='Ubicación Aproximada')),
                ('fecha_inicio', models.DateTimeField(auto_now_add=True, verbose_name='Fecha de Inicio')),
                ('ultima_actividad', models.DateTimeField(auto_now=True, verbose_name='Última Actividad')),
                ('es_actual', models.BooleanField(default=False, verbose_name='Es Sesión Actual')),
                ('activa', models.BooleanField(default=True, verbose_name='Sesión Activa')),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, 
                    related_name='sesiones_activas', 
                    to=settings.AUTH_USER_MODEL, 
                    verbose_name='Usuario'
                )),
            ],
            options={
                'verbose_name': 'Sesión Activa',
                'verbose_name_plural': 'Sesiones Activas',
                'ordering': ['-ultima_actividad'],
            },
        ),
        
        # Crear modelo TokenResetPassword
        migrations.CreateModel(
            name='TokenResetPassword',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(default=uuid.uuid4, unique=True, verbose_name='Token')),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True, verbose_name='Fecha de Creación')),
                ('fecha_expiracion', models.DateTimeField(verbose_name='Fecha de Expiración')),
                ('usado', models.BooleanField(default=False, verbose_name='Token Usado')),
                ('ip_solicitud', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP de Solicitud')),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, 
                    related_name='tokens_reset', 
                    to=settings.AUTH_USER_MODEL, 
                    verbose_name='Usuario'
                )),
            ],
            options={
                'verbose_name': 'Token de Reset de Password',
                'verbose_name_plural': 'Tokens de Reset de Password',
                'ordering': ['-fecha_creacion'],
            },
        ),
    ]
