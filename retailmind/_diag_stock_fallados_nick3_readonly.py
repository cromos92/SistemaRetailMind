# -*- coding: utf-8 -*-
"""
READ-ONLY: mide stock plano, negativos y lotes FIFO de sucursales candidatas
a vaciado (EDEL FALLADOS / NICK3). No escribe nada.

Uso: python manage.py shell -c "exec(open('_diag_stock_fallados_nick3_readonly.py', encoding='utf-8').read())"
"""
from django.db.models import Sum, Count, F, Q
from app.models import Sucursal, Producto_Talla, LoteProducto

candidatas = Sucursal.objects.filter(
    Q(alias__icontains='FALLAD') | Q(alias__icontains='NICK') | Q(alias__icontains='EDEL')
).select_related('empresa').order_by('alias')

print(f"{'id':>4}  {'alias':<28} {'empresa':<22} {'estado':<9} "
      f"{'tallas>0':>9} {'unidades':>9} {'$ costo':>15} {'tallas<0':>9} {'u.neg':>7} {'lotes act':>10} {'u.lotes':>9}")
print('-' * 140)

for s in candidatas:
    qs = Producto_Talla.objects.filter(producto__sucursal=s)
    pos = qs.filter(stock__gt=0).aggregate(n=Count('id'), u=Sum('stock'),
                                           v=Sum(F('stock') * F('producto__costo')))
    neg = qs.filter(stock__lt=0).aggregate(n=Count('id'), u=Sum('stock'))
    lotes = LoteProducto.objects.filter(
        producto_talla__producto__sucursal=s, activo=True, cantidad_disponible__gt=0
    ).aggregate(n=Count('id'), u=Sum('cantidad_disponible'))
    estado = 'activa' if getattr(s, 'activa', True) else 'INACTIVA'
    print(f"{s.id:>4}  {s.alias:<28} {(s.empresa.nombre if s.empresa else '-'):<22} {estado:<9} "
          f"{pos['n'] or 0:>9} {pos['u'] or 0:>9} {pos['v'] or 0:>15,} {neg['n'] or 0:>9} {neg['u'] or 0:>7} "
          f"{lotes['n'] or 0:>10} {lotes['u'] or 0:>9}")

print('\n(read-only: no se modificó nada)')
