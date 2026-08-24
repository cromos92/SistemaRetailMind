# Índices propuestos para el módulo de Reportes — cuantificación

**Fase D (cierre de deuda) de la auditoría de Reportes · medido contra la BD de producción
(`retail`, PostgreSQL 17.11, DigitalOcean) en modo SOLO LECTURA.**

> **Revisión 2 — 2026-08-22, 17:09-18:05 hora de Santiago (21:09-22:05 UTC).**
> Una verificación adversarial independiente encontró que el razonamiento central de la
> revisión 1 estaba viciado: el proxy que sostenía el titular medía otra cosa, y el
> documento omitía el mecanismo que hoy hace barata la consulta. Esta revisión **rehace las
> mediciones** y **cambia el veredicto de la Propuesta 1**. El detalle de qué cambió y por
> qué está en §0.2. Todos los números de esta revisión están fechados: la base de datos
> sigue vendiendo mientras se mide.

Este documento cierra las dos propuestas de índice que quedaron abiertas en la Fase C
(§11 de [AUDITORIA_REPORTES_2026-08.md](AUDITORIA_REPORTES_2026-08.md)):

1. índice parcial sobre `Movimientos_Producto(fecha) WHERE estado='COMPLETADO'`
   (o `(fecha, concepto)`), y
2. `(ProductoTalla_id, fecha, hora, id)` para el `DISTINCT ON` de productos-origen.

**NO se creó ningún índice.** Todo lo que sigue son `SELECT`, `EXPLAIN` y
`EXPLAIN (ANALYZE, BUFFERS)` de sentencias `SELECT`, más invocaciones de las vistas reales
con `RequestFactory` dentro de `transaction.atomic()` + `set_rollback(True)`
(patrón `retailmind/_test_reportes_readonly.py`; guarda anti-escritura activa: **0
escrituras** detectadas en las 6 invocaciones de vista de esta revisión).

---

## 0. Veredicto

### 0.1 En una tabla

| # | Propuesta | Veredicto | Razón medida (22-ago) |
|---|---|---|---|
| 1a | **`("ProductoTalla_id", fecha) WHERE estado='COMPLETADO'`** — índice **parcial PT-leading** (forma nueva de esta revisión) | **SÍ, si y solo si no se toca el código** | Es la **única** forma de índice que el planificador llegaría a usar en la consulta real. Medido *in situ*: la consulta más cara del endpoint pasa de **20.016 ms / 687.397 buffers** a **14.293 ms / 387.537 buffers** (1,40× / 1,77×) y el nodo `SubPlan 1` de **12.418 ms / 675.093** a **7.500 ms / 375.233**. Costo **~75 MB**. Pero **un cambio de 1 línea produce exactamente el mismo plan con 0 MB** (§4.6). |
| 1b | `(fecha, "ProductoTalla_id") WHERE estado='COMPLETADO'` — el índice **fecha-leading** que recomendaba la revisión 1 | **NO** | Dentro de la consulta real, el planificador elige `Unique → Index Scan` sobre el índice **PT-leading** en **las 12 configuraciones** probadas (`work_mem` de 2 MB a 1 GB × paralelismo 0 y 2), siempre con el mismo costo estimado de 105.755. Un índice fecha-leading **no puede alimentar ese `Unique` en streaming**, así que no sería elegido; y si lo fuera, cambiaría un `Unique` gratis por un `HashAggregate` que con `work_mem=2 MB` **derrama 33,5 MB a disco** por ejecución (§4.4). |
| 2 | `("ProductoTalla_id", fecha, hora, id)` | **NO** | El nodo que ataca (`Sort` del `DISTINCT ON` de productos-origen) es una fracción de un endpoint cuyo cuello está en otra parte, y el A/B muestra que **el planificador no cambia de plan** ni cuando el orden pedido ya está cubierto por un índice existente (re-medido, §5). Costo **~113 MB**. |
| 3 | `(fecha, concepto) WHERE estado='COMPLETADO'` (variante mencionada) | **NO por ahora** | Mete `concepto` en el `Index Cond` y ahorra ~96 ms y ~9.500 buffers por scan, pero ese scan es ~2,8 % de plan-liquidación, cuyo cuello es `app_producto_talla`. |
| — | Palancas de **código** (0 MB) que van ANTES de cualquier índice | **SÍ** | Memoizar el set de ids: la consulta más cara pasa de 20.016 ms a **848 ms de ejecución** — pero con **6.309 ms de planificación** y **1,13 MB de SQL** por consulta, que hay que dimensionar antes (§4.7). Quitar `estado='COMPLETADO'` de `productos_activos_qs`: **1,40× / 1,77× medido in situ, 0 SKU de diferencia**. |
| — | Palancas de **mantenimiento** (0 MB) | **SÍ, ya** | `VACUUM (ANALYZE)` — nunca se ha corrido (`vacuum_count = analyze_count = 0`, `last_analyze = NULL` en las 5 tablas; visibility map al 66,0 %). Es lo que convierte los **235.686 heap fetches** del mejor plan disponible en casi cero. · Borrar el índice **duplicado exacto** `dte_receptor_idx` (4,8 MB). |

### 0.2 Qué cambió respecto de la revisión 1, y por qué

| Hallazgo de la verificación | Qué decía la revisión 1 | Qué mide esta revisión | Consecuencia |
|---|---|---|---|
| **P1** — el proxy estaba confundido | «A.3: proxy del índice propuesto = 290,4 ms, 3,7× más rápido». El proxy medía `SELECT DISTINCT tipo_movimiento`, que tiene **2 grupos**, y lo extrapolaba a `DISTINCT "ProductoTalla_id"`, que tiene **173.939**. Además era el único de los tres que corría con plan paralelo. | Proxy justo (`DISTINCT hora`, **125.609 grupos**, mismo índice fecha-leading, **mismo paralelismo**, serial): **4.989 ms mediana / 46.465 buffers** contra 10.124 ms / 675.021 de la consulta de hoy. | El titular baja de **3,7× a 2,0×** en tiempo y de **36,8× a 14,5×** en buffers. Y el paralelismo del proxy viejo era un artefacto de tener 2 grupos: con cardinalidad real, PostgreSQL **no** paraleliza (medido). |
| **P2** — mecanismo omitido | No mencionaba que el plan de hoy hace la deduplicación **en streaming**. | El plan de hoy es `Unique → Index Scan`: **sin `Sort`, sin `HashAggregate`, sin memoria, sin disco**. El índice fecha-leading obliga a un `HashAggregate` que con `work_mem=2 MB` **derrama 20.256 kB en 5 tandas y escribe 4.192 bloques (33,5 MB) de temporales** por ejecución. | El beneficio del índice recomendado estaba **sobreestimado**, y su costo oculto (I/O de temporales en una instancia compartida que además atiende el POS) no estaba contabilizado. |
| **P3** — escepticismo asimétrico | Rechazaba la Propuesta 2 con un A/B, pero afirmaba sin prueba que con la Propuesta 1 «el nodo desaparece». | Barrido del **riesgo espejo** con la misma vara: 12 configuraciones (`work_mem` 2 MB…1 GB × paralelismo 0 y 2) sobre la consulta **real**. En **todas**, el `SubPlan` se queda en `Unique → Index Scan` PT-leading, costo 105.755. | El índice fecha-leading **probablemente nunca se usaría**. La Propuesta 1 se reformula a PT-leading (1a) y la forma fecha-leading (1b) se rechaza. |
| **P4** — criterio de aceptación no reproducible | «Objetivo: `Execution Time` ≤ 550 ms». | La misma consulta con el mismo plan, medida **10 veces** entre las 17:13 y las 17:26, dio entre **8.398 y 13.433 ms** (dispersión 1,60×) con los **buffers idénticos a ±0,01 %** (675.021…675.084). Contra la revisión 1 (674.824, cuatro horas antes) la diferencia de buffers es **0,04 %**. | El criterio de aceptación pasa a ser **buffers + forma del plan + nombre del índice elegido + heap fetches**. El tiempo de pared solo se admite como **razón contra una línea base tomada en la misma sesión** (§6.4). |
| **P5** — Nivel 1.1 sin medir | «Beneficio medido: el endpoint pasa de 111-127 s a ~15-25 s», en una tabla titulada «Beneficio medido», sin haberlo medido, y proponiendo `list(...)`. | Medido: universo = **173.294 ids**, literal de **1,13 MB**; la consulta más cara baja a **848 ms de ejecución** pero sube a **6.309 ms de planificación**. | La proyección «15-25 s» no se sostiene: hay que contar la planificación (§4.7). El beneficio sigue siendo el mayor de todos, pero la implementación importa. |
| **P6** — error factual | «Cardinalidades reales: … `estado` **1**». | La cardinalidad **real** de `estado` es **2** (`COMPLETADO` + `CANCELADO`); el 1,0 es el **estimador `n_distinct` de `pg_stats`**. `hora` es **206.152**, no 205.985. | Corregido en §2.2, que ya no se contradice con §4.1. |
| **P7** — deriva de datos | Números presentados como estables. | Dos `SELECT` consecutivos separados por segundos devolvieron **2.576.486** y **2.576.490** filas. | Todos los números de esta revisión llevan hora. |
| **P8** — reproducibilidad | 11 scripts que solo viven en el scratchpad de una sesión. | El **SQL exacto** de cada medición queda transcrito en el anexo §8 del propio documento. | El rastro sobrevive al scratchpad. |

---

## 1. Método, entorno y la métrica de aceptación

