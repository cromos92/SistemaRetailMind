# Correo a proveedores en Requerimientos: diagnóstico y plan

Fecha: 27-ago-2026 · Módulo: `/app/requerimientos/` · Base: análisis de código + DNS + logs

---

## 1. Resumen ejecutivo

**El código sí envía.** El envío está razonablemente bien construido (una sola conexión SMTP,
PDF + fotos, CC al administrador del proveedor, Reply-To doble, copia de control, historial).
Lo que falla está **fuera del código**: el proveedor de correo (MailerSend) y **la ausencia total
de trazabilidad posterior al `send()`**.

Tres problemas distintos que hoy se confunden en uno:

| Síntoma | Causa real | Dónde se arregla |
|---|---|---|
| "No le llega al proveedor" | **Cuenta de MailerSend suspendida por pago pendiente** (confirmado 27-ago) + falta de verificación del resultado del envío | Pago + Fase 0/1 |
| "No sé si lo abrió" | El sistema no registra **nada** después de `send()`: ni entregado, ni rebotado, ni abierto | Fases 1-2 y 4 |
| "Si responde, ¿a dónde llega?" | `From = noreply@webappsolutions.cl` (buzón que no existe) y `Reply-To` = correo **personal** del usuario que envió | Fase 3 |

**Recomendación en una línea:** seguir enviando por MailerSend (subiendo de plan), poner como
remitente un **buzón real de Google Workspace** (`requerimientos@webappsolutions.cl` — el dominio
ya tiene MX en Google), capturar aperturas/rebotes por webhook, capturar respuestas por IMAP con
token en el `Reply-To`, y agregar un **portal con link firmado** para el proveedor: eso último es
lo único que da seguimiento "sí o sí".

---

## 2. Diagnóstico técnico

### 2.1 Cómo está montado hoy el correo

```
retailmind/.env
  EMAIL_HOST         = smtp.mailersend.net
  EMAIL_HOST_USER    = MS_rlRa1n@webappsolutions.cl
  DEFAULT_FROM_EMAIL = noreply@webappsolutions.cl
```

DNS de `webappsolutions.cl` (verificado hoy):

- `TXT` → `v=spf1 include:amazonses.com include:_spf.mailersend.net ~all` → **dominio autorizado en MailerSend** (bien).
- `MX` → `smtp.google.com` → **ya tienen Google Workspace** en el dominio. Dato clave: el buzón genérico no hay que comprarlo, hay que crearlo.
- `retail.webappsolutions.cl` → `retail-ap-mh3y2.ondigitalocean.app` → producción en DigitalOcean App Platform (las variables de entorno de prod se editan ahí, **no** en el `.env` del repo).

### 2.2 Evidencia de que MailerSend está rechazando

En `retailmind/logs/errors.log` (22-ago):

```
smtplib.SMTPRecipientsRefused: {'jose.morales@albemarle.com': (421, b'Service not available, closing transmission channel.')}
smtplib.SMTPServerDisconnected: please run connect() first     (x5, en cadena)
```

Dos causas, ambas de MailerSend:

1. **CONFIRMADA — cuenta suspendida por pago pendiente.** El panel mostraba el plan impago; al
   regularizarlo el relay volvió a aceptar mensajes de inmediato (ver 2.2.1). *No era la cuota del
   plan Free*: la cuenta mueve ~13.000 correos, muy por encima de ese tramo.
2. **Límite de 5 correos por conexión SMTP.** MailerSend cierra la conexión al sexto mensaje y
   devuelve 421. Lo golpea el envío masivo de gift cards (que reusa una conexión en bucle); por eso
   los `please run connect() first` encadenados: la conexión ya estaba muerta y los envíos
   siguientes fallaron en cascada. *Requerimientos* manda solo 2 (proveedor + copia) por conexión,
   así que no lo golpea, pero **el bug de gift cards sigue vivo** y hay que arreglarlo aparte
   (reabrir la conexión cada 5 mensajes).

