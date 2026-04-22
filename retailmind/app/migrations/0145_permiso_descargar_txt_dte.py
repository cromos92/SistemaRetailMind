# Generated manually on 2026-04-22
#
# Crea el permiso granular "Descargar TXT Acepta" que controla la
# visibilidad del botón de descarga en la pantalla "Gestión DTE"
# (/app/documentos/gestion-dte/) y la autorización del endpoint
# `generar_txt_desde_dte_existente`.
#
# Se otorga por defecto `puede_ver=True` al rol `administrador` para
# preservar el comportamiento previo en el que únicamente los
# administradores veían el botón.

from django.db import migrations


CODIGO_OPCION = 'dte_descargar_txt'
NOMBRE_OPCION = 'Descargar TXT Acepta de DTE'
ICONO_OPCION = 'bi-file-earmark-text'
ORDEN_OPCION = 23


def crear_permiso(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    # El permiso cuelga del módulo "documentos", que es donde vive la
    # pantalla que lo consume.
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

    # Por defecto, sólo administrador lo tiene habilitado. Los demás
    # roles pueden habilitarse después desde la pantalla de gestión
    # de permisos.
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
        ('app', '0144_alter_dte_tipo_documento'),
    ]

    operations = [
        migrations.RunPython(crear_permiso, eliminar_permiso),
    ]
