# Guía de Uso - SDK Transbank POS Integrado

## 📋 Índice
1. [Introducción](#introducción)
2. [Requisitos Previos](#requisitos-previos)
3. [Instalación](#instalación)
4. [Métodos Disponibles](#métodos-disponibles)
5. [Ejemplos de Uso](#ejemplos-de-uso)
6. [Manejo de Errores](#manejo-de-errores)
7. [Solución de Problemas](#solución-de-problemas)

---

## 🎯 Introducción

Este documento describe cómo usar el SDK oficial de Transbank POS Integrado en RetailMind.
El SDK proporciona una interfaz JavaScript para comunicarse con terminales POS de Transbank.

## ✅ Requisitos Previos

1. **Agente Desktop de Transbank** ejecutándose en `https://localhost:8090`
2. **Terminal POS** físico conectado al computador
3. **Navegador moderno** con soporte para ES6+ y Promises

## 📦 Instalación

El SDK ya está integrado en RetailMind. Los archivos necesarios son:

- `/static/js/transbank-pos-sdk.js` - Librería oficial de Transbank
- `/static/js/pos-transbank.js` - Wrapper de integración con RetailMind

## 🛠️ Métodos Disponibles

### 1. Conexión y Configuración

#### `initialize()`
Inicializa y conecta con el agente POS.

```javascript
const pos = new TransbankPOSIntegration();

try {
    const result = await pos.initialize();
    if (result.success) {
        console.log('✅ Conectado al agente POS');
        console.log('Puertos disponibles:', result.ports);
    }
} catch (error) {
    console.error('❌ Error:', error.message);
}
```

**Retorna:**
```javascript
{
    success: true,
    ports: ['COM1', 'COM2', ...],
    message: 'Conectado exitosamente al agente POS'
}
```

---

#### `openPort(portName, baudRate = 115200)`
Abre un puerto específico para comunicarse con el POS.

```javascript
try {
    await pos.openPort('COM3', 115200);
    console.log('✅ Puerto COM3 abierto');
} catch (error) {
    console.error('❌ Error abriendo puerto:', error);
}
```

**Parámetros:**
- `portName` (string): Nombre del puerto (ej: 'COM1', 'COM3')
- `baudRate` (number, opcional): Velocidad de conexión (default: 115200)

---

#### `closePort()`
Cierra el puerto actual.

```javascript
await pos.closePort();
```

---

#### `autoconnect(baudRate = 115200)`
Busca y conecta automáticamente al POS.

```javascript
try {
    const result = await pos.autoconnect();
    console.log('✅ POS detectado en:', result.port);
} catch (error) {
    console.error('❌ No se pudo detectar ningún POS');
}
```

**Retorna:**
```javascript
{
    success: true,
    port: 'COM3',
    message: 'POS conectado automáticamente en COM3',
    data: {...}
}
```

---

#### `disconnect()`
Desconecta del agente POS.

```javascript
await pos.disconnect();
console.log('🔌 Desconectado');
```

---

### 2. Operaciones de Venta

#### `doSale(amount, ticketId, onStatusUpdate)`
Realiza una venta.

```javascript
const amount = 15990; // Monto en pesos chilenos
const ticketId = 'TKT-' + Date.now();

// Callback para estados intermedios
function onStatusUpdate(status) {
    console.log('📊 Estado:', status);
    // Actualizar UI con el estado
}

try {
    const result = await pos.doSale(amount, ticketId, onStatusUpdate);
    
    if (result.success) {
        console.log('✅ Venta aprobada');
        console.log('Código de autorización:', result.authorization_code);
        console.log('Tipo de tarjeta:', result.card_type);
        console.log('Últimos 4 dígitos:', result.last_4_digits);
    } else {
        console.log('❌ Venta rechazada:', result.response_message);
    }
} catch (error) {
    console.error('❌ Error en venta:', error);
}
```

**Parámetros:**
- `amount` (number): Monto en pesos chilenos
- `ticketId` (string): ID único del ticket/boleta
- `onStatusUpdate` (function, opcional): Callback para estados intermedios

**Retorna:**
```javascript
{
    success: true,
    response_code: '00',
    response_message: 'Transacción aprobada',
    authorization_code: '123456',
    card_type: 'DEBITO',
    card_brand: 'VISA',
    card_number: '************1234',
    last_4_digits: '1234',
    operation_number: '789012',
    installments: 1,
    commerce_code: '597020000540',
    terminal_id: '12345678',
    amount: 15990,
    timestamp: '2025-11-07T10:30:00Z'
}
```

---

#### `doMulticodeSale(amount, ticketId, commerceCode, onStatusUpdate)`
Realiza una venta con múltiples códigos de comercio.

```javascript
const commerceCode = "597020000541"; // Código de comercio específico

try {
    const result = await pos.doMulticodeSale(
        50000,
        'TKT-MULTI-001',
        commerceCode,
        onStatusUpdate
    );
    
    if (result.success) {
        console.log('✅ Venta multicode aprobada');
    }
} catch (error) {
    console.error('❌ Error:', error);
}
```

**Parámetros:**
- `amount` (number): Monto en pesos
- `ticketId` (string): ID del ticket
- `commerceCode` (string, opcional): Código de comercio (default: "0")
- `onStatusUpdate` (function, opcional): Callback para estados

---

#### `doRefund(operationId)`
Anula una transacción.

```javascript
const operationId = '789012'; // Número de operación a anular

try {
    const result = await pos.doRefund(operationId);
    
    if (result.success) {
        console.log('✅ Transacción anulada');
    }
} catch (error) {
    console.error('❌ Error en anulación:', error);
}
```

**Parámetros:**
- `operationId` (string): Número de operación a anular

---

### 3. Consultas

#### `getLastSale()`
Obtiene información de la última venta.

```javascript
try {
    const result = await pos.getLastSale();
    console.log('📄 Última venta:', result);
} catch (error) {
    console.error('❌ Error:', error);
}
```

**Retorna:**
Mismo formato que `doSale()`

---

#### `getTotals()`
Obtiene los totales del día.

```javascript
try {
    const totals = await pos.getTotals();
    console.log('📊 Totales del día:', totals);
    console.log('Total ventas:', totals.sales_amount);
    console.log('Cantidad transacciones:', totals.sales_count);
} catch (error) {
    console.error('❌ Error:', error);
}
```

**Retorna:**
```javascript
{
    sales_count: 45,
    sales_amount: 1250000,
    refunds_count: 2,
    refunds_amount: 35000,
    last_sale: {...}
}
```

---

#### `getDetails(printOnPos = false)`
Obtiene detalles de ventas del día.

```javascript
try {
    // Obtener sin imprimir
    const details = await pos.getDetails(false);
    
    // Obtener e imprimir en el POS
    const detailsWithPrint = await pos.getDetails(true);
    
    console.log('📋 Detalles:', details);
} catch (error) {
    console.error('❌ Error:', error);
}
```

**Parámetros:**
- `printOnPos` (boolean): Si true, imprime en el POS

---

#### `poll()`
Verifica el estado del terminal.

```javascript
try {
    const status = await pos.poll();
    console.log('📡 Terminal OK:', status);
} catch (error) {
    console.error('❌ Terminal no responde');
}
```

---

#### `getPortStatus()`
Obtiene el estado del puerto y conexión.

```javascript
try {
    const status = await pos.getPortStatus();
    console.log('Estado:', status);
} catch (error) {
    console.error('❌ Error:', error);
}
```

**Retorna:**
```javascript
{
    success: true,
    connected: true,
    currentPort: 'COM3',
    agentStatus: {...}
}
```

---

### 4. Operaciones Especiales

#### `loadKeys()`
Carga las llaves criptográficas en el POS.

```javascript
try {
    const result = await pos.loadKeys();
    console.log('✅ Llaves cargadas correctamente');
} catch (error) {
    console.error('❌ Error cargando llaves:', error);
}
```

**Nota:** Esta operación se debe realizar cuando se configura el POS por primera vez o cuando Transbank lo indica.

---

#### `closeDay()`
Realiza el cierre de día (cierre de batch).

```javascript
try {
    const result = await pos.closeDay();
    console.log('✅ Cierre de día completado');
    console.log('Total día:', result.data);
} catch (error) {
    console.error('❌ Error en cierre:', error);
}
```

**Nota:** Esto imprime el cierre en el POS y resetea los contadores.

---

#### `setNormalMode()`
Cambia el POS a modo normal.

```javascript
try {
    await pos.setNormalMode();
    console.log('✅ Modo normal activado');
} catch (error) {
    console.error('❌ Error:', error);
}
```

---

## 💡 Ejemplos de Uso

### Ejemplo Completo: Flujo de Venta

```javascript
// 1. Inicializar y conectar
const pos = new TransbankPOSIntegration();

async function realizarVenta() {
    try {
        // Paso 1: Inicializar conexión
        console.log('🔌 Conectando al agente POS...');
        const initResult = await pos.initialize();
        
        if (!initResult.success) {
            throw new Error('No se pudo conectar al agente POS');
        }
        
        // Paso 2: Autoconectar al terminal
        console.log('🔍 Buscando terminal POS...');
        const autoResult = await pos.autoconnect();
        
        console.log(`✅ Conectado al puerto: ${autoResult.port}`);
        
        // Paso 3: Verificar terminal con poll
        console.log('📡 Verificando terminal...');
        await pos.poll();
        
        // Paso 4: Realizar venta
        console.log('💳 Procesando venta...');
        const amount = 25990;
        const ticketId = 'TICKET-' + Date.now();
        
        const result = await pos.doSale(amount, ticketId, (status) => {
            console.log('📊 Estado:', status);
            // Actualizar UI aquí
        });
        
        // Paso 5: Procesar resultado
        if (result.success) {
            console.log('✅ ¡VENTA APROBADA!');
            console.log('═══════════════════════════');
            console.log('Autorización:', result.authorization_code);
            console.log('Tarjeta:', result.card_type, result.card_brand);
            console.log('Últimos 4:', result.last_4_digits);
            console.log('Monto:', result.amount);
            console.log('Operación:', result.operation_number);
            console.log('═══════════════════════════');
            
            // Guardar en base de datos
            await guardarTransaccionEnBD(result);
            
            return result;
        } else {
            console.log('❌ Venta rechazada:', result.response_message);
            throw new Error(result.response_message);
        }
        
    } catch (error) {
        console.error('❌ Error en venta:', error);
        throw error;
    } finally {
        // Paso 6: Cerrar conexión
        await pos.closePort();
        console.log('🔌 Puerto cerrado');
    }
}

// Ejecutar
realizarVenta()
    .then(result => {
        console.log('Venta completada exitosamente');
    })
    .catch(error => {
        console.error('Error en el proceso:', error);
    });
```

---

### Ejemplo: Integración con Django

```javascript
// Función para guardar transacción en Django
async function guardarTransaccionEnBD(resultadoPOS) {
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    
    const response = await fetch('/pos/guardar-transaccion/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({
            ticket_pos: resultadoPOS.operation_number,
            monto: resultadoPOS.amount,
            codigo_autorizacion: resultadoPOS.authorization_code,
            tipo_tarjeta: resultadoPOS.card_type,
            marca_tarjeta: resultadoPOS.card_brand,
            ultimos_digitos: resultadoPOS.last_4_digits,
            codigo_respuesta: resultadoPOS.response_code,
            mensaje_respuesta: resultadoPOS.response_message,
            codigo_comercio: resultadoPOS.commerce_code,
            terminal_id: resultadoPOS.terminal_id,
            cuotas: resultadoPOS.installments
        })
    });
    
    const data = await response.json();
    
    if (!data.success) {
        throw new Error('Error guardando en base de datos');
    }
    
    return data;
}
```

---

### Ejemplo: UI con Actualización en Tiempo Real

```javascript
// HTML
<div id="pos-status">Desconectado</div>
<div id="transaction-log"></div>
<button onclick="procesarVenta()">Procesar Venta</button>

// JavaScript
function actualizarEstado(mensaje, tipo = 'info') {
    const statusDiv = document.getElementById('pos-status');
    statusDiv.textContent = mensaje;
    statusDiv.className = tipo; // 'success', 'error', 'warning', 'info'
}

function agregarLog(mensaje) {
    const logDiv = document.getElementById('transaction-log');
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${mensaje}`;
    logDiv.insertBefore(entry, logDiv.firstChild);
}

async function procesarVenta() {
    const pos = new TransbankPOSIntegration();
    
    try {
        actualizarEstado('Conectando...', 'warning');
        agregarLog('Iniciando conexión con POS');
        
        await pos.initialize();
        agregarLog('Agente POS conectado');
        
        await pos.autoconnect();
        actualizarEstado('Conectado', 'success');
        agregarLog('Terminal POS detectado');
        
        actualizarEstado('Procesando venta...', 'warning');
        
        const result = await pos.doSale(10000, 'TKT-001', (status) => {
            agregarLog(`Estado: ${JSON.stringify(status)}`);
        });
        
        if (result.success) {
            actualizarEstado('Venta aprobada', 'success');
            agregarLog(`Autorización: ${result.authorization_code}`);
        } else {
            actualizarEstado('Venta rechazada', 'error');
            agregarLog(`Rechazo: ${result.response_message}`);
        }
        
    } catch (error) {
        actualizarEstado('Error', 'error');
        agregarLog(`Error: ${error.message}`);
    } finally {
        await pos.closePort();
    }
}
```

---

## ⚠️ Manejo de Errores

### Códigos de Respuesta Transbank

| Código | Descripción |
|--------|-------------|
| `00` | Transacción aprobada |
| `01` | Debe ser autorizada por el emisor |
| `05` | Transacción rechazada |
| `14` | Número de tarjeta inválido |
| `51` | Fondos insuficientes |
| `55` | Clave incorrecta |
| `58` | Transacción no permitida en el terminal |
| `91` | Emisor no disponible |
| `96` | Error en el sistema |

### Manejo de Excepciones

```javascript
try {
    const result = await pos.doSale(amount, ticket);
} catch (error) {
    if (error.code === 'UNKNOWN_ERROR') {
        console.error('Error desconocido:', error.message);
    } else if (error.name === 'TransactionError') {
        console.error('Error de transacción:', error.message);
        console.log('Sugerencia:', error.suggestion);
    } else {
        console.error('Error general:', error);
    }
}
```

---

## 🔧 Solución de Problemas

### Problema: No se puede conectar al agente POS

**Síntoma:** Error "No se pudo conectar al agente POS"

**Soluciones:**
1. Verificar que el agente desktop está ejecutándose
2. Verificar que corre en `https://localhost:8090`
3. Revisar el firewall de Windows
4. Reiniciar el agente desktop

```bash
# Verificar si el agente está corriendo
# Abrir navegador en: https://localhost:8090
```

---

### Problema: No se detecta el POS

**Síntoma:** Error "No se pudo detectar ningún POS"

**Soluciones:**
1. Verificar que el POS está conectado físicamente
2. Verificar que el puerto COM es correcto
3. Probar con otros puertos:

```javascript
const ports = ['COM1', 'COM2', 'COM3', 'COM4'];

for (const port of ports) {
    try {
        await pos.openPort(port);
        const status = await pos.poll();
        console.log(`✅ POS encontrado en ${port}`);
        break;
    } catch (error) {
        console.log(`❌ No hay POS en ${port}`);
    }
}
```

---

### Problema: Timeout en transacción

**Síntoma:** Error "Timeout: We have not received anything from POS"

**Soluciones:**
1. Verificar que el POS está encendido
2. Verificar el cable de conexión
3. Reiniciar el POS
4. Verificar que no hay otra aplicación usando el puerto

---

### Problema: Error de llaves

**Síntoma:** El POS no procesa transacciones

**Solución:**

```javascript
// Cargar llaves nuevamente
await pos.loadKeys();
```

---

## 📞 Soporte

Para problemas con el SDK de Transbank:
- **Documentación oficial:** https://www.transbankdevelopers.cl
- **Soporte Transbank:** soporte@transbank.cl
- **Mesa de ayuda:** (600) 638 9000

Para problemas con RetailMind:
- **Soporte interno del proyecto**

---

## 📝 Notas Importantes

1. **Siempre cerrar el puerto** después de usar el POS
2. **Manejar errores** apropiadamente para mejor experiencia de usuario
3. **Guardar transacciones** exitosas en la base de datos inmediatamente
4. **No reintentarautomáticamente** transacciones fallidas sin confirmación del usuario
5. **Realizar cierre de día** todos los días al final de la jornada

---

**Última actualización:** Noviembre 7, 2025
**Versión del SDK:** Oficial Transbank
**Versión RetailMind:** Compatible con sistema actual

