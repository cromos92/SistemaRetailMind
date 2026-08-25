"""
Comprobante de retiro de pedido ecommerce en PDF térmico 80mm.

Por qué existe: el comprobante se imprime primero por ESC/POS vía QZ Tray
(formato ``RETIRO_ECOMMERCE`` en ``_qz_tray_module.html``), pero QZ hay que
instalarlo y habilitarlo sucursal por sucursal — donde no está (o el día que
se cae) este PDF produce el MISMO papel en cualquier navegador. Mismo enfoque
que ``pdf_guia_preparacion``, de donde se reutilizan estilos y helpers.

IMPORTANTE — CONFIRMAR ≠ IMPRIMIR: cuando este PDF se genera, el acta de
retiro YA quedó registrada en AllConnected. El papel es el respaldo que firma
quien retira, no la fuente de verdad. El código de retiro llega ENMASCARADO
(``****20``) desde AllConnected: acá jamás se ve ni se imprime completo.

Entrada: el ``print_data`` que devuelve el endpoint de confirmación de
AllConnected (numero_pedido, ticket_rm, cliente, retirador_nombre,
retirador_documento, items[{sku, nombre, talla, cantidad}], codigo_enmascarado,
fecha, sucursal, usuario_pos) + ``sucursal_info`` que agrega la vista
(empresa, rut_empresa, alias, direccion de la sucursal en sesión).

Se emiten DOS páginas (una por copia): ORIGINAL — TIENDA (se archiva firmada)
y COPIA — CLIENTE.
"""
from __future__ import annotations

import logging
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

# Se calcan papel, estilos y helpers del PDF de la guía de preparación para que
# ambos papeles salgan de la misma "familia" (y un solo lugar defina el ancho).
from app.services.pdf_guia_preparacion import (
    ANCHO_PAPEL_MM,
    MARGEN_MM,
    ALTO_MAXIMO_MM,
    ALTO_MINIMO_MM,
    _barcode,
    _estilos,
    _linea,
    _paginas,
    _txt,
)

logger = logging.getLogger('app')

_COPIAS = ('ORIGINAL — TIENDA', 'COPIA — CLIENTE')


