# -*- coding: utf-8 -*-
# TANDA 2 — AUDITORIA plan-liquidacion (SOLO LECTURA)
import json, sys, time
from datetime import timedelta, date
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from django.test import RequestFactory
from django.db import connection, reset_queries
from django.utils import timezone
from django.db.models import Sum, Count, F, Q, BigIntegerField
from django.db.models.functions import Abs, TruncMonth
from django.contrib.auth import get_user_model

from app.models import (EmpresaUser, Movimientos_Producto, Producto,
                        Producto_Talla, Sucursal, LoteProducto)
from app.constants_kardex import CONCEPTOS_VENTA
from app.views_inteligencia_compra import (
    obtener_plan_liquidacion, obtener_plan_liquidacion_por_anio,
    obtener_plan_liquidacion_detalle, exportar_plan_liquidacion_excel,
    _scope_plan, _detalle_query)

BI = BigIntegerField()
P = print
User = get_user_model()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())
emp_ids = list(EmpresaUser.objects.filter(user=admin, status=True)
               .values_list('empresa_id', flat=True).distinct())
sucs = list(Sucursal.objects.filter(empresa_id__in=emp_ids)
            .values('id', 'alias', 'es_centro_distribucion'))
tiendas_ids = [s['id'] for s in sucs if not s['es_centro_distribucion']]
factory = RequestFactory()


def invocar(view, params):
    req = factory.get('/x', params)
    req.user = admin
    req.session = {'idSucursalActual': tiendas_ids[0], 'idEmpresaActual': emp_ids[0]}
    connection.force_debug_cursor = True
    reset_queries()
    t0 = time.perf_counter()
    resp = view(req)
    ms = round((time.perf_counter() - t0) * 1000)
    nq = len(connection.queries)
    w = [q['sql'][:60] for q in connection.queries
         if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
    connection.force_debug_cursor = False
    return resp, nq, ms, w


# ---- 1. ranking principal ----
resp, nq, ms, w = invocar(obtener_plan_liquidacion, {})
P('PLAN status=%s queries=%s ms=%s writes=%s' % (resp.status_code, nq, ms, w))
d = json.loads(resp.content)['data']
t = d['totales']
P('PLAN totales=%s' % json.dumps(t))
top = d['marcas'][0]
P('PLAN fila#1=%s' % json.dumps(top))
P('PLAN top5=%s' % json.dumps([{k: f.get(k) for k in ('marca', 'stock', 'valor_costo', 'ttm_u', 'rotacion', 'gmroi', 'cobertura', 'dead_costo', 'accion')} for f in d['marcas'][:5]]))

# ---- 2. por-anio ----
resp2, nq2, ms2, w2 = invocar(obtener_plan_liquidacion_por_anio, {})
P('PORANIO status=%s queries=%s ms=%s writes=%s' % (resp2.status_code, nq2, ms2, w2))
d2 = json.loads(resp2.content)
P('PORANIO anios=%s' % json.dumps([{k: a[k] for k in ('anio', 'valor', 'pares', 'productos')} for a in d2['anios']]))
P('PORANIO totales=%s' % json.dumps(d2['totales']))

# ---- 3. detalle p1 ----
resp3, nq3, ms3, w3 = invocar(obtener_plan_liquidacion_detalle,
                              {'orden': '-valor_costo', 'page_size': '50'})
P('DET status=%s queries=%s ms=%s writes=%s' % (resp3.status_code, nq3, ms3, w3))
d3 = json.loads(resp3.content)
P('DET total_productos=%s' % d3['total'])
P('DET top3=%s' % json.dumps([{k: f.get(k) for k in ('producto_id', 'articulo', 'sucursal', 'stock_u', 'costo', 'valor_costo', 'fecha_fifo', 'antiguedad_fuente', 'anio', 'dias_antiguedad', 'ultima_venta', 'u365', 'descuento_sugerido', 'precio_liquidacion', 'precioventa')} for f in d3['filas'][:3]]))

# ---- ORACULOS ----
hoy = timezone.localdate()
pt = Producto_Talla.objects.filter(
    stock__gt=0, producto__excluir_de_analitica=False,
    producto__sucursal_id__in=tiendas_ids)
o_tot = pt.aggregate(stock_u=Sum('stock', output_field=BI),
                     valor=Sum(F('stock') * F('producto__costo'), output_field=BI),
                     skus=Count('id'))
P('ORACULO tot tiendas: %s' % o_tot)
mov = Movimientos_Producto.objects.filter(
    estado='COMPLETADO', concepto__in=CONCEPTOS_VENTA,
    ProductoTalla__producto__sucursal_id__in=tiendas_ids)
vend180 = mov.filter(fecha__gte=hoy - timedelta(days=180)).values('ProductoTalla')
o_dead = pt.exclude(id__in=vend180).aggregate(
    u=Sum('stock', output_field=BI),
    c=Sum(F('stock') * F('producto__costo'), output_field=BI), n=Count('id'))
P('ORACULO dead tiendas: %s' % o_dead)

# top marca fila#1: recomputo independiente
mid = top['id']
pt_m = pt.filter(producto__atributo1_id=mid)
o_m = pt_m.aggregate(stock_u=Sum('stock', output_field=BI),
                     valor=Sum(F('stock') * F('producto__costo'), output_field=BI),
                     skus=Count('id'))
o_m_dead = pt_m.exclude(id__in=vend180).aggregate(
    u=Sum('stock', output_field=BI),
    c=Sum(F('stock') * F('producto__costo'), output_field=BI), n=Count('id'))
mov_m = mov.filter(fecha__gte=hoy - timedelta(days=365),
                   ProductoTalla__producto__atributo1_id=mid)
o_m_ttm_conexcl = mov_m.aggregate(u=Sum(Abs('cantidad'), output_field=BI),
                                  v=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI),
                                  c=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI))
