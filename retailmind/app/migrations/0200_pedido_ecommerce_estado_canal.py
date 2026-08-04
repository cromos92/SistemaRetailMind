"""
PedidoEcommerce: último estado conocido en el canal (AllConnected).

2 columnas nullable/default — sin backfill, no toca datos existentes:
  - estado_canal: Pedido.estado crudo de AC ('' = nunca sincronizado)
  - fecha_sync_estado_canal: cuándo se sincronizó por última vez

Las llena la sincronización de estados del botón "Traer pedidos"
(allconnected_pedidos_service.sincronizar_estados_pedidos).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0199_devolucion_garantia_rebaja_credito'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoecommerce',
            name='estado_canal',
            field=models.CharField(
                blank=True, default='', max_length=20,
                verbose_name='Estado en el canal',
                help_text='Último Pedido.estado reportado por AllConnected (sync de estados)',
            ),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='fecha_sync_estado_canal',
            field=models.DateTimeField(
                blank=True, null=True,
                verbose_name='Última sync de estado canal',
            ),
        ),
    ]
