# Integración AllConnected → SistemaRetailMind (Pedidos)

> Documento de contrato para la ingesta de pedidos desde **AllConnected**
> (VicentAllEcommercesConected, `https://ecommerce.webappsolutions.cl`) hacia el
> ERP externo **SistemaRetailMind**. Enfocado en **cómo se crean los pedidos,
> qué campos se exponen y la semántica de los montos** (la parte delicada).

---

## 1. Mapa de la integración

| Capa | Nombre |
|------|--------|
| App externa (lo que RetailMind consume) | AllConnected / VicentAllEcommercesConected — `https://ecommerce.webappsolutions.cl` |
| Service en RetailMind (hace la llamada) | `allconnected_pedidos_service.py` → `traer_pedidos_pendientes()` |
| Endpoint consumido (en AllConnected) | `GET /app/pedidos/pendientes/` (vista `pedidos_pendientes_rm_view`) |
| Header de autenticación | `X-AllConnected-Key` (validado contra `ALLCONNECTED_API_KEY`) |
| Vista/URL en RetailMind que lo dispara | `traer_pedidos_allconnected` → `/app/ecommerce/pedidos/traer/` |

**Settings en RetailMind:**

```
ALLCONNECTED_API_BASE_URL     = https://ecommerce.webappsolutions.cl
ALLCONNECTED_API_KEY          = <key de 43>
ALLCONNECTED_API_HEADER_NAME  = X-AllConnected-Key   (default)
ALLCONNECTED_PEDIDOS_PATH     = /app/pedidos/pendientes/   (default)
```

> **Dirección del flujo:** RetailMind hace un **pull a demanda** ("traer pedidos").
> AllConnected también tiene un **push** (`asignar_ticket_rm` → `POST .../app/api/ecommerce/pedidos/`),
> y **ambos usan el mismo serializador** (`construir_payload_pedido_rm`), por lo
> que el formato del payload es idéntico en pull y push.

---

## 2. Endpoint `GET /app/pedidos/pendientes/`

Código: [`system/orders/retailmind_connector.py`](system/orders/retailmind_connector.py) → `pedidos_pendientes_rm_view`.

### Autenticación
- Header `X-AllConnected-Key: <ALLCONNECTED_API_KEY>` (también acepta `?api_key=` como fallback).
- Si la key no coincide → `401 {"ok": false, "error": "API key inválida"}`.

### Query params (todos opcionales)

| Param | Formato | Default |
|-------|---------|---------|
| `desde` | `YYYY-MM-DD` | primer día del mes actual |
| `hasta` | `YYYY-MM-DD` | hoy |
| `rut_empresa` | rut sin puntos | sin filtro |
| `limit` | int | 500 (máx **2000**) |

### Respuesta `200`

```json
{
  "ok": true,
  "pedidos": [ { "...payload pedido..." } ],
  "total": 12,
  "omitidos": 3,
  "desde": "2026-06-01",
  "hasta": "2026-06-02"
}
```

- `total` = cantidad de pedidos devueltos.
- `omitidos` = pedidos del rango que **no pasaron el gating** (ver abajo).

### Gating (qué pedidos se devuelven)
Un pedido se incluye sólo si, vía `construir_payload_pedido_rm`:
1. El **canal** tiene `sincronizar_pedidos_erp = True`.
2. `pedido.fecha_pedido >= canal.fecha_inicio_sync_pedidos` (no se mandan pedidos viejos).
3. Se resuelve una **`sucursal_id`** RetailMind para el pedido (si no, `PENDIENTE_CONFIG`).
4. El canal tiene **`rut_empresa`**.
5. El pedido **aún no tiene `numero_ticket_rm`** (filtro `numero_ticket_rm` nulo/vacío).

### Idempotencia
- AllConnected ya filtra los pedidos con `numero_ticket_rm` asignado, así que el
  endpoint es **idempotente del lado origen**.
