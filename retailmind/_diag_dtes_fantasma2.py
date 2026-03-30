import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models import Dte, Dte_Detalle_Pago
from django.db.models import Count, Max

# Para cada sucursal + tipo_documento, obtener el folio máximo ANTES de 2026
print('=== FOLIO MAXIMO POR SUCURSAL+TIPO (antes de 2026-01-01) ===')
from django.db.models import Q

max_folios = (
    Dte.objects.filter(fecha_emision__lt='2026-01-01')
    .values('sucursal__alias', 'tipo_documento')
    .annotate(max_folio=Max('numero_documento'))
    .order_by('sucursal__alias', 'tipo_documento')
)
folio_max_dict = {}  # (suc_alias, tipo) → max_folio
for r in max_folios:
    folio_max_dict[(r['sucursal__alias'], r['tipo_documento'])] = r['max_folio']
    print(f"  suc={r['sucursal__alias']:<8} tipo={r['tipo_documento']:<25} max_folio={r['max_folio']}")

# DTEs fantasma: folio MENOR al maximo historico de esa sucursal+tipo, creados en 2026
print()
print('=== DTEs CON FOLIO MENOR AL MAXIMO HISTORICO (posibles fantasmas) ===')
fantasmas_real = []
candidatos = (
    Dte.objects.filter(fecha_emision__gte='2026-01-01')
    .annotate(n_prod=Count('dte_productos'))
    .filter(n_prod=0)
    .select_related('sucursal')
    .values('id', 'numero_documento', 'tipo_documento', 'sucursal__alias', 'sucursal_id', 'fecha_emision')
)

for d in candidatos:
    alias = d['sucursal__alias']
    tipo = d['tipo_documento']
    folio = d['numero_documento']
    max_folio = folio_max_dict.get((alias, tipo))
    if max_folio and folio < max_folio:
        fantasmas_real.append(d)
        print(f"  FANTASMA: id={d['id']} folio={folio} {tipo[:15]:<15} suc={alias} max_historico={max_folio} fecha={d['fecha_emision']}")

print(f'\nTotal DTEs fantasma (folio < max_historico, 0 productos, 2026): {len(fantasmas_real)}')

if fantasmas_real:
    ids_fantasma = [d['id'] for d in fantasmas_real]
    pagos = Dte_Detalle_Pago.objects.filter(dte_id__in=ids_fantasma).count()
    print(f'Con pagos asignados: {pagos}')
    print(f'Sin pagos: {len(ids_fantasma) - pagos}')
