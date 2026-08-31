"""Credenciales Mercado Pago en BD: MercadoPagoCuenta por empresa/RUT.

El access token y el webhook secret se guardan CIFRADOS en reposo (Fernet,
clave desde env MP_CRED_KEY o derivada de SECRET_KEY — ver
services/mp_credenciales.py). MercadoPagoConfig gana el FK ``cuenta`` y sus
campos token_env/webhook_secret_env pasan a ser fallback opcional (blank).
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0224_opcion_menu_dineros_mercadopago'),
    ]

    operations = [
        migrations.CreateModel(
            name='MercadoPagoCuenta',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mp_user_id', models.CharField(blank=True, help_text='user_id (collector) de la cuenta MP', max_length=30)),
                ('access_token_cifrado', models.TextField(blank=True, help_text='Access token CIFRADO — usar set_access_token()')),
                ('webhook_secret_cifrado', models.TextField(blank=True, help_text='Secret de firma de webhooks CIFRADO — usar set_webhook_secret()')),
                ('activo', models.BooleanField(default=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('empresa', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='cuenta_mercadopago', to='app.empresa')),
            ],
            options={
                'verbose_name': 'Cuenta Mercado Pago',
                'verbose_name_plural': 'Cuentas Mercado Pago',
            },
        ),
        migrations.AddField(
            model_name='mercadopagoconfig',
            name='cuenta',
            field=models.ForeignKey(blank=True, help_text='Cuenta MP a usar; si es NULL se resuelve por la empresa de la sucursal', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='configuraciones', to='app.mercadopagocuenta'),
        ),
        migrations.AlterField(
            model_name='mercadopagoconfig',
            name='token_env',
            field=models.CharField(blank=True, help_text='Fallback: nombre de la env var con el access token', max_length=100),
        ),
        migrations.AlterField(
            model_name='mercadopagoconfig',
            name='webhook_secret_env',
            field=models.CharField(blank=True, help_text='Fallback: nombre de la env var con el secret de firma', max_length=100),
        ),
    ]
