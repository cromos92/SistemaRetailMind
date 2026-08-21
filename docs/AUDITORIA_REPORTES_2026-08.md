# Auditoría integral del módulo de Reportes — 2026-08-20

**3ª auditoría del módulo** (delta sobre las del 22-jul y 25-jul-2026). Ejecutada con 9
agentes en paralelo sobre el código commiteado (working tree limpio) y la BD de
**producción** (`retail`, solo SELECT — 0 escrituras detectadas por la guarda de la
suite en todas las invocaciones). Oráculos al peso sobre **julio 2026** (período
cerrado), vistas invocadas vía RequestFactory con usuarios reales (admin `javier` y
restringido/cajero `andrybethca`, empresa 1320). Scripts de oráculos preservados en
`docs/auditoria_reportes_2026-08_oraculos/` (32 archivos, 8 grupos).

**Pregunta central: ¿la información que muestran los reportes es correcta?**
Respuesta corta: **el corazón del módulo (ventas, documentos, existencias base,
movimientos, compras integral) cuadra AL PESO contra oráculos independientes — delta $0
en decenas de KPIs.** Lo que miente vive en: (1) KPIs "derivados" que mezclan universos
(salud de existencias, rendimiento de compras, plan de liquidación), (2) los reportes
legacy de `views.py`, y (3) un bug de EMISIÓN (boletas $0) que hace subestimar a todos
los reportes de cabecera. Más 2 fugas de datos entre empresas.

---

## 1. Tabla de veredictos

| # | Reporte | Veredicto | Lo esencial |
|---|---|---|---|
| 1 | ventas-global | **OK** (números) | $163.584.440 = oráculo exacto; NC restan; sin gate de rol (P1) |
| 2 | ventas-sucursal (tabs) | **OK** | Sucursales y vendedores = oráculo exacto; NC nunca imputadas a vendedor (P2) |
| 3 | diagnóstico cuadratura-vs-reporte | **RIESGO** | Ya NO explica la diferencia: día top $653.620 con las 3 pestañas vacías (P1) |
| 4 | comisiones-vendedor | **OK** | $2.044.564 = oráculo (delta $2 redondeo); Excel = JSON 4/4; permisos finos OK |
| 5 | ventas-comparativo | **OK** | Netas = oráculo exacto; canales cierran al peso |
| 6 | comparativa-mensual | **OK** | F-16 vivo; julio $155.843.792 = oráculo exacto |
| 7 | productos-vendidos | **OK** | $155.129.379 / 5.281 u = oráculo exacto; neto/bruto resuelto (delta $1 sobre $8M) |
| 8 | documentos-vendedor | **OK** | Doble conteo POS cerrado; fila = drill-down = oráculo |
| 9 | documentos-emitidos | **OK** | Paginación real; Excel = JSON exacto (4.020 filas); destapa el bug boletas $0 |
| 10 | ventas-internet | **OK** | $21.113.113 / 472 tk = oráculo; marketplaces rotulados; gap FACTURADO_EXTERNO $750K (P2) |
| 11 | productos-origen | **RIESGO** | Números exactos; rótulos desinforman ("Compra formal 0%"); sin gate de rol; 25s |
| 12 | compras integral | **OK** | Backlog jul-25 TODO cerrado; julio = oráculo exacto en 6/6 KPIs; 9.000q/44min muerto (30q/5s) |
| 13 | compras · pestaña Rendimiento | **MIENTE** | "Inversión año $2.212M" vs $707M real (3,1x): suma la apertura de migración (P0) |
| 14 | rendimiento-proveedor | **OK** | Match por texto eliminado; sin ceros falsos; cobertura de atribución 24,7% (declarada) |
| 15 | recepciones-detallado | **REDUNDANTE** | Huérfano confirmado (0 consumidores); 51% de unidades "Sin asignar" |
| 16 | despachos-detallado | **REDUNDANTE + MIENTE** | Huérfano; NC y descartados SUMAN ($92,7M vs $89,6M vivas); N+1 125q/21s |
| 17 | existencias-marca | **OK** | Stock = oráculo 0 difs; columna Original (19-ago) sigue cuadrando 25/25 celdas |
| 18 | existencias-sucursal | **MIENTE** (KPIs salud) | Tabla y totales exactos 5/5; pero "% viejo 155,4%" (real 71,3%) y filtro marca mezcla universos (P1) |
| 19 | resumen-existencias | **OK** | Corte actual y histórico exactos; vaciado FALLADOS/NICK3 perfecto; filtro categoría padre → 0 silencioso (P1) |
| 20 | quiebre-talla | **OK** | 7/7 celdas exactas vs kardex; apertura detectada y marcada; KPI cabecera sin tope (P1) |
| 21 | movimientos-sucursal | **OK** | Columna Descripción sin costo (12q ambos modos); Excel = JSON; kardex cuadrado |
| 22 | despachos-tiendas | **OK** | 10.046 u = oráculo canónico exacto; sin dobles ni mezcla de empresas |
| 23 | diferencias-recepción | **OK** | 10/10 KPIs = oráculo exacto; filtro sucursal ciega el 100% del histórico (dato NULL legacy) |
| 24 | mercadería-tránsito | **RIESGO** | 14/14 campos = oráculo; pero "Llegó todo" (19-ago) deja falso positivo permanente (P1) |
| 25 | inteligencia-compra | **RIESGO** | Internos exactos; demanda bruta +12% (no resta NC); sell-through con apertura (26,8% vs 60,2% real); sin permiso |
| 26 | plan-liquidación | **MIENTE** | Totales/Excel exactos; pero recomienda **Reponer** marcas que hay que **Liquidar** (P0) |
| 27 | kardex legacy (movimientos + agrupado) | **MIENTE** | Apertura de migración doble-contada: saldo cuadra 8/20 SKUs (sin apertura: 19/20); 41.189 SKUs afectados |
| 28 | fifo-general legacy | **OK/REDUNDANTE** | Reescrito a 1 query (el 500 histórico murió); aporta poco post-reconciliación |
| 29 | despachos-por-proveedor legacy | **MIENTE** (rótulo) | "Despachado/Saldo restante" = métrica muerta (0 EGRESOS sobre DTE compra en TODO el histórico); 88q/15s |
| 30 | reportes/existencias legacy (API) | **OK** | KPIs exactos, scoping OK |
| 31 | reportes/existencias legacy (Excel) | **ROTO + FUGA** | Sin scoping empresa, sin excluir_de_analitica, categoría plana, N+1 ~2,4M queries (no termina) (P0) |

