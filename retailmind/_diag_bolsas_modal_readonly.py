# -*- coding: utf-8 -*-
"""
READ-ONLY: por qué las bolsas de IMP y PA00 no aparecen en el modal
"Excluir Artículos del Análisis" del resumen de existencias.

Uso: python manage.py shell -c "exec(open('_diag_bolsas_modal_readonly.py', encoding='utf-8').read())"
"""
from django.db.models import Q, Sum
from app.models import Producto, Sucursal

# 1. Identificar IMP y PA00
print('=' * 100)
print('[1] Sucursales que matchean IMP / PA00 / PAO')
print('=' * 100)
for s in Sucursal.objects.filter(
    Q(alias__icontains='IMP') | Q(alias__icontains='PA0') | Q(alias__icontains='PAO')
).select_related('empresa').order_by('alias'):
    print(f"  id {s.id:>3}  {s.alias:<20} empresa: {s.empresa.nombre if s.empresa else '-'}")

# 2. Todos los productos "bolsa" del holding, por sucursal
print('\n' + '=' * 100)
print('[2] Productos con "BOLSA" en articulo o descripcion, por sucursal')
print('=' * 100)
bolsas = (
    Producto.objects.filter(
        Q(articulo__icontains='bolsa') | Q(descripcion__icontains='bolsa')
    )
    .select_related('sucursal', 'sucursal__empresa')
    .annotate(stock_total=Sum('producto_talla__stock'))
    .order_by('articulo', 'id')
)
print(f"Total productos que matchean: {bolsas.count()}\n")
print(f"{'pos':>4} {'prod_id':>8} {'articulo':<38} {'sucursal':<16} {'stock':>7} {'excl_analitica':>14}")
print('-' * 100)
for i, p in enumerate(bolsas, 1):
    marca_50 = ' <== FUERA del corte [:50] del modal' if i > 50 else ''
    print(f"{i:>4} {p.id:>8} {p.articulo[:38]:<38} {p.sucursal.alias[:16]:<16} "
          f"{p.stock_total or 0:>7} {str(p.excluir_de_analitica):>14}{marca_50}")

# 3. Simular EXACTAMENTE la query del modal con q='bolsa' (sin filtro de sucursal)
print('\n' + '=' * 100)
print("[3] Simulación de la query del modal: icontains 'bolsa' en articulo/descripcion/sku, [:50] por articulo")
print('=' * 100)
modal_qs = (
    Producto.objects.filter(
        Q(articulo__icontains='bolsa')
        | Q(descripcion__icontains='bolsa')
        | Q(producto_talla__sku__icontains='bolsa')
    )
    .select_related('sucursal')
    .distinct().order_by('articulo')[:50]
)
suc_en_modal = {}
for p in modal_qs:
    suc_en_modal.setdefault(p.sucursal.alias, 0)
    suc_en_modal[p.sucursal.alias] += 1
print('Sucursales presentes en los 50 resultados que devuelve el modal:')
for alias, n in sorted(suc_en_modal.items()):
    print(f'  {alias:<20} {n} productos')

print('\n(read-only: no se modificó nada)')
