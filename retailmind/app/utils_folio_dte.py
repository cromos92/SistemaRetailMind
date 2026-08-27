"""Utilidades para la edición segura del folio / vendedor de un DTE.

Contexto
--------
Editar el folio (`Dte.numero_documento`) de un documento tributario es la
operación más delicada del sistema: el SII asigna los folios por rango
(CAF) al **RUT del emisor + tipo de documento**, no por sucursal. Reusar
un folio ya emitido genera dos documentos distintos con el mismo número
ante el SII.

Este módulo concentra los chequeos que faltaban en los dos endpoints que
hoy pueden mover un folio:

* ``app.views.editar_folio_dte``                  (pantalla Gestión DTE)
* ``app.views_modulo_ventas.editar_dte_boleta_papel`` (Gestión de Documentos)

Ambos validaban duplicados **filtrando por sucursal**, y ahí está el punto
ciego: un mismo contribuyente puede tener más de una ficha de `Empresa` en
esta base, y cada ficha lleva su propio correlativo. Cuando dos fichas del
mismo RUT emiten el mismo tipo de documento, sus correlativos se solapan y
se reemiten folios que la otra ficha ya había consumido — el chequeo por
sucursal no ve nada porque cada documento es único *dentro de su sucursal*.
Ya ocurrió en esta instalación, a escala de cientos de documentos, y la
única defensa es comparar por RUT + tipo, que es como lo mira el SII.

Todo lo que hay aquí es de **solo lectura** salvo
`sincronizar_ticket_por_cambio_folio` y `registrar_cambio_vendedor_dte`,
que deben llamarse dentro de la misma `transaction.atomic()` del endpoint.

No requiere migraciones: reutiliza `HistorialCambioFolioDte` (migración
0147) y `RegistroAutorizacion` (ya existente) como sumideros de auditoría.
"""

from __future__ import annotations

import logging
import re

from django.db.models import Value
from django.db.models.functions import Replace, Trim, Upper

logger = logging.getLogger('app')


# Tipos de DTE que consumen folio autorizado por el SII. La BOLETA PAPEL
# usa talonario propio y no se valida contra el correlativo electrónico.
TIPOS_CON_FOLIO_SII = {
    'BOLETA ELECTRONICA',
    'BOLETA EXENTA',
    'FACTURA ELECTRONICA',
    'FACTURA EXENTA',
    'NOTA DE CREDITO',
    'NOTA DE DEBITO',
    'GUIA',
    'GUIA DESPACHO',
}

# Tipos cuyo folio NO es único para siempre: la BOLETA PAPEL sale de un
# talonario físico y su numeración se reinicia con cada talonario, así que el
# mismo número reaparece legítimamente año a año. Validar contra todo el
# historial bloquea cargas correctas; lo que sí es un error real es repetir el
# folio dentro del mismo período contable.
#
# Para estos tipos la colisión se busca en una ventana de
# `VENTANA_MESES_FOLIO_REUSABLE` meses **a cada lado** de la fecha de emisión
# del documento (no sólo hacia atrás: el duplicado puede haberse cargado
# después). Fuera de la ventana la colisión se reporta como advertencia, no
# como error.
TIPOS_FOLIO_REUSABLE = {
    'BOLETA PAPEL',
}
VENTANA_MESES_FOLIO_REUSABLE = 2


def ventana_folio_reusable(tipo_documento, fecha_ref):
    """`(desde, hasta)` de la ventana de unicidad, o None si no aplica.

    None significa "el folio es único para siempre" (folios CAF del SII).
    """
    from dateutil.relativedelta import relativedelta

    tipo = (tipo_documento or '').upper().strip()
    if tipo not in TIPOS_FOLIO_REUSABLE or not fecha_ref:
        return None
    delta = relativedelta(months=VENTANA_MESES_FOLIO_REUSABLE)
    return (fecha_ref - delta, fecha_ref + delta)


def normalizar_rut(rut) -> str:
    """Deja el RUT en formato comparable: sin puntos, sin espacios, upper.

    ``'12.345.678-9'`` y ``'12345678-9'`` colapsan al mismo valor.
    """
    return re.sub(r'[.\s]', '', str(rut or '')).upper()


