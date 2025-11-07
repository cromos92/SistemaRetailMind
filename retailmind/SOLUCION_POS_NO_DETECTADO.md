# ⚠️ Solución: POS No Detectado

## Problema Reportado

```
❌ Error: El agente no detectó ningún POS conectado. 
Verifique que el terminal esté encendido y conectado por USB/Serial.

Estado: {"connected":false,"activePort":null}
```

## ✅ Soluciones Paso a Paso

### Solución 1: Verificar Hardware

#### 1️⃣ **Verificar que el POS esté encendido**
- El terminal POS debe estar completamente encendido
- Debe mostrar "Listo" o "Ready" en la pantalla
- Las luces indicadoras deben estar encendidas

#### 2️⃣ **Verificar la conexión física**
- Cable USB o Serial bien conectado
- Probar con otro puerto USB del computador
- Probar con otro cable si es posible

#### 3️⃣ **Verificar en Windows**
```
1. Abrir "Administrador de Dispositivos"
   (Tecla Windows + X → Administrador de dispositivos)

2. Expandir "Puertos (COM y LPT)"

3. Buscar el dispositivo del POS:
   - "USB Serial Port (COM3)"
   - "Prolific USB-to-Serial"
   - "FTDI USB Serial Port"
   
4. Anotar el número del puerto (ej: COM3, COM4)
```

---

### Solución 2: Usar Conexión Manual

Si la autodetección no funciona, conecta manualmente:

#### Paso 1: Listar Puertos
1. Haz clic en el botón **🔄** junto al selector de puerto
2. Verás los puertos disponibles: `COM1, COM2, COM3...`

#### Paso 2: Seleccionar Puerto
1. En el selector **"Puerto Manual"**, selecciona el puerto del POS
2. Generalmente es `COM3`, `COM4` o `COM5`

#### Paso 3: Conectar
1. Haz clic en **"Conectar Terminal"**
2. El sistema intentará conectar a ese puerto específico

```javascript
// Ejemplo en consola:
const pos = new TransbankPOSIntegration();
await pos.initialize();
await pos.openPort('COM3', 115200); // Cambiar COM3 por tu puerto
await pos.poll(); // Verificar que responde
```

---

### Solución 3: Verificar el Agente Desktop

#### ¿El agente está corriendo?
1. Buscar en la barra de tareas el icono de Transbank
2. Debe estar activo y en verde
3. Si no está, ejecutar el agente:
   - `C:\Program Files\Transbank\POS Agent\TransbankPOSAgent.exe`

#### Verificar URL del agente
1. Abrir navegador en: `https://localhost:8090`
2. Debe mostrar página del agente
3. Si no abre, reinstalar el agente de Transbank

---

### Solución 4: Verificar Drivers

#### Windows necesita drivers del POS:

**Para POS Verifone:**
```
Driver: Verifone USB Driver
Descarga: Desde portal de Transbank
```

**Para POS Ingenico:**
```
Driver: Ingenico USB Driver
Descarga: Desde portal de Transbank
```

**Verificar instalación:**
1. Administrador de Dispositivos
2. No debe haber signos de exclamación amarillos
3. El dispositivo debe aparecer en "Puertos (COM y LPT)"

---

### Solución 5: Configurar Permisos

#### Windows 10/11:
```
1. Click derecho en el agente Transbank
2. "Ejecutar como administrador"
3. Intentar nuevamente
```

#### Firewall:
```
1. Panel de Control → Firewall de Windows
2. Configuración avanzada
3. Reglas de entrada
4. Permitir conexión en puerto 8090
```

---

### Solución 6: Probar Manualmente con el SDK

Abre la consola del navegador (F12) y ejecuta:

```javascript
// 1. Verificar que el SDK está cargado
console.log(typeof Transbank); // Debe mostrar "object"

// 2. Crear instancia
const pos = Transbank.POS;

// 3. Conectar al agente
await pos.connect('https://localhost:8090');

// 4. Listar puertos
const ports = await pos.getPorts();
console.log('Puertos:', ports);
// Resultado esperado: ["COM1", "COM3", "COM4"]

// 5. Probar cada puerto manualmente
await pos.openPort('COM3', 115200);
const status = await pos.poll();
console.log('Poll result:', status);
```

Si funciona en consola pero no en la interfaz, hay un problema en el código JavaScript de la interfaz.

---

### Solución 7: Verificar Baudrate

El POS puede estar configurado con otro baudrate:

```javascript
// Probar con diferentes baudrates
const baudrates = [9600, 19200, 38400, 57600, 115200];

for (const baud of baudrates) {
    try {
        await pos.openPort('COM3', baud);
        await pos.poll();
        console.log(`✅ Funciona con baudrate: ${baud}`);
        break;
    } catch (error) {
        console.log(`❌ No funciona con ${baud}`);
        await pos.closePort();
    }
}
```

