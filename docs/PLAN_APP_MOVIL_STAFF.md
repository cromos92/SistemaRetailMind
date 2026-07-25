# Plan — App móvil interna de staff ("NEXO Móvil")

> Fecha: 2026-07-25. Estado: **v0.1 IMPLEMENTADA** (2026-07-25 tarde) — proyecto
> `DjangoProyects/appNexoStaff` creado (login staff + código autorización + ajuste
> stock con escáner pendiente), `flutter analyze` limpio, tests PASS, APK debug
> compilado. Revisión adversarial de 22 agentes aplicada: fixes en la app (refresh
> resiliente, idempotencia de ajuste) y en el backend (`api/mobile/views.py`:
> sucursal del body validada + permiso `ajuste_stock_rapido` + idempotencia + tz
> Chile; `api/desktop/serializers.py`: valida sucursal contra el set completo;
> throttle 10/min en login/sucursales). Backend SIN commit.
>
> Pregunta que responde: *"¿podemos crear una app propia y qué se podría hacer desde ella?
> ¿Sirve la app de fidelización como base?"*

**Respuesta corta: SÍ sirve como base, pero como PLANTILLA para una segunda app, no para
extenderla.** La app de fidelización (`DjangoProyects/appFidelizacion`) es de **clientes
finales** (login RUT+OTP, puntos, carnet). Mezclarle un "modo staff" contaminaría auth,
seguridad y publicación en tiendas. Lo correcto es **clonar su esqueleto técnico** (que es
justo la parte cara de construir) en un proyecto Flutter nuevo para uso interno.

---

## 1. Lo que YA existe y se reutiliza

### 1.1 Backend (SistemaRetailMind) — sin escribir una línea ya hay:

| Pieza | Ruta | Estado |
|---|---|---|
| Login staff JWT (username+password+sucursal, device_id auto) | `POST /api/v1/desktop/login/` | ✅ productivo (lo usa NEXO POS Tauri) |
| Refresh (7 días) / Logout | `POST /api/v1/desktop/refresh/` · `logout/` | ✅ |
| Selector de sucursales pre-login | `GET /api/v1/desktop/sucursales/` | ✅ |
| **Código de autorización dinámico** (admin/jefe_local) | `GET /api/v1/mobile/codigo-autorizacion/actual/` | ✅ ya pensado para móvil |
| **Ajuste rápido de stock** por SKU+concepto | `POST /api/v1/mobile/ajuste-stock-rapido/` | ✅ ya pensado para móvil |
| Consulta saldo puntos cliente | `GET /api/v1/desktop/fidelizacion/saldo/` | ✅ |
| Consulta/validación gift card | `GET/POST /api/v1/desktop/giftcards/...` | ✅ |
| Consulta/aplicación vale de canje | `/api/v1/desktop/canje/...` | ✅ |

El paquete `app/api/mobile/` ([views.py](../retailmind/app/api/mobile/views.py)) es el
hogar natural de todos los endpoints nuevos: `APIView` + `JWTAuthentication` +
`IsAuthenticated`, chequeo de rol a mano (mismo patrón de las 2 vistas existentes).
El JWT del login desktop trae claims `rol`, `sucursal_id`, `device_id` → sirven para
autorizar sin round-trips.

### 1.2 Flutter (appFidelizacion) — el "core" es 100% transplantable

De `lib/core/` se copia casi todo tal cual:

- `api_client.dart` — dio + interceptor con refresh automático y lock anti-refresh-doble
  (solo cambiar rutas de auth a `/api/v1/desktop/`)
- `token_storage.dart` (secure storage), `config.dart` (baseUrl por entorno)
- `theme.dart` (paleta NEXO `#0066FF`/`#1A1A2E`/`#00D4AA`), `formatters.dart` (CLP es_CL)
- `router.dart` (go_router + guard de sesión), `error_text.dart`, `api_exception.dart`
- Widgets: `main_shell` (bottom nav), `skeleton`, `estado_vista`, `brand_*`
- `bio_lock.dart` (biometría al abrir) y `push_registrar.dart` (FCM ya cableado)

