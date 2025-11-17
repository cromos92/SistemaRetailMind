# Guía de Funciones - Módulo de Requerimientos

## 📚 Índice de Funciones Consolidadas

Todas las funciones JavaScript del módulo de requerimientos están organizadas en un solo archivo HTML para facilitar el mantenimiento y comprensión.

---

## 🔍 CATEGORÍA 1: NAVEGACIÓN

### `navegarA(seccion)`
**Descripción**: Cambia entre las diferentes secciones del sistema.

**Parámetros**:
- `seccion` (string): 'lista', 'crear', 'detalle', 'estadisticas'

**Uso**:
```javascript
navegarA('lista');      // Va a la lista de requerimientos
navegarA('crear');      // Va al formulario de creación
navegarA('detalle');    // Va al detalle (requiere verDetalle() primero)
navegarA('estadisticas'); // Va a estadísticas
```

**Acciones**:
- Oculta todas las secciones
- Muestra la sección solicitada
- Ejecuta funciones específicas por sección
- Scroll to top automático

---

## 📊 CATEGORÍA 2: CARGA DE DATOS

### `cargarEstadisticas()`
**Descripción**: Actualiza los contadores de estadísticas en el dashboard.

**Sin parámetros**

**Uso**:
```javascript
cargarEstadisticas();
```

**Actualiza**:
- Total de requerimientos
- Pendientes
- En proceso
- Completados

---

### `cargarRequerimientos(pagina = 1)`
**Descripción**: Carga y filtra los requerimientos con paginación.

**Parámetros**:
- `pagina` (number): Número de página a mostrar

**Uso**:
```javascript
cargarRequerimientos(1);  // Primera página
cargarRequerimientos(2);  // Segunda página
```

**Funcionalidad**:
- Aplica filtros actuales
- Pagina resultados
- Actualiza tabla
- Actualiza paginación

---

### `cargarDesdeLocalStorage()`
**Descripción**: Carga los datos desde localStorage al iniciar la aplicación.

**Sin parámetros**

**Uso**:
```javascript
cargarDesdeLocalStorage(); // Ejecutado automáticamente al iniciar
```

**Funcionalidad**:
- Lee datos de localStorage
- Si no hay datos, carga ejemplos
- Inicializa requerimientosData

---

### `cargarEstadisticasDetalladas()`
**Descripción**: Genera estadísticas detalladas para la sección de reportes.

**Sin parámetros**

**Uso**:
```javascript
cargarEstadisticasDetalladas();
```

**Genera**:
- Tabla de resumen
- Conteos por estado
- Conteos por tipo
- Métricas generales

---

## 📋 CATEGORÍA 3: VISUALIZACIÓN

### `mostrarRequerimientos(requerimientos)`
**Descripción**: Renderiza la tabla de requerimientos en el DOM.

**Parámetros**:
- `requerimientos` (array): Lista de objetos de requerimientos

**Uso**:
```javascript
const reqs = requerimientosData.slice(0, 20);
mostrarRequerimientos(reqs);
```

**Genera**:
- Filas de tabla HTML
- Badges de estado
- Links a detalles
- Mensaje si está vacío

---

### `actualizarPaginacion(total, paginaActual, itemsPorPagina)`
**Descripción**: Actualiza los controles de paginación.

**Parámetros**:
- `total` (number): Total de items
- `paginaActual` (number): Página actual
- `itemsPorPagina` (number): Items por página

**Uso**:
```javascript
actualizarPaginacion(150, 2, 20);
// Muestra: 21-40 de 150 resultados
```

**Actualiza**:
- Contador de resultados
- Botones de paginación
- Estado activo de página

---

### `verDetalle(id)`
**Descripción**: Muestra el detalle completo de un requerimiento.

**Parámetros**:
- `id` (string): ID del requerimiento

**Uso**:
```javascript
verDetalle('REQ-001');
```

**Acciones**:
- Busca el requerimiento
- Llena todos los campos del detalle
- Renderiza historial
- Navega a sección detalle

---

## ✏️ CATEGORÍA 4: FORMULARIOS

### `resetearFormulario()`
**Descripción**: Limpia el formulario de creación.

**Sin parámetros**

**Uso**:
```javascript
resetearFormulario();
```

**Acciones**:
- Reset de todos los campos
- Elimina fotos agregadas
- Reinicia contador de fotos

---

### `guardarRequerimiento(event)`
**Descripción**: Guarda un nuevo requerimiento.

**Parámetros**:
- `event` (Event): Evento del formulario

**Uso**:
```javascript
// Llamado automáticamente al submit del formulario
<form onsubmit="return guardarRequerimiento(event)">
```

