# 🔧 Corrección: Problema "Sucursal No Seleccionada" en Ticket de Venta

**Fecha:** 7 de Noviembre, 2025  
**Problema:** Modal de búsqueda mostraba "Sucursal no seleccionada" aunque había sesión activa  
**Estado:** ✅ **RESUELTO**

---

## 🐛 Problema Identificado

### Síntoma
```
❌ Modal de búsqueda muestra: "Sucursal no seleccionada"
❌ No se puede buscar productos
❌ Aparece aunque el usuario tiene sesión activa
```

### Causa Raíz
**Inconsistencia en variables de sesión:**

La vista `ticket_venta` estaba usando:
```python
sucursal_actual_id = request.session.get('sucursalActual')  # ❌ INCORRECTO
```

Pero el resto del sistema usa:
```python
sucursal_actual_id = request.session.get('idSucursalActual')  # ✅ CORRECTO
```

---

## ✅ Solución Implementada

### 1. Corrección en `views.py` (Línea 9391)

**ANTES:**
```python
@login_required
def ticket_venta(request):
    # Obtener sucursal actual del usuario
    sucursal_actual_id = request.session.get('sucursalActual')  # ❌ Variable incorrecta
    sucursal_actual = None
```

**DESPUÉS:**
```python
@login_required
def ticket_venta(request):
    # Obtener sucursal actual del usuario (intentar ambas variables de sesión)
    sucursal_actual_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')  # ✅ Fallback
    sucursal_actual = None
```

**Ventajas del Fallback:**
- ✅ Usa primero la variable estándar del sistema (`idSucursalActual`)
- ✅ Si no existe, intenta con `sucursalActual` (compatibilidad)
- ✅ Evita romper otras partes del sistema

### 2. Mejoras en `ticket_venta.html`

#### A. Información Detallada de Sucursal en Modal

**ANTES:**
```html
<span>{{ sucursal_actual.alias|default:"No seleccionada" }}</span>
```

**DESPUÉS:**
```html
<span id="modalSucursalActual">
    {% if sucursal_actual %}
        {{ sucursal_actual.alias }} (ID: {{ sucursal_actual.id }})
    {% else %}
        No seleccionada - Por favor selecciona una sucursal desde el menú principal
    {% endif %}
</span>
```

**Beneficios:**
- ✅ Muestra el ID de la sucursal para depuración
- ✅ Mensaje claro cuando no hay sucursal
- ✅ Instrucciones para el usuario

#### B. Validación al Abrir Modal de Búsqueda

```javascript
$('#btnBuscarArticulo').click(function() {
    // Verificar que haya sucursal seleccionada
    const sucursalActualId = {% if sucursal_actual %}{{ sucursal_actual.id }}{% else %}null{% endif %};
    
    if (!sucursalActualId) {
        Swal.fire({
            icon: 'warning',
            title: 'Sucursal requerida',
            html: 'Debes seleccionar una sucursal desde el menú principal...',
        });
        return;  // No abre el modal
    }
    
    // Continúa con apertura del modal...
});
```

**Beneficios:**
- ✅ Previene abrir modal sin sucursal
- ✅ Mensaje claro al usuario
- ✅ Evita errores en la búsqueda

#### C. Logs de Depuración en JavaScript

```javascript
function buscarProductosModal(pagina = 1) {
    const sucursalActualId = {% if sucursal_actual %}{{ sucursal_actual.id }}{% else %}null{% endif %};

    // Debug: Mostrar información en consola
    console.log('===== BÚSQUEDA DE PRODUCTOS =====');
    console.log('Sucursal Actual ID:', sucursalActualId);
    console.log('Búsqueda:', filtroBusqueda);
    console.log('Solo con stock:', soloConStock);
    console.log('Buscar en todas:', buscarTodas);
    
    if (!buscarTodas && sucursalActualId) {
        filtros.sucursal_id = sucursalActualId;
        console.log('Filtrando por sucursal ID:', sucursalActualId);
    } else if (!sucursalActualId) {
        console.warn('⚠️ No hay sucursal seleccionada!');
    }
    
    console.log('Enviando petición con filtros:', filtros);
}
```

**Beneficios:**
- ✅ Facilita depuración
- ✅ Permite verificar qué se está enviando
- ✅ Identifica rápidamente problemas

---

## 🔍 Variables de Sesión en el Sistema

### Variables Estándar (usadas en todo el sistema)
```python
request.session['idSucursalActual']      # ✅ ID de la sucursal
request.session['idEmpresaActual']       # ✅ ID de la empresa
request.session['nombreEmpresaActual']   # ✅ Nombre de la empresa
```

