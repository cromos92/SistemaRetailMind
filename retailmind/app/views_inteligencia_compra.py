"""
Inteligencia de Compra — análisis histórico + pronóstico de una marca para
decidir cuánto/qué comprar. 100% SOLO LECTURA sobre el kardex.

Vista nueva del módulo de Reportes (FBV, patrón del proyecto):
  - ver_inteligencia_compra:      página con selector de marca + gráficos ApexCharts
  - obtener_inteligencia_compra:  API JSON con todo el análisis de una marca

Reglas de dominio:
  - Marca = Producto.atributo1 (AtributoOpcion). Excluye excluir_de_analitica=True.
  - Demanda de público = ventas en TIENDAS vendedoras (es_centro_distribucion=False).
    Las BODEGAS/CD (EDEL, IMP, PA00, PAO0, GILD) son distribución/mayorista.
  - Conceptos SIEMPRE desde constants_kardex (no listas inline).
  - Unidades = Σ|cantidad| (robusto ante bugs de signo de la migración).
  - Pronóstico: seasonal-naive con tendencia acotada.
"""
import logging
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import BigIntegerField, Count, F, Max, Sum
from django.db.models.functions import Abs, ExtractMonth, ExtractYear
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .constants_kardex import CONCEPTOS_ABASTECIMIENTO, CONCEPTOS_VENTA
from .models import (
    AtributoOpcion, Compras_Producto, EmpresaUser, Movimientos_Producto,
    Producto, Producto_Talla, Productos_Atributos, Sucursal,
)

logger = logging.getLogger('app')

BI = BigIntegerField()
MESES = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


# ------------------------------------------------------------------ helpers
def _sucursales_usuario(request):
    empresas = EmpresaUser.objects.filter(
        user=request.user, status=True
    ).values_list('empresa_id', flat=True)
    return Sucursal.objects.filter(empresa_id__in=empresas).order_by('alias')


def _talla_norm(t):
    """Normaliza talla a talla EU de calzado. Fuera de escala -> None ('Otros')."""
    if t is None:
        return None
    s = str(t).strip().replace(',', '.')
    try:
        v = float(s)
    except (ValueError, TypeError):
        return None
    if 19 <= v <= 48:
        if abs(v - round(v)) < 0.01:
            return str(int(round(v)))
        return f"{v:.1f}"
    return None


def _talla_key(t):
    try:
        return (0, float(str(t).replace(',', '.')))
    except (ValueError, TypeError):
        return (1, str(t))


def _u(qs):
    return qs.aggregate(u=Sum(Abs('cantidad'), output_field=BI))['u'] or 0


def _um(qs, campo):
    return qs.aggregate(m=Sum(Abs(F('cantidad')) * F(campo), output_field=BI))['m'] or 0


def _mes_siguiente(y, m):
    return (y, m + 1) if m < 12 else (y + 1, 1)


def _mes_anterior(y, m):
    return (y, m - 1) if m > 1 else (y - 1, 12)


def _forecast(serie, hoy):
    """serie: dict {(y,m): u}. Devuelve dict con ttm, yoy, forward y forecast 12m."""
    # índices estacionales: promedio de participación mensual en años completos recientes
    years = sorted({y for (y, _m) in serie})
    shares = {m: [] for m in range(1, 13)}
    for y in years:
        tot = sum(serie.get((y, m), 0) for m in range(1, 13))
        # solo años con los 12 meses con algo de dato y no el año en curso
        if tot > 0 and all((y, m) in serie for m in range(1, 13)) and y < hoy.year:
            for m in range(1, 13):
                shares[m].append(serie.get((y, m), 0) / tot)
    seas = {m: (sum(v) / len(v) if v else 1 / 12) for m, v in shares.items()}
    ssum = sum(seas.values()) or 1
    seas = {m: seas[m] / ssum for m in seas}

    last_y, last_m = _mes_anterior(hoy.year, hoy.month)  # último mes completo

    def ttm_end(y, m):
        tot, yy, mm = 0, y, m
        for _ in range(12):
            tot += serie.get((yy, mm), 0)
            yy, mm = _mes_anterior(yy, mm)
        return tot

    ttm = ttm_end(last_y, last_m)
    py, pm = last_y - 1, last_m
    ttm_prev = ttm_end(py, pm)
    yoy = (ttm / ttm_prev - 1) if ttm_prev else 0.0
    g = max(-0.35, min(0.10, yoy))
    forward = ttm * (1 + g)

    fc = []
    yy, mm = _mes_siguiente(last_y, last_m)
    for _ in range(12):
        base = forward * seas[mm]
        fc.append({'label': f"{MESES[mm]} {yy}", 'y': yy, 'm': mm,
                   'base': round(base), 'low': round(base * 0.85), 'high': round(base * 1.15)})
        yy, mm = _mes_siguiente(yy, mm)

    return {
        'ttm': ttm, 'ttm_prev': ttm_prev, 'yoy': round(yoy * 100, 1),
        'g': round(g * 100, 1), 'forward_annual': round(forward),
        'forecast': fc, 'forecast_total': round(sum(f['base'] for f in fc)),
        'seasonal': [{'mes': MESES[m], 'indice': round(seas[m] * 100, 1)} for m in range(1, 13)],
    }


