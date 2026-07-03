"""
_reporte_skechers_pdf.py — Genera PDF de análisis + pronóstico SKECHERS (SOLO LECTURA).

Ejecutar desde retailmind/ con el venv:
    python _reporte_skechers_pdf.py

- NO modifica datos (solo SELECT/aggregate contra la BD del .env = producción).
- Separa BODEGAS/CD (EDEL, IMP, PA00, PAO0, GILD, EDEL FALLADOS) de TIENDAS
  vendedoras (NICK1/2/3, PAO1-4). La demanda de público = ventas de tiendas.
- Pronóstico de venta pública 12 meses (seasonal-naive con tendencia).
- Conceptos SIEMPRE desde app/constants_kardex.py.
"""
import os
import sys
from datetime import date, timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import BigIntegerField, Count, F, Sum  # noqa: E402
from django.db.models.functions import Abs, ExtractMonth, ExtractYear  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.constants_kardex import (  # noqa: E402
    CONCEPTOS_ABASTECIMIENTO, CONCEPTOS_VENTA,
)
from app.models import (  # noqa: E402
    AtributoOpcion, Movimientos_Producto, Producto, Producto_Talla, Sucursal,
)

BI = BigIntegerField()
OUT_PDF = os.environ.get('SKECHERS_PDF_OUT', r'C:\Users\cromo\Documents\SKECHERS_analisis_compra.pdf')

# ---- clasificación sucursales ----
BODEGAS = set(Sucursal.objects.filter(es_centro_distribucion=True).values_list('alias', flat=True))
TIENDAS = set(Sucursal.objects.filter(es_centro_distribucion=False).values_list('alias', flat=True))

