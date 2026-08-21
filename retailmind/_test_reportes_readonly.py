# -*- coding: utf-8 -*-
"""
_test_reportes_readonly.py — Testeo integral del módulo de reportes (SOLO LECTURA).

Invoca las vistas REALES de reportes con RequestFactory (patrón benchmark_ventas)
y cruza sus respuestas contra oráculos de BD independientes (patrón
_auditoria_readonly). Mide además queries/tiempo y detecta fugas de scoping.

Ejecutar desde retailmind/ (donde está manage.py):

    python _test_reportes_readonly.py --confirmo-prod                # completo
    python _test_reportes_readonly.py --confirmo-prod --rapido       # sin N+1 pesados
    python _test_reportes_readonly.py --confirmo-prod --solo productos_vendidos
    python _test_reportes_readonly.py --confirmo-prod --desde 2026-06-01 --hasta 2026-06-30

NO modifica nada: solo GET + agregaciones. Guarda anti-escritura: si una vista
emitiera INSERT/UPDATE/DELETE se aborta ese reporte y se marca FAIL-CRITICO.
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import django

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.conf import settings  # noqa: E402
from django.db import connection, reset_queries, transaction  # noqa: E402
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.utils import timezone  # noqa: E402

from app.models import (  # noqa: E402
    AtributoOpcion,
    CambioDevolucionDetalle,
    Categoria,
    Dte,
    Dte_Productos,
    EmpresaUser,
    LoteProducto,
    Movimientos_Producto,
    Producto,
    Producto_Talla,
    ProductoAtributoValor,
    Sucursal,
    Ticket,
    Ticket_Productos,
)
from app.utils_permisos import usuario_puede_ver_todas_sucursales  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402

SEP = '=' * 78
VERDES = {'PASS': '✔', 'FAIL': '✘', 'WARN': '⚠', 'SKIP': '·'}


# ============================================================ INFRAESTRUCTURA

def _import_view(path):
    module_name, func_name = path.rsplit('.', 1)
    mod = __import__(module_name, fromlist=[func_name])
    return getattr(mod, func_name)


ESCRITURAS = ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP', 'CREATE')


def _solo_selects(queries):
    """Devuelve la lista de queries de ESCRITURA detectadas (vacía = ok)."""
    malas = []
    for q in queries:
        sql = (q.get('sql') or '').lstrip().upper()
        if sql.startswith(ESCRITURAS):
            malas.append(sql[:120])
    return malas


class Contexto:
    def __init__(self, nombre, usuario, sucursal_id, empresa_id):
        self.nombre = nombre
        self.usuario = usuario
        self.sucursal_id = sucursal_id
        self.empresa_id = empresa_id

    def __repr__(self):
        u = getattr(self.usuario, 'username', None)
        return f'<Ctx {self.nombre} user={u} suc={self.sucursal_id}>'


def invocar_vista(import_path, params, ctx):
    """Invoca una vista GET real y devuelve dict de resultado (nunca lanza)."""
    factory = RequestFactory()
    res = {
        'vista': import_path, 'contexto': ctx.nombre, 'params': dict(params),
        'status': None, 'json': None, 'tiempo_ms': None, 'n_queries': None,
        'error': None, 'escrituras': [],
    }
    try:
        view = _import_view(import_path)
    except Exception as e:
        res['error'] = f'import: {e}'
        return res

    request = factory.get('/_test', data=params)
    request.user = ctx.usuario
    request.session = {
        'idSucursalActual': ctx.sucursal_id,
        'idEmpresaActual': ctx.empresa_id,
    }
    reset_queries()
    t0 = time.perf_counter()
    try:
        with transaction.atomic():
            response = view(request)
            transaction.set_rollback(True)  # cinturón: nada persiste
    except Exception as e:
        res['error'] = f'runtime: {type(e).__name__}: {e}'
        res['tiempo_ms'] = round((time.perf_counter() - t0) * 1000, 1)
        res['n_queries'] = len(connection.queries)
        return res
    res['tiempo_ms'] = round((time.perf_counter() - t0) * 1000, 1)
    res['n_queries'] = len(connection.queries)
    res['escrituras'] = _solo_selects(connection.queries)
    res['status'] = getattr(response, 'status_code', None)
    try:
        res['json'] = json.loads(response.content)
    except Exception:
        res['json'] = None
    return res


def C(nombre, ok, esperado='', obtenido='', detalle=''):
    """Constructor corto de check. ok: True/False/None(WARN)/'skip'."""
    estado = 'SKIP' if ok == 'skip' else ('PASS' if ok is True else ('WARN' if ok is None else 'FAIL'))
    return {'check': nombre, 'estado': estado, 'esperado': str(esperado),
            'obtenido': str(obtenido), 'detalle': detalle}


def _pct(parte, todo):
    return round(100.0 * parte / todo, 1) if todo else 0.0


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _cerca(a, b, tol_pct=0.5):
    a, b = _num(a), _num(b)
    if a == b:
        return True
    base = max(abs(a), abs(b))
    return base > 0 and abs(a - b) / base * 100 <= tol_pct


# ============================================================ RESOLVER CONTEXTOS

def resolver_contextos(fi, ff):
    User = get_user_model()
    admin = (User.objects.filter(rol='administrador', is_active=True).first()
             or User.objects.filter(is_superuser=True, is_active=True).first())

    tiendas = (Ticket.objects
               .filter(created_at__date__gte=fi, created_at__date__lte=ff,
                       estado='PAGADO', sucursal__es_centro_distribucion=False)
               .values('sucursal_id', 'sucursal__empresa_id')
               .annotate(n=Count('id')).order_by('-n'))
    tienda = tiendas[0] if tiendas else None

    cds = (Producto_Talla.objects
           .filter(producto__sucursal__es_centro_distribucion=True, stock__gt=0)
           .values('producto__sucursal_id', 'producto__sucursal__empresa_id')
           .annotate(s=Sum('stock')).order_by('-s'))
    cd = cds[0] if cds else None

    # Usuario restringido: activo, no admin, con EmpresaUser acotado y sin
    # permiso "ver todas". Se evalúa con la MISMA función que usan las vistas.
    restringido = None
    candidatos = (User.objects.filter(is_active=True, is_superuser=False)
                  .exclude(rol='administrador')[:60])
    for u in candidatos:
        eus = list(EmpresaUser.objects.filter(user=u, status=True)
                   .values_list('empresa_id', 'sucursal_id'))
        if not eus:
            continue
        empresas = {e for e, _ in eus}
        if len(empresas) >= Sucursal.objects.values('empresa_id').distinct().count():
            continue
        try:
            if usuario_puede_ver_todas_sucursales(u):
                continue
        except Exception:
            continue
        suc_id = next((s for _, s in eus if s), None)
        if not suc_id:
            suc_id = (Sucursal.objects.filter(empresa_id__in=empresas)
                      .values_list('id', flat=True).first())
        restringido = Contexto('RESTRINGIDO', u, suc_id, next(iter(empresas)))
        break

    ctxs = {}
    if admin and tienda:
        ctxs['GLOBAL'] = Contexto('GLOBAL', admin, tienda['sucursal_id'],
                                  tienda['sucursal__empresa_id'])
        ctxs['TIENDA'] = Contexto('TIENDA', admin, tienda['sucursal_id'],
                                  tienda['sucursal__empresa_id'])
    if admin and cd:
        ctxs['CD'] = Contexto('CD', admin, cd['producto__sucursal_id'],
                              cd['producto__sucursal__empresa_id'])
    if restringido:
        ctxs['RESTRINGIDO'] = restringido
    return ctxs


def resolver_fixtures(fi, ff, ctxs):
    """IDs de apoyo resueltos por query (sin hardcodeo)."""
    fx = {}
    # Día más movido de la tienda en el rango (para diagnóstico cuadratura)
    tienda = ctxs.get('TIENDA')
    if tienda:
        dia = (Dte.objects.filter(sucursal_id=tienda.sucursal_id,
                                  fecha_emision__gte=fi, fecha_emision__lte=ff)
               .values('fecha_emision').annotate(n=Count('id')).order_by('-n').first())
        fx['dia_top'] = dia['fecha_emision'].strftime('%Y-%m-%d') if dia else fi.strftime('%Y-%m-%d')

    v = (Dte.objects.filter(fecha_emision__gte=fi, fecha_emision__lte=ff,
                            tipo_transaccion='VENTA_PUBLICO', vendedor__isnull=False)
         .values('vendedor_id').annotate(n=Count('id')).order_by('-n').first())
    fx['vendedor_top'] = v['vendedor_id'] if v else None

    m = (Producto_Talla.objects.filter(stock__gt=0, producto__atributo1__isnull=False)
         .values('producto__atributo1_id').annotate(s=Sum('stock')).order_by('-s').first())
    fx['marca_top'] = m['producto__atributo1_id'] if m else None

    padre = (Categoria.objects.filter(padre__isnull=True, subcategorias__isnull=False)
             .exclude(nombre__startswith='_ZZ_').annotate(n=Count('subcategorias'))
             .order_by('-n').first())
    fx['cat_padre'] = padre.id if padre else None
    fx['cat_padre_nombre'] = padre.nombre if padre else None
    if padre:
        hija = (Categoria.objects.filter(padre=padre)
                .annotate(n=Count('categoria_productos')).order_by('-n').first())
        fx['cat_hija'] = hija.id if hija else None
        fx['cat_hija_nombre'] = hija.nombre if hija else None

    # SKU de otra empresa (para fuga kardex con usuario RESTRINGIDO)
    r = ctxs.get('RESTRINGIDO')
    if r:
        empresas_r = set(EmpresaUser.objects.filter(user=r.usuario, status=True)
                         .values_list('empresa_id', flat=True))
        talla_ajena = (Producto_Talla.objects
                       .exclude(producto__sucursal__empresa_id__in=empresas_r)
                       .filter(stock__gt=0).values_list('id', flat=True).first())
        fx['talla_ajena'] = talla_ajena
        fx['empresas_restringido'] = sorted(empresas_r)
    return fx


# ============================================================ ORÁCULOS

def oraculo_ventas_reporte(fi, ff, sucursal_id=None):
    """Réplica independiente de obtener_ventas_por_sucursal_reporte (neto)."""
    base = Dte.objects.filter(
        fecha_emision__gte=fi, fecha_emision__lte=ff,
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION', 'ANULACION'],
        estado_dte__in=['EMITIDO', 'ACEPTADO', 'ANULADO'],
        descartado=False, sucursal__isnull=False,
    )
    if sucursal_id:
        base = base.filter(sucursal_id=sucursal_id)
    ventas = base.exclude(tipo_documento='NOTA DE CREDITO').aggregate(
        m=Sum('monto_con_iva'), n=Count('id'))
    ncs = base.filter(tipo_documento='NOTA DE CREDITO').aggregate(
        m=Sum('monto_con_iva'), n=Count('id'))
    return {
        'monto_neto': int(ventas['m'] or 0) - int(ncs['m'] or 0),
        'docs_venta': ventas['n'], 'docs_nc': ncs['n'],
    }


def oraculo_docs_emitidos(fi, ff, sucursal_ids=None):
    qs = Dte.objects.filter(
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        fecha_emision__gte=fi, fecha_emision__lte=ff,
    )
    if sucursal_ids:
        qs = qs.filter(sucursal_id__in=sucursal_ids)
    return qs.count()


def oraculo_productos_vendidos(fi, ff):
    """Réplica exacta de _agregar_productos_vendidos a nivel de totales (GLOBAL)."""
    # Tickets sin DTE
    tp = (Ticket_Productos.objects.filter(
        idTicket__created_at__date__gte=fi, idTicket__created_at__date__lte=ff,
        idTicket__estado='PAGADO',
        idTicket__modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
        idTicket__dte_generado=False, ProductoTalla__isnull=False,
        ProductoTalla__producto__excluir_de_analitica=False,
    ).values('ProductoTalla__producto_id')
        .annotate(u=Sum('stock'), m=Sum('subtotal')))
    prods = {}
    for r in tp:
        pid = r['ProductoTalla__producto_id']
        if not pid:
            continue
        p = prods.setdefault(pid, {'u': 0, 'm': 0})
        p['u'] += int(r['u'] or 0)
        p['m'] += int(r['m'] or 0)
    # Líneas DTE (fallback monto_item→precio*stock POR PRODUCTO, igual que la vista)
    dp = (Dte_Productos.objects.filter(
        dte__fecha_emision__gte=fi, dte__fecha_emision__lte=ff,
        dte__tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
        productoTalla__isnull=False,
        productoTalla__producto__excluir_de_analitica=False,
    ).exclude(dte__estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])
        .exclude(dte__tipo_documento='NOTA DE CREDITO')
        .exclude(dte__receptor__isnull=False, dte__receptor_id=F('dte__emisor_id'))
        .values('productoTalla__producto_id')
        .annotate(u=Sum('stock'), mi=Sum('monto_item'),
                  mp=Sum(ExpressionWrapper(F('precio') * F('stock'),
                                           output_field=DecimalField()))))
    for r in dp:
        pid = r['productoTalla__producto_id']
        if not pid:
            continue
        p = prods.setdefault(pid, {'u': 0, 'm': 0})
        monto = int(r['mi'] or 0)
        if monto <= 0:
            monto = int(r['mp'] or 0)
        p['u'] += int(r['u'] or 0)
        p['m'] += monto
    # Devoluciones (solo netean productos CON venta en el período)
    dev = (CambioDevolucionDetalle.objects.filter(
        producto_original__isnull=False, cantidad_original__gt=0,
        cambio_devolucion__fecha_ejecucion__date__gte=fi,
        cambio_devolucion__fecha_ejecucion__date__lte=ff,
        producto_original__ProductoTalla__producto__excluir_de_analitica=False,
    ).values('producto_original__ProductoTalla__producto_id')
        .annotate(u=Sum('cantidad_original'),
                  m=Sum(ExpressionWrapper(
                      F('precio_original_unitario') * F('cantidad_original'),
                      output_field=DecimalField()))))
    for r in dev:
        pid = r['producto_original__ProductoTalla__producto_id']
        p = prods.get(pid)
        if not p:
            continue
        p['u'] -= int(r['u'] or 0)
        p['m'] -= int(r['m'] or 0)
    # % del monto con especialidad v1.2 (gap de filtro)
    etiquetados = set(ProductoAtributoValor.objects.filter(
        atributo__nombre__iexact='Especialidad').values_list('producto_id', flat=True))
    monto_total = sum(p['m'] for p in prods.values())
    monto_esp = sum(p['m'] for pid, p in prods.items() if pid in etiquetados)
    return {
        'unidades': sum(p['u'] for p in prods.values()),
        'monto': monto_total,
        'skus': len(prods),
        'pct_monto_con_especialidad': _pct(monto_esp, monto_total),
    }


def oraculo_censo_categorias():
    padres = Categoria.objects.filter(padre__isnull=True).exclude(nombre__startswith='_ZZ_')
    hijas = Categoria.objects.filter(padre__isnull=False)
    zz = Categoria.objects.filter(nombre__startswith='_ZZ_')
    padres_ids = set(padres.values_list('id', flat=True))
    hijas_ids = set(hijas.values_list('id', flat=True))
    con_hijas = set(Categoria.objects.filter(subcategorias__isnull=False)
                    .values_list('id', flat=True))
    planas_vivas = padres_ids - con_hijas  # raíz sin hijas = plana vieja viva
    prod = Producto.objects.filter(excluir_de_analitica=False)
    total = prod.count()
    en_hijas = prod.filter(categoria_id__in=hijas_ids).count()
    sin_cat = prod.filter(categoria__isnull=True).count()
    en_planas = total - en_hijas - sin_cat
    return {
        'padres_v12': sorted(Categoria.objects.filter(
            padre__isnull=True, subcategorias__isnull=False)
            .exclude(nombre__startswith='_ZZ_')
            .values_list('nombre', flat=True).distinct()),
        'n_hijas': hijas.count(), 'n_zz': zz.count(),
        'n_planas_vivas': len(planas_vivas),
        'planas_vivas_ids': planas_vivas,
        'hijas_ids': hijas_ids,
        'pct_prod_en_hijas': _pct(en_hijas, total),
        'pct_prod_en_planas': _pct(en_planas, total),
        'pct_prod_sin_cat': _pct(sin_cat, total),
        'total_productos': total,
    }


def oraculo_genero_atributos():
    prod = Producto.objects.filter(excluir_de_analitica=False)
    total = prod.count()
    a3 = prod.filter(atributo3__isnull=False).count()
    a4 = prod.filter(atributo4__isnull=False).count()
    return {'total': total, 'pct_atributo3': _pct(a3, total), 'pct_atributo4': _pct(a4, total)}


def oraculo_stock_resumen(usuario):
    """Mismo scope que resumen-existencias (empresas del usuario)."""
    empresas = EmpresaUser.objects.filter(user=usuario, status=True) \
        .values_list('empresa_id', flat=True)
    base = Producto_Talla.objects.filter(
        producto__sucursal__empresa_id__in=empresas, stock__gt=0)
    visible = base.filter(producto__excluir_de_analitica=False,
                          producto__categoria__isnull=False).aggregate(
        pares=Sum('stock'), costo=Sum(F('stock') * F('producto__costo')))
    sin_cat = base.filter(producto__excluir_de_analitica=False,
                          producto__categoria__isnull=True).aggregate(
        pares=Sum('stock'), costo=Sum(F('stock') * F('producto__costo')))
    excluidos = base.filter(producto__excluir_de_analitica=True).aggregate(pares=Sum('stock'))
    return {
        'pares_visibles': int(visible['pares'] or 0),
        'costo_visible': int(visible['costo'] or 0),
        'pares_sin_categoria': int(sin_cat['pares'] or 0),
        'costo_sin_categoria': int(sin_cat['costo'] or 0),
        'pares_excluidos_analitica': int(excluidos['pares'] or 0),
    }


def oraculo_lotes_vs_stock(muestra_n=200):
    universo = list(Producto_Talla.objects.filter(stock__gt=0)
                    .order_by('id').values_list('id', 'stock')[:20000])
    paso = max(1, len(universo) // muestra_n)
    tallas = universo[::paso][:muestra_n]
    ids = [t for t, _ in tallas]
    lotes = {r['producto_talla_id']: int(r['s'] or 0)
             for r in LoteProducto.objects.filter(
                 producto_talla_id__in=ids, activo=True)
             .values('producto_talla_id').annotate(s=Sum('cantidad_disponible'))}
    drift = sum(1 for tid, stock in tallas if lotes.get(tid, 0) != stock)
    return {'muestra': len(tallas), 'con_drift': drift, 'pct_drift': _pct(drift, len(tallas))}


def oraculo_comparativa_doble_conteo(fi, ff):
    """Tickets PAGADO con DTE generado (los cuenta 2 veces comparativa-mensual)."""
    qs = Ticket.objects.filter(
        created_at__date__gte=fi, created_at__date__lte=ff, estado='PAGADO',
        modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'], dte_generado=True)
    agg = qs.aggregate(n=Count('id'), m=Sum('total'))
    return {'tickets_con_dte': agg['n'], 'monto_duplicable': int(agg['m'] or 0)}


# ============================================================ CHECKS POR REPORTE

def _lista_en(js, *keys):
    """Encuentra la primera lista no vacía bajo alguna de las keys (o anidada en data)."""
    if not isinstance(js, dict):
        return None, None
    for k in keys:
        v = js.get(k)
        if isinstance(v, list):
            return k, v
    data = js.get('data')
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return f'data.{k}', v
    if isinstance(data, list):
        return 'data', data
    return None, None


def checks_universales(res, umbral_q=50, umbral_ms=8000):
    out = []
    ok_status = res['status'] == 200 and res['error'] is None
    out.append(C('status_200', ok_status, 200, res['status'] or res['error']))
    out.append(C('solo_selects', not res['escrituras'], '0 escrituras',
                 f"{len(res['escrituras'])}", '; '.join(res['escrituras'][:3])))
    js = res['json'] or {}
    if isinstance(js, dict) and js.get('success') is False:
        out.append(C('success_true', False, 'success=true', js.get('error', 'success=false')))
    perf_ok = (res['n_queries'] or 0) <= umbral_q and (res['tiempo_ms'] or 0) <= umbral_ms
    out.append(C('performance', True if perf_ok else None,
                 f'<={umbral_q}q/<={umbral_ms}ms',
                 f"{res['n_queries']}q/{res['tiempo_ms']}ms"))
    return out


def check_ventas_sucursal(res, oraculos, fx):
    out = checks_universales(res)
    js = res['json'] or {}
    filas = js.get('sucursales') or []
    total_api = sum(_num(f.get('ventas')) for f in filas)
    ora = oraculos['ventas_reporte']
    out.append(C('total_vs_oraculo', _cerca(total_api, ora['monto_neto']),
                 f"${ora['monto_neto']:,}", f"${int(total_api):,}",
                 'suma filas.ventas vs Dte neto (regla reporte)'))
    suma_docs = sum(int(_num(f.get('documentos'))) for f in filas)
    out.append(C('docs_vs_oraculo', _cerca(suma_docs, ora['docs_venta'], 1.0),
                 ora['docs_venta'], suma_docs))
    return out


def check_ventas_vendedor(res, oraculos, fx):
    out = checks_universales(res)
    js = res['json'] or {}
    _, filas = _lista_en(js, 'vendedores', 'datos')
    filas = filas or []
    total_api = sum(_num(f.get('ventas') if 'ventas' in f else f.get('total_ventas'))
                    for f in filas)
    ora = oraculos['ventas_reporte']
    # neto: filas ya vienen con NC restadas (mismas reglas)
    out.append(C('suma_vendedores_vs_oraculo', _cerca(total_api, ora['monto_neto'], 1.0),
                 f"${ora['monto_neto']:,}", f"${int(total_api):,}",
                 'incluye fila "Sin vendedor"'))
    return out


def check_diagnostico(res, oraculos, fx):
    out = checks_universales(res)
    js = res['json'] or {}
    if js.get('success'):
        dif = _num(js.get('diferencia'))
        auto = _num(js.get('cuadratura_total')) - _num(js.get('reporte_total'))
        out.append(C('autoconsistencia_delta', abs(dif - auto) < 1,
                     auto, dif, 'diferencia == cuadratura - reporte'))
        disputa = (js.get('solo_en_cuadratura') or []) + (js.get('solo_en_reporte') or [])
        sin_motivo = [d for d in js.get('solo_en_cuadratura', [])
                      if not d.get('motivos_exclusion_reporte')]
        out.append(C('motivos_completos', not sin_motivo,
                     '0 sin motivo', len(sin_motivo),
                     f'{len(disputa)} DTEs en disputa el {js.get("fecha")}'))
    return out


def check_documentos_vendedor(res, oraculos, fx):
    out = checks_universales(res)
    js = res['json'] or {}
    docs = js.get('documentos') or []
    fechas = []
    for d in docs:
        try:
            fechas.append(datetime.strptime(d.get('fecha', ''), '%d/%m/%Y').date())
        except ValueError:
            pass
    orden_ok = all(fechas[i] >= fechas[i + 1] for i in range(len(fechas) - 1))
    out.append(C('orden_cronologico', orden_ok if fechas else 'skip',
                 'descendente por fecha real',
                 'ordenado' if orden_ok else 'DESORDENADO (ordena por string dd/mm/YYYY)',
                 f'{len(docs)} documentos'))
    return out


def check_comparativa(res, oraculos, fx):
    out = checks_universales(res)
    js = res['json'] or {}
    series = js.get('series') or []
    out.append(C('series_presentes', bool(series), '>0 series', len(series)))
    # Verificación a nivel de VISTA: el mes del rango de test sumado sobre
    # todas las series debe calzar con tickets-sin-DTE + DTEs VP netos.
    fi, ff = oraculos['rango']
    label_mes = fi.strftime('%b %Y')
    cats = js.get('categories') or []
    if label_mes in cats and series:
        idx = cats.index(label_mes)
        total_vista = sum(_num((s.get('data') or [0] * len(cats))[idx]) for s in series)
        t = Ticket.objects.filter(
            created_at__date__gte=fi, created_at__date__lte=ff, estado='PAGADO',
            dte_generado=False,
            modulo_origen__in=['VENTA_PUBLICO', 'POS', 'ECOMMERCE'],
            sucursal__isnull=False,
        ).aggregate(m=Sum('total'))['m'] or 0
        base_d = Dte.objects.filter(
            fecha_emision__gte=fi, fecha_emision__lte=ff,
            tipo_transaccion='VENTA_PUBLICO', sucursal__isnull=False,
        ).exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO']) \
            .exclude(receptor__isnull=False, receptor_id=F('emisor_id'))
        dv = base_d.exclude(tipo_documento='NOTA DE CREDITO').aggregate(m=Sum('monto_con_iva'))['m'] or 0
        dn = base_d.filter(tipo_documento='NOTA DE CREDITO').aggregate(m=Sum('monto_con_iva'))['m'] or 0
        esperado = int(t) + int(dv) - int(dn)
        dc = oraculos['doble_conteo']
        out.append(C('anti_doble_conteo', _cerca(total_vista, esperado, 1.5),
                     f'${esperado:,}', f'${int(total_vista):,}',
                     f"si duplica: habría ~${dc['monto_duplicable']:,} extra "
                     f"({dc['tickets_con_dte']} tickets con DTE en el mes)"))
    else:
        out.append(C('anti_doble_conteo', 'skip', label_mes, f'mes no presente en {cats[:3]}'))
    return out


def check_documentos_emitidos(res, oraculos, fx):
    out = checks_universales(res)
    js = res['json'] or {}
    _, docs = _lista_en(js, 'documentos', 'datos')
    docs = docs or []
    total_real = js.get('total_real') or js.get('total') or (js.get('resumen') or {}).get('total_documentos')
    pag = js.get('pagination') or {}
    ora = oraculos['docs_emitidos_mes']
    # Con paginación declarada (page/total) ya no es truncado silencioso.
    truncado = len(docs) == 100 and ora > 100 and not pag.get('total_pages')
    out.append(C('truncamiento_100', not truncado,
                 f'{ora} docs del mes visibles o paginados',
                 f'{len(docs)} filas (total_real={total_real}, '
                 f'paginacion={pag.get("total_pages", "NO")})',
                 f'{max(ora - 100, 0)} documentos invisibles en el mes' if truncado else ''))
    return out


def check_ventas_global(res, oraculos, fx, ctx):
    out = checks_universales(res)
    js = res['json'] or {}
    _, filas = _lista_en(js, 'empresas', 'sucursales', 'datos')
    filas = filas or []
    permitidas = set(fx.get('empresas_restringido') or [])
    vistas = set()
    for f in filas:
        eid = f.get('empresa_id') or f.get('id')
        if eid:
            vistas.add(eid)
        for s in (f.get('sucursales') or []):
            if s.get('empresa_id'):
                vistas.add(s['empresa_id'])
    fugadas = vistas - permitidas if permitidas else set()
    if ctx.nombre == 'RESTRINGIDO':
        out.append(C('fuga_multiempresa', not fugadas,
                     f'solo empresas {sorted(permitidas)}',
                     f've {sorted(vistas)}',
                     'P0: agrega TODAS las empresas sin scoping' if fugadas else ''))
    return out


def check_productos_vendidos(res, oraculos, fx):
    out = checks_universales(res, umbral_q=80)
    js = res['json'] or {}
    kpis = js.get('kpis') or {}
    ora = oraculos['productos_vendidos']
    out.append(C('kpi_monto_vs_oraculo', _cerca(kpis.get('total_monto'), ora['monto']),
                 f"${ora['monto']:,}", f"${int(_num(kpis.get('total_monto'))):,}"))
    out.append(C('kpi_unidades_vs_oraculo', _cerca(kpis.get('total_unidades'), ora['unidades']),
                 ora['unidades'], kpis.get('total_unidades')))
    # Partición: cada agrupación debe sumar el total
    for dim in ('por_marca', 'por_categoria', 'por_sexo', 'por_genero'):
        filas = js.get(dim) or []
        s = sum(_num(f.get('monto')) for f in filas)
        posible_trunc = len(filas) == 100
        ok = _cerca(s, kpis.get('total_monto'))
        out.append(C(f'particion_{dim}', ok if not posible_trunc or ok else None,
                     f"${int(_num(kpis.get('total_monto'))):,}", f"${int(s):,}",
                     'truncado a 100 filas' if posible_trunc else ''))
    # Censo v1.2 en por_categoria
    censo = oraculos['censo']
    filas_cat = js.get('por_categoria') or []
    ids_api = {f.get('id') for f in filas_cat if f.get('id')}
    en_hijas = ids_api & censo['hijas_ids']
    en_planas = ids_api & censo['planas_vivas_ids']
    zz_nombres = [f['nombre'] for f in filas_cat
                  if str(f.get('nombre', '')).startswith('_ZZ_')]
    monto_hijas = sum(_num(f.get('monto')) for f in filas_cat if f.get('id') in censo['hijas_ids'])
    monto_tot = sum(_num(f.get('monto')) for f in filas_cat)
    out.append(C('categorias_v12', len(en_planas) == 0 and not zz_nombres,
                 '100% hijas v1.2, 0 _ZZ_',
                 f'{len(en_hijas)} hijas / {len(en_planas)} planas vivas / {len(zz_nombres)} _ZZ_',
                 f'{_pct(monto_hijas, monto_tot)}% del monto en hijas v1.2'))
    # Sin label Padre › Hijo (ambigüedad de hijas homónimas)
    tiene_padre = any('›' in str(f.get('nombre', '')) for f in filas_cat)
    out.append(C('label_padre_hijo', tiene_padre if filas_cat else 'skip',
                 'label "Padre › Hijo"', 'solo nombre hijo' if not tiene_padre else 'ok',
                 'hijas homónimas de padres distintos colapsan visualmente'))
    # atributo4 (columna "género") muerta
    gen = oraculos['genero']
    filas_gen = js.get('por_genero') or []
    monto_sin = sum(_num(f.get('monto')) for f in filas_gen if not f.get('id'))
    monto_gen_tot = sum(_num(f.get('monto')) for f in filas_gen)
    out.append(C('atributo4_poblado', gen['pct_atributo4'] >= 5,
                 '>=5% productos con atributo4',
                 f"{gen['pct_atributo4']}% poblado; "
                 f"{_pct(monto_sin, monto_gen_tot)}% del monto sin género(atributo4)",
                 'columna muerta: candidata a eliminar del reporte'))
    # Heatmap consistente
    heat = js.get('heatmap') or []
    s_heat = sum(_num(h.get('monto')) for h in heat)
    out.append(C('heatmap_suma', _cerca(s_heat, kpis.get('total_monto'), 2.0),
                 f"${int(_num(kpis.get('total_monto'))):,}", f"${int(s_heat):,}",
                 'tolerancia 2% (devoluciones sin celda)'))
    # Gap de especialidades
    out.append(C('gap_especialidades', None,
                 'filtro/agrupación por especialidad',
                 f"{ora['pct_monto_con_especialidad']}% del monto vendido tiene etiqueta",
                 'ningún reporte consume especialidades v1.2 aún'))
    return out


def check_pv_filtro_padre(res_padre, res_hija, fx):
    out = []
    js_p = res_padre['json'] or {}
    js_h = res_hija['json'] or {}
    n_padre = js_p.get('productos_total', 0)
    n_hija = js_h.get('productos_total', 0)
    out.append(C('filtro_categoria_padre_expande',
                 (n_padre or 0) >= (n_hija or 0) and (n_padre or 0) > 0,
                 f'padre "{fx.get("cat_padre_nombre")}" >= hija "{fx.get("cat_hija_nombre")}" (>0)',
                 f'padre={n_padre} productos, hija={n_hija}',
                 'filtrar por el padre debería incluir todas sus hijas'))
    return out


def check_resumen_existencias(res, oraculos, fx):
    out = checks_universales(res)
    js = res['json'] or {}
    key, filas = _lista_en(js, 'datos', 'categorias', 'resumen', 'items', 'grupos')
    filas = filas or []
    pares_api = sum(_num(f.get('pares') or f.get('total_pares') or f.get('stock')) for f in filas)
    ora = oraculos['stock_resumen']
    out.append(C('total_vs_oraculo', _cerca(pares_api, ora['pares_visibles'], 1.0),
                 f"{ora['pares_visibles']:,} pares", f'{int(pares_api):,} pares',
                 f'lista bajo "{key}"'))
    out.append(C('stock_invisible', ora['pares_sin_categoria'] == 0,
                 '0 pares sin categoría',
                 f"{ora['pares_sin_categoria']:,} pares (${ora['costo_sin_categoria']:,}) "
                 f"+ {ora['pares_excluidos_analitica']:,} excluidos analítica",
                 'el reporte exige categoria__isnull=False → esto queda invisible'))
    censo = oraculos['censo']
    nombres = {str(f.get('nombre') or f.get('categoria') or '') for f in filas}
    zz = [n for n in nombres if n.startswith('_ZZ_')]
    padres_ok = set(censo['padres_v12'])
    raices_extranas = [n for n in nombres if n and n not in padres_ok
                       and not n.startswith('_ZZ_')
                       and not n.startswith('Sin recategorizar')
                       and n not in ('Sin categoría', 'Sin clasificar')]
    out.append(C('raices_son_padres_v12', not raices_extranas and not zz,
                 f'raíces = {censo["padres_v12"]}',
                 f'{len(raices_extranas)} raíces no-v1.2: {sorted(raices_extranas)[:8]}; _ZZ_: {len(zz)}',
                 'planas viejas vivas apareciendo como raíz'))
    return out


def check_existencias_sucursal(res, oraculos, fx, ctx):
    out = checks_universales(res)
    js = res['json'] or {}
    resumen = js.get('resumen') or {}
    ora_stock = Producto_Talla.objects.filter(
        producto__sucursal_id=ctx.sucursal_id,
        producto__excluir_de_analitica=False, stock__gt=0,
        **({'producto__atributo1_id': fx['marca_top']} if fx.get('marca_top') else {}),
    ).aggregate(s=Sum('stock'))['s'] or 0
    out.append(C('stock_vs_oraculo', _cerca(resumen.get('stock_total'), ora_stock, 1.0),
                 f'{ora_stock:,}', resumen.get('stock_total')))
    datos = js.get('datos') or []
    tiene_genero = bool(datos) and 'genero' in datos[0]
    out.append(C('rotulo_atributo3', None if tiene_genero else 'skip',
                 'convención única sexo/género',
                 'aquí atributo3="genero"; en productos-vendidos atributo3="sexo"',
                 'misma columna, dos nombres — homogeneizar'))
    censo = oraculos['censo']
    cats = {d.get('categoria') for d in datos if d.get('categoria') and d['categoria'] != '-'}
    hijas_nombres = set(Categoria.objects.filter(id__in=censo['hijas_ids'])
                        .values_list('nombre', flat=True))
    no_v12 = [c for c in cats if c not in hijas_nombres]
    out.append(C('categorias_v12_filas', not no_v12,
                 'todas las filas en hijas v1.2',
                 f'{len(no_v12)} categorías no-v1.2 en filas: {sorted(no_v12)[:6]}'))
    return out


def check_existencias_general(res_p1, res_p2, res_padre, fx):
    out = checks_universales(res_p1)
    js1, js2 = res_p1['json'] or {}, res_p2['json'] or {}
    _, l1 = _lista_en(js1, 'productos', 'datos', 'existencias')
    _, l2 = _lista_en(js2, 'productos', 'datos', 'existencias')
    if l1 and l2:
        skus1 = {str(r.get('sku') or r.get('id')) for r in l1}
        skus2 = {str(r.get('sku') or r.get('id')) for r in l2}
        dup = skus1 & skus2
        out.append(C('paginacion_sin_duplicados', not dup,
                     'páginas disjuntas', f'{len(dup)} SKUs repetidos'))
    jsp = res_padre['json'] or {}
    _, lp = _lista_en(jsp, 'productos', 'datos', 'existencias')
    total_padre = (jsp.get('total_registros') or jsp.get('total')
                   or (len(lp) if lp is not None else None))
    out.append(C('filtro_categoria_padre_expande',
                 bool(total_padre) or 'skip' if total_padre is None else bool(total_padre),
                 f'filtrar por padre "{fx.get("cat_padre_nombre")}" devuelve >0',
                 total_padre,
                 'el filtro no expande hijas → padre devuelve 0'))
    return out


def check_movimientos_sucursal(res, oraculos, fx, ctx):
    out = checks_universales(res)
    js = res['json'] or {}
    datos = js.get('datos') or []
    restante_api = sum(_num(d.get('total_restante')) for d in datos)
    empresas = EmpresaUser.objects.filter(user=ctx.usuario, status=True) \
        .values_list('empresa_id', flat=True)
    # El reporte trae `solo_tiendas=true` por defecto, así que el oráculo tiene
    # que excluir las bodegas proveedoras/CD o compara peras con manzanas.
    # `es_compradora` es una property de Python: se replica su definición.
    ora = Producto_Talla.objects.filter(
        producto__sucursal__empresa_id__in=empresas,
        producto__excluir_de_analitica=False,
        producto__atributo1_id=fx['marca_top'],
    ).exclude(
        Q(producto__sucursal__es_centro_distribucion=True)
        | Q(producto__sucursal__tipo_sucursal='CENTRO_DISTRIBUCION')
    ).aggregate(s=Sum('stock'))['s'] or 0
    out.append(C('restante_vs_stock_oraculo', _cerca(restante_api, ora, 2.0),
                 f'{ora:,}', f'{int(restante_api):,}',
                 'marca top, solo tiendas; tolerancia 2% (filas sin datos se omiten)'))
    # Ninguna bodega proveedora debe aparecer como columna.
    cds = set(Sucursal.objects.filter(
        Q(empresa_id__in=empresas)
        & (Q(es_centro_distribucion=True) | Q(tipo_sucursal='CENTRO_DISTRIBUCION'))
    ).values_list('alias', flat=True))
    filtradas = {s for d in datos for s in (d.get('sucursales') or {})} & cds
    out.append(C('sin_bodegas_proveedoras', not filtradas,
                 '0 bodegas en columnas', sorted(filtradas)[:6] or '0'))
    censo = oraculos['censo']
    hijas_nombres = set(Categoria.objects.filter(id__in=censo['hijas_ids'])
                        .values_list('nombre', flat=True))
    deps = {d.get('departamento') for d in datos if d.get('departamento') and d['departamento'] != '-'}
    no_v12 = [d for d in deps if d not in hijas_nombres]
    out.append(C('departamentos_v12', not no_v12,
                 'todas hijas v1.2', f'{len(no_v12)} no-v1.2: {sorted(no_v12)[:6]}'))
    return out


def check_kardex_fuga(res, fx):
    # Aquí el resultado DESEADO es un 403, así que no aplica check_status_200.
    out = [C('solo_selects', not res['escrituras'], '0 escrituras', len(res['escrituras']))]
    js = res['json'] or {}
    devolvio = res['status'] == 200 and bool(js.get('success')) and bool(js.get('items'))
    out.append(C('fuga_kardex', not devolvio,
                 'denegado (SKU de otra empresa)',
                 f'devolvió {len(js.get("items") or [])} movimientos' if devolvio
                 else f'denegado (status={res["status"]})',
                 'P0: sin filtro de empresa/sucursal' if devolvio else ''))
    return out


def check_kardex_saldo(ctx):
    """Compara saldo final del kardex vs stock actual en muestra de SKUs."""
    out = []
    tallas = list(Producto_Talla.objects.filter(stock__gt=0)
                  .order_by('-id').values_list('id', 'stock')[:60])
    probados = coinciden = 0
    for tid, stock in tallas:
        if probados >= 20:
            break
        res = invocar_vista('app.views.reporte_movimientos_kardex',
                            {'producto_talla_id': tid, 'page_size': 500}, ctx)
        js = res['json'] or {}
        pag = js.get('pagination') or {}
        if not js.get('success') or (pag.get('total_count') or 0) > 500:
            continue
        items = js.get('items') or []
        if not items:
            continue
        probados += 1
        if int(items[-1].get('saldo') or 0) == int(stock):
            coinciden += 1
    ok = coinciden >= probados * 0.8 if probados else 'skip'
    out.append(C('saldo_kardex_vs_stock', True if ok is True else (None if probados else 'skip'),
                 '>=80% SKUs cuadran', f'{coinciden}/{probados} cuadran',
                 'drift kardex↔stock conocido (ver reconciliar_stock_lotes)'))
    return out


def check_smoke(res, extra=''):
    return checks_universales(res, umbral_q=250, umbral_ms=30000)


# ============================================================ REGISTRO

def construir_plan(fi, ff, ctxs, fx, rapido):
    """Lista de (clave, callable) — cada callable devuelve (checks, invocaciones)."""
    G = ctxs.get('GLOBAL')
    T = ctxs.get('TIENDA')
    CDx = ctxs.get('CD') or G
    R = ctxs.get('RESTRINGIDO')
    fi_s, ff_s = fi.strftime('%Y-%m-%d'), ff.strftime('%Y-%m-%d')
    rango = {'fecha_inicio': fi_s, 'fecha_fin': ff_s}
    plan = []

    def _run(nombre, path, params, ctx_inv, checker, **kw):
        def go(oraculos):
            res = invocar_vista(path, params, ctx_inv)
            try:
                checks = checker(res, oraculos, fx, **kw) if kw else checker(res, oraculos, fx)
            except TypeError:
                checks = checker(res, oraculos, fx)
            except Exception as e:
                checks = checks_universales(res) + [C('checker_error', False, '', str(e))]
            return checks, [res]
        return (nombre, go)

    if G:
        plan.append(_run('ventas_sucursal',
                         'app.views_modulo_reportes.obtener_ventas_por_sucursal_reporte',
                         rango, G, check_ventas_sucursal))
        plan.append(_run('ventas_vendedor',
                         'app.views_modulo_reportes.obtener_ventas_por_vendedor_reporte',
                         rango, G, check_ventas_vendedor))
        plan.append(('comisiones', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes.obtener_comisiones_por_vendedor', rango, G)), [])))
    if T and fx.get('dia_top'):
        plan.append(_run('diagnostico_cuadratura',
                         'app.views_modulo_reportes.api_diagnostico_cuadratura_vs_reporte',
                         {'fecha': fx['dia_top'], 'sucursal_id': T.sucursal_id},
                         T, check_diagnostico))
    if G and fx.get('vendedor_top'):
        plan.append(_run('documentos_vendedor',
                         'app.views_modulo_reportes.obtener_documentos_vendedor_reporte',
                         {**rango, 'vendedor_id': fx['vendedor_top']},
                         G, check_documentos_vendedor))
    if G:
        plan.append(_run('comparativa_mensual',
                         'app.views_modulo_reportes.obtener_comparativa_mensual',
                         {}, G, check_comparativa))
        plan.append(_run('documentos_emitidos',
                         'app.views_modulo_reportes.obtener_documentos_emitidos',
                         {'fecha_desde': fi_s, 'fecha_hasta': ff_s, 'sucursal_id': 'all'},
                         G, check_documentos_emitidos))
        plan.append(('ventas_internet', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes.obtener_reporte_ventas_internet',
                rango, G)), [])))
        plan.append(('ventas_comparativo', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes.obtener_ventas_comparativo',
                {'tipo_flujo': 'custom', **rango}, G)), [])))
        plan.append(('despachos_tiendas', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes.obtener_reporte_despachos_tiendas',
                {'fecha_desde': fi_s, 'fecha_hasta': ff_s}, G)), [])))
    if R:
        plan.append(_run('ventas_global_fuga',
                         'app.views_modulo_reportes.obtener_ventas_global_por_empresa',
                         rango, R, check_ventas_global, ctx=R))
    if G:
        def pv(oraculos):
            res = invocar_vista(
                'app.views_modulo_reportes.obtener_productos_vendidos',
                {'tipo_flujo': 'custom', **rango, 'top_n': 500}, G)
            checks = check_productos_vendidos(res, oraculos, fx)
            invs = [res]
            if fx.get('cat_padre') and fx.get('cat_hija'):
                rp = invocar_vista(
                    'app.views_modulo_reportes.obtener_productos_vendidos',
                    {'tipo_flujo': 'custom', **rango, 'categoria_id': fx['cat_padre']}, G)
                rh = invocar_vista(
                    'app.views_modulo_reportes.obtener_productos_vendidos',
                    {'tipo_flujo': 'custom', **rango, 'categoria_id': fx['cat_hija']}, G)
                checks += check_pv_filtro_padre(rp, rh, fx)
                invs += [rp, rh]
            return checks, invs
        plan.append(('productos_vendidos', pv))
        plan.append(_run('resumen_existencias',
                         'app.views_resumen_existencias.obtener_resumen_existencias',
                         {'agrupar_por': 'categoria'}, G, check_resumen_existencias))
        if fx.get('marca_top'):
            plan.append(('existencias_marca', lambda o: (
                check_smoke(invocar_vista(
                    'app.views_modulo_reportes.obtener_reporte_existencias_marca',
                    {'marca_id': fx['marca_top'], 'sucursal_id': 'todas'}, G)), [])))
    if T:
        plan.append(_run('existencias_sucursal',
                         'app.views_modulo_reportes.obtener_reporte_existencias_sucursal',
                         {'sucursal_id': T.sucursal_id,
                          **({'marca_id': fx['marca_top']} if fx.get('marca_top') else {})},
                         T, check_existencias_sucursal, ctx=T))

    if G:
        def eg(oraculos):
            base = 'app.views.obtener_existencias_reporte'
            p1 = invocar_vista(base, {'pagina': 1, 'por_pagina': 50,
                                      'sucursal_id': T.sucursal_id if T else ''}, G)
            p2 = invocar_vista(base, {'pagina': 2, 'por_pagina': 50,
                                      'sucursal_id': T.sucursal_id if T else ''}, G)
            pp = invocar_vista(base, {'pagina': 1, 'por_pagina': 50,
                                      'categoria_id': fx.get('cat_padre') or ''}, G)
            return check_existencias_general(p1, p2, pp, fx), [p1, p2, pp]
        plan.append(('existencias_general', eg))
        if fx.get('marca_top'):
            plan.append(_run('movimientos_sucursal',
                             'app.views_modulo_reportes.obtener_reporte_movimientos_sucursal',
                             {'marca_id': fx['marca_top']}, G,
                             check_movimientos_sucursal, ctx=G))
    if R and fx.get('talla_ajena'):
        plan.append(('kardex_fuga', lambda o: (
            check_kardex_fuga(invocar_vista(
                'app.views.reporte_movimientos_kardex',
                {'producto_talla_id': fx['talla_ajena']}, R), fx), [])))
    if G:
        plan.append(('kardex_saldo', lambda o: (check_kardex_saldo(G), [])))
        plan.append(('compras_integral', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes.api_reporte_compras', {}, CDx)), [])))
        plan.append(('productos_origen', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes.api_productos_por_origen',
                {'fecha_desde': fi_s, 'fecha_hasta': ff_s}, G)), [])))
        # recepciones/despachos-detallado ELIMINADOS en Fase C (huérfanos,
        # auditoría 2026-08) — sus smoke checks se retiraron con ellos.
        if not rapido:
            plan.append(('rendimiento_proveedor', lambda o: (
                check_smoke(invocar_vista(
                    'app.views_modulo_reportes.api_reporte_rendimiento_proveedor',
                    {'fecha_desde': fi_s, 'fecha_hasta': ff_s}, G)), [])))
            plan.append(('fifo_general', lambda o: (
                check_smoke(invocar_vista('app.views.reporte_fifo_general', {}, T or G)), [])))
        if fx.get('marca_top'):
            plan.append(('inteligencia_compra', lambda o: (
                check_smoke(invocar_vista(
                    'app.views_inteligencia_compra.obtener_inteligencia_compra',
                    {'marca_id': fx['marca_top'], 'sucursal_id': 'todas'}, G)), [])))
        plan.append(('plan_liquidacion', lambda o: (
            check_smoke(invocar_vista(
                'app.views_inteligencia_compra.obtener_plan_liquidacion', {}, G)), [])))
    # --- guards de la Fase A de fixes (2026-08-20) ---
    if R:
        def dfuga(oraculos):
            emp_r = set(EmpresaUser.objects.filter(user=R.usuario, status=True)
                        .values_list('empresa_id', flat=True))
            ajena = Sucursal.objects.exclude(empresa_id__in=emp_r).first()
            if not ajena:
                return [C('fuga_403', 'skip', '', '', 'sin sucursal ajena')], []
            res = invocar_vista(
                'app.views_modulo_reportes.api_diagnostico_cuadratura_vs_reporte',
                {'fecha': fi_s, 'sucursal_id': ajena.id}, R)
            return [C('fuga_403', res['status'] == 403, '403', str(res['status']),
                      f'cuadratura de sucursal ajena ({ajena.alias}) via querystring')], [res]
        plan.append(('diagnostico_fuga', dfuga))
    if G and not rapido:
        def rc(oraculos):
            res = invocar_vista('app.views_modulo_reportes.api_rendimiento_compras',
                                {'anio': fi.year}, G)
            checks = checks_universales(res, umbral_q=60, umbral_ms=90000)
            js = res.get('json') or {}
            inv = float(((js.get('resumen') or {}).get('total_inversion')) or 0)
            neto = float(Dte.objects.filter(
                tipo_transaccion='COMPRA', fecha_emision__year=fi.year,
                descartado=False,
            ).exclude(estado_dte__in=['ANULADO', 'CANCELADO', 'RECHAZADO'])
             .aggregate(t=Sum('monto_neto'))['t'] or 0)
            ok = neto > 0 and abs(inv - neto) <= 0.35 * neto
            checks.append(C('inversion_sin_apertura', ok,
                            f'±35% de compras DTE ({neto:,.0f})', f'{inv:,.0f}',
                            'la apertura de migración inflaba esto 3x (fix 20-ago)'))
            return checks, [res]
        plan.append(('rendimiento_compras', rc))
    # --- nuevos ago-2026 (auditoría 2026-08-20): los 3 reportes post-julio ---
    if T:
        plan.append(('quiebre_talla', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes_tallas.api_reporte_quiebre_talla',
                {'sucursal_id': T.sucursal_id, 'desde': fi_s, 'hasta': ff_s}, T)), [])))
    if G:
        plan.append(('diferencias_recepcion', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes_diferencias.api_reporte_diferencias_recepcion',
                {'fecha_desde': fi_s, 'fecha_hasta': ff_s}, G)), [])))
        plan.append(('mercaderia_transito', lambda o: (
            check_smoke(invocar_vista(
                'app.views_modulo_reportes_diferencias.api_reporte_mercaderia_transito',
                {'dias': 90}, G)), [])))
    return plan


# ============================================================ MAIN

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--confirmo-prod', action='store_true')
    ap.add_argument('--desde', default='2026-06-01')
    ap.add_argument('--hasta', default='2026-06-30')
    ap.add_argument('--solo', default=None)
    ap.add_argument('--rapido', action='store_true')
    ap.add_argument('--json', dest='json_path', default=None)
    args = ap.parse_args()

    db = settings.DATABASES['default']
    print(f"BD: {db.get('NAME')} @ {db.get('HOST')}")
    if not args.confirmo_prod:
        print('Aborta: este script corre contra la BD del .env (producción). '
              'Relanza con --confirmo-prod para confirmar. NO escribe nada.')
        sys.exit(1)

    settings.DEBUG = True  # habilita connection.queries (solo en memoria)
    fi = datetime.strptime(args.desde, '%Y-%m-%d').date()
    ff = datetime.strptime(args.hasta, '%Y-%m-%d').date()
    print(f'Rango: {fi} → {ff}')

    print('\nResolviendo contextos y fixtures…')
    ctxs = resolver_contextos(fi, ff)
    for c in ctxs.values():
        print(f'  {c!r}')
    fx = resolver_fixtures(fi, ff, ctxs)
    print(f'  fixtures: dia_top={fx.get("dia_top")} vendedor={fx.get("vendedor_top")} '
          f'marca={fx.get("marca_top")} padre={fx.get("cat_padre_nombre")} '
          f'hija={fx.get("cat_hija_nombre")} talla_ajena={fx.get("talla_ajena")}')

    print('\nCalculando oráculos…')
    oraculos = {
        'rango': (fi, ff),
        'ventas_reporte': oraculo_ventas_reporte(fi, ff),
        'docs_emitidos_mes': oraculo_docs_emitidos(fi, ff),
        'productos_vendidos': oraculo_productos_vendidos(fi, ff),
        'censo': oraculo_censo_categorias(),
        'genero': oraculo_genero_atributos(),
        'stock_resumen': (oraculo_stock_resumen(ctxs['GLOBAL'].usuario)
                          if 'GLOBAL' in ctxs else {}),
        'doble_conteo': oraculo_comparativa_doble_conteo(fi, ff),
        'lotes_vs_stock': oraculo_lotes_vs_stock(),
    }
    cs = oraculos['censo']
    print(f"  censo: {cs['pct_prod_en_hijas']}% productos en hijas v1.2, "
          f"{cs['pct_prod_en_planas']}% en planas, {cs['pct_prod_sin_cat']}% sin categoría; "
          f"{cs['n_zz']} _ZZ_, {cs['n_planas_vivas']} planas vivas")
    print(f"  drift lotes↔stock (muestra {oraculos['lotes_vs_stock']['muestra']}): "
          f"{oraculos['lotes_vs_stock']['pct_drift']}%")

    plan = construir_plan(fi, ff, ctxs, fx, args.rapido)
    if args.solo:
        quiero = [s.strip() for s in args.solo.split(',') if s.strip()]
        plan = [(n, f) for n, f in plan if any(q in n for q in quiero)]

    resultados = {}
    for nombre, runner in plan:
        print(f'\n{SEP}\n{nombre}\n{SEP}')
        try:
            checks, invocaciones = runner(oraculos)
        except Exception as e:
            checks, invocaciones = [C('runner_error', False, '', f'{type(e).__name__}: {e}')], []
        for inv in invocaciones:
            if inv.get('escrituras'):
                print('  ¡¡ESCRITURA DETECTADA!! →', inv['escrituras'][:2])
        for ch in checks:
            print(f"  {VERDES[ch['estado']]} {ch['check']:<34} "
                  f"esperado={ch['esperado'][:48]:<50} obtenido={ch['obtenido'][:60]}")
            if ch['detalle']:
                print(f"      ↳ {ch['detalle']}")
        resultados[nombre] = {
            'checks': checks,
            'invocaciones': [{k: v for k, v in inv.items() if k != 'json'}
                             for inv in invocaciones],
        }

    # Resumen final
    print(f'\n{SEP}\nRESUMEN\n{SEP}')
    tot = {'PASS': 0, 'FAIL': 0, 'WARN': 0, 'SKIP': 0}
    for nombre, r in resultados.items():
        estados = [c['estado'] for c in r['checks']]
        for e in estados:
            tot[e] += 1
        fails = [c['check'] for c in r['checks'] if c['estado'] == 'FAIL']
        marca = '✘' if fails else '✔'
        print(f"  {marca} {nombre:<26} PASS={estados.count('PASS')} "
              f"FAIL={estados.count('FAIL')} WARN={estados.count('WARN')}"
              + (f"  → {', '.join(fails)}" if fails else ''))
    print(f"\nTOTAL: {tot['PASS']} PASS · {tot['FAIL']} FAIL · "
          f"{tot['WARN']} WARN · {tot['SKIP']} SKIP")

    salida = {
        'rango': [str(fi), str(ff)],
        'oraculos': {k: {kk: (sorted(vv) if isinstance(vv, set) else vv)
                         for kk, vv in v.items()} if isinstance(v, dict) else v
                     for k, v in oraculos.items()},
        'fixtures': {k: (sorted(v) if isinstance(v, set) else v) for k, v in fx.items()},
        'resultados': resultados,
    }
    if args.json_path:
        with open(args.json_path, 'w', encoding='utf-8') as f:
            json.dump(salida, f, ensure_ascii=False, indent=1, default=str)
        print(f'\nJSON: {args.json_path}')


if __name__ == '__main__':
    main()
