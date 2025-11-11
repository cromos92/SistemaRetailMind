from retailmind import settings
from . import views
from django.shortcuts import render
from . import views_modulo_compras
from .views_modulo_ventas import (
    # Funciones POS Dashboard
    pos_dashboard,
    dashboard_stats,
    verificar_correlativos_disponibles,
    validar_rut_cliente,
    buscar_cliente_rut,
    # Funciones Ticket POS
    obtener_ticket_por_correlativo,
    registrar_pagos_ticket,
    ticket_pago_pos,
    buscar_ticket_pos,
    anular_ticket_pendiente,
    # Funciones Gestión Documentos
    gestion_ventas_documentos,
    listar_documentos_ventas,
    convertir_ticket_a_factura,
    detalle_documento_venta,
    anular_documento_venta,
    # Funciones Cuadratura y Arqueo
    cuadratura_caja,
    generar_cuadratura_caja,
    guardar_cuadratura_completa,
    verificar_cuadratura_existente,
    eliminar_cuadratura,
    listar_cuadraturas,
    obtener_detalle_arqueo,
    editar_cuadratura,
    exportar_cuadratura_excel,
    obtener_transacciones_dia,
    listar_arqueos,
    crear_arqueo,
    guardar_conteo_fisico,
    cerrar_arqueo,
    corregir_arqueos_express,
    obtener_arqueo_detalle,
    # Funciones POS Transbank
    gestion_pos_transbank,
    detectar_terminales_pos,
    obtener_configuraciones_pos,
    crear_configuracion_pos,
    probar_conexion_pos,
    iniciar_venta_pos,
    guardar_venta_pos,
    validar_password_usuario,
    completar_transaccion_pos,
    obtener_transacciones_pos,
    anular_transaccion_pos,
    obtener_logs_pos,
    # Funciones Cambios y Devoluciones
    gestion_cambios_devoluciones,
    listar_cambios_devoluciones,
    crear_cambio_devolucion,
    obtener_detalle_cambio,
    aprobar_cambio_devolucion,
    cancelar_cambio_devolucion,
    completar_cambio_devolucion,
    buscar_ticket_para_cambio,
    buscar_documento_cambio,
    buscar_productos_para_cambio,
    # Funciones Clientes POS
    guardar_cliente_pos,
    enviar_ticket_email,
    # Funciones Dashboard de Ventas
    dashboard_ventas,
    obtener_indicadores_globales_ventas,
    obtener_ventas_por_vendedor,
    obtener_ventas_por_sucursal,
    obtener_sucursales_dashboard,
    obtener_ventas_por_metodo_pago,
    obtener_analisis_cambios_devoluciones,
    obtener_estado_cuadraturas,
    obtener_productos_mas_vendidos,
    obtener_tendencias_ventas,
    exportar_dashboard_ventas_excel,
)
from .views_modulo_creditos import (
    # Gestión de Créditos
    gestion_creditos,
    crear_credito_trabajador,
    cargar_creditos_trabajadores,
    detalle_credito_trabajador,
    aprobar_credito_trabajador,
    rechazar_credito_trabajador,
    activar_credito_trabajador,
    # Pagos y Firmas
    registrar_pago_credito,
    registrar_firma_credito,
    # Utilidades
    obtener_trabajadores_credito,
    reporte_creditos_trabajadores,
    # Voucher e Integración POS
    imprimir_voucher_credito,
    validar_codigo_credito,
    usar_credito_en_venta,
)
from .views_modulo_gestion_precios import (
    # Vistas principales
    gestion_precios_view,
    revisar_cambios_precios_view,
    edicion_rapida_precios_view,
    # Estadísticas
    obtener_estadisticas,
    # Búsqueda y filtrado
    buscar_productos,
    # Recomendaciones
    obtener_recomendaciones,
    # Actualización de precios
    actualizar_precio,
    modificacion_masiva,
    sincronizar_sucursales,
    # Análisis
    analisis_inventario_antiguo,
    # Endpoints auxiliares
    listar_categorias,
    listar_atributos,
    listar_sucursales,
    obtener_historial_precio,
    buscar_productos_similares_sucursales,
    # Sistema de aprobación
    proponer_cambio_precio,
    obtener_indicadores_precios_pendientes,
    listar_cambios_pendientes,
    revisar_cambio_precio,
    aprobar_cambio_precio,
    rechazar_cambio_precio,
    obtener_notificaciones_precio,
    marcar_notificacion_leida,
    # Debug
    debug_session_precios,
)
from . import views_modulo_documentos
from .views_modulo_cotizaciones import (
    # Vistas principales
    gestion_cotizaciones,
    # APIs de listado
    listar_cotizaciones,
    detalle_cotizacion,
    # APIs de creación y edición
    crear_cotizacion,
    editar_cotizacion,
    # APIs de acciones
    anular_cotizacion,
    convertir_cotizacion_factura,
    # APIs de búsqueda
    buscar_productos_cotizacion,
    # APIs de clientes
    crear_cliente_cotizacion,
)
from .views_edicion_productos import (
    # Edición de productos
    obtener_producto_edicion,
    actualizar_producto,
    actualizar_variacion,
    ajustar_stock,
    obtener_historial_movimientos,
    eliminar_variacion,
    obtener_producto_desde_talla,
)
from .views_transbank_sdk import (
    # Vista principal
    gestion_transbank_pos_sdk,
    # API Transbank POS SDK
    listar_puertos,
    autoconectar,
    conectar,
    conectar_con_reintentos,
    desconectar,
    verificar,
    obtener_info_puerto,
    cargar_llaves,
    venta,
    venta_multicodigo,
    ultima_venta,
    anular,
    totales,
    detalles,
    cerrar_dia,
)
from django.urls import path
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