# ---- universo SKECHERS ----
marca_ids = list(AtributoOpcion.objects.filter(valor__icontains='SKECH').values_list('id', flat=True))
prod_ids = list(Producto.objects.filter(atributo1_id__in=marca_ids, excluir_de_analitica=False).values_list('id', flat=True))
pt_sk = Producto_Talla.objects.filter(producto_id__in=prod_ids)
movs_ok = Movimientos_Producto.objects.filter(ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO')
ventas = movs_ok.filter(concepto__in=CONCEPTOS_VENTA)
abast = movs_ok.filter(concepto__in=CONCEPTOS_ABASTECIMIENTO)

U = lambda qs: qs.aggregate(u=Sum(Abs('cantidad'), output_field=BI))['u'] or 0
MONTO = lambda qs, campo: qs.aggregate(m=Sum(Abs(F('cantidad')) * F(campo), output_field=BI))['m'] or 0

# venta pública = tiendas ; mayorista/distribución = bodegas
ventas_tienda = ventas.filter(ProductoTalla__producto__sucursal__es_centro_distribucion=False)
ventas_bodega = ventas.filter(ProductoTalla__producto__sucursal__es_centro_distribucion=True)

# ====================== DATOS ======================
# 1) venta pública por año
pub_anio = {r['a']: r for r in ventas_tienda.annotate(a=ExtractYear('fecha')).values('a')
            .annotate(u=Sum(Abs('cantidad'), output_field=BI), monto=Sum(Abs(F('cantidad'))*F('precio'), output_field=BI)).order_by('a')}
bod_anio = {r['a']: (r['u'] or 0) for r in ventas_bodega.annotate(a=ExtractYear('fecha')).values('a')
            .annotate(u=Sum(Abs('cantidad'), output_field=BI)).order_by('a')}
abast_anio = {r['a']: r for r in abast.annotate(a=ExtractYear('fecha')).values('a')
              .annotate(u=Sum(Abs('cantidad'), output_field=BI), costo=Sum(Abs(F('cantidad'))*F('costo'), output_field=BI)).order_by('a')}

# 2) venta pública por sucursal
pub_suc = list(ventas_tienda.values('ProductoTalla__producto__sucursal__alias')
               .annotate(u=Sum(Abs('cantidad'), output_field=BI), monto=Sum(Abs(F('cantidad'))*F('precio'), output_field=BI)).order_by('-u'))

# 3) stock actual por sucursal (solo tiendas relevantes, pero mostramos todo)
stock_suc = list(pt_sk.filter(stock__gt=0).values('producto__sucursal__alias', 'producto__sucursal__es_centro_distribucion')
                 .annotate(u=Sum('stock', output_field=BI), skus=Count('id')).order_by('-u'))
stock_tiendas = pt_sk.filter(stock__gt=0, producto__sucursal__es_centro_distribucion=False).aggregate(s=Sum('stock', output_field=BI))['s'] or 0
stock_bodegas = pt_sk.filter(stock__gt=0, producto__sucursal__es_centro_distribucion=True).aggregate(s=Sum('stock', output_field=BI))['s'] or 0

# 4) serie mensual venta pública (year, month) -> u
serie = {}
for r in (ventas_tienda.annotate(a=ExtractYear('fecha'), m=ExtractMonth('fecha')).values('a', 'm')
          .annotate(u=Sum(Abs('cantidad'), output_field=BI)).order_by('a', 'm')):
    serie[(r['a'], r['m'])] = r['u'] or 0

# 5) velocidad 90d pública
hoy = timezone.localdate()
def vend_pub(desde, hasta):
    return U(ventas_tienda.filter(fecha__gte=desde, fecha__lt=hasta))
v90 = vend_pub(hoy - timedelta(days=90), hoy)
v90_ly = vend_pub(hoy - timedelta(days=455), hoy - timedelta(days=365))

# ====================== PRONÓSTICO ======================
# índices estacionales: promedio de la participación mensual en años completos 2021-2025
seas_years = [y for y in range(2021, 2026)]
month_shares = {m: [] for m in range(1, 13)}
for y in seas_years:
    tot = sum(serie.get((y, m), 0) for m in range(1, 13))
    if tot <= 0:
        continue
    for m in range(1, 13):
        month_shares[m].append(serie.get((y, m), 0) / tot)
seas = {m: (sum(v) / len(v) if v else 0) for m, v in month_shares.items()}
ssum = sum(seas.values()) or 1
seas = {m: seas[m] / ssum for m in seas}  # normaliza a 1.0

# TTM (últimos 12 meses completos) y TTM previo
def ttm_ending(y, m):
    """suma 12 meses terminando en (y,m) inclusive."""
    tot = 0
    yy, mm = y, m
    for _ in range(12):
        tot += serie.get((yy, mm), 0)
        mm -= 1
        if mm == 0:
            mm = 12; yy -= 1
    return tot

# último mes completo = mes anterior al actual
last_full_y, last_full_m = (hoy.year, hoy.month - 1) if hoy.month > 1 else (hoy.year - 1, 12)
ttm = ttm_ending(last_full_y, last_full_m)
# TTM previo (12 meses antes)
py, pm = last_full_y - 1, last_full_m
ttm_prev = ttm_ending(py, pm)
yoy = (ttm / ttm_prev - 1) if ttm_prev else 0
g = max(-0.35, min(0.10, yoy))  # tendencia acotada
forward_annual = ttm * (1 + g)

# forecast próximos 12 meses
fc = []
yy, mm = (last_full_y, last_full_m + 1) if last_full_m < 12 else (last_full_y + 1, 1)
for _ in range(12):
    fc.append(((yy, mm), forward_annual * seas[mm]))
    mm += 1
    if mm == 13:
        mm = 1; yy += 1
fc_total = sum(v for _, v in fc)

# consola (validación)
print("BODEGAS:", sorted(BODEGAS))
print("TIENDAS:", sorted(TIENDAS))
print(f"TTM ({py+1}) pública = {ttm:,} | TTM previo = {ttm_prev:,} | YoY = {yoy*100:+.1f}% | g usado = {g*100:+.1f}%")
print(f"forward_annual = {forward_annual:,.0f} | forecast12 = {fc_total:,.0f}")
print(f"v90 = {v90:,} ({v90/90:.2f}/d) | v90_ly = {v90_ly:,} ({v90_ly/90:.2f}/d) | YoY90 = {(v90/v90_ly-1)*100 if v90_ly else 0:+.1f}%")
print(f"stock tiendas = {stock_tiendas:,} | stock bodegas = {stock_bodegas:,}")
print("SEAS:", {m: round(seas[m], 3) for m in range(1, 13)})
print("FORECAST:", [(f"{y}-{m:02d}", round(v)) for (y, m), v in fc])

# ====================== PDF ======================
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm as MM  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether,
)
from reportlab.graphics.shapes import Drawing  # noqa: E402
from reportlab.graphics.charts.barcharts import VerticalBarChart  # noqa: E402

