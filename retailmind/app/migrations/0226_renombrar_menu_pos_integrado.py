"""Renombra la opción de menú 'POS Transbank' → 'POS Transbank y Mercado Pago'.

La página /app/pos/transbank/ ahora tiene pestañas para ambos proveedores
(gestión de credenciales/webhook MP solo admin + asociación de cajas QR).
Idempotente; reversible al nombre anterior.
"""
from django.db import migrations


def renombrar(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo='pos_transbank').update(
        nombre='POS Transbank y Mercado Pago'
    )


def revertir(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo='pos_transbank').update(nombre='POS Transbank')


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0225_mercadopago_cuenta_en_bd'),
    ]

    operations = [
        migrations.RunPython(renombrar, revertir),
    ]
