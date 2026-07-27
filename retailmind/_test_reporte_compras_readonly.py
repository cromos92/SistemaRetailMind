# -*- coding: utf-8 -*-
"""
_test_reporte_compras_readonly.py — verificación SOLO LECTURA del Reporte de
Compras Integral (app.views_modulo_reportes.api_reporte_compras).

Invoca la vista real con RequestFactory (patrón _test_reportes_readonly.py),
mide queries/tiempo y cruza las cifras contra oráculos de BD independientes:
scoping por empresa, exclusión de descartados/anulados, NC en negativo,
saldo real de deuda y cumplimiento de entregas.

Ejecutar desde retailmind/:

    python _test_reporte_compras_readonly.py --anio 2026
    python _test_reportes_readonly.py ...  (suite general, no la reemplaza)

NO escribe nada: solo GET + agregaciones, envuelto en transacción con rollback.
"""
import argparse
import json
import os
import sys
import time

import django

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db import connection, reset_queries, transaction  # noqa: E402
from django.db.models import Count, DecimalField, Q, Sum, Value  # noqa: E402
from django.db.models.functions import Coalesce  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.models import (  # noqa: E402
    Compras_Producto_Talla, Dte, EmpresaUser, Productos_Recepcionados, Sucursal,
)

ESCRITURAS = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP', 'CREATE')
NC = 'NOTA DE CREDITO'
ESTADOS_MUERTOS = ['ANULADO', 'CANCELADO', 'RECHAZADO']


def escrituras(queries):
    malas = []
    for q in queries:
        sql = (q.get('sql') or '').lstrip().upper()
        if sql.startswith(ESCRITURAS):
            malas.append(q['sql'][:120])
    return malas


def invocar(params, usuario, sucursal_id, empresa_id):
    from app.views_modulo_reportes import api_reporte_compras
    factory = RequestFactory()
    request = factory.get('/app/api/reporte-compras/', data=params)
    request.user = usuario
    request.session = {'idSucursalActual': sucursal_id, 'idEmpresaActual': empresa_id}
    reset_queries()
    t0 = time.perf_counter()
    with transaction.atomic():
        resp = api_reporte_compras(request)
        transaction.set_rollback(True)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    nq = len(connection.queries)
    mal = escrituras(connection.queries)
    try:
        data = json.loads(resp.content)
    except Exception:
        data = None
    return {'ms': ms, 'queries': nq, 'escrituras': mal, 'status': resp.status_code, 'json': data}


def oraculo(anio, empresas_ids):
    """Cifras esperadas calculadas aparte de la vista."""
    base = (Dte.objects
            .filter(tipo_transaccion='COMPRA', fecha_emision__year=anio,
                    receptor_id__in=empresas_ids)
            .exclude(descartado=True)
            .exclude(estado_dte__in=ESTADOS_MUERTOS))
    docs = base.exclude(tipo_documento=NC)
    ncs = base.filter(tipo_documento=NC)
    inv_docs = float(docs.aggregate(t=Sum('monto_neto'))['t'] or 0)
    inv_nc = float(ncs.aggregate(t=Sum('monto_neto'))['t'] or 0)
    con_iva = base.annotate(
        pagado=Coalesce(Sum('dte_asociado__monto'), Value(0),
                        output_field=DecimalField(max_digits=16, decimal_places=2))
    )
    saldo_pend = 0.0
    saldo_venc = 0.0
    hoy = timezone.localdate()
    for d in con_iva.exclude(tipo_documento=NC).values('monto_con_iva', 'pagado', 'fecha_vencimiento'):
        s = float(d['monto_con_iva']) - float(d['pagado'])
        if s > 0:
            saldo_pend += s
            if d['fecha_vencimiento'] and d['fecha_vencimiento'] < hoy:
                saldo_venc += s
    return {
        'total_docs': docs.count(),
        'total_nc': ncs.count(),
        'inversion_neta': inv_docs - inv_nc,
        'devoluciones_nc': inv_nc,
        'proveedores': docs.values('emisor_id').distinct().count(),
        'saldo_pendiente': saldo_pend,
        'saldo_vencido': saldo_venc,
        # universo sin scoping: si la vista lo iguala, hay fuga
        'total_docs_holding': (Dte.objects
                               .filter(tipo_transaccion='COMPRA', fecha_emision__year=anio)
                               .exclude(descartado=True)
                               .exclude(estado_dte__in=ESTADOS_MUERTOS)
                               .exclude(tipo_documento=NC).count()),
    }