---

## 2. Hallazgos P0 (miente con plata / fuga / roto)

### P0-1 · Fuga entre empresas: `api/reportes/diagnostico-cuadratura/`
`views_modulo_reportes.py:1852` hace `Sucursal.objects.get(id=GET['sucursal_id'])` sin
intersectar con el alcance del usuario. Cualquier usuario con
`reporte_ventas_sucursal.puede_ver` — **que incluye al rol cajero** (verificado en BD) —
obtiene la cuadratura de caja completa, totales y lista de DTEs con montos de cualquier
sucursal de cualquier empresa cambiando el parámetro. Es el único endpoint del módulo
mapeado en el middleware pero sin scoping interno.

### P0-2 · Fuga + roto: `api/exportar-existencias-excel/` (legacy)
`views.py:33906-33920`: el Excel reconstruye el queryset **sin** el filtro multi-empresa
que el JSON sí aplica (33647-33665) → un rol con `reporte_existencias.puede_ver`
exporta stock/costos/PVP de TODO el holding. Además no filtra `excluir_de_analitica`
(2.063 filas de más), el filtro de categoría es plano (padre → menos filas que la
pantalla) y tiene un N+1 de LoteProducto por fila × 2 hojas: con el universo sin filtro
(608.666 filas) son ~2,4M queries — **el botón no puede terminar**. Recomendación:
eliminar el export (los reportes nuevos de existencias tienen exports sanos) o
reescribirlo desde `_get_existencias_datos`.

### P0-3 · Pestaña "Rendimiento" de compras miente 3,1x
`api_rendimiento_compras` (`views_modulo_reportes.py:5637`): "Inversión (costo) /
entrada año 2026" = **$2.212.577.474** cuando la compra real del año es **$707,4M**.
Causa medida: `CONCEPTOS_ENTRADA` (:5618) incluye `INGRESO_INICIAL` y en 2026 eso es la
**apertura de la migración** ($2.005,7M, foto del 22-ene); además `RECEPCION_COMPRA`
tiene 0 movimientos en 2026 (las recepciones reales se escriben como INGRESO_MANUAL).
"Entrada stock 168.287 u", "Rotación 15,3%" y "stock sin vender 140.636 u" son
artefactos (~92% apertura). Extra: cuenta tickets **PENDIENTE** como vendido
(+$28,4M, +3,9%) y filtra por `Ticket.fecha` (auto_now) — el P0 de julio que seguía
abierto. 19q pero 45,9s.

