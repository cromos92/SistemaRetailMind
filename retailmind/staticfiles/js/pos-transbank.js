/**
 * POS Transbank Integration - JavaScript SDK
 * Integración con SDK Web de Transbank para terminales POS
 * Usa la librería oficial de Transbank (transbank-pos-sdk.js)
 */

class TransbankPOSIntegration {
    constructor() {
        this.isConnected = false;
        this.currentPort = null;
        this.agentUrl = 'https://localhost:8090';
        this.currentTransaction = null;
        this.callbacks = {};
        this.sdk = null; // Instancia del SDK oficial
    }

    /**
     * Inicializar conexión con el agente POS
     */
    async initialize() {
        try {
            // Verificar si Transbank SDK está disponible
            if (typeof Transbank === 'undefined') {
                throw new Error('SDK de Transbank no está cargado. Asegúrese de incluir transbank-pos-sdk.js');
            }

            // Crear instancia del SDK oficial
            this.sdk = Transbank.POS;

            // Conectar al agente POS
            await this.sdk.connect(this.agentUrl);
            console.log('✅ Conectado al agente POS Transbank');
            this.isConnected = true;

            // Obtener puertos disponibles
            const ports = await this.sdk.getPorts();
            console.log('🔌 Puertos disponibles:', ports);

            return {
                success: true,
                ports: ports,
                message: 'Conectado exitosamente al agente POS'
            };

        } catch (error) {
            console.error('❌ Error conectando al agente POS:', error);
            this.isConnected = false;
            
            return {
                success: false,
                error: error.message || error,
                suggestion: this.getSuggestionForError(error.message || error.toString())
            };
        }
    }

    /**
     * Abrir puerto específico
     */
    async openPort(portName, baudRate = 115200) {
        if (!this.isConnected || !this.sdk) {
            throw new Error('No hay conexión con el agente POS');
        }

        try {
            await this.sdk.openPort(portName, baudRate);
            this.currentPort = portName;
            console.log(`🔌 Puerto abierto: ${portName} @ ${baudRate} baud`);
            return true;
        } catch (error) {
            console.error(`❌ Error abriendo puerto ${portName}:`, error);
            throw error;
        }
    }

    /**
     * Cerrar puerto actual
     */
    async closePort() {
        if (this.currentPort && this.sdk) {
            try {
                await this.sdk.closePort();
                console.log(`🔌 Puerto cerrado: ${this.currentPort}`);
                this.currentPort = null;
            } catch (error) {
                console.error('Error cerrando puerto:', error);
            }
        }
    }

    /**
     * Cargar llaves criptográficas en el POS
     */
    async loadKeys() {
        if (!this.isConnected || !this.sdk) {
            throw new Error('POS no conectado');
        }

        try {
            console.log('🔑 Cargando llaves en el POS...');
            const result = await this.sdk.loadKeys();
            console.log('✅ Llaves cargadas exitosamente');
            return {
                success: true,
                message: 'Llaves cargadas correctamente',
                data: result
            };
        } catch (error) {
            console.error('❌ Error cargando llaves:', error);
            throw error;
        }
    }

