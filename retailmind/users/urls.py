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
    path('asignar-sucursal/<int:usuario_id>/', views.asignar_sucursal_sesion, name='asignar_sucursal_sesion'),
    
    # Gestión de asignaciones empresa/sucursal
    path('empresas-sucursales/', views.obtener_empresas_sucursales, name='obtener_empresas_sucursales'),
    path('asignaciones/<int:usuario_id>/', views.obtener_asignaciones_usuario, name='obtener_asignaciones_usuario'),
    path('agregar-asignacion/<int:usuario_id>/', views.agregar_asignacion_usuario, name='agregar_asignacion_usuario'),
    path('eliminar-asignacion/<int:usuario_id>/<int:empresa_user_id>/', views.eliminar_asignacion_usuario, name='eliminar_asignacion_usuario'),
    path('cambiar-activa/<int:usuario_id>/', views.cambiar_sucursal_activa_usuario, name='cambiar_sucursal_activa_usuario'),
    
    # Exportación
    path('exportar/', views.exportar_usuarios, name='exportar_usuarios'),
    
    # Perfil de usuario
    path('mi-perfil/', views.mi_perfil, name='mi_perfil'),
    path('actualizar-perfil/', views.actualizar_perfil, name='actualizar_perfil'),
    path('cambiar-password/', views.cambiar_password, name='cambiar_password'),
    path('subir-foto-perfil/', views.subir_foto_perfil, name='subir_foto_perfil'),
    path('eliminar-foto-perfil/', views.eliminar_foto_perfil, name='eliminar_foto_perfil'),
    
    # Gestión de sesiones
    path('sesiones/', views.listar_sesiones, name='listar_sesiones'),
    path('cerrar-sesion/<int:sesion_id>/', views.cerrar_sesion, name='cerrar_sesion'),
    path('cerrar-todas-sesiones/', views.cerrar_todas_sesiones, name='cerrar_todas_sesiones'),
    
    # Reset de password por correo
    path('solicitar-cambio-password/', views.solicitar_cambio_password_email, name='solicitar_cambio_password'),
    path('reset-password/<uuid:token>/', views.confirmar_cambio_password, name='confirmar_cambio_password'),
    
    # APIs para gestión de permisos
    path('api/usuarios-por-rol/', views.usuarios_por_rol, name='usuarios_por_rol'),
    path('api/cambiar-rol-usuario/', views.cambiar_rol_usuario, name='cambiar_rol_usuario'),
]
