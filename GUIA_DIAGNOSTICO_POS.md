# 🔧 Guía de Uso - Script de Diagnóstico POS

## 🎯 Propósito

El script `diagnostico_pos.py` identifica **exactamente** cuál es el problema de conexión con tu terminal POS Transbank.

---

## 🚀 Cómo Ejecutar

### 1. Asegúrate de que el POS esté conectado por USB

### 2. Ejecuta el script:

```bash
# Activar entorno virtual
venv\Scripts\activate

# Ejecutar diagnóstico
python diagnostico_pos.py
```

---

## 📊 Tests que Realiza

### ✅ **TEST 1: Listar Puertos Disponibles**
- Detecta todos los puertos seriales del sistema
- Muestra nombre y descripción de cada puerto
- **Si falla:** No hay puertos o faltan permisos

### ✅ **TEST 2: Información del Puerto**
- Muestra detalles técnicos del puerto
- VID, PID, fabricante, etc.
- Útil para verificar drivers

### ✅ **TEST 3: Abrir Puerto**
- Intenta abrir el puerto con baudrate 115200
- Verifica que el puerto sea accesible
- **Si falla:** Puerto en uso o permisos incorrectos

### ✅ **TEST 4: POLL - Verificar Comunicación**
- **Este es el test MÁS IMPORTANTE**
- Verifica que el POS responda
- **Si falla:** POS apagado o en modo incorrecto

### ✅ **TEST 5: Cargar Llaves**
- Prueba el comando `load_keys()`
- **IMPORTANTE:** El POS pedirá confirmación
- Debes presionar SÍ en el POS físico

### ✅ **TEST 6: Probar Todos los Baudrates**
- Si 115200 falla, prueba otros baudrates
- Prueba: 115200, 9600, 19200, 38400, 57600, etc.
- Encuentra el baudrate correcto automáticamente

### ✅ **TEST 7: Conexión Directa pyserial**
- Último recurso: conexión de bajo nivel
- Útil para verificar problemas de drivers

---

## 📖 Interpretación de Resultados

### ✅ Resultado Exitoso:

```
✅ Se encontraron 1 puerto(s):
   1. COM9 - VX 520 GPRS Terminal

✅ Puerto abierto exitosamente

✅ POS responde correctamente - CONECTADO

🎉🎉🎉 POS FUNCIONAL en COM9 @ 115200 🎉🎉🎉
```

**Solución:** Tu POS está funcionando perfectamente. Usa `COM9` con baudrate `115200`.

---

### ❌ Error: No se encontraron puertos

```
❌ No se encontraron puertos
```

**Causas posibles:**
1. POS no está conectado físicamente
2. Cable USB defectuoso
3. POS apagado
4. Falta driver USB-Serial

**Soluciones:**
- Verifica conexión física
- Prueba otro cable USB
- Enciende el POS
- Instala drivers: `pip install pyserial`
- En Windows: Administrador de Dispositivos → Puertos COM

---

### ❌ Error: Puerto se abre pero POS no responde (POLL falla)

```
✅ Puerto abierto exitosamente
❌ POS no responde

💡 El puerto se abrió pero el POS no responde
```

**Causa MÁS COMÚN:**
- **El POS NO está en modo "POS INTEGRADO"**

**Solución:**
1. En el POS físico:
   - Presiona MENÚ/F4
   - Busca "Modo de Operación" o "Operation Mode"
   - Cambia a "POS INTEGRADO" o "INTEGRATED"
   - Reinicia el POS
2. Verifica que el baudrate sea 115200
3. Prueba con otro cable USB

---

### ⚠️ Carga de Llaves: Usuario debe confirmar

```
⚠️ ATENCIÓN: El POS puede pedir confirmación en pantalla
   Si aparece '¿Desea cargar llaves criptográficas?'
   PRESIONA SÍ/ACEPTAR en el POS físico
```

**Esto es NORMAL:**
- El POS pide confirmación por seguridad
- Ve al POS físico
- Presiona SÍ/ACEPTAR
- Espera 30-60 segundos
- Verás "APROBADO" en el POS

---

## 🔍 Problemas Comunes y Soluciones

### Problema 1: "Access Denied" o "Permission Denied"

**En Linux:**
```bash
# Agregar usuario al grupo dialout
sudo usermod -a -G dialout $USER

# Cerrar sesión y volver a entrar
# O reiniciar
```

**En Windows:**
- Ejecutar como Administrador
- Verificar que el puerto no esté en uso por otro programa

---

### Problema 2: "Puerto en uso"

