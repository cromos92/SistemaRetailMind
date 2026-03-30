import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models.inventario import Movimientos_Producto
from app.models.ventas import Ticket, Ticket_Productos
from app.models.catalogo import Producto, Producto_Talla
from app.models.organizacion import Sucursal
from django.db.models import Count, Sum, Q

print("=" * 60)
print("DIAGNÓSTICO DE DATOS PARA PREDICCIÓN")
print("=" * 60)

print("\n--- SUCURSALES ---")
for s in Sucursal.objects.all():
    prods = Producto.objects.filter(sucursal=s).count()
    print(f"  {s.alias or s.nombre} (tipo: {s.tipo_sucursal}): {prods} productos")

print("\n--- MOVIMIENTOS POR CONCEPTO ---")
movs = Movimientos_Producto.objects.values('concepto').annotate(
    total=Count('id'),
    unidades=Sum('cantidad'),
).order_by('-total')
for m in movs:
    print(f"  {m['concepto']}: {m['total']} movs, {m['unidades']} unidades")
print(f"  TOTAL: {Movimientos_Producto.objects.count()}")

print("\n--- MOVIMIENTOS POR TIPO ---")
tipos = Movimientos_Producto.objects.values('tipo_movimiento').annotate(
    total=Count('id')).order_by('-total')
for t in tipos:
    print(f"  {t['tipo_movimiento']}: {t['total']}")

print("\n--- TICKETS POR ESTADO ---")
tickets = Ticket.objects.values('estado').annotate(total=Count('id')).order_by('-total')
for t in tickets:
    print(f"  {t['estado']}: {t['total']}")

print("\n--- VENTAS PÚBLICAS (conceptos de venta en movimientos) ---")
ventas_mov = Movimientos_Producto.objects.filter(
    concepto__in=['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA']
).aggregate(total=Count('id'), unidades=Sum('cantidad'))
print(f"  Movimientos de venta: {ventas_mov['total']}, unidades: {ventas_mov['unidades']}")

print("\n--- TRASPASOS (venta interna) ---")
traspasos = Movimientos_Producto.objects.filter(
    concepto__in=['TRASPASO_SUCURSAL', 'TRASPASO_SALIDA', 'TRASPASO_ENTRADA']
).aggregate(total=Count('id'), unidades=Sum('cantidad'))
print(f"  Movimientos traspaso: {traspasos['total']}, unidades: {traspasos['unidades']}")

print("\n--- MUESTRA DE CONCEPTOS DISTINTOS ---")
conceptos = list(Movimientos_Producto.objects.values_list('concepto', flat=True).distinct())
print(f"  {conceptos}")
