"""
_analisis_skechers_readonly.py — Análisis histórico de la marca SKECHERS
para decisión de compra (SOLO LECTURA).

Ejecutar desde retailmind/ (donde está manage.py), con el venv activo:

    python _analisis_skechers_readonly.py

NO modifica nada: solo consultas de conteo/agregación contra la BD que
apunte DATABASE_URL / PG_* del .env (actualmente producción). Seguro de
correr contra producción.

Reglas aplicadas (según pedido):
  - Marca en Producto.atributo1 (AtributoOpcion.valor), match icontains 'SKECH'.
  - Excluye Producto.excluir_de_analitica = True.
  - NO usa LoteProducto (en reconciliación); usa kardex Movimientos_Producto
    + Producto_Talla.stock.
  - Conceptos SIEMPRE desde app/constants_kardex.py (no listas inline).
  - Solo movimientos estado='COMPLETADO' para las cifras; se muestra el
    desglose por estado como control.
  - Unidades = Σ|cantidad| (robusto ante bugs de signo de la migración);
    se muestra también la suma firmada como control.
"""
import os
import sys
from datetime import timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import (  # noqa: E402
    BigIntegerField, Count, F, Sum,
)
from django.db.models.functions import Abs, ExtractYear  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.constants_kardex import (  # noqa: E402
    CONCEPTOS_ABASTECIMIENTO,
    CONCEPTOS_TRASPASO_ENTRADA,
    CONCEPTOS_TRASPASO_SALIDA,
    CONCEPTOS_VENTA,
)
from app.models import (  # noqa: E402
    AtributoOpcion,
    Movimientos_Producto,
    Producto,
    Producto_Talla,
)

SEP = '=' * 84
BI = BigIntegerField()


def titulo(txt):
    print(f"\n{SEP}\n{txt}\n{SEP}")


def unidades_expr():
    return Sum(Abs('cantidad'), output_field=BI)


def monto_expr(campo):
    # Σ (|cantidad| * campo_unitario)   -> costo (campo='costo') o venta (campo='precio')
    return Sum(Abs(F('cantidad')) * F(campo), output_field=BI)


def firmada_expr():
    return Sum('cantidad', output_field=BI)


# ============================================================ 0. UNIVERSO SKECHERS
titulo("0. UNIVERSO SKECHERS (control de match de marca)")

marca_ids = list(
    AtributoOpcion.objects.filter(valor__icontains='SKECH').values_list('id', flat=True)
)
opciones = list(
    AtributoOpcion.objects.filter(id__in=marca_ids).values_list('valor', flat=True)
)
print(f"AtributoOpcion que matchean 'SKECH' (icontains): {len(marca_ids)}")
for v in sorted(set(opciones)):
    print(f"   - {v!r}")

productos_sk = Producto.objects.filter(
    atributo1_id__in=marca_ids, excluir_de_analitica=False,
)
prod_excluidos = Producto.objects.filter(
    atributo1_id__in=marca_ids, excluir_de_analitica=True,
).count()
prod_ids = list(productos_sk.values_list('id', flat=True))

pt_sk = Producto_Talla.objects.filter(producto_id__in=prod_ids)

print(f"\nProductos SKECHERS (excluir_de_analitica=False): {productos_sk.count():,}")
print(f"   (excluidos por excluir_de_analitica=True):    {prod_excluidos:,}")
print(f"SKUs (Producto_Talla):                            {pt_sk.count():,}")

# Movimientos base (todas las marcas SKECHERS analíticas)
movs = Movimientos_Producto.objects.filter(
    ProductoTalla__producto_id__in=prod_ids,
)
print(f"Movimientos_Producto (todos los estados):         {movs.count():,}")

print("\nControl — movimientos por estado:")
for f in movs.values('estado').annotate(n=Count('id')).order_by('-n'):
    print(f"   {str(f['estado']):<22} {f['n']:>10,}")

movs_ok = movs.filter(estado='COMPLETADO')

