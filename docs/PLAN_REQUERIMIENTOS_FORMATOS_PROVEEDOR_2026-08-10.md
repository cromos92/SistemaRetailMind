# Requerimientos: auditoría del modal "Nuevo Requerimiento" + formatos por proveedor

Fecha: 2026-08-10
URL analizada: `https://retail.webappsolutions.cl/app/requerimientos/`
Vista: `modulo_requerimientos` → [gestion_requerimientos.html](../retailmind/app/templates/vistas/modulo_requerimientos/gestion_requerimientos.html)

---

## 0. Decisiones tomadas (2026-08-10) y estado

| Decisión del usuario | Efecto |
|---|---|
| El cliente **puede faltar**, pero lo ideal es tenerlo | Campo `origen` (CLIENTE / STOCK). Sin cliente solo se permite en STOCK, y la UI insiste en que se cargue |
| El **administrador** analiza y cierra | Se mantiene el modelo de permisos actual, sin roles nuevos |
| **No** se replican las planillas de cada proveedor: **creamos formato propio** y se adjunta al correo | Se descarta el modelo multi-canal (Excel/portal). Un único PDF RetailMind, siempre igual, con página de respuesta para el proveedor |

**Implementado en esta tanda** (sin desplegar, migraciones `0210` y `0211` creadas y NO aplicadas):
formato PDF propio + adjunto automático al correo · **evidencia en DigitalOcean Spaces** ·
origen/cantidad/factura de compra ·
proveedor sugerido por SKU · fixes de export, PARCIAL, fecha de respuesta, orden de fotos,
tope de adjuntos · correo rediseñado. **39 tests PASS** (29 nuevos).

Pendiente de decidir: multi-SKU por requerimiento (§4.6).

---

## 1. Cómo funciona hoy (mapa real del código)

| Pieza | Archivo |
|---|---|
| Página + modal | [gestion_requerimientos.html](../retailmind/app/templates/vistas/modulo_requerimientos/gestion_requerimientos.html) (modal en línea 600) |
| Formulario del modal | [_crear_requerimiento_form.html](../retailmind/app/templates/vistas/modulo_requerimientos/_crear_requerimiento_form.html) (375 líneas) |
| JS del modal | [_crear_requerimiento_scripts.html](../retailmind/app/templates/vistas/modulo_requerimientos/_crear_requerimiento_scripts.html) (649 líneas) |
| Backend | [views_modulo_requerimientos.py](../retailmind/app/views_modulo_requerimientos.py) (1.626 líneas) |
| Modelos | [requerimientos.py](../retailmind/app/models/requerimientos.py) (`Requerimiento`, `FotoRequerimiento`, `HistorialRequerimiento`, `TipoFotoRequerimiento`) |
| Correo al proveedor | [emails/requerimiento_proveedor.html](../retailmind/app/templates/emails/requerimiento_proveedor.html) |
| Copia de control | [emails/requerimiento_copia_resumen.html](../retailmind/app/templates/emails/requerimiento_copia_resumen.html) |
| Tests | [test_requerimientos_envio.py](../retailmind/app/tests/test_requerimientos_envio.py) (10 tests, solo envío) |

Flujo actual: tienda abre el modal → 5 bloques (tipo, documento/producto, cliente, descripción, fotos) → `POST /app/api/requerimientos/crear/` → estado `PENDIENTE` → **nadie se entera** → el administrador entra al listado, abre el detalle y usa "Enviar a Proveedor" (SweetAlert con correo destino, copia y mensaje) → `ESPERANDO_RESPUESTA` → registra respuesta → `APROBADO`/`RECHAZADO`.

Permisos: solo `rol == 'administrador'` puede enviar a proveedor y registrar respuesta. `jefe_local` gestiona su sucursal. `cajero`/`vendedor` solo crean y ven.

---

## 2. El problema de fondo: el modal modela un reclamo DE CLIENTE, no uno A PROVEEDOR

Éste es el hallazgo principal, y condiciona todo lo demás.

