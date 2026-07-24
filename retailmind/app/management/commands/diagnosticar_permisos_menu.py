"""
Diagnóstico SOLO LECTURA del menú: por qué un rol NO ve una opción.

No escribe absolutamente nada en la base de datos.

    python manage.py diagnosticar_permisos_menu --rol administrador
    python manage.py diagnosticar_permisos_menu --rol administrador --sucursal 5
    python manage.py diagnosticar_permisos_menu --rol jefe_local --solo-ocultas

Reproduce la MISMA lógica que usa el menú (`PermisoRol.tiene_permiso` vía
`app/templatetags/permisos_tags.py`) y para cada opción oculta dice el motivo:

  SIN_FILA        -> no existe PermisoRol(rol, opcion): nunca se sembró.
  VER_FALSE       -> existe la fila pero puede_ver=False.
  SUCURSAL_BLOQUEA-> PermisoSucursal(sucursal, opcion).habilitado=False
                     (bloquea incluso al administrador).
  OPCION_INACTIVA -> OpcionMenu.activo=False.
"""
from django.core.management.base import BaseCommand

from app.models import (
    ModuloSistema, OpcionMenu, PermisoRol, PermisoSucursal, Sucursal,
)


class Command(BaseCommand):
    help = 'Diagnostica (solo lectura) qué opciones de menú ve un rol y por qué no ve las demás.'

    def add_arguments(self, parser):
        parser.add_argument('--rol', default='administrador',
                            help='Rol a diagnosticar (default: administrador)')
        parser.add_argument('--sucursal', type=int, default=None,
                            help='ID de sucursal para evaluar PermisoSucursal (opcional)')
        parser.add_argument('--solo-ocultas', action='store_true',
                            help='Listar únicamente las opciones que NO se ven')

    def handle(self, *args, **opts):
        rol = opts['rol']
        sucursal_id = opts['sucursal']
        solo_ocultas = opts['solo_ocultas']

        sucursal = None
        if sucursal_id:
            sucursal = Sucursal.objects.filter(id=sucursal_id).first()
            if not sucursal:
                self.stdout.write(self.style.ERROR(f'Sucursal {sucursal_id} no existe'))
                return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'== Diagnóstico de menú — rol="{rol}"'
            + (f' — sucursal="{sucursal.alias}" (id={sucursal.id})' if sucursal else ' — sin sucursal')
            + ' =='))

        permisos = {p.opcion_menu_id: p for p in
                    PermisoRol.objects.filter(rol=rol).select_related('opcion_menu')}
        bloqueos_suc = {}
        if sucursal:
            bloqueos_suc = {ps.opcion_menu_id: ps for ps in
                            PermisoSucursal.objects.filter(sucursal=sucursal)}

        total_ver = total_oculta = 0
        resumen_modulos = []

        for modulo in ModuloSistema.objects.all().order_by('orden', 'nombre'):
            opciones = OpcionMenu.objects.filter(modulo=modulo).order_by('orden', 'nombre')
            if not opciones.exists():
                continue

            filas, n_ver = [], 0
            for op in opciones:
                motivo = None
                if not op.activo:
                    motivo = 'OPCION_INACTIVA'
                else:
                    p = permisos.get(op.id)
                    if p is None:
                        motivo = 'SIN_FILA'
                    elif not p.puede_ver:
                        motivo = 'VER_FALSE'
                    else:
                        ps = bloqueos_suc.get(op.id)
                        if ps is not None and not ps.habilitado:
                            motivo = 'SUCURSAL_BLOQUEA'

                if motivo is None:
                    n_ver += 1
                    total_ver += 1
                    if not solo_ocultas:
                        filas.append(('  OK  ', op.codigo, ''))
                else:
                    total_oculta += 1
                    filas.append((' OCULTA', op.codigo, motivo))

            estado_mod = 'MÓDULO OCULTO (0 opciones visibles)' if n_ver == 0 else f'{n_ver}/{opciones.count()} visibles'
            resumen_modulos.append((modulo.codigo, n_ver, opciones.count()))

            if filas:
                marca = self.style.ERROR(estado_mod) if n_ver == 0 else estado_mod
                if not modulo.activo:
                    marca = self.style.ERROR('MÓDULO DESACTIVADO (activo=False)')
                self.stdout.write(f'\n[{modulo.codigo}] {modulo.nombre} — {marca}')
                for estado, codigo, motivo in filas:
                    linea = f'   {estado} {codigo:<32}'
                    if motivo:
                        linea += self.style.WARNING(motivo)
                    self.stdout.write(linea)

        self.stdout.write(self.style.MIGRATE_HEADING('\n== Resumen por módulo =='))
        for cod, n_ver, n_tot in resumen_modulos:
            txt = f'   {cod:<18} {n_ver}/{n_tot}'
            self.stdout.write(self.style.ERROR(txt + '   <-- MÓDULO NO APARECE') if n_ver == 0 else txt)

        self.stdout.write(f'\nTotal visibles: {total_ver} | ocultas: {total_oculta}')
        self.stdout.write(self.style.SUCCESS('(diagnóstico solo lectura: no se modificó nada)'))
