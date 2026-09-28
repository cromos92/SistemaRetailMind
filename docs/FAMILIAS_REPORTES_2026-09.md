# Familias de reportes — inventario y navegación unificada (sep-2026)

Objetivo: los 21 reportes del módulo viven en pantallas separadas y varios responden
la misma pregunta. Fusionarlos en una pantalla con pestañas es demasiado riesgoso
(cada template trae 150-1.500 líneas de JS propio con nombres globales que chocan),
así que en esta etapa se unificó **la navegación y la estructura visible**:

- Parcial nuevo [`app/templates/vistas/modulo_reportes/_familia_reportes.html`](../retailmind/app/templates/vistas/modulo_reportes/_familia_reportes.html):
  una tira de `quick-filter-btn` (mismo estilo de ESTILOS_MODULOS.md) con los
  reportes de la familia, el activo resaltado y un título corto arriba
  ("Reportes de ventas" / "de existencias" / "de logística" / "de compras").
- Se incluye justo debajo del header de cada reporte:
  `{% include 'vistas/modulo_reportes/_familia_reportes.html' with familia='ventas' activo='sucursal' %}`
- Al hacer clic conserva del query string actual SOLO los filtros compartidos
  (`fecha_inicio`, `fecha_fin`, `fecha_desde`, `fecha_hasta`, `desde`, `hasta`,
  `anio`, `mes`, `sucursal`, `sucursal_id`, `marca`, `marca_id`). JS vanilla,
  idempotente (`window.__familiaReportesInit`), reescribe el `href` al cargar y
  lo recalcula en el clic. **No toca datos ni el JS de cálculo de ningún reporte.**
- KPI cards sin `title` recibieron un tooltip corto con cómo se calcula el número
  (78 en total, leídos del JS + API; ver §4).

Un reporte pertenece a una sola familia. Familias: **VENTAS (6) · EXISTENCIAS (7) ·
LOGÍSTICA (3) · COMPRAS (5)**.

## 1. Inventario

"Filtros compartidos" = los que la tira conserva al cambiar de reporte, con el
**nombre real** que el JS de cada reporte envía a su API. "Filtros propios" = el
resto. Todas las rutas cuelgan de `/app/`.

### VENTAS

| Reporte (título visible) | `activo` | Ruta | Template | Vista | Filtros compartidos | Filtros propios / APIs |
|---|---|---|---|---|---|---|
| Reporte de Ventas (ventas por vendedor y sucursal + pestaña Comisiones) | `sucursal` | `reportes/ventas-sucursal/` | `modulo_reportes/reporte_ventas_sucursal.html` | `views_modulo_reportes.ver_reporte_ventas_sucursal` | `mes`, `fecha`, `fecha_inicio`, `fecha_fin`, `sucursal_id` | `vendedor_id` · APIs `api/reportes/ventas-por-vendedor`, `ventas-por-sucursal`, `comisiones-vendedor(/exportar)`, `comparativa-mensual`, `documentos-vendedor`, `diagnostico-cuadratura` |
| Comparativo de Ventas | `comparativo` | `reportes/ventas-comparativo/` | `modulo_reportes/reporte_ventas_comparativo.html` | `views_modulo_reportes.ver_reporte_ventas_comparativo` | `fecha_inicio`, `fecha_fin`, `sucursal_id` | `tipo_flujo` · API `api/reportes/ventas-comparativo` |
| Ventas Globales por Empresa | `global` | `reportes/ventas-global/` | `modulo_reportes/reporte_ventas_global.html` | `views_modulo_reportes.ver_reporte_ventas_global` | `mes` **o** `fecha_inicio`+`fecha_fin` | — · API `api/reportes/ventas-global-empresa` |
| Ventas por Internet | `internet` | `reportes/ventas-internet/` | `modulo_reportes/reporte_ventas_internet.html` | `views_modulo_reportes.ver_reporte_ventas_internet` | `fecha_inicio`, `fecha_fin`, `sucursal_id` | `todo_historico`, `empresa_id`, `origen`, `plataforma`, `vendedor_id`, `q`, `page`, `page_size` · APIs `obtener_reporte_ventas_internet`, `exportar_reporte_ventas_internet` |
| Productos Vendidos | `productos` | `reportes/productos-vendidos/` | `modulo_reportes/reporte_productos_vendidos.html` | `views_modulo_reportes.ver_reporte_productos_vendidos` | `fecha_inicio`, `fecha_fin`, `sucursal_id`, `marca_id` | `tipo_flujo`, `categoria_id`, `sexo_id`, `especialidad_id`, `temporada`, `rango_precio`, `orden`, `busqueda`, `incluir_excluidos`, `top_n` · APIs `api/reportes/productos-vendidos`, `atributo-opciones` |
| Documentos Emitidos | `documentos` | `reportes/documentos-emitidos/` | `modulo_reportes/documentos_emitidos.html` | `views_modulo_reportes.ver_documentos_emitidos` | `fecha_desde`, `fecha_hasta`, `sucursal_id` | `tipo_documento`, `metodo_pago` · APIs `api/reportes/documentos-emitidos`, `documentos-emitidos-excel` |

