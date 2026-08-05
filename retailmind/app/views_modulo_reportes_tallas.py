"""
Reporte "Quiebre de talla por sucursal" — módulo de reportes.

QUÉ MIDE Y POR QUÉ NO LO MIDE NINGÚN OTRO REPORTE
==================================================
En calzado el quiebre de talla es venta perdida INMEDIATA: el cliente no
transa el calce. Un modelo con 40 unidades repartidas en tallas que nadie usa
se ve "sano" en cualquier KPI agregado (stock alto, cobertura alta, sin
quiebre de SKU), y sin embargo está muerto en la talla que se pide. Este
reporte baja el análisis al nivel donde el problema existe —estilo × talla ×
tienda— y termina en una acción concreta: "esta talla está quebrada en la
tienda X y hay stock en el CD" = una orden de traspaso.

AGRUPADOR DE ESTILO (derivado, SIN migración)
==============================================
El modelo no tiene un campo "estilo". La identidad canónica del proyecto
(``app/utils_producto_match``) es
``articulo + marca + color + género + categoría`` POR SUCURSAL, y las tallas
viven en filas ``Producto_Talla`` colgando de ese ``Producto``.

Aquí se agrupa por ``(articulo normalizado, marca, color)``, que es el
"modelo × color" del retail de moda:

* Se **normaliza el articulo** con ``normalizar_articulo`` (mayúsculas, sin
  acentos, espacios colapsados) porque en producción hay fichas duplicadas que
  sólo difieren en la caja del código; sin normalizar, la curva de tallas de un
  mismo modelo quedaba partida en dos.
* Se **incluye color**: en calzado el color es una unidad de compra y de
  reposición distinta (un par negro no reemplaza a uno blanco), así que
  consolidar colores escondería el quiebre.
* Se **excluyen género y categoría** del agrupador: son atributos derivados del
  modelo (un mismo código no cambia de género), y en el catálogo real están
  mal poblados en fichas legacy — meterlos en la clave volvía a partir estilos
  que son el mismo producto. Se exponen como metadatos del estilo (``generos``,
  ``categorias``) para que se vea si un estilo quedó con datos inconsistentes.
* Las **tallas se consolidan** dentro del estilo: si dos fichas duplicadas
  tienen la talla 40, sus stocks y sus ventas se suman.

LA TRAMPA DE LA CURVA (por qué no se usa la venta cruda a secas)
=================================================================
Calcular la curva de tallas con las ventas históricas CRUDAS hereda el propio
quiebre: una talla que estuvo agotada 8 semanas "demuestra" que se vende poco,
así que se compra menos y se agota antes. Es una profecía autocumplida.

Mitigación implementada: por cada estilo × talla se reconstruye desde el
kardex cuántos días del período esa talla tuvo stock > 0 (``dias_disponible``),
partiendo del stock actual y caminando los movimientos hacia atrás. Con eso:

* ``pct_disponible``       — % del período con stock disponible.
* ``demanda_subestimada``  — bandera cuando la disponibilidad fue baja
                             (< ``PCT_DISPONIBILIDAD_MINIMA``): la venta cruda
                             de esa talla NO es demanda, es lo poco que
                             alcanzó a venderse.
* ``venta_ajustada``       — venta extrapolada a disponibilidad completa
                             (``vendidas × dias_periodo / dias_disponible``).
* ``unidades_perdidas``    — ``venta_ajustada − vendidas``, la estimación de lo
                             que no se vendió por no haber talla.

LIMITACIONES (leerlas antes de tomar decisiones de compra con esto)
====================================================================
1. La reconstrucción de disponibilidad asume que ``Producto_Talla.stock`` y el
   kardex están cuadrados. En producción hay descuadres conocidos (traspasos
   legacy de una sola pierna, conceptos migrados desde Laravel). Cuando la
   reconstrucción pasa por valores negativos se marca
   ``reconstruccion_dudosa`` en esa talla, y el número deja de ser confiable.
2. ``venta_ajustada`` es una extrapolación lineal: supone que la demanda diaria
   con stock se habría mantenido durante los días sin stock. Con
   disponibilidades muy bajas (2-3 días) el multiplicador se dispara; por eso
   el ``pct_disponible`` se muestra SIEMPRE junto a la cifra ajustada.
3. El universo son los estilos CON VENTA en el período: un estilo que nunca se
   vendió no tiene curva de la cual detectar un hueco (eso es stock muerto, y
   se ve en el reporte de liquidación, no aquí).
4. Sustitución entre tallas contiguas no se modela: parte de la venta de la 41
   puede ser un cliente de 40 que se conformó. Eso hace que la venta perdida
   estimada sea, si acaso, CONSERVADORA.

CONVENCIONES OBLIGATORIAS QUE CUMPLE
=====================================
* Alcance por empresa (``ids_sucursales_alcance``): jamás se confía en el
  ``sucursal_id`` que llega por querystring — sucursal ajena ⇒ 403.
* ``@requiere_permiso('reporte_existencias_sucursal')``.
* ``excluir_de_analitica`` respetado en TODOS los universos.
* Conceptos de venta importados de ``constants_kardex.CONCEPTOS_VENTA``.
* Toda suma es ``values().annotate(Sum(...))`` en la BD; Python sólo
  reorganiza filas ya agregadas.
* Tope de estilos analizados (``limite``) con bandera ``truncado`` + paginación
  sobre el resultado.
"""
import logging
from datetime import date, timedelta