    /**
     * Realizar cierre de día
     */
    async closeDay() {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            console.log('📊 Iniciando cierre de día...');
            const result = await this.sdk.closeDay();
            console.log('✅ Cierre de día completado:', result);
            return {
                success: true,
                message: 'Cierre de día completado',
                data: result
            };
        } catch (error) {
            console.error('❌ Error en cierre de día:', error);
            throw error;
        }
    }

    /**
     * Poll - Verificar estado del terminal
     */
    async poll() {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            const result = await this.sdk.poll();
            console.log('📡 Poll exitoso:', result);
            return {
                success: true,
                data: result
            };
        } catch (error) {
            console.error('❌ Error en poll:', error);
            throw error;
        }
    }

    /**
     * Obtener estado del puerto
     */
    async getPortStatus() {
        if (!this.sdk) {
            throw new Error('SDK no inicializado');
        }

        try {
            const status = await this.sdk.getPortStatus();
            return {
                success: true,
                connected: this.isConnected,
                currentPort: this.currentPort,
                agentStatus: status
            };
        } catch (error) {
            return {
                success: false,
                connected: this.isConnected,
                currentPort: this.currentPort,
                error: error.message
            };
        }
    }

    /**
     * Cambiar a modo normal
     */
    async setNormalMode() {
        if (!this.isConnected || !this.sdk) {
            throw new Error('POS no conectado');
        }

        try {
            const result = await this.sdk.setNormalMode();
            console.log('✅ Modo normal activado');
            return {
                success: true,
                data: result
            };
        } catch (error) {
            console.error('❌ Error cambiando a modo normal:', error);
            throw error;
        }
    }

    /**
     * Realizar venta
     */
    async doSale(amount, ticketId, onStatusUpdate = null) {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            console.log(`💳 Iniciando venta: $${amount} - Ticket: ${ticketId}`);
            
            // Callback para estados intermedios
            const statusCallback = (intermediateResponse) => {
                console.log('📊 Estado intermedio:', intermediateResponse);
                if (onStatusUpdate) {
                    onStatusUpdate(intermediateResponse);
                }
                this.updateTransactionUI(intermediateResponse);
            };

            // Ejecutar venta con SDK oficial
            const result = await this.sdk.doSale(
                Math.round(amount * 100), // Convertir a centavos
                ticketId.toString(),
                statusCallback
            );

            console.log('✅ Resultado de venta:', result);
            return this.processTransactionResult(result);

        } catch (error) {
            console.error('❌ Error en venta:', error);
            throw this.processTransactionError(error);
        }
    }

    /**
     * Realizar venta multi-código de comercio
     */
    async doMulticodeSale(amount, ticketId, commerceCode = "0", onStatusUpdate = null) {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            console.log(`💳 Iniciando venta multicode: $${amount} - Ticket: ${ticketId} - Commerce: ${commerceCode}`);
            
            // Callback para estados intermedios
            const statusCallback = (intermediateResponse) => {
                console.log('📊 Estado intermedio (multicode):', intermediateResponse);
                if (onStatusUpdate) {
                    onStatusUpdate(intermediateResponse);
                }
                this.updateTransactionUI(intermediateResponse);
            };

            // Ejecutar venta multicode con SDK oficial
            const result = await this.sdk.doMulticodeSale(
                Math.round(amount * 100), // Convertir a centavos
                ticketId.toString(),
                commerceCode,
                statusCallback
            );

            console.log('✅ Resultado de venta multicode:', result);
            return this.processTransactionResult(result);

        } catch (error) {
            console.error('❌ Error en venta multicode:', error);
            throw this.processTransactionError(error);
        }
    }

    /**
     * Anular transacción por ID de operación
     */
    async doRefund(operationId) {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            console.log(`🔄 Iniciando anulación de operación: ${operationId}`);
            const result = await this.sdk.refund(operationId);
            console.log('✅ Resultado de anulación:', result);
            return this.processTransactionResult(result);
        } catch (error) {
            console.error('❌ Error en anulación:', error);
            throw this.processTransactionError(error);
        }
    }

    /**
     * Obtener última venta
     */
    async getLastSale() {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            console.log('📄 Obteniendo última venta...');
            const result = await this.sdk.getLastSale();
            console.log('✅ Última venta obtenida:', result);
            return this.processTransactionResult(result);
        } catch (error) {
            console.error('❌ Error obteniendo última venta:', error);
            throw this.processTransactionError(error);
        }
    }

    /**
     * Obtener totales del día
     */
    async getTotals() {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            console.log('📊 Obteniendo totales del día...');
            const result = await this.sdk.getTotals();
            console.log('✅ Totales obtenidos:', result);
            return result;
        } catch (error) {
            console.error('❌ Error obteniendo totales:', error);
            throw error;
        }
    }

    /**
     * Obtener detalles de ventas
     */
    async getDetails(printOnPos = false) {
        if (!this.isConnected || !this.currentPort || !this.sdk) {
            throw new Error('POS no conectado o puerto no abierto');
        }

        try {
            console.log('📋 Obteniendo detalles de ventas...');
            const result = await this.sdk.getDetails(printOnPos);
            console.log('✅ Detalles obtenidos:', result);
            return result;
        } catch (error) {
            console.error('❌ Error obteniendo detalles:', error);
            throw error;
        }
    }

    /**
     * Probar conexión con terminal
     */
    async testConnection(portName) {
        try {
            await this.openPort(portName);
            
            // Intentar obtener totales como prueba de conexión
            const totals = await this.getTotals();
            
            return {
                success: true,
                message: 'Conexión exitosa con el terminal',
                data: totals
            };
        } catch (error) {
            return {
                success: false,
                error: error.message
            };
        }
    }

    /**
     * Procesar resultado de transacción
     */
    processTransactionResult(result) {
        const processed = {
            success: result.response_code === '00',
            response_code: result.response_code,
            response_message: result.response_message || this.getResponseMessage(result.response_code),
            authorization_code: result.authorization_code,
            card_type: this.determineCardType(result),
            card_brand: result.card_brand || 'DESCONOCIDO',
            card_number: result.card_number,
            operation_number: result.operation_number,
            installments: result.installments || 1,
            commerce_code: result.commerce_code,
            terminal_id: result.terminal_id,
            amount: result.amount ? result.amount / 100 : 0, // Convertir de centavos
            timestamp: new Date().toISOString()
        };

        // Agregar información adicional según el tipo de respuesta
        if (result.card_number) {
            processed.last_4_digits = result.card_number.slice(-4);
        }

        return processed;
    }

    /**
     * Procesar error de transacción
     */
    processTransactionError(error) {
        const processedError = new Error();
        processedError.name = 'TransactionError';
        processedError.message = error.message || 'Error desconocido en transacción';
        processedError.code = error.code || 'UNKNOWN_ERROR';
        processedError.suggestion = this.getSuggestionForError(error.message);
        processedError.timestamp = new Date().toISOString();

        return processedError;
    }

    /**
     * Determinar tipo de tarjeta
     */
    determineCardType(result) {
        if (result.card_type) {
            return result.card_type.toUpperCase();
        }

        // Intentar determinar por otros campos
        if (result.card_brand) {
            const brand = result.card_brand.toUpperCase();
            if (['VISA', 'MASTERCARD', 'AMEX'].includes(brand)) {
                return 'CREDITO';
            }
            if (brand === 'MAESTRO') {
                return 'DEBITO';
            }
        }

        return 'DESCONOCIDO';
    }

    /**
     * Obtener mensaje de respuesta por código
     */
    getResponseMessage(code) {
        const messages = {
            '00': 'Transacción aprobada',
            '01': 'Transacción debe ser autorizada por el emisor',
            '02': 'Transacción debe ser autorizada por el emisor',
            '03': 'Comercio inválido',
            '04': 'Retener tarjeta',
            '05': 'Transacción rechazada',
            '06': 'Error general',
            '07': 'Retener tarjeta - condiciones especiales',
            '08': 'Transacción aprobada - identificar al portador',
            '09': 'Solicitud en proceso',
            '10': 'Monto parcialmente aprobado',
            '11': 'Transacción aprobada VIP',
            '12': 'Transacción inválida',
            '13': 'Monto inválido',
            '14': 'Número de tarjeta inválido',
            '15': 'Emisor no existe',
            '19': 'Reingresar transacción',
            '21': 'Transacción no reversada',
            '25': 'No se pudo localizar el registro en el archivo',
            '30': 'Error en formato del mensaje',
            '41': 'Tarjeta perdida',
            '43': 'Tarjeta robada',
            '51': 'Fondos insuficientes',
            '54': 'Tarjeta vencida',
            '55': 'Clave incorrecta',
            '56': 'Tarjeta no válida',
            '57': 'Transacción no permitida al portador',
            '58': 'Transacción no permitida en el terminal',
            '61': 'Monto límite excedido',
            '62': 'Tarjeta restringida',
            '65': 'Límite de actividad excedido',
            '75': 'Número de intentos de clave excedido',
            '76': 'Cuenta bloqueada',
            '77': 'Cuenta cancelada',
            '78': 'Cuenta nueva - no activada',
            '81': 'Falla criptográfica',
            '82': 'CVV incorrecto',
            '83': 'No es posible verificar el PIN',
            '85': 'Transacción rechazada - clave correcta',
            '89': 'Error en autenticación',
            '91': 'Emisor no disponible',
            '92': 'Tipo de transacción inválida para el emisor',
            '94': 'Transacción duplicada',
            '96': 'Error en el sistema'
        };

        return messages[code] || `Código de respuesta: ${code}`;
    }

    /**
     * Obtener sugerencia para error
     */
    getSuggestionForError(errorMessage) {
        const suggestions = {
            'connection': 'Verifique que el agente POS esté ejecutándose y el terminal esté conectado',
            'port': 'Verifique el puerto de conexión y que no esté siendo usado por otra aplicación',
            'timeout': 'La operación tardó demasiado. Verifique la conexión del terminal',
            'cancelled': 'La operación fue cancelada por el usuario',
            'invalid_amount': 'El monto debe ser mayor a 0 y no exceder los límites permitidos',
            'card_error': 'Problema con la tarjeta. Intente con otra tarjeta o método de pago',
            'terminal_error': 'Error en el terminal. Verifique la conexión y configuración'
        };

        const message = errorMessage.toLowerCase();
        
        for (const [key, suggestion] of Object.entries(suggestions)) {
            if (message.includes(key)) {
                return suggestion;
            }
        }

        return 'Contacte al soporte técnico si el problema persiste';
    }

    /**
     * Actualizar UI durante transacción
     */
    updateTransactionUI(response) {
        // Esta función puede ser sobrescrita para personalizar la UI
        console.log('🔄 Actualizando UI:', response);
        
        // Emitir evento personalizado para que la aplicación pueda escuchar
        const event = new CustomEvent('posTransactionUpdate', {
            detail: response
        });
        document.dispatchEvent(event);
    }

    /**
     * Desconectar del agente POS
     */
    async disconnect() {
        try {
            await this.closePort();
            
            if (this.isConnected && this.sdk) {
                await this.sdk.disconnect();
                this.isConnected = false;
                this.sdk = null;
                console.log('🔌 Desconectado del agente POS');
            }
        } catch (error) {
            console.error('Error al desconectar:', error);
        }
    }

    /**
     * Autoconectar - Buscar y conectar automáticamente al POS
     */
    async autoconnect(baudRate = 115200) {
        if (!this.sdk) {
            throw new Error('SDK no inicializado');
        }

        try {
            console.log(`🔍 Buscando POS automáticamente...`);
            const result = await this.sdk.autoconnect(baudRate);
            
            if (result && result.port) {
                this.currentPort = result.port;
                console.log(`✅ POS detectado automáticamente en: ${result.port}`);
                return {
                    success: true,
                    port: result.port,
                    message: `POS conectado automáticamente en ${result.port}`,
                    data: result
                };
            } else {
                throw new Error('No se pudo detectar ningún POS');
            }
        } catch (error) {
            console.error('❌ Error en autoconexión:', error);
            throw error;
        }
    }
}

