"""
Chat con el agente sobre la vista previa de una carga.

La persona escribe en lenguaje natural («la marca es CHALADA», «las tallas
son CL», «la línea 3 es de mujer y color plata», «usa margen 1,9», «¿por qué
la línea 2 está en error?») y Claude lo traduce a correcciones del MISMO tipo
que las de la tarjeta (web.aplicar_correcciones): nada fuera de esa lista se
puede tocar desde el chat, y los valores de marca / color / género /
categoría / especialidad / guía tienen que existir en el sistema (van como
enum en el esquema de salida). Cargar, borrar o crear fichas no se puede
desde aquí: para eso está el botón «Cargar» de la tarjeta.
"""
import json
import logging

from . import lectura as svc_lectura
from . import web as svc_web
from .facturas import ErrorCarga

logger = logging.getLogger('app')

_HISTORIAL = 12      # mensajes recientes que ve Claude
_MAX_TEXTO = 2000

_INSTRUCCIONES = """Eres el agente de carga de productos desde facturas de proveedor de una cadena de
tiendas de calzado y ropa deportiva en Chile. La persona ya subió una factura; en el JSON de
abajo van la VISTA PREVIA actual (qué haría el sistema con cada línea: estado, identidad,
tallas, precios, avisos y errores), las listas del sistema y la conversación reciente.
La persona te escribe para corregir datos, dar parámetros o preguntar.

Responde en "respuesta" (español de Chile, tuteando, breve y concreto) y pon en "cambios"
SOLO lo que la persona pidió, con valores EXACTOS de las listas. Reglas:
- Si pide una marca, color, categoría, especialidad o guía que NO está en las listas, no la
  inventes ni elijas otra parecida: dilo en la respuesta (se crea en Gestión de Productos y
  después te lo vuelve a pedir) y no cambies ese campo.
- Cambios de toda la factura (marca, color por defecto, tipo de talla, guías por género,
  regla de precio, margen de sobreprecio, DTE) van en el cambio de esa factura (por "idx").
  Cambios de una línea van en "lineas" con su "n". «Todas las líneas» = repetir en cada n.
- "tallas" reemplaza la curva COMPLETA de la línea (talla y cantidad); si la persona solo
  corrige una talla, escribe la curva completa con esa corrección.
- Precios: "factor_bajo" / "factor_alto" / "umbral_costo" cambian la regla venta = costo ×
  factor de toda la factura; "precioventa" en una línea fija el precio de esa línea. Solo
  enteros en pesos. "margen_sobreprecio" es el % de sobreprecio de las fichas nuevas.
- Un código que ya existe entra en su ficha y la ficha manda (color, género, categoría):
  si la persona quiere que entre en otra ficha, usa "ficha_id" con un id de las candidatas.
- No puedes cargar, borrar, crear fichas ni cambiar stock: si lo pide, explica que use el
  botón «Cargar» de la tarjeta (o que revise la ficha en Gestión de Productos).
- Si algo es ambiguo (no sabes a qué línea o factura se refiere), pregunta en "respuesta" y
  deja "cambios" vacío. Si solo pregunta, responde con lo que ves en la vista previa y deja
  "cambios" vacío. Explica los errores y avisos en palabras simples.
- No repitas toda la vista previa en la respuesta: di qué cambiaste (o qué falta) y nada más."""


def _nulo(tipo):
    return {'anyOf': [{'type': tipo}, {'type': 'null'}]}


def _enum_nulo(valores):
    valores = [v for v in valores if v]
    return ({'anyOf': [{'type': 'string', 'enum': valores}, {'type': 'null'}]}
            if valores else _nulo('string'))


def _lista_nula(item):
    return {'anyOf': [{'type': 'array', 'items': item}, {'type': 'null'}]}


