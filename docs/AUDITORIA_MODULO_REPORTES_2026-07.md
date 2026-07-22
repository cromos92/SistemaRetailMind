# Auditoría integral del módulo de reportes — julio 2026

**Fecha:** 2026-07-22 · **Alcance:** 24 endpoints de reporte + adaptación a recategorización v1.2 · **Método:** testeo automatizado read-only contra producción (`retailmind/_test_reportes_readonly.py`) + revisión de código · **Rango de datos:** junio 2026 completo (mes cerrado).

---

## 1. Resumen ejecutivo

| # | Reporte | Vista (archivo:línea aprox.) | Veredicto pre-fix | Estado post-fix |
|---|---|---|---|---|
| 1 | Ventas por sucursal | `views_modulo_reportes.py:1136` | **OK** — cuadra al peso con oráculo ($177.511.482 / 4.430 docs) | OK + nota metodológica (F-05) |
| 2 | Ventas por vendedor | `:71` | **OK** — suma vendedores = total | OK |
| 3 | Comisiones vendedor | `:674` | **OK** — permiso fino operativo | OK |
| 4 | Diagnóstico cuadratura vs reporte | `:1451` | **OK** — autoconsistente; delta 04-jun $1.015.490 explicado 100% | OK |
| 5 | Documentos por vendedor | `:1732` | **BUG LATENTE** — ordenaba por string dd/mm/YYYY (rompe en rangos multi-mes) | **Corregido** (F-04) |
| 6 | Comparativa mensual | `:1828` | **ROTO** — doble conteo: 4.358 tickets con DTE ($180.080.200/mes) contados 2 veces | **Corregido** (F-16) |
| 7 | Documentos emitidos | `:1962` | **ROTO (UX)** — truncado silencioso a 100 filas; junio dejaba **4.343 de 4.443 docs invisibles** | **Corregido** — paginación real (F-03) |
| 8 | Ventas internet | `:7100` | OK (smoke) | OK |
| 9 | **Ventas global por empresa** | `:7250` | **ROTO (P0 seguridad)** — usuario restringido a empresa 1320 veía además 1319 y 1802 | **Corregido** — scoping EmpresaUser (F-01) |
| 10 | Ventas comparativo | `:7876` | OK | OK |
| 11 | Despachos a tiendas | `:8677` | OK con reserva (proveedor por match de nombre) | OK · backlog F-12 |
| 12 | **Productos vendidos** | `:8535` | **DESACTUALIZADO v1.2** — 4 fallas (ver §3) pese a KPIs exactos ($172.498.562 / 5.622 uds) | **Actualizado** — árbol+especialidad+género (F-07) |
| 13 | Resumen existencias | `views_resumen_existencias.py:470` | **DESACTUALIZADO** — 13 planas viejas como raíces; total sí cuadra (98.653 pares) | **Actualizado** (F-08) |
| 14 | Existencias por marca | `:2633` | DESACTUALIZADO (filtro depto plano) | **Actualizado** (F-09) |
| 15 | Existencias por sucursal | `:3127` | OK datos; rótulo atributo3 inconsistente | **Homogeneizado** ("Género") |
| 16 | Existencias general | `views.py:31784` | DESACTUALIZADO — filtro por padre devolvía 0 | **Actualizado** (F-09) |
| 17 | Movimientos por sucursal | `:5460` | OK datos (restante = stock oráculo); filtro depto plano | **Actualizado** (F-10) |
| 18 | Kardex por talla / agrupado | `views.py:7930/:7993` | **ROTO (P0 seguridad)** — sin filtro de empresa (probado: devolvió kardex ajeno); saldo 20/20 OK | **Corregido** — `puede_ver_sucursal` (F-02) |
| 19 | FIFO general | `views.py:22479` | **ROTO en la práctica** — medido: 2.968 queries / 9,9 min y termina en **500** ("server closed the connection": la BD corta la sesión antes de responder). Drift lotes↔stock muestral = **0%** | Backlog **F-14 (urgente: hoy no entrega respuesta)** |
| 20 | **Compras integral** | `:3678` | **CRÍTICO PERFORMANCE** — **9.000 queries / 43,8 minutos** por consulta | Backlog **F-17 (nuevo, prioridad alta)** |
| 21 | Productos por origen | `:3563` | OK (4 queries, 21,8 s — lento pero tolerable) | OK |
| 22 | Recepciones / despachos detallado | `:5940/:6141` | Recepciones OK (12q); **despachos ROTO — devolvía 500 SIEMPRE** (campos inexistentes: `producto_talla`, `cantidad`, `numero_dte`, `total`) | **Corregido** (F-18) — ahora 200 OK, 47q/9s; N+1 leve en backlog F-13 |
| 23 | Rendimiento proveedor | `:6249` | Funciona pero pesado — medido: **465 queries / 90,7 s** + match ventas por nombre (falsos ceros) | Backlog F-12 |
| 24 | Inteligencia compra / Plan liquidación | `views_inteligencia_compra.py:157/:479` | OK — **ya excluían CD del denominador**; faltaba dimensión categoría | **Plan liquidación ampliado** marca×categoría (F-11) |