**Reuso estimado: 60-70% del esqueleto.** Lo que se escribe de cero son las pantallas de
negocio, que es lo barato.

Lo que CAMBIA respecto a la app de clientes:
- Auth: username+password (+ sucursal) en vez de RUT+OTP. El PIN 2FA de `users` puede
  quedar como candado biométrico local (bio_lock) en v1.
- Distribución: **no va a las tiendas públicas**. Android: APK directa o Play "pruebas
  internas" (gratis, mismo $25 de la cuenta ya pagada). iOS solo si de verdad se necesita
  (TestFlight). Eso elimina reviews de Apple/Google del camino crítico.

---

## 2. Qué se podría hacer desde la app — catálogo por fases

Criterio de orden: primero lo que NO requiere backend nuevo, después read-only de alto
valor, después escritura con permisos, al final gestión avanzada.

### Fase 0 — Fundación (backend nuevo: CERO)

1. **Login staff** contra `/api/v1/desktop/login/` con selector de sucursal + biometría.
2. **Código de autorización dinámico en el bolsillo** — el admin/jefe autoriza descuentos
   u operaciones en caja leyendo el código desde el celular, sin correr al PC. *(endpoint listo)*
3. **Ajuste rápido de stock** tipeando SKU. *(endpoint listo)*

→ Con solo esto la app ya paga el esfuerzo: es el caso "estoy en el local, no en el PC".

### Fase 1 — Consulta (read-only; backend: 3-4 endpoints delgados en `app/api/mobile/`)

4. **Escáner de códigos con la cámara** (paquete `mobile_scanner`): apuntar a la etiqueta
   → SKU → stock por sucursal + precio + oferta vigente. El celular se vuelve pistola
   inalámbrica. Reutiliza la lógica de consulta existente; endpoint nuevo
   `GET mobile/producto/<sku>/` (stock multi-sucursal usa `stock_sucursal()` como el POS).
5. **Ventas de hoy** — mini dashboard: $ / unidades / nº tickets / ticket promedio por
   sucursal, comparado vs ayer y mismo día semana pasada. Regla ya conocida: contar
   `VENTA_PUBLICO` (misma exclusión de `'VENTA'` que el Resumen de Caja).
6. **Consulta fidelización/gift card de un cliente** en el mesón. *(endpoints desktop listos)*
7. **DTEs del día / alertas DTE** — emitidos, rechazados, pendientes (el limbo DTE ya
   tiene servicio propio: `services/limbo_dte.py`).

### Fase 2 — Operación con escritura (backend: reusar flujos web existentes vía API)

8. **Ajuste de stock con escáner** (une 3 + 4: escanear → concepto → cantidad → listo).
9. **Aprobaciones desde el celular**: aprobar/rechazar **devolución por garantía**
   (el flujo 2 pasos solicitud→aprobación ya existe en web con permiso `puede_aprobar`);
   después solicitudes de cambio de producto y requerimientos. Es el caso de uso más
   valioso para el dueño: hoy la aprobación espera a que alguien esté frente al PC.
10. **Notificaciones push** (FCM ya cableado en la app de clientes; `push_registrar.dart`
    se reutiliza): venta grande, diferencia de arqueo, DTE rechazado, quiebre de stock
    (`stock_notifier.py` ya existe como fuente). Requiere UNA migración: modelo
    `DispositivoPushStaff` (o generalizar el de fidelización) — **avisar antes de crearla**.
11. **Resumen/arqueo de caja del día** por sucursal (read-only del cierre).

### Fase 3 — Gestión (para después; consumen vistas/reportes ya auditados)

12. KPIs de compra/cobertura (indicador de compra, sobre-stock, liquidación) — versión
    móvil de los dashboards de jul-2026.
13. Trazabilidad DTE / cuadratura desde el celular.
14. **Asistente Claude en la app** — la app `assistant` ya existe en el backend; exponerla
    como chat móvil ("¿cuánto vendió NICK1 hoy?") es la interfaz móvil más barata de
    construir por metro de valor.

