"""
Vistas de Configuración → Inteligencia Artificial.

Una pantalla para los agentes de IA (lectura de facturas, chat de la carga,
búsqueda en internet, asistente):
  * Claves de API por proveedor (Anthropic, OpenAI, Gemini, DeepSeek,
    OpenRouter, API compatible), guardadas CIFRADAS (ClaveProveedorIA) y
    validadas contra el proveedor antes de guardarlas. Nunca vuelven al
    navegador: solo los últimos 4 caracteres.
  * Modelo de cada tarea (ModeloTareaIA), con respaldo, y cuál rige hoy y de
    dónde sale (pantalla / variable de entorno / por defecto).
  * Gasto estimado de los últimos 30 días (CargaFacturaPdf.uso).

Lo guardado aquí manda sobre las variables de entorno (ver app/utils_ia.py);
sin nada guardado todo sigue como antes. Permiso `inteligencia_artificial`:
por defecto solo el Maestro (puede_ver para entrar, puede_editar para
guardar / probar).
"""
import json
import logging
import re
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from app import utils_ia
from app.decorators import requiere_permiso
from app.models import (
    PROVEEDOR_IA_CHOICES, TAREA_IA_CHOICES, CargaFacturaPdf, ClaveProveedorIA, ModeloTareaIA, PermisoRol,
)

logger = logging.getLogger('app')

CODIGO_PERMISO = 'inteligencia_artificial'

# Dónde se crea la clave de cada proveedor (para el enlace «¿Dónde la consigo?»).
ENLACES_CLAVE = {
    'anthropic': 'https://console.anthropic.com/settings/keys',
    'openai': 'https://platform.openai.com/api-keys',
    'gemini': 'https://aistudio.google.com/apikey',
    'deepseek': 'https://platform.deepseek.com/api_keys',
    'openrouter': 'https://openrouter.ai/keys',
    'compatible': '',
}
# Un nombre de modelo: letras, números y . _ - / @ (el «:» separa el proveedor
# y OpenRouter lo usa en variantes como «:online» / «:free»).
MODELO_RE = re.compile(r'^[A-Za-z0-9._@/:+\-]{1,120}$')
_PROVEEDORES_VALIDOS = {p for p, _ in PROVEEDOR_IA_CHOICES}
_BUSCAN_EN_INTERNET = {'anthropic', 'openai', 'openrouter'}


def _json(request):
    try:
        return json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None


def _error(mensaje, status=400, **extra):
    return JsonResponse({'ok': False, 'error': mensaje, **extra}, status=status)


def _precio(modelo):
    """(entrada, salida) US$/millón del primer modelo de la cadena, o None."""
    from app.services.carga_factura import lectura as svc_lectura

    primero = (utils_ia.cadena(modelo) or [''])[0]
    if not primero:
        return None
    if utils_ia.es_claude(primero):
        p = svc_lectura._precios(primero)
    else:
        p = utils_ia.precios(primero)
    return [p[0], p[1]] if p else None


# --------------------------------------------------------------- estado


def _estado_proveedor(codigo, nombre, fila):
    return {
        'codigo': codigo,
        'nombre': nombre,
        'variable': utils_ia.variable_clave(codigo),
        # De dónde sale la clave que se usa hoy: 'pantalla' | 'variable' | ''.
        'origen': utils_ia.origen_clave(codigo),
        'guardada': bool(fila and (fila.clave_cifrada or fila.url_base)),
        'ultimos4': fila.ultimos4 if fila else '',
        'activa': fila.activa if fila else True,
        'url_base': fila.url_base if fila else '',
        'workspace_id': fila.workspace_id if fila else '',
        'probada_en': fila.probada_en.isoformat(timespec='minutes') if fila and fila.probada_en else '',
        'prueba_ok': fila.prueba_ok if fila else None,
        'prueba_detalle': fila.prueba_detalle if fila else '',
        'modelos': (fila.modelos or [])[:500] if fila else [],
        'actualizado_por': (fila.actualizado_por.get_full_name() or fila.actualizado_por.username)
                           if fila and fila.actualizado_por else '',
        'enlace': ENLACES_CLAVE.get(codigo, ''),
        'pide_url': codigo == 'compatible',
        'pide_workspace': codigo == 'anthropic',
    }


