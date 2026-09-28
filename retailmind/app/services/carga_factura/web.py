"""
Agente "Cargar desde factura" de la pantalla Gestión de Productos: lo que hay
entre la conversación (modelo CargaFacturaPdf) y el motor de carga.

  - iniciar_lectura(sesion_id): lee el PDF con Claude en segundo plano y deja
    en el expediente una factura por cada factura del PDF (mismo JSON que
    compras/facturas/*.json).
  - aplicar_correcciones(sesion, cambios): lo que la persona corrigió en la
    vista previa (precio, género, categoría, ficha en la que entra, tallas…).
  - planificar(sesion, user): la vista previa de cada factura, ya sin objetos
    de modelo, lista para mandarla a la pantalla.
  - iniciar_carga(sesion_id, idx, opciones, user_id): carga una factura en
    segundo plano por el MISMO camino que el modal (aplicador).

Todo lo que decide qué es nuevo, qué existe y con qué precios entra vive en
planificador.py; aquí solo se orquesta y se traduce a JSON.
"""
import logging
import threading
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from app.models import (
    AtributoOpcion, CargaFacturaPdf, Dte, GuiaTalla, PerfilCargaMarca, ProductoAprendido,
)
from app.utils_producto_match import normalizar_articulo

from . import lectura as svc_lectura
from .aplicador import aplicar_linea
from .facturas import (
    DteAmbiguo, DteNoEncontrado, ErrorCarga, factura_desde_datos, variantes_rut,
)
from .perfiles import clave_marca, perfil_para
from .planificador import (
    ATRIBUTOS_GENERO, PlanificadorCarga, estado_visible, opciones_existente,
    tallas_y_stock,
)
from .precios import fmt
from .tallas import COLUMNAS_GUIA

logger = logging.getLogger('app')

AGENTE = 'agente'
USUARIO = 'usuario'

# En tests se ponen en True para que la lectura y la carga corran en el mismo
# hilo (el hilo de fondo no vería la transacción del test).
SINCRONO = False

# Campos que la vista previa (tarjeta o chat) deja corregir. Cualquier otra
# clave del JSON (RUT, sucursal, totales…) se conserva tal como se leyó.
# Los que empiezan con "_" son parámetros de esta carga que el JSON de
# compras/facturas no trae: pisan la regla del perfil de la marca.
CAMPOS_FACTURA_EDITABLES = ('marca', 'color', 'dte_id', 'folio', 'proveedor_rut',
                            'fecha_emision', 'tipo_talla', 'guias_talla', '_renombrar_tallas',
                            '_umbral_costo', '_factor_bajo', '_factor_alto', '_margen_sobreprecio')
CAMPOS_LINEA_EDITABLES = ('articulo', 'descripcion', 'costo', 'precioventa', 'genero',
                          'categoria', 'color', 'marca', 'especialidades', 'ficha_id',
                          'guia', 'tallas', 'cantidad', 'importe', '_omitir')
_ENTEROS = ('costo', 'precioventa', 'ficha_id', 'dte_id', 'cantidad', 'importe', 'folio',
            '_umbral_costo')
_DECIMALES = ('_factor_bajo', '_factor_alto', '_margen_sobreprecio')
TIPOS_TALLA = tuple(c.upper() for c in COLUMNAS_GUIA)
GENEROS_GUIA = ('HOMBRE', 'MUJER', 'UNISEX', 'INFANTIL', 'DEFAULT')


# ------------------------------------------------------------------ hilos


def _lanzar(fn, *args):
    if SINCRONO:
        fn(*args)
        return
    threading.Thread(target=fn, args=args, daemon=True).start()


def _cerrar_conexion():
    """Cada hilo abre su propia conexión: se cierra al terminar. En modo
    síncrono (tests) no: cerraría la conexión dentro de la transacción del test."""
    if not SINCRONO:
        connection.close()


def _progreso(sesion_id):
    def avisar(texto):
        CargaFacturaPdf.objects.filter(id=sesion_id).update(
            progreso=str(texto)[:255], actualizado_en=timezone.now())
    return avisar


def _guardar_factura(sesion_id, idx, data):
    """Reemplaza la factura `idx` leyendo el expediente fresco (otro hilo
    puede haber escrito mensajes o progreso mientras tanto)."""
    sesion = CargaFacturaPdf.objects.get(id=sesion_id)
    facturas = list(sesion.facturas or [])
    facturas[idx] = data
    sesion.facturas = facturas
    sesion.save(update_fields=['facturas', 'actualizado_en'])
    return sesion


def _sumar_uso(sesion_id, paso, uso):
    """Acumula en sesion.uso lo consumido en un paso (lectura, chat, búsqueda)."""
    if not uso or not uso.get('llamadas'):
        return uso
    sesion = CargaFacturaPdf.objects.get(id=sesion_id)
    total = dict(sesion.uso or {})
    for k, v in uso.items():
        if k == 'costo_usd':
            total[k] = round(float(total.get(k) or 0) + float(v or 0), 4)
        else:
            total[k] = int(total.get(k) or 0) + int(v or 0)
    pasos = list(total.get('pasos') or [])
    pasos.append({'paso': paso, 'fecha': timezone.now().isoformat(timespec='seconds'), **uso})
    total['pasos'] = pasos[-40:]
    sesion.uso = total
    sesion.save(update_fields=['uso', 'actualizado_en'])
    return uso


# Sin señal del hilo (progreso, mensajes, facturas) en este tiempo, la lectura
# o la carga murieron con el proceso (deploy / reinicio del servidor): el hilo
# es daemon y nadie la retoma. Una lectura larga avisa al empezar cada pasada.
TIEMPO_MAX_SIN_SENAL = timedelta(minutes=30)


def revisar_interrumpida(sesion):
    """Si la sesión lleva demasiado LEYENDO / CARGANDO sin señal, la cierra en
    un estado del que la persona pueda seguir. Devuelve True si la cambió."""
    if sesion.estado not in ('LEYENDO', 'CARGANDO', 'BUSCANDO'):
        return False
    if timezone.now() - sesion.actualizado_en <= TIEMPO_MAX_SIN_SENAL:
        return False
    if sesion.estado == 'BUSCANDO':
        sesion.estado = 'LEIDA'
        sesion.progreso = ''
        sesion.save(update_fields=['estado', 'progreso', 'actualizado_en'])
        sesion.agregar_mensaje(AGENTE, 'La búsqueda en internet se interrumpió (el servidor se '
                               'reinició). Puedes pedirla de nuevo.', tipo='error')
        return True
    if sesion.estado == 'LEYENDO':
        sesion.estado = 'ERROR'
        sesion.error = 'La lectura se interrumpió (el servidor se reinició a mitad de camino).'
        sesion.progreso = ''
        sesion.save(update_fields=['estado', 'error', 'progreso', 'actualizado_en'])
        sesion.agregar_mensaje(AGENTE, 'La lectura se interrumpió (el servidor se reinició a mitad '
                               'de camino). Vuelve a subir el PDF: no se cargó nada.', tipo='error')
        return True
    facturas = list(sesion.facturas or [])
    for d in facturas:
        if d.get('_estado') == 'CARGANDO':
            d['_estado'] = 'PARCIAL'
    sesion.facturas = facturas
    sesion.estado = 'LEIDA'
    sesion.progreso = ''
    sesion.save(update_fields=['facturas', 'estado', 'progreso', 'actualizado_en'])
    sesion.agregar_mensaje(AGENTE, 'La carga se interrumpió (el servidor se reinició a mitad de '
                           'camino). Las líneas que alcanzaron a entrar aparecen como YA CARGADO '
                           'en la vista previa; aprieta «Cargar» de nuevo para las que faltan.',
                           tipo='error')
    logger.warning('carga_factura: sesión %s marcada como interrumpida', sesion.id)
    return True


