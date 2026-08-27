# -*- coding: utf-8 -*-
"""
Helpers compartidos del módulo ventas.

Centraliza lógica repetida en vistas de listado/POS:
- Obtención de sucursal desde sesión.
- Normalización de nombres de métodos de pago.
- Agrupación de pagos por método.
- Campos `only()` estandarizados para prefetch.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

logger = logging.getLogger('app')


# ========== COSTEO FIFO DE LA LÍNEA DE VENTA ==========

def persistir_costeo_fifo(linea_ticket, costo_total_consumido, lotes_utilizados) -> bool:
    """Guarda en la línea de venta qué lotes FIFO la abastecieron.

    `consumir_stock_fifo()` calcula el costo real y los lotes consumidos, pero ese
    dato solo existe durante el cobro: los lotes son fungibles y `cantidad_disponible`
    ya quedó decrementada, así que si no se persiste aquí se pierde de forma
    irrecuperable de qué lote —y por tanto de qué DTE de compra y a qué costo— salió
    la unidad vendida.

    `costo_fifo` se guarda UNITARIO porque los reportes de margen calculan
    `stock * costo_fifo` (ver views_modulo_reportes, views_modulo_compras y el
    resumen de márgenes de views_modulo_ventas).

    Nunca propaga excepciones: un fallo guardando trazabilidad no puede voltear un
    cobro ya materializado.
    """
    if linea_ticket is None:
        return False

    try:
        cantidad = int(getattr(linea_ticket, 'stock', 0) or 0)
        total = int(costo_total_consumido or 0)
        costo_unitario = int(round(total / cantidad)) if cantidad > 0 else 0

        linea_ticket.costo_fifo = costo_unitario
        linea_ticket.lotes_utilizados = json.dumps(
            lotes_utilizados or [], ensure_ascii=False, default=str
        )
        linea_ticket.save(update_fields=['costo_fifo', 'lotes_utilizados'])
        return True
    except Exception:
        logger.exception(
            "No se pudo persistir el costeo FIFO de la linea de ticket id=%s",
            getattr(linea_ticket, 'id', None),
        )
        return False


# ========== NOMBRES DE MÉTODOS DE PAGO ==========

NOMBRES_METODOS_PAGO = {
    'EFECTIVO': 'Efectivo',
    'TARJETA_DEBITO': 'Tarjeta Débito',
    'TARJETA_CREDITO': 'Tarjeta Crédito',
    'TRANSFERENCIA': 'Transferencia',
    'CHEQUE': 'Cheque',
    'OTRO': 'Otro',
    'TBK_POS_INTEGRADO': 'Transbank POS',
    'TBK_MANUAL': 'Transbank Manual',
    'TBK_DEBITO_POS': 'TBK Débito POS',
    'TBK_CREDITO_POS': 'TBK Crédito POS',
    'TBK_PREPAGO_POS': 'TBK Prepago POS',
    'TARJETA_COMERCIAL': 'Tarjeta Comercial',
    'VENTA_INTERNET': 'Venta por Internet',
    'ORDEN_COMPRA': 'Orden de Compra',
    'CREDITO_TRABAJADOR': 'Crédito Trabajador',
    'CREDITO_EXTERNO': 'Crédito Externo',
    'CONVENIO': 'Convenio',
}


def obtener_nombre_metodo_pago(codigo: str | None) -> str:
    """Convierte un código de método de pago en su nombre legible."""
    if not codigo:
        return ''
    return NOMBRES_METODOS_PAGO.get(codigo, codigo)


# ========== VENTA POR INTERNET ==========

def es_metodo_pago_internet(metodo: str | None) -> bool:
    """True si el método de pago marca una venta por internet.

    En BD conviven el código ('VENTA_INTERNET') y variantes legibles
    ('Venta por Internet' cuando un flujo copió el pago al DTE con
    get_metodo_pago_display(); 'Venta Internet' en data migrada de Laravel),
    según el flujo que creó el registro. Se normalizan todas.
    """
    normalizado = (metodo or '').strip().upper().replace(' POR ', ' ').replace(' ', '_')
    return normalizado == 'VENTA_INTERNET'


# Plataforma del pago internet (TicketDetallePago / Dte_Detalle_Pago.tipo_tarjeta,
# texto libre: 'Paris', 'Ripley', 'Falabella', 'Mercado Pago'…) → código de canal
# del contrato con AllConnected (CANAL_ECOMMERCE_CHOICES). Incluye alias y errores
# frecuentes; plataformas sin canal mapeado caen a 'OTRO'.
PLATAFORMA_PAGO_A_CANAL = {
    'PARIS': 'PARIS',
    'RIPLEY': 'RIPLEY',
    'WALMART': 'WALMART',
    'WALLMART': 'WALMART',
    'LIDER': 'WALMART',
    'SHOPIFY': 'SHOPIFY',
}


def canal_desde_plataforma_pago(plataforma: str | None) -> str | None:
    """Mapea la plataforma del pago internet a un código de canal ecommerce.

    Devuelve None si la plataforma viene vacía; 'OTRO' si no hay mapeo
    (Falabella, Mercado Pago, etc. — son venta internet pero no un canal
    que AllConnected administre).
    """
    clave = (
        (plataforma or '').strip().upper()
        .replace(' ', '').replace('-', '').replace('_', '').replace('.', '')
    )
    if not clave:
        return None
    return PLATAFORMA_PAGO_A_CANAL.get(clave, 'OTRO')


# ========== SUCURSAL ACTIVA ==========

def get_sucursal_id(request):
    """Obtiene el id de sucursal activa desde la sesión (acepta ambas claves legacy)."""
    return (
        request.session.get('idSucursalActual')
        or request.session.get('sucursalActual')
    )


# ========== AGRUPACIÓN DE PAGOS ==========

def agrupar_metodos_pago(pagos: Iterable[dict]) -> list[dict]:
    """
    Agrupa pagos por (metodo, metodo_display, tipo_tarjeta) sumando montos
    y concatenando vouchers/notas únicos.
    """
    agrupados: dict[tuple, dict] = {}
    for pago in pagos:
        metodo = pago.get('metodo') or ''
        metodo_display = pago.get('metodo_display') or metodo
        tipo_tarjeta = pago.get('tipo_tarjeta') or ''
        key = (metodo, metodo_display, tipo_tarjeta)
        if key not in agrupados:
            agrupados[key] = {
                'metodo': metodo,
                'metodo_display': metodo_display,
                'monto': 0,
                'voucher': '',
                'tipo_tarjeta': tipo_tarjeta,
                'notas': '',
                '_vouchers': set(),
                '_notas': set(),
            }
        agrupados[key]['monto'] += pago.get('monto') or 0
        if pago.get('voucher'):
            agrupados[key]['_vouchers'].add(str(pago['voucher']))
        if pago.get('notas'):
            agrupados[key]['_notas'].add(str(pago['notas']))

    resultado: list[dict] = []
    for item in agrupados.values():
        if item['_vouchers']:
            item['voucher'] = ', '.join(sorted(item['_vouchers']))
        if item['_notas']:
            item['notas'] = ' | '.join(sorted(item['_notas']))
        item.pop('_vouchers', None)
        item.pop('_notas', None)
        resultado.append(item)
    return resultado


def formatear_metodos_pago_str(metodos_pago: Iterable[dict]) -> str:
    """
    Genera un string concatenado con los métodos de pago ya agrupados.
    Añade el `tipo_tarjeta` entre paréntesis para tarjetas y venta por internet.
    """
    partes: list[str] = []
    for p in metodos_pago:
        texto = p.get('metodo_display') or p.get('metodo') or ''
        tipo = p.get('tipo_tarjeta') or ''
        metodo = p.get('metodo') or ''
        if tipo and (metodo == 'VENTA_INTERNET' or 'TARJETA' in metodo):
            texto += f" ({tipo})"
        if texto:
            partes.append(texto)
    return ', '.join(partes) if partes else 'Sin pagos'


# ========== CAMPOS `only()` ESTANDARIZADOS ==========

# DTE (listado principal)
ONLY_DTE_LISTA = (
    'id', 'numero_documento', 'fecha_emision', 'tipo_documento',
    'tipo_transaccion', 'estado_dte', 'descuento', 'monto_neto',
    'monto_con_iva', 'sucursal_id',
    'vendedor_id', 'vendedor__nombre', 'vendedor__codigo_vendedor',
    'receptor_id', 'receptor__nombre', 'receptor__rut', 'receptor__giro',
    'receptor__correoVendedor', 'receptor__direccion', 'receptor__comuna',
)

ONLY_DTE_PRODUCTO = (
    'id', 'dte_id', 'stock', 'precio', 'descuento_monto', 'monto_item',
    'costo', 'sobreprecio', 'descripcion',
    'productoTalla_id', 'productoTalla__sku', 'productoTalla__talla',
    'productoTalla__producto_id', 'productoTalla__producto__articulo',
)

ONLY_DTE_PAGO = (
    # OJO: todo campo que el consumidor lea debe estar aquí. Un campo ausente
    # queda diferido y Django dispara un `refresh_from_db` por cada acceso —
    # un N+1 mudo (no falla, solo se hace lento). `notas` faltaba y se lee en
    # `listar_documentos_ventas`: eran ~120 queries extra por página de 100.
    'id', 'dte_id', 'metodo_pago', 'monto', 'voucher', 'tipo_tarjeta', 'notas',
)

# Ticket (dashboard POS)
ONLY_TICKET_POS = (
    'id', 'correlativo', 'estado', 'total', 'metodo_pago',
    'modulo_origen', 'observaciones', 'created_at', 'fecha',
    'cliente_nombre', 'cliente_rut',
    'sucursal_id', 'vendedor_id',
    'vendedor__codigo_vendedor', 'vendedor__nombre',
)

ONLY_TICKET_PRODUCTO_POS = (
    'id', 'idTicket_id', 'stock',
    'ProductoTalla_id', 'ProductoTalla__sku', 'ProductoTalla__talla',
    'ProductoTalla__stock', 'ProductoTalla__producto_id',
    'ProductoTalla__producto__articulo', 'ProductoTalla__producto__sucursal_id',
)


# ========== PERMISOS GRANULARES DE EDICIÓN DE DTE ==========
#
# La matriz es separada (3 campos editables + 4 tipos de DTE). Para
# autorizar la edición de un campo sobre un DTE concreto, el usuario
# debe tener `puede_editar=True` en AMBAS opciones (la del campo y la
# del tipo de DTE).
#
# Los códigos viven en `OpcionMenu` y se administran desde la pantalla
# de gestión de permisos como cualquier otro permiso.

# Mapping campo lógico -> código de OpcionMenu.
CODIGO_PERMISO_CAMPO_DTE = {
    'fecha': 'dte_editar_fecha',
    'numero_documento': 'dte_editar_numero',
    'pago': 'dte_editar_pago',
    # Edición del vendedor asociado al DTE. Útil cuando el vendedor
    # original quedó mal asignado (p. ej., un cajero registró la venta a
    # su nombre y debe corregirse al vendedor real para no distorsionar
    # los reportes de comisiones).
    'vendedor': 'dte_editar_vendedor',
}

# Mapping `tipo_documento` almacenado en DB -> código de OpcionMenu.
CODIGO_PERMISO_TIPO_DTE = {
    'BOLETA ELECTRONICA': 'dte_editar_tipo_boleta_electronica',
    'BOLETA PAPEL': 'dte_editar_tipo_boleta_papel',
    'FACTURA ELECTRONICA': 'dte_editar_tipo_factura_electronica',
    'FACTURA EXENTA': 'dte_editar_tipo_factura_exenta',
}

# Pares de tipos de DTE entre los que se permite el cambio "en caliente"
# desde la gestión de documentos. Sólo se incluyen combinaciones con el
# mismo tratamiento tributario (IVA y datos del receptor), de modo que
# cambiar de uno a otro no requiere recalcular montos ni pedir giro.
TIPOS_DTE_INTERCAMBIABLES: tuple[frozenset, ...] = (
    frozenset({'BOLETA ELECTRONICA', 'BOLETA PAPEL'}),
)


# Mapa `Dte.tipo_documento` -> `Ticket.tipo_dte` (choices del modelo Ticket).
#
# Vive acá, y no en `views_modulo_ventas`, porque lo necesitan tres consumidores
# que no deben importarse entre sí: la edición de DTE, la eliminación de
# documentos y el servicio de reapertura de cotizaciones. Todos resuelven el
# MISMO problema: `Ticket.folio_dte` no es único (las series se numeran por tipo
# de documento y en producción hay 6 pares (sucursal, folio_dte) con más de un
# ticket), así que buscar por sucursal + folio sin el tipo puede devolver el
# ticket de otra venta.
TIPO_DTE_A_TIPO_TICKET = {
    'BOLETA ELECTRONICA': 'BOLETA_ELECTRONICA',
    'BOLETA PAPEL': 'BOLETA',
    'FACTURA ELECTRONICA': 'FACTURA_ELECTRONICA',
    'FACTURA EXENTA': 'FACTURA_EXENTA',
}


def tipo_ticket_para_dte(tipo_documento) -> str | None:
    """`Ticket.tipo_dte` esperado para un `Dte.tipo_documento`, o None."""
    return TIPO_DTE_A_TIPO_TICKET.get((tipo_documento or '').upper().strip())


# Valores de `Ticket.tipo_dte` que dicen algo sobre QUÉ SERIE de documento
# tributario respalda el ticket. Son exactamente los del mapa de arriba.
#
# El resto de los choices NO es informativo para desambiguar series:
#   * 'TICKET' es el **default del modelo** ("Ticket (sin DTE)"): lo trae todo
#     ticket al que nadie le escribió el tipo, y `registrar_pagos_ticket` nunca
#     lo actualiza al emitir el DTE. Medido en producción: de 18.964 tickets con
#     `folio_dte`, 18.673 están en 'TICKET' y 291 en 'TICKET_COBRO_CAMBIO' —
#     o sea CERO traen el tipo específico.
#   * 'TICKET_COBRO_CAMBIO' / 'TICKET_DEVOLUCION' / 'TICKET_CAMBIO_DIRECTO'
#     describen el flujo de cambio/devolución, no la serie: la diferencia de un
#     cambio se cobra con una boleta o una factura indistintamente.
#
# Tratar el default como si contradijera al DTE bloquearía TODAS las
# operaciones sobre tickets reales, así que hay que distinguir "tipo que
# contradice" de "tipo que no dice nada".
TIPOS_TICKET_DOCUMENTALES = frozenset(TIPO_DTE_A_TIPO_TICKET.values())


def tipo_ticket_contradice_dte(tipo_ticket, tipo_documento) -> bool:
    """True solo si el `tipo_dte` del ticket NIEGA al tipo del DTE.

    Devuelve False cuando el tipo del ticket es neutro (None, '', 'TICKET',
    'TICKET_*'), porque en ese caso no hay información con la que contradecir.
    """
    t = (tipo_ticket or '').upper().strip()
    if t not in TIPOS_TICKET_DOCUMENTALES:
        return False
    esperado = tipo_ticket_para_dte(tipo_documento)
    if not esperado:
        return False
    return t != esperado


def son_tipos_compatibles(origen: str, destino: str) -> bool:
    """True si los dos tipos pertenecen al mismo grupo intercambiable.

    La comparación es case-insensitive y tolerante a espacios/None.
    Si `origen == destino` devuelve False (no es un "cambio").
    """
    o = (origen or '').upper().strip()
    d = (destino or '').upper().strip()
    if not o or not d or o == d:
        return False
    return frozenset({o, d}) in TIPOS_DTE_INTERCAMBIABLES


def tipos_compatibles_para(tipo: str) -> list[str]:
    """Devuelve la lista de tipos DTE a los que `tipo` puede cambiar
    (incluye el propio tipo al inicio). Vacía si no participa de ningún
    grupo intercambiable.
    """
    t = (tipo or '').upper().strip()
    for grupo in TIPOS_DTE_INTERCAMBIABLES:
        if t in grupo:
            return [t] + sorted(x for x in grupo if x != t)
    return []


def puede_cambiar_tipo_dte(user, origen: str, destino: str,
                           sucursal_id=None) -> bool:
    """Verifica que el usuario pueda cambiar un DTE de `origen` a `destino`.

    Requiere:
      * Que sean tipos compatibles (ver :data:`TIPOS_DTE_INTERCAMBIABLES`).
      * Que el usuario tenga permiso de edición sobre AMBOS tipos
        (`dte_editar_tipo_*`).
    """
    if not son_tipos_compatibles(origen, destino):
        return False
    cod_origen = CODIGO_PERMISO_TIPO_DTE.get(origen.upper().strip())
    cod_destino = CODIGO_PERMISO_TIPO_DTE.get(destino.upper().strip())
    if not cod_origen or not cod_destino:
        return False
    return (
        _tiene_permiso_edicion(user, cod_origen, sucursal_id)
        and _tiene_permiso_edicion(user, cod_destino, sucursal_id)
    )


def _tiene_permiso_edicion(user, codigo_opcion: str, sucursal_id=None) -> bool:
    """Wrapper local para evitar import circular con `app.models`."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    # Import local para evitar ciclos en tiempo de carga.
    from app.models import PermisoRol
    return PermisoRol.tiene_permiso(
        user, codigo_opcion, 'puede_editar', sucursal_id=sucursal_id
    )


