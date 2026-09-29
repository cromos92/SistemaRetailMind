"""
Comprobante de rechazo de traspaso (Recepción DTE) en PDF A4.

Por qué existe: al rechazar un traspaso el stock vuelve al origen
(`rechazar_recepcion_api`), pero no había ningún papel que respaldara quién
rechazó, cuándo, por qué y qué unidades volvieron; el destino no tenía nada
que firmar ni el origen nada que archivar (auditoría R-05).

De dónde salen los datos (sin migraciones — no hay modelo de eventos de
rechazo): `rechazar_recepcion_api` deja en cada TRASPASO_SALIDA una línea
``[RECHAZO_JSON] {...}`` dentro de `observaciones` (usuario, fecha-hora,
motivo, cantidad devuelta, lote de reposición) y en `Dte.referencias` el
bloque de texto "RECEPCIÓN RECHAZADA por ...". Ambos son append-only, así que
el comprobante sigue saliendo después de cancelar o rehabilitar (que ponen
`motivo_rechazo=None` y borran la NotificacionDTE). Los rechazos anteriores a
la marca se reconstruyen desde `referencias` + las salidas CANCELADO/RECHAZADO.

Estilo: mismo A4/reportlab que `exportar_productos_regularizar_pdf` y los
helpers de texto/código de barras de `pdf_guia_preparacion`.
"""
from __future__ import annotations

import json
import logging
import re
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.pdf_guia_preparacion import _barcode, _txt

logger = logging.getLogger('app')

# Prefijo de la línea JSON que `rechazar_recepcion_api` agrega a
# `Movimientos_Producto.observaciones` de cada TRASPASO_SALIDA rechazado.
MARCA_RECHAZO_JSON = '[RECHAZO_JSON]'

_RE_MARCA = re.compile(re.escape(MARCA_RECHAZO_JSON) + r' (\{.*?\})\s*(?:\n|$)')
_RE_REFERENCIA = re.compile(
    r'RECEPCI[ÓO]N RECHAZADA por (\S+) el (\d{4}-\d{2}-\d{2} \d{2}:\d{2})'
    r'\nMotivo: (.*?)\nUnidades devueltas al stock de origen: (\d+)',
    re.S,
)


def _marca_rechazo(observaciones):
    """Último bloque [RECHAZO_JSON] de una observación (None si no hay)."""
    ultima = None
    for m in _RE_MARCA.finditer(observaciones or ''):
        try:
            ultima = json.loads(m.group(1))
        except ValueError:
            continue
    return ultima


def _evento_desde_referencias(dte):
    """Reconstruye (usuario, fecha, motivo, unidades) del último rechazo
    anotado en `Dte.referencias`. Es el respaldo para rechazos anteriores a
    la marca JSON y para completar la fecha si algún movimiento no la trae."""
    ultimo = None
    for m in _RE_REFERENCIA.finditer(dte.referencias or ''):
        ultimo = {
            'usuario': m.group(1),
            'fecha': m.group(2),
            'motivo': m.group(3).strip(),
            'unidades': int(m.group(4)),
        }
    return ultimo


def _fmt_fecha_hora(valor):
    """'2026-09-29T10:11:05' / '2026-09-29 10:11' → '29/09/2026 10:11'."""
    if not valor:
        return '-'
    s = str(valor).replace('T', ' ')
    try:
        fecha, hora = s.split(' ', 1)
        a, m, d = fecha.split('-')
        return f'{d}/{m}/{a} {hora[:5]}'
    except ValueError:
        return s


def _fmt_fecha(valor):
    if not valor:
        return '-'
    try:
        return valor.strftime('%d/%m/%Y')
    except AttributeError:
        return str(valor)


