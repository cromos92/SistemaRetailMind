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
from django.db.models import (
    BigIntegerField, Count, F, Max, OuterRef, Q, Subquery, Sum,
)
from django.db.models.functions import Abs, Coalesce, ExtractMonth, ExtractYear
from django.db.utils import ProgrammingError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .constants_kardex import CONCEPTOS_ABASTECIMIENTO, CONCEPTOS_VENTA
from .decorators import requiere_permiso
from .models import (
    AtributoOpcion, CampanaLiquidacionProducto, Categoria, Compras_Producto,
    EmpresaUser, LoteProducto, Movimientos_Producto, Producto, Producto_Talla,
    ProductoAtributoValor, Productos_Atributos, Sucursal,
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
@requiere_permiso('plan_liquidacion', 'puede_ver')
def ver_plan_liquidacion(request):
    return render(request, 'vistas/modulo_reportes/plan_liquidacion.html', {})


# ------------------------------------------ plan de liquidación (v2)
NOMBRE_ATRIBUTO_ESPECIALIDAD = 'Especialidad'
ES_TIENDA_PT = Q(producto__sucursal__es_centro_distribucion=False)
ES_CD_PT = Q(producto__sucursal__es_centro_distribucion=True)
MAX_EXPORT_FILAS = 20000
BUCKETS_ANTIGUEDAD = {
    '0-90': (0, 90), '90-180': (90, 180), '180-365': (180, 365), '365+': (365, None),
}
# Descuento de liquidación sugerido por antigüedad (mismo criterio que
# analisis_inventario_antiguo: 6 meses / 1 año / 2 años -> 15 / 25 / 40 %).
ESCALA_DESCUENTO_LIQUIDACION = [(730, 40), (365, 25), (180, 15)]
PISO_COSTO_FACTOR = 1.1


def _descuento_sugerido(dias):
    """% de descuento de liquidación sugerido según días de antigüedad."""
    if dias is None:
        return 0
    for umbral, pct in ESCALA_DESCUENTO_LIQUIDACION:
        if dias >= umbral:
            return pct
    return 0


def _precio_liq_sugerido(precioventa, costo, dias):
    """Precio de liquidación sugerido (precioventa - descuento sugerido), con
    piso en costo*1.1. Devuelve (precio, descuento_pct)."""
    pct = _descuento_sugerido(dias)
    if not pct:
        return (precioventa or 0), 0
    nuevo = int(round((precioventa or 0) * (1 - pct / 100.0)))
    piso = int((costo or 0) * PISO_COSTO_FACTOR)
    return max(nuevo, piso), pct


def _scope_plan(request):
    """Alcance y filtros GET comunes del plan de liquidación.

    Devuelve (base_pt, mov_base, ctx):
      - base_pt: Producto_Talla con stock>0 en el alcance. Con incluir_cd=1
        entran también las bodegas/CD del usuario (su stock se reporta
        aparte, nunca en los denominadores de rotación/GMROI).
      - mov_base: ventas del kardex SIEMPRE solo en tiendas vendedoras.
      - ctx: filtros parseados + sucursales del usuario.
    """
    sucursales = list(_sucursales_usuario(request))
    tiendas_ids = [s.id for s in sucursales if not s.es_centro_distribucion]
    cd_ids = [s.id for s in sucursales if s.es_centro_distribucion]

    incluir_cd = request.GET.get('incluir_cd') == '1'
    sucursal_id = request.GET.get('sucursal_id') or None
    marca_id = request.GET.get('marca_id') or None
    especialidad_id = request.GET.get('especialidad_id') or None
    categoria_id = request.GET.get('categoria_id') or None

    alcance_ids = tiendas_ids + (cd_ids if incluir_cd else [])
    if sucursal_id:
        # Elegir una bodega explícitamente la incluye aunque incluir_cd=0.
        sid = int(sucursal_id)
        alcance_ids = [sid] if sid in tiendas_ids + cd_ids else []

    base_pt = Producto_Talla.objects.filter(
        stock__gt=0, producto__excluir_de_analitica=False,
        producto__sucursal_id__in=alcance_ids,
    )
    mov_scope = [i for i in alcance_ids if i in tiendas_ids]
    mov_base = Movimientos_Producto.objects.filter(
        estado='COMPLETADO', concepto__in=CONCEPTOS_VENTA,
        ProductoTalla__producto__sucursal_id__in=mov_scope,
    )

    if categoria_id:
        from app.views_modulo_reportes import _expandir_categoria_ids
        cat_scope = _expandir_categoria_ids(categoria_id)
        base_pt = base_pt.filter(producto__categoria_id__in=cat_scope)
        mov_base = mov_base.filter(ProductoTalla__producto__categoria_id__in=cat_scope)
    if marca_id:
        base_pt = base_pt.filter(producto__atributo1_id=marca_id)
        mov_base = mov_base.filter(ProductoTalla__producto__atributo1_id=marca_id)
    if especialidad_id:
        # Subconsulta IN (no join): multi-etiqueta sin duplicar filas.
        prods_esp = ProductoAtributoValor.objects.filter(
            atributo__nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD,
            opcion_id=especialidad_id,
        ).values('producto_id')
        base_pt = base_pt.filter(producto_id__in=prods_esp)
        mov_base = mov_base.filter(ProductoTalla__producto_id__in=prods_esp)

    ctx = {
        'sucursales': sucursales, 'tiendas_ids': tiendas_ids, 'cd_ids': cd_ids,
        'incluir_cd': incluir_cd, 'sucursal_id': sucursal_id,
        'marca_id': marca_id, 'especialidad_id': especialidad_id,
        'categoria_id': categoria_id,
    }
    return base_pt, mov_base, ctx


def _opciones_filtros_plan(ctx):
    """Catálogos para poblar los selects de filtros (solo primera carga)."""
    suc_ids = ctx['tiendas_ids'] + ctx['cd_ids']
    marcas = list(
        AtributoOpcion.objects.filter(
            productos_marca__sucursal_id__in=suc_ids,
            productos_marca__producto_talla__stock__gt=0,
        ).values('id', 'valor').distinct().order_by('valor'))
    categorias = list(
        Categoria.objects.select_related('padre')
        .values('id', 'nombre', 'padre_id', 'padre__nombre')
        .order_by('padre__nombre', 'nombre'))
    especialidades = list(
        AtributoOpcion.objects.filter(
            atributo__nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD,
        ).values('id', 'valor').order_by('valor'))
    sucursales = [{'id': s.id, 'alias': s.alias, 'es_cd': s.es_centro_distribucion}
                  for s in ctx['sucursales']]
    return {'marcas': marcas, 'categorias': categorias,
            'especialidades': especialidades, 'sucursales': sucursales}


def _fila_liquidacion(ir, t, dd):
    """Métricas de una fila del ranking (dimensión agnóstica).

    ir: agregados de inventario; t: ventas TTM 365d; dd: dead-stock 180d.
    Ratios (rotación/cobertura/GMROI) usan SOLO stock/ventas de tiendas; el
    stock CD se exhibe aparte. Fila solo-bodega => acción 'Traspasar'.
    """
    stock = ir.get('stock_u') or 0
    stock_cd = ir.get('stock_cd') or 0
    if stock + stock_cd <= 0:
        return None
    valor_costo = ir.get('valor_costo') or 0
    valor_cd = ir.get('valor_cd') or 0
    skus = ir.get('skus') or 0
    u = t.get('u') or 0
    venta = t.get('venta') or 0
    costo = t.get('costo') or 0
    dead_u = dd.get('dead_u') or 0
    dead_costo = dd.get('dead_costo') or 0
    dead_skus = dd.get('dead_skus') or 0

    rotacion = round(u / stock, 2) if stock else None
    cobertura = round(stock / (u / 12.0), 1) if (u and stock) else None
    margen = (venta - costo) if (venta > 0 and 0 < costo < venta) else None
    gmroi = round(margen / valor_costo, 2) if (margen and valor_costo) else None
    pct_dead = round(100.0 * dead_skus / skus, 1) if skus else 0

    if stock == 0 and stock_cd > 0:
        accion = 'Traspasar'
    elif gmroi is not None and gmroi < 1 and (cobertura is None or cobertura > 12):
        accion = 'Liquidar'
    elif gmroi is not None and gmroi >= 3 and cobertura is not None and cobertura < 6:
        accion = 'Reponer'
    elif pct_dead >= 70:
        accion = 'Depurar'
    else:
        accion = 'Monitorear'

    return {
        'stock': stock, 'stock_cd': stock_cd, 'skus': skus,
        'valor_costo': valor_costo, 'valor_cd': valor_cd, 'ttm_u': u,
        'rotacion': rotacion, 'cobertura': cobertura, 'gmroi': gmroi,
        'dead_u': dead_u, 'dead_costo': dead_costo, 'dead_skus': dead_skus,
        'pct_dead': pct_dead, 'accion': accion,
    }


@require_GET
@requiere_permiso('plan_liquidacion', 'puede_ver')
def obtener_plan_liquidacion(request):
    """Ranking por capital inmovilizado (dead-stock 180d) + GMROI/rotación.

    v2 — dimensiones: marca, categoría hija v1.2 ("Padre › Hijo"),
    especialidad (multi-etiqueta, vista de atribución) y sucursal. Filtros
    GET combinables: categoria_id (un padre incluye su rama), marca_id,
    especialidad_id, sucursal_id, incluir_cd. Ventas/rotación/GMROI se
    calculan SIEMPRE solo con tiendas vendedoras; el stock de bodegas/CD
    (incluir_cd=1) se reporta aparte (stock_cd/valor_cd, acción Traspasar).
    `con_opciones=1` agrega los catálogos para poblar los selects.
    """
    try:
        hoy = timezone.localdate()
        base_pt, mov_base, ctx = _scope_plan(request)

        vend180 = (mov_base.filter(fecha__gte=hoy - timedelta(days=180))
                   .values('ProductoTalla'))

        def _filas_por(campo_id, campos_label, nombre_key, etiquetar,
                       filtro_extra=None, extra_fila=None):
            """Ranking agrupado por un campo id que vive en Producto."""
            pt = base_pt.filter(**filtro_extra) if filtro_extra else base_pt
            v_inv = [f'producto__{campo_id}'] + [f'producto__{l}' for l in campos_label]
            inv = {r[f'producto__{campo_id}']: r for r in
                   pt.values(*v_inv)
                   .annotate(stock_u=Sum('stock', filter=ES_TIENDA_PT, output_field=BI),
                             stock_cd=Sum('stock', filter=ES_CD_PT, output_field=BI),
                             valor_costo=Sum(F('stock') * F('producto__costo'),
                                             filter=ES_TIENDA_PT, output_field=BI),
                             valor_cd=Sum(F('stock') * F('producto__costo'),
                                          filter=ES_CD_PT, output_field=BI),
                             skus=Count('id', filter=ES_TIENDA_PT))}
            mv = (mov_base.filter(fecha__gte=hoy - timedelta(days=365))
                  .values(f'ProductoTalla__producto__{campo_id}')
                  .annotate(u=Sum(Abs('cantidad'), output_field=BI),
                            venta=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI),
                            costo=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI)))
            ttm = {r[f'ProductoTalla__producto__{campo_id}']: r for r in mv}
            dead = {r[f'producto__{campo_id}']: r for r in
                    pt.filter(ES_TIENDA_PT).exclude(id__in=vend180)
                    .values(f'producto__{campo_id}')
                    .annotate(dead_u=Sum('stock', output_field=BI),
                              dead_costo=Sum(F('stock') * F('producto__costo'), output_field=BI),
                              dead_skus=Count('id'))}

            filas = []
            for did, ir in inv.items():
                fila = _fila_liquidacion(ir, ttm.get(did, {}), dead.get(did, {}))
                if fila is None:
                    continue
                fila[nombre_key] = etiquetar(ir)
                fila['id'] = did
                if extra_fila:
                    fila.update(extra_fila(ir))
                filas.append(fila)
            filas.sort(key=lambda x: (-(x['dead_costo'] or 0), -(x['valor_cd'] or 0)))
            return filas

        filas = _filas_por(
            'atributo1_id', ['atributo1__valor'], 'marca',
            lambda ir: ir['producto__atributo1__valor'],
            filtro_extra={'producto__atributo1__isnull': False},
        )

        def _label_cat(ir):
            nombre = ir.get('producto__categoria__nombre') or 'Sin categoría'
            padre = ir.get('producto__categoria__padre__nombre') or ''
            return f'{padre} › {nombre}' if padre else nombre

        filas_cat = _filas_por(
            'categoria_id', ['categoria__nombre', 'categoria__padre__nombre'],
            'categoria', _label_cat,
            filtro_extra={'producto__categoria__isnull': False},
        )

        filas_suc = _filas_por(
            'sucursal_id', ['sucursal__alias', 'sucursal__es_centro_distribucion'],
            'sucursal', lambda ir: ir['producto__sucursal__alias'],
            extra_fila=lambda ir: {
                'es_cd': bool(ir['producto__sucursal__es_centro_distribucion'])},
        )
        filas_suc.sort(key=lambda x: (x.get('es_cd', False),
                                      -(x['dead_costo'] or 0), -(x['valor_cd'] or 0)))

        def _filas_por_especialidad():
            """Dimensión especialidad v1.2 — multi-etiqueta => ATRIBUCIÓN:
            un producto suma en cada especialidad que tiene, así que el
            total de la tabla puede exceder el 100% del inventario."""
            ESP = Q(producto__atributos__atributo__nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD)
            k = 'producto__atributos__opcion_id'
            inv = {r[k]: r for r in
                   base_pt.filter(ESP)
                   .values(k, 'producto__atributos__opcion__valor')
                   .annotate(stock_u=Sum('stock', filter=ES_TIENDA_PT, output_field=BI),
                             stock_cd=Sum('stock', filter=ES_CD_PT, output_field=BI),
                             valor_costo=Sum(F('stock') * F('producto__costo'),
                                             filter=ES_TIENDA_PT, output_field=BI),
                             valor_cd=Sum(F('stock') * F('producto__costo'),
                                          filter=ES_CD_PT, output_field=BI),
                             skus=Count('id', filter=ES_TIENDA_PT))}
            km = 'ProductoTalla__producto__atributos__opcion_id'
            mv = (mov_base.filter(
                      fecha__gte=hoy - timedelta(days=365),
                      ProductoTalla__producto__atributos__atributo__nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD)
                  .values(km)
                  .annotate(u=Sum(Abs('cantidad'), output_field=BI),
                            venta=Sum(Abs(F('cantidad')) * F('precio'), output_field=BI),
                            costo=Sum(Abs(F('cantidad')) * F('costo'), output_field=BI)))
            ttm = {r[km]: r for r in mv}
            dead = {r[k]: r for r in
                    base_pt.filter(ESP & ES_TIENDA_PT).exclude(id__in=vend180)
                    .values(k)
                    .annotate(dead_u=Sum('stock', output_field=BI),
                              dead_costo=Sum(F('stock') * F('producto__costo'), output_field=BI),
                              dead_skus=Count('id'))}
            filas_e = []
            for did, ir in inv.items():
                fila = _fila_liquidacion(ir, ttm.get(did, {}), dead.get(did, {}))
                if fila is None:
                    continue
                fila['especialidad'] = ir['producto__atributos__opcion__valor']
                fila['id'] = did
                filas_e.append(fila)
            filas_e.sort(key=lambda x: (-(x['dead_costo'] or 0), -(x['valor_cd'] or 0)))
            return filas_e

        filas_esp = _filas_por_especialidad()

        # Totales sobre el universo filtrado completo (no solo filas con marca).
        tot = base_pt.aggregate(
            stock_u=Sum('stock', filter=ES_TIENDA_PT, output_field=BI),
            valor_costo=Sum(F('stock') * F('producto__costo'), filter=ES_TIENDA_PT, output_field=BI),
            stock_cd=Sum('stock', filter=ES_CD_PT, output_field=BI),
            valor_cd=Sum(F('stock') * F('producto__costo'), filter=ES_CD_PT, output_field=BI),
        )
        dead_tot = (base_pt.filter(ES_TIENDA_PT).exclude(id__in=vend180)
                    .aggregate(dead_u=Sum('stock', output_field=BI),
                               dead_costo=Sum(F('stock') * F('producto__costo'), output_field=BI)))
        tot_valor = tot['valor_costo'] or 0
        tot_dead_costo = dead_tot['dead_costo'] or 0

        data = {
            'totales': {
                'marcas': len(filas),
                'stock_u': tot['stock_u'] or 0, 'valor_costo': tot_valor,
                'stock_cd': tot['stock_cd'] or 0, 'valor_cd': tot['valor_cd'] or 0,
                'dead_u': dead_tot['dead_u'] or 0, 'dead_costo': tot_dead_costo,
                'dead_pct_valor': round(100.0 * tot_dead_costo / tot_valor, 1) if tot_valor else 0,
                'n_liquidar': sum(1 for f in filas if f['accion'] == 'Liquidar'),
                'n_reponer': sum(1 for f in filas if f['accion'] == 'Reponer'),
            },
            'marcas': filas,
            'categorias': filas_cat,
            'especialidades': filas_esp,
            'sucursales': filas_suc,
            'filtros_aplicados': {
                'categoria_id': ctx['categoria_id'], 'marca_id': ctx['marca_id'],
                'especialidad_id': ctx['especialidad_id'],
                'sucursal_id': ctx['sucursal_id'], 'incluir_cd': ctx['incluir_cd'],
            },
        }
        if request.GET.get('con_opciones') == '1':
            data['opciones'] = _opciones_filtros_plan(ctx)
        return JsonResponse({'success': True, 'data': data})

    except Exception as e:
        logger.exception('Error en plan de liquidación')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# Orden GET -> expresión de order_by en el queryset de Producto anotado.
