# -*- coding: utf-8 -*-
"""READ-ONLY: la reversa pre-recepcion del DTE 17058 dejo drift stock-plano vs lotes FIFO?

La rama pre-recepcion de ajustar_dte_emisor_api hace
    Producto_Talla.objects.filter(id=...).update(stock=F('stock') + diferencia)
sin tocar LoteProducto ni crear un movimiento de kardex propio (reescribe el
TRASPASO_SALIDA original). Este script mide si eso produjo descuadre.

NO ESCRIBE NADA.
"""
from django.db.models import Sum
from app.models import Producto_Talla, LoteProducto, Movimientos_Producto

# Producto 1382915-001 en EDEL (los 4 SKUs que tocó la NC 967)
PT_EDEL = {53792: 'S', 53791: 'M', 53788: 'L', 53795: 'XL'}
# Y los mismos en NICK2 (destino)
PT_NICK2 = {53793: 'S', 53790: 'M', 53789: 'L', 53794: 'XL'}

for titulo, mapa in (('EDEL (origen)', PT_EDEL), ('NICK2 (destino)', PT_NICK2)):
    print("=" * 100)
    print(titulo)
    print("=" * 100)
    for pt_id, talla in mapa.items():
        try:
            pt = Producto_Talla.objects.select_related('producto__sucursal').get(id=pt_id)
        except Producto_Talla.DoesNotExist:
            print(f"  pt {pt_id}: NO EXISTE")
            continue
        lotes = list(LoteProducto.objects.filter(producto_talla=pt).order_by('id'))
        suma_lotes = sum(int(getattr(l, 'cantidad_disponible', 0) or 0) for l in lotes)
        drift = int(pt.stock or 0) - suma_lotes
        flag = '   <<< DRIFT' if drift else ''
        print(f"  pt={pt_id} talla={talla:>3} sku={pt.sku} | stock_plano={pt.stock} | "
              f"lotes={len(lotes)} | disponible_en_lotes={suma_lotes} | drift={drift}{flag}")
        for l in lotes:
            print(f"       lote={l.id} | ingreso={getattr(l, 'fecha_ingreso', '-')} | "
                  f"inicial={getattr(l, 'cantidad_inicial', '-')} | "
                  f"disp={getattr(l, 'cantidad_disponible', '-')} | costo={getattr(l, 'costo_unitario', '-')}")

print()
print("=" * 100)
print("Kardex completo de los 4 SKUs en EDEL (ultimos 12 movs) - hay rastro de la devolucion por NC?")
print("=" * 100)
for pt_id, talla in PT_EDEL.items():
    print(f"\n  --- pt {pt_id} (talla {talla}) ---")
    for m in (
        Movimientos_Producto.objects
        .filter(ProductoTalla_id=pt_id)
        .select_related('dte')
        .order_by('-fecha', '-id')[:12]
    ):
        print(f"    mov={m.id} | {m.fecha} | {m.concepto} | {m.tipo_movimiento} | "
              f"cant={m.cantidad} | estado={m.estado} | "
              f"dte={m.dte.numero_documento if m.dte else '-'} | "
              f"resp={m.responsable} | obs={(m.observaciones or '')[:90]}")

print()
print("FIN (read-only)")
