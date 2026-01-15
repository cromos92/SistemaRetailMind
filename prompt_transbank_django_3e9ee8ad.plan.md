---
name: Prompt Transbank Django
overview: ""
todos: []
---

# Prompt para Implementar Transbank POS en Django

## Resumen del Analisis

He analizado la implementacion actual de Transbank POS Integrado en tu proyecto Laravel. A continuacion te presento el prompt completo para replicarlo en Django.

---

## PROMPT COMPLETO PARA DJANGO

---

### CONTEXTO Y ARQUITECTURA

Necesito implementar integracion con terminales POS Transbank en mi aplicacion Django. La arquitectura es la siguiente:

**IMPORTANTE:** La comunicacion con el POS se realiza 100% desde el NAVEGADOR usando Web Serial API (JavaScript). El backend Django solo se usa para guardar transacciones en la base de datos.

```
┌─────────────────────────────────────────────────────────────────┐
│                        NAVEGADOR (Chrome/Edge)                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  transbank-webserial.js (SDK JavaScript)                 │   │
│  │  - Comunicacion serial directa con POS                   │   │
│  │  - Protocolo: STX + PAYLOAD + ETX + LRC                  │   │
│  │  - Comandos: 0100, 0200, 0250, 0500, 0700, 0800, 1200    │   │
│  └───────────────────────┬─────────────────────────────────┘   │
│                          │ USB Serial                           │
│                          ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Terminal POS (Verifone VX520/VX680 o Ingenico DESK)     │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                          │
                          │ AJAX (guardar transaccion)
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     BACKEND DJANGO                              │
│  - Solo guarda transacciones en BD                              │
│  - NO comunica con POS directamente                             │
└─────────────────────────────────────────────────────────────────┘
```

---

### ARCHIVOS A CREAR

#### 1. SDK JavaScript Principal: `static/js/transbank-webserial.js`

