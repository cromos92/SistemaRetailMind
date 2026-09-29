"""
Varios proveedores de IA para los agentes (lectura de facturas, chat de la
carga, búsqueda en internet y el asistente): Claude (Anthropic) y cualquier
API compatible con la de OpenAI — ChatGPT (OpenAI), Gemini (Google),
DeepSeek, OpenRouter (cientos de modelos con una sola clave) o un servidor
propio (Ollama, vLLM, Groq…).

Claves y modelo de cada tarea se configuran en Configuración → Inteligencia
Artificial (modelos ClaveProveedorIA / ModeloTareaIA, claves cifradas). Lo
guardado ahí manda; si no hay nada, se usan las variables de entorno de
siempre (ver TAREAS: CARGA_FACTURA_MODELO, …_CHAT, …_RAPIDO, …_BUSQUEDA,
ASISTENTE_MODELO…) y, sin ellas, el modelo por defecto. El proveedor va como
prefijo cuando el nombre no lo deja claro:

    claude-opus-5                     → Anthropic (sin prefijo = Claude)
    openai:gpt-5.4-mini               → OpenAI   (gpt-…, o3…, o4… sin prefijo también)
    gemini:gemini-3.8-flash           → Google   (gemini-… sin prefijo también)
    deepseek:deepseek-chat            → DeepSeek (solo texto: no lee imágenes)
    openrouter:google/gemini-3.8-flash → OpenRouter
    compatible:llama3.2-vision        → el servidor de IA_COMPATIBLE_URL

Varios separados por coma forman una CADENA DE RESPALDO: si el primero falla
(sin clave, caído, saturado, rechaza la petición o no devuelve JSON válido)
se usa el siguiente. Ej.: CARGA_FACTURA_MODELO_CHAT=
"gemini:gemini-3.8-flash,claude-sonnet-5".

Claves: las de la pantalla (cifradas en BD) o, de respaldo, las variables
ANTHROPIC_API_KEY (settings), OPENAI_API_KEY, GEMINI_API_KEY,
DEEPSEEK_API_KEY, OPENROUTER_API_KEY, IA_COMPATIBLE_API_KEY +
IA_COMPATIBLE_URL. Nunca en el código ni en los logs. Opcionales:
IA_TIMEOUT (segundos por petición, 600) e IA_PRECIOS (JSON
{"modelo": [entrada, salida, caché leída]} en US$ por millón, para modelos
que no estén en la tabla).

Sin paquetes nuevos: lo que no es Claude va por HTTP con `requests` a
/chat/completions; la búsqueda web de OpenAI usa /responses (herramienta
web_search) y la de OpenRouter su plugin «web».

`crear_mensaje` recibe la petición en el formato de Anthropic (system,
messages con bloques text / image / document / tool_use / tool_result,
tools, output_config) y devuelve un objeto con la forma de la respuesta del
SDK de Anthropic (content, stop_reason, usage, model): el código de los
agentes (bucle de zoom, parseo del JSON, contador de uso) no cambia.
"""
import json
import logging
import os
import re
import time
from types import SimpleNamespace

from django.conf import settings

logger = logging.getLogger('app')

ANTHROPIC = 'anthropic'
_PROVEEDORES = {
    'openai': {'nombre': 'OpenAI', 'clave': 'OPENAI_API_KEY', 'url': 'https://api.openai.com/v1'},
    'gemini': {'nombre': 'Google Gemini', 'clave': 'GEMINI_API_KEY',
               'url': 'https://generativelanguage.googleapis.com/v1beta/openai'},
    'deepseek': {'nombre': 'DeepSeek', 'clave': 'DEEPSEEK_API_KEY', 'url': 'https://api.deepseek.com/v1'},
    'openrouter': {'nombre': 'OpenRouter', 'clave': 'OPENROUTER_API_KEY', 'url': 'https://openrouter.ai/api/v1'},
    'compatible': {'nombre': 'API compatible', 'clave': 'IA_COMPATIBLE_API_KEY', 'url_env': 'IA_COMPATIBLE_URL'},
}
# Nombre sin prefijo → proveedor (el primero que calce).
_POR_NOMBRE = (('claude', ANTHROPIC), ('gpt-', 'openai'), ('chatgpt', 'openai'), ('o1', 'openai'),
               ('o3', 'openai'), ('o4', 'openai'), ('gemini', 'gemini'), ('deepseek', 'deepseek'))

# Precio de lista (US$ por millón de tokens): entrada, salida, caché leída,
# caché escrita. OpenAI y Gemini cachean solos el prefijo repetido (sin
# recargo de escritura). Solo para mostrar el costo de cada paso; la cuenta
# real la lleva cada proveedor. Por prefijo más largo del nombre. Revisado el
# 29-09-2026.
PRECIOS_USD_POR_MILLON = {
    'gpt-5.5': (5.0, 30.0, 0.5, 0.0),
    'gpt-5.4-mini': (0.75, 4.5, 0.075, 0.0),
    'gpt-5.4-nano': (0.20, 1.25, 0.02, 0.0),
    'gpt-5.4': (2.5, 15.0, 0.25, 0.0),
    'gpt-5.2': (1.75, 14.0, 0.175, 0.0),
    'gpt-5-mini': (0.25, 2.0, 0.025, 0.0),
    'gpt-5-nano': (0.05, 0.40, 0.005, 0.0),
    'gpt-5': (1.25, 10.0, 0.125, 0.0),
    'gemini-3.8-flash': (0.75, 3.75, 0.075, 0.0),
    'gemini-3.7-flash': (0.75, 3.75, 0.075, 0.0),
    'gemini-3.6-flash': (0.75, 3.75, 0.075, 0.0),
    'gemini-3.5-flash-lite': (0.30, 2.5, 0.03, 0.0),
    'gemini-3.5-flash': (1.5, 9.0, 0.15, 0.0),
    'gemini-3.1-flash-lite': (0.25, 1.5, 0.025, 0.0),
    'gemini-3.1-pro': (2.0, 12.0, 0.2, 0.0),
    'gemini-3-flash': (0.5, 3.0, 0.05, 0.0),
    'gemini-3-pro': (2.0, 12.0, 0.2, 0.0),
    'gemini-2.5-flash-lite': (0.10, 0.40, 0.01, 0.0),
    'gemini-2.5-flash': (0.30, 2.5, 0.03, 0.0),
    'gemini-2.5-pro': (1.25, 10.0, 0.125, 0.0),
    'deepseek': (0.28, 0.42, 0.028, 0.0),
}
# Modelo que no está en la tabla ni en IA_PRECIOS: se estima alto (tarifa de
# gpt-5.4) para no mostrar un costo menor al real.
PRECIO_DESCONOCIDO = (2.5, 15.0, 0.25, 0.0)