### 1.1 Entorno (medido 22-ago 17:09 Santiago / 21:09 UTC)

| Dato | Valor |
|---|---|
| Motor | PostgreSQL 17.11 x86_64 (DigitalOcean managed) |
| BD / host | `retail` @ `db-postgresql-hotelinn-do-user-6152671-0.k.db.ondigitalocean.com` |
| `pg_postmaster_start_time()` | **2026-08-18 20:18:06 UTC** (uptime **4 d 0 h 51 m**) |
| `pg_is_in_recovery()` | `false` (es el primario, no una réplica) |
| `shared_buffers` | 50.048 × 8 kB = **391 MB** |
| `work_mem` | 2.048 kB = **2 MB** |
| `maintenance_work_mem` | 152.576 kB = **149 MB** |
| `effective_cache_size` | 150.016 × 8 kB = 1,14 GB |
| `random_page_cost` / `seq_page_cost` | 1 / 1 |
| `max_parallel_workers_per_gather` | 2 |
| `default_statistics_target` | 100 |

> **ADVERTENCIA sobre `idx_scan`.** Los contadores de `pg_stat_user_*` se perdieron con el
> arranque del 18-ago. **Todo `idx_scan` de este documento cubre solo 4 días y 1 hora.** Un
> `idx_scan = 0` en esa ventana **NO prueba** que el índice esté muerto. Los candidatos a
> borrar se justifican por **redundancia estructural**, no por el contador.

> **ADVERTENCIA sobre los datos vivos.** La tabla recibe escrituras del POS mientras se
> mide. Entre dos `SELECT` consecutivos de esta misma sesión, `app_movimientos_producto`
> pasó de **2.576.486** a **2.576.490** filas. Los conteos absolutos de este documento son
> **fotos con hora**, no constantes. Los ratios y los bytes/fila sí son estables.

### 1.2 Por qué el tiempo de pared NO sirve como criterio (cierre de P4)

La consulta `A.1` (§4.1) se midió **10 veces con el mismo plan** entre las 17:13 y las
17:26 — tres scripts distintos, alternando el paralelismo, `work_mem = 2 MB` en todas:
13.151 · 9.995 · 8.398 · 9.045 · 10.124 · 12.964 · 11.567 · 12.326 · 13.433 · 10.666 ms.

| Métrica | Dispersión observada |
|---|---|
| `Execution Time` | **8.398 → 13.433 ms** (1,60×) |
| `Buffers` (hit + read) | 675.021 → 675.084 (**+0,009 %**) |
| `Buffers` contra la revisión 1 (674.824, cuatro horas antes) | **+0,04 %** |
| `Rows` del `Unique` | 173.939 → 173.982 (crecimiento real de la tabla) |
| Forma del plan | idéntica, nodo por nodo |

El trabajo físico es el mismo; lo que se mueve es la contención de CPU en una instancia
compartida. **Conclusión operativa: cualquier compuerta de aceptación expresada en
milisegundos absolutos producirá falsos negativos.** La métrica de aceptación de este
documento es, en este orden:

1. **el nombre del índice que el planificador elige** (`Index Name` en el `EXPLAIN`),
2. **la forma del plan** (`Index Only Scan` vs `Index Scan`, presencia de `Filter`,
   presencia de `Sort`/`HashAggregate`),
3. **`Buffers`** (hit + read) y **`Heap Fetches`**,
4. **`Temp Read/Written Blocks`** (derrames),
5. y solo entonces, el tiempo — **como razón contra una línea base tomada en la misma
   sesión, minutos antes**, nunca como umbral absoluto.

---

## 2. (a) Inventario de índices existentes

### 2.1 Tamaño de las tablas (22-ago 17:09)

| Tabla | Filas (`COUNT` real / `reltuples`) | Total | Heap | Índices | # índices |
|---|---|---|---|---|---|
| `app_movimientos_producto` | **2.576.486** / 2.554.477 | 892 MB | 516 MB | **376 MB** | **12** |
| `app_dte` | — / 680.930 | 200 MB | 129 MB | 71 MB | 12 |
| `app_producto_talla` | 608.797 / 606.574 | 73 MB | 42 MB | 31 MB | 3 |
| `app_producto` | — / 138.377 | 59 MB | 35 MB | 24 MB | 9 |
| `app_ticket` | — / 19.877 | 12 MB | 7.120 kB | 4.864 kB | 11 |
| `app_ticket_productos` | — / 31.780 | 6.560 kB | 4.120 kB | 2.400 kB | 4 |

### 2.2 `app_movimientos_producto` — los 12 índices

`B/fila` = `pg_relation_size / 2.576.486` (ancla para estimar el tamaño de un índice nuevo).
`idx_scan` cubre la ventana de 4 d 1 h.

| Índice | Columnas | Bytes | B/fila | `idx_scan` |
|---|---|---|---|---|
| `app_movimie_Product_747217_idx` | `("ProductoTalla_id", fecha)` | 77.447.168 | **30,06** | **48.999.445** |
| `app_movimientos_producto_ProductoTalla_id_5e4067f4` | `("ProductoTalla_id")` | 33.693.696 | 13,08 | 5.584.136 |
| `app_movimientos_producto_dte_id_fa21752b` | `(dte_id)` | 21.233.664 | 8,24 | 421.829 |
| `app_movimie_concept_dc5da8_idx` | `(concepto, estado)` | 21.102.592 | 8,19 | 184.030 |
| `app_movimientos_producto_sucursal_destino_id_f325cbe4` | `(sucursal_destino_id)` | 20.496.384 | 7,96 | 183.387 |
| `app_movimientos_producto_pkey` | `(id)` PK | 70.926.336 | 27,53 | 3.259 |
| `app_movimie_fecha_cdfa97_idx` | `(fecha DESC, hora DESC)` | 33.103.872 | 12,85 | 1.657 |
| `app_movimientos_producto_ticket_id_77b40c0e` | `(ticket_id)` | 21.471.232 | 8,33 | 1.199 |
| `app_movimie_fecha_7c3fd8_idx` | `(fecha, tipo_movimiento)` | 25.518.080 | 9,90 | 498 |
| `app_movimie_sucursa_ce69b6_idx` | `(sucursal_origen_id, fecha)` | 25.321.472 | 9,83 | 181 |
| `app_movimientos_producto_sucursal_origen_id_04b9dfaf` | `(sucursal_origen_id)` | 20.381.696 | 7,91 | 33 |
| `app_movimie_sucursa_eb04f6_idx` | `(sucursal_destino_id, fecha)` | 23.306.240 | 9,05 | **12** |

**Cardinalidades (22-ago 17:09) — distinguiendo el dato del estimador (cierre de P6):**

| Columna | Cardinalidad **real** (`COUNT(DISTINCT …)`) | Estimador `n_distinct` de `pg_stats` |
|---|---|---|
| `estado` | **2** (`COMPLETADO` + `CANCELADO`) | **1,0** · `MCV = {COMPLETADO}`, `freq = 1` |
| `"ProductoTalla_id"` | 455.997 | 94.746 |
| `fecha` | 2.774 | 2.536 |
| `hora` | **206.152** | 51.972 |
| `concepto` | 27 | — |
| `tipo_movimiento` | 2 | 2,0 |

El estimador de `estado` vale 1,0 porque `CANCELADO` son 4 filas de 2,58 M y no alcanzan a
entrar en el histograma. Eso es lo que hace que el planificador trate `estado` como una
constante — que es el punto que importa —, pero **no** es la cardinalidad real, y decirlo
al revés contradecía a §4.1 del propio documento.

Nota de tamaño: los índices con columnas repetitivas se comprimen con la **deduplicación de
btree** (PG13+). Por eso `(fecha, hora)` pesa 12,85 B/fila y `(concepto, estado)` 8,19,
mientras `("ProductoTalla_id", fecha)` — cuyo par es casi único — pesa 30,06. Esto es
determinante para estimar los índices nuevos (§4.9).

### 2.3 Los otros modelos (extracto)

*En esta revisión solo se re-midió `app_movimientos_producto`; los `idx_scan` de abajo son
los de la revisión 1 (verificados de forma independiente por la verificación adversarial),
redondeados porque siguen subiendo.*

| Tabla | Índice | Tamaño | `idx_scan` (~4 d) |
|---|---|---|---|
| `app_producto_talla` | `app_producto_talla_producto_id_614bce9a` | 7.072 kB | ~44 M |
| | `app_producto_talla_pkey` | 13 MB | ~11,7 M |
| | `prodtalla_sku_idx (sku)` | 11 MB | 24.292 |
| `app_loteproducto` | `..._producto_talla_id_5d72b452` | 2.352 kB | ~61 M |
| | `app_lotepro_agotado_cd7fa4_idx (agotado, activo)` | 752 kB | **0** |
| | `app_lotepro_fecha_v_df78cf_idx (fecha_vencimiento)` | 616 kB | **0** |
| `app_dte` | `dte_receptor_idx (receptor_id)` | 4.832 kB | ~300 k |
| | **`app_dte_receptor_id_612a5144 (receptor_id)`** | **4.856 kB** | **0** |
| | `app_dte_vendedor_id_70e63ae8 (vendedor_id)` | 5.008 kB | **0** |
| `app_ticket_productos` | `..._promo_campana_id_b6dc9e7a` | 280 kB | **0** |

### 2.4 Candidatos a borrar — SOLO REPORTADOS

