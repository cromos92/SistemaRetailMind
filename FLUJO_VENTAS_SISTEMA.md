# 📊 Flujo Completo de Ventas y Emisión de DTEs - Sistema RetailMind

## 🎯 Modelos Afectados en una Venta

### 1️⃣ **CREAR TICKET** (Estado: PENDIENTE)

**Tablas afectadas:**
- ✅ `Ticket` - Se crea el registro principal
- ✅ `Ticket_Productos` - Se agregan los productos
- ✅ `Correlativo` - Se consume número correlativo

**Campos importantes:**
```python
Ticket:
  - correlativo (único por sucursal)
  - vendedor
  - sucursal
  - cliente_* (todos los datos del cliente)
  - subtotal, descuento, total
  - estado = 'PENDIENTE'
  - metodo_pago (tentativo)
  
Ticket_Productos:
  - ProductoTalla (relación)
  - stock (cantidad vendida)
  - precio (precio unitario)
  - subtotal
```

**NO se afecta en este punto:**
- ❌ Stock de productos (solo reserva lógica)
- ❌ Movimientos_Producto
- ❌ LoteProducto
- ❌ DTE

---

### 2️⃣ **REGISTRAR PAGOS** (Cambio de PENDIENTE → PAGADO)

**Tablas afectadas:**
- ✅ `Ticket` - Se actualiza estado a PAGADO
- ✅ `TicketDetallePago` - Se registran los pagos
- ✅ `Movimientos_Producto` - **NUEVO: Se crean movimientos de EGRESO**
- ✅ `LoteProducto` - **Se consumen lotes FIFO**
- ✅ `Producto_Talla` - **Stock se reduce**
- ✅ `Cliente` (empresa_management) - Se guarda/actualiza

**Proceso automático:**
```python
1. Se marcan los pagos
2. Ticket.estado = 'PAGADO'
3. Para cada producto del ticket:
   a. Se consume stock FIFO:
      - Busca lotes más antiguos
      - Consume stock por orden
      - Actualiza LoteProducto.cantidad_disponible
      - Crea Movimientos_Producto con:
        * tipo_movimiento = 'EGRESO'
        * concepto = 'VENTA_TICKET' 
        * cantidad = -X (negativo)
        * ticket = referencia
   b. Se actualiza Producto_Talla.stock -= cantidad
4. Si tipo_documento es BOLETA/FACTURA ELECTRONICA:
   → Se genera DTE automáticamente
```

---

### 3️⃣ **GENERAR DTE** (Boleta/Factura Electrónica)

**Se ejecuta solo si:**
- ✅ tipo_documento in ['BOLETA_ELECTRONICA', 'FACTURA_ELECTRONICA']
- ✅ ticket.estado == 'PAGADO'

**Tablas afectadas:**
- ✅ `Dte` - Se crea el documento electrónico
- ✅ `Dte_Productos` - Se copian productos
- ✅ `Dte_Detalle_Pago` - Se copian métodos de pago
- ✅ `Empresa` (receptor) - Se crea/busca el cliente como empresa
- ✅ `Correlativo` - Se consume número de DTE

**Datos del DTE:**
```python
Dte:
  - numero_documento (correlativo único)
  - tipo_documento (BOLETA ELECTRONICA / FACTURA ELECTRONICA)
  - tipo_transaccion = 'VENTA_PUBLICO'
  - emisor = sucursal.empresa
  - receptor = cliente (como Empresa)
  - vendedor
  - montos (neto, iva, total)
  - estado_dte = 'EMITIDO'
  - observaciones = 'Generado desde Ticket #X'
```

---

### 4️⃣ **ANULAR TICKET/VENTA**

**Tablas afectadas:**
- ✅ `Ticket` - estado = 'ANULADO'
- ✅ `Movimientos_Producto` - Se crean movimientos de INGRESO
- ✅ `Producto_Talla` - Stock se devuelve
- ✅ `LoteProducto` - **NO se afecta** (movimiento manual)

**Proceso:**
```python
1. Ticket.estado = 'ANULADO'
2. Para cada producto:
   a. Se crea Movimientos_Producto:
      * tipo_movimiento = 'INGRESO'
      * concepto = 'ANULACION_TICKET'
      * cantidad = +X (positivo)
   b. Producto_Talla.stock += cantidad
```