"Comisiones" no es pantalla aparte: es la pestaña de comisiones dentro de
Reporte de Ventas (`api/reportes/comisiones-vendedor`).

### EXISTENCIAS

| Reporte (título visible) | `activo` | Ruta | Template | Vista | Filtros compartidos | Filtros propios / APIs |
|---|---|---|---|---|---|---|
| Reporte de Existencias | `existencias` | `reportes/existencias/` | `vistas/reporte_existencias.html` (fuera de `modulo_reportes/`) | `views.ver_reporte_existencias` | `sucursal_id` | `categoria_id`, `estado_stock`, `busqueda`, `pagina`, `por_pagina` · APIs `api/obtener-existencias`, `exportar-existencias-excel` |
| Existencias por Marca | `marca` | `reportes/existencias-marca/` | `modulo_reportes/reporte_existencias_marca.html` | `views_modulo_reportes.ver_reporte_existencias_marca` | `sucursal_id`, `marca_id` | `departamento_id`, `busqueda`, `limite`, `sin_filtro` · APIs `api/reporte-existencias-marca`, `exportar-existencias-marca-excel`, `sucursales-usuario` |
| Existencias por Sucursal | `sucursal` | `reportes/existencias-sucursal/` | `modulo_reportes/reporte_existencias_sucursal.html` | `views_modulo_reportes.ver_reporte_existencias_sucursal` | `sucursal_id`, `marca_id` | — · APIs `api/reporte-existencias-sucursal`, `exportar-existencias-sucursal-excel/pdf` |
| Resumen de Existencias | `resumen` | `reportes/resumen-existencias/` | `modulo_reportes/resumen_existencias.html` | `views_resumen_existencias.ver_resumen_existencias` | `sucursal_id`, `marca_id` | `agrupar_por`, `fecha_corte`, `categoria_id`, `excluir_articulos`, `ids`, `q`, `limite` · APIs `api/resumen-existencias`, `exportar-resumen-existencias-excel/pdf`, `verificar-disponibilidad-historico`, `listar-articulos-para-excluir`, `listar-sucursales-resumen`, `detalle-stock-sucursal` |
| Stock Original vs Stock Actual por Sucursal | `movimientos` | `reportes/movimientos-sucursal/` | `modulo_reportes/reporte_movimientos_sucursal.html` | `views_modulo_reportes.ver_reporte_movimientos_sucursal` | `fecha_desde`, `fecha_hasta`, `marca_id` | `departamento_id`, `busqueda`, `limite`, `sin_filtro`, `solo_tiendas`, `mostrar`, `incluir_descripcion` · APIs `api/reporte-movimientos-sucursal`, `exportar-movimientos-sucursal-excel` |
| Quiebre de Talla por Sucursal | `quiebre` | `reportes/quiebre-talla/` | `modulo_reportes/reporte_quiebre_talla.html` | `views_modulo_reportes_tallas.ver_reporte_quiebre_talla` | `sucursal_id` | `cd_id`, `dias`, `min_venta`, `umbral_core`, `solo_quiebres`, `page`, `page_size` · API `api/reportes/quiebre-talla` |
| Plan de Liquidación | `liquidacion` | `reportes/plan-liquidacion/` | `modulo_reportes/plan_liquidacion.html` | `views_inteligencia_compra.ver_plan_liquidacion` | `sucursal_id`, `marca_id` | `categoria_id`, `especialidad_id`, `incluir_cd`, `con_opciones`; detalle: `page`, `page_size`, `orden`, `antiguedad`, `q`, `tramos` · APIs `api/plan-liquidacion(/detalle, /por-anio, /exportar-excel, /importar)`, `reportes/plan-liquidacion/imprimir/` |

