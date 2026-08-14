# -*- coding: utf-8 -*-
"""
READ-ONLY: qué ve (y qué no) el resumen de existencias para IMP y PA00,
y por qué las bolsas no figuran en el modal de exclusiones.

Uso: python manage.py shell -c "exec(open('_diag_imp_pa00_bolsas_readonly.py', encoding='utf-8').read())"
"""
from django.db.models import Q, Sum, Count, F
from app.models import Producto, Producto_Talla, Sucursal, EmpresaUser

Q_BOLSA = Q(producto__articulo__icontains='bolsa') | Q(producto__descripcion__icontains='bolsa')

print('=' * 108)
print('[1] TODAS las sucursales: stock que ENTRA al reporte vs stock EXCLUIDO por excluir_de_analitica')
print('=' * 108)
print(f"{'id':>4} {'alias':<16} {'empresa':<26} {'EN REPORTE u.':>14} {'EXCLUIDO u.':>12} {'de eso bolsas':>14} {'¿sale?':>8}")
print('-' * 108)

for s in Sucursal.objects.select_related('empresa').order_by('alias'):
    base = Producto_Talla.objects.filter(producto__sucursal=s, stock__gt=0)
    en_reporte = base.filter(producto__excluir_de_analitica=False).aggregate(u=Sum('stock'))['u'] or 0
    excluido = base.filter(producto__excluir_de_analitica=True).aggregate(u=Sum('stock'))['u'] or 0
    bolsas_excl = base.filter(producto__excluir_de_analitica=True).filter(Q_BOLSA).aggregate(u=Sum('stock'))['u'] or 0
    if en_reporte == 0 and excluido == 0:
        continue
    sale = 'SI' if en_reporte > 0 else '*** NO ***'
    print(f"{s.id:>4} {s.alias[:16]:<16} {(s.empresa.nombre[:26] if s.empresa else '-'):<26} "
          f"{en_reporte:>14,} {excluido:>12,} {bolsas_excl:>14,} {sale:>8}")

print('\n' + '=' * 108)
print('[2] Detalle IMP y PA00: qué productos tienen stock y si están excluidos')
print('=' * 108)
for alias in ['IMP', 'PA00']:
    s = Sucursal.objects.filter(alias__iexact=alias).first()
    if not s:
        print(f'\n  (no existe sucursal {alias})')
        continue
    print(f"\n--- {s.alias} (id {s.id}) — empresa {s.empresa.nombre if s.empresa else '-'} ---")
    filas = (
        Producto_Talla.objects.filter(producto__sucursal=s, stock__gt=0)
        .values('producto_id', 'producto__articulo', 'producto__excluir_de_analitica')
        .annotate(u=Sum('stock'), n=Count('id'))
        .order_by('-u')[:25]
    )
    print(f"  {'prod_id':>8} {'articulo':<40} {'tallas':>7} {'unidades':>9} {'excluido':>9}")
    for f in filas:
        print(f"  {f['producto_id']:>8} {(f['producto__articulo'] or '')[:40]:<40} "
              f"{f['n']:>7} {f['u']:>9,} {str(f['producto__excluir_de_analitica']):>9}")
    tot = Producto_Talla.objects.filter(producto__sucursal=s, stock__gt=0).aggregate(
        u=Sum('stock'), n=Count('id'))
    tot_excl = Producto_Talla.objects.filter(
        producto__sucursal=s, stock__gt=0, producto__excluir_de_analitica=True
    ).aggregate(u=Sum('stock'))['u'] or 0
    print(f"  TOTAL: {tot['n'] or 0} tallas / {tot['u'] or 0:,} unidades — "
          f"excluidas de analítica: {tot_excl:,} "
          f"({(100 * tot_excl / tot['u']) if tot['u'] else 0:.1f}%)")

print('\n' + '=' * 108)
print('[3] Universo excluir_de_analitica=True: ¿son solo bolsas?')
print('=' * 108)
excl = Producto.objects.filter(excluir_de_analitica=True)
print(f"  Productos con excluir_de_analitica=True: {excl.count():,}")
print(f"    de ellos con 'bolsa' en articulo/descripcion: "
      f"{excl.filter(Q(articulo__icontains='bolsa') | Q(descripcion__icontains='bolsa')).count():,}")
print('  Top artículos excluidos que NO son bolsa:')
for f in (excl.exclude(Q(articulo__icontains='bolsa') | Q(descripcion__icontains='bolsa'))
          .values('articulo').annotate(n=Count('id')).order_by('-n')[:15]):
    print(f"    {f['articulo'][:60]:<60} {f['n']:>5} fichas")

print('\n' + '=' * 108)
print('[4] Modal de exclusiones: cuántos productos matchean vs cuántos DEVUELVE (cap 50)')
print('=' * 108)
for termino in ['bolsa', 'BOLSA REAL', 'BOLSA GENERO']:
    total = Producto.objects.filter(
        Q(articulo__icontains=termino) | Q(descripcion__icontains=termino)
    ).distinct().count()
    print(f"  q='{termino}': matchean {total} productos → el modal muestra {min(total, 50)} "
          f"{'(SE PIERDEN ' + str(total - 50) + ')' if total > 50 else ''}")

print('\n(read-only: no se modificó nada)')
