# 🔴 URGENTE — Credenciales de producción expuestas (2026-07-25)

> Hallazgo de un análisis de configuración de producción de los tres sistemas
> (RetailMind, Hotel, AllConnected). **Verificado a mano**, no es una sospecha.
>
> Este documento es una lista de acciones. No borra ni cambia nada por sí solo.

---

## 1. Lo que está pasando ahora mismo

| Hecho | Verificación |
|---|---|
| El repo **`cromos92/SistemaRetailMind` es PÚBLICO** en GitHub | API de GitHub: `visibility: public`, `private: False` |
| **`retailmind/.env` está versionado** (trackeado) en ese repo | `git ls-files` lo lista |
| Ese archivo contiene credenciales **de producción** | 31 variables, ver §2 |

Un archivo trackeado en un repositorio público **lo puede descargar cualquiera,
sin cuenta**. Borrarlo hoy no soluciona nada: **queda en el historial de commits**.

**Lo mismo, pero menos grave, en los otros dos repos:**

- **Hotel** (`Sistema-Gestion-Hotelera-Django`): `.env` **también está trackeado**
  (con `SECRET_KEY`, `DATABASE_URL`, MercadoPago, Twilio, WhatsApp, OpenAI).
  Mitiga que el repo hoy es **privado** — pero un cambio de visibilidad o un fork
  repite el incidente.
- **AllConnected** (`VicentAllEcommercesConected`): hoy **NO** está trackeado
  (bien hecho), pero **estuvo ~20 commits** y sigue en el historial
  (borrado en `acf6f0f1f "Delete .env"`).

---

## 2. Qué hay que rotar (variables de `retailmind/.env`, solo nombres)

Asumir **todas** comprometidas. Prioridad de arriba hacia abajo:

| Variable | Por qué importa |
|---|---|
| `DATABASE_URL`, `PG_USER`, `PG_PASSWORD` | Acceso al Postgres de producción. **El mismo cluster aloja la base de AllConnected** y ambos usan el mismo usuario administrativo |
| `SECRET_KEY` | Firma los **JWT de la app móvil y del POS** (`SIGNING_KEY = SECRET_KEY`). Con esta clave se pueden **falsificar sesiones de staff sin login** |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Token SMTP de MailerSend. **Es el mismo en los tres sistemas** → por ahí salen el OTP de clientes, el PIN del hotel y el PIN de AllConnected |
| `RETAILMIND_API_KEY`, `ALLCONNECTED_API_KEY` | Las dos direcciones del canal RetailMind ↔ AllConnected (stock, pedidos, facturas) |
| `QZ_PRIVATE_KEY` | Clave privada de firma de impresión (QZ Tray) |
| `MYSQL_*` | Base Laravel legacy |

---

## 3. Orden de acción

1. **Poner el repo en privado.** GitHub → Settings → Danger Zone → Change
   visibility. Detiene la hemorragia; no repara lo ya expuesto.
2. **Revisar Trusted Sources del cluster Postgres** en DigitalOcean. Si acepta
   conexiones desde cualquier IP, restringir a las apps (App Platform + Railway).
   *Esto define si la filtración fue explotable o solo peligrosa.*
3. **Rotar la contraseña de Postgres** y actualizarla en las variables de entorno
   de RetailMind **y de AllConnected** (comparten usuario). Aprovechar para crear
   un usuario por base en vez de usar el administrador del cluster.
4. **Rotar el token SMTP de MailerSend** y actualizarlo en los **tres** sistemas.
5. **Rotar `SECRET_KEY` de RetailMind.** ⚠️ Al cambiarlo se invalidan todos los
   JWT vivos: los usuarios de NEXO Staff y del POS Tauri tendrán que volver a
   entrar. Hacerlo en horario de baja actividad.
6. **Rotar el par de API keys** RetailMind ↔ AllConnected con valores nuevos y
   **distintos entre sí**, actualizando ambos lados (env de los dos sistemas +
   la clave por canal que AllConnected guarda en BD desde su formulario).
