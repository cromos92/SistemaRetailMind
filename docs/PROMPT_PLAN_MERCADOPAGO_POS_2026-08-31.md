# PROMPT — Implementación Mercado Pago vía API: cobro integrado en POS + cuadratura + reportes por método de pago

> **Versión 1.1 (31-08-2026)** — refinada con investigación de tendencias: Orders API como
> riel único (QR + Point), firma de webhooks con manifest exacto, cuotas/propinas,
> contracargos, Point Tap, y hoja de ruta del Banco Central de Chile (QR interoperable /
> "Pix chileno") como argumento para el diseño proveedor-agnóstico.
>
> **Uso**: pega este prompt completo en una sesión nueva de Claude Code sobre el repo
> `SistemaRetailMind`. Contiene el contexto ya investigado (con archivos y líneas verificadas
> al 31-08-2026), las decisiones de diseño y el plan por fases. Ejecutar fase por fase,
> confirmando conmigo antes de pasar a la siguiente.

---

Eres Claude Code trabajando en **SistemaRetailMind** (ERP/POS Django 4.2 + PostgreSQL, retail
chileno, multi-empresa/multi-sucursal, DTE/SII, Transbank ya integrado). Lee `CLAUDE.md` y
respeta sus reglas (FBV, includes en templates, pip, sin frameworks frontend, no migrar sin
avisar, secretos solo por env).

**Objetivo**: integrar **Mercado Pago como método de pago presencial vía API** en el POS web
(`generacionVentas.html`), con registro contable correcto, bucket propio en cuadratura y
arqueo, reportería por método de pago saneada, devoluciones/anulaciones seguras y
conciliación contra Mercado Pago.

---

## 1. CONTEXTO YA INVESTIGADO (no re-explorar, verificar solo si el código cambió)

### 1.1 Cómo funciona hoy el cobro integrado (Transbank)

- El flujo vivo es **Web Serial en el navegador** (`transbank-webserial.js`): Chrome habla
  con el terminal por cable; Django **solo persiste**. No hay polling ni websocket vivo.
- Botón en `retailmind/app/templates/vistas/modulo_ventas/generacionVentas.html:916`
  → `window.pagarConPOSTransbank` (`:12575-12891`): guardas → Swal de monto (permite
  **pago parcial**, min $50) → cobro → si aprueba, `pagosActuales.push({metodo_pago, monto,
  tipo_tarjeta, voucher, notas, origen_pago:'POS_INTEGRADO'})` + POST de auditoría
  fire-and-forget a `/app/pos/transbank/venta/` (→ `TransaccionPOS`).
- El pago **contable** se escribe recién al finalizar la venta:
  `POST /app/api/tickets/<correlativo>/pagos/` → `registrar_pagos_ticket`
  (`retailmind/app/views_modulo_ventas.py:3720`). Ahí: candado anti doble cobro
  (`filter(estado='PENDIENTE').update(estado='PAGADO') == 1`, `:4726-4772`), guard de
  cobertura, upsert de `TicketDetallePago`, emisión de DTE que copia pagos a
  `Dte_Detalle_Pago` (`:3162-3170`).
- Persistencia de auditoría: `views_transbank_sdk.py` (`autoconectar` :75, `venta` :119) →
  `TransbankPersistenceService` (`app/services/transbank_simple_service.py`). Lección
  documentada en su comentario `:44-57`: **buscar el ticket por (sucursal, correlativo),
  nunca por PK**.
- Modelos POS: `app/models/pos.py` — `ConfiguracionPOS` (:34, por sucursal),
  `TransaccionPOS` (:115, con `codigo_autorizacion`, `ultimos_4_digitos`, `numero_operacion`,
  `estado` ∈ INICIADA/ESPERANDO_TARJETA/PROCESANDO/APROBADA/RECHAZADA/ANULADA/ERROR/TIMEOUT),
  `LogPOS` (:257).
- **No hay config Transbank en settings** (las credenciales viven en el terminal). Mercado
  Pago SÍ necesitará credenciales server-side — bloque nuevo.

### 1.2 Modelo de pagos

- Choices canónicos `METODO_PAGO_TICKET_CHOICES` en `retailmind/app/models/ventas.py:9-33`
  (EFECTIVO, TARJETA_DEBITO/CREDITO, TRANSFERENCIA, CHEQUE, OTRO, TBK_POS_INTEGRADO,
  TBK_MANUAL, TBK_DEBITO_POS, TBK_CREDITO_POS, TBK_PREPAGO_POS, TARJETA_COMERCIAL,
  VENTA_INTERNET, ORDEN_COMPRA, CREDITO_TRABAJADOR, CREDITO_EXTERNO, CONVENIO, GIFTCARD,
  MULTIPLE). `ORIGEN_PAGO_CHOICES` (:38-43): MANUAL / POS_INTEGRADO / POS_WEB / EXTERNO.
- `TicketDetallePago` (`ventas.py:419`): metodo_pago (choices), tipo_tarjeta (texto libre,
  ahí va la marca/plataforma), voucher (código autorización), monto (**IntegerField**),
  origen_pago (nullable), property `es_pos_integrado`. Sin fecha propia (usa `Ticket.fecha`).
- `Dte_Detalle_Pago` (`app/models/dte.py:401`): metodo_pago **CharField(100) SIN choices**
  (conviven códigos con literales tipo `'Nota de Crédito'`); `fecha_pago` = fecha de caja de
  las NC.
- En `registrar_pagos_ticket:4787` un método fuera de choices **se degrada silenciosamente a
  `'OTRO'`** — por eso el choice DEBE existir antes de que el frontend lo envíe.
- `METODOS_TBK_TARJETA` (`:4794-4801`) decide el `origen_pago` por defecto.

### 1.3 Cuadratura / arqueo

- Corazón: `_calcular_cuadratura_data` (`views_modulo_ventas.py:8635`). **Dos cadenas
  if/elif duplicadas**: pagos de Ticket `:8745-8810` y pagos de DTE sin ticket `:8931-9011`
  (ya divergen entre sí). Bucket sin rama ⇒ el monto cae al vacío y dispara
  `#alertaDescuadreMedios`.
- ⚠️ **`total_mercadopago` YA EXISTE pero es otra cosa**: es el sub-bucket de
  `VENTA_INTERNET` con `tipo_tarjeta` MERCADO/SHOPIFY (marketplace/ecommerce), con campo
  `ArqueoCaja.total_mercadopago_teorico` (`app/models/caja.py:46`). El `else` del loop de
  tickets (`:8810`) cae ahí. **NO reutilizar ese bucket para el cobro presencial** — se
  mezclaría la plata de marketplace con la de caja.
