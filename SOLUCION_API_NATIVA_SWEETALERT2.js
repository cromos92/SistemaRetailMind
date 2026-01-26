// ============================================
// SOLUCIÓN ALTERNATIVA: Usar API nativa de SweetAlert2
// ============================================

// Reemplazar la función rechazarRecepcion() con esta versión:

function rechazarRecepcion() {
    if (!documentoSeleccionado) {
        Swal.fire('Sin documento', 'Selecciona un documento.', 'warning');
        return;
    }
    
    // ✅ VERSIÓN CON API NATIVA DE SWEETALERT2
    Swal.fire({
        title: '❌ Rechazar Recepción',
        html: `
            <div class="text-start mb-3">
                <p><strong>¿Estás seguro que deseas rechazar esta recepción?</strong></p>
                <div class="alert alert-warning">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    <strong>Atención:</strong> Esta acción implica:
                </div>
                <ul class="text-muted">
                    <li>El DTE <strong>#${documentoSeleccionado.numero_documento}</strong> será marcado como rechazado</li>
                    <li>El stock NO se incrementará en tu sucursal</li>
                    <li>Debes ingresar un motivo del rechazo</li>
                </ul>
            </div>
        `,
        // ✅ USAR API NATIVA DE INPUTS
        input: 'textarea',
        inputLabel: 'Motivo del rechazo (obligatorio)',
        inputPlaceholder: 'Describe el motivo por el cual rechazas esta recepción...',
        inputAttributes: {
            'aria-label': 'Motivo del rechazo',
            'style': 'min-height: 100px;'
        },
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: '<i class="bi bi-x-circle me-1"></i>Sí, rechazar',
        confirmButtonColor: '#dc3545',
        cancelButtonText: 'Cancelar',
        // ✅ VALIDACIÓN NATIVA
        inputValidator: (value) => {
            if (!value || value.trim() === '') {
                return '⚠️ Debes ingresar un motivo del rechazo'
            }
        },
        // Personalización adicional
        customClass: {
            input: 'form-control',
            validationMessage: 'alert alert-danger'
        }
    }).then(result => {
        if (!result.isConfirmed) return;
        
        const motivo = result.value.trim();
        procesarRechazo(motivo);
    });
}

// ============================================
// INSTRUCCIONES DE IMPLEMENTACIÓN
// ============================================

/*
1. LOCALIZAR la función rechazarRecepcion() actual en recepcion_dte.html
   (aproximadamente línea 2697)

2. REEMPLAZAR toda la función con la versión de arriba

3. GUARDAR el archivo

4. LIMPIAR caché: Ctrl + Shift + R

5. PROBAR:
   - Seleccionar un DTE
   - Clic en "Rechazar Recepción"
   - El textarea debe permitir escribir INMEDIATAMENTE
   - La validación debe funcionar al intentar enviar vacío
*/

// ============================================
// VENTAJAS DE ESTA SOLUCIÓN
// ============================================

/*
✅ Usa la API nativa de SweetAlert2 (input: 'textarea')
✅ SweetAlert2 gestiona automáticamente el foco
✅ Validación integrada con inputValidator
✅ No requiere manipulación manual del DOM
✅ Más confiable y predecible
✅ Menos conflictos con CSS
✅ Mantenible a largo plazo
*/

// ============================================
// APLICAR LO MISMO A OTRAS FUNCIONES
// ============================================

// Para abrirObservacionMasiva():
function abrirObservacionMasiva() {
    // ... código previo ...
    
    Swal.fire({
        title: '📝 Observación Masiva',
        html: `
            <div class="text-start mb-3">
                <!-- Tu HTML informativo aquí -->
            </div>
        `,
        input: 'textarea',
        inputLabel: 'Observación para productos desmarcados',
        inputPlaceholder: 'Describe el problema común...',
        inputAttributes: {
            'style': 'min-height: 100px;'
        },
        showCancelButton: true,
        confirmButtonText: 'Aplicar',
        inputValidator: (value) => {
            if (!value || value.trim() === '') {
                return '⚠️ Debes ingresar una observación'
            }
        }
    }).then(result => {
        if (result.isConfirmed) {
            const observacion = result.value.trim();
            // Aplicar a productos desmarcados
            productosVerificacion.forEach(prod => {
                if (!prod.marcado_ok) {
                    prod.observaciones = observacion;
                }
            });
            renderizarProductosVerificacion();
            actualizarResumenVerificacion();
        }
    });
}

// Para rehabilitarDTE():
function rehabilitarDTE(dteId, numeroDocumento) {
    Swal.fire({
        title: '🔄 Rehabilitar DTE',
        html: `
            <div class="text-start mb-3">
                <!-- Tu HTML informativo aquí -->
            </div>
        `,
        input: 'textarea',
        inputLabel: 'Observación (opcional)',
        inputPlaceholder: 'Ej: Se corrigió el problema...',
        inputAttributes: {
            'style': 'min-height: 80px;'
        },
        showCancelButton: true,
        confirmButtonText: '<i class="bi bi-check-circle me-1"></i>Sí, rehabilitar',
        confirmButtonColor: '#28a745'
    }).then(result => {
        if (result.isConfirmed) {
            const observaciones = result.value ? result.value.trim() : '';
            // Procesar rehabilitación
            // ...
        }
    });
}