from django.db.models import Q, Sum
from django.db.models.functions import Abs
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.utils import timezone

from .constants_kardex import CONCEPTOS_VENTA
from .decorators import requiere_permiso
from .models import Movimientos_Producto, Producto, Producto_Talla, Sucursal
from .utils_permisos import ids_sucursales_alcance
from .utils_producto_match import normalizar_articulo
from .utils_tallas import clave_orden_talla

logger = logging.getLogger('app')


# ── Parámetros y topes ────────────────────────────────────────────────────
DIAS_DEFAULT = 90
DIAS_MAX = 730

# Ventana para DERIVAR las tallas core. Más larga que la de análisis a
# propósito: las tallas core son una propiedad estructural del rubro en esa
# tienda y con un solo trimestre de datos una categoría chica es puro ruido.
DIAS_CORE_DEFAULT = 365
DIAS_CORE_MAX = 1460

# % de la venta acumulada del grupo (categoría × marca) que define el núcleo
# de tallas. Configurable por querystring; 80% es el default del retail.
UMBRAL_CORE_DEFAULT = 80

# Unidades mínimas vendidas en el período para considerar que una talla tiene
# "venta histórica relevante" y por lo tanto su stock 0 es un QUIEBRE y no
# simplemente una talla que este estilo nunca movió.
MIN_VENTA_QUIEBRE_DEFAULT = 1

# Bajo este % de disponibilidad la venta cruda de la talla no se puede leer
# como demanda.
PCT_DISPONIBILIDAD_MINIMA = 60

LIMITE_ESTILOS_DEFAULT = 300
LIMITE_ESTILOS_MAX = 1000

PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _sin_acceso(mensaje='No tienes acceso a esa sucursal: pertenece a otra empresa.'):
    """403 uniforme, mismo contrato JSON que el resto del módulo de reportes."""
    return JsonResponse({'success': False, 'error': mensaje}, status=403)


def _validar_sucursal(request, sucursal_id):
    """
    Valida el ``sucursal_id`` recibido contra el alcance real del usuario.

    Espejo de ``views_modulo_reportes._alcance_sucursales`` (se reimplementa en
    lugar de importarse para no acoplar este módulo a las 10k líneas del otro).
    Devuelve ``(permitidas, error)``; ``permitidas is None`` significa "ve todo"
    (rol administrador o flag ``puede_ver_todas_sucursales``).
    """
    permitidas = ids_sucursales_alcance(request.user)

    try:
        pedida = int(sucursal_id)
    except (TypeError, ValueError):
        return permitidas, _sin_acceso('Sucursal no válida.')

    if permitidas is not None and pedida not in permitidas:
        logger.warning(
            'quiebre-talla: usuario %s pidió la sucursal %s fuera de su alcance',
            getattr(request.user, 'username', request.user), sucursal_id,
        )
        return permitidas, _sin_acceso()

    return permitidas, None


def normalizar_talla(valor):
    """
    Canoniza el texto de una talla para poder agruparla.

    Las tallas son texto libre ('40', ' 40 ', '7,5', '7.5', 'xl'). Sin
    canonizar, la misma talla escrita distinto parte la curva en dos y produce
    quiebres fantasma. Las numéricas se llevan a la notación chilena con coma
    ('7.50' → '7,5'; '40.0' → '40') y las alfabéticas a mayúsculas.
    """
    t = (valor or '').strip().upper()
    if not t:
        return '—'
    try:
        num = float(t.replace(',', '.'))
    except ValueError:
        return ' '.join(t.split())
    # %g quita ceros insignificantes: 40.0 → '40', 7.50 → '7.5'
    return ('%g' % num).replace('.', ',')


