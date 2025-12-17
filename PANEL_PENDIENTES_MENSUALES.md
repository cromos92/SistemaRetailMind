# 📊 Panel de Pendientes Mensuales

## 🎯 Descripción

Panel informativo que muestra un resumen de los **DTEs pendientes del mes actual** con indicadores visuales y acceso rápido.

## ✨ Características

### **Período: Mes Actual**

El sistema calcula automáticamente:
- 📅 **Mes actual**: Diciembre 2025
- 📊 **DTEs pendientes** del mes
- 💰 **Monto total** pendiente del mes

**Ejemplo en Diciembre 2025:**
```
┌────────────────────────────────────────────────┐
│ ⚠️ Documentos Pendientes                       │
├────────────────────────────────────────────────┤
│                                                 │
│  📊 15 documentos  [15]                        │
│  💰 $8.500.000 pendiente                       │
│  📅 Diciembre 2025                             │
│                                                 │
│           [Ver Pendientes]                     │
└────────────────────────────────────────────────┘
```

## 🎨 Indicadores Visuales

### **Badge de Cantidad:**

**Según cantidad de pendientes:**

```
0-10 pendientes   → 🟢 Badge verde (todo bajo control)
11-30 pendientes  → 🟡 Badge amarillo (atención normal)
31+ pendientes    → 🔴 Badge rojo (urgente)
```

### **Colores del Panel:**

```
Card amarillo con borde
Icono de alerta (⚠️)
Números grandes y claros
```

## 🔄 Funcionalidad "Ver Pendientes"

### **Al hacer clic:**

```
1. Ajusta filtro de fechas:
   Desde: 01/12/2025 (primer día del mes)
   Hasta: 31/12/2025 (último día del mes)

2. Recarga la tabla

3. Muestra TODOS los DTEs del mes actual
   (Pendientes y Pagados)

4. Mensaje: "Mostrando DTEs de Diciembre 2025"
```

## 📊 Cálculo del Monto

**Fórmula:**
```
Saldo Pendiente = Monto DTE - Notas de Crédito
```

**Ejemplo:**
```
DTE #227:
  Monto: $4.382.770
  NC: $0
  Pendiente: $4.382.770 ✅

DTE #80:
  Monto: $2.000.000
  NC: $897.979
  Pendiente: $1.102.021 ✅
```

**Total Pendiente:** Suma de todos los saldos

## 🎯 Estados Considerados

| Estado | Se Cuenta | Color |
|--------|-----------|-------|
| **Pendiente** | ✅ Sí | 🔴 Rojo |
| **Parcial** | ✅ Sí | 🟡 Amarillo |
| **Pagado** | ❌ No | 🟢 Verde |

## 📅 Información del Panel

### **Componentes:**

1. **📊 Cantidad de Pendientes**
   - Número grande
   - Badge con el mismo número

2. **💰 Monto Total Pendiente**
   - En rojo (urgente)
   - Formato con separador de miles

3. **📅 Período**
   - Badge azul
   - "Diciembre 2025"

4. **🔍 Botón "Ver Pendientes"**
   - Amarillo
   - Filtra todo el mes

## 🔄 Actualización Automática

El panel se actualiza:
- ✅ Al cargar la página
- ✅ Al cambiar de página
- ✅ Al buscar DTEs
- ✅ Después de filtrar

**Siempre muestra datos del mes actual.**

## 📝 Ejemplo por Meses

### **Enero 2025:**
```
📊 8 documentos pendientes
💰 $2.500.000
📅 Enero 2025
```

### **Diciembre 2025:**
```
📊 15 documentos pendientes
💰 $8.500.000
📅 Diciembre 2025
```

## 💡 Ventajas del Enfoque Mensual

### **Vs. Anual:**

| Aspecto | Anual | Mensual |
|---------|-------|---------|
| Relevancia | Baja | Alta ⭐ |
| Cantidad | 100-500 | 10-50 ⭐ |
| Gestión | Difícil | Fácil ⭐ |
| Control | General | Preciso ⭐ |
| Performance | Lento | Rápido ⭐ |

### **Beneficios:**

- ✅ **Más manejable:** 10-50 documentos vs 100-500
- ✅ **Más relevante:** Lo que importa AHORA
- ✅ **Mejor control:** Cierre mensual
- ✅ **Más rápido:** Menos datos a procesar

## 🎯 Casos de Uso

### **Inicio del Día:**
```
1. Entrar a Gestión de DTEs
2. Ver panel de pendientes
3. "15 documentos pendientes este mes"
4. Planificar pagos del día
```

### **Fin de Mes:**
```
1. Ver panel
2. "5 documentos pendientes"
3. Clic en "Ver Pendientes"
4. Revisar y pagar antes de cierre
```

### **Durante el Mes:**
```
1. Panel actualizado en tiempo real
2. Ver cómo disminuyen los pendientes
3. Control de flujo de caja mensual
```

## 📊 Debug en Consola

```javascript
📊 Resumen pendientes Diciembre 2025: {
  anio: 2025,
  mes: 12,
  nombre_mes: "Diciembre",
  periodo: "Diciembre 2025",
  cantidad_pendientes: 15,
  monto_pendiente: 8500000,
  total_dtes: 42,
  pagados: 27
}
```

## ✅ Resumen

**Implementado:**
- ✅ API que calcula pendientes del **mes actual**
- ✅ Panel visual con badge
- ✅ Monto total pendiente del mes
- ✅ Badge de período (Diciembre 2025)
- ✅ Botón para ver todos los pendientes
- ✅ Colores según urgencia (verde/amarillo/rojo)
- ✅ Actualización automática

**Beneficios:**
- 🎯 Control mensual efectivo
- 📊 Datos relevantes y actuales
- ⚡ Más rápido y eficiente
- 💡 Mejor gestión de pagos

**¡Recarga la página (Ctrl + F5) y verás el panel con datos del mes actual!** 📅✅
