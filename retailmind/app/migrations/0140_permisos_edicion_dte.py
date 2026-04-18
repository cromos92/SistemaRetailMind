# Generated manually on 2026-04-17
#
# Crea permisos granulares para la edición de DTEs desde la
# pantalla "Gestión de Documentos de Ventas".
#
# La matriz es separada (3 campos + 4 tipos de DTE):
#   - Para editar un campo sobre un DTE, el usuario debe tener
#     `puede_editar=True` en AMBAS opciones: la del campo y la del
#     tipo de DTE.
#
# Se otorga por defecto `puede_editar=True` al rol `administrador`
# en las 7 opciones, para preservar el comportamiento previo.

from django.db import migrations


# Campos editables del DTE (cada uno es una opción independiente
# con su propio checkbox `puede_editar` en la UI de permisos).
OPCIONES_CAMPOS = [
    ('dte_editar_fecha', 'Editar Fecha de DTE', 'ri-calendar-line', 20),
    ('dte_editar_numero', 'Editar N° Documento DTE', 'ri-hashtag', 21),
    ('dte_editar_pago', 'Editar Pagos de DTE', 'ri-money-dollar-circle-line', 22),
]


# Tipos de DTE editables (cada tipo es una opción independiente).
OPCIONES_TIPOS = [
    ('dte_editar_tipo_boleta_electronica', 'Editar Boleta Electrónica', 'ri-file-text-line', 30),
    ('dte_editar_tipo_boleta_papel', 'Editar Boleta Papel', 'ri-file-paper-2-line', 31),
    ('dte_editar_tipo_factura_electronica', 'Editar Factura Electrónica', 'ri-file-list-3-line', 32),
    ('dte_editar_tipo_factura_exenta', 'Editar Factura Exenta', 'ri-file-forbid-line', 33),
]


CODIGOS_NUEVOS = [c for c, *_ in OPCIONES_CAMPOS + OPCIONES_TIPOS]


def crear_permisos(apps, schema_editor):
    ModuloSistema = apps.get_model('app', 'ModuloSistema')
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')

    # Asegurar que exista el módulo "ventas"
    modulo_ventas, _ = ModuloSistema.objects.get_or_create(
        codigo='ventas',
        defaults={
            'nombre': 'Módulo Ventas',
            'descripcion': 'Gestión de ventas y punto de venta',
            'icono': 'ri-money-cny-circle-line',
            'orden': 2,
            'activo': True,
        },
    )

    for codigo, nombre, icono, orden in OPCIONES_CAMPOS + OPCIONES_TIPOS:
        opcion, _ = OpcionMenu.objects.update_or_create(
            codigo=codigo,
            defaults={
                'modulo': modulo_ventas,
                'nombre': nombre,
                'url_name': None,
                'url_path': None,
                'icono': icono,
                'orden': orden,
                'es_submenu': False,
                'padre': None,
                'activo': True,
            },
        )

        # Otorgar puede_editar al administrador para no romper flujos previos.
        PermisoRol.objects.update_or_create(
            rol='administrador',
            opcion_menu=opcion,
            defaults={
                'puede_ver': True,
                'puede_editar': True,
            },
        )


def eliminar_permisos(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    OpcionMenu.objects.filter(codigo__in=CODIGOS_NUEVOS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0139_compras_fecha_editable_temporada_normalizada'),
        # Merge del leaf huérfano `0100_add_origen_pago_ticketdetallepago`,
        # que añade el campo y el índice en TicketDetallePago.
        ('app', '0100_add_origen_pago_ticketdetallepago'),
    ]

    operations = [
        migrations.RunPython(crear_permisos, eliminar_permisos),
    ]