---

## 3. Trabajo backend por fase

| Fase | Endpoints nuevos | Migraciones | Riesgo |
|---|---|---|---|
| 0 | 0 | 0 | nulo |
| 1 | 3-4 (producto/sku, ventas-hoy, dtes-hoy) | 0 | nulo (read-only) |
| 2 | 3-5 (aprobaciones, push register, arqueo) | 1 (push staff) | bajo (reusa servicios web con mismos permisos) |
| 3 | según se priorice | 0-1 | bajo |

Reglas a respetar (las mismas de siempre):
- Permisos: chequear `rol` + `PermisoSucursal` igual que el menú web (recordar el caso
  NICK1: `PermisoSucursal.habilitado=False` bloquea incluso al admin).
- Todos los endpoints al paquete `app/api/mobile/` con el patrón existente; URLs planas.
- `timezone.now()` + `America/Santiago`; loggers configurados; nada de `print()`.
- El `.env` local apunta a PROD → el desarrollo de la app se prueba contra endpoints
  read-only o con dry-run; nada de `manage.py test` por defecto.

## 4. Trabajo Flutter

1. Proyecto nuevo (p. ej. `DjangoProyects/appNexoStaff`), `applicationId` propio.
2. Copiar `lib/core/` desde appFidelizacion; ajustar `config.dart` y rutas de auth.
3. Pantallas F0: login (user+pass+sucursal), home con 2 tarjetas (código autorización,
   ajuste stock). Con eso sale la v0.1 instalable.
4. F1: escáner (`mobile_scanner`), ficha de producto, ventas-hoy.
5. F2: aprobaciones + push.

## 5. Alternativas descartadas (y por qué)

- **Extender la app de clientes con modo staff** — mezcla dos audiencias y dos auth en un
  binario publicado en tiendas públicas; un bug de permisos expondría datos internos a
  clientes. No.
- **PWA / web responsive** — ya existe la web y el dolor es justamente la UX móvil, el
  escáner de cámara y el push confiable. Una PWA resuelve mal las tres cosas.
- **Meter los endpoints en `desktop/`** — `desktop/` tiene semántica de dispositivo POS
  autorizado (`IsAuthorizedDevice`); `mobile/` ya existe con la semántica correcta.

## 6. Orden sugerido de arranque

1. v0.1 (Fase 0): clonar core + login + código autorización + ajuste stock. **Sin tocar
   el backend.** Instalable por APK en el celular del dueño en la primera iteración.
2. v0.2 (Fase 1): escáner + ficha producto + ventas-hoy (3 endpoints read-only).
3. v0.3 (Fase 2): aprobación devolución garantía + push (1 migración, avisada).
4. Evaluar con uso real antes de invertir en Fase 3.

---

## Anexo A — Catálogo general por dominio (mapeado del código real, 2026-07-25)

> Resultado de un barrido con 7 agentes sobre el backend. Cada feature está respaldada
> por código que YA existe (archivo:función verificados). "Esf" = esfuerzo del wrapper
> móvil (bajo = la lógica ya es JSON y casi solo falta el endpoint JWT en `api/mobile/`).
> Patrón común a casi todos: la vista web resuelve sucursal/empresa por **sesión**
> (`idSucursalActual`/`idEmpresaActual`); el wrapper JWT debe recibir `sucursal_id` por
> parámetro o header `x-sucursal-id` (ya permitido en CORS) y validar con
> `puede_ver_sucursal`/rol, como ya hace `AjusteStockRapidoView`.

### A.1 Ventas, caja y POS

