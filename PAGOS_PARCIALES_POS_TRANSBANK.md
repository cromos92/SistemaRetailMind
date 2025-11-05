# ✅ Pagos Parciales con POS Transbank

## 🎯 NUEVA FUNCIONALIDAD: PAGOS PARCIALES

El sistema ahora permite hacer **múltiples pagos con POS Transbank** en un mismo ticket.

---

## 💡 ¿Qué son los Pagos Parciales?

Permite dividir el pago total en varias transacciones con el POS.

### Ejemplos de Uso:

#### **Caso 1: Cliente con 2 tarjetas**
```
Total: $10,000

1. POS Transbank → $5,000 (Tarjeta 1 - Débito)
2. POS Transbank → $5,000 (Tarjeta 2 - Crédito)

✅ Total pagado: $10,000
✅ 2 transacciones registradas
```

#### **Caso 2: Pago mixto (POS + Efectivo)**
```
Total: $15,000

1. POS Transbank → $10,000 (Débito)
2. Efectivo → $5,000

✅ Total pagado: $15,000
```

#### **Caso 3: Límite de tarjeta**
```
Total: $20,000

1. POS Transbank → $8,000 (Límite de tarjeta)
2. POS Transbank → $12,000 (Otra tarjeta)

✅ Total pagado: $20,000
```

---

## 🚀 FLUJO DE USO

### 1. **Crear Ticket con Productos**
```
http://127.0.0.1:8000/app/pos-dashboard/

- Agregar productos
- Total: $10,000
- Ir a Paso de Pago
```

### 2. **Primer Pago con POS** (Parcial)
```
1. Click "POS Transbank"

2. Aparece modal:
   ┌──────────────────────────────────┐
   │ Pago con POS Transbank           │
   │                                  │
   │ Saldo pendiente: $10,000         │
   │                                  │
   │ Ingrese monto a cobrar:          │
   │ ┌────────────────────────────┐   │
   │ │     5000                   │   │ ← Puedes cambiar
   │ └────────────────────────────┘   │
   │                                  │
   │ Mínimo: $50 | Máximo: $10,000    │
   │                                  │
   │ [Continuar al POS] [Cancelar]    │
   └──────────────────────────────────┘

3. Ingresar monto: 5000
4. Click "Continuar al POS"
5. Pasar tarjeta
6. ✅ Aprobada: $5,000

7. Resultado:
   ┌──────────────────────────────────┐
   │ 🎉 Venta Aprobada                │
   │                                  │
   │ ⚠️ Saldo pendiente: $5,000       │ ← Indica que falta
   │                                  │
   │ Monto cobrado: $5,000            │
   │ Tarjeta: VISA Débito             │
   │ ...                              │
   │                                  │
   │ ⚠️ Puede agregar más pagos       │
   │                                  │
   │ [OK]                             │
   └──────────────────────────────────┘
```

### 3. **Segundo Pago** (Completar)
```
1. Click "POS Transbank" nuevamente

2. Modal:
   Saldo pendiente: $5,000
   Monto a cobrar: [5000] ← Por defecto el saldo

3. Click "Continuar al POS"
4. Pasar otra tarjeta
5. ✅ Aprobada: $5,000

6. Resultado:
   ┌──────────────────────────────────┐
   │ 🎉 Venta Aprobada                │
   │                                  │
   │ ✅ Ticket Completamente Pagado   │ ← Ya no hay saldo
   │                                  │
   │ Monto cobrado: $5,000            │
   │ Tarjeta: VISA Crédito            │
   │ ...                              │
   │                                  │
   │ ✅ Puede finalizar la venta      │
   │                                  │
   │ [OK]                             │
   └──────────────────────────────────┘
```

### 4. **Finalizar Venta**
```
- Saldo pendiente: $0
- Click "FINALIZAR VENTA"
- ✅ Ticket completado con 2 pagos POS
```

---

## 📊 EN LA LISTA DE PAGOS

