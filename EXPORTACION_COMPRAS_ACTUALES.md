# 📊 Exportación de Compras Actuales

## 🎯 Descripción

Nueva funcionalidad para exportar todas las compras registradas en el sistema con su información completa de productos, tallas, costos y recepciones.

## ✨ Características

### **Botón Exportar en Gestión de Compras**

Ubicación: `http://localhost:8000/app/verGestionCompras/`

**Menú desplegable con opciones:**
```
┌────────────────────────────────┐
│ ▼ Exportar                     │
├────────────────────────────────┤
│ 📊 Compras Actuales            │
│  ├─ Excel (.xlsx)              │
│  └─ CSV (.csv)                 │
├────────────────────────────────┤
│ 📄 Formato de Ejemplo          │
│  └─ Formato CSV Productos      │
└────────────────────────────────┘
```

## 📁 Archivos Generados

### **Archivo Excel (.xlsx)**

**Nombre:** `compras_2025.xlsx` (según año seleccionado)

**Incluye 2 hojas:**

#### **Hoja 1: Resumen Compras**
Información general de cada compra:

| Columna | Descripción |
|---------|-------------|
| ID | Identificador único de la compra |
| Proveedor | Nombre del proveedor |
| RUT Proveedor | RUT del proveedor |
| Nombre Compra | Nombre descriptivo de la compra |
| Temporada | Temporada asociada |
| Fecha Inicio | Inicio de temporada |
| Fecha Término | Fin de temporada |
| Fecha Registro | Fecha de creación |
| Responsable | Usuario responsable |
| Total Productos | Cantidad de productos diferentes |
| Total Unidades | Suma de todas las unidades |
| Costo Total | Inversión total (costo × stock) |
| Venta Esperada | Venta proyectada (precio × stock) |
| Recepcionado | Total de unidades recepcionadas |

#### **Hoja 2: Detalle Productos**
Desglose completo por producto y talla:

| Columna | Descripción |
|---------|-------------|
| ID Compra | ID de la compra |
| Nombre Compra | Nombre de la compra |
| Proveedor | Proveedor asociado |
| Nombre Producto | Artículo |
| Descripción | Descripción del producto |
| Marca | Atributo 1 |
| Color | Atributo 2 |
| Género | Atributo 3 |
| Costo | Costo unitario |
| Precio Sugerido | Precio de venta |
| Talla | Talla específica |
| Stock | Unidades esperadas |
| Recepcionado | Unidades recibidas |
| Factura DTE | Número de factura asociada |

### **Archivo CSV (.csv)**

**Nombre:** `compras_2025.csv`

**Formato:** Todas las columnas del detalle de productos en un solo archivo CSV
- Más simple que Excel
- Compatible con cualquier software
- Perfecto para análisis en otras herramientas

## 🚀 Cómo Usar

### **Paso 1: Seleccionar Año**
```
1. En "Gestión de Compras"
2. Ingresar año (ej: 2025)
3. Hacer clic en "Ver Compra" (opcional)
```

### **Paso 2: Exportar**
```
1. Clic en dropdown "Exportar"
2. Seleccionar formato deseado:
   - "Exportar a Excel" → 2 hojas con formato
   - "Exportar a CSV" → 1 archivo simple
3. El archivo se descarga automáticamente
```

## 💡 Casos de Uso

### **Caso 1: Análisis de Compras del Año**
```
1. Exportar compras del año actual
2. Abrir Excel
3. Usar filtros y tablas dinámicas
4. Analizar por proveedor, temporada, etc.
```

### **Caso 2: Respaldo de Información**
```
1. Exportar compras a Excel
2. Guardar en servidor/nube
3. Usar como respaldo histórico
```

### **Caso 3: Auditoría de Recepciones**
```
1. Exportar compras
2. Filtrar por "Recepcionado < Stock"
3. Identificar compras pendientes
4. Hacer seguimiento
```

### **Caso 4: Cálculo de ROI**
```
1. Exportar compras
2. Comparar "Costo Total" vs "Venta Esperada"
3. Calcular márgenes y rentabilidad
4. Tomar decisiones de compra
```

## 📊 Información Incluida

### **Datos de Compra:**
- ✅ Información completa de la compra
- ✅ Datos del proveedor (nombre + RUT)
- ✅ Fechas de temporada
- ✅ Responsable