| Feature | Tipo | Esf | Valor | Respaldo |
|---|---|---|---|---|
| Ventas del día en tiempo real por sucursal | consulta | bajo | alto | `views_modulo_ventas.py:dashboard_stats` + `views_dashboard_home.py:api_dashboard_ventas_tiempo_real` |
| Historial de tickets con filtros + detalle | consulta | bajo | alto | `views_modulo_ventas.py:obtener_tickets_venta`, `construir_ticket_data` |
| Análisis de fraude de caja (score por cajero/sucursal) | consulta | bajo | alto | `services/analisis_caja.py:AnalisisFraudeCaja` (servicio puro) |
| Arqueos históricos + detalle + bitácora | consulta | medio | alto | `listar_arqueos` (ya soporta override de sucursal para supervisor) |
| Cuadratura del día por método de pago | consulta | medio | alto | `_calcular_cuadratura_data` (helper puro sucursal+fecha) |
| Confirmar depósitos bancarios (con foto comprobante) | operación | medio | alto | `obtener_depositos_pendientes`, `confirmar_deposito`, `verificar_deposito` |
| Alerta de ventas post-cierre de arqueo | consulta | bajo | medio | `verificar_ventas_post_cierre` |
| Fraude en cambios/devoluciones | consulta | bajo | medio | `services/fraud_detection.py:obtener_analisis_avanzado` |
| Anular ticket PENDIENTE con motivo | operación | medio | medio | `anular_ticket_pendiente` (combina con código autorización móvil) |
| Ranking de vendedores 30 días | consulta | bajo | medio | `obtener_metricas_vendedores` (OJO: hoy no filtra por sucursal) |

### A.2 Inventario y existencias

| Feature | Tipo | Esf | Valor | Respaldo |
|---|---|---|---|---|
| Consulta producto por SKU con escáner de cámara (precio, stock, foto) | consulta | bajo | alto | `views.py:buscar_producto_por_sku` |
| Stock multi-sucursal + tarjeta de movimiento ("¿dónde queda la 42?") | consulta | medio | alto | `views_modulo_existencias_nuevo.py:api_tarjeta_movimiento` (modo resumido para móvil) |
| Ajuste rápido de stock | operación | **listo** | alto | `api/mobile/views.py:AjusteStockRapidoView` (endpoint ya operativo) |
| Kardex/movimientos de un SKU | consulta | bajo | medio | `views_modulo_existencias.py:obtener_movimientos_producto` |
| Traspasos: crear / aprobar / **recibir con escáner** | operación | medio | alto | `crear_traspaso`, `aprobar_traspaso`, `recibir_traspaso` (transaccional completo) |
| Toma de inventario: celular como pistola de conteo | operación | medio | alto | `views_gestion_inventarios.py:registrar_conteo`/`registrar_reconteo` (reemplaza Excel de pistola) |
| Resumen de existencias valorizado (capital inmovilizado) | consulta | medio | alto | `views_resumen_existencias.py:obtener_resumen_existencias` (solo stock actual en móvil) |
| Despachos CD→tiendas: pendientes + confirmación | operación | alto | medio | `api_pendientes_despacho_sucursal`, `api_historial_despachos` (v1 solo read-only) |
| Etiquetas: SKUs/precios de un artículo | consulta | medio | medio | `views_etiquetas_zebra.py:obtener_skus_articulo` (imprimir desde el cel = cola nueva, v2) |
| Fusión de duplicados detectada en sala | gestión | medio | medio | `views_fusion_duplicados.py:api_ejecutar_fusion` (solo admin, doble confirmación) |

### A.3 Compras y recepción

