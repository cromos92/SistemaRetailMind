# -*- coding: utf-8 -*-
# Script A2: oraculos productos-vendidos + particiones/heatmap + documentos-vendedor
import sys
import time
from datetime import date
from decimal import Decimal

sys.path.insert(0, r'C:\Users\cromo\AppData\Local\Temp\claude\c--Users-cromo-Documents-DjangoProyects-SistemaRetailMind\15dd0fbe-a29a-4c80-8ad2-c70c318e71b7\scratchpad\ventas_deriv')
from _boot import invocar, ADMIN  # noqa: E402

from django.db.models import (Case, Count, DecimalField, ExpressionWrapper, F,
                              Sum, When)  # noqa: E402
from app.models import (CambioDevolucionDetalle, Dte, Dte_Productos,
                        Ticket_Productos)  # noqa: E402

FI, FF = date(2026, 7, 1), date(2026, 7, 31)
print('ADMIN:', ADMIN.username)

# ---------- vista (de nuevo, para particiones/heatmap) ----------
r = invocar('app.views_modulo_reportes.obtener_productos_vendidos',
            {'tipo_flujo': 'custom', 'fecha_inicio': '2026-07-01',
             'fecha_fin': '2026-07-31', 'top_n': 500})
js = r.get('json') or {}
k = js.get('kpis') or {}
print('vista: status', r['status'], 'ms', r['ms'], 'nq', r['nq'],
      'monto', k.get('total_monto'), 'unid', k.get('total_unidades'),
      'skus', k.get('total_skus'))
for dim in ('por_marca', 'por_categoria', 'por_sexo', 'por_genero', 'por_especialidad'):
    filas = js.get(dim) or []
    s = sum(int(f.get('monto') or 0) for f in filas)
    sinid = sum(int(f.get('monto') or 0) for f in filas if not f.get('id'))
    print(f'  {dim}: n={len(filas)} suma_monto={s} monto_sin_id={sinid}')
heat = js.get('heatmap') or []
print('  heatmap: n=', len(heat), 'suma=', sum(int(h.get('monto') or 0) for h in heat))
zz = [f['nombre'] for f in (js.get('por_categoria') or [])
      if str(f.get('nombre', '')).startswith('_ZZ_')]
con_flecha = sum(1 for f in (js.get('por_categoria') or []) if '›' in str(f.get('nombre', '')))
print('  por_categoria: _ZZ_=', len(zz), 'labels Padre›Hijo=', con_flecha)

# ---------- oraculo espejo ----------
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
print('lado tickets-sin-DTE: monto=', t_monto)

base_dp = Dte_Productos.objects.filter(
    dte__fecha_emision__gte=FI, dte__fecha_emision__lte=FF,
    dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
).exclude(dte__estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']) \
 .exclude(dte__tipo_documento='NOTA DE CREDITO') \
 .exclude(dte__receptor__isnull=False, dte__receptor_id=F('dte__emisor_id'))
dp = base_dp.filter(productoTalla__isnull=False,
                    productoTalla__producto__excluir_de_analitica=False)

monto_linea = Case(
    When(monto_item__gt=0, then=ExpressionWrapper(F('monto_item'),
                                                  output_field=DecimalField())),
    default=ExpressionWrapper(F('precio') * F('stock'), output_field=DecimalField()),
    output_field=DecimalField(),
)
d_monto = 0
for x in dp.values('productoTalla__producto_id').annotate(u=Sum('stock'), m=Sum(monto_linea)):
    pid = x['productoTalla__producto_id']
    if not pid:
        continue
    p = prods.setdefault(pid, {'u': 0, 'm': 0})
    p['u'] += int(x['u'] or 0)
    p['m'] += int(x['m'] or 0)
    d_monto += int(x['m'] or 0)
print('lado DTE (lineas, sin fix IVA): monto=', d_monto)

factores = {}
filas_f = (Dte_Productos.objects
           .filter(dte_id__in=base_dp.values('dte_id'),
                   dte__monto_neto__gt=0,
                   dte__monto_con_iva__gt=F('dte__monto_neto'))
           .values('dte_id', neto=F('dte__monto_neto'), bruto=F('dte__monto_con_iva'))
           .annotate(tl=Sum(monto_linea)))
n_netos = 0
no_cuadra = []
for f in filas_f:
    neto, bruto, tl = Decimal(f['neto'] or 0), Decimal(f['bruto'] or 0), Decimal(f['tl'] or 0)
    if tl <= 0 or neto <= 0:
        continue
    if abs(tl - neto) <= neto * Decimal('0.01'):
        factores[f['dte_id']] = bruto / neto
        n_netos += 1
    elif abs(tl - bruto) > bruto * Decimal('0.01'):
        no_cuadra.append((f['dte_id'], int(tl), int(neto), int(bruto)))
print('DTEs lineas EN NETO:', n_netos, '| DTEs que no cuadran ni con neto ni bruto:',
      len(no_cuadra))
for d_ in no_cuadra[:8]:
    print('   no-cuadra dte', d_)
monto_dif = 0
if factores:
    for x in dp.filter(dte_id__in=list(factores)).values(
            'productoTalla__producto_id', 'dte_id').annotate(m=Sum(monto_linea)):
        p = prods.get(x['productoTalla__producto_id'])
        if not p:
            continue
        base = Decimal(x['m'] or 0)
        dif = int(base * factores[x['dte_id']]) - int(base)
        p['m'] += dif
        monto_dif += dif
print('diferencial IVA neto->bruto sumado:', monto_dif)

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
      f'dev_u={dev_u} dev_m={dev_m} omitidas={dev_omit} '
      f'({round(time.perf_counter() - t0, 1)}s)')