**Solución:**
```bash
# Cerrar cualquier programa que use el puerto
# Ej: Otro terminal, Putty, Arduino IDE, etc.

# En Windows ver procesos:
# Administrador de Tareas → Detalles
```

---

### Problema 3: Baudrate Incorrecto

**Solución:**
- El script prueba automáticamente todos los baudrates
- Si encuentra uno que funciona, te lo dirá:
  ```
  🎉 ¡ÉXITO! Baudrate correcto: 9600
  ```
- Usa ese baudrate en tu configuración

---

### Problema 4: POS Responde pero Load Keys Falla

**Causas:**
1. Usuario canceló en el POS
2. POS sin conexión a internet/GPRS
3. Timeout (tardó más de 90 segundos)

**Soluciones:**
- Verifica conexión del POS a internet/GPRS
- Confirma en el POS cuando pregunte
- Aumenta el timeout si es necesario

---

## 📝 Ejemplo de Salida Completa

```
╔══════════════════════════════════════════════════════════════╗
║         DIAGNÓSTICO TRANSBANK POS - PYTHON SDK               ║
╚══════════════════════════════════════════════════════════════╝

============================================================
  TEST 1: Listar Puertos Disponibles
============================================================
✅ Se encontraron 3 puerto(s):
   1. COM4 - Serie estándar sobre el vínculo Bluetooth (COM4)
   2. COM3 - Serie estándar sobre el vínculo Bluetooth (COM3)
   3. COM9 - VX 520 GPRS Terminal (COM9)

============================================================
  TEST 2: Información del Puerto COM9
============================================================
✅ Información detallada:
   Device: COM9
   Description: VX 520 GPRS Terminal
   Manufacturer: Verifone
   Product: VX520
   ...

============================================================
  TEST 3: Abrir Puerto COM9 @ 115200
============================================================
Intentando abrir puerto...
✅ Puerto abierto exitosamente

============================================================
  TEST 4: POLL - Verificar Comunicación
============================================================
Ejecutando POLL...
✅ POS responde correctamente - CONECTADO

¿Deseas probar la carga de llaves? (s/N): s

============================================================
  TEST 5: Cargar Llaves
============================================================
⚠️  ATENCIÓN: El POS puede pedir confirmación en pantalla
Ejecutando load_keys()...
[El POS muestra: "¿Desea cargar llaves criptográficas?"]
[Presionas SÍ en el POS]
[Esperas 30-60 segundos]
✅ Comando load_keys ejecutado
   ✅ Llaves cargadas exitosamente
   Commerce Code: 597020000541
   Terminal ID: ABC123

🎉🎉🎉🎉🎉 POS FUNCIONAL en COM9 @ 115200 🎉🎉🎉
```

---

## 💡 Tips Útiles

### Antes de Ejecutar:
1. ✅ Conecta el POS por USB
2. ✅ Enciende el POS
3. ✅ Verifica que esté en modo "POS INTEGRADO"
4. ✅ Cierra otros programas que usen puertos seriales

### Durante la Ejecución:
1. ✅ Lee los mensajes con atención
2. ✅ Si el POS pide confirmación, acéptala
3. ✅ No desconectes el POS durante los tests
4. ✅ Ten paciencia con load_keys (tarda 60+ segundos)

### Después de Ejecutar:
1. ✅ Anota el puerto y baudrate que funcionó
2. ✅ Usa esos valores en tu configuración
3. ✅ Si falla, lee las "💡 SOLUCIONES" que muestra el script

---

## 📞 Información para Soporte

Si necesitas ayuda, ejecuta el script y envía:

1. **Salida completa del script**
2. **Modelo del POS** (ej: Verifone VX520)
3. **Sistema operativo** (Windows/Linux/Mac + versión)
4. **Puerto detectado** (ej: COM9, /dev/ttyUSB0)
5. **En qué test falla**

---

## 🎯 Próximos Pasos

### Si el diagnóstico fue exitoso:

```bash
# Usar en la API con el puerto y baudrate detectados:

# Auto-conectar (recomendado)
curl -X POST http://localhost:8000/app/pos/transbank/autoconectar/

# O manual con puerto específico:
curl -X POST http://localhost:8000/app/pos/transbank/conectar/ \
  -H "Content-Type: application/json" \
  -d '{"puerto": "COM9", "baud_rate": 115200}'
```

### Si el diagnóstico falló:

1. Revisa las "💡 SOLUCIONES" que mostró el script
2. Verifica modo "POS INTEGRADO"
3. Prueba otro cable USB
4. Contacta soporte de Transbank

---

**Ejecuta el diagnóstico ahora:**

```bash
python diagnostico_pos.py
```

**¡Y pega la salida completa para ayudarte mejor!** 🚀

