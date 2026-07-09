"""
_analisis_skechers_2027.py — Fase 1: cruce del CATALOGO SKECHERS 2027-1
(pedido dealer del Excel) contra venta/stock reales de RetailMind, y CALCULO
de la compra sugerida para la temporada 2027.  SOLO LECTURA. Seguro en prod.

    python _analisis_skechers_2027.py

Lee  _catalogo_skechers_2027-1.csv  (Fase 0)  ->  _reporte_skechers_2027.html
(se abre solo).  Nivel STYLE-COLOR (articulo en RetailMind = style-color).

Correcciones de metodo (segun feedback del usuario):
  - Demanda = venta de TIENDAS (Sucursal.es_centro_distribucion=False). Las
    bodegas/CD (EDEL, etc.) NO cuentan como demanda de publico; en EDEL se
    CREA y despacha. Stock = total (EDEL+tiendas) = supply despachable.
  - 'MercadoCL 26-1/26-2' = order book SKECHERS Chile de TODOS los dealers
    (5,6M pares) = señal de POPULARIDAD, NO las compras de este retailer.
  - Ing.hist/ST% se muestran pero son poco fiables (migracion=INGRESO_INICIAL,
    restocks reales=TRASPASO). La decision usa cobertura = stock / demanda.
"""
import os
import csv
import html
import django
from collections import defaultdict
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import BigIntegerField, F, Sum  # noqa: E402
from django.db.models.functions import Abs, ExtractYear  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.constants_kardex import (  # noqa: E402
    CONCEPTOS_ABASTECIMIENTO,
    CONCEPTOS_VENTA,
)
from app.models import (  # noqa: E402
    AtributoOpcion,
    Movimientos_Producto,
    Producto,
    Producto_Talla,
    Sucursal,
)

# ---------------- PARAMETROS DE LA RECOMENDACION (ajustables) ----------------
MESES_OBJETIVO = 6      # cobertura forward objetivo para la temporada 2027
PACK = 6               # redondeo de la sugerencia (media curva de tallas)
# tope de TEST para novedades segun popularidad de mercado (MercadoCL 26-1+26-2)
NOV_ALTA, NOV_MEDIA = 2000, 500
CAP_ALTA, CAP_MEDIA, CAP_BAJA = 30, 18, 12
# -----------------------------------------------------------------------------

BI = BigIntegerField()
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, '_catalogo_skechers_2027-1.csv')
HTML_OUT = os.path.join(HERE, '_reporte_skechers_2027.html')


def u_expr():
    return Sum(Abs('cantidad'), output_field=BI)


# ---------------- universo ----------------
print("0. Universo SKECHERS ...")
marca_ids = list(AtributoOpcion.objects.filter(valor__icontains='SKECH')
                 .values_list('id', flat=True))
prod_rows = list(Producto.objects.filter(atributo1_id__in=marca_ids,
                                         excluir_de_analitica=False)
                 .values('id', 'articulo'))
prod_ids = [r['id'] for r in prod_rows]
art_de = {r['id']: (str(r['articulo']).strip().upper() if r['articulo'] else '')
          for r in prod_rows}
articulo_to_pids = defaultdict(list)
style_to_pids = defaultdict(list)
for pid, art in art_de.items():
    if not art:
        continue
    articulo_to_pids[art].append(pid)
    style_to_pids[art.split('-')[0]].append(pid)

cd_ids = set(Sucursal.objects.filter(es_centro_distribucion=True)
             .values_list('id', flat=True))
print(f"   productos {len(prod_ids):,} | articulos {len(articulo_to_pids):,} | "
      f"modelos {len(style_to_pids):,} | bodegas/CD {len(cd_ids)}")

movs_ok = Movimientos_Producto.objects.filter(
    ProductoTalla__producto_id__in=prod_ids, estado='COMPLETADO')

# ---------------- metricas por producto ----------------
print("Agregando stock (total), venta PUBLICO (tiendas) e ingreso ...")
hoy = timezone.localdate()
ini_365 = hoy - timedelta(days=365)
ini_90 = hoy - timedelta(days=90)

stock_pid = defaultdict(int)   # stock total (EDEL + tiendas = supply)
for r in (Producto_Talla.objects.filter(producto_id__in=prod_ids)
          .values('producto_id').annotate(s=Sum('stock', output_field=BI))):
    stock_pid[r['producto_id']] = r['s'] or 0

# ventas SOLO tiendas (publico) -> demanda real de reposicion
vpub = movs_ok.filter(concepto__in=CONCEPTOS_VENTA,
                      ProductoTalla__producto__sucursal__es_centro_distribucion=False)
vttm_pid = defaultdict(int)
for r in (vpub.filter(fecha__gte=ini_365)
          .values('ProductoTalla__producto_id').annotate(u=u_expr())):
    vttm_pid[r['ProductoTalla__producto_id']] = r['u'] or 0