**Duplicado exacto (no depende del contador de 4 días):** `app_dte` tiene
`dte_receptor_idx (receptor_id)` y `app_dte_receptor_id_612a5144 (receptor_id)` con la
**misma definición**. El segundo es el índice automático de la FK (Django no puede
prescindir de él); el redundante es el declarado en `Meta`, en `app/models/dte.py:397`.
Quitarlo libera **4,8 MB** y una escritura de índice por cada DTE. *(Requiere tocar
`app/models/dte.py` + migración — archivo ajeno a esta fase, se reporta.)*

**Sin uso en la ventana de 4 días** (re-medir con ≥30 días antes de decidir):
`app_dte(vendedor_id)` 5.008 kB · `app_loteproducto(agotado, activo)` 752 kB ·
`app_loteproducto(fecha_vencimiento)` 616 kB · `app_ticket_productos(promo_campana_id)`
280 kB · `app_movimientos_producto(sucursal_destino_id, fecha)` **22 MB** (12 scans) ·
`app_movimientos_producto(sucursal_origen_id)` 19 MB (33 scans). Fuera del alcance de esta
fase: **`app_tomainventariodetalle` tiene 6 índices con 0 scans que suman ~55 MB**.

### 2.5 Salud de vacuum / visibility map — importa para cualquier índice

Ninguna de las tablas medidas registra un `VACUUM` ni un `ANALYZE`
(`vacuum_count = autovacuum_count = analyze_count = 0`, `last_analyze` y `last_autovacuum`
en `NULL`; la única excepción es un `autoanalyze` de `app_ticket` el 21-ago).

| Tabla | `relpages` | `relallvisible` | % del heap marcado all-visible |
|---|---|---|---|
| `app_movimientos_producto` | 65.255 | 43.046 | **66,0 %** |
| `app_producto_talla` | 5.181 | 2.572 | **49,6 %** |
| `app_producto` | 4.501 | 1.732 | **38,5 %** |
| `app_ticket_productos` | 439 | 326 | 74,3 % |
| `app_ticket` | 866 | 384 | 44,3 % |
| `app_dte` | 15.880 | 15.870 | 99,9 % |

**Consecuencia medida y directamente relevante para la decisión de índices:** el mejor plan
disponible hoy para el nodo caro (§4.6) es un `Index Only Scan` que **todavía hace 235.686
heap fetches** de 721.797 filas, exactamente porque el 34 % del heap no está marcado
all-visible. Un `VACUUM` acerca ese número a cero **sin gastar un solo MB de índice**.

---

## 3. (b) Dónde está realmente el tiempo

Las vistas se invocaron con `RequestFactory`, usuario `javier` (superusuario), sucursal 7 /
empresa 1320. Los endpoints con gate nuevo (`productos_origen`, `inteligencia_compra`) se
perfilaron con un **monkeypatch en memoria de `PermisoRol.tiene_permiso`** (solo en el
proceso de medición, sin ninguna escritura).

| Endpoint | Wall | Queries | Nodo dominante | Costo del nodo |
|---|---|---|---|---|
| `api_rendimiento_compras` (anio=2026) | **144,8 / 148,3 / 155,6 / 159,3 / 174,7 s** en cinco corridas del 22-ago 17:26-17:41 | 19 | `SubPlan 1` de `productos_activos_qs`, dentro de un `Seq Scan on app_producto_talla` | **10 de 19 consultas** lo llevan = **93,1-94,7 % del SQL**. En la consulta más cara: **12.418 ms y 675.093 buffers = 64,7 % del tiempo y 98,2 % de los buffers de esa consulta** |
| `obtener_plan_liquidacion` | 8,24 s | 13 | `Seq Scan on app_producto_talla` con `hashed SubPlan` (**1.859 ms**, descarta 576.479 de 608.754) + `Parallel Hash` (1.034 ms) | El scan de movimientos es solo **428 ms** de 987 ms en la q#4 |
| `api_productos_por_origen` (anio=2026) | 6,04 / 8,68 s | 7 | **Paso 2, anti-join**: `Nested Loop Anti Join`, 65.819 probes sobre el índice de **1 columna** `("ProductoTalla_id")` con `fecha` como `Filter` | **2.018 ms / 358.506 buffers**. El `DISTINCT ON` del paso 4 es **0,25-0,28 s** |
| `obtener_resumen_existencias` (histórico) | 2,91 s | 9 | `Seq Scan on app_producto_talla`: **947 ms**, descarta 571.088 filas | q#2 **1.828 ms** |
| `obtener_inteligencia_compra` (marca 299) | 3,71 s | 20 | Sin nodo dominante: 20 consultas de 0,15-0,38 s sobre `app_producto_talla ⋈ app_producto ⋈ app_sucursal` | Ninguna supera 0,38 s |

**Lectura de conjunto:** de los 5 endpoints caros, **uno solo** (`api_rendimiento_compras`)
tiene su cuello en un scan de `app_movimientos_producto`. En plan-liquidación,
resumen-histórico e inteligencia-compra el cuello es **`app_producto_talla`** (608.797
filas, 3 índices, ninguno sobre `stock`; en la ventana de 4 días se leyeron **624 M de
tuplas por Seq Scan** en esa tabla). En productos-origen el cuello es la forma de la
consulta (65.819 probes), no la falta de un índice.

---

## 4. (c) Propuesta 1 — evidencia rehecha

### 4.1 La consulta, y por qué la premisa de selectividad original es falsa

`app/utils_analitica.py:33-38` (`productos_activos_qs`) construye:

```python
ids_con_actividad = (
    Movimientos_Producto.objects
    .filter(fecha__year__gte=2024, estado='COMPLETADO')
    .values_list('ProductoTalla_id', flat=True)
    .distinct()
)
```

que Django traduce a un predicado de rango indexable (no a un `EXTRACT`):

```sql
SELECT DISTINCT U0."ProductoTalla_id" FROM app_movimientos_producto U0
WHERE U0.estado = 'COMPLETADO' AND U0.fecha >= '2024-01-01'::date
```

La propuesta original decía `(fecha) WHERE estado='COMPLETADO'` «porque todos los scans
filtran COMPLETADO + ventana de fecha». La premisa de selectividad es falsa:

```sql
-- 22-ago 17:09
SELECT estado, count(*) FROM app_movimientos_producto GROUP BY estado;
-- COMPLETADO  2.576.486
-- CANCELADO           4
```

El predicado parcial excluiría **4 filas (0,00016 %)**: el índice parcial **no es más chico**
que el completo. Y `(fecha)` sola ya está cubierta por **dos** índices con `fecha` de
cabecera. Lo que sí hace el predicado parcial es otra cosa, y es lo único que importa:
**demostrar `estado` desde la definición del índice y así habilitar un `Index Only Scan`.**

### 4.2 El A/B, rehecho con proxies comparables (cierre de P1)

Cuatro consultas sobre la misma tabla y la misma ventana (721.702-721.789 filas, según el
instante), **todas en serie** (`max_parallel_workers_per_gather = 0`), 3 repeticiones cada
una, intercaladas dentro de cada ronda para que la deriva de carga las afecte por igual.
Medido 22-ago **17:13-17:21**.

| # | Consulta | Grupos del `DISTINCT` | Plan | Tiempo (min / **mediana** / max) | Buffers | Temporales |
|---|---|---|---|---|---|---|
| **A.1** | `DISTINCT "ProductoTalla_id" … fecha ≥ 2024 AND estado='COMPLETADO'` — **lo que corre hoy** | **173.939** | `Unique → Index Scan (PT, fecha)`, `Filter: estado`, `Rows Removed: 4` | 8.398 / **10.124** / 13.151 ms | **675.021** | 0 |
| **A.2** | idéntica, **sin** el predicado `estado` | 173.939 | `Unique → Index Only Scan (PT, fecha)`, `Heap Fetches: 235.578` | 4.511 / **5.083** / 5.877 ms | **375.144** | 0 |
| **A.3d** | `DISTINCT tipo_movimiento … fecha ≥ 2024` — **el proxy de la revisión 1** | **2** | `HashAggregate → Index Only Scan (fecha, tipo_movimiento)` | 1.937 / **2.346** / 2.507 ms | 18.281 | 0 |
| **A.3f** | `DISTINCT hora … fecha ≥ 2024` — **proxy justo**: índice fecha-leading, alta cardinalidad, mismo paralelismo | **125.609** | `HashAggregate → Index Only Scan (fecha DESC, hora DESC)`, `Heap Fetches: 235.177` | 4.703 / **4.989** / 5.437 ms | **46.465** | **`Disk 20.256 kB`, 5 tandas, 4.192 bloques escritos (33,5 MB)** |

Tres cosas que la revisión 1 no veía:

1. **A.3d no es un proxy de nada.** Deduplicar 721.789 filas en **2** grupos y en **173.939**
   grupos son trabajos distintos: la diferencia de cardinalidad es de un factor **87.000**.
   Al reemplazarlo por A.3f, el titular baja de **3,7× a 2,03×** (10.124 / 4.989) y los
   buffers de **36,8× a 14,5×** (675.021 / 46.465).
2. **El paralelismo de A.3d era un artefacto.** Con `max_parallel_workers_per_gather = 2`,
   **solo A.3d** obtiene un plan paralelo (`Unique → Gather Merge → Sort → HashAggregate`);
   A.1, A.2 y A.3f eligen el mismo plan serie. Es decir: la comparación de la revisión 1
   ponía workers de un lado y no del otro **por la propia cardinalidad del proxy**.
3. **A.3f derrama a disco y A.1 no.** Ver §4.3.

### 4.3 El mecanismo que faltaba: hoy la deduplicación es gratis (cierre de P2)

