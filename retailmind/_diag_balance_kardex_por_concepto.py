# -*- coding: utf-8 -*-
"""READ-ONLY: de que concepto sale el descuadre kardex-vs-stock de una marca.

Uso (por defecto PANAMA JACK, igual que _diag_stock_inicial_panama_jack.py):
    python manage.py shell -c "exec(open('_diag_balance_kardex_por_concepto.py', encoding='utf-8').read())"
Para otra marca, editar MARCA abajo.
"""
from django.db.models import Sum, Count
from app.models import Producto, Producto_Talla, Movimientos_Producto
from app.constants_kardex import (
    CONCEPTOS_TRASPASO_ENTRADA, CONCEPTOS_TRASPASO_LEGACY, CONCEPTOS_TRASPASO_SALIDA,
)

MARCA = 'SKECHERS'

print('Conceptos que el script considera SALIDA de traspaso: '
      f'{list(CONCEPTOS_TRASPASO_SALIDA)}')
print(f'Conceptos ENTRADA moderna: {list(CONCEPTOS_TRASPASO_ENTRADA)}')
print(f'Conceptos LEGACY: {list(CONCEPTOS_TRASPASO_LEGACY)}')

productos = Producto.objects.filter(atributo1__valor__iexact=MARCA)
tallas = Producto_Talla.objects.filter(producto__in=productos)
stock_total = tallas.aggregate(s=Sum('stock'))['s'] or 0
movs = Movimientos_Producto.objects.filter(ProductoTalla__in=tallas, estado='COMPLETADO')
neto = movs.aggregate(s=Sum('cantidad'))['s'] or 0

print('=' * 88)
print(f'BALANCE KARDEX vs STOCK -- marca {MARCA}')
print('=' * 88)
print(f'  tallas: {tallas.count()}  |  stock actual: {stock_total}  |  kardex neto: {neto}')
print(f'  DIFERENCIA (stock - kardex): {stock_total - neto}')

print()
print('--- Aporte de CADA concepto al kardex neto (ordenado por magnitud) ---')
print(f'{"concepto":<36}{"movs":>8}{"neto und":>12}{"solo +":>10}{"solo -":>10}')
filas = []
for r in movs.values('concepto').annotate(n=Count('id'), s=Sum('cantidad')):
    pos = movs.filter(concepto=r['concepto'], cantidad__gt=0).aggregate(s=Sum('cantidad'))['s'] or 0
    neg = movs.filter(concepto=r['concepto'], cantidad__lt=0).aggregate(s=Sum('cantidad'))['s'] or 0
    filas.append((r['concepto'], r['n'], r['s'] or 0, pos, neg))
for c, n, s, pos, neg in sorted(filas, key=lambda x: -abs(x[2])):
    print(f'{c:<36}{n:>8}{s:>12}{pos:>10}{neg:>10}')

print()
print('--- Los traspasos: cuadran las dos piernas? ---')
grupos = [
    ('TRASPASO ENTRADA (moderno)', CONCEPTOS_TRASPASO_ENTRADA),
    ('TRASPASO LEGACY', CONCEPTOS_TRASPASO_LEGACY),
    ('TRASPASO SALIDA', CONCEPTOS_TRASPASO_SALIDA),
]
resumen = {}
for nombre, conceptos in grupos:
    agg = movs.filter(concepto__in=list(conceptos)).aggregate(n=Count('id'), s=Sum('cantidad'))
    resumen[nombre] = agg['s'] or 0
    print(f'  {nombre:<28} movs {agg["n"] or 0:>7} | neto {agg["s"] or 0:>9} und')
entradas = resumen['TRASPASO ENTRADA (moderno)'] + resumen['TRASPASO LEGACY']
salidas = resumen['TRASPASO SALIDA']
print(f'  => entradas de traspaso {entradas} + salidas {salidas} = {entradas + salidas}')
print('     Si NO es ~0, hay traspasos con una sola pierna registrada')
print('     (esa es la causa candidata del descuadre, no ventas sin registrar).')

print()
print('FIN (solo lectura, no se modifico nada).')
