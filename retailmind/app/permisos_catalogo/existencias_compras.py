# -*- coding: utf-8 -*-
"""Catálogo de permisos — Existencias, Compras y Requerimientos. Ver app/permisos_catalogo/__init__.py."""

CATALOGO = {
    # ------------------------------------------------------------------
    # EXISTENCIAS
    # ------------------------------------------------------------------
    'gestion_producto': {
        'pantalla': 'Gestión Producto',
        'ruta': '/app/verGestionProducto/',
        'resumen': 'Crear productos (manual o desde recepciones), editar fichas, ajustar stock y administrar guías de talla.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú (cubre también la función Salida de stock).',
            'puede_editar': 'Guardar la ficha editada, aplicar la Edición masiva, hacer Salida de stock y editar atributos de líneas de compra.',
        },
        'depende_de': [],
        'notas': 'Solo desde las sucursales EDEL, GILD, IMP y PA00. Crear producto (manual o desde recepción), '
                 'ajustar/eliminar una talla y Excluir de analítica no revisan permiso: basta estar logueado. '
                 'puede_crear y puede_eliminar no se usan.',
    },
    'edicion_rapida_precios': {
        'pantalla': 'Gestión de Precios',
        'ruta': '/app/gestion-precios/edicion-rapida/',
        'resumen': 'Buscar productos y cambiar su precio de venta al vuelo, con recomendaciones e historial.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú (también por la ruta antigua /app/edicion-rapida-precios/).',
            'puede_editar': 'Guardar el precio nuevo en la pantalla y corregir precio o recategorizar desde la app móvil NEXO Staff.',
        },
        'depende_de': [],
        'notas': 'Modificación masiva, sincronizar sucursales y proponer cambio no revisan este permiso (solo login). '
                 'Si la opción no existe en la BD, la app móvil deja editar a administrador y jefe de local.',
    },
    'revisar_cambios_precios': {
        'pantalla': 'Alertas de Precios',
        'ruta': '/app/gestion-precios/revisar-pendientes/',
        'resumen': 'Bandeja de cambios de precio propuestos: revisar, aprobar (aplica el precio) o rechazar.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú.',
            'puede_aprobar': 'Usar Aprobar y Rechazar (por fila y masivo) y el Aprobar del widget de precios del Dashboard General.',
        },
        'depende_de': [],
        'notas': 'puede_aprobar se evalúa por rol y usuario, nunca por sucursal (a propósito: en prod ninguna sucursal '
                 'tiene aprobar en True). Descartar/archivar, listar y exportar Excel no revisan permiso.',
    },
    'movimientos_producto': {
        'pantalla': 'Movimientos Por Sucursal',
        'ruta': '/app/verMovimientosProducto/',
        'resumen': 'Consultar los movimientos de stock (ingresos, egresos, traspasos) de una sucursal por período.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú.',
        },
        'depende_de': [],
        'notas': 'La consulta de datos (/app/obtener_movimientos_producto/) no está cubierta: responde a cualquier logueado.',
    },
    'gestion_inventarios': {
        'pantalla': 'Gestión de Inventarios',
        'ruta': '/app/gestion-inventarios/',
        'resumen': 'Tomas de inventario: crear, contar, recontar, aprobar y aplicar ajustes al stock. Incluye Fusionar Duplicados.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla, ver detalle y análisis, exportar Excel y usar Fusionar Duplicados (buscar y fusionar).',
            'puede_crear': 'Crear una toma de inventario nueva (botón Nuevo Inventario).',
            'puede_editar': 'Registrar conteo/reconteo, importar pistola, excluir líneas, finalizar, aprobar, rechazar, aplicar ajustes y cancelar.',
        },
        'depende_de': [],
        'notas': 'Aprobar Inventario y Aplicar Ajustes usan puede_editar a propósito (puede_aprobar está en False en toda '
                 'sucursal de prod). Fusionar duplicados mueve stock y solo pide puede_ver. puede_eliminar, puede_exportar '
                 'y puede_aprobar no se usan.',
    },
    'gestion_etiquetas_zebra': {
        'pantalla': 'Impresión Etiquetas Zebra',
        'ruta': '/app/etiquetas/',
        'resumen': 'Generar etiquetas Zebra desde documentos (recepciones, traspasos) o buscando productos.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú; cubre buscar documentos/productos y generar los datos de etiquetas.',
        },
        'depende_de': [],
        'notas': '',
    },
    'buscar_productos_sucursal': {
        'pantalla': 'Buscar Producto Sucursal',
        'ruta': '/app/productos-sucursal/',
        'resumen': 'Buscar productos y ver stock por talla en la sucursal actual (pantalla táctil de tienda).',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú.',
        },
        'depende_de': [],
        'notas': 'La búsqueda de datos (/app/api/productos-sucursal/) no está cubierta: responde a cualquier logueado.',
    },
    'tarjeta_movimiento_producto': {
        'pantalla': 'Tarjeta Movimiento Producto',
        'ruta': '/app/tarjeta-movimiento/',
        'resumen': 'Ver el kardex (tarjeta de movimientos) de un producto: entradas, salidas y saldo.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla, verla en el menú, buscar productos y consultar su tarjeta.',
        },
        'depende_de': [],
        'notas': '',
    },
    'despacho_sucursales': {
        'pantalla': 'Despacho a Sucursales',
        'ruta': '/app/despacho-sucursales/',
        'resumen': 'Armar y crear despachos (traspasos) masivos desde la bodega a varias tiendas y ver su historial.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla, ver productos, pendientes e historial, y Crear el despacho masivo.',
        },
        'depende_de': [],
        'notas': 'Crear despacho mueve stock y solo pide puede_ver: no hay flag de crear separado.',
    },
    'trazabilidad_producto': {
        'pantalla': 'Trazabilidad Completa',
        'ruta': '/app/trazabilidad-producto/',
        'resumen': 'Seguir el recorrido completo de un producto: compra, recepción, traspasos, ventas y ajustes.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla, verla en el menú y consultar la ficha de trazabilidad.',
        },
        'depende_de': [],
        'notas': '',
    },
    'modificacion_precios_costos': {
        'pantalla': 'Modificación Precios y Costos',
        'ruta': '/app/precios-costos/',
        'resumen': 'Cambiar precio de venta, sobreprecio y costo de productos, uno a uno o de forma masiva.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y usar Modificar y Modificar masivo: con solo ver ya se cambian precios y costos.',
        },
        'depende_de': [],
        'notas': 'Opción marcada como sensible en la pantalla de permisos. No existe flag de editar: todo cuelga de puede_ver.',
    },
    'ver_guias_talla': {
        'pantalla': 'Guías de Talla',
        'ruta': '/app/ver_guias_talla/',
        'resumen': 'Administrar guías de talla por marca: crear, editar, ordenar y eliminar.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú (quien tiene Gestión Producto también la ve).',
        },
        'depende_de': [],
        'notas': 'Crear, editar y eliminar guías no revisan permiso: los usa también el modal de guía dentro de '
                 'Gestión Producto y Gestión Compras.',
    },

    # ------------------------------------------------------------------
    # COMPRAS
    # ------------------------------------------------------------------
    'gestion_compras': {
        'pantalla': 'Gestión Compras',
        'ruta': '/app/verGestionCompras/',
        'resumen': 'Registrar compras a proveedores, recepcionar mercadería, vincular productos y seguir pendientes.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú.',
            'puede_editar': 'Usar Editar Compra (abrir el modal y guardar los cambios de la compra).',
            'puede_eliminar': 'Ver y usar Eliminar Compra en el menú de acciones de cada compra.',
        },
        'depende_de': [],
        'notas': 'Crear compra, Recepcionar, Editar recepciones y Vincular productos no revisan permiso (solo login); '
                 'Importar CSV no exige ni login. puede_crear no se usa.',
    },
    'gestion_dte_compras': {
        'pantalla': 'Gestión Documentos Compras',
        'ruta': '/app/verGestionDteCompras/',
        'resumen': 'Facturas y documentos de proveedores: registrar, pagar, notas de crédito e incidencias.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla, verla en el menú y verificar si un documento está duplicado.',
            'puede_crear': 'Importar facturas de proveedor desde XML del SII (/app/compras/importar-xml-dte/: analizar y confirmar).',
        },
        'depende_de': ['dte_compras_pagos', 'dte_compras_eliminar'],
        'notas': 'Crear/editar el documento a mano, Pagar, notas de crédito e incidencias no revisan este permiso. '
                 'Editar/eliminar pagos y eliminar el documento usan los permisos de depende_de. '
                 'La importación XML no tiene link en el menú (solo por URL).',
    },
    'prediccion_compras': {
        'pantalla': 'Predicción de Compras',
        'ruta': '/app/prediccion/',
        'resumen': 'Dashboard de predicción: sugerencias de compra, clasificación, alertas de velocidad y quiebre.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y verla en el menú.',
        },
        'depende_de': [],
        'notas': 'Los datos y acciones (/app/api/prediccion/: aprobar sugerencia, recalcular, configuración) no están '
                 'cubiertos: cualquier logueado.',
    },

    # ------------------------------------------------------------------
    # REQUERIMIENTOS
    # ------------------------------------------------------------------
    'lista_requerimientos': {
        'pantalla': 'Requerimientos',
        'ruta': '/app/requerimientos/',
        'resumen': 'Bandeja de requerimientos (reclamos y garantías a proveedor): listar, ver detalle y crear.',
        'permisos': {
            'puede_ver': 'Entrar a Requerimientos, verla en el menú y abrir el detalle de un requerimiento.',
        },
        'depende_de': [],
        'notas': 'Lo que cada uno puede hacer dentro (validar, aprobar, enviar al proveedor, editar) lo decide el ROL '
                 '(administrador todo; jefe de local su sucursal; cajero/vendedor crear y ver), no este permiso. '
                 'Las APIs /app/api/requerimientos/ no están cubiertas.',
    },
    'crear_requerimiento': {
        'pantalla': 'Crear Requerimiento',
        'ruta': '/app/requerimientos/crear/',
        'resumen': 'Acceso directo que abre Requerimientos con el panel de creación desplegado.',
        'permisos': {
            'puede_ver': 'Entrar por /app/requerimientos/crear/ y ver "Crear Requerimiento" en el menú si el rol no tiene la lista.',
        },
        'depende_de': ['lista_requerimientos'],
        'notas': 'Solo es un atajo: redirige a /app/requerimientos/?panel=crear, que exige Lista de Requerimientos. '
                 'Guardar el requerimiento (API) no revisa este permiso. puede_crear no se usa.',
    },
    'gestionar_requerimientos': {
        'pantalla': 'Gestionar Requerimientos',
        'ruta': '/app/requerimientos/gestionar/',
        'resumen': 'Acceso directo (heredado) a la bandeja de Requerimientos para quien gestiona.',
        'permisos': {
            'puede_ver': 'Entrar por /app/requerimientos/gestionar/ y ver "Requerimientos" en el menú aunque falte la lista.',
        },
        'depende_de': ['lista_requerimientos'],
        'notas': 'La ruta solo redirige a /app/requerimientos/ (exige Lista de Requerimientos). No habilita ninguna '
                 'acción extra: aprobar o enviar al proveedor lo decide el rol.',
    },
}
