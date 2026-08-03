# Plan: Picking de pedidos ecommerce en TIENDAS — asignación, tiempos, alertas y visibilidad central

**Fecha:** 2026-08-03
**Sistemas:** SistemaRetailMind (RM) + VicentAllEcommercesConected (AC)
**Estado:** ✅ IMPLEMENTADO (fases 1–4, 2026-08-03) — pendiente: `migrate app 0197` (RM), deploy AC + `sync_beat_schedule --apply`, prueba en navegador y piloto en 1 tienda. Tests: RM `test_picking_ecommerce` 14/14 PASS · AC `test_sync_tracking_rm` 6/6 PASS. Fase 5 (unificar bitácora AC) NO implementada (opcional).

---

## 1. El problema

Hoy hay DOS flujos de preparación de pedidos ecommerce y solo UNO está instrumentado:

| | Central (bodega) | Tiendas (sucursales) |
|---|---|---|
| Dónde se opera | AllConnected | RetailMind (menú Ecommerce) |
| Detalle para sacar | PDF térmico desde AC | ❌ Guía existe pero manual, 1×1, escondida en el detalle |
| Registro de "sacado" | ✅ BitacoraPedido (`En Preparacion`) | ⚠️ Sub-estados existen pero casi no se usan (nadie los marca) |
| Medición de tiempo | ✅ Conciliación diaria + hitos | ⚠️ Solo total recepción→factura (`tiempo_procesamiento_min`) |
| Alerta al terminar | N/A (central se auto-ve) | ❌ No existe |
| Visibilidad central | ✅ Filtros POR_SACAR / FALTA_EMPAQUE / etc. | ❌ AC no ve NADA del avance en tienda (solo "facturado sí/no" a demanda) |

La tienda factura en RM pero no tiene una cola de trabajo clara ni deja rastro de cuándo sacó el pedido. Central no sabe si un pedido asignado a NICK2 está sacado, listo o abandonado.

## 2. Lo que YA existe (no construir de nuevo)

### RetailMind
- `PedidoEcommerce` con pipeline completo de sub-estados: `RECIBIDO → ASIGNADO → EN_PREPARACION → LISTO_DESPACHO` (+ transiciones validadas en `TRANSICIONES_SUB_ESTADO`), `prioridad`, `sucursal`, `fecha_asignacion`, `asignado_por` — [app/models/ecommerce.py](../retailmind/app/models/ecommerce.py)
- **Auto-asignación al ingestar**: si todos los ítems tienen stock en la sucursal → nace `ASIGNADO` con `fecha_asignacion` (`_ingestar_pedido_dict`)
- `HistorialPedidoEcommerce` append-only (usuario + fechas de cada transición)
- `MetricaAsignacionPedido` (reasignaciones, stock, `tiempo_procesamiento_min` recepción→factura)
- APIs: `api_cambiar_sub_estado`, `api_reasignar_pedido`, `api_sugerir_sucursal`, `api_distribuir_pedidos`, `api_historial_pedido`
- **Impresión térmica QZ Tray por sucursal** ya integrada: `imprimirGuiaPreparacion()` en [pedido_ecommerce_detalle.html:1341](../retailmind/app/templates/app/ecommerce/pedido_ecommerce_detalle.html) (manual, no registra nada) + impresión post-factura individual y masiva
- Scoping por sucursal (`_scope_sucursal_pedidos`) y por empresa; permisos `_verificar_permiso_ecommerce`
- `ecommerce_dashboard_asignacion` con KPIs por sucursal (reasignación, sin stock, tiempo promedio)
- API para AC: `GET /app/api/ecommerce/pedidos/consultar/` (`api_asignar_ticket_rm`) — hoy devuelve solo `estado`, ticket_id, dte_id

