"""Rol 'jefe' + permiso fino para devolver plata por la API de Mercado Pago.

- PermisoRol.rol: agrega 'jefe' a los choices (sin efecto en la base).
- OpcionMenu `devolver_mercadopago` (módulo Documentos): devolver a la
  tarjeta por la API de MP al emitir una NC. Hoy lo hacían administrador y
  administración por chequeo fijo de rol → se siembra igual, así desplegar no
  cambia nada. El comando `configurar_perfiles` aplica después la política
  (Administrador sin devoluciones MP).

Escrita a mano. Segura en caliente: solo inserta filas.
"""
from django.db import migrations, models


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
    opcion, _ = OpcionMenu.objects.get_or_create(
        codigo='devolver_mercadopago',
        defaults={'modulo': modulo, 'nombre': 'Devolver a la tarjeta por Mercado Pago',
                  'icono': 'ri-refund-line', 'orden': 16, 'url_name': None, 'url_path': None},
    )
    for rol in ('administrador', 'administracion'):
        PermisoRol.objects.get_or_create(rol=rol, opcion_menu=opcion,
                                         defaults={**APAGADO, 'puede_ver': True, 'puede_crear': True})


def borrar(apps, schema_editor):
    apps.get_model('app', 'OpcionMenu').objects.filter(codigo='devolver_mercadopago').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0236_carga_factura_pdf'),
    ]

    operations = [
        migrations.AlterField(
            model_name='permisorol',
            name='rol',
            field=models.CharField(choices=[('maestro', 'Maestro'), ('administrador', 'Administrador'), ('jefe', 'Jefe'), ('administracion', 'Administración'), ('jefe_local', 'Jefe Local'), ('cajero', 'Cajero'), ('vendedor', 'Vendedor')], help_text='Rol de usuario', max_length=50),
        ),
        migrations.RunPython(crear, borrar),
    ]
