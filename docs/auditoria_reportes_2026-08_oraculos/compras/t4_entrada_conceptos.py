# -*- coding: utf-8 -*-
# TANDA 4 — descomposicion de "entrada 2026" de api_rendimiento_compras.
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
from django.db.models import Sum, Count, F
from app.models import Movimientos_Producto

print('=== ENTRADA 2026 por concepto (regla api_rendimiento_compras) ===')
qs = (Movimientos_Producto.objects
      .filter(fecha__year=2026, estado='COMPLETADO',
              concepto__in=['RECEPCION_COMPRA', 'INGRESO_INICIAL', 'INGRESO_MANUAL'])
      .values('concepto')
      .annotate(n=Count('id'), uds=Sum('cantidad'), costo=Sum(F('costo') * F('cantidad'))))
for r in qs:
    print('  %-18s n=%-6s uds=%-8s costo=%s' % (r['concepto'], r['n'], r['uds'], r['costo']))

print()
print('=== INGRESO_INICIAL 2026 por mes (apertura migracion?) ===')
qs2 = (Movimientos_Producto.objects
       .filter(fecha__year=2026, estado='COMPLETADO', concepto='INGRESO_INICIAL')
       .values(mes=F('fecha__month'))
       .annotate(uds=Sum('cantidad'), costo=Sum(F('costo') * F('cantidad')))
       .order_by('mes'))
for r in qs2:
    print('  mes=%-2s uds=%-8s costo=%s' % (r['mes'], r['uds'], r['costo']))
print('FIN T4')