def _parse_int(valor, default, minimo=None, maximo=None):
    """Entero de querystring acotado. El clamp se aplica TAMBIÉN al default.

    (Si no se aplicara, `dias_core` se quedaba en 365 aunque el período pedido
    fuera de 730 días y las tallas core salían de una ventana MÁS CORTA que el
    análisis, que es justo al revés de lo que se busca.)
    """
    try:
        n = int(valor)
    except (TypeError, ValueError):
        n = default
    if minimo is not None:
        n = max(minimo, n)
    if maximo is not None:
        n = min(maximo, n)
    return n


def _parse_fecha(valor):
    try:
        return date.fromisoformat((valor or '').strip())
    except (TypeError, ValueError):
        return None


def _ids_sucursales_compradoras(permitidas, cd_id=None):
    """
    Sucursales que abastecen (``es_compradora``) dentro del alcance del usuario.

    ``es_compradora`` es una *property* de Python, no una columna: se replica
    su definición (``es_centro_distribucion`` OR ``tipo_sucursal ==
    'CENTRO_DISTRIBUCION'``) en SQL.

    NO se restringe a la misma empresa de la tienda a propósito: en producción
    el CD principal (EDEL) abastece a tiendas de MÁS DE UNA empresa del
    holding, así que acotar por empresa dejaría fuera justo el traspaso que el
    reporte quiere proponer. La frontera de seguridad es el alcance del
    usuario, que ya viene aplicado en ``permitidas``.
    """
    qs = Sucursal.objects.filter(
        Q(es_centro_distribucion=True) | Q(tipo_sucursal='CENTRO_DISTRIBUCION')
    )
    if permitidas is not None:
        qs = qs.filter(id__in=permitidas)
    if cd_id:
        qs = qs.filter(id=cd_id)
    return list(qs.values_list('id', flat=True))


def _mapa_productos(queryset_valores):
    """
    Construye ``producto_id → clave_estilo`` y ``clave_estilo → metadatos``.

    ``queryset_valores`` es un ``values_list`` plano de Producto (una fila por
    ficha, NO por talla), así que esto es barato incluso con el catálogo
    completo de una bodega.
    """
    por_producto = {}
    meta = {}
    for (pid, articulo, marca_id, color_id, marca, color,
         categoria_id, categoria, genero) in queryset_valores:
        art = normalizar_articulo(articulo)
        if not art:
            continue
        clave = (art, marca_id, color_id)
        por_producto[pid] = clave
        info = meta.setdefault(clave, {
            'articulo': art,
            'articulo_original': articulo,
            'marca': marca or '—',
            'color': color or '—',
            'categoria_id': categoria_id,
            'categorias': set(),
            'generos': set(),
            'producto_ids': [],
        })
        info['producto_ids'].append(pid)
        if categoria:
            info['categorias'].add(categoria)
        if genero:
            info['generos'].add(genero)
        # La categoría del grupo core se toma de la primera ficha no nula: las
        # fichas legacy sin categoría no deben borrar la del estilo.
        if info['categoria_id'] is None and categoria_id is not None:
            info['categoria_id'] = categoria_id
    return por_producto, meta


def _tallas_core(filas_core, umbral_pct):
    """
    Deriva el núcleo de tallas por grupo ``(categoría, marca)`` DE LOS DATOS.

    Regla: se ordenan las tallas del grupo por unidades vendidas y se toman las
    que acumulan el ``umbral_pct``% de la venta del grupo. No hay lista fija de
    tallas "normales": la 43 puede ser core en una tienda y marginal en otra, y
    eso es exactamente lo que se quiere capturar.

    Devuelve ``(core, detalle)`` donde ``core[(cat_id, marca_id)]`` es un set de
    tallas normalizadas y ``detalle`` alimenta la UI.
    """
    por_grupo = {}
    for fila in filas_core:
        grupo = (fila['ProductoTalla__producto__categoria_id'],
                 fila['ProductoTalla__producto__atributo1_id'])
        talla = normalizar_talla(fila['ProductoTalla__talla'])
        acum = por_grupo.setdefault(grupo, {})
        acum[talla] = acum.get(talla, 0) + (fila['unidades'] or 0)

    core = {}
    detalle = {}
    for grupo, tallas in por_grupo.items():
        total = sum(tallas.values())
        if total <= 0:
            core[grupo] = set()
            detalle[grupo] = []
            continue
        objetivo = total * umbral_pct / 100.0
        acumulado = 0
        seleccionadas = []
        for talla, unidades in sorted(tallas.items(), key=lambda kv: (-kv[1], kv[0])):
            seleccionadas.append({
                'talla': talla,
                'unidades': unidades,
                'pct': round(100.0 * unidades / total, 1),
            })
            acumulado += unidades
            if acumulado >= objetivo:
                break
        core[grupo] = {t['talla'] for t in seleccionadas}
        detalle[grupo] = seleccionadas
    return core, detalle