**Acciones**:
- Valida campos requeridos
- Crea objeto de requerimiento
- Agrega a requerimientosData
- Guarda en localStorage
- Muestra confirmación
- Navega a lista o detalle

---

### `agregarFoto()`
**Descripción**: Agrega un nuevo campo de foto al formulario.

**Sin parámetros**

**Uso**:
```javascript
agregarFoto();
```

**Límite**: Máximo 5 fotos

**Acciones**:
- Incrementa contador
- Crea nuevo campo de archivo
- Agrega previsualización
- Oculta botón si alcanza máximo

---

### `eliminarFoto(numero)`
**Descripción**: Elimina un campo de foto del formulario.

**Parámetros**:
- `numero` (number): Número de la foto

**Uso**:
```javascript
eliminarFoto(1); // Elimina foto 1
```

**Acciones**:
- Elimina el contenedor
- Decrementa contador
- Muestra botón agregar

---

### `previsualizarFoto(input, numero)`
**Descripción**: Muestra preview de la foto seleccionada.

**Parámetros**:
- `input` (HTMLInputElement): Input file
- `numero` (number): Número de la foto

**Uso**:
```javascript
// Llamado automáticamente al cambiar archivo
<input onchange="previsualizarFoto(this, 1)">
```

**Acciones**:
- Lee el archivo seleccionado
- Convierte a Data URL
- Muestra imagen en preview

---

### `buscarProductoPorSKU()`
**Descripción**: Busca un producto por su SKU.