### **Datos de Productos:**
- ✅ Todos los productos de cada compra
- ✅ Desglose por talla
- ✅ Costos y precios
- ✅ Stock esperado vs recepcionado

### **Datos de Recepción:**
- ✅ Estado de recepción por talla
- ✅ Factura DTE asociada
- ✅ Pendientes por recepcionar

## 🎯 Beneficios

### **Para Análisis:**
- 📊 Toda la información en un solo archivo
- 🔍 Filtrar y buscar fácilmente
- 📈 Crear gráficos y reportes
- 💡 Identificar tendencias

### **Para Control:**
- ✅ Verificar recepciones pendientes
- 📋 Auditar compras
- 🎯 Seguimiento de inversión
- 💰 Cálculo de ROI

### **Para Respaldo:**
- 💾 Exportación rápida
- 📁 Archivo completo
- 🔄 Formato estándar
- ☁️ Fácil de compartir

## 📝 Formato de Excel

### **Características:**
- ✅ **2 hojas:** Resumen + Detalle
- ✅ **Formato profesional:** Colores, bordes, alineación
- ✅ **Columnas ajustadas:** Ancho óptimo automático
- ✅ **Encabezados destacados:** Fondo azul, texto blanco
- ✅ **Bordes en todas las celdas**
- ✅ **Listo para análisis**

### **Ejemplo de Resumen:**
```excel
ID | Proveedor       | Nombre Compra  | Temporada    | Total Unidades | Costo Total | Recepcionado
1  | Nike Chile      | Compra Invierno| Invierno 2025| 500            | $5.000.000  | 450
2  | Adidas SA       | Primavera      | Primavera 25 | 300            | $3.500.000  | 300
```

### **Ejemplo de Detalle:**
```excel
ID | Compra         | Producto        | Marca  | Color | Talla | Stock | Recepcionado | Factura
1  | Compra Invierno| Zapatilla Run   | Nike   | Negro | 42    | 10    | 10           | 12345
1  | Compra Invierno| Zapatilla Run   | Nike   | Negro | 43    | 8     | 8            | 12345
2  | Primavera      | Polera Sport    | Adidas | Azul  | M     | 15    | 15           | 67890
```

## 🔧 APIs Disponibles

| Método | URL | Parámetros | Descripción |
|--------|-----|------------|-------------|
| GET | `/app/api/exportar-compras-excel/` | `anio` | Exportar a Excel |
| GET | `/app/api/exportar-compras-csv/` | `anio` | Exportar a CSV |

**Ejemplos:**
```
/app/api/exportar-compras-excel/?anio=2025
/app/api/exportar-compras-csv/?anio=2024
```

## 💡 Consejos

### **Antes de Exportar:**
1. Selecciona el año deseado
2. Verifica que haya compras en ese año

### **Después de Exportar:**
1. Abre el archivo Excel
2. Explora las 2 hojas
3. Usa filtros para análisis específicos
4. Guarda en carpeta de respaldos

### **Para Análisis Avanzado:**
1. Usa tablas dinámicas en Excel
2. Crea gráficos de inversión
3. Compara temporadas
4. Identifica productos rentables

## ⚠️ Notas Importantes

- **Año requerido:** Se exportan solo las compras del año seleccionado
- **Sin filtros adicionales:** Exporta todas las compras del año (sin filtro de proveedor, temporada, etc.)
- **Formato completo:** Incluye todos los detalles disponibles
- **Recepción:** Muestra estado actual de recepciones
- **Performance:** Optimizado para grandes volúmenes de datos

## 🎁 Ventajas vs Formato CSV

| Aspecto | Formato CSV | Exportar Actuales |
|---------|-------------|-------------------|
| Propósito | Plantilla vacía | Datos reales |
| Contenido | Ejemplos | Tu información |
| Hojas | 2 (Formato + Instrucciones) | 2 (Resumen + Detalle) |
| Uso | Importar nuevos | Analizar/Respaldar |

## ✅ Listo para Usar

El botón "Exportar" está disponible en la página de Gestión de Compras con:
- ✅ Exportación a Excel (2 hojas)
- ✅ Exportación a CSV
- ✅ Filtrado por año
- ✅ Información completa
- ✅ Formato profesional

**¡Ahora puedes exportar y analizar todas tus compras fácilmente!** 📊🚀