def empresas_con_mismo_rut(empresa):
    """IDs de todas las `Empresa` que comparten el RUT de `empresa`.

    Un mismo contribuyente puede tener más de una ficha de `Empresa` en
    esta base, y cada ficha corre su propio correlativo. El SII no ve esas
    fichas: ve un solo RUT. Cualquier chequeo de folio tiene que hacerse
    sobre el conjunto completo.
    """
    from app.models import Empresa

    if empresa is None:
        return []

    rut_norm = normalizar_rut(getattr(empresa, 'rut', ''))
    if not rut_norm:
        return [empresa.id] if getattr(empresa, 'id', None) else []

    ids = list(
        Empresa.objects
        .annotate(
            _rut_norm=Replace(
                Replace('rut', Value('.'), Value('')),
                Value(' '), Value(''),
            )
        )
        .filter(_rut_norm__iexact=rut_norm)
        .values_list('id', flat=True)
    )
    if empresa.id not in ids:
        ids.append(empresa.id)
    return ids


def buscar_colisiones_folio(dte, nuevo_folio, *, incluir_descartados=False,
                            tipo_destino=None, fecha_ref=None):
    """DTEs que ya ocupan `nuevo_folio` para el mismo RUT emisor + tipo.

    Devuelve una lista de dicts (vacía si no hay colisión). **No** filtra
    por sucursal: el folio es único por RUT + tipo ante el SII.

    `tipo_destino` permite consultar por un tipo distinto al actual del DTE
    (caso: convertir una BOLETA ELECTRONICA en BOLETA PAPEL, donde el folio
    hay que contrastarlo contra el universo del tipo destino).

    Cada colisión trae `dentro_ventana`: True si cae dentro de la ventana de
    unicidad del tipo (ver `ventana_folio_reusable`). Para los tipos con folio
    CAF la ventana no existe y `dentro_ventana` es siempre True — el folio es
    único para siempre.
    """
    from app.models import Dte

    emisor_ids = empresas_con_mismo_rut(getattr(dte, 'emisor', None))
    if not emisor_ids:
        return []

    tipo = (tipo_destino or dte.tipo_documento or '').upper().strip()
    if fecha_ref is None:
        fecha_ref = dte.fecha_emision
    ventana = ventana_folio_reusable(tipo, fecha_ref)

    qs = (
        Dte.objects
        .filter(
            emisor_id__in=emisor_ids,
            tipo_documento=tipo,
            numero_documento=nuevo_folio,
        )
        .exclude(id=dte.id)
        .select_related('sucursal', 'emisor')
    )
    if not incluir_descartados:
        qs = qs.filter(descartado=False)

    # Con ventana se priorizan las colisiones que caen dentro (son las
    # bloqueantes): el corte a 20 filas no puede dejarlas fuera por culpa
    # de un montón de reusos antiguos legítimos del talonario.
    if ventana:
        qs = qs.order_by('-fecha_emision')

    def _dentro(fecha):
        if not ventana:
            return True
        if fecha is None:
            # Sin fecha no se puede ubicar en el tiempo: se trata como
            # dentro de la ventana para no dejar pasar un duplicado real.
            return True
        return ventana[0] <= fecha <= ventana[1]

    resultado = [
        {
            'dte_id': o.id,
            'tipo_documento': o.tipo_documento,
            'numero_documento': o.numero_documento,
            'emisor_id': o.emisor_id,
            'emisor': getattr(o.emisor, 'nombre', '') or '',
            'sucursal_id': o.sucursal_id,
            'sucursal': getattr(o.sucursal, 'alias', '') or 'SIN SUCURSAL',
            'fecha_emision': (
                o.fecha_emision.strftime('%Y-%m-%d') if o.fecha_emision else None
            ),
            'monto_con_iva': int(o.monto_con_iva or 0),
            'estado_dte': o.estado_dte,
            'dentro_ventana': _dentro(o.fecha_emision),
        }
        for o in qs[:40]
    ]
    # Las bloqueantes primero, para que el detalle del error las muestre.
    resultado.sort(key=lambda c: not c['dentro_ventana'])
    return resultado[:20]


def _detalle_colisiones(colisiones, limite=5):
    """Texto legible con los DTE que chocan (`DTE #id (sucursal, fecha, $monto)`)."""
    detalle = '; '.join(
        "DTE #{id} ({suc}, {fec}, ${monto})".format(
            id=c['dte_id'], suc=c['sucursal'], fec=c['fecha_emision'] or '?',
            monto=f"{c['monto_con_iva']:,}".replace(',', '.'),
        )
        for c in colisiones[:limite]
    )
    if len(colisiones) > limite:
        detalle += f' y {len(colisiones) - limite} más'
    return detalle


