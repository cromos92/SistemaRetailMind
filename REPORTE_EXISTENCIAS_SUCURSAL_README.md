# 🏪 Reporte de Existencias por Sucursal - RetailMind

## Descripción

Reporte detallado de existencias que muestra **todas las tallas** de productos disponibles en una sucursal específica. Tipo listado completo con toda la información de cada SKU/talla.

## ✨ Características Principales

### 1. **Vista Detallada por Talla**
- **Una fila por cada talla** (no agrupado)
- Muestra información completa de cada SKU
- Ideal para inventarios físicos y auditorías

### 2. **Columnas del Reporte**
```
┌──────────┬────────────┬──────────┬───────┬───────┬────────┬───────┬────────┬───────┬───────┬─────────┬──────────┐
│ Artículo │Descripción │Categoría │ Marca │ Color │ Género │ Talla │Stock In│ Stock │ Costo │Sobrepre │PrecioVta │
├──────────┼────────────┼──────────┼───────┼───────┼────────┼───────┼────────┼───────┼───────┼─────────┼──────────┤
│ 890      │MALLA/LARGA │VESTUARIO │RIPHOL │MULTI  │SHORT   │  00   │   0    │   1   │ 6900  │  7935   │  13990   │
│M7652C-102│CT AS CORE  │ZAPATILLA │CONVER │WHITE  │LONA    │  4,0  │   56   │   1   │ 27858 │  29529  │  54990   │
└──────────┴────────────┴──────────┴───────┴───────┴────────┴───────┴────────┴───────┴───────┴─────────┴──────────┘
```

### 3. **Filtros Disponibles**
- 🏪 **Sucursal** (Obligatorio)
- 🏷️ **Marca** (Opcional con búsqueda)

### 4. **Tarjetas de Resumen**
- Total de Productos (SKUs con stock)
- Stock Total (unidades)
- Valor de Inventario ($)
- Productos Sin Stock

## 📁 Archivos Creados

### 1. Template HTML
```
retailmind/app/templates/vistas/modulo_reportes/reporte_existencias_sucursal.html
```

### 2. Vistas Django
```python
# En retailmind/app/views_modulo_reportes.py

@login_required
def ver_reporte_existencias_sucursal(request):
    """Vista principal del reporte"""

@require_GET
@login_required
def obtener_reporte_existencias_sucursal(request):
    """API que retorna datos JSON"""

@require_GET
@login_required
def exportar_existencias_sucursal_excel(request):
    """Exporta a Excel"""
```

### 3. URLs Configuradas
```python
# En retailmind/app/urls.py

path('reportes/existencias-sucursal/', ...)
path('api/reporte-existencias-sucursal/', ...)
path('api/exportar-existencias-sucursal-excel/', ...)
```

## 🚀 Cómo Usar

### Opción 1: Desde el Menú
1. **Módulo Reportes** → **Reportes Existencias** → **Existencias por Sucursal**

### Opción 2: URL Directa
```
http://localhost:8000/app/reportes/existencias-sucursal/
```

### Pasos de Uso:
1. **Selecciona una Sucursal** (obligatorio)
2. Opcionalmente busca y selecciona una Marca
3. Clic en **"Generar Reporte"**
4. Revisa el resumen y la tabla detallada
5. Exporta a Excel si lo necesitas

## 📊 Estructura de Datos

### Mapeo de Campos

| Columna | Origen en BD | Descripción |
|---------|--------------|-------------|
| Artículo | `Producto.articulo` | Código del producto |
| Descripción | `Producto.descripcion` | Descripción del producto |
| Categoría | `Producto.categoria.nombre` | Categoría (Cat1) |
| Marca | `Producto.atributo1.valor` | Marca (Cat2) |
| Color | `Producto.atributo2.valor` | Color (Cat3) |
| Género | `Producto.atributo3.valor` | Género u otro (Cat4) |
| Talla | `Producto_Talla.talla` | Talla/variante |
| Stock Inicial | `Producto_Talla.stock` | Stock legacy (inicial = actual) |
| Stock | `Producto_Talla.stock` | Stock actual |
| Costo | `Producto.costo` | Costo unitario |
| Sobreprecio | `Producto.sobreprecio` | Sobreprecio unitario |
| Precio Venta | `Producto.precioventa` | PVP |