urlpatterns = [
        
     path('home/', views.verHome, name='verHome'),
     path('ruta_a_check_session/', views.verHome, name='check_session'),
     path('verResetPassword/', views.ver_resetPassword, name='verResetPassword'),
     #Modulo Compras
     path('verGestionCompras/', views.verGestionCompras, name='verGestionCompras'),
     path('dashboard_compras_estrategico/', views_modulo_compras.dashboard_compras_estrategico, name='dashboard_compras_estrategico'),
         path('exportar_dashboard_compras/', views_modulo_compras.exportar_dashboard_compras, name='exportar_dashboard_compras'),
    path('verDashboardCompras/', views_modulo_compras.verDashboardCompras, name='verDashboardCompras'),
    path('diagnostico_datos_compras/', views_modulo_compras.diagnostico_datos_compras, name='diagnostico_datos_compras'),
    path('verDiagnosticoCompras/', views_modulo_compras.verDiagnosticoCompras, name='verDiagnosticoCompras'),
     path('obtenerDetalleComprasPorParametros/', views.obtenerDetalleComprasPorParametros, name='obtenerDetalleComprasPorParametros'),
     path('crear_compra/', views.crear_compra, name='crear_compra'),
     path('obtener_compras/', views.obtener_compras_por_anio, name='obtener_compras'),
     path('verGestionProducto/', views.verGestionProducto, name='verGestionProducto'),
     path('importar_csv_compra/', views.importar_csv_compra, name='importar_csv_compra'),
     path('compra/recepcionar/', views.recepcionar_compra, name='recepcionar_compra'),
     path('verGestionDteCompras/', views.verGestionDteCompras, name='verGestionDteCompras'),
     path('crearDteCompras/', views.crearDteCompras, name='crearDteCompras'),
     path('actualizarDteCompras/<int:dte_id>/', views.actualizarDteCompras, name='actualizarDteCompras'),
     path('empresas_proveedoras/', views_modulo_compras.empresas_proveedoras, name='empresas_proveedoras'),
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
    
    # ========== URLs PARA EDICIÓN DE PRODUCTOS Y STOCK ==========
    path('productos/obtener-para-editar/<int:producto_id>/', obtener_producto_edicion, name='obtener_producto_edicion'),
    path('productos/obtener-producto-desde-talla/<int:talla_id>/', obtener_producto_desde_talla, name='obtener_producto_desde_talla'),
    path('productos/actualizar/<int:producto_id>/', actualizar_producto, name='actualizar_producto'),
    path('productos/variacion/actualizar/<int:variacion_id>/', actualizar_variacion, name='actualizar_variacion'),
    path('productos/variacion/ajustar-stock/<int:variacion_id>/', ajustar_stock, name='ajustar_stock'),
    path('productos/variacion/historial/<int:variacion_id>/', obtener_historial_movimientos, name='obtener_historial_movimientos'),
    path('productos/variacion/eliminar/<int:variacion_id>/', eliminar_variacion, name='eliminar_variacion'),
    
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
     path('reporte_kardex_agrupado/', views.reporte_kardex_agrupado, name='reporte_kardex_agrupado'),
     path('obtener_productos_base/', views.obtener_productos_base, name='obtener_productos_base'),
     
     # === URLs EXISTENTES PARA FACTURAS ===
     path('facturas_pendientes/', views.facturas_pendientes, name='facturas_pendientes'),
     path('reporte_despachos_por_proveedor/', views.reporte_despachos_por_proveedor, name='reporte_despachos_por_proveedor'),
     path('obtener_proveedores_para_reporte/', views.obtener_proveedores_para_reporte, name='obtener_proveedores_para_reporte'),
     path('verReporteDespachosProveedor/', views.verReporteDespachosProveedor, name='verReporteDespachosProveedor'),
     
     # ========== URLs PARA CREACIÓN MANUAL DE PRODUCTOS ==========
    path('proveedores/', views.obtener_proveedores, name='obtener_proveedores'),
    path('dtes_por_proveedor/<int:proveedor_id>/', views.obtener_dtes_por_proveedor, name='obtener_dtes_por_proveedor'),
    path('crear_producto_manual/', views.crear_producto_manual, name='crear_producto_manual'),
    path('actualizar_producto_existente/', views.actualizar_producto_existente, name='actualizar_producto_existente'),
     
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

    # === URLs PARA EMISIÓN DE DTE ===
    path('emisionDTE/', views.emision_dte, name='emision_dte'),
    path('recepcion-dte/', views.recepcion_dte, name='recepcion_dte'),
    path('dte/recepciones_pendientes/', views.recepciones_pendientes_api, name='recepciones_pendientes_api'),
    path('dte/historial_recepciones/', views.historial_recepciones_api, name='historial_recepciones_api'),
    path('dte/confirmar_recepcion/', views.confirmar_recepcion_api, name='confirmar_recepcion_api'),
    path('dte/rechazar_recepcion/', views.rechazar_recepcion_api, name='rechazar_recepcion_api'),
    path('regularizar-recepciones/', views.regularizar_recepciones, name='regularizar_recepciones'),
    path('solicitudes-regularizacion/', views.solicitudes_regularizacion_recibidas, name='solicitudes_regularizacion_recibidas'),
    path('dte/obtener_productos_regularizar/', views.obtener_productos_regularizar, name='obtener_productos_regularizar'),
    path('dte/obtener_solicitudes_recibidas/', views.obtener_solicitudes_recibidas, name='obtener_solicitudes_recibidas'),
    path('dte/documento-regularizacion/<int:recepcion_id>/', views.documento_regularizacion, name='documento_regularizacion'),
    path('dte/obtener_solicitud_producto/<int:producto_id>/', views.obtener_solicitud_producto, name='obtener_solicitud_producto'),
    path('dte/decidir_solicitud/', views.decidir_solicitud_api, name='decidir_solicitud_api'),
    path('dte/buscar_productos_emisor/', views.buscar_productos_emisor, name='buscar_productos_emisor'),
    path('dte/regularizar_producto/', views.regularizar_producto_api, name='regularizar_producto_api'),
    path('dte/obtener_dtes_con_problemas/', views.obtener_dtes_con_problemas, name='obtener_dtes_con_problemas'),
    path('debug_session/', views.debug_session, name='debug_session'),
    path('debug_user_empresas/', views.debug_user_empresas, name='debug_user_empresas'),  # Temporal para debug
    path('empresas_clientes/', views.empresas_clientes, name='empresas_clientes'),
    path('obtener_marcas/', views.obtener_marcas, name='obtener_marcas'),
    path('obtener_categorias/', views.obtener_categorias, name='obtener_categorias'),
    path('obtener_sucursales/', views.obtener_sucursales, name='obtener_sucursales'),
    path('buscar_productos_bodega/', views.buscar_productos_bodega, name='buscar_productos_bodega'),
    path('emitir_dte/', views.emitir_dte, name='emitir_dte'),
    
    # === URLs PARA GESTIÓN DE USUARIOS ===
    path('gestion_usuarios/', views.gestion_usuarios_redirect, name='gestion_usuarios'),
    
    # ========== GESTIÓN DE CAMBIO DE EMPRESA/SUCURSAL ==========
    path('cambiar-empresa/', views.cambiar_empresa, name='cambiar_empresa'),
    path('seleccionar-empresa-sucursal/', views.seleccionar_empresa_sucursal, name='seleccionar_empresa_sucursal'),
    
    # ========== BÚSQUEDA DE PRODUCTOS POR SUCURSAL ==========
    path('productos-sucursal/', views.buscar_productos_sucursal, name='buscar_productos_sucursal'),
    path('api/productos-sucursal/', views.obtener_productos_sucursal, name='obtener_productos_sucursal'),
    path('api/opciones-atributo/', views.obtener_opciones_atributo, name='obtener_opciones_atributo'),
    
    # ========== TICKET DE VENTA ==========
    path('ticket-venta/', views.ticket_venta, name='ticket_venta'),
    path('api/buscar-vendedor/', views.buscar_vendedor_por_codigo, name='buscar_vendedor_por_codigo'),
    path('api/buscar-producto-sku/', views.buscar_producto_por_sku, name='buscar_producto_por_sku'),
    path('api/crear-ticket/', views.crear_ticket, name='crear_ticket'),
    path('api/tickets/<int:correlativo>/', obtener_ticket_por_correlativo, name='obtener_ticket_por_correlativo'),
    path('api/tickets/buscar/', buscar_ticket_pos, name='buscar_ticket_pos'),
    path('api/tickets/<int:correlativo>/pagos/', registrar_pagos_ticket, name='registrar_pagos_ticket'),
    path('api/tickets/anular/', anular_ticket_pendiente, name='anular_ticket_pendiente'),
    path('ticket-pago-pos/', ticket_pago_pos, name='ticket_pago_pos'),

    # ========== NUEVO POS DASHBOARD ==========
    path('pos-dashboard/', pos_dashboard, name='pos_dashboard'),
    path('api/dashboard/stats/', dashboard_stats, name='dashboard_stats'),
    path('api/correlativos/verificar/', verificar_correlativos_disponibles, name='verificar_correlativos_disponibles'),
    path('api/validar-rut/', validar_rut_cliente, name='validar_rut_cliente'),
    path('api/buscar-cliente/', buscar_cliente_rut, name='buscar_cliente_rut'),
    path('api/guardar-cliente-pos/', guardar_cliente_pos, name='guardar_cliente_pos'),
    path('api/enviar-ticket-email/', enviar_ticket_email, name='enviar_ticket_email'),
    
    # === GESTIÓN DE DOCUMENTOS DE VENTAS ===
    path('ventas/documentos/', gestion_ventas_documentos, name='gestion_ventas_documentos'),
    path('api/ventas/documentos/', listar_documentos_ventas, name='listar_documentos_ventas'),
    path('api/ventas/convertir-factura/', convertir_ticket_a_factura, name='convertir_ticket_a_factura'),
    path('api/ventas/documento/<int:documento_id>/', detalle_documento_venta, name='detalle_documento_venta'),
    path('api/ventas/anular-documento/', anular_documento_venta, name='anular_documento_venta'),
    
    # === CUADRATURA Y ARQUEO DE CAJA ===
    path('ventas/cuadratura-caja/', cuadratura_caja, name='cuadratura_caja'),
    path('api/cuadratura/generar/', generar_cuadratura_caja, name='generar_cuadratura_caja'),
    path('api/cuadratura/guardar/', guardar_cuadratura_completa, name='guardar_cuadratura_completa'),
    path('api/cuadratura/verificar-existente/', verificar_cuadratura_existente, name='verificar_cuadratura_existente'),
    path('api/cuadratura/eliminar/<int:arqueo_id>/', eliminar_cuadratura, name='eliminar_cuadratura'),
    path('api/cuadratura/listar/', listar_cuadraturas, name='listar_cuadraturas'),
    path('api/cuadratura/detalle/<int:arqueo_id>/', obtener_detalle_arqueo, name='obtener_detalle_arqueo'),
    path('api/cuadratura/editar/<int:arqueo_id>/', editar_cuadratura, name='editar_cuadratura'),
    path('api/cuadratura/exportar/', exportar_cuadratura_excel, name='exportar_cuadratura_excel'),
    path('api/cuadratura/transacciones-dia/', obtener_transacciones_dia, name='obtener_transacciones_dia'),
    
    # URLs para arqueo mejorado
    path('api/arqueos/', listar_arqueos, name='listar_arqueos'),
    path('api/arqueo/crear/', crear_arqueo, name='crear_arqueo'),
    path('api/arqueo/conteo/', guardar_conteo_fisico, name='guardar_conteo_fisico'),
    path('api/arqueo/cerrar/', cerrar_arqueo, name='cerrar_arqueo'),
    path('api/arqueo/corregir-express/', corregir_arqueos_express, name='corregir_arqueos_express'),
    path('api/arqueo/<int:arqueo_id>/', obtener_arqueo_detalle, name='obtener_arqueo_detalle'),

    # ========== MÓDULO DOCUMENTOS ==========
    # === Gestión de DTEs ===
    path('documentos/gestion-dte/', views.gestion_dte, name='gestion_dte'),
    path('documentos/api/cargar-dte-ventas/', views.cargar_dte_ventas, name='cargar_dte_ventas'),
    path('documentos/api/dte/<int:dte_id>/', views.detalle_dte, name='detalle_dte'),
    
    # === Gestión de Correlativos ===
    path('documentos/gestion-correlativos/', views.gestion_correlativos, name='gestion_correlativos'),
    path('correlativos/guardar/', views.guardar_correlativo, name='guardar_correlativo'),
    path('correlativos/obtener/<int:correlativo_id>/', views.obtener_correlativo, name='obtener_correlativo'),
    path('correlativos/renovar/', views.renovar_correlativo, name='renovar_correlativo'),
    path('correlativos/historial/<int:correlativo_id>/', views.historial_correlativo, name='historial_correlativo'),
    
    # === Gestión de Créditos ===
    path('documentos/gestion-creditos/', views_modulo_documentos.gestion_creditos_documentos, name='gestion_creditos_documentos'),

    # ========== MÓDULO DE CRÉDITOS A TRABAJADORES ==========
    path('creditos/', gestion_creditos, name='gestion_creditos'),
    path('api/creditos/crear/', crear_credito_trabajador, name='crear_credito_trabajador'),
    path('api/creditos/cargar/', cargar_creditos_trabajadores, name='cargar_creditos_trabajadores'),
    path('api/creditos/<int:credito_id>/', detalle_credito_trabajador, name='detalle_credito_trabajador'),
    path('api/creditos/aprobar/', aprobar_credito_trabajador, name='aprobar_credito_trabajador'),
    path('api/creditos/rechazar/', rechazar_credito_trabajador, name='rechazar_credito_trabajador'),
    path('api/creditos/activar/', activar_credito_trabajador, name='activar_credito_trabajador'),
    path('api/creditos/pago/', registrar_pago_credito, name='registrar_pago_credito'),
    path('api/creditos/firma/', registrar_firma_credito, name='registrar_firma_credito'),
    path('api/creditos/trabajadores/', obtener_trabajadores_credito, name='obtener_trabajadores_credito'),
    path('api/creditos/reporte/', reporte_creditos_trabajadores, name='reporte_creditos_trabajadores'),
    path('api/creditos/imprimir-voucher/<int:credito_id>/', imprimir_voucher_credito, name='imprimir_voucher_credito'),
    path('api/creditos/validar-codigo/', validar_codigo_credito, name='validar_codigo_credito'),
    path('api/creditos/usar-en-venta/', usar_credito_en_venta, name='usar_credito_en_venta'),

    # ========== MÓDULO POS TRANSBANK (SISTEMA VIEJO - WEBSOCKET) ==========
    # Vista principal WebSocket (movida a /transbank-websocket/)
    path('pos/transbank-websocket/', gestion_pos_transbank, name='gestion_pos_transbank'),
    
    # Página de test del SDK (solo para diagnóstico)
    path('test-transbank-sdk/', lambda request: render(request, 'test_transbank_sdk.html'), name='test_transbank_sdk'),
    
    # APIs de configuración
    path('pos/detectar-terminales/', detectar_terminales_pos, name='detectar_terminales_pos'),
    path('pos/configuraciones/', obtener_configuraciones_pos, name='obtener_configuraciones_pos'),
    path('pos/configuraciones/crear/', crear_configuracion_pos, name='crear_configuracion_pos'),
    path('pos/probar-conexion/', probar_conexion_pos, name='probar_conexion_pos'),
    
    # APIs de transacciones
    path('pos/iniciar-venta/', iniciar_venta_pos, name='iniciar_venta_pos'),
    path('pos/guardar-venta/', guardar_venta_pos, name='guardar_venta_pos'),
    path('pos/completar-transaccion/', completar_transaccion_pos, name='completar_transaccion_pos'),
    path('pos/transacciones/', obtener_transacciones_pos, name='obtener_transacciones_pos'),
    path('pos/anular-transaccion/', anular_transaccion_pos, name='anular_transaccion_pos'),
    
    # APIs de logs
    path('pos/logs/<int:configuracion_id>/', obtener_logs_pos, name='obtener_logs_pos'),
    
    # API de validación
    path('api/validar-password/', validar_password_usuario, name='validar_password_usuario'),

    # ========== MÓDULO POS TRANSBANK SDK (CONEXIÓN SERIAL DIRECTA) ==========
    # Vista principal
    path('pos/transbank/', gestion_transbank_pos_sdk, name='gestion_transbank_pos_sdk'),
    
    # APIs sin base de datos - Conexión directa a puerto serial
    path('pos/transbank/puertos/', listar_puertos, name='transbank_sdk_listar_puertos'),
    path('pos/transbank/autoconectar/', autoconectar, name='transbank_sdk_autoconectar'),
    path('pos/transbank/conectar/', conectar, name='transbank_sdk_conectar'),
    path('pos/transbank/conectar-reintentos/', conectar_con_reintentos, name='transbank_sdk_conectar_reintentos'),
    path('pos/transbank/desconectar/', desconectar, name='transbank_sdk_desconectar'),
    path('pos/transbank/verificar/', verificar, name='transbank_sdk_verificar'),
    path('pos/transbank/info-puerto/', obtener_info_puerto, name='transbank_sdk_info_puerto'),
    path('pos/transbank/cargar-llaves/', cargar_llaves, name='transbank_sdk_cargar_llaves'),
    path('pos/transbank/venta/', venta, name='transbank_sdk_venta'),
    path('pos/transbank/venta-multicodigo/', venta_multicodigo, name='transbank_sdk_venta_multicodigo'),
    path('pos/transbank/ultima-venta/', ultima_venta, name='transbank_sdk_ultima_venta'),
    path('pos/transbank/anular/', anular, name='transbank_sdk_anular'),
    path('pos/transbank/totales/', totales, name='transbank_sdk_totales'),
    path('pos/transbank/detalles/', detalles, name='transbank_sdk_detalles'),
    path('pos/transbank/cerrar-dia/', cerrar_dia, name='transbank_sdk_cerrar_dia'),

    # ========== MÓDULO DE CAMBIOS Y DEVOLUCIONES ==========
    # Vista principal
    path('ventas/cambios-devoluciones/', gestion_cambios_devoluciones, name='gestion_cambios_devoluciones'),
    
    # APIs de gestión de cambios y devoluciones
    path('ventas/api/cambios-devoluciones/', listar_cambios_devoluciones, name='listar_cambios_devoluciones'),
    path('ventas/api/crear-cambio-devolucion/', crear_cambio_devolucion, name='crear_cambio_devolucion'),
    path('ventas/api/cambio-detalle/<int:cambio_id>/', obtener_detalle_cambio, name='obtener_detalle_cambio'),
    path('ventas/api/aprobar-cambio-devolucion/', aprobar_cambio_devolucion, name='aprobar_cambio_devolucion'),
    path('ventas/api/cancelar-cambio-devolucion/', cancelar_cambio_devolucion, name='cancelar_cambio_devolucion'),
    path('ventas/api/completar-cambio-devolucion/', completar_cambio_devolucion, name='completar_cambio_devolucion'),
    
    # APIs de búsqueda
    path('ventas/api/buscar-ticket-cambio/', buscar_ticket_para_cambio, name='buscar_ticket_para_cambio'),
    path('ventas/api/buscar-documento-cambio/', buscar_documento_cambio, name='buscar_documento_cambio'),
    path('ventas/api/buscar-productos-cambio/', buscar_productos_para_cambio, name='buscar_productos_para_cambio'),

    # ========== MÓDULO DE COTIZACIONES ==========
    # Vista principal
    path('cotizaciones/', gestion_cotizaciones, name='gestion_cotizaciones'),
    
    # APIs de listado y consulta
    path('api/cotizaciones/', listar_cotizaciones, name='listar_cotizaciones'),
    path('api/cotizaciones/<int:cotizacion_id>/', detalle_cotizacion, name='detalle_cotizacion'),
    
    # APIs de creación y edición
    path('api/cotizaciones/crear/', crear_cotizacion, name='crear_cotizacion'),
    path('api/cotizaciones/<int:cotizacion_id>/editar/', editar_cotizacion, name='editar_cotizacion'),
    
    # APIs de acciones
    path('api/cotizaciones/anular/', anular_cotizacion, name='anular_cotizacion'),
    path('api/cotizaciones/convertir-factura/', convertir_cotizacion_factura, name='convertir_cotizacion_factura'),
    
    # APIs de búsqueda
    path('api/cotizaciones/buscar-productos/', buscar_productos_cotizacion, name='buscar_productos_cotizacion'),
    
    # APIs de clientes
    path('api/cotizaciones/crear-cliente/', crear_cliente_cotizacion, name='crear_cliente_cotizacion'),

    # ========== DASHBOARD DE VENTAS ==========
    # Vista principal del dashboard
    path('ventas/dashboard/', dashboard_ventas, name='dashboard_ventas'),
    
    # APIs de indicadores y métricas
    path('api/ventas/indicadores-globales/', obtener_indicadores_globales_ventas, name='obtener_indicadores_globales_ventas'),
    path('api/ventas/por-vendedor/', obtener_ventas_por_vendedor, name='obtener_ventas_por_vendedor'),
    path('api/ventas/por-sucursal/', obtener_ventas_por_sucursal, name='obtener_ventas_por_sucursal'),
    path('api/ventas/sucursales-dashboard/', obtener_sucursales_dashboard, name='obtener_sucursales_dashboard'),
    path('api/ventas/por-metodo-pago/', obtener_ventas_por_metodo_pago, name='obtener_ventas_por_metodo_pago'),
    path('api/ventas/analisis-cambios/', obtener_analisis_cambios_devoluciones, name='obtener_analisis_cambios_devoluciones'),
    path('api/ventas/estado-cuadraturas/', obtener_estado_cuadraturas, name='obtener_estado_cuadraturas'),
    path('api/ventas/productos-mas-vendidos/', obtener_productos_mas_vendidos, name='obtener_productos_mas_vendidos'),
    path('api/ventas/tendencias/', obtener_tendencias_ventas, name='obtener_tendencias_ventas'),
    
    # API de exportación
    path('api/ventas/exportar-dashboard/', exportar_dashboard_ventas_excel, name='exportar_dashboard_ventas_excel'),

    # ========== MÓDULO DE GESTIÓN DE PRECIOS ==========
    # Vistas principales
    path('gestion-precios/', gestion_precios_view, name='gestion_precios'),
    path('gestion-precios/edicion-rapida/', edicion_rapida_precios_view, name='edicion_rapida_precios'),
    path('gestion-precios/revisar-pendientes/', revisar_cambios_precios_view, name='revisar_cambios_precios'),
    
    # APIs de estadísticas y análisis
    path('gestion-precios/estadisticas/', obtener_estadisticas, name='gestion_precios_estadisticas'),
    path('gestion-precios/inventario-antiguo/', analisis_inventario_antiguo, name='analisis_inventario_antiguo'),
    
    # APIs de búsqueda y filtrado
    path('gestion-precios/buscar/', buscar_productos, name='buscar_productos_precios'),
    
    # APIs de recomendaciones
    path('gestion-precios/recomendaciones/<int:producto_id>/', obtener_recomendaciones, name='obtener_recomendaciones_precio'),
    
    # APIs de actualización de precios
    path('gestion-precios/actualizar-precio/', actualizar_precio, name='actualizar_precio'),
    path('gestion-precios/modificacion-masiva/', modificacion_masiva, name='modificacion_masiva_precios'),
    path('gestion-precios/sincronizar-sucursales/', sincronizar_sucursales, name='sincronizar_precios_sucursales'),
    
    # APIs auxiliares
    path('api/categorias/listar/', listar_categorias, name='listar_categorias'),
    path('api/atributos/listar/', listar_atributos, name='listar_atributos'),
    path('api/sucursales/listar/', listar_sucursales, name='listar_sucursales'),
    path('gestion-precios/historial/<int:producto_id>/', obtener_historial_precio, name='obtener_historial_precio'),
    path('gestion-precios/sucursales-similares/<int:producto_id>/', buscar_productos_similares_sucursales, name='buscar_productos_similares_sucursales'),
    
    # ========== SISTEMA DE APROBACIÓN DE CAMBIOS DE PRECIOS ==========
    path('gestion-precios/proponer-cambio/', proponer_cambio_precio, name='proponer_cambio_precio'),
    path('gestion-precios/indicadores-pendientes/', obtener_indicadores_precios_pendientes, name='indicadores_precios_pendientes'),
    path('gestion-precios/listar-cambios/', listar_cambios_pendientes, name='listar_cambios_pendientes'),
    path('gestion-precios/revisar-cambio/', revisar_cambio_precio, name='revisar_cambio_precio'),
    path('gestion-precios/aprobar-cambio/', aprobar_cambio_precio, name='aprobar_cambio_precio'),
    path('gestion-precios/rechazar-cambio/', rechazar_cambio_precio, name='rechazar_cambio_precio'),
    path('gestion-precios/notificaciones/', obtener_notificaciones_precio, name='obtener_notificaciones_precio'),
    path('gestion-precios/marcar-notificacion/', marcar_notificacion_leida, name='marcar_notificacion_leida'),
    
    # Endpoint temporal de debug
    path('gestion-precios/debug-session/', debug_session_precios, name='debug_session_precios'),

    # ========== MÓDULO DE GENERACIÓN DE ARCHIVOS TXT ACEPTA ==========
    path('configuracion/interfaz-prueba-acepta/', views_modulo_documentos.interfaz_prueba_acepta, name='interfaz_prueba_acepta'),
    path('documentos/generar-txt-acepta/', views_modulo_documentos.generar_txt_acepta_api, name='generar_txt_acepta_api'),
    path('documentos/generar-txt-desde-dte/', views_modulo_documentos.generar_txt_desde_dte_existente, name='generar_txt_desde_dte_existente'),
    path('documentos/generar-dte-ticket/', views_modulo_documentos.generar_dte_desde_ticket_api, name='generar_dte_desde_ticket_api'),  # ✅ Nuevo endpoint

]
