# -*- coding: utf-8 -*-
"""Catálogo de permisos — módulo Documentos. Ver app/permisos_catalogo/__init__.py."""

CATALOGO = {
    'emision_dte': {
        'pantalla': 'Emisión DTE',
        'ruta': '/app/emisionDTE/',
        'resumen': 'Emitir facturas, boletas, guías y notas de crédito con mercadería, y documentos por concepto (sin stock).',
        'permisos': {
            'puede_ver': 'Entrar a Emisión DTE y Emisión por Concepto, verlas en el menú y emitir documentos; también descargar el TXT Acepta.',
        },
        'depende_de': ['emitir_nota_credito', 'emitir_nota_credito_traspaso', 'dte_descargar_txt'],
        'notas': (
            'El mapa de URLs cubre la página, el envío (emitir_dte / emitir_dte_concepto) y el alta rápida de empresa del wizard. '
            'Emitir una Nota de Crédito exige además el permiso de NC (clientes; de traspaso si el despacho es interno).'
        ),
    },

    'gestion_dte': {
        'pantalla': 'Gestión DTE',
        'ruta': '/app/documentos/gestion-dte/',
        'resumen': 'Revisar los documentos de venta emitidos: pagos, notas de crédito, folios, TXT Acepta y traspasos.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión DTE y verla en el menú (solo la página: cada acción se rige por los permisos de abajo).',
        },
        'depende_de': [
            'emitir_nota_credito', 'emitir_nota_credito_traspaso', 'dte_descargar_txt',
            'dte_eliminar_documento', 'dte_editar_folio', 'recepcion_dte',
        ],
        'notas': (
            'Botón NC → permiso de NC (clientes o de traspaso según el documento); TXT → dte_descargar_txt; '
            'Eliminar (boleta de papel) → dte_eliminar_documento; Folio → dte_editar_folio; '
            'Reasignar destino y Stock dest. de un traspaso → recepcion_dte / puede_aprobar. '
            'Los botones NC, Folio, Receptor y Diagnóst. solo se muestran a administrador / administración.'
        ),
    },

    'recepcion_dte': {
        'pantalla': 'Recepción Documentos',
        'ruta': '/app/recepcion-dte/',
        'resumen': 'Recibir traspasos internos, resolver faltantes y sobrantes, y ajustar los documentos emitidos a otras tiendas.',
        'permisos': {
            'puede_ver': 'Entrar a Recepción Documentos y DTEs en Limbo, verlas en el menú y consultar listados, solicitudes y problemas.',
            'puede_crear': 'Recepcionar en la pestaña Por recibir (botón Recepcionar, total o con problemas) y confirmar la devolución física al origen.',
            'puede_aprobar': 'Usar Por resolver y Emitidos: Rechazar Recepción, Llegó todo, Cancelar, Ajustar, Cambiar talla, decidir solicitudes y sobrantes.',
            'puede_exportar': 'Descargar el PDF de la tabla Por resolver (botón PDF).',
        },
        'depende_de': ['emitir_nota_credito_traspaso'],
        'notas': (
            'puede_aprobar también cubre Resolver en DTEs en Limbo, Reparar / Diagnóstico, Reasignar destino y Stock dest. de Gestión DTE. '
            'Emitir NC, 1 NC por todo, Enviar cambio y Ajustar de una factura/boleta exigen además emitir_nota_credito_traspaso. '
            'La plantilla de sucursal VENDEDORA deshabilita este permiso.'
        ),
    },

    'gestion_cotizaciones': {
        'pantalla': 'Gestión Cotizaciones',
        'ruta': '/app/cotizaciones/',
        'resumen': 'Crear cotizaciones a empresas, facturarlas, despachar por SKU y dar el OK final al despacho.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión Cotizaciones, verla en el menú y usar Nueva Cotización, Anular, Facturar, Reabrir y asignar/revertir SKU.',
            'puede_aprobar': 'Dar el OK al despacho (botón verde de la fila): confirma que lo facturado coincide con lo despachado.',
        },
        'depende_de': ['dte_descargar_txt'],
        'notas': (
            'Reabrir una cotización facturada exige además rol administrador. Descargar el TXT de la guía emitida pasa por '
            'dte_descargar_txt o emision_dte. Editar, emitir/anular guía y cerrar pendiente solo piden estar logueado. '
            'Cargar como ticket (POS) queda fuera a propósito.'
        ),
    },

    'gestion_correlativos': {
        'pantalla': 'Gestión Correlativos',
        'ruta': '/app/documentos/gestion-correlativos/',
        'resumen': 'Administrar los rangos de folios (correlativos) por sucursal y tipo de documento.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión Correlativos y verla en el menú.',
        },
        'notas': 'Guardar, Renovar, Eliminar, Crear faltantes y Exportar PDF solo exigen estar logueado: ningún otro flag los controla hoy.',
    },

    'gestion_creditos': {
        'pantalla': 'Gestión Créditos',
        'ruta': '/app/documentos/gestion-creditos/',
        'resumen': 'Otorgar y cobrar créditos a trabajadores: solicitud, aprobación, activación, pagos y vouchers.',
        'permisos': {
            'puede_ver': 'Entrar a Gestión Créditos (menú y /app/creditos/) y usar Aprobar, Rechazar, Activar, Ajustar monto, Registrar pago, Firma y Voucher.',
            'puede_crear': 'Crear un crédito con Nuevo Crédito (queda Pendiente si no puede aprobar).',
            'puede_aprobar': 'Dejar el crédito ACTIVO de inmediato al crearlo con Nuevo Crédito, sin pasar por Pendiente.',
        },
        'notas': (
            'Los botones Aprobar, Activar y Rechazar NO revisan puede_aprobar: basta ver la pantalla y alcanzar la sucursal del crédito. '
            'Validar código y Usar en venta (POS) quedan fuera a propósito.'
        ),
    },

    'dte_descargar_txt': {
        'pantalla': 'Descargar TXT Acepta de DTE',
        'ruta': '',
        'resumen': 'Descargar el archivo TXT Acepta de un documento ya emitido.',
        'permisos': {
            'puede_ver': 'Ver y usar el botón TXT de Gestión DTE y descargar el TXT Acepta de cualquier documento emitido.',
        },
        'notas': (
            'Quien tiene emision_dte también puede descargar: la descarga automática tras emitir y el TXT de la guía en '
            'Cotizaciones usan la misma puerta.'
        ),
    },

    'dineros_mercadopago': {
        'pantalla': 'Conciliación Mercado Pago',
        'ruta': '/app/ventas/dineros-mercadopago/',
        'resumen': 'Cruzar los cobros de Mercado Pago con ventas, documentos, liberaciones, retiros al banco y cartola.',
        'permisos': {
            'puede_ver': 'Entrar a Conciliación Mercado Pago: Cobros y documentos, Liberaciones y banco, Contra Mercado Pago y Por sucursal.',
        },
        'depende_de': ['asociar_pagos_mercadopago'],
        'notas': (
            'Cubre la página y sus APIs (/api/mercadopago/dineros/ y /conciliacion/...). Asignación de retiros, pedir liberaciones, '
            'cartola y devolver exigen además rol administrador / administración. La pestaña Asociar depende de asociar_pagos_mercadopago. '
            'La alerta MP de Cuadratura de caja muestra el link a esta pantalla solo con este permiso.'
        ),
    },

    'emitir_nota_credito': {
        'pantalla': 'Emitir Nota de Crédito (clientes)',
        'ruta': '',
        'resumen': 'Permiso fino para emitir notas de crédito a clientes; se exige además del permiso de cada pantalla.',
        'permisos': {
            'puede_crear': 'Emitir NC a clientes: botón NC de Gestión DTE, NC en Emisión DTE y por Concepto, Generar NC en Cambios, Aprobar en Garantía.',
        },
        'notas': (
            'Nunca amplía acceso: exige también el permiso de la pantalla (gestion_dte, emision_dte, cambios_devoluciones, '
            'devolucion_garantia). Generar NC en Cambios pide además rol administrador / administración / jefe_local. '
            'En Garantía el botón es «Aprobar y generar NC».'
        ),
    },

    'emitir_nota_credito_traspaso': {
        'pantalla': 'Emitir NC de traspasos internos (recepción)',
        'ruta': '',
        'resumen': 'Permiso fino para emitir notas de crédito sobre traspasos entre empresas del grupo.',
        'permisos': {
            'puede_crear': 'Emitir NC de traspaso: Emitir NC, 1 NC por todo, Enviar cambio y Ajustar factura/boleta (Recepción, Limbo) y NC en Gestión DTE.',
        },
        'notas': (
            'Se exige además de recepcion_dte / puede_aprobar o de gestion_dte. Las guías no llevan NC: Ajustar una guía no lo necesita. '
            'También lo pide una NC desde Emisión DTE con despacho interno.'
        ),
    },

    'asociar_pagos_mercadopago': {
        'pantalla': 'Asociar pagos Mercado Pago',
        'ruta': '',
        'resumen': 'Pestaña Asociar de Conciliación Mercado Pago: amarrar cobros de MP sin venta a su ticket.',
        'permisos': {
            'puede_ver': 'Ver la pestaña Asociar de Conciliación MP con sus listados de cobros sin venta y pagos manuales sin respaldo.',
            'puede_editar': 'Usar Asociar, Verificar, Corregir N°, Buscar pagos del día en MP y Asignar (uno o todos) en Asociar y Contra Mercado Pago.',
        },
        'depende_de': ['dineros_mercadopago'],
        'notas': (
            'Sin dineros_mercadopago no sirve: sus APIs exigen ambos. Administrador / administración asignan en todas las tiendas; '
            'el resto solo en la tienda de su sesión y nunca convierte efectivo o transferencia a MP. '
            'Cuadratura de caja avisa quién puede asociar.'
        ),
    },

    'dte_eliminar_documento': {
        'pantalla': 'Eliminar / anular documento de venta',
        'ruta': '',
        'resumen': 'Eliminar (descartar) o anular boletas, facturas y NC de venta, devolviendo stock y sacándolas de la cuadratura.',
        'permisos': {
            'puede_eliminar': 'Usar Eliminar en Consulta Documentos, en el detalle del Resumen de Cuadratura de caja y sobre boletas de papel en Gestión DTE.',
        },
        'notas': (
            'Cubre también anular un documento de venta y el Eliminar DTE de Gestión Documentos Compras cuando el documento no es de compra. '
            'Por defecto solo el Maestro (comando configurar_rol_maestro).'
        ),
    },

    'dte_compras_pagos': {
        'pantalla': 'Editar / eliminar pagos de documentos de compra',
        'ruta': '',
        'resumen': 'Acciones sobre los pagos ya registrados de un documento de compra en Gestión Documentos Compras.',
        'permisos': {
            'puede_editar': 'Editar un pago existente (lápiz) y agregar una Nota de Crédito como pago en Gestión Documentos Compras.',
            'puede_eliminar': 'Eliminar un pago o una NC registrada como pago (papelera) en Gestión Documentos Compras.',
        },
        'depende_de': ['gestion_dte_compras'],
        'notas': 'Registrar un pago nuevo con Pagar no depende de esto. Por defecto solo el Maestro.',
    },

    'dte_compras_eliminar': {
        'pantalla': 'Eliminar documento de compra',
        'ruta': '',
        'resumen': 'Eliminar (descartar) un documento de compra desde Gestión Documentos Compras.',
        'permisos': {
            'puede_eliminar': 'Usar Eliminar DTE y Eliminar NC del menú de acciones de Gestión Documentos Compras.',
        },
        'depende_de': ['gestion_dte_compras'],
        'notas': 'Por defecto solo el Maestro. Si el documento no es de compra, el mismo botón exige dte_eliminar_documento.',
    },

    'dte_editar_folio': {
        'pantalla': 'Editar folio de DTE',
        'ruta': '',
        'resumen': 'Cambiar el folio (y receptor) de una nota de crédito emitida, dejando historial del cambio.',
        'permisos': {
            'puede_editar': 'Guardar un folio nuevo con el botón Folio (Editar folio / cliente NC) de Gestión DTE.',
        },
        'depende_de': ['gestion_dte'],
        'notas': 'El botón Folio solo se muestra a administrador / administración; este permiso decide si el cambio se guarda.',
    },

    'dte_editar_vendedor': {
        'pantalla': 'Editar vendedor de DTE',
        'ruta': '',
        'resumen': 'Cambiar el vendedor asignado a un documento de venta desde Consulta Documentos.',
        'permisos': {
            'puede_editar': 'Cambiar el campo Vendedor en Editar de Consulta Documentos (requiere además el permiso del tipo de documento).',
        },
        'depende_de': ['gestion_documentos_ventas'],
        'notas': (
            'Regla AND: dte_editar_vendedor y dte_editar_tipo_<boleta/factura...> deben tener puede_editar. '
            'Sin permiso el selector queda bloqueado con un candado.'
        ),
    },

    'devolver_mercadopago': {
        'pantalla': 'Devolver a la tarjeta por Mercado Pago',
        'ruta': '',
        'resumen': 'Acción dentro de la NC de Gestión DTE: devolver la plata a la tarjeta del cliente por la API de Mercado Pago.',
        'permisos': {
            'puede_crear': 'Elegir «Devolución a la tarjeta (Mercado Pago)» al emitir la NC: mueve plata real de la cuenta MP.',
        },
        'depende_de': ['emitir_nota_credito', 'gestion_dte'],
        'notas': 'Sin este permiso la NC igual se puede emitir con devolución en efectivo, transferencia o sin afectar caja. '
                 'Antes lo decidía el rol (administrador/administración).',
    },
}