---

### Solución 8: Revisar Configuración del POS

#### El POS debe estar en modo correcto:

```javascript
// Cambiar a modo normal
await pos.setNormalMode();

// Cargar llaves (si es primera vez)
await pos.loadKeys();
```

---

## 🔧 Script de Diagnóstico Completo

Ejecuta este script en la consola del navegador para diagnosticar:

```javascript
async function diagnosticarPOS() {
    console.log('🔍 === INICIANDO DIAGNÓSTICO ===');
    
    // 1. Verificar SDK
    console.log('1️⃣ Verificando SDK...');
    if (typeof Transbank === 'undefined') {
        console.error('❌ SDK no cargado');
        return;
    }
    console.log('✅ SDK disponible');
    
    // 2. Conectar al agente
    console.log('2️⃣ Conectando al agente...');
    try {
        await Transbank.POS.connect('https://localhost:8090');
        console.log('✅ Agente conectado');
    } catch (error) {
        console.error('❌ Error conectando al agente:', error);
        console.log('💡 Solución: Verifique que el agente esté ejecutándose');
        return;
    }
    
    // 3. Listar puertos
    console.log('3️⃣ Listando puertos...');
    try {
        const ports = await Transbank.POS.getPorts();
        console.log('✅ Puertos disponibles:', ports);
        
        if (!ports || ports.length === 0) {
            console.error('❌ No hay puertos disponibles');
            console.log('💡 Solución: Verifique la conexión USB del POS');
            return;
        }
        
        // 4. Probar cada puerto
        console.log('4️⃣ Probando cada puerto...');
        for (const port of ports) {
            console.log(`\n🔍 Probando ${port}...`);
            
            try {
                await Transbank.POS.openPort(port, 115200);
                console.log(`  ↳ Puerto ${port} abierto`);
                
                const pollResult = await Transbank.POS.poll();
                console.log(`  ↳ Poll exitoso:`, pollResult);
                
                console.log(`✅ ¡POS ENCONTRADO EN ${port}!`);
                console.log('═══════════════════════════════');
                console.log(`Use este puerto: ${port}`);
                console.log('═══════════════════════════════');
                
                return port;
                
            } catch (error) {
                console.log(`  ↳ ${port} no responde:`, error.message);
                try {
                    await Transbank.POS.closePort();
                } catch {}
            }
        }
        
        console.error('❌ No se detectó POS en ningún puerto');
        console.log('💡 Soluciones:');
        console.log('  1. Verifique que el POS esté encendido');
        console.log('  2. Reinstale drivers del POS');
        console.log('  3. Pruebe con otro cable USB');
        console.log('  4. Contacte soporte de Transbank');
        
    } catch (error) {
        console.error('❌ Error en diagnóstico:', error);
    }
}

// Ejecutar diagnóstico
diagnosticarPOS();
```

---

## 📋 Checklist de Verificación

Antes de contactar soporte, verifique:

- [ ] POS está encendido y muestra "Listo"
- [ ] Cable USB/Serial bien conectado
- [ ] Aparece en Administrador de Dispositivos
- [ ] Agente Transbank ejecutándose (icono en barra de tareas)
- [ ] `https://localhost:8090` abre en el navegador
- [ ] Drivers del POS instalados
- [ ] No hay otra aplicación usando el puerto
- [ ] Ejecutar navegador/aplicación como administrador

---

## 🎯 Solución Rápida Recomendada

### Opción A: Conexión Manual (MÁS CONFIABLE)

1. **Identificar puerto en Windows:**
   - Abrir Administrador de Dispositivos
   - Buscar en "Puertos (COM y LPT)"
   - Anotar el puerto (ej: COM3)

2. **En la interfaz:**
   - Haz clic en 🔄 para actualizar puertos
   - Selecciona el puerto en el dropdown
   - Haz clic en "Conectar Terminal"

3. **Verificar:**
   - Haz clic en "Verificar Conexión" (poll)
   - Debe responder con estado OK

### Opción B: Reiniciar Todo

```
1. Cerrar navegador
2. Cerrar agente Transbank (desde barra de tareas)
3. Desconectar y reconectar el POS
4. Iniciar agente Transbank como administrador
5. Abrir navegador
6. Ir a la página POS
7. Intentar autodetección nuevamente
```

---

## 📞 Soporte

Si ninguna solución funciona:

**Transbank:**
- Mesa de ayuda: (600) 638 9000
- Email: soporte@transbank.cl
- Portal: https://www.transbankdevelopers.cl

**Verificar:**
- Modelo exacto del POS (Verifone VX520, Ingenico, etc.)
- Versión del agente desktop instalado
- Sistema operativo (Windows 10, 11, etc.)

---

**Última actualización:** Noviembre 7, 2025