print("\nControl — conceptos EN USO (estado=COMPLETADO) y a qué set pertenecen:")
SET_LABEL = {}
for c in CONCEPTOS_VENTA:              SET_LABEL[c] = 'VENTA'
for c in CONCEPTOS_ABASTECIMIENTO:     SET_LABEL[c] = 'ABASTECIMIENTO'
for c in CONCEPTOS_TRASPASO_SALIDA:    SET_LABEL[c] = 'TRASPASO_SALIDA'
for c in CONCEPTOS_TRASPASO_ENTRADA:   SET_LABEL[c] = 'TRASPASO_ENTRADA'
sin_set = 0
for f in movs_ok.values('concepto').annotate(
        n=Count('id'), u=unidades_expr()).order_by('-n'):
    lab = SET_LABEL.get(f['concepto'], '— (no en sets pedidos)')
    if lab.startswith('—'):
        sin_set += f['n']
    print(f"   {str(f['concepto']):<32} {lab:<24} n={f['n']:>9,}  Σ|u|={f['u'] or 0:>10,}")
print(f"\n   Movimientos COMPLETADO en conceptos fuera de los 4 sets pedidos: {sin_set:,}"
      "  (no entran en las cifras de abajo)")


def por_anio(qs, conceptos, con_costo=False, con_venta=False):
    """Devuelve dict anio -> {'u', 'firmada', 'monto'} para el set de conceptos."""
    base = qs.filter(concepto__in=conceptos)
    ann = {'u': unidades_expr(), 'firmada': firmada_expr(), 'n': Count('id')}
    if con_costo:
        ann['monto'] = monto_expr('costo')
    if con_venta:
        ann['monto'] = monto_expr('precio')
    filas = (base.annotate(anio=ExtractYear('fecha')).values('anio')
             .annotate(**ann).order_by('anio'))
    return {f['anio']: f for f in filas}


def tabla_anual(titulo_txt, data, columnas):
    """columnas = [(clave, header, ancho), ...]"""
    print(f"\n{titulo_txt}")
    header = "   " + f"{'AÑO':<6}" + "".join(f"{h:>{w}}" for _, h, w in columnas)
    print(header)
    print("   " + "-" * (len(header) - 3))
    tot = {c: 0 for c, _, _ in columnas}
    for anio in sorted(data):
        row = data[anio]
        linea = "   " + f"{anio:<6}"
        for c, _, w in columnas:
            val = row.get(c) or 0
            tot[c] += val
            linea += f"{val:>{w},}"
        print(linea)
    print("   " + "-" * (len(header) - 3))
    linea = "   " + f"{'TOTAL':<6}"
    for c, _, w in columnas:
        linea += f"{tot[c]:>{w},}"
    print(linea)
    return tot


# ============================================================ 1. ABASTECIMIENTO
titulo("1. COMPRADO / INGRESADO  (CONCEPTOS_ABASTECIMIENTO)")
print(f"   conceptos: {CONCEPTOS_ABASTECIMIENTO}")
abast = por_anio(movs_ok, CONCEPTOS_ABASTECIMIENTO, con_costo=True)
tot_abast = tabla_anual(
    "Ingresos por año:", abast,
    [('u', 'UNIDADES', 12), ('monto', 'COSTO $', 18), ('firmada', 'Σfirmada', 12)],
)

# ============================================================ 2. TRASPASOS
titulo("2. TRASPASOS  (SALIDA desde bodega  /  ENTRADA en tiendas)")
print(f"   salida:  {CONCEPTOS_TRASPASO_SALIDA}")
print(f"   entrada: {CONCEPTOS_TRASPASO_ENTRADA}")
salida = por_anio(movs_ok, CONCEPTOS_TRASPASO_SALIDA)
entrada = por_anio(movs_ok, CONCEPTOS_TRASPASO_ENTRADA)
tot_salida = tabla_anual("Despachado desde bodega (SALIDA):", salida,
                         [('u', 'UNIDADES', 12), ('n', 'N_MOV', 10)])
