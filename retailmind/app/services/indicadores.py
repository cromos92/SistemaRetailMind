"""
Glosario ÚNICO de indicadores de inventario y venta + funciones PURAS de cálculo.

Objetivo ("misma definición en todas partes"): que cobertura, rotación,
sell-through, % de stock viejo, dead stock, GMROI y margen teórico tengan UNA
fórmula nombrada, con su ventana por defecto, y que los reportes la citen en
el rótulo/`title` de sus KPI cards en vez de reinventarla sin decirlo.

Este módulo NO consulta la base de datos: recibe números y devuelve números.
La lógica de queryset (universo, conceptos del kardex, exclusiones de
analítica) sigue viviendo en cada vista y en `app.constants_kardex`. Las
funciones sólo encapsulan la aritmética y el manejo de la división por cero,
para que dos reportes que digan "cobertura (30 d)" calculen exactamente lo
mismo.

Convención de división por cero: toda función recibe `sin_datos` (por
defecto ``None``) y lo devuelve cuando el denominador es cero o no hay
ventas. Los reportes que históricamente mostraban ``0`` en ese caso lo pasan
explícitamente (``sin_datos=0``) para no cambiar su salida.

Inventario de cómo usa cada reporte estas definiciones (y sus divergencias):
docs/GLOSARIO_INDICADORES_2026-09.md.

Uso en templates::

    {% load indicadores_tags %}
    <div class="nexo-kpi-label" title="{% definicion 'cobertura_dias' 30 %}">Cobertura (30 d)</div>
"""

# Semanas por mes (365,25 / 12 / 7). Mismo factor que usa inteligencia de compra.
SEMANAS_POR_MES = 4.345