Verás cada transacción por separado:

```
Pagos Registrados:
┌──────────────────────────────────┐
│ POS Transbank Débito        [X]  │
│ $5,000                           │
└──────────────────────────────────┘

┌──────────────────────────────────┐
│ POS Transbank Crédito       [X]  │
│ $5,000                           │
└──────────────────────────────────┘

Total Pagado: $10,000
Saldo Pendiente: $0
```

---

## 💳 VALIDACIONES IMPLEMENTADAS

### 1. **Monto Mínimo: $50**
```
Si ingresa < $50 → "El monto mínimo es $50"
```

### 2. **Monto Máximo: Saldo Pendiente**
```
Si ingresa > saldo → "El monto no puede ser mayor al saldo pendiente"
```

### 3. **Valor por Defecto**
```
Al abrir modal → Muestra el saldo pendiente completo
Puede modificarlo para pago parcial
```

---

## 🎨 MENSAJES MEJORADOS

### **Durante el Pago:**
```
Conectando al POS Transbank...
Monto a cobrar: $5,000       ← Monto específico
```

```
✅ Conectado al POS
Monto a cobrar: $5,000       ← Monto a cobrar
Saldo total: $10,000         ← Saldo total del ticket
Pase la tarjeta en el terminal POS
```

### **Después del Pago:**

#### Si quedó saldo:
```
🎉 Venta Aprobada

⚠️ Saldo pendiente: $5,000

Monto cobrado: $5,000
...

⚠️ Puede agregar más pagos si es necesario
```

#### Si se completó:
```
🎉 Venta Aprobada

✅ Ticket Completamente Pagado

Monto cobrado: $5,000
...

✅ Puede finalizar la venta
```

---

## ⚠️ MANEJO DE ERRORES MEJORADO

### Error "undefined" (Cancelación)

**Antes:**
```
❌ Error en POS
undefined
```

**Ahora:**
```
⚠️ Transacción no completada

⚠️ La transacción no se completó. Posibles causas:
   - Cliente canceló en el POS
   - Timeout esperando tarjeta
   - Tarjeta retirada antes de tiempo

Razones comunes:
• Cliente presionó "Cancelar" en el POS
• No se pasó la tarjeta a tiempo (timeout 60s)
• Tarjeta retirada antes de completar
• Tarjeta sin fondos o bloqueada

💡 Sugerencia: Puede intentar nuevamente o usar otro método de pago

[Intentar de nuevo] [Usar otro método]
```

### Si Elige "Intentar de nuevo":
- Se abre nuevamente el modal de monto
- Puede ingresar el mismo u otro monto
- Reinicia el proceso

### Si Elige "Usar otro método":
- Cierra el modal
- Puede usar Efectivo, Transferencia, etc.

---

## 🔧 CASOS DE USO DETALLADOS

### **Escenario 1: Pago Total con POS**
```
Total: $8,000

1. Click "POS Transbank"
2. Monto sugerido: $8,000
3. Click "Continuar al POS"
4. Pasar tarjeta
5. ✅ Aprobada
6. Saldo: $0
7. Finalizar venta
```

### **Escenario 2: Pago Parcial + Efectivo**
```
Total: $12,000

1. Click "POS Transbank"
2. Cambiar monto a: $7,000
3. Click "Continuar al POS"
4. Pasar tarjeta
5. ✅ Aprobada
6. Saldo pendiente: $5,000
7. Click "Efectivo"
8. Ingresar: $5,000
9. Saldo: $0
10. Finalizar venta
```

### **Escenario 3: Múltiples Tarjetas**
```
Total: $15,000

1. Click "POS Transbank"
2. Monto: $8,000 (límite de tarjeta 1)
3. ✅ Aprobada → Saldo: $7,000

4. Click "POS Transbank" nuevamente
5. Monto: $7,000
6. ✅ Aprobada → Saldo: $0

7. Lista de pagos:
   - POS Débito: $8,000
   - POS Crédito: $7,000
   - Total: $15,000

8. Finalizar venta
```