---

## 📦 Modelo `Movimientos_Producto` - Estructura

**Campos principales:**
```python
- dte (ForeignKey nullable)
- ticket (ForeignKey nullable)  
- ProductoTalla (ForeignKey required)
- cantidad (Integer) → Positivo=INGRESO, Negativo=EGRESO
- costo, sobreprecio, precio
- concepto (choices: VENTA_TICKET, DEVOLUCION_CLIENTE, ANULACION_TICKET, etc.)
- tipo_movimiento (choices: INGRESO, EGRESO, AJUSTE, TRASPASO)
- estado (COMPLETADO, PENDIENTE, CANCELADO)
- responsable
- observaciones
- referencia_externa
```

**Conceptos importantes para ventas:**
- `VENTA_TICKET` - Venta normal con ticket
- `VENTA_DIRECTA` - Venta directa
- `DEVOLUCION_CLIENTE` - Cliente devuelve producto
- `ANULACION_TICKET` - Ticket anulado

---

## 🔄 Modelo `LoteProducto` - Sistema FIFO

**Estructura:**
```python
- producto_talla (ForeignKey)
- dte (origen del lote)
- cantidad_inicial
- cantidad_disponible  ← Se reduce en ventas
- costo_unitario
- fecha_ingreso  ← Ordenamiento FIFO
- activo
- agotado (se marca True cuando cantidad_disponible = 0)
```

**Función `consumir_stock_fifo`:**
```python
1. Busca lotes con stock disponible
2. Ordena por fecha_ingreso (más antiguo primero)
3. Para cada lote:
   - Calcula cuánto consumir
   - LoteProducto.cantidad_disponible -= cantidad
   - Registra lote utilizado
   - Calcula costo consumido
4. Crea Movimientos_Producto de EGRESO
5. Actualiza Producto_Talla.stock
6. Retorna lotes utilizados y costo total
```

---

## ✅ Verificación de Integridad

### Al finalizar una venta, deberías ver:

**1. En tabla `Ticket`:**
```sql
SELECT correlativo, estado, total, cliente_nombre 
FROM app_ticket 
WHERE id = X;
```
✅ estado = 'PAGADO'

**2. En tabla `Ticket_Productos`:**
```sql
SELECT tp.*, pt.sku, p.articulo 
FROM app_ticket_productos tp
JOIN app_producto_talla pt ON tp.producttalla_id = pt.id
JOIN app_producto p ON pt.producto_id = p.id
WHERE tp.idticket_id = X;
```
✅ Productos del ticket

**3. En tabla `TicketDetallePago`:**
```sql
SELECT metodo_pago, monto, voucher 
FROM app_ticketdetallepago 
WHERE ticket_id = X;
```
✅ Métodos de pago registrados

**4. En tabla `Movimientos_Producto`:**
```sql
SELECT tipo_movimiento, concepto, cantidad, observaciones
FROM app_movimientos_producto
WHERE ticket_id = X;
```
✅ Movimientos de EGRESO (cantidad negativa)

**5. En tabla `LoteProducto`:**
```sql
SELECT cantidad_inicial, cantidad_disponible, fecha_ingreso
FROM app_loteproducto
WHERE producto_talla_id = Y
ORDER BY fecha_ingreso;
```
✅ Cantidad disponible reducida

**6. En tabla `Producto_Talla`:**
```sql
SELECT sku, stock 
FROM app_producto_talla 
WHERE id = Y;
```
✅ Stock reducido

**7. Si es DTE - En tabla `Dte`:**
```sql
SELECT numero_documento, tipo_documento, estado_dte, monto_total
FROM app_dte
WHERE observaciones LIKE '%Ticket #X%';
```
✅ DTE generado y emitido

---

## 🐛 Debugging - Comandos SQL

### Verificar si una venta afectó el stock:

```sql
-- 1. Ver el ticket
SELECT * FROM app_ticket WHERE correlativo = 67;

-- 2. Ver productos del ticket
SELECT tp.*, pt.sku, pt.stock as stock_actual
FROM app_ticket_productos tp
JOIN app_producto_talla pt ON tp.producttalla_id = pt.id
WHERE tp.idticket_id = (SELECT id FROM app_ticket WHERE correlativo = 67);

-- 3. Ver movimientos creados
SELECT tipo_movimiento, concepto, cantidad, fecha, hora, observaciones
FROM app_movimientos_producto
WHERE ticket_id = (SELECT id FROM app_ticket WHERE correlativo = 67)
ORDER BY created_at DESC;

-- 4. Ver lotes afectados
SELECT l.*, pt.sku
FROM app_loteproducto l
JOIN app_producto_talla pt ON l.producto_talla_id = pt.id
WHERE pt.id IN (
    SELECT producttalla_id FROM app_ticket_productos 
    WHERE idticket_id = (SELECT id FROM app_ticket WHERE correlativo = 67)
)
ORDER BY fecha_ingreso;

-- 5. Ver DTEs generados
SELECT tipo_documento, numero_documento, estado_dte, monto_total, observaciones
FROM app_dte
WHERE observaciones LIKE '%Ticket #67%';
```

---

## 📋 Checklist de Venta Completa

Cuando finalizas una venta, el sistema debe:

- [x] Crear `Ticket` con estado PAGADO
- [x] Crear `Ticket_Productos` (detalles)
- [x] Crear `TicketDetallePago` (pagos)
- [x] Crear `Movimientos_Producto` (EGRESO por cada producto)
- [x] Actualizar `LoteProducto` (reducir cantidad_disponible usando FIFO)
- [x] Actualizar `Producto_Talla` (reducir stock)
- [x] Consumir `Correlativo` (TICKET)
- [x] Guardar/Actualizar `Cliente` (empresa_management)
- [x] Si es DTE: Crear `Dte`, `Dte_Productos`, `Dte_Detalle_Pago`
- [x] Si es DTE: Crear/Buscar `Empresa` (receptor)
- [x] Si es DTE: Consumir `Correlativo` (BOLETA/FACTURA ELECTRONICA)

---

## 🚨 Problemas Comunes

### Problema: No se crean movimientos de stock

**Causa**: El ticket se creó pero no se pagó, o el pago se registró sin activar FIFO

**Solución**: 
- Verificar que `ticket.estado = 'PAGADO'`
- Verificar que existe la función `consumir_stock_fifo` en la transacción
- Revisar logs del servidor para errores

### Problema: No se genera DTE

**Causa**: 
- Tipo de documento no es BOLETA_ELECTRONICA o FACTURA_ELECTRONICA
- Ticket no está en estado PAGADO
- Faltan correlativos
- Error al crear empresa receptora

**Solución**:
- Verificar `tipo_documento` en el payload
- Verificar correlativos disponibles
- Revisar que cliente tenga RUT y nombre

### Problema: Stock no se reduce

**Causa**:
- No hay lotes disponibles
- Lotes están marcados como inactivos
- Error en FIFO

**Solución**:
- Verificar que existan lotes: `SELECT * FROM app_loteproducto WHERE producto_talla_id = X`
- Verificar que tengan stock: `cantidad_disponible > 0`
- Si no hay lotes, el sistema crea movimiento manual y reduce stock directo

---

## 📈 Flujo Visual

```
CREAR TICKET
    ↓
[Ticket PENDIENTE creado]
    ↓
REGISTRAR PAGOS
    ↓
[Verifica: ¿Estado cambió a PAGADO?]
    ↓ SÍ
    ├─→ Consumir Stock FIFO
    │    ├─→ Buscar lotes disponibles
    │    ├─→ Consumir de más antiguos primero
    │    ├─→ Actualizar LoteProducto.cantidad_disponible
    │    ├─→ Crear Movimientos_Producto (EGRESO)
    │    └─→ Actualizar Producto_Talla.stock
    │
    ├─→ Guardar Cliente
    │    ├─→ Buscar si existe por RUT
    │    ├─→ Si existe: actualizar datos faltantes
    │    └─→ Si no existe: crear nuevo
    │
    └─→ Generar DTE (si aplica)
         ├─→ Crear/Buscar Empresa (receptor)
         ├─→ Obtener correlativo DTE
         ├─→ Crear Dte
         ├─→ Copiar Dte_Productos
         ├─→ Copiar Dte_Detalle_Pago
         └─→ Marcar estado_dte = 'EMITIDO'
```

