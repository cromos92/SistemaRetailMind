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
import re

from app.models import CargaFacturaPdf

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
- Marca, color, categoría, especialidades, guía y tipo de talla van con el valor EXACTO tal
  como aparece en listas_del_sistema (se validan al aplicar). Si pide una que NO está en las
  listas, no la inventes ni elijas otra parecida: dilo en la respuesta (se crea en Gestión de
  Productos y después te lo vuelve a pedir) y deja ese campo vacío.
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
- No repitas toda la vista previa en la respuesta: di qué cambiaste (o qué falta) y nada más.
- Si preguntan por otra factura o por un código ya cargado antes («¿a cuánto lo compré?», «¿en
  qué factura vino?»), responde con "facturas_anteriores": son lecturas ya hechas y guardadas
  de otras cargas; nunca hace falta volver a leer un PDF. Si ahí no hay nada, dilo.
- Todos los campos de un cambio van SIEMPRE, y los que NO cambian van vacíos: "" en textos y
  en los campos de lista de opciones, -1 en los numéricos, [] en las listas, y "" en "omitir"
  y "renombrar_tallas" (que valen "si" o "no" solo cuando la persona lo pide). Nunca
  rellenes un campo con un valor real que la persona no pidió."""


# La API compila el esquema a una gramática y tiene dos techos: 16 campos con
# unión de tipos (anyOf / null) y un tamaño total («Schema is too complex»).
# Por eso (1) no hay anyOf ni null: cada tipo tiene un valor «sin cambio» ('' en
# textos y enums, -1 en números, [] en listas, '' en los tri-estado si/no); y
# (2) las listas grandes del sistema (marcas, colores, categorías, guías,
# especialidades) NO van como enum: Claude las ve en el contexto y el valor se
# valida acá contra el catálogo (`_a_correcciones` rechaza lo que no exista).
SIN_CAMBIO_NUM = -1
ENUM_MAXIMO = 12
_TRI = {'type': 'string', 'enum': ['', 'si', 'no']}


def _num(tipo):
    return {'type': tipo, 'description': f'{SIN_CAMBIO_NUM} = sin cambio'}


def _texto():
    return {'type': 'string', 'description': '"" = sin cambio'}


def _opcion(valores):
    """Enum con '' («sin cambio») al frente si la lista es corta; si no, texto
    libre que se valida contra el catálogo al aplicar."""
    valores = [v for v in valores if v]
    if valores and len(valores) <= ENUM_MAXIMO:
        return {'type': 'string', 'enum': [''] + valores, 'description': '"" = sin cambio'}
    return {'type': 'string', 'description': '"" = sin cambio; si no, valor EXACTO de listas_del_sistema'}


def _lista(item):
    return {'type': 'array', 'items': item, 'description': '[] = sin cambio'}


def _esquema(catalogo):
    guias = _guias(catalogo)
    especialidad = {'type': 'string', 'description': 'valor EXACTO de listas_del_sistema.especialidades'}
    linea = {
        'type': 'object',
        'properties': {
            'n': {'type': 'integer'},
            'articulo': _texto(), 'descripcion': _texto(),
            'costo': _num('integer'), 'precioventa': _num('integer'),
            'cantidad': _num('integer'), 'importe': _num('integer'),
            'genero': _opcion(catalogo['generos']),
            'categoria': _opcion(catalogo['categorias']),
            'color': _opcion(catalogo['colores']),
            'marca': _opcion(catalogo['marcas']),
            'especialidades': _lista(especialidad),
            'ficha_id': _num('integer'),
            'guia': _opcion(guias),
            'tallas': _lista({
                'type': 'object',
                'properties': {'talla': {'type': 'string'}, 'cantidad': {'type': 'integer'}},
                'required': ['talla', 'cantidad'], 'additionalProperties': False}),
            'omitir': _TRI,
        },
        'additionalProperties': False,
    }
    linea['required'] = list(linea['properties'])
    cambio = {
        'type': 'object',
        'properties': {
            'idx': {'type': 'integer'},
            'marca': _opcion(catalogo['marcas']),
            'color': _opcion(catalogo['colores']),
            'tipo_talla': _opcion(list(svc_web.TIPOS_TALLA)),
            'guias_talla': _lista({
                'type': 'object',
                'properties': {'genero': {'type': 'string', 'enum': list(svc_web.GENEROS_GUIA)},
                               'guia': _opcion(guias)},
                'required': ['genero', 'guia'], 'additionalProperties': False}),
            'umbral_costo': _num('integer'), 'factor_bajo': _num('number'),
            'factor_alto': _num('number'), 'margen_sobreprecio': _num('number'),
            'dte_id': _num('integer'), 'renombrar_tallas': _TRI,
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


def _texto_historial(m):
    """Lo que Claude ve de un mensaje anterior: el texto y, si aplicó cambios, cuáles."""
    texto = str(m.get('texto', ''))[:600]
    if m.get('cambios'):
        texto += ' [Apliqué: ' + '; '.join(m['cambios'])[:400] + ']'
    if m.get('rechazos'):
        texto += ' [No apliqué: ' + '; '.join(m['rechazos'])[:200] + ']'
    return texto


def _preguntar(catalogo, previa, historial, texto, anteriores=None):
    """Una vuelta con Claude: dict {'respuesta', 'cambios'} según el esquema."""
    cliente = svc_lectura._cliente()
    contexto = {
        'listas_del_sistema': {k: catalogo[k] for k in ('marcas', 'colores', 'generos',
                                                          'categorias', 'especialidades', 'guias')},
        'tipos_de_talla': list(svc_web.TIPOS_TALLA),
        'vista_previa': [_resumen_factura(i) for i in previa],
        'facturas_anteriores': anteriores or 'ninguna coincidencia con cargas anteriores',
        'conversacion_reciente': [
            {'quien': m.get('quien'), 'texto': _texto_historial(m)}
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


_TRI_ESTADO = ('omitir', 'renombrar_tallas')


def _guias(catalogo):
    return sorted({g for lista in catalogo['guias'].values() for g in lista})


def _listas(catalogo):
    """Campo → valores admitidos (los que en el esquema van como texto libre).
    Una lista vacía en el sistema no valida nada: el valor pasa tal cual."""
    listas = {
        'marca': catalogo['marcas'], 'color': catalogo['colores'], 'genero': catalogo['generos'],
        'categoria': catalogo['categorias'], 'guia': _guias(catalogo),
        'especialidades': catalogo['especialidades'], 'tipo_talla': list(svc_web.TIPOS_TALLA),
    }
    return {k: v for k, v in listas.items() if v}


def _clave(valor):
    return ' '.join(str(valor).split()).upper()


def _normalizar(valor, lista):
    """El valor del catálogo que coincide (sin mayúsculas ni espacios de más) o None."""
    clave = _clave(valor)
    for v in lista:
        if _clave(v) == clave:
            return v
    return None


def _validar(campo, valor, listas, rechazos):
    """Valor normalizado contra el catálogo; None (y se anota) si no existe."""
    if campo not in listas:
        return valor
    if campo == 'especialidades':
        buenas = []
        for v in valor if isinstance(valor, list) else [valor]:
            n = _normalizar(v, listas[campo])
            if n is None:
                rechazos.append(f'especialidad «{v}»')
            elif n not in buenas:
                buenas.append(n)
        return buenas or None
    n = _normalizar(valor, listas[campo])
    if n is None:
        rechazos.append(f'{campo.replace("_", " ")} «{valor}»')
    return n


def _validar_guias_talla(valor, listas, rechazos):
    buenas = []
    for par in valor if isinstance(valor, list) else []:
        if not isinstance(par, dict):
            continue
        genero = _normalizar(par.get('genero', ''), svc_web.GENEROS_GUIA)
        guia = (_normalizar(par.get('guia', ''), listas['guia']) if 'guia' in listas
                else (str(par.get('guia') or '').strip() or None))
        if genero is None or guia is None:
            rechazos.append(f'guía «{par.get("guia")}» para {par.get("genero")}')
            continue
        buenas.append({'genero': genero, 'guia': guia})
    return buenas or None


def _sin_cambio(valor):
    """True si el campo vino con su valor «sin cambio» (o faltó)."""
    if valor is None or isinstance(valor, bool):
        return valor is None
    return valor == '' or valor == [] or (isinstance(valor, (int, float)) and valor == SIN_CAMBIO_NUM)


def _valor(campo, valor):
    if campo in _TRI_ESTADO and isinstance(valor, str):
        return valor.strip().lower() == 'si'
    return valor


def _a_correcciones(cambios, previa, catalogo):
    """Lo que devolvió Claude → (lista para web.aplicar_correcciones, valores
    rechazados por no existir en el catálogo). Líneas por posición."""
    por_idx = {item['idx']: item for item in previa}
    listas = _listas(catalogo)
    salida, rechazos = [], []
    for c in cambios or []:
        item = por_idx.get(c.get('idx'))
        if item is None:
            continue
        cambio = {'idx': item['idx']}
        for origen, destino in _CAMPOS_FACTURA.items():
            valor = c.get(origen)
            if _sin_cambio(valor):
                continue
            if origen == 'guias_talla':
                valor = _validar_guias_talla(valor, listas, rechazos)
            else:
                valor = _validar(origen, _valor(origen, valor), listas, rechazos)
            if valor is not None:
                cambio[destino] = valor
        lineas = [{} for _ in range(item.get('n_lineas') or len(item['planes']))]
        for l in c.get('lineas') or []:
            n = l.get('n')
            if not isinstance(n, int) or not 1 <= n <= len(lineas):
                continue
            destino = lineas[n - 1]
            for campo, valor in l.items():
                if campo == 'n' or _sin_cambio(valor):
                    continue
                if campo == 'tallas':
                    destino['tallas'] = {str(t['talla']): int(t['cantidad']) for t in valor}
                elif campo == 'omitir':
                    destino['_omitir'] = _valor(campo, valor)
                else:
                    valor = _validar(campo, valor, listas, rechazos)
                    if valor is not None:
                        destino[campo] = valor
        if any(lineas):
            cambio['lineas'] = lineas
        if len(cambio) > 1:
            salida.append(cambio)
    return salida, rechazos


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


# ------------------------------------------------------ cargas anteriores

_RE_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9./-]{2,}')
_SESIONES_A_MIRAR = 150
_MAX_COINCIDENCIAS = 8


def _claves(texto):
    """Códigos/folios (con dígitos) y palabras largas (marca, proveedor) del mensaje."""
    codigos, palabras = set(), set()
    for t in _RE_TOKEN.findall(texto):
        t = t.strip('.-/')
        if any(c.isdigit() for c in t):
            if len(t) >= 3:          # folios cortos (555) y códigos (126511-02)
                codigos.add(t.upper())
        elif len(t) >= 4:            # marca o proveedor (NIKE, CHALADA)
            palabras.add(t.upper())
    return codigos, palabras


def facturas_anteriores(sesion, user, texto):
    """Lo ya leído en OTRAS cargas de las bodegas de la persona que calza con el
    mensaje: líneas cuyo código contiene un token con dígitos, y facturas cuyo
    folio, proveedor o marca calzan. Así el agente responde «¿a cuánto lo compré
    antes?» desde el expediente guardado, sin volver a leer ningún PDF."""
    codigos, palabras = _claves(texto)
    if not codigos and not palabras:
        return {}
    from app.utils_permisos import obtener_sucursales_usuario
    ids = obtener_sucursales_usuario(user).values_list('id', flat=True)
    sesiones = (CargaFacturaPdf.objects.filter(sucursal_id__in=ids).exclude(id=sesion.id)
                .exclude(estado='LEYENDO').select_related('sucursal')
                .only('id', 'facturas', 'creado_en', 'sucursal__alias').order_by('-id')[:_SESIONES_A_MIRAR])
    facturas, lineas = [], []
    for s in sesiones:
        for d in s.facturas or []:
            folio = str(d.get('folio') or '')
            cabecera = ' '.join(str(d.get(k) or '') for k in ('proveedor_nombre', 'marca')).upper()
            # Palabras completas: «para» no debe calzar con PARAGUAY S.A.
            if folio in codigos or any(re.search(r'\b' + re.escape(p) + r'\b', cabecera) for p in palabras):
                if len(facturas) < _MAX_COINCIDENCIAS:
                    facturas.append({
                        'sesion': s.id, 'folio': d.get('folio'), 'proveedor': d.get('proveedor_nombre'),
                        'fecha': d.get('fecha_emision'), 'bodega': s.sucursal.alias, 'marca': d.get('marca'),
                        'lineas': len(d.get('lineas') or []),
                        'unidades': sum(sum((l.get('tallas') or {}).values()) for l in d.get('lineas') or []),
                        'estado': d.get('_estado'), 'leida_el': s.creado_en.date().isoformat(),
                    })
            if not codigos or len(lineas) >= _MAX_COINCIDENCIAS:
                continue
            for l in d.get('lineas') or []:
                art = str(l.get('articulo') or '').upper()
                if any(c in art for c in codigos):
                    lineas.append({
                        'sesion': s.id, 'folio': d.get('folio'), 'proveedor': d.get('proveedor_nombre'),
                        'fecha': d.get('fecha_emision'), 'bodega': s.sucursal.alias,
                        'articulo': l.get('articulo'), 'descripcion': l.get('descripcion'),
                        'color': l.get('color'), 'tallas': l.get('tallas'),
                        'unidades': sum((l.get('tallas') or {}).values()),
                        'costo': l.get('costo'), 'precioventa': l.get('precioventa'),
                        'estado_factura': d.get('_estado'),
                    })
                    if len(lineas) >= _MAX_COINCIDENCIAS:
                        break
        if len(facturas) >= _MAX_COINCIDENCIAS and len(lineas) >= _MAX_COINCIDENCIAS:
            break
    salida = {}
    if facturas:
        salida['facturas'] = facturas
    if lineas:
        salida['lineas'] = lineas
    return salida


def conversar(sesion, user, texto):
    """Un turno de chat. Guarda los dos mensajes (el del agente con `cambios` y
    `rechazos` estructurados), aplica los cambios y devuelve
    {'respuesta', 'cambios': [textos], 'rechazos': [textos], 'facturas': vista previa nueva}."""
    texto = (texto or '').strip()[:_MAX_TEXTO]
    if not texto:
        raise ErrorCarga('Escribe algo.')
    if sesion.estado != 'LEIDA':
        raise ErrorCarga('Espera a que termine la lectura o la carga para conversar.')
    catalogo = svc_web.opciones_catalogo(user)
    previa = svc_web.planificar(sesion, user)
    sesion.agregar_mensaje(svc_web.USUARIO, texto, tipo='chat')
    try:
        anteriores = facturas_anteriores(sesion, user, texto)
    except Exception:
        logger.exception('carga_factura: falló la búsqueda en cargas anteriores (sesión %s)', sesion.id)
        anteriores = {}
    try:
        salida = _preguntar(catalogo, previa, sesion.mensajes, texto, anteriores=anteriores)
    except ErrorCarga:
        raise
    except Exception as exc:
        logger.exception('carga_factura: falló el chat de la sesión %s', sesion.id)
        raise ErrorCarga(f'No pude procesar el mensaje ({type(exc).__name__}: {exc}).')
    respuesta = str(salida.get('respuesta') or '').strip() or 'Listo.'
    cambios, rechazos = _a_correcciones(salida.get('cambios'), previa, catalogo)
    rechazos = list(dict.fromkeys(rechazos))
    aplicados, fallo = [], ''
    if cambios:
        try:
            svc_web.aplicar_correcciones(sesion, cambios)
            aplicados = _describir(cambios, previa)
        except ErrorCarga as exc:
            fallo = str(exc)
    # El mensaje guardado lleva el texto limpio y los cambios aparte (la
    # pantalla los pinta como fichas); la respuesta de la API los lleva en texto.
    sesion.agregar_mensaje(svc_web.AGENTE, respuesta, tipo='chat',
                           cambios=aplicados, rechazos=rechazos, fallo=fallo)
    texto_api = respuesta
    if rechazos:
        texto_api += ('\n\nNo apliqué (no existe en el sistema; se crea en Gestión de Productos y '
                      'me lo vuelves a pedir): ' + '; '.join(rechazos) + '.')
    if fallo:
        texto_api += f'\n\nNo pude aplicar el cambio: {fallo}'
    if aplicados:
        texto_api += '\n\nApliqué: ' + '; '.join(aplicados) + '. La vista previa ya está recalculada.'
    return {'respuesta': texto_api, 'cambios': aplicados, 'rechazos': rechazos,
            'facturas': svc_web.planificar(sesion, user)}