**Balance del testeo pre-fix: 88 PASS · 11 FAIL · 4 WARN.** Los 11 FAIL están corregidos en esta pasada o especificados en backlog.

---

## 2. Metodología

- Script `retailmind/_test_reportes_readonly.py`: invoca las vistas reales con `RequestFactory` + sesión simulada (patrón `benchmark_ventas`), captura JSON/tiempo/nº queries, y cruza contra **oráculos** de BD independientes (solo `aggregate/values/annotate`, patrón `_auditoria_readonly`).
- **Garantía read-only:** guarda `_assert_solo_selects` (aborta si una vista emite INSERT/UPDATE/DELETE — 0 detectados en 24 endpoints) + `transaction.atomic` con rollback forzado + flag `--confirmo-prod` obligatorio.
- **Contextos** (resueltos por query): GLOBAL (admin `javier`), TIENDA (suc. 7, la de más tickets), CD (suc. 12), RESTRINGIDO (`andrybethca`, EmpresaUser=1320) — este último es el que probó las fugas de scoping.
- Reproducir: `python _test_reportes_readonly.py --confirmo-prod [--rapido] [--solo <nombres,csv>]`.

## 3. Matriz de impacto de la recategorización v1.2

Estado del catálogo medido en prod (137.556 productos analíticos): **98,7% ya cuelga de hijas v1.2**, 1,3% en planas viejas, 0% sin categoría; 3 padres (Calzado/Ropa/Accesorios), 29 hijas, 31 `_ZZ_`, 49 planas vivas. Género: atributo3 poblado 100%; **atributo4 poblado 0% (columna muerta)**. **90,6% del monto vendido en junio tiene ≥1 etiqueta de especialidad.**

| Reporte | Árbol Padre›Hijo | Especialidades | Género (atributo3) | atributo4 | Exposición planas/_ZZ_ |
|---|---|---|---|---|---|
| Productos vendidos | USABA-MAL (hijo solo, padre no expandía) → **USA-BIEN** | NO-USABA → **USA** (filtro + agregación) | rotulaba "Sexo" → **"Género"** | mostraba columna muerta → **eliminada** | 99,8% del monto ya en hijas; 4 planas visibles (dato, no bug) |
| Resumen existencias | raíces mezcladas → **padres v1.2 + bucket "Sin recategorizar"** | N-A | N-A | N-A | 13 raíces planas → agrupadas |
| Existencias marca/general/movimientos | filtro plano → **expande rama** (`_expandir_categoria_ids`) | N-A | N-A | N-A | selects ahora ocultan `_ZZ_` (endpoint `obtener_categorias` v1.2) |
| Existencias sucursal | salida ya v1.2 (0 filas no-v1.2) | N-A | ya rotulaba "género" (convención adoptada) | N-A | OK |
| Plan liquidación | solo marca → **+ dimensión categoría hija + `?categoria_id=` (rama)** | N-A | N-A | N-A | OK |
| Inteligencia compra | por marca (suficiente para su caso de uso) | N-A | N-A | N-A | OK |

**Convención fijada:** `atributo3` = **Género** en todo el módulo (DAMA ya migrado a MUJER; BEBÉ activo). `atributo4` no se muestra en ningún reporte.

