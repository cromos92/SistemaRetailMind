# 💰 Estado de Pago: Pendiente por Defecto

## 🎯 Implementación

Al importar DTEs, todos se crean automáticamente con:
- ✅ **Estado de Pago: "Pendiente"**
- ✅ **Estado DTE: "EMITIDO"**

Esto permite que el usuario registre los pagos manualmente después.

## 📋 Flujo de Trabajo

### **1. Importar DTEs**
```
Importas 5 DTEs desde CSV
         ↓
Estado de Pago: "Pendiente" (automático)
         ↓
Aparecen en Gestión de DTEs
```

### **2. Registrar Pagos**
```
Ver DTE en lista
         ↓
Abrir detalle del DTE
         ↓
Ir a "Pagos"
         ↓
Registrar pago (fecha, monto, método)
         ↓
Estado cambia a "Pagado" (si está completo)
```

### **3. Estados Posibles**

| Estado | Significado | Cuándo |
|--------|-------------|--------|
| **Pendiente** | Sin pagar | Al importar (por defecto) |
| **Parcial** | Pago parcial | Al registrar pago < total |
| **Pagado** | Completamente pagado | Al registrar pago = total |

## 💡 Ejemplo Práctico

### **Importas este CSV:**
```csv
rut_proveedor,numero_documento,monto_con_iva
76276941-7,227,4382770
76276941-7,241,3407565
```

### **Resultado en Base de Datos:**
```
DTE #227:
  Monto: $4.382.770
  Estado Pago: Pendiente ✅
  Estado DTE: EMITIDO ✅
  
DTE #241:
  Monto: $3.407.565
  Estado Pago: Pendiente ✅
  Estado DTE: EMITIDO ✅
```

### **En Gestión de DTEs verás:**
```
┌────────────────────────────────────────────┐
│ DTE #227 | Nike | $4.382.770 | 🔴 Pendiente │
│ DTE #241 | Nike | $3.407.565 | 🔴 Pendiente │
└────────────────────────────────────────────┘
```

### **Después de Registrar Pago:**
```
1. Abrir DTE #227
2. Ir a sección "Pagos"
3. Registrar pago:
   - Fecha: 15/12/2025
   - Monto: $4.382.770
   - Método: Transferencia
4. Guardar

Resultado:
DTE #227: Estado → 🟢 Pagado ✅
```

## 🎨 Visualización en la Lista

**DTEs recién importados:**
```
┌───────────────────────────────────────────────────────┐
│ N° Doc │ Proveedor │ Monto      │ Estado │ Acciones  │
├───────────────────────────────────────────────────────┤
│ 227    │ Nike      │ $4.382.770 │ 🔴 Pendiente │ [💰]  │
│ 241    │ Nike      │ $3.407.565 │ 🔴 Pendiente │ [💰]  │
│ 248    │ Nike      │ $7.875.420 │ 🔴 Pendiente │ [💰]  │
│ 236    │ Nike      │ $4.392.528 │ 🔴 Pendiente │ [💰]  │
│ 235    │ Nike      │ $134.232   │ 🔴 Pendiente │ [💰]  │
└───────────────────────────────────────────────────────┘

[💰] = Botón para registrar pago
```

## 🔧 Campos No Necesarios en CSV

**No necesitas incluir:**
- ❌ `estado_pago` (siempre será "Pendiente")
- ❌ `estado_dte` (siempre será "EMITIDO")
- ❌ `responsable` (se asigna tu usuario)
- ❌ `receptor` (se asigna tu empresa)

## 📊 Formato CSV Mínimo Requerido

```csv
rut_proveedor,numero_documento,monto_con_iva
76276941-7,227,4382770
76276941-7,241,3407565
76276941-7,248,7875420
76276941-7,236,4392528
76276941-7,235,134232
```

**Campos opcionales adicionales:**
```csv
rut_proveedor,numero_documento,monto_con_iva,fecha_emision,tipo_documento,dias_credito,bultos,unidades,referencias
76276941-7,227,4382770,2025-05-28,33,30,0,0,Factura Mayo
```

## ✅ Beneficios

### **Para el Usuario:**
- ✅ Control total sobre los pagos
- ✅ Registro manual cuando se efectúa el pago
- ✅ Historial de pagos detallado
- ✅ Trazabilidad completa

### **Para el Sistema:**
- ✅ Estados consistentes
- ✅ Proceso claro de gestión
- ✅ Auditoría de pagos
- ✅ Cuentas por pagar precisas

## 🎯 Flujo Completo

```
1. Importar DTEs
   └─ Estado: Pendiente ✅

2. Ver en Gestión de DTEs
   └─ Identificar DTEs pendientes 🔴

3. Registrar Pago
   └─ Fecha, monto, método

4. Estado Actualizado
   └─ Parcial o Pagado 🟢
```

## 💡 Ventajas del Estado Pendiente

- ✅ **Claridad:** Sabes qué está por pagar
- ✅ **Control:** Registras pago cuando realmente se efectúa
- ✅ **Filtrado:** Puedes filtrar por "Pendiente" para ver cuentas por pagar
- ✅ **Reportes:** Métricas de pagos pendientes vs pagados

**¡Todo listo!** Tus DTEs se importarán con estado "Pendiente" y podrás registrar los pagos cuando lo necesites. 💰✅