def _esquema(catalogo):
    guias = sorted({g for lista in catalogo['guias'].values() for g in lista})
    especialidad = ({'type': 'string', 'enum': catalogo['especialidades']}
                    if catalogo['especialidades'] else {'type': 'string'})
    linea = {
        'type': 'object',
        'properties': {
            'n': {'type': 'integer'},
            'articulo': _nulo('string'), 'descripcion': _nulo('string'),
            'costo': _nulo('integer'), 'precioventa': _nulo('integer'),
            'cantidad': _nulo('integer'), 'importe': _nulo('integer'),
            'genero': _enum_nulo(catalogo['generos']),
            'categoria': _enum_nulo(catalogo['categorias']),
            'color': _enum_nulo(catalogo['colores']),
            'marca': _enum_nulo(catalogo['marcas']),
            'especialidades': _lista_nula(especialidad),
            'ficha_id': _nulo('integer'),
            'guia': _enum_nulo(guias),
            'tallas': _lista_nula({
                'type': 'object',
                'properties': {'talla': {'type': 'string'}, 'cantidad': {'type': 'integer'}},
                'required': ['talla', 'cantidad'], 'additionalProperties': False}),
            'omitir': _nulo('boolean'),
        },
        'additionalProperties': False,
    }
    linea['required'] = list(linea['properties'])
    cambio = {
        'type': 'object',
        'properties': {
            'idx': {'type': 'integer'},
            'marca': _enum_nulo(catalogo['marcas']),
            'color': _enum_nulo(catalogo['colores']),
            'tipo_talla': _enum_nulo(list(svc_web.TIPOS_TALLA)),
            'guias_talla': _lista_nula({
                'type': 'object',
                'properties': {'genero': {'type': 'string', 'enum': list(svc_web.GENEROS_GUIA)},
                               'guia': {'type': 'string', 'enum': guias} if guias else {'type': 'string'}},
                'required': ['genero', 'guia'], 'additionalProperties': False}),
            'umbral_costo': _nulo('integer'), 'factor_bajo': _nulo('number'),
            'factor_alto': _nulo('number'), 'margen_sobreprecio': _nulo('number'),
            'dte_id': _nulo('integer'), 'renombrar_tallas': _nulo('boolean'),
            'lineas': {'type': 'array', 'items': linea},
        },
        'additionalProperties': False,
    }
    cambio['required'] = list(cambio['properties'])
    return {'type': 'object',
            'properties': {'respuesta': {'type': 'string'},
                           'cambios': {'type': 'array', 'items': cambio}},
            'required': ['respuesta', 'cambios'], 'additionalProperties': False}


# ---------------------------------------------------------------- contexto


def _resumen_linea(p):
    return {
        'n': p['n'], 'articulo': p['articulo'], 'descripcion': p['descripcion'],
        'estado': 'OMITIDA' if p['omitida'] else p['estado'], 'unidades': p['unidades'],
        'tallas': {t['factura']: t['cantidad'] for t in p['tallas']},
        'tallas_como_quedan': [t['ficha'] for t in p['tallas']],
        'tipo_talla': p['tipo_talla'], 'guia': p['guia'] and p['guia']['nombre'],
        'costo': p['costo'], 'precio_lista': p.get('precio_lista'), 'descuento': p.get('descuento'),
        'venta': p['precioventa'], 'fuente_venta': p['fuente_pv'],
        'vigentes': p['vigentes'], 'opciones_existente': p['opciones'],
        'marca': p['marca'] and p['marca']['valor'], 'color': p['color'] and p['color']['valor'],
        'genero': p['genero'] and p['genero']['valor'],
        'categoria': p['categoria'] and p['categoria']['ruta'],
        'especialidades': p['especialidades'],
        'ficha_destino': p['destino'] and (
            f"#{p['destino']['id']} {p['destino']['sucursal']} «{p['destino']['descripcion']}» "
            f"{p['destino']['marca']}/{p['destino']['color']}/{p['destino']['genero']}/{p['destino']['categoria']}"),
        'fichas_candidatas': [
            f"#{f['id']} {f['marca']}/{f['color']}/{f['genero']}/{f['categoria']} "
            f"({f['tallas']} tallas, stock {f['stock']})" for f in p['candidatas']],
        'errores': p['errores'], 'avisos': p['avisos'][:5],
        'valores_actuales_json': {k: v for k, v in p['json'].items() if v not in (None, '', [])},
    }


def _resumen_factura(item):
    return {
        'idx': item['idx'], 'folio': item['folio'], 'proveedor': item['proveedor'],
        'fecha': item['fecha_emision'], 'estado': item['estado'], 'bodega': item['sucursal'],
        'marca': item['marca'], 'perfil_marca': item.get('perfil'),
        'color_por_defecto': item['color'], 'tipo_talla': item.get('tipo_talla'),
        'guias_talla': item.get('guias_talla'), 'regla_precio': item.get('regla'),
        'dte': item['dte'], 'error': item['error'],
        'dtes_candidatos': item['candidatos_dte'][:8],
        'avisos_factura': (item['totales'] or {}).get('avisos', []) + item['revisar'],
        'lineas': [_resumen_linea(p) for p in item['planes']],
    }


def _preguntar(catalogo, previa, historial, texto):
    """Una vuelta con Claude: dict {'respuesta', 'cambios'} según el esquema."""
    cliente = svc_lectura._cliente()
    contexto = {
        'listas_del_sistema': {k: catalogo[k] for k in ('marcas', 'colores', 'generos',
                                                          'categorias', 'especialidades', 'guias')},
        'tipos_de_talla': list(svc_web.TIPOS_TALLA),
        'vista_previa': [_resumen_factura(i) for i in previa],
        'conversacion_reciente': [
            {'quien': m.get('quien'), 'texto': str(m.get('texto', ''))[:600]}
            for m in historial[-_HISTORIAL:]],
        'mensaje_de_la_persona': texto,
    }
    respuesta = svc_lectura._pedir(
        cliente, max_tokens=8000,
        messages=[{'role': 'user', 'content': [
            {'type': 'text', 'text': _INSTRUCCIONES},
            {'type': 'text', 'text': json.dumps(contexto, ensure_ascii=False, default=str)}]}],
        output_config={'effort': 'medium',
                       'format': {'type': 'json_schema', 'schema': _esquema(catalogo)}})
    return json.loads(svc_lectura._texto(respuesta))


