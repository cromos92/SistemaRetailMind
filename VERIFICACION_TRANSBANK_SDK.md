# ✅ Checklist de Verificación - Transbank POS SDK

## 🎯 Objetivo

Verificar que la integración de Transbank POS SDK esté correctamente instalada y funcionando.

---

## 📋 Checklist de Archivos

### Archivos que DEBEN existir

- [ ] `retailmind/app/services/transbank_pos_sdk_service.py`
- [ ] `retailmind/app/views_transbank_sdk.py`
- [ ] `retailmind/app/management/commands/test_transbank_pos.py`
- [ ] `test_transbank_sdk.py`
- [ ] `ejemplo_frontend_transbank.html`
- [ ] `GUIA_TRANSBANK_POS_SDK.md`
- [ ] `INSTALACION_TRANSBANK_SDK.md`
- [ ] `RESUMEN_IMPLEMENTACION_TRANSBANK_SDK.md`
- [ ] `README_TRANSBANK_POS_SDK.md`
- [ ] `VERIFICACION_TRANSBANK_SDK.md` (este archivo)

### Archivos modificados

- [ ] `retailmind/app/urls.py` - debe tener imports de `views_transbank_sdk`
- [ ] `retailmind/retailmind/settings.py` - debe tener `rest_framework` en INSTALLED_APPS
- [ ] `requirements.txt` - debe tener `transbank-pos-sdk` y `djangorestframework`

---

## 🔧 Verificación Paso a Paso

### 1. Verificar Dependencias Instaladas

```bash
# Verificar transbank-pos-sdk
pip show transbank-pos-sdk

# Verificar djangorestframework
pip show djangorestframework
```

**Resultado esperado:**
```
Name: transbank-pos-sdk
Version: 0.3.0
...

Name: djangorestframework
Version: 3.14.0
...
```

- [ ] ✅ transbank-pos-sdk instalado
- [ ] ✅ djangorestframework instalado

---

### 2. Verificar settings.py

```bash
# Windows PowerShell
Select-String -Path "retailmind\retailmind\settings.py" -Pattern "rest_framework"

# Linux/Mac
grep "rest_framework" retailmind/retailmind/settings.py
```

**Resultado esperado:**
```python
'rest_framework',  # Django REST Framework
```

- [ ] ✅ `rest_framework` en INSTALLED_APPS
- [ ] ✅ Configuración REST_FRAMEWORK existe

---

### 3. Verificar urls.py

```bash
# Windows PowerShell
Select-String -Path "retailmind\app\urls.py" -Pattern "transbank_sdk"

# Linux/Mac
grep "transbank_sdk" retailmind/app/urls.py
```

**Resultado esperado:**
```python
from .views_transbank_sdk import (
    listar_puertos,
    conectar,
    ...
)
```

- [ ] ✅ Imports de `views_transbank_sdk` presentes
- [ ] ✅ URLs `/pos/transbank/` configuradas

---

### 4. Iniciar Servidor Django

```bash
python manage.py runserver
```

**Resultado esperado:**
```
Django version 4.2.2, using settings 'retailmind.settings'
Starting development server at http://127.0.0.1:8000/
Quit the server with CTRL-BREAK.
```

- [ ] ✅ Servidor inicia sin errores
- [ ] ✅ No hay errores de importación
- [ ] ✅ No hay errores de configuración

---

### 5. Probar Endpoint de Puertos (API Test)

**En otra terminal (con servidor corriendo):**

```bash
# Windows PowerShell
Invoke-WebRequest -Uri "http://localhost:8000/app/pos/transbank/puertos/" -UseBasicParsing

# Linux/Mac o Git Bash
curl http://localhost:8000/app/pos/transbank/puertos/
```

**Resultado esperado:**
```json
{
    "success": true,
    "puertos": ["COM3", "COM4"]
}
```

O si no hay POS conectado:
```json
{
    "success": true,
    "puertos": []
}
```

- [ ] ✅ Endpoint responde (status 200)
- [ ] ✅ Respuesta en formato JSON
- [ ] ✅ Campo "success" presente

---

### 6. Verificar Todos los Endpoints

**Ejecutar estos comandos para verificar que todos los endpoints existan:**

```bash
# GET endpoints
curl http://localhost:8000/app/pos/transbank/puertos/
curl http://localhost:8000/app/pos/transbank/verificar/
curl http://localhost:8000/app/pos/transbank/ultima-venta/
curl http://localhost:8000/app/pos/transbank/totales/
curl "http://localhost:8000/app/pos/transbank/detalles/?imprimir_en_pos=false"

# POST endpoints (estos darán error si no hay POS, pero deben responder)
curl -X POST http://localhost:8000/app/pos/transbank/conectar/ \
  -H "Content-Type: application/json" \
  -d '{"puerto": "COM3"}'

curl -X POST http://localhost:8000/app/pos/transbank/desconectar/

curl -X POST http://localhost:8000/app/pos/transbank/cargar-llaves/

curl -X POST http://localhost:8000/app/pos/transbank/venta/ \
  -H "Content-Type: application/json" \
  -d '{"monto": 1000, "ticket": "TEST"}'

curl -X POST http://localhost:8000/app/pos/transbank/anular/ \
  -H "Content-Type: application/json" \
  -d '{"operation_id": 1}'

curl -X POST http://localhost:8000/app/pos/transbank/cerrar-dia/
```

**Todos deben:**
- [ ] ✅ Responder con status 200 o 500 (no 404)
- [ ] ✅ Retornar JSON válido
- [ ] ✅ Tener campo "success" en la respuesta

---

### 7. Ejecutar Script de Prueba

