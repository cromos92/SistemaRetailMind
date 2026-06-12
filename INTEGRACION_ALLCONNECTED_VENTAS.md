# RetailMind → AllConnected: API de ventas para conciliación diaria

> **Para**: Claude Code / desarrollo de AllConnected (ecommerce.webappsolutions.cl)
> **De**: RetailMind (retail.webappsolutions.cl)
> **Estado**: ✅ Implementado en RetailMind. Este documento es el contrato definitivo para que AllConnected construya su lado (cliente de conciliación + receptor del webhook).

Responde al requerimiento "API de consulta de ventas/boletas (conciliación diaria)". Hay **dos mecanismos complementarios**, ambos ya implementados en RetailMind:

1. **`GET /api/ventas/`** (pull) — listado de documentos de venta del día con `origen`, anulación y referencias del pedido. Para el reporte de conciliación de las 08:00.
2. **Webhook de facturación** (push) — POST a AllConnected al emitir boleta o al emitir la NC que la anula. **AllConnected debe construir el endpoint receptor** (spec en sección 3).

---

## 1. Autenticación y base

- **Base URL**: la misma de los endpoints `/api/*` que AllConnected ya consume (`/api/skus/`, `/api/precios-actuales/`, etc.).
- **Auth**: `Authorization: Bearer <RETAILMIND_API_KEY>` (la misma key compartida; también se acepta `X-Api-Key: <key>` legacy).
- **401** con key inválida o ausente.
- Multi-empresa por `rut_empresa`: `76104936-4` (realsport) o `78503140-7` (paola).

---

## 2. `GET /api/ventas/`

### Query params

| Param | Tipo | Req. | Descripción |
|---|---|---|---|
| `rut_empresa` | string | ✅ | RUT de la empresa, formato `76104936-4` |
| `fecha` | `YYYY-MM-DD` | ✅* | Documentos emitidos ese día (zona America/Santiago) |
| `fecha_desde` / `fecha_hasta` | `YYYY-MM-DD` | ✅* | Alternativa a `fecha` para rangos. Máx 31 días. Si falta `fecha_hasta`, se usa `fecha_desde` |
| `origen` | string | ❌ | `ecommerce` \| `pos` |
| `referencia` | string | ❌ | Busca por **cualquier** identificador del pedido AllConnected: `numero_pedido_canal`, folio de despacho impreso (`RE30005376`), `numero_ticket_rm` (`RM-XXXXXXXX`) o `numero_pedido_origen` (`MP-000123`). Match exacto case-insensitive. **Si se entrega `referencia`, la fecha pasa a ser opcional** (busca en todo el histórico — cubre el caso "boleta descubierta semanas después") |
| `tipo_documento` | string | ❌ | `BOLETA_ELECTRONICA` \| `BOLETA_PAPEL` \| `FACTURA_ELECTRONICA` \| `FACTURA_EXENTA` |
| `page` / `page_size` | int | ❌ | Paginación. Default 100, máx 500 |

\* uno de los dos modos de fecha es obligatorio, salvo que se entregue `referencia`.

### Response 200

```json
{
  "status": true,
  "success": true,
  "rut_empresa": "76104936-4",
  "fecha": "2026-06-11",
  "fecha_desde": "2026-06-11",
  "fecha_hasta": "2026-06-11",
  "total": 12,
  "page": 1,
  "page_size": 100,
  "data": [
    {
      "numero_documento": "12345",
      "tipo_documento": "BOLETA_ELECTRONICA",
      "fecha_emision": "2026-06-11T10:42:05-04:00",
      "monto_total": 109990,
      "origen": "ecommerce",
      "numero_ticket_rm": "RM-A3F7C2E1",
      "referencia_externa": "ORD-2026-00200",
      "folio_despacho": "RE30005376",
      "canal_origen": "PARIS",
      "anulada": false,
      "nota_credito": null,
      "sucursal": "MATTA",
      "sucursal_nombre": "Matta 2479",
      "usuario_emisor": "caja1"
    },
    {
      "numero_documento": "12346",
      "tipo_documento": "BOLETA_ELECTRONICA",
      "fecha_emision": "2026-06-11T13:05:11-04:00",
      "monto_total": 66980,
      "origen": "pos",
      "numero_ticket_rm": null,
      "referencia_externa": null,
      "folio_despacho": null,
      "canal_origen": null,
      "anulada": true,
      "nota_credito": "871",
      "sucursal": "MATTA",
      "sucursal_nombre": "Matta 2479",
      "usuario_emisor": "caja2"
    }
  ],
  "error": null
}
```

Respuesta vacía: `{"status": true, "success": true, "total": 0, "data": [], ...}`.

### Definición de campos

