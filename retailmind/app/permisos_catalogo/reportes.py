# -*- coding: utf-8 -*-
"""Catálogo de permisos — Reportes y Liquidación. Ver app/permisos_catalogo/__init__.py."""

# Cómo se aplica `puede_ver` en casi todos los reportes: el middleware de
# permisos (URL_PERMISO_MAP) lo exige para la PÁGINA y para cada API JSON /
# Excel / PDF del reporte; las vistas solo piden login. Por eso, salvo donde se
# indique, descargar el Excel o el PDF exige el mismo `puede_ver` que entrar,
# y `puede_exportar` no se revisa en ninguna parte.
#
# Alcance de datos: la mayoría acota por las sucursales/empresas del usuario y
# lo amplía a todo el holding si tiene el override `puede_ver_todas_sucursales`
# (utils_permisos.ids_sucursales_alcance / usuario_puede_ver_todas_sucursales).
# El rol maestro pasa todos los chequeos; PermisoSucursal solo puede quitar.

CATALOGO = {
    # ------------------------------------------------------------------ Ventas
    'reporte_ventas_sucursal': {
        'pantalla': 'Ventas por Sucursal',
        'ruta': '/app/reportes/ventas-sucursal/',
        'resumen': 'Ventas del período por vendedor y por sucursal, con comparativa mensual y detalle de documentos por vendedor.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú y cargar sus tablas, la comparativa mensual, el detalle por vendedor y el diagnóstico vs cuadratura.',
        },
        'depende_de': [],
        'notas': 'Los botones Excel y PDF de esta pantalla solo muestran «Funcionalidad en desarrollo»; «Imprimir» usa la impresión del navegador. El botón «Comisiones» y su Excel dependen de reporte_comisiones_vendedor. El alcance por sucursal se amplía con puede_ver_todas_sucursales.',
    },
    'reporte_ventas_comparativo': {
        'pantalla': 'Comparativo de Ventas',
        'ruta': '/app/reportes/ventas-comparativo/',
        'resumen': 'Compara las ventas del período actual contra el período anterior equivalente (hoy, semana, mes, últimos 30 días, año contra año).',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú y consultar la comparativa de ventas.',
        },
        'depende_de': [],
        'notas': 'No tiene exportación. El alcance por sucursal se amplía con puede_ver_todas_sucursales.',
    },
    'reporte_productos_vendidos': {
        'pantalla': 'Productos Vendidos',
        'ruta': '/app/reportes/productos-vendidos/',
        'resumen': 'Productos vendidos en el período, agrupados por marca, categoría y género, con mapa de calor.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú, consultar los productos vendidos y cargar las opciones de sus filtros.',
        },
        'depende_de': [],
        'notas': 'No tiene exportación. El alcance por sucursal se amplía con puede_ver_todas_sucursales.',
    },
    'reporte_ventas_internet': {
        'pantalla': 'Ventas Internet',
        'ruta': '/app/reportes/ventas-internet/',
        'resumen': 'Ventas de los canales de internet (ecommerce y marketplaces) en el período.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú, consultar las ventas de internet y descargar el Excel (botón «Exportar»).',
        },
        'depende_de': [],
        'notas': 'El Excel (/app/reportes/ventas-internet/exportar/) exige solo puede_ver: lo cubre la clave de la página en el middleware. puede_exportar no se revisa.',
    },
    'reporte_documentos_emitidos': {
        'pantalla': 'Documentos Emitidos',
        'ruta': '/app/reportes/documentos-emitidos/',
        'resumen': 'Listado de boletas, facturas y notas de crédito emitidas en el período, con totales por tipo y medio de pago.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú, consultar los documentos y descargar el Excel («Exportar Excel»).',
        },
        'depende_de': [],
        'notas': 'El Excel exige solo puede_ver (mapa del middleware); puede_exportar no se revisa. «Imprimir» es la impresión del navegador. El botón de reimprimir ticket de cambio usa el API de tickets, fuera de este permiso. Alcance por sucursal ampliable con puede_ver_todas_sucursales.',
    },
    'reporte_comisiones_vendedor': {
        'pantalla': 'Comisiones por Vendedor (dentro de Ventas por Sucursal)',
        'ruta': '',
        'resumen': 'Comisión de cada vendedor (venta neta sin IVA × su % de comisión) más el bono por sucursal, para los filtros del reporte de ventas.',
        'permisos': {
            'puede_ver': 'Ver el botón «Comisiones» en Ventas por Sucursal, abrir el modal y calcular las comisiones y el bono por sucursal.',
            'puede_exportar': 'Ver el botón «Exportar Excel» del modal de comisiones y descargar el Excel (exige además puede_ver).',
        },
        'depende_de': ['reporte_ventas_sucursal'],
        'notas': 'Único reporte donde el Excel exige puede_exportar de verdad: la vista lo revalida, no solo el middleware. Se evalúa con la sucursal activa, así que PermisoSucursal puede apagarlo por tienda. No es una pantalla ni aparece en el menú.',
    },

    # ------------------------------------------------------------- Existencias
    'reporte_existencias': {
        'pantalla': 'Reporte de Existencias',
        'ruta': '/app/reportes/existencias/',
        'resumen': 'Stock por producto y talla con costos y precios, filtrable por sucursal, categoría y estado de stock.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú, consultar el stock con costos y precios y descargar el Excel («Exportar»).',
        },
        'depende_de': [],
        'notas': 'El Excel exige solo puede_ver (mapa del middleware); puede_exportar no se revisa. Los datos se acotan a las empresas asignadas al usuario.',
    },
    'reporte_existencias_marca': {
        'pantalla': 'Existencias por Marca',
        'ruta': '/app/reportes/existencias-marca/',
        'resumen': 'Stock y valorización agrupados por marca, con detalle por sucursal.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú, consultar las existencias por marca y descargar el Excel («Exportar»).',
        },
        'depende_de': [],
        'notas': 'El Excel exige solo puede_ver (mapa del middleware); puede_exportar no se revisa.',
    },
    'reporte_existencias_sucursal': {
        'pantalla': 'Existencias por Sucursal (incluye Quiebre de Talla)',
        'ruta': '/app/reportes/existencias-sucursal/',
        'resumen': 'Stock por sucursal y su valorización; Quiebre de Talla muestra las tallas en cero con venta histórica y qué se puede reponer desde bodega.',
        'permisos': {
            'puede_ver': 'Entrar, verlo en el menú, consultar el stock por sucursal, descargar Excel y PDF y abrir «Quiebre de talla» (mismo permiso).',
        },
        'depende_de': [],
        'notas': 'Excel y PDF exigen solo puede_ver; puede_exportar no se revisa. Quiebre de Talla (/app/reportes/quiebre-talla/) no tiene opción de menú: se abre desde el botón de este reporte y su vista revalida este permiso. Solo se puede pedir una sucursal del alcance (ids_sucursales_alcance).',
    },
    'resumen_existencias': {
        'pantalla': 'Resumen Existencias',
        'ruta': '/app/reportes/resumen-existencias/',
        'resumen': 'Totales de stock y valorización por sucursal o por categoría, con vista histórica a una fecha y detalle de los productos top por sucursal.',
        'permisos': {
            'puede_ver': 'Entrar, verlo en el menú, consultar el resumen por sucursal o categoría, el histórico y el detalle, y descargar Excel y PDF.',
        },
        'depende_de': [],
        'notas': 'Excel y PDF exigen solo puede_ver (mapa del middleware); las vistas solo piden login. puede_exportar no se revisa. Los datos se acotan a las empresas asignadas al usuario.',
    },
    'reporte_movimientos_sucursal': {
        'pantalla': 'Inicial vs Restante (incluye Despachos a Tiendas)',
        'ruta': '/app/reportes/movimientos-sucursal/',
        'resumen': 'Stock inicial vs restante por sucursal en el período; Despachos a Tiendas muestra lo que las bodegas del usuario enviaron a cada tienda.',
        'permisos': {
            'puede_ver': 'Entrar, ver en el menú «Inicial vs Restante» y «Despachos a Tiendas», consultar ambos y descargar el Excel de Inicial vs Restante.',
        },
        'depende_de': [],
        'notas': 'El Excel exige solo puede_ver; puede_exportar no se revisa. El CSV de Despachos a Tiendas se arma en el navegador con los datos ya cargados, sin endpoint propio. Alcance por sucursal ampliable con puede_ver_todas_sucursales.',
    },

    # ----------------------------------------------------------------- Compras
    'reporte_despachos_proveedor': {
        'pantalla': 'Despachos por Proveedor',
        'ruta': '/app/verReporteDespachosProveedor/',
        'resumen': 'Ingresos por proveedor y DTE de compra: unidades del documento, pendientes de ingreso, ingresadas y su monto.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú y consultar los ingresos por proveedor; el botón de exportar genera un CSV en el navegador.',
        },
        'depende_de': [],
        'notas': 'La exportación es un CSV armado en el navegador con lo ya cargado: no hay endpoint ni se revisa puede_exportar. El listado de proveedores del filtro (/app/obtener_proveedores_para_reporte/) no está en el mapa y solo pide login. Datos acotados a las empresas del usuario.',
    },
    'reporte_compras': {
        'pantalla': 'Reporte de Compras',
        'ruta': '/app/reportes/compras/',
        'resumen': 'Compras del período por proveedor, sucursal y documento, más el rendimiento anual entrada → despacho → venta.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú, consultar las compras y el rendimiento anual, y descargar el Excel («Exportar»).',
        },
        'depende_de': [],
        'notas': 'El Excel exige solo puede_ver (mapa del middleware); puede_exportar no se revisa. «Ver recepción» abre Recepción DTE, que exige su propio permiso (recepcion_dte).',
    },
    'reporte_rendimiento_proveedor': {
        'pantalla': 'Rendimiento por Proveedor',
        'ruta': '/app/reportes/rendimiento-proveedor/',
        'resumen': 'Rendimiento de cada proveedor: lo comprado, lo vendido, rotación y margen en el período.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú, consultar el rendimiento por proveedor y descargar el Excel (botón «Excel»).',
        },
        'depende_de': [],
        'notas': 'El Excel exige solo puede_ver (mapa del middleware); puede_exportar no se revisa. Alcance por sucursal ampliable con puede_ver_todas_sucursales.',
    },
    'reporte_diferencias_recepcion': {
        'pantalla': 'Diferencias de Recepción',
        'ruta': '/app/reportes/diferencias-recepcion/',
        'resumen': 'Faltantes, dañados y sobrantes entre lo despachado y lo recepcionado, por sucursal y proveedor, con valorización.',
        'permisos': {
            'puede_ver': 'Entrar al reporte y consultar sus datos y filtros (la vista y su API exigen este permiso directamente).',
        },
        'depende_de': [],
        'notas': 'No tiene enlace en el menú lateral: se entra por URL. No está en el mapa del middleware; lo exige @requiere_permiso en la vista (código en constante, por eso el escáner no lo ve). Sin exportación. Si el código no existe en OpcionMenu, todos reciben 403.',
    },
    'reporte_mercaderia_transito': {
        'pantalla': 'Mercadería en Tránsito',
        'ruta': '/app/reportes/mercaderia-transito/',
        'resumen': 'Despachos entre sucursales aún no recibidos, consolidados para todas las sucursales del usuario, con antigüedad, valorización y detalle por SKU.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, consultar los despachos en tránsito y abrir el detalle por SKU («Ver SKUs»).',
        },
        'depende_de': [],
        'notas': 'No tiene enlace en el menú lateral: se entra por URL. No está en el mapa del middleware; lo exige @requiere_permiso en la vista (código en constante). Sin exportación. Datos acotados a las empresas del usuario, ampliables con puede_ver_todas_sucursales.',
    },

    # ------------------------------------------------- Consolidados / análisis
    'reporte_ventas_global': {
        'pantalla': 'Ventas Global por Empresa',
        'ruta': '/app/reportes/ventas-global/',
        'resumen': 'Ventas consolidadas de todas las sucursales agrupadas por empresa, con comparativo contra el período anterior.',
        'permisos': {
            'puede_ver': 'Entrar al reporte y consultar las ventas consolidadas por empresa.',
        },
        'depende_de': [],
        'notas': 'No tiene enlace en el menú lateral: se entra por URL. Sin exportación. Sin puede_ver_todas_sucursales solo suma las sucursales asignadas al usuario.',
    },
    'reporte_productos_origen': {
        'pantalla': 'Productos por Origen',
        'ruta': '/app/reportes/productos-origen/',
        'resumen': 'Productos creados en el período clasificados por su origen: compra, creación manual, traspaso o ajuste.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú y consultar las altas de catálogo por origen.',
        },
        'depende_de': [],
        'notas': 'Sin exportación.',
    },
    'inteligencia_compra': {
        'pantalla': 'Inteligencia de Compra',
        'ruta': '/app/reportes/inteligencia-compra/',
        'resumen': 'Análisis histórico y pronóstico de compra de una marca (demanda, stock, costos y GMROI) para decidir cuánto y qué comprar.',
        'permisos': {
            'puede_ver': 'Entrar al reporte, verlo en el menú y consultar el análisis y el pronóstico de una marca.',
        },
        'depende_de': [],
        'notas': '«Imprimir / PDF» es la impresión del navegador: no revisa puede_exportar. Datos acotados a las sucursales de las empresas asignadas al usuario; puede_ver_todas_sucursales no los amplía.',
    },

    # ------------------------------------------------------------- Liquidación
    'plan_liquidacion': {
        'pantalla': 'Plan de Liquidación',
        'ruta': '/app/reportes/plan-liquidacion/',
        'resumen': 'Ranking del stock a liquidar por antigüedad, marca y sucursal, con descuento sugerido y selección de productos para armar campañas.',
        'permisos': {
            'puede_ver': 'Entrar, verlo en el menú, consultar el plan, su detalle y los gráficos por antigüedad, e «Importar» un Excel o CSV de verificación.',
            'puede_exportar': 'Descargar el Excel de verificación («Exportar») y abrir el formulario «Imprimir verificación».',
        },
        'depende_de': ['campanas_liquidacion'],
        'notas': '«Crear campaña» exige campanas_liquidacion.puede_crear. El «Imprimir / PDF» del encabezado es impresión del navegador, sin permiso. Importar solo pide puede_ver. Datos acotados a las sucursales de las empresas asignadas al usuario.',
    },
    'campanas_liquidacion': {
        'pantalla': 'Campañas de Liquidación',
        'ruta': '/app/campanas-liquidacion/',
        'resumen': 'Campañas de precios de liquidación y promos NxM (2x1, 3x2) sobre conjuntos de productos: borrador, activa, finalizada.',
        'permisos': {
            'puede_ver': 'Entrar, verlo en el menú, listar las campañas con sus contadores por estado y abrir el detalle de cada una.',
            'puede_crear': 'Crear una campaña en borrador desde «Crear campaña» del Plan de Liquidación (artículos y sucursales elegidos).',
            'puede_editar': 'Activar una campaña en borrador (aplica precios/NxM), cerrarla si está activa (restaura precios) y agregar o quitar productos.',
        },
        'depende_de': [],
        'notas': 'Hoy ninguna pantalla usa el endpoint de agregar/quitar productos: solo existe como API. puede_eliminar no se revisa: las campañas no se borran, se cierran. Los endpoints de promos y ofertas activas que usa el POS solo piden login.',
    },
}