```javascript
/**
 * TRANSBANK POS SDK UNIVERSAL - Web Serial API
 * Compatible con: Verifone VX520/VX675/VX680 + Ingenico iCT220/DESK3500/Move
 */
(function(global) {
    'use strict';

    // CONSTANTES DEL PROTOCOLO
    const STX = 0x02;
    const ETX = 0x03;
    const ACK = 0x06;
    const NAK = 0x15;
    const TBKPOS_DEFAULT_BAUD = 115200;

    // DETECTOR DE DISPOSITIVOS POR USB VID/PID
    const POS_DEVICES = {
        verifone: {
            name: 'Verifone',
            vids: [0x11CA, 0x2504],
            models: {
                0x0215: 'VX520',
                0x0220: 'VX520 Ethernet',
                0x0222: 'VX680',
                0x0219: 'VX675'
            }
        },
        ingenico: {
            name: 'Ingenico',
            vids: [0x0B00, 0x079B],
            models: {
                0x0053: 'iCT220',
                0x0054: 'DESK/3500',
                0x0064: 'Move/2500'
            }
        }
    };

    class UniversalPOS {
        constructor() {
            this.port = null;
            this.reader = null;
            this.keepReading = false;
            this.currentResolver = null;
            this.timeoutId = null;
            this.buffer = [];
            this.debug = true;
            this.deviceInfo = null;
            this.isConnected = false;
        }

        log(msg, data = null) {
            if (this.debug) {
                console.log('[TBK-Universal] ' + msg, data || '');
            }
        }

        // CONSTRUCCION DE COMANDOS
        buildCommand(cmd, params = []) {
            let payload = cmd;
            if (params.length > 0) {
                payload += '|' + params.join('|');
            }
            let msg = String.fromCharCode(STX) + payload + String.fromCharCode(ETX);
            let lrc = 0;
            for (let i = 1; i < msg.length; i++) {
                lrc ^= msg.charCodeAt(i);
            }
            msg += String.fromCharCode(lrc);
            const encoder = new TextEncoder();
            return encoder.encode(msg);
        }

        // PARSEO DE RESPUESTAS
        parseResponse(buffer) {
            let stxIndex = buffer.indexOf(STX);
            if (stxIndex === -1) {
                return { success: false, responseCode: -1, responseMessage: 'Sin STX' };
            }

            let etxIndex = buffer.indexOf(ETX, stxIndex);
            if (etxIndex === -1) {
                return { success: false, responseCode: -1, responseMessage: 'Trama incompleta' };
            }

            const payloadBytes = buffer.slice(stxIndex + 1, etxIndex);
            let payload = '';
            for (let byte of payloadBytes) {
                payload += String.fromCharCode(byte);
            }

            const parts = payload.split('|');
            const msgId = parts[0] || '';

            const response = {
                success: false,
                responseCode: -1,
                responseMessage: 'Error desconocido',
                raw: payload,
                fields: parts,
                command: msgId
            };

            if (parts.length >= 2) {
                response.responseCode = parseInt(parts[1], 10) || -1;
                response.success = (response.responseCode === 0);

                // Parsear respuesta de venta (0210)
                if (msgId === '0210') {
                    response.commerceCode = parts[2] || '';
                    response.terminal = parts[3] || '';
                    response.ticket = parts[4] || '';
                    response.authorizationCode = parts[5] || '';
                    response.amount = parseInt(parts[6], 10) || 0;
                    response.sharesNumber = parseInt(parts[7], 10) || 0;
                    response.last4Digits = parts[8] || '';
                    response.cardType = parts[9] || 'DB';
                    response.responseMessage = response.success ? 'APROBADA' : this.getErrorMessage(response.responseCode);
                }
            }

            return response;
        }

        getErrorMessage(code) {
            const errors = {
                5: 'TRANSACCION RECHAZADA',
                51: 'FONDOS INSUFICIENTES',
                54: 'TARJETA VENCIDA',
                70: 'ERROR INICIALIZACION',
                88: 'SIN CONEXION TRANSBANK',
                99: 'OPERACION CANCELADA'
            };
            return errors[code] || 'ERROR CODIGO ' + code;
        }

        // APERTURA DE PUERTO
        async openPort(puerto, baudRate) {
            this.buffer = [];
            this.port = puerto;

            await this.port.open({
                baudRate: baudRate,
                dataBits: 8,
                stopBits: 1,
                parity: 'none',
                bufferSize: 16384,
                flowControl: 'none'
            });

            // Detectar dispositivo
            const info = puerto.getInfo();
            if (info && info.usbVendorId) {
                this.deviceInfo = this.detectDevice(info.usbVendorId, info.usbProductId);
                localStorage.setItem('tbk_device_info', JSON.stringify(this.deviceInfo));
            }

            this.keepReading = true;
            this.isConnected = true;
            this.readLoop();
        }

        detectDevice(vid, pid) {
            for (const [brand, data] of Object.entries(POS_DEVICES)) {
                if (data.vids.includes(vid)) {
                    return {
                        brand: data.name,
                        model: data.models[pid] || 'Modelo desconocido',
                        vid: vid,
                        pid: pid
                    };
                }
            }
            return { brand: 'Generico', model: 'POS', vid, pid };
        }

        // LOOP DE LECTURA
        async readLoop() {
            while (this.port && this.port.readable && this.keepReading) {
                this.reader = this.port.readable.getReader();
                try {
                    while (true) {
                        const { value, done } = await this.reader.read();
                        if (done) break;
                        if (value) {
                            this.buffer.push(...Array.from(value));
                            this.checkBuffer();
                        }
                    }
                } catch (error) {
                    console.error('[TBK] Error lectura:', error);
                } finally {
                    this.reader.releaseLock();
                }
            }
        }

        checkBuffer() {
            // Remover ACKs
            while (this.buffer.includes(ACK)) {
                this.buffer.splice(this.buffer.indexOf(ACK), 1);
            }

            let stxIndex = this.buffer.indexOf(STX);
            if (stxIndex !== -1) {
                let etxIndex = this.buffer.indexOf(ETX, stxIndex);
                if (etxIndex !== -1 && this.buffer.length > etxIndex + 1) {
                    const frame = this.buffer.slice(stxIndex, etxIndex + 2);
                    this.buffer = this.buffer.slice(etxIndex + 2);
                    const parsed = this.parseResponse(frame);

                    if (this.currentResolver) {
                        clearTimeout(this.timeoutId);
                        this.currentResolver(parsed);
                        this.currentResolver = null;
                    }
                    this.sendAck();
                }
            }
        }

        async sendAck() {
            if (!this.port || !this.port.writable) return;
            const writer = this.port.writable.getWriter();
            await writer.write(new Uint8Array([ACK]));
            writer.releaseLock();
        }

        // ENVIO DE COMANDOS
        async send(cmd, params = []) {
            if (!this.isConnected) throw new Error("POS no conectado");

            this.buffer = [];
            const commandBytes = this.buildCommand(cmd, params);

            const writer = this.port.writable.getWriter();
            await writer.write(commandBytes);
            writer.releaseLock();

            return new Promise((resolve) => {
                this.currentResolver = resolve;
                let timeoutMs = cmd === '0200' ? 180000 : 15000;
                this.timeoutId = setTimeout(() => {
                    if (this.currentResolver) {
                        this.currentResolver({
                            success: false,
                            responseCode: -1,
                            responseMessage: 'Timeout'
                        });
                        this.currentResolver = null;
                    }
                }, timeoutMs);
            });
        }

        // CONEXION
        async connect(baudRate = 115200) {
            if (!navigator.serial) throw new Error("Web Serial API no soportada");

            let puertos = await navigator.serial.getPorts();
            if (puertos.length === 0) {
                const puerto = await navigator.serial.requestPort();
                await this.openPort(puerto, baudRate);
            } else {
                for (const puerto of puertos) {
                    try {
                        await this.openPort(puerto, baudRate);
                        const poll = await this.send('0100');
                        if (poll && poll.responseCode === 0) break;
                    } catch (e) {
                        continue;
                    }
                }
            }

            return {
                connected: true,
                port: this.describePort(this.port),
                device: this.deviceInfo
            };
        }

        async disconnect() {
            this.keepReading = false;
            this.isConnected = false;
            if (this.reader) {
                try { await this.reader.cancel(); } catch (e) {}
                this.reader = null;
            }
            if (this.port) {
                try { await this.port.close(); } catch (e) {}
                this.port = null;
            }
        }

        describePort(puerto) {
            if (!puerto) return 'Puerto Serial';
            const info = puerto.getInfo();
            if (info && info.usbVendorId) {
                return 'USB ' + info.usbVendorId.toString(16) + ':' + info.usbProductId.toString(16);
            }
            return 'Puerto Serial';
        }

        // API PUBLICA
        async poll() { return await this.send('0100'); }
        async loadKeys() { return await this.send('0800'); }
        async sale(amount, ticket) {
            const monto = parseInt(amount, 10);
            if (monto < 50) return { success: false, responseMessage: 'Monto minimo $50' };
            const ticketClean = String(ticket).replace(/[^A-Za-z0-9]/g, '').substring(0, 6).toUpperCase();
            return await this.send('0200', [monto, ticketClean, '', '', '', '', '']);
        }
        async lastSale() { return await this.send('0250'); }
        async getTotals() { return await this.send('0700'); }
        async refund(opId) { return await this.send('1200', [opId]); }
        async closeDay() { return await this.send('0500'); }
        async getSalesDetail(print = true) { return await this.send('0260', [print ? '0' : '1']); }
    }

    // INSTANCIA GLOBAL
    const posInstance = new UniversalPOS();

    // EXPORTAR API
    global.Transbank = global.Transbank || {};
    global.Transbank.POS = global.Transbank.POS || {};
    global.Transbank.POS.Integrado = {
        autoConnect: (baud) => posInstance.connect(baud || TBKPOS_DEFAULT_BAUD),
        disconnect: () => posInstance.disconnect(),
        poll: () => posInstance.poll(),
        loadKeys: () => posInstance.loadKeys(),
        sale: (amount, ticket) => posInstance.sale(amount, ticket),
        lastSale: () => posInstance.lastSale(),
        getTotals: () => posInstance.getTotals(),
        refund: (opId) => posInstance.refund(opId),
        closeDay: () => posInstance.closeDay(),
        getSalesDetail: (print) => posInstance.getSalesDetail(print)
    };

    console.log('Transbank POS SDK cargado');
})(window);
```