### Respuesta JSON de la API

```json
{
  "success": true,
  "datos": [
    {
      "articulo": "890",
      "descripcion": "MALLA/LARGA",
      "categoria": "VESTUARIO",
      "marca": "RIPHOLIA",
      "color": "MULTI",
      "genero": "SHORT Y PATAS",
      "talla": "00",
      "stock_inicial": 1,
      "stock": 1,
      "costo": 6900,
      "sobreprecio": 7935,
      "precio_venta": 13990
    }
  ],
  "resumen": {
    "total_productos": 450,
    "stock_total": 1250,
    "valor_inventario": 15500000,
    "sin_stock": 85,
    "sucursal": "EDEL"
  }
}
```

## 🎯 Casos de Uso

### 1. Inventario Físico
- Genera el reporte de una sucursal
- Exporta a Excel
- Imprime y usa para contar físicamente
- Compara con cantidades reales

### 2. Auditoría de Stock
- Verifica stock por sucursal
- Identifica productos sin movimiento
- Revisa valor de inventario

### 3. Reposición
- Filtra por marca
- Identifica stock bajo
- Genera órdenes de compra

### 4. Análisis por Marca
- Selecciona una marca específica
- Revisa todas sus tallas
- Analiza rotación

## 🎨 Diferencias con Otros Reportes

| Característica | Por Marca | Por Sucursal |
|----------------|-----------|--------------|
| **Agrupación** | Por marca | No agrupado |
| **Formato** | Una fila por producto | Una fila por talla |
| **Sucursales** | Columnas dinámicas | Una sucursal fija |
| **Filtro principal** | Marca | Sucursal |
| **Uso** | Comparar sucursales | Detalle de inventario |
| **Totales** | Por columna | Al final |

## 🔐 Seguridad

- ✅ Requiere autenticación (`@login_required`)
- ✅ Solo muestra productos con stock > 0
- ✅ Filtra por sucursal del producto
- ✅ Validación de parámetros
- ✅ Protección contra inyección SQL

## 📦 Dependencias

```python
# Ya incluidas en requirements.txt
- django
- openpyxl  # Para Excel
```

## 🐛 Solución de Problemas

### No aparecen productos
**Causa**: La sucursal no tiene productos asignados  
**Solución**: Verificar que `Producto.sucursal_id` = sucursal seleccionada

### Stock siempre es igual a Stock Inicial
**Causa**: Sistema legacy sin lotes FIFO  
**Solución**: Es normal, se muestra el campo `stock` como ambos valores

### No se puede seleccionar sucursal
**Causa**: No hay sucursales en el sistema  
**Solución**: Verificar tabla `Sucursal`

## 💡 Tips de Uso

1. **Para inventario físico**: Exporta a Excel e imprime
2. **Para comparar marcas**: Genera varios reportes por marca
3. **Para auditoría**: Ordena por valor en Excel
4. **Para reposición**: Filtra stock bajo en Excel

## 🚀 Mejoras Futuras

1. **Filtros adicionales**:
   - Por categoría
   - Por rango de stock
   - Por género

2. **Ordenamiento**:
   - Por stock ascendente/descendente
   - Por valor de inventario
   - Por marca/categoría

3. **Vistas adicionales**:
   - Incluir productos sin stock (checkbox)
   - Ver solo stock bajo
   - Ver solo stock crítico

4. **Exportaciones**:
   - PDF para impresión
   - CSV simple
   - Código de barras

## 📞 Soporte

Para dudas o problemas:
- Revisar este README
- Consultar comentarios en el código
- Verificar logs en consola

## ✅ Checklist de Implementación

- [x] Template HTML creado
- [x] Vista principal implementada
- [x] API de datos implementada
- [x] Exportación a Excel funcional
- [x] URLs configuradas
- [x] Enlace en menú agregado
- [x] Filtros funcionales
- [x] Diseño responsive
- [x] Tarjetas de resumen
- [x] Documentación completa

---

**Desarrollado para RetailMind** 🚀  
Sistema de Gestión Comercial Integral

**Versión**: 1.0  
**Fecha**: Noviembre 2024

