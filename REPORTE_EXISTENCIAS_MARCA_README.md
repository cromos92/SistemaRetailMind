# 📊 Reporte de Existencias por Marca con Lotes - RetailMind

## Descripción

Sistema completo de generación de reportes de existencias agrupados por **marca**, mostrando el detalle de **lotes** (FIFO) con cantidades iniciales y stock disponible actual. Este reporte permite visualizar de manera clara el inventario disponible por cada lote recibido.

## ✨ Características Principales

### 1. **Agrupación por Marca**
- Los productos se agrupan automáticamente por marca
- Cada marca tiene su propia sección en el reporte
- Se muestra el total de stock por marca

### 2. **Detalle de Lotes**
Cada producto muestra:
- **Artículo**: Nombre del producto
- **Color**: Color del producto (atributo2)
- **Depart**: Departamento/Categoría
- **Costo**: Costo base del producto
- **PrecioV.**: Precio de venta
- **Lote 1, Lote 2, Lote N...**: Cada lote recibido con:
  - **Inicial**: Cantidad original del lote
  - **Stk**: Stock actual disponible
- **TOTAL**: Suma de todos los lotes

### 3. **Filtros Disponibles**
- 🏷️ **Filtro por Marca**: Ver solo productos de una marca específica
- 📦 **Filtro por Departamento**: Filtrar por categoría/departamento
- 🔄 **Actualización en tiempo real**

### 4. **Exportación a Excel**
- Genera archivo Excel con formato profesional
- Mantiene los colores y estilos del reporte
- Agrupa por marca con encabezados destacados
- Formato de tabla con bordes y colores

## 📁 Archivos Creados

### 1. Template HTML
```
retailmind/app/templates/vistas/modulo_reportes/reporte_existencias_marca.html
```
- Interfaz moderna y responsive
- Tablas dinámicas con JavaScript
- Sistema de filtros interactivos

### 2. Vistas Django
```python
# En retailmind/app/views_modulo_reportes.py

@login_required
def ver_reporte_existencias_marca(request):
    """Vista principal del reporte de existencias por marca"""

@require_GET
@login_required
def obtener_reporte_existencias_marca(request):
    """API que retorna datos JSON del reporte"""

@require_GET
@login_required
def exportar_existencias_marca_excel(request):
    """Exporta el reporte a Excel con formato"""
```

### 3. URLs Configuradas
```python
# En retailmind/app/urls.py

path('reportes/existencias-marca/', views_modulo_reportes.ver_reporte_existencias_marca, name='ver_reporte_existencias_marca'),
path('api/reporte-existencias-marca/', views_modulo_reportes.obtener_reporte_existencias_marca, name='obtener_reporte_existencias_marca'),
path('api/exportar-existencias-marca-excel/', views_modulo_reportes.exportar_existencias_marca_excel, name='exportar_existencias_marca_excel'),
```

## 🚀 Cómo Usar

### Opción 1: Acceso desde el Menú
1. En el menú lateral, ir a **"Módulo Reportes"**
2. Expandir **"Reportes Existencias"**
3. Clic en **"Existencias por Marca"**

### Opción 2: Acceso Directo por URL
```
http://localhost:8000/app/reportes/existencias-marca/
```

### Opción 3: En producción
```
https://tu-dominio.com/app/reportes/existencias-marca/
```

## 📊 Estructura de la Tabla