`plan_liquidacion_imprimir.html` es la vista de impresión (sin layout): no lleva tira.

### LOGÍSTICA

| Reporte (título visible) | `activo` | Ruta | Template | Vista | Filtros compartidos | Filtros propios / APIs |
|---|---|---|---|---|---|---|
| Despachos a Tiendas | `despachos` | `reportes/despachos-tiendas/` | `modulo_reportes/reporte_despachos_tiendas.html` | `views_modulo_reportes.ver_reporte_despachos_tiendas` | `fecha_desde`, `fecha_hasta` | `sucursal_destino_id`, `proveedor_id` · API `api/reporte-despachos-tiendas` |
| Mercadería en Tránsito (consolidado) | `transito` | `reportes/mercaderia-transito/` | `modulo_reportes/reporte_mercaderia_transito.html` | `views_modulo_reportes_diferencias.ver_reporte_mercaderia_transito` | — (no usa fechas ni sucursal activa) | `origen_id`, `destino_id`, `dias`, `situacion`, `page`; detalle `dte_id` · APIs `api/reportes/mercaderia-transito(/detalle)` |
| Diferencias Despachado vs Recepcionado | `diferencias` | `reportes/diferencias-recepcion/` | `modulo_reportes/reporte_diferencias_recepcion.html` | `views_modulo_reportes_diferencias.ver_reporte_diferencias_recepcion` | `fecha_desde`, `fecha_hasta`, `sucursal_id` | `proveedor_id`, `tipo`, `page`, `page_size` · API `api/reportes/diferencias-recepcion` |

Los reportes "mejorados" recepciones/despachos-detallado se eliminaron en la
auditoría de ago-2026 (ver `docs/AUDITORIA_REPORTES_2026-08.md`): no hay
"recepciones" como pantalla aparte.

### COMPRAS

| Reporte (título visible) | `activo` | Ruta | Template | Vista | Filtros compartidos | Filtros propios / APIs |
|---|---|---|---|---|---|---|
| Reporte de Compras (incluye la sección "Rendimiento" en la misma pantalla) | `compras` | `reportes/compras/` | `modulo_reportes/reporte_compras.html` | `views_modulo_reportes.ver_reporte_compras` | `anio`, `sucursal` | `periodo`, `proveedor`, `temporada`, `comparar`, `recepcion_id` · APIs `api/reporte-compras`, `api/rendimiento-compras`, `exportar-reporte-compras-excel` |
| Rendimiento por Proveedor | `rendimiento` | `reportes/rendimiento-proveedor/` | `modulo_reportes/reporte_rendimiento_proveedor.html` | `views_modulo_reportes.ver_reporte_rendimiento_proveedor` | `anio`, `fecha_inicio`, `fecha_fin`, `sucursal_id` | `proveedor_id` · APIs `api/reporte-rendimiento-proveedor`, `exportar-rendimiento-proveedor-excel` |
| Reporte de Ingresos por Proveedor | `ingresos` | `reportes/ingresos-proveedor/` (desde el 28-sep; `verReporteDespachosProveedor/` redirige con sus filtros) | `modulo_reportes/reporte_ingresos_proveedor.html` (antes `vistas/modulo reportes/reporteDespachosProveedor.html`) | `views.verReporteDespachosProveedor` | `fecha_inicio`, `fecha_fin` | `proveedor_id`, `dte_numero`, `excluir_interna`, `page`, `page_size` · APIs `reporte_despachos_por_proveedor/`, `obtener_proveedores_para_reporte/` |
| Origen de Catálogo | `origen` | `reportes/productos-origen/` | `modulo_reportes/productos_por_origen.html` | `views_modulo_reportes.ver_reporte_productos_origen` | `anio`, `sucursal` | — · APIs `api/reportes/productos-origen`, `api/reportes/sucursales` |
| Inteligencia de Compra | `inteligencia` | `reportes/inteligencia-compra/` | `modulo_reportes/inteligencia_compra.html` | `views_inteligencia_compra.ver_inteligencia_compra` | `sucursal_id`, `marca_id` | — · API `api/inteligencia-compra` |