- Snapshot arqueo: `_MAPEO_TEORICOS_ARQUEO` (`:9109-9138`) → campos `total_*_teorico` de
  `ArqueoCaja`; `_recalcular_teoricos_arqueo` (`:9151`); creadores `crear_arqueo` (`:13194`)
  y `guardar_cuadratura_completa` (`:10278`). Categorías del modal Resumen de Caja:
  `_CATEGORIAS_METODO_PAGO` (`:9460-9490`) — sin entrada ⇒ el pago desaparece de las
  pestañas (precedente documentado en `docs/PLAN_GIFTCARDS_REALSPORT_NICK_2026-08.md`).
- UI: `cuadraturaCaja.html` filas hardcodeadas `:1257-1307` + sumas JS `:3603-3627`;
  `revisionArqueos.html` `TEORICO_LABELS` `:1311-1341`; Excel
  `exportar_cuadratura_excel` (`:12012-12028`).

### 1.4 Reportes por método de pago (estado actual + bugs conocidos)

- `obtener_ventas_por_metodo_pago` (`views_modulo_ventas.py:20193`) — **agnóstico**, se
  actualiza solo con los choices. ✅
- `clasificar_metodo` en `views_modulo_reportes.py:2839-2864` — clasifica **por substring**
  (`'DEBITO' in m`, `'CREDITO' in m`…); un método nuevo cae a `'otros'`.
- Tres mapas de display duplicados en Python: `NOMBRES_METODOS_PAGO`
  (`app/utils_ventas.py:62`, ya le faltan GIFTCARD y MULTIPLE) + copias locales en
  `views_modulo_ventas.py:6076-6096` y `:6537-6557`; más mapas en JS
  (`generacionVentas.html:11234-11254`, `documentos_emitidos.html:74-83`).
- 🔴 Bugs preexistentes a corregir de pasada (misma zona de código):
  1. `listar_cuadraturas` usa `METODOS_TRANSBANK = {'TARJETA_DEBITO','TARJETA_CREDITO','TARJETA'}`
     (`:~10740`) — omite todos los `TBK_*` ⇒ su teórico Transbank no cuadra con el arqueo.
  2. `exportar_cuadratura_excel` referencia claves inexistentes (`total_abcdin`,
     `total_tricot`) y omite crédito externo/trabajador/orden_compra/venta_internet/klap.
  3. `total_tarjetas_comerciales = total_hites` (`:9064`) pisa el acumulado — Presto queda
     fuera del total de medios.
  4. `NOMBRES_METODOS_PAGO` sin GIFTCARD/MULTIPLE.

### 1.5 Devoluciones / anulaciones

- No hay pagos negativos: la reversa es una **NC** (`Dte.tipo_transaccion='DEVOLUCION'`) con
  `Dte_Detalle_Pago` cuyo `metodo_pago` decide el teórico y `fecha_pago` el día de caja.
- `devolucion_garantia_service.py:57-79`: `METODOS_PAGO_TRANSBANK` = métodos "anulables en
  la máquina"; `pago_transbank_dte` (`:139-204`) **bloquea la doble devolución** exigiendo la
  `TransaccionPOS` de anulación. MP necesita el equivalente.
- `anular_ticket_pendiente` (`views_modulo_ventas.py:2126`) reversa giftcards/puntos/cupón —
  cualquier método integrado nuevo necesita su reversa aquí.
- NC desde cambios/devoluciones: `views_modulo_ventas.py:22215-22220` hardcodea
  EFECTIVO/TRANSFERENCIA.

### 1.6 Sync desktop (NEXO POS Tauri)

- `PagoUploadSerializer.TIPOS_PAGO` (`app/api/sync/serializers.py:190-228`) se deriva de los
  choices ⇒ los códigos nuevos entran gratis; pero **no propaga `origen_pago`**
  (`services.py:226-234`, gap preexistente que también afecta a TBK).
- `TicketUploadSerializer.metodo_pago` (`serializers.py:286`) es CharField libre sin validar.
- `SucursalConfigView` no envía catálogo de métodos al cliente (hardcodeado en Tauri).

### 1.7 Permisos (para pantallas nuevas)

1. `app/middleware_permisos.py` → `URL_PERMISO_MAP` (match por substring, gana la clave más
   larga; mapear TAMBIÉN los endpoints AJAX/export).
2. `app/management/commands/inicializar_permisos.py` → `OpcionMenu` + habilitación por rol.
3. Migración de datos para prod (patrón `0218_opcion_menu_retiro_pedido_local.py`) o comando
   `habilitar_permisos_recientes`.

### 1.8 Lado Mercado Pago (API, Chile)

- **Riel único: Orders API** (`POST https://api.mercadopago.com/v1/orders`). Desde 2025 es
  la API vigente TANTO para QR (`type: "qr"`) como para Point (`type: "point"`) — la
  referencia oficial tiene sección "in-person-payments/point/orders". Las APIs legacy
  (instore v2, `point/integration-api/.../payment-intents`) siguen vivas pero MP publica
  guías de migración hacia Orders: **integrar directo sobre Orders API** y escribir el
  service UNA vez para ambos canales. Fallback a integration-api legacy solo si la cuenta
  chilena aún no habilita Orders para Point (verificar en sandbox).
  - QR requiere crear antes **Store** (sucursal) y **POS** (caja) en MP y referenciarlos.
  - `processing_mode`: usar `"automatic"` (transacción en una sola etapa — lo correcto para
    POS presencial); el modo `"manual"` multi-etapa existe pero no aplica aquí.
- **Auth**: Access Token privado de una aplicación MP del vendedor (server-side only).
  **Multi-empresa**: cada RUT/cadena (p.ej. REALSPORT y PAOLA) necesita su PROPIA cuenta MP
  y su propio token.
- **Webhooks**: MP notifica a una URL pública; responder HTTP 200/201 en ≤22 s o reintenta
  cada 15 min. Topics relevantes: `payment`, `order`/`merchant_order`,
  `point_integration_wh`, `chargebacks`. **Validación de firma (implementar EXACTO así)**:
  el header `x-signature` trae `ts=...,v1=...`; se construye el manifest
  `id:{data.id};request-id:{header x-request-id};ts:{ts};` y se compara
  `HMAC-SHA256(secret, manifest)` contra `v1` con `hmac.compare_digest` (timing-safe).
  Añadir tolerancia de reloj sobre `ts` (±5 min) como protección anti-replay.
