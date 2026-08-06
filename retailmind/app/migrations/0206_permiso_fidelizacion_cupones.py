"""Data migration: da de alta la opción de menú ``fidelizacion_cupones``.

El ``ModuloSistema`` 'fidelizacion' ya existe (creado por la 0165), así que acá
sólo se agrega la ``OpcionMenu`` nueva y sus ``PermisoRol``.

Reparto de permisos (decisión de negocio):
- administrador / administracion: acceso completo (crear campañas, emitir, anular).
- jefe_local: ver y emitir cupones; NO crea campañas ni anula.
- cajero / vendedor: SIN opción de menú. Validan el código desde el POS con su
  permiso de 'ticket_venta' — emitir descuentos no es una capacidad de caja.

Idempotente (``get_or_create`` en todo): re-correrla no duplica ni rompe nada.
"""
from django.db import migrations


CODIGO = 'fidelizacion_cupones'
NOMBRE = 'Códigos de Descuento'
URL_PATH = '/app/fidelizacion/cupones/'
ICONO = 'ri-coupon-3-line'
ORDEN = 6

PERMISOS_ROL = {
    'jefe_local': dict(ver=True, crear=True, editar=False, eliminar=False,
                       exportar=True, aprobar=False),
}


def crear(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    modulo = ModuloSistema.objects.filter(codigo='fidelizacion').first()
    if not modulo:
        # La 0165 debería haberlo creado; si no está (BD parcial), se crea acá
        # para que la opción no quede huérfana y sin permisos.
        modulo = ModuloSistema.objects.create(
            codigo='fidelizacion',
            nombre='Fidelización',
            descripcion='Gift cards y programa de puntos de clientes',
            icono='ri-gift-line',
            orden=8,
            activo=True,
        )

    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo=CODIGO,
        defaults={
            'modulo': modulo,
            'nombre': NOMBRE,
            'url_path': URL_PATH,
            'icono': ICONO,
            'orden': ORDEN,
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
                'puede_eliminar': rol == 'administrador',
                'puede_exportar': True,
                'puede_aprobar': True,
            },
        )

    for rol, flags in PERMISOS_ROL.items():
        PermisoRol.objects.get_or_create(
            rol=rol,
            opcion_menu=opcion,
            defaults={
                'puede_ver': flags['ver'],
                'puede_crear': flags['crear'],
                'puede_editar': flags['editar'],
                'puede_eliminar': flags['eliminar'],
                'puede_exportar': flags['exportar'],
                'puede_aprobar': flags['aprobar'],
            },
        )


def quitar(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    # Borrar la opción arrastra sus PermisoRol (cascade). El módulo se conserva:
    # lo comparten gift cards y puntos.
    OpcionMenu.objects.filter(codigo=CODIGO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0205_cupon_descuento_nominativo'),
    ]

    operations = [
        migrations.RunPython(crear, quitar),
    ]
