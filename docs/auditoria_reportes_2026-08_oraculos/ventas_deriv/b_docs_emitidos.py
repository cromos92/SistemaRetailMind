# -*- coding: utf-8 -*-
# Script B: documentos-emitidos JSON vs Excel vs oraculo (julio 2026)
import sys
from datetime import date
from decimal import Decimal
from io import BytesIO

sys.path.insert(0, r'C:\Users\cromo\AppData\Local\Temp\claude\c--Users-cromo-Documents-DjangoProyects-SistemaRetailMind\15dd0fbe-a29a-4c80-8ad2-c70c318e71b7\scratchpad\ventas_deriv')
from _boot import invocar, ADMIN  # noqa: E402

from django.db.models import Count, F, Sum  # noqa: E402
from app.models import Dte  # noqa: E402
from app.views_modulo_reportes import TIPOS_TRANSACCION_NC_VENTA  # noqa: E402

FI, FF = '2026-07-01', '2026-07-31'
FID, FFD = date(2026, 7, 1), date(2026, 7, 31)

# ---------- JSON pagina 1 y 2 ----------
p1 = invocar('app.views_modulo_reportes.obtener_documentos_emitidos',
             {'fecha_desde': FI, 'fecha_hasta': FF, 'sucursal_id': 'all',
              'page': 1, 'per_page': 100})
p2 = invocar('app.views_modulo_reportes.obtener_documentos_emitidos',
             {'fecha_desde': FI, 'fecha_hasta': FF, 'sucursal_id': 'all',
              'page': 2, 'per_page': 100})
j1, j2 = p1.get('json') or {}, p2.get('json') or {}
r1, r2 = j1.get('resumen') or {}, j2.get('resumen') or {}
print('p1: status', p1['status'], 'ms', p1['ms'], 'nq', p1['nq'],
      'escrituras', len(p1['escrituras']))
print('p2: status', p2['status'], 'ms', p2['ms'], 'nq', p2['nq'])
print('pagination p1:', j1.get('pagination'))
print('total_real p1/p2:', j1.get('total_real'), j2.get('total_real'))
print('resumen p1:', {x: r1.get(x) for x in ('ventas_brutas', 'notas_credito',
      'total_global', 'descuentos', 'saldo_no_pagado', 'efectivo', 'tbk_credito',
      'tbk_debito', 'transferencia', 'otros')})
iguales = all(r1.get(x) == r2.get(x) for x in r1)
print('resumen igual entre paginas:', iguales)
d1 = j1.get('diagnostico') or {}
print('diagnostico: dtes=', d1.get('cantidad_dtes'), 'nc=', d1.get('cantidad_notas_credito'),
      'sin_pagos=', d1.get('dtes_sin_pagos_registrados'),
      'sucursales=', d1.get('cantidad_sucursales_incluidas'))

# NC en el listado JSON: signo del total
docs1 = j1.get('documentos') or []
ncs_lista = [d for d in docs1 if d.get('es_nota_credito')]
print('NC en pagina 1:', len(ncs_lista),
      'totales positivos:', sum(1 for d in ncs_lista if d['total'] > 0),
      'ejemplo:', ncs_lista[0] if ncs_lista else None)

# paginas disjuntas
ids1 = {d['id'] for d in docs1}
ids2 = {d['id'] for d in (j2.get('documentos') or [])}
print('overlap p1/p2:', len(ids1 & ids2))

# ---------- ORACULO ----------
qs_v = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    fecha_emision__gte=FID, fecha_emision__lte=FFD, descartado=False,
).exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])
qs_nc = Dte.objects.filter(
    fecha_emision__gte=FID, fecha_emision__lte=FFD,
    tipo_documento='NOTA DE CREDITO',
    tipo_transaccion__in=TIPOS_TRANSACCION_NC_VENTA,
    descartado=False, estado_dte__in=['EMITIDO', 'ACEPTADO'])
av = qs_v.aggregate(m=Sum('monto_con_iva'), n=Count('id'))
an = qs_nc.aggregate(m=Sum('monto_con_iva'), n=Count('id'))
print(f'ORACULO: ventas={int(av["m"] or 0)} ({av["n"]}) NC={int(an["m"] or 0)} ({an["n"]}) '
      f'neto={int(av["m"] or 0) - int(an["m"] or 0)} | total_docs={av["n"] + an["n"]}')
