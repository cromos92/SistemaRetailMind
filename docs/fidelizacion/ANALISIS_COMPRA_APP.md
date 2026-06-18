# 🛒 Análisis — Compra híbrida (puntos + dinero) desde la app móvil

> **Proyecto:** SistemaRetailMind (`retailmind/`) — backend orquestador
> **Objetivo:** permitir que el cliente **compre desde la app Flutter de fidelización** pagando con **dinero (Transbank Webpay Plus) + puntos**, en una sola transacción.
> **Fecha de análisis:** 2026-06-17
> **Documentos hermanos:** `realsport.cl/ANALISIS_COMPRA_APP.md` (ecommerce) · `appFidelizacion/ANALISIS_COMPRA_APP.md` (Flutter)
>
> **⚠️ Relación con la documentación existente:**
> - **`APP_FLUTTER_FIDELIZACION.md`** (este mismo repo) = especificación de la **API móvil actual** (auth RUT/email+OTP, puntos, gift cards, carnet, perfil). Es el **contrato vigente y autoritativo** de los endpoints que YA existen. **ESTE documento NO lo reemplaza: lo EXTIENDE.** Los endpoints nuevos de compra (§3.3) se agregan como una nueva sección de aquel contrato (su §3).
> - **`CONSUMO_APLICACION.md`** = inventario general de consumo del backend (su sección 5 cubre la app móvil).
> - Cuando este documento dice "agregar endpoint", se entiende: **agregarlo al contrato descrito en `APP_FLUTTER_FIDELIZACION.md`**, respetando su mismo estilo (envoltura `success`, auth JWT cliente, throttles).

---

## 0. Conclusión arquitectónica (léela primero)

**SistemaRetailMind es el ERP y la única fuente de verdad real de catálogo, stock, precio, folios y boleta SII. Por lo tanto, RetailMind debe ORQUESTAR la compra de la app de principio a fin — no "cobrar y empujar el pedido al ecommerce".**

Por qué (verificado en código, no es opinión):

1. **El ecommerce NO emite boleta válida.** `apps/dte` de realsport.cl solo genera un TXT de referencia/preview, manual desde el admin, sin firma/CAF/Acepta (`apps/dte/services.py:11-13`). La emisión real ante el SII vive **aquí** (vía TXT Acepta).
2. **RetailMind ya es el destino de los pedidos de ecommerce, no el origen.** Existe un pipeline robusto de ingesta: `POST /api/ecommerce/pedidos/` → `PedidoEcommerce` → `_crear_ticket_desde_pedido()` (descuenta stock FIFO) → `generar_dte_desde_ticket()` (emite boleta) → push de stock a AllConnected. La venta de la app es **un canal más** que alimenta ese mismo pipeline.
3. **Empujar un pedido HACIA el ecommerce no existe y está bloqueado** (`OrderViewSet.create()` devuelve 405 a propósito en el ecommerce). Construirlo sería trabajo nuevo *y* contradiría la dirección actual del dato.

**Implicancia:** la app habla **solo con `/api/v1/cliente/`**. RetailMind resuelve empresa/sucursal (Paola vs Realsport), cobra (puntos + Webpay), crea el Ticket, descuenta stock, emite boleta y acumula puntos. El ecommerce queda **al margen** (a lo sumo se le notifica stock vía el `stock_notifier` que ya existe).

```
┌──────────┐   /api/v1/cliente/   ┌────────────────────────────────────────┐
│   App    │ ───────────────────► │           SistemaRetailMind            │
│  Flutter │   (única API)        │  catálogo · carrito · cotización        │
└──────────┘                      │  reserva puntos · Webpay web · commit   │
                                  │  Ticket + stock FIFO + boleta DTE       │
                                  │  acumula puntos · push stock AllConnected│
                                  └────────────────────────────────────────┘
                                          (el ecommerce NO interviene en la venta)
```

---

## 1. Lo que YA existe (reutilizable)

### 1.1 API cliente (`app/api/cliente/`)
Prefijo `/api/v1/cliente/`. Estilo APIView DRF, delega en servicios. Auth = **RUT u email + OTP**, JWT (access 12h con claims `tipo='cliente'`, `cliente_id`, `cuenta_app_id`; refresh opaco rotatorio 7d con detección de reúso). Clase `ClienteJWTAuthentication`, permiso `IsClienteApp`.

