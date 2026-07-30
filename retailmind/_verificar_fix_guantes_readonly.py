# -*- coding: utf-8 -*-
"""READ-ONLY: verifica que el fix del caso guantes/zapatillas quedo aplicado."""
from app.models import Producto, Producto_Talla, LoteProducto, HistorialCambioPrecio

print("=== FICHAS CON CODIGO 009283623* (post-fix) ===")
for p in (Producto.objects
          .filter(articulo__istartswith="009283623")
          .select_related("sucursal", "categoria", "atributo2")
          .order_by("articulo", "sucursal__alias")):
    stock = sum(pt.stock or 0 for pt in Producto_Talla.objects.filter(producto=p))
    print(f"  ficha {p.id:7} | {p.sucursal.alias if p.sucursal else '-':6} | codigo {p.articulo:14} | "
          f"{(p.categoria.nombre if p.categoria else '-'):12} | "
          f"{(p.atributo2.valor if p.atributo2 else '-'):8} | ${p.precioventa:8} | stock {stock}")

print()
print("=== ZAPATILLA ROJO NICK2 (ficha 136745): precio y lotes ===")
zap = Producto.objects.get(id=136745)
print(f"  precioventa ficha: ${zap.precioventa}  (esperado: $109990)")
lotes = LoteProducto.objects.filter(
    producto_talla__producto_id=136745, cantidad_disponible__gt=0, activo=True
).values_list("precio_venta_unitario", flat=True)
print(f"  lotes activos: {len(lotes)} | precios: {sorted(set(lotes))}  (esperado: [109990])")

print()
print("=== GUANTES (ficha 138379): precio intacto ===")
g = Producto.objects.get(id=138379)
print(f"  codigo: {g.articulo}  | precioventa: ${g.precioventa}  (esperado: $44990)")

print()
print("=== ULTIMOS CAMBIOS DE PRECIO DE LA ZAPATILLA NICK2 ===")
for h in (HistorialCambioPrecio.objects.filter(producto_id=136745)
          .select_related("usuario").order_by("-fecha_cambio")[:4]):
    print(f"  {h.fecha_cambio} | ${h.precio_anterior} -> ${h.precio_nuevo} | {h.usuario} | {(h.motivo or '')[:90]}")

print()
print("=== COLISION RESUELTA? fichas que comparten codigo con la zapatilla ROJO ===")
colisiones = (Producto.objects
              .filter(articulo__iexact="009283623")
              .exclude(categoria__nombre__icontains="zapatilla")
              .select_related("sucursal", "categoria"))
if not colisiones.exists():
    print("  OK: ningun producto de otra categoria usa ya el codigo 009283623")
for p in colisiones:
    print(f"  PENDIENTE: ficha {p.id} {p.sucursal.alias if p.sucursal else '-'} "
          f"{p.categoria.nombre if p.categoria else '-'}")
print()
print("FIN (solo lectura)")