| Feature | Tipo | Esf | Valor | Respaldo |
|---|---|---|---|---|
| KPIs deuda a proveedores + vencimientos (vencido/7 días/al día) | consulta | bajo | alto | `views_modulo_compras.py:obtener_resumen_pendientes_anio` |
| Facturas de compra con estado de pago (paginado) | consulta | bajo | alto | `obtener_dte_compras` |
| Detalle factura: pagos y saldo | consulta | bajo | alto | `views.py:obtenerDetallePago` (⚠️ usar la de views.py; la de views_modulo_compras está rota) |
| Registrar pago a proveedor | operación | medio | alto | `views.py:registrarPagoDTE` (misma advertencia de duplicado roto) |
| % avance de recepción de una compra | consulta | bajo | alto | `obtener_pendientes_compra` + `obtener_recepciones_compra` (la app estrena el badge pendiente) |
| Órdenes de compra del año | consulta | bajo | medio | `obtener_compras_por_anio` (excluir ELIMINADA) |
| Traspasos en camino a mi sucursal (badge) | consulta | medio | alto | `obtener_dtes_pendientes_recibir`, `recepciones_pendientes_api` |
| Aprobar/rechazar solicitudes de regularización | gestión | medio | alto | `obtener_solicitudes_recibidas` + `decidir_solicitud_api` (la ejecución sigue en PC) |
| Dashboard compras estratégico (pareto proveedores, alertas) | consulta | medio | medio | `dashboard_compras_mejorado_api` (pedir por secciones) |
| Facturas pendientes de recepcionar por proveedor | consulta | bajo | medio | `views.py:facturas_pendientes` (⚠️ NO usar `recepcionar_compra`, deprecado) |

### A.4 DTE / SII / documentos

| Feature | Tipo | Esf | Valor | Respaldo |
|---|---|---|---|---|
| Documentos emitidos del día + búsqueda (folio/RUT/SKU) | consulta | bajo | alto | `views.py:cargar_dte_ventas` (ya resuelve sucursal por EmpresaUser → casi directo a JWT) |
| Detalle completo de un DTE (líneas, pagos, stock) | consulta | bajo | alto | `views.py:detalle_dte`, `api_detalle_dte_completo` |
| Trazabilidad de un folio (árbol padre/hijos/movimientos) | consulta | bajo | alto | `views.py:api_dte_trazabilidad` (JSON puro) |
| Limbo Inbox: traspasos rechazados/parciales/estancados | consulta | medio | alto | `obtener_dtes_limbo_emisor_api` + `obtener_resumen_limbo_dte_api` |
| Notificaciones DTE con badge no-leídas | consulta | bajo | alto | `obtener_notificaciones_dte` + modelo `NotificacionDTE` — candidata Nº1 a push |
| Semáforo de folios (CAF por agotarse) | consulta | bajo | alto | `verificar_correlativos_disponibles` |
| DTEs rechazados: lista + rehabilitar | operación | medio | medio | `obtener_dtes_rechazados_api`, `rehabilitar_dte_rechazado_api` |
| Compartir TXT Acepta por email/WhatsApp | operación | bajo | medio | `generar_txt_desde_dte_existente` (respetar permiso `dte_descargar_txt`) |
| Anular factura con NC 61 (admin, doble confirmación) | operación | medio | medio | `anular_factura_dte` (ya endurecido; pendiente staging) |
| Resolver limbo: NC con/sin devolución desde terreno | gestión | alto | alto | `services/limbo_dte.py` (requiere orquestación nueva del wizard) |

### A.5 Reportes, dashboards y predicciones

| Feature | Tipo | Esf | Valor | Respaldo |
|---|---|---|---|---|
| Ventas por sucursal en rango (día/semana/mes) | consulta | bajo | alto | `views_modulo_reportes.py:obtener_ventas_por_sucursal_reporte` |
| Ranking vendedores + comisiones | consulta | medio | alto | `obtener_comisiones_por_vendedor` (gate `_puede_ver_reporte_comisiones`; "solo mis comisiones" = filtro menor nuevo) |
| Ventas por categoría/especialidad v1.2 | consulta | bajo | alto | `views_modulo_ventas.py:obtener_ventas_por_categoria`/`por_especialidad` (ya van por querystring) |
| Indicador de compra / cobertura (QUIEBRE vs SOBRE-STOCK) | consulta | bajo | alto | `obtener_indicador_compra_categoria` (forzar filtro por sucursal en móvil) |
| Resumen predicción de compras + plan por proveedor | consulta | medio | alto | `views_prediccion_compras.py:api_prediccion_resumen`/`api_prediccion_sugerencias` |
| Alertas de velocidad y quiebre de talla | consulta | bajo | alto | `api_prediccion_alertas_velocidad`/`_quiebre` + drill-down `api_prediccion_producto_detalle` |
| Aprobar sugerencias de compra (individual/lote) | operación | bajo | medio | `api_prediccion_aprobar_sugerencia` (idempotente; falta permiso fino) |

