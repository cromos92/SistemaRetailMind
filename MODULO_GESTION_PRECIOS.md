# 📊 MÓDULO DE GESTIÓN DE PRECIOS - RetailMind

## ✅ IMPLEMENTACIÓN COMPLETADA

Sistema avanzado de gestión de precios con **recomendaciones inteligentes** basadas en análisis de múltiples factores.

---

## 🎯 FUNCIONALIDADES IMPLEMENTADAS

### 1. **Búsqueda Avanzada de Productos**

**Criterios de Búsqueda:**
- ✅ Búsqueda por texto (nombre, SKU, descripción)
- ✅ Filtro por categoría
- ✅ Filtro por marca (atributo1)
- ✅ Filtro por sucursal
- ✅ Rango de precios de venta (mín/máx)
- ✅ Margen mínimo (%)
- ✅ Stock mínimo
- ✅ Antigüedad del inventario (nuevo/medio/antiguo)
- ✅ Año de ingreso al inventario

**Características:**
- Paginación eficiente (50 productos por página)
- Resultados en tiempo real
- Vista de tabla interactiva con columnas ordenables
- Indicadores visuales de estado (stock, margen, antigüedad)

---

### 2. **Sistema de Recomendaciones Inteligentes** 🤖

El sistema analiza **múltiples factores** para sugerir el precio óptimo:

#### **Factores Analizados:**

**A. Antigüedad del Inventario**
```
> 365 días: -20% (descuento del 20%)
180-365 días: -10% (descuento del 10%)
90-180 días: -5% (descuento del 5%)
< 90 días: Sin ajuste
```

**B. Rotación de Inventario**
```
Sin ventas: -15% (producto estancado)
Rotación muy rápida (< 30 días): +5% (alta demanda)
Rotación normal (30-90 días): Sin ajuste
Rotación lenta (90-180 días): -5%
Muy lenta (> 180 días): -10%
```

**C. Tendencia de Ventas**
```
Compara últimos 30 días vs período anterior:
- Creciente (>+20%): Mantener o subir precio
- Decreciente (<-20%): Ajuste adicional -5%
- Estable: Sin ajuste por tendencia
```

**D. Nivel de Stock**
```
< 3 unidades: +10% (escasez)
3-10 unidades: Sin ajuste
10-50 unidades: -5% (stock elevado)
> 50 unidades: -10% (exceso)
```

#### **Cálculo del Precio Recomendado:**

1. **Factor Total** = Antigüedad + Rotación + Stock
2. **Precio Ajustado** = Precio Actual × (1 + Factor Total)
3. **Validación Margen Mínimo** (10%)
4. **Redondeo Psicológico** (termina en 490 o 990)

#### **Ejemplo Real:**

```javascript
Producto: Zapatillas Nike Air Max
Precio Actual: $59,990
Costo Promedio: $35,000
Antigüedad: 280 días → Factor: -10%
Ventas 30 días: 0 unidades → Factor: -15%
Stock: 45 unidades → Factor: -5%

Factor Total: -30%
Precio Sugerido: $41,990
Nuevo Margen: 16.7%

Justificación: "Ajuste por: Inventario antiguo, Sin ventas recientes, Stock elevado"
```

---

### 3. **Modificación Masiva de Precios**

**Tipos de Modificación:**

#### **A. Precio Fijo**
Establece un precio específico para todos los productos seleccionados.

```javascript
Ejemplo:
- Seleccionar 50 productos
- Tipo: Precio Fijo
- Valor: $9,990
→ Todos los productos quedan a $9,990
```

#### **B. Incremento/Decremento Porcentual**
Ajusta el precio en un porcentaje (positivo o negativo).

```javascript
Ejemplo 1 - Aumento:
- Precio actual: $10,000
- Incremento: +15%
- Nuevo precio: $11,500

Ejemplo 2 - Descuento:
- Precio actual: $10,000
- Descuento: -20%
- Nuevo precio: $8,000
```

#### **C. Incremento/Decremento en Pesos**
Ajusta el precio en un monto fijo.

```javascript
Ejemplo:
- Precio actual: $10,000
- Incremento: +$2,000
- Nuevo precio: $12,000
```

#### **D. Por Margen de Ganancia Objetivo**
Calcula el precio necesario para lograr un margen específico.

