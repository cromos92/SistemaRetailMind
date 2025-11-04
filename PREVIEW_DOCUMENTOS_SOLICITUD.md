# 📄 Preview de Documentos en Solicitudes

## 🎯 Funcionalidad Implementada

Cuando el usuario crea una solicitud de regularización, ahora ve un **PREVIEW COMPLETO** de los documentos que se generarán, incluyendo montos calculados con los precios del DTE original.

---

## ✅ Información Agregada al Modal

### 1. **Precio Unitario del Producto Original**

Se muestra en la información del producto:

```
┌────────────────────────────────────────┐
│ 📦 Producto Original del DTE           │
├────────────────────────────────────────┤
│ Zapatilla Nike Air Max                 │
│ SKU: 100024 | Talla: 7                 │
│ Precio Unitario: $62.990  ← NUEVO     │
├────────────────────────────────────────┤
│ DTE: 1096                              │
│ Esperado: 10 | Recibido: 5 | Falta: 5 │
└────────────────────────────────────────┘
```

**Origen del precio:**
- Se obtiene del campo `precio` de `Dte_Productos`
- Es el mismo precio con el que se facturó originalmente
- Se envía desde el backend en `obtener_productos_regularizar`

---

## 📊 Preview en "Solicitar NC"

Cuando el usuario selecciona "Solicitar NC", ve:

```
┌────────────────────────────────────────────┐
│ 📄 Preview: Nota de Crédito a Generar     │
├────────────────────────────────────────────┤
│ Producto:      Zapatilla Nike Air Max     │
│ Cantidad:      5 unidades                 │
│ Precio Unit.:  $62.990                    │
│ ────────────────────────────────────────  │
│ Monto Total NC: $314.950                  │
│                                            │
│ ℹ️ Este es el monto que se facturó        │
│ originalmente y que será devuelto vía NC. │
└────────────────────────────────────────────┘
```

**Cálculo:**
```
Monto NC = Cantidad Problema × Precio Unitario Original
         = 5 × $62.990
         = $314.950
```

---

## 📦 Preview en "Solicitar Cambio de Producto"

Cuando el usuario selecciona producto de cambio, ve DOS previews:

### 1️⃣ Nota de Crédito (por producto original)

```
┌────────────────────────────────────────────┐
│ 1️⃣ Nota de Crédito (por producto original)│
├────────────────────────────────────────────┤
│ Producto:      Nike Air Max T7            │
│ Cantidad:      3 unidades                 │
│ Precio Unit.:  $62.990                    │
│ ────────────────────────────────────────  │
│ Monto NC:      $188.970                   │
└────────────────────────────────────────────┘
```

### 2️⃣ Nuevo DTE (con producto de cambio)

```
┌────────────────────────────────────────────┐
│ 2️⃣ Nuevo DTE (con producto de cambio)     │
├────────────────────────────────────────────┤
│ Tipo:          GUIA DE DESPACHO           │
│ Destino:       Tu sucursal                │
│ Producto:      Adidas Stan Smith T7       │
│ Cantidad:      3 unidades  ← Dinámica    │
│ Precio Unit.:  $55.990                    │
│ ────────────────────────────────────────  │
│ Monto Total:   $167.970    ← Actualiza   │
│                                            │
│ ℹ️ El emisor emitirá estos documentos      │
│ una vez apruebe tu solicitud.             │
└────────────────────────────────────────────┘
```

**Actualización Dinámica:**
```javascript
Usuario cambia cantidad de 1 a 3:
↓
Preview se actualiza automáticamente:
- Cantidad DTE: 1 → 3
- Monto DTE: $55.990 → $167.970
```

---

## 💡 Ejemplo Completo

### Escenario: NICK1 recibe de EDEL

```
DTE Original #1096:
- Producto: Nike Air Max T7
- Precio facturado: $62.990
- Cantidad esperada: 10
- Cantidad recepcionada: 5
- Faltante: 5
```

### Usuario solicita cambio:

**1. Modal inicial muestra:**
```
Producto Original del DTE:
Nike Air Max T7
SKU: 100024 | Talla: 7
Precio Unitario: $62.990  ← Del DTE original
```

**2. Usuario selecciona: Adidas Stan Smith T7 (Precio: $55.990)**

**3. Preview de NC aparece:**
```
1️⃣ Nota de Crédito:
Producto: Nike Air Max T7
Cantidad: 5 unidades
Precio: $62.990
Monto NC: $314.950  ← 5 × $62.990
```

**4. Preview de DTE aparece:**
```
2️⃣ Nuevo DTE:
Producto: Adidas Stan Smith T7
Cantidad: 1 unidad (inicial)
Precio: $55.990
Monto: $55.990
```

**5. Usuario cambia cantidad a 3:**
```
2️⃣ Nuevo DTE (actualizado):
Cantidad: 3 unidades  ← Cambió
Monto: $167.970  ← 3 × $55.990 (actualizado)
```

