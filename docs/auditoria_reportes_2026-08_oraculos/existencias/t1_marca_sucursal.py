# -*- coding: utf-8 -*-
# Tanda 1: existencias-marca + existencias-sucursal (SOLO LECTURA)
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
from django.db.models import Sum, Count, Q, F, Value, IntegerField
from django.db.models.functions import Coalesce
from django.test import RequestFactory
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.models import (EmpresaUser, Sucursal, Producto, Producto_Talla,
                        Movimientos_Producto, LoteProducto)
from app.constants_kardex import CONCEPTOS_VENTA, REF_SALDO_INICIAL_SINTETICO

settings.DEBUG = True
print('BD:', settings.DATABASES['default'].get('NAME'), '@', settings.DATABASES['default'].get('HOST'))
HOY = timezone.localdate()
print('HOY:', HOY)

User = get_user_model()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())
emp_admin = list(EmpresaUser.objects.filter(user=admin, status=True).values_list('empresa_id', flat=True))
print('admin:', admin.username, 'empresas:', emp_admin)

ESCR = ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP', 'TRUNCATE')

def invocar(path, params, user, suc=None, emp=None):
    mod, fn = path.rsplit('.', 1)
    view = getattr(__import__(mod, fromlist=[fn]), fn)
    rf = RequestFactory()
    req = rf.get('/_t', data=params)
    req.user = user
    req.session = {'idSucursalActual': suc, 'idEmpresaActual': emp}
    reset_queries()
    t0 = time.perf_counter()
    try:
        with transaction.atomic():
            resp = view(req)
            transaction.set_rollback(True)
    except Exception as e:
        print('  EXC', type(e).__name__, str(e)[:120])
        return None, None, round((time.perf_counter() - t0) * 1000, 1), len(connection.queries)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    nq = len(connection.queries)
    malas = [q['sql'][:90] for q in connection.queries
             if q['sql'].lstrip().upper().startswith(ESCR)]
    if malas:
        print('  !!ESCRITURAS DETECTADAS!!', malas[:3])
    try:
        js = json.loads(resp.content)
    except Exception:
        js = None
    return resp, js, ms, nq

# ---- marca acotada (<=400 articulos distintos, mayor stock)
cand = (Producto_Talla.objects.filter(stock__gt=0, producto__excluir_de_analitica=False,
        producto__atributo1__isnull=False, producto__sucursal__empresa_id__in=emp_admin)
        .values('producto__atributo1_id', 'producto__atributo1__valor')
        .annotate(s=Sum('stock'), n=Count('producto__articulo', distinct=True))
        .order_by('-s'))
marca = None
for c in cand[:30]:
    if c['n'] <= 400:
        marca = c
        break
print('MARCA ELEGIDA:', marca)
MID = marca['producto__atributo1_id']

t = (Producto_Talla.objects.filter(stock__gt=0, producto__atributo1_id=MID,
        producto__excluir_de_analitica=False)
     .exclude(Q(producto__sucursal__es_centro_distribucion=True) | Q(producto__sucursal__tipo_sucursal='CENTRO_DISTRIBUCION'))
     .values('producto__sucursal_id', 'producto__sucursal__alias')
     .annotate(s=Sum('stock')).order_by('-s').first())
print('TIENDA:', t)
TID = t['producto__sucursal_id']

print('')
print('================ 1. EXISTENCIAS-MARCA ================')
resp, js, ms, nq = invocar('app.views_modulo_reportes.obtener_reporte_existencias_marca',
                           {'marca_id': MID, 'sucursal_id': 'todas'}, admin, TID, None)
js = js or {}
print('status', getattr(resp, 'status_code', None), 'ms', ms, 'queries', nq)
print('success', js.get('success'), 'truncado', js.get('truncado'),
      'mostrados', js.get('articulos_mostrados'), 'disponibles', js.get('articulos_disponibles'))
datos = js.get('datos') or []
print('filas', len(datos), 'sucursales_cols', len(js.get('sucursales') or []))

tot_rep = sum(f['total_stock'] for f in datos)
ora_all = Producto_Talla.objects.filter(
    producto__atributo1_id=MID, producto__excluir_de_analitica=False,
    producto__sucursal__empresa_id__in=emp_admin).aggregate(s=Sum('stock'))['s'] or 0
ora_pos = Producto_Talla.objects.filter(
    producto__atributo1_id=MID, producto__excluir_de_analitica=False, stock__gt=0,
    producto__sucursal__empresa_id__in=emp_admin).aggregate(s=Sum('stock'))['s'] or 0
