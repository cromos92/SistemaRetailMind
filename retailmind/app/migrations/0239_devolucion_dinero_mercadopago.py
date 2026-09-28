"""
Agrega el método de devolución MERCADO_PAGO a la Devolución de Dinero.

Solo cambia `choices` (validación a nivel de Django): NO altera la columna en
PostgreSQL, no reescribe ni borra datos. Segura en caliente.

Escrita a mano (28-09-2026).
"""
from django.db import migrations, models


METODOS = [
    ('TRANSFERENCIA_BANCARIA', 'Transferencia bancaria'),
    ('MERCADO_PAGO', 'Mercado Pago'),
    ('REBAJA_CREDITO', 'Rebaja crédito del cliente'),
    ('NO_AFECTA_CAJA', 'No afecta caja'),
    ('EFECTIVO_CAJA', 'Efectivo de caja'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0238_carga_factura_aprendizaje'),
    ]

    operations = [
        migrations.AlterField(
            model_name='devoluciongarantia',
            name='metodo_devolucion',
            field=models.CharField(
                blank=True, choices=METODOS, default='', max_length=30,
                help_text='Cómo impacta la NC en la cuadratura de caja',
            ),
        ),
        migrations.AlterField(
            model_name='devoluciongarantia',
            name='metodo_solicitado',
            field=models.CharField(
                blank=True, choices=METODOS, default='', max_length=30,
                help_text='Método de devolución pedido por el cliente (efectivo/transferencia)',
            ),
        ),
    ]
