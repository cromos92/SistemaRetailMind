# 🔍 DEBUG: Modal de Edición de Productos

## 🎯 Problemas Reportados

1. ❌ No se cargan los atributos (marca, color, género) en los selects
2. ❌ La pestaña "Variaciones / Tallas" no muestra nada

## 🔧 Sistema de Debug Implementado

He agregado **logs de consola** para ver exactamente qué datos está recibiendo el modal.

### Cómo Usar el Debug

1. **Refrescar la página**:
   ```
   Ctrl + Shift + R
   ```

2. **Abrir consola del navegador**:
   ```
   Presionar F12
   Ir a pestaña "Console"
   ```

3. **Probar edición**:
   ```
   1. Clic en "Edición Productos"
   2. Buscar: "m91"
   3. Clic en "Editar" de cualquier producto
   ```

4. **Revisar logs en consola**:
   
   Deben aparecer estos mensajes:
   ```javascript
   Datos recibidos del backend: {success: true, producto: {...}, variaciones: [...]}
   Producto: {id: 67970, articulo: "M9160C", ...}
   Variaciones: [...]
   ```

---

## 📊 Qué Verificar en los Logs

### 1. Verificar Datos del Producto

En la consola, busca el objeto `Producto:` y verifica:

```javascript
Producto: {
    id: 67970,
    articulo: "M9160C",
    descripcion: "Chuck Taylor All Star",
    categoria_id: 123,
    categoria_nombre: "Accesorios",
    
    // VERIFICAR ESTOS:
    atributo1_id: ???,        // ¿Tiene valor?
    atributo1_nombre: ???,    // ¿Tiene nombre?
    atributo2_id: ???,        // ¿Tiene valor?
    atributo2_nombre: ???,    // ¿Tiene nombre?
    atributo3_id: ???,        // ¿Tiene valor?
    atributo3_nombre: ???,    // ¿Tiene nombre?
    
    costo: 5000,
    sobreprecio: 5500,
    precioventa: 5990,
    ...
}
```

### 2. Verificar Variaciones

En la consola, busca el array `Variaciones:` y verifica:

```javascript
Variaciones: [
    {
        id: 123,
        sku: 4747892,
        talla: "38",
        stock_total: 15,
        stock_db: 15,
        lotes: [...]
    },
    // ... más variaciones
]
```

---

## ✅ Escenarios Posibles

### Escenario 1: atributo1_id es null o undefined

```javascript
Producto: {
    atributo1_id: null,        // ← PROBLEMA
    atributo1_nombre: "",
    ...
}
```

**Causa**: El producto en la BD no tiene atributos asignados  
**Solución**: Es normal, el producto puede no tener marca/color asignados

**Cómo verificar en BD**:
```bash
python manage.py shell
>>> from app.models import Producto
>>> p = Producto.objects.get(id=67970)
>>> print(p.atributo1)  # Debe mostrar el objeto o None
>>> print(p.atributo2)
>>> print(p.atributo3)
```

### Escenario 2: Variaciones es array vacío

```javascript
Variaciones: []  // ← PROBLEMA
```

**Causa**: El producto no tiene tallas/variaciones asociadas  
**Solución**: Verificar en BD:

```bash
python manage.py shell
>>> from app.models import Producto
>>> p = Producto.objects.get(id=67970)
>>> p.producto_talla.all()  # Debe mostrar QuerySet con tallas
>>> p.producto_talla.count()  # Cantidad de tallas
```

### Escenario 3: Datos llegan pero no se muestran

```javascript
Producto: {
    atributo1_id: 25,
    atributo1_nombre: "TORPEDO",  // ← Datos correctos
    ...
}
```

**Causa**: Los selects están vacíos o tienen problemas  
**Solución**: La lógica mejorada debería agregar las opciones automáticamente

---

## 🔧 Soluciones Implementadas

### 1. Carga Dinámica de Atributos

Ahora el código hace esto:

```javascript
// Si el option no existe en el select, lo crea
if (producto.atributo1_id && producto.atributo1_nombre) {
    const select1 = $('#edit_atributo1_id');
    
    // Verificar si ya existe el option
    if (select1.find(`option[value="${producto.atributo1_id}"]`).length === 0) {
        // Si no existe, crearlo
        select1.append(`<option value="${producto.atributo1_id}" selected>${producto.atributo1_nombre}</option>`);
    } else {
        // Si existe, seleccionarlo
        select1.val(producto.atributo1_id).trigger('change');
    }
}
```