print('DELTA vista-oraculo: brutas=', int(r1.get('ventas_brutas') or 0) - int(av['m'] or 0),
      'nc=', int(r1.get('notas_credito') or 0) - int(an['m'] or 0),
      'docs=', (j1.get('total_real') or 0) - (av['n'] + an['n']))

# internas receptor==emisor dentro del universo
internas = qs_v.filter(receptor__isnull=False, receptor_id=F('emisor_id')) \
    .aggregate(m=Sum('monto_con_iva'), n=Count('id'))
print('facturas internas receptor==emisor incluidas:', internas['n'],
      'monto', int(internas['m'] or 0))
# ANULADOS excluidos aqui pero incluidos en ventas-sucursal
anulados = Dte.objects.filter(
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
    fecha_emision__gte=FID, fecha_emision__lte=FFD, descartado=False,
    estado_dte='ANULADO').aggregate(m=Sum('monto_con_iva'), n=Count('id'))
print('DTEs ANULADO julio (fuera de este reporte, dentro de ventas-sucursal):',
      anulados['n'], int(anulados['m'] or 0))

# ---------- EXCEL ----------
ex = invocar('app.views_modulo_reportes.exportar_documentos_emitidos_excel',
             {'fecha_desde': FI, 'fecha_hasta': FF, 'sucursal_id': 'all'})
print('excel: status', ex['status'], 'ms', ex['ms'], 'nq', ex['nq'])
resp = ex.get('resp')
if resp is not None and ex['status'] == 200 and 'spreadsheet' in resp.get('Content-Type', ''):
    import openpyxl
    wb = openpyxl.load_workbook(BytesIO(resp.content), read_only=True)
    ws = wb.active
    filas = 0
    suma_total = 0
    ncs_excel = 0
    ncs_neg = 0
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
        filas += 1
        tipo = str(row[1] or '')
        tot = int(row[5] or 0)
        suma_total += tot
        if 'NOTA' in tipo.upper():
            ncs_excel += 1
            if tot < 0:
                ncs_neg += 1
    print(f'excel filas={filas} suma_Total={suma_total} NC={ncs_excel} NC_negativas={ncs_neg}')
    print('CHECK excel filas == total_real:', filas == j1.get('total_real'))
    print('CHECK excel suma == total_global:', suma_total == r1.get('total_global'),
          '| delta =', suma_total - int(r1.get('total_global') or 0))
else:
    print('excel NO xlsx:', getattr(resp, 'content', b'')[:200])

# ---------- causa del delta lineas-vs-cabecera (boletas, hallazgo A2) ----------
print('--- causa delta boletas (lineas > cabecera) ---')
from django.db.models import Case, DecimalField, ExpressionWrapper, When  # noqa: E402
from app.models import Dte_Productos  # noqa: E402
monto_linea = Case(
    When(monto_item__gt=0, then=ExpressionWrapper(F('monto_item'), output_field=DecimalField())),
    default=ExpressionWrapper(F('precio') * F('stock'), output_field=DecimalField()),
    output_field=DecimalField(),
)
base_dp = Dte_Productos.objects.filter(
    dte__fecha_emision__gte=FID, dte__fecha_emision__lte=FFD,
    dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
).exclude(dte__estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']) \
 .exclude(dte__tipo_documento='NOTA DE CREDITO') \
 .exclude(dte__receptor__isnull=False, dte__receptor_id=F('dte__emisor_id'))
agg = (base_dp.values('dte_id', cab=F('dte__monto_con_iva'), dcto=F('dte__descuento'),
                      tipo=F('dte__tipo_documento'))
       .annotate(lin=Sum(monto_linea)))
peores = []
delta_igual_dcto = delta_total = n_desvio = 0
for x in agg:
    lin, cab = int(x['lin'] or 0), int(x['cab'] or 0)
    delta = lin - cab
    if abs(delta) > max(2, cab * 0.005):
        n_desvio += 1
        delta_total += delta
        if abs(delta - int(x['dcto'] or 0)) <= 2:
            delta_igual_dcto += 1
        peores.append((x['dte_id'], x['tipo'], lin, cab, int(x['dcto'] or 0), delta))
peores.sort(key=lambda t: -abs(t[5]))
print(f'docs con lineas!=cabecera(>0.5%): {n_desvio}, delta_total={delta_total}, '
      f'de los cuales delta==descuento_dte: {delta_igual_dcto}')
for t in peores[:10]:
    print('   dte', t)
print('FIN B')