# 'antiguedad' ordena por la fecha FIFO (más viejo = más días); 'sin_venta'
# por la última venta (venta más vieja / nunca = más días sin vender).
ORDENES_DETALLE = {
    'valor_costo': F('valor_ord'), 'stock_u': F('stock_u'),
    'precioventa': F('precioventa'), 'articulo': F('articulo'),
    'dias_antiguedad': F('aging_dt'), 'dias_sin_venta': F('ultima_venta'),
    'u365': F('u365'),
}


def _detalle_query(base_pt, mov_base, hoy, q=None):
    """Queryset de Producto anotado para el drill-down, paginable en el DB.

    Todo el cálculo pesado (agregados + antigüedad FIFO + última venta) se
    resuelve con anotaciones/Subquery para que Postgres ordene y pagine
    (LIMIT/OFFSET) sin materializar los ~15k productos del catálogo en Python.
    Antigüedad = lote vivo más antiguo; si el producto no tiene lotes (stock
    migrado), `fecha_creacion` (corregida en prod desde el kardex el
    2026-05-20). Ventas siempre solo tiendas (mov_base ya viene acotado).
    """
    prod = Producto.objects.filter(id__in=base_pt.values('producto_id'))
    if q:
        prod = prod.filter(Q(articulo__icontains=q) |
                           Q(descripcion__icontains=q) |
                           Q(atributo1__valor__icontains=q))

    lote_sq = (LoteProducto.objects.filter(
        producto_talla__producto=OuterRef('pk'),
        activo=True, agotado=False, cantidad_disponible__gt=0,
    ).order_by('fecha_ingreso').values('fecha_ingreso')[:1])
    venta_sq = (mov_base.filter(ProductoTalla__producto=OuterRef('pk'))
                .order_by('-fecha').values('fecha')[:1])
    u365_sq = (mov_base.filter(ProductoTalla__producto=OuterRef('pk'),
                               fecha__gte=hoy - timedelta(days=365))
               .values('ProductoTalla__producto')
               .annotate(s=Sum(Abs('cantidad'), output_field=BI)).values('s')[:1])

    stock_f = Q(producto_talla__stock__gt=0)
    return prod.annotate(
        stock_u=Coalesce(Sum('producto_talla__stock', filter=stock_f, output_field=BI), 0),
        tallas=Count('producto_talla', filter=stock_f),
        fecha_lote=Subquery(lote_sq),
        ultima_venta=Subquery(venta_sq),
        u365=Coalesce(Subquery(u365_sq, output_field=BI), 0),
    ).annotate(
        aging_dt=Coalesce('fecha_lote', 'fecha_creacion'),
        valor_ord=F('costo') * F('stock_u'),
    )


