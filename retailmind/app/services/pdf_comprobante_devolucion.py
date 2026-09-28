"""
Comprobante de Devolución de Dinero en PDF térmico 80mm (copia para el cliente).

Por qué existe: el módulo imprime el comprobante por QZ Tray (ESC/POS) o por
``window.print()`` (`_dg_print_module.html`), pero al cliente hay que mandárselo
por correo y ahí hace falta un archivo. Este PDF reproduce el MISMO papel, en
el mismo orden y con los mismos datos, a partir del payload que ya consume la
impresión (`views_modulo_devolucion_garantia._payload_comprobante`).

Papel, estilos y helpers se calcan de `pdf_guia_preparacion` (misma familia que
el comprobante de retiro ecommerce), para que un solo lugar defina el ancho.
"""
from __future__ import annotations

import logging
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.pdf_guia_preparacion import (
    ALTO_MAXIMO_MM,
    ALTO_MINIMO_MM,
    ANCHO_PAPEL_MM,
    MARGEN_MM,
    _barcode,
    _clp,
    _estilos,
    _linea,
    _paginas,
    _txt,
)

logger = logging.getLogger('app')


def _estilos_dg():
    st = _estilos()
    st['estado'] = ParagraphStyle(
        'estado', fontName='Helvetica-Bold', fontSize=9.5, alignment=TA_CENTER,
        leading=12, borderWidth=0.9, borderColor=colors.black, borderPadding=2.5,
        spaceBefore=3, spaceAfter=3,
    )
    st['legal'] = ParagraphStyle(
        'legal', fontName='Helvetica', fontSize=7.5, alignment=TA_CENTER, leading=9.5,
    )
    st['legal_b'] = ParagraphStyle(
        'legal_b', fontName='Helvetica-Bold', fontSize=8, alignment=TA_CENTER, leading=10,
    )
    st['celda'] = ParagraphStyle('celda', fontName='Helvetica', fontSize=8.5, leading=10)
    st['celda_sub'] = ParagraphStyle(
        'celda_sub', fontName='Helvetica', fontSize=7, leading=8.5,
        textColor=colors.HexColor('#444444'),
    )
    return st


def _tabla_kv(pares, ancho, st):
    """Etiqueta a la izquierda, valor a la derecha; se saltan los vacíos."""
    filas = [
        [Paragraph(f'<b>{_txt(k)}</b>', st['label']), Paragraph(_txt(v, 60), st['valor'])]
        for k, v in pares if str(v or '').strip()
    ]
    if not filas:
        return None
    t = Table(filas, colWidths=[ancho * 0.34, ancho * 0.66])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0.6), ('BOTTOMPADDING', (0, 0), (-1, -1), 0.6),
    ]))
    return t


def _tabla_productos(productos, ancho, st):
    filas = [[
        Paragraph('<b>CANT</b>', st['celda']),
        Paragraph('<b>DESCRIPCIÓN</b>', st['celda']),
        Paragraph('<b>MONTO</b>', st['celda']),
    ]]
    for p in productos or []:
        es_monto = p.get('modo') == 'MONTO'
        detalle = ' | '.join(x for x in [
            f"SKU:{_txt(p.get('sku'))}" if p.get('sku') else '',
            f"T:{_txt(p.get('talla'))}" if p.get('talla') else '',
        ] if x)
        nota = ('ABONO PARCIAL (sin retiro de producto)' if es_monto
                else (f"P/U: {_clp(p.get('precio_unitario'))}" if p.get('precio_unitario') else ''))
        celda = [Paragraph(_txt(p.get('descripcion'), 80).upper(), st['celda'])]
        if detalle:
            celda.append(Paragraph(detalle, st['celda_sub']))
        if nota:
            celda.append(Paragraph(_txt(nota), st['celda_sub']))
        filas.append([
            Paragraph('—' if es_monto else str(int(p.get('cantidad') or 0)), st['celda']),
            celda,
            Paragraph(_clp(p.get('subtotal')), st['celda']),
        ])
    t = Table(filas, colWidths=[ancho * 0.14, ancho * 0.58, ancho * 0.28], repeatRows=1)
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, colors.black),
        ('LINEBELOW', (0, 1), (-1, -1), 0.3, colors.HexColor('#BBBBBB')),
        ('LEFTPADDING', (0, 0), (-1, -1), 1), ('RIGHTPADDING', (0, 0), (-1, -1), 1),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
    ]))
    return t


