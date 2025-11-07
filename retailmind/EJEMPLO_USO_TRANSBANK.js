/**
 * EJEMPLO DE USO - SDK Transbank POS Integrado
 * Este archivo muestra ejemplos de cómo usar todos los métodos disponibles
 * Incluir este código en la vista HTML de gestión POS Transbank
 */

// =====================================================
// 1. INICIALIZACIÓN Y CONEXIÓN
// =====================================================

// Crear instancia global del POS
let posIntegration = null;

/**
 * Inicializar conexión con el agente POS
 */
async function inicializarPOS() {
    try {
        // Crear nueva instancia
        posIntegration = new TransbankPOSIntegration();
        
        // Mostrar mensaje de progreso
        mostrarMensaje('Conectando al agente POS...', 'info');
        
        // Inicializar
        const result = await posIntegration.initialize();
        
        if (result.success) {
            mostrarMensaje('✅ Conectado al agente POS', 'success');
            console.log('Puertos disponibles:', result.ports);
            
            // Actualizar UI con puertos disponibles
            actualizarListaPuertos(result.ports);
            
            return true;
        } else {
            mostrarMensaje('❌ ' + result.error, 'error');
            return false;
        }
    } catch (error) {
        mostrarMensaje('❌ Error: ' + error.message, 'error');
        return false;
    }
}

/**
 * Autoconectar al POS (busca automáticamente el puerto)
 */
async function autoConectarPOS() {
    try {
        if (!posIntegration) {
            await inicializarPOS();
        }
        
        mostrarMensaje('🔍 Buscando terminal POS...', 'info');
        
        const result = await posIntegration.autoconnect();
        
        if (result.success) {
            mostrarMensaje(`✅ POS detectado en ${result.port}`, 'success');
            document.getElementById('puerto-actual').textContent = result.port;
            return true;
        }
    } catch (error) {
        mostrarMensaje('❌ No se detectó ningún POS', 'error');
        return false;
    }
}

/**
 * Conectar a puerto específico
 */
async function conectarPuertoEspecifico(puerto) {
    try {
        if (!posIntegration) {
            await inicializarPOS();
        }
        
        mostrarMensaje(`Conectando al puerto ${puerto}...`, 'info');
        
        await posIntegration.openPort(puerto, 115200);
        
        // Verificar con poll
        await posIntegration.poll();
        
        mostrarMensaje(`✅ Conectado al puerto ${puerto}`, 'success');
        document.getElementById('puerto-actual').textContent = puerto;
        
        return true;
    } catch (error) {
        mostrarMensaje(`❌ Error en puerto ${puerto}: ${error.message}`, 'error');
        return false;
    }
}

/**
 * Desconectar del POS
 */
async function desconectarPOS() {
    try {
        if (posIntegration) {
            await posIntegration.disconnect();
            mostrarMensaje('🔌 Desconectado del POS', 'info');
            document.getElementById('puerto-actual').textContent = '-';
        }
    } catch (error) {
        console.error('Error al desconectar:', error);
    }
}

// =====================================================
// 2. OPERACIONES DE VENTA
// =====================================================

/**
 * Realizar venta simple
 */
async function realizarVenta(monto, ticketId) {
    try {
        // Validar conexión
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return null;
        }
        
        // Validar monto
        if (!monto || monto <= 0) {
            mostrarMensaje('❌ Monto inválido', 'error');
            return null;
        }
        
        // Mostrar modal de espera
        mostrarModalProcesando('Procesando venta...');
        
        // Callback para estados intermedios
        function actualizarEstadoVenta(estado) {
            console.log('📊 Estado intermedio:', estado);
            actualizarModalProcesando(estado.mensaje || 'Procesando...');
        }
        
        // Ejecutar venta
        const resultado = await posIntegration.doSale(
            monto,
            ticketId,
            actualizarEstadoVenta
        );
        
        // Cerrar modal de procesando
        cerrarModalProcesando();
        
        // Procesar resultado
        if (resultado.success) {
            // Venta aprobada
            mostrarModalVentaAprobada(resultado);
            
            // Guardar en base de datos
            await guardarTransaccionBD(resultado, ticketId, monto);
            
            return resultado;
        } else {
            // Venta rechazada
            mostrarModalVentaRechazada(resultado);
            return null;
        }
        
    } catch (error) {
        cerrarModalProcesando();
        mostrarMensaje('❌ Error en venta: ' + error.message, 'error');
        return null;
    }
}

