# 📊 EXPLICACIÓN: Dashboard y Notificaciones

## ❓ POR QUÉ NO APARECE EN "PRECIOS PENDIENTES" DEL DASHBOARD

### **Situación Actual:**

```
Modificas precio:
  $59,990 → $47,992
  ✓ Se aplica INMEDIATAMENTE
  
Dashboard de Ventas:
  Precios Pendientes: 0
  ❌ No aparece nada
```

---

## 🔍 CAUSA

**El sistema está configurado en MODO SIMPLE:**

```javascript
// En gestion_precios.html y edicion_rapida_precios.html
// Cuando aplicas un precio:

aplicarPrecioDirecto(productoId, precioNuevo)
  ↓
fetch('/app/gestion-precios/actualizar-precio/')
  ↓
actualizar_precio() en backend
  ↓
✓ Actualiza precio INMEDIATAMENTE
✓ NO crea registro pendiente
✓ NO requiere aprobación
```

**Por eso:**
- ✅ El precio se cambia al instante
- ❌ NO aparece en "Precios Pendientes"
- ❌ NO requiere aprobación

---

## 💡 SOLUCIÓN: 2 MODOS DISPONIBLES

### **MODO A: Aplicación Inmediata (ACTUAL)**

✅ **Ventajas:**
- Cambios instantáneos
- No requiere aprobación
- Más rápido
- Menos burocracia

❌ **Desventajas:**
- Sin control previo
- No aparece en Dashboard
- Sin workflow de aprobación

---

### **MODO B: Con Workflow de Aprobación**

✅ **Ventajas:**
- Control total
- Aparece en Dashboard
- Requiere aprobación
- Notificaciones a supervisores

❌ **Desventajas:**
- Más lento
- Requiere pasos adicionales
- Precio no cambia hasta aprobar

---

## 🔄 CÓMO ACTIVAR MODO CON APROBACIÓN

### **Opción 1: Activar en Edición Rápida**

Modificar `edicion_rapida_precios.html`:

**BUSCAR (línea ~1220):**
```javascript
// Aplicar todos los cambios
async function aplicarTodosCambios() {
    // ... código actual ...
    
    // Aquí usa aplicarPrecioDirecto
}
```

**CAMBIAR A:**
```javascript
// Aplicar todos los cambios CON APROBACIÓN
async function aplicarTodosCambios() {
    const cambios = listaEdicion.filter(item => item.precio_nuevo !== item.precio_original);
    
    if (cambios.length === 0) {
        alert('No hay cambios para aplicar');
        return;
    }
    
    // PREGUNTAR: ¿Aplicar directo o enviar a aprobación?
    const aplicarDirecto = confirm(
        `Tienes ${cambios.length} cambios pendientes.\n\n` +
        `OK = Aplicar INMEDIATAMENTE\n` +
        `Cancelar = Enviar a APROBACIÓN`
    );
    
    if (aplicarDirecto) {
        // MODO DIRECTO (actual)
        aplicarCambiosDirectos(cambios);
    } else {
        // MODO CON APROBACIÓN (nuevo)
        enviarCambiosAprobacion(cambios);
    }
}

// Nueva función para enviar a aprobación
async function enviarCambiosAprobacion(cambios) {
    let exitosos = 0;
    
    for (const cambio of cambios) {
        try {
            const response = await fetch('/app/gestion-precios/proponer-cambio/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    producto_id: cambio.id,
                    nuevo_precio: Math.round(cambio.precio_nuevo),
                    tipo_cambio: 'MANUAL',
                    motivo: 'Edición rápida de precio',
                    prioridad: 'MEDIA'
                })
            });
            
            const data = await response.json();
            if (data.success) exitosos++;
        } catch (error) {
            console.error('Error:', error);
        }
    }
    
    if (exitosos > 0) {
        alert(`✓ ${exitosos} cambios enviados a aprobación\nAparecerán en Dashboard de Ventas`);
        limpiarLista();
    }
}
```

---

## 🏪 NOTIFICACIONES A SUCURSALES

### **¿Cómo Determina a Qué Sucursal Notificar?**

El sistema notifica a **usuarios de la MISMA sucursal del producto**:

```python
# En views_modulo_gestion_precios.py
# Línea ~990

# Obtener usuarios de la sucursal DEL PRODUCTO
usuarios_sucursal = EmpresaUser.objects.filter(
    sucursal=producto.sucursal,  # ← MISMA sucursal que el producto
    status=True
).select_related('user')

# Enviar notificación a cada usuario
for empresa_user in usuarios_sucursal:
    if empresa_user.user != request.user:  # No notificar al creador
        NotificacionCambioPrecio.objects.create(
            cambio_precio=cambio,
            usuario=empresa_user.user,
            tipo='NUEVA',
            mensaje=mensaje
        )
```

