"""
Middleware para verificar permisos de acceso basados en la configuración de menú.
Intercepta las peticiones y verifica si el usuario tiene permiso para acceder a la URL.
"""
from django.shortcuts import redirect
from django.http import JsonResponse
from django.contrib import messages
from django.urls import resolve
from .models import PermisoRol, OpcionMenu, Sucursal


# Mapeo de URLs a códigos de opción del menú
# Clave: parte de la URL (se busca si la URL contiene esta cadena)
# Valor: código de la opción en el sistema de permisos
URL_PERMISO_MAP = {
    # Dashboard
    # ⚠️ Las PÁGINAS de dashboard estaban mapeadas, pero sus endpoints AJAX y de
    # exportación NO: el match es por substring y el segmento '/api/' (o el
    # prefijo 'obtener_datos_'/'exportar_') rompe la coincidencia, así que
    # obtener_codigo_opcion devolvía None y el middleware dejaba pasar. Cualquier
    # autenticado podía leer por URL directa el JSON completo (montos, deuda,
    # costos, márgenes, ranking de proveedores) de un dashboard que su rol tiene
    # vedado. Se mapea cada endpoint al MISMO código que su página.
    # Las claves sin barra final cubren la página y su variante '_mejorado'.
    '/app/dashboard_productos': 'dashboard_productos',
    '/app/exportar_dashboard_productos/': 'dashboard_productos',
    '/app/exportar_productos_filtrado/': 'dashboard_productos',
    '/app/dashboard_fifo/': 'dashboard_fifo',
    '/app/obtener_datos_dashboard_fifo/': 'dashboard_fifo',
    '/app/exportar_dashboard_fifo/': 'dashboard_fifo',
    '/app/verDashboardCompras': 'dashboard_compras_estrategico',
    '/app/dashboard_compras_mejorado_api/': 'dashboard_compras_estrategico',
    '/app/exportar_dashboard_compras/': 'dashboard_compras_estrategico',
    '/app/ventas/dashboard': 'dashboard_ventas',
    '/app/dashboard-documentos/': 'dashboard_documentos',
    '/app/api/dashboard-documentos/': 'dashboard_documentos',
    '/app/dashboard-despachos/': 'dashboard_despachos',
    '/app/api/dashboard-despachos/': 'dashboard_despachos',
    '/app/dashboard-requerimientos/': 'dashboard_requerimientos',
    '/app/api/dashboard-requerimientos/': 'dashboard_requerimientos',
    '/app/prediccion/': 'prediccion_compras',

    # APIs del Dashboard de Ventas Mejorado. Se listan una a una a propósito:
    # NO mapear el prefijo '/app/api/ventas/', porque bajo él viven también
    # 'editar-boleta-papel' y 'eliminar-documento', que llama cuadraturaCaja.html
    # (rol cajero, sin permiso de dashboard) — gatear el prefijo los dejaría en 403.
    '/app/api/ventas/indicadores-globales/': 'dashboard_ventas',
    '/app/api/ventas/indicadores-avanzados/': 'dashboard_ventas',
    '/app/api/ventas/por-vendedor/': 'dashboard_ventas',
    '/app/api/ventas/por-sucursal/': 'dashboard_ventas',
    '/app/api/ventas/sucursales-dashboard/': 'dashboard_ventas',
    '/app/api/ventas/por-metodo-pago/': 'dashboard_ventas',
    '/app/api/ventas/analisis-cambios/': 'dashboard_ventas',
    '/app/api/ventas/estado-cuadraturas/': 'dashboard_ventas',
    '/app/api/ventas/estado-operacional/': 'dashboard_ventas',
    '/app/api/ventas/productos-mas-vendidos/': 'dashboard_ventas',
    '/app/api/ventas/por-categoria/': 'dashboard_ventas',
    '/app/api/ventas/por-especialidad/': 'dashboard_ventas',
    '/app/api/ventas/indicador-compra/': 'dashboard_ventas',
    '/app/api/ventas/mix-por-sucursal/': 'dashboard_ventas',
    '/app/api/ventas/tendencias/': 'dashboard_ventas',
    '/app/api/ventas/exportar-dashboard/': 'dashboard_ventas',
    
    # Ventas
    '/app/ticket-venta/': 'ticket_venta',
    '/app/cambios-devoluciones/': 'cambios_devoluciones',
    '/app/devolucion-garantia/': 'devolucion_garantia',
    '/app/pos-dashboard/': 'pos_dashboard',
    '/app/gestion-ventas-documentos/': 'gestion_documentos_ventas',
    '/app/ventas/cuadratura-caja/': 'cuadratura_caja',
    # POS Transbank: la clave anterior era '/app/transbank/', que NO corresponde
    # a ninguna ruta real (urls.py monta todo bajo '/app/pos/...'), así que el
    # módulo entero quedaba sin control de permisos.
    # ⚠️ NO se mapea '/app/pos/transbank/' (pantalla del SDK oficial): el match
    # es por substring, y esa clave taparía también '/app/pos/transbank/venta/'
    # y '/app/pos/transbank/autoconectar/', que los llama el POS de venta
    # (generacionVentas.html y static/js/transbank-helpers.js). El rol
    # 'vendedor' tiene pos_transbank.puede_ver=False, así que gatearlas dejaría
    # a los 6 vendedores activos con 403 al cobrar con tarjeta.
    '/app/pos/transbank-websocket/': 'pos_transbank',
    '/app/pos/transbank-manual/': 'pos_transbank',
    '/app/pos/detectar-terminales/': 'pos_transbank',

    # Fidelización (GiftCards + Puntos)
    # El match es por substring y gana la clave más larga (ver
    # obtener_codigo_opcion), así que /app/giftcards/emitir/ prevalece sobre
    # /app/giftcards/ sin depender del orden del diccionario.
    '/app/giftcards/emitir/': 'giftcards_emitir',
    '/app/giftcards/': 'giftcards_listado',
    '/app/fidelizacion/configuracion/': 'fidelizacion_programa',
    '/app/fidelizacion/reporte/': 'fidelizacion_reporte',
    '/app/fidelizacion/registrar-cliente/': 'fidelizacion_cuentas',
    # Va ANTES de '/app/fidelizacion/': el match es por orden del diccionario y
    # la clave genérica se comería esta URL, dándole a los cupones el permiso
    # equivocado (cualquiera con acceso a Clientes y Puntos podría emitirlos).
    '/app/fidelizacion/cupones/': 'fidelizacion_cupones',
    '/app/fidelizacion/': 'fidelizacion_cuentas',
    
    # Documentos
    '/app/emisionDTE/': 'emision_dte',
    # El endpoint que MUEVE stock y quema folio del SII solo tenía
    # @login_required: cualquier usuario autenticado podía emitir aunque la
    # página estuviera vedada para su rol.
    '/app/emitir_dte/': 'emision_dte',
    '/app/emisionDTEConcepto/': 'emision_dte',
    '/app/emitir_dte_concepto/': 'emision_dte',
    '/app/documentos/gestion-dte/': 'gestion_dte',
    '/app/recepcion-dte/': 'recepcion_dte',
    '/app/regularizar-recepciones/': 'recepcion_dte',
    '/app/cotizaciones/': 'gestion_cotizaciones',
    '/app/documentos/gestion-correlativos/': 'gestion_correlativos',
    '/app/documentos/gestion-creditos/': 'gestion_creditos',

    # --- Créditos de trabajador (pantalla + endpoints de escritura) ---
    # Solo estaba mapeada la pantalla de Documentos. La pantalla propia
    # ('/app/creditos/') y sus endpoints (crear, aprobar, rechazar, activar,
    # ajustar monto, registrar pago) colgaban de @login_required, así que
    # cualquier usuario autenticado podía otorgarse o aprobarse un crédito por
    # POST directo. Único consumidor de todos ellos: gestion_creditos.html,
    # que ya exige el mismo permiso.
    # ⚠️ NO se usa la clave genérica '/app/api/creditos/': taparía por substring
    # los dos endpoints que consume el POS de venta (generacionVentas.html),
    # '/app/api/creditos/validar-codigo/' y '/app/api/creditos/usar-en-venta/'.
    # Cajero, vendedor y jefe_local tienen gestion_creditos.puede_ver=False, así
    # que gatearlos rompería el cobro con crédito de trabajador en caja.
    # Tampoco se mapea '/app/api/creditos/<id>/' (detalle) por la misma razón:
    # su prefijo es el genérico.
    '/app/creditos/': 'gestion_creditos',
    '/app/api/creditos/crear/': 'gestion_creditos',
    '/app/api/creditos/cargar/': 'gestion_creditos',
    '/app/api/creditos/aprobar/': 'gestion_creditos',
    '/app/api/creditos/rechazar/': 'gestion_creditos',
    '/app/api/creditos/activar/': 'gestion_creditos',
    '/app/api/creditos/ajustar-monto/': 'gestion_creditos',
    '/app/api/creditos/pago/': 'gestion_creditos',
    '/app/api/creditos/firma/': 'gestion_creditos',
    '/app/api/creditos/reporte/': 'gestion_creditos',
    '/app/api/creditos/sucursales/': 'gestion_creditos',
    '/app/api/creditos/imprimir-voucher/': 'gestion_creditos',
    # Cubre por substring trabajadores/, trabajadores/crear/,
    # trabajadores/actualizar/ y trabajadores/validar-codigo/ (todos de la
    # pantalla de administración; el validar-codigo del POS vive en otra ruta).
    '/app/api/creditos/trabajadores/': 'gestion_creditos',


    # Existencias
    '/app/verGestionProducto/': 'gestion_producto',
    '/app/productos/stock-salida/': 'gestion_producto',
    '/app/edicion-rapida-precios/': 'edicion_rapida_precios',
    '/app/gestion-precios/edicion-rapida/': 'edicion_rapida_precios',
    # La pantalla de Alertas de Precios vive en
    # '/app/gestion-precios/revisar-pendientes/' (urls.py:1115). La clave
    # anterior ('/app/revisar-cambios-precios/') no existe como ruta, así que
    # nunca hacía match y la pantalla quedaba abierta a cualquier autenticado.
    # Sus endpoints AJAX (listar-cambios, aprobar-cambio, rechazar-cambio) NO
    # se mapean aquí: '/app/gestion-precios/aprobar-cambio/' lo llama también el
    # widget de dashboard_general.html, que es página siempre accesible.
    '/app/gestion-precios/revisar-pendientes/': 'revisar_cambios_precios',
    '/app/verMovimientosProducto/': 'movimientos_producto',
    '/app/gestion-inventarios/': 'gestion_inventarios',
    # Fusión de duplicados: comparte el permiso fino de Gestión de Inventarios
    # (opera stock/kardex). Cubre la página y sus 2 endpoints AJAX.
    '/app/existencias/fusion-duplicados/': 'gestion_inventarios',
    '/app/api/fusion-duplicados/': 'gestion_inventarios',
    # La ruta real es '/app/etiquetas/' (urls.py:1505), no
    # '/app/etiquetas-zebra/': la clave vieja nunca hacía match, así que la
    # pantalla y sus 6 endpoints quedaban abiertos a cualquier autenticado.
    # El match es por substring (gana la clave más larga), de modo que
    # '/app/etiquetas/' cubre también obtener-documentos, productos-documento,
    # generar-datos, sucursales, buscar-producto y skus-articulo. Único
    # consumidor de todos: gestion_etiquetas_zebra.html.
    '/app/etiquetas/': 'gestion_etiquetas_zebra',
    # Guías de Talla: la pantalla no estaba mapeada en absoluto.
    # ⚠️ Solo se mapea la PÁGINA. '/app/guias_talla/', '/app/crear_guia_talla/',
    # '/app/guia_talla_detalle/', '/app/eliminar_guia_talla/' y
    # '/app/app/guias_talla_por_marca/' los consumen TAMBIÉN
    # verGestionProductos.html y modulo_compras/gestionCompras.html (modal de
    # guía dentro de la creación/recepción de productos): gatearlos con
    # 'ver_guias_talla' rompería esos flujos.
    '/app/ver_guias_talla/': 'ver_guias_talla',
    '/app/buscar-productos-sucursal/': 'buscar_productos_sucursal',
    # Igual que en Reportes: las APIs cuelgan de /app/api/... y no las cubre la
    # clave de la página. Cada una se declara con el permiso de su módulo.
    '/app/tarjeta-movimiento/': 'tarjeta_movimiento_producto',
    '/app/api/tarjeta-movimiento/': 'tarjeta_movimiento_producto',
    '/app/despacho-sucursales/': 'despacho_sucursales',
    '/app/api/despacho/': 'despacho_sucursales',
    '/app/trazabilidad-producto/': 'trazabilidad_producto',
    '/app/api/trazabilidad-producto/': 'trazabilidad_producto',
    '/app/precios-costos/': 'modificacion_precios_costos',
    '/app/api/precios-costos/': 'modificacion_precios_costos',
    
    # Compras
    '/app/verGestionCompras/': 'gestion_compras',
    '/app/eliminar_compra/': 'gestion_compras',
    '/app/verGestionDteCompras/': 'gestion_dte_compras',
    
    # Requerimientos
    '/app/requerimientos/': 'lista_requerimientos',
    
    # Reportes
    # ⚠️ Cada reporte se declara con SU PÁGINA **y** sus endpoints JSON/export.
    # El match es por substring sobre el path completo, y las APIs cuelgan de
    # `/app/api/...`, así que NUNCA quedan cubiertas por la clave de la página
    # (`/app/reportes/x/` no es substring de `/app/api/reportes/x/`). Sin la
    # entrada explícita, un rol sin el permiso puede pedir el JSON directo y
    # ver montos, comisiones y costos. Al agregar un reporte nuevo hay que
    # mapear también sus endpoints.

    # Ventas por sucursal (incluye vendedores, documentos y comparativa mensual
    # del mismo tablero)
    '/app/reportes/ventas-sucursal/': 'reporte_ventas_sucursal',
    '/app/api/reportes/ventas-por-vendedor/': 'reporte_ventas_sucursal',
    '/app/api/reportes/ventas-por-sucursal/': 'reporte_ventas_sucursal',
    '/app/api/reportes/diagnostico-cuadratura/': 'reporte_ventas_sucursal',
    '/app/api/reportes/comparativa-mensual/': 'reporte_ventas_sucursal',
    '/app/api/reportes/documentos-vendedor/': 'reporte_ventas_sucursal',
    '/app/api/reportes/vendedores/': 'reporte_ventas_sucursal',
    # Comisiones: permiso fino propio (la vista ya lo revalida con puede_ver /
    # puede_exportar); cubre también .../comisiones-vendedor/exportar/
    '/app/api/reportes/comisiones-vendedor/': 'reporte_comisiones_vendedor',

    # Comparativo de ventas
    '/app/reportes/ventas-comparativo/': 'reporte_ventas_comparativo',
    '/app/api/reportes/ventas-comparativo/': 'reporte_ventas_comparativo',

    # Productos vendidos (atributo-opciones solo alimenta sus filtros)
    '/app/reportes/productos-vendidos/': 'reporte_productos_vendidos',
    '/app/api/reportes/productos-vendidos/': 'reporte_productos_vendidos',
    '/app/api/reportes/atributo-opciones/': 'reporte_productos_vendidos',

    # Ventas internet (la clave de la página cubre /exportar/)
    '/app/reportes/ventas-internet/': 'reporte_ventas_internet',
    '/app/api/reportes/ventas-internet/': 'reporte_ventas_internet',

    # Documentos emitidos
    '/app/reportes/documentos-emitidos/': 'reporte_documentos_emitidos',
    '/app/api/reportes/documentos-emitidos/': 'reporte_documentos_emitidos',
    '/app/api/reportes/documentos-emitidos-excel/': 'reporte_documentos_emitidos',

    # Existencias (general). Las rutas viejas '/app/reporte-existencias*/' y
    # '/app/resumen-existencias/' ya no existen en urls.py: quedaron bajo
    # '/app/reportes/...', por eso el permiso no se aplicaba.
    '/app/reportes/existencias/': 'reporte_existencias',
    '/app/api/obtener-existencias/': 'reporte_existencias',
    '/app/api/exportar-existencias-excel/': 'reporte_existencias',

    # Existencias por marca
    '/app/reportes/existencias-marca/': 'reporte_existencias_marca',
    '/app/api/reporte-existencias-marca/': 'reporte_existencias_marca',
    '/app/api/exportar-existencias-marca-excel/': 'reporte_existencias_marca',

    # Existencias por sucursal
    '/app/reportes/existencias-sucursal/': 'reporte_existencias_sucursal',
    # Quiebre de talla comparte el permiso de existencias por sucursal: es la
    # misma información de stock, mirada por talla. La vista además lo revalida
    # con @requiere_permiso (el middleware solo comprueba `puede_ver`).
    '/app/reportes/quiebre-talla/': 'reporte_existencias_sucursal',
    '/app/api/reporte-existencias-sucursal/': 'reporte_existencias_sucursal',
    '/app/api/exportar-existencias-sucursal-excel/': 'reporte_existencias_sucursal',
    '/app/api/exportar-existencias-sucursal-pdf/': 'reporte_existencias_sucursal',

    # Resumen de existencias
    '/app/reportes/resumen-existencias/': 'resumen_existencias',
    '/app/api/resumen-existencias/': 'resumen_existencias',
    '/app/api/exportar-resumen-existencias-excel/': 'resumen_existencias',
    '/app/api/exportar-resumen-existencias-pdf/': 'resumen_existencias',
    '/app/api/verificar-disponibilidad-historico/': 'resumen_existencias',
    '/app/api/listar-articulos-para-excluir/': 'resumen_existencias',
    '/app/api/listar-sucursales-resumen/': 'resumen_existencias',
    '/app/api/detalle-stock-sucursal/': 'resumen_existencias',

    # Movimientos por sucursal (Inicial vs Restante) y despachos a tiendas
    '/app/reportes/movimientos-sucursal/': 'reporte_movimientos_sucursal',
    '/app/api/reporte-movimientos-sucursal/': 'reporte_movimientos_sucursal',
    '/app/api/exportar-movimientos-sucursal-excel/': 'reporte_movimientos_sucursal',
    '/app/reportes/despachos-tiendas/': 'reporte_movimientos_sucursal',
    '/app/api/reporte-despachos-tiendas/': 'reporte_movimientos_sucursal',

    # Despachos por proveedor
    '/app/verReporteDespachosProveedor/': 'reporte_despachos_proveedor',
    '/app/reporte_despachos_por_proveedor/': 'reporte_despachos_proveedor',

    # Compras integral
    '/app/reportes/compras/': 'reporte_compras',
    '/app/api/reporte-compras/': 'reporte_compras',
    '/app/api/exportar-reporte-compras-excel/': 'reporte_compras',
    '/app/api/rendimiento-compras/': 'reporte_compras',

    # Rendimiento por proveedor
    '/app/reportes/rendimiento-proveedor/': 'reporte_rendimiento_proveedor',
    '/app/api/reporte-rendimiento-proveedor/': 'reporte_rendimiento_proveedor',
    '/app/api/exportar-rendimiento-proveedor-excel/': 'reporte_rendimiento_proveedor',

    # --- Fase B auditoría 2026-08 (P1-10): tres reportes sin gate de rol ---
    # Sus códigos NO existían en OpcionMenu; los crea inicializar_permisos
    # (correrlo en prod junto con el deploy, o el decorador fail-closed de las
    # vistas dará 403 a todos). Endpoint por endpoint, sin prefijos amplios.

    # Ventas global por empresa (KPIs consolidados multi-empresa)
    '/app/reportes/ventas-global/': 'reporte_ventas_global',
    '/app/api/reportes/ventas-global-empresa/': 'reporte_ventas_global',

    # Productos creados por origen (alta por compra / manual / traspaso / ajuste)
    '/app/reportes/productos-origen/': 'reporte_productos_origen',
    '/app/api/reportes/productos-origen/': 'reporte_productos_origen',

    # Inteligencia de compra (análisis + pronóstico por marca, con costos y GMROI).
    # ⚠️ Solo su página y SU API: '/app/api/plan-liquidacion/' y
    # '/app/reportes/plan-liquidacion/' son de plan_liquidacion (otro permiso)
    # y no se tocan aquí.
    '/app/reportes/inteligencia-compra/': 'inteligencia_compra',
    '/app/api/inteligencia-compra/': 'inteligencia_compra',


    # Ventas (adicionales)
    '/app/ventas/revision-arqueos/': 'revision_arqueos',
    
    # Ecommerce
    '/app/ecommerce/pedidos/': 'ecommerce_pedidos_todos',
    
    # Configuración
    '/app/gestion_usuarios/': 'gestion_usuarios',
    '/app/gestion-sucursales/': 'gestion_sucursales',
    '/empresa_management/lista_empresas/': 'gestion_empresas',
    '/empresa_management/lista_clientes/': 'gestion_clientes',
    '/app/gestion_vendedores/': 'gestion_vendedores',

    # --- CRUD de vendedores ---
    # Solo estaba mapeada la PÁGINA ('/app/gestion_vendedores/'). Los endpoints
    # que escriben colgaban de @login_required + @csrf_exempt, así que cualquier
    # usuario autenticado podía crear un vendedor, borrarlo o **editarse su
    # propio porcentaje de comisión** con un POST/PUT directo.
    # Único consumidor de los 4: gestion_vendedores.html (que ya exige el mismo
    # permiso), por lo que no cambia nada para quien hoy usa la pantalla.
    # OJO: '/app/obtener_vendedores/' NO se mapea a propósito — lo consume
    # también dashboard_ventas.html para poblar su filtro de vendedor.
    '/app/crear_vendedor/': 'gestion_vendedores',
    '/app/editar_vendedor/': 'gestion_vendedores',
    '/app/eliminar_vendedor/': 'gestion_vendedores',
    '/app/exportar_vendedores/': 'gestion_vendedores',

    # --- CRUD de empresas (y sus sucursales/contactos) ---
    # Mismo caso: la lista estaba protegida y el CRUD no.
    # '/empresa_management/empresas/' cubre las rutas con <empresa_id> en medio
    # (crear sucursal, crear contacto, listar sucursales, reporte de clientes);
    # '/empresa_management/sucursales/' cubre editar/eliminar sucursal y
    # '/empresa_management/contactos/' el borrado de contactos.
    # Se usa 'gestion_empresas' (no 'gestion_sucursales') porque la única puerta
    # de entrada a esas pantallas es lista_empresas.html: exigir otro código
    # dejaría fuera a roles que hoy administran empresas.
    # OJO: '/empresa_management/crear_empresa/' NO se mapea — lo llama también
    # el modal "Nueva Empresa" del POS (generacionVentas.html) y gatearlo con
    # 'gestion_empresas' dejaría a cajeros y jefes de local sin poder facturar.
    '/empresa_management/editar_empresa/': 'gestion_empresas',
    '/empresa_management/eliminar_empresa/': 'gestion_empresas',
    '/empresa_management/activar_desactivar_empresa/': 'gestion_empresas',
    '/empresa_management/importar_empresas_sucursales/': 'gestion_empresas',
    '/empresa_management/exportar_empresas/': 'gestion_empresas',
    '/empresa_management/exportar_empresas_sucursales/': 'gestion_empresas',
    '/empresa_management/empresas/': 'gestion_empresas',
    '/empresa_management/sucursales/': 'gestion_empresas',
    '/empresa_management/contactos/': 'gestion_empresas',
    # Alta rápida de empresa desde el wizard de emisión por concepto: su único
    # consumidor es emisionDTEConcepto.html, que ya exige 'emision_dte'.
    '/app/crear_empresa_rapida/': 'emision_dte',

    # --- CRUD de clientes ---
    # Único consumidor: lista_clientes.html, que ya exige 'gestion_clientes'.
    # Las altas de cliente del POS, cotizaciones y requerimientos viven en otras
    # URLs ('/app/api/guardar-cliente-pos/', '/app/api/cotizaciones/crear-cliente/',
    # '/app/api/requerimientos/crear-cliente/') y quedan intencionalmente fuera.
    '/empresa_management/crear_cliente/': 'gestion_clientes',
    '/empresa_management/editar_cliente/': 'gestion_clientes',
    '/empresa_management/eliminar_cliente/': 'gestion_clientes',
    '/empresa_management/activar_desactivar_cliente/': 'gestion_clientes',
    '/empresa_management/exportar_clientes/': 'gestion_clientes',
    '/app/permisos/gestion/': 'gestion_permisos',
    '/app/permisos/': 'gestion_permisos',
    '/app/configuracion/interfaz-prueba-acepta/': 'interfaz_acepta',
    # El generador de TXT del SII colgaba solo de @login_required: la URL no
    # matcheaba ninguna clave del mapa. Único consumidor:
    # static/js/generador_txt_acepta.js, cargado exclusivamente por
    # interfaz_prueba_acepta.html, que ya exige este mismo permiso.
    '/app/documentos/generar-txt-acepta/': 'interfaz_acepta',
    
    # Usuario
    '/users/mi-perfil/': 'mi_perfil',
    '/app/ajuste-stock-rapido/': 'ajuste_stock_rapido',
    '/app/cambiar-empresa/': 'cambiar_empresa',
}

