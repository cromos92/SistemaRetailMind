# Plan GiftCards REALSPORT — NICK1 / NICK2 (30 × $50.000)

**Fecha:** 2026-08-21 · **Alcance:** emisión de 30 gift cards de $50.000, cobro en POS-dashboard, contabilización en arqueo/cuadratura, reporte de uso por sucursal, envío del código por correo y ámbito de uso por empresa.

## ✅ ESTADO: IMPLEMENTADO (2026-08-21, sin commit)

Todas las fases fueron implementadas el mismo día. Resumen de lo construido:

| Fase | Estado | Entregable |
|---|---|---|
| A — Cobro POS | ✅ | Botón 🎁 Gift Card + consulta de saldo en `generacionVentas.html`; guard server-side pre-candado en `registrar_pagos_ticket`; endpoints validar/consultar en `login_required`; respuesta del cobro informa saldo restante |
| B — Arqueo | ✅ | Bucket `total_giftcard` en `_calcular_cuadratura_data` (tickets y DTEs); fila GIFT CARD en Resumen de Caja + `sumEfectivo`; `ArqueoCaja.total_giftcard_teorico` + `_MAPEO_TEORICOS_ARQUEO`; categoría 'efectivo' en modal detalle; fila en Excel |
| C — Reporte | ✅ | Trazabilidad con folio boleta/tipo DTE/total venta + botón "Ver solo canjes" con subtotales por sucursal; export con columnas nuevas; columna Boleta en detalle |
| D — Lote | ✅ | Command `emitir_giftcards_lote` (dry-run default, `--aplicar`, `--empresa`, `--vigencia-dias 60`, CSV con códigos) |
| F — Ámbito empresa | ✅ | `GiftCard.empresa` (null=todas); rechazo en `consumir()`/`validar()` fail-closed; selector de ámbito al emitir; acción "Ámbito" en Gestionar con fila AJUSTE; badge en listado/detalle |
| G — Correo | ✅ | Endpoint `api_enviar_correo_giftcard` + template `emails/giftcard_codigo.html` (60 días + descargo de responsabilidad); botón en Gestionar + post-emisión; fila `ENVIO_CORREO` en ledger con destinatario |
| Migración | ✅ escrita | `0214_giftcard_empresa_arqueo_giftcard_teorico` (a mano, **SIN aplicar** — correr `migrate` en el deploy) |
| Tests | ✅ | `test_giftcards.py`: 34/34 PASS en SQLite (12 nuevos: ámbito, cuadratura, correo, command). `test_cupon_pos_cobro` (ejercita el cobro con el guard) PASS. Fallas pre-existentes en `test_fidelizacion`/`test_cuadratura_nc` NO relacionadas (defaults de puntos y anular_factura_dte de la auditoría anterior) |

Env vars nuevas (opcionales): `GIFTCARD_CORREO_MARCA` (default `REALSPORT`), `GIFTCARD_EMAIL_TIMEOUT` (default 30s).

### Revisión adversarial post-implementación (21-ago) — 11 defectos corregidos

Se auditó el código recién escrito con revisores independientes. Todo lo confirmado quedó **arreglado y cubierto con tests**:

