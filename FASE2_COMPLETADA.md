# ✅ Fase 2: Actualizar Sistema de Regularización - COMPLETADA

## 📊 Resumen de Implementación

La Fase 2 integra el sistema de SOLICITUDES con el sistema de regularización existente, permitiendo dos flujos diferentes según el tipo de traspaso.

---

## ✅ Frontend Completado

### 1. **Detección Automática de Tipo de Traspaso**

**Archivo:** `regularizar_recepciones.html` (línea 465-521)

```javascript
function abrirModalRegularizar(productoId) {
    // ...
    
    // Detecta automáticamente
    window.requiereSolicitud = productoSeleccionado.requiere_nc || false;
    
    if (productoSeleccionado.requiere_nc) {
        // 🏢 Entre empresas → Muestra alertas y opciones de solicitud
        alertTipo.className = 'alert alert-warning mb-3';
        tituloTipo.textContent = '🏢 Traspaso Entre Empresas';
        descripcionTipo.innerHTML = `
            • Para ajustes de cantidad: Se genera NC automáticamente
            • Para cambio de producto: Se crea SOLICITUD
        `;
    } else {
        // ✏️ Interno → Flujo directo
        alertTipo.className = 'alert alert-success mb-3';
        tituloTipo.textContent = '✏️ Traspaso Interno';
    }
}
```

### 2. **UI Dual en Panel Cambiar Producto**

**Archivo:** `regularizar_recepciones.html` (línea 306-419)

```html
<div id="panelCambiarProducto">
    
    <!-- Opción A: Cambio Directo (Traspaso Interno) -->
    <div id="opcionCambioDirecto" style="display:none;">
        <!-- Búsqueda en inventario local -->
        <!-- Selección inmediata -->
        <!-- Stock se actualiza al guardar -->
    </div>
    
    <!-- Opción B: Solicitar Cambio (Entre Empresas) -->
    <div id="opcionSolicitarCambio" style="display:none;">
        <!-- Búsqueda en inventario del EMISOR -->
        <!-- Justificación obligatoria -->
        <!-- Evidencia opcional -->
        <!-- Crea SOLICITUD al guardar -->
    </div>
    
</div>
```

### 3. **Función: Buscar Productos del Emisor**

**Archivo:** `regularizar_recepciones.html` (línea 643-725)

```javascript
function buscarProductosEmisor() {
    // Busca en inventario de la sucursal EMISORA
    fetch(`/app/dte/buscar_productos_emisor/?query=${query}&sucursal_emisor_id=${window.sucursalEmisorId}`)
    
    // Muestra productos con stock disponible en el emisor
    // Permite seleccionar producto
}

function seleccionarProductoSolicitud(id, sku, nombre, talla, stock, precio) {
    // Guarda selección
    // Muestra card con info del producto seleccionado
    // Indica stock disponible en emisor
}
```

### 4. **Validaciones en Guardar Regularización**

**Archivo:** `regularizar_recepciones.html` (línea 904-946)

```javascript
else if (tipo === 'CAMBIAR_PRODUCTO') {
    if (window.requiereSolicitud) {
        // FLUJO DE SOLICITUD
        const productoSolicitudId = document.getElementById('productoSolicitudId').value;
        const justificacion = document.getElementById('justificacionSolicitud').value.trim();
        
        if (!productoSolicitudId) {
            Swal.fire('Error', 'Debes seleccionar un producto del emisor', 'warning');
            return;
        }
        
        if (!justificacion) {
            Swal.fire('Error', 'La justificación es obligatoria', 'warning');
            return;
        }
        
        data.es_solicitud = true;
        data.nuevo_producto_id = parseInt(productoSolicitudId);
        data.justificacion = justificacion;
        
    } else {
        // FLUJO DIRECTO (código original)
        data.es_solicitud = false;
        // ...
    }
}
```

### 5. **Mensajes Personalizados**

**Archivo:** `regularizar_recepciones.html` (línea 948-1062)

```javascript
// Detecta tipo de operación y muestra mensaje apropiado

if (data.es_solicitud) {
    tituloConfirmacion = '📨 Crear Solicitud';
    mensajeConfirmacion = `
        Se creará una SOLICITUD de cambio de producto.
        El emisor deberá aprobar tu solicitud.
        Una vez aprobada, recibirás:
        - Nota de Crédito por el producto original
        - Nuevo DTE con el producto solicitado
    `;
}

// Respuesta diferenciada
if (data.tipo === 'SOLICITUD_CREADA') {
    titulo = '📨 Solicitud Creada';
    mensaje = `Solicitud #${data.numero_solicitud} creada correctamente.
               Estado: Pendiente de revisión por el emisor`;
}
```

### 6. **Función: Ver Estado de Solicitud**

**Archivo:** `regularizar_recepciones.html` (línea 546-642)