# URLs que nunca deben verificar permisos (páginas públicas o con su propia autenticación)
URLS_SIN_VERIFICACION = [
    '/app/home/',       # Home siempre accesible (tiene su propia verificación interna)
    '/app/dashboard/',  # Dashboard general siempre accesible (es diferente a dashboard_general del menú)
    '/app/bienvenida/', # Página de bienvenida básica siempre accesible
]

# URLs restringidas a sucursales específicas (por alias).
# Si la sucursal activa NO está en la lista, se deniega el acceso con un mensaje claro.
URL_SOLO_SUCURSALES = {
    '/app/verGestionProducto/': ['EDEL', 'GILD', 'IMP', 'PA00'],
}

# URLs que siempre están permitidas (login, logout, static, etc.)
URLS_SIEMPRE_PERMITIDAS = [
    '/accounts/',
    '/admin/',
    '/static/',
    '/media/',
    '/api/',  # APIs pueden tener su propia autenticación
    '/__debug__/',  # Django Debug Toolbar
    '/logout/',
    '/login/',
    '/favicon.ico',
    '/puntos/',  # landing pública de la app de fidelización (QR en tickets)
]


class PermisosMenuMiddleware:
    """
    Middleware que verifica los permisos de acceso basados en la configuración del menú.
    
    Si el usuario no tiene permiso 'puede_ver' para una opción del menú,
    no podrá acceder a la URL correspondiente.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if (request.user.is_authenticated
                and not request.session.get('idSucursalActual')
                and request.path.startswith('/app/')):
            self._corregir_sesion_sin_sucursal(request)

        resultado_verificacion = self.verificar_permiso(request)
        
        if resultado_verificacion is not None:
            return resultado_verificacion
        
        response = self.get_response(request)
        return response

    @staticmethod
    def _corregir_sesion_sin_sucursal(request):
        from .models import EmpresaUser
        eu = EmpresaUser.objects.filter(
            user=request.user, status=True, sucursal__isnull=False
        ).select_related('empresa', 'sucursal').first()
        if not eu:
            return
        if not eu.active:
            EmpresaUser.objects.filter(user=request.user).update(active=False)
            eu.active = True
            eu.save(update_fields=['active'])
        request.session['idEmpresaActual'] = eu.empresa.id
        request.session['idSucursalActual'] = eu.sucursal.id
        request.session['direccionSucursal'] = eu.sucursal.direccion or 'Sin dirección'
        request.session['alias'] = eu.sucursal.alias or 'Sin sucursal'
        request.session['nombreEmpresaActual'] = eu.empresa.nombre
        request.session['rutEmpresaActual'] = eu.empresa.rut
    
    def verificar_permiso(self, request):
        """
        Verifica si el usuario tiene permiso para acceder a la URL actual.
        
        Returns:
            None si tiene permiso, o una respuesta de error/redirect si no tiene.
        """
        path = request.path
        
        # 1. Verificar si la URL está siempre permitida (login, logout, static, etc.)
        for url_permitida in URLS_SIEMPRE_PERMITIDAS:
            if path.startswith(url_permitida):
                return None  # Permitir acceso
        
        # 2. Verificar si la URL no requiere verificación de permisos
        for url_sin_verificacion in URLS_SIN_VERIFICACION:
            if path.startswith(url_sin_verificacion):
                return None  # Permitir acceso
        
        # 3. Si el usuario no está autenticado, dejar que Django maneje la autenticación
        if not request.user.is_authenticated:
            return None  # Django redirigirá al login si es necesario

        # 4a. Verificar restricción por sucursal activa (antes del chequeo de roles)
        for url_restringida, sucursales_permitidas in URL_SOLO_SUCURSALES.items():
            if url_restringida in path:
                alias_actual = request.session.get('alias', '')
                if alias_actual.upper() not in [s.upper() for s in sucursales_permitidas]:
                    return self.denegar_acceso_sucursal(
                        request, sucursales_permitidas, alias_actual
                    )
                break
        
        # 4. Buscar si la URL tiene un permiso asociado
        # Nota: Todos los usuarios respetan los permisos por rol. is_superuser no otorga privilegios.
        codigo_opcion = self.obtener_codigo_opcion(path)
        
        if codigo_opcion is None:
            # URL no está en el mapa de permisos, permitir acceso
            # (esto incluye páginas de error, APIs sin mapeo, etc.)
            return None
        
        # 6. Obtener la sucursal actual de la sesión
        sucursal_id = request.session.get('idSucursalActual')
        
        # 7. Verificar permiso (por rol y por sucursal)
        tiene_permiso = PermisoRol.tiene_permiso(
            usuario=request.user,
            codigo_opcion=codigo_opcion,
            tipo_permiso='puede_ver',
            sucursal_id=sucursal_id
        )
        
        if tiene_permiso:
            return None  # Permitir acceso
        
        # 7. Denegar acceso
        return self.denegar_acceso(request, codigo_opcion)
    
    def obtener_codigo_opcion(self, path):
        """
        Obtiene el código de opción del menú para una URL dada.
        """
        # Buscar coincidencia exacta primero
        if path in URL_PERMISO_MAP:
            return URL_PERMISO_MAP[path]

        # Coincidencia parcial: gana la clave MÁS LARGA que esté contenida en el
        # path. Así una clave genérica ('/app/giftcards/', '/app/api/despacho/')
        # no puede tapar a una más específica según el orden del diccionario.
        mejor_pattern = None
        for url_pattern in URL_PERMISO_MAP:
            if url_pattern in path and (mejor_pattern is None or len(url_pattern) > len(mejor_pattern)):
                mejor_pattern = url_pattern

        return URL_PERMISO_MAP[mejor_pattern] if mejor_pattern else None
    
    def denegar_acceso(self, request, codigo_opcion):
        """
        Genera la respuesta de acceso denegado.
        """
        # Determinar si es petición AJAX
        is_ajax = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            request.content_type == 'application/json' or
            request.headers.get('Accept', '').startswith('application/json')
        )
        
        if is_ajax:
            return JsonResponse({
                'success': False,
                'error': True,
                'mensaje': 'No tienes permiso para acceder a esta funcionalidad.',
                'codigo_requerido': codigo_opcion
            }, status=403)
        else:
            messages.error(
                request,
                '⚠️ Acceso denegado: No tienes permiso para acceder a esta funcionalidad. '
                'Contacta al administrador si crees que deberías tener acceso.'
            )
            # Redirigir a página de bienvenida (siempre accesible)
            return redirect('bienvenida')

    def denegar_acceso_sucursal(self, request, sucursales_permitidas, alias_actual):
        """
        Deniega el acceso porque la sucursal activa no está en la lista permitida.
        """
        is_ajax = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
            request.content_type == 'application/json' or
            request.headers.get('Accept', '').startswith('application/json')
        )
        lista = ', '.join(sucursales_permitidas)
        mensaje = (
            f'⚠️ Módulo restringido: solo accesible desde las sucursales {lista}. '
            f'Tu sucursal activa es "{alias_actual or "Sin sucursal"}". '
            'Cambia de sucursal para continuar.'
        )
        if is_ajax:
            return JsonResponse({
                'success': False,
                'error': True,
                'mensaje': mensaje,
            }, status=403)
        messages.error(request, mensaje)
        return redirect('bienvenida')
