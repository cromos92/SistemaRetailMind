# PLAN DE IMPLEMENTACIÓN: EDICIÓN DE PRODUCTOS Y GESTIÓN DE STOCK

## 📋 RESUMEN EJECUTIVO

Implementar funcionalidad completa de edición para productos y sus variaciones (tallas), incluyendo:
- Edición de datos del producto base
- Edición de variaciones/tallas (precios, SKU, estado)
- Ajuste de stock con registro automático en `Movimientos_Producto`
- Control de lotes FIFO existente
- Validaciones y auditoría completa

---

## 🔍 ANÁLISIS DEL SISTEMA ACTUAL

### Modelos Existentes

#### 1. **Producto** (modelo principal)
```python
- articulo (CharField)
- descripcion (CharField)
- atributo1, atributo2, atributo3, atributo4 (FK a AtributoOpcion)
- categoria (FK a Categoria)
- sucursal (FK a Sucursal)
- costo, sobreprecio, precioventa (IntegerField)
- precioSugerido (IntegerField)
- tipo_talla (CharField)
- guia_talla (FK a GuiaTalla)
```

#### 2. **Producto_Talla** (variaciones)
```python
- producto (FK a Producto)
- sku (IntegerField)
- stock (IntegerField)
- talla (CharField)
```

#### 3. **Movimientos_Producto** (registro de movimientos de stock)
```python
- dte (FK opcional)
- ticket (FK opcional)
- ProductoTalla (FK a Producto_Talla)
- sucursal_origen, sucursal_destino (FK a Sucursal)
- cantidad (IntegerField) - positivo para ingresos, negativo para egresos
- costo, sobreprecio, precio (IntegerField)
- concepto (CharField) - tipo de movimiento
- responsable (FK a User)
- observaciones (TextField)
- fecha_hora (DateTimeField)
- estado (CharField)
```

#### 4. **LoteProducto** (sistema FIFO)
```python
- producto_talla (FK a Producto_Talla)
- numero_lote (CharField)
- cantidad_inicial, cantidad_disponible (IntegerField)
- costo_unitario, sobreprecio_unitario, precio_venta_unitario (DecimalField)
- fecha_vencimiento (DateField)
- dte_origen, movimiento_origen (FK opcionales)
- observaciones (TextField)
- activo (BooleanField)
```

### Funciones Existentes Relevantes

1. **`registrar_movimiento_producto()`** - Función centralizada para registrar movimientos
2. **`crear_lote_producto()`** - Crear lotes FIFO
3. **`consumir_stock_fifo()`** - Consumir stock usando FIFO
4. **`obtener_valor_inventario_fifo()`** - Obtener valor del inventario
5. **`obtener_costo_promedio_fifo()`** - Costo promedio ponderado

---

## 🎯 OBJETIVOS DEL PLAN

### Funcionalidades a Implementar

1. **Edición de Producto Base**
   - Nombre (articulo)
   - Descripción
   - Categoría
   - Atributos (marca, color, género, otro)
   - Precios base (costo, sobreprecio, precio venta)

2. **Edición de Variaciones/Tallas**
   - SKU
   - Precio de venta específico
   - Estado (activo/inactivo)
   - NO editar talla directamente (solo crear/eliminar)

3. **Ajuste de Stock**
   - Incremento de stock → Crea lote FIFO + Movimiento "AJUSTE_ENTRADA"
   - Decremento de stock → Consume FIFO + Movimiento "AJUSTE_SALIDA"
   - Motivo obligatorio
   - Validación de stock disponible
   - Usuario responsable

4. **Auditoría y Trazabilidad**
   - Todos los cambios de stock registrados en `Movimientos_Producto`
   - Historial de cambios visible
   - Usuario responsable de cada cambio

---

## 🏗️ ARQUITECTURA DE LA SOLUCIÓN

### Backend (Django Views)

#### 1. Obtener Producto para Edición
```
GET /app/productos/obtener_para_editar/<producto_id>/

Retorna:
{
  "success": true,
  "producto": {
    "id": 123,
    "articulo": "Zapatilla Nike Air Max",
    "descripcion": "Zapatilla deportiva",
    "categoria_id": 5,
    "categoria_nombre": "Calzado",
    "atributo1_id": 10,  // Marca
    "atributo1_nombre": "Nike",
    "atributo2_id": 25,  // Color
    "atributo2_nombre": "Negro",
    ...
    "costo": 50000,
    "sobreprecio": 10000,
    "precioventa": 70000,
    "precioSugerido": 75000,
    "sucursal_id": 1,
    "guia_talla_id": 3,
    "tipo_talla": "CL"
  },
  "variaciones": [
    {
      "id": 456,
      "sku": 10001,
      "talla": "38",
      "stock_total": 15,
      "stock_sucursal": 10,
      "precio_venta": 70000,
      "activo": true,
      "lotes": [
        {
          "numero_lote": "LOTE-ABC123",
          "cantidad_disponible": 15,
          "costo_unitario": 50000,
          "fecha_creacion": "2024-01-15"
        }
      ]
    }
  ]
}
```