## 4. Hallazgos transversales cuantificados (junio 2026)

1. **Fuga multi-empresa (P0):** `ventas-global` agregaba tickets+DTEs de TODAS las empresas; usuario restringido a 1320 veía 1319 y 1802. Kardex devolvía movimientos de SKUs de otras empresas. Ambos corregidos y verificados con usuario real restringido.
2. **Doble conteo comparativa-mensual:** sumaba `Ticket` PAGADO (incluidos los 4.358/mes con boleta) **y además** el DTE de esos tickets → ~$180M inflados por mes (≈2× la venta POS real). Corregido con `dte_generado=False` (misma regla que ventas-global/comparativo).
3. **Truncado silencioso:** documentos-emitidos mostraba 100 de 4.443 docs del mes sin paginación. Ahora pagina (100/página, controles Anterior/Siguiente).
4. **Divergencia cuadratura vs reporte = BY DESIGN y sana:** el endpoint de diagnóstico explica el 100% del delta (04-jun: $1.015.490, 0 DTEs sin clasificar). Se añadió nota metodológica visible en el reporte (F-05).
5. **Columna género fantasma:** atributo4 0% poblado en 137.556 productos — el reporte mostraba un donut "Género" siempre vacío. Eliminado; su lugar lo ocupa Especialidad (90,6% de cobertura).
6. **Drift lotes FIFO↔stock: 0%** en muestra representativa de 200 SKUs (la reconciliación de jun-2026 sigue firme). OJO: los SKUs **más nuevos** (creación manual reciente) aún no tienen lotes — sesgo detectado al muestrear por `-id` (100% "drift" aparente); es ausencia de lote inicial, no descuadre.
7. **Kardex coherente:** saldo acumulado == stock actual en 20/20 SKUs muestreados.
8. **Stock invisible en resumen-existencias:** 0 pares sin categoría (bien), pero 70.230 "pares" excluidos por `excluir_de_analitica` (pseudo-artículos VISA/ENVÍO/etc.) — correcto excluirlos, se documenta la magnitud.
9. **Performance crítica:** compras-integral = 9.000 queries / **43,8 min**. Inusable en la práctica. Nuevo F-17.

## 5. Fixes aplicados en esta pasada

| Fix | Qué | Dónde |
|---|---|---|
| F-01 | Scoping EmpresaUser en ventas-global (salvo "ver todas") | `views_modulo_reportes.py` (`_scope` en `_sumar_periodo`) |
| F-02 | `puede_ver_sucursal` en kardex por talla y agrupado (403 si ajeno) | `views.py:7930/:7993` |
| F-03 | Paginación real `page/per_page` + controles en UI | `views_modulo_reportes.py` + `documentos_emitidos.html` |
| F-04 | Orden por fecha real (clave ISO interna) | `obtener_documentos_vendedor_reporte` |
| F-05 | Nota metodológica facturación vs caja | `reporte_ventas_sucursal.html` |
| F-07 | Productos vendidos v1.2: label Padre›Hijo (tabla+heatmap), filtro padre expande rama, filtro+agregación por Especialidad, rótulo Género, columna atributo4 eliminada, selects sin `_ZZ_` | `views_modulo_reportes.py` + `reporte_productos_vendidos.html` |
| F-08 | Resumen existencias: raíces = padres v1.2, bucket "Sin recategorizar (N antiguas)" + "Sin categoría" visibles | `views_resumen_existencias.py` |
| F-09 | Filtro categoría expande rama en existencias-marca/general + endpoint compartido `obtener_categorias` devuelve árbol sin `_ZZ_` (beneficia 4 pantallas) | `views_modulo_reportes.py`, `views.py` |
| F-10 | Ídem en movimientos-sucursal | `views_modulo_reportes.py` |
| F-11 | Plan liquidación: dimensión categoría hija (mismas métricas GMROI/dead-stock) + `?categoria_id=` (marca×categoría) + tabla en UI | `views_inteligencia_compra.py` + `plan_liquidacion.html` |
| F-16 | Anti-doble-conteo en comparativa mensual (`dte_generado=False`) | `views_modulo_reportes.py:1838` |
| F-18 | Despachos-detallado devolvía **500 siempre** (nombres de campo inventados: `producto_talla`→`productoTalla`, `cantidad`→`stock`, `numero_dte`→`numero_documento`, `total`→`monto_con_iva`) — descubierto por la suite al invocarlo | `api_reporte_despachos_detallado` |

