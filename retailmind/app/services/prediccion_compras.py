"""
Motor predictivo de compras — RetailMind v3

Pipeline:  corrección censura → ABC-XYZ → velocidades históricas
         → curvas de talle → predicción demanda → sugerencias de compra
         → alertas de velocidad → alertas de quiebre de talle

Dependencias: numpy (percentiles, CV, regresión).
"""
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
from django.db.models import Sum, F, Count, Q
from django.db.models.functions import Abs
from django.utils import timezone

from ..models.catalogo import Producto, Producto_Talla, AtributoOpcion, Categoria
from ..models.ventas import Ticket_Productos, Ticket
from ..models.inventario import Movimientos_Producto
from ..models.compras import Compras, Compras_Producto_Talla

CONCEPTOS_VENTA = ['VENTA_PUBLICO', 'VENTA_MAYORISTA', 'VENTA_DIRECTA', 'VENTA_TICKET']
TIPOS_SUCURSAL_VENTA = ['VENDEDORA', 'MIXTA']

from ..models.predicciones import (
    ConfiguracionPrediccion,
    ClasificacionABC,
    VelocidadHistorica,
    CurvaTalles,
    PrediccionDemanda,
    SugerenciaCompra,
    AlertaVelocidad,
    AlertaQuiebreTalle,
    PendienteReevaluacion,
    StockInicialTemporada,
)

logger = logging.getLogger('app')


# ──────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────

def _get_marca_nombre(producto):
    if producto.atributo1 and producto.atributo1.valor:
        return producto.atributo1.valor
    return ''


def _get_genero_nombre(producto):
    if producto.atributo3 and producto.atributo3.valor:
        return producto.atributo3.valor
    return ''


def _get_categoria_nombre(producto):
    if producto.categoria:
        cat = producto.categoria
        if cat.padre:
            return cat.padre.nombre
        return cat.nombre
    return ''


def _get_subcategoria_nombre(producto):
    if producto.categoria and producto.categoria.padre:
        return producto.categoria.nombre
    return ''


def _get_proveedor_empresa(producto):
    """Intenta obtener la Empresa proveedora más reciente para este producto."""
    from ..models.compras import Compras_Producto
    cp = Compras_Producto.objects.filter(
        nombre=producto.articulo,
    ).select_related('compras__empresa').order_by('-fecha').first()
    if cp:
        return cp.compras.empresa
    return None


_lead_time_cache = {}

def _calcular_lead_time_real(empresa):
    """
    Calcula lead time promedio real de un proveedor.
    Cadena de fallback:
      1) OCs completadas con fecha_envio y fecha_entrega_real
      2) Movimientos RECEPCION_COMPRA migrados (estima desde primer ingreso
         del producto hasta la recepcion)
      3) empresa.lead_time_dias (default configurado)
      4) 21 dias hardcoded
    """
    if empresa is None:
        return 21
    if empresa.id in _lead_time_cache:
        return _lead_time_cache[empresa.id]

    from ..models.compras import Compras as ComprasModel
    from django.db.models import Avg

    resultado = ComprasModel.objects.filter(
        empresa=empresa,
        estado='COMPLETADA',
        fecha_envio_proveedor__isnull=False,
        fecha_entrega_real__isnull=False,
    ).annotate(
        dias=F('fecha_entrega_real') - F('fecha_envio_proveedor'),
    ).aggregate(avg_dias=Avg('dias'))

    avg = resultado.get('avg_dias')
    if avg is not None:
        dias = avg.days if hasattr(avg, 'days') else int(avg)
        lead_time = max(1, dias)
        _lead_time_cache[empresa.id] = lead_time
        return lead_time

    from ..models.compras import Productos_Recepcionados
    from django.db.models.functions import TruncDate
    recep_avg = Productos_Recepcionados.objects.filter(
        compra_producto_talla__compra_producto__compras__empresa=empresa,
        fecha_recepcion__isnull=False,
        compra_producto_talla__compra_producto__compras__fecha__isnull=False,
    ).annotate(
        fecha_recep_date=TruncDate('fecha_recepcion'),
    ).annotate(
        dias=F('fecha_recep_date') - F('compra_producto_talla__compra_producto__compras__fecha'),
    ).aggregate(avg_dias=Avg('dias')).get('avg_dias')

    if recep_avg is not None:
        dias = recep_avg.days if hasattr(recep_avg, 'days') else int(recep_avg)
        if 1 <= dias <= 180:
            _lead_time_cache[empresa.id] = dias
            return dias

    lead_time = empresa.lead_time_dias if empresa.lead_time_dias else 21
    _lead_time_cache[empresa.id] = lead_time
    return lead_time


def _semanas_entre(fecha_inicio, fecha_fin):
    if not fecha_inicio or not fecha_fin:
        return 0
    delta = (fecha_fin - fecha_inicio).days
    return max(Decimal(str(delta)) / 7, Decimal('0.01'))


# ──────────────────────────────────────────────────────────────
#  1. Corrección de demanda censurada
# ──────────────────────────────────────────────────────────────

def _obtener_ventas_producto(producto):
    """Obtiene unidades vendidas desde Movimientos_Producto + Ticket_Productos."""
    ventas_mov = Movimientos_Producto.objects.filter(
        ProductoTalla__producto=producto,
        concepto__in=CONCEPTOS_VENTA,
    ).aggregate(total=Sum(Abs(F('cantidad'))))['total'] or 0

    ventas_ticket = Ticket_Productos.objects.filter(
        ProductoTalla__producto=producto,
        idTicket__estado='PAGADO',
    ).aggregate(total=Sum('stock'))['total'] or 0

    return max(ventas_mov, ventas_ticket)


def corregir_demanda_censurada(producto, semanas_activo):
    """
    Cuando stock llega a 0 antes de fin de temporada, las ventas
    registradas subestiman la demanda real.

    Usa curva de decaimiento logarítmico en vez de extrapolación lineal:
    en retail la demanda real decrece a medida que avanza la temporada
    (pico al inicio, cola al final). Extrapolar linealmente sobreestima.

    Modelo: demanda_restante = velocidad_observada × factor_decaimiento × semanas_restantes
    donde factor_decaimiento = ln(1 + semanas_activo) / ln(1 + semanas_esperadas)

    Returns: (demanda_estimada, factor_correccion, es_censurada)
    """
    stock_actual = sum(
        t.stock for t in producto.producto_talla.all()
    )
    semanas_esperadas = producto.semanas_temporada or 8

    ventas = _obtener_ventas_producto(producto)

    se_agoto = stock_actual <= 0
    if se_agoto and semanas_activo > 0 and semanas_activo < semanas_esperadas:
        velocidad_observada = ventas / float(semanas_activo)
        semanas_restantes = semanas_esperadas - semanas_activo

        # Factor de decaimiento: la demanda en semanas posteriores es menor
        # que en las primeras semanas. Usamos log para modelar la curva
        # descendente típica de retail por temporada.
        import math
        factor_decaimiento = math.log(1 + semanas_activo) / math.log(1 + semanas_esperadas)
        demanda_restante = velocidad_observada * factor_decaimiento * semanas_restantes
        demanda_estimada = int(ventas + max(0, demanda_restante))

        factor = demanda_estimada / max(ventas, 1)
        return demanda_estimada, round(factor, 4), True

    return ventas, 1.0, False


