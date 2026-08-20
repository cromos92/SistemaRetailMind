# -*- coding: utf-8 -*-
"""Tanda 1 (SOLO LECTURA): movimientos-sucursal + despachos-tiendas. Julio 2026."""
import io
import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.getcwd())  # se ejecuta desde retailmind/

import django

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.conf import settings
settings.DEBUG = True  # habilita connection.queries (solo en memoria)

from django.db import connection, reset_queries, transaction
from django.db.models import Count, F, Q, Sum
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from app.models import (EmpresaUser, Movimientos_Producto, Producto,
                        Producto_Talla, Sucursal)
from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO
from app.views_modulo_reportes import (exportar_movimientos_sucursal_excel,
                                       obtener_reporte_despachos_tiendas,
                                       obtener_reporte_movimientos_sucursal)

FI, FF = date(2026, 7, 1), date(2026, 7, 31)
User = get_user_model()

ESCR = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP')


def call(view, params, user):
    rf = RequestFactory()
    req = rf.get('/x', data=params)
    req.user = user
    req.session = {'idSucursalActual': None, 'idEmpresaActual': None}
    reset_queries()
    t0 = time.perf_counter()
    with transaction.atomic():
        resp = view(req)
        transaction.set_rollback(True)
    dt = round(time.perf_counter() - t0, 2)
    qs = list(connection.queries)
    nq = len(qs)
    writes = [q['sql'][:100] for q in qs
              if (q['sql'] or '').lstrip().upper().startswith(ESCR)]
    return resp, dt, nq, writes


# ---------- usuario admin con EmpresaUser mas amplio ----------
admins = list(User.objects.filter(rol='administrador', is_active=True)
              .values_list('id', 'username'))
best, best_n = None, -1
for uid, uname in admins:
    n = EmpresaUser.objects.filter(user_id=uid, status=True).count()
    if n > best_n:
        best, best_n = uid, n
admin = User.objects.get(id=best)
emp_ids = list(EmpresaUser.objects.filter(user=admin, status=True)
               .values_list('empresa_id', flat=True))
suc_all = list(Sucursal.objects.filter(empresa_id__in=emp_ids)
               .values('id', 'alias', 'es_centro_distribucion', 'tipo_sucursal'))
suc_ids = [s['id'] for s in suc_all]
tiendas_ids = [s['id'] for s in suc_all
               if not (s['es_centro_distribucion'] or s['tipo_sucursal'] == 'CENTRO_DISTRIBUCION')]
print('ADMIN=%s empresas=%s sucursales=%d (tiendas=%d)' %
      (admin.username, emp_ids, len(suc_ids), len(tiendas_ids)))

# ---------- elegir marca acotada con movimientos de julio ----------
top_marcas = list(
    Movimientos_Producto.objects
    .filter(fecha__gte=FI, fecha__lte=FF, estado='COMPLETADO',
            ProductoTalla__producto__sucursal_id__in=suc_ids)
    .exclude(ProductoTalla__producto__atributo1__isnull=True)
    .values('ProductoTalla__producto__atributo1_id',
            'ProductoTalla__producto__atributo1__valor')
    .annotate(n=Count('id')).order_by('-n')[:8])
marca_id = marca_nom = None
for m in top_marcas:
    mid = m['ProductoTalla__producto__atributo1_id']
    nprod = Producto.objects.filter(atributo1_id=mid, sucursal_id__in=suc_ids,
                                    excluir_de_analitica=False).count()
    print('  marca cand: %s movs_jul=%d productos=%d' %
          (m['ProductoTalla__producto__atributo1__valor'], m['n'], nprod))
    if marca_id is None and 30 <= nprod <= 450:
        marca_id, marca_nom = mid, m['ProductoTalla__producto__atributo1__valor']
if marca_id is None:
    marca_id = top_marcas[-1]['ProductoTalla__producto__atributo1_id']
    marca_nom = top_marcas[-1]['ProductoTalla__producto__atributo1__valor']
print('MARCA ELEGIDA: %s (id=%s)' % (marca_nom, marca_id))

base_params = {'marca_id': str(marca_id), 'fecha_desde': '2026-07-01',
               'fecha_hasta': '2026-07-31', 'mostrar': 'todo',
               'solo_tiendas': 'true'}

# ---------- A1: API sin descripcion ----------
r_off, t_off, q_off, w_off = call(obtener_reporte_movimientos_sucursal,
                                  base_params, admin)
j_off = json.loads(r_off.content)
print('\nA1 API(desc OFF): status=%s ok=%s filas=%s t=%ss q=%d writes=%s' %
      (r_off.status_code, j_off.get('success'), len(j_off.get('datos', [])),
       t_off, q_off, w_off))

