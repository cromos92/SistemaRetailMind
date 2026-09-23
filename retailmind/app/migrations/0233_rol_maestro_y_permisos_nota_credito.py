"""Rol Maestro + permisos finos de Nota de Crédito.

- PermisoRol.rol: agrega 'maestro' a los choices (sin efecto en la base).
- OpcionMenu 'emitir_nota_credito' (NC a clientes) y
  'emitir_nota_credito_traspaso' (NC de traspasos internos en recepción), en el
  módulo Documentos. El código las exige ADEMÁS del permiso de cada pantalla.
- Se siembran con el MISMO acceso que había (administrador, administración y
  jefe local), así que desplegar esto no le quita nada a nadie. El bloqueo del
  Administrador lo aplica después el comando `configurar_rol_maestro`.
- Repara nombres de menú guardados con acentos rotos ('Gesti?n') por una
  versión del seeder que se guardó sin UTF-8.

Escrita a mano. Segura en caliente: solo inserta filas y corrige textos.
"""
from django.db import migrations, models


OPCIONES_NC = [
    ('emitir_nota_credito', 'Emitir Nota de Crédito (clientes)', 'ri-refund-2-line', 10),
    ('emitir_nota_credito_traspaso', 'Emitir NC de traspasos internos (recepción)', 'ri-arrow-go-back-line', 11),
]
ROLES_CON_NC = ('administrador', 'administracion', 'jefe_local')

ACENTOS = {
    'Gesti?n': 'Gestión', 'M?dulo': 'Módulo', 'Fidelizaci?n': 'Fidelización',
    'Configuraci?n': 'Configuración', 'Liquidaci?n': 'Liquidación',
    'Conciliaci?n': 'Conciliación', 'Revisi?n': 'Revisión', 'Dep?sitos': 'Depósitos',
    'Recepci?n': 'Recepción', 'Predicci?n': 'Predicción', 'Modificaci?n': 'Modificación',
    'Impresi?n': 'Impresión', 'Emisi?n': 'Emisión', 'Cr?ditos': 'Créditos',
    'Campa?as': 'Campañas', 'Garant?a': 'Garantía', 'Devoluci?n': 'Devolución',
}


def crear_opciones_nc(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    modulo, _ = ModuloSistema.objects.get_or_create(
        codigo='documentos',
        defaults={'nombre': 'Módulo Documentos', 'descripcion': 'Gestión de documentos tributarios',
                  'icono': 'ri-file-list-line', 'orden': 3},
    )
    for codigo, nombre, icono, orden in OPCIONES_NC:
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo=codigo,
            defaults={'modulo': modulo, 'nombre': nombre, 'icono': icono, 'orden': orden,
                      'url_name': None, 'url_path': None},
        )
        for rol in ROLES_CON_NC:
            PermisoRol.objects.get_or_create(
                rol=rol, opcion_menu=opcion,
                defaults={'puede_ver': True, 'puede_crear': True, 'puede_editar': False,
                          'puede_eliminar': False, 'puede_exportar': False, 'puede_aprobar': False},
            )


def borrar_opciones_nc(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo__in=[c for c, *_ in OPCIONES_NC]).delete()


def reparar_acentos(apps, schema_editor):
    for modelo in ('ModuloSistema', 'OpcionMenu'):
        Modelo = apps.get_model('app', modelo)
        for obj in Modelo.objects.filter(nombre__contains='?'):
            nombre = obj.nombre
            for roto, bueno in ACENTOS.items():
                nombre = nombre.replace(roto, bueno)
            if nombre != obj.nombre:
                obj.nombre = nombre
                obj.save(update_fields=['nombre'])


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0232_cotizacion_guia_y_cierre_despacho'),
    ]

    operations = [
        migrations.AlterField(
            model_name='permisorol',
            name='rol',
            field=models.CharField(choices=[('maestro', 'Maestro'), ('administrador', 'Administrador'), ('administracion', 'Administración'), ('jefe_local', 'Jefe Local'), ('cajero', 'Cajero'), ('vendedor', 'Vendedor')], help_text='Rol de usuario', max_length=50),
        ),
        migrations.RunPython(crear_opciones_nc, borrar_opciones_nc),
        migrations.RunPython(reparar_acentos, migrations.RunPython.noop),
    ]
