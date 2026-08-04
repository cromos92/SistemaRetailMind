"""
Agrega el método de devolución REBAJA_CREDITO y reordena los choices.

Solo cambia `choices` (validación a nivel de Django): NO altera la columna en
PostgreSQL, no reescribe ni borra datos. Las devoluciones históricas con
'EFECTIVO_CAJA' siguen siendo válidas — ese valor se conserva en la lista,
solo queda oculto en la UI.
"""
from django.db import migrations, models


METODOS = [
    ('TRANSFERENCIA_BANCARIA', 'Transferencia bancaria'),
    ('REBAJA_CREDITO', 'Rebaja crédito del cliente'),
    ('NO_AFECTA_CAJA', 'No afecta caja'),
    ('EFECTIVO_CAJA', 'Efectivo de caja'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0198_restaurar_indices_sku_articulo'),
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
