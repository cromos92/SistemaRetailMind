"""
Guía de preparación (picking) en PDF térmico 80mm.

Por qué existe: la guía se imprimía SOLO por ESC/POS vía QZ Tray, que hay que
instalar y habilitar sucursal por sucursal — donde no está configurado, la
tienda simplemente no podía imprimir. Este generador produce el mismo papel en
PDF, que abre en cualquier navegador (mismo enfoque que usa AllConnected en
`system/shipping/services/pdf_generator.py`, para que el formato le resulte
familiar al personal de tienda).

Diferencia deliberada con el PDF de AllConnected: acá NO van precios ni totales.
Es un documento de PICKING —qué sacar y cuánto— y mezclarlo con montos invita a
confundirlo con la boleta. Lleva casilla por línea, SKU, talla, stock del
sistema y el total de unidades a sacar.

Un `PageBreak` por pedido: un solo PDF sirve para el lote de toda la sucursal.
"""
from __future__ import annotations

import logging
from io import BytesIO

from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger('app')

# Papel térmico estándar de las tiendas. 80mm ≈ 226.77pt (mismo ancho que el
# PDF de AllConnected). El alto se estima por contenido: si queda corto,
# ReportLab pasa a una página nueva —nunca recorta—, así que se prefiere
# subestimar antes que hacer avanzar 70cm de papel en cada impresión.
ANCHO_PAPEL_MM = 80
MARGEN_MM = 3.5
ALTO_MINIMO_MM = 120
ALTO_MAXIMO_MM = 900


def _estilos():
    return {
        'empresa': ParagraphStyle('empresa', fontName='Helvetica-Bold', fontSize=11,
                                  alignment=TA_CENTER, leading=13, spaceAfter=1),
        'sucursal': ParagraphStyle('sucursal', fontName='Helvetica', fontSize=8.5,
                                   alignment=TA_CENTER, leading=10),
        'banner': ParagraphStyle('banner', fontName='Helvetica-Bold', fontSize=14,
                                 alignment=TA_CENTER, leading=16, spaceBefore=2, spaceAfter=1),
        'banner_sub': ParagraphStyle('banner_sub', fontName='Helvetica', fontSize=7.5,
                                     alignment=TA_CENTER, leading=9, textColor=colors.HexColor('#444444')),
        'ticket': ParagraphStyle('ticket', fontName='Helvetica-Bold', fontSize=12,
                                 alignment=TA_CENTER, leading=14),
        'label': ParagraphStyle('label', fontName='Helvetica-Bold', fontSize=8.5, leading=10),
        'valor': ParagraphStyle('valor', fontName='Helvetica', fontSize=8.5, leading=10),
        'seccion': ParagraphStyle('seccion', fontName='Helvetica-Bold', fontSize=9.5,
                                  alignment=TA_LEFT, leading=11, spaceBefore=2, spaceAfter=1),
        'prod': ParagraphStyle('prod', fontName='Helvetica-Bold', fontSize=10, leading=12),
        'prod_meta': ParagraphStyle('prod_meta', fontName='Helvetica', fontSize=8, leading=9.5,
                                    textColor=colors.HexColor('#333333')),
        'cant': ParagraphStyle('cant', fontName='Helvetica-Bold', fontSize=13,
                               alignment=TA_CENTER, leading=15),
        'casilla': ParagraphStyle('casilla', fontName='Helvetica', fontSize=15,
                                  alignment=TA_CENTER, leading=16),
        'total': ParagraphStyle('total', fontName='Helvetica-Bold', fontSize=11,
                                alignment=TA_CENTER, leading=13),
        'pie': ParagraphStyle('pie', fontName='Helvetica-Bold', fontSize=8.5,
                              alignment=TA_CENTER, leading=11),
        'firma': ParagraphStyle('firma', fontName='Helvetica', fontSize=8,
                                alignment=TA_LEFT, leading=14),
        'alerta': ParagraphStyle('alerta', fontName='Helvetica-Bold', fontSize=8,
                                 alignment=TA_LEFT, leading=10,
                                 textColor=colors.HexColor('#B91C1C')),
    }


def _txt(valor, limite=None):
    """Texto seguro para Paragraph (escapa &, <, > y recorta)."""
    s = str(valor or '').strip()
    if limite and len(s) > limite:
        s = s[:limite - 1] + '…'
    return _xml_escape(s)


