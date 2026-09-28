# Glosario de indicadores — inventario por reporte (2026-09)

Primer paso de **"misma definición en todas partes"** para el módulo Reportes.
Hasta ahora cada reporte calculaba cobertura, rotación, sell-through, stock
viejo, dead stock, GMROI y margen con su propia fórmula y ventana, sin decirlo
en el rótulo. Resultado: la misma marca sale con coberturas distintas en
Inteligencia de Compra y en el Plan de Liquidación, "348 d" en Existencias por
Sucursal y "~274 d" (9 meses) en el Plan, y "62 % de stock viejo" al lado de
"44 % de dead stock" como si fueran el mismo indicador.

Lo que se hizo en este paso:

- **Glosario único**: [`app/services/indicadores.py`](../retailmind/app/services/indicadores.py)
  (`GLOSARIO` + funciones puras con manejo de división por cero).
- **Template tag** `{% definicion 'clave' [ventana] %}` en
  [`app/templatetags/indicadores_tags.py`](../retailmind/app/templatetags/indicadores_tags.py)
  para que el `title` de cada KPI card cite la fórmula del glosario.
- **Rótulos honestos**: cada KPI card declara su ventana y su base
  ("Cobertura (30 d)", "Cobertura (pronóstico 12 m)", "Cob. (m · 365 d)",
  "Stock >180 d por edad de lote", "Dead stock 180 d (sin venta)").
- **Sin cambiar cifras**: las vistas que tenían la misma aritmética ahora
  llaman a las funciones del glosario (verificado con 1.020 comparaciones
  contra las fórmulas inline anteriores, incluidos ceros y negativos). Donde
  unificar cambiaría un número, **no se tocó** y queda anotado abajo.

---

## 1. Definiciones canónicas (`GLOSARIO`)

| Clave | Nombre | Fórmula | Ventana default |
|---|---|---|---|
| `cobertura_dias` | Cobertura (días) | stock actual ÷ (unidades vendidas en los últimos N d ÷ N), truncado | 30 d |
| `cobertura_meses_ttm` | Cobertura (meses, venta TTM) | stock tiendas ÷ (unidades vendidas 365 d ÷ 12) | 365 d |
| `cobertura_meses_pronostico` | Cobertura (meses, pronóstico 12 m) | stock tiendas ÷ (pronóstico 12 m ÷ 12) | — (pronóstico) |
| `rotacion` | Rotación (vueltas/año) | unidades vendidas 365 d ÷ stock actual (otra ventana se anualiza) | 365 d |
| `rotacion_pronostico` | Rotación (pronóstico) | 12 ÷ cobertura sobre pronóstico = pronóstico 12 m ÷ stock | — |
| `wos` | Semanas de stock | cobertura (meses) × 4,345 | — |
| `sell_through` | Sell-through (%) | vendidas en el período ÷ (vendidas + stock actual) × 100 | período del reporte |
| `sell_through_ingresado` | Sell-through vs ingresado (%) | vendidas en el año ÷ ingresadas (abastecimiento) en el año × 100, sin apertura sintética | 365 d (año calendario) |
| `pct_stock_viejo` | Stock viejo por **edad de lote** (%) | unidades en lotes vivos ingresados hace más de N d ÷ stock total × 100 | 180 d |
| `dead_stock` | Dead stock por **falta de venta** (%) | SKU-talla con stock y sin venta en N d ÷ SKU-talla con stock × 100 (o sus unidades / costo) | 180 d |
| `gmroi` | GMROI | margen bruto 365 d (a precio y costo de LISTA) ÷ inventario actual a costo | 365 d |
| `margen_teorico` | Margen bruto teórico (%) | (venta a lista − costo de lista de lo vendido) ÷ venta a lista × 100 | 365 d |

Convención de división por cero en las funciones: devuelven `sin_datos`
(`None` por defecto); el reporte que históricamente mostraba `0` lo pasa
explícito (`sin_datos=0`) para no cambiar su salida.

---

## 2. Inventario: cómo calcula cada reporte cada indicador

Leído del código el 2026-09-26. "Universo" = qué productos/ventas entran al
numerador y denominador.

### 2.1 Existencias por marca — `views_modulo_reportes.obtener_reporte_existencias_marca` (`/app/api/reporte-existencias-marca/`)

