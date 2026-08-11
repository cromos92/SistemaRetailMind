"""
Guía de preparación (picking) en PDF térmico 80mm.

Por qué existe: la guía se imprimía SOLO por ESC/POS vía QZ Tray, que hay que
instalar y habilitar sucursal por sucursal — donde no está configurado, la
tienda simplemente no podía imprimir. Este generador produce el mismo papel en
PDF, que abre en cualquier navegador (mismo enfoque que usa AllConnected en
`system/shipping/services/pdf_generator.py`, para que el formato le resulte
familiar al personal de tienda).

Es un documento de PICKING: casilla por línea, SKU/artículo destacados, marca,
talla, color, stock del sistema y el total de UNIDADES a sacar. Va rotulado "NO
VÁLIDO COMO BOLETA" para que no se confunda con el documento tributario.

Sobre los montos: el papel muestra el precio de cada línea y el TOTAL DEL PEDIDO
tal como lo ve el listado (`PedidoEcommerce.total`), NO la suma de las líneas.
En prod casi ningún pedido cuadra —despacho, descuentos de nivel pedido y
canales que mandan la línea incompleta— y ver dos cifras distintas para lo mismo
confundía a la tienda; por eso cuando difieren se imprime el desglose.

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
# PDF de AllConnected). El alto se mide del contenido y se verifica contando las
# páginas del PDF generado (ver `generar_guia_preparacion_pdf`): quedarse corto
# parte cada guía en dos hojas y pasarse hace avanzar papel de más.
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
        # SKU + artículo: el dato más grande de la línea, arriba de todo.
        'ident': ParagraphStyle('ident', fontName='Helvetica-Bold', fontSize=12.5, leading=14),
        'prod': ParagraphStyle('prod', fontName='Helvetica', fontSize=9.5, leading=11),
        # Talla y color van más grandes que el resto de la metadata: son el dato
        # que evita sacar el producto equivocado del estante.
        'variante': ParagraphStyle('variante', fontName='Helvetica', fontSize=9.5, leading=11.5,
                                   spaceBefore=0.5),
        'prod_meta': ParagraphStyle('prod_meta', fontName='Helvetica', fontSize=7.5, leading=9,
                                    textColor=colors.HexColor('#444444')),
        'precio': ParagraphStyle('precio', fontName='Helvetica', fontSize=8.5, leading=10.5,
                                 spaceBefore=0.5),
        'cant': ParagraphStyle('cant', fontName='Helvetica-Bold', fontSize=13,
                               alignment=TA_CENTER, leading=15),
        'casilla': ParagraphStyle('casilla', fontName='Helvetica', fontSize=15,
                                  alignment=TA_CENTER, leading=16),
        'total': ParagraphStyle('total', fontName='Helvetica-Bold', fontSize=11,
                                alignment=TA_CENTER, leading=13),
        'total_sub': ParagraphStyle('total_sub', fontName='Helvetica', fontSize=9,
                                    alignment=TA_CENTER, leading=11),
        'desglose': ParagraphStyle('desglose', fontName='Helvetica', fontSize=7.5,
                                   alignment=TA_CENTER, leading=9,
                                   textColor=colors.HexColor('#444444')),
        'pie': ParagraphStyle('pie', fontName='Helvetica-Bold', fontSize=8.5,
                              alignment=TA_CENTER, leading=11),
        'firma': ParagraphStyle('firma', fontName='Helvetica', fontSize=8,
                                alignment=TA_LEFT, leading=14),
        'alerta': ParagraphStyle('alerta', fontName='Helvetica-Bold', fontSize=8,
                                 alignment=TA_LEFT, leading=10,
                                 textColor=colors.HexColor('#B91C1C')),
    }


def _clp(valor):
    """Monto en pesos con separador de miles (1234567 → $1.234.567)."""
    try:
        return '$' + f'{int(valor):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return '$0'


def _txt(valor, limite=None):
    """Texto seguro para Paragraph (escapa &, <, > y recorta)."""
    s = str(valor or '').strip()
    if limite and len(s) > limite:
        s = s[:limite - 1] + '…'
    return _xml_escape(s)


def _casilla(lado=4.2 * mm):
    """Casilla VACÍA para ticar a mano, dibujada como recuadro.

    No se usa el carácter '☐': Helvetica no lo tiene y ReportLab lo sustituye
    por ZapfDingbats 'n', que es un cuadrado NEGRO RELLENO — o sea el papel sale
    con todas las líneas como si ya estuvieran marcadas. Dibujarla como tabla
    con borde es independiente de la fuente y de la impresora.
    """
    c = Table([['']], colWidths=[lado], rowHeights=[lado])
    c.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.9, colors.black),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return c


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
    suma_items = 0
    for i, it in enumerate(items):
        cant = int(it.get('cantidad') or 1)
        total_unidades += cant
        precio = int(it.get('precio') or 0)
        suma_items += precio * cant

        celda = []

        # Línea 1: SKU y ARTÍCULO, lo más grande y arriba de todo. Son los
        # códigos con los que se busca en el sistema y los que vienen impresos
        # en la etiqueta de la caja, así que la vendedora arranca por ahí.
        ident = []
        if it.get('sku'):
            ident.append(f"SKU {_txt(it['sku'], 18)}")
        if it.get('articulo'):
            ident.append(_txt(it['articulo'], 20))
        if ident:
            celda.append(Paragraph(' · '.join(ident), st['ident']))

        # Línea 2: MARCA + nombre del artículo.
        titulo = _txt(it.get('nombre') or 'Ítem', 52)
        marca = _txt(it.get('marca'), 22)
        celda.append(Paragraph(f'<b>{marca}</b> {titulo}'.strip() if marca else f'<b>{titulo}</b>',
                               st['prod']))

        # Línea 3: TALLA y COLOR destacados — definen CUÁL de todas sacar.
        variante = []
        if it.get('talla'):
            variante.append(f"TALLA <b>{_txt(it['talla'], 12)}</b>")
        if it.get('color'):
            variante.append(f"COLOR <b>{_txt(it['color'], 18)}</b>")
        if variante:
            celda.append(Paragraph(' · '.join(variante), st['variante']))

        # Línea 4: stock del sistema, en chico (solo se lee si hay dudas).
        if it.get('stock_disponible') is not None:
            celda.append(Paragraph(f"stock sistema: {int(it['stock_disponible'])}",
                                   st['prod_meta']))

        # Línea 5: precio unitario y descuento, si lo hubo.
        if precio:
            precio_txt = f"<b>{_clp(precio)}</b> c/u"
            if cant > 1:
                precio_txt += f" · total {_clp(precio * cant)}"
            if it.get('precio_lista'):
                pct = it.get('pct_descuento')
                pct_txt = f" (-{pct:.0f}%)" if pct else ''
                precio_txt += (f'  <font color="#B91C1C">antes '
                               f'{_clp(it["precio_lista"])}{pct_txt}</font>')
            celda.append(Paragraph(precio_txt, st['precio']))

        if not it.get('encontrado', True):
            celda.append(Paragraph('⚠ SIN STOCK SUFICIENTE EN EL SISTEMA', st['alerta']))

        fila = Table(
            [[_casilla(), Paragraph(f'{cant}', st['cant']), celda]],
            colWidths=[ancho * 0.10, ancho * 0.12, ancho * 0.78],
        )
        fila.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 1),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        ]))
        els.append(fila)
        if i < len(items) - 1:
            els.append(Spacer(1, 1 * mm))
            els.append(_linea(ancho, 0.4, '#888888'))
            els.append(Spacer(1, 1 * mm))

    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1.5 * mm))

    # ── Totales: unidades a contar y monto del pedido ───────────
    # El monto que manda es el TOTAL DEL PEDIDO (el mismo del listado y el que
    # pagó el cliente), NO la suma de las líneas: difieren casi siempre por
    # despacho, descuentos de nivel pedido o líneas incompletas del canal.
    # Cuando difieren se imprime el desglose, así el número cuadra a la vista.
    total_pedido = int(ctx.get('total_pedido') or 0) or suma_items
    filas_total = [[Paragraph(
        f"TOTAL A SACAR: {total_unidades} "
        f"{'UNIDAD' if total_unidades == 1 else 'UNIDADES'}", st['total'])]]
    if total_pedido:
        filas_total.append([Paragraph(
            f"Total del pedido: {_clp(total_pedido)}", st['total_sub'])])
    caja = Table(filas_total, colWidths=[ancho])
    caja.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1.2, colors.black),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    els.append(caja)

    envio = int(ctx.get('costo_envio') or 0)
    descuento_pedido = int(ctx.get('descuento_pedido') or 0)
    if total_pedido and total_pedido != suma_items:
        partes = [f"ítems {_clp(suma_items)}"]
        if envio:
            partes.append(f"despacho {_clp(envio)}")
        if descuento_pedido:
            partes.append(f"desc. -{_clp(descuento_pedido)}")
        # Lo que no explican envío ni descuento: típicamente el canal mandó la
        # línea incompleta. Se muestra como "otros" en vez de dejar una resta
        # que no cierra.
        otros = total_pedido - (suma_items + envio - descuento_pedido)
        if otros:
            partes.append(f"otros {_clp(otros)}")
        els.append(Spacer(1, 1 * mm))
        els.append(Paragraph(' + '.join(partes), st['desglose']))

    # ── Pie ─────────────────────────────────────────────────────
    els.append(Spacer(1, 2.5 * mm))
    els.append(Paragraph('*** NO VÁLIDO COMO BOLETA ***', st['pie']))
    els.append(Spacer(1, 2 * mm))
    els.append(Paragraph('Preparado por: ______________________', st['firma']))
    # Al emitir la boleta la tienda anota su fecha a mano sobre este papel; la
    # dirección de la sucursal va impresa para no copiarla cada vez.
    els.append(Paragraph('Fecha Boleta: _______________________', st['firma']))
    if suc.get('direccion'):
        els.append(Paragraph(f"Dirección: {_txt(suc['direccion'], 60)}", st['firma']))
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
