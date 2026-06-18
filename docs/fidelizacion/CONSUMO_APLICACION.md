# 📦 Inventario de consumo — SistemaRetailMind

> Inventario consolidado de **todo lo que consume la aplicación web**: dependencias (Python + JavaScript), servicios externos / APIs, variables de entorno, la superficie de API interna y sus consumidores (incluida la **app móvil de fidelización AppFidelizar**), y la infraestructura de datos.

**Generado:** 2026-06-17 · **Método:** inventario automatizado por módulo + verificación fáctica de cada sección contra el código real (paquetes, versiones, endpoints, env vars y servicios confirmados en el repositorio). Las afirmaciones marcadas como *verificado* fueron contrastadas con los archivos fuente.

> ⚠️ Documento de referencia auto-generado. Si cambias `requirements*.txt`, `settings.py`, las apps `api/` o el `Dockerfile`, regénéralo o actualízalo a mano.

## Índice

1. Dependencias (Python + JavaScript)
2. Servicios externos / APIs que la app consume
3. Variables de entorno
4. API interna y consumidores
5. App móvil de fidelización (AppFidelizar)
6. Datos, almacenamiento e infraestructura

---
## 1. Dependencias (Python + JavaScript)

### 1.1 Python — vista consolidada por categoría

Cuatro archivos de requirements (siglas usadas en la columna "Aparece en"):

- **R** = `requirements.txt` (runtime base — sirve el POS)
- **D** = `requirements-dev.txt` (dev / migración legacy)
- **RW** = `requirements-railway.txt` (lo instala el `Dockerfile` en deploy — verificado: `COPY requirements-railway.txt` + `pip install -r requirements-railway.txt`)
- **EXT** = `retailmind/requirements.txt` (set extendido: Celery, Flask helpers, matplotlib, IA, cache, etc.)

> **Verificado:** el `Dockerfile` (raíz del repo, `python:3.11-slim`) instala **solo `requirements-railway.txt`**. Por lo tanto NADA que viva únicamente en **EXT** (anthropic, langfuse, redis, django-silk, celery, matplotlib, Google APIs, etc.) llega a la imagen de deploy de Railway/DO.

#### Web / API / Servidor

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| Django | ==4.2.2 | Framework web (LTS) | R, RW, EXT |
| djangorestframework | ==3.14.0 | API REST (cliente Tauri, mobile, external) | R, RW |
| djangorestframework-simplejwt | ==5.3.1 | Auth JWT para la API | R, RW |
| django-cors-headers | ==4.3.1 | CORS (headers custom x-device-id, x-sucursal-id, etc.) | R, RW |
| PyJWT | ==2.3.0 | Codificación/verificación JWT (base de simplejwt) | R, RW, EXT |
| jwt | ==1.3.1 | Librería JWT alternativa (paquete distinto a PyJWT — posible duplicado/legacy) | EXT |
| gunicorn | ==21.2.0 | Servidor WSGI de producción (CMD del Dockerfile) | R, RW, EXT |
| whitenoise | ==6.5.0 | Servir static files (CompressedManifest en prod) | R, RW, EXT |
| Flask | ==2.0.2 | Helper / servicio auxiliar (no es el core; entorno extendido) | EXT |
| flask-swagger-ui | ==3.36.0 | UI Swagger para documentar API (sobre Flask) | EXT |
| Werkzeug | ==2.0.3 | Dependencia de Flask (WSGI utils) | EXT |
| Jinja2 | ==3.0.3 | Templating (dependencia de Flask) | EXT |
| itsdangerous | ==2.0.1 | Firmas seguras (dependencia de Flask) | EXT |
| MarkupSafe | ==2.0.1 | Escaping (dependencia de Jinja2/Flask) | EXT |
| asgiref | ==3.7.2 | Capa ASGI de Django | EXT |
| sqlparse | ==0.4.4 | Parser SQL (dependencia de Django) | EXT |
| requests | ==2.30.0 | Cliente HTTP (integraciones, SII, etc.) | R, RW, EXT |
| requests-oauthlib | ==2.0.0 | OAuth1/2 sobre requests | EXT |
| oauthlib | ==3.3.1 | Implementación OAuth | EXT |
| httplib2 | ==0.31.0 | Cliente HTTP (dependencia Google API) | EXT |
| urllib3 | ==1.26.8 | Pool HTTP (dependencia de requests) | EXT |
| certifi | ==2021.10.8 | Bundle de certificados CA | EXT |
| charset-normalizer | ==3.1.0 | Detección de encoding (requests) | EXT |
| idna | ==3.4 | Manejo de dominios internacionalizados | EXT |
| jsonify | ==0.5 | Helper de respuesta JSON (utilitario menor) | EXT |
| response | ==0.5.0 | Helper de respuesta (utilitario menor) | EXT |
| generic | ==1.1.1 | Utilidad genérica menor | EXT |

#### Base de datos

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| psycopg2-binary | ==2.9.10 | Driver PostgreSQL (BD principal) | R, RW, EXT |
| psycopg2 | ==2.9.10 | Driver PostgreSQL (compilado; además del -binary) | EXT |
| dj-database-url | ==2.1.0 | Parseo de `DATABASE_URL` (Railway / DigitalOcean) | R, RW, EXT |
| DBManager | ==0.1.3 | Helper de gestión de BD (utilitario) | EXT |
| python-dotenv | ==1.1.1 | Carga de variables de entorno desde `.env` | R, RW, EXT |
| pytz | ==2024.1 | Zonas horarias (America/Santiago) | R, RW, EXT |
| tzdata | ==2023.3 | Base de datos de zonas horarias | R, RW, EXT |
| python-dateutil | ==2.8.2 | Parsing/manejo de fechas | EXT |
| mysql-connector-python | ==9.5.0 | Migración legacy desde Laravel/MySQL (no runtime) | D, RW |

> Nota: `mysql-connector-python` está en **D** (dev/legacy) **y** en **RW** (deploy), pero NO en **R**. El comentario en `requirements.txt` dice explícitamente que se movió a dev (junto con statsmodels/numpy) para no cargarlo en el servidor POS; aun así sigue presente en `requirements-railway.txt`, que es el que instala el `Dockerfile`. El `Dockerfile` además instala `default-libmysqlclient-dev` como dependencia de sistema.

#### IA / Asistente

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| anthropic | >=0.40.0 | Cliente de Claude (app `assistant`, importado en `assistant/agent.py`) | EXT |
| langfuse | >=2.0.0 | Tracing/observabilidad del asistente IA (`assistant/agent.py`) | EXT |

> **Atención (verificado):** `anthropic` y `langfuse` SOLO están en **EXT**, NO en **R** ni en **RW**. Como el `Dockerfile` instala únicamente `requirements-railway.txt`, **el asistente IA no tiene sus dependencias instaladas en el deploy de Railway/DO**. `assistant/agent.py` importa `anthropic`, así que en ese entorno la app `assistant` fallaría al importarse salvo que el deploy instale EXT por otra vía. Esto es una inconsistencia real entre el código y el deploy, no una suposición.

#### Hardware POS

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| transbank-pos-sdk | ==1.0.1 | SDK POS físico Transbank | R, RW |
| pyserial | ==3.5 | Comunicación serial con el POS/hardware | R, RW |
| websockets | ==11.0.3 | Canal WebSocket (puente con hardware / cliente) | R, RW |
| cryptography | >=42.0.0 | Cifrado (DTE/SII, firma, TLS hardware) | RW, EXT |
| cffi | ==1.15.0 | FFI (dependencia de cryptography) | EXT |
| pycparser | ==2.21 | Parser C (dependencia de cffi) | EXT |

> Nota: `cryptography` está en **RW** y **EXT** pero NO en **R** base. El `Dockerfile` instala `libffi-dev` / `libpq-dev` como dependencias de sistema para compilarla.

#### Exports / PDF / Excel / Imágenes

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| reportlab | ==4.4.3 | Generación de PDFs | R, RW, EXT |
| openpyxl | ==3.1.5 | Exportar/leer Excel (.xlsx) | R, RW |
| Pillow | ==11.1.0 (R/RW) / ==9.0.1 (EXT) | Procesamiento de imágenes | R, RW, EXT |
| num2words | ==0.5.14 | Montos en palabras (documentos/DTE) | R, RW |

> **Diferencia de versión relevante (verificada):** Pillow está fijado en **11.1.0** en R/RW pero en **9.0.1** en EXT (set extendido desactualizado).

#### Predicción / Cálculo numérico

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| statsmodels | >=0.14 | Predicción de compras (batch offline) | D, EXT |
| scipy | ==1.8.0 | Cálculo científico (predicción) | EXT |
| numpy | >=1.26,<3 (RW/EXT) | Arrays numéricos (predicción). En D entra como transitiva de statsmodels (no fijada) | RW, EXT |
| matplotlib | ==3.5.1 | Gráficos (predicción / reportes batch) | EXT |
| fonttools | ==4.29.1 | Fuentes (dependencia de matplotlib) | EXT |
| kiwisolver | ==1.3.2 | Solver de layout (dependencia de matplotlib) | EXT |
| cycler | ==0.11.0 | Ciclos de estilo (dependencia de matplotlib) | EXT |
| pyparsing | ==3.0.7 | Parsing (dependencia de matplotlib/packaging) | EXT |

> Nota: `numpy` aparece fijado en RW y EXT (`>=1.26,<3`); en **D** el comentario del archivo documenta que numpy/scipy entran como dependencia transitiva de statsmodels y por eso no se fijan.

#### Async / Celery / Tareas

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| celery | ==5.4.0 | Cola de tareas asíncronas | EXT |
| flower | ==2.0.1 | Monitor web de Celery | EXT |
| amqp | ==5.2.0 | Protocolo AMQP (broker) | EXT |
| kombu | ==5.3.7 | Mensajería (capa de Celery) | EXT |
| billiard | ==4.2.0 | Multiprocessing (fork de Celery) | EXT |
| vine | ==5.1.0 | Promesas (dependencia de Celery) | EXT |
| click / click-didyoumean / click-plugins / click-repl | 8.1.7 / 0.3.1 / 1.1.1 / 0.3.0 | CLI (Celery usa click) | EXT |
| prometheus_client | ==0.20.0 | Métricas Prometheus (flower) | EXT |
| tornado | ==6.4.1 | Servidor async (flower) | EXT |
| humanize | ==4.9.0 | Formato humano de tiempos (flower) | EXT |
| prompt_toolkit | ==3.0.47 | REPL interactivo (celery shell) | EXT |
| wcwidth | ==0.2.13 | Ancho de caracteres (prompt_toolkit) | EXT |

#### Cache

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| django-redis | >=6.0.0 | Backend de cache Redis (cache `ventas`, via `REDIS_URL`) | EXT |
| redis | >=5.0.0 | Cliente Redis | EXT |

> Como con la IA: `django-redis`/`redis` solo viven en **EXT**, no en **RW**, así que el cache Redis no tiene dependencias en el deploy de Railway que instala `requirements-railway.txt`.

#### Profiling

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| django-silk | >=5.0.0 | Profiler opt-in (`ENABLE_SILK=true`) | EXT |

#### Dev / Legacy / Migración / Integraciones externas

| Paquete | Versión | Para qué | Aparece en |
|---|---|---|---|
| mysql-connector-python | ==9.5.0 | Migración Laravel/MySQL legacy (ver Base de datos) | D, RW |
| autopep8 | ==1.6.0 | Formateo de código | EXT |
| pycodestyle | ==2.8.0 | Linter PEP8 (autopep8) | EXT |
| toml | ==0.10.2 | Lectura TOML (config de tooling) | EXT |
| PyYAML | ==6.0 | Lectura YAML | EXT |
| meli | ==3.0.0 | SDK MercadoLibre (integración marketplace) | EXT |
| WooCommerce | ==3.0.0 | API WooCommerce (integración ecommerce) | EXT |
| google-api-python-client | ==2.182.0 | Cliente Google APIs | EXT |
| google-api-core | ==2.25.1 | Core de Google APIs | EXT |
| google-auth | ==2.40.3 | Auth Google | EXT |
| google-auth-httplib2 | ==0.2.0 | Transporte httplib2 para google-auth | EXT |
| google-auth-oauthlib | ==1.2.2 | Flujo OAuth Google | EXT |
| googleapis-common-protos | ==1.70.0 | Protos comunes de Google | EXT |
| proto-plus | ==1.26.1 | Wrappers de protobuf | EXT |
| protobuf | ==6.32.1 | Serialización protobuf | EXT |
| uritemplate | ==4.2.0 | Plantillas URI (google client) | EXT |
| rsa | ==4.9.1 | RSA (google-auth) | EXT |
| pyasn1 / pyasn1_modules | 0.6.1 / 0.4.2 | ASN.1 (criptografía/auth) | EXT |
| cachetools | ==5.5.2 | Caché en memoria (google-auth) | EXT |
| six | ==1.16.0 | Compat Py2/Py3 (transitiva) | EXT |
| colorama | ==0.4.4 | Colores en consola (Windows) | EXT |
| packaging | ==21.3 | Parsing de versiones | EXT |
| typing_extensions | ==4.6.3 | Backports de typing | EXT |
| exceptiongroup | ==1.1.0 | Backport ExceptionGroup | EXT |
| filelock | ==3.9.0 | Locks de archivo (virtualenv) | EXT |
| distlib | ==0.3.6 | Distribución (virtualenv) | EXT |
| platformdirs | ==3.0.0 | Rutas de plataforma (virtualenv) | EXT |
| virtualenv | ==20.19.0 | Entornos virtuales | EXT |

#### Diferencias entre archivos (resumen)

- **R vs RW:** RW añade `mysql-connector-python==9.5.0`, `numpy>=1.26,<3` y `cryptography>=42.0.0` que NO están en R. R los excluye a propósito para aligerar el servidor POS (comentario en el archivo). RW es por lo demás idéntico a R (mismas versiones de Django, DRF, simplejwt, etc.).
- **EXT** es un superset enorme (Celery, Flask, matplotlib, Google APIs, MercadoLibre, WooCommerce, IA, cache, profiling) usado solo en algunos entornos. Incluye `psycopg2` (compilado) además de `psycopg2-binary`.
- **Pillow:** 11.1.0 (R/RW) vs 9.0.1 (EXT) — versión muy desfasada en el set extendido.
- **anthropic / langfuse / django-redis / redis / django-silk:** SOLO en EXT. **NO están en el deploy de Railway** (`requirements-railway.txt`, el único que instala el `Dockerfile`); si el asistente IA o el cache Redis deben correr en ese deploy, faltan sus dependencias.
- **D:** mínimo (`mysql-connector-python==9.5.0`, `statsmodels>=0.14`); el comentario documenta que numpy/scipy entran transitivamente.

---

### 1.2 JavaScript / CSS (terceros)

El proyecto usa **jQuery** + vanilla JS. **No hay React, Vue, Alpine ni HTMX.** Las librerías de terceros se reparten entre tres lugares: vendorizadas en `retailmind/app/static/libs/` (48 subcarpetas), un par de archivos en `static/js/`, y **varias cargadas por CDN** desde `layout/header.html` y `layout/footer.html`.

> **Atención sobre jQuery (verificado):** el archivo se llama `static/js/jquery-3.5.1.min.js` y así se referencia en `footer.html`, **pero su contenido real es jQuery v3.7.0** (la cabecera del archivo declara `jQuery v3.7.0`). El nombre del archivo está desactualizado respecto a la versión que realmente carga.

#### CSS base / tema

