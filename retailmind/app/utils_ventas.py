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

from typing import Iterable


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
    'id', 'dte_id', 'metodo_pago', 'monto', 'voucher', 'tipo_tarjeta',
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