| Campo | Definición |
|---|---|
| `numero_documento` | **Folio puro como string, sin prefijo** (`"12345"`, no `"B-0012345"`). En RM el folio es un entero; el tipo va aparte. Para deduplicar usar la tupla `(rut_empresa, tipo_documento, numero_documento)` |
| `tipo_documento` | Normalizado con underscore: `BOLETA_ELECTRONICA`, `BOLETA_PAPEL`, `FACTURA_ELECTRONICA`, `FACTURA_EXENTA`. Las notas de crédito **no aparecen como filas**: van en el campo `nota_credito` del documento afectado |
| `fecha_emision` | ISO 8601 con offset de Chile (`-04:00`/`-03:00` según DST). Si RM no registró hora, viene `T00:00:00` |
| `monto_total` | Entero CLP, con IVA |
| `origen` | `"ecommerce"` si el documento nació de un pedido del módulo `/app/ecommerce/pedidos/` (vínculo directo por FK en BD — confiable, no heurístico). `"pos"` para toda emisión manual |
| `numero_ticket_rm` | El `RM-XXXXXXXX` que RM devolvió al ingestar el pedido. Solo vía ecommerce; `null` en POS |
| `referencia_externa` | Vía ecommerce: el `numero_pedido_canal` que AllConnected envió al ingestar. Vía POS: el folio que el cajero haya tipeado como referencia al emitir, si existe (ver sección 4); si no, `null` |
| `folio_despacho` | **Campo extra respecto del spec original**: el folio correlativo que AllConnected imprime en la hoja del pedido (ej. `RE30005376`). RM ya lo recibe en los re-pulls del pedido, así que para la vía ecommerce la conciliación por folio impreso es **exacta y sin cambios en el POS**. `null` si el pedido aún no se imprimía al momento del último pull, o en vía POS |
| `canal_origen` | Marketplace del pedido: `SHOPIFY`, `PARIS`, `RIPLEY`, `WALMART`, `OTRO`. `null` en POS |
| `anulada` | `true` si el documento está en estado ANULADO/CANCELADO en RM **o** tiene una nota de crédito vinculada |
| `nota_credito` | Folio (string) de la NC que afecta al documento; `null` si no tiene. Si hubiera más de una NC se reporta la primera emitida |
| `sucursal` / `sucursal_nombre` | Alias corto (el mismo que entrega `/api/sucursales/`) y nombre legible |
| `usuario_emisor` | Username/responsable que emitió en RM |

### Semántica importante para la conciliación

- **Los documentos anulados SÍ aparecen** en el listado (con `anulada: true`) — la conciliación los necesita. Solo se excluyen documentos descartados (soft-delete) y rechazados por el SII.
- La alerta "pedido cancelado pero la boleta no tiene NC" se detecta con: boleta del pedido existe + `anulada: false`.
- La alerta "boleta emitida pero pedido extraviado" se detecta con: fila con `origen: ecommerce` cuyo `numero_ticket_rm`/`referencia_externa` no cruza con ningún pedido completado en AllConnected, **o** fila `origen: pos` sin match (conciliar por monto+fecha como aproximación).

### Errores

| Código | Caso |
|---|---|
| `401` | Key inválida o ausente |
| `400` | Falta `rut_empresa`; falta modo de fecha (y no hay `referencia`); fecha mal formateada; rango > 31 días; `origen` distinto de `ecommerce`/`pos`; `tipo_documento` inválido; `page`/`page_size` no enteros |

Body de error: `{"status": false, "success": false, "data": [], "total": 0, "error": "<detalle>"}`.

### Ejemplos

```bash
# Conciliación del día anterior, realsport
curl -H "Authorization: Bearer $KEY" \
  "https://<rm-host>/api/ventas/?rut_empresa=76104936-4&fecha=2026-06-11"

# Solo boletas de la vía POS
curl -H "Authorization: Bearer $KEY" \
  "https://<rm-host>/api/ventas/?rut_empresa=76104936-4&fecha=2026-06-11&origen=pos"

# Buscar la boleta de un pedido por su folio impreso (sin fecha: busca en todo el histórico)
curl -H "Authorization: Bearer $KEY" \
  "https://<rm-host>/api/ventas/?rut_empresa=76104936-4&referencia=RE30005376"
```

---

## 3. Webhook de facturación (push) — **AllConnected debe construir el receptor**

RetailMind ya tiene implementado el emisor. Notifica en tiempo real al **emitir** un documento de venta y al **emitir la NC** que lo anula.

### Contrato

```
POST https://ecommerce.webappsolutions.cl/system/webhooks/retailmind/factura/
Header: X-AllConnected-Key: <la misma key compartida del pull de pedidos>
Content-Type: application/json
```

```json
{
  "evento": "boleta_emitida",
  "rut_empresa": "76104936-4",
  "numero_documento": "12345",
  "tipo_documento": "BOLETA_ELECTRONICA",
  "fecha_emision": "2026-06-11T10:42:05-04:00",
  "monto_total": 109990,
  "origen": "ecommerce",
  "numero_ticket_rm": "RM-A3F7C2E1",
  "referencia_externa": "ORD-2026-00200",
  "folio_despacho": "RE30005376",
  "canal_origen": "PARIS",
  "nota_credito": null,
  "sucursal": "MATTA",
  "usuario_emisor": "caja1"
}
```