- **Cuotas**: las cuotas (con/sin interés) se configuran EN LA CUENTA MP antes de crear la
  orden, no por request. Guardar `installments` del pago resultante en la transacción local
  (informativo para conciliación de comisiones).
- **Propina**: los reportes de MP traen columna `TIP_AMOUNT`. El POS retail NO maneja
  propina: si la conciliación detecta propina ≠ 0, alertar (descuadraría el monto del
  ticket).
- **Idempotencia**: header `X-Idempotency-Key` al crear órdenes/intents.
- **Devoluciones**: `POST /v1/payments/{payment_id}/refunds` (total o parcial con `amount`).
- **Conciliación**: API de reportes de liquidación (released money):
  `POST /v1/account/release_report` + config en `/v1/account/release_report/config`, con
  notificación al generarse; detalla comisiones, liberaciones, contracargos.
- **Montos en CLP**: verificar en sandbox el formato exacto de `amount`/`total_amount` para
  Chile (entero en pesos) antes de fijar la conversión — dejar la conversión en UN solo
  helper del service.
- Sin SDK instalado: `mercadopago` (SDK Python oficial) NO está en requirements. Decisión:
  usar **`requests`** (ya disponible) con un wrapper propio — evita dependencia nueva; si se
  prefiere el SDK oficial, pedir aprobación antes de tocar requirements.

### 1.9 Tendencias 2026 relevantes al diseño (investigado 31-08)

- **Banco Central de Chile — hoja de ruta de pagos interoperables**: estándares comunes de
  QR interoperable, alias y confirmación inmediata; la banca prepara un sistema de pagos
  inmediatos tipo "Pix chileno" cuyos lineamientos regulatorios salen el 2º semestre 2026.
  Consecuencia de diseño: el cobro por QR de MP NO será el último riel que se enchufe al
  POS — en 1-2 años puede aparecer QR interoperable/TEF inmediata. Por eso el guard de
  `registrar_pagos_ticket` y el registro de metadatos de métodos (Fase 4) se diseñan
  **proveedor-agnósticos** (ver decisión §2.10), sin sobre-ingeniería adicional.
- **Point Tap (Tap to Phone)**: lanzado por MP en 2026 — cualquier celular Android 10+/
  iPhone XS+ con NFC cobra tarjetas contactless desde la app MP, sin hardware. Hoy NO tiene
  API pública de integración a PDV: sirve como **contingencia manual** (cobrar por la app y
  registrar en el POS como pago manual MP) y hay que vigilar si MP lo expone vía Orders API.
  No es objetivo de integración de este plan.
- **Transferencias inmediatas + QR desplazando tarjetas** en LatAm; velocidad como driver
  principal según Transbank. Refuerza que el flujo QR del POS debe ser de **mínimos toques y
  latencia visible baja** (auto-mostrar QR con el saldo pendiente, feedback sonoro/visual
  inmediato al aprobar — ver Fase 2).
- **Comisiones y liquidación**: MP compite en Chile contra Transbank/Klap/Getnet/SumUp con
  liquidación rápida y agregación de wallet. La conciliación (Fase 5) debe capturar
  `fee_mp` por transacción y cruzar contra el reporte de liquidaciones para que
  administración pueda comparar costo real por medio de pago — ese dato hoy no existe en el
  ERP para ningún medio.

---

## 2. DECISIONES DE DISEÑO (ya tomadas — implementar así salvo que el usuario diga otra cosa)

1. **MVP = QR dinámico** (Fase 2): cero hardware, funciona con la infra actual (Django en
   Docker cloud, HTTPS público en `retail.webappsolutions.cl`). **Point = Fase 5 opcional**
   (requiere comprar terminales MP; la capa service queda preparada).
2. **Arquitectura server-side** (a diferencia de Transbank): el navegador NUNCA habla con MP
   ni ve el token. Flujo: frontend pide a Django crear la orden → Django llama a MP y
   devuelve `qr_data` + `transaccion_id` → frontend muestra QR y **hace polling a Django**
   (cada 2-3 s, timeout configurable ~120 s) → Django resuelve el estado por webhook (camino
   rápido) o consultando a MP (fallback en el mismo endpoint de polling si lleva >N s sin
   noticia del webhook).
3. **Choices nuevos** (familia `MP_*`, espejo del patrón TBK):
   - `('MP_QR', 'Mercado Pago QR')`
   - `('MP_POINT_DEBITO', 'Mercado Pago Point Débito')`
   - `('MP_POINT_CREDITO', 'Mercado Pago Point Crédito')`
   - `('MP_POINT', 'Mercado Pago Point')` (sin detalle de tarjeta)
   En Fase 2 el POS solo emite `MP_QR`; los `MP_POINT*` quedan listos para Fase 5.
4. **`origen_pago`: reutilizar `'POS_INTEGRADO'`** — el método ya distingue proveedor y
   `TicketDetallePago.es_pos_integrado` funciona sin cambios. No agregar choice nuevo.
5. **Bucket de cuadratura propio: `total_mercadopago_pos`** (+ campo
   `ArqueoCaja.total_mercadopago_pos_teorico`, `cierre_mp_fisico`,
   `diferencia_mercadopago_pos`). NUNCA mezclar con el `total_mercadopago` de marketplace.
   Categoría en `_CATEGORIAS_METODO_PAGO`: `'tarjetas'`.