El plan de hoy es:

```
Unique  (rows=173.982)
  ->  Index Scan using "app_movimie_Product_747217_idx"   <-- ("ProductoTalla_id", fecha)
        Index Cond: (fecha >= '2024-01-01'::date)
        Filter: ((estado)::text = 'COMPLETADO'::text)
        Rows Removed by Filter: 4
```

**No hay `Sort` ni `HashAggregate`.** El índice está ordenado por `"ProductoTalla_id"`, así
que las filas llegan agrupadas y `Unique` deduplica **en streaming**: memoria constante,
cero temporales. La revisión 1 no mencionaba esto en ninguna parte, y por eso contabilizaba
como puro beneficio algo que en realidad es un **intercambio**:

| | Hoy (`(PT, fecha)`) | Con un índice fecha-leading |
|---|---|---|
| Orden de salida | por `"ProductoTalla_id"` | por `fecha` |
| Deduplicación | `Unique` en **streaming**, memoria constante | `HashAggregate` de 721.789 filas en 173.939 grupos |
| Memoria | ~0 | ~13-14 MB de tabla hash (medido: `Peak Memory Usage = 13.329 kB` con 173.982 grupos) |
| Con `work_mem = 2 MB` | — | **derrama**: 5 tandas, 20.256 kB en disco, **4.192 bloques escritos** |
| Buffers | 675.021 | 46.465 (proxy A.3f) |

El derrame no es un detalle: son **33,5 MB de escritura de temporales por ejecución**, en un
endpoint que ejecuta ese nodo **10 veces por request**, sobre la misma instancia que atiende
las ventas del POS. La revisión 1 no lo contabilizaba. Y está **subestimado**: el derrame
medido corresponde a los **125.609 grupos** del proxy; con los **173.939** grupos reales
(1,38×) el derrame sería mayor.

### 4.4 El riesgo espejo, evaluado con la misma vara (cierre de P3)

La revisión 1 rechazaba la Propuesta 2 en parte porque «pedir un orden que un índice
existente ya puede entregar no cambió el plan», pero afirmaba de la Propuesta 1 que «el nodo
desaparece», sin prueba y declarando que no había instalado `hypopg`. El riesgo espejo es
evidente una vez que se ve §4.3: **si el índice PT-leading regala el `Unique`, el
planificador puede seguir prefiriéndolo aunque exista el índice nuevo.**

No se puede crear el índice (está prohibido y sería irreversible en caliente), pero sí se
puede preguntarle al planificador qué prefiere **cuando la alternativa fecha-leading ya
existe**. Dos experimentos:

**(i) En la consulta aislada, el planificador SÍ abandona el índice PT-leading** — pero solo
cuando le alcanza la memoria. Barrido de `work_mem`, serie, 3 repeticiones (17:20-17:26):

| `work_mem` | Plan elegido para A.1 | Costo estimado | Buffers | Tiempo (min/mediana/max) |
|---|---|---|---|---|
| 2 MB | `Unique → Index Scan (PT, fecha)` + `Filter` | 105.755 | **675.082** | 10.666 / 12.326 / 13.433 ms |
| 4 MB | `HashAggregate → Index Scan (fecha, tipo_movimiento)` | 83.350 | **40.885** | 7.046 / 8.567 / 9.985 ms (derrama 1.867 bloques) |
| 8 MB | ídem | 83.350 | 40.885 | 6.553 / 7.658 / 7.881 ms |
| 16 MB | ídem (`Peak Memory 13.329 kB`, 1 tanda, sin derrame) | 83.350 | 40.885 | 5.576 / **6.537** / 6.605 ms |
| 32 MB | ídem | 83.350 | 40.885 | 5.454 / 6.004 / 7.087 ms |
| 64 MB | ídem | 83.350 | 40.885 | 6.353 / 6.676 / 6.686 ms |

Esto por sí solo desmonta el titular de la revisión 1: **los 675.082 buffers no son la marca
de un índice que falta, son la marca de `work_mem = 2 MB`.** Con 16 MB y **cero MB de índice
nuevo**, el mismo `SELECT` baja a **40.885 buffers (16,5×)** usando un índice fecha-leading
que **ya existe**.

**(ii) En la consulta real del endpoint, el planificador NO abandona el índice PT-leading —
nunca.** Se capturó el SQL exacto de las 10 consultas del endpoint que llevan el subplan y
se barrió la más cara con `EXPLAIN` en 12 configuraciones (17:44):

| `work_mem` | paralelismo | Plan del `SubPlan 1` | Índice | Costo |
|---|---|---|---|---|
| 2 MB / 8 MB / 16 MB / 64 MB / 256 MB / **1 GB** | 0 | `Unique → Index Scan` | `app_movimie_Product_747217_idx` | **105.755** |
| 2 MB / 8 MB / 16 MB / 64 MB / 256 MB / **1 GB** | 2 | `Unique → Index Scan` | `app_movimie_Product_747217_idx` | **105.755** |

Doce configuraciones, el mismo plan y el mismo costo estimado. Y a nivel de endpoint el
A/B confirma la consecuencia: cuatro corridas alternadas A-B-A-B de
`api_rendimiento_compras` (17:26-17:38) dieron **152.069 ms de media con `work_mem = 2 MB`
y 151.984 ms con 16 MB: 1,00×**.

**Conclusión (P3 cerrado):** el índice **fecha-leading** de la revisión 1 se apoyaba en una
consulta aislada cuyo comportamiento **no se traslada** a la consulta real. En la consulta
real ese índice no sería elegido — el planificador insiste en un plan que exige entrada
ordenada por `"ProductoTalla_id"`, cosa que un índice fecha-leading no puede dar. Se
rechaza (1b) y se reformula la propuesta a PT-leading (1a, §4.6).

### 4.5 El nodo real, medido dentro de su plan real

Consulta más cara del endpoint (19,0-20,0 s), `EXPLAIN (ANALYZE, BUFFERS)` del SQL
literal capturado, serie, `work_mem = 2 MB`, 22-ago 17:47:

```
Aggregate                                                   20.016 ms   687.397 buffers
  Hash Join
    ├─ Bitmap Heap Scan on app_movimientos_producto            653 ms     2.384 buffers
    │    BitmapAnd de (fecha,tipo_movimiento) + (concepto,estado)
    └─ Hash                                                 19.021 ms   685.013 buffers
         Hash Join
           ├─ Seq Scan on app_producto_talla               16.873 ms   680.512 buffers
           │    SubPlan 1:  Unique                         12.418 ms   675.093 buffers   loops=1
           │      -> Index Scan using app_movimie_Product_747217_idx
           │           Filter: estado   Rows Removed: 4    11.765 ms   675.093 buffers
           └─ Hash / Seq Scan on app_producto                 927 ms     4.501 buffers
```

Tres correcciones factuales respecto de la revisión 1:

- El `SubPlan` se ejecuta **una vez por consulta** (`loops = 1`, `Workers Launched = 0`), no
  «×3 workers». La revisión 1 reportaba «5,8 s y 2.024.432 buffers por consulta»; lo medido
  es **12.418 ms y 675.093 buffers por consulta**. *(El riesgo de ×3 es real pero
  condicional: el `SubPlan` cuelga de un `Seq Scan` bajo un `Gather`, así que **si** se
  lanzaran workers cada uno lo ejecutaría. En las corridas del 22-ago no se lanzó ninguno.)*
- El plan del `SubPlan` es **idéntico** al de la consulta aislada A.1 (mismo índice, mismo
  costo 105.755, mismos ~675 k buffers): la consulta aislada **sí** es fiel a lo que corre…
  siempre que no se cambie `work_mem`, que es justamente donde deja de serlo (§4.4).
- El `SubPlan` es el **64,7 % del tiempo y el 98,2 % de los buffers** de esa consulta. En la
  corrida de captura (17:38), las 10 consultas con subplan sumaron **164,1 s** y las otras 9
  **10,5 s**, sobre un total de SQL de **174,6 s**: el `SubPlan` solo son **~124 s**.

### 4.6 Las dos formas de arreglarlo, medidas *in situ*

Sobre exactamente el mismo SQL real, misma sesión, serie, `work_mem = 2 MB` (17:47):

| Variante | `Execution` | `Planning` | Buffers | Plan del `SubPlan 1` |
|---|---|---|---|---|
| **BASE (hoy)** | 20.016 ms | 22 ms | **687.397** | `Unique → Index Scan (PT,fecha)` + `Filter: estado` |
| **Sin el predicado `estado`** (`utils_analitica.py:35`) | **14.293 ms** | 18 ms | **387.537** | `Unique → Index Only Scan (PT,fecha)`, `Heap Fetches: 235.686` |
| **Memoizada** (set de ids ya materializado) | **848 ms** | **6.309 ms** | **2.384** | *(el `SubPlan` desaparece)* |

**Un índice parcial PT-leading debería producir el mismo plan que la fila del medio.** El
mecanismo es explícito: `("ProductoTalla_id", fecha) WHERE estado='COMPLETADO'` le permite
al planificador **demostrar `estado` desde la definición del índice** (el predicado del
índice implica el de la consulta), quitar el `Filter`, y hacer `Index Only Scan`
**conservando el `Unique` en streaming** — que es exactamente lo que consigue quitar el
predicado del código. Es decir:

> El índice de 75 MB y el cambio de una línea compran **el mismo plan**:
> 20.016 → 14.293 ms y 687.397 → 387.537 buffers (**1,40× / 1,77×**) en la consulta real.

