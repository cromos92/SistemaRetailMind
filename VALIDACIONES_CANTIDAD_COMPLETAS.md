# ✅ Validaciones de Cantidad - Implementación Completa

## 📊 Resumen de Validaciones

Se implementaron validaciones completas en **DOS flujos**:

1. **Ajustar Cantidad** (encontrar más productos)
2. **Cambiar Producto** (solicitud entre empresas)

---

## 1️⃣ Validaciones en "Ajustar Cantidad"

### Objetivo:
Aumentar la cantidad si se encontraron más productos después de la recepción inicial.

### Reglas de Validación:

```
✅ Mínimo: 1 unidad
✅ Máximo: Lo que faltaba
❌ No permite: 0
❌ No permite: Negativos
❌ No permite: Más de lo faltante
```

### Ejemplo Visual:

```
Producto: Zapatilla Nike T42
Esperado: 10
Recibido: 7
Faltante: 3

┌─────────────────────────────────────┐
│ ¿Cuántas unidades más encontraste? *│
│ [1] ▼                               │
│ Mínimo: 1 | Máximo: 3 unidades      │
└─────────────────────────────────────┘

Intentos:
❌ 0  → "Debe ser al menos 1 unidad"
✅ 1  → Nueva total: 8 de 10
✅ 2  → Nueva total: 9 de 10  
✅ 3  → Nueva total: 10 de 10 ✅ REGULARIZADO
❌ 4  → "No puedes ingresar más de 3 unidades"
❌ -1 → "Debe ser al menos 1 unidad"
```

### Implementación:

**Frontend:** `regularizar_recepciones.html` línea 712-762
```javascript
inputAjuste.max = cantidadFaltante;
inputAjuste.min = 1;
inputAjuste.value = Math.min(1, cantidadFaltante);

// Validación en tiempo real
inputAjuste.addEventListener('input', function() {
    if (valor <= 0) → ERROR
    if (valor > cantidadFaltante) → ERROR
    
    // Deshabilita botón si hay error
    btnGuardar.disabled = hayError;
});
```

**Backend:** `views.py` línea 1127-1167
```python
if cantidadAdicional <= 0:
    return JsonResponse({
        'error': 'La cantidad debe ser al menos 1 unidad'
    }, status=400)

if cantidadAdicional > cantidadFaltante:
    return JsonResponse({
        'error': f'No puedes agregar más de {cantidadFaltante}'
    }, status=400)
```

---

## 2️⃣ Validaciones en "Cambiar Producto" (Solicitud)

### Objetivo:
Solicitar cambio por otro producto cuando es entre empresas diferentes.

### Reglas de Validación:

```
✅ Mínimo: 1 unidad
✅ Máximo: Cantidad del problema
❌ No permite: 0
❌ No permite: Negativos
❌ No permite: Más del problema detectado
```

### Ejemplo Visual:

```
Producto Original: Nike Air T42
Problema: 5 faltantes

Usuario selecciona: Adidas Stan T42
↓
┌─────────────────────────────────────┐
│ ¿Cuántas unidades solicitas? *      │
│ [1] ▼                               │
│ Mínimo: 1 | Máximo: 5 unidades      │
│ (problema detectado: 5)             │
└─────────────────────────────────────┘

Intentos:
❌ 0  → "Debe ser al menos 1 unidad"
✅ 1  → OK
✅ 3  → OK
✅ 5  → OK (solicita todas)
❌ 6  → "No puedes solicitar más de 5 unidades"
```

### Campo Aparece Dinámicamente:

```javascript
// Solo se muestra después de seleccionar producto
seleccionarProductoSolicitud() {
    // Muestra producto
    // ↓
    mostrarCampoCantidadSolicitud();  // Muestra campo cantidad
}
```

### Implementación:

**HTML:** `regularizar_recepciones.html` línea 400-415
```html
<div id="contenedorCantidadSolicitud" style="display:none;">
    <label>¿Cuántas unidades solicitas? *</label>
    <input type="number" id="cantidadSolicitud" 
           min="1" max="0" value="1">
    <small>Mínimo: 1 | Máximo: <span id="maxCantidadSolicitud">0</span></small>
    <div id="errorCantidadSolicitud">...</div>
</div>
```