v90_pid = defaultdict(int)
for r in (vpub.filter(fecha__gte=ini_90)
          .values('ProductoTalla__producto_id').annotate(u=u_expr())):
    v90_pid[r['ProductoTalla__producto_id']] = r['u'] or 0

ingh_pid = defaultdict(int)
for r in (movs_ok.filter(concepto__in=CONCEPTOS_ABASTECIMIENTO)
          .values('ProductoTalla__producto_id').annotate(u=u_expr())):
    ingh_pid[r['ProductoTalla__producto_id']] = r['u'] or 0
vhist_pid = defaultdict(int)
for r in (vpub.values('ProductoTalla__producto_id').annotate(u=u_expr())):
    vhist_pid[r['ProductoTalla__producto_id']] = r['u'] or 0


def suma(pids, m):
    return sum(m.get(p, 0) for p in pids)


# ---------------- A. ingresos por año  /  B. remanente por año ----------------
ing_anio = list(movs_ok.filter(concepto__in=CONCEPTOS_ABASTECIMIENTO)
                .annotate(anio=ExtractYear('fecha')).values('anio')
                .annotate(u=u_expr(),
                          costo=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI))
                .order_by('anio'))
rem_anio = list(Producto_Talla.objects.filter(producto_id__in=prod_ids, stock__gt=0)
                .annotate(anio=ExtractYear('producto__fecha_creacion'))
                .values('anio')
                .annotate(u=Sum('stock', output_field=BI),
                          valor=Sum(F('stock') * F('producto__costo'), output_field=BI))
                .order_by('anio'))


# ---------------- recomendacion ----------------
def sugerir(cat, buy, stock, vttm, m_cob, mercado):
    """Pares sugeridos a comprar 2027 (forward MESES_OBJETIVO)."""
    if cat in ('recortar', 'ajustar', 'sinventa'):
        return 0                                   # ya cubierto / no rota
    if cat == 'reponer':                           # cobertura publico < 6m
        objetivo = (vttm / 12.0) * MESES_OBJETIVO
        return int(max(0, round((objetivo - stock) / PACK) * PACK))
    if cat == 'novcolor':                          # color nueva
        if m_cob is None:                          # el modelo NO vende -> no testear
            return 0
        cap = CAP_ALTA if m_cob < 12 else CAP_BAJA
        return int(min(buy, cap))
    if cat == 'novmodelo':                         # modelo nuevo -> test x popularidad
        cap = CAP_ALTA if mercado >= NOV_ALTA else (CAP_MEDIA if mercado >= NOV_MEDIA else CAP_BAJA)
        return int(min(buy, cap))
    return 0


