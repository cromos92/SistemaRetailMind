import os, sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
import django
django.setup()

from app.models import Movimientos_Producto, Dte, Dte_Productos
from django.db.models import Count, Sum

CONCEPTOS_ENTRADA  = ['RECEPCION_COMPRA', 'INGRESO_INICIAL']
CONCEPTOS_DESPACHO = ['TRASPASO_SUCURSAL', 'TRASPASO_SALIDA']
CONCEPTOS_VENTA    = ['VENTA_PUBLICO', 'VENTA_MAYORISTA']

print("=== TODOS LOS CONCEPTOS (todos los anios) ===")
todos = (Movimientos_Producto.objects
         .values('concepto')
         .annotate(total=Count('id'), uds=Sum('cantidad'))
         .order_by('-total'))
for t in todos:
    cat = "(sin mapeo en Rendimiento)"
    if t['concepto'] in CONCEPTOS_ENTRADA:    cat = "ENTRADA"
    elif t['concepto'] in CONCEPTOS_DESPACHO: cat = "DESPACHO"
    elif t['concepto'] in CONCEPTOS_VENTA:    cat = "VENTA"
    print("  {:35s}  {:8d} movs  {:10} uds  [{}]".format(
        t['concepto'], t['total'], str(t['uds'] or 0), cat))

print()
print("=== TRASPASO_SUCURSAL 2026 - Origen->Destino (top10) ===")
suc = (Movimientos_Producto.objects
       .filter(fecha__year=2026, estado='COMPLETADO', concepto='TRASPASO_SUCURSAL')
       .values('sucursal_origen__alias', 'sucursal_destino__alias')
       .annotate(total=Count('id'), uds=Sum('cantidad'))
       .order_by('-uds')[:10])
for s in suc:
    print("  {:15s} -> {:15s}  movs={:5d}  uds={}".format(
        str(s['sucursal_origen__alias'] or '?'),
        str(s['sucursal_destino__alias'] or '?'),
        s['total'], s['uds']))

print()
print("=== AJUSTE_INVENTARIO 2026 (muestra) ===")
aj = (Movimientos_Producto.objects
      .filter(fecha__year=2026, estado='COMPLETADO', concepto='AJUSTE_INVENTARIO')
      .aggregate(total=Count('id'), uds=Sum('cantidad')))
print("  AJUSTE_INVENTARIO 2026: movs={}, uds={}".format(aj['total'], aj['uds']))

print()
print("=== Campos de Movimientos_Producto ===")
fields = [f.name for f in Movimientos_Producto._meta.get_fields()]
print("  ", fields)
