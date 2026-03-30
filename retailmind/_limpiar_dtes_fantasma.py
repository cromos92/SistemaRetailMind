"""
Limpia DTEs fantasma creados por crear_dtes_faltantes con folios retroactivos.
Un DTE es fantasma si su folio es MENOR al máximo histórico de su sucursal+tipo
y tiene 0 productos (indicando que fue creado por faltantes, no por migración real).
"""
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from app.models import Dte, Dte_Detalle_Pago
from django.db.models import Count, Max, Q

print('=== LIMPIEZA DE DTEs FANTASMA ===')
print()

# 1) Calcular folio máximo por (sucursal_id, tipo_documento) usando DTEs con
#    muchos productos (claramente legítimos) como referencia
print('Calculando folios máximos históricos...')
max_folios = {}
for suc_id, tipo, max_f in (
    Dte.objects.values('sucursal_id', 'tipo_documento')
    .annotate(max_folio=Max('numero_documento'))
    .values_list('sucursal_id', 'tipo_documento', 'max_folio')
):
    if suc_id:
        max_folios[(suc_id, tipo)] = max_f

print(f'  {len(max_folios)} combinaciones suc+tipo indexadas')

# 2) Identificar DTEs fantasma: folio < max_historico Y 0 productos
print('Identificando DTEs fantasma...')
candidatos = list(
    Dte.objects.annotate(n_prod=Count('dte_productos'))
    .filter(n_prod=0)
    .values('id', 'numero_documento', 'tipo_documento', 'sucursal_id',
            'sucursal__alias', 'fecha_emision')
)

fantasmas = []
for d in candidatos:
    suc_id = d['sucursal_id']
    if not suc_id:
        continue
    max_f = max_folios.get((suc_id, d['tipo_documento']), 0)
    if max_f > 0 and d['numero_documento'] < max_f:
        fantasmas.append(d['id'])

print(f'  DTEs fantasma identificados: {len(fantasmas):,}')

if not fantasmas:
    print('  No hay DTEs fantasma. Nada que limpiar.')
    exit(0)

# 3) Contar pagos que se eliminarían en cascada
pagos_count = Dte_Detalle_Pago.objects.filter(dte_id__in=fantasmas).count()
print(f'  Pagos asociados a eliminar: {pagos_count:,}')

# 4) Confirmar
print()
print(f'Eliminar {len(fantasmas):,} DTEs fantasma y {pagos_count:,} pagos asociados? (s/n)')
resp = input()
if resp.lower() != 's':
    print('Cancelado.')
    exit(0)

# 5) Eliminar por lotes
batch_size = 1000
deleted_dtes = 0
deleted_pagos = 0

for i in range(0, len(fantasmas), batch_size):
    batch = fantasmas[i:i + batch_size]
    p_del, _ = Dte_Detalle_Pago.objects.filter(dte_id__in=batch).delete()
    deleted_pagos += p_del
    d_del, _ = Dte.objects.filter(id__in=batch).delete()
    deleted_dtes += d_del
    print(f'  Lote {i//batch_size + 1}: {d_del} DTEs eliminados', end='\r')

print()
print(f'Listo: {deleted_dtes:,} DTEs y {deleted_pagos:,} pagos eliminados.')
print()
print('Ahora corre:')
print('  python manage.py migrate_from_laravel --tables ventas_pagos --no-input')
