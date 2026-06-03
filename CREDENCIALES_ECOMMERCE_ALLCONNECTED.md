# Integraciones Ecommerce (AllConnected) — Credenciales y Conexión

> Documenta cómo RetailMind se conecta a los ecommerces externos vía **AllConnected**
> (`VicentAllEcommercesConected`), para **PAOLA** (`paola.cl`) y **REAL/Realsport**
> (`realsport.cl`). Incluye dónde viven las credenciales, los métodos HTTP, los
> headers y el troubleshooting de los errores **403** y **timeout**.
>
> ⚠️ **Este archivo NO debe contener las API keys reales.** Usa los placeholders
> y los comandos de la sección "Cómo ver los valores reales". No lo comitees con
> secretos dentro.

## Glosario

| Nombre que usas | Qué es realmente | Host |
|---|---|---|
| **PAOLA** | ecommerce `paola.cl` | (su propio dominio) |
| **REAL** | ecommerce `realsport.cl` (Realsport) | (su propio dominio) |
| **AllConnected** | middleware `VicentAllEcommercesConected` que centraliza los pedidos de todos los canales | `ecommerce.webappsolutions.cl` |
| **RetailMind** | este sistema (ERP/POS) | `retail.webappsolutions.cl` |

RetailMind **no** se conecta directo a paola.cl ni a realsport.cl para los **pedidos**:
habla con **AllConnected**, que es quien consolida los pedidos de cada canal. Cada
pedido trae su `canal_origen` (ej. el de PAOLA viene marcado con su canal). Para las
**fotos de portada** sí hay una conexión por-ecommerce (ver sección 2).

---

## Hay DOS integraciones distintas (no confundir)

| | **1. Pedidos** | **2. Fotos de portada** |
|---|---|---|
| Para qué | Traer/recibir pedidos de venta | Traer URLs de imágenes de productos |
| Con quién | AllConnected (`ecommerce.webappsolutions.cl`) | Cada ecommerce (realsport.cl, paola.cl) |
| Dónde viven las credenciales | **Variables de entorno** (env de producción) | **Base de datos**, tabla `CredencialesEcommerce` |
| Se configura desde | env/redeploy | UI: Configuración → Integraciones Ecommerce |
| Header de auth | `X-AllConnected-Key` (salida) / `X-RetailMind-Key` (entrada) | `X-AllConnected-Key` (configurable por fila) |

El error que estás viendo (`/app/ecommerce/pedidos/traer/`) es de la integración **1 (Pedidos)**.

---

## 1. Pedidos

### 1a. Push — AllConnected → RetailMind (recepción automática)

AllConnected hace `POST` de cada pedido a RetailMind.

- **Endpoints** (en [app/urls.py](retailmind/app/urls.py)):
  - `POST /api/ecommerce/pedidos/` → `api_recibir_pedido_ecommerce`
  - `POST /api/ecommerce/pedidos/consultar/` → `api_asignar_ticket_rm`
  - `POST /api/ecommerce/pedidos/cancelar/` → `api_cancelar_pedido_ecommerce`
