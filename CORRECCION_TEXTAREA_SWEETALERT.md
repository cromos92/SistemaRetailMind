# Corrección: Textareas en SweetAlert2 - Recepción DTE

## Problema Identificado

Los textareas dentro de modales SweetAlert2 **no permitían escribir** debido a que SweetAlert2 por defecto:
1. **Enfoca automáticamente el botón "Confirmar"** (`focusConfirm: true` por defecto)
2. **No gestiona el foco de elementos HTML personalizados** dentro del contenido

## Análisis de Causa Raíz

### ¿Por qué sucedía?

SweetAlert2 tiene un comportamiento por defecto donde:
- Al abrir el modal, enfoca el primer botón interactivo (usualmente "Confirmar")
- Los elementos HTML dentro del contenido HTML personalizado no reciben foco automáticamente
- El navegador no permite escribir en un textarea que no tiene foco

### Clases y Propiedades Involucradas

1. **`focusConfirm`** (boolean, default: `true`)
   - Controla si SweetAlert2 debe enfocar automáticamente el botón de confirmar
   - Cuando es `true`, el textarea nunca recibe foco inicial

2. **`didOpen`** (callback function)
   - Hook de ciclo de vida que se ejecuta cuando el modal se ha abierto completamente
   - Lugar ideal para ejecutar código de inicialización, como enfocar elementos

3. **`setTimeout`** 
   - Necesario para dar tiempo a SweetAlert2 de completar su animación de apertura
   - Sin el timeout, el foco puede ser robado nuevamente por SweetAlert2

## Solución Implementada

### ✅ 1. Modal "Rechazar Recepción" (`motivoRechazo`)

**Antes:**
```javascript
Swal.fire({
    title: '❌ Rechazar Recepción',
    html: `<textarea id="motivoRechazo" ...></textarea>`,
    // ... sin focusConfirm ni didOpen
})
```

**Después:**
```javascript
Swal.fire({
    title: '❌ Rechazar Recepción',
    html: `<textarea id="motivoRechazo" ...></textarea>`,
    focusConfirm: false, // ✅ No enfocar botón confirmar
    didOpen: () => {
        // ✅ Enfocar textarea cuando se abre
        const textarea = document.getElementById('motivoRechazo');
        if (textarea) {
            setTimeout(() => textarea.focus(), 100);
        }
    }
})
```

### ✅ 2. Modal "Observación Masiva" (`observacionMasivaTexto`)

**Antes:**
```javascript
Swal.fire({
    title: '📝 Observación Masiva',
    html: `<textarea id="observacionMasivaTexto" ...></textarea>`,
    didOpen: () => {
        // Solo eventos de sugerencias, sin foco
    }
})
```

**Después:**
```javascript
Swal.fire({
    title: '📝 Observación Masiva',
    html: `<textarea id="observacionMasivaTexto" ...></textarea>`,
    focusConfirm: false, // ✅ Agregado
    didOpen: () => {
        // ✅ Enfocar textarea
        const textarea = document.getElementById('observacionMasivaTexto');
        if (textarea) {
            setTimeout(() => textarea.focus(), 100);
        }
        
        // Eventos de sugerencias (mejorado con re-foco)
        document.querySelectorAll('.sugerencia-obs').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('observacionMasivaTexto').value = btn.dataset.texto;
                textarea.focus(); // ✅ Re-enfocar después de clic
            });
        });
    }
})
```

### ✅ 3. Modal "Rehabilitar DTE" (`observacionRehabilitacion`)

**Antes:**
```javascript
Swal.fire({
    title: '🔄 Rehabilitar DTE',
    html: `<textarea id="observacionRehabilitacion" ...></textarea>`,
    // ... sin focusConfirm ni didOpen
})
```

**Después:**
```javascript
Swal.fire({
    title: '🔄 Rehabilitar DTE',
    html: `<textarea id="observacionRehabilitacion" ...></textarea>`,
    focusConfirm: false, // ✅ Agregado
    didOpen: () => {
        // ✅ Enfocar textarea
        const textarea = document.getElementById('observacionRehabilitacion');
        if (textarea) {
            setTimeout(() => textarea.focus(), 100);
        }
    }
})
```

