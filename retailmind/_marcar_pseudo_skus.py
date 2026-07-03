"""
Marca los pseudo-SKUs de servicio con excluir_de_analitica=True.

Son artículos que no son mercadería real (bolsas de despacho, el SKU dummy
123456789 "VISA", envíos, mesas de exhibición) y concentran el grueso del gap
stock↔kardex, contaminando KPIs, predicción y reportes.

SEGURIDAD:
  - Por defecto DRY-RUN: lista los candidatos con su stock y volumen de kardex
    para que se revisen uno a uno ANTES de marcar.
  - --apply marca Producto.excluir_de_analitica=True vía UPDATE de queryset
    (sin señales). No borra nada, no toca stock; es reversible poniendo el
    flag de vuelta en False.

Uso (desde retailmind/):
    python _marcar_pseudo_skus.py            # dry-run
    python _marcar_pseudo_skus.py --apply
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
import django  # noqa: E402

django.setup()

from django.db.models import Count, Q, Sum  # noqa: E402

from app.models import Movimientos_Producto, Producto, Producto_Talla  # noqa: E402

APPLY = '--apply' in sys.argv

# Detectados en la auditoría (top del gap stock↔kardex). El dry-run existe
# justamente para revisar que ningún producto real caiga en estos patrones.
FILTRO_CANDIDATOS = (
    Q(articulo__istartswith='BOLSA')
    | Q(articulo__iexact='ENVIOS')
    | Q(articulo__iexact='VISA')
    | Q(articulo__iexact='45-1')
    | Q(articulo__iexact='45-2')
    | Q(articulo__iexact='MESA DE PING PONG')
    | Q(producto_talla__sku=123456789)
)

candidatos = (
    Producto.objects.filter(FILTRO_CANDIDATOS, excluir_de_analitica=False)
    .distinct()
    .select_related('sucursal')
    .order_by('articulo', 'sucursal__alias')
)

print('=' * 88)
print(f"PSEUDO-SKUs DE SERVICIO -> excluir_de_analitica (modo: {'APPLY' if APPLY else 'DRY-RUN'})")
print('=' * 88)
print(f"{'ARTÍCULO':<32} {'SUC':<8} {'STOCK':>8} {'MOVS':>10} {'Σ KARDEX':>10}")
print('-' * 88)

ids = []
for p in candidatos:
    stock = Producto_Talla.objects.filter(producto=p).aggregate(s=Sum('stock'))['s'] or 0
    kx = Movimientos_Producto.objects.filter(ProductoTalla__producto=p).aggregate(
        n=Count('id'), s=Sum('cantidad'))
    print(f"{(p.articulo or '?')[:32]:<32} {(p.sucursal.alias if p.sucursal else '?'):<8} "
          f"{stock:>8,} {kx['n'] or 0:>10,} {kx['s'] or 0:>10,}")
    ids.append(p.id)

print('-' * 88)
print(f'Total productos candidatos: {len(ids)}')

if not APPLY:
    print('\n[DRY-RUN] No se marcó nada. Revisa la lista: si TODOS son de '
          'servicio, aplica con:\n    python _marcar_pseudo_skus.py --apply')
    sys.exit(0)

actualizados = Producto.objects.filter(id__in=ids).update(excluir_de_analitica=True)
print(f'\n[OK] {actualizados} productos marcados excluir_de_analitica=True '
      '(reversible con update(excluir_de_analitica=False)).')