### Semántica de eventos

- `boleta_emitida`: se emitió un documento de venta (cualquiera de los 4 tipos; el tipo real va en `tipo_documento`). `nota_credito` viene `null`.
- `boleta_anulada`: se emitió una **nota de crédito** que afecta a un documento de venta. El payload describe el **documento original** (mismo `numero_documento` que el evento de emisión) y `nota_credito` trae el folio de la NC. Nota: una anulación de estado sin NC no genera evento — la captura el pull diario vía `anulada: true`.

### Reglas para el receptor

- **Responder 2xx rápido** (ideal < 5 s; RM corta a los 10 s). Procesar async si hace falta.
- **Idempotencia obligatoria**: deduplicar por `(rut_empresa, tipo_documento, numero_documento, evento)`. RM puede reenviar.
- **Reintentos de RM**: 3 intentos con backoff (inmediato, +5 s, +30 s). Si los 3 fallan, RM solo loguea — el pull diario de `GET /api/ventas/` cubre el gap, así que el webhook puede tratarse como best-effort.
- El header de auth es configurable en RM (`ALLCONNECTED_API_HEADER_NAME`, default `X-AllConnected-Key`) — validarlo con la key compartida y responder 401 si no calza.

### Activación

El emisor está **desactivado por defecto** en RM. Cuando el receptor esté desplegado en AllConnected, avisar a RetailMind para setear en su `.env`:

```
ALLCONNECTED_WEBHOOK_FACTURA_ENABLED=true
# opcional, default ya correcto:
ALLCONNECTED_WEBHOOK_FACTURA_PATH=/system/webhooks/retailmind/factura/
```

(Reusa `ALLCONNECTED_API_BASE_URL` y `ALLCONNECTED_API_KEY` ya configurados para el pull de pedidos.)

---

## 4. Respuestas a las preguntas del requerimiento original

1. **¿Endpoint existente?** No existía a nivel documento — `GET /api/ventas/` es nuevo, construido sobre el mismo stack que `/api/movimientos-ventas/`. Este último sigue disponible para detalle a nivel línea de producto (devoluciones).
2. **¿Campo referencia en POS?** El modelo de RM **ya tiene** el campo (`Ticket.referencia_folio`) y tanto la API como el webhook **ya lo exponen** como `referencia_externa` cuando `origen: pos`. Lo que falta es el input en la pantalla de emisión del POS (decisión pendiente en RM: POS web vs cliente desktop NEXO POS). Mientras tanto, boletas POS de pedidos AllConnected llegan con `referencia_externa: null` → conciliar por monto+fecha.
3. **¿NC vinculadas al original?** Sí, con FK directa en BD (`documento_afectado`) — `anulada` y `nota_credito` son confiables, no inferidos.
4. **¿Sucursal y usuario en boleta POS?** Sí, ambos vienen siempre (`sucursal`, `sucursal_nombre`, `usuario_emisor`).
5. **¿Volumen diario?** Decenas de boletas/día por empresa en operación normal — `page_size` default 100 alcanza para un día; para rangos de varios días, paginar.

---

## 5. Criterios de aceptación (estado)

- [x] `GET /api/ventas/?rut_empresa=76104936-4&fecha=2026-06-11` devuelve todos los documentos de venta del día, de ambas vías, con `origen` correcto (resuelto por FK en BD, no heurística).
- [x] Boletas emitidas desde `/app/ecommerce/pedidos/` traen `numero_ticket_rm`, `referencia_externa` y además `folio_despacho`.
- [x] Boleta anulada → `anulada: true` + folio en `nota_credito`.
- [x] Auth con la misma API key Bearer; 401 con key inválida.
- [x] Día normal (~50 boletas) responde en una sola página, muy bajo el límite de 3 s (4 queries acotadas, sin N+1).
- [x] Webhook emisor implementado (pendiente: receptor en AllConnected + activación por env).

---

## 6. Qué construir en AllConnected

1. **Cliente del pull**: en el job de conciliación de las 08:00, llamar `GET /api/ventas/?rut_empresa=<rut>&fecha=<ayer>` por cada empresa y cruzar contra pedidos impresos. Claves de cruce, en orden de precisión: `folio_despacho` → `numero_ticket_rm` → `referencia_externa` (numero_pedido_canal) → monto+fecha (solo `origen: pos` sin referencia).
2. **Receptor del webhook** en `/system/webhooks/retailmind/factura/` según sección 3, y avisar a RM para activar el flag.
3. Tratar `anulada` / `nota_credito` del pull como fuente de verdad (el webhook `boleta_anulada` solo cubre anulación vía NC).