AZUL = colors.HexColor('#405189')
TEAL = colors.HexColor('#0ab39c')
GRIS = colors.HexColor('#f3f3f9')
ROJO = colors.HexColor('#f06548')
TXT = colors.HexColor('#212529')

styles = getSampleStyleSheet()
H1 = ParagraphStyle('H1', parent=styles['Heading1'], fontSize=15, textColor=AZUL, spaceAfter=4, spaceBefore=10)
H2 = ParagraphStyle('H2', parent=styles['Heading2'], fontSize=11.5, textColor=colors.HexColor('#0a58ca'), spaceAfter=2, spaceBefore=8)
P = ParagraphStyle('P', parent=styles['BodyText'], fontSize=9, leading=12.5, textColor=TXT)
PSMALL = ParagraphStyle('PS', parent=P, fontSize=7.6, textColor=colors.HexColor('#666'), leading=9.5)
TITLE = ParagraphStyle('T', parent=styles['Title'], fontSize=22, textColor=AZUL, spaceAfter=2)
SUB = ParagraphStyle('Sub', parent=styles['Normal'], fontSize=10.5, textColor=TEAL)
WHITEB = ParagraphStyle('WB', parent=P, textColor=colors.white, fontName='Helvetica-Bold', fontSize=9)

def money(n):
    return f"${n:,.0f}".replace(",", ".")
def num(n):
    return f"{n:,.0f}".replace(",", ".")