def puede_editar_campo_dte(user, campo: str, tipo_documento: str | None,
                           sucursal_id=None) -> bool:
    """
    Verifica que el usuario pueda editar el `campo` sobre un DTE del
    `tipo_documento` indicado, considerando ambas dimensiones de permiso.

    Args:
        user: Usuario autenticado.
        campo: 'fecha' | 'numero_documento' | 'pago'.
        tipo_documento: valor tal como está almacenado en `Dte.tipo_documento`
            (ej. 'BOLETA ELECTRONICA'). Si es desconocido retorna False.
        sucursal_id: sucursal activa, para respetar `PermisoSucursal`.
    """
    codigo_campo = CODIGO_PERMISO_CAMPO_DTE.get(campo)
    codigo_tipo = CODIGO_PERMISO_TIPO_DTE.get((tipo_documento or '').upper().strip())
    if not codigo_campo or not codigo_tipo:
        return False
    return (
        _tiene_permiso_edicion(user, codigo_campo, sucursal_id)
        and _tiene_permiso_edicion(user, codigo_tipo, sucursal_id)
    )


def permisos_edicion_dte_context(user, sucursal_id=None) -> dict:
    """
    Devuelve un diccionario apto para pasar al template con el estado
    de los 7 permisos de edición de DTE (3 campos + 4 tipos) más los
    pares de tipos intercambiables habilitados para el usuario.
    Útil en context_processors o en vistas que renderizan el modal.
    """
    flags_campo = {
        campo: _tiene_permiso_edicion(user, codigo, sucursal_id)
        for campo, codigo in CODIGO_PERMISO_CAMPO_DTE.items()
    }
    flags_tipo = {
        tipo: _tiene_permiso_edicion(user, codigo, sucursal_id)
        for tipo, codigo in CODIGO_PERMISO_TIPO_DTE.items()
    }

    # Para cada tipo DTE conocido, la lista de destinos permitidos al
    # usuario (sólo los que pertenecen al mismo grupo intercambiable Y
    # para los que tiene permiso de edición). Incluye el propio tipo.
    compatibles_por_tipo: dict[str, list[str]] = {}
    for tipo in CODIGO_PERMISO_TIPO_DTE:
        compatibles = tipos_compatibles_para(tipo)
        if not compatibles:
            continue
        # Filtrar los destinos a los que NO tiene permiso.
        permitidos = [
            t for t in compatibles
            if flags_tipo.get(t, False)
        ]
        # Sólo exponer si el usuario tiene permiso sobre el propio tipo
        # y al menos un destino distinto.
        if flags_tipo.get(tipo) and len(permitidos) > 1:
            compatibles_por_tipo[tipo] = permitidos

    return {
        'campo': flags_campo,
        'tipo': flags_tipo,
        # Booleano rápido: ¿puede editar algo en algún tipo?
        'cualquiera': (
            any(flags_campo.values()) and any(flags_tipo.values())
        ),
        # Pares intercambiables con ambos permisos concedidos.
        'compatibles_por_tipo': compatibles_por_tipo,
    }