| Sev | Defecto encontrado | Corrección |
|---|---|---|
| P0 | Doble click / Enter sostenido en CONFIRMAR PAGO lanzaba N validaciones con el mismo snapshot → **la gift card se descontaba dos veces por una sola venta** | Candado `_gcValidacionEnCurso` + botón deshabilitado durante el fetch + recálculo del pendiente real antes del push |
| P1 | Emitir con fecha de vencimiento del modal → `AttributeError` → 500 **después** de crear la tarjeta; el usuario reintentaba y duplicaba el pasivo | `emitir()` normaliza el string `YYYY-MM-DD` a `date` (y rechaza formatos inválidos antes de crear nada) |
| P1 | El POS desktop (NEXO/Tauri) validaba **sin sucursal** → con el ámbito fail-closed, toda gift card acotada quedaba incobrable por ese canal | `GiftCardValidarView` ahora lee `x-sucursal-id` (header que el cliente ya envía) o `sucursal_id` del body |
| P1 | Los toasts de saldo restante **cerraban el modal "¡Venta Completada!"** (SweetAlert2 es singleton) y disparaban su `.then` antes de tiempo | El saldo restante se inyecta **dentro** del modal de éxito (`bloqueGiftcardsHtml`), sin toasts |
| P1 | XSS almacenado: las observaciones del ledger (texto libre del cajero) se inyectaban crudas en la trazabilidad | `esc()` en todas las celdas y en el resumen por sucursal |
| P2 | Anular una venta con **dos pagos de la misma tarjeta** devolvía solo uno (idempotencia por ticket, consumo por pago) | `reversar(pago_ticket=)` con clave por pago, simétrica al consumo |
| P2 | Se podía enviar por correo una tarjeta vencida/agotada/bloqueada ("válida hasta (-112 días)", "$0") | Guards de estado, vigencia y saldo antes de enviar |
| P2 | Emitir con ámbito "solo esta empresa" y sesión sin empresa activa creaba la tarjeta **global en silencio** | 400 explícito (mismo criterio que cambiar ámbito) |
| P2 | `sucursal_id`/fechas/`per_page` inválidos en la URL → 500 en trazabilidad y exports | Validación de tipo y formato, se ignoran los valores basura |
| P2 | El select de ámbito quedaba pegado entre emisiones; "Ámbito"/"Enviar correo" usaban el estado de la tarjeta anterior si se tipeaba otro código | Reset del select y cachés invalidados/verificados contra el código consultado |
| P2 | El endpoint de caja (`login_required`) devolvía el **email del cliente** a cualquier usuario logueado | El correo solo se expone a quien tiene `giftcards_listado.puede_ver` |

Pendiente conocido (fuera de alcance, pre-existente): el **PIN de gift card es decorativo** — `consumir()` solo lo valida si el llamador lo envía y el cobro no lo envía. Ninguna tarjeta de este lote usa PIN.

### Seguimiento de envío + saldo pendiente (21-ago, iteración 3)

Pedido: *"saber si el correo llegó bien por sistema"* y *"si queda monto pendiente por usar también"*.

- **Campos nuevos en `GiftCard`** (migración **0215**, aditiva): `correo_enviado_a`, `correo_enviado_en`, `correo_envios`, `correo_message_id`.
- El endpoint de envío ahora: verifica que `send()` haya devuelto 1 (si el servidor rechaza el mensaje, **no** se registra como enviado y responde 502 con el motivo), captura el **Message-ID** para rastrear el envío en el panel de MailerSend, y actualiza los campos + la fila `ENVIO_CORREO` del ledger.
- **Dónde se ve**: badge por fila en el listado (`📧 destinatario` / `📭 sin enviar`), bloque en el modal Gestionar (destinatario, fecha, N° de envíos), sección en el detalle, columnas nuevas en el Excel (Ámbito, Pendiente por usar, Correo enviado a, Fecha envío, N° envíos) y contadores `con_correo_enviado` / `sin_correo_enviado` en el reporte.
- **Filtros de entrega**: chips "📭 Sin enviar" / "📧 Enviadas" — responden "¿a quién todavía no le llegó su gift card?". La búsqueda también matchea por correo del destinatario.
- **Saldo pendiente por usar**: campo `pendiente_por_usar` por tarjeta (0 si ya no es canjeable), visible en el modal de gestión y en el Excel; el KPI "deuda vigente" ya sumaba el total del lote.

**Alcance honesto de "llegó bien"**: el sistema confirma que el servidor de correo **aceptó y entregó** el mensaje (con su Message-ID). Eso no prueba que el destinatario lo haya abierto ni que no cayera en spam. Confirmación de entrega/apertura real requeriría enganchar los **webhooks de MailerSend** (`delivered`, `opened`, `bounced`) a un endpoint del sistema — es una extensión pequeña y bien delimitada si la quieres.

### Lote corporativo ALBEMARLE (26 tarjetas) — iteración 4

Lista recibida: 26 gift cards de $50.000, una por hijo de trabajador, avisadas al correo del trabajador. **26 tarjetas pero solo 15 correos distintos** (Patrick Rivera tiene 4 hijos; Jorge Manzano 3; varios 2).