---

## 🎯 Resumen Ejecutivo

**Una venta completa en RetailMind afecta hasta 11 tablas:**

1. `Ticket` - Documento principal
2. `Ticket_Productos` - Detalles de productos
3. `TicketDetallePago` - Métodos de pago
4. `Movimientos_Producto` - Trazabilidad de inventario
5. `LoteProducto` - Control FIFO
6. `Producto_Talla` - Stock actualizado
7. `Correlativo` - Numeración consumida
8. `Cliente` - Base de datos de clientes
9. `Dte` - Documento tributario (opcional)
10. `Dte_Productos` - Productos del DTE (opcional)
11. `Dte_Detalle_Pago` - Pagos del DTE (opcional)
12. `Empresa` - Receptor del DTE (opcional)

**Total: 8 tablas obligatorias + 4 opcionales (si es DTE) = 12 tablas máximo**

---

## 🏢 MÓDULO DE EMISIÓN DE DTEs (Facturación)

### Ubicación: `/app/emisionDTE/`

Este módulo es para **emitir DTEs directamente** (sin pasar por tickets):
- Facturas Electrónicas a clientes
- Boletas Electrónicas
- Guías de despacho
- Traspasos entre sucursales

### ✅ **Proceso Correcto (OPTIMIZADO):**

**1. Despacho Externo (Venta a Cliente):**
```python
1. Usuario selecciona productos
2. Selecciona cliente (receptor)
3. Confirma emisión
4. Sistema:
   ✅ Crea DTE
   ✅ Crea Dte_Productos
   ✅ Consume stock FIFO (lotes + movimiento automático)
   ✅ Si FIFO falla → Consumo manual con movimiento
   ✅ Concepto: VENTA_MAYORISTA
   ✅ Estado: COMPLETADO
```

**2. Despacho Interno (Traspaso):**
```python
1. Usuario selecciona productos
2. Selecciona sucursal destino
3. Confirma emisión
4. Sistema:
   ✅ Crea DTE
   ✅ Crea Dte_Productos
   ✅ Crea movimiento PENDIENTE
   ✅ NO reduce stock (se reduce al confirmar recepción)
   ✅ Concepto: TRASPASO_SALIDA
   ✅ Estado: PENDIENTE
```

### 🔧 **Optimizaciones Aplicadas:**

1. ✅ **Integrado con FIFO**: Ahora usa `consumir_stock_fifo()`
2. ✅ **Actualiza lotes**: `LoteProducto.cantidad_disponible` se reduce
3. ✅ **Typo corregido**: "observaciones" (antes "observencias")
4. ✅ **Fallback robusto**: Si FIFO falla, usa método manual
5. ✅ **Logs mejorados**: Muestra si usó FIFO o manual

### 📊 **Movimientos Creados:**

**Por FIFO (nuevo):**
```
✓ Consumo de lotes
✓ Actualización de stock
✓ Movimiento creado automáticamente
✓ Costo calculado con FIFO
```

**Manual (fallback):**
```
✓ Stock reducido directamente
✓ Movimiento creado manualmente
✓ Costo del producto
```

---

## 🆚 Diferencias: Módulo Ventas vs Módulo DTEs

| Característica | Módulo Ventas (POS) | Módulo DTEs (Facturación) |
|----------------|---------------------|---------------------------|
| **Ubicación** | `/app/pos-dashboard/` | `/app/emisionDTE/` |
| **Propósito** | Ventas al público | Ventas mayoristas/Traspasos |
| **Documento** | Ticket → DTE (opcional) | DTE directo |
| **Cliente** | Persona/Empresa | Solo Empresas |
| **Flujo** | Ticket PENDIENTE → Pagar | DTE directo |
| **Stock** | FIFO al pagar | FIFO al emitir |
| **Movimientos** | Al pagar ticket | Al emitir DTE |
| **Uso** | Retail/Tienda | Mayorista/B2B |

---

Generado: 2025-10-25
Sistema: RetailMind POS
Última actualización: Optimización FIFO en emisión DTEs