"Rendimiento de compras" no es pantalla aparte: es la sección inferior de Reporte
de Compras (`api/rendimiento-compras`).

### Ajustes respecto a la propuesta inicial

- **Plan de Liquidación pasa de COMPRAS a EXISTENCIAS.** Lee stock/kardex con
  antigüedad FIFO ("capital inmovilizado"), sus filtros son exactamente los de la
  familia existencias (`sucursal_id`, `marca_id`, `categoria_id`, `especialidad_id`)
  y el menú lateral ya lo lista bajo "Reportes Existencias". Inteligencia de Compra
  se queda en COMPRAS (responde "cuánto y qué comprar", aunque el menú también lo
  lista bajo Existencias).
- **Reporte de Existencias** (`reportes/existencias/`, template fuera de
  `modulo_reportes/`) se incorporó a EXISTENCIAS: es el reporte al que remiten los
  tooltips de Resumen de Existencias.
- **Ingresos por Proveedor** (`verReporteDespachosProveedor`) va en COMPRAS y no en
  LOGÍSTICA: compara lo comprometido en el DTE de compra contra lo ingresado, por
  proveedor; es la pareja natural de Rendimiento por Proveedor.
- Cuatro reportes no están en el menú lateral (Ventas Global, Quiebre de Talla,
  Mercadería en Tránsito, Diferencias de Recepción): la tira es ahora su acceso
  visible desde el resto de la familia. `menu.html` no se tocó.

## 2. Parcial `_familia_reportes.html`

- `id="familiaReportes"`, `class="familia-reportes"`, `data-familia` y `data-activo`
  para tests/smoke; cada enlace lleva `data-familia-link` y el activo `aria-current="page"`.
- Estilos acotados a `.familia-reportes .quick-filter-btn` (paleta `#405189 → #0ab39c`
  de ESTILOS_MODULOS.md) para no pisar los `quick-filter-btn` propios de cada vista.
  En los contenedores full-bleed (`.nexo-container`, `.nexo-page-wrapper`,
  `.nexo-resumen-container`, `.ic-wrap`) se alinea con el padding lateral de sus
  filas (40 / 32 / 34 px). Se oculta en `@media print`.
- Punto de inserción por template (siempre después del header, antes de filtros/KPI):
  compactos (`h4` + `small`): antes de `<!-- Filtros -->`; `nexo-header`: antes de la
  fila de KPI; `module-header`/`rp-header` dentro de card: primer hijo del `card-body`;
  `ic-header` → antes de `.ic-controls`; `pl-header` → primer hijo de `.pl-body`;
  `page-header-nexo` → antes de `.filtros-card`; `origen-header` → antes de
  `<!-- ===== FILTROS ===== -->`; `nexo-hero` → antes de la barra de filtros;
  `reporte-header` → antes de `.kpis-strip`.
- **Sin gating por permisos.** El menú usa variables de contexto `permiso_reporte_*`
  que no existen para 4 reportes (los que no están en el menú); condicionar con una
  variable indefinida ocultaría el enlace para todos. Un usuario sin permiso que
  pulse un enlace recibe la misma página 403 que tecleando la URL
  (`@requiere_permiso` / `middleware_permisos`).

## 3. Filtros compartidos: estado real

> **Actualizado 28-sep-2026:** resuelto en la segunda etapa (§7). Lo que sigue
> describe el estado del 26-sep.

Hoy **ningún** reporte lee el query string: las 21 vistas HTML no consultan
`request.GET` y ningún template lee `location.search` (verificado con
`inv_params.py` / `inv_views_get.py`). La tira ya hace viajar los filtros en la
URL, pero el destino los ignora y arranca con sus valores por defecto. Hacer que
cada reporte inicialice sus controles desde la URL es el primer paso de la
segunda etapa (§5): es JS de inicialización, no de cálculo, pero cada template
tiene ids distintos (`filtroFechaDesde` / `fechaInicio` / `filtroDesde` / `f-sucursal`…)
y distintos disparadores de carga, por lo que no se tocó en este frente.

