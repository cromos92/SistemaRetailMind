/**
 * Funciones auxiliares para integración Transbank en RetailMind
 *
 * NOTA IMPORTANTE: las funciones se declaran al TOP-LEVEL del archivo,
 * NO dentro de un bloque if/else. En Chrome/Edge antiguos (presentes en
 * muchos PCs de tienda), `async function` declarada dentro de bloques
 * queda block-scoped aunque el archivo esté en modo sloppy, y los
 * `onclick="autoconectarPOS()"` de la página fallan con "not defined".
 * El guard de doble carga se aplica solo al listener de auto-conexión.
 */

// ==================== FUNCIONES DE CONEXIÓN ====================

/**
 * Auto-conectar al POS (solicita permisos si es necesario)
 * Prueba múltiples baudrates automáticamente
 * Si no hay puertos autorizados, abre el selector del navegador
 */
async function autoconectarPOS() {
    try {
        showLoading('Conectando al POS...');

        let resultado;

        // Verificar si hay puertos previamente autorizados
        const puertosDisponibles = await navigator.serial.getPorts();

        if (puertosDisponibles.length > 0) {
            // Hay puertos autorizados, intentar auto-conectar
            try {
                resultado = await Transbank.POS.autoConnect(false);
            } catch (e) {
                // Si falla con baudrate por defecto, probar todos
                showLoading('Probando otras velocidades...');
                try {
                    resultado = await Transbank.POS.autoConnect(true);
                } catch (e2) {
                    // Si falla con todos los baudrates, usar conexión manual
                    console.log('Auto-connect falló, abriendo selector manual...');
                    resultado = null;
                }
            }
        }

        // Si no hay puertos autorizados o auto-connect falló, abrir selector del navegador
        if (!resultado || !resultado.success) {
            showLoading('Seleccione el POS en la ventana del navegador...');
            resultado = await Transbank.POS.connect();
        }

        if (resultado && resultado.success) {
            // Guardar configuración en el backend
            await guardarConfiguracionPOS(resultado);

            hideLoading();

            const deviceInfo = resultado.deviceType || 'POS Transbank';
            const baudInfo = resultado.baudrate ? ` (${resultado.baudrate} bps)` : '';

            showSuccess(`${deviceInfo} conectado${baudInfo}`);
            actualizarEstadoPOS(true, resultado.port, resultado.deviceType);
            return resultado;
        } else {
            throw new Error('No se pudo conectar');
        }
    } catch (error) {
        hideLoading();
        // Diferenciar cancelación del usuario vs error real
        if (error.message && error.message.includes('No port selected')) {
            showInfo('Conexión cancelada por el usuario');
        } else {
            showError('Error conectando al POS: ' + error.message);
        }
        actualizarEstadoPOS(false);
        throw error;
    }
}

/**
 * Auto-conectar silencioso (sin solicitar permisos)
 * Solo intenta con puertos ya autorizados, nunca abre diálogo
 */
async function autoconectarPOSPre() {
    try {
        // Solo intentar si hay puertos previamente autorizados
        const puertos = await navigator.serial.getPorts();
        if (puertos.length === 0) {
            console.log('ℹ️ Sin puertos autorizados, esperando conexión manual');
            return { success: false, error: 'Sin puertos autorizados' };
        }

        const resultado = await Transbank.POS.autoConnect(false);
        if (resultado.success) {
            actualizarEstadoPOS(true, resultado.port, resultado.deviceType);
            console.log('✅ POS auto-conectado silenciosamente');
        }
        return resultado;
    } catch (error) {
        console.log('ℹ️ Auto-conexión silenciosa no disponible:', error.message);
        return { success: false, error: error.message };
    }
}

/**
 * Desconectar del POS
 */
async function liberarPuertoPOS() {
    try {
        await Transbank.POS.disconnect();
        actualizarEstadoPOS(false);
        showInfo('POS desconectado');
    } catch (error) {
        showError('Error desconectando: ' + error.message);
    }
}

// ==================== FUNCIONES DE VENTA ====================

/**
 * Ejecutar venta en el POS
 */