o_m_ttm_limpio = mov_m.filter(ProductoTalla__producto__excluir_de_analitica=False
                              ).aggregate(u=Sum(Abs('cantidad'), output_field=BI),
                                          v=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI),
                                          c=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI))
P('ORACULO marca#1 (%s) inv=%s dead=%s' % (top.get('marca'), o_m, o_m_dead))
P('ORACULO marca#1 ttm con-excluidos=%s ttm limpio=%s' % (o_m_ttm_conexcl, o_m_ttm_limpio))

# ventas TTM de productos excluidos por marca (sesgo mov_base sin filtro analitica)
exq = (Movimientos_Producto.objects.filter(
    estado='COMPLETADO', concepto__in=CONCEPTOS_VENTA,
    ProductoTalla__producto__sucursal_id__in=tiendas_ids,
    ProductoTalla__producto__excluir_de_analitica=True,
    fecha__gte=hoy - timedelta(days=365))
    .values('ProductoTalla__producto__atributo1__valor')
    .annotate(u=Sum(Abs('cantidad'), output_field=BI),
              v=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI))
    .order_by('-u')[:6])
P('ORACULO ventasTTM excluidos por marca: %s' % list(exq))

# costo=0 en movimientos de venta TTM (sesgo margen/GMROI global)
c0 = mov.filter(fecha__gte=hoy - timedelta(days=365)).aggregate(
    n=Count('id'), n_c0=Count('id', filter=Q(costo__lte=0)),
    u=Sum(Abs('cantidad'), output_field=BI),
    u_c0=Sum(Abs('cantidad'), filter=Q(costo__lte=0), output_field=BI))
P('ORACULO ttm global costo<=0: %s' % c0)

# ---- consistencia detalle vs ranking ----
req = factory.get('/x', {})
req.user = admin
req.session = {'idSucursalActual': tiendas_ids[0], 'idEmpresaActual': emp_ids[0]}
base_pt, mov_base, ctx = _scope_plan(req)
qs = _detalle_query(base_pt, mov_base, hoy)
o_det = qs.aggregate(n=Count('id'), stock=Sum('stock_u', output_field=BI),
                     valor=Sum(F('costo') * F('stock_u'), output_field=BI))
P('ORACULO detalle-universo: %s (vs ranking tot stock=%s valor=%s ; det total=%s)' % (
    o_det, t['stock_u'], t['valor_costo'], d3['total']))

# ---- sesgo reconciliacion: lotes vivos por mes de ingreso ----
lot = LoteProducto.objects.filter(
    activo=True, agotado=False, cantidad_disponible__gt=0,
    producto_talla__producto__sucursal_id__in=tiendas_ids,
    producto_talla__producto__excluir_de_analitica=False,
    producto_talla__stock__gt=0)
por_mes = list(lot.annotate(m=TruncMonth('fecha_ingreso')).values('m')
               .annotate(n=Count('id'), u=Sum('cantidad_disponible', output_field=BI))
               .order_by('-u')[:8])
P('ORACULO lotes vivos por mes (top8 por unidades): %s' %
  [(str(r['m'].date()), r['n'], r['u']) for r in por_mes])

# capital cuyo lote mas antiguo es de la ventana de reconciliacion pero el
# producto existia mucho antes (fecha_creacion corregida desde el kardex)
recon = qs.filter(fecha_lote__gte=date(2026, 1, 1), fecha_lote__lt=date(2026, 3, 1))
o_recon = recon.aggregate(n=Count('id'), stock=Sum('stock_u', output_field=BI),
                          valor=Sum(F('costo') * F('stock_u'), output_field=BI))
o_recon_viejo = recon.filter(fecha_creacion__lt=date(2025, 10, 1)).aggregate(
    n=Count('id'), stock=Sum('stock_u', output_field=BI),
    valor=Sum(F('costo') * F('stock_u'), output_field=BI))
P('ORACULO aging=ene/feb-2026 (lote): %s ; de esos, producto creado antes de oct-2025: %s' % (
    o_recon, o_recon_viejo))

# ---- 3 productos del detalle: costo ficha vs FIFO ----
for f in d3['filas'][:3]:
    pid = f['producto_id']
    lots = LoteProducto.objects.filter(
        producto_talla__producto_id=pid, activo=True, agotado=False,
        cantidad_disponible__gt=0).aggregate(
        u=Sum('cantidad_disponible', output_field=BI),
        v=Sum(F('cantidad_disponible') * F('costo_unitario'), output_field=BI))
    st = Producto_Talla.objects.filter(producto_id=pid, stock__gt=0).aggregate(
        s=Sum('stock', output_field=BI))
    pr = Producto.objects.filter(id=pid).values('costo', 'fecha_creacion').first()
    P('PROD %s %s: det{stock:%s valor:%s} plano:%s fifo:{u:%s v:%s} costo_ficha:%s creado:%s' % (
        pid, f['articulo'], f['stock_u'], f['valor_costo'], st['s'],
        lots['u'], lots['v'], pr['costo'], str(pr['fecha_creacion'])[:10]))

P('FIN T2')
