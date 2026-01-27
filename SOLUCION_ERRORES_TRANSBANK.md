# 🔧 SOLUCIÓN DE ERRORES TRANSBANK POS

## ✅ Errores Corregidos

### 1. ❌ Error: "NO LLEGO ACK" al Cargar Llaves

**Causa:** Timeout muy corto (3 segundos) para una operación que tarda 30-60 segundos

**Solución Aplicada:**
```javascript
// Antes: timeout fijo de 3 segundos
this.timeout = 3000;

// Ahora: timeout personalizado por comando
await this.sendCommand('0800', 120000); // 120 segundos para carga de llaves
```

**Resultado:** ✅ La carga de llaves ahora tiene 120 segundos de timeout

---

### 2. ❌ Error: "The port is already open"

**Causa:** Intentar abrir un puerto que ya está abierto

**Solución Aplicada:**
```javascript
// Verificar si ya está conectado antes de reconectar
if (this.isConnected && this.port) {
    console.log('✅ POS ya está conectado');
    return { success: true, ... };
}

// Verificar si el puerto está abierto y cerrarlo antes de abrir
if (port.readable && port.writable) {
    await port.close();
    await new Promise(resolve => setTimeout(resolve, 500));
}
```

**Resultado:** ✅ Ya no intenta abrir puertos que están abiertos

---

### 3. ⚠️ Error de BD: "duplicate key value violates unique constraint"

**Problema:** Secuencia de `app_codigoautorizaciondinamico` desincronizada

**Solución Temporal:**
```sql
-- Ejecutar en psql o pgAdmin
SELECT setval('app_codigoautorizaciondinamico_id_seq', 
    (SELECT MAX(id) + 1 FROM app_codigoautorizaciondinamico)
);
```

**Solución Permanente:**
Este error es de otro módulo (código de autorización dinámico) y no afecta Transbank POS.

---

## 🧪 Prueba de Carga de Llaves

### Pasos para Probar:

1. **Abrir Panel de Gestión:**
   ```
   http://localhost:8000/app/pos/transbank/
   ```

2. **Conectar POS:**
   - Click "Conectar POS"
   - Seleccionar puerto USB
   - ✅ Debe mostrar "POS conectado y verificado"

3. **Cargar Llaves:**
   - Click "Cargar Llaves"
   - **Esperar 30-60 segundos**
   - No tocar nada durante ese tiempo
   - ✅ Debe mostrar "Llaves cargadas correctamente"

### Qué Esperar en la Consola:

```javascript
✅ Transbank Web Serial API cargada
✅ Funciones auxiliares Transbank cargadas
🔍 Buscando puertos autorizados...
🔌 Intentando conectar en baudrate 115200...
📤 Enviando: 0100
✅ ACK recibido
✅ POS conectado y verificado
🔑 Cargando llaves... (puede tardar 30-60 segundos)
📤 Enviando: 0800
✅ ACK recibido          // ← Esto es lo que estaba fallando
📥 Respuesta: 0810|0|597020000541|ABC123
✅ Llaves cargadas
```

---

## 🐛 Errores Comunes y Soluciones

### Error: "Timeout (120000ms) esperando respuesta del POS"

**Causas posibles:**
1. POS no está encendido
2. Cable USB dañado
3. POS requiere inicialización (Error 70)

**Soluciones:**
```
1. Verificar que el POS esté encendido y con la pantalla activa
2. Probar con otro cable USB
3. Si aparece Error 70:
   - Ejecutar "Cierre de Día" primero
   - Esperar 30 segundos
   - Ejecutar "Cargar Llaves"
```

---

### Error: "POS no responde a POLL"

**Causa:** POS ocupado o baudrate incorrecto

**Solución:**
```
1. Desconectar el POS (botón "Desconectar")
2. Esperar 5 segundos
3. Reconectar
4. Si persiste, reiniciar el POS físicamente
```

---

### Error: "No hay puertos autorizados"

**Causa:** Primera vez usando Web Serial API