# clave -> {'nombre', 'formula', 'ventana_default_dias', 'nota'}
# `formula` admite el marcador {ventana} (días). Sin ventana aplicable: None.
GLOSARIO = {
    'cobertura_dias': {
        'nombre': 'Cobertura (días)',
        'formula': ('Stock actual ÷ (unidades vendidas en los últimos {ventana} d ÷ {ventana}). '
                    'Días que dura el stock al ritmo de venta de la ventana.'),
        'ventana_default_dias': 30,
        'nota': ('Sin ventas en la ventana no hay velocidad: se muestra sin dato (—). '
                 'Con ventana corta (30 d) reacciona rápido pero es ruidosa en marcas chicas.'),
    },
    'cobertura_meses_ttm': {
        'nombre': 'Cobertura (meses, venta TTM)',
        'formula': ('Stock en tiendas ÷ (unidades vendidas en los últimos {ventana} d ÷ 12). '
                    'Meses que dura el stock al ritmo REAL de los últimos 12 meses.'),
        'ventana_default_dias': 365,
        'nota': 'Es la cobertura del Plan de Liquidación. Divide por venta histórica, no por pronóstico.',
    },
    'cobertura_meses_pronostico': {
        'nombre': 'Cobertura (meses, pronóstico 12 m)',
        'formula': ('Stock en tiendas ÷ (pronóstico de venta de los próximos 12 meses ÷ 12). '
                    'Meses que dura el stock al ritmo PROYECTADO.'),
        'ventana_default_dias': None,
        'nota': ('Es la cobertura de Inteligencia de Compra. Con tendencia a la baja el pronóstico '
                 'es menor que el TTM y la cobertura sale MAYOR que la del Plan de Liquidación '
                 'para la misma marca.'),
    },
    'rotacion': {
        'nombre': 'Rotación (vueltas/año)',
        'formula': ('Unidades vendidas en los últimos {ventana} d ÷ stock actual. '
                    'Cuántas veces al año se vende el inventario que hay hoy.'),
        'ventana_default_dias': 365,
        'nota': 'Ventana distinta de 365 d se anualiza (× 365 ÷ ventana).',
    },
    'rotacion_pronostico': {
        'nombre': 'Rotación (vueltas/año, pronóstico 12 m)',
        'formula': ('12 ÷ cobertura en meses sobre pronóstico = pronóstico 12 m ÷ stock en tiendas. '
                    'Vueltas al año que daría el stock de hoy al ritmo proyectado.'),
        'ventana_default_dias': None,
        'nota': 'Inteligencia de Compra la deriva de su cobertura (pronóstico), no de la venta histórica.',
    },
    'wos': {
        'nombre': 'Semanas de stock (WOS)',
        'formula': 'Cobertura en meses × 4,345 (semanas por mes).',
        'ventana_default_dias': None,
        'nota': 'Misma base que la cobertura de la que se deriva (pronóstico o TTM).',
    },
    'sell_through': {
        'nombre': 'Sell-through (%)',
        'formula': ('Unidades vendidas en el período ÷ (unidades vendidas en el período + stock actual) × 100. '
                    'Qué parte de lo disponible ya se vendió.'),
        'ventana_default_dias': None,
        'nota': 'Ventana = el período del reporte. Unidades netas de devoluciones.',
    },
    'sell_through_ingresado': {
        'nombre': 'Sell-through anual vs ingresado (%)',
        'formula': ('Unidades vendidas en el año ÷ unidades ingresadas (abastecimiento) en el mismo año × 100. '
                    'Excluye la apertura sintética de la migración.'),
        'ventana_default_dias': 365,
        'nota': ('Variante de Inteligencia de Compra: el denominador es lo INGRESADO, no lo disponible; '
                 'puede superar 100 % cuando se vende stock ingresado en años anteriores o traspasado.'),
    },
    'pct_stock_viejo': {
        'nombre': 'Stock viejo por edad de lote (%)',
        'formula': ('Unidades en lotes vivos ingresados hace más de {ventana} d ÷ stock total × 100. '
                    'Mide EDAD del stock (fecha de ingreso del lote), no si se vende o no.'),
        'ventana_default_dias': 180,
        'nota': ('No es dead stock: un SKU puede ser viejo y seguir vendiendo. Si lotes y stock plano '
                 'no cuadran, el porcentaje es aproximado.'),
    },
    'dead_stock': {
        'nombre': 'Dead stock por falta de venta (%)',
        'formula': ('SKU-talla con stock y sin ninguna venta en los últimos {ventana} d ÷ SKU-talla con stock × 100 '
                    '(o sus unidades / valor a costo). Mide falta de VENTA, no edad del lote.'),
        'ventana_default_dias': 180,
        'nota': ('No es stock viejo: un SKU recién ingresado que no vendió en 180 d cuenta; uno viejo que '
                 'vendió una unidad no cuenta.'),
    },
    'gmroi': {
        'nombre': 'GMROI',
        'formula': ('Margen bruto de los últimos {ventana} d (venta − costo de lo vendido, ambos a precio y costo '
                    'de LISTA del kardex) ÷ inventario actual a costo. Pesos de margen por peso invertido.'),
        'ventana_default_dias': 365,
        'nota': 'Sirve para comparar y ordenar, no como rentabilidad real (no usa lo cobrado ni el costo FIFO).',
    },
    'margen_teorico': {
        'nombre': 'Margen bruto teórico (%)',
        'formula': ('(Venta a precio de lista − costo de lista de lo vendido) ÷ venta a precio de lista × 100. '
                    'Con descuentos o campañas activas queda sobreestimado.'),
        'ventana_default_dias': 365,
        'nota': ('Inteligencia de Compra usa la venta TTM (365 d); si el kardex no trae costo usa el margen de '
                 'lista del inventario. En Rendimiento de Compras es (precio lista − costo) ÷ costo sobre lo '
                 'COMPRADO (markup, otro universo).'),
    },
}


def definicion(clave, ventana=None):
    """Fórmula del glosario para `clave`, con la ventana en días sustituida.

    >>> definicion('cobertura_dias', 30)
    'Stock actual ÷ (unidades vendidas en los últimos 30 d ÷ 30). ...'

    Sin `ventana` usa `ventana_default_dias`. Clave desconocida -> ''.
    """
    item = GLOSARIO.get(clave)
    if not item:
        return ''
    v = ventana if ventana is not None else item.get('ventana_default_dias')
    return item['formula'].replace('{ventana}', str(v) if v is not None else '')


def nombre(clave):
    """Nombre legible del indicador (rótulo canónico). Clave desconocida -> ''."""
    item = GLOSARIO.get(clave)
    return item['nombre'] if item else ''


# ---------------------------------------------------------------- funciones puras
def cobertura_dias(stock, unidades_vendidas, dias, sin_datos=None):
    """Días de cobertura: ``int(stock ÷ (unidades_vendidas ÷ dias))``.

    Glosario 'cobertura_dias'. Trunca (no redondea), igual que los reportes de
    existencias por marca/sucursal. Sin ventas (``unidades_vendidas <= 0``) o
    ventana inválida (``dias <= 0``) devuelve `sin_datos`; stock 0 => 0.
    """
    if not unidades_vendidas or unidades_vendidas <= 0 or not dias or dias <= 0:
        return sin_datos
    return int(stock / (unidades_vendidas / dias))