#### 2. Actualizar Producto Base
```
PUT /app/productos/actualizar_producto/<producto_id>/

Recibe:
{
  "articulo": "Nuevo nombre",
  "descripcion": "Nueva descripción",
  "categoria_id": 5,
  "atributo1_id": 10,
  "atributo2_id": 25,
  "atributo3_id": 30,
  "atributo4_id": null,
  "costo": 55000,
  "sobreprecio": 12000,
  "precioventa": 75000,
  "precioSugerido": 80000
}

Retorna:
{
  "success": true,
  "message": "Producto actualizado exitosamente"
}
```

#### 3. Actualizar Variación/Talla
```
PUT /app/productos/actualizar_variacion/<variacion_id>/

Recibe:
{
  "sku": 10001,
  "precio_venta": 72000,
  "activo": true
}

Retorna:
{
  "success": true,
  "message": "Variación actualizada exitosamente"
}
```

#### 4. Ajustar Stock de Variación
```
POST /app/productos/ajustar_stock/<variacion_id>/

Recibe:
{
  "tipo_ajuste": "ENTRADA" | "SALIDA",
  "cantidad": 10,
  "motivo": "Recuento de inventario",
  "costo_unitario": 50000,  // Solo para ENTRADA
  "sobreprecio_unitario": 10000,  // Solo para ENTRADA
  "precio_venta_unitario": 70000,  // Solo para ENTRADA
  "numero_lote": "LOTE-XYZ789"  // Opcional para ENTRADA
}

Retorna:
{
  "success": true,
  "message": "Stock ajustado exitosamente",
  "nuevo_stock": 25,
  "movimiento_id": 789,
  "lote_id": 101  // Si es entrada
}

Lógica:
- Si tipo_ajuste = "ENTRADA":
  1. Crear nuevo lote FIFO con la cantidad y costos especificados
  2. Registrar Movimiento_Producto con concepto "AJUSTE_ENTRADA"
  3. Actualizar stock total de Producto_Talla

- Si tipo_ajuste = "SALIDA":
  1. Validar que haya stock suficiente
  2. Consumir stock usando FIFO (consumir_stock_fifo)
  3. Registrar Movimiento_Producto con concepto "AJUSTE_SALIDA"
  4. Actualizar stock total de Producto_Talla
```

#### 5. Obtener Historial de Movimientos
```
GET /app/productos/historial_movimientos/<variacion_id>/

Parámetros:
- fecha_inicio (opcional)
- fecha_fin (opcional)
- limit (default: 50)

Retorna:
{
  "success": true,
  "movimientos": [
    {
      "id": 789,
      "fecha_hora": "2024-01-15 10:30:00",
      "concepto": "AJUSTE_ENTRADA",
      "cantidad": 10,
      "responsable": "Juan Pérez",
      "observaciones": "Recuento de inventario",
      "stock_resultante": 25
    }
  ]
}
```

### Frontend (JavaScript + HTML)

#### Estructura de la Interfaz

```
┌─────────────────────────────────────────────────┐
│ GESTIÓN DE PRODUCTOS                            │
├─────────────────────────────────────────────────┤
│ [Búsqueda] [Filtros] [+ Crear Producto]        │
├─────────────────────────────────────────────────┤
│ Tabla de Productos                              │
│ ┌───┬────────┬────────┬────────┬──────────┐    │
│ │ID │Nombre  │Stock   │Precio  │Acciones  │    │
│ ├───┼────────┼────────┼────────┼──────────┤    │
│ │123│Nike Air│45 unid │$70.000 │[✏️][📊] │    │
│ └───┴────────┴────────┴────────┴──────────┘    │
└─────────────────────────────────────────────────┘

Acciones:
- ✏️ Editar Producto → Abre Modal de Edición
- 📊 Ver Detalle → Abre vista detallada con historial
```

#### Modal de Edición de Producto