- Aun así, RetailMind **debe** ser idempotente por **`canal_origen + numero_pedido_canal`**
  (un mismo rango de fechas puede re-traerse).

---

## 3. Schema del payload por pedido

Construido por `construir_payload_pedido_rm` ([retailmind_connector.py:50](system/orders/retailmind_connector.py#L50)).

| Campo | Tipo | Origen / nota |
|-------|------|---------------|
| `numero_pedido_canal` | str | Referencia externa del marketplace. **Clave de idempotencia.** |
| `canal_origen` | str | Código del `tipo_marketplace` en MAYÚSCULAS: `SHOPIFY`, `PARIS`, `RIPLEY`, `WALMART`, `DJANGO_ECOMMERCE`… o `OTRO`. |
| `sucursal_id` | int | Sucursal RetailMind ya resuelta por AllConnected. |
| `rut_empresa` | str | RUT empresa del canal, **sin puntos**. |
| `cliente_nombre` | str | |
| `cliente_email` | str | |
| `cliente_documento` | str | |
| `subtotal` | float | Ver §5 (semántica por canal). |
| `descuento` | float | |
| `impuestos` | float | **Campo agregado** (cambio aditivo). Ver §4. |
| `costo_envio` | float | |
| `total` | float | **Gran total — autoritativo.** Ver §6. |
| `items` | list | `[{ "sku": str, "nombre": str, "cantidad": int, "precio_unitario": float }]` |
| `direccion_envio` | str | Dirección como **texto plano** (el payload **no** desglosa ciudad/región/CP). |

---

## 4. Cómo se crean los pedidos en AllConnected

AllConnected **no tiene checkout propio**: los pedidos **llegan ya confirmados**
desde los marketplaces vía sync/webhook y se materializan en el modelo
[`Pedido`](system/orders/models/base.py#L22) (+ `DetallePedido`). Campos monetarios:
todos `DecimalField(max_digits=12, decimal_places=2)`.

Fórmula canónica ([base.py:205-210](system/orders/models/base.py#L205)):

```
total = subtotal − descuento + impuestos + costo_envio
```

> ⚠️ **`Pedido.calcular_totales()` NO se vuelve a ejecutar tras crear el pedido.**
> El `total` se guarda **verbatim** del marketplace (o se calcula en la ingesta),
> así que las partes (`subtotal`/`impuestos`/…) **no siempre** reconstruyen el
> `total` con la fórmula de arriba. **El `total` es el único valor garantizado.**

### Qué pone cada marketplace en los montos

| Canal | `subtotal` | `descuento` | `impuestos` | `costo_envio` | `total` |
|-------|-----------|-------------|-------------|---------------|---------|
| **Walmart** (10) | **NETO** (`con_iva ÷ 1.19`) | 0 | **IVA desglosado** (`con_iva − neto`) | suma `shippingCharge` | `subtotal_con_iva + envío` ✅ |
| **Shopify** (1,2) | **BRUTO** (`subtotal_price`, IVA incluido) | suma de descuentos | `total_tax` (**informativo**, ya dentro del subtotal) | cascada 3 niveles | `total_price` verbatim ✅ |
| **Ripley** (6) | **BRUTO** (`price_unit × qty`) | `order.discount` | **0** | `shipping_price` | `total_price` (o fallback) ✅ |
| **Paris** (3,4) | **0** ❗ | **0** ❗ | **0** ❗ | **0** ❗ | calculado (suma líneas + envío) ✅ |
| **realsport.cl / paola.cl** (28,29,30,31) | lo que envía la API | `discount` | `tax` | `shipping_cost` | `total` verbatim ✅ |

Referencias: Walmart [order_sync.py:242](system/marketplaces/walmart/order_sync.py#L242) ·
Shopify [shopify_utils.py](system/core/shopify_utils.py) + [webhooks/views.py](system/webhooks/views.py) ·
Ripley [order_sync.py:230](system/marketplaces/ripley/order_sync.py#L230) ·
Paris [order_sync.py:262](system/marketplaces/paris/order_sync.py#L262) ·
realsport/paola [order_sync.py:154](system/marketplaces/django_ecommerce/order_sync.py#L154).

**Líneas (`items`):** `precio_unitario` es el precio de venta de la línea. La
suma de `precio_unitario × cantidad` **puede no igualar** `subtotal` cuando hay
`descuento_linea` o cuando el canal define `subtotal` distinto (Walmart neto).

---

## 5. El campo `impuestos` (cambio aditivo)

Históricamente el payload **no incluía `impuestos`** (solo `subtotal`, `descuento`,
`costo_envio`, `total`). Se agregó como **cambio aditivo y retrocompatible** en
`construir_payload_pedido_rm` para que RetailMind tenga el desglose completo.

- RetailMind debe leerlo de forma defensiva: `payload.get("impuestos", 0)`
  (pedidos servidos por una versión previa pueden no traerlo).
- **No** sumar `impuestos` por fuera al `subtotal` para reconstruir el total
  (en Shopify ya está dentro del subtotal; en Walmart el subtotal es neto).

---

## 6. Reglas de montos para RetailMind ⭐

1. **Regla de oro:** `total` es el **gran total real** que cobró el marketplace,
   es **confiable en todos los canales** y coincide con el total que AllConnected
   imprime en sus **PDF térmicos** ([processing.py:337](system/orders/processing.py#L337)
   usa `pedido.total` directo — por eso "los PDF salen bien con los montos").
   → **Tratar `total` como el monto final autoritativo. NUNCA recalcularlo desde las partes.**

2. **No validar** `total == subtotal − descuento + impuestos + costo_envio`:
   falla legítimamente (p. ej. Walmart, Shopify) porque el desglose no es homogéneo.

3. **Neto / IVA para DTE** (Chile, IVA 19%, precios con IVA incluido): derivarlo
   **siempre desde el total**, uniforme para todos los canales:
   ```
   neto = round(total / 1.19)
   iva  = total − neto
   ```

4. **Caveats por canal:**
   - **Walmart:** `subtotal` es neto; no volver a sumar el IVA por fuera. Los
     valores parciales se ven "raros" pero el `total` cuadra.
   - **Shopify:** `impuestos` es informativo (ya dentro del `subtotal`).
   - **Paris:** `subtotal/descuento/impuestos/costo_envio` llegan en **0** —
     usar **exclusivamente** `total`; **no abortar** la ingesta si `subtotal == 0`.

---

## 7. Checklist de implementación en RetailMind

En `allconnected_pedidos_service.py` / `traer_pedidos_allconnected`:

- [ ] Usar `payload["total"]` como monto final / base del documento.
- [ ] Leer `payload.get("impuestos", 0)` (defensivo).
- [ ] Derivar neto/IVA con `neto = round(total/1.19)` si se necesita para el DTE.
- [ ] **No** recomputar el total desde las partes ni validar la fórmula canónica.
- [ ] Upsert idempotente por `(canal_origen, numero_pedido_canal)`.
- [ ] Tolerar `subtotal == 0` (Paris) y la ausencia de `impuestos`.
- [ ] No romper el batch completo por un pedido con error.

### Criterio de aceptación
- El documento generado en RM usa el `total` del payload y **no difiere** del
  total de AllConnected (ni del PDF).
- Pedidos Paris (`subtotal=0`) se ingieren OK usando `total`.
- Pedidos Walmart **sin IVA duplicado** ni total inflado.
- Re-traer el mismo rango **no** genera documentos/movimientos duplicados.
- Si el payload no trae `impuestos`, la ingesta sigue funcionando.

---

## 8. Changelog

- **2026-06-02:** Se agregó el campo `impuestos` (float) al payload de
  `construir_payload_pedido_rm` (pull + push). Cambio aditivo y retrocompatible;
  no altera `calcular_totales()`, la ingesta de marketplaces, ni el cálculo de
  `total`.