def _bucket_filter(qs, bucket, hoy):
    """Aplica el rango de antigüedad al queryset anotado (sobre aging_dt)."""
    if bucket == 'sin-dato':
        return qs.filter(aging_dt__isnull=True)
    rango = BUCKETS_ANTIGUEDAD.get(bucket)
    if not rango:
        return qs
    lo, hi = rango
    # dias in [lo, hi)  <=>  aging_date in (hoy-hi, hoy-lo]
    qs = qs.filter(aging_dt__date__lte=hoy - timedelta(days=lo))
    if hi is not None:
        qs = qs.filter(aging_dt__date__gt=hoy - timedelta(days=hi))
    return qs


def _parse_anios(raw):
    """'2025,2026' -> [2025, 2026] (ignora no-numéricos)."""
    anios = []
    for p in (raw or '').split(','):
        p = p.strip()
        if p.isdigit():
            anios.append(int(p))
    return anios


def _anio_filter(qs, anios):
    """Filtra el queryset del detalle por AÑO(s) de antigüedad (sobre aging_dt)."""
    if not anios:
        return qs
    return qs.annotate(_anio=ExtractYear('aging_dt')).filter(_anio__in=anios)


def _serializar_detalle(qs, hoy):
    """Convierte una página del queryset (ya sliceada) en dicts JSON."""
    filas = list(qs.values(
        'id', 'articulo', 'descripcion', 'atributo1__valor', 'atributo2__valor',
        'categoria__nombre', 'categoria__padre__nombre', 'sucursal_id',
        'sucursal__alias', 'sucursal__es_centro_distribucion', 'precioventa',
        'costo', 'stock_u', 'tallas', 'fecha_lote', 'fecha_creacion',
        'ultima_venta', 'u365'))
    out = []
    for r in filas:
        f = r['fecha_lote'] or r['fecha_creacion']
        fuente = 'lote' if r['fecha_lote'] else ('creacion' if r['fecha_creacion'] else None)
        fecha_fifo = timezone.localtime(f).date() if f else None
        ult = r['ultima_venta']
        cat_n = r['categoria__nombre'] or 'Sin categoría'
        cat_p = r['categoria__padre__nombre'] or ''
        dias = (hoy - fecha_fifo).days if fecha_fifo else None
        precio_liq, pct = _precio_liq_sugerido(r['precioventa'], r['costo'], dias)
        out.append({
            'producto_id': r['id'], 'articulo': r['articulo'],
            'descripcion': r['descripcion'], 'marca': r['atributo1__valor'],
            'color': r['atributo2__valor'],
            'categoria': f'{cat_p} › {cat_n}' if cat_p else cat_n,
            'sucursal_id': r['sucursal_id'], 'sucursal': r['sucursal__alias'],
            'es_cd': bool(r['sucursal__es_centro_distribucion']),
            'tallas': r['tallas'], 'stock_u': r['stock_u'] or 0,
            'valor_costo': (r['stock_u'] or 0) * (r['costo'] or 0),
            'precioventa': r['precioventa'], 'costo': r['costo'],
            'fecha_fifo': fecha_fifo, 'antiguedad_fuente': fuente,
            'anio': fecha_fifo.year if fecha_fifo else None,
            'ultima_venta': ult,
            'dias_sin_venta': (hoy - ult).days if ult else None,
            'u365': r['u365'] or 0,
            'dias_antiguedad': dias,
            'descuento_sugerido': pct,
            'precio_liquidacion': precio_liq,
        })
    return out