```html
┌─────────────────────────────────────────────────┐
│ EDITAR PRODUCTO: Zapatilla Nike Air Max    [✕]  │
├─────────────────────────────────────────────────┤
│ DATOS GENERALES                                 │
│ ┌─────────────────────────────────────────────┐ │
│ │ Nombre: [Zapatilla Nike Air Max          ] │ │
│ │ Descripción: [Zapatilla deportiva...     ] │ │
│ │ Categoría: [Calzado ▼]                     │ │
│ │ Marca: [Nike ▼]                            │ │
│ │ Color: [Negro ▼]                           │ │
│ │ Género: [Unisex ▼]                         │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ PRECIOS BASE                                     │
│ ┌─────────────────────────────────────────────┐ │
│ │ Costo: [$50.000] Sobreprecio: [$10.000]   │ │
│ │ Precio Venta: [$70.000]                    │ │
│ │ Precio Sugerido: [$75.000]                 │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ VARIACIONES / TALLAS                             │
│ ┌─────────────────────────────────────────────┐ │
│ │ Talla │ SKU   │ Stock │ Precio │ Estado     │ │
│ ├───────┼───────┼───────┼────────┼──────────┤ │
│ │ 38    │10001  │ 15    │$70.000 │✅[✏️][📦]│ │
│ │ 39    │10002  │ 8     │$70.000 │✅[✏️][📦]│ │
│ │ 40    │10003  │ 0     │$70.000 │❌[✏️][📦]│ │
│ └───────┴───────┴───────┴────────┴──────────┘ │
│                                                  │
│ Acciones por variación:                          │
│ - ✏️ Editar SKU/Precio/Estado                   │
│ - 📦 Ajustar Stock                              │
│                                                  │
│ [Cancelar] [Guardar Cambios]                   │
└─────────────────────────────────────────────────┘
```

#### Modal de Ajuste de Stock

```html
┌─────────────────────────────────────────────────┐
│ AJUSTAR STOCK: Nike Air Max - Talla 38    [✕]  │
├─────────────────────────────────────────────────┤
│ Stock Actual: 15 unidades                       │
│                                                  │
│ Tipo de Ajuste:                                 │
│ ○ Entrada (Incrementar stock)                   │
│ ● Salida (Decrementar stock)                    │
│                                                  │
│ Cantidad: [____]                                │
│                                                  │
│ ┌─────────────────────────────────────────────┐ │
│ │ ⚠️ Si es ENTRADA, complete:                  │ │
│ │ Costo Unitario: [$50.000]                   │ │
│ │ Sobreprecio: [$10.000]                      │ │
│ │ Precio Venta: [$70.000]                     │ │
│ │ Nº Lote: [LOTE-XYZ789] (opcional)           │ │
│ └─────────────────────────────────────────────┘ │
│                                                  │
│ Motivo: [________________________________]       │
│                                                  │
│ Stock Resultante: 13 unidades                   │
│                                                  │
│ [Cancelar] [Confirmar Ajuste]                  │
└─────────────────────────────────────────────────┘
```

---

## 📝 VALIDACIONES Y REGLAS DE NEGOCIO

### Validaciones Backend

1. **Edición de Producto Base**
   - ✅ Nombre no puede estar vacío
   - ✅ Costo >= 0
   - ✅ Sobreprecio >= 0
   - ✅ Precio venta > 0
   - ✅ Categoría debe existir
   - ✅ Atributos deben existir si se proporcionan

2. **Edición de Variación**
   - ✅ SKU debe ser único
   - ✅ Precio venta > 0
   - ✅ No se puede modificar talla (solo crear nueva variación)

3. **Ajuste de Stock**
   - ✅ Cantidad > 0
   - ✅ Para SALIDA: Cantidad <= Stock disponible
   - ✅ Para ENTRADA: Debe proporcionar costos
   - ✅ Motivo es obligatorio (min 10 caracteres)
   - ✅ Usuario debe tener permisos

### Validaciones Frontend

1. **Campos requeridos marcados con asterisco**
2. **Validación en tiempo real de números**
3. **Confirmación para cambios críticos (desactivar producto)**
4. **Mostrar stock resultante antes de confirmar ajuste**

---

## 🔐 PERMISOS Y SEGURIDAD

### Niveles de Permiso

1. **Ver Productos** - Todos los usuarios autenticados
2. **Editar Producto Base** - Gerentes y Administradores
3. **Editar Variaciones** - Gerentes y Administradores
4. **Ajustar Stock** - Gerentes, Administradores y usuarios con permiso especial
5. **Ver Historial** - Todos los usuarios autenticados

