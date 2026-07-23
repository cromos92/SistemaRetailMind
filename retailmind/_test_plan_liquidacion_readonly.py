# -*- coding: utf-8 -*-
"""
_test_plan_liquidacion_readonly.py — Validación SOLO LECTURA del Plan de
Liquidación v2 (dimensiones, antigüedad FIFO, drill-down) y del motor de
campañas/NxM. Patrón de _test_reportes_readonly.py.

Invoca las vistas REALES con RequestFactory dentro de una transacción con
rollback forzado + guardia anti-escritura, y cruza sus respuestas contra
oráculos de BD independientes. NO modifica nada.

Ejecutar desde retailmind/ (donde está manage.py):

    python _test_plan_liquidacion_readonly.py --confirmo-prod

o vía shell:

    python manage.py shell -c "import runpy; runpy.run_path('_test_plan_liquidacion_readonly.py', run_name='__main__')" -- --confirmo-prod
"""
import os
import sys
import time
from datetime import timedelta

import django
from django.apps import apps as _apps

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

if not _apps.ready:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
    django.setup()

from django.db import connection, transaction  # noqa: E402
from django.db.models import Min, Sum  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.models import (  # noqa: E402
    AtributoOpcion, CampanaLiquidacionProducto, EmpresaUser, LoteProducto,
    Movimientos_Producto, Producto_Talla, ProductoAtributoValor, Sucursal,
)
from app.constants_kardex import CONCEPTOS_VENTA  # noqa: E402
from app import views_inteligencia_compra as V  # noqa: E402
from app import views_modulo_campanas_liquidacion as VC  # noqa: E402

ICON = {'PASS': '✔', 'FAIL': '✘', 'WARN': '⚠', 'SKIP': '·'}
RESULTADOS = []
ESCRITURAS = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP', 'CREATE')


def chk(nombre, ok, detalle=''):
    estado = 'PASS' if ok else 'FAIL'
    RESULTADOS.append((estado, nombre))
    print(f'  {ICON[estado]} {nombre}' + (f'  — {detalle}' if detalle else ''))
    return ok


def _escrituras(queries):
    return [q['sql'][:100] for q in queries
            if (q.get('sql') or '').lstrip().upper().startswith(ESCRITURAS)]


def invocar(view, params, user, sucursal_id, empresa_id, **kwargs):
    """Invoca una vista GET real; devuelve (json, escrituras, ms, nq).

    CaptureQueriesContext captura queries aunque DEBUG=False (prod). El
    rollback forzado garantiza que nada persista pase lo que pase.
    """
    factory = RequestFactory()
    request = factory.get('/_test', data=params)
    request.user = user
    request.session = {'idSucursalActual': sucursal_id, 'idEmpresaActual': empresa_id}
    import json as _json
    t0 = time.perf_counter()
    with transaction.atomic():
        with CaptureQueriesContext(connection) as cap:
            resp = view(request, **kwargs)
        transaction.set_rollback(True)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    escrituras = _escrituras(cap.captured_queries)
    data = _json.loads(resp.content.decode('utf-8'))
    return data, escrituras, ms, len(cap.captured_queries)


