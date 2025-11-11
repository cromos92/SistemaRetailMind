# 🔍 DEBUG - Problema con Folio en Generador TXT Acepta

## 🎯 Problema Reportado

El archivo TXT generado muestra:
```
33|150|2025-11-08||||1||||2025-11-08T15:54:50|
```

Pero debería mostrar:
```
33|12345|2025-11-08||||1||||2025-11-08T15:54:50|
```

**El folio aparece como "150" en lugar de "12345"**

---

## ✅ CAMBIOS REALIZADOS

Se agregaron puntos de debugging en:

### 1. Frontend (JavaScript) ✅
- **Archivo:** `interfaz_prueba_acepta.html`
- **Ubicación:** Función `generarTXT()`
- **Logs agregados:**
  - Valor del folio del input
  - Valor del folio después de `parseInt()`
  - Datos completos antes de enviar al servidor
  - Tipo de dato del folio

### 2. Backend (Python) ✅
- **Archivo:** `views_modulo_documentos.py`
- **Ubicación:** Función `generar_txt_acepta_api()` y `generar_txt_dte_acepta()`
- **Logs agregados:**
  - Folio recibido del request
  - Tipo de dato del folio
  - Folio antes de convertir a string
  - Línea 1 completa generada

---

## 🧪 PASOS PARA DIAGNOSTICAR

### Paso 1: Reiniciar el Servidor
```bash
# Detén el servidor (Ctrl+C)
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py runserver
```

### Paso 2: Limpiar Caché del Navegador
1. Abre Chrome/Firefox
2. Presiona `Ctrl + Shift + R` para forzar recarga
3. O abre DevTools (F12) y mantén clic derecho en el botón de recargar → "Vaciar caché y recargar de forma forzada"

### Paso 3: Abrir la Interfaz
```
http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
```

### Paso 4: Abrir Consolas de Debug

#### A. Consola del Navegador (F12)
- Abre las DevTools (presiona F12)
- Ve a la pestaña "Console"
- Deja esta ventana abierta

#### B. Terminal del Servidor Django
- Deja visible la terminal donde corre el servidor
- Aquí aparecerán los logs de Python

### Paso 5: Generar un TXT
1. Haz clic en "Cargar Ejemplo"
2. Verifica que el campo "Folio" muestre **12345**
3. Abre la consola del navegador (F12)
4. Haz clic en "Generar Archivo TXT"

### Paso 6: Revisar los Logs

#### En la Consola del Navegador deberías ver:
```javascript
🔍 DEBUG - Folio del input: 12345
🔍 DEBUG - Folio después de parseInt: 12345
🔍 DEBUG - Datos completos a enviar: {documento: {tipo_documento: 33, folio: 12345, ...}, ...}
🔍 DEBUG - Folio en documento: 12345
🔍 DEBUG - Tipo de dato del folio: number
🔍 DEBUG - Enviando datos al servidor...
```

#### En la Terminal del Servidor Django deberías ver:
```python
🔍 DEBUG - Folio recibido: 12345
🔍 DEBUG - Tipo de dato: <class 'int'>
🔍 DEBUG - Datos documento completos: {'tipo_documento': 33, 'folio': 12345, ...}
🔍 DEBUG generar_txt - Folio raw: 12345 (tipo: <class 'int'>)
🔍 DEBUG generar_txt - Folio convertido a str: 12345
🔍 DEBUG - Línea 1 generada: 33|12345|2025-11-08||||1||||2025-11-08T15:54:50|
```

---

## 🔍 POSIBLES CAUSAS Y SOLUCIONES

### Causa 1: Caché del Navegador ⚠️
**Síntoma:** Los logs en el navegador no aparecen

**Solución:**
1. Limpia caché del navegador (Ctrl + Shift + Delete)
2. Selecciona "Todo el tiempo"
3. Marca "Imágenes y archivos en caché"
4. Haz clic en "Borrar datos"
5. Reinicia el navegador
6. Vuelve a cargar la página con Ctrl + Shift + R

