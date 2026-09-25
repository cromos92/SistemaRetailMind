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

from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from app.models import AtributoOpcion, CargaFacturaPdf, Dte, GuiaTalla
from app.utils_producto_match import normalizar_articulo

from . import lectura as svc_lectura
from .aplicador import aplicar_linea
from .facturas import ErrorCarga, factura_desde_datos, variantes_rut
from .planificador import (
    ATRIBUTOS_GENERO, PlanificadorCarga, estado_visible, opciones_existente,
    tallas_y_stock,
)
from .precios import fmt

logger = logging.getLogger('app')

AGENTE = 'agente'
USUARIO = 'usuario'

# En tests se ponen en True para que la lectura y la carga corran en el mismo
# hilo (el hilo de fondo no vería la transacción del test).
SINCRONO = False

# Campos que la vista previa deja corregir. Cualquier otra clave del JSON
# (folio, RUT, sucursal, totales…) se conserva tal como se leyó.
CAMPOS_FACTURA_EDITABLES = ('marca', 'color', 'dte_id', 'folio', 'proveedor_rut',
                            'fecha_emision', '_renombrar_tallas')
CAMPOS_LINEA_EDITABLES = ('articulo', 'descripcion', 'costo', 'precioventa', 'genero',
                          'categoria', 'color', 'marca', 'especialidades', 'ficha_id',
                          'guia', 'tallas', 'cantidad', 'importe', '_omitir')
_ENTEROS = ('costo', 'precioventa', 'ficha_id', 'dte_id', 'cantidad', 'importe', 'folio')


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
        leido = svc_lectura.leer_pdf(pdf, marca=sesion.marca or None,
                                     lecturas=sesion.lecturas, progreso=avisar)
        avisar('Comparando las lecturas…')
        consolidada = svc_lectura.combinar_lecturas(leido['lecturas'])
        facturas = consolidada.get('facturas', [])
        if not facturas:
            raise ErrorCarga('No encontré ninguna factura en el documento.')
        datos = []
        for factura in facturas:
            d = svc_lectura.a_json_de_carga(
                factura, sesion.sucursal.alias, marca=sesion.marca or None,
                fuente=f'{sesion.nombre_archivo}, leída con {svc_lectura.MODELO} '
                       f'({sesion.lecturas} lectura(s))')
            d['_estado'] = 'PENDIENTE'
            datos.append(d)
        sesion.refresh_from_db()
        sesion.facturas = datos
        sesion.estado = 'LEIDA'
        sesion.modelo = svc_lectura.MODELO
        sesion.leida_en = timezone.now()
        sesion.progreso = ''
        sesion.error = ''
        sesion.save(update_fields=['facturas', 'estado', 'modelo', 'leida_en', 'progreso',
                                   'error', 'actualizado_en'])
        sesion.agregar_mensaje(AGENTE, _texto_lectura(datos, leido['modo']), tipo='lectura')
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


def _texto_lectura(facturas, modo):
    partes = [f'Leí el PDF ({"escaneo" if modo == "escaneo" else "PDF con texto"}) y encontré '
              f'{len(facturas)} factura(s):']
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
                  'haga falta y cuando esté bien aprieta «Cargar».')
    return '\n'.join(partes)


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
    """
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
            elif campo == '_renombrar_tallas':
                valor = bool(valor)
            else:
                valor = str(valor or '').strip().upper() if campo != 'fecha_emision' else str(valor or '').strip()
            if data.get(campo) != valor:
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


def _plan(plan):
    linea = plan['linea']
    costo, sobre, pv = plan['factura']
    vigentes = plan['vigentes']
    return {
        'n': plan['n'], 'articulo': plan['articulo'], 'estado': estado_visible(plan),
        'omitida': bool(linea.get('_omitir')),
        'descripcion': linea.get('descripcion', ''), 'unidades': plan['unidades'],
        'costo': costo, 'sobreprecio': sobre, 'precioventa': pv, 'fuente_pv': plan['fuente_pv'],
        'precioventa_a_mano': linea.get('precioventa'),
        'vigentes': ({'costo': vigentes[0], 'sobreprecio': vigentes[1], 'precioventa': vigentes[2]}
                     if vigentes is not None else None),
        'opciones': opciones_existente(plan) if vigentes is not None else None,
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
        # Lo que dice hoy el JSON (para los editores de la vista previa).
        'json': {campo: linea.get(campo) for campo in CAMPOS_LINEA_EDITABLES},
    }


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
    return PlanificadorCarga({'renombrar_tallas': data.get('_renombrar_tallas', True) is not False})


def planificar(sesion, user, solo_idx=None):
    """Vista previa de cada factura del expediente (lista de dicts JSON)."""
    salida, vistos = [], {}
    for idx, data in enumerate(sesion.facturas or []):
        if solo_idx is not None and idx != solo_idx:
            continue
        item = {
            'idx': idx, 'estado': data.get('_estado', 'PENDIENTE'),
            'folio': data.get('folio'), 'proveedor': data.get('proveedor_nombre'),
            'proveedor_rut': data.get('proveedor_rut'), 'fecha_emision': data.get('fecha_emision'),
            'marca': data.get('marca'), 'color': data.get('color'), 'sucursal': data.get('sucursal'),
            'dte_id': data.get('dte_id'), 'renombrar_tallas': data.get('_renombrar_tallas', True) is not False,
            'fuente': data.get('_fuente'), 'revisar': list(data.get('_revisar') or []),
            'resultado': data.get('_resultado'),
            'dte': None, 'error': None, 'candidatos_dte': [], 'planes': [], 'totales': None,
        }
        try:
            f = factura_desde_datos(data, user)
            motor = _motor(data)
            planes = motor.planificar_factura(f, vistos)
            umbral, bajo, alto = motor.regla(f)
            item['dte'] = _dte(f['dte'])
            item['regla'] = {'umbral': umbral, 'factor_bajo': str(bajo), 'factor_alto': str(alto),
                             'margen_sobreprecio': str(f['margen'])}
            item['planes'] = [_plan(p) for p in planes]
            item['totales'] = _totales(f, planes)
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
        data = dict(sesion.facturas[idx])
        avisar = _progreso(sesion_id)
        avisar('Revisando la factura antes de cargar…')
        f = factura_desde_datos(data, user)
        planes = _motor(data).planificar_factura(f, {})
        bloqueantes = [p['articulo'] for p in planes if _bloqueante(p)]
        if bloqueantes:
            raise ErrorCarga('Hay líneas con error: ' + ', '.join(bloqueantes)
                             + '. No se cargó nada.')
        opciones = {normalizar_articulo(k): str(v).lower() for k, v in (opciones or {}).items()}
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
                        opcion = opciones.get(plan['articulo'], 's')
                        if opcion == 'n':
                            fila.update(estado='SALTADA', detalle='saltada por ti')
                        elif opcion not in disponibles:
                            opcion = disponibles[0]
                if fila.get('estado') != 'SALTADA':
                    r = aplicar_linea(plan, f, user, opcion)
                    if r['ok']:
                        resp = r['respuesta']
                        cargadas = sum(t.get('stock_ingresado', 0) for t in resp.get('tallas_detalle', []))
                        fila.update(
                            estado='OK', opcion=opcion, producto_id=resp.get('producto_id'),
                            cargadas=cargadas, detalle=resp.get('mensaje', ''),
                            tallas=[{'talla': t.get('talla'), 'sku': t.get('sku'),
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
        sesion.agregar_mensaje(AGENTE, _texto_carga(data, resultado), tipo='carga', factura=idx)
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
    categorias, especialidades = svc_lectura._listas_del_sistema()
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