### **Escenario 4: Cliente Cancela**
```
Total: $10,000

1. Click "POS Transbank"
2. Monto: $10,000
3. Click "Continuar al POS"
4. Cliente presiona "Cancelar" en el POS

5. Modal de error:
   ⚠️ Transacción no completada
   Cliente canceló en el POS
   
   [Intentar de nuevo] [Usar otro método]

6. Si elige "Intentar de nuevo":
   → Vuelve al paso 1
   
7. Si elige "Usar otro método":
   → Puede usar Efectivo, etc.
```

---

## 📋 INFORMACIÓN EN CUADRATURA

Cada pago POS se registra por separado:

```
CUADRATURA DE CAJA - 04/11/2025

Transbank Débito POS:
├─ Venta #123: $5,000 (Voucher: 005483)
├─ Venta #124: $8,000 (Voucher: 005484)
└─ Total Débito: $13,000

Transbank Crédito POS:
├─ Venta #123: $5,000 (Voucher: 404837)
├─ Venta #125: $7,000 (Voucher: 404838)
└─ Total Crédito: $12,000

TOTAL TRANSBANK: $25,000
```

---

## ✨ VENTAJAS

### ✅ **Flexibilidad Total**
- Cliente puede usar múltiples tarjetas
- Puede combinar con otros métodos
- Sin límite de transacciones POS

### ✅ **Control del Cajero**
- Decide cuánto cobrar en cada transacción
- Ve el saldo pendiente en todo momento
- Puede cancelar y usar otro método

### ✅ **UX Mejorada**
- Modal de monto claro y simple
- Validaciones en tiempo real
- Mensajes de error informativos
- Opción de reintentar fácilmente

### ✅ **Trazabilidad**
- Cada transacción se guarda por separado
- Cada una tiene su voucher único
- Fácil de auditar y conciliar

---

## 🎓 CARACTERÍSTICAS TÉCNICAS

### Ticket ID Único por Transacción:
```javascript
const timestamp = Date.now().toString().slice(-6);
const ticketIdPOS = `B:${ticketActual.ticket_id}-${timestamp}`;

// Ejemplo: B:123-156910 (ticket 123, timestamp único)
```

### Validación de Monto:
```javascript
preConfirm: () => {
    const monto = parseInt(document.getElementById('monto-pos-input').value);
    if (!monto || monto < 50) {
        Swal.showValidationMessage('El monto mínimo es $50');
        return false;
    }
    if (monto > saldoPendiente) {
        Swal.showValidationMessage('El monto no puede ser mayor al saldo pendiente');
        return false;
    }
    return monto;
}
```

### Actualización Automática:
```javascript
// Después de cada pago:
1. Agregar a pagosActuales[]
2. actualizarListaPagos()
3. actualizarResumenVenta()
4. Calcular nuevo saldo
5. Mostrar si está completo o falta
```

---

## 📊 COMPARACIÓN: ANTES vs AHORA

| Aspecto | Antes | Ahora |
|---------|-------|-------|
| Monto | Saldo completo obligatorio | Configurable ✅ |
| Pagos múltiples | NO ❌ | SÍ ✅ |
| Combinación métodos | Limitada | Total ✅ |
| Validación monto | NO | Sí (min/max) ✅ |
| Mensaje saldo | No informaba | Muestra pendiente ✅ |
| Reintentar | NO | Sí, con botón ✅ |
| Error "undefined" | Confuso | Explicado claramente ✅ |

---

## 🎯 INSTRUCCIONES DE PRUEBA

### Prueba 1: Pago Parcial

```bash
1. Reiniciar Django
2. Abrir: http://127.0.0.1:8000/app/pos-dashboard/
3. Crear ticket con total $10,000
4. Click "POS Transbank"
5. Cambiar monto a: 3000
6. Continuar al POS
7. Pasar tarjeta
8. Ver: "Saldo pendiente: $7,000"
9. Click "OK"
10. Ver en lista: "POS Transbank ... $3,000"
11. Saldo muestra: $7,000
```

