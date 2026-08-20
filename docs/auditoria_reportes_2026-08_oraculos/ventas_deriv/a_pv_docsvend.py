# -*- coding: utf-8 -*-
# AUDITORIA READONLY: productos-vendidos + documentos-vendedor (julio 2026)
import json, sys, time
from datetime import date
from decimal import Decimal

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from django.conf import settings
from django.db import connection, reset_queries, transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.test import RequestFactory
from django.contrib.auth import get_user_model

from app.models import (Dte, Dte_Productos, Ticket, Ticket_Productos,
                        CambioDevolucionDetalle, Producto_Talla, Categoria)

settings.DEBUG = True
FI, FF = date(2026, 7, 1), date(2026, 7, 31)
User = get_user_model()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())
print('ADMIN:', admin.username if admin else None)


def invocar(path, params, suc=None, emp=None, user=None):
    mod, fn = path.rsplit('.', 1)
    view = getattr(__import__(mod, fromlist=[fn]), fn)
    rf = RequestFactory()
    req = rf.get('/_a', data=params)
    req.user = user or admin
    req.session = {'idSucursalActual': suc, 'idEmpresaActual': emp}
    reset_queries()
    t0 = time.perf_counter()
    try:
        with transaction.atomic():
            resp = view(req)
            transaction.set_rollback(True)
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}',
                'ms': round((time.perf_counter()-t0)*1000), 'nq': len(connection.queries)}
    out = {'status': resp.status_code, 'ms': round((time.perf_counter()-t0)*1000),
           'nq': len(connection.queries)}
    esc = [q['sql'][:90] for q in connection.queries
           if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
    out['escrituras'] = esc
    try:
        out['json'] = json.loads(resp.content)
    except Exception:
        out['json'] = None
    return out


# ======================= 1. PRODUCTOS VENDIDOS (GLOBAL julio) =================
print('\n=== PV GLOBAL julio 2026 ===')
r = invocar('app.views_modulo_reportes.obtener_productos_vendidos',
            {'tipo_flujo': 'custom', 'fecha_inicio': '2026-07-01',
             'fecha_fin': '2026-07-31', 'top_n': 500})
js = r.get('json') or {}
k = js.get('kpis') or {}
print('status', r.get('status'), 'ms', r.get('ms'), 'nq', r.get('nq'),
      'escrituras', len(r.get('escrituras') or []))
print('KPIs:', {x: k.get(x) for x in ('total_unidades', 'total_monto', 'total_costo',
      'total_margen_pct', 'total_skus', 'total_devoluciones_unid',
      'stock_actual_total', 'sell_through_periodo', 'cobertura_dias', 'stock_alcance',
      'criterio_monto')})
print('periodo:', js.get('periodo'), 'productos_total:', js.get('productos_total'))

# particiones
for dim in ('por_marca', 'por_categoria', 'por_sexo', 'por_genero'):
    filas = js.get(dim) or []
    s = sum(int(f.get('monto') or 0) for f in filas)
    sinid = sum(int(f.get('monto') or 0) for f in filas if not f.get('id'))
    print(f'{dim}: n={len(filas)} suma_monto={s} monto_sin_id={sinid}')
heat = js.get('heatmap') or []
print('heatmap: n=', len(heat), 'suma=', sum(int(h.get('monto') or 0) for h in heat))

# ======================= 2. ORACULO espejo (mismas reglas, codigo propio) =====
print('\n--- oraculo espejo ---')
t0 = time.perf_counter()
prods = {}
tp = (Ticket_Productos.objects.filter(
    idTicket__created_at__date__gte=FI, idTicket__created_at__date__lte=FF,
    idTicket__estado='PAGADO',
    idTicket__modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
    idTicket__dte_generado=False, ProductoTalla__isnull=False,
    ProductoTalla__producto__excluir_de_analitica=False,
).values('ProductoTalla__producto_id').annotate(u=Sum('stock'), m=Sum('subtotal')))
t_monto = 0
for x in tp:
    pid = x['ProductoTalla__producto_id']
    if not pid:
        continue
    p = prods.setdefault(pid, {'u': 0, 'm': 0})
    p['u'] += int(x['u'] or 0)
    p['m'] += int(x['m'] or 0)
    t_monto += int(x['m'] or 0)

# universo DTE (lineas c/talla, analiticas)
base_dp = Dte_Productos.objects.filter(
    dte__fecha_emision__gte=FI, dte__fecha_emision__lte=FF,
    dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
).exclude(dte__estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']) \
 .exclude(dte__tipo_documento='NOTA DE CREDITO') \
 .exclude(dte__receptor__isnull=False, dte__receptor_id=F('dte__emisor_id'))
dp = base_dp.filter(productoTalla__isnull=False,
                    productoTalla__producto__excluir_de_analitica=False)

from django.db.models import Case, When
monto_linea = Case(
    When(monto_item__gt=0, then=ExpressionWrapper(F('monto_item'), output_field=DecimalField())),
    default=ExpressionWrapper(F('precio') * F('stock'), output_field=DecimalField()),
    output_field=DecimalField(),
)
d_monto_sin_iva_fix = 0
for x in dp.values('productoTalla__producto_id').annotate(u=Sum('stock'), m=Sum(monto_linea)):
    pid = x['productoTalla__producto_id']
    if not pid:
        continue
    p = prods.setdefault(pid, {'u': 0, 'm': 0})
    p['u'] += int(x['u'] or 0)
    p['m'] += int(x['m'] or 0)
    d_monto_sin_iva_fix += int(x['m'] or 0)

# factores IVA (replica independiente sobre TODAS las lineas del dte)
factores = {}
filas_f = (Dte_Productos.objects
           .filter(dte_id__in=base_dp.values('dte_id'),
                   dte__monto_neto__gt=0,
                   dte__monto_con_iva__gt=F('dte__monto_neto'))
           .values('dte_id', neto=F('dte__monto_neto'), bruto=F('dte__monto_con_iva'))
           .annotate(tl=Sum(monto_linea)))
n_netos = 0
monto_diferencial = 0
for f in filas_f:
    neto, bruto, tl = Decimal(f['neto'] or 0), Decimal(f['bruto'] or 0), Decimal(f['tl'] or 0)
    if tl <= 0 or neto <= 0:
        continue
    if abs(tl - neto) <= neto * Decimal('0.01'):
        factores[f['dte_id']] = bruto / neto
        n_netos += 1
print('DTEs con lineas EN NETO detectados:', n_netos)
if factores:
    for x in dp.filter(dte_id__in=list(factores)).values(
            'productoTalla__producto_id', 'dte_id').annotate(m=Sum(monto_linea)):
        p = prods.get(x['productoTalla__producto_id'])
        if not p:
            continue
        base = Decimal(x['m'] or 0)
        dif = int(base * factores[x['dte_id']]) - int(base)
        p['m'] += dif
        monto_diferencial += dif
print('diferencial IVA sumado (neto->bruto):', monto_diferencial)

# devoluciones
dev = (CambioDevolucionDetalle.objects.filter(
    producto_original__isnull=False, cantidad_original__gt=0,
    cambio_devolucion__fecha_ejecucion__date__gte=FI,
    cambio_devolucion__fecha_ejecucion__date__lte=FF,
    producto_original__ProductoTalla__producto__excluir_de_analitica=False,
).values('producto_original__ProductoTalla__producto_id')
    .annotate(u=Sum('cantidad_original'),
              m=Sum(ExpressionWrapper(F('precio_original_unitario') * F('cantidad_original'),
                                      output_field=DecimalField()))))
dev_u = dev_m = dev_omit = 0
for x in dev:
    p = prods.get(x['producto_original__ProductoTalla__producto_id'])
    if not p:
        dev_omit += int(x['u'] or 0)
        continue
    p['u'] -= int(x['u'] or 0)
    p['m'] -= int(x['m'] or 0)
    dev_u += int(x['u'] or 0)
    dev_m += int(x['m'] or 0)
ora_u = sum(p['u'] for p in prods.values())
ora_m = sum(p['m'] for p in prods.values())
print(f'ORACULO espejo: unidades={ora_u} monto={ora_m} skus={len(prods)} '
      f'dev_u={dev_u} dev_m={dev_m} dev_omitidas={dev_omit} '
      f'({round(time.perf_counter()-t0, 1)}s)')
print('VS VISTA: monto vista=', k.get('total_monto'), 'delta=',
      int(k.get('total_monto') or 0) - ora_m,
      '| unid vista=', k.get('total_unidades'), 'delta=',
      int(k.get('total_unidades') or 0) - ora_u)

# ======================= 3. ORACULO cabecera (independiente) ==================
# Para DTEs cuyo 100% de lineas tiene talla analitica: la verdad CON IVA es la
# cabecera monto_con_iva. Mide el drift lineas-vs-cabecera restante.
print('\n--- oraculo cabecera (DTEs full-cubiertos) ---')
tot_lineas = {r_['dte_id']: int(r_['n'] or 0) for r_ in
              Dte_Productos.objects.filter(dte_id__in=base_dp.values('dte_id'))
              .values('dte_id').annotate(n=Count('id'))}
lineas_ok = {r_['dte_id']: int(r_['n'] or 0) for r_ in
             dp.values('dte_id').annotate(n=Count('id'))}
full = [d for d, n in tot_lineas.items() if lineas_ok.get(d, 0) == n]
parcial = [d for d in tot_lineas if d not in set(full)]
cab = Dte.objects.filter(id__in=full).aggregate(m=Sum('monto_con_iva'))
cab_m = int(cab['m'] or 0)
# lo que el reporte suma por esos mismos DTEs (lineas + diferencial IVA)
rep_full = 0
for x in dp.filter(dte_id__in=full).values('dte_id').annotate(m=Sum(monto_linea)):
    base = Decimal(x['m'] or 0)
    fac = factores.get(x['dte_id'])
    rep_full += int(base * fac) if fac else int(base)
print(f'DTEs universo={len(tot_lineas)} full={len(full)} parciales={len(parcial)}')
print(f'cabecera(con_iva) suma={cab_m} | reporte(lineas+fix) suma={rep_full} '
      f'| delta={rep_full - cab_m} ({round(100.0*(rep_full-cab_m)/cab_m, 2) if cab_m else 0}%)')

# desglose del delta por tipo_documento (mezcla neto/bruto residual)
por_tipo = {}
cab_por_dte = dict(Dte.objects.filter(id__in=full).values_list('id', 'monto_con_iva'))
tipo_por_dte = dict(Dte.objects.filter(id__in=full).values_list('id', 'tipo_documento'))
for x in dp.filter(dte_id__in=full).values('dte_id').annotate(m=Sum(monto_linea)):
    base = Decimal(x['m'] or 0)
    fac = factores.get(x['dte_id'])
    rep = int(base * fac) if fac else int(base)
    cabx = int(cab_por_dte.get(x['dte_id']) or 0)
    t = tipo_por_dte.get(x['dte_id']) or '?'
    s = por_tipo.setdefault(t, {'n': 0, 'delta': 0, 'n_desvio': 0})
    s['n'] += 1
    s['delta'] += rep - cabx
    if abs(rep - cabx) > max(2, cabx * 0.005):
        s['n_desvio'] += 1
for t, s in sorted(por_tipo.items()):
    print(f'  {t}: n={s["n"]} delta_total={s["delta"]} docs_con_desvio(>0.5%)={s["n_desvio"]}')

# ======================= 4. Sell-through scope sucursal =======================
print('\n--- PV filtrado por sucursal (sell-through scope) ---')
suc_top = (Ticket.objects.filter(created_at__date__gte=FI, created_at__date__lte=FF,
                                 estado='PAGADO', sucursal__es_centro_distribucion=False)
           .values('sucursal_id', 'sucursal__empresa_id').annotate(n=Count('id'))
           .order_by('-n').first())
print('sucursal top julio:', suc_top)
if suc_top:
    SID = suc_top['sucursal_id']
    r2 = invocar('app.views_modulo_reportes.obtener_productos_vendidos',
                 {'tipo_flujo': 'custom', 'fecha_inicio': '2026-07-01',
                  'fecha_fin': '2026-07-31', 'sucursal_id': SID, 'top_n': 500},
                 suc=SID, emp=suc_top['sucursal__empresa_id'])
    js2 = r2.get('json') or {}
    k2 = js2.get('kpis') or {}
    print('status', r2.get('status'), 'ms', r2.get('ms'), 'nq', r2.get('nq'))
    print('KPIs suc:', {x: k2.get(x) for x in ('total_monto', 'total_unidades',
          'stock_actual_total', 'sell_through_periodo', 'stock_alcance')})
    pids = [p['producto_id'] for p in (js2.get('productos') or [])]
    # oraculo stock: solo productos de ESA sucursal
    ora_stock = Producto_Talla.objects.filter(
        producto__sucursal_id=SID, stock__gt=0,
    ).values('producto_id').annotate(s=Sum('stock'))
    # el kpi cuenta stock de TODOS los productos del universo (no solo top 500)
    print('nota: kpi stock cubre todo el universo; comparo alcance:',
          'sucursal' if k2.get('stock_alcance') == 'sucursal' else 'RED (BUG)')
    # cross-check con stock de la red para los mismos productos
    if pids:
        red = Producto_Talla.objects.filter(producto_id__in=pids, stock__gt=0) \
            .aggregate(s=Sum('stock'))
        suc_only = Producto_Talla.objects.filter(
            producto_id__in=pids, producto__sucursal_id=SID, stock__gt=0) \
            .aggregate(s=Sum('stock'))
        print(f'stock top500: red={int(red["s"] or 0)} sucursal={int(suc_only["s"] or 0)} '
              f'kpi_stock_total={k2.get("stock_actual_total")}')

# ======================= 5. Filtro categoria padre/hija + atributo4 ===========
print('\n--- filtro categoria padre vs hija ---')
padre = (Categoria.objects.filter(padre__isnull=True, subcategorias__isnull=False)
         .exclude(nombre__startswith='_ZZ_')
         .annotate(n=Count('subcategorias')).order_by('-n').first())
if padre:
    hija = (Categoria.objects.filter(padre=padre)
            .annotate(n=Count('categoria_productos')).order_by('-n').first())
    rp = invocar('app.views_modulo_reportes.obtener_productos_vendidos',
                 {'tipo_flujo': 'custom', 'fecha_inicio': '2026-07-01',
                  'fecha_fin': '2026-07-31', 'categoria_id': padre.id})
    rh = invocar('app.views_modulo_reportes.obtener_productos_vendidos',
                 {'tipo_flujo': 'custom', 'fecha_inicio': '2026-07-01',
                  'fecha_fin': '2026-07-31', 'categoria_id': hija.id})
    np_ = (rp.get('json') or {}).get('productos_total')
    nh = (rh.get('json') or {}).get('productos_total')
    print(f'padre "{padre.nombre}"({padre.id})={np_} productos; '
          f'hija "{hija.nombre}"({hija.id})={nh} productos')

# ======================= 6. DOCUMENTOS-VENDEDOR ==============================
print('\n=== DOCUMENTOS-VENDEDOR julio 2026 ===')
v = (Dte.objects.filter(fecha_emision__gte=FI, fecha_emision__lte=FF,
                        tipo_transaccion='VENTA_PUBLICO', vendedor__isnull=False)
     .values('vendedor_id').annotate(n=Count('id')).order_by('-n').first())
print('vendedor top:', v)
if v:
    VID = v['vendedor_id']
    ragg = invocar('app.views_modulo_reportes.obtener_ventas_por_vendedor_reporte',
                   {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'})
    jsa = ragg.get('json') or {}
    fila = next((x for x in (jsa.get('vendedores') or []) if x.get('id') == VID), None)
    print('agregado status', ragg.get('status'), 'ms', ragg.get('ms'), 'nq', ragg.get('nq'))
    print('fila vendedor:', {x: fila.get(x) for x in ('ventas', 'ventas_brutas',
          'devoluciones', 'documentos')} if fila else None)
    rdet = invocar('app.views_modulo_reportes.obtener_documentos_vendedor_reporte',
                   {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31',
                    'vendedor_id': VID})
    jsd = rdet.get('json') or {}
    docs = jsd.get('documentos') or []
    print('detalle status', rdet.get('status'), 'ms', rdet.get('ms'), 'nq', rdet.get('nq'))
    print('detalle: total=', jsd.get('total'), 'cantidad=', jsd.get('cantidad'),
          'tickets_sin_dte=', jsd.get('tickets_sin_dte_cantidad'),
          'tickets_sin_dte_total=', jsd.get('tickets_sin_dte_total'))
    origenes = {}
    for d_ in docs:
        origenes[d_.get('origen')] = origenes.get(d_.get('origen'), 0) + 1
    print('origenes en documentos:', origenes)
    # oraculo independiente
    qs = Dte.objects.filter(
        fecha_emision__gte=FI, fecha_emision__lte=FF, vendedor_id=VID,
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
        estado_dte__in=['EMITIDO', 'ACEPTADO', 'ANULADO'], descartado=False)
    vv = qs.exclude(tipo_documento='NOTA DE CREDITO').aggregate(m=Sum('monto_con_iva'), n=Count('id'))
    nn = qs.filter(tipo_documento='NOTA DE CREDITO').aggregate(m=Sum('monto_con_iva'), n=Count('id'))
    ora_det = int(vv['m'] or 0) - int(nn['m'] or 0)
    print(f'oraculo Dte: ventas={int(vv["m"] or 0)} ({vv["n"]}) - NC={int(nn["m"] or 0)} '
          f'({nn["n"]}) = {ora_det}')
    if fila:
        print('CHECK fila.ventas == detalle.total == oraculo:',
              fila.get('ventas'), jsd.get('total'), ora_det)
print('\nFIN SCRIPT A')
