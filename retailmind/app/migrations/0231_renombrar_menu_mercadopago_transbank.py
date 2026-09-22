"""Renombra la opción de menú 'POS Transbank y Mercado Pago' → 'Mercado Pago y Transbank'.

Mercado Pago pasó a ser el cobro principal del POS (Transbank quedó chico y al
final), así que el menú se ordena igual: primero el medio que más se usa.
Idempotente; reversible al nombre anterior.
"""
from django.db import migrations


def renombrar(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo='pos_transbank').update(
        nombre='Mercado Pago y Transbank'
    )


def revertir(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo='pos_transbank').update(
        nombre='POS Transbank y Mercado Pago'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0230_canal_ecommerce_mercado_libre'),
    ]

    operations = [
        migrations.RunPython(renombrar, revertir),
    ]
