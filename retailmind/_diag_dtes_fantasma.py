import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models import Dte, Dte_Detalle_Pago
from django.db.models import Count

# DTEs con folio bajo en 2026-03-21 que NO existen en fechas anteriores para la misma sucursal
# (estos son los fantasmas creados por crear_dtes_faltantes)

# Paso 1: folios bajos en 2026-03-21 con 0 productos
candidatos = (
    Dte.objects.filter(fecha_emision='2026-03-21', numero_documento__lt=100000)
    .annotate(n_prod=Count('dte_productos'))
    .filter(n_prod=0)
    .values('id', 'numero_documento', 'tipo_documento', 'sucursal_id', 'sucursal__alias')
)

print(f'DTEs con folio < 100000 en 2026-03-21 y 0 productos: {candidatos.count()}')
print()

fantasmas = []
for d in candidatos:
    folio = d['numero_documento']
    tipo = d['tipo_documento']
    suc_id = d['sucursal_id']

    # Verificar si el mismo (folio, tipo, suc_id) existe en OTRA fecha (anterior)
    otros = Dte.objects.filter(
        numero_documento=folio,
        tipo_documento=tipo,
        sucursal_id=suc_id
    ).exclude(fecha_emision='2026-03-21').exists()

    if otros:
        fantasmas.append(d)
        print(f"  FANTASMA: id={d['id']} folio={folio} {tipo[:15]} suc={d['sucursal__alias']} (existe en otra fecha para la misma suc)")

print(f'\nTotal fantasmas claros (mismo folio+tipo+suc, fecha distinta): {len(fantasmas)}')

# Paso 2: ver si tienen pagos
if fantasmas:
    ids_fantasma = [d['id'] for d in fantasmas]
    pagos = Dte_Detalle_Pago.objects.filter(dte_id__in=ids_fantasma).values('dte_id', 'voucher', 'monto', 'metodo_pago')
    print(f'\nPagos asignados a estos fantasmas: {pagos.count()}')
    for p in pagos:
        print(f"  DTE {p['dte_id']} — {p['metodo_pago']} {p['voucher']} ${p['monto']}")