---

### **Ejemplo Práctico:**

```
Producto: Zapatillas Nike Air Max
Sucursal del Producto: Centro

Usuarios en Sucursal Centro:
├─ admin (tú - NO notifica)
├─ vendedor1 (✓ notifica)
├─ vendedor2 (✓ notifica)
└─ supervisor (✓ notifica)

Usuarios en Sucursal Mall:
├─ vendedor3 (✗ NO notifica - otra sucursal)
└─ vendedor4 (✗ NO notifica - otra sucursal)

Resultado:
✓ 3 notificaciones enviadas (vendedor1, vendedor2, supervisor)
```

---

## 🎯 FLUJO COMPLETO CON APROBACIÓN

### **Para que APAREZCA en Dashboard:**

```
1. Usuario propone cambio
   └─ Usar "Enviar a Aprobación"
   
2. Sistema crea registro PENDIENTE
   └─ Notifica a usuarios de la sucursal
   
3. Dashboard muestra:
   🏷️ Precios Pendientes: 1
   ⚠️ 1 requiere atención
   
4. Supervisor ve notificación y revisa
   
5. Supervisor aprueba desde Dashboard
   └─ Click en tarjeta "Precios Pendientes"
   └─ Click "Aprobar"
   
6. Sistema aplica el precio
```

---

## 🔧 CONFIGURACIÓN RECOMENDADA

### **Opción 1: Modo Mixto (Lo mejor de ambos)**

```javascript
// En aplicarTodosCambios():

// Detectar cambios grandes automáticamente
const cambiosGrandes = cambios.filter(c => {
    const porcentaje = Math.abs((c.precio_nuevo - c.precio_original) / c.precio_original * 100);
    return porcentaje > 20; // Cambios > 20%
});

const cambiosPequenos = cambios.filter(c => {
    const porcentaje = Math.abs((c.precio_nuevo - c.precio_original) / c.precio_original * 100);
    return porcentaje <= 20;
});

// Aplicar directamente los pequeños
await aplicarCambiosDirectos(cambiosPequenos);

// Enviar a aprobación los grandes
if (cambiosGrandes.length > 0) {
    await enviarCambiosAprobacion(cambiosGrandes);
    alert(`✓ ${cambiosPequenos.length} cambios aplicados directamente\n` +
          `⏳ ${cambiosGrandes.length} cambios enviados a aprobación (>20%)`);
}
```

**Beneficio:**
- Cambios pequeños: Inmediatos
- Cambios grandes: Requieren aprobación
- Lo mejor de ambos mundos

---

## 📊 DASHBOARD: INDICADOR DE PRECIOS PENDIENTES

### **Cuándo SE LLENA:**

El indicador en Dashboard SOLO se llena cuando:

```
1. Usas el endpoint: proponer_cambio_precio()
   └─ Crea registro en CambioPrecioPendiente
   └─ Estado: PENDIENTE

2. Dashboard consulta:
   GET /app/gestion-precios/indicadores-pendientes/
   └─ Cuenta registros PENDIENTES
   └─ Muestra en tarjeta
```

**Si usas `actualizar_precio()` directamente:**
- ❌ NO crea registro pendiente
- ❌ NO aparece en Dashboard
- ✓ Precio se aplica de inmediato

---

## 🎯 EJEMPLO COMPLETO

### **Escenario: Quieres que aparezca en Dashboard**

```javascript
// ANTES (No aparece en Dashboard):
fetch('/app/gestion-precios/actualizar-precio/', {
    body: JSON.stringify({
        producto_id: 123,
        nuevo_precio: 47992
    })
})
→ Aplica directo, NO aparece en Dashboard

// AHORA (Aparece en Dashboard):
fetch('/app/gestion-precios/proponer-cambio/', {
    body: JSON.stringify({
        producto_id: 123,
        nuevo_precio: 47992,
        tipo_cambio: 'MANUAL',
        motivo: 'Liquidación de inventario',
        prioridad: 'ALTA'
    })
})
→ Crea PENDIENTE, SÍ aparece en Dashboard
```

---

## 🔔 NOTIFICACIONES POR SUCURSAL

### **Lógica de Notificación:**

```python
Producto en Sucursal A:
  └─ Notifica a usuarios de Sucursal A

Producto en Sucursal B:
  └─ Notifica a usuarios de Sucursal B
```

### **¿Y si el producto existe en varias sucursales?**