6. **Modelos nuevos propios** (NO reutilizar `ConfiguracionPOS`/`TransaccionPOS`, que son
   serial-céntricos y ya tienen deuda — `tipo_pos='SDK_SERIAL'` fuera de choices):
   - `MercadoPagoConfig` — por sucursal: `sucursal` FK, `habilitado`, `modo`
     (QR/POINT/AMBOS), `mp_user_id`, `external_store_id`, `store_id`, `external_pos_id`,
     `pos_id`, `device_id` (Point, nullable), `token_env` (CharField: NOMBRE de la variable
     de entorno con el token, p.ej. `MP_ACCESS_TOKEN_REALSPORT` — el token JAMÁS en BD ni en
     el repo), timestamps. Dominio: `app/models/pos.py`, re-exportar en `models/__init__.py`.
     **Varias máquinas por sucursal**: por defecto TODAS las máquinas de la sucursal
     comparten la misma config (mismo Store/POS de MP). Es seguro porque cada cobro es una
     orden única por `external_reference` (no hay cruce entre máquinas) y la
     cuadratura/arqueo del ERP es por sucursal — igual que Transbank hoy. Único efecto: en
     el panel de MP ambas máquinas figuran como una sola caja. ⚠️ VERIFICAR EN SANDBOX si
     Orders API permite **2+ órdenes QR abiertas simultáneas sobre el mismo POS ID** (las
     APIs QR legacy limitaban a una orden abierta por caja). Si NO permite: modelar
     `unique_together (sucursal, nombre)` con `es_principal` (patrón `ConfiguracionPOS`),
     crear un POS en MP por máquina, y que cada máquina recuerde su caja con un selector
     persistido en `localStorage` (misma identidad-por-máquina que ya da el pairing serial
     de TBK).
   - `TransaccionMercadoPago` — log/estado: `config` FK, `ticket` FK nullable,
     `correlativo_ticket`, `sucursal` FK, `tipo` (VENTA/DEVOLUCION), `canal` (QR/POINT),
     `order_id`, `payment_intent_id`, `payment_id`, `external_reference` (unique),
     `monto` (IntegerField, CLP), `estado` (CREADA/PENDIENTE/APROBADA/RECHAZADA/CANCELADA/
     EXPIRADA/DEVUELTA/ERROR), `estado_detalle`, `metodo_pago_mp` (debit_card/credit_card/
     account_money…), `ultimos_4_digitos`, `codigo_autorizacion`, `fee_mp` (nullable),
     `monto_neto` (nullable — lo que MP efectivamente abona tras comisión),
     `money_release_date` (DateTimeField nullable — cuándo MP libera la plata; viene en el
     recurso payment y alimenta el módulo Dineros de Fase 5),
     `raw_response` JSONField, `webhook_recibido_en`, `usuario` FK, timestamps.
     `external_reference = f"RM-{sucursal_id}-{correlativo}-{uuid4().hex[:8]}"`.
7. **Verificación server-side del pago** (mejora sobre el flujo TBK, que confía en el
   navegador): en `registrar_pagos_ticket`, si un pago trae `metodo_pago` de la familia
   `MP_*` con `origen_pago='POS_INTEGRADO'`, exigir que exista `TransaccionMercadoPago`
   APROBADA para ese (sucursal, correlativo) con monto ≥ al del pago y no consumida;
   marcarla consumida (FK/flag) dentro de la misma transacción. Si no existe → 400 con
   mensaje claro. Flag env `MP_VALIDAR_PAGO_SERVER=true` para poder apagarlo en emergencia.
   Los `MP_*` con `origen_pago='MANUAL'` (contingencia Point Tap, §Fase 5.6) quedan exentos
   del guard — es el mismo nivel de confianza que TBK_MANUAL hoy, y la conciliación diaria
   los cruza igual contra MP (un ticket MP manual sin pago real en MP sale en el reporte).
8. **Secretos** (ACTUALIZADO 31-ago a pedido del usuario): las credenciales viven en BD
   en `MercadoPagoCuenta` (una por empresa/RUT, gestionada desde el admin de Django)
   pero **CIFRADAS en reposo** con Fernet (`services/mp_credenciales.py`): la clave de
   cifrado NO está en la BD — sale de la env var `MP_CRED_KEY` o, si no existe, se
   deriva de `SECRET_KEY`. Un dump de la BD no expone tokens en claro. Los campos
   `token_env`/`webhook_secret_env` de la config quedan como fallback legacy.
   ⚠️ Rotar SECRET_KEY/MP_CRED_KEY invalida lo cifrado (re-guardar desde el admin).
   Nada de tokens en settings.py ni en el repo (histórico de secretos expuestos).
