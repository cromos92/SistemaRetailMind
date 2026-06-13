# Auditoría: Trazabilidad de productos y Dashboards — SistemaRetailMind

> Análisis verificado contra el código **realmente enrutado** en `app/urls.py`. Donde un agente de exploración reportó algo, se confirmó leyendo la función real. Los hallazgos marcados ✅ descartado eran falsos positivos.

---

## 0. Aviso sobre código muerto (leer primero)

`app/urls.py` importa las vistas de productos/existencias desde:
- `views.py`
- `views_dashboard_home.py`
- `views_dashboards_kpi.py`
- `views_modulo_existencias_nuevo.py`

Existen **funciones homónimas no enrutadas** en `views_modulo_existencias.py` y `views_modulo_productos.py` (`crear_producto_manual`, `crear_producto_desde_recepcion`, `consumir_stock_fifo`, etc.). **No se ejecutan en producción.** Solo importan ruido y son la única vía que llama a la función `crear_producto()` con el bug H1. Recomendación: eliminarlas o documentarlas como deprecated.

---

## 1. Flujo de trazabilidad (vías reales)

### Modelos núcleo
| Modelo | Archivo | Rol |
|--------|---------|-----|
| `Producto` | `models/catalogo.py` | Catálogo por sucursal. `fecha_creacion` (auto_now_add, forzable). |
| `Producto_Talla` | `models/catalogo.py` | Stock por talla/SKU. `stock` = fuente de verdad del stock actual. |
| `Movimientos_Producto` | `models/inventario.py:159` | Kardex. `concepto`, `tipo_movimiento`, `fecha`/`hora` (del movimiento, puede ser retroactiva), `created_at` (auditoría real de inserción), FKs `dte`/`ticket`, `sucursal_origen`/`destino`. |
| `LoteProducto` | `models/inventario.py` | FIFO. `fecha_ingreso` ordena consumo; `cantidad_disponible` decrementa. |
| `Compras`→`Compras_Producto`→`Compras_Producto_Talla`→`Productos_Recepcionados` | `models/compras.py` | Cadena de compra/recepción. |
| `Ticket`→`Ticket_Productos` | `models/ventas.py` | Venta. Guarda `costo_fifo` y `lotes_utilizados` (JSON). |
| `Traspaso`→`Traspaso_Detalle` | `models/inventario.py` | Traspaso entre sucursales. |

### Hub central de stock
`registrar_movimiento_producto()` — `views.py:6613`. Crea `INGRESO_INICIAL` retroactivo si no había movimientos, escribe el kardex, ajusta `Producto_Talla.stock` y crea lote FIFO si el concepto está en la lista de ingresos (`views.py:6671`).

### Vía A — Creación MANUAL (producción)
`views.crear_producto_manual` — `views.py:20235` · URL `urls.py:590`.
Exige **proveedor + DTE**. Genera `Compras`/`Compras_Producto`/`Compras_Producto_Talla`/`Dte_Productos`/`Productos_Recepcionados` y registra movimiento **`INGRESO_MANUAL`** (`views.py:20474`). → Toda alta manual queda ligada a un documento de compra y deja kardex. **No existe el caso "producto creado sin movimiento".**

### Vía B — Creación/ingreso DESDE RECEPCIÓN
`views.crear_producto_desde_recepcion` — `views.py:18474` · URL `urls.py:516`.
`views.recepcionar_compra` — `views.py:9139` · URL `urls.py:428`.
Vincula `Productos_Recepcionados.producto_talla`, registra **`RECEPCION_COMPRA`**, crea lote FIFO. Catálogo centralizado en la sucursal activa; `sucursal_destino` indica el destino físico.

### Venta local
`consumir_stock_fifo()` — `views.py:21143`. Consume lotes por `fecha_ingreso`, calcula `costo_fifo`, registra **`VENTA_PUBLICO`** (egreso), guarda `lotes_utilizados`.

### Traspaso
2 movimientos: **`TRASPASO_SALIDA`** (al aprobar, resta origen) + **`TRASPASO_ENTRADA`** (al recibir, suma destino).