Endpoints actuales (todos **consulta/identificación**, ninguno de compra):
`auth/solicitar-otp/`, `auth/vincular/`, `auth/verificar-otp/`, `auth/refresh/`, `auth/logout/`, `puntos/saldo/`, `puntos/movimientos/`, `giftcards/`, `perfil/` (GET/PATCH), `carnet/`.

### 1.2 Fidelización / puntos (`app/services/fidelizacion_service.py`)
- **Tasa:** `ProgramaFidelizacion.valor_punto_en_pesos = 10` → **1 punto = $10 al canjear**. Acumulación: 1 punto por cada $1.000. Mínimo de canje: **50 puntos**.
- **Saldo:** `consultar_saldo(cliente|rut)` → `{saldo_puntos, valor_pesos, puntos_por_vencer}`.
- **Canje (débito):** `canjear_puntos(cliente, puntos, *, ticket, sucursal, usuario, idempotency_key)` — bloquea cuenta con `select_for_update`, consume lotes **FIFO**, idempotente.
- **Modelos:** `CuentaPuntos` (OneToOne con `Cliente`, `saldo_puntos` cache, **bolsa única global, sin partición por empresa**), `MovimientoPuntos` (ledger inmutable: tipos ACUMULACION/CANJE/EXPIRACION/AJUSTE/REVERSA/BIENVENIDA, FIFO con `lote_origen`, `idempotency_key` unique).

### 1.3 Pipeline de venta (lo que reusaremos para la compra de app)
- **Ticket** (`app/models/ventas.py:155`): venta interna. Estados PENDIENTE/PAGADO/ANULADO. `modulo_origen` incluye `ECOMMERCE` (agregaremos `APP`). Soporta **pago mixto** (`TicketDetallePago`, método `MULTIPLE`).
- **Crear Ticket sin sesión HTTP:** `_crear_ticket_desde_pedido(...)` (`app/views_ecommerce.py:1279`) — crea Ticket + líneas + **descuenta stock FIFO**.
- **Stock FIFO:** `consumir_stock_fifo(producto_talla, cantidad, ...)` — consume `LoteProducto`, registra `Movimientos_Producto`, actualiza `Producto_Talla.stock` con `F()`.
- **Emitir boleta:** `generar_dte_desde_ticket(ticket, tipo_documento, usuario, cotizacion=None)` (`app/views_modulo_ventas.py:2306`) — crea `Dte` + TXT Acepta **sin sesión HTTP**. Folio vía `Correlativo`. `num2words` disponible.
- **Pago ecommerce:** `_crear_pago_ecommerce(...)` (método `VENTA_INTERNET`).
- **Ingesta/historial:** `PedidoEcommerce` (`app/models/ecommerce.py:75`) con `canal_origen` (REALSPORT/PAOLA/…), `numero_ticket_rm` (`RM-XXXX`), idempotente por `(canal_origen, numero_pedido_canal)`.

### 1.4 Multi-empresa
`Empresa` (Paola y Realsport son **dos Empresas**) → `Sucursal.empresa` → `Producto.sucursal`. El stock está particionado por sucursal/empresa; la boleta se asocia a `Dte.emisor` (FK Empresa). El mapeo de canal está en `CredencialesEcommerce` (`codigo`/`tipo` = `realsport`/`paola`, FK `empresa`).

### 1.5 Trabajo en curso (sin commitear) — relevante pero NO es compra
- Migración `0167_cuentaclienteapp_alter_dte_tipo_precio_externo_and_more.py`: crea `CuentaClienteApp`, `RefreshTokenClienteApp`, `CodigoOTPCliente` (infra de identidad/sesión de la app) y **altera** `Dte.tipo_precio_externo` (modo de precio para **despacho externo B2B**, *no* es para la app).
- Diffs en `serializers/views/cliente_app_service/fidelizacion_service`: agregan **login por email además de RUT**. Nada de carrito/checkout/pago.

---

## 2. Lo que FALTA (los gaps reales)