El formulario obliga a que exista un **cliente final** (`cliente_nombre` es requerido en el backend, línea 217) y toma el **documento de venta** (boleta al cliente). Pero un requerimiento de garantía al proveedor necesita otra cosa:

| Lo que pide el modal | Lo que el proveedor exige para aceptar el reclamo |
|---|---|
| Boleta de venta al cliente | **Factura de compra al proveedor** (N° + fecha) |
| Nombre / RUT del cliente | RUT de la empresa reclamante y su código de cliente |
| 1 SKU por requerimiento | Tabla de SKU + **cantidad** por línea |
| — | **Cantidad** (no existe el campo en el modelo) |
| — | Código de artículo del proveedor / OC |

Consecuencias directas:

- **No se puede reclamar merma de bodega.** Si la tienda encuentra 6 pares fallados en stock (sin venta, sin cliente), no hay forma de crear el requerimiento: el backend rechaza sin `cliente_nombre`.
- **El proveedor recibe un correo sin el dato que necesita** (factura de compra) y con datos que no le sirven (RUT del cliente final).
- **Un requerimiento = 1 unidad de 1 SKU.** Reclamar 10 pares son 10 requerimientos y 10 correos.

---

## 3. Auditoría del modal — hallazgos

✅ = corregido en esta tanda · ◐ = mitigado · (sin marca) = sigue abierto.

### 3.1 Bloqueantes de negocio

| # | Hallazgo | Evidencia |
|---|---|---|
| ✅ B1 | `cliente_nombre` obligatorio siempre → imposible reclamar por stock/bodega | `views_modulo_requerimientos.py:217` |
| ✅ B2 | No existe campo **cantidad** en `Requerimiento` | `models/requerimientos.py` |
| ✅ B3 | No existe vínculo con la **factura de compra** (`Dte` COMPRA) ni campo para su número | idem |
| ◐ B4 | El proveedor es **opcional** al crear pero **obligatorio** para enviar; la tienda no sabe cuál es y lo deja vacío **Mitigado:** el proveedor se autocompleta desde la última compra; sigue sin ser obligatorio | form línea 287 vs `views:760` |
| B5 | Al crear, nadie es notificado. El analista tiene que descubrir el requerimiento entrando al listado | no hay hook en `crear_requerimiento` |
| ✅ B6 | `numero_boleta` / `tipo_documento` / `fecha_compra` son `<input type="hidden">`: **solo se llenan si la búsqueda por folio encuentra el documento**. Si falla, el dato se pierde y no hay forma de tipearlo | form líneas 169-171 |

### 3.2 Bugs de backend

| # | Hallazgo | Evidencia |
|---|---|---|
| ✅ A1 | **Export Excel sin scoping por rol**: cualquier usuario logueado descarga TODOS los requerimientos de TODAS las empresas | `exportar_requerimientos`, `views:1509-1511` (el listado sí filtra, el export no) |
| ✅ A2 | Decisión **`PARCIAL` se guarda como estado `RECHAZADO`** | `views:972` (`'APROBADO' if decision == 'APROBADO' else 'RECHAZADO'`) |
| ✅ A3 | El input "Fecha respuesta" del modal de respuesta **no se envía ni se usa**: el backend siempre pisa con `timezone.now()` | `detalle_requerimiento.html:229` vs `views:970` |
| ◐ A4 | `enviar_a_proveedor` **no valida fotos obligatorias ni tamaño de adjuntos**. 8 fotos de celular ≈ 30-40 MB → el SMTP rechaza (Gmail/MailerSend ~25 MB) y el envío falla entero **Mitigado:** tope de adjuntos aplicado; falta bloquear el envío sin fotos obligatorias | `views:841-854` |
| ✅ A5 | `MEDIA_ROOT = BASE_DIR/'media'` (disco local del contenedor). Tras un deploy las fotos desaparecen; `default_storage.exists()` las filtra en silencio y **el correo sale sin evidencia y sin avisar** **Resuelto:** la evidencia va a DigitalOcean Spaces (§9); las fotos ya subidas al disco se migran con `subir_fotos_requerimientos_spaces` | `settings.py:543` + `views:796-799` |
| ✅ A6 | `crear_requerimiento` no valida que la sucursal de sesión pertenezca al usuario | `views:226-233` |
| ✅ A7 | El corte por `max_fotos` recorre `request.FILES` en orden arbitrario: con muchas fotos adicionales puede descartar las **obligatorias** | `views:287-311` |
| ◐ A8 | Los `except Exception` devuelven `str(e)` al cliente y **no loguean**. **Mitigado:** se agregó `logger.exception` en `crear_requerimiento` y en el formato PDF; el resto de los endpoints del módulo sigue igual | `views:324-328`, y en casi todos los endpoints del módulo |
| ◐ A9 | `tipo` y `subtipo` no se validan contra los choices. **Mitigado:** `tipo` y `origen` ya se validan; `subtipo` sigue siendo texto libre (depende del tipo principal) | `views:244-268` |