### A.6 CRM, fidelización, gift cards, cotizaciones, créditos, requerimientos

| Feature | Tipo | Esf | Valor | Respaldo |
|---|---|---|---|---|
| Puntos de un cliente por RUT (en el mesón) | consulta | bajo | alto | `api/desktop/fidelizacion_views.py:SaldoPuntosView` (JWT ya listo) |
| Gift card: consulta y validación de saldo | consulta | bajo | alto | `GiftCardConsultaView`/`GiftCardValidarView` (JWT ya listo) |
| Vale de canje: validar y aplicar (idempotente) | operación | bajo | medio | `ValeCanjeConsultaView`/`ValeCanjeAplicarView` (JWT ya listo) |
| Ficha fidelización + alta rápida de cliente | gestión | medio | medio | `views_modulo_fidelizacion.py:api_detalle_cuenta`, `api_registrar_cliente` |
| Cotizaciones: listado, detalle, despacho pendiente | consulta | medio | alto | `views_modulo_cotizaciones.py:listar_cotizaciones`, `detalle_cotizacion` |
| Reenviar cotización por correo / compartir PDF | operación | medio | medio | `cotizacion_pdf`, `enviar_cotizacion_correo` (PDF server-side ya existe) |
| Créditos de trabajadores por cobrar (scoping por rol listo) | consulta | medio | alto | `views_modulo_creditos.py:cargar_creditos_trabajadores` |
| Aprobar crédito / registrar pago de cuota | operación | medio | medio | `aprobar_credito_trabajador`, `registrar_pago_credito` |
| Bandeja de requerimientos con semáforo de urgencia | consulta | medio | alto | `views_modulo_requerimientos.py:listar_requerimientos` + props `nivel_urgencia` |
| **Crear requerimiento con fotos desde la cámara** | operación | alto | alto | `crear_requerimiento` + matriz `TipoFotoRequerimiento` (el caso móvil por excelencia) |

### A.7 Asistente IA y alertas

| Feature | Tipo | Esf | Valor | Respaldo |
|---|---|---|---|---|
| Chat con el asistente Claude (24 tools, datos reales) | consulta | medio | alto | `assistant/agent.py:AssistantAgent.chat` (solo falta wrapper JWT; hoy `api_chat` es sesión) |
| Resumen ejecutivo del día (KPIs + contadores de alertas) | consulta | bajo | alto | `assistant/tools.py:get_resumen_diario` (dict JSON-ready, sin pasar por el LLM) |
| Centro de notificaciones DTE | gestión | bajo | alto | `obtener_notificaciones_dte` (ver A.4) |
| Alertas de velocidad de venta / quiebre de talle | consulta | bajo | alto | ver A.5 (cron cada 30 min ya las mantiene frescas) |
| Stock crítico de la sucursal | consulta | bajo | medio | `AssistantTools.get_productos_stock_bajo` (resuelve sucursal por EmpresaUser) |
| Notificaciones de cambios de precio (por usuario) | gestión | bajo | medio | `views_modulo_gestion_precios.py:obtener_notificaciones_precio` (modelo ya por-usuario, candidata Nº2 a push) |
| Historial / multi-conversación del asistente + feedback | consulta | bajo | medio | `assistant/views.py:api_history`, `api_new_conversation`, `api_feedback` |
| Panel antifraude cambios/devoluciones (dueño) | consulta | medio | medio | `services/fraud_detection.py:obtener_analisis_avanzado` (servicio puro) |
| **Push al celular** (alerta CRÍTICA, DTE, precio por aprobar) | operación | alto | alto | Generadores ya existen (`evaluar_alertas_pendientes` cron, `signals.py`); falta canal FCM staff (1 migración) — convierte todo lo anterior de "entrar a mirar" a tiempo real |

## Anexo B — Módulo "Verificador de Etiquetas" (pedido 2026-07-25)