| Archivo | Librería / versión | Para qué |
|---|---|---|
| `css/bootstrap.min.css` | Bootstrap **5.2.3** (verificado en cabecera) | Framework CSS base (grid, componentes). Importa la fuente Inter vía Google Fonts |
| `css/app.min.css` (+ `custom.min.css`) | Tema heredado tipo **Velzon** | Estilos del admin/dashboard heredado |
| `css/icons.min.css` | Sets de iconos del tema Velzon (verificado) | Incluye **Material Design Icons** (v6.5.95), **Remix Icon**, **Boxicons** y **Line Awesome** (Free + Brands) |
| `css/nexo-design-system.css` | Design system NEXO (propio) | Paleta NEXO `#0066FF` / `#1A1A2E` / `#00D4AA`, variables `--nexo-*`, componentes |
| `css/nexo-responsive.css` | Propio | Overrides responsivos sobre el design system |
| `css/pos-kiosk.css` | Propio | Modo táctil/kiosko 1920px (targets 48-64px); se enlaza solo si `pos_kiosk` está activo |
| `css/pos-transbank.css` | Propio | UI de la integración POS físico Transbank |
| `css/app-rtl.min.css`, `bootstrap-rtl.min.css`, `custom-rtl.min.css` | Variantes RTL del tema | Soporte right-to-left (heredado, probablemente sin uso en es-cl) |
| `css/myCss.css`, `css/app.css`, `css/bootstrap.css`, `css/custom.css`, `css/icons.css` | Versiones no minificadas / overrides | Fuentes de desarrollo del tema |

CSS de terceros cargado por CDN en `header.html`: **Select2 4.1.0-rc.0** (`select2.min.css`) y **Bootstrap Icons 1.10.5** (cargado desde `footer.html`).

> Los `nexo-*` y `pos-*` son **propios del proyecto**, no terceros.

#### Librerías JS de terceros vendorizadas (`static/libs/` — 48 carpetas)

| Carpeta | Librería | Para qué |
|---|---|---|
| `@ckeditor/ckeditor5-build-classic` | CKEditor 5 (classic build) | Editor de texto enriquecido (WYSIWYG). Cargado en `footer.html` |
| `@simonwep/pickr` | Pickr | Selector de color |
| `@tarekraafat/autocomplete.js` | autoComplete.js | Autocompletado de inputs |
| `aos` | AOS (Animate On Scroll) | Animaciones al hacer scroll |
| `apexcharts` | ApexCharts | Gráficos (líneas, barras) en dashboards. Cargado en `footer.html` |
| `bootstrap` | Bootstrap 5 (JS bundle) | Componentes JS de Bootstrap (modales, dropdowns). Cargado en `footer.html` |
| `card` | Card.js (`card.js` + `jquery.card.js`) | Vista previa animada de tarjetas de crédito |
| `chart.js` | Chart.js | Gráficos (alternativa a ApexCharts). Cargado en `footer.html` |
| `choices.js` | Choices.js **v9.1.0** | Selects mejorados / multiselect con búsqueda. Cargado en `footer.html` |
| `cleave.js` | Cleave.js | Formateo de inputs (montos, teléfonos, tarjetas) |
| `dom-autoscroller` | dom-autoscroller | Autoscroll durante drag (acompaña a dragula) |
| `dragula` | Dragula | Drag & drop de listas |
| `dropzone` | Dropzone.js | Subida de archivos por arrastre. CSS y JS cargados en header/footer |
| `echarts` | Apache ECharts | Gráficos avanzados/interactivos |
| `feather-icons` | Feather Icons | Set de iconos SVG. Cargado en `footer.html` |
| `fg-emoji-picker` | fg-emoji-picker | Selector de emojis |
| `filepond` | FilePond | Subida de archivos (alternativa a Dropzone) |
| `filepond-plugin-file-encode` | FilePond plugin | Codifica archivos a base64 al subir |
| `filepond-plugin-file-validate-size` | FilePond plugin | Valida tamaño de archivo |
| `filepond-plugin-image-exif-orientation` | FilePond plugin | Corrige orientación EXIF de imágenes |
| `filepond-plugin-image-preview` | FilePond plugin | Previsualización de imágenes |
| `flatpickr` | Flatpickr | Selector de fecha/hora. Cargado en header/footer |
| `fs` | **Paquete placeholder npm `fs@0.0.1-security`** | NO es una librería funcional: es el stub de seguridad de npm (`security-holder`). Vendorizado por error/arrastre; sin uso real |
| `fullcalendar` | FullCalendar | Calendario de eventos |
| `glightbox` | GLightbox | Lightbox para imágenes/video |
| `gmaps` | GMaps.js | Wrapper de Google Maps |
| `gridjs` | Grid.js | Tablas avanzadas (orden, búsqueda, paginación) |
| `isotope-layout` | Isotope | Layouts filtrables/masonry |
| `jsvectormap` | jsVectorMap | Mapas vectoriales (ej. mapa de Chile). Cargado en header/footer |
| `leaflet` | Leaflet | Mapas interactivos open-source |
| `list.js` | List.js | Búsqueda/orden/filtro de listas HTML |
| `list.pagination.js` | List.js pagination plugin | Paginación para List.js |
| `masonry-layout` | Masonry | Layout tipo mosaico |
| `moment` | Moment.js | Manejo de fechas (legacy) |
| `multi.js` | multi.js | Multiselect accesible |
| `node-waves` | Waves | Efecto ripple en clics (Material). Cargado en `footer.html` |
| `nouislider` | noUiSlider | Sliders de rango |
| `particles.js` | particles.js | Fondos de partículas animadas |
| `prismjs` | Prism.js | Resaltado de sintaxis de código |
| `quill` | Quill | Editor de texto enriquecido (alternativa a CKEditor) |
| `rater-js` | rater-js | Widget de estrellas/valoraciones |
| `shepherd.js` | Shepherd | Tours guiados / onboarding |
| `simplebar` | SimpleBar | Scrollbars personalizadas. Cargado en `footer.html` |
| `sortablejs` | SortableJS | Reordenar listas por drag & drop |
| `sweetalert2` | SweetAlert2 **v11.6.11** (verificado en cabecera) | Alertas/modales (muy usado en confirmaciones). Cargado en header/footer |
| `swiper` | Swiper | Carruseles/sliders táctiles. Cargado en header/footer |
| `toastify-js` | Toastify | Notificaciones tipo toast |
| `wnumb` | wNumb | Formateo de números (acompaña a noUiSlider) |

#### Librerías JS de terceros cargadas por CDN (NO vendorizadas)

Referenciadas directamente desde `layout/header.html` y `layout/footer.html`:

| Librería | Versión | Origen | Para qué |
|---|---|---|---|
| Select2 | **4.1.0-rc.0** | jsdelivr (CSS en header, JS en footer) | Selects con búsqueda/tags. **Muy usado** (compras, ventas, DTE, inventario, créditos, usuarios, etc.) |
| DataTables | **1.11.5** (+ `dataTables.bootstrap5`) | cdn.datatables.net | Tablas interactivas (orden, paginación, búsqueda) |
| DataTables Responsive | **2.2.9** | cdn.datatables.net | Modo responsivo de DataTables |
| DataTables Buttons | **2.2.2** (+ print + html5) | cdn.datatables.net | Botones de export/print en DataTables |
| pdfmake | **0.1.53** (+ `vfs_fonts`) | cdnjs | Generación de PDFs en cliente (export DataTables) |
| JSZip | **3.1.3** | cdnjs | Compresión (export Excel de DataTables) |
| SheetJS (xlsx) | **0.16.9** (`xlsx.full.min.js`) | cdnjs | Lectura/escritura de Excel en cliente |
| xlsx-renderer | **0.3.1** | jsdelivr | Render de plantillas Excel en cliente |
| Bootstrap Icons | **1.10.5** | jsdelivr | Set de iconos (clases `bi-*`) |
| lord-icon | 2.1.0 (vendor local en `js/pages/`) | local | Iconos animados |

#### JS propio (no terceros, para contexto)

`static/js/`: `jquery-3.5.1.min.js` (vendor — nombre 3.5.1 pero contenido v3.7.0), `JsBarcode.all.min.js` (vendor — generación de códigos de barras, cargado en `footer.html`), `foto_lightbox.js`, y módulos propios `layout.js`, `app.js`, `plugins.js`, `edicion_productos.js`, `pos-transbank.js`, `transbank-helpers.js`, `transbank-pos-sdk.js`, `transbank-web-serial.js`, `transbank-webserial.js`, `trazabilidad_dte.js`, `generador_txt_acepta.js`.

---

**Discrepancias detectadas frente a CLAUDE.md (JS):**

- **Select2 SÍ está disponible y se usa intensamente** — pero NO vendorizado en `libs/` sino cargado por **CDN** (`select2@4.1.0-rc.0`, jsdelivr) desde `header.html` (CSS) y `footer.html` (JS). La mención en CLAUDE.md es correcta; no es una referencia obsoleta. **Choices.js** (v9.1.0) y **multi.js** también están vendorizadas, pero no reemplazan a Select2.
- CLAUDE.md no menciona varias libs que SÍ están vendorizadas en `libs/`: ApexCharts, Chart.js, ECharts, Leaflet, GLightbox, GMaps, Grid.js, Quill, Dragula, FilePond, Pickr, Shepherd, AOS, Isotope, Masonry, autoComplete.js, multi.js, Cleave.js, noUiSlider/wNumb, Feather, node-waves, SimpleBar, Moment, List.js, Card.js. Son parte del bundle del tema Velzon (muchas probablemente sin uso real).
- CLAUDE.md tampoco menciona las libs cargadas por **CDN** que sí se usan: **DataTables** (1.11.5 + responsive/buttons), **pdfmake**, **JSZip**, **SheetJS/xlsx**, **xlsx-renderer**, **Bootstrap Icons**. DataTables en particular es relevante para las tablas exportables.
- La carpeta `libs/fs` NO es un helper del tema: es el paquete placeholder de seguridad de npm (`fs@0.0.1-security`), sin código funcional.

---

## 2. Servicios externos / APIs

Inventario de todos los servicios externos / APIs que la aplicación consume o expone. Se distingue **saliente** (RetailMind llama hacia afuera) de **entrante** (un tercero llama a RetailMind).

### Resumen rápido

