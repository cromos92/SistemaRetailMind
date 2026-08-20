# -*- coding: utf-8 -*-
"""Tanda 2 (SOLO LECTURA): oraculo kardex mov-sucursal, scoping restringido,
legacy: reporte_movimientos_kardex / kardex_agrupado / despachos_por_proveedor /
existencias. Julio 2026."""
import io
import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.getcwd())

import django

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.conf import settings
settings.DEBUG = True

from django.db import connection, reset_queries, transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from app.models import (Dte, Dte_Productos, EmpresaUser, Movimientos_Producto,
                        Producto, Producto_Talla, Sucursal)
from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO
from app.views import (obtener_existencias_reporte, reporte_despachos_por_proveedor,
                       reporte_kardex_agrupado, reporte_movimientos_kardex)
from app.views_modulo_reportes import (obtener_reporte_despachos_tiendas,
                                       obtener_reporte_movimientos_sucursal)

FI, FF = date(2026, 7, 1), date(2026, 7, 31)
User = get_user_model()
ESCR = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP')


def call(view, params, user, session=None):
    rf = RequestFactory()
    req = rf.get('/x', data=params)
    req.user = user
    req.session = session or {'idSucursalActual': None, 'idEmpresaActual': None}
    reset_queries()
    t0 = time.perf_counter()
    with transaction.atomic():
        resp = view(req)
        transaction.set_rollback(True)
    dt = round(time.perf_counter() - t0, 2)
    qs = list(connection.queries)
    writes = [q['sql'][:100] for q in qs
              if (q['sql'] or '').lstrip().upper().startswith(ESCR)]
    return resp, dt, len(qs), writes


admin = None
best_n = -1
for uid in User.objects.filter(rol='administrador', is_active=True).values_list('id', flat=True):
    n = EmpresaUser.objects.filter(user_id=uid, status=True).count()
    if n > best_n:
        admin_id, best_n = uid, n
admin = User.objects.get(id=admin_id)
emp_ids = list(set(EmpresaUser.objects.filter(user=admin, status=True)
                   .values_list('empresa_id', flat=True)))
suc_ids = list(Sucursal.objects.filter(empresa_id__in=emp_ids).values_list('id', flat=True))
print('ADMIN=%s' % admin.username)

# ================= C1: oraculo kardex articulo/sucursal (julio) =================
pA = {'marca_id': '299', 'fecha_desde': '2026-07-01', 'fecha_hasta': '2026-07-31',
      'mostrar': 'todo', 'solo_tiendas': 'true'}
rA, tA, qA, wA = call(obtener_reporte_movimientos_sucursal, pA, admin)
jA = json.loads(rA.content)
fila_t = suc_t = None
for f in jA.get('datos', []):
    for alias, d in f.get('sucursales', {}).items():
        if d.get('salidas', 0) > 0:
            fila_t, suc_t = f, (alias, d)
            break
    if fila_t:
        break
if fila_t:
    alias, d = suc_t
    art = fila_t['articulo']
    prods = list(Producto.objects.filter(
        articulo=art, sucursal_id=d['sucursal_id'], atributo1_id=299,
        excluir_de_analitica=False).values_list('id', flat=True))
    base_m = Movimientos_Producto.objects.filter(
        ProductoTalla__producto_id__in=prods, estado='COMPLETADO'
    ).exclude(concepto='INGRESO_INICIAL',
              referencia_externa=REF_SALDO_INICIAL_SINTETICO)
    stock_hoy = Producto_Talla.objects.filter(producto_id__in=prods).aggregate(
        s=Sum('stock'))['s'] or 0
    post = base_m.filter(fecha__gt=FF).aggregate(s=Sum('cantidad'))['s'] or 0
    ent = base_m.filter(fecha__gte=FI, fecha__lte=FF, cantidad__gt=0
                        ).aggregate(s=Sum('cantidad'))['s'] or 0
    sal = abs(base_m.filter(fecha__gte=FI, fecha__lte=FF, cantidad__lt=0
                            ).aggregate(s=Sum('cantidad'))['s'] or 0)
    fwd = base_m.filter(fecha__lte=FF).aggregate(s=Sum('cantidad'))['s'] or 0
    apert = Movimientos_Producto.objects.filter(
        ProductoTalla__producto_id__in=prods, estado='COMPLETADO',
        concepto='INGRESO_INICIAL', referencia_externa=REF_SALDO_INICIAL_SINTETICO
    ).aggregate(s=Sum('cantidad'))['s'] or 0
    restante_o = stock_hoy - post
    print('\nC1 ORACULO jul %s @ %s prods=%s' % (art, alias, prods))
    print('  reporte: original=%s actual=%s ent=%s sal=%s vendido=%s' %
          (d['stock_original'], d['stock_actual'], d['entradas'], d['salidas'], d['vendido']))
    print('  oraculo: original=%s actual=%s ent=%s sal=%s' %
          ((restante_o - (ent - sal)) + ent, restante_o, ent, sal))
    print('  forward<=31jul sin apertura=%s apertura=%s stock_hoy=%s post=%s' %
          (fwd, apert, stock_hoy, post))