tot_entrada = tabla_anual("Recibido en tiendas (ENTRADA):", entrada,
                          [('u', 'UNIDADES', 12), ('n', 'N_MOV', 10)])

print("\nRecibido en tiendas (ENTRADA) por sucursal:")
qs_ent_suc = (movs_ok.filter(concepto__in=CONCEPTOS_TRASPASO_ENTRADA)
              .values('ProductoTalla__producto__sucursal__alias')
              .annotate(u=unidades_expr()).order_by('-u'))
for f in qs_ent_suc:
    suc = f['ProductoTalla__producto__sucursal__alias'] or '(sin sucursal)'
    print(f"   {str(suc):<30} {f['u'] or 0:>10,}")

# ============================================================ 3. VENTAS
titulo("3. VENDIDO  (CONCEPTOS_VENTA): unidades y monto")
print(f"   conceptos: {CONCEPTOS_VENTA}")
ventas = por_anio(movs_ok, CONCEPTOS_VENTA, con_venta=True)
tot_ventas = tabla_anual(
    "Ventas por año:", ventas,
    [('u', 'UNIDADES', 12), ('monto', 'VENTA $', 18), ('n', 'N_MOV', 10)],
)

print("\nVentas TOTALES por sucursal (unidades y monto):")
qs_ven_suc = (movs_ok.filter(concepto__in=CONCEPTOS_VENTA)
              .values('ProductoTalla__producto__sucursal__alias')
              .annotate(u=unidades_expr(), monto=monto_expr('precio')).order_by('-u'))
print(f"   {'SUCURSAL':<30}{'UNIDADES':>12}{'VENTA $':>18}")
print("   " + "-" * 57)
for f in qs_ven_suc:
    suc = f['ProductoTalla__producto__sucursal__alias'] or '(sin sucursal)'
    print(f"   {str(suc):<30}{f['u'] or 0:>12,}{f['monto'] or 0:>18,}")

# ============================================================ 4. STOCK ACTUAL
titulo("4. STOCK ACTUAL  (Producto_Talla.stock > 0)")
stock_total = pt_sk.filter(stock__gt=0).aggregate(s=Sum('stock', output_field=BI))['s'] or 0
skus_con_stock = pt_sk.filter(stock__gt=0).count()
print(f"Unidades en stock (Σ stock > 0): {stock_total:,}   en {skus_con_stock:,} SKUs")

print("\nStock por sucursal:")
qs_stock_suc = (pt_sk.filter(stock__gt=0)
                .values('producto__sucursal__alias')
                .annotate(u=Sum('stock', output_field=BI), skus=Count('id')).order_by('-u'))
print(f"   {'SUCURSAL':<30}{'STOCK':>12}{'SKUs':>10}")
print("   " + "-" * 52)
for f in qs_stock_suc:
    suc = f['producto__sucursal__alias'] or '(sin sucursal)'
    print(f"   {str(suc):<30}{f['u'] or 0:>12,}{f['skus']:>10,}")

print("\nStock por talla (todas las sucursales):")
qs_stock_talla = (pt_sk.filter(stock__gt=0)
                  .values('talla')
                  .annotate(u=Sum('stock', output_field=BI)).order_by('talla'))
stock_por_talla = {(f['talla'] or '?'): (f['u'] or 0) for f in qs_stock_talla}


def _talla_key(t):
    try:
        return (0, float(str(t).replace(',', '.')))
    except (ValueError, TypeError):
        return (1, str(t))


for t in sorted(stock_por_talla, key=_talla_key):
    print(f"   talla {str(t):<8} {stock_por_talla[t]:>10,}")

# ============================================================ 5. SELL-THROUGH + VELOCIDAD
titulo("5. SELL-THROUGH ANUAL  y  VELOCIDAD 90 DÍAS  (vs año anterior)")
print("Sell-through = unidades vendidas / unidades ingresadas (abastecimiento), por año:")
print(f"   {'AÑO':<6}{'VENDIDO':>12}{'INGRESADO':>12}{'SELL-THROUGH':>16}")
print("   " + "-" * 46)
anios = sorted(set(ventas) | set(abast))
for anio in anios:
    v = (ventas.get(anio) or {}).get('u') or 0
    i = (abast.get(anio) or {}).get('u') or 0
    st = f"{(100.0 * v / i):.1f}%" if i else "—"
    print(f"   {anio:<6}{v:>12,}{i:>12,}{st:>16}")
