# Generated manually on 2026-04-30
#
# Crea el permiso granular `reporte_comisiones_vendedor` que controla:
#   * La visibilidad del botón "Comisiones" en /app/reportes/ventas-sucursal/.
#   * El acceso a los endpoints `obtener_comisiones_por_vendedor` y
#     `exportar_comisiones_vendedor_excel` (devuelven 403 sin permiso).
#
# El permiso vive en el módulo `reportes` (mismo que los otros reportes
# de ventas-sucursal). Por defecto se otorga `puede_ver=True` y
# `puede_exportar=True` al rol `administrador`. Los demás roles
# pueden habilitarse después desde la pantalla de gestión de permisos.

from django.db import migrations


CODIGO_OPCION = 'reporte_comisiones_vendedor'
NOMBRE_OPCION = 'Reporte Comisiones por Vendedor'
ICONO_OPCION = 'ri-percent-line'
ORDEN_OPCION = 5


def crear_permiso(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    # El módulo `reportes` ya existe (lo crea inicializar_permisos /
    # 0057_modulosistema_*). Se hace get_or_create defensivo por si la
    # base es muy antigua.
    modulo_reportes, _ = ModuloSistema.objects.get_or_create(
        codigo='reportes',
        defaults={
            'nombre': 'Módulo Reportes',
            'descripcion': 'Reportes y análisis de datos',
            'icono': 'ri-bar-chart-grouped-line',
            'orden': 7,
            'activo': True,
        },
    )

    opcion, _ = OpcionMenu.objects.update_or_create(
        codigo=CODIGO_OPCION,
        defaults={
            'modulo': modulo_reportes,
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

    # Por defecto, sólo administrador puede ver y exportar el reporte.
    # Los demás roles pueden habilitarse manualmente desde la pantalla
    # de gestión de permisos (Permisos por Rol).
    PermisoRol.objects.update_or_create(
        rol='administrador',
        opcion_menu=opcion,
        defaults={
            'puede_ver': True,
            'puede_exportar': True,
        },
    )


def eliminar_permiso(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo=CODIGO_OPCION).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0149_dte_es_manual'),
    ]

    operations = [
        migrations.RunPython(crear_permiso, eliminar_permiso),
    ]