### 3.3 UI / UX

| # | Hallazgo |
|---|---|
| U1 | El modal es una página completa dentro de un `modal-xl`: 5 tarjetas + sidebar. En un notebook de tienda son ~3 pantallas de scroll dentro del modal. |
| U2 | 7 tarjetas de tipo, con solapamiento real: "Producto Fallado" vs "Garantía" vs "Devolución" vs "Reclamo" no son distinguibles para un vendedor. Los tipos condicionan qué fotos se piden, así que elegir mal degrada la evidencia. |
| U3 | La **sucursal no se muestra**: sale de `request.session['idSucursalActual']`. Si el usuario cambió de sucursal en otra pestaña, el requerimiento queda en la sucursal equivocada sin que nadie lo note. |
| U4 | Sin autoguardado. Con el middleware de timeout de sesión activo, un formulario a medio llenar + fotos se pierde completo. |
| U5 | Los inputs de foto no usan `capture="environment"` ni comprimen: en tienda con 4G, subir 8 fotos de 5 MB es un cuelgue. |
| U6 | `DEVOLUCION` no tiene tipos de foto guiados (solo `FOTO_ADICIONAL` en el seed `0119`): la sección 5 aparece vacía y desconcierta. |
| ✅ U7 | `_crear_requerimiento_scripts.html:2` carga Select2 por CDN **antes** de que exista jQuery (jQuery está en el footer) → `ReferenceError` en consola. Funciona igual porque `layout/footer.html:99` ya carga Select2 después de jQuery: el `<script>` del parcial es redundante y ruidoso. |
| ✅ U8 | `gestion_requerimientos.html` cierra `container-fluid` y `page-content` **dos veces** (285-286 y 616-618) → 2 `</div>` sobrantes que cierran `main-content`/`layout-wrapper` antes de tiempo y sacan el footer del wrapper. |
| U9 | `crear_requerimiento.html` es template muerto: `crear_requerimiento_vista` redirige a `?panel=crear`. |

---

## 4. Formato propio RetailMind (IMPLEMENTADO)

Decisión: en vez de replicar la planilla de cada proveedor, se manda **siempre el mismo documento**, autocontenido y formal, adjunto al correo. El proveedor lo archiva, lo imprime y lo responde.

Generador: [app/services/pdf_requerimiento_proveedor.py](../retailmind/app/services/pdf_requerimiento_proveedor.py)
Función: `generar_pdf_requerimiento(requerimiento, *, usuario=None, plazo_dias=7) -> bytes`

### 4.1 Qué contiene el documento (A4)

**Página 1-2 — el reclamo**