| Servicio | Dirección | Cliente / Protocolo | Criticidad | Env / credenciales |
|---|---|---|---|---|
| Anthropic / Claude | Saliente | SDK `anthropic` (HTTPS) | Opcional (app `assistant`) | `ANTHROPIC_API_KEY` |
| Langfuse (tracing) | Saliente | SDK `langfuse` (HTTPS) | Opcional | `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` |
| Transbank POS físico (SDK serial) | Saliente local | `transbank-pos-sdk` + `pyserial` (serial COM) | Crítico para pago tarjeta | ninguna env (puerto autodetectado) |
| Transbank POS (WebSocket agente desktop) | Saliente local | `websockets` (ws://) | Opcional/alternativo al serial | `TRANSBANK_WEBSOCKET_URL` |
| Transbank POS (Web Serial, navegador) | Saliente local | Web Serial API (browser) | Crítico para pago tarjeta (cliente web) | ninguna |
| SII / Acepta (DTE) | **Sin HTTP directo** | Generación de TXT + QZ Tray | Crítico (facturación) | (sin env de red; CAF/folios en BD) |
| QZ Tray (impresión térmica) | Saliente local | Firma local + WebSocket del browser al daemon QZ | Importante (vouchers/tickets) | `QZ_PRIVATE_KEY_PATH`, `QZ_CERTIFICATE_PATH` (fallback env: `QZ_PRIVATE_KEY`, `QZ_CERTIFICATE`) |
| Email / SMTP | Saliente | `django.core.mail` (SMTP) | Importante (recuperación clave, envíos) | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` |
| Redis (cache `ventas`) | Saliente | `django-redis` (redis://) | Opcional (fallback LocMem) | `REDIS_URL` |
| AllConnected — push stock | Saliente | `requests` (HTTP POST) | Opcional | `ALLCONNECTED_WEBHOOK_URL`, `ALLCONNECTED_CANAL_ORIGEN_ID` |
| AllConnected — push factura/boleta | Saliente | `requests` (HTTP POST) | Opcional (default OFF) | `ALLCONNECTED_API_BASE_URL`, `ALLCONNECTED_API_KEY`, `ALLCONNECTED_API_HEADER_NAME`, `ALLCONNECTED_WEBHOOK_FACTURA_ENABLED`, `ALLCONNECTED_WEBHOOK_FACTURA_PATH` |
| AllConnected — pull pedidos | Saliente | `requests` (HTTP GET) | Opcional | `ALLCONNECTED_API_BASE_URL`, `ALLCONNECTED_API_KEY`, `ALLCONNECTED_API_HEADER_NAME`, `ALLCONNECTED_PEDIDOS_PATH` |
| Ecommerce externo — fotos portada (realsport.cl / paola.cl) | Saliente | `requests` (HTTP GET) | Opcional | credenciales en BD (`CredencialesEcommerce`), no env |
| AllConnected / Marketplaces — ingesta pedidos (push) | **Entrante** | DRF endpoints + API key | Opcional | `RETAILMIND_API_KEY` |
| Cliente Tauri "NEXO POS" / móvil | **Entrante** | DRF + JWT | Crítico (cliente desktop) | JWT (`SIMPLE_JWT`), CORS |

---

### Detalle por servicio

#### 1. Anthropic / Claude (asistente conversacional)
- **Propósito**: chat conversacional con tool-use sobre datos de la empresa/sucursal (app `assistant`).
- **Invocado por**: `retailmind/assistant/agent.py` (`AssistantAgent`), usa el SDK `anthropic.Anthropic(api_key=...)`, `client.messages.create(...)`. Modelo hardcodeado `claude-sonnet-4-5-20250929`, `MAX_TOKENS=4096`, `MAX_TOOL_CALLS=10`. Las tools/prompts viven en `assistant/tools.py` y `assistant/prompts.py` (no hacen llamadas HTTP propias; solo definen herramientas que ejecutan ORM local — grep negativo de clientes HTTP en ambos).
- **Dirección**: saliente (HTTPS a la API de Anthropic).
- **Credenciales**: `ANTHROPIC_API_KEY` (settings.py:573). Si está vacío, `self.client = None` y el asistente responde "no está configurado".
- **Paquete**: `anthropic>=0.40.0` (en `retailmind/requirements.txt`, no en el `requirements.txt` base — por eso es opcional).
- **Criticidad**: opcional — degradación elegante; el resto del ERP funciona sin él. El import de `anthropic` está envuelto en try/except (`ANTHROPIC_AVAILABLE`).

#### 2. Langfuse (observabilidad / tracing del asistente)
- **Propósito**: tracing de las llamadas a Claude (`@observe` en `chat()` y `_call_claude()`), metadata de usuario/empresa/sucursal.
- **Invocado por**: `retailmind/assistant/agent.py` vía `from langfuse.decorators import observe, langfuse_context`.
- **Dirección**: saliente (HTTPS al host de Langfuse).
- **Credenciales**: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST` (default `https://cloud.langfuse.com`) — settings.py:577-579.
- **Paquete**: `langfuse>=2.0.0` (en `retailmind/requirements.txt`).
- **Criticidad**: opcional — import envuelto en try/except (`LANGFUSE_AVAILABLE`), con decorador `observe` dummy si no está instalado.

#### 3. Transbank — POS físico vía SDK serial (`transbank-pos-sdk` + `pyserial`)
- **Propósito**: comunicación directa con el terminal POS Integrado (ventas con tarjeta, anulaciones, cierre de día, carga de llaves). El terminal a su vez se conecta a Transbank para autorizar.
- **Invocado por**: `retailmind/app/services/transbank_pos_sdk_service.py` (`POSService`, singleton). Usa `from transbank import POSIntegrado` y `import serial` (pyserial) para listar/validar puertos COM. Operaciones del SDK invocadas: `open_port`, `sale`, `multicode_sale`, `refund`, `load_keys`, `poll`, `get_totals`, `close_day` (envueltas en métodos en español: `venta_multicodigo`, etc.).
- **Dirección**: saliente local (conexión serial COM/USB al hardware; el POS conecta a Transbank).
- **Credenciales**: ninguna env; autodetección de puerto y baudrate (probando `[115200, 9600, 19200, 38400, 57600]`). `commerce_code`/`terminal_id` vienen del propio terminal.
- **Paquetes**: `transbank-pos-sdk==1.0.1`, `pyserial==3.5` (en `requirements.txt`).
- **Criticidad**: crítico para cobros con tarjeta cuando el POS corre server-side.

#### 4. Transbank — POS vía WebSocket a agente desktop (`websockets`)
- **Propósito**: ruta alternativa que habla con un "agente desktop de Transbank" por WebSocket (acciones JSON: `getPorts`, `openPort`, `doSale`, `cancelSale`, `poll`).
- **Invocado por**: `retailmind/app/services/transbank_sdk_service.py` (`TransbankSDKService`, async con `websockets.connect`).
- **Dirección**: saliente local (ws://).
- **Credenciales**: `TRANSBANK_WEBSOCKET_URL` (default `ws://localhost:8090`) — leído vía `getattr(settings, 'TRANSBANK_WEBSOCKET_URL', 'ws://localhost:8090')` en el constructor del servicio; **no está definido en `settings.py`**, así que en práctica siempre se usa el default salvo que se inyecte como atributo/env. Conviene confirmar si producción lo setea por env.
- **Paquete**: `websockets==11.0.3`.
- **Criticidad**: opcional / alternativo al SDK serial directo.

#### 5. Transbank — POS vía Web Serial API (navegador)
- **Propósito**: integración del POS físico desde el cliente web/kiosk directamente por el puerto serial del navegador (protocolo STX/ETX/ACK/NAK, códigos de respuesta del POS Integrado).
- **Invocado por**: JS de front: `retailmind/app/static/js/transbank-webserial.js` (y la variante `transbank-web-serial.js`), `transbank-helpers.js`, `transbank-pos-sdk.js`, `pos-transbank.js` — usan `navigator.serial.getPorts()` / `requestPort()`.
- **Dirección**: saliente local (navegador → puerto serial; sin red).
- **Credenciales**: ninguna.
- **Criticidad**: crítico para cobro con tarjeta en el flujo web.

#### 6. SII / DTE / Acepta (documentos tributarios electrónicos)
- **Propósito**: emisión de DTE (boletas/facturas/NC). **No hay llamada HTTP directa a SII ni a Acepta desde el backend** — el patrón es generar un archivo **TXT** que se sube/procesa en la plataforma Acepta.
- **Invocado por**: front `retailmind/app/static/js/generador_txt_acepta.js` (genera el TXT vía `fetch('/app/documentos/generar-txt-acepta/')`, también `/app/documentos/generar-txt-desde-dte/`) y `trazabilidad_dte.js`. Backend: `views_modulo_documentos.generar_txt_acepta_api` (`/app/documentos/generar-txt-acepta/` en `app/urls.py:1104`), sin clientes `requests`/`zeep`/`soap` a SII/Acepta (grep negativo). `app/services/limbo_dte.py` solo maneja movimientos de stock por NC/AJUSTE de traspaso (lógica ORM interna, sin red).
- **Dirección**: indirecta — RetailMind produce TXT; el envío al SII lo hace Acepta fuera de la app.
- **Credenciales**: sin env de red. CAF/folios y datos de firma se gestionan en BD; ver QZ Tray (siguiente) para la firma del token de impresión, no de DTE.
- **Criticidad**: crítico para facturación (pero el canal hacia SII es offline/TXT, no API).

#### 7. QZ Tray (impresión térmica silenciosa)
- **Propósito**: imprimir tickets/vouchers en impresora térmica sin diálogo, vía el daemon QZ Tray instalado en el equipo.
- **Invocado por**: endpoints backend `qz_certificado`, `qz_firmar`, `qz_config_sucursal` (en `app/views.py`: `qz_certificado` ~:31494, `qz_firmar` ~:31523, `qz_config_sucursal` ~:31583; URLs en `app/urls.py:757-759` → `/app/qz/certificado/`, `/app/qz/firmar/`, `/app/qz/config/`). `qz_firmar` **firma localmente** el token que QZ pide con la clave privada RSA (`cryptography`, `PKCS1v15` + `SHA512`, base64) — no hace HTTP externo. El navegador habla con el daemon QZ Tray local por WebSocket.
- **Dirección**: saliente local (browser ↔ daemon QZ en localhost); la firma es local.
- **Credenciales**: `QZ_PRIVATE_KEY_PATH` (`BASE_DIR/retailmind/certs/private-key.pem`) y `QZ_CERTIFICATE_PATH` (`.../digital-certificate.txt`) — settings.py:492-493. Si el archivo no existe, hay fallback a variable de entorno: `QZ_PRIVATE_KEY` (clave privada PEM) en `qz_firmar` y `QZ_CERTIFICATE` (certificado) en `qz_certificado`; ambos soportan `\n` literal o saltos reales.
- **Criticidad**: importante para impresión de comprobantes; degradable a impresión normal.

#### 8. Email / SMTP
- **Propósito**: recuperación de contraseña, notificaciones, envíos de documentos.
- **Invocado por**: `django.core.mail` (`send_mail`/`EmailMessage`/`EmailMultiAlternatives`) en `users/views.py`, `app/views.py`, `app/views_modulo_ventas.py`, `app/views_modulo_requerimientos.py`, `app/views_modulo_cotizaciones.py`, `app/services/cliente_app_service.py`, `retailmind/views.py`. Scripts sueltos de prueba: `test_email_config.py`, `test_enviar_email.py`.
- **Dirección**: saliente (SMTP).
- **Credenciales**: `EMAIL_HOST` (default `smtp.gmail.com`), `EMAIL_PORT` (default 587), `EMAIL_USE_TLS` (default `True`), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` (default = `EMAIL_HOST_USER`) — settings.py:304-309. Soporta Gmail App Password o MailerSend (ejemplos comentados en settings).
- **Criticidad**: importante (recuperación de clave / OTP de cliente); no bloquea operación POS.

#### 9. Redis (cache `ventas`)
- **Propósito**: cache compartido entre workers para dashboards/indicadores del módulo ventas, con invalidación atómica desde signals.
- **Invocado por**: configurado en `settings.py:436-483` (`CACHES['ventas']` con `django_redis.cache.RedisCache` cuando hay `REDIS_URL`). Si `REDIS_URL` está vacío, ese cache cae a `LocMemCache`. `DJANGO_REDIS_IGNORE_EXCEPTIONS = True` (módulo, línea 483) e `IGNORE_EXCEPTIONS: True` (en `OPTIONS` del cache `ventas`) para no crashear ante fallos de red.
- **Dirección**: saliente (redis://).
- **Credenciales**: `REDIS_URL` (settings.py:434).
- **Paquetes**: `django-redis>=6.0.0`, `redis>=5.0.0` (en `retailmind/requirements.txt`).
- **Criticidad**: opcional — fallback transparente a LocMem. Los caches `default` (TTL 300s) y `catalogo` (TTL 900s) son SIEMPRE LocMem.

#### 10. AllConnected — push de stock (webhook saliente)
- **Propósito**: al descontar stock (venta/devolución/ajuste), notifica a AllConnected (VicentAllEcommercesConected) para que propague a Shopify/Paris/Ripley/realsport, etc.
- **Invocado por**: `retailmind/app/stock_notifier.py` — `requests.post` fire-and-forget en thread daemon a `ALLCONNECTED_WEBHOOK_URL` (POST `/app/sincronizacion-stock/`). Logger `app.stock_notifier`. Funciones `notificar_cambio_stock` / `notificar_cambios_stock_batch`.
- **Dirección**: saliente (HTTP POST).
- **Credenciales**: `ALLCONNECTED_WEBHOOK_URL`, `ALLCONNECTED_CANAL_ORIGEN_ID` (settings.py:636-640). Sin URL = deshabilitado. El payload prioriza `rut_empresa` sobre `idCanalOrigen` (legacy).
- **Criticidad**: opcional; no bloquea la caja.

#### 11. AllConnected — push de factura/boleta (webhook saliente)
- **Propósito**: al emitir boleta/factura o NC que anula, notifica en tiempo real a AllConnected (complementa la conciliación diaria por pull `GET /api/ventas/`). Idempotente por `numero_documento`, 3 intentos con backoff (5s, 30s).
- **Invocado por**: `retailmind/app/factura_notifier.py` — `requests.post` en thread daemon, disparado desde el signal `post_save` de `Dte` (`programar_notificacion_factura` → `transaction.on_commit` → `_notificar`).
- **Dirección**: saliente (HTTP POST).
- **Credenciales**: reusa `ALLCONNECTED_API_BASE_URL`, `ALLCONNECTED_API_KEY`, `ALLCONNECTED_API_HEADER_NAME` (settings). Propias del webhook (solo `os.environ`, NO definidas en settings.py): `ALLCONNECTED_WEBHOOK_FACTURA_ENABLED` (**OBLIGATORIO = "true"** para activar, default OFF) y `ALLCONNECTED_WEBHOOK_FACTURA_PATH` (default `/system/webhooks/retailmind/factura/`).
- **Criticidad**: opcional, **deshabilitado por default** (para que commands de migración masiva no disparen miles de webhooks).

#### 12. AllConnected — pull de pedidos (saliente)
- **Propósito**: RetailMind sale a consultar pedidos pendientes de marketplaces (botón "Traer pedidos") e ingesta cada uno reutilizando `app.views_ecommerce._ingestar_pedido_dict`.
- **Invocado por**: `retailmind/app/services/allconnected_pedidos_service.py` (`traer_pedidos_pendientes`) — `requests.get` a `{base}{path}`, timeout 90s. `requests` importado lazy. Disparado desde la vista `traer_pedidos_allconnected` (`/app/ecommerce/pedidos/traer/`).
- **Dirección**: saliente (HTTP GET).
- **Credenciales**: `ALLCONNECTED_API_BASE_URL` (default `''` = pull deshabilitado; sin URL hardcodeada — los ejemplos en código son `https://allconnected.host` en settings.py:649 y `https://ecommerce.webappsolutions.cl` en docstrings, ambos ilustrativos), `ALLCONNECTED_API_KEY`, `ALLCONNECTED_API_HEADER_NAME` (default `X-AllConnected-Key`), `ALLCONNECTED_PEDIDOS_PATH` (default `/app/pedidos/pendientes/`) — settings.py:649-652.
- **Criticidad**: opcional; sin él la UI solo muestra pedidos recibidos por push.

#### 13. Ecommerce externo — sincronización de fotos de portada (realsport.cl / paola.cl)
- **Propósito**: traer URLs de imágenes de portada por SKU desde ecommerces externos (`GET /api/v1/products/images/`, health en `/api/v1/health/`).
- **Invocado por**: `retailmind/app/services/realsport_imagenes_service.py` — `requests.get` (lazy import), timeout 15s. Cachea resultados en el cache `default` con TTL 1h (`CACHE_TTL_RESOLUCION = 3600`).
- **Dirección**: saliente (HTTP GET).
- **Credenciales**: **no usa env** — lee de la tabla `CredencialesEcommerce` en BD (`url_api`, `api_key`, `header_name` con default `X-AllConnected-Key`, gestionada desde Configuración → Integraciones Ecommerce).
- **Criticidad**: opcional (enriquecimiento de catálogo).

#### 14. (Entrante) Ingesta de pedidos ecommerce / API externa
- **Propósito**: AllConnected/marketplaces hacen **push** de pedidos a RetailMind (mismo serializador/ingesta que el pull, vía `_ingestar_pedido_dict`).
- **Expuesto por**: app DRF `app/api/external/` (con `authentication.py`, `views.py` que incluye `GET /api/ventas/` para conciliación, `serializers.py`, `urls.py`) y `app/views_ecommerce.py` (`_ingestar_pedido_dict`, `api_recibir_pedido_ecommerce`, `api_asignar_ticket_rm`); URLs `/app/api/ecommerce/pedidos/` (recepción push), `/app/api/ecommerce/pedidos/consultar/`, etc. (`app/urls.py:1387+`).
- **Dirección**: entrante (HTTP POST hacia RetailMind).
- **Credenciales**: `RETAILMIND_API_KEY` (settings.py:582) — validado por `app/api/external/authentication.py` vía header `X-Api-Key` (con fallback Bearer).
- **Criticidad**: opcional según uso del canal ecommerce.

#### 15. (Entrante) Cliente desktop Tauri "NEXO POS" y móvil
- **Propósito**: el cliente desktop/móvil consume la API JWT de RetailMind.
- **Expuesto por**: `app/api/desktop/`, `app/api/mobile/`, `app/api/sync/`, `app/api/cliente/` (DRF). Throttling con `ScopedRateThrottle` en endpoints de fidelización (`otp_solicitar` 5/h, `otp_verificar` 10/h, `vincular_cliente` 5/h — settings.py:516-520).
- **Dirección**: entrante (HTTPS + JWT).
- **Credenciales/config**: `SIMPLE_JWT` (`ALGORITHM='HS256'`, `SIGNING_KEY=SECRET_KEY`, `ISSUER='retailmind'`, access 12h / refresh 7d con rotación + blacklist — settings.py:533-559). CORS: en `DEBUG` se usa `CORS_ALLOW_ALL_ORIGINS=True`; en prod `CORS_ALLOWED_ORIGINS` = `http://localhost:1420`, `tauri://localhost`, `https://tauri.localhost`, `https://retail.webappsolutions.cl` (+ dominio Railway si `RAILWAY_PUBLIC_DOMAIN`). Headers custom: `x-device-id`, `x-app-version`, `x-sucursal-id`, `x-request-timestamp`, `x-api-key`.
- **Criticidad**: crítico (es el cliente POS principal de escritorio).

---

### Notas

- **No se encontraron** clientes hacia: SII directo, Acepta vía API/SOAP (`zeep`/`suds`/`soap`), Google Maps/gmaps/geocoding, AWS S3/boto3, Sentry, Cloudinary, Firebase/FCM, Twilio, SendGrid, Stripe/MercadoPago/PayPal. El grep de esos términos fue negativo (los matches aparentes fueron por fragmentos de palabra en plantillas/JS, no por integraciones reales).
- El único cliente HTTP genérico (`requests==2.30.0`) se usa en **4** módulos salientes: `stock_notifier.py`, `factura_notifier.py`, `allconnected_pedidos_service.py` y `realsport_imagenes_service.py` (todos hacia AllConnected/ecommerce). `httpx`/`aiohttp` no se usan (grep negativo).
- Alcance no cubierto exhaustivamente: los 60+ management commands y los scripts sueltos `_fix_*`/`_reconciliacion_*` en la raíz de `retailmind/` no se auditaron línea por línea por posibles clientes HTTP adicionales.

---

## 3. Variables de entorno

Inventario de TODAS las variables de entorno que la aplicación **lee** en runtime. La carga se hace con `python-dotenv` (`load_dotenv()` + `load_dotenv(BASE_DIR / '.env')`) en `retailmind/retailmind/settings.py`. La mayoría se leen ahí; algunas se leen directamente en código de aplicación (`factura_notifier.py`, `views.py` para QZ Tray) y un bloque grande de `MYSQL_*` solo lo usan los comandos de migración legacy.

> Convención de defaults: cuando el valor por defecto es `''` (string vacío), normalmente significa "feature deshabilitado / no configurado".

### Base de datos (PostgreSQL — principal)

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `DATABASE_URL` | (sin default; si existe, gana) | URL completa de conexión en producción (Railway / DigitalOcean), parseada con `dj_database_url.config` (`conn_max_age=600`, `conn_health_checks=True`). Si está definida, ignora las `PG_*` | `retailmind/retailmind/settings.py:150-158` |
| `PG_DATABASE` | `retailmind` | Nombre de la BD local (solo si NO hay `DATABASE_URL`). Nota: `.env`/`.env.example` usan `retail` (default real distinto del del código) | `retailmind/retailmind/settings.py:164` |
| `PG_USER` | `postgres` | Usuario PostgreSQL local | `retailmind/retailmind/settings.py:165` |
| `PG_PASSWORD` | `admin` | Password PostgreSQL local | `retailmind/retailmind/settings.py:166` |
| `PG_HOST` | `localhost` | Host PostgreSQL local | `retailmind/retailmind/settings.py:167` |
| `PG_PORT` | `5432` | Puerto PostgreSQL local | `retailmind/retailmind/settings.py:168` |

### Base de datos (MySQL legacy — solo migración/diagnóstico)

No las consume la app web; solo comandos de `app/management/commands/` (migración desde Laravel/Vicent/holdingtebes y diagnósticos). No están en `settings.py`.

| Variable | Default | Qué controla | Archivo (ejemplos) |
|---|---|---|---|
| `MYSQL_HOST` | (ninguno; en `migrate_from_vicent.py` default `localhost`) | Host del MySQL origen para migración legacy | `app/management/commands/migrate_from_laravel.py:60`, `migrate_from_vicent.py:165`, y ~30 comandos más |
| `MYSQL_PORT` | `3306` | Puerto MySQL origen | `migrate_from_laravel.py:61`, etc. |
| `MYSQL_DATABASE` | (ninguno; `migrate_from_vicent.py` default `vicent_software`) | BD origen | `migrate_from_laravel.py:62`, `migrate_from_vicent.py:162` |
| `MYSQL_USER` | (ninguno; `migrate_from_vicent.py` default `root`) | Usuario MySQL origen | `migrate_from_laravel.py:63`, `migrate_from_vicent.py:163` |
| `MYSQL_PASSWORD` | (ninguno; `migrate_from_vicent.py` default `''`) | Password MySQL origen | `migrate_from_laravel.py:64`, `migrate_from_vicent.py:164` |

> En total ~32 comandos de `management/commands/` referencian `MYSQL_*`. En `migrate_from_laravel.py` se leen a nivel de módulo con `os.getenv(...)` SIN defaults (salvo `MYSQL_PORT=3306`); en `migrate_from_vicent.py` se inyectan en `settings.DATABASES['vicent_mysql']` con defaults `localhost`/`vicent_software`/`root`/`''`. El `.env` real apunta `MYSQL_*` a `dbHoldingTebes` en DigitalOcean (puerto `25060`).

### Email

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `EMAIL_HOST` | `smtp.gmail.com` | Servidor SMTP | `retailmind/retailmind/settings.py:304` |
| `EMAIL_PORT` | `587` (int) | Puerto SMTP | `retailmind/retailmind/settings.py:305` |
| `EMAIL_USE_TLS` | `True` | Activar TLS. Comparación literal `== 'True'` (case-sensitive, T mayúscula): un valor `true` en minúsculas DESACTIVA TLS (a diferencia de `DEBUG`/`ENABLE_SILK`/`POS_KIOSK_DEFAULT` que usan `.lower() == 'true'`) | `retailmind/retailmind/settings.py:306` |
| `EMAIL_HOST_USER` | `None` | Usuario/cuenta SMTP | `retailmind/retailmind/settings.py:307` |
| `EMAIL_HOST_PASSWORD` | `None` | Password/token SMTP (secreto) | `retailmind/retailmind/settings.py:308` |
| `DEFAULT_FROM_EMAIL` | valor (ya resuelto) de `EMAIL_HOST_USER` | Remitente por defecto. El fallback es al valor de la variable `EMAIL_HOST_USER`, no a la env var en sí | `retailmind/retailmind/settings.py:309` |

### IA / Anthropic / Langfuse

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `''` | API key de Claude para la app `assistant` (secreto) | `retailmind/retailmind/settings.py:573` |
| `LANGFUSE_SECRET_KEY` | `''` | Secret key de Langfuse para tracing/observabilidad (secreto) | `retailmind/retailmind/settings.py:577` |
| `LANGFUSE_PUBLIC_KEY` | `''` | Public key de Langfuse | `retailmind/retailmind/settings.py:578` |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Host de Langfuse | `retailmind/retailmind/settings.py:579` |

> Nota: la app `assistant` (`agent.py`, etc.) NO lee `os.environ` directamente (verificado: 0 referencias a `os.environ`/`getenv` en `retailmind/assistant/`); consume estos valores vía `django.conf.settings`.

### Cache / Redis

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `REDIS_URL` | `''` (vacío → usa LocMemCache) | URL de Redis para el cache `ventas` (compartido entre workers, invalidación por signals). Si vacío, fallback a LocMemCache | `retailmind/retailmind/settings.py:434` (usado en 460-470) |

### Seguridad / Django (SECRET / DEBUG / ALLOWED_HOSTS / CORS / sesiones)

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `SECRET_KEY` | `django-insecure-u6k00%6(...)` (clave dev hardcodeada) | SECRET_KEY de Django; también firma JWT (`SIMPLE_JWT.SIGNING_KEY = SECRET_KEY`). El mismo fallback de dev está duplicado en `.env` | `retailmind/retailmind/settings.py:30` (reusada en 541) |
| `DEBUG` | `False` (true si `.lower() == 'true'`) | Modo debug; afecta CORS (`CORS_ALLOW_ALL_ORIGINS`), storage de static, loaders de templates, nivel de logging, seguridad HSTS/cookies, `ENABLE_SILK` | `retailmind/retailmind/settings.py:33` |
| `ALLOWED_HOSTS` | `*` (split por comas) | Hosts permitidos. Se añaden además dominios fijos de DigitalOcean (`retail-ap-mh3y2.ondigitalocean.app`, `*.ondigitalocean.app`) y, si hay `RAILWAY_ENVIRONMENT`, los de Railway | `retailmind/retailmind/settings.py:36` |
| `SESSION_COOKIE_AGE` | `28800` (int, 8 h) | Vida máxima de la cookie de sesión | `retailmind/retailmind/settings.py:415` |
| `SESSION_EXPIRE_AT_BROWSER_CLOSE` | `True` (`.lower() == 'true'`) | Destruir sesión al cerrar navegador | `retailmind/retailmind/settings.py:416-418` |
| `SESSION_INACTIVITY_TIMEOUT` | `3600` (int, 1 h) | Inactividad máxima antes de cerrar sesión (usado por `SessionTimeoutMiddleware`) | `retailmind/retailmind/settings.py:421` |
| `SESSION_ACTIVITY_THROTTLE` | `300` (int) | Throttle de escritura del timestamp de actividad (evita UPDATE en cada AJAX del POS) | `retailmind/retailmind/settings.py:423` |

> CORS no tiene env vars propias salvo lo derivado de `DEBUG` y `RAILWAY_PUBLIC_DOMAIN` (ver Deploy). En no-DEBUG, los orígenes permitidos están hardcodeados (Tauri localhost, `retail.webappsolutions.cl`).

### Transbank / POS / Impresión (QZ Tray)

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `QZ_CERTIFICATE` | `''` | Certificado público QZ Tray (fallback cuando NO existe `certs/digital-certificate.txt`). Soporta `\n` literal | `retailmind/app/views.py:31508` |
| `QZ_PRIVATE_KEY` | `''` | Clave privada PEM para firmar peticiones de QZ Tray (fallback cuando NO existe `certs/private-key.pem`; secreto). Soporta `\n` literal | `retailmind/app/views.py:31550` |

> En el `.env` real, `QZ_PRIVATE_KEY` contiene un bloque `-----BEGIN CERTIFICATE-----` (un certificado embebido, NO una clave privada PEM); con ese valor `qz_firmar` fallaría al cargar la clave. La ruta de archivos es `BASE_DIR / 'retailmind' / 'certs' / ...` (`QZ_PRIVATE_KEY_PATH`, `QZ_CERTIFICATE_PATH`, settings.py:492-493).
> No se encontraron variables de entorno específicas de Transbank en el código (`transbank-*-service.py` no leen `os.environ`/`getenv`); la configuración de POS físico parece manejarse por otros medios (modelos/BD).

### Feature flags

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `POS_KIOSK_DEFAULT` | `True` (`.lower() == 'true'`) | Arranca todo el sistema en modo POS táctil (kiosk). Se puede sobreescribir por request con `?kiosk=0/1` | `retailmind/retailmind/settings.py:229` |
| `ENABLE_SILK` | `False` (`.lower() == 'true'`; además requiere `DEBUG=True`) | Activa el profiler `django-silk` (app + middleware) | `retailmind/retailmind/settings.py:75` |
| `ALLCONNECTED_WEBHOOK_FACTURA_ENABLED` | `''` (false; debe ser `'true'`, vía `.lower()`) | OBLIGATORIO para activar el webhook push de facturación hacia AllConnected (deshabilitado por default para no disparar webhooks en migraciones masivas de `Dte`). Leída con `os.environ` (no settings) | `retailmind/app/factura_notifier.py:74` |

### Integraciones externas (AllConnected / Vicent ecommerce)

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `RETAILMIND_API_KEY` | `''` | API key entrante para recibir pedidos desde VicentAllEcommercesConected | `retailmind/retailmind/settings.py:582` |
| `ALLCONNECTED_WEBHOOK_URL` | `''` (vacío = deshabilitado) | URL del webhook de AllConnected que recibe actualizaciones de stock | `retailmind/retailmind/settings.py:636` |
| `ALLCONNECTED_CANAL_ORIGEN_ID` | `0` (int) | ID del canal origen en AllConnected que representa a RetailMind | `retailmind/retailmind/settings.py:640` |
| `ALLCONNECTED_API_BASE_URL` | `''` (vacío = pull deshabilitado) | Base URL para CONSULTAR pedidos pendientes a AllConnected (botón "Traer pedidos") y para el webhook de factura. Lo consume `app/services/allconnected_pedidos_service.py` | `retailmind/retailmind/settings.py:649` |
| `ALLCONNECTED_API_KEY` | `''` | Key de autenticación saliente hacia AllConnected (secreto) | `retailmind/retailmind/settings.py:650` |
| `ALLCONNECTED_API_HEADER_NAME` | `X-AllConnected-Key` | Nombre del header de auth saliente | `retailmind/retailmind/settings.py:651` |
| `ALLCONNECTED_PEDIDOS_PATH` | `/app/pedidos/pendientes/` | Path del endpoint de pedidos pendientes en AllConnected | `retailmind/retailmind/settings.py:652` |
| `ALLCONNECTED_WEBHOOK_FACTURA_PATH` | `/system/webhooks/retailmind/factura/` | Path del webhook de factura en AllConnected. Leída con `os.environ` (no settings) | `retailmind/app/factura_notifier.py:83` |

### Deploy

| Variable | Default | Qué controla | Archivo |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `retailmind.settings` | Módulo de settings de Django (lo fijan/leen los entrypoints) | `retailmind/manage.py`, `retailmind/retailmind/wsgi.py`, `retailmind/retailmind/asgi.py` |
| `RAILWAY_ENVIRONMENT` | (solo se comprueba presencia con `in os.environ`) | Si está definida, activa lógica específica de Railway: añade hosts y CSRF trusted origins de Railway | `retailmind/retailmind/settings.py:43,268` |
| `RAILWAY_PUBLIC_DOMAIN` | `''` | Dominio público de Railway; se añade a `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` y `CORS_ALLOWED_ORIGINS` | `retailmind/retailmind/settings.py:44,270,599-600` |

### Notas de descubrimiento

- `.env.example` (`retailmind/.env.example`) solo documenta: `PG_*`, `MYSQL_*`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, y email (comentado). Está **desactualizado** respecto a settings.py (no menciona Anthropic, Langfuse, AllConnected, Redis, sesiones, QZ, etc.).
- `.env` real (`retailmind/.env`) contiene secretos productivos: password MySQL DigitalOcean (`dbHoldingTebes`, puerto `25060`), token MailerSend, `RETAILMIND_API_KEY`, `ALLCONNECTED_API_KEY`/`ALLCONNECTED_API_BASE_URL`, y un certificado embebido en `QZ_PRIVATE_KEY`. Define también `SESSION_COOKIE_AGE`/`SESSION_INACTIVITY_TIMEOUT`/`SESSION_EXPIRE_AT_BROWSER_CLOSE` y duplica el `SECRET_KEY` de dev. NO define `ANTHROPIC_API_KEY`, `LANGFUSE_*`, `POS_KIOSK_DEFAULT`, `ENABLE_SILK`, `QZ_CERTIFICATE` ni las demás `ALLCONNECTED_*` (header/path/canal/webhook), y `REDIS_URL` está comentada.
- La app `assistant` y los servicios de dominio NO leen `os.environ` directamente: consumen los valores vía `django.conf.settings`.

---

## 4. API interna y consumidores

El proyecto expone **dos superficies de API completamente distintas**, con autenticación y consumidores diferentes:

1. **API v1 versionada** (`/api/v1/...`) — montada en el `urls.py` raíz vía `include('app.api.urls', namespace='api_v1')`. Pensada para clientes programáticos (Tauri, app móvil de fidelización, app móvil de staff). Auth: JWT.
2. **API externa** (`/api/...`) — montada en el `urls.py` raíz vía `include('app.api.external.urls')`. Para integraciones de terceros. Auth: API key. (Nota: el mismo `app.api.external.urls` también queda incluido dentro de `app.api.urls` bajo `/api/v1/external/`, por lo que estos endpoints son alcanzables por ambos prefijos; el contrato documentado con AllConnected usa el prefijo raíz `/api/...`.)
3. **API "web"** (`/app/api/...` y `/assistant/api/...`) — cientos de endpoints AJAX FBV consumidos por jQuery dentro de los templates Django. Auth: SessionAuthentication (cookie de sesión web).

Default global DRF (`settings.REST_FRAMEWORK`, `settings.py:506-512`): `IsAuthenticated` + `JWTAuthentication` y `SessionAuthentication`. Cada vista de la API v1/externa sobrescribe esto según su consumidor.

### Montaje (`retailmind/urls.py`)
- `path('api/v1/', include('app.api.urls', namespace='api_v1'))` → desktop, mobile, cliente, sync, health (y `external/` re-incluido).
- `path('api/', include('app.api.external.urls'))` → integración externa AllConnected.
- `path('app/', include('app.urls'))` → todos los `api/...` de `app/urls.py` quedan bajo `/app/api/...`.
- `path('assistant/', include('assistant.urls'))` → `/assistant/api/...`.

---

### (a) Cliente desktop Tauri "NEXO POS" — `/api/v1/desktop/` + `/api/v1/sync/` + `/api/v1/health/`

Auth: login devuelve un **JWT de access** (vida real **12 h**, `SIMPLE_JWT.ACCESS_TOKEN_LIFETIME = timedelta(hours=12)`; la respuesta del login reporta además un campo `expires_at` de +7 días, que NO coincide con la vida real del token) + un **refresh token propio en BD** (`RefreshTokenDesktop`, hash en `models_sync.py`, con rotación, default **30 días**). Endpoints de config/fidelización/logout exigen además `IsAuthorizedDevice` (header **`X-Device-ID`** validado contra `DispositivoAutorizado`: debe estar registrado, activo y pertenecer al usuario autenticado). Los de sync usan solo `IsAuthenticated` (JWT) y resuelven sucursal por `sucursal_id` (query/body) → dispositivo (si estuviera seteado, lo cual no ocurre en sync) → `EmpresaUser`.

Las vistas son **APIView (CBV)** de DRF, no FBV.

Auth / config (`app/api/desktop/views.py`):
| Método | Path | Propósito | Auth |
|---|---|---|---|
| POST | `/api/v1/desktop/login/` | Login dispositivo → JWT + refresh + datos sucursal/vendedor | AllowAny (valida credenciales) |
| POST | `/api/v1/desktop/refresh/` | Rota refresh token, emite nuevo access | AllowAny (valida refresh) |
| POST | `/api/v1/desktop/logout/` | Revoca refresh tokens del dispositivo | JWT + IsAuthorizedDevice |
| POST | `/api/v1/desktop/sucursales/` | Sucursales disponibles para el user (pre-login, selector) | AllowAny (valida credenciales) |
| GET | `/api/v1/desktop/sucursal/` | Config completa de la sucursal del dispositivo | JWT + IsAuthorizedDevice |
| GET | `/api/v1/health/` | Health check (incluye chequeo de BD) | AllowAny |
| GET | `/api/v1/sync/status/` | Estado de sync por `X-Device-ID` (sin auth completa) | AllowAny |

Fidelización / Gift Cards desde el POS (`app/api/desktop/fidelizacion_views.py`) — solo consulta/validación; la acumulación real ocurre al sincronizar el ticket:
| Método | Path | Propósito | Auth |
|---|---|---|---|
| GET | `/api/v1/desktop/fidelizacion/saldo/?rut=` | Saldo de puntos del cliente por RUT (al cobrar) | JWT + IsAuthorizedDevice |
| POST | `/api/v1/desktop/giftcards/validar/` | Valida (sin descontar) que la gift card cubra un monto | JWT + IsAuthorizedDevice |
| GET | `/api/v1/desktop/giftcards/<codigo>/` | Estado y saldo de una gift card | JWT + IsAuthorizedDevice |

Sincronización bidireccional (`app/api/sync/views.py`) — todas `IsAuthenticated` (JWT). Nota: aunque `IsSameSucursal` y `CanSyncTickets` están definidos en `app/api/desktop/permissions.py` y se importan en este módulo, NO se aplican en estas vistas (solo usan `IsAuthenticated`):
| Método | Path | Propósito |
|---|---|---|
| GET | `/api/v1/sync/productos/` | Descarga productos (incremental `since`, paginado, filtra por sucursal) |
| GET | `/api/v1/sync/categorias/` | Descarga categorías |
| GET | `/api/v1/sync/vendedores/` | Descarga vendedores activos de la sucursal |
| GET | `/api/v1/sync/configuracion/` | Config sucursal + empresa + correlativos DTE |
| POST | `/api/v1/sync/tickets/` | Sube tickets (idempotente por `local_id`, valida stock, asigna correlativos) |
| POST | `/api/v1/sync/cuadraturas/` | Sube cierres de caja + movimientos |
| POST | `/api/v1/sync/movimientos-caja/` | Stub — responde indicando usar `/cuadraturas/` (no redirige; devuelve 200 con mensaje) |
| GET | `/api/v1/sync/status/detail/` | Estado detallado de sync (logs, tickets pendientes) |

### (b) App móvil de fidelización (cliente final) — `/api/v1/cliente/`

Auth: **JWT propio de cliente** vía `ClienteJWTAuthentication` (`app/api/cliente/authentication.py`). El subject NO es `users.Usuario` sino una `CuentaClienteApp` (token con claim `tipo='cliente'`, `cliente_id`, sin `user_id`). Login por **RUT + OTP** (no password). Las vistas protegidas fijan `authentication_classes=[ClienteJWTAuthentication]` (quita SessionAuth → sin CSRF) + `IsClienteApp` (`app/api/cliente/permissions.py`). Aislamiento estricto staff↔cliente. Throttling por scope (`settings.py:516-520`: `otp_solicitar` 5/h, `otp_verificar` 10/h, `vincular_cliente` 5/h; los endpoints aplican `ScopedRateThrottle`).

`app/api/cliente/views.py`:
| Método | Path | Propósito | Auth |
|---|---|---|---|
| POST | `/api/v1/cliente/auth/solicitar-otp/` | Envía OTP (respuesta genérica anti-enumeración; crea la `CuentaClienteApp` de un Cliente existente si falta, NO crea clientes nuevos) | AllowAny + throttle `otp_solicitar` |
| POST | `/api/v1/cliente/auth/vincular/` | Subclase de solicitar-otp para primer login (reclamar cuenta) | AllowAny + throttle `vincular_cliente` |
| POST | `/api/v1/cliente/auth/verificar-otp/` | Verifica OTP → access + refresh + cliente | AllowAny + throttle `otp_verificar` |
| POST | `/api/v1/cliente/auth/refresh/` | Rota refresh de cliente | AllowAny |
| POST | `/api/v1/cliente/auth/logout/` | Revoca familia del refresh token | Cliente-JWT + IsClienteApp |
| GET | `/api/v1/cliente/puntos/saldo/` | Saldo, valor en pesos y puntos por vencer | Cliente-JWT + IsClienteApp |
| GET | `/api/v1/cliente/puntos/movimientos/` | Historial de movimientos de puntos (paginado) | Cliente-JWT + IsClienteApp |
| GET | `/api/v1/cliente/giftcards/` | Gift cards del cliente | Cliente-JWT + IsClienteApp |
| GET/PATCH | `/api/v1/cliente/perfil/` | Ver/editar email-celular (resetea verificación de canal) | Cliente-JWT + IsClienteApp |
| GET | `/api/v1/cliente/carnet/` | RUT + payload QR para identificar al cliente en caja | Cliente-JWT + IsClienteApp |

**App móvil de staff (operación)** — `/api/v1/mobile/` (`app/api/mobile/views.py`). Cliente DISTINTO de la app de fidelización. Auth: **JWT estándar** (`JWTAuthentication` + `IsAuthenticated`, el subject SÍ es `users.Usuario`):
| Método | Path | Propósito | Auth |
|---|---|---|---|
| GET | `/api/v1/mobile/codigo-autorizacion/actual/` | Código de autorización dinámico vigente (la vista exige rol administrador/jefe_local) | JWT staff |
| POST | `/api/v1/mobile/ajuste-stock-rapido/` | Ajuste rápido de stock por SKU + concepto | JWT staff |

### (c) Integraciones externas — `/api/...` (contrato v1 "AllConnected" / VicentAllEcommercesConected)

Auth: **API key** vía `ApiKeyAuthentication` + `ApiKeyPermission` (`app/api/external/authentication.py`). Acepta `Authorization: Bearer {key}` (contrato oficial) o `X-Api-Key: {key}` (legacy); ambas comparadas contra `settings.RETAILMIND_API_KEY` (env `RETAILMIND_API_KEY`, default `''` — `settings.py:582`). El `request.user` es `AnonymousUser`. Filtrado por `rut_empresa`. `/api/skus/` cachea 15 min (900 s) con anti-stampede (lock 120 s + copia stale 1 h / 3600 s). Vistas son APIView (CBV).

`app/api/external/views.py`:
| Método | Path | Propósito | Auth |
|---|---|---|---|
| GET | `/api/skus/?rut_empresa=` | Catálogo completo agrupado por producto con tallas + stock por sucursal (1 fila = 1 producto Shopify) | API key |
| GET | `/api/articulos/<articulo_codigo>/tallas/?rut_empresa=` | Refresca un artículo puntual (mismas agrupación/tallas) | API key |
| GET | `/api/stock/movimientos/?rut_empresa=[&fecha_desde=]` | Snapshot/incremental de stock por SKU×sucursal (formato plano) | API key |
| GET/POST | `/api/stock/por-skus/?rut_empresa=&skus=` | Stock por SKU×sucursal con `sucursal_id` (POST para listas grandes) | API key |
| GET/POST | `/api/stock/global/?rut_empresa=&skus=` | Stock TOTAL por SKU sumando todas las sucursales (POST para listas grandes) | API key |
| GET | `/api/guias-talla/?rut_empresa=` | Guías de talla + items de conversión + artículos vinculados | API key |
| GET | `/api/sucursales/?rut_empresa=` | Sucursales activas de la empresa (para decidir destino de pedidos) | API key |
| GET | `/api/precios-actuales/?rut_empresa=` | Precios/costos/antigüedad FIFO por SKU (`dias_sin_venta` = driver de descuento) | API key |
| GET | `/api/novedades/` | Novedades (productos nuevos) | API key |
| GET | `/api/movimientos-ventas/` | Líneas de DTE de venta (reemplaza API legacy HoldingTebes; pantalla de devoluciones) | API key |
| GET | `/api/ventas/` | Documentos de venta a nivel documento (conciliación diaria) | API key |
| GET | `/api/health/` | Health check externo | AllowAny |

### (d) Sincronización — ver bloque (a)

Toda la sincronización (`/api/v1/sync/...`) es consumida por el desktop Tauri y está documentada en la tabla de la sección (a). No hay otros consumidores de `/sync/`.

### (e) Frontend web POS / módulos — `/app/api/...` y `/assistant/api/...`

Auth: **SessionAuthentication** (cookie de sesión Django + CSRF) por default DRF; varias son FBV puras con `@login_required`. Son **FBV** consumidos por jQuery/vanilla JS dentro de los templates. Hay **248 rutas `api/...` en `app/urls.py`** (sin `app_name`, importadas planas) — no son una API "de cliente externo" sino el backend AJAX de cada módulo web. No se listan todas (no se inspeccionó decorador por decorador; el conteo y la agrupación salen de grep sobre `app/urls.py`). Resumen por familia (conteo de paths):

- `cuadratura` (21), `ventas` (20), `creditos` (17), `reportes` (16), `arqueo` (16), `requerimientos` (15), `prediccion` (15), `cotizaciones` (12), `giftcards` (8), `fidelizacion` (7), `dte` (6), `tickets` (5), `compra(s)` (5), `ecommerce` (4), `despacho` (4), `precios-costos` (3), `curvas-distribucion` (3).
- Endpoints utilitarios sueltos: `validar-rut`, `validar-password`, `verificar-disponibilidad-historico`, `trazabilidad-producto`, `sucursales`/`sucursales-usuario`/`obtener-sucursales`, `productos`/`productos-sucursal`, `opciones-atributo`, `resumen-existencias`, y una larga lista de `reporte-*`.
- Exportación/importación (CSV/Excel) en `views_modulo_compras.py`.
- Dashboards.

Asistente conversacional (`assistant/urls.py`, prefijo `/assistant/`): `api/chat/`, `api/feedback/`, `api/history/`, `api/new/`, `api/stats/` — FBV con SessionAuth, consumidos por el chat web con Claude.

También en el `urls.py` raíz (SessionAuth, frontend web): `api/check-session/`, `api/check-login-method/`.

---

### Resumen por consumidor

| Consumidor | Prefijo | Auth | Header(s) clave |
|---|---|---|---|
| Desktop Tauri NEXO POS | `/api/v1/desktop/`, `/api/v1/sync/`, `/api/v1/health/` | JWT (access 12 h) + IsAuthorizedDevice (refresh BD 30 días) | `X-Device-ID`, `X-App-Version`, `X-Sucursal-ID` |
| App móvil fidelización (cliente final) | `/api/v1/cliente/` | JWT de cliente (RUT+OTP, `ClienteJWTAuthentication`) | Authorization Bearer |
| App móvil staff | `/api/v1/mobile/` | JWT estándar (`users.Usuario`) | Authorization Bearer |
| Integración externa AllConnected | `/api/` (skus, stock, precios, ventas…) | API key (`RETAILMIND_API_KEY`) | `Authorization: Bearer` o `X-Api-Key` |
| Frontend web (módulos + asistente) | `/app/api/...`, `/assistant/api/...` | SessionAuthentication + CSRF | cookie de sesión |

Los headers CORS custom permitidos (`x-device-id`, `x-app-version`, `x-sucursal-id`, `x-request-timestamp`, `x-api-key`, en `settings.py:613-628`) corresponden: los tres primeros al cliente desktop Tauri (`x-device-id` validado por `IsAuthorizedDevice`; `x-app-version` por `IsMinimumAppVersion` y `x-sucursal-id` por `IsSameSucursal`, ambos disponibles pero NO enganchados en las vistas de sync/desktop revisadas), `x-api-key` a la integración externa, y `x-request-timestamp` permitido en CORS pero sin código que lo valide en las vistas inspeccionadas (reservado para clientes programáticos por inferencia).

---

## 5. App móvil de fidelización (AppFidelizar)

Backend REST que consume la app móvil del cliente final. Todo cuelga de `/api/v1/cliente/` y delega en dos servicios function-based: `cliente_app_service` (auth/OTP/tokens) y `fidelizacion_service` (puntos/saldo). Las gift cards se leen directo del modelo `GiftCard`.

> Nota de nombre: el prompt llama a la app "AppFidelizar"; en el código (comentarios de `app/models/cliente_app.py` y `fidelizacion.py`) se la menciona como app Flutter "Paola" / `puntos.realsport.cl`. Se asume que son la misma app. Este documento cubre solo el backend; el cliente Flutter no se revisó.

**Montaje de rutas (confirmado):**
- `retailmind/urls.py:38` → `path('api/v1/', include('app.api.urls', namespace='api_v1'))`
- `app/api/urls.py:30` → `path('cliente/', include('app.api.cliente.urls', namespace='cliente'))`
- `app/api/cliente/urls.py` declara `app_name = 'cliente'`.
- Prefijo final efectivo: **`/api/v1/cliente/`**

---

### 5.1 Autenticación (modelo, registro, login, tokens)

**Login SIN contraseña: RUT + OTP de un solo uso.** No hay password ni device-id ni API key para esta app (esos headers — `x-device-id`, `x-api-key` — existen en CORS para el cliente desktop Tauri, NO para AppFidelizar).

**Identidad / modelo de usuario:**
- El "usuario" de la app es un `Cliente` del CRM (`app/models/crm.py`), NO el `AUTH_USER_MODEL` (`users.Usuario`). La app **solo vincula clientes que ya existen** en el CRM (creados en caja/POS); **no crea clientes nuevos**.
- El estado de la app vive aislado en `CuentaClienteApp` (OneToOne con `Cliente`), que actúa como `request.user` (expone `is_authenticated`/`is_active`/`is_anonymous`).

**Tokens (emitidos en `cliente_app_service.emitir_tokens`):**
- **access**: `AccessToken` de SimpleJWT con claims propios `tipo='cliente'`, `cliente_id`, `cuenta_app_id`, y **SIN `user_id`**. Vida: `ACCESS_TOKEN_LIFETIME = 12 horas` (compartido en `settings.SIMPLE_JWT`).
- **refresh**: UUID opaco (no es JWT). Se guarda solo el hash SHA256 en `RefreshTokenClienteApp` (campo `token_hash`, `unique`). Vida: `REFRESH_TOKEN_LIFETIME = 7 días`. Rotación con detección de reúso: reusar un refresh ya usado revoca toda la familia (`familia_id`).
- Header de auth: `Authorization: Bearer <access>` (`AUTH_HEADER_TYPES=('Bearer',)`).

**Clase de auth:** `app/api/cliente/authentication.py::ClienteJWTAuthentication` (hereda de `JWTAuthentication`). Sobrescribe `get_user`: exige `tipo=='cliente'`, resuelve `CuentaClienteApp` por `cliente_id` (con `activa=True` y `cliente.activo`), y nunca toca `users.Usuario`.

**Permiso:** `app/api/cliente/permissions.py::IsClienteApp` exige que `request.user` sea instancia de `CuentaClienteApp`.

**Aislamiento staff ↔ cliente (verificado en tests):**
- Token de cliente contra endpoint staff → falla (no tiene `user_id`).
- Token de staff contra endpoint de cliente → `ClienteJWTAuthentication` lo rechaza por `tipo != 'cliente'` → 401 (`app/tests/test_cliente_app.py::AislamientoTokensTest.test_token_staff_no_accede_a_endpoints_cliente`).

**Anti-abuso:**
- Anti-enumeración de RUT: `solicitar-otp`/`vincular` SIEMPRE responden el mismo mensaje genérico, exista o no el RUT, tenga o no canal (no se filtra existencia).
- Throttling por scope (`ScopedRateThrottle`, en `settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`): `otp_solicitar=5/hour`, `otp_verificar=10/hour`, `vincular_cliente=5/hour`.
- Bloqueo de cuenta por fuerza bruta: `MAX_LOGIN_FALLIDOS=5` fallos → bloqueo `BLOQUEO_LOGIN_MINUTOS=15`.
- OTP: 6 dígitos, hash SHA256 en BD, un solo uso, vence en `OTP_EXPIRACION_MINUTOS=10`, máx `MAX_INTENTOS_OTP=5` intentos por código. Crear un OTP nuevo invalida los anteriores.

---

### 5.2 Endpoints (método, path, payload, respuesta)

Todos los paths van prefijados con `/api/v1/cliente/`. Las respuestas siempre llevan `success: true|false`.

**AUTH (sin token):**

| Método | Path | Payload | Respuesta OK |
|---|---|---|---|
| POST | `auth/solicitar-otp/` | `{ "rut": "12.345.678-9", "canal": "EMAIL" }` (`canal` ∈ `EMAIL`/`SMS`, default `EMAIL`, opcional) | `{ "success": true, "mensaje": "Si el RUT está registrado, te enviamos un código de acceso." }` (200 siempre, anti-enumeración). 400 solo si el RUT es inválido. |
| POST | `auth/vincular/` | igual que solicitar-otp | igual (es subclase `VincularCuentaView(SolicitarOTPView)`; mismo handler, distinto `throttle_scope='vincular_cliente'`). Pensado para el primer login. |
| POST | `auth/verificar-otp/` | `{ "rut": "12.345.678-9", "codigo": "123456" }` (`codigo` exactamente 6 chars) | `{ "success": true, "access": "...", "refresh": "...", "expires_at": "ISO8601", "cliente": { "id", "nombre_completo", "rut", "email" } }`. Error: 400 `{ "success": false, "error": "Código inválido o expirado." }` |
| POST | `auth/refresh/` | `{ "refresh": "<token>" }` | `{ "success": true, "access": "...", "refresh": "<nuevo>", "expires_at": "ISO8601" }`. Error: 401 `{ "success": false, "error": "..." }` (token inválido/revocado/expirado/reúso) |
| POST | `auth/logout/` (requiere token) | `{ "refresh": "<token>" }` (opcional) | `{ "success": true }`. Revoca toda la familia del refresh. |

**DATOS DEL CLIENTE (requieren `Authorization: Bearer <access>`):**

| Método | Path | Payload | Respuesta OK |
|---|---|---|---|
| GET | `puntos/saldo/` | — | `{ "success": true, "cliente": "<nombre>", "saldo_puntos": int, "valor_pesos": int, "puntos_por_vencer": int }` (vía `fidelizacion_service.consultar_saldo`; `puntos_por_vencer` = lotes que vencen en los próximos 30 días) |
| GET | `puntos/movimientos/?page=1&page_size=20` | — | Paginado (PageNumberPagination, `page_size` default 20, máx 100): `{ "success": true, "count": int, "next": url|null, "previous": url|null, "results": [ { "tipo", "tipo_display", "puntos", "saldo_resultante", "fecha", "fecha_expiracion", "observaciones" } ] }`. Si no hay cuenta de puntos: `{ "success": true, "count": 0, "results": [] }` |
| GET | `giftcards/` | — | `{ "success": true, "results": [ { "codigo", "saldo_actual", "estado", "estado_display", "fecha_vencimiento", "esta_vencida" } ] }` (NO expone `pin`/`saldo_inicial`/auditoría) |
| GET | `perfil/` | — | `{ "success": true, "perfil": { "id", "nombre", "apellido", "nombre_completo", "rut", "email", "celular", "tipo_cliente" } }` |
| PATCH | `perfil/` | `{ "email": "...", "celular": "..." }` (solo estos dos son editables) | `{ "success": true, "perfil": {...} }`. Cambiar email/celular resetea `email_verificado`/`celular_verificado` en la `CuentaClienteApp`. |
| GET | `carnet/` | — | `{ "success": true, "rut": "111111111", "rut_formateado": "11.111.111-1", "nombre_completo": "...", "qr_payload": "111111111" }` (RUT normalizado para que la app dibuje QR/código de barras; el POS identifica al cliente por RUT) |

Endpoints de datos sin token → 401.

---

### 5.3 Modelo de datos

**`app/models/cliente_app.py`:**
- **`CuentaClienteApp`** — OneToOne con `Cliente` (`related_name='cuenta_app'`). Campos: `email_verificado`, `celular_verificado`, `activa` (indexado), `ultimo_login`, `fecha_alta`, `updated_at`, `intentos_fallidos`, `bloqueada_hasta`. Métodos: `is_authenticated/is_anonymous/is_active`, `esta_bloqueada()`, `registrar_login()`, `registrar_fallo_login()`. Es el `request.user`.
- **`CodigoOTPCliente`** — PK UUID. FK a `Cliente` (`related_name='otps_app'`). Campos: `codigo_hash` (SHA256, indexado), `canal` (`EMAIL`/`SMS`), `destino`, `expires_at`, `intentos`, `usado`, `used_at`, `ip_address`, `created_at`. Classmethods: `crear_para_cliente()` (invalida vigentes y devuelve `(obj, codigo_plano)`), `validar()` → `(ok, error)`. El código en claro nunca se persiste.
- **`RefreshTokenClienteApp`** — PK UUID. FK a `CuentaClienteApp` (`related_name='refresh_tokens'`). Campos: `token_hash` (SHA256, unique), `familia_id`, `revocado`, `utilizado`, `created_at`, `expires_at`, `used_at`, `ip_address`, `user_agent`. Classmethods: `crear_token()`, `validar_y_rotar()` (rota + detecta reúso → revoca familia), `revocar_familia()`. Calcado de `RefreshTokenDesktop`.

**Relación con fidelización (`app/models/fidelizacion.py`) — modelos separados, ya existentes:**
- **`CuentaPuntos`** — OneToOne con `Cliente` (`related_name='cuenta_puntos'`). `saldo_puntos` (cache denormalizado), `valor_en_pesos()`. Puntos GLOBALES en toda la cadena (una sola bolsa por cliente).
- **`MovimientoPuntos`** — ledger inmutable FK a `CuentaPuntos`. Tipos: `ACUMULACION`, `CANJE`, `EXPIRACION`, `AJUSTE`, `REVERSA`, `BIENVENIDA`. Los ACUMULACION/BIENVENIDA son lotes con `fecha_expiracion` consumidos FIFO. `idempotency_key` unique.
- **`ProgramaFidelizacion`** — config (1 activo): `valor_punto_en_pesos` (default 10), `minimo_canje_puntos` (default 50), `puntos_bienvenida` (default 20), `vigencia_dias` (default 365), regla `calcular_puntos()`.
- **`GiftCard`** (`app/models/giftcards.py`) — FK a `Cliente` (`related_name='giftcards'`, `on_delete=SET_NULL`, opcional). La app expone `codigo`, `saldo_actual`, `estado` (`ACTIVA`/`AGOTADA`/`ANULADA`/`VENCIDA`/`BLOQUEADA`), `fecha_vencimiento`, `esta_vencida`.

**Vínculo clave:** `CuentaClienteApp` y `CuentaPuntos` son **dos OneToOne distintos sobre el mismo `Cliente`** (no se relacionan entre sí; se unen a través de `Cliente`). La auth produce un `request.user` = `CuentaClienteApp`; los endpoints de puntos hacen `request.user.cliente` → `cliente.cuenta_puntos`.

---

### 5.4 Flujos clave

1. **Registro / vinculación (primer login):** la app NO registra clientes. Llama `POST auth/vincular/` (o `solicitar-otp/`) con el RUT. Si el `Cliente` existe en el CRM, el backend crea implícitamente su `CuentaClienteApp` (`get_or_create_cuenta_app`) y envía el OTP. Respuesta genérica siempre. Si el RUT no existe o no tiene email/celular, igual responde genérico y no envía nada. El cliente debe haberse registrado antes en tienda/POS (alta vía `fidelizacion_service.registrar_cliente_manual` o el flujo de caja).

2. **Login:** `solicitar-otp/` → llega OTP por email (asunto "Tu código de acceso", vence en 10 min) → `verificar-otp/` con RUT + código → devuelve `access`/`refresh`. Al verificar se marca verificado el canal usado (`email_verificado`/`celular_verificado`, vía `_marcar_canal_verificado`) y se resetea el contador de fallidos (`registrar_login`).

3. **Sesión:** access dura 12h; cuando vence, `auth/refresh/` rota el refresh (devuelve uno nuevo, el anterior queda usado). Reusar un refresh ya rotado invalida toda la familia (sesión cerrada por seguridad → 401).

4. **Consulta de saldo/puntos:** `GET puntos/saldo/` (saldo, valor en pesos, por vencer en 30 días) y `GET puntos/movimientos/` (historial paginado del ledger, ordenado por `-fecha`).

5. **Carnet (identificación en caja):** `GET carnet/` devuelve el RUT normalizado como `qr_payload`. La app dibuja el QR/código de barras; el cajero/POS escanea y resuelve al cliente por RUT. La **acumulación de puntos NO ocurre vía esta API** — la dispara el hook de cobro del POS (`fidelizacion_service.acumular_puntos_por_venta`) al pagar un ticket con `cliente_rut`.

6. **Canje:** NO hay endpoint de canje en AppFidelizar (no aparece en `app/api/cliente/urls.py`). `fidelizacion_service.canjear_puntos()` existe pero se invoca desde el POS/desktop (canje como descuento en la venta presencial), no desde la app móvil. La app es de consulta + identificación.

7. **Perfil:** `GET/PATCH perfil/`. Solo `email`/`celular` editables (validados con `fidelizacion_service.validar_email`/`normalizar_celular`); al cambiarlos se invalida la verificación del canal.

8. **Logout:** `POST auth/logout/` con el refresh → revoca la familia (`cliente_app_service.logout`).

---

### 5.5 Configuración / env necesaria

Todo en `retailmind/settings.py`:
- **`SIMPLE_JWT`** (compartido con desktop): `ACCESS_TOKEN_LIFETIME=12h`, `REFRESH_TOKEN_LIFETIME=7d`, `ALGORITHM=HS256`, `SIGNING_KEY=SECRET_KEY`, `ISSUER='retailmind'`. (El refresh de cliente es UUID opaco propio en `RefreshTokenClienteApp`, no usa el blacklist de SimpleJWT — `ROTATE_REFRESH_TOKENS`/`BLACKLIST_AFTER_ROTATION` solo aplican al flujo de refresh JWT del desktop.)
- **Tunables de OTP/bloqueo** (hardcoded en settings con default, sobreescribibles): `OTP_EXPIRACION_MINUTOS=10`, `MAX_INTENTOS_OTP=5`, `MAX_LOGIN_FALLIDOS=5`, `BLOQUEO_LOGIN_MINUTOS=15`.
- **Throttle rates** en `REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`: `otp_solicitar=5/hour`, `otp_verificar=10/hour`, `vincular_cliente=5/hour`. (Los tests sobreescriben estos tres a `1000/hour` vía `@override_settings`.)
- **Email (envío de OTP):** SMTP vía env — `EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend'`, `EMAIL_HOST` (default `smtp.gmail.com`), `EMAIL_PORT` (default `587`), `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` (default = `EMAIL_HOST_USER`). El OTP se manda con `send_mail` (`fail_silently=False`, capturado en `enviar_otp_email`).
- **SMS:** NO implementado. `cliente_app_service.enviar_otp_sms()` es un stub que solo loguea y devuelve `False`; pasar `canal=SMS` registra el OTP en BD pero no lo envía. Punto de inserción para Twilio/Redvoiss vía `os.environ`.
- **CORS:** `x-device-id`/`x-api-key` en `CORS_ALLOW_HEADERS` son para el desktop Tauri, no para AppFidelizar (que solo usa `Authorization: Bearer`). Una app nativa con Bearer no manda `Origin`, así que no requiere CORS; no se validó en runtime una configuración específica para AppFidelizar (la config de CORS está orientada a web/Railway).
- **DB:** requiere aplicar las migraciones que crean `CuentaClienteApp`/`CodigoOTPCliente`/`RefreshTokenClienteApp` (no verificado: número/orden de migración ni si están aplicadas; la memoria del proyecto menciona migraciones de Fidelización+GiftCards 0164/0165 sin aplicar).

---

## 6. Datos, almacenamiento e infraestructura

### 6.1 Base de datos

- **Motor:** PostgreSQL (único motor). Configurado en `retailmind/retailmind/settings.py` (líneas 149-170).
- **Producción:** si existe la env var `DATABASE_URL`, se usa `dj_database_url.config(...)` con `conn_max_age=600` y `conn_health_checks=True` (conexiones persistentes con health-check). Esta es la ruta para Railway y DigitalOcean.
- **Local:** si no hay `DATABASE_URL`, se arma el `DATABASES['default']` con `ENGINE=django.db.backends.postgresql` y las vars `PG_DATABASE` (def. `retailmind`), `PG_USER` (def. `postgres`), `PG_PASSWORD` (def. `admin`), `PG_HOST` (def. `localhost`), `PG_PORT` (def. `5432`).
- **Driver:** `psycopg2-binary==2.9.10` (presente en `requirements.txt` raíz, `requirements-railway.txt` y el set extendido `retailmind/requirements.txt`). El set extendido además trae `psycopg2==2.9.10` (compilado) junto al binary.
- **MySQL legacy:** `mysql-connector-python==9.5.0` se usa solo para la migración legacy (`migrate_from_laravel.py`, servidor MySQL remoto `holdingtebes.cl`); NO es una BD de runtime. Las vars `MYSQL_*` solo las consume ese comando (confirmado por comentario en settings, líneas 172-173). Ojo: aunque su uso es solo de migración, el paquete **sí se instala en el deploy por Dockerfile** porque figura en `requirements-railway.txt` (y también en `requirements-dev.txt`).
- **Nota operativa (memoria del proyecto):** el `.env` local tiene `DATABASE_URL` apuntando a la BD de producción (DigitalOcean), por lo que `migrate`/`test`/`runserver` golpean producción si se corren a ciegas.

### 6.2 Archivos estáticos

- **Servidor:** WhiteNoise (`whitenoise==6.5.0`), `WhiteNoiseMiddleware` ubicado justo después de `SecurityMiddleware` (settings 81-82).
- **Storage backend** (settings 249-256):
  - `DEBUG=True` → `whitenoise.storage.CompressedStaticFilesStorage` (sin fingerprint).
  - `DEBUG=False` → `whitenoise.storage.CompressedManifestStaticFilesStorage` con `WHITENOISE_MANIFEST_STRICT=False`, `WHITENOISE_MAX_AGE=1 año` (`60*60*24*365`), `WHITENOISE_USE_FINDERS=False`. Esto exige `collectstatic` previo para generar `staticfiles.json`; sin él, TODOS los `{% static %}` se rompen (advertido en el código y en CLAUDE.md). `WHITENOISE_AUTOREFRESH=DEBUG`.
- **Rutas:** `STATIC_URL='/static/'`, `STATICFILES_DIRS=[BASE_DIR/'app'/'static']`, `STATIC_ROOT=BASE_DIR/'staticfiles'`.
- **collectstatic en deploy:** el `Dockerfile` lo corre en build (`collectstatic --noinput --ignore="*.map"`); `nixpacks.toml` también lo corre en `phases.build` (`collectstatic --noinput`).
- En `DEBUG` runserver sirve static desde `STATIC_ROOT` vía `static()` (urls.py 52-53).

### 6.3 Media / imágenes

- **Almacenamiento: filesystem local, NO hay servicio externo** (no se usa S3/boto3, django-storages, Cloudinary ni DO Spaces en código).
- `MEDIA_URL='/media/'`, `MEDIA_ROOT=BASE_DIR/'media'` (settings 486-487).
- **Servido por Django siempre** (no solo en DEBUG): `urls.py` 44-49 registra `re_path(r'^media/(?P<path>.*)$', serve, {document_root: MEDIA_ROOT})` tanto en dev como en prod. (Esto significa que los media viven en el disco efímero del contenedor en Railway/DO — no hay volumen persistente declarado en los archivos de deploy revisados.)
- **Campos que escriben media** (`upload_to`):
  - `users.Usuario.foto_perfil` → función `user_profile_photo_path` que retorna `usuarios/fotos_perfil/perfil_<id>_<timestamp>.<ext>` (users/models.py 8-12, 37).
  - `app.models.cotizaciones` `archivo_pdf` → `cotizaciones/pdfs/` (FileField PDF, cotizaciones.py 162).
  - `app.models.compras` `evidencia_foto` → `evidencias_problemas/` (compras.py 355).
  - `app.models.requerimientos.imagen` → `requerimientos/fotos/%Y/%m/%d/` (requerimientos.py 467; se borra vía `default_storage` en `views_modulo_requerimientos.py`).
  - `app.models.caja` `imagen_comprobante` → `comprobantes_bancarios/` (caja.py 451 y 538).
  - DTE/NC TXT escritos directo a `MEDIA_ROOT/documentos_electronicos/nc` en `views.py` (líneas 2882, 5181, 5954).
- **Imágenes de producto (catálogo/ecommerce): NO se almacenan localmente.** El servicio `app/services/realsport_imagenes_service.py` sincroniza URLs de portada remotas (ecommerces externos tipo realsport.cl) y las guarda como **URL** en `FotoPortadaArticulo.url_foto` (`URLField`), no como archivo. Las credenciales viven en la tabla `CredencialesEcommerce` (`app/models/configuracion.py`), NO en env. El resolver de portadas (`resolver_foto_portada_url` / `resolver_fotos_portada_bulk`) cachea las URLs en el cache `default` con TTL 1h (`CACHE_TTL_RESOLUCION=3600`).
- Comando `optimizar_imagenes_productos` opera sobre subcarpetas de `MEDIA_ROOT` (por defecto `productos`).

### 6.4 Cache

- **Backend por defecto: LocMemCache por proceso** (no compartido entre workers). Definidos en settings 436-480:
  - `default` (LocMem, `LOCATION='retailmind-default'`, TTL 300s, MAX_ENTRIES 2000) — usado por `@cache_page`, `@vary_on_cookie` y el resolver de portadas.
  - `catalogo` (LocMem, `LOCATION='retailmind-catalogo'`, TTL 900s, MAX_ENTRIES 5000) — catálogo de productos, invalidación manual.
  - `ventas` (TTL 60s) — dashboards/indicadores.
- **Redis opcional:** solo el cache `ventas` usa Redis (`django_redis.cache.RedisCache`) **si** existe la env var `REDIS_URL` (leída y `.strip()` en settings 434); si no, cae a LocMemCache (`LOCATION='retailmind-ventas'`). Opciones del backend Redis: `CLIENT_CLASS='django_redis.client.DefaultClient'`, `IGNORE_EXCEPTIONS=True`, `KEY_PREFIX='rm:ventas'`. Además `DJANGO_REDIS_IGNORE_EXCEPTIONS=True` global (degrada a None en caída de red, no crashea).
- `django-redis>=6.0.0` y `redis>=5.0.0` están en el set extendido `retailmind/requirements.txt`, **pero NO en `requirements.txt` raíz ni en `requirements-railway.txt`**, así que el deploy por Dockerfile no trae el cliente Redis. **Riesgo confirmado:** si en ese deploy se setea `REDIS_URL` sin instalar el cliente, el backend `RedisCache` fallaría al importar `django_redis`.

### 6.5 Tareas asíncronas (Celery / Flower)

- **No hay Celery operativo.** No existe `celery.py`, ni `CELERY_*`/broker/result-backend en settings, ni `@shared_task`/`.delay()`/`apply_async()` en ningún `.py` del proyecto (grep en todo el árbol no encontró coincidencias).
- `celery==5.4.0` y `flower==2.0.1` figuran **solo** en el set extendido `retailmind/requirements.txt` (no en `requirements.txt` raíz ni en `requirements-railway.txt`).
- La única mención es un docstring en `app/management/commands/expirar_puntos.py` línea 8 ("Pensado para correr a diario por cron / Celery beat").
- **Conclusión:** el trabajo batch/periódico se hace vía **management commands disparados por cron**, no por un worker Celery vivo. Comandos batch típicos: `calcular_predicciones`, `evaluar_alertas_pendientes`, `generar_notificaciones_dte`, `expirar_puntos`, `optimizar_imagenes_productos`, sincronización de portadas, reconciliaciones.

### 6.6 Logging

- Configurado en `settings.LOGGING` (335-405). Nivel dinámico: `DEBUG` si `DEBUG=True`, si no `WARNING` (`_APP_LOG_LEVEL`, línea 333 — para no escribir INFO en cada AJAX del POS).
- **Handlers:**
  - `console` (StreamHandler, formato verbose).
  - `file_app` → `BASE_DIR/logs/app.log`, RotatingFileHandler 5MB × 5 backups.
  - `file_errors` → `BASE_DIR/logs/errors.log`, RotatingFileHandler 5MB × 10 backups, nivel ERROR.
- **Loggers de app** (todos con `console`+`file_app`+`file_errors`, `propagate=False`): `app`, `users`, `empresa_management`, `assistant`.
- `django` (WARNING) y `django.request` (ERROR) a `console`+`file_errors` (ambos `propagate=False`). `root` en WARNING con `console`+`file_errors`.
- El directorio `logs/` se crea en import de settings si no existe (líneas 408-411).
- **Nota infra:** los logs van a disco del contenedor (efímero en Railway/DO); en prod la observabilidad real es vía dashboard de la plataforma y `--log-file -` de gunicorn (stdout).

### 6.7 Deploy

- **Dos rutas de build coexisten:**
  - **Dockerfile** (`/Dockerfile`, raíz): `python:3.11-slim`, `ENV TZ=America/Santiago`, instala libs de sistema (build-essential, pkg-config, `default-libmysqlclient-dev`, libffi, `libpq-dev`, zlib, `libjpeg-dev`, libfreetype, lcms2, libwebp, tcl/tk + python3-tk), instala **`requirements-railway.txt`**, corre `collectstatic` en build, arranca `gunicorn retailmind.wsgi --bind 0.0.0.0:8000 --log-file -`. El `railway.json` de la raíz usa `"builder": "DOCKERFILE"`.
  - **Nixpacks** (`retailmind/railway.json` + `retailmind/nixpacks.toml`): `"builder": "NIXPACKS"`, `python311`, instala `requirements.txt` (el de la carpeta `retailmind/`, el set extendido), `collectstatic`, arranca `gunicorn retailmind.wsgi --log-file -`.
  - Nota: no se pudo confirmar cuál ruta usa cada plataforma realmente (Railway vs DigitalOcean); el `railway.json` raíz fuerza `DOCKERFILE` pero `RAILWAY_DEPLOYMENT.md` describe un deploy automático Django (detección Nixpacks).
- **Servidor de aplicación:** Gunicorn (`gunicorn==21.2.0`) sobre WSGI (`retailmind.wsgi.application`, definido en settings 141). No hay ASGI/async server. (No se revisó `wsgi.py` ni una `gunicorn.conf`: no se confirmó número de workers/timeouts — se usan los defaults de gunicorn salvo que la plataforma inyecte flags.)
- **Migraciones en deploy:** `Procfile` (raíz: `release: python manage.py migrate`; `retailmind/Procfile` además declara `web: gunicorn retailmind.wsgi --log-file -`) — las migraciones corren automáticamente en cada deploy (confirmado en `RAILWAY_DEPLOYMENT.md` líneas 52 y 93). Restart policy `ON_FAILURE`, máx 10 reintentos (ambos `railway.json`).
- **Plataformas:** Railway y DigitalOcean App Platform (host `retail-ap-mh3y2.ondigitalocean.app`, dominio custom `retail.webappsolutions.cl`). `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` contemplan ambas + `*.ondigitalocean.app` + `*.railway.app`/`*.up.railway.app` (estos últimos solo si está `RAILWAY_ENVIRONMENT`). HTTPS lo provee la plataforma; en prod (`DEBUG=False`) se activan HSTS (86400s, includeSubDomains, preload), cookies Secure (CSRF y sesión), nosniff, XSS filter y X-Frame DENY.
- **Variables de entorno clave:** `DATABASE_URL` (auto en Railway), `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `RAILWAY_ENVIRONMENT`/`RAILWAY_PUBLIC_DOMAIN` (auto), `EMAIL_*`, `REDIS_URL` (opcional), `ANTHROPIC_API_KEY`, `LANGFUSE_SECRET_KEY`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_HOST`, `RETAILMIND_API_KEY`, `ALLCONNECTED_*` (`ALLCONNECTED_WEBHOOK_URL`, `ALLCONNECTED_API_BASE_URL`, `ALLCONNECTED_API_KEY`, `ALLCONNECTED_API_HEADER_NAME`, `ALLCONNECTED_PEDIDOS_PATH`, `ALLCONNECTED_CANAL_ORIGEN_ID`), `POS_KIOSK_DEFAULT`, `SESSION_*`.
- **Servicios externos consumidos (red saliente):** SMTP (Gmail/MailerSend, `smtp.gmail.com:587` por defecto) para correo; Anthropic (Claude) + Langfuse (tracing, `https://cloud.langfuse.com` por defecto) para el asistente; webhooks/pull de AllConnected; endpoints de ecommerces externos para sync de portadas; Transbank (POS físico, SDK local `transbank-pos-sdk==1.0.1`). QZ Tray para impresión térmica usa certificados en disco — la ruta configurada es `BASE_DIR/'retailmind'/'certs'/private-key.pem` y `digital-certificate.txt` (es decir `retailmind/retailmind/certs/`, no `retailmind/certs/`); ese directorio no existe en el repo, los certs se aportan fuera de control de versiones.
- **Inconsistencia a destacar (packaging vs deploy estándar):** `requirements-railway.txt` (instalado por el Dockerfile) y `requirements.txt` raíz NO incluyen `celery`, `flower`, `django-redis`, `redis`, **ni `anthropic` ni `langfuse`**; solo el set extendido `retailmind/requirements.txt` los trae. Por tanto, en el deploy por Dockerfile el asistente conversacional (Anthropic/Langfuse) y Redis no están instalados aunque el código los soporte. El deploy por Nixpacks (que instala `retailmind/requirements.txt`) sí los trae. Cuál corre en cada plataforma productiva no se pudo verificar dentro del repo.

---

## Apéndice — Verificación

Cada sección fue revisada por un verificador independiente que contrastó sus afirmaciones concretas con el código y corrigió/eliminó lo inexacto. Resumen:

| Sección | Confianza | Correcciones aplicadas en verificación |
|---|---|---|
| 1. Dependencias (Python + JavaScript) | alta | 13 |
| 2. Servicios externos / APIs que la app consume | alta | 11 |
| 3. Variables de entorno | alta | 13 |
| 4. API interna y consumidores | alta | 14 |
| 5. App móvil de fidelización (AppFidelizar) | alta | 11 |
| 6. Datos, almacenamiento e infraestructura | alta | 12 |

### Detalle de correcciones por sección

**1. Dependencias (Python + JavaScript)**

- CORRECCIÓN MAYOR (Select2): la sección original afirmaba que Select2 está 'ausente en libs/ y js/' y lo trataba como referencia obsoleta. FALSO: Select2 4.1.0-rc.0 se carga por CDN (jsdelivr) en layout/header.html (CSS, línea 65) y layout/footer.html (JS, línea 99), y se usa en 40+ templates (gestionCompras, ventas, DTE, inventario, créditos, usuarios, etc.). La mención de CLAUDE.md es correcta. Reescrita la discrepancia.
- CORRECCIÓN (jQuery): el doc decía 'jQuery 3.5.1'. El archivo se llama jquery-3.5.1.min.js pero su contenido real es jQuery v3.7.0 (cabecera del archivo). Añadida nota de la discrepancia nombre-vs-versión.
- CORRECCIÓN (libs/fs): el doc lo describía como 'helper/shim del tema'. En realidad es el paquete placeholder de npm fs@0.0.1-security (security-holder), sin código funcional. Corregido.
- AÑADIDO (Choices.js versión): confirmada v9.1.0 desde libs/choices.js/package.json (el doc no daba versión).
- AÑADIDO crítico (libs CDN faltantes): el doc omitía librerías de terceros cargadas por CDN y realmente usadas: DataTables 1.11.5 (+ bootstrap5, responsive 2.2.9, buttons 2.2.2, print, html5), pdfmake 0.1.53 (+ vfs_fonts), JSZip 3.1.3, SheetJS/xlsx 0.16.9, xlsx-renderer 0.3.1, Bootstrap Icons 1.10.5, lord-icon 2.1.0. Añadida tabla y nota de discrepancia.
- VERIFICADO y reforzado (Dockerfile/deploy): el Dockerfile (raíz) hace COPY requirements-railway.txt + pip install -r requirements-railway.txt (líneas 27-28); NO instala retailmind/requirements.txt. Confirmada la incertidumbre del lector: anthropic/langfuse/redis/django-silk SOLO viven en EXT y NO llegan al deploy. assistant/agent.py importa anthropic, así que es una inconsistencia real (no hipotética). Reforzadas las notas.
- VERIFICADO (icons.min.css): confirmado que contiene Material Design Icons v6.5.95, remixicon, boxicons y Line Awesome (Free + Brands) por font-family. La afirmación del doc era correcta.
- VERIFICADO (versiones en cabecera): Bootstrap 5.2.3 (bootstrap.min.css), SweetAlert2 v11.6.11 (sweetalert2.min.js) confirmados leyendo cabeceras. Correctos.
- VERIFICADO (todos los requirements Python): R, D, RW y EXT coinciden exactamente con lo declarado por el lector y con las tablas del doc (versiones, presencia/ausencia de mysql-connector-python en D/RW pero no en R, Pillow 11.1.0 vs 9.0.1, numpy>=1.26,<3 en RW/EXT, etc.). No se encontraron errores en las tablas Python.
- AJUSTE menor (tabla CSS): el doc agrupaba mal el path RTL ('app-rtl/bootstrap-rtl/custom-rtl.min.css' como un único archivo anidado). En realidad son tres archivos planos en css/: app-rtl.min.css, bootstrap-rtl.min.css, custom-rtl.min.css. Corregido. También añadido myCss.css ya listado y aclarado que pos-kiosk.css se enlaza condicionalmente ({% if pos_kiosk %}).
- AJUSTE menor (entradas jsonify/response): el doc las fusionaba en una fila 'jsonify / response'. Son dos paquetes distintos en EXT: jsonify==0.5 y response==0.5.0. Separadas en filas.
- AJUSTE menor (fila basura 'wnumb (Python wnumb? no)'): eliminada de la tabla Python; wNumb es lib JS, ya está en la tabla JS. También se eliminó del listado Python.
- AÑADIDO (JS propio): el listado de static/js/ estaba incompleto. Añadidos foto_lightbox.js, transbank-helpers.js, transbank-pos-sdk.js, transbank-web-serial.js, transbank-webserial.js (verificados con ls).

**2. Servicios externos / APIs que la app consume**

- #12 pull pedidos: el ejemplo de `ALLCONNECTED_API_BASE_URL` como `https://ecommerce.webappsolutions.cl` era engañoso (lo presentaba casi como valor real). Verificado: el default real es '' (vacío); en el código aparecen DOS ejemplos distintos solo ilustrativos: `https://allconnected.host` (comentario en settings.py:649) y `https://ecommerce.webappsolutions.cl` (docstring de factura_notifier). Aclarado que no hay URL hardcodeada.
- Nota final: la sección decía '3 servicios salientes' pero a continuación enumeraba 4 archivos (contradicción interna). Corregido a '4 módulos salientes' (verificado: requests se importa exactamente en stock_notifier.py, factura_notifier.py, allconnected_pedidos_service.py, realsport_imagenes_service.py).
- #7 QZ Tray: el markdown dejaba vago el fallback a env ('contempla fallback a variable de entorno'). Verificado y nombrado: `qz_firmar` cae a la env `QZ_PRIVATE_KEY` y `qz_certificado` a `QZ_CERTIFICATE` (app/views.py:31550 y 31508); ambos aceptan `\n` literal. Añadidos a tabla y detalle.
- #7 QZ Tray: añadido el detalle del algoritmo de firma verificado en código (RSA PKCS1v15 + SHA512, base64, lib cryptography) que el markdown no especificaba.
- #14 ingesta entrante: precisado el header de API key. Verificado en app/api/external/authentication.py: se valida `RETAILMIND_API_KEY` vía header `X-Api-Key` (con fallback Bearer). Añadidos los archivos reales de la app external (authentication.py/views.py/serializers.py/urls.py) y la función real de recepción push `api_recibir_pedido_ecommerce` (el markdown mencionaba `_ingestar_pedido_dict` y `api_asignar_ticket_rm`, ambos confirmados).
- #6 DTE: precisada la función backend real `views_modulo_documentos.generar_txt_acepta_api` (app/urls.py:1104) en vez del genérico 'views_modulo_documentos.py / views.py'. Añadido el segundo endpoint que el JS también llama: `/app/documentos/generar-txt-desde-dte/`.
- #4 WebSocket Transbank: matizado que `TRANSBANK_WEBSOCKET_URL` NO está en settings.py, por lo que en práctica SIEMPRE se usa el default `ws://localhost:8090` salvo inyección externa (confirmado getattr en transbank_sdk_service.py:24).
- #9 Redis: corregida la atribución de los flags. `DJANGO_REDIS_IGNORE_EXCEPTIONS=True` es setting de módulo (settings.py:483); `IGNORE_EXCEPTIONS:True` está dentro de OPTIONS del cache `ventas` (no son lo mismo). Añadidos TTL verificados de los caches LocMem: `default`=300s, `catalogo`=900s.
- Enriquecimiento verificado (no eran errores): #1 añadido MAX_TOOL_CALLS=10 y que anthropic/langfuse están en retailmind/requirements.txt (no en el base) lo que explica su 'opcionalidad'; #5 añadida la variante de archivo `transbank-web-serial.js` además de `transbank-webserial.js` (ambos existen); versiones de paquetes confirmadas: transbank-pos-sdk==1.0.1, pyserial==3.5, websockets==11.0.3, requests==2.30.0, anthropic>=0.40.0, langfuse>=2.0.0, django-redis>=6.0.0, redis>=5.0.0.
- #5 Web Serial: el item #15 del lector mencionaba PedidosEcommerceListView (CBV) — se omitió en la sección por ser UI interna, no servicio externo; se documenta aquí solo que existe pero no aplica al inventario de APIs externas.
- Nota de cierre: matizado el alcance — NO se verificó que el grep de servicios fuera exhaustivo sobre los 60+ management commands ni los scripts sueltos `_fix_*`/`_reconciliacion_*` de la raíz (incertidumbre declarada por el lector, mantenida).

**3. Variables de entorno**

- MySQL: la doc decía '~25 comandos más'; el conteo real de archivos en management/commands/ que referencian MYSQL_HOST/MYSQL_DATABASE es ~32. Corregido a '~30 comandos más' / '~32 comandos'.
- QZ_PRIVATE_KEY en .env: la doc decía 'También está definida en .env como certificado embebido'. Verificado y aclarado: el valor del .env es un bloque -----BEGIN CERTIFICATE----- (un certificado, NO una clave privada PEM), por lo que qz_firmar fallaría con ese valor. Movido a una nota explícita.
- DEBUG: la doc decía 'true si == \'true\''; el código usa .lower() == 'true' (settings.py:33). Aclarado para distinguirlo del == 'True' case-sensitive de EMAIL_USE_TLS.
- EMAIL_USE_TLS: reforzada la advertencia de que la comparación es case-sensitive '== \'True\'' y que un valor 'true' en minúsculas DESACTIVA TLS (contrasta con DEBUG/ENABLE_SILK/POS_KIOSK_DEFAULT que usan .lower()).
- DEFAULT_FROM_EMAIL: aclarado que el fallback es al VALOR ya resuelto de EMAIL_HOST_USER (settings.py:309), no a la env var en sí.
- PG_DATABASE: confirmado el doble default ('retailmind' en código vs 'retail' en .env/.env.example) y marcado explícitamente como 'default real distinto del del código'.
- SECRET_KEY: añadido que firma los JWT (SIMPLE_JWT.SIGNING_KEY = SECRET_KEY, settings.py:541) y que el fallback de dev está duplicado en .env (verificado en .env línea 27).
- ALLCONNECTED_WEBHOOK_FACTURA_ENABLED / _PATH: aclarado que se leen con os.environ directo (no via settings), confirmado en factura_notifier.py:74 y 83.
- assistant: verificado con grep que retailmind/assistant/ tiene 0 referencias a os.environ/getenv (confirma que consume todo via settings).
- DJANGO_SETTINGS_MODULE: quitados los números de línea específicos de manage.py/wsgi.py/asgi.py que no se verificaron uno a uno; se dejan los archivos como referencia.
- Django: la doc no afirmaba versión de Django, pero se verificó que el pin real es Django==4.2.2 en requirements.txt/requirements-railway.txt/retailmind/requirements.txt (el comentario 'Django 5.2' en el docstring de settings.py es texto autogenerado, no la versión real). No requirió cambio en la tabla.
- MySQL defaults: detallada la diferencia entre migrate_from_laravel.py (os.getenv sin defaults, salvo MYSQL_PORT=3306) y migrate_from_vicent.py (inyecta en settings.DATABASES['vicent_mysql'] con defaults localhost/vicent_software/root/''), y el destino real del .env (dbHoldingTebes, puerto 25060).
- .env: completada la lista de lo que NO define (ANTHROPIC_API_KEY, LANGFUSE_*, POS_KIOSK_DEFAULT, ENABLE_SILK, QZ_CERTIFICATE, ALLCONNECTED header/path/canal/webhook; REDIS_URL comentada) para evitar dar a entender que define más de lo que realmente tiene.

**4. API interna y consumidores**

- JWT access token: el doc decía 'access 7 días'. El valor real es 12 h (settings.py:534, SIMPLE_JWT.ACCESS_TOKEN_LIFETIME = timedelta(hours=12)). El campo 'expires_at' de +7 días que devuelve DesktopLoginView NO refleja la vida real del JWT (es un valor calculado a mano en la vista, inconsistente con la config). Corregido a 12 h y aclarada la discrepancia.
- Refresh token desktop: 'refresh propio en BD RefreshTokenDesktop con rotación, 30 días' es correcto (RefreshTokenDesktop.crear_token default dias_expiracion=30, models_sync.py:248). El doc mezclaba REFRESH_TOKEN_LIFETIME de SIMPLE_JWT (que es 7 días, no usado por el flujo desktop que usa su propio token en BD). Aclarado: el refresh real del desktop es el de BD (30 días), no el JWT refresh de SimpleJWT (7 días).
- Doble montaje de la API externa: app.api.external.urls se incluye DOS veces — en urls.py raíz bajo /api/ (línea 41) Y dentro de app.api.urls bajo /api/v1/external/ (app/api/urls.py:36). El doc solo documentaba /api/. Añadida la nota del dual-mount.
- El stub /api/v1/sync/movimientos-caja/ NO 'redirige' a /cuadraturas/; responde HTTP 200 con un mensaje indicando usar /cuadraturas/ (sync/views.py:436-442). Corregida la descripción.
- Tipo de vistas: el doc en (a) y (c) no aclaraba que todas las vistas de la API v1 y externa son APIView (CBV) de DRF, en contraste con el proyecto que es 'FBV-only'. Añadido para que no se infiera que son FBV; el FBV/SessionAuth aplica solo a /app/api/ y /assistant/api/.
- IsSameSucursal / CanSyncTickets: el doc no lo mencionaba; verificado que están importados en sync/views.py pero NO aplicados (las vistas solo usan IsAuthenticated). Añadida la aclaración para no dar a entender que validan sucursal en sync.
- x-app-version y x-sucursal-id: el resumen del doc atribuía la validación de los tres headers a 'IsAuthorizedDevice/IsSameSucursal'. En realidad solo x-device-id lo valida IsAuthorizedDevice; x-app-version es de IsMinimumAppVersion y x-sucursal-id de IsSameSucursal, ambos permisos definidos pero no enganchados en las vistas revisadas. Corregido.
- x-request-timestamp: confirmado que está en CORS_ALLOW_HEADERS (settings.py:626) pero no hay código que lo valide; mantenido como 'reservado por inferencia' en vez de afirmar uso.
- solicitar-otp: precisado que NO crea clientes nuevos, solo crea la CuentaClienteApp de un Cliente ya existente (coincide con docstring de SolicitarOTPView).
- vincular: el doc lo llamaba 'alias'; en el código VincularCuentaView es una subclase de SolicitarOTPView que solo cambia throttle_scope a 'vincular_cliente'. Precisado y corregido el throttle scope por endpoint.
- El conteo de 248 rutas api/ en app/urls.py: verificado exacto (grep = 248), el doc decía '~248'. Mantenido el conteo confirmado.
- Líneas de referencia: confirmadas REST_FRAMEWORK en settings.py:496 (DEFAULT_AUTHENTICATION_CLASSES líneas 509-512, no exactamente 509), DEFAULT_THROTTLE_RATES líneas 516-520, RETAILMIND_API_KEY línea 582, CORS_ALLOW_HEADERS líneas 613-628. Ajustadas las referencias.
- Verificado que NovedadesView, MovimientosVentasView y VentasView son todas GET-only con ApiKeyAuthentication+ApiKeyPermission (external/views.py:991, 1096, 1360) — confirmada la inferencia del lector, ninguna acepta POST. Solo StockPorSkusView y StockGlobalView tienen GET y POST.
- Cache de /api/skus/: confirmados los valores exactos — 900 s (15 min) fresca, 3600 s (1 h) stale, lock 120 s (external/views.py:44-45,166-167). El doc decía '15 min' y 'stale 1h', correcto; añadidos los segundos para precisión.

**5. App móvil de fidelización (AppFidelizar)**

- Sección 5.5: el throttle 'vincular_cliente' decía 10/hour; el valor real en settings.py:519 es 5/hour (la sección 5.1 ya lo tenía correcto en 5/hour). Corregida la contradicción interna.
- Montaje de rutas: se añadió la cita exacta 'retailmind/urls.py:38' y 'app_name = cliente' (declarado en app/api/cliente/urls.py); ambos namespaces (api_v1, cliente) están presentes en el código y se mantuvieron.
- Sección 5.3 GiftCard: se precisó que la FK a Cliente usa related_name='giftcards', on_delete=SET_NULL y es opcional (null/blank), dato que faltaba.
- Sección 5.3 ProgramaFidelizacion: se aclaró que 10/50/20/365 son valores DEFAULT del modelo (puntos_bienvenida default 20, minimo_canje 50, vigencia 365, valor_punto 10), no valores fijos.
- Sección 5.5 SIMPLE_JWT: se añadió ISSUER='retailmind' y se precisó que ROTATE_REFRESH_TOKENS/BLACKLIST_AFTER_ROTATION del bloque SIMPLE_JWT aplican al flujo JWT del desktop, no al refresh opaco del cliente.
- Sección 5.5 Email: se añadieron los defaults reales de settings (EMAIL_HOST='smtp.gmail.com', EMAIL_PORT=587) y se confirmó EMAIL_BACKEND=SMTP y DEFAULT_FROM_EMAIL default=EMAIL_HOST_USER.
- Sección 5.5 Throttle: se documentó que los tests sobreescriben los tres rates a 1000/hour vía @override_settings (test_cliente_app.py), aclarando por qué pueden verse distintos.
- Aislamiento de tokens: se completó la ruta del test a app/tests/test_cliente_app.py::AislamientoTokensTest.test_token_staff_no_accede_a_endpoints_cliente (verificado existente).
- Nota de nombre: se documentó explícitamente que 'AppFidelizar' (prompt) corresponde en el código a la app Flutter 'Paola'/puntos.realsport.cl, y que el frontend Flutter no se revisó.
- Sección 5.4: se enlazaron los flujos a sus funciones reales (_marcar_canal_verificado, registrar_login, cliente_app_service.logout) y se confirmó que el canje no está en urls.py de cliente.
- No se encontraron errores en: nombres/related_names de los 7 modelos, claims del access token (tipo/cliente_id/cuenta_app_id sin user_id), nombres de las clases ClienteJWTAuthentication/IsClienteApp, las funciones de ambos servicios, los 13 endpoints/paths, ni los campos del perfil/giftcard/movimiento serializers; todo coincide con el código.

**6. Datos, almacenamiento e infraestructura**

- 6.1 Driver: se precisó que `psycopg2-binary==2.9.10` está en los tres archivos, y que el set extendido `retailmind/requirements.txt` ADEMÁS trae `psycopg2==2.9.10` (compilado). El original decía solo 'set extendido' sin notar el psycopg2 extra.
- 6.1 MySQL legacy: se corrigió la versión a `mysql-connector-python==9.5.0` y se aclaró que SÍ se instala en el deploy por Dockerfile (está en `requirements-railway.txt`, además de `requirements-dev.txt`). El original implicaba que solo era de migración sin notar que viaja en el deploy.
- 6.3 Media: el campo de cotizaciones es `archivo_pdf` (no genérico); el de caja es `imagen_comprobante`; el de compras es `evidencia_foto`. Se añadieron números de línea reales (cotizaciones.py 162, compras.py 355, caja.py 451/538, requerimientos.py 467) y se precisó que `user_profile_photo_path` es una función que retorna `usuarios/fotos_perfil/perfil_<id>_<ts>.<ext>`.
- 6.3 Media: se quitó la afirmación no verificable sobre que las únicas referencias a AWS estén en un .md de correo (no se reverificó ese punto); se dejó solo el hecho confirmado de que no hay S3/django-storages/Cloudinary/Spaces en código.
- 6.3 Imágenes de producto: se confirmó que `CredencialesEcommerce` y `FotoPortadaArticulo` viven en `app/models/configuracion.py`, y que el TTL del resolver es `CACHE_TTL_RESOLUCION=3600` (1h) sobre el cache `default`.
- 6.5 Celery: se reforzó con que el grep de `shared_task/.delay/apply_async/CELERY_` en TODO el árbol .py no encontró nada (no solo 'en el código'). Confirmado docstring en expirar_puntos.py línea 8.
- 6.7 Deploy / QZ certs: CORRECCIÓN FÁCTICA. La ruta real configurada es `BASE_DIR/'retailmind'/'certs'/` = `retailmind/retailmind/certs/`, no `retailmind/certs/`. Además ese directorio no existe en el repo (los certs se aportan fuera de VCS).
- 6.7 Deploy: se corrigió que el `requirements.txt` que instala Nixpacks es el de la carpeta `retailmind/` (el set extendido), no el `requirements.txt` raíz — esto es relevante porque el set extendido SÍ trae anthropic/langfuse/redis/celery.
- 6.7 Inconsistencia: ADICIÓN CRÍTICA. `anthropic` y `langfuse` también están SOLO en el set extendido `retailmind/requirements.txt`, NO en `requirements.txt` raíz ni en `requirements-railway.txt`. Por tanto el deploy por Dockerfile no instala las dependencias del asistente conversacional, mismo gap que Redis/Celery. El original solo mencionaba celery/flower/django-redis/redis.
- 6.7 Email/Langfuse defaults: se añadieron los valores por defecto verificados (`smtp.gmail.com:587`, `https://cloud.langfuse.com`) y la lista real de vars `ALLCONNECTED_*` y `LANGFUSE_*` (el original agrupaba con comodines).
- 6.4 Cache: se añadieron los `LOCATION` reales de cada backend (`retailmind-default`, `retailmind-catalogo`, `retailmind-ventas`) y se explicitó el riesgo de import-error de `django_redis` si se setea REDIS_URL en el deploy Dockerfile sin el cliente.
- Se mantuvieron sin cambios (verificados como correctos): conn_max_age=600/conn_health_checks=True, vars PG_* y sus defaults, whitenoise==6.5.0 + posición del middleware, storage backends y flags WHITENOISE_*, STATIC_*, gunicorn==21.2.0, retailmind.wsgi.application, Procfile release migrate, restartPolicy ON_FAILURE max 10, hosts DigitalOcean/Railway, handlers/loggers de logging y rotación 5MBx5 / 5MBx10.

