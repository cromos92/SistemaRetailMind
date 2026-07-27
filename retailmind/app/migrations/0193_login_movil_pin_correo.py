"""
2º factor por correo para el login de la app móvil de staff (NEXO Staff).

- `DispositivoAutorizado.pin_verificado_en`: marca cuándo ese teléfono
  completó el PIN. NULL = todavía no. Suspender/revocar el dispositivo lo
  vuelve a dejar en NULL (ver `DispositivoAutorizado.suspender/revocar`), que
  es el mecanismo para expulsar un teléfono perdido.
- `DesafioPinMovil`: desafío de un solo uso que une usuario + device_id + PIN
  hasheado, con contador de intentos y control de reenvíos.

NO toca nada del login desktop (`/api/v1/desktop/login/`): el POS Tauri sigue
entrando exactamente igual.

Migración escrita a mano (no generada) — aplicar con `python manage.py migrate app`.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0192_devolucion_garantia_metodo_transferencia'),
    ]

    operations = [
        migrations.AddField(
            model_name='dispositivoautorizado',
            name='pin_verificado_en',
            field=models.DateTimeField(
                blank=True, null=True,
                verbose_name='PIN verificado el',
                help_text=(
                    'Momento en que este dispositivo completó el segundo factor por '
                    'correo (app móvil de staff). NULL = todavía no lo hizo.'
                ),
            ),
        ),
        migrations.CreateModel(
            name='DesafioPinMovil',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('device_id', models.UUIDField(db_index=True, verbose_name='ID del Dispositivo')),
                ('device_name', models.CharField(blank=True, default='', max_length=100)),
                ('sistema_operativo', models.CharField(blank=True, default='', max_length=100)),
                ('version_app', models.CharField(blank=True, default='', max_length=20)),
                ('token_hash', models.CharField(db_index=True, max_length=64, unique=True,
                                                verbose_name='Hash del token de desafío')),
                ('pin_hash', models.CharField(max_length=64, verbose_name='Hash del PIN')),
                ('intentos', models.IntegerField(default=0, verbose_name='Intentos de PIN')),
                ('envios', models.IntegerField(default=1, verbose_name='Correos enviados')),
                ('consumido', models.BooleanField(
                    default=False, verbose_name='Consumido',
                    help_text='Un desafío es de un solo uso: se marca al verificar OK o al agotar los intentos')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expira_en', models.DateTimeField(db_index=True, verbose_name='Expira el')),
                ('ultimo_envio_en', models.DateTimeField(verbose_name='Último envío')),
                ('consumido_en', models.DateTimeField(blank=True, null=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True, null=True)),
                ('sucursal', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='desafios_pin_movil',
                    to='app.sucursal',
                    verbose_name='Sucursal')),
                ('usuario', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='desafios_pin_movil',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Usuario')),
            ],
            options={
                'verbose_name': 'Desafío PIN Móvil',
                'verbose_name_plural': 'Desafíos PIN Móvil',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='desafiopinmovil',
            index=models.Index(fields=['token_hash'], name='app_desafio_token_h_idx'),
        ),
        migrations.AddIndex(
            model_name='desafiopinmovil',
            index=models.Index(fields=['expira_en'], name='app_desafio_expira_idx'),
        ),
        migrations.AddIndex(
            model_name='desafiopinmovil',
            index=models.Index(fields=['usuario', 'device_id'], name='app_desafio_user_dev_idx'),
        ),
    ]