### P0-4 · Plan de liquidación recomienda "Reponer" lo que hay que "Liquidar"
`_scope_plan` (`views_inteligencia_compra.py:549-552`): las VENTAS no filtran
`excluir_de_analitica` pero el STOCK sí → numerador y denominador en universos
distintos. Medido: ventas TTM de productos excluidos = 31.515 u / $52,8M. La fila
PAOLA muestra rotación **147,56** / GMROI 130 / cobertura 0,1 m → acción **"Reponer"**;
limpia da rotación **0,56** / GMROI 0,87 / cobertura 21,5 m → acción real
**"Liquidar"**. REAL SPORT igual. El KPI "Marcas a reponer: 22" incluye ambas filas
corruptas. Los totales del ranking, el detalle y el Excel sí cuadran exactos.

### P0-5 · Transversal (bug de EMISIÓN): boletas con cabecera $0 y pagos reales
**228 boletas electrónicas** entre 04-feb y 05-ago con `monto_con_iva=0` (o menor al
pago) y pagos reales — julio: 38 boletas / **$1.854.280**, 37 con pago VENTA_INTERNET
(ej. dte 2193698 folio 286227: cabecera $0, ticket y pago $199.950). Es el problema de
fondo de las "boletas con cabecera en 0" (la alerta se quitó del listado en agosto; el
bug sigue vivo). Consecuencia en reportes: **todo lo basado en cabecera DTE subestima
~$1,85M/mes** (documentos-emitidos, ventas-global/sucursal, cuadratura); en
documentos-emitidos se ve como "métodos de pago $171,4M > brutas $169,4M". No es bug de
los reportes: hay que arreglar la emisión de boletas de venta internet (y decidir si se
corrigen las 228 cabeceras históricas).

---

## 3. Hallazgos P1

1. **Kardex legacy miente el saldo** (`reporte_movimientos_kardex` views.py:9139-9158 y
   `reporte_kardex_agrupado` :9225): suman la apertura sintética (`INGRESO_INICIAL` ref
   `MIGRACION_LARAVEL`) ENCIMA del kardex legacy migrado. Muestra aleatoria: saldo ==
   stock solo **8/20 SKUs**; excluyendo la apertura, 19/20. **41.189 SKUs** tienen
   apertura sintética; las pantallas están vivas (modal de gestionMovimientos). Fix:
   excluir la apertura del saldo (o netearla) — mismo criterio que ya usan
   movimientos-sucursal y existencias-marca.
2. **existencias-sucursal, KPIs de salud** (`views_modulo_reportes.py:3853-3868`):
   (a) `pct_stock_viejo` = lotes de TODA la sucursal ÷ stock solo-analítica → PAO3
   muestra **155,4%** (real 71,3%); (b) con filtro de marca, vendidas_30/lotes ignoran
   `marca_id` → cobertura "2 días" cuando la de la marca es 9; (c) `vendidas_30` no
   filtra analítica (+25%); (d) "Recibido hist." doble-cuenta la apertura (27.308 u
   dobles en PAO3).
3. **resumen-existencias: filtro por categoría PADRE devuelve 0 en silencio**
   (`views_resumen_existencias.py:104-105,146-147`): el dropdown promete "toda la
   rama" pero la vista no expande (`_expandir_categoria_ids` no se usa). Calzado →
   0 pares vs 61.653 reales.
4. **mercadería-tránsito vs "Llegó todo"**: el botón del 19-ago ingresa con concepto
   `ANULACION_REGULARIZACION` (`views.py:7616`) y el reporte solo cuenta
   `CONCEPTOS_TRASPASO_ENTRADA` (`views_modulo_reportes_diferencias.py:592-598`) →
   folio 17098 queda "SIN_RECIBIR 12 u / $227.244" para siempre pese a estar
   RECEPCIONADO_COMPLETO. Pasará con **cada uso** del botón. Fix: sumar
   `ANULACION_REGULARIZACION` (y `CORRECCION_STOCK`) a las entradas del neteo.
5. **quiebre-talla: KPI cabecera sin tope** (`views_modulo_reportes_tallas.py:694-717`):
   `unidades_perdidas_estimadas` suma extrapolaciones de celdas con ≤7 días disponibles
   y tallas dudosas: en ene-mar da 29.615 u donde UNA celda (balón CAFU ×90 lineal)
   es el 90%. Capear el multiplicador y excluir dudosas del agregado (los flags por
   talla ya existen).
6. **inteligencia-compra**: (a) demanda BRUTA — no resta `CONCEPTOS_REINGRESO`
   (NC/devoluciones): +12% medido en SKECHERS, infla velocidad, pronóstico y compra
   sugerida; (b) sell-through 2026 incluye la apertura de migración en "ingresado":
   muestra 26,8% cuando sin apertura es 60,2%; (c) **sin permiso** — cajero obtuvo 200
   con costos/margen/GMROI (scoping por empresa sí funciona).