| Bloque | Contenido |
|---|---|
| Cabecera | Empresa emisora + RUT + dirección, sucursal, y caja con el N° de requerimiento y la fecha |
| Título | `SOLICITUD DE <TIPO>` + prioridad y estado |
| Proveedor | Razón social, RUT, correo al que se envió |
| Producto reclamado | SKU y **cantidad** destacados; marca, talla, color y descripción desde la ficha |
| Respaldo | **Factura de compra N° + fecha** (lo que el proveedor exige) y, si hubo venta, documento y cliente |
| Descripción | Tipo, subtipo, severidad, condición, motivo y detalle |
| Evidencia | Fotos incrustadas en grilla de 2, con su etiqueta (“Foto del defecto”, “Etiqueta / SKU”…) |

**Página final — `RESPUESTA DEL PROVEEDOR`**, que es la razón de ser del formato propio: casillas Aprueba / Rechaza / Requiere más información, resolución ofrecida (reposición, NC, reparación, otra), N° de autorización, observaciones, nombre, firma y fecha.

### 4.2 Tres decisiones de diseño

1. **Las fotos van dentro del PDF, reescaladas** (lado mayor 1000px, JPEG q72, corrigiendo la orientación EXIF con Pillow). Antes se adjuntaban 8 fotos de celular sueltas (30-40 MB) y el SMTP rechazaba el envío entero.
2. **Lo que falta se dice en rojo, no se omite**: sin factura de compra o sin fotos, el documento lo declara. Es mejor que el analista lo vea antes de enviar que descubrirlo por el rechazo del proveedor.
3. **Las casillas se dibujan**, no son el carácter `☐`: Helvetica/WinAnsi no tiene ese glifo y ReportLab lo sustituía por una `n` justo en la parte que hay que marcar.

### 4.3 Cómo se usa

| Ruta | Qué hace |
|---|---|
| `GET /app/api/requerimientos/<id>/formato-pdf/` | Abre el formato en el navegador (`?descargar=1` lo baja). Botón **“Ver formato PDF”** en el detalle y link dentro del modal de envío: **revisar antes de enviar** es lo que evita mandar un reclamo incompleto |
| `POST /app/api/requerimientos/<id>/enviar-proveedor/` | Adjunta el PDF automáticamente, además de las fotos originales mientras quepan en `REQUERIMIENTOS_MAX_ADJUNTOS_MB` (default 12) |
| `GET /app/api/requerimientos/sugerir-proveedor/?sku=` | Última compra del SKU (`Dte_Productos → Dte COMPRA → emisor`): devuelve proveedor, N° de factura, fecha y costo. La tienda ya no tiene que saber a quién reclamarle |

Si el PDF falla, el correo **igual sale** (queda registrado en el historial como “SIN formato PDF”): perder el envío por un problema de maquetación sería peor.

### 4.4 Variables de entorno nuevas

- `REQUERIMIENTOS_MAX_ADJUNTOS_MB` (default `12`) — presupuesto total de adjuntos.
- `REQUERIMIENTOS_PLAZO_RESPUESTA_DIAS` (default `7`) — fecha límite que se imprime en el PDF y en el correo.

### 4.5 Campos nuevos en `Requerimiento` (migración 0210)

`origen` (CLIENTE/STOCK) · `cantidad` · `dte_compra` (FK) · `numero_factura_compra` · `fecha_factura_compra` · `cliente_nombre` pasa a `blank=True`.

Todos nullables o con default: los requerimientos existentes quedan `origen='CLIENTE'`, `cantidad=1`, que es exactamente lo que eran.

### 4.6 Pendiente: reclamo multi-SKU

Hoy un requerimiento = 1 SKU. Reclamar 10 pares son 10 documentos. La siguiente etapa natural es `RequerimientoLinea` (sku, cantidad, motivo) con el PDF mostrando una tabla, o un consolidado que agrupe los `PENDIENTE` del mismo proveedor en un solo envío. **Requiere tu decisión** — es la única parte del plan original que quedó fuera.

---

## 5. Mejoras a los formatos existentes

### 5.1 Correo al proveedor — APLICADO ([requerimiento_proveedor.html](../retailmind/app/templates/emails/requerimiento_proveedor.html))

