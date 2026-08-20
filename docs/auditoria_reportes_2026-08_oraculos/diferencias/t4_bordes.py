# -*- coding: utf-8 -*-
# AUDITORIA read-only - tanda 4: bordes y scoping con usuario CON permiso.
import os
import sys
import time
import json
from datetime import timedelta
from collections import defaultdict

import django

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db import connection
from django.db.models import Q, Sum, Count, Min, Max
from django.db.models.functions import Abs
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model
from django.utils import timezone

from app.models import (
    Dte, Movimientos_Producto, Productos_Recepcionados, Sucursal, EmpresaUser,
)

EXCL = ('ANULADO', 'CANCELADO', 'RECHAZADO')
ENTRADA = ('TRASPASO_ENTRADA', 'REGULARIZACION_TRASPASO')


def p(*a):
    print(*a, flush=True)


p('== 1. Solicitud_Regularizacion total ==')
try:
    from app.models import Solicitud_Regularizacion as SR
except ImportError:
    from app.models.compras import Solicitud_Regularizacion as SR
p('   filas totales:', SR.objects.count())

p('== 2. Filas legacy con cantidad_esperada=0 (explican recibidas>esperadas) ==')
base = (Productos_Recepcionados.objects.filter(dte__isnull=False)
        .exclude(dte__descartado=True).exclude(dte__estado_dte__in=EXCL))
ag = base.filter(cantidad_esperada=0).aggregate(
    n=Count('id'), arribado=Sum('stockArribado'))
p('   lineas esperada=0:', ag)
p('   de ellas dte_producto NULL:',
  base.filter(cantidad_esperada=0, dte_producto__isnull=True).count())

p('== 3. Los 11 dtes con faltante: movimientos por concepto + pendiente estilo transito ==')
falt_dtes = [2182663, 2182661, 2182662, 2180310, 2178716, 2181689, 2182609,
             2180325, 2180102, 2178807, 2189501]
por_dte = defaultdict(dict)
for r in (Movimientos_Producto.objects.filter(dte_id__in=falt_dtes)
          .values('dte_id', 'concepto', 'tipo_movimiento')
          .annotate(u=Sum(Abs('cantidad')), n=Count('id'))
          .order_by('dte_id')):
    por_dte[r['dte_id']][(r['concepto'], r['tipo_movimiento'])] = (r['u'], r['n'])
for d in falt_dtes:
    env = sum(u for (c, t), (u, n) in por_dte[d].items() if c == 'TRASPASO_SALIDA')
    rec = sum(u for (c, t), (u, n) in por_dte[d].items() if c in ENTRADA)
    pend = max(0, env - rec)
    p(f'   dte={d} env={env} rec(entrada oficial)={rec} pend_transito={pend} '
      f'conceptos={ {k: v for k, v in por_dte[d].items()} }')

p('== 4. Los 14 SIN_RECIBIR: estado_dte y destino ==')
ids14 = [2197408, 2197402, 2197422, 2197418, 2192168, 2197091, 2188918,
         2189046, 2191125, 2187002, 2187832, 2189501, 2189694, 2196232]
for r in (Dte.objects.filter(id__in=ids14)
          .values('id', 'numero_documento', 'tipo_documento', 'estado_dte',
                  'fecha_emision', 'sucursal__alias')):
    dest = (Movimientos_Producto.objects
            .filter(dte_id=r['id'], concepto='TRASPASO_SALIDA')
            .values_list('sucursal_destino__alias', flat=True).first())
    p('  ', r, '-> destino', dest)

p('== 5. Movimiento ANULACION_REGULARIZACION del dte 2197402 ==')
for r in (Movimientos_Producto.objects
          .filter(dte_id=2197402, concepto='ANULACION_REGULARIZACION')
          .values('fecha', 'hora', 'responsable', 'sucursal_destino__alias')
          .annotate(u=Sum(Abs('cantidad')), n=Count('id'))
          .order_by('fecha')[:3]):
    p('  ', r)

