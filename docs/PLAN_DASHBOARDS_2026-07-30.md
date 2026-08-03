# Plan robusto de mejora de dashboards — 2026-07-30

Auditoría profunda de los dashboards del ERP contra el código actual **y contra los datos reales de producción**, con verificación adversarial (8 claims clave verificados por agentes independientes: 7 CONFIRMED, 1 PARTIAL). Este documento cubre **Ventas Mejorado** y **Compras Mejorado**; las secciones de Documentos, Despachos, Requerimientos, Productos y FIFO se agregan al final a medida que se completa su auditoría.

Contexto de datos (verdad de terreno, feb–jul 2026, verificada contra prod el 30-jul):

- Ventas POS (tickets PAGADO sin cambios): may $192,6M · jun $180,5M · jul $152,2M (parcial). Tickets ANULADOS: $11–15M/mes (5–8%). NC de venta: chicas (jun $518k).
- DTE COMPRA: feb $202M · mar $223M · abr $130M · may–jul ~$73M/mes (estacionalidad fuerte de invierno). **Cero NC de compra** registradas como es_nota_credito. 2 NC "por concepto" existen.
- `Dte.estado_pago` tiene case sucio: `'PAGADO'` (197/$311M), `'Pagado'` (122/$185M), `'Pendiente'` (206/$260M), `'Abonado'`. **$260M en cuentas por pagar reales** que ningún dashboard muestra.
- Las 82 OC de 2026 tienen `temporada` vacía → toda la feature de Temporadas corre sobre datos vacíos.
- `TRASPASO_SUCURSAL` (concepto legacy) tiene **19.281 movimientos EN 2026** — se sigue escribiendo hoy.
- "ROI Promedio" del dashboard compras 2026: muestra **120,3%**; sin el IVA del precio de lista sería **85,2%** (35 pts inflados).
- Pseudo-artículos (DIFER VISA / BOLSAS / ENVIOS): ya están `excluir_de_analitica=True`; los KPIs que no filtran ese flag inflan unidades (~68,8k und fantasma en la cadena).

---

## PARTE 1 — DASHBOARD VENTAS MEJORADO (`/app/ventas/dashboard-mejorado/`)

Template: `vistas/modulo_dashboards/dashboard_ventas_nexo.html` (3.255 líneas). 16 requests AJAX por carga, ~85-90 queries ≈ 19s de RTT acumulado. Usa nexo-design-system y Chart.js local. Excepcionalmente bien comentado (post-mortems inline).

### P0 — Bugs visibles y seguridad (hacer YA)

| # | Qué | Dónde | Fix |
|---|---|---|---|
| V-P0.1 | **"Ventas por Día" corrido un día** (VERIFICADO): backend indexa 0=Lunes (`ExtractIsoWeekDay`, `ventas_por_dia[d-1]`), frontend etiqueta `['Dom','Lun',...]`. El badge "Mejor día" siempre nombra el día equivocado y el coloreo de fin de semana marca lunes y domingo | `views_modulo_ventas.py:19692-19706` vs `nexo.html:2153-2159, 2456-2469` | Cambiar `diasNombres` a `['Lun','Mar','Mié','Jue','Vie','Sáb','Dom']` y el coloreo weekend a índices 5-6 |
| V-P0.2 | **APIs sin permiso** (VERIFICADO PARTIAL): la página está gateada (`'/app/ventas/dashboard'` matchea por substring) pero los 16 endpoints `/app/api/ventas/*` solo tienen `@login_required` — un rol sin el permiso saca el JSON directo. Además `_scope_suc_emp` acepta `sucursal_id`/`empresa_id` crudos del GET sin validar contra `obtener_sucursales_usuario`, y sin parámetros devuelve TODO el holding | `middleware_permisos.py:19-20,471`, `views_modulo_ventas.py:19230-19249` | (a) agregar `'/app/api/ventas/'` al URL_PERMISO_MAP → `dashboard_ventas`; (b) validar sucursal/empresa contra permisos en `_scope_suc_emp` y defaultear al universo visible del usuario |
| V-P0.3 | **Margen null pintado como $0** (VERIFICADO): `formatMoney(response.margen_bruto \|\| 0)`, `gmroi \|\| 0`; `margen_calculable/margen_nota/cobertura_costeo_pct` jamás se renderizan. Un margen no calculable se lee como "margen cero" y el 0.00 de GMROI se propaga (línea 2928 parsea el texto) | `nexo.html:2195-2200,2928` vs backend `19784-19863` | Renderizar `s/d` + tooltip con `margen_nota` cuando `margen_calculable=false`; mostrar `cobertura_costeo_pct` como subtexto |