**JavaScript:** `regularizar_recepciones.html` línea 931-987
```javascript
function mostrarCampoCantidadSolicitud() {
    const cantidadProblema = cantidad_faltante || cantidad_danada || cantidad_esperada;
    
    inputCantidad.max = cantidadProblema;
    inputCantidad.min = 1;
    inputCantidad.value = Math.min(1, cantidadProblema);
    
    // Validación en tiempo real
    inputCantidad.addEventListener('input', function() {
        if (valor <= 0) → ERROR
        if (valor > cantidadProblema) → ERROR
    });
}
```

**Validación al Guardar:** línea 1175-1206
```javascript
const cantidadSolicitud = parseInt(cantidadSolicitud.value);

if (isNaN(cantidadSolicitud) || cantidadSolicitud <= 0) {
    Swal.fire('Error', 'Cantidad inválida');
    return;
}

if (cantidadSolicitud > cantidadProblema) {
    Swal.fire('Error', `No puedes solicitar más de ${cantidadProblema}`);
    return;
}

data.cantidad_solicitud = cantidadSolicitud;
```

**Backend:** `views.py` línea 1219-1240
```python
cantidad_solicitud = int(data.get('cantidad_solicitud', 0))
cantidad_problema = recepcion.cantidad_faltante or recepcion.cantidad_danada

if cantidad_solicitud <= 0:
    return JsonResponse({'error': 'Mínimo 1 unidad'}, status=400)

if cantidad_solicitud > cantidad_problema:
    return JsonResponse({
        'error': f'No puedes solicitar más de {cantidad_problema}'
    }, status=400)

# Guardar en solicitud
solicitud.cantidad_cambio_solicitada = cantidad_solicitud
```

---

## ✅ Cambios Adicionales

### Eliminado Campo de Evidencia

**Motivo:** No es necesario

**Archivos modificados:**
- ✅ `regularizar_recepciones.html` - Campo eliminado
- ✅ JavaScript - Código de evidencia eliminado

**ANTES:**
```html
<!-- Adjuntar evidencia (opcional) -->
<input type="file" id="evidenciaFotoSolicitud">
```

**AHORA:**
```html
<!-- Campo eliminado -->
```

---

## 📋 Flujo Completo con Validaciones

### Caso: Solicitar 3 unidades cuando faltaron 5

```
1. Usuario abre modal de regularización
2. Selecciona "Cambiar Producto"
3. Sistema detecta: Entre empresas → Muestra solicitud
4. Usuario busca "Adidas Stan T42"
5. Usuario selecciona producto
6. ✨ Campo de cantidad aparece automáticamente
   - min="1"
   - max="5"
   - value="1"
7. Usuario cambia cantidad a 3
8. Validación en tiempo real: ✅ OK
9. Usuario escribe justificación
10. Usuario presiona "Enviar Solicitud"
11. Validación final: ✅ OK
12. Backend valida: ✅ OK
13. ✅ Solicitud creada con cantidad = 3
```

---

## 🎯 Campos Validados en Cada Flujo

### Ajustar Cantidad:
| Campo | Validación |
|-------|-----------|
| Cantidad adicional | min=1, max=faltante |

### Cambiar Producto (Interno):
| Campo | Validación |
|-------|-----------|
| Producto | Requerido |

### Cambiar Producto (Solicitud):
| Campo | Validación |
|-------|-----------|
| Producto | Requerido, debe tener stock en emisor |
| Cantidad | min=1, max=problema, requerido |
| Justificación | Requerido, texto no vacío |

---

## 🔧 Validaciones Totales Implementadas

### Nivel 1: HTML Native
```html
<input type="number" min="1" max="X" step="1">
```

### Nivel 2: JavaScript Tiempo Real
```javascript
addEventListener('input') → valida y deshabilita botón
```

### Nivel 3: JavaScript al Guardar
```javascript
guardarRegularizacion() → doble check antes de enviar
```

### Nivel 4: Backend
```python
regularizar_producto_api() → validación final en servidor
```

---

## ✅ Estado Final

**Campos eliminados:**
- ❌ Cambiar Talla (opción completa)
- ❌ Evidencia fotográfica

**Campos con validación:**
- ✅ Ajustar Cantidad (min=1, max=faltante)
- ✅ Cantidad Solicitud (min=1, max=problema)

**Flujos funcionales:**
- ✅ Ajustar cantidad con validación robusta
- ✅ Cambio directo (traspaso interno)
- ✅ Solicitud de cambio (entre empresas) con cantidad validada

---

¡Todo validado y funcionando correctamente! 🎉