/**
 * Realizar venta con código de comercio específico
 */
async function realizarVentaMulticode(monto, ticketId, codigoComercio) {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return null;
        }
        
        mostrarModalProcesando('Procesando venta multicode...');
        
        const resultado = await posIntegration.doMulticodeSale(
            monto,
            ticketId,
            codigoComercio,
            (estado) => actualizarModalProcesando(estado.mensaje)
        );
        
        cerrarModalProcesando();
        
        if (resultado.success) {
            mostrarModalVentaAprobada(resultado);
            await guardarTransaccionBD(resultado, ticketId, monto);
            return resultado;
        } else {
            mostrarModalVentaRechazada(resultado);
            return null;
        }
        
    } catch (error) {
        cerrarModalProcesando();
        mostrarMensaje('❌ Error: ' + error.message, 'error');
        return null;
    }
}

/**
 * Anular transacción
 */
async function anularTransaccion(numeroOperacion) {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return false;
        }
        
        // Confirmar con el usuario
        if (!confirm(`¿Está seguro de anular la operación ${numeroOperacion}?`)) {
            return false;
        }
        
        mostrarModalProcesando('Procesando anulación...');
        
        const resultado = await posIntegration.doRefund(numeroOperacion);
        
        cerrarModalProcesando();
        
        if (resultado.success) {
            mostrarMensaje('✅ Transacción anulada exitosamente', 'success');
            
            // Actualizar en BD
            await actualizarAnulacionBD(numeroOperacion);
            
            return true;
        } else {
            mostrarMensaje('❌ No se pudo anular: ' + resultado.response_message, 'error');
            return false;
        }
        
    } catch (error) {
        cerrarModalProcesando();
        mostrarMensaje('❌ Error en anulación: ' + error.message, 'error');
        return false;
    }
}

// =====================================================
// 3. CONSULTAS E INFORMACIÓN
// =====================================================

/**
 * Obtener última venta
 */
async function obtenerUltimaVenta() {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return null;
        }
        
        const resultado = await posIntegration.getLastSale();
        
        if (resultado.success) {
            mostrarDetalleVenta(resultado);
            return resultado;
        } else {
            mostrarMensaje('❌ No se pudo obtener la última venta', 'error');
            return null;
        }
        
    } catch (error) {
        mostrarMensaje('❌ Error: ' + error.message, 'error');
        return null;
    }
}

/**
 * Obtener totales del día
 */
async function obtenerTotalesDia() {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return null;
        }
        
        mostrarMensaje('📊 Consultando totales...', 'info');
        
        const totales = await posIntegration.getTotals();
        
        // Mostrar en UI
        mostrarTotalesDia(totales);
        
        return totales;
        
    } catch (error) {
        mostrarMensaje('❌ Error obteniendo totales: ' + error.message, 'error');
        return null;
    }
}

/**
 * Obtener detalles de ventas
 */
async function obtenerDetallesVentas(imprimirEnPOS = false) {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return null;
        }
        
        const mensaje = imprimirEnPOS ? 
            'Obteniendo detalles e imprimiendo...' : 
            'Obteniendo detalles...';
        mostrarMensaje(mensaje, 'info');
        
        const detalles = await posIntegration.getDetails(imprimirEnPOS);
        
        mostrarDetallesVentas(detalles);
        
        return detalles;
        
    } catch (error) {
        mostrarMensaje('❌ Error: ' + error.message, 'error');
        return null;
    }
}

/**
 * Verificar estado del terminal
 */
