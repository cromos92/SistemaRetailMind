"""
Formato propio de RetailMind para reclamar a un proveedor (A4, PDF).

Por qué existe: cada proveedor tiene su propia planilla de garantías (o
ninguna), y perseguir el formato de cada uno es inmantenible. En vez de eso se
manda SIEMPRE el mismo documento formal, autocontenido, que el proveedor puede
archivar, imprimir y responder. El correo HTML sigue yendo, pero es el sobre;
esto es el documento.

Tres decisiones de diseño:

1. **Las fotos van DENTRO del PDF**, reescaladas. Antes se adjuntaban 8 fotos
   de celular sueltas (30-40 MB) y el SMTP rechazaba el envío completo. Acá el
   proveedor ve la evidencia junto al dato, en una sola pieza de ~1 MB.
2. **Última página: "RESPUESTA DEL PROVEEDOR"** con casillas y firma. Es la
   razón de ser del formato propio: hoy la respuesta llega como texto libre en
   un correo y hay que interpretarla. Con esto vuelve estructurada.
3. **Si falta un dato que el proveedor va a exigir** (factura de compra,
   evidencia fotográfica) el documento lo dice en rojo en vez de omitirlo en
   silencio: es mejor que el analista lo vea antes de enviar.
"""
from __future__ import annotations

import logging
from io import BytesIO
from xml.sax.saxutils import escape as _xml_escape

from django.utils import timezone

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

logger = logging.getLogger('app')

# Paleta NEXO (nexo-design-system.css) para que el papel se parezca al sistema.
AZUL = colors.HexColor('#405189')
AZUL_SUAVE = colors.HexColor('#EEF1F8')
GRIS_LINEA = colors.HexColor('#D9DCE5')
GRIS_TEXTO = colors.HexColor('#5C6479')
ROJO = colors.HexColor('#B91C1C')

MARGEN = 14 * mm
# Lado mayor al que se reescala cada foto antes de incrustarla. 1000px es
# suficiente para ver un despegue de suela y deja el PDF en ~1MB con 4 fotos.
FOTO_LADO_MAX = 1000
FOTO_CALIDAD = 72


def _txt(valor, limite=None):
    """Texto seguro para Paragraph (escapa &, <, > y recorta)."""
    s = str(valor if valor is not None else '').strip()
    if limite and len(s) > limite:
        s = s[:limite - 1] + '…'
    return _xml_escape(s)


def _fecha(valor, con_hora=False):
    if not valor:
        return ''
    try:
        return valor.strftime('%d/%m/%Y %H:%M' if con_hora else '%d/%m/%Y')
    except AttributeError:
        return str(valor)