print('TOTAL: reporte=%s / oraculo neto=%s / oraculo solo_pos=%s' % (tot_rep, ora_all, ora_pos))

por_suc_rep = {}
for f in datos:
    for sid, v in (f.get('stock_por_sucursal') or {}).items():
        por_suc_rep[sid] = por_suc_rep.get(sid, 0) + v
ora_suc = {str(r['producto__sucursal_id']): int(r['s'] or 0) for r in Producto_Talla.objects.filter(
    producto__atributo1_id=MID, producto__excluir_de_analitica=False,
    producto__sucursal__empresa_id__in=emp_admin)
    .values('producto__sucursal_id').annotate(s=Sum('stock'))}
difs = {k: (por_suc_rep.get(k, 0), ora_suc.get(k, 0)) for k in set(por_suc_rep) | set(ora_suc)
        if por_suc_rep.get(k, 0) != ora_suc.get(k, 0)}
print('por sucursal: %s columnas, %s con dif (rep,ora): %s' % (len(ora_suc), len(difs), list(difs.items())[:6]))

# estilos vendidos-todo invisibles con solo_con_stock (afecta columna Original)
resp2, js2, ms2, nq2 = invocar('app.views_modulo_reportes.obtener_reporte_existencias_marca',
    {'marca_id': MID, 'sucursal_id': 'todas', 'solo_con_stock': 'false'}, admin, TID, None)
datos2 = (js2 or {}).get('datos') or []
vend_todo = [f for f in datos2 if f['total_stock'] <= 0 and f['total_stock_original'] > 0]
print('solo_con_stock=false: filas=%s ; estilos Actual<=0 con Original>0 (invisibles por defecto): %s (%s u de Original)'
      % (len(datos2), len(vend_todo), sum(f['total_stock_original'] for f in vend_todo)))

# salud vs oraculo
salud = js.get('salud') or {}
arts = set(f['articulo'] for f in datos)
pids = list(Producto.objects.filter(articulo__in=arts, atributo1_id=MID,
    excluir_de_analitica=False, sucursal__empresa_id__in=emp_admin).values_list('id', flat=True))
stock_u = Producto_Talla.objects.filter(producto_id__in=pids).aggregate(s=Sum('stock'))['s'] or 0
v30 = abs(Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
    fecha__gte=HOY - timedelta(days=30), ProductoTalla__producto_id__in=pids)
    .aggregate(s=Sum('cantidad'))['s'] or 0)
viejo = LoteProducto.objects.filter(activo=True, cantidad_disponible__gt=0,
    producto_talla__producto_id__in=pids,
    fecha_ingreso__date__lte=HOY - timedelta(days=181)).aggregate(s=Sum('cantidad_disponible'))['s'] or 0
cob_ora = int(stock_u / (v30 / 30.0)) if v30 else None
pv_ora = round(100.0 * viejo / stock_u, 1) if stock_u else 0
print('salud reporte :', salud)
print('salud oraculo : cobertura=%s pct_viejo=%s vendidas_30=%s stock=%s' % (cob_ora, pv_ora, v30, stock_u))

# cruce Original vs movimientos-sucursal (Inicial vs Restante)
respM, jsM, msM, nqM = invocar('app.views_modulo_reportes.obtener_reporte_movimientos_sucursal',
                               {'marca_id': MID}, admin, TID, None)
print('mov-sucursal: status', getattr(respM, 'status_code', None), 'ms', msM, 'queries', nqM)
datosM = (jsM or {}).get('datos') or []
mapaM = {}
for f in datosM:
    for alias, d in (f.get('sucursales') or {}).items():
        mapaM[(f.get('articulo'), f.get('color'), alias)] = (d.get('stock_original'), d.get('stock_actual'))
celdas = 0
difs_o = []
for f in datos:
    for alias, d in (f.get('sucursales') or {}).items():
        key = (f.get('articulo'), f.get('color'), alias)
        if key in mapaM and mapaM[key][0] is not None:
            celdas += 1
            if int(mapaM[key][0]) != int(d.get('original', 0)):
                difs_o.append((key, d.get('original'), mapaM[key][0]))
print('cruce Original: %s celdas comparadas, %s con diferencia' % (celdas, len(difs_o)))
for x in difs_o[:8]:
    print('   DIF', x)