# ---------- A2: API con descripcion ----------
p2 = dict(base_params); p2['incluir_descripcion'] = 'true'
r_on, t_on, q_on, w_on = call(obtener_reporte_movimientos_sucursal, p2, admin)
j_on = json.loads(r_on.content)
print('A2 API(desc ON):  filas=%s t=%ss q=%d writes=%s' %
      (len(j_on.get('datos', [])), t_on, q_on, w_on))


def tot(j, campo):
    return sum(f.get(campo, 0) or 0 for f in j.get('datos', []))

for campo in ('total_stock_original', 'total_stock_actual', 'total_entradas',
              'total_salidas', 'total_vendido'):
    a, b = tot(j_off, campo), tot(j_on, campo)
    print('  %-22s off=%-8s on=%-8s %s' % (campo, a, b, 'OK' if a == b else 'DELTA!'))
con_desc_off = sum(1 for f in j_off.get('datos', []) if 'descripcion' in f)
con_desc_on = sum(1 for f in j_on.get('datos', []) if 'descripcion' in f)
print('  filas con clave descripcion: off=%d on=%d' % (con_desc_off, con_desc_on))
print('  flag en respuesta: off=%s on=%s' %
      (j_off.get('incluir_descripcion'), j_on.get('incluir_descripcion')))

# ---------- A3: oraculo kardex para 1 articulo/sucursal ----------
fila_target = None
suc_target = None
for f in j_off.get('datos', []):
    for alias, d in f.get('sucursales', {}).items():
        if d.get('entradas', 0) > 0 and d.get('salidas', 0) > 0:
            fila_target, suc_target = f, (alias, d)
            break
    if fila_target:
        break