def payload_comprobante_rechazo(dte, sucursal_destino=None, impreso_por=''):
    """Un solo lugar decide qué sale en el comprobante (el endpoint y el
    PDF lo consumen tal cual). Devuelve None si el DTE no registra ningún
    rechazo (ni marca JSON, ni bloque en referencias, ni estado RECHAZADO)."""
    from app.models import Movimientos_Producto

    movs = list(
        Movimientos_Producto.objects
        .filter(dte=dte, concepto='TRASPASO_SALIDA')
        .select_related('ProductoTalla__producto')
        .order_by('id')
    )
    origen = dte.sucursal
    alias_origen = getattr(origen, 'alias', None) or '-'

    lineas = []
    evento = None
    for mov in movs:
        marca = _marca_rechazo(mov.observaciones)
        if not marca:
            continue
        evento = evento or marca
        talla = mov.ProductoTalla
        lineas.append({
            'sku': getattr(talla, 'sku', '') or '',
            'articulo': getattr(getattr(talla, 'producto', None), 'articulo', '') or '',
            'talla': getattr(talla, 'talla', '') or '',
            'cantidad': int(marca.get('cantidad') or abs(mov.cantidad or 0)),
            'lote_id': marca.get('lote_id'),
            'movimiento_id': mov.id,
            'stock_devuelto': bool(marca.get('cantidad')),
            'devuelto_a': alias_origen,
        })

    referencia = _evento_desde_referencias(dte)
    if not lineas:
        # Rechazo anterior a la marca JSON (o sin líneas con talla): se
        # reconstruye desde referencias + las salidas revertidas. Sin ningún
        # rastro de rechazo no hay comprobante que emitir.
        if referencia is None and dte.estado_dte != 'RECHAZADO':
            return None
        for mov in movs:
            obs = mov.observaciones or ''
            revertida = mov.estado in ('CANCELADO', 'RECHAZADO')
            if not (revertida and ('RECHAZ' in obs.upper() or dte.estado_dte == 'RECHAZADO')):
                continue
            talla = mov.ProductoTalla
            lineas.append({
                'sku': getattr(talla, 'sku', '') or '',
                'articulo': getattr(getattr(talla, 'producto', None), 'articulo', '') or '',
                'talla': getattr(talla, 'talla', '') or '',
                'cantidad': abs(int(mov.cantidad or 0)),
                'lote_id': None,
                'movimiento_id': mov.id,
                # En RECHAZADO (rechazo previo al fix) el stock nunca volvió.
                'stock_devuelto': mov.estado == 'CANCELADO',
                'devuelto_a': alias_origen,
            })
        evento = referencia or {
            'usuario': '-', 'fecha': None,
            'motivo': dte.motivo_rechazo or '', 'unidades': sum(l['cantidad'] for l in lineas),
        }

    # La marca JSON manda; referencias completa lo que falte.
    if referencia:
        for clave in ('usuario', 'fecha', 'motivo'):
            if not evento.get(clave):
                evento[clave] = referencia.get(clave)

    total_unidades = sum(l['cantidad'] for l in lineas)
    unidades_devueltas = sum(l['cantidad'] for l in lineas if l['stock_devuelto'])
    empresa_origen = getattr(origen, 'empresa', None) or dte.emisor
    empresa_destino = getattr(sucursal_destino, 'empresa', None) or dte.receptor

    return {
        'dte_id': dte.id,
        'codigo': f'RCH-{dte.id}',
        'tipo_documento': dte.tipo_documento or '',
        'folio': dte.numero_documento,
        'fecha_emision': _fmt_fecha(dte.fecha_emision),
        'estado_actual': dte.estado_dte or '',
        'origen': {
            'empresa': getattr(empresa_origen, 'nombre', '') or '',
            'rut': getattr(empresa_origen, 'rut', '') or '',
            'alias': alias_origen,
            'direccion': getattr(origen, 'direccion', '') or '',
        },
        'destino': {
            'empresa': getattr(empresa_destino, 'nombre', '') or '',
            'rut': getattr(empresa_destino, 'rut', '') or '',
            'alias': getattr(sucursal_destino, 'alias', None) or '-',
            'direccion': getattr(sucursal_destino, 'direccion', '') or '',
        },
        'rechazo': {
            'usuario': evento.get('usuario') or '-',
            'fecha_hora': _fmt_fecha_hora(evento.get('fecha')),
            'motivo': evento.get('motivo') or '-',
        },
        'lineas': lineas,
        'total_unidades': total_unidades,
        'unidades_devueltas': unidades_devueltas,
        'impreso_por': impreso_por or '',
        'fecha_impresion': timezone.localtime().strftime('%d/%m/%Y %H:%M'),
    }