def _bloque_comprobante(pd, ancho, st, rotulo_copia):
    """Flowables de UNA copia del comprobante."""
    suc_info = pd.get('sucursal_info') or {}
    els = []

    # Cabecera empresa / sucursal
    if suc_info.get('empresa'):
        els.append(Paragraph(_txt(suc_info.get('empresa')).upper(), st['empresa']))
    if suc_info.get('alias') or pd.get('sucursal'):
        els.append(Paragraph(_txt(suc_info.get('alias') or pd.get('sucursal')).upper(),
                             st['sucursal']))
    if suc_info.get('direccion'):
        els.append(Paragraph(_txt(suc_info.get('direccion')), st['sucursal']))
    if suc_info.get('rut_empresa'):
        els.append(Paragraph('RUT: ' + _txt(suc_info.get('rut_empresa')), st['sucursal']))
    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho, grosor=1.1))
    els.append(Spacer(1, 1.5 * mm))

    # Título + rótulo de la copia
    els.append(Paragraph('COMPROBANTE DE RETIRO', st['banner']))
    els.append(Paragraph(_txt(rotulo_copia), st['banner_sub']))
    els.append(Spacer(1, 1.5 * mm))

    # Ticket RM destacado (equivalente al doble tamaño del térmico)
    els.append(Paragraph(_txt(pd.get('ticket_rm')), st['banner']))
    if pd.get('numero_pedido'):
        els.append(Paragraph('Pedido canal: ' + _txt(pd.get('numero_pedido')),
                             st['banner_sub']))
    els.append(Spacer(1, 1 * mm))
    els.append(_linea(ancho))
    els.append(Spacer(1, 1.5 * mm))

    # Datos del retiro
    def _fila(label, valor):
        if valor:
            els.append(Paragraph(f'<b>{label}:</b> {_txt(valor)}', st['valor']))

    _fila('Fecha', pd.get('fecha'))
    _fila('Cliente', pd.get('cliente'))
    _fila('Retira', pd.get('retirador_nombre'))
    _fila('Documento', pd.get('retirador_documento'))
    _fila('Atendió', pd.get('usuario_pos'))
    _fila('Código validado', pd.get('codigo_enmascarado'))
    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho))
    els.append(Spacer(1, 1 * mm))

    # Ítems entregados (nombre / talla / cantidad — sin precios: no es boleta)
    total_unidades = 0
    for item in (pd.get('items') or []):
        if not isinstance(item, dict):
            continue
        cant = item.get('cantidad') or 1
        try:
            total_unidades += int(cant)
        except (TypeError, ValueError):
            total_unidades += 1
        nombre = _txt(item.get('nombre') or item.get('sku') or 'Ítem', 60)
        talla = item.get('talla')
        detalle = f'{cant} x {nombre}'
        if talla:
            detalle += f' — Talla {_txt(talla)}'
        els.append(Paragraph(detalle, st['prod']))
        if item.get('sku'):
            els.append(Paragraph('SKU: ' + _txt(item.get('sku')), st['prod_meta']))
    els.append(Spacer(1, 1 * mm))
    els.append(Paragraph(f'TOTAL: {total_unidades} unidad{"" if total_unidades == 1 else "es"}',
                         st['total_sub']))
    els.append(Spacer(1, 1.5 * mm))
    els.append(_linea(ancho))

    # Barcode del ticket (escaneable en el mesón; sin el prefijo RM-, igual que
    # el resto de los papeles del módulo ecommerce)
    ticket = str(pd.get('ticket_rm') or '').replace('RM-', '')
    codigo_barras = _barcode(ticket, ancho)
    if codigo_barras is not None:
        els.append(Spacer(1, 1.5 * mm))
        els.append(codigo_barras)
        els.append(Paragraph(_txt(ticket), st['desglose']))

    # Firma de quien retira — el papel que reemplaza al que se perdía
    els.append(Spacer(1, 8 * mm))
    els.append(_linea(ancho * 0.75))
    els.append(Paragraph('Firma del retirador', st['firma']))
    els.append(Spacer(1, 1 * mm))
    els.append(Paragraph('El acta digital de este retiro quedó registrada en el sistema.',
                         st['desglose']))
    return els


def generar_comprobante_retiro_pdf(print_data, ancho_mm: int = ANCHO_PAPEL_MM) -> bytes:
    """Arma el PDF con las dos copias del comprobante (una página cada una).

    Lanza solo si ReportLab falla de raíz (el llamador responde 500); los
    campos faltantes se toleran uno por uno.
    """
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
            title='Comprobante de retiro',
            author='RetailMind',
        )
        elements = []
        for idx, rotulo in enumerate(_COPIAS):
            elements.extend(_bloque_comprobante(print_data, ancho_util, st, rotulo))
            if idx < len(_COPIAS) - 1:
                elements.append(PageBreak())
        doc.build(elements)
        return buffer.getvalue()

    # Medición del alto con wrap() + verificación por conteo de páginas: mismo
    # criterio que la guía (quedarse corto parte la copia en dos hojas).
    alto = 0
    for f in _bloque_comprobante(print_data, ancho_util, st, _COPIAS[0]):
        try:
            _, h = f.wrap(ancho_util, ALTO_MAXIMO_MM * mm)
        except Exception:  # pragma: no cover — si algo no mide, se ignora
            h = 0
        alto += h + getattr(f, 'spaceBefore', 0) + getattr(f, 'spaceAfter', 0)
    alto = max(ALTO_MINIMO_MM * mm, min(alto + 2 * margen_v + 6 * mm, ALTO_MAXIMO_MM * mm))

    pdf = _construir(alto)
    for _ in range(4):
        if _paginas(pdf) <= len(_COPIAS) or alto >= ALTO_MAXIMO_MM * mm:
            break
        alto = min(alto * 1.2 + 8 * mm, ALTO_MAXIMO_MM * mm)
        pdf = _construir(alto)
    else:  # pragma: no cover — solo si nunca converge
        logger.warning('El comprobante de retiro no entró en una página por copia '
                       '(alto %.0fmm).', alto / mm)
    return pdf
