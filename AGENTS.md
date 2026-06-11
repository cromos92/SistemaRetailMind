# Proyecto: SistemaRetailMind

Sistema ERP/POS para retail chileno (DTE/SII, Transbank, multi-sucursal). Núcleo web Django + cliente desktop Tauri ("NEXO POS") que consume API JWT. Incluye asistente conversacional con Codex.

## Stack

- **Python**: 3.11 (definido en `Dockerfile`)
- **Django**: 4.2.2 (LTS)
- **Base de datos**: PostgreSQL
  - Local: vars `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`, `PG_HOST`, `PG_PORT` (defaults: `retailmind` / `postgres` / `admin` / `localhost` / `5432`)
  - Producción: `DATABASE_URL` (Railway / DigitalOcean) vía `dj-database-url`
- **Gestor de paquetes**: `pip` (NO uv, NO poetry — no mezclar)
- **Archivos de dependencias**:
  - [requirements.txt](requirements.txt) — runtime base
  - [requirements-dev.txt](requirements-dev.txt) — solo dev/migración legacy (`mysql-connector-python`, `statsmodels`)
  - [requirements-railway.txt](requirements-railway.txt) — instalado por el Dockerfile en deploy
  - [retailmind/requirements.txt](retailmind/requirements.txt) — set extendido (Celery, Flask helpers, matplotlib, etc.) usado en algunos entornos
- **Modelo de usuario**: `AUTH_USER_MODEL = 'users.Usuario'`
- **Locale**: `es-cl`, `America/Santiago`, `USE_TZ = True`

### Librerías clave

- **API**: `djangorestframework` 3.14, `djangorestframework-simplejwt` 5.3.1, `django-cors-headers` 4.3.1
- **Static**: `whitenoise` (CompressedManifest en prod)
- **Asistente IA**: `anthropic` + `langfuse` (app `assistant`)
- **Hardware POS**: `transbank-pos-sdk`, `pyserial`, `websockets`
- **Predicción de compras**: `statsmodels` + `scipy` (batch offline)
- **Exports**: `reportlab` (PDFs), `openpyxl` (Excel), `Pillow`
- **Cache opcional**: `django-redis` + `redis` (cache `ventas`, controlado por env `REDIS_URL`)
- **Profiling opcional**: `django-silk` (opt-in via `ENABLE_SILK=true`)
- **Async**: `celery` + `flower` (configurado en `retailmind/requirements.txt`)

## Frontend y estilos

### CSS principal

- [retailmind/app/static/css/bootstrap.min.css](retailmind/app/static/css/bootstrap.min.css) — Bootstrap 5
- [retailmind/app/static/css/app.min.css](retailmind/app/static/css/app.min.css) + `custom.min.css` + `icons.min.css` — tema heredado tipo "Velzon"
- [retailmind/app/static/css/nexo-design-system.css](retailmind/app/static/css/nexo-design-system.css) — design system NEXO (paleta `#0066FF` / `#1A1A2E` / `#00D4AA`)
- [retailmind/app/static/css/nexo-responsive.css](retailmind/app/static/css/nexo-responsive.css) — overrides responsivos
- [retailmind/app/static/css/pos-kiosk.css](retailmind/app/static/css/pos-kiosk.css) — modo táctil 1920px (targets 48-64px, fuentes 16-18px, sin `:hover`). Se carga cuando `pos_kiosk` está activo (env `POS_KIOSK_DEFAULT` o `?kiosk=1`)
- [retailmind/app/static/css/pos-transbank.css](retailmind/app/static/css/pos-transbank.css) — UI integración POS físico

### Documentación de estilos (LEER antes de tocar UI)

- [retailmind/NEXO_DESIGN_SYSTEM.md](retailmind/NEXO_DESIGN_SYSTEM.md) — paleta NEXO completa, variables CSS, componentes
- [retailmind/app/templates/vistas/ESTILOS_MODULOS.md](retailmind/app/templates/vistas/ESTILOS_MODULOS.md) — estructura estándar de módulos (header gradiente `#405189 → #0ab39c`, KPI cards, controles de paginación)

### Estructura de templates — IMPORTANTE

**Este proyecto NO usa `{% extends %}` ni bloques `{% block %}`.** El patrón es composición por includes:

```django
{% load static %}
{% include '../../layout/header.html' %}
{% include '../../layout/menu.html' %}

<style>/* CSS inline del módulo */</style>

<div class="page-content">
    <!-- contenido de la vista -->
</div>

{% include '../../layout/footer.html' %}
```