### Decoradores Django

```python
@login_required
@permission_required('app.change_producto')
def actualizar_producto(request, producto_id):
    ...

@login_required
@permission_required('app.change_producto_talla')
def actualizar_variacion(request, variacion_id):
    ...

@login_required
@permission_required('app.add_movimientos_producto')
def ajustar_stock(request, variacion_id):
    ...
```

---

## 🗄️ ESTRUCTURA DE BASE DE DATOS

### Cambios Necesarios

**NO SE REQUIEREN MIGRACIONES** - Los modelos actuales ya soportan todas las funcionalidades:
- ✅ `Producto` - tiene todos los campos necesarios
- ✅ `Producto_Talla` - tiene SKU, stock, talla
- ✅ `Movimientos_Producto` - registra todos los movimientos
- ✅ `LoteProducto` - maneja FIFO

### Consideraciones

1. **Campo `stock` en `Producto_Talla`**
   - Actualmente existe pero puede estar desincronizado
   - Debe calcularse dinámicamente sumando lotes FIFO
   - O mantenerlo actualizado después de cada movimiento

2. **Sistema FIFO ya implementado**
   - Usar funciones existentes: `crear_lote_producto()`, `consumir_stock_fifo()`
   - No reinventar la rueda

---

## 📊 FLUJO DE DATOS

### Flujo: Ajuste de Stock (ENTRADA)

```
Usuario → Frontend → Backend
                      ↓
                   Validar datos
                      ↓
                   Crear LoteProducto
                      ↓
                   Crear Movimientos_Producto
                      (concepto: AJUSTE_ENTRADA)
                      ↓
                   Actualizar stock en Producto_Talla
                      ↓
                   Retornar confirmación
                      ↓
Frontend ← Backend (mostrar mensaje éxito)
```

### Flujo: Ajuste de Stock (SALIDA)

```
Usuario → Frontend → Backend
                      ↓
                   Validar datos
                      ↓
                   Validar stock disponible
                      ↓
                   Consumir stock FIFO
                   (consumir_stock_fifo)
                      ↓
                   Crear Movimientos_Producto
                   (concepto: AJUSTE_SALIDA)
                      ↓
                   Actualizar stock en Producto_Talla
                      ↓
                   Retornar confirmación
                      ↓
Frontend ← Backend (mostrar mensaje éxito)
```

---

## 🧪 CASOS DE PRUEBA

### Caso 1: Editar Nombre de Producto
```
DADO que soy un gerente autenticado
CUANDO edito el nombre de un producto
ENTONCES el nombre se actualiza correctamente
Y se muestra un mensaje de confirmación
```

### Caso 2: Ajustar Stock (Entrada)
```
DADO que tengo un producto con 10 unidades
CUANDO agrego 5 unidades con ajuste de entrada
ENTONCES se crea un nuevo lote FIFO con 5 unidades
Y se registra un movimiento de tipo AJUSTE_ENTRADA
Y el stock total es 15
```

### Caso 3: Ajustar Stock (Salida)
```
DADO que tengo un producto con 10 unidades
CUANDO resto 3 unidades con ajuste de salida
ENTONCES se consume stock del lote más antiguo (FIFO)
Y se registra un movimiento de tipo AJUSTE_SALIDA
Y el stock total es 7
```

### Caso 4: Validación de Stock Insuficiente
```
DADO que tengo un producto con 5 unidades
CUANDO intento restar 10 unidades
ENTONCES se muestra un error "Stock insuficiente"
Y NO se realiza ningún movimiento
```

### Caso 5: Cambiar Precio de Variación
```
DADO que tengo una variación con precio $70.000
CUANDO cambio el precio a $75.000
ENTONCES el precio se actualiza
Y las nuevas ventas usarán el nuevo precio
Y los lotes antiguos mantienen su precio original
```

---

## 📦 ARCHIVOS A CREAR/MODIFICAR

### Nuevos Archivos Backend

1. **`retailmind/app/views_edicion_productos.py`**
   - `obtener_producto_edicion(request, producto_id)`
   - `actualizar_producto(request, producto_id)`
   - `actualizar_variacion(request, variacion_id)`
   - `ajustar_stock(request, variacion_id)`
   - `obtener_historial_movimientos(request, variacion_id)`

### Modificar Archivos Backend

2. **`retailmind/app/urls.py`**
   - Agregar rutas para las nuevas vistas

