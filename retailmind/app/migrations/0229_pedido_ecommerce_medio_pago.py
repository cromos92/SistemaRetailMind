"""Medio de pago real del pedido ecommerce (Webpay / Mercado Pago / ...).

`PedidoEcommerce` no guardaba con qué pagó el cliente en el canal, así que la
cuadratura de caja tenía que adivinarlo desde `canal_origen`. Los ecommerce
propios (REALSPORT / PAOLA) no estaban en `PLATAFORMA_INTERNET_POR_CANAL`,
caían al literal ``'Internet'`` y de ahí al ``else`` del clasificador de Venta
Internet: **todo pedido propio se contaba como venta Mercado Pago**, aunque se
hubiera pagado con Webpay.

Estos dos campos guardan el dato real:
  * ``medio_pago``        — WEBPAY / MERCADO_PAGO / TRANSFERENCIA / OTRO, '' = sin definir.
  * ``medio_pago_origen`` — 'CANAL' (lo informó AllConnected en la ingesta) o
                            'MANUAL' (lo fijó un operador desde el listado).

Solo agrega dos columnas con default vacío: no toca datos existentes. Los
pedidos ya facturados quedan en '' (sin definir) y la cuadratura los muestra
como "ECOMMERCE S/DEF." en vez de inventarles un medio.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0228_mp_payment_id_numerico'),
    ]

    operations = [
        migrations.AddField(
            model_name='pedidoecommerce',
            name='medio_pago',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=20,
                choices=[
                    ('', 'Sin definir'),
                    ('WEBPAY', 'Webpay / Transbank'),
                    ('MERCADO_PAGO', 'Mercado Pago'),
                    ('TRANSFERENCIA', 'Transferencia'),
                    ('OTRO', 'Otro'),
                ],
                help_text='Con qué pagó el cliente en el canal. Determina cómo se '
                          'clasifica la venta en la cuadratura de caja.',
                verbose_name='Medio de pago',
            ),
        ),
        migrations.AddField(
            model_name='pedidoecommerce',
            name='medio_pago_origen',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text="'CANAL' si lo informó AllConnected, 'MANUAL' si lo fijó un "
                          "operador. Vacío = sin definir.",
                verbose_name='Origen del medio de pago',
            ),
        ),
    ]
