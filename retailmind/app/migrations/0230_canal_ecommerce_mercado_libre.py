"""Declara MercadoLibre como canal ecommerce ('MERCADO' -> 'Mercado Libre').

Sólo cambia `choices`: a nivel de base de datos es un NO-OP (la columna sigue
siendo el mismo varchar). Los 25 pedidos que ya existen en produccion con
canal_origen='MERCADO' quedan validos sin tocar un solo dato.

Por que 'MERCADO' y no 'MERCADOLIBRE': AllConnected manda 'MERCADOLIBRE' (el
tipo_marketplace de los canales 32/33) y `CANAL_ALIAS` en views_ecommerce lo
normaliza a 'MERCADO' desde antes de que existiera el canal. Se adopta ese
codigo como canonico en vez de migrar datos productivos.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0229_pedido_ecommerce_medio_pago'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pedidoecommerce',
            name='canal_origen',
            field=models.CharField(
                choices=[
                    ('SHOPIFY', 'Shopify'),
                    ('PARIS', 'Paris'),
                    ('RIPLEY', 'Ripley'),
                    ('WALMART', 'Walmart'),
                    ('MERCADO', 'Mercado Libre'),
                    ('REALSPORT', 'Realsport'),
                    ('PAOLA', 'Paola'),
                    ('OTRO', 'Otro'),
                ],
                db_index=True,
                max_length=20,
                verbose_name='Canal origen',
            ),
        ),
    ]