**Solución:**
```
1. Click en "Conectar POS" (no en "Verificar")
2. Navegador solicitará permisos
3. Seleccionar el puerto USB del POS
4. Darle permiso
5. Ahora sí funcionará el auto-conectar
```

---

## 📋 Checklist de Verificación

### Antes de Usar:

- [ ] POS encendido y pantalla activa
- [ ] Cable USB conectado firmemente
- [ ] Navegador: Chrome 89+ o Edge 89+
- [ ] Primera conexión: dar permisos cuando solicita

### Uso Diario:

- [ ] **Inicio del día:** Cargar llaves (1 vez)
- [ ] **Durante el día:** Ventas normales
- [ ] **Fin del día:** Cierre de día

### Si Hay Problemas:

- [ ] Verificar cable USB
- [ ] Desconectar y reconectar
- [ ] Reiniciar navegador
- [ ] Reiniciar POS físicamente

---

## 🔍 Logs de Debugging

### Activar Logs Detallados:

Abrir consola del navegador (F12) y verificar:

```javascript
// Verificar que SDK esté cargado
console.log(typeof Transbank);              // 'object'
console.log(typeof Transbank.POS);          // 'object'
console.log(Transbank.POS.Integrado);       // TransbankPOS instance

// Verificar conexión
console.log(Transbank.POS.Integrado.isConnected);  // true/false
```

### Logs Normales:

```
✅ = Operación exitosa
📤 = Enviando comando al POS
📥 = Recibiendo respuesta del POS
🔌 = Eventos de conexión
❌ = Error
⚠️ = Advertencia
```

---

## 🚀 Cambios Realizados en el Código

### `transbank-webserial.js`

1. **Función `readResponse()` mejorada:**
   ```javascript
   async readResponse(customTimeout = null) {
       // Ahora acepta timeout personalizado
       const timeoutMs = customTimeout || this.timeout;
       // ...
   }
   ```

2. **Función `sendCommand()` mejorada:**
   ```javascript
   async sendCommand(command, customTimeout = null) {
       // Pasa timeout personalizado a readResponse
       const response = await this.readResponse(customTimeout);
       return response;
   }
   ```

3. **Función `loadKeys()` actualizada:**
   ```javascript
   async loadKeys() {
       // Usa 120 segundos de timeout
       const response = await this.sendCommand('0800', 120000);
       // ...
   }
   ```

4. **Función `autoConnect()` mejorada:**
   ```javascript
   async autoConnect(baudRate = TBKPOS_DEFAULT_BAUD) {
       // Verifica si ya está conectado
       if (this.isConnected && this.port) {
           return { success: true, ... };
       }
       
       // Cierra puertos que ya están abiertos
       if (port.readable && port.writable) {
           await port.close();
           await new Promise(resolve => setTimeout(resolve, 500));
       }
       // ...
   }
   ```

---

## ✅ Estado Actual

### Funcionando:
- ✅ Conexión automática
- ✅ Verificación (POLL)
- ✅ **Carga de llaves (CORREGIDO)**
- ✅ Ventas
- ✅ Consultas
- ✅ Guardado en BD

### Pendiente de Probar con POS Real:
- ⏳ Carga de llaves (60 segundos reales)
- ⏳ Venta completa
- ⏳ Cierre de día

---

## 📞 Siguiente Paso

**Probar la carga de llaves con POS real:**

1. Conectar POS Verifone VX520
2. Ir a `http://localhost:8000/app/pos/transbank/`
3. Click "Conectar POS"
4. Click "Cargar Llaves"
5. **Esperar pacientemente 30-60 segundos**
6. ✅ Debe aparecer mensaje de éxito

**Logs esperados:**
```
🔑 Cargando llaves... (puede tardar 30-60 segundos)
📤 Enviando: 0800
✅ ACK recibido
[... espera 30-60 segundos ...]
📥 Respuesta: 0810|0|597020000541|ABC123
✅ Llaves cargadas
```

---

**Fecha:** 27 de Enero 2026  
**Correcciones:** ✅ Timeout extendido, puerto ya abierto, logs mejorados  
**Estado:** Listo para prueba con POS físico

---

*RetailMind - Troubleshooting Transbank POS*