// Clase para gestión de múltiples terminales
class POSManager {
    constructor() {
        this.terminals = new Map();
        this.activeTerminal = null;
        this.sdk = new TransbankPOSIntegration();
    }

    /**
     * Inicializar manager
     */
    async initialize() {
        try {
            const result = await this.sdk.initialize();
            if (result.success) {
                console.log('✅ POS Manager inicializado');
                return result;
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            console.error('❌ Error inicializando POS Manager:', error);
            throw error;
        }
    }

    /**
     * Registrar terminal
     */
    registerTerminal(config) {
        this.terminals.set(config.id, {
            id: config.id,
            name: config.name,
            port: config.port,
            type: config.type,
            active: config.active || false,
            connected: false,
            lastConnection: null
        });
    }

    /**
     * Activar terminal específico
     */
    async activateTerminal(terminalId) {
        const terminal = this.terminals.get(terminalId);
        if (!terminal) {
            throw new Error(`Terminal ${terminalId} no encontrado`);
        }

        try {
            // Cerrar puerto actual si hay uno abierto
            if (this.activeTerminal && this.sdk.currentPort) {
                await this.sdk.closePort();
            }

            // Abrir nuevo puerto
            await this.sdk.openPort(terminal.port);
            
            // Actualizar estado
            terminal.connected = true;
            terminal.lastConnection = new Date();
            this.activeTerminal = terminal;

            console.log(`✅ Terminal activado: ${terminal.name} (${terminal.port})`);
            return terminal;

        } catch (error) {
            terminal.connected = false;
            console.error(`❌ Error activando terminal ${terminal.name}:`, error);
            throw error;
        }
    }

    /**
     * Procesar venta en terminal activo
     */
    async processSale(amount, ticketId, onStatusUpdate = null) {
        if (!this.activeTerminal) {
            throw new Error('No hay terminal activo');
        }

        if (!this.activeTerminal.connected) {
            throw new Error('Terminal no conectado');
        }

        return await this.sdk.doSale(amount, ticketId, onStatusUpdate);
    }

    /**
     * Obtener estado de terminales
     */
    getTerminalsStatus() {
        const status = [];
        for (const [id, terminal] of this.terminals) {
            status.push({
                id: terminal.id,
                name: terminal.name,
                port: terminal.port,
                type: terminal.type,
                connected: terminal.connected,
                active: this.activeTerminal && this.activeTerminal.id === id,
                lastConnection: terminal.lastConnection
            });
        }
        return status;
    }
}

// Utilidades para integración con Django
class DjangoPOSIntegration {
    constructor() {
        this.posManager = new POSManager();
        this.csrfToken = this.getCSRFToken();
        this.baseUrl = '/pos/';
    }