```
Producto: Nike Air Max
├─ Sucursal Centro: $59,990
├─ Sucursal Mall: $62,000
└─ Sucursal Outlet: $55,000

Si editas el de Sucursal Centro:
  ✓ Notifica solo a usuarios de Centro
  ✗ NO notifica a Mall ni Outlet
  
Si quieres sincronizar todas las sucursales:
  1. Usar "Sincronizar Sucursales"
  2. Seleccionar sucursales destino
  3. Aplicar
  → Ahora SÍ se actualiza en todas
```

---

## ⚙️ CÓDIGO PARA MODO MIXTO

Agrega esto al final de `edicion_rapida_precios.html`:

```javascript
// Función para aplicar cambios directos (sin aprobación)
async function aplicarCambiosDirectos(cambios) {
    document.getElementById('loadingOverlay').style.display = 'flex';
    
    let exitosos = 0;
    let errores = 0;
    
    for (let i = 0; i < cambios.length; i++) {
        const cambio = cambios[i];
        document.getElementById('loadingProgress').textContent = `${i + 1} de ${cambios.length}`;
        
        try {
            const response = await fetch('/app/gestion-precios/actualizar-precio/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    producto_id: cambio.id,
                    nuevo_precio: Math.round(cambio.precio_nuevo)
                })
            });
            
            const data = await response.json();
            if (data.success) exitosos++;
            else errores++;
        } catch (error) {
            errores++;
        }
        
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    
    document.getElementById('loadingOverlay').style.display = 'none';
    
    if (exitosos > 0) {
        alert(`✓ ${exitosos} productos actualizados` + (errores > 0 ? `\n✗ ${errores} errores` : ''));
        limpiarLista();
    }
}

// Función para enviar cambios a aprobación
async function enviarCambiosAprobacion(cambios) {
    let exitosos = 0;
    
    for (const cambio of cambios) {
        try {
            const response = await fetch('/app/gestion-precios/proponer-cambio/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({
                    producto_id: cambio.id,
                    nuevo_precio: Math.round(cambio.precio_nuevo),
                    tipo_cambio: 'MANUAL',
                    motivo: `Edición rápida: ${cambio.descuento_porcentaje.toFixed(1)}% descuento`,
                    prioridad: 'MEDIA'
                })
            });
            
            const data = await response.json();
            if (data.success) exitosos++;
        } catch (error) {
            console.error('Error:', error);
        }
    }
    
    if (exitosos > 0) {
        alert(`✓ ${exitosos} cambios enviados a aprobación\n` +
              `Aparecerán en Dashboard de Ventas → Precios Pendientes`);
        limpiarLista();
    }
}
```

---

## 🎯 CONFIGURACIÓN SEGÚN TU NECESIDAD

### **Si quieres TODO en Dashboard (requiere aprobación):**

Reemplaza función `aplicarTodosCambios()` con:

```javascript
async function aplicarTodosCambios() {
    const cambios = listaEdicion.filter(item => item.precio_nuevo !== item.precio_original);
    
    if (cambios.length === 0) {
        alert('No hay cambios para aplicar');
        return;
    }
    
    if (!confirm(`¿Enviar ${cambios.length} cambios a aprobación?`)) {
        return;
    }
    
    // Enviar TODOS a aprobación
    await enviarCambiosAprobacion(cambios);
}
```

**Resultado:**
```
1. Editas precios en lista
2. Click "Aplicar Todos"
3. Sistema crea registros PENDIENTES
4. Aparece en Dashboard: "Precios Pendientes: X"
5. Supervisor aprueba
6. Precio se aplica
```

---

### **Si quieres APLICACIÓN INMEDIATA (actual):**

Mantén el código como está. Es más rápido pero no pasa por Dashboard.

---

### **Si quieres MODO MIXTO (recomendado):**