### P1 — KPIs honestos (correctitud)

| # | Qué | Fix |
|---|---|---|
| V-P1.1 | **Ninguna NC se resta en todo el tablero** — venta bruta en K1/K3/margen. Magnitud actual chica (~$0,5M/mes) pero estructural | Nueva serie "NC del período" desde `Dte(es_nota_credito=True, tipo_transaccion in VENTA/VENTA_PUBLICO)` y KPI "Venta neta" = bruta − NC. Mismo criterio que ya usa reporte ventas-global (fix 25-jul) |
| V-P1.2 | **Filtro Vendedor solo lo honra 1 de 14 endpoints** (VERIFICADO) — al filtrar, K1-K3 bajan y el resto sigue en total tienda, sin aviso | Corto plazo: sumar "Vendedor" a la tabla `ALCANCE_BLOQUES` de badges de alcance (ya existe para cat/esp). Mediano: aplicar vendedor_id en productos-más-vendidos y tendencias |
| V-P1.3 | **Sell-Through K7 contradice al Indicador de Compra de la misma pantalla**: no excluye CDs ni `excluir_de_analitica` en el denominador | Reusar el mismo stock_filter de `obtener_indicador_compra_categoria` (19411-19430): excluir CDs + analítica |
| V-P1.4 | UPT recalculado en frontend con numerador/denominador de universos distintos; el backend ya expone `upt` coherente que el JS ignora | Usar `response.upt` del backend |
| V-P1.5 | Dos "ratios de cambio" distintos en la misma pantalla (K8: pagados sin cambios; strip: total con anulados) | Unificar denominador al de K8 y documentarlo en tooltip |
| V-P1.6 | "Pérdida No Apto" muestra `devoluciones_total` (todo lo devuelto), no la pérdida NO_APTO | Cablear `no_apto_monto` real o renombrar la tarjeta |
| V-P1.7 | Control de Fugas usa `Ticket.fecha` (auto_now) y scoping por sesión, distinto al resto | Migrar `fraud_detection.py:374-376` a `created_at` + excluir CAMBIO_DEVOLUCION; alinear scoping |
| V-P1.8 | Cuadraturas: ignora `diferencia_transbank`, y "pendientes = días − arqueos" no tiene sentido multi-sucursal | Incluir transbank en la diferencia; pendientes = sucursales_con_venta × días − arqueos |
| V-P1.9 | Factura+Boleta ≠ Total (deja fuera BOLETA manual y TICKET) | Agregar bucket "Ticket/Otro" |

### P2 — Performance (85-90 queries → objetivo <40, camino crítico <2s)

| # | Qué | Ahorro |
|---|---|---|
| V-P2.1 | `estado-operacional` = ~22 queries ≈ 4,8s (camino crítico): 4 counts de tickets → 1 `aggregate(filter=Q())`; 4 de depósitos → 1; 2 de POS → 1 | ~12 queries |
| V-P2.2 | `/analisis-cambios/` se llama y se descarta (VERIFICADO: `dashboardData.cambios` tiene cero lecturas) + comparativos internos redundantes de indicadores-globales | ~10 queries |
| V-P2.3 | Endpoints v1.2 (por-categoria, por-especialidad, indicador-compra) sin `@cache_ventas_json` — dos golpean `Producto_Talla` (145k filas) | cache 120s |
| V-P2.4 | `created_at__date__gte` no sargable con USE_TZ → seq scan en rangos largos | filtrar por datetime aware (`__gte=make_aware(...)`) en los endpoints pesados |
| V-P2.5 | Payload muerto: 13 campos de indicadores-avanzados, 7 de por-vendedor, top-20 transferido para pintar 10 | recortar |

### P3 — HTML/UX

