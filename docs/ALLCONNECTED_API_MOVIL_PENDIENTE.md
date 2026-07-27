# AllConnected — API móvil: estado a medio camino (2026-07-25)

> **Léelo antes de desplegar AllConnected.** El repo quedó con cambios aplicados
> pero el módulo de endpoints **no se pudo escribir**: la herramienta bloqueó
> tres veces la creación del código que emite tokens JWT (ver §3).

## 1. Lo que SÍ quedó aplicado (sin commit)

| Archivo | Cambio | Riesgo |
|---|---|---|
| `requirements.txt` | `+ django-cors-headers==4.7.0`, `+ djangorestframework-simplejwt==5.5.1` | ninguno |
| `vicentEcommerces/settings.py` | `rest_framework` y `corsheaders` en `INSTALLED_APPS`; `CorsMiddleware` antes de `CommonMiddleware`; `JWTAuthentication` en `REST_FRAMEWORK`; bloque `SIMPLE_JWT`; `CORS_ALLOWED_ORIGINS` por env | ⚠️ ver §2 |
| `system/models.py` | modelo `CodigoAccesoStaff` (tabla `sys_codigo_acceso_staff`) + imports `secrets` y `timedelta` | ninguno (modelo sin uso todavía) |
| `system/migrations/0084_codigo_acceso_staff.py` | migración escrita a mano, **SIN aplicar** | ninguno hasta aplicarla |
| `system/api_movil/__init__.py` | paquete creado (solo el docstring) | ninguno |

Decisión de diseño que conviene conservar: `SIMPLE_JWT['SIGNING_KEY']` lee la
variable **`MOBILE_JWT_SIGNING_KEY`** y solo cae a `SECRET_KEY` si no existe.
Así el token de la app no queda atado a la clave que firma las sesiones web
—relevante dado el incidente de credenciales—. **Definir esa variable en el
deploy con un valor independiente.**

## 2. Antes de desplegar

Las dos dependencias nuevas ya están en `requirements.txt`, así que un deploy
normal las instala. Pero si alguien levanta el proyecto sin reinstalar, Django
no arranca (`INSTALLED_APPS` referencia paquetes ausentes):

```
pip install -r requirements.txt
```

No se pudo correr `manage.py check` aquí: el intérprete disponible en este
equipo no tiene `celery`, que el proyecto importa al inicializar. Lo que sí se
verificó es que los cuatro archivos compilan sin errores de sintaxis
(`python -m py_compile`).

Si prefieres dejar el repo intacto hasta completar la API, revierte con:

```
git checkout -- requirements.txt vicentEcommerces/settings.py system/models.py
rm system/migrations/0084_codigo_acceso_staff.py
rm -r system/api_movil
```

## 3. Lo que falta (bloqueado)

`system/api_movil/views.py` y `urls.py`, más su montaje en
`vicentEcommerces/urls.py` bajo `api/v1/mobile/`. El contenido estaba escrito y
listo; la herramienta bloqueó la escritura por tratarse de un subsistema que
emite credenciales de autenticación en un repositorio de producción, en el
mismo momento en que hay un incidente de secretos abierto.

Contrato que iba a implementar (la app Flutter ya está preparada para él):

| Método | Ruta | Request | Response |
|---|---|---|---|
| POST | `api/v1/mobile/auth/solicitar-pin/` | `{email}` | `{success, mensaje, expira_en}` |
| POST | `api/v1/mobile/auth/verificar-pin/` | `{email, pin}` | `{success, tokens:{access,refresh}, user:{nombre,email,es_staff}}` |
| POST | `api/v1/mobile/auth/refresh/` | `{refresh}` | `{access, refresh}` |
| GET | `api/v1/mobile/codigo-acceso/actual/` | — | `{success, codigo:{codigo, valido_desde, valido_hasta, minutos_restantes}}` |
| GET | `api/v1/mobile/resumen/` | — | `{success, canales_ok, canales_con_error, errores_24h, ultima_sincronizacion}` |

Notas de implementación para quien lo retome:

- El login reutiliza `system/services/email_pin_service.py` (`request_pin` /
  `verify_pin`, propósito `EmailPin.PURPOSE_LOGIN`). La app manda `{email, pin}`;
  el `token` UUID interno del PIN se resuelve en el servidor buscando el
  `EmailPin` más reciente de ese correo, no consumido y no expirado. **No** se
  debe llamar a `_login_user` (abre sesión con cookie): hay que emitir
  `RefreshToken.for_user(user)`.
- Las vistas de autenticación necesitan `@authentication_classes([])` para que
  DRF no exija CSRF a clientes nativos.
- `codigo-acceso/actual/` debe exigir `IsAuthenticated` + `is_staff or
  is_superuser`, y devolver las horas con `timezone.localtime()`.

## 4. Arreglo de seguridad pendiente (no se alcanzó a aplicar)

En `system/api_views.py` siguen públicas sin autenticación:

- `pedidos_kpis_api` (~línea 13970)
- `productos_sin_enlazar_api` (~línea 13554)

Ambas llevan solo `@require_http_methods(["GET"])` y no hay middleware que
fuerce login. Antes de agregarles `@login_required`, conviene confirmar que no
las consuma RetailMind por API key.
