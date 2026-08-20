# -*- coding: utf-8 -*-
# AUDITORIA read-only - tanda 5: composicion SIN_RECIBIR a 365d + neteo NC.
import os
import sys
from datetime import timedelta
from collections import defaultdict

import django

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db.models import Q, Sum, Count, F, Value, IntegerField, Min
from django.db.models.functions import Abs, Coalesce
from django.utils import timezone

from app.models import Dte, Dte_Productos, Movimientos_Producto, Productos_Recepcionados

EXCL = ('ANULADO', 'CANCELADO', 'RECHAZADO')
ENTRADA = ('TRASPASO_ENTRADA', 'REGULARIZACION_TRASPASO')
ms = Movimientos_Producto.objects


def p(*a):
    print(*a, flush=True)


hoy = timezone.localdate()
desde = hoy - timedelta(days=365)
dte_ids = list(
    ms.filter(concepto='TRASPASO_SALIDA', dte__isnull=False, fecha__gte=desde)
    .exclude(dte__descartado=True).exclude(dte__estado_dte__in=EXCL)
    .order_by().values_list('dte_id', flat=True).distinct()
)
p('dtes 365d:', len(dte_ids))

env = {}
for r in (ms.filter(concepto='TRASPASO_SALIDA', dte_id__in=dte_ids)
          .values('dte_id').annotate(u=Sum(Abs('cantidad')), f0=Min('fecha')).order_by()):
    env[r['dte_id']] = (r['u'] or 0, r['f0'])
rec = dict(ms.filter(concepto__in=ENTRADA, dte_id__in=dte_ids)
           .values_list('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by())

nc_all = list(Dte.objects.filter(documento_afectado_id__in=dte_ids)
              .values('id', 'documento_afectado_id', 'estado_dte',
                      'redujo_lineas_documento', 'numero_documento'))
nc_ids = [n['id'] for n in nc_all]
nc_map = {n['id']: n for n in nc_all}
nc_ing = dict(ms.filter(dte_id__in=nc_ids, tipo_movimiento__in=('INGRESO', 'DEVOLUCION'))
              .values_list('dte_id').annotate(u=Sum(Abs('cantidad'))).order_by()) if nc_ids else {}
dev = defaultdict(int)
dev_detalle = defaultdict(list)
for nc_id, u in nc_ing.items():
    n = nc_map[nc_id]
    dev[n['documento_afectado_id']] += (u or 0)
    dev_detalle[n['documento_afectado_id']].append(
        (n['numero_documento'], n['estado_dte'], n['redujo_lineas_documento'], u))

p('-- conceptos de los movimientos de las NC hijas --')
for r in (ms.filter(dte_id__in=nc_ids).values('concepto', 'tipo_movimiento')
          .annotate(u=Sum(Abs('cantidad')), n=Count('id')).order_by('-u')):
    p('  ', r)

nc_sin_mov = [n for n in nc_all if n['id'] not in nc_ing
              and n['estado_dte'] not in ('ANULADO', 'CANCELADO')]
p('NCs vigentes SIN movimientos (no netean nada):', len(nc_sin_mov))
for n in nc_sin_mov[:15]:
    p('  ', n)

p('-- SIN_RECIBIR a 365d --')
sin_rec = []
for d in dte_ids:
    enviadas, f0 = env.get(d, (0, None))
    r = rec.get(d, 0) or 0
    dv = dev.get(d, 0)
    pend = max(0, enviadas - r - dv)
    dias = (hoy - f0).days if f0 else 0
    if r <= enviadas and pend > 0 and dias > 3:
        sin_rec.append((d, enviadas, r, dv, pend, dias))
p('docs:', len(sin_rec), '| unidades:', sum(x[4] for x in sin_rec))
folios = dict(Dte.objects.filter(id__in=[x[0] for x in sin_rec])
              .values_list('id', 'numero_documento'))
pr_map = {r['dte_id']: r for r in
          (Productos_Recepcionados.objects.filter(dte_id__in=[x[0] for x in sin_rec])
           .values('dte_id').annotate(f=Sum('cantidad_faltante'), reg=Count('id', filter=Q(estado='REGULARIZADO'))))}
for (d, enviadas, r, dv, pend, dias) in sorted(sin_rec, key=lambda x: -x[5]):
    pr = pr_map.get(d)
    p(f'   dte={d} folio={folios.get(d)} env={enviadas} rec={r} devNC={dv} pend={pend} '
      f'dias={dias} PR_falt={pr and pr["f"]} PR_reg={pr and pr["reg"]} NCs={dev_detalle.get(d)}')
