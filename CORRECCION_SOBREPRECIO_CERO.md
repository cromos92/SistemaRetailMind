# ✅ CORRECCIÓN: Sobreprecio Mostraba $0

## ❌ PROBLEMA IDENTIFICADO

El detalle del despacho mostraba:
```
Costo: $0
Sobreprecio: $0
```

Aunque el modelo `Producto` tiene el campo `sobreprecio` con valor.

---

## 🔍 CAUSA DEL PROBLEMA

La función `buscar_productos_bodega` (backend) **NO incluía** el campo `sobreprecio` en la respuesta JSON enviada al frontend.

**Línea 9665-9688** (`views.py`):

```python
# ANTES:
productos_data.append({
    'id': producto.id,
    'articulo': producto.articulo,
    'costo': float(producto.costo) if producto.costo else 0,
    # ❌ sobreprecio NO ESTABA
    'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
    ...
})
```

---

## ✅ SOLUCIÓN APLICADA

### **Cambio 1: Backend - Agregar sobreprecio al objeto producto**

**Archivo**: `retailmind/app/views.py`  
**Línea**: 9675

```python
# DESPUÉS:
productos_data.append({
    'id': producto.id,
    'articulo': producto.articulo,
    'costo': float(producto.costo) if producto.costo else 0,
    'sobreprecio': float(producto.sobreprecio) if producto.sobreprecio else 0,  # ✅ AGREGADO
    'precio_venta': float(producto.precioventa) if producto.precioventa else 0,
    ...
})
```

### **Cambio 2: Backend - Agregar costo y sobreprecio a cada talla**

**Archivo**: `retailmind/app/views.py`  
**Líneas**: 9687-9688

```python
'tallas_detalle': [{
    'id': talla.id,
    'talla': talla.talla,
    'sku': str(talla.sku),
    'stock': talla.stock,
    'costo': float(producto.costo) if producto.costo else 0,  # ✅ AGREGADO
    'sobreprecio': float(producto.sobreprecio) if producto.sobreprecio else 0  # ✅ AGREGADO
} for talla in tallas_disponibles_obj]
```

### **Cambio 3: Frontend - Obtener y pasar costo/sobreprecio**

**Archivo**: `emisionDTE.html`  
**Línea**: ~3643-3657

```javascript
// Obtener costo y sobreprecio
const costoProducto = parseInt(inputCantidad.data('costo')) || 
                      parseInt(window.currentProduct.costo) || 0;
const sobrepreioProducto = parseInt(inputCantidad.data('sobreprecio')) || 
                           parseInt(window.currentProduct.sobreprecio) || 0;

console.log('💰 Costo y Sobreprecio:', {costo: costoProducto, sobreprecio: sobrepreioProducto});

const tallaData = [{
    ...
    costo: costoProducto,  // ✅ AGREGADO
    sobreprecio: sobrepreioProducto,  // ✅ AGREGADO
    ...
}];
```

### **Cambio 4: Frontend - Agregar data-attributes en modal**

**Archivo**: `emisionDTE.html`  
**Líneas**: ~3134-3164

```javascript
// Obtener costo y sobreprecio de la talla o del producto
const costoTalla = talla.costo || product.costo || 0;
const sobrepreioTalla = talla.sobreprecio || product.sobreprecio || 0;

<tr data-costo="${costoTalla}" data-sobreprecio="${sobrepreioTalla}">
    ...
    <input ... data-costo="${costoTalla}" data-sobreprecio="${sobrepreioTalla}">
</tr>
```

### **Cambio 5: Frontend - Mostrar en tabla del detalle**

**Archivo**: `emisionDTE.html`  
**Líneas**: ~3454-3455

```javascript
<td class="text-end text-muted"><small>$${costo.toLocaleString()}</small></td>
<td class="text-end text-muted"><small>$${sobreprecio.toLocaleString()}</small></td>
```

---

## 📊 FLUJO DE DATOS

```
1. Backend (views.py):
   ├─ Producto.sobreprecio → JSON response
   └─ Cada talla.sobreprecio → tallas_detalle

2. Frontend recibe:
   ├─ product.sobreprecio
   └─ talla.sobreprecio (en cada talla)

3. Modal de tallas:
   ├─ Renderiza con data-sobreprecio
   └─ Muestra en input

4. Al agregar al detalle:
   ├─ Obtiene desde data-sobreprecio
   ├─ Pasa en tallaData
   └─ Muestra en columna

5. Tabla de detalle:
   ├─ Columna "Costo": valor real
   ├─ Columna "Sobreprecio": valor real
   └─ Columna "Precio Unit.": según tipo despacho
```