# ──────────────────────────────────────────────────────────────
#  2. Clasificación ABC-XYZ
# ──────────────────────────────────────────────────────────────

def calcular_clasificacion_abc_xyz(temporada=None, anio=None, sucursal_id=None):
    """
    Clasifica todos los productos en ABC (ingreso) y XYZ (variabilidad).
    Solo considera productos de sucursales vendedoras/mixtas.
    """
    config = ConfiguracionPrediccion.get_config()
    fecha_inicio = timezone.localdate() - timedelta(days=config.dias_historico_analisis)

    filtros_mov = Q(
        concepto__in=CONCEPTOS_VENTA,
        fecha__gte=fecha_inicio,
        ProductoTalla__producto__sucursal__tipo_sucursal__in=TIPOS_SUCURSAL_VENTA,
    )
    if sucursal_id:
        filtros_mov &= Q(sucursal_origen_id=sucursal_id)

    ventas_por_producto = Movimientos_Producto.objects.filter(
        filtros_mov
    ).values('ProductoTalla__producto_id').annotate(
        ingreso_total=Sum(F('precio') * Abs(F('cantidad'))),
        unidades_total=Sum(Abs(F('cantidad'))),
    ).order_by('-ingreso_total')

    if not ventas_por_producto.exists():
        filtros_ticket = Q(
            idTicket__estado='PAGADO',
            idTicket__fecha__gte=fecha_inicio,
            ProductoTalla__producto__sucursal__tipo_sucursal__in=TIPOS_SUCURSAL_VENTA,
        )
        if sucursal_id:
            filtros_ticket &= Q(idTicket__sucursal_id=sucursal_id)
        ventas_por_producto = Ticket_Productos.objects.filter(
            filtros_ticket
        ).values('ProductoTalla__producto_id').annotate(
            ingreso_total=Sum(F('precio') * F('stock')),
            unidades_total=Sum('stock'),
        ).order_by('-ingreso_total')

    if not ventas_por_producto:
        logger.warning("Sin datos de ventas para clasificación ABC-XYZ")
        return 0

    productos_map = {
        p.id: p for p in Producto.objects.filter(
            id__in=[v['ProductoTalla__producto_id'] for v in ventas_por_producto]
        ).select_related('atributo1', 'atributo3', 'categoria', 'categoria__padre')
    }

    ingreso_total_global = sum(v['ingreso_total'] or 0 for v in ventas_por_producto)
    if ingreso_total_global == 0:
        return 0

    temp = temporada or ''
    yr = anio or timezone.localdate().year

    acumulado = 0
    clasificaciones = []

    for venta in ventas_por_producto:
        prod_id = venta['ProductoTalla__producto_id']
        producto = productos_map.get(prod_id)
        if not producto:
            continue

        ingreso = venta['ingreso_total'] or 0
        unidades = venta['unidades_total'] or 0
        acumulado += ingreso
        pct_acumulado = acumulado / ingreso_total_global

        if pct_acumulado <= 0.80:
            abc = 'A'
        elif pct_acumulado <= 0.95:
            abc = 'B'
        else:
            abc = 'C'

        ventas_semanales = _calcular_ventas_semanales(prod_id, fecha_inicio)
        if len(ventas_semanales) > 1:
            media = np.mean(ventas_semanales)
            std = np.std(ventas_semanales)
            cv = float(std / media) if media > 0 else 99
        else:
            cv = 0

        # Clasificación XYZ mejorada con detección de demanda intermitente.
        # Usa el framework Syntetos-Boylan: ADI (Average Demand Interval)
        # para distinguir entre demanda errática (Z por CV alto pero continua)
        # y demanda intermitente (Z por muchos ceros). Esto es crítico
        # para retail con miles de SKUs y cantidades pequeñas.
        es_intermitente, adi, cv2 = _es_demanda_intermitente(ventas_semanales)

        if not es_intermitente and cv < float(config.umbral_cv_x):
            xyz = 'X'  # Suave: demanda estable y continua
        elif not es_intermitente and cv < float(config.umbral_cv_y):
            xyz = 'Y'  # Moderada: algo variable pero continua
        else:
            xyz = 'Z'  # Errática o intermitente

        sit = StockInicialTemporada.objects.filter(
            producto=producto, temporada=producto.temporada or temp,
        ).first()
        semanas_activo = 0
        if sit:
            semanas_activo = float(_semanas_entre(sit.fecha_primer_ingreso, timezone.localdate()))

        demanda_corr, factor, censurada = corregir_demanda_censurada(producto, semanas_activo)

        clasificaciones.append(ClasificacionABC(
            articulo=producto,
            temporada=producto.temporada or temp,
            anio=producto.anio_temporada or yr,
            clasificacion_abc=abc,
            clasificacion_xyz=xyz,
            ventas_totales_periodo=unidades,
            porcentaje_acumulado=round(pct_acumulado, 4),
            coeficiente_variacion=round(cv, 4),
            demanda_censurada=censurada,
            factor_correccion_censura=factor,
            ventas_corregidas=demanda_corr,
        ))

    temporadas_usadas = set(c.temporada for c in clasificaciones)
    anios_usados = set(c.anio for c in clasificaciones)
    # Borrar solo los pares (temporada, anio) exactos generados en este run,
    # no el producto cartesiano de todos los valores.
    pares_abc = set(zip(temporadas_usadas, anios_usados))
    filtro_del = Q()
    for temp, anio in pares_abc:
        filtro_del |= Q(temporada=temp, anio=anio)
    if filtro_del:
        ClasificacionABC.objects.filter(filtro_del).delete()
    ClasificacionABC.objects.bulk_create(clasificaciones, batch_size=500)

    logger.info("Clasificación ABC-XYZ: %d productos clasificados", len(clasificaciones))
    return len(clasificaciones)


def _raw_ventas_semanales(producto_id, fecha_inicio=None):
    """
    Obtiene ventas semanales raw (lista de dicts {yr, semana, total}).
    Si fecha_inicio es None busca en toda la historia.
    """
    from django.db.models.functions import ExtractWeek, ExtractYear
    filtro = Q(ProductoTalla__producto_id=producto_id, concepto__in=CONCEPTOS_VENTA)
    if fecha_inicio:
        filtro &= Q(fecha__gte=fecha_inicio)

    datos = list(
        Movimientos_Producto.objects.filter(filtro)
        .annotate(semana=ExtractWeek('fecha'), yr=ExtractYear('fecha'))
        .values('semana', 'yr')
        .annotate(total=Sum(Abs(F('cantidad'))))
        .order_by('yr', 'semana')
    )
    if not datos:
        filtro_t = Q(
            ProductoTalla__producto_id=producto_id,
            idTicket__estado='PAGADO',
        )
        if fecha_inicio:
            filtro_t &= Q(idTicket__fecha__gte=fecha_inicio)
        datos = list(
            Ticket_Productos.objects.filter(filtro_t)
            .annotate(semana=ExtractWeek('idTicket__fecha'), yr=ExtractYear('idTicket__fecha'))
            .values('semana', 'yr')
            .annotate(total=Sum('stock'))
            .order_by('yr', 'semana')
        )
    return datos


