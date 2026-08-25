"""Data migration: da de alta la opción de menú ``retiro_pedido_local``.

Crea (idempotente) la OpcionMenu para que aparezca el ítem en el menú
Ecommerce → Retiro pedido local — la pantalla del mesón donde la tienda
valida el código de retiro contra AllConnected e imprime el comprobante
que firma quien retira.

Permisos: además de administrador/administracion, se otorga a jefe_local,
vendedor y cajero (ver + crear) porque la pantalla la opera quien atiende
el mesón — exigir un rol administrativo dejaría al cliente esperando.

Usa ``get_or_create`` en todas las operaciones — re-correr la migración
no duplica ni rompe nada (misma receta que 0157).
"""
from django.db import migrations


def crear_opcion_menu(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    # Mismo módulo que crea inicializar_permisos.crear_modulo_ecommerce.
    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='ecommerce',
        defaults={
            'nombre': 'Ecommerce',
            'descripcion': 'Gestión de pedidos de comercio electrónico',
            'icono': 'ri-shopping-cart-2-line',
            'orden': 9,
        },
    )

    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='retiro_pedido_local',
        defaults={
            'modulo': modulo,
            'nombre': 'Retiro pedido local',
            'url_name': 'retiro_pedido_local',
            'url_path': '/app/ecommerce/retiro-local/',
            'icono': 'ri-qr-scan-2-line',
            'orden': 4,
            'es_submenu': False,
            'activo': True,
        },
    )

    # Roles administrativos: control total sobre la opción.
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

    # Roles de tienda: ver la pantalla y registrar retiros (crear), nada más.
    for rol in ('jefe_local', 'vendedor', 'cajero'):
        PermisoRol.objects.get_or_create(
            rol=rol,
            opcion_menu=opcion,
            defaults={
                'puede_ver': True,
                'puede_crear': True,
                'puede_editar': False,
                'puede_eliminar': False,
                'puede_exportar': False,
                'puede_aprobar': False,
            },
        )


def quitar_opcion_menu(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo='retiro_pedido_local').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0217_pedido_ecommerce_es_retiro_local'),
    ]

    operations = [
        migrations.RunPython(crear_opcion_menu, quitar_opcion_menu),
    ]
