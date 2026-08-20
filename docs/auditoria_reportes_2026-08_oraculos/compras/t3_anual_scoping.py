# -*- coding: utf-8 -*-
# TANDA 3 — anual 2026, api_rendimiento_compras, oraculos Ticket, scoping restringido.
import json
import sys
import time
from datetime import date
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from django.db import connection, transaction
from django.db.models import Sum, Count, Q
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.contrib.auth import get_user_model

from app import views_modulo_reportes as vr
from app.models import Dte, Ticket, Ticket_Productos, EmpresaUser, Sucursal
from app.utils_permisos import usuario_puede_ver_todas_sucursales

U = get_user_model()
admin = U.objects.get(username='javier')
factory = RequestFactory()
ESCR = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP')


def invocar(view, params, usuario, suc, emp):
    req = factory.get('/_t', data=params)
    req.user = usuario
    req.session = {'idSucursalActual': suc, 'idEmpresaActual': emp}
    t0 = time.perf_counter()
    with CaptureQueriesContext(connection) as cap:
        with transaction.atomic():
            resp = view(req)
            transaction.set_rollback(True)
    ms = round((time.perf_counter() - t0) * 1000)
    malas = [q['sql'][:80] for q in cap.captured_queries
             if q['sql'].lstrip().upper().startswith(ESCR)]
    try:
        js = json.loads(resp.content)
    except Exception:
        js = None
    return resp.status_code, js, len(cap.captured_queries), ms, malas


print('=== 1. METRICAS ANUAL 2026 (ceguera unidades a nivel anio) ===')
filtros = {
    'anio': 2026, 'periodo': 'anual', 'temporada': '', 'proveedor_id': '',
    'empresas_ids': [1802], 'empresa_activa_id': 1802, 'es_vendedora': False,
    'empresa_receptora_id': None, 'sucursal_activa_id': 1,
    'fecha_inicio': date(2026, 1, 1), 'fecha_fin': date(2026, 12, 31),
    'hoy': timezone.localdate(), 'incluye_cd': True,
    'compras_ids': None, 'temporada_aplicada': False,
}
t0 = time.perf_counter()
with CaptureQueriesContext(connection) as cap:
    with transaction.atomic():
        ranking = vr.calcular_ranking_proveedores_compras(filtros)
        ep = vr.calcular_estado_pagos_compras(filtros)
        er = vr.calcular_estado_recepciones_compras(filtros)
        mr = vr.calcular_metricas_recepcion_compras(filtros, er)
        met = vr.calcular_metricas_compras(filtros, ranking, ep, mr)
        transaction.set_rollback(True)
print('perf anual: %sq / %sms' % (len(cap.captured_queries), round((time.perf_counter() - t0) * 1000)))
for k in ('total_compras', 'inversion_total', 'devoluciones_nc', 'unidades_esperadas',
          'unidades_recepcionadas', 'cumplimiento_general', 'costo_promedio_unidad',
          'proveedores_activos', 'roi_promedio', 'pendiente_pago', 'pct_facturas_pagadas'):
    print('  %s = %s' % (k, met.get(k)))
print('  estado_recepciones:', er)
print('  suma ranking inversion:', round(sum(f['inversion'] for f in ranking)),
      'vs cabecera', round(met['inversion_total']))

print()
print('=== 2. api_rendimiento_compras anio=2026 (admin, sin sucursal) ===')
st, js, nq, ms, malas = invocar(vr.api_rendimiento_compras, {'anio': '2026'}, admin, 1, 1802)
print('status=%s queries=%s ms=%s escrituras=%s' % (st, nq, ms, malas))
if js and js.get('success'):
    r = js['resumen']
    for k in ('total_comprado', 'total_despachado', 'total_vendido', 'total_inversion',
              'total_ingreso_ventas', 'margen_real', 'rotacion_global', 'roi_real',
              'total_otros_egresos', 'stock_sin_vender'):
        print('  %s = %s' % (k, r.get(k)))
    print('  fuente:', js.get('fuente_datos'))
    print('  nota:', (js.get('nota_metodologia') or '')[:160])
elif js:
    print('ERROR:', js.get('error'))