```bash
python test_transbank_sdk.py
```

**Resultado esperado:**
```
╔══════════════════════════════════════════════════════════╗
║   🧪 SUITE DE PRUEBAS TRANSBANK POS SDK                 ║
║   Sistema RetailMind - Integración Directa              ║
╚══════════════════════════════════════════════════════════╝
```

- [ ] ✅ Script ejecuta sin errores de importación
- [ ] ✅ Muestra interfaz de prueba
- [ ] ✅ Permite listar puertos

---

### 8. Ejecutar Comando Django

```bash
python manage.py test_transbank_pos
```

**Resultado esperado:**
```
============================================================
   PRUEBA TRANSBANK POS SDK
   Sistema RetailMind
============================================================

📍 Listando puertos disponibles...
```

- [ ] ✅ Comando existe
- [ ] ✅ Ejecuta sin errores
- [ ] ✅ Muestra interfaz de prueba

---

### 9. Verificar Ejemplo Frontend

**Abrir en navegador:**
```
file:///C:/DjangoProyects/retailmind/SistemaRetailMind/ejemplo_frontend_transbank.html
```

- [ ] ✅ Página carga correctamente
- [ ] ✅ Interfaz se ve bien
- [ ] ✅ Botón "Listar Puertos" funciona (con servidor corriendo)
- [ ] ✅ Log muestra mensajes

---

### 10. Prueba con POS Real (Opcional)

**Solo si tienes un terminal POS conectado:**

1. Conectar POS físicamente
2. Ejecutar:
   ```bash
   python test_transbank_sdk.py
   ```
3. Seleccionar puerto correcto
4. Seguir las instrucciones

- [ ] ✅ Detecta puerto del POS
- [ ] ✅ Conecta exitosamente
- [ ] ✅ POLL responde
- [ ] ✅ Llaves se cargan (si se ejecuta)

---

## 🎯 Verificación Rápida (30 segundos)

### Opción A: Verificación Básica

```bash
# 1. Iniciar servidor
python manage.py runserver &

# 2. Esperar 3 segundos
sleep 3

# 3. Probar endpoint
curl http://localhost:8000/app/pos/transbank/puertos/

# Debe retornar JSON con "success": true
```

### Opción B: Verificación con Comando

```bash
# Una sola línea
python manage.py test_transbank_pos
```

---

## ✅ Resultado de la Verificación

### TODO CORRECTO ✅

Si todos los puntos están marcados, la integración está **100% funcional**.

### HAY ERRORES ❌

Si algún punto falla, revisar:

1. **Dependencias no instaladas:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Archivos no encontrados:**
   - Verificar que todos los archivos estén en su lugar
   - Re-ejecutar la implementación

3. **Servidor no inicia:**
   - Revisar logs de Django
   - Verificar configuración en settings.py

4. **Endpoints retornan 404:**
   - Verificar urls.py
   - Reiniciar servidor

---

## 🆘 Solución de Problemas Comunes

### Error 1: ModuleNotFoundError

```bash
# Instalar dependencias faltantes
pip install transbank-pos-sdk==0.3.0
pip install djangorestframework==3.14.0
```

### Error 2: URL not found (404)

**Verificar que las URLs estén configuradas:**

```python
# En retailmind/app/urls.py debe haber:
from .views_transbank_sdk import (
    listar_puertos, conectar, ...
)

urlpatterns = [
    # ...
    path('pos/transbank/puertos/', listar_puertos, ...),
    # ...
]
```

### Error 3: REST framework no configurado

**Verificar settings.py:**

```python
INSTALLED_APPS = [
    # ...
    'rest_framework',  # Debe estar aquí
    # ...
]
```

### Error 4: Puerto no encontrado

**Normal si no hay POS conectado.**

Para probar sin POS:
- La API responderá con `puertos: []`
- Los endpoints de conexión darán error
- Pero la API está funcionando correctamente

---

## 📊 Tabla de Verificación Final

| Componente | Estado | Notas |
|------------|--------|-------|
| Archivos creados | ☐ | 10 archivos nuevos |
| Archivos modificados | ☐ | 3 archivos |
| Dependencias | ☐ | 2 paquetes |
| Servidor Django | ☐ | Puerto 8000 |
| Endpoint /puertos/ | ☐ | GET |
| Endpoint /conectar/ | ☐ | POST |
| Endpoint /venta/ | ☐ | POST |
| Script de prueba | ☐ | test_transbank_sdk.py |
| Comando Django | ☐ | test_transbank_pos |
| Ejemplo frontend | ☐ | HTML funcional |

---

## 🎉 Verificación Completada

Si llegaste hasta aquí y todos los puntos están ✅, entonces:

### ¡FELICITACIONES! 🎊

La integración de Transbank POS SDK está:

- ✅ **Completamente instalada**
- ✅ **Correctamente configurada**
- ✅ **100% funcional**
- ✅ **Lista para usar**

---

## 📝 Próximos Pasos

1. **Leer documentación completa:**
   - `GUIA_TRANSBANK_POS_SDK.md`

2. **Probar con POS real:**
   - Conectar terminal físico
   - Ejecutar `python test_transbank_sdk.py`

3. **Integrar en tu aplicación:**
   - Ver ejemplo frontend
   - Adaptar a tus necesidades

4. **Desplegar en producción:**
   - Configurar CORS
   - Configurar permisos
   - Probar exhaustivamente

---

**Verificación realizada:** [Fecha]  
**Estado:** ☐ TODO OK / ☐ HAY ERRORES  
**Notas adicionales:**

```
[Espacio para notas]
```

---

**¡Sistema listo para procesar pagos con Transbank!** 💳✨