async function ejecutarVentaPOS(monto, ticket) {
    try {
        // Verificar conexión
        if (!Transbank.POS.isConnected) {
            throw new Error('POS no está conectado. Por favor conecte primero.');
        }

        showLoading(`Procesando pago de $${formatMoney(monto)}...`);
        
        // Ejecutar venta
        const resultado = await Transbank.POS.sale(monto, ticket);
        
        hideLoading();
        
        if (resultado.successful) {
            showSuccess(`Venta APROBADA<br>Autorización: ${resultado.authorizationCode}`);
            return resultado;
        } else {
            let html = `Venta RECHAZADA<br><strong>${resultado.responseMessage}</strong>`;
            if (resultado.hint) {
                html += `<br><br><small>${resultado.hint}</small>`;
            }
            // Sugerir acción concreta según el código de rechazo.
            // El código que realmente requiere cargar llaves es el 26 ("Debe Cargar Llaves"),
            // no el 70 (que es "Error de formato Campo Boleta MAX 6").
            if (resultado.responseCode === 26 && typeof cargarLlavesPOSConfirm === 'function') {
                html += `<br><br><button type="button" class="btn btn-warning btn-sm" onclick="Swal.close(); cargarLlavesPOSConfirm();">
                            <i class="ri-key-2-line"></i> Cargar llaves ahora
                         </button>`;
            }
            // Para códigos que requieren cierre de día: 13, 67, 74, 76, 77
            if ([13, 67, 74, 76, 77].includes(resultado.responseCode)) {
                html += `<br><small class="text-muted">Ejecute "Cierre de Día" en el POS y reintente.</small>`;
            }
            showError(html);
            return resultado;
        }
        
    } catch (error) {
        hideLoading();
        showError('Error procesando venta: ' + error.message);
        throw error;
    }
}

/**
 * Cargar llaves criptográficas en el POS (comando 0800)
 * Se usa cuando el POS devuelve código 70 (ERROR INICIALIZACIÓN).
 * Pide confirmación porque el proceso toma 30-60 segundos y requiere
 * presionar SÍ en el POS físico.
 */
async function cargarLlavesPOSConfirm() {
    try {
        if (!Transbank.POS.isConnected) {
            showError('POS no está conectado. Por favor conéctelo primero.');
            return;
        }

        const confirm = await Swal.fire({
            icon: 'warning',
            title: 'Cargar llaves criptográficas',
            html: 'Esto enviará el comando <code>0800</code> al POS.<br><br>' +
                  '<strong>Importante:</strong>' +
                  '<ul style="text-align:left">' +
                  '<li>El POS puede pedir confirmación — presione <strong>SÍ</strong> en el POS físico.</li>' +
                  '<li>El proceso dura 30-60 segundos.</li>' +
                  '<li>No apague el POS ni el computador durante la carga.</li>' +
                  '</ul>',
            showCancelButton: true,
            confirmButtonText: 'Cargar llaves',
            cancelButtonText: 'Cancelar',
            confirmButtonColor: '#f0a000'
        });
        if (!confirm.isConfirmed) return;

        showLoading('Cargando llaves en el POS... (30-60 seg)');
        const resultado = await Transbank.POS.loadKeys();
        hideLoading();

        if (resultado && resultado.success) {
            showSuccess(
                'Llaves cargadas correctamente.<br>' +
                `Código de comercio: ${resultado.commerceCode}<br>` +
                `Terminal: ${resultado.terminalId}<br><br>` +
                'Ya puede reintentar la venta.'
            );
        } else {
            showError(
                'No se pudieron cargar las llaves.<br>' +
                `Código de respuesta: ${resultado ? resultado.responseCode : 'desconocido'}<br><br>` +
                'Contacte a soporte Transbank si el problema persiste.'
            );
        }
    } catch (error) {
        hideLoading();
        showError('Error cargando llaves: ' + (error.message || error));
    }
}

/**
 * Guardar transacción en el backend
 */
async function guardarTransaccionPOS(resultado, ticketId) {
    try {
        const data = {
            amount: resultado.amount,
            ticket: resultado.ticket,
            ticket_id: ticketId,
            successful: resultado.successful,
            authorizationCode: resultado.authorizationCode,
            responseCode: resultado.responseCode,
            operationNumber: resultado.operationNumber,
            cardType: resultado.cardType,
            last4Digits: resultado.last4Digits,
            cardBrand: resultado.cardBrand,
            sharesNumber: resultado.sharesNumber,
            commerceCode: resultado.commerceCode,
            terminalId: resultado.terminalId,
            accountingDate: resultado.accountingDate,
            realDate: resultado.realDate,
            realTime: resultado.realTime
        };
        
        const response = await fetch('/app/pos/transbank/venta/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify(data)
        });
        
        const json = await response.json();
        
        if (json.success) {
            console.log('✅ Transacción guardada en BD:', json.transaccion_id);
            return json;
        } else {
            console.error('❌ Error guardando transacción:', json.error);
            throw new Error(json.error);
        }
        
    } catch (error) {
        console.error('❌ Error en guardarTransaccionPOS:', error);
        throw error;
    }
}