@require_GET
@requiere_permiso('plan_liquidacion', 'puede_ver')
def obtener_plan_liquidacion_detalle(request):
    """Drill-down del plan de liquidación a nivel Producto, paginado en el DB.

    GET: filtros de _scope_plan + antiguedad (0-90|90-180|180-365|365+|
    sin-dato), q (artículo/descripción/marca), orden (dias_antiguedad|
    dias_sin_venta|valor_costo|stock_u|u365|precioventa|articulo, prefijo
    '-' = descendente), page, page_size (máx 200).
    """
    try:
        hoy = timezone.localdate()
        base_pt, mov_base, ctx = _scope_plan(request)
        qs = _detalle_query(base_pt, mov_base, hoy, q=request.GET.get('q') or None)

        bucket = request.GET.get('antiguedad') or None
        if bucket:
            qs = _bucket_filter(qs, bucket, hoy)
        qs = _anio_filter(qs, _parse_anios(request.GET.get('anio')))

        orden = request.GET.get('orden') or '-valor_costo'
        rev = orden.startswith('-')
        key = orden.lstrip('-')
        expr = ORDENES_DETALLE.get(key, F('valor_ord'))
        # nulls_last para no llenar la primera página de productos sin dato,
        # salvo dias_sin_venta desc (nunca vendido = más días => va primero).
        if key == 'dias_sin_venta':
            ordering = expr.asc(nulls_first=True) if rev else expr.desc(nulls_last=True)
        else:
            ordering = expr.desc(nulls_last=True) if rev else expr.asc(nulls_last=True)
        qs = qs.order_by(ordering, 'id')

        total = qs.count()
        try:
            page = max(int(request.GET.get('page') or 1), 1)
            page_size = min(max(int(request.GET.get('page_size') or 50), 10), 200)
        except (TypeError, ValueError):
            page, page_size = 1, 50
        ini = (page - 1) * page_size
        pagina = _serializar_detalle(qs[ini:ini + page_size], hoy)

        # Badge "en campaña activa" solo para los productos de la página.
        # Tolerante a que la migración 0188 aún no esté aplicada (si el código
        # se despliega antes de migrar, el reporte sigue funcionando sin badge).
        try:
            en_campana = set(CampanaLiquidacionProducto.objects.filter(
                activo=True, producto_id__in=[f['producto_id'] for f in pagina],
            ).values_list('producto_id', flat=True))
        except ProgrammingError:
            logger.warning('CampanaLiquidacionProducto no existe aún (migración 0188 pendiente)')
            en_campana = set()

        for f in pagina:
            f['fecha_fifo'] = f['fecha_fifo'].strftime('%Y-%m-%d') if f['fecha_fifo'] else None
            f['ultima_venta'] = f['ultima_venta'].strftime('%Y-%m-%d') if f['ultima_venta'] else None
            f['en_campana'] = f['producto_id'] in en_campana

        return JsonResponse({'success': True, 'total': total, 'page': page,
                             'page_size': page_size, 'filas': pagina})
    except Exception as e:
        logger.exception('Error en detalle de plan de liquidación')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _aging_year_expr():
    """ExtractYear de la antigüedad FIFO (lote vivo más antiguo o creación)."""
    lote_sq = (LoteProducto.objects.filter(
        producto_talla__producto=OuterRef('pk'),
        activo=True, agotado=False, cantidad_disponible__gt=0,
    ).order_by('fecha_ingreso').values('fecha_ingreso')[:1])
    return ExtractYear(Coalesce(Subquery(lote_sq), F('fecha_creacion')))