1. **Asunto filtrable**: `[GARANTIA PRODUCTO] · REQ-20260810-0007 · SKU 12345 · FAC 8842 · Realsport`. Antes era solo tipo + número: el proveedor no podía filtrar ni cruzarlo con su sistema.
2. **Ficha de decisión arriba**: producto, cantidad, factura de compra, problema y fecha límite en una sola caja. Antes había que leer 4 tablas para reunir esos datos.
3. **Subtipo con etiqueta humana** (`Despegue de Suela`, no `DESPEGUE_SUELA`) vía la property nueva `Requerimiento.subtipo_display`.
4. **Aviso del PDF adjunto** y de su página de respuesta.
5. **3 botones `mailto:` prellenados** (Aprobar / Rechazar / Pedir info) con el N° de requerimiento y el SKU en el asunto → la respuesta vuelve identificable.
6. **Rama sin cliente**: si `origen='STOCK'` no se imprime una sección de cliente vacía, se declara que el producto se detectó en tienda.
7. **RUT de la empresa** reclamante junto al nombre.

Pendiente (no crítico): miniaturas inline por CID en el cuerpo del correo — hoy la evidencia se ve en el PDF adjunto.

### 5.2 Copia de control — APLICADO

Ahora informa si el formato PDF se adjuntó y cuántas fotos originales viajaron. Sigue sin adjuntos, como corresponde a una copia de certificación.

### 5.3 Export Excel — APLICADO

Alcance por rol (antes cualquiera bajaba todo el holding) + columnas nuevas: Origen, Cantidad, Factura de compra, Decisión del proveedor y Días sin respuesta.

### 5.4 Tope de adjuntos — APLICADO

Las fotos se leen por `default_storage` (no `.path`, que revienta con almacenamiento remoto) y se adjuntan mientras entren en `REQUERIMIENTOS_MAX_ADJUNTOS_MB`. Lo que no cabe viaja igual dentro del PDF y queda avisado en la respuesta y en el log.

---

## 6. Modal — qué se aplicó y qué falta

**Aplicado en esta tanda:**
- **Pregunta de origen** al elegir el tipo: *¿lo reclama un cliente?* / *¿se detectó en stock?* Define si el cliente es obligatorio, con un aviso que empuja a cargarlo igual cuando existe.
- **Cantidad**, **factura de compra** y **fecha de factura** (los datos que el proveedor exige).
- **Proveedor y factura autocompletados** desde la última compra del SKU, con aviso de dónde salió el dato y posibilidad de cambiarlo.
- **Documento de venta ingresable a mano**: antes eran `<input type="hidden">` y si la búsqueda por folio fallaba, el dato se perdía.
- Se quitó el `<script>` de Select2 duplicado (U7) y los dos `</div>` sobrantes del listado (U8).
- El listado marca **“sin factura”** y la cantidad, para que el analista priorice de un vistazo.

**Pendiente (Fase 2 completa):** wizard de 3 pasos, consolidar los 7 tipos en 4, mostrar la sucursal activa, `capture="environment"` + compresión de fotos en canvas, autoguardado en `localStorage` y notificación al analista al crear (B5).

---

## 7. Estado por fases

| Fase | Contenido | Estado |
|---|---|---|
| **0 — Higiene** | export scoping, PARCIAL, fecha de respuesta, logging, orden de fotos, select2 duplicado, divs sobrantes | ✅ aplicado |
| **1 — Datos que faltan** | `origen`, `cantidad`, `dte_compra`, `numero_factura_compra`, `fecha_factura_compra`, cliente opcional | ✅ código + **migración 0210 creada, NO aplicada** |
| **2 — Modal** | campos nuevos + proveedor sugerido + documento manual | ✅ parcial (falta wizard, fotos y autoguardado) |
| **3 — Formato propio PDF** | generador + adjunto automático + endpoint de revisión | ✅ aplicado |
| **4 — Formatos existentes** | correo, copia de control, export, tope de adjuntos | ✅ aplicado |
| **5 — Multi-SKU / consolidado** | `RequerimientoLinea` o agrupación por proveedor | ⏸ requiere tu decisión |