```javascript
async function aplicarTodosCambios() {
    const cambios = listaEdicion.filter(item => item.precio_nuevo !== item.precio_original);
    
    if (cambios.length === 0) {
        alert('No hay cambios para aplicar');
        return;
    }
    
    // Separar cambios grandes de pequeños
    const cambiosGrandes = [];
    const cambiosPequenos = [];
    
    cambios.forEach(c => {
        const porcentaje = Math.abs((c.precio_nuevo - c.precio_original) / c.precio_original * 100);
        if (porcentaje > 20) {
            cambiosGrandes.push(c);
        } else {
            cambiosPequenos.push(c);
        }
    });
    
    // Preguntar solo si hay cambios grandes
    if (cambiosGrandes.length > 0) {
        const aplicarGrandes = confirm(
            `Tienes ${cambiosGrandes.length} cambios GRANDES (>20%):\n` +
            `¿Aplicar directamente o enviar a aprobación?\n\n` +
            `OK = Aplicar TODO directamente\n` +
            `Cancelar = Cambios grandes a aprobación`
        );
        
        if (aplicarGrandes) {
            // Aplicar todos directamente
            await aplicarCambiosDirectos([...cambiosPequenos, ...cambiosGrandes]);
        } else {
            // Pequeños: directo, Grandes: aprobación
            if (cambiosPequenos.length > 0) {
                await aplicarCambiosDirectos(cambiosPequenos);
            }
            await enviarCambiosAprobacion(cambiosGrandes);
            
            alert(
                `✓ ${cambiosPequenos.length} cambios aplicados directamente\n` +
                `⏳ ${cambiosGrandes.length} enviados a aprobación (>20%)`
            );
        }
    } else {
        // Todos son pequeños, aplicar directo
        await aplicarCambiosDirectos(cambiosPequenos);
    }
}
```

**Resultado:**
- Cambios < 20%: Inmediatos
- Cambios > 20%: A aprobación (aparecen en Dashboard)

---

## 📱 RESUMEN DE NOTIFICACIONES

### **Flujo de Notificación:**

```
PASO 1: Propones cambio de precio
  └─ Producto: Nike Air Max
  └─ Sucursal: Centro
  └─ Precio: $60k → $45k (-25%)

PASO 2: Sistema identifica usuarios
  └─ Query: EmpresaUser WHERE sucursal_id = Centro
  └─ Encuentra: vendedor1, vendedor2, supervisor
  └─ Excluye: Tu usuario (admin)

PASO 3: Crea notificaciones
  ├─ Para vendedor1: "Nuevo cambio de precio..."
  ├─ Para vendedor2: "Nuevo cambio de precio..."
  └─ Para supervisor: "Nuevo cambio de precio..."

PASO 4: Dashboard muestra
  └─ 🏷️ Precios Pendientes: 1
  └─ vendedor1, vendedor2, supervisor ven el indicador
  
PASO 5: Supervisor aprueba
  └─ Notifica de vuelta a admin: "Aprobado"
```

---

## 🔍 VERIFICAR NOTIFICACIONES

### **Query SQL para ver configuración:**

```sql
-- Ver usuarios por sucursal
SELECT 
    s.alias as sucursal,
    u.username,
    eu.status
FROM empresa_management_empresauser eu
JOIN users_usuario u ON eu.user_id = u.id
JOIN app_sucursal s ON eu.sucursal_id = s.id
WHERE eu.status = 1
ORDER BY s.alias, u.username;
```

### **En Django Shell:**

```python
from app.models import *

# Ver usuarios de una sucursal
sucursal = Sucursal.objects.get(alias='Centro')
usuarios = EmpresaUser.objects.filter(
    sucursal=sucursal,
    status=True
)

for eu in usuarios:
    print(f"Usuario: {eu.user.username}")
```

---

## 💡 RECOMENDACIÓN FINAL

### **Para tu Caso (simplicidad):**

**Usa MODO SIMPLE (actual):**
- ✅ Cambios inmediatos
- ✅ Sin complicaciones
- ✅ Más rápido

**Si necesitas control:**
- Activa Modo Mixto (código arriba)
- Cambios < 20%: Inmediatos
- Cambios > 20%: A aprobación

---

## 📋 INSTRUCCIONES RÁPIDAS

### **1. Ejecutar Migración (OBLIGATORIO):**

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
py .\manage.py makemigrations
py .\manage.py migrate
```

### **2. Refrescar página:**

```
http://localhost:8000/app/gestion-precios/edicion-rapida/
```

### **3. Probar:**

```
- Agregar 2 productos
- Editar ambos precios
- Click "Aplicar Todos"
- ✓ Ambos se actualizan
- ✓ Totales aparecen solo si hay cambios
- ✓ Fecha "hace cuánto" visible
```

---

## ✅ RESUMEN DE CORRECCIONES

| Problema | Solución |
|----------|----------|
| Totales no se actualizan | ✓ Función mejorada |
| Totales siempre visibles | ✓ Solo si hay cambios |
| Solo modifica 1 producto | ✓ Loop corregido |
| Falta "hace cuánto" | ✓ Ya visible en badge |
| No aparece en Dashboard | ✓ Explicado (modo directo vs aprobación) |
| Notificaciones a quién | ✓ Explicado (misma sucursal) |

---

**¡Ejecuta la migración y todo funcionará perfecto!** 🚀