def _estilos_a4():
    base = getSampleStyleSheet()
    return {
        'titulo': ParagraphStyle('rch_titulo', parent=base['Title'], fontSize=15, leading=18,
                                 spaceAfter=2),
        'subtitulo': ParagraphStyle('rch_sub', parent=base['Normal'], fontSize=8.5, leading=10,
                                    alignment=TA_CENTER, textColor=colors.HexColor('#B91C1C')),
        'seccion': ParagraphStyle('rch_seccion', parent=base['Heading4'], fontSize=10, leading=12,
                                  spaceBefore=6, spaceAfter=3),
        'normal': ParagraphStyle('rch_normal', parent=base['Normal'], fontSize=9, leading=11),
        'celda': ParagraphStyle('rch_celda', parent=base['Normal'], fontSize=8.5, leading=10),
        'celda_b': ParagraphStyle('rch_celda_b', parent=base['Normal'], fontSize=8.5, leading=10,
                                  fontName='Helvetica-Bold'),
        'nota': ParagraphStyle('rch_nota', parent=base['Normal'], fontSize=9, leading=11,
                               textColor=colors.HexColor('#1A1A2E')),
        'pie': ParagraphStyle('rch_pie', parent=base['Normal'], fontSize=7.5, leading=9,
                              textColor=colors.HexColor('#555555'), alignment=TA_CENTER),
        'firma': ParagraphStyle('rch_firma', parent=base['Normal'], fontSize=8.5, leading=11,
                                alignment=TA_CENTER),
    }


def _tabla_datos(filas, ancho, st, col1=4.2 * cm):
    """Tabla de dos columnas etiqueta / valor."""
    data = [[Paragraph(_txt(k), st['celda_b']), Paragraph(_txt(v), st['celda'])] for k, v in filas]
    t = Table(data, colWidths=[col1, ancho - col1])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    return t