Tests: [app/tests/test_requerimientos_formato.py](../retailmind/app/tests/test_requerimientos_formato.py) — 25 tests nuevos. Junto con `test_requerimientos_envio`: **35 PASS**.

---

## 9. Evidencia en DigitalOcean Spaces (APLICADO)

El problema A5 (fotos en disco efímero) se resolvió apuntando **solo** la evidencia de requerimientos al Space que el usuario ya tiene para el ecommerce: `media-ecommerce` (sfo3), en una carpeta propia `retailmind/`.

### 9.1 Alcance deliberadamente acotado

**No** se tocó `DEFAULT_FILE_STORAGE`. Solo `FotoRequerimiento.imagen` recibe `storage=storage_evidencias`. Cotizaciones, evidencias de compras y fotos de producto siguen exactamente donde están, con las mismas URLs. Ampliar después es cambiar una línea por campo.

### 9.2 Cómo se activa

Se enciende **solo** si están definidas `SPACES_ACCESS_KEY` y `SPACES_SECRET_KEY`. Sin ellas —local, tests, CI— todo sigue en `MEDIA_ROOT` y nada cambia. Si las credenciales están pero falta la librería, se registra un ERROR en el log y la foto se guarda igual en disco: es preferible a rechazar la carga y perderla.

| Variable | Default | Para qué |
|---|---|---|
| `SPACES_ACCESS_KEY` / `SPACES_SECRET_KEY` | vacío | Credenciales. **Sin ellas la integración queda apagada** |
| `SPACES_BUCKET` | `media-ecommerce` | Bucket |
| `SPACES_REGION` | `sfo3` | Región |
| `SPACES_ENDPOINT` | `https://sfo3.digitaloceanspaces.com` | Endpoint |
| `SPACES_PREFIJO` | `retailmind` | Carpeta dentro del bucket |
| `SPACES_ACL` | `private` | `private` entrega URLs firmadas; `public-read` da URL fija |
| `SPACES_URL_EXPIRA_SEGUNDOS` | `3600` | Vigencia de la URL firmada |

`private` es el default a propósito: la evidencia puede incluir la foto de una boleta con datos del cliente. `foto.imagen.url` devuelve la URL firmada sola, así que el detalle y el listado funcionan sin cambios.

### 9.3 La trampa que hay que recordar

Con dos storages conviviendo, **leer con `default_storage` o con `foto.imagen.path` está mal**: el primero mira el disco local aunque el archivo esté en Spaces, y el segundo lanza `NotImplementedError`. Todo el código de lectura pasó a `foto.imagen.storage.open(...)` / `.exists(...)` — el envío al proveedor y el generador de PDF ya estaban escritos así.

### 9.4 Fotos que ya existen

`python manage.py subir_fotos_requerimientos_spaces` (dry-run; `--aplicar` para subir). Solo copia, nunca borra el original ni toca la BD, y es re-ejecutable. Informa cuántas fotos **ya no están en disco**: ésas se perdieron en deploys anteriores y no hay de dónde recuperarlas.

---

## 8. Lo que sigue pendiente de tu parte

1. **Instalar dependencias y migrar**: `pip install -r requirements.txt` (trae django-storages + boto3) y `python manage.py migrate app 0211` (aplica 0210 y 0211; solo columnas nullables/con default y un cambio de `storage` que no toca la BD).
2. **Definir las env vars de Spaces** en el `.env` de producción: `SPACES_ACCESS_KEY` y `SPACES_SECRET_KEY` (las mismas del ecommerce). El resto tiene defaults. Luego `python manage.py subir_fotos_requerimientos_spaces` para las fotos que aún existan en disco.
3. **Multi-SKU**: ¿un requerimiento debe poder reclamar varios SKU/cantidades en un solo documento? Es la única parte del plan que quedó fuera.
4. **Correo del proveedor**: los `mailto:` de respuesta apuntan al correo del usuario que envía. Si prefieres una casilla común (`garantias@…`), se cambia en una línea.