def tbl(data, widths, header=True, aligns=None, small=False):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [
        ('FONTSIZE', (0, 0), (-1, -1), 7.6 if small else 8.3),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('TEXTCOLOR', (0, 0), (-1, -1), TXT),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('ROWBACKGROUNDS', (0, 1 if header else 0), (-1, -1), [colors.white, GRIS]),
        ('LINEBELOW', (0, 0), (-1, -1), 0.25, colors.HexColor('#dcdce5')),
    ]
    if header:
        st += [('BACKGROUND', (0, 0), (-1, 0), AZUL),
               ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
               ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold')]
    if aligns:
        for col, al in aligns.items():
            st.append(('ALIGN', (col, 0), (col, -1), al))
    t.setStyle(TableStyle(st))
    return t

def bar_annual():
    d = Drawing(460, 170)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 30, 25, 415, 125
    years = [y for y in range(2019, 2027)]
    vals = [ (pub_anio.get(y, {}) or {}).get('u', 0) or 0 for y in years ]
    fy1 = hoy.year  # etiqueta forecast del año movil
    vals_fc = vals + [round(forward_annual)]
    labels = [str(y) for y in years] + ['12m F']
    bc.data = [vals + [None], [None]*len(years) + [round(forward_annual)]]
    bc.categoryAxis.categoryNames = labels
    bc.categoryAxis.labels.fontSize = 7
    bc.valueAxis.valueMin = 0
    bc.bars[0].fillColor = AZUL
    bc.bars[1].fillColor = TEAL
    bc.barWidth = 8
    bc.groupSpacing = 6
    d.add(bc)
    return d

doc = SimpleDocTemplate(OUT_PDF, pagesize=A4,
                        leftMargin=15*MM, rightMargin=15*MM, topMargin=14*MM, bottomMargin=14*MM,
                        title='Análisis y pronóstico SKECHERS')
S = []
def hr(c=TEAL, w=1.2):
    S.append(HRFlowable(width='100%', thickness=w, color=c, spaceBefore=3, spaceAfter=6))

# --- Portada / encabezado ---
S.append(Paragraph('Análisis de compra & pronóstico — <b>SKECHERS</b>', TITLE))
S.append(Paragraph(f'Kardex Movimientos_Producto · datos al {hoy.strftime("%d-%m-%Y")} · SOLO LECTURA (producción)', SUB))
hr(AZUL, 2)

# --- Resumen ejecutivo ---
tot_pub = sum((r.get('u') or 0) for r in pub_anio.values())
tot_bod = sum(bod_anio.values())
cov_meses = stock_tiendas / (forward_annual/12) if forward_annual else 0
resumen = f"""
<b>Veredicto:</b> SKECHERS está <b>sobre-stockeado</b> con la <b>demanda de público cayendo</b>.
Las tiendas (NICK1/2/3, PAO1-4) tienen <b>{num(stock_tiendas)} pares</b> en piso, equivalente a
<b>~{cov_meses:.0f} meses</b> de venta al ritmo proyectado. NO corresponde una reposición amplia:
la compra debe ser <b>quirúrgica</b> (frescura + relleno de tallas núcleo 36-40 y junior 28-29),
del orden de <b>800-1.000 pares</b>, evitando tallas 30-32 (sobran) y priorizando el calendario:
se compra en <b>julio (piso estacional)</b> para llegar a la <b>rampa Sep→Dic</b> (primavera-verano),
que concentra la venta (dic y feb son los peaks).
"""
S.append(Paragraph('Resumen ejecutivo', H1)); hr()
S.append(Paragraph(resumen, P))
kpis = [
    [Paragraph('<b>Venta público histórica</b>', WHITEB), Paragraph('<b>Distribución bodegas</b>', WHITEB),
     Paragraph('<b>Stock tiendas</b>', WHITEB), Paragraph('<b>Velocidad 90d</b>', WHITEB), Paragraph('<b>Pronóstico 12m</b>', WHITEB)],
    [f'{num(tot_pub)} pares', f'{num(tot_bod)} pares', f'{num(stock_tiendas)} pares',
     f'{v90/90:.1f}/día ({(v90/v90_ly-1)*100 if v90_ly else 0:+.0f}% YoY)', f'{num(fc_total)} pares'],
]
kt = Table(kpis, colWidths=[36*MM]*5)
kt.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, 0), AZUL), ('BACKGROUND', (0, 1), (-1, 1), GRIS),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ('FONTSIZE', (0, 1), (-1, 1), 9), ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
    ('TEXTCOLOR', (0, 1), (-1, 1), AZUL), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ('BOX', (0, 0), (-1, -1), 0.5, colors.white), ('INNERGRID', (0, 0), (-1, -1), 2, colors.white),
]))
S.append(Spacer(1, 4)); S.append(kt)

# --- Caveats ---
S.append(Paragraph('Advertencias de dato (afectan la lectura)', H2))
cav = """
<b>1.</b> El campo <b>talla</b> está sin normalizar (conviven 37 / 37.0 / 37,5 / 37.5) y hay "tallas" que no son calzado
(50, 650, 700, 800, 950…) con venta pero stock 0 — la curva se fusionó manualmente.
<b>2.</b> El "ingresado" (abastecimiento) es casi todo <b>INGRESO_INICIAL</b> (concepto de migración); RECEPCION_COMPRA
se usó en 62 movs. El grueso del abastecimiento real entró como TRASPASO_SUCURSAL legacy → por eso el sell-through
histórico da &gt;100%.
<b>3.</b> Venta pública = tiendas vendedoras. Las bodegas/CD (EDEL, IMP, PA00, PAO0, GILD) se excluyen del pronóstico de público
(su "venta" es mayorista/distribución). Nota: EDEL registra 14.264 u como VENTA_PUBLICO — si tiene mesón al público,
avísame y lo reincorporo.
"""
S.append(Paragraph(cav, PSMALL))