**Esto es una proyección con mecanismo, no una medición post-índice** (no se puede crear el
índice: está prohibido y `hypopg` requiere DDL). Lo que sí está medido es la comparación de
costos que el planificador haría: el plan de hoy cuesta **105.755** y el plan con
`Index Only Scan` sobre las mismas dos columnas cuesta **60.912** — 1,74× más barato, un
margen amplio, así que el riesgo de que el planificador **no** tome el índice parcial es
bajo. Aun así, ese riesgo es la razón por la que §6.4 exige verificar **qué índice eligió**
antes de dar la operación por buena, y hacer rollback si eligió el de siempre.

La diferencia entre ambos no es de rendimiento sino de tipo de riesgo: el índice cuesta
75 MB y una escritura más por venta; la línea cuesta una revisión de semántica.
**Delta de semántica medido (22-ago 17:09): 0 SKU.**
`count(DISTINCT "ProductoTalla_id")` con el predicado = **173.939**; sin el predicado =
**173.939**. Las 4 filas `CANCELADO` pertenecen a 4 SKU que ya entran al universo por sus
propios movimientos `COMPLETADO`. Riesgo residual: un SKU futuro cuyo *único* movimiento
≥2024 sea `CANCELADO` **y** con `stock = 0` — hoy no existe ninguno.

### 4.7 La memoización: el mayor beneficio, con una trampa medida (cierre de P5)

La revisión 1 ponía «memoizar el set de ids» en una tabla titulada **«Beneficio medido»**
con la proyección «el endpoint pasa de 111-127 s a ~15-25 s», sin haberlo medido, y
sugiriendo `list(...)`. Medido ahora:

| Dato | Valor (22-ago 17:47) |
|---|---|
| Tamaño del universo `productos_activos_qs()` | **173.294 ids** de 608.797 filas de `app_producto_talla` (**28,5 %**) |
| De dónde sale | 173.939 SKU con movimiento ≥2024 ∪ 41.471 con `stock > 0`, menos los de productos con `excluir_de_analitica` |
| Bytes del literal separado por comas | **1.180.074 B = 1,13 MB** |
| Traer esos ids al proceso Python (una vez) | 14,1 s con `work_mem = 64 MB` (la consulta del universo es la misma que hoy se resuelve 10 veces) |
| Consulta más cara, ejecución | 20.016 ms → **848 ms** (23,6×) |
| Consulta más cara, **planificación** | 22 ms → **6.309 ms** (287×) |
| Total por consulta | 20.038 ms → **7.158 ms** (**2,80×**, no 23,6×) |
| SQL enviado por request si se repite en las 10 consultas | **~11,3 MB** |

El beneficio sigue siendo el mayor de la lista, pero **la proyección de la revisión 1 no se
sostiene**: extrapolando las 10 consultas (7,2 s cada una: 0,85 de ejecución + 6,3 de
planificación) más las 9 que no llevan el subplan (10,5 s medidos), el endpoint quedaría en
el orden de **80-90 s**, no de 15-25 s. Con la implementación buena (array como parámetro,
que planifica en la mitad: §4.8) la banda razonable es **50-90 s**, y sigue siendo el mayor
ahorro disponible: de los **145-175 s de SQL** medidos en cinco corridas, a la mitad o
menos, con 0 MB. Dos advertencias que la revisión 1 no daba:

1. **`list(...)` en Django no genera `= ANY(array)`.** `.filter(id__in=<lista python>)`
   genera `IN (%s, %s, …)` con 173.294 marcadores que psycopg2 interpola del lado del
   cliente: el servidor recibe y **parsea** más de 1 MB de literales por consulta. La cifra
   de 6.309 ms de planificación de arriba corresponde a la forma **más favorable**
   (`= ANY(ARRAY[…])`); la forma que Django emite se midió aparte (§4.8).
2. **Hay implementaciones mejores que materializar.** Pasar el set como un único parámetro
   de tipo array, o reestructurar para que el universo se calcule una sola vez del lado del
   servidor (CTE materializada / tabla temporal por request), evita tanto las 9 ejecuciones
   redundantes **como** el megabyte de SQL. Eso es diseño de código y queda fuera de esta
   fase; lo que este documento aporta es el **presupuesto**: 173.294 ids, 1,13 MB, 6,3 s de
   planificación por consulta si se hace por la vía ingenua.

### 4.8 Las tres formas de pasar el set de ids, comparadas

Sobre una consulta sonda simple (`SELECT count(*) FROM app_producto_talla pt WHERE pt.id …`)
para aislar el efecto de la **forma** de pasar el set, sin el ruido del resto del plan.
Medido 22-ago **17:47-17:55**, en serie:

| Forma | `work_mem` | Ejecución | **Planificación** | Buffers | SQL enviado |
|---|---|---|---|---|---|
| **(a)** subconsulta inline — **lo que corre hoy** | 2 MB | 11.459 ms | 0,20 ms | 693.809 | 0,2 kB |
| **(a)** subconsulta inline | 16 MB | 9.318 ms | 0,23 ms | 46.307 | 0,2 kB |
| **(b)** `= ANY (ARRAY[…173.294 literales…])` | 2 MB | 931 ms | **1.899 ms** | 19.197 | **1,13 MB** |
| **(c)** `= ANY (%s)` con el array como **parámetro** | 2 MB | 966 ms | **944 ms** | 19.203 | binario |
| **(d)** `IN (…173.294 literales…)` — **lo que emite Django** con `.filter(id__in=<lista>)` | 2 MB | 939 ms | **1.932 ms** | 19.197 | **1,13 MB** |

Tres lecturas:

1. **Materializar el set gana muchísimo en ejecución** (11.459 → ~940 ms, 12×; 693.809 →
   19.197 buffers, 36×) y ese es el beneficio real del Nivel 1.1.
2. **Pero la planificación deja de ser gratis.** Pasa de 0,2 ms a 1,9 s en esta sonda y a
   **6,3 s en la consulta real del endpoint** (§4.6), que es más compleja. Ese costo se paga
   **en cada una de las 10 consultas**, así que el ahorro neto es real pero mucho menor que
   el bruto.
3. **La forma importa y Django elige la peor.** El array como **parámetro** (c) planifica en
   **la mitad** que los literales (944 ms vs 1.899/1.932 ms) y no manda 1,13 MB de texto por
   el cable. `.filter(id__in=<lista python>)` genera la forma (d).

*(Nota de método: en esta sonda simple, subir `work_mem` a 16 MB **sí** cambia el plan de la
variante (a) — 693.809 → 46.307 buffers —, mientras que en la consulta real del endpoint no
lo cambia en ninguna de las 12 configuraciones probadas (§4.4 ii). Es el mismo fenómeno de
fondo: **una subconsulta idéntica se planifica distinto según la consulta que la envuelve**,
y por eso las mediciones aisladas no se pueden extrapolar sin verificarlas in situ.)*

### 4.9 Costo de los índices candidatos

| Concepto | **1a** `("ProductoTalla_id", fecha) WHERE estado='COMPLETADO'` (recomendado si no se toca código) | **1b** `(fecha, "ProductoTalla_id") WHERE estado='COMPLETADO'` (rechazado) |
|---|---|---|
| **Tamaño estimado** | **~75 MB** (74-80). Ancla directa: `("ProductoTalla_id", fecha)`, **las mismas dos columnas en el mismo orden**, pesa 77.447.168 B = **30,06 B/fila**. El predicado parcial quita 4 filas: no reduce nada. | **~75 MB** (mismas dos columnas; el orden no cambia el ancho ni la deduplicación, porque el par es casi único en ambos sentidos) |
| **Footprint de la tabla** | índices 376 → **~451 MB** (+20 %); relación 892 → **~967 MB** (+8,4 %) | ídem |
| **`shared_buffers`** | 75 MB de 391 MB = **19 %** | ídem |
| **Escrituras del POS** | 12 → 13 índices = **+8,3 %** de mantenimiento por `INSERT`. `fecha` es la 2.ª columna, así que las inserciones se reparten por `"ProductoTalla_id"`: **sí genera page splits**, igual que el índice gemelo que ya existe | 12 → 13 = **+8,3 %**. `fecha` es monótona creciente ⇒ las inserciones caen en la página más a la derecha, **sin splits aleatorios** (es su única ventaja) |
| **Volumen real de escritura** | ver §4.10 | ídem |
| **Construcción** | ~1-4 min con `CONCURRENTLY` (2 pasadas sobre 516 MB de heap; el sort de 2,58 M × ~30 B ≈ 77 MB cabe en `maintenance_work_mem` = 149 MB). Sin bloqueo de escrituras | ídem |
| **Duplicación** | **Es un cuasi-duplicado** del índice `app_movimie_Product_747217_idx` (48,9 M scans en 4 días): mismas columnas, mismo orden, difiere solo en el predicado parcial. Ese es su costo conceptual: 75 MB para agregar 4 filas de selectividad y un `Index Only Scan` | No duplica nada, pero **no sería elegido** (§4.4) |
| **Beneficio medido** *in situ* | 20.016 → **14.293 ms**, 687.397 → **387.537 buffers** | **0** mientras el planificador no lo elija; y si lo eligiera, cambiaría el `Unique` gratis por un `HashAggregate` que derrama 33,5 MB (§4.3) |

### 4.10 Volumen real de escritura del POS

*(Medido 22-ago 17:55.)*

