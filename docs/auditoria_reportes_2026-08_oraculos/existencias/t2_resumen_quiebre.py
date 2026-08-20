# -*- coding: utf-8 -*-
# Tanda 2: resumen-existencias + quiebre-talla (SOLO LECTURA)
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
from django.db.models.functions import Coalesce, Abs
from django.test import RequestFactory
from django.utils import timezone
from django.contrib.auth import get_user_model

from app.models import (EmpresaUser, Sucursal, Producto, Producto_Talla,
                        Movimientos_Producto, Categoria)
from app.constants_kardex import CONCEPTOS_VENTA, REF_SALDO_INICIAL_SINTETICO

settings.DEBUG = True
print('BD:', settings.DATABASES['default'].get('NAME'), '@', settings.DATABASES['default'].get('HOST'))
HOY = timezone.localdate()

User = get_user_model()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())
emp_admin = list(set(EmpresaUser.objects.filter(user=admin, status=True).values_list('empresa_id', flat=True)))
print('admin:', admin.username, 'empresas:', emp_admin)

ESCR = ('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP', 'TRUNCATE')

def invocar(path, params, user, suc=None, emp=None, ajax=True):
    mod, fn = path.rsplit('.', 1)
    view = getattr(__import__(mod, fromlist=[fn]), fn)
    rf = RequestFactory()
    extra = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
    req = rf.get('/_t', data=params, **extra)
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
        print('  !!ESCRITURAS DETECTADAS!!', malas[:3])
    try:
        js = json.loads(resp.content)
    except Exception:
        js = None
    return resp, js, ms, nq

print('')
print('================ 3. RESUMEN-EXISTENCIAS ================')
# sucursales de referencia
for s in Sucursal.objects.filter(id__in=[4, 8, 11]).values('id', 'alias', 'empresa_id'):
    print('  suc', s)

# --- vaciado 14-ago: que movimientos dejo en FALLADOS(11)/NICK3(8)
vac = (Movimientos_Producto.objects
       .filter(fecha__gte=date(2026, 8, 10), fecha__lte=date(2026, 8, 19))
       .filter(Q(sucursal_origen_id__in=[8, 11]) | Q(sucursal_destino_id__in=[8, 11])
               | Q(ProductoTalla__producto__sucursal_id__in=[8, 11]))
       .values('ProductoTalla__producto__sucursal_id', 'concepto', 'tipo_movimiento', 'estado', 'fecha')
       .annotate(n=Count('id'), s=Sum('cantidad')).order_by('fecha'))
print('movimientos 10-19 ago en FALLADOS/NICK3:')
for r in vac:
    print('  ', r)

# --- resumen ACTUAL
resp, js, ms, nq = invocar('app.views_resumen_existencias.obtener_resumen_existencias', {}, admin)
js = js or {}
datos = js.get('datos') or []
tg = js.get('total_general') or {}
of = js.get('totales_ocultos_flag') or {}
print('actual: status=%s ms=%s q=%s filas=%s' % (getattr(resp, 'status_code', None), ms, nq, len(datos)))
print('  sucursales:', [(d['sucursal_id'], d['sucursal'], d['total_pares'], d['ocultos_pares']) for d in datos])
print('  total_general:', tg)
print('  ocultos_flag:', {k: of.get(k) for k in ('pares', 'sucursales', 'sucursales_sin_stock_visible')})
en_datos = {d['sucursal_id'] for d in datos}
print('  FALLADOS(11) presente:', 11 in en_datos, '| NICK3(8) presente:', 8 in en_datos)

zero = Value(0, output_field=IntegerField())
ora = Producto_Talla.objects.filter(
    producto__sucursal__empresa_id__in=emp_admin,
    producto__excluir_de_analitica=False, stock__gt=0).aggregate(
    p=Sum('stock'),
    c=Sum(Coalesce(F('producto__costo'), zero) * F('stock')),
    v=Sum(Coalesce(F('producto__precioventa'), zero) * F('stock')))
ora_oc = Producto_Talla.objects.filter(
    producto__sucursal__empresa_id__in=emp_admin,
    producto__excluir_de_analitica=True, stock__gt=0).aggregate(p=Sum('stock'))