**Sin parámetros** (Lee del input #sku)

**Uso**:
```javascript
buscarProductoPorSKU();
```

**Nota**: En versión standalone, simula búsqueda. En versión Django conectaría con API.

---

## 🔄 CATEGORÍA 5: GESTIÓN DE ESTADOS

### `cambiarEstadoRequerimiento()`
**Descripción**: Abre el modal para cambiar estado.

**Sin parámetros**

**Uso**:
```javascript
cambiarEstadoRequerimiento();
```

**Acciones**:
- Muestra modal de cambio de estado
- Limpia campos del modal

---

### `guardarCambioEstado()`
**Descripción**: Guarda el cambio de estado del requerimiento.

**Sin parámetros** (Lee del modal)

**Uso**:
```javascript
// Llamado desde el modal
guardarCambioEstado();
```

**Acciones**:
- Lee nuevo estado y comentario
- Actualiza requerimiento actual
- Agrega entrada al historial
- Guarda en localStorage
- Cierra modal
- Recarga detalle

---

### `completarRequerimiento()`
**Descripción**: Completa un requerimiento con resolución final.

**Sin parámetros**

**Uso**:
```javascript
completarRequerimiento();
```

**Acciones**:
- Muestra SweetAlert con textarea
- Valida resolución
- Cambia estado a COMPLETADO
- Agrega resolución al historial
- Guarda cambios
- Recarga detalle

---

## 🔍 CATEGORÍA 6: FILTROS Y BÚSQUEDA

### `toggleFiltros()`
**Descripción**: Muestra u oculta el panel de filtros.

**Sin parámetros**

**Uso**:
```javascript
toggleFiltros();
```

**Acciones**:
- Toggle display del panel de filtros

---

### `aplicarFiltros(event)`
**Descripción**: Aplica los filtros seleccionados.

**Parámetros**:
- `event` (Event): Evento del formulario

**Uso**:
```javascript
// Llamado automáticamente al submit
<form onsubmit="return aplicarFiltros(event)">
```

**Acciones**:
- Lee valores de filtros
- Actualiza filtrosActuales
- Recarga primera página
- Previene submit

---

### `exportarRequerimientos()`
**Descripción**: Exporta requerimientos a Excel.

**Sin parámetros**

**Uso**:
```javascript
exportarRequerimientos();
```

**Nota**: En versión standalone muestra mensaje. En Django generaría Excel real.

---

## 🛠️ CATEGORÍA 7: UTILIDADES

### `obtenerBadgeEstado(estado)`
**Descripción**: Retorna HTML del badge según el estado.

**Parámetros**:
- `estado` (string): Código del estado

**Retorna**: String HTML

**Uso**:
```javascript
const badge = obtenerBadgeEstado('PENDIENTE');
// Retorna: '<span class="badge badge-soft-warning">Pendiente</span>'
```

**Estados soportados**:
- PENDIENTE → Warning (amarillo)
- EN_REVISION → Info (azul)
- ESPERANDO_PROVEEDOR → Primary (azul oscuro)
- APROBADO → Success (verde)
- RECHAZADO → Danger (rojo)
- EN_PROCESO → Info (azul)
- COMPLETADO → Success (verde)

---

### `obtenerColorPrioridad(prioridad)`
**Descripción**: Retorna el color Bootstrap según la prioridad.

**Parámetros**:
- `prioridad` (string): Código de prioridad

**Retorna**: String (nombre de color Bootstrap)

**Uso**:
```javascript
const color = obtenerColorPrioridad('ALTA');
// Retorna: 'warning'
```

**Prioridades**:
- BAJA → success
- MEDIA → info
- ALTA → warning
- URGENTE → danger

---

### `guardarEnLocalStorage()`
**Descripción**: Guarda requerimientosData en localStorage.

**Sin parámetros**

**Uso**:
```javascript
guardarEnLocalStorage();
```

**Acciones**:
- Convierte array a JSON
- Guarda en localStorage con key 'requerimientosData'

---

## 📦 ESTRUCTURA DE DATOS

### Objeto Requerimiento
```javascript
{
    id: 'REQ-001',                    // ID único
    numero: 'REQ-00001',              // Número visible
    tipo: 'GARANTIA',                 // Código de tipo
    tipoTexto: 'Garantía',            // Texto del tipo
    estado: 'PENDIENTE',              // Estado actual
    sku: 'PROD-12345',                // SKU del producto
    nombreProducto: 'Producto X',     // Nombre del producto
    clienteNombre: 'Juan Pérez',      // Nombre cliente
    clienteRut: '12345678-9',         // RUT cliente
    clienteTelefono: '+56912345678',  // Teléfono
    clienteEmail: 'email@test.com',   // Email
    motivo: 'Descripción...',         // Motivo del req.
    descripcion: 'Detalles...',       // Descripción detallada
    prioridad: 'MEDIA',               // Prioridad
    sucursal: 'Santiago',             // Sucursal
    tipoDocumento: 'BOLETA',          // Tipo de doc
    numeroDocumento: '12345',         // Número doc
    fechaCompra: '2024-11-01',        // Fecha de compra
    fecha: '15/11/2024',              // Fecha creación
    dias: 2,                          // Días transcurridos
    historial: [                      // Historial de cambios
        {
            accion: 'CREADO',
            fecha: '15/11/2024 10:30',
            comentario: 'Comentario...',
            usuario: 'Usuario'
        }
    ],
    fotos: []                         // Array de fotos
}
```

---

## 🎯 VARIABLES GLOBALES

```javascript
requerimientosData = []     // Array principal de requerimientos
requerimientoActual = null  // Requerimiento en vista de detalle
paginaActual = 1            // Página actual de la lista
filtrosActuales = {}        // Objeto con filtros aplicados
contadorFotos = 0           // Contador de fotos en formulario
maxFotos = 5                // Máximo de fotos permitidas
```

---

## 🔄 FLUJO TÍPICO DE USO

### Crear Requerimiento:
```javascript
1. navegarA('crear')
2. // Usuario llena formulario
3. guardarRequerimiento(event)
4. → Guarda en requerimientosData
5. → guardarEnLocalStorage()
6. → navegarA('lista') o verDetalle(id)
```

### Ver y Actualizar:
```javascript
1. navegarA('lista')
2. cargarRequerimientos()
3. verDetalle('REQ-001')
4. cambiarEstadoRequerimiento()
5. guardarCambioEstado()
6. → Actualiza requerimientosData
7. → guardarEnLocalStorage()
```

### Filtrar:
```javascript
1. toggleFiltros()
2. // Usuario selecciona filtros
3. aplicarFiltros(event)
4. → Actualiza filtrosActuales
5. cargarRequerimientos(1)
```

---

## 🚀 EVENTOS AUTOMÁTICOS

Al cargar la página (`DOMContentLoaded`):
```javascript
1. cargarDesdeLocalStorage()  // Carga datos
2. navegarA('lista')           // Muestra lista
3. → cargarRequerimientos()    // Carga tabla
4. → cargarEstadisticas()      // Actualiza stats
```

---

## 💡 TIPS DE DESARROLLO

1. **Agregar nueva función**: Agrégala en la categoría correspondiente con comentarios
2. **Modificar flujo**: Actualiza las funciones de navegación y carga
3. **Nuevos campos**: Actualiza estructura de datos y formularios
4. **Estilos**: Usa las variables CSS en `:root`
5. **Testing**: Usa los datos de ejemplo incluidos

---

## 📞 DEBUGGING

**Ver datos actuales**:
```javascript
console.log(requerimientosData);
```

**Ver filtros activos**:
```javascript
console.log(filtrosActuales);
```

**Ver requerimiento en detalle**:
```javascript
console.log(requerimientoActual);
```

**Limpiar localStorage**:
```javascript
localStorage.removeItem('requerimientosData');
location.reload();
```

---

**Última actualización**: 17 de Noviembre, 2024  
**Total de funciones documentadas**: 24