- Parciales base en [retailmind/app/templates/layout/](retailmind/app/templates/layout/): `header.html`, `menu.html`, `footer.html`
- Templates de módulos en [retailmind/app/templates/vistas/](retailmind/app/templates/vistas/), agrupados por carpeta: `modulo_compras/`, `modulo_ventas/`, `modulo_documentos/`, `modulo_existencias/`, `modulo_reportes/`, `modulo_requerimientos/`, `modulo_dashboards/`, `modulo_administracion/`, `modulo_configuracion/`
- Templates de auth en [retailmind/app/templates/registration/](retailmind/app/templates/registration/) (login, login_2fa, password reset)
- Las apps `empresa_management` y `assistant` tienen sus propios `templates/<app>/`
- `TEMPLATES.DIRS` apunta a `BASE_DIR / 'templates'` (carpeta no existe — todo va por `APP_DIRS`); en prod se usa `cached.Loader`

### JavaScript

- jQuery 3.5.1 + vanilla JS. **No hay HTMX, Alpine, React ni Vue** — no los introduzcas
- Libs ya disponibles en `app/static/libs/`: SweetAlert2, Select2, Swiper, Dropzone, Flatpickr, jsvectormap, JsBarcode, FullCalendar, Choices.js, CKEditor 5, particles.js, prismjs, fg-emoji-picker, rater-js, sortablejs, toastify-js
- JS propio en [retailmind/app/static/js/](retailmind/app/static/js/): `layout.js`, `app.js`, `plugins.js`, `edicion_productos.js`, `pos-transbank.js`, `transbank-*.js`, `trazabilidad_dte.js`, `generador_txt_acepta.js`

### REGLA DURA — antes de generar HTML/CSS

Antes de generar cualquier HTML o CSS nuevo:

1. Lee al menos un template existente del mismo módulo (ej. para algo de compras: [gestionCompras.html](retailmind/app/templates/vistas/modulo_compras/gestionCompras.html) o [gestionDteCompras.html](retailmind/app/templates/vistas/modulo_compras/gestionDteCompras.html))
2. Revisa [ESTILOS_MODULOS.md](retailmind/app/templates/vistas/ESTILOS_MODULOS.md) si vas a crear un módulo nuevo
3. Revisa [nexo-design-system.css](retailmind/app/static/css/nexo-design-system.css) para conocer las variables y utilidades ya definidas
4. NUNCA inventes paletas, gradientes o componentes desde cero — usa lo que ya existe (`module-header`, `kpi-card`, `pagination-controls`, `quick-filter-btn`, etc.)
5. Respeta el patrón de includes (`layout/header.html`, `layout/menu.html`, `layout/footer.html`) — no introduzcas `{% extends %}` en archivos que usan includes

## Estructura de apps

- **`users`** — modelo `Usuario` personalizado (AUTH_USER_MODEL), login, recuperación de contraseña, PIN 2FA opcional ([retailmind/users/](retailmind/users/))
- **`app`** — núcleo monolítico: POS, ventas, compras, DTE/SII, inventario, predicciones, dashboards, reportes, cotizaciones, créditos, requerimientos, etiquetas Zebra, ecommerce, Transbank ([retailmind/app/](retailmind/app/)). Tiene 155+ migraciones y `views.py` de 30k+ líneas
- **`empresa_management`** — gestión de empresas y clientes (multi-empresa) ([retailmind/empresa_management/](retailmind/empresa_management/))
- **`assistant`** — chat conversacional con Codex (Anthropic) + tracing con Langfuse ([retailmind/assistant/](retailmind/assistant/)): `agent.py`, `prompts.py`, `tools.py`

### Submódulos relevantes dentro de `app/`