# ------------------------------------------------------------------ vista
@login_required
def ver_inteligencia_compra(request):
    """Página 'Inteligencia de Compra' con selector de marca."""
    sucursales = list(_sucursales_usuario(request))
    tiendas = [s for s in sucursales if not s.es_centro_distribucion]

    atributo_marca = Productos_Atributos.objects.filter(nombre__icontains='marca').first()
    marcas = []
    if atributo_marca:
        marcas = list(AtributoOpcion.objects.filter(atributo=atributo_marca)
                      .order_by('valor').values('id', 'valor'))

    context = {
        'marcas': marcas,
        'tiendas': tiendas,
        'sucursal_actual_id': request.session.get('idSucursalActual'),
    }
    return render(request, 'vistas/modulo_reportes/inteligencia_compra.html', context)


@require_GET
@login_required
def obtener_inteligencia_compra(request):
    """API: análisis + pronóstico de compra para una marca."""
    try:
        marca_id = request.GET.get('marca_id')
        sucursal_id = request.GET.get('sucursal_id')  # 'todas' o id de tienda
        if not marca_id:
            return JsonResponse({'success': False, 'error': 'Selecciona una marca.'})

        marca = AtributoOpcion.objects.filter(id=marca_id).first()
        if not marca:
            return JsonResponse({'success': False, 'error': 'Marca no encontrada.'})

        sucursales = list(_sucursales_usuario(request))
        all_ids = [s.id for s in sucursales]
        tiendas_ids = [s.id for s in sucursales if not s.es_centro_distribucion]
        bodegas_ids = [s.id for s in sucursales if s.es_centro_distribucion]

        tienda_especifica = None
        if sucursal_id and str(sucursal_id).lower() != 'todas':
            try:
                sid = int(sucursal_id)
                if sid in tiendas_ids:
                    tienda_especifica = sid
            except (ValueError, TypeError):
                pass
        tienda_scope = [tienda_especifica] if tienda_especifica else tiendas_ids

        hoy = timezone.localdate()

        # -------- base querysets (joins, sin materializar IN gigante) --------
        prod_f = {
            'ProductoTalla__producto__atributo1_id': marca_id,
            'ProductoTalla__producto__excluir_de_analitica': False,
            'ProductoTalla__producto__sucursal_id__in': all_ids,
            'estado': 'COMPLETADO',
        }
        movs = Movimientos_Producto.objects.filter(**prod_f)
        ventas = movs.filter(concepto__in=CONCEPTOS_VENTA)
        abast = movs.filter(concepto__in=CONCEPTOS_ABASTECIMIENTO)
        ventas_tienda = ventas.filter(
            ProductoTalla__producto__sucursal__es_centro_distribucion=False,
            ProductoTalla__producto__sucursal_id__in=tienda_scope,
        )
        ventas_bodega = ventas.filter(
            ProductoTalla__producto__sucursal__es_centro_distribucion=True)

        # -------- 1. venta pública por año --------
        va = {r['a']: r for r in ventas_tienda.annotate(a=ExtractYear('fecha')).values('a')
              .annotate(u=Sum(Abs('cantidad'), output_field=BI),
                        monto=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI)).order_by('a')}
        ventas_anual = [{'anio': a, 'unidades': (va[a]['u'] or 0), 'monto': (va[a]['monto'] or 0)}
                        for a in sorted(va)]
        aa = {r['a']: (r['u'] or 0) for r in abast.annotate(a=ExtractYear('fecha')).values('a')
              .annotate(u=Sum(Abs('cantidad'), output_field=BI)).order_by('a')}

        # -------- 2. venta por tienda + distribución bodegas --------
        ventas_por_tienda = [
            {'alias': r['ProductoTalla__producto__sucursal__alias'],
             'unidades': (r['u'] or 0), 'monto': (r['monto'] or 0)}
            for r in ventas_tienda.values('ProductoTalla__producto__sucursal__alias')
            .annotate(u=Sum(Abs('cantidad'), output_field=BI),
                      monto=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI)).order_by('-u')]
        distribucion_bodega = [
            {'alias': r['ProductoTalla__producto__sucursal__alias'], 'unidades': (r['u'] or 0)}
            for r in ventas_bodega.values('ProductoTalla__producto__sucursal__alias')
            .annotate(u=Sum(Abs('cantidad'), output_field=BI)).order_by('-u')]

        # -------- 3. stock actual --------
        pt = Producto_Talla.objects.filter(
            producto__atributo1_id=marca_id, producto__excluir_de_analitica=False,
            producto__sucursal_id__in=all_ids, stock__gt=0)
        stock_suc = [
            {'alias': r['producto__sucursal__alias'],
             'tipo': 'Bodega' if r['producto__sucursal__es_centro_distribucion'] else 'Tienda',
             'stock': (r['u'] or 0), 'skus': r['skus']}
            for r in pt.values('producto__sucursal__alias', 'producto__sucursal__es_centro_distribucion')
            .annotate(u=Sum('stock', output_field=BI), skus=Count('id')).order_by('-u')]
        stock_tiendas = pt.filter(producto__sucursal__es_centro_distribucion=False,
                                  producto__sucursal_id__in=tienda_scope
                                  ).aggregate(s=Sum('stock', output_field=BI))['s'] or 0

        # -------- 4. serie mensual venta pública (para gráfico + pronóstico) --------
        serie = {}
        for r in (ventas_tienda.annotate(a=ExtractYear('fecha'), m=ExtractMonth('fecha'))
                  .values('a', 'm').annotate(u=Sum(Abs('cantidad'), output_field=BI)).order_by('a', 'm')):
            serie[(r['a'], r['m'])] = r['u'] or 0
        # serie continua últimos 36 meses para el chart
        serie_chart = []
        yy, mm = hoy.year, hoy.month
        tmp = []
        for _ in range(36):
            tmp.append({'label': f"{MESES[mm]} {str(yy)[2:]}", 'u': serie.get((yy, mm), 0)})
            yy, mm = _mes_anterior(yy, mm)
        serie_chart = list(reversed(tmp))

        fc = _forecast(serie, hoy)

        # -------- 5. sell-through anual --------
        sellthrough = []
        for a in sorted(set(va) | set(aa)):
            v = (va.get(a, {}) or {}).get('u') or 0
            i = aa.get(a, 0)
            sellthrough.append({'anio': a, 'vendido': v, 'ingresado': i,
                                'str': round(100.0 * v / i, 1) if i else None})

        # -------- 6. velocidad 90d --------
        def vend(desde, hasta):
            return _u(ventas_tienda.filter(fecha__gte=desde, fecha__lt=hasta))
        v90 = vend(hoy - timedelta(days=90), hoy)
        v90_ly = vend(hoy - timedelta(days=455), hoy - timedelta(days=365))

        # -------- 7. curva de tallas (EU normalizada, demanda reciente 24m) --------
        venta_talla, stock_talla = {}, {}
        ventas_curva = ventas_tienda.filter(fecha__gte=hoy - timedelta(days=730))
        if not ventas_curva.exists():
            ventas_curva = ventas_tienda  # fallback: marca sin venta reciente → all-time
        for r in (ventas_curva.values('ProductoTalla__talla')
                  .annotate(u=Sum(Abs('cantidad'), output_field=BI))):
            t = _talla_norm(r['ProductoTalla__talla'])
            if t:
                venta_talla[t] = venta_talla.get(t, 0) + (r['u'] or 0)
        for r in (pt.filter(producto__sucursal__es_centro_distribucion=False,
                            producto__sucursal_id__in=tienda_scope)
                  .values('talla').annotate(u=Sum('stock', output_field=BI))):
            t = _talla_norm(r['talla'])
            if t:
                stock_talla[t] = stock_talla.get(t, 0) + (r['u'] or 0)
        tot_v = sum(venta_talla.values()) or 1
        tot_s = sum(stock_talla.values()) or 1
        curva = []
        for t in sorted(set(venta_talla) | set(stock_talla), key=_talla_key):
            vp = 100.0 * venta_talla.get(t, 0) / tot_v
            sp = 100.0 * stock_talla.get(t, 0) / tot_s
            gap = vp - sp
            flag = 'quebrada' if (vp >= 3 and stock_talla.get(t, 0) == 0) else (
                'sobre_stock' if gap <= -1.0 else ('gap' if gap >= 1.0 else 'ok'))
            curva.append({'talla': t, 'venta_u': venta_talla.get(t, 0), 'venta_pct': round(vp, 1),
                          'stock_u': stock_talla.get(t, 0), 'stock_pct': round(sp, 1),
                          'gap': round(gap, 1), 'flag': flag})

        # -------- 8. recomendación --------
        forward = fc['forward_annual']
        mensual = forward / 12 if forward else 0
        cobertura = round(stock_tiendas / mensual, 1) if mensual else None
        season = forward / 2  # ~6 meses
        gross_need = max(0, season - stock_tiendas)
        newness = round(season * 0.25)
        comprar = int(round(max(gross_need, newness)))

        if cobertura is None:
            veredicto = 'Sin datos de venta suficientes para proyectar.'
        elif cobertura > 12:
            veredicto = (f'Sobre-stock ({cobertura} meses de cobertura). NO reposición amplia: '
                         f'comprar solo frescura + relleno de tallas núcleo.')
        elif cobertura > 6:
            veredicto = f'Cobertura holgada ({cobertura} meses). Compra moderada, muy enfocada en curva.'
        elif cobertura >= 3:
            veredicto = f'Cobertura sana ({cobertura} meses). Reponer según curva de demanda.'
        else:
            veredicto = f'Riesgo de quiebre ({cobertura} meses). Reponer con prioridad.'
            comprar = int(round(max(gross_need, forward * 0.75 - stock_tiendas, newness)))

        # curva de compra: reparte por demanda, excluye tallas sobre-stockeadas
        pesos = {c['talla']: c['venta_u'] for c in curva if c['flag'] != 'sobre_stock' and c['venta_u'] > 0}
        suma_pesos = sum(pesos.values()) or 1
        curva_compra = sorted(
            [{'talla': t, 'pct': round(100.0 * w / suma_pesos, 1), 'pares': int(round(comprar * w / suma_pesos))}
             for t, w in pesos.items()],
            key=lambda x: _talla_key(x['talla']))
        evitar = [c['talla'] for c in curva if c['flag'] == 'sobre_stock']

        # asignación por tienda (por participación de venta pública en scope)
        base_alloc = ventas_por_tienda if ventas_por_tienda else []
        suma_alloc = sum(t['unidades'] for t in base_alloc) or 1
        asignacion = [{'alias': t['alias'],
                       'pct': round(100.0 * t['unidades'] / suma_alloc, 1),
                       'pares': int(round(comprar * t['unidades'] / suma_alloc))}
                      for t in base_alloc]

        tot_pub = sum(x['unidades'] for x in ventas_anual)
        tot_bod = sum(x['unidades'] for x in distribucion_bodega)

        # -------- 9. finanzas: valor inventario, margen, rotación, WOS, GMROI --------
        pt_tienda = pt.filter(producto__sucursal__es_centro_distribucion=False,
                              producto__sucursal_id__in=tienda_scope)
        inv_costo = pt_tienda.aggregate(
            c=Sum(F('stock') * F('producto__costo'), output_field=BI))['c'] or 0
        inv_precio = pt_tienda.aggregate(
            p=Sum(F('stock') * F('producto__precioventa'), output_field=BI))['p'] or 0

        d12 = hoy - timedelta(days=365)
        tv = ventas_tienda.filter(fecha__gte=d12)
        ttm_venta_m = _um(tv, 'precio')
        ttm_costo_m = _um(tv, 'costo')
        # margen: realizado si el costo viene en las ventas; si no, margen de lista del inventario
        if ttm_venta_m > 0 and 0 < ttm_costo_m < ttm_venta_m:
            margen_pct = round(100.0 * (ttm_venta_m - ttm_costo_m) / ttm_venta_m, 1)
            margen_src = 'realizado'
            margen_anual = ttm_venta_m - ttm_costo_m
        elif inv_precio > 0:
            margen_pct = round(100.0 * (inv_precio - inv_costo) / inv_precio, 1)
            margen_src = 'lista'
            margen_anual = ttm_venta_m * margen_pct / 100.0
        else:
            margen_pct, margen_src, margen_anual = None, None, 0
        rotacion = round(12.0 / cobertura, 2) if cobertura else None
        wos = round(cobertura * 4.345, 1) if cobertura else None
        gmroi = round(margen_anual / inv_costo, 2) if inv_costo else None

        # -------- 10. dead stock / antigüedad (SKUs con stock sin venta reciente) --------
        skus_total = pt_tienda.count()
        vend90 = ventas_tienda.filter(fecha__gte=hoy - timedelta(days=90)).values('ProductoTalla')
        vend180 = ventas_tienda.filter(fecha__gte=hoy - timedelta(days=180)).values('ProductoTalla')
        dead90 = pt_tienda.exclude(id__in=vend90)
        dead180 = pt_tienda.exclude(id__in=vend180)
        dead90_n = dead90.count()
        dead180_n = dead180.count()
        dead180_u = dead180.aggregate(s=Sum('stock', output_field=BI))['s'] or 0
        dead180_costo = dead180.aggregate(
            c=Sum(F('stock') * F('producto__costo'), output_field=BI))['c'] or 0

        # -------- 11. clasificación ABC (motor de predicción, snapshot batch) --------
        abc = None
        try:
            from .models.predicciones import ClasificacionABC
            abc_qs = ClasificacionABC.objects.filter(articulo__atributo1_id=marca_id)
            max_anio = abc_qs.aggregate(x=Max('anio'))['x']
            if max_anio:
                abc_qs = abc_qs.filter(anio=max_anio)
                dist = {r['clasificacion_abc']: r['n'] for r in
                        abc_qs.values('clasificacion_abc').annotate(n=Count('articulo', distinct=True))}
                fecha_abc = abc_qs.aggregate(x=Max('fecha_calculo'))['x']
                abc = {'A': dist.get('A', 0), 'B': dist.get('B', 0), 'C': dist.get('C', 0),
                       'anio': max_anio,
                       'fecha': fecha_abc.strftime('%Y-%m-%d') if fecha_abc else None}
        except Exception:
            logger.warning('ABC no disponible para marca %s', marca_id)

        # -------- 12. lead time (proveedor dominante de la marca) --------
        lead = {'dias': 21, 'proveedor': None, 'fuente': 'default'}
        try:
            articulos = list(Producto.objects.filter(atributo1_id=marca_id)
                             .values_list('articulo', flat=True).distinct()[:500])
            if articulos:
                top_prov = (Compras_Producto.objects.filter(nombre__in=articulos)
                            .values('compras__empresa__nombre', 'compras__empresa__lead_time_dias')
                            .annotate(n=Count('id')).order_by('-n').first())
                if top_prov and top_prov['compras__empresa__lead_time_dias']:
                    lead = {'dias': top_prov['compras__empresa__lead_time_dias'],
                            'proveedor': top_prov['compras__empresa__nombre'], 'fuente': 'proveedor'}
        except Exception:
            logger.warning('Lead time no disponible para marca %s', marca_id)

        # -------- 13. liquidación (dado el sobre-stock, cuánto/qué liberar) --------
        tallas_sobre = [c['talla'] for c in curva if c['flag'] == 'sobre_stock']
        if cobertura and cobertura > 12:
            liq_texto = (f'Liberar capital: {dead180_n:,} SKUs sin venta en 180 días '
                         f'({dead180_u:,} pares, ${dead180_costo:,} a costo) son candidatos a liquidación/markdown.')
        elif dead180_u > 0:
            liq_texto = (f'{dead180_n:,} SKUs sin venta 180d ({dead180_u:,} pares) para depurar; '
                         f'el resto rota bien.')
        else:
            liq_texto = 'Inventario sano: sin dead-stock relevante.'
        liquidacion = {'skus': dead180_n, 'unidades': dead180_u, 'valor_costo': dead180_costo,
                       'tallas_sobre': tallas_sobre, 'texto': liq_texto}

        data = {
            'marca': marca.valor,
            'scope': 'Todas las tiendas' if not tienda_especifica else
                     next((s.alias for s in sucursales if s.id == tienda_especifica), ''),
            'kpis': {
                'venta_publica_hist': tot_pub,
                'distribucion_bodega': tot_bod,
                'stock_tiendas': stock_tiendas,
                'vel_dia': round(v90 / 90, 1),
                'vel_yoy': round((v90 / v90_ly - 1) * 100, 1) if v90_ly else None,
                'pronostico_12m': fc['forecast_total'],
                'cobertura_meses': cobertura,
            },
            'finanzas': {
                'inv_costo': inv_costo, 'inv_precio': inv_precio,
                'margen_pct': margen_pct, 'margen_src': margen_src,
                'rotacion': rotacion, 'wos': wos, 'gmroi': gmroi,
            },
            'salud': {
                'skus_total': skus_total, 'dead90_n': dead90_n, 'dead180_n': dead180_n,
                'dead180_u': dead180_u, 'dead180_costo': dead180_costo,
                'pct_dead180': round(100.0 * dead180_n / skus_total, 1) if skus_total else None,
                'abc': abc, 'lead_time': lead,
            },
            'liquidacion': liquidacion,
            'ventas_anual': ventas_anual,
            'ventas_por_tienda': ventas_por_tienda,
            'distribucion_bodega': distribucion_bodega,
            'stock_suc': stock_suc,
            'serie_chart': serie_chart,
            'forecast': fc,
            'sellthrough': sellthrough,
            'curva': curva,
            'recomendacion': {
                'veredicto': veredicto, 'comprar': comprar, 'cobertura_meses': cobertura,
                'ttm': fc['ttm'], 'yoy': fc['yoy'], 'lead_dias': lead['dias'],
                'curva_compra': curva_compra, 'evitar_tallas': evitar, 'asignacion': asignacion,
            },
        }
        return JsonResponse({'success': True, 'data': data})

    except Exception as e:
        logger.exception('Error en inteligencia de compra')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ══════════════════════════════════════════════════════════════
