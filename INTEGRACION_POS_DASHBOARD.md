# 🎯 Integración SDK Transbank en POS Dashboard

## Resumen

Para integrar el SDK oficial de Transbank Web Serial en `pos-dashboard`:

---

## 📝 Pasos a Implementar:

### 1. **Reemplazar SDK antiguo por nuevo**

En `generacionVentas.html` línea 4827:

```html
<!-- ANTES (WebSocket - requiere agente): -->
<script src="https://unpkg.com/transbank-pos-sdk-web@3/dist/pos.js"></script>

<!-- AHORA (Web Serial - sin agente): -->
<script src="{% static 'js/transbank-web-serial.js' %}"></script>
```

### 2. **Auto-Conectar al Cargar Página**

Agregar al inicio del JavaScript:

```javascript
// Auto-conectar si hay configuración guardada
window.addEventListener('DOMContentLoaded', async () => {
    {% if config_pos %}
        console.log('🔌 Configuración guardada encontrada');
        console.log('Puerto: {{ config_pos.puerto_conexion }}');
        console.log('Baudrate: {{ config_pos.velocidad_conexion }}');
        
        // Auto-conectar automáticamente
        await autoConectarPOS();
    {% else %}
        console.log('⚠️ Sin configuración guardada - Conectar manualmente');
    {% endif %}
});
```

### 3. **Función Auto-Conectar**

```javascript
async function autoConectarPOS() {
    try {
        const pos = window.Transbank.POS.Integrado;
        const baudrate = {{ config_pos.velocidad_conexion|default:115200 }};
        
        console.log('🚀 Auto-conectando al POS...');
        
        // Obtener puertos autorizados previamente
        const puertos = await pos.getAuthorizedPorts();
        
        if (puertos.length > 0) {
            // Conectar al primer puerto autorizado
            pos.port = puertos[0];
            await pos.openPort({ baudRate: baudrate });
            
            console.log('✅ Auto-conectado al POS');
            return true;
        } else {
            console.log('⚠️ No hay puertos autorizados - Pedir permiso');
            await pos.connect(baudrate);
            
            // Guardar puerto en DB
            await guardarConfiguracionPOS();
        }
    } catch (err) {
        console.error('Error auto-conectando:', err);
        return false;
    }
}
```

### 4. **Guardar Configuración en DB**

```javascript
async function guardarConfiguracionPOS() {
    const csrftoken = getCookie('csrftoken');
    
    await fetch('/app/pos/transbank/autoconectar/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrftoken
        }
    });
    
    console.log('💾 Configuración guardada en DB');
}
```

### 5. **Actualizar función pagarConPOSTransbank()**

```javascript
window.pagarConPOSTransbank = async function() {
    const ticketActual = window.ticketActual;
    
    if (!ticketActual) {
        Swal.fire('Error', 'No hay ticket activo', 'error');
        return;
    }
    
    const saldoPendiente = ticketActual.total - ticketActual.total_pagado;
    
    try {
        const pos = window.Transbank.POS.Integrado;
        
        // Verificar si está conectado
        if (!pos.isOpen) {
            await pos.connect(115200);
        }
        
        // Generar ticket ID
        const ticketPOS = ticketActual.correlativo.toString().padStart(6, '0');
        
        // Procesar venta
        Swal.fire({
            title: 'Procesando Pago',
            html: '<h5>⏳ Pase la tarjeta en el POS...</h5>',
            allowOutsideClick: false,
            didOpen: () => Swal.showLoading()
        });
        
        const resultado = await pos.sale(saldoPendiente, ticketPOS);
        
        Swal.close();
        
        if (resultado.successful) {
            // Guardar pago en ticket
            const metodoPago = resultado.cardType === 'DB' ? 'TBK_DEBITO_POS' : 
                             resultado.cardType === 'CR' ? 'TBK_CREDITO_POS' : 
                             'TBK_POS_INTEGRADO';
            
            await registrarPago({
                metodo_pago: metodoPago,
                monto: resultado.amount,
                tipo_tarjeta: resultado.cardType,
                voucher: resultado.authorizationCode,
                notas: `Operation: ${resultado.operationNumber}`
            });
            
            Swal.fire('✅ Pago Aprobado', 
                     `Autorización: ${resultado.authorizationCode}`, 
                     'success');
        } else {
            Swal.fire('❌ Pago Rechazado', 
                     resultado.responseMessage, 
                     'error');
        }
        
    } catch (err) {
        Swal.close();
        Swal.fire('Error', err.toString(), 'error');
    }
}
```

---

## ✅ Ventajas:

- ✅ **Auto-conecta** al cargar si ya tienes puerto guardado
- ✅ **Guarda** puerto la primera vez que conectas
- ✅ **No necesitas conectar** cada vez que abres pos-dashboard
- ✅ **Funciona en producción** sin agente
- ✅ **Se integra** con el sistema de tickets existente

---

## 🚀 Flujo:

```
1. Usuario abre pos-dashboard
2. Sistema verifica si hay config_pos en DB
3. SI existe → Auto-conecta automáticamente ✅
4. SI no existe → Usuario conecta manualmente → Guarda en DB
5. Próxima vez → Auto-conecta ✅
```

---

¿Quiero que implemente esto en generacionVentas.html?



