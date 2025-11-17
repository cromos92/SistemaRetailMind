# 📊 Módulo de Reporte de Existencias - RetailMind

## Descripción

Sistema completo de generación de reportes de existencias de productos con visualización interactiva y exportación a Excel. El reporte muestra información detallada de inventario por producto y sucursal, incluyendo stocks, costos, costos FIFO y precios de venta.

## ✨ Características Implementadas

### 1. **Reporte HTML Interactivo**
- 📱 **Diseño Responsive**: Se adapta a cualquier dispositivo
- 🎨 **Interfaz Moderna**: Con gradientes y efectos visuales
- 📊 **Múltiples Vistas**:
  - **Vista General**: Todos los productos con información completa
  - **Por Sucursal**: Productos agrupados por alias de sucursal
  - **Por Producto**: Variantes agrupadas por artículo

### 2. **Filtros Avanzados**
- 🏪 Filtro por Sucursal
- 📦 Filtro por Categoría
- 🔍 Búsqueda por SKU/Artículo
- 📈 Filtro por Estado de Stock (Con Stock, Sin Stock, Bajo Stock)

### 3. **Tarjetas de Resumen**
- Total de Productos (SKUs únicos)
- Stock Total (Unidades disponibles)
- Valor de Inventario (Costo FIFO)
- Valor Potencial de Venta
- Productos Sin Stock
- Productos con Bajo Stock (<10 unidades)

### 4. **Información Detallada**
- **SKU**: Código único del producto/talla
- **Artículo**: Nombre del producto
- **Descripción**: Descripción completa
- **Talla/Alias**: Variante del producto
- **Sucursal**: Ubicación del inventario
- **Stock**: Unidades disponibles
- **Costo**: Costo base del producto
- **Costo FIFO**: Costo promedio ponderado de lotes disponibles
- **PVP**: Precio de venta al público
- **Valor Total**: Stock × Costo FIFO
- **Estado**: Disponible / Bajo Stock / Sin Stock

### 5. **Exportación a Excel**
Genera un archivo Excel profesional con 3 hojas:
- **Existencias General**: Vista completa de todos los productos
- **Por Sucursal**: Datos agrupados por sucursal
- **Resumen**: Métricas generales y resumen por sucursal

## 📁 Archivos Creados

### 1. **Template HTML**
```
retailmind/app/templates/vistas/reporte_existencias.html
```
- Interfaz completa con diseño moderno
- JavaScript integrado para manejo de datos
- Tablas dinámicas con agrupación
- Sistema de pestañas para múltiples vistas

### 2. **Vistas en Django**
```python
# En retailmind/app/views.py (líneas 10798-11188)

@login_required
def ver_reporte_existencias(request):
    """Vista principal del reporte de existencias"""
    
@login_required
@require_GET
def obtener_existencias_reporte(request):
    """API que retorna datos JSON de existencias"""
    
@login_required
@require_GET
def exportar_existencias_excel(request):
    """Exporta el reporte completo a Excel"""
```

### 3. **URLs**
```python
# En retailmind/app/urls.py (líneas 598-601)

path('reportes/existencias/', views.ver_reporte_existencias, name='ver_reporte_existencias'),
path('api/obtener-existencias/', views.obtener_existencias_reporte, name='obtener_existencias_reporte'),
path('api/exportar-existencias-excel/', views.exportar_existencias_excel, name='exportar_existencias_excel'),
```

## 🚀 Cómo Usar

### Opción 1: Acceso Directo por URL
```
http://localhost:8000/reportes/existencias/
```

### Opción 2: Agregar al Menú Principal
En tu template de navegación, agrega:
```html
<a href="{% url 'ver_reporte_existencias' %}" class="nav-link">
    📊 Reporte de Existencias
</a>
```

### Opción 3: Botón en Dashboard
```html
<a href="{% url 'ver_reporte_existencias' %}" class="btn btn-primary">
    Ver Reporte de Existencias
</a>
```

## 🎯 Flujo de Funcionamiento

1. **Usuario accede al reporte**: `/reportes/existencias/`
2. **Se carga el HTML** con la interfaz
3. **JavaScript hace llamada AJAX** a `/api/obtener-existencias/`
4. **Backend procesa datos**:
   - Obtiene todos los Producto_Talla
   - Calcula costo FIFO de lotes disponibles
   - Retorna JSON con datos y filtros
5. **Frontend renderiza**:
   - Tarjetas de resumen
   - Tablas con datos
   - Aplica filtros dinámicamente
6. **Usuario puede**:
   - Filtrar por sucursal/categoría/estado
   - Buscar productos específicos
   - Cambiar entre vistas (General/Sucursal/Producto)
   - Exportar a Excel
   - Imprimir reporte

## 📊 Estructura de Datos