def _filtrar_brecha_inactiva(datos_semanales):
    """
    Detecta y elimina la brecha de inactividad (ej. 2021-2024) entre
    periodos con actividad de venta real. Solo quita ceros que estan
    en un hueco entre dos periodos activos, no ceros en los bordes.
    """
    if len(datos_semanales) < 4:
        return datos_semanales

    activos = [(d['yr'], d['semana']) for d in datos_semanales if d['total'] and d['total'] > 0]
    if len(activos) < 2:
        return datos_semanales

    anios_activos = sorted(set(a[0] for a in activos))
    if len(anios_activos) < 2:
        return datos_semanales

    brecha_anios = set()
    for i in range(len(anios_activos) - 1):
        gap = anios_activos[i + 1] - anios_activos[i]
        if gap > 1:
            for yr in range(anios_activos[i] + 1, anios_activos[i + 1]):
                brecha_anios.add(yr)

    if not brecha_anios:
        return datos_semanales

    return [d for d in datos_semanales if d['yr'] not in brecha_anios]


def _calcular_ventas_semanales(producto_id, fecha_inicio):
    """
    Calcula ventas semanales con soporte para datos historicos migrados.
    Estrategia:
      1) Buscar en ventana reciente (fecha_inicio). Si >=8 semanas -> usar.
      2) Si <8, extender a toda la historia y filtrar la brecha inactiva.
    """
    datos_recientes = _raw_ventas_semanales(producto_id, fecha_inicio)
    semanas_con_datos = [d for d in datos_recientes if d['total'] and d['total'] > 0]

    if len(semanas_con_datos) >= 8:
        return [d['total'] for d in datos_recientes]

    datos_completos = _raw_ventas_semanales(producto_id, fecha_inicio=None)
    if not datos_completos:
        return [d['total'] for d in datos_recientes] if datos_recientes else []

    datos_filtrados = _filtrar_brecha_inactiva(datos_completos)
    semanas_filtradas = [d for d in datos_filtrados if d['total'] and d['total'] > 0]

    if len(semanas_filtradas) > len(semanas_con_datos):
        return [d['total'] for d in datos_filtrados]

    return [d['total'] for d in datos_recientes] if datos_recientes else []


# ──────────────────────────────────────────────────────────────
#  3. Velocidad histórica
# ──────────────────────────────────────────────────────────────

def calcular_velocidades_historicas():
    """
    Calcula benchmarks de rotación por grupo (marca, categoría, género, temporada).
    Solo productos de sucursales vendedoras/mixtas.
    """
    productos = Producto.objects.filter(
        temporada__isnull=False,
        sucursal__tipo_sucursal__in=TIPOS_SUCURSAL_VENTA,
    ).select_related('atributo1', 'atributo3', 'categoria', 'categoria__padre')

    grupos = defaultdict(list)

    for producto in productos:
        marca_id = producto.atributo1_id
        cat = _get_categoria_nombre(producto) or 'SIN_CATEGORIA'
        subcat = _get_subcategoria_nombre(producto)
        genero = _get_genero_nombre(producto) or 'SIN_GENERO'
        temp = producto.temporada or ''

        sit = StockInicialTemporada.objects.filter(
            producto=producto, temporada=temp,
        ).first()
        if not sit or not sit.stock_inicial:
            continue

        semanas_activo = float(_semanas_entre(sit.fecha_primer_ingreso, timezone.localdate()))
        if semanas_activo < 1:
            continue

        demanda_corr, factor, censurada = corregir_demanda_censurada(producto, semanas_activo)
        if demanda_corr <= 0:
            continue

        velocidad_sem = demanda_corr / semanas_activo
        sellthrough = demanda_corr / sit.stock_inicial if sit.stock_inicial > 0 else 0

        key = (marca_id, cat, subcat, genero, temp)
        grupos[key].append({
            'velocidad_sem': velocidad_sem,
            'sellthrough': sellthrough,
            'semanas_para_agotar': sit.stock_inicial / velocidad_sem if velocidad_sem > 0 else 999,
            'censurada': censurada,
        })

    VelocidadHistorica.objects.all().delete()
    registros = []

    for (marca_id, cat, subcat, genero, temp), items in grupos.items():
        if len(items) < 2:
            continue

        semanas_arr = np.array([i['semanas_para_agotar'] for i in items])
        sellthrough_arr = np.array([i['sellthrough'] for i in items])

        cap = 999999.99
        registros.append(VelocidadHistorica(
            marca_id=marca_id,
            categoria=cat,
            subcategoria=subcat,
            genero=genero,
            temporada=temp,
            velocidad_semanas_p25=min(round(float(np.percentile(semanas_arr, 25)), 2), cap),
            velocidad_semanas_p50=min(round(float(np.percentile(semanas_arr, 50)), 2), cap),
            velocidad_semanas_p75=min(round(float(np.percentile(semanas_arr, 75)), 2), cap),
            sellthrough_p50=min(round(float(np.percentile(sellthrough_arr, 50)), 4), 9.9999),
            total_articulos_base=len(items),
            articulos_censurados=sum(1 for i in items if i['censurada']),
        ))

    VelocidadHistorica.objects.bulk_create(registros, batch_size=500)
    logger.info("Velocidades históricas: %d grupos calculados", len(registros))
    return len(registros)


# ──────────────────────────────────────────────────────────────
#  4. Curva de talles
# ──────────────────────────────────────────────────────────────

def calcular_curvas_talles():
    """Distribución % de ventas por talle para cada grupo (solo vendedoras)."""
    config = ConfiguracionPrediccion.get_config()
    fecha_inicio = timezone.localdate() - timedelta(days=config.dias_historico_analisis)

    ventas = Movimientos_Producto.objects.filter(
        concepto__in=CONCEPTOS_VENTA,
        fecha__gte=fecha_inicio,
        ProductoTalla__producto__sucursal__tipo_sucursal__in=TIPOS_SUCURSAL_VENTA,
    ).select_related(
        'ProductoTalla__producto__atributo1',
        'ProductoTalla__producto__atributo3',
        'ProductoTalla__producto__categoria',
        'ProductoTalla__producto__categoria__padre',
    )

    grupos = defaultdict(lambda: defaultdict(int))
    totales_grupo = defaultdict(int)

    for venta in ventas.iterator(chunk_size=2000):
        pt = venta.ProductoTalla
        if not pt:
            continue
        producto = pt.producto
        marca_id = producto.atributo1_id
        cat = _get_categoria_nombre(producto) or 'SIN_CATEGORIA'
        genero = _get_genero_nombre(producto) or 'SIN_GENERO'
        subcat = _get_subcategoria_nombre(producto)

        talle = pt.talla
        cant = abs(venta.cantidad)
        key = (marca_id, cat, subcat, genero)
        grupos[key][talle] += cant
        totales_grupo[key] += cant

    CurvaTalles.objects.all().delete()
    registros = []

    for (marca_id, cat, subcat, genero), talles in grupos.items():
        total = totales_grupo[(marca_id, cat, subcat, genero)]
        if total < 10:
            continue
        for talle, cantidad in talles.items():
            registros.append(CurvaTalles(
                marca_id=marca_id,
                categoria=cat,
                subcategoria=subcat,
                genero=genero,
                talle=talle,
                porcentaje=round(cantidad / total, 4),
                total_ventas_base=total,
            ))

    CurvaTalles.objects.bulk_create(registros, batch_size=500)
    logger.info("Curvas de talles: %d registros", len(registros))
    return len(registros)


