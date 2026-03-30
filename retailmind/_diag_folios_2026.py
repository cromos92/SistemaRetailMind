import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models import Dte, Dte_Detalle_Pago

# DTEs con folio bajo (< 100000) en fecha 21-03-2026
dtes = Dte.objects.filter(
    fecha_emision='2026-03-21',
    numero_documento__lt=100000
).select_related('sucursal').values(
    'id', 'numero_documento', 'tipo_documento',
    'sucursal__alias', 'sucursal__direccion',
    'monto_neto', 'estado_dte'
).order_by('numero_documento')

print(f'DTEs con folio < 100000 en 2026-03-21: {dtes.count()}')
print()
for d in dtes:
    print(f"  folio={d['numero_documento']:>7}  tipo={d['tipo_documento'][:20]:<20}  suc={str(d['sucursal__alias']):<8}  dir={str(d['sucursal__direccion']):<20}  monto={d['monto_neto']}  estado={d['estado_dte']}")

print()

# Ver si esos mismos folios existen en otras fechas (folio reusado)
folios_bajos = [d['numero_documento'] for d in dtes]
if folios_bajos:
    print('Verificando si esos folios existen en OTRAS fechas (colision):')
    colisiones = Dte.objects.filter(
        numero_documento__in=folios_bajos
    ).exclude(
        fecha_emision='2026-03-21'
    ).values('numero_documento', 'fecha_emision', 'tipo_documento', 'sucursal__alias').order_by('numero_documento', 'fecha_emision')
    for c in colisiones:
        print(f"  folio={c['numero_documento']}  fecha={c['fecha_emision']}  tipo={c['tipo_documento'][:20]}  suc={c['sucursal__alias']}")
    if not colisiones:
        print('  (ninguno — los folios son unicos en esa fecha)')

# Verificar si esos DTEs tienen productos
print()
print('DTEs sin productos (sospechosos de crear_dtes_faltantes):')
from django.db.models import Count
dtes_con_prods = Dte.objects.filter(
    fecha_emision='2026-03-21',
    numero_documento__lt=100000
).annotate(n_prod=Count('dte_productos')).values('numero_documento', 'n_prod', 'sucursal__alias')
for d in dtes_con_prods:
    print(f"  folio={d['numero_documento']}  productos={d['n_prod']}  suc={d['sucursal__alias']}")
