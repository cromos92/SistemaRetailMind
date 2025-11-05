# 🔧 Solución: Timeout ACK (2000ms) - POS Transbank

## 🐛 PROBLEMA ACTUAL

```
Error: ACK has not been received in 2000 ms.
```

### ¿Qué significa?

- **ACK** = Acknowledgment (confirmación)
- El **agente Transbank** espera que el **POS físico** confirme que recibió el comando
- El timeout es solo **2000ms (2 segundos)**
- El cliente necesita más tiempo para:
  1. Pasar la tarjeta
  2. **Seleccionar Débito/Crédito** ← Aquí se cae
  3. Ingresar cuotas
  4. Confirmar

---

## 🎯 SOLUCIONES IMPLEMENTADAS (Temporal)

### 1. **Instrucciones Claras al Usuario**

Modal inicial ahora muestra:

```
⚠️ IMPORTANTE: El cliente debe:
1. Pasar la tarjeta
2. Seleccionar rápido Débito/Crédito  ← ÉNFASIS
3. Ingresar cuotas si aplica

⏱️ Debe hacerlo en menos de 60 segundos
```

**Botón**: "✅ Cliente listo - Continuar"

### 2. **Instrucciones Durante el Pago**

```
✅ Conectado al POS
Monto a cobrar: $5,000

PASO 1: Pase la tarjeta
PASO 2: Seleccione Débito/Crédito  ← Instrucción clara
PASO 3: Ingrese cuotas (si aplica)

⏳ Tiempo máximo: 90 segundos
```

### 3. **Callback Mejorado**

```javascript
const statusCallback = (statusMessage) => {
    // Muestra mensajes del POS en tiempo real
    // Ayuda al cajero a guiar al cliente
};
```

### 4. **Parámetro sendStatus**

```javascript
await Transbank.POS.doSale(
    monto,
    ticket,
    statusCallback,
    true  // sendStatus = true (mantiene comunicación activa)
);
```

### 5. **Manejo de Error Mejorado**

Si falla por timeout:
```
⚠️ Transacción no completada

La transacción no se completó. Posibles causas:
- Cliente canceló en el POS
- Timeout esperando tarjeta
- Cliente no seleccionó débito/crédito a tiempo

[Intentar de nuevo] [Usar otro método]
```

---

## ✅ SOLUCIÓN DEFINITIVA (Configurar Agente)

El problema está en el **Agente Transbank**, no en el SDK web. Necesitas configurar el agente con timeout más largo.

### Opción A: Actualizar Agente Transbank

1. **Descargar última versión del agente:**
```
https://github.com/TransbankDevelopers/transbank-pos-sdk-web-agent2/releases/latest
```

2. **Desinstalar versión actual**

3. **Instalar nueva versión**

4. **Verificar que la nueva versión tenga timeout más largo**

### Opción B: Configurar Archivo del Agente

El agente puede tener un archivo de configuración. Buscar en:

```
Windows:
C:\Program Files\Transbank\POS Agent\config.json
C:\Users\TU_USUARIO\AppData\Local\Transbank\config.json

Mac/Linux:
~/.transbank/config.json
/etc/transbank/config.json
```

**Agregar/modificar**:
```json
{
    "ackTimeout": 30000,
    "transactionTimeout": 90000,
    "connectionTimeout": 10000
}
```

### Opción C: Variable de Entorno

Configurar antes de iniciar el agente:

```powershell
# Windows PowerShell
$env:TRANSBANK_ACK_TIMEOUT=30000
$env:TRANSBANK_TRANSACTION_TIMEOUT=90000

# Luego iniciar el agente
Start-Process "C:\Program Files\Transbank\POS Agent\tbk_agent.exe"
```

### Opción D: Modificar Código del Agente (Avanzado)

Si tienes acceso al código fuente del agente:

```javascript
// En el archivo del agente (ej: main.js)
// Buscar:
const ACK_TIMEOUT = 2000;

// Cambiar a:
const ACK_TIMEOUT = 30000; // 30 segundos
```

---

## 🎯 SOLUCIÓN PRÁCTICA INMEDIATA

### Mientras configuras el agente:

#### 1. **Instruir al Cliente:**

```
Cajero dice:
"Por favor tenga lista su tarjeta y seleccione 
rápidamente si es débito o crédito cuando el POS 
le pregunte. Tiene aprox. 60 segundos."
```

#### 2. **Usar POS en Modo Rápido:**

Si el POS permite configuración, activar:
- ✅ **Detección automática** de tipo de tarjeta
- ✅ **Sin cuotas** (solo contado)
- ✅ **Sin menú** (más rápido)

#### 3. **Practicar con Clientes:**

