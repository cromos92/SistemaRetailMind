# Guía: Solucionar Conexión con Agente Transbank POS

## 🚨 Problema Común
**Situación**: Tenías otro sistema usando Transbank, lo cerraste, pero este sistema no logra conectarse al POS.

**Causa**: El Agente Transbank POS puede estar:
- En estado bloqueado por la sesión anterior
- Con el puerto 8090 aún ocupado
- Necesitando reinicio completo
- Con sesión activa del sistema anterior

---

## ✅ Solución Paso a Paso

### Paso 1: Cerrar Completamente el Agente Transbank

#### En Windows:

1. **Abrir Administrador de Tareas** (Ctrl + Shift + Esc)

2. **Buscar estos procesos** y finalizarlos:
   ```
   - Transbank POS Agent
   - tbk_agent.exe
   - transbank-pos-agent.exe
   - node.exe (si está relacionado con Transbank)
   ```

3. **Clic derecho → Finalizar Tarea** en cada uno

#### En Mac/Linux:

```bash
# Verificar procesos en puerto 8090
lsof -i :8090

# Matar el proceso (reemplazar PID con el número que aparece)
kill -9 PID

# O buscar por nombre
pkill -f "transbank"
```

---

### Paso 2: Verificar que el Puerto 8090 esté Libre

#### En Windows PowerShell:

```powershell
# Ver qué está usando el puerto 8090
netstat -ano | findstr :8090

# Si aparece algo, anotar el PID y matarlo:
taskkill /PID numero_pid /F
```

#### En Mac/Linux:

```bash
# Ver qué proceso usa el puerto 8090
lsof -i :8090

# Resultado esperado si está libre: (ninguna salida)
```

---

### Paso 3: Reiniciar el Agente Transbank

1. **Iniciar el Agente Transbank POS**
   - Buscar en menú inicio: "Transbank POS Agent"
   - O ejecutar desde: `C:\Program Files\Transbank\POS Agent\` (Windows)

2. **Esperar a que aparezca**:
   ```
   ✅ "Agente iniciado en puerto 8090"
   ✅ Ícono en la bandeja del sistema
   ```

3. **NO abrir ningún otro sistema** todavía

---

### Paso 4: Probar la Conexión desde el Sistema

1. **Abrir este sistema**: `http://127.0.0.1:8000/app/pos/transbank/`

2. **Abrir la consola del navegador** (F12)

3. **Click en "Diagnosticar SDK"**

4. **Revisar el log**:

✅ **Si funciona, verás**:
```
✅ Conectado al agente POS Transbank
✅ getPorts() funciona: X puerto(s) encontrado(s)
Puertos: [COM3, COM4] (o similar)
```

❌ **Si falla, verás**:
```
❌ Error al conectar: ...
⚠️ Agente POS: NO DETECTADO
```

---

### Paso 5: Ejecutar "Probar getPorts()"

1. **Click en el botón "Probar getPorts()"**

2. **Observar el log detallado**:

✅ **Conexión exitosa**:
```
🧪 Probando getPorts() directamente...
🔗 Asegurando conexión al agente...
✅ Conectado al agente POS Transbank
🔍 Ejecutando getPorts()...
⏱️ getPorts() completado en 1.23s
📋 Respuesta cruda: ["COM3"]
✅ 1 puerto(s) detectado(s): [COM3]
```

❌ **Error de conexión**:
```
❌ Error probando getPorts(): Connection refused
```

---

## 🔧 Problemas Específicos y Soluciones

### Problema 1: "Connection refused" o "net::ERR_CONNECTION_REFUSED"

**Causa**: El agente no está corriendo

**Solución**:
```bash
1. Verificar que el Agente Transbank esté iniciado
2. Buscar ícono en bandeja del sistema (tray)
3. Si no está, iniciarlo manualmente
4. Esperar 5-10 segundos
5. Recargar la página del sistema
```

---

### Problema 2: "ERR_SSL_PROTOCOL_ERROR" persistente

**Causa**: El agente está corriendo pero no acepta conexiones SSL

**Solución**:
```bash
1. Cerrar completamente el agente (Paso 1)
2. Limpiar caché del navegador (Ctrl + Shift + Delete)
3. Reiniciar el agente
4. Reiniciar el navegador
5. Volver a intentar
```

---

### Problema 3: "Timeout: getPorts() tardó más de 15 segundos"

**Causa**: El agente está sobrecargado o bloqueado

**Solución**:
```bash
1. Desconectar el terminal POS físico
2. Cerrar el agente
3. Esperar 30 segundos
4. Reconectar el terminal POS
5. Iniciar el agente
6. Esperar a que detecte el terminal
7. Probar desde el sistema
```

---

### Problema 4: "getPorts() devolvió respuesta vacía"

**Causa**: El terminal POS no está conectado o no es detectado

**Solución**:
```bash
1. Verificar cable USB/Serial del terminal
2. En Windows: Device Manager → Ports (COM & LPT)
3. Debe aparecer el terminal (ej: "Verifone POS - COM3")
4. Si no aparece:
   - Reconectar cable
   - Reinstalar drivers del terminal
   - Probar otro puerto USB
5. Reiniciar el agente después de reconectar
```

---

## 🎯 Script de Comandos para Windows

Crea un archivo `reiniciar_agente.bat` con esto:

```batch
@echo off
echo ========================================
echo Reiniciando Agente Transbank POS
echo ========================================

echo.
echo [1/4] Cerrando procesos del agente...
taskkill /F /IM tbk_agent.exe 2>nul
taskkill /F /IM transbank-pos-agent.exe 2>nul
timeout /t 2 >nul

echo.
echo [2/4] Verificando puerto 8090...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8090') do (
    echo Liberando puerto 8090 (PID: %%a)
    taskkill /F /PID %%a 2>nul
)
timeout /t 2 >nul

echo.
echo [3/4] Iniciando agente Transbank...
start "" "C:\Program Files\Transbank\POS Agent\tbk_agent.exe"
timeout /t 5 >nul

echo.
echo [4/4] Verificando estado...
netstat -ano | findstr :8090
if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo   EXITO: Agente iniciado en puerto 8090
    echo ========================================
) else (
    echo.
    echo ========================================
    echo   ERROR: Agente no inicio correctamente
    echo   Verifique la instalacion
    echo ========================================
)

echo.
pause
```

**Uso**: Doble click en el archivo `.bat`

---

## 🔍 Verificación Manual del Agente

### Probar con curl (Windows PowerShell):

```powershell
# Probar conexión básica
Invoke-WebRequest -Uri "https://localhost:8090" -SkipCertificateCheck
```

### Probar con navegador:

1. Abrir: `https://localhost:8090`
2. Aceptar certificado auto-firmado
3. Debe aparecer alguna respuesta del agente (aunque sea error 404)
4. Si aparece "No se puede acceder", el agente NO está corriendo

---

## 📋 Checklist de Verificación

Antes de intentar conectar desde el sistema, verificar:

- [ ] ✅ Otro sistema con Transbank completamente cerrado
- [ ] ✅ Procesos del agente finalizados (Administrador de Tareas)
- [ ] ✅ Puerto 8090 libre (netstat)
- [ ] ✅ Agente Transbank iniciado
- [ ] ✅ Ícono del agente visible en bandeja del sistema
- [ ] ✅ Terminal POS conectado por USB/Serial
- [ ] ✅ Terminal POS encendido
- [ ] ✅ Drivers del terminal instalados
- [ ] ✅ COM port visible en Device Manager (Windows)
- [ ] ✅ Navegador con caché limpio
- [ ] ✅ Sistema iniciado en: http://127.0.0.1:8000/app/pos/transbank/

---

## 🎓 Flujo Correcto de Trabajo

### Secuencia recomendada:

```
1. Cerrar TODOS los sistemas que usen Transbank
   ↓
2. Cerrar completamente el Agente Transbank
   ↓
3. Verificar que puerto 8090 esté libre
   ↓
4. Conectar terminal POS físico
   ↓
5. Iniciar el Agente Transbank
   ↓
6. Esperar 10 segundos
   ↓
7. Abrir UNO de los sistemas
   ↓
8. Probar conexión con "Diagnosticar SDK"
   ↓
9. Si funciona → "Auto-Detectar Terminales"
```

---

## 🚨 Errores Comunes a Evitar

❌ **NO hacer**:
- Abrir múltiples sistemas simultáneamente
- Iniciar el sistema antes del agente
- Conectar el terminal después de iniciar el agente
- Usar `localhost` y `127.0.0.1` al mismo tiempo
- Tener el agente corriendo sin terminal conectado

✅ **SÍ hacer**:
- Un solo sistema a la vez
- Agente primero, sistema después
- Terminal conectado antes del agente
- Usar siempre la misma URL (127.0.0.1:8000)
- Verificar conexión antes de operar

---

## 🛠️ Comandos Útiles para Debug

### Ver todos los puertos abiertos:
```powershell
# Windows
netstat -ano | findstr LISTENING

# Mac/Linux
netstat -an | grep LISTEN
```

### Ver procesos de Transbank:
```powershell
# Windows
tasklist | findstr transbank

# Mac/Linux  
ps aux | grep transbank
```

### Logs del agente:
```
# Ubicación típica (Windows):
C:\Users\TU_USUARIO\AppData\Local\Transbank\POS Agent\logs\

# Buscar archivo más reciente:
*.log
```

---

## 📞 Si Nada Funciona

### Reinstalar el Agente Transbank:

1. **Desinstalar**:
   ```
   Panel de Control → Programas → Desinstalar
   Buscar: "Transbank POS"
   ```

2. **Limpiar residuos**:
   ```
   Eliminar carpeta: C:\Program Files\Transbank\
   Eliminar carpeta: C:\Users\TU_USUARIO\AppData\Local\Transbank\
   ```

3. **Reiniciar PC**

4. **Descargar versión más reciente**:
   ```
   https://www.transbankdevelopers.cl/producto/posintegrado
   ```

5. **Instalar y configurar**

6. **Probar nuevamente**

---

## 🎯 Alternativa: Usar Modo Demo

Si necesitas trabajar AHORA y el hardware da problemas:

```
1. Click en botón "Modo Demo"
2. Sistema completamente funcional sin hardware
3. Simula todas las operaciones
4. Perfecto para:
   - Capacitación
   - Desarrollo
   - Testing
   - Demostración a clientes
```

---

**Última actualización**: 4 de Noviembre, 2025  
**Versión**: 1.0  
**Autor**: Soporte Técnico