> **Lección de fondo:** una cuenta de correo impaga dejó mudo a **todo el ERP** — OTP de login,
> recuperación de contraseña, gift cards, cotizaciones y requerimientos — y nadie se enteró hasta
> que un usuario reclamó. Eso es exactamente lo que arregla la Fase 1: si los fallos de envío
> quedaran registrados y visibles, el problema se detectaba el primer día.

### 2.2.1 Verificación del 27-ago (post-pago)

Prueba de humo directa contra el relay (sin Django, sin tocar la BD):

```
250 Accepted                                     ← MAIL FROM: noreply@webappsolutions.cl
250 Accepted                                     ← RCPT TO
250 Message queued as 6a9068083f3f7659bff76c97   ← id del mensaje en MailerSend
```

**El relay funciona.** Y ese `Message queued as <id>` es el dato que permite correlacionar los
webhooks con el requerimiento: es el `data.email.message.id` que llega en los eventos. El problema
es que **el backend SMTP de Django descarta esa respuesta** — `send_messages()` solo devuelve un
contador. Para capturarlo hay que subclasear `django.core.mail.backends.smtp.EmailBackend` y leer
la respuesta del `DATA` final. Está incorporado a la Fase 1.

### 2.3 Defectos del código de envío (`views_modulo_requerimientos.py::enviar_a_proveedor`)

| # | Defecto | Consecuencia | Línea aprox. |
|---|---|---|---|
| D1 | No se revisa el retorno de `email.send()` | Si el servidor acepta 0 mensajes sin lanzar excepción, el requerimiento igual queda `correo_enviado_proveedor=True` y la UI dice "Enviado". **Falso positivo.** (El módulo de gift cards sí lo valida y levanta error) | ~1500 |
| D2 | No se guarda el `Message-ID` | Imposible correlacionar un evento del proveedor (entregado/rebotado) con el requerimiento. Gift cards sí lo guarda | ~1500 |
| D3 | Los fallos no dejan rastro en la ficha | Si el envío falla, solo queda en `logs/errors.log`. El usuario ve el SweetAlert una vez y desaparece; mañana nadie sabe que ese caso nunca salió | ~1499 |
| D4 | `From = noreply@…` (buzón inexistente) | El proveedor que aprieta "Responder al remitente" (o cuyo cliente ignora `Reply-To`) escribe a un agujero negro. Además `noreply@` baja reputación y sube la probabilidad de spam | `settings.py:328` |
| D5 | `Reply-To` = correo **personal** del usuario + copia | La respuesta queda en la casilla personal de quien envió. Si esa persona está de vacaciones o se va, el caso se muere. No hay memoria institucional | ~1464 |
| D6 | Cero campos de seguimiento en el modelo | `Requerimiento` tiene `correo_enviado_proveedor`, `fecha_envio_proveedor`, `intentos_envio`… y nada más. No hay `entregado`, `abierto`, `rebotado`, `respondido` (que sí existen en `GiftCard`) | `models/requerimientos.py:450-490` |
| D7 | La respuesta del proveedor se tipea a mano | `registrar_respuesta_proveedor` exige que un humano copie y pegue lo que dijo el proveedor. Si nadie lo hace, el caso queda "esperando respuesta" para siempre aunque el proveedor haya contestado | ~1610 |

### 2.4 Lo que YA existe y hay que reutilizar (no reinventar)

El módulo de **gift cards** ya resolvió exactamente este problema y funciona:

- `app/views_modulo_giftcards.py::webhook_correo_giftcard` — webhook de MailerSend con firma
  HMAC-SHA256, manejo del secret de prueba público, tolerancia a payload v1/v2, y prioridad de
  estados para que un evento fuera de orden no retroceda el estado.
- `app/models/giftcards.py` — campos `correo_estado`, `correo_message_id`, `correo_enviado_a`,
  `correo_estado_en`, `correo_estado_detalle` + `ESTADO_CORREO_GIFTCARD_CHOICES` con `ABIERTO`.