| # | Pieza | Estado | Esfuerzo |
|---|---|---|---|
| a | Endpoints de **catálogo** en API cliente (listar/buscar/detalle por empresa) | ❌ no existe | Medio |
| b | **Carrito / cotización** (server-side) | ❌ no existe | Medio |
| c | **Cotización dinero+puntos** (tope de puntos, monto Webpay) | ❌ no existe | Bajo (la materia prima está) |
| d | **Reserva de puntos** (2 fases: reservar→confirmar/liberar) | ❌ solo hay débito directo | **Medio-alto (núcleo)** |
| e | **Webpay Plus WEB** (REST `transaction.create`/`commit` headless) | ❌ **no existe** (solo POS físico serial) | **Alto (núcleo)** |
| f | Emisión de DTE para venta de app | ✅ `generar_dte_desde_ticket` existe; falta extraerlo a un **servicio** sin `request` | Bajo (refactor) |
| g | Creación de Ticket + stock | ✅ `_crear_ticket_desde_pedido` existe; falta extraerlo a servicio | Bajo (refactor) |
| h | **Orquestador transaccional** que ate todo con idempotencia y reverso | ❌ no existe | **Alto (núcleo)** |

> **Confirmado:** NO hay Webpay web hoy. `requirements.txt:17` trae `transbank-pos-sdk==1.0.1` (POS físico serial), **no** `transbank-sdk` (Webpay web). El choice `POS_WEB` existe en `origen_pago` pero sin implementación.

---

## 3. Arquitectura propuesta

### 3.1 Flujo de compra híbrida (paso a paso)

```
1. App: GET  /api/v1/cliente/catalogo/?tienda=paola        → productos + precio + stock
2. App: arma carrito (client-side) y pide cotización
   POST /api/v1/cliente/checkout/cotizar/  { tienda, items[], puntos_a_usar }
   → backend: valida stock en la sucursal/empresa, calcula
        total_pesos, max_puntos_aplicables, valor_punto=$10,
        monto_dinero = total − valor(puntos)
3. App: confirma
   POST /api/v1/cliente/checkout/iniciar/  { ...mismo payload, idempotency_key }
   → backend (atómico):
        - RESERVA los puntos (estado=reservado, TTL ~15 min)
        - crea PedidoApp (estado=INICIADO)
        - si monto_dinero == 0  → CANJE PURO: salta Webpay, va directo al commit interno
        - si monto_dinero  > 0  → Webpay create(monto_dinero, buy_order, return_url)
                                  con el commerce code de la EMPRESA del pedido
   → devuelve { pedido_id, url_webpay, token }  (o { pedido_id, pagado:false→commit directo })
4. App: abre WebView con url_webpay (página hosteada de Transbank)
5. Transbank → return_url (RetailMind):
   POST /api/v1/cliente/checkout/commit/  { token_ws }  (o lo hace el return_url server-side)
   → backend (atómico, idempotente):
        AUTORIZADO →  crea Ticket (modulo_origen=APP) + descuenta stock FIFO
                      + CONSUME los puntos reservados (canjear_puntos)
                      + emite boleta DTE (generar_dte_desde_ticket)
                      + acumula puntos por la parte en dinero
                      + notifica stock a AllConnected
        RECHAZADO  →  LIBERA puntos reservados, PedidoApp=FALLIDO
6. App: GET /api/v1/cliente/pedido/{id}/  → estado final (la app consulta ESTO, no a Transbank)
```

### 3.2 Decisiones de diseño

**(D1) Resolución de empresa/sucursal.** La app envía `tienda` (`paola`/`realsport`). RetailMind lo mapea a `Empresa`/`Sucursal` vía `CredencialesEcommerce`. **Un pedido = una sola tienda** (los puntos son unificados, así que el cliente puede comprar en cualquiera, pero cada pedido va a una empresa por la boleta y la cuenta Webpay).

**(D2) Reserva de puntos = commit en dos fases.** Hoy `canjear_puntos` debita directo. Para un pago Webpay asíncrono hay que:
- **Reservar** al iniciar (nuevo estado/movimiento `RESERVA` o modelo `ReservaPuntos` con `expires_at`).
- **Confirmar** (consumir) al commit AUTORIZADO.
- **Liberar** al commit RECHAZADO o al expirar el TTL (job/cron).
- Reusar la maquinaria FIFO + idempotencia existente.

