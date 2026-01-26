# 🔧 Script de Diagnóstico: Textarea Bloqueado en SweetAlert2

## 🎯 Objetivo
Diagnosticar POR QUÉ el textarea no permite escribir en el modal "Rechazar Recepción".

## 📋 Instrucciones

### 1. Limpiar Caché TOTALMENTE
```
1. Presionar Ctrl + Shift + Delete
2. Seleccionar:
   - Imágenes y archivos en caché
   - Cookies y otros datos del sitio
3. Rango de tiempo: "Desde siempre"
4. Limpiar datos
5. Cerrar el navegador COMPLETAMENTE
6. Volver a abrir
```

### 2. Abrir la Página y DevTools
```
1. Ir a http://localhost:8000/app/recepcion-dte/
2. Presionar F12 para abrir DevTools
3. Ir a la pestaña "Console"
```

### 3. Ejecutar Script de Diagnóstico

**Copiar y pegar este código en la Console ANTES de abrir el modal:**

```javascript
// Script de diagnóstico para textarea en SweetAlert2
window.diagnosticarTextarea = function() {
    console.log('🔍 ========== DIAGNÓSTICO TEXTAREA ==========');
    
    const textarea = document.getElementById('motivoRechazo');
    
    if (!textarea) {
        console.error('❌ PROBLEMA: No se encuentra el textarea #motivoRechazo');
        console.log('📋 Textareas disponibles:', document.querySelectorAll('textarea'));
        return;
    }
    
    console.log('✅ Textarea encontrado:', textarea);
    
    // 1. Atributos HTML
    console.log('\n📝 ATRIBUTOS HTML:');
    console.log('  - disabled:', textarea.disabled);
    console.log('  - readOnly:', textarea.readOnly);
    console.log('  - aria-disabled:', textarea.getAttribute('aria-disabled'));
    console.log('  - class:', textarea.className);
    console.log('  - id:', textarea.id);
    
    // 2. Estilos Computados CRÍTICOS
    const computed = window.getComputedStyle(textarea);
    console.log('\n🎨 ESTILOS COMPUTADOS:');
    console.log('  - pointer-events:', computed.pointerEvents);
    console.log('  - cursor:', computed.cursor);
    console.log('  - user-select:', computed.userSelect);
    console.log('  - opacity:', computed.opacity);
    console.log('  - display:', computed.display);
    console.log('  - visibility:', computed.visibility);
    console.log('  - z-index:', computed.zIndex);
    
    // 3. Estilos del contenedor SweetAlert2
    const swalContainer = textarea.closest('.swal2-container');
    const swalPopup = textarea.closest('.swal2-popup');
    
    if (swalContainer) {
        const containerStyles = window.getComputedStyle(swalContainer);
        console.log('\n📦 CONTENEDOR SWAL2:');
        console.log('  - pointer-events:', containerStyles.pointerEvents);
        console.log('  - z-index:', containerStyles.zIndex);
    }
    
    if (swalPopup) {
        const popupStyles = window.getComputedStyle(swalPopup);
        console.log('\n🔲 POPUP SWAL2:');
        console.log('  - pointer-events:', popupStyles.pointerEvents);
        console.log('  - z-index:', popupStyles.zIndex);
    }
    
    // 4. Verificar si hay overlays bloqueantes
    const overlays = document.querySelectorAll('.swal2-container::after, .swal2-container::before');
    console.log('\n🚧 OVERLAYS:', overlays.length);
    
    // 5. Verificar focus
    console.log('\n🎯 FOCUS:');
    console.log('  - Tiene focus:', document.activeElement === textarea);
    console.log('  - Elemento activo:', document.activeElement.tagName, document.activeElement.id);
    
    // 6. Intentar forzar escritura
    console.log('\n🧪 PRUEBA DE ESCRITURA:');
    try {
        textarea.value = 'TEST';
        console.log('  ✅ Se puede escribir programáticamente');
        console.log('  - Valor actual:', textarea.value);
        textarea.value = ''; // Limpiar
    } catch(e) {
        console.error('  ❌ ERROR al escribir:', e);
    }
    
    // 7. Event listeners
    console.log('\n👂 EVENT LISTENERS:');
    const eventTypes = ['click', 'focus', 'input', 'keydown', 'mousedown'];
    eventTypes.forEach(type => {
        const listeners = getEventListeners(textarea)[type];
        if (listeners && listeners.length > 0) {
            console.log(`  - ${type}:`, listeners.length);
        }
    });
    
    console.log('\n🔍 ========== FIN DIAGNÓSTICO ==========\n');
    
    // Resumen
    const problemas = [];
    if (textarea.disabled) problemas.push('disabled=true');
    if (textarea.readOnly) problemas.push('readOnly=true');
    if (computed.pointerEvents === 'none') problemas.push('pointer-events: none');
    if (computed.userSelect === 'none') problemas.push('user-select: none');
    if (computed.opacity === '0') problemas.push('opacity: 0');
    if (computed.visibility === 'hidden') problemas.push('visibility: hidden');
    
    if (problemas.length > 0) {
        console.error('⚠️ PROBLEMAS DETECTADOS:');
        problemas.forEach(p => console.error('  ❌', p));
    } else {
        console.log('✅ No se detectaron problemas obvios en el textarea');
    }
};

console.log('✅ Script de diagnóstico cargado. Abre el modal y ejecuta: diagnosticarTextarea()');
```