```javascript
function verEstadoSolicitud(productoId) {
    // Obtiene información de la solicitud
    // Muestra estado actual:
    //   - PENDIENTE: "Pendiente de revisión"
    //   - APROBADA: "Aprobada! Espera ejecución"
    //   - RECHAZADA: "Rechazada" + motivo
    //   - EJECUTADA: "DTE #X enviado"
    //   - COMPLETADA: "Completada"
}
```

### 7. **Actualización de Tabla**

**Archivo:** `regularizar_recepciones.html` (línea 527-541)

```javascript
// Columna "Tipo Reg." muestra:
${prod.estado === 'EN_SOLICITUD_REGULARIZACION' ?
    '<span class="badge bg-primary">📨 Solicitud</span>' :
    (prod.requiere_nc ? 
        '<span class="badge bg-danger">NC</span>' : 
        '<span class="badge bg-success">Ajuste</span>')
}

// Columna "Acciones" muestra:
${prod.estado === 'EN_SOLICITUD_REGULARIZACION' ?
    '<button onclick="verEstadoSolicitud()">👁️ Ver</button>' :
    '<button onclick="abrirModalRegularizar()">⚙️ Regularizar</button>'
}
```

### 8. **Filtro Actualizado**

**Archivo:** `regularizar_recepciones.html` (línea 115-126)

```html
<select id="filtroEstado">
    <option value="">Todos</option>
    <option value="FALTANTE">Faltantes</option>
    <option value="RECEPCIONADO_PARCIAL">Parciales</option>
    <option value="RECEPCIONADO_DANADO">Dañados</option>
    <option value="EN_REGULARIZACION">En Regularización</option>
    <option value="EN_SOLICITUD_REGULARIZACION">En Solicitud</option> <!-- NUEVO -->
    <option value="REGULARIZADO">Regularizados</option>
</select>
```

---

## ✅ Backend Completado

### 1. **Endpoint: Buscar Productos del Emisor**

**Archivo:** `views.py` (línea 754-809)

```python
@login_required
@require_GET
def buscar_productos_emisor(request):
    """
    Busca productos en el inventario de la sucursal EMISORA
    """
    query = request.GET.get('query')
    sucursal_emisor_id = request.GET.get('sucursal_emisor_id')
    
    # Busca solo productos con stock > 0 en el emisor
    productos = Producto_Talla.objects.filter(
        sucursal_id=sucursal_emisor_id,
        stock__gt=0
    ).filter(
        Q(sku__icontains=query) |
        Q(producto__articulo__icontains=query) |
        Q(producto__codigoBarra__icontains=query)
    ).select_related('producto')[:20]
    
    return JsonResponse({
        'success': True,
        'productos': [...]
    })
```

### 2. **Endpoint: Obtener Solicitud de Producto**

**Archivo:** `views.py` (línea 698-752)

```python
@login_required
@require_GET
def obtener_solicitud_producto(request, producto_id):
    """Obtiene la solicitud de regularización asociada a un producto"""
    recepcion = get_object_or_404(Productos_Recepcionados, id=producto_id)
    
    solicitud = Solicitud_Regularizacion.objects.filter(
        producto_recepcionado=recepcion
    ).order_by('-fecha_solicitud').first()
    
    return JsonResponse({
        'success': True,
        'solicitud': {
            'numero_solicitud': ...,
            'estado': ...,
            'producto_original_sku': ...,
            'producto_cambio_sku': ...,
            'decision_emisor': ...,
            // etc.
        }
    })
```

### 3. **Actualización: regularizar_producto_api**

**Archivo:** `views.py` (línea 870-1036)

```python
elif tipo_regularizacion == 'CAMBIAR_PRODUCTO':
    nuevo_producto_talla = get_object_or_404(Producto_Talla, id=nuevo_producto_id)
    cantidad = recepcion.stockArribado or recepcion.cantidad_esperada
    
    # ✅ NUEVO: Detectar si es SOLICITUD
    if es_solicitud:
        # CREAR SOLICITUD (entre empresas)
        justificacion = data.get('justificacion', '')
        
        if not justificacion:
            return JsonResponse({
                'success': False,
                'error': 'Debe ingresar una justificación'
            }, status=400)
        
        # Generar número
        numero_solicitud = generar_numero_solicitud()
        
        # Crear solicitud
        solicitud = Solicitud_Regularizacion.objects.create(
            numero_solicitud=numero_solicitud,
            dte_original=recepcion.dte,
            producto_recepcionado=recepcion,
            sucursal_solicitante=recepcion.dte.sucursal,  # Receptor
            sucursal_emisora=recepcion.dte.emisor.sucursales.first(),  # Emisor
            usuario_solicita=usuario,
            tipo_problema=...,  # Auto-detectado
            cantidad_problema=...,
            descripcion_problema=justificacion,
            tipo_solucion_solicitada='CAMBIO_PRODUCTO',
            producto_cambio_solicitado=nuevo_producto_talla,
            cantidad_cambio_solicitada=cantidad,
            estado='PENDIENTE'
        )
        
        # Actualizar estado del producto
        recepcion.estado = 'EN_SOLICITUD_REGULARIZACION'
        recepcion.save()
        
        # Notificar al emisor
        notificar_nueva_solicitud(solicitud)
        
        return JsonResponse({
            'success': True,
            'message': f'Solicitud #{solicitud.numero_solicitud} creada',
            'tipo': 'SOLICITUD_CREADA',
            'numero_solicitud': solicitud.numero_solicitud,
            'requiere_aprobacion': True
        })
    
    else:
        # CAMBIO DIRECTO (traspaso interno - código original)
        nuevo_producto_talla.stock += cantidad
        nuevo_producto_talla.save()
        # ...
```