Los nombres tampoco están unificados: fechas como `fecha_inicio/fecha_fin`
(ventas, rendimiento, ingresos), `fecha_desde/fecha_hasta` (documentos, movimientos,
despachos, diferencias), `mes` (ventas-sucursal, global) o `fecha_corte` (resumen);
sucursal como `sucursal_id` (mayoría) o `sucursal` (compras, origen); marca como
`marca_id`. Por eso la tira conserva las variantes y no solo los siete nombres
canónicos.

## 4. Tooltips de KPI (`title`) añadidos

Solo markup, sin tocar JS ni APIs. Cada texto se leyó del campo JSON que pinta el
JS y de la línea de la API que lo calcula. Resumen por template (78 en total):

| Template | title añadidos | Quedan sin title (motivo) |
|---|---|---|
| reporte_ventas_comparativo | 7 (Ventas Netas, % Internet, Ticket Promedio, Documentos, Unidades, Top Sucursal, Top Vendedor) | — |
| reporte_ventas_global | 6 | — |
| reporte_ventas_internet | 5 (Tickets, Unidades, Plataformas, Ecommerce, POS Internet) | **Boletas**: la condición con que `total_boletas` cuenta (L9015) no se verificó |
| reporte_productos_vendidos | 4 (Monto, Margen, SKUs, Top Producto) | **Top Marca / Top Categoría**: el orden de `por_marca` / `por_categoria` no está a la vista en la API (el de productos sí: `productos.sort(orden)`) |
| documentos_emitidos | 6 (Ventas Brutas, Efectivo, TBK Débito, TBK Crédito, Otros, Descuentos) | — |
| reporte_ventas_sucursal | 8 (Total Documentos, Ticket Promedio, Top Vendedor + 5 de la pestaña Comisiones) | — (Total Facturado y Ventas brutas c/IVA ya lo tenían) |
| reporte_movimientos_sucursal | 2 (Total Artículos, Vendido) | — |
| reporte_quiebre_talla | 1 (Unidades en CD) | — |
| reporte_despachos_tiendas | 5 | — |
| reporte_mercaderia_transito | 4 | — |
| reporte_diferencias_recepcion | 3 | — |
| reporteDespachosProveedor | 5 | — |
| reporte_compras | 11 (4 KPI principales + 7 del bloque Rendimiento) | **Costo Promedio/Unidad**: la fórmula de `costo_promedio` no se localizó en `api_reporte_compras` |
| reporte_rendimiento_proveedor | 6 | — |
| productos_por_origen | 5 | — |
| reporte_existencias, resumen_existencias | 0 | sus KPI se pintan desde strings de JS (`renderizarKPIsPlaceholder`, `nexo-empresa-stat`): añadir `title` implicaba editar JS |
| existencias_marca, existencias_sucursal, inteligencia_compra, plan_liquidacion | 0 | glosario ya hecho hoy; solo se insertó la tira |

## 5. Fusión real pendiente (segunda etapa)

> **28-sep-2026:** el paso 0.1, 0.2 (a nivel URL) y 0.3 quedaron hechos, y los grupos se
> fusionaron como pestañas con URL propia. Ver §7.

Qué habría que unificar por familia para pasar de "tira de navegación" a "una
pantalla con pestañas". Los conteos de nombres JS salen de `inv_js_colisiones.py`
(funciones y `const/let/var` de nivel superior repetidos en ≥2 templates de la
familia; los templates marcados IIFE ya encapsulan casi todo).

### Paso 0 (común a todas)

1. Que cada reporte **inicialice sus filtros desde la URL** (§3) y, al consultar,
   refleje los compartidos con `history.replaceState`. Con eso la tira ya
   preserva el contexto de verdad.
2. Unificar nombres de parámetros: `fecha_inicio/fecha_fin`, `sucursal_id`,
   `marca_id`, `anio`, `mes` (con alias de compatibilidad en las APIs durante la
   transición, porque los números cuadran contra oráculos y los tests de
   `test_reportes_correcciones` / `test_scoping_reportes` llaman a las APIs con los
   nombres actuales).