    /**
     * Obtener token CSRF
     */
    getCSRFToken() {
        const token = document.querySelector('[name=csrfmiddlewaretoken]');
        return token ? token.value : '';
    }

    /**
     * Inicializar con configuraciones desde Django
     */
    async initializeWithConfigurations(configurations) {
        try {
            // Inicializar SDK
            await this.posManager.initialize();

            // Registrar terminales
            configurations.forEach(config => {
                this.posManager.registerTerminal({
                    id: config.id,
                    name: config.nombre,
                    port: config.puerto_conexion,
                    type: config.tipo_pos,
                    active: config.activo
                });
            });

            return { success: true };

        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    /**
     * Procesar venta completa (Django + POS)
     */
    async processSaleComplete(saleData) {
        try {
            // 1. Iniciar venta en Django
            const djangoResponse = await this.initiateSaleInDjango(saleData);
            if (!djangoResponse.success) {
                throw new Error(djangoResponse.error);
            }

            const transactionData = djangoResponse.transaccion;

            // 2. Activar terminal
            await this.posManager.activateTerminal(saleData.configuracion_id);

            // 3. Procesar en POS
            const posResult = await this.posManager.processSale(
                transactionData.monto,
                transactionData.ticket_pos,
                (status) => this.onTransactionStatusUpdate(status)
            );

            // 4. Completar en Django
            const completeResponse = await this.completeTransactionInDjango(
                transactionData.ticket_pos,
                posResult
            );

            if (!completeResponse.success) {
                throw new Error(completeResponse.error);
            }

            return {
                success: true,
                transaction: completeResponse.transaccion,
                posResult: posResult
            };

        } catch (error) {
            console.error('❌ Error en venta completa:', error);
            throw error;
        }
    }

    /**
     * Iniciar venta en Django
     */
    async initiateSaleInDjango(saleData) {
        const response = await fetch(this.baseUrl + 'iniciar-venta/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.csrfToken
            },
            body: JSON.stringify(saleData)
        });

        return await response.json();
    }