3. **`retailmind/app/views_modulo_productos.py`**
   - Agregar botón "Editar" en listado de productos

### Nuevos Archivos Frontend

4. **`retailmind/app/static/js/edicion_productos.js`**
   - Funciones para modal de edición
   - Funciones para ajuste de stock
   - Validaciones frontend
   - Manejo de AJAX

5. **`retailmind/app/templates/vistas/modulo_existencias/modal_editar_producto.html`**
   - Estructura del modal de edición

6. **`retailmind/app/templates/vistas/modulo_existencias/modal_ajustar_stock.html`**
   - Estructura del modal de ajuste de stock

### Modificar Archivos Frontend

7. **`retailmind/app/templates/vistas/modulo_existencias/verGestionProductos.html`**
   - Agregar botones de edición
   - Incluir modales
   - Incluir script JS

---

## 🚀 PLAN DE IMPLEMENTACIÓN PASO A PASO

### FASE 1: Backend - Vistas Base (2-3 horas)
✅ **Tarea 1.1**: Crear `views_edicion_productos.py`
✅ **Tarea 1.2**: Implementar `obtener_producto_edicion()`
✅ **Tarea 1.3**: Implementar `actualizar_producto()`
✅ **Tarea 1.4**: Implementar `actualizar_variacion()`
✅ **Tarea 1.5**: Registrar URLs

### FASE 2: Backend - Ajuste de Stock (3-4 horas)
✅ **Tarea 2.1**: Implementar `ajustar_stock()` para ENTRADA
✅ **Tarea 2.2**: Implementar `ajustar_stock()` para SALIDA
✅ **Tarea 2.3**: Implementar validaciones
✅ **Tarea 2.4**: Implementar `obtener_historial_movimientos()`
✅ **Tarea 2.5**: Pruebas unitarias backend

### FASE 3: Frontend - Modal de Edición (3-4 horas)
✅ **Tarea 3.1**: Crear estructura HTML del modal
✅ **Tarea 3.2**: Implementar carga de datos en modal
✅ **Tarea 3.3**: Implementar guardado de producto base
✅ **Tarea 3.4**: Implementar edición de variaciones
✅ **Tarea 3.5**: Agregar validaciones frontend

### FASE 4: Frontend - Ajuste de Stock (2-3 horas)
✅ **Tarea 4.1**: Crear modal de ajuste de stock
✅ **Tarea 4.2**: Implementar lógica de entrada/salida
✅ **Tarea 4.3**: Calcular stock resultante en tiempo real
✅ **Tarea 4.4**: Validaciones y confirmaciones

### FASE 5: Integración y Pruebas (2-3 horas)
✅ **Tarea 5.1**: Integrar con verGestionProductos.html
✅ **Tarea 5.2**: Pruebas de flujo completo
✅ **Tarea 5.3**: Pruebas de validaciones
✅ **Tarea 5.4**: Pruebas de permisos

### FASE 6: Documentación y Refinamiento (1-2 horas)
✅ **Tarea 6.1**: Documentar código
✅ **Tarea 6.2**: Crear guía de usuario
✅ **Tarea 6.3**: Ajustes visuales
✅ **Tarea 6.4**: Optimizaciones de rendimiento

**TOTAL ESTIMADO: 13-19 horas**

---

## 📚 CONSIDERACIONES ADICIONALES

### 1. Manejo de Concurrencia
- Usar `@transaction.atomic` en todas las vistas de modificación
- Validar stock disponible dentro de la transacción
- Bloqueo pesimista si es necesario: `select_for_update()`

### 2. Optimización de Rendimiento
- Usar `select_related()` y `prefetch_related()` en queries
- Cachear listas de categorías y atributos
- Paginar historial de movimientos
- Índices en campos de búsqueda frecuente

### 3. Auditoría Avanzada (Opcional - Futuro)
- Crear modelo `HistorialCambiosProducto` para auditoría detallada
- Registrar cambios de precios
- Registrar activación/desactivación
- Tracking de quién modificó qué y cuándo

### 4. Notificaciones (Opcional - Futuro)
- Notificar a gerentes cuando stock < umbral
- Notificar cambios masivos de precios
- Alertas de stock negativo o inconsistencias

### 5. Exportación de Datos
- Exportar historial de movimientos a Excel
- Exportar lista de productos editados
- Reportes de ajustes de stock

---

## 🎨 WIREFRAMES Y MOCKUPS

### Vista Principal con Botón Editar

