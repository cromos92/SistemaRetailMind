import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import Dte, Dte_Detalle_Pago, Sucursal
from django.db.models import Count, Sum

print('=== ESTADO DTE de las ventas ===')
for row in Dte.objects.filter(tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']).values('estado_dte').annotate(c=Count('id')).order_by('-c'):
    print(f'  {row["estado_dte"]:20s}: {row["c"]:>10,}')

print('\n=== DTEs VENTA con fecha_emision NULL ===')
nulos = Dte.objects.filter(tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'], fecha_emision__isnull=True).count()
print(f'  {nulos:,}')

print('\n=== DTEs VENTA con sucursal NULL ===')
sin_suc = Dte.objects.filter(tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'], sucursal__isnull=True).count()
print(f'  {sin_suc:,}')

# Probar cuadratura para una fecha real
print('\n=== PRUEBA DE CUADRATURA: buscar fechas con datos ===')
from django.db.models.functions import TruncDate
from django.db.models import F

# Fechas con más ventas
fechas = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    estado_dte='EMITIDO',
    sucursal__isnull=False,
    fecha_emision__isnull=False
).values('fecha_emision').annotate(c=Count('id')).order_by('-c')[:5]

for f in fechas:
    print(f'  {f["fecha_emision"]}: {f["c"]:,} DTEs')

# Para la fecha con más datos, simular cuadratura por sucursal
if fechas:
    mejor_fecha = fechas[0]['fecha_emision']
    print(f'\n=== CUADRATURA SIMULADA para {mejor_fecha} ===')
    for suc in Sucursal.objects.all():
        dtes = Dte.objects.filter(
            sucursal=suc,
            fecha_emision=mejor_fecha,
            estado_dte='EMITIDO',
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
        )
        cnt = dtes.count()
        if cnt == 0:
            continue
        
        pagos = Dte_Detalle_Pago.objects.filter(
            dte__in=dtes
        ).values('metodo_pago').annotate(
            total=Sum('monto'),
            cantidad=Count('id')
        ).order_by('-total')
        
        print(f'\n  Sucursal: {suc.alias} ({cnt} DTEs)')
        for p in pagos:
            print(f'    {p["metodo_pago"]:25s}: ${p["total"]:>12,} ({p["cantidad"]} pagos)')
        
        if not pagos:
            print(f'    *** SIN PAGOS VINCULADOS ***')
            muestra = dtes[:3]
            for d in muestra:
                pagos_dte = Dte_Detalle_Pago.objects.filter(dte=d).count()
                print(f'    DTE#{d.numero_documento} monto=${d.monto_con_iva} pagos={pagos_dte}')