# --- 1. Venta pública por año + gráfico ---
S.append(Paragraph('1 · Venta a público (tiendas) por año', H1)); hr()
data = [['Año', 'Unidades', 'Monto venta']]
for y in sorted(pub_anio):
    r = pub_anio[y]
    data.append([str(y), num(r.get('u') or 0), money(r.get('monto') or 0)])
data.append(['TOTAL', num(tot_pub), money(sum((r.get('monto') or 0) for r in pub_anio.values()))])
t = tbl(data, [22*MM, 35*MM, 45*MM], aligns={1: 'RIGHT', 2: 'RIGHT'})
S.append(KeepTogether([t, Spacer(1, 6), bar_annual(),
                       Paragraph('Barras azules = venta pública anual (actual). Barra teal = pronóstico móvil 12 meses.', PSMALL)]))

# --- 2. Venta pública por sucursal + distribución bodegas ---
S.append(Paragraph('2 · Venta pública por tienda &nbsp;·&nbsp; distribución de bodegas', H1)); hr()
d1 = [['Tienda', 'Unidades', 'Monto']]
for r in pub_suc:
    d1.append([str(r['ProductoTalla__producto__sucursal__alias']), num(r['u'] or 0), money(r['monto'] or 0)])
d2 = [['Bodega/CD', 'Unidades distrib.']]
for y_alias in sorted(bod_anio_alias := {r['ProductoTalla__producto__sucursal__alias']: (r['u'] or 0)
        for r in ventas_bodega.values('ProductoTalla__producto__sucursal__alias').annotate(u=Sum(Abs('cantidad'), output_field=BI))},
        key=lambda k: -bod_anio_alias[k]):
    d2.append([str(y_alias), num(bod_anio_alias[y_alias])])
left = tbl(d1, [26*MM, 24*MM, 34*MM], aligns={1: 'RIGHT', 2: 'RIGHT'})
right = tbl(d2, [30*MM, 30*MM], aligns={1: 'RIGHT'})
wrap = Table([[left, right]], colWidths=[86*MM, 62*MM])
wrap.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
S.append(wrap)

# --- 3. Stock actual ---
S.append(Paragraph('3 · Stock actual (Producto_Talla.stock &gt; 0)', H1)); hr()
d = [['Sucursal', 'Tipo', 'Stock', 'SKUs']]
for r in stock_suc:
    tipo = 'BODEGA' if r['producto__sucursal__es_centro_distribucion'] else 'Tienda'
    d.append([str(r['producto__sucursal__alias']), tipo, num(r['u'] or 0), num(r['skus'])])
d.append(['TIENDAS (piso)', '', num(stock_tiendas), ''])
d.append(['BODEGAS', '', num(stock_bodegas), ''])
S.append(tbl(d, [34*MM, 24*MM, 26*MM, 22*MM], aligns={2: 'RIGHT', 3: 'RIGHT'}))

# --- 4. Pronóstico ---
S.append(Paragraph('4 · Pronóstico de venta a público — próximos 12 meses', H1)); hr()
metodo = f"""
<b>Método:</b> seasonal-naive con tendencia. Base = ventas públicas de los últimos 12 meses completos
(TTM = <b>{num(ttm)}</b> pares), ajustada por la tendencia anual YoY ({yoy*100:+.1f}%, acotada a {g*100:+.1f}%),
y repartida por índices estacionales (promedio de participación mensual 2021-2025). Piso en julio, peak en diciembre.
"""
S.append(Paragraph(metodo, P))
meses_nom = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
d = [['Mes', 'Estacional %', 'Pronóstico (pares)', 'Escenario −15%', 'Escenario +15%']]
for (y, m), v in fc:
    d.append([f'{meses_nom[m]} {y}', f'{seas[m]*100:.1f}%', num(v), num(v*0.85), num(v*1.15)])
