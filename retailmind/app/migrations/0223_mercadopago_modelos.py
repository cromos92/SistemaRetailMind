"""Modelos del cobro Mercado Pago presencial + bucket propio en ArqueoCaja.

- MercadoPagoConfig: config por sucursal (tokens SOLO por nombre de env var).
- RetiroMercadoPago: transferencias MP→banco (conciliación 1:1 por retiro).
- TransaccionMercadoPago: log/estado de cada cobro/devolución (fuente de
  verdad server-side para el guard de registrar_pagos_ticket).
- MercadoPagoWebhookEvento: event log idempotente por x-request-id.
- ArqueoCaja: total_mercadopago_pos_teorico / cierre_mp_fisico /
  diferencia_mercadopago_pos — bucket SEPARADO de total_mercadopago_teorico
  (que es marketplace/Venta Internet).
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0222_metodos_pago_mercadopago'),
    ]

    operations = [
        migrations.AddField(
            model_name='arqueocaja',
            name='total_mercadopago_pos_teorico',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='arqueocaja',
            name='cierre_mp_fisico',
            field=models.IntegerField(default=0, help_text='Monto según app/panel Mercado Pago al cierre (opcional)'),
        ),
        migrations.AddField(
            model_name='arqueocaja',
            name='diferencia_mercadopago_pos',
            field=models.IntegerField(default=0, help_text='Diferencia MP presencial: cierre digitado - teórico (0 si no se digitó cierre)'),
        ),
        migrations.CreateModel(
            name='MercadoPagoConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(default='Caja principal', max_length=100)),
                ('habilitado', models.BooleanField(default=False)),
                ('modo', models.CharField(choices=[('QR', 'QR dinámico'), ('POINT', 'Terminal Point'), ('AMBOS', 'QR y Point')], default='QR', max_length=10)),
                ('es_principal', models.BooleanField(default=True)),
                ('mp_user_id', models.CharField(blank=True, help_text='user_id (collector) de la cuenta MP', max_length=30)),
                ('external_store_id', models.CharField(blank=True, max_length=60)),
                ('store_id', models.CharField(blank=True, max_length=60)),
                ('external_pos_id', models.CharField(blank=True, max_length=60)),
                ('pos_id', models.CharField(blank=True, max_length=60)),
                ('device_id', models.CharField(blank=True, help_text='Solo Point', max_length=60)),
                ('token_env', models.CharField(help_text='Nombre de la env var con el access token', max_length=100)),
                ('webhook_secret_env', models.CharField(blank=True, help_text='Nombre de la env var con el secret de firma de webhooks', max_length=100)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('sucursal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='configuraciones_mercadopago', to='app.sucursal')),
            ],
            options={
                'verbose_name': 'Configuración Mercado Pago',
                'verbose_name_plural': 'Configuraciones Mercado Pago',
                'unique_together': {('sucursal', 'nombre')},
            },
        ),
        migrations.CreateModel(
            name='RetiroMercadoPago',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('withdrawal_id', models.CharField(max_length=60, unique=True)),
                ('fecha', models.DateField()),
                ('monto', models.IntegerField(help_text='Monto CLP transferido al banco')),
                ('estado', models.CharField(choices=[('PENDIENTE_CONCILIAR', 'Pendiente de conciliar'), ('CONCILIADO', 'Conciliado'), ('CON_DIFERENCIA', 'Con diferencia')], default='PENDIENTE_CONCILIAR', max_length=20)),
                ('visto_en_cartola', models.BooleanField(default=False)),
                ('detalle_diferencia', models.TextField(blank=True)),
                ('raw_reporte', models.JSONField(blank=True, null=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('config', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='retiros', to='app.mercadopagoconfig')),
            ],
            options={
                'verbose_name': 'Retiro Mercado Pago',
                'verbose_name_plural': 'Retiros Mercado Pago',
                'ordering': ['-fecha'],
            },
        ),
        migrations.CreateModel(
            name='MercadoPagoWebhookEvento',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('request_id', models.CharField(max_length=80, unique=True)),
                ('topic', models.CharField(blank=True, max_length=40)),
                ('data_id', models.CharField(blank=True, max_length=60)),
                ('firma_valida', models.BooleanField(default=False)),
                ('procesado', models.BooleanField(default=False)),
                ('payload', models.JSONField(blank=True, null=True)),
                ('error', models.TextField(blank=True)),
                ('recibido_en', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Evento Webhook Mercado Pago',
                'verbose_name_plural': 'Eventos Webhook Mercado Pago',
                'ordering': ['-recibido_en'],
            },
        ),
        migrations.CreateModel(
            name='TransaccionMercadoPago',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('correlativo_ticket', models.CharField(blank=True, max_length=50)),
                ('tipo', models.CharField(choices=[('VENTA', 'Venta'), ('DEVOLUCION', 'Devolución')], default='VENTA', max_length=12)),
                ('canal', models.CharField(choices=[('QR', 'QR dinámico'), ('POINT', 'Terminal Point')], default='QR', max_length=10)),
                ('external_reference', models.CharField(max_length=80, unique=True)),
                ('order_id', models.CharField(blank=True, max_length=60)),
                ('payment_id', models.CharField(blank=True, max_length=60)),
                ('monto', models.IntegerField(help_text='Monto CLP')),
                ('monto_neto', models.IntegerField(blank=True, help_text='Neto tras comisión MP', null=True)),
                ('fee_mp', models.IntegerField(blank=True, null=True)),
                ('installments', models.IntegerField(default=1)),
                ('estado', models.CharField(choices=[('CREADA', 'Creada'), ('PENDIENTE', 'Pendiente'), ('APROBADA', 'Aprobada'), ('RECHAZADA', 'Rechazada'), ('CANCELADA', 'Cancelada'), ('EXPIRADA', 'Expirada'), ('DEVUELTA', 'Devuelta'), ('CONTRACARGO', 'Contracargo'), ('ERROR', 'Error')], default='CREADA', max_length=15)),
                ('estado_detalle', models.CharField(blank=True, max_length=120)),
                ('metodo_pago_mp', models.CharField(blank=True, help_text='debit_card / credit_card / account_money…', max_length=40)),
                ('ultimos_4_digitos', models.CharField(blank=True, max_length=4)),
                ('codigo_autorizacion', models.CharField(blank=True, max_length=30)),
                ('money_release_date', models.DateTimeField(blank=True, null=True)),
                ('consumida', models.BooleanField(default=False, help_text='Ya respalda un TicketDetallePago')),
                ('raw_response', models.JSONField(blank=True, null=True)),
                ('webhook_recibido_en', models.DateTimeField(blank=True, null=True)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('actualizado_en', models.DateTimeField(auto_now=True)),
                ('config', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='transacciones', to='app.mercadopagoconfig')),
                ('detalle_pago', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transacciones_mercadopago', to='app.ticketdetallepago')),
                ('retiro', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transacciones', to='app.retiromercadopago')),
                ('sucursal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='transacciones_mercadopago', to='app.sucursal')),
                ('ticket', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transacciones_mercadopago', to='app.ticket')),
                ('transaccion_origen', models.ForeignKey(blank=True, help_text='Venta original (solo devoluciones)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='devoluciones', to='app.transaccionmercadopago')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transacciones_mercadopago', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Transacción Mercado Pago',
                'verbose_name_plural': 'Transacciones Mercado Pago',
                'ordering': ['-creado_en'],
            },
        ),
        migrations.AddIndex(
            model_name='transaccionmercadopago',
            index=models.Index(fields=['sucursal', 'correlativo_ticket'], name='mp_trx_suc_corr_idx'),
        ),
        migrations.AddIndex(
            model_name='transaccionmercadopago',
            index=models.Index(fields=['estado', 'creado_en'], name='mp_trx_estado_idx'),
        ),
    ]
