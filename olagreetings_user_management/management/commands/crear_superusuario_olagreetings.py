from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError

Usuario = get_user_model()

class Command(BaseCommand):
    help = 'Crear superusuario para Olagreetings con configuración automática'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            default='admin',
            help='Nombre de usuario del superusuario'
        )
        parser.add_argument(
            '--email',
            type=str,
            default='admin@olagreetings.com',
            help='Email del superusuario'
        )
        parser.add_argument(
            '--password',
            type=str,
            help='Contraseña del superusuario (si no se proporciona, se generará automáticamente)'
        )
        parser.add_argument(
            '--first-name',
            type=str,
            default='Administrador',
            help='Nombre del superusuario'
        )
        parser.add_argument(
            '--last-name',
            type=str,
            default='Sistema',
            help='Apellido del superusuario'
        )
        parser.add_argument(
            '--rut',
            type=str,
            help='RUT del superusuario'
        )
        parser.add_argument(
            '--empresa',
            type=str,
            default='Olagreetings',
            help='Empresa del superusuario'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Forzar la creación incluso si el usuario ya existe'
        )

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        password = options['password']
        first_name = options['first_name']
        last_name = options['last_name']
        rut = options['rut']
        empresa = options['empresa']
        force = options['force']

        # Verificar si el usuario ya existe
        if Usuario.objects.filter(username=username).exists():
            if not force:
                self.stdout.write(
                    self.style.WARNING(
                        f'El usuario "{username}" ya existe. Usa --force para sobrescribir.'
                    )
                )
                return
            else:
                # Eliminar usuario existente
                Usuario.objects.filter(username=username).delete()
                self.stdout.write(
                    self.style.WARNING(f'Usuario "{username}" eliminado y será recreado.')
                )

        # Generar contraseña si no se proporciona
        if not password:
            from django.utils.crypto import get_random_string
            password = get_random_string(12)
            self.stdout.write(
                self.style.SUCCESS(f'Contraseña generada automáticamente: {password}')
            )

        try:
            # Crear superusuario
            usuario = Usuario.objects.create_superuser(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                rut=rut,
                empresa=empresa,
                es_activo=True,
                puede_crear_usuarios=True,
                puede_editar_usuarios=True,
                puede_eliminar_usuarios=True,
                is_staff=True
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f'Superusuario "{username}" creado exitosamente!'
                )
            )
            
            # Mostrar información del usuario creado
            self.stdout.write('\nInformación del superusuario:')
            self.stdout.write(f'  Usuario: {usuario.username}')
            self.stdout.write(f'  Email: {usuario.email}')
            self.stdout.write(f'  Nombre: {usuario.get_full_name()}')
            self.stdout.write(f'  RUT: {usuario.rut or "No especificado"}')
            self.stdout.write(f'  Empresa: {usuario.empresa}')
            self.stdout.write(f'  Contraseña: {password}')
            
            self.stdout.write(
                self.style.WARNING(
                    '\n⚠️  IMPORTANTE: Guarda esta contraseña en un lugar seguro!'
                )
            )

        except IntegrityError as e:
            self.stdout.write(
                self.style.ERROR(f'Error al crear superusuario: {e}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error inesperado: {e}')
            ) 