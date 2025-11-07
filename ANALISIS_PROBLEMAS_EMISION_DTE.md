# 🔍 ANÁLISIS: Problemas en Emisión de DTE

## 📋 PROBLEMAS REPORTADOS

### **Problema 1: Stock sin Control**
**Descripción**: Un producto con stock 12 puede agregarse varias veces al detalle de despacho sin validar el stock total acumulado.

**Ejemplo**:
```
Producto: Zapatilla Nike Talla 40
Stock disponible: 12 unidades

Usuario puede agregar:
1. Primera vez: 5 unidades → Total en detalle: 5
2. Segunda vez: 5 unidades → Total en detalle: 10
3. Tercera vez: 5 unidades → Total en detalle: 15 ❌ (excede stock de 12)
```

### **Problema 2: SKU Incorrecto**
**Descripción**: En el detalle de despacho, donde dice "SKU" muestra el artículo del producto en lugar del SKU real de la variación/talla.

**Ejemplo**:
```
Columna del detalle:
Artículo: M9160C  
SKU esperado: 4827052
SKU mostrado: M9160C ❌ (muestra artículo)
```

---

## 🔍 ANÁLISIS TÉCNICO

### Código Actual (Líneas Relevantes)

#### Función agregarTallaIndividual (Línea 3388)

```javascript
function agregarTallaIndividual(tallaId) {
    const row = $(`tr[data-talla-id="${tallaId}"]`);
    const cantidad = parseInt(row.find('.cantidad-input').val()) || 0;
    
    if (cantidad <= 0) {
        Swal.fire('Atención', 'Ingrese una cantidad válida', 'warning');
        return;
    }
    
    // Obtener datos de la talla
    const sku = row.find('td:nth-child(2)').text().trim();  // ← PROBLEMA 2
    const talla = row.find('td:nth-child(3) .badge').text().trim();
    const stock = parseInt(row.find('td:nth-child(4) .badge').text()) || 0;
    const precio = parseInt(row.find('.cantidad-input').data('precio')) || 0;
    
    const tallaData = [{
        id: tallaId,
        sku: sku,  // ← SKU puede ser incorrecto aquí
        talla: talla,
        cantidad: cantidad,
        stock: stock,  // ← Stock individual, no acumulado
        precio: precio,
        subtotal: cantidad * precio
    }];
    
    addTallasToDetalle(tallaData, window.currentProduct);  // ← PROBLEMA 1
}
```

#### Función addTallasToDetalle (Línea 3207)

```javascript
function addTallasToDetalle(tallas, productOverride = null) {
    console.log('🔄 Agregando tallas al detalle:', tallas);
    
    const tbody = $('#detalleBody');
    $('#emptyRow').remove();
    
    // Crear una fila por cada talla seleccionada
    tallas.forEach(talla => {
        const rowHtml = `
            <tr data-producto-id="${currentProduct.id}" data-talla-id="${talla.id}">
                <td class="text-center">${currentProduct.articulo}</td>  // ← Columna 1: Artículo
                <td>${currentProduct.descripcion}</td>
                <td class="text-center">
                    <span class="badge bg-primary">${talla.talla}</span>
                    <br><small class="text-muted">${talla.sku}</small>  // ← SKU correcto aquí
                </td>
                <td class="text-center">
                    <input type="number" ... value="${talla.cantidad}" 
                           min="1" max="${talla.stock}" ...>  // ← Validación individual
                </td>
                ...
            </tr>
        `;
        tbody.append($row);  // ← Siempre agrega, no valida si ya existe
    });
    
    // Listener para cambios de cantidad
    $('.cantidad-detalle-input').on('input', function() {
        const cantidad = parseInt($(this).val()) || 0;
        const maxStock = parseInt($(this).data('stock-max')) || 0;
        
        if (cantidad > maxStock) {  // ← Solo valida contra stock individual
            $(this).val(maxStock);
            ...
        }
    });
}
```

---

## ⚠️ PROBLEMAS IDENTIFICADOS

