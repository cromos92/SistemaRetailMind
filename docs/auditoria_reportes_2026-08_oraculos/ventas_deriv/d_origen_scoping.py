# -*- coding: utf-8 -*-
# Script D: productos-origen + scoping restringido + boletas cabecera 0
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, r'C:\Users\cromo\AppData\Local\Temp\claude\c--Users-cromo-Documents-DjangoProyects-SistemaRetailMind\15dd0fbe-a29a-4c80-8ad2-c70c318e71b7\scratchpad\ventas_deriv')
from _boot import invocar, ADMIN, User  # noqa: E402

from django.db import connection  # noqa: E402
from django.db.models import Count, Q, Sum  # noqa: E402
from app.models import (Dte, Dte_Detalle_Pago, EmpresaUser, Movimientos_Producto,
                        Sucursal, Ticket)  # noqa: E402
from app.utils_analitica import productos_activos_qs  # noqa: E402
from app.utils_permisos import usuario_puede_ver_todas_sucursales  # noqa: E402

# ---------- 1. productos-origen 2026 (admin) ----------
r = invocar('app.views_modulo_reportes.api_productos_por_origen', {'anio': 2026})
js = r.get('json') or {}
print('API: status', r['status'], 'ms', r['ms'], 'nq', r['nq'],
      'escrituras', len(r['escrituras']))
print('resumen:', js.get('resumen'))
for o in (js.get('por_origen') or []):
    print('  ', o['concepto'], '->', o['origen'], '| skus', o['skus'],
          'uds', o['unidades'], 'costo', o['costo'], 'pct', o['pct'],
          'alerta', o['alerta'])
print('por_mes:', [(m['mes'], m['skus']) for m in (js.get('por_mes') or [])])

# ---------- 2. oraculo raw SQL DISTINCT ON ----------
tabla = Movimientos_Producto._meta.db_table
print('tabla kardex:', tabla)
sql = f'''
SELECT DISTINCT ON ("ProductoTalla_id")
       "ProductoTalla_id", concepto, cantidad, costo, fecha, referencia_externa
FROM "{tabla}"
WHERE tipo_movimiento = 'INGRESO' AND estado = 'COMPLETADO'
ORDER BY "ProductoTalla_id", fecha, hora, id
'''
with connection.cursor() as cur:
    cur.execute(sql)
    primeros = cur.fetchall()
print('PTs con algun INGRESO:', len(primeros))
scope = set(productos_activos_qs().values_list('id', flat=True))
print('PTs en scope analitica:', len(scope))
por_con = defaultdict(lambda: [0, 0, 0.0])   # skus, uds, costo
por_mes = defaultdict(int)
mig = {'skus': 0, 'uds': 0, 'costo': 0.0}
tot = [0, 0, 0.0]
for fila in primeros:
    pt, concepto, cant, costo, fecha, ref = fila
    if pt not in scope or not fecha or fecha.year != 2026:
        continue
    uds = abs(int(cant or 0))
    c = float(costo or 0) * uds
    key = concepto or 'OTRO'
    por_con[key][0] += 1
    por_con[key][1] += uds
    por_con[key][2] += c
    por_mes[fecha.month] += 1
    tot[0] += 1
    tot[1] += uds
    tot[2] += c
    if ref == 'MIGRACION_LARAVEL':
        mig['skus'] += 1
        mig['uds'] += uds
        mig['costo'] += c
print('ORACULO 2026: skus=', tot[0], 'uds=', tot[1], 'costo=', round(tot[2]))
for k in sorted(por_con, key=lambda x: -por_con[x][0]):
    v = por_con[k]
    print('  ', k, '| skus', v[0], 'uds', v[1], 'costo', round(v[2]))
print('APERTURA MIGRACION_LARAVEL dentro de las altas 2026:', mig)
print('por_mes oraculo:', dict(sorted(por_mes.items())))
vista_res = js.get('resumen') or {}
print('DELTA skus vista-oraculo:', (vista_res.get('total_skus') or 0) - tot[0],
      '| uds:', (vista_res.get('total_unidades') or 0) - tot[1])

# INGRESO_INICIAL 2026: cuanto es apertura vs real
ini = [f for f in primeros
       if f[1] == 'INGRESO_INICIAL' and f[0] in scope and f[4] and f[4].year == 2026]
ini_mig = [f for f in ini if f[5] == 'MIGRACION_LARAVEL']
print('INGRESO_INICIAL 2026: total', len(ini), '| con ref MIGRACION_LARAVEL', len(ini_mig))
fechas_ini = defaultdict(int)
for f in ini:
    fechas_ini[str(f[4])[:7]] += 1
print('INGRESO_INICIAL por mes:', dict(sorted(fechas_ini.items())))