def cobertura_meses(stock, unidades_anuales, sin_datos=None):
    """Meses de cobertura: ``round(stock ÷ (unidades_anuales ÷ 12), 1)``.

    Glosario 'cobertura_meses_ttm' (unidades_anuales = venta 365 d) o
    'cobertura_meses_pronostico' (unidades_anuales = pronóstico 12 m).
    Sin unidades anuales (``<= 0``) devuelve `sin_datos`; stock 0 => 0.0.
    """
    if not unidades_anuales or unidades_anuales <= 0:
        return sin_datos
    return round(stock / (unidades_anuales / 12.0), 1)


def rotacion(unidades_vendidas, stock, dias=365, sin_datos=None):
    """Vueltas por año: ``round(unidades_vendidas anualizadas ÷ stock, 2)``.

    Glosario 'rotacion'. Con ``dias == 365`` no se toca el numerador (mismo
    resultado que ``round(u / stock, 2)``); otra ventana se anualiza
    ``× 365 ÷ dias``. Stock 0 (o ventana inválida) devuelve `sin_datos`.
    """
    if not stock or not dias or dias <= 0:
        return sin_datos
    anual = unidades_vendidas if dias == 365 else unidades_vendidas * 365.0 / dias
    return round(anual / stock, 2)


def sell_through(unidades_vendidas, stock_actual, sin_datos=None):
    """Sell-through %: ``round(100 × vendidas ÷ (vendidas + stock_actual), 1)``.

    Glosario 'sell_through'. Disponible (vendidas + stock) igual a 0 devuelve
    `sin_datos`.
    """
    disponible = unidades_vendidas + stock_actual
    if not disponible:
        return sin_datos
    return round(100 * unidades_vendidas / disponible, 1)


def sell_through_ingresado(unidades_vendidas, unidades_ingresadas, sin_datos=None):
    """Sell-through vs ingresado %: ``round(100 × vendidas ÷ ingresadas, 1)``.

    Glosario 'sell_through_ingresado'. Sin ingresos devuelve `sin_datos`.
    Puede superar 100 % (ver nota del glosario).
    """
    if not unidades_ingresadas:
        return sin_datos
    return round(100.0 * unidades_vendidas / unidades_ingresadas, 1)


def pct_stock_viejo(unidades_viejas, stock_total, sin_datos=None):
    """% de stock viejo por edad de lote: ``round(100 × viejas ÷ stock_total, 1)``.

    Glosario 'pct_stock_viejo'. Stock total 0 devuelve `sin_datos`.
    """
    if not stock_total:
        return sin_datos
    return round(100 * unidades_viejas / stock_total, 1)


def dead_stock_pct(skus_sin_venta, skus_total, sin_datos=None):
    """% dead stock: ``round(100 × sin_venta ÷ total, 1)``.

    Glosario 'dead_stock'. Vale para SKU-talla, unidades o valor a costo
    mientras numerador y denominador sean la misma magnitud. Total 0
    devuelve `sin_datos`.
    """
    if not skus_total:
        return sin_datos
    return round(100.0 * skus_sin_venta / skus_total, 1)


def gmroi(margen_bruto, inventario_costo, sin_datos=None):
    """GMROI: ``round(margen_bruto ÷ inventario_costo, 2)``.

    Glosario 'gmroi'. Inventario a costo 0 devuelve `sin_datos`. El margen
    se calcula fuera (a precio/costo de lista, ver 'margen_teorico').
    """
    if not inventario_costo:
        return sin_datos
    return round(margen_bruto / inventario_costo, 2)


def margen_teorico_pct(venta_lista, costo_lista, sin_datos=None):
    """Margen bruto teórico %: ``round(100 × (venta − costo) ÷ venta, 1)``.

    Glosario 'margen_teorico'. Venta 0 devuelve `sin_datos`.
    """
    if not venta_lista:
        return sin_datos
    return round(100.0 * (venta_lista - costo_lista) / venta_lista, 1)


def semanas_de_stock(cobertura_en_meses, sin_datos=None):
    """WOS: ``round(cobertura_en_meses × 4,345, 1)``. Glosario 'wos'.

    Cobertura ausente (``None``) devuelve `sin_datos`.
    """
    if cobertura_en_meses is None:
        return sin_datos
    return round(cobertura_en_meses * SEMANAS_POR_MES, 1)
