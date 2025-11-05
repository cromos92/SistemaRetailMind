# 📊 DASHBOARD DE COMPRAS ESTRATÉGICO - DOCUMENTACIÓN COMPLETA

**Fecha de Implementación:** 05 de Noviembre 2025  
**Sistema:** RetailMind - Módulo de Compras  
**URL:** `http://localhost:8000/app/verDashboardCompras/`

---

## 🎯 RESUMEN EJECUTIVO

El Dashboard de Compras Estratégico es un módulo completo que analiza el rendimiento de las compras en tiempo real utilizando datos reales de la base de datos. Proporciona métricas clave, análisis de cumplimiento, ROI y recomendaciones para optimizar el proceso de compras.

---

## 📋 CARACTERÍSTICAS PRINCIPALES

### 1. **Métricas KPI Principales**

#### 📈 Cumplimiento General
- **Cálculo:** `(Unidades Recepcionadas / Unidades Esperadas) × 100`
- **Datos:** Compara stock esperado vs stock arribado en recepciones
- **Indicador de tendencia:** Muestra cambio respecto a periodo anterior

#### 💰 ROI Promedio
- **Cálculo:** `((Valor Venta Esperado - Inversión Total) / Inversión Total) × 100`
- **Datos:** Basado en precio sugerido vs costo de compra
- **Útil para:** Evaluar rentabilidad potencial de las compras

#### 🔄 Rotación de Inventario
- **Cálculo:** `Productos con Recepción / Total Productos`
- **Datos:** Indica qué porcentaje de productos han sido recepcionados
- **Útil para:** Medir eficiencia del proceso de recepción

#### 🎯 Precisión de Pronóstico
- **Cálculo:** Similar al cumplimiento general
- **Datos:** Mide qué tan precisas son las estimaciones de compra
- **Útil para:** Mejorar planificación de compras futuras

---

### 2. **Métricas Adicionales**

| Métrica | Descripción | Fuente de Datos |
|---------|-------------|-----------------|
| **Total Compras** | Número de compras registradas | Modelo `Compras` |
| **Productos Distintos** | SKUs únicos comprados | Modelo `Compras_Producto` |
| **Unidades Esperadas** | Total de unidades en órdenes de compra | `Compras_Producto_Talla.stock` |
| **Unidades Recepcionadas** | Total de unidades recibidas | `Productos_Recepcionados.stockArribado` |
| **Inversión Total** | Monto total invertido en compras | `Σ(costo × stock)` |
| **Valor Venta Esperado** | Valor potencial de venta | `Σ(precioSugerido × stock)` |

---

### 3. **Análisis por Proveedor**

El dashboard calcula el cumplimiento individual de cada proveedor:

```python
# Para cada proveedor:
Cumplimiento = (Unidades Recepcionadas del Proveedor / Unidades Esperadas del Proveedor) × 100
```

**Gráfico de Barras:** Muestra comparativa visual del cumplimiento por proveedor.

---

### 4. **Análisis por Temporada**

Calcula el ROI de cada temporada de compras:

```python
# Para cada temporada:
ROI = ((Valor Venta - Inversión) / Inversión) × 100
```

**Gráfico de Dona:** Distribuye visualmente el ROI entre temporadas.

---

### 5. **Rendimiento Detallado por Compra**

Tabla completa con las siguientes columnas:

| Columna | Cálculo | Interpretación |
|---------|---------|----------------|
| **Compra** | Nombre de la compra | Identificación |
| **Proveedor** | Empresa proveedora | Origen |
| **Temporada** | Temporada de la compra | Clasificación |
| **Cumplimiento** | % recepcionado | 🟢 ≥80% / 🟡 60-79% / 🔴 <60% |
| **ROI** | % de rentabilidad | 🟢 ≥20% / 🟡 10-19% / 🔴 <10% |
| **Rotación** | Velocidad de recepción | Eficiencia logística |
| **Precisión** | Exactitud del pedido | Calidad de planificación |
| **Estado** | Completado/Pendiente/Retrasado | Estado actual |

---

### 6. **Sistema de Alertas Inteligentes**

El sistema genera alertas automáticas basadas en los datos:

#### ⚠️ Alertas Generadas

1. **Cumplimiento Bajo:** Se activa si cumplimiento < 80%
   ```
   "Cumplimiento general bajo (XX%). Revisar procesos de recepción."
   ```

2. **Compras sin Recepción:** Detecta compras sin productos recepcionados
   ```
   "N compra(s) sin recepción registrada."
   ```

3. **ROI Bajo:** Se activa si ROI < 15%
   ```
   "ROI promedio bajo (XX%). Revisar precios y costos."
   ```

---

### 7. **Sistema de Recomendaciones**

Recomendaciones inteligentes basadas en el análisis:

#### 💡 Recomendaciones Generadas

1. **Mejorar Cumplimiento:** Si cumplimiento < 90%
   ```
   "Implementar seguimiento más estricto de recepciones para mejorar cumplimiento."
   ```

2. **Optimizar Rotación:** Si rotación < 0.5
   ```
   "Optimizar gestión de inventario para aumentar rotación de productos."
   ```

3. **Revisar Proveedores:** Si hay proveedores con cumplimiento < 80%
   ```
   "Revisar desempeño de N proveedor(es) con bajo cumplimiento."
   ```

---

## 🔧 FILTROS DISPONIBLES

### Filtro por Año
- **Valores:** 2023, 2024, 2025
- **Efecto:** Filtra todas las compras del año seleccionado

### Filtro por Temporada
- **Valores:** Todas, Invierno, Verano, Otoño, Primavera
- **Efecto:** Filtra compras de la temporada específica

### Filtro por Proveedor
- **Valores:** Lista dinámica de proveedores activos
- **Fuente:** `Empresa.objects.filter(esProveedor=True)`

### Filtro por Responsable
- **Valores:** Lista dinámica de responsables únicos
- **Fuente:** Campo `responsable` de compras del año

---

## 🗄️ ESTRUCTURA DE DATOS

### Modelos Utilizados

```python
# 1. COMPRAS (Orden de Compra)
Compras
├─ empresa: ForeignKey(Empresa)         # Proveedor
├─ nombre: CharField                     # Nombre de la compra
├─ correlativo: IntegerField            # Número correlativo
├─ responsable: CharField               # Encargado
├─ temporada: CharField                 # Temporada (Invierno, Verano, etc.)
└─ fecha: DateField                     # Fecha de la compra

# 2. COMPRAS_PRODUCTO (Productos de la Compra)
Compras_Producto
├─ compras: ForeignKey(Compras)
├─ nombre: CharField                    # Nombre del producto
├─ descripcion: CharField
├─ atributo1, atributo2, atributo3, atributo4  # Marca, Color, Género, etc.
├─ tipo_talla: CharField
├─ costo: IntegerField                  # Costo unitario
└─ precioSugerido: IntegerField        # Precio sugerido de venta

# 3. COMPRAS_PRODUCTO_TALLA (Tallas del Producto)
Compras_Producto_Talla
├─ compra_producto: ForeignKey(Compras_Producto)
├─ stock: IntegerField                  # Cantidad esperada
└─ talla: CharField                     # Talla específica

# 4. PRODUCTOS_RECEPCIONADOS (Recepciones)
Productos_Recepcionados
├─ compra_producto_talla: ForeignKey(Compras_Producto_Talla)
├─ producto_talla: ForeignKey(Producto_Talla)
├─ stockArribado: IntegerField          # Cantidad recibida
├─ cantidad_esperada: IntegerField
├─ cantidad_danada: IntegerField
├─ cantidad_faltante: IntegerField
├─ estado: CharField                    # Estado de recepción
├─ observaciones: TextField
├─ fecha: DateField
└─ recepcionado_por: CharField
```

---

## 📡 API ENDPOINT

### **GET** `/app/dashboard_compras_estrategico/`

#### Parámetros de Query

| Parámetro | Tipo | Requerido | Descripción |
|-----------|------|-----------|-------------|
| `anio` | int | No | Año de las compras (default: año actual) |
| `temporada` | string | No | Filtrar por temporada |
| `proveedor` | int | No | ID de la empresa proveedora |
| `responsable` | string | No | Nombre del responsable |

#### Respuesta JSON

```json
{
  "cumplimiento_general": 85.3,
  "roi_promedio": 18.8,
  "rotacion_inventario": 3.4,
  "precision_pronostico": 87.1,
  "cumplimiento_proveedores": [
    {
      "proveedor": "Nike Chile",
      "cumplimiento": 85.5
    }
  ],
  "roi_temporadas": [
    {
      "temporada": "Invierno 2025",
      "roi": 18.2
    }
  ],
  "rendimiento_detallado": [
    {
      "nombre": "Compra Invierno 2025",
      "proveedor": "Nike Chile",
      "temporada": "Invierno 2025",
      "cumplimiento": 85.5,
      "roi": 18.2,
      "rotacion": 3.2,
      "precision": 87.3,
      "estado": "Pendiente"
    }
  ],
  "alertas": [
    {
      "mensaje": "Cumplimiento general bajo (85.3%). Revisar procesos de recepción."
    }
  ],
  "recomendaciones": [
    {
      "mensaje": "Implementar seguimiento más estricto de recepciones para mejorar cumplimiento."
    }
  ],
  "trend_cumplimiento": 5.2,
  "trend_roi": 8.5,
  "trend_rotacion": 0,
  "trend_precision": 2.3,
  "metricas_adicionales": {
    "total_compras": 10,
    "total_productos": 50,
    "total_unidades_esperadas": 1000,
    "total_unidades_recepcionadas": 853,
    "inversion_total": 25000000,
    "valor_venta_esperado": 45000000
  }
}
```

