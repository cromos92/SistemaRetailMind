"""
Precio original de referencia por SKU — lógica PURA (sin BD ni Django).

La consume el endpoint externo GET /api/precios-referencia/
(app/api/external/views.py::PreciosReferenciaView), que precarga en lote el
historial y las liquidaciones y le pasa a estas funciones listas de dicts.
Separada de la vista para poder testearla con SimpleTestCase.

Contexto de los datos:
  - HistorialCambioPrecio vive a nivel PRODUCTO (ficha = artículo×sucursal),
    no a nivel SKU/talla. El endpoint junta el historial de TODAS las fichas
    de la empresa que contienen el SKU (mismo criterio de consolidación que
    /api/precios-actuales/, que toma el MAX de precioventa entre fichas).
  - El historial mezcla cambios de COSTO, SOBREPRECIO y PRECIO_VENTA: el
    servicio app/services/historial_precios.py etiqueta el motivo con
    "[COSTO] …" / "[SOBREPRECIO] …" / "[PRECIO_VENTA] …" (o "Cambio de COSTO"
    cuando no hay motivo). Las filas sin etiqueta son de los flujos viejos
    (edición rápida, sincronización, campañas, app móvil), que solo registran
    precioventa. Solo las filas de PVP sirven acá: ver es_cambio_pvp().
  - Montos: todos los campos de precio del ERP son IntegerField (CLP sin
    decimales) y la API los expone como int, igual que precios-actuales.
"""
import calendar
from decimal import Decimal, ROUND_HALF_UP

FUENTE_PVP_ACTUAL = 'PVP_ACTUAL'
FUENTE_LIQUIDACION = 'LIQUIDACION'
FUENTE_HISTORIAL = 'HISTORIAL'

#: Prefijos de motivo de filas que NO son cambios de PVP (formato de
#: historial_precios.registrar_cambios_precio y de la edición masiva de
#: existencias). Sensible a mayúsculas a propósito: las etiquetas las genera
#: el código, y un motivo libre tipo "cambio de costo proveedor" en una fila
#: de edición rápida SÍ es un cambio de PVP.
PREFIJOS_MOTIVO_NO_PVP = ('[COSTO]', '[SOBREPRECIO]')
#: Motivos exactos que se generan cuando el cambio no trae motivo libre.
MOTIVOS_NO_PVP = ('Cambio de COSTO', 'Cambio de SOBREPRECIO')


def es_cambio_pvp(motivo) -> bool:
    """True si la fila de HistorialCambioPrecio corresponde al precio de venta."""
    texto = (motivo or '').strip()
    if texto.startswith(PREFIJOS_MOTIVO_NO_PVP):
        return False
    return texto not in MOTIVOS_NO_PVP