7. **plan-liquidación (además del P0-4)**: (a) `por-anio` es la única ruta sin
   `@requiere_permiso` — cajero obtuvo 200 con totales; (b) el bucket "≥1 año a
   liquidar $1.087,7M" sobrestima ≥$220,7M (año calendario vs 365 días reales; el
   detalle usa días exactos y el Resumen del mismo Excel usa años×365 — inconsistencia
   interna); (c) la "edad" es del LOTE, que **se resetea con cada traspaso**: $435,7M
   (28,5% del capital de tiendas) figura "reciente jun-ago" cuando las compras reales
   de tiendas en ese período fueron 10 u — subestima la urgencia del stock invernal
   redistribuido.
8. **diagnóstico cuadratura-vs-reporte ya no explica la divergencia**
   (`views_modulo_reportes.py:1780+`): día top de julio, diferencia $653.620 con
   `solo_en_cuadratura=0 / solo_en_reporte=0 / tickets_sin_dte=$0` — la causa (10 NC
   ANULACION "en ambos" con tratamiento distinto) no se surfacea; además acepta
   tt=VENTA que la cuadratura excluye.
9. **despachos-detallado suma NC y descartados** ($92,7M vs $89,6M vivas, cuadre
   exacto) — irrelevante si se elimina (huérfano), grave si alguien lo revive.
10. **Gates de rol faltantes** (fail-open del middleware, scoping por empresa sí
    presente): ventas-global (página+API), productos-origen, inteligencia-compra,
    recepciones/despachos-detallado, plan-liquidacion/por-anio, feed
    `api/reportes/vendedores/` (metadatos cross-empresa). OJO: sus códigos de permiso
    **no existen** en OpcionMenu — cerrar esto exige crear OpcionMenu+PermisoRol
    ANTES de decorar/mapear (fail-closed: si no, 403 para todos, como el incidente
    del 05-ago).
11. **Compras, cabecera anual**: "Costo promedio/unidad" inflado ~2,4x ($35.071 vs
    $14.536 real) porque las unidades salen de los 192 docs con líneas y la plata de
    los 585 (74% sin líneas, migración Laravel); "Cumplimiento entregas 100%" es
    estructural (un DTE jamás recepcionado no aporta esperadas). Falta un aviso de
    cobertura en la UI (en julio la ceguera es 6,8%; en el año es total).

## 4. Hallazgos P2 (selección)

- **Rendimiento sobre umbral (5s)**: api_rendimiento_compras 45,9s · resumen-existencias
  histórico 35,8s · Excel plan-liquidación 33s · Excel existencias-sucursal 26,4s ·
  productos-origen 24,9s (loop Python; el oráculo hace lo mismo en 1 query DISTINCT ON) ·
  inteligencia-compra 21,3s · despachos-detallado 21s (N+1 125q) · despachos-por-proveedor
  15,2s (88q, 4q/DTE) · existencias-sucursal 13,8s · plan-liquidación 16,7s.
- **ventas-internet**: 14 pedidos FACTURADO_EXTERNO de julio ($750.174) invisibles en
  todos los reportes de internet (sin ticket PAGADO).
- **Tab vendedores**: $6.066.392 de NC caen a "Sin vendedor" (falta el fallback
  `documento_afectado__vendedor` que comisiones sí usa).
- **Compras**: tabla Top Proveedores $81,2M vs KPI $73,8M (OC sin DTE inyectan
  `costo_ordenes` como inversión); "vs año anterior" con período relativo ancla al
  31-dic (compara ago-2026 contra dic-2025).
- **rendimiento-proveedor**: cobertura de atribución 24,7% — el JSON/pantalla lo
  declaran con asteriscos; el Excel pierde la advertencia.