```javascript
Ejemplo:
- Costo: $7,000
- Margen objetivo: 35%
- Cálculo: Precio = Costo / (1 - Margen/100)
- Precio = $7,000 / (1 - 0.35) = $10,769
- Nuevo precio: $10,990 (redondeado)
```

**Validaciones:**
- ✅ Precio mínimo = Costo + 10% de margen
- ✅ Confirmación antes de aplicar cambios masivos
- ✅ Actualización atómica (todo o nada)

---

### 4. **Sincronización Multi-Sucursal**

Sincroniza precios de productos **similares** entre sucursales.

**Funcionamiento:**

1. **Identificación de Productos Similares:**
   - Mismo nombre (articulo)
   - Mismo atributo1 (marca)
   - Mismo atributo2 (color)
   - Diferente sucursal

2. **Ajuste por Zona:**
   ```javascript
   Ejemplo:
   Producto origen (Sucursal Centro): $10,000
   Ajuste por zona: +10%
   Precio en sucursales destino: $11,000
   
   Casos de uso:
   - Sucursales en zonas caras: +10% a +15%
   - Sucursales en zonas populares: -5% a -10%
   - Sin ajuste: 0%
   ```

3. **Aplicación:**
   - Actualiza precio base del producto
   - Actualiza todos los lotes activos
   - Mantiene estructura de costos original

---

### 5. **Dashboard de Inventario Antiguo**

Análisis detallado de productos con inventario antiguo y **sugerencias automáticas de descuento**.

**Categorías de Antigüedad:**

| Antigüedad | Categoría | Descuento Sugerido |
|------------|-----------|-------------------|
| > 730 días (2 años) | Crítico | 40% |
| 365-730 días (1-2 años) | Crítico | 25% |
| 180-365 días (6-12 meses) | Atención | 15% |

**Información Mostrada:**
- SKU y nombre del producto
- Número de lote
- Cantidad en inventario
- Días de antigüedad
- Precio actual
- Costo unitario
- Valor total del inventario
- **Descuento sugerido automático**
- **Precio sugerido**

**Resumen:**
- Total de productos antiguos
- Valor total inmovilizado
- Promedio de días en inventario

---

## 📁 ARCHIVOS CREADOS

### 1. **Vista HTML**
```
retailmind/app/templates/vistas/modulo_existencias/gestion_precios.html
```

**Características:**
- Diseño moderno y responsivo
- Interfaz intuitiva con filtros colapsables
- Tabla interactiva con edición inline
- Modales para recomendaciones y acciones masivas
- Sistema de paginación
- Indicadores visuales (badges de color)

### 2. **Backend (Vistas)**
```
retailmind/app/views_modulo_gestion_precios.py
```

**Endpoints Implementados:**

| Endpoint | Método | Descripción |
|----------|--------|-------------|
| `/gestion-precios/` | GET | Vista principal |
| `/gestion-precios/estadisticas/` | GET | Estadísticas del dashboard |
| `/gestion-precios/buscar/` | GET | Búsqueda con filtros |
| `/gestion-precios/recomendaciones/<id>/` | GET | Recomendaciones inteligentes |
| `/gestion-precios/actualizar-precio/` | POST | Actualizar precio individual |
| `/gestion-precios/modificacion-masiva/` | POST | Modificación masiva |
| `/gestion-precios/sincronizar-sucursales/` | POST | Sincronización multi-sucursal |
| `/gestion-precios/inventario-antiguo/` | GET | Análisis de inventario antiguo |
| `/api/categorias/listar/` | GET | Listar categorías |
| `/api/atributos/listar/` | GET | Listar atributos (marcas) |
| `/api/sucursales/listar/` | GET | Listar sucursales |

### 3. **Rutas URL**
Agregadas en: `retailmind/app/urls.py`

---

## 🗄️ MODELOS UTILIZADOS (Sin crear nuevos)

El sistema utiliza los modelos existentes:

### **Producto**
```python
- articulo (nombre)
- descripcion
- atributo1, atributo2, atributo3, atributo4
- categoria (FK)
- sucursal (FK)
- costo
- sobreprecio
- precioventa
- precioSugerido
```

### **Producto_Talla**
```python
- producto (FK)
- sku
- stock
- talla
```

