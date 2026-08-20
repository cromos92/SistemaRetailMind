# -*- coding: utf-8 -*-
"""Tanda 3 (SOLO LECTURA): causas raiz del descuadre kardex + metricas muertas."""
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
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from app.models import (EmpresaUser, Movimientos_Producto, Producto_Talla, Sucursal)
from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO
from app.views import reporte_movimientos_kardex
from app.views_modulo_reportes import obtener_reporte_despachos_tiendas

FI, FF = date(2026, 7, 1), date(2026, 7, 31)
User = get_user_model()


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
    return resp, round(time.perf_counter() - t0, 2), len(connection.queries)


admin = User.objects.filter(username='javier').first()

# ---------- D1: descomposicion del descuadre en los 4 SKU ----------
objetivos = [214822, 467549, 19187, 362970]
filas = (Movimientos_Producto.objects.filter(ProductoTalla_id__in=objetivos)
         .values('ProductoTalla_id')
         .annotate(
             total_all=Sum('cantidad'),
             total_comp=Sum('cantidad', filter=Q(estado='COMPLETADO')),
             apertura=Sum('cantidad', filter=Q(concepto='INGRESO_INICIAL',
                          referencia_externa=REF_SALDO_INICIAL_SINTETICO)),
             n_no_comp=Count('id', filter=~Q(estado='COMPLETADO')),
             u_no_comp=Sum('cantidad', filter=~Q(estado='COMPLETADO')),
         ))
stocks = dict(Producto_Talla.objects.filter(id__in=objetivos)
              .values_list('id', 'stock'))
print('D1 descomposicion descuadre kardex:')
for f in filas:
    pid = f['ProductoTalla_id']
    comp = f['total_comp'] or 0
    ap = f['apertura'] or 0
    print(('  PT %s: stock=%s | sum_all=%s sum_COMPLETADO=%s sum_comp_sin_apertura=%s'
           ' | apertura=%s | no_completado n=%s u=%s') %
          (pid, stocks.get(pid), f['total_all'], comp, comp - ap, ap,
           f['n_no_comp'], f['u_no_comp']))

# estados presentes en los no-completado de esos SKU
est = list(Movimientos_Producto.objects.filter(ProductoTalla_id__in=objetivos)
           .exclude(estado='COMPLETADO').values('estado', 'concepto')
           .annotate(n=Count('id'), u=Sum('cantidad')).order_by('-n')[:10])
print('  detalle no-COMPLETADO: %s' % est)

# ---------- D2: muestra de 20 SKU con stock>0 y movimientos ----------
emp_ids = list(set(EmpresaUser.objects.filter(user=admin, status=True)
                   .values_list('empresa_id', flat=True)))
suc_ids = list(Sucursal.objects.filter(empresa_id__in=emp_ids)
               .values_list('id', flat=True))
muestra = list(Producto_Talla.objects.filter(
    producto__sucursal_id__in=suc_ids, stock__gt=0).order_by('?')[:20]
    .values_list('id', flat=True))
agg = (Movimientos_Producto.objects.filter(ProductoTalla_id__in=muestra)
       .values('ProductoTalla_id')
       .annotate(total_all=Sum('cantidad'),
                 total_comp=Sum('cantidad', filter=Q(estado='COMPLETADO')),
                 apertura=Sum('cantidad', filter=Q(concepto='INGRESO_INICIAL',
                              referencia_externa=REF_SALDO_INICIAL_SINTETICO))))
stocks2 = dict(Producto_Talla.objects.filter(id__in=muestra)
               .values_list('id', 'stock'))
ok_all = ok_comp = ok_sin_ap = n = 0
for f in agg:
    n += 1
    st = stocks2.get(f['ProductoTalla_id']) or 0
    if (f['total_all'] or 0) == st:
        ok_all += 1
    if (f['total_comp'] or 0) == st:
        ok_comp += 1
    if ((f['total_comp'] or 0) - (f['apertura'] or 0)) == st:
        ok_sin_ap += 1
print('\nD2 muestra %d SKU con stock>0 (con movimientos: %d):' % (len(muestra), n))
print('  cuadra sum_all==stock (lo que muestra el kardex): %d/%d' % (ok_all, n))
print('  cuadra sum_COMPLETADO==stock:                     %d/%d' % (ok_comp, n))
print('  cuadra sum_comp_SIN_apertura==stock:              %d/%d' % (ok_sin_ap, n))

# volumen de SKUs con apertura sintetica y de no-COMPLETADO global
n_apert_sku = (Movimientos_Producto.objects.filter(
    concepto='INGRESO_INICIAL', referencia_externa=REF_SALDO_INICIAL_SINTETICO)
    .values('ProductoTalla_id').distinct().count())
no_comp = (Movimientos_Producto.objects.exclude(estado='COMPLETADO')
           .values('estado').annotate(n=Count('id')).order_by('-n'))
print('  SKUs con apertura sintetica: %s' % n_apert_sku)
print('  movimientos no-COMPLETADO global: %s' % list(no_comp))

# ---------- D3: "despachado" muerto en despachos_por_proveedor ----------
n_egr_compra = Movimientos_Producto.objects.filter(
    tipo_movimiento='EGRESO', dte__tipo_transaccion='COMPRA').count()
print('\nD3 EGRESOS colgados de DTE COMPRA (histórico completo): %s' % n_egr_compra)

# ---------- D4: kardex con fecha_inicio a mitad de historia ----------
r, t, q = call(reporte_movimientos_kardex,
               {'producto_talla_id': '362970', 'fecha_inicio': '2026-07-01',
                'fecha_fin': '2026-07-31', 'page': '1', 'page_size': '5'}, admin)
j = json.loads(r.content)
if j.get('success') and j['items']:
    it = j['items'][0]
    print('\nD4 kardex PT 362970 filtrado desde 01-jul: primer saldo=%s '
          '(entrada=%s salida=%s) -> saldo arranca en 0, no en el saldo real'
          % (it['saldo'], it['entrada'], it['salida']))

# ---------- D5: despachos-tiendas: proveedor 'Sin recepcion' ----------
rB, tB, qB = call(obtener_reporte_despachos_tiendas,
                  {'fecha_desde': '2026-07-01', 'fecha_hasta': '2026-07-31'}, admin)
jB = json.loads(rB.content)
datos = jB.get('datos', [])
sin_prov = [p for p in datos if p['proveedor'] == 'Sin recepción']
u_sin = sum(p['total'] for p in sin_prov)
u_tot = sum(p['total'] for p in datos)
print('\nD5 despachos-tiendas jul: articulos=%d sin_proveedor=%d (%.1f%% de %d uds)'
      % (len(datos), len(sin_prov), 100.0 * u_sin / u_tot if u_tot else 0, u_tot))
por_prov = jB.get('por_proveedor', [])
sobre100 = [p['proveedor'] for p in por_prov if p['tasa_recepcion'] > 100]
print('  proveedores con tasa_recepcion>100%%: %s' % sobre100[:5])

print('\nFIN T3')
