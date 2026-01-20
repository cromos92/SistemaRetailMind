# Integracion de Pedidos Externos -> RetailMind

## Objetivo
Enviar pedidos desde la otra app a RetailMind para crear boleta automaticamente
usando `codigo_asociado` y precios de internet. El `external_order_id` queda
guardado en la venta.

## Endpoint
**POST** `/api/pedidos/externos/crear-boleta/`

**Headers**
```
Authorization: Token <TOKEN_FIJO>
Content-Type: application/json
```

## Payload (minimo requerido)
```json
{
  "external_order_id": "ORD-123456",
  "sucursal": "Sucursal Centro",
  "alias": "CENTRO",
  "items": [
    {
      "codigo_asociado": "ABC123",
      "cantidad": 2
    },
    {
      "codigo_asociado": "XYZ999",
      "cantidad": 1
    }
  ]
}
```

## Reglas de negocio
- `external_order_id` debe ser unico.
- Cada item se busca por `codigo_asociado`.
- Si hay stock, se descuenta y se crea la boleta.
- Se guarda `external_order_id` en la venta/boleta.

## Respuestas

### Exito
```json
{
  "status": "ok",
  "boleta_id": 9876,
  "external_order_id": "ORD-123456"
}
```

### Error de autenticacion
```json
{
  "status": "error",
  "message": "Token invalido"
}
```

### Error de validacion
```json
{
  "status": "error",
  "message": "Producto no encontrado",
  "detalle": {
    "codigo_asociado": "XYZ999"
  }
}
```

## Opcional (si desean enviar mas datos)
- `cliente` (nombre, documento, email)
- `precios` por item (si no usan precios internos)
- `notas`
