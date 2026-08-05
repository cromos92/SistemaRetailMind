"""
PedidoEcommerce: quiebre de stock reportado por la tienda.

- Sub-estado nuevo `SIN_STOCK` (solo `choices`, no altera la columna): la tienda
  fue a buscar el producto y no estaba. El pedido sigue PENDIENTE pero sale del
  flujo de picking y se le reporta la incidencia a AllConnected, que decide
  (reasignar / sustituir / cancelar con el cliente).
- `sin_stock_motivo`: lo que declaró la tienda (para el tooltip del listado).
- `sin_stock_avisado_ac`: False = el aviso a AllConnected falló y hay que
  reintentarlo; el pedido igual quedó marcado en RM.

Las 2 columnas son nuevas con default no-nulo sobre una tabla chica
(app_pedido_ecommerce); no hay backfill de datos.
"""
from django.db import migrations, models


SUB_ESTADOS = [
    ('RECIBIDO', 'Recibido'),
    ('ASIGNADO', 'Asignado a Sucursal'),
    ('EN_PREPARACION', 'En Preparación'),
    ('LISTO_DESPACHO', 'Listo para Despacho'),
    ('SIN_STOCK', 'Sin stock en tienda'),
    ('FACTURADO_OK', 'Facturado OK'),
    ('FACTURADO_EXTERNO', 'Facturado por Concepto (externo)'),
    ('CANCELADO_CLIENTE', 'Cancelado por Cliente'),
    ('CANCELADO_SIN_STOCK', 'Cancelado por Sin Stock'),
    ('ERROR_STOCK', 'Error de Stock'),
    ('ERROR_DTE', 'Error al Generar DTE'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0202_proveedor_producto_equivalencia'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedidoecommerce',
            name='sub_estado',
            field=models.CharField(
                choices=SUB_ESTADOS, db_index=True, default='RECIBIDO',
                max_length=30, verbose_name='Sub-estado',
            ),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='sin_stock_motivo',
            field=models.CharField(
                blank=True, default='', max_length=255,
                help_text='Lo que la tienda declaró al marcar el pedido sin stock.',
                verbose_name='Motivo sin stock',
            ),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='sin_stock_avisado_ac',
            field=models.BooleanField(
                default=False,
                help_text='True si AllConnected confirmó la incidencia. False = el aviso '
                          'falló y hay que reintentarlo (el pedido igual quedó marcado acá).',
                verbose_name='Sin stock avisado a AllConnected',
            ),
        ),
    ]