/**
 * Guardar configuración del POS en el backend
 */
async function guardarConfiguracionPOS(resultado) {
    try {
        const data = {
            port: resultado.port,
            baudrate: resultado.baudrate,
            descripcion: 'POS Transbank Auto-detectado'
        };
        
        const response = await fetch('/app/pos/transbank/autoconectar/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify(data)
        });
        
        const json = await response.json();
        
        if (json.success) {
            console.log('✅ Configuración guardada:', json.config_id);
        }
        
        return json;
        
    } catch (error) {
        console.error('❌ Error guardando configuración:', error);
    }
}

// ==================== FUNCIONES DE UI ====================

/**
 * Actualizar estado visual del POS
 */
function actualizarEstadoPOS(conectado, puerto = '', deviceType = '') {
    const estadoElement = document.getElementById('estado_pos');
    const puertoElement = document.getElementById('puerto');
    
    if (estadoElement) {
        if (conectado) {
            const tipo = deviceType ? ` (${deviceType})` : '';
            estadoElement.textContent = `Conectado${tipo}`;
            estadoElement.style.backgroundColor = '#00D4AA';
            estadoElement.style.color = 'white';
        } else {
            estadoElement.textContent = 'Desconectado';
            estadoElement.style.backgroundColor = '#FF4D4D';
            estadoElement.style.color = 'white';
        }
    }

    if (puertoElement) {
        puertoElement.textContent = puerto || '-';
    }
}

/**
 * Mostrar loading
 */
function showLoading(mensaje = 'Procesando...') {
    Swal.fire({
        title: mensaje,
        allowOutsideClick: false,
        didOpen: () => {
            Swal.showLoading();
        }
    });
}

/**
 * Ocultar loading
 */
function hideLoading() {
    Swal.close();
}

/**
 * Mostrar mensaje de éxito
 */
function showSuccess(mensaje) {
    Swal.fire({
        icon: 'success',
        title: '¡Éxito!',
        html: mensaje,
        timer: 3000,
        showConfirmButton: false
    });
}

/**
 * Mostrar mensaje de error
 */
function showError(mensaje) {
    Swal.fire({
        icon: 'error',
        title: 'Error',
        html: mensaje,
        confirmButtonText: 'Aceptar'
    });
}

/**
 * Mostrar mensaje informativo
 */
function showInfo(mensaje) {
    Swal.fire({
        icon: 'info',
        title: 'Información',
        text: mensaje,
        timer: 2000,
        showConfirmButton: false
    });
}

// ==================== FUNCIONES AUXILIARES ====================

/**
 * Obtener cookie CSRF
 */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

/**
 * Formatear dinero
 */
function formatMoney(amount) {
    return new Intl.NumberFormat('es-CL', {
        style: 'currency',
        currency: 'CLP'
    }).format(amount);
}

/**
 * Verificar si el navegador soporta Web Serial API
 */
function verificarSoporteWebSerial() {
    if (!('serial' in navigator)) {
        showError(
            'Tu navegador no soporta Web Serial API.<br>' +
            'Por favor usa Chrome o Edge versión 89 o superior.'
        );
        return false;
    }
    return true;
}

/**
 * Verificar configuración guardada
 */
function verificarConfiguracionPOS() {
    // Esta función se puede expandir para verificar localStorage
    // o hacer una consulta al backend
    return {
        configured: true,
        port: null
    };
}

// ==================== AUTO-CONEXIÓN AL CARGAR ====================

// Intentar auto-conectar al cargar la página
document.addEventListener('DOMContentLoaded', function() {
    // Evitar que múltiples listeners disparen auto-conexión simultánea
    if (window.__TBK_AUTOCONNECT_STARTED__) {
        console.log('ℹ️ Auto-conexión POS ya fue iniciada, se omite duplicado.');
        return;
    }
    window.__TBK_AUTOCONNECT_STARTED__ = true;

    // Verificar soporte
    if (!verificarSoporteWebSerial()) {
        return;
    }

    // Intentar auto-conectar después de 1.5 segundos
    setTimeout(async function() {
        try {
            // Si otra rutina ya conectó mientras esperábamos, no reintentar
            if (window.Transbank && Transbank.POS && Transbank.POS.isConnected) {
                console.log('✅ POS ya conectado, se omite auto-conexión inicial');
                return;
            }
            const resultado = await autoconectarPOSPre();
            if (resultado.success) {
                console.log('✅ POS auto-conectado');
            }
        } catch (e) {
            console.log('ℹ️ POS no auto-conectado (normal si es la primera vez)');
        }
    }, 1500);
});

console.log('✅ Funciones auxiliares Transbank cargadas');
