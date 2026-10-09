"""Gestión de Inventarios: el jefe de local puede REVISAR las tomas de su tienda.

PermisoRol (jefe_local, gestion_inventarios) pasa a Ver = True y todo lo demás
en False. Con solo Ver la pantalla entra en «modo revisión»: ve sus tomas, lo
contado, las diferencias y los faltantes en unidades, sin costos, precios,
análisis valorizado ni Excel, y sin botones que muevan la toma (todas esas
rutas exigen Editar). Fusionar Duplicados, que comparte esta opción y mueve
stock, ahora también exige Editar (app/urls.py), así que este Ver ya no lo abre.

Escrita a mano. Segura en caliente: solo actualiza o inserta una fila.
"""
from django.db import migrations

SOLO_VER = {'puede_ver': True, 'puede_crear': False, 'puede_editar': False,
            'puede_eliminar': False, 'puede_exportar': False, 'puede_aprobar': False}
NADA = {**SOLO_VER, 'puede_ver': False}


def _fijar(apps, valores):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')
    opcion = OpcionMenu.objects.filter(codigo='gestion_inventarios').first()
    if opcion is None:  # BD sin el catálogo de permisos (tests): nada que hacer
        return
    PermisoRol.objects.update_or_create(rol='jefe_local', opcion_menu=opcion, defaults=valores)


def dar_ver(apps, schema_editor):
    _fijar(apps, SOLO_VER)


def quitar_ver(apps, schema_editor):
    _fijar(apps, NADA)


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0243_revision_faltantes_toma'),
    ]

    operations = [
        migrations.RunPython(dar_ver, quitar_ver),
    ]
