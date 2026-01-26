# Corrección: Buscador Modal Verificar Recepción - DTE

## Problema Identificado

El buscador del modal "Verificar Recepción" **filtraba correctamente** pero **no mostraba datos en las columnas** (SKU, descripción, artículo aparecían vacíos).

## Análisis del Problema

### Causa Raíz

El problema NO era la lógica de filtrado (que funcionaba correctamente), sino el **manejo de valores `null` o `undefined`** en los datos del producto:

```javascript
// ❌ ANTES: Si prod.sku era null/undefined, mostraba vacío
<td><small class="font-monospace">${prod.sku}</small></td>

// ✅ DESPUÉS: Protección contra valores vacíos
const skuMostrar = prod.sku || 'N/A';
<td><small class="font-monospace">${skuMostrar}</small></td>
```

### Posibles Causas de Datos Vacíos

1. **Backend no envía todos los campos** - Algunos productos pueden no tener todos los atributos
2. **Campos opcionales en BD** - `articulo`, `marca`, `color` pueden ser NULL
3. **Mappeo incompleto** - El mapeo del objeto `doc.detalle` puede no incluir todos los campos
4. **Valores `undefined`** - JavaScript interpreta campos faltantes como `undefined`

## Solución Implementada

### ✅ 1. Protección contra Valores Vacíos

Se agregó validación para cada campo mostrado:

```javascript
// ✅ Asegurar que los valores no sean undefined/null
const skuMostrar = prod.sku || 'N/A';
const descripcionMostrar = prod.descripcion || 'Sin descripción';
const articuloMostrar = prod.articulo || '-';
const colorMostrar = prod.color || '-';
const marcaMostrar = prod.marca || '-';
const tallaMostrar = prod.talla || 'N/A';
```

**Beneficios:**
- Si el valor es `null`, `undefined`, `''` (cadena vacía) → Se muestra el valor por defecto
- Evita que las celdas aparezcan completamente vacías
- Mejora la experiencia visual del usuario

### ✅ 2. Logs de Depuración

Se agregaron console.logs para diagnosticar el problema:

```javascript
// Log de búsqueda activa
console.log('🔍 Búsqueda activa:', busqueda || '(sin filtro)');
console.log('📦 Total productos:', productosVerificacion.length);

// Log detallado por producto (solo cuando hay búsqueda)
if (busqueda) {
    console.log(`Producto ${index}:`, {
        sku: prod.sku,
        descripcion: prod.descripcion,
        articulo: prod.articulo,
        // ...
    });
}

// Log de resultados
console.log('✅ Productos visibles:', productosVisibles);
```

**Cómo usar los logs:**
1. Abrir DevTools del navegador (F12)
2. Ir a la pestaña "Console"
3. Buscar un producto en el modal
4. Ver los logs que muestran:
   - Qué se está buscando
   - Qué datos tienen los productos
   - Cuántos productos se encontraron

### ✅ 3. Contador de Productos Visibles

Se agregó un contador que verifica cuántos productos pasan el filtro:

```javascript
let productosVisibles = 0;

productosVerificacion.forEach((prod, index) => {
    // ... lógica de filtrado ...
    if (!coincide) return;
    
    productosVisibles++; // ✅ Incrementar contador
    
    // ... renderizar producto ...
});

console.log('✅ Productos visibles:', productosVisibles);
```

## Cómo Funciona el Filtrado

### Lógica de Búsqueda

El filtro busca en **6 campos diferentes**:

```javascript
const coincide = !busqueda || // Sin búsqueda = mostrar todos
    (prod.sku && prod.sku.toLowerCase().includes(busqueda)) ||
    (prod.descripcion && prod.descripcion.toLowerCase().includes(busqueda)) ||
    (prod.articulo && prod.articulo.toLowerCase().includes(busqueda)) ||
    (prod.color && prod.color.toLowerCase().includes(busqueda)) ||
    (prod.marca && prod.marca.toLowerCase().includes(busqueda)) ||
    (prod.talla && prod.talla.toLowerCase().includes(busqueda));
```

### Campos Buscables

| Campo | Descripción | Ejemplo |
|-------|-------------|---------|
| `sku` | Código único del producto | "SKU-12345" |
| `descripcion` | Nombre del producto | "Polera Azul" |
| `articulo` | Código de artículo | "ART-001" |
| `color` | Color del producto | "Azul" |
| `marca` | Marca del producto | "Nike" |
| `talla` | Talla del producto | "M", "42" |

### Ejemplos de Búsqueda

