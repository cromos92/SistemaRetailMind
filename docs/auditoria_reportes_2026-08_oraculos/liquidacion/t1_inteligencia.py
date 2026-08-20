# -*- coding: utf-8 -*-
# TANDA 1 — AUDITORIA inteligencia-compra (SOLO LECTURA). marca=SKECHERS
import json, sys, time
from datetime import timedelta
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from django.test import RequestFactory
from django.db import connection, reset_queries
from django.utils import timezone
from django.db.models import Sum, Count, F, Q, BigIntegerField, Max
from django.db.models.functions import Abs
from django.contrib.auth import get_user_model

from app.models import (AtributoOpcion, EmpresaUser, Movimientos_Producto,
                        Producto_Talla, Sucursal, Ticket_Productos, OpcionMenu,
                        PermisoRol)
from app.constants_kardex import (CONCEPTOS_VENTA, CONCEPTOS_REINGRESO,
                                  CONCEPTOS_ABASTECIMIENTO)
from app.views_inteligencia_compra import obtener_inteligencia_compra

BI = BigIntegerField()
P = print

User = get_user_model()
admin = (User.objects.filter(rol='administrador', is_active=True).first()
         or User.objects.filter(is_superuser=True, is_active=True).first())
emp_ids = list(EmpresaUser.objects.filter(user=admin, status=True)
               .values_list('empresa_id', flat=True))
sucs = list(Sucursal.objects.filter(empresa_id__in=emp_ids)
            .values('id', 'alias', 'es_centro_distribucion', 'empresa_id'))
all_ids = [s['id'] for s in sucs]
tiendas_ids = [s['id'] for s in sucs if not s['es_centro_distribucion']]
P('ADMIN=%s empresas=%s' % (admin.username, emp_ids))
P('SUCS=%s' % [(s['id'], s['alias'], 'CD' if s['es_centro_distribucion'] else 'T') for s in sucs])

# --- permisos: existen las OpcionMenu? ---
oms = list(OpcionMenu.objects.filter(
    Q(codigo__icontains='liquidacion') | Q(codigo__icontains='inteligencia')
).values('codigo', 'activo'))
P('OPCIONMENU=%s' % oms)
for cod in ('plan_liquidacion', 'campanas_liquidacion'):
    roles = list(PermisoRol.objects.filter(opcion_menu__codigo=cod)
                 .values_list('rol', 'puede_ver', 'puede_exportar'))
    P('PERMISOROL %s = %s' % (cod, roles))

marca = (AtributoOpcion.objects.filter(atributo__nombre__icontains='marca',
                                       valor__icontains='SKECHERS').first())
P('MARCA=%s id=%s' % (marca.valor if marca else None, marca.id if marca else None))

# --- invocar la vista real ---
factory = RequestFactory()
req = factory.get('/app/api/inteligencia-compra/',
                  {'marca_id': marca.id, 'sucursal_id': 'todas'})