# ---------------- catalogo por color ----------------
print("Cruzando catalogo 2027-1 y calculando sugerencia ...")
filas = []
with open(CSV_PATH, encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        buy = float(row['total_tiendas'] or 0)
        if buy <= 0:
            continue
        sc = (row['style_color'] or '').strip().upper()
        style = (row['style'] or '').strip().upper()
        color = (row['color'] or '').strip().upper()
        whs = float(row['whs'] or 0)
        mer1 = int(float(row['orden_2026_1'] or 0))
        mer2 = int(float(row['orden_2026_2'] or 0))

        pids_c = articulo_to_pids.get(sc, [])
        pids_m = style_to_pids.get(style, [])
        stock = suma(pids_c, stock_pid)
        vttm = suma(pids_c, vttm_pid)
        v90 = suma(pids_c, v90_pid)
        ingh = suma(pids_c, ingh_pid)
        vhist = suma(pids_c, vhist_pid)
        m_stock = suma(pids_m, stock_pid)
        m_vttm = suma(pids_m, vttm_pid)
        m_cob = (m_stock / (m_vttm / 12.0)) if m_vttm else None

        st = (100.0 * vhist / ingh) if ingh else None
        cob = (stock / (vttm / 12.0)) if vttm else None

        mercado = mer1 + mer2
        # 1) categoria
        if pids_c and (ingh or vhist or stock):
            if vttm == 0 and stock > 0:
                cat = 'sinventa'
            elif vttm == 0:
                cat = 'novcolor'
            elif cob >= 12:
                cat = 'recortar'
            elif cob >= 6:
                cat = 'ajustar'
            else:
                cat = 'reponer'
        elif pids_m:
            cat = 'novcolor'
        else:
            cat = 'novmodelo'

        # 2) cantidad sugerida
        sug = sugerir(cat, int(buy), stock, vttm, m_cob, mercado)
        cob_post = ((stock + sug) / (vttm / 12.0)) if vttm else None

        # 3) veredicto en claro: ACCION - MOTIVO
        if cat == 'recortar':
            ver = f'NO comprar — sobra stock ({cob:.0f} meses de cobertura)'
        elif cat == 'ajustar':
            ver = f'No comprar por ahora — ya cubierto ({cob:.0f} meses)'
        elif cat == 'reponer':
            ver = f'COMPRAR {sug} — rota bien, solo {cob:.0f} meses de stock'
        elif cat == 'sinventa':
            ver = f'NO comprar — no vende hace 12m (liquidar los {stock} en stock)'
        elif cat == 'novmodelo':
            pop = ('popular en Chile' if mercado >= NOV_ALTA
                   else 'demanda media' if mercado >= NOV_MEDIA else 'poca demanda')
            ver = (f'TEST {sug} — modelo nuevo, {pop} (mercado {mercado:,})' if sug
                   else f'No comprar — modelo nuevo con poca demanda (mercado {mercado:,})')
        else:  # novcolor
            if sug:
                ver = f'TEST {sug} — color nueva; el modelo vende {m_vttm}/año'
            elif m_vttm:
                ver = f'No comprar — color nueva pero modelo sobre-stockeado ({m_cob:.0f} meses)'
            else:
                ver = 'No comprar — color/modelo sin venta al público 12m'

        filas.append(dict(
            style=style, color=color, familia=row['familia'] or '', buy=int(buy),
            sug=sug, whs=whs, stock=stock, vttm=vttm, v90=v90, ingh=ingh,
            vhist=vhist, st=st, cob=cob, cob_post=cob_post, mer1=mer1, mer2=mer2,
            m_stock=m_stock, m_vttm=m_vttm, cat=cat, ver=ver,
        ))

filas.sort(key=lambda x: -x['buy'])

# ---------------- KPIs ----------------
tot_buy = sum(r['buy'] for r in filas)
tot_sug = sum(r['sug'] for r in filas)
costo_buy = sum(r['buy'] * r['whs'] for r in filas)
costo_sug = sum(r['sug'] * r['whs'] for r in filas)
CATS = ['reponer', 'ajustar', 'recortar', 'sinventa', 'novcolor', 'novmodelo']
cnt = {c: sum(1 for r in filas if r['cat'] == c) for c in CATS}
sug_cat = {c: sum(r['sug'] for r in filas if r['cat'] == c) for c in CATS}
buy_cat = {c: sum(r['buy'] for r in filas if r['cat'] == c) for c in CATS}

# --- datos para el RESUMEN EJECUTIVO ---
tot_ing_hist = sum((r['u'] or 0) for r in ing_anio)
u_reciente = sum((r['u'] or 0) for r in ing_anio if r['anio'] and r['anio'] >= hoy.year - 2)
anio_top_ing = max(ing_anio, key=lambda r: (r['u'] or 0))['anio'] if ing_anio else '-'
pct_ajuste = (100.0 * (tot_buy - tot_sug) / tot_buy) if tot_buy else 0
n_nov = cnt['novcolor'] + cnt['novmodelo']
CAT_LABEL = {'reponer': '🟢 Reponer', 'ajustar': '🟡 Ajustar', 'recortar': '🔴 Recortar',
             'sinventa': '⚪ Sin venta', 'novcolor': '🟣 Novedad color', 'novmodelo': '🔵 Novedad modelo'}
CAT_DESC = {
    'reponer': 'Rotan bien (cobertura &lt;6m) → COMPRAR, rellenar a 6 meses',
    'ajustar': 'Cobertura 6–12m → suficiente, no comprar aún',
    'recortar': 'Sobre-stock ≥12m → recortar a 0',
    'sinventa': 'Con stock pero sin venta 12m → 0, liquidar lo que hay',
    'novcolor': 'Color nueva de un modelo que ya vendes → test acotado',
    'novmodelo': 'Modelo nuevo sin historia tuya → test según popularidad',
}

# ---------------- HTML ----------------
CSS = """
<style>
:root{--p:#405189;--a:#0ab39c;--bg:#f6f7fb;--ink:#1a1a2e;--mut:#7c85a3;}
*{box-sizing:border-box;font-family:'Segoe UI',system-ui,sans-serif;}
body{margin:0;background:var(--bg);color:var(--ink);padding:24px;}
h1{margin:0 0 4px;font-size:22px;} .sub{color:var(--mut);margin-bottom:18px;font-size:13px;}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px;}
.card{background:#fff;border-radius:12px;padding:14px 18px;box-shadow:0 1px 4px rgba(0,0,0,.06);min-width:150px;}
.card .k{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;}
.card .v{font-size:22px;font-weight:700;margin-top:4px;}
.card.hl{background:linear-gradient(135deg,var(--p),var(--a));color:#fff;}
.card.hl .k{color:#e5eefb;}
.grid2{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px;}
.panel{background:#fff;border-radius:12px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.06);flex:1;min-width:320px;}
.panel h3{margin:0 0 10px;font-size:14px;}
table{border-collapse:collapse;width:100%;font-size:12.5px;}
th,td{padding:6px 8px;text-align:right;border-bottom:1px solid #eef0f5;white-space:nowrap;}
th{background:#eef1f7;position:sticky;top:0;cursor:pointer;user-select:none;}
th.l,td.l{text-align:left;} td.sug{font-weight:700;color:#0a7d68;background:#f2fbf8;}
tbody tr:hover{background:#fafbff;}
.main{background:#fff;border-radius:12px;padding:8px;box-shadow:0 1px 4px rgba(0,0,0,.06);overflow:auto;max-height:68vh;}
.tag{padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;display:inline-block;}
.reponer{background:#d1f5ec;color:#0a7d68;} .ajustar{background:#fff3cd;color:#8a6d0b;}
.recortar{background:#fde2e1;color:#c0392b;} .sinventa{background:#e2e3e5;color:#5a5f66;}
.novcolor{background:#e7e0fb;color:#5b3fb5;} .novmodelo{background:#d6e9ff;color:#1c62b9;}
.b-reponer{border-left:3px solid #0ab39c;} .b-recortar{border-left:3px solid #e74c3c;}
.b-sinventa{border-left:3px solid #9aa0a6;} .b-ajustar{border-left:3px solid #f1c40f;}
.b-novcolor{border-left:3px solid #7b5be0;} .b-novmodelo{border-left:3px solid #3b82f6;}
.warn{font-size:11px;color:#8a6d0b;margin-top:8px;line-height:1.5;}
</style>
"""
JS = """
<script>
function sortT(th){
 var t=th.closest('table'),tb=t.tBodies[0],i=Array.from(th.parentNode.children).indexOf(th);
 var asc=th.dataset.asc=th.dataset.asc==='1'?'0':'1';
 var rows=Array.from(tb.rows);
 rows.sort(function(a,b){
   var x=a.cells[i].dataset.v??a.cells[i].innerText,y=b.cells[i].dataset.v??b.cells[i].innerText;
   var nx=parseFloat(x),ny=parseFloat(y);
   if(!isNaN(nx)&&!isNaN(ny)){x=nx;y=ny;}
   return (x>y?1:x<y?-1:0)*(asc==='1'?1:-1);
 });
 rows.forEach(function(r){tb.appendChild(r);});
}
</script>
"""


def fmt(n, dec=0):
    if n is None:
        return '<span style="color:#aaa">-</span>'
    return f"{n:,.{dec}f}"


def td(v, dec=0, cls=''):
    raw = '' if v is None else v
    disp = fmt(v, dec) if isinstance(v, (int, float)) or v is None else html.escape(str(v))
    return f'<td class="{cls}" data-v="{raw}">{disp}</td>'


rowsA = ''.join(f"<tr><td class='l' data-v='{r['anio']}'>{r['anio']}</td>"
                f"{td(r['u'] or 0)}{td(r['costo'] or 0)}</tr>" for r in ing_anio)
tA = (f"<table><thead><tr><th class='l' onclick='sortT(this)'>Año</th>"
      f"<th onclick='sortT(this)'>Ingresado u</th><th onclick='sortT(this)'>Costo $</th>"
      f"</tr></thead><tbody>{rowsA}</tbody></table>")
rowsB = ''.join(f"<tr><td class='l' data-v='{r['anio'] or 0}'>{r['anio'] or '(sin)'}</td>"
                f"{td(r['u'] or 0)}{td(r['valor'] or 0)}</tr>" for r in rem_anio)
tB = (f"<table><thead><tr><th class='l' onclick='sortT(this)'>Año creación</th>"
      f"<th onclick='sortT(this)'>Stock u</th><th onclick='sortT(this)'>Valor costo $</th>"
      f"</tr></thead><tbody>{rowsB}</tbody></table>")

# cada head: (align, titulo corto, ayuda tooltip)
heads = [
    ('l', 'Modelo', 'Código del estilo SKECHERS'),
    ('l', 'Color', 'Código de color'),
    ('l', 'Familia', 'Silueta / línea del zapato'),
    ('', 'Pedido<br>catálogo', 'Pares que propone comprar el Excel del dealer'),
    ('', 'COMPRAR<br>sugerido', 'Pares que recomendamos comprar (nuestro cálculo)'),
    ('', 'Stock<br>hoy', 'Pares en existencia hoy (todas las sucursales)'),
    ('', 'Vendido<br>12 meses', 'Pares vendidos al público en tiendas, últimos 365 días = tu demanda anual'),
    ('', 'Vendido<br>90 días', 'Pares vendidos en tiendas los últimos 90 días (tendencia reciente)'),
    ('', 'Cobertura<br>(meses)', 'Cuántos meses te dura el stock a ese ritmo = Stock ÷ (Vendido12m ÷ 12). Alto=sobra, bajo=falta'),
    ('', 'Cobertura<br>tras comprar', 'Meses de cobertura si compras lo sugerido'),
    ('', 'Demanda<br>mercado CL', 'Pares que pidió TODO el mercado chileno (todos los dealers) en 2026 = popularidad. NO son tus compras'),
    ('', 'Stock<br>del modelo', 'Stock sumando TODAS las colores del modelo (contexto)'),
    ('', 'Vendido 12m<br>del modelo', 'Venta anual sumando todas las colores del modelo (contexto para colores nuevas)'),
    ('l', 'Veredicto — acción y motivo', 'Qué hacer y por qué'),
]
th_html = ''.join(f"<th class='{c}' title=\"{html.escape(t)}\" onclick='sortT(this)'>{h}</th>"
                  for c, h, t in heads)
body = ''
for r in filas:
    body += (
        f"<tr class='b-{r['cat']}'>"
        f"<td class='l'>{html.escape(r['style'])}</td>"
        f"<td class='l'>{html.escape(r['color'])}</td>"
        f"<td class='l'>{html.escape(r['familia'][:22])}</td>"
        f"{td(r['buy'])}{td(r['sug'],0,'sug')}{td(r['stock'])}{td(r['vttm'])}{td(r['v90'])}"
        f"{td(r['cob'],0)}{td(r['cob_post'],0)}{td(r['mer1']+r['mer2'])}"
        f"{td(r['m_stock'])}{td(r['m_vttm'])}"
        f"<td class='l'><span class='tag {r['cat']}'>{html.escape(r['ver'])}</span></td></tr>")
tMain = (f"<div class='main'><table><thead><tr>{th_html}</tr></thead>"
         f"<tbody>{body}</tbody></table></div>")

# glosario de indicadores
GLOS = [
    ('Pedido catálogo', 'Pares que el Excel del dealer propone comprar de esa color.'),
    ('COMPRAR sugerido', 'Pares que recomendamos comprar (nuestro cálculo, columna verde).'),
    ('Stock hoy', 'Pares en existencia hoy, sumando todas las sucursales (EDEL + tiendas).'),
    ('Vendido 12 meses', 'Pares vendidos al público en TIENDAS en los últimos 365 días. Es tu demanda real (no cuenta EDEL/bodega).'),
    ('Vendido 90 días', 'Pares vendidos en tiendas en los últimos 90 días. Sirve para ver si viene subiendo o cayendo.'),
    ('Cobertura (meses)', 'Cuántos meses te dura el stock actual a ese ritmo de venta. = Stock ÷ (Vendido 12m ÷ 12). Ejemplo: 24 = tienes stock para 2 años → sobra.'),
    ('Cobertura tras comprar', 'La cobertura que quedaría si compras lo sugerido.'),
    ('Demanda mercado CL', 'Cuántos pares pidió TODO el mercado chileno de SKECHERS (todos los dealers juntos) en 2026. Es una señal de qué tan popular es esa color. NO son tus compras.'),
    ('Stock / Vendido del modelo', 'Lo mismo pero sumando TODAS las colores del modelo. Sirve para juzgar una color nueva por cómo va el modelo completo.'),
    ('Veredicto', 'La acción recomendada (comprar / no comprar / test) y el motivo en una frase.'),
]
glos_html = ('<div class="panel" style="margin-bottom:20px"><h3>📖 Qué significa cada indicador</h3>'
             '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:12.5px;line-height:1.5">'
             + ''.join(f"<div><b>{html.escape(k)}:</b> {html.escape(v)}</div>" for k, v in GLOS)
             + '</div></div>')


def kcard(k, v, hl=False):
    return f"<div class='card{' hl' if hl else ''}'><div class='k'>{k}</div><div class='v'>{v}</div></div>"


cards = (
    kcard('Pedido catálogo', f"{tot_buy:,} pares", False) +
    kcard('SUGERIDO 2027', f"{tot_sug:,} pares", True) +
    kcard('Costo sugerido', f"${costo_sug:,.0f}") +
    kcard('Ahorro vs catálogo', f"${costo_buy - costo_sug:,.0f}") +
    kcard('🟢 Reponer', f"{cnt['reponer']} · {sug_cat['reponer']:,}p") +
    kcard('🔴 Recortar (sug 0)', f"{cnt['recortar']} · {buy_cat['recortar']:,}p") +
    kcard('⚪ Sin venta (sug 0)', f"{cnt['sinventa']}") +
    kcard('🟣🔵 Novedades', f"{cnt['novcolor']+cnt['novmodelo']} · {sug_cat['novcolor']+sug_cat['novmodelo']:,}p")
)

# tabla de categorias (semaforo del veredicto)
cat_rows = ''.join(
    f"<tr class='b-{c}'><td class='l'>{CAT_LABEL[c]}</td>{td(cnt[c])}{td(buy_cat[c])}"
    f"<td class='sug' data-v='{sug_cat[c]}'>{sug_cat[c]:,}</td>"
    f"<td class='l' style='color:#666'>{CAT_DESC[c]}</td></tr>" for c in CATS)
cat_tbl = (f"<table><thead><tr><th class='l'>Categoría</th><th>Colores</th>"
           f"<th>Ped.catálogo</th><th>Sugerido</th><th class='l'>Qué significa / acción</th>"
           f"</tr></thead><tbody>{cat_rows}</tbody>"
           f"<tfoot><tr style='font-weight:700;background:#eef1f7'><td class='l'>TOTAL</td>"
           f"{td(sum(cnt.values()))}{td(tot_buy)}<td class='sug'>{tot_sug:,}</td><td></td></tr></tfoot></table>")

_dir = 'menos' if tot_sug <= tot_buy else 'más'
resumen_html = f"""
<div class="panel" style="margin-bottom:20px;border-left:4px solid var(--a)">
  <h3>Resumen ejecutivo — SKECHERS 2027-1</h3>
  <p style="line-height:1.7;font-size:13.5px;margin:0 0 12px">
    <b>Compras por año (histórico).</b> Has ingresado <b>{tot_ing_hist:,} pares</b> de SKECHERS en total.
    El grueso figura en <b>{anio_top_ing}</b> por la migración legacy (todo entró como INGRESO_INICIAL ese año),
    así que ese año NO es una compra real; como arribos recientes reales, en los últimos 2 años entraron
    <b>{u_reciente:,} pares</b> (ver panel A). El stock que aún queda, por año de creación, está en el panel B.<br><br>
    <b>Compra sugerida 2027.</b> El catálogo dealer propone <b>{tot_buy:,} pares</b> (${costo_buy:,.0f} costo).
    El análisis sugiere comprar <b>{tot_sug:,} pares</b> (${costo_sug:,.0f}) — un <b>{abs(pct_ajuste):.0f}% {_dir}</b>.
    Se concentra en <b>{cnt['reponer']} colores de reposición</b> ({sug_cat['reponer']:,}p) y
    <b>{n_nov} novedades</b> ({sug_cat['novcolor']+sug_cat['novmodelo']:,}p de test); se recorta a <b>0</b> en
    <b>{cnt['recortar']+cnt['sinventa']} colores</b> sobre-stockeados o sin venta.
  </p>
  {cat_tbl}
  <p style="line-height:1.7;font-size:13px;margin:14px 0 0">
    <b>Cómo se decide (explicación):</b></p>
  <ul style="line-height:1.7;font-size:13px;margin:4px 0 0">
    <li><b>Demanda = venta de tiendas (público)</b>, excluyendo EDEL y bodegas/CD — porque el pedido repone las tiendas, no la bodega donde se crea y despacha.</li>
    <li><b>Regla de reposición:</b> llevar cada color a <b>{MESES_OBJETIVO} meses</b> de cobertura (stock ÷ demanda mensual). Si ya tiene ≥6 meses, no se compra.</li>
    <li><b>Sobre-stock (≥12m) y sin venta → 0:</b> SKECHERS está globalmente sobre-stockeado; no se suma a lo que no rota.</li>
    <li><b>Novedades (sin historia tuya):</b> se compran como <b>test acotado</b> según la popularidad del mercado chileno (columna MerCL = order book de todos los dealers, NO tus compras).</li>
    <li><b>No me apoyo</b> en ingreso histórico ni sell-through: están distorsionados por la migración. La señal principal es la <b>cobertura</b>.</li>
  </ul>
</div>"""

html_doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SKECHERS 2027-1 · Compra sugerida</title>{CSS}{JS}</head><body>
<h1>SKECHERS 2027-1 — Compra sugerida por modelo/color</h1>
<div class="sub">Generado {hoy} · demanda = venta público (tiendas, sin EDEL/CD) ·
objetivo cobertura {MESES_OBJETIVO} meses forward · click en encabezado para ordenar</div>
<div class="cards">{cards}</div>
{resumen_html}
<div class="grid2">
  <div class="panel"><h3>A. Lo que ha LLEGADO por año (kardex abastecimiento real)</h3>{tA}
    <div class="warn">Son movimientos de recepción reales (no fecha de creación). OJO: la migración
    cargó casi todo como INGRESO_INICIAL en su año, así que el reparto por año está sesgado.</div></div>
  <div class="panel"><h3>B. Stock que QUEDA por año (proxy = año de creación)</h3>{tB}
    <div class="warn">Como todo se crea en EDEL y se despacha, fecha_creacion ≈ año de llegada a EDEL;
    las copias de tienda se crean al despachar → aproximado.</div></div>
</div>
{glos_html}
<h3 style="margin:0 0 8px">C. Pedido 2027-1 — {len(filas)} colores · Pedido {tot_buy:,} → <b>Sugerido {tot_sug:,} pares</b>
<span style="font-weight:400;font-size:12px;color:#7c85a3">(pasa el mouse por cada título para ver su definición)</span></h3>
{tMain}
<div class="warn">
<b>Cómo se calcula COMPRAR sugerido:</b> si rota bien (cobertura &lt;6 meses) → llevar el stock a {MESES_OBJETIVO} meses de venta;
si ya tiene 6–12 meses o &ge;12 meses o no vende → 0; colores/modelos nuevos → test acotado por la popularidad
del mercado (Demanda mercado CL). Puedes ajustar los parámetros (meses objetivo, topes de test) en la cabecera
del script.</div>
</body></html>"""

with open(HTML_OUT, 'w', encoding='utf-8') as f:
    f.write(html_doc)

# ---------------- PDF (reportlab) — tabla COMPLETA de productos ----------------
PDF_OUT = os.path.join(HERE, '_reporte_skechers_2027.pdf')
pdf_ok = False
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors as rc
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer)

    def pf(v, dec=0):
        return '-' if v is None else f"{v:,.{dec}f}"

    VER_SHORT = {'reponer': 'REPONER', 'ajustar': 'OK', 'recortar': 'RECORTAR',
                 'sinventa': 'SIN VENTA', 'novcolor': 'NOV COLOR', 'novmodelo': 'NOV MODELO'}
    CAT_DESC_PDF = {
        'reponer': 'Rotan bien (<6m) -> comprar a 6m', 'ajustar': '6-12m -> suficiente, 0',
        'recortar': 'Sobre-stock >=12m -> 0', 'sinventa': 'Stock sin venta 12m -> 0',
        'novcolor': 'Color nueva, modelo vende -> test', 'novmodelo': 'Modelo nuevo -> test x popularidad'}
    TINT = {'reponer': rc.HexColor('#d1f5ec'), 'ajustar': rc.HexColor('#fff3cd'),
            'recortar': rc.HexColor('#fde2e1'), 'sinventa': rc.HexColor('#e2e3e5'),
            'novcolor': rc.HexColor('#e7e0fb'), 'novmodelo': rc.HexColor('#d6e9ff')}

    styles = getSampleStyleSheet()
    st_h1 = ParagraphStyle('h1', parent=styles['Title'], fontSize=15, spaceAfter=3)
    st_body = ParagraphStyle('body', parent=styles['Normal'], fontSize=9, leading=13)
    st_small = ParagraphStyle('small', parent=styles['Normal'], fontSize=7.5, leading=10)

    doc = SimpleDocTemplate(PDF_OUT, pagesize=landscape(A4), leftMargin=9 * mm,
                            rightMargin=9 * mm, topMargin=9 * mm, bottomMargin=9 * mm)
    E = []
    E.append(Paragraph("SKECHERS 2027-1 - Compra sugerida por modelo/color", st_h1))
    _d = 'menos' if tot_sug <= tot_buy else 'mas'
    E.append(Paragraph(
        f"<b>Compras por año:</b> ingresado {tot_ing_hist:,} pares histórico "
        f"(pico {anio_top_ing} = migración, no compra real; últimos 2 años {u_reciente:,}). "
        f"<b>Compra sugerida 2027:</b> catálogo {tot_buy:,} pares (${costo_buy:,.0f}) -&gt; sugerido "
        f"<b>{tot_sug:,} pares</b> (${costo_sug:,.0f}), {abs(pct_ajuste):.0f}% {_d}. "
        f"Reponer {cnt['reponer']} ({sug_cat['reponer']:,}p); novedades {n_nov} "
        f"({sug_cat['novcolor']+sug_cat['novmodelo']:,}p test); recorte a 0 en {cnt['recortar']+cnt['sinventa']}.",
        st_body))
    E.append(Spacer(1, 3 * mm))
    E.append(Paragraph(
        f"<b>Método:</b> demanda = venta de tiendas (público, sin EDEL/CD); regla = llevar a "
        f"{MESES_OBJETIVO} meses de cobertura; sobre-stock (&gt;=12m) y sin venta -&gt; 0; novedades = test según "
        f"popularidad de mercado (MerCL = order book Chile de todos los dealers, NO tus compras). "
        f"Ingreso/ST% no fiables por migración -&gt; se decide por cobertura.", st_small))
    E.append(Spacer(1, 3 * mm))

    # tabla resumen por categoria
    cat_data = [['Categoría', 'Colores', 'Ped.cat', 'Sugerido', 'Qué significa / acción']]
    for c in CATS:
        cat_data.append([c, str(cnt[c]), f"{buy_cat[c]:,}", f"{sug_cat[c]:,}", CAT_DESC_PDF[c]])
    cat_data.append(['TOTAL', str(sum(cnt.values())), f"{tot_buy:,}", f"{tot_sug:,}", ''])
    tcat = Table(cat_data, colWidths=[32 * mm, 18 * mm, 22 * mm, 22 * mm, 90 * mm], repeatRows=1)
    tcat_style = [('BACKGROUND', (0, 0), (-1, 0), rc.HexColor('#405189')),
                  ('TEXTCOLOR', (0, 0), (-1, 0), rc.white),
                  ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                  ('FONTSIZE', (0, 0), (-1, -1), 8), ('GRID', (0, 0), (-1, -1), 0.3, rc.HexColor('#ccc')),
                  ('ALIGN', (1, 0), (3, -1), 'RIGHT'),
                  ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
                  ('BACKGROUND', (0, -1), (-1, -1), rc.HexColor('#eef1f7'))]
    for i, c in enumerate(CATS, start=1):
        tcat_style.append(('BACKGROUND', (0, i), (0, i), TINT[c]))
    tcat.setStyle(TableStyle(tcat_style))
    E.append(tcat)
    E.append(Spacer(1, 4 * mm))
    E.append(Paragraph(f"<b>Detalle — {len(filas)} colores con pedido (tabla completa):</b>", st_body))
    E.append(Spacer(1, 2 * mm))

    # tabla principal COMPLETA (headers claros multilinea + veredicto que envuelve)
    st_th = ParagraphStyle('th', parent=styles['Normal'], fontSize=7, leading=8,
                           textColor=rc.white, fontName='Helvetica-Bold', alignment=1)
    st_cell = ParagraphStyle('cell', parent=styles['Normal'], fontSize=6.5, leading=8)
    heads_pdf = [Paragraph(t, st_th) for t in [
        'Modelo', 'Color', 'Familia', 'Pedido<br/>catálogo', 'COMPRAR<br/>sugerido',
        'Stock<br/>hoy', 'Vendido<br/>12 meses', 'Cobertura<br/>(meses)',
        'Demanda<br/>mercado CL', 'Veredicto — acción y motivo']]
    data = [heads_pdf]
    for r in filas:
        data.append([r['style'], r['color'], (r['familia'] or '')[:22], pf(r['buy']),
                     pf(r['sug']), pf(r['stock']), pf(r['vttm']), pf(r['cob']),
                     f"{r['mer1']+r['mer2']:,}", Paragraph(html.escape(r['ver']), st_cell)])
    tmain = Table(data, repeatRows=1, colWidths=[
        18 * mm, 14 * mm, 40 * mm, 15 * mm, 16 * mm, 14 * mm, 16 * mm, 14 * mm, 18 * mm, 52 * mm])
    ts = [('BACKGROUND', (0, 0), (-1, 0), rc.HexColor('#405189')),
          ('TEXTCOLOR', (0, 0), (-1, 0), rc.white),
          ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
          ('FONTSIZE', (0, 0), (-1, -1), 7), ('FONTSIZE', (0, 0), (-1, 0), 7.5),
          ('ALIGN', (3, 0), (8, -1), 'RIGHT'), ('ALIGN', (0, 0), (2, -1), 'LEFT'),
          ('ALIGN', (9, 0), (9, -1), 'LEFT'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
          ('GRID', (0, 0), (-1, -1), 0.25, rc.HexColor('#dddddd')),
          ('FONTNAME', (4, 1), (4, -1), 'Helvetica-Bold'),
          ('TEXTCOLOR', (4, 1), (4, -1), rc.HexColor('#0a7d68')),
          ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
          ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5)]
    for i, r in enumerate(filas, start=1):
        ts.append(('BACKGROUND', (0, i), (-1, i), TINT[r['cat']]))
    tmain.setStyle(TableStyle(ts))
    E.append(tmain)
    doc.build(E)
    pdf_ok = True
except ImportError:
    print("(reportlab no instalado: se genero solo el HTML)")
except Exception as e:
    print(f"(no se pudo generar PDF: {e})")

# ---------------- consola ----------------
print("\n" + "=" * 72)
print("RESUMEN FINAL — SKECHERS 2027-1")
print("-" * 72)
print(f"Compras historicas ingresadas: {tot_ing_hist:,} pares "
      f"(pico en {anio_top_ing} por migracion; ultimos 2 anos {u_reciente:,})")
print(f"PEDIDO catalogo: {tot_buy:,} pares (${costo_buy:,.0f})")
print(f"SUGERIDO 2027:   {tot_sug:,} pares (${costo_sug:,.0f})   "
      f"({abs(pct_ajuste):.0f}% {'menos' if tot_sug <= tot_buy else 'mas'}, "
      f"ahorro ${costo_buy-costo_sug:,.0f})")
print(f"Demanda = venta publico (tiendas, sin EDEL/CD) | cobertura objetivo {MESES_OBJETIVO}m")
print("-" * 72)
print(f"   {'CATEGORIA':<11}{'COLORES':>8}{'PED.CAT':>10}{'SUGERIDO':>10}")
for c in CATS:
    print(f"   {c:<11}{cnt[c]:>8}{buy_cat[c]:>10,}{sug_cat[c]:>10,}")
print(f"   {'TOTAL':<11}{sum(cnt.values()):>8}{tot_buy:>10,}{tot_sug:>10,}")
print(f"\nReporte HTML -> {HTML_OUT}")
if pdf_ok:
    print(f"Reporte PDF  -> {PDF_OUT}  ({len(filas)} productos, tabla completa)")
try:
    os.startfile(PDF_OUT if pdf_ok else HTML_OUT)
except Exception:
    print("(abrelo manualmente)")