| Indicador | Fórmula en el código | Ventana | Universo |
|---|---|---|---|
| Cobertura (días) `salud.cobertura_dias` | `stock_univ ÷ (|Σ cantidad| ventas 30 d ÷ 30)`, truncado; `None` sin ventas | **30 d** | Productos de las sucursales permitidas (o la del filtro / sesión), `excluir_de_analitica=False`, filtros marca/depto/búsqueda… **pero sólo los artículos visibles (≤ `limite`, 500)**: con la lista truncada el KPI cubre un universo parcial. Ventas: `CONCEPTOS_VENTA`, `COMPLETADO`, por `ProductoTalla__producto_id` (sin filtro `sucursal_origen`). |
| Stock >180 d `salud.pct_stock_viejo` | `Σ cantidad_disponible` de `LoteProducto` activos con `fecha_ingreso ≤ hoy−181 d` ÷ `stock_univ` × 100; `0` sin stock | **>180 d (edad de lote)** | Mismos `productos_ids`. Lotes vs stock plano pueden descuadrar (drift FIFO). |

### 2.2 Existencias por sucursal — `views_modulo_reportes.obtener_reporte_existencias_sucursal` (`/app/api/reporte-existencias-sucursal/`)

| Indicador | Fórmula en el código | Ventana | Universo |
|---|---|---|---|
| Cobertura (días) `resumen.cobertura_dias` | `total_stock ÷ (|Σ cantidad| ventas 30 d ÷ 30)`, truncado; `None` sin ventas | **30 d** | Sucursal elegida + `excluir_de_analitica=False` + marca si se filtró, en numerador y denominador (regla auditoría ago-2026). Ventas con `sucursal_origen_id = sucursal`. |
| Stock >180 d `resumen.pct_stock_viejo` | lotes activos `fecha_ingreso ≤ hoy−181 d` ÷ `total_stock` × 100; `0` sin stock | **>180 d (edad de lote)** | Mismo universo. Rotulado "(aprox.)" por el descalce lotes/stock plano. |

### 2.3 Inteligencia de compra — `views_inteligencia_compra.obtener_inteligencia_compra` (`/app/api/inteligencia-compra/`)

| Indicador | Fórmula en el código | Ventana | Universo |
|---|---|---|---|
| Velocidad `kpis.vel_dia` | (ventas − reingresos) 90 d ÷ 90; `fecha < hoy` (excluye hoy) | **90 d** | Marca, `excluir_de_analitica=False`, **sólo tiendas** (`es_centro_distribucion=False`, o la tienda elegida). |
| Cobertura `kpis.cobertura_meses` | `stock_tiendas ÷ (forward_annual ÷ 12)`; `forward = TTM × (1+g)`, `g = yoy` acotado a [−35 %, +10 %] | **pronóstico 12 m** (TTM de meses completos como base) | Stock tiendas con stock > 0; serie de ventas netas en tiendas. |
| Rotación `finanzas.rotacion` | `12 ÷ cobertura` (≡ pronóstico ÷ stock, pasando por la cobertura redondeada a 1 decimal) | pronóstico | ídem |
| WOS `finanzas.wos` | `cobertura × 4,345` | pronóstico | ídem |
| Margen bruto `finanzas.margen_pct` | `(Σ|q|·precio − Σ|q|·costo) ÷ Σ|q|·precio` de ventas netas TTM, a precio/costo de LISTA del kardex (`margen_src='realizado'`); si el costo no viene: `(inv_precio − inv_costo) ÷ inv_precio` (`'lista'`) | **365 d** (o foto del inventario) | Ventas tiendas 365 d (incluye hoy). |
| GMROI `finanzas.gmroi` | `margen_anual ÷ inv_costo` (inventario tiendas a costo) | 365 d | ídem |
| Dead stock `salud.dead90_n / dead180_n / pct_dead180` | SKU-talla con stock > 0 en tiendas **sin ninguna venta** en 90 / 180 d; `pct = dead180_n ÷ skus_total`; `None` sin SKUs | **90 d / 180 d (falta de venta)** | Tiendas. `dead180_u/costo` = unidades y costo de esos SKU. |
| Sell-through anual `sellthrough[].str` | ventas netas del año (tiendas) ÷ abastecimiento del año (`CONCEPTOS_ABASTECIMIENTO` sin la apertura sintética `MIGRACION_LARAVEL`) × 100; `None` sin ingresos | año calendario | **Numerador: tiendas. Denominador: todas las sucursales del usuario (incluye CD).** Puede superar 100 %. |