def mensajes_por_colisiones(dte, folio, colisiones, *, tipo_destino=None,
                            fecha_ref=None):
    """`(errores, advertencias)` a partir de las colisiones de folio.

    Para los tipos con folio CAF toda colisión es error. Para los tipos de
    `TIPOS_FOLIO_REUSABLE` (talonario físico) sólo bloquean las que caen
    dentro de la ventana; las de fuera se informan como advertencia porque
    reusar el número de un talonario viejo es legítimo.
    """
    tipo = (tipo_destino or dte.tipo_documento or '').upper().strip()
    rut = getattr(getattr(dte, 'emisor', None), 'rut', '?')
    if fecha_ref is None:
        fecha_ref = dte.fecha_emision
    ventana = ventana_folio_reusable(tipo, fecha_ref)

    dentro = [c for c in colisiones if c.get('dentro_ventana', True)]
    fuera = [c for c in colisiones if not c.get('dentro_ventana', True)]

    errores, advertencias = [], []
    if dentro:
        if ventana:
            errores.append(
                f'El folio {folio} ya está usado en otra {tipo} del RUT {rut} '
                f'dentro de los {VENTANA_MESES_FOLIO_REUSABLE} meses previos o '
                f'posteriores al {fecha_ref.strftime("%d/%m/%Y")} '
                f'({ventana[0].strftime("%d/%m/%Y")} a '
                f'{ventana[1].strftime("%d/%m/%Y")}): '
                + _detalle_colisiones(dentro)
            )
        else:
            errores.append(
                f'El folio {folio} ya está emitido para {tipo} bajo el RUT '
                f'{rut}: ' + _detalle_colisiones(dentro)
            )
    if fuera:
        advertencias.append(
            f'El folio {folio} ya se usó en otra {tipo} del RUT {rut}, pero '
            f'fuera de la ventana de {VENTANA_MESES_FOLIO_REUSABLE} meses '
            f'(talonario anterior): ' + _detalle_colisiones(fuera)
        )
    return errores, advertencias


def rango_correlativo_de(dte, folio=None):
    """(base_inicial, termino, siguiente_a_emitir) del correlativo del DTE.

    Es el sustituto local del rango CAF: este ERP no modela el CAF del SII,
    lo más cercano es `Correlativo(sucursal, tipo_dte, rango_inicial,
    inicio, termino)`. Devuelve None si no hay correlativo definido.

    Un par (sucursal, tipo) puede tener **más de un** `Correlativo`: en
    producción hay una sucursal con dos rangos de FACTURA EXENTA (uno
    acotado y otro amplio) porque el rango se redefinió sin borrar el
    anterior. El `.first()` sin `order_by` que había aquí dejaba la elección
    en manos del plan de PostgreSQL, así que la advertencia de rango salía o
    no salía sin que nada hubiese cambiado. Criterio determinístico:

    1. Si se pasa `folio` y algún correlativo lo cubre, gana ése: si hay un
       rango configurado que autoriza el folio, no tiene sentido advertir.
    2. Si no, gana el de `id` más alto — el último definido, que es el que
       la operación considera vigente.
    """
    from app.models import Correlativo

    if not dte.sucursal_id:
        return None

    tipo = (dte.tipo_documento or '').upper().strip()
    candidatos = list(
        Correlativo.objects
        .filter(sucursal_id=dte.sucursal_id)
        .annotate(_tipo_norm=Upper(Trim('tipo_dte')))
        .filter(_tipo_norm=tipo)
        .order_by('-id')
    )
    if not candidatos:
        return None

    corr = candidatos[0]
    if folio is not None and len(candidatos) > 1:
        for cand in candidatos:
            if (cand.base_inicial is not None and cand.termino is not None
                    and cand.base_inicial <= folio <= cand.termino):
                corr = cand
                break

    return (corr.base_inicial, corr.termino, corr.inicio)


