/**
 * Transbank POS - Web Serial API Integration
 * Adaptado de Laravel para Django RetailMind
 * Versión: 1.0.0
 */

(function(window) {
    'use strict';

    // Guard contra doble carga del archivo (previene instancias duplicadas y carreras de open())
    if (window.__TBK_WEBSERIAL_LOADED__) {
        console.warn('⚠️ transbank-webserial.js ya estaba cargado; se ignora la segunda carga.');
        return;
    }
    window.__TBK_WEBSERIAL_LOADED__ = true;

    // ==================== CONSTANTES DEL PROTOCOLO ====================
    const STX = 0x02;  // Start of Text
    const ETX = 0x03;  // End of Text
    const ACK = 0x06;  // Acknowledge
    const NAK = 0x15;  // Negative Acknowledge
    
    // Baudrates soportados (Verifone e Ingenico)
    const BAUDRATES = [115200, 9600, 19200, 38400, 57600];
    const TBKPOS_DEFAULT_BAUD = 115200;  // Preferido por defecto

    // Códigos de respuesta del POS Integrado Transbank (según SDK oficial)
    // https://github.com/TransbankDevelopers/transbank-pos-sdk-nodejs
    const RESPONSE_CODES = {
        0:  'Aprobado',
        1:  'Rechazado',
        2:  'Host no responde',
        3:  'Conexión falló',
        4:  'Transacción ya fue anulada',
        5:  'No existe transacción para anular',
        6:  'Tarjeta no soportada',
        7:  'Transacción cancelada desde el POS',
        8:  'No puede anular transacción débito',
        9:  'Error lectura tarjeta',
        10: 'Monto menor al mínimo permitido',
        11: 'No existe venta',
        12: 'Transacción no soportada',
        13: 'Debe ejecutar cierre',
        14: 'No hay tono',
        15: 'Archivo BITMAP.DAT no encontrado',
        16: 'Error formato respuesta del host',
        17: 'Error en los 4 últimos dígitos',
        18: 'Menú inválido',
        19: 'Error tarjeta distribuidora',
        20: 'Tarjeta inválida',
        21: 'Anulación no permitida',
        22: 'TIMEOUT',
        24: 'Impresora sin papel',
        25: 'Fecha inválida',
        26: 'Debe cargar llaves',
        27: 'Debe actualizar',
        60: 'Error en número de cuotas',
        61: 'Error en armado de solicitud',
        62: 'Problema con el pinpad interno',
        65: 'Error al procesar la respuesta del host',
        67: 'Superó número máximo de ventas, debe ejecutar cierre',
        68: 'Error genérico, falla al ingresar montos',
        70: 'Error de formato: número de boleta/ticket excede 6 caracteres',
        71: 'Error de largo campo de impresión',
        72: 'Error de monto: debe ser mayor que 0',
        73: 'Terminal ID no configurado',
        74: 'Debe ejecutar CIERRE',
        75: 'Comercio no tiene tarjetas configuradas',
        76: 'Superó número máximo de ventas, debe ejecutar CIERRE',
        77: 'Debe ejecutar cierre',
        78: 'Esperando leer tarjeta',
        79: 'Solicitando confirmar monto',
        80: 'Selección de cuotas',
        81: 'Solicitando ingreso de clave',
        82: 'Enviando transacción al host',
        83: 'Selección menú crédito/redcompra',
        84: 'Opere tarjeta',
        85: 'Selección de cuotas',
        86: 'Ingreso de cuotas',
        87: 'Confirmación de cuotas',
        88: 'Error cantidad cuotas',
        93: 'Declinada',
        94: 'Error al procesar respuesta',
        95: 'Error al imprimir TASA'
    };

    // Sugerencias de acción por código de respuesta (para mostrar al usuario cuando hay rechazo)
    const RESPONSE_HINTS = {
        1:  'El banco emisor rechazó la transacción. Intente con otra tarjeta.',
        2:  'El host de Transbank no responde. Verifique la conexión a internet del POS.',
        3:  'Falló la conexión con Transbank. Verifique la red del POS.',
        7:  'La venta fue cancelada desde el POS.',
        10: 'El monto es inferior al mínimo permitido por Transbank.',
        13: 'Debe ejecutar "Cierre de día" en el POS antes de continuar.',
        22: 'El POS no respondió a tiempo (timeout).',
        26: 'El POS no tiene llaves criptográficas cargadas. Ejecute "Cargar llaves" (comando 0800).',
        67: 'El POS alcanzó el máximo de ventas permitidas. Ejecute "Cierre de día".',
        70: 'El número de ticket excede los 6 caracteres permitidos por el POS. Use un identificador numérico de máximo 6 dígitos.',
        72: 'El monto de la venta debe ser mayor que $0.',
        73: 'El terminal ID no está configurado en el POS. Contacte a soporte Transbank.',
        74: 'Debe ejecutar "Cierre de día" antes de continuar.',
        75: 'El comercio no tiene tarjetas configuradas en Transbank. Contacte a soporte.',
        76: 'El POS alcanzó el máximo de ventas. Ejecute "Cierre de día".',
        77: 'Debe ejecutar "Cierre de día".',
        88: 'Error en la cantidad de cuotas solicitadas.',
        93: 'Transacción declinada por el banco emisor.'
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
            // Promesa en vuelo de auto-conexión: sirve para deduplicar llamadas concurrentes
            // (dos listeners DOMContentLoaded, doble click del usuario, etc.)
            this._connectingPromise = null;
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
            // Si ya hay una conexión en curso, reutilizar esa promesa en lugar de abrir otra
            if (this._connectingPromise) {
                console.log('⏳ Auto-conexión en curso, esperando resultado previo...');
                return this._connectingPromise;
            }
            this._connectingPromise = this._doAutoConnect(tryAllBaudrates)
                .finally(() => { this._connectingPromise = null; });
            return this._connectingPromise;
        }

        async _doAutoConnect(tryAllBaudrates = false) {
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
                this.readBuffer = [];
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
         * Leer respuesta del POS (usa buffer compartido para no perder bytes entre llamadas)
         *
         * El POS puede enviar mensajes intermedios (functionCode 0900) durante operaciones
         * largas como impresión, cierre de día o carga de llaves. Por defecto se descartan
         * en forma transparente y se sigue esperando la trama final — así lo hace también
         * el SDK oficial de Transbank (ver PosBase.js de transbank-pos-sdk-nodejs).
         *
         * @param {number} customTimeout - Timeout personalizado (opcional)
         * @param {object}  [options] - { onIntermediate: fn, skipIntermediate: true }
         */
        async readResponse(customTimeout = null, options = {}) {
            const { onIntermediate = null, skipIntermediate = true } = options;

            return new Promise((resolve, reject) => {
                let timeout;
                const timeoutMs = customTimeout || this.timeout;
                let settled = false;

                const settle = (fn, val) => {
                    if (settled) return;
                    settled = true;
                    clearTimeout(timeout);
                    fn(val);
                };

                const tryParse = () => {
                    const buf = this.readBuffer;

                    // ACK solo si es el primer byte y no hay datos de trama detrás
                    const ackIdx = buf.indexOf(ACK);
                    if (ackIdx >= 0) {
                        const stxBefore = buf.indexOf(STX);
                        if (stxBefore < 0 || ackIdx < stxBefore) {
                            this.readBuffer = buf.slice(ackIdx + 1);
                            console.log('✅ ACK recibido del POS');
                            settle(resolve, { type: 'ACK' });
                            return true;
                        }
                    }

                    // Buscar trama completa STX...ETX LRC
                    const stxIndex = buf.indexOf(STX);
                    if (stxIndex >= 0) {
                        const etxIndex = buf.indexOf(ETX, stxIndex + 1);
                        if (etxIndex >= 0 && buf.length >= etxIndex + 2) {
                            const data = buf.slice(stxIndex + 1, etxIndex);
                            this.readBuffer = buf.slice(etxIndex + 2);

                            const decoder = new TextDecoder();
                            const response = decoder.decode(new Uint8Array(data));
                            console.log(`📥 Respuesta: ${response}`);

                            // ACK obligatorio por protocolo: el POS espera ACK tras cada trama,
                            // incluyendo los mensajes intermedios 0900. Si no lo enviamos, el POS
                            // puede quedarse esperando y no mandar la trama final.
                            this.sendAck().catch(err => console.warn('Error enviando ACK:', err));

                            // Mensaje intermedio (estado): 0900|<codigo>|
                            // El POS lo emite mientras imprime / procesa. No es la respuesta
                            // final y debemos seguir leyendo.
                            if (skipIntermediate && /^0900\|/.test(response)) {
                                const parts = response.split('|');
                                const code = parseInt(parts[1]);
                                console.log(`⏳ POS estado intermedio (0900): ${RESPONSE_CODES[code] || code}`);
                                if (typeof onIntermediate === 'function') {
                                    try {
                                        onIntermediate({
                                            responseCode: code,
                                            responseMessage: RESPONSE_CODES[code] || `Código ${parts[1]}`
                                        });
                                    } catch (e) { /* callback del usuario no debe romper la lectura */ }
                                }
                                return false; // seguir leyendo hasta recibir trama final
                            }

                            settle(resolve, { type: 'DATA', data: response });
                            return true;
                        }
                    }
                    return false;
                };

                // Intentar con datos que ya estén en el buffer
                if (tryParse()) return;

                const read = async () => {
                    try {
                        const { value, done } = await this.reader.read();
                        if (done) { settle(reject, new Error('Conexión cerrada')); return; }

                        this.readBuffer.push(...value);

                        if (!tryParse()) read();
                    } catch (error) {
                        settle(reject, error);
                    }
                };

                read();

                timeout = setTimeout(() => {
                    settle(reject, new Error(`Timeout (${timeoutMs}ms) esperando respuesta del POS`));
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
                console.log(`💳 Procesando venta: $${amount} - Ticket original: ${ticket}`);

                // Formatear monto: 9 dígitos, padding con ceros, truncado a 9 (consistente con SDK oficial)
                const amountStr = amount.toString().padStart(9, '0').slice(0, 9);

                // Formatear ticket: MÁX 6 caracteres.
                // El POS rechaza con código 70 ("Error de formato Campo de Boleta MAX 6") si excede 6 chars.
                // Si viene con prefijos no numéricos (ej: "TKT112624"), extraer solo los dígitos.
                let ticketClean = String(ticket || '').replace(/\D/g, ''); // solo dígitos
                if (!ticketClean) {
                    // Fallback si el ticket no tiene dígitos: usar timestamp (últimos 6)
                    ticketClean = String(Date.now()).slice(-6);
                }
                // Si quedan más de 6 dígitos, tomar los últimos 6 (más significativos de un id secuencial)
                if (ticketClean.length > 6) {
                    ticketClean = ticketClean.slice(-6);
                }
                const ticketStr = ticketClean.padStart(6, '0').slice(0, 6);
                console.log(`🎫 Ticket formateado para POS: ${ticketStr} (6 chars)`);

                // Comando: 0200|MONTO|TICKET|||sendStatus  (mismo formato que SDK oficial)
                const command = `0200|${amountStr}|${ticketStr}|||0`;
                
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
            
            const responseCode = parseInt(parts[1]);
            const response = {
                functionCode: parseInt(parts[0]),
                responseCode: responseCode,
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
                successful: responseCode === 0,
                responseMessage: RESPONSE_CODES[responseCode] || `Código ${parts[1]}`,
                hint: RESPONSE_HINTS[responseCode] || ''
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
                
                // 1. Esperar ACK (10s)
                const ack = await this.readResponse(10000);
                if (ack.type !== 'ACK') {
                    throw new Error('No se recibió ACK del POS');
                }
                
                // 2. Esperar datos (30s — el POS puede tardar si está imprimiendo)
                const response = await this.readResponse(30000);
                
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
                console.log('📊 Consultando totales del día...');

                const command = '0700||';
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

                // 2. Esperar datos (30s — el POS puede imprimir el resumen antes de responder;
                // los 0900 intermedios se descartan automáticamente en readResponse)
                const response = await this.readResponse(30000);

                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    const responseCode = parseInt(parts[1]);
                    return {
                        functionCode: parseInt(parts[0]),
                        responseCode: responseCode,
                        txCount: parseInt(parts[2]) || 0,
                        txTotal: parseInt(parts[3]) || 0,
                        successful: responseCode === 0,
                        responseMessage: RESPONSE_CODES[responseCode] || `Código ${parts[1]}`
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
                console.log('⏳ POS procesando cierre (puede tardar 60-120 segundos mientras imprime)...');

                // 2. Esperar trama final 0500 (hasta 120s). El POS envía varios 0900
                // "imprimiendo…" que readResponse descarta automáticamente.
                const response = await this.readResponse(120000);

                if (response.type === 'DATA') {
                    const parts = response.data.split('|');
                    const responseCode = parseInt(parts[1]);
                    return {
                        functionCode: parseInt(parts[0]),
                        responseCode: responseCode,
                        commerceCode: parts[2] || '',
                        terminalId: parts[3] || '',
                        successful: responseCode === 0,
                        responseMessage: RESPONSE_CODES[responseCode] || `Código ${parts[1]}`
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
         *
         * Protocolo Transbank POS Integrado — comando 0260|<print>|
         *   print = "0" → el POS imprime el detalle; no manda tramas de detalle por serial
         *                 (solo ACK + eventuales 0900 "imprimiendo" + 0260 resumen final).
         *   print = "1" → el POS NO imprime; envía múltiples 0261 (uno por venta) y
         *                 termina con un 0261 "vacío" (authorizationCode vacío) como
         *                 marcador de fin de stream (así funciona el SDK oficial).
         *
         * @param {boolean} printOnPOS - true = imprimir en el POS, false = solo consultar
         */
        async getSalesDetail(printOnPOS = false) {
            try {
                console.log('📋 Obteniendo detalle de ventas...');
                // IMPORTANTE: el flag se envía "invertido" respecto al nombre del parámetro,
                // pero esto es lo que exige el protocolo (ver SDK oficial Node:
                // src/PosIntegrado.js: `let print = printOnPos ? "0" : "1"`).
                const print = printOnPOS ? '0' : '1';
                const command = `0260|${print}|`;
                const encoder = new TextEncoder();
                const commandBytes = encoder.encode(command);

                let lrc = 0;
                for (let byte of commandBytes) {
                    lrc ^= byte;
                }
                lrc ^= ETX;

                const frame = new Uint8Array([STX, ...commandBytes, ETX, lrc]);
                console.log(`📤 Enviando: ${command} (printOnPOS=${printOnPOS})`);
                await this.writer.write(frame);

                // 1. Esperar ACK (10s)
                const ack = await this.readResponse(10000);
                if (ack.type !== 'ACK') {
                    throw new Error('No se recibió ACK del POS');
                }

                const transactions = [];

                // --- Modo IMPRIMIR EN POS ---
                // El POS imprime físicamente. Puede tardar y no devolver tramas de detalle.
                // Esperamos hasta 60s una trama final (0260) para confirmar, o bien damos
                // el comando por exitoso si no llega nada adicional (timeout "suave").
                if (printOnPOS) {
                    console.log('🖨️ POS imprimiendo detalle de ventas… esperando confirmación');
                    try {
                        const response = await this.readResponse(60000);
                        if (response.type === 'DATA') {
                            const parts = response.data.split('|');
                            const funcCode = parseInt(parts[0]);
                            const responseCode = parseInt(parts[1]) || 0;
                            return {
                                functionCode: funcCode,
                                responseCode: responseCode,
                                txCount: parseInt(parts[2]) || 0,
                                txTotal: parseInt(parts[3]) || 0,
                                successful: responseCode === 0,
                                responseMessage: RESPONSE_CODES[responseCode] || 'IMPRESO EN POS',
                                printedOnPOS: true,
                                transactions: transactions
                            };
                        }
                    } catch (e) {
                        // Muchos POS no emiten trama final tras imprimir; si sólo vimos ACK
                        // (y descartamos 0900 intermedios) consideramos el comando ejecutado.
                        console.log('ℹ️ Sin trama final tras impresión; se considera ejecutado.');
                    }
                    return {
                        functionCode: 260,
                        responseCode: 0,
                        txCount: 0,
                        txTotal: 0,
                        successful: true,
                        responseMessage: 'IMPRESO EN POS',
                        printedOnPOS: true,
                        transactions: transactions
                    };
                }

                // --- Modo SOLO CONSULTAR (streaming 0261) ---
                const maxFrames = 500;
                for (let i = 0; i < maxFrames; i++) {
                    let response;
                    try {
                        response = await this.readResponse(30000);
                    } catch (e) {
                        // Timeout entre tramas: asumimos fin si ya recibimos al menos una.
                        if (transactions.length > 0) {
                            console.warn('⚠️ Timeout leyendo siguiente trama; retornando lo acumulado.');
                            break;
                        }
                        throw e;
                    }
                    if (response.type !== 'DATA') break;

                    const parts = response.data.split('|');
                    const funcCode = parseInt(parts[0]);

                    if (funcCode === 261) {
                        const parsed = this.parseSaleResponse(response.data);
                        const authCode = (parsed.authorizationCode || '').trim();
                        // Marcador de fin de stream: 0261 con authorizationCode vacío
                        if (!authCode) {
                            console.log('🏁 Fin de stream de detalle (trama 0261 vacía)');
                            break;
                        }
                        transactions.push(parsed);
                        console.log(`   📄 Transacción ${transactions.length} recibida`);
                        continue;
                    }

                    // Trama 0260 de resumen (algunos POS la envían al final)
                    const responseCode = parseInt(parts[1]) || 0;
                    return {
                        functionCode: funcCode,
                        responseCode: responseCode,
                        txCount: parseInt(parts[2]) || transactions.length,
                        txTotal: parseInt(parts[3]) || transactions.reduce((s, t) => s + (t.amount || 0), 0),
                        successful: responseCode === 0,
                        responseMessage: RESPONSE_CODES[responseCode] || `Código ${parts[1]}`,
                        printedOnPOS: false,
                        transactions: transactions
                    };
                }

                return {
                    functionCode: 260,
                    responseCode: 0,
                    txCount: transactions.length,
                    txTotal: transactions.reduce((s, t) => s + (t.amount || 0), 0),
                    successful: true,
                    responseMessage: 'APROBADA',
                    printedOnPOS: false,
                    transactions: transactions
                };
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
        getDetails: (printOnPOS) => pos.getSalesDetail(printOnPOS),
        getLastSale: () => pos.lastSale(),
        
        // Estado (propiedad, no función)
        get isConnected() {
            return pos.isConnected;
        }
    };

    console.log('✅ Transbank Web Serial API cargada');

})(window);