### 2.4 Plan de liquidación — `views_inteligencia_compra.obtener_plan_liquidacion` / `_fila_liquidacion` (`/app/api/plan-liquidacion/`)

| Indicador | Fórmula en el código | Ventana | Universo |
|---|---|---|---|
| Rotación `rotacion` | `u ÷ stock` (u = `Σ|cantidad|` ventas 365 d); `None` con stock tiendas 0 | **365 d (TTM)** | Ventas `CONCEPTOS_VENTA` **sólo tiendas**, `excluir_de_analitica=False`, filtros marca/categoría/especialidad/sucursal. Stock tiendas con stock > 0 (CD aparte). Ventas **brutas** (no se restan reingresos, a diferencia de Inteligencia). |
| Cobertura (m) `cobertura` | `stock ÷ (u ÷ 12)`; `None` si `u == 0` o `stock == 0` | **365 d** | ídem |
| GMROI `gmroi` | `(venta − costo) ÷ valor_costo` con venta/costo a LISTA del kardex; `None` si `costo ≥ venta`, `costo == 0` o `valor_costo == 0` | 365 d | ídem |
| Dead stock `dead_u / dead_costo / dead_skus / pct_dead` | SKU-talla tiendas con stock > 0 sin venta 180 d; `pct_dead = dead_skus ÷ skus × 100`, `0` sin SKUs | **180 d (falta de venta)** | ídem |
| `totales.dead_pct_valor` | `dead_costo ÷ valor_costo` tiendas × 100 | 180 d | Todo el alcance (incluye filas sin marca). |
| Antigüedad (detalle / por-año) | días desde el lote vivo más antiguo (`Min(fecha_ingreso)`) o fecha de creación; tramos 180 / 365 / 730 d | **edad de lote** | Sólo en el detalle; no se mezcla con el dead stock del ranking. |

### 2.5 Resumen de existencias — `views_resumen_existencias.obtener_resumen_existencias` (`/app/api/resumen-existencias/`)

No calcula **ninguno** de los indicadores del glosario: es una foto valorizada
(pares, valor costo, valor precio interno = costo + sobreprecio, valor precio
venta) a la fecha de corte, por sucursal/empresa o por categoría. No se tocó
el template. Única relación: su "precio interno" es el mismo valor que
Existencias por Sucursal llama "Valor Inv. (costo+sobrepr.)" (ya rotulado).

### 2.6 Relacionados fuera de los 5 templates (para que el inventario sea completo)

| Reporte | Indicador | Fórmula | Ventana |
|---|---|---|---|
| Productos vendidos (`_agregar_productos_vendidos`, `/app/api/reportes/productos-vendidos/`) | `sell_through` | unidades **netas** de devoluciones ÷ (unidades netas + stock actual del alcance) × 100; `0` sin disponible | período del reporte |
| ídem | `cobertura_dias` | stock actual ÷ (unidades netas ÷ días del período), truncado | período del reporte |
| Rendimiento de compras (`api_rendimiento_compras`) | "rotación" (`rotacion_global`, `rotacion_pct`) | vendido ÷ entrada × 100, tope 999 | período |
| Métricas de compras (`calcular_metricas_compras`) | `margen_teorico`, `markup_teorico` | venta_lista − costo_lista sobre lo **comprado**; markup = margen ÷ costo × 100 | período |

---

## 3. Divergencias (mismo nombre, distinta definición)