- `_EVENTOS_CORREO` — mapa `activity.delivered/opened/hard_bounced/spam_complaint` → estado.

**Ese código es el molde.** El plan de abajo lo generaliza en vez de duplicarlo.

---

## 3. Respuestas directas a las preguntas

### 3.1 "¿Cómo sé si el proveedor abrió el correo?"

Hay que ser honesto con la jerarquía de evidencia, porque "abierto" **no es prueba**:

| Nivel | Qué significa | ¿Confiable? | Cómo se obtiene |
|---|---|---|---|
| 1. Aceptado | El relay recibió el mensaje | Sí, pero no dice nada del destinatario | Retorno de `send()` |
| 2. **Entregado** | El servidor del proveedor lo aceptó en su buzón | **Sí, verificable** | Webhook `activity.delivered` |
| 3. Rebotado / Spam | No llegó, o llegó y lo marcaron spam | **Sí, y es lo más accionable** | Webhook `activity.hard_bounced`, `spam_complaint` |
| 4. Abierto (píxel) | Se cargaron las imágenes del correo | **Indicativo, no prueba** | Píxel 1×1 propio o `activity.opened` |
| 5. **Click en el link** | Alguien hizo clic desde el correo | Fuerte | URL con token propio |
| 6. **Respondió en el portal** | Firmó una decisión con fecha, IP y navegador | **Prueba dura** | Portal del proveedor (Fase 4) |

Por qué el nivel 4 no es prueba, y hay que decirlo en la UI:

- **Gmail** cachea las imágenes en su proxy: se registra apertura cuando se muestran, pero el proxy
  puede pre-cargar → falsos positivos.
- **Apple Mail Privacy Protection** pre-carga *todas* las imágenes aunque el usuario nunca abra →
  falsos positivos masivos (buena parte de los proveedores usa iPhone).
- **Outlook corporativo** bloquea imágenes por defecto → el proveedor lee el correo y el sistema
  marca "no abierto" → falso negativo.

**Conclusión:** medir apertura sirve como semáforo blando ("hace 6 días que no da señales"), pero
para exigirle al proveedor sirven el **nivel 2/3** (entregado o rebotado) y el **nivel 5/6** (click
y respuesta en el portal). El plan implementa los tres.

### 3.2 "Si me responde, ¿cómo lo recibo? ¿De un correo genérico?"

Sí, y es la parte que hoy no existe. El diseño:

```
From:      Requerimientos <requerimientos@webappsolutions.cl>   ← buzón REAL de Google Workspace
Reply-To:  requerimientos+r7f3a91c@webappsolutions.cl           ← token único por requerimiento
```

- **Plus-addressing** (`usuario+loquesea@`) funciona nativamente en Google Workspace, es
  *receive-only* y no requiere crear alias ni configurar nada. Todo lo que llegue a
  `requerimientos+CUALQUIERCOSA@` cae en el buzón `requerimientos@`.
- Un comando (`capturar_respuestas_correo`) lee ese buzón por IMAP cada N minutos, extrae el token
  del `To:`/`Delivered-To:`, encuentra el requerimiento y **registra la respuesta automáticamente**:
  texto, adjuntos, fecha real, y pasa el estado a "Respondido" para que deje de aparecer atrasado.
- Como red de seguridad, el token también va en el asunto (`[REQ-2026-000123]`) por si el proveedor
  responde desde otra dirección o alguien reenvía el hilo.

Ventaja frente al CC actual: la respuesta queda **en la ficha del requerimiento**, no en la casilla
personal de quien lo mandó. Y sigue siendo visible en un buzón compartido para quien quiera leerlo
como correo normal.

### 3.3 "¿Tengo que habilitar un Gmail para enviar desde ahí?"

**Buzón sí, envío por Gmail no.** Son dos cosas separadas:

| | Enviar por Gmail SMTP | Enviar por MailerSend (recomendado) |
|---|---|---|
| Límite | 2.000/día (Workspace), sin cuota mensual | Según plan (5.000/mes en Hobby, 50.000 en Starter) |
| Tracking de entrega | **No existe** | Webhook `delivered` / `bounced` / `spam` |
| Tracking de apertura | No | Sí (más el píxel propio) |
| Rebotes | Llegan como correo a la casilla, hay que leerlos a mano | Evento estructurado al webhook |
| Reputación | Se juega la reputación del dominio corporativo en cada envío del ERP | Aislada en el ESP |
| Adjuntos | 25 MB | 25 MB (el código ya topea en 12 MB) |
| Riesgo | Si el ERP manda algo mal, Google puede suspender el envío de **toda la empresa** | El incidente queda acotado |

Lo que sí conviene de Google Workspace: **el buzón `requerimientos@webappsolutions.cl`** como
`From` y como destino de las respuestas. Que el `From` sea un buzón que existe de verdad arregla
D4 (nadie escribe al vacío) y mejora la entregabilidad frente a `noreply@`.

Nota de plan (actualizada 27-ago): la cuenta mueve **~13.000 correos**, así que Free (500) y Hobby
(5.000) quedan descartados — el tramo real es **Starter o superior**. Eso tiene dos consecuencias
buenas para este plan:

- **Inbound Routing sí está disponible** (arranca en Starter) → la Fase 3 puede hacerse con
  webhook de entrada en vez de polling IMAP, sin guardar contraseñas de buzón.
- **El límite de 1 webhook desaparece** (Starter trae 50) → ya no es *obligatorio* unificar el
  endpoint. Igual conviene hacerlo por diseño, pero deja de ser una restricción.

---

## 4. Arquitectura propuesta

```
                 ┌──────────────────────────────────────────────┐
                 │  Requerimiento (VALIDADO)                    │
                 └───────────────┬──────────────────────────────┘
                                 │ enviar_a_proveedor()
                                 ▼
              ┌───────────────── EnvioCorreo ─────────────────┐
              │ token · destinatario · message_id · estado    │  ← bitácora única de TODO el ERP
              └───────────────┬───────────────────────────────┘
                              │  SMTP MailerSend
    From: requerimientos@webappsolutions.cl
    Reply-To: requerimientos+<token>@webappsolutions.cl
    X-MailerSend-Tags: requerimiento
    body: <img src=".../c/a/<token>.png">  +  botón → .../c/r/<token>/
                              ▼
                        📧 PROVEEDOR
                              │
        ┌─────────────────────┼────────────────────────┬──────────────────────┐
        ▼                     ▼                        ▼                      ▼
  webhook MailerSend    píxel propio            portal con token         responde el correo
  delivered/bounced/    → ABIERTO               → CLICK + decisión       → IMAP poller
  spam/opened           (indicativo)            firmada (prueba)         → respuesta en la ficha
        └─────────────────────┴────────────────────────┴──────────────────────┘
                              ▼
                    Estado de seguimiento en la ficha + en la lista
```

Dos decisiones de diseño que importan:

1. **Bitácora propia (`EnvioCorreo`), no campos sueltos en `Requerimiento`.** El mismo problema lo
   tienen gift cards, cotizaciones, OTP y recuperación de contraseña. Una tabla única permite un
   solo webhook (obligatorio con el límite de 1 de MailerSend) y un panel "¿qué correos salieron
   hoy y cuáles rebotaron?".
2. **El píxel y el portal son propios, no del proveedor.** Si mañana cambian MailerSend por
   Brevo/SES/Postmark, la apertura y la respuesta del portal siguen funcionando. Del ESP solo
   dependemos para lo que no podemos saber solos: entregado y rebotado.

---

## 5. Plan por fases

### Fase 0 — Destrabar el envío · ✅ HECHO (27-ago)

1. ~~Regularizar el pago de MailerSend~~ → **hecho**. Relay verificado con envío real:
   `250 Message queued as 6a9068083f3f7659bff76c97`, recibido en bandeja de entrada (no spam).