# ---------------------------------------------------------------- lectura


def iniciar_lectura(sesion_id):
    _lanzar(leer_en_segundo_plano, sesion_id)


def leer_en_segundo_plano(sesion_id):
    """Hilo: PDF → facturas en el expediente (estado LEIDA) o ERROR."""
    try:
        sesion = CargaFacturaPdf.objects.select_related('sucursal').get(id=sesion_id)
        avisar = _progreso(sesion_id)
        with sesion.archivo.open('rb') as fh:
            pdf = fh.read()
        # Indicaciones que la persona escribió al subir ("la marca es X, las
        # tallas son CL…"): van al lector junto con las pistas del perfil.
        pistas = next((m.get('texto', '') for m in reversed(sesion.mensajes or [])
                       if m.get('tipo') == 'indicaciones'), '')
        svc_lectura.uso_iniciar()
        leido = svc_lectura.leer_pdf(pdf, marca=sesion.marca or None,
                                     lecturas=sesion.lecturas, progreso=avisar, pistas=pistas)
        avisar('Comparando las lecturas…')
        consolidada = svc_lectura.combinar_lecturas(leido['lecturas'])
        facturas = consolidada.get('facturas', [])
        if not facturas:
            raise ErrorCarga('No encontré ninguna factura en el documento.')
        datos, aprendido = [], []
        for factura in facturas:
            d = svc_lectura.a_json_de_carga(
                factura, sesion.sucursal.alias, marca=sesion.marca or None,
                fuente=f'{sesion.nombre_archivo}, leída con {svc_lectura.MODELO} '
                       f'({len(leido["lecturas"])} lectura(s))')
            d['_estado'] = 'PENDIENTE'
            # Lo confirmado en cargas anteriores del mismo código (género,
            # categoría…) manda sobre lo que el lector supuso.
            try:
                aprendido += aplicar_aprendido(d)
            except Exception:
                logger.exception('carga_factura: no se pudo aplicar lo aprendido (sesión %s)', sesion_id)
            datos.append(d)
        uso = _sumar_uso(sesion_id, 'lectura', svc_lectura.uso_actual())
        sesion.refresh_from_db()
        sesion.facturas = datos
        sesion.estado = 'LEIDA'
        sesion.modelo = svc_lectura.MODELO
        sesion.leida_en = timezone.now()
        sesion.progreso = ''
        sesion.error = ''
        sesion.save(update_fields=['facturas', 'estado', 'modelo', 'leida_en', 'progreso',
                                   'error', 'actualizado_en'])
        sesion.agregar_mensaje(AGENTE, _texto_lectura(datos, leido['modo'], leido.get('segunda'),
                                                      leido.get('verificadas')),
                               tipo='lectura', uso=uso, aprendido=aprendido,
                               lecturas=len(leido['lecturas']), segunda=leido.get('segunda'),
                               verificadas=leido.get('verificadas'))
    except Exception as exc:
        mensaje = str(exc) if isinstance(exc, ErrorCarga) else f'{type(exc).__name__}: {exc}'
        logger.exception('carga_factura: falló la lectura de la sesión %s', sesion_id)
        CargaFacturaPdf.objects.filter(id=sesion_id).update(
            estado='ERROR', error=mensaje[:2000], progreso='', actualizado_en=timezone.now())
        try:
            CargaFacturaPdf.objects.get(id=sesion_id).agregar_mensaje(
                AGENTE, f'No pude leer el PDF: {mensaje}', tipo='error')
        except Exception:
            pass
    finally:
        _cerrar_conexion()


_TEXTO_SEGUNDA = {
    'no hizo falta': 'La primera lectura cuadró completa, así que no hizo falta verificar nada.',
    'por dudas': 'La primera lectura dejó dudas, así que hice una segunda y las comparé.',
    'verificación': 'La primera lectura dejó dudas en {n} línea(s): las volví a mirar con zoom y comparé.',
    'siempre': 'Hice dos lecturas completas independientes y las comparé.',
}


def _texto_lectura(facturas, modo, segunda=None, verificadas=None):
    partes = [f'Leí el PDF ({"escaneo" if modo == "escaneo" else "PDF con texto"}) y encontré '
              f'{len(facturas)} factura(s):']
    if _TEXTO_SEGUNDA.get(segunda):
        partes[0] += ' ' + _TEXTO_SEGUNDA[segunda].format(n=verificadas or '?')
    for d in facturas:
        lineas = d['lineas']
        unidades = sum(sum(l['tallas'].values()) for l in lineas)
        neto = sum(int(l.get('importe') or 0) for l in lineas)
        a_mano = sum(1 for l in lineas if l.get('precioventa'))
        dudas = sum(1 for l in lineas if l.get('_revisar') or l.get('_precio_duda'))
        partes.append(
            f'• N° {d["folio"]} de {d.get("proveedor_nombre") or d["proveedor_rut"]} '
            f'({d["fecha_emision"]}): {len(lineas)} línea(s), {unidades} unidades, '
            f'neto ${fmt(neto)}'
            + (f', precio de venta a mano en {a_mano}' if a_mano else '')
            + (f', {dudas} línea(s) para revisar' if dudas else '')
            + (f', {len(d["_revisar"])} aviso(s) de cuadre' if d.get('_revisar') else '') + '.')
    partes.append('Abajo va la vista previa de cada una: revisa lo marcado, corrige lo que '
                  'haga falta y cuando esté bien aprieta «Cargar». Si algún artículo no trae '
                  'color, dime «busca en internet la línea N» (o «todas») y lo averiguo.')
    return '\n'.join(partes)


# ------------------------------------------------------------- aprendizaje


def _ruta_categoria(cat):
    if cat is None:
        return ''
    return (f'{cat.padre.nombre} > ' if cat.padre_id else '') + cat.nombre


def aplicar_aprendido(data):
    """Rellena en las líneas lo confirmado en cargas anteriores del mismo
    código (ProductoAprendido): género, categoría, especialidades y, si el
    código lleva el color (Nike), el color. El último precio de venta y lo
    encontrado en internet se dejan como pista (no se imponen).
    Devuelve textos «código: campos» de lo aplicado."""
    marca = clave_marca(data.get('marca'))
    if not marca:
        return []
    perfil = perfil_para(data.get('marca'))
    lineas = data.get('lineas') or []
    codigos = {normalizar_articulo(l.get('articulo')) for l in lineas}
    aprendidos = {p.articulo: p for p in ProductoAprendido.objects.filter(marca=marca, articulo__in=codigos)}
    if not aprendidos:
        return []
    aplicados = []
    for l in lineas:
        p = aprendidos.get(normalizar_articulo(l.get('articulo')))
        if p is None:
            continue
        campos = []
        if p.genero and l.get('genero') != p.genero:
            l['genero'] = p.genero
            campos.append('género')
        if p.categoria and l.get('categoria') != p.categoria:
            l['categoria'] = p.categoria
            campos.append('categoría')
        if p.especialidades and not l.get('especialidades'):
            l['especialidades'] = list(p.especialidades)
            campos.append('especialidades')
        color = p.color or p.color_internet
        if color and not l.get('color') and not perfil.identidad_color:
            l['color'] = color
            campos.append('color')
        if p.precioventa:
            l['_precio_aprendido'] = p.precioventa
        que_es = ': '.join(t for t in (p.nombre_internet, p.que_es) if t)
        if que_es:
            l['_que_es'] = que_es
        if campos:
            l['_aprendido'] = campos
            aplicados.append(f'{l.get("articulo")}: {", ".join(campos)}')
    if aplicados:
        ProductoAprendido.objects.filter(marca=marca, articulo__in=codigos).update(
            veces_usado=F('veces_usado') + 1)
    return aplicados