### Ejemplo Visual

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MARCA: NIKE                                         │
├──────────┬────────┬────────┬────────┬─────────┬────────────┬──────────┬─────┤
│ Artículo │ Color  │ Depart │ Costo  │PrecioV. │   Lote 1   │  Lote 2  │TOTAL│
│          │        │        │        │         ├──────┬─────┼─────┬────┼─────┤
│          │        │        │        │         │Inic. │ Stk │Inic.│Stk │ Stk │
├──────────┼────────┼────────┼────────┼─────────┼──────┼─────┼─────┼────┼─────┤
│Zapatilla │ Negro  │Calzado │$45,000 │ $85,000 │  20  │ 15  │ 30  │ 25 │ 40  │
│Air Max   │        │        │        │         │      │     │     │    │     │
├──────────┼────────┼────────┼────────┼─────────┼──────┼─────┼─────┼────┼─────┤
│Polera    │ Blanco │Vestuario│$12,000│ $22,000 │  50  │ 35  │  -  │ -  │ 35  │
│Running   │        │        │        │         │      │     │     │    │     │
└──────────┴────────┴────────┴────────┴─────────┴──────┴─────┴─────┴────┴─────┘
```

## 🎯 Flujo de Funcionamiento

1. **Usuario accede al reporte**: `/app/reportes/existencias-marca/`
2. **Se carga la interfaz HTML** con filtros
3. **JavaScript hace llamada AJAX** a `/app/api/reporte-existencias-marca/`
4. **Backend procesa**:
   - Obtiene productos activos
   - Filtra por marca/departamento si se especifica
   - Para cada producto, obtiene sus lotes FIFO activos
   - Calcula cantidades iniciales y stock disponible
   - Agrupa por marca
5. **Frontend renderiza**:
   - Tablas agrupadas por marca
   - Columnas dinámicas según número de lotes
   - Colorea stock bajo en rojo, stock ok en verde
6. **Usuario puede**:
   - Filtrar por marca específica
   - Filtrar por departamento
   - Exportar a Excel
   - Imprimir reporte

## 📈 Estructura de Datos JSON

### Respuesta de la API

```json
{
  "success": true,
  "datos": [
    {
      "articulo": "ZAPATILLA NIKE AIR",
      "marca": "NIKE",
      "marca_id": 5,
      "color": "Negro",
      "departamento": "Calzado",
      "costo": 45000,
      "precio_venta": 85000,
      "sku": "ZAP-NIKE-001",
      "talla": "42",
      "lotes": [
        {
          "numero_lote": "LOTE-001",
          "inicial": 20,
          "stock": 15,
          "costo": 45000,
          "fecha": "15/10/2024"
        },
        {
          "numero_lote": "LOTE-002",
          "inicial": 30,
          "stock": 25,
          "costo": 47000,
          "fecha": "20/10/2024"
        }
      ]
    }
  ]
}
```

## 🎨 Características de Diseño

### Colores del Sistema
- **Header Marca**: Gradiente púrpura-azul (#667eea → #764ba2)
- **Lotes**: Fondo azul claro (#E7F1FF)
- **Total**: Fondo amarillo (#FFF3CD)
- **Stock Bajo** (<5): Texto rojo (#dc3545)
- **Stock OK** (≥5): Texto verde (#28a745)

### Elementos Visuales
- ✅ Tablas con bordes y hover effects
- ✅ Columnas dinámicas según número de lotes
- ✅ Agrupación visual por marca
- ✅ Responsive design para móviles
- ✅ Impresión optimizada

## 💡 Ejemplo de Uso

### Caso 1: Ver todas las marcas
1. Acceder al reporte
2. Dejar filtros en "Todas las marcas" y "Todos"
3. Clic en "Actualizar Reporte"
4. Se muestran todas las marcas con sus productos

### Caso 2: Ver solo NIKE
1. Acceder al reporte
2. En "Filtrar por Marca" seleccionar "NIKE"
3. Clic en "Actualizar Reporte"
4. Se muestran solo productos de marca NIKE

### Caso 3: Exportar a Excel
1. Aplicar los filtros deseados
2. Clic en "📥 Exportar a Excel"
3. Se descarga archivo `reporte_existencias_marca.xlsx`
4. Abrir en Excel/LibreOffice

## 🔐 Seguridad

- ✅ Requiere autenticación (`@login_required`)
- ✅ Solo muestra productos activos
- ✅ Filtra según permisos de usuario
- ✅ Validación de parámetros
- ✅ Protección contra inyección SQL

## 📦 Dependencias

### Python/Django
```python
# Ya incluidas en requirements.txt
- django
- openpyxl  # Para exportación a Excel
```

### Frontend
- Vanilla JavaScript (sin dependencias externas)
- CSS3 moderno
- HTML5

## 🐛 Solución de Problemas

### Problema: No aparecen productos
**Causa**: No hay productos con lotes activos
**Solución**: Verificar que existan lotes con stock disponible:
```python
python manage.py shell
>>> from app.models import LoteProducto
>>> LoteProducto.objects.filter(activo=True, cantidad_disponible__gt=0).count()
```

### Problema: No se muestran marcas
**Causa**: Los productos no tienen atributo1 (marca) asignado
**Solución**: Asignar marcas a los productos desde el módulo de productos

### Problema: Error al exportar Excel
**Causa**: openpyxl no instalado
**Solución**: 
```bash
pip install openpyxl
```

### Problema: Lotes no aparecen
**Causa**: Lotes inactivos o sin stock
**Solución**: Verificar que los lotes tengan:
- `activo = True`
- `cantidad_disponible > 0`

## 🚀 Mejoras Futuras Sugeridas

1. **Filtros Adicionales**
   - Filtro por rango de fechas de lotes
   - Filtro por sucursal
   - Filtro por proveedor

2. **Visualizaciones**
   - Gráfico de barras de stock por marca
   - Gráfico de pie de distribución
   - Tendencias de stock

3. **Exportaciones**
   - PDF con formato profesional
   - CSV para análisis
   - Envío automático por email

4. **Análisis**
   - Rotación de inventario por marca
   - Alertas de stock bajo por marca
   - Comparativa entre marcas

5. **Personalización**
   - Configurar número de lotes a mostrar
   - Ocultar/mostrar columnas
   - Guardar filtros preferidos

## 📞 Soporte

Para dudas o problemas:
- Revisar este README
- Consultar comentarios en el código
- Verificar modelos en `app/models.py`
- Revisar logs en consola del navegador

## ✅ Checklist de Implementación

- [x] Template HTML creado
- [x] Vista principal implementada
- [x] API de datos implementada
- [x] Exportación a Excel funcional
- [x] URLs configuradas
- [x] Enlace en menú agregado
- [x] Filtros funcionales
- [x] Diseño responsive
- [x] Seguridad implementada
- [x] Documentación completa

## 📝 Notas Importantes

1. **Sistema FIFO**: El reporte respeta el sistema FIFO, mostrando los lotes en orden de fecha de creación
2. **Stock Dinámico**: El stock se calcula en tiempo real al momento de generar el reporte
3. **Performance**: Para grandes cantidades de productos, considerar agregar paginación o límites
4. **Lotes**: Solo se muestran productos que tengan al menos un lote activo con stock disponible

---

**Desarrollado para RetailMind** 🚀
Sistema de Gestión Comercial Integral

**Versión**: 1.0
**Fecha**: Noviembre 2024