def _bloque_comprobante(td, ancho, st):
    """Flowables del comprobante: mismo contenido y orden que el ESC/POS."""
    suc = td.get('sucursal') or {}
    cli = td.get('cliente') or {}
    dte = td.get('dte') or {}
    tr = td.get('transferencia')
    mp = td.get('mercadopago')
    els = []

    if suc.get('empresa'):
        els.append(Paragraph(_txt(suc['empresa']).upper(), st['empresa']))
    if suc.get('alias'):
        els.append(Paragraph(f"<b>{_txt(suc['alias']).upper()}</b>", st['sucursal']))
    for linea in (suc.get('direccion'),
                  f"RUT: {suc['rut_empresa']}" if suc.get('rut_empresa') else '',
                  f"Tel: {suc['telefono']}" if suc.get('telefono') else ''):
        if linea:
            els.append(Paragraph(_txt(linea, 60), st['sucursal']))
    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1.5 * mm))

    els.append(Paragraph('DEVOLUCIÓN<br/>DE DINERO', st['banner']))
    els.append(Spacer(1, 1.5 * mm))
    bc = _barcode(td.get('numero_operacion'), ancho)
    if bc is not None:
        els.append(bc)
    els.append(Paragraph(_txt(td.get('numero_operacion')), st['ticket']))
    els.append(Paragraph(_txt(td.get('estado_display') or td.get('estado')), st['estado']))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1.5 * mm))

    kv = _tabla_kv([
        ('Fecha:', f"{td.get('fecha') or ''} {td.get('hora') or ''}".strip()),
        ('Documento:', f"{dte.get('tipo') or ''} #{dte['folio']}" if dte.get('folio') else ''),
        ('Fec. doc.:', dte.get('fecha')),
        ('Solicita:', td.get('solicitante')),
        ('Cliente:', cli.get('nombre')),
        ('RUT:', cli.get('rut')),
        ('Correo:', cli.get('email')),
        ('Devolución:', td.get('metodo_solicitado_display')),
    ], ancho, st)
    if kv is not None:
        els.append(kv)

    if mp:
        els.append(Spacer(1, 1 * mm))
        els.append(_linea(ancho, 0.6))
        els.append(Paragraph('MERCADO PAGO', st['seccion']))
        kv_mp = _tabla_kv([
            ('N° operación:' if mp.get('devuelto') else 'N° cobro:', mp.get('numero_operacion')),
            ('Medio:', mp.get('medio')),
            ('Refund API:', ', '.join(mp.get('refunds') or [])),
        ], ancho, st)
        if kv_mp is not None:
            els.append(kv_mp)
        els.append(Paragraph(
            _txt('Devuelto a la tarjeta / cuenta Mercado Pago del cliente' if mp.get('devuelto')
                 else 'Se devolverá por Mercado Pago al aprobarse'),
            st['celda_sub']))

    if tr:
        els.append(Spacer(1, 1 * mm))
        els.append(_linea(ancho, 0.6))
        els.append(Paragraph('DATOS DE TRANSFERENCIA', st['seccion']))
        kv_tr = _tabla_kv([
            ('Banco:', tr.get('banco')), ('Tipo:', tr.get('tipo_cuenta')),
            ('Cuenta:', tr.get('numero_cuenta')), ('Titular:', tr.get('titular_rut')),
        ], ancho, st)
        if kv_tr is not None:
            els.append(kv_tr)

    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1 * mm))
    els.append(_tabla_productos(td.get('productos') or [], ancho, st))

    if td.get('motivo'):
        els.append(Spacer(1, 1 * mm))
        els.append(_linea(ancho, 0.6))
        els.append(Paragraph('MOTIVO:', st['seccion']))
        els.append(Paragraph(_txt(td['motivo'], 400), st['celda']))

    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1 * mm))
    els.append(Paragraph(f"TOTAL: {_clp(td.get('total'))}", st['total']))
    els.append(Spacer(1, 1 * mm))
    els.append(_linea(ancho, 1.2))
    els.append(Spacer(1, 1.5 * mm))

    if td.get('nota_credito'):
        els.append(Paragraph(f"NOTA DE CRÉDITO N° {_txt(td['nota_credito'])}", st['legal_b']))
    else:
        els.append(Paragraph('COMPROBANTE NO TRIBUTARIO', st['legal_b']))
        els.append(Paragraph('No válido como nota de crédito', st['legal']))
        estado = td.get('estado')
        if estado == 'RECHAZADA':
            els.append(Paragraph('SOLICITUD RECHAZADA — SIN VALOR', st['legal_b']))
        elif estado == 'ANULADA':
            els.append(Paragraph('SOLICITUD ANULADA — SIN VALOR', st['legal_b']))
        else:
            els.append(Paragraph('Sujeto a aprobación', st['legal']))
    els.append(Spacer(1, 2.5 * mm))
    els.append(Paragraph('Conserve este comprobante', st['legal']))
    return els


