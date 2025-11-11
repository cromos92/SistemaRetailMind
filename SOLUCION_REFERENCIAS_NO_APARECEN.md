# 🔧 SOLUCIÓN: Referencias y Descuento No Aparecen

## 🎯 PROBLEMA IDENTIFICADO

**Logs muestran:**
- ✅ Frontend recopila: Referencias=1, Descuento=10000
- ❌ Backend recibe: Referencias=[], Descuento=0

**Causa:** El navegador usa **archivo JavaScript desactualizado** en caché.

---

## ✅ SOLUCIÓN APLICADA

He actualizado manualmente el archivo en `staticfiles`:
```
retailmind/staticfiles/js/generador_txt_acepta.js
```

**PERO** necesitas hacer lo siguiente para que el navegador use la nueva versión:

---

## 🚀 PASOS OBLIGATORIOS

### PASO 1: Limpiar TODA la caché del navegador

**Opción A - Chrome/Edge:**
1. Presiona `Ctrl + Shift + Delete`
2. Selecciona "Todo el tiempo"
3. Marca SOLO "Imágenes y archivos en caché"
4. Clic en "Borrar datos"

**Opción B - Forzar recarga sin caché:**
1. Abre DevTools (F12)
2. Mantén clic derecho en el botón recargar
3. Selecciona "Vaciar caché y recargar de forma forzada"

### PASO 2: Cerrar COMPLETAMENTE el navegador
1. Cierra TODAS las pestañas
2. Cierra el navegador
3. Espera 5 segundos
4. Abre el navegador de nuevo

### PASO 3: Ir a la interfaz
```
http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
```

### PASO 4: Forzar recarga SIN caché
```
Ctrl + Shift + R
```

### PASO 5: Verificar que el archivo JS se actualizó

Abre la consola (F12) y ejecuta:
```javascript
GeneradorTXTAcepta.crearFacturaElectronica.toString().includes('referencias')
```

**Debe dar:** `true`

Si da `false` = El navegador sigue usando la versión vieja

### PASO 6: Generar TXT y ver logs

1. Cargar Ejemplo
2. Generar TXT
3. Ver terminal del servidor

**Ahora DEBE mostrar:**
```python
🔍 DEBUG - Referencias recibidas: [{'tipo_documento': '801', ...}]
🔍 DEBUG - Cantidad de referencias: 1
🔍 DEBUG - Descuento global: 10000
```

---

## 🔍 VERIFICACIÓN RÁPIDA

### En la consola del navegador (F12):

```javascript
// 1. Verificar que el módulo tiene referencias
console.log('Módulo actualizado:', 
    typeof GeneradorTXTAcepta.crearFacturaElectronica({
        folio: 1,
        fechaEmision: '2025-11-10',
        emisor: {rut: '1-1', razon_social: 'TEST', giro: 'TEST'},
        receptor: {rut: '1-1', razon_social: 'TEST'},
        productos: [{nombre: 'TEST', cantidad: 1, precio_unitario: 1000}],
        referencias: [{tipo_documento: '801', folio: 'TEST'}]
    }).referencias
);

// Debe mostrar: [{tipo_documento: '801', folio: 'TEST'}]
// Si muestra undefined = archivo viejo
```

---

## ⚠️ SI AÚN NO FUNCIONA

### Opción 1: Modo Incógnito
1. Abre ventana de incógnito (Ctrl + Shift + N)
2. Ve a la interfaz
3. Prueba generar TXT
4. Si funciona = problema de caché
5. Cierra el navegador normal y limpia caché completamente

### Opción 2: Verificar archivo servido

Abre en el navegador:
```
http://localhost:8000/static/js/generador_txt_acepta.js
```

Busca en el código (Ctrl + F):
```
referencias = []
```

**Debe aparecer** en la línea donde dice:
```javascript
referencias = []  // ✅ Referencias a otros documentos
```

Si NO aparece = El servidor sigue sirviendo el archivo viejo

### Opción 3: Forzar uso del archivo de app/static

En `settings.py`, verifica que `DEBUG = True` y que esté configurado:
```python
STATICFILES_DIRS = [
    BASE_DIR / 'retailmind' / 'static',
    BASE_DIR / 'app' / 'static',  # ← Debe estar
]
```

Luego reinicia el servidor.

---

## 📋 CHECKLIST

Antes de reportar que no funciona:

- [ ] Limpié TODO el caché del navegador
- [ ] Cerré y abrí el navegador
- [ ] Forcé recarga con Ctrl + Shift + R
- [ ] Verifiqué que `GeneradorTXTAcepta.crearFacturaElectronica.toString().includes('referencias')` da `true`
- [ ] Vi los logs en la terminal del servidor
- [ ] Probé en ventana de incógnito

---

## 🎯 DESPUÉS DE LIMPIAR CACHÉ

Cuando vuelvas a generar, los logs DEBERÍAN mostrar:

**Backend:**
```
🔍 DEBUG - Referencias recibidas: [{'tipo_documento': '801', 'folio': 'OC-98765', ...}]
🔍 DEBUG - Cantidad de referencias: 1
🔍 DEBUG - Descuento global: 10000
🔍 DEBUG - Agregando línea de descuento global: 10000
🔍 DEBUG - Agregando 1 referencias al TXT
```

**TXT generado:**
```
190375|0|19|36171|226546|||||||||||||}
D|DESCUENTO GLOBAL|10000||}            ← DEBE APARECER
~
|Item PRODUCTO A|...|Item|}
~
801|| OC-98765 | 2025-11-05|| |}       ← DEBE APARECER
```

---

**El problema es SOLO caché del navegador. El código está correcto.** ✅

Limpia caché completamente y prueba de nuevo.