3. Sacar los tokens `:root { --nexo-* }` que 7 templates (`existencias_marca`,
   `existencias_sucursal`, `resumen_existencias`, `movimientos_sucursal`,
   `quiebre_talla`, `inteligencia_compra`, `plan_liquidacion`) redeclaran inline:
   ya vienen de `nexo-design-system.css` (cargado en `layout/header.html`).
4. Homogeneizar el header: conviven 9 patrones (`h4` compacto, `module-header` en
   card, `nexo-header`, `page-header-nexo`, `rp-header`, `origen-header`,
   `ic-header`, `pl-header`, `reporte-header`, `nexo-hero`).

### VENTAS

- APIs distintas por pantalla: `ventas-por-vendedor` + `ventas-por-sucursal` +
  `comisiones-vendedor` (sucursal), `ventas-comparativo`, `ventas-global-empresa`,
  `obtener_reporte_ventas_internet`, `productos-vendidos`, `documentos-emitidos`.
  Todas calculan "ventas netas" con bases distintas (facturación histórica con NC el
  día de emisión vs. tickets POS sin documento vs. solo documentos de venta sin NC):
  la nota metodológica de Ventas por Sucursal y el tooltip de NC en Documentos
  Emitidos lo advierten. Fusionar exige antes un helper único de "ventas netas del
  período" (hoy `_queryset_ncs_venta` solo unifica la parte de NC).
- 17 nombres JS globales repetidos: `cargarDatos`, `cargarSucursales`, `consultar`,
  `esSuperuser`, `fmt`, `fmtMoney`, `fmtNum`, `fmtPct`, `formatearNumero`, `hoy`,
  `params`, `renderEmpresas`, `renderKPIs`, `renderPeriodo`, `renderProductos`,
  `sucursalActivaId`, `sucursalActivaNombre`. `reporte_ventas_sucursal` son 1.505
  líneas de JS sin IIFE; `comparativo`, `internet` y `productos_vendidos` ya van en
  IIFE.
- Selectores de período incompatibles: `tipoFiltroFecha` (mes/fecha), `tipoFlujo`
  (12 presets), `filtroMes`/`filtroDesde`/`filtroHasta`, botones Hoy/Ayer/Semana/Mes,
  `quick-range` con "Todo histórico".

### EXISTENCIAS

- 7 APIs, 4 archivos de vistas (`views.py`, `views_modulo_reportes.py`,
  `views_resumen_existencias.py`, `views_modulo_reportes_tallas.py`,
  `views_inteligencia_compra.py`) y 3 nociones de stock: stock plano actual
  (existencias, marca, sucursal), stock reconstruido a fecha de corte (resumen,
  movimientos: rebobinan el kardex) y lotes FIFO (plan de liquidación). Cuadrar
  esas tres es el trabajo de fondo (ver `project_drift_stock_lotes_fifo`).
- 53 nombres JS globales repetidos (la familia más chocante): `actualizarKPIs`,
  `cargarMarcas`, `cargarReporte`, `cargarSucursales`, `exportarExcel`,
  `filtrarMarcas`, `formatearNumero`, `irPagina`, `limpiarFiltros`, `mostrarError`,
  `mostrarKpis`, `mostrarLoading`, `paginaActual`, `params`… Ningún template de la
  familia usa IIFE.
- El filtro de categoría de Resumen NO expande la rama v1.2 (padre = solo hijos
  directos) mientras Existencias sí: hay que decidir una sola semántica.

### LOGÍSTICA

- Tres reconstrucciones distintas del mismo traspaso: Despachos a Tiendas (kardex de
  salida + ingresos en destino atribuidos vía artículo → proveedor aproximado),
  Mercadería en Tránsito (enviado − recibido − NC, todas las sucursales, sin filtro
  de fecha) y Diferencias (Productos_Recepcionados). Una vista fusionada necesita
  una sola tabla base "documento de traspaso" con enviado/recibido/faltante/dañado.