print('  ORACULO actual: pares=%s costo=%s venta=%s | ocultos=%s'
      % (ora['p'], ora['c'], ora['v'], ora_oc['p']))

# --- resumen HISTORICO (2026-08-01, antes del vaciado del 14-ago)
FC = '2026-08-01'
resph, jsh, msh, nqh = invocar('app.views_resumen_existencias.obtener_resumen_existencias',
                               {'fecha_corte': FC}, admin)
jsh = jsh or {}
datosh = jsh.get('datos') or []
print('historico %s: status=%s ms=%s q=%s filas=%s es_historico=%s'
      % (FC, getattr(resph, 'status_code', None), msh, nqh, len(datosh), jsh.get('es_historico')))
print('  filas:', [(d['sucursal_id'], d['sucursal'], d['total_pares'], d['ocultos_pares']) for d in datosh])
print('  total_general hist:', jsh.get('total_general'))

# oraculo independiente del stock historico de FALLADOS(11) y NICK3(8) al corte
# (formula: stock_hoy - ingresos_post + |egresos_post|, productos con y sin exclusion)
fc = date(2026, 8, 1)
for sid in (11, 8):
    for excl in (False, True):
        tallas = dict(Producto_Talla.objects.filter(
            producto__sucursal_id=sid, producto__excluir_de_analitica=excl)
            .values_list('id', 'stock'))
        ids = list(tallas.keys())
        ing = {r['ProductoTalla_id']: int(r['t'] or 0) for r in Movimientos_Producto.objects.filter(
            ProductoTalla_id__in=ids, fecha__gt=fc, estado='COMPLETADO', sucursal_destino_id=sid)
            .filter(Q(tipo_movimiento='INGRESO') | Q(concepto='TRASPASO_ENTRADA'))
            .values('ProductoTalla_id').annotate(t=Sum('cantidad'))}
        egr = {r['ProductoTalla_id']: int(r['t'] or 0) for r in Movimientos_Producto.objects.filter(
            ProductoTalla_id__in=ids, fecha__gt=fc, estado='COMPLETADO', sucursal_origen_id=sid)
            .filter(Q(tipo_movimiento='EGRESO') | Q(concepto='TRASPASO_SALIDA'))
            .values('ProductoTalla_id').annotate(t=Sum('cantidad'))}
        tot = sum(max(0, (tallas[t] or 0) - ing.get(t, 0) + abs(egr.get(t, 0))) for t in ids)
        # ojo: la vista descarta stock_hist<=0 por talla, igual que aqui max(0,..) por fila
        print('  oraculo hist suc=%s excluidos=%s: pares=%s (tallas=%s)' % (sid, excl, tot, len(ids)))

# --- cobertura de la reversion: movimientos post-corte que la formula IGNORA
post = Movimientos_Producto.objects.filter(estado='COMPLETADO', fecha__gt=fc, fecha__lte=HOY)
tot_post = post.aggregate(n=Count('id'), s=Sum(Abs('cantidad')))
cubiertos = post.filter(
    Q(tipo_movimiento__in=['INGRESO', 'EGRESO'])
    | Q(concepto__in=['TRASPASO_ENTRADA', 'TRASPASO_SALIDA']))
tot_cub = cubiertos.aggregate(n=Count('id'), s=Sum(Abs('cantidad')))
fuera = post.exclude(
    Q(tipo_movimiento__in=['INGRESO', 'EGRESO'])
    | Q(concepto__in=['TRASPASO_ENTRADA', 'TRASPASO_SALIDA']))
print('reversion hist (post %s): movs=%s (%s u) cubiertos=%s (%s u) IGNORADOS=%s'
      % (fc, tot_post['n'], tot_post['s'], tot_cub['n'], tot_cub['s'],
         (tot_post['n'] or 0) - (tot_cub['n'] or 0)))
for r in fuera.values('tipo_movimiento', 'concepto').annotate(n=Count('id'), s=Sum(Abs('cantidad'))).order_by('-n')[:10]:
    print('   ignorado:', r)