def nombre_archivo_comprobante(td) -> str:
    base = ''.join(ch if ch.isalnum() or ch in '-_' else '_'
                   for ch in str(td.get('numero_operacion') or 'devolucion'))
    return f'Comprobante_{base}.pdf'


def generar_comprobante_devolucion_pdf(td, ancho_mm: int = ANCHO_PAPEL_MM) -> bytes:
    """Arma el PDF (una página, alto medido del contenido).

    Lanza solo si ReportLab falla de raíz; los campos faltantes se toleran
    uno por uno.
    """
    margen = MARGEN_MM * mm
    margen_v = 3 * mm
    page_w = ancho_mm * mm
    ancho_util = page_w - 2 * margen
    st = _estilos_dg()

    def _construir(alto_pagina):
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=(page_w, alto_pagina),
            leftMargin=margen, rightMargin=margen,
            topMargin=margen_v, bottomMargin=margen_v,
            title=f"Comprobante {td.get('numero_operacion') or ''}".strip(),
            author='RetailMind',
        )
        doc.build(_bloque_comprobante(td, ancho_util, st))
        return buffer.getvalue()

    # Medición del alto con wrap() + verificación por conteo de páginas (mismo
    # criterio que la guía y el comprobante de retiro).
    alto = 0
    for f in _bloque_comprobante(td, ancho_util, st):
        try:
            _, h = f.wrap(ancho_util, ALTO_MAXIMO_MM * mm)
        except Exception:  # pragma: no cover — si algo no mide, se ignora
            h = 0
        alto += h + getattr(f, 'spaceBefore', 0) + getattr(f, 'spaceAfter', 0)
    alto = max(ALTO_MINIMO_MM * mm, min(alto + 2 * margen_v + 8 * mm, ALTO_MAXIMO_MM * mm))

    pdf = _construir(alto)
    for _ in range(4):
        if _paginas(pdf) <= 1 or alto >= ALTO_MAXIMO_MM * mm:
            break
        alto = min(alto * 1.2 + 8 * mm, ALTO_MAXIMO_MM * mm)
        pdf = _construir(alto)
    else:  # pragma: no cover — solo si nunca converge
        logger.warning('El comprobante de devolución no entró en una página (alto %.0fmm).',
                       alto / mm)
    return pdf
