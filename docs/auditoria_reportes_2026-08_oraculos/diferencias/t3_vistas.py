# -*- coding: utf-8 -*-
# AUDITORIA read-only - tanda 3: invocar vistas reales (RequestFactory),
# comparar contra oraculo SQL crudo, medir queries/tiempo, probar scoping.
# SOLO GET/SELECT. Guarda anti-escritura al estilo _test_reportes_readonly.
import os
import sys
import time
import json

import django

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model

from app.models import EmpresaUser, Dte, Sucursal

ESCRITURAS = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP', 'CREATE')


def p(*a):
    print(*a, flush=True)


def check_solo_selects(queries):
    malas = [q['sql'][:120] for q in queries
             if (q.get('sql') or '').lstrip().upper().startswith(ESCRITURAS)]
    if malas:
        p('!!!! ESCRITURAS DETECTADAS:', malas)
    return malas


factory = RequestFactory()
User = get_user_model()

admin = User.objects.filter(rol='administrador', is_active=True).order_by('id').first()
p('usuario admin de prueba:', admin.username, '| rol:', admin.rol)

# sucursal de sesion del admin: cualquiera activa
suc_admin = Sucursal.objects.filter(activa=True).order_by('id').first()
p('sucursal sesion:', suc_admin.id, suc_admin.alias)