---

#### 2. Modelo Django: `transbank/models.py`

```python
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class TransaccionPOS(models.Model):
    TIPO_CHOICES = [
        ('VENTA', 'Venta'),
        ('ANULACION', 'Anulacion'),
        ('CIERRE', 'Cierre'),
    ]
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('APROBADA', 'Aprobada'),
        ('RECHAZADA', 'Rechazada'),
        ('ANULADA', 'Anulada'),
    ]

    monto = models.DecimalField(max_digits=12, decimal_places=2)
    tipo_transaccion = models.CharField(max_length=20, choices=TIPO_CHOICES, default='VENTA')
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='PENDIENTE')
    codigo_autorizacion = models.CharField(max_length=20, blank=True, null=True)
    numero_operacion = models.CharField(max_length=50, blank=True, null=True)
    tipo_tarjeta = models.CharField(max_length=50, blank=True, null=True)
    ultimos_4_digitos = models.CharField(max_length=4, blank=True, null=True)
    numero_cuotas = models.IntegerField(default=1)
    codigo_comercio = models.CharField(max_length=20, blank=True, null=True)
    terminal_id = models.CharField(max_length=20, blank=True, null=True)
    ticket_referencia = models.CharField(max_length=100, blank=True, null=True)
    usuario = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    respuesta_completa = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'transacciones_pos'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tipo_transaccion} ${self.monto} - {self.estado}"
```