### **LoteProducto** (FIFO)
```python
- producto_talla (FK)
- cantidad_inicial
- cantidad_disponible
- costo_unitario
- precio_venta_unitario
- fecha_ingreso ← CLAVE para antigüedad
- activo
```

### **Ticket_Productos** (para análisis de ventas)
```python
- ProductoTalla (FK)
- idTicket (FK)
- stock (cantidad vendida)
- precio
```

---

## 🎓 CONCEPTOS PARA DETERMINAR "AÑO" DEL PRODUCTO

### **Campo Principal: `LoteProducto.fecha_ingreso`**

Este es el campo **más importante** para determinar la antigüedad:

```python
# Ejemplo de consulta
lotes_2023 = LoteProducto.objects.filter(
    fecha_ingreso__year=2023,
    cantidad_disponible__gt=0
)
```

### **Cálculo de Antigüedad**
```python
dias_inventario = (timezone.now() - lote.fecha_ingreso).days

Categorías:
- Nuevo: < 180 días (6 meses)
- Medio: 180-365 días (6-12 meses)
- Antiguo: > 365 días (1+ año)
```

### **Otros Campos Útiles:**

1. **`Compras.temporada`** (string)
   - "Verano 2024", "Invierno 2023", etc.
   - Útil para clasificación estacional

2. **`Compras.fechaInicioTemporada` / `fechaTerminoTemporada`**
   - Rango de fechas de la temporada
   - Permite asociar productos a períodos

3. **`Productos_Recepcionados.fecha_recepcion`**
   - Fecha de recepción del proveedor
   - Útil para tracking de compras

---

## 🚀 GUÍA DE USO

### **1. Acceder al Módulo**
```
URL: /gestion-precios/
```

### **2. Buscar Productos**
1. Usar filtros en el panel superior
2. Click en "Buscar Productos"
3. Revisar resultados en la tabla

### **3. Ver Recomendaciones de Precio**
1. Click en el ícono 💡 en la columna "Acciones"
2. Revisar análisis completo:
   - Precio actual vs recomendado
   - Factores de ajuste
   - Justificación
3. Click en "Aplicar Precio Recomendado" si está de acuerdo

### **4. Modificación Masiva**
1. Seleccionar productos (checkbox)
2. Click en "Modificar Seleccionados"
3. Elegir tipo de modificación
4. Ingresar valor
5. Confirmar cambios

### **5. Sincronizar Sucursales**
1. Seleccionar productos de sucursal origen
2. Click en "Sincronizar Sucursales"
3. Seleccionar sucursales destino
4. Configurar ajuste porcentual (opcional)
5. Confirmar sincronización

### **6. Análisis de Inventario Antiguo**
```
URL: /gestion-precios/inventario-antiguo/
```
API que retorna productos con más de 6 meses de antigüedad y sugerencias de descuento.

---

## 📊 EJEMPLOS DE RECOMENDACIONES

### **Caso 1: Producto con Alta Rotación**
```
Producto: Polera Adidas Negra
Stock: 5 unidades
Ventas últimos 30 días: 12 unidades
Antigüedad: 45 días

Análisis:
✅ Producto nuevo (sin penalización)
✅ Alta rotación (+5%)
✅ Stock bajo (+10%)

Recomendación: AUMENTAR PRECIO
Precio actual: $15,990
Precio sugerido: $18,490
Justificación: "Alta rotación, Stock limitado"
```

### **Caso 2: Producto Estancado**
```
Producto: Chaqueta Nike Roja
Stock: 38 unidades
Ventas últimos 30 días: 0 unidades
Antigüedad: 420 días

Análisis:
❌ Inventario antiguo (-10%)
❌ Sin ventas (-15%)
❌ Stock elevado (-5%)

Recomendación: BAJAR PRECIO
Precio actual: $45,990
Precio sugerido: $31,990
Justificación: "Inventario antiguo, Sin ventas recientes, Stock elevado"
```

