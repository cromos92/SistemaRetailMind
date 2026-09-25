# -*- coding: utf-8 -*-
"""Catálogo de permisos — módulo Ventas. Ver app/permisos_catalogo/__init__.py."""

_TIPOS_DTE = [
    'dte_editar_tipo_boleta_electronica', 'dte_editar_tipo_boleta_papel',
    'dte_editar_tipo_factura_electronica', 'dte_editar_tipo_factura_exenta',
]
_CAMPOS_DTE = ['dte_editar_fecha', 'dte_editar_numero', 'dte_editar_pago', 'dte_editar_vendedor']

CATALOGO = {
    'ticket_venta': {
        'pantalla': 'Ticket de Venta',
        'ruta': '/app/ticket-venta/',
        'resumen': 'Pantalla clásica de venta al público por ticket, más las consultas de puntos, vales y cupones que usa la caja.',
        'permisos': {
            'puede_ver': 'Entrar a Ticket de Venta (menú y URL) y, al cobrar, consultar los puntos del cliente por RUT y validar un vale de canje.',
            'puede_crear': 'Validar un cupón de descuento y generar un vale de canje con puntos desde la caja (Ticket de Venta y Generar Venta).',
        },
        'depende_de': [],
        'notas': 'Crear el ticket y cobrarlo no revisan este permiso (basta estar conectado). Puntos, vales y cupones también los autoriza '
                 'Fidelización (Clientes y Puntos / Cupones): basta con uno de los dos.',
    },

    'cambios_devoluciones': {
        'pantalla': 'Cambios y Devoluciones',
        'ruta': '/app/ventas/cambios-devoluciones/',
        'resumen': 'Solicitar, aprobar, ejecutar, cobrar o devolver la diferencia y revisar cambios y devoluciones de productos vendidos.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla (menú, accesos del Home y URL) con su historial, análisis y exportación.',
            'puede_eliminar': 'Botones «Cancelar» y «Revertir» de un cambio: pedir la acción y ejecutarla con el código de un administrador (30 min).',
        },
        'depende_de': ['emitir_nota_credito'],
        'notas': 'Administrador y jefe de local tienen «Cancelar/Revertir» sin este casillero (el administrador sin código). Crear no revisa permisos; '
                 'aprobar pide código de supervisor y, fuera de plazo, el PIN de un administrador; «Generar NC» exige Nota de Crédito.',
    },

    'devolucion_garantia': {
        'pantalla': 'Devolución por Garantía',
        'ruta': '/app/devolucion-garantia/',
        'resumen': 'Solicitudes de devolución de dinero por garantía: la tienda las crea y un aprobador las aprueba (emite la NC) o rechaza.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla y al detalle, buscar el documento, ver el listado, imprimir el comprobante y anular una solicitud propia.',
            'puede_crear': 'Botón «Nueva Solicitud»: registrar la solicitud de devolución (queda pendiente, sin NC).',
            'puede_aprobar': 'Modal «Analizar solicitud»: ver detalle e impacto en caja, «Rechazar» y «Aprobar y generar NC».',
        },
        'depende_de': [],
        'notas': 'La NC de la garantía la autoriza «Aprobar» por sí solo (no exige el permiso de NC a clientes). '
                 'Solo administrador/administración ven solicitudes de otras sucursales.',
    },

    'pos_dashboard': {
        'pantalla': 'Generar Venta (POS)',
        'ruta': '/app/pos-dashboard/',
        'resumen': 'Punto de venta: armar el ticket, aplicar descuentos, cobrar (efectivo, tarjeta, Mercado Pago, crédito, gift card) y emitir el DTE.',
        'permisos': {
            'puede_ver': 'Entrar al POS (menú «Generar Venta», accesos del Home y URL).',
        },
        'depende_de': ['ticket_venta'],
        'notas': 'El descuento máximo se fija por rol en Permisos (Límite de descuento) y sobre eso se pide código de supervisor. Puntos, vales y '
                 'cupones en caja usan Ticket de Venta. Cobrar y cerrar ventas no revisa Crear ni Editar: basta entrar al POS.',
    },

    'gestion_documentos_ventas': {
        'pantalla': 'Consulta Documentos',
        'ruta': '/app/ventas/documentos/',
        'resumen': 'Buscar boletas, facturas y tickets emitidos, ver su detalle, imprimir, exportar y corregir o eliminar documentos.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla (aparece en Ventas y en Documentos), buscar, ver detalle, imprimir y exportar el listado.',
        },
        'depende_de': _CAMPOS_DTE + _TIPOS_DTE + ['dte_eliminar_documento'],
        'notas': 'Los botones «Editar» y «Eliminar» se rigen por los permisos de edición de DTE y por Eliminar documento; «DTE manual» es solo '
                 'para administrador (por rol). Solo se protege la página: sus acciones se validan una a una.',
    },

    'cuadratura_caja': {
        'pantalla': 'Cuadratura y Arqueo',
        'ruta': '/app/ventas/cuadratura-caja/',
        'resumen': 'Cuadrar la caja del día: Resumen de Caja, arqueo con conteo físico, declaración de depósitos y cierre.',
        'permisos': {
            'puede_ver': 'Entrar a la pantalla (menú, Home y URL) y consultar el Resumen de Caja y los arqueos de la sucursal.',
        },
        'depende_de': ['dte_editar_fecha', 'dte_editar_pago', 'dte_eliminar_documento',
                       'dineros_mercadopago', 'asociar_pagos_mercadopago'],
        'notas': 'Las acciones van por rol, no por casilleros: crear arqueo dentro del «Rango de arqueo» configurable por rol en Permisos; declarar '
                 'depósito (cajero, vendedor, jefe); confirmar depósito, «Actualizar teórico» y eliminar arqueo (administrador/administración); '
                 '«Reabrir» (administrador; jefe/administración con tolerancia); «Corregir Express» (administrador). En el Resumen de Caja, '
                 '«Editar fecha», «Sync», «Mover fecha» y «Eliminar» usan los permisos de edición de DTE.',
    },

    'pos_transbank': {
        'pantalla': 'Mercado Pago y Transbank',
        'ruta': '/app/pos/transbank/',
        'resumen': 'Estado de la caja con Transbank y Mercado Pago: conexión del terminal, cobro directo, cierre del día, modo de cobro y máquinas.',
        'permisos': {
            'puede_ver': 'Mostrar «Mercado Pago y Transbank» en el menú y abrir las pantallas antiguas de POS (WebSocket y manual) y «Detectar terminales».',
        },
        'depende_de': [],
        'notas': 'La página que abre el menú (/app/pos/transbank/) NO está protegida: cualquier usuario conectado la abre por URL. Dentro, credenciales, '
                 'cuentas, máquinas y modo de cobro son solo administrador/administración (por rol). Cobrar con tarjeta desde el POS, «Cobro directo» '
                 'e «Imprimir cierre» no exigen este permiso.',
    },

    'revision_arqueos': {
        'pantalla': 'Revisión Arqueos y Depósitos',
        'ruta': '/app/ventas/revision-arqueos/',
        'resumen': 'Supervisión de los arqueos de caja: aprobarlos u observarlos, confirmar depósitos y corregir depósitos ya declarados.',
        'permisos': {
            'puede_ver': 'Mostrar la opción en el menú y el Home. La pantalla exige además rol administrador o administración; otros vuelven a Cuadratura.',
            'puede_editar': 'Botón «Cambiar fecha» de un depósito en el detalle del arqueo.',
            'puede_eliminar': 'Botón «Eliminar depósito» en el detalle del arqueo, incluso si ya está verificado o lo declaró otra persona.',
        },
        'depende_de': [],
        'notas': '«Aprobar OK / con observaciones / Requiere acción», confirmar y verificar depósitos y «Actualizar teórico» se autorizan por rol '
                 '(administrador/administración), no por estos casilleros. Sin Eliminar, solo se puede borrar el depósito propio aún no verificado.',
    },

    'dte_editar_fecha': {
        'pantalla': 'Editar Fecha de DTE',
        'ruta': '',
        'resumen': 'Acción dentro de Consulta Documentos y Cuadratura: cambiar la fecha de emisión de un documento de venta o mover un ticket de día.',
        'permisos': {
            'puede_editar': 'Campo «Fecha» del modal Editar (Consulta Documentos) y botones «Editar fecha», «Sync» y «Mover fecha» del Resumen de Caja.',
        },
        'depende_de': _TIPOS_DTE,
        'notas': 'Se exige junto con el permiso del tipo de documento (Editar Boleta Electrónica, Boleta Papel, Factura Electrónica o Exenta); '
                 'solo «Mover fecha» de tickets sin DTE usa este permiso a secas. Por defecto, solo el Maestro.',
    },

    'dte_editar_numero': {
        'pantalla': 'Editar N° Documento DTE',
        'ruta': '',
        'resumen': 'Acción dentro de Consulta Documentos: corregir el número (folio) de una boleta o factura ya emitida.',
        'permisos': {
            'puede_editar': 'Campo «N° documento» del modal Editar en Consulta Documentos (se guarda solo si también puede editar ese tipo de DTE).',
        },
        'depende_de': _TIPOS_DTE,
        'notas': 'Se exige junto con el permiso del tipo de documento. Por defecto, solo el Maestro. El folio en Gestión DTE usa otro permiso (Editar folio).',
    },

    'dte_editar_pago': {
        'pantalla': 'Editar Pagos de DTE',
        'ruta': '',
        'resumen': 'Acción dentro de Consulta Documentos y Cuadratura: corregir los medios de pago de un documento o el día de caja de una nota de crédito.',
        'permisos': {
            'puede_editar': 'Sección «Pagos» (método y monto) del modal Editar en Consulta Documentos y «Editar fecha» de una NC en el Resumen de Caja.',
        },
        'depende_de': _TIPOS_DTE,
        'notas': 'Para boletas y facturas se exige junto con el permiso del tipo de documento; la fecha de caja de una NC pide solo este permiso. '
                 'Por defecto, solo el Maestro.',
    },

    'dte_editar_tipo_boleta_electronica': {
        'pantalla': 'Editar Boleta Electrónica',
        'ruta': '',
        'resumen': 'Autoriza modificar boletas electrónicas desde Consulta Documentos y Cuadratura, junto con el permiso del campo que se edita.',
        'permisos': {
            'puede_editar': 'Habilita «Editar» (fecha, N°, pagos, vendedor) en boletas electrónicas; cambiar a papel exige además Editar Boleta Papel y Editar N°.',
        },
        'depende_de': _CAMPOS_DTE + ['dte_editar_tipo_boleta_papel'],
        'notas': 'Sin este permiso ninguna boleta electrónica muestra «Editar», aunque el rol tenga los permisos por campo. Por defecto, solo el Maestro.',
    },

    'dte_editar_tipo_boleta_papel': {
        'pantalla': 'Editar Boleta Papel',
        'ruta': '',
        'resumen': 'Autoriza modificar boletas de papel (talonario) desde Consulta Documentos y Cuadratura, junto con el permiso del campo que se edita.',
        'permisos': {
            'puede_editar': 'Habilita «Editar» (fecha, N°, pagos, vendedor) en boletas de papel y, con Editar Boleta Electrónica, cambiar el tipo entre ambas.',
        },
        'depende_de': _CAMPOS_DTE + ['dte_editar_tipo_boleta_electronica'],
        'notas': 'Sin este permiso ninguna boleta de papel muestra «Editar», aunque el rol tenga los permisos por campo. Por defecto, solo el Maestro.',
    },

    'dte_editar_tipo_factura_electronica': {
        'pantalla': 'Editar Factura Electrónica',
        'ruta': '',
        'resumen': 'Autoriza modificar facturas electrónicas desde Consulta Documentos y Cuadratura, y convertir un ticket cobrado en factura.',
        'permisos': {
            'puede_editar': 'Habilita «Editar» (fecha, N°, pagos, vendedor) en facturas electrónicas y autoriza «Convertir a factura» un ticket ya cobrado.',
        },
        'depende_de': _CAMPOS_DTE,
        'notas': 'Convertir a factura hoy no tiene botón en Consulta Documentos (solo la acción de servidor). Sin este permiso ninguna factura '
                 'electrónica muestra «Editar». Por defecto, solo el Maestro.',
    },

    'dte_editar_tipo_factura_exenta': {
        'pantalla': 'Editar Factura Exenta',
        'ruta': '',
        'resumen': 'Autoriza modificar facturas exentas desde Consulta Documentos y Cuadratura, junto con el permiso del campo que se edita.',
        'permisos': {
            'puede_editar': 'Habilita «Editar» (fecha, N°, pagos, vendedor) en facturas exentas dentro de Consulta Documentos y el Resumen de Caja.',
        },
        'depende_de': _CAMPOS_DTE,
        'notas': 'Sin este permiso ninguna factura exenta muestra «Editar», aunque el rol tenga los permisos por campo. Por defecto, solo el Maestro.',
    },
}