- Botón "Semanal" de Evolución es no-op (`cambiarVistaEvolucion` vacío) → implementarlo con `TruncWeek` o quitarlo.
- `#trendUPT` muerto (0% fijo) → calcularlo contra el período comparativo o quitarlo.
- KPI "Venta neta" y strip de ANULADOS ($11-15M/mes es dato de control real, hoy invisible arriba).
- Aviso de alcance para Vendedor (ver V-P1.2); nota en "Ventas por Canal" de que incluye cambios a propósito.
- Endpoint muerto `/app/api/ventas/por-sucursal/` y templates huérfanos `dashboard_ventas.html` / `dashboard_ventas_mejorado.html` (~3.000 líneas) → eliminar.

---

## PARTE 2 — DASHBOARD COMPRAS MEJORADO (`/app/verDashboardComprasMejorado/`)

Template: `vistas/modulo_dashboards/dashboard_compras_mejorado.html` (2.656 líneas). 1 API monolítica (~57 queries ≈ 13s RTT) + export que la re-ejecuta. **Paleta propia** (`--rm-primary #405189`) en 529 líneas de CSS inline, ignora nexo-design-system. Chart.js desde CDN sin pin + plugin datalabels cargado y nunca usado. Los 15 bugs del 27-jul están confirmados arreglados.

### P0 — Seguridad y honestidad del número grande

| # | Qué | Dónde | Fix |
|---|---|---|---|
| C-P0.1 | **Página + API + export SIN permiso** (VERIFICADO): `'/app/verDashboardCompras/'` con barra final no es substring de `verDashboardComprasMejorado/`; la API y el export tampoco están mapeados. Cualquier autenticado ve costos y márgenes. Bonus: el `except` global devuelve `traceback.format_exc()` al JSON | `middleware_permisos.py:19`, `views_modulo_compras.py:3085-3091` | Mapear `'/app/verDashboardCompras'` (sin barra) + `'/app/dashboard_compras_mejorado_api/'` + `'/app/exportar_dashboard_compras/'`; quitar el traceback del JSON |
| C-P0.2 | **"ROI Promedio" 120,3% real→85,2%**: `Sum(stock×precioSugerido)` bruto vs costo neto, sobre unidades ordenadas. Alertas e insights calibrados sobre el número inflado (umbral 15%/25%) | `views_modulo_compras.py:3104-3121` | Renombrar a "Markup lista (teórico)" + dividir precioSugerido por 1.19 (el Excel ya lo rotula honesto); recalibrar umbrales de alertas |
| C-P0.3 | **Fallback `esProveedor` vivo en Márgenes CD** (VERIFICADO): `except:` desnudo → las 13 sucursales como CD. Hoy dormido (los flags están bien en BD) pero es una bomba de tiempo con 3 criterios de CD distintos en la misma pantalla | `views_modulo_compras.py:3993-4002` | Eliminar el fallback; usar el mismo criterio que sus 2 funciones hermanas |

### P1 — KPIs honestos