| # | Indicador | Dónde | Qué difiere | Efecto |
|---|---|---|---|---|
| D1 | **Cobertura** | Existencias marca/sucursal vs Plan vs Inteligencia | **Tres bases**: días ÷ velocidad **30 d**; meses ÷ **venta TTM 365 d**; meses ÷ **pronóstico 12 m**. | Misma marca: Inteligencia ÷ pronóstico = TTM × (1+g) con g ∈ [−35 %, +10 %] ⇒ cobertura hasta **+54 %** mayor que en el Plan cuando la tendencia cae (el +30 % observado). Sucursal 30 d (348 d) vs Plan 365 d (~274 d = 9 m): distinta ventana y distinto universo (sucursal vs tiendas + filtros). |
| D2 | **"Stock viejo" vs "dead stock"** | Existencias (marca/sucursal) vs Inteligencia / Plan | Existencias mide **edad de lote** (`fecha_ingreso` > 180 d, en unidades); Inteligencia y Plan miden **falta de venta** (SKU-talla sin venta 180 d, en SKUs / unidades / costo). | 62 % vs 44 % no son comparables: un SKU viejo que vende una unidad sale del dead stock pero no del stock viejo. Ahora los rótulos lo dicen. |
| D3 | **Rotación** | Plan vs Inteligencia vs Rendimiento de compras | Plan: TTM ÷ stock. Inteligencia: pronóstico ÷ stock, derivada de la cobertura **redondeada** (12 ÷ cob). Rendimiento de compras llama "rotación" a vendido ÷ entrada × 100 (es un sell-through vs ingresado). | Mismas cifras de entrada dan rotaciones distintas; la de Inteligencia hereda el redondeo de la cobertura. |
| D4 | **Sell-through** | Productos vendidos vs Inteligencia | Denominador: vendidas + stock actual (período) vs **ingresado en el año** (numerador tiendas, denominador todas las sucursales incl. CD). | El de Inteligencia puede superar 100 %; el otro no. |
| D5 | **Ventas brutas vs netas** | Plan vs Inteligencia | Plan usa `Σ|cantidad|` de `CONCEPTOS_VENTA` sin restar reingresos; Inteligencia resta `CONCEPTOS_REINGRESO`. | Rotación/cobertura TTM del Plan levemente más optimistas que la base de Inteligencia (≈ +12 % en SKECHERS según auditoría ago-2026). |
| D6 | **Universo de Existencias por marca** | marca vs sucursal | Marca: productos de las sucursales permitidas **limitado a los artículos visibles** (≤ 500) y ventas sin filtro de sucursal de origen. Sucursal: sucursal + analítica + marca, ventas con `sucursal_origen`. | Con lista truncada, cobertura y % viejo de Marca describen sólo lo listado. |
| D7 | **División por cero** | varios | `pct_stock_viejo` → 0; `pct_dead` (Plan) → 0; `pct_dead180` (Inteligencia) → `None`; cobertura con stock 0: Plan → `None`, Inteligencia → 0,0; GMROI con margen 0: Plan → `None`, Inteligencia → 0,0. | Se preservó cada convención (parámetro `sin_datos` / guardas en el sitio de llamada). Unificar cambiaría el JSON. |
| D8 | **Cobertura negativa** | Productos vendidos | Unidades netas pueden ser < 0 (devoluciones > ventas): velocidad negativa ⇒ cobertura negativa. La función del glosario devolvería `None`. | No se cambió (cambiaría un valor). |
| D9 | **Ventana de velocidad** | Inteligencia | Velocidad 90 d excluye hoy (`fecha < hoy`); TTM incluye hoy. | Documentado; sin cambio. |

---

## 4. Qué se unificó (sin cambiar cifras) y qué no

**Vistas que ahora llaman al glosario (resultado idéntico, verificado):**

- `obtener_reporte_existencias_marca`: `cobertura_dias(stock, vendidas_30, 30)`, `pct_stock_viejo(viejo, stock, sin_datos=0)`; la API añade `salud.ventana_dias=30` y `salud.umbral_viejo_dias=180` (el rótulo los usa).
- `obtener_reporte_existencias_sucursal`: ídem; `resumen.ventana_dias`, `resumen.umbral_viejo_dias`.
- `obtener_inteligencia_compra`: `cobertura_meses(stock_tiendas, forward)`, `gmroi(...)`, `dead_stock_pct(...)`, `sell_through_ingresado(v, i)`; añade `data.ventanas`.
- `_fila_liquidacion` (Plan): `rotacion(u, stock)`, `cobertura_meses(stock, u)` (con la guarda `stock == 0 → None`), `gmroi(...)` (guarda `margen` sin dato), `dead_stock_pct(..., sin_datos=0)`; añade `data.ventanas`.
- `_agregar_productos_vendidos`: `sell_through(vendidas, stock, sin_datos=0)` por producto y total.

**No se tocó (cambiaría un número):**

- Rotación y WOS de Inteligencia (derivadas de la cobertura redondeada).
- Cobertura de Productos vendidos (D8).
- "Rotación" de Rendimiento de compras y margen/markup de Métricas de compras (otro universo: compras).
- Ninguna cifra de dinero (valor a costo, capital inmovilizado, margen $, GMROI numerador).

**Siguiente paso sugerido (no hecho):** decidir UNA base de cobertura para
marca (TTM o pronóstico) y una ventana única para "sin venta" / "edad", y
migrar los reportes uno a uno con sus oráculos.
