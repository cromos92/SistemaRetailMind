"""
Configura el rol Maestro y los bloqueos del rol Administrador.

SUPERSEDIDO (25-sep-2026) por `configurar_perfiles`, que aplica la política
completa (Administrador conserva Conciliación MP; Jefe, Administración, etc.).
Se conserva por si hace falta el bloqueo antiguo de dineros_mercadopago.

  1. Asigna el rol 'maestro' (acceso total, no configurable) a las cuentas
     indicadas con --maestro.
  2. Bloquea al rol Administrador:
       - Conciliación Mercado Pago  (opción `dineros_mercadopago`)
       - Nota de Crédito a clientes (opción `emitir_nota_credito`)
       - NC de traspasos internos   (opción `emitir_nota_credito_traspaso`;
         se omite con --permitir-nc-traspasos)
     y neutraliza los overrides individuales que se lo devolverían a un
     usuario Administrador (los pasa a "usar rol").
  3. Edición de documentos SOLO Maestro: apaga para TODOS los demás roles los
     permisos de editar fecha / N° / folio / pagos / vendedor / tipo de un
     documento, eliminar o anular documentos de venta, y editar / eliminar
     pagos o documentos de compra (y neutraliza overrides SI). Se omite con
     --mantener-edicion-documentos.

Por defecto es una VISTA PREVIA: muestra el estado actual y lo que cambiaría.
Solo escribe con --aplicar, en una transacción.

    python manage.py configurar_rol_maestro
    python manage.py configurar_rol_maestro --maestro jperez --aplicar
    python manage.py configurar_rol_maestro --solo-maestro --maestro jperez --aplicar

Requiere las migraciones app 0233 y 0234. Para revertir un bloqueo, el
Maestro lo reactiva en /app/permisos/gestion/.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from app.models import (
    OpcionMenu, PermisoRol, PermisoUsuario, ROL_MAESTRO,
    CODIGO_NC_CLIENTES, CODIGO_NC_TRASPASO, CODIGOS_EDICION_DOCUMENTOS,
)
from users.models import Usuario


TIPOS = ('puede_ver', 'puede_crear', 'puede_editar',
         'puede_eliminar', 'puede_exportar', 'puede_aprobar')

BLOQUEOS_ADMIN = [
    ('dineros_mercadopago', 'Conciliación Mercado Pago'),
    (CODIGO_NC_CLIENTES, 'Nota de Crédito a clientes'),
    (CODIGO_NC_TRASPASO, 'NC de traspasos internos (recepción)'),
]


class Command(BaseCommand):
    help = ('[Supersedido por configurar_perfiles] Asigna el rol Maestro y bloquea al Administrador la Conciliación '
            'Mercado Pago y la emisión de Notas de Crédito (vista previa por defecto).')

    def add_arguments(self, parser):
        parser.add_argument('--maestro', action='append', default=[], metavar='USUARIO',
                            help='username o email de la cuenta que pasa a Maestro (repetible).')
        parser.add_argument('--aplicar', action='store_true',
                            help='Escribe los cambios. Sin esto solo muestra la vista previa.')
        parser.add_argument('--solo-maestro', action='store_true',
                            help='Solo asigna Maestro; no toca los permisos del Administrador.')
        parser.add_argument('--permitir-nc-traspasos', action='store_true',
                            help='Deja al Administrador emitir las NC de traspasos internos (recepción).')
        parser.add_argument('--mantener-edicion-documentos', action='store_true',
                            help='No toca los permisos de editar/eliminar documentos y sus pagos.')

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        titulo = 'APLICANDO' if aplicar else 'VISTA PREVIA (no escribe nada; agrega --aplicar)'
        self.stdout.write(self.style.MIGRATE_HEADING(f'== configurar_rol_maestro — {titulo} =='))

        cuentas = self._resolver_cuentas(opts['maestro'])
        bloqueos = [] if opts['solo_maestro'] else [
            (c, n) for c, n in BLOQUEOS_ADMIN
            if not (opts['permitir_nc_traspasos'] and c == CODIGO_NC_TRASPASO)
        ]
        edicion = [] if (opts['solo_maestro'] or opts['mantener_edicion_documentos']) else list(CODIGOS_EDICION_DOCUMENTOS)
        opciones = {o.codigo: o for o in OpcionMenu.objects.filter(
            codigo__in=[c for c, _ in bloqueos] + edicion)}
        faltan = [c for c, _ in bloqueos if c not in opciones]
        faltan += [c for c in ('dte_eliminar_documento', 'dte_compras_pagos') if edicion and c not in opciones]
        if faltan:
            raise CommandError(
                f'Faltan las opciones {", ".join(faltan)}. Corre primero: python manage.py migrate app')

        self._mostrar_estado()

        with transaction.atomic():
            self._asignar_maestros(cuentas)
            for codigo, nombre in bloqueos:
                self._bloquear_admin(opciones[codigo], nombre)
            if edicion:
                self._edicion_solo_maestro([opciones[c] for c in edicion if c in opciones])
            if not aplicar:
                transaction.set_rollback(True)

        maestros = Usuario.objects.filter(rol=ROL_MAESTRO, es_activo=True, is_active=True).count()
        if aplicar and maestros == 0:
            self.stdout.write(self.style.WARNING(
                '\n!! Nadie tiene el rol Maestro: el rol Administrador queda bloqueado y nadie '
                'podrá reactivarlo desde la pantalla. Corre de nuevo con --maestro <tu_usuario> --aplicar.'))
        if aplicar:
            self.stdout.write(self.style.SUCCESS(
                '\n>> Listo. Verifica en /app/permisos/gestion/ -> pestaña Diagnóstico. '
                'Los usuarios afectados deben recargar la página para ver el menú actualizado.'))
        else:
            self.stdout.write(self.style.WARNING('\n>> Vista previa: no se escribió nada. Agrega --aplicar.'))

    # ------------------------------------------------------------------

    def _resolver_cuentas(self, identificadores):
        cuentas = []
        for ident in identificadores:
            usuario = Usuario.objects.filter(Q(username__iexact=ident) | Q(email__iexact=ident)).first()
            if usuario is None:
                raise CommandError(f'No existe un usuario con username o email "{ident}".')
            if not (usuario.es_activo and usuario.is_active):
                raise CommandError(f'El usuario "{usuario.username}" está inactivo.')
            cuentas.append(usuario)
        return cuentas

    def _mostrar_estado(self):
        self.stdout.write('\nUsuarios activos por rol:')
        for rol, nombre in Usuario.ROLES:
            usuarios = Usuario.objects.filter(rol=rol, es_activo=True, is_active=True).order_by('username')
            nombres = ', '.join(u.username for u in usuarios[:12])
            extra = f' (+{usuarios.count() - 12})' if usuarios.count() > 12 else ''
            self.stdout.write(f'   {nombre:<15} {usuarios.count():>3}  {nombres}{extra}')

    def _asignar_maestros(self, cuentas):
        if not cuentas:
            return
        self.stdout.write(self.style.MIGRATE_HEADING('\n-- Rol Maestro --'))
        for usuario in cuentas:
            if usuario.rol == ROL_MAESTRO:
                self.stdout.write(f'   {usuario.username:<20} ya es Maestro')
                continue
            self.stdout.write(f'   {usuario.username:<20} {usuario.rol} -> maestro')
            usuario.rol = ROL_MAESTRO
            usuario.save(update_fields=['rol'])

    def _bloquear_admin(self, opcion, nombre):
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n-- Administrador: bloquear {nombre} ({opcion.codigo}) --'))
        permiso = PermisoRol.objects.filter(rol='administrador', opcion_menu=opcion).first()
        if permiso is None:
            self.stdout.write('   rol: sin fila (ya sin acceso) -> se crea en blanco para dejarlo explícito')
            PermisoRol.objects.create(rol='administrador', opcion_menu=opcion, **{t: False for t in TIPOS})
        else:
            antes = {t: getattr(permiso, t) for t in TIPOS}
            if not any(antes.values()):
                self.stdout.write('   rol: ya estaba bloqueado')
            else:
                encendidos = ', '.join(t.replace('puede_', '') for t, v in antes.items() if v)
                self.stdout.write(f'   rol: apaga {encendidos}')
                for t in TIPOS:
                    setattr(permiso, t, False)
                permiso.save(update_fields=list(TIPOS))

        self._neutralizar_overrides(opcion, PermisoUsuario.objects.filter(usuario__rol='administrador'))

    def _edicion_solo_maestro(self, opciones):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n-- Edición / eliminación de documentos: solo Maestro --'))
        for opcion in opciones:
            filas = PermisoRol.objects.filter(opcion_menu=opcion).exclude(rol=ROL_MAESTRO)
            con_acceso = [p.rol for p in filas if any(getattr(p, t) for t in TIPOS)]
            if con_acceso:
                self.stdout.write(f'   {opcion.codigo:<38} apaga para: {", ".join(sorted(con_acceso))}')
                filas.update(**{t: False for t in TIPOS})
            else:
                self.stdout.write(f'   {opcion.codigo:<38} ya estaba solo para el Maestro')
            self._neutralizar_overrides(opcion, PermisoUsuario.objects.exclude(usuario__rol=ROL_MAESTRO))

    def _neutralizar_overrides(self, opcion, base):
        # Un override individual en True le devolvería el acceso a esa persona.
        overrides = base.filter(
            opcion_menu=opcion,
        ).filter(
            Q(puede_ver=True) | Q(puede_crear=True) | Q(puede_editar=True)
            | Q(puede_eliminar=True) | Q(puede_exportar=True) | Q(puede_aprobar=True)
        ).select_related('usuario')
        for ov in overrides:
            self.stdout.write(f'   override de {ov.usuario.username}: SI -> "usar rol"')
            for t in TIPOS:
                if getattr(ov, t) is True:
                    setattr(ov, t, None)
            ov.save(update_fields=list(TIPOS))