| # | Qué | Fix |
|---|---|---|
| C-P1.1 | **Tendencias YoY comparan filtrado vs sin filtrar** (proveedor X 2026 vs TODOS 2025) | Aplicar los mismos filtros al año anterior (como ya hace la Comparativa Anual) |
| C-P1.2 | **Temporadas = feature muerta por datos**: 82/82 OC de 2026 sin temporada. Además el fold de acentos falta en evolución/comparativa (VERIFICADO) | (a) capturar temporada al crear OC (default por mes de compra); (b) unificar el fold en un helper único; (c) mientras no haya datos, ocultar el doughnut con CTA "asignar temporadas" |
| C-P1.3 | **Distribución ciega al flujo legacy**: 19.281 movs `TRASPASO_SUCURSAL` en 2026 invisibles; K9/G5/G6 solo miran `TRASPASO_SALIDA` | Usar `CONCEPTOS_TRASPASO` + `CONCEPTOS_TRASPASO_LEGACY` de constants_kardex (regla que el propio archivo exige) |
| C-P1.4 | G6 "Despachos por Sucursal" no filtra origen CD (mezcla tienda↔tienda) e ignora legacy sin destino | Filtrar origen CD como K9; nota "sin destino: N und" |
| C-P1.5 | K7 "no inventariables": las 2 NC por concepto SUMAN en vez de restar; sesión sin validar; base bruta junto a inversión neta | Restar `tipo_documento NC`; validar empresa vs EmpresaUser; rotular "bruto c/IVA" |
| C-P1.6 | Stock CD (K10) no excluye `excluir_de_analitica` mientras la tabla de marcas sí → dos cifras de stock en la misma página (en CDs los pseudo-SKUs son casi TODO el stock: IMP real=10 und) | Excluir analítica en K10 |
| C-P1.7 | Combo proveedores incluye a las 4 empresas del holding (están `esProveedor=True`) | Excluirlas del combo por RUT/flag |
| C-P1.8 | **KPI nuevo que falta y la data pide a gritos**: cuentas por pagar ($260M `Pendiente`) — con normalización previa del case de `estado_pago` (`'PAGADO'` vs `'Pagado'`) | KPI + tabla aging de pago por proveedor; command de normalización de estado_pago |
| C-P1.9 | Filtro Período solo aplica a `compras_query` y solo si año=actual; medio dashboard lo ignora sin aviso | Propagarlo o marcar alcance por bloque (patrón ALCANCE_BLOQUES de ventas) |

### P2 — Performance (~57 queries → objetivo <25)

- `compras_ids = list(...)` materializada e inyectada en ~12 queries (VERIFICADO) → pasar el queryset como subconsulta (ya se hace en 4 sitios).
- ROI temporadas: query del año anterior SIN filtro de año (escanea toda la historia) para campos YoY que la UI no renderiza (VERIFICADO) → eliminar el cálculo muerto.
- 2 `COUNT DISTINCT` de métricas nunca mostradas → eliminar.
- El export re-ejecuta las ~57 queries → cachear el JSON 120s y compartirlo con el export.
- `Sucursal.exclude(...)[:10]` sin `order_by` (2 sitios) → orden explícito.

### P3 — HTML/UX

- **Migrar la paleta propia a nexo-design-system** (regla dura de CLAUDE.md): sustituir `--rm-*` por `--nexo-*`, gradiente estándar `#405189→#0ab39c` solo en `module-header`.
- Chart.js CDN sin pin + `chartjs-plugin-datalabels` muerto → servir local (ya existe en `libs/chart.js/`) y quitar el plugin.
- Botones muertos: "Mensual/Semanal" (TODO vacío), `exportarTabla()` (Swal que dice "use el otro botón") → implementar o quitar.
- `LoteProducto.fecha_ingreso` es auto_now_add → el filtro por año de la Comparativa de Costos es poco fiable en datos migrados; rotular "según fecha de ingreso al sistema".
- Datos muertos del payload (YoY temporadas completo, `filtros_aplicados`, `centros_distribucion`, etc.) → recortar.
- Segunda URL viva al mismo template (`verDashboardCompras`) → consolidar.

---

## Orden de ejecución sugerido (ventas+compras)

1. **Tanda 1 (seguridad + bugs visibles, ~1 sesión)**: V-P0.1, V-P0.2, V-P0.3, C-P0.1, C-P0.2, C-P0.3. Sin migraciones.
2. **Tanda 2 (KPIs honestos)**: V-P1.1→V-P1.9 y C-P1.1→C-P1.9. Incluye command de normalización `estado_pago` (con dry-run) y KPI cuentas por pagar.
3. **Tanda 3 (performance)**: V-P2.*, C-P2.*. Medir antes/después con `benchmark` (nº queries por endpoint).
4. **Tanda 4 (HTML/UX + limpieza)**: paleta NEXO en compras, botones muertos, payload muerto, templates huérfanos.

Validación por tanda: `manage.py check` + smoke read-only de cada endpoint contra prod + prueba en navegador con un rol restringido (para P0 de permisos).

---

## PARTE 3 — LOS OTROS 5 DASHBOARDS

Auditados los 5 restantes. Datos de contraste verificados contra prod el 30-jul.

### 3.0 — HALLAZGO TRANSVERSAL (afecta a los 7 dashboards)

