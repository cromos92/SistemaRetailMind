"""
Verificar los DTEs recién importados
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from app.models import Dte, Sucursal
from datetime import datetime, timedelta

print("="*70)
print("VERIFICAR DTEs IMPORTADOS")
print("="*70)

# Últimos DTEs creados (por ID más alto)
ultimos = Dte.objects.order_by('-id')[:10]
print("\n1. Últimos 10 DTEs creados (por ID):")
for dte in ultimos:
    suc = dte.sucursal.alias if dte.sucursal else 'SIN SUC'
    vend = dte.vendedor.nombre if dte.vendedor else 'SIN VEND'
    print(f"   ID={dte.id}, num={dte.numero_documento}, tipo={dte.tipo_documento}, "
          f"suc={suc}, estado={dte.estado_dte}, trans={dte.tipo_transaccion}")

# Sucursal NICK1
print("\n2. Sucursal NICK1:")
nick1 = Sucursal.objects.filter(alias='NICK1').first()
if nick1:
    print(f"   ID={nick1.id}, alias={nick1.alias}, direccion={nick1.direccion}")
    
    # DTEs de NICK1 recientes
    dtes_nick1 = Dte.objects.filter(sucursal=nick1).order_by('-id')[:10]
    print(f"\n3. Últimos 10 DTEs de NICK1:")
    for dte in dtes_nick1:
        vend = f"{dte.vendedor.codigo_vendedor}-{dte.vendedor.nombre}" if dte.vendedor else 'SIN'
        print(f"   ID={dte.id}, num={dte.numero_documento}, tipo={dte.tipo_documento}, "
              f"fecha={dte.fecha_emision}, estado={dte.estado_dte}, vend={vend}")
else:
    print("   NO ENCONTRADA")

# Verificar si hay DTEs con estado_dte='EMITIDO' o 'PAGADO'
print("\n4. Estados de DTEs (últimos 500):")
from django.db.models import Count
estados = Dte.objects.order_by('-id')[:500].values('estado_dte').annotate(total=Count('id'))
for e in estados:
    print(f"   {e['estado_dte']}: {e['total']}")

# Verificar tipo_transaccion
print("\n5. Tipos de transacción (últimos 500):")
tipos = Dte.objects.order_by('-id')[:500].values('tipo_transaccion').annotate(total=Count('id'))
for t in tipos:
    print(f"   {t['tipo_transaccion']}: {t['total']}")

# Verificar filtros de la vista de documentos
print("\n6. DTEs que deberían verse en la vista (NICK1, VENTA/VENTA_PUBLICO):")
if nick1:
    dtes_vista = Dte.objects.filter(
        sucursal=nick1,
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
    ).count()
    print(f"   Total: {dtes_vista}")
    
    # Desglose por estado
    for estado in ['EMITIDO', 'PAGADO', 'PENDIENTE', 'ANULADO']:
        count = Dte.objects.filter(
            sucursal=nick1,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            estado_dte=estado
        ).count()
        print(f"   - {estado}: {count}")

print("\n" + "="*70)