- **Auth de entrada** ([views_ecommerce.py](retailmind/app/views_ecommerce.py#L222) `_verificar_api_key`):
  - Header `X-RetailMind-Key: <RETAILMIND_API_KEY>` **o** query param `?api_key=...`
  - Se compara contra la env var `RETAILMIND_API_KEY` ([settings.py:567](retailmind/retailmind/settings.py#L567)).
  - ⚠️ Si `RETAILMIND_API_KEY` está **vacía**, el endpoint **no bloquea** (modo compatibilidad).
- Estos endpoints son `@csrf_exempt` (los llama un sistema externo, no el navegador).

### 1b. Pull — RetailMind → AllConnected (botón "Traer pedidos")  ← lo que estás usando

RetailMind sale a **consultar** los pedidos pendientes. Este es el botón
"Traer pedidos" de `https://retail.webappsolutions.cl/app/ecommerce/pedidos/`.

- **Vista RetailMind** ([views_ecommerce.py:546](retailmind/app/views_ecommerce.py#L546) `traer_pedidos_allconnected`):
  - URL: `POST /ecommerce/pedidos/traer/` (`name='traer_pedidos_allconnected'`)
  - **Solo acepta POST.** Cualquier otro método → **HTTP 405** "Método no permitido".
  - `@login_required` + **NO** `@csrf_exempt` → el POST **requiere CSRF token válido**.
  - Permiso requerido: `ecommerce_pedidos_todos` / `puede_crear`
    ([_verificar_permiso_ecommerce](retailmind/app/views_ecommerce.py#L30)) → si falta, **HTTP 403** con
    `{"error": "No tiene permiso..."}`.
  - El botón del front ([pedidos_ecommerce_list.html](retailmind/app/templates/app/ecommerce/pedidos_ecommerce_list.html#L525) `traerPedidos()`)
    manda `fetch(..., { method:'POST', headers:{ 'X-CSRFToken': <cookie csrftoken>, 'Content-Type':'application/json' } })`
    con body opcional `{ "desde":"YYYY-MM-DD", "hasta":"YYYY-MM-DD" }`.

- **Salida hacia AllConnected** ([allconnected_pedidos_service.py](retailmind/app/services/allconnected_pedidos_service.py)):
  - **Método: `GET`**
  - URL: `<ALLCONNECTED_API_BASE_URL><ALLCONNECTED_PEDIDOS_PATH>`
    → en producción: `https://ecommerce.webappsolutions.cl/app/pedidos/pendientes/`
  - Query: `?estado=PENDIENTE[&rut_empresa=<rut>][&desde=...&hasta=...]`
  - Header: `X-AllConnected-Key: <ALLCONNECTED_API_KEY>`
  - `User-Agent: RetailMind-PedidosPull/1.0`, `Accept: application/json`
  - **Timeout de lectura: 30 s** (`TIMEOUT_SEGUNDOS = 30`).
  - Respuesta esperada: `200` con `[ {pedido}, ... ]` o `{"pedidos":[...]}`.
  - Cada pedido se ingesta con la misma lógica que el push (`_ingestar_pedido_dict`): stock, sub_estado, historial, idempotencia.
  - Si `ALLCONNECTED_API_BASE_URL` está **vacía** → `{ok:true, configurado:false}` (pull deshabilitado, solo refresca la tabla).

- **Credenciales (env vars de producción)** ([settings.py:634-637](retailmind/retailmind/settings.py#L634)):

  | Variable | Valor en prod | Default |
  |---|---|---|
  | `ALLCONNECTED_API_BASE_URL` | `https://ecommerce.webappsolutions.cl` *(deducido del error de timeout)* | `''` (vacío = pull off) |
  | `ALLCONNECTED_API_KEY` | `<SECRETO — ver comandos abajo>` | `''` |
  | `ALLCONNECTED_API_HEADER_NAME` | (probablemente default) | `X-AllConnected-Key` |
  | `ALLCONNECTED_PEDIDOS_PATH` | (probablemente default) | `/app/pedidos/pendientes/` |

  > Estas variables **no están en el `.env` local** (se confirmó: el `.env` no las
  > tiene). Viven en el entorno de **producción** (DigitalOcean/Railway). Por eso
  > la respuesta dice `"configurado": true`: en prod sí están seteadas.

- **Otras env vars AllConnected** (notificación de stock, [settings.py:621-625](retailmind/retailmind/settings.py#L621)):
  - `ALLCONNECTED_WEBHOOK_URL`, `ALLCONNECTED_CANAL_ORIGEN_ID`.

---

## 2. Fotos de portada (realsport.cl / paola.cl)

Esta es la integración **por-ecommerce** y sus credenciales viven en la **BD**.

- **Modelo**: `CredencialesEcommerce` ([app/models/configuracion.py](retailmind/app/models/configuracion.py))
  — una fila por ecommerce, por Empresa. Campos: `codigo`, `tipo`
  (`realsport` / `paola` / `otro`), `url_api`, `api_key`, `header_name`
  (default `X-AllConnected-Key`), `activo`, `prioridad`.
- **Se gestiona desde la UI**: Configuración → Integraciones Ecommerce
  (`/app/configuracion/integraciones-ecommerce/`,
  [views_modulo_configuracion.py](retailmind/app/views_modulo_configuracion.py#L30)).
  Botones: Guardar / Probar / Sincronizar / Eliminar.
- **Endpoints remotos** ([realsport_imagenes_service.py](retailmind/app/services/realsport_imagenes_service.py)):
  - `GET <url_api>/api/v1/health/` → probar conexión
  - `GET <url_api>/api/v1/products/images/?skus=...` (lookup) o `?page=&page_size=` (catálogo)
  - Header: `<header_name>: <api_key>` (default `X-AllConnected-Key`)
- **Sincronización**: comando `python manage.py sincronizar_fotos_ecommerce --codigo <realsport|paola>`.

---

## Dónde viven las credenciales (resumen)

| Credencial | Para qué | Dónde se setea | Dónde se lee |
|---|---|---|---|
| `ALLCONNECTED_API_BASE_URL` / `ALLCONNECTED_API_KEY` | **Pull de pedidos** | env de **producción** | `settings.py` → `allconnected_pedidos_service.py` |
| `RETAILMIND_API_KEY` | **Push de pedidos** (que AllConnected nos llame) | env de **producción** | `settings.py:567` → `_verificar_api_key` |
| `ALLCONNECTED_WEBHOOK_URL` | Notificar stock a AllConnected | env de **producción** | `settings.py:621` |
| Fila `realsport` en `CredencialesEcommerce` | **Fotos** realsport.cl | **BD** (UI Integraciones) | `realsport_imagenes_service.py` |
| Fila `paola` en `CredencialesEcommerce` | **Fotos** paola.cl | **BD** (UI Integraciones) | `realsport_imagenes_service.py` |

---

## Cómo ver los valores reales (comandos)

> ⚠️ El `.env` local apunta a la **BD de producción** y las env vars reales están
> en el entorno de prod, no en el repo. Corré estos comandos en el servidor donde
> esté configurado (o localmente si tenés las env vars cargadas). Son **solo lectura**.

**Env vars del pull/push de pedidos** (desde `retailmind/`):

```powershell
python manage.py shell -c "from django.conf import settings as s; print('BASE_URL =', s.ALLCONNECTED_API_BASE_URL); print('HEADER   =', s.ALLCONNECTED_API_HEADER_NAME); print('PATH     =', s.ALLCONNECTED_PEDIDOS_PATH); print('API_KEY  =', (s.ALLCONNECTED_API_KEY[:4] + '...') if s.ALLCONNECTED_API_KEY else '(vacia)'); print('RM_KEY   =', 'set' if settings.RETAILMIND_API_KEY else '(vacia)')"
```

**Credenciales de fotos (BD)**:

```powershell
python manage.py shell -c "from app.models import CredencialesEcommerce as C; [print(x.codigo, x.tipo, x.url_api, x.header_name, x.api_key[:4]+'...', 'activo='+str(x.activo)) for x in C.objects.all()]"
```

---

## Troubleshooting

### A) Error 403 en `/app/ecommerce/pedidos/traer/` ("ayer funcionaba")

El endpoint **solo acepta POST con CSRF token válido**. Un 403 casi siempre es:

1. **CSRF token vencido / cookie `csrftoken` perdida** (la causa más común del
   "ayer funcionaba"): la sesión expiró (hay middleware de timeout por
   inactividad), o tenés una pestaña vieja abierta. El botón lee el token de la
   cookie `csrftoken`; si está vacía/vieja, Django responde **403 CSRF**.
   - **Solución**: recargá la página con **Ctrl + F5** (o re-logueate) y volvé a
     tocar "Traer pedidos". Eso regenera la cookie `csrftoken`.
2. **Falta de permiso**: tu rol no tiene `ecommerce_pedidos_todos / puede_crear`.
   En ese caso el cuerpo del 403 dice `"No tiene permiso para esta acción..."`.
3. Si entraste a la URL **escribiéndola en el navegador** (eso es un GET) → no da
   403 sino **405** "Método no permitido". El botón debe llamarse desde la página.

> Como en tu último mensaje ya recibiste un JSON de respuesta (no un 403), **ya
> pasaste este problema** — el POST llegó bien. El error de ahora es otro (abajo).

### B) `"No se pudo conectar a AllConnected: ... Read timed out (read timeout=30)"`

Esto **ya no es problema de RetailMind**: el POST funcionó, RetailMind conectó por
HTTPS a `ecommerce.webappsolutions.cl` (TCP/TLS OK) pero **el servidor de
AllConnected no respondió en 30 segundos**.

- `"configurado": true` confirma que `ALLCONNECTED_API_BASE_URL` está seteada y el
  pull está activo apuntando a `https://ecommerce.webappsolutions.cl`.
- Causas probables de que "ayer funcionara y hoy no":
  1. **AllConnected lento/caído/reiniciando** o su BD saturada → tarda > 30 s.
  2. **El rango de fechas pedido es muy grande** (sin `desde`/`hasta`, usa el mes
     actual; mientras más pedidos, más demora la consulta remota).
- **Qué hacer**:
  1. **Acotá el rango**: poné `desde`/`hasta` de pocos días en los inputs de fecha
     y reintentá. Menos datos = respuesta más rápida = no llega al timeout.
  2. **Verificá si AllConnected responde** (medí el tiempo). Reemplazá `<KEY>` por
     el valor real de `ALLCONNECTED_API_KEY`:

     ```powershell
     Measure-Command {
       Invoke-WebRequest -Uri "https://ecommerce.webappsolutions.cl/app/pedidos/pendientes/?estado=PENDIENTE" `
         -Headers @{ "X-AllConnected-Key" = "<KEY>" } -TimeoutSec 60 | Out-Null
     }
     ```

     - Si tarda mucho o tira timeout → el problema está en AllConnected (avisá a
       quien administre `ecommerce.webappsolutions.cl`).
     - Si responde rápido → el problema fue puntual; reintentá el botón.
  3. Si AllConnected **funciona pero es legítimamente lento**, se puede subir el
     timeout `TIMEOUT_SEGUNDOS = 30` en
     [allconnected_pedidos_service.py:49](retailmind/app/services/allconnected_pedidos_service.py#L49)
     — pero es un parche; lo correcto es que el endpoint remoto responda rápido.

### Sobre "traer el pedido de PAOLA"

El botón "Traer pedidos" trae **todos** los pendientes desde AllConnected (no se
filtra por canal en la salida); el pedido de PAOLA llega con su `canal_origen` y
queda en la lista de `/app/ecommerce/pedidos/?estado=PENDIENTE`. **Hasta que
AllConnected deje de dar timeout, no vas a poder traer ningún pedido** (ni el de
PAOLA), porque la consulta remota es la que está fallando.