**NINGUNA API de dashboard estaba en `URL_PERMISO_MAP`.** Las páginas sí; sus endpoints AJAX y de exportación no. El middleware matchea por substring y el segmento `/api/` (o los prefijos `obtener_datos_` / `exportar_`) rompe la coincidencia → `codigo_opcion=None` → acceso permitido. Cualquier autenticado podía leer por URL directa el JSON completo de un dashboard vedado a su rol: montos, deuda, costos de proveedor, márgenes del CD, valorización de inventario y CSV descargable.

Trampa al arreglarlo: **no se puede mapear el prefijo `/app/api/ventas/`**, porque bajo él viven `editar-boleta-papel` y `eliminar-documento`, que usa `cuadraturaCaja.html` con rol cajero — gatearlo dejaría a los cajeros en 403. Hay que listar endpoint por endpoint.

### 3.1 — DASHBOARD DOCUMENTOS (`/app/dashboard-documentos/`)

31 queries secuenciales ≈ 6,8s de RTT. Paleta propia `--doc-*`. Chart.js local.

| # | Hallazgo | Dato real de prod |
|---|---|---|
| D-P0.1 | **"Deuda Pendiente" no era deuda**: sumaba `estado_pago='PENDIENTE'` sobre TODOS los tipos, contando las boletas del POS (que nacen PENDIENTE y se cobran en caja) | Mostraba **$23.322 millones** |
| D-P0.2 | `estado_pago` case-sensitive: compras escribe `'Pendiente'` en formato título | **277 docs / $316,5M** invisibles |
| D-P0.3 | **El filtro de sucursal borraba TODAS las compras** (se crean con `sucursal=NULL`) | **1.186 docs / $1.557M**; Top Proveedores siempre vacío |
| D-P0.4 | "Pagos Vencidos" siempre 0: nadie escribe `estado_pago='VENCIDO'` | **0 filas** en toda la BD |
| D-P1.1 | Boletas solo contaba `BOLETA ELECTRONICA` | **140.491 BOLETA PAPEL** fuera (21%) |
| D-P1.2 | Facturas omitía `FACTURA EXENTA` | 300 docs |
| D-P1.3 | "% Aceptados SII" no mide el SII sino el estado interno de recepción; las boletas quedan EMITIDO para siempre | 672.059 de 697.344 en EMITIDO → el KPI marca ~3,6% permanente |
| D-P2.1 | 31 queries colapsables a ~8 con `aggregate(filter=Q())` | −75% de latencia |
| D-P3.1 | `por_transaccion` (query completa) y 6 campos más calculados y nunca renderizados; G7 "Documentos por Sucursal" siempre tiene 1 barra | — |

### 3.2 — DASHBOARD DESPACHOS (`/app/dashboard-despachos/`)

Zona superior (`/flujo/`) = 6 queries, es la única que distingue CD→tienda correctamente. Zona inferior (`/datos/`) = 19 queries ≈ 4,2s.

| # | Hallazgo | Dato real de prod |
|---|---|---|
| E-P0.1 | **Inflación por `.distinct()` + `Count('id')`**: el scoping por sucursal añade un join reverso a las líneas de kardex que multiplica cada DTE por su nº de líneas. El `.distinct()` NO salva la agregación | NICK2: RECEPCIONADO_COMPLETO marcaba **2.859 cuando son 96** (×30); PARCIAL **237 vs 4** (×59) |
| E-P0.2 | `/flujo/` sin scoping de sucursal ni gate: expone el **valorizado a costo** de todos los CD | — |
| E-P1.1 | Ciego a `TRASPASO_SUCURSAL` (concepto legacy que se sigue escribiendo) | **19.281 movimientos en 2026** |
| E-P1.2 | Dos tablas "origen→destino" con reglas incompatibles (unidades vs DTEs, con/sin estado, con/sin CD) | — |
| E-P1.3 | `abs(SUM)` en vez de `SUM(abs)`; días promedio truncado con `.days` (0,9d → 0) | — |
| E-P1.4 | Sin `try/except`: un 500 deja la pantalla muda en "--" | — |

### 3.3 — DASHBOARD REQUERIMIENTOS (`/app/dashboard-requerimientos/`)

16 queries ≈ 3,5s. **El bug del `main-content` duplicado está confirmado corregido aquí.** Chart.js local.

