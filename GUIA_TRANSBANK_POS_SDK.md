# 📱 Guía de Integración Transbank POS SDK en Django

## 🎯 Descripción

Integración completa del SDK de Transbank POS Integrado en Django para procesar pagos con terminal POS físico. API REST funcional **sin persistencia en base de datos**.

---

## 📦 Instalación

### 1. Instalar dependencias

```bash
pip install transbank-pos-sdk djangorestframework
```

### 2. Verificar instalación

Las dependencias ya están configuradas en el proyecto:
- ✅ `rest_framework` agregado a `INSTALLED_APPS`
- ✅ `REST_FRAMEWORK` configurado en settings
- ✅ URLs configuradas en `/app/pos/transbank/`

---

## 🏗️ Estructura Implementada

```
retailmind/app/
├── services/
│   └── transbank_pos_sdk_service.py   # Servicio POS (Singleton)
├── views_transbank_sdk.py              # API REST Views
└── urls.py                             # URLs configuradas
```

### Archivos creados/modificados:

1. **`services/transbank_pos_sdk_service.py`**
   - Clase `POSService` (Singleton)
   - Gestión de conexión serial directa
   - Operaciones POS sin base de datos

2. **`views_transbank_sdk.py`**
   - 12 endpoints REST
   - Manejo de errores TransbankException
   - Respuestas JSON estandarizadas

3. **`urls.py`**
   - URLs en `/app/pos/transbank/*`
   - Integradas con las rutas existentes

4. **`settings.py`**
   - `rest_framework` agregado a INSTALLED_APPS
   - Configuración REST_FRAMEWORK

---

## 🔌 Endpoints Disponibles

### Base URL: `http://localhost:8000/app/pos/transbank/`

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| GET | `/puertos/` | Listar puertos seriales disponibles |
| POST | `/conectar/` | Abrir conexión con POS |
| POST | `/desconectar/` | Cerrar conexión con POS |
| GET | `/verificar/` | Verificar conexión (POLL) |
| POST | `/cargar-llaves/` | Cargar llaves en POS |
| POST | `/venta/` | Procesar venta |
| POST | `/venta-multicodigo/` | Venta con código de comercio |
| GET | `/ultima-venta/` | Consultar última venta |
| POST | `/anular/` | Anular transacción |
| GET | `/totales/` | Consultar totales del día |
| GET | `/detalles/` | Consultar detalles de ventas |
| POST | `/cerrar-dia/` | Cierre de caja |

---

## 🧪 Ejemplos de Uso

### 1. Listar Puertos Disponibles

```bash
curl http://localhost:8000/app/pos/transbank/puertos/
```

**Respuesta:**
```json
{
    "success": true,
    "puertos": ["COM3", "COM4", "/dev/ttyUSB0"]
}
```

---

### 2. Conectar al POS

```bash
curl -X POST http://localhost:8000/app/pos/transbank/conectar/ \
  -H "Content-Type: application/json" \
  -d '{
    "puerto": "COM3",
    "baud_rate": 115200
  }'
```

**Respuesta:**
```json
{
    "success": true,
    "conectado": true,
    "puerto": "COM3",
    "baud_rate": 115200
}
```

**Puertos comunes:**
- **Windows:** `COM3`, `COM4`, `COM5`
- **Linux:** `/dev/ttyUSB0`, `/dev/ttyS0`
- **macOS:** `/dev/tty.usbserial-*`

---

### 3. Verificar Conexión (POLL)

```bash
curl http://localhost:8000/app/pos/transbank/verificar/
```

**Respuesta:**
```json
{
    "success": true,
    "conectado": true
}
```

---

### 4. Cargar Llaves

⚠️ **Importante:** Ejecutar 1 vez al día o tras conectar el terminal.

```bash
curl -X POST http://localhost:8000/app/pos/transbank/cargar-llaves/
```

**Respuesta:**
```json
{
    "success": true,
    "function_code": 810,
    "response_code": 0,
    "commerce_code": "597020000541",
    "terminal_id": "ABC123"
}
```

---

### 5. Procesar Venta

```bash
curl -X POST http://localhost:8000/app/pos/transbank/venta/ \
  -H "Content-Type: application/json" \
  -d '{
    "monto": 25000,
    "ticket": "TKT001",
    "con_mensajes": false
  }'
```

**Respuesta Exitosa (Aprobada):**
```json
{
    "success": true,
    "function_code": 200,
    "response_code": 0,
    "commerce_code": "597020000541",
    "terminal_id": "ABC123",
    "ticket": "TKT001",
    "authorization_code": "123456",
    "amount": 25000,
    "card_number": "************1234",
    "operation_number": 83,
    "card_type": "CR",
    "installments": 1
}
```