> Requerimiento del usuario: escanear el código de barras con la cámara O sacar foto de la
> etiqueta física; extraer de la foto el PRECIO y la FECHA impresos; el sistema busca el
> SKU y compara etiqueta vs sistema; si no calzan, corregir desde la app (precio) y además
> recategorizar: categoría, especialidad, marca, color, género.
> Investigado contra el código real (3 agentes). **Veredicto: 100% factible; la mayor
> parte del backend ya existe.**

### B.1 Qué trae la etiqueta física (verificado en el ZPL real)

La etiqueta Zebra imprime 10 campos ([views_etiquetas_zebra.py:generar_datos_etiquetas](../retailmind/app/views_etiquetas_zebra.py) + `generarZPL` en `gestion_etiquetas_zebra.html`):

| Campo | Formato/posición | Fuente en el sistema |
|---|---|---|
| **Barcode** | CODE128, SKU numérico, sin texto integrado | `Producto_Talla.sku` (BigInteger, truncado a 10 chars) |
| SKU en texto | bajo el barcode | idem |
| **Precio** | `39.990` (miles con punto, sin $), zona media-derecha | `Producto.precioventa` (entero CLP, por Producto — igual en todas las tallas) |
| **Fecha** | `dd-mm-yyyy`, esquina inferior izquierda | **fecha de IMPRESIÓN elegida por el operador** (default hoy) — NO es fecha de ingreso |
| Artículo / talla / marca / color / descripción / sucursal / N° factura | truncados ([:14]/[:4]/[:10]/[:10]/[:28]/[:8]/[:8]) | `Producto.articulo`, `Producto_Talla.talla`, `atributo1/atributo2`, `Dte.numero_documento` |

**Clave del diseño:** existe historial de impresiones con snapshot —
`HistorialImpresionEtiqueta` + `DetalleImpresionEtiqueta.precio_impreso` ([models/etiquetas.py](../retailmind/app/models/etiquetas.py))
— así que dado un SKU se sabe cuándo se imprimió etiqueta y con qué precio.

### B.2 Flujo del módulo en la app

1. **Capturar**: escanear el barcode (`mobile_scanner`) → SKU directo. O foto de la
   etiqueta → OCR **en el propio celular** (`google_mlkit_text_recognition`: offline,
   gratis, sin API paga) que extrae precio (patrón `\d{1,3}(\.\d{3})+` zona derecha),
   fecha (`dd-mm-yyyy` abajo-izquierda) y el SKU del texto bajo el barcode como fallback.
2. **Buscar**: `GET /api/v1/mobile/verificador/<sku>/` (endpoint nuevo, delgado) devuelve
   la ficha completa: precio vigente, categoría (Padre › Hijo), especialidades, marca,
   color, género, talla, stock, foto, y la última impresión registrada (fecha +
   `precio_impreso`).
3. **Comparar** (semáforo):
   - 🟢 **OK** — precio etiqueta == `precioventa` vigente.
   - 🟡 **Etiqueta desactualizada** — no calza con el vigente PERO sí con un
     `precio_impreso` histórico de ese SKU → el precio cambió después de imprimir.
     Acción: reimprimir etiqueta (y opcionalmente corregir precio si el bueno es el impreso).
   - 🔴 **Nunca fue ese precio** — no calza ni con el histórico → etiqueta mal impresa
     o etiqueta de otro producto. Acción: revisar/corregir.
4. **Corregir desde la app**:
   - **Precio**: wrapper móvil sobre la lógica de `actualizar_precio`
     ([views_modulo_gestion_precios.py:812](../retailmind/app/views_modulo_gestion_precios.py)) —
     cambio directo con auditoría completa en `HistorialCambioPrecio` (usuario, IP,
     antes/después, motivo `"Verificador etiquetas móvil"`), actualiza lotes activos y
     sincroniza gemelos de otras sucursales (con umbral `UMBRAL_DIVERGENCIA_PRECIO_PCT`
     que fuerza aprobación si el salto es grande — ya implementado).
   - **Recategorizar**: pantalla con picker árbol categoría padre→hijo
     (`GET /app/categorias_existentes/?tree=1`), especialidades multi
     (`GET /app/opciones_atributo/?atributo_id=<Especialidad>`), marca/color/género
     (mismo endpoint genérico). Guardado vía wrapper sobre `actualizar_producto` +
     bloque de especialidades de `actualizar_productos_masivo`
     ([views_edicion_productos.py](../retailmind/app/views_edicion_productos.py)).