| Métrica | Valor |
|---|---|
| Movimientos en los últimos 90 días | **40.255** en 91 días con movimiento = **442,4 / día** |
| Día más cargado / más flojo de esos 90 | **2.208** / **25** |
| Movimientos en los últimos 30 días | 16.645 |
| `n_tup_ins` / `n_tup_upd` desde el arranque (4 d 1 h) | 2.147 / 1.025 |

Un índice más significa **~442 entradas de índice extra al día** en promedio, **2.208 en el
peor día medido**. Sobre 12 índices existentes, el 13.º añade **+8,3 %** de trabajo de
mantenimiento por `INSERT`. En términos absolutos es despreciable para el POS: el costo de
un índice aquí **no es la latencia de la venta, son los 75 MB de `shared_buffers` y el
cuasi-duplicado permanente** (§4.9).

---

## 5. Propuesta 2 — `("ProductoTalla_id", fecha, hora, id)`: **NO**

Para no repetir el escepticismo asimétrico que señaló la verificación, esta propuesta se
re-midió con la misma vara que la Propuesta 1: A/B propio, en la misma sesión, en serie, y
declarando qué prueba **no** se pudo hacer.

### 5.1 El A/B, re-medido (22-ago 17:19)

Sobre el `DISTINCT ON` acotado al año 2026 e `INGRESO` (81.584 filas de entrada, 65.866 de
salida), **sin** la lista literal de 20.655 ids, para que la medición no arrastre el costo
de enviar 166 kB de SQL. *(Por eso mis 5.A/5.B no son idénticas al paso 4 del pipeline real
—que sí lleva la lista y resuelve el scan con un `BitmapAnd`—: aíslan la pregunta que
importa, que es si el planificador cambia de índice cuando cambia el `ORDER BY`.)*

| # | Consulta | Plan | Tiempo | Buffers | Temporales |
|---|---|---|---|---|---|
| 5.A | `DISTINCT ON (PT) … ORDER BY PT, fecha, hora, id` — **la de hoy** | `Unique → Sort (external merge 3.360 kB) → Index Scan (fecha, tipo_movimiento)` | 747,6 ms | 4.378 | 420/422 |
| 5.B | idéntica pero `ORDER BY PT, fecha` — **orden que el índice existente `("ProductoTalla_id", fecha)` SÍ entrega** | **el mismo**: `Unique → Sort (external merge 1.760 kB) → Index Scan (fecha, tipo_movimiento)` | 579,7 ms | 4.367 | 220/222 |

**La conclusión de la revisión 1 se confirma, pero su evidencia era más débil de lo que
parecía:** lo que prueba el A/B es que **el planificador no cambia de índice** aunque el
orden pedido esté cubierto por uno existente — prefiere leer por `fecha` (que acota el
rango) y ordenar 81.584 filas. Lo que la revisión 1 presentaba como refuerzo («y además fue
más lento: 82,2 ms vs 42,6 ms») **no es reproducible**: en mi corrida 5.B fue **más rápida**
que 5.A. Es exactamente el problema P4 — el tiempo de pared no sostiene un argumento. Lo
que sí sostiene el rechazo es la **forma del plan** y el **tamaño del nodo**.

### 5.2 El nodo que ataca es pequeño y el cuello está en otra parte

- El `Sort` que el índice eliminaría cuesta **319 ms de los 747** en mi variante 5.A
  *(re-medido)*; en la variante acotada a 20.655 SKU que corre el pipeline real, la revisión
  1 lo midió en **2,6 ms de 42,6 ms** *(no re-medido en esta revisión)*. El derrame
  (`external merge`) es consecuencia de `work_mem = 2 MB`, no de la falta de índice:
  **subir `work_mem` lo elimina gratis**.
- En el endpoint completo, la consulta del paso 4 es **0,250-0,282 s de 6,04-8,68 s**
  *(revisión 1, no re-medido)*.
- El **cuello real** de productos-origen es el anti-join del paso 2: **2.018 ms / 358.506
  buffers**, 65.819 probes con el índice de 1 columna y `fecha` como `Filter`. Reescrituras
  probadas: rango cerrado 1.823 ms (mismo plan); `LATERAL … LIMIT 1` sí flipea al índice de
  2 columnas (−29 % buffers) pero es **peor**: 2.919 ms. **Ningún índice de las dos
  propuestas lo arregla**; el arreglo es de forma de consulta.
- El «44 s» del informe original corresponde al `DISTINCT ON` **global sin acotar**, que hoy
  mide 8,78 s y que **el pipeline de la Fase C ya no ejecuta**.

### 5.3 Costo si igual se creara

**~113 MB** (rango 105-125). `id` es único ⇒ **deduplicación de btree = 0**. Payload
alineado: `bigint 8 + date 4 (+4 relleno) + time 8 + bigint 8 = 32 B` + 8 de cabecera + 4 de
line pointer = **44 B/fila × 2,58 M**. Anclas: `(id)` único = 27,53 B/fila; `(PT, fecha)` =
30,06. Índices +30 %, relación +13 %, **29 % de `shared_buffers`**, y **con page splits** (la
clave incluye `hora` e `id`, y el orden de inserción es por `id`, no por `PT`).

### 5.4 Lo que no se pudo probar

No se pudo demostrar que el planificador **rechazaría** el índice de 4 columnas si
existiera: para eso haría falta crearlo (prohibido) o `hypopg` (requiere `CREATE EXTENSION`,
que es DDL; se verificó que no está instalado, igual que `pg_stat_statements` y
`pgstattuple`). Lo que sí está medido es la evidencia indirecta de §5.1: con
`= ANY(20.655 ids)` acotando el scan, el planificador prefiere leer por `fecha` y ordenar, y
un índice **más ancho** (4 columnas, 113 MB) tiene menos probabilidad de ser elegido, no más.

---

## 6. (d) SQL exacto: creación, verificación y rollback

Esta sección aplica **solo al índice 1a** (`("ProductoTalla_id", fecha) WHERE
estado='COMPLETADO'`), que es el único con veredicto favorable, y **solo si el usuario
decide no tocar el código** (§4.6: la línea de `utils_analitica.py` compra el mismo plan con
0 MB).

### 6.1 Convención de nombres del repo

Los índices nombrados a mano siguen `<tabla_abrev>_<cols_abrev>_idx` (máx. 30 caracteres,
límite de Django): `dte_suc_fecha_idx`, `dte_receptor_idx`, `prodtalla_sku_idx`,
`ticket_vend_fecha_idx`, etc. Nombre elegido: **`mov_pt_fecha_compl_idx`** (22 caracteres).

### 6.2 Antes de empezar: el caso «CONCURRENTLY a medias»

`CREATE INDEX CONCURRENTLY` **espera** a que terminen todas las transacciones abiertas más
antiguas que él. Si hay una sesión `idle in transaction`, la creación se cuelga; si se
cancela a mitad, **queda un índice `indisvalid = false`** que sigue costando escrituras y
que **nunca se usa para leer**.

```sql
-- 1) transacciones largas que bloquearían el CONCURRENTLY
SELECT pid, usename, state, xact_start, now() - xact_start AS edad,
       left(query, 100) AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND xact_start IS NOT NULL
  AND now() - xact_start > interval '30 seconds'
ORDER BY xact_start;

-- 2) que no haya quedado un intento anterior inválido
SELECT i.relname, x.indisvalid, x.indisready, x.indislive
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
WHERE NOT x.indisvalid;
```

Si (1) devuelve filas, esperar o cerrar esas sesiones. Si (2) devuelve algo, limpiarlo
(§6.5) antes de reintentar.

### 6.3 Creación

`CREATE INDEX CONCURRENTLY` **no puede correr dentro de un bloque de transacción**. En
`psql`, ejecutarlo suelto (autocommit), sin `BEGIN`/`COMMIT`:

```sql
-- Antes: tomar la línea base EN LA MISMA SESIÓN (ver §6.4)
EXPLAIN (ANALYZE, BUFFERS)
SELECT DISTINCT "ProductoTalla_id" FROM app_movimientos_producto
WHERE fecha >= '2024-01-01'::date AND estado = 'COMPLETADO';

-- app_movimientos_producto ~2,58 M filas / 516 MB de heap.
-- Duración estimada: 1-4 min. Toma SHARE UPDATE EXCLUSIVE: NO bloquea INSERT/UPDATE
-- del POS; sí bloquea otro DDL y el VACUUM sobre esta tabla.
CREATE INDEX CONCURRENTLY IF NOT EXISTS mov_pt_fecha_compl_idx
    ON public.app_movimientos_producto
    USING btree ("ProductoTalla_id", fecha)
    WHERE estado = 'COMPLETADO';

-- estadísticas frescas + visibility map (sin esto, el Index Only Scan sigue
-- haciendo ~235.000 heap fetches: ver §2.5)
VACUUM (ANALYZE) public.app_movimientos_producto;
```

Las comillas dobles de `"ProductoTalla_id"` son obligatorias (la columna tiene mayúsculas).

### 6.4 Verificación — criterio de aceptación reproducible (cierre de P4)

**Paso 1 — que el índice quedó VÁLIDO.** Este chequeo sí es binario y no depende de la
carga:

```sql
SELECT i.relname                               AS indice,
       x.indisvalid                            AS valido,
       x.indisready                            AS listo,
       x.indislive                             AS vivo,
       pg_size_pretty(pg_relation_size(i.oid)) AS tamano,
       pg_get_indexdef(i.oid)                  AS ddl
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
WHERE t.relname = 'app_movimientos_producto'
  AND i.relname = 'mov_pt_fecha_compl_idx';
```