# ------------------------------------------------------------- correcciones

_CAMPOS_FACTURA = {
    'marca': 'marca', 'color': 'color', 'tipo_talla': 'tipo_talla', 'guias_talla': 'guias_talla',
    'dte_id': 'dte_id', 'renombrar_tallas': '_renombrar_tallas', 'umbral_costo': '_umbral_costo',
    'factor_bajo': '_factor_bajo', 'factor_alto': '_factor_alto',
    'margen_sobreprecio': '_margen_sobreprecio',
}


def _a_correcciones(cambios, previa):
    """Lo que devolvió Claude → lista para web.aplicar_correcciones (líneas por posición)."""
    por_idx = {item['idx']: item for item in previa}
    salida = []
    for c in cambios or []:
        item = por_idx.get(c.get('idx'))
        if item is None:
            continue
        cambio = {'idx': item['idx']}
        for origen, destino in _CAMPOS_FACTURA.items():
            if c.get(origen) is not None:
                cambio[destino] = c[origen]
        lineas = [{} for _ in range(item.get('n_lineas') or len(item['planes']))]
        for l in c.get('lineas') or []:
            n = l.get('n')
            if not isinstance(n, int) or not 1 <= n <= len(lineas):
                continue
            destino = lineas[n - 1]
            for campo, valor in l.items():
                if campo == 'n' or valor is None:
                    continue
                if campo == 'tallas':
                    destino['tallas'] = {str(t['talla']): int(t['cantidad']) for t in valor}
                elif campo == 'omitir':
                    destino['_omitir'] = bool(valor)
                else:
                    destino[campo] = valor
        if any(lineas):
            cambio['lineas'] = lineas
        if len(cambio) > 1:
            salida.append(cambio)
    return salida


def _describir(cambios, previa):
    por_idx = {item['idx']: item for item in previa}
    partes = []
    for c in cambios:
        folio = por_idx[c['idx']]['folio']
        campos = [f'{k.lstrip("_").replace("_", " ")} → {v}' for k, v in c.items()
                  if k not in ('idx', 'lineas')]
        if campos:
            partes.append(f'factura {folio}: ' + ', '.join(campos))
        for n, l in enumerate(c.get('lineas') or [], start=1):
            if l:
                partes.append(f'línea {n}: ' + ', '.join(
                    f'{k.lstrip("_")} → {v}' for k, v in l.items()))
    return partes


def conversar(sesion, user, texto):
    """Un turno de chat. Guarda los dos mensajes, aplica los cambios y devuelve
    {'respuesta', 'cambios': [textos], 'facturas': vista previa nueva}."""
    texto = (texto or '').strip()[:_MAX_TEXTO]
    if not texto:
        raise ErrorCarga('Escribe algo.')
    if sesion.estado != 'LEIDA':
        raise ErrorCarga('Espera a que termine la lectura o la carga para conversar.')
    catalogo = svc_web.opciones_catalogo(user)
    previa = svc_web.planificar(sesion, user)
    sesion.agregar_mensaje(svc_web.USUARIO, texto, tipo='chat')
    try:
        salida = _preguntar(catalogo, previa, sesion.mensajes, texto)
    except ErrorCarga:
        raise
    except Exception as exc:
        logger.exception('carga_factura: falló el chat de la sesión %s', sesion.id)
        raise ErrorCarga(f'No pude procesar el mensaje ({type(exc).__name__}: {exc}).')
    respuesta = str(salida.get('respuesta') or '').strip() or 'Listo.'
    cambios = _a_correcciones(salida.get('cambios'), previa)
    aplicados = []
    if cambios:
        try:
            svc_web.aplicar_correcciones(sesion, cambios)
            aplicados = _describir(cambios, previa)
        except ErrorCarga as exc:
            respuesta += f'\n\nNo pude aplicar el cambio: {exc}'
    if aplicados:
        respuesta += '\n\nApliqué: ' + '; '.join(aplicados) + '. La vista previa ya está recalculada.'
    sesion.agregar_mensaje(svc_web.AGENTE, respuesta, tipo='chat')
    return {'respuesta': respuesta, 'cambios': aplicados,
            'facturas': svc_web.planificar(sesion, user)}