9. **Saneamiento de reportería como parte del alcance** (el usuario pidió "plan robusto de
   reportes por método de pago"): crear registro único de metadatos de métodos de pago y
   corregir los 4 bugs preexistentes de §1.4 en la misma pasada.
10. **Guard proveedor-agnóstico** (por la hoja de ruta BCCh, §1.9): la verificación
    server-side de §2.7 se implementa como interfaz genérica — "para el método X existe una
    transacción integrada aprobada y no consumida" — donde MP es el primer proveedor. Basta
    con que el helper reciba (metodo_pago, sucursal, correlativo, monto) y resuelva el
    backend por el registro de metadatos de Fase 4 (`es_integrado` + proveedor). Cuando
    llegue QR interoperable/TEF inmediata, se agrega un proveedor sin tocar
    `registrar_pagos_ticket` de nuevo.
11. **Event log de webhooks**: modelo liviano `MercadoPagoWebhookEvento` (`request_id`
    unique, `topic`, `data_id`, `payload` JSONField, `firma_valida`, `procesado`,
    `recibido_en`). Da idempotencia real por `request_id` (re-entregas de MP no reprocesan),
    trazabilidad para debug y base para reprocesar a mano. Sin esto, un webhook que falla a
    mitad de procesamiento se pierde en silencio.
12. **Barrido de transacciones colgadas**: las PENDIENTE que nunca recibieron webhook ni
    polling (cajero cerró la pestaña, caída de red) deben cerrarse solas — command
    `sincronizar_transacciones_mp` (Fase 5) que consulta a MP toda transacción PENDIENTE
    con más de N minutos y la resuelve (APROBADA huérfana → alerta de conciliación;
    EXPIRADA → cerrada). Programable como cron/comando diario junto a los existentes.
13. **`processing_mode: "automatic"`** en toda orden (una sola etapa); jamás capturas
    diferidas en POS presencial.

---

## 3. PLAN POR FASES

### FASE 0 — Prerrequisitos (manuales, del usuario — dejar checklist en el PR/resumen)

- [ ] Crear/confirmar cuenta Mercado Pago **vendedor** por cada empresa que cobrará (RUT
      REALSPORT, RUT PAOLA, …) y una **aplicación** en el panel de developers de cada una.
- [ ] Obtener Access Token de producción y de test por empresa.
- [ ] Configurar en el panel de cada aplicación la URL de webhooks:
      `https://retail.webappsolutions.cl/app/pos/mercadopago/webhook/` y copiar la **clave
      secreta** de firma.
- [ ] Definir variables de entorno en Railway/DO:
      `MP_ACCESS_TOKEN_<EMPRESA>`, `MP_WEBHOOK_SECRET_<EMPRESA>`, `MP_VALIDAR_PAGO_SERVER`,
      `MP_QR_TIMEOUT_SEGUNDOS` (default 120).
- [ ] Configurar en la cuenta MP de cada empresa las **cuotas** (con/sin interés) que se
      aceptarán — se define en la cuenta, NO por API (§1.8).
- [ ] Crear **usuarios de prueba** (vendedor + comprador) en el panel de developers para el
      sandbox y verificar ahí: formato de montos CLP, si Orders API `type: "point"` está
      habilitado para cuentas chilenas, y si un mismo POS ID admite **2+ órdenes QR
      abiertas simultáneas** (define si basta una config por sucursal o se necesita un POS
      de MP por máquina — §2.6).
- [ ] Configurar **retiro automático DIARIO** al banco en cada cuenta MP (base del método de
      conciliación 1:1 por retiro de la Fase 5 — cada abono en cartola = un retiro = una
      unidad conciliable).
- [ ] Decidir qué sucursales parten con QR (piloto: 1 tienda).
- [ ] Comparar comisiones vigentes MP vs Transbank/Klap por tipo de tarjeta (dato para
      administración; la conciliación de Fase 5 lo medirá con datos reales vía `fee_mp`).

### FASE 1 — Backend base (modelos + service + endpoints + webhook)

1. **Choices y migraciones** (`app/models/ventas.py:9`): agregar los 4 `MP_*`. Migración
   `AlterField` no-op (Ticket, TicketDetallePago, PagoCambioDevolucion,
   PagoCreditoTrabajador) — próximo número tras la última en `app/migrations/` (verificar;
   al 31-08 la última era `0221_*`, y hay `0222+` de otros trabajos sin commitear:
   **coordinar numeración con el usuario ANTES de makemigrations**).
2. **Modelos** `MercadoPagoConfig`, `TransaccionMercadoPago` y `MercadoPagoWebhookEvento`
   (§2.11) en `app/models/pos.py` + re-export en `models/__init__.py` + migración
   `CreateModel`. En `TransaccionMercadoPago` incluir además `installments` (int, default 1)
   y estado `CONTRACARGO` en los choices de estado.
3. **Service** `app/services/mercadopago_service.py` (molde:
   `transbank_simple_service.py` para persistencia + wrapper HTTP propio):
   - `_client(config)` → sesión requests con token desde `os.environ[config.token_env]`,
     timeout corto (connect 5 s / read 10 s), retries controlados, `X-Idempotency-Key`.
   - `crear_orden(config, correlativo, monto, descripcion, canal)` → POST `/v1/orders`
     (`type` qr o point según canal, `processing_mode: "automatic"`, external_reference,
     expiración = `MP_QR_TIMEOUT_SEGUNDOS`) → crea `TransaccionMercadoPago(estado=
     'PENDIENTE')` → devuelve `qr_data` (o intent en device) + id local. UN solo método
     para ambos canales (§1.8: Orders API es el riel único).
   - `consultar_estado(transaccion)` → primero BD (webhook ya procesado); si sigue
     PENDIENTE y han pasado >5 s desde la creación, GET a MP y actualizar.
   - `cancelar(transaccion)` → cancela la orden/intent en MP y marca CANCELADA.
   - `procesar_notificacion(payload, headers)` → 1) upsert de `MercadoPagoWebhookEvento`
     por `x-request-id` (si ya existe y `procesado`, responder 200 y salir — idempotencia);
     2) validar `x-signature` con el manifest EXACTO
     `id:{data.id};request-id:{x-request-id};ts:{ts};` + `hmac.compare_digest` + tolerancia
     de `ts` ±5 min (§1.8); firma inválida → registrar evento con `firma_valida=False`,
     responder 200 (no dar señal al atacante) y NO procesar; 3) resolver la transacción por
     `external_reference`/ids, actualizar estado + `payment_id`, `fee_mp`, `installments`,
     tarjeta; marcar evento `procesado`.
   - `reembolsar(transaccion, monto=None)` → POST `/v1/payments/{id}/refunds`, crea
     `TransaccionMercadoPago(tipo='DEVOLUCION')`.
   - Buscar ticket SIEMPRE por (sucursal, correlativo) — lección de
     `transbank_simple_service.py:44-57`.
   - Logging con `logging.getLogger('app')`.
4. **Vistas** `app/views_mercadopago.py` (FBV, espejo de `views_transbank_sdk.py`):
   - `POST /app/pos/mercadopago/qr/crear/` — sucursal desde sesión, valida
     `MercadoPagoConfig.habilitado`, llama al service.
   - `GET /app/pos/mercadopago/estado/<int:transaccion_id>/` — polling del frontend.
   - `POST /app/pos/mercadopago/cancelar/<int:transaccion_id>/`.
   - `POST /app/pos/mercadopago/webhook/` — `@csrf_exempt`, SIN login (viene de MP), valida
     firma, responde 200 rápido; el procesamiento pesado dentro pero acotado (sin llamadas
     lentas; si algo falla igual responder 200 y dejar log — el polling es la red de
     seguridad).
   - Registrar en `app/urls.py` (bloque nuevo "MÓDULO POS MERCADO PAGO", sin namespaces,
     imports explícitos).
   - `URL_PERMISO_MAP`: los endpoints de cobro cuelgan del permiso del POS existente; el
     webhook se EXCLUYE del middleware de permisos (verificar cómo el middleware trata rutas
     no mapeadas y listarlo explícitamente si hace falta).
5. **Guard server-side en `registrar_pagos_ticket`** (decisión §2.7): familia `MP_*` exige
   `TransaccionMercadoPago` APROBADA no consumida; agregar los `MP_*` al set que asigna
   `origen_pago` (hoy `METODOS_TBK_TARJETA`, `:4794-4801` — renombrar a
   `METODOS_TARJETA_INTEGRADA` y que MP mapee a `'POS_INTEGRADO'`).
6. **Reversa en `anular_ticket_pendiente`** (`:2126`): si el ticket tiene pagos `MP_*`
   aprobados/consumidos, ejecutar `reembolsar()` (o bloquear la anulación si el refund
   falla, con mensaje claro — NO dejar plata cobrada con ticket anulado).

### FASE 2 — UI POS (QR en el paso 3 de cobro)

Leer antes: `generacionVentas.html` paso 3 (botones ~`:916-990`) y
`docs/PLAN_GIFTCARDS_REALSPORT_NICK_2026-08.md` (último método agregado). Respetar
NEXO design system y patrón de includes. jQuery/vanilla + SweetAlert2 (ya cargado).

1. Exponer en el contexto de `pos_dashboard` (`views_modulo_ventas.py:1710`) un flag
   `mp_habilitado` desde `MercadoPagoConfig` de la sucursal, y condicionar el botón con
   `{% if %}` (Transbank hoy muestra su botón incondicionalmente — no imitar eso).