### Trazabilidad por SKU — existe y es completa ✅
`api_trazabilidad_producto` — `views_modulo_existencias_nuevo.py:453` (+ helper en `views.py:31653`).
Timeline unificada: movimientos + lotes FIFO + traspasos + historial de precios + pendientes de despacho, con etiquetas legibles por concepto (`views.py:31784`).

---

## 2. Tabla de conceptos de movimiento (canónico vs uso)

| Clave canónica (choice en `models/ventas.py:55+`) | Significado | Quién lo escribe |
|----------------------------------------------------|-------------|------------------|
| `INGRESO_INICIAL` | Saldo inicial / carga | hub central, `views.py:6641` |
| `INGRESO_MANUAL` | Alta manual de catálogo | `crear_producto_manual` `views.py:20474` |
| `RECEPCION_COMPRA` | Recepción de compra | `recepcionar_compra` |
| `VENTA_PUBLICO` / `VENTA_MAYORISTA` | Egreso por venta | `consumir_stock_fifo` |
| `TRASPASO_SALIDA` / `TRASPASO_ENTRADA` | Traspaso | flujo de traspasos |
| `AJUSTE_POSITIVO` / `AJUSTE_NEGATIVO` | Ajuste de inventario | módulo ajustes |

**Reportes que filtran por estas claves** (deben coincidir EXACTO): `CONCEPTOS_ENTRADA = ['RECEPCION_COMPRA','INGRESO_INICIAL']` (`views_modulo_reportes.py:5578`), dashboard productos (`views.py:23749`), movimientos-sucursal (`views_modulo_reportes.py:4167`), API precios (`views.py:13935`).

---

## 3. Inventario de Dashboards (consistencia con modelos)

| Dashboard | URL | View (archivo:línea) | Fuente de datos | Estado |
|-----------|-----|----------------------|-----------------|--------|
| Home | `/app/home/`, `/app/dashboard/` | `views_dashboard_home.dashboard_home:150` | `Ticket`(PAGADO+sucursal), `Producto_Talla`, `Compras`, `Dte`, `Requerimiento`, `Traspaso` | ✅ filtra estado+sucursal correctamente |
| POS | `/app/pos-dashboard/` | `pos_dashboard` (views_modulo_ventas) + API `dashboard_stats` | `Ticket`/`Ticket_Productos` por sucursal+día | ⚠️ revisar que conteos del día excluyan `modulo_origen='CAMBIO_DEVOLUCION'` |
| Ventas | `/app/ventas/dashboard/` | `dashboard_ventas` (views_modulo_ventas) | `Ticket` con filtros vendedor/método/estado | ✅ default `PAGADO` |
| Compras estratégico | `/app/dashboard_compras_estrategico/` | `dashboard_compras_estrategico` (views_modulo_compras) | `Compras`, `Compras_Producto*`, `Productos_Recepcionados` | ⚠️ usa `precioSugerido` (no precio real de venta) para ROI |
| Productos (mejorado) | `/app/dashboard_productos_mejorado/` | `dashboard_productos_mejorado_api` `views.py:23647` | `Producto_Talla`, `LoteProducto`, `Ticket_Productos`, `Movimientos_Producto` | 🟠 **H2**: filtra movimientos por `created_at`, no `fecha` |
| FIFO | `/app/dashboard_fifo/` | `dashboard_fifo` | `LoteProducto` | ⚠️ no reconcilia con `Producto_Talla.stock` (H6) |
| Documentos (DTE) | `/app/dashboard-documentos/` | `views_dashboards_kpi.dashboard_documentos:67` | `Dte`, `Dte_Incidencia` | ⚠️ confirmar que filtros no crucen empresas |
| Requerimientos | `/app/dashboard-requerimientos/` | `views_dashboards_kpi:305` | `Requerimiento` | ✅ |
| Despachos/Recepciones | `/app/dashboard-despachos/` | `views_dashboards_kpi:417` | `Dte`(TRASPASO), `Productos_Recepcionados` | ✅ |
| Predicción compras | `/app/prediccion/` | `views_prediccion_compras:78` | tablas de predicción batch | ✅ |
| Ecommerce asignación | `/app/ecommerce/dashboard-asignacion/` | `views_ecommerce:2249` | `PedidoEcommerce`, `MetricaAsignacionPedido` | ✅ |

