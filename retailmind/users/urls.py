from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # Vistas principales
    path('gestion/', views.gestion_usuarios, name='gestion_usuarios'),
    path('listar/', views.listar_usuarios, name='listar_usuarios'),
    
    # CRUD de usuarios
    path('crear/', views.crear_usuario, name='crear_usuario'),
    path('editar/<int:usuario_id>/', views.editar_usuario, name='editar_usuario'),
    path('obtener/<int:usuario_id>/', views.obtener_usuario, name='obtener_usuario'),
    
    # Acciones específicas
    path('resetear-password/<int:usuario_id>/', views.resetear_password, name='resetear_password'),
    path('toggle-estado/<int:usuario_id>/', views.toggle_estado_usuario, name='toggle_estado_usuario'),
    
    # Exportación
    path('exportar/', views.exportar_usuarios, name='exportar_usuarios'),
]