### 4. Abrir el Modal y Ejecutar Diagnóstico

1. **Seleccionar un DTE** en la lista
2. **Clic en "Rechazar Recepción"**
3. **En la Console, ejecutar:**
   ```javascript
   diagnosticarTextarea()
   ```

### 5. Capturar y Enviar Resultados

**Envía el output completo del diagnóstico.** Especialmente presta atención a:

- ❌ **PROBLEMAS DETECTADOS** (al final del output)
- 🎨 **ESTILOS COMPUTADOS** (especialmente `pointer-events`, `user-select`)
- 📦 **CONTENEDOR SWAL2** (si `pointer-events` es `none`)

---

## 🔧 Correcciones Alternativas Según Diagnóstico

### Si `pointer-events: none` en textarea
```css
.swal2-textarea {
    pointer-events: auto !important;
}
```

### Si `user-select: none` en textarea
```css
.swal2-textarea {
    user-select: text !important;
    -webkit-user-select: text !important;
}
```

### Si `disabled=true` o `readOnly=true`
El JavaScript ya lo está removiendo, pero si persiste:
```javascript
Object.defineProperty(textarea, 'disabled', { value: false, writable: false });
Object.defineProperty(textarea, 'readOnly', { value: false, writable: false });
```

### Si el contenedor tiene `pointer-events: none`
```css
.swal2-container {
    pointer-events: none !important;
}

.swal2-popup {
    pointer-events: auto !important;
}
```

---

## 🎯 Checklist de Verificación

Antes de ejecutar el diagnóstico:

- [ ] Caché limpiado completamente
- [ ] Navegador cerrado y reabierto
- [ ] Página recargada (Ctrl + F5)
- [ ] DevTools abierto en pestaña Console
- [ ] Script de diagnóstico ejecutado

Después del diagnóstico:

- [ ] Output capturado
- [ ] Problemas identificados
- [ ] Intento de escribir en el textarea (¿funciona?)
- [ ] Clic en el textarea (¿cambia el foco?)

---

## 💡 Soluciones Adicionales

### Opción 1: Usar Modal Bootstrap en lugar de SweetAlert2

Si SweetAlert2 sigue dando problemas, podemos cambiar a un modal Bootstrap:

```javascript
// En lugar de Swal.fire, usar modal Bootstrap
$('#modalRechazarRecepcion').modal('show');
```

### Opción 2: Usar API nativa de SweetAlert2 para inputs

```javascript
Swal.fire({
    title: '❌ Rechazar Recepción',
    input: 'textarea',
    inputLabel: 'Motivo del rechazo',
    inputPlaceholder: 'Ingresa el motivo...',
    inputAttributes: {
        'aria-label': 'Motivo del rechazo'
    },
    showCancelButton: true,
    inputValidator: (value) => {
        if (!value) {
            return 'Debes ingresar un motivo'
        }
    }
})
```

### Opción 3: Usar otra librería de alertas

**Opciones recomendadas:**
1. **Bootstrap Modal** (ya está incluido)
2. **Notiflix** (más ligero)
3. **iziToast** (simple)
4. **Bootbox.js** (sobre Bootstrap)

---

## 📞 Siguiente Paso

**Ejecuta el diagnóstico y envíame el output completo.** Con esa información podremos:
1. Identificar la causa EXACTA
2. Aplicar la solución CORRECTA
3. O cambiar a una alternativa que SÍ funcione

---

**Fecha:** 21 de enero de 2026  
**Tipo:** Diagnóstico avanzado  
**Objetivo:** Identificar bloqueo en textarea