### B.3 Trabajo backend (todo en `app/api/mobile/`, patrón existente)

| Endpoint nuevo | Reusa | Notas |
|---|---|---|
| `GET verificador/<sku>/` | lógica de `buscar_producto_por_sku` + `DetalleImpresionEtiqueta` | read-only; filtrar por sucursal (SKU **no es único** en BD) |
| `POST verificador/precio/` | núcleo de `actualizar_precio` | extraer a helper compartido; añadir chequeo de permiso explícito |
| `POST verificador/atributos/` | `actualizar_producto` + bloque especialidades del masivo | ver gotchas B.4 |
| `GET verificador/opciones/` | `categorias_existentes` + `opciones_atributo` | un solo payload con árbol + marcas + colores + géneros + especialidades |

Sin migraciones. La reimpresión remota de etiquetas (cola que el PC con la Zebra consuma)
es v2 — en v1 el hallazgo 🟡 se anota y se reimprime desde el PC como hoy.

### B.4 Gotchas verificados (respetarlos en la implementación)

1. **`actualizar_producto` anula atributos ausentes**: si no reenvías `atributo1..4_id`
   los deja en `None` → el wrapper móvil SIEMPRE precarga y reenvía los 4.
2. **`actualizar_producto` NO maneja especialidades** (y `obtener_producto_edicion` no
   las devuelve) — solo `actualizar_productos_masivo` las edita (modos
   agregar/reemplazar/quitar, aditivo e idempotente). El wrapper móvil debe combinar ambos.
3. **Propagación a fichas hermanas**: el producto es una fila POR SUCURSAL; la política
   vigente (2026-07) es propagar la edición a todas las filas del mismo `articulo`
   (match EXACTO por texto — cuidado con variantes de color que comparten código).
4. **Género**: el atributo se llama `Sexo` (fallback `Género`); NO ofrecer `DAMA` como
   opción (migrado a `MUJER` en v1.2). Categorías con prefijo `_ZZ_` = deprecadas, excluir.
5. **Permisos**: los endpoints web de precio/edición solo llevan `@login_required` (sin
   rol) — los wrappers móviles deben añadir verificación explícita (rol admin/jefe_local
   o permiso fino `edicion_rapida_precios`), porque la API JWT no pasa por el
   middleware de permisos de páginas.
6. **SKU truncado a 10 chars en el barcode** y `BigIntegerField` sin unique → buscar
   `filter(sku=..., producto__sucursal=...)` como ya hace `AjusteStockRapidoView`.
7. **La fecha impresa no se persiste** (solo la fecha servidor del historial) y el
   operador puede haberla cambiado a mano al imprimir → usarla como referencia, no como
   verdad dura.

### Gotchas transversales detectadas en el barrido

- `views_modulo_compras.py` tiene **duplicados muertos rotos** de `obtenerDetallePago` y
  `registrarPagoDTE` (usan campos inexistentes) — los cableados en `urls.py` son los de
  `views.py`. Igual con `cargar_dte_ventas` (usar la de `views.py`, no la de
  `views_modulo_documentos.py`).
- `recepcionar_compra` en `views_modulo_compras.py` está deprecado (lanza
  `NotImplementedError`) — la recepción/creación de productos se queda en el PC.
- `obtener_metricas_vendedores` agrega global (no filtra sucursal) — cambio menor si se
  quiere ranking por local.
- Aprobar solicitud de regularización NO la ejecuta (nada ejecuta `CAMBIO_PRODUCTO` aún)
  — la UI móvil debe dejarlo claro.