```
┌─────────────────────────────────────────────────────────────┐
│ Gestión de Productos                                        │
├─────────────────────────────────────────────────────────────┤
│ [🔍 Buscar...] [Categoría ▼] [Marca ▼] [+ Nuevo Producto]  │
├─────────────────────────────────────────────────────────────┤
│ ID │ Código │ Nombre         │ Stock │ Precio │ Acciones   │
├────┼────────┼────────────────┼───────┼────────┼────────────┤
│123 │ P-0123 │ Nike Air Max   │ 45    │ 70.000 │ ✏️ 📊 🗑️  │
│124 │ P-0124 │ Adidas Ultra   │ 30    │ 85.000 │ ✏️ 📊 🗑️  │
│125 │ P-0125 │ Puma Suede     │  0    │ 60.000 │ ✏️ 📊 🗑️  │
└────┴────────┴────────────────┴───────┴────────┴────────────┘

Leyenda:
✏️ = Editar producto
📊 = Ver detalle y estadísticas
🗑️ = Eliminar/Desactivar
```

### Tab de Variaciones en Modal

```
┌─────────────────────────────────────────────────┐
│ [ Datos Generales ] [ Variaciones/Tallas ]      │
├─────────────────────────────────────────────────┤
│                                                  │
│ Talla │ SKU    │ Stock │ Precio  │ Estado      │
│ ──────┼────────┼───────┼─────────┼─────────    │
│ 38    │ 10001  │  15   │ 70.000  │ ✅ ✏️ 📦   │
│       │        │       │         │ [Editar]    │
│       │        │       │         │ [Ajustar]   │
│ ──────┼────────┼───────┼─────────┼─────────    │
│ 39    │ 10002  │   8   │ 70.000  │ ✅ ✏️ 📦   │
│ ──────┼────────┼───────┼─────────┼─────────    │
│ 40    │ 10003  │   0   │ 70.000  │ ❌ ✏️ 📦   │
│                                                  │
│ [+ Agregar nueva talla]                         │
└─────────────────────────────────────────────────┘
```

---

## ✅ CHECKLIST FINAL

### Backend
- [ ] Vista para obtener producto completo
- [ ] Vista para actualizar producto base
- [ ] Vista para actualizar variación
- [ ] Vista para ajustar stock (entrada)
- [ ] Vista para ajustar stock (salida)
- [ ] Vista para historial de movimientos
- [ ] Validaciones de datos
- [ ] Permisos y seguridad
- [ ] Manejo de errores
- [ ] Tests unitarios
- [ ] URLs registradas

### Frontend
- [ ] Modal de edición de producto
- [ ] Formulario de datos generales
- [ ] Tabla de variaciones editable
- [ ] Modal de ajuste de stock
- [ ] Validaciones frontend
- [ ] Feedback visual (loading, success, error)
- [ ] Confirmaciones para acciones críticas
- [ ] Responsive design
- [ ] Accesibilidad (ARIA labels)
- [ ] Integración con página principal

### Integración
- [ ] Botones de edición en tabla principal
- [ ] AJAX funcionando correctamente
- [ ] Actualización automática después de guardar
- [ ] Manejo de errores del servidor
- [ ] Sincronización de datos
- [ ] Pruebas de flujo completo

### Documentación
- [ ] Comentarios en código backend
- [ ] Comentarios en código frontend
- [ ] Guía de usuario
- [ ] Documentación técnica
- [ ] Casos de prueba documentados

---

## 🔗 REFERENCIAS

### Código Existente Relacionado
- `retailmind/app/views_modulo_productos.py` - Vistas actuales de productos
- `retailmind/app/views.py` - Función `registrar_movimiento_producto()`
- `retailmind/app/models.py` - Modelos de datos
- `retailmind/app/templates/vistas/modulo_existencias/verGestionProductos.html` - Template actual

### Conceptos Clave
- **FIFO** (First In, First Out) - Sistema de gestión de inventario
- **Transacciones Atómicas** - Garantizar consistencia de datos
- **Select for Update** - Bloqueo pesimista para concurrencia
- **Prefetch Related** - Optimización de queries

---

## 📞 CONTACTO Y SOPORTE

Para dudas sobre la implementación:
1. Revisar este documento
2. Revisar código existente en `views_modulo_productos.py`
3. Consultar documentación de Django: https://docs.djangoproject.com/
4. Revisar sistema FIFO implementado en líneas 706-836 de `views_modulo_productos.py`

---

**Última actualización:** 2024-11-06  
**Versión del documento:** 1.0  
**Estado:** Listo para implementación