### **Problema 1: Validación de Stock Acumulado**

**Ubicación**: Función `addTallasToDetalle` y `agregarTallaIndividual`

**Issue**:
1. La función siempre agrega una nueva fila al detalle
2. NO verifica si ya existe una fila para ese producto/talla
3. NO suma las cantidades ya existentes en el detalle
4. Validación de stock solo es individual por fila (max="${talla.stock}")

**Escenario Problemático**:
```
Stock disponible: 12 unidades

1. Usuario agrega 5 unidades → Fila 1: 5 unid. (OK)
2. Usuario vuelve a agregar 5 unidades → Fila 2: 5 unid. (OK individual, pero total 10)
3. Usuario agrega 5 más → Fila 3: 5 unid. (OK individual, pero total 15 > 12 ❌)
```

### **Problema 2: SKU Incorrecto en Obtención**

**Ubicación**: Función `agregarTallaIndividual`, línea 3401

**Issue**:
```javascript
const sku = row.find('td:nth-child(2)').text().trim();
```

Está obteniendo el texto de la **segunda columna** del modal de tallas, que puede contener:
- El artículo del producto
- O el SKU (dependiendo de cómo esté estructurado el modal)

Luego ese valor se pasa a `addTallasToDetalle` donde se usa:
```javascript
<br><small class="text-muted">${talla.sku}</small>
```

Si `talla.sku` contiene el artículo en lugar del SKU real, se mostrará incorrectamente.

---

## ✅ SOLUCIONES PROPUESTAS

### **Solución 1: Validación de Stock Acumulado**

Modificar `addTallasToDetalle` para:

1. **Verificar si ya existe** una fila con ese producto/talla en el detalle
2. **Sumar cantidades** en lugar de crear fila duplicada
3. **Validar stock total** acumulado contra stock disponible

```javascript
function addTallasToDetalle(tallas, productOverride = null) {
    ...
    
    tallas.forEach(talla => {
        // NUEVO: Verificar si ya existe en el detalle
        const existingRow = $(`#detalleBody tr[data-producto-id="${currentProduct.id}"][data-talla-id="${talla.id}"]`).first();
        
        if (existingRow.length > 0) {
            // Ya existe: sumar cantidades
            const inputCantidad = existingRow.find('.cantidad-detalle-input');
            const cantidadActual = parseInt(inputCantidad.val()) || 0;
            const nuevaCantidad = cantidadActual + talla.cantidad;
            const stockMax = parseInt(inputCantidad.data('stock-max')) || 0;
            
            // Validar que no exceda el stock
            if (nuevaCantidad > stockMax) {
                Swal.fire({
                    icon: 'warning',
                    title: 'Stock Insuficiente',
                    text: `Ya hay ${cantidadActual} unidades en el detalle. Stock disponible: ${stockMax}. No se puede agregar ${talla.cantidad} más.`,
                    confirmButtonText: 'Entendido'
                });
                return; // No agregar
            }
            
            // Actualizar cantidad y recalcular subtotal
            inputCantidad.val(nuevaCantidad).trigger('input');
            console.log(`✅ Cantidad actualizada: ${cantidadActual} + ${talla.cantidad} = ${nuevaCantidad}`);
            
        } else {
            // No existe: crear nueva fila (código actual)
            const rowHtml = `...`;
            tbody.append($row);
        }
    });
    
    recalcularTotales();
}
```

### **Solución 2: SKU Correcto**

**Opción A: Obtener SKU desde data-attribute**

Modificar el modal de tallas para almacenar el SKU en un atributo:

```html
<tr data-talla-id="${talla.id}" data-sku="${talla.sku}">
    <td>...</td>
    <td>${talla.sku}</td>  <!-- SKU visible -->
    ...
