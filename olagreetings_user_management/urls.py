from django.urls import path
from . import views

app_name = 'usuarios'

urlpatterns = [
    # Vistas principales
    path('gestion/', views.gestion_usuarios, name='gestion_usuarios'),
    path('listar/', views.listar_usuarios, name='listar_usuarios'),
    
    # CRUD de usuarios
    path('crear/', views.crear_usuario, name='crear_usuario'),
    path('gestionar/<int:usuario_id>/', views.gestionar_usuario, name='gestionar_usuario'),
    
    # Acciones específicas
    path('resetear-password/<int:usuario_id>/', views.resetear_password, name='resetear_password'),
    path('toggle-estado/<int:usuario_id>/', views.activar_desactivar_usuario, name='toggle_estado'),
    
    # Exportación
    path('exportar/', views.exportar_usuarios, name='exportar_usuarios'),
    
    # Logs de acceso
    path('logs/', views.logs_acceso, name='logs_acceso'),
    path('logs/obtener/', views.obtener_logs_acceso, name='obtener_logs_acceso'),
] 