### 4. **Actualización: obtener_productos_regularizar**

**Archivo:** `views.py` (línea 647-674)

```python
# Agregados campos para solicitudes:
productos.append({
    # ... campos existentes ...
    
    # NUEVO: Información del emisor
    'emisor': emisor_nombre,
    'sucursal_origen': sucursal_origen,
    'sucursal_origen_id': sucursal_origen_id
})
```

### 5. **URLs Agregadas**

**Archivo:** `urls.py` (línea 238-241)

```python
path('dte/obtener_solicitud_producto/<int:producto_id>/', 
     views.obtener_solicitud_producto, 
     name='obtener_solicitud_producto'),
     
path('dte/buscar_productos_emisor/', 
     views.buscar_productos_emisor, 
     name='buscar_productos_emisor'),
```

---

## 🎯 Flujos Implementados

### Flujo A: Traspaso Interno (Misma Empresa)

```
1. Usuario abre modal de regularización
2. Sistema detecta: requiere_nc = false
3. Modal muestra: Opción de cambio DIRECTO
4. Usuario busca producto en inventario local
5. Usuario selecciona producto
6. Usuario presiona "Guardar Regularización"
7. Backend ejecuta cambio inmediatamente:
   ├─ Stock se actualiza
   ├─ Movimiento creado
   └─ Estado: REGULARIZADO
8. ✅ Completado
```

### Flujo B: Traspaso Entre Empresas (Requiere Solicitud)

```
1. Usuario abre modal de regularización
2. Sistema detecta: requiere_nc = true
3. Modal muestra: Opción de SOLICITUD
4. Usuario busca producto en inventario DEL EMISOR
5. Usuario selecciona producto del emisor
6. Usuario escribe justificación (obligatorio)
7. Usuario adjunta foto (opcional)
8. Usuario presiona "Enviar Solicitud"
9. Backend crea Solicitud:
   ├─ Número: SOL-202411-00001
   ├─ Estado: PENDIENTE
   ├─ Producto: EN_SOLICITUD_REGULARIZACION
   └─ Notifica al emisor
10. ⏳ Esperando aprobación del emisor
11. Usuario puede ver estado en cualquier momento
```

---

## 🎨 Cambios Visuales

### Tabla de Productos

**Antes:**
```
| Estado | Tipo Reg. | Acciones |
|--------|-----------|----------|
| ⚠️ Parcial | NC | [Regularizar] |
```

**Ahora:**
```
| Estado | Tipo Reg. | Acciones |
|--------|-----------|----------|
| ⚠️ Parcial | NC | [Regularizar] |
| 🔵 En Solicitud | 📨 Solicitud | [Ver] |
| ✅ Regularizado | Ajuste | [Regularizar] |
```

### Alertas del Modal

**Entre Empresas:**
```
┌───────────────────────────────────────┐
│ ⚠️ Traspaso Entre Empresas            │
│ Emisor: EDEL (Bodega Central)         │
│ • Ajustes → NC automática             │
│ • Cambio producto → SOLICITUD         │
└───────────────────────────────────────┘
```

**Traspaso Interno:**
```
┌───────────────────────────────────────┐
│ ✅ Traspaso Interno                   │
│ Misma empresa. Ajustes directos       │
│ sin generar documentos formales       │
└───────────────────────────────────────┘
```

---

## 📋 Nuevos Estados

### Badge Colors:

- `FALTANTE` → 🔴 Rojo (bg-danger)
- `RECEPCIONADO_PARCIAL` → 🟡 Amarillo (bg-warning)
- `RECEPCIONADO_DANADO` → ⚫ Negro (bg-dark)
- `EN_REGULARIZACION` → 🔵 Info (bg-info)
- `EN_SOLICITUD_REGULARIZACION` → 🔵 Primario (bg-primary) **[NUEVO]**
- `REGULARIZADO` → 🟢 Verde (bg-success)