---

## 4. Hallazgos

### 🔴 H1 — `crear_producto()` escribe conceptos en formato incompatible con los reportes
- **Dónde:** `views.py:17683` → `concepto='Ingreso Inicial'` / `'Recepción Compra'` (texto con espacios/acentos).
- **Problema:** Reportes y dashboards filtran por las CLAVES `INGRESO_INICIAL`/`RECEPCION_COMPRA`. Los movimientos así escritos quedan **invisibles** en kardex/entradas y rompen el saldo acumulado.
- **Severidad:** latente. `crear_producto()` solo la llaman funciones **no enrutadas** (`views_modulo_existencias.py:284,336`, `views_modulo_productos.py:286,334`). No está vivo, pero es una trampa para quien reutilice la función.
- **Fix:** usar las claves del choice (`'INGRESO_INICIAL'`/`'RECEPCION_COMPRA'`), o eliminar la función + sus llamadores muertos.

### 🟠 H2 — Dashboard de productos mezcla `created_at` vs `fecha`
- **Dónde:** `dashboard_productos_mejorado_api` — `views.py:23746`, `23765`, `23809`.
- **Problema:** filtra `Movimientos_Producto` por `created_at` (inserción real). El resto de reportes usa `fecha` (fecha del movimiento, retroactiva en cargas históricas). → En recepciones históricas, este dashboard **no cuenta** entradas que sí salen en los demás reportes. KPIs distintos entre pantallas para el mismo período.
- **Fix:** cambiar `created_at__gte` → `fecha__gte` en el agregado de movimientos (línea 23745-23748) para alinear con `CONCEPTOS_ENTRADA`/kardex. Evaluar si stock-muerto (23765) y flujo-mensual (23809) deben seguir el mismo criterio.

### 🟠 H3 — Existencias no exponen origen ni fecha de ingreso (GAP de reporte)
- **Dónde:** `existencias-marca`, `existencias-sucursal`, `resumen-existencias`.
- **Problema:** muestran stock instantáneo sin `fecha_creacion`, sin tipo de ingreso (manual/recepción/traspaso), sin fecha de último ingreso. El dato existe en modelos pero no se agrega aquí. Para antigüedad/origen hay que ir SKU por SKU.

### 🟡 H4 — No hay reporte agregado "productos creados"
No existe vista que liste altas de catálogo del período ni que separe manual-vs-recepción de forma agregada. Derivable del primer movimiento de cada SKU.

### ✅ H5 — Falsos positivos descartados
- `dashboard_home` **sí** filtra `estado='PAGADO'` + sucursal en ventas (`views_dashboard_home.py:269-298`) y por sucursal en stock (`:349`). El reporte de "no filtra" era erróneo.
- `VENTA_PUBLICO` **sí** es un choice válido (`models/ventas.py:68`, `dte.py:107`). El reporte de "no existe" era erróneo.
- Único matiz real: la rotación de inventario usa stock actual como denominador (el código lo marca "simplificada", `views_dashboard_home.py:390`) — aproximación conocida, no bug.

### ℹ️ H6 — Sin reconciliación stock plano vs lotes FIFO
No hay verificación de que `Producto_Talla.stock == Σ LoteProducto.cantidad_disponible`. Recomendable un command de solo-lectura para detectar drift.

---

## 5. Resumen ejecutivo

- **La trazabilidad de producto está bien diseñada** y es completa a nivel SKU (entrada → movimientos/lotes → venta FIFO). Las dos vías de alta (manual y recepción) dejan kardex y lote.
- **El bug real de "lo que se registra vs lo que muestran los reportes" es H1**, pero está contenido en código muerto. Conviene cerrarlo para evitar reincidencia.
- **La inconsistencia entre dashboards más relevante es H2** (`created_at` vs `fecha`): afecta cifras visibles cuando hay cargas históricas.
- **Los GAPs (H3, H4)** no son bugs de datos: son reportes que no exponen información que el modelo ya tiene.
- Varios "bugs" reportados por exploración automática (H5) **no eran reales** y se descartaron tras leer el código.