**6. Al confirmar, muestra resumen:**
```
Documentos que se generarán:

NC por original: $314.950
(5 × $62.990)

Nuevo DTE: 3 unidades
Producto de cambio
```

---

## 🔧 Implementación Técnica

### Backend: Agregar precio al endpoint

**Archivo:** `views.py` línea 658-686

```python
# Obtener precio del producto original del DTE
precio_unitario = 0
if recepcion.dte_producto:
    precio_unitario = recepcion.dte_producto.precio or 0

productos.append({
    # ... campos existentes ...
    'precio_unitario': precio_unitario  # NUEVO
})
```

### Frontend: Mostrar precio en modal

**Archivo:** `regularizar_recepciones.html` línea 717-719

```javascript
const precioUnitario = productoSeleccionado.precio_unitario || 0;
document.getElementById('regPrecioUnitario').textContent = 
    Number(precioUnitario).toLocaleString('es-CL');
```

### Preview de NC

**Archivo:** `regularizar_recepciones.html` línea 289-319

```html
<div class="card border-warning">
    <h6>Preview: Nota de Crédito a Generar</h6>
    <table>
        <tr><td>Producto:</td><td id="ncPreviewProducto">-</td></tr>
        <tr><td>Cantidad:</td><td id="ncPreviewCantidad">-</td></tr>
        <tr><td>Precio Unit.:</td><td>$<span id="ncPreviewPrecioUnit">0</span></td></tr>
        <tr><td>Monto Total NC:</td><td>$<span id="ncPreviewTotal">0</span></td></tr>
    </table>
</div>
```

### Preview de DTE Nuevo

**Archivo:** `regularizar_recepciones.html` línea 497-565

```html
<div class="card border-primary">
    <h6>2️⃣ Nuevo DTE (con producto de cambio)</h6>
    <table>
        <tr><td>Tipo:</td><td>GUIA DE DESPACHO</td></tr>
        <tr><td>Destino:</td><td id="previewDTEDestino">Tu sucursal</td></tr>
        <tr><td>Producto:</td><td id="previewDTEProducto">-</td></tr>
        <tr><td>Cantidad:</td><td id="previewDTECantidad">-</td></tr>
        <tr><td>Precio Unit.:</td><td>$<span id="previewDTEPrecio">0</span></td></tr>
        <tr><td>Monto Total:</td><td>$<span id="previewDTEMonto">0</span></td></tr>
    </table>
</div>
```

### Actualización Dinámica

**Archivo:** `regularizar_recepciones.html` línea 1157-1168

```javascript
const inputCantidad = document.getElementById('cantidadSolicitud');
const actualizarMontos = () => {
    const cantidad = parseInt(inputCantidad.value) || 0;
    document.getElementById('previewDTECantidad').textContent = cantidad;
    document.getElementById('previewDTEPrecio').textContent = Number(precio).toLocaleString('es-CL');
    document.getElementById('previewDTEMonto').textContent = Number(cantidad * precio).toLocaleString('es-CL');
};

inputCantidad.addEventListener('input', actualizarMontos);
```

---

## ✅ Beneficios

1. **Transparencia Total**
   - Usuario ve exactamente qué se generará
   - Montos calculados con precios reales del DTE original
   - No hay sorpresas

2. **Validación Visual**
   - Usuario puede verificar que los montos sean correctos
   - Compara NC con lo facturado originalmente
   - Ve el costo del producto de reemplazo

3. **Trazabilidad**
   - Precio de NC = Precio del DTE original
   - Garantiza que se devuelve lo mismo que se cobró
   - Cumple normativa SII

4. **UX Mejorada**
   - Actualización en tiempo real
   - Cálculos automáticos
   - Información clara y organizada

---

## 🎯 Garantía de Montos Correctos

### El monto de la NC ES el mismo que se facturó porque:

```
1. Se obtiene el precio de Dte_Productos (DTE original)
   ↓
2. Ese precio es el que se facturó en su momento
   ↓
3. Se usa para calcular monto de NC
   ↓
4. NC devuelve EXACTAMENTE lo facturado
```

### Ejemplo Numérico:

```
DTE Original #1096 (facturado el 1 Nov):
- Nike Air Max T7
- Precio: $62.990 (precio de venta al facturar)
- Cantidad: 10
- Total facturado: $629.900

Recepción:
- Llegaron solo 5
- Faltante: 5

Solicitud de NC:
- Cantidad: 5
- Precio: $62.990 (mismo del DTE #1096)
- Monto NC: $314.950 (5 × $62.990)

✅ La NC devuelve EXACTAMENTE lo que se cobró
   por esas 5 unidades faltantes
```

---

## 📋 Validación de Precios

```
Precio NC = Precio DTE Original
          = dte_productos.precio
          = Lo que se facturó
          
✅ Garantiza que NC devuelve lo correcto
✅ Cumple normativa SII
✅ Trazabilidad completa
```

---

¡Ahora el usuario ve exactamente qué documentos se generarán y con qué montos! 💰📄