# movimientos cubiertos cuya sucursal no calza con la del producto (se caen del mapa)
mal_dest = cubiertos.filter(Q(tipo_movimiento='INGRESO') | Q(concepto='TRASPASO_ENTRADA')) \
    .exclude(sucursal_destino_id=F('ProductoTalla__producto__sucursal_id')) \
    .aggregate(n=Count('id'), s=Sum(Abs('cantidad')))
mal_orig = cubiertos.filter(Q(tipo_movimiento='EGRESO') | Q(concepto='TRASPASO_SALIDA')) \
    .exclude(sucursal_origen_id=F('ProductoTalla__producto__sucursal_id')) \
    .aggregate(n=Count('id'), s=Sum(Abs('cantidad')))
print('   ingresos con destino != sucursal del producto:', mal_dest)
print('   egresos con origen  != sucursal del producto:', mal_orig)

# --- modal detalle vs fila (actual e historico) para la sucursal con mas stock
suc_top = max(datos, key=lambda d: d['total_pares'])
sid = suc_top['sucursal_id']
rd, jd, msd, nqd = invocar('app.views_resumen_existencias.detalle_stock_sucursal',
                           {'sucursal_id': sid, 'limite': 200}, admin)
jd = jd or {}
print('modal actual suc=%s(%s): fila=%s modal_total=%s productos=%s (ms=%s q=%s)'
      % (sid, suc_top['sucursal'], suc_top['total_pares'], jd.get('total_pares'),
         jd.get('total_productos'), msd, nqd))
fila_h = next((d for d in datosh if d['sucursal_id'] == sid), None)
rdh, jdh, msdh, nqdh = invocar('app.views_resumen_existencias.detalle_stock_sucursal',
                               {'sucursal_id': sid, 'limite': 200, 'fecha_corte': FC}, admin)
jdh = jdh or {}
print('modal hist   suc=%s: fila=%s modal_total=%s (ms=%s q=%s)'
      % (sid, fila_h and fila_h['total_pares'], jdh.get('total_pares'), msdh, nqdh))

# --- filtro por categoria PADRE (v1.2) sin expandir
padre = (Categoria.objects.filter(padre__isnull=True, subcategorias__isnull=False)
         .exclude(nombre__startswith='_ZZ_')
         .annotate(n=Count('subcategorias')).order_by('-n').first())
hijas = list(Categoria.objects.filter(padre=padre).values_list('id', flat=True))
rp, jp, _, _ = invocar('app.views_resumen_existencias.obtener_resumen_existencias',
                       {'categoria_id': padre.id}, admin)
tp = ((jp or {}).get('total_general') or {}).get('pares')
ora_padre_solo = Producto_Talla.objects.filter(
    producto__sucursal__empresa_id__in=emp_admin, producto__excluir_de_analitica=False,
    stock__gt=0, producto__categoria_id=padre.id).aggregate(s=Sum('stock'))['s'] or 0
ora_rama = Producto_Talla.objects.filter(
    producto__sucursal__empresa_id__in=emp_admin, producto__excluir_de_analitica=False,
    stock__gt=0, producto__categoria_id__in=[padre.id] + hijas).aggregate(s=Sum('stock'))['s'] or 0
print('filtro categoria padre "%s"(id=%s, %s hijas): reporte=%s / solo-padre=%s / rama completa=%s'
      % (padre.nombre, padre.id, len(hijas), tp, ora_padre_solo, ora_rama))

# --- listar_articulos_para_excluir con ids guardados + filtro de sucursal (fix elif)
pid_ej = Producto.objects.filter(sucursal__empresa_id__in=emp_admin).values_list('id', flat=True).first()
rl, jl, _, _ = invocar('app.views_resumen_existencias.listar_articulos_para_excluir',
                       {'ids': str(pid_ej), 'sucursal_id': sid}, admin)
jl = jl or {}
print('listar_articulos (ids=%s, sucursal=%s): items=%s truncado=%s'
      % (pid_ej, sid, len(jl.get('items') or []), jl.get('truncado')))

# --- exports status
rx, jx, msx, _ = invocar('app.views_resumen_existencias.exportar_resumen_existencias_excel', {}, admin)
print('export excel: status=%s content=%s ms=%s'
      % (getattr(rx, 'status_code', None), rx['Content-Type'] if rx else None, msx))

