# Corrección: Emisión de DTE - Gestión de Stock y Movimientos

## Fecha
14 de Noviembre, 2025

## Problema Identificado

Al emitir un DTE (Documento Tributario Electrónico) de tipo traspaso interno o venta externa, se identificaron los siguientes problemas:

### 1. Doble Descuento de Stock
- **Problema**: El código estaba modificando el campo `Producto_Talla.stock` directamente
- **Impacto**: En un sistema híbrido que calcula stock desde movimientos, esto causaba inconsistencias

### 2. Validación Incorrecta de Stock
- **Problema**: Se validaba el stock usando el campo `talla.stock` directo en lugar de calcular el stock específico de la sucursal
- **Impacto**: No se validaba correctamente el stock disponible en la sucursal de origen

### 3. Confusión sobre Movimientos de Recepción
- **Problema reportado**: "Se crean movimientos de recepción en la sucursal de destino"
- **Realidad**: No se creaban automáticamente, pero el código no era claro sobre cuándo se deben crear

## Sistema de Stock Híbrido

El sistema utiliza un enfoque híbrido para gestión de stock:

### Método `Producto_Talla.stock_sucursal(sucursal_id)`
```python
def stock_sucursal(self, sucursal_id):
    """
    Calcula el stock disponible en una sucursal específica
    
    SISTEMA HÍBRIDO:
    - Si existen movimientos: Calcula desde movimientos (sistema nuevo)
    - Si NO hay movimientos: Usa campo 'stock' directo (datos migrados/legacy)
    """
```

### Cálculo de Stock
- **Con movimientos**: `stock = ingresos + egresos` (egresos son negativos)
- **Sin movimientos**: Usa el campo `stock` directo (solo si pertenece a la sucursal)

## Correcciones Implementadas

### 1. Validación de Stock por Sucursal

**ANTES:**
```python
stock_disponible = talla.stock  # Campo directo
```

**DESPUÉS:**
```python
stock_disponible = talla.stock_sucursal(sucursal_id)  # ✅ Stock específico de sucursal
```

### 2. Eliminación de Modificación Directa del Campo Stock

**ANTES:**
```python
# Crear movimiento
Movimientos_Producto.objects.create(...)

# ACTUALIZAR campo stock (descuento)
talla.stock -= cantidad  # ❌ Doble descuento
talla.save()
```

**DESPUÉS:**
```python
# Crear movimiento
Movimientos_Producto.objects.create(...)

# ✅ NO modificar el campo talla.stock
# El stock se calcula automáticamente desde los movimientos usando stock_sucursal()
```

### 3. Clarificación del Flujo de Traspasos Internos

**Flujo Correcto para Traspasos Internos:**

1. **Al EMITIR el DTE** (Sucursal Origen):
   - Se crea UN movimiento de `EGRESO` en la sucursal origen
   - Estado: `COMPLETADO` (el stock ya salió físicamente)
   - Campo `cantidad`: valor negativo (ej: -10)
   - Campo `sucursal_origen`: Sucursal de donde sale
   - Campo `sucursal_destino`: Sucursal a donde va
   - **NO se crea movimiento de INGRESO en destino**

2. **Al RECEPCIONAR el DTE** (Sucursal Destino):
   - Se llama a `confirmar_recepcion_api()`
   - Se crea UN movimiento de `INGRESO` en la sucursal destino
   - Estado: `COMPLETADO` (el stock llegó físicamente)
   - Campo `cantidad`: valor positivo (ej: +10)
   - Se registra en `Productos_Recepcionados`

## Archivos Modificados

### `retailmind/app/views.py`

#### Función: `emitir_dte()` (líneas 9493-9843)

**Cambios:**
1. Líneas 9633-9645: Validación de stock usando `stock_sucursal()`
2. Líneas 9768-9818: Eliminación de modificación directa del campo `stock`
3. Líneas 9792-9794: Comentarios explicativos sobre el flujo de traspasos

