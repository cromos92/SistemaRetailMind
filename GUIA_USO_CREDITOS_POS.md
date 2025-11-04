# 🎯 GUÍA DE USO - SISTEMA DE CRÉDITOS TRABAJADORES + POS

## ✅ IMPLEMENTACIÓN COMPLETADA

Todo está listo y funcionando. Aquí está la guía completa de uso:

---

## 📋 FLUJO COMPLETO DE OPERACIÓN

### **PASO 1: CREAR CRÉDITO** 📝

**Ubicación:** `http://127.0.0.1:8000/app/documentos/gestion-creditos/`

1. Click en **"Nuevo Crédito"**
2. Llenar formulario:
   - ✅ Seleccionar trabajador
   - ✅ Tipo de crédito
   - ✅ Monto solicitado
   - ✅ Mes de vencimiento
   - ✅ Número de cuotas
   - ✅ Motivo
3. Click en **"Guardar Crédito"**
4. Se crea con estado **ACTIVO** automáticamente
5. Aparece modal de éxito con botón **"Imprimir Voucher"**
6. Click en **"Imprimir Voucher"**

---

### **PASO 2: IMPRIMIR VOUCHER** 🖨️

**Se abre ventana con voucher térmico que incluye:**

```
══════════════════════════════════════
           NOMBRE EMPRESA
        (Letra grande 24px)
       Sucursal - Dirección

══════════════════════════════════════
       VOUCHER CRÉDITO TRABAJADOR

┌────────────────────────────────────┐
│  ▐│││▌│▌│││▌  [CÓDIGO BARRAS]     │
│      Altura 80px, Ancho 3          │
└────────────────────────────────────┘
        CR-2025-0002
       (Letra 24px bold)

══════════════════════════════════════
       DATOS TRABAJADOR (20px)

Nombre:              (15px bold)
JUAN PÉREZ

RUT:
12.345.678-9

Código:
VEND001

══════════════════════════════════════
      DETALLES CRÉDITO (20px)

Tipo:
Préstamo de Empresa

Emisión:              Vencimiento:
03/11/2025           31/12/2025

══════════════════════════════════════
╔════════════════════════════════════╗
║      MONTO APROBADO (28px)         ║
║                                    ║
║      $500,000 (36px)               ║
╚════════════════════════════════════╝

══════════════════════════════════════
MOTIVO: (14px)
Anticipo para emergencia familiar...

══════════════════════════════════════
         FIRMAS (20px)

╔════════════════════════════════════╗
║ AUTORIZADO POR: (13px bold)       ║
║                                    ║
║ [Espacio 50px para firma]          ║
║ ──────────────────────             ║
║ Admin Usuario                      ║
║ FIRMA Y TIMBRE                     ║
╚════════════════════════════════════╝

╔════════════════════════════════════╗
║ RECIBÍ CONFORME: (13px bold)      ║
║                                    ║
║ [Espacio 50px para firma]          ║
║ ──────────────────────             ║
║ JUAN PÉREZ                         ║
║ RUT: 12.345.678-9                  ║
║ FIRMA TRABAJADOR                   ║
╚════════════════════════════════════╝

══════════════════════════════════════
CONDICIONES:
• Compromiso de pago del trabajador
• Descuentos vía nómina mensual
• Documento con validez legal

══════════════════════════════════════
03/11/2025 14:30
Usuario: admin
Doc ID: CR-2025-0002
══════════════════════════════════════
```

**Características del voucher:**
- ✅ Letras grandes (14-36px)
- ✅ Fuente Arial (óptima para térmicas)
- ✅ Código de barras CODE128 grande
- ✅ 2-3 espacios para firmas (según requiera aval)
- ✅ Bordes gruesos (3px)
- ✅ Auto-impresión al abrir

---

### **PASO 3: FIRMAR VOUCHER** ✍️

1. Imprimir 2 copias
2. Hacer firmar a:
   - ✅ Autorizador (Gerente/Supervisor)
   - ✅ Trabajador
   - ✅ Aval (si aplica)
3. Una copia para archivo
4. Una copia para el trabajador

---

### **PASO 4: USAR EN POS** 🛒

**Ubicación:** `http://127.0.0.1:8000/app/pos-dashboard/`

#### **A. Validar Crédito**

1. Trabajador llega con el voucher impreso
2. Cajero hace click en botón **"CRÉDITO TRABAJADOR"** (botón rojo con borde punteado)
3. Aparece modal pidiendo código
4. **Opciones:**
   - Escanear código de barras del voucher
   - O escribir manualmente: `CR-2025-0002`
5. Click en **"Validar"**

#### **B. Sistema Valida**

El sistema verifica:
- ✅ Código existe
- ✅ Crédito está ACTIVO
- ✅ Tiene saldo disponible
- ✅ No está vencido