- `models/` — paquete dividido por dominio: `organizacion.py`, `catalogo.py`, `dte.py`, `ventas.py`, `compras.py`, `inventario.py`, `caja.py`, `pos.py`, `precios.py`, `crm.py`, `predicciones.py`, `requerimientos.py`, `cotizaciones.py`, `ecommerce.py`, `etiquetas.py`, `permisos.py`. Re-exportados desde `models/__init__.py`
- `api/` — endpoints para el cliente Tauri y otros: `desktop/`, `external/`, `mobile/`, `sync/`
- `services/` — lógica de dominio aislada: `pos_service.py`, `limbo_dte.py`, `fraud_detection.py`, `analisis_caja.py`, `prediccion_compras.py`, `transbank_*_service.py`
- `management/commands/` — 60+ comandos custom (migración Laravel/MySQL legacy, diagnósticos, reconciliaciones, predicciones batch)
- `templatetags/` — `math_filters.py`, `permisos_tags.py`
- `middleware/` y `middleware_permisos.py` / `middleware_session_timeout.py` — middlewares propios para permisos de menú y timeout de sesión por inactividad
- `tests/` — `test_ventas.py`, `test_inventario.py`, `test_auth.py`, `test_models.py`, `test_txt_dte.py`, `test_cuadratura_nc.py`, `test_ajuste_traspaso.py`, más `factories.py`
- `signals.py`, `stock_notifier.py`, `cache_utils.py`, `decorators.py`, `utils_*.py`

## Comandos comunes

Ejecutar desde [retailmind/](retailmind/) (donde está `manage.py`):

```powershell
# Servidor dev
python manage.py runserver

# Migraciones
python manage.py makemigrations
python manage.py migrate

# Crear superusuario
python manage.py createsuperuser

# Tests (usa el test runner de Django, no pytest)
python manage.py test app
python manage.py test app.tests.test_ventas

# Static files (necesario antes del primer deploy o tras cambios)
python manage.py collectstatic --noinput

# Shell
python manage.py shell

# Comandos custom destacados (en app/management/commands/)
python manage.py calcular_predicciones
python manage.py evaluar_alertas_pendientes
python manage.py generar_notificaciones_dte
python manage.py benchmark_ventas
# (60+ comandos disponibles — listar con `python manage.py help`)
```

Dependencias:

```powershell
# Runtime
pip install -r requirements.txt

# Dev + migración legacy
pip install -r requirements.txt -r requirements-dev.txt

# Set completo (con Celery, etc.)
pip install -r retailmind/requirements.txt
```

Profiler opt-in (silk):

```powershell
$env:DEBUG="true"; $env:ENABLE_SILK="true"; python manage.py runserver
```

## Convenciones de código

### Modelos

- Paquete `app/models/` dividido por dominio (no un archivo único). Cualquier modelo nuevo va al archivo del dominio que le corresponde y se re-exporta en `models/__init__.py`
- Validación de RUT chileno con `RUT_REGEX_VALIDATOR` y `validar_rut_chileno()` en [app/models/base.py](retailmind/app/models/base.py)
- Choices declarados como constantes a nivel de módulo (ej. `TIPO_DOCUMENTO_CHOICES`, `ESTADO_PAGO_CHOICES`) y re-exportados
- `default_auto_field = 'django.db.models.BigAutoField'` en todas las apps

### Vistas

- **100% function-based views (FBV)**. No hay class-based views en el proyecto — no introduzcas CBV salvo que el código alrededor ya las use
- `views.py` principal tiene 30k+ líneas; vistas nuevas van preferentemente a un archivo `views_modulo_<dominio>.py` separado (ya existen 23: `views_modulo_compras.py`, `views_modulo_ventas.py`, `views_modulo_reportes.py`, etc.)
- Decoradores de permisos custom en `app/decorators.py` y `app/utils_permisos.py`
- Logging via `logger = logging.getLogger('app')` (o `'users'`, `'empresa_management'`, `'assistant'`); estos loggers ya están configurados en `settings.LOGGING`

### URLs

- **Sin `app_name` ni namespaces.** [app/urls.py](retailmind/app/urls.py) importa funciones explícitamente y las registra plano. Mantén ese patrón al añadir rutas
- URLs en español, kebab-case o camelCase mezclados según el módulo (revisa los vecinos antes de elegir)

### API REST

- DRF con `IsAuthenticated` por default y autenticación dual (JWT + SessionAuthentication)
- Endpoints para cliente Tauri en `app/api/desktop/`
- Headers CORS custom permitidos: `x-device-id`, `x-app-version`, `x-sucursal-id`, `x-request-timestamp`, `x-api-key`

### Templates

- Patrón de **includes** (no extends, no blocks) — ver sección "Estructura de templates"
- CSS específico del módulo inline en `<style>` al inicio del template (ver `gestionCompras.html` como referencia)
- Nombres de archivos en `camelCase.html` o `snake_case.html` según el módulo — revisa los vecinos

