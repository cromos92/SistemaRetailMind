from retailmind import settings
from . import views
from django.urls import path
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
urlpatterns = [
        
     path('home/', views.verHome, name='verHome'),
     path('ruta_a_check_session/', views.verHome, name='check_session'),
     path('verResetPassword/', views.ver_resetPassword, name='verResetPassword'),
     #Modulo Compras
     path('verGestionCompras/', views.verGestionCompras, name='verGestionCompras'),
     path('dashboard_compras_estrategico/', views.dashboard_compras_estrategico, name='dashboard_compras_estrategico'),
         path('exportar_dashboard_compras/', views.exportar_dashboard_compras, name='exportar_dashboard_compras'),
    path('verDashboardCompras/', views.verDashboardCompras, name='verDashboardCompras'),
    path('diagnostico_datos_compras/', views.diagnostico_datos_compras, name='diagnostico_datos_compras'),
    path('verDiagnosticoCompras/', views.verDiagnosticoCompras, name='verDiagnosticoCompras'),
     path('obtenerDetalleComprasPorParametros/', views.obtenerDetalleComprasPorParametros, name='obtenerDetalleComprasPorParametros'),
     path('crear_compra/', views.crear_compra, name='crear_compra'),
     path('obtener_compras/', views.obtener_compras_por_anio, name='obtener_compras'),
     path('verGestionProducto/', views.verGestionProducto, name='verGestionProducto'),
     path('importar_csv_compra/', views.importar_csv_compra, name='importar_csv_compra'),
     path('compra/recepcionar/', views.recepcionar_compra, name='recepcionar_compra'),
     path('verGestionDteCompras/', views.verGestionDteCompras, name='verGestionDteCompras'),
     path('crearDteCompras/', views.crearDteCompras, name='crearDteCompras'),
     path('empresas_proveedoras/', views.empresas_proveedoras, name='empresas_proveedoras'),
     path('cargarDteCompra/', views.cargarDteCompra, name='cargarDteCompra'),
     path('registrarPagoDTE/', views.registrarPagoDTE, name='registrarPagoDTE'),
     path('obtenerDetallePago/<int:dte_id>/', views.obtenerDetallePago, name='obtenerDetallePago'),
     path('pagosDTE/<int:dte_id>/', views.pagosDTE, name='pagosDTE'),
     path('eliminarPago/<int:pago_id>/', views.eliminarPago, name='eliminarPago'),
     path('detallePago/<int:pago_id>/', views.detallePago, name='detallePago'),
     path('editarPago/<int:pago_id>/', views.editarPago, name='editarPago'),
     path('notasCredito/<int:dte_id>/', views.notasCredito, name='notasCredito'),
     path('agregarNC/', views.agregarNotaCredito, name='agregarNotaCredito'),
     path('eliminarNC/<int:nc_id>/', views.eliminarNotaCredito, name='eliminarNotaCredito'),
     path('obtenerDTE/<int:dte_id>/', views.obtener_dte, name='obtener_dte'),
     path('eliminarDTE/<int:dte_id>/', views.eliminar_dte, name='eliminar_dte'),
     path('guardar_recepcion/', views.guardar_recepcion, name='guardar_recepcion'),
     path('agregar_producto_manual/', views.agregar_producto_manual, name='agregar_producto_manual'),
     path('productos_recepcionados/', views.productos_recepcionados, name='productos_recepcionados'),
     path('productos_para_crear/', views.obtener_productos_para_crear, name='productos_para_crear'),
     path('detalle_producto_para_crear/<int:producto_id>/', views.detalle_producto_para_crear, name='detalle_producto_para_crear'),
     path('opciones_atributo/', views.opciones_atributo, name='opciones_atributo'),
     path('opcion_atributo_crear/', views.opcion_atributo_crear, name='opcion_atributo_crear'),
     path('guardar_margenes_usuario/', views.guardar_margenes_usuario, name='guardar_margenes_usuario'),
     path('ajustar_margenes/', views.ajustar_margenes, name='ajustar_margenes'),
     path('margenes_usuario/', views.margenes_usuario, name='margenes_usuario'),
     path('categorias_existentes/', views.categorias_existentes, name='categorias_existentes'),
     path('categoria_guardar/', views.categoria_guardar, name='categoria_guardar'),
     path('guias_talla/', views.guias_talla_list, name='guias_talla'),
     path('crear_guia_talla/', views.crear_guia_talla, name='crear_guia_talla'),
     path('guia_talla_detalle/<int:id>/', views.guia_talla_detalle, name='guia_talla_detalle'),
     path('eliminar_guia_talla/', views.eliminar_guia_talla, name='eliminar_guia_talla'),
     path('app/guias_talla_por_marca/', views.guias_talla_por_marca, name='guias_talla_por_marca'),
     path('app/verificar_producto_existente/', views.verificar_existencia_producto, name='verificar_producto_existente'),
     path('obtener_siguiente_sku/', views.obtener_siguiente_sku_view, name='obtener_siguiente_sku'),
     path('verificar_producto_existente/', views.verificar_producto_existente, name='verificar_producto_existente'),
     path('crear_producto_desde_recepcion/', views.crear_producto_desde_recepcion, name='crear_producto_desde_recepcion'),
     path('app/sku_para_talla/', views.sku_para_talla, name='sku_para_talla'),
     path('verMovimientosProducto/', views.verMovimientosProducto, name='verMovimientosProducto'),
     path('obtener_movimientos_producto/', views.obtener_movimientos_producto, name='obtener_movimientos_producto'),
     path('obtener_productos/', views.obtener_productos, name='obtener_productos'),
     
     # ========== NUEVAS URLs PARA MOVIMIENTOS ==========
     
     # === VENTAS AL PÚBLICO ===
     path('crear_ticket_venta/', views.crear_ticket_venta, name='crear_ticket_venta'),
     path('obtener_tickets_venta/', views.obtener_tickets_venta, name='obtener_tickets_venta'),
     
     # === TRASPASOS ===
     path('crear_traspaso/', views.crear_traspaso, name='crear_traspaso'),
     path('aprobar_traspaso/', views.aprobar_traspaso, name='aprobar_traspaso'),
     path('recibir_traspaso/', views.recibir_traspaso, name='recibir_traspaso'),
     
     # === AJUSTES DE INVENTARIO ===
     path('crear_ajuste_inventario/', views.crear_ajuste_inventario, name='crear_ajuste_inventario'),
     
     # === REPORTES ===
     path('reporte_movimientos_kardex/', views.reporte_movimientos_kardex, name='reporte_movimientos_kardex'),
     
     # === URLs EXISTENTES PARA FACTURAS ===
     path('facturas_pendientes/', views.facturas_pendientes, name='facturas_pendientes'),
     path('reporte_despachos_por_proveedor/', views.reporte_despachos_por_proveedor, name='reporte_despachos_por_proveedor'),
     path('obtener_proveedores_para_reporte/', views.obtener_proveedores_para_reporte, name='obtener_proveedores_para_reporte'),
     path('verReporteDespachosProveedor/', views.verReporteDespachosProveedor, name='verReporteDespachosProveedor'),
     
     # ========== URLs PARA CREACIÓN MANUAL DE PRODUCTOS ==========
     path('proveedores/', views.obtener_proveedores, name='obtener_proveedores'),
     path('dtes_por_proveedor/<int:proveedor_id>/', views.obtener_dtes_por_proveedor, name='obtener_dtes_por_proveedor'),
     path('crear_producto_manual/', views.crear_producto_manual, name='crear_producto_manual'),
     
     # ========== URLs PARA GESTIÓN DE PROVEEDORES ==========
     path('crear_proveedor/', views.crear_proveedor, name='crear_proveedor'),
     path('gestionar_proveedor/<int:proveedor_id>/', views.gestionar_proveedor, name='gestionar_proveedor'),
     path('listar_proveedores/', views.listar_proveedores, name='listar_proveedores'),
     
     # ========== URLs PARA BUSCADOR DE PRODUCTOS EXISTENTES ==========
     path('buscar_productos_existentes/', views.buscar_productos_existentes, name='buscar_productos_existentes'),
     path('detalle_producto_para_copiar/<int:producto_id>/', views.detalle_producto_para_copiar, name='detalle_producto_para_copiar'),
     path('tallas_producto/<int:producto_id>/', views.tallas_producto, name='tallas_producto'),

    # === URLs FIFO ===
    path('lotes_producto/<int:producto_talla_id>/', views.ver_lotes_producto, name='ver_lotes_producto'),
    path('obtener_lotes_producto/<int:producto_talla_id>/', views.obtener_lotes_producto, name='obtener_lotes_producto'),
    path('crear_lote_manual/', views.crear_lote_manual, name='crear_lote_manual'),
    path('ajustar_lote/<int:lote_id>/', views.ajustar_lote, name='ajustar_lote'),
    path('reporte_fifo_general/', views.reporte_fifo_general, name='reporte_fifo_general'),
    path('dashboard_fifo/', views.dashboard_fifo, name='dashboard_fifo'),
    
    # === URLs AJAX PARA DASHBOARD FIFO ===
    path('obtener_datos_dashboard_fifo/', views.obtener_datos_dashboard_fifo, name='obtener_datos_dashboard_fifo'),
    path('obtener_metricas_fifo/', views.obtener_metricas_fifo, name='obtener_metricas_fifo'),
    path('exportar_dashboard_fifo/', views.exportar_dashboard_fifo, name='exportar_dashboard_fifo'),
    path('obtener_analisis_fifo_detallado/', views.obtener_analisis_fifo_detallado, name='obtener_analisis_fifo_detallado'),

    # === URLs PARA DASHBOARD DE PRODUCTOS ===
    path('dashboard_productos/', views.dashboard_productos, name='dashboard_productos'),
    path('obtener_datos_dashboard_productos/', views.obtener_datos_dashboard_productos, name='obtener_datos_dashboard_productos'),
    path('filtrar_productos_dashboard/', views.filtrar_productos_dashboard, name='filtrar_productos_dashboard'),
    path('exportar_dashboard_productos/', views.exportar_dashboard_productos, name='exportar_dashboard_productos'),
    path('exportar_productos_filtrado/', views.exportar_productos_filtrado, name='exportar_productos_filtrado'),

    # === URLs PARA GESTIÓN DE VENDEDORES ===
    path('gestion_vendedores/', views.gestion_vendedores, name='gestion_vendedores'),
    path('obtener_vendedores/', views.obtener_vendedores, name='obtener_vendedores'),
    path('obtener_metricas_vendedores/', views.obtener_metricas_vendedores, name='obtener_metricas_vendedores'),
    path('crear_vendedor/', views.crear_vendedor, name='crear_vendedor'),
    path('editar_vendedor/', views.editar_vendedor, name='editar_vendedor'),
    path('eliminar_vendedor/<int:vendedor_id>/', views.eliminar_vendedor, name='eliminar_vendedor'),
    path('exportar_vendedores/', views.exportar_vendedores, name='exportar_vendedores'),

]

 

 
