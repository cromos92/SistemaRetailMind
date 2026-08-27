from retailmind import settings
from . import views
from django.shortcuts import render
from . import views_modulo_compras
from . import views_modulo_compras_xml
from . import views_modulo_configuracion
from . import views_modulo_reportes
from . import views_modulo_reportes_diferencias
from . import views_modulo_reportes_tallas
from . import views_gestion_sucursales
from . import views_resumen_existencias
from . import views_inteligencia_compra
from . import views_dashboard_home
from . import views_gestion_inventarios
from . import views_etiquetas_zebra
from . import views_fusion_duplicados
from . import views_ecommerce
from . import views_cron
from . import views_modulo_campanas_liquidacion
from .decorators import requiere_permiso


def _permiso_inventarios(tipo_permiso, vista):
    """
    Envuelve una vista de Gestión de Inventarios con el permiso fino que le
    corresponde.

    Las vistas de `views_gestion_inventarios.py` solo llevan `@login_required`
    y el middleware de permisos únicamente comprueba `puede_ver`. Resultado:
    cualquier usuario que pudiera abrir la pantalla podía además crear la toma,
    registrar conteos, aprobarla y aplicar los ajustes — y aplicar ajustes
    MUEVE STOCK y escribe kardex. Se envuelve aquí (y no en el módulo de
    vistas) para dejar el control de acceso declarado junto a la ruta.

    OJO — `PermisoRol.tiene_permiso` resuelve TRES capas, no solo el rol:

      1. PermisoUsuario  (override por usuario; si el campo no es None manda él)
      2. PermisoRol      (el permiso del rol)
      3. PermisoSucursal (restricción de la sucursal activa en sesión,
                          `request.session['idSucursalActual']`)

    Las tres son AND: basta que la SUCURSAL diga False para que el permiso se
    caiga aunque el rol lo tenga en True. Documentar solo la capa 2 fue lo que
    dejó pasar el error que se corrige más abajo.

    Permisos vigentes en producción para la opción 'gestion_inventarios':

    - Capa 2 (rol): administrador ver/crear/editar/eliminar/exportar/aprobar =
      True; super_admin ver/crear/editar = True y aprobar = False (sin usuarios
      activos con ese rol); jefe_local, cajero, vendedor y administracion todo
      en False, así que nunca llegan a estos endpoints.
    - Capa 3 (sucursal): la única sucursal con filas de PermisoSucursal es
      NICK1 (id=6), con 49 filas — entre ellas 'gestion_inventarios', que tiene
      habilitado/crear/editar/exportar = True pero eliminar y aprobar = False.

    De ahí la elección de permisos de las rutas: en TODA la tabla
    PermisoSucursal de producción no existe ni una sola fila con
    puede_aprobar=True (0 de 49), de modo que exigir 'puede_aprobar' equivale a
    un 403 permanente para cualquiera —administradores incluidos— en cuanto la
    sucursal activa tenga fila. 'puede_editar', en cambio, sí es un control
    real y administrable por sucursal (está en False en 2 de las 49 filas).
    """
    return requiere_permiso('gestion_inventarios', tipo_permiso)(vista)
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
    reintentar_generar_dte_ticket,
    listar_tickets_dte_fallido,
    ticket_pago_pos,
    buscar_ticket_pos,
    anular_ticket_pendiente,
    crear_ticket_pendiente_pos,
    # Funciones Gestión Documentos
    gestion_ventas_documentos,
    listar_documentos_ventas,
    exportar_documentos_ventas_excel,
    convertir_ticket_a_factura,
    detalle_documento_venta,
    anular_documento_venta,
    eliminar_documento_venta,
    editar_dte_boleta_papel,
    crear_dte_manual,
    # Funciones Cuadratura y Arqueo
    cuadratura_caja,
    generar_cuadratura_caja,
    obtener_detalle_cuadratura_metodos_pago,
    editar_fecha_pago_nc,
    sincronizar_fecha_ticket_dte,
    editar_fecha_ticket_sin_dte,
    guardar_cuadratura_completa,
    verificar_cuadratura_existente,
    eliminar_cuadratura,
    listar_cuadraturas,
    obtener_detalle_arqueo,
    editar_cuadratura,
    exportar_cuadratura_excel,
    obtener_transacciones_dia,
    agregar_deposito_arqueo,
    eliminar_deposito_bancario,
    cambiar_fecha_deposito,
    declarar_deposito,
    finalizar_declaracion,
    confirmar_deposito,
    obtener_depositos_pendientes,
    listar_arqueos_para_deposito,
    crear_deposito_multidia,
    detalle_grupo_deposito,
    revision_arqueos,
    listar_arqueos,
    crear_arqueo,
    guardar_conteo_fisico,
    cerrar_arqueo,
    corregir_arqueos_express,
    obtener_arqueo_detalle,
    recalcular_teoricos_arqueo,
    verificar_ventas_post_cierre,
    reabrir_arqueo,
    cancelar_arqueo,
    revisar_arqueo,
    revisar_arqueos_lote,
    registrar_comprobante_supervisor,
    obtener_depositos_arqueo,
    verificar_deposito,
    analisis_fraude_caja,
    crear_observacion_arqueo,
    obtener_bitacora_arqueo,
    obtener_bloqueos_arqueo,
    obtener_sucursales,
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
    aprobar_cambio_generar_ticket,
    validar_codigo_vendedor,
    cancelar_cambio_devolucion,
    revertir_cambio_devolucion,
    ejecutar_cambio_devolucion,
    registrar_pago_diferencia,
    condonar_diferencia_cobro,
    ajustar_diferencia_cobro,
    completar_cambio_devolucion,
    buscar_ticket_para_cambio,
    buscar_documento_cambio,
    buscar_productos_para_cambio,
    # Funciones Códigos de Autorización Dinámicos
    obtener_codigo_autorizacion_actual,
    validar_codigo_autorizacion,
    # Funciones Análisis Avanzado y Control de Fraude
    obtener_analisis_fraude_cambios,
    obtener_analisis_cambios_avanzado,
    listar_autorizaciones_cross_branch,
    revisar_autorizacion,
    obtener_cola_revision_gerencial,
    revisar_cambio_gerencial,
    exportar_cambios_devoluciones,
    # Funciones NC desde Devoluciones
    generar_nc_devolucion,
    detalle_nc_devolucion,
    # Funciones Clientes POS
    guardar_cliente_pos,
    enviar_ticket_email,
    # Funciones Búsqueda Productos POS
    buscar_productos_pos_avanzado,
    # Funciones Dashboard de Ventas
    dashboard_ventas,
    dashboard_ventas_mejorado,
    obtener_indicadores_globales_ventas,
    obtener_ventas_por_vendedor,
    obtener_ventas_por_sucursal,
    obtener_sucursales_dashboard,
    obtener_ventas_por_metodo_pago,
    obtener_analisis_cambios_devoluciones,
    obtener_estado_cuadraturas,
    obtener_productos_mas_vendidos,
    obtener_ventas_por_categoria,
    obtener_ventas_por_especialidad,
    obtener_indicador_compra_categoria,
    obtener_mix_por_sucursal,
    obtener_tendencias_ventas,
    exportar_dashboard_ventas_excel,
    obtener_indicadores_avanzados_ventas,
    obtener_estado_operacional_ventas,
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
    ajustar_monto_credito,
    # Pagos y Firmas
    registrar_pago_credito,
    registrar_firma_credito,
    # Utilidades
    obtener_trabajadores_credito,
    crear_trabajador_credito,
    actualizar_trabajador_credito,
    validar_codigo_trabajador,
    obtener_sucursales_empresa,
    obtener_empresas_disponibles,
    reporte_creditos_trabajadores,
    exportar_creditos_pdf,
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
    obtener_historial_ediciones_recientes,
    obtener_kpis_cambios_precios,
    buscar_productos_similares_sucursales,
    # Sistema de aprobación
    proponer_cambio_precio,
    obtener_indicadores_precios_pendientes,
    listar_cambios_pendientes,
    exportar_cambios_precios_excel,
    eliminar_cambios_aplicados,
    revisar_cambio_precio,
    aprobar_cambio_precio,
    rechazar_cambio_precio,
    obtener_notificaciones_precio,
    marcar_notificacion_leida,
    marcar_notificacion_leida_por_cambio,
    eliminar_notificaciones_precio,
    # Regularización de precios entre sucursales
    detectar_discrepancias_precios,
    regularizar_precio_sucursales,
    resumen_discrepancias_precios,
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
    cotizacion_pdf,
    # APIs de creación y edición
    actualizar_email_cliente,
    crear_cotizacion,
    editar_cotizacion,
    # APIs de acciones
    anular_cotizacion,
    convertir_cotizacion_factura,
    reabrir_cotizacion,
    # APIs de búsqueda
    buscar_productos_cotizacion,
    # APIs de vendedores
    listar_vendedores_cotizacion,
    # APIs de clientes
    crear_cliente_cotizacion,
    # APIs de integración POS
    cargar_cotizacion_como_ticket,
    # API de envío por correo
    enviar_cotizacion_correo,
    # Despacho diferido
    asignar_sku_pendiente,
    revertir_sku_despachado,
    validar_despacho_cotizacion,
)
from .views_modulo_existencias_nuevo import (
    # Tarjeta de Movimiento por Producto
    tarjeta_movimiento_producto,
    api_tarjeta_movimiento,
    api_buscar_productos_tarjeta_movimiento,
    # Despacho a Todas Sucursales
    despacho_todas_sucursales,
    api_obtener_sucursales_despacho,
    api_productos_disponibles_despacho,
    api_pendientes_despacho_sucursal,
    api_crear_despacho_masivo,
    api_marcas_disponibles_despacho,
    api_historial_despachos,
    # Trazabilidad Completa
    trazabilidad_producto,
    api_trazabilidad_producto,
    # Modificación de Precios y Costos
    modificacion_precios_costos,
    api_buscar_productos_precios,
    api_modificar_precio_costo,
    api_modificar_precios_masivo,
    # Corrección de talla / categoría / especialidad global (todas las bodegas)
    api_editar_talla_producto_global,
    api_editar_categoria_producto_global,
    api_editar_especialidad_producto_global,
    # Actividad de creación manual (verGestionProducto)
    api_actividad_creacion_manual,
    # Acciones rápidas sobre un ingreso manual (tabla Actividad reciente)
    api_evento_ingreso_manual,
    api_sumar_stock_rapido,
    api_preview_reasignar_dte,
    api_reasignar_dte_ingreso,
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
    excluir_analitica_masivo,
    listar_productos_excluidos,
    obtener_impacto_recategorizacion,
    actualizar_productos_masivo,
    preview_edicion_masiva,
    preview_salida_stock_producto,
    aplicar_salida_stock_producto,
)
from .views_modulo_requerimientos import (
    # Vistas principales
    modulo_requerimientos,
    crear_requerimiento_vista,
    detalle_requerimiento_vista,
    gestionar_requerimientos_vista,
    # APIs
    crear_requerimiento,
    listar_requerimientos,
    detalle_requerimiento,
    editar_requerimiento,
    actualizar_estado_requerimiento,
    decidir_requerimiento,
    enviar_a_proveedor,
    registrar_respuesta_proveedor,
    completar_requerimiento,
    buscar_producto_sku,
    buscar_ticket_por_folio,
    buscar_cliente_por_rut,
    validar_rut_chileno,
    crear_cliente_rapido,
    obtener_estadisticas_requerimientos,
    exportar_requerimientos,
    descargar_formato_requerimiento,
    sugerir_proveedor_por_sku,
    buscar_compras_producto,
    buscar_dte_compra_por_numero,
    obtener_tipos_foto,
)
from .views_modulo_giftcards import (
    # Vistas HTML
    modulo_giftcards,
    emitir_giftcard_vista,
    detalle_giftcard_vista,
    trazabilidad_giftcards_vista,
    # APIs
    api_emitir_giftcard,
    api_listar_giftcards,
    api_detalle_giftcard,
    api_consultar_saldo_giftcard,
    api_validar_giftcard,
    api_recargar_giftcard,
    api_anular_giftcard,
    api_reporte_giftcards,
    api_bloquear_giftcard,
    api_desbloquear_giftcard,
    api_editar_giftcard,
    api_cambiar_ambito_giftcard,
    api_enviar_correo_giftcard,
    api_confirmar_entrega_giftcard,
    webhook_correo_giftcard,
    api_trazabilidad_giftcards,
    api_exportar_giftcards,
    api_exportar_trazabilidad,
)

