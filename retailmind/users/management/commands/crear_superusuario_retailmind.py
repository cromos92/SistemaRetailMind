from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

Usuario = get_user_model()

class Command(BaseCommand):
    help = 'Crear superusuario para RetailMind'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, help='Nombre de usuario')
        parser.add_argument('--email', type=str, help='Email del usuario')
        parser.add_argument('--first-name', type=str, help='Nombre')
        parser.add_argument('--last-name', type=str, help='Apellido')
        parser.add_argument('--empresa', type=str, help='Empresa')
        parser.add_argument('--password', type=str, help='Contraseña (opcional)')

    def handle(self, *args, **options):
        username = options.get('username') or input('Nombre de usuario: ')
        email = options.get('email') or input('Email: ')
        first_name = options.get('first_name') or input('Nombre: ')
        last_name = options.get('last_name') or input('Apellido: ')
        empresa = options.get('empresa') or input('Empresa (opcional): ')
        password = options.get('password')

        if not password:
            import getpass
            password = getpass.getpass('Contraseña: ')
            password_confirm = getpass.getpass('Confirmar contraseña: ')
            
            if password != password_confirm:
                self.stdout.write(
                    self.style.ERROR('Las contraseñas no coinciden')
                )
                return

        # Verificar si el usuario ya existe
        if Usuario.objects.filter(username=username).exists():
            self.stdout.write(
                self.style.ERROR(f'El usuario "{username}" ya existe')
            )
            return

        if Usuario.objects.filter(email=email).exists():
            self.stdout.write(
                self.style.ERROR(f'El email "{email}" ya está registrado')
            )
            return

        try:
            with transaction.atomic():
                usuario = Usuario.objects.create_superuser(
                    username=username,
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    empresa=empresa,
                    es_activo=True,
                    puede_crear_usuarios=True,
                    puede_editar_usuarios=True,
                    puede_eliminar_usuarios=True
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f'Superusuario "{usuario.username}" creado exitosamente'
                    )
                )
                self.stdout.write(f'Nombre completo: {usuario.get_full_name()}')
                self.stdout.write(f'Email: {usuario.email}')
                if empresa:
                    self.stdout.write(f'Empresa: {empresa}')

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error al crear superusuario: {str(e)}')
            )