def generar_comprobante_rechazo_pdf(payload) -> bytes:
    """PDF A4 (portrait) del comprobante. Lanza sólo si ReportLab falla de
    raíz; los campos faltantes se toleran uno por uno."""
    st = _estilos_a4()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm, bottomMargin=1.4 * cm,
        title=f'Comprobante de rechazo {payload.get("codigo", "")}', author='RetailMind',
    )
    ancho = A4[0] - doc.leftMargin - doc.rightMargin
    origen = payload.get('origen') or {}
    destino = payload.get('destino') or {}
    rechazo = payload.get('rechazo') or {}
    els = []

    els.append(Paragraph('COMPROBANTE DE RECHAZO DE TRASPASO', st['titulo']))
    els.append(Paragraph('NO VÁLIDO COMO DOCUMENTO TRIBUTARIO — respaldo interno de devolución de stock',
                         st['subtitulo']))
    els.append(Spacer(1, 0.35 * cm))

    # Encabezado: origen (recibe de vuelta) | destino (rechazó)
    def _bloque_sucursal(titulo, s):
        partes = [f'<b>{_txt(titulo)}</b>', _txt(s.get('empresa')).upper()]
        if s.get('rut'):
            partes.append('RUT ' + _txt(s.get('rut')))
        partes.append('Sucursal: <b>' + _txt(s.get('alias')) + '</b>')
        if s.get('direccion'):
            partes.append(_txt(s.get('direccion')))
        return Paragraph('<br/>'.join(p for p in partes if p), st['celda'])

    cab = Table(
        [[_bloque_sucursal('ORIGEN (recibe la mercadería de vuelta)', origen),
          _bloque_sucursal('DESTINO (rechazó la recepción)', destino)]],
        colWidths=[ancho / 2, ancho / 2],
    )
    cab.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, colors.HexColor('#405189')),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#405189')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F4F6FB')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    els.append(cab)

    els.append(Paragraph('Documento rechazado', st['seccion']))
    els.append(_tabla_datos([
        ('Documento', f'{payload.get("tipo_documento") or "DTE"} N° {payload.get("folio") or "-"}'),
        ('Fecha de emisión', payload.get('fecha_emision') or '-'),
        ('Estado actual', payload.get('estado_actual') or '-'),
        ('Código comprobante', payload.get('codigo') or '-'),
    ], ancho, st))

    els.append(Paragraph('Rechazo', st['seccion']))
    els.append(_tabla_datos([
        ('Fecha y hora', rechazo.get('fecha_hora') or '-'),
        ('Rechazado por', rechazo.get('usuario') or '-'),
        ('Motivo', rechazo.get('motivo') or '-'),
    ], ancho, st))

    # Líneas rechazadas
    els.append(Paragraph('Unidades rechazadas', st['seccion']))
    anchos = [2.6 * cm, ancho - (2.6 + 1.8 + 1.7 + 3.6) * cm, 1.8 * cm, 1.7 * cm, 3.6 * cm]
    data = [[Paragraph(h, st['celda_b']) for h in ('SKU', 'Artículo', 'Talla', 'Cant.', 'Destino del stock')]]
    for ln in payload.get('lineas') or []:
        destino_txt = (f'Devuelto a {ln.get("devuelto_a")}' if ln.get('stock_devuelto')
                       else 'Sin devolución (rechazo previo al fix)')
        if ln.get('lote_id'):
            destino_txt += f' · lote #{ln["lote_id"]}'
        data.append([
            Paragraph(_txt(ln.get('sku')), st['celda']),
            Paragraph(_txt(ln.get('articulo'), 90), st['celda']),
            Paragraph(_txt(ln.get('talla')), st['celda']),
            Paragraph(_txt(ln.get('cantidad')), st['celda_b']),
            Paragraph(_txt(destino_txt), st['celda']),
        ])
    if len(data) == 1:
        data.append([Paragraph('Sin líneas con movimiento de despacho registrado.', st['celda']), '', '', '', ''])
    data.append(['', Paragraph('<b>Total unidades</b>', st['celda_b']), '',
                 Paragraph(f'<b>{payload.get("total_unidades", 0)}</b>', st['celda_b']), ''])
    tabla = Table(data, colWidths=anchos, repeatRows=1)
    estilo = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#405189')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -2), 0.4, colors.HexColor('#C7CCDA')),
        ('LINEABOVE', (0, -1), (-1, -1), 0.8, colors.HexColor('#405189')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (3, 1), (3, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor('#F7F8FB')]),
    ]
    if len(data) == 3 and data[1][1] == '':
        estilo.append(('SPAN', (0, 1), (-1, 1)))
    # La cabecera lleva Paragraph con estilo propio: forzar el color del texto.
    for i in range(len(data[0])):
        data[0][i] = Paragraph(
            f'<font color="#FFFFFF"><b>{("SKU", "Artículo", "Talla", "Cant.", "Destino del stock")[i]}</b></font>',
            st['celda'])
    tabla = Table(data, colWidths=anchos, repeatRows=1)
    tabla.setStyle(TableStyle(estilo))
    els.append(tabla)

    els.append(Spacer(1, 0.3 * cm))
    devueltas = payload.get('unidades_devueltas', 0)
    if devueltas:
        nota = (f'<b>Stock devuelto al inventario de {_txt(origen.get("alias"))}:</b> '
                f'{devueltas} unidad{"" if devueltas == 1 else "es"} reingresaron al stock y a la capa '
                f'FIFO del origen con la antigüedad del despacho. La sucursal destino no recibió ninguna unidad.')
    else:
        nota = ('<b>Sin devolución automática de stock:</b> este rechazo es anterior a la devolución '
                'automática; las unidades siguen descontadas del origen hasta que el emisor '
                'cancele o rehabilite el documento.')
    els.append(Paragraph(nota, st['nota']))

    # Código de barras del comprobante (escaneable para ubicar el DTE).
    codigo_barras = _barcode(payload.get('codigo') or '', ancho * 0.5)
    if codigo_barras is not None:
        els.append(Spacer(1, 0.35 * cm))
        els.append(codigo_barras)
        els.append(Paragraph(_txt(payload.get('codigo')), st['pie']))

    # Firmas
    els.append(Spacer(1, 1.6 * cm))
    firmas = Table(
        [[Paragraph('______________________________<br/>Rechaza (sucursal destino)<br/>'
                    + _txt(destino.get('alias')), st['firma']),
          Paragraph('______________________________<br/>Recibe de vuelta (sucursal origen)<br/>'
                    + _txt(origen.get('alias')), st['firma'])]],
        colWidths=[ancho / 2, ancho / 2],
    )
    els.append(firmas)

    els.append(Spacer(1, 0.6 * cm))
    els.append(Paragraph(
        f'Impreso por {_txt(payload.get("impreso_por") or "-")} el {_txt(payload.get("fecha_impresion"))} · '
        f'RetailMind · Recepción DTE',
        st['pie']))

    doc.build(els)
    return buffer.getvalue()