def _estado_tarea(codigo, nombre, guardado):
    datos = utils_ia.TAREAS[codigo]
    efectivo = utils_ia.modelo_tarea(codigo)
    if codigo == 'lectura_opciones':
        efectivos = [m.strip() for m in efectivo.split(';') if m.strip()]
    else:
        efectivos = [efectivo] if efectivo else []
    # El que responde de verdad: el primero de cada cadena con clave (los
    # anteriores sin clave se saltan en cada llamada).
    usa = [next((m for m in utils_ia.cadena(c) if utils_ia._listo(m)), '') for c in efectivos]
    return {
        'codigo': codigo,
        'nombre': nombre,
        'ayuda': datos['ayuda'],
        'lista': bool(datos.get('lista')),
        'variable': datos['env'],
        'defecto': datos['defecto'],
        'guardado': guardado,
        'efectivo': efectivo,
        'origen': utils_ia.origen_modelo(codigo),
        'etiquetas': [utils_ia.etiqueta(m) for m in efectivos],
        'configurado': [bool(u) for u in usa],
        'principales': [(utils_ia.cadena(c) or [''])[0] for c in efectivos],
        'usa': usa,
        'usa_etiquetas': [utils_ia.etiqueta(u) if u else '' for u in usa],
        'precio': _precio((usa[0] or efectivos[0])) if efectivos else None,
    }


def _catalogo_modelos(filas):
    """Sugerencias para los campos de modelo: los de la tabla de precios y los
    que listó cada proveedor al probar su clave (con su prefijo)."""
    vistos, catalogo = set(), []

    def agregar(modelo, proveedor, precio):
        if modelo not in vistos:
            vistos.add(modelo)
            catalogo.append({'id': modelo, 'proveedor': proveedor, 'precio': precio})

    for modelo in utils_ia.MODELOS_SUGERIDOS:
        agregar(modelo, utils_ia.separar(modelo)[0], _precio(modelo))
    for fila in filas:
        for modelo in (fila.modelos or [])[:500]:
            completo = modelo if fila.proveedor == 'anthropic' else f'{fila.proveedor}:{modelo}'
            agregar(completo, fila.proveedor, _precio(completo))
    return catalogo


def _gasto_30_dias():
    """Costo estimado de la IA en la carga por factura (últimos 30 días), por
    paso y por modelo, según lo que cada sesión anotó en `uso`."""
    desde = timezone.now() - timedelta(days=30)
    total, sesiones, por_paso, por_modelo = 0.0, 0, {}, {}
    for uso in CargaFacturaPdf.objects.filter(creado_en__gte=desde).values_list('uso', flat=True):
        if not isinstance(uso, dict) or not uso.get('llamadas'):
            continue
        sesiones += 1
        total += float(uso.get('costo_usd') or 0)
        for paso in uso.get('pasos') or []:
            costo = float(paso.get('costo_usd') or 0)
            por_paso[paso.get('paso') or '?'] = por_paso.get(paso.get('paso') or '?', 0.0) + costo
            modelo = paso.get('modelo') or '(sin registro)'
            por_modelo[modelo] = por_modelo.get(modelo, 0.0) + costo
    redondear = lambda d: sorted(([k, round(v, 4)] for k, v in d.items()), key=lambda x: -x[1])
    return {'total': round(total, 4), 'sesiones': sesiones,
            'por_paso': redondear(por_paso), 'por_modelo': redondear(por_modelo)}


def _estado(request):
    utils_ia.olvidar_config()      # la pantalla muestra lo vigente, sin la caché de 15 s
    filas = {f.proveedor: f for f in ClaveProveedorIA.objects.select_related('actualizado_por')}
    guardados = dict(ModeloTareaIA.objects.values_list('tarea', 'modelo'))
    return {
        'proveedores': [_estado_proveedor(c, n, filas.get(c)) for c, n in PROVEEDOR_IA_CHOICES],
        'tareas': [_estado_tarea(c, n, guardados.get(c, '')) for c, n in TAREA_IA_CHOICES],
        'catalogo': _catalogo_modelos(filas.values()),
        'gasto': _gasto_30_dias(),
        'puede_editar': PermisoRol.tiene_permiso(
            usuario=request.user, codigo_opcion=CODIGO_PERMISO, tipo_permiso='puede_editar',
            sucursal_id=request.session.get('idSucursalActual')),
    }


# ---------------------------------------------------------------- vistas


@login_required
@requiere_permiso(CODIGO_PERMISO, 'puede_ver')
@require_http_methods(['GET'])
def inteligencia_artificial(request):
    estado = _estado(request)
    proveedores = estado['proveedores']
    return render(request, 'vistas/modulo_configuracion/inteligencia_artificial.html', {
        'datos_ia': estado,
        'kpi_con_clave': sum(1 for p in proveedores if p['origen']),
        'kpi_proveedores': len(proveedores),
        'kpi_tareas_pantalla': sum(1 for t in estado['tareas'] if t['origen'] == 'pantalla'),
        'kpi_tareas': len(estado['tareas']),
        'kpi_gasto': estado['gasto']['total'],
        'kpi_sesiones': estado['gasto']['sesiones'],
        'kpi_lector': next((t['usa_etiquetas'][0] or t['etiquetas'][0] for t in estado['tareas']
                            if t['codigo'] == 'lectura' and t['etiquetas']), ''),
    })


