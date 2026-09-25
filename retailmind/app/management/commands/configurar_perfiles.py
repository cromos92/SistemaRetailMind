"""
Aplica la política de perfiles (app/services/perfiles_permisos.py):

  Maestro        todo.
  Administrador  todo menos Fidelización, NC a clientes, eliminar documentos y
                 devoluciones Mercado Pago; en Consulta Documentos solo cambia
                 el medio de pago; único que aprueba garantías.
  Jefe           como Administrador, sin Ajuste de Stock, sin cambiar medio de
                 pago y sin aprobar garantías.
  Administración como Jefe de Local + cambiar de tienda.
  Jefe Local / Cajero / Vendedor: lo suyo, sin editar/eliminar documentos.

Por defecto es una VISTA PREVIA: muestra fila por fila qué cambiaría. Solo
escribe con --aplicar. Es idempotente: correrlo dos veces no cambia nada.

    python manage.py configurar_perfiles
    python manage.py configurar_perfiles --aplicar
    python manage.py configurar_perfiles --rol administrador --rol jefe --aplicar
    python manage.py configurar_perfiles --maestro jav.teb@gmail.com --aplicar

Reemplaza a `configurar_rol_maestro` (que dejaba al Administrador sin
Conciliación MP y sin cambiar el medio de pago). Requiere las migraciones
app 0237 y users 0010.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from app.models import OpcionMenu, PermisoRol, ROL_MAESTRO, CODIGO_DEVOLUCION_MP
from app.services import perfiles_permisos as perfiles
from users.models import Usuario


class Command(BaseCommand):
    help = 'Aplica la política de perfiles (Maestro/Administrador/Jefe/Administración/…). Vista previa por defecto.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Escribe los cambios (sin esto solo muestra).')
        parser.add_argument('--rol', action='append', default=[], choices=list(perfiles.PERFILES),
                            help='Solo este rol (repetible). Por defecto, todos.')
        parser.add_argument('--maestro', action='append', default=[], metavar='USUARIO',
                            help='username o email que pasa a Maestro (repetible).')

    def handle(self, *args, **opts):
        aplicar = opts['aplicar']
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"== configurar_perfiles — {'APLICANDO' if aplicar else 'VISTA PREVIA (agrega --aplicar)'} =="))
        if not OpcionMenu.objects.filter(codigo=CODIGO_DEVOLUCION_MP).exists():
            raise CommandError('Falta la opción devolver_mercadopago: corre primero `python manage.py migrate`.')

        for ident in opts['maestro']:
            usuario = Usuario.objects.filter(Q(username__iexact=ident) | Q(email__iexact=ident)).first()
            if usuario is None:
                raise CommandError(f'No existe un usuario con username o email "{ident}".')
            if usuario.rol == ROL_MAESTRO:
                self.stdout.write(f'   {usuario.username}: ya es Maestro')
                continue
            self.stdout.write(f'   {usuario.username}: {usuario.rol} -> maestro')
            if aplicar:
                usuario.rol = ROL_MAESTRO
                usuario.save(update_fields=['rol'])

        roles = opts['rol'] or list(perfiles.ORDEN)
        total = 0
        for rol in roles:
            self.stdout.write(self.style.MIGRATE_HEADING(f'\n-- {rol} --'))
            cambios = perfiles.aplicar(rol, escribir=aplicar)
            for c in cambios:
                self.stdout.write('   ' + c.split(': ', 1)[1])
            if not cambios:
                self.stdout.write('   sin cambios: ya cumple la política')
            total += len(cambios)
            n = PermisoRol.objects.filter(rol=rol, puede_ver=True, opcion_menu__activo=True).count()
            self.stdout.write(f'   → ve {n} opciones')

        maestros = Usuario.objects.filter(rol=ROL_MAESTRO, es_activo=True, is_active=True).count()
        if maestros == 0:
            self.stdout.write(self.style.WARNING(
                '\n!! Nadie tiene el rol Maestro: nadie podrá configurar al Administrador desde la pantalla. '
                'Corre de nuevo con --maestro <tu_usuario> --aplicar.'))
        if aplicar:
            self.stdout.write(self.style.SUCCESS(f'\n>> Listo: {total} cambio(s). Los usuarios deben recargar para ver el menú nuevo.'))
        else:
            self.stdout.write(self.style.WARNING(f'\n>> Vista previa: {total} cambio(s) pendientes. Agrega --aplicar.'))