TIMEOUT = int(os.environ.get('IA_TIMEOUT', '600') or 600)
_REINTENTOS = 1           # por petición, ante 429 / 5xx / red caída (después pasa al siguiente de la cadena)
_TOPE_SALIDA_MINIMO = 8192

# Esfuerzo de razonamiento (el de Anthropic) → el de cada API.
_ESFUERZO = {
    'openai': {'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'xhigh', 'max': 'xhigh'},
    'gemini': {'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'high', 'max': 'high'},
    'openrouter': {'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'high', 'max': 'high'},
}


class ErrorProveedor(Exception):
    """El proveedor no respondió, rechazó la petición o devolvió algo
    inservible (mensaje para la persona). Se puede probar el siguiente de la
    cadena."""


class _Rechazo(Exception):
    """HTTP 400: la petición tiene algo que este proveedor no acepta (se
    intenta una versión más simple antes de rendirse)."""


class Bloque(dict):
    """Bloque de contenido de la respuesta. Es un dict (si la conversación
    sigue con Claude, el SDK de Anthropic lo acepta tal cual) con acceso por
    atributo, como los bloques del SDK (`b.type`, `b.text`, `b.input`)."""
    __slots__ = ()

    def __getattr__(self, clave):
        try:
            return self[clave]
        except KeyError:
            raise AttributeError(clave)


# ----------------------------------------------------------------- tareas

# Tareas de IA: variable de entorno y modelo por defecto de cada una (lo que
# rige si en Configuración → Inteligencia Artificial no hay nada guardado).
# Los módulos toman de aquí su constante de respaldo (lectura.MODELO…).
TAREAS = {
    'lectura': {
        'env': 'CARGA_FACTURA_MODELO', 'defecto': 'claude-opus-5',
        'ayuda': 'Lee el PDF de la factura (escaneo o PDF del SII). Lo delicado son los precios '
                 'escritos a mano: conviene el modelo más preciso.'},
    'lectura_opciones': {
        'env': 'CARGA_FACTURA_MODELOS', 'defecto': '', 'lista': True,
        'ayuda': 'Aparecen en el selector «Modelo» al subir una factura, para probar uno más '
                 'barato con la misma factura.'},
    'verificacion': {
        'env': 'CARGA_FACTURA_MODELO_VERIFICACION', 'defecto': '',
        'ayuda': 'Segunda mirada solo a las líneas con dudas. Vacío = el mismo de la lectura '
                 '(reutiliza las páginas desde la caché: lo más barato).'},
    'rapido': {
        'env': 'CARGA_FACTURA_MODELO_RAPIDO', 'defecto': 'claude-sonnet-5',
        'ayuda': 'Dice cuántos grados girar una página escaneada. Tarea simple: alcanza el más barato.'},
    'chat': {
        'env': 'CARGA_FACTURA_MODELO_CHAT', 'defecto': 'claude-sonnet-5',
        'ayuda': 'Traduce lo que escribes en el chat de la carga a correcciones de la vista previa.'},
    'busqueda': {
        'env': 'CARGA_FACTURA_MODELO_BUSQUEDA', 'defecto': 'claude-sonnet-5',
        'ayuda': 'Busca en internet qué producto es y su color. Solo pueden buscar Claude, OpenAI '
                 'y OpenRouter (el resto pasa a su respaldo).'},
    'asistente': {
        'env': 'ASISTENTE_MODELO', 'defecto': 'claude-sonnet-4-5-20250929',
        'ayuda': 'Asistente conversacional de RetailMind (consultas de ventas, stock, etc.).'},
}


# Sugerencias de la pantalla (ids completos, no prefijos de precio); los
# reales de cada cuenta llegan al probar su clave (ClaveProveedorIA.modelos).
MODELOS_SUGERIDOS = (
    'claude-opus-5', 'claude-sonnet-5', 'claude-haiku-4-5', 'claude-sonnet-4-5',
    'openai:gpt-5.4-mini', 'openai:gpt-5.4-nano', 'openai:gpt-5.4', 'openai:gpt-5-mini',
    'gemini:gemini-3.8-flash', 'gemini:gemini-2.5-flash', 'gemini:gemini-2.5-flash-lite',
    'deepseek:deepseek-chat',
)


def modelo_env(tarea):
    """Modelo de la tarea según su variable de entorno o, sin ella, el por defecto."""
    datos = TAREAS[tarea]
    return (os.environ.get(datos['env'], '') or '').strip() or datos['defecto']


# ------------------------------------------------ configuración guardada (BD)

# Se relee cada _TTL_CONFIG segundos: con varios workers de gunicorn, un cambio
# hecho en la pantalla llega a todos en ese plazo sin reiniciar (en el worker
# que guardó, al instante: la vista llama olvidar_config()).
_TTL_CONFIG = 15
_CACHE_CONFIG = {'datos': None, 'hasta': 0.0}


def olvidar_config():
    """Descarta la configuración leída (después de guardar en la pantalla)."""
    _CACHE_CONFIG['datos'] = None


def _leer_config():
    vacia = {'claves': {}, 'modelos': {}}
    try:
        from django.db import transaction

        from app.models import ClaveProveedorIA, ModeloTareaIA
        # atomic: si la tabla aún no existe (migración sin aplicar) el error
        # queda en un savepoint y no aborta la transacción de quien llama.
        with transaction.atomic():
            claves = list(ClaveProveedorIA.objects.filter(activa=True))
            modelos = list(ModeloTareaIA.objects.values_list('tarea', 'modelo'))
    except Exception as exc:   # sin tabla, sin BD o test sin BD: rigen las variables de entorno
        logger.debug('IA: sin configuración en BD (%s); uso variables de entorno', type(exc).__name__)
        return vacia
    datos = {'claves': {}, 'modelos': {}}
    for tarea, modelo in modelos:
        if str(modelo or '').strip():
            datos['modelos'][tarea] = str(modelo).strip()
    for c in claves:
        clave, url = c.get_clave().strip(), (c.url_base or '').strip()
        if clave or url:
            datos['claves'][c.proveedor] = {'clave': clave, 'url': url,
                                            'workspace': (c.workspace_id or '').strip()}
    return datos


def _config():
    ahora = time.monotonic()
    if _CACHE_CONFIG['datos'] is None or ahora >= _CACHE_CONFIG['hasta']:
        _CACHE_CONFIG['datos'] = _leer_config()
        _CACHE_CONFIG['hasta'] = ahora + _TTL_CONFIG
    return _CACHE_CONFIG['datos']


def modelo_tarea(tarea, respaldo=None):
    """Modelo (o cadena de respaldo) de una tarea: lo guardado en Configuración
    → Inteligencia Artificial; si no hay, `respaldo` (la constante del módulo,
    que ya trae la variable de entorno) o modelo_env(tarea)."""
    guardado = _config()['modelos'].get(tarea)
    if guardado:
        return guardado
    return modelo_env(tarea) if respaldo is None else respaldo


def origen_modelo(tarea):
    """'pantalla' | 'variable' | 'defecto': de dónde sale el modelo de la tarea."""
    if _config()['modelos'].get(tarea):
        return 'pantalla'
    if (os.environ.get(TAREAS[tarea]['env'], '') or '').strip():
        return 'variable'
    return 'defecto'


# ------------------------------------------------------------ configuración


def cadena(valor):
    """'a, b' → ['a', 'b'] (la cadena de respaldo, en orden)."""
    return [m.strip() for m in str(valor or '').split(',') if m.strip()]


def separar(modelo):
    """'gemini:gemini-3.8-flash' → ('gemini', 'gemini-3.8-flash'); sin
    prefijo se deduce por el nombre y, si no se reconoce, es Claude."""
    texto = str(modelo or '').strip()
    prefijo, sep, resto = texto.partition(':')
    if sep and (prefijo.lower() in _PROVEEDORES or prefijo.lower() == ANTHROPIC):
        return prefijo.lower(), resto.strip()
    baja = texto.lower()
    for inicio, proveedor in _POR_NOMBRE:
        if baja.startswith(inicio):
            return proveedor, texto
    return ANTHROPIC, texto


def es_claude(modelo):
    return separar(modelo)[0] == ANTHROPIC


def nombre_proveedor(proveedor):
    return 'Anthropic' if proveedor == ANTHROPIC else _PROVEEDORES.get(proveedor, {}).get('nombre', proveedor)


def variable_clave(proveedor):
    return 'ANTHROPIC_API_KEY' if proveedor == ANTHROPIC else _PROVEEDORES[proveedor]['clave']


def _clave_env(proveedor):
    if proveedor == ANTHROPIC:
        return str(getattr(settings, 'ANTHROPIC_API_KEY', '') or '').strip()
    return os.environ.get(_PROVEEDORES[proveedor]['clave'], '').strip()


def _clave(proveedor):
    """La clave de la pantalla (descifrada) o, si el proveedor no está en la
    pantalla, la variable de entorno. Si está en la pantalla manda entera,
    aunque su clave esté vacía (API compatible solo con URL, p. ej. Ollama):
    la clave del entorno NUNCA viaja a una URL escrita en la pantalla."""
    guardada = _config()['claves'].get(proveedor)
    if guardada is not None:
        return guardada['clave']
    return _clave_env(proveedor)


def origen_clave(proveedor):
    """'pantalla' | 'variable' | '': de dónde sale la configuración en uso."""
    if proveedor in _config()['claves']:
        return 'pantalla'
    if _clave_env(proveedor) or (proveedor == 'compatible' and _url_base(proveedor)):
        return 'variable'
    return ''


def clave_anthropic():
    """Clave de Anthropic para el SDK (pantalla o ANTHROPIC_API_KEY), o None."""
    return _clave(ANTHROPIC) or None


def workspace_anthropic():
    """Workspace de Anthropic (wrkspc_…) si la clave es de organización. Sale de
    la misma fuente que la clave: con clave en la pantalla, el de la pantalla
    (aunque esté vacío); si no, ANTHROPIC_WORKSPACE_ID."""
    guardada = _config()['claves'].get(ANTHROPIC)
    if guardada is not None:
        return guardada['workspace']
    return os.environ.get('ANTHROPIC_WORKSPACE_ID', '').strip()


def _url_base(proveedor):
    datos = _PROVEEDORES[proveedor]
    if 'url_env' in datos:
        url = _config()['claves'].get(proveedor, {}).get('url') or os.environ.get(datos['url_env'], '')
    else:
        url = datos['url']
    return url.strip().rstrip('/')


def falta_clave(proveedor, modelo=''):
    """Mensaje para la persona cuando un proveedor no tiene clave."""
    para = f' para usar {modelo}' if modelo else ''
    return (f'Falta la clave de {nombre_proveedor(proveedor)}{para}: agrégala en Configuración → '
            f'Inteligencia Artificial (o en la variable {variable_clave(proveedor)}).')


_PATRON_SECRETO = re.compile(r'(sk-[A-Za-z0-9_\-*]{6,}|AIza[A-Za-z0-9_\-]{10,}|[A-Za-z0-9_\-]*\*{3,}[A-Za-z0-9_\-]*)')


def sin_secretos(texto, *claves):
    """Quita de un mensaje de error cualquier clave (entera o enmascarada por el
    proveedor, p. ej. «sk-proj-****abcd»): esos mensajes se guardan y se muestran."""
    texto = str(texto or '')
    for clave in claves:
        if clave:
            texto = texto.replace(clave, '[clave oculta]')
    return _PATRON_SECRETO.sub('[clave oculta]', texto)


def _listo(modelo):
    """True si el proveedor de este modelo tiene clave (y URL, el compatible).
    Un servidor propio (Ollama) puede no pedir clave: basta la URL."""
    proveedor, nombre = separar(modelo)
    if not nombre:
        return False
    if proveedor == 'compatible':
        return bool(_url_base(proveedor))
    return bool(_clave(proveedor))


def configurado(modelos):
    """True si al menos un modelo de la cadena se puede usar en este servidor."""
    return any(_listo(m) for m in cadena(modelos))


def etiqueta(modelos):
    """Texto corto para la pantalla: 'gemini-3.8-flash (Google Gemini) · respaldo: claude-opus-5'."""
    partes = []
    for m in cadena(modelos):
        proveedor, nombre = separar(m)
        partes.append(f'{nombre} ({nombre_proveedor(proveedor)})')
    if not partes:
        return ''
    return partes[0] + (' · respaldo: ' + ', '.join(partes[1:]) if len(partes) > 1 else '')


def _precios_env():
    try:
        extra = json.loads(os.environ.get('IA_PRECIOS', '') or '{}')
    except ValueError:
        logger.warning('IA_PRECIOS no es JSON válido; se ignora')
        return {}
    tabla = {}
    for nombre, valores in (extra or {}).items():
        try:
            v = [float(x) for x in valores][:4]
        except (TypeError, ValueError):
            continue
        if len(v) >= 2:
            tabla[str(nombre).lower()] = tuple(v + [v[0] / 10.0, 0.0][len(v) - 2:])
    return tabla


def precios(modelo):
    """(entrada, salida, caché leída, caché escrita) en US$/millón de un
    modelo que NO es Claude (esos están en carga_factura/lectura.py), o None."""
    _, nombre = separar(modelo)
    nombre = nombre.lower().split('/')[-1].split(':')[0]   # 'openai/gpt-5.4-mini:online' → 'gpt-5.4-mini'
    tabla = {**PRECIOS_USD_POR_MILLON, **_precios_env()}
    mejor = ''
    for prefijo in tabla:
        if nombre.startswith(prefijo) and len(prefijo) > len(mejor):
            mejor = prefijo
    return tabla[mejor] if mejor else None


# ------------------------------------------------------ respaldo en cadena


def _se_puede_seguir(exc):
    """¿Vale la pena probar el siguiente modelo de la cadena tras este error?"""
    if isinstance(exc, ErrorProveedor):
        return True
    # Errores del SDK de Anthropic (clave, saturación, red, 5xx): sin importar
    # el paquete, por su módulo.
    return type(exc).__module__.split('.')[0] == 'anthropic'


def responder(modelos, llamar_claude, **peticion):
    """Recorre la cadena de modelos hasta que uno responda.

    Claude va por `llamar_claude(nombre_modelo, peticion)` (cada agente usa su
    forma: streaming, betas…); el resto por `crear_mensaje`. Un Claude sin
    ANTHROPIC_API_KEY se salta si hay otro después. Si todos fallan, sube el
    error del último."""
    candidatos = cadena(modelos)
    if not candidatos:
        raise ErrorProveedor('No hay ningún modelo de IA configurado para este paso.')
    for i, modelo in enumerate(candidatos):
        ultimo = i == len(candidatos) - 1
        proveedor, nombre = separar(modelo)
        try:
            if proveedor == ANTHROPIC:
                if not ultimo and not _clave(ANTHROPIC):
                    raise ErrorProveedor(falta_clave(ANTHROPIC))
                return llamar_claude(nombre, dict(peticion))
            return crear_mensaje(modelo, **peticion)
        except Exception as exc:
            if ultimo or not _se_puede_seguir(exc):
                raise
            logger.warning('IA: %s no respondió (%s); pruebo con %s', modelo, exc, candidatos[i + 1])


# ------------------------------------------------ Anthropic → OpenAI (ida)


def _campo(bloque, clave, defecto=None):
    if isinstance(bloque, dict):
        return bloque.get(clave, defecto)
    return getattr(bloque, clave, defecto)


def _url_datos(fuente):
    if _campo(fuente, 'type') == 'url':
        return _campo(fuente, 'url')
    return f"data:{_campo(fuente, 'media_type')};base64,{_campo(fuente, 'data')}"


def _parte(bloque, proveedor):
    """Un bloque de Anthropic (text / image / document) → parte de mensaje OpenAI."""
    tipo = _campo(bloque, 'type')
    if tipo == 'text':
        return {'type': 'text', 'text': _campo(bloque, 'text') or ''}
    if tipo == 'image':
        imagen = {'url': _url_datos(_campo(bloque, 'source'))}
        if proveedor == 'openai':
            imagen['detail'] = 'high'   # sin esto OpenAI puede mirar la imagen a baja resolución
        return {'type': 'image_url', 'image_url': imagen}
    if tipo == 'document':
        fuente = _campo(bloque, 'source')
        if _campo(fuente, 'type') == 'text':
            return {'type': 'text', 'text': _campo(fuente, 'data') or ''}
        return {'type': 'file', 'file': {'filename': 'documento.pdf', 'file_data': _url_datos(fuente)}}
    return None     # thinking, búsquedas del servidor de Anthropic, etc.: no aplican


def _texto_sistema(system):
    if isinstance(system, str):
        return system
    return '\n\n'.join(_campo(b, 'text') or '' for b in (system or []) if _campo(b, 'type', 'text') == 'text')


def _mensajes(system, messages, proveedor):
    salida = []
    sistema = _texto_sistema(system)
    if sistema:
        salida.append({'role': 'system', 'content': sistema})
    for n, mensaje in enumerate(messages or []):
        rol, contenido = _campo(mensaje, 'role'), _campo(mensaje, 'content')
        if isinstance(contenido, str):
            salida.append({'role': rol, 'content': contenido})
            continue
        if rol == 'assistant':
            textos, llamadas = [], []
            for b in contenido or []:
                tipo = _campo(b, 'type')
                if tipo == 'text':
                    textos.append(_campo(b, 'text') or '')
                elif tipo == 'tool_use':
                    llamadas.append({'id': _campo(b, 'id'), 'type': 'function', 'function': {
                        'name': _campo(b, 'name'),
                        'arguments': json.dumps(_campo(b, 'input') or {}, ensure_ascii=False)}})
            nuevo = {'role': 'assistant', 'content': '\n'.join(t for t in textos if t) or None}
            if llamadas:
                nuevo['tool_calls'] = llamadas
            salida.append(nuevo)
            continue
        # Mensaje del usuario: cada tool_result es un mensaje 'tool' (van justo
        # después de la llamada) y el resto va en un mensaje 'user'. Las API
        # compatibles no aceptan imágenes en un mensaje 'tool': la imagen que
        # devolvió la herramienta (zoom) va en el mensaje 'user' siguiente.
        partes, imagenes = [], []
        for b in contenido or []:
            if _campo(b, 'type') != 'tool_result':
                parte = _parte(b, proveedor)
                if parte:
                    partes.append(parte)
                continue
            resultado = _campo(b, 'content')
            if isinstance(resultado, str) or resultado is None:
                texto = resultado or ''
            else:
                textos = []
                for rb in resultado:
                    if _campo(rb, 'type') == 'text':
                        textos.append(_campo(rb, 'text') or '')
                    else:
                        parte = _parte(rb, proveedor)
                        if parte:
                            imagenes.append(parte)
                texto = '\n'.join(textos) or ('La imagen pedida va en el mensaje siguiente.' if imagenes else '')
            if _campo(b, 'is_error'):
                texto = 'ERROR: ' + texto
            salida.append({'role': 'tool', 'tool_call_id': _campo(b, 'tool_use_id'),
                           'content': texto or '(sin contenido)'})
        if imagenes:
            partes = [{'type': 'text', 'text': 'Resultado de la herramienta:'}] + imagenes + partes
        if partes:
            salida.append({'role': 'user', 'content': partes})
    return salida


def _herramientas(tools, proveedor):
    """Herramientas propias → funciones. Devuelve (funciones, pide_busqueda_web)."""
    funciones, busqueda = [], False
    for t in tools or []:
        tipo = str(_campo(t, 'type') or '')
        if tipo.startswith('web_search'):
            busqueda = True
            continue
        if _campo(t, 'input_schema') is None:
            continue    # otra herramienta del servidor de Anthropic: aquí no existe
        funcion = {'name': _campo(t, 'name'), 'description': _campo(t, 'description') or '',
                   'parameters': _campo(t, 'input_schema')}
        if proveedor == 'openai' and _campo(t, 'strict'):
            funcion['strict'] = True
        funciones.append({'type': 'function', 'function': funcion})
    return funciones, busqueda


def esquema_estricto(esquema):
    """Copia del JSON Schema para el modo estricto de OpenAI: todo objeto con
    additionalProperties false y TODAS sus propiedades en required. Los campos
    opcionales pasan a obligatorios: el modelo pone el centinela ("" / -1 /
    []) que el código ya entiende como «no viene»."""
    if isinstance(esquema, list):
        return [esquema_estricto(x) for x in esquema]
    if not isinstance(esquema, dict):
        return esquema
    copia = {k: esquema_estricto(v) for k, v in esquema.items()}
    if copia.get('type') == 'object' and isinstance(copia.get('properties'), dict):
        copia['required'] = list(copia['properties'])
        copia['additionalProperties'] = False
    return copia


def _pedido_json(esquema):
    return ('\n\nResponde ÚNICAMENTE con un objeto JSON válido (sin texto alrededor ni ```) que '
            'cumpla este JSON Schema:\n' + json.dumps(esquema, ensure_ascii=False))


def extraer_json(texto):
    """El JSON de una respuesta (quita ```json … ``` y texto alrededor).
    ValueError si no hay JSON válido."""
    t = str(texto or '').strip()
    bloque = re.search(r'```(?:json)?\s*(.*?)```', t, re.S)
    if bloque:
        t = bloque.group(1).strip()
    if not t.startswith(('{', '[')):
        ini, fin = t.find('{'), t.rfind('}')
        if ini >= 0 and fin > ini:
            t = t[ini:fin + 1]
    json.loads(t)
    return t


# --------------------------------------------------------------------- HTTP


def _detalle(respuesta_http):
    try:
        datos = respuesta_http.json()
    except ValueError:
        return (respuesta_http.text or '')[:300]
    if isinstance(datos, list) and datos:
        datos = datos[0]
    error = datos.get('error') if isinstance(datos, dict) else None
    if isinstance(error, dict):
        return str(error.get('message') or error)[:300]
    return str(error or datos)[:300]


def _post(proveedor, ruta, payload, modelo):
    import requests

    nombre = nombre_proveedor(proveedor)
    cabeceras = {'Content-Type': 'application/json'}
    clave = _clave(proveedor)
    if clave:
        cabeceras['Authorization'] = f'Bearer {clave}'
    elif proveedor != 'compatible':
        raise ErrorProveedor(falta_clave(proveedor, modelo))
    if proveedor == 'openrouter':
        cabeceras['X-Title'] = 'RetailMind'
    url = _url_base(proveedor) + ruta
    if not url.startswith('http'):
        raise ErrorProveedor('Falta la URL de la API compatible: agrégala en Configuración → Inteligencia '
                             'Artificial o en IA_COMPATIBLE_URL (p. ej. http://localhost:11434/v1 para Ollama).')
    for intento in range(_REINTENTOS + 1):
        try:
            r = requests.post(url, json=payload, headers=cabeceras, timeout=(15, TIMEOUT))
        except requests.RequestException as exc:
            if intento < _REINTENTOS:
                time.sleep(3)
                continue
            raise ErrorProveedor(f'{nombre} no respondió ({type(exc).__name__}).') from exc
        if r.status_code in (429, 500, 502, 503, 504, 529) and intento < _REINTENTOS:
            time.sleep(3)
            continue
        break
    if r.status_code == 200:
        try:
            return r.json()
        except ValueError:
            raise ErrorProveedor(f'{nombre} devolvió una respuesta que no es JSON.')
    detalle = sin_secretos(_detalle(r), clave)
    if r.status_code == 400 or r.status_code == 422:
        raise _Rechazo(detalle)
    if r.status_code in (401, 403):
        raise ErrorProveedor(f'{nombre} rechazó la clave (revísala en Configuración → Inteligencia '
                             f'Artificial o en {variable_clave(proveedor)}): {detalle}')
    if r.status_code == 404:
        raise ErrorProveedor(f'{nombre} no reconoce el modelo «{modelo}»: {detalle}')
    if r.status_code in (402, 429):
        raise ErrorProveedor(f'{nombre} está saturado o sin saldo/cuota: {detalle}')
    raise ErrorProveedor(f'{nombre} respondió {r.status_code}: {detalle}')


def _degradar(payload, detalle, pasos):
    """Ante un 400, quita lo que el proveedor no acepta según lo que nombra el
    error. False si el error no nombra nada que se pueda simplificar (no se
    reintenta a ciegas: con imágenes cada reintento es caro)."""
    texto = detalle.lower()
    for paso in pasos:
        if paso['aplica'](payload) and any(p in texto for p in paso['claves']):
            paso['hacer'](payload)
            return True
    return False


def _agregar_al_sistema(payload, texto):
    mensajes = payload['messages']
    if mensajes and mensajes[0]['role'] == 'system':
        mensajes[0]['content'] += texto
    else:
        mensajes.insert(0, {'role': 'system', 'content': texto.strip()})


# ----------------------------------------------------- Chat Completions


def crear_mensaje(modelo, *, messages, system=None, tools=None, max_tokens=4096, output_config=None,
                  **_propio_de_anthropic):
    """Una respuesta de un modelo que no es Claude, con la forma de la del
    SDK de Anthropic. `cache_control`, `betas`, `extra_body`… son de Anthropic
    y se ignoran (OpenAI y Gemini cachean solos el prefijo repetido)."""
    proveedor, nombre = separar(modelo)
    if proveedor == ANTHROPIC:
        raise ValueError('crear_mensaje es para modelos que no son Claude')
    if not _listo(modelo):
        raise ErrorProveedor(falta_clave(proveedor, nombre))
    output_config = output_config or {}
    formato = output_config.get('format') or {}
    esquema = formato.get('schema') if formato.get('type') == 'json_schema' else None
    esfuerzo = _ESFUERZO.get(proveedor, {}).get(output_config.get('effort') or '')
    funciones, busqueda = _herramientas(tools, proveedor)

    if busqueda and proveedor == 'openai':
        return _crear_con_busqueda_openai(modelo, nombre, system, messages, max_tokens, esquema, esfuerzo)
    if busqueda and proveedor != 'openrouter':
        raise ErrorProveedor(f'{nombre_proveedor(proveedor)} no puede buscar en internet desde aquí; '
                             f'usa openai:, openrouter: o Claude para la búsqueda.')

    payload = {'model': nombre, 'messages': _mensajes(system, messages, proveedor)}
    campo_tope = 'max_completion_tokens' if proveedor == 'openai' else 'max_tokens'
    payload[campo_tope] = int(max_tokens)
    if funciones:
        payload['tools'] = funciones
    if esfuerzo:
        if proveedor == 'openrouter':
            payload['reasoning'] = {'effort': esfuerzo}
        else:
            payload['reasoning_effort'] = esfuerzo
    if proveedor == 'openrouter':
        payload['usage'] = {'include': True}     # devuelve el costo real
        if busqueda:
            payload['plugins'] = [{'id': 'web', 'max_results': 5}]
    if esquema:
        if proveedor == 'deepseek':
            # DeepSeek solo tiene modo JSON (sin esquema): el esquema va en el pedido.
            payload['response_format'] = {'type': 'json_object'}
            _agregar_al_sistema(payload, _pedido_json(esquema))
        else:
            estricto = proveedor in ('openai', 'openrouter')
            payload['response_format'] = {'type': 'json_schema', 'json_schema': {
                'name': 'respuesta', 'strict': estricto,
                'schema': esquema_estricto(esquema) if estricto else esquema}}

    def formato_simple(p):
        """json_schema → json_object → sin formato (el esquema va en el pedido)."""
        if p['response_format']['type'] == 'json_schema':
            p['response_format'] = {'type': 'json_object'}
            _agregar_al_sistema(p, _pedido_json(esquema))
        else:
            p.pop('response_format')

    def bajar_tope(p):
        p[campo_tope] = max(_TOPE_SALIDA_MINIMO, p[campo_tope] // 4)

    pasos = [
        {'claves': ('reasoning',), 'aplica': lambda p: 'reasoning_effort' in p or 'reasoning' in p,
         'hacer': lambda p: (p.pop('reasoning_effort', None), p.pop('reasoning', None))},
        {'claves': ('max_tokens', 'max_completion_tokens', 'maximum', 'too large'),
         'aplica': lambda p: p[campo_tope] > _TOPE_SALIDA_MINIMO, 'hacer': bajar_tope},
        {'claves': ('strict',), 'aplica': lambda p: any(f['function'].get('strict') for f in p.get('tools', [])),
         'hacer': lambda p: [f['function'].pop('strict', None) for f in p.get('tools', [])]},
        {'claves': ('response_format', 'json_schema', 'schema', 'json', 'additionalproperties', 'strict'),
         'aplica': lambda p: 'response_format' in p, 'hacer': formato_simple},
    ]
    for _intento in range(len(pasos) + 2):
        try:
            datos = _post(proveedor, '/chat/completions', payload, nombre)
            break
        except _Rechazo as exc:
            if not _degradar(payload, str(exc), pasos):
                raise ErrorProveedor(f'{nombre_proveedor(proveedor)} rechazó la petición a {nombre}: {exc}')
            logger.info('IA: %s rechazó la petición (%s); reintento simplificada', modelo, exc)
    else:
        raise ErrorProveedor(f'{nombre_proveedor(proveedor)} rechazó la petición a {nombre}.')
    return _respuesta_chat(datos, f'{proveedor}:{nombre}', con_json=bool(esquema), busqueda=busqueda)


def _respuesta_chat(datos, modelo, con_json=False, busqueda=False):
    """Chat Completions → respuesta con la forma de la de Anthropic."""
    opcion = (datos.get('choices') or [{}])[0]
    mensaje = opcion.get('message') or {}
    texto = mensaje.get('content')
    if isinstance(texto, list):
        texto = ''.join(p.get('text', '') for p in texto if isinstance(p, dict))
    llamadas = mensaje.get('tool_calls') or []
    fin = opcion.get('finish_reason')
    if mensaje.get('refusal') or fin == 'content_filter':
        razon = 'refusal'
    elif llamadas:
        razon = 'tool_use'
    elif fin == 'length':
        razon = 'max_tokens'
    else:
        razon = 'end_turn'
    if texto and con_json and razon == 'end_turn':
        try:
            texto = extraer_json(texto)
        except ValueError:
            raise ErrorProveedor(f'{modelo} no devolvió un JSON válido.')
    contenido = [Bloque(type='text', text=texto)] if texto else []
    for n, llamada in enumerate(llamadas):
        funcion = llamada.get('function') or {}
        try:
            argumentos = json.loads(funcion.get('arguments') or '{}')
        except ValueError:
            argumentos = {}
        contenido.append(Bloque(type='tool_use', id=llamada.get('id') or f'llamada_{n}',
                                name=funcion.get('name'), input=argumentos))
    u = datos.get('usage') or {}
    return _respuesta(contenido, razon, modelo, entrada=u.get('prompt_tokens'),
                      salida=u.get('completion_tokens'),
                      cacheada=(u.get('prompt_tokens_details') or {}).get('cached_tokens'),
                      busquedas=1 if busqueda else 0, costo=u.get('cost'),
                      detalle=mensaje.get('refusal') or fin)


def _respuesta(contenido, razon, modelo, entrada=0, salida=0, cacheada=0, busquedas=0, costo=None,
               detalle=None):
    entrada, cacheada = int(entrada or 0), int(cacheada or 0)
    uso = SimpleNamespace(
        # Como en Anthropic: input_tokens NO incluye lo leído de caché.
        input_tokens=max(0, entrada - cacheada), output_tokens=int(salida or 0),
        cache_read_input_tokens=cacheada, cache_creation_input_tokens=0,
        server_tool_use=SimpleNamespace(web_search_requests=busquedas) if busquedas else None,
        costo_usd=float(costo) if isinstance(costo, (int, float)) else None)
    return SimpleNamespace(content=contenido, stop_reason=razon, model=modelo, usage=uso,
                           stop_details=SimpleNamespace(category=detalle or '') if razon == 'refusal' else None)


# ------------------------------------------- OpenAI Responses (web_search)


def _entrada_responses(messages):
    entrada = []
    for mensaje in messages or []:
        rol, contenido = _campo(mensaje, 'role'), _campo(mensaje, 'content')
        if isinstance(contenido, str):
            textos = [contenido]
        else:
            textos = [_campo(b, 'text') or '' for b in contenido or [] if _campo(b, 'type') == 'text']
        tipo = 'output_text' if rol == 'assistant' else 'input_text'
        partes = [{'type': tipo, 'text': t} for t in textos if t]
        if partes:
            entrada.append({'role': rol, 'content': partes})
    return entrada


def _crear_con_busqueda_openai(modelo, nombre, system, messages, max_tokens, esquema, esfuerzo):
    """Búsqueda en internet con OpenAI: Responses API + herramienta web_search."""
    payload = {'model': nombre, 'input': _entrada_responses(messages), 'tools': [{'type': 'web_search'}],
               'max_output_tokens': int(max_tokens)}
    sistema = _texto_sistema(system)
    if sistema:
        payload['instructions'] = sistema
    if esfuerzo:
        payload['reasoning'] = {'effort': esfuerzo}
    if esquema:
        payload['text'] = {'format': {'type': 'json_schema', 'name': 'respuesta', 'strict': True,
                                      'schema': esquema_estricto(esquema)}}

    def sin_formato(p):
        p.pop('text')
        p['instructions'] = (p.get('instructions') or '') + _pedido_json(esquema)

    pasos = [
        {'claves': ('reasoning',), 'aplica': lambda p: 'reasoning' in p, 'hacer': lambda p: p.pop('reasoning')},
        {'claves': ('format', 'schema', 'json'), 'aplica': lambda p: 'text' in p, 'hacer': sin_formato},
    ]
    for _intento in range(len(pasos) + 2):
        try:
            datos = _post('openai', '/responses', payload, nombre)
            break
        except _Rechazo as exc:
            if not _degradar(payload, str(exc), pasos):
                raise ErrorProveedor(f'OpenAI rechazó la búsqueda con {nombre}: {exc}')
    else:
        raise ErrorProveedor(f'OpenAI rechazó la búsqueda con {nombre}.')
    textos, busquedas, rechazo = [], 0, ''
    for item in datos.get('output') or []:
        if item.get('type') == 'web_search_call':
            busquedas += 1
        elif item.get('type') == 'message':
            for c in item.get('content') or []:
                if c.get('type') == 'output_text':
                    textos.append(c.get('text') or '')
                elif c.get('type') == 'refusal':
                    rechazo = c.get('refusal') or 'refusal'
    incompleta = (datos.get('status') == 'incomplete'
                  and (datos.get('incomplete_details') or {}).get('reason') == 'max_output_tokens')
    razon = 'refusal' if rechazo else ('max_tokens' if incompleta else 'end_turn')
    texto = textos[-1] if textos else ''
    if texto and esquema and razon == 'end_turn':
        try:
            texto = extraer_json(texto)
        except ValueError:
            raise ErrorProveedor(f'{nombre} no devolvió un JSON válido.')
    u = datos.get('usage') or {}
    return _respuesta([Bloque(type='text', text=texto)] if texto else [], razon, f'openai:{nombre}',
                      entrada=u.get('input_tokens'), salida=u.get('output_tokens'),
                      cacheada=(u.get('input_tokens_details') or {}).get('cached_tokens'),
                      busquedas=busquedas, detalle=rechazo)


# ------------------------------------------------------ probar una clave


def probar(proveedor, clave=None, url=None, workspace=None):
    """Valida una clave pidiéndole al proveedor su lista de modelos (no gasta
    tokens). `clave` / `url` / `workspace` None = los que están en uso
    (pantalla o variable). Devuelve {'ok', 'detalle', 'modelos': [ids]}."""
    import requests

    if proveedor != ANTHROPIC and proveedor not in _PROVEEDORES:
        return {'ok': False, 'detalle': 'Proveedor desconocido.', 'modelos': []}
    clave = (_clave(proveedor) if clave is None else clave or '').strip()
    nombre = nombre_proveedor(proveedor)
    cabeceras = {'Accept': 'application/json'}
    if proveedor == ANTHROPIC:
        base = 'https://api.anthropic.com/v1'
        cabeceras.update({'x-api-key': clave, 'anthropic-version': '2023-06-01'})
        ws = (workspace_anthropic() if workspace is None else workspace or '').strip()
        if ws:
            cabeceras['anthropic-workspace-id'] = ws
        ruta = '/models?limit=1000'
    else:
        datos = _PROVEEDORES[proveedor]
        if 'url_env' in datos:
            base = ((_url_base(proveedor) if url is None else url) or '').strip().rstrip('/')
            if not base.startswith('http'):
                return {'ok': False, 'detalle': 'Falta la URL base (p. ej. http://localhost:11434/v1).',
                        'modelos': []}
        else:
            base = datos['url']
        if clave:
            cabeceras['Authorization'] = f'Bearer {clave}'
        ruta = '/models'
    if not clave and proveedor != 'compatible':
        return {'ok': False, 'detalle': 'No hay clave para probar.', 'modelos': []}
    try:
        if proveedor == 'openrouter':
            # /models de OpenRouter es público: la clave se valida aparte.
            r = requests.get(base + '/key', headers=cabeceras, timeout=(10, 30))
            if r.status_code == 200:
                r = requests.get(base + '/models', headers=cabeceras, timeout=(10, 30))
        else:
            r = requests.get(base + ruta, headers=cabeceras, timeout=(10, 30))
    except requests.RequestException as exc:
        return {'ok': False, 'detalle': f'{nombre} no respondió ({type(exc).__name__}).', 'modelos': []}
    if r.status_code in (401, 403):
        return {'ok': False, 'modelos': [],
                'detalle': sin_secretos(f'{nombre} rechazó la clave: {_detalle(r)}', clave)[:300]}
    if r.status_code != 200:
        return {'ok': False, 'modelos': [],
                'detalle': sin_secretos(f'{nombre} respondió {r.status_code}: {_detalle(r)}', clave)[:300]}
    try:
        filas = r.json().get('data') or []
    except (ValueError, AttributeError):
        filas = []
    modelos = sorted({str(f.get('id') or '').replace('models/', '', 1)
                      for f in filas if isinstance(f, dict) and f.get('id')})
    return {'ok': True, 'modelos': modelos[:500],
            'detalle': f'Clave válida: {nombre} ofrece {len(modelos)} modelo(s).'}