def _anios_liquidacion(base_pt, hoy):
    """Capital + pares por AÑO de antigüedad del stock filtrado."""
    stock_f = Q(producto_talla__stock__gt=0)
    rows = (Producto.objects.filter(id__in=base_pt.values('producto_id'))
            .annotate(anio=_aging_year_expr())
            .values('anio')
            .annotate(
                valor=Coalesce(Sum(F('producto_talla__stock') * F('costo'),
                                   filter=stock_f, output_field=BI), 0),
                pares=Coalesce(Sum('producto_talla__stock', filter=stock_f, output_field=BI), 0),
                productos=Count('id', distinct=True))
            .order_by('anio'))
    anio_actual = hoy.year
    anios = []
    for r in rows:
        if not r['anio']:
            continue
        antig = anio_actual - r['anio']
        anios.append({
            'anio': r['anio'], 'valor': r['valor'] or 0, 'pares': r['pares'] or 0,
            'productos': r['productos'] or 0, 'antiguedad_anios': antig,
            'descuento_sugerido': _descuento_sugerido(antig * 365)})
    return anios


def _dimension_liquidacion(base_pt, hoy, dim='marca', top_n=10):
    """Top items de una dimensión (marca|especialidad) con su capital dividido
    en buckets de urgencia (reciente / 1 año / 2+ años). Especialidad es
    multi-etiqueta (atribución): un producto suma en cada una que tenga.
    """
    stock_f = Q(producto_talla__stock__gt=0)
    qs = (Producto.objects.filter(id__in=base_pt.values('producto_id'))
          .annotate(anio=_aging_year_expr()))
    if dim == 'especialidad':
        qs = qs.filter(atributos__atributo__nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD)
        campo, etiqueta = 'atributos__opcion__valor', 'especialidad'
    else:
        campo, etiqueta = 'atributo1__valor', 'marca'
    rows = list(qs.values(campo, 'anio').annotate(
        valor=Coalesce(Sum(F('producto_talla__stock') * F('costo'),
                           filter=stock_f, output_field=BI), 0),
        pares=Coalesce(Sum('producto_talla__stock', filter=stock_f, output_field=BI), 0)))
    anio_actual = hoy.year
    acc = {}
    for r in rows:
        nombre = r[campo] or f'Sin {etiqueta}'
        anio = r['anio']
        valor, pares = r['valor'] or 0, r['pares'] or 0
        it = acc.setdefault(nombre, {'nombre': nombre, 'val_reciente': 0,
                                     'val_1anio': 0, 'val_2mas': 0, 'valor': 0, 'pares': 0})
        it['valor'] += valor
        it['pares'] += pares
        if anio is None or anio >= anio_actual:
            it['val_reciente'] += valor
        elif anio == anio_actual - 1:
            it['val_1anio'] += valor
        else:
            it['val_2mas'] += valor
    items = sorted(acc.values(), key=lambda x: -x['valor'])
    top = items[:top_n]
    if len(items) > top_n:
        otras = {'nombre': 'Otras', 'val_reciente': 0, 'val_1anio': 0,
                 'val_2mas': 0, 'valor': 0, 'pares': 0}
        for m in items[top_n:]:
            for k in ('val_reciente', 'val_1anio', 'val_2mas', 'valor', 'pares'):
                otras[k] += m[k]
        top.append(otras)
    return top