def _estilos():
    return {
        'titulo': ParagraphStyle('titulo', fontName='Helvetica-Bold', fontSize=16,
                                 leading=18, textColor=AZUL),
        'subtitulo': ParagraphStyle('subtitulo', fontName='Helvetica', fontSize=8.5,
                                    leading=10.5, textColor=GRIS_TEXTO),
        'emisor': ParagraphStyle('emisor', fontName='Helvetica-Bold', fontSize=10.5,
                                 leading=12.5),
        'emisor_det': ParagraphStyle('emisor_det', fontName='Helvetica', fontSize=8,
                                     leading=10, textColor=GRIS_TEXTO),
        'folio_lbl': ParagraphStyle('folio_lbl', fontName='Helvetica', fontSize=7.5,
                                    leading=9, alignment=TA_CENTER, textColor=GRIS_TEXTO),
        'folio': ParagraphStyle('folio', fontName='Helvetica-Bold', fontSize=13,
                                leading=15, alignment=TA_CENTER, textColor=AZUL),
        'folio_sub': ParagraphStyle('folio_sub', fontName='Helvetica', fontSize=8,
                                    leading=10, alignment=TA_CENTER),
        'seccion': ParagraphStyle('seccion', fontName='Helvetica-Bold', fontSize=9.5,
                                  leading=11, textColor=colors.white),
        'label': ParagraphStyle('label', fontName='Helvetica', fontSize=8,
                                leading=10, textColor=GRIS_TEXTO),
        'valor': ParagraphStyle('valor', fontName='Helvetica-Bold', fontSize=9,
                                leading=11),
        'valor_grande': ParagraphStyle('valor_grande', fontName='Helvetica-Bold',
                                       fontSize=12, leading=14),
        'texto': ParagraphStyle('texto', fontName='Helvetica', fontSize=9, leading=12),
        'falta': ParagraphStyle('falta', fontName='Helvetica-Bold', fontSize=8.5,
                                leading=10.5, textColor=ROJO),
        'foto_cap': ParagraphStyle('foto_cap', fontName='Helvetica-Bold', fontSize=8,
                                   leading=10, alignment=TA_CENTER),
        'foto_guia': ParagraphStyle('foto_guia', fontName='Helvetica', fontSize=7,
                                    leading=8.5, alignment=TA_CENTER, textColor=GRIS_TEXTO),
        'casilla': ParagraphStyle('casilla', fontName='Helvetica', fontSize=11, leading=15),
        'firma': ParagraphStyle('firma', fontName='Helvetica', fontSize=8,
                                leading=22, textColor=GRIS_TEXTO),
        'pie': ParagraphStyle('pie', fontName='Helvetica', fontSize=7.5,
                              leading=9, textColor=GRIS_TEXTO, alignment=TA_RIGHT),
    }


def _banda(titulo, ancho, st):
    """Encabezado de sección: banda azul de ancho completo."""
    t = Table([[Paragraph(titulo, st['seccion'])]], colWidths=[ancho])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), AZUL),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
    ]))
    return t


def _grilla(pares, ancho, st, columnas=3):
    """Grilla de label/valor en N columnas. `pares`: [(label, valor), ...].

    Cada celda es una lista de flowables (etiqueta arriba, valor abajo): es lo
    que espera ReportLab y evita anidar tablas para algo que es una pila.
    Devuelve None si no quedó ningún par con valor.
    """
    pares = [(l, v) for l, v in pares if str(v or '').strip()]
    if not pares:
        return None

    celdas = [
        [Paragraph(_txt(l), st['label']), Paragraph(_txt(v, 120), st['valor'])]
        for l, v in pares
    ]
    filas = []
    for i in range(0, len(celdas), columnas):
        grupo = celdas[i:i + columnas]
        grupo += [''] * (columnas - len(grupo))
        filas.append(grupo)

    estilo = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if len(filas) > 1:
        estilo.append(('LINEBELOW', (0, 0), (-1, -2), 0.4, GRIS_LINEA))

    t = Table(filas, colWidths=[ancho / columnas] * columnas)
    t.setStyle(TableStyle(estilo))
    return t


def _foto_flowable(foto, ancho_celda):
    """Imagen reescalada lista para incrustar, o None si no se puede leer.

    Se usa Pillow (ya es dependencia por ImageField) para: corregir la
    orientación EXIF —las fotos de celular llegan acostadas—, convertir a RGB
    (un PNG con alpha revienta al guardar como JPEG) y bajar el peso.
    """
    try:
        from PIL import Image as PILImage, ImageOps
    except ImportError:  # pragma: no cover - Pillow es dependencia del proyecto
        logger.warning('Pillow no disponible: el PDF del requerimiento irá sin fotos')
        return None

    try:
        # `foto.imagen.storage`, no `default_storage`: la evidencia puede vivir
        # en Spaces mientras el default sigue siendo el disco local.
        with foto.imagen.storage.open(foto.imagen.name, 'rb') as fh:
            img = PILImage.open(fh)
            img = ImageOps.exif_transpose(img)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            img.thumbnail((FOTO_LADO_MAX, FOTO_LADO_MAX), PILImage.LANCZOS)
            buf = BytesIO()
            img.save(buf, format='JPEG', quality=FOTO_CALIDAD, optimize=True)
            ancho_px, alto_px = img.size
    except Exception as e:
        logger.warning('No se pudo incrustar la foto %s del requerimiento: %s',
                       getattr(foto, 'id', '?'), e)
        return None

    buf.seek(0)
    alto = ancho_celda * (alto_px / ancho_px) if ancho_px else ancho_celda
    # Techo de alto: una foto vertical de celular ocuparía media página y
    # empujaría el resto de la evidencia a páginas sueltas.
    alto_max = 62 * mm
    if alto > alto_max:
        ancho_celda = ancho_celda * (alto_max / alto)
        alto = alto_max
    return Image(buf, width=ancho_celda, height=alto)


