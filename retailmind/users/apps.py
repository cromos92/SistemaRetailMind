from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'
    verbose_name = 'Gestión de Usuarios'
    
    def ready(self):
        # Importar señales si las hay
        pass
