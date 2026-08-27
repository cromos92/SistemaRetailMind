"""Bitácora de correo saliente: EnvioCorreo + RespuestaCorreo.

OJO: los campos `es_copia_control` y `entregado_en` NO van acá aunque el
modelo los tenga. Se agregaron cuando esta migración ya estaba aplicada, y
meterlos aquí dejaría el estado de migraciones diciendo que las columnas
existen mientras la tabla real no las tiene. Viven en 0221.

Solo CREA dos tablas nuevas. No altera ni migra datos existentes: los campos
`correo_*` de GiftCard y los de Requerimiento siguen intactos.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import app.models.comunicaciones


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0218_opcion_menu_retiro_pedido_local'),
    ]

    operations = [
        migrations.CreateModel(
            name='EnvioCorreo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('token', models.CharField(
                    db_index=True, default=app.models.comunicaciones.nuevo_token_correo,
                    help_text='Identificador público del envío (píxel, enlace y Reply-To)',
                    max_length=32, unique=True)),
                ('modulo', models.CharField(
                    choices=[('REQUERIMIENTO', 'Requerimiento a proveedor'),
                             ('GIFTCARD', 'Gift card'),
                             ('COTIZACION', 'Cotización'),
                             ('OTP', 'Código de verificación'),
                             ('PASSWORD', 'Recuperación de contraseña'),
                             ('OTRO', 'Otro')],
                    db_index=True, default='OTRO',
                    help_text='Módulo que originó el correo', max_length=20)),
                ('objeto_id', models.IntegerField(
                    blank=True, db_index=True,
                    help_text='ID del objeto del módulo (ej. Requerimiento.id)',
                    null=True)),
                ('destinatario', models.CharField(db_index=True, max_length=200)),
                ('cc', models.CharField(blank=True, default='', max_length=500)),
                ('reply_to', models.CharField(blank=True, default='', max_length=500)),
                ('from_email', models.CharField(blank=True, default='', max_length=200)),
                ('asunto', models.CharField(blank=True, default='', max_length=300)),
                ('adjuntos', models.PositiveSmallIntegerField(
                    default=0,
                    help_text='Cantidad de archivos adjuntos que viajaron')),
                ('message_id', models.CharField(
                    blank=True, db_index=True, default='',
                    help_text='Message-ID que genera Python (cabecera del mensaje)',
                    max_length=255)),
                ('proveedor_message_id', models.CharField(
                    blank=True, db_index=True, default='',
                    help_text=("ID que devuelve el relay en el 250 final "
                               "('Message queued as ...'): es el que viaja en los webhooks"),
                    max_length=120)),
                ('enviado_en', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('estado', models.CharField(
                    choices=[('ENVIADO', 'Enviado (aceptado por el servidor)'),
                             ('ENTREGADO', 'Entregado en el buzón'),
                             ('ABIERTO', 'Abierto por el destinatario'),
                             ('CLICK', 'Hizo clic en el enlace'),
                             ('RESPONDIDO', 'Respondió'),
                             ('REBOTADO', 'Rebotado (no llegó)'),
                             ('SPAM', 'Marcado como spam'),
                             ('FALLIDO', 'Falló el envío')],
                    db_index=True, default='ENVIADO', max_length=15)),
                ('estado_en', models.DateTimeField(blank=True, null=True)),
                ('estado_detalle', models.CharField(blank=True, default='', max_length=255)),
                ('error', models.TextField(
                    blank=True, default='',
                    help_text='Detalle del fallo cuando el estado es FALLIDO')),
                ('aperturas', models.PositiveIntegerField(default=0)),
                ('abierto_en', models.DateTimeField(blank=True, null=True)),
                ('clicks', models.PositiveIntegerField(default=0)),
                ('click_en', models.DateTimeField(blank=True, null=True)),
                ('ultima_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('ultimo_user_agent', models.CharField(blank=True, default='', max_length=300)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('enviado_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='correos_enviados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Envío de correo',
                'verbose_name_plural': 'Envíos de correo',
                'db_table': 'app_envio_correo',
                'ordering': ['-creado_en'],
            },
        ),
        migrations.CreateModel(
            name='RespuestaCorreo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('remitente', models.CharField(db_index=True, max_length=200)),
                ('asunto', models.CharField(blank=True, default='', max_length=300)),
                ('cuerpo', models.TextField(blank=True, default='')),
                ('recibido_en', models.DateTimeField(db_index=True)),
                ('message_id', models.CharField(
                    blank=True, db_index=True, default='',
                    help_text='Message-ID de la respuesta (evita procesarla dos veces)',
                    max_length=255)),
                ('in_reply_to', models.CharField(blank=True, default='', max_length=255)),
                ('adjuntos', models.JSONField(
                    blank=True, default=list,
                    help_text='[{nombre, tipo, tamano}] de los archivos que traía')),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('envio', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='respuestas', to='app.enviocorreo')),
            ],
            options={
                'verbose_name': 'Respuesta de correo',
                'verbose_name_plural': 'Respuestas de correo',
                'db_table': 'app_respuesta_correo',
                'ordering': ['-recibido_en'],
            },
        ),
        migrations.AddIndex(
            model_name='enviocorreo',
            index=models.Index(fields=['modulo', 'objeto_id'],
                               name='app_envio_c_modulo_9c1f3d_idx'),
        ),
        migrations.AddIndex(
            model_name='enviocorreo',
            index=models.Index(fields=['estado', 'enviado_en'],
                               name='app_envio_c_estado_4b7a21_idx'),
        ),
        migrations.AddConstraint(
            model_name='respuestacorreo',
            constraint=models.UniqueConstraint(
                condition=models.Q(('message_id', ''), _negated=True),
                fields=('envio', 'message_id'),
                name='uniq_respuesta_por_message_id'),
        ),
    ]