def _casilla(texto, st, ancho_texto):
    """Casilla de verificación + etiqueta.

    El cuadro se DIBUJA con una celda con borde: el carácter '☐' (U+2610) no
    existe en Helvetica/WinAnsi y ReportLab lo sustituye por una 'n', que en el
    papel se lee como basura justo en la parte que el proveedor debe marcar.
    """
    cuadro = Table([['']], colWidths=[3.6 * mm], rowHeights=[3.6 * mm])
    cuadro.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.8, colors.black),
        ('BACKGROUND', (0, 0), (-1, -1), colors.white),
    ]))
    fila = Table([[cuadro, Paragraph(texto, st['casilla'])]],
                 colWidths=[6 * mm, ancho_texto])
    fila.setStyle(TableStyle([
        ('VALIGN', (0, 0), (0, 0), 'MIDDLE'), ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    return fila


def _bloque_respuesta(ancho, st, plazo_dias):
    """Última sección: la que el proveedor completa y devuelve firmada."""
    els = [_banda('RESPUESTA DEL PROVEEDOR — a completar por el proveedor', ancho, st),
           Spacer(1, 3 * mm)]

    col_op = ancho / 3
    opciones = Table(
        [[_casilla('<b>APRUEBA</b><br/>el requerimiento', st, col_op - 14 * mm),
          _casilla('<b>RECHAZA</b><br/>el requerimiento', st, col_op - 14 * mm),
          _casilla('<b>REQUIERE MÁS</b><br/>INFORMACIÓN', st, col_op - 14 * mm)]],
        colWidths=[col_op] * 3,
    )
    opciones.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.6, GRIS_LINEA),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, GRIS_LINEA),
        ('BACKGROUND', (0, 0), (-1, -1), AZUL_SUAVE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    els.append(opciones)
    els.append(Spacer(1, 5 * mm))

    els.append(Paragraph('Resolución ofrecida', st['label']))
    els.append(Spacer(1, 1.5 * mm))
    col_res = ancho / 4
    resoluciones = Table(
        [[_casilla('Reposición del producto', st, col_res - 8 * mm),
          _casilla('Nota de crédito', st, col_res - 8 * mm),
          _casilla('Reparación', st, col_res - 8 * mm),
          _casilla('Otra', st, col_res - 8 * mm)]],
        colWidths=[col_res] * 4,
    )
    resoluciones.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    els.append(resoluciones)
    els.append(Spacer(1, 4 * mm))

    els.append(Paragraph(
        'N° de autorización / nota de crédito: _______________________________',
        st['casilla']))
    els.append(Spacer(1, 4 * mm))

    els.append(Paragraph('Observaciones del proveedor', st['label']))
    obs = Table([['']], colWidths=[ancho], rowHeights=[22 * mm])
    obs.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, GRIS_LINEA)]))
    els.append(obs)
    els.append(Spacer(1, 6 * mm))

    firmas = Table(
        [[Paragraph('Nombre y cargo<br/>_______________________________', st['firma']),
          Paragraph('Firma<br/>_______________________________', st['firma']),
          Paragraph('Fecha<br/>_______________________________', st['firma'])]],
        colWidths=[ancho / 3] * 3,
    )
    firmas.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))
    els.append(firmas)

    if plazo_dias:
        els.append(Spacer(1, 3 * mm))
        els.append(Paragraph(
            f'Agradecemos responder dentro de {plazo_dias} días hábiles. '
            'Puede responder este correo adjuntando este documento completado.',
            st['texto']))
    return els


