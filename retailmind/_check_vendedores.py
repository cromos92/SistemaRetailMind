import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'retailmind.settings'
django.setup()

from app.models import Dte, Dte_Detalle_Pago, Vendedor
from django.db.models import Count

# Verificar vendedores cruzados: vendedores asignados a DTEs de sucursales donde no trabajan
print('=== VERIFICACION: Vendedores asignados a sucursales correctas ===')
from django.db.models import F

# Ver vendedores por empresa
print('\n--- Vendedores por empresa ---')
for row in Vendedor.objects.values('empresa__razon_social').annotate(c=Count('id')).order_by('-c'):
    e = row['empresa__razon_social'] or '(Sin empresa)'
    print(f'  {e}: {row["c"]}')

# Muestra de DTEs con vendedor: verificar si el vendedor corresponde a la sucursal
print('\n--- Muestra DTEs venta (ultimos, con vendedor): vendedor vs sucursal ---')
muestra = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    vendedor__isnull=False,
    fecha_emision__gte='2024-01-01'
).select_related('vendedor', 'sucursal', 'vendedor__empresa', 'sucursal__empresa').order_by('-fecha_emision')[:30]

for d in muestra:
    vend_empresa = d.vendedor.empresa.razon_social[:20] if d.vendedor and d.vendedor.empresa else '?'
    suc_empresa = d.sucursal.empresa.razon_social[:20] if d.sucursal and d.sucursal.empresa else '?'
    match = 'OK' if vend_empresa == suc_empresa else 'CRUZADO!'
    print(f'  DTE#{d.numero_documento} | suc={d.sucursal.alias if d.sucursal else "?"} ({suc_empresa}) | vend={d.vendedor.nombre if d.vendedor else "?"} ({vend_empresa}) | {match}')

# Contar cuantos DTEs tienen vendedor de empresa diferente a la sucursal
print('\n--- Conteo de vendedores cruzados entre empresas ---')
from django.db.models import Q
total_con_ambos = Dte.objects.filter(
    vendedor__isnull=False,
    sucursal__isnull=False,
    vendedor__empresa__isnull=False,
    sucursal__empresa__isnull=False,
).count()

cruzados = Dte.objects.filter(
    vendedor__isnull=False,
    sucursal__isnull=False,
    vendedor__empresa__isnull=False,
    sucursal__empresa__isnull=False,
).exclude(
    vendedor__empresa=F('sucursal__empresa')
).count()
print(f'  DTEs con vendedor+sucursal+empresa: {total_con_ambos:,}')
print(f'  Vendedor de empresa DIFERENTE a sucursal: {cruzados:,}')
if total_con_ambos > 0:
    print(f'  Porcentaje cruzado: {cruzados/total_con_ambos*100:.1f}%')
