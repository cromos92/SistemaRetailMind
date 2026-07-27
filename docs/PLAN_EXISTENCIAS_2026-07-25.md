# Módulo de Existencias — Auditoría y plan (2026-07-25)

> 5 frentes: trazabilidad de punta a punta, pantallas principales, movimientos y
> traspasos, inventarios/etiquetas/fusión, menú y estilos.
>
> **Todo lo de §1 está MEDIDO contra la base de datos de producción**, no estimado.

---

## 1. 🔴 Integridad de datos — lo más grave de toda la auditoría del ERP

### 1.1 Cada despacho a tienda crea stock SIN costo FIFO (bug activo)

Desde el **17-abr-2026** hay **5.911 movimientos `TRASPASO_ENTRADA` por 25.060
unidades** y **ninguno creó lote FIFO**. La recepción de un traspaso escribe el
stock con `bulk_create` + `Case/When`, saltándose `crear_lote_producto`. El lado
del origen **sí** consume lotes, así que el sistema pierde el costo en cada
despacho.

Descalce medido, stock vs lotes:

| Sucursal | Stock | En lotes | Sin costo FIFO |
|---|---|---|---|
| NICK2 | 33.141 | 28.663 | **4.478** |
| PAO2 | — | — | 998 |
| PAO4 | — | — | 744 |
| NICK1 | — | — | 636 |

**~6.943 unidades vendibles sin costeo.** Los centros de distribución
(EDEL/IMP/PA00) cuadran perfecto, porque ahí el ingreso sí crea lote.

> Esto explica por qué el margen real es inauditable en tiendas: se vende
> mercadería cuyo costo el sistema no conoce.

### 1.2 El kardex no cuadra con el stock

**126.455 de 605.362 SKUs (21%)** tienen `SUM(movimientos) ≠ stock`. Global:
stock 167.981 vs kardex 83.013 (delta **84.968 unidades**). Aislando solo
productos nacidos en 2026 —sin deuda legacy— el kardex **sobra** 6.499 u en EDEL.

Caso testigo verificado: SKU 4829352 (art. 406745201, talla M, EDEL) tiene 32
ingresos por 730 u, un solo egreso de 6 u… y stock 0. **724 unidades salieron sin
dejar rastro.**

### 1.3 Ventas que no descontaron stock

**151 tickets PAGADOS de 2026, por $5.657.487**, no tienen ningún movimiento de
stock asociado (PAO1: 49 tickets/$2,19M · NICK2: 32/$1,52M). En el otro sentido:
**3.787 movimientos `VENTA_PUBLICO` (−4.161 u) sin ticket ni DTE** que los
respalde.

### 1.4 Mercadería despachada que nadie recibió

**14 DTE de traspaso con salida y sin ninguna entrada (995 u)** + 11 parciales
(57 u) = **1.052 unidades en limbo**. Ninguna pantalla del módulo las expone.

### 1.5 El eslabón compra ↔ kardex nunca funcionó

`Productos_Recepcionados.movimiento_ingreso_id` está **NULL en las 9.992 filas**.
El `.update(movimiento_ingreso=...)` de `views.py:20026` jamás matcheó en
producción: no se puede ir de una factura de compra al movimiento que generó.

### 1.6 El SKU no identifica de forma única

**152.593 SKUs están repetidos**, ocupando 421.106 de las 605.362 filas de
`Producto_Talla` (el SKU se clona a la sucursal destino al recepcionar un
traspaso). El SKU 123456789 vive en 8 bodegas con 32.577 unidades. Las pantallas
de trazabilidad buscan por `sku=` y **eligen una fila arbitraria**.

---

## 2. Plan de remediación (en este orden)

1. **Detener la hemorragia**: hacer que la recepción de traspaso cree el lote
   FIFO. Es un cambio en el flujo de escritura de stock — hay que hacerlo con
   pruebas, no a ciegas. Diseño: reemplazar el `bulk_create` por el mismo camino
   que ya usa la creación de productos (`registrar_movimiento_producto` →
   `crear_lote_producto`), o crear los lotes en un segundo paso dentro de la
   misma transacción, tomando el costo del lote consumido en el origen.
2. **Backfill de lotes faltantes** para las ~6.943 unidades ya ingresadas sin
   costo, con el costo del movimiento de origen. Comando idempotente, con
   `--dry-run` primero.
3. **Investigar los 151 tickets sin movimiento** ($5,6M): ¿fallaron a mitad del
   cobro? Se conecta con el problema de atomicidad del módulo de ventas
   (ver [PLAN_VENTAS_2026-07-25.md](PLAN_VENTAS_2026-07-25.md) §3.1) — es
   probablemente la **misma causa raíz**.
4. **Exponer las 1.052 unidades en limbo** en la pantalla de despachos.
5. **Reparar el enlace compra↔kardex** (`movimiento_ingreso_id`).
6. **Dejar de clonar el SKU** entre bodegas o, si el negocio lo necesita así,
   hacer que toda búsqueda por SKU pida bodega y avise cuando hay múltiples.

---

## 3. Pantallas

### Corregido hoy
**Trazabilidad de Producto**: la timeline se ordenaba con `sort()` sobre strings
`dd/mm/YYYY`, o sea **por día del mes**: 30/01/2024 aparecía antes que
05/12/2026, y el corte a 100 eventos descartaba hitos arbitrarios. Ahora ordena
por fecha real.

### Pendiente
- **Trazabilidad de Producto** es la pantalla más engañosa del módulo: su tabla
  de movimientos **no tiene columna de saldo acumulado** (justo lo que se
  necesita para verificar el kardex), corta a 200 movimientos sin avisar, y su
  pestaña "Traspasos" lee `Traspaso_Detalle`, que tiene **0 filas en
  producción**: siempre sale vacía y hace creer que el producto nunca se movió.
  Usa `alert()` nativo en vez de SweetAlert2 y no aplica el design system.
- **Tarjeta de Movimiento** es la mejor construida (es la única que aplica la
  regla correcta de bodega dueña del SKU), pero: al poner "Desde" el saldo
  arranca en 0 y no cuadra; al filtrar en el front la columna Saldo queda
  desfasada; y el CSV exporta todo ignorando los filtros aplicados.

---

## 4. Lo que sí está bien

- El `save()` del modelo deriva el tipo del signo y **la base está limpia en
  eso**: 0 INGRESO con cantidad negativa, 0 EGRESO con positiva.
- `api_tarjeta_movimiento` calcula el saldo por serie (bodega·talla) usando la
  bodega dueña del SKU — es el único punto del sistema que aplica la regla
  correcta, en vez del `COALESCE(destino, origen)`.
- Tras los arreglos de esta sesión, sus endpoints ya validan la empresa del SKU
  consultado (antes se podía leer el kardex de otra empresa).