else:
    print('\nC1: sin fila con salidas>0 (raro)')

# ================= C2: scoping usuario restringido =================
restr = None
for u in User.objects.filter(is_active=True, is_superuser=False).exclude(rol='administrador')[:80]:
    eus = set(EmpresaUser.objects.filter(user=u, status=True).values_list('empresa_id', flat=True))
    if eus and len(eus) < len(emp_ids):
        restr = u
        r_emp = eus
        break
if restr:
    r_suc = list(Sucursal.objects.filter(empresa_id__in=r_emp).values_list('id', flat=True))
    rB, tB, qB, wB = call(obtener_reporte_despachos_tiendas,
                          {'fecha_desde': '2026-07-01', 'fecha_hasta': '2026-07-31'}, restr)
    jB = json.loads(rB.content)
    o_desp = abs(Movimientos_Producto.objects.filter(
        estado='COMPLETADO', fecha__gte=FI, fecha__lte=FF,
        concepto='TRASPASO_SALIDA', sucursal_origen_id__in=r_suc
    ).aggregate(s=Sum('cantidad'))['s'] or 0)
    print('\nC2 RESTRINGIDO=%s empresas=%s: despachos reporte=%s oraculo=%s t=%ss' %
          (restr.username, sorted(r_emp), jB.get('resumen', {}).get('total_despachado'),
           o_desp, tB))
    rM, tM, qM, wM = call(obtener_reporte_movimientos_sucursal,
                          {'marca_id': '299', 'mostrar': 'todo', 'solo_tiendas': 'false'}, restr)
    jM = json.loads(rM.content)
    sucs_resp = {s['id'] for s in jM.get('sucursales', [])}
    print('  mov-sucursal: sucursales respuesta=%s subset_alcance=%s' %
          (sorted(sucs_resp), sucs_resp.issubset(set(r_suc))))
else:
    print('\nC2: no se encontro usuario restringido')

# ================= C3: volumetria legacy TRASPASO_SUCURSAL 2026 =================
leg = (Movimientos_Producto.objects
       .filter(concepto__in=('TRASPASO_SUCURSAL', 'TRASPASO_BODEGA', 'TRASPASO_VITRINA'),
               fecha__gte=date(2026, 1, 1))
       .annotate(mes=TruncMonth('fecha')).values('mes')
       .annotate(n=Count('id'), u=Sum('cantidad')).order_by('mes'))
print('\nC3 legacy TRASPASO_* por mes 2026: %s' %
      [(str(x['mes'])[:7], x['n'], x['u']) for x in leg])

# ================= C4: kardex por SKU =================
# SKU con apertura sintetica Y filas MIG: (candidato a doble conteo)
apert_pts = list(Movimientos_Producto.objects.filter(
    concepto='INGRESO_INICIAL', referencia_externa=REF_SALDO_INICIAL_SINTETICO,
    ProductoTalla__producto__sucursal_id__in=suc_ids
).values_list('ProductoTalla_id', flat=True)[:400])
dobles = list(Movimientos_Producto.objects.filter(
    ProductoTalla_id__in=apert_pts, referencia_externa__startswith='MIG:'
).values('ProductoTalla_id').annotate(n=Count('id')).order_by('-n')[:3])
# SKU con movimientos julio
jul_pts = list(Movimientos_Producto.objects.filter(
    fecha__gte=FI, fecha__lte=FF, estado='COMPLETADO',
    ProductoTalla__producto__sucursal_id__in=suc_ids)
    .values('ProductoTalla_id').annotate(n=Count('id')).order_by('-n')[:3])