- 25 nombres JS globales repetidos: `cargarReporte`, `mostrarLoading`, `pintar`,
  `pintarPaginacion`, `paginaActual`, `totalPaginas`, `params`, `num`, `money`,
  `esc`, `toast`… Sin IIFE.
- Mercadería en Tránsito no acepta fechas ni sucursal activa: es la única de la
  familia que no puede recibir los filtros compartidos sin cambiar su API.

### COMPRAS

- Reporte de Compras ya reúne dos APIs (`reporte-compras` + `rendimiento-compras`)
  y es el candidato a pantalla base; Rendimiento por Proveedor e Ingresos por
  Proveedor miden la cadena OC → recepción → venta con fuentes distintas
  (`Compras_Producto_Talla` + `Productos_Recepcionados` vs. `Dte_Productos` +
  `Movimientos_Producto` INGRESO). Ingresos por Proveedor además vive en una ruta
  legacy (`verReporteDespachosProveedor/`) y en una carpeta con espacio
  (`modulo reportes/`): mover template y ruta a `reportes/ingresos-proveedor/` es
  el primer paso.
- 13 nombres JS globales repetidos: `anioActual`, `datosReporte`, `exportarExcel`,
  `fmt`, `formatearNumero`, `mostrarError`, `mostrarLoading`, `params`, `render`,
  `renderChart`, `labels`, `tbody`, `aviso`. `productos_por_origen` e
  `inteligencia_compra` ya van en IIFE; `reporte_compras` son 1.223 líneas sin IIFE.
- Filtro de año: `anio` (compras, origen, rendimiento) vs. fechas libres
  (ingresos); sucursal como `sucursal` (compras, origen) vs. `sucursal_id`.

## 6. Verificación hecha en este frente (26-sep-2026)

- `check_tpl.py` / `check_js.py` / `check_hooks.py` sobre los 21 templates + parcial:
  OK; ningún gancho perdido (solo se añadió `{% include %}` y atributos `title`).
- Smoke con `django.test.Client` (BD local desechable, usuario `javier`, sesión
  sucursal 1 / empresa 1802 / EDEL): 21/21 páginas en HTTP 200 con la tira,
  familia/activo correctos, enlace activo resaltado y número de enlaces esperado
  (6 / 7 / 3 / 5).
- `manage.py test app.tests.test_reportes_correcciones --keepdb`: 22 tests OK.

## 7. Segunda etapa (28-sep-2026): filtros compartidos de verdad + grupos con pestañas

**Decisión de alcance.** La fusión en un único documento (un template con los JS de
3 reportes) se descartó: son ~10.000 líneas de JS con decenas de globals que chocan
(§5) y el usuario no ganaría nada que no dé una pestaña con URL propia. Se hizo:

### 7.1 Filtros compartidos (paso 0.1 y 0.2)

- Helper `window.FiltrosReporte` en `_familia_reportes.html`: `leer()` (canónicos
  `fecha_inicio`, `fecha_fin`, `mes`, `anio`, `sucursal_id`, `marca_id`; acepta los
  alias `fecha_desde/desde/fecha_hasta/hasta/sucursal/marca`), `completar(f)`
  (mes → rango; rango de un mes → mes; año desde mes/fecha), `reflejar(estado)`
  (`history.replaceState`; el período es un bloque: si el reporte informa una clave
  de tiempo, las demás se borran; sucursal/marca que el reporte no maneja pasan
  intactas; `''` borra) y `ponerValor(el, v)` (en `<select>` solo si existe la opción).
- Los 20 reportes con filtros compartidos ponen en sus controles lo que trae la URL
  ANTES de su primera consulta y reflejan su estado al consultar. Sin parámetros
  la primera llamada a cada API es idéntica a la de antes (verificado reporte por
  reporte en Chrome headless contra la BD local). Las APIs NO cambiaron: cada
  template traduce canónico ↔ su nombre (`fecha_desde`, `sucursal`…). La
  unificación de nombres queda hecha a nivel URL; en las APIs no hace falta.
- Mercadería en tránsito no tiene período ni sucursal compartida: no lee ni
  refleja, y deja la URL como llegó para que el siguiente reporte la reciba.
