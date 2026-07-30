# -*- coding: utf-8 -*-
"""READ-ONLY: que se movio con la ficha GUANTES 138379 y con las ZAPATILLAS ROJO.

Responde: se despacho/vendio algo como guante? es seguro borrar la ficha?
que SKUs y cantidades hay que re-etiquetar?
"""
from app.models import (
    Producto, Producto_Talla, Movimientos_Producto, Dte_Productos,
    Ticket_Productos, LoteProducto,
)

ID_GUANTES = 138379
IDS_ZAPATILLAS_ROJO = [136744, 136745]


def _dump_fichas(ids, titulo):
    print("=" * 100)
    print(titulo)
    print("=" * 100)
    for pid in ids:
        try:
            p = Producto.objects.select_related('sucursal', 'categoria', 'atributo2').get(id=pid)
        except Producto.DoesNotExist:
            print(f"ficha {pid}: NO EXISTE")
            continue
        print(f"ficha {p.id} | {p.sucursal.alias if p.sucursal else '-'} | codigo {p.articulo} | "
              f"{p.categoria.nombre if p.categoria else '-'} | "
              f"{p.atributo2.valor if p.atributo2 else '-'} | ${p.precioventa} | creada {p.fecha_creacion}")
        for pt in Producto_Talla.objects.filter(producto_id=pid).order_by('talla'):
            n_mov = Movimientos_Producto.objects.filter(ProductoTalla=pt).count()
            n_dte = Dte_Productos.objects.filter(productoTalla=pt).count()
            n_tkt = Ticket_Productos.objects.filter(ProductoTalla=pt).count()
            n_lot = LoteProducto.objects.filter(producto_talla=pt).count()
            print(f"    talla {str(pt.talla):5} | SKU {pt.sku} | stock {pt.stock:3} | "
                  f"movs {n_mov} | lineas DTE {n_dte} | ventas {n_tkt} | lotes {n_lot}")


_dump_fichas([ID_GUANTES], "FICHA GUANTES (la creada el 25-jul con el codigo de la zapatilla)")

print()
print("--- TODOS los movimientos de los SKUs de la ficha GUANTES ---")
movs_g = (Movimientos_Producto.objects
          .filter(ProductoTalla__producto_id=ID_GUANTES)
          .select_related('ProductoTalla', 'sucursal_origen', 'sucursal_destino', 'dte')
          .order_by('fecha', 'hora'))
if not movs_g.exists():
    print("  (NINGUNO: la ficha guantes nunca movio stock -> nada se despacho como guante)")
for m in movs_g:
    print(f"  {m.fecha} | SKU {m.ProductoTalla.sku} | {m.tipo_movimiento:7} | {m.concepto:28} | "
          f"cant {m.cantidad:4} | {m.sucursal_origen.alias if m.sucursal_origen else '-'} -> "
          f"{m.sucursal_destino.alias if m.sucursal_destino else '-'} | "
          f"DTE {m.dte.numero_documento if m.dte else '-'} | {m.responsable}")

print()
print("--- Lineas de DTE de la ficha GUANTES (despachos/facturas) ---")
lineas_g = (Dte_Productos.objects
            .filter(productoTalla__producto_id=ID_GUANTES)
            .select_related('dte', 'productoTalla'))
if not lineas_g.exists():
    print("  (NINGUNA)")
for l in lineas_g:
    d = l.dte
    print(f"  DTE {d.numero_documento if d else '-'} ({d.tipo_documento if d else '-'}, "
          f"{d.fecha_emision if d else '-'}) | SKU {l.productoTalla.sku if l.productoTalla else '-'} | "
          f"cant {l.stock} | ${l.precio} | activo={l.activo}")

_dump_fichas(IDS_ZAPATILLAS_ROJO, "FICHAS ZAPATILLAS ROJO (mismo codigo)")

print()
print("--- Movimientos de las ZAPATILLAS ROJO desde el 2026-07-01 (ver si salio algo como guante) ---")
movs_z = (Movimientos_Producto.objects
          .filter(ProductoTalla__producto_id__in=IDS_ZAPATILLAS_ROJO, fecha__gte='2026-07-01')
          .select_related('ProductoTalla', 'sucursal_origen', 'sucursal_destino', 'dte')
          .order_by('fecha', 'hora'))
if not movs_z.exists():
    print("  (ninguno en julio)")
for m in movs_z:
    print(f"  {m.fecha} | SKU {m.ProductoTalla.sku} | {m.tipo_movimiento:7} | {m.concepto:28} | "
          f"cant {m.cantidad:4} | {m.sucursal_origen.alias if m.sucursal_origen else '-'} -> "
          f"{m.sucursal_destino.alias if m.sucursal_destino else '-'} | {m.responsable}")

print()
print("=" * 100)
print("OTRAS FICHAS DE GUANTES EVERLAST (para ver que codigo usan los guantes de verdad)")
print("=" * 100)
otros = (Producto.objects
         .filter(categoria__nombre__icontains='guante', atributo1__valor__icontains='everlast')
         .exclude(id=ID_GUANTES)
         .select_related('sucursal', 'atributo2', 'categoria')
         .order_by('articulo')[:25])
if not otros:
    print("  (no hay otras fichas de guantes EVERLAST)")
for p in otros:
    stock = sum(pt.stock or 0 for pt in Producto_Talla.objects.filter(producto=p))
    print(f"  ficha {p.id} | {p.sucursal.alias if p.sucursal else '-':6} | codigo {p.articulo:16} | "
          f"{p.atributo2.valor if p.atributo2 else '-':10} | ${p.precioventa:8} | stock {stock}")

print()
print("FIN (read-only, no se modifico nada)")
