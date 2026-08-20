# -*- coding: utf-8 -*-
"""AUDITORIA VENTAS CORE - tanda 2 (SOLO LECTURA). Scoping + flecos.
OJO consola interactiva: blank line tras cada bloque compuesto ANTES de
la siguiente sentencia top-level.
"""
import json
import sys
import time
from datetime import date

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from django.conf import settings
from django.db import connection, reset_queries, transaction
from django.db.models import Count, F, Q, Sum
from django.test import RequestFactory
from django.utils import timezone as _tz
from app.models import Dte, Dte_Productos, EmpresaUser, Sucursal, Ticket
from app.utils_permisos import (usuario_puede_ver_todas_sucursales,
                                obtener_sucursales_usuario, ids_sucursales_alcance)
from django.contrib.auth import get_user_model

settings.DEBUG = True
FI = date(2026, 7, 1)
FF = date(2026, 7, 31)
SEP = '=' * 78
I = lambda x: int(x or 0)


def _import_view(path):
    mod_name, fn = path.rsplit('.', 1)
    mod = __import__(mod_name, fromlist=[fn])
    return getattr(mod, fn)


def invocar(path, params, user, sucursal_id=None, empresa_id=None):
    factory = RequestFactory()
    req = factory.get('/_aud', data=params)
    req.user = user
    req.session = {'idSucursalActual': sucursal_id, 'idEmpresaActual': empresa_id}
    out = {'status': None, 'json': None, 'nq': None, 'ms': None, 'err': None}
    reset_queries()
    t0 = time.perf_counter()
    try:
        with transaction.atomic():
            resp = _import_view(path)(req)
            transaction.set_rollback(True)
    except Exception as e:
        out['err'] = f'{type(e).__name__}: {e}'
        return out
    out['ms'] = round((time.perf_counter() - t0) * 1000)
    out['nq'] = len(connection.queries)
    out['status'] = resp.status_code
    try:
        out['json'] = json.loads(resp.content)
    except Exception:
        pass
    return out


User = get_user_model()
ADMIN = User.objects.filter(rol='administrador', is_active=True).first()


def buscar_restringido():
    for u in User.objects.filter(is_active=True, is_superuser=False).exclude(rol='administrador')[:80]:
        eus = list(EmpresaUser.objects.filter(user=u, status=True)
                   .values_list('empresa_id', 'sucursal_id'))
        if not eus:
            continue
        empresas = {e for e, _ in eus}
        try:
            if usuario_puede_ver_todas_sucursales(u):
                continue
        except Exception:
            continue
        suc = next((s for _, s in eus if s), None)
        if not suc:
            suc = Sucursal.objects.filter(empresa_id__in=empresas).values_list('id', flat=True).first()
        return (u, suc, sorted(empresas))
    return None


RESTR = buscar_restringido()
print('restringido =', RESTR and (RESTR[0].username, getattr(RESTR[0], 'rol', '?'), 'suc', RESTR[1], 'empresas', RESTR[2]))