tv = tot_ventas['u']
ti = tot_abast['u']
st_tot = f"{(100.0 * tv / ti):.1f}%" if ti else "—"
print("   " + "-" * 46)
print(f"   {'TOTAL':<6}{tv:>12,}{ti:>12,}{st_tot:>16}")

hoy = timezone.localdate()
ini_90 = hoy - timedelta(days=90)
ini_90_ly = ini_90 - timedelta(days=365)
fin_90_ly = hoy - timedelta(days=365)


def vendido_en(desde, hasta):
    return (movs_ok.filter(concepto__in=CONCEPTOS_VENTA, fecha__gte=desde, fecha__lt=hasta)
            .aggregate(u=unidades_expr())['u'] or 0)


u_90 = vendido_en(ini_90, hoy)
u_90_ly = vendido_en(ini_90_ly, fin_90_ly)
print(f"\nVelocidad últimos 90 días  [{ini_90} .. {hoy})")
print(f"   Vendido 90d actual:          {u_90:>10,}   ({u_90/90:.2f} u/día)")
print(f"   Vendido 90d año anterior:    {u_90_ly:>10,}   ({u_90_ly/90:.2f} u/día)   "
      f"[{ini_90_ly} .. {fin_90_ly})")
if u_90_ly:
    print(f"   Variación YoY:               {(100.0*(u_90-u_90_ly)/u_90_ly):+.1f}%")
else:
    print("   Variación YoY:               — (sin base año anterior)")

# ============================================================ 6. CURVA DE TALLAS
titulo("6. CURVA DE TALLAS  —  vendida vs en stock  (tallas quebradas)")
qs_ven_talla = (movs_ok.filter(concepto__in=CONCEPTOS_VENTA)
                .values('talla')  # talla del movimiento? no existe -> usar PT
                if False else
                movs_ok.filter(concepto__in=CONCEPTOS_VENTA)
                .values('ProductoTalla__talla')
                .annotate(u=unidades_expr()).order_by())
venta_por_talla = {}
for f in qs_ven_talla:
    t = f['ProductoTalla__talla'] or '?'
    venta_por_talla[t] = venta_por_talla.get(t, 0) + (f['u'] or 0)

tot_v = sum(venta_por_talla.values()) or 1
tot_s = sum(stock_por_talla.values()) or 1
todas = sorted(set(venta_por_talla) | set(stock_por_talla), key=_talla_key)
print(f"   {'TALLA':<8}{'VEND u':>10}{'VEND %':>9}{'STK u':>10}{'STK %':>9}{'GAP(V%-S%)':>12}")
print("   " + "-" * 58)
for t in todas:
    vu = venta_por_talla.get(t, 0)
    su = stock_por_talla.get(t, 0)
    vp = 100.0 * vu / tot_v
    sp = 100.0 * su / tot_s
    gap = vp - sp
    flag = "  <== quebrada" if (vp >= 5 and su == 0) else (
        "  <= poca cob." if (gap >= 5 and sp < vp * 0.5) else "")
    print(f"   {str(t):<8}{vu:>10,}{vp:>8.1f}%{su:>10,}{sp:>8.1f}%{gap:>+11.1f}%{flag}")
print("   " + "-" * 58)
print(f"   {'TOTAL':<8}{tot_v if venta_por_talla else 0:>10,}{'100.0%':>9}"
      f"{tot_s if stock_por_talla else 0:>10,}{'100.0%':>9}")
print("   quebrada = tiene >=5% de la venta pero 0 stock; "
      "poca cob. = venta muy sobre-representada vs stock.")

titulo("FIN — análisis SKECHERS de solo lectura completado")