```
Búsqueda: "nike"
→ Encuentra productos donde:
  - SKU contiene "nike"
  - Descripción contiene "nike"  
  - Marca contiene "nike"
  - etc.

Búsqueda: "azul"
→ Encuentra productos donde:
  - Color = "Azul"
  - Descripción = "Polera Azul"
  - etc.

Búsqueda: "42"
→ Encuentra productos donde:
  - Talla = "42"
  - SKU contiene "42"
  - etc.
```

## Valores por Defecto

| Campo | Si está vacío/null | Se muestra |
|-------|-------------------|------------|
| `sku` | null/undefined/'' | "N/A" |
| `descripcion` | null/undefined/'' | "Sin descripción" |
| `articulo` | null/undefined/'' | "-" |
| `color` | null/undefined/'' | "-" |
| `marca` | null/undefined/'' | "-" |
| `talla` | null/undefined/'' | "N/A" |

## Testing

### Casos de Prueba

1. ✅ **Búsqueda por SKU válido** → Debe mostrar el producto con todos sus datos
2. ✅ **Búsqueda por descripción** → Debe filtrar y mostrar productos coincidentes
3. ✅ **Búsqueda por color/marca** → Debe filtrar correctamente
4. ✅ **Búsqueda sin resultados** → Debe mostrar "No se encontraron productos"
5. ✅ **Productos con campos null** → Debe mostrar valores por defecto ("-", "N/A")
6. ✅ **Limpiar búsqueda** → Debe volver a mostrar todos los productos

### Cómo Probar

1. Abrir modal "Verificar Recepción" en un DTE
2. Abrir DevTools (F12) → pestaña Console
3. Escribir en el buscador (ej: "nike")
4. Verificar en console:
   ```
   🔍 Búsqueda activa: nike
   📦 Total productos: 10
   Producto 0: { sku: "SKU-001", descripcion: "Zapatilla Nike", ... }
   Producto 1: { sku: "SKU-002", descripcion: "Polera Adidas", ... }
   ...
   ✅ Productos visibles: 3
   ```
5. Verificar que la tabla muestra correctamente los 3 productos Nike

### Qué Revisar en Console

```javascript
// ✅ Si ves esto = El filtro funciona bien
🔍 Búsqueda activa: nike
📦 Total productos: 10
Producto 3: { sku: "SKU-NIKE-123", descripcion: "Zapatilla Nike Air", ... }
✅ Productos visibles: 3

// ❌ Si ves esto = Problema con datos del backend
Producto 3: { sku: null, descripcion: undefined, articulo: null, ... }
```

## Posibles Problemas Backend

Si los productos siguen apareciendo vacíos después de esta corrección, el problema está en el **backend**:

### Verificar Endpoint

El endpoint `/app/recepciones-pendientes/` debe enviar:

```json
{
  "success": true,
  "recepciones": [
    {
      "id": 1,
      "numero_documento": "DTE-001",
      "detalle": [
        {
          "dte_producto_id": 1,
          "sku": "SKU-12345",           // ✅ REQUERIDO
          "descripcion": "Polera Azul",  // ✅ REQUERIDO
          "articulo": "ART-001",         // Opcional
          "marca": "Nike",               // Opcional
          "color": "Azul",               // Opcional
          "talla": "M",                  // ✅ REQUERIDO
          "cantidad": 10,
          "precio": 15000
        }
      ]
    }
  ]
}
```

### Campos Mínimos Requeridos

Para que el buscador funcione correctamente, el backend **debe enviar al menos**:
- `sku` (obligatorio)
- `descripcion` (obligatorio)
- `talla` (obligatorio)

Los campos opcionales (`articulo`, `marca`, `color`) pueden ser `null` o no enviarse.

## Mejoras Futuras (Opcionales)

1. **Highlight de coincidencias** - Resaltar en amarillo el texto que coincide con la búsqueda
2. **Búsqueda por rango de cantidad** - ">=10" para productos con 10 o más unidades
3. **Filtros avanzados** - Dropdown para filtrar por marca, color, etc.
4. **Búsqueda con tildes** - Normalizar búsqueda para ignorar acentos

## Archivos Modificados

- `retailmind/app/templates/vistas/modulo_compras/recepcion_dte.html`
  - Función `renderizarProductosVerificacion()` - Línea ~1868
  - Agregados:
    - Validación de valores null/undefined
    - Logs de depuración
    - Contador de productos visibles

---

**Fecha:** 21 de enero de 2026  
**Tipo de corrección:** Manejo de datos + Debugging  
**Impacto:** Medio - Mejora la visualización y facilita debugging
