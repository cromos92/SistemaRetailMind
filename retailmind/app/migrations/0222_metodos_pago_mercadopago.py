"""Agrega la familia de métodos de pago Mercado Pago presencial (MP_*).

AlterField sin efecto en BD (choices no viven en el schema): solo actualiza
el estado de migraciones para que Django no acuse drift. Los métodos nuevos:
MP_QR (cobro por QR dinámico), MP_POINT / MP_POINT_DEBITO / MP_POINT_CREDITO
(terminal física, fase posterior). NO confundir con VENTA_INTERNET +
tipo_tarjeta MERCADOPAGO, que es el canal marketplace/ecommerce.
"""
from django.db import migrations, models


METODO_PAGO_TICKET_CHOICES = [
    ('EFECTIVO', 'Efectivo'),
    ('TARJETA_DEBITO', 'Tarjeta Débito'),
    ('TARJETA_CREDITO', 'Tarjeta Crédito'),
    ('TRANSFERENCIA', 'Transferencia'),
    ('CHEQUE', 'Cheque'),
    ('OTRO', 'Otro'),
    ('TBK_POS_INTEGRADO', 'Transbank POS Integrado'),
    ('TBK_MANUAL', 'Transbank Manual'),
    ('TBK_DEBITO_POS', 'Transbank Débito POS'),
    ('TBK_CREDITO_POS', 'Transbank Crédito POS'),
    ('TBK_PREPAGO_POS', 'Transbank Prepago POS'),
    ('MP_QR', 'Mercado Pago QR'),
    ('MP_POINT', 'Mercado Pago Point'),
    ('MP_POINT_DEBITO', 'Mercado Pago Point Débito'),
    ('MP_POINT_CREDITO', 'Mercado Pago Point Crédito'),
    ('TARJETA_COMERCIAL', 'Tarjeta Comercial'),
    ('VENTA_INTERNET', 'Venta por Internet'),
    ('ORDEN_COMPRA', 'Orden de Compra'),
    ('CREDITO_TRABAJADOR', 'Crédito Trabajador'),
    ('CREDITO_EXTERNO', 'Crédito Externo'),
    ('CONVENIO', 'Convenio'),
    ('GIFTCARD', 'Gift Card'),
    ('MULTIPLE', 'Pagos Combinados'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0221_envio_correo_copia_y_entrega'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='metodo_pago',
            field=models.CharField(choices=METODO_PAGO_TICKET_CHOICES, default='EFECTIVO', max_length=50),
        ),
        migrations.AlterField(
            model_name='ticketdetallepago',
            name='metodo_pago',
            field=models.CharField(choices=METODO_PAGO_TICKET_CHOICES, max_length=50),
        ),
        migrations.AlterField(
            model_name='pagocambiodevolucion',
            name='metodo_pago',
            field=models.CharField(choices=METODO_PAGO_TICKET_CHOICES, max_length=50),
        ),
        migrations.AlterField(
            model_name='pagocreditotrabajador',
            name='metodo_pago',
            field=models.CharField(choices=METODO_PAGO_TICKET_CHOICES, default='EFECTIVO', max_length=50),
        ),
    ]