def aprender_de_carga(data, planes, resultado, user=None):
    """Guarda lo que esta carga confirmó: el perfil de la marca (tipo de talla,
    guías, regla de precio, margen, color por defecto) y, por cada línea que
    entró, su clasificación final y precios. Devuelve textos para el chat."""
    marca = clave_marca(data.get('marca'))
    if not marca:
        return []
    perfil = perfil_para(data.get('marca'))
    aprendido = []
    defaults = {'nombre': str(data.get('marca') or '').strip().upper(),
                'ultima_factura': str(data.get('folio') or '')[:60]}
    if user is not None and getattr(user, 'pk', None):
        defaults['actualizado_por'] = user
    if data.get('tipo_talla'):
        defaults['tipo_talla'] = str(data['tipo_talla']).upper()[:5]
    if data.get('guias_talla'):
        defaults['guias_talla'] = {str(k).upper(): v for k, v in data['guias_talla'].items() if v}
    if data.get('color'):
        defaults['color_defecto'] = str(data['color']).upper()[:100]
    for campo, destino in (('_umbral_costo', 'umbral_costo'), ('_factor_bajo', 'factor_bajo'),
                           ('_factor_alto', 'factor_alto'), ('_margen_sobreprecio', 'margen_sobreprecio')):
        if data.get(campo) not in (None, ''):
            defaults[destino] = data[campo]
    fila, _creado = PerfilCargaMarca.objects.update_or_create(marca=marca, defaults=defaults)
    PerfilCargaMarca.objects.filter(id=fila.id).update(veces_usado=F('veces_usado') + 1)
    detalle = [f'tallas {fila.tipo_talla or perfil.tipo_talla}']
    if fila.guias_talla:
        detalle.append('guías ' + ', '.join(sorted(set(fila.guias_talla.values()))))
    if fila.factor_bajo or fila.factor_alto:
        detalle.append(f'venta ×{fila.factor_bajo or perfil.factor_bajo} / ×{fila.factor_alto or perfil.factor_alto}')
    detalle.append('color aparte del código' if perfil.identidad_color else 'color en el código')
    aprendido.append(f'{fila.nombre or marca}: ' + ', '.join(detalle))

    ok = {f['n'] for f in resultado.get('lineas', []) if f.get('estado') == 'OK'}
    for plan in planes:
        if plan['n'] not in ok:
            continue
        linea = plan['linea']
        defaults = {
            'descripcion': str(linea.get('descripcion') or '')[:255],
            'genero': str(getattr(plan['genero'], 'valor', '') or '')[:50],
            'categoria': _ruta_categoria(plan['categoria'])[:150],
            'especialidades': [o.valor for o in plan['especialidades']],
            'precioventa': plan['factura'][2], 'costo': plan['factura'][0],
            'fuente': 'carga',
        }
        if not perfil.identidad_color:
            # El código lleva el color: se aprende, salvo el por defecto (MULTI),
            # que solo dice «no se sabía».
            color = str(getattr(plan['color'], 'valor', '') or '').strip().upper()
            defaults['color'] = '' if color == str(perfil.color_defecto).upper() else color[:100]
        fila, _creado = ProductoAprendido.objects.update_or_create(
            marca=marca, articulo=plan['articulo'], defaults=defaults)
        aprendido.append(f'{plan["articulo"]}: ' + ' / '.join(
            t for t in (fila.genero, fila.categoria.split(' > ')[-1] if fila.categoria else '',
                        fila.color) if t))
    return aprendido


def recordar_para_marca(marca, texto, user=None):
    """«Recuerda que en Chalada el color va en la descripción»: queda como
    pista del lector para esa marca (PerfilCargaMarca.pistas_lectura)."""
    clave = clave_marca(marca)
    texto = ' '.join(str(texto or '').split())[:500]
    if not clave or not texto:
        return None
    fila, _creado = PerfilCargaMarca.objects.get_or_create(
        marca=clave, defaults={'nombre': str(marca).strip().upper()})
    pistas = [p for p in fila.pistas_lectura.splitlines() if p.strip()]
    if texto not in pistas:
        pistas.append(texto)
        fila.pistas_lectura = '\n'.join(pistas[-20:])
        if user is not None and getattr(user, 'pk', None):
            fila.actualizado_por = user
        fila.save(update_fields=['pistas_lectura', 'actualizado_por', 'actualizado_en'])
    return f'{fila.nombre or clave}: «{texto}»'


# ------------------------------------------------------------ correcciones


def _entero(valor):
    # Ojo: `0 in (None, '', False)` es True en Python (0 == False); se compara
    # por identidad para que el índice 0 y un costo 0 sigan siendo números.
    if valor is None or valor is False or (isinstance(valor, str) and not valor.strip()):
        return None
    try:
        return int(str(valor).replace('.', '').replace(',', '').strip())
    except ValueError:
        raise ErrorCarga(f'{valor!r} no es un número')


