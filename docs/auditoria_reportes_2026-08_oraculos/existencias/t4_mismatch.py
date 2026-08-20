# -*- coding: utf-8 -*-
# Tanda 4: caracterizar movimientos que la reversion historica no puede revertir
import os, sys
from datetime import date

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
import django
django.setup()

from django.db.models import Sum, Count, Q, F
from django.db.models.functions import Abs, TruncMonth
from django.utils import timezone
from app.models import Movimientos_Producto

HOY = timezone.localdate()
fc = date(2026, 3, 1)
post = Movimientos_Producto.objects.filter(estado='COMPLETADO', fecha__gt=fc, fecha__lte=HOY)

ing_mal = post.filter(Q(tipo_movimiento='INGRESO') | Q(concepto='TRASPASO_ENTRADA')) \
    .exclude(sucursal_destino_id=F('ProductoTalla__producto__sucursal_id'))
print('ingresos destino!=producto.sucursal desde 2026-03, por concepto/null:')
for r in ing_mal.values('concepto').annotate(
        n=Count('id'), u=Sum(Abs('cantidad')),
        nulos=Count('id', filter=Q(sucursal_destino__isnull=True))).order_by('-n')[:12]:
    print('  ', r)
print('por mes:')
for r in ing_mal.annotate(m=TruncMonth('fecha')).values('m').annotate(
        n=Count('id'), u=Sum(Abs('cantidad'))).order_by('m'):
    print('  ', r['m'], r['n'], r['u'])

eg_mal = post.filter(Q(tipo_movimiento='EGRESO') | Q(concepto='TRASPASO_SALIDA')) \
    .exclude(sucursal_origen_id=F('ProductoTalla__producto__sucursal_id'))
print('egresos origen!=producto.sucursal desde 2026-03:')
for r in eg_mal.values('concepto').annotate(
        n=Count('id'), u=Sum(Abs('cantidad')),
        nulos=Count('id', filter=Q(sucursal_origen__isnull=True))).order_by('-n')[:12]:
    print('  ', r)
print('FIN T4')