# ──────────────────────────────────────────────────────────────
#  5. Predicción de demanda (con métodos estadísticos reales)
# ──────────────────────────────────────────────────────────────

def _es_demanda_intermitente(serie):
    """
    Detecta demanda intermitente usando ADI (Average Demand Interval).
    ADI > 1.32 = intermitente (Syntetos & Boylan, 2005).
    También calcula CV² de los períodos con demanda.
    Returns: (es_intermitente, adi, cv2_demanda)
    """
    if len(serie) < 4:
        return True, 99.0, 99.0

    periodos_con_demanda = [v for v in serie if v > 0]
    if len(periodos_con_demanda) < 2:
        return True, 99.0, 99.0

    # ADI = total períodos / número de períodos con demanda
    adi = len(serie) / len(periodos_con_demanda)

    # CV² de las cantidades cuando hay demanda
    media_demanda = np.mean(periodos_con_demanda)
    std_demanda = np.std(periodos_con_demanda)
    cv2 = (std_demanda / media_demanda) ** 2 if media_demanda > 0 else 99.0

    # Clasificación Syntetos-Boylan:
    #   ADI <= 1.32 y CV² <= 0.49 → Suave (smooth) → métodos clásicos OK
    #   ADI >  1.32 y CV² <= 0.49 → Intermitente → Croston/SBA
    #   ADI <= 1.32 y CV² >  0.49 → Errática → Croston/SBA
    #   ADI >  1.32 y CV² >  0.49 → Lumpy → SBA preferido
    es_intermitente = adi > 1.32 or cv2 > 0.49
    return es_intermitente, round(adi, 2), round(cv2, 4)


def _predecir_croston_sba(serie, semanas_restantes):
    """
    Croston's method con ajuste SBA (Syntetos-Boylan Approximation).
    Diseñado para demanda intermitente: muchos ceros intercalados
    con cantidades pequeñas.

    Descompone en:
      - z_t: tamaño promedio de demanda (cuando ocurre)
      - p_t: intervalo promedio entre demandas
    Forecast = (z_t / p_t) × factor_sba
    """
    alpha = 0.15  # suavizado conservador para datos escasos

    # Extraer intervalos entre demandas y cantidades
    intervalos = []
    cantidades = []
    periodo_desde_ultima = 0

    for valor in serie:
        periodo_desde_ultima += 1
        if valor > 0:
            intervalos.append(periodo_desde_ultima)
            cantidades.append(float(valor))
            periodo_desde_ultima = 0

    if len(cantidades) < 2:
        # Insuficientes eventos de demanda — fallback a promedio simple
        total = float(np.sum(serie))
        if total <= 0:
            return max(1, int(semanas_restantes))  # mínimo 1 por temporada
        promedio_sem = total / len(serie)
        return max(1, int(promedio_sem * semanas_restantes))

    # Inicializar con promedios
    z_t = float(np.mean(cantidades))
    p_t = float(np.mean(intervalos))

    # Suavizado exponencial de Croston
    for i in range(len(cantidades)):
        z_t = alpha * cantidades[i] + (1 - alpha) * z_t
        p_t = alpha * intervalos[i] + (1 - alpha) * p_t

    # Factor SBA (Syntetos-Boylan): corrige el sesgo de Croston
    # SBA = Croston × (1 - alpha/2)
    factor_sba = 1.0 - alpha / 2.0

    # Forecast por período
    if p_t > 0:
        forecast_por_semana = (z_t / p_t) * factor_sba
    else:
        forecast_por_semana = z_t * factor_sba

    return max(1, int(forecast_por_semana * semanas_restantes))


def _predecir_holt_winters(serie, semanas_restantes):
    """Holt-Winters additive trend. Requires >=16 data points."""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        model = ExponentialSmoothing(
            serie, trend='add', seasonal=None, initialization_method='estimated',
        ).fit(optimized=True)
        forecast = model.forecast(steps=int(semanas_restantes))
        return max(0, int(np.sum(np.maximum(forecast, 0))))
    except Exception:
        return None


def _predecir_wma(serie, semanas_restantes, alpha=0.3):
    """Weighted Moving Average with exponential decay weights."""
    n = len(serie)
    pesos = np.array([alpha * (1 - alpha) ** i for i in range(n - 1, -1, -1)])
    pesos /= pesos.sum()
    promedio_ponderado = float(np.dot(serie, pesos))
    return max(0, int(promedio_ponderado * semanas_restantes))


def _predecir_regresion(serie, semanas_restantes):
    """Linear regression on weekly data, extrapolate forward."""
    x = np.arange(len(serie), dtype=float)
    coefs = np.polyfit(x, serie, 1)
    x_futuro = np.arange(len(serie), len(serie) + int(semanas_restantes))
    forecast = np.polyval(coefs, x_futuro)
    return max(0, int(np.sum(np.maximum(forecast, 0))))


def _calcular_confianza(serie, metodo='clasico'):
    """
    Confianza mejorada según tipo de demanda y método.

    Para demanda clásica: MAPE backtest (últimas 2 semanas).
    Para demanda intermitente: basado en frecuencia de demanda + estabilidad.
    Retorna confianza entre 0.10 y 0.95.
    """
    if len(serie) < 4:
        return Decimal('0.20')

    if metodo == 'croston_sba':
        periodos_con_demanda = sum(1 for v in serie if v > 0)
        ratio_actividad = periodos_con_demanda / len(serie)

        # Más períodos con demanda = más confiable el patrón
        confianza_base = 0.25 + (ratio_actividad * 0.40)

        # Bonus por consistencia en cantidades
        cantidades = [v for v in serie if v > 0]
        if len(cantidades) >= 3:
            cv_cantidades = float(np.std(cantidades) / np.mean(cantidades))
            if cv_cantidades < 0.5:
                confianza_base += 0.10  # cantidades estables
            elif cv_cantidades > 1.5:
                confianza_base -= 0.05  # cantidades muy erráticas

        # Bonus por cantidad de observaciones
        if len(serie) >= 16:
            confianza_base += 0.05
        elif len(serie) >= 8:
            confianza_base += 0.02

        confianza = max(0.15, min(0.85, confianza_base))
        return Decimal(str(round(confianza, 2)))

    # Método clásico: MAPE backtest
    if len(serie) < 6:
        return Decimal('0.40')
    train = serie[:-2]
    actual = serie[-2:]
    actual_sum = float(np.sum(actual))
    if actual_sum <= 0:
        return Decimal('0.40')

    promedio_train = float(np.mean(train))
    pred_sum = promedio_train * 2
    mape = abs(actual_sum - pred_sum) / actual_sum
    confianza = max(0.10, min(0.95, 1.0 - mape))
    return Decimal(str(round(confianza, 2)))


