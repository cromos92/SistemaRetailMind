# Módulo de Reportes — Auditoría completa y plan de trabajo (2026-07-25)

> 34 reportes revisados uno por uno contra el código y contra **datos reales de
> producción** (solo lectura). El anexo con la ficha de cada reporte está en
> [ANEXO_REPORTES_2026-07-25.md](ANEXO_REPORTES_2026-07-25.md).
>
> Continúa la auditoría del 22-jul (`AUDITORIA_MODULO_REPORTES_2026-07.md`), cuyos
> 12 fixes ya están commiteados. Esto es lo que quedaba y lo que apareció nuevo.

---

## 1. El titular

Dos reportes estaban entregando cifras de plata **falsas**, no imprecisas:

| Reporte | Lo que mostraba | Lo que era realmente | Medido en prod |
|---|---|---|---|
| **Ventas Global por Empresa** | "Ventas netas" | Ventas **brutas**: las notas de crédito nunca se restaban | **$7,4M sin restar** solo en junio-2026 |
| **Compras Integral** | "ROI Estimado 19%" y "Margen Bruto Esperado $131M" | El **IVA** de las facturas de compra | El ROI daba 19,0% = la tasa de IVA |

Ambos ya están corregidos y verificados (§3).

**Estado general:** de 34 reportes, **9 son útiles**, **15 mejorables**, **5 están
rotos** y **5 son redundantes** (duplican a otro y conviene eliminarlos).

---

## 2. Inventario con veredicto

### Rotos — entregan datos falsos o fallan

| Reporte | Qué le pasa |
|---|---|
| Ventas Global por Empresa | NC nunca restadas → netas = brutas · **CORREGIDO** |
| Compras Integral | ROI/margen = IVA; filtros muertos; sin scoping de empresa; 44 min · **PARCIAL** |
| Documentos por Vendedor | Doble conteo de ventas POS con boleta; el detalle no cuadra con la fila que explica |
| Existencias por Marca | Fuga entre empresas + totales truncados sin avisar · **CORREGIDO** |
| Diagnóstico de datos de compras | Consultaba campos inexistentes → error siempre · **CORREGIDO** |

### Redundantes — candidatos a eliminar

| Reporte | Duplica a | Acción |
|---|---|---|
| Dashboard de Compras Estratégico | Dashboard de Compras Mejorado (es un subconjunto) | Eliminar; ya está fuera de la navegación |
| Despachos Detallado | Dashboard de Despachos | Eliminar (endpoint huérfano) |
| Recepciones Detallado | Dashboard de Despachos | Eliminar (endpoint huérfano) |
| Existencias por Sucursal | Reporte de Existencias con filtro de sucursal | Fusionar |
| Ruta duplicada `dashboard_ventas/mejorado` | Mismo template | Eliminar ruta |

### Útiles (mantener y potenciar)

Productos Vendidos · Ventas por Categoría/Especialidad · Reporte de Existencias ·
Inicial vs Restante · Plan de Liquidación v2 · Dashboard de Despachos (Flujo y
Recepción) · Trazabilidad de DTE · Diagnóstico Cuadratura vs Ventas.

---

## 3. Ejecutado y verificado hoy

Los cuatro cambios se probaron invocando las vistas reales contra la base de
producción y contrastando con un oráculo independiente.

### 3.1 Ventas Global: las NC ahora se restan
`views_modulo_reportes.py` — el filtro exigía `tipo_transaccion` de venta **y**
`tipo_documento='NOTA DE CREDITO'` a la vez, pero las NC de venta se emiten con
`DEVOLUCION` o `ANULACION`: la intersección era vacía. Ahora acepta las cuatro y
excluye las NC de compra.

```
ventas brutas   = $177.922.231
devoluciones    = $7.383.449   <-- antes $0
ventas netas    = $170.538.782   (cuadra con el oráculo)
```

### 3.2 Compras Integral: markup teórico en vez del IVA
El trío ROI / Margen / Valor de Venta se calculaba como `monto_con_iva − monto_neto`
de la factura de compra. Ahora se calcula contra el **precio sugerido de la orden
de compra** (`Compras_Producto.precioSugerido` vs `costo`), se rotula como
**teórico** en la UI y los "insights" automáticos ya no opinan sobre el IVA.

```
costo órdenes 2026   = $135.615.397
valor a precio lista = $302.364.384
markup teórico       = 123,0%   (antes: 19,0% = IVA)
```
Se agregó `iva_soportado` y `costo_ordenes_compra` al payload para que la pantalla
compare bases homogéneas.

### 3.3 Existencias por Marca: scoping y totales honestos
- El `sucursal_id` recibido ya no se usa a ciegas: se intersecta con las empresas
  del usuario y devuelve **403** si no corresponde.
- El límite se aplicaba sobre filas `Producto` (una por sucursal), así que con N
  sucursales se mostraban ~límite/N artículos **con las columnas recortadas**. Ahora
  se eligen primero los artículos y luego se traen todas sus filas; la respuesta
  informa `truncado`, `articulos_mostrados` y `articulos_disponibles`.