- Decisiones por reporte que conviene conocer:
  - Comparativo y Productos vendidos (presets): un mes en curso abre en MTD, el año
    en curso en YTD, el año pasado completo en "año completo", el resto en rango
    personalizado. Reflejan el período que resolvió el backend; el preset en sí no
    viaja.
  - Compras y Origen trabajan por año (`completar().anio`); si el año coincide con el
    `mes`/rango que ya traía la URL, no lo pisan (la tira lo sigue pasando).
  - Ingresos por proveedor: un `anio` suelto abre 1-ene..31-dic (tope hoy).
  - Existencias por marca: la opción "(actual)" se refleja con el id de la sucursal
    actual; "todas" viaja como `sucursal_id=todas`.
  - Despachos a tiendas: su sucursal es la tienda DESTINO, no se mapea a `sucursal_id`.
  - Inteligencia, Existencias por marca/sucursal, Movimientos y Quiebre siguen sin
    consultar solos: dejan los controles puestos y esperan el botón.
- Limitaciones conocidas: "Todas" se refleja borrando `sucursal_id`, así que al
  volver a Ventas por sucursal o Documentos emitidos se abre la sucursal actual (su
  default); `vendedor_id`, categoría, departamento, `fecha_corte`, `dias` y CD son
  filtros propios y no viajan.

### 7.2 Tokens CSS (paso 0.3)

Bloques `:root { --nexo-* }` redundantes retirados donde el valor era idéntico al de
`nexo-design-system.css` (existencias, existencias-marca, resumen, movimientos,
inteligencia: bloque entero o casi). Se conservaron las variables que no existen en el
design system o difieren (`--nexo-gray-600/400/200`, `--nexo-transition`,
`--nexo-radius`, `--nexo-error:#f06548` y `--nexo-shadow-md` con .08 en plan de
liquidación/inteligencia). Estilos computados de toda la `.page-content`: 0 diferencias.
El paso 0.4 (homogeneizar los 9 patrones de header) NO se hizo.

### 7.3 Grupos con pestañas (la "fusión" visible)

| Grupo | Pestañas | Menú |
|---|---|---|
| Ventas | Por sucursal · Comparativo · Global por empresa | 1 entrada "Ventas" (antes 2; Global no estaba) |
| Stock | Por marca · Por sucursal · Resumen | 1 entrada "Stock" (antes 3) |
| Cadena logística | Despachos · Tránsito · Diferencias | sección nueva "Reportes Logística" con 1 entrada (antes solo Despachos, bajo Existencias) |

- En la tira, cada grupo es un bloque `.familia-grupo` con título; solo se pinta si
  el usuario ve al menos una pestaña (mismo `puede_ver_opcion_tag` que antes).
- En el menú la entrada abre la primera pestaña que el usuario puede ver y muestra
  los nombres de las pestañas como subtítulo (el buscador del menú los encuentra).
- Resultado: 21 pantallas → 15 entradas de reporte (Compras 5, Ventas 4, Stock y
  existencias 5 contando Existencias/Movimientos/Quiebre/Liquidación, Logística 1).

### 7.4 Ingresos por proveedor

Ruta `reportes/ingresos-proveedor/` (mismo nombre de URL `verReporteDespachosProveedor`);
la ruta vieja redirige con su query string. Template movido (git mv) a
`modulo_reportes/reporte_ingresos_proveedor.html`: ya no hay carpeta con espacio.
`URL_PERMISO_MAP`, catálogo de permisos e `inicializar_permisos` apuntan a la ruta
nueva (la OpcionMenu ya creada en BD conserva la vieja y funciona por la redirección).

### 7.5 Verificación

- Chrome headless (CDP) contra runserver + BD local: 21/21 reportes sin errores JS ni
  HTTP ≥ 400; recorrido por la tira haciendo clic (ventas → comparativo → global →
  documentos → productos; marca → sucursal → resumen → liquidación; compras →
  ingresos → origen; despachos → tránsito → diferencias): 23/23 comprobaciones de
  lo que recibe cada API.
- Tests nuevos: `test_reportes_fusion_etapa2.py` (ruta y redirección, home legacy
  retirado, grupos y permisos en la tira, menú agrupado, cada reporte lee y refleja)
  y `test_excel_notas_existencias.py`.