### Causa 2: Archivo JavaScript no Actualizado ⚠️
**Síntoma:** Los logs aparecen en Python pero no en JavaScript

**Solución:**
```bash
# Opción 1: Verificar que el archivo se sirve correctamente
http://localhost:8000/static/js/generador_txt_acepta.js

# Opción 2: Colectar archivos estáticos (si DEBUG=False)
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py collectstatic --clear --noinput
```

### Causa 3: Valor del Input Incorrecto ⚠️
**Síntoma:** El log muestra un valor diferente de 12345

**Solución:**
1. Verifica el valor del input en la consola del navegador:
   ```javascript
   document.getElementById('folio').value
   ```
2. Si no es 12345, hay un problema con la función `cargarEjemplo()`
3. Verifica que no haya JavaScript adicional modificando el valor

### Causa 4: Problema en el Servidor ⚠️
**Síntoma:** Python recibe un valor diferente del que JavaScript envió

**Solución:**
1. Revisa los logs en orden cronológico
2. Compara el valor que JavaScript dice enviar con el que Python recibe
3. Si son diferentes, puede haber un middleware o proxy modificando la petición

### Causa 5: Código Python con Error ⚠️
**Síntoma:** La línea 1 generada muestra "150" en lugar de "12345"

**Solución:**
1. Revisa si el folio se está confundiendo con otro campo
2. Verifica que no se esté aplicando `formatear_monto()` al folio
3. Busca en el código: `formatear_monto(doc.get('folio')` (no debería existir)

---

## 📊 TABLA DE DIAGNÓSTICO

| Paso | ¿Qué verificar? | Valor esperado | Si falla... |
|------|----------------|----------------|-------------|
| 1 | Valor del input | 12345 | Problema en HTML/cargarEjemplo() |
| 2 | parseInt() | 12345 (number) | Problema en conversión JS |
| 3 | datos.documento.folio | 12345 (number) | Problema en crearFacturaElectronica() |
| 4 | Folio recibido en Python | 12345 (int) | Problema en transmisión HTTP |
| 5 | Folio en generar_txt | "12345" (str) | Problema en procesamiento Python |
| 6 | Línea 1 generada | 33\|12345\|... | Problema en formateo final |

---

## 🚨 REPORTAR RESULTADOS

Después de seguir estos pasos, reporta:

1. **¿Aparecen los logs en la consola del navegador?**
   - [ ] Sí
   - [ ] No

2. **¿Qué valor muestra para el folio en JavaScript?**
   - Valor: _______________

3. **¿Aparecen los logs en la terminal del servidor?**
   - [ ] Sí
   - [ ] No

4. **¿Qué valor muestra para el folio en Python?**
   - Valor: _______________

5. **¿Qué muestra la Línea 1 generada?**
   - Línea completa: _______________

6. **¿El archivo descargado es correcto o incorrecto?**
   - [ ] Correcto (folio = 12345)
   - [ ] Incorrecto (folio = 150)

---

## 💡 SOLUCIÓN RÁPIDA SI EL PROBLEMA PERSISTE

Si después de todo esto el problema sigue:

```bash
# 1. Detener servidor
Ctrl + C

# 2. Limpiar caché de Python
cd C:\DjangoProyects\retailmind\SistemaRetailMind
Get-ChildItem -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force

# 3. Limpiar archivos estáticos
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py collectstatic --clear --noinput

# 4. Reiniciar servidor
python manage.py runserver

# 5. En el navegador
- Cerrar todas las pestañas
- Limpiar caché (Ctrl + Shift + Delete)
- Abrir nueva pestaña
- Ir a la interfaz
- Forzar recarga (Ctrl + Shift + R)
```

---

## 📝 NOTAS ADICIONALES

- Los logs con 🔍 son temporales solo para debugging
- Una vez identificado el problema, se pueden eliminar
- El símbolo 🔍 facilita encontrar los logs en consolas grandes

---

**Fecha:** Noviembre 8, 2025  
**Versión:** 1.0  
**Estado:** Listo para diagnosticar