print('')
print('================ 4. QUIEBRE-TALLA ================')
# tienda con mas venta kardex en julio (no CD)
vt = (Movimientos_Producto.objects.filter(
    concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
    fecha__gte=date(2026, 7, 1), fecha__lte=date(2026, 7, 31))
    .exclude(Q(ProductoTalla__producto__sucursal__es_centro_distribucion=True)
             | Q(ProductoTalla__producto__sucursal__tipo_sucursal='CENTRO_DISTRIBUCION'))
    .values('ProductoTalla__producto__sucursal_id', 'ProductoTalla__producto__sucursal__alias')
    .annotate(s=Sum(Abs('cantidad'))).order_by('-s').first())
print('tienda quiebre:', vt)
QID = vt['ProductoTalla__producto__sucursal_id']

rq, jq, msq, nqq = invocar('app.views_modulo_reportes_tallas.api_reporte_quiebre_talla',
                           {'sucursal_id': QID, 'desde': '2026-07-01', 'hasta': '2026-07-31',
                            'page_size': 100}, admin, QID, None)
jq = jq or {}
print('julio: status=%s ms=%s q=%s success=%s' % (getattr(rq, 'status_code', None), msq, nqq, jq.get('success')))
print('  resumen:', jq.get('resumen'))
print('  avisos:', [a[:90] for a in (jq.get('avisos') or [])])
estilos = jq.get('estilos') or []
print('  estilos en pagina:', len(estilos))

# dudosas en julio
tallas_tot = sum(len(e['curva']) for e in estilos)
dudosas = sum(1 for e in estilos for t in e['curva'] if t.get('reconstruccion_dudosa'))
print('  tallas en curvas=%s, reconstruccion_dudosa=%s' % (tallas_tot, dudosas))

# --- verificacion de UNA celda contra kardex directo
from app.utils_producto_match import normalizar_articulo
from app.views_modulo_reportes_tallas import normalizar_talla

celda = None
for e in estilos:
    for t in e['curva']:
        if t['vendidas'] > 0 and not t.get('reconstruccion_dudosa'):
            celda = (e, t)
            break
    if celda:
        break
if celda:
    e, t = celda
    art_norm, mid_s, cid_s = e['clave'].split('|')
    mid_s = int(mid_s) or None
    cid_s = int(cid_s) or None
    print('  celda: estilo=%s marca=%s color=%s talla=%s vendidas=%s stock=%s dias_disp=%s/%s'
          % (e['articulo'], e['marca'], e['color'], t['talla'], t['vendidas'], t['stock'],
             t['dias_disponible'], t['dias_periodo']))
    # productos de la tienda que caen en la misma clave (recalculo independiente)
    pids = [p for (p, a, m, c) in Producto.objects.filter(
        sucursal_id=QID, excluir_de_analitica=False).values_list(
        'id', 'articulo', 'atributo1_id', 'atributo2_id')
        if normalizar_articulo(a) == art_norm and m == mid_s and c == cid_s]
    tallas_rows = list(Producto_Talla.objects.filter(producto_id__in=pids)
                       .values('id', 'talla', 'stock'))
    tids = [r['id'] for r in tallas_rows if normalizar_talla(r['talla']) == t['talla']]
    stock_hoy_cell = sum(r['stock'] or 0 for r in tallas_rows if normalizar_talla(r['talla']) == t['talla'])
    venta_ora = Movimientos_Producto.objects.filter(
        ProductoTalla_id__in=tids, concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
        fecha__gte=date(2026, 7, 1), fecha__lte=date(2026, 7, 31)).aggregate(
        s=Sum(Abs('cantidad')))['s'] or 0
    print('  ORACULO venta julio celda: %s (reporte %s) | fichas=%s tallas=%s stock_hoy=%s (reporte %s)'
          % (venta_ora, t['vendidas'], len(pids), len(tids), stock_hoy_cell, t['stock']))
    # dias_disponible por fuerza bruta (dia a dia)
    movs = {}
    for r in Movimientos_Producto.objects.filter(
            ProductoTalla_id__in=tids, estado='COMPLETADO',
            fecha__gte=date(2026, 7, 1), fecha__lte=HOY).exclude(cantidad=0) \
            .values('fecha').annotate(d=Sum('cantidad')):
        movs[r['fecha']] = movs.get(r['fecha'], 0) + int(r['d'] or 0)
    saldo = stock_hoy_cell
    dias_bf = 0
    d = HOY
    while d >= date(2026, 7, 1):
        if date(2026, 7, 1) <= d <= date(2026, 7, 31) and saldo > 0:
            dias_bf += 1
        saldo -= movs.get(d, 0)
        d -= timedelta(days=1)
    print('  ORACULO dias_disponible (fuerza bruta): %s (reporte %s)' % (dias_bf, t['dias_disponible']))