@login_required
@requiere_permiso(CODIGO_PERMISO, 'puede_editar')
@require_http_methods(['POST'])
def guardar_clave_ia(request):
    """Guarda (cifrada) la clave de un proveedor, o la quita.

    JSON: {proveedor, clave ('' = conservar la guardada), url_base,
    workspace_id, activa, quitar, forzar}. Una clave NUEVA se prueba contra el
    proveedor antes de guardarla: si la rechaza, no se guarda (salvo
    `forzar`, p. ej. si el proveedor está caído)."""
    datos = _json(request)
    if not isinstance(datos, dict):
        return _error('Petición inválida.')
    proveedor = str(datos.get('proveedor') or '').strip()
    if proveedor not in _PROVEEDORES_VALIDOS:
        return _error('Proveedor desconocido.')
    nombre = dict(PROVEEDOR_IA_CHOICES)[proveedor]

    if datos.get('quitar'):
        ClaveProveedorIA.objects.filter(proveedor=proveedor).delete()
        utils_ia.olvidar_config()
        logger.info('IA: %s quitó la clave de %s de la pantalla', request.user.username, proveedor)
        return JsonResponse({'ok': True, 'mensaje': f'Se quitó la clave de {nombre}. Si hay variable de '
                                                     f'entorno, se usa esa.',
                             'estado': _estado(request)})

    clave = str(datos.get('clave') or '').strip()
    url_base = str(datos.get('url_base') or '').strip().rstrip('/')
    workspace = str(datos.get('workspace_id') or '').strip()
    activa = bool(datos.get('activa', True))
    if clave and (len(clave) > 500 or any(c.isspace() for c in clave)):
        return _error('La clave no parece válida (tiene espacios o es demasiado larga).')
    if proveedor == 'compatible':
        if url_base and not re.match(r'^https?://[^\s]+$', url_base):
            return _error('La URL base debe empezar con http:// o https://.')
    else:
        url_base = ''
    if proveedor == 'anthropic':
        if workspace and not re.match(r'^[A-Za-z0-9_\-]{1,100}$', workspace):
            return _error('El id del workspace no es válido (se ve como wrkspc_…).')
    else:
        workspace = ''

    fila = ClaveProveedorIA.objects.filter(proveedor=proveedor).first()
    if not clave and not (fila and fila.clave_cifrada) and not (proveedor == 'compatible' and url_base):
        return _error('Escribe la clave.')

    # Cambiar la URL de la API compatible sin volver a escribir la clave
    # DESCARTA la guardada: la clave de un servidor nunca viaja a otro.
    url_cambia = proveedor == 'compatible' and fila is not None and url_base != (fila.url_base or '')
    clave_prueba = clave or ('' if url_cambia or not fila else fila.get_clave())
    workspace_cambia = (proveedor == 'anthropic' and fila is not None and bool(fila.clave_cifrada)
                        and workspace != (fila.workspace_id or ''))
    prueba = None
    if clave or (proveedor == 'compatible' and url_base) or workspace_cambia:
        prueba = utils_ia.probar(proveedor, clave=clave_prueba, url=url_base or None, workspace=workspace)
        if not prueba['ok'] and not datos.get('forzar'):
            return _error(f'No la guardé: {prueba["detalle"]}', puede_forzar=True)

    with transaction.atomic():
        fila = ClaveProveedorIA.objects.select_for_update().filter(proveedor=proveedor).first() \
            or ClaveProveedorIA(proveedor=proveedor)
        if clave:
            fila.set_clave(clave)
        elif url_cambia:
            fila.set_clave('')
        fila.url_base = url_base
        fila.workspace_id = workspace
        fila.activa = activa
        fila.actualizado_por = request.user
        if prueba is not None:
            fila.probada_en = timezone.now()
            fila.prueba_ok = prueba['ok']
            fila.prueba_detalle = prueba['detalle'][:300]
            if prueba['ok']:
                fila.modelos = prueba['modelos']
        fila.save()
    utils_ia.olvidar_config()
    logger.info('IA: %s guardó la clave de %s (activa=%s, prueba=%s)', request.user.username, proveedor,
                activa, None if prueba is None else prueba['ok'])
    mensaje = f'Clave de {nombre} guardada.'
    if prueba is not None:
        mensaje += ' ' + prueba['detalle']
    return JsonResponse({'ok': True, 'mensaje': mensaje, 'estado': _estado(request)})