p('== 6. SCOPING con usuario CON permiso (jefe_local/administracion) ==')
User = get_user_model()
factory = RequestFactory()
ESCRITURAS = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP', 'CREATE')


def llamar(fn_name, params, usuario, sucursal_id, etiqueta):
    from app import views_modulo_reportes_diferencias as V
    view = getattr(V, fn_name)
    request = factory.get('/_audit', data=params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    request.user = usuario
    request.session = {'idSucursalActual': sucursal_id, 'idEmpresaActual': None}
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        resp = view(request)
        dt = (time.perf_counter() - t0) * 1000
    malas = [q['sql'][:100] for q in ctx.captured_queries
             if (q.get('sql') or '').lstrip().upper().startswith(ESCRITURAS)]
    if malas:
        p('   !!!! ESCRITURAS:', malas)
    try:
        body = json.loads(resp.content.decode('utf-8'))
    except Exception:
        body = {}
    p(f'   [{etiqueta}] status={resp.status_code} queries={len(ctx.captured_queries)} t={dt:.0f}ms')
    return resp.status_code, body


emp_dte = {1802, 1319}  # empresas del dte 2197402
candidato = None
for u in (User.objects.filter(is_active=True, rol__in=('jefe_local', 'administracion'))
          .order_by('id')):
    emps = set(EmpresaUser.objects.filter(user=u, status=True).values_list('empresa_id', flat=True))
    if emps and not (emps & emp_dte):
        candidato = (u, emps)
        break
if candidato is None:
    p('   sin candidato sin cruce; busco cualquiera jefe_local/administracion con empresas acotadas')
    for u in (User.objects.filter(is_active=True, rol__in=('jefe_local', 'administracion'))
              .order_by('id')):
        emps = set(EmpresaUser.objects.filter(user=u, status=True).values_list('empresa_id', flat=True))
        if emps:
            candidato = (u, emps)
            break
if candidato:
    u, emps = candidato
    cruza = bool(emps & emp_dte)
    p('   usuario:', u.username, '| rol:', u.rol, '| empresas:', emps, '| cruza con dte 2197402:', cruza)
    suc_u = Sucursal.objects.filter(empresa_id__in=list(emps), activa=True).first()
    st, body = llamar('api_reporte_diferencias_recepcion',
                      {'fecha_desde': '2026-04-01', 'fecha_hasta': '2026-08-20'},
                      u, suc_u.id if suc_u else None, 'dif universo acotado')
    if st == 200:
        p('      universo acotado:', {k: body['resumen'][k] for k in ('lineas', 'documentos', 'faltantes')},
          '(admin veia 14130/978/57)')
    st, body = llamar('api_reporte_diferencias_recepcion',
                      {'fecha_desde': '2026-04-01', 'fecha_hasta': '2026-08-20', 'sucursal_id': '1'},
                      u, suc_u.id if suc_u else None, 'dif sucursal_id=1 (EDEL)')
    p('      sucursal EDEL ->', st, body.get('error', 'OK' if st == 200 else ''))
    if not cruza:
        st, body = llamar('api_detalle_mercaderia_transito', {'dte_id': '2197402'},
                          u, suc_u.id if suc_u else None, 'detalle dte ajeno')
        p('      detalle ajeno ->', st, body.get('error', ''))
    st, body = llamar('api_reporte_mercaderia_transito', {'dias': '90'},
                      u, suc_u.id if suc_u else None, 'transito acotado')
    if st == 200:
        p('      transito acotado resumen:', {k: body['resumen'][k] for k in
                                              ('documentos', 'unidades_enviadas', 'unidades_pendientes')},
          '(admin veia 516/23895/153)')
else:
    p('   NO MEDIDO: no hay usuario jefe_local/administracion con EmpresaUser')

p('== 7. transito dias=365 (admin): perf + truncado ==')
admin = User.objects.filter(rol='administrador', is_active=True).order_by('id').first()
st, body = llamar('api_reporte_mercaderia_transito', {'dias': '365'}, admin, 1, 'transito 365d')
if st == 200:
    p('   truncado:', body['truncado'], '| resumen:', body['resumen'])