2. **Pendiente:** verificar en **DigitalOcean → App → Settings → Environment Variables** que
   producción tenga las mismas `EMAIL_*` que el `.env` local (pueden estar desincronizadas).
3. **Pendiente:** confirmar DKIM verificado en MailerSend, no solo SPF (el SPF ya está).
4. **Pendiente:** activar en MailerSend → *Domains → Manage → Tracking* el **open tracking** y el
   **click tracking** (los necesita la Fase 2).
5. **Pendiente y no menor:** poner una **alerta de facturación** (tarjeta al día + aviso de pago
   fallido a un correo que alguien lea). Este incidente dejó mudo al ERP completo sin que nadie se
   enterara.

### Fase 0.b — Bug de las gift cards (independiente) · ~1 h

`views_modulo_giftcards.py` reusa una conexión SMTP en bucle y MailerSend la corta al 6º mensaje
(`421` → `please run connect() first` en cascada). Hay que **cerrar y reabrir la conexión cada 5
envíos**. No afecta a requerimientos, pero es la misma cuenta y hoy pierde correos de gift cards
en silencio.

### Fase 1 — Bitácora de correo y honestidad del envío · ~4-6 h

Archivos: `app/models/comunicaciones.py` (nuevo) + `models/__init__.py` + migración +
`app/services/correo_service.py` (nuevo) + `views_modulo_requerimientos.py`.

- Modelo **`EnvioCorreo`**: `token` (uuid, único, indexado), `modulo` + `objeto_id`,
  `destinatario`, `cc`, `reply_to`, `asunto`, `from_email`, `message_id`, `proveedor_message_id`,
  `enviado_en`, `enviado_por`, `estado`, `estado_en`, `estado_detalle`, `aperturas`, `abierto_en`,
  `clicks`, `click_en`, `ultima_ip`, `ultimo_user_agent`, `error`.
  Estados: `ENVIADO → ENTREGADO → ABIERTO → CLICK → RESPONDIDO` + `REBOTADO / SPAM / FALLIDO`,
  con la misma tabla de prioridad que gift cards (un evento fuera de orden no retrocede).
- Modelo **`RespuestaCorreo`**: `envio` (FK), `remitente`, `asunto`, `cuerpo`, `recibido_en`,
  `message_id`, `in_reply_to`, `adjuntos_json`.
- Servicio **`enviar_correo_trazado(...)`** que envuelve `EmailMultiAlternatives`, crea el
  `EnvioCorreo`, inyecta el píxel + `Reply-To` con token + `X-MailerSend-Tags`, **valida que
  `send()` devuelva ≥ 1** (arregla **D1**) y guarda el `Message-ID` (**D2**).
- **Backend SMTP propio** (`app/mail_backends.py`) que subclasea
  `django.core.mail.backends.smtp.EmailBackend` y captura la respuesta del `DATA` final
  (`250 Message queued as <id>`) para guardarla en `EnvioCorreo.proveedor_message_id`. Django la
  descarta; verificado en la prueba del 27-ago. **Es la llave de correlación con los webhooks**
  (`data.email.message.id`) y evita depender del matching por destinatario+fecha que usa hoy
  gift cards.
- **Registrar también los fallos** (**D3**): si el envío falla, `EnvioCorreo.estado='FALLIDO'` +
  `HistorialRequerimiento` con acción `ENVIO_FALLIDO`, para que quede en la ficha y en la lista.
- `enviar_a_proveedor` pasa a usar el servicio. No cambia el flujo ni la UI todavía.

### Fase 2 — Webhook unificado de estados de entrega · ~3-4 h

Archivos: `app/views_modulo_correo.py` (nuevo) + `app/urls.py` + `views_modulo_giftcards.py` (redirigir).

- Endpoint único `POST /app/api/correo/webhook/` con la **misma firma HMAC** ya probada en gift
  cards (incluido el manejo del secret de prueba público y el alta huevo-y-gallina). Env:
  `CORREO_WEBHOOK_SECRET`.
