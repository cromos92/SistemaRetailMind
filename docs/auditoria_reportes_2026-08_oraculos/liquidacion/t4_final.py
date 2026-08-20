# -*- coding: utf-8 -*-
# TANDA 4 — filas PAOLA/REALSPORT, pico lotes jul-2026, incluir_cd, bucket 1-anio
import json, sys, time
from datetime import timedelta, date
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
from django.test import RequestFactory
from django.utils import timezone
from django.db.models import Sum, Count, F, BigIntegerField
from django.db.models.functions import Abs
from django.contrib.auth import get_user_model
from app.models import EmpresaUser, Movimientos_Producto, Sucursal
from app.constants_kardex import CONCEPTOS_ABASTECIMIENTO, CONCEPTOS_TRASPASO_ENTRADA
from app.views_inteligencia_compra import (obtener_plan_liquidacion, _scope_plan,
                                           _detalle_query)
BI = BigIntegerField()
P = print
User = get_user_model()
factory = RequestFactory()
hoy = timezone.localdate()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())
emp_ids = list(EmpresaUser.objects.filter(user=admin, status=True)
               .values_list('empresa_id', flat=True).distinct())
tiendas_ids = [s.id for s in Sucursal.objects.filter(empresa_id__in=emp_ids)
               if not s.es_centro_distribucion]


def req_as(params=None):
    r = factory.get('/x', params or {})
    r.user = admin
    r.session = {'idSucursalActual': tiendas_ids[0], 'idEmpresaActual': emp_ids[0]}
    return r


# filas reales PAOLA / REAL SPORT / n_reponer
resp = obtener_plan_liquidacion(req_as())
d = json.loads(resp.content)['data']
for f in d['marcas']:
    if f.get('marca') in ('PAOLA', 'REAL SPORT', 'NITYA'):
        P('FILA %s' % json.dumps(f))
rep = [(f['marca'], f['ttm_u'], f['gmroi'], f['cobertura']) for f in d['marcas']
       if f['accion'] == 'Reponer']
P('REPONER n=%s: %s' % (len(rep), rep[:25]))

# abastecimiento real tiendas jun-ago 2026 (interpreta pico lotes julio)
ab = Movimientos_Producto.objects.filter(
    estado='COMPLETADO',
    ProductoTalla__producto__sucursal_id__in=tiendas_ids,
    fecha__gte=date(2026, 6, 1), fecha__lt=date(2026, 9, 1))
o_ab = ab.filter(concepto__in=CONCEPTOS_ABASTECIMIENTO).aggregate(
    u=Sum(Abs('cantidad'), output_field=BI), n=Count('id'))
o_tr = ab.filter(concepto__in=CONCEPTOS_TRASPASO_ENTRADA).aggregate(
    u=Sum(Abs('cantidad'), output_field=BI), n=Count('id'))
P('ABASTECIMIENTO tiendas jun-ago2026: compras=%s traspasos_entrada=%s' % (o_ab, o_tr))

# incluir_cd=1: totales + tiempo
t0 = time.perf_counter()
resp2 = obtener_plan_liquidacion(req_as({'incluir_cd': '1'}))
ms = round((time.perf_counter() - t0) * 1000)
d2 = json.loads(resp2.content)['data']
P('PLAN incluir_cd totales=%s ms=%s' % (json.dumps(d2['totales']), ms))
suc_rows = [(f['sucursal'], f.get('es_cd'), f['stock'], f['stock_cd'], f['accion'])
            for f in d2['sucursales']]
P('PLAN incluir_cd sucursales=%s' % suc_rows)

# capital etiquetado "1 anio" (anio 2025) pero con lote de hace <12 meses
base_pt, mov_base, ctx = _scope_plan(req_as())
qs = _detalle_query(base_pt, mov_base, hoy)
h2 = qs.filter(fecha_lote__gte=date(2025, 8, 20), fecha_lote__lt=date(2026, 1, 1))
o_h2 = h2.aggregate(n=Count('id'), s=Sum('stock_u', output_field=BI),
                    v=Sum(F('costo') * F('stock_u'), output_field=BI))
P('BUCKET anio-2025 con <365d de antiguedad real: %s' % o_h2)
P('FIN T4')
