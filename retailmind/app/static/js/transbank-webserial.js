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
    
    // Baudrates soportados (Verifone e Ingenico)
    const BAUDRATES = [115200, 9600, 19200, 38400, 57600];
    const TBKPOS_DEFAULT_BAUD = 115200;  // Preferido por defecto

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
            this.currentBaudrate = null;
            this.readBuffer = [];
        }

        // ==================== MÉTODOS DE CONEXIÓN ====================
        
        /**
         * Enviar ACK al POS (confirmar recepción de trama)
         */
        async sendAck() {
            try {
                if (!this.writer) {
                    console.warn('⚠️ No hay writer disponible para enviar ACK');
                    return;
                }
                await this.writer.write(new Uint8Array([ACK]));
                console.log('📤 ACK enviado');
            } catch (error) {
                console.warn('⚠️ Error enviando ACK:', error.message);
            }
        }
        
        /**
         * Auto-conectar al POS buscando en puertos disponibles
         * Prueba múltiples baudrates para compatibilidad Verifone/Ingenico
         */
        async autoConnect(tryAllBaudrates = false) {
            try {
                console.log('🔍 Buscando puertos autorizados...');
                
                // Si ya está conectado, no reconectar
                if (this.isConnected && this.port) {
                    console.log('✅ POS ya está conectado');
                    const info = this.port.getInfo();
                    return {
                        success: true,
                        connected: true,
                        port: `VID:${info.usbVendorId} PID:${info.usbProductId}`,
                        baudrate: this.currentBaudrate || TBKPOS_DEFAULT_BAUD,
                        info: info
                    };
                }
                
                const ports = await navigator.serial.getPorts();
                
                if (ports.length === 0) {
                    throw new Error('No hay puertos autorizados. Por favor, conecte manualmente.');
                }

                // Baudrates a probar
                const baudratesToTry = tryAllBaudrates ? BAUDRATES : [TBKPOS_DEFAULT_BAUD];
                
                for (const port of ports) {
                    for (const baudRate of baudratesToTry) {
                        try {
                            console.log(`🔌 Probando baudrate ${baudRate}...`);
                            
                            // Verificar si el puerto ya está abierto
                            if (port.readable && port.writable) {
                                console.log('⚠️ Puerto abierto, cerrando...');
                                try {
                                    await port.close();
                                    await new Promise(resolve => setTimeout(resolve, 500));
                                } catch (e) {
                                    console.warn('No se pudo cerrar puerto:', e.message);
                                }
                            }
                            
                            await port.open({ baudRate });
                            this.port = port;
                            this.reader = port.readable.getReader();
                            this.writer = port.writable.getWriter();
                            this.isConnected = true;
                            this.currentBaudrate = baudRate;
                            
                            // Verificar con POLL
                            const pollResult = await this.poll();
                            if (pollResult) {
                                const info = port.getInfo();
                                const deviceType = this.detectDeviceType(info);
                                
                                console.log(`✅ POS conectado y verificado en ${baudRate} bps`);
                                console.log(`📱 Dispositivo detectado: ${deviceType}`);
                                
                                return {
                                    success: true,
                                    connected: true,
                                    port: `VID:${info.usbVendorId} PID:${info.usbProductId}`,
                                    baudrate: baudRate,
                                    deviceType: deviceType,
                                    info: info
                                };
                            }
                        } catch (err) {
                            console.log(`❌ Fallo con baudrate ${baudRate}: ${err.message}`);
                            if (this.port) {
                                try {
                                    await this.disconnect();
                                } catch (e) {}
                            }
                            continue;
                        }
                    }
                }
                
                throw new Error('No se pudo conectar al POS en ningún puerto/baudrate');
                
            } catch (error) {
                console.error('❌ Error en autoConnect:', error);
                throw error;
            }
        }
        
        /**
         * Detectar tipo de dispositivo por VID/PID
         */
        detectDeviceType(info) {
            const vid = info.usbVendorId;
            const pid = info.usbProductId;
            
            // Verifone VIDs comunes
            if (vid === 0x11CA || vid === 0x079B) {
                return 'Verifone';
            }
            
            // Ingenico VIDs comunes
            if (vid === 0x0B00 || vid === 0x15D1) {
                return 'Ingenico';
            }
            
            return 'Transbank POS';
        }

        /**
         * Conectar manualmente (solicita al usuario seleccionar puerto)
         * Prueba múltiples baudrates si el primero falla
         */
        async connect(baudRate = TBKPOS_DEFAULT_BAUD) {
            try {
                console.log('🔌 Solicitando puerto serial...');

                // Solicitar puerto al usuario (abre diálogo del navegador)
                const selectedPort = await navigator.serial.requestPort();

                // Probar baudrates: primero el default, luego otros
                const baudratesToTry = [baudRate, ...BAUDRATES.filter(b => b !== baudRate)];

                for (const baud of baudratesToTry) {
                    try {
                        console.log(`📡 Abriendo puerto a ${baud} bps...`);

                        // Cerrar si estaba abierto de un intento previo
                        if (selectedPort.readable || selectedPort.writable) {
                            try { await selectedPort.close(); } catch(e) {}
                            await new Promise(r => setTimeout(r, 300));
                        }

                        await selectedPort.open({ baudRate: baud });

                        this.port = selectedPort;
                        this.reader = selectedPort.readable.getReader();
                        this.writer = selectedPort.writable.getWriter();
                        this.isConnected = true;
                        this.currentBaudrate = baud;

                        // Verificar con POLL
                        const pollResult = await this.poll();
                        if (pollResult) {
                            const info = selectedPort.getInfo();
                            const deviceType = this.detectDeviceType(info);

                            console.log(`✅ POS conectado exitosamente a ${baud} bps`);
                            console.log(`📱 Dispositivo: ${deviceType}`);

                            return {
                                success: true,
                                connected: true,
                                port: `VID:${info.usbVendorId} PID:${info.usbProductId}`,
                                baudrate: baud,
                                deviceType: deviceType,
                                info: info
                            };
                        }

                        // POLL falló, limpiar y probar siguiente baudrate
                        await this.disconnect();

                    } catch (err) {
                        console.log(`❌ Fallo con ${baud} bps: ${err.message}`);
                        try { await this.disconnect(); } catch(e) {}
                    }
                }

                throw new Error('POS no responde en ninguna velocidad. Verifique que esté encendido.');

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
         * @param {string} command - Comando a enviar
         * @param {number} customTimeout - Timeout personalizado en ms (opcional)
         */
        async sendCommand(command, customTimeout = null) {
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
                
                // Leer respuesta con timeout personalizado si se especifica
                const response = await this.readResponse(customTimeout);
                return response;
                
            } catch (error) {
                console.error('❌ Error enviando comando:', error);
                throw error;
            }
        }

        /**
         * Leer respuesta del POS
         * @param {number} customTimeout - Timeout personalizado (opcional)
         */
        async readResponse(customTimeout = null) {
            return new Promise((resolve, reject) => {
                let buffer = [];
                let timeout;
                const timeoutMs = customTimeout || this.timeout;

                const read = async () => {
                    try {
                        const { value, done } = await this.reader.read();
                        
                        if (done) {
                            reject(new Error('Conexión cerrada'));
                            return;
                        }

                        // Agregar bytes al buffer
                        buffer.push(...value);

                        // Verificar si es ACK simple (byte único 0x06)
                        if (buffer.length === 1 && buffer[0] === ACK) {
                            clearTimeout(timeout);
                            console.log('✅ ACK recibido del POS');
                            resolve({ type: 'ACK' });
                            return;
                        }

                        // Buscar trama completa (STX...ETX LRC)
                        const stxIndex = buffer.indexOf(STX);
                        if (stxIndex >= 0) {
                            const etxIndex = buffer.indexOf(ETX, stxIndex);
                            if (etxIndex >= 0 && buffer.length >= etxIndex + 2) {
                                // Trama completa recibida
                                const frame = buffer.slice(stxIndex, etxIndex + 2);
                                const data = frame.slice(1, frame.length - 2); // Sin STX, ETX, LRC
                                
                                clearTimeout(timeout);
                                
                                const decoder = new TextDecoder();
                                const response = decoder.decode(new Uint8Array(data));
                                console.log(`📥 Respuesta: ${response}`);
                                
                                // ✅ ENVIAR ACK AL POS (confirmar recepción)
                                this.sendAck().catch(err => console.warn('Error enviando ACK:', err));
                                
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
                    reject(new Error(`Timeout (${timeoutMs}ms) esperando respuesta del POS`));
                }, timeoutMs);
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
         * Cargar llaves criptográficas (tarda 30-60 segundos)
         */
        async loadKeys() {
            try {
                console.log('🔑 Cargando llaves... (puede tardar 30-60 segundos)');
                
                // Construir y enviar comando manualmente para manejar las dos respuestas
                const encoder = new TextEncoder();
                const commandBytes = encoder.encode('0800');
                
                let lrc = 0;
                for (let byte of commandBytes) {
                    lrc ^= byte;
                }
                lrc ^= ETX;
                
                const frame = new Uint8Array([STX, ...commandBytes, ETX, lrc]);
                console.log('📤 Enviando: 0800');
                await this.writer.write(frame);
                
                // 1. Esperar ACK inicial (POS confirma que recibió el comando)
                const ack = await this.readResponse(10000); // 10 segundos para ACK
                if (ack.type !== 'ACK') {
                    throw new Error('No se recibió ACK del POS');
                }
                console.log('⏳ POS procesando carga de llaves (30-60 segundos)...');
                
                // 2. Esperar respuesta con datos (puede tardar 30-60 segundos)
                const response = await this.readResponse(120000); // 120 segundos para datos
                
                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    const result = {
                        functionCode: parseInt(parts[0]),
                        responseCode: parseInt(parts[1]),
                        commerceCode: parts[2] || '',
                        terminalId: parts[3] || '',
                        success: parseInt(parts[1]) === 0
                    };
                    
                    console.log(result.success ? '✅ Llaves cargadas exitosamente' : '❌ Error cargando llaves');
                    return result;
                }
                
                throw new Error('Respuesta inválida de carga de llaves');
                
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
                
                // Construir trama manualmente para manejar ACK + respuesta
                const encoder = new TextEncoder();
                const commandBytes = encoder.encode(command);
                
                let lrc = 0;
                for (let byte of commandBytes) {
                    lrc ^= byte;
                }
                lrc ^= ETX;
                
                const frame = new Uint8Array([STX, ...commandBytes, ETX, lrc]);
                console.log(`📤 Enviando: ${command}`);
                await this.writer.write(frame);
                
                // 1. Esperar ACK inicial (POS confirma que recibió el comando)
                const ack = await this.readResponse(10000); // 10 segundos para ACK
                if (ack.type !== 'ACK') {
                    throw new Error('No se recibió ACK del POS');
                }
                console.log('⏳ POS procesando venta (puede tardar hasta 3 minutos)...');
                
                // 2. Esperar respuesta con datos (puede tardar varios minutos)
                const response = await this.readResponse(180000); // 180 segundos (3 minutos) para venta
                
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
                console.log('📋 Consultando última venta...');
                
                // Construir y enviar comando manualmente
                const command = '0250|';
                const encoder = new TextEncoder();
                const commandBytes = encoder.encode(command);
                
                let lrc = 0;
                for (let byte of commandBytes) {
                    lrc ^= byte;
                }
                lrc ^= ETX;
                
                const frame = new Uint8Array([STX, ...commandBytes, ETX, lrc]);
                console.log(`📤 Enviando: ${command}`);
                await this.writer.write(frame);
                
                // 1. Esperar ACK
                const ack = await this.readResponse(5000);
                if (ack.type !== 'ACK') {
                    throw new Error('No se recibió ACK del POS');
                }
                
                // 2. Esperar datos
                const response = await this.readResponse(10000);
                
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
                
                // Construir y enviar comando manualmente
                const command = '0500||';
                const encoder = new TextEncoder();
                const commandBytes = encoder.encode(command);
                
                let lrc = 0;
                for (let byte of commandBytes) {
                    lrc ^= byte;
                }
                lrc ^= ETX;
                
                const frame = new Uint8Array([STX, ...commandBytes, ETX, lrc]);
                console.log(`📤 Enviando: ${command}`);
                await this.writer.write(frame);
                
                // 1. Esperar ACK
                const ack = await this.readResponse(10000);
                if (ack.type !== 'ACK') {
                    throw new Error('No se recibió ACK del POS');
                }
                console.log('⏳ POS procesando cierre (puede tardar 30-60 segundos)...');
                
                // 2. Esperar datos (60 segundos)
                const response = await this.readResponse(60000);
                
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
                
                // Construir y enviar comando manualmente
                const opId = operationId.toString().padStart(6, '0');
                const command = `1200|${opId}|`;
                const encoder = new TextEncoder();
                const commandBytes = encoder.encode(command);
                
                let lrc = 0;
                for (let byte of commandBytes) {
                    lrc ^= byte;
                }
                lrc ^= ETX;
                
                const frame = new Uint8Array([STX, ...commandBytes, ETX, lrc]);
                console.log(`📤 Enviando: ${command}`);
                await this.writer.write(frame);
                
                // 1. Esperar ACK
                const ack = await this.readResponse(10000);
                if (ack.type !== 'ACK') {
                    throw new Error('No se recibió ACK del POS');
                }
                console.log('⏳ POS procesando anulación (puede tardar 30 segundos)...');
                
                // 2. Esperar datos (30 segundos)
                const response = await this.readResponse(30000);
                
                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    return {
                        functionCode: parseInt(parts[0]),
                        responseCode: parseInt(parts[1]),
                        commerceCode: parts[2],
                        terminalId: parts[3],
                        authorizationCode: parts[4],
                        operationId: parts[5],
                        successful: parseInt(parts[1]) === 0,
                        responseMessage: RESPONSE_CODES[parseInt(parts[1])] || `Código ${parts[1]}`
                    };
                }
                throw new Error('Respuesta inválida');
            } catch (error) {
                console.error('❌ Error en anulación:', error);
                throw error;
            }
        }
        
        /**
         * Obtener detalle de ventas del día
         * @param {boolean} printOnPOS - Si debe imprimir en el POS (0=no, 1=sí)
         */
        async getSalesDetail(printOnPOS = false) {
            try {
                console.log('📋 Obteniendo detalle de ventas...');
                const print = printOnPOS ? '1' : '0';
                const response = await this.sendCommand(`0260|${print}|`);
                
                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    return {
                        functionCode: parseInt(parts[0]),
                        responseCode: parseInt(parts[1]),
                        txCount: parseInt(parts[2]) || 0,
                        txTotal: parseInt(parts[3]) || 0,
                        successful: parseInt(parts[1]) === 0,
                        responseMessage: RESPONSE_CODES[parseInt(parts[1])] || `Código ${parts[1]}`
                    };
                }
                throw new Error('Respuesta inválida');
            } catch (error) {
                console.error('❌ Error obteniendo detalle:', error);
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
        getSalesDetail: (printOnPOS) => pos.getSalesDetail(printOnPOS),
        
        // Estado (propiedad, no función)
        get isConnected() {
            return pos.isConnected;
        }
    };

    console.log('✅ Transbank Web Serial API cargada');

})(window);