def _dias_con_stock(stock_actual, deltas, desde, hasta, hoy):
    """
    Días del período ``[desde, hasta]`` en que la talla tuvo stock > 0.

    Se reconstruye hacia atrás desde el stock de HOY:
    ``stock_fin(d-1) = stock_fin(d) − Σ movimientos del día d``.

    Se recorre por SEGMENTOS entre días con movimiento (no día por día): entre
    dos movimientos el stock es constante, así que el costo es O(días con
    kardex) y no O(días del período).

    Devuelve ``(dias_con_stock, reconstruccion_dudosa)``. La bandera se levanta
    si la reconstrucción pasa por stock negativo, señal de que el kardex y el
    stock plano no cuadran para ese SKU y el número no es confiable.
    """
    saldo = stock_actual
    cursor = hoy
    dias = 0
    dudosa = False

    def _contar(saldo_seg, inicio_seg, fin_seg):
        lo = max(inicio_seg, desde)
        hi = min(fin_seg, hasta)
        if saldo_seg > 0 and hi >= lo:
            return (hi - lo).days + 1
        return 0

    cerrado = False
    for dia in sorted((d for d in deltas if d <= hoy), reverse=True):
        dias += _contar(saldo, dia, cursor)
        if saldo < 0:
            dudosa = True
        if dia <= desde:
            cerrado = True
            break
        saldo -= deltas[dia]
        cursor = dia - timedelta(days=1)

    if not cerrado:
        dias += _contar(saldo, desde, cursor)
        if saldo < 0:
            dudosa = True

    return dias, dudosa


# ══════════════════════════════════════════════════════════════════════════
# Vistas
# ══════════════════════════════════════════════════════════════════════════

@requiere_permiso('reporte_existencias_sucursal')
def ver_reporte_quiebre_talla(request):
    """Pantalla del reporte de quiebre de talla por sucursal."""
    return render(request, 'vistas/modulo_reportes/reporte_quiebre_talla.html')


