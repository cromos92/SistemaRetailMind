# 🔍 Diagnóstico - Fotos en Requerimientos

## 🎯 Problema Reportado
> "Las fotos no figuran cuando se crea un requerimiento"

---

## ✅ CAMBIOS APLICADOS

1. ✅ Debug agregado en formulario de creación
2. ✅ Debug agregado en visualización de detalle
3. ✅ Tabla actualizada con columna "Fotos"
4. ✅ Columna "Sucursal" agregada
5. ✅ Imagen placeholder si falla la carga

---

## 🧪 DIAGNÓSTICO PASO A PASO

### PASO 1: Crear Requerimiento con Foto

```
1. Ir a http://localhost:8000/app/requerimientos/crear/
2. Presionar F12 para abrir consola del navegador
3. Ir a tab "Console"
4. Llenar formulario
5. Click "Agregar Foto"
6. Seleccionar una imagen
7. Ver preview de la imagen (debe aparecer)
8. Click "Crear Requerimiento"
```

### PASO 2: Ver Debug en Consola

**Deberías ver algo como**:
```
=== FormData Debug ===
csrfmiddlewaretoken: xxxxxxxxxxx
tipo: GARANTIA
sku: 4819942
nombre_producto: CALZADO  
cliente_nombre: javier araya
motivo: Problema con el producto
descripcion_problema: ...
foto_1: IMG_1234.jpg (234567 bytes)  ← ✅ ESTO DEBE APARECER
descripcion_foto_1: Foto de la suela
=== Fin FormData ===
```

### PASO 3: Verificar Resultado

**SI APARECE `foto_1: archivo.jpg`**:
- ✅ Las fotos SÍ se están enviando al servidor
- Problema está en el backend o almacenamiento
- Ir a PASO 4

**SI NO APARECE `foto_1`**:
- ❌ Las fotos NO se están capturando
- Problema está en el frontend
- Verificar que seleccionaste archivo antes de guardar
- Verificar que el input file tiene name="foto_1"

### PASO 4: Verificar en Base de Datos

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind
.\venv\Scripts\activate
cd retailmind
python manage.py shell
```

```python
from app.models import Requerimiento, FotoRequerimiento

# Ver último requerimiento
req = Requerimiento.objects.last()
print(f"Requerimiento: {req.numero_requerimiento}")
print(f"Cantidad de fotos: {req.fotos.count()}")

# Si tiene fotos
if req.fotos.count() > 0:
    print("✅ LAS FOTOS SE GUARDARON EN LA BASE DE DATOS")
    for f in req.fotos.all():
        print(f"  Foto {f.orden}:")
        print(f"    - Archivo: {f.imagen.name}")
        print(f"    - URL: {f.imagen.url}")
        print(f"    - Existe: {f.imagen.storage.exists(f.imagen.name)}")
else:
    print("❌ NO HAY FOTOS EN LA BASE DE DATOS")
    print("Verificar que request.FILES tenga datos")
```

### PASO 5: Diagnóstico por Resultado

#### Caso A: Fotos en DB pero no se ven
```
Problema: Configuración de MEDIA_URL

Solución:
1. Abrir retailmind/settings.py
2. Verificar:
   MEDIA_URL = '/media/'
   MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

3. Abrir retailmind/urls.py
4. Verificar al final:
   from django.conf import settings
   from django.conf.urls.static import static
   
   if settings.DEBUG:
       urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

5. Reiniciar servidor
```

#### Caso B: Fotos NO en DB pero SÍ se envían
```
Problema: Backend no está procesando request.FILES

Solución:
1. Verificar permisos de carpeta media/
2. Crear carpetas si no existen:
   mkdir media\requerimientos\fotos

3. Verificar que el backend esté recibiendo FILES:
   # En views_modulo_requerimientos.py, agregar print:
   print("FILES recibidos:", request.FILES.keys())
```

#### Caso C: Fotos NO se envían (no aparece en console.log)
```
Problema: Input file no está dentro del form o no tiene archivo

Solución:
1. Verificar que el input esté dentro de <form id="form-requerimiento">
2. Verificar que seleccionaste un archivo antes de guardar
3. El preview debe aparecer antes de guardar
```

---

## 🔧 SOLUCIÓN TEMPORAL

Si las fotos aún no funcionan, puedes continuar usando el sistema:

**El vendedor puede**:
- Describir el problema en texto
- Mencionar "Ver fotos enviadas por email/WhatsApp"
- Sistema funciona igual sin fotos

**Para fotos urgentes**:
- Tómalas con el celular
- Envíalas por WhatsApp al admin
- Admin las puede adjuntar después

---

## 📊 VERIFICACIÓN DE CONFIGURACIÓN

### 1. settings.py
```python
# Buscar estas líneas:
import os
BASE_DIR = Path(__file__).resolve().parent.parent

# Debe tener:
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# O en versión pathlib:
MEDIA_ROOT = BASE_DIR / 'media'
```

### 2. Estructura de carpetas
```
retailmind/
├── manage.py
├── media/                    ← Debe existir
│   └── requerimientos/       ← Se crea automáticamente
│       └── fotos/
│           └── 2024/
│               └── 11/
│                   └── 17/
│                       └── foto.jpg
├── retailmind/
│   ├── settings.py
│   └── urls.py
└── app/
```

### 3. Crear carpeta media
```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
mkdir media
```

---

## 🎯 RESUMEN

### ✅ Implementado Hoy:
1. Debug completo de FormData
2. Columna "Sucursal" en tabla
3. Columna "Fotos" con contador
4. Console.log para diagnosticar
5. Placeholder si imagen falla

### 🔍 Para Diagnosticar:
1. Abre F12 → Console
2. Crea requerimiento con foto
3. Busca: `=== FormData Debug ===`
4. Verifica si aparece `foto_1: archivo.jpg`
5. Repórtame qué ves

### 📝 Información Necesaria:
- ¿Aparece `foto_1` en console.log?
- ¿Cuántas fotos hay en la base de datos? (comando arriba)
- ¿Qué dice `python manage.py shell` cuando verificas?

---

**Recarga, crea un requerimiento con foto, y dime qué ves en la consola** 🔍