- Correlación: `proveedor_message_id` (el `250 Message queued as <id>` que ahora capturamos) →
  `Message-ID` → destinatario + envío más reciente (la cascada que ya usa gift cards).
- Despacho por `EnvioCorreo.modulo`: `REQUERIMIENTO` actualiza el requerimiento, `GIFTCARD`
  mantiene el comportamiento actual. Un endpoint único es mejor diseño (y hoy el plan ya no lo
  exige).
- Endpoint del píxel: `GET /app/c/a/<token>.png` — público, sin login, devuelve un GIF/PNG 1×1,
  suma apertura, guarda IP y user-agent. Cabeceras `Cache-Control: no-store` para que el proxy de
  Gmail no lo sirva una sola vez.
- En MailerSend: **Domains → Manage → Tracking** activar *open tracking* y *click tracking*, y
  crear el webhook con los eventos `delivered`, `opened`, `clicked`, `hard_bounced`,
  `soft_bounced`, `spam_complaint`.

### Fase 3 — Buzón genérico y captura automática de respuestas · ~6-8 h

Google Workspace (sin código):

1. Crear el buzón **`requerimientos@webappsolutions.cl`** (o un grupo con buzón colaborativo si
   quieren que varios lo vean).
2. Generar una **contraseña de aplicación** para IMAP (requiere 2FA en esa cuenta).
3. En MailerSend, agregar `requerimientos@webappsolutions.cl` como remitente del dominio.

Código:

- `settings.py`: `REQUERIMIENTOS_FROM_EMAIL`, `CORREO_IMAP_HOST/USER/PASSWORD` — todo por
  `os.environ.get(...)`, sin tocar valores de producción a mano.
- `From` pasa a ser el buzón real (**D4**); `Reply-To` pasa a ser
  `requerimientos+<token>@webappsolutions.cl` (**D5**). El correo del usuario que envía se mantiene
  como CC opcional, no como único destino de la respuesta.
- Comando **`capturar_respuestas_correo`** (`management/commands/`): IMAP → busca no leídos →
  extrae el token del `To`/`Delivered-To`/asunto → crea `RespuestaCorreo` → marca el `EnvioCorreo`
  como `RESPONDIDO` → **rellena `respuesta_proveedor` y `fecha_respuesta_proveedor` del
  requerimiento** (**D7**) → deja el caso en "respuesta recibida, falta clasificar" para que un
  humano solo tenga que elegir APROBADO / RECHAZADO / PARCIAL.
- Cron cada 10 min (DigitalOcean scheduled job o `celery beat` — Celery ya está en
  `retailmind/requirements.txt`).

> **Variante recomendada si el plan es Starter o superior (probable, dado el volumen de ~13K):**
> MailerSend *Inbound Routing* sobre un subdominio (`resp.webappsolutions.cl` con MX apuntando a
> MailerSend) hace POST del correo ya parseado a un endpoint Django. Ventajas sobre IMAP: sin
> polling (la respuesta entra en segundos), sin contraseña de buzón guardada en variables de
> entorno, y los adjuntos llegan ya separados. Costo: un registro MX nuevo en el DNS —
> **en un subdominio, así que no toca el MX de Google Workspace del dominio raíz**.
> El IMAP queda como plan B si no quieren tocar DNS.

### Fase 4 — Portal del proveedor: el seguimiento "sí o sí" · ~8-10 h

Esto es lo que responde de verdad a *"cómo hago seguimiento sí o sí"*.

- URL pública firmada: `GET /app/c/r/<token>/` — **sin login**, el token es la credencial
  (uuid4 = 122 bits, imposible de adivinar), con expiración configurable y revocable.
- Muestra el requerimiento en modo lectura: producto, motivo, factura de compra, fotos, PDF
  descargable — respetando `nexo-design-system.css`, sin `{% extends %}`, patrón de includes.
- Tres botones: **Aceptar la garantía** / **Rechazar** / **Pedir más información**, con campo de
  comentario obligatorio y opción de adjuntar un documento.