### Prueba 2: Múltiples Pagos

```bash
1. Continuar del ejemplo anterior (saldo $7,000)
2. Click "POS Transbank" de nuevo
3. Monto sugerido: 7000 (automático)
4. Cambiar a: 4000
5. Pasar tarjeta
6. Ver: "Saldo pendiente: $3,000"
7. Click "POS Transbank" tercera vez
8. Monto: 3000
9. Pasar tarjeta
10. Ver: "✅ Ticket Completamente Pagado"
11. Lista muestra 3 pagos POS
```

### Prueba 3: Cancelación

```bash
1. Ticket con saldo $5,000
2. Click "POS Transbank"
3. Click "Continuar al POS"
4. En el POS físico → Presionar "Cancelar"
5. Ver modal de error mejorado
6. Click "Intentar de nuevo"
7. Volver al paso 2
```

---

## 🛡️ VALIDACIONES

### ✅ Al Ingresar Monto:

```javascript
// Monto mínimo
if (monto < 50) → "El monto mínimo es $50"

// Monto máximo
if (monto > saldoPendiente) → "El monto no puede ser mayor al saldo pendiente"

// Campo vacío
if (!monto) → "El monto mínimo es $50"
```

### ✅ Durante la Transacción:

```javascript
// Si cancela en modal de monto
if (!montoCobrar) → Sale sin procesar

// Si error en POS
catch(error) → Modal explicativo con opciones
```

---

## 💾 GUARDADO EN BASE DE DATOS

Cada pago se guarda individualmente:

```sql
TicketDetallePago
├─ ID: 1
│  ├─ ticket_id: 123
│  ├─ metodo_pago: 'TBK_DEBITO_POS'
│  ├─ monto: 5000
│  ├─ voucher: '005483'
│  ├─ tipo_tarjeta: 'VISA'
│  └─ notas: 'Terminal: C1905327 | Op: 012540 | ...'
│
├─ ID: 2
│  ├─ ticket_id: 123
│  ├─ metodo_pago: 'TBK_CREDITO_POS'
│  ├─ monto: 5000
│  ├─ voucher: '404837'
│  ├─ tipo_tarjeta: 'VI'
│  └─ notas: 'Terminal: C1905327 | Op: 012541 | ...'
```

---

## 🎯 RESUMEN DE MEJORAS

| Mejora | Descripción |
|--------|-------------|
| **Pagos parciales** | ✅ Permite dividir el pago en múltiples transacciones |
| **Modal de monto** | ✅ Permite ingresar monto específico a cobrar |
| **Validación monto** | ✅ Mínimo $50, Máximo = saldo pendiente |
| **Mensajes claros** | ✅ Indica saldo pendiente después de cada pago |
| **Reintentar fácil** | ✅ Botón para reintentar si falla |
| **Error "undefined"** | ✅ Mensaje claro sobre cancelación |
| **Múltiples usos** | ✅ Puede usar POS varias veces en mismo ticket |
| **Combinación métodos** | ✅ POS + Efectivo + Transferencia, etc. |

---

## 🎉 RESULTADO FINAL

### En Dashboard:
- ✅ Botón "POS Transbank" siempre visible
- ✅ Permite cobrar parcialmente
- ✅ Permite múltiples transacciones POS
- ✅ Actualiza saldo en tiempo real
- ✅ Mensajes claros en cada paso

### En Cuadratura:
- ✅ Cada pago POS registrado por separado
- ✅ Débito y Crédito diferenciados
- ✅ Vouchers y detalles completos
- ✅ Totales correctos

---

**Fecha**: 4 de Noviembre, 2025  
**Versión**: 2.0 - Pagos Parciales  
**Estado**: ✅ IMPLEMENTADO  
**Funcionalidad**: 🟢 COMPLETA