- **diferencias-recepción**: el filtro/agrupación por sucursal ciega el 100% de los
  faltantes históricos (36 líneas / $906.571 con destino NULL legacy → bucket "Sin
  asignar"); los escritores actuales siguen dejando `sucursal_destino NULL`
  (~150-270 u/mes) — deuda que también infla los cortes históricos del resumen.
- **mercadería-tránsito**: detalle no cuadra en $ con el listado (costo promedio vs
  Max por SKU, −12% medido); la ventana default 90d esconde el caso más grande
  (guía de abril, 300 u jamás recibidas = 2/3 del pendiente del año).
- **productos-origen (rótulos)**: "Compra formal 0%" y el semáforo de "altas
  irregulares" desinforman — en este ERP el stock entra al crear el producto, no como
  RECEPCION_COMPRA; el 12% INGRESO_MANUAL marcado "irregular" es en gran parte el
  flujo normal de recepción-DTE.
- **Cobertura/rotación divergen entre reportes hermanos** sin rótulo: inteligencia
  (÷pronóstico) 23,6 m vs plan (÷TTM) 18,1 m para la MISMA marca el mismo día (+30%);
  existencias-sucursal usa ventana 30d (348d) vs plan 365d (~274d). Y "% viejo por
  edad de lote" (62,1%) vs "dead por falta de venta" (44,2%): 18pp entre las dos
  definiciones de salud.
- **documentos-emitidos (UI)**: el listado pinta las NC en positivo sin distinción
  (el Excel sí las firma); NC sin pagos muestran método "Efectivo" (fallback).

## 5. P3 (lista corta)

atributo4 aún calculado en productos-vendidos (payload muerto) · columna Original de
existencias-marca invisible con `solo_con_stock` default y sin nota · KPI salud sobre
universo truncado sin rótulo · Excel sin nota de truncado/exclusión que la pantalla sí
muestra (marca, resumen) · `verificar_disponibilidad_historico` sin scoping (metadata) ·
`obtener_proveedores_para_reporte` lista proveedores del holding a cualquier autenticado ·
GMROIs absurdos con denominador chico (CAFU 299) · daño = 0 en TODA la historia (hay
pantalla, sigue sin haber dato) · comentario stale en urls.py:1434 · NC ANULADA seguiría
neteando en tránsito (0 casos hoy) · riesgo NC-oculta/descartado sin filtrar en
ventas-global (julio $0).

---

## 6. Delta vs auditorías de julio

- **Los 16 fixes de julio (F-01..F-18 + los 4 del 25-jul) están TODOS vivos y
  commiteados** (verificado patrón por patrón, archivo:línea en el reporte de Fase 0).
- **Backlog jul-25 cerrado después de julio**: todo el P0 de compras-integral, doble
  conteo documentos-vendedor, neto/bruto y sell-through de productos-vendidos, F-12
  (match texto), F-14 (FIFO 447q→1q), F-17 (compras 9.000q→~30q), scoping de los
  detallado, dashboard compras estratégico y ruta duplicada eliminados.
- **Backlog que SEGUÍA abierto y esta auditoría confirma/re-mide**: `Ticket.fecha` en
  cuadratura y pestaña Rendimiento (→ P0-3), F-13 despachos-detallado N+1, eliminación
  de recepciones/despachos-detallado y existencias-por-sucursal legacy, F-15 parcial
  (2 copias de parseo de fechas), F-06 (funciones muertas).
- **Permisos**: el hallazgo transversal de julio ("ningún JSON en el mapa") quedó
  mayormente cerrado — el middleware ahora matchea por clave más larga y mapea página+
  API+export por reporte; los huecos restantes son los 6 endpoints del P1-10.
- El incidente "diferencias/tránsito 403 para todos" (05-ago) está **cerrado en prod**
  (OpcionMenu 88/89 + PermisoRol verificados en BD).

## 7. Suite de regresión

`retailmind/_test_reportes_readonly.py` **extendida en esta auditoría** con
`quiebre_talla`, `diferencias_recepcion` y `mercaderia_transito` (smoke: 200 + solo
SELECT + presupuesto q/ms). Corrida: **9 PASS / 0 FAIL**. Sigue pendiente (para la fase
de fixes): oráculos profundos de los 3 nuevos (los scripts están en
`docs/auditoria_reportes_2026-08_oraculos/`), cobertura de exports Excel/PDF, y
`api_rendimiento_compras` (agregarla DESPUÉS del fix P0-3, si no nacería en FAIL).

---

## 8. Plan priorizado (propuesta — nada aplicado aún)

**Fase A — P0 (1-2 sesiones):**
1. Scoping en `api_diagnostico_cuadratura` (patrón `filtrar_queryset_por_sucursal` que
   ya usan sus vecinos). Chico.
2. `exportar_existencias_excel` legacy: eliminar y redirigir a los exports nuevos
   (recomendado) o reescribir sobre `_get_existencias_datos`. Chico si se elimina.
3. `api_rendimiento_compras`: excluir apertura de migración de CONCEPTOS_ENTRADA,
   mapear INGRESO_MANUAL↔recepciones reales, solo tickets PAGADO, `created_at`.
   Mediano (o decisión: ocultar la pestaña hasta arreglarla).
4. `_scope_plan` del plan de liquidación: filtrar `excluir_de_analitica` en `mov_base`
   (1 línea) + re-verificar acciones. Chico.
5. Boletas $0 (emisión ventas internet): diagnóstico del flujo que crea el DTE con
   cabecera 0 + decisión sobre las 228 históricas. Mediano — módulo emisión, no reportes.

**Fase B — P1 (2-3 sesiones):** kardex legacy (excluir apertura del saldo) ·
KPIs salud existencias-sucursal (mismo universo num/den + marca) · expandir rama en
resumen-existencias · "Llegó todo" en tránsito (1 concepto más en el neteo) · cap del
KPI quiebre-talla · inteligencia-compra (restar reingresos + apertura en sell-through) ·
por-anio con permiso · bucket 365d en liquidación · diagnóstico-cuadratura que explique
NC ANULACION · gates de rol faltantes (crear OpcionMenu+PermisoRol primero, luego
decorar; correr `inicializar_permisos` actualizado) · aviso de cobertura en compras anual.

**Fase C — P2/P3 (limpieza):** eliminar recepciones/despachos-detallado y
despachos-por-proveedor legacy (o arreglar sus métricas muertas) · unificar definición
de cobertura entre reportes hermanos (o rotular la diferencia) · rótulos de
productos-origen · performance de los 10 endpoints >5s · fallback NC→vendedor en tab
vendedores · advertencia de atribución en Excel de rendimiento-proveedor · writers de
`sucursal_destino NULL`.

**Regla para toda la fase de fixes**: cada fix entra con su oráculo portado a
`_test_reportes_readonly.py` (los scripts ya están en el repo) y se re-corre la suite
completa contra prod antes de commitear.

---

## 9. FASE A — APLICADA (2026-08-20, misma jornada; verificada read-only contra prod)

Regla del usuario respetada en todo: **los valores de la cuadratura de caja no se
tocan** (cuadra por día); ahí solo cambió el control de acceso.

| Fix | Cambio | Verificación medida |
|---|---|---|
| P0-1 diagnóstico-cuadratura | Sucursal pedida se intersecta con EmpresaUser (patrón existencias-marca) | Restringido → sucursal ajena **403**; admin día-top: diferencia **$653.620 idéntica** a la auditoría (valores intactos) |
| P0-2 Excel existencias legacy | Helper `_queryset_existencias_reporte` compartido JSON+Excel (scoping, analítica, rama v1.2); FIFO en 1 query agrupada; tope 20.000 filas con nota; resumen sobre universo completo | Excel restringido: solo NICK1/NICK2, 0 fuera de alcance, 12.025 filas = JSON 12.025, 11 queries / 12s (antes: no terminaba) |
| P0-3 Rendimiento compras | Entrada excluye apertura de migración (`REF_SALDO_INICIAL_SINTETICO`); tickets solo PAGADO; `created_at` en vez de `Ticket.fecha` | Inversión 2026: **$715,6M** (antes $2.212M) — dentro de ±5% de las compras DTE reales; rotación 52,6% (antes 15,3% artefacto) |
| P0-4 Plan de liquidación | `mov_base` filtra `excluir_de_analitica` (mismo universo que el stock) | PAOLA: rotación **0,56** / GMROI 0,87 / acción **"Liquidar"** (antes 147,56/"Reponer"); REAL SPORT → "Monitorear"; KPI a-reponer 22→20 |
| P0-5 boletas cabecera $0 | **Causa raíz corregida respecto del borrador**: NO era la emisión — es `anular_factura_dte` (NC por línea) que desde el 23-abr reescribía la cabecera del documento ORIGINAL (`views.py` rama `usa_productos_afectados`): devolución total → $0; parcial → trataba el precio IVA-inclusivo como neto. **Fix aplicado**: la cabecera del original ya no se reescribe jamás (la NC documenta el crédito; el SII recibió la boleta completa); se mantiene la reducción de `dp.stock/activo` (tope anti-doble-NC) y la regla ANULADO-solo-OCULTA | Medido: **234 docs / $11.981.499** de déficit (232 boletas + 2 facturas), 226 en $0 exacto; último caso 12-ago → estaba VIVO. Tests: 68 de `test_cuadratura_nc`+`test_txt_dte` con **1 falla PRE-EXISTENTE** (probado contra HEAD con stash: falla igual) |

Suite extendida con 2 guards nuevos: `diagnostico_fuga` (403 con sucursal ajena) y
`rendimiento_compras` (inversión dentro de ±35% del oráculo DTE) — ambos PASS.
`test_scoping_reportes` 24/24 PASS en SQLite.

## 10. FASE B — APLICADA (2026-08-20, misma jornada; 6 agentes, verificada read-only contra prod)

| Fix (P1) | Cambio | Verificación medida |
|---|---|---|
| Kardex legacy (movs + agrupado) | La apertura sintética ya no suma al saldo, con **re-anclaje** cuando el legacy está incompleto o el SKU es solo-apertura; CANCELADO no suma; filas con `es_apertura` | Muestra 20 SKUs: **20/20 saldo==stock** (antes 8/20); PT 467549→0, PT 214822→5 ✔ |
| KPIs salud existencias-sucursal | Numerador y denominador SIEMPRE mismo universo (analítica+marca+sucursal); "Recibido hist." sin apertura | PAO3: pct_viejo **155,4%→71,3%**; con marca: 29.101%→2,7%. Nota: la "cobertura 9 días" del informe original venía de un oráculo con universos mixtos — el valor consistente es 370d |
| Diagnóstico cuadratura | Nueva pestaña "NC trato distinto" + Explicada/Residual en el modal (helper de LECTURA; `_calcular_cuadratura_data` intacto) | 11-jul/suc 7: explicada **$653.620 / residual $0**, 10 NC listadas |
| Compras anual | `cobertura_lineas` en payload + aviso en UI cuando <90% | 2026: 256/585 docs con detalle (43,8%) — ahora visible |
| Mercadería-tránsito | "Llegó todo" cuenta como recepción (`ANULACION_REGULARIZACION`/`CORRECCION_STOCK` INGRESO+COMPLETADO — la reversa EGRESO no suma); detalle valoriza igual que el listado | Folio 17098: **pendiente 0, fuera de SIN_RECIBIR** (delta exacto −12 u/−$227.244); detalle==listado en los 3 docs top |
| Quiebre-talla | KPI cabecera conservador: excluye dudosas y <7 días disponibles, cap 3x, todo itemizado en `unidades_perdidas_excluidas` | Ene-mar: 29.617→**577,6 u** con 29.040 excluidas desglosadas; filas individuales intactas |
| Inteligencia-compra | Demanda NETA (resta CONCEPTOS_REINGRESO) en toda la cadena (velocidad, TTM, pronóstico, curva tallas, GMROI); sell-through sin apertura; `@requiere_permiso('inteligencia_compra')` | SKECHERS: vel_dia 15,7→**13,8**; STR 24,5%→**55,2%** |
| Plan-liquidación | Buckets por DÍAS exactos alineados a la escala de dcto (<6m/6-12m/1-2a/2+a); fila==resumen 1:1; `por-anio` con permiso | "≥1 año": $1.086,6M→**$866,0M** (−$220,6M); tramo t2: 3.576 productos todos 365-730d/25%, 0 fuera de rango |
| Resumen-existencias | Filtro por categoría PADRE expande la rama v1.2 (actual + histórico; exports heredan) | Calzado: 0→**61.687 pares** |
| Gates de rol | Decoradores en ventas-global, productos-origen, inteligencia-compra (códigos nuevos), detallados (`reporte_compras` existente), feed vendedores scopeado (403 ajena) | `inicializar_permisos` extendido (3 códigos, ver+exportar para administrador/administración/jefe_local, idempotente, 11/11 tests). **Fail-closed hasta correr el command en prod** |

Tests SQLite de la fase: **40/40** (reportes) + **16/16** (inteligencia) + **12/12** (tránsito/tallas) + **8/8** (kardex) + **11/11** (permisos) + regresiones 64/64 + 9/9 + 16/16 + 24/24. Suite prod final: **103 PASS / 6 FAIL** = 3 fail-closed esperados (se resuelven con `inicializar_permisos`) + 3 pre-existentes de datos (comparativa — parcialmente explicado por las 234 cabeceras SIN backfill —, categorías planas, atributo4).

**Command de backfill ENTREGADO (sin aplicar):** `restaurar_cabeceras_boletas_nc` — 16/16 tests (incl. dry-run=0 escrituras, idempotencia, `--revertir`); dry-run real: **228 candidatos / $11.965.836**, 8 a revisión manual. OJO: detectó 2 boletas dañadas más que ayer — el daño sigue mientras el fix de `anular_factura_dte` no se despliegue.

**Deuda que quedó para Fase C** (además de la sección 8): pintar badge `es_apertura` en el modal de gestionMovimientos; eliminar los reportes huérfanos y `exportar_existencias`-legacy redundantes; performance de los endpoints >5s; edad de lote reseteada por traspaso (necesita diseño de propagación de fecha); umbral relativo-a-ventana del KPI quiebre-talla si el absoluto de 7d recorta mucho en ventanas cortas; `get_or_create` en el setUp de `test_resumen_existencias_ocultos` (choca con seed de migración 0150).

---

## 11. FASE C — APLICADA (2026-08-21; limpieza + performance)

**Backfill del P0-5: APLICADO por el usuario el 21-ago 11:18** — 228 cabeceras
restauradas ($11.965.836), verificación post-escritura 0 con déficit, re-dry-run
idempotente (0 candidatos), y confirmación independiente: brutas julio por cabecera
== pagos AL PESO. Quedan 8 docs en revisión manual (~$700K en pagos). Snapshot:
`retailmind/_restauracion_cabeceras_20260821_111853.csv`.

| Fix | Cambio | Verificación medida |
|---|---|---|
| Endpoints huérfanos | `api_reporte_recepciones/despachos_detallado` **ELIMINADOS** (338 líneas + rutas + tests + entradas de la suite); helpers de alcance conservados | check limpio; test_scoping 18/18 |
| Tab vendedores | NC imputadas en cascada NC→documento_afectado→"Sin vendedor" (patrón comisiones), también en drill-down | "Sin vendedor": $6.066.392 → **$2.414.255/11 NC** (predicción exacta); Σ brutas/devoluciones/netas idénticas al peso — la plata solo cambia de fila |
| productos-origen | Rótulos honestos (INGRESO_MANUAL/INICIAL = alta normal, sin semáforo rojo; grupos exhaustivos) + pipeline DISTINCT-ON acotado | Totales idénticos (20.6k SKU/56.8k u/$1.000M); 18,3s → 8,6-12,6s (la meta <5s requiere índice `(ProductoTalla_id, fecha, hora, id)` — propuesto, no creado) |
| Compras tabla↔KPI | OC-sin-DTE separado en `costo_ordenes_sin_dte` (ya no infla la inversión); proveedores solo-NC conservados; tendencia "vs año anterior" con la MISMA ventana desplazada (antes anclaba al 31-dic) | Tabla Σ == KPI **$74.068.332 delta 0**; ventana anterior de "mes": dic-2025 → jul/ago-2025 |
| Excel rendimiento-proveedor | Banner de cobertura de atribución + asteriscos + nota metodológica | A1 = "ATRIBUCIÓN 24.7% … 4.032 u sin atribuir" == JSON exacto |
| ventas-internet | Sección "Facturado externo (sin boleta propia)" APARTE (KPI + tab alertas + hoja Excel), sin tocar el total | 14 pedidos/$750.174 == oráculo; total principal intacto $21.113.113 |
| despachos-por-proveedor legacy | Métricas muertas eliminadas (despachado/saldo = siempre 0); renombrado "Ingresos por Proveedor"; bulk + default 90d declarado | 88q/15,2s → **9q/2s**; julio 61 docs/5.083 u == oráculo |
| Writer destino NULL | `anular_ticket_pendiente` (único writer activo, ~250 u/mes) ahora puebla `sucursal_destino` = sucursal dueña del SKU | Los demás conceptos ya no escriben NULL desde abr-2026. Backfill legacy NO hecho: 546.118 movs/2,8M u históricos — decisión aparte |
| Badge apertura | Modal kardex marca las filas de apertura ("no suma al saldo") | node --check OK |
| Performance (igualdad EXACTA verificada bajo snapshot) | inteligencia 6,9→3,4s (39→20q) · plan 11,4→6,8s · por-año 11,6→6,0s · Excel plan 22,4→17,1s (resto es openpyxl) · **resumen histórico 24,5→3,5s (7x)** y por categoría 10x | Igualdad campo a campo / byte a byte en cada endpoint; reconstrucción histórica reproducida idéntica (834/264 ocultos exactos) |
| Rótulos cobertura | inteligencia: "stock ÷ pronóstico 12m"; plan: "÷ venta TTM" — la divergencia del 30% entre hermanos quedó declarada | Fórmulas intactas |

Tests de la fase: 16/16 (reportes C) + 7/7 (legacy) + 15/15 (perf) + regresiones B
completas. Índice propuesto (decisión futura, con EXPLAIN): parcial
`Movimientos_Producto(fecha) WHERE estado='COMPLETADO'` y
`(ProductoTalla_id, fecha, hora, id)` para productos-origen.

**Deuda restante (menor):** backfill legacy de destino NULL (546k movs — decisión) ·
8 docs de cabecera en revisión manual · Excel plan >8s (openpyxl) · detalle plan
ordenado por última venta ~14s · dim=especialidad sin optimizar (multi-etiqueta) ·
existencias-por-sucursal legacy convive con los reportes nuevos (fusión opcional).

---

**PENDIENTE DE DECISIÓN (escribe en prod — no aplicado):** backfill de las 234
cabeceras históricas. **[RESUELTO — ver §11: aplicado el 21-ago.]** Diseño en el diagnóstico (scripts `scratchpad/boletas_cero/`):
command `restaurar_cabeceras_boletas_nc`, dry-run por defecto, fuente de verdad =
`Σ Dte_Productos.monto_item` (lo que llevó el TXT al SII, intacto tras la NC);
219/234 cuadran solos con pagos y ticket; 6 van a revisión manual (dte 900643,
901236, 2175143, 1166683, 2186646, 836011). Restaurar REALINEA lo local con el SII
(la boleta se subió completa antes de que la NC la rompiera). Follow-up aparte: un
TXT re-generado DESPUÉS de la NC sale con cantidades 0 (bug histórico "unidades en
0", misma raíz vía `dp.stock`).
