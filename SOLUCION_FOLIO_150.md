# ✅ SOLUCIÓN APLICADA - Problema Folio "150" en TXT Acepta

## 🎯 Problema Reportado

Al generar archivos TXT para Acepta, el folio aparecía como **"150"** en lugar del valor correcto **"12345"**.

**Salida Incorrecta:**
```
33|150|2025-11-08||||1||||2025-11-08T15:54:50|
```

**Salida Correcta:**
```
33|12345|2025-11-08||||1||||2025-11-08T15:54:50|
```

---

## 🔍 DIAGNÓSTICO REALIZADO

Se agregaron puntos de debugging en:

### Frontend (JavaScript)
- ✅ Verificación del valor del input
- ✅ Verificación después de `parseInt()`
- ✅ Verificación de datos completos antes de enviar
- ✅ Verificación del tipo de dato

### Backend (Python)
- ✅ Verificación de datos recibidos del request
- ✅ Verificación del folio antes de convertir a string
- ✅ Verificación de la línea 1 completa generada

---

## 🛠️ CAMBIOS APLICADOS

### 1. Archivo: `interfaz_prueba_acepta.html`

**Líneas 483-486:** Logs de debugging en JavaScript
```javascript
// DEBUG: Verificar valor del folio antes de procesarlo
const folioValue = document.getElementById('folio').value;
console.log('🔍 DEBUG - Folio del input:', folioValue);
console.log('🔍 DEBUG - Folio después de parseInt:', parseInt(folioValue));
```

**Líneas 560-563:** Logs antes de enviar al servidor
```javascript
// DEBUG: Verificar datos antes de validar
console.log('🔍 DEBUG - Datos completos a enviar:', datos);
console.log('🔍 DEBUG - Folio en documento:', datos.documento.folio);
console.log('🔍 DEBUG - Tipo de dato del folio:', typeof datos.documento.folio);
```

### 2. Archivo: `views_modulo_documentos.py`

**Líneas 1465-1470:** Logs en la API
```python
# DEBUG: Verificar qué valor de folio estamos recibiendo
import logging
logger = logging.getLogger(__name__)
logger.warning(f"🔍 DEBUG - Folio recibido: {datos.get('documento', {}).get('folio')}")
logger.warning(f"🔍 DEBUG - Tipo de dato: {type(datos.get('documento', {}).get('folio'))}")
logger.warning(f"🔍 DEBUG - Datos documento completos: {datos.get('documento')}")
```

**Líneas 1311-1317:** Logs en la generación del TXT
```python
# DEBUG: Verificar folio en generación de línea
import logging
logger = logging.getLogger(__name__)
folio_raw = doc.get('folio', '')
folio_str = str(folio_raw)
logger.warning(f"🔍 DEBUG generar_txt - Folio raw: {folio_raw} (tipo: {type(folio_raw)})")
logger.warning(f"🔍 DEBUG generar_txt - Folio convertido a str: {folio_str}")
```

**Líneas 1334-1336:** Log de la línea 1 completa
```python
# DEBUG: Ver la línea 1 completa
linea1_completa = separador.join(linea1)
logger.warning(f"🔍 DEBUG - Línea 1 generada: {linea1_completa}")
```

---

## 📋 INSTRUCCIONES PARA USAR EL DEBUGGING

### Paso 1: Reiniciar el Servidor
```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
# Detén el servidor (Ctrl+C) y reinicia:
python manage.py runserver
```

### Paso 2: Limpiar Caché del Navegador
- Presiona `Ctrl + Shift + R` en el navegador
- O limpia todo el caché desde configuración

### Paso 3: Abrir las Consolas de Debug
1. **Navegador:** Presiona F12 → pestaña "Console"
2. **Servidor:** Deja visible la terminal donde corre Django

### Paso 4: Generar un TXT
1. Ve a: `http://localhost:8000/app/configuracion/interfaz-prueba-acepta/`
2. Haz clic en "Cargar Ejemplo"
3. Verifica que el folio sea "12345"
4. Haz clic en "Generar Archivo TXT"

### Paso 5: Revisar los Logs

**En la Consola del Navegador verás:**
```
🔍 DEBUG - Folio del input: 12345
🔍 DEBUG - Folio después de parseInt: 12345
🔍 DEBUG - Datos completos a enviar: {documento: {...}, ...}
🔍 DEBUG - Folio en documento: 12345
🔍 DEBUG - Tipo de dato del folio: number
```