### **Caso 3: Producto con Demanda Creciente**
```
Producto: Zapatillas Puma Runner
Stock: 18 unidades
Ventas últimos 30 días: 8 unidades
Ventas período anterior: 3 unidades
Antigüedad: 120 días

Análisis:
✅ Inventario fresco (sin penalización)
✅ Tendencia creciente (+167%)
⚖️ Stock normal (sin ajuste)

Recomendación: MANTENER O SUBIR LIGERAMENTE
Precio actual: $32,990
Precio sugerido: $33,990
Justificación: "Tendencia de ventas creciente"
```

---

## 💡 MEJORES PRÁCTICAS

### **1. Revisión Periódica**
- Revisar precios semanalmente
- Enfocarse en productos antiguos (> 6 meses)
- Analizar productos sin ventas recientes

### **2. Uso de Recomendaciones**
- Usar como guía, no regla absoluta
- Considerar factores externos (temporada, competencia)
- Ajustar según estrategia de negocio

### **3. Modificación Masiva**
- Agrupar productos similares
- Aplicar cambios por categoría o marca
- Revisar antes de confirmar

### **4. Sincronización Multi-Sucursal**
- Considerar diferencias de zona
- Ajustar precios según costos logísticos
- Mantener coherencia de marca

### **5. Gestión de Inventario Antiguo**
- Revisar mensualmente
- Aplicar descuentos escalonados
- Considerar promociones especiales

---

## 🔧 CONFIGURACIÓN Y PERSONALIZACIÓN

### **Ajustar Factores de Recomendación**

En `views_modulo_gestion_precios.py`, líneas 285-345:

```python
# Factor Antigüedad (personalizable)
if dias_promedio > 365:
    factor_antiguedad = -0.20  # Cambiar según política
elif dias_promedio > 180:
    factor_antiguedad = -0.10
# ... etc

# Factor Rotación (personalizable)
if ventas_30_dias == 0:
    factor_rotacion = -0.15  # Ajustar penalización
elif dias_para_agotar < 30:
    factor_rotacion = 0.05   # Ajustar bonificación
# ... etc
```

### **Ajustar Margen Mínimo**

Línea 370:
```python
# Margen mínimo del 10% (cambiar según política)
precio_minimo_margen = costo_promedio / 0.90
```

### **Ajustar Redondeo Psicológico**

Líneas 374-380:
```python
# Cambiar terminaciones según preferencia
if ultimo_digito < 490:
    precio_recomendado = (precio_recomendado // 1000) * 1000 + 490
else:
    precio_recomendado = (precio_recomendado // 1000) * 1000 + 990
```

---

## 🎯 PRÓXIMAS MEJORAS SUGERIDAS

1. **Exportación a Excel**
   - Exportar resultados de búsqueda
   - Exportar análisis de inventario antiguo

2. **Historial de Cambios**
   - Registrar todos los cambios de precio
   - Auditoría de modificaciones

3. **Alertas Automáticas**
   - Notificaciones de productos estancados
   - Alertas de inventario antiguo

4. **Comparación con Competencia**
   - Integración con scraping de precios
   - Benchmarking automático

5. **Reglas de Pricing Automático**
   - Programar cambios de precio por fechas
   - Promociones automáticas

6. **Dashboard Ejecutivo**
   - Gráficos de evolución de precios
   - Análisis de márgenes por categoría

---

## ✅ RESUMEN DE IMPLEMENTACIÓN

| Componente | Estado | Archivo |
|------------|--------|---------|
| Vista HTML | ✅ Completo | `gestion_precios.html` |
| Backend API | ✅ Completo | `views_modulo_gestion_precios.py` |
| Rutas URL | ✅ Completo | `urls.py` (actualizado) |
| Búsqueda Avanzada | ✅ Completo | 10 criterios de filtrado |
| Recomendaciones IA | ✅ Completo | 4 factores de análisis |
| Modificación Masiva | ✅ Completo | 4 tipos de modificación |
| Sincronización | ✅ Completo | Multi-sucursal con ajustes |
| Inventario Antiguo | ✅ Completo | Análisis + sugerencias |
| Endpoints Auxiliares | ✅ Completo | Categorías, Marcas, Sucursales |

---

## 📞 SOPORTE

Para cualquier duda o mejora adicional, referirse a este documento y al código fuente comentado.

**Sistema desarrollado por:** RetailMind Team  
**Fecha:** Noviembre 2025  
**Versión:** 1.0.0

---

**¡El módulo está listo para usar! 🚀**

