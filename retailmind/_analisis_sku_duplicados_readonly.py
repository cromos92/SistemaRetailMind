# -*- coding: utf-8 -*-
# ANALISIS READ-ONLY: SKUs duplicados dentro de la MISMA sucursal.
# Solo hace SELECT/agregaciones. No escribe nada.
from collections import defaultdict
from django.db.models import Count, Sum, Min, Max
from app.models import Producto_Talla

print("=" * 110)
print("ANALISIS DE SKUs DUPLICADOS DENTRO DE LA MISMA SUCURSAL (read-only)")
print("=" * 110)

total_tallas = Producto_Talla.objects.count()
n_null = Producto_Talla.objects.filter(sku__isnull=True).count()
n_zero = Producto_Talla.objects.filter(sku=0).count()
print(f"Total filas Producto_Talla: {total_tallas}  |  sku NULL: {n_null}  |  sku=0: {n_zero} (excluidos del analisis)")

base = Producto_Talla.objects.exclude(sku__isnull=True).exclude(sku=0)

# Referencia: SKUs repetidos a nivel GLOBAL (entre sucursales es esperado:
# el mismo codigo de barras vive en la ficha de cada bodega)
dup_global = base.values('sku').annotate(n=Count('id')).filter(n__gt=1).count()
print(f"SKUs repetidos a nivel global (todas las sucursales juntas): {dup_global} (referencia, no necesariamente problema)")

# El problema real: mismo SKU repetido DENTRO de una sucursal
dups = (base
        .values('producto__sucursal_id', 'producto__sucursal__alias', 'sku')
        .annotate(
            n_tallas=Count('id'),
            n_fichas=Count('producto_id', distinct=True),
            n_codigos=Count('producto__articulo', distinct=True),
            n_precios=Count('producto__precioventa', distinct=True),
            stock_total=Sum('stock'),
            precio_min=Min('producto__precioventa'),
            precio_max=Max('producto__precioventa'),
        )
        .filter(n_tallas__gt=1)
        .order_by())

resumen = defaultdict(lambda: dict(skus=0, tallas=0, multi_ficha=0, multi_codigo=0,
                                   precio_dist=0, precio_dist_stock=0))
peores = []
for d in dups.iterator(chunk_size=5000):
    al = d['producto__sucursal__alias'] or '(sin sucursal)'
    r = resumen[al]
    r['skus'] += 1
    r['tallas'] += d['n_tallas']
    if d['n_fichas'] > 1:
        r['multi_ficha'] += 1
    if d['n_codigos'] > 1:
        r['multi_codigo'] += 1
    if d['n_precios'] > 1:
        r['precio_dist'] += 1
        if (d['stock_total'] or 0) > 0:
            r['precio_dist_stock'] += 1
            peores.append(d)

print()
print("Por sucursal (SKU repetido dentro de la sucursal):")
hdr = (f"{'SUCURSAL':10} {'SKUs dup':>9} {'tallas':>8} {'en >1 ficha':>12} "
       f"{'codigos distintos':>18} {'precios distintos':>18} {'y CON stock':>12}")
print(hdr)
print("-" * len(hdr))
tot = dict(skus=0, tallas=0, multi_ficha=0, multi_codigo=0, precio_dist=0, precio_dist_stock=0)
for al in sorted(resumen, key=lambda a: -resumen[a]['skus']):
    r = resumen[al]
    for k in tot:
        tot[k] += r[k]
    print(f"{al:10} {r['skus']:>9} {r['tallas']:>8} {r['multi_ficha']:>12} "
          f"{r['multi_codigo']:>18} {r['precio_dist']:>18} {r['precio_dist_stock']:>12}")
print("-" * len(hdr))
print(f"{'TOTAL':10} {tot['skus']:>9} {tot['tallas']:>8} {tot['multi_ficha']:>12} "
      f"{tot['multi_codigo']:>18} {tot['precio_dist']:>18} {tot['precio_dist_stock']:>12}")

peores.sort(key=lambda d: ((d['precio_max'] or 0) - (d['precio_min'] or 0)), reverse=True)
print()
print("TOP 15 MAS PELIGROSOS: mismo SKU + misma sucursal + precios distintos + CON stock")
hdr2 = (f"{'SUCURSAL':10} {'SKU':>13} {'tallas':>7} {'fichas':>7} {'stock':>6} "
        f"{'precio min':>12} {'precio max':>12} {'diferencia':>12}")
print(hdr2)
print("-" * len(hdr2))
for d in peores[:15]:
    dif = (d['precio_max'] or 0) - (d['precio_min'] or 0)
    print(f"{(d['producto__sucursal__alias'] or '-'):10} {d['sku']:>13} {d['n_tallas']:>7} "
          f"{d['n_fichas']:>7} {(d['stock_total'] or 0):>6} {(d['precio_min'] or 0):>12} "
          f"{(d['precio_max'] or 0):>12} {dif:>12}")

print()
print("Caso concreto SKU 4832116 (el del BOX ELITE 2):")
for pt in Producto_Talla.objects.filter(sku=4832116).select_related(
        'producto__sucursal', 'producto__atributo2'):
    p = pt.producto
    color = p.atributo2.valor if p.atributo2 else '-'
    suc = p.sucursal.alias if p.sucursal else '-'
    print(f"  talla_id {pt.id} | talla {pt.talla} | stock {pt.stock} | ficha {p.id} "
          f"{p.articulo} {color} | {suc} | precio ${p.precioventa}")
print()
print("FIN (read-only, no se modifico nada)")