@require_GET
@login_required
def obtener_plan_liquidacion_por_anio(request):
    """Análisis de antigüedad para los gráficos: capital/pares por año y por
    dimensión (marca|especialidad, param `dim`). `solo=dim` omite el año (para
    el toggle de dimensión, que no recalcula el gráfico de años)."""
    try:
        hoy = timezone.localdate()
        base_pt, mov_base, ctx = _scope_plan(request)
        dim = request.GET.get('dim') or 'marca'
        solo = request.GET.get('solo')
        por_dimension = _dimension_liquidacion(base_pt, hoy, dim=dim)
        if solo == 'dim':
            return JsonResponse({'success': True, 'dim': dim, 'por_dimension': por_dimension})
        anios = _anios_liquidacion(base_pt, hoy)
        total_valor = sum(a['valor'] for a in anios)
        total_pares = sum(a['pares'] for a in anios)
        valor_liquidar = sum(a['valor'] for a in anios if (a['descuento_sugerido'] or 0) > 0)
        pares_liquidar = sum(a['pares'] for a in anios if (a['descuento_sugerido'] or 0) > 0)
        return JsonResponse({'success': True, 'anios': anios, 'dim': dim,
                             'por_dimension': por_dimension, 'anio_actual': hoy.year,
                             'anios_disponibles': [a['anio'] for a in anios],
                             'totales': {'valor': total_valor, 'pares': total_pares,
                                         'valor_liquidar': valor_liquidar,
                                         'pares_liquidar': pares_liquidar}})
    except Exception as e:
        logger.exception('Error en plan de liquidación por año')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@requiere_permiso('plan_liquidacion', 'puede_exportar')