Si todo OK, muestra:
```
┌────────────────────────────────────┐
│     ✅ Crédito Válido              │
│                                    │
│  👤 JUAN PÉREZ                     │
│  RUT: 12.345.678-9                 │
│  Código: VEND001                   │
│  Crédito: CR-2025-0002             │
│                                    │
│    Saldo Disponible:               │
│       $500,000                     │
│                                    │
│  Tipo: Préstamo Empresa            │
│  Vence: 31/12/2025 (58 días)       │
└────────────────────────────────────┘
```

#### **C. Aparece Banner Verde**

En la interfaz del POS aparece un banner:
```
┌────────────────────────────────────┐
│ 👑 CRÉDITO ACTIVO            [×]   │
│ JUAN PÉREZ                         │
│ Saldo: $500,000                    │
└────────────────────────────────────┘
```

#### **D. Agregar Productos**

1. Escanear o agregar productos normalmente
2. Total ejemplo: $120,000

#### **E. Pago Automático**

- El sistema **AGREGA AUTOMÁTICAMENTE** el pago con crédito
- Aparece en "Pagos Registrados":
  ```
  ┌────────────────────────────────────┐
  │ Crédito Trabajador           [🗑]  │
  │ 👤 JUAN PÉREZ                      │
  │ $120,000                           │
  └────────────────────────────────────┘
  ```

#### **F. Finalizar Venta**

1. Click en **"Finalizar Venta"**
2. El sistema:
   - ✅ Crea el ticket/documento
   - ✅ Descuenta $120,000 del crédito
   - ✅ Nuevo saldo: $380,000
   - ✅ Muestra confirmación

**Mensaje de éxito:**
```
┌────────────────────────────────────┐
│     ✅ ¡Venta Completada!          │
│                                    │
│  Ticket #1234 procesado            │
│                                    │
│  👑 Crédito Trabajador             │
│  Usado: $120,000                   │
│  Nuevo saldo: $380,000             │
└────────────────────────────────────┘
```

---

## 🔄 **COMPRAS MÚLTIPLES**

El trabajador puede seguir comprando con el mismo código:

```
Compra 1: $120,000 → Saldo: $380,000
Compra 2: $80,000  → Saldo: $300,000
Compra 3: $50,000  → Saldo: $250,000
...hasta agotar el saldo
```

**Cuando saldo = $0:**
- Estado cambia a: **PAGADO** ✅
- Mensaje: "CRÉDITO PAGADO COMPLETO"
- Ya no se puede usar más

---

## ⚠️ **VALIDACIONES AUTOMÁTICAS**

El sistema valida automáticamente:

| Validación | Comportamiento |
|------------|----------------|
| **Código inválido** | Muestra error y permite reintentar |
| **Crédito no ACTIVO** | Rechaza y muestra estado actual |
| **Sin saldo** | Rechaza con mensaje "Sin saldo disponible" |
| **Crédito vencido** | Rechaza con fecha de vencimiento |
| **Total > Saldo** | Permite usar saldo parcial + otro método |

---

## 💡 **CASOS DE USO**

### **Caso 1: Compra menor al saldo del crédito**
```
Saldo crédito: $500,000
Total compra: $120,000
→ Usa $120,000 del crédito
→ Nuevo saldo: $380,000
```

### **Caso 2: Compra mayor al saldo del crédito**
```
Saldo crédito: $100,000
Total compra: $250,000
→ Usa $100,000 del crédito
→ Faltan $150,000
→ Sistema pregunta si desea completar con otro método
→ Opciones: Efectivo, Tarjeta, etc.
```

### **Caso 3: Última compra (agota el saldo)**
```
Saldo crédito: $80,000
Total compra: $80,000
→ Usa $80,000 del crédito
→ Saldo: $0
→ Estado cambia a: PAGADO ✅
→ Mensaje: "Crédito pagado completamente"
```

---

## 🎯 **ELEMENTOS CLAVE DE LA INTERFAZ**

### **En Gestión de Créditos:**
- ✅ Botón "Nuevo Crédito"
- ✅ Modal de éxito con "Imprimir Voucher"
- ✅ Menú de acciones con "Imprimir Voucher" siempre disponible
- ✅ Todos los créditos se crean ACTIVOS directamente

### **En POS:**
- ✅ Botón "CRÉDITO TRABAJADOR" (rojo con borde punteado)
- ✅ Modal de validación de código
- ✅ Banner verde cuando hay crédito activo
- ✅ Pago se agrega automáticamente
- ✅ Indicador en lista de pagos (fondo amarillo)

---

## 📊 **REPORTES Y SEGUIMIENTO**

### **Ver historial de uso del crédito:**

1. En gestión de créditos
2. Click en crédito → "Ver Detalle"
3. Sección "Historial de Pagos" muestra:
   ```
   Número | Fecha      | Monto    | Método              | Observaciones
   ──────────────────────────────────────────────────────────────────
   #1     | 03/11/2025 | $120,000 | Crédito Trabajador | Compra POS - Ticket #1234
   #2     | 05/11/2025 | $80,000  | Crédito Trabajador | Compra POS - Ticket #1256
   ```