- Cada visita registra `click`, IP, user-agent y timestamp. Cada respuesta escribe
  `decision_proveedor`, `respuesta_proveedor` y `fecha_respuesta_proveedor` **estructurados**, más
  una fila en `HistorialRequerimiento` con acción `RESPUESTA_PORTAL`.
- Resultado: aunque el proveedor no conteste el correo, queda registro fechado de que **abrió el
  caso** (clic real, no píxel) y de qué decidió. Eso es lo que sirve para reclamar.

### Fase 5 — Visibilidad y recordatorios · ~4-5 h

- En `detalle_requerimiento.html`: línea de tiempo del correo — *Enviado 12:04 → Entregado 12:04 →
  Abierto 15:22 → Sin respuesta hace 6 días*, con el aviso explícito de que "abierto" es indicativo
  (no mentirle al usuario sobre lo que el dato significa).
- En `gestion_requerimientos.html`: columna/badge de estado de entrega y filtro **"rebotados /
  nunca entregados"** — hoy esos casos son invisibles y son los urgentes.
- Comando **`enviar_recordatorios_requerimientos`**: a los N días sin respuesta (usa el
  `PLAZO_RESPUESTA_DIAS=7` que ya existe), reenvía con `RECORDATORIO ·` en el asunto y escala por
  correo al administrador. Hoy el recordatorio es 100% manual.
- Regla: si el correo **rebotó**, no mandar recordatorios — avisar que hay que corregir la
  dirección en la ficha del proveedor.

---

## 6. Esfuerzo y orden sugerido

| Fase | Qué destraba | Esfuerzo | Estado |
|---|---|---|---|
| 0 · Pago MailerSend | Que el correo salga | — | ✅ **hecho 27-ago** |
| 0.b · Reset proactivo de conexión en gift cards | Evita el 421 previsible cada 6 destinatarios | 1 h | ✅ **hecho 27-ago** |
| 1 · Bitácora + honestidad del envío | Saber qué salió y qué falló | 4-6 h | ✅ **hecho 27-ago** |
| 2 · Webhook unificado + píxel | Entregado / rebotado / abierto | 3-4 h | ✅ **hecho 27-ago** |
| 3 · Captura de respuestas por IMAP | Respuestas que no se pierden | 6-8 h | ✅ **hecho 27-ago** |
| 4 · Portal del proveedor | Prueba dura de recepción y decisión | 8-10 h | ⏳ falta tu OK |
| 5 · UI de seguimiento + recordatorios | Que el equipo lo use sin explicación | 4-5 h | ✅ **hecho 27-ago** |

### Lo implementado el 27-ago

| Archivo | Qué hace |
|---|---|
| `app/models/comunicaciones.py` | `EnvioCorreo` + `RespuestaCorreo`, con la tabla de prioridad de estados |
| `app/migrations/0219_bitacora_correo.py` | Crea las dos tablas. **No** altera datos existentes |
| `app/mail_backends.py` | `TrazableEmailBackend`: captura el `250 Message queued as <id>` que Django descarta |
| `app/services/correo_service.py` | `enviar_correo_trazado()`: bitácora, validación del resultado, píxel y Reply-To con token |
| `app/views_modulo_correo.py` | Píxel de apertura + webhook unificado con firma HMAC |
| `app/views_modulo_requerimientos.py` | `enviar_a_proveedor` usa el servicio; los fallos quedan en el historial |
| `app/tests/test_correo_seguimiento.py` | 30 tests nuevos (**89 verdes** en la suite de correo/requerimientos) |
| `retailmind/settings.py` | `CORREO_BASE_URL`, `CORREO_BUZON_RESPUESTAS`, `REQUERIMIENTOS_FROM_EMAIL`, `EMAIL_BACKEND` |
| `detalle_requerimiento.html` + `_estilos_requerimientos.html` | Línea de tiempo del correo en la ficha, con la salvedad de que "abierto" no es prueba |
| `gestion_requerimientos.html` | Badge de entrega en la columna Proveedor (el rebote manda sobre la fecha) + filtro "Correo" |
| `management/commands/enviar_recordatorios_requerimientos.py` | Reenvío automático a los atrasados. **Simula por defecto**, `--enviar` para mandar |
| `app/services/captura_respuestas.py` | Parseo de la respuesta: token, hilo citado, adjuntos, fecha real |
| `management/commands/capturar_respuestas_correo.py` | Poller IMAP del buzón → pega la respuesta en la ficha |