objetivos = [x['ProductoTalla_id'] for x in dobles] + [jul_pts[0]['ProductoTalla_id']]
print('\nC4 kardex SKU: candidatos doble=%s top-julio=%s' %
      ([x['ProductoTalla_id'] for x in dobles], jul_pts[0]))
for pt_id in objetivos[:4]:
    page, last, blanks, total_items, mig_blank = 1, None, 0, 0, 0
    nq_tot, t_tot = 0, 0.0
    while page <= 8:
        r, t, nq, w = call(reporte_movimientos_kardex,
                           {'producto_talla_id': str(pt_id), 'page': str(page),
                            'page_size': '500'}, admin)
        nq_tot += nq
        t_tot += t
        j = json.loads(r.content)
        if not j.get('success'):
            print('  PT %s: ERROR %s' % (pt_id, j.get('error')))
            break
        items = j['items']
        total_items += len(items)
        for it in items:
            if not it['referencia']:
                blanks += 1
        if items:
            last = items[-1]
        if not j['pagination']['has_next']:
            break
        page += 1
    stock = Producto_Talla.objects.filter(id=pt_id).values_list('stock', flat=True).first()
    tiene_apert = pt_id in apert_pts
    print('  PT %s: movs=%s saldo_final=%s stock=%s %s | ref_vacias=%s | apertura=%s | q=%d t=%.1fs'
          % (pt_id, total_items, last and last['saldo'], stock,
             'OK' if last and last['saldo'] == stock else 'DESCUADRE',
             blanks, tiene_apert, nq_tot, t_tot))

# referencias vacias en ventana legacy (que mostraria el kardex)
blank_legacy = Movimientos_Producto.objects.filter(
    fecha__lt=date(2026, 1, 23), dte__isnull=True, ticket__isnull=True,
    sucursal_destino__isnull=True).filter(
    Q(referencia_externa__isnull=True) | Q(referencia_externa='')).count()
tot_legacy = Movimientos_Producto.objects.filter(fecha__lt=date(2026, 1, 23)).count()
print('  legacy(<23-ene): total=%s sin_referencia_visible=%s' % (tot_legacy, blank_legacy))

# ================= C5: kardex agrupado =================
prod_id = Producto_Talla.objects.filter(id=objetivos[-1]).values_list(
    'producto_id', flat=True).first()
r5, t5, q5, w5 = call(reporte_kardex_agrupado,
                      {'producto_id': str(prod_id), 'page': '1', 'page_size': '500'}, admin)
j5 = json.loads(r5.content)
if j5.get('success'):
    tot_mov = j5['pagination']['total_count']
    # ultima pagina para el saldo final
    lastp = j5['pagination']['total_pages']
    if lastp > 1:
        r5b, t5b, q5b, _ = call(reporte_kardex_agrupado,
                                {'producto_id': str(prod_id), 'page': str(lastp),
                                 'page_size': '500'}, admin)
        j5b = json.loads(r5b.content)
        last_saldo = j5b['items'][-1]['saldo'] if j5b['items'] else None
        t5 += t5b
        q5 += q5b
    else:
        last_saldo = j5['items'][-1]['saldo'] if j5['items'] else None
    print('\nC5 kardex_agrupado prod=%s: grupos=%s saldo_final=%s stock_total=%s %s | q=%d t=%.1fs writes=%s'
          % (prod_id, tot_mov, last_saldo, j5['producto']['stock_total'],
             'OK' if last_saldo == j5['producto']['stock_total'] else 'DESCUADRE',
             q5, t5, w5))
else:
    print('\nC5 ERROR: %s' % j5.get('error'))

# ================= C6: despachos por proveedor (julio) =================
p6 = {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31', 'page': '1',
      'page_size': '25'}
