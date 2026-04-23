# Generated manually on 2026-04-22
#
# 1. Crea la tabla HistorialCambioFolioDte para auditar cambios de
#    folio (numero_documento) sobre un DTE existente, preservando
#    trazabilidad de quién, cuándo, por qué y qué documentos relacionados
#    se actualizaron.
#
# 2. Registra el permiso granular "dte_editar_folio" que controla:
#       - la visibilidad del botón "Editar folio" en Gestión DTE.
#       - la autorización del endpoint `editar_folio_dte`.
#
#    Por defecto se otorga al rol `administrador` (puede_ver +
#    puede_editar). Los demás roles pueden habilitarlo luego desde
#    la pantalla de gestión de permisos.

from django.conf import settings
from django.db import migrations, models


CODIGO_OPCION = 'dte_editar_folio'
NOMBRE_OPCION = 'Editar folio de DTE'
ICONO_OPCION = 'bi-pencil-square'
ORDEN_OPCION = 24


def crear_permiso(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

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

    # Solo administrador habilitado por defecto.
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
        ('app', '0146_devolucion_nc_post_recepcion'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistorialCambioFolioDte',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('folio_anterior', models.IntegerField(help_text='Número de documento previo al cambio')),
                ('folio_nuevo', models.IntegerField(help_text='Número de documento después del cambio')),
                ('motivo', models.TextField(help_text='Motivo declarado por el usuario')),
                ('usuario_nombre', models.CharField(blank=True, default='', help_text='Snapshot del usuario al momento del cambio (por si el user se borra)', max_length=150)),
                ('fecha_cambio', models.DateTimeField(auto_now_add=True)),
                ('referencias_actualizadas', models.JSONField(blank=True, default=list, help_text='Lista de documentos relacionados cuyas referencias se actualizaron')),
                ('ip_cliente', models.GenericIPAddressField(blank=True, null=True)),
                ('dte', models.ForeignKey(help_text='DTE al que se le cambió el folio', on_delete=models.deletion.CASCADE, related_name='historial_cambios_folio', to='app.dte')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='cambios_folio_dte', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Historial Cambio de Folio DTE',
                'verbose_name_plural': 'Historial Cambios de Folio DTE',
                'ordering': ['-fecha_cambio', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='historialcambiofoliodte',
            index=models.Index(fields=['dte', '-fecha_cambio'], name='hist_folio_dte_fecha_idx'),
        ),
        migrations.AddIndex(
            model_name='historialcambiofoliodte',
            index=models.Index(fields=['folio_anterior'], name='hist_folio_anterior_idx'),
        ),
        migrations.AddIndex(
            model_name='historialcambiofoliodte',
            index=models.Index(fields=['folio_nuevo'], name='hist_folio_nuevo_idx'),
        ),
        migrations.RunPython(crear_permiso, eliminar_permiso),
    ]