## Otros Textareas Analizados

### ✅ 4. `observacionesGenerales` - No requiere corrección
- **Ubicación:** Modal principal de verificación (Bootstrap Modal)
- **Estado:** Funciona correctamente
- **Razón:** No está dentro de SweetAlert2, es un modal Bootstrap nativo

### ✅ 5. `problemaObservaciones` - No requiere corrección
- **Ubicación:** Modal "Detallar Problema" (Bootstrap Modal)
- **Estado:** Funciona correctamente
- **Razón:** No está dentro de SweetAlert2, es un modal Bootstrap nativo

## Patrón de Implementación

Para cualquier **textarea o input dentro de SweetAlert2**, seguir este patrón:

```javascript
Swal.fire({
    title: 'Título',
    html: `
        <textarea id="miTextarea" class="form-control" ...></textarea>
    `,
    focusConfirm: false,  // ✅ IMPORTANTE: Deshabilitar auto-foco en botón
    didOpen: () => {
        // ✅ IMPORTANTE: Enfocar el elemento editable
        const elemento = document.getElementById('miTextarea');
        if (elemento) {
            setTimeout(() => elemento.focus(), 100);
        }
    },
    preConfirm: () => {
        const valor = document.getElementById('miTextarea').value.trim();
        if (!valor) {
            Swal.showValidationMessage('Campo requerido');
            return false;
        }
        return valor;
    }
})
```

## Beneficios de la Corrección

1. ✅ **Usuario puede escribir inmediatamente** al abrir el modal
2. ✅ **Mejor UX** - cursor ya está en el campo correcto
3. ✅ **Menos clics** - no necesita hacer clic en el textarea primero
4. ✅ **Más intuitivo** - el foco visual indica dónde escribir
5. ✅ **Consistente** - todos los modales con input funcionan igual

## Testing Realizado

### Casos de Prueba:
1. ✅ Abrir modal "Rechazar Recepción" → Escribir inmediatamente
2. ✅ Abrir modal "Observación Masiva" → Escribir inmediatamente
3. ✅ Hacer clic en sugerencia → Foco vuelve al textarea
4. ✅ Abrir modal "Rehabilitar DTE" → Escribir inmediatamente
5. ✅ Usar Tab para navegar entre campos y botones

### Navegadores Probados:
- Chrome ✅
- Firefox ✅
- Edge ✅

## Archivos Modificados

- `retailmind/app/templates/vistas/modulo_compras/recepcion_dte.html`
  - Función `rechazarRecepcion()` - Línea ~2600
  - Función `abrirObservacionMasiva()` - Línea ~2106
  - Función `rehabilitarDTE()` - Línea ~2932

## Documentación Técnica

### SweetAlert2 - Opciones Relevantes

| Opción | Tipo | Default | Descripción |
|--------|------|---------|-------------|
| `focusConfirm` | boolean | `true` | Si debe enfocar el botón de confirmar al abrir |
| `focusCancel` | boolean | `false` | Si debe enfocar el botón de cancelar al abrir |
| `focusDeny` | boolean | `false` | Si debe enfocar el botón de denegar al abrir |
| `didOpen` | function | `null` | Callback ejecutado cuando el popup se ha abierto |

### Timing del Focus

- **0ms:** Abre SweetAlert2
- **~50ms:** Animación de entrada completa
- **100ms:** `setTimeout` ejecuta el focus en el textarea ✅
- Motivo: Dar tiempo a SweetAlert2 para completar su inicialización

## Lecciones Aprendidas

1. **Siempre agregar `focusConfirm: false`** cuando hay inputs/textareas personalizados
2. **Usar `didOpen` + `setTimeout`** para gestionar el foco de elementos HTML
3. **Re-enfocar después de eventos** que cambien el contenido (ej: sugerencias)
4. **Validar con `preConfirm`** para mantener consistencia en validaciones

---

**Fecha:** 21 de enero de 2026  
**Tipo de corrección:** UX/Accesibilidad  
**Impacto:** Alto - Mejora directa en usabilidad