| # | Hallazgo | Dato real de prod |
|---|---|---|
| R-P1.1 | El fix de `decision_proveedor` cubre solo una de las dos rutas de cierre: `actualizar_estado_requerimiento` (EN_REVISION→APROBADO) escribe `estado` pero no `decision_proveedor` ni `fecha_resolucion` → la tasa de aprobación y el SLA subcuentan | Hoy sin impacto: **solo 2 requerimientos vivos** (1 PENDIENTE, 1 ESPERANDO_RESPUESTA), 0 cerrados sin decisión |
| R-P1.2 | `PARCIAL` se cuenta como éxito en el KPI pero se pinta como *Rechazado* en el donut (porque `estado` se fuerza a RECHAZADO) | — |
| R-P1.3 | El aging del dashboard NO usa la escala de `nivel_urgencia` pese al comentario que lo afirma (usa `fecha_creacion`, el modelo usa `dias_sin_respuesta`) | mismo requerimiento sale CRÍTICA aquí y NORMAL en la lista |
| R-P2.1 | `por_prioridad`: query ejecutada, serializada y nunca consumida | −1 query |
| R-P3.1 | "Por Sucursal" siempre muestra 1 barra (el scoping ya fija la sucursal) | — |

### 3.4 — DASHBOARD PRODUCTOS (`/app/dashboard_productos_mejorado/`)

~22 queries sin caché ≈ 5s de RTT + escaneos; el loader está neutralizado (llama a una función que no existe) → pantalla congelada sin feedback.

| # | Hallazgo | Dato real de prod |
|---|---|---|
| P-P0.1 | **"Margen Potencial" restaba universos distintos**: valor de venta de TODA la red menos costo de UNA sucursal, con `sucursal_id` defaulteando a la sesión aunque el selector diga "Todas" | Mostraba **608,4%** cuando lo correcto es **97,9%** |
| P-P0.2 | Dashboard y API sin permisos (clave con barra final no matchea `_mejorado`) | — |
| P-P0.3 | Exportación "con filtros" rota: manda un **ID** de categoría contra un filtro por **nombre** | devuelve el catálogo entero o nada |
| P-P0.4 | Botones "Ver"/"Editar" apuntan a rutas inexistentes | 100 botones en 404 por carga |
| P-P1.1 | "Total Productos" no excluía pseudo-artículos ni respetaba filtros | 138.101 vs 137.738 |
| P-P1.2 | Rotación: numerador de ventas global vs denominador de stock por sucursal | — |
| P-P1.3 | Stock muerto valorizado a **precio de venta**, mientras el FIFO lo valoriza a **costo** → dos cifras irreconciliables | — |
| P-P1.4 | `int(request.GET.get('periodo'))` sin `try` | `?periodo=abc` → 500 |
| P-P1.5 | Stock negativo invisible en todos los buckets pero contamina el denominador | **52 SKUs, −195 und** |
| P-P1.6 | Dos verdades de "ventas" en la misma pantalla (tickets vs kardex) | — |

### 3.5 — DASHBOARD FIFO (`/app/dashboard_fifo/`)

7 queries pero ~39s de ejecución (la cirugía de julio es real: 600s→39s confirmado en código). Sin librería de gráficos: todo es HTML/CSS y tablas.

| # | Hallazgo | Detalle |
|---|---|---|
| F-P0.1 | Los endpoints AJAX y de export escapan al control de permisos → valorización completa + CSV para cualquier autenticado | |
| F-P0.2 | Sin scoping validado; el vecino `reporte_fifo_general` sí aplica `puede_ver_sucursal` — la corrección se hizo a un lado del archivo y no al otro | |
| F-P1.1 | **El KPI insignia mide otra cosa**: "Diferencia Total FIFO vs Sistema" mezcla descalce de unidades con drift de costeo (costo del lote vs `Producto.costo` actual). Con las unidades cuadradas, cualquier reprecio genera "diferencia" | partir en dos KPIs |
| F-P1.2 | **Los SKU con stock y CERO lotes son invisibles** (`n_lotes_agg__gt=0`) — justo el peor caso de drift que el dashboard dice diagnosticar | |
| F-P1.3 | Interacción filtro-período × KPIs incoherente: 4 KPIs respetan el período y 4 lo ignoran, en la misma rejilla | |
| F-P1.4 | Todo el aging depende de `fecha_ingreso` (`auto_now_add`); si `corregir_fecha_ingreso_lotes` no se corrió con `--apply`, los 4 KPIs de capital son optimistas | **verificar**: lotes por año 2018-2026 se ven distribuidos, así que la corrección parece aplicada |
| F-P2.1 | 4 queries de aging colapsables a 1; `fecha_ingreso__date` anula el índice; sin paginación; el export re-ejecuta los 39s | |
| F-P3.1 | ~120 líneas de CSS muerto (gauge, progress-ring, legend-*), 12 campos de payload nunca renderizados, "Exportar Excel" devuelve CSV | |