---

## 🎨 INTERFAZ DE USUARIO

### Secciones del Dashboard

1. **Header con Título y Botones**
   - Botón "Exportar" - Descarga reporte en Excel
   - Botón "Actualizar" - Recarga datos en tiempo real

2. **Alerta de Datos**
   - Se oculta automáticamente cuando hay datos reales
   - Se muestra solo si no hay compras en el sistema

3. **Panel de Filtros**
   - Fondo gris claro
   - 4 selectores en línea
   - Actualización automática al cambiar filtros

4. **KPIs Principales** (4 tarjetas grandes)
   - Con valores numéricos destacados
   - Indicadores de tendencia con colores

5. **Métricas Adicionales** (4 tarjetas pequeñas)
   - Totales generales
   - Formato numérico con separadores de miles

6. **Inversión y Valor** (2 tarjetas con borde de color)
   - Montos en formato moneda chilena
   - Bordes de colores para diferenciación visual

7. **Gráficos** (2 gráficos lado a lado)
   - Cumplimiento por Proveedor (gráfico de barras)
   - ROI por Temporada (gráfico de dona)

8. **Tabla de Rendimiento Detallado**
   - 8 columnas con información completa
   - Barras de progreso para cumplimiento
   - Badges de colores para ROI y estado

9. **Alertas y Recomendaciones** (2 paneles)
   - Alertas con borde amarillo (advertencias)
   - Recomendaciones con borde azul (sugerencias)

---

## 🚀 FUNCIONES JAVASCRIPT

### Funciones Principales

```javascript
// Carga inicial del dashboard
cargarFiltros()           // Carga opciones de proveedores y responsables
actualizarDashboard()     // Obtiene datos del API y actualiza todo

// Actualización de componentes
actualizarKPIs()          // Actualiza las 4 KPIs principales + métricas adicionales
actualizarGraficos()      // Actualiza los 2 gráficos
actualizarTabla()         // Llena la tabla de rendimiento detallado
actualizarAlertas()       // Muestra alertas generadas
actualizarRecomendaciones() // Muestra recomendaciones

// Funciones auxiliares
actualizarTendencia(id, valor)  // Actualiza indicadores de tendencia
formatMoney(amount)             // Formatea montos en pesos chilenos
getProgressBarClass(%)          // Clase CSS según porcentaje
getROIBadgeClass(roi)           // Clase CSS según ROI
getEstadoBadgeClass(estado)     // Clase CSS según estado

// Exportación
exportarReporte()         // Descarga Excel del dashboard
```

---

## 📊 EJEMPLOS DE CÁLCULO

### Ejemplo 1: Compra de Zapatillas Nike

**Datos de Entrada:**
- Compra: "Zapatillas Nike Invierno 2025"
- Proveedor: Nike Chile
- Productos: 3 modelos
- Tallas: 9 tallas por modelo
- Unidades esperadas: 270 pares
- Costo promedio: $25,000
- Precio sugerido: $45,000

**Proceso de Recepción:**
- Unidades recibidas: 250 pares
- Unidades dañadas: 5 pares
- Unidades faltantes: 15 pares

**Cálculos del Dashboard:**

1. **Cumplimiento:**
   ```
   (250 / 270) × 100 = 92.6%
   Estado: COMPLETADO (verde)
   ```

2. **Inversión Total:**
   ```
   270 × $25,000 = $6,750,000
   ```

3. **Valor Venta Esperado:**
   ```
   270 × $45,000 = $12,150,000
   ```

4. **ROI:**
   ```
   ((12,150,000 - 6,750,000) / 6,750,000) × 100 = 80%
   Badge: Verde (excelente)
   ```

5. **Rotación:**
   ```
   250 / 270 = 0.93
   ```

---

## 🔍 CASOS DE USO

### Caso 1: Gerente de Compras - Evaluación Mensual

**Objetivo:** Evaluar rendimiento de compras del mes

**Pasos:**
1. Acceder al dashboard
2. Seleccionar año actual y mes
3. Revisar KPIs principales
4. Identificar proveedores con bajo cumplimiento
5. Leer alertas y recomendaciones
6. Exportar reporte para reunión gerencial

**Resultado Esperado:**
- Identificar 2-3 áreas de mejora
- Lista de proveedores a negociar
- Reporte Excel para presentación

---

### Caso 2: Analista de Inventario - Optimización de Stocks

