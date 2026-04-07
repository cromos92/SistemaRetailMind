from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0105_add_tarea_aplicacion_ajustes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PedidoEcommerce',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('numero_ticket_rm', models.CharField(
                    db_index=True, max_length=50, unique=True,
                    verbose_name='N° Ticket RM',
                    help_text='Número mostrado en la impresión del pedido externo',
                )),
                ('numero_pedido_canal', models.CharField(
                    db_index=True, max_length=255, verbose_name='N° Pedido Canal',
                )),
                ('canal_origen', models.CharField(
                    choices=[
                        ('SHOPIFY', 'Shopify'), ('PARIS', 'Paris'),
                        ('RIPLEY', 'Ripley'), ('WALMART', 'Walmart'), ('OTRO', 'Otro'),
                    ],
                    db_index=True, max_length=20, verbose_name='Canal origen',
                )),
                ('cliente_nombre', models.CharField(max_length=255, verbose_name='Cliente')),
                ('cliente_email', models.EmailField(blank=True, verbose_name='Email')),
                ('cliente_documento', models.CharField(
                    blank=True, max_length=20, verbose_name='RUT/Doc.',
                )),
                ('subtotal', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('descuento', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('costo_envio', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('total', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('items', models.JSONField(blank=True, default=list, verbose_name='Ítems')),
                ('direccion_envio', models.TextField(blank=True, verbose_name='Dirección envío')),
                ('estado', models.CharField(
                    choices=[
                        ('PENDIENTE', 'Pendiente de Facturar'), ('FACTURADO', 'Facturado'),
                        ('CANCELADO', 'Cancelado'), ('ERROR', 'Error'),
                    ],
                    db_index=True, default='PENDIENTE', max_length=20, verbose_name='Estado',
                )),
                ('notas', models.TextField(blank=True, verbose_name='Notas')),
                ('error_detalle', models.TextField(blank=True, verbose_name='Detalle error')),
                ('fecha_recepcion', models.DateTimeField(
                    db_index=True, default=django.utils.timezone.now, verbose_name='Fecha recepción',
                )),
                ('fecha_facturacion', models.DateTimeField(blank=True, null=True, verbose_name='Fecha facturación')),
                ('sucursal', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='pedidos_ecommerce',
                    to='app.sucursal',
                    verbose_name='Sucursal',
                )),
                ('ticket', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pedidos_ecommerce',
                    to='app.ticket',
                    verbose_name='Ticket generado',
                )),
                ('dte', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pedidos_ecommerce',
                    to='app.dte',
                    verbose_name='DTE emitido',
                )),
                ('recibido_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pedidos_ecommerce_recibidos',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Recibido por (API)',
                )),
                ('facturado_por', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pedidos_ecommerce_facturados',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Facturado por',
                )),
            ],
            options={
                'verbose_name': 'Pedido Ecommerce',
                'verbose_name_plural': 'Pedidos Ecommerce',
                'db_table': 'app_pedido_ecommerce',
                'ordering': ['-fecha_recepcion'],
                'indexes': [
                    models.Index(fields=['estado', 'fecha_recepcion'], name='app_pe_estado_fecha_idx'),
                    models.Index(fields=['canal_origen', 'estado'], name='app_pe_canal_estado_idx'),
                    models.Index(fields=['sucursal', 'estado'], name='app_pe_suc_estado_idx'),
                ],
            },
        ),
    ]