2. Botón "Mercado Pago QR" junto al de POS TBK (`:916`) → `window.pagarConMercadoPago()`
   (exponer a window — gotcha conocido de closures vs onclick inline en plantillas POS).
3. `pagarConMercadoPago()` modelada sobre `pagarConPOSTransbank` (`:12575`):
   - Reutilizar guardas + Swal de monto parcial (extraer helper compartido si es limpio).
   - POST crear QR → Swal modal con el QR (render con lib local o `qr_data` como imagen
     que entregue el backend — **no cargar CDNs**; si hace falta lib QR nueva, preguntar
     primero; alternativa: generar el PNG del QR server-side con `qrcode`/Pillow — Pillow ya
     está; `qrcode` sería dependencia nueva ⇒ preguntar) + contador de expiración + botón
     Cancelar.
   - Polling a `/estado/` cada 2-3 s hasta APROBADA/RECHAZADA/EXPIRADA/timeout.
   - APROBADA ⇒ `pagosActuales.push({metodo_pago:'MP_QR', monto, tipo_tarjeta: metodo_mp,
     voucher: payment_id, notas, origen_pago:'POS_INTEGRADO'})` + `actualizarListaPagos()` +
     `actualizarResumenVenta()` + Swal éxito. (El registro contable llega con
     `registrar_pagos_ticket`, igual que TBK.)
   - RECHAZADA/EXPIRADA/cancelada ⇒ Swal con motivo y opción reintentar. El reintento crea
     una orden NUEVA (external_reference nuevo con el mismo prefijo de correlativo) — nunca
     reutilizar una orden expirada.
4. **UX de mínimos toques** (§1.9 — velocidad es el driver): el Swal de monto viene
   pre-cargado con el saldo pendiente (Enter = cobrar todo, igual que el patrón TBK);
   feedback inmediato al aprobar (Swal de éxito + beep corto vía `Audio` con data URI, sin
   assets externos); el contador de expiración visible sobre el QR para que el cajero sepa
   cuándo regenerar.
5. Display JS: agregar `MP_*` al mapa `obtenerNombreMetodoPago`
   (`generacionVentas.html:11234-11254`).
6. Modo kiosk: verificar targets táctiles del botón/modal con `pos-kiosk.css` activo
   (targets 48-64px, sin `:hover`). El QR debe verse a tamaño escaneable desde el lado
   cliente del mesón (~300px mínimo en 1920px).

### FASE 3 — Cuadratura + arqueo (checklist GIFTCARD, calcado)

1. `_calcular_cuadratura_data`: inicializar `total_mercadopago_pos` en el dict
   (`:8658-8726`) + rama `MP_*` en **AMBOS** loops (tickets `:8745-8810` y DTEs
   `:8931-9011`). Aprovechar de extraer la clasificación a UNA función usada por los dos
   loops (elimina la divergencia actual) — solo si los tests de cuadratura quedan verdes.
2. `_MAPEO_TEORICOS_ARQUEO` (`:9109`): `('total_mercadopago_pos_teorico',
   'total_mercadopago_pos')`.
3. `ArqueoCaja` (`app/models/caja.py`): `total_mercadopago_pos_teorico`,
   `cierre_mp_fisico`, `diferencia_mercadopago_pos` + migración; incluir la diferencia en
   `diferencia_total` si corresponde (revisar property `:320`).
4. `_recalcular_teoricos_arqueo` (`:9151`): calcular `diferencia_mercadopago_pos =
   cierre_mp_fisico − total_mercadopago_pos_teorico`.
5. `_CATEGORIAS_METODO_PAGO` (`:9460`): `MP_*` → `'tarjetas'`.
6. `cuadraturaCaja.html`: fila nueva (`:1257-1307`, patrón `row-detalle data-metodos`) +
   sumas JS (`:3603-3627`) + campo de cierre físico MP si el arqueo lo pedirá.
7. `revisionArqueos.html` `TEORICO_LABELS` (`:1311-1341`).
8. `exportar_cuadratura_excel` (`:12012`): fila MP POS **y de pasada corregir**
   `total_abcdin`/`total_tricot` fantasmas y los buckets omitidos (§1.4.2).
9. `listar_cuadraturas` (`:10598`): incluir `MP_*` en su teórico de tarjetas **y corregir**
   `METODOS_TRANSBANK` para que incluya los `TBK_*` (§1.4.1).
10. `crear_arqueo` (`:13287-13320`) y `guardar_cuadratura_completa` (`:10333-10350`):
    incluir el campo nuevo (idealmente derivar ambos de `_MAPEO_TEORICOS_ARQUEO` para matar
    la divergencia).
11. NC/devoluciones en cuadratura: decidir bucket de las NC con `metodo_pago` MP (espejo de
    `total_nc_*`): agregar `total_nc_mercadopago_pos` restando del teórico MP (patrón de
    `total_nc_transferencia`).

### FASE 4 — Reportería por método de pago (saneamiento robusto)

1. **Registro único** `app/metodos_pago.py` (módulo nuevo, sin tocar BD):
   `METODOS_PAGO_META = {codigo: {'display', 'categoria', 'bucket_cuadratura',
   'es_integrado', 'anulable_en_terminal', 'abrev_dte'}}` para TODOS los métodos.
   Helpers: `display(codigo)`, `categoria(codigo)`, `es_integrado(codigo)`.
2. Apuntar al registro (sin cambiar comportamiento, salvo los fixes listados):
   - `NOMBRES_METODOS_PAGO` / `obtener_nombre_metodo_pago` (`utils_ventas.py:62-87`) +
     **fix GIFTCARD/MULTIPLE faltantes**.
   - Eliminar las 2 copias locales shadowed (`views_modulo_ventas.py:6076-6096`,
     `:6537-6557`) → importar el helper.
   - `clasificar_metodo` (`views_modulo_reportes.py:2839-2864`): agregar rama explícita
     `MP_*` → bucket `mercado_pago` en el resumen de documentos emitidos (ANTES de los
     checks por substring de DEBITO/CREDITO, que si no se los tragan).
   - `_ABREV_METODO_PAGO` (`views_modulo_documentos.py:1590-1612`): abreviatura `MP`.
   - `_categoria_metodo_pago` ya cubierto en Fase 3.
3. Filtros/selects de UI: `documentos_emitidos.html:74-83`,
   `gestionVentasDocumentos.html` (`:50-51`, `usaTarjeta` `:3002-3004`),
   `metodo_pago_grupos` (`views_modulo_ventas.py:5762-5778`) y labels (`:6084-6087`,
   `:6545-6548`).