def calcular_predicciones_demanda(temporada=None, anio=None):
    """
    Selección automática de método según tipo de demanda y datos disponibles.

    Primero detecta si la demanda es intermitente (ADI > 1.32 o CV² > 0.49):
      → Intermitente con >=4 sem datos: Croston/SBA
      → Continua >=16 sem: Holt-Winters
      → Continua 8-15 sem: WMA
      → Continua 4-7 sem: Regresión lineal
      → <4 sem con historial de grupo: Punto de reorden dinámico
      → Sin datos: Fallback por velocidad de grupo

    Solo productos de sucursales vendedoras/mixtas.
    """
    config = ConfiguracionPrediccion.get_config()
    temp = temporada or ''
    yr = anio or timezone.localdate().year
    fecha_inicio_hist = timezone.localdate() - timedelta(days=config.dias_historico_analisis)

    productos = Producto.objects.filter(
        temporada__isnull=False,
        sucursal__tipo_sucursal__in=TIPOS_SUCURSAL_VENTA,
    ).select_related('atributo1', 'atributo3', 'categoria', 'categoria__padre')

    predicciones = []

    for producto in productos:
        semanas_temp = producto.semanas_temporada or 8

        sit = StockInicialTemporada.objects.filter(
            producto=producto, temporada=producto.temporada or temp,
        ).first()

        serie_semanal = _calcular_ventas_semanales(producto.id, fecha_inicio_hist)
        n_semanas = len(serie_semanal)

        semanas_activo = 0
        if sit and sit.fecha_primer_ingreso:
            semanas_activo = float(_semanas_entre(sit.fecha_primer_ingreso, timezone.localdate()))

        semanas_restantes = max(1, semanas_temp - semanas_activo)
        demanda_corr = 0
        factor = 1.0
        censurada = False
        metodo = 'similitud'
        confianza = Decimal('0.20')
        articulos_usados = 0

        # --- Paso 1: Detectar tipo de demanda ---
        es_intermitente = False
        if n_semanas >= 4:
            es_intermitente, adi, cv2 = _es_demanda_intermitente(serie_semanal)

        # --- Paso 2: Seleccionar método según tipo de demanda ---
        if es_intermitente and n_semanas >= 4:
            # DEMANDA INTERMITENTE: Croston/SBA es el método correcto.
            # Los métodos clásicos (HW, WMA, regresión) asumen demanda
            # continua y fallan con muchos ceros intercalados.
            serie = np.array(serie_semanal, dtype=float)
            demanda_corr = _predecir_croston_sba(serie, semanas_restantes)
            metodo = 'croston_sba'
            confianza = _calcular_confianza(serie, metodo='croston_sba')

        elif n_semanas >= 16:
            serie = np.array(serie_semanal, dtype=float)
            pred = _predecir_holt_winters(serie, semanas_restantes)
            if pred is not None:
                demanda_corr = pred
                metodo = 'holt_winters'
                confianza = _calcular_confianza(serie)
            else:
                demanda_corr = _predecir_wma(serie, semanas_restantes)
                metodo = 'promedio_movil_ponderado'
                confianza = _calcular_confianza(serie)

        elif n_semanas >= 8:
            serie = np.array(serie_semanal, dtype=float)
            demanda_corr = _predecir_wma(serie, semanas_restantes)
            metodo = 'promedio_movil_ponderado'
            confianza = _calcular_confianza(serie)

        elif n_semanas >= 4:
            serie = np.array(serie_semanal, dtype=float)
            demanda_corr = _predecir_regresion(serie, semanas_restantes)
            metodo = 'regresion_lineal'
            confianza = _calcular_confianza(serie)

        elif sit and sit.stock_inicial > 0 and semanas_activo > 0:
            demanda_corr_raw, factor, censurada = corregir_demanda_censurada(
                producto, semanas_activo,
            )
            demanda_corr = demanda_corr_raw
            metodo = 'demanda_observada'
            confianza = min(Decimal('0.50'), Decimal(str(
                round(semanas_activo / max(semanas_temp, 1), 2)
            )))
        else:
            vel = _buscar_velocidad_historica(producto)
            if vel:
                demanda_corr = int(float(vel.velocidad_semanas_p50) * semanas_temp)
                metodo = 'similitud'
                confianza = Decimal('0.35')
                articulos_usados = vel.total_articulos_base
            else:
                demanda_corr = semanas_temp * 5
                metodo = 'similitud'
                confianza = Decimal('0.15')

        abc_factor = _factor_seguridad_abc(producto, config)
        demanda_final = int(demanda_corr * float(abc_factor))

        predicciones.append(PrediccionDemanda(
            articulo=producto,
            temporada=producto.temporada or temp,
            anio=producto.anio_temporada or yr,
            unidades_predichas=demanda_final,
            unidades_sin_correccion=int(demanda_corr / max(factor, 0.01)),
            factor_censura_aplicado=factor,
            metodo_usado=metodo,
            confianza=min(confianza, Decimal('1.00')),
            articulos_similares_usados=articulos_usados,
        ))

    temporadas_pred = set(p.temporada for p in predicciones)
    anios_pred = set(p.anio for p in predicciones)
    if temporadas_pred:
        pares_pred = set(zip(temporadas_pred, anios_pred))
        filtro_del_pred = Q()
        for temp, anio in pares_pred:
            filtro_del_pred |= Q(temporada=temp, anio=anio)
        PrediccionDemanda.objects.filter(filtro_del_pred).delete()
    PrediccionDemanda.objects.bulk_create(predicciones, batch_size=500)
    logger.info("Predicciones de demanda: %d generadas", len(predicciones))
    return len(predicciones)


def _buscar_velocidad_historica(producto):
    cat = _get_categoria_nombre(producto)
    genero = _get_genero_nombre(producto)
    temp = producto.temporada or ''

    vel = VelocidadHistorica.objects.filter(
        marca_id=producto.atributo1_id,
        categoria=cat,
        genero=genero,
        temporada=temp,
    ).first()
    if vel:
        return vel

    vel = VelocidadHistorica.objects.filter(
        categoria=cat,
        genero=genero,
        temporada=temp,
    ).first()
    return vel