resp3, js3, ms3, nq3 = invocar('app.views_modulo_reportes.obtener_reporte_existencias_marca',
    {'marca_id': MID, 'sucursal_id': 'todas', 'limite': 20}, admin, TID, None)
js3 = js3 or {}
print('limite=20 -> truncado=%s mostrados=%s disponibles=%s filas=%s'
      % (js3.get('truncado'), js3.get('articulos_mostrados'), js3.get('articulos_disponibles'),
         len(js3.get('datos') or [])))

# ---- usuario restringido + sucursal ajena
from app.utils_permisos import usuario_puede_ver_todas_sucursales
restr = None
todas_emp = set(Sucursal.objects.values_list('empresa_id', flat=True).distinct())
for u in User.objects.filter(is_active=True, is_superuser=False).exclude(rol='administrador')[:80]:
    eus = list(EmpresaUser.objects.filter(user=u, status=True).values_list('empresa_id', 'sucursal_id'))
    if not eus:
        continue
    empresas = {e for e, _ in eus}
    if empresas >= todas_emp:
        continue
    try:
        if usuario_puede_ver_todas_sucursales(u):
            continue
    except Exception:
        continue
    restr = (u, empresas)
    break
if restr:
    u, empresas = restr
    suc_ajena = Sucursal.objects.exclude(empresa_id__in=empresas).values_list('id', flat=True).first()
    print('restringido:', u.username, 'rol', getattr(u, 'rol', '?'), 'empresas', sorted(empresas), 'suc_ajena', suc_ajena)
    r4, j4, _, _ = invocar('app.views_modulo_reportes.obtener_reporte_existencias_marca',
                           {'marca_id': MID, 'sucursal_id': suc_ajena}, u, None, None)
    print('marca cross-empresa: status=%s success=%s error=%s'
          % (getattr(r4, 'status_code', None), (j4 or {}).get('success'), str((j4 or {}).get('error'))[:70]))
    r5, j5, _, _ = invocar('app.views_modulo_reportes.obtener_reporte_existencias_sucursal',
                           {'sucursal_id': suc_ajena}, u, None, None)
    print('exist-sucursal cross-empresa: status=%s success=%s datos=%s error=%s'
          % (getattr(r5, 'status_code', None), (j5 or {}).get('success'),
             len((j5 or {}).get('datos') or []), str((j5 or {}).get('error'))[:70]))
else:
    print('NO se encontro usuario restringido')

print('')
print('================ 2. EXISTENCIAS-SUCURSAL ================')
resp, js, ms, nq = invocar('app.views_modulo_reportes.obtener_reporte_existencias_sucursal',
                           {'sucursal_id': TID}, admin, TID, None)
js = js or {}
res = js.get('resumen') or {}
print('status', getattr(resp, 'status_code', None), 'ms', ms, 'queries', nq, 'filas', len(js.get('datos') or []))
print('resumen:', {k: res.get(k) for k in ('total_productos', 'stock_total', 'valor_inventario',
                                           'sin_stock', 'valor_venta_potencial', 'cobertura_dias',
                                           'pct_stock_viejo', 'vendidas_30')})

zero = Value(0, output_field=IntegerField())
base = Producto_Talla.objects.filter(producto__sucursal_id=TID, producto__excluir_de_analitica=False)
ora_stock = base.filter(stock__gt=0).aggregate(s=Sum('stock'))['s'] or 0
ora_vinv = base.filter(stock__gt=0).aggregate(
    v=Sum((Coalesce(F('producto__costo'), zero) + Coalesce(F('producto__sobreprecio'), zero)) * F('stock')))['v'] or 0
ora_vv = base.filter(stock__gt=0).aggregate(
    v=Sum(Coalesce(F('producto__precioventa'), zero) * F('stock')))['v'] or 0
ora_skus = base.filter(stock__gt=0).count()
ora_sin = base.filter(stock__lte=0).count()
print('ORACULO: skus_con_stock=%s stock=%s valor_inv=%s valor_venta=%s skus_sin_stock=%s'
      % (ora_skus, ora_stock, ora_vinv, ora_vv, ora_sin))

# vendidas_30: sin filtro analitica en la vista -> medir delta
v30_all = abs(Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
    fecha__gte=HOY - timedelta(days=30), sucursal_origen_id=TID).aggregate(s=Sum('cantidad'))['s'] or 0)
v30_excl = abs(Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
    fecha__gte=HOY - timedelta(days=30), sucursal_origen_id=TID,
    ProductoTalla__producto__excluir_de_analitica=False).aggregate(s=Sum('cantidad'))['s'] or 0)
