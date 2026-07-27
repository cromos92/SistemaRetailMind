# Dashboards — Auditoría completa y plan (2026-07-25)

> 17 dashboards revisados uno por uno contra el código y contra datos reales de
> producción. Ficha de cada uno en
> [ANEXO_DASHBOARDS_2026-07-25.md](ANEXO_DASHBOARDS_2026-07-25.md).
>
> Complementa la auditoría de reportes del mismo día
> ([PLAN_REPORTES_2026-07-25.md](PLAN_REPORTES_2026-07-25.md)).

---

## 1. Resumen

**17 dashboards: 3 útiles, 8 mejorables, 2 rotos, 4 redundantes.** 37 problemas de
severidad alta.

El patrón que se repite: **cada pantalla mide "ventas" a su manera**. El home solo
cuenta tickets; el dashboard de ventas usa `Ticket.fecha` (que es `auto_now` y se
reescribe en cada guardado); el reporte de ventas suma tickets + DTE y resta notas
de crédito. Tres pantallas, tres respuestas distintas a "¿cuánto vendimos?".

### Rotos

| Dashboard | Qué le pasa |
|---|---|
| **Existencias / Productos** | Recibe el filtro de sucursal y **no lo aplica**: muestra KPIs globales contra costos de una sucursal. Sin scoping de empresa (fuga entre empresas del holding). El botón *Exportar* descarga el catálogo completo de **todas** las empresas, sin filtros. |
| **Caja / Arqueos** | **No existe**: hay código que lo anuncia en el menú pero no hay dashboard detrás. |

### Redundantes (eliminar o redirigir)

| Dashboard | Estado |
|---|---|
| Compras Estratégico | **Retirado hoy** — subconjunto estricto del Mejorado; su botón en el home devolvía **JSON crudo** |
| Ruta `/app/ventas/dashboard/` | **Redirigida hoy** al mejorado (renderizaba el mismo template sin el contexto de filtros) |
| Home legacy "Dashboard General" | Sustituido por el Centro de Control |
| Dashboard Productos (legacy) | Sustituido por el mejorado |

---

## 2. Corregido hoy

### 2.1 Dashboard Home
- **Los tickets de cambio/devolución se contaban como venta.** Son la diferencia
  cobrada en un cambio, no venta nueva; el POS y el reporte de ventas ya los
  excluían, el home no. Medido en junio-2026: **$551.300 de más en 154 tickets**, y
  el ticket promedio subestimado en $1.287 ($40.053 → $41.340 real).
- **Dos alertas llevaban a 404**: "Ver quiebres" apuntaba a `/app/resumen-existencias/`
  (la ruta real es `/app/reportes/resumen-existencias/`) y "Ver cambios" a
  `/app/cambios-devoluciones/` (real: `/app/ventas/cambios-devoluciones/`). Eran
  justo las dos alertas más accionables del tablero.
- **El botón "Dashboard Compras" mostraba JSON crudo**: apuntaba a un endpoint que
  devuelve `JsonResponse`. Ahora va al Dashboard de Compras Mejorado.

### 2.2 Rutas duplicadas
`/app/ventas/dashboard/` y `/app/dashboard_compras_estrategico/` quedaron como
redirecciones a la versión viva, para no romper enlaces guardados.

---

## 3. Plan por prioridad

### P0 — Números que no son lo que dicen

1. **Dashboard de Ventas NEXO** (9 problemas altos, el peor del lote):
   - La mitad de sus endpoints filtra por **`Ticket.fecha`**, que es `auto_now`:
     las ventas migran de día solas. Debe usar `created_at`.
   - **"Unidades Vendidas" y "UPT" salen del top-20 de productos**, no del total.
   - **Margen Bruto y GMROI** mezclan ingreso **con IVA** contra costo **neto**,
     ignoran descuentos y se van a 100% cuando falta el costo FIFO.
   - Los filtros de **Categoría y Especialidad no afectan a ningún KPI principal**.
   - Los atajos de fecha usan `toISOString()`: en Chile, después de las 20:00,
     "Hoy" se corre al día siguiente.
   - El Excel exportado no coincide con lo que se ve en pantalla.
2. **Dashboard Documentos/DTE**: "Monto Facturado" suma **compras + ventas +
   traspasos + notas de crédito en positivo**; "% Aceptados SII" no mide aceptación
   del SII; "Deuda Pendiente" mezcla cuentas por pagar con cuentas por cobrar.
3. **Dashboard de Compras Mejorado**: repite el error del reporte — **"ROI Promedio"
   y "Margen Esperado" están inflados por el IVA**; además dos paneles completos del
   bloque CD están **muertos por un `FieldError` silenciado**, y los gráficos no
   comparten el filtro de estado con las tarjetas (incluyen compras ELIMINADAS).
4. **Dashboard de Predicción**: la pestaña "Por Proveedor" **ignora el año** y suma
   todas las temporadas de todos los años; el proveedor del plan se elige ordenando
   por un campo `auto_now`.

### P1 — Fugas entre empresas

Seis dashboards resuelven empresa/sucursal desde `request.session` sin validar
contra `EmpresaUser`, o reciben `sucursal_id` y no lo comprueban: Existencias,
FIFO, Despachos (`/flujo/` no aplica ningún filtro), Documentos/DTE,
Requerimientos y los endpoints del Dashboard de Ventas.

### P2 — Rendimiento

- El **home** hace 40-50 consultas por carga y es la pantalla de entrada de todos;
  varias sobre `Producto_Talla` (100k+ filas) sin caché. El botón "Actualizar" hace
  `location.reload()`, o sea recalcula todo.
- **Existencias**: dos consultas materializan el catálogo entero en memoria.
- **Ventas**: `tendencias` itera todos los tickets del período en Python.
- **Predicción**: N+1 sin límite en el endpoint de portada.

### P3 — Consolidación

Eliminar definitivamente el código muerto del Compras Estratégico (≈440 líneas
duplicadas entre `views.py` y `views_modulo_compras.py`), el Home legacy y el
Dashboard Productos legacy. Decidir si el Dashboard FIFO se reorienta a **"Salud de
Inventario"**: su KPI principal ("Diferencia FIFO vs Sistema") ya no mide descalce
de stock sino *drift de costeo*, porque las diferencias quedaron en ~0 tras la
reconciliación.

### P4 — Lo que falta medir

| Dashboard | KPI faltante | Por qué |
|---|---|---|
| Home | **Venta neta** (con NC descontadas) y comparable con el reporte | Hoy el dueño ve dos cifras distintas según la pantalla |
| Home | Cobertura y % stock >180d **excluyendo centros de distribución** | Hoy se contradice con el indicador de compra |
| Ventas | Margen real por categoría y especialidad | Los filtros existen pero no mueven ningún KPI |
| Compras | Markup **real** (venta efectiva vs costo), no solo teórico | Cierra el ciclo compra → venta |
| Despachos | % de lo despachado vendido a 30 días | Mide si el despacho sirvió |
| Documentos | Separar por cobrar de por pagar | Hoy se suman en un solo número |

---

## 4. Archivos tocados hoy

- `retailmind/app/views_dashboard_home.py` — cambios como venta + 2 enlaces 404
- `retailmind/app/templates/vistas/dashboard_home.html` — botón a JSON crudo
- `retailmind/app/urls.py` — rutas duplicadas convertidas en redirecciones