---

#### 3. Vista Django: `transbank/views.py`

```python
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
import json
from .models import TransaccionPOS

@login_required
def pos_index(request):
    """Vista principal del modulo POS"""
    return render(request, 'transbank/pos_index.html')

@csrf_exempt
@require_http_methods(["POST"])
@login_required
def guardar_venta(request):
    """Guardar transaccion procesada por Web Serial API"""
    try:
        data = json.loads(request.body)
        
        transaccion = TransaccionPOS.objects.create(
            monto=data.get('monto', 0),
            tipo_transaccion='VENTA',
            estado='APROBADA' if data.get('success') else 'RECHAZADA',
            codigo_autorizacion=data.get('authorizationCode', ''),
            numero_operacion=data.get('operationNumber', ''),
            tipo_tarjeta=data.get('cardType', ''),
            ultimos_4_digitos=data.get('last4Digits', ''),
            numero_cuotas=data.get('sharesNumber', 1),
            codigo_comercio=data.get('commerceCode', ''),
            terminal_id=data.get('terminal', ''),
            ticket_referencia=data.get('ticket', ''),
            usuario=request.user,
            respuesta_completa=data
        )

        return JsonResponse({
            'success': True,
            'id': transaccion.id,
            'mensaje': 'Transaccion guardada'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
```

---

#### 4. URLs Django: `transbank/urls.py`

```python
from django.urls import path
from . import views

app_name = 'transbank'

urlpatterns = [
    path('pos/', views.pos_index, name='pos_index'),
    path('api/guardar-venta/', views.guardar_venta, name='guardar_venta'),
]
```

---

#### 5. Template Django: `templates/transbank/pos_index.html`