def validar_folio_dte(dte, nuevo_folio, *, incluir_descartados=False,
                      fecha_ref=None) -> dict:
    """Valida un folio de destino antes de escribirlo.

    Devuelve::

        {
          'ok': bool,                 # False => NO escribir
          'errores': [str, ...],      # bloqueantes
          'advertencias': [str, ...], # informativas, requieren confirmación
          'colisiones': [dict, ...],  # detalle de los DTE que ya usan el folio
          'rango': (inicio, termino, siguiente) | None,
        }

    Criterios:

    * **Error** — el folio ya está usado por otro DTE del mismo RUT emisor
      y mismo tipo, en *cualquier* sucursal (dos fichas de `Empresa` del
      mismo contribuyente pueden solaparse; ver el docstring del módulo).
    * **Advertencia** — el folio cae fuera del rango del `Correlativo` de
      la sucursal (proxy del CAF). No se bloquea porque hay 8.033 DTEs de
      2026 en producción fuera de rango por rangos redefinidos a posteriori.
    * **Advertencia** — el folio es >= al "siguiente a emitir": el sistema
      lo volverá a entregar en una emisión futura y se generará un
      duplicado más adelante.
    """
    errores: list[str] = []
    advertencias: list[str] = []

    try:
        nuevo_folio = int(nuevo_folio)
    except (TypeError, ValueError):
        return {
            'ok': False,
            'errores': ['El folio debe ser un número entero'],
            'advertencias': [],
            'colisiones': [],
            'rango': None,
        }

    if nuevo_folio <= 0:
        errores.append('El folio debe ser mayor a 0')
        return {
            'ok': False, 'errores': errores, 'advertencias': [],
            'colisiones': [], 'rango': None,
        }

    # `fecha_ref`: fecha con la que quedará el documento. Si la misma
    # edición mueve `fecha_emision`, hay que pasar la NUEVA — si no, la
    # ventana de unicidad de los tipos de talonario se centra en la fecha
    # vieja y puede dejar pasar (o bloquear de más) un folio del período
    # al que el documento realmente se va a mover.
    if fecha_ref is None:
        fecha_ref = dte.fecha_emision
    colisiones = buscar_colisiones_folio(
        dte, nuevo_folio, incluir_descartados=incluir_descartados,
        fecha_ref=fecha_ref,
    )
    errores_col, advertencias_col = mensajes_por_colisiones(
        dte, nuevo_folio, colisiones,
        tipo_destino=dte.tipo_documento, fecha_ref=fecha_ref,
    )
    errores.extend(errores_col)
    advertencias.extend(advertencias_col)

    rango = None
    tipo = (dte.tipo_documento or '').upper().strip()
    if tipo in TIPOS_CON_FOLIO_SII:
        rango = rango_correlativo_de(dte, folio=nuevo_folio)
        if rango:
            base, termino, siguiente = rango
            if base is not None and termino is not None:
                if not (base <= nuevo_folio <= termino):
                    advertencias.append(
                        f'El folio {nuevo_folio} queda fuera del rango '
                        f'autorizado de esta sucursal para {dte.tipo_documento} '
                        f'({base}-{termino}). Verifique el CAF antes de continuar.'
                    )
            if siguiente is not None and nuevo_folio >= siguiente:
                advertencias.append(
                    f'El folio {nuevo_folio} todavía no se ha emitido '
                    f'(el siguiente del correlativo es {siguiente}): el sistema '
                    'lo volverá a entregar en una emisión futura y quedará '
                    'duplicado.'
                )
        else:
            advertencias.append(
                'No hay correlativo configurado para esta sucursal y tipo de '
                'documento: no se pudo verificar el rango autorizado.'
            )

    return {
        'ok': not errores,
        'errores': errores,
        'advertencias': advertencias,
        'colisiones': colisiones,
        'rango': rango,
    }


# ---------------------------------------------------------------------------
# Reemplazo seguro del folio dentro de textos libres
# ---------------------------------------------------------------------------

def reemplazar_folio_en_texto(texto, folio_anterior, folio_nuevo) -> str:
    """Reemplaza el folio en un texto libre SIN destrozar el resto.

    `editar_folio_dte` hacía ``texto.replace(str(folio_anterior), str(nuevo))``
    a secas. Con folios cortos eso corrompe el texto: para la FACTURA EXENTA
    folio 1 (DTE 2180814, real en producción) convierte
    ``'Ingreso manual - 10400190076 Talla 00'`` en
    ``'Ingreso manual - 987654040098765490076 Talla 00'``.

    Aquí solo se sustituye cuando el número aparece como token completo
    (precedido por ``#`` o delimitado por caracteres no numéricos).
    """
    if not texto:
        return texto or ''
    fa = str(folio_anterior)
    fn = str(folio_nuevo)
    # 1) '#<folio>' (formato usado por las observaciones del sistema).
    texto = re.sub(rf'#{re.escape(fa)}(?!\d)', f'#{fn}', texto)
    # 2) número suelto delimitado (no pegado a otros dígitos).
    texto = re.sub(rf'(?<!\d){re.escape(fa)}(?!\d)', fn, texto)
    return texto


