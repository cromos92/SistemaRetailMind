# 🔍 DEBUGGING - Referencias y Descuentos Globales

## 🎯 PROBLEMA REPORTADO

Al generar el TXT:
- ❌ Referencias NO aparecen
- ❌ Descuento global NO aparece

---

## 🔧 DEBUGGING AGREGADO

He agregado logs completos en:

### Frontend (Consola del navegador):
```javascript
🔍 DEBUG - Referencias: [...]
🔍 DEBUG - Cantidad de referencias: 1
✅ Hay referencias para enviar
   Ref 1: Tipo=801, Folio=OC-98765, Fecha=2025-11-05
🔍 DEBUG - Descuento global: 10000
```

### Backend (Terminal del servidor):
```python
🔍 DEBUG - Descuento global en totales: 10000
🔍 DEBUG - Agregando línea de descuento global: 10000
🔍 DEBUG - Línea descuento: D|DESCUENTO GLOBAL|10000||}
🔍 DEBUG - Referencias recibidas: [{'tipo_documento': '801', ...}]
🔍 DEBUG - Cantidad de referencias: 1
🔍 DEBUG - Procesando referencias: [...]
🔍 DEBUG - Agregando 1 referencias al TXT
🔍 DEBUG - Referencia 1: tipo=801, folio=OC-98765
```

---

## 🚀 PASOS PARA DIAGNOSTICAR

### Paso 1: Reiniciar servidor
```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py runserver
```

### Paso 2: Limpiar caché
```
Ctrl + Shift + R
```

### Paso 3: Abrir consolas
- **Navegador:** F12 → Console
- **Servidor:** Terminal visible

### Paso 4: Cargar ejemplo
1. Ir a interfaz
2. Clic en "Cargar Ejemplo"
3. Verificar que muestre:
   - Descuento Global: $10,000 ✅
   - Referencia: Orden de Compra OC-98765 ✅

### Paso 5: Generar TXT
1. Clic en "Generar Archivo TXT"
2. **Revisar AMBAS consolas**

---

## 📊 QUÉ BUSCAR EN LOS LOGS

### En la Consola del Navegador (F12):

**Si las referencias se recopilan bien:**
```javascript
✅ Hay referencias para enviar
   Ref 1: Tipo=801, Folio=OC-98765, Fecha=2025-11-05
```

**Si NO se recopilan:**
```javascript
⚠️ NO hay referencias
```

**En este caso:** Problema en `recopilarReferencias()`

### En la Terminal del Servidor:

**Si el descuento llega:**
```python
🔍 DEBUG - Descuento global en totales: 10000
🔍 DEBUG - Agregando línea de descuento global: 10000
🔍 DEBUG - Línea descuento: D|DESCUENTO GLOBAL|10000||}
```

**Si NO llega:**
```python
🔍 DEBUG - Descuento global en totales: 0
🔍 DEBUG - NO hay descuento global para agregar
```

**En este caso:** El frontend no está enviando `descuento_global` en `totales`

**Si las referencias llegan:**
```python
🔍 DEBUG - Cantidad de referencias: 1
🔍 DEBUG - Agregando 1 referencias al TXT
```

**Si NO llegan:**
```python
🔍 DEBUG - Cantidad de referencias: 0
🔍 DEBUG - NO hay referencias para agregar
```

---

## 🔍 POSIBLES CAUSAS

### Causa 1: Referencias no se recopilan
**Síntoma:** Consola navegador muestra "NO hay referencias"

**Verificar:**
```javascript
// Abrir consola (F12) y ejecutar:
document.querySelectorAll('#referencias-container .referencia-row').length
// Debe mostrar: 1 (si cargaste el ejemplo)
```

**Solución:** Problema en el selector o en el HTML

### Causa 2: Descuento no se envía en totales
**Síntoma:** Backend recibe descuento = 0

**Verificar en consola navegador:**
```javascript
console.log('Descuento:', datos.totales.descuento_global);
```

**Solución:** Agregar descuento_global a totales en JavaScript

### Causa 3: Referencias no se envían
**Síntoma:** Backend recibe referencias = []

**Verificar en consola navegador:**
```javascript
console.log('Referencias:', datos.referencias);
```

---

## 🛠️ VERIFICACIÓN RÁPIDA

Abre la consola del navegador y ejecuta estos comandos después de cargar el ejemplo:

### Verificar referencias en DOM:
```javascript
document.querySelectorAll('#referencias-container .referencia-row').length
// Debe dar: 1
```

### Verificar valores de referencia:
```javascript
document.querySelector('.referencia-tipo').value
// Debe dar: "801"

document.querySelector('.referencia-folio').value
// Debe dar: "OC-98765"
```

### Verificar descuento:
```javascript
document.getElementById('descuento_global').value
// Debe dar: "10000"
```

---

## 📋 CHECKLIST DE VERIFICACIÓN

**Antes de generar TXT:**

- [ ] Campo descuento global muestra: 10000
- [ ] Hay 1 referencia visible en pantalla
- [ ] Tipo de referencia: Orden de Compra (801)
- [ ] Folio referencia: OC-98765
- [ ] Consola navegador abierta (F12)
- [ ] Terminal servidor visible

**Al generar TXT:**

- [ ] Consola navegador muestra: "✅ Hay referencias para enviar"
- [ ] Consola navegador muestra: "Ref 1: Tipo=801..."
- [ ] Terminal muestra: "Agregando línea de descuento global"
- [ ] Terminal muestra: "Agregando 1 referencias al TXT"

**En el archivo TXT:**

- [ ] Después de totales hay: `D|DESCUENTO GLOBAL|10000||}` 
- [ ] Después de productos hay: `801|| OC-98765 | 2025-XX-XX|| |}`

---

## 💡 SOLUCIÓN RÁPIDA

Si los logs muestran que SÍ se envían pero NO aparecen en el TXT:

1. Verifica que el archivo descargado sea el nuevo (no caché)
2. Abre el TXT con un editor de texto plano
3. Busca la línea que empieza con `D|`
4. Busca la línea que empieza con `801|`

---

**Ejecuta estos pasos y reporta qué ves en los logs** 🔍