Esperado: `valido = t`, `listo = t`, `vivo = t`, tamaño **70-85 MB**. **Cualquier `f`
significa que la construcción no terminó ⇒ ir a §6.5 (rollback) y reintentar.**

**Paso 2 — que el planificador lo elige y el plan cambió de forma.** Correr el `EXPLAIN` de
§6.3 otra vez, **en la misma sesión** que la línea base:

| Métrica | Antes (medido 22-ago 17:13-17:26, 10 corridas) | Criterio de aceptación |
|---|---|---|
| `Index Name` | `app_movimie_Product_747217_idx` | **`mov_pt_fecha_compl_idx`**. Si sigue eligiendo el de siempre, **el índice nuevo no compró nada**: es 75 MB y una escritura por venta a cambio de cero ⇒ ir a §6.5 |
| Tipo de nodo | `Index Scan` | **`Index Only Scan`** |
| `Filter: estado` | presente, `Rows Removed by Filter: 4` | **ausente** |
| Nodo de deduplicación | `Unique` (streaming) | **`Unique`**. Si aparece `HashAggregate` o `Sort`, el planificador se fue por un camino distinto del previsto (§4.3-§4.4) y hay que volver a medir antes de conservar el índice |
| `Buffers` (hit + read) | **675.021 - 675.084** (dispersión ±0,01 %) | **≤ 400.000** *(referencia: la variante equivalente midió 375.144-387.537)* |
| `Heap Fetches` | — | reportar el valor; si es > 200.000, falta el `VACUUM` |
| `Temp Written Blocks` | 0 | **0** (si aparece derrame, el plan no es el esperado) |
| `Execution Time` | 8.398 - 13.433 ms (dispersión **1,60×**) | **solo como razón contra la línea base de la misma sesión: se espera ≈ 1,7-2,0×.** NO usar un umbral absoluto en ms |

> **Por qué el criterio de la revisión 1 estaba mal.** Pedía `Execution Time ≤ 550 ms` y
> declaraba «cualquier incumplimiento significa que la construcción no terminó: ir al
> rollback». Con la carga medida el 22-ago, la consulta **con el índice perfecto**
> reprobaría esa compuerta: la línea base sin índice ya oscila entre 8,4 y 13,4 s con los
> buffers idénticos al 0,01 %. El trabajo físico se mide en **buffers**; el reloj mide la
> instancia compartida de DigitalOcean.

**Paso 3 — que el endpoint no cambió de valores.**

```powershell
cd retailmind
python _test_reportes_readonly.py --confirmo-prod --solo rendimiento_compras
python _test_reportes_readonly.py --confirmo-prod
```

**Paso 4 — a las 24-48 h, que el índice se está usando de verdad.** Si `idx_scan` sigue en
0, el índice es puro costo de escritura y hay que borrarlo:

```sql
SELECT i.relname, s.idx_scan, s.idx_tup_read,
       pg_size_pretty(pg_relation_size(i.oid))
FROM pg_stat_user_indexes s JOIN pg_class i ON i.oid = s.indexrelid
WHERE s.relname = 'app_movimientos_producto'
ORDER BY s.idx_scan DESC;
```

### 6.5 Rollback

```sql
-- Reversa normal (no bloquea lecturas ni escrituras).
-- Tampoco puede ir dentro de un bloque de transaccion.
DROP INDEX CONCURRENTLY IF EXISTS public.mov_pt_fecha_compl_idx;
```

Si `indisvalid = false` (construcción a medias), **este `DROP` es obligatorio**: un índice
inválido se sigue manteniendo en cada `INSERT`/`UPDATE` y nunca se usa para leer, o sea que
es puro costo. Si el `DROP CONCURRENTLY` también quedara colgado por una transacción larga,
la alternativa toma `ACCESS EXCLUSIVE` unos milisegundos y hay que hacerla **fuera del
horario de caja**:

```sql
DROP INDEX IF EXISTS public.mov_pt_fecha_compl_idx;
```

### 6.6 Sincronizar el estado de Django

Si el índice se crea a mano, el estado del modelo no lo sabe y `makemigrations` seguirá
proponiéndolo. Para cerrar el círculo (cambio en `app/models/inventario.py`, archivo ajeno a
esta fase — se propone, no se aplica):

```python
# app/models/inventario.py, Movimientos_Producto.Meta.indexes
models.Index(
    fields=['ProductoTalla', 'fecha'],
    name='mov_pt_fecha_compl_idx',
    condition=Q(estado='COMPLETADO'),
),
```

y una migración **con esa única operación** (`0217_mov_pt_fecha_compl_idx.py`,
`migrations.AddIndex(...)`), que en prod se marca como aplicada sin ejecutarla:

```powershell
python manage.py migrate app 0217 --fake
```

En BD nuevas y en los tests (SQLite soporta índices parciales vía `condition`) la migración
lo crea normalmente. **No usar `AddIndex` sin `--fake` contra prod**: el `CREATE INDEX` no
concurrente toma un lock `SHARE` que bloquea todas las escrituras del POS durante 1-4
minutos. `django.contrib.postgres` **no** está en `INSTALLED_APPS`, así que
`AddIndexConcurrently` exigiría agregarlo y además rompería los tests en SQLite.

---

## 7. (e) Recomendación final priorizada

### Nivel 0 — gratis, antes que cualquier índice

| # | Acción | Evidencia medida | Riesgo |
|---|---|---|---|
| 0.1 | **`VACUUM (ANALYZE)`** en `app_movimientos_producto`, `app_producto_talla`, `app_producto`, `app_ticket` | `vacuum_count = analyze_count = 0` y `last_analyze = NULL` en las 5 tablas; VM al 66,0 / 49,6 / 38,5 / 44,3 %. El mejor plan disponible para el nodo caro hace **235.686 heap fetches** de 721.797 filas solo por eso | Bajo. Escribe (marca páginas) ⇒ **decide el usuario y se corre fuera del horario de caja**. Revisar además **por qué el autovacuum no corre** en esta instancia de DigitalOcean |
| 0.2 | **Subir `work_mem`** para las sesiones de reportes (hoy 2 MB) | **No arregla `api_rendimiento_compras`** (A/B de endpoint: 1,00×, §4.4) pero sí quita derrames en los demás: el `DISTINCT ON` de productos-origen derrama 3.360 kB, plan-liquidación escribe 1.224/2.188 bloques, resumen-histórico 1.153. En la consulta aislada A.1 el efecto es enorme (675.082 → 40.885 buffers a partir de 4 MB) | Bajo si se hace por sesión/rol, no global |
| 0.3 | **Borrar el duplicado exacto** `dte_receptor_idx` | Misma definición que `app_dte_receptor_id_612a5144`; 4,8 MB + una escritura por DTE | Bajo. Requiere tocar `app/models/dte.py:397` + migración |

### Nivel 1 — el fix que de verdad arregla `api_rendimiento_compras` (código, 0 MB)

| # | Acción | Beneficio **medido** *in situ* | Advertencia |
|---|---|---|---|
| 1.1 | **Dejar de resolver `productos_activos_qs()` 10 veces por request** (`views_modulo_reportes.py:6349`; `pt_ids` es un queryset perezoso que Django inlinea como subconsulta en cada uso) | Consulta más cara: **20.016 → 848 ms de ejecución** y **687.397 → 2.384 buffers**. Extrapolado a las 19 consultas: el endpoint bajaría de los 145-175 s de SQL medidos a **50-90 s** según la implementación | **NO es gratis**: materializar con `list(...)` sube la planificación de 22 ms a **6.309 ms** por consulta y manda **1,13 MB de SQL** (173.294 ids) ×10 = 11,25 MB por request. Pasar el set como **array-parámetro** en vez de `__in` reduce la planificación a la mitad (medido: 944 ms vs 1.932 ms en la sonda de §4.8). Elegir la implementación con §4.7-§4.8 a la vista |
| 1.2 | **Quitar `estado='COMPLETADO'` de `productos_activos_qs`** (`utils_analitica.py:35`) | Consulta real: **20.016 → 14.293 ms** y **687.397 → 387.537 buffers** (1,40× / 1,77×). El `SubPlan` pasa de `Index Scan` + `Filter` a **`Index Only Scan`** conservando el `Unique` en streaming | Cambio de semántica. **Delta medido hoy: 0 SKU** (173.939 = 173.939). Riesgo residual: un SKU futuro cuyo único movimiento ≥2024 sea `CANCELADO` y con `stock = 0`. Requiere el visto bueno del usuario |

1.1 y 1.2 componen: 1.2 abarata las 10 ejecuciones, 1.1 elimina 9 de ellas.

### Nivel 2 — el índice, solo en un escenario

| # | Acción | Cuándo tiene sentido | Costo |
|---|---|---|---|
| 2.1 | **`mov_pt_fecha_compl_idx` = `("ProductoTalla_id", fecha) WHERE estado='COMPLETADO'`** | **Solo si el usuario decide NO hacer 1.2.** Debería comprar el mismo plan y los mismos números que 1.2 (`Index Only Scan`, 387.537 buffers) sin tocar la semántica — **proyección con mecanismo, no medición post-índice** (§4.6); por eso §6.4 exige verificar qué índice eligió el planificador y hacer rollback si eligió el de siempre | ~**75 MB** (+20 % de índices de la tabla, +8,4 % de la relación, 19 % de `shared_buffers`); +8,3 % de mantenimiento por `INSERT`, con page splits; 1-4 min de construcción sin bloquear escrituras. Y es un **cuasi-duplicado** de un índice de 74 MB que ya existe |