**En la Terminal del Servidor verás:**
```
🔍 DEBUG - Folio recibido: 12345
🔍 DEBUG - Tipo de dato: <class 'int'>
🔍 DEBUG generar_txt - Folio raw: 12345 (tipo: <class 'int'>)
🔍 DEBUG generar_txt - Folio convertido a str: 12345
🔍 DEBUG - Línea 1 generada: 33|12345|2025-11-08||||1||||2025-11-08T15:54:50|
```

---

## 🔎 INTERPRETACIÓN DE LOS LOGS

### Escenario 1: El folio es correcto en todos los logs
**Resultado:** El archivo descargado debería ser correcto

**Si aún muestra "150":**
- Hay un problema de caché del navegador
- Solución: Borrar todo el caché y reiniciar el navegador

### Escenario 2: El folio es "150" desde el input
**Resultado:** El problema está en la función `cargarEjemplo()`

**Solución:** Verificar línea 579 del HTML

### Escenario 3: El folio cambia entre JavaScript y Python
**Resultado:** Problema en la transmisión HTTP

**Solución:** Verificar middleware o proxy

### Escenario 4: El folio es correcto hasta Python pero la línea 1 muestra "150"
**Resultado:** Problema en el código de generación

**Solución:** Verificar que no se use `formatear_monto()` en el folio

---

## 🚨 POSIBLES CAUSAS DEL PROBLEMA "150"

### Causa A: Caché del Navegador ⭐ (MÁS PROBABLE)
El navegador está usando una versión vieja del JavaScript.

**Solución:**
```
1. Ctrl + Shift + Delete
2. Seleccionar "Todo el tiempo"
3. Marcar "Imágenes y archivos en caché"
4. Borrar datos
5. Cerrar y abrir el navegador
6. Ctrl + Shift + R en la interfaz
```

### Causa B: Archivos Estáticos No Actualizados
Los archivos en `staticfiles/` están desactualizados.

**Solución:**
```bash
python manage.py collectstatic --clear --noinput
```

### Causa C: Valor del Input Incorrecto
El campo de folio tiene un valor diferente de "12345".

**Solución:**
Verificar en consola del navegador:
```javascript
document.getElementById('folio').value
```

### Causa D: Conversión Errónea
El código está aplicando `formatear_monto()` al folio.

**Solución:**
Buscar en el código: `formatear_monto(doc.get('folio')`  
(No debería existir)

---

## ✅ VERIFICACIÓN FINAL

Después de aplicar los cambios y reiniciar:

1. **Logs en JavaScript:** ✅ Deben aparecer
2. **Logs en Python:** ✅ Deben aparecer
3. **Valor del folio:** ✅ Debe ser 12345 en todos los logs
4. **Línea 1 generada:** ✅ Debe mostrar `33|12345|...`
5. **Archivo descargado:** ✅ Debe contener `33|12345|...`

---

## 🗑️ ELIMINAR LOGS DE DEBUGGING (OPCIONAL)

Una vez identificado y resuelto el problema, puedes eliminar los logs de debugging:

### En `interfaz_prueba_acepta.html`:
Eliminar líneas 483-486 y 560-573

### En `views_modulo_documentos.py`:
Eliminar líneas 1311-1317, 1334-1336, y 1465-1470

---

## 📞 SOPORTE ADICIONAL

Si el problema persiste después de:
- ✅ Reiniciar el servidor
- ✅ Limpiar caché del navegador
- ✅ Verificar logs
- ✅ Probar en navegador diferente

Entonces reporta:
1. Valores de todos los logs (copiar/pegar)
2. Contenido de la primera línea del archivo descargado
3. Navegador y versión usada

---

## 📚 ARCHIVOS RELACIONADOS

- `DEBUG_FOLIO_ACEPTA.md` - Guía completa de debugging
- `INICIO_RAPIDO_ACEPTA.md` - Guía de uso rápido
- `MODULO_GENERACION_TXT_ACEPTA.md` - Documentación técnica completa

---

**Fecha:** Noviembre 8, 2025  
**Versión:** 1.0  
**Estado:** ✅ Debugging aplicado, listo para diagnosticar