print('DELTA vista-oraculo: monto=', int(k.get('total_monto') or 0) - ora_m,
      'unid=', int(k.get('total_unidades') or 0) - ora_u,
      'skus=', int(k.get('total_skus') or 0) - len(prods))

# ---------- oraculo cabecera ----------
print('--- oraculo cabecera ---')
tot_lineas = {y['dte_id']: int(y['n'] or 0) for y in
              Dte_Productos.objects.filter(dte_id__in=base_dp.values('dte_id'))
              .values('dte_id').annotate(n=Count('id'))}
lineas_ok = {y['dte_id']: int(y['n'] or 0) for y in
             dp.values('dte_id').annotate(n=Count('id'))}
full = [d_ for d_, n in tot_lineas.items() if lineas_ok.get(d_, 0) == n]
cab_por_dte = dict(Dte.objects.filter(id__in=full).values_list('id', 'monto_con_iva'))
tipo_por_dte = dict(Dte.objects.filter(id__in=full).values_list('id', 'tipo_documento'))
cab_m = sum(int(v or 0) for v in cab_por_dte.values())
rep_full = 0
por_tipo = {}
for x in dp.filter(dte_id__in=full).values('dte_id').annotate(m=Sum(monto_linea)):
    base = Decimal(x['m'] or 0)
    fac = factores.get(x['dte_id'])
    rep = int(base * fac) if fac else int(base)
    rep_full += rep
    cabx = int(cab_por_dte.get(x['dte_id']) or 0)
    t = tipo_por_dte.get(x['dte_id']) or '?'
    s = por_tipo.setdefault(t, {'n': 0, 'delta': 0, 'n_desvio': 0, 'monto_cab': 0})
    s['n'] += 1
    s['delta'] += rep - cabx
    s['monto_cab'] += cabx
    if abs(rep - cabx) > max(2, cabx * 0.005):
        s['n_desvio'] += 1
print(f'DTEs universo={len(tot_lineas)} full-cubiertos={len(full)} '
      f'parciales={len(tot_lineas) - len(full)}')
pct = round(100.0 * (rep_full - cab_m) / cab_m, 2) if cab_m else 0
print(f'cabecera={cab_m} reporte(lineas+fix)={rep_full} delta={rep_full - cab_m} ({pct}%)')
for t, s in sorted(por_tipo.items()):
    print(f'  {t}: n={s["n"]} monto_cab={s["monto_cab"]} delta={s["delta"]} '
          f'docs_desvio>0.5%={s["n_desvio"]}')

# DTEs parciales: cuanto monto de lineas queda fuera (lineas sin talla / excluidas)
parciales = [d_ for d_ in tot_lineas if d_ not in set(full)]
if parciales:
    fuera = Dte_Productos.objects.filter(dte_id__in=parciales).exclude(
        id__in=dp.filter(dte_id__in=parciales).values('id')
    ).aggregate(m=Sum(monto_linea), n=Count('id'))
    print(f'lineas EXCLUIDAS en DTEs parciales: n={fuera["n"]} monto={int(fuera["m"] or 0)}')

# ---------- documentos-vendedor ----------
print('=== DOCUMENTOS-VENDEDOR julio ===')
v = (Dte.objects.filter(fecha_emision__gte=FI, fecha_emision__lte=FF,
                        tipo_transaccion='VENTA_PUBLICO', vendedor__isnull=False)
     .values('vendedor_id').annotate(n=Count('id')).order_by('-n').first())
VID = v['vendedor_id']
print('vendedor top:', v)
ragg = invocar('app.views_modulo_reportes.obtener_ventas_por_vendedor_reporte',
               {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'})
jsa = ragg.get('json') or {}
fila = next((x for x in (jsa.get('vendedores') or []) if x.get('id') == VID), None)
print('agregado: status', ragg['status'], 'ms', ragg['ms'], 'nq', ragg['nq'])
if fila:
    print('fila vendedor:', {x: fila.get(x) for x in
          ('ventas', 'ventas_brutas', 'devoluciones', 'documentos')})
rdet = invocar('app.views_modulo_reportes.obtener_documentos_vendedor_reporte',
               {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31',
                'vendedor_id': VID})
jsd = rdet.get('json') or {}
docs = jsd.get('documentos') or []
print('detalle: status', rdet['status'], 'ms', rdet['ms'], 'nq', rdet['nq'],
      '| total=', jsd.get('total'), 'cantidad=', jsd.get('cantidad'),
      '| tickets_sin_dte=', jsd.get('tickets_sin_dte_cantidad'),
      'monto', jsd.get('tickets_sin_dte_total'))
origenes = {}
for d_ in docs:
    origenes[d_.get('origen')] = origenes.get(d_.get('origen'), 0) + 1
print('origenes:', origenes,
      '| NC negativas:', sum(1 for d_ in docs if d_.get('es_nota_credito') and d_['monto'] < 0))
qs = Dte.objects.filter(
    fecha_emision__gte=FI, fecha_emision__lte=FF, vendedor_id=VID,
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
    estado_dte__in=['EMITIDO', 'ACEPTADO', 'ANULADO'], descartado=False)
vv = qs.exclude(tipo_documento='NOTA DE CREDITO').aggregate(m=Sum('monto_con_iva'), n=Count('id'))
nn = qs.filter(tipo_documento='NOTA DE CREDITO').aggregate(m=Sum('monto_con_iva'), n=Count('id'))
ora_det = int(vv['m'] or 0) - int(nn['m'] or 0)
print(f'oraculo Dte: ventas={int(vv["m"] or 0)}({vv["n"]}) - NC={int(nn["m"] or 0)}({nn["n"]}) '
      f'= {ora_det}')
print('CHECK fila.ventas / detalle.total / oraculo:',
      fila.get('ventas') if fila else None, jsd.get('total'), ora_det)
print('FIN A2')