---

## 🔧 **SOLUCIÓN DE PROBLEMAS**

### **Error: "Código no encontrado"**
- ✅ Verificar que el código esté bien escrito
- ✅ Código debe estar en MAYÚSCULAS: `CR-2025-0002`
- ✅ Verificar que el crédito exista en gestión de créditos

### **Error: "Crédito no está activo"**
- ✅ El crédito debe estar en estado ACTIVO
- ✅ Todos los créditos nuevos se crean ACTIVOS automáticamente
- ✅ Si dice PAGADO, ya se agotó el saldo

### **Error: "Sin saldo disponible"**
- ✅ El crédito ya se usó completamente
- ✅ Verificar saldo en gestión de créditos

### **Voucher se ve borroso**
- ✅ Ya está optimizado para Epson TM-T80
- ✅ Usar fuente Arial (no Courier)
- ✅ Letras grandes (14-36px)
- ✅ Si persiste, verificar configuración de impresora

---

## 🎉 **CARACTERÍSTICAS IMPLEMENTADAS**

### **Backend:**
- ✅ Creación automática en estado ACTIVO
- ✅ API validar código de crédito
- ✅ API usar crédito en venta
- ✅ API imprimir voucher térmico
- ✅ Cambio automático de estado a PAGADO
- ✅ Registro de historial completo

### **Frontend - Gestión:**
- ✅ Modal de éxito con botón imprimir
- ✅ Botón imprimir en menú de cada crédito
- ✅ Voucher optimizado para Epson TM-T80

### **Frontend - POS:**
- ✅ Botón "CRÉDITO TRABAJADOR"
- ✅ Validación de código con escaneo o manual
- ✅ Banner indicador de crédito activo
- ✅ Pago automático al validar
- ✅ Validación de saldo disponible
- ✅ Combinación con otros métodos si falta
- ✅ Descuento automático al finalizar venta
- ✅ Mensaje con nuevo saldo

---

## 📞 **EJEMPLO PRÁCTICO COMPLETO**

```
1. Supervisor crea crédito para "Juan Pérez" de $500,000
   → Sistema genera: CR-2025-0005
   → Estado: ACTIVO
   
2. Se imprime voucher con código de barras
   → Se firman las partes
   → Juan guarda su copia

3. Juan va a comprar al día siguiente
   → Lleva su voucher
   
4. Cajero hace click en "CRÉDITO TRABAJADOR"
   → Escanea código de barras del voucher
   → Sistema muestra: "✅ Saldo: $500,000"
   → Aparece banner verde

5. Juan selecciona productos:
   → Zapatillas: $89,990
   → Polera: $25,990
   → Total: $115,980
   
6. Sistema agrega pago automáticamente:
   → Método: Crédito Trabajador
   → Monto: $115,980
   
7. Cajero click en "Finalizar Venta"
   → ✅ Ticket #1234 creado
   → ✅ Se descuenta del crédito
   → Nuevo saldo: $384,020
   → Muestra mensaje de éxito

8. Una semana después, Juan vuelve a comprar
   → Cajero valida código: CR-2025-0005
   → Sistema muestra: "Saldo: $384,020"
   → Juan compra por $80,000
   → Nuevo saldo: $304,020

9. Continúa así hasta agotar los $500,000
   → Cuando saldo = $0
   → Estado cambia a: PAGADO ✅
```

---

## 📁 **ARCHIVOS MODIFICADOS**

| Archivo | Cambios |
|---------|---------|
| `models.py` | ✅ Generación robusta de número único |
| `views_modulo_creditos.py` | ✅ Creación ACTIVA, APIs nuevas, voucher térmico |
| `urls.py` | ✅ 3 URLs nuevas para APIs |
| `gestion_creditos.html` | ✅ Modal con imprimir, botón en acciones |
| `generacionVentas.html` | ✅ Botón crédito, validación, integración completa |

---

## 🎯 **BENEFICIOS**

- ⚡ **Proceso rápido:** 1 click para crear crédito activo
- 🖨️ **Voucher profesional:** Con firmas y código de barras
- 🔒 **Seguro:** Validaciones automáticas en cada paso
- 📊 **Trazable:** Historial completo de uso
- 💰 **Flexible:** Permite combinar métodos de pago
- ✅ **Automático:** Cambio de estado cuando se paga completo

---

## 🚀 **PRÓXIMOS PASOS OPCIONALES**

### **Mejoras futuras (si quieres):**
1. Notificaciones de vencimiento por email
2. Reporte Excel para descuentos de nómina
3. Dashboard de estadísticas de créditos
4. Límite de crédito por trabajador
5. Scoring de cumplimiento de pagos

---

**Fecha:** 3 de Noviembre, 2025  
**Estado:** ✅ COMPLETAMENTE FUNCIONAL  
**Versión:** 1.0 - Sistema Integrado de Créditos + POS

