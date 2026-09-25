# -*- coding: utf-8 -*-
"""Catálogo de permisos — Dashboards, Configuración, Ecommerce, Fidelización y Mi Cuenta. Ver app/permisos_catalogo/__init__.py."""

CATALOGO = {
    # ------------------------------------------------------------------ Dashboard
    'dashboard_general': {
        'pantalla': 'Dashboard General',
        'ruta': '/app/home/',
        'resumen': 'Portada con KPIs de ventas, stock, compras y requerimientos de la sucursal activa.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard General y verlo en el menú; sin él, /app/home/ lleva a la Bienvenida.',
        },
        'depende_de': [],
        'notas': 'Es también el acceso destacado de la Bienvenida para Administrador, Administración y Jefe de Local. '
                 'La API de ventas en tiempo real (/app/dashboard/api/ventas-tiempo-real/) solo pide sesión iniciada.',
    },
    'dashboard_ventas': {
        'pantalla': 'Dashboard Ventas',
        'ruta': '/app/ventas/dashboard-mejorado/',
        'resumen': 'Indicadores de ventas por sucursal, vendedor, categoría, medio de pago y tendencias.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard Ventas, verlo en menú y portada, cargar sus indicadores y exportar a Excel.',
        },
        'depende_de': [],
        'notas': 'Exportar a Excel se controla con puede_ver, no con puede_exportar. '
                 'El listado de vendedores del filtro (/app/obtener_vendedores/) queda abierto a cualquier usuario.',
    },
    'dashboard_productos': {
        'pantalla': 'Dashboard Productos',
        'ruta': '/app/dashboard_productos_mejorado/',
        'resumen': 'KPIs de catálogo y stock: rotación, alertas, flujo y rendimiento de productos.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard Productos (clásico y mejorado), verlo en menú y portada, cargar datos y exportar.',
        },
        'depende_de': [],
        'notas': 'Exportar (CSV/Excel y /app/exportar_productos_filtrado/) va con puede_ver, no con puede_exportar.',
    },
    'dashboard_fifo': {
        'pantalla': 'Dashboard FIFO / Lotes',
        'ruta': '/app/dashboard_fifo/',
        'resumen': 'Lotes FIFO de la sucursal activa: antigüedad, costo y valorización del inventario.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard FIFO, verlo en menú y portada, cargar sus datos y exportar el CSV con costos.',
        },
        'depende_de': [],
        'notas': 'Además exige tener acceso a la sucursal activa. Exportar va con puede_ver, no con puede_exportar.',
    },
    'dashboard_compras_estrategico': {
        'pantalla': 'Dashboard Compras',
        'ruta': '/app/verDashboardComprasMejorado/',
        'resumen': 'Compras por proveedor, temporada y marca, con deuda, márgenes y ranking de proveedores.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard Compras (clásico y mejorado), verlo en menú y portada, cargar datos y exportar.',
        },
        'depende_de': [],
        'notas': 'Exportar va con puede_ver, no con puede_exportar.',
    },
    'dashboard_documentos': {
        'pantalla': 'Dashboard Documentos',
        'ruta': '/app/dashboard-documentos/',
        'resumen': 'Indicadores de documentos tributarios emitidos y recibidos (DTE) por periodo.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard Documentos, verlo en menú y portada y cargar sus datos.',
        },
        'depende_de': [],
        'notas': 'Con este permiso (o Recepción DTE / Consulta Documentos) aparece el módulo Documentos en el menú.',
    },
    'dashboard_despachos': {
        'pantalla': 'Dashboard Despachos',
        'ruta': '/app/dashboard-despachos/',
        'resumen': 'Despachos, recepciones y regularizaciones entre bodegas y tiendas.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard Despachos, verlo en menú y portada y cargar sus datos.',
        },
        'depende_de': [],
        'notas': '',
    },
    'dashboard_requerimientos': {
        'pantalla': 'Dashboard Requerimientos',
        'ruta': '/app/dashboard-requerimientos/',
        'resumen': 'Estado y tiempos de los requerimientos a proveedores.',
        'permisos': {
            'puede_ver': 'Entrar al Dashboard Requerimientos, verlo en menú y portada y cargar sus datos.',
        },
        'depende_de': [],
        'notas': '',
    },

    # -------------------------------------------------------------- Configuración
    'gestion_usuarios': {
        'pantalla': 'Gestión Usuarios',
        'ruta': '/users/gestion/',
        'resumen': 'Alta, edición, activación, roles, claves, asignación de empresas/sucursales e importación de usuarios.',
        'permisos': {
            'puede_ver': 'Mostrar "Gestión Usuarios" en el menú y pasar por /app/gestion_usuarios/ (redirige a /users/gestion/).',
        },
        'depende_de': [],
        'notas': 'La pantalla real y todas sus acciones exigen rol Administrador o Maestro, no este permiso: '
                 'un administrador sin el check entra igual por URL y otro rol no entra ni con él. '
                 'Crear, editar, eliminar y exportar no se revisan por permiso.',
    },
    'gestion_sucursales': {
        'pantalla': 'Gestión Sucursales',
        'ruta': '/app/gestion-sucursales/',
        'resumen': 'Crear, editar y desactivar sucursales de las empresas del grupo.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión Sucursales, verla en el menú y usar Nueva, Editar y Eliminar (mismo permiso).',
        },
        'depende_de': [],
        'notas': 'Crear, editar y eliminar no se revisan: quien entra puede hacer todo.',
    },
    'gestion_empresas': {
        'pantalla': 'Gestión Empresas',
        'ruta': '/empresa_management/lista_empresas/',
        'resumen': 'Empresas del grupo, proveedores y clientes-empresa, con sus sucursales y contactos.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión Empresas, verla en el menú y editar, eliminar, activar e importar/exportar empresas.',
        },
        'depende_de': [],
        'notas': 'Sin permisos finos: el mismo puede_ver cubre sucursales y contactos de cada empresa. '
                 'Crear empresa queda fuera porque lo usa el POS. Las empresas del grupo solo las modifican '
                 'usuarios asignados a ellas o administradores.',
    },
    'gestion_clientes': {
        'pantalla': 'Gestión Clientes',
        'ruta': '/empresa_management/lista_clientes/',
        'resumen': 'Maestro de clientes personas: datos de contacto, tipo de cliente y estado.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión Clientes y verla en el menú.',
            'puede_crear': 'Mostrar el botón Nuevo Cliente y guardar el alta.',
            'puede_editar': 'Mostrar Editar en cada fila y guardar los cambios del cliente.',
            'puede_eliminar': 'Mostrar Eliminar en cada fila y borrar el cliente.',
            'puede_exportar': 'Mostrar el botón Exportar (Excel de clientes).',
        },
        'depende_de': [],
        'notas': 'Los cuatro permisos finos solo ocultan botones: el servidor exige puede_ver en las URLs de '
                 'crear, editar, eliminar, activar y exportar.',
    },
    'gestion_vendedores': {
        'pantalla': 'Gestión Vendedores',
        'ruta': '/app/gestion_vendedores/',
        'resumen': 'Equipo de ventas por sucursal y sus porcentajes de comisión.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión Vendedores y verla en el menú.',
            'puede_crear': 'Mostrar Nuevo Vendedor y crear vendedores.',
            'puede_editar': 'Mostrar Editar en cada fila y modificar datos y porcentaje de comisión.',
            'puede_eliminar': 'Mostrar Eliminar en cada fila y borrar el vendedor.',
            'puede_exportar': 'Mostrar Exportar y descargar el CSV de vendedores.',
        },
        'depende_de': [],
        'notas': 'Los cuatro se revisan también en el servidor. El listado de vendedores (/app/obtener_vendedores/) '
                 'queda abierto porque lo usa el Dashboard Ventas.',
    },
    'gestion_permisos': {
        'pantalla': 'Gestión Permisos',
        'ruta': '/app/permisos/gestion/',
        'resumen': 'Permisos por rol, por sucursal y por usuario, con copia, importación/exportación y diagnóstico.',
        'permisos': {
            'puede_ver': 'Mostrar Gestión Permisos en el menú y entrar a la pantalla y a todas sus acciones (/app/permisos/...).',
        },
        'depende_de': [],
        'notas': 'Además exige rol Administrador o Maestro en la pantalla y en cada acción (guardar, copiar, '
                 'importar/exportar). El rol Maestro no se configura.',
    },
    'interfaz_acepta': {
        'pantalla': 'Interfaz Prueba Acepta',
        'ruta': '/app/configuracion/interfaz-prueba-acepta/',
        'resumen': 'Generador de prueba del archivo TXT que se envía a Acepta (SII).',
        'permisos': {
            'puede_ver': 'Entrar a Interfaz Prueba Acepta, verla en el menú y generar el TXT de prueba.',
        },
        'depende_de': [],
        'notas': '',
    },
    'integraciones_ecommerce': {
        'pantalla': 'Integraciones Ecommerce',
        'ruta': '/app/configuracion/integraciones-ecommerce/',
        'resumen': 'Credenciales de las tiendas web que proveen fotos de portada y su estado de sincronización.',
        'permisos': {
            'puede_ver': 'Mostrar "Integraciones Ecommerce" en el menú.',
        },
        'depende_de': [],
        'notas': 'La pantalla y sus acciones (guardar, eliminar, probar, sincronizar, verificar) exigen rol '
                 'Administrador, Jefe de Local o Maestro, no este permiso: sin el check igual se entra por URL.',
    },

    # ------------------------------------------------------------------ Ecommerce
    'ecommerce_pedidos_pendientes': {
        'pantalla': 'Pendientes de Facturar',
        'ruta': '',
        'resumen': 'Opción heredada del menú Ecommerce; hoy los pedidos son una sola pantalla con filtro por estado.',
        'permisos': {},
        'depende_de': [],
        'notas': 'Hoy ninguna pantalla revisa este permiso. Ojo: es opción raíz del módulo Ecommerce, así que con '
                 'puede_ver activo el menú muestra la cabecera "Ecommerce" aunque quede sin entradas.',
    },
    'ecommerce_pedidos_facturados': {
        'pantalla': 'Facturados',
        'ruta': '',
        'resumen': 'Opción heredada del menú Ecommerce; hoy los pedidos son una sola pantalla con filtro por estado.',
        'permisos': {},
        'depende_de': [],
        'notas': 'Hoy ninguna pantalla revisa este permiso. Ojo: es opción raíz del módulo Ecommerce, así que con '
                 'puede_ver activo el menú muestra la cabecera "Ecommerce" aunque quede sin entradas.',
    },
    'ecommerce_pedidos_todos': {
        'pantalla': 'Pedidos Ecommerce',
        'ruta': '/app/ecommerce/pedidos/',
        'resumen': 'Pedidos de las tiendas web: match de SKU, preparación, guías, facturación y cancelación.',
        'permisos': {
            'puede_ver': 'Entrar a Pedidos, verlos en el menú, abrir detalle e historial, exportar CSV y bajar TXT/ZIP de boletas.',
            'puede_crear': 'Usar "Traer pedidos", Facturar (individual y masivo) y Vincular a un ticket existente.',
            'puede_editar': 'Cambiar SKU, avanzar etapa, fijar medio de pago, imprimir guías, marcar/reactivar Sin stock y Reasignar.',
            'puede_eliminar': 'Mostrar la Zona de riesgo y Cancelar el pedido.',
        },
        'depende_de': [],
        'notas': 'Exportar CSV, Distribuir y Sugerir sucursal solo exigen puede_ver (todo cuelga de '
                 '/app/ecommerce/pedidos/). El dashboard de asignación (/app/ecommerce/dashboard-asignacion/) '
                 'no revisa permiso.',
    },
    'retiro_pedido_local': {
        'pantalla': 'Retiro pedido local',
        'ruta': '/app/ecommerce/retiro-local/',
        'resumen': 'Mesón de entrega de pedidos web retirados en tienda: escanear código, validar y confirmar.',
        'permisos': {
            'puede_ver': 'Entrar al mesón, verlo en el menú, escanear y validar el código y descargar el comprobante PDF.',
            'puede_crear': 'Confirmar el retiro (crea el acta en AllConnected y marca el código como usado).',
        },
        'depende_de': [],
        'notas': 'Solo desde la sucursal PAO1: en otra sucursal el menú lo oculta y la URL se bloquea aunque el '
                 'permiso esté activo.',
    },

    # --------------------------------------------------------------- Fidelización
    'giftcards_listado': {
        'pantalla': 'Gift Cards',
        'ruta': '/app/giftcards/',
        'resumen': 'Listado, detalle y trazabilidad de gift cards, con exportación a Excel.',
        'permisos': {
            'puede_ver': 'Entrar a Gift Cards y Trazabilidad, verlas en el menú, ver detalle y reporte y exportar a Excel.',
        },
        'depende_de': [],
        'notas': 'Exportar va con puede_ver, no con puede_exportar. Consultar saldo y validar en caja solo piden '
                 'sesión; con este permiso además muestran el correo del titular y el estado de envío.',
    },
    'giftcards_emitir': {
        'pantalla': 'Emitir Gift Card',
        'ruta': '/app/giftcards/emitir/',
        'resumen': 'Acciones sobre gift cards desde el listado: emitir, recargar, enviar, anular, bloquear y editar.',
        'permisos': {
            'puede_ver': 'Mostrar "Emitir Gift Card" en el menú y abrir /app/giftcards/emitir/ (lleva al listado con el modal).',
            'puede_crear': 'Mostrar y usar Emitir Gift Card, Recargar saldo y Enviar código por correo.',
            'puede_editar': 'Anular, Bloquear/Desbloquear, Editar datos o vencimiento, cambiar ámbito de empresa y Confirmar entrega.',
        },
        'depende_de': ['giftcards_listado'],
        'notas': 'La ruta de emitir exige puede_ver (menú) y puede_crear (vista). Los botones viven en el listado, '
                 'que pide giftcards_listado.puede_ver.',
    },
    'fidelizacion_cuentas': {
        'pantalla': 'Clientes y Puntos',
        'ruta': '/app/fidelizacion/',
        'resumen': 'Cuentas de puntos: listado de clientes, ficha con saldo e historial, altas y ajustes manuales.',
        'permisos': {
            'puede_ver': 'Entrar a Clientes y Puntos, verla en el menú, abrir la ficha de cada cliente y consultar saldo por RUT.',
            'puede_crear': 'Mostrar y usar "Registrar cliente" (alta manual con cuenta de puntos).',
            'puede_editar': 'Mostrar y usar Ajuste manual de puntos y Bono cumpleaños en la ficha; emitir vales de canje.',
        },
        'depende_de': [],
        'notas': 'Consultar saldo, validar vale y generar vale de canje también los puede hacer quien tiene '
                 'Ticket de Venta (caja), sin este permiso.',
    },
    'fidelizacion_programa': {
        'pantalla': 'Configuración Programa',
        'ruta': '/app/fidelizacion/configuracion/',
        'resumen': 'Parámetros del programa de puntos: tasa, mínimo de canje, vencimiento y bonos.',
        'permisos': {
            'puede_ver': 'Entrar a Configuración Programa, verla en el menú y simular el impacto de los parámetros.',
            'puede_editar': 'Guardar la configuración del programa de puntos.',
        },
        'depende_de': [],
        'notas': 'El botón Guardar se muestra siempre; sin puede_editar el servidor responde 403.',
    },
    'fidelizacion_reporte': {
        'pantalla': 'Reporte Fidelización',
        'ruta': '/app/fidelizacion/reporte/',
        'resumen': 'Puntos acumulados, canjes, vencimientos, ranking y señales de abuso por periodo.',
        'permisos': {
            'puede_ver': 'Entrar a Reporte Fidelización, verlo en el menú y cargar sus datos.',
        },
        'depende_de': [],
        'notas': '',
    },
    'fidelizacion_cupones': {
        'pantalla': 'Códigos de Descuento',
        'ruta': '/app/fidelizacion/cupones/',
        'resumen': 'Campañas de cupones y cupones emitidos a clientes; validación en caja.',
        'permisos': {
            'puede_ver': 'Entrar a Códigos de Descuento, verla en el menú, listar campañas y cupones y buscar clientes.',
            'puede_crear': 'Mostrar y usar Nueva campaña, Editar campaña, Emitir cupón (uno o en lote) y copiar código público.',
            'puede_editar': 'Activar o desactivar una campaña.',
            'puede_eliminar': 'Mostrar y usar Anular en cupones pendientes.',
        },
        'depende_de': [],
        'notas': 'Editar campaña usa el mismo guardado que crear (puede_crear). El botón Activar/Desactivar solo '
                 'aparece con puede_crear y el servidor exige puede_editar: hacen falta ambos. Validar cupón en '
                 'caja también lo permite Ticket de Venta.',
    },

    # ------------------------------------------------------------------ Mi Cuenta
    'mi_perfil': {
        'pantalla': 'Mi Perfil',
        'ruta': '/users/mi-perfil/',
        'resumen': 'Datos personales, foto, clave, PIN de autorización y sesiones activas del usuario.',
        'permisos': {
            'puede_ver': 'Entrar a Mi Perfil (/users/mi-perfil/).',
        },
        'depende_de': [],
        'notas': 'El enlace "Mi Perfil" del menú de usuario se muestra siempre. Cambiar clave, PIN, foto y '
                 'sesiones tienen URL propia y no revisan este permiso.',
    },
    'ajuste_stock_rapido': {
        'pantalla': 'Ajuste de Stock',
        'ruta': '/app/ajuste-stock-rapido/',
        'resumen': 'Corrección rápida de stock por SKU y concepto en la sucursal activa (web y app móvil).',
        'permisos': {
            'puede_ver': 'Mostrar "Ajuste de Stock" en el menú de usuario, entrar, consultar el SKU y registrar el ajuste.',
        },
        'depende_de': [],
        'notas': 'Crear y editar no se revisan: quien entra puede ajustar (también desde la app móvil). Si la opción '
                 'no existe en la base, la app móvil deja pasar a Administrador y Jefe de Local.',
    },
    'cambiar_empresa': {
        'pantalla': 'Cambiar Empresa/Sucursal',
        'ruta': '/app/cambiar-empresa/',
        'resumen': 'Elegir la empresa y sucursal donde se trabaja (pantalla y selector rápido del encabezado).',
        'permisos': {
            'puede_ver': 'Mostrar "Cambiar Empresa/Sucursal" en menú y perfil, entrar y cambiar la sucursal activa.',
        },
        'depende_de': [],
        'notas': 'Administrador y Maestro siempre pueden cambiar, tengan o no el permiso. Sin él la pantalla '
                 'queda en solo lectura y el cambio responde error.',
    },
}