def dtes_con_referencia_a_folio(emisor, folio):
    """Queryset acotado de DTEs cuyo JSON de `referencias` menciona `folio`.

    `editar_folio_dte` recorría **todos** los DTE del emisor con
    `referencias` no vacío. Para el emisor 1319 eso son 267.402 filas
    (medido en producción: 17,4 s solo en iterar, dentro de la transacción,
    para encontrar 136 JSON válidos). Acotar por `referencias__contains`
    deja el trabajo en un puñado de filas.

    OJO con el patrón de búsqueda: `Dte.referencias` es un **TextField**, no
    un JSONField, así que `__contains` es un LIKE sobre el texto crudo. La
    versión original filtraba por `'"<folio>"'` (entre comillas) suponiendo
    que el folio se guardaba como cadena, pero el JSON lo guarda como
    **entero** — `[{"tipo_documento": 39, "folio": 285423, ...}]` — y además
    el campo a veces trae texto libre (`'TICKET-184120'`). Con las comillas
    el filtro no calzaba NUNCA y la cascada de referencias quedaba muda, que
    es bastante peor que la lentitud que venía a resolver. Verificado en
    producción: para el folio 285423 del emisor 1320 devolvía 0 filas cuando
    el escaneo completo encuentra 1 (el DTE 2192328).

    Se filtra por el folio sin comillas y se aceptan falsos positivos (un
    folio corto puede aparecer dentro de otro número): quien consume esto
    parsea el JSON y compara el folio exacto, así que un falso positivo se
    descarta ahí y no escribe nada.
    """
    from app.models import Dte

    return (
        Dte.objects
        .filter(emisor=emisor, referencias__contains=str(folio))
        .only('id', 'tipo_documento', 'numero_documento', 'referencias')
    )


# ---------------------------------------------------------------------------
# Sincronizaciones que hoy faltan al editar
# ---------------------------------------------------------------------------

def sincronizar_ticket_por_cambio_folio(dte, folio_anterior) -> int:
    """Reapunta el `Ticket` de venta al nuevo folio del DTE.

    El TXT Acepta de una boleta se arma buscando el ticket por
    ``folio_dte == Dte.numero_documento`` y ``sucursal``. Si el folio
    cambia y el ticket no se mueve, el vínculo se rompe y el TXT sale
    degradado: el campo del vendedor pasa del código numérico al username
    del responsable y desaparece el correlativo de ticket. Verificado en
    producción reproduciendo la generación del TXT antes y después.

    Debe llamarse **dentro** de la transacción del endpoint, después de
    guardar el nuevo folio. Devuelve la cantidad de tickets actualizados.

    Se usa ``.update()`` (no ``.save()``) porque ``Ticket.fecha`` es
    ``auto_now`` y un save lo reescribiría a hoy.
    """
    from app.models import Ticket

    if folio_anterior is None or folio_anterior == dte.numero_documento:
        return 0

    actualizados = Ticket.objects.filter(
        sucursal_id=dte.sucursal_id,
        folio_dte=folio_anterior,
    ).update(folio_dte=dte.numero_documento)

    if actualizados:
        logger.info(
            '[FOLIO] Ticket(s) reapuntados por cambio de folio DTE #%s: '
            '%s -> %s (%s ticket/s)',
            dte.id, folio_anterior, dte.numero_documento, actualizados,
        )
    return actualizados


def sincronizar_vendedor_ticket(dte) -> int:
    """Alinea el vendedor del `Ticket` con el del DTE.

    El reporte de comisiones lee ``Dte.vendedor``; el TXT Acepta de las
    boletas lee ``Ticket.vendedor``. Al editar solo el DTE quedan en
    desacuerdo: hay varias decenas de documentos en producción donde el
    vendedor que muestra la pantalla no es el que viaja al SII en el TXT,
    con lo que la comisión se le paga a una persona y el documento queda
    emitido a nombre de otra.
    """
    from app.models import Ticket

    if not dte.vendedor_id or not dte.numero_documento:
        return 0

    return Ticket.objects.filter(
        sucursal_id=dte.sucursal_id,
        folio_dte=dte.numero_documento,
    ).exclude(vendedor_id=dte.vendedor_id).update(vendedor_id=dte.vendedor_id)