**Respuesta Rechazada:**
```json
{
    "success": false,
    "function_code": 200,
    "response_code": 5,
    "message": "Transacción no autorizada"
}
```

**Códigos de Respuesta:**
- `0` = ✅ Aprobada
- `1-7` = ❌ Rechazada (ver tabla de códigos)
- `96` = ⚠️ Error del sistema POS
- `97` = ⏱️ Timeout

---

### 6. Venta con Mensajes Intermedios

Útil para mostrar progreso al usuario:

```bash
curl -X POST http://localhost:8000/app/pos/transbank/venta/ \
  -H "Content-Type: application/json" \
  -d '{
    "monto": 15000,
    "ticket": "TKT002",
    "con_mensajes": true
  }'
```

**Respuesta con mensajes intermedios:**
```json
{
    "success": true,
    "response_code": 0,
    "authorization_code": "654321",
    "mensajes_intermedios": [
        {
            "mensaje": "Conectando con Transbank...",
            "datos": {...}
        },
        {
            "mensaje": "Esperando tarjeta...",
            "datos": {...}
        },
        {
            "mensaje": "Procesando...",
            "datos": {...}
        }
    ]
}
```

---

### 7. Anular Transacción

⚠️ **Importante:** Guardar `operation_number` de cada venta para anulaciones.

```bash
curl -X POST http://localhost:8000/app/pos/transbank/anular/ \
  -H "Content-Type: application/json" \
  -d '{
    "operation_id": 83
  }'
```

**Respuesta:**
```json
{
    "success": true,
    "function_code": 1200,
    "response_code": 0,
    "authorization_code": "ANU123456",
    "message": "Anulación aprobada"
}
```

---

### 8. Consultar Última Venta

```bash
curl http://localhost:8000/app/pos/transbank/ultima-venta/
```

**Respuesta:**
```json
{
    "success": true,
    "ticket": "TKT001",
    "authorization_code": "123456",
    "amount": 25000,
    "operation_number": 83
}
```

---

### 9. Consultar Totales del Día

```bash
curl http://localhost:8000/app/pos/transbank/totales/
```

**Respuesta:**
```json
{
    "success": true,
    "function_code": 710,
    "response_code": 0,
    "tx_count": 15,
    "tx_total": 450000
}
```

---

### 10. Consultar Detalles

```bash
# Imprimir en caja (default)
curl "http://localhost:8000/app/pos/transbank/detalles/?imprimir_en_pos=false"

# Imprimir en POS
curl "http://localhost:8000/app/pos/transbank/detalles/?imprimir_en_pos=true"
```

---

### 11. Cerrar Día

```bash
curl -X POST http://localhost:8000/app/pos/transbank/cerrar-dia/
```

**Respuesta:**
```json
{
    "success": true,
    "function_code": 510,
    "response_code": 0,
    "message": "Cierre exitoso"
}
```

---

### 12. Desconectar

```bash
curl -X POST http://localhost:8000/app/pos/transbank/desconectar/
```

**Respuesta:**
```json
{
    "success": true,
    "desconectado": true
}
```

---

## 📋 Flujo de Trabajo Completo

### Inicio del Día

```bash
# 1. Listar puertos
curl http://localhost:8000/app/pos/transbank/puertos/

# 2. Conectar
curl -X POST http://localhost:8000/app/pos/transbank/conectar/ \
  -H "Content-Type: application/json" \
  -d '{"puerto": "COM3"}'

# 3. Cargar llaves (obligatorio)
curl -X POST http://localhost:8000/app/pos/transbank/cargar-llaves/

# 4. Verificar conexión
curl http://localhost:8000/app/pos/transbank/verificar/
```

### Venta

```bash
# 1. Procesar venta
curl -X POST http://localhost:8000/app/pos/transbank/venta/ \
  -H "Content-Type: application/json" \
  -d '{"monto": 25000, "ticket": "TKT001"}'

# 2. Guardar operation_number de la respuesta
```

### Anulación (si es necesario)

```bash
curl -X POST http://localhost:8000/app/pos/transbank/anular/ \
  -H "Content-Type: application/json" \
  -d '{"operation_id": 83}'
```

### Cierre del Día

```bash
# 1. Consultar totales
curl http://localhost:8000/app/pos/transbank/totales/

# 2. Cerrar día
curl -X POST http://localhost:8000/app/pos/transbank/cerrar-dia/

# 3. Desconectar
curl -X POST http://localhost:8000/app/pos/transbank/desconectar/
```

---

## ⚠️ Notas Importantes

### Requisitos del POS

1. **Modo Operación:** El POS debe estar en modo "POS Integrado"
2. **Carga de Llaves:** Ejecutar `cargar-llaves/` una vez al día o tras conectar
3. **Operation ID:** Guardar `operation_number` de cada venta para anulaciones

