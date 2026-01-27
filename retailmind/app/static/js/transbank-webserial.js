/**
 * Transbank POS - Web Serial API Integration
 * Adaptado de Laravel para Django RetailMind
 * Versión: 1.0.0
 */

(function(window) {
    'use strict';

    // ==================== CONSTANTES DEL PROTOCOLO ====================
    const STX = 0x02;  // Start of Text
    const ETX = 0x03;  // End of Text
    const ACK = 0x06;  // Acknowledge
    const NAK = 0x15;  // Negative Acknowledge
    const TBKPOS_DEFAULT_BAUD = 115200;

    // Códigos de respuesta
    const RESPONSE_CODES = {
        0: 'APROBADA',
        5: 'TRANSACCIÓN RECHAZADA',
        7: 'RETENER TARJETA',
        12: 'TRANSACCIÓN INVÁLIDA',
        13: 'MONTO INVÁLIDO',
        51: 'FONDOS INSUFICIENTES',
        54: 'TARJETA VENCIDA',
        61: 'EXCEDE LÍMITE',
        70: 'ERROR INICIALIZACIÓN',
        88: 'SIN CONEXIÓN TRANSBANK',
        91: 'EMISOR NO DISPONIBLE',
        99: 'CANCELADA POR USUARIO'
    };

    // ==================== CLASE PRINCIPAL ====================
    class TransbankPOS {
        constructor() {
            this.port = null;
            this.reader = null;
            this.writer = null;
            this.isConnected = false;
            this.timeout = 180000; // 3 minutos para ventas
            this.readBuffer = [];
        }

        // ==================== MÉTODOS DE CONEXIÓN ====================
        
        /**
         * Auto-conectar al POS buscando en puertos disponibles
         */
        async autoConnect(baudRate = TBKPOS_DEFAULT_BAUD) {
            try {
                console.log('🔍 Buscando puertos autorizados...');
                
                const ports = await navigator.serial.getPorts();
                
                if (ports.length === 0) {
                    throw new Error('No hay puertos autorizados. Por favor, conecte manualmente.');
                }

                for (const port of ports) {
                    try {
                        console.log(`🔌 Intentando conectar en baudrate ${baudRate}...`);
                        
                        await port.open({ baudRate });
                        this.port = port;
                        this.reader = port.readable.getReader();
                        this.writer = port.writable.getWriter();
                        this.isConnected = true;
                        
                        // Verificar con POLL
                        const pollResult = await this.poll();
                        if (pollResult) {
                            console.log('✅ POS conectado y verificado');
                            
                            // Obtener info del puerto
                            const info = port.getInfo();
                            return {
                                success: true,
                                connected: true,
                                port: `VID:${info.usbVendorId} PID:${info.usbProductId}`,
                                baudrate: baudRate,
                                info: info
                            };
                        }
                    } catch (err) {
                        console.log(`❌ Fallo en puerto: ${err.message}`);
                        if (this.port) {
                            try {
                                await this.disconnect();
                            } catch (e) {}
                        }
                        continue;
                    }
                }
                
                throw new Error('No se pudo conectar al POS en ningún puerto');
                
            } catch (error) {
                console.error('❌ Error en autoConnect:', error);
                throw error;
            }
        }

        /**
         * Conectar manualmente (solicita al usuario seleccionar puerto)
         */
        async connect(baudRate = TBKPOS_DEFAULT_BAUD) {
            try {
                console.log('🔌 Solicitando puerto serial...');
                
                // Solicitar puerto al usuario
                this.port = await navigator.serial.requestPort();
                
                console.log('📡 Abriendo puerto...');
                await this.port.open({ baudRate });
                
                this.reader = this.port.readable.getReader();
                this.writer = this.port.writable.getWriter();
                this.isConnected = true;
                
                // Verificar con POLL
                const pollResult = await this.poll();
                if (!pollResult) {
                    throw new Error('POS no responde a POLL');
                }
                
                console.log('✅ POS conectado exitosamente');
                
                const info = this.port.getInfo();
                return {
                    success: true,
                    connected: true,
                    port: `VID:${info.usbVendorId} PID:${info.usbProductId}`,
                    baudrate: baudRate,
                    info: info
                };
                
            } catch (error) {
                console.error('❌ Error conectando:', error);
                this.isConnected = false;
                throw error;
            }
        }

        /**
         * Desconectar del POS
         */
        async disconnect() {
            try {
                if (this.reader) {
                    await this.reader.cancel();
                    this.reader.releaseLock();
                    this.reader = null;
                }
                
                if (this.writer) {
                    this.writer.releaseLock();
                    this.writer = null;
                }
                
                if (this.port) {
                    await this.port.close();
                    this.port = null;
                }
                
                this.isConnected = false;
                console.log('🔌 POS desconectado');
                return true;
                
            } catch (error) {
                console.error('❌ Error desconectando:', error);
                return false;
            }
        }

        // ==================== MÉTODOS DE COMUNICACIÓN ====================
        
        /**
         * Enviar comando al POS
         */
        async sendCommand(command) {
            if (!this.isConnected) {
                throw new Error('POS no está conectado');
            }

            try {
                // Construir trama: STX + comando + ETX + LRC
                const encoder = new TextEncoder();
                const commandBytes = encoder.encode(command);
                
                // Calcular LRC
                let lrc = 0;
                for (let byte of commandBytes) {
                    lrc ^= byte;
                }
                lrc ^= ETX;
                
                // Construir trama completa
                const frame = new Uint8Array([STX, ...commandBytes, ETX, lrc]);
                
                console.log(`📤 Enviando: ${command}`);
                await this.writer.write(frame);
                
                // Leer respuesta
                const response = await this.readResponse();
                return response;
                
            } catch (error) {
                console.error('❌ Error enviando comando:', error);
                throw error;
            }
        }

        /**
         * Leer respuesta del POS
         */
        async readResponse() {
            return new Promise((resolve, reject) => {
                let buffer = [];
                let timeout;

                const read = async () => {
                    try {
                        const { value, done } = await this.reader.read();
                        
                        if (done) {
                            reject(new Error('Conexión cerrada'));
                            return;
                        }

                        // Agregar bytes al buffer
                        buffer.push(...value);

                        // Verificar si es ACK simple
                        if (buffer.length === 1 && buffer[0] === ACK) {
                            clearTimeout(timeout);
                            resolve({ type: 'ACK' });
                            return;
                        }

                        // Buscar trama completa (STX...ETX LRC)
                        const stxIndex = buffer.indexOf(STX);
                        if (stxIndex >= 0) {
                            const etxIndex = buffer.indexOf(ETX, stxIndex);
                            if (etxIndex >= 0 && buffer.length >= etxIndex + 2) {
                                // Trama completa
                                const frame = buffer.slice(stxIndex, etxIndex + 2);
                                const data = frame.slice(1, frame.length - 2); // Sin STX, ETX, LRC
                                
                                clearTimeout(timeout);
                                
                                const decoder = new TextDecoder();
                                const response = decoder.decode(new Uint8Array(data));
                                console.log(`📥 Respuesta: ${response}`);
                                
                                resolve({ type: 'DATA', data: response });
                                return;
                            }
                        }

                        // Continuar leyendo
                        read();
                        
                    } catch (error) {
                        clearTimeout(timeout);
                        reject(error);
                    }
                };

                // Iniciar lectura
                read();

                // Timeout
                timeout = setTimeout(() => {
                    reject(new Error('Timeout esperando respuesta del POS'));
                }, this.timeout);
            });
        }

        // ==================== COMANDOS POS ====================
        
        /**
         * POLL - Verificar conexión
         */
        async poll() {
            try {
                const response = await this.sendCommand('0100');
                return response.type === 'ACK';
            } catch (error) {
                console.error('❌ Error en POLL:', error);
                return false;
            }
        }

        /**
         * Cargar llaves criptográficas
         */
        async loadKeys() {
            try {
                console.log('🔑 Cargando llaves... (puede tardar 30-60 segundos)');
                
                // Aumentar timeout temporalmente
                const originalTimeout = this.timeout;
                this.timeout = 90000; // 90 segundos
                
                const response = await this.sendCommand('0800');
                
                this.timeout = originalTimeout;
                
                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    const result = {
                        functionCode: parseInt(parts[0]),
                        responseCode: parseInt(parts[1]),
                        commerceCode: parts[2],
                        terminalId: parts[3],
                        success: parseInt(parts[1]) === 0
                    };
                    
                    console.log(result.success ? '✅ Llaves cargadas' : '❌ Error cargando llaves');
                    return result;
                }
                
                throw new Error('Respuesta inválida');
                
            } catch (error) {
                console.error('❌ Error cargando llaves:', error);
                throw error;
            }
        }

        /**
         * Procesar venta
         */
        async sale(amount, ticket) {
            try {
                console.log(`💳 Procesando venta: $${amount} - Ticket: ${ticket}`);
                
                // Formatear parámetros
                const amountStr = amount.toString().padStart(9, '0');
                const ticketStr = ticket.toString().padStart(6, '0');
                
                // Comando: 0200|MONTO|TICKET|||||
                const command = `0200|${amountStr}|${ticketStr}|||||`;
                
                const response = await this.sendCommand(command);
                
                if (response.type === 'DATA') {
                    return this.parseSaleResponse(response.data);
                }
                
                throw new Error('Respuesta inválida del POS');
                
            } catch (error) {
                console.error('❌ Error en venta:', error);
                throw error;
            }
        }

        /**
         * Parsear respuesta de venta
         */
        parseSaleResponse(data) {
            const parts = data.split('|');
            
            const response = {
                functionCode: parseInt(parts[0]),
                responseCode: parseInt(parts[1]),
                commerceCode: parts[2],
                terminalId: parts[3],
                ticket: parts[4],
                authorizationCode: parts[5] ? parts[5].trim() : '',
                amount: parseInt(parts[6]),
                sharesNumber: parseInt(parts[7]) || 0,
                sharesAmount: parts[8] || '',
                last4Digits: parts[9] || '',
                operationNumber: parts[10] || '',
                cardType: parts[11] || '',
                accountingDate: parts[12] || '',
                accountNumber: parts[13] || '',
                cardBrand: parts[14] || '',
                realDate: parts[15] || '',
                realTime: parts[16] || '',
                employeeId: parts[17] || '',
                tip: parseInt(parts[18]) || 0,
                successful: parseInt(parts[1]) === 0,
                responseMessage: RESPONSE_CODES[parseInt(parts[1])] || `Código ${parts[1]}`
            };
            
            console.log(response.successful ? '✅ Venta APROBADA' : '❌ Venta RECHAZADA');
            console.log(`   Autorización: ${response.authorizationCode}`);
            console.log(`   Tarjeta: ${response.cardBrand} - ${response.cardType}`);
            
            return response;
        }

        /**
         * Última venta
         */
        async lastSale() {
            try {
                const response = await this.sendCommand('0250|');
                if (response.type === 'DATA') {
                    return this.parseSaleResponse(response.data);
                }
                throw new Error('Respuesta inválida');
            } catch (error) {
                console.error('❌ Error obteniendo última venta:', error);
                throw error;
            }
        }

        /**
         * Totales del día
         */
        async getTotals() {
            try {
                const response = await this.sendCommand('0700||');
                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    return {
                        functionCode: parseInt(parts[0]),
                        responseCode: parseInt(parts[1]),
                        txCount: parseInt(parts[2]),
                        txTotal: parseInt(parts[3]),
                        successful: parseInt(parts[1]) === 0
                    };
                }
                throw new Error('Respuesta inválida');
            } catch (error) {
                console.error('❌ Error obteniendo totales:', error);
                throw error;
            }
        }

        /**
         * Cierre de día
         */
        async closeDay() {
            try {
                console.log('🔒 Ejecutando cierre de día...');
                const response = await this.sendCommand('0500||');
                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    return {
                        functionCode: parseInt(parts[0]),
                        responseCode: parseInt(parts[1]),
                        commerceCode: parts[2],
                        terminalId: parts[3],
                        successful: parseInt(parts[1]) === 0
                    };
                }
                throw new Error('Respuesta inválida');
            } catch (error) {
                console.error('❌ Error en cierre:', error);
                throw error;
            }
        }

        /**
         * Anular transacción
         */
        async refund(operationId) {
            try {
                console.log(`↩️ Anulando operación ${operationId}...`);
                const opId = operationId.toString().padStart(6, '0');
                const response = await this.sendCommand(`1200|${opId}|`);
                
                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    return {
                        functionCode: parseInt(parts[0]),
                        responseCode: parseInt(parts[1]),
                        commerceCode: parts[2],
                        terminalId: parts[3],
                        authorizationCode: parts[4],
                        operationId: parts[5],
                        successful: parseInt(parts[1]) === 0
                    };
                }
                throw new Error('Respuesta inválida');
            } catch (error) {
                console.error('❌ Error en anulación:', error);
                throw error;
            }
        }
    }

    // ==================== API GLOBAL ====================
    
    // Instancia singleton
    const pos = new TransbankPOS();

    // Exponer al objeto window
    window.Transbank = window.Transbank || {};
    window.Transbank.POS = {
        Integrado: pos,
        
        // Métodos directos
        autoConnect: (baudRate) => pos.autoConnect(baudRate),
        connect: (baudRate) => pos.connect(baudRate),
        disconnect: () => pos.disconnect(),
        poll: () => pos.poll(),
        loadKeys: () => pos.loadKeys(),
        sale: (amount, ticket) => pos.sale(amount, ticket),
        lastSale: () => pos.lastSale(),
        getTotals: () => pos.getTotals(),
        closeDay: () => pos.closeDay(),
        refund: (operationId) => pos.refund(operationId),
        
        // Estado
        isConnected: () => pos.isConnected
    };

    console.log('✅ Transbank Web Serial API cargada');

})(window);
