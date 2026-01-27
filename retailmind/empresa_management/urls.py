from django.urls import path
from . import views, views_clientes

app_name = 'empresa_management'

urlpatterns = [
    # ========== URLs PARA EMPRESAS ==========
    
    # Lista y dashboard (URLs principales que coinciden con el menú)
    path('lista_empresas/', views.lista_empresas, name='lista_empresas'),
    path('dashboard/', views.dashboard_empresas, name='dashboard_empresas'),
    path('exportar_empresas/', views.exportar_empresas, name='exportar_empresas'),
    path('exportar_empresas_sucursales/', views.exportar_empresas_con_sucursales, name='exportar_empresas_sucursales'),
    path('importar_empresas_sucursales/', views.importar_empresas_con_sucursales, name='importar_empresas_sucursales'),
    
    # CRUD empresas
    path('crear_empresa/', views.crear_empresa, name='crear_empresa'),
    path('editar_empresa/', views.editar_empresa, name='editar_empresa'),
    path('eliminar_empresa/<int:empresa_id>/', views.eliminar_empresa, name='eliminar_empresa'),
    path('activar_desactivar_empresa/<int:empresa_id>/', views.activar_desactivar_empresa, name='activar_desactivar_empresa'),
    path('obtener_empresa/<int:empresa_id>/', views.obtener_empresa, name='obtener_empresa'),
    
    # Sucursales
    path('empresas/<int:empresa_id>/sucursales/', views.listar_sucursales, name='listar_sucursales'),
    path('empresas/<int:empresa_id>/sucursales/crear/', views.crear_sucursal, name='crear_sucursal'),
    path('sucursales/<int:sucursal_id>/editar/', views.editar_sucursal, name='editar_sucursal'),
    path('sucursales/<int:sucursal_id>/eliminar/', views.eliminar_sucursal, name='eliminar_sucursal'),
    
    # Contactos
    path('empresas/<int:empresa_id>/contactos/crear/', views.crear_contacto, name='crear_contacto'),
    path('contactos/<int:contacto_id>/eliminar/', views.eliminar_contacto, name='eliminar_contacto'),
    
    # ========== URLs PARA CLIENTES ==========
    
    # Lista y dashboard (URLs principales que coinciden con el menú)
    path('lista_clientes/', views_clientes.lista_clientes, name='lista_clientes'),
    path('dashboard_clientes/', views_clientes.dashboard_clientes, name='dashboard_clientes'),
    path('exportar_clientes/', views_clientes.exportar_clientes, name='exportar_clientes'),
    
    # CRUD clientes
    path('crear_cliente/', views_clientes.crear_cliente, name='crear_cliente'),
    path('editar_cliente/', views_clientes.editar_cliente, name='editar_cliente'),
    path('eliminar_cliente/<int:cliente_id>/', views_clientes.eliminar_cliente, name='eliminar_cliente'),
    path('activar_desactivar_cliente/<int:cliente_id>/', views_clientes.activar_desactivar_cliente, name='activar_desactivar_cliente'),
    path('obtener_cliente/<int:cliente_id>/', views_clientes.obtener_cliente, name='obtener_cliente'),
    
    # Reportes
    path('empresas/<int:empresa_id>/clientes/reporte/', views_clientes.reporte_clientes_empresa, name='reporte_clientes_empresa'),
    
    # Búsqueda AJAX
    path('clientes/buscar/', views_clientes.buscar_clientes_ajax, name='buscar_clientes_ajax'),
    path('api/buscar-empresa/', views.buscar_empresa_ajax, name='buscar_empresa_ajax'),
] 