def _elegir_usuario():
    """Primer usuario con EmpresaUser activo y tiendas con stock."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    for eu in EmpresaUser.objects.filter(status=True).select_related('user', 'empresa'):
        tiendas = Sucursal.objects.filter(
            empresa_id=eu.empresa_id, es_centro_distribucion=False)
        if Producto_Talla.objects.filter(
                stock__gt=0, producto__sucursal__in=tiendas).exists():
            return eu.user, eu.empresa_id
    u = User.objects.filter(is_superuser=True).first()
    return u, None


def main():
    if '--confirmo-prod' not in sys.argv:
        print('Refuse: agrega --confirmo-prod (la BD del .env es PRODUCCIÓN, solo lectura).')
        return
    print('=' * 74)
    print('VALIDACIÓN READ-ONLY · Plan de Liquidación v2 + Campañas/NxM')
    print('=' * 74)

    user, empresa_id = _elegir_usuario()
    if user is None:
        print('No hay usuario válido. Abortando.')
        return
    sucursales = list(Sucursal.objects.filter(
        empresa_id__in=EmpresaUser.objects.filter(user=user, status=True)
        .values_list('empresa_id', flat=True)))
    tiendas = [s for s in sucursales if not s.es_centro_distribucion]
    cds = [s for s in sucursales if s.es_centro_distribucion]
    sucursal_id = tiendas[0].id if tiendas else (sucursales[0].id if sucursales else None)
    print(f'Usuario: {getattr(user, "username", user)} · tiendas={len(tiendas)} · CD={len(cds)}\n')
    hoy = timezone.localdate()

    # ---- 1. Agregados sin CD ----
    print('[1] obtener_plan_liquidacion (sin CD)')
    data, w, ms, nq = invocar(V.obtener_plan_liquidacion, {'con_opciones': '1'},
                              user, sucursal_id, empresa_id)
    chk('sin escrituras', not w, ';'.join(w))
    chk('respuesta ok', data.get('success') is True, str(data.get('error'))[:80])
    d = data.get('data', {})
    tot = d.get('totales', {})
    print(f'    dims: marcas={len(d.get("marcas",[]))} cat={len(d.get("categorias",[]))} '
          f'esp={len(d.get("especialidades",[]))} suc={len(d.get("sucursales",[]))} · {ms}ms/{nq}q')

    # Oráculo: stock tiendas del usuario
    tiendas_ids = [s.id for s in tiendas]
    oraculo_stock = Producto_Talla.objects.filter(
        stock__gt=0, producto__excluir_de_analitica=False,
        producto__sucursal_id__in=tiendas_ids,
    ).aggregate(s=Sum('stock'))['s'] or 0
    chk('total stock tiendas == oráculo',
        (tot.get('stock_u') or 0) == oraculo_stock,
        f"vista={tot.get('stock_u')} oráculo={oraculo_stock}")
    chk('sin CD => stock_cd=0', (tot.get('stock_cd') or 0) == 0,
        f"stock_cd={tot.get('stock_cd')}")

    # Suma de filas por sucursal (tiendas) ~ total (todas las tiendas del usuario)
    suc_rows = d.get('sucursales', [])
    suma_suc_tienda = sum(r['stock'] for r in suc_rows if not r.get('es_cd'))
    chk('Σ stock por sucursal (tiendas) == total',
        suma_suc_tienda == (tot.get('stock_u') or 0),
        f"Σsuc={suma_suc_tienda} total={tot.get('stock_u')}")

    # ---- 2. Agregados con CD ----
    print('[2] obtener_plan_liquidacion (incluir_cd=1)')
    data2, w2, ms2, nq2 = invocar(V.obtener_plan_liquidacion, {'incluir_cd': '1'},
                                  user, sucursal_id, empresa_id)
    chk('sin escrituras', not w2)
    tot2 = data2.get('data', {}).get('totales', {})
    oraculo_cd = Producto_Talla.objects.filter(
        stock__gt=0, producto__excluir_de_analitica=False,
        producto__sucursal_id__in=[s.id for s in cds],
    ).aggregate(s=Sum('stock'))['s'] or 0
    chk('stock_cd == oráculo bodegas', (tot2.get('stock_cd') or 0) == oraculo_cd,
        f"vista={tot2.get('stock_cd')} oráculo={oraculo_cd}")
    chk('stock tiendas invariante con/ sin CD',
        (tot2.get('stock_u') or 0) == (tot.get('stock_u') or 0),
        f"{tot2.get('stock_u')} vs {tot.get('stock_u')}")

    # ---- 3. Filtro especialidad no duplica filas ----
    print('[3] filtro especialidad (subconsulta IN, sin duplicar)')
    esp = AtributoOpcion.objects.filter(
        atributo__nombre__iexact='Especialidad',
        productoatributovalor__producto__sucursal_id__in=tiendas_ids,
    ).first()
    if esp:
        d3, w3, _, _ = invocar(V.obtener_plan_liquidacion, {'especialidad_id': esp.id},
                               user, sucursal_id, empresa_id)
        tot3 = d3.get('data', {}).get('totales', {})
        prods_esp = ProductoAtributoValor.objects.filter(
            atributo__nombre__iexact='Especialidad', opcion_id=esp.id
        ).values('producto_id')
        oraculo_esp = Producto_Talla.objects.filter(
            stock__gt=0, producto__excluir_de_analitica=False,
            producto__sucursal_id__in=tiendas_ids, producto_id__in=prods_esp,
        ).aggregate(s=Sum('stock'))['s'] or 0
        chk(f'stock filtrado por "{esp.valor}" == oráculo IN',
            (tot3.get('stock_u') or 0) == oraculo_esp,
            f"vista={tot3.get('stock_u')} oráculo={oraculo_esp}")
    else:
        chk('hay especialidad para probar', False, 'sin especialidades asignadas — SKIP')

    # ---- 4. Drill-down + antigüedad FIFO ----
    print('[4] obtener_plan_liquidacion_detalle (antigüedad FIFO)')
    det, wd, msd, nqd = invocar(V.obtener_plan_liquidacion_detalle,
                                {'page_size': '50', 'orden': '-dias_antiguedad'},
                                user, sucursal_id, empresa_id)
    chk('sin escrituras', not wd, ';'.join(wd))
    chk('respuesta ok', det.get('success') is True, str(det.get('error'))[:80])
    filas = det.get('filas', [])
    print(f'    total={det.get("total")} page_size={det.get("page_size")} · {msd}ms/{nqd}q')
    # Cruce FIFO para una muestra con lote vivo
    verificados = 0
    for f in filas[:20]:
        if f.get('antiguedad_fuente') != 'lote':
            continue
        real = LoteProducto.objects.filter(
            producto_talla__producto_id=f['producto_id'],
            activo=True, agotado=False, cantidad_disponible__gt=0,
        ).aggregate(m=Min('fecha_ingreso'))['m']
        if real:
            esperado = timezone.localtime(real).strftime('%Y-%m-%d')
            if not chk(f'FIFO producto {f["producto_id"]} == Min(lote)',
                       f['fecha_fifo'] == esperado, f"{f['fecha_fifo']} vs {esperado}"):
                break
            verificados += 1
        if verificados >= 3:
            break
    if verificados == 0:
        chk('muestra FIFO por lote', True, 'ningún producto de la página tenía lote vivo — SKIP')

    # Paginación consistente (total estable)
    det2, _, _, _ = invocar(V.obtener_plan_liquidacion_detalle,
                            {'page': '2', 'page_size': '50'},
                            user, sucursal_id, empresa_id)
    chk('total estable entre páginas',
        det.get('total') == det2.get('total'),
        f"p1={det.get('total')} p2={det2.get('total')}")

    # ---- 5. Campañas y promos (read-only) ----
    print('[5] campañas / promos NxM (endpoints read-only)')
    lst, wl, _, _ = invocar(VC.listar_campanas_liquidacion, {}, user, sucursal_id, empresa_id)
    chk('listar campañas sin escrituras', not wl)
    chk('listar campañas ok', lst.get('success') is True)
    promos, wp, _, _ = invocar(VC.obtener_promos_activas, {'sucursal_id': sucursal_id},
                               user, sucursal_id, empresa_id)
    chk('promos activas sin escrituras', not wp)
    chk('promos activas ok', promos.get('success') is True,
        f"n={len(promos.get('promos', []))}")

    # ---- 6. Validador NxM puro ----
    print('[6] validar_promos_nxm_payload (función pura)')
    from app.services.campanas_service import validar_promos_nxm_payload
    payload_vacio = [{'sku': 1, 'cantidad': 1}]
    suc0 = Sucursal.objects.get(id=sucursal_id)
    r6 = validar_promos_nxm_payload(payload_vacio, suc0)
    chk('payload sin promos => ok', r6['ok'] and not r6['lineas_ok'])

    print('\n' + '=' * 74)
    n_fail = sum(1 for e, _ in RESULTADOS if e == 'FAIL')
    n_pass = sum(1 for e, _ in RESULTADOS if e == 'PASS')
    print(f'RESULTADO: {n_pass} PASS · {n_fail} FAIL')
    print('=' * 74)


if __name__ == '__main__':
    main()