def _linea(ancho, grosor=0.8, color='#000000'):
    t = Table([['']], colWidths=[ancho], rowHeights=[0.5])
    t.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, -1), grosor, colors.HexColor(color)),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def _barcode(texto, ancho_disponible):
    """Code128 del ticket, escaneable en el mesón.

    Se usa `createBarcodeDrawing` (devuelve un Drawing) y NO `code128.Code128`,
    que devuelve un Flowable y revienta con "Can only add Shape or UserNode
    objects to a Group" al meterlo en un Drawing — ese error deja el papel sin
    código de barras y solo se ve como un warning en el log.

    El ancho de barra se ajusta para que quepa en el papel: con tickets largos,
    0.62 se pasa de los 73mm útiles y el código sale cortado.
    """
    contenido = str(texto or '').strip()
    if not contenido:
        return None
    for bar_width in (0.62, 0.5, 0.4, 0.32):
        try:
            d = createBarcodeDrawing(
                'Code128', value=contenido, barHeight=13 * mm,
                barWidth=bar_width, humanReadable=False, quiet=False,
            )
        except Exception:  # pragma: no cover - defensivo
            logger.warning('No se pudo generar el código de barras para %r', contenido)
            return None
        if d.width <= ancho_disponible:
            d.hAlign = 'CENTER'
            return d
    d.hAlign = 'CENTER'   # el más angosto posible, aunque quede justo
    return d


def _alto_necesario(pedidos_ctx, ancho_util, st, margenes):
    """Alto de página REAL, midiendo los flowables con `wrap()`.

    Estimar por cantidad de ítems no sirve: el alto depende de cuántas líneas
    ocupa cada nombre, y quedarse corto parte la guía en dos páginas (pasó en la
    primera versión: 2 pedidos salían en 4 páginas). Acá se mide cada bloque y
    se toma el más alto, que es el que define el tamaño de página del documento.

    Los flowables medidos se DESCARTAN: el documento final se arma con objetos
    nuevos, para que ninguno arrastre estado del cálculo.
    """
    alto_max = 0
    for ctx in pedidos_ctx:
        alto = 0
        for f in _bloque_pedido(ctx, ancho_util, st):
            try:
                _, h = f.wrap(ancho_util, ALTO_MAXIMO_MM * mm)
            except Exception:  # pragma: no cover - si algo no mide, se ignora
                h = 0
            alto += h + getattr(f, 'spaceBefore', 0) + getattr(f, 'spaceAfter', 0)
        alto_max = max(alto_max, alto)

    # Colchón: los saltos de línea internos de Paragraph y el redondeo de las
    # tablas pueden agregar 1-2mm que wrap() no anticipa.
    total = alto_max + margenes + 6 * mm
    return max(ALTO_MINIMO_MM * mm, min(total, ALTO_MAXIMO_MM * mm))


def _bloque_pedido(ctx, ancho, st):
    """Flowables de UNA guía."""
    els = []
    suc = ctx.get('sucursal') or {}

    # ── Cabecera ────────────────────────────────────────────────
    if suc.get('empresa'):
        els.append(Paragraph(_txt(suc['empresa']).upper(), st['empresa']))
    linea_suc = ' · '.join(filter(None, [suc.get('alias'), suc.get('direccion')]))
    if linea_suc:
        els.append(Paragraph(_txt(linea_suc, 60), st['sucursal']))
    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1.5 * mm))

    els.append(Paragraph('GUÍA DE PREPARACIÓN', st['banner']))
    els.append(Paragraph('Documento interno de picking', st['banner_sub']))
    els.append(Spacer(1, 2 * mm))

    # ── Ticket + código de barras ───────────────────────────────
    ticket = str(ctx.get('numero_ticket_rm') or '').strip()
    bc = _barcode(ticket.replace('RM-', '') or ticket, ancho)
    if bc is not None:
        els.append(bc)
    if ticket:
        els.append(Paragraph(_txt(ticket), st['ticket']))
    els.append(Spacer(1, 2 * mm))

    # ── Datos del pedido ────────────────────────────────────────
    filas = []
    def _fila(lbl, val):
        if val:
            filas.append([Paragraph(f'<b>{lbl}</b>', st['label']),
                          Paragraph(_txt(val, 42), st['valor'])])

    _fila('Canal:', ctx.get('canal_origen'))
    _fila('N° pedido:', ctx.get('numero_pedido_canal'))
    _fila('Folio desp.:', ctx.get('folio_despacho'))
    _fila('Fecha:', ctx.get('fecha'))
    _fila('Sucursal:', suc.get('alias'))
    _fila('Cliente:', ctx.get('cliente'))
    if filas:
        t = Table(filas, colWidths=[ancho * 0.30, ancho * 0.70])
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0.6), ('BOTTOMPADDING', (0, 0), (-1, -1), 0.6),
        ]))
        els.append(t)

    if ctx.get('direccion_envio'):
        els.append(Spacer(1, 1 * mm))
        els.append(Paragraph(f"<b>Envío:</b> {_txt(ctx['direccion_envio'], 90)}", st['valor']))

    els.append(Spacer(1, 2 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1.5 * mm))

    # ── Productos a sacar ───────────────────────────────────────
    els.append(Paragraph('PRODUCTOS A SACAR', st['seccion']))
    els.append(Spacer(1, 1 * mm))

    items = ctx.get('items') or []
    total_unidades = 0
    for i, it in enumerate(items):
        cant = int(it.get('cantidad') or 1)
        total_unidades += cant

        meta = []
        if it.get('sku'):
            meta.append(f"SKU {_txt(it['sku'], 20)}")
        if it.get('talla'):
            meta.append(f"TALLA {_txt(it['talla'], 12)}")
        if it.get('stock_disponible') is not None:
            meta.append(f"stock sist.: {int(it['stock_disponible'])}")

        celda_izq = [Paragraph(_txt(it.get('nombre') or 'Ítem', 60), st['prod'])]
        if meta:
            celda_izq.append(Paragraph(' · '.join(meta), st['prod_meta']))
        if not it.get('encontrado', True):
            celda_izq.append(Paragraph('⚠ SIN STOCK SUFICIENTE EN EL SISTEMA', st['alerta']))

        fila = Table(
            [[Paragraph('☐', st['casilla']), Paragraph(f'{cant}', st['cant']), celda_izq]],
            colWidths=[ancho * 0.11, ancho * 0.13, ancho * 0.76],
        )
        fila.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 1),
            ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        els.append(fila)
        if i < len(items) - 1:
            els.append(Spacer(1, 0.8 * mm))
            els.append(_linea(ancho, 0.3, '#999999'))
            els.append(Spacer(1, 0.8 * mm))

    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1.5 * mm))

    # ── Total de unidades (lo que la tienda tiene que contar) ───
    caja = Table(
        [[Paragraph(f"TOTAL A SACAR: {total_unidades} "
                    f"{'UNIDAD' if total_unidades == 1 else 'UNIDADES'}", st['total'])]],
        colWidths=[ancho],
    )
    caja.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1.2, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    els.append(caja)

    # ── Pie ─────────────────────────────────────────────────────
    els.append(Spacer(1, 2.5 * mm))
    els.append(Paragraph('*** NO VÁLIDO COMO BOLETA ***', st['pie']))
    els.append(Spacer(1, 2 * mm))
    els.append(Paragraph('Preparado por: ______________________', st['firma']))
    return els


