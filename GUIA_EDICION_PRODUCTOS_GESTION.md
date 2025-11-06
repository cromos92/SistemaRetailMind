# 📝 GUÍA: Edición de Productos desde Gestión de Productos

## 🎯 Acceso Rápido

### Ubicación
**Página**: Gestión de Productos  
**URL**: `http://localhost:8000/app/verGestionProducto/`

### Botones Disponibles
- 🔵 **Crear Producto Manual**: Crear nuevos productos
- 🟡 **Edición Productos**: Buscar y editar productos existentes ← **NUEVO**

---

## 🔍 Cómo Usar la Edición de Productos

### Paso 1: Abrir el Modal de Búsqueda

1. En la página de Gestión de Productos
2. Haga clic en el botón amarillo **"Edición Productos"**
3. Se abrirá el modal de búsqueda

### Paso 2: Buscar Productos

#### Opción A: Búsqueda por Texto
```
1. Escriba en el buscador:
   - Nombre del producto
   - Código
   - SKU
   - Marca
   
2. Haga clic en "Buscar" o presione Enter
3. Verá los resultados en la tabla
```

#### Opción B: Filtros Rápidos
```
Haga clic en uno de los botones:
- 📋 Todos: Muestra todos los productos
- ✅ Con Stock: Solo productos con stock > 0
- ⚠️ Sin Stock: Solo productos sin stock
- 🔋 Stock Bajo: Solo productos con stock < 5
```

### Paso 3: Editar un Producto

1. Localice el producto en la tabla de resultados
2. Haga clic en el botón **"Editar"** (botón amarillo)
3. Se abrirá el modal de edición completo

### Paso 4: Modificar Datos

#### Pestaña "Datos Generales"
- ✏️ Nombre del producto
- ✏️ Descripción
- ✏️ Categoría
- ✏️ Atributos (Marca, Color, Género, Otro)
- ✏️ Precios (Costo, Sobreprecio, Precio Venta, Precio Sugerido)

#### Pestaña "Variaciones / Tallas"
- 📦 Ver stock por talla
- 📦 Ajustar stock (ENTRADA/SALIDA)
- 🕐 Ver historial de movimientos
- 📑 Ver lotes FIFO

### Paso 5: Guardar Cambios

1. Revise todos los cambios realizados
2. Haga clic en **"Guardar Cambios"**
3. Recibirá confirmación de éxito
4. Los cambios se aplicarán inmediatamente

---

## 📊 Información Mostrada en Búsqueda

La tabla de resultados muestra:

| Columna | Descripción |
|---------|-------------|
| **Código** | Código del producto |
| **Nombre** | Nombre descriptivo |
| **Categoría** | Categoría asignada |
| **Marca** | Marca del producto |
| **Stock Total** | Stock total en todas las sucursales |
| **Tallas** | Cantidad de tallas/variaciones |
| **Estado** | Activo o Inactivo |
| **Acciones** | Botón Editar |

### Colores de Stock
- 🟢 **Verde**: Stock normal (≥ 5 unidades)
- 🟡 **Amarillo**: Stock bajo (< 5 unidades)
- 🔴 **Rojo**: Sin stock (0 unidades)

---

## ✨ Funcionalidades Disponibles

### En el Modal de Búsqueda

✅ **Buscar por múltiples criterios**
- Nombre
- Código
- SKU  
- Marca

✅ **Filtros rápidos**
- Todos los productos
- Con stock
- Sin stock
- Stock bajo

✅ **Resultados en tiempo real**
- Tabla interactiva
- Ordenable
- Scroll horizontal si es necesario

### En el Modal de Edición

✅ **Edición completa de producto**
- Todos los atributos
- Todos los precios
- Categorización

✅ **Gestión de variaciones**
- Ver todas las tallas
- Ver stock por talla
- Ajustar stock individual

✅ **Ajuste de stock con FIFO**
- Entrada: Crea lote automático
- Salida: Consume FIFO
- Registro de movimientos

✅ **Historial completo**
- Todos los movimientos
- Usuario responsable
- Fecha y hora
- Motivos

---

## 💡 Casos de Uso Comunes

### Caso 1: Cambiar Precio de un Producto

```
1. Clic en "Edición Productos"
2. Buscar: "Zapatilla Nike"
3. Clic en "Editar"
4. Pestaña "Datos Generales"
5. Modificar "Precio Venta": 75000
6. Guardar Cambios
```

### Caso 2: Ajustar Stock de una Talla

```
1. Clic en "Edición Productos"
2. Buscar el producto
3. Clic en "Editar"
4. Pestaña "Variaciones / Tallas"
5. Localizar talla (ej: "40")
6. Clic en "Ajustar Stock"
7. Seleccionar ENTRADA o SALIDA
8. Completar formulario
9. Confirmar Ajuste
```

### Caso 3: Ver Productos Sin Stock

```
1. Clic en "Edición Productos"
2. Clic en botón "Sin Stock" (filtro rápido)
3. Ver lista de productos sin stock
4. Editar uno por uno según necesidad
```