---

## ✅ RESULTADO ESPERADO

### **Antes:**

```
Detalle del Despacho:
┌──────────┬─────────────┬──────────────┐
│ Costo    │ Sobreprecio │ Precio Unit. │
├──────────┼─────────────┼──────────────┤
│ $0       │ $0          │ $42,990      │ ❌
└──────────┴─────────────┴──────────────┘
```

### **Ahora:** ✅

```
Detalle del Despacho:
┌──────────┬─────────────┬──────────────┐
│ Costo    │ Sobreprecio │ Precio Unit. │
├──────────┼─────────────┼──────────────┤
│ $30,000  │ $12,990     │ $42,990      │ ✅
└──────────┴─────────────┴──────────────┘

Verificación:
$30,000 + $12,990 = $42,990 ✅
```

---

## 🧪 CÓMO VERIFICAR

### **Test 1: Ver Datos del Backend**

```
1. Abrir consola (F12)
2. Buscar un producto
3. Ver en Network la respuesta de /app/buscar_productos_bodega/
4. Verificar que el objeto producto tenga:
   {
     "costo": 30000,
     "sobreprecio": 12990,  ← DEBE aparecer
     "precio_venta": 42990
   }
```

### **Test 2: Ver en Modal de Tallas**

```
1. Abrir modal de tallas
2. Ver consola
3. Buscar logs:
   👕 Procesando talla 0: {...}
   
4. Verificar que tenga:
   {
     costo: 30000,
     sobreprecio: 12990
   }
```

### **Test 3: Ver en Detalle de Despacho**

```
1. Agregar producto al detalle
2. Ver tabla "Detalle del Despacho"
3. Verificar columnas:
   - Costo: $30,000 ✅ (no $0)
   - Sobreprecio: $12,990 ✅ (no $0)
   - Precio Unit.: $42,990 ✅

4. Ver consola, buscar log:
   💰 Desglose para fila: Costo: 30000, Sobreprecio: 12990, Precio: 42990
```

---

## 📋 ARCHIVOS MODIFICADOS

| Archivo | Cambio | Línea |
|---------|--------|-------|
| `views.py` | +sobreprecio en producto | 9675 |
| `views.py` | +costo y sobreprecio en tallas | 9687-9688 |
| `emisionDTE.html` | Obtener costo/sobreprecio de talla | 3643-3657 |
| `emisionDTE.html` | data-attributes en modal | 3134-3164 |
| `emisionDTE.html` | Mostrar en detalle | 3454-3455 |
| `emisionDTE.html` | Log de desglose | 3438 |

**Total**: 2 archivos, 6 cambios

---

## 🚀 PRUEBA AHORA

```bash
# 1. Ir a emisión DTE
http://localhost:8000/app/emisionDTE/

# 2. Buscar producto
- Escribir nombre de producto
- Presionar Enter

# 3. Abrir modal de tallas
- Clic en producto
- Ver modal

# 4. Agregar al detalle
- Seleccionar cantidad
- Clic en "+"

# 5. Verificar en Detalle del Despacho
- Columna "Costo": debe mostrar valor real (no $0)
- Columna "Sobreprecio": debe mostrar valor real (no $0)
- Columna "Precio Unit.": suma de ambos
```

---

## ✅ LOGS A VERIFICAR

En la consola, deberías ver:

```javascript
// Al buscar productos (Network):
{
  costo: 30000,
  sobreprecio: 12990,
  precio_venta: 42990
}

// Al cargar tallas:
👕 Procesando talla 0: {costo: 30000, sobreprecio: 12990}

// Al agregar al detalle:
💰 Costo y Sobreprecio: {costo: 30000, sobreprecio: 12990}
💰 Desglose para fila: Costo: 30000, Sobreprecio: 12990, Precio: 42990
➕ Creando nueva fila para producto/talla
✅ Nueva fila agregada - SKU: 4824824, Talla: 34, Cantidad: 1
```

---

**Fecha**: 2024-11-06  
**Estado**: ✅ CORREGIDO  
**Resultado**: Costo y Sobreprecio ahora muestran valores reales

