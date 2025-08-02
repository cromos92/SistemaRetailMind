from django.apps import AppConfig


class EmpresaManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'empresa_management'
    verbose_name = 'Gestión de Empresas y Clientes'
    
    def ready(self):
        """Se ejecuta cuando la aplicación está lista"""
        try:
            import empresa_management.signals  # noqa
        except ImportError:
            pass 