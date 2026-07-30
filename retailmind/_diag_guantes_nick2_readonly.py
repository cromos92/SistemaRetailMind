# -*- coding: utf-8 -*-
"""READ-ONLY: donde estan hoy los SKUs de guantes y en que estado va el traspaso 17049."""
from app.models import Producto_Talla, Dte, Movimientos_Producto

SKUS_GUANTES = [4837256, 4837257, 4837264]

print("=" * 100)
print("FICHAS QUE TIENEN LOS SKUs DE GUANTES (en cualquier sucursal)")
print("=" * 100)
for pt in (Producto_Talla.objects
           .filter(sku__in=SKUS_GUANTES)
           .select_related('producto__sucursal', 'producto__categoria', 'producto__atributo2')
           .order_by('sku', 'producto__sucursal__alias')):
    p = pt.producto
    print(f"SKU {pt.sku} | talla {str(pt.talla):5} | stock {pt.stock:3} | ficha {p.id} | "
          f"{p.sucursal.alias if p.sucursal else '-':6} | codigo {p.articulo:14} | "
          f"{p.categoria.nombre if p.categoria else '-':12} | ${p.precioventa}")

print()
print("=" * 100)
print("ESTADO DEL TRASPASO DTE 17049 (los guantes que salieron de EDEL a NICK2)")
print("=" * 100)
for d in Dte.objects.filter(numero_documento=17049).select_related('sucursal', 'emisor', 'receptor'):
    print(f"DTE id={d.id} nro={d.numero_documento} | {d.tipo_documento} | {d.tipo_transaccion} | "
          f"estado={d.estado_dte} | emitido {d.fecha_emision} | recepcion {d.fecha_recepcion} | "
          f"origen {d.sucursal.alias if d.sucursal else '-'} | emisor {d.emisor.nombre if d.emisor else '-'} "
          f"-> receptor {d.receptor.nombre if d.receptor else '-'}")
    destino = (Movimientos_Producto.objects
               .filter(dte=d, concepto='TRASPASO_SALIDA', sucursal_destino__isnull=False)
               .select_related('sucursal_destino').first())
    print(f"   destino segun movimientos: {destino.sucursal_destino.alias if destino else 'SIN DESTINO'}")
    print(f"   lineas del DTE: {d.dte_productos.count()} | unidades cabecera: {d.unidades_productos}")
    for l in d.dte_productos.select_related('productoTalla__producto__categoria').all():
        pt = l.productoTalla
        cat = pt.producto.categoria.nombre if (pt and pt.producto and pt.producto.categoria) else '-'
        print(f"      SKU {pt.sku if pt else '-'} | talla {pt.talla if pt else '-'} | "
              f"cant {l.stock} | ${l.precio} | {cat}")

print()
print("FIN (read-only, no se modifico nada)")
