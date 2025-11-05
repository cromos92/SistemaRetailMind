# 📊 Dashboard de Productos - Indicadores Clave de Negocio (KPIs)

## 🎯 Resumen Ejecutivo

Este dashboard proporciona una visión completa y profesional del inventario, integrando **indicadores clave de negocio** que permiten tomar decisiones estratégicas basadas en datos.

---

## 📈 Indicadores Implementados

### 1️⃣ MÉTRICAS BÁSICAS

#### **Total de Productos**
- **Descripción**: Número total de productos base en el catálogo
- **Fuente**: Tabla `Producto`
- **Uso**: Conocer el tamaño del catálogo

#### **Total de Tallas/SKUs**
- **Descripción**: Número total de variantes (producto + talla)
- **Fuente**: Tabla `Producto_Talla`
- **Uso**: Conocer la complejidad del inventario

#### **Productos con Stock**
- **Descripción**: Cantidad de SKUs que tienen existencias
- **Fuente**: `Producto_Talla.stock > 0`
- **Uso**: Medir disponibilidad

#### **Productos Agotados**
- **Descripción**: Cantidad de SKUs sin stock
- **Fuente**: `Producto_Talla.stock = 0`
- **⚠️ Alerta**: Alto número indica problemas de reposición

#### **Productos Nuevos (30 días)**
- **Descripción**: Productos ingresados en los últimos 30 días
- **Fuente**: `LoteProducto.fecha_ingreso`
- **Uso**: Seguimiento de renovación de catálogo

---

### 2️⃣ MÉTRICAS DE VALOR

#### **Valor Total Inventario (Precio Venta)**
- **Descripción**: Valor del inventario a precio de venta
- **Cálculo**: `Σ (stock × precio_venta)`
- **Uso**: Conocer el valor potencial del inventario

#### **Valor Inventario FIFO (Costo)**
- **Descripción**: Valor real del inventario al costo FIFO
- **Cálculo**: `Σ (cantidad_disponible × costo_unitario)` de lotes activos
- **Uso**: Valor contable real del inventario
- **✅ Ventaja**: Refleja el costo real de cada lote

#### **Margen Potencial**
- **Descripción**: Ganancia potencial si se vende todo el inventario
- **Cálculo**: `Valor Venta - Valor Costo FIFO`
- **Uso**: Proyección de rentabilidad

#### **Margen Porcentual**
- **Descripción**: Porcentaje de margen sobre el costo
- **Cálculo**: `(Margen Potencial / Valor FIFO) × 100`
- **Uso**: KPI de rentabilidad
- **📊 Benchmarks**: 
  - < 30%: Bajo
  - 30-50%: Aceptable
  - > 50%: Excelente

---

### 3️⃣ MÉTRICAS DE ROTACIÓN Y EFICIENCIA

#### **Rotación de Inventario**
- **Descripción**: Cuántas veces se vendió el inventario en 30 días
- **Cálculo**: `Unidades Vendidas (30d) / Stock Total Actual`
- **Uso**: Medir eficiencia del inventario
- **📊 Benchmarks**:
  - < 0.5: Inventario lento
  - 0.5-2: Normal
  - > 2: Alta rotación

#### **Días de Inventario**
- **Descripción**: Cuántos días durará el inventario actual
- **Cálculo**: `Stock Total / (Ventas 30d / 30)`
- **Uso**: Planificación de compras
- **⚠️ Alerta**: < 15 días = riesgo de quiebre

#### **Ventas 30 Días (Unidades)**
- **Descripción**: Total de unidades vendidas en últimos 30 días
- **Fuente**: `Ticket_Productos` con estado PAGADO
- **Uso**: Tendencia de ventas

#### **Ingresos 30 Días**
- **Descripción**: Ingresos totales de los últimos 30 días
- **Cálculo**: `Σ (cantidad × precio_venta)`
- **Uso**: Desempeño financiero

---

### 4️⃣ MÉTRICAS DE ALERTA (Problemas Potenciales)

#### **Stock Muerto (sin movimiento 90 días)**
- **Descripción**: Productos sin movimientos en 90 días
- **Fuente**: `Producto_Talla` sin `Movimientos_Producto` recientes
- **⚠️ Alerta Crítica**: Recursos inmovilizados
- **Acción**: Considerar promociones o descuentos

#### **Valor Stock Muerto**
- **Descripción**: Dinero inmovilizado en stock sin movimiento
- **Cálculo**: `Σ stock_muerto × precio_venta`
- **Uso**: Cuantificar pérdida de oportunidad

#### **Lotes Próximos a Vencimiento (30 días)**
- **Descripción**: Lotes que vencen en los próximos 30 días
- **Fuente**: `LoteProducto.fecha_vencimiento`
- **⚠️ Alerta**: Requiere acción inmediata
- **Acción**: Promociones urgentes o ajuste de precios

#### **Roturas de Stock (últimos 7 días)**
- **Descripción**: Productos que se agotaron recientemente
- **Fuente**: Movimientos EGRESO con stock actual = 0
- **⚠️ Alerta**: Pérdida de ventas potenciales
- **Acción**: Mejorar proceso de reposición

---

### 5️⃣ ANÁLISIS ABC (Clasificación por Valor)

Sistema de clasificación que identifica los productos más valiosos:

#### **Productos Clase A (Top 80%)**
- **Descripción**: Productos que representan el 80% del valor
- **Uso**: Prioridad máxima en gestión
- **Acción**: Control estricto, reposición ágil

#### **Productos Clase B (80-95%)**
- **Descripción**: Productos que representan el 15% del valor
- **Uso**: Control moderado

#### **Productos Clase C (95-100%)**
- **Descripción**: Productos que representan el 5% final
- **Uso**: Control básico
- **Consideración**: Evaluar si vale la pena mantenerlos

---

### 6️⃣ ANÁLISIS POR CATEGORÍA

#### **Distribución por Categoría (Cantidad)**
- **Descripción**: Cuántos productos hay por categoría
- **Visualización**: Gráfico de torta
- **Uso**: Balance del catálogo

#### **Valor por Categoría**
- **Descripción**: Valor de inventario agrupado por categoría
- **Cálculo**: `Σ (stock × precio_venta)` por categoría
- **Uso**: Identificar categorías más valiosas
- **Acción**: Asignar recursos proporcionalmente

---

### 7️⃣ ANÁLISIS DE DESEMPEÑO

#### **Productos Más Vendidos (Top 10)**
- **Descripción**: Los 10 productos con más unidades vendidas (30d)
- **Campos**: Nombre, SKU, Categoría, Unidades, Ingresos
- **Uso**: Identificar best-sellers
- **Acción**: Asegurar stock suficiente

#### **Productos con Bajo Stock**
- **Descripción**: Productos con menos de 10 unidades
- **Uso**: Prevenir roturas de stock
- **Acción**: Reposición prioritaria

---

### 8️⃣ ESTADO DEL STOCK

Clasificación por niveles de inventario:

- **Stock Alto (> 50 unidades)**: Productos bien abastecidos
- **Stock Medio (10-50 unidades)**: Nivel normal
- **Stock Bajo (1-9 unidades)**: ⚠️ Requiere reposición
- **Sin Stock (0 unidades)**: ❌ Crítico

---

## 🎨 Visualizaciones Disponibles

### Gráficos Principales

1. **Gráfico de Torta**: Distribución por Categoría
2. **Gráfico de Barras**: Estado del Stock (Alto/Medio/Bajo/Agotado)
3. **Lista**: Top 10 Productos Más Vendidos
4. **Lista**: Productos con Bajo Stock (alerta)

---

## 🔧 Filtros Disponibles

- **Por Categoría**: Ver métricas de categorías específicas
- **Por Estado**: Activo/Inactivo
- **Por Nivel de Stock**: Alto/Medio/Bajo/Agotado
- **Solo Activos**: Checkbox para filtrar

---

## 📊 Cómo Interpretar los Indicadores

### Escenario 1: Inventario Saludable ✅
- Rotación > 1.0
- Días inventario: 30-60 días
- Stock muerto < 5%
- Margen > 40%
- Roturas de stock = 0

### Escenario 2: Inventario Lento ⚠️
- Rotación < 0.5
- Días inventario > 90 días
- Stock muerto > 15%
- **Acción**: Revisar estrategia de compras

### Escenario 3: Riesgo de Quiebre ⚠️
- Días inventario < 15 días
- Roturas de stock > 5
- Stock bajo > 20%
- **Acción**: Acelerar reposición

---

## 🚀 Acciones Recomendadas por KPI

| KPI | Valor Crítico | Acción Recomendada |
|-----|---------------|-------------------|
| Stock Muerto | > 10% | Promociones, descuentos |
| Rotación | < 0.5 | Revisar catálogo y compras |
| Días Inventario | < 15 | Reposición urgente |
| Margen | < 30% | Revisar precios |
| Roturas Stock | > 3 | Mejorar planificación |
| Lotes por Vencer | > 0 | Liquidación inmediata |

---

## 📱 Exportación de Datos

El dashboard permite exportar:
- Reporte completo en Excel/PDF
- Datos filtrados según criterios
- Análisis por categoría

---

## 🔄 Actualización de Datos

- **Automática**: Cada 60 segundos
- **Manual**: Botón "Actualizar"
- **Datos en Tiempo Real**: Basados en sistema FIFO

---

## 💡 Mejores Prácticas

1. **Revisar diariamente**: Stock bajo y roturas
2. **Revisar semanalmente**: Rotación y ventas
3. **Revisar mensualmente**: Análisis ABC y stock muerto
4. **Configurar alertas**: Para valores críticos
5. **Tomar decisiones basadas en datos**: No en intuición

---

## 🎯 Beneficios del Dashboard

✅ **Visibilidad Total**: Todo el inventario en un solo lugar  
✅ **Decisiones Informadas**: Basadas en datos reales  
✅ **Prevención**: Detectar problemas antes que ocurran  
✅ **Rentabilidad**: Optimizar márgenes y rotación  
✅ **Eficiencia**: Reducir stock muerto y roturas  
✅ **Integración FIFO**: Costos reales y precisos  

---

## 📞 Notas Técnicas

- **Backend**: Django con queries optimizadas
- **Sistema de Costos**: FIFO (First In, First Out)
- **Performance**: Queries limitadas para rapidez
- **Seguridad**: Login requerido, permisos por rol
- **Escalabilidad**: Preparado para grandes volúmenes

---

**Última actualización**: Noviembre 2025  
**Versión**: 2.0 - Dashboard Completo con KPIs Avanzados