#  PLAN DE LIQUIDACIÓN — ranking consolidado de TODAS las marcas
# ══════════════════════════════════════════════════════════════
@login_required
def ver_plan_liquidacion(request):
    return render(request, 'vistas/modulo_reportes/plan_liquidacion.html', {})


@require_GET
@login_required
def obtener_plan_liquidacion(request):
    """Ranking de marcas por capital inmovilizado (dead-stock 180d) + GMROI/rotación."""
    try:
        sucursales = list(_sucursales_usuario(request))
        tiendas_ids = [s.id for s in sucursales if not s.es_centro_distribucion]
        hoy = timezone.localdate()

        base_pt = Producto_Talla.objects.filter(
            stock__gt=0, producto__excluir_de_analitica=False,
            producto__sucursal__es_centro_distribucion=False,
            producto__sucursal_id__in=tiendas_ids,
            producto__atributo1__isnull=False,
        )
        # inventario por marca
        inv = {r['producto__atributo1_id']: r for r in
               base_pt.values('producto__atributo1_id', 'producto__atributo1__valor')
               .annotate(stock_u=Sum('stock', output_field=BI),
                         valor_costo=Sum(F('stock') * F('producto__costo'), output_field=BI),
                         skus=Count('id'))}

        # ventas TTM (12m) por marca
        mv = (Movimientos_Producto.objects.filter(
                estado='COMPLETADO', concepto__in=CONCEPTOS_VENTA,
                fecha__gte=hoy - timedelta(days=365),
                ProductoTalla__producto__sucursal__es_centro_distribucion=False,
                ProductoTalla__producto__sucursal_id__in=tiendas_ids)
              .values('ProductoTalla__producto__atributo1_id')
              .annotate(u=Sum(Abs('cantidad'), output_field=BI),
                        venta=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI),
                        costo=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI)))
        ttm = {r['ProductoTalla__producto__atributo1_id']: r for r in mv}

        # dead-stock 180d por marca
        vend180 = (Movimientos_Producto.objects.filter(
                    estado='COMPLETADO', concepto__in=CONCEPTOS_VENTA,
                    fecha__gte=hoy - timedelta(days=180),
                    ProductoTalla__producto__sucursal__es_centro_distribucion=False,
                    ProductoTalla__producto__sucursal_id__in=tiendas_ids)
                   .values('ProductoTalla'))
        dead = {r['producto__atributo1_id']: r for r in
                base_pt.exclude(id__in=vend180).values('producto__atributo1_id')
                .annotate(dead_u=Sum('stock', output_field=BI),
                          dead_costo=Sum(F('stock') * F('producto__costo'), output_field=BI),
                          dead_skus=Count('id'))}

        filas = []
        for mid, ir in inv.items():
            stock = ir['stock_u'] or 0
            if stock <= 0:
                continue
            valor_costo = ir['valor_costo'] or 0
            skus = ir['skus'] or 0
            t = ttm.get(mid, {})
            u = t.get('u') or 0
            venta = t.get('venta') or 0
            costo = t.get('costo') or 0
            dd = dead.get(mid, {})
            dead_u = dd.get('dead_u') or 0
            dead_costo = dd.get('dead_costo') or 0
            dead_skus = dd.get('dead_skus') or 0

            rotacion = round(u / stock, 2) if stock else None
            cobertura = round(stock / (u / 12.0), 1) if u else None
            margen = (venta - costo) if (venta > 0 and 0 < costo < venta) else None
            gmroi = round(margen / valor_costo, 2) if (margen and valor_costo) else None
            pct_dead = round(100.0 * dead_skus / skus, 1) if skus else 0

            if gmroi is not None and gmroi < 1 and (cobertura is None or cobertura > 12):
                accion = 'Liquidar'
            elif gmroi is not None and gmroi >= 3 and cobertura is not None and cobertura < 6:
                accion = 'Reponer'
            elif pct_dead >= 70:
                accion = 'Depurar'
            else:
                accion = 'Monitorear'

            filas.append({
                'marca': ir['producto__atributo1__valor'], 'stock': stock, 'skus': skus,
                'valor_costo': valor_costo, 'ttm_u': u, 'rotacion': rotacion,
                'cobertura': cobertura, 'gmroi': gmroi, 'dead_u': dead_u,
                'dead_costo': dead_costo, 'dead_skus': dead_skus, 'pct_dead': pct_dead,
                'accion': accion,
            })

        filas.sort(key=lambda x: -x['dead_costo'])

        tot_valor = sum(f['valor_costo'] for f in filas)
        tot_dead_costo = sum(f['dead_costo'] for f in filas)
        tot_dead_u = sum(f['dead_u'] for f in filas)
        tot_stock = sum(f['stock'] for f in filas)
        n_liquidar = sum(1 for f in filas if f['accion'] == 'Liquidar')
        n_reponer = sum(1 for f in filas if f['accion'] == 'Reponer')

        data = {
            'totales': {
                'marcas': len(filas), 'stock_u': tot_stock, 'valor_costo': tot_valor,
                'dead_u': tot_dead_u, 'dead_costo': tot_dead_costo,
                'dead_pct_valor': round(100.0 * tot_dead_costo / tot_valor, 1) if tot_valor else 0,
                'n_liquidar': n_liquidar, 'n_reponer': n_reponer,
            },
            'marcas': filas,
        }
        return JsonResponse({'success': True, 'data': data})

    except Exception as e:
        logger.exception('Error en plan de liquidación')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