def _contexto_producto(requerimiento):
    """Marca / color / talla desde la ficha, cuando el SKU existe en el sistema."""
    pt = requerimiento.producto_talla
    if not pt:
        return {}
    datos = {'talla': pt.talla}
    producto = getattr(pt, 'producto', None)
    if producto:
        datos['articulo'] = producto.articulo
        datos['descripcion'] = producto.descripcion
        # atributo1 = marca, atributo2 = color (ver models/catalogo.py)
        if producto.atributo1_id:
            datos['marca'] = producto.atributo1.valor
        if producto.atributo2_id:
            datos['color'] = producto.atributo2.valor
    return datos


def generar_pdf_requerimiento(requerimiento, *, usuario=None, plazo_dias=7) -> bytes:
    """
    Arma el formato RetailMind del requerimiento y devuelve los bytes del PDF.

    Tolera datos faltantes campo por campo: un requerimiento sin cliente, sin
    factura de compra o sin fotos igual genera documento (y lo hace notar).
    """
    st = _estilos()
    buffer = BytesIO()
    ancho = A4[0] - 2 * MARGEN

    empresa = requerimiento.sucursal.empresa if requerimiento.sucursal_id else None
    proveedor = requerimiento.proveedor
    prod = _contexto_producto(requerimiento)

    fotos = list(requerimiento.fotos.select_related('tipo_foto').all())
    fotos_ok, fotos_perdidas = [], 0
    for foto in fotos:
        flow = _foto_flowable(foto, (ancho - 6 * mm) / 2) if foto.imagen else None
        if flow is None:
            fotos_perdidas += 1
        else:
            fotos_ok.append((foto, flow))

    def _pie(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(GRIS_LINEA)
        canvas.line(MARGEN, 12 * mm, A4[0] - MARGEN, 12 * mm)
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(GRIS_TEXTO)
        canvas.drawString(
            MARGEN, 8 * mm,
            f'{requerimiento.numero_requerimiento} · '
            f'{empresa.nombre if empresa else "RetailMind"} · '
            f'generado el {_fecha(timezone.localtime(), con_hora=True)}')
        canvas.drawRightString(A4[0] - MARGEN, 8 * mm, f'Página {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGEN, rightMargin=MARGEN,
        topMargin=MARGEN, bottomMargin=18 * mm,
        title=f'Requerimiento {requerimiento.numero_requerimiento}',
        author='RetailMind',
    )

    els = []

    # ── Cabecera: emisor a la izquierda, folio a la derecha ──────────────
    izq = [Paragraph(_txt(empresa.nombre if empresa else 'RetailMind'), st['emisor'])]
    if empresa:
        detalle_emisor = ' · '.join(filter(None, [
            f'RUT {empresa.rut}' if empresa.rut else '',
            empresa.direccion, empresa.comuna,
        ]))
        if detalle_emisor:
            izq.append(Paragraph(_txt(detalle_emisor, 90), st['emisor_det']))
    if requerimiento.sucursal_id:
        izq.append(Paragraph(
            _txt(f'Sucursal {requerimiento.sucursal.alias} · '
                 f'{requerimiento.sucursal.direccion}', 90), st['emisor_det']))

    caja_folio = Table(
        [[Paragraph('N° DE REQUERIMIENTO', st['folio_lbl'])],
         [Paragraph(_txt(requerimiento.numero_requerimiento), st['folio'])],
         [Paragraph(_fecha(timezone.localtime(requerimiento.fecha_creacion)), st['folio_sub'])]],
        colWidths=[52 * mm],
    )
    caja_folio.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, AZUL),
        ('BACKGROUND', (0, 0), (-1, -1), AZUL_SUAVE),
        ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))

    cabecera = Table([[izq, caja_folio]], colWidths=[ancho - 52 * mm, 52 * mm])
    cabecera.setStyle(TableStyle([
        ('VALIGN', (0, 0), (0, 0), 'TOP'), ('VALIGN', (1, 0), (1, 0), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    els.append(cabecera)
    els.append(Spacer(1, 5 * mm))

    els.append(Paragraph(
        f'SOLICITUD DE {_txt(requerimiento.get_tipo_display()).upper()}', st['titulo']))
    els.append(Paragraph(
        'Documento formal de reclamo a proveedor · '
        f'Prioridad {_txt(requerimiento.get_prioridad_display())} · '
        f'Estado {_txt(requerimiento.get_estado_display())}', st['subtitulo']))
    els.append(Spacer(1, 5 * mm))

    # ── Proveedor ────────────────────────────────────────────────────────
    els.append(_banda('PROVEEDOR', ancho, st))
    grilla_prov = _grilla([
        ('Razón social', proveedor.nombre if proveedor else 'SIN PROVEEDOR ASIGNADO'),
        ('RUT', proveedor.rut if proveedor else ''),
        ('Contacto', requerimiento.correo_proveedor_destino or ''),
    ], ancho, st, columnas=3)
    if grilla_prov:
        els.append(grilla_prov)
    els.append(Spacer(1, 4 * mm))

    # ── Producto ─────────────────────────────────────────────────────────
    els.append(_banda('PRODUCTO RECLAMADO', ancho, st))
    fila_producto = Table(
        [[Paragraph('SKU', st['label']), Paragraph('Producto', st['label']),
          Paragraph('Cantidad', st['label'])],
         [Paragraph(_txt(requerimiento.sku), st['valor_grande']),
          Paragraph(_txt(requerimiento.nombre_producto, 70), st['valor']),
          Paragraph(str(requerimiento.cantidad or 1), st['valor_grande'])]],
        colWidths=[ancho * 0.22, ancho * 0.62, ancho * 0.16],
    )
    fila_producto.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, 0), 0.4, GRIS_LINEA),
    ]))
    els.append(fila_producto)
    detalle_prod = _grilla([
        ('Marca', prod.get('marca')),
        ('Talla', prod.get('talla')),
        ('Color', prod.get('color')),
        ('Descripción ficha', prod.get('descripcion')),
    ], ancho, st, columnas=4)
    if detalle_prod:
        els.append(detalle_prod)
    els.append(Spacer(1, 4 * mm))

    # ── Respaldo de compra ───────────────────────────────────────────────
    els.append(_banda('RESPALDO', ancho, st))
    factura = requerimiento.numero_factura_compra or (
        str(requerimiento.dte_compra.numero_documento) if requerimiento.dte_compra_id else '')
    fecha_factura = requerimiento.fecha_factura_compra or (
        requerimiento.dte_compra.fecha_emision if requerimiento.dte_compra_id else None)
    respaldo = [
        ('Factura de compra N°', factura),
        ('Fecha de factura', _fecha(fecha_factura)),
    ]
    if requerimiento.origen == 'CLIENTE':
        respaldo += [
            ('Documento de venta', ' '.join(filter(None, [
                requerimiento.tipo_documento or '',
                f'N° {requerimiento.numero_boleta}' if requerimiento.numero_boleta else '',
            ]))),
            ('Fecha de venta', _fecha(requerimiento.fecha_compra)),
            ('Cliente', requerimiento.cliente_nombre),
            ('RUT cliente', requerimiento.cliente_rut),
        ]
    grilla_resp = _grilla(respaldo, ancho, st, columnas=3)
    if grilla_resp:
        els.append(grilla_resp)
    if not factura:
        els.append(Spacer(1, 1.5 * mm))
        els.append(Paragraph(
            '⚠ Sin factura de compra registrada. Si el proveedor la exige, '
            'debe completarse antes de tramitar la garantía.', st['falta']))
    if requerimiento.origen == 'STOCK':
        els.append(Spacer(1, 1.5 * mm))
        els.append(Paragraph(
            'Producto detectado en stock de la tienda: no hubo venta a cliente final.',
            st['texto']))
    els.append(Spacer(1, 4 * mm))

    # ── Problema ─────────────────────────────────────────────────────────
    els.append(_banda('DESCRIPCIÓN DEL PROBLEMA', ancho, st))
    grilla_prob = _grilla([
        ('Tipo', requerimiento.get_tipo_display()),
        ('Subtipo', requerimiento.subtipo_display),
        ('Severidad', requerimiento.get_severidad_defecto_display()
         if requerimiento.severidad_defecto else ''),
        ('Condición del producto', requerimiento.get_condicion_producto_display()
         if requerimiento.condicion_producto else ''),
        ('Producto esperado', requerimiento.producto_esperado),
        ('Detectado en', requerimiento.sucursal.alias if requerimiento.sucursal_id else ''),
    ], ancho, st, columnas=3)
    if grilla_prob:
        els.append(grilla_prob)
    els.append(Spacer(1, 2 * mm))
    els.append(Paragraph('<b>Motivo:</b> ' + _txt(requerimiento.motivo), st['texto']))
    if requerimiento.descripcion_problema:
        els.append(Spacer(1, 1.5 * mm))
        els.append(Paragraph('<b>Detalle:</b> ' + _txt(requerimiento.descripcion_problema),
                             st['texto']))
    els.append(Spacer(1, 4 * mm))

    # ── Evidencia ────────────────────────────────────────────────────────
    els.append(_banda(f'EVIDENCIA FOTOGRÁFICA ({len(fotos_ok)})', ancho, st))
    els.append(Spacer(1, 2.5 * mm))
    if fotos_ok:
        pareadas = [fotos_ok[i:i + 2] for i in range(0, len(fotos_ok), 2)]
        for par in pareadas:
            celdas = []
            for foto, flow in par:
                nombre = foto.tipo_foto.nombre if foto.tipo_foto else 'Foto del producto'
                pie = foto.descripcion or ''
                celda = [flow, Spacer(1, 1 * mm), Paragraph(_txt(nombre, 48), st['foto_cap'])]
                if pie:
                    celda.append(Paragraph(_txt(pie, 60), st['foto_guia']))
                celdas.append(celda)
            while len(celdas) < 2:
                celdas.append('')
            t = Table([celdas], colWidths=[(ancho - 6 * mm) / 2 + 3 * mm] * 2)
            t.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            els.append(KeepTogether(t))
    else:
        els.append(Paragraph(
            '⚠ Este requerimiento se envía SIN evidencia fotográfica.', st['falta']))
    if fotos_perdidas:
        els.append(Spacer(1, 1.5 * mm))
        els.append(Paragraph(
            f'⚠ {fotos_perdidas} foto(s) registradas no pudieron recuperarse del '
            'servidor y no se incluyen.', st['falta']))

    # ── Respuesta del proveedor (página propia) ──────────────────────────
    els.append(PageBreak())
    els.extend(_bloque_respuesta(ancho, st, plazo_dias))

    els.append(Spacer(1, 6 * mm))
    contacto = []
    if usuario is not None:
        nombre_contacto = usuario.get_full_name() or usuario.get_username()
        contacto.append(f'Responsable: {nombre_contacto}')
        if usuario.email:
            contacto.append(usuario.email)
    elif requerimiento.usuario_creador_id:
        contacto.append(f'Responsable: {requerimiento.usuario_creador.get_full_name()}')
    if contacto:
        els.append(Paragraph(_txt(' · '.join(contacto), 120), st['subtitulo']))

    doc.build(els, onFirstPage=_pie, onLaterPages=_pie)
    return buffer.getvalue()


def nombre_archivo_pdf(requerimiento) -> str:
    return f'Requerimiento_{requerimiento.numero_requerimiento}.pdf'