### AllConnected
- Tracking operativo central via `BitacoraPedido`: `Etiqueta Impresa` → `En Preparacion` (="sacado") → `Empaquetado` → `Chequeo Interno`; filtros `POR_SACAR / IMPRESO_SIN_SACAR / FALTA_EMPAQUE / FALTA_CHEQUEO / COMPLETO` ([orders/api/views.py:534](../../VicentAllEcommercesConected/system/orders/api/views.py))
- Conciliación diaria de sacados vs completos vs extraviados ([orders/conciliacion.py](../../VicentAllEcommercesConected/system/orders/conciliacion.py))
- `consultar_estado_pedido_rm(pedido)` — consulta per-pedido a RM (facturado sí/no), contrato defensivo
- `AlertaSistema` + `crear_alerta_sistema()` con **dedup + email + campana topbar** ([audit/alertas.py](../../VicentAllEcommercesConected/system/audit/alertas.py))
- Celery beat (30+ jobs) y auth bidireccional ya montada: `X-RetailMind-Key` (AC→RM) y `X-AllConnected-Key` (RM→AC)
- `Pedido.metadatos` JSONField — permite cachear tracking RM **sin migración** sobre `orders/`

## 3. Análisis de opciones

### Opción elegida: **"RM opera, AC observa y alerta" (pull batch + caché)**

- La tienda trabaja SOLO en RM (donde ya factura). El acto de **imprimir la guía** marca el pedido como "en preparación" y el botón **"Listo"** marca fin de picking — cero fricción, el rastro sale gratis.
- AC hace **pull batch cada 10 min** de los estados de tienda y los cachea en `Pedido.metadatos` → columna/filtro en su gestión de pedidos + **alertas automáticas** (listo para retiro / atrasado) con la infra `crear_alerta_sistema` que ya tiene dedup y email.
- Es el mismo patrón que ya usan para stock (sync periódico + reconciliación como red de seguridad), con las mismas API keys.

### Descartadas (y por qué)

1. **Push RM→AC por webhook en cada transición** (tiempo real): exige endpoint nuevo en AC + cola de reintentos en RM + secret reverso. La latencia de 10 min del pull es suficiente para coordinar retiros; el push se puede añadir DESPUÉS sin rehacer nada (el pull quedaría de red de seguridad). No para v1.
2. **Tiendas operando en AllConnected**: doble sistema para el personal de tienda, AC no modela sucursales ni permisos por tienda, y contradice que RM sea la fuente de verdad de stock/facturación.
3. **Pantalla de picking dedicada con escáner**: sobredimensionado para el volumen actual. La lista scoped + guía térmica + 2 botones cubren el flujo. La guía llevará código de barras del ticket, así que un escáner se puede sumar más adelante sin rediseño.

## 4. Diseño por fases

### FASE 1 — RM: flujo de picking en tienda (núcleo) 🔴

**1a. Modelo** — 4 campos nullable en `PedidoEcommerce` (migración segura, sin backfill):
- `fecha_impresion_guia` (DateTimeField) + `guia_impresa_por` (FK usuario)
- `fecha_inicio_preparacion`, `fecha_listo_despacho` (DateTimeField)
- (`fecha_asignacion` y `fecha_facturacion` ya existen — con esto queda la línea de tiempo completa)

**1b. Endpoint `POST /app/ecommerce/pedidos/<id>/imprimir-guia/`** (`api_imprimir_guia_preparacion`):
- Permiso `puede_editar`. Registra `fecha_impresion_guia` (primera vez) + `guia_impresa_por`.
- Si `sub_estado == 'ASIGNADO'` → transiciona a `EN_PREPARACION` (+ `fecha_inicio_preparacion`) + historial. Reimprimir es idempotente (no duplica transición).
- Devuelve el `print_data` para `imprimirConQZ` (mismo formato que `_print_data_pedido` pero desde `pedido.items`): ticket RM, canal, N° pedido canal, **folio despacho AC (`correlativo`)** para calzar con la etiqueta física, cliente, SKU + nombre + talla + cantidad, código de barras del `numero_ticket_rm`, y rótulo **"GUÍA DE PREPARACIÓN — NO VÁLIDO COMO BOLETA"**.

**1c. Lista de pedidos** ([pedidos_ecommerce_list.html](../retailmind/app/templates/app/ecommerce/pedidos_ecommerce_list.html)):
- Botón 🖨 **Guía** por fila (PENDIENTE en `ASIGNADO`/`EN_PREPARACION`) → llama 1b y luego QZ.
- Selección múltiple → **"Imprimir guías (N)"** en lote (reusa el patrón de `imprimirTodosResultados`).
- Botón ✓ **Listo** cuando `EN_PREPARACION` → `api_cambiar_sub_estado('LISTO_DESPACHO')` sin abrir el detalle.
- Filtro rápido por `sub_estado` + orden prioridad/antigüedad (la tienda entra y ve SU cola).