---

## 🧪 Casos de Prueba

### Caso 1: Cambio de Producto - Traspaso Interno

```
Receptor: NICK1
Emisor: NICK1 (misma empresa)
Problema: Llegaron Nike en lugar de Adidas

1. Ir a /app/regularizar-recepciones/
2. Click en "Regularizar" en el producto
3. Sistema muestra: "✏️ Traspaso Interno"
4. Seleccionar "Cambiar Producto"
5. Buscar "Adidas"
6. Seleccionar Adidas Stan T42
7. Guardar
8. ✅ Stock se actualiza inmediatamente
   - Estado: REGULARIZADO
```

### Caso 2: Cambio de Producto - Entre Empresas

```
Receptor: NICK1 (Empresa A)
Emisor: EDEL (Empresa B)
Problema: Llegaron Nike en lugar de Adidas

1. Ir a /app/regularizar-recepciones/
2. Click en "Regularizar" en el producto
3. Sistema muestra: "🏢 Traspaso Entre Empresas"
4. Seleccionar "Cambiar Producto"
5. Sistema muestra: "📨 Solicitar Cambio de Producto"
6. Buscar productos EN EL INVENTARIO DE EDEL
7. Seleccionar Adidas Stan T42 (stock EDEL: 15)
8. Escribir justificación: "Tengo más demanda de Adidas"
9. Adjuntar foto de caja dañada
10. Enviar solicitud
11. ✅ Solicitud #SOL-202411-00001 creada
    - Estado producto: EN_SOLICITUD_REGULARIZACION
    - Emisor notificado
    - Badge azul "📨 Solicitud" en tabla
12. Click en botón "Ver" → Muestra estado de solicitud
```

---

## 📊 Archivos Modificados

### Frontend:
- ✅ `regularizar_recepciones.html` 
  - Nuevas funciones: `buscarProductosEmisor()`, `seleccionarProductoSolicitud()`, `limpiarSeleccionSolicitud()`, `verEstadoSolicitud()`
  - Actualizada: `abrirModalRegularizar()`, `mostrarPanelRegularizacion()`, `guardarRegularizacion()`, `obtenerClaseEstado()`
  - HTML modificado: Panel dual para cambio de producto
  - Filtro actualizado: Nuevo estado EN_SOLICITUD_REGULARIZACION

### Backend:
- ✅ `views.py`
  - Nueva función: `buscar_productos_emisor()` (57 líneas)
  - Nueva función: `obtener_solicitud_producto()` (55 líneas)
  - Actualizada: `regularizar_producto_api()` (nueva lógica para solicitudes)
  - Actualizada: `obtener_productos_regularizar()` (agrega campos de emisor)

### URLs:
- ✅ `urls.py`
  - Nueva ruta: `/dte/obtener_solicitud_producto/<id>/`
  - Nueva ruta: `/dte/buscar_productos_emisor/`

---

## 📈 Estadísticas

- **Líneas de código agregadas:** ~400 líneas
- **Nuevas funciones JS:** 4 funciones
- **Nuevos endpoints:** 2 endpoints
- **Funciones actualizadas:** 4 funciones
- **Estados nuevos:** 1 estado
- **Validaciones agregadas:** 2 validaciones

---

## ✅ Checklist Fase 2

- [x] Detección automática de tipo de traspaso
- [x] UI dual para cambio de producto
- [x] Búsqueda de productos en inventario del emisor
- [x] Selección de producto con validaciones
- [x] Justificación obligatoria para solicitudes
- [x] Evidencia opcional (foto)
- [x] Creación de solicitud en backend
- [x] Endpoint buscar productos emisor
- [x] Endpoint obtener solicitud
- [x] Actualización de regularizar_producto_api
- [x] Mensajes personalizados
- [x] Función ver estado de solicitud
- [x] Badge y botón diferenciado en tabla
- [x] Filtro de estado actualizado
- [x] Notificación al emisor (placeholder)

---

## 🎯 Próximos Pasos

### Fase 3: Panel del Emisor (2 días)

Crear la vista donde el EMISOR ve y aprueba las solicitudes recibidas.

Componentes a crear:
1. Vista `/app/solicitudes-regularizacion/`
2. Tabla de solicitudes recibidas
3. Modal de revisión con opciones:
   - Aprobar
   - Rechazar
   - Modificar solución
4. Endpoint `aprobar_solicitud_api`

### Fase 4: Ejecución de Soluciones (2 días)

Implementar la ejecución automática de documentos cuando el emisor aprueba.

---

**Fecha de Completación:** 3 Nov 2024  
**Tiempo estimado:** 2 días  
**Estado:** ✅ COMPLETADA

**Progreso Total:** [████████████░░░░░░░] 60%

