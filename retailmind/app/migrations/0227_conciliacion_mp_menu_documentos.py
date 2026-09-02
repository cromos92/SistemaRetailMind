"""Mueve la conciliación Mercado Pago al menú del módulo DOCUMENTOS.

A pedido del usuario: la opción ``dineros_mercadopago`` (pantalla
/app/ventas/dineros-mercadopago/ — pendiente de liberación, depósitos,
conciliación) pasa del módulo Ventas al módulo Documentos, renombrada
"Conciliación Mercado Pago". Solo perfil administrador/administración
(los PermisoRol de 0224 se conservan: cuelgan de la opción, no del módulo).

Idempotente; reversible (vuelve a Ventas con su nombre anterior).
"""
from django.db import migrations


def mover_a_documentos(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')

    modulo_doc, _ = ModuloSistema.objects.get_or_create(
        codigo='documentos',
        defaults={
            'nombre': 'Módulo Documentos',
            'descripcion': 'Gestión de documentos tributarios',
            'icono': 'ri-file-text-line',
            'orden': 3,
        },
    )
    OpcionMenu.objects.filter(codigo='dineros_mercadopago').update(
        modulo=modulo_doc,
        nombre='Conciliación Mercado Pago',
        orden=8,
    )


def volver_a_ventas(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    modulo_ventas = ModuloSistema.objects.filter(codigo='ventas').first()
    if modulo_ventas:
        OpcionMenu.objects.filter(codigo='dineros_mercadopago').update(
            modulo=modulo_ventas, nombre='Dineros Mercado Pago', orden=9,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0226_renombrar_menu_pos_integrado'),
    ]

    operations = [
        migrations.RunPython(mover_a_documentos, volver_a_ventas),
    ]