7. **Rotar `QZ_PRIVATE_KEY`** y las credenciales `MYSQL_*`.
8. **Sacar los `.env` del control de versiones** (el ignore ya existe; el problema
   es que estaban trackeados de antes):
   ```
   # en SistemaRetailMind
   git rm --cached retailmind/.env
   # en Sistema-Gestion-Hotelera-Django
   git rm --cached .env
   ```
   Después verificar con `git ls-files | grep env` que solo quede `.env.example`.
9. **Quitar los secretos hardcodeados como default** en código versionado:
   - `retailmind/retailmind/settings.py:30` — `SECRET_KEY` con default fijo.
   - `vicentHotelSystem/settings.py:43` — ídem.
   - `vicentHotelSystem/settings.py:184` — **contraseña de aplicación de Gmail en
     texto plano** como default de `EMAIL_HOST_PASSWORD`.

---

## 4. Otros hallazgos de producción (arreglar después de lo anterior)

### 4.1 Hotel: **está corriendo con `DEBUG=True` en producción** 🔴

Verificado en vivo: una URL inexistente en `https://mihotel.webappsolutions.cl`
devuelve la página de depuración de Django. Consecuencias reales:

- Se publica el mapa completo de URLs; ante un error 500 se expondría el
  traceback con variables y settings.
- El bloque `if not DEBUG and IS_PRODUCTION` **nunca se ejecuta** → sin
  redirección a HTTPS, **sin HSTS**, y las cookies de sesión/CSRF viajan **sin
  el flag `Secure`**.
- `CORS_ALLOW_ALL_ORIGINS = DEBUG` queda en `True`: el servidor **refleja
  cualquier Origin con `allow-credentials: true`** — cualquier web puede hacer
  peticiones autenticadas contra el hotel.

Causa probable: el `.env` versionado trae `DEBUG=True` y la plataforma no define
la variable. **Fix**: definir `DEBUG=False` en las variables del deploy.

### 4.2 RetailMind: la validación de API key **falla en abierto**

`app/views_ecommerce.py:271-276` — si `RETAILMIND_API_KEY` no está configurada,
`_verificar_api_key()` devuelve `True`, dejando los endpoints abiertos. Además
acepta la clave por query string (`?api_key=`), que queda en logs y proxies, y
compara con `==` en vez de `hmac.compare_digest`.

### 4.3 AllConnected: endpoints JSON **sin autenticación**

`system/api_views.py` — `pedidos_kpis_api` (:13970) y `productos_sin_enlazar_api`
(:13554) están registrados públicamente con solo `@require_http_methods(["GET"])`.
No hay middleware que fuerce login.

### 4.4 Hotel: el refresh token no rota ni se puede revocar

`ROTATE_REFRESH_TOKENS=True` no aplica porque el endpoint propio
`staff_token_refresh` devuelve el **mismo** token; sin `token_blacklist`, un
refresh robado sirve 7 días y solo se corta rotando la `SECRET_KEY`.

### 4.5 Deuda menor

- `ALLOWED_HOSTS` de RetailMind cae a `*` si falta la variable.
- Hotel en **Django 3.2.18**, fuera de soporte desde abril 2024.
- El `.env` del hotel apunta a la **BD de producción** (igual que RetailMind):
  no correr `migrate`/`test` desde esa carpeta.
- El schema OpenAPI del hotel (`/api/v1/schema/` y `/docs/`) es **público**.
  Útil para integrar, pero expone el mapa de la API.

---

## 5. Estado de la app NEXO Staff frente a esto

- La URL que usa para el hotel (`https://mihotel.webappsolutions.cl/api/v1/`) es
  **la correcta**; el otro dominio que aparece en la app Expo del repo del hotel
  (`hotelinn.webappsolutions.cl`) **no resuelve**.
- La app **no introduce** ninguno de estos problemas, pero **hereda** el de la
  `SECRET_KEY`: mientras no se rote, sus JWT son falsificables.
- Ya se implementó el **cierre de sesión global** (salir de NEXO cierra también
  el hotel), que era el riesgo del celular compartido.
- **AllConnected no se puede conectar** hasta que tenga API con JWT — y eso debe
  ir **después** de la rotación de credenciales.