4. Fix `total_tarjetas_comerciales = total_hites` (`:9064`) → suma real Hites+Presto
   (§1.4.3) — verificar impacto en pantallas antes de cambiar.
5. Dashboard/gráficos: `obtener_ventas_por_metodo_pago` se actualiza solo; revisar
   `dashboard_ventas.html` por listas hardcodeadas.
6. AllConnected/canales: `canal_desde_plataforma_pago` (`utils_ventas.py:92-131`) NO se
   toca (MP presencial no es canal ecommerce).

### FASE 5 — Devoluciones, conciliación y Point (opcional/final)

1. **Devolución por garantía**: en `devolucion_garantia_service.py`, espejo de
   `pago_transbank_dte`: para pagos `MP_*`, ofrecer devolución vía API
   (`reembolsar()`) y bloquear doble devolución exigiendo la `TransaccionMercadoPago`
   tipo DEVOLUCION. Agregar `MP_*` a los sets `:57-79` según corresponda.
2. **NC de cambios/devoluciones** (`views_modulo_ventas.py:22215-22220`): contemplar
   devolución a MP (refund API) además de EFECTIVO/TRANSFERENCIA.
3. **Barrido de colgadas** — command `sincronizar_transacciones_mp` (§2.12): toda
   `TransaccionMercadoPago` PENDIENTE con >30 min consulta su estado real en MP y se
   cierra (APROBADA huérfana → queda marcada para conciliación; EXPIRADA/CANCELADA →
   cerrada). Idempotente, seguro de correr en cron diario/horario.
4. **Conciliación diaria** — command `conciliar_mercadopago`
   (`app/management/commands/`): por config/empresa, `GET /v1/payments/search` por rango de
   fechas (o el reporte de liquidación) vs `TransaccionMercadoPago` + `TicketDetallePago`;
   reporta: pagos MP sin ticket (plata huérfana), tickets MP sin pago MP real (fraude/bug),
   diferencias de monto, comisiones (`fee_mp` y totales por día — primer medio de pago del
   ERP con costo real medible), propinas inesperadas (`TIP_AMOUNT` ≠ 0, §1.8), y
   **contracargos** (webhook topic `chargebacks` → estado CONTRACARGO; un contracargo NO
   genera NC automática — se reporta para gestión manual). Salida por consola + opcional
   Excel (openpyxl). Read-only, sin `--apply`.
5. **Pantalla "Dineros Mercado Pago"** (SOLICITADA por el usuario — visibilidad de la plata
   cobrada que aún no llega al banco): vista FBV nueva en `views_mercadopago.py`, template
   en `modulo_ventas/` siguiendo `ESTILOS_MODULOS.md` (module-header + kpi-cards), con dos
   pestañas:
   - **Dineros** (por empresa/cuenta MP): KPIs — cobrado del período, **pendiente de
     liberación** (pagos APROBADOS con `money_release_date` futura, dato local ya guardado
     en `TransaccionMercadoPago` — no requiere llamar a MP para pintar la pantalla),
     liberado, y **transferido al banco** (retiros según el reporte de Liberaciones).
     Tabla de pagos con semáforo pendiente/liberado y su fecha de liberación; tabla de
     retiros al banco. Fuentes API: `money_release_date` del recurso payment (se captura al
     aprobar y en el barrido) + **reporte de Liberaciones** vía API
     (`/v1/account/release_report`, se puede generar además por retiro) para
     liberaciones/retiros/bloqueos. Nota: el reporte "saldo disponible" viejo fue
     deshabilitado por MP (mar-2022) — NO construir sobre él; si existe endpoint de balance
     de cuenta para Chile, tratarlo como mejora opcional verificada en sandbox, nunca como
     fuente primaria.
   - **Depósitos al banco (conciliación 1:1 por retiro)** — el método: con retiro
     automático DIARIO configurado en la cuenta MP (Fase 0), cada abono en la cartola = un
     retiro de MP = una unidad conciliable. MP genera el reporte de Liberaciones **por
     retiro**, que lista los pagos exactos que componen cada transferencia (con
     `external_reference` → tickets), comisiones, devoluciones y bloqueos — cero matching
     difuso. **Ingesta 100% por API, sin panel**: (1) config una sola vez en
     `/v1/account/release_report/config` activando generación automática en modo "por
     retiro" (y/o diaria); (2) el command lista los reportes generados
     (`GET /v1/account/release_report`) y descarga cada archivo por nombre; (3) parsea las
     filas (retiro + pagos + débitos) y crea/marca `RetiroMercadoPago` y las
     transacciones DEPOSITADAS automáticamente. MP notifica cuando el archivo está listo,
     pero el cron diario que lo va a buscar es suficiente. Modelo nuevo `RetiroMercadoPago` (`withdrawal_id` unique, `config` FK, fecha,
     `monto`, `estado`: PENDIENTE_CONCILIAR/CONCILIADO/CON_DIFERENCIA, `visto_en_cartola`
     bool manual, timestamps) + FK nullable `retiro` en `TransaccionMercadoPago`: al
     procesar el reporte, cada pago referenciado queda marcado con su retiro. Ciclo de
     estados de cada peso cobrado: COBRADO → LIBERADO → DEPOSITADO (→ visto en cartola).
     **Invariante de cuadre** (calculado y alertado en pantalla, por empresa):
     `Σ netos aprobados − devoluciones − contracargos − Σ retiros = saldo esperado en MP
     (pendiente + disponible)`; y por retiro: `Σ netos de pagos referenciados == monto
     depositado` (diferencias = ajustes/bloqueos que el mismo reporte detalla). Lo que
     queda sin retiro asignado ES la plata que sigue en MP. NO mezclar con el flujo de
     depósitos del arqueo (ese es efectivo físico; la plata MP nunca pasa por caja).
     **Anulaciones/devoluciones en el flujo de dinero**: un refund vía API impacta según el
     momento — (a) pago aún NO liberado: MP lo descuenta del pendiente de liberación (esa
     plata simplemente no llega al banco); (b) pago ya liberado/depositado: MP lo debita del
     saldo disponible de la cuenta (o lo netea contra ventas futuras si no hay saldo), y
     aparece como línea de débito (REFUND) en el reporte de Liberaciones y dentro del
     retiro que lo absorbió. Por eso el invariante RESTA devoluciones y el detalle por
     retiro puede traer débitos legítimos. La fila `TransaccionMercadoPago(tipo=
     'DEVOLUCION')` se marca con el retiro que la absorbió, igual que los pagos. Verificar
     en sandbox si MP Chile devuelve la comisión en refund total (afecta el neto esperado).
     Contracargos NO son anulaciones: se debitan igual en Liberaciones pero quedan en
     estado CONTRACARGO para gestión manual (sin NC automática).
   - **Conciliación**: la salida del command `conciliar_mercadopago` (item 4) en pantalla.
   Permisos: los 3 pasos de §1.7 (URL_PERMISO_MAP incluyendo los endpoints AJAX,
   inicializar_permisos, migración de datos — sin la migración los roles dan 403 en prod).