- **CSV de entrada**: [`retailmind/giftcards_albemarle_2026-08.csv`](../retailmind/giftcards_albemarle_2026-08.csv) (`n;beneficiario;trabajador;correo`).
- **Command nuevo `emitir_giftcards_desde_lista`**: emite una tarjeta por fila con el nombre del niño en la descripción (`ALBEMARLE 2026-08 - ISIDORA DIAZ`) y el trabajador en observaciones; deja CSV de respaldo con los códigos **antes** de intentar enviar; con `--enviar` manda los correos por **una sola conexión SMTP**, agrupando por destinatario (Patrick recibe UN correo con sus 4 códigos). `--correo-por-tarjeta` invierte el criterio. Un correo caído no detiene el lote: al final lista los pendientes, que se reenvían con el filtro "Sin enviar".
- **Template del correo** ahora soporta N tarjetas en un mensaje, cada una rotulada `PARA <NOMBRE DEL NIÑO>`.
- **Helper `enviar_codigos_por_correo`** (en `views_modulo_giftcards`) es la única ruta de envío: la usan el botón de la pantalla y el comando, así que el registro en el ledger y los campos de seguimiento son idénticos por ambos caminos.

### Comprobante térmico de USO de gift card (QZ Tray) — iteración 4

Pedido: *"cuando se utilice giftcard que envíe a imprimir un papel por la térmica por QZ Tray como comprobante de quién usó la gift card, con RUT y firma"*.

- Formato ESC/POS nuevo `_generarEscPosGiftCard` + rama `GIFTCARD_USO` en el dispatcher de [`_qz_tray_module.html`](../retailmind/app/templates/vistas/modulo_ventas/_qz_tray_module.html) (Epson TM-T20II, 80mm, 48 columnas, mismo mecanismo que el ticket de venta).
- Contenido: cabecera de empresa/sucursal, **código de barras CODE128 del código de la tarjeta**, ticket y folio del DTE, cajero, saldo anterior, monto usado, **SALDO restante en doble alto**, vencimiento, y el bloque **IDENTIFICACIÓN DE QUIEN LA UTILIZA con Nombre, RUT y Firma** (impresos si la venta identificó al cliente; en blanco para completar a mano si fue venta anónima). Cierra con "COMPROBANTE NO TRIBUTARIO".
- **Se imprime solo**, uno por cada tarjeta canjeada, apenas responde el cobro — antes del modal de éxito, para que el papel ya esté saliendo cuando el cajero se lo pasa al cliente.
- **Fallback**: si QZ Tray está apagado, abre el diálogo del navegador con el mismo comprobante maquetado a 80mm (`generarHtmlComprobanteGiftCard`), con todo el texto escapado.
- Verificado: `node --check` sobre el módulo QZ y sobre las funciones nuevas del POS; los 7 templates tocados compilan.

## Decisiones YA resueltas por el usuario (2026-08-21)

1. **DIGITAL con código generado por el sistema** (`GC-XXXX-XXXX-XXXX`), sin tarjeta física ni serie impresa.
2. **Envío por correo**: botón por tarjeta que manda un correo profesional con el código al beneficiario.
3. **Vigencia 60 días** desde la emisión (no el default de 12 meses).
4. **Custodia del código = responsabilidad del titular**: la tarjeta funciona al portador; el correo y las condiciones lo declaran expresamente (sin reposición por pérdida/robo/divulgación).
5. **Ámbito por EMPRESA**: la GC vale en **todas las sucursales de la misma empresa**. Para este lote: **IMPORTADORA NICOLE ANDREA, RUT 76.104.936-4** (verificado en prod: empresa id=1320, sucursales NICK1 id=6, NICK2 id=7, NICK3 id=8 y CD IMP id=12 — el scope cubre las 4). Debe existir una **acción** para ampliar una GC a "todas las empresas" cuando se quiera.

---

## 0. Veredicto ejecutivo

| Caso | Estado hoy | Veredicto |
|---|---|---|
| Emisión de las 30 GC (`/app/giftcards/`) | Modal emite **de a una**; servicio robusto (ledger, código seguro, vencimiento 12m) | ⚠️ Falta emisión en lote (command) |
| Cobro en POS (`/app/pos-dashboard/`) | Backend **100% listo** (consumo idempotente, parcial, reversa al anular, guard de cobertura) | 🔴 **La UI del POS no tiene botón Gift Card** — hoy es imposible cobrar con GC desde esa pantalla |
| Arqueo (`/app/ventas/cuadratura-caja/`) | `_calcular_cuadratura_data` **no clasifica GIFTCARD** | 🔴 Cada venta con GC dispara el descuadre "documentos vs medios" y subestima el VENTA TOTAL por medios |
| Reporte GC usadas + boleta por sucursal | Trazabilidad global existe (ticket, sucursal, usuario, export Excel) | ⚠️ Falta folio de **boleta**, detalle de la venta y agrupación por sucursal |
| Saldo parcial / cobrar el restante | **Soportado por diseño**: `consumir()` acepta cualquier monto ≤ saldo; la GC queda ACTIVA con el resto y se puede usar en otra venta | ✅ Solo falta que la UI lo muestre |