Herramienta permanente: `retailmind/_test_reportes_readonly.py` — correr tras cada cambio al módulo (`--solo <reporte>`) como suite de regresión.

**Regresión post-fix (2026-07-22, contra prod):** 13 reportes re-testeados → **57 PASS · 3 FAIL · 3 WARN**. Verificado en vivo: fuga multi-empresa cerrada (restringido ve solo empresa 1320), kardex ajeno → 403, documentos-emitidos paginado (4.443/4.443 accesibles), filtro padre "Calzado" devuelve la rama (50 filas vs 0), raíces de resumen = padres v1.2, comparativa-mensual suma exacta $176.018.819 (sin los $180M duplicados). Los 3 FAIL restantes son de **datos**, no de código: 4 categorías planas vivas aún venden (1,3% del catálogo sin recategorizar, incluye 'SIN DEFINIR') y atributo4 sigue 0% poblado (ya retirado de la UI) — acciones en §6 "Datos".

## 6. Backlog especificado (NO implementado — aprobar antes)

- **F-17 (NUEVO, prioridad alta) — compras-integral 9.000 queries/44 min:** los ~15 helpers de `api_reporte_compras` (:3678) iteran proveedor por proveedor / DTE por DTE. Reescribir con `values().annotate()` por bloque (patrón de `_mapas_movimientos_sucursal`) y acotar el año por defecto. Regresión: `check_performance` con umbral 100q/30s.
- **F-12 — rendimiento-proveedor:** matchear ventas por `producto_id`/`ProductoTalla` (no por nombre de artículo, que sobrecuenta homónimos y produce falsos ceros) + agregación en BD. `:6249`.
- **F-13 — despachos-detallado:** eliminar N+1 (query por DTE) con `values().annotate()` + `select_related`. `:6141`.
- **F-14 — FIFO general (URGENTE):** agregación de lotes en BD en vez de 2 helpers por SKU. `views.py:22479`. Medido 2026-07-22: 2.968 queries, 9,9 min y la BD corta la conexión → **el reporte hoy devuelve 500 y no entrega datos**.
- **F-15 — deduplicar parseo de fechas:** 4+ copias verbatim del bloque de rango → reusar `_parse_rango_fechas_reporte` (:347).
- **F-06 — borrar funciones FIFO muertas** con bug H1 (conceptos texto libre) en `views_modulo_existencias.py:284+` tras confirmar 0 llamadores.
- **Datos (no código):** recategorizar el 1,3% de productos aún en 49 planas vivas (incluye 'SIN DEFINIR' y las 4 que venden: ver `check_categorias_v12`); poblar lotes iniciales de SKUs nuevos creados por Crear Manual (hoy nacen sin lote FIFO).

## 7. Rúbrica de utilidad (consolidada post-fix)

- **Accionables y únicos (mantener):** productos-vendidos (ahora con especialidad = la vista de compra por deporte), plan-liquidación (marca×categoría = decisión liquidar/reponer), inteligencia-compra, resumen-existencias, diagnóstico-cuadratura, movimientos-sucursal, kardex.
- **Operativos correctos:** ventas-sucursal/vendedor/comisiones, documentos-emitidos, ventas-internet, ventas-comparativo, existencias ×3, recepciones, productos-origen.
- **Redundancia parcial (fusionables a futuro):** ventas-global vs ventas-comparativo vs comparativa-mensual — tres vistas del mismo dato con reglas ligeramente distintas; ya comparten regla anti-doble-conteo tras F-16. Candidato natural: absorber comparativa-mensual dentro de ventas-comparativo.
- **Inusable por performance hasta F-17:** compras-integral.
- **Ninguno amerita eliminación directa**; el valor bajo estaba en columnas muertas (atributo4) ya retiradas.