Esto garantiza que:
- ✅ Si el select está vacío, crea la opción dinámicamente
- ✅ Si el select tiene opciones, selecciona la correcta
- ✅ Los atributos siempre se muestran

### 2. Función cargarVariacionesEnTabla Mejorada

```javascript
window.cargarVariacionesEnTabla = function(variaciones) {
    console.log('Cargando variaciones:', variaciones);  // DEBUG
    
    const tbody = $('#tablaVariacionesEdicion tbody');
    tbody.empty();
    
    if (!variaciones || variaciones.length === 0) {
        tbody.append('<tr><td colspan="5">No hay variaciones</td></tr>');
        return;
    }
    
    variaciones.forEach(variacion => {
        // ... crear fila ...
    });
};
```

---

## 🧪 Pasos de Verificación

### Paso 1: Refrescar y Probar

```
1. Ctrl + Shift + R
2. F12 (abrir consola)
3. Ir a pestaña "Console"
4. Clic en "Edición Productos"
5. Buscar producto
6. Clic en "Editar"
7. VER LOGS EN CONSOLA
```

### Paso 2: Copiar Logs

En la consola, verás algo como:

```javascript
Usando funciones de edición inline
Datos recibidos del backend: {...}
Producto: {...}
Variaciones: [...]
```

**COPIA ESTOS LOGS COMPLETOS** y compártelos para que pueda analizar exactamente qué está pasando.

### Paso 3: Verificar Modal

Después de que se abra el modal, verifica:

```
Pestaña "Datos Generales":
- ¿Se cargó el nombre? (articulo)
- ¿Se cargó la descripción?
- ¿Se cargó la categoría?
- ¿Aparecen marca, color, género?
- ¿Se cargaron los precios?

Pestaña "Variaciones / Tallas":
- ¿Aparece la tabla?
- ¿Hay filas en la tabla?
- ¿Se muestran tallas y SKUs?
- ¿Se muestra el stock?
```

---

## 🎯 Qué Hacer Con Los Logs

### Si los logs muestran:

1. **`atributo1_id: null`** → El producto no tiene marca asignada en BD
2. **`variaciones: []`** → El producto no tiene tallas en BD
3. **`atributo1_id: 25, atributo1_nombre: "TORPEDO"`** → Los datos están correctos

### Acciones según los logs:

| Log | Significado | Acción |
|-----|-------------|--------|
| `atributo1_id: null` | Sin atributos en BD | Normal, editar y asignar |
| `atributo1_id: 25` pero no se ve | Problema de carga | Verificar select |
| `variaciones: []` | Sin tallas en BD | Normal si es producto nuevo |
| `variaciones: [...]` pero no se ven | Problema de tabla | Verificar función |

---

## 📝 Formato de Reporte

Por favor, copia y comparte esto:

```
LOGS DE CONSOLA:
================
[Pegar aquí los logs que aparecen cuando haces clic en "Editar"]

ESTADO DEL MODAL:
=================
Pestaña Datos Generales:
- Nombre: [¿se ve?]
- Descripción: [¿se ve?]
- Marca: [¿se ve?]
- Color: [¿se ve?]
- Género: [¿se ve?]

Pestaña Variaciones:
- ¿Aparece tabla?: [Sí/No]
- ¿Cuántas filas?: [X filas]
- ¿Se ve el stock?: [Sí/No]
```

---

## 🔍 Verificación Manual en BD

Si los atributos no se cargan, verifica en la base de datos:

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
..\venv\Scripts\python.exe manage.py shell
```

```python
from app.models import Producto

# Obtener el producto
p = Producto.objects.get(id=67970)

# Verificar atributos
print(f"Marca: {p.atributo1}")
print(f"Color: {p.atributo2}")
print(f"Género: {p.atributo3}")

# Verificar variaciones
print(f"Variaciones: {p.producto_talla.all()}")
print(f"Cantidad: {p.producto_talla.count()}")

# Ver detalles de una variación
for pt in p.producto_talla.all():
    print(f"Talla: {pt.talla}, SKU: {pt.sku}, Stock: {pt.stock}")
```

---

**Fecha**: 2024-11-06  
**Estado**: Debug implementado  
**Acción requerida**: Copiar logs de consola para análisis