def _factor_seguridad_abc(producto, config):
    abc = ClasificacionABC.objects.filter(articulo=producto).order_by('-fecha_calculo').first()
    if not abc:
        return config.factor_seguridad_clase_b
    if abc.clasificacion_abc == 'A':
        return config.factor_seguridad_clase_a
    elif abc.clasificacion_abc == 'B':
        return config.factor_seguridad_clase_b
    return config.factor_seguridad_clase_c


# ──────────────────────────────────────────────────────────────
#  6. Sugerencias de compra
# ──────────────────────────────────────────────────────────────

def generar_sugerencias_compra(temporada=None, anio=None):
    """
    Para cada predicción, distribuye por talle usando CurvaTalles
    y descuenta stock actual + en tránsito.
    """
    filtro = Q()
    if temporada:
        filtro &= Q(temporada=temporada)
    if anio:
        filtro &= Q(anio=anio)

    predicciones = PrediccionDemanda.objects.filter(
        filtro
    ).select_related('articulo', 'articulo__atributo1', 'articulo__atributo3',
                     'articulo__categoria', 'articulo__categoria__padre')

    sugerencias = []

    for pred in predicciones:
        producto = pred.articulo
        talles = Producto_Talla.objects.filter(producto=producto)
        if not talles.exists():
            continue

        curva = _obtener_curva_talles(producto)
        total_predicho = pred.unidades_predichas

        for pt in talles:
            pct = curva.get(pt.talla, Decimal('0'))
            if not pct and len(talles) > 0:
                pct = Decimal('1') / len(talles)

            unidades_sugeridas = int(float(total_predicho) * float(pct))
            stock_actual = max(0, pt.stock or 0)

            transito = Compras_Producto_Talla.objects.filter(
                compra_producto__compras__estado='ACTIVA',
                producto_talla=pt,
            ).aggregate(
                total=Sum(F('stock') - F('unidades_recibidas'))
            )['total'] or 0
            transito = max(0, transito)

            a_pedir = max(0, unidades_sugeridas - stock_actual - transito)

            if a_pedir > 0:
                sugerencias.append(SugerenciaCompra(
                    prediccion=pred,
                    articulo_talle=pt,
                    unidades_sugeridas=unidades_sugeridas,
                    stock_actual=stock_actual,
                    unidades_en_transito=transito,
                    unidades_a_pedir=a_pedir,
                    origen='prediccion',
                ))

    temporadas_sug = set(s.prediccion.temporada for s in sugerencias if s.prediccion)
    anios_sug = set(s.prediccion.anio for s in sugerencias if s.prediccion)
    if temporadas_sug:
        pares_sug = set(zip(temporadas_sug, anios_sug))
        filtro_del_sug = Q()
        for temp, anio in pares_sug:
            filtro_del_sug |= Q(prediccion__temporada=temp, prediccion__anio=anio)
        SugerenciaCompra.objects.filter(
            filtro_del_sug,
            origen='prediccion',
            aprobada=False,
        ).delete()
    SugerenciaCompra.objects.bulk_create(sugerencias, batch_size=500)
    logger.info("Sugerencias de compra: %d generadas", len(sugerencias))
    return len(sugerencias)


def _obtener_curva_talles(producto):
    cat = _get_categoria_nombre(producto) or 'SIN_CATEGORIA'
    genero = _get_genero_nombre(producto) or 'SIN_GENERO'

    curvas = CurvaTalles.objects.filter(
        marca_id=producto.atributo1_id,
        categoria=cat,
        genero=genero,
    )
    if not curvas.exists():
        curvas = CurvaTalles.objects.filter(
            categoria=cat,
            genero=genero,
        )

    return {c.talle: c.porcentaje for c in curvas}


# ──────────────────────────────────────────────────────────────
#  7. Alertas de velocidad (punto de reorden dinámico)
# ──────────────────────────────────────────────────────────────

def evaluar_alertas_velocidad(producto_ids=None):
    """
    Detecta artículos que rotan más rápido de lo normal.
    Solo productos de sucursales vendedoras/mixtas.
    """
    config = ConfiguracionPrediccion.get_config()

    filtro = Q(
        temporada__isnull=False,
        producto__sucursal__tipo_sucursal__in=TIPOS_SUCURSAL_VENTA,
    )
    if producto_ids:
        filtro &= Q(producto__id__in=producto_ids)

    sits = StockInicialTemporada.objects.filter(filtro).select_related(
        'producto', 'producto__atributo1', 'producto__atributo3',
        'producto__categoria', 'producto__categoria__padre',
        'producto__sucursal',
    )

    alertas = []

    for sit in sits:
        producto = sit.producto
        semanas_activo = float(_semanas_entre(sit.fecha_primer_ingreso, timezone.localdate()))

        if semanas_activo < float(config.semanas_minimas_para_alerta):
            continue

        ventas_acum = _obtener_ventas_producto(producto)

        stock_restante = sum(t.stock for t in producto.producto_talla.all())
        if stock_restante <= 0:
            continue

        velocidad_real = ventas_acum / semanas_activo if semanas_activo > 0 else 0
        if velocidad_real <= 0:
            continue

        vel_hist = _buscar_velocidad_historica(producto)

        # Convertir velocidad histórica al mismo dominio que velocidad_real (unidades/semana).
        # velocidad_semanas_p50 = semanas medianas para agotar stock = stock_inicial / (u/sem).
        # Invirtiendo: velocidad_normal_u_sem = stock_inicial / p50_semanas.
        if vel_hist and float(vel_hist.velocidad_semanas_p50) > 0:
            velocidad_normal = sit.stock_inicial / float(vel_hist.velocidad_semanas_p50)
        else:
            # fallback: velocidad_default_semanas = semanas de rotacion → invertir
            velocidad_normal = (
                sit.stock_inicial / float(config.velocidad_default_semanas)
                if float(config.velocidad_default_semanas) > 0 else 1.0
            )

        # ratio > 1 → rota más rápido de lo normal; < 1 → más lento.
        ratio = velocidad_real / velocidad_normal if velocidad_normal > 0 else 1.0

        semanas_stock = stock_restante / velocidad_real
        semanas_temp_rest = max(0, (producto.semanas_temporada or 8) - semanas_activo)

        empresa = _get_proveedor_empresa(producto)
        lead_time = _calcular_lead_time_real(empresa)
        semanas_lead = lead_time / 7
        buffer = float(config.semanas_buffer_seguridad)

        umbral_critico = float(config.umbral_ratio_velocidad_critica)
        umbral_alto = float(config.umbral_ratio_velocidad_alta)

        if semanas_stock <= semanas_lead:
            urgencia = 'CRITICA'
        elif semanas_stock <= semanas_lead + buffer:
            urgencia = 'ALTA'
        elif ratio > (1.0 / umbral_critico if umbral_critico > 0 else 2.0):
            # Rota más de 1/umbral_critico veces lo normal → urgencia ALTA
            urgencia = 'ALTA'
        elif ratio > (1.0 / umbral_alto if umbral_alto > 0 else 1.43):
            urgencia = 'MEDIA'
        elif semanas_stock <= semanas_lead + (buffer * 2):
            urgencia = 'MEDIA'
        else:
            urgencia = 'BAJA'

        if urgencia == 'BAJA':
            continue

        transito = Compras_Producto_Talla.objects.filter(
            compra_producto__compras__estado='ACTIVA',
            producto_talla__producto=producto,
        ).aggregate(
            total=Sum(F('stock') - F('unidades_recibidas'))
        )['total'] or 0
        transito = max(0, transito)

        necesarias = int(velocidad_real * semanas_temp_rest)
        recompra = max(0, necesarias - stock_restante - transito)

        alertas.append(AlertaVelocidad(
            articulo=producto,
            temporada=producto.temporada or '',
            anio=producto.anio_temporada or timezone.localdate().year,
            stock_inicial=sit.stock_inicial,
            ventas_acumuladas=ventas_acum,
            stock_restante=stock_restante,
            semanas_activo=round(semanas_activo, 2),
            velocidad_real_semanas=round(velocidad_real, 2),
            velocidad_normal_semanas=round(velocidad_normal, 2),
            ratio_velocidad=round(ratio, 2),
            semanas_stock_restante=round(semanas_stock, 2),
            semanas_temporada_restante=round(semanas_temp_rest, 2),
            lead_time_proveedor_dias=lead_time,
            semanas_para_pedir=round(semanas_lead + buffer, 2),
            urgencia=urgencia,
            unidades_recompra_sugerida=recompra,
            unidades_en_transito=transito,
        ))

    if producto_ids:
        AlertaVelocidad.objects.filter(
            articulo_id__in=producto_ids, resuelta=False,
        ).delete()
    else:
        AlertaVelocidad.objects.filter(resuelta=False).delete()

    AlertaVelocidad.objects.bulk_create(alertas, batch_size=500)
    logger.info("Alertas de velocidad: %d generadas", len(alertas))
    return len(alertas)