**(D3) Webpay Plus web, headless, por empresa.** Integrar `transbank-sdk` (Webpay Plus REST): `transaction.create(buy_order, session_id, amount, return_url)` → `transaction.commit(token_ws)`. **El monto enviado a Transbank es SOLO la parte en dinero** (los puntos son descuento interno, no viajan a Transbank). CLP entero, sin decimales. Credenciales (commerce code + api key + ambiente integración/producción) **parametrizadas por empresa** (Paola y Realsport son razones sociales distintas → cuentas Transbank distintas). Nuevo modelo `TransaccionWebpay` (el `TransaccionPOS` actual no sirve: es para POS serial).

**(D4) El commit del servidor es la fuente de verdad.** Nunca confiar solo en el redirect del cliente. La app consulta `GET /pedido/{id}/`. Manejar las 4 combinaciones de retorno de Webpay (token_ws / TBK_TOKEN / timeout / error) e idempotencia (doble retorno no debe cobrar/canjear dos veces).

**(D5) Caso 100% puntos.** Si los puntos cubren todo, `monto_dinero == 0` → **se salta Webpay** (no procesa $0) y es un canje puro. Encaja natural con `canjear_puntos`.

**(D6) Fulfillment / despacho — decisión abierta (ver §6).** El MVP recomendado es **retiro en tienda** (RetailMind ya crea Ticket+boleta+stock). El despacho a domicilio requiere trabajo extra.

### 3.3 Contrato de API nuevo (a agregar en `/api/v1/cliente/`)

```
GET  catalogo/?tienda=&q=&categoria=&page=        → lista de productos (precio, stock, foto)
GET  catalogo/{sku}/?tienda=                       → detalle + variantes (talla/color)
POST checkout/cotizar/                             → { total, max_puntos, valor_punto, monto_dinero }
POST checkout/iniciar/                             → reserva puntos + Webpay → { pedido_id, url, token }
POST checkout/commit/                              → commit + Ticket + stock + boleta + puntos
GET  pedido/                                        → lista de pedidos del cliente
GET  pedido/{id}/                                   → estado + detalle + boleta
```

### 3.4 Modelos nuevos / cambios

- **`PedidoApp`** (o reusar `PedidoEcommerce` con `canal_origen='APP'`): estado (INICIADO/PAGADO/FALLIDO/ENTREGADO), empresa/sucursal, cliente, items snapshot, total, monto_dinero, puntos_usados, FK `ticket`, FK `dte`, FK `transaccion_webpay`, `idempotency_key`.
- **`ReservaPuntos`**: cuenta, puntos, estado (RESERVADO/CONSUMIDO/LIBERADO), `expires_at`, FK pedido. (O un tipo `RESERVA` en `MovimientoPuntos` + reverso.)
- **`TransaccionWebpay`**: buy_order, session_id, token, amount, empresa, status (INICIADA/AUTORIZADA/RECHAZADA), response JSON, return_url.
- **`Ticket.modulo_origen`**: agregar valor `APP`. Medio de pago de la boleta: `VENTA_INTERNET` (o nuevo) + el descuento por puntos reflejado.
- **`Producto`**: definir **precio online** por canal si difiere de `precioventa` (decisión, §6).

### 3.5 Refactors necesarios (extraer lógica de vistas a servicios)
Hoy la creación de Ticket+DTE vive en `views_*.py` mezclada con HTTP. Extraer a `app/services/`:
- `venta_app_service.py`: `crear_venta_app(empresa, sucursal, cliente, items, pago, puntos)` que internamente use `consumir_stock_fifo` + `generar_dte_desde_ticket` (versión sin `request`).
- `webpay_web_service.py`: `crear_transaccion()`, `confirmar_transaccion()`.
- Extender `fidelizacion_service.py`: `reservar_puntos()`, `confirmar_reserva()`, `liberar_reserva()`, `expirar_reservas()`.

---

## 4. Plan de implementación por fases

**Fase 0 — Cerrar el WIP actual.** Commitear la infra de identidad (CuentaClienteApp, OTP, refresh, login email/RUT). Base para todo lo demás.

**Fase 1 — Catálogo (solo lectura).** Serializer de `Producto`/`Producto_Talla` + endpoints `catalogo/` y `catalogo/{sku}/` filtrados por empresa. Decidir fuente de fotos/descripción (RetailMind vs enriquecer desde la API del ecommerce por SKU). Sin esto la app no muestra nada que comprar.