**Conclusión:** el motor (modelos + `giftcard_service`) aguanta perfectamente las 30 GC y el pago parcial; lo que falta es *cablear* el POS, el arqueo y el reporte. Nada requiere rediseño.

---

## 1. Análisis por caso

### 1.1 Emisión (`/app/giftcards/`)

Lo que ya funciona ([app/services/giftcard_service.py](../retailmind/app/services/giftcard_service.py), [app/models/giftcards.py](../retailmind/app/models/giftcards.py)):

- `emitir()` crea la GC con ledger EMISION, código CSPRNG `GC-XXXX-XXXX-XXXX`, vencimiento default 12 meses, `motivo` (PROMOCION/REGALO_CORP/…) y `descripcion` libre — ideal para etiquetar el lote.
- `sucursal_emision` se toma de la sucursal activa de la sesión ([views_modulo_giftcards.py:302](../retailmind/app/views_modulo_giftcards.py#L302)).
- Ya existen: recargar, anular, bloquear/desbloquear, extender vencimiento, export Excel, KPIs de pasivo.

Gaps:

1. **No hay emisión masiva** — 30 tarjetas = 30 pasadas por el modal ([api_emitir_giftcard](../retailmind/app/views_modulo_giftcards.py#L241) emite una por request).
2. **DIGITAL exige RUT de cliente** ([views_modulo_giftcards.py:288](../retailmind/app/views_modulo_giftcards.py#L288)). Para un lote promocional sin beneficiarios conocidos, el camino correcto es **FISICA** con serie propia (ej. `RSNICK1-0001`…): el titular se vincula solo en el primer canje (ya implementado en `consumir(cliente=...)`).
3. **Las GC son GLOBALES por diseño** (sin empresa/sucursal de uso): una GC emitida "para NICK1" se puede canjear en cualquier sucursal de cualquier empresa, incluida PAOLA. Restringirlas es feature nueva (campo + validación en `consumir`) — ver Decisiones §3.

### 1.2 Cobro en POS-dashboard

Backend listo ([views_modulo_ventas.py:4687-4709](../retailmind/app/views_modulo_ventas.py#L4687-L4709)):

- `GIFTCARD` está en `METODO_PAGO_TICKET_CHOICES`; el código viaja en el campo `voucher` del pago.
- Consumo con lock + `idempotency_key` por pago → doble submit no descuenta dos veces.
- **Monto parcial:** se consume exactamente `pago.monto`; el saldo restante queda en la tarjeta (ACTIVA) y se cobra en otra venta después. Varias GC en una misma venta y mezclar con efectivo/tarjeta: soportado.
- El guard "los pagos deben cubrir el total" ([views_modulo_ventas.py:4481](../retailmind/app/views_modulo_ventas.py#L4481)) cuenta la GC como pago válido.
- Anular la venta ejecuta `reversar()` y devuelve el saldo a la tarjeta, idempotente ([views_modulo_ventas.py:2252-2262](../retailmind/app/views_modulo_ventas.py#L2252-L2262)).
- Existen `api_validar_giftcard` y `api_consultar_saldo_giftcard` para pre-validar sin descontar.

Gaps:

1. 🔴 **[generacionVentas.html](../retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html) no tiene botón Gift Card** (los botones son EFECTIVO/TBK/TRANSFERENCIA/ORDEN_COMPRA + créditos, líneas 867-940). El único lugar de la web con la opción GIFTCARD es el select de edición de pagos de gestionVentasDocumentos.html.
2. 🔴 **Si la GC falla después de confirmado el cobro, la venta NO se cae** — queda solo un `logger.exception` ([línea 4703](../retailmind/app/views_modulo_ventas.py#L4703)): venta PAGADA con un pago GC que nunca se descontó = agujero de plata. El comentario del código asume una "pre-validación AJAX" que hoy no existe porque no hay UI.
3. ⚠️ **Permisos:** `api_validar_giftcard` y `api_consultar_saldo_giftcard` exigen `giftcards_listado.puede_ver`; **cajero y vendedor no lo tienen** (el seeder solo da GC a administrador y jefe_local) → 403 en la caja. Mismo problema que se resolvió con `obtener_promos_activas`: los endpoints que consume el POS al vender se dejan en `@login_required`.

### 1.3 Arqueo / cuadratura de caja

Cómo DEBE contabilizarse (regla de negocio): la GC **no es efectivo** — la venta entra al VENTA TOTAL, pero el canje **no exige plata en el conteo físico ni en el depósito**. Es un medio no-efectivo informativo, igual que CRÉD. TRABAJADOR (el pasivo ya se creó al emitir la tarjeta; el canje lo extingue).

Gaps (todos verificados en código):

1. 🔴 `_calcular_cuadratura_data` ([views_modulo_ventas.py:8368-8431](../retailmind/app/views_modulo_ventas.py#L8368-L8431)) **no tiene rama `GIFTCARD`** en el loop de pagos de tickets (ni en el de DTEs sin ticket): el monto cae al vacío. Consecuencia directa: `sumTotal` por medios < `venta_total` por documentos → se enciende `#alertaDescuadreMedios` ([cuadraturaCaja.html:3617-3626](../retailmind/app/templates/vistas/modulo_ventas/cuadraturaCaja.html#L3617-L3626)) con un gap "inexplicable" de $50.000 por cada GC canjeada, y el VENTA TOTAL del Resumen (que se pinta desde sumTotal) queda subestimado.
2. 🔴 `ArqueoCaja` **no tiene** `total_giftcard_teorico` y `_MAPEO_TEORICOS_ARQUEO` ([views_modulo_ventas.py:8726-8754](../retailmind/app/views_modulo_ventas.py#L8726-L8754)) no lo snapshotea → el arqueo congelado pierde el dato. Requiere **migración** (la 0214; hoy la última es 0213).
3. ⚠️ [cuadraturaCaja.html:3602](../retailmind/app/templates/vistas/modulo_ventas/cuadraturaCaja.html#L3602): `sumEfectivo` no incluye GC y no hay fila "GIFT CARD" en la tarjeta Efectivo y Otros.
4. ⚠️ Modal "Detalle de Métodos de Pago": `_CATEGORIAS_METODO_PAGO` ([views_modulo_ventas.py:8949-8972](../retailmind/app/views_modulo_ventas.py#L8949-L8972)) no mapea GIFTCARD → cae en `'otros'` y no aparece en ninguna pestaña salvo "todo".
5. ✅ Lo que NO se rompe: el **efectivo teórico** y la diferencia del conteo físico no se contaminan (la GC simplemente no suma a ningún bucket), y la **emisión** de las 30 GC promocionales no toca caja (no son vendidas por caja — si algún día se venden GC como producto, ese es otro flujo, fuera de alcance).

### 1.4 Reporte de GC usadas con boleta y detalle por sucursal

Ya existe ([trazabilidad_giftcards_vista](../retailmind/app/views_modulo_giftcards.py#L773)): ledger global filtrable por fecha/sucursal/usuario/tipo/código, con `ticket.correlativo`, monto, saldo resultante y export Excel. Los KPIs de pasivo ([api_reporte_giftcards](../retailmind/app/views_modulo_giftcards.py#L569)) medirán el lote ($1.500.000 de `saldo_vigente`).

Gaps:

1. La trazabilidad muestra el **correlativo del ticket pero no el folio de la boleta** (`ticket.folio_dte` / `tipo_dte`) ni el total de la venta.
2. No hay vista "GC usadas" agrupada **por sucursal** con subtotales (nº canjes, $ canjeado) ni drill-down al detalle de productos de la venta.

---

## 2. Plan de implementación

### Fase A — Cobro en POS-dashboard (P0, primero)

| # | Cambio | Dónde |
|---|---|---|
| A1 | Botón **🎁 Gift Card** en el panel de pagos + modal: input código (escáner o tipeo) → consulta saldo → monto precargado `min(saldo, pendiente)` editable → `agregarPago('GIFTCARD')` con `voucher=código`. Permitir varias GC por venta. Mostrar **"Saldo restante: $X"** tras validar y en la fila del pago | `generacionVentas.html` (patrón de los botones existentes, líneas 867-940) |
| A2 | Pasar `api_validar_giftcard` y `api_consultar_saldo_giftcard` de `@requiere_permiso('giftcards_listado','puede_ver')` a `@login_required` (mismo criterio que `obtener_promos_activas`: lo consume la caja) | `views_modulo_giftcards.py:449,479` |
| A3 | **Guard server-side**: en el cobro, ANTES del candado que marca PAGADO, validar cada pago GIFTCARD con `giftcard_service.validar()` (existe, ACTIVA, no vencida, saldo suficiente **acumulando pagos de la misma GC**) → 400 si falla. El `except GiftCardError` del consumo queda como última red, no la única | `views_modulo_ventas.py` junto al guard de cobertura (~L4481) |
| A4 | Respuesta del cobro incluye por cada GC: código, monto usado, saldo restante (para que el cajero se lo diga al cliente / se imprima) | `views_modulo_ventas.py` |

### Fase B — Arqueo / cuadratura (P0, junto con A)

| # | Cambio | Dónde |
|---|---|---|
| B1 | Rama `elif metodo == 'GIFTCARD': total_giftcard += monto` en el loop de pagos de tickets Y en el de DTEs sin ticket; inicializar `'total_giftcard': 0` | `_calcular_cuadratura_data` |
| B2 | Campo `total_giftcard_teorico = IntegerField(default=0)` en `ArqueoCaja` + entrada en `_MAPEO_TEORICOS_ARQUEO` → **migración 0214** (avisar antes de `migrate`; arqueos históricos quedan en 0, correcto) | `app/models/caja.py`, `views_modulo_ventas.py` |
| B3 | Fila **GIFT CARD** en la tarjeta "Efectivo y Otros" (`data-categoria="efectivo" data-metodos="GIFTCARD"`) + `sumEfectivo += total_giftcard` + span en el JS de pintado y en la impresión térmica del resumen | `cuadraturaCaja.html` |
| B4 | `_CATEGORIAS_METODO_PAGO['GIFTCARD'] = 'efectivo'` para que el modal de detalle la muestre en la pestaña correcta | `views_modulo_ventas.py:8949` |
| B5 | Revisar el **Excel de cuadratura** (comparte el helper): añadir la fila Gift Card donde lista métodos | exportador en `views_modulo_ventas.py` |

Regla que queda garantizada: GC **nunca** suma al efectivo teórico, al depósito esperado ni a las diferencias del conteo — solo explica el gap entre documentos y medios.

### Fase C — Reporte "GC usadas" por sucursal (P1)

| # | Cambio | Dónde |
|---|---|---|
| C1 | Trazabilidad + export: añadir columnas **folio boleta** (`ticket.folio_dte`), tipo DTE y total de la venta | `api_trazabilidad_giftcards` / `api_exportar_trazabilidad` |
| C2 | En la vista de trazabilidad: modo "**Canjes**" (filtro `tipo=CONSUMO`) con subtotales por sucursal (nº canjes, $ canjeado en el rango) — reutiliza filtros existentes | `trazabilidad.html` + endpoint |
| C3 | Drill-down: click en la fila abre el detalle de la venta (productos) reutilizando el endpoint de detalle de ticket ya existente en gestión de ventas | `trazabilidad.html` |
| C4 | Filtro de lote: el listado ya filtra por descripción → etiquetar las 30 con `descripcion="LOTE REALSPORT NICK 2026-08"` las hace rastreables como grupo | operativo, sin código |

### Fase D — Emisión del lote (P1, operativo) — ACTUALIZADA 21-ago

| # | Cambio | Dónde |
|---|---|---|
| D1 | Command **`emitir_giftcards_lote`**: `--cantidad 15 --monto 50000 --sucursal NICK1 --empresa 76104936-4 --motivo PROMOCION --descripcion "LOTE REALSPORT NICK 2026-08" --vigencia-dias 60 --dry-run` (default dry-run, `--aplicar` para ejecutar). Emite **DIGITALES con código de sistema y SIN titular** (el servicio `emitir()` no exige cliente — la exigencia vive solo en la vista del modal; el titular se vincula al primer canje, mecanismo ya existente). Reusa `giftcard_service.emitir()` (ledger + validaciones intactos). Genera CSV/XLSX con los códigos | `app/management/commands/` |
| D2 | (Opcional) Campo "cantidad" en el modal de emisión para lotes chicos desde la UI | `lista.html` + `api_emitir_giftcard` |

Se ejecuta 2 veces (15 por sucursal, ajustable) para que `sucursal_emision` quede correcta por tienda. Vencimiento = hoy + 60 días.

### Fase F — Ámbito por empresa (P0, decidido 21-ago)

Hoy las GC son globales por diseño. El usuario decidió: **la GC vale en todas las sucursales de la MISMA empresa**, con acción para ampliarla a todas las empresas.

| # | Cambio | Dónde |
|---|---|---|
| F1 | Campo `empresa = FK(Empresa, null=True, blank=True)` en `GiftCard`. **`null` = válida en todas las empresas** (compatibilidad total con las GC ya emitidas, que siguen globales). Entra en la misma migración 0214 | `app/models/giftcards.py` |
| F2 | Validación en `consumir()`: si `gc.empresa_id` y `sucursal.empresa_id != gc.empresa_id` → `GiftCardError('Esta gift card solo es válida en tiendas de <empresa>')`. `Sucursal.empresa` existe ([organizacion.py:104](../retailmind/app/models/organizacion.py#L104)) y el hook de cobro ya pasa `sucursal=ticket.sucursal` → aplicable sin tocar el POS. Misma validación en `validar()` (acepta `sucursal` opcional) para que la pre-validación del POS lo rechace ANTES de cobrar | `giftcard_service.py` |
| F3 | Acción **"Ámbito"** en lista/detalle (permiso `giftcards_emitir.puede_editar`): alternar "Solo <empresa>" ↔ "Todas las empresas". Deja fila AJUSTE monto=0 en el ledger con el cambio | `views_modulo_giftcards.py`, `lista.html`, `detalle.html` |
| F4 | Mostrar el ámbito en listado/detalle/trazabilidad y en `consultar_saldo` (el cajero ve de inmediato dónde vale) | vistas + templates |
| F5 | Command lote acepta `--empresa <rut|id>` (este lote: 76104936-4 → empresa id=1320, cubre NICK1/NICK2/NICK3/IMP — **verificado en prod 21-ago**) | command |

### Fase G — Envío del código por correo (P0, decidido 21-ago)

| # | Cambio | Dónde |
|---|---|---|
| G1 | Botón **"Enviar por correo"** por tarjeta en lista.html y detalle.html (+ ofrecerlo en el modal post-emisión). Modal pide destinatario: prefill con `cliente.email` si la GC tiene titular con correo; siempre editable | `lista.html`, `detalle.html` |
| G2 | Endpoint `api_enviar_correo_giftcard` (POST código + email + nombre destinatario, permiso `giftcards_emitir.puede_crear`): valida email, arma el correo HTML y lo envía con la infraestructura SMTP ya configurada del proyecto (patrón del envío de requerimientos: conexión con timeout) | `views_modulo_giftcards.py` |
| G3 | **Registro en el ledger**: fila monto=0 tipo `ENVIO_CORREO` (nuevo choice en `TIPO_MOV_GIFTCARD_CHOICES` — cambio de choices, no-op en BD) con destinatario/usuario/fecha en observaciones → la trazabilidad muestra a quién y cuándo se envió cada código | `models/giftcards.py`, servicio |
| G4 | Template HTML del correo (formato profesional REALSPORT, ver demo enviada a jav.teb 21-ago): monto, código destacado, vigencia (60 días con fecha exacta), cómo usarla (incluye saldo parcial), tiendas donde vale (según ámbito), y bloque de **responsabilidad del titular**: tarjeta al portador, custodia del código responsabilidad exclusiva del titular, sin reposición por pérdida/robo/divulgación, no canjeable por dinero | `app/templates/emails/` (o carpeta equivalente existente) |
| G5 | Seguridad: solo el destinatario indicado (sin CC masivos), enviado desde el remitente configurado; el código es un instrumento al portador — el correo ES el canal de entrega | — |

**Notas de implementación (recon verificado 21-ago):**

- **Demo enviada y validada**: correo de ejemplo (formato REALSPORT, código, 60 días, disclaimer) enviado OK a jav.teb@gmail.com vía el SMTP productivo (MailerSend, `noreply@webappsolutions.cl`). El HTML de referencia queda como base del template `emails/giftcard_codigo.html`.
- **Patrón a copiar**: `enviar_a_proveedor` de requerimientos ([views_modulo_requerimientos.py:1274](../retailmind/app/views_modulo_requerimientos.py#L1274)) — `EmailMultiAlternatives` (texto plano + `attach_alternative` HTML con `render_to_string('emails/...')`), `validate_email`, **`get_connection(timeout=30)`** (los flujos sin timeout dejan la request colgada si el SMTP no responde), registrar el envío SOLO después de `send(fail_silently=False)` exitoso.
- Settings SMTP ya operativos por env ([settings.py:320-347](../retailmind/retailmind/settings.py#L320-L347)); no hay helper central de correo — el endpoint arma el suyo.
- **Ledger**: fila `ENVIO_CORREO` cabe en `tipo` (max_length=20; el choice nuevo genera migración trivial AlterField no-op). **`idempotency_key=None`** en estas filas: es unique y el reenvío del código debe permitirse.
- **lista.html gotchas**: el JS va después del include del footer y toda función usada en `onclick` inline debe exponerse en `window` (líneas 932-952); el botón nuevo del modal Gestionar nace `d-none`, lo muestra `consultarSaldoGc()` y hay que agregarlo a la lista fija de `ocultarAccionesGc()` (líneas 796-800) o queda visible entre consultas.
- `api_listar_giftcards` hoy NO envía el email del cliente (solo nombre) — agregarlo para prefill del modal. `Cliente.email` existe ([crm.py:97](../retailmind/app/models/crm.py#L97), null/blank) y muchas tarjetas no tendrán titular ni correo: el modal siempre permite tipear el destinatario.
- **No usar como referencia** el "enviar ticket por email" del POS ([views_modulo_ventas.py:19194](../retailmind/app/views_modulo_ventas.py#L19194)): es un TODO que responde `success: True` sin enviar nada.
- Copia de control opcional: patrón `REQUERIMIENTOS_CORREO_COPIA` ya establecido si se quiere BCC de auditoría.

### Fase E — Validación y deploy (gate)

1. Tests nuevos (runner Django + `--settings=test_settings_sqlite`): cobro GC total / parcial / 2 GC en una venta / saldo insuficiente rechazado por el guard A3 / doble submit idempotente / anulación reversa / cuadratura con bucket GC / command lote dry-run y aplicar.
2. Los 27 tests existentes de `test_giftcards.py` deben seguir en verde.
3. Orden de deploy: commit → deploy → `python manage.py migrate` (0214) → smoke en una venta de prueba → emitir lote.
4. Sin permisos nuevos que sembrar (A2 elimina la dependencia); recordar que sigue pendiente el `inicializar_permisos` de la auditoría de reportes (tema aparte).

---

## 3. Decisiones — estado 21-ago

Resueltas (ver sección "Decisiones YA resueltas" arriba): ámbito por empresa (IMPORTADORA NICOLE ANDREA 76.104.936-4, con acción para ampliar a todas), DIGITAL con código de sistema, vigencia 60 días, envío por correo con descargo de responsabilidad del titular.

Quedan abiertas:

1. **Reparto del lote:** ¿15 NICK1 + 15 NICK2? (el command lo parametriza por sucursal de emisión; el uso vale igual en las 4 sucursales de la empresa).
2. **Entrega de los 30 códigos:** ¿se envían los correos uno a uno desde el botón a medida que se entregan (recomendado — queda trazado quién recibió cada código), o quieres además un envío masivo con una lista de 30 destinatarios?

---

## 4. Riesgos cubiertos por el diseño existente

- Doble cobro / doble submit → `idempotency_key` por pago + savepoint anti-carrera (verificado en `consumir`).
- Anulación de la venta → reversa automática del saldo, idempotente.
- Tarjeta vencida/bloqueada/agotada → `consumir` la rechaza; con A3 la venta ni se confirma.
- Recarga de tarjeta vencida (plata muerta) → ya bloqueado en `api_recargar_giftcard`.
- Descuadre ledger vs saldo cache → KPI `descuadres_ledger` ya lo vigila.