def _decimal(valor):
    """'1,9' → '1.9' (se guarda como texto: el JSON no tiene Decimal)."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    try:
        d = Decimal(str(valor).replace(',', '.').strip())
    except InvalidOperation:
        raise ErrorCarga(f'{valor!r} no es un número')
    if d <= 0:
        raise ErrorCarga(f'{valor!r} tiene que ser mayor que 0')
    return format(d.normalize(), 'f')


def _guias_talla(valor):
    """{'HOMBRE': 'NIKE HOMBRE', ...} desde un dict o una lista [{genero, guia}]."""
    if not valor:
        return None
    pares = valor.items() if isinstance(valor, dict) else [
        (p.get('genero'), p.get('guia')) for p in valor if isinstance(p, dict)]
    guias = {}
    for genero, guia in pares:
        genero = str(genero or '').strip().upper()
        guia = str(guia or '').strip()
        if genero not in GENEROS_GUIA:
            raise ErrorCarga(f'género de guía {genero!r} no válido (usa {", ".join(GENEROS_GUIA)})')
        if guia:
            guias[genero] = guia
    return guias or None


def _tallas(valor):
    """{'7': 2, '7.5': 3} desde un dict, o desde texto 'talla cantidad' por línea."""
    if isinstance(valor, dict):
        pares = valor.items()
    else:
        pares = []
        for fila in str(valor or '').replace(',', '.').splitlines():
            trozos = fila.replace('×', ' ').replace('x', ' ').replace(':', ' ').split()
            if not trozos:
                continue
            if len(trozos) != 2:
                raise ErrorCarga(f'Talla mal escrita: «{fila.strip()}» (usa «talla cantidad»)')
            pares.append((trozos[0], trozos[1]))
    tallas = {}
    for talla, cant in pares:
        talla = str(talla).strip().upper()
        cant = _entero(cant) or 0
        if not talla:
            continue
        if cant <= 0:
            raise ErrorCarga(f'La talla {talla} tiene cantidad {cant}')
        tallas[talla] = tallas.get(talla, 0) + cant
    return tallas


def aplicar_correcciones(sesion, cambios):
    """Copia al expediente lo corregido en la vista previa.

    `cambios` = [{'idx': n, 'lineas': [{...}, ...], <campos de factura>}]. Solo
    se copian los campos editables; las líneas se emparejan por posición.
    Devuelve True si cambió algo. Lanza ErrorCarga con el dato mal escrito.

    El chat llama con una sesión leída ANTES de esperar a Claude: el estado y
    las facturas se validan y se corrigen sobre la fila fresca y bloqueada,
    así una carga que terminó mientras tanto no se pisa (su _estado y su
    _resultado quedan). El objeto `sesion` sale con lo guardado.
    """
    with transaction.atomic():
        fresca = CargaFacturaPdf.objects.select_for_update().get(id=sesion.id)
        tocado = _corregir_facturas(fresca, cambios)
    sesion.estado = fresca.estado
    sesion.facturas = fresca.facturas
    sesion.actualizado_en = fresca.actualizado_en
    return tocado


def _corregir_facturas(sesion, cambios):
    """aplicar_correcciones sobre la sesión ya bloqueada."""
    if sesion.estado != 'LEIDA':
        raise ErrorCarga('La sesión no está en vista previa: no se puede corregir ahora.')
    facturas = list(sesion.facturas or [])
    tocado = False
    for cambio in cambios or []:
        idx = _entero(cambio.get('idx'))
        if idx is None or not 0 <= idx < len(facturas):
            raise ErrorCarga(f'No existe la factura {idx} en esta sesión')
        data = dict(facturas[idx])
        if data.get('_estado') == 'CARGADA':
            raise ErrorCarga(f'La factura {data.get("folio")} ya se cargó: no se edita.')
        for campo in CAMPOS_FACTURA_EDITABLES:
            if campo not in cambio:
                continue
            valor = cambio[campo]
            if campo in _ENTEROS:
                valor = _entero(valor)
            elif campo in _DECIMALES:
                valor = _decimal(valor)
            elif campo == '_renombrar_tallas':
                valor = bool(valor)
            elif campo == 'tipo_talla':
                valor = str(valor or '').strip().upper() or None
                if valor and valor not in TIPOS_TALLA:
                    raise ErrorCarga(f'tipo de talla {valor!r} no válido (usa {", ".join(TIPOS_TALLA)})')
            elif campo == 'guias_talla':
                valor = _guias_talla(valor)
            else:
                valor = str(valor or '').strip().upper() if campo != 'fecha_emision' else str(valor or '').strip()
            if data.get(campo) != valor:
                if valor is None and campo.startswith('_') or valor is None and campo in ('tipo_talla', 'guias_talla'):
                    data.pop(campo, None)
                else:
                    data[campo] = valor
                tocado = True
        lineas = list(data.get('lineas') or [])
        for n, cambio_linea in enumerate(cambio.get('lineas') or []):
            if n >= len(lineas) or not isinstance(cambio_linea, dict):
                continue
            linea = dict(lineas[n])
            for campo in CAMPOS_LINEA_EDITABLES:
                if campo not in cambio_linea:
                    continue
                valor = cambio_linea[campo]
                if campo in _ENTEROS:
                    valor = _entero(valor)
                elif campo == 'tallas':
                    valor = _tallas(valor)
                elif campo == 'especialidades':
                    valor = [str(v).strip() for v in (valor or []) if str(v).strip()]
                elif campo == '_omitir':
                    valor = bool(valor)
                elif campo in ('articulo', 'marca', 'color', 'genero'):
                    valor = str(valor or '').strip().upper() or None
                else:
                    valor = str(valor or '').strip() or None
                if valor is None and campo in ('articulo', 'descripcion', 'tallas'):
                    continue
                if linea.get(campo) != valor:
                    if valor is None:
                        linea.pop(campo, None)
                    else:
                        linea[campo] = valor
                    tocado = True
            lineas[n] = linea
        data['lineas'] = lineas
        facturas[idx] = data
    if tocado:
        sesion.facturas = facturas
        sesion.save(update_fields=['facturas', 'actualizado_en'])
    return tocado


# ------------------------------------------------------------ vista previa


def _op(opcion):
    return {'id': opcion.id, 'valor': opcion.valor} if opcion is not None else None


def _ficha(f):
    if f is None:
        return None
    n_tallas, stock = tallas_y_stock(f)
    return {
        'id': f.id, 'sucursal': f.sucursal.alias, 'descripcion': f.descripcion,
        'marca': getattr(f.atributo1, 'valor', None), 'color': getattr(f.atributo2, 'valor', None),
        'genero': getattr(f.atributo3, 'valor', None),
        'categoria': getattr(f.categoria, 'nombre', None),
        'tipo_talla': f.tipo_talla, 'guia': getattr(f.guia_talla, 'nombre', None),
        'tallas': n_tallas, 'stock': stock,
        'costo': int(f.costo or 0), 'precioventa': int(f.precioventa or 0),
    }


def _dte(dte):
    return {
        'id': dte.id, 'numero': dte.numero_documento, 'tipo': dte.tipo_documento,
        'transaccion': dte.tipo_transaccion, 'emisor': dte.emisor.nombre,
        'rut': dte.emisor.rut, 'fecha': str(dte.fecha_emision),
        'neto': int(dte.monto_neto or 0), 'descartado': bool(getattr(dte, 'descartado', False)),
    }


def _candidatos_dte(data):
    """DTE del sistema con ese folio (cualquier emisor y tipo) o del mismo
    RUT, para elegir el correcto cuando no se encontró la factura."""
    qs = Dte.objects.select_related('emisor').order_by('-id')
    folio = _entero(data.get('folio'))
    vistos, salida = set(), []
    consultas = []
    if folio:
        consultas.append(qs.filter(numero_documento=folio))
    if data.get('proveedor_rut'):
        try:
            consultas.append(qs.filter(emisor__rut__in=variantes_rut(data['proveedor_rut']),
                                       tipo_transaccion='COMPRA')[:8])
        except (ValueError, IndexError):
            pass
    for consulta in consultas:
        for d in consulta[:12]:
            if d.id in vistos:
                continue
            vistos.add(d.id)
            salida.append(_dte(d))
    return salida


# Pantalla donde se registra a mano un DTE de compra (el agente no lo crea:
# la lectura no trae total con IVA ni vencimiento).
URL_GESTION_DTE_COMPRAS = '/app/verGestionDteCompras/'


def datos_para_registrar(data):
    """Lo leído de la factura para registrarla en Gestión Documentos Compras
    (la tarjeta lo muestra y lo manda en el querystring del enlace)."""
    datos = {'folio': data.get('folio'), 'rut': data.get('proveedor_rut'),
             'proveedor': data.get('proveedor_nombre'), 'fecha': data.get('fecha_emision'),
             'neto': data.get('total_neto')}
    params = {'nuevo': 1, **{k: v for k, v in datos.items()
                             if k != 'proveedor' and v not in (None, '')}}
    return {**datos, 'url': f'{URL_GESTION_DTE_COMPRAS}?{urlencode(params)}'}


def _plan(plan):
    linea = plan['linea']
    costo, sobre, pv = plan['factura']
    vigentes = plan['vigentes']
    return {
        'n': plan['n'], 'articulo': plan['articulo'], 'estado': estado_visible(plan),
        'omitida': bool(linea.get('_omitir')),
        'descripcion': linea.get('descripcion', ''), 'unidades': plan['unidades'],
        'costo': costo, 'sobreprecio': sobre, 'precioventa': pv, 'fuente_pv': plan['fuente_pv'],
        'precio_lista': linea.get('precio_lista'), 'descuento': linea.get('descuento'),
        'precioventa_a_mano': linea.get('precioventa'),
        'vigentes': ({'costo': vigentes[0], 'sobreprecio': vigentes[1], 'precioventa': vigentes[2]}
                     if vigentes is not None else None),
        'opciones': opciones_existente(plan) if vigentes is not None else None,
        'opcion_sugerida': opcion_por_defecto(plan) if vigentes is not None else None,
        'marca': _op(plan['marca']), 'color': _op(plan['color']), 'genero': _op(plan['genero']),
        'categoria': ({'id': plan['categoria'].id, 'nombre': plan['categoria'].nombre,
                       'ruta': (f'{plan["categoria"].padre.nombre} > ' if plan['categoria'].padre_id else '')
                               + plan['categoria'].nombre}
                      if plan['categoria'] is not None else None),
        'especialidades': [o.valor for o in plan['especialidades']],
        'tipo_talla': plan.get('tipo_talla'),
        'guia': ({'id': plan['guia'].id, 'nombre': plan['guia'].nombre} if plan.get('guia') else None),
        'tallas': [{'factura': f, 'ficha': final, 'cantidad': c, 'existe': e}
                   for f, final, c, e in plan['tallas']],
        'destino': _ficha(plan['destino']),
        'referencia': _ficha(plan['referencia']) if plan['referencia'] is not plan['destino'] else None,
        'gemelas': [{'sucursal': a, 'costo': c, 'precioventa': v, 'misma_identidad': m}
                    for a, c, v, m in plan['gemelas']],
        'renombres': [{'sucursal': a, 'de': viejo, 'a': nuevo}
                      for _p, a, _pt, viejo, nuevo in plan.get('renombres', [])],
        'conflictos': [{'sucursal': a, 'de': viejo, 'a': nuevo, 'stock': s}
                       for a, viejo, nuevo, s in plan.get('conflictos', [])],
        'sin_resolver': [{'sucursal': a, 'talla': t} for a, t in plan.get('sin_resolver', [])],
        'fichas_formato': [f'{f.sucursal.alias} #{f.id}' for f in plan.get('fichas_formato', [])],
        'candidatas': [_ficha(f) for f in plan.get('candidatas', [])],
        'avisos': list(plan['avisos']), 'errores': list(plan['errores']),
        # Lo aprendido de cargas anteriores / internet (pistas para la persona).
        'aprendido': list(linea.get('_aprendido') or []),
        'precio_aprendido': linea.get('_precio_aprendido'),
        'que_es': linea.get('_que_es'),
        # Lo que dice hoy el JSON (para los editores de la vista previa).
        'json': {campo: linea.get(campo) for campo in CAMPOS_LINEA_EDITABLES},
    }


def opcion_por_defecto(plan):
    """Opción sugerida para un código existente: si la venta de la factura es
    MENOR que la vigente, se conserva la venta ([c]: stock + costo); si sube o
    es igual, [s]. Regla de la casa: no bajar precios sin que alguien lo pida."""
    disponibles = opciones_existente(plan)
    if not disponibles:
        return 't'
    _c0, _s0, v0 = plan['vigentes']
    _c1, _s1, v1 = plan['factura']
    if v1 < v0:
        # [c] si el costo cambió; si no cambió, [t] (solo stock) conserva la venta.
        for opcion in ('c', 't'):
            if opcion in disponibles:
                return opcion
    return disponibles[0]


def _bloqueante(plan):
    """Línea con error que impide cargar la factura (las omitidas y las ya
    cargadas no bloquean)."""
    return bool(plan['errores']) and plan['estado'] != 'YA_CARGADO' and not plan['linea'].get('_omitir')


def _totales(f, planes):
    data, dte = f['data'], f['dte']
    activos = [p for p in planes if not p['linea'].get('_omitir')]
    unidades = sum(p['unidades'] for p in activos)
    neto = sum(int(p['linea'].get('costo') or 0) * p['unidades'] for p in activos)
    por_estado = {}
    for p in planes:
        clave = 'OMITIDA' if p['linea'].get('_omitir') else estado_visible(p)
        por_estado[clave] = por_estado.get(clave, 0) + 1
    avisos = []
    if data.get('total_unidades') and unidades != int(data['total_unidades']):
        avisos.append(f'las unidades de las líneas ({unidades}) no cuadran con la factura '
                      f'({data["total_unidades"]})')
    if data.get('total_neto') and neto != int(data['total_neto']):
        avisos.append(f'el neto de las líneas (${fmt(neto)}) no cuadra con la factura '
                      f'(${fmt(data["total_neto"])})')
    if dte.monto_neto and abs(int(dte.monto_neto) - neto) >= 1:
        avisos.append(f'el neto de las líneas (${fmt(neto)}) no cuadra con el DTE registrado '
                      f'(${fmt(int(dte.monto_neto))})')
    return {'unidades': unidades, 'neto': neto, 'unidades_factura': data.get('total_unidades'),
            'neto_factura': data.get('total_neto'), 'neto_dte': int(dte.monto_neto or 0),
            'por_estado': por_estado, 'avisos': avisos,
            'bloqueantes': sum(1 for p in planes if _bloqueante(p)),
            'a_cargar': sum(1 for p in activos if p['estado'] != 'YA_CARGADO')}


def _motor(data):
    """Planificador con los parámetros de esta factura (los "_" del JSON pisan
    al perfil de la marca; None = perfil)."""
    return PlanificadorCarga({
        'renombrar_tallas': data.get('_renombrar_tallas', True) is not False,
        'umbral_costo': _entero(data.get('_umbral_costo')),
        'factor_bajo': Decimal(data['_factor_bajo']) if data.get('_factor_bajo') else None,
        'factor_alto': Decimal(data['_factor_alto']) if data.get('_factor_alto') else None,
    })


def _factura(data, user):
    """Factura lista para planificar. El DTE (por folio + RUT o el elegido con
    dte_id) tiene que poder respaldar un ingreso de stock de la empresa de la
    bodega: el mismo criterio con que crear_producto_manual rechaza cada línea
    (CC-03). Se revisa aquí para que la vista previa lo muestre y la carga no se
    lance para fallar entera en segundo plano."""
    from app.views import _error_dte_compra_para_ingreso

    margen = Decimal(data['_margen_sobreprecio']) if data.get('_margen_sobreprecio') else None
    f = factura_desde_datos(data, user, margen=margen)
    error = _error_dte_compra_para_ingreso(f['dte'], f['sucursal'].empresa_id)
    if error:
        dte = f['dte']
        raise ErrorCarga(f'El DTE #{dte.id} ({dte.tipo_documento} N° {dte.numero_documento} de '
                         f'{dte.emisor.nombre}) no sirve para cargar en {f["sucursal"].alias}: '
                         f'{error} Elige en la lista el DTE correcto.')
    return f


def planificar(sesion, user, solo_idx=None):
    """Vista previa de cada factura del expediente (lista de dicts JSON)."""
    salida, vistos = [], {}
    for idx, data in enumerate(sesion.facturas or []):
        if solo_idx is not None and idx != solo_idx:
            continue
        perfil = perfil_para(data.get('marca'))
        item = {
            'idx': idx, 'estado': data.get('_estado', 'PENDIENTE'),
            'folio': data.get('folio'), 'proveedor': data.get('proveedor_nombre'),
            'proveedor_rut': data.get('proveedor_rut'), 'fecha_emision': data.get('fecha_emision'),
            'marca': data.get('marca'), 'color': data.get('color'), 'sucursal': data.get('sucursal'),
            'dte_id': data.get('dte_id'), 'renombrar_tallas': data.get('_renombrar_tallas', True) is not False,
            'tipo_talla': (data.get('tipo_talla') or perfil.tipo_talla or 'CL').upper(),
            'guias_talla': {str(k).upper(): v for k, v in (data.get('guias_talla') or perfil.guias or {}).items()},
            'perfil': (perfil.marca or 'genérico') + (' (aprendido)' if perfil.aprendido else ''),
            'perfil_aprendido': perfil.aprendido,
            'identidad_color': perfil.identidad_color,
            'n_lineas': len(data.get('lineas') or []),
            'descuento_global': data.get('descuento_global'),
            'fuente': data.get('_fuente'), 'revisar': list(data.get('_revisar') or []),
            'resultado': data.get('_resultado'),
            'dte': None, 'error': None, 'candidatos_dte': [], 'registrar_dte': None,
            'planes': [], 'totales': None,
        }
        try:
            f = _factura(data, user)
            motor = _motor(data)
            planes = motor.planificar_factura(f, vistos)
            umbral, bajo, alto = motor.regla(f)
            item['dte'] = _dte(f['dte'])
            item['regla'] = {'umbral': umbral, 'factor_bajo': str(bajo), 'factor_alto': str(alto),
                             'margen_sobreprecio': str(f['margen'])}
            item['planes'] = [_plan(p) for p in planes]
            item['totales'] = _totales(f, planes)
        except DteNoEncontrado as exc:
            # El texto del comando habla de "dte_id en el JSON": aquí se le dice
            # a la persona qué hacer en la pantalla (B15-10).
            item['error'] = (
                f'La FACTURA {exc.folio} del RUT {exc.rut} no está registrada en el sistema'
                + (f' (con ese número solo hay: {exc.otros})' if exc.otros else '')
                + '. Regístrala en Gestión Documentos Compras («Registrar esta factura») y '
                  'aprieta «Volver a calcular», o elige en la lista el DTE correcto si la '
                  'registraron con otro proveedor o tipo.')
            item['candidatos_dte'] = _candidatos_dte(data)
            item['registrar_dte'] = datos_para_registrar(data)
        except DteAmbiguo as exc:
            item['error'] = (f'El folio {exc.folio} calza con varios DTE ({exc.detalle}): '
                             f'elige el correcto en la lista.')
            item['candidatos_dte'] = _candidatos_dte(data)
        except ErrorCarga as exc:
            item['error'] = str(exc)
            item['candidatos_dte'] = _candidatos_dte(data)
        except (KeyError, TypeError, ValueError) as exc:
            logger.exception('carga_factura: la factura %s de la sesión %s no se pudo planificar',
                             idx, sesion.id)
            item['error'] = f'La factura leída viene incompleta ({type(exc).__name__}: {exc}).'
        salida.append(item)
    return salida


# ------------------------------------------------------------------ carga


def iniciar_carga(sesion_id, idx, opciones, user_id):
    _lanzar(cargar_en_segundo_plano, sesion_id, idx, opciones, user_id)


def cargar_en_segundo_plano(sesion_id, idx, opciones, user_id):
    """Hilo: carga la factura `idx` línea a línea (aplicador) y deja el
    resultado en la factura del expediente. `opciones` = {articulo: 's'|'c'|'t'|'n'}
    para los códigos que ya existen."""
    resultado = {'lineas': [], 'ok': 0, 'fallidas': 0, 'saltadas': 0, 'unidades': 0,
                 'inicio': timezone.now().isoformat(timespec='seconds'), 'fin': None}
    data = None
    try:
        user = get_user_model().objects.get(id=user_id)
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        if sesion.facturas[idx].get('_estado') == 'CARGADA':
            # Otra orden de carga ya la terminó: no se toca su resultado.
            raise ErrorCarga(f'la factura {sesion.facturas[idx].get("folio")} ya se cargó; '
                             f'no se repite.')
        data = dict(sesion.facturas[idx])
        avisar = _progreso(sesion_id)
        avisar('Revisando la factura antes de cargar…')
        f = _factura(data, user)
        planes = _motor(data).planificar_factura(f, {})
        bloqueantes = [p['articulo'] for p in planes if _bloqueante(p)]
        if bloqueantes:
            raise ErrorCarga('Hay líneas con error: ' + ', '.join(bloqueantes)
                             + '. No se cargó nada.')
        # Opciones por N° de línea (dos líneas pueden tener el mismo código:
        # dos colores). Se acepta también por código, por compatibilidad.
        opciones = {str(k).strip().upper(): str(v).lower() for k, v in (opciones or {}).items()}
        total = len(planes)
        for i, plan in enumerate(planes, start=1):
            avisar(f'Cargando {i} de {total}: {plan["articulo"]}…')
            fila = {'n': plan['n'], 'articulo': plan['articulo'], 'unidades': plan['unidades']}
            if plan['linea'].get('_omitir'):
                fila.update(estado='SALTADA', detalle='omitida por ti')
            elif plan['estado'] == 'YA_CARGADO':
                fila.update(estado='SALTADA', detalle='ya estaba cargada contra este DTE')
            else:
                opcion = 's'
                if plan['vigentes'] is not None:
                    disponibles = opciones_existente(plan)
                    if disponibles is None:
                        opcion = 't'
                    else:
                        opcion = opciones.get(str(plan['n']), opciones.get(plan['articulo'])
                                              or opcion_por_defecto(plan))
                        if opcion == 'n':
                            fila.update(estado='SALTADA', detalle='saltada por ti')
                        elif opcion not in disponibles:
                            opcion = disponibles[0]
                if fila.get('estado') != 'SALTADA':
                    r = aplicar_linea(plan, f, user, opcion)
                    if r.get('ya_cargado'):
                        fila.update(estado='SALTADA', detalle=r['error'])
                    elif r['ok']:
                        resp = r['respuesta']
                        cargadas = sum(t.get('stock_ingresado', 0) for t in resp.get('tallas_detalle', []))
                        fila.update(
                            estado='OK', opcion=opcion, producto_id=resp.get('producto_id'),
                            cargadas=cargadas, detalle=resp.get('mensaje', ''),
                            tallas=[{'talla': t.get('talla'), 'sku': t.get('sku'),
                                     'ingresado': t.get('stock_ingresado'),
                                     'stock_final': t.get('stock_final')}
                                    for t in resp.get('tallas_detalle', [])],
                            renombradas=len(plan.get('renombres', [])))
                        resultado['ok'] += 1
                        resultado['unidades'] += cargadas
                    else:
                        fila.update(estado='FALLO', detalle=r['error'])
                        resultado['fallidas'] += 1
            if fila.get('estado') == 'SALTADA':
                resultado['saltadas'] += 1
            resultado['lineas'].append(fila)
            data['_resultado'] = resultado
            data['_estado'] = 'CARGANDO'
            _guardar_factura(sesion_id, idx, data)
        resultado['fin'] = timezone.now().isoformat(timespec='seconds')
        data['_resultado'] = resultado
        data['_estado'] = 'PARCIAL' if resultado['fallidas'] else 'CARGADA'
        sesion = _guardar_factura(sesion_id, idx, data)
        aprendido = []
        if resultado['ok']:
            try:
                aprendido = aprender_de_carga(data, planes, resultado, user)
            except Exception:
                logger.exception('carga_factura: no se pudo guardar lo aprendido (sesión %s)', sesion_id)
        sesion.agregar_mensaje(AGENTE, _texto_carga(data, resultado), tipo='carga', factura=idx,
                               aprendido=aprendido)
    except Exception as exc:
        mensaje = str(exc) if isinstance(exc, ErrorCarga) else f'{type(exc).__name__}: {exc}'
        logger.exception('carga_factura: falló la carga de la factura %s de la sesión %s', idx, sesion_id)
        try:
            if data is not None:
                data['_estado'] = 'PARCIAL' if resultado['ok'] else 'PENDIENTE'
                data['_resultado'] = resultado if resultado['lineas'] else data.get('_resultado')
                sesion = _guardar_factura(sesion_id, idx, data)
            else:
                sesion = CargaFacturaPdf.objects.get(id=sesion_id)
            sesion.agregar_mensaje(AGENTE, f'La carga se detuvo: {mensaje}', tipo='error', factura=idx)
        except Exception:
            pass
    finally:
        CargaFacturaPdf.objects.filter(id=sesion_id).update(
            estado='LEIDA', progreso='', actualizado_en=timezone.now())
        _cerrar_conexion()


def _texto_carga(data, resultado):
    texto = (f'Factura N° {data.get("folio")}: {resultado["ok"]} línea(s) cargadas, '
             f'{resultado["unidades"]} unidades en {data.get("sucursal")}')
    if resultado['saltadas']:
        texto += f' · {resultado["saltadas"]} saltada(s)'
    if resultado['fallidas']:
        texto += (f' · {resultado["fallidas"]} fallida(s): no quedó nada de ellas; corrige y '
                  f'vuelve a cargar (las que entraron se saltan solas)')
    else:
        texto += '. Ya aparecen en «Actividad reciente».'
    return texto


# --------------------------------------------------------- internet


def lineas_sin_color(data):
    """N° (1-based) de las líneas que no tienen color propio (o traen el por
    defecto) y todavía se pueden cargar: candidatas a buscar en internet."""
    defecto = str(data.get('color') or '').strip().upper()
    salida = []
    for n, l in enumerate(data.get('lineas') or [], start=1):
        if l.get('_omitir'):
            continue
        color = str(l.get('color') or '').strip().upper()
        if not color or color == defecto:
            salida.append(n)
    return salida


def iniciar_investigacion(sesion, pares, user):
    """Deja la sesión BUSCANDO y lanza la búsqueda en internet de `pares`
    [(idx, n), ...] en segundo plano. Devuelve cuántas líneas se buscarán."""
    if sesion.estado != 'LEIDA':
        raise ErrorCarga('Espera a que termine lo que está haciendo la sesión.')
    validos = []
    for idx, n in pares:
        try:
            data = sesion.facturas[idx]
            linea = data['lineas'][n - 1]
        except (IndexError, KeyError, TypeError):
            continue
        if data.get('_estado') == 'CARGADA' or linea.get('_omitir') or n < 1:
            continue
        if (idx, n) not in validos:
            validos.append((idx, n))
    if not validos:
        raise ErrorCarga('No hay líneas para buscar (¿ya están cargadas u omitidas?).')
    validos = validos[:25]
    progreso = f'Buscando en internet {len(validos)} artículo(s)…'
    # Paso LEIDA → BUSCANDO con UPDATE condicional: dos pedidos simultáneos
    # (o un «Cargar» a la vez) no lanzan dos hilos sobre la misma sesión.
    tomada = CargaFacturaPdf.objects.filter(id=sesion.id, estado='LEIDA').update(
        estado='BUSCANDO', progreso=progreso, actualizado_en=timezone.now())
    if not tomada:
        raise ErrorCarga('Espera a que termine lo que está haciendo la sesión.')
    sesion.estado, sesion.progreso = 'BUSCANDO', progreso
    _lanzar(investigar_en_segundo_plano, sesion.id, validos, getattr(user, 'id', None))
    return len(validos)


def investigar_en_segundo_plano(sesion_id, pares, user_id):
    """Hilo: busca cada artículo en internet, completa color / categoría /
    género donde la línea no los tenía, guarda lo aprendido y deja un
    mensaje con lo encontrado."""
    from . import busqueda

    hallazgos = []
    try:
        user = get_user_model().objects.filter(id=user_id).first() if user_id else None
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        catalogo = opciones_catalogo(user) if user else _catalogo_basico()
        avisar = _progreso(sesion_id)
        svc_lectura.uso_iniciar()
        por_factura = {}
        for k, (idx, n) in enumerate(pares, start=1):
            sesion = CargaFacturaPdf.objects.get(id=sesion_id)
            data = dict(sesion.facturas[idx])
            lineas = list(data.get('lineas') or [])
            linea = dict(lineas[n - 1])
            articulo = str(linea.get('articulo') or '')
            marca = str(linea.get('marca') or data.get('marca') or '')
            avisar(f'Buscando en internet {k} de {len(pares)}: {articulo}…')
            hallazgo = {'idx': idx, 'n': n, 'articulo': articulo, 'ok': False, 'aplicado': []}
            # Ya buscado hace poco para esta marca + código: se reutiliza lo
            # aprendido (cada búsqueda web cuesta aparte y da lo mismo).
            previo = _hallazgo_previo(marca, articulo)
            if previo is not None:
                r = previo
                hallazgo['reusado'] = previo['_fecha']
            else:
                try:
                    r = busqueda.investigar_articulo(marca, articulo, linea.get('descripcion'), catalogo)
                except Exception as exc:
                    logger.exception('carga_factura: falló la búsqueda de %s (sesión %s)', articulo, sesion_id)
                    hallazgo['detalle'] = str(exc)
                    hallazgos.append(hallazgo)
                    continue
            hallazgo.update(ok=r['encontrado'], nombre=r['nombre'], que_es=r['que_es'],
                            color=r['color_primario'], colores_vistos=r['colores_vistos'],
                            fuente_url=r['fuente_url'], confianza=r['confianza'])
            if r['encontrado']:
                defecto = str(data.get('color') or '').strip().upper()
                color_actual = str(linea.get('color') or '').strip().upper()
                if r['color_primario'] and (not color_actual or color_actual == defecto):
                    linea['color'] = r['color_primario']
                    hallazgo['aplicado'].append('color')
                if r['categoria'] and not linea.get('categoria'):
                    linea['categoria'] = r['categoria']
                    hallazgo['aplicado'].append('categoría')
                if r['genero'] and str(linea.get('genero') or 'UNISEX').upper() == 'UNISEX' \
                        and r['genero'].upper() != 'UNISEX':
                    linea['genero'] = r['genero']
                    hallazgo['aplicado'].append('género')
                que_es = ': '.join(t for t in (r['nombre'], r['que_es']) if t)
                if que_es:
                    linea['_que_es'] = que_es
                if hallazgo['aplicado']:
                    linea['_aprendido'] = sorted(set(list(linea.get('_aprendido') or []) + ['internet']))
                lineas[n - 1] = linea
                data['lineas'] = lineas
                _guardar_factura(sesion_id, idx, data)
                por_factura[idx] = por_factura.get(idx, 0) + 1
                if previo is None:
                    try:
                        _aprender_de_internet(marca, articulo, linea, r)
                    except Exception:
                        logger.exception('carga_factura: no se pudo guardar lo aprendido de internet')
            hallazgos.append(hallazgo)
        uso = _sumar_uso(sesion_id, 'busqueda', svc_lectura.uso_actual())
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        sesion.agregar_mensaje(AGENTE, _texto_busqueda(hallazgos), tipo='busqueda',
                               hallazgos=hallazgos, uso=uso)
    except Exception as exc:
        mensaje = str(exc) if isinstance(exc, ErrorCarga) else f'{type(exc).__name__}: {exc}'
        logger.exception('carga_factura: falló la búsqueda en internet (sesión %s)', sesion_id)
        try:
            CargaFacturaPdf.objects.get(id=sesion_id).agregar_mensaje(
                AGENTE, f'La búsqueda en internet se detuvo: {mensaje}', tipo='error',
                hallazgos=hallazgos)
        except Exception:
            pass
    finally:
        CargaFacturaPdf.objects.filter(id=sesion_id).update(
            estado='LEIDA', progreso='', actualizado_en=timezone.now())
        _cerrar_conexion()


# Lo buscado en internet hace menos de esto se reutiliza sin volver a buscar.
DIAS_VIGENCIA_BUSQUEDA = 90


def _hallazgo_previo(marca, articulo):
    """Resultado guardado de una búsqueda anterior del mismo marca + código
    (con el formato de busqueda.investigar_articulo, más `_fecha`), o None si
    no hay o ya venció."""
    clave = clave_marca(marca)
    if not clave or not articulo:
        return None
    fila = ProductoAprendido.objects.filter(marca=clave, articulo=normalizar_articulo(articulo)).first()
    if fila is None or not (fila.nombre_internet or fila.color_internet):
        return None
    if timezone.now() - fila.actualizado_en > timedelta(days=DIAS_VIGENCIA_BUSQUEDA):
        return None
    return {
        'encontrado': True, 'nombre': fila.nombre_internet, 'que_es': fila.que_es,
        'color_primario': fila.color_internet, 'colores_vistos': '', 'genero': '',
        'categoria': '', 'fuente_url': fila.fuente_url, 'confianza': 'media',
        '_fecha': fila.actualizado_en.date().isoformat(),
    }


def _aprender_de_internet(marca, articulo, linea, r):
    clave = clave_marca(marca)
    if not clave or not articulo:
        return
    defaults = {'nombre_internet': r['nombre'][:255], 'que_es': r['que_es'][:2000],
                'color_internet': r['color_primario'][:100], 'fuente_url': r['fuente_url'][:500]}
    fila, creado = ProductoAprendido.objects.get_or_create(
        marca=clave, articulo=normalizar_articulo(articulo),
        defaults={**defaults, 'descripcion': str(linea.get('descripcion') or '')[:255], 'fuente': 'internet'})
    if not creado:
        for k, v in defaults.items():
            if v:
                setattr(fila, k, v)
        fila.save(update_fields=list(defaults) + ['actualizado_en'])


def _texto_busqueda(hallazgos):
    if not hallazgos:
        return 'No había artículos para buscar.'
    partes = [f'Busqué en internet {len(hallazgos)} artículo(s):']
    for h in hallazgos:
        if not h.get('ok'):
            partes.append(f'• {h["articulo"]}: no lo encontré' + (f' ({h["detalle"]})' if h.get('detalle') else '') + '.')
            continue
        texto = f'• {h["articulo"]}: {h.get("nombre") or "encontrado"}'
        if h.get('reusado'):
            texto += f' (ya lo había buscado el {h["reusado"]}: reutilizado, sin costo)'
        if h.get('que_es'):
            texto += f' — {h["que_es"]}'
        if h.get('color'):
            texto += f' · color {h["color"]}'
            if h.get('colores_vistos'):
                texto += f' ({h["colores_vistos"]})'
        texto += (' → apliqué ' + ', '.join(h['aplicado']) if h.get('aplicado') else ' → sin cambios en la línea')
        partes.append(texto + '.')
    partes.append('Lo encontrado queda aprendido para la próxima factura con esos códigos. '
                  'Revisa la vista previa recalculada.')
    return '\n'.join(partes)


def _catalogo_basico():
    categorias, especialidades, colores = svc_lectura._listas_del_sistema()
    generos = []
    for nombre in ATRIBUTOS_GENERO:
        generos = [v for v in AtributoOpcion.objects.filter(atributo__nombre__iexact=nombre)
                   .values_list('valor', flat=True)]
        if generos:
            break
    return {'categorias': categorias, 'especialidades': especialidades, 'colores': colores,
            'generos': generos, 'marcas': [], 'guias': {}}


# ---------------------------------------------------------------- catálogo


def opciones_catalogo(user):
    """Listas para los editores de la vista previa (marcas, colores, géneros,
    categorías v1.2, especialidades, guías por marca y bodegas del usuario)."""
    from app.utils_permisos import obtener_sucursales_usuario

    def valores(nombre_atributo):
        vistos, salida = set(), []
        for v in (AtributoOpcion.objects.filter(atributo__nombre__iexact=nombre_atributo)
                  .order_by('valor').values_list('valor', flat=True)):
            clave = str(v).strip().upper()
            if clave and clave not in vistos:
                vistos.add(clave)
                salida.append(str(v).strip())
        return salida

    generos = []
    for nombre in ATRIBUTOS_GENERO:
        generos = valores(nombre)
        if generos:
            break
    categorias, especialidades, _colores = svc_lectura._listas_del_sistema()
    guias = {}
    for g in GuiaTalla.objects.select_related('marca').order_by('marca__valor', 'nombre'):
        guias.setdefault(str(g.marca.valor).strip().upper(), []).append(g.nombre)
    return {
        'marcas': valores('Marca'), 'colores': valores('Color'), 'generos': generos,
        'categorias': categorias, 'especialidades': especialidades, 'guias': guias,
        'sucursales': [{'id': s.id, 'alias': s.alias}
                       for s in obtener_sucursales_usuario(user)],
        'modelo': svc_lectura.MODELO,
    }
