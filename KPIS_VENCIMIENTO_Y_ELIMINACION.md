# 📊 KPIs de Vencimiento y Eliminación Forzada

## 🎯 Funcionalidades Implementadas

### **1. Panel de KPIs de Vencimiento** ⭐

**Vista completa del año con 4 categorías:**

```
┌──────────────┬──────────────┬──────────────┬──────────────┐
│ 📄 Pendientes│ 🔴 Vencidos  │ 🟠 Por Vencer│ 🟢 Al Día    │
├──────────────┼──────────────┼──────────────┼──────────────┤
│    342       │     45       │     28       │    269       │
│  $125.5M     │  $18.2M      │  $12.3M      │  $95M        │
└──────────────┴──────────────┴──────────────┴──────────────┘
          [Año 2025]
[Ver Vencidos] [Ver Por Vencer] [Ver Todos Pendientes]
```

### **2. Eliminación Forzada para Datos de Prueba** ⭐

**Flujo mejorado:**

```
1. Intentar eliminar DTE
   ↓
2. Sistema detecta datos relacionados
   ↓
3. Muestra diálogo especial:
   "DTE tiene 5 productos asociados
    ⚠️ Datos de Prueba:
    ¿Deseas eliminarlo forzadamente?"
   
   [🗑️ Forzar Eliminación] [Cancelar]
   ↓
4. Si confirma → Elimina en cascada:
   ✅ Productos del DTE
   ✅ Pagos/NCs asociadas
   ✅ El DTE mismo
```

## 📊 Clasificación por Vencimiento

### **Cálculo:**
```python
dias_hasta_vencimiento = fecha_vencimiento - hoy

Si dias < 0:
  → 🔴 VENCIDO (urgente)
  
Si 0 ≤ dias ≤ 7:
  → 🟠 POR VENCER (prioridad)
  
Si dias > 7:
  → 🟢 AL DÍA (normal)
```

### **Ejemplos:**

**DTE con fecha_vencimiento = 2025-12-10** (hoy es 17/12):
```
dias_hasta_vencimiento = -7 días
Estado: 🔴 VENCIDO
```

**DTE con fecha_vencimiento = 2025-12-20** (hoy es 17/12):
```
dias_hasta_vencimiento = 3 días
Estado: 🟠 POR VENCER
```

**DTE con fecha_vencimiento = 2026-01-15** (hoy es 17/12):
```
dias_hasta_vencimiento = 29 días
Estado: 🟢 AL DÍA
```

## 🎯 KPIs Mostrados

### **1. Pendientes** (Amarillo)
```
Cantidad: 342 documentos
Monto: $125.500.000
Incluye: Todos los pendientes del año
```

### **2. Vencidos** (Rojo)
```
Cantidad: 45 documentos
Monto: $18.200.000
Acción: ¡Urgente! Pagar ya
```

### **3. Por Vencer** (Naranja)
```
Cantidad: 28 documentos
Monto: $12.300.000
Plazo: Próximos 7 días
Acción: Planificar pago
```

### **4. Al Día** (Verde)
```
Cantidad: 269 documentos
Monto: $95.000.000
Estado: Sin urgencia (+7 días)
```

## 🔘 Botones de Filtrado Rápido

### **Ver Vencidos** (Rojo)
- Ajusta filtro a todo el año
- Mensaje: "¡Requieren atención urgente!"
- Ideal para: Priorizar pagos

### **Ver Por Vencer** (Amarillo)
- Ajusta filtro a todo el año
- Mensaje: "Vencen en próximos 7 días"
- Ideal para: Planificación semanal

### **Ver Todos Pendientes** (Azul)
- Ajusta filtro a todo el año completo
- Muestra: Todos sin importar vencimiento
- Ideal para: Vista general

## 🗑️ Eliminación Forzada

### **Casos de Uso:**

**1. DTEs de Prueba:**
```
Problema: DTE con productos de prueba
Solución: Forzar eliminación en cascada
Resultado: Todo se elimina limpiamente
```

**2. DTEs con Productos:**
```
Error: "No se puede eliminar un DTE con 5 productos asociados"

Opciones mostradas:
- 🗑️ Forzar Eliminación (elimina todo)
- ❌ Cancelar (mantiene todo)
```

**3. DTEs con Recepciones:**
```
Error: "DTE ya fue recepcionado"

Opciones:
- 🗑️ Forzar (elimina DTE + productos + recepciones)
- ❌ Cancelar
```

### **Qué Elimina en Cascada:**

```
Eliminación Forzada →
  ✅ Productos del DTE (Dte_Productos)
  ✅ Pagos asociados (Dte_Detalle_Pago)
  ✅ Notas de Crédito vinculadas
  ✅ El DTE mismo
```

## 💡 Recomendaciones

### **Para Datos de Producción:**
```
❌ NO usar eliminación forzada
✅ Anular el documento
✅ Mantener historial
✅ Cumplir normativas
```

### **Para Datos de Prueba:**
```
✅ Usar eliminación forzada
✅ Limpia completamente
✅ Sin rastros en BD
```

## 🎨 Interfaz Mejorada

### **Diálogo de Confirmación:**

```
┌────────────────────────────────────────┐
│ ⚠️ DTE con Datos Relacionados          │
├────────────────────────────────────────┤
│                                         │
│ No se puede eliminar un DTE con        │
│ 5 producto(s) asociado(s)              │
│                                         │
│ ───────────────────────────────────    │
│                                         │
│ ⚠️ Datos de Prueba:                    │
│ ¿Deseas eliminarlo forzadamente?       │
│                                         │
│ Esto eliminará el DTE y todos          │
│ sus datos asociados.                   │
│                                         │
│ [🗑️ Forzar Eliminación] [Cancelar]    │
└────────────────────────────────────────┘
```

## 📊 Beneficios del Panel de KPIs

### **Gestión Proactiva:**
- 🔴 **Vencidos:** Acción inmediata
- 🟠 **Por vencer:** Planificar esta semana
- 🟢 **Al día:** Sin presión

### **Control Financiero:**
- Ver montos por categoría
- Priorizar pagos grandes vencidos
- Distribuir pagos por urgencia

### **Toma de Decisiones:**
```
Si vencidos > 10%:
  → Problema de flujo de caja
  
Si por_vencer > monto_al_dia:
  → Preparar liquidez
  
Si al_dia alto:
  → Buena gestión
```

## ✅ Resumen de Cambios

**KPIs implementados:**
1. ✅ Pendientes totales del año
2. ✅ Vencidos (pasó fecha límite)
3. ✅ Por vencer (próximos 7 días)
4. ✅ Al día (más de 7 días)
5. ✅ Montos por cada categoría
6. ✅ 3 botones de filtrado rápido

**Eliminación mejorada:**
1. ✅ Detecta productos asociados
2. ✅ Ofrece eliminación forzada
3. ✅ Elimina en cascada
4. ✅ Mensajes claros y específicos
5. ✅ Ideal para datos de prueba

## 🚀 Listo para Usar

**Recarga la página (Ctrl + F5) y tendrás:**
- ✅ Panel con 4 KPIs de vencimiento
- ✅ Datos del año 2025 completo
- ✅ Botones de filtrado por categoría
- ✅ Eliminación forzada para datos de prueba

**¡Sistema completo de gestión de pagos con control de vencimientos!** 📊✅
