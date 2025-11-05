# 📘 EXPLICACIÓN: Flujo de Cambios de Precios

## 🎯 CÓMO FUNCIONA AHORA

Tienes **2 OPCIONES** al cambiar precios:

---

## ⚡ OPCIÓN 1: Aplicar INMEDIATAMENTE (Sin Aprobación)

### **Cuándo usar:**
- ✅ Cambios pequeños (< 10%)
- ✅ Tienes autoridad para cambiar precios
- ✅ Necesitas actualización inmediata
- ✅ No requiere supervisión

### **Flujo:**
```
1. Usuario ve recomendación: $59,990 → $49,990
2. Click "Aplicar Precio Recomendado"
3. Diálogo:
   "¿Cómo deseas proceder con el precio de $49,990?"
   
   [OK] = Aplicar INMEDIATAMENTE (sin aprobación)
   [Cancelar] = Enviar a APROBACIÓN (requiere revisar)

4. Usuario hace click en [OK]
5. Sistema:
   ✓ Actualiza precio inmediatamente
   ✓ Actualiza todas las tallas
   ✓ Actualiza todos los lotes FIFO
   ✓ Precio visible de inmediato

6. Usuario busca de nuevo:
   ✓ Ve el nuevo precio: $49,990
```

**Resultado:** Cambio aplicado al instante ⚡

---

## 🔐 OPCIÓN 2: Enviar a APROBACIÓN (Con Workflow)

### **Cuándo usar:**
- ✅ Cambios grandes (> 20%)
- ✅ Políticas requieren aprobación
- ✅ Necesitas supervisión
- ✅ Múltiples niveles de autorización

### **Flujo:**
```
1. Usuario ve recomendación: $59,990 → $35,990 (-40%)
2. Click "Aplicar Precio Recomendado"
3. Diálogo:
   "¿Cómo deseas proceder con el precio de $35,990?"
   
   [OK] = Aplicar INMEDIATAMENTE
   [Cancelar] = Enviar a APROBACIÓN ← Usuario hace click aquí

4. Sistema:
   ✓ Crea registro PENDIENTE
   ✓ Notifica a supervisores de la sucursal
   ✓ Precio NO cambia todavía
   
5. Usuario busca de nuevo:
   ❌ Precio sigue siendo $59,990 (pendiente de aprobar)
   
6. Supervisor ve en Dashboard:
   🏷️ Precios Pendientes: 1
   
7. Supervisor revisa y aprueba:
   - Ir a: Revisar Cambios Precios
   - Ver detalle del cambio
   - Click [Aprobar]
   
8. Sistema:
   ✓ Ahora SÍ actualiza el precio a $35,990
   ✓ Actualiza todas las tallas
   ✓ Notifica al creador
   
9. Usuario busca de nuevo:
   ✓ Ahora ve el nuevo precio: $35,990
```

**Resultado:** Cambio controlado y aprobado 🔐

---

## 🎬 EJEMPLO PRÁCTICO

### **Escenario: Liquidación de Inventario Antiguo**

```
Producto: Zapatillas Nike Air Max
Precio Actual: $59,990
Recomendación: $41,990 (-30%)
Motivo: Inventario de 420 días sin ventas
```

**Si eres VENDEDOR (sin autoridad para grandes descuentos):**
```
1. Ver recomendación
2. Click "Aplicar Precio Recomendado"
3. Diálogo aparece
4. Click [Cancelar] para enviar a aprobación
5. Mensaje: "Cambio propuesto. 3 usuarios notificados. Esperando aprobación."
6. Ir a Dashboard → Ver "Precios Pendientes: 1"
7. Esperar que supervisor apruebe
```

**Si eres SUPERVISOR (con autoridad):**
```
1. Ver recomendación
2. Click "Aplicar Precio Recomendado"
3. Diálogo aparece
4. Click [OK] para aplicar inmediatamente
5. Mensaje: "Precio actualizado para 4 tallas"
6. Buscar producto de nuevo → ✓ Precio actualizado a $41,990
```

---

## 📊 VISTA DE REVISIÓN DE CAMBIOS

### **URL:**
```
http://localhost:8000/app/gestion-precios/revisar-pendientes/
```

### **Qué muestra:**
```
┌─────────────────────────────────────────────────────┐
│ Zapatillas Nike Air Max                             │
│ Tallas: 38, 39, 40, 41 (4 tallas)                  │
│ Sucursal: Centro | Tipo: Por Recomendación         │
│                                                     │
│ $59,990 → $41,990 (-30%)                           │
│                                                     │
│ Motivo: Aplicación de recomendación del sistema    │
│ Propuesto por: vendedor1 - 05/11/2025 14:30       │
│ Días pendiente: 0 días                             │
│                                                     │
│ [👁️ Revisar] [✓ Aprobar] [✗ Rechazar]             │
└─────────────────────────────────────────────────────┘
```

**Acciones:**
- **[Revisar]** → Marca como revisado (no cambia precio)
- **[Aprobar]** → Aplica el precio a las 4 tallas
- **[Rechazar]** → Cancela el cambio

---

## 🎯 RECOMENDACIÓN DE USO

### **Para la Mayoría de Casos:**

```
Usa APLICAR INMEDIATAMENTE:
- Es más rápido
- No requiere pasos adicionales
- El precio se actualiza al instante
```

### **Para Casos Especiales:**

```
Usa ENVIAR A APROBACIÓN cuando:
- Descuento > 30%
- Producto de alta rotación
- Requiere autorización superior
- Políticas de la empresa lo requieren
```

---

## ⚙️ CONFIGURACIÓN RECOMENDADA

Si quieres **aplicar SIEMPRE inmediatamente** (más simple):

<function_calls>
<invoke name="search_replace">
<parameter name="file_path">retailmind/app/templates/vistas/modulo_existencias/gestion_precios.html