### Tests

- `manage.py test` (no pytest configurado)
- `factory_boy`-style factories en `app/tests/factories.py`

## Reglas para ti, Codex

- **Antes de crear HTML/CSS nuevo**: lee al menos un template del mismo módulo y revisa `nexo-design-system.css` + `ESTILOS_MODULOS.md`. Reusa clases existentes (`module-header`, `kpi-card`, `pagination-controls`, `quick-filter-btn`). NUNCA inventes paletas o gradientes propios
- **Antes de crear modelos nuevos**: revisa el archivo del dominio en `app/models/` y los choices/constantes ya definidos. Añade el modelo al archivo correcto y re-expórtalo en `models/__init__.py`
- **Antes de crear vistas**: usa FBV y, si el módulo es nuevo, crea un archivo `views_modulo_<nombre>.py` en vez de seguir engordando `views.py`
- **Antes de añadir dependencias**: pregúntame primero. Si se aprueba, añádela al archivo de requirements correcto (`requirements.txt` para runtime, `requirements-dev.txt` para herramientas locales, `requirements-railway.txt` para deploy)
- **Gestor de paquetes**: solo `pip`. No introduzcas `uv`, `poetry`, `pipenv` ni `pyproject.toml`
- **Templates**: respeta el patrón de includes (`layout/header.html`, `layout/menu.html`, `layout/footer.html`). No introduzcas `{% extends %}` en archivos que usan includes
- **CSS**: respeta las variables de `nexo-design-system.css` (`--nexo-primary`, `--nexo-accent`, etc.) y los gradientes/utilidades existentes
- **JS**: usa jQuery + vanilla. No introduzcas HTMX, Alpine, Vue ni React
- **URLs**: sin namespaces, importa funciones explícitas en `app/urls.py`
- **Logging**: usa los loggers ya configurados (`app`, `users`, `empresa_management`, `assistant`), no `print()` en código de producción
- **Imports de modelos**: usa siempre `from app.models import X` (el paquete re-exporta), no `from app.models.dte import X`
- **Zona horaria**: el proyecto es `America/Santiago` con `USE_TZ=True`. Usa `django.utils.timezone.now()`, NUNCA `datetime.now()` naive
- **RUT chileno**: usa `validar_rut_chileno()` y `RUT_REGEX_VALIDATOR` de `app.models.base`

## Cosas que NO debes hacer

- **No ejecutes `migrate` ni `makemigrations` sin avisarme primero.** Hay migraciones nuevas sin commitear (`0154_*`, `0155_*`) y el orden importa
- **No corras comandos `clean_*` o `_limpiar_*` de `management/commands/`** — varios borran o reescriben datos productivos (ver [README_CLEAN_MIGRATION_DATA.md](retailmind/app/management/commands/README_CLEAN_MIGRATION_DATA.md) y [SEGURIDAD_CLEAN_VS_MIGRATE.md](retailmind/app/management/commands/SEGURIDAD_CLEAN_VS_MIGRATE.md))
- **No borres datos ni tablas sin confirmación explícita.** Esto incluye `flush`, `truncate`, `Modelo.objects.all().delete()` en `shell`, y los scripts sueltos `_fix_*.py`, `_limpiar_*.py`, `_reconciliacion_*.py` que hay sueltos en `retailmind/`
- **No toques `settings.py` para cambiar configuración de producción** (DATABASE_URL, CORS, ALLOWED_HOSTS, EMAIL_*, ANTHROPIC_API_KEY, secretos). Si necesitas un setting nuevo, propónlo y úsalo via `os.environ.get(...)`
- **No instales paquetes nuevos sin mi visto bueno** ni mezcles gestores de paquetes
- **No hagas `git push --force`, `git reset --hard`, ni borres ramas** sin confirmación
- **No modifiques archivos `__pycache__/`, `.pyc`, `staticfiles/`, ni archivos generados** (logs, migration_errors.log, pagos_*.log, *.csv de reconciliación)
- **No comitees archivos con secretos**: `.env`, `certs/private-key.pem`, tokens de email/Anthropic/Langfuse
- **No corras `collectstatic` sin avisarme** — sobrescribe `staticfiles/` y, en prod, requiere regenerar `staticfiles.json` o todos los `{% static %}` se rompen
- **No introduzcas frameworks frontend** (React, Vue, Alpine, HTMX) ni cambies el patrón de templates por `{% extends %}`
