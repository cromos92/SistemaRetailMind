# -*- coding: utf-8 -*-
# Tanda 3: verificaciones extra (SOLO LECTURA)
import json, os, sys, time
from datetime import date, timedelta

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
import django
django.setup()

from django.conf import settings
from django.db import connection, reset_queries, transaction
from django.db.models import Sum, Count, Q, F
from django.db.models.functions import Abs
from django.test import RequestFactory
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.models import (EmpresaUser, Sucursal, Producto, Producto_Talla,
                        Movimientos_Producto, LoteProducto)
from app.constants_kardex import CONCEPTOS_VENTA

settings.DEBUG = True
HOY = timezone.localdate()
User = get_user_model()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())

ESCR = ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP', 'TRUNCATE')

def invocar(path, params, user, suc=None, emp=None):
    mod, fn = path.rsplit('.', 1)
    view = getattr(__import__(mod, fromlist=[fn]), fn)
    rf = RequestFactory()
    req = rf.get('/_t', data=params, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    req.user = user
    req.session = {'idSucursalActual': suc, 'idEmpresaActual': emp}
    reset_queries()
    t0 = time.perf_counter()
    try:
        with transaction.atomic():
            resp = view(req)
            transaction.set_rollback(True)
    except Exception as e:
        print('  EXC', type(e).__name__, str(e)[:150])
        return None, None, round((time.perf_counter() - t0) * 1000, 1), len(connection.queries)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    nq = len(connection.queries)
    malas = [q['sql'][:90] for q in connection.queries
             if q['sql'].lstrip().upper().startswith(ESCR)]
    if malas:
        print('  !!ESCRITURAS!!', malas[:3])
    try:
        js = json.loads(resp.content)
    except Exception:
        js = None
    return resp, js, ms, nq

QID = 7  # NICK2

print('===== A. quiebre-talla: 6 celdas con vendidas>=3 (julio) =====')
rq, jq, msq, nqq = invocar('app.views_modulo_reportes_tallas.api_reporte_quiebre_talla',
                           {'sucursal_id': QID, 'desde': '2026-07-01', 'hasta': '2026-07-31',
                            'page_size': 100, 'solo_quiebres': '0'}, admin, QID, None)
jq = jq or {}
estilos = jq.get('estilos') or []
print('solo_quiebres=0: ms=%s q=%s estilos_pagina=%s' % (msq, nqq, len(estilos)))

from app.utils_producto_match import normalizar_articulo
from app.views_modulo_reportes_tallas import normalizar_talla

celdas = []
for e in estilos:
    for t in e['curva']:
        if t['vendidas'] >= 3:
            celdas.append((e, t))
        if len(celdas) >= 6:
            break
    if len(celdas) >= 6:
        break

D1, D2 = date(2026, 7, 1), date(2026, 7, 31)
ok_v = ok_d = ok_a = 0
for e, t in celdas:
    art_norm, mid_s, cid_s = e['clave'].split('|')
    mid_s = int(mid_s) or None
    cid_s = int(cid_s) or None
    pids = [p for (p, a, m, c) in Producto.objects.filter(
        sucursal_id=QID, excluir_de_analitica=False).values_list(
        'id', 'articulo', 'atributo1_id', 'atributo2_id')
        if normalizar_articulo(a) == art_norm and m == mid_s and c == cid_s]
    tallas_rows = list(Producto_Talla.objects.filter(producto_id__in=pids)
                       .values('id', 'talla', 'stock'))
    tids = [r['id'] for r in tallas_rows if normalizar_talla(r['talla']) == t['talla']]
    stock_cell = sum(r['stock'] or 0 for r in tallas_rows if normalizar_talla(r['talla']) == t['talla'])
    venta_ora = Movimientos_Producto.objects.filter(
        ProductoTalla_id__in=tids, concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
        fecha__gte=D1, fecha__lte=D2).aggregate(s=Sum(Abs('cantidad')))['s'] or 0
    movs = {}
    for r in Movimientos_Producto.objects.filter(
            ProductoTalla_id__in=tids, estado='COMPLETADO',
            fecha__gte=D1, fecha__lte=HOY).exclude(cantidad=0) \
            .values('fecha').annotate(d=Sum('cantidad')):
        movs[r['fecha']] = movs.get(r['fecha'], 0) + int(r['d'] or 0)
    saldo = stock_cell
    dias_bf = 0
    d = HOY
    while d >= D1:
        if D1 <= d <= D2 and saldo > 0:
            dias_bf += 1
        saldo -= movs.get(d, 0)
        d -= timedelta(days=1)
    # aritmetica de extrapolacion
    dias_p = t['dias_periodo']
    aj_esp = round(t['vendidas'] * dias_p / t['dias_disponible'], 1) if t['dias_disponible'] else None
    pe_esp = round(max(0.0, (aj_esp or 0) - t['vendidas']), 1) if aj_esp is not None else 0.0
    v_ok = venta_ora == t['vendidas']
    d_ok = dias_bf == t['dias_disponible']
    a_ok = (aj_esp == t['venta_ajustada']) and (pe_esp == t['unidades_perdidas'])
    ok_v += v_ok; ok_d += d_ok; ok_a += a_ok
    print('  %s %s T%s: vendidas rep=%s ora=%s %s | dias rep=%s bf=%s %s | ajust rep=%s esp=%s perd rep=%s esp=%s %s'
          % (e['articulo'], e['color'], t['talla'], t['vendidas'], venta_ora, 'OK' if v_ok else 'DIF',
             t['dias_disponible'], dias_bf, 'OK' if d_ok else 'DIF',
             t['venta_ajustada'], aj_esp, t['unidades_perdidas'], pe_esp, 'OK' if a_ok else 'DIF'))
print('celdas: venta %s/6, dias %s/6, aritmetica %s/6' % (ok_v, ok_d, ok_a))

print('')
print('===== B. perdidas ene-mar: cuanto viene de tallas dudosas / baja disponibilidad =====')
rq2, jq2, _, _ = invocar('app.views_modulo_reportes_tallas.api_reporte_quiebre_talla',
                         {'sucursal_id': QID, 'desde': '2026-01-01', 'hasta': '2026-03-31',
                          'page_size': 100}, admin, QID, None)
est2 = (jq2 or {}).get('estilos') or []
tot_p = sum(t['unidades_perdidas'] for e in est2 for t in e['curva'] if t['quiebre'])
p_dud = sum(t['unidades_perdidas'] for e in est2 for t in e['curva'] if t['quiebre'] and t['reconstruccion_dudosa'])
p_low = sum(t['unidades_perdidas'] for e in est2 for t in e['curva']
            if t['quiebre'] and t['dias_disponible'] <= 7)
mx = sorted(((t['unidades_perdidas'], t['vendidas'], t['dias_disponible'], e['articulo'])
             for e in est2 for t in e['curva'] if t['quiebre']), reverse=True)[:5]
print('pagina 1: perdidas=%s | de dudosas=%s | de dias_disp<=7=%s' % (round(tot_p, 1), round(p_dud, 1), round(p_low, 1)))
print('top 5 celdas perdidas (perd, vend, dias, art):', mx)

print('')
print('===== C. existencias-sucursal: causa pct_viejo>100 y costo export =====')
TID = 4  # PAO3
lot_all = LoteProducto.objects.filter(activo=True, cantidad_disponible__gt=0,
    producto_talla__producto__sucursal_id=TID,
    fecha_ingreso__date__lte=HOY - timedelta(days=181)).aggregate(s=Sum('cantidad_disponible'))['s'] or 0
lot_excl = LoteProducto.objects.filter(activo=True, cantidad_disponible__gt=0,
    producto_talla__producto__sucursal_id=TID,
    producto_talla__producto__excluir_de_analitica=True,
    fecha_ingreso__date__lte=HOY - timedelta(days=181)).aggregate(s=Sum('cantidad_disponible'))['s'] or 0
stock_vis = Producto_Talla.objects.filter(producto__sucursal_id=TID,
    producto__excluir_de_analitica=False, stock__gt=0).aggregate(s=Sum('stock'))['s'] or 0
print('PAO3: lotes>180d total=%s, de productos excluidos=%s, stock visible=%s -> pct sin excluidos=%.1f%% (reporte 155.4%%)'
      % (lot_all, lot_excl, stock_vis, 100.0 * (lot_all - lot_excl) / stock_vis))

t0 = time.perf_counter()
rx, jx, msx, nqx = invocar('app.views_modulo_reportes.exportar_existencias_sucursal_excel',
                           {'sucursal_id': TID}, admin, TID, None)
print('export excel exist-sucursal: status=%s ms=%s q=%s' % (getattr(rx, 'status_code', None), msx, nqx))

print('')
print('===== D. reversion historica: ignorados desde 2026-03-01 =====')
fc = date(2026, 3, 1)
post = Movimientos_Producto.objects.filter(estado='COMPLETADO', fecha__gt=fc, fecha__lte=HOY)
tot_post = post.aggregate(n=Count('id'))
fuera = post.exclude(Q(tipo_movimiento__in=['INGRESO', 'EGRESO'])
                     | Q(concepto__in=['TRASPASO_ENTRADA', 'TRASPASO_SALIDA']))
print('post 2026-03-01: movs=%s ignorados=%s' % (tot_post['n'], fuera.count()))
mal_dest = post.filter(Q(tipo_movimiento='INGRESO') | Q(concepto='TRASPASO_ENTRADA')) \
    .exclude(sucursal_destino_id=F('ProductoTalla__producto__sucursal_id')) \
    .aggregate(n=Count('id'), s=Sum(Abs('cantidad')))
print('ingresos destino!=sucursal producto desde 2026-03:', mal_dest)

print('')
print('===== E. existencias-marca: export excel misma data =====')
MID = 299
rz, jz, msz, nqz = invocar('app.views_modulo_reportes.exportar_existencias_marca_excel',
                           {'marca_id': MID, 'sucursal_id': 'todas'}, admin, TID, None)
ct = rz['Content-Type'] if rz else None
print('export marca: status=%s content=%s ms=%s q=%s' % (getattr(rz, 'status_code', None), ct, msz, nqz))
print('FIN T3')
