"""
PedidoEcommerce: sub-estado FACTURADO_EXTERNO (facturado por concepto).

Solo cambia `choices` (validación Django) — no altera la columna ni los datos.
Lo usa el sync de estados: un PENDIENTE que el canal reporta ENVIADO/ENTREGADO
se cierra como FACTURADO/FACTURADO_EXTERNO (la venta ya se documentó por
concepto fuera del módulo — decisión de negocio 2026-08-04).
"""
from django.db import migrations, models


SUB_ESTADOS = [
    ('RECIBIDO', 'Recibido'),
    ('ASIGNADO', 'Asignado a Sucursal'),
    ('EN_PREPARACION', 'En Preparación'),
    ('LISTO_DESPACHO', 'Listo para Despacho'),
    ('FACTURADO_OK', 'Facturado OK'),
    ('FACTURADO_EXTERNO', 'Facturado por Concepto (externo)'),
    ('CANCELADO_CLIENTE', 'Cancelado por Cliente'),
    ('CANCELADO_SIN_STOCK', 'Cancelado por Sin Stock'),
    ('ERROR_STOCK', 'Error de Stock'),
    ('ERROR_DTE', 'Error al Generar DTE'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0200_pedido_ecommerce_estado_canal'),
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
    ]
