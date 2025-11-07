# Solución: Búsqueda de Productos en Ticket de Venta

## Fecha: 7 de Noviembre, 2025

## Problemas Identificados

1. **Filtros no funcionaban**: Los parámetros enviados desde el frontend no coincidían con lo que esperaba la API
2. **No filtraba por sucursal**: No se estaba enviando el ID de la sucursal actual en la búsqueda
3. **Falta checkbox de stock**: No había opción para filtrar solo productos con stock disponible

## Soluciones Implementadas

### 1. Actualización del Modal de Búsqueda (ticket_venta.html)

#### Cambios en la Interfaz:

**Antes:**
- 4 campos separados: Artículo, Descripción, Marca, SKU
- Sin información de sucursal
- Sin checkbox para filtrar por stock

**Después:**
- **Información de sucursal actual** visible en el modal
- **Campo de búsqueda unificado** para buscar en Artículo, Descripción o SKU
- **Checkbox "Solo con stock disponible"** activado por defecto
- **Checkbox "Buscar en todas mis sucursales"** para expandir la búsqueda más allá de la sucursal actual

```html
<!-- Nuevo diseño del modal -->
<div class="alert alert-info py-2 mb-3">
    <i class="ri-information-line me-1"></i>
    <strong>Sucursal:</strong> <span id="modalSucursalActual">{{ sucursal_actual.alias|default:"No seleccionada" }}</span>
    <span class="ms-3">
        <input type="checkbox" class="form-check-input me-1" id="chkBuscarTodasSucursales">
        <label class="form-check-label" for="chkBuscarTodasSucursales">
            <small>Buscar en todas mis sucursales</small>
        </label>
    </span>
</div>

<div class="row g-3 mb-3">
    <div class="col-md-8">
        <label class="form-label mb-1"><small>Búsqueda General (Artículo, Descripción o SKU)</small></label>
        <input type="text" class="form-control form-control-sm" id="filtroBusqueda" placeholder="Ej: Polera, Nike, 123456...">
    </div>
    <div class="col-md-4">
        <label class="form-label mb-1"><small>&nbsp;</small></label>
        <div class="form-check mt-2">
            <input type="checkbox" class="form-check-input" id="chkSoloConStock" checked>
            <label class="form-check-label" for="chkSoloConStock">
                <strong>Solo con stock disponible</strong>
            </label>
        </div>
    </div>
</div>
```

### 2. Actualización de la Función JavaScript de Búsqueda

**Antes:**
```javascript
const filtros = {
    articulo: filtroArticulo,
    descripcion: filtroDescripcion,
    marca: filtroMarca,
    sku: filtroSku,
    page: pagina
};
```

**Después:**
```javascript
const filtros = {
    search: filtroBusqueda,              // Búsqueda general
    solo_con_stock: soloConStock ? 'on' : 'off',  // Filtro de stock
    page: pagina
};

// Filtrar por sucursal actual (si no está marcado "buscar todas")
if (!buscarTodas && sucursalActualId) {
    filtros.sucursal_id = sucursalActualId;
}
```

### 3. Actualización de la Vista Backend (views.py)

Se agregó el campo `precio_venta` a la respuesta JSON de la API `obtener_productos_sucursal`:

```python
productos_data.append({
    'id': producto.id,
    'articulo': producto.articulo,
    'descripcion': producto.descripcion,
    'sucursal': producto.sucursal.alias,
    'categoria': producto.categoria.nombre if producto.categoria else '',
    'marca': producto.atributo1.valor if producto.atributo1 else '',
    'color': producto.atributo2.valor if producto.atributo2 else '',
    'genero': producto.atributo3.valor if producto.atributo3 else '',
    'stock_total': stock_total,
    'precio_venta': float(producto.precio_venta) if producto.precio_venta else 0,  # ← NUEVO
    'tallas_stock': tallas_stock,
    'tipo_talla': producto.tipo_talla,
})
```

### 4. Mejoras en la Función de Visualización de Resultados

Se corrigió la función `mostrarResultadosBusqueda` para usar correctamente los datos que devuelve la API:

```javascript
productos.forEach(producto => {
    // La API ya devuelve 'marca' como string, no como objeto
    const marca = producto.marca || '-';
    
    // Obtener precio de venta del producto
    const precioVenta = producto.precio_venta || 0;
    const precioFormateado = parseInt(precioVenta).toLocaleString();
    
    // Mostrar cada talla con su stock
    if (producto.tallas_stock && producto.tallas_stock.length > 0) {
        producto.tallas_stock.forEach(tallaStock => {
            const stockDisponible = tallaStock.stock || 0;
            // Crear fila para cada combinación producto-talla
            // Deshabilitar botón si no hay stock
        });
    }
});
```

## Características Nuevas

### 1. Filtro por Sucursal
- **Por defecto**: Busca solo en la sucursal actual del usuario
- **Opción**: Marcar checkbox para buscar en todas las sucursales disponibles

### 2. Filtro por Stock
- **Activado por defecto**: Solo muestra productos con stock disponible
- **Desactivable**: El usuario puede desmarcar para ver todos los productos

### 3. Búsqueda Unificada
- Un solo campo de búsqueda que filtra por:
  - Artículo
  - Descripción
  - SKU
- Más simple e intuitivo que múltiples campos

### 4. Validación Mejorada
- No permite buscar sin criterios
- Muestra mensajes claros al usuario
- Desactiva botones de productos sin stock

## Flujo de Búsqueda Actualizado

1. Usuario abre modal de búsqueda desde ticket de venta
2. Ve información de su sucursal actual
3. Por defecto, el checkbox "Solo con stock" está marcado
4. Ingresa término de búsqueda (ej: "polera", "nike", "123456")
5. Presiona "Buscar" o Enter
6. Sistema busca en la sucursal actual con los filtros aplicados
7. Muestra resultados con:
   - SKU de cada talla
   - Información del producto
   - Stock disponible
   - Precio de venta
   - Botón para seleccionar (deshabilitado si no hay stock)

## Archivos Modificados

1. **retailmind/app/templates/vistas/modulo_ventas/ticket_venta.html**
   - Actualización del modal de búsqueda
   - Nuevos checkboxes de filtro
   - Funciones JavaScript actualizadas

2. **retailmind/app/views.py**
   - Vista `obtener_productos_sucursal`: Agregado campo `precio_venta` en respuesta

## Cómo Probar

1. Acceder a: `http://localhost:8000/app/ticket-venta/`
2. Seleccionar un vendedor
3. Click en "Buscar Artículo"
4. Verificar que se muestra la sucursal actual
5. Probar búsquedas con:
   - Checkbox "Solo con stock" marcado → debe mostrar solo productos con stock
   - Checkbox "Solo con stock" desmarcado → debe mostrar todos
   - Checkbox "Buscar en todas mis sucursales" marcado → busca en todas
   - Checkbox "Buscar en todas mis sucursales" desmarcado → solo sucursal actual

## Beneficios

✅ **Búsqueda más rápida y eficiente**
✅ **Filtrado correcto por sucursal**
✅ **Control sobre productos con/sin stock**
✅ **Interfaz más clara y simple**
✅ **Mejor experiencia de usuario**
✅ **Evita intentar vender productos sin stock**

## Notas Técnicas

- La API `obtener_productos_sucursal` ya tenía la lógica de filtrado por sucursal y stock, solo faltaba integrarla correctamente desde el frontend
- El filtro de stock usa anotación Django para sumar el stock de todas las tallas
- La búsqueda es case-insensitive y busca en múltiples campos simultáneamente
- La paginación se mantiene funcional con los nuevos filtros

