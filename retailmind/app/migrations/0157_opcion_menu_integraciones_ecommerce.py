"""Data migration: da de alta la opción de menú ``integraciones_ecommerce``.

Crea (idempotente) la OpcionMenu para que aparezca el ítem en el menú
Configuración → Integraciones Ecommerce, y otorga permiso de ver/crear/
editar/eliminar a los roles ``administrador`` y ``administracion``.

Usa ``get_or_create`` en todas las operaciones — re-correr la migración
no duplica ni rompe nada.
"""
from django.db import migrations


def crear_opcion_menu(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='configuracion',
        defaults={
            'nombre': 'Configuración',
            'icono': 'ri-settings-4-line',
            'orden': 90,
            'activo': True,
        },
    )

    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='integraciones_ecommerce',
        defaults={
            'modulo': modulo,
            'nombre': 'Integraciones Ecommerce',
            'url_name': 'integraciones_ecommerce',
            'url_path': '/app/configuracion/integraciones-ecommerce/',
            'icono': 'ri-image-line',
            'orden': 80,
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
                'puede_crear': True,
                'puede_editar': True,
                'puede_eliminar': True,
                'puede_exportar': False,
                'puede_aprobar': False,
            },
        )


def quitar_opcion_menu(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo='integraciones_ecommerce').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0156_credenciales_ecommerce_y_fotos'),
    ]

    operations = [
        migrations.RunPython(crear_opcion_menu, quitar_opcion_menu),
    ]