def exportar_plan_liquidacion_excel(request):
    """Excel del drill-down filtrado (mismos GET params que el detalle, sin
    paginación; tope MAX_EXPORT_FILAS). Hoja Detalle + hoja Filtros."""
    try:
        from openpyxl import Workbook

        hoy = timezone.localdate()
        base_pt, mov_base, ctx = _scope_plan(request)
        qs = _detalle_query(base_pt, mov_base, hoy, q=request.GET.get('q') or None)
        bucket = request.GET.get('antiguedad') or None
        if bucket:
            qs = _bucket_filter(qs, bucket, hoy)
        qs = _anio_filter(qs, _parse_anios(request.GET.get('anio')))
        qs = qs.order_by(F('valor_ord').desc(nulls_last=True), 'id')
        truncado = qs.count() > MAX_EXPORT_FILAS
        filas = _serializar_detalle(qs[:MAX_EXPORT_FILAS], hoy)

        esp_por_prod = {}
        for pav in ProductoAtributoValor.objects.filter(
                producto_id__in=[f['producto_id'] for f in filas],
                atributo__nombre__iexact=NOMBRE_ATRIBUTO_ESPECIALIDAD,
        ).values('producto_id', 'opcion__valor'):
            esp_por_prod.setdefault(pav['producto_id'], []).append(pav['opcion__valor'])

        wb = Workbook(write_only=True)
        ws = wb.create_sheet('Detalle')
        # Columnas clave de liquidación primero (año, descuento y precio
        # sugerido) para que la lista sea accionable de un vistazo.
        ws.append(['ID', 'Año', 'Antigüedad (años)', 'Días antigüedad',
                   'Descuento sugerido %', 'Precio venta', 'Precio liquidación sugerido',
                   'Ahorro cliente', 'Sucursal', 'CD', 'Marca', 'Color', 'Categoría',
                   'Especialidades', 'Artículo', 'Descripción', 'Tallas',
                   'Stock (pares)', 'Costo unit.', 'Valor costo',
                   'Fecha ingreso FIFO', 'Fuente antigüedad',
                   'Última venta', 'Días sin venta', 'Ventas 365d (pares)'])
        resumen_anio = {}  # anio -> {pares, valor, valor_liq}
        for f in filas:
            dias = f['dias_antiguedad']
            anio = f['fecha_fifo'].year if f['fecha_fifo'] else None
            antig_anios = (hoy.year - anio) if anio else None
            precio_liq, pct = _precio_liq_sugerido(f['precioventa'], f['costo'], dias)
            ahorro = (f['precioventa'] or 0) - precio_liq
            ws.append([
                f['producto_id'],
                anio if anio else 's/d',
                antig_anios if antig_anios is not None else 's/d',
                dias if dias is not None else 's/d',
                pct, f['precioventa'], precio_liq, ahorro,
                f['sucursal'], 'Sí' if f['es_cd'] else 'No', f['marca'],
                f['color'], f['categoria'],
                ', '.join(esp_por_prod.get(f['producto_id'], [])),
                f['articulo'], f['descripcion'], f['tallas'], f['stock_u'],
                f['costo'], f['valor_costo'],
                f['fecha_fifo'].strftime('%Y-%m-%d') if f['fecha_fifo'] else 's/d',
                f['antiguedad_fuente'] or 's/d',
                f['ultima_venta'].strftime('%Y-%m-%d') if f['ultima_venta'] else 'Nunca',
                f['dias_sin_venta'] if f['dias_sin_venta'] is not None else 'Nunca',
                f['u365'],
            ])
            k = anio if anio else 's/d'
            r = resumen_anio.setdefault(k, {'pares': 0, 'valor': 0, 'valor_liq': 0})
            r['pares'] += f['stock_u'] or 0
            r['valor'] += f['valor_costo'] or 0
            r['valor_liq'] += (precio_liq * (f['stock_u'] or 0)) if pct else 0

        # Hoja Resumen por año (capital y descuento sugerido).
        ws3 = wb.create_sheet('Resumen por año')
        ws3.append(['Año', 'Antigüedad (años)', 'Descuento sugerido %',
                    'Productos (pares)', 'Valor a costo', 'Valor liquidación estimado'])
        for k in sorted(resumen_anio.keys(), key=lambda x: (x == 's/d', x)):
            r = resumen_anio[k]
            antig = (hoy.year - k) if isinstance(k, int) else 's/d'
            pct = _descuento_sugerido(antig * 365 if isinstance(antig, int) else None)
            ws3.append([k, antig, pct, r['pares'], r['valor'],
                        r['valor_liq'] if r['valor_liq'] else '—'])

        ws2 = wb.create_sheet('Filtros')
        ws2.append(['Filtro', 'Valor'])
        ws2.append(['Generado', hoy.strftime('%Y-%m-%d')])
        etiquetas = _etiquetas_filtros(ctx)
        for nombre, valor in etiquetas:
            ws2.append([nombre, valor])
        ws2.append(['Bucket antigüedad', bucket or 'Todos'])
        ws2.append(['Búsqueda', request.GET.get('q') or '—'])
        if truncado:
            ws2.append(['ADVERTENCIA', f'Export truncado a {MAX_EXPORT_FILAS} filas'])

        resp = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = (
            f'attachment; filename=plan_liquidacion_{hoy.strftime("%Y%m%d")}.xlsx')
        wb.save(resp)
        return resp
    except Exception as e:
        logger.exception('Error exportando plan de liquidación')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _leer_ids_archivo(archivo):
    """Extrae los producto_id de la primera columna de un .xlsx/.csv exportado."""
    nombre = (archivo.name or '').lower()
    ids = []
    if nombre.endswith('.csv'):
        import csv
        import io
        txt = archivo.read().decode('utf-8-sig', errors='replace')
        for row in csv.reader(io.StringIO(txt)):
            if row and str(row[0]).strip().isdigit():
                ids.append(int(str(row[0]).strip()))
    else:
        from openpyxl import load_workbook
        wb = load_workbook(archivo, read_only=True, data_only=True)
        for row in wb.active.iter_rows(min_row=2, values_only=True):
            if row and row[0] is not None and str(row[0]).strip().isdigit():
                ids.append(int(str(row[0]).strip()))
    return ids