def generar_guia_preparacion_pdf(pedidos_ctx, ancho_mm: int = ANCHO_PAPEL_MM) -> bytes:
    """
    Arma el PDF térmico con una guía por pedido.

    `pedidos_ctx`: lista de dicts con
        numero_ticket_rm, canal_origen, numero_pedido_canal, folio_despacho,
        fecha, cliente, direccion_envio,
        sucursal: {empresa, alias, direccion},
        items: [{sku, nombre, talla, cantidad, stock_disponible, encontrado}]

    Devuelve los bytes del PDF. Lanza solo si ReportLab falla de raíz (el
    llamador responde 500); los datos incompletos se toleran campo por campo.
    """
    if not pedidos_ctx:
        raise ValueError('No hay pedidos para generar la guía.')

    margen = MARGEN_MM * mm
    margen_v = 3 * mm
    page_w = ancho_mm * mm
    ancho_util = page_w - 2 * margen
    st = _estilos()

    def _construir(alto_pagina):
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=(page_w, alto_pagina),
            leftMargin=margen, rightMargin=margen,
            topMargin=margen_v, bottomMargin=margen_v,
            title='Guía de preparación',
            author='RetailMind',
        )
        elements = []
        for idx, ctx in enumerate(pedidos_ctx):
            elements.extend(_bloque_pedido(ctx, ancho_util, st))
            if idx < len(pedidos_ctx) - 1:
                elements.append(PageBreak())
        doc.build(elements)
        return buffer.getvalue()

    # `wrap()` da una buena base pero subestima (tablas anidadas, saltos de
    # línea internos): si el alto queda corto la guía se parte en dos páginas y
    # sale media hoja de papel extra. Por eso se verifica el resultado y se
    # reintenta con más alto hasta que cada pedido entre en UNA página.
    esperado = len(pedidos_ctx)
    alto = _alto_necesario(pedidos_ctx, ancho_util, st, 2 * margen_v)
    pdf = _construir(alto)
    for _ in range(4):
        if _paginas(pdf) <= esperado or alto >= ALTO_MAXIMO_MM * mm:
            break
        alto = min(alto * 1.2 + 8 * mm, ALTO_MAXIMO_MM * mm)
        pdf = _construir(alto)
    else:  # pragma: no cover - solo si nunca converge
        logger.warning('La guía no entró en una página por pedido (alto %.0fmm).', alto / mm)
    return pdf


def _paginas(pdf_bytes):
    """N° de páginas leído del árbol de páginas del PDF (0 si no se puede)."""
    import re
    m = re.search(rb'/Count\s+(\d+)', pdf_bytes)
    return int(m.group(1)) if m else 0