from .views_modulo_correo import (
    pixel_apertura,
    webhook_correo,
)
from .views_modulo_devolucion_garantia import (
    # Vistas HTML
    modulo_devolucion_garantia,
    detalle_devolucion_garantia,
    # APIs — solicitante
    api_buscar_dte_devolucion_garantia,
    api_generar_devolucion_garantia,
    api_listar_devoluciones_garantia,
    api_anular_solicitud_devolucion_garantia,
    # APIs — aprobador
    api_detalle_solicitud_devolucion_garantia,
    api_ticket_devolucion_garantia,
    api_impacto_caja_devolucion_garantia,
    api_aprobar_devolucion_garantia,
    api_rechazar_devolucion_garantia,
)
from .views_modulo_fidelizacion import (
    # Vistas HTML
    descargar_app_puntos,
    modulo_fidelizacion,
    configurar_programa_vista,
    reporte_fidelizacion_vista,
    ficha_cliente_puntos_vista,
    registrar_cliente_vista,
    # APIs
    api_listar_cuentas,
    api_detalle_cuenta,
    api_consultar_saldo_puntos,
    api_guardar_programa,
    api_ajuste_manual_puntos,
    api_registrar_cliente,
    api_reporte_fidelizacion,
    api_bono_cumpleanos,
    api_generar_vale_canje,
    api_validar_vale_canje,
)
from .views_modulo_cupones import (
    # Vistas HTML
    modulo_cupones,
    # APIs
    api_listar_campanas,
    api_guardar_campana,
    api_toggle_campana,
    api_buscar_cliente_cupon,
    api_emitir_cupon,
    api_emitir_lote_cupones,
    api_listar_cupones,
    api_anular_cupon,
    api_validar_cupon_caja,
)
from .views_permisos import (
    # Gestión de permisos por rol
    gestion_permisos,
    obtener_permisos_rol,
    guardar_permiso,
    guardar_permisos_masivos,
    copiar_permisos_rol,
    gestionar_modulos_opciones,
    estadisticas_permisos,
    # Gestión de permisos por sucursal
    obtener_sucursales_permisos,
    obtener_permisos_sucursal,
    guardar_permisos_sucursal,
    copiar_permisos_sucursal,
    aplicar_plantilla_tipo_sucursal,
    restablecer_permisos_sucursal,
    # Exportar/Importar permisos
    exportar_permisos_rol,
    exportar_todos_permisos,
    importar_permisos,
    exportar_permisos_sucursal,
    importar_permisos_sucursal,
    # Gestión de permisos por usuario
    obtener_usuarios_permisos,
    obtener_permisos_usuario,
    guardar_permisos_usuario,
    eliminar_permisos_usuario,
    copiar_permisos_usuario,
)
from .views_transbank_sdk import (
    # Vistas
    gestion_transbank_pos_sdk,
    gestion_transbank_pos_manual,
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
from .views_dashboards_kpi import (
    dashboard_documentos,
    api_dashboard_documentos,
    dashboard_requerimientos,
    api_dashboard_requerimientos,
    dashboard_despachos,
    api_dashboard_despachos,
    api_despachos_flujo,
)
from .views_prediccion_compras import (
    dashboard_prediccion,
    api_prediccion_resumen,
    api_prediccion_clasificacion,
    api_prediccion_sugerencias,
    api_prediccion_alertas_velocidad,
    api_prediccion_alertas_quiebre,
    api_prediccion_producto_detalle,
    api_prediccion_aprobar_sugerencia,
    api_prediccion_recalcular,
    api_prediccion_configuracion,
    api_prediccion_categorias_disponibles,
    api_prediccion_analisis_categoria,
    api_prediccion_analisis_marca,
    api_prediccion_analisis_proveedor,
    api_prediccion_marca_articulos,
    api_prediccion_graficos,
)
from django.urls import path
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView

urlpatterns = [
        
     # Dashboard Home con KPIs de Retail (NEXO Design System)
     path('home/', views_dashboard_home.dashboard_home, name='verHome'),
     path('dashboard/', views_dashboard_home.dashboard_home, name='dashboard_home'),
     path('bienvenida/', views_dashboard_home.bienvenida, name='bienvenida'),
     path('dashboard/api/ventas-tiempo-real/', views_dashboard_home.api_dashboard_ventas_tiempo_real, name='api_dashboard_ventas_tiempo_real'),
     path('dashboard/api/stock-alertas/', views_dashboard_home.api_dashboard_stock_alertas, name='api_dashboard_stock_alertas'),
     
     # Dashboard antiguo (backup)
     path('home-legacy/', views.verHome, name='verHomeLegacy'),
     path('ruta_a_check_session/', views_dashboard_home.dashboard_home, name='check_session'),
     path('verResetPassword/', views.ver_resetPassword, name='verResetPassword'),
     path('verResetPasswordSuccess/', views.verResetPasswordSuccess, name='verResetPasswordSuccess'),
     path('cambiar-password/', views.cambiar_password_obligatorio, name='cambiar_password_obligatorio'),
     #Modulo Compras
     path('verGestionCompras/', views.verGestionCompras, name='verGestionCompras'),
     # 'dashboard_compras_estrategico' se eliminó (2026-07-25): devolvía JSON
     # crudo y el botón del Dashboard Home apuntaba ahí, así que el usuario
     # veía un volcado de datos en el navegador. Su contenido es un subconjunto
     # del Dashboard de Compras Mejorado, que es adonde apunta ahora.
     path('dashboard_compras_estrategico/', RedirectView.as_view(
         pattern_name='verDashboardComprasMejorado', permanent=False,
     ), name='dashboard_compras_estrategico'),
         path('exportar_dashboard_compras/', views_modulo_compras.exportar_dashboard_compras, name='exportar_dashboard_compras'),
    path('verDashboardCompras/', views_modulo_compras.verDashboardCompras, name='verDashboardCompras'),
    path('verDashboardComprasMejorado/', views_modulo_compras.verDashboardComprasMejorado, name='verDashboardComprasMejorado'),
    path('dashboard_compras_mejorado_api/', views_modulo_compras.dashboard_compras_mejorado_api, name='dashboard_compras_mejorado_api'),
    path('diagnostico_datos_compras/', views_modulo_compras.diagnostico_datos_compras, name='diagnostico_datos_compras'),
    path('verDiagnosticoCompras/', views_modulo_compras.verDiagnosticoCompras, name='verDiagnosticoCompras'),
     path('obtenerDetalleComprasPorParametros/', views.obtenerDetalleComprasPorParametros, name='obtenerDetalleComprasPorParametros'),
     
     # === IMPORTACIÓN DE PROVEEDORES Y DTEs ===
     path('importacion-proveedores/', views_modulo_compras.ver_importacion_proveedores, name='ver_importacion_proveedores'),
     path('api/importar-proveedores/', views_modulo_compras.importar_proveedores_csv, name='importar_proveedores_csv'),
     path('api/descargar-formato-proveedores/', views_modulo_compras.descargar_formato_proveedores, name='descargar_formato_proveedores'),
     path('api/exportar-proveedores-actuales/', views_modulo_compras.exportar_proveedores_actuales, name='exportar_proveedores_actuales'),
     path('api/exportar-proveedores-excel/', views_modulo_compras.exportar_proveedores_excel, name='exportar_proveedores_excel'),
     path('importacion-dtes/', views_modulo_compras.ver_importacion_dtes, name='ver_importacion_dtes'),
     path('api/importar-dtes/', views_modulo_compras.importar_dtes_csv, name='importar_dtes_csv'),
     path('api/descargar-formato-dtes/', views_modulo_compras.descargar_formato_dtes, name='descargar_formato_dtes'),
     path('api/exportar-dtes-actuales/', views_modulo_compras.exportar_dtes_actuales, name='exportar_dtes_actuales'),
     path('api/exportar-dtes-excel/', views_modulo_compras.exportar_dtes_excel, name='exportar_dtes_excel'),

     # === IMPORTACIÓN DE FACTURA DE PROVEEDOR DESDE XML DTE (SII) ===
     # Entrada de documentos, NO emisión: la emisión sigue siendo el TXT de
     # Acepta. Confirmar aquí crea Dte + Dte_Productos y NO mueve stock
     # (ver docstring de views_modulo_compras_xml).
     # Las vistas llevan @requiere_permiso('gestion_dte_compras','puede_crear')
     # en el propio módulo: el middleware solo comprueba `puede_ver` y además
     # su match por substring no cubre las rutas bajo `/app/api/`.
     path('compras/importar-xml-dte/', views_modulo_compras_xml.ver_importar_xml_dte, name='ver_importar_xml_dte'),
     path('api/compras/xml-dte/analizar/', views_modulo_compras_xml.analizar_xml_dte, name='analizar_xml_dte'),
     path('api/compras/xml-dte/buscar-producto/', views_modulo_compras_xml.buscar_producto_xml_dte, name='buscar_producto_xml_dte'),
     path('api/compras/xml-dte/confirmar/', views_modulo_compras_xml.confirmar_xml_dte, name='confirmar_xml_dte'),


     # === EXPORTACIÓN DE COMPRAS ACTUALES ===
     path('api/exportar-compras-excel/', views_modulo_compras.exportar_compras_excel, name='exportar_compras_excel'),
     path('api/exportar-compras-csv/', views_modulo_compras.exportar_compras_csv, name='exportar_compras_csv'),
     path('crear_compra/', views.crear_compra, name='crear_compra'),
     path('eliminar_compra/', views.eliminar_compra, name='eliminar_compra'),
     path('obtener_compra/<int:compra_id>/', views.obtener_compra_para_editar, name='obtener_compra_para_editar'),
     path('actualizar_compra/<int:compra_id>/', views.actualizar_compra, name='actualizar_compra'),
     path('validar_factura_proveedor/', views.validar_factura_proveedor, name='validar_factura_proveedor'),
     path('obtener_compras/', views.obtener_compras_por_anio, name='obtener_compras'),
     path('verGestionProducto/', views.verGestionProducto, name='verGestionProducto'),
     path('importar_csv_compra/', views.importar_csv_compra, name='importar_csv_compra'),
     path('compra/recepcionar/', views.recepcionar_compra, name='recepcionar_compra'),
     path('verGestionDteCompras/', views.verGestionDteCompras, name='verGestionDteCompras'),
     path('obtener_dte_compras/', views_modulo_compras.obtener_dte_compras, name='obtener_dte_compras'),
     path('api/resumen-pendientes-anio/', views_modulo_compras.obtener_resumen_pendientes_anio, name='obtener_resumen_pendientes_anio'),
     path('crearDteCompras/', views.crearDteCompras, name='crearDteCompras'),
     path('actualizarDteCompras/<int:dte_id>/', views.actualizarDteCompras, name='actualizarDteCompras'),
    path('empresas_proveedoras/', views_modulo_compras.empresas_proveedoras, name='empresas_proveedoras'),
    path('empresas_receptoras/', views.empresas_receptoras, name='empresas_receptoras'),
     path('cargarDteCompra/', views.cargarDteCompra, name='cargarDteCompra'),
     path('facturasPendientesPorMes/', views.facturasPendientesPorMes, name='facturasPendientesPorMes'),
     path('comprobantePagoDTE/', views.comprobantePagoDTE, name='comprobantePagoDTE'),
     path('datos_envio_comprobante/<int:dte_id>/', views.datos_envio_comprobante, name='datos_envio_comprobante'),
     path('enviar_comprobante_pago/', views.enviar_comprobante_pago, name='enviar_comprobante_pago'),
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
     path('restaurarDTE/<int:dte_id>/', views.restaurar_dte, name='restaurar_dte'),
     
     # Incidencias DTE
     path('incidencias/<int:dte_id>/', views.listar_incidencias, name='listar_incidencias'),
     path('incidencias/crear/', views.crear_incidencia, name='crear_incidencia'),
     path('incidencias/actualizar/<int:incidencia_id>/', views.actualizar_incidencia, name='actualizar_incidencia'),
     path('incidencias/eliminar/<int:incidencia_id>/', views.eliminar_incidencia, name='eliminar_incidencia'),
     
     # Documentos Base y Notas de Crédito
     path('obtener_documentos_base/', views.obtener_documentos_base, name='obtener_documentos_base'),
     path('obtener_ncs_disponibles/', views.obtener_ncs_disponibles, name='obtener_ncs_disponibles'),
     path('obtener_facturas_para_nc/', views.obtener_facturas_para_nc, name='obtener_facturas_para_nc'),
     path('obtener_info_asociacion_nc/<int:nc_id>/', views.obtener_info_asociacion_nc, name='obtener_info_asociacion_nc'),
     path('desasociar_nc/<int:nc_id>/', views.desasociar_nc, name='desasociar_nc'),
     path('asociar_nc_existente/', views.asociar_nc_existente, name='asociar_nc_existente'),

     # Asociación Factura <-> Cotización/Guía (documento_padre) y panel de asociaciones
     path('asociar_factura_cotizacion/', views.asociar_factura_cotizacion, name='asociar_factura_cotizacion'),
     path('desasociar_factura_cotizacion/<int:factura_id>/', views.desasociar_factura_cotizacion, name='desasociar_factura_cotizacion'),
     path('obtener_asociaciones_dte/<int:dte_id>/', views.obtener_asociaciones_dte, name='obtener_asociaciones_dte'),

     # Compensación factura-contra-factura ("Pagar con Factura")
     path('obtener_facturas_compensar_disponibles/', views_modulo_compras.obtener_facturas_compensar_disponibles, name='obtener_facturas_compensar_disponibles'),
     path('asociar_factura_compensacion/', views_modulo_compras.asociar_factura_compensacion, name='asociar_factura_compensacion'),
     path('desasociar_factura_compensacion/<int:pago_id>/', views_modulo_compras.desasociar_factura_compensacion, name='desasociar_factura_compensacion'),
     path('obtener_info_compensacion/<int:dte_id>/', views_modulo_compras.obtener_info_compensacion, name='obtener_info_compensacion'),

     # Compensación con factura EMITIDA a este proveedor (cuando no se puede cargar una NC)
     path('obtener_documentos_emitidos_compensar_disponibles/', views_modulo_compras.obtener_documentos_emitidos_compensar_disponibles, name='obtener_documentos_emitidos_compensar_disponibles'),
     path('asociar_documento_emitido_compensacion/', views_modulo_compras.asociar_documento_emitido_compensacion, name='asociar_documento_emitido_compensacion'),
     path('desasociar_documento_emitido_compensacion/<int:pago_id>/', views_modulo_compras.desasociar_documento_emitido_compensacion, name='desasociar_documento_emitido_compensacion'),

     path('procesar_pago_masivo/', views.procesar_pago_masivo, name='procesar_pago_masivo'),
     path('guardar_recepcion/', views.guardar_recepcion, name='guardar_recepcion'),
     path('actualizar_sucursal_recepciones/', views.actualizar_sucursal_recepciones, name='actualizar_sucursal_recepciones'),
     path('agregar_producto_manual/', views.agregar_producto_manual_a_compra, name='agregar_producto_manual'),
     path('eliminar_producto_compra/', views.eliminar_producto_compra, name='eliminar_producto_compra'),
     path('limpiar_productos_compra/', views.limpiar_productos_compra, name='limpiar_productos_compra'),
     # Distribución por guía de tallas / curvas
     path('api/curvas-distribucion/', views.listar_curvas_distribucion, name='listar_curvas_distribucion'),
     path('api/curvas-distribucion/guardar/', views.guardar_curva_distribucion, name='guardar_curva_distribucion'),
     path('api/curvas-distribucion/eliminar/', views.eliminar_curva_distribucion, name='eliminar_curva_distribucion'),
     path('api/distribuir-tallas-compra-producto/', views.distribuir_tallas_compra_producto, name='distribuir_tallas_compra_producto'),
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
     path('ver_guias_talla/', views.ver_guias_talla, name='ver_guias_talla'),
     path('api/guias-talla-completas/', views.api_guias_talla_completas, name='api_guias_talla_completas'),
     path('api/asignar-guia-talla-producto/', views.asignar_guia_talla_producto, name='asignar_guia_talla_producto'),
     path('crear_guia_talla/', views.crear_guia_talla, name='crear_guia_talla'),
     path('guia_talla_detalle/<int:id>/', views.guia_talla_detalle, name='guia_talla_detalle'),
     path('eliminar_guia_talla/', views.eliminar_guia_talla, name='eliminar_guia_talla'),
     path('app/guias_talla_por_marca/', views.guias_talla_por_marca, name='guias_talla_por_marca'),
     path('app/verificar_producto_existente/', views.verificar_producto_existente, name='verificar_producto_existente_app'),
     path('obtener_siguiente_sku/', views.obtener_siguiente_sku_view, name='obtener_siguiente_sku'),
     path('obtener_multiples_skus/', views.obtener_multiples_skus_view, name='obtener_multiples_skus'),
     path('configuracion-sku/', views.obtener_configuracion_sku, name='configuracion_sku'),
     path('configuracion-sku/actualizar/', views.actualizar_configuracion_sku, name='actualizar_configuracion_sku'),
     path('verificar_producto_existente/', views.verificar_producto_existente, name='verificar_producto_existente'),
     path('buscar_productos_por_articulo/', views.buscar_productos_por_articulo, name='buscar_productos_por_articulo'),
     path('buscar_articulo_autocomplete/', views.buscar_articulo_autocomplete, name='buscar_articulo_autocomplete'),
     path('crear_producto_desde_recepcion/', views.crear_producto_desde_recepcion, name='crear_producto_desde_recepcion'),
     path('obtener_recepciones_producto/<int:producto_id>/', views.obtener_recepciones_producto, name='obtener_recepciones_producto'),
     path('actualizar_recepciones_producto/', views.actualizar_recepciones_producto, name='actualizar_recepciones_producto'),
    path('api/compras-producto/<int:producto_id>/editar-atributos/', views.actualizar_atributos_compra_producto, name='actualizar_atributos_compra_producto'),
     path('eliminar_recepcion_pendiente/', views.eliminar_recepcion_pendiente, name='eliminar_recepcion_pendiente'),
     path('eliminar_producto_todas_sucursales/', views.eliminar_producto_todas_sucursales, name='eliminar_producto_todas_sucursales'),
     path('pendientes_despacho/', views.pendientes_despacho, name='pendientes_despacho'),
     path('consumir_pendientes_despacho/', views.consumir_pendientes_despacho, name='consumir_pendientes_despacho'),
     path('obtener_recepciones_compra/<int:compra_id>/', views.obtener_recepciones_compra, name='obtener_recepciones_compra'),
     path('obtener_pendientes_compra/<int:compra_id>/', views.obtener_pendientes_compra, name='obtener_pendientes_compra'),
     path('actualizar_recepciones_compra/', views.actualizar_recepciones_compra, name='actualizar_recepciones_compra'),
     path('eliminar_pendientes_compra_masivo/', views.eliminar_pendientes_compra_masivo, name='eliminar_pendientes_compra_masivo'),

     # === Vinculación retroactiva: productos existentes → compra ===
     path('api/compra/items-para-vincular/', views.items_compra_para_vincular, name='items_compra_para_vincular'),
     path('api/compra/buscar-sku-vincular/', views.buscar_sku_para_vincular, name='buscar_sku_para_vincular'),
     path('api/compra/buscar-producto-agrupado-vincular/', views.buscar_producto_agrupado_para_vincular, name='buscar_producto_agrupado_para_vincular'),
     path('api/compra/vincular-retroactivo/', views.vincular_productos_retroactivo, name='vincular_productos_retroactivo'),
     path('api/compra/desvincular-retroactivo/', views.desvincular_cpt_retroactivo, name='desvincular_cpt_retroactivo'),

     # === Revertir / Editar productos ya creados ===
     path('api/producto/revertir-a-pendiente/', views.revertir_producto_a_pendiente, name='revertir_producto_a_pendiente'),
     path('api/producto/editar-talla-creado/', views.editar_producto_talla_creado, name='editar_producto_talla_creado'),

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
    path('productos/excluir-analitica-masivo/', excluir_analitica_masivo, name='excluir_analitica_masivo'),
    path('productos/listar-excluidos/', listar_productos_excluidos, name='listar_productos_excluidos'),
    path('productos/impacto-recategorizacion/<int:producto_id>/', obtener_impacto_recategorizacion, name='obtener_impacto_recategorizacion'),
    path('productos/actualizar-masivo/', actualizar_productos_masivo, name='actualizar_productos_masivo'),
    path('productos/preview-edicion-masiva/', preview_edicion_masiva, name='preview_edicion_masiva'),
    path('productos/stock-salida/preview/', preview_salida_stock_producto, name='preview_salida_stock_producto'),
    path('productos/stock-salida/aplicar/', aplicar_salida_stock_producto, name='aplicar_salida_stock_producto'),
    
    # ========== NUEVAS URLs PARA MOVIMIENTOS ==========
     
     # === VENTAS AL PÚBLICO ===
     path('crear_ticket_venta/', views.crear_ticket_venta, name='crear_ticket_venta'),
     path('obtener_tickets_venta/', views.obtener_tickets_venta, name='obtener_tickets_venta'),
     
     # === TRASPASOS ===
     # Rutas crear_traspaso / aprobar_traspaso / recibir_traspaso eliminadas
     # (2026-07-28). Eran flujo muerto: ningún template ni JS las llamaba, y las
     # tres FBV movían stock sin @login_required (POST anónimo con CSRF token
     # obtenible sin sesión). Además aprobar_traspaso descontaba stock de
     # Traspaso PENDIENTE que api_crear_despacho_masivo ya había descontado.
     # El circuito real de traspasos es por DTE (ver recepcion-dte/).

     # === AJUSTES DE INVENTARIO ===
     path('crear_ajuste_inventario/', views.crear_ajuste_inventario, name='crear_ajuste_inventario'),
    path('ajuste-stock-rapido/', views.ajuste_stock_rapido, name='ajuste_stock_rapido'),
     
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
    # Sugerencia de marca/categoria/especialidad segun el proveedor elegido
    # (modal Crear Producto Manual). Solo lectura.
    path('api/sugerencias-proveedor/', views.sugerencias_por_proveedor, name='sugerencias_por_proveedor'),
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
    # (obtener_metricas_fifo y obtener_analisis_fifo_detallado eliminados 2026-07:
    #  endpoints muertos sin consumidores; el segundo estaba expuesto sin login)
    path('obtener_datos_dashboard_fifo/', views.obtener_datos_dashboard_fifo, name='obtener_datos_dashboard_fifo'),
    path('exportar_dashboard_fifo/', views.exportar_dashboard_fifo, name='exportar_dashboard_fifo'),

    # === URLs PARA DASHBOARD DE PRODUCTOS ===
    path('dashboard_productos/', views.dashboard_productos_mejorado, name='dashboard_productos'),  # Redirige al mejorado
    path('dashboard_productos_mejorado/', views.dashboard_productos_mejorado, name='dashboard_productos_mejorado'),
    path('dashboard_productos_mejorado_api/', views.dashboard_productos_mejorado_api, name='dashboard_productos_mejorado_api'),
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
    path('emisionDTEConcepto/', views.emision_dte_concepto, name='emision_dte_concepto'),
    path('emitir_dte_concepto/', views.emitir_dte_concepto, name='emitir_dte_concepto'),
    path('crear_empresa_rapida/', views.crear_empresa_rapida, name='crear_empresa_rapida'),
    path('recepcion-dte/', views.recepcion_dte, name='recepcion_dte'),
    path('dte/recepciones_pendientes/', views.recepciones_pendientes_api, name='recepciones_pendientes_api'),
    path('dte/historial_recepciones/', views.historial_recepciones_api, name='historial_recepciones_api'),
    path('dte/confirmar_recepcion/', views.confirmar_recepcion_api, name='confirmar_recepcion_api'),
    path('dte/rechazar_recepcion/', views.rechazar_recepcion_api, name='rechazar_recepcion_api'),
    path('dte/decidir_sobrante/', views.decidir_sobrante_api, name='decidir_sobrante_api'),
    path('dte/rehabilitar_rechazado/', views.rehabilitar_dte_rechazado_api, name='rehabilitar_dte_rechazado_api'),
    path('dte/obtener_rechazados/', views.obtener_dtes_rechazados_api, name='obtener_dtes_rechazados_api'),
    path('dte/obtener_productos_problema/', views.obtener_productos_problema_dte_api, name='obtener_productos_problema_dte_api'),
    path('dte/corregir_recepcion_emisor/', views.corregir_recepcion_emisor_api, name='corregir_recepcion_emisor_api'),
    path('dte/cancelar_traspaso/', views.cancelar_dte_traspaso_api, name='cancelar_dte_traspaso_api'),
    path('dte/editar_traspaso/', views.editar_dte_traspaso_api, name='editar_dte_traspaso_api'),
    path('dte/reasignar_destino_traspaso/', views.reasignar_destino_traspaso_api, name='reasignar_destino_traspaso_api'),
    path('dte/emitidos_pendientes/', views.emitidos_pendientes_api, name='emitidos_pendientes_api'),
    path('dte/emitidos_recepcionados/', views.emitidos_recepcionados_api, name='emitidos_recepcionados_api'),
    path('dte/ajustar_emitido/', views.ajustar_dte_emisor_api, name='ajustar_dte_emisor_api'),
    # Alias semántico: mismo endpoint, detecta pre/post recepción automáticamente.
    path('dte/ajustar_traspaso/', views.ajustar_dte_emisor_api, name='ajustar_traspaso_api'),
    # Cambio de talla pre-recepcion: mueve unidades entre tallas DENTRO del
    # mismo folio, sin emitir documento hijo ni mover los totales.
    path('dte/cambiar_talla/', views.cambiar_talla_dte_traspaso_api, name='cambiar_talla_dte_traspaso_api'),
    # === LIMBO INBOX ===
    path('dtes-en-limbo/', views.dtes_en_limbo, name='dtes_en_limbo'),
    path('dte/obtener_limbo_emisor/', views.obtener_dtes_limbo_emisor_api, name='obtener_dtes_limbo_emisor_api'),
    path('dte/limbo_resumen/<int:dte_id>/', views.obtener_resumen_limbo_dte_api, name='obtener_resumen_limbo_dte_api'),
    path('dte/confirmar_devolucion_fisica/', views.confirmar_devolucion_fisica_api, name='confirmar_devolucion_fisica_api'),
    path('dte/obtener_regularizacion_receptor/', views.obtener_dtes_regularizacion_receptor_api, name='obtener_dtes_regularizacion_receptor_api'),
    path('dte/devolucion_pendiente_detalle/<int:dte_hijo_id>/', views.obtener_devolucion_pendiente_detalle_api, name='obtener_devolucion_pendiente_detalle_api'),
    path('regularizar-recepciones/', views.regularizar_recepciones, name='regularizar_recepciones'),
    path('dte/obtener_productos_regularizar/', views.obtener_productos_regularizar, name='obtener_productos_regularizar'),
    path('dte/exportar_productos_regularizar_pdf/', views.exportar_productos_regularizar_pdf, name='exportar_productos_regularizar_pdf'),
    path('dte/obtener_solicitudes_recibidas/', views.obtener_solicitudes_recibidas, name='obtener_solicitudes_recibidas'),
    path('dte/documento-regularizacion/<int:recepcion_id>/', views.documento_regularizacion, name='documento_regularizacion'),
    path('dte/ajuste_interno_individual/', views.procesar_ajuste_interno_individual, name='procesar_ajuste_interno_individual'),
    path('dte/cambio_producto_individual/', views.procesar_cambio_producto_individual, name='procesar_cambio_producto_individual'),
    path('dte/obtener_solicitud_producto/<int:producto_id>/', views.obtener_solicitud_producto, name='obtener_solicitud_producto'),
    path('dte/decidir_solicitud/', views.decidir_solicitud_api, name='decidir_solicitud_api'),
    path('dte/buscar_productos_emisor/', views.buscar_productos_emisor, name='buscar_productos_emisor'),
    path('dte/regularizar_producto/', views.regularizar_producto_api, name='regularizar_producto_api'),
    path('dte/<int:nc_id>/txt-acepta/', views.descargar_txt_nc_api, name='descargar_txt_nc_api'),
    path('dte/regularizar_dte_masivo/', views.regularizar_dte_masivo, name='regularizar_dte_masivo'),
    path('dte/anular_regularizacion_dte/', views.anular_regularizacion_dte, name='anular_regularizacion_dte'),
    path('dte/cancelar_regularizacion/', views.cancelar_regularizacion_producto, name='cancelar_regularizacion_producto'),
    path('dte/obtener_dtes_con_problemas/', views.obtener_dtes_con_problemas, name='obtener_dtes_con_problemas'),
    path('dte/obtener_detalle_dte_recepcionado/', views.obtener_detalle_dte_recepcionado, name='obtener_detalle_dte_recepcionado'),
    path('dte/<int:dte_id>/audit/', views.dte_audit_api, name='dte_audit_api'),
    path('debug_session/', views.debug_session, name='debug_session'),
    path('debug_user_empresas/', views.debug_user_empresas, name='debug_user_empresas'),  # Temporal para debug
    path('empresas_clientes/', views.empresas_clientes, name='empresas_clientes'),
    path('obtener_marcas/', views.obtener_marcas, name='obtener_marcas'),
    path('obtener_categorias/', views.obtener_categorias, name='obtener_categorias'),
    path('obtener_sucursales/', views.obtener_sucursales, name='obtener_sucursales'),
    path('buscar_productos_bodega/', views.buscar_productos_bodega, name='buscar_productos_bodega'),
    path('buscar_dte_referencia/', views.buscar_dte_referencia, name='buscar_dte_referencia'),
    path('emitir_dte/', views.emitir_dte, name='emitir_dte'),
    
    # === URLs PARA GESTIÓN DE USUARIOS ===
    path('gestion_usuarios/', views.gestion_usuarios_redirect, name='gestion_usuarios'),
    
    # === URLs PARA GESTIÓN DE SUCURSALES ===
    path('gestion-sucursales/', views_gestion_sucursales.gestion_sucursales, name='gestion_sucursales'),
    path('gestion-sucursales/listar/', views_gestion_sucursales.listar_sucursales_tabla, name='listar_sucursales_tabla'),
    path('gestion-sucursales/crear/', views_gestion_sucursales.crear_sucursal, name='crear_sucursal'),
    path('gestion-sucursales/<int:sucursal_id>/', views_gestion_sucursales.obtener_sucursal, name='obtener_sucursal'),
    path('gestion-sucursales/editar/<int:sucursal_id>/', views_gestion_sucursales.editar_sucursal, name='editar_sucursal'),
    path('gestion-sucursales/eliminar/<int:sucursal_id>/', views_gestion_sucursales.eliminar_sucursal, name='eliminar_sucursal'),
    
    # ========== GESTIÓN DE CAMBIO DE EMPRESA/SUCURSAL ==========
    path('cambiar-empresa/', views.cambiar_empresa, name='cambiar_empresa'),
    path('seleccionar-empresa-sucursal/', views.seleccionar_empresa_sucursal, name='seleccionar_empresa_sucursal'),
    path('api/sucursales-usuario/', views.api_sucursales_usuario, name='api_sucursales_usuario'),
    
    # ========== BÚSQUEDA DE PRODUCTOS POR SUCURSAL ==========
    path('productos-sucursal/', views.buscar_productos_sucursal, name='buscar_productos_sucursal'),
    path('api/productos-sucursal/', views.obtener_productos_sucursal, name='obtener_productos_sucursal'),
    path('api/opciones-atributo/', views.obtener_opciones_atributo, name='obtener_opciones_atributo'),
    path('api/atributos-compras/', views.api_atributos_compras, name='api_atributos_compras'),
    path('api/formato-importacion-compras/', views.descargar_formato_importacion_compras, name='descargar_formato_importacion_compras'),
    
    # ========== TICKET DE VENTA ==========
    path('ticket-venta/', views.ticket_venta, name='ticket_venta'),
    path('api/buscar-vendedor/', views.buscar_vendedor_por_codigo, name='buscar_vendedor_por_codigo'),
    path('api/buscar-producto-sku/', views.buscar_producto_por_sku, name='buscar_producto_por_sku'),
    path('api/crear-ticket/', views.crear_ticket, name='crear_ticket'),
    path('api/tickets/<int:correlativo>/', obtener_ticket_por_correlativo, name='obtener_ticket_por_correlativo'),
    path('api/tickets/buscar/', buscar_ticket_pos, name='buscar_ticket_pos'),
    path('api/tickets/<str:correlativo>/pagos/', registrar_pagos_ticket, name='registrar_pagos_ticket'),
    path('api/tickets/<int:ticket_id>/reintentar-dte/', reintentar_generar_dte_ticket, name='reintentar_generar_dte_ticket'),
    path('api/tickets/dte-fallido/', listar_tickets_dte_fallido, name='listar_tickets_dte_fallido'),
    path('api/tickets/anular/', anular_ticket_pendiente, name='anular_ticket_pendiente'),
    path('api/tickets/crear-pendiente/', crear_ticket_pendiente_pos, name='crear_ticket_pendiente_pos'),
    path('ticket-pago-pos/', ticket_pago_pos, name='ticket_pago_pos'),

    # ========== QZ TRAY — IMPRESIÓN TÉRMICA ==========
    path('qz/certificado/', views.qz_certificado, name='qz_certificado'),
    path('qz/firmar/', views.qz_firmar, name='qz_firmar'),
    path('qz/config/', views.qz_config_sucursal, name='qz_config_sucursal'),

    # ========== NUEVO POS DASHBOARD ==========
    path('pos-dashboard/', pos_dashboard, name='pos_dashboard'),
    path('api/dashboard/stats/', dashboard_stats, name='dashboard_stats'),
    path('api/correlativos/verificar/', verificar_correlativos_disponibles, name='verificar_correlativos_disponibles'),
    path('api/validar-rut/', validar_rut_cliente, name='validar_rut_cliente'),
    path('api/buscar-cliente/', buscar_cliente_rut, name='buscar_cliente_rut'),
    path('api/guardar-cliente-pos/', guardar_cliente_pos, name='guardar_cliente_pos'),
    path('api/enviar-ticket-email/', enviar_ticket_email, name='enviar_ticket_email'),
    path('api/buscar-productos-pos-avanzado/', buscar_productos_pos_avanzado, name='buscar_productos_pos_avanzado'),
    
    # === GESTIÓN DE DOCUMENTOS DE VENTAS ===
    path('ventas/documentos/', gestion_ventas_documentos, name='gestion_ventas_documentos'),
    path('api/ventas/documentos/', listar_documentos_ventas, name='listar_documentos_ventas'),
    path('api/ventas/exportar-documentos/', exportar_documentos_ventas_excel, name='exportar_documentos_ventas_excel'),
    path('api/ventas/convertir-factura/', convertir_ticket_a_factura, name='convertir_ticket_a_factura'),
    path('api/ventas/documento/<int:documento_id>/', detalle_documento_venta, name='detalle_documento_venta'),
    path('api/ventas/anular-documento/', anular_documento_venta, name='anular_documento_venta'),
    path('api/ventas/eliminar-documento/', eliminar_documento_venta, name='eliminar_documento_venta'),
    path('api/ventas/editar-boleta-papel/', editar_dte_boleta_papel, name='editar_dte_boleta_papel'),
    path('api/ventas/documentos/crear-manual/', crear_dte_manual, name='crear_dte_manual'),
    
    # === CUADRATURA Y ARQUEO DE CAJA ===
    path('ventas/cuadratura-caja/', cuadratura_caja, name='cuadratura_caja'),
    path('ventas/revision-arqueos/', revision_arqueos, name='revision_arqueos'),
    path('api/cuadratura/generar/', generar_cuadratura_caja, name='generar_cuadratura_caja'),
    path('api/cuadratura/detalle-metodos-pago/', obtener_detalle_cuadratura_metodos_pago, name='obtener_detalle_cuadratura_metodos_pago'),
    path('api/cuadratura/editar-fecha-pago-nc/', editar_fecha_pago_nc, name='editar_fecha_pago_nc'),
    path('api/cuadratura/sincronizar-fecha-ticket-dte/', sincronizar_fecha_ticket_dte, name='sincronizar_fecha_ticket_dte'),
    path('api/cuadratura/editar-fecha-ticket/', editar_fecha_ticket_sin_dte, name='editar_fecha_ticket_sin_dte'),
    path('api/cuadratura/guardar/', guardar_cuadratura_completa, name='guardar_cuadratura_completa'),
    path('api/cuadratura/verificar-existente/', verificar_cuadratura_existente, name='verificar_cuadratura_existente'),
    path('api/cuadratura/eliminar/<int:arqueo_id>/', eliminar_cuadratura, name='eliminar_cuadratura'),
    path('api/cuadratura/listar/', listar_cuadraturas, name='listar_cuadraturas'),
    path('api/obtener-sucursales/', obtener_sucursales, name='obtener_sucursales'),
    path('api/cuadratura/detalle/<int:arqueo_id>/', obtener_detalle_arqueo, name='obtener_detalle_arqueo'),
    path('api/cuadratura/editar/<int:arqueo_id>/', editar_cuadratura, name='editar_cuadratura'),
    path('api/cuadratura/exportar/', exportar_cuadratura_excel, name='exportar_cuadratura_excel'),
    path('api/cuadratura/transacciones-dia/', obtener_transacciones_dia, name='obtener_transacciones_dia'),
    path('api/cuadratura/agregar-deposito/', agregar_deposito_arqueo, name='agregar_deposito_arqueo'),
    path('api/cuadratura/eliminar-deposito/', eliminar_deposito_bancario, name='eliminar_deposito_bancario'),
    path('api/cuadratura/cambiar-fecha-deposito/', cambiar_fecha_deposito, name='cambiar_fecha_deposito'),
    path('api/cuadratura/deposito/declarar/', declarar_deposito, name='declarar_deposito'),
    path('api/cuadratura/deposito/finalizar/', finalizar_declaracion, name='finalizar_declaracion'),
    path('api/cuadratura/deposito/<int:deposito_id>/confirmar/', confirmar_deposito, name='confirmar_deposito'),
    path('api/cuadratura/deposito/pendientes/', obtener_depositos_pendientes, name='obtener_depositos_pendientes'),
    # Depósito multi-día
    path('api/cuadratura/deposito-multidia/arqueos-disponibles/', listar_arqueos_para_deposito, name='listar_arqueos_para_deposito'),
    path('api/cuadratura/deposito-multidia/crear/', crear_deposito_multidia, name='crear_deposito_multidia'),
    path('api/cuadratura/deposito-multidia/<int:grupo_id>/', detalle_grupo_deposito, name='detalle_grupo_deposito'),
    
    # URLs para arqueo mejorado
    path('api/arqueos/', listar_arqueos, name='listar_arqueos'),
    path('api/arqueo/crear/', crear_arqueo, name='crear_arqueo'),
    path('api/arqueo/conteo/', guardar_conteo_fisico, name='guardar_conteo_fisico'),
    path('api/arqueo/cerrar/', cerrar_arqueo, name='cerrar_arqueo'),
    path('api/arqueo/corregir-express/', corregir_arqueos_express, name='corregir_arqueos_express'),
    path('api/arqueo/<int:arqueo_id>/', obtener_arqueo_detalle, name='obtener_arqueo_detalle'),
    path('api/arqueo/<int:arqueo_id>/recalcular-teoricos/', recalcular_teoricos_arqueo, name='recalcular_teoricos_arqueo'),
    path('api/arqueo/verificar-ventas-post-cierre/', verificar_ventas_post_cierre, name='verificar_ventas_post_cierre'),
    path('api/arqueo/reabrir/', reabrir_arqueo, name='reabrir_arqueo'),
    path('api/arqueo/cancelar/', cancelar_arqueo, name='cancelar_arqueo'),
    
    # Funciones de supervisión (Administración/Administrador)
    path('api/arqueo/revisar/', revisar_arqueo, name='revisar_arqueo'),
    path('api/arqueo/revisar-lote/', revisar_arqueos_lote, name='revisar_arqueos_lote'),
    path('api/arqueo/comprobante/', registrar_comprobante_supervisor, name='registrar_comprobante_supervisor'),
    path('api/arqueo/depositos/<int:arqueo_id>/', obtener_depositos_arqueo, name='obtener_depositos_arqueo'),
    path('api/deposito/verificar/', verificar_deposito, name='verificar_deposito'),
    path('api/arqueo/analisis-fraude/', analisis_fraude_caja, name='analisis_fraude_caja'),
    
    # Bitácora y bloqueos
    path('api/arqueo/observacion/crear/', crear_observacion_arqueo, name='crear_observacion_arqueo'),
    path('api/arqueo/<int:arqueo_id>/bitacora/', obtener_bitacora_arqueo, name='obtener_bitacora_arqueo'),
    path('api/arqueo/bloqueos/<str:fecha>/', obtener_bloqueos_arqueo, name='obtener_bloqueos_arqueo'),

    # ========== MÓDULO DOCUMENTOS ==========
    # === Gestión de DTEs ===
    path('documentos/gestion-dte/', views.gestion_dte, name='gestion_dte'),
    path('documentos/anular-factura/', views.anular_factura_dte, name='anular_factura_dte'),
    path('documentos/editar-folio-dte/', views.editar_folio_dte, name='editar_folio_dte'),
    path('documentos/asignar-receptor-dte/', views.asignar_receptor_dte, name='asignar_receptor_dte'),
    path('documentos/api/cargar-dte-ventas/', views.cargar_dte_ventas, name='cargar_dte_ventas'),
    path('documentos/dte/<int:dte_id>/lineas-disponibles-nc/', views.lineas_disponibles_nc_api, name='lineas_disponibles_nc_api'),
    path('documentos/api/dte/<int:dte_id>/', views.detalle_dte, name='detalle_dte'),
    path('detalle_dte/<int:dte_id>/', views.vista_detalle_dte, name='vista_detalle_dte'),  # Vista HTML
    path('api/detalle_dte_completo/<int:dte_id>/', views.api_detalle_dte_completo, name='api_detalle_dte_completo'),  # API completa
    path('api/dte/<int:dte_id>/trazabilidad/', views.api_dte_trazabilidad, name='api_dte_trazabilidad'),  # Árbol de trazabilidad
    path('api/dte/ncs_sin_stock/', views.api_ncs_sin_stock, name='api_ncs_sin_stock'),  # Diagnóstico NCs sin movimientos
    path('dte/<int:nc_id>/reparar_stock/', views.api_reparar_stock_nc, name='api_reparar_stock_nc'),  # Reparación retroactiva
    path('api/dte/<int:dte_id>/diagnostico_reparacion_traspaso/', views.api_diagnostico_reparacion_traspaso, name='api_diagnostico_reparacion_traspaso'),  # Diagnóstico de traspasos con trazabilidad rota
    path('api/dte/<int:dte_id>/reparar_traspaso_manual/', views.api_reparar_traspaso_manual, name='api_reparar_traspaso_manual'),  # Reparación manual de traspasos con líneas huérfanas
    path('api/dte/<int:dte_id>/crear_skus_destino/', views.api_crear_skus_destino, name='api_crear_skus_destino'),  # Replica SKUs faltantes en sucursal destino
    path('api/dte/<int:dte_id>/crear_stock_destino_manual/', views.api_crear_stock_destino_manual, name='api_crear_stock_destino_manual'),  # Recepción manual de TRASPASO sin NC
    path('api/productos/stock/', views.api_stock_productos, name='api_stock_productos'),  # Refresh de stock por SKU
    
    # === Gestión de Correlativos ===
    path('documentos/gestion-correlativos/', views.gestion_correlativos, name='gestion_correlativos'),
    path('correlativos/guardar/', views.guardar_correlativo, name='guardar_correlativo'),
    path('correlativos/obtener/<int:correlativo_id>/', views.obtener_correlativo, name='obtener_correlativo'),
    path('correlativos/renovar/', views.renovar_correlativo, name='renovar_correlativo'),
    path('correlativos/historial/<int:correlativo_id>/', views.historial_correlativo, name='historial_correlativo'),
    path('correlativos/eliminar/<int:correlativo_id>/', views.eliminar_correlativo, name='eliminar_correlativo'),
    path('correlativos/faltantes/', views.obtener_correlativos_faltantes, name='obtener_correlativos_faltantes'),
    path('correlativos/crear-faltantes/', views.crear_correlativos_faltantes, name='crear_correlativos_faltantes'),
    path('correlativos/exportar-pdf/', views.exportar_correlativos_pdf, name='exportar_correlativos_pdf'),
    
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
    path('api/creditos/ajustar-monto/', ajustar_monto_credito, name='ajustar_monto_credito'),
    path('api/creditos/pago/', registrar_pago_credito, name='registrar_pago_credito'),
    path('api/creditos/firma/', registrar_firma_credito, name='registrar_firma_credito'),
    path('api/creditos/trabajadores/', obtener_trabajadores_credito, name='obtener_trabajadores_credito'),
    path('api/creditos/trabajadores/crear/', crear_trabajador_credito, name='crear_trabajador_credito'),
    path('api/creditos/trabajadores/actualizar/', actualizar_trabajador_credito, name='actualizar_trabajador_credito'),
    path('api/creditos/trabajadores/validar-codigo/', validar_codigo_trabajador, name='validar_codigo_trabajador'),
    path('api/creditos/sucursales/', obtener_sucursales_empresa, name='obtener_sucursales_empresa'),
    path('api/empresas/', obtener_empresas_disponibles, name='obtener_empresas_disponibles'),
    path('api/creditos/reporte/', reporte_creditos_trabajadores, name='reporte_creditos_trabajadores'),
    path('api/creditos/exportar-pdf/', exportar_creditos_pdf, name='exportar_creditos_pdf'),
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
    # Vista principal con SDK oficial de Transbank
    path('pos/transbank/', gestion_transbank_pos_sdk, name='gestion_transbank_pos_sdk'),
    path('pos/transbank-manual/', gestion_transbank_pos_manual, name='gestion_transbank_pos_manual'),
    path('testTransbank/', lambda request: render(request, 'test_transbank_pos.html'), name='test_transbank'),
    
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
    path('ventas/api/aprobar-cambio-generar-ticket/', aprobar_cambio_generar_ticket, name='aprobar_cambio_generar_ticket'),
    path('ventas/api/validar-codigo-vendedor/', validar_codigo_vendedor, name='validar_codigo_vendedor'),
    path('ventas/api/cancelar-cambio-devolucion/', cancelar_cambio_devolucion, name='cancelar_cambio_devolucion'),
    path('ventas/api/revertir-cambio-devolucion/', revertir_cambio_devolucion, name='revertir_cambio_devolucion'),
    
    # === APIs para Códigos de Autorización Dinámicos ===
    path('ventas/api/codigo-autorizacion/actual/', obtener_codigo_autorizacion_actual, name='obtener_codigo_autorizacion_actual'),
    path('ventas/api/codigo-autorizacion/validar/', validar_codigo_autorizacion, name='validar_codigo_autorizacion'),
    path('ventas/api/ejecutar-cambio-devolucion/', ejecutar_cambio_devolucion, name='ejecutar_cambio_devolucion'),
    path('ventas/api/registrar-pago-diferencia/', registrar_pago_diferencia, name='registrar_pago_diferencia'),
    path('ventas/api/condonar-diferencia-cobro/', condonar_diferencia_cobro, name='condonar_diferencia_cobro'),
    path('ventas/api/ajustar-diferencia-cobro/', ajustar_diferencia_cobro, name='ajustar_diferencia_cobro'),
    path('ventas/api/completar-cambio-devolucion/', completar_cambio_devolucion, name='completar_cambio_devolucion'),
    
    # APIs de búsqueda
    path('ventas/api/buscar-ticket-cambio/', buscar_ticket_para_cambio, name='buscar_ticket_para_cambio'),
    path('ventas/api/buscar-documento-cambio/', buscar_documento_cambio, name='buscar_documento_cambio'),
    path('ventas/api/buscar-productos-cambio/', buscar_productos_para_cambio, name='buscar_productos_para_cambio'),

    # APIs de análisis avanzado y detección de fraude
    path('ventas/api/analisis-fraude-cambios/', obtener_analisis_fraude_cambios, name='obtener_analisis_fraude_cambios'),
    path('ventas/api/analisis-cambios-avanzado/', obtener_analisis_cambios_avanzado, name='obtener_analisis_cambios_avanzado'),
    path('ventas/api/autorizaciones-cross-branch/', listar_autorizaciones_cross_branch, name='listar_autorizaciones_cross_branch'),
    path('ventas/api/revisar-autorizacion/<int:registro_id>/', revisar_autorizacion, name='revisar_autorizacion'),
    path('ventas/api/cola-revision-gerencial/', obtener_cola_revision_gerencial, name='obtener_cola_revision_gerencial'),
    path('ventas/api/revisar-cambio-gerencial/', revisar_cambio_gerencial, name='revisar_cambio_gerencial'),
    path('ventas/api/exportar-cambios/', exportar_cambios_devoluciones, name='exportar_cambios_devoluciones'),

    # APIs de Notas de Crédito desde Devoluciones
    path('ventas/api/generar-nc-devolucion/', generar_nc_devolucion, name='generar_nc_devolucion'),
    path('ventas/api/nc-devolucion/<int:cambio_id>/', detalle_nc_devolucion, name='detalle_nc_devolucion'),

    # ========== MÓDULO DE COTIZACIONES ==========
    # Vista principal
    path('cotizaciones/', gestion_cotizaciones, name='gestion_cotizaciones'),
    
    # APIs de listado y consulta
    path('api/cotizaciones/', listar_cotizaciones, name='listar_cotizaciones'),
    path('api/cotizaciones/<int:cotizacion_id>/', detalle_cotizacion, name='detalle_cotizacion'),
    path('api/cotizaciones/<int:cotizacion_id>/pdf/', cotizacion_pdf, name='cotizacion_pdf'),
    path('api/cotizaciones/<int:cotizacion_id>/enviar-correo/', enviar_cotizacion_correo, name='enviar_cotizacion_correo'),

    # APIs de creación y edición
    path('api/cotizaciones/crear/', crear_cotizacion, name='crear_cotizacion'),
    path('api/cotizaciones/<int:cotizacion_id>/editar/', editar_cotizacion, name='editar_cotizacion'),
    
    # APIs de acciones
    path('api/cotizaciones/anular/', anular_cotizacion, name='anular_cotizacion'),
    path('api/cotizaciones/convertir-factura/', convertir_cotizacion_factura, name='convertir_cotizacion_factura'),
    # Reabrir una cotización FACTURADA cuyo DTE fue eliminado/anulado, para
    # poder re-facturarla. Sin esto la cotización queda sin salida (no se puede
    # facturar, ni editar, ni anular).
    path('api/cotizaciones/reabrir/', reabrir_cotizacion, name='reabrir_cotizacion'),
    path('api/cotizaciones/asignar-sku-pendiente/', asignar_sku_pendiente, name='asignar_sku_pendiente'),
    path('api/cotizaciones/revertir-sku-despachado/', revertir_sku_despachado, name='revertir_sku_despachado'),
    path('api/cotizaciones/validar-despacho/', validar_despacho_cotizacion, name='validar_despacho_cotizacion'),
    
    # APIs de integración POS
    path('api/cotizaciones/cargar-como-ticket/<int:cotizacion_id>/', cargar_cotizacion_como_ticket, name='cargar_cotizacion_como_ticket'),
    
    # APIs de búsqueda
    path('api/cotizaciones/buscar-productos/', buscar_productos_cotizacion, name='buscar_productos_cotizacion'),

    # APIs de vendedores
    path('api/cotizaciones/vendedores/', listar_vendedores_cotizacion, name='listar_vendedores_cotizacion'),

    # APIs de clientes
    path('api/cotizaciones/crear-cliente/', crear_cliente_cotizacion, name='crear_cliente_cotizacion'),
    path('api/actualizar-email-cliente/', actualizar_email_cliente, name='actualizar_email_cliente'),

    # ========== DASHBOARD DE VENTAS ==========
    # `dashboard_ventas` renderizaba el MISMO template que el mejorado pero sin
    # el contexto de categorías v1.2, así que los filtros salían vacíos. Se
    # mantiene la ruta como redirección para no romper enlaces guardados.
    path('ventas/dashboard/', RedirectView.as_view(
        pattern_name='dashboard_ventas_mejorado', permanent=False,
    ), name='dashboard_ventas'),
    path('ventas/dashboard-mejorado/', dashboard_ventas_mejorado, name='dashboard_ventas_mejorado'),
    
    # APIs de indicadores y métricas
    path('api/ventas/indicadores-globales/', obtener_indicadores_globales_ventas, name='obtener_indicadores_globales_ventas'),
    path('api/ventas/por-vendedor/', obtener_ventas_por_vendedor, name='obtener_ventas_por_vendedor'),
    path('api/ventas/por-sucursal/', obtener_ventas_por_sucursal, name='obtener_ventas_por_sucursal'),
    path('api/ventas/sucursales-dashboard/', obtener_sucursales_dashboard, name='obtener_sucursales_dashboard'),
    path('api/ventas/por-metodo-pago/', obtener_ventas_por_metodo_pago, name='obtener_ventas_por_metodo_pago'),
    path('api/ventas/analisis-cambios/', obtener_analisis_cambios_devoluciones, name='obtener_analisis_cambios_devoluciones'),
    path('api/ventas/estado-cuadraturas/', obtener_estado_cuadraturas, name='obtener_estado_cuadraturas'),
    path('api/ventas/productos-mas-vendidos/', obtener_productos_mas_vendidos, name='obtener_productos_mas_vendidos'),
    path('api/ventas/por-categoria/', obtener_ventas_por_categoria, name='obtener_ventas_por_categoria'),
    path('api/ventas/por-especialidad/', obtener_ventas_por_especialidad, name='obtener_ventas_por_especialidad'),
    path('api/ventas/indicador-compra/', obtener_indicador_compra_categoria, name='obtener_indicador_compra_categoria'),
    path('api/ventas/mix-por-sucursal/', obtener_mix_por_sucursal, name='obtener_mix_por_sucursal'),
    path('api/ventas/tendencias/', obtener_tendencias_ventas, name='obtener_tendencias_ventas'),
    path('api/ventas/indicadores-avanzados/', obtener_indicadores_avanzados_ventas, name='obtener_indicadores_avanzados_ventas'),
    path('api/ventas/estado-operacional/', obtener_estado_operacional_ventas, name='obtener_estado_operacional_ventas'),
    
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
    path('gestion-precios/historial-reciente/', obtener_historial_ediciones_recientes, name='obtener_historial_ediciones_recientes'),
    path('gestion-precios/kpis-cambios/', obtener_kpis_cambios_precios, name='obtener_kpis_cambios_precios'),
    path('gestion-precios/sucursales-similares/<int:producto_id>/', buscar_productos_similares_sucursales, name='buscar_productos_similares_sucursales'),
    
    # ========== SISTEMA DE APROBACIÓN DE CAMBIOS DE PRECIOS ==========
    path('gestion-precios/proponer-cambio/', proponer_cambio_precio, name='proponer_cambio_precio'),
    path('gestion-precios/indicadores-pendientes/', obtener_indicadores_precios_pendientes, name='indicadores_precios_pendientes'),
    path('gestion-precios/listar-cambios/', listar_cambios_pendientes, name='listar_cambios_pendientes'),
    path('gestion-precios/exportar-excel/', exportar_cambios_precios_excel, name='exportar_cambios_precios_excel'),
    path('gestion-precios/eliminar-cambios-aplicados/', eliminar_cambios_aplicados, name='eliminar_cambios_aplicados'),
    path('gestion-precios/revisar-cambio/', revisar_cambio_precio, name='revisar_cambio_precio'),
    path('gestion-precios/aprobar-cambio/', aprobar_cambio_precio, name='aprobar_cambio_precio'),
    path('gestion-precios/rechazar-cambio/', rechazar_cambio_precio, name='rechazar_cambio_precio'),
    path('gestion-precios/notificaciones/', obtener_notificaciones_precio, name='obtener_notificaciones_precio'),
    path('gestion-precios/marcar-notificacion/', marcar_notificacion_leida, name='marcar_notificacion_leida'),
    path('gestion-precios/marcar-leida-por-cambio/<int:cambio_id>/', marcar_notificacion_leida_por_cambio, name='marcar_notificacion_leida_por_cambio'),
    path('gestion-precios/eliminar-notificaciones/', eliminar_notificaciones_precio, name='eliminar_notificaciones_precio'),
    
    # ========== NOTIFICACIONES DE DTEs RECIBIDOS ==========
    path('notificaciones-dte/', views.obtener_notificaciones_dte, name='obtener_notificaciones_dte'),
    path('notificaciones-dte/marcar-leida/', views.marcar_notificacion_dte_leida, name='marcar_notificacion_dte_leida'),
    path('notificaciones-dte/eliminar/', views.eliminar_notificacion_dte, name='eliminar_notificacion_dte'),
    path('notificaciones-dte/descartar-todas/', views.descartar_todas_notificaciones_dte, name='descartar_todas_notificaciones_dte'),
    path('dtes-pendientes-recibir/', views.obtener_dtes_pendientes_recibir, name='obtener_dtes_pendientes_recibir'),
    path('dtes-pendientes-recibir/descartar/', views.descartar_dte_pendiente, name='descartar_dte_pendiente'),
    path('dtes-pendientes-regularizar/', views.obtener_dtes_pendientes_regularizar, name='obtener_dtes_pendientes_regularizar'),

    # Regularización de precios entre sucursales
    path('gestion-precios/discrepancias/', detectar_discrepancias_precios, name='detectar_discrepancias_precios'),
    path('gestion-precios/regularizar-sucursales/', regularizar_precio_sucursales, name='regularizar_precio_sucursales'),
    path('gestion-precios/resumen-discrepancias/', resumen_discrepancias_precios, name='resumen_discrepancias_precios'),
    
    # Endpoint temporal de debug
    path('gestion-precios/debug-session/', debug_session_precios, name='debug_session_precios'),

    # ========== MÓDULO CONFIGURACIÓN — INTEGRACIONES ECOMMERCE ==========
    path('configuracion/integraciones-ecommerce/',
         views_modulo_configuracion.integraciones_ecommerce,
         name='integraciones_ecommerce'),
    path('configuracion/integraciones-ecommerce/guardar/',
         views_modulo_configuracion.guardar_integracion_ecommerce,
         name='guardar_integracion_ecommerce'),
    path('configuracion/integraciones-ecommerce/<int:pk>/eliminar/',
         views_modulo_configuracion.eliminar_integracion_ecommerce,
         name='eliminar_integracion_ecommerce'),
    path('configuracion/integraciones-ecommerce/<int:pk>/probar/',
         views_modulo_configuracion.probar_integracion_ecommerce,
         name='probar_integracion_ecommerce'),
    path('configuracion/integraciones-ecommerce/<int:pk>/sincronizar/',
         views_modulo_configuracion.sincronizar_integracion_ecommerce,
         name='sincronizar_integracion_ecommerce'),
    path('configuracion/integraciones-ecommerce/<int:pk>/verificar/',
         views_modulo_configuracion.verificar_integracion_ecommerce,
         name='verificar_integracion_ecommerce'),

    # ========== MÓDULO DE GENERACIÓN DE ARCHIVOS TXT ACEPTA ==========
    path('configuracion/interfaz-prueba-acepta/', views_modulo_documentos.interfaz_prueba_acepta, name='interfaz_prueba_acepta'),
    path('documentos/generar-txt-acepta/', views_modulo_documentos.generar_txt_acepta_api, name='generar_txt_acepta_api'),
    path('documentos/generar-txt-desde-dte/', views_modulo_documentos.generar_txt_desde_dte_existente, name='generar_txt_desde_dte_existente'),
    path('documentos/generar-dte-ticket/', views_modulo_documentos.generar_dte_desde_ticket_api, name='generar_dte_desde_ticket_api'),
    path('documentos/importar-txt-acepta/', views_modulo_documentos.importar_txt_acepta_api, name='importar_txt_acepta_api'),

    # ========== MÓDULO DE REPORTE DE EXISTENCIAS ==========

    # ========== TARJETA DE MOVIMIENTO POR PRODUCTO ==========
    path('tarjeta-movimiento/', tarjeta_movimiento_producto, name='tarjeta_movimiento_producto'),
    path('api/tarjeta-movimiento/', api_tarjeta_movimiento, name='api_tarjeta_movimiento'),
    path('api/tarjeta-movimiento/buscar/', api_buscar_productos_tarjeta_movimiento, name='api_buscar_productos_tarjeta_movimiento'),
    path('api/actividad-creacion-manual/', api_actividad_creacion_manual, name='api_actividad_creacion_manual'),

    # Acciones de la tabla "Actividad reciente" de verGestionProducto:
    # edición rápida del ingreso y corrección del DTE mal asignado.
    path('api/ingreso-manual/ficha/', api_evento_ingreso_manual, name='api_evento_ingreso_manual'),
    path('api/ingreso-manual/sumar-stock/', api_sumar_stock_rapido, name='api_sumar_stock_rapido'),
    path('api/ingreso-manual/preview-reasignar-dte/', api_preview_reasignar_dte, name='api_preview_reasignar_dte'),
    path('api/ingreso-manual/reasignar-dte/', api_reasignar_dte_ingreso, name='api_reasignar_dte_ingreso'),

    # ========== DESPACHO A TODAS SUCURSALES ==========
    path('despacho-sucursales/', despacho_todas_sucursales, name='despacho_todas_sucursales'),
    path('api/despacho/sucursales/', api_obtener_sucursales_despacho, name='api_obtener_sucursales_despacho'),
    path('api/despacho/productos/', api_productos_disponibles_despacho, name='api_productos_disponibles_despacho'),
    path('api/despacho/pendientes/', api_pendientes_despacho_sucursal, name='api_pendientes_despacho_sucursal'),
    path('api/despacho/crear-masivo/', api_crear_despacho_masivo, name='api_crear_despacho_masivo'),
    path('api/despacho/marcas/', api_marcas_disponibles_despacho, name='api_marcas_disponibles_despacho'),
    path('api/despacho/historial/', api_historial_despachos, name='api_historial_despachos'),

    # ========== TRAZABILIDAD COMPLETA DE PRODUCTO ==========
    path('trazabilidad-producto/', trazabilidad_producto, name='trazabilidad_producto'),
    path('api/trazabilidad-producto/', api_trazabilidad_producto, name='api_trazabilidad_producto'),

    # ========== MODIFICACIÓN DE PRECIOS Y COSTOS ==========
    path('precios-costos/', modificacion_precios_costos, name='modificacion_precios_costos'),
    path('api/precios-costos/buscar/', api_buscar_productos_precios, name='api_buscar_productos_precios'),
    path('api/precios-costos/modificar/', api_modificar_precio_costo, name='api_modificar_precio_costo'),
    path('api/precios-costos/modificar-masivo/', api_modificar_precios_masivo, name='api_modificar_precios_masivo'),

    # Corrección de talla / categoría mal registradas — replican a TODAS las bodegas (modal Crear Manual)
    path('api/editar-talla-global/', api_editar_talla_producto_global, name='api_editar_talla_producto_global'),
    path('api/editar-categoria-global/', api_editar_categoria_producto_global, name='api_editar_categoria_producto_global'),
    path('api/editar-especialidad-global/', api_editar_especialidad_producto_global, name='api_editar_especialidad_producto_global'),

    path('reportes/existencias/', views.ver_reporte_existencias, name='ver_reporte_existencias'),
    path('api/obtener-existencias/', views.obtener_existencias_reporte, name='obtener_existencias_reporte'),
    path('api/exportar-existencias-excel/', views.exportar_existencias_excel, name='exportar_existencias_excel'),

    # ========== MÓDULO DE REPORTE DE VENTAS ==========
    path('reportes/ventas-sucursal/', views_modulo_reportes.ver_reporte_ventas_sucursal, name='ver_reporte_ventas_sucursal'),
    path('api/reportes/ventas-por-vendedor/', views_modulo_reportes.obtener_ventas_por_vendedor_reporte, name='obtener_ventas_por_vendedor_reporte'),
    path('api/reportes/ventas-por-sucursal/', views_modulo_reportes.obtener_ventas_por_sucursal_reporte, name='obtener_ventas_por_sucursal_reporte'),
    path('api/reportes/diagnostico-cuadratura/', views_modulo_reportes.api_diagnostico_cuadratura_vs_reporte, name='api_diagnostico_cuadratura'),
    path('api/reportes/comisiones-vendedor/', views_modulo_reportes.obtener_comisiones_por_vendedor, name='obtener_comisiones_por_vendedor'),
    path('api/reportes/comisiones-vendedor/exportar/', views_modulo_reportes.exportar_comisiones_vendedor_excel, name='exportar_comisiones_vendedor_excel'),
    path('reportes/ventas-global/', views_modulo_reportes.ver_reporte_ventas_global, name='ver_reporte_ventas_global'),
    path('api/reportes/ventas-global-empresa/', views_modulo_reportes.obtener_ventas_global_por_empresa, name='obtener_ventas_global_por_empresa'),
    path('reportes/ventas-internet/', views_modulo_reportes.ver_reporte_ventas_internet, name='ver_reporte_ventas_internet'),
    path('api/reportes/ventas-internet/', views_modulo_reportes.obtener_reporte_ventas_internet, name='obtener_reporte_ventas_internet'),
    path('reportes/ventas-internet/exportar/', views_modulo_reportes.exportar_reporte_ventas_internet, name='exportar_reporte_ventas_internet'),
    path('reportes/ventas-comparativo/', views_modulo_reportes.ver_reporte_ventas_comparativo, name='ver_reporte_ventas_comparativo'),
    path('api/reportes/ventas-comparativo/', views_modulo_reportes.obtener_ventas_comparativo, name='obtener_ventas_comparativo'),
    path('reportes/productos-vendidos/', views_modulo_reportes.ver_reporte_productos_vendidos, name='ver_reporte_productos_vendidos'),
    path('api/reportes/productos-vendidos/', views_modulo_reportes.obtener_productos_vendidos, name='obtener_productos_vendidos'),
    path('api/reportes/atributo-opciones/', views_modulo_reportes.obtener_atributo_opciones, name='obtener_atributo_opciones'),
    path('api/reportes/vendedores/', views_modulo_reportes.obtener_vendedores_reporte, name='obtener_vendedores_reporte'),
    path('api/reportes/sucursales/', views_modulo_reportes.obtener_sucursales_reporte, name='obtener_sucursales_reporte'),
    path('api/reportes/comparativa-mensual/', views_modulo_reportes.obtener_comparativa_mensual, name='obtener_comparativa_mensual'),
    path('api/reportes/documentos-vendedor/', views_modulo_reportes.obtener_documentos_vendedor_reporte, name='obtener_documentos_vendedor_reporte'),
    path('reportes/documentos-emitidos/', views_modulo_reportes.ver_documentos_emitidos, name='ver_documentos_emitidos'),
    path('api/reportes/documentos-emitidos/', views_modulo_reportes.obtener_documentos_emitidos, name='obtener_documentos_emitidos'),
    path('api/reportes/documentos-emitidos-excel/', views_modulo_reportes.exportar_documentos_emitidos_excel, name='exportar_documentos_emitidos_excel'),
    
    # Reporte de compras integral (NEXO Design System)
    path('reportes/compras/', views_modulo_reportes.ver_reporte_compras, name='ver_reporte_compras'),
    path('api/reporte-compras/', views_modulo_reportes.api_reporte_compras, name='api_reporte_compras'),
    path('api/exportar-reporte-compras-excel/', views_modulo_reportes.exportar_reporte_compras_excel, name='exportar_reporte_compras_excel'),
    path('api/rendimiento-compras/', views_modulo_reportes.api_rendimiento_compras, name='api_rendimiento_compras'),

    # Reporte: productos creados por origen (alta por compra / manual / traspaso / ajuste)
    path('reportes/productos-origen/', views_modulo_reportes.ver_reporte_productos_origen, name='ver_reporte_productos_origen'),
    path('api/reportes/productos-origen/', views_modulo_reportes.api_productos_por_origen, name='api_productos_por_origen'),

    # Reporte de rendimiento por proveedor (compra -> recepcion -> venta)
    path('reportes/rendimiento-proveedor/', views_modulo_reportes.ver_reporte_rendimiento_proveedor, name='ver_reporte_rendimiento_proveedor'),
    path('api/reporte-rendimiento-proveedor/', views_modulo_reportes.api_reporte_rendimiento_proveedor, name='api_reporte_rendimiento_proveedor'),
    path('api/exportar-rendimiento-proveedor-excel/', views_modulo_reportes.exportar_rendimiento_proveedor_excel, name='exportar_rendimiento_proveedor_excel'),

    # Los reportes "mejorados" recepciones/despachos-detallado se ELIMINARON en
    # la Fase C de la auditoría ago-2026: huérfanos confirmados (0 consumidores
    # en templates/JS), y despachos-detallado además sumaba NC y descartados
    # con un N+1 de ~125 queries. Ver docs/AUDITORIA_REPORTES_2026-08.md §4.

    # Inteligencia de Compra (análisis + pronóstico por marca)
    path('reportes/inteligencia-compra/', views_inteligencia_compra.ver_inteligencia_compra, name='ver_inteligencia_compra'),
    path('api/inteligencia-compra/', views_inteligencia_compra.obtener_inteligencia_compra, name='obtener_inteligencia_compra'),
    # Plan de Liquidación (ranking multi-dimensión + drill-down + export)
    path('reportes/plan-liquidacion/', views_inteligencia_compra.ver_plan_liquidacion, name='ver_plan_liquidacion'),
    path('api/plan-liquidacion/', views_inteligencia_compra.obtener_plan_liquidacion, name='obtener_plan_liquidacion'),
    path('api/plan-liquidacion/detalle/', views_inteligencia_compra.obtener_plan_liquidacion_detalle, name='obtener_plan_liquidacion_detalle'),
    path('api/plan-liquidacion/por-anio/', views_inteligencia_compra.obtener_plan_liquidacion_por_anio, name='obtener_plan_liquidacion_por_anio'),
    path('api/plan-liquidacion/exportar-excel/', views_inteligencia_compra.exportar_plan_liquidacion_excel, name='exportar_plan_liquidacion_excel'),
    path('api/plan-liquidacion/importar/', views_inteligencia_compra.importar_seleccion_liquidacion, name='importar_seleccion_liquidacion'),
    path('reportes/plan-liquidacion/imprimir/', views_inteligencia_compra.imprimir_plan_liquidacion, name='imprimir_plan_liquidacion'),

    # Campañas de liquidación (gestión masiva de precios + promos NxM)
    path('campanas-liquidacion/', views_modulo_campanas_liquidacion.ver_campanas_liquidacion, name='ver_campanas_liquidacion'),
    path('api/campanas-liquidacion/', views_modulo_campanas_liquidacion.listar_campanas_liquidacion, name='listar_campanas_liquidacion'),
    path('api/campanas-liquidacion/crear/', views_modulo_campanas_liquidacion.crear_campana_liquidacion, name='crear_campana_liquidacion'),
    path('api/campanas-liquidacion/<int:campana_id>/', views_modulo_campanas_liquidacion.detalle_campana_liquidacion, name='detalle_campana_liquidacion'),
    path('api/campanas-liquidacion/<int:campana_id>/productos/', views_modulo_campanas_liquidacion.modificar_productos_campana, name='modificar_productos_campana'),
    path('api/campanas-liquidacion/<int:campana_id>/activar/', views_modulo_campanas_liquidacion.activar_campana_liquidacion, name='activar_campana_liquidacion'),
    path('api/campanas-liquidacion/<int:campana_id>/cerrar/', views_modulo_campanas_liquidacion.cerrar_campana_liquidacion, name='cerrar_campana_liquidacion'),
    path('api/promos-activas/', views_modulo_campanas_liquidacion.obtener_promos_activas, name='obtener_promos_activas'),
    path('api/ofertas-activas/', views_modulo_campanas_liquidacion.obtener_ofertas_activas, name='obtener_ofertas_activas'),

    # Reporte de existencias por marca
    path('reportes/existencias-marca/', views_modulo_reportes.ver_reporte_existencias_marca, name='ver_reporte_existencias_marca'),
    path('api/reporte-existencias-marca/', views_modulo_reportes.obtener_reporte_existencias_marca, name='obtener_reporte_existencias_marca'),
    path('api/exportar-existencias-marca-excel/', views_modulo_reportes.exportar_existencias_marca_excel, name='exportar_existencias_marca_excel'),
    
    # Reporte de existencias por sucursal
    path('reportes/existencias-sucursal/', views_modulo_reportes.ver_reporte_existencias_sucursal, name='ver_reporte_existencias_sucursal'),
    # Quiebre de talla: en calzado una talla core en cero es venta perdida
    # inmediata (el cliente no transa el calce). Cuelga del mismo permiso que
    # existencias por sucursal.
    path('reportes/quiebre-talla/', views_modulo_reportes_tallas.ver_reporte_quiebre_talla, name='ver_reporte_quiebre_talla'),
    path('api/reportes/quiebre-talla/', views_modulo_reportes_tallas.api_reporte_quiebre_talla, name='api_reporte_quiebre_talla'),
    path('api/reporte-existencias-sucursal/', views_modulo_reportes.obtener_reporte_existencias_sucursal, name='obtener_reporte_existencias_sucursal'),
    path('api/exportar-existencias-sucursal-excel/', views_modulo_reportes.exportar_existencias_sucursal_excel, name='exportar_existencias_sucursal_excel'),
    path('api/exportar-existencias-sucursal-pdf/', views_modulo_reportes.exportar_existencias_sucursal_pdf, name='exportar_existencias_sucursal_pdf'),
    
    # Reporte de movimientos por sucursal (Inicial vs Restante)
    path('reportes/movimientos-sucursal/', views_modulo_reportes.ver_reporte_movimientos_sucursal, name='ver_reporte_movimientos_sucursal'),
    path('api/reporte-movimientos-sucursal/', views_modulo_reportes.obtener_reporte_movimientos_sucursal, name='obtener_reporte_movimientos_sucursal'),
    path('api/exportar-movimientos-sucursal-excel/', views_modulo_reportes.exportar_movimientos_sucursal_excel, name='exportar_movimientos_sucursal_excel'),

    # Reporte de despachos a tiendas (traspasos de salida)
    path('reportes/despachos-tiendas/', views_modulo_reportes.ver_reporte_despachos_tiendas, name='ver_reporte_despachos_tiendas'),
    path('api/reporte-despachos-tiendas/', views_modulo_reportes.obtener_reporte_despachos_tiendas, name='obtener_reporte_despachos_tiendas'),

    # Reporte de diferencias despachado vs recepcionado (faltantes / dañados /
    # sobrantes de Productos_Recepcionados) y mercadería en tránsito consolidada.
    # Ambas vistas llevan @requiere_permiso con sus códigos dedicados
    # ('reporte_diferencias_recepcion' / 'reporte_mercaderia_transito'),
    # así que no dependen de que la URL esté en URL_PERMISO_MAP.
    path('reportes/diferencias-recepcion/', views_modulo_reportes_diferencias.ver_reporte_diferencias_recepcion, name='ver_reporte_diferencias_recepcion'),
    path('api/reportes/diferencias-recepcion/', views_modulo_reportes_diferencias.api_reporte_diferencias_recepcion, name='api_reporte_diferencias_recepcion'),
    path('reportes/mercaderia-transito/', views_modulo_reportes_diferencias.ver_reporte_mercaderia_transito, name='ver_reporte_mercaderia_transito'),
    path('api/reportes/mercaderia-transito/', views_modulo_reportes_diferencias.api_reporte_mercaderia_transito, name='api_reporte_mercaderia_transito'),
    path('api/reportes/mercaderia-transito/detalle/', views_modulo_reportes_diferencias.api_detalle_mercaderia_transito, name='api_detalle_mercaderia_transito'),


    # Reporte de resumen de existencias
    path('reportes/resumen-existencias/', views_resumen_existencias.ver_resumen_existencias, name='ver_resumen_existencias'),
    path('api/resumen-existencias/', views_resumen_existencias.obtener_resumen_existencias, name='obtener_resumen_existencias'),
    path('api/exportar-resumen-existencias-excel/', views_resumen_existencias.exportar_resumen_existencias_excel, name='exportar_resumen_existencias_excel'),
    path('api/exportar-resumen-existencias-pdf/', views_resumen_existencias.exportar_resumen_existencias_pdf, name='exportar_resumen_existencias_pdf'),
    path('api/verificar-disponibilidad-historico/', views_resumen_existencias.verificar_disponibilidad_historico, name='verificar_disponibilidad_historico'),
    path('api/listar-articulos-para-excluir/', views_resumen_existencias.listar_articulos_para_excluir, name='listar_articulos_para_excluir'),
    path('api/listar-sucursales-resumen/', views_resumen_existencias.listar_sucursales_resumen, name='listar_sucursales_resumen'),
    path('api/detalle-stock-sucursal/', views_resumen_existencias.detalle_stock_sucursal, name='detalle_stock_sucursal'),

    # ========== MÓDULO DE REQUERIMIENTOS DE GARANTÍAS ==========
    # Vistas principales
    path('requerimientos/', modulo_requerimientos, name='modulo_requerimientos'),
    path('requerimientos/crear/', crear_requerimiento_vista, name='crear_requerimiento_vista'),
    path('requerimientos/gestionar/', gestionar_requerimientos_vista, name='gestionar_requerimientos_vista'),
    path('requerimientos/<int:requerimiento_id>/', detalle_requerimiento_vista, name='detalle_requerimiento_vista'),
    
    # APIs de requerimientos
    path('api/requerimientos/crear/', crear_requerimiento, name='api_crear_requerimiento'),
    path('api/requerimientos/listar/', listar_requerimientos, name='api_listar_requerimientos'),
    path('api/requerimientos/<int:requerimiento_id>/', detalle_requerimiento, name='api_detalle_requerimiento'),
    path('api/requerimientos/<int:requerimiento_id>/editar/', editar_requerimiento, name='api_editar_requerimiento'),
    path('api/requerimientos/<int:requerimiento_id>/actualizar-estado/', actualizar_estado_requerimiento, name='api_actualizar_estado_requerimiento'),
    path('api/requerimientos/<int:requerimiento_id>/decidir/', decidir_requerimiento, name='api_decidir_requerimiento'),
    path('api/requerimientos/<int:requerimiento_id>/enviar-proveedor/', enviar_a_proveedor, name='api_enviar_a_proveedor'),
    path('api/requerimientos/<int:requerimiento_id>/respuesta-proveedor/', registrar_respuesta_proveedor, name='api_registrar_respuesta_proveedor'),
    path('api/requerimientos/<int:requerimiento_id>/completar/', completar_requerimiento, name='api_completar_requerimiento'),
    
    # APIs de utilidades
    path('api/requerimientos/<int:requerimiento_id>/formato-pdf/', descargar_formato_requerimiento, name='api_formato_pdf_requerimiento'),
    path('api/requerimientos/sugerir-proveedor/', sugerir_proveedor_por_sku, name='api_sugerir_proveedor_requerimiento'),
    path('api/requerimientos/buscar-compras/', buscar_compras_producto, name='api_buscar_compras_requerimiento'),
    path('api/requerimientos/buscar-factura/', buscar_dte_compra_por_numero, name='api_buscar_factura_requerimiento'),
    path('api/requerimientos/buscar-producto/', buscar_producto_sku, name='api_buscar_producto_requerimiento'),
    path('api/requerimientos/buscar-ticket/', buscar_ticket_por_folio, name='api_buscar_ticket_requerimiento'),
    path('api/requerimientos/buscar-cliente/', buscar_cliente_por_rut, name='api_buscar_cliente_requerimiento'),
    path('api/requerimientos/validar-rut/', validar_rut_chileno, name='api_validar_rut_requerimiento'),
    path('api/requerimientos/crear-cliente/', crear_cliente_rapido, name='api_crear_cliente_requerimiento'),
    path('api/requerimientos/estadisticas/', obtener_estadisticas_requerimientos, name='api_estadisticas_requerimientos'),
    path('api/requerimientos/exportar/', exportar_requerimientos, name='api_exportar_requerimientos'),
    path('api/requerimientos/tipos-foto/', obtener_tipos_foto, name='api_tipos_foto_requerimiento'),

    # ========== MÓDULO GIFT CARDS ==========
    # Vistas HTML
    path('giftcards/', modulo_giftcards, name='modulo_giftcards'),
    path('giftcards/emitir/', emitir_giftcard_vista, name='emitir_giftcard_vista'),
    path('giftcards/trazabilidad/', trazabilidad_giftcards_vista, name='trazabilidad_giftcards'),
    path('giftcards/<int:giftcard_id>/', detalle_giftcard_vista, name='detalle_giftcard_vista'),
    # APIs
    path('api/giftcards/emitir/', api_emitir_giftcard, name='api_emitir_giftcard'),
    path('api/giftcards/listar/', api_listar_giftcards, name='api_listar_giftcards'),
    path('api/giftcards/consultar/', api_consultar_saldo_giftcard, name='api_consultar_saldo_giftcard'),
    path('api/giftcards/validar/', api_validar_giftcard, name='api_validar_giftcard'),
    path('api/giftcards/recargar/', api_recargar_giftcard, name='api_recargar_giftcard'),
    path('api/giftcards/anular/', api_anular_giftcard, name='api_anular_giftcard'),
    path('api/giftcards/bloquear/', api_bloquear_giftcard, name='api_bloquear_giftcard'),
    path('api/giftcards/desbloquear/', api_desbloquear_giftcard, name='api_desbloquear_giftcard'),
    path('api/giftcards/editar/', api_editar_giftcard, name='api_editar_giftcard'),
    path('api/giftcards/ambito/', api_cambiar_ambito_giftcard, name='api_cambiar_ambito_giftcard'),
    path('api/giftcards/enviar-correo/', api_enviar_correo_giftcard, name='api_enviar_correo_giftcard'),
    path('api/giftcards/confirmar-entrega/', api_confirmar_entrega_giftcard, name='api_confirmar_entrega_giftcard'),
    # Público: lo llama el proveedor de correo. Autenticado por firma HMAC.
    path('api/giftcards/webhook-correo/', webhook_correo_giftcard, name='webhook_correo_giftcard'),

    # ===== SEGUIMIENTO DE CORREO (público a propósito) =====
    # El píxel lo pide el cliente de correo del destinatario y el webhook lo
    # llama el proveedor de envío: ninguno de los dos puede autenticarse con
    # sesión. El píxel solo escribe si el token existe; el webhook exige firma
    # HMAC. Rutas cortas porque viajan dentro del cuerpo del correo.
    path('c/a/<str:token>.png', pixel_apertura, name='pixel_apertura_correo'),
    path('api/correo/webhook/', webhook_correo, name='webhook_correo'),
    path('api/giftcards/reporte/', api_reporte_giftcards, name='api_reporte_giftcards'),
    path('api/giftcards/trazabilidad/', api_trazabilidad_giftcards, name='api_trazabilidad_giftcards'),
    path('api/giftcards/trazabilidad/exportar/', api_exportar_trazabilidad, name='api_exportar_trazabilidad'),
    path('api/giftcards/exportar/', api_exportar_giftcards, name='api_exportar_giftcards'),
    path('api/giftcards/<int:giftcard_id>/', api_detalle_giftcard, name='api_detalle_giftcard'),

    # ========== MÓDULO DEVOLUCIÓN DE DINERO POR GARANTÍA ==========
    # Vistas HTML
    path('devolucion-garantia/', modulo_devolucion_garantia, name='modulo_devolucion_garantia'),
    path('devolucion-garantia/<int:devolucion_id>/', detalle_devolucion_garantia, name='detalle_devolucion_garantia'),
    # APIs
    path('api/devolucion-garantia/buscar-dte/', api_buscar_dte_devolucion_garantia, name='api_buscar_dte_devolucion_garantia'),
    path('api/devolucion-garantia/generar/', api_generar_devolucion_garantia, name='api_generar_devolucion_garantia'),
    path('api/devolucion-garantia/listar/', api_listar_devoluciones_garantia, name='api_listar_devoluciones_garantia'),
    path('api/devolucion-garantia/<int:devolucion_id>/detalle/', api_detalle_solicitud_devolucion_garantia, name='api_detalle_solicitud_devolucion_garantia'),
    path('api/devolucion-garantia/<int:devolucion_id>/ticket/', api_ticket_devolucion_garantia, name='api_ticket_devolucion_garantia'),
    path('api/devolucion-garantia/<int:devolucion_id>/impacto-caja/', api_impacto_caja_devolucion_garantia, name='api_impacto_caja_devolucion_garantia'),
    path('api/devolucion-garantia/<int:devolucion_id>/aprobar/', api_aprobar_devolucion_garantia, name='api_aprobar_devolucion_garantia'),
    path('api/devolucion-garantia/<int:devolucion_id>/rechazar/', api_rechazar_devolucion_garantia, name='api_rechazar_devolucion_garantia'),
    path('api/devolucion-garantia/<int:devolucion_id>/anular/', api_anular_solicitud_devolucion_garantia, name='api_anular_solicitud_devolucion_garantia'),

    # ========== MÓDULO FIDELIZACIÓN (PUNTOS) ==========
    # Landing PÚBLICA (QR impreso en tickets del POS) — sin login
    path('puntos/app/', descargar_app_puntos, name='descargar_app_puntos'),
    # Vistas HTML
    path('fidelizacion/', modulo_fidelizacion, name='modulo_fidelizacion'),
    path('fidelizacion/configuracion/', configurar_programa_vista, name='configurar_programa_vista'),
    path('fidelizacion/reporte/', reporte_fidelizacion_vista, name='reporte_fidelizacion_vista'),
    path('fidelizacion/registrar-cliente/', registrar_cliente_vista, name='registrar_cliente_vista'),
    path('fidelizacion/cliente/<int:cliente_id>/', ficha_cliente_puntos_vista, name='ficha_cliente_puntos_vista'),
    # APIs
    path('api/fidelizacion/cuentas/', api_listar_cuentas, name='api_listar_cuentas'),
    path('api/fidelizacion/cuenta/<int:cliente_id>/', api_detalle_cuenta, name='api_detalle_cuenta'),
    path('api/fidelizacion/saldo/', api_consultar_saldo_puntos, name='api_consultar_saldo_puntos'),
    path('api/fidelizacion/programa/guardar/', api_guardar_programa, name='api_guardar_programa'),
    path('api/fidelizacion/ajuste/', api_ajuste_manual_puntos, name='api_ajuste_manual_puntos'),
    path('api/fidelizacion/registrar-cliente/', api_registrar_cliente, name='api_registrar_cliente'),
    path('api/fidelizacion/reporte/', api_reporte_fidelizacion, name='api_reporte_fidelizacion'),
    path('api/fidelizacion/bono-cumpleanos/', api_bono_cumpleanos, name='api_bono_cumpleanos'),
    # Vales de canje EN CAJA (sesión web). El único emisor era la app móvil del
    # cliente, que tiene 0 instalaciones, y el validador que llamaba el POS
    # exigía header X-Device-ID: por eso el programa lleva 787.000 puntos
    # repartidos y CERO canjes. Estas dos rutas cierran ambos cortes.
    path('api/fidelizacion/vale/generar/', api_generar_vale_canje, name='api_generar_vale_canje'),
    path('api/fidelizacion/vale/<str:codigo>/', api_validar_vale_canje, name='api_validar_vale_canje'),

    # ========== CUPONES DE DESCUENTO NOMINATIVOS ==========
    path('fidelizacion/cupones/', modulo_cupones, name='modulo_cupones'),
    path('api/cupones/campanas/', api_listar_campanas, name='api_listar_campanas'),
    path('api/cupones/campana/guardar/', api_guardar_campana, name='api_guardar_campana'),
    path('api/cupones/campana/<int:campana_id>/toggle/', api_toggle_campana, name='api_toggle_campana'),
    path('api/cupones/clientes/', api_buscar_cliente_cupon, name='api_buscar_cliente_cupon'),
    path('api/cupones/emitir/', api_emitir_cupon, name='api_emitir_cupon'),
    path('api/cupones/emitir-lote/', api_emitir_lote_cupones, name='api_emitir_lote_cupones'),
    path('api/cupones/', api_listar_cupones, name='api_listar_cupones'),
    path('api/cupones/<int:cupon_id>/anular/', api_anular_cupon, name='api_anular_cupon'),
    # Validación desde la caja (permiso de 'ticket_venta', no de administración).
    # Va al final para que 'validar' no se coma las rutas de arriba.
    path('api/cupones/validar/<str:codigo>/', api_validar_cupon_caja, name='api_validar_cupon_caja'),

    # ========== MÓDULO DE GESTIÓN DE PERMISOS ==========
    # Vista principal de gestión de permisos
    path('permisos/gestion/', gestion_permisos, name='gestion_permisos'),
    
    # APIs de permisos por rol
    path('permisos/obtener-permisos-rol/', obtener_permisos_rol, name='obtener_permisos_rol'),
    path('permisos/guardar-permiso/', guardar_permiso, name='guardar_permiso'),
    path('permisos/guardar-permisos-masivos/', guardar_permisos_masivos, name='guardar_permisos_masivos'),
    path('permisos/copiar-permisos-rol/', copiar_permisos_rol, name='copiar_permisos_rol'),
    
    # APIs de permisos por sucursal
    path('permisos/sucursales/', obtener_sucursales_permisos, name='obtener_sucursales_permisos'),
    path('permisos/obtener-permisos-sucursal/', obtener_permisos_sucursal, name='obtener_permisos_sucursal'),
    path('permisos/guardar-permisos-sucursal/', guardar_permisos_sucursal, name='guardar_permisos_sucursal'),
    path('permisos/copiar-permisos-sucursal/', copiar_permisos_sucursal, name='copiar_permisos_sucursal'),
    path('permisos/aplicar-plantilla-sucursal/', aplicar_plantilla_tipo_sucursal, name='aplicar_plantilla_tipo_sucursal'),
    path('permisos/restablecer-permisos-sucursal/', restablecer_permisos_sucursal, name='restablecer_permisos_sucursal'),
    
    # Gestión de módulos y opciones
    path('permisos/modulos-opciones/', gestionar_modulos_opciones, name='gestionar_modulos_opciones'),
    
    # Estadísticas
    path('permisos/estadisticas/', estadisticas_permisos, name='estadisticas_permisos'),
    
    # Exportar/Importar permisos por rol
    path('permisos/exportar-rol/', exportar_permisos_rol, name='exportar_permisos_rol'),
    path('permisos/exportar-todos/', exportar_todos_permisos, name='exportar_todos_permisos'),
    path('permisos/importar/', importar_permisos, name='importar_permisos'),
    
    # Exportar/Importar permisos por sucursal
    path('permisos/exportar-sucursal/', exportar_permisos_sucursal, name='exportar_permisos_sucursal'),
    path('permisos/importar-sucursal/', importar_permisos_sucursal, name='importar_permisos_sucursal'),

    # Gestión de permisos por usuario
    path('permisos/usuarios/', obtener_usuarios_permisos, name='obtener_usuarios_permisos'),
    path('permisos/obtener-permisos-usuario/', obtener_permisos_usuario, name='obtener_permisos_usuario'),
    path('permisos/guardar-permisos-usuario/', guardar_permisos_usuario, name='guardar_permisos_usuario'),
    path('permisos/eliminar-permisos-usuario/', eliminar_permisos_usuario, name='eliminar_permisos_usuario'),
    path('permisos/copiar-permisos-usuario/', copiar_permisos_usuario, name='copiar_permisos_usuario'),


    # ========== FUSIÓN Y RE-ETIQUETADO DE DUPLICADOS (EXISTENCIAS) ==========
    path('existencias/fusion-duplicados/', views_fusion_duplicados.ver_fusion_duplicados, name='ver_fusion_duplicados'),
    path('api/fusion-duplicados/buscar/', views_fusion_duplicados.api_buscar_producto_fusion, name='api_buscar_producto_fusion'),
    path('api/fusion-duplicados/ejecutar/', views_fusion_duplicados.api_ejecutar_fusion, name='api_ejecutar_fusion'),

    # ========== MÓDULO DE GESTIÓN DE INVENTARIOS (TOMA FÍSICA) ==========
    # Vistas principales
    path('gestion-inventarios/', views_gestion_inventarios.gestion_inventarios, name='gestion_inventarios'),
    path('gestion-inventarios/detalle/<int:inventario_id>/', views_gestion_inventarios.detalle_inventario, name='detalle_inventario'),
    
    # APIs de listado y filtros
    path('gestion-inventarios/api/inventarios/', views_gestion_inventarios.obtener_inventarios, name='api_obtener_inventarios'),
    path('gestion-inventarios/api/filtros/', views_gestion_inventarios.obtener_filtros_disponibles, name='api_filtros_inventarios'),
    
    # APIs de creación (requiere puede_crear)
    path('gestion-inventarios/api/crear/', _permiso_inventarios('puede_crear', views_gestion_inventarios.crear_inventario), name='api_crear_inventario'),

    # APIs de conteo (escriben el conteo físico: requieren puede_editar)
    path('gestion-inventarios/api/productos-conteo/<int:inventario_id>/', views_gestion_inventarios.obtener_productos_conteo, name='api_productos_conteo'),
    path('gestion-inventarios/api/registrar-conteo/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.registrar_conteo), name='api_registrar_conteo'),
    path('gestion-inventarios/api/registrar-reconteo/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.registrar_reconteo), name='api_registrar_reconteo'),
    path('gestion-inventarios/api/importar-conteo/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.importar_conteo_pistola), name='api_importar_conteo_pistola'),
    path('gestion-inventarios/api/importar-conteo/preview/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.preview_conteo_pistola), name='api_preview_conteo_pistola'),
    path('gestion-inventarios/api/excluir-detalle/<int:inventario_id>/<int:detalle_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.actualizar_exclusion_detalle), name='api_excluir_detalle_inventario'),
    
    # APIs de análisis
    path('gestion-inventarios/api/analisis/<int:inventario_id>/', views_gestion_inventarios.obtener_analisis_inventario, name='api_analisis_inventario'),
    path('gestion-inventarios/api/exportar/<int:inventario_id>/', views_gestion_inventarios.exportar_inventario, name='api_exportar_inventario'),
    path('gestion-inventarios/api/exportar-diferencias/<int:inventario_id>/', views_gestion_inventarios.exportar_diferencias_inventario, name='api_exportar_diferencias_inventario'),
    path('gestion-inventarios/api/historial/<int:inventario_id>/', views_gestion_inventarios.obtener_historial_inventario, name='api_historial_inventario'),
    
    # APIs de flujo de aprobación
    # finalizar/enviar-aprobación cierran el conteo -> puede_editar.
    #
    # aprobar/rechazar son la firma que habilita el ajuste. Se exige
    # 'puede_editar' y NO 'puede_aprobar': en producción no hay una sola fila
    # de PermisoSucursal con puede_aprobar=True (0 de 49, todas de NICK1), así
    # que 'puede_aprobar' dejaba en 403 a los 8 administradores de esa sucursal
    # y mataba el módulo entero ahí — sin poder aprobar, el inventario nunca
    # llega a APROBADO y los ajustes tampoco se pueden aplicar. Ver el
    # docstring de _permiso_inventarios (capa 3: sucursal).
    path('gestion-inventarios/api/finalizar-conteo/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.finalizar_conteo), name='api_finalizar_conteo'),
    path('gestion-inventarios/api/enviar-aprobacion/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.enviar_aprobacion), name='api_enviar_aprobacion'),
    path('gestion-inventarios/api/aprobar/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.aprobar_inventario), name='api_aprobar_inventario'),
    path('gestion-inventarios/api/rechazar/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.rechazar_inventario), name='api_rechazar_inventario'),

    # APIs de aplicación de ajustes: esta es la que MUEVE STOCK y escribe
    # kardex. Sigue exigiendo el mismo permiso que la aprobación, y el control
    # fuerte NO se apoya solo en el permiso sino en la máquina de estados de la
    # vista: aplicar_ajustes_inventario solo corre si el inventario está en
    # APROBADO/APLICANDO, y aprobar_inventario solo pasa desde
    # PENDIENTE_APROBACION con 0 líneas sin contar y 0 reconteos pendientes,
    # dejando traza en aprobado_por + LogInventario. Con eso el módulo no queda
    # abierto: quien solo tiene ver/exportar en su sucursal no puede aplicar.
    path('gestion-inventarios/api/aplicar-ajustes/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.aplicar_ajustes_inventario), name='api_aplicar_ajustes'),
    path('gestion-inventarios/api/estado-ajustes/<int:inventario_id>/', views_gestion_inventarios.estado_tarea_ajustes, name='api_estado_tarea_ajustes'),

    # APIs de cancelación: quien puede crear/editar la toma puede abandonarla
    # (no borra datos, solo deja el inventario en CANCELADO).
    path('gestion-inventarios/api/cancelar/<int:inventario_id>/', _permiso_inventarios('puede_editar', views_gestion_inventarios.cancelar_inventario), name='api_cancelar_inventario'),

    # ========== MÓDULO DE IMPRESIÓN DE ETIQUETAS ZEBRA ==========
    # Vista principal
    path('etiquetas/', views_etiquetas_zebra.gestion_etiquetas_zebra, name='gestion_etiquetas_zebra'),
    
    # APIs de documentos
    path('etiquetas/obtener-documentos/', views_etiquetas_zebra.obtener_documentos_etiquetas, name='obtener_documentos_etiquetas'),
    path('etiquetas/productos-documento/<str:tipo_documento>/<int:documento_id>/', views_etiquetas_zebra.obtener_productos_documento, name='obtener_productos_documento'),
    
    # APIs de generación
    path('etiquetas/generar-datos/', views_etiquetas_zebra.generar_datos_etiquetas, name='generar_datos_etiquetas'),
    
    # APIs auxiliares
    path('etiquetas/sucursales/', views_etiquetas_zebra.obtener_sucursales_usuario, name='obtener_sucursales_etiquetas'),
    path('etiquetas/buscar-producto/', views_etiquetas_zebra.buscar_producto_etiqueta, name='buscar_producto_etiqueta'),
    path('etiquetas/skus-articulo/', views_etiquetas_zebra.obtener_skus_articulo, name='obtener_skus_articulo'),

    # ========== DASHBOARDS KPI (nuevos) ==========
    path('dashboard-documentos/', dashboard_documentos, name='dashboard_documentos'),
    path('api/dashboard-documentos/datos/', api_dashboard_documentos, name='api_dashboard_documentos'),


    path('dashboard-despachos/', dashboard_despachos, name='dashboard_despachos'),
    path('api/dashboard-despachos/datos/', api_dashboard_despachos, name='api_dashboard_despachos'),
    path('api/dashboard-despachos/flujo/', api_despachos_flujo, name='api_despachos_flujo'),

    path('dashboard-requerimientos/', dashboard_requerimientos, name='dashboard_requerimientos'),
    path('api/dashboard-requerimientos/datos/', api_dashboard_requerimientos, name='api_dashboard_requerimientos'),


    # ========== MÓDULO DE PREDICCIÓN DE COMPRAS ==========
    path('prediccion/', dashboard_prediccion, name='dashboard_prediccion'),
    path('api/prediccion/resumen/', api_prediccion_resumen, name='api_prediccion_resumen'),
    path('api/prediccion/clasificacion/', api_prediccion_clasificacion, name='api_prediccion_clasificacion'),
    path('api/prediccion/sugerencias/', api_prediccion_sugerencias, name='api_prediccion_sugerencias'),
    path('api/prediccion/alertas-velocidad/', api_prediccion_alertas_velocidad, name='api_prediccion_alertas_velocidad'),
    path('api/prediccion/alertas-quiebre/', api_prediccion_alertas_quiebre, name='api_prediccion_alertas_quiebre'),
    path('api/prediccion/producto/<int:producto_id>/', api_prediccion_producto_detalle, name='api_prediccion_producto_detalle'),
    path('api/prediccion/aprobar-sugerencia/', api_prediccion_aprobar_sugerencia, name='api_prediccion_aprobar_sugerencia'),
    path('api/prediccion/recalcular/', api_prediccion_recalcular, name='api_prediccion_recalcular'),
    path('api/prediccion/configuracion/', api_prediccion_configuracion, name='api_prediccion_configuracion'),
    path('api/prediccion/categorias-disponibles/', api_prediccion_categorias_disponibles, name='api_prediccion_categorias_disponibles'),
    path('api/prediccion/analisis-categoria/', api_prediccion_analisis_categoria, name='api_prediccion_analisis_categoria'),
    path('api/prediccion/analisis-marca/', api_prediccion_analisis_marca, name='api_prediccion_analisis_marca'),
    path('api/prediccion/analisis-proveedor/', api_prediccion_analisis_proveedor, name='api_prediccion_analisis_proveedor'),
    path('api/prediccion/marca-articulos/', api_prediccion_marca_articulos, name='api_prediccion_marca_articulos'),
    path('api/prediccion/graficos/', api_prediccion_graficos, name='api_prediccion_graficos'),

    # =====================================================
    # MÓDULO ECOMMERCE — Pedidos online externos
    # =====================================================
    path('api/ecommerce/pedidos/', views_ecommerce.api_recibir_pedido_ecommerce, name='api_ecommerce_recibir_pedido'),
    path('api/ecommerce/pedidos/pagado/', views_ecommerce.api_pedido_pagado, name='api_ecommerce_pedido_pagado'),
    path('api/cron/tareas/', views_cron.ejecutar_tareas_periodicas, name='api_cron_tareas'),
    path('api/cron/verificar-drift/', views_cron.verificar_drift_http, name='api_cron_verificar_drift'),
    path('api/ecommerce/pedidos/consultar/', views_ecommerce.api_asignar_ticket_rm, name='api_ecommerce_consultar_pedido'),
    path('api/ecommerce/pedidos/estado-batch/', views_ecommerce.api_estado_pedidos_batch, name='api_ecommerce_estado_batch'),
    path('api/ecommerce/pedidos/cancelar/', views_ecommerce.api_cancelar_pedido_ecommerce, name='api_ecommerce_cancelar_pedido'),
    path('api/ecommerce/pedidos/cambio-producto/', views_ecommerce.api_cambio_producto_pedido, name='api_ecommerce_cambio_producto'),
    path('api/ecommerce/facturar-masivo/', views_ecommerce.facturar_ecommerce_masivo, name='facturar_ecommerce_masivo'),
    path('ecommerce/pedidos/', views_ecommerce.PedidosEcommerceListView.as_view(), name='pedidos_ecommerce_list'),
    path('ecommerce/pedidos/traer/', views_ecommerce.traer_pedidos_allconnected, name='traer_pedidos_allconnected'),
    path('ecommerce/pedidos/<int:pedido_id>/', views_ecommerce.pedido_ecommerce_detalle, name='pedido_ecommerce_detalle'),
    path('ecommerce/pedidos/<int:pedido_id>/buscar-producto/', views_ecommerce.api_buscar_producto_match, name='api_buscar_producto_match'),
    path('ecommerce/pedidos/<int:pedido_id>/guardar-match/', views_ecommerce.api_guardar_match_sku, name='api_guardar_match_sku'),
    path('ecommerce/pedidos/<int:pedido_id>/facturar/', views_ecommerce.api_facturar_pedido_individual, name='api_facturar_pedido_individual'),
    path('ecommerce/pedidos/<int:pedido_id>/sub-estado/', views_ecommerce.api_cambiar_sub_estado, name='api_cambiar_sub_estado_pedido'),
    path('ecommerce/pedidos/<int:pedido_id>/imprimir-guia/', views_ecommerce.api_imprimir_guia_preparacion, name='api_imprimir_guia_preparacion'),
    path('ecommerce/pedidos/imprimir-guias-sucursal/', views_ecommerce.api_imprimir_guias_sucursal, name='api_imprimir_guias_sucursal'),
    path('ecommerce/pedidos/<int:pedido_id>/guia-pdf/', views_ecommerce.api_guia_preparacion_pdf, name='api_guia_preparacion_pdf'),
    path('ecommerce/pedidos/guias-pdf-sucursal/', views_ecommerce.api_guias_pdf_sucursal, name='api_guias_pdf_sucursal'),
    path('ecommerce/pedidos/<int:pedido_id>/sin-stock/', views_ecommerce.api_marcar_sin_stock, name='api_marcar_sin_stock'),
    path('ecommerce/pedidos/<int:pedido_id>/reactivar/', views_ecommerce.api_reactivar_sin_stock, name='api_reactivar_sin_stock'),
    path('ecommerce/pedidos/<int:pedido_id>/reasignar/', views_ecommerce.api_reasignar_pedido, name='api_reasignar_pedido'),
    path('ecommerce/pedidos/<int:pedido_id>/sugerir-sucursal/', views_ecommerce.api_sugerir_sucursal, name='api_sugerir_sucursal'),
    path('ecommerce/pedidos/<int:pedido_id>/historial/', views_ecommerce.api_historial_pedido, name='api_historial_pedido'),
    path('ecommerce/pedidos/distribuir/', views_ecommerce.api_distribuir_pedidos, name='api_distribuir_pedidos'),
    path('ecommerce/pedidos/exportar-csv/', views_ecommerce.exportar_pedidos_csv, name='exportar_pedidos_csv'),
    path('ecommerce/dashboard-asignacion/', views_ecommerce.ecommerce_dashboard_asignacion, name='ecommerce_dashboard_asignacion'),
    path('ecommerce/dte/<int:dte_id>/txt/', views_ecommerce.descargar_txt_dte_ecommerce, name='descargar_txt_dte_ecommerce'),
    path('ecommerce/dte/txts-zip/', views_ecommerce.descargar_txts_zip_ecommerce, name='descargar_txts_zip_ecommerce'),

    # Retiro de pedido en tienda (meson PAO1): pantalla interna con login del
    # ERP que valida el codigo de retiro contra AllConnected e imprime el
    # comprobante. El codigo NUNCA vive en este ERP.
    path('ecommerce/retiro-local/', views_ecommerce.retiro_pedido_local, name='retiro_pedido_local'),
    path('ecommerce/retiro-local/validar/', views_ecommerce.api_retiro_validar, name='api_retiro_validar'),
    path('ecommerce/retiro-local/confirmar/', views_ecommerce.api_retiro_confirmar, name='api_retiro_confirmar'),
    path('ecommerce/retiro-local/comprobante-pdf/', views_ecommerce.api_retiro_comprobante_pdf, name='api_retiro_comprobante_pdf'),

]