print('\n' + SEP + '\nS9 SCOPING CON USUARIO RESTRINGIDO\n' + SEP)
try:
    if not RESTR:
        print('no hay usuario restringido utilizable: no medido')
    else:
        u, suc, emps = RESTR
        n_asig = obtener_sucursales_usuario(u).count()
        alc = ids_sucursales_alcance(u)
        print(f"sucursales asignadas (EmpresaUser c/sucursal, activas)={n_asig}  "
              f"alcance por empresa={len(alc) if alc is not None else 'TODAS'}")
        rr = invocar('app.views_modulo_reportes.obtener_ventas_global_por_empresa',
                     {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, u,
                     sucursal_id=suc, empresa_id=emps[0])
        jsr = rr['json'] or {}
        emp_ids = [e.get('id') for e in jsr.get('empresas') or []]
        sucs_vistas = sorted({s['id'] for e in jsr.get('empresas') or [] for s in e.get('sucursales') or []})
        fuga = [e for e in emp_ids if e not in emps]
        print(f"ventas-global: status={rr['status']} err={rr['err']} ve empresas={emp_ids} sucursales={sucs_vistas}")
        print(f"  FUGA multi-empresa: {fuga if fuga else 'NO'}")
        rs = invocar('app.views_modulo_reportes.obtener_ventas_por_sucursal_reporte',
                     {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, u,
                     sucursal_id=suc, empresa_id=emps[0])
        jss = rs['json'] or {}
        filas = jss.get('sucursales') or []
        ajenas = []
        for f_ in filas:
            emp_de_suc = Sucursal.objects.filter(id=f_['id']).values_list('empresa_id', flat=True).first()
            if emp_de_suc not in emps:
                ajenas.append((f_['id'], emp_de_suc))
        print(f"ventas-sucursal: status={rs['status']} err={rs['err']} filas={[(f_['id'], f_['nombre']) for f_ in filas]}")
        print(f"  filas de OTRA empresa: {ajenas if ajenas else 'NO'}")
        rc = invocar('app.views_modulo_reportes.obtener_comisiones_por_vendedor',
                     {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, u,
                     sucursal_id=suc, empresa_id=emps[0])
        jc = rc['json'] or {}
        print(f"comisiones: status={rc['status']} success={jc.get('success')} err={jc.get('error') or rc['err']}")
        if rc['status'] == 200 and jc.get('success'):
            emps_c = sorted({v.get('empresa_id') for v in jc.get('vendedores') or [] if v.get('empresa_id')})
            print(f"  empresas en filas: {emps_c} (permitidas {emps})  FUGA: {[e for e in emps_c if e not in emps] or 'NO'}")
        rx = invocar('app.views_modulo_reportes.exportar_comisiones_vendedor_excel',
                     {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, u,
                     sucursal_id=suc, empresa_id=emps[0])
        print(f"export comisiones: status={rx['status']} err={(rx['json'] or {}).get('error') if rx['json'] else rx['err']}")
        rv = invocar('app.views_modulo_reportes.obtener_ventas_comparativo',
                     {'tipo_flujo': 'custom', 'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, u,
                     sucursal_id=suc, empresa_id=emps[0])
        jv = rv['json'] or {}
        sucs_cmp = sorted({s['id'] for s in (jv.get('sucursales') or []) if s.get('id')})
        ajenas2 = [s for s in sucs_cmp
                   if Sucursal.objects.filter(id=s).values_list('empresa_id', flat=True).first() not in emps]
        print(f"ventas-comparativo: status={rv['status']} err={rv['err']} sucursales={sucs_cmp} ajenas={ajenas2 or 'NO'}")
        rm = invocar('app.views_modulo_reportes.obtener_comparativa_mensual', {}, u,
                     sucursal_id=suc, empresa_id=emps[0])
        jm = rm['json'] or {}
        series_n = [s.get('name') for s in jm.get('series') or []]
        print(f"comparativa-mensual: status={rm['status']} err={rm['err']} series={series_n}")
except Exception as e:
    print('S9 ERROR:', type(e).__name__, e)

print('\n' + SEP + '\nS10 TICKETS: folio_dte / drift fecha / doble conteo (fix)\n' + SEP)
try:
    tk = Ticket.objects.filter(created_at__date__gte=FI, created_at__date__lte=FF,
                               estado='PAGADO',
                               modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'])
    tsd = tk.filter(dte_generado=False)
    con_folio = tsd.filter(folio_dte__isnull=False)
    a = con_folio.aggregate(n=Count('id'), m=Sum('total'))
    print(f"tickets sin dte con folio_dte not null: n={a['n']} monto={I(a['m']):,}")
    folios = [str(x) for x in con_folio.values_list('folio_dte', flat=True)]
    if folios:
        dmatch = Dte.objects.filter(fecha_emision__gte=FI, fecha_emision__lte=FF,
                                    numero_documento__in=folios,
                                    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'])\
            .exclude(tipo_documento='NOTA DE CREDITO')\
            .exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])
        am = dmatch.aggregate(n=Count('id'), m=Sum('monto_con_iva'))
        print(f"  DTE vigentes con ese folio (doble conteo real): n={am['n']} monto={I(am['m']):,}")
    drift = 0
    tot = 0
    for t in tk.only('fecha', 'created_at').iterator(chunk_size=2000):
        tot += 1
        if t.fecha != _tz.localtime(t.created_at).date():
            drift += 1
    print(f"tickets julio con Ticket.fecha != date(created_at): {drift}/{tot}")
except Exception as e:
    print('S10 ERROR:', type(e).__name__, e)

print('\n' + SEP + '\nS11 UNIVERSO ventas-sucursal por tipo_documento\n' + SEP)
try:
    ob = Dte.objects.filter(fecha_emision__gte=FI, fecha_emision__lte=FF,
                            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
                            estado_dte__in=['EMITIDO', 'ACEPTADO', 'ANULADO'],
                            descartado=False, sucursal__isnull=False)
    for r in ob.values('tipo_documento', 'tipo_transaccion').annotate(n=Count('id'), m=Sum('monto_con_iva')).order_by('-m'):
        print(f"  {r['tipo_documento']!s:22} tt={r['tipo_transaccion']!s:14} n={r['n']:5} monto={I(r['m']):>13,}")
    bp_hist = Dte.objects.filter(tipo_documento='BOLETA PAPEL').values('fecha_emision__year')\
        .annotate(n=Count('id'), m=Sum('monto_con_iva')).order_by('fecha_emision__year')
    print('BOLETA PAPEL historicas por anio:')
    for r in bp_hist:
        print(f"  {r['fecha_emision__year']}: n={r['n']} monto={I(r['m']):,}")
except Exception as e:
    print('S11 ERROR:', type(e).__name__, e)

print('\n' + SEP + '\nS12 %DCTO: semantica de Dte.descuento (muestra julio)\n' + SEP)
try:
    qd = Dte.objects.filter(fecha_emision__gte=FI, fecha_emision__lte=FF,
                            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
                            descuento__gt=0, descartado=False,
                            estado_dte__in=['EMITIDO', 'ACEPTADO'])\
        .exclude(tipo_documento='NOTA DE CREDITO')
    ag = qd.aggregate(n=Count('id'), d=Sum('descuento'), m=Sum('monto_con_iva'))
    print(f"DTEs venta julio con descuento>0: n={ag['n']} sum_dcto={I(ag['d']):,} sum_monto={I(ag['m']):,}")
    ok_lineas = 0
    rev = 0
    for d in qd.order_by('-descuento')[:12]:
        lineas = Dte_Productos.objects.filter(dte=d).aggregate(
            mi=Sum('monto_item'),
            pl=Sum(F('precio') * F('stock')))
        mi, pl = I(lineas['mi']), I(lineas['pl'])
        rev += 1
        estado = 'monto+dcto==precioLista' if abs((I(d.monto_con_iva) + I(d.descuento)) - pl) <= max(5, pl * 0.005) else 'NO cuadra'
        if estado.startswith('monto+dcto'):
            ok_lineas += 1
        print(f"  dte={d.id} {d.tipo_documento[:18]:18} monto_iva={I(d.monto_con_iva):>9,} dcto={I(d.descuento):>8,} "
              f"lineas_monto_item={mi:>9,} lineas_precioxstock={pl:>9,} -> {estado}")
    print(f"muestra: {ok_lineas}/{rev} cumplen monto_con_iva+descuento ~= sum(precio*stock)")
except Exception as e:
    print('S12 ERROR:', type(e).__name__, e)

print('\n' + SEP + '\nS13 UNIDADES comparativo: Dte.unidades_productos vs lineas\n' + SEP)
try:
    qs = Dte.objects.filter(fecha_emision__gte=FI, fecha_emision__lte=FF,
                            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'])\
        .exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])\
        .exclude(receptor__isnull=False, receptor_id=F('emisor_id'))\
        .exclude(tipo_documento='NOTA DE CREDITO')
    u_campo = I(qs.aggregate(u=Sum('unidades_productos'))['u'])
    u_lineas = I(Dte_Productos.objects.filter(dte__in=qs).aggregate(u=Sum('stock'))['u'])
    nulos = qs.filter(unidades_productos__isnull=True).count()
    print(f"sum(Dte.unidades_productos)={u_campo:,}  sum(lineas.stock)={u_lineas:,}  "
          f"delta={u_campo - u_lineas:,}  dtes con campo NULL={nulos}")
except Exception as e:
    print('S13 ERROR:', type(e).__name__, e)

print('\n' + SEP + '\nS14 CONSISTENCIA ventas-vendedor: KPI brutas y devoluciones\n' + SEP)
try:
    r2 = invocar('app.views_modulo_reportes.obtener_ventas_por_vendedor_reporte',
                 {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, ADMIN)
    js2 = r2['json'] or {}
    k = js2.get('kpis') or {}
    vend = js2.get('vendedores') or []
    dev_col = sum(I(v.get('devoluciones')) for v in vend if not v.get('sin_vendedor'))
    print(f"KPI: total_ventas={I(k.get('total_ventas')):,} brutas={I(k.get('total_ventas_brutas')):,} "
          f"devoluciones={I(k.get('total_devoluciones')):,} docs={k.get('total_documentos')} ticket_prom={k.get('ticket_promedio')}")
    print(f"columna devoluciones (filas con vendedor): {dev_col:,}  ->  NC julio reales: 6,066,392")
    con_dev = [v for v in vend if I(v.get('devoluciones')) > 0]
    print(f"filas con devoluciones>0: {len(con_dev)} (de {len(vend)})  "
          f"-> la columna esta muerta si 0 con vendedor real")
except Exception as e:
    print('S14 ERROR:', type(e).__name__, e)

print('\nFIN TANDA 2')
