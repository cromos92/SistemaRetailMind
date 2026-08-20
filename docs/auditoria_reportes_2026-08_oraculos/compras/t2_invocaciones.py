# -*- coding: utf-8 -*-
# TANDA 2 — reporte=$X vs oraculo=$Y (JULIO 2026) + perf. SOLO LECTURA.
import json
import sys
import time
from datetime import date
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from django.db import connection, transaction
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.contrib.auth import get_user_model

from app import views_modulo_reportes as vr

FI, FF = date(2026, 7, 1), date(2026, 7, 31)
U = get_user_model()
admin = U.objects.get(username='javier')
factory = RequestFactory()

ESCR = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP')


def invocar(view, params, suc=1, emp=1802):
    req = factory.get('/_t', data=params)
    req.user = admin
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


print('=== 1. FUNCIONES DEL REPORTE DE COMPRAS con ventana JULIO 2026 ===')
filtros = {
    'anio': 2026, 'periodo': 'custom-julio', 'temporada': '', 'proveedor_id': '',
    'empresas_ids': [1802], 'empresa_activa_id': 1802, 'es_vendedora': False,
    'empresa_receptora_id': None, 'sucursal_activa_id': 1,
    'fecha_inicio': FI, 'fecha_fin': FF, 'hoy': timezone.localdate(),
    'incluye_cd': True, 'compras_ids': None, 'temporada_aplicada': False,
}
t0 = time.perf_counter()
with CaptureQueriesContext(connection) as cap:
    with transaction.atomic():
        ranking = vr.calcular_ranking_proveedores_compras(filtros)
        estado_pagos = vr.calcular_estado_pagos_compras(filtros)
        estado_recep = vr.calcular_estado_recepciones_compras(filtros)
        met_recep = vr.calcular_metricas_recepcion_compras(filtros, estado_recep)
        metricas = vr.calcular_metricas_compras(filtros, ranking, estado_pagos, met_recep)
        evolucion = vr.calcular_evolucion_mensual_compras(filtros)
        vencimientos = vr.calcular_vencimientos_compras(filtros)
        pend = vr.obtener_pagos_pendientes_compras(filtros)
        transaction.set_rollback(True)
ms = round((time.perf_counter() - t0) * 1000)
print('perf funciones julio: %sq / %sms' % (len(cap.captured_queries), ms))
print('METRICAS julio:')
for k in ('total_compras', 'total_notas_credito', 'devoluciones_nc', 'inversion_bruta',
          'inversion_total', 'proveedores_activos', 'unidades_esperadas',
          'unidades_recepcionadas', 'cumplimiento_general', 'iva_soportado',
          'costo_ordenes_compra', 'valor_venta_esperado', 'roi_promedio',
          'costo_promedio_unidad', 'pendiente_pago', 'pct_facturas_pagadas'):
    print('  %s = %s' % (k, metricas.get(k)))
print('ESTADO_PAGOS:', {k: round(v) if isinstance(v, float) else v for k, v in estado_pagos.items()})
print('ESTADO_RECEP:', estado_recep)
print('MET_RECEP:', met_recep)
print('evolucion julio (mes 7):', [f for f in evolucion if f['mes'] == 7])
print('suma evolucion inversion:', round(sum(f['inversion'] for f in evolucion)))
print('n ranking:', len(ranking), '| suma inversion ranking:', round(sum(f['inversion'] for f in ranking)))
print('ranking top3:', [(f['nombre'][:22], round(f['inversion']), f['cumplimiento'],
                        f['cumplimiento_calculable'], f['pct_facturas_pagadas'], f['markup'])
                       for f in ranking[:3]])
nc_only = [f['nombre'][:25] for f in ranking if f['total_compras'] == 0]
print('proveedores NC-only en ranking:', nc_only)
print('vencimientos:', [(t['periodo'], round(t['monto'])) for t in vencimientos])
print('pagos_pendientes n=%s suma_saldo=%s' % (len(pend), round(sum(p['saldo'] for p in pend))))

print()
print('=== 2. ENDPOINT api_reporte_compras ACOTADO (periodo=semana, prov=1453) ===')
st, js, nq, ms, malas = invocar(vr.api_reporte_compras,
                                {'anio': '2026', 'periodo': 'semana', 'proveedor': '1453'})
print('status=%s queries=%s ms=%s escrituras=%s' % (st, nq, ms, malas))
if js and js.get('success'):
    m = js['metricas']
    print('filtros_aplicados:', js['filtros_aplicados'])
    print('semana adidas: total_compras=%s inversion=%s uds=%s cumplimiento=%s' % (
        m['total_compras'], round(m['inversion_total']), m['unidades_esperadas'], m['cumplimiento_general']))
elif js:
    print('ERROR:', js.get('error'))

print()
print('=== 3. api_reporte_rendimiento_proveedor JULIO 2026 ===')
st, js, nq, ms, malas = invocar(vr.api_reporte_rendimiento_proveedor,
                                {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'})
print('status=%s queries=%s ms=%s escrituras=%s' % (st, nq, ms, malas))
if js and js.get('success'):
    k = js['kpis']
    for kk in ('total_proveedores', 'total_comprados', 'total_recepcionados', 'total_vendidos',
               'total_inversion', 'total_venta', 'total_margen', 'unidades_venta_sin_atribuir',
               'venta_sin_atribuir', 'cobertura_atribucion_pct', 'articulos_multiproveedor',
               'unidades_venta_por_nombre_oc', 'total_monto_no_inventariable'):
        print('  %s = %s' % (kk, k.get(kk)))
    ceros = [p['proveedor_nombre'][:22] for p in js['proveedores']
             if p['pares_recepcionados'] and not p['pares_vendidos']]
    print('  proveedores con recepcion>0 y venta=0:', len(ceros), ceros[:6])
    for p in js['proveedores'][:5]:
        print('  fila: %s comp=%s recep=%s vend=%s venta=%s margen=%s' % (
            p['proveedor_nombre'][:22], p['pares_comprados'], p['pares_recepcionados'],
            p['pares_vendidos'], round(p['venta_total']), p['margen']))
elif js:
    print('ERROR:', js.get('error'))

print()
print('=== 4. api_reporte_recepciones_detallado JULIO ===')
st, js, nq, ms, malas = invocar(vr.api_reporte_recepciones_detallado,
                                {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'})
print('status=%s queries=%s ms=%s escrituras=%s' % (st, nq, ms, malas))
if js and js.get('success'):
    print('  resumen:', js['resumen'])
    print('  n por_sucursal=%s n por_proveedor=%s n cambios_precio=%s' % (
        len(js['por_sucursal']), len(js['por_proveedor']), len(js['cambios_precio'])))
    print('  top sucursales:', [(s['sucursal'], s['unidades']) for s in js['por_sucursal'][:5]])
elif js:
    print('ERROR:', js.get('error'))

print()
print('=== 5. api_reporte_despachos_detallado JULIO ===')
st, js, nq, ms, malas = invocar(vr.api_reporte_despachos_detallado,
                                {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'})
print('status=%s queries=%s ms=%s escrituras=%s' % (st, nq, ms, malas))
if js and js.get('success'):
    print('  resumen:', js['resumen'])
    print('  n despachos:', len(js['despachos']))
elif js:
    print('ERROR:', js.get('error'))
print('FIN T2')
