"""Toma de inventario: revisión posterior de faltantes (jefe de local → administrador).

- TomaInventarioDetalle: estado de la revisión del faltante ya descontado
  (ENCONTRADO / CONFIRMADO / REPUESTO), unidades encontradas, nota, quién y
  cuándo lo reportó y quién y cuándo lo repuso al stock.
- OpcionMenu `revision_faltantes_inventario` (módulo Existencias): pantalla
  «Faltantes por revisar». Es un permiso APARTE de Gestión de Inventarios a
  propósito: ese comparte permiso con Fusionar Duplicados (mueve stock con solo
  Ver), así que darle Ver al jefe de local le abría también eso.
  Ver + Editar = ver la lista y reportar (no mueve stock). Reponer al stock
  exige además rol Administrador / Jefe / Maestro (chequeo en la vista).

Escrita a mano. Segura en caliente: agrega columnas opcionales e inserta filas.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


APAGADO = {'puede_ver': False, 'puede_crear': False, 'puede_editar': False,
           'puede_eliminar': False, 'puede_exportar': False, 'puede_aprobar': False}
VER_EDITAR = {**APAGADO, 'puede_ver': True, 'puede_editar': True, 'puede_exportar': True}


def crear_opcion(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')
    # Solo si el módulo ya existe (producción): una base nueva lo crea
    # `inicializar_permisos`, que también siembra esta opción.
    modulo = ModuloSistema.objects.filter(codigo='existencias').first()
    if modulo is None:
        return
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='revision_faltantes_inventario',
        defaults={'modulo': modulo, 'nombre': 'Faltantes por revisar', 'icono': 'ri-search-eye-line',
                  'orden': 13, 'url_name': 'revision_faltantes', 'url_path': None},
    )
    for rol in ('administrador', 'jefe', 'administracion', 'jefe_local'):
        PermisoRol.objects.get_or_create(rol=rol, opcion_menu=opcion, defaults=VER_EDITAR)


def borrar_opcion(apps, schema_editor):
    apps.get_model('app', 'OpcionMenu').objects.filter(codigo='revision_faltantes_inventario').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0242_correo_modulo_requerimientos'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='revision_estado',
            field=models.CharField(blank=True, choices=[('', 'Sin revisar'), ('ENCONTRADO', 'Encontrado: falta reponer'), ('CONFIRMADO', 'Faltante confirmado'), ('REPUESTO', 'Repuesto al stock')], default='', max_length=12, verbose_name='Revisión del faltante'),
        ),
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='revision_cantidad',
            field=models.IntegerField(blank=True, null=True, verbose_name='Unidades encontradas'),
        ),
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='revision_nota',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Nota de la revisión'),
        ),
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='revision_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revisiones_faltante_toma', to=settings.AUTH_USER_MODEL, verbose_name='Revisado por'),
        ),
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='revision_fecha',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Fecha de revisión'),
        ),
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='reposicion_por',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reposiciones_faltante_toma', to=settings.AUTH_USER_MODEL, verbose_name='Repuesto por'),
        ),
        migrations.AddField(
            model_name='tomainventariodetalle',
            name='reposicion_fecha',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Fecha de reposición'),
        ),
        migrations.RunPython(crear_opcion, borrar_opcion),
    ]