```
Primera vez: Explicar el proceso
- "Pase la tarjeta"
- "Seleccione rápido débito o crédito"
- "Confirme"

Después de explicar, los clientes lo harán más rápido
```

---

## 📊 TIEMPOS ACTUALES

| Evento | Tiempo | Estado |
|--------|--------|--------|
| Conectar al agente | ~1s | ✅ OK |
| Enviar comando al POS | <1s | ✅ OK |
| **Esperar ACK del POS** | **2s** | ❌ MUY CORTO |
| Cliente pasa tarjeta | 3-5s | OK |
| Cliente selecciona tipo | 2-5s | ← PROBLEMA |
| Cliente ingresa cuotas | 3-10s | OK |
| Autorización banco | 5-15s | OK |
| **TOTAL** | **15-40s** | ✅ NORMAL |

**Problema**: ACK timeout (2s) < Tiempo real necesario (15-40s)

---

## 🔍 VERIFICAR VERSION DEL AGENTE

En PowerShell:

```powershell
# Ubicación del agente
cd "C:\Program Files\Transbank\POS Agent\"

# Ver versión
Get-ItemProperty .\tbk_agent.exe | Select-Object VersionInfo

# O verificar en Agregar/Quitar Programas
Get-WmiObject -Class Win32_Product | Where-Object { $_.Name -like "*Transbank*" }
```

### Versiones conocidas:

- **v1.0.0**: Timeout 2000ms (problema actual)
- **v1.1.0+**: Timeout configurable
- **v2.0.0+**: Timeout 30000ms por defecto

**Actualizar si estás en v1.0.0**

---

## 💡 WORKAROUND TEMPORAL

### Mientras actualizas el agente:

#### **Opción 1: Reiniciar agente antes de cada venta**

```powershell
# Script rápido
taskkill /F /IM tbk_agent.exe
timeout /t 2
start "" "C:\Program Files\Transbank\POS Agent\tbk_agent.exe"
```

#### **Opción 2: Usar venta rápida**

- Entrenar al cliente a ser rápido
- Tener tarjeta lista antes de hacer click
- Seleccionar tipo inmediatamente

#### **Opción 3: Configurar POS sin menú**

Si el comercio permite:
- Configurar POS para detectar tipo automáticamente
- Desactivar selección de cuotas (solo contado)
- Modo "fast transaction"

---

## 🎓 PASOS PARA ACTUALIZAR AGENTE

### 1. **Descargar nueva versión**
```
https://github.com/TransbankDevelopers/transbank-pos-sdk-web-agent2/releases
```

### 2. **Cerrar agente actual**
```powershell
taskkill /F /IM tbk_agent.exe
```

### 3. **Desinstalar versión anterior**
```
Panel de Control → Programas → Transbank POS Agent → Desinstalar
```

### 4. **Instalar nueva versión**

### 5. **Verificar configuración**

Buscar archivo `config.json` y verificar timeouts:
```json
{
    "ackTimeout": 30000,
    "transactionTimeout": 90000
}
```

### 6. **Reiniciar y probar**

---

## 📋 CHECKLIST

### Verificar:
- [ ] Versión del agente Transbank
- [ ] Archivo de configuración existe
- [ ] Timeout ACK configurado
- [ ] Timeout de transacción configurado
- [ ] Agente reiniciado
- [ ] Prueba de venta exitosa

---

## 🎯 MIENTRAS TANTO

### Con las mejoras implementadas:

✅ **Modal con instrucciones** claras  
✅ **Advertencia de tiempo** (60s)  
✅ **Pasos numerados** para el cliente  
✅ **Callback mejorado** con mensajes claros  
✅ **Botón "reintentar"** si falla  
✅ **sendStatus habilitado** para mantener comunicación  

### El sistema ahora:

1. Informa al cliente qué debe hacer
2. Le da tiempo específico (60s)
3. Muestra pasos claros
4. Permite reintentar fácilmente
5. Explica el error si falla

---

## ⚡ PRUEBA AHORA

```bash
1. Reiniciar Django
2. Abrir dashboard
3. Crear ticket
4. Click "POS Transbank"
5. Leer instrucciones al cliente
6. Click "Cliente listo - Continuar"
7. Cliente debe:
   - Pasar tarjeta RÁPIDO
   - Seleccionar Débito/Crédito INMEDIATAMENTE
   - Confirmar
```

**Si sigue fallando** → Necesitas actualizar el agente Transbank

---

**Fecha**: 4 de Noviembre, 2025  
**Issue**: Timeout ACK 2000ms  
**Solución temporal**: ✅ Implementada  
**Solución definitiva**: Actualizar agente Transbank

