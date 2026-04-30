# Generated manually on 2026-04-30
#
# Crea el permiso granular `dte_editar_vendedor` que controla el acceso
# al cambio de vendedor en un DTE existente desde Gestión de Documentos.
#
# Funciona en combinación con los permisos por tipo de DTE
# (`dte_editar_tipo_*`): para que un usuario pueda cambiar el vendedor
# de un DTE necesita tener tanto `dte_editar_vendedor.puede_editar`
# como el `dte_editar_tipo_*.puede_editar` correspondiente al tipo del
# documento.
#
# Por defecto se habilita sólo al rol `administrador`. Los demás roles
# pueden habilitarse manualmente desde la pantalla de gestión de
# permisos.

from django.db import migrations


CODIGO_OPCION = 'dte_editar_vendedor'
NOMBRE_OPCION = 'Editar vendedor de DTE'
ICONO_OPCION = 'ri-user-settings-line'
ORDEN_OPCION = 24


def crear_permiso(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    # El permiso vive en el módulo `documentos`, junto al resto de
    # permisos de edición granular del DTE (fecha, número, pagos, tipo).
    modulo_documentos, _ = ModuloSistema.objects.get_or_create(
        codigo='documentos',
        defaults={
            'nombre': 'Módulo Documentos',
            'descripcion': 'Gestión de documentos tributarios',
            'icono': 'ri-file-list-line',
            'orden': 3,
            'activo': True,
        },
    )

    opcion, _ = OpcionMenu.objects.update_or_create(
        codigo=CODIGO_OPCION,
        defaults={
            'modulo': modulo_documentos,
            'nombre': NOMBRE_OPCION,
            'url_name': None,
            'url_path': None,
            'icono': ICONO_OPCION,
            'orden': ORDEN_OPCION,
            'es_submenu': False,
            'padre': None,
            'activo': True,
        },
    )

    # Por defecto sólo administrador puede ver/editar. El permiso real
    # consultado en runtime es `puede_editar` (mismo patrón que el resto
    # de `dte_editar_*`).
    PermisoRol.objects.update_or_create(
        rol='administrador',
        opcion_menu=opcion,
        defaults={
            'puede_ver': True,
            'puede_editar': True,
        },
    )


def eliminar_permiso(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo=CODIGO_OPCION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0150_permiso_reporte_comisiones_vendedor'),
    ]

    operations = [
        migrations.RunPython(crear_permiso, eliminar_permiso),
    ]