async function verificarEstadoTerminal() {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return false;
        }
        
        const status = await posIntegration.poll();
        mostrarMensaje('✅ Terminal operativo', 'success');
        return true;
        
    } catch (error) {
        mostrarMensaje('❌ Terminal no responde', 'error');
        return false;
    }
}

// =====================================================
// 4. OPERACIONES ESPECIALES
// =====================================================

/**
 * Cargar llaves criptográficas
 */
async function cargarLlaves() {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return false;
        }
        
        // Confirmar operación
        if (!confirm('¿Desea cargar las llaves criptográficas en el POS?')) {
            return false;
        }
        
        mostrarModalProcesando('Cargando llaves...');
        
        const resultado = await posIntegration.loadKeys();
        
        cerrarModalProcesando();
        
        if (resultado.success) {
            mostrarMensaje('✅ Llaves cargadas exitosamente', 'success');
            return true;
        } else {
            mostrarMensaje('❌ Error cargando llaves', 'error');
            return false;
        }
        
    } catch (error) {
        cerrarModalProcesando();
        mostrarMensaje('❌ Error: ' + error.message, 'error');
        return false;
    }
}

/**
 * Realizar cierre de día
 */
async function realizarCierreDia() {
    try {
        if (!posIntegration || !posIntegration.isConnected) {
            mostrarMensaje('❌ Debe conectar el POS primero', 'error');
            return false;
        }
        
        // Confirmar operación
        if (!confirm('¿Desea realizar el cierre de día?\n\nEsto imprimirá el cierre y resetea los contadores del POS.')) {
            return false;
        }
        
        mostrarModalProcesando('Realizando cierre de día...');
        
        const resultado = await posIntegration.closeDay();
        
        cerrarModalProcesando();
        
        if (resultado.success) {
            mostrarModalCierreDia(resultado.data);
            return true;
        } else {
            mostrarMensaje('❌ Error en cierre de día', 'error');
            return false;
        }
        
    } catch (error) {
        cerrarModalProcesando();
        mostrarMensaje('❌ Error: ' + error.message, 'error');
        return false;
    }
}

// =====================================================
// 5. FUNCIONES DE UI (PARA PERSONALIZAR)
// =====================================================

function mostrarMensaje(mensaje, tipo = 'info') {
    console.log(`[${tipo.toUpperCase()}] ${mensaje}`);
    // Implementar notificación visual (toast, alert, etc.)
    // Ejemplo con Bootstrap Toast o SweetAlert2
}

function actualizarListaPuertos(puertos) {
    const select = document.getElementById('select-puerto');
    if (select) {
        select.innerHTML = '<option value="">Seleccione puerto...</option>';
        puertos.forEach(puerto => {
            select.innerHTML += `<option value="${puerto}">${puerto}</option>`;
        });
    }
}

function mostrarModalProcesando(mensaje) {
    // Implementar modal de carga
    console.log('🔄', mensaje);
}

function actualizarModalProcesando(mensaje) {
    console.log('🔄', mensaje);
}

function cerrarModalProcesando() {
    console.log('✓ Procesamiento completado');
}

function mostrarModalVentaAprobada(resultado) {
    console.log('✅ VENTA APROBADA', resultado);
    // Implementar modal con detalles de venta aprobada
}

function mostrarModalVentaRechazada(resultado) {
    console.log('❌ VENTA RECHAZADA', resultado);
    // Implementar modal con motivo de rechazo
}

function mostrarDetalleVenta(venta) {
    console.log('📄 Detalle de venta:', venta);
    // Implementar visualización de detalle
}

function mostrarTotalesDia(totales) {
    console.log('📊 Totales del día:', totales);
    // Actualizar UI con totales
}

function mostrarDetallesVentas(detalles) {
    console.log('📋 Detalles de ventas:', detalles);
    // Mostrar tabla o lista de ventas
}

function mostrarModalCierreDia(datos) {
    console.log('📊 Cierre de día:', datos);
    // Implementar modal con resumen del cierre
}

// =====================================================
// 6. INTEGRACIÓN CON DJANGO
// =====================================================