### Variables Alternativas (legacy)
```python
request.session['sucursalActual']        # ⚠️ Usado en pocas vistas
```

### Cómo se Establece la Sesión
Generalmente en el login o al seleccionar sucursal desde el menú:
```python
request.session['idSucursalActual'] = sucursal.id
request.session['idEmpresaActual'] = empresa.id
request.session['nombreEmpresaActual'] = empresa.razon_social
```

---

## 📋 Cómo Verificar que Funciona

### 1. Abrir la Consola del Navegador (F12)

### 2. Acceder a Ticket de Venta
```
http://localhost:8000/app/ticket-venta/
```

### 3. Verificar en la Interfaz
Deberías ver:
```
Sucursal: CASA MATRIZ (ID: 1)
```
En lugar de:
```
Sucursal: No seleccionada
```

### 4. Abrir Modal de Búsqueda
Click en "Buscar Artículo"

### 5. Ver Logs en Consola
Deberías ver algo como:
```javascript
===== BÚSQUEDA DE PRODUCTOS =====
Sucursal Actual ID: 1
Búsqueda: polera
Solo con stock: true
Buscar en todas: false
Filtrando por sucursal ID: 1
Enviando petición con filtros: {
    search: "polera",
    solo_con_stock: "on",
    sucursal_id: 1,
    page: 1
}
```

### 6. Verificar Resultados
Deberías ver productos de la sucursal con stock

---

## 🚨 Casos Especiales

### Caso 1: Usuario Sin Sucursal Asignada
**Síntoma:** Realmente no tiene sucursal en sesión

**Solución:**
1. Ir al menú principal
2. Seleccionar "Cambiar Sucursal" o "Seleccionar Sucursal"
3. Elegir una sucursal de la lista
4. Volver a ticket de venta

### Caso 2: Sesión Expirada
**Síntoma:** La sesión se perdió

**Solución:**
1. Cerrar sesión (logout)
2. Volver a iniciar sesión
3. Seleccionar sucursal
4. Acceder a ticket de venta

### Caso 3: Problemas de Caché
**Síntoma:** Sigue mostrando "No seleccionada" después de la corrección

**Solución:**
1. Limpiar caché del navegador (Ctrl + Shift + Delete)
2. Refrescar con Ctrl + F5
3. Volver a iniciar sesión

---

## 📊 Comparación Antes/Después

| Aspecto | Antes | Después |
|---------|-------|---------|
| **Variable de sesión** | `sucursalActual` | `idSucursalActual` + fallback |
| **Mensaje de error** | "No seleccionada" | "No seleccionada - Instrucciones claras" |
| **Validación** | ❌ Ninguna | ✅ Antes de abrir modal |
| **Depuración** | ❌ Sin logs | ✅ Console.log detallados |
| **Muestra ID** | ❌ No | ✅ Sí (para debug) |
| **Manejo de errores** | ⚠️ Básico | ✅ Robusto |

---

## 📁 Archivos Modificados

### 1. `retailmind/app/views.py`
- **Línea 9391:** Corregida obtención de sucursal de sesión

### 2. `retailmind/app/templates/vistas/modulo_ventas/ticket_venta.html`
- **Líneas 336-351:** Mejora display de sucursal en modal
- **Líneas 158-165:** Mejora display de sucursal en ticket
- **Líneas 930-953:** Validación al abrir modal
- **Líneas 993-1012:** Logs de depuración
- **Línea 1039:** Log antes de AJAX

---

## ✅ Checklist de Verificación

- [x] Corregida variable de sesión en views.py
- [x] Agregado fallback para compatibilidad
- [x] Mejorado mensaje en modal de búsqueda
- [x] Mejorado mensaje en sección de ticket
- [x] Agregada validación antes de abrir modal
- [x] Agregados logs de depuración
- [x] Mostrado ID de sucursal para debug
- [x] Probado en navegador
- [x] Verificados logs en consola

---

## 🎯 Resultado Final

**Estado:** ✅ **FUNCIONANDO**

**Comportamiento Esperado:**
1. Usuario con sesión activa ve su sucursal
2. Modal muestra: "Sucursal: CASA MATRIZ (ID: 1)"
3. Búsqueda filtra correctamente por sucursal
4. Si no hay sucursal, mensaje claro e instrucciones

**Prueba Rápida:**
```bash
# 1. Reiniciar servidor
python manage.py runserver

# 2. Abrir navegador
http://localhost:8000/app/ticket-venta/

# 3. Verificar que muestra la sucursal correctamente
```

---

**Última actualización:** 7 de Noviembre, 2025  
**Probado en:** Django 4.x con sesiones activas  
**Compatibilidad:** ✅ Backward compatible con sistema existente

