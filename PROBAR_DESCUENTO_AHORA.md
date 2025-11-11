# 🧪 PROBAR DESCUENTO GLOBAL AHORA

## ✅ Referencias YA funcionan

Ahora vamos por el descuento global.

---

## 🚀 PASOS

### 1. Limpiar caché del navegador
```
Ctrl + Shift + Delete
→ Todo el tiempo
→ Imágenes y archivos en caché
→ Borrar
```

### 2. Cerrar y abrir navegador
- Cerrar TODO
- Abrir de nuevo

### 3. Ir a interfaz
```
http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
Ctrl + Shift + R (forzar recarga)
```

### 4. Abrir consolas
- **F12** en navegador → Console
- **Terminal** del servidor visible

### 5. Cargar ejemplo
Clic en "Cargar Ejemplo"

### 6. Verificar campo de descuento
- Campo "Descuento Global ($)" debe mostrar: **10000**
- Si muestra 0, cambiarlo a 10000

### 7. Generar TXT
Clic en "Generar Archivo TXT"

---

## 👀 QUÉ BUSCAR EN LOS LOGS

### En la CONSOLA DEL NAVEGADOR:

```javascript
🔍 VERIFICACIÓN FINAL:
   - Referencias en datos: [{tipo_documento: '801', ...}]
   - Descuento en datos.totales: 10000  ← DEBE SER 10000
   - Descuento calculado (montoDescGlobal): 10000
```

**Si muestra:**
```javascript
- Descuento en datos.totales: 0  ← PROBLEMA
```

**Entonces:** El JavaScript NO está pasando el descuento a totales

### En la TERMINAL DEL SERVIDOR:

```python
🔍 DEBUG - Descuento global: 10000  ← DEBE SER 10000
🔍 DEBUG - Agregando línea de descuento global: 10000
🔍 DEBUG - Línea descuento: D|DESCUENTO GLOBAL|10000||}
```

**Si muestra:**
```python
🔍 DEBUG - Descuento global: 0  ← PROBLEMA
```

**Entonces:** El descuento NO llega al servidor

---

## 🔍 VERIFICACIÓN RÁPIDA

En la consola del navegador (F12), ejecuta:

```javascript
// Verificar que el JavaScript actualizado se cargó
GeneradorTXTAcepta.crearFacturaElectronica.toString().includes('descuento_global')
```

**Debe dar:** `true`

**Si da `false`:** El navegador sigue usando archivo viejo

---

## 📝 REPORTA

Después de limpiar caché y probar, dime:

**1. ¿Qué valor muestra en la consola del navegador?**
```
Descuento en datos.totales: _____
```

**2. ¿Qué valor muestra en la terminal del servidor?**
```
DEBUG - Descuento global: _____
```

**3. ¿Aparece la línea `D|DESCUENTO GLOBAL|...` en el TXT?**
- [ ] Sí
- [ ] No

---

Con esos datos sabré exactamente dónde está el problema. 🔍

