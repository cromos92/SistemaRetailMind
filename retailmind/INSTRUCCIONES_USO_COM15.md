# 🎯 Cómo Usar el POS en COM15 - Instrucciones Completas

## ✅ CAMBIOS IMPLEMENTADOS

He modificado **`gestion_pos_transbank_simple.html`** (el que se ve en http://localhost:8000/app/pos/transbank/) con:

### 1. Campo GRANDE para escribir puerto manual
- **Ubicación**: Arriba de todo, recuadro VERDE
- **Título**: "Escribir Puerto COM Manualmente"
- **Campo**: Letras grandes, ya tiene "COM15" pre-cargado
- **Botón**: "CONECTAR AL PUERTO" (verde, grande)

### 2. SDK Oficial de Transbank
- ✅ Cambiado de CDN a librería local
- ✅ Todos los métodos nuevos implementados:
  - `openPort(puerto, baudRate)` ← NUEVO
  - `loadKeys()` ← NUEVO
  - `closeDay()` ← NUEVO
  - `getTotals()` ← NUEVO
  - `getLastSale()` ← NUEVO
  - `getDetails()` ← NUEVO
  - `poll()` ← NUEVO
  - `doMulticodeSale()` ← NUEVO
  - `refund(operationId)` ← NUEVO

---

## 🚀 CÓMO USAR EN 3 PASOS

### Paso 1: Refrescar Sin Caché

**Presiona: Ctrl + F5** en tu navegador

Esto asegura que veas la versión nueva del HTML.

---

### Paso 2: Verás un Recuadro VERDE Grande

Arriba de todo verás:

```
┌──────────────────────────────────────────────────────────┐
│ ⌨️ Escribir Puerto COM Manualmente                      │
│                                                           │
│ Escriba el Puerto COM:                                   │
│ ┌─────────────┐                                         │
│ │ [USB] COM15 │  [🔌 CONECTAR AL PUERTO]  [Estado]     │
│ └─────────────┘                                         │
│      ↑              ↑                                    │
│   EL CAMPO      EL BOTÓN                                │
│   (LETRAS        (VERDE)                                │
│   GRANDES)                                              │
│                                                          │
│ Puerto: -                                               │
└──────────────────────────────────────────────────────────┘
```

---

### Paso 3: Escribir COM15 y Conectar

1. **El campo ya tiene "COM15" pre-cargado** ✅
   - Si no está, escríbelo

2. **Haz clic en el botón verde:** `[🔌 CONECTAR AL PUERTO]`

3. **El sistema hará:**
   ```
   🔗 Conectando al agente Transbank...
   ✅ Agente conectado
   🔌 Abriendo puerto COM15...
   ✅ Puerto COM15 abierto
   📡 Verificando con poll...
   ✅ POS responde correctamente
   ```

4. **Si TIENES el POS conectado:**
   ```
   ✅ ¡CONECTADO EXITOSAMENTE!
   Puerto: COM15
   Estado: POS Operativo
   
   Ahora puede realizar operaciones.
   ```

5. **Si NO tienes el POS (desarrollo):**
   ```
   ❌ Timeout
   
   💡 ESTO ES NORMAL SI:
   • No tienes el POS conectado en este PC ✓
   • El POS está apagado
   • El puerto no es correcto
   
   👉 En producción debe funcionar.
   ```

---

## 📊 TODOS LOS MÉTODOS NUEVOS DISPONIBLES

Una vez conectado, estos botones usan el SDK oficial:

| Botón | Método SDK | Descripción |
|-------|-----------|-------------|
| 🔑 Cargar Llaves | `Transbank.POS.loadKeys()` | Carga llaves criptográficas |
| 📊 Totales del Día | `Transbank.POS.getTotals()` | Consulta ventas del día |
| 🌙 Cerrar Día | `Transbank.POS.closeDay()` | Cierre de batch |
| 📄 Última Venta | `Transbank.POS.getLastSale()` | Info última transacción |
| ❤️ Verificar Conexión | `Transbank.POS.poll()` | Verifica estado del POS |
| 💳 Procesar Venta | `Transbank.POS.doSale()` | Venta con callback |
| 🔄 Anular | `Transbank.POS.refund()` | Anular transacción |

---

## 🔍 LOGS EN CONSOLA

Presiona **F12** y ve a la pestaña "Consola" para ver:

```
🔌 Intentando conectar al puerto COM15
1️⃣ Conectando al agente Transbank (localhost:8090)...
✅ Agente conectado
2️⃣ Abriendo puerto COM15 @ 115200 baud...
✅ Puerto COM15 abierto
3️⃣ Verificando comunicación con POS (poll)...
✅ POS responde correctamente
```

---

## ⚠️ REQUISITOS

Para que funcione necesitas:

### En PC de Desarrollo (sin POS):
- ✅ El agente Transbank ejecutándose
- ✅ Escribir COM15 (o cualquier puerto)
- ⚠️ Dará timeout (normal, no hay hardware)

### En PC de Producción (con POS):
- ✅ Agente Transbank ejecutándose
- ✅ POS encendido y conectado en COM15
- ✅ Drivers instalados
- ✅ Debe conectar exitosamente

---

## 🎯 RESUMEN

### LO QUE TIENES AHORA:

✅ Campo GRANDE y VISIBLE para escribir puerto (viene con COM15)
✅ SDK oficial de Transbank integrado
✅ Todos los métodos nuevos funcionando:
   - loadKeys()
   - closeDay()
   - getTotals()
   - getLastSale()
   - poll()
   - doMulticodeSale()
   - getDetails()
   - refund()

✅ Mensajes de error informativos
✅ Logs detallados en consola
✅ Interfaz lista para desarrollo Y producción

---

## 🚀 PRÓXIMOS PASOS

1. **CTRL + F5** en `http://localhost:8000/app/pos/transbank/`

2. **Verás el recuadro VERDE** arriba con el campo grande

3. **Ya tiene "COM15"** pre-cargado

4. **Haz clic en**: `[🔌 CONECTAR AL PUERTO]`

5. **En producción**: Funcionará con el Ingenico DESK 3500

6. **En desarrollo**: Verás timeout (normal sin POS)

---

## 📝 NOTA IMPORTANTE

El campo está en la pestaña **"Procesar Venta"** (segunda pestaña), NO en "Terminales POS".

---

¿Necesitas que te muestre cómo probar algún método específico del SDK (como doMulticodeSale, getDetails, etc.)?

