# -*- coding: utf-8 -*-
"""READ-ONLY: la ficha 138379 duplica algun guante ROJO que YA existia con codigo propio?"""
from app.models import Producto, Producto_Talla, Movimientos_Producto

print("=" * 105)
print("TODAS las fichas de GUANTES ROJO (cualquier marca) con sus tallas/SKUs")
print("=" * 105)
fichas = (Producto.objects
          .filter(categoria__nombre__icontains='guante', atributo2__valor__icontains='rojo')
          .select_related('sucursal', 'atributo1', 'atributo2', 'atributo3')
          .order_by('articulo', 'sucursal__alias'))
for p in fichas:
    tallas = list(Producto_Talla.objects.filter(producto=p).order_by('talla'))
    stock = sum(pt.stock or 0 for pt in tallas)
    n_movs = Movimientos_Producto.objects.filter(ProductoTalla__producto=p).count()
    marca = p.atributo1.valor if p.atributo1 else '-'
    gen = p.atributo3.valor if p.atributo3 else '-'
    print(f"\nficha {p.id:7} | {p.sucursal.alias if p.sucursal else '-':6} | codigo {p.articulo:16} | "
          f"{marca:10} | {gen:8} | ${p.precioventa:8} | stock {stock:3} | movs {n_movs} | "
          f"creada {p.fecha_creacion.date() if p.fecha_creacion else '-'}")
    print(f"           tallas: " + ", ".join(f"{pt.talla}(sku {pt.sku}, st {pt.stock})" for pt in tallas))

print()
print("=" * 105)
print("FAMILIA DE CODIGOS QUE EMPIEZAN CON 009283623 (para entender la convencion)")
print("=" * 105)
vistos = {}
for p in (Producto.objects
          .filter(articulo__istartswith='009283623')
          .select_related('categoria', 'atributo2')):
    key = (p.articulo, p.categoria.nombre if p.categoria else '-',
           p.atributo2.valor if p.atributo2 else '-')
    vistos[key] = vistos.get(key, 0) + 1
for (art, cat, col), n in sorted(vistos.items()):
    print(f"  codigo {art:16} | len {len(art):2} | {cat:22} | {col:10} | {n} ficha(s)")

print()
print("FIN (solo lectura)")