**1d. `api_cambiar_sub_estado`**: setear `fecha_inicio_preparacion` / `fecha_listo_despacho` al transicionar (si están vacías). El detalle (`imprimirGuiaPreparacion()` existente) pasa a llamar al endpoint 1b para que también registre.

**1e. Facturar**: no se bloquea si no pasó por `LISTO_DESPACHO` (no romper el flujo actual), pero el historial/métrica queda con etapas en null → el dashboard mide adopción ("% facturados con guía impresa").

### FASE 2 — RM: medición de tiempos + SLA 🟠

- **Dashboard asignación** ampliado, por sucursal:
  - **T1 reacción**: asignación → impresión guía
  - **T2 picking**: impresión → listo despacho
  - **T3 espera factura**: listo → facturación
  - **T total**: recepción → facturación (ya existe)
  - % con guía impresa, % marcados listo, promedio y p90 por etapa
- **Tabla "Atrasados AHORA"** (aging en vivo): `ASIGNADO` sin imprimir > X h, `EN_PREPARACION` sin listo > Y h (SLA configurable via env, default 4 h corridas).
- **Semáforo en la lista**: badge de horas desde asignación (verde <2 h, amarillo 2–4, rojo >4) en pedidos PENDIENTE.
- Por persona: `guia_impresa_por` + usuario del historial permiten ranking por preparador (tabla secundaria).
- `exportar_pedidos_csv`: añadir fechas de etapa + duraciones.

### FASE 3 — API RM→AC: visibilidad central 🟠

- **RM**: ampliar respuesta de `GET /app/api/ecommerce/pedidos/consultar/` (solo AGREGAR claves — retrocompatible con el cliente defensivo de AC): `sub_estado`, `sub_estado_display`, `sucursal {id, alias}`, `fechas {asignacion, impresion_guia, inicio_preparacion, listo_despacho, facturacion}`.
- **RM**: endpoint batch nuevo `POST /app/api/ecommerce/pedidos/estado-batch/` (misma `X-RetailMind-Key`): recibe lista de `numero_ticket_rm` (máx ~300), devuelve dict por ticket con los mismos campos.
- **AC**: `consultar_estados_batch_rm(tickets)` en [retailmind_connector.py](../../VicentAllEcommercesConected/system/orders/retailmind_connector.py) (mismo contrato defensivo que nunca lanza).
- **AC**: task Celery `sync_tracking_tiendas_rm_task` (beat cada 10 min — **tocar `CELERY_BEAT_SCHEDULE` requiere OK explícito**):
  - Universo: pedidos con `numero_ticket_rm` ≠ '' de los últimos 90 días cuyo tracking cacheado no sea terminal (`FACTURADO`/`CANCELADO`).
  - Guarda en `Pedido.metadatos['rm_tracking']` = `{estado, sub_estado, sucursal, fechas…, synced_at}` — **sin migración de `orders/`**. Read-modify-write por pedido bajo `select_for_update` para no pisar otros keys de `metadatos`.
- **AC UI**: columna **"Tienda (RM)"** en gestión de pedidos (badge: — / Asignado / 🔵 Preparando / 🟢 Listo / ✅ Facturado) + filtro; bloque timeline RM en el detalle del pedido.

### FASE 4 — AC: alertas automáticas 🟢

Dentro de la MISMA task de sync (detectando transiciones vs el valor cacheado anterior):
- **→ `LISTO_DESPACHO`**: `crear_alerta_sistema` "📦 Pedido listo en tienda NICK2: MP-000123 (RM-456) — coordinar retiro/despacho" (dedup por pedido+sub_estado; email según config existente). **Así "la tienda envía la alerta" sin hacer nada extra: marcar Listo ES la alerta.**
- **SLA vencido**: tracking `ASIGNADO` con `fecha_asignacion` > X h sin preparación → alerta tipo `PEDIDO_PROBLEMA` (dedup 1/día por pedido).
- Tipo de alerta: usar `SISTEMA`/`PEDIDO_PROBLEMA` existentes para NO tocar choices (evita migración en AC). Si se quiere un tipo dedicado `PEDIDO_LISTO_TIENDA`, es una migración menor en `audit/` — decisión aparte.

