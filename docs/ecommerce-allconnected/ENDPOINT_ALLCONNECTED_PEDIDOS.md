# Endpoint AllConnected — Pull de pedidos

> Especificación del endpoint **remoto** que RetailMind consulta para **traer**
> (pull) los pedidos pendientes desde **AllConnected**
> (`VicentAllEcommercesConected`), y del endpoint **interno** que dispara ese pull
> desde la UI.
>
> ⚠️ Este archivo **no** contiene API keys. Las credenciales viven en env vars de
> producción (ver [CREDENCIALES_ECOMMERCE_ALLCONNECTED.md](CREDENCIALES_ECOMMERCE_ALLCONNECTED.md)).

---

## 1. Endpoint REMOTO que consume RetailMind (AllConnected)

Es el que pediste. Lo llama el service
[allconnected_pedidos_service.py](retailmind/app/services/allconnected_pedidos_service.py#L107-L122).

```
GET {ALLCONNECTED_API_BASE_URL}{ALLCONNECTED_PEDIDOS_PATH}?estado=PENDIENTE
```

**En producción (valores por defecto / deducidos):**

```
GET https://ecommerce.webappsolutions.cl/app/pedidos/pendientes/?estado=PENDIENTE
```

### Método y headers

| | Valor |
|---|---|
| **Método** | `GET` |
| **Auth header** | `X-AllConnected-Key: <ALLCONNECTED_API_KEY>` (nombre configurable vía `ALLCONNECTED_API_HEADER_NAME`) |
| `Accept` | `application/json` |
| `User-Agent` | `RetailMind-PedidosPull/1.0` |
| **Timeout** | 90 s lectura (`TIMEOUT_SEGUNDOS` en el service) |

### Query params (los manda RetailMind)

| Param | Obligatorio | Descripción |
|---|---|---|
| `estado` | sí | Siempre `PENDIENTE` (hardcode en el service). |
| `rut_empresa` | opcional | RUT de la empresa de la sesión (`rutEmpresaActual`). Si va, AllConnected filtra por empresa. |
| `desde` | opcional | `YYYY-MM-DD`. Si se omite, AllConnected usa el **mes actual**. |
| `hasta` | opcional | `YYYY-MM-DD`. Idem. |

### Respuesta esperada (200)

RetailMind acepta **dos formas** (ver `_extraer_lista`):

```jsonc
// (a) lista directa
[ { /* pedido */ }, { /* pedido */ } ]

// (b) objeto con clave "pedidos"
{ "pedidos": [ { /* pedido */ } ], "desde": "2026-06-01", "hasta": "2026-06-03" }
```

Cada `{pedido}` debe tener el **mismo shape** que el body del push
`POST /api/ecommerce/pedidos/`:

```jsonc
{
  "numero_pedido_canal": "PAOLA-12345",
  "canal_origen": "PARIS",            // SHOPIFY | PARIS | RIPLEY | WALMART | OTRO ...
  "sucursal_id": 3,
  "cliente_nombre": "Juan Pérez",
  "cliente_documento": "12.345.678-9",
  "items": [
    { "sku": "10234", "cantidad": 2, "producto_talla_id": null }
  ],
  "subtotal": 19990,
  "descuento": 0,
  "costo_envio": 2990,
  "total": 22980,
  "rut_empresa": "76.123.456-7"
}
```

### Shape REAL observado en producción (2026-06-03)

NO pagina. Devuelve **todo en una sola respuesta**, un dict:

```jsonc
{
  "ok": true,
  "pedidos": [ { /* ... */ } ],   // los que SÍ entrega
  "total": 132,                   // = len(pedidos), NO es total de páginas
  "omitidos": 123,                // ⚠️ pedidos que AllConnected DESCARTÓ y NO envía
  "desde": "2026-06-01",
  "hasta": "2026-06-03"
}
```

### ⚠️ Por qué "no trae algunos pedidos" (verificado, NO es paginación)

Tres causas, en orden de impacto:

1. **`omitidos` (causa principal).** AllConnected **descarta pedidos en su propio
   lado** antes de enviarlos. Medido el 2026-06-03 (junio, mes en curso):

   | Filtro | entregados (`total`) | `omitidos` |
   |---|---|---|
   | sin rut | 132 | **123** (~48% descartado) |
   | rut `76104936-4` | 87 | 24 |
   | rut `78503140-7` | 45 | **99** |
   | mayo 2026 (sin rut) | 92 | **317** |

   Esos `omitidos` **nunca llegan a RetailMind**. La causa está en AllConnected
   (`ecommerce.webappsolutions.cl`). **Sondeo confirmado (2026-06-03):** los 132
   pedidos que SÍ entrega tienen **todos** `sucursal_id`, `items` y `rut_empresa`
   (132/132). Los `omitidos` son los que AllConnected **no logra mapear/completar**
   a ese shape: sin sucursal RetailMind asignable, sin empresa (`rut_empresa`)
   resuelta, o sin items/SKU mapeables. Es el mismo tipo de validación que
   `_ingestar_pedido_dict` (exige `numero_pedido_canal`, `canal_origen`,
   `sucursal_id`, `cliente_nombre`, y que la sucursal pertenezca a la empresa):
   AllConnected los descarta de antemano. El endpoint solo da el **número**, no la
   lista ni el motivo, y **`estado`/`debug`/`incluir_omitidos` son ignorados** (no
   hay modo verbose) → para ver el detalle hay que mirar los **logs/datos de
   AllConnected**, no RetailMind.

2. **El filtro `rut_empresa`.** El botón manda `request.session['rutEmpresaActual']`.
   Si no coincide **exacto** con el rut que AllConnected tiene en el pedido, devuelve
   0. Ruts con pendientes hoy: `76104936-4` (87) y `78503140-7` (45). El rut
   `75503140-7` devuelve **0** (no existe ahí).

3. **Rango de fechas.** Por defecto AllConnected usa el **mes en curso**. Pedidos de
   meses anteriores no vienen salvo que mandes `desde`/`hasta` (mayo tenía 92
   pendientes adicionales).

> Nota: el service `traer_pedidos_pendientes()` **hoy ignora el campo `omitidos`** —
> el operador no se entera de que AllConnected descartó pedidos. Surfacearlo en el
> resultado/toast es una mejora recomendada.

---

## 2. Endpoint INTERNO de RetailMind (botón "Traer pedidos")

Lo llama el front; este a su vez dispara el GET remoto de arriba.

| | Valor |
|---|---|
| **URL** | `POST /app/ecommerce/pedidos/traer/` (`name='traer_pedidos_allconnected'`) |
| **Vista** | [views_ecommerce.py → `traer_pedidos_allconnected`](retailmind/app/views_ecommerce.py#L546) |
| **Método** | **solo POST** (otro método → `405`) |
| **Auth** | `@login_required` + CSRF (`X-CSRFToken`). Permiso `ecommerce_pedidos_todos` / `puede_crear` (si falta → `403`) |
| **Body** (opcional) | `{ "desde": "YYYY-MM-DD", "hasta": "YYYY-MM-DD" }` |

### Respuesta

```jsonc
{
  "ok": true,
  "configurado": true,            // false si ALLCONNECTED_API_BASE_URL está vacío (pull off)
  "total": 42,                    // pedidos que vinieron en la respuesta remota
  "nuevos": 5,
  "ya_existian": 37,
  "errores": [ { "indice": 3, "numero_pedido_canal": "...", "error": "..." } ],
  "desde": "2026-06-01",
  "hasta": "2026-06-03",
  "detalle": "5 nuevos, 37 ya existían, 0 con error."
}
```

---

## 3. Variables de entorno (producción)

Definidas en [settings.py:634-637](retailmind/retailmind/settings.py#L634-L637):

| Variable | Default | Descripción |
|---|---|---|
| `ALLCONNECTED_API_BASE_URL` | `''` (vacío = pull off) | Host de AllConnected, ej. `https://ecommerce.webappsolutions.cl` |
| `ALLCONNECTED_API_KEY` | `''` | Key de auth saliente (header) |
| `ALLCONNECTED_API_HEADER_NAME` | `X-AllConnected-Key` | Nombre del header de auth |
| `ALLCONNECTED_PEDIDOS_PATH` | `/app/pedidos/pendientes/` | Path del endpoint de pedidos pendientes |

> Estas env vars **no están en el `.env` local**; viven en el entorno de
> producción (DigitalOcean/Railway). Por eso en local el pull responde
> `configurado: false`.
