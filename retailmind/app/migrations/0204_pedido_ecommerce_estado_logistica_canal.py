"""
PedidoEcommerce: guarda el estado LOGÍSTICO que reporta AllConnected.

AllConnected tiene `estado` y `estado_logistica` como campos paralelos, sin
máquina de estados: un pedido despachado normalmente queda `estado='PREPARANDO'`
con `estado_logistica='ENVIADO'`. La sincronización miraba solo el primero, así
que pedidos YA enviados seguían en la cola de picking de la tienda (había uno de
44 días con tracking asignado — diagnóstico 2026-08-05).

Con este campo:
  - ENVIADO/EN_TRANSITO/ENTREGADO/COMPLETADO → el sync cierra el pedido como
    FACTURADO_EXTERNO (misma regla del 04-ago, ahora mirando ambos campos).
  - LISTO_ENVIO/LISTO_RETIRO → NO se cierra (aún no sale), pero se marca
    "Preparado en central" y sale del flujo de picking de la tienda.

Columna nueva con default no-nulo sobre una tabla chica; sin backfill (se llena
en la próxima sincronización).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0203_pedido_ecommerce_sin_stock'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoecommerce',
            name='estado_logistica_canal',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text='Último Pedido.estado_logistica reportado por AllConnected '
                          '(LISTO_ENVIO/LISTO_RETIRO = lo preparó la central).',
                verbose_name='Estado logístico en el canal',
            ),
        ),
    ]