### Montos

- Los montos se envían en **pesos chilenos sin decimales**
- Ejemplo: `25000` = $25.000 CLP

### Puertos

- **Windows:** Generalmente `COM3`, `COM4`, `COM5`
- **Linux:** `/dev/ttyUSB0`, `/dev/ttyS0`
- Verificar con `listar-puertos/` antes de conectar

### Códigos de Respuesta

| Código | Significado |
|--------|-------------|
| `0` | ✅ Operación exitosa |
| `1` | ❌ Tarjeta no válida |
| `2` | ❌ Código de autorización inválido |
| `3` | ❌ Comercio no válido |
| `4` | ❌ Retener tarjeta |
| `5` | ❌ Transacción no autorizada |
| `6` | ❌ Error en la tarjeta |
| `7` | ❌ Retener tarjeta - condiciones especiales |
| `96` | ⚠️ Error del sistema POS |
| `97` | ⏱️ Timeout |

### Timeout

- **Default:** 150 segundos (2.5 minutos)
- Configurable en `POSService.__init__()`

---

## 🔧 Configuración Avanzada

### Cambiar Timeout

Editar `services/transbank_pos_sdk_service.py`:

```python
def __new__(cls):
    if cls._instance is None:
        cls._instance = super().__new__(cls)
        cls._instance.pos = POSIntegrado()
        cls._instance.pos.timeout = 180  # 3 minutos
        # ...
```

### Venta Multicodigo

Si tienes múltiples códigos de comercio:

```bash
curl -X POST http://localhost:8000/app/pos/transbank/venta-multicodigo/ \
  -H "Content-Type: application/json" \
  -d '{
    "monto": 25000,
    "ticket": "TKT001",
    "commerce_code": 597020000541
  }'
```

---

## 🐛 Troubleshooting

### Error: "Puerto no disponible"

**Solución:**
1. Verificar que el POS esté conectado físicamente
2. Ejecutar `listar-puertos/` para ver puertos disponibles
3. Verificar permisos de acceso al puerto serial
4. En Linux: `sudo chmod 666 /dev/ttyUSB0`

### Error: "Error al conectar"

**Solución:**
1. Verificar velocidad de conexión (115200 es estándar)
2. Verificar que otro programa no esté usando el puerto
3. Reiniciar el terminal POS

### Error: "Timeout esperando respuesta"

**Solución:**
1. Verificar que el POS esté encendido
2. Aumentar timeout si la red es lenta
3. Verificar cable USB/Serial

### Transacción Rechazada

**Solución:**
1. Ver `response_code` para identificar el problema
2. Verificar saldo de la tarjeta
3. Verificar que las llaves estén cargadas
4. Consultar con Transbank si el problema persiste

---

## 📚 Recursos Adicionales

- [Documentación Oficial SDK Transbank POS](https://github.com/TransbankDevelopers/transbank-pos-sdk-python)
- [Documentación Django REST Framework](https://www.django-rest-framework.org/)

---

## 🎉 Endpoints Listos

Todos los endpoints están **listos para usar** en:

```
http://localhost:8000/app/pos/transbank/
```

✅ Sin base de datos
✅ Conexión serial directa
✅ Respuestas JSON estandarizadas
✅ Manejo de errores completo
✅ Logging integrado

---

## 💡 Ejemplo de Integración Frontend

```javascript
// Ejemplo con Fetch API
async function procesarVentaPOS(monto, ticket) {
    try {
        const response = await fetch('http://localhost:8000/app/pos/transbank/venta/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                monto: monto,
                ticket: ticket,
                con_mensajes: true
            })
        });
        
        const data = await response.json();
        
        if (data.success && data.response_code === 0) {
            console.log('✅ Venta aprobada');
            console.log('Autorización:', data.authorization_code);
            console.log('Operation ID:', data.operation_number);
        } else {
            console.error('❌ Venta rechazada:', data.error || data.message);
        }
        
        return data;
    } catch (error) {
        console.error('Error procesando venta:', error);
        throw error;
    }
}

// Usar
procesarVentaPOS(25000, 'TKT001')
    .then(result => console.log(result))
    .catch(error => console.error(error));
```

---

## 📝 Notas Finales

- ✅ **Implementación Completa:** Todos los endpoints del prompt están implementados
- ✅ **Sin Base de Datos:** Operaciones en memoria, sin persistencia
- ✅ **Singleton Pattern:** Una sola instancia del servicio POS
- ✅ **Error Handling:** Manejo robusto de excepciones
- ✅ **Logging:** Registro de todas las operaciones
- ✅ **REST Compliant:** APIs siguiendo estándares REST

**¡Listo para producción!** 🚀