# ---------- 3. scoping restringido ----------
print('\n=== SCOPING RESTRINGIDO ===')
restr = None
for u in User.objects.filter(is_active=True, is_superuser=False).exclude(rol='administrador')[:80]:
    eus = list(EmpresaUser.objects.filter(user=u, status=True)
               .values_list('empresa_id', 'sucursal_id'))
    if not eus:
        continue
    empresas = {e for e, _ in eus}
    try:
        if usuario_puede_ver_todas_sucursales(u):
            continue
    except Exception:
        continue
    suc_id = next((s for _, s in eus if s), None)
    if suc_id:
        restr = (u, suc_id, next(iter(empresas)), empresas)
        break
if restr:
    u, suc_id, emp_id, empresas = restr
    print('restringido:', u.username, 'suc', suc_id, 'empresas', sorted(empresas))
    suc_ajena = Sucursal.objects.exclude(empresa_id__in=empresas).values_list('id', flat=True).first()
    # productos-origen con sucursal ajena -> debe 403
    rr = invocar('app.views_modulo_reportes.api_productos_por_origen',
                 {'anio': 2026, 'sucursal': suc_ajena}, suc=suc_id, emp=emp_id, user=u)
    print('origen suc ajena: status', rr['status'],
          '(esperado 403) json_success=', (rr.get('json') or {}).get('success'))
    # productos-origen sin filtro -> solo sus empresas
    rr2 = invocar('app.views_modulo_reportes.api_productos_por_origen',
                  {'anio': 2026}, suc=suc_id, emp=emp_id, user=u)
    tot_r = ((rr2.get('json') or {}).get('resumen') or {}).get('total_skus')
    print('origen restringido total_skus:', tot_r, 'vs admin:', vista_res.get('total_skus'))
    # ventas-internet restringido
    rv = invocar('app.views_modulo_reportes.obtener_reporte_ventas_internet',
                 {'fecha_inicio': '2026-07-01', 'fecha_fin': '2026-07-31'},
                 suc=suc_id, emp=emp_id, user=u)
    resv = ((rv.get('json') or {}).get('resumen') or {})
    sucs_vistas = {s['id'] for e in ((rv.get('json') or {}).get('empresas') or [])
                   for s in e.get('sucursales', [])}
    sucs_propias = set(Sucursal.objects.filter(empresa_id__in=empresas).values_list('id', flat=True))
    print('internet restringido: status', rv['status'], 'venta=', resv.get('venta_internet'),
          'sucursales fuera de alcance:', sorted(sucs_vistas - sucs_propias))
    # documentos-emitidos 'all' restringido
    rd = invocar('app.views_modulo_reportes.obtener_documentos_emitidos',
                 {'fecha_desde': '2026-07-01', 'fecha_hasta': '2026-07-31',
                  'sucursal_id': 'all'}, suc=suc_id, emp=emp_id, user=u)
    diag = ((rd.get('json') or {}).get('diagnostico') or {})
    sucs_diag = {s['id'] for s in diag.get('sucursales_incluidas', [])}
    print('docs-emitidos restringido: status', rd['status'],
          'sucursales incluidas:', sorted(sucs_diag),
          'fuera de alcance:', sorted(sucs_diag - sucs_propias))
    # productos-vendidos restringido (sin filtro -> sucursal de sesion)
    rp = invocar('app.views_modulo_reportes.obtener_productos_vendidos',
                 {'tipo_flujo': 'custom', 'fecha_inicio': '2026-07-01',
                  'fecha_fin': '2026-07-31'}, suc=suc_id, emp=emp_id, user=u)
    kp = ((rp.get('json') or {}).get('kpis') or {})
    print('prod-vendidos restringido: status', rp['status'],
          'monto=', kp.get('total_monto'), '(admin global=155129379)',
          'alcance stock=', kp.get('stock_alcance'))
else:
    print('NO se encontro usuario restringido utilizable')

# ---------- 4. boletas con cabecera 0 (hallazgo B) ----------
print('\n=== BOLETAS cabecera<lineas (julio) ===')
ids = [2193698, 2193332, 2190984, 2191887, 2193336, 2192013, 2191730, 2191857]
for d in Dte.objects.filter(id__in=ids).values(
        'id', 'numero_documento', 'tipo_documento', 'monto_neto', 'monto_con_iva',
        'descuento', 'sucursal_id', 'fecha_emision', 'estado_dte'):
    pagos = list(Dte_Detalle_Pago.objects.filter(dte_id=d['id'])
                 .values_list('metodo_pago', 'monto'))
    tk = Ticket.objects.filter(folio_dte=d['numero_documento'],
                               sucursal_id=d['sucursal_id']).values(
        'id', 'total', 'metodo_pago', 'descuento').first()
    print('  dte', d['id'], 'folio', d['numero_documento'], d['tipo_documento'],
          'neto', d['monto_neto'], 'bruto', d['monto_con_iva'], 'dcto', d['descuento'],
          d['estado_dte'], '| pagos', pagos, '| ticket', tk)
cero = Dte.objects.filter(
    fecha_emision__gte=date(2026, 7, 1), fecha_emision__lte=date(2026, 7, 31),
    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'], monto_con_iva=0,
    descartado=False).exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])
print('DTEs julio con monto_con_iva=0 en universo ventas:', cero.count())
print('FIN D')