Si se hace 1.1, este índice pierde casi todo su sentido: el nodo que abarata deja de
ejecutarse 9 de cada 10 veces.

### Nivel 3 — NO

| # | Propuesta | Motivo |
|---|---|---|
| 3.1 | **`(fecha, "ProductoTalla_id") WHERE estado='COMPLETADO'`** *(era la recomendación de la revisión 1)* | En la consulta real el planificador elige el índice PT-leading en **12 de 12** configuraciones (`work_mem` 2 MB…1 GB × paralelismo 0 y 2), siempre con costo 105.755: un índice fecha-leading **no puede alimentar ese `Unique` en streaming**, así que no sería elegido. Y si lo fuera, cambiaría una deduplicación de memoria constante por un `HashAggregate` que con `work_mem = 2 MB` **derrama 33,5 MB de temporales por ejecución**, ×10 por request |
| 3.2 | `("ProductoTalla_id", fecha, hora, id)` | 113 MB (+30 % del footprint de índices) para un `Sort` que es una fracción de un endpoint cuyo cuello es el anti-join de 2.018 ms; el A/B muestra que el planificador **no cambia de índice** ni cuando el orden pedido ya está cubierto (§5.1) |
| 3.3 | `(fecha) WHERE estado='COMPLETADO'` «a secas» | Sin `"ProductoTalla_id"` no hay `Index Only Scan`, y `(fecha)` sola ya está cubierta por dos índices existentes con `fecha` de cabecera |

### Nivel 4 — esperar y medir en otra ronda

| # | Candidato | Nodo que ataca | Estado |
|---|---|---|---|
| 4.1 | `(fecha, concepto) WHERE estado='COMPLETADO'` (~25-40 MB) | 173,6 ms → ~78 ms por scan; pero es 2,8 % de plan-liquidación | **No medido post-índice.** Reevaluar cuando el cuello de plan-liquidación deje de ser `app_producto_talla` |
| 4.2 | Algo sobre **`app_producto_talla`** (p. ej. parcial `WHERE stock > 0`) | `Seq Scan` de **1.859 ms** en plan-liquidación y **947 ms** en resumen-histórico; en 4 días esa tabla acumuló **624 M de tuplas leídas por Seq Scan** | **No propuesto en Fase C y no medido post-índice.** Es, con diferencia, el mayor candidato pendiente: aparece en 3 de los 5 endpoints caros |
| 4.3 | Borrar los índices con `idx_scan = 0` | ~55 MB solo en `app_tomainventariodetalle`, más los de §2.4 | **Re-medir con ≥30 días de estadísticas.** La ventana actual es de 4 días |

---

## 8. Reproducibilidad — el SQL exacto (cierre de P8)

Los scripts de medición viven en el scratchpad de la sesión y **no sobreviven a ella**; lo
que sigue es el SQL que ejecutaron, para poder repetir cualquier número de este documento.
Todos son `SELECT`/`EXPLAIN`: no escriben. Los `SET` son de sesión y mueren con la conexión.

**Preámbulo de toda medición comparativa** (fija el paralelismo, que es la variable que la
revisión 1 dejó suelta):

```sql
SET max_parallel_workers_per_gather = 0;   -- o 2, pero el MISMO para todas las ramas del A/B
SET work_mem = '2MB';                      -- el valor real de producción
```

**§1.1 entorno:**

```sql
SELECT now(), pg_postmaster_start_time(), pg_is_in_recovery(), version();
SELECT name, setting, unit FROM pg_settings
WHERE name IN ('work_mem','shared_buffers','maintenance_work_mem','effective_cache_size',
               'random_page_cost','seq_page_cost','max_parallel_workers_per_gather',
               'default_statistics_target');
```

**§2.2 cardinalidad real vs estimador (P6):**

```sql
SELECT count(*), count(DISTINCT estado), count(DISTINCT "ProductoTalla_id"),
       count(DISTINCT fecha), count(DISTINCT hora), count(DISTINCT concepto)
FROM app_movimientos_producto;

SELECT attname, n_distinct, most_common_vals, most_common_freqs
FROM pg_stats WHERE tablename = 'app_movimientos_producto';
```

**§2.2 tamaños y B/fila de los índices:**

```sql
SELECT i.relname, pg_get_indexdef(x.indexrelid), pg_relation_size(i.oid) AS bytes,
       round(pg_relation_size(i.oid)::numeric
             / (SELECT count(*) FROM app_movimientos_producto), 2) AS b_fila,
       s.idx_scan
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.oid
WHERE t.relname = 'app_movimientos_producto'
ORDER BY s.idx_scan DESC NULLS LAST;
```

**§2.5 visibility map y salud de vacuum:**

```sql
SELECT relname, relpages, relallvisible,
       round(100.0 * relallvisible / NULLIF(relpages,0), 1) AS pct_all_visible
FROM pg_class WHERE relname IN ('app_movimientos_producto','app_producto_talla',
                                'app_producto','app_ticket','app_dte');
SELECT relname, vacuum_count, autovacuum_count, analyze_count, last_analyze, last_autovacuum
FROM pg_stat_user_tables WHERE relname LIKE 'app_%';
```

**§4.2 el A/B de las cuatro consultas** (cada una con
`EXPLAIN (ANALYZE, BUFFERS, TIMING ON)`, 3 repeticiones, intercaladas):

```sql
-- A.1  (lo que corre hoy)
SELECT DISTINCT "ProductoTalla_id" FROM app_movimientos_producto
WHERE fecha >= '2024-01-01'::date AND estado = 'COMPLETADO';
-- A.2  (sin el predicado estado)
SELECT DISTINCT "ProductoTalla_id" FROM app_movimientos_producto
WHERE fecha >= '2024-01-01'::date;
-- A.3d (el proxy de la revision 1: 2 grupos — NO usar)
SELECT DISTINCT tipo_movimiento FROM app_movimientos_producto
WHERE fecha >= '2024-01-01'::date;
-- A.3f (proxy justo: 125.609 grupos sobre indice fecha-leading)
SELECT DISTINCT hora FROM app_movimientos_producto
WHERE fecha >= '2024-01-01'::date;
-- cardinalidad de grupos de cada uno, para poder comparar
SELECT count(*), count(DISTINCT "ProductoTalla_id"), count(DISTINCT hora),
       count(DISTINCT tipo_movimiento)
FROM app_movimientos_producto WHERE fecha >= '2024-01-01'::date;
```

**§4.4 (i) barrido de `work_mem` sobre la consulta aislada:** repetir A.1 con
`SET work_mem` en `'2MB','4MB','8MB','16MB','32MB','64MB'`.

**§4.4 (ii) barrido sobre la consulta REAL:** capturar el SQL con
`connection.queries` invocando la vista, y correr `EXPLAIN (FORMAT JSON)` de la más cara con
`work_mem` en `'2MB','8MB','16MB','64MB','256MB','1GB'` × `max_parallel_workers_per_gather`
en `0, 2`, leyendo el nodo cuyo `Subplan Name = 'SubPlan 1'`.

**§4.6 las tres variantes** — sobre el SQL literal capturado:
BASE tal cual · quitarle `U0."estado" = 'COMPLETADO' AND ` · reemplazar todo el bloque
`IN (SELECT V0."id" … NOT V1."excluir_de_analitica"))` por
`= ANY (ARRAY[<ids>]::bigint[])`.

**§4.6 delta de semántica (0 SKU):**

```sql
SELECT count(DISTINCT "ProductoTalla_id") FROM app_movimientos_producto
WHERE fecha >= '2024-01-01'::date AND estado = 'COMPLETADO';   -- 173.939
SELECT count(DISTINCT "ProductoTalla_id") FROM app_movimientos_producto
WHERE fecha >= '2024-01-01'::date;                             -- 173.939
```

**§4.7 tamaño del universo:**

```sql
SELECT count(*) FROM app_producto_talla pt
JOIN app_producto p ON p.id = pt.producto_id
WHERE p.excluir_de_analitica = false
  AND (pt.stock > 0 OR pt.id IN (
        SELECT DISTINCT U0."ProductoTalla_id" FROM app_movimientos_producto U0
        WHERE U0.estado = 'COMPLETADO' AND U0.fecha >= '2024-01-01'::date));
```

**§4.10 volumen de escritura del POS:**

```sql
SELECT count(*) AS movs_90d, count(DISTINCT fecha) AS dias,
       round(count(*)::numeric / count(DISTINCT fecha), 1) AS por_dia
FROM app_movimientos_producto WHERE fecha >= CURRENT_DATE - 90;
```

**§5.1 el A/B de la Propuesta 2:**

```sql
SELECT DISTINCT ON (m."ProductoTalla_id") m."ProductoTalla_id", m.fecha
FROM app_movimientos_producto m
WHERE m.fecha BETWEEN '2026-01-01'::date AND '2026-12-31'::date
  AND m.tipo_movimiento = 'INGRESO'
ORDER BY m."ProductoTalla_id", m.fecha, m.hora, m.id;   -- 5.A
-- 5.B: la misma con  ORDER BY m."ProductoTalla_id", m.fecha
```

**Endpoints** — invocación read-only con `RequestFactory` + `transaction.atomic()` +
`set_rollback(True)` + guarda anti-escritura por regex sobre `connection.queries`; y la
suite completa:

```powershell
cd retailmind
python _test_reportes_readonly.py --confirmo-prod --solo rendimiento_compras
python _test_reportes_readonly.py --confirmo-prod
```