</tr>
```

Luego en JavaScript:

```javascript
function agregarTallaIndividual(tallaId) {
    const row = $(`tr[data-talla-id="${tallaId}"]`);
    
    // NUEVO: Obtener SKU desde data-attribute
    const sku = row.data('sku') || row.find('td:nth-child(2)').text().trim();
    ...
}
```

**Opción B: Usar el objeto talla original**

Si tienes acceso al objeto talla original con todos sus datos:

```javascript
// En lugar de obtener del DOM, usar el objeto directo
const tallaData = tallasDisponibles.find(t => t.id === tallaId);

if (tallaData) {
    addTallasToDetalle([{
        id: tallaData.id,
        sku: tallaData.sku,  // ← SKU directo del objeto
        talla: tallaData.talla,
        cantidad: cantidad,
        stock: tallaData.stock,
        precio: tallaData.precio
    }], window.currentProduct);
}
```

---

## 📊 COMPARACIÓN

| Aspecto | Actual | Con Solución |
|---------|--------|--------------|
| **Agregar 2 veces el mismo producto** | ✅ Permite (crea 2 filas) | ❌ No permite o suma cantidades |
| **Stock total validado** | ❌ No | ✅ Sí |
| **SKU mostrado** | ❌ Puede ser artículo | ✅ SKU real |
| **Experiencia de usuario** | ⚠️ Confusa | ✅ Clara |

---

## 🧪 CASOS DE PRUEBA

### Caso 1: Agregar Misma Talla Dos Veces

**Antes**:
```
Stock: 12
1. Agregar 5 → Fila 1: 5 unid
2. Agregar 5 → Fila 2: 5 unid (duplicado)
Total: 2 filas, 10 unidades
```

**Después (Solución)**:
```
Stock: 12
1. Agregar 5 → Fila 1: 5 unid
2. Agregar 5 → Fila 1: 10 unid (actualiza fila existente)
Total: 1 fila, 10 unidades
```

### Caso 2: Exceder Stock

**Antes**:
```
Stock: 12
1. Agregar 10 → Fila 1: 10 unid
2. Agregar 10 → Fila 2: 10 unid (debería fallar)
Total: 20 unidades (excede stock de 12) ❌
```

**Después (Solución)**:
```
Stock: 12
1. Agregar 10 → Fila 1: 10 unid
2. Agregar 10 → Mensaje de error: "Ya hay 10 en detalle, stock disponible 12" ✅
Total: 10 unidades (correcto)
```

---

## 🎯 PLAN DE IMPLEMENTACIÓN

### Fase 1: Validación de Stock Acumulado (30-45 min)
1. Modificar función `addTallasToDetalle`
2. Agregar verificación de fila existente
3. Implementar suma de cantidades
4. Validar contra stock total disponible
5. Mostrar mensajes informativos

### Fase 2: Corrección de SKU (15-30 min)
1. Verificar estructura del modal de tallas
2. Agregar `data-sku` attribute si no existe
3. Modificar `agregarTallaIndividual` para obtener SKU correcto
4. Verificar que se muestre SKU real en detalle
5. Testing

### Fase 3: Testing y Ajustes (15-20 min)
1. Probar agregar mismo producto 2 veces
2. Probar exceder stock
3. Verificar SKU correcto en detalle
4. Ajustes finales

**Tiempo total estimado**: 1-1.5 horas

---

## 📚 ARCHIVOS A MODIFICAR

1. **`retailmind/app/templates/vistas/modulo_documentos/emisionDTE.html`**
   - Línea ~3388: Función `agregarTallaIndividual`
   - Línea ~3207: Función `addTallasToDetalle`
   - Modal de tallas (agregar data-sku si falta)

---

## ✅ BENEFICIOS DE LAS CORRECCIONES

1. **Previene errores de inventario**: No permite despachar más de lo disponible
2. **Mejor UX**: Usuario ve cantidad acumulada en una sola fila
3. **SKU correcto**: Información precisa en documentos
4. **Auditoría**: Stock correctamente controlado

---

**¿Quieres que implemente estas correcciones ahora?**

Puedo:
1. Agregar validación de stock acumulado
2. Corregir el SKU para que muestre el valor real
3. Testing completo

Dime si procedo con la implementación. 🚀