```html
{% extends 'base.html' %}
{% load static %}

{% block content %}
<meta name="csrf-token" content="{{ csrf_token }}">

<style>
.status-badge {
    display: inline-block;
    padding: 10px 24px;
    border-radius: 25px;
    font-weight: 600;
}
.badge-success { background: #10b981; color: white; }
.badge-danger { background: #ef4444; color: white; }
.log-container {
    background: #1a202c;
    color: #68d391;
    padding: 20px;
    border-radius: 8px;
    font-family: monospace;
    font-size: 13px;
    max-height: 400px;
    overflow-y: auto;
}
</style>

<div class="container-fluid">
    <!-- Header -->
    <div class="card mb-4" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
        <div class="card-body text-center py-4">
            <h2 class="text-white">Transbank POS</h2>
            <span class="status-badge badge-danger" id="pos-state">DESCONECTADO</span>
            <p class="text-white mt-2" id="port-name">Puerto: Ninguno</p>
        </div>
    </div>

    <div class="row">
        <div class="col-lg-8">
            <!-- Conexion -->
            <div class="card mb-3">
                <div class="card-header bg-primary text-white">Conexion</div>
                <div class="card-body">
                    <button onclick="conectar()" class="btn btn-success w-100 mb-2" id="btnConectar">
                        Auto-Conectar POS
                    </button>
                    <button onclick="desconectar()" class="btn btn-danger w-100" id="btnDesconectar" disabled>
                        Desconectar
                    </button>
                </div>
            </div>

            <!-- Operaciones (oculto hasta conectar) -->
            <div id="operations-panel" style="display: none;">
                <!-- Cargar Llaves -->
                <div class="card mb-3">
                    <div class="card-header bg-warning">Cargar Llaves</div>
                    <div class="card-body">
                        <div class="alert alert-warning">
                            Ejecutar 1 vez al dia. Tarda 30-60 segundos.
                        </div>
                        <button onclick="cargarLlaves()" class="btn btn-warning w-100">
                            Cargar Llaves
                        </button>
                    </div>
                </div>

                <!-- Venta -->
                <div class="card mb-3">
                    <div class="card-header bg-success text-white">Procesar Venta</div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label>Monto ($):</label>
                                <input type="number" id="monto-venta" class="form-control" value="1000" min="50">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label>Ticket:</label>
                                <input type="text" id="ticket-venta" class="form-control" value="TKT-001">
                            </div>
                        </div>
                        <button onclick="procesarVenta()" class="btn btn-success btn-lg w-100">
                            Procesar Venta
                        </button>
                    </div>
                </div>

                <!-- Cierre -->
                <div class="card mb-3">
                    <div class="card-header bg-dark text-white">Cierre de Dia</div>
                    <div class="card-body">
                        <button onclick="cerrarDia()" class="btn btn-dark w-100">
                            Cerrar Dia
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- Logs -->
        <div class="col-lg-4">
            <div class="card">
                <div class="card-header bg-dark text-white">Logs</div>
                <div class="card-body p-0">
                    <div class="log-container" id="log-container">
                        <div>Sistema listo...</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script src="{% static 'js/transbank-webserial.js' %}"></script>
<script>
var pos = null;
var posConectado = false;

function log(msg, tipo) {
    var container = document.getElementById('log-container');
    var div = document.createElement('div');
    div.style.color = tipo === 'error' ? '#fc8181' : tipo === 'success' ? '#68d391' : '#63b3ed';
    div.textContent = '[' + new Date().toLocaleTimeString() + '] ' + msg;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function actualizarEstado(conectado) {
    posConectado = conectado;
    document.getElementById('pos-state').className = 'status-badge ' + (conectado ? 'badge-success' : 'badge-danger');
    document.getElementById('pos-state').textContent = conectado ? 'CONECTADO' : 'DESCONECTADO';
    document.getElementById('btnConectar').disabled = conectado;
    document.getElementById('btnDesconectar').disabled = !conectado;
    document.getElementById('operations-panel').style.display = conectado ? 'block' : 'none';
}

async function conectar() {
    try {
        log('Conectando...', 'info');
        pos = Transbank.POS.Integrado;
        var result = await pos.autoConnect();
        if (result.connected) {
            log('Conectado a ' + result.port, 'success');
            document.getElementById('port-name').textContent = 'Puerto: ' + result.port;
            actualizarEstado(true);
        }
    } catch (e) {
        log('Error: ' + e, 'error');
    }
}

async function desconectar() {
    await pos.disconnect();
    log('Desconectado', 'success');
    actualizarEstado(false);
}

async function cargarLlaves() {
    log('Cargando llaves...', 'info');
    var result = await pos.loadKeys();
    if (result.success) {
        log('Llaves cargadas', 'success');
    } else {
        log('Error: ' + result.responseMessage, 'error');
    }
}

async function procesarVenta() {
    var monto = parseInt(document.getElementById('monto-venta').value);
    var ticket = document.getElementById('ticket-venta').value;

    log('Procesando venta: $' + monto, 'info');
    var result = await pos.sale(monto, ticket);

    if (result.success) {
        log('APROBADA - Auth: ' + result.authorizationCode, 'success');
        
        // Guardar en BD
        fetch('/transbank/api/guardar-venta/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content
            },
            body: JSON.stringify({
                monto: monto,
                ticket: ticket,
                success: true,
                ...result
            })
        });
    } else {
        log('RECHAZADA: ' + result.responseMessage, 'error');
    }
}

async function cerrarDia() {
    if (!confirm('Cerrar dia?')) return;
    log('Cerrando dia...', 'info');
    var result = await pos.closeDay();
    log(result.success ? 'Cierre exitoso' : 'Error en cierre', result.success ? 'success' : 'error');
}

document.addEventListener('DOMContentLoaded', function() {
    log('Sistema iniciado', 'success');
});
</script>
{% endblock %}
```