print('vend30 tienda: todo=%s solo_analitica=%s (delta=%s)' % (v30_all, v30_excl, v30_all - v30_excl))

# KPI con filtro de marca: numerador marca / denominador tienda completa
respm, jsm, msm, nqm = invocar('app.views_modulo_reportes.obtener_reporte_existencias_sucursal',
                               {'sucursal_id': TID, 'marca_id': MID}, admin, TID, None)
resm = (jsm or {}).get('resumen') or {}
stock_marca = base.filter(stock__gt=0, producto__atributo1_id=MID).aggregate(s=Sum('stock'))['s'] or 0
v30_marca = abs(Movimientos_Producto.objects.filter(concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
    fecha__gte=HOY - timedelta(days=30), sucursal_origen_id=TID,
    ProductoTalla__producto__atributo1_id=MID).aggregate(s=Sum('cantidad'))['s'] or 0)
cob_correcta = int(stock_marca / (v30_marca / 30.0)) if v30_marca else None
viejo_marca = LoteProducto.objects.filter(activo=True, cantidad_disponible__gt=0,
    producto_talla__producto__sucursal_id=TID, producto_talla__producto__atributo1_id=MID,
    fecha_ingreso__date__lte=HOY - timedelta(days=181)).aggregate(s=Sum('cantidad_disponible'))['s'] or 0
pv_correcto = round(100.0 * viejo_marca / stock_marca, 1) if stock_marca else 0
print('CON marca_id=%s: reporte cobertura=%s pct_viejo=%s vendidas_30=%s'
      % (MID, resm.get('cobertura_dias'), resm.get('pct_stock_viejo'), resm.get('vendidas_30')))
print('   correcto (todo marca): cobertura=%s pct_viejo=%s vendidas_30_marca=%s stock_marca=%s'
      % (cob_correcta, pv_correcto, v30_marca, stock_marca))

# unidades escondidas por excluir_de_analitica (por sucursal y global)
oc_tid = Producto_Talla.objects.filter(producto__sucursal_id=TID,
    producto__excluir_de_analitica=True, stock__gt=0).aggregate(s=Sum('stock'), n=Count('id'))
oc_glob = (Producto_Talla.objects.filter(producto__sucursal__empresa_id__in=emp_admin,
    producto__excluir_de_analitica=True, stock__gt=0)
    .values('producto__sucursal_id', 'producto__sucursal__alias')
    .annotate(s=Sum('stock')).order_by('-s'))
print('oculto por excluir_de_analitica en tienda %s: %s u (%s SKUs)' % (TID, oc_tid['s'] or 0, oc_tid['n']))
print('oculto global por sucursal:', [(r['producto__sucursal__alias'], int(r['s'])) for r in oc_glob[:10]])
print('oculto global total:', sum(int(r['s']) for r in oc_glob))

# "Recibido hist." — inflacion por apertura sintetica en SKUs con kardex legacy previo
CORTE = date(2026, 1, 23)
tallas_apertura = set(Movimientos_Producto.objects.filter(
    ProductoTalla__producto__sucursal_id=TID, concepto='INGRESO_INICIAL',
    referencia_externa=REF_SALDO_INICIAL_SINTETICO, estado='COMPLETADO', cantidad__gt=0)
    .values_list('ProductoTalla_id', flat=True))
tallas_legacy = set(Movimientos_Producto.objects.filter(
    ProductoTalla__producto__sucursal_id=TID, estado='COMPLETADO', cantidad__gt=0,
    fecha__lt=CORTE).exclude(
    concepto='INGRESO_INICIAL', referencia_externa=REF_SALDO_INICIAL_SINTETICO)
    .values_list('ProductoTalla_id', flat=True))
solape = tallas_apertura & tallas_legacy
infl = Movimientos_Producto.objects.filter(
    ProductoTalla_id__in=list(solape)[:50000], concepto='INGRESO_INICIAL',
    referencia_externa=REF_SALDO_INICIAL_SINTETICO, estado='COMPLETADO', cantidad__gt=0
).aggregate(s=Sum('cantidad'))['s'] or 0 if solape else 0
print('Recibido hist. tienda %s: tallas con apertura=%s, con ingresos legacy previos=%s, SOLAPE=%s -> apertura doblemente contada=%s u'
      % (TID, len(tallas_apertura), len(tallas_legacy), len(solape), infl))
print('FIN T1')
