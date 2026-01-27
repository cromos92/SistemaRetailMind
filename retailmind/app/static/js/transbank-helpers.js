/**
 * Funciones auxiliares para integración Transbank en RetailMind
 */

// ==================== FUNCIONES DE CONEXIÓN ====================

/**
 * Auto-conectar al POS (solicita permisos si es necesario)
 * Prueba múltiples baudrates automáticamente
 */
async function autoconectarPOS() {
    try {
        showLoading('Conectando al POS...');
        
        // Intentar primero con baudrate por defecto
        let resultado = await Transbank.POS.autoConnect(false);
        
        // Si falla, probar todos los baudrates
        if (!resultado.success) {
            showLoading('Probando otras velocidades...');
            resultado = await Transbank.POS.autoConnect(true);
        }
        
        if (resultado.success) {
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
        showError('Error conectando al POS: ' + error.message);
        actualizarEstadoPOS(false);
        throw error;
    }
}

/**
 * Auto-conectar silencioso (sin solicitar permisos)
 */
async function autoconectarPOSPre() {
    try {
        const resultado = await Transbank.POS.autoConnect(false);
        if (resultado.success) {
            actualizarEstadoPOS(true, resultado.port, resultado.deviceType);
        }
        return resultado;
    } catch (error) {
        console.log('No se pudo auto-conectar:', error.message);
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
            showError(`Venta RECHAZADA<br>${resultado.responseMessage}`);
            return resultado;
        }
        
    } catch (error) {
        hideLoading();
        showError('Error procesando venta: ' + error.message);
        throw error;
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
    // Verificar soporte
    if (!verificarSoporteWebSerial()) {
        return;
    }
    
    // Intentar auto-conectar después de 1.5 segundos
    setTimeout(async function() {
        try {
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