def llamar(vista_path, params, usuario, sucursal_id, etiqueta):
    mod_name, fn_name = vista_path.rsplit('.', 1)
    mod = __import__(mod_name, fromlist=[fn_name])
    view = getattr(mod, fn_name)
    request = factory.get('/_audit', data=params,
                          HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    request.user = usuario
    request.session = {'idSucursalActual': sucursal_id, 'idEmpresaActual': None}
    with CaptureQueriesContext(connection) as ctx:
        t0 = time.perf_counter()
        resp = view(request)
        dt = (time.perf_counter() - t0) * 1000
    check_solo_selects(ctx.captured_queries)
    body = None
    try:
        body = json.loads(resp.content.decode('utf-8'))
    except Exception:
        body = {'_no_json': resp.content[:200]}
    p(f'[{etiqueta}] status={resp.status_code} queries={len(ctx.captured_queries)} tiempo={dt:.0f}ms')
    return resp.status_code, body, len(ctx.captured_queries), dt


V = 'app.views_modulo_reportes_diferencias'

# ---------------------------------------------------------------- diferencias
p('== 1. api_reporte_diferencias_recepcion (admin) ==')
# ventana abril-agosto: donde viven los 57 faltantes historicos
st, body, nq, dt = llamar(
    f'{V}.api_reporte_diferencias_recepcion',
    {'fecha_desde': '2026-04-01', 'fecha_hasta': '2026-08-20', 'tipo': 'todas',
     'page_size': '50'},
    admin, suc_admin.id, 'dif abr-ago todas')
if st == 200:
    r = body['resumen']
    p('   resumen API:', {k: r[k] for k in ('lineas', 'documentos', 'esperadas', 'recibidas',
                                            'faltantes', 'danadas', 'sobrantes',
                                            'valor_faltante', 'valor_danado', 'valor_sobrante',
                                            'valor_problema', 'pct_diferencia')})

# oraculo SQL crudo (mismo universo, camino independiente)
SQL = """
SELECT COUNT(*),
       COUNT(DISTINCT pr.dte_id),
       COALESCE(SUM(pr.cantidad_esperada),0),
       COALESCE(SUM(pr."stockArribado"),0),
       COALESCE(SUM(pr.cantidad_faltante),0),
       COALESCE(SUM(pr.cantidad_danada),0),
       COALESCE(SUM(pr.cantidad_sobrante),0),
       COALESCE(SUM(pr.cantidad_faltante * COALESCE(dp.costo,0)),0),
       COALESCE(SUM(pr.cantidad_danada  * COALESCE(dp.costo,0)),0),
       COALESCE(SUM(pr.cantidad_sobrante* COALESCE(dp.costo,0)),0)
FROM app_productos_recepcionados pr
JOIN app_dte d ON d.id = pr.dte_id
LEFT JOIN app_dte_productos dp ON dp.id = pr.dte_producto_id
WHERE d.descartado = false
  AND d.estado_dte NOT IN ('ANULADO','CANCELADO','RECHAZADO')
  AND (
        (pr.fecha_recepcion IS NOT NULL
         AND (pr.fecha_recepcion AT TIME ZONE 'America/Santiago')::date BETWEEN %s AND %s)
     OR (pr.fecha_recepcion IS NULL AND pr.fecha BETWEEN %s AND %s)
  )
"""
with connection.cursor() as cur:
    cur.execute(SQL, ['2026-04-01', '2026-08-20', '2026-04-01', '2026-08-20'])
    row = cur.fetchone()
p('   oraculo SQL :', {'lineas': row[0], 'documentos': row[1], 'esperadas': int(row[2]),
                       'recibidas': int(row[3]), 'faltantes': int(row[4]), 'danadas': int(row[5]),
                       'sobrantes': int(row[6]), 'valor_faltante': int(row[7]),
                       'valor_danado': int(row[8]), 'valor_sobrante': int(row[9])})

p('== 1b. filtro tipo=faltante, guia oraculo 16933 (13 faltantes) ==')
st, body, nq, dt = llamar(
    f'{V}.api_reporte_diferencias_recepcion',
    {'fecha_desde': '2026-04-01', 'fecha_hasta': '2026-08-20', 'tipo': 'faltante',
     'page_size': '200'},
    admin, suc_admin.id, 'dif tipo=faltante')
if st == 200:
    r = body['resumen']
    p('   resumen faltante:', {k: r[k] for k in ('lineas', 'documentos', 'faltantes', 'valor_faltante')})
    filas_16933 = [d for d in body['datos'] if d['folio'] == 16933]
    p(f'   filas folio 16933 en detalle: {len(filas_16933)} | faltantes={sum(f["faltante"] for f in filas_16933)}')
    for f in filas_16933[:3]:
        p('     ', {k: f[k] for k in ('sku', 'talla', 'esperado', 'recibido', 'faltante',
                                      'estado', 'costo_unitario', 'valor_problema', 'sucursal')})
    p('   filas folio 17003:', [
        {k: f[k] for k in ('sku', 'esperado', 'recibido', 'faltante', 'estado', 'valor_problema')}
        for f in body['datos'] if f['folio'] == 17003])
    p('   por_sucursal:', body['por_sucursal'])

p('== 1c. filtro sucursal con faltantes (sucursal_destino NULL) ==')
st, body, nq, dt = llamar(
    f'{V}.api_reporte_diferencias_recepcion',
    {'fecha_desde': '2026-04-01', 'fecha_hasta': '2026-08-20', 'tipo': 'faltante',
     'sucursal_id': str(suc_admin.id)},
    admin, suc_admin.id, f'dif faltante+suc={suc_admin.id}')
if st == 200:
    p('   con filtro sucursal -> lineas:', body['resumen']['lineas'],
      'faltantes:', body['resumen']['faltantes'])

p('== 1d. ventana por defecto (30d) ==')
st, body, nq, dt = llamar(
    f'{V}.api_reporte_diferencias_recepcion', {},
    admin, suc_admin.id, 'dif default 30d')
if st == 200:
    r = body['resumen']
    p('   default:', {k: r[k] for k in ('lineas', 'faltantes', 'danadas', 'sobrantes',
                                        'fecha_desde', 'fecha_hasta')})

# ---------------------------------------------------------------- transito
p('== 2. api_reporte_mercaderia_transito dias=90 (admin) ==')
st, body, nq, dt = llamar(
    f'{V}.api_reporte_mercaderia_transito', {'dias': '90'},
    admin, suc_admin.id, 'transito 90d')
if st == 200:
    p('   resumen API:', body['resumen'])
    p('   truncado:', body['truncado'], '| total_despachos:', body['total_despachos'])
    fila_17098 = [d for d in body['despachos'] if d['folio'] == 17098]
    p('   fila folio 17098 (llego-todo):', fila_17098)

p('== 2b. detalle dte=2197402 (folio 17098) ==')
st, body, nq, dt = llamar(
    f'{V}.api_detalle_mercaderia_transito', {'dte_id': '2197402'},
    admin, suc_admin.id, 'detalle 17098')
if st == 200:
    p('   totales detalle:', body['totales'])
    pend = [f for f in body['detalle'] if f['pendientes'] > 0]
    p('   skus con pendiente:', pend[:5])

p('== 2c. detalle dte=2189501 (folio 17003, cruce con diferencias) ==')
st, body, nq, dt = llamar(
    f'{V}.api_detalle_mercaderia_transito', {'dte_id': '2189501'},
    admin, suc_admin.id, 'detalle 17003')
if st == 200:
    p('   totales detalle:', body['totales'])
    p('   skus pendientes:', [f for f in body['detalle'] if f['pendientes'] > 0])

# ---------------------------------------------------------------- scoping
p('== 3. SCOPING: usuario no-admin de otra empresa ==')
dte_obj = Dte.objects.select_related('sucursal').get(id=2197402)
emp_dte = {dte_obj.emisor_id, dte_obj.receptor_id}
if dte_obj.sucursal_id:
    emp_dte.add(dte_obj.sucursal.empresa_id)
p('empresas del dte 2197402 (emisor/receptor/suc):', emp_dte)

candidato = None
for u in (User.objects.filter(is_active=True).exclude(rol='administrador')
          .exclude(is_superuser=True).order_by('id')[:60]):
    emps = set(EmpresaUser.objects.filter(user=u, status=True).values_list('empresa_id', flat=True))
    if emps and not (emps & emp_dte):
        try:
            from app.models import PermisoUsuario as PU
            if PU.usuario_ve_todas_sucursales(u):
                continue
        except Exception:
            pass
        candidato = (u, emps)
        break
if candidato:
    u, emps = candidato
    p('usuario acotado:', u.username, '| rol:', u.rol, '| empresas:', emps)
    suc_u = Sucursal.objects.filter(empresa_id__in=list(emps), activa=True).first()
    st, body, nq, dt = llamar(
        f'{V}.api_detalle_mercaderia_transito', {'dte_id': '2197402'},
        u, suc_u.id if suc_u else None, 'detalle ajeno (espera 403)')
    p('   detalle dte ajeno -> status', st, body.get('error'))
    st, body, nq, dt = llamar(
        f'{V}.api_reporte_diferencias_recepcion',
        {'fecha_desde': '2026-04-01', 'fecha_hasta': '2026-08-20',
         'sucursal_id': str(suc_admin.id if suc_admin.empresa_id not in emps else 0)},
        u, suc_u.id if suc_u else None, 'dif sucursal ajena (espera 403)')
    p('   dif sucursal ajena -> status', st, body.get('error') or body.get('mensaje'))
    st, body, nq, dt = llamar(
        f'{V}.api_reporte_diferencias_recepcion',
        {'fecha_desde': '2026-04-01', 'fecha_hasta': '2026-08-20'},
        u, suc_u.id if suc_u else None, 'dif universo acotado')
    if st == 200:
        p('   universo del usuario acotado:', {k: body['resumen'][k] for k in ('lineas', 'documentos', 'faltantes')})
    else:
        p('   status', st, body.get('error') or body.get('mensaje'))
else:
    p('NO se encontro usuario acotado sin cruce de empresas (no medido)')

p('== 4. paginas HTML (render basico) ==')
for vista in ('ver_reporte_diferencias_recepcion', 'ver_reporte_mercaderia_transito'):
    try:
        st, body, nq, dt = llamar(f'{V}.{vista}', {}, admin, suc_admin.id, vista)
    except Exception as e:
        p(f'   {vista} -> EXCEPCION: {type(e).__name__}: {e}')