else:
    print('  sin celda verificable en la pagina')

# --- ventana que cruza la apertura de la migracion (ene-mar)
rq2, jq2, msq2, nqq2 = invocar('app.views_modulo_reportes_tallas.api_reporte_quiebre_talla',
                               {'sucursal_id': QID, 'desde': '2026-01-01', 'hasta': '2026-03-31',
                                'page_size': 100}, admin, QID, None)
jq2 = jq2 or {}
est2 = jq2.get('estilos') or []
t2 = sum(len(e['curva']) for e in est2)
d2 = sum(1 for e in est2 for t in e['curva'] if t.get('reconstruccion_dudosa'))
print('ene-mar (cruza apertura 22-ene): status=%s ms=%s q=%s tallas=%s dudosas=%s (%.0f%%)'
      % (getattr(rq2, 'status_code', None), msq2, nqq2, t2, d2, 100.0 * d2 / t2 if t2 else 0))
print('  resumen ene-mar:', jq2.get('resumen'))

# --- perf con marca+categoria concreta
marca_top = (Movimientos_Producto.objects.filter(
    concepto__in=CONCEPTOS_VENTA, estado='COMPLETADO',
    fecha__gte=date(2026, 7, 1), fecha__lte=date(2026, 7, 31),
    ProductoTalla__producto__sucursal_id=QID)
    .values('ProductoTalla__producto__atributo1_id')
    .annotate(s=Sum(Abs('cantidad'))).order_by('-s').first())
mq = marca_top['ProductoTalla__producto__atributo1_id']
rq3, jq3, msq3, nqq3 = invocar('app.views_modulo_reportes_tallas.api_reporte_quiebre_talla',
                               {'sucursal_id': QID, 'desde': '2026-07-01', 'hasta': '2026-07-31',
                                'marca_id': mq}, admin, QID, None)
print('julio marca_id=%s: status=%s ms=%s q=%s resumen=%s'
      % (mq, getattr(rq3, 'status_code', None), msq3, nqq3, (jq3 or {}).get('resumen')))

# --- scoping restringido
from app.utils_permisos import usuario_puede_ver_todas_sucursales
todas_emp = set(Sucursal.objects.values_list('empresa_id', flat=True).distinct())
restr = None
for u in User.objects.filter(is_active=True, is_superuser=False).exclude(rol='administrador')[:80]:
    eus = list(EmpresaUser.objects.filter(user=u, status=True).values_list('empresa_id', 'sucursal_id'))
    if not eus:
        continue
    empresas = {x for x, _ in eus}
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
    r4, j4, _, _ = invocar('app.views_modulo_reportes_tallas.api_reporte_quiebre_talla',
                           {'sucursal_id': suc_ajena, 'desde': '2026-07-01', 'hasta': '2026-07-31'},
                           u, None, None)
    print('quiebre cross-empresa (%s -> suc %s): status=%s body=%s'
          % (u.username, suc_ajena, getattr(r4, 'status_code', None), str(j4)[:100]))
    # y resumen-existencias como restringido: cuantas empresas ve
    r5, j5, _, _ = invocar('app.views_resumen_existencias.obtener_resumen_existencias', {}, u)
    emps_vistas = {d.get('empresa_id') for d in ((j5 or {}).get('datos') or [])}
    print('resumen restringido: empresas visibles=%s (permitidas=%s)' % (sorted(emps_vistas), sorted(empresas)))
print('FIN T2')