### Caso 4: Cambiar Categoría de un Producto

```
1. Clic en "Edición Productos"
2. Buscar el producto
3. Clic en "Editar"
4. Pestaña "Datos Generales"
5. Cambiar "Categoría"
6. Guardar Cambios
```

---

## ⚠️ Validaciones y Restricciones

### En la Búsqueda
- ⚠️ Debe ingresar un término o usar un filtro
- ⚠️ Máximo 50 resultados por búsqueda

### En la Edición
- ⚠️ Nombre no puede estar vacío
- ⚠️ Precio de venta debe ser > 0
- ⚠️ Para ajustes de salida: Stock disponible debe ser suficiente
- ⚠️ Motivo de ajuste: Mínimo 10 caracteres

---

## 🔄 Flujo Visual

```
┌──────────────────────────────────────────────────┐
│ Gestión de Productos                             │
│ [Crear Producto Manual] [Edición Productos] ←   │
└────────────────┬─────────────────────────────────┘
                 ▼
┌──────────────────────────────────────────────────┐
│ Modal: Búsqueda de Productos                     │
│ [Buscador: ________________] [Buscar]            │
│ [Todos] [Con Stock] [Sin Stock] [Stock Bajo]    │
│                                                   │
│ Tabla de Resultados:                             │
│ ┌─────┬──────┬──────┬──────┬──────┬────────┐   │
│ │Código│Nombre│Stock │...   │Acciones       │   │
│ ├─────┼──────┼──────┼──────┼──────────────┤    │
│ │P-001│Nike  │ 45   │...   │[Editar]      │    │
│ └─────┴──────┴──────┴──────┴──────────────┘    │
└────────────────┬─────────────────────────────────┘
                 ▼ Clic en Editar
┌──────────────────────────────────────────────────┐
│ Modal: Editar Producto                           │
│ Tab: [Datos Generales] [Variaciones/Tallas]     │
│                                                   │
│ → Modificar todos los atributos                  │
│ → Ajustar stock por talla                        │
│ → Ver historial de movimientos                   │
│                                                   │
│ [Cancelar] [Guardar Cambios]                    │
└──────────────────────────────────────────────────┘
```

---

## 🎨 Capturas de Pantalla Conceptuales

### Botón en la Página Principal
```
┌─────────────────────────────────────────────┐
│ Gestión de Productos                        │
├─────────────────────────────────────────────┤
│                                             │
│  [🔵 Crear Producto Manual]                │
│  [🟡 Edición Productos]  ← NUEVO           │
│                                             │
└─────────────────────────────────────────────┘
```

### Modal de Búsqueda
```
╔═════════════════════════════════════════════╗
║ 🔍 Buscar y Editar Productos          [✕]  ║
╠═════════════════════════════════════════════╣
║                                             ║
║  🔍 [____________________] [Buscar] [⨯]    ║
║                                             ║
║  [Todos] [Con Stock] [Sin Stock] [Bajo]    ║
║                                             ║
║  ┌───────────────────────────────────────┐ ║
║  │ Código │ Nombre │ Stock │ Acciones   │ ║
║  ├────────┼────────┼───────┼────────────┤ ║
║  │ P-001  │ Nike   │  45   │ [Editar]   │ ║
║  │ P-002  │ Adidas │   8   │ [Editar]   │ ║
║  └────────┴────────┴───────┴────────────┘ ║
║                                             ║
╚═════════════════════════════════════════════╝
```

---

## 📚 Documentación Relacionada

- [Sistema Completo de Edición](PLAN_EDICION_PRODUCTOS_Y_STOCK.md)
- [Guía de Usuario Completa](GUIA_USO_EDICION_PRODUCTOS.md)
- [Inicio Rápido](INICIO_RAPIDO_EDICION_PRODUCTOS.md)

---

## ✅ Checklist de Primera Vez

```
☐ Abrí el modal de búsqueda con "Edición Productos"
☐ Busqué un producto por nombre
☐ Probé los filtros rápidos
☐ Edité un producto exitosamente
☐ Cambié el precio de un producto
☐ Ajusté el stock de una talla
☐ Vi el historial de movimientos
```

---

## 🔧 Solución de Problemas

### No aparece el botón "Edición Productos"
**Solución**: Refresque la página con Ctrl+F5

### No encuentra productos al buscar
**Solución**: 
1. Verifique que hay productos en la BD
2. Intente con un término más simple
3. Use los filtros rápidos

### Error al abrir modal de edición
**Solución**:
1. Verifique la consola del navegador (F12)
2. Asegúrese de que el script edicion_productos.js esté cargado
3. Refresque la página

---

**¡Listo para usar! 🚀**

Con esta nueva funcionalidad puede:
- ✅ Buscar productos rápidamente
- ✅ Editar todos los atributos
- ✅ Gestionar stock por talla
- ✅ Mantener control total de su inventario

**URL de acceso**: `http://localhost:8000/app/verGestionProducto/`