async function guardarTransaccionBD(resultado, ticketId, monto) {
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    
    try {
        const response = await fetch('/pos/guardar-transaccion/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                ticket_pos: resultado.operation_number,
                monto: monto,
                codigo_autorizacion: resultado.authorization_code,
                tipo_tarjeta: resultado.card_type,
                marca_tarjeta: resultado.card_brand,
                ultimos_digitos: resultado.last_4_digits,
                codigo_respuesta: resultado.response_code,
                mensaje_respuesta: resultado.response_message,
                codigo_comercio: resultado.commerce_code,
                terminal_id: resultado.terminal_id,
                cuotas: resultado.installments,
                estado: 'APROBADA'
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('✅ Transacción guardada en BD');
            return data;
        } else {
            throw new Error(data.error);
        }
        
    } catch (error) {
        console.error('❌ Error guardando transacción:', error);
        throw error;
    }
}

async function actualizarAnulacionBD(numeroOperacion) {
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    
    try {
        const response = await fetch('/pos/anular-transaccion/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                numero_operacion: numeroOperacion
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            console.log('✅ Anulación actualizada en BD');
            return data;
        }
        
    } catch (error) {
        console.error('❌ Error actualizando anulación:', error);
    }
}

// =====================================================
// 7. EVENT LISTENERS Y INICIALIZACIÓN
// =====================================================

// Inicializar cuando el DOM esté listo
document.addEventListener('DOMContentLoaded', function() {
    console.log('🚀 SDK Transbank POS cargado');
    
    // Botón inicializar
    const btnInicializar = document.getElementById('btn-inicializar-pos');
    if (btnInicializar) {
        btnInicializar.addEventListener('click', inicializarPOS);
    }
    
    // Botón autoconectar
    const btnAutoconectar = document.getElementById('btn-autoconectar');
    if (btnAutoconectar) {
        btnAutoconectar.addEventListener('click', autoConectarPOS);
    }
    
    // Botón desconectar
    const btnDesconectar = document.getElementById('btn-desconectar');
    if (btnDesconectar) {
        btnDesconectar.addEventListener('click', desconectarPOS);
    }
    
    // Botón verificar terminal
    const btnVerificar = document.getElementById('btn-verificar-terminal');
    if (btnVerificar) {
        btnVerificar.addEventListener('click', verificarEstadoTerminal);
    }
    
    // Botón obtener totales
    const btnTotales = document.getElementById('btn-obtener-totales');
    if (btnTotales) {
        btnTotales.addEventListener('click', obtenerTotalesDia);
    }
    
    // Botón cierre de día
    const btnCierre = document.getElementById('btn-cierre-dia');
    if (btnCierre) {
        btnCierre.addEventListener('click', realizarCierreDia);
    }
    
    // Botón cargar llaves
    const btnLlaves = document.getElementById('btn-cargar-llaves');
    if (btnLlaves) {
        btnLlaves.addEventListener('click', cargarLlaves);
    }
});

// Limpiar al salir de la página
window.addEventListener('beforeunload', function() {
    if (posIntegration) {
        posIntegration.disconnect();
    }
});

// =====================================================
// 8. EXPORTAR FUNCIONES GLOBALES
// =====================================================

// Hacer funciones accesibles globalmente
window.TransbankPOSManager = {
    inicializar: inicializarPOS,
    autoconectar: autoConectarPOS,
    conectarPuerto: conectarPuertoEspecifico,
    desconectar: desconectarPOS,
    realizarVenta: realizarVenta,
    realizarVentaMulticode: realizarVentaMulticode,
    anularTransaccion: anularTransaccion,
    obtenerUltimaVenta: obtenerUltimaVenta,
    obtenerTotales: obtenerTotalesDia,
    obtenerDetalles: obtenerDetallesVentas,
    verificarTerminal: verificarEstadoTerminal,
    cargarLlaves: cargarLlaves,
    cierreDia: realizarCierreDia
};

console.log('✅ TransbankPOSManager disponible globalmente');