### Decisión de buzón (confirmada 27-ago)

Se usa la casilla que **ya existía**: `calzadospaolafallados@gmail.com`, que históricamente recibía
todos los fallados. Sirve para las dos cadenas, así que no hace falta ruteo por empresa. Ventaja
sobre estrenar una dirección: los proveedores ya la tienen en su libreta.

Gmail común soporta plus-addressing igual que Workspace, así que el `Reply-To` es
`calzadospaolafallados+<token>@gmail.com` sin configurar ningún alias.

Lo que esa casilla **no** puede ser es el remitente: MailerSend exige dominio verificado y
`gmail.com` no se puede verificar. El `From` sigue en `webappsolutions.cl`.

Verificado contra el relay real: el id se captura (`6a906add4a930fc33c6c7358`).

**El Reply-To con token y el píxel están escritos pero dormidos**: se activan solos
al definir `CORREO_BASE_URL` y `CORREO_BUZON_RESPUESTAS`. Mientras no existan, el
correo sale exactamente como antes.

Fases 0 y 1 sirven aunque después se decida otra cosa: la bitácora es independiente del proveedor.

---

## 7. Decisiones pendientes

1. **Nombre exacto del plan de MailerSend** (Hobby / Starter / Professional). Con ~13K correos no
   puede ser Hobby, pero conviene confirmarlo: *Inbound Routing* arranca en **Starter**, y de eso
   depende si la Fase 3 va por inbound webhook (mejor) o por polling IMAP.
2. **Buzón**: ¿se crea `requerimientos@webappsolutions.cl` en Google Workspace, o se reusa uno
   existente? ¿Buzón individual o grupo colaborativo con varios lectores?
3. **Alcance de la bitácora**: ¿se aplica de entrada a todo el ERP (gift cards, OTP, cotizaciones)
   o arranca solo con requerimientos y se migra después?
4. **Portal del proveedor (Fase 4)**: ¿se hace? Es la única forma de tener prueba real, pero expone
   una URL pública (con token) al mundo.

---

## 8. Comandos para ejecutar (copy-paste)

**Ninguno de estos toca datos.** Migraciones y envíos reales quedan para cuando el plan esté aprobado.

```powershell
# 1) Ver qué variables de correo tiene realmente el entorno actual
cd C:\Users\cromo\Documents\DjangoProyects\SistemaRetailMind\retailmind
python -c "import os;[print(k,'=',(v[:10]+'...') if 'PASS' in k else v) for k,v in os.environ.items() if k.startswith('EMAIL') or 'CORREO' in k]"

# 2) Chequeo de configuración de Django (no toca la BD)
python manage.py check

# 3) Ver los errores de correo registrados
Select-String -Path logs\errors.log -Pattern "smtplib|Error al enviar" | Select-Object -Last 30

# 4) DNS del dominio (confirmar SPF / DKIM / MX)
nslookup -type=TXT webappsolutions.cl
nslookup -type=TXT mlsend2._domainkey.webappsolutions.cl
nslookup -type=MX  webappsolutions.cl
```

En el panel de MailerSend (manual):
- **Activity** → últimos envíos, con estado real por destinatario.
- **Domains → webappsolutions.cl → Manage → Tracking** → activar *Open* y *Click tracking*.
- **Settings → Plan** → cuota consumida del mes.

En DigitalOcean (manual):
- **App → Settings → Environment Variables** → confirmar `EMAIL_HOST`, `EMAIL_HOST_USER`,
  `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`, `REQUERIMIENTOS_CORREO_COPIA`.
