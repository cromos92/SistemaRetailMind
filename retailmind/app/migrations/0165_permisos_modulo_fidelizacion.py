"""Data migration: da de alta el módulo ``fidelizacion`` (GiftCards + Puntos).

Crea (idempotente) el ModuloSistema, sus OpcionMenu y los PermisoRol por rol,
espejando lo que hace ``inicializar_permisos.crear_modulo_fidelizacion`` para
que producción quede consistente sin tener que re-ejecutar el comando.

Usa ``get_or_create`` en todas las operaciones — re-correr la migración no
duplica ni rompe nada.

Reparto de permisos (decisión de negocio):
- administrador / administracion: todas las opciones, acceso completo.
- jefe_local: gift cards (ver/crear/anular), cuentas, reporte; SIN config del programa.
- cajero: solo ver gift cards (redime al cobrar) y ver puntos del cliente.
- vendedor: solo lectura de gift cards y puntos.
"""
from django.db import migrations


OPCIONES = [
    # (codigo, nombre, url_path, icono, orden)
    ('giftcards_listado', 'Gift Cards', '/app/giftcards/', 'ri-gift-line', 1),
    ('giftcards_emitir', 'Emitir Gift Card', '/app/giftcards/emitir/', 'ri-add-circle-line', 2),
    ('fidelizacion_cuentas', 'Clientes y Puntos', '/app/fidelizacion/', 'ri-user-star-line', 3),
    ('fidelizacion_programa', 'Configuración Programa', '/app/fidelizacion/configuracion/', 'ri-settings-3-line', 4),
    ('fidelizacion_reporte', 'Reporte Fidelización', '/app/fidelizacion/reporte/', 'ri-bar-chart-box-line', 5),
]

# Permisos por rol distinto a admin/administracion (que reciben todo).
# Mapea codigo -> dict de flags. Si un código no aparece para un rol, ese rol
# no recibe permiso sobre él.
PERMISOS_ROL = {
    'jefe_local': {
        'giftcards_listado': dict(ver=True, crear=True, editar=True, eliminar=False, exportar=True, aprobar=False),
        'giftcards_emitir': dict(ver=True, crear=True, editar=True, eliminar=False, exportar=False, aprobar=False),
        'fidelizacion_cuentas': dict(ver=True, crear=True, editar=True, eliminar=False, exportar=True, aprobar=False),
        'fidelizacion_reporte': dict(ver=True, crear=False, editar=False, eliminar=False, exportar=True, aprobar=False),
    },
    'cajero': {
        'giftcards_listado': dict(ver=True, crear=False, editar=False, eliminar=False, exportar=False, aprobar=False),
        'fidelizacion_cuentas': dict(ver=True, crear=False, editar=False, eliminar=False, exportar=False, aprobar=False),
    },
    'vendedor': {
        'giftcards_listado': dict(ver=True, crear=False, editar=False, eliminar=False, exportar=False, aprobar=False),
        'fidelizacion_cuentas': dict(ver=True, crear=False, editar=False, eliminar=False, exportar=False, aprobar=False),
    },
}


def crear(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='fidelizacion',
        defaults={
            'nombre': 'Fidelización',
            'descripcion': 'Gift cards y programa de puntos de clientes',
            'icono': 'ri-gift-line',
            'orden': 8,
            'activo': True,
        },
    )

    opciones_por_codigo = {}
    for codigo, nombre, url_path, icono, orden in OPCIONES:
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo=codigo,
            defaults={
                'modulo': modulo,
                'nombre': nombre,
                'url_path': url_path,
                'icono': icono,
                'orden': orden,
                'es_submenu': False,
                'activo': True,
            },
        )
        opciones_por_codigo[codigo] = opcion

    # administrador y administracion: acceso completo a todas las opciones
    for rol in ('administrador', 'administracion'):
        for opcion in opciones_por_codigo.values():
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

    # Resto de roles según el mapa
    for rol, mapa in PERMISOS_ROL.items():
        for codigo, flags in mapa.items():
            opcion = opciones_por_codigo.get(codigo)
            if not opcion:
                continue
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
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    codigos = [c for c, *_ in OPCIONES]
    # Borrar opciones (cascade elimina sus PermisoRol asociados)
    OpcionMenu.objects.filter(codigo__in=codigos).delete()
    ModuloSistema.objects.filter(codigo='fidelizacion').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0164_cuentapuntos_ticket_cliente_and_more'),
    ]

    operations = [
        migrations.RunPython(crear, quitar),
    ]