r6, t6, q6, w6 = call(reporte_despachos_por_proveedor, p6, admin)
j6 = json.loads(r6.content)
res6 = j6.get('resumen', {})
print('\nC6 despachos_por_proveedor jul: status=%s t=%ss q=%d writes=%s' %
      (r6.status_code, t6, q6, w6))
print('  resumen=%s' % json.dumps(res6, ensure_ascii=False))
# oraculos
excl = ['BOLETA ELECTRONICA', 'BOLETA PAPEL', 'TICKET']
dtes_o = (Dte.objects.filter(tipo_transaccion='COMPRA', fecha_emision__gte='2026-07-01',
                             fecha_emision__lte='2026-07-31')
          .exclude(tipo_documento__in=excl).exclude(emisor_id=F('receptor_id')))
ids_o = list(dtes_o.values_list('id', flat=True))
ing_o = Movimientos_Producto.objects.filter(dte_id__in=ids_o, tipo_movimiento='INGRESO'
                                            ).aggregate(s=Sum('cantidad'))['s'] or 0
egr_conceptos = list(Movimientos_Producto.objects.filter(
    dte_id__in=ids_o, tipo_movimiento='EGRESO').values('concepto')
    .annotate(n=Count('id'), u=Sum('cantidad')).order_by('u'))
print('  oraculo: dtes=%s (reporte=%s) unidades_ingresadas=%s (reporte=%s)' %
      (len(ids_o), res6.get('total_dtes'), ing_o, res6.get('total_unidades_ingresadas')))
print('  EGRESOS colgados de DTE compra jul (lo que el reporte llama "despachado"): %s'
      % egr_conceptos)

# ================= C7: existencias legacy =================
# sucursal chica del alcance
suc_chica = (Producto_Talla.objects.filter(producto__sucursal_id__in=suc_ids)
             .values('producto__sucursal_id', 'producto__sucursal__alias')
             .annotate(n=Count('id')).order_by('n').first())
sid = suc_chica['producto__sucursal_id']
print('\nC7 existencias: sucursal chica=%s (%s filas PT)' %
      (suc_chica['producto__sucursal__alias'], suc_chica['n']))
r7, t7, q7, w7 = call(obtener_existencias_reporte,
                      {'sucursal_id': str(sid), 'pagina': '1', 'por_pagina': '100'}, admin)
j7 = json.loads(r7.content)
if j7.get('success'):
    k = j7['kpis_globales']
    pt_o = Producto_Talla.objects.filter(producto__sucursal_id=sid,
                                         producto__excluir_de_analitica=False)
    o_stock = pt_o.aggregate(s=Sum('stock'))['s'] or 0
    o_venta = pt_o.aggregate(s=Sum(F('stock') * F('producto__precioventa')))['s'] or 0
    print('  API: t=%ss q=%d writes=%s' % (t7, q7, w7))
    print('  kpi total_stock=%s oraculo=%s %s' %
          (k['total_stock'], o_stock, 'OK' if k['total_stock'] == o_stock else 'DELTA'))
    print('  kpi valor_venta=%s oraculo=%s %s' %
          (int(k['valor_venta_total']), int(o_venta),
           'OK' if int(k['valor_venta_total']) == int(o_venta) else 'DELTA'))
else:
    print('  ERROR: %s' % j7.get('error'))

# volumetria del EXPORT (sin invocarlo): universo sin scoping ni analitica
n_export = Producto_Talla.objects.filter(producto__isnull=False,
                                         producto__sucursal__isnull=False).count()
n_api_admin = Producto_Talla.objects.filter(
    producto__sucursal_id__in=suc_ids, producto__excluir_de_analitica=False).count()
n_excluidos = Producto_Talla.objects.filter(
    producto__sucursal__isnull=False, producto__excluir_de_analitica=True).count()
print('  EXPORT universo=%s filas PT (API admin=%s; filas de excluidos analitica=%s)'
      % (n_export, n_api_admin, n_excluidos))
print('  EXPORT estimado queries: 2 hojas x %s filas x ~2 q/fila = ~%s queries N+1'
      % (n_export, 4 * n_export))

print('\nFIN T2')