---

### COMANDOS TRANSBANK POS INTEGRADO

| Comando | Descripcion | Parametros |

|---------|-------------|------------|

| 0100 | POLL - Verificar conexion | Ninguno |

| 0200 | VENTA | monto, ticket, empleado, operacion, cuotas, propina |

| 0250 | ULTIMA VENTA | Ninguno |

| 0260 | DETALLE DE VENTAS | 0=imprimir, 1=solo datos |

| 0500 | CIERRE DE DIA | Ninguno |

| 0700 | TOTALES | Ninguno |

| 0800 | CARGA DE LLAVES | Ninguno |

| 1200 | ANULACION | operation_id |

---

### FORMATO DEL PROTOCOLO

```
STX (0x02) + PAYLOAD + ETX (0x03) + LRC

Donde PAYLOAD = COMANDO|PARAM1|PARAM2|...

Ejemplo venta:
0x02 + "0200|15000|TKT001||||" + 0x03 + LRC

LRC = XOR de todos los bytes despues de STX hasta ETX inclusive
```

---

### RESPUESTA DE VENTA (0210)

```
0210|RESPONSE_CODE|COMMERCE_CODE|TERMINAL_ID|TICKET|AUTH_CODE|AMOUNT|SHARES|LAST4|CARD_TYPE|...

Campos:
- [0] Command ID: 0210
- [1] Response Code: 0=OK, 70=Error inicializacion, etc.
- [2] Commerce Code
- [3] Terminal ID
- [4] Ticket
- [5] Authorization Code (VOUCHER)
- [6] Amount
- [7] Shares (cuotas)
- [8] Last 4 digits
- [9] Card Type: DB=Debito, CR=Credito
```

---

### REQUISITOS

1. **Navegador**: Chrome 89+ o Edge 89+ (requiere Web Serial API)
2. **HTTPS**: Obligatorio en produccion (localhost es excepcion)
3. **Terminal POS**: Verifone VX520/VX680 o Ingenico DESK/3500
4. **Bau