req.user = admin
req.session = {'idSucursalActual': tiendas_ids[0], 'idEmpresaActual': emp_ids[0]}
connection.force_debug_cursor = True
reset_queries()
t0 = time.perf_counter()
resp = obtener_inteligencia_compra(req)
dt_ms = round((time.perf_counter() - t0) * 1000)
nq = len(connection.queries)
writes = [q['sql'][:80] for q in connection.queries
          if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
connection.force_debug_cursor = False
P('API status=%s queries=%s tiempo_ms=%s escrituras=%s' % (resp.status_code, nq, dt_ms, writes))
body = json.loads(resp.content)
if not body.get('success'):
    P('API ERROR: %s' % body.get('error'))
d = body['data']
P('API kpis=%s' % json.dumps(d['kpis']))
P('API finanzas=%s' % json.dumps(d['finanzas']))
P('API salud={skus:%s dead90:%s dead180:%s dead180_u:%s dead180_costo:%s pct:%s}' % (
    d['salud']['skus_total'], d['salud']['dead90_n'], d['salud']['dead180_n'],
    d['salud']['dead180_u'], d['salud']['dead180_costo'], d['salud']['pct_dead180']))
P('API forecast ttm=%s ttm_prev=%s yoy=%s g=%s forward=%s fc_total=%s' % (
    d['forecast']['ttm'], d['forecast']['ttm_prev'], d['forecast']['yoy'],
    d['forecast']['g'], d['forecast']['forward_annual'], d['forecast']['forecast_total']))
P('API recomendacion comprar=%s cobertura=%s' % (
    d['recomendacion']['comprar'], d['recomendacion']['cobertura_meses']))
P('API ventas_anual=%s' % json.dumps(d['ventas_anual']))

# --- ORACULOS independientes (mismo alcance que la vista: empresas del admin) ---
hoy = timezone.localdate()
base = Movimientos_Producto.objects.filter(
    ProductoTalla__producto__atributo1_id=marca.id,
    ProductoTalla__producto__excluir_de_analitica=False,
    ProductoTalla__producto__sucursal_id__in=all_ids,
    estado='COMPLETADO')
vt = base.filter(concepto__in=CONCEPTOS_VENTA,
                 ProductoTalla__producto__sucursal__es_centro_distribucion=False)

v90 = vt.filter(fecha__gte=hoy - timedelta(days=90), fecha__lt=hoy).aggregate(
    u_abs=Sum(Abs('cantidad'), output_field=BI),
    u_net=Sum('cantidad', output_field=BI),
    n=Count('id'), n_pos=Count('id', filter=Q(cantidad__gt=0)),
    u_pos=Sum('cantidad', filter=Q(cantidad__gt=0), output_field=BI))
P('ORACULO v90: %s' % v90)

rein90 = base.filter(concepto__in=CONCEPTOS_REINGRESO,
                     ProductoTalla__producto__sucursal__es_centro_distribucion=False,
                     fecha__gte=hoy - timedelta(days=90), fecha__lt=hoy).aggregate(
    u=Sum(Abs('cantidad'), output_field=BI), n=Count('id'))
P('ORACULO reingresos90 (NC/devol NO restadas): %s' % rein90)

tk90 = Ticket_Productos.objects.filter(
    ProductoTalla__producto__atributo1_id=marca.id,
    ProductoTalla__producto__excluir_de_analitica=False,
    ProductoTalla__producto__sucursal__es_centro_distribucion=False,
    ProductoTalla__producto__sucursal_id__in=all_ids,
    idTicket__created_at__date__gte=hoy - timedelta(days=90),
    idTicket__created_at__date__lt=hoy,
    idTicket__estado='PAGADO').aggregate(u=Sum('stock', output_field=BI), n=Count('id'))
P('ORACULO tickets90 (PAGADO, lineas): %s' % tk90)

pt_t = Producto_Talla.objects.filter(
    producto__atributo1_id=marca.id, producto__excluir_de_analitica=False,
    producto__sucursal_id__in=all_ids, stock__gt=0,
    producto__sucursal__es_centro_distribucion=False)
stk = pt_t.aggregate(s=Sum('stock', output_field=BI), skus=Count('id'),
                     inv_costo=Sum(F('stock') * F('producto__costo'), output_field=BI),
                     inv_precio=Sum(F('stock') * F('producto__precioventa'), output_field=BI))
P('ORACULO stock tiendas: %s' % stk)

tv = vt.filter(fecha__gte=hoy - timedelta(days=365))
fin = tv.aggregate(m_venta=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI),
                   m_costo=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI),
                   u=Sum(Abs('cantidad'), output_field=BI),
                   n=Count('id'), n_c0=Count('id', filter=Q(costo__lte=0)),
                   u_c0=Sum(Abs('cantidad'), filter=Q(costo__lte=0), output_field=BI))
P('ORACULO ttm365: %s' % fin)
if fin['m_venta'] and fin['m_costo'] and stk['inv_costo']:
    marg = fin['m_venta'] - fin['m_costo']
    P('ORACULO gmroi=(venta-costo)/inv_costo = %.2f ; margen_pct=%.1f' % (
        marg / stk['inv_costo'], 100.0 * marg / fin['m_venta']))
if fin['u'] and stk['s']:
    P('ORACULO wos_trailing = stock/(u365/52) = %.1f semanas' % (stk['s'] / (fin['u'] / 52.0)))
    P('ORACULO cobertura_trailing = stock/(u365/12) = %.1f meses' % (stk['s'] / (fin['u'] / 12.0)))

# dead stock replica
vend180_ids = vt.filter(fecha__gte=hoy - timedelta(days=180)).values('ProductoTalla')
dead180 = pt_t.exclude(id__in=vend180_ids).aggregate(
    n=Count('id'), u=Sum('stock', output_field=BI),
    c=Sum(F('stock') * F('producto__costo'), output_field=BI))
P('ORACULO dead180: %s' % dead180)

# sucursales vaciadas 14-ago
for sid in (8, 11):
    row = next((s for s in sucs if s['id'] == sid), None)
    st = Producto_Talla.objects.filter(producto__sucursal_id=sid, stock__gt=0).aggregate(
        s=Sum('stock', output_field=BI), n=Count('id'))
    P('SUC %s %s: %s' % (sid, row, st))

# ventas TTM de productos EXCLUIDOS de analitica (sesgo si alguien no filtra)
excl = Movimientos_Producto.objects.filter(
    ProductoTalla__producto__excluir_de_analitica=True,
    ProductoTalla__producto__sucursal_id__in=tiendas_ids,
    estado='COMPLETADO', concepto__in=CONCEPTOS_VENTA,
    fecha__gte=hoy - timedelta(days=365)).aggregate(
    u=Sum(Abs('cantidad'), output_field=BI), n=Count('id'),
    venta=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI),
    costo=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI))
P('ORACULO ventasTTM de productos excluir_de_analitica=True (tiendas): %s' % excl)
P('FIN T1')