# ──────────────────────────────────────────────────────────────
#  8. Alertas de quiebre de talle
# ──────────────────────────────────────────────────────────────

def detectar_quiebres_talle(producto_ids=None):
    """
    Detecta productos donde los talles populares están agotados
    aunque el stock total sea > 0.  Solo sucursales vendedoras/mixtas.
    """
    config = ConfiguracionPrediccion.get_config()

    filtro = Q(
        temporada__isnull=False,
        sucursal__tipo_sucursal__in=TIPOS_SUCURSAL_VENTA,
    )
    if producto_ids:
        filtro &= Q(id__in=producto_ids)

    productos = Producto.objects.filter(filtro).select_related(
        'atributo1', 'atributo3', 'categoria', 'categoria__padre',
    ).prefetch_related('producto_talla')

    alertas = []

    for producto in productos:
        talles = list(producto.producto_talla.all())
        stock_total = sum(t.stock for t in talles)
        if stock_total <= 0:
            continue

        curva = _obtener_curva_talles(producto)
        if not curva:
            continue

        agotados = []
        con_stock = []
        pct_sin_cubrir = Decimal('0')

        for t in talles:
            pct = curva.get(t.talla, Decimal('0'))
            if t.stock <= 0:
                agotados.append(t.talla)
                pct_sin_cubrir += pct
            else:
                con_stock.append(t.talla)

        if float(pct_sin_cubrir) < 0.25:
            continue

        criticos = [
            talle for talle in agotados
            if float(curva.get(talle, 0)) >= 0.05
        ]

        sit = StockInicialTemporada.objects.filter(
            producto=producto, temporada=producto.temporada,
        ).first()
        velocidad_total = 0
        if sit and sit.fecha_primer_ingreso:
            semanas = float(_semanas_entre(sit.fecha_primer_ingreso, timezone.localdate()))
            ventas_tot = _obtener_ventas_producto(producto)
            velocidad_total = ventas_tot / semanas if semanas > 0 else 0

        perdidas = int(velocidad_total * float(pct_sin_cubrir))

        umbral_critico = float(config.umbral_quiebre_talle_critico)
        if float(pct_sin_cubrir) >= umbral_critico:
            urgencia = 'CRITICA'
        elif float(pct_sin_cubrir) >= 0.40:
            urgencia = 'ALTA'
        else:
            urgencia = 'MEDIA'

        alertas.append(AlertaQuiebreTalle(
            articulo=producto,
            temporada=producto.temporada or '',
            anio=producto.anio_temporada or timezone.localdate().year,
            talles_agotados=agotados,
            talles_con_stock=con_stock,
            talles_agotados_criticos=criticos,
            stock_total_restante=stock_total,
            porcentaje_demanda_sin_cubrir=round(float(pct_sin_cubrir) * 100, 2),
            unidades_perdidas_semana_estimadas=perdidas,
            urgencia=urgencia,
        ))

    if producto_ids:
        AlertaQuiebreTalle.objects.filter(
            articulo_id__in=producto_ids, resuelta=False,
        ).delete()
    else:
        AlertaQuiebreTalle.objects.filter(resuelta=False).delete()

    AlertaQuiebreTalle.objects.bulk_create(alertas, batch_size=500)
    logger.info("Alertas quiebre talle: %d generadas", len(alertas))
    return len(alertas)


# ──────────────────────────────────────────────────────────────
#  8b. Punto de reorden dinámico (alternativa a predicción pura)
# ──────────────────────────────────────────────────────────────

def calcular_punto_reorden(producto, config=None):
    """
    Calcula punto de reorden dinámico basado en:
      - Velocidad del GRUPO (no del producto individual) → más robusto con datos escasos
      - Clasificación ABC → más stock de seguridad para productos clase A
      - Lead time real del proveedor

    Para retail con miles de SKUs y cantidades pequeñas, este enfoque
    es más confiable que predicción individual porque:
      1) Usa datos agregados del grupo (marca+categoría+género)
      2) No necesita historial largo por producto
      3) Se adapta automáticamente al cambiar lead times

    Returns: dict con punto_reorden, stock_seguridad, stock_maximo, velocidad_grupo
    """
    if config is None:
        config = ConfiguracionPrediccion.get_config()

    # 1. Velocidad del grupo (más robusta que la individual)
    vel_hist = _buscar_velocidad_historica(producto)
    sit = StockInicialTemporada.objects.filter(
        producto=producto, temporada=producto.temporada,
    ).first()

    if vel_hist and sit and sit.stock_inicial > 0:
        # Velocidad del grupo en unidades/semana
        velocidad_grupo = sit.stock_inicial / float(vel_hist.velocidad_semanas_p50)
    elif sit and sit.stock_inicial > 0:
        velocidad_grupo = sit.stock_inicial / float(config.velocidad_default_semanas)
    else:
        return None

    # 2. Lead time en semanas
    empresa = _get_proveedor_empresa(producto)
    lead_time_dias = _calcular_lead_time_real(empresa)
    lead_time_semanas = lead_time_dias / 7.0

    # 3. Factor de seguridad según ABC
    abc = ClasificacionABC.objects.filter(
        articulo=producto,
    ).order_by('-fecha_calculo').first()

    if abc and abc.clasificacion_abc == 'A':
        factor_ss = float(config.factor_seguridad_clase_a)
        semanas_review = 1.0  # Revisión más frecuente
    elif abc and abc.clasificacion_abc == 'B':
        factor_ss = float(config.factor_seguridad_clase_b)
        semanas_review = 1.5
    else:
        factor_ss = float(config.factor_seguridad_clase_c)
        semanas_review = 2.0

    buffer = float(config.semanas_buffer_seguridad)

    # 4. Cálculos de punto de reorden
    demanda_lead_time = velocidad_grupo * lead_time_semanas
    stock_seguridad = velocidad_grupo * buffer * factor_ss
    punto_reorden = int(demanda_lead_time + stock_seguridad)
    stock_maximo = int(velocidad_grupo * (lead_time_semanas + semanas_review) * factor_ss)

    return {
        'punto_reorden': punto_reorden,
        'stock_seguridad': int(stock_seguridad),
        'stock_maximo': stock_maximo,
        'velocidad_grupo_sem': round(velocidad_grupo, 2),
        'lead_time_semanas': round(lead_time_semanas, 2),
        'factor_seguridad': factor_ss,
    }


