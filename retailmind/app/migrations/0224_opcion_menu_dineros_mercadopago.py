"""Data migration: opción de menú ``dineros_mercadopago`` (Ventas).

Pantalla financiera del ciclo del dinero Mercado Pago (cobrado → pendiente de
liberación → liberado → depositado + conciliación). Solo roles administrativos:
es información de plata por llegar al banco, no operación de tienda.

Idempotente (get_or_create), misma receta que 0218.
"""
from django.db import migrations


def crear_opcion_menu(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='ventas',
        defaults={
            'nombre': 'Módulo Ventas',
            'descripcion': 'Gestión de ventas y punto de venta',
            'icono': 'ri-money-cny-circle-line',
            'orden': 2,
        },
    )

    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='dineros_mercadopago',
        defaults={
            'modulo': modulo,
            'nombre': 'Dineros Mercado Pago',
            'url_name': 'dineros_mercadopago',
            'url_path': '/app/ventas/dineros-mercadopago/',
            'icono': 'ri-money-dollar-circle-line',
            'orden': 9,
            'es_submenu': False,
            'activo': True,
        },
    )

    for rol in ('administrador', 'administracion'):
        PermisoRol.objects.get_or_create(
            rol=rol,
            opcion_menu=opcion,
            defaults={
                'puede_ver': True,
                'puede_crear': False,
                'puede_editar': True,
                'puede_eliminar': False,
                'puede_exportar': True,
                'puede_aprobar': False,
            },
        )


def quitar_opcion_menu(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo='dineros_mercadopago').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0223_mercadopago_modelos'),
    ]

    operations = [
        migrations.RunPython(crear_opcion_menu, quitar_opcion_menu),
    ]