    /**
     * Completar transacción en Django
     */
    async completeTransactionInDjango(ticketPos, posResult) {
        const response = await fetch(this.baseUrl + 'completar-transaccion/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.csrfToken
            },
            body: JSON.stringify({
                ticket_pos: ticketPos,
                respuesta_pos: posResult
            })
        });

        return await response.json();
    }

    /**
     * Callback para actualizaciones de estado
     */
    onTransactionStatusUpdate(status) {
        // Emitir evento para que la UI pueda escuchar
        const event = new CustomEvent('djangoPosStatusUpdate', {
            detail: status
        });
        document.dispatchEvent(event);
    }

    /**
     * Probar conexión con terminal
     */
    async testTerminalConnection(configurationId) {
        const response = await fetch(this.baseUrl + 'probar-conexion/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.csrfToken
            },
            body: JSON.stringify({ configuracion_id: configurationId })
        });

        return await response.json();
    }
}

// Exportar clases para uso global
window.TransbankPOSIntegration = TransbankPOSIntegration;
window.POSManager = POSManager;
window.DjangoPOSIntegration = DjangoPOSIntegration;

// Instancia global para uso fácil
window.djangoPOS = new DjangoPOSIntegration();

// Event listeners globales
document.addEventListener('DOMContentLoaded', function() {
    console.log('🚀 POS Transbank JavaScript cargado');
    
    // Escuchar actualizaciones de estado
    document.addEventListener('posTransactionUpdate', function(event) {
        console.log('📊 Estado POS actualizado:', event.detail);
    });
    
    document.addEventListener('djangoPosStatusUpdate', function(event) {
        console.log('🔄 Estado Django POS actualizado:', event.detail);
    });
});