### FASE 5 (OPCIONAL, después de estabilizar 1–4) — Unificar reporting en AC ⚪

Que el sync escriba `BitacoraPedido` (`En Preparacion` con comentario "Sacado en tienda X (RM)") para que **hitos, filtros POR_SACAR y la conciliación diaria cuenten los pedidos de tienda igual que los centrales**. ⚠️ ANTES revisar side-effects: `motivo_bloqueo_sustitucion`, `_revertir_si_sacado_no_empaquetado`, filtro `IMPRESO_SIN_SACAR` — la bitácora es semántica operativa central y meterle eventos de tienda puede confundir la conciliación. Riesgo medio; no entra en v1.

## 5. Archivos a tocar

**RetailMind** (fases 1–3):
- `retailmind/app/models/ecommerce.py` + migración (4 campos nullable)
- `retailmind/app/views_ecommerce.py` (endpoint imprimir-guía, timestamps en cambiar_sub_estado, consulta ampliada, batch, dashboard)
- `retailmind/app/urls.py` (2 rutas nuevas)
- `retailmind/app/templates/app/ecommerce/pedidos_ecommerce_list.html` (botones, bulk, semáforo, filtro)
- `retailmind/app/templates/app/ecommerce/pedido_ecommerce_detalle.html` (hook imprimir→registrar)
- `retailmind/app/templates/app/ecommerce/dashboard_asignacion.html` (KPIs etapa + aging)
- `retailmind/app/tests/test_picking_ecommerce.py` (nuevo)

**AllConnected** (fases 3–4):
- `system/orders/retailmind_connector.py` (cliente batch)
- `system/orders/tasks.py` (task sync + alertas)
- `vicentEcommerces/settings.py` `CELERY_BEAT_SCHEDULE` (⚠️ requiere OK explícito)
- Template gestión pedidos + `_gp_filtros.html` (columna + filtro) y detalle (timeline)
- Test de la task con requests mockeado

## 6. Riesgos y precauciones

- **.env local = PROD en ambos repos**: tests solo contra SQLite (`$env:DATABASE_URL="sqlite:///C:/temp/t.sqlite3"`); nada de `migrate`/`runserver` sin aviso.
- Migración RM: 1 sola, campos nullable, sin backfill — pero **hay migraciones de otras features en cola** (0196 neteo NC pendiente de aplicar; choice REASIGNACION_DESTINO pendiente de makemigrations). Coordinar orden antes de generar la nueva.
- QZ Tray: habilitar `QZ_CONFIG` en cada tienda que prepara (config por sucursal ya existente). Sin QZ el botón cae a "descargar/ver guía" (fallback HTML imprimible).
- Gotcha conocido: el footer trae jQuery — el JS del módulo va DESPUÉS del include del footer.
- API retrocompatible: solo AGREGAR claves a la respuesta de `consultar/` (el cliente AC la lee defensivamente).
- AC: cero migraciones en v1 (todo en `metadatos`); si después se quiere filtro indexado por estado tienda, evaluar campo real con OK previo.

## 7. Orden de ejecución y estimación

| Fase | Alcance | Esfuerzo | Valor |
|---|---|---|---|
| 1 | RM picking (guía registra, bulk, botón Listo) | ~½ día | La tienda tiene cola de trabajo y deja rastro |
| 2 | RM tiempos + SLA + semáforo | ~½ día | Medición por etapa/sucursal/persona |
| — | **Piloto 1 tienda** (validar flujo real + impresora) | 1–2 días calendario | Ajustes finos |
| 3 | API batch + sync AC + columna/filtro | ~1 día | Central VE el avance de tiendas |
| 4 | Alertas AC (listo / atrasado) | ~2–3 h | Coordinación de retiros sin preguntar |
| 5 | (Opcional) unificar bitácora/conciliación AC | aparte | Reporting único central+tiendas |

**Métricas de éxito**: % pedidos de tienda con guía impresa > 90 %; T1 (reacción) mediana < 2 h; central deja de llamar a tiendas para preguntar "¿lo sacaste?"; 0 pedidos "listos" sin retirar > 24 h sin alerta.