def cumplimiento_oraculo(anio, empresas_ids):
    esperadas = (Compras_Producto_Talla.objects
                 .filter(compra_producto__compras__fecha__year=anio,
                         compra_producto__compras__empresa_id__isnull=False)
                 .exclude(compra_producto__compras__estado='ELIMINADA')
                 .values('compra_producto__compras__empresa_id')
                 .annotate(u=Sum('stock')))
    recibidas = (Productos_Recepcionados.objects
                 .filter(compra_producto_talla__compra_producto__compras__fecha__year=anio)
                 .exclude(compra_producto_talla__compra_producto__compras__estado='ELIMINADA')
                 .values('compra_producto_talla__compra_producto__compras__empresa_id')
                 .annotate(u=Sum('stockArribado')))
    esp = {r['compra_producto__compras__empresa_id']: r['u'] or 0 for r in esperadas}
    rec = {r['compra_producto_talla__compra_producto__compras__empresa_id']: r['u'] or 0 for r in recibidas}
    return esp, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--anio', type=int, default=timezone.localdate().year)
    ap.add_argument('--periodo', default='anual')
    ap.add_argument('--temporada', default='')
    args = ap.parse_args()

    from django.conf import settings
    settings.DEBUG = True  # habilita connection.queries (solo en memoria)

    User = get_user_model()
    admin = (User.objects.filter(rol='administrador', is_active=True).first()
             or User.objects.filter(is_superuser=True, is_active=True).first())
    eu = (EmpresaUser.objects.filter(status=True)
          .exclude(user__rol='administrador')
          .select_related('user', 'empresa').first())

    suc_edel = Sucursal.objects.filter(empresa__nombre__icontains='EDELMIRA TEBES').first()
    contextos = []
    if admin:
        contextos.append(('admin/EDEL', admin, suc_edel.id if suc_edel else None,
                          suc_edel.empresa_id if suc_edel else None))
    if eu:
        contextos.append((f'{eu.user.username}/{eu.empresa.nombre[:18]}', eu.user,
                          eu.sucursal_id, eu.empresa_id))

    print('=' * 78)
    print(f'REPORTE DE COMPRAS — año {args.anio} periodo={args.periodo} temporada={args.temporada or "-"}')
    print('=' * 78)

    for nombre, usuario, suc_id, emp_id in contextos:
        params = {'anio': args.anio, 'periodo': args.periodo, 'temporada': args.temporada}
        r = invocar(params, usuario, suc_id, emp_id)
        print(f'\n--- Contexto {nombre} (sucursal={suc_id}, empresa={emp_id})')
        print(f'    status={r["status"]}  queries={r["queries"]}  tiempo={r["ms"]} ms')
        if r['escrituras']:
            print('    !! ESCRITURAS DETECTADAS:', r['escrituras'][:3])
        data = r['json'] or {}
        if not data.get('success'):
            print('    ERROR:', str(data.get('error'))[:300])
            continue
        m = data['metricas']
        modo = data.get('filtros_aplicados', {}).get('modo')
        if modo == 'vendedora':
            # Universo distinto por diseño (traspasos recibidos, no compras):
            # aquí solo se verifica que nada del holding se cuele.
            from app.models import Dte
            emp = data['filtros_aplicados']['empresas_ids']
            fuera = (Dte.objects
                     .filter(tipo_transaccion='TRASPASO',
                             fecha_emision__year=args.anio)
                     .exclude(receptor_id__in=emp).count())
            print(f'    modo vendedora: {m["total_compras"]} traspasos recibidos; '
                  f'{fuera} traspasos de otras empresas quedaron fuera (deben quedar fuera)')
            print(f'    órdenes de compra del CD incluidas: '
                  f'{"NO (correcto)" if not data["cumplimiento_proveedores"] else "SI (FUGA)"}')
            print('    filtros aplicados:', data.get('filtros_aplicados'))
            continue
        # empresas realmente permitidas para este usuario
        if getattr(usuario, 'rol', '') == 'administrador':
            from app.utils_permisos import obtener_empresas_usuario
            empresas_ids = list(obtener_empresas_usuario(usuario).values_list('id', flat=True))
        else:
            empresas_ids = list(EmpresaUser.objects.filter(user=usuario, status=True)
                                .values_list('empresa_id', flat=True))
        if emp_id and emp_id in empresas_ids:
            empresas_ids = [emp_id]
        o = oraculo(args.anio, empresas_ids)
        print(f'    total_compras vista={m["total_compras"]}   oráculo={o["total_docs"]}   '
              f'(holding completo={o["total_docs_holding"]})')
        print(f'    inversion vista={m["inversion_total"]:,.0f}   oráculo(neta de NC)={o["inversion_neta"]:,.0f}')
        print(f'    devoluciones_nc vista={m.get("devoluciones_nc", "n/a")}   oráculo={o["devoluciones_nc"]:,.0f}')
        print(f'    proveedores vista={m["proveedores_activos"]}   oráculo={o["proveedores"]}')
        ep = data.get('estado_pagos', {})
        print(f'    deuda pendiente vista={ep.get("pendientes", 0):,.0f}  oráculo(saldo real)={o["saldo_pendiente"]:,.0f}')
        print(f'    deuda vencida  vista={ep.get("vencidos", 0):,.0f}  oráculo={o["saldo_vencido"]:,.0f}')
        cump = data.get('cumplimiento_proveedores', [])
        if cump:
            esp, rec = cumplimiento_oraculo(args.anio, empresas_ids)
            print('    cumplimiento (top 3, entregas):')
            for p in cump[:3]:
                pid = p.get('id')
                e, rr = esp.get(pid, 0), rec.get(pid, 0)
                real = round(rr / e * 100, 1) if e else None
                print(f'      {p.get("nombre", "?")[:32]:32} vista={p.get("cumplimiento")}%  '
                      f'oráculo={real}% ({rr}/{e})')
        suma_meses = sum(x['inversion'] for x in data.get('evolucion_mensual', []))
        suma_trim = sum(x['inversion'] for x in data.get('roi_temporadas', []))
        print(f'    coherencia: suma meses={suma_meses:,.0f}  suma trimestres={suma_trim:,.0f}  '
              f'(header={m["inversion_total"]:,.0f})')
        print(f'    unidades esperadas={m["unidades_esperadas"]:,} recibidas={m["unidades_recepcionadas"]:,} '
              f'cumplimiento={m["cumplimiento_general"]}%  markup={m["roi_promedio"]}%')
        print(f'    estado recepciones={data.get("estado_recepciones")}')
        print(f'    vencimientos={[(t["periodo"], round(t["monto"])) for t in data.get("vencimientos", [])]}')
        rp = data.get('recepciones_pendientes', [])
        if rp:
            print('    recepciones pendientes (3):',
                  [(x['numero'], x['esperadas'], x['recibidas'], x['estado']) for x in rp[:3]])
        print('    filtros aplicados:', data.get('filtros_aplicados'))

        # ---- Efecto real de los filtros Período y Temporada ----
        if args.periodo == 'anual' and not args.temporada:
            for periodo in ('trimestre', 'mes'):
                r2 = invocar({'anio': args.anio, 'periodo': periodo}, usuario, suc_id, emp_id)
                d2 = r2['json'] or {}
                if d2.get('success'):
                    f2 = d2['filtros_aplicados']
                    print(f'    período={periodo}: {f2["desde"]}..{f2["hasta"]} -> '
                          f'{d2["metricas"]["total_compras"]} docs, '
                          f'${d2["metricas"]["inversion_total"]:,.0f} ({r2["queries"]} queries)')
            for temporada in ('VERANO', 'INVIERNO'):
                r3 = invocar({'anio': args.anio, 'periodo': 'anual', 'temporada': temporada},
                             usuario, suc_id, emp_id)
                d3 = r3['json'] or {}
                if d3.get('success'):
                    print(f'    temporada={temporada}: aplicada='
                          f'{d3["filtros_aplicados"]["temporada_aplicada"]} -> '
                          f'{d3["metricas"]["total_compras"]} docs, '
                          f'${d3["metricas"]["inversion_total"]:,.0f}')

        # ---- Export Excel: mismo scoping, sin excepciones ----
        from app.views_modulo_reportes import exportar_reporte_compras_excel
        factory = RequestFactory()
        req = factory.get('/x', data={'anio': args.anio})
        req.user = usuario
        req.session = {'idSucursalActual': suc_id, 'idEmpresaActual': emp_id}
        with transaction.atomic():
            resp_x = exportar_reporte_compras_excel(req)
            transaction.set_rollback(True)
        print(f'    export excel: status={resp_x.status_code} '
              f'tipo={resp_x.get("Content-Type", "")[:60]}')


if __name__ == '__main__':
    main()