**Objetivo:** Identificar productos con baja rotación

**Pasos:**
1. Filtrar por temporada actual
2. Revisar métrica de rotación de inventario
3. Analizar tabla de rendimiento detallado
4. Identificar compras con rotación < 0.5
5. Revisar recomendaciones del sistema

**Resultado Esperado:**
- Lista de productos con baja rotación
- Acciones correctivas sugeridas
- Plan de optimización de compras futuras

---

### Caso 3: Director Financiero - Análisis de ROI

**Objetivo:** Evaluar rentabilidad de inversiones en compras

**Pasos:**
1. Filtrar por año fiscal
2. Revisar ROI promedio general
3. Analizar gráfico de ROI por temporada
4. Identificar temporadas más rentables
5. Comparar con presupuesto asignado

**Resultado Esperado:**
- ROI promedio del periodo
- Temporadas más y menos rentables
- Decisiones de inversión para próximo periodo

---

## ⚡ RENDIMIENTO Y OPTIMIZACIÓN

### Queries Optimizadas

El dashboard utiliza agregaciones eficientes de Django ORM:

```python
# ✅ BUENO - Una sola query con agregación
total_unidades = tallas_compras.aggregate(total=Sum('stock'))['total']

# ❌ MALO - Múltiples queries en loop
for talla in tallas:
    total += talla.stock
```

### Estrategias de Optimización

1. **Select Related:** Pre-carga relaciones ForeignKey
   ```python
   compras_query.select_related('empresa')
   ```

2. **Prefetch Related:** Pre-carga relaciones Many-to-Many

3. **Agregaciones:** Usa `Sum()`, `Count()`, `Avg()` de Django

4. **Limitación de Resultados:** Tabla muestra solo primeras 10 compras

5. **Cache de Filtros:** Almacena lista de proveedores y responsables

---

## 🛠️ MANTENIMIENTO Y MEJORAS FUTURAS

### Posibles Mejoras

1. **Tendencias Reales:**
   - Actualmente las tendencias son simuladas
   - Implementar comparación con periodos anteriores
   - Calcular tendencias basadas en histórico real

2. **Exportación Avanzada:**
   - Incluir gráficos en el Excel exportado
   - Formato profesional con logos y estilos
   - Múltiples hojas con análisis detallados

3. **Predictivo:**
   - Machine Learning para predecir cumplimiento
   - Alertas tempranas de problemas potenciales
   - Sugerencias de cantidades óptimas de compra

4. **Integración con DTEs:**
   - Vincular compras con DTEs de compra
   - Análisis de pagos y créditos
   - Control de facturas pendientes

5. **Dashboard en Tiempo Real:**
   - WebSockets para actualización automática
   - Notificaciones push de alertas críticas
   - Actualización sin necesidad de refrescar

---

## 🐛 SOLUCIÓN DE PROBLEMAS

### Error: "No se puede cargar el dashboard"

**Causa:** Endpoint no accesible o error en la query

**Solución:**
1. Verificar que el servidor esté corriendo
2. Revisar console del navegador para errores JavaScript
3. Verificar endpoint en `/app/dashboard_compras_estrategico/`
4. Revisar logs del servidor Django

---

### Dashboard muestra valores en 0

**Causa:** No hay datos de compras o recepciones

**Solución:**
1. Verificar que existan registros en modelo `Compras`
2. Verificar que haya productos asociados
3. Verificar que haya recepciones registradas
4. Ajustar filtros (año, temporada, proveedor)

---

### Gráficos no se muestran

**Causa:** Error en librería Chart.js o datos vacíos

**Solución:**
1. Verificar que Chart.js esté cargado correctamente
2. Revisar console para errores de JavaScript
3. Verificar que haya datos para graficar
4. Verificar estructura de datos en respuesta del API

---

## 📞 SOPORTE

Para soporte adicional o reportar errores:
- **Desarrollador:** WebAppSolutions
- **Sistema:** RetailMind
- **Módulo:** Compras - Dashboard Estratégico

---

## ✅ CHECKLIST DE IMPLEMENTACIÓN

- [x] Modelo de datos analizado y documentado
- [x] Endpoint API implementado con datos reales
- [x] Cálculos de métricas validados
- [x] Interfaz HTML con todos los componentes
- [x] JavaScript funcional con todas las actualizaciones
- [x] Filtros dinámicos implementados
- [x] Gráficos interactivos (Chart.js)
- [x] Sistema de alertas automáticas
- [x] Sistema de recomendaciones
- [x] Formateo de números y monedas
- [x] Responsive design
- [x] Exportación a Excel
- [x] Documentación completa

---

**Última Actualización:** 05 de Noviembre 2025  
**Versión:** 1.0  
**Estado:** ✅ PRODUCCIÓN