### API Response (`/api/obtener-existencias/`)
```json
{
  "success": true,
  "existencias": [
    {
      "sku": "1001",
      "articulo": "ZAPATILLA NIKE AIR",
      "descripcion": "Zapatilla deportiva Nike Air Max",
      "talla": "42",
      "sucursal": "Casa Matriz",
      "sucursal_id": 1,
      "categoria": "Calzado",
      "categoria_id": 1,
      "stock": 15,
      "costo": 45000,
      "costo_fifo": 47000,
      "pvp": 85000
    }
  ],
  "sucursales": [
    {"id": 1, "alias": "Casa Matriz"},
    {"id": 2, "alias": "Sucursal Centro"}
  ],
  "categorias": [
    {"id": 1, "nombre": "Calzado"},
    {"id": 2, "nombre": "Vestuario"}
  ]
}
```

## 🔧 Cálculo de Costo FIFO

El sistema calcula el **costo FIFO** (costo promedio ponderado) de la siguiente manera:

```python
# Para cada Producto_Talla:
lotes_disponibles = LoteProducto.objects.filter(
    producto_talla=pt,
    cantidad_disponible__gt=0,
    activo=True
)

# Costo promedio ponderado
costo_fifo = Σ(costo_unitario × cantidad_disponible) / Σ(cantidad_disponible)

# Si no hay lotes, usa el costo base del producto
if not lotes_disponibles.exists():
    costo_fifo = producto.costo
```

## 📈 Indicadores de Estado

| Stock | Estado | Badge | Color |
|-------|--------|-------|-------|
| 0 | Sin Stock | 🔴 | Rojo |
| 1-9 | Bajo Stock | 🟡 | Amarillo |
| ≥10 | Disponible | 🟢 | Verde |

## 🎨 Características de Diseño

### Colores del Sistema
- **Primario**: Gradiente púrpura-azul (#667eea → #764ba2)
- **Éxito**: Verde (#28a745)
- **Advertencia**: Amarillo (#ffc107)
- **Peligro**: Rojo (#dc3545)
- **Información**: Azul claro (#0c5460)

### Elementos Interactivos
- ✅ Hover effects en tablas
- ✅ Animaciones de transición
- ✅ Loading states
- ✅ Empty states personalizados
- ✅ Responsive design

## 📱 Compatibilidad

- ✅ Chrome/Edge (últimas versiones)
- ✅ Firefox (últimas versiones)
- ✅ Safari (últimas versiones)
- ✅ Móviles (iOS/Android)
- ✅ Tablets
- ✅ Impresión (estilos optimizados)

## 🔐 Seguridad

- ✅ Requiere autenticación (`@login_required`)
- ✅ Validación de permisos por usuario
- ✅ Filtrado seguro con Django ORM
- ✅ Protección contra inyección SQL
- ✅ Sanitización de inputs

## 📦 Dependencias

### Python/Django
```python
# Ya incluidas en requirements.txt del proyecto
- django
- openpyxl  # Para exportación a Excel
```

### Frontend
- Vanilla JavaScript (sin dependencias externas)
- CSS3 moderno
- HTML5

## 🐛 Solución de Problemas

### Problema: No aparecen datos
**Solución**: Verificar que existan productos en la base de datos con:
```python
python manage.py shell
>>> from app.models import Producto_Talla
>>> Producto_Talla.objects.count()
```

### Problema: Error en exportación Excel
**Solución**: Instalar openpyxl:
```bash
pip install openpyxl
```

### Problema: Costo FIFO es 0
**Solución**: Verificar que existan lotes activos con stock disponible:
```python
>>> from app.models import LoteProducto
>>> LoteProducto.objects.filter(cantidad_disponible__gt=0).count()
```

## 🚀 Mejoras Futuras Sugeridas

1. **Gráficos de Análisis**
   - Gráfico de barras de stock por categoría
   - Gráfico de pie de distribución por sucursal
   - Tendencias de stock en el tiempo

2. **Filtros Adicionales**
   - Rango de fechas de creación
   - Filtro por marca/atributos
   - Filtro por rango de precios

3. **Exportaciones Adicionales**
   - PDF con formato profesional
   - CSV para análisis en Excel
   - Envío automático por email

4. **Alertas y Notificaciones**
   - Notificaciones de bajo stock
   - Alertas de productos sin movimiento
   - Sugerencias de reposición

5. **Comparativas**
   - Comparación entre sucursales
   - Evolución histórica de stock
   - Análisis de rotación de inventario

## 📞 Soporte

Para cualquier duda o problema con el módulo de existencias:
- Revisar este README
- Consultar los comentarios en el código
- Verificar los modelos en `app/models.py`

## ✅ Checklist de Implementación

- [x] Template HTML creado
- [x] Vistas Django implementadas
- [x] URLs configuradas
- [x] Cálculo de costo FIFO
- [x] Exportación a Excel
- [x] Filtros funcionales
- [x] Diseño responsive
- [x] Seguridad implementada
- [x] Documentación completa

---

**Desarrollado para RetailMind** 🚀
Sistema de Gestión Comercial Integral

