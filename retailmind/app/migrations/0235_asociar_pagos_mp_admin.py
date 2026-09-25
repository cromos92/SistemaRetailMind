"""Enciende `asociar_pagos_mercadopago` (ver + editar) para Administrador y
Administración.

Pedido 25-09-2026: con las máquinas de Mercado Pago en modo manual (21-09) las
ventas quedaron anotadas como tarjeta y el cobro de MP sin registro; el usuario
tiene que poder asignar cada pago a su venta desde «Contra Mercado Pago» sin
esperar al Maestro. La 0234 había dejado estas filas APAGADAS.

Solo a los roles que ya ven Conciliación Mercado Pago (`dineros_mercadopago`).
Otros roles (jefe de local…) se encienden desde Permisos si se quiere: el
servidor los limita a la tienda de su sesión y no les deja convertir efectivo
o transferencia. Reversa: vuelve a apagarlo para esos dos roles.

Escrita a mano. Segura en caliente: solo actualiza/crea dos filas.
"""
from django.db import migrations

CODIGO = 'asociar_pagos_mercadopago'
ROLES = ('administrador', 'administracion')
BASE = {'puede_crear': False, 'puede_eliminar': False, 'puede_exportar': False, 'puede_aprobar': False}


def encender(apps, schema_editor):
    OpcionMenu = apps.get_model('app', 'OpcionMenu')
    PermisoRol = apps.get_model('app', 'PermisoRol')
    opcion = OpcionMenu.objects.filter(codigo=CODIGO).first()
    if opcion is None:
        return
    for rol in ROLES:
        # Sin acceso a Conciliación MP (p. ej. configurar_rol_maestro se lo quitó al
        # Administrador): no se le abre la asignación por la puerta de atrás.
        if not PermisoRol.objects.filter(rol=rol, opcion_menu__codigo='dineros_mercadopago', puede_ver=True).exists():
            continue
        PermisoRol.objects.update_or_create(
            rol=rol, opcion_menu=opcion,
            defaults={**BASE, 'puede_ver': True, 'puede_editar': True},
        )


def apagar(apps, schema_editor):
    PermisoRol = apps.get_model('app', 'PermisoRol')
    PermisoRol.objects.filter(rol__in=ROLES, opcion_menu__codigo=CODIGO).update(
        puede_ver=False, puede_editar=False)


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0234_permisos_asociar_mp_y_edicion_documentos'),
    ]

    operations = [
        migrations.RunPython(encender, apagar),
    ]