6. **Point** (cuando haya terminales): mismo `crear_orden(..., canal='POINT')` sobre Orders
   API con `type: "point"` (device de `MercadoPagoConfig`), mismo polling/webhook; el POS
   emite `MP_POINT_DEBITO/CREDITO` según `payment_method` del resultado. Si Orders `type:
   "point"` no está habilitado para la cuenta chilena, fallback documentado a
   `point/integration-api/devices/{device_id}/payment-intents` encapsulado en el service
   (la interfaz hacia el POS no cambia).
7. **Contingencia Point Tap** (§1.9): si una tienda se queda sin QR/terminal, se puede
   cobrar desde la app MP con el celular (NFC) y registrar en el POS como `MP_POINT` con
   `origen_pago='MANUAL'` (voucher = nº de operación de la app). Documentarlo como
   procedimiento operativo — la conciliación diaria igual lo cruzará contra MP; NO requiere
   código nuevo, pero el guard server-side debe eximir a los `MP_*` con
   `origen_pago='MANUAL'` (solo exigir transacción cuando `origen_pago='POS_INTEGRADO'`).

### FASE 6 — Tests + deploy

1. **Tests nuevos** `app/tests/test_mercadopago_pos.py` (Django test runner, NO pytest;
   factories de `app/tests/factories.py`; mockear requests con `unittest.mock`):
   - service: crear orden (payload/idempotency), webhook firma válida/ inválida/re-entrega
     idempotente, transiciones de estado, refund.
   - guard de `registrar_pagos_ticket`: MP sin transacción aprobada → 400; con transacción →
     OK y queda consumida; doble uso → 400.
   - cuadratura: ticket con pago MP_QR cae en `total_mercadopago_pos` y NO en
     `total_mercadopago` (marketplace); NC MP resta del teórico MP.
   - anulación de ticket con pago MP dispara refund.
2. **Tests existentes a revisar/actualizar**: `test_cuadratura_nc.py`, `test_giftcards.py`,
   `test_arqueo_deposito_estado.py`, `test_reporte_ventas_internet.py`,
   `test_reportes_correcciones.py`, `test_cupon_pos_cobro.py`, `test_devolucion_garantia.py`.
   ⚠️ El `.env` local apunta a BD de PRODUCCIÓN: correr tests con `DATABASE_URL` a PG local
   + `--keepdb` (patrón ya usado en este repo). Hay 4 fallas de test PRE-EXISTENTES
   conocidas — no confundirlas con regresiones.
3. **Checklist de deploy** (entregar como lista copy-paste, NO ejecutar sin aviso):
   - Coordinar y aplicar migraciones (hay migraciones de otros trabajos sin commitear —
     el orden importa).
   - Setear env vars MP en Railway/DO.
   - Configurar webhook + secret en el panel MP de cada empresa.
   - Crear Store/POS en MP por sucursal piloto (vía panel o pequeño command
     `configurar_mercadopago_sucursal`).
   - Cargar `MercadoPagoConfig` de la sucursal piloto (admin de Django o command).
   - `inicializar_permisos` / migración de permisos si se creó pantalla nueva.
   - Probar en sandbox de MP ANTES de tocar credenciales productivas.

---

## 4. CRITERIOS DE ACEPTACIÓN

1. Cobro QR completo en ≤3 toques desde el paso 3 del POS; pago parcial soportado;
   expiración y cancelación limpias (la orden se cancela en MP, no queda QR vivo).
2. Un pago MP aprobado que el cajero abandona (no finaliza la venta) queda visible como
   `TransaccionMercadoPago` APROBADA sin consumir → aparece en la conciliación (plata
   huérfana detectable, nunca silenciosa).
3. Imposible registrar un pago `MP_*` sin transacción MP aprobada real (con el flag activo).
4. Cuadratura y arqueo cuadran: bucket MP POS propio, separado del marketplace; NC MP resta
   del teórico MP; snapshot del arqueo incluye el campo nuevo.
5. Reportes: documentos de venta, documentos emitidos, resumen de caja y export Excel
   muestran/filtran los métodos `MP_*` con display correcto; los 4 bugs preexistentes de
   §1.4 corregidos.
6. Devolución/anulación: refund vía API con bloqueo de doble devolución.
7. Webhook: firma validada con el manifest exacto (timing-safe + anti-replay), idempotente
   por `x-request-id` (event log persistido), responde <22 s; caída del webhook no rompe el
   cobro (polling con fallback a consulta directa).
8. Ninguna transacción queda PENDIENTE para siempre: el barrido
   `sincronizar_transacciones_mp` cierra las colgadas y las aprobadas huérfanas quedan
   visibles en conciliación.
9. Conciliación reporta comisiones (`fee_mp`), cuotas, propinas inesperadas y contracargos —
   costo real del medio de pago medible por día/sucursal.
9b. Pantalla Dineros responde de un vistazo "¿cuánta plata cobrada por MP aún no llega al
    banco?" por empresa: pendiente de liberación (con fechas), liberado y retirado, sin
    depender de entrar al panel de Mercado Pago.
10. Cero secretos en repo/BD; todo por env.
11. Tests nuevos verdes + suite existente sin regresiones (módulo `app`). Tests adicionales
    del refinamiento: firma de webhook (manifest correcto/incorrecto/replay viejo),
    idempotencia por `x-request-id`, guard exento para `MP_*` manual, barrido de colgadas.

## 5. REGLAS DURAS (heredadas del repo)

- NO correr `makemigrations`/`migrate` sin avisar (hay migraciones sin commitear, el orden
  importa). NO `collectstatic`. NO tocar settings de prod. NO dependencias nuevas sin
  aprobación (propuesta pendiente: lib `qrcode` si se genera el QR server-side).
- Templates por includes, FBV, jQuery/vanilla, URLs planas en `app/urls.py`, loggers
  configurados, `timezone.now()`, montos CLP enteros (half-up si MP devuelve decimales —
  precedente: `Dte.monto_con_iva`).
- Cerrar cada entrega con la lista copy-paste de comandos pendientes.