@require_POST
@login_required
def importar_seleccion_liquidacion(request):
    """Importa un .xlsx/.csv (columna ID = producto_id, como el export) y
    devuelve los producto_ids válidos en las sucursales del usuario, para
    seleccionarlos y armar una campaña de liquidación."""
    try:
        archivo = request.FILES.get('archivo')
        if not archivo:
            return JsonResponse({'success': False, 'error': 'Sube un archivo .xlsx o .csv'}, status=400)
        ids = _leer_ids_archivo(archivo)
        if not ids:
            return JsonResponse({'success': False,
                                 'error': 'No se encontraron IDs en la primera columna (usá el Excel exportado).'}, status=400)
        sucursales = [s.id for s in _sucursales_usuario(request)]
        validos = list(Producto.objects.filter(
            id__in=ids, sucursal_id__in=sucursales).values_list('id', flat=True))
        return JsonResponse({'success': True, 'producto_ids': validos,
                             'importados': len(validos),
                             'no_encontrados': len(set(ids)) - len(validos)})
    except Exception as e:
        logger.exception('Error importando selección de liquidación')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _etiquetas_filtros(ctx):
    """Pares (etiqueta, valor legible) de los filtros aplicados, para la
    hoja Filtros del export."""
    pares = []
    if ctx['sucursal_id']:
        alias = next((s.alias for s in ctx['sucursales']
                      if s.id == int(ctx['sucursal_id'])), ctx['sucursal_id'])
        pares.append(['Sucursal', alias])
    if ctx['marca_id']:
        op = AtributoOpcion.objects.filter(id=ctx['marca_id']).first()
        pares.append(['Marca', op.valor if op else ctx['marca_id']])
    if ctx['categoria_id']:
        cat = Categoria.objects.select_related('padre').filter(id=ctx['categoria_id']).first()
        if cat:
            label = f'{cat.padre.nombre} › {cat.nombre}' if cat.padre else cat.nombre
        else:
            label = ctx['categoria_id']
        pares.append(['Categoría', label])
    if ctx['especialidad_id']:
        op = AtributoOpcion.objects.filter(id=ctx['especialidad_id']).first()
        pares.append(['Especialidad', op.valor if op else ctx['especialidad_id']])
    pares.append(['Incluye bodegas/CD', 'Sí' if ctx['incluir_cd'] else 'No'])
    return pares