# ---------------------------------------------------------------------------
# Auditoría
# ---------------------------------------------------------------------------

def ip_de(request):
    """IP del cliente respetando el proxy (mismo criterio que views.py)."""
    if request is None:
        return None
    return (
        (request.META.get('HTTP_X_FORWARDED_FOR', '') or '').split(',')[0].strip()
        or request.META.get('REMOTE_ADDR')
    )


def registrar_cambio_folio_dte(dte, folio_anterior, folio_nuevo, motivo,
                               request=None, referencias_actualizadas=None):
    """Escribe en `HistorialCambioFolioDte` (migración 0147, ya aplicada).

    `editar_dte_boleta_papel` cambia el folio y **no deja rastro**: la
    tabla tiene 0 filas en producción. Esta función existe para que
    cualquier vía que mueva un folio audite igual.
    """
    from app.models.dte import HistorialCambioFolioDte

    usuario = getattr(request, 'user', None) if request else None
    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None

    return HistorialCambioFolioDte.objects.create(
        dte=dte,
        folio_anterior=folio_anterior,
        folio_nuevo=folio_nuevo,
        motivo=(motivo or '')[:2000],
        usuario=usuario,
        usuario_nombre=(
            (usuario.get_full_name() or usuario.username)[:150]
            if usuario else ''
        ),
        referencias_actualizadas=referencias_actualizadas or [],
        ip_cliente=ip_de(request),
    )


def registrar_cambio_vendedor_dte(dte, vendedor_anterior, vendedor_nuevo,
                                  request=None, motivo=''):
    """Audita el cambio de vendedor de un DTE.

    Cambiar el vendedor mueve la comisión de una persona a otra: el
    reporte de comisiones (`_calcular_comisiones_vendedor`) agrupa por
    ``Dte.vendedor`` y aplica ``Vendedor.comision``. Hoy ese cambio no
    deja ningún registro ni línea de log.

    Se usa `RegistroAutorizacion` porque ya existe y no requiere
    migración. Lo ideal sería un modelo propio (ver `no_hecho`).
    """
    from app.models import RegistroAutorizacion

    usuario = getattr(request, 'user', None) if request else None
    if usuario is not None and not getattr(usuario, 'is_authenticated', False):
        usuario = None

    def _label(v):
        if v is None:
            return 'SIN VENDEDOR'
        return f'{v.codigo_vendedor} - {v.nombre}'

    monto_fmt = f'{int(dte.monto_con_iva or 0):,}'.replace(',', '.')
    descripcion = (
        f'Cambio de vendedor en {dte.tipo_documento} #{dte.numero_documento} '
        f'({_label(vendedor_anterior)} -> {_label(vendedor_nuevo)}). '
        f'Monto ${monto_fmt}'
    )
    if motivo:
        descripcion += f'. Motivo: {motivo[:200]}'

    logger.info('[DTE-VENDEDOR] %s | usuario=%s', descripcion,
                getattr(usuario, 'username', 'anonimo'))

    return RegistroAutorizacion.objects.create(
        usuario_solicitante=usuario,
        tipo_operacion='OTRO',
        descripcion=descripcion,
        ip_origen=ip_de(request),
        exitoso=True,
        datos_adicionales={
            'evento': 'CAMBIO_VENDEDOR_DTE',
            'dte_id': dte.id,
            'tipo_documento': dte.tipo_documento,
            'numero_documento': dte.numero_documento,
            'sucursal_id': dte.sucursal_id,
            'fecha_emision': (
                dte.fecha_emision.strftime('%Y-%m-%d')
                if dte.fecha_emision else None
            ),
            'monto_con_iva': int(dte.monto_con_iva or 0),
            'monto_neto': int(dte.monto_neto or 0),
            'vendedor_anterior_id': getattr(vendedor_anterior, 'id', None),
            'vendedor_anterior': _label(vendedor_anterior),
            'vendedor_nuevo_id': getattr(vendedor_nuevo, 'id', None),
            'vendedor_nuevo': _label(vendedor_nuevo),
            'motivo': motivo or '',
        },
    )