@require_GET
@requiere_permiso('reporte_existencias_sucursal')
def api_reporte_quiebre_talla(request):
    """
    Datos del reporte de quiebre de talla (estilo × talla × tienda).

    Querystring:
      * ``sucursal_id``  (obligatorio) — la TIENDA a analizar.
      * ``dias``         (default 90)  — período de la curva de venta.
      * ``desde`` / ``hasta`` (ISO)    — período explícito; pisan a ``dias``.
      * ``dias_core``    (default 365) — ventana para derivar las tallas core.
      * ``umbral_core``  (default 80)  — % de venta acumulada que define core.
      * ``min_venta``    (default 1)   — unidades mínimas para llamar quiebre.
      * ``marca_id`` / ``categoria_id`` — filtros opcionales de catálogo.
      * ``cd_id``        — acota el cruce de reposición a un CD concreto.
      * ``solo_quiebres`` (default 1)  — sólo estilos con al menos un quiebre.
      * ``limite``       (default 300) — tope de estilos ANALIZADOS.
      * ``page`` / ``page_size``       — paginación del resultado.
    """
    try:
        sucursal_id = request.GET.get('sucursal_id')
        if not sucursal_id:
            return JsonResponse(
                {'success': False, 'error': 'Sucursal es requerida'}, status=400)

        permitidas, error = _validar_sucursal(request, sucursal_id)
        if error:
            return error
        sucursal_id = int(sucursal_id)

        tienda = Sucursal.objects.filter(id=sucursal_id).first()
        if not tienda:
            return JsonResponse(
                {'success': False, 'error': 'La sucursal no existe'}, status=404)

        # ── Parámetros ────────────────────────────────────────────────────
        hoy = timezone.localdate()
        dias = _parse_int(request.GET.get('dias'), DIAS_DEFAULT, 7, DIAS_MAX)
        hasta = _parse_fecha(request.GET.get('hasta')) or hoy
        hasta = min(hasta, hoy)
        desde = _parse_fecha(request.GET.get('desde')) or (hasta - timedelta(days=dias - 1))
        if desde > hasta:
            desde, hasta = hasta, desde
        # Techo duro también cuando el período llega por desde/hasta explícitos:
        # el barrido de kardex de la reconstrucción de disponibilidad crece con
        # el largo de la ventana.
        desde = max(desde, hasta - timedelta(days=DIAS_MAX - 1))
        dias_periodo = (hasta - desde).days + 1

        dias_core = _parse_int(request.GET.get('dias_core'), DIAS_CORE_DEFAULT,
                               dias_periodo, DIAS_CORE_MAX)
        umbral_core = _parse_int(request.GET.get('umbral_core'),
                                 UMBRAL_CORE_DEFAULT, 10, 100)
        min_venta = _parse_int(request.GET.get('min_venta'),
                               MIN_VENTA_QUIEBRE_DEFAULT, 1, 9999)
        limite = _parse_int(request.GET.get('limite'), LIMITE_ESTILOS_DEFAULT,
                            1, LIMITE_ESTILOS_MAX)
        page = _parse_int(request.GET.get('page'), 1, 1)
        page_size = _parse_int(request.GET.get('page_size'), PAGE_SIZE_DEFAULT,
                               1, PAGE_SIZE_MAX)
        solo_quiebres = request.GET.get('solo_quiebres', '1').lower() not in (
            '0', 'false', 'no')
        marca_id = request.GET.get('marca_id') or None
        categoria_id = request.GET.get('categoria_id') or None
        cd_id = request.GET.get('cd_id') or None

        avisos = []
        if tienda.es_compradora:
            avisos.append(
                f'{tienda.alias} está marcada como sucursal compradora / centro '
                f'de distribución. El quiebre de talla mide venta perdida en '
                f'PISO DE VENTA: en una bodega el indicador no significa lo mismo.'
            )

        # ── Catálogo de la tienda → mapa de estilos ───────────────────────
        filtros_catalogo = {'sucursal_id': sucursal_id, 'excluir_de_analitica': False}
        if marca_id:
            filtros_catalogo['atributo1_id'] = marca_id
        if categoria_id:
            filtros_catalogo['categoria_id'] = categoria_id

        productos = Producto.objects.filter(**filtros_catalogo).values_list(
            'id', 'articulo', 'atributo1_id', 'atributo2_id',
            'atributo1__valor', 'atributo2__valor',
            'categoria_id', 'categoria__nombre', 'atributo3__valor',
        )
        estilo_de_producto, meta_estilos = _mapa_productos(productos)

        if not estilo_de_producto:
            return JsonResponse({
                'success': True,
                'parametros': _parametros(
                    tienda, desde, hasta, dias_periodo, dias_core, umbral_core,
                    min_venta, limite, solo_quiebres),
                'resumen': _resumen_vacio(),
                'estilos': [],
                'tallas_core': [],
                'paginacion': {'page': 1, 'page_size': page_size, 'total': 0,
                               'total_paginas': 0},
                'avisos': avisos + ['La sucursal no tiene catálogo analizable '
                                    '(¿todo marcado como excluir_de_analitica?).'],
            })

        # ── Filtros compartidos del kardex de la tienda ───────────────────
        filtros_kardex = {
            'ProductoTalla__producto__sucursal_id': sucursal_id,
            'ProductoTalla__producto__excluir_de_analitica': False,
            'estado': 'COMPLETADO',
        }
        if marca_id:
            filtros_kardex['ProductoTalla__producto__atributo1_id'] = marca_id
        if categoria_id:
            filtros_kardex['ProductoTalla__producto__categoria_id'] = categoria_id

        # ── (1) Venta del período por estilo × talla ──────────────────────
        # Los movimientos de venta son negativos (egresos): se suma el valor
        # ABSOLUTO en la BD, no en Python.
        ventas_filas = (
            Movimientos_Producto.objects
            .filter(concepto__in=CONCEPTOS_VENTA,
                    fecha__gte=desde, fecha__lte=hasta,
                    **filtros_kardex)
            .values('ProductoTalla__producto_id', 'ProductoTalla__talla')
            .annotate(unidades=Sum(Abs('cantidad')))
        )

        venta_por_estilo = {}     # clave_estilo → {talla: unidades}
        venta_estilo = {}         # clave_estilo → unidades
        for fila in ventas_filas:
            clave = estilo_de_producto.get(fila['ProductoTalla__producto_id'])
            if clave is None:
                continue
            talla = normalizar_talla(fila['ProductoTalla__talla'])
            unidades = fila['unidades'] or 0
            acum = venta_por_estilo.setdefault(clave, {})
            acum[talla] = acum.get(talla, 0) + unidades
            venta_estilo[clave] = venta_estilo.get(clave, 0) + unidades

        if not venta_estilo:
            return JsonResponse({
                'success': True,
                'parametros': _parametros(
                    tienda, desde, hasta, dias_periodo, dias_core, umbral_core,
                    min_venta, limite, solo_quiebres),
                'resumen': _resumen_vacio(),
                'estilos': [],
                'tallas_core': [],
                'paginacion': {'page': 1, 'page_size': page_size, 'total': 0,
                               'total_paginas': 0},
                'avisos': avisos + [
                    'Sin ventas en el período: no hay curva de tallas que '
                    'analizar. Amplía el rango de fechas.'],
            })

        # ── Tope de estilos analizados (los de mayor venta primero) ───────
        estilos_ordenados = sorted(venta_estilo.items(), key=lambda kv: -kv[1])
        estilos_totales = len(estilos_ordenados)
        truncado = estilos_totales > limite
        claves_analizadas = [clave for clave, _ in estilos_ordenados[:limite]]
        set_analizadas = set(claves_analizadas)

        producto_ids = [
            pid for clave in claves_analizadas
            for pid in meta_estilos[clave]['producto_ids']
        ]

        # ── (2) Stock actual por estilo × talla en la tienda ──────────────
        stock_filas = (
            Producto_Talla.objects
            .filter(producto_id__in=producto_ids)
            .values('producto_id', 'talla')
            .annotate(stock=Sum('stock'))
        )
        stock_por_estilo = {}     # clave_estilo → {talla: stock}
        for fila in stock_filas:
            clave = estilo_de_producto.get(fila['producto_id'])
            if clave is None:
                continue
            talla = normalizar_talla(fila['talla'])
            acum = stock_por_estilo.setdefault(clave, {})
            acum[talla] = acum.get(talla, 0) + (fila['stock'] or 0)

        # ── (3) Tallas core por (categoría, marca) en esta tienda ─────────
        desde_core = hasta - timedelta(days=dias_core - 1)
        core_filas = (
            Movimientos_Producto.objects
            .filter(concepto__in=CONCEPTOS_VENTA,
                    fecha__gte=desde_core, fecha__lte=hasta,
                    **filtros_kardex)
            .values('ProductoTalla__producto__categoria_id',
                    'ProductoTalla__producto__atributo1_id',
                    'ProductoTalla__talla')
            .annotate(unidades=Sum(Abs('cantidad')))
        )
        core_por_grupo, core_detalle = _tallas_core(core_filas, umbral_core)

        # ── (4) Disponibilidad histórica (el anti-profecía-autocumplida) ──
        # Un movimiento por día y por estilo × talla, agregado en la BD.
        deltas_filas = (
            Movimientos_Producto.objects
            .filter(ProductoTalla__producto_id__in=producto_ids,
                    estado='COMPLETADO',
                    fecha__gte=desde, fecha__lte=hoy)
            .exclude(cantidad=0)
            .values('ProductoTalla__producto_id', 'ProductoTalla__talla', 'fecha')
            .annotate(delta=Sum('cantidad'))
        )
        deltas_por_estilo = {}    # clave_estilo → {talla: {fecha: delta}}
        for fila in deltas_filas:
            clave = estilo_de_producto.get(fila['ProductoTalla__producto_id'])
            if clave is None:
                continue
            talla = normalizar_talla(fila['ProductoTalla__talla'])
            por_dia = deltas_por_estilo.setdefault(clave, {}).setdefault(talla, {})
            por_dia[fila['fecha']] = por_dia.get(fila['fecha'], 0) + (fila['delta'] or 0)

        # ── (5) Stock en las sucursales que abastecen (la orden de traspaso)
        cd_ids = [s for s in _ids_sucursales_compradoras(permitidas, cd_id)
                  if s != sucursal_id]
        stock_cd = {}       # clave_estilo → {talla: {alias_sucursal: stock}}
        if cd_ids:
            marcas = {c[1] for c in set_analizadas if c[1] is not None}
            colores = {c[2] for c in set_analizadas if c[2] is not None}
            filtros_cd = {'sucursal_id__in': cd_ids, 'excluir_de_analitica': False}
            if marcas:
                filtros_cd['atributo1_id__in'] = marcas
            if colores:
                filtros_cd['atributo2_id__in'] = colores

            productos_cd = Producto.objects.filter(**filtros_cd).values_list(
                'id', 'articulo', 'atributo1_id', 'atributo2_id',
                'atributo1__valor', 'atributo2__valor',
                'categoria_id', 'categoria__nombre', 'atributo3__valor',
            )
            estilo_de_producto_cd = {}
            for (pid, articulo, m_id, c_id, *_resto) in productos_cd:
                clave = (normalizar_articulo(articulo), m_id, c_id)
                if clave in set_analizadas:
                    estilo_de_producto_cd[pid] = clave

            if estilo_de_producto_cd:
                cd_filas = (
                    Producto_Talla.objects
                    .filter(producto_id__in=list(estilo_de_producto_cd.keys()),
                            stock__gt=0)
                    .values('producto_id', 'talla',
                            'producto__sucursal_id', 'producto__sucursal__alias')
                    .annotate(stock=Sum('stock'))
                )
                for fila in cd_filas:
                    clave = estilo_de_producto_cd.get(fila['producto_id'])
                    if clave is None:
                        continue
                    talla = normalizar_talla(fila['talla'])
                    destino = stock_cd.setdefault(clave, {}).setdefault(talla, {})
                    alias = fila['producto__sucursal__alias'] or \
                        f"Sucursal {fila['producto__sucursal_id']}"
                    destino[alias] = destino.get(alias, 0) + (fila['stock'] or 0)
        else:
            avisos.append(
                'No hay sucursales compradoras / centro de distribución dentro '
                'de tu alcance: el reporte muestra el quiebre pero no puede '
                'proponer de dónde reponer.')

        # ── (6) Armado del resultado ──────────────────────────────────────
        estilos = []
        total_quiebres = 0
        total_criticos = 0
        total_reponibles = 0
        total_unidades_cd = 0
        total_perdidas = 0.0

        for clave in claves_analizadas:
            info = meta_estilos[clave]
            grupo_core = (info['categoria_id'], clave[1])
            core = core_por_grupo.get(grupo_core, set())

            ventas_talla = venta_por_estilo.get(clave, {})
            stocks_talla = stock_por_estilo.get(clave, {})
            deltas_talla = deltas_por_estilo.get(clave, {})
            reposicion_talla = stock_cd.get(clave, {})

            tallas = set(ventas_talla) | set(stocks_talla) | set(core)

            curva = []
            stock_total = 0
            quiebres_estilo = 0
            perdidas_estilo = 0.0
            core_presentes = 0
            core_en_cero = 0

            for talla in sorted(tallas, key=clave_orden_talla):
                vendidas = ventas_talla.get(talla, 0)
                stock = stocks_talla.get(talla, 0)
                es_core = talla in core
                # Una talla que no está en el catálogo del estilo ni vendió ni
                # tiene stock sólo aparece porque es core del rubro: es un hueco
                # de surtido, no un quiebre. Se omite si además no es core.
                if vendidas == 0 and stock == 0 and not es_core:
                    continue

                stock_total += max(0, stock)
                if es_core:
                    core_presentes += 1
                    if stock <= 0:
                        core_en_cero += 1

                dias_ok, dudosa = _dias_con_stock(
                    stock, deltas_talla.get(talla, {}), desde, hasta, hoy)
                pct_disp = round(100.0 * dias_ok / dias_periodo, 1) if dias_periodo else 0.0

                if dias_ok > 0:
                    ajustada = round(vendidas * dias_periodo / dias_ok, 1)
                    perdidas = round(max(0.0, ajustada - vendidas), 1)
                else:
                    ajustada = None
                    perdidas = 0.0

                # QUIEBRE (estricto, como lo pide el negocio): stock 0 HOY en una
                # talla con venta relevante en el período. Una talla que nunca
                # se vendió NO es quiebre — es surtido que este estilo no tiene.
                quiebre = stock <= 0 and vendidas >= min_venta
                # Señal aparte: talla CORE del rubro sin stock. Puede no tener
                # venta propia justamente porque nunca estuvo disponible.
                core_sin_stock = es_core and stock <= 0

                reposicion = reposicion_talla.get(talla, {})
                unidades_cd = sum(reposicion.values())

                if quiebre:
                    quiebres_estilo += 1
                    perdidas_estilo += perdidas
                    if unidades_cd > 0:
                        total_reponibles += 1
                        total_unidades_cd += unidades_cd

                curva.append({
                    'talla': talla,
                    'vendidas': vendidas,
                    'stock': stock,
                    'es_core': es_core,
                    'quiebre': quiebre,
                    'core_sin_stock': core_sin_stock,
                    'dias_disponible': dias_ok,
                    'dias_periodo': dias_periodo,
                    'pct_disponible': pct_disp,
                    'demanda_subestimada': pct_disp < PCT_DISPONIBILIDAD_MINIMA,
                    'venta_ajustada': ajustada,
                    'unidades_perdidas': perdidas,
                    'reconstruccion_dudosa': dudosa,
                    'stock_cd': unidades_cd,
                    'reposicion': [{'sucursal': alias, 'stock': unidades}
                                   for alias, unidades in sorted(
                                       reposicion.items(), key=lambda kv: -kv[1])],
                })

            # EL CASO MÁS GRAVE: el estilo TIENE stock pero ninguna de sus
            # tallas core lo tiene. Se ve sano en cualquier KPI agregado y en
            # el piso de venta está muerto: lo que queda son las puntas.
            critico = stock_total > 0 and core_presentes > 0 and core_en_cero == core_presentes

            if quiebres_estilo:
                total_quiebres += quiebres_estilo
                total_perdidas += perdidas_estilo
            if critico:
                total_criticos += 1

            if solo_quiebres and not quiebres_estilo and not critico:
                continue

            estilos.append({
                'clave': f'{clave[0]}|{clave[1] or 0}|{clave[2] or 0}',
                'articulo': info['articulo_original'],
                'articulo_normalizado': info['articulo'],
                'marca': info['marca'],
                'color': info['color'],
                'categorias': sorted(info['categorias']) or ['—'],
                'generos': sorted(info['generos']) or ['—'],
                'fichas': len(info['producto_ids']),
                'venta_total': venta_estilo.get(clave, 0),
                'stock_total': stock_total,
                'tallas_core': sorted(core, key=clave_orden_talla),
                'quiebres': quiebres_estilo,
                'critico': critico,
                'unidades_perdidas': round(perdidas_estilo, 1),
                'reponible': any(t['quiebre'] and t['stock_cd'] > 0 for t in curva),
                'curva': curva,
            })

        # Orden accionable: primero lo crítico, después lo que más venta perdida
        # explica, y a igualdad lo que se puede reponer HOY desde el CD.
        estilos.sort(key=lambda e: (
            not e['critico'], -e['unidades_perdidas'], -e['quiebres'],
            not e['reponible'], -e['venta_total'],
        ))

        total_resultado = len(estilos)
        total_paginas = (total_resultado + page_size - 1) // page_size
        if total_paginas and page > total_paginas:
            page = total_paginas
        inicio = (page - 1) * page_size
        pagina = estilos[inicio:inicio + page_size]

        if truncado:
            avisos.append(
                f'Resultado TRUNCADO: la tienda tiene {estilos_totales} estilos '
                f'con venta en el período y se analizaron los {limite} de mayor '
                f'venta. Sube `limite` (máx. {LIMITE_ESTILOS_MAX}) o acota por '
                f'marca / categoría para ver el resto.')

        avisos.append(
            'La demanda por talla se corrige por disponibilidad: las tallas con '
            f'menos de {PCT_DISPONIBILIDAD_MINIMA}% de días con stock quedan '
            'marcadas como demanda subestimada y su venta cruda NO debe leerse '
            'como demanda real.')

        return JsonResponse({
            'success': True,
            'parametros': _parametros(tienda, desde, hasta, dias_periodo,
                                      dias_core, umbral_core, min_venta, limite,
                                      solo_quiebres),
            'resumen': {
                'estilos_analizados': len(claves_analizadas),
                'estilos_totales': estilos_totales,
                'estilos_con_quiebre': sum(1 for e in estilos if e['quiebres']),
                'estilos_criticos': total_criticos,
                'tallas_quebradas': total_quiebres,
                'quiebres_reponibles': total_reponibles,
                'unidades_disponibles_cd': total_unidades_cd,
                'unidades_perdidas_estimadas': round(total_perdidas, 1),
                'truncado': truncado,
            },
            'tallas_core': [
                {
                    'categoria_id': grupo[0],
                    'marca_id': grupo[1],
                    'tallas': detalle,
                }
                for grupo, detalle in core_detalle.items() if detalle
            ][:200],
            'estilos': pagina,
            'paginacion': {
                'page': page,
                'page_size': page_size,
                'total': total_resultado,
                'total_paginas': total_paginas,
            },
            'avisos': avisos,
        })

    except Exception as exc:  # noqa: BLE001 — contrato JSON del módulo
        logger.exception('Error en reporte de quiebre de talla')
        return JsonResponse(
            {'success': False, 'error': f'Error al generar el reporte: {exc}'},
            status=500)


def _parametros(tienda, desde, hasta, dias_periodo, dias_core, umbral_core,
                min_venta, limite, solo_quiebres):
    return {
        'sucursal_id': tienda.id,
        'sucursal': tienda.alias,
        'desde': desde.isoformat(),
        'hasta': hasta.isoformat(),
        'dias_periodo': dias_periodo,
        'dias_core': dias_core,
        'umbral_core': umbral_core,
        'min_venta': min_venta,
        'limite': limite,
        'solo_quiebres': solo_quiebres,
        'pct_disponibilidad_minima': PCT_DISPONIBILIDAD_MINIMA,
    }


def _resumen_vacio():
    return {
        'estilos_analizados': 0,
        'estilos_totales': 0,
        'estilos_con_quiebre': 0,
        'estilos_criticos': 0,
        'tallas_quebradas': 0,
        'quiebres_reponibles': 0,
        'unidades_disponibles_cd': 0,
        'unidades_perdidas_estimadas': 0,
        'truncado': False,
    }