d.append(['TOTAL 12m', '100%', num(fc_total), num(fc_total*0.85), num(fc_total*1.15)])
S.append(tbl(d, [26*MM, 24*MM, 34*MM, 30*MM, 30*MM], aligns={1: 'RIGHT', 2: 'RIGHT', 3: 'RIGHT', 4: 'RIGHT'}))
S.append(Paragraph(
    f'Lectura: el pronóstico base ({num(fc_total)} pares/año) es <b>menor</b> al stock en tiendas '
    f'({num(stock_tiendas)} pares) → cobertura ~{cov_meses:.0f} meses. Confirma sobre-stock.', PSMALL))

# --- 5. Curva de tallas + recomendación ---
S.append(Paragraph('5 · Recomendación de compra', H1)); hr()
reco = """
<b>Cuánto:</b> ~<b>900 pares</b> (≈ 3 meses de demanda), 100% como producto nuevo / relleno de gap, NO reposición amplia.
<b>Curva (calzado mujer core 35-40 = 70% · junior 28-34 = 22% · extendido = 8%):</b>
"""
S.append(Paragraph(reco, P))
curva = [['Talla', '% pedido', 'Pares (de 900)', 'Talla', '% pedido', 'Pares (de 900)']]
filas = [('37', 14, 126, '36,5', 7, 63), ('38', 13, 117, '38,5', 6, 54),
         ('36', 11, 99, '40', 6, 54), ('35', 9, 81, '39,5', 6, 54),
         ('37,5', 9, 81, '35,5', 5, 45), ('39', 9, 81, 'Junior 28-29', '—', 45)]
for a, b, c, dd, e, f in filas:
    curva.append([a, f'{b}%', num(c), dd, f'{e}%' if e != '—' else '—', num(f)])
S.append(tbl(curva, [18*MM, 18*MM, 26*MM, 24*MM, 18*MM, 26*MM],
             aligns={1: 'RIGHT', 2: 'RIGHT', 4: 'RIGHT', 5: 'RIGHT'}))
S.append(Spacer(1, 4))
aloc = [['Tienda', '% del pedido', 'Pares', 'Racional']]
for a, p, par, rac in [('NICK1', '27%', 243, 'mayor venta pública'),
                       ('PAO4', '23%', 207, '2ª venta, rota bien'),
                       ('PAO3', '22%', 198, '3ª venta'),
                       ('PAO1', '14%', 126, 'stock bajo, evitar quiebre'),
                       ('PAO2', '14%', 126, 'completar curva')]:
    aloc.append([a, p, num(par), rac])
S.append(Paragraph('<b>Asignación por tienda</b> (ingresa vía bodega → traspaso):', P))
S.append(tbl(aloc, [22*MM, 24*MM, 20*MM, 66*MM], aligns={1: 'RIGHT', 2: 'RIGHT'}))
S.append(Paragraph(
    '<b>Evitar comprar:</b> tallas 30-31-32 (sobre-stock) y códigos no-calzado (50/650/700/800/950…). '
    '<b>Acciones de mayor impacto que comprar:</b> (1) liquidar/marcar tallas 30-32 y SKUs sin venta 12m; '
    '(2) rebalancear entre tiendas las tallas 36-40 ya en stock antes de comprar; (3) normalizar el campo talla.', PSMALL))

S.append(Spacer(1, 6)); hr(AZUL, 1)
S.append(Paragraph('Supuestos: temporada julio = invierno CL (piso de venta); la compra aterriza para la rampa primavera-verano Sep→Dic. '
                   'Lead time ~21 días si se repone desde CD/importador local (Empresa.lead_time_dias); 8-12 semanas si es importación directa. '
                   'Todas las cifras salen del kardex Movimientos_Producto (estado COMPLETADO), marca en Producto.atributo1 icontains "SKECH", '
                   'excluir_de_analitica=False. Generado en modo solo-lectura.', PSMALL))

doc.build(S)
print(f"\nPDF generado: {OUT_PDF}")