### 3.4 Diagnóstico de compras: revivido
Consultaba `fecha_compra` y `total`, que no existen en el modelo. Ahora usa
`fecha`, excluye `ELIMINADA` y cambia el chequeo inútil por uno que importa:
órdenes sin líneas. Responde 200 con datos reales (117 órdenes, calidad 100%) y de
paso destapó un hallazgo: **1.077 DTE de compra sin productos asociados**.

---

## 4. Plan por prioridad (lo que queda)

### P0 — Mienten con plata (hacer primero)

1. **Compras Integral, el resto del saneamiento**: los filtros *Período* y
   *Temporada* de la pantalla no hacen nada; no hay scoping por empresa (mezcla
   todo el holding); las NC de compra, los DTE anulados y los descartados **suman
   en vez de restar**; la deuda pierde las facturas con pago parcial y no cuenta
   el estado VENCIDO; "cumplimiento del proveedor" mide **pagos**, no entregas; las
   unidades recibidas de recepciones parciales están inventadas.
2. **Documentos por Vendedor**: doble conteo de ventas POS que generaron boleta y
   filtros distintos a los de la fila que explica → el drill-down nunca cuadra.
3. **Productos Vendidos**: el monto mezcla facturas en **neto** con boletas y
   tickets en **bruto**; sell-through y cobertura ignoran el filtro de sucursal.
4. **Cuadratura vs Ventas** y **pestaña Rendimiento**: filtran tickets por
   `Ticket.fecha`, que es `auto_now` y se reescribe en cada guardado → hay que usar
   `created_at`.

### P1 — Fugas entre empresas y permisos

Seis endpoints resuelven la empresa desde la sesión sin validarla, o no validan el
`sucursal_id` recibido: kardex/tarjeta de movimiento, ventas por
categoría/especialidad, despachos (flujo y recepción), recepciones detallado,
predicción de compras y compras integral. Además, los endpoints JSON de reportes no
pasan por el middleware de permisos (el mapa solo cubre las páginas), así que un rol
sin acceso puede pedir el JSON directo.

### P2 — Reportes inservibles por lentitud

| Reporte | Medido | Causa |
|---|---|---|
| FIFO general | ~2.968 queries / 9,9 min → **la BD corta la conexión** | N+1 por lote |
| Compras Integral | ~9.000 queries / **44 min** | el ranking de proveedores se recalcula 3× y los datos de OC 4× |
| Rendimiento por Proveedor | 465 queries / 91 s | N+1 por proveedor con `IN` de decenas de miles de ids |
| Despachos Detallado | N+1 sin tope de filas | 2 queries por documento |

> El "Rendimiento por Proveedor" tiene además un defecto de fondo: atribuye las
> ventas por **coincidencia de texto** entre el nombre de la orden y el código de
> artículo, lo que produce ceros falsos y totales no sumables entre proveedores.

### P3 — Limpieza

Eliminar los 5 redundantes de §2, borrar las funciones FIFO muertas y unificar el
parseo de fechas (hoy duplicado en varias vistas).

### P4 — KPIs que faltan (los de mayor retorno)

| Reporte | KPI | Por qué |
|---|---|---|
| Ventas Sucursal/Vendedor | **Margen bruto $ y %** por sucursal y vendedor | Hoy el ranking premia al que más factura, no al que más gana |
| Ventas Sucursal/Vendedor | **Tasa de devolución** (NC/ventas) separando DEVOLUCION de ANULACION | Los datos ya se calculan, falta el ratio; anulaciones altas huelen a error o fraude |
| Productos Vendidos | Sell-through y cobertura **respetando el filtro de sucursal** | Ya existen, pero mienten al filtrar |
| Existencias | **% de stock >180 días** y **cobertura en días** por marca y categoría | Miden quiebre pero no exceso, que es el problema real (70% del stock >180d) |
| Despachos | **% de lo despachado vendido a 30 días** | Mide si el despacho sirvió, no solo si llegó |
| Plan de Liquidación | Margen y GMROI a **precio y costo reales**, no de lista | Hoy decide liquidaciones con precios que nadie pagó |
| Compras | **Markup real** (venta efectiva vs costo) además del teórico | Cierra el ciclo compra → venta |

---

## 5. Cómo verificar

La suite de regresión sigue siendo la herramienta:

```powershell
cd retailmind
python _test_reportes_readonly.py --confirmo-prod --rapido
python _test_reportes_readonly.py --confirmo-prod --solo ventas_global,existencias_marca
```

⚠️ La corrida completa se cuelga en los reportes del P2 (FIFO general y compras
integral): eso **es** el síntoma, no un problema de la suite.

---

## 6. Archivos tocados hoy (sin commit)

- `retailmind/app/views_modulo_reportes.py` — fixes 3.1, 3.2, 3.3
- `retailmind/app/views_modulo_compras.py` — fix 3.4
- `retailmind/app/templates/vistas/modulo_reportes/reporte_compras.html` — rótulos honestos