def restar_meses(fecha, meses: int):
    """`fecha` menos `meses` meses calendario (día acotado al fin de mes).

    Conserva hora y tzinfo. 31/03 - 1 mes = 28/02 (o 29 en bisiesto).
    """
    total = fecha.year * 12 + (fecha.month - 1) - int(meses)
    anio, mes0 = divmod(total, 12)
    mes = mes0 + 1
    dia = min(fecha.day, calendar.monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


def _int(valor) -> int:
    return int(valor or 0)


def _clave_orden(cambio):
    """Orden cronológico estable: fecha_cambio y, en empate, id."""
    return (cambio['fecha_cambio'], _int(cambio.get('id')))


def ultimo_cambio(cambios):
    """El cambio más reciente (fecha_cambio, desempate por id) o None."""
    validos = [c for c in cambios if c.get('fecha_cambio') is not None]
    return max(validos, key=_clave_orden, default=None)


def ultima_rebaja(cambios):
    """El cambio más reciente que BAJÓ el precio (precio_nuevo < precio_anterior)."""
    return ultimo_cambio([
        c for c in cambios
        if _int(c.get('precio_nuevo')) < _int(c.get('precio_anterior'))
    ])


def pvp_maximo_en_ventana(cambios, desde):
    """Mayor PVP observado en el historial con fecha_cambio >= `desde`.

    Considera precio_anterior Y precio_nuevo de cada fila: el anterior estuvo
    vigente hasta la fecha del cambio, así que también es un PVP observado
    dentro de la ventana. Precios <= 0 se ignoran. En empate de precio gana la
    observación más reciente. Devuelve {'precio', 'fecha'} o None.
    """
    mejor = None  # (precio, fecha, id)
    for c in cambios:
        fecha = c.get('fecha_cambio')
        if fecha is None or fecha < desde:
            continue
        for precio in (_int(c.get('precio_anterior')), _int(c.get('precio_nuevo'))):
            if precio <= 0:
                continue
            clave = (precio, fecha, _int(c.get('id')))
            if mejor is None or clave > mejor:
                mejor = clave
    if mejor is None:
        return None
    return {'precio': mejor[0], 'fecha': mejor[1]}


def elegir_liquidacion(items):
    """Elige UN item de liquidación activa entre las fichas del SKU.

    El constraint del modelo garantiza máx. una campaña activa por ficha, pero
    un SKU vive en varias fichas (una por sucursal). Se toma el de mayor
    precio_original (coherente con el MAX de PVP entre fichas de
    precios-actuales); desempate: aplicación más reciente, luego campana_id.
    """
    validos = [i for i in items if i]
    if not validos:
        return None

    def clave(item):
        fecha = item.get('fecha_aplicacion')
        return (
            _int(item.get('precio_original')),
            fecha is not None,
            fecha if fecha is not None else 0,
            _int(item.get('campana_id')),
        )

    return max(validos, key=clave)


def precio_original_referencia(pvp_actual, precio_original_liquidacion=None,
                               pvp_maximo_ventana=None):
    """(precio, fuente) = el mayor entre PVP actual, original de la liquidación
    activa y PVP máximo del historial en la ventana.

    En empate gana la fuente más "fuerte" en este orden: PVP_ACTUAL,
    LIQUIDACION, HISTORIAL (si el PVP actual ya es el máximo, no hay rebaja).
    """
    precio_ref, fuente_ref = _int(pvp_actual), FUENTE_PVP_ACTUAL
    for fuente, precio in (
        (FUENTE_LIQUIDACION, precio_original_liquidacion),
        (FUENTE_HISTORIAL, pvp_maximo_ventana),
    ):
        precio = _int(precio)
        if precio > precio_ref:
            precio_ref, fuente_ref = precio, fuente
    return precio_ref, fuente_ref


def descuento_vigente_pct(pvp_actual, precio_original) -> Decimal:
    """round((1 - pvp_actual / precio_original) * 100, 1), HALF_UP, en Decimal.

    0.0 si no hay rebaja (pvp >= original) o si falta alguno de los precios
    (<= 0: un PVP en cero es dato roto, no un 100 % de descuento).
    """
    pvp = _int(pvp_actual)
    original = _int(precio_original)
    if pvp <= 0 or original <= 0 or pvp >= original:
        return Decimal('0.0')
    pct = (Decimal(1) - Decimal(pvp) / Decimal(original)) * 100
    return pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def _fecha_iso(dt):
    return dt.strftime('%Y-%m-%d') if dt else None


def _fecha_hora_iso(dt):
    return dt.isoformat() if dt else None


def armar_referencia_sku(pvp_actual, cambios, liquidacion, ventana_desde,
                         fmt_fecha=_fecha_iso, fmt_fecha_hora=_fecha_hora_iso):
    """Arma los campos de referencia de precio de UN SKU.

    - pvp_actual: int, el mismo valor que precios-actuales (MAX entre fichas).
    - cambios: filas de HistorialCambioPrecio de todas las fichas del SKU como
      dicts {id, precio_anterior, precio_nuevo, tipo_cambio, fecha_cambio
      (datetime), motivo, usuario}. Puede traer filas de COSTO/SOBREPRECIO: se
      filtran acá con es_cambio_pvp().
    - liquidacion: item ya elegido (elegir_liquidacion) o None, dict con
      {campana_id, nombre, estado, tipo_regla, fecha_inicio, fecha_fin,
      precio_original, precio_liquidacion, estado_item, fecha_aplicacion}.
    - ventana_desde: datetime de inicio de la ventana del PVP máximo.
    - fmt_fecha / fmt_fecha_hora: formateadores de datetime (la vista pasa
      versiones que convierten a hora local de Chile).
    """
    pvp_actual = _int(pvp_actual)
    cambios_pvp = [c for c in cambios if es_cambio_pvp(c.get('motivo'))]

    ultimo = ultimo_cambio(cambios_pvp)
    ultimo_out = None
    if ultimo is not None:
        ultimo_out = {
            'precio_anterior': _int(ultimo.get('precio_anterior')),
            'precio_nuevo': _int(ultimo.get('precio_nuevo')),
            'tipo': ultimo.get('tipo_cambio') or '',
            'fecha': fmt_fecha(ultimo['fecha_cambio']),
            'fecha_hora': fmt_fecha_hora(ultimo['fecha_cambio']),
            'usuario': ultimo.get('usuario') or None,
            'motivo': ultimo.get('motivo') or '',
        }

    maximo = pvp_maximo_en_ventana(cambios_pvp, ventana_desde)
    maximo_out = (
        {'precio': maximo['precio'], 'fecha': fmt_fecha(maximo['fecha'])}
        if maximo else None
    )

    rebaja = ultima_rebaja(cambios_pvp)

    liq_out = None
    precio_original_liq = None
    if liquidacion:
        precio_original_liq = _int(liquidacion.get('precio_original')) or None
        precio_liq = liquidacion.get('precio_liquidacion')
        liq_out = {
            'campana_id': liquidacion.get('campana_id'),
            'nombre': liquidacion.get('nombre') or '',
            'estado': liquidacion.get('estado') or '',
            'tipo_regla': liquidacion.get('tipo_regla') or '',
            'fecha_inicio': fmt_fecha(liquidacion.get('fecha_inicio')),
            'fecha_fin': fmt_fecha(liquidacion.get('fecha_fin')),
            'fecha_aplicacion': fmt_fecha(liquidacion.get('fecha_aplicacion')),
            'precio_original': precio_original_liq,
            # null en NxM: esa regla no cambia el precio (línea gratis en POS).
            'precio_liquidacion': int(precio_liq) if precio_liq is not None else None,
            'estado_item': liquidacion.get('estado_item') or '',
        }

    precio_ref, fuente = precio_original_referencia(
        pvp_actual,
        precio_original_liq,
        maximo['precio'] if maximo else None,
    )

    return {
        'pvp_actual': pvp_actual,
        'ultimo_cambio_pvp': ultimo_out,
        'pvp_maximo_ventana': maximo_out,
        'pvp_antes_ultima_rebaja': _int(rebaja.get('precio_anterior')) if rebaja else None,
        'fecha_ultima_rebaja': fmt_fecha(rebaja['fecha_cambio']) if rebaja else None,
        'liquidacion': liq_out,
        'precio_original_referencia': precio_ref,
        'fuente_original': fuente,
        'descuento_vigente_pct': descuento_vigente_pct(pvp_actual, precio_ref),
    }