@login_required
@requiere_permiso(CODIGO_PERMISO, 'puede_editar')
@require_http_methods(['POST'])
def probar_clave_ia(request):
    """Prueba la clave que se está usando (pantalla o variable de entorno) y,
    si es la de la pantalla, guarda el resultado y los modelos que ofrece."""
    datos = _json(request)
    if not isinstance(datos, dict):
        return _error('Petición inválida.')
    proveedor = str(datos.get('proveedor') or '').strip()
    if proveedor not in _PROVEEDORES_VALIDOS:
        return _error('Proveedor desconocido.')
    utils_ia.olvidar_config()
    prueba = utils_ia.probar(proveedor)
    fila = ClaveProveedorIA.objects.filter(proveedor=proveedor).first()
    if fila and utils_ia.origen_clave(proveedor) == 'pantalla':
        fila.probada_en = timezone.now()
        fila.prueba_ok = prueba['ok']
        fila.prueba_detalle = prueba['detalle'][:300]
        campos = ['probada_en', 'prueba_ok', 'prueba_detalle', 'actualizado_en']
        if prueba['ok']:
            fila.modelos = prueba['modelos']
            campos.append('modelos')
        fila.save(update_fields=campos)
    return JsonResponse({'ok': prueba['ok'], 'detalle': prueba['detalle'],
                         'modelos': prueba['modelos'][:500], 'estado': _estado(request)})


def _validar_modelo(texto, lista=False):
    """(valor normalizado, errores, avisos) de lo escrito para una tarea."""
    errores, avisos = [], []
    grupos = [g.strip() for g in texto.split(';')] if lista else [texto.strip()]
    limpios = []
    for grupo in grupos:
        if not grupo:
            continue
        modelos = utils_ia.cadena(grupo)
        for m in modelos:
            if not MODELO_RE.match(m):
                errores.append(f'«{m}» no es un nombre de modelo válido.')
                continue
            prefijo, sep, _resto = m.partition(':')
            if sep and prefijo.lower() not in _PROVEEDORES_VALIDOS:
                errores.append(f'«{prefijo}» no es un proveedor conocido (usa anthropic:, openai:, gemini:, '
                               f'deepseek:, openrouter: o compatible:).')
                continue
            proveedor, nombre = utils_ia.separar(m)
            if not nombre:
                errores.append(f'«{m}» no trae el nombre del modelo.')
            elif not utils_ia.configurado(m):
                avisos.append(f'{nombre} ({utils_ia.nombre_proveedor(proveedor)}) no tiene clave: '
                              f'se saltará al respaldo.')
        limpios.append(','.join(modelos))
    valor = ';'.join(limpios) if lista else (limpios[0] if limpios else '')
    if len(valor) > 255:
        errores.append('Demasiado largo (máximo 255 caracteres).')
    return valor, errores, avisos


@login_required
@requiere_permiso(CODIGO_PERMISO, 'puede_editar')
@require_http_methods(['POST'])
def guardar_modelos_ia(request):
    """JSON {tareas: {codigo: 'modelo[,respaldo]' | ''}}. Vacío = volver a la
    variable de entorno / modelo por defecto (se borra la fila)."""
    datos = _json(request)
    tareas = (datos or {}).get('tareas') if isinstance(datos, dict) else None
    if not isinstance(tareas, dict):
        return _error('Petición inválida.')
    validas = {c for c, _ in TAREA_IA_CHOICES}
    errores, avisos, cambios = {}, [], {}
    for codigo, texto in tareas.items():
        if codigo not in validas:
            return _error(f'Tarea desconocida: {codigo}.')
        valor, errs, avs = _validar_modelo(str(texto or ''), lista=bool(utils_ia.TAREAS[codigo].get('lista')))
        if errs:
            errores[codigo] = errs
        else:
            cambios[codigo] = valor
            avisos += [f'{dict(TAREA_IA_CHOICES)[codigo]}: {a}' for a in avs]
            primero = (utils_ia.cadena(valor.split(';')[0]) or [''])[0]
            if codigo == 'busqueda' and primero and utils_ia.separar(primero)[0] not in _BUSCAN_EN_INTERNET:
                avisos.append('Búsqueda en internet: ese proveedor no busca en internet; se usará su respaldo.')
    if errores:
        return _error('Revisa los modelos marcados.', errores=errores)
    with transaction.atomic():
        for codigo, valor in cambios.items():
            if valor:
                ModeloTareaIA.objects.update_or_create(
                    tarea=codigo, defaults={'modelo': valor, 'actualizado_por': request.user})
            else:
                ModeloTareaIA.objects.filter(tarea=codigo).delete()
    utils_ia.olvidar_config()
    logger.info('IA: %s guardó modelos por tarea: %s', request.user.username, cambios)
    return JsonResponse({'ok': True, 'mensaje': 'Modelos guardados.', 'avisos': avisos,
                         'estado': _estado(request)})