def enriquecer_sugerencias_con_reorden(temporada=None, anio=None):
    """
    Para productos con predicción de baja confianza (<0.40),
    complementa o reemplaza la sugerencia con punto de reorden dinámico.
    Esto asegura que productos con datos escasos igual reciban
    sugerencias de compra razonables basadas en el grupo.
    """
    config = ConfiguracionPrediccion.get_config()
    filtro = Q(confianza__lt=Decimal('0.40'))
    if temporada:
        filtro &= Q(temporada=temporada)
    if anio:
        filtro &= Q(anio=anio)

    predicciones_baja = PrediccionDemanda.objects.filter(filtro).select_related(
        'articulo', 'articulo__atributo1', 'articulo__atributo3',
        'articulo__categoria', 'articulo__categoria__padre',
    )

    sugerencias_nuevas = []
    productos_actualizados = 0

    for pred in predicciones_baja:
        producto = pred.articulo
        reorden = calcular_punto_reorden(producto, config)
        if not reorden:
            continue

        talles = Producto_Talla.objects.filter(producto=producto)
        if not talles.exists():
            continue

        curva = _obtener_curva_talles(producto)
        stock_total = sum(max(0, t.stock or 0) for t in talles)

        # Solo generar sugerencia si stock actual < punto de reorden
        if stock_total >= reorden['punto_reorden']:
            continue

        unidades_necesarias = reorden['stock_maximo'] - stock_total

        for pt in talles:
            pct = curva.get(pt.talla, Decimal('0'))
            if not pct and len(talles) > 0:
                pct = Decimal('1') / len(talles)

            unidades_talle = int(float(unidades_necesarias) * float(pct))
            stock_actual = max(0, pt.stock or 0)

            transito = Compras_Producto_Talla.objects.filter(
                compra_producto__compras__estado='ACTIVA',
                producto_talla=pt,
            ).aggregate(
                total=Sum(F('stock') - F('unidades_recibidas'))
            )['total'] or 0
            transito = max(0, transito)

            a_pedir = max(0, unidades_talle - stock_actual - transito)

            if a_pedir > 0:
                # Eliminar sugerencia previa de baja confianza para este talle
                SugerenciaCompra.objects.filter(
                    prediccion=pred, articulo_talle=pt,
                    origen='prediccion', aprobada=False,
                ).delete()

                sugerencias_nuevas.append(SugerenciaCompra(
                    prediccion=pred,
                    articulo_talle=pt,
                    unidades_sugeridas=unidades_talle,
                    stock_actual=stock_actual,
                    unidades_en_transito=transito,
                    unidades_a_pedir=a_pedir,
                    origen='prediccion',
                ))

        productos_actualizados += 1

    if sugerencias_nuevas:
        SugerenciaCompra.objects.bulk_create(sugerencias_nuevas, batch_size=500)

    logger.info(
        "Reorden dinámico: %d productos actualizados, %d sugerencias generadas",
        productos_actualizados, len(sugerencias_nuevas),
    )
    return len(sugerencias_nuevas)


# ──────────────────────────────────────────────────────────────
#  9. Procesar pendientes (mark-then-evaluate)
# ──────────────────────────────────────────────────────────────

def procesar_pendientes_reevaluacion():
    """
    Procesa la tabla PendienteReevaluacion en batch.
    Llamado por management command cada 30 min.
    """
    pendientes = PendienteReevaluacion.objects.filter(procesado=False)

    vel_ids = list(
        pendientes.filter(tipo='velocidad')
        .values_list('producto_id', flat=True).distinct()
    )
    quiebre_ids = list(
        pendientes.filter(tipo='quiebre_talle')
        .values_list('producto_id', flat=True).distinct()
    )

    resultado = {'velocidad': 0, 'quiebre': 0}

    if vel_ids:
        resultado['velocidad'] = evaluar_alertas_velocidad(producto_ids=vel_ids)

    if quiebre_ids:
        resultado['quiebre'] = detectar_quiebres_talle(producto_ids=quiebre_ids)

    pendientes.update(procesado=True, fecha_procesado=timezone.now())

    logger.info(
        "Pendientes procesados: %d velocidad, %d quiebre",
        resultado['velocidad'], resultado['quiebre']
    )
    return resultado


# ──────────────────────────────────────────────────────────────
#  10. Pipeline completo
# ──────────────────────────────────────────────────────────────

def ejecutar_pipeline_completo(temporada=None, anio=None, sucursal_id=None,
                                solo_clasificacion=False, solo_alertas=False):
    """Ejecuta todo el pipeline de predicción."""
    _lead_time_cache.clear()
    resultados = {}

    if solo_alertas:
        resultados['alertas_velocidad'] = evaluar_alertas_velocidad()
        resultados['alertas_quiebre'] = detectar_quiebres_talle()
        return resultados

    resultados['clasificacion'] = calcular_clasificacion_abc_xyz(
        temporada=temporada, anio=anio, sucursal_id=sucursal_id,
    )
    if solo_clasificacion:
        return resultados

    resultados['velocidades'] = calcular_velocidades_historicas()
    resultados['curvas_talle'] = calcular_curvas_talles()
    resultados['predicciones'] = calcular_predicciones_demanda(
        temporada=temporada, anio=anio,
    )
    resultados['sugerencias'] = generar_sugerencias_compra(
        temporada=temporada, anio=anio,
    )
    # Enriquecer sugerencias de baja confianza con punto de reorden dinámico
    resultados['reorden_dinamico'] = enriquecer_sugerencias_con_reorden(
        temporada=temporada, anio=anio,
    )
    resultados['alertas_velocidad'] = evaluar_alertas_velocidad()
    resultados['alertas_quiebre'] = detectar_quiebres_talle()

    logger.info("Pipeline completo finalizado: %s", resultados)
    return resultados
