"""Permisos finos: asociar pagos Mercado Pago y edición/eliminación de documentos.

- `asociar_pagos_mercadopago`: asociar cobros MP sin venta y pagos «MP manual»
  a su venta (Conciliación Mercado Pago). Nadie lo tiene: solo el Maestro (que
  pasa todo). Se crean filas APAGADAS para administrador y administración para
  que `inicializar_permisos` (que enciende todo lo que falta al administrador)
  no se lo regale.
- `dte_eliminar_documento`: eliminar / anular un documento de venta. Hoy lo
  hacía el rol administrador por chequeo fijo → se siembra igual.
- `dte_compras_pagos`: editar / eliminar pagos de documentos de compra.
- `dte_compras_eliminar`: eliminar un documento de compra.
  Hoy esos endpoints solo pedían login; se siembran para los roles que ven
  Gestión Documentos Compras, así desplegar no le quita nada a nadie.
El paso a «solo Maestro» lo aplica el comando `configurar_rol_maestro`.

Escrita a mano. Segura en caliente: solo inserta filas.
"""
from django.db import migrations


OPCIONES = [
    # codigo, nombre, icono, orden
    ('asociar_pagos_mercadopago', 'Asociar pagos Mercado Pago', 'ri-links-line', 12),
    ('dte_eliminar_documento', 'Eliminar / anular documento de venta', 'ri-delete-bin-6-line', 13),
    ('dte_compras_pagos', 'Editar / eliminar pagos de documentos de compra', 'ri-bank-card-2-line', 14),
    ('dte_compras_eliminar', 'Eliminar documento de compra', 'ri-file-reduce-line', 15),
]
APAGADO = {'puede_ver': False, 'puede_crear': False, 'puede_editar': False,
           'puede_eliminar': False, 'puede_exportar': False, 'puede_aprobar': False}


def crear(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='documentos',
        defaults={'nombre': 'Módulo Documentos', 'descripcion': 'Gestión de documentos tributarios',
                  'icono': 'ri-file-list-line', 'orden': 3},
    )
    op = {}
    for codigo, nombre, icono, orden in OPCIONES:
        op[codigo], _ = OpcionMenu.objects.get_or_create(
            codigo=codigo,
            defaults={'modulo': modulo, 'nombre': nombre, 'icono': icono, 'orden': orden,
                      'url_name': None, 'url_path': None},
        )

    for rol in ('administrador', 'administracion'):
        PermisoRol.objects.get_or_create(rol=rol, opcion_menu=op['asociar_pagos_mercadopago'], defaults=APAGADO)

    PermisoRol.objects.get_or_create(
        rol='administrador', opcion_menu=op['dte_eliminar_documento'],
        defaults={**APAGADO, 'puede_ver': True, 'puede_eliminar': True},
    )

    roles_compras = set(
        PermisoRol.objects.filter(opcion_menu__codigo='gestion_dte_compras', puede_ver=True)
        .values_list('rol', flat=True)
    )
    for rol in roles_compras:
        PermisoRol.objects.get_or_create(
            rol=rol, opcion_menu=op['dte_compras_pagos'],
            defaults={**APAGADO, 'puede_ver': True, 'puede_editar': True, 'puede_eliminar': True},
        )
        PermisoRol.objects.get_or_create(
            rol=rol, opcion_menu=op['dte_compras_eliminar'],
            defaults={**APAGADO, 'puede_ver': True, 'puede_eliminar': True},
        )


def borrar(apps, schema_editor):
    apps.get_model('app', 'OpcionMenu').objects.filter(codigo__in=[c for c, *_ in OPCIONES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0233_rol_maestro_y_permisos_nota_credito'),
    ]

    operations = [
        migrations.RunPython(crear, borrar),
    ]