print()
print('=== 3. ORACULO Ticket 2026: fecha(auto_now) vs created_at, PENDIENTE ===')
with transaction.atomic():
    a = Ticket_Productos.objects.filter(idTicket__fecha__year=2026,
                                        idTicket__estado__in=['PAGADO', 'PENDIENTE']).aggregate(
        u=Sum('stock'), m=Sum('subtotal'))
    b = Ticket_Productos.objects.filter(idTicket__fecha__year=2026,
                                        idTicket__estado='PAGADO').aggregate(
        u=Sum('stock'), m=Sum('subtotal'))
    c = Ticket_Productos.objects.filter(idTicket__created_at__year=2026,
                                        idTicket__estado='PAGADO').aggregate(
        u=Sum('stock'), m=Sum('subtotal'))
    pend = Ticket.objects.filter(fecha__year=2026, estado='PENDIENTE').aggregate(
        n=Count('id'), m=Sum('total'))
    drift = Ticket.objects.filter(fecha__year=2026, created_at__year__lt=2026).count()
    transaction.set_rollback(True)
print('regla del reporte (fecha, PAGADO+PENDIENTE): uds=%s monto=%s' % (a['u'], a['m']))
print('solo PAGADO (fecha):                        uds=%s monto=%s' % (b['u'], b['m']))
print('solo PAGADO (created_at):                   uds=%s monto=%s' % (c['u'], c['m']))
print('tickets PENDIENTE con fecha 2026: n=%s monto=%s' % (pend['n'], pend['m']))
print('tickets fecha=2026 pero created_at<2026 (arrastrados por auto_now):', drift)

print()
print('=== 4. delta despachos-detallado: descartados+NC julio ===')
crudo = Dte.objects.filter(tipo_transaccion='COMPRA', fecha_emision__range=(date(2026, 7, 1), date(2026, 7, 31)))
d_desc = crudo.filter(descartado=True).aggregate(n=Count('id'), m=Sum('monto_con_iva'))
d_nc = crudo.exclude(descartado=True).filter(tipo_documento='NOTA DE CREDITO').aggregate(n=Count('id'), m=Sum('monto_con_iva'))
print('descartados julio:', d_desc, '| NC vivas julio:', d_nc)

print()
print('=== 5. USUARIO RESTRINGIDO (otra empresa, sin CD) ===')
restr = None
for u in U.objects.filter(is_active=True, is_superuser=False).exclude(rol='administrador')[:80]:
    eus = list(EmpresaUser.objects.filter(user=u, status=True).values_list('empresa_id', 'sucursal_id'))
    if not eus:
        continue
    empresas = {e for e, _ in eus}
    if 1802 in empresas:
        continue
    try:
        if usuario_puede_ver_todas_sucursales(u):
            continue
    except Exception:
        continue
    suc = next((s for _, s in eus if s), None) or Sucursal.objects.filter(
        empresa_id__in=empresas).values_list('id', flat=True).first()
    restr = (u, suc, next(iter(empresas)))
    break
if not restr:
    print('sin usuario restringido utilizable')
else:
    u, suc, emp = restr
    print('restringido: %s empresas!=1802 emp=%s suc=%s' % (u.username, emp, suc))
    st, js, nq, ms, _ = invocar(vr.api_reporte_compras, {'anio': '2026', 'periodo': 'semana'}, u, suc, emp)
    inv = js['metricas']['inversion_total'] if js and js.get('success') else None
    print('api_reporte_compras: status=%s inv=%s empresas=%s (fuga si inv>0 con datos de 1802)' % (
        st, inv, js['filtros_aplicados']['empresas_ids'] if js and js.get('success') else '?'))
    st, js, nq, ms, _ = invocar(vr.api_reporte_rendimiento_proveedor,
                                {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, u, suc, emp)
    if js and js.get('success'):
        print('rendimiento-proveedor: status=%s proveedores=%s comprados=%s vendidos=%s' % (
            st, js['kpis']['total_proveedores'], js['kpis']['total_comprados'], js['kpis']['total_vendidos']))
    else:
        print('rendimiento-proveedor: status=%s err=%s' % (st, js and js.get('error')))
    st, js, nq, ms, _ = invocar(vr.api_reporte_recepciones_detallado,
                                {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'}, u, suc, emp)
    if js and js.get('success'):
        print('recepciones-detallado: status=%s items=%s uds=%s sucursales=%s' % (
            st, js['resumen']['total_items'], js['resumen']['total_unidades'],
            [s['sucursal'] for s in js['por_sucursal'][:6]]))
    else:
        print('recepciones-detallado: status=%s err=%s' % (st, js and js.get('error')))
    st, js, nq, ms, _ = invocar(vr.api_reporte_despachos_detallado,
                                {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-07'}, u, suc, emp)
    if js and js.get('success'):
        print('despachos-detallado (1 semana): status=%s docs=%s monto=%s' % (
            st, js['resumen']['total_documentos'], js['resumen']['monto_total']))
    else:
        print('despachos-detallado: status=%s err=%s' % (st, js and js.get('error')))
print('FIN T3')