---

## ✅ IMPLEMENTADO EN ESTA SESIÓN (2026-07-30)

Todo con `manage.py check` PASS, `py_compile` PASS, `node --check` PASS y smoke funcional contra prod.

**Seguridad (transversal)** — `middleware_permisos.py`
- 25 rutas nuevas en `URL_PERMISO_MAP`: las APIs, exportaciones y variantes `_mejorado` de los 7 dashboards.
- Verificado ruta por ruta: las 16 quedan gateadas; `editar-boleta-papel` y `eliminar-documento` (rol cajero) siguen libres.

**Documentos** — `views_dashboards_kpi.py` + `dashboard_documentos.html`
- "Deuda Pendiente" → **"Por Pagar a Proveedores"** (solo COMPRA) + KPI nuevo **"Por Cobrar"** (solo ventas con `diasCredito>0`). Comparación con `__iexact`. En NICK2 pasó de $0 a $274.648.
- "Pagos Vencidos" ahora se mide por `fecha_vencimiento` real, con monto: 8 docs vencidos.
- Compras con `sucursal=NULL` dejan de perderse en el scoping por sucursal.
- Facturas incluye EXENTA; Boletas incluye PAPEL; NC capta `es_nota_credito=True`.
- "% Aceptados SII" → **"% Recepción Cerrada"** con tooltip explicando que no es el acuse del SII.

**Despachos** — `views_dashboards_kpi.py`
- `Count('id', distinct=True)` en las 6 agregaciones afectadas por el join reverso (fin de la inflación ×30/×59).
- Días promedio de recepción con `total_seconds()/86400` en vez de `.days`.

**Productos** — `views.py` + `dashboard_productos_mejorado.html`
- El filtro de sucursal se propaga al queryset base, a los lotes y a las ventas → margen de 608,4% a **99,9%** (NICK2).
- "Total Productos" excluye pseudo-artículos y respeta filtros.
- `periodo` inválido ya no revienta la vista.
- El badge del margen deja de estar fijo en verde ↑.

**Ventas** — `views_modulo_ventas.py` (sin cambios) + `dashboard_ventas_nexo.html`
- Gráfico "Ventas por Día" y badge "Mejor día": etiquetas corregidas a lunes-first; resaltado de fin de semana en los índices 5-6.
- Margen y GMROI nulos se muestran como **s/d** con la nota y la cobertura de costeo en el tooltip, en vez de `$0` / `0.00`.

**Compras** — `views_modulo_compras.py` + `dashboard_compras_mejorado.html`
- "ROI Promedio" → **"Markup de Lista (teórico)"**, descontando el IVA del precio sugerido: de **120,3% a 85,2%**.
- "Margen Esperado" del frontend usa el valor neto (antes restaba PVP con IVA menos costo neto).
- Eliminado el fallback `esProveedor` que podía reclasificar las 13 sucursales como CD; ahora loguea y deja el panel vacío si no hay CD marcados.
- Eliminada la query `COUNT DISTINCT` de `productos_distintos`, que no se renderizaba.

### Pendiente de las tandas 2-4
Performance (colapsar los ~31 queries de documentos, ~22 de productos, 4 de aging FIFO), NC no restadas en ventas, filtro Vendedor sin alcance, conceptos legacy de traspaso, exportación rota de productos, botones en 404, migración de la paleta de compras a NEXO, y la limpieza de payload/CSS muerto.