## Impacto de los Cambios

### ✅ Beneficios
1. **Consistencia**: El stock se calcula siempre desde movimientos
2. **Exactitud**: Se valida el stock específico de cada sucursal
3. **Claridad**: Código documentado sobre cuándo se crean movimientos
4. **No hay doble descuento**: Se elimina la modificación directa del campo stock

### ⚠️ Consideraciones
1. **Productos sin movimientos**: Seguirán usando el campo `stock` directo (legacy)
2. **Migración gradual**: El sistema es compatible con datos migrados
3. **Recepción manual**: La sucursal destino debe recepcionar manualmente el DTE

## Flujo Completo de Traspaso Interno

### Paso 1: Emisión (Sucursal A → Sucursal B)
```
Usuario en Sucursal A emite DTE de traspaso a Sucursal B
↓
Se valida stock en Sucursal A usando stock_sucursal(A)
↓
Se crea DTE con tipo_transaccion='TRASPASO'
↓
Se crea movimiento:
  - tipo_movimiento: 'EGRESO'
  - concepto: 'TRASPASO_SALIDA'
  - sucursal_origen: Sucursal A
  - sucursal_destino: Sucursal B
  - cantidad: -10 (negativo)
  - estado: 'COMPLETADO'
↓
Stock en Sucursal A: Se reduce automáticamente (calculado desde movimientos)
Stock en Sucursal B: Sin cambios (aún no recepcionado)
```

### Paso 2: Recepción (Sucursal B)
```
Usuario en Sucursal B accede a /app/recepcion-dte/
↓
Ve el DTE pendiente de recepción
↓
Confirma recepción con confirmar_recepcion_api()
↓
Se registra en Productos_Recepcionados
↓
Se crea movimiento:
  - tipo_movimiento: 'INGRESO'
  - concepto: 'TRASPASO_ENTRADA'
  - sucursal_destino: Sucursal B
  - cantidad: +10 (positivo)
  - estado: 'COMPLETADO'
↓
Stock en Sucursal B: Se incrementa automáticamente (calculado desde movimientos)
```

## Verificación

### Para verificar que funciona correctamente:

1. **Emitir un DTE de traspaso interno**:
   ```
   Sucursal Origen → Sucursal Destino
   ```

2. **Verificar en base de datos**:
   ```sql
   -- Debe haber SOLO UN movimiento de EGRESO
   SELECT * FROM app_movimientos_producto 
   WHERE dte_id = [DTE_ID];
   
   -- Resultado esperado: 1 registro
   -- tipo_movimiento: 'EGRESO'
   -- concepto: 'TRASPASO_SALIDA'
   -- estado: 'COMPLETADO'
   -- cantidad: negativa
   ```

3. **Verificar stock en sucursal origen**:
   ```python
   talla = Producto_Talla.objects.get(id=talla_id)
   stock_origen = talla.stock_sucursal(sucursal_origen_id)
   # Debe ser menor que antes de emitir
   ```

4. **Verificar stock en sucursal destino**:
   ```python
   stock_destino = talla.stock_sucursal(sucursal_destino_id)
   # Debe ser igual que antes (aún no recepcionado)
   ```

5. **Después de recepcionar**:
   ```sql
   -- Ahora debe haber DOS movimientos
   SELECT * FROM app_movimientos_producto 
   WHERE dte_id = [DTE_ID];
   
   -- Resultado esperado: 2 registros
   -- 1. EGRESO en origen (cantidad negativa)
   -- 2. INGRESO en destino (cantidad positiva)
   ```

## Conclusión

Los cambios implementados aseguran que:
- ✅ Solo se crea movimiento de EGRESO al emitir
- ✅ Solo se crea movimiento de INGRESO al recepcionar
- ✅ El stock se calcula correctamente por sucursal
- ✅ No hay modificación directa del campo stock
- ✅ El flujo es claro y documentado