**Fase 2 — Cotización + reserva de puntos.** `checkout/cotizar/` y la maquinaria `reservar/confirmar/liberar` en fidelización + job de expiración. Probable testear con el caso 100% puntos (canje puro) primero, que no toca Webpay.

**Fase 3 — Webpay web.** `transbank-sdk`, `webpay_web_service`, modelo `TransaccionWebpay`, `checkout/iniciar` + `checkout/commit` + manejo del `return_url`. Empezar en **ambiente de integración** de Transbank.

**Fase 4 — Orquestador + venta.** `venta_app_service` que en el commit cree Ticket + stock + boleta + consuma puntos + acumule + notifique stock. Idempotencia y reverso. `pedido/{id}/`.

**Fase 5 — Despacho** (si aplica; ver §6).

---

## 5. Riesgos / cosas que romperán si no se previenen

- **Doble cobro / doble canje:** idempotencia por `idempotency_key` en iniciar y por `buy_order`/token en commit. El return de Webpay puede llegar dos veces.
- **Puntos gastados dos veces:** sin reserva, dos compras paralelas pueden canjear el mismo saldo. La reserva con `select_for_update` lo evita.
- **Pago huérfano (cliente paga, app se cierra):** el commit server-side + `pedido/{id}/` deben reconstruir el estado; soportar `transaction.status()` de Transbank para reconciliar.
- **Monto Webpay:** entero CLP, nunca $0, solo la parte en dinero.
- **Tratamiento tributario de los puntos:** la boleta se emite sobre el monto en dinero; cómo se refleja el descuento por puntos conviene validarlo con el contador (los puntos suelen ser pasivo/descuento comercial). **Decisión de negocio, no técnica.**

---

## 6. Decisiones ABIERTAS (requieren al dueño del producto)

1. **Despacho.** ¿La compra de app es **retiro en tienda** (MVP simple, RetailMind lo resuelve solo) o **envío a domicilio**? El envío requiere: (a) integrar despacho en RetailMind, o (b) crear la orden en el ecommerce para usar su CorreosChile — lo que implica construir el endpoint de creación (hoy 405) y mapear el cliente por **email** (el ecommerce no maneja RUT). **Recomendación: MVP retiro en tienda; envío en fase posterior.**
2. **Precio online.** ¿El precio de la app es `Producto.precioventa` o una lista de precios distinta por canal? Hoy `precioventa` es único.
3. **Fuente del catálogo/contenido.** ¿RetailMind sirve su propio catálogo (tiene precio+stock+empresa, lo transaccionalmente correcto) y se enriquece con imágenes/descripciones desde la API del ecommerce por SKU, o se replica todo en RetailMind? **Recomendación: RetailMind como fuente transaccional + enriquecimiento por SKU.**
4. **¿Pedido de app como `PedidoEcommerce` (canal `APP`) o modelo nuevo `PedidoApp`?** Reusar `PedidoEcommerce` da gratis historial/conciliación/`numero_ticket_rm`.

---

## 7. Apéndice — archivos clave

- API cliente: `app/api/cliente/{views,serializers,urls,authentication,permissions}.py`
- Servicios: `app/services/{fidelizacion_service,cliente_app_service,allconnected_pedidos_service}.py`
- Modelos: `app/models/{fidelizacion,cliente_app,ventas,dte,ecommerce,configuracion,organizacion,catalogo,inventario,pos}.py`
- Pipeline reusable: `app/views_ecommerce.py:1085/1279`, `app/views_modulo_ventas.py:2306` (`generar_dte_desde_ticket`), `consumir_stock_fifo`, `obtener_siguiente_correlativo`
- Notificadores: `app/stock_notifier.py`, `app/factura_notifier.py`
- Transbank (solo POS serial hoy): `app/services/transbank_pos_sdk_service.py`, `requirements.txt:17`
- Migración WIP: `app/migrations/0167_cuentaclienteapp_alter_dte_tipo_precio_externo_and_more.py`
- Settings: `retailmind/settings.py:516` (throttle), `:533` (SIMPLE_JWT), `:621-652` (ALLCONNECTED_*)
- Inventario general: `CONSUMO_APLICACION.md` (sección 5 = app móvil)
