# API Ventas Internet — Conciliación Ecommerce (RetailMind)

**Endpoint:** `GET /api/external/ventas/`
**Base:** `https://retail.webappsolutions.cl/api/external/ventas/`
**Auth:** API Key en header (confirmar nombre con backend; placeholder `Authorization: Bearer <key>`).

Lista boletas/facturas emitidas para conciliar contra pedidos de marketplace. Cada doc tiene `origen`: `ecommerce` (nació de pedido marketplace) o `pos` (venta tienda).

## Query params

| Param | Oblig. | Notas |
|-------|--------|-------|
| `rut_empresa` | **Sí** | `76104936-4` (sin puntos, con guión). Separa tiendas (realsport.cl vs calzadospaola.cl). |
| `fecha` | Cond.* | `YYYY-MM-DD` día exacto. |
| `fecha_desde` / `fecha_hasta` | Cond.* | Rango, **máx 31 días**. |
| `origen` | No | `ecommerce` (solo internet) o `pos`. |
| `referencia` | No | Busca pedido por `numero_pedido_canal`, `correlativo`, `numero_ticket_rm`, `numero_pedido_origen` **o el voucher del pago internet** (boletas POS/migradas). Si la usas, fecha no es obligatoria. |
| `tipo_documento` | No | `BOLETA_ELECTRONICA`/`BOLETA_PAPEL`/`FACTURA_ELECTRONICA`/`FACTURA_EXENTA`. |
| `page` / `page_size` | No | Default 100, **máx 500**. Offset, sin cursor. |

\* Requiere fecha **o** `referencia`.

## Respuesta

```json
{ "success": true, "total": 45, "page": 1, "page_size": 500, "data": [...], "error": null }
```
`total` = docs tras filtros (antes de paginar). Páginas a iterar = `ceil(total / page_size)`.

### Campos de cada doc en `data`

| Campo | Descripción |
|-------|-------------|
| `numero_documento` | Folio boleta/factura. |
| `tipo_documento` | Tipo (espacios → `_`). |
| `fecha_emision` | ISO 8601 TZ Chile. |
| `monto_total` | CLP int (con IVA). |
| `origen` | `ecommerce` = venta INTERNET (cualquiera de 3 señales: pedido ecommerce, ticket módulo ECOMMERCE, **o pago "Venta por Internet"** — cubre boletas POS vendedor 1000 y data migrada de Laravel). `pos` = venta presencial en tienda. |
| `via_emision` | `ecommerce` (facturada desde `/app/ecommerce/pedidos/`) o `pos` (emitida a mano). Conserva la semántica vieja de `origen`. |
| `plataforma_pago` | Plataforma del pago internet: `Paris`, `Falabella`, `Mercado Pago`… null si no hay pago internet. |
| `canal_origen` | Marketplace: `SHOPIFY`/`PARIS`/`RIPLEY`/`WALMART`/`OTRO`. Sale del pedido ecommerce o, si no hay, derivado de `plataforma_pago`. null si venta presencial. |
| `numero_ticket_rm` | ID único RM (`RM-000045`). Cruce más confiable. null si no hay PedidoEcommerce. |
| `referencia_externa` | ID del pedido en el marketplace. En boletas POS/migradas cae al `voucher` del pago internet (el POS lo exige al emitir). |
| `folio_despacho` | Folio etiqueta AllConnected (`RE30005376`). |
| `anulada` / `nota_credito` | `true` + folio NC si fue anulada (igual se incluye). |
| `sucursal` / `sucursal_nombre` | Alias y nombre. |
| `usuario_emisor` | Responsable/vendedor. |

## Distinguir marketplace

- **Internet vs tienda:** campo `origen` (filtra `?origen=ecommerce`). Incluye las ventas internet facturadas a mano por el POS y las históricas migradas (misma definición que el Reporte de Ventas Internet de RM).
- **Qué marketplace:** campo `canal_origen` (`SHOPIFY`/`PARIS`/`RIPLEY`/`WALMART`/`OTRO`).
- **realsport.cl / calzadospaola.cl:** son empresas distintas → separa por `rut_empresa`, NO por `canal_origen` (canal = plataforma técnica, no dominio).
- **Cruce contra marketplace:** usa `numero_ticket_rm` (más confiable) o `referencia_externa` (en boletas POS/migradas trae el voucher = N° de pedido del marketplace).
- **Vía de facturación:** `via_emision` distingue módulo ecommerce vs emisión manual, sin perder la clasificación internet.

## Ejemplos

Ventas internet de un día:
```
GET /api/external/ventas/?rut_empresa=76104936-4&fecha=2026-06-12&origen=ecommerce&page_size=500
```
Buscar por ID de pedido:
```
GET /api/external/ventas/?rut_empresa=76104936-4&referencia=2024-000456
```

Python con paginación:
```python
import math, requests
BASE = "https://retail.webappsolutions.cl/api/external/ventas/"

def traer_ventas_internet(rut_empresa, fecha, api_key):
    h = {"Authorization": f"Bearer {api_key}"}  # confirmar header real
    p = {"rut_empresa": rut_empresa, "fecha": fecha,
         "origen": "ecommerce", "page_size": 500, "page": 1}
    r = requests.get(BASE, headers=h, params=p, timeout=30).json()
    docs, total = list(r["data"]), r["total"]
    for page in range(2, math.ceil(total / 500) + 1):
        p["page"] = page
        docs += requests.get(BASE, headers=h, params=p, timeout=30).json()["data"]
    return docs
```

## Reglas

- `rut_empresa` siempre obligatorio; fecha o `referencia` obligatorio.
- Rango máx 31 días; `page_size` máx 500.
- Sin `origen` vienen ecommerce + POS mezclados.
- Anulados se incluyen (`anulada=true`).
- Notas de Crédito NO son filas: aparecen en `nota_credito` del doc afectado.

_Backend: `app/api/external/views.py:1300` (`VentasView`), ruta en `app/api/external/urls.py:32`._
