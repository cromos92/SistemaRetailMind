import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import Dte, Dte_Detalle_Pago, Sucursal
from django.db.models import Count, Sum, Exists, OuterRef, Q

# Últimas fechas con DTEs de venta
print('=== ULTIMAS 10 FECHAS con ventas ===')
fechas = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    estado_dte='EMITIDO'
).values('fecha_emision').annotate(
    total_dtes=Count('id'),
    con_pago=Count('id', filter=Exists(
        Dte_Detalle_Pago.objects.filter(dte_id=OuterRef('pk'))
    ))
).order_by('-fecha_emision')[:10]

for f in fechas:
    sin = f['total_dtes'] - f['con_pago']
    print(f'  {f["fecha_emision"]} | {f["total_dtes"]:>5} DTEs | {f["con_pago"]:>5} con pago | {sin:>5} SIN pago')

# Para las últimas 3 fechas, ver desglose por metodo_pago
print('\n=== DESGLOSE POR METODO_PAGO (últimas 3 fechas) ===')
for f in list(fechas)[:3]:
    fecha = f['fecha_emision']
    print(f'\n  Fecha: {fecha}')
    
    dtes_ids = Dte.objects.filter(
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        estado_dte='EMITIDO',
        fecha_emision=fecha
    ).values_list('id', flat=True)
    
    pagos = Dte_Detalle_Pago.objects.filter(
        dte_id__in=dtes_ids
    ).values('metodo_pago').annotate(
        total=Sum('monto'),
        cnt=Count('id')
    ).order_by('-total')
    
    if not pagos:
        print('    *** SIN PAGOS ***')
    for p in pagos:
        print(f'    {p["metodo_pago"]:25s}: ${p["total"]:>12,} ({p["cnt"]} pagos)')

# Verificar si hay DTEs recientes SIN ningún pago
print('\n=== DTEs VENTA sin pago - por fecha (top 10 fechas) ===')
from django.db.models import Subquery
sin_pago = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    estado_dte='EMITIDO'
).exclude(
    Exists(Dte_Detalle_Pago.objects.filter(dte_id=OuterRef('pk')))
).values('fecha_emision').annotate(c=Count('id')).order_by('-fecha_emision')[:10]

for f in sin_pago:
    print(f'  {f["fecha_emision"]}: {f["c"]} DTEs sin pago')