if fila_target:
    alias, d = suc_target
    art = fila_target['articulo']
    suc_id = d['sucursal_id']
    prods = list(Producto.objects.filter(
        articulo=art, sucursal_id=suc_id, atributo1_id=marca_id,
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
    restante_o = stock_hoy - post
    saldo_ini_o = restante_o - (ent - sal)
    original_o = saldo_ini_o + ent
    # camino independiente hacia adelante (todo el kardex hasta FF)
    fwd_sin_apertura = base_m.filter(fecha__lte=FF).aggregate(s=Sum('cantidad'))['s'] or 0
    apertura = Movimientos_Producto.objects.filter(
        ProductoTalla__producto_id__in=prods, estado='COMPLETADO',
        concepto='INGRESO_INICIAL',
        referencia_externa=REF_SALDO_INICIAL_SINTETICO
    ).aggregate(s=Sum('cantidad'))['s'] or 0
    print('\nA3 ORACULO %s @ %s (prods=%s):' % (art, alias, prods))
    print('  reporte: original=%s actual=%s entradas=%s salidas=%s' %
          (d['stock_original'], d['stock_actual'], d['entradas'], d['salidas']))
    print('  oraculo: original=%s actual=%s entradas=%s salidas=%s saldo_ini=%s' %
          (original_o, restante_o, ent, sal, saldo_ini_o))
    print('  invariante Original-Actual==salidas: rep=%s orac=%s' %
          (d['stock_original'] - d['stock_actual'], sal))
    print('  kardex forward<=FF sin apertura=%s | apertura sintetica=%s | stock_hoy=%s post_jul=%s'
          % (fwd_sin_apertura, apertura, stock_hoy, post))
else:
    print('\nA3: sin fila con entradas y salidas en julio para la marca elegida')

# ---------- A4: Excel vs JSON (ambos modos) ----------
import openpyxl

def leer_excel(resp, con_desc):
    wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True)
    ws = wb.active
    filas = list(ws.values)
    hdr = filas[3]
    datos = []
    total_row = None
    for row in filas[4:]:
        if row[0] == 'TOTALES':
            total_row = row
            break
        if row[0] is not None:
            datos.append(row)
    return hdr, datos, total_row

for tag, params, jref in (('OFF', dict(base_params), j_off),
                          ('ON', dict(p2), j_on)):
    rx, tx, qx, wx = call(exportar_movimientos_sucursal_excel, params, admin)
    ct = rx.get('Content-Type', '')
    if 'spreadsheet' not in ct:
        print('\nA4 EXCEL(%s): FALLO status=%s body=%s' % (tag, rx.status_code, rx.content[:200]))
        continue
    hdr, datos, total_row = leer_excel(rx, tag == 'ON')
    ncols_fijas = 7 if tag == 'ON' else 6
    idx_tot = hdr.index('TOTAL Original')
    tot_xls = total_row[idx_tot:idx_tot + 5] if total_row else None
    tot_json = tuple(tot(jref, c) for c in ('total_stock_original', 'total_stock_actual',
                                            'total_entradas', 'total_salidas', 'total_vendido'))
    print('\nA4 EXCEL(%s): filas=%d (json=%d) t=%ss q=%d writes=%s' %
          (tag, len(datos), len(jref.get('datos', [])), tx, qx, wx))
    print('  hdr fijas=%s...' % (hdr[:ncols_fijas],))
    print('  TOTALES xls =%s' % (tot_xls,))
    print('  TOTALES json=%s -> %s' % (tot_json,
          'OK' if tot_xls and tuple(tot_xls) == tot_json else 'DELTA!'))

# ---------- B: despachos-tiendas ----------
pB = {'fecha_desde': '2026-07-01', 'fecha_hasta': '2026-07-31'}
rB, tB, qB, wB = call(obtener_reporte_despachos_tiendas, pB, admin)
jB = json.loads(rB.content)
res = jB.get('resumen', {})
print('\nB API despachos-tiendas jul: status=%s ok=%s t=%ss q=%d writes=%s' %
      (rB.status_code, jB.get('success'), tB, qB, wB))
print('  resumen=%s' % json.dumps(res, ensure_ascii=False))

mv = Movimientos_Producto.objects.filter(estado='COMPLETADO',
                                         fecha__gte=FI, fecha__lte=FF)
o_desp = abs(mv.filter(concepto='TRASPASO_SALIDA', sucursal_origen_id__in=suc_ids)
             .aggregate(s=Sum('cantidad'))['s'] or 0)
o_rec = abs(mv.filter(concepto='TRASPASO_ENTRADA', sucursal_origen_id__in=suc_ids)
            .aggregate(s=Sum('cantidad'))['s'] or 0)
cd_ids = [s['id'] for s in suc_all
          if s['es_centro_distribucion'] or s['tipo_sucursal'] == 'CENTRO_DISTRIBUCION']
o_canon = abs(mv.filter(concepto='TRASPASO_SALIDA', sucursal_origen_id__in=cd_ids)
              .aggregate(s=Sum('cantidad'))['s'] or 0)
o_tienda_orig = abs(mv.filter(concepto='TRASPASO_SALIDA', sucursal_origen_id__in=tiendas_ids)
                    .aggregate(s=Sum('cantidad'))['s'] or 0)
fuera = mv.filter(concepto='TRASPASO_SALIDA').exclude(
    sucursal_origen_id__in=suc_ids).aggregate(s=Sum('cantidad'), n=Count('id'))
legacy = mv.filter(concepto__in=('TRASPASO_SUCURSAL', 'TRASPASO_BODEGA', 'TRASPASO_VITRINA')
                   ).aggregate(n=Count('id'), pos=Sum('cantidad', filter=Q(cantidad__gt=0)),
                               neg=Sum('cantidad', filter=Q(cantidad__lt=0)))
o_null_art = abs(mv.filter(concepto='TRASPASO_SALIDA', sucursal_origen_id__in=suc_ids,
                           ProductoTalla__producto__articulo__isnull=True)
                 .aggregate(s=Sum('cantidad'))['s'] or 0)
print('  oraculo despachado (TRASPASO_SALIDA origen usuario)=%s | reporte=%s' %
      (o_desp, res.get('total_despachado')))
print('  oraculo recibido   (TRASPASO_ENTRADA origen usuario)=%s | reporte=%s' %
      (o_rec, res.get('total_recibido')))
print('  canonico (origen CD es_compradora)=%s | desde tiendas=%s' % (o_canon, o_tienda_orig))
print('  fuera de alcance usuario: n=%s uds=%s' % (fuera['n'], fuera['s']))
print('  legacy TRASPASO_SUCURSAL/BODEGA/VITRINA jul-2026: n=%s +%s %s (NO los ve el reporte)'
      % (legacy['n'], legacy['pos'], legacy['neg']))
print('  articulo NULL en despachos jul=%s' % o_null_art)

# desglose salidas de tiendas por destino (mezcla?): top 5
top_td = list(mv.filter(concepto='TRASPASO_SALIDA', sucursal_origen_id__in=tiendas_ids)
              .values('sucursal_origen__alias', 'sucursal_destino__alias')
              .annotate(u=Sum('cantidad')).order_by('u')[:5])
print('  top salidas ORIGEN TIENDA: %s' % top_td)

print('\nFIN T1')
