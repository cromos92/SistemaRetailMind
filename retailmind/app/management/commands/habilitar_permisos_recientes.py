"""
Habilita en el sistema de permisos los 3 cambios recientes del repo y otorga
los permisos pedidos a los roles `administrador` y `jefe_local`:

  1. Gift Cards / Fidelizacion  (modulo `fidelizacion`)
  2. Devolucion por Garantia    (opcion `devolucion_garantia` del modulo Ventas)
  3. Liquidacion                (modulo `liquidacion`: Plan + Campanas)

El comando es IDEMPOTENTE y ADITIVO:
  - Solo CREA opciones/modulos que falten (get_or_create, nunca borra).
  - Solo ENCIENDE permisos (nunca apaga un flag ya activo ni toca otros roles).
  - Se puede correr varias veces sin efectos secundarios.

    python manage.py habilitar_permisos_recientes

IMPORTANTE (cambios de comportamiento que este comando acompana):
  - Aprobar/rechazar una devolucion por garantia ahora se gobierna por el
    permiso `devolucion_garantia.puede_aprobar` (antes era rol fijo
    administrador). Este comando se lo enciende SOLO al administrador; el
    jefe de local queda con crear pero SIN aprobar.
  - Plan de Liquidacion y Campanas de Liquidacion ahora exigen los permisos
    `plan_liquidacion` / `campanas_liquidacion` (antes solo requerian login).
    Hasta correr este comando esas pantallas quedan sin acceso para todos
    (incluido el administrador), asi que debe correrse junto con el deploy.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import ModuloSistema, OpcionMenu, PermisoRol


# Modulos/opciones que deben existir para que las 3 features sean gobernables.
# (codigo_modulo, defaults_modulo, [(codigo_opcion, nombre, url_name, icono, orden), ...])
MODULOS = [
    ('ventas', {
        'nombre': 'Modulo Ventas',
        'descripcion': 'Gestion de ventas y punto de venta',
        'icono': 'ri-money-cny-circle-line',
        'orden': 2,
    }, [
        ('devolucion_garantia', 'Devolucion por Garantia', 'modulo_devolucion_garantia', 'ri-refund-2-line', 3),
    ]),
    ('fidelizacion', {
        'nombre': 'Fidelizacion',
        'descripcion': 'Gift cards y programa de puntos de clientes',
        'icono': 'ri-gift-line',
        'orden': 8,
    }, [
        ('giftcards_listado', 'Gift Cards', None, 'ri-gift-line', 1),
        ('giftcards_emitir', 'Emitir Gift Card', None, 'ri-add-circle-line', 2),
        ('fidelizacion_cuentas', 'Clientes y Puntos', None, 'ri-user-star-line', 3),
        ('fidelizacion_programa', 'Configuracion Programa', None, 'ri-settings-3-line', 4),
        ('fidelizacion_reporte', 'Reporte Fidelizacion', None, 'ri-bar-chart-box-line', 5),
    ]),
    ('liquidacion', {
        'nombre': 'Liquidacion',
        'descripcion': 'Plan de liquidacion de stock y campanas de precios/NxM',
        'icono': 'ri-scissors-cut-line',
        'orden': 11,
    }, [
        ('plan_liquidacion', 'Plan de Liquidacion', 'ver_plan_liquidacion', 'ri-scissors-cut-line', 1),
        ('campanas_liquidacion', 'Campanas de Liquidacion', 'ver_campanas_liquidacion', 'ri-price-tag-2-line', 2),
    ]),
]

FULL = dict(puede_ver=True, puede_crear=True, puede_editar=True,
            puede_eliminar=True, puede_exportar=True, puede_aprobar=True)

# Permisos ADITIVOS por rol: codigo_opcion -> flags a ENCENDER.
GRANTS = {
    'administrador': {
        # Devolucion por garantia: todo, incluido aprobar.
        'devolucion_garantia': FULL,
        # Liquidacion: todo.
        'plan_liquidacion': FULL,
        'campanas_liquidacion': FULL,
        # Gift Cards / Fidelizacion: todo.
        'giftcards_listado': FULL,
        'giftcards_emitir': FULL,
        'fidelizacion_cuentas': FULL,
        'fidelizacion_programa': FULL,
        'fidelizacion_reporte': FULL,
    },
    'jefe_local': {
        # Puede CREAR la solicitud de devolucion/garantia, pero NO aprobar
        # (aprobar queda solo para quien tenga puede_aprobar, i.e. administrador).
        'devolucion_garantia': dict(puede_ver=True, puede_crear=True,
                                    puede_editar=True, puede_exportar=True),
        # Liquidacion: ve el reporte y gestiona campanas de su(s) sucursal(es).
        'plan_liquidacion': dict(puede_ver=True, puede_exportar=True),
        'campanas_liquidacion': dict(puede_ver=True, puede_crear=True,
                                     puede_editar=True, puede_exportar=True),
        # Gift Cards / Fidelizacion (sin configuracion del programa).
        'giftcards_listado': dict(puede_ver=True),
        'giftcards_emitir': dict(puede_ver=True, puede_crear=True),
        'fidelizacion_cuentas': dict(puede_ver=True),
        'fidelizacion_reporte': dict(puede_ver=True),
    },
}


class Command(BaseCommand):
    help = ('Registra Gift Cards, Devolucion por Garantia y Liquidacion en el '
            'sistema de permisos y otorga los permisos a administrador y '
            'jefe_local (idempotente y aditivo).')

    def handle(self, *args, **options):
        with transaction.atomic():
            self._asegurar_opciones()
            self._aplicar_grants()
        self.stdout.write(self.style.SUCCESS(
            '\n>> Listo. Verifica en /app/permisos/gestion/ (rol Administrador y Jefe Local).'))

    def _asegurar_opciones(self):
        self.stdout.write(self.style.MIGRATE_HEADING('== Asegurando modulos/opciones =='))
        for cod_mod, defaults_mod, opciones in MODULOS:
            modulo, mod_creado = ModuloSistema.objects.get_or_create(
                codigo=cod_mod, defaults=defaults_mod)
            if mod_creado:
                self.stdout.write(f'   [modulo]  {cod_mod:<14} creado')
            for codigo, nombre, url_name, icono, orden in opciones:
                _, creada = OpcionMenu.objects.get_or_create(
                    codigo=codigo,
                    defaults={'modulo': modulo, 'nombre': nombre,
                              'url_name': url_name, 'icono': icono, 'orden': orden},
                )
                marca = 'CREADA' if creada else 'ya existia'
                self.stdout.write(f'   [opcion]  {codigo:<24} {marca}')

    def _aplicar_grants(self):
        for rol, permisos in GRANTS.items():
            self.stdout.write(self.style.MIGRATE_HEADING(f'== Permisos rol: {rol} =='))
            for codigo, flags in permisos.items():
                opcion = OpcionMenu.objects.filter(codigo=codigo).first()
                if not opcion:
                    self.stdout.write(self.style.WARNING(
                        f'   ! {codigo}: opcion inexistente, se omite'))
                    continue
                permiso, creado = PermisoRol.objects.get_or_create(
                    rol=rol, opcion_menu=opcion)
                cambios = []
                for campo, valor in flags.items():
                    if valor and not getattr(permiso, campo):
                        setattr(permiso, campo, valor)
                        cambios.append(campo)
                if creado or cambios:
                    permiso.save()
                if creado:
                    estado = self.style.SUCCESS('permiso NUEVO')
                elif cambios:
                    estado = self.style.SUCCESS('encendidos: ' + ', '.join(cambios))
                else:
                    estado = 'sin cambios (ya estaba)'
                self.stdout.write(f'   {codigo:<24} -> {estado}')
