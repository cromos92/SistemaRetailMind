# Diagnóstico POS Touch · Fase 0

> Equipo destino: Intel Celeron J1900 · 4 GB RAM · 1920x1080 táctil 10 puntos · Win10 LTSC · Chrome
> Topología: cliente remoto contra `retail.webappsolutions.cl` (Django en servidor, POS solo navegador).
> Criterio táctil: target mínimo 48x48 px · botón principal 56–64 px · fuente base ≥ 16 px · hover reemplazado por :active.

## Prioridades

| P | Definición |
|---|---|
| **P0** | Hace la pantalla inutilizable con el dedo en el POS. Se arregla ya. |
| **P1** | Degrada la UX táctil o consume ancho de banda/CPU sin necesidad. Sprint siguiente. |
| **P2** | Mejora fina o cosmética, puede esperar. |

---

## 1. Configuración global

| Archivo | Línea | Problema | Fix propuesto | Prio |
|---|---|---|---|---|
| `retailmind/app/templates/layout/header.html` | 9 | `<meta viewport content="width=device-width, initial-scale=1.0">` permite pinch/zoom y no fija el layout a 1920px. Con Windows al 125% descuadra todo. | En rutas POS cambiar a `width=1920, initial-scale=1, maximum-scale=1, user-scalable=no` vía `{% if pos_kiosk %}`. | P0 |
| `retailmind/app/templates/layout/header.html` | 3 | `data-sidebar="dark" data-sidebar-size="lg"` fuerza sidebar de 163 KB en POS. | Si `pos_kiosk`, usar `data-layout="horizontal" data-sidebar-size="sm-hover"` o simplemente no renderizar `menu.html`. | P0 |
| `retailmind/app/templates/layout/menu.html` | — | 163 KB de HTML con 300+ items de menú que el POS nunca usa. | Envolver en `{% if not pos_kiosk %}...{% endif %}`. | P0 |
| `retailmind/app/static/css/nexo-responsive.css` | 12, 80, 127, etc. | Todos los arreglos táctiles (min-height 44px, font-size 16px, paddings) dentro de `@media (max-width: 992px)` y `@media (pointer: coarse)`. A 1920x1080 nada aplica. | Nuevo `pos-kiosk.css` con reglas scopeadas a `body.pos-kiosk` sin media query. | P0 |
| `retailmind/app/templates/registration/login.html` | 8 | Mismo viewport permisivo. | Cargar `pos-kiosk.css` + viewport POS-lock si `?kiosk=1`. | P0 |
| `retailmind/app/static/libs/chart.js/docs/**` | — | Ships docs HTML completas de Chart.js en collectstatic (cientos de archivos). | Borrar `docs/` o agregar `.whitenoise-ignore`. | P1 |
| `retailmind/retailmind/settings.py` | 199 | `CompressedStaticFilesStorage` (sin hash inmutable). | Cambiar a `CompressedManifestStaticFilesStorage` → Cache-Control `immutable, max-age=31536000`. | P1 |
| `retailmind/retailmind/settings.py` | 89–103 | TEMPLATES sin `cached.Loader`. Cada render reparsea `ticket_venta.html` (165 KB) y `generacionVentas.html` (545 KB). | `loaders: [('django.template.loaders.cached.Loader', [...])]` cuando `not DEBUG`. | P1 |
| `retailmind/retailmind/settings.py` | 356 | `SESSION_SAVE_EVERY_REQUEST = True` → un UPDATE a `django_session` en cada AJAX. | `SESSION_SAVE_EVERY_REQUEST = False`, `SESSION_COOKIE_AGE` ya bastante largo. | P1 |
| `retailmind/retailmind/settings.py` | — | Sin bloque `CACHES`. | Agregar `LocMemCache` + `@cache_page(300)` en endpoints de catálogo. | P2 |
| `retailmind/requirements.txt` | 11 | `mysql-connector-python==9.5.0` (~40 MB) solo se usó para migrar desde Laravel. | Mover a `requirements-dev.txt`. | P2 |

---

## 2. `ticket_venta.html` (creación de ticket, primary POS screen)

| Línea | Problema | Fix | Prio |
|---|---|---|---|
| 115–123 | `.vendedor-card:hover { transform: translateY(-6px); box-shadow 0 12px 30px; }` sin `:active`. En touch se queda pegado. | Pasar el efecto a `.vendedor-card:active, .vendedor-card.selected`. Dentro de `.pos-kiosk` dejar solo `scale(0.98)` al active. | P0 |
| 312 | `height: 36px;` en input | `min-height: 3rem` (48px) vía `pos-kiosk.css`. | P0 |
| 781 | `min-height: 32px;` | Override en `.pos-kiosk .nexo-chip { min-height: 2.75rem !important; }`. | P0 |
| 823 | `height: 22px;` (badge clickeable) | `min-height: 2.5rem; padding: 0.25rem 0.75rem;`. | P0 |
| 891 | `height: 34px;` | `min-height: 3rem`. | P0 |
| 1229 | `width: 42px; height: 42px;` (botón de acción) | `width: 3rem; height: 3rem;` mínimo. | P0 |
| 1691 | `style="min-height:40px"` inline en item de ticket | Subir a 48px en scope POS. | P1 |
| 627, 3771, 3805, 3812, 3845, 3889, 3903, 3927, 3942 | `font-size: 9-12px` en líneas de ticket y totales | En `.pos-kiosk` subir textos principales a 1rem (16px), montos a 1.125rem. Dejar tickets impresos con su font chico aparte. | P0 |
| 1248 | `.pos-input-precio, .pos-input-cantidad, .pos-input-stock { height: 46px !important; }` | Subir a 3rem (48px) para cumplir WCAG 2.5.5. | P1 |
| 1252 | `.pos-print-btn { height: 46px !important; }` | Subir a 3.5rem (56px) — botón principal. | P1 |
| ~100 | 28 reglas `:hover` totales en inline CSS | Duplicar a `:active, :focus-visible` o mover bajo `@media (hover:hover)`. | P0 |

---

## 3. `generacionVentas.html` (flujo wizard de cobro + DTE)

| Línea | Problema | Fix | Prio |
|---|---|---|---|
| 333, 337, 341, 345 | Labels `OBLIGATORIO/OPCIONAL` con `font-size:9px` — ilegibles a distancia de caja. | `font-size: 0.75rem` (12px) mínimo en POS, y separar badge del label con gap. | P0 |
| 1985 | `height: 44px;` (límite estricto, no falla WCAG pero sí recomendación POS). | Subir a 3rem (48px). | P1 |
| 2324 | `height: 40px;` en input | 48px. | P0 |
| 3015 | `height: 38px;` | 48px. | P0 |
| 2543 | `.stock-badge { padding: 2px 6px; font-size: 10px; }` | En POS: `padding: 0.25rem 0.625rem; font-size: 0.75rem;`. | P1 |
| 2581–2582 | Step counter 34px con font-size 11px | En POS: 48px con font-size 14px. | P1 |
| 2006, 2079, 2296 | `font-size: 12-14px` en cuerpo | Base 16px en `.pos-kiosk`. | P1 |
| 8247, 8432, 8566, 8642 | `font-size: 10-12px` — esto es del ticket impreso (monospace 80mm). | **Dejar intacto** — se imprime en papel térmico. Documentar exención en `pos-kiosk.css`. | — |
| 8660, 8692, 8699, 8706, 8716, 8726, 8740, 8751 | Más `font-size: 9-14px` en previsualización | Verificar si son print o pantalla; ajustar solo los de pantalla. | P1 |

---

## 4. `gestion_pos_transbank.html` (cobro tarjeta)

| Línea | Problema | Fix | Prio |
|---|---|---|---|
| 17 | `.pos-card:hover { ... }` sin `:active`. | Duplicar a `:active`. | P0 |
| — | Botones de montos y PIN: verificar que estén ≥ 56x56 px, son los más críticos del cobro. | Clase `.pos-action-primary` 3.5rem. | P0 |
| — | Botón "CONFIRMAR PAGO" debe ser el target más grande (≥ 64 px). | `.pos-action-primary-xl { min-height: 4rem; font-size: 1.25rem; }`. | P0 |

---

## 5. `gestion_cambios_devoluciones.html` (devolución / cambio)

| Línea | Problema | Fix | Prio |
|---|---|---|---|
| 52, 135, 463, 480, 506, 626, 1238, 1416, 1449 | 9 reglas `:hover` con transforms y colores activos | Pasar a `:active, :focus-visible`. | P0 |
| 147 | `height: 34px` | 48px. | P0 |
| 430, 441, 1063, 1352 | `height: 10–24px` en badges/selectores (¡10 px no es tocable!) | Si son clickeables, subir a 48px. Si son solo visuales decorativos, dejar pero confirmar. | P0 |
| 1119 | `height: 30px !important;` | 48px. | P0 |
| 184, 204, 514 | `font-size: 11–12px` | 1rem (16px) en POS. | P1 |
| 516 | `min-height: 28px;` en tab | 48px. | P0 |
| 556 | `height: 26px;` | 48px. | P0 |

---

## 6. `cuadraturaCaja.html` (cierre de caja, operada a mano con denominaciones)

| Línea | Problema | Fix | Prio |
|---|---|---|---|
| 66, 100, 102, 104, 106, 108, 110, 112, 114, 179, 194 | 11 reglas `:hover` con transforms y cambios de color | Duplicar a `:active`. | P0 |
| 81 | `.input min-height: 40px` (denominaciones) | 48px mínimo — la cajera teclea montos con el dedo. | P0 |
| 99 | `.btn min-height: 38px` | 48px. | P0 |
| 115 | `.btn-sm min-height: 32px` | 44px mínimo (WCAG). | P0 |
| 121 | `.table-hover tbody tr:hover` | En POS táctil no tiene sentido; reemplazar por `tr:active`. | P1 |
| 129 | Avatares circulares 34px | Si son clickeables, 48px. | P1 |
| 210, 221, 261 | Overrides ya a 36–40px en media query mobile | Unificar en POS a 48px sin media query. | P1 |

---

## 7. `emisionDTE.html` (emisión factura/boleta)

| Línea | Problema | Fix | Prio |
|---|---|---|---|
| 49, 86, 256, 299, 343, 365, 397, 413, 431, 449 | 10 reglas `:hover` | Duplicar a `:active, :focus-visible`. | P0 |
| 108 | `height: 28px` | 48px. | P0 |
| 310, 321 | `height: 20px, 15px` | Si son barras decorativas, OK. Si son clickeables, 48px. Revisar. | P0 |
| 514 | `height: 34px` | 48px. | P0 |
| 642 | `height: 30px` | 48px. | P0 |
| 116, 522, 669 | `font-size: 14px` | 16px en POS. | P1 |
| 530, 589 | `font-size: 11px` | 14px mínimo para texto secundario. | P1 |

---

## 8. `registration/login.html`

| Línea | Problema | Fix | Prio |
|---|---|---|---|
| 8 | Viewport `width=device-width` | En kiosko, POS-lock a 1920. | P0 |
| — | No carga `pos-kiosk.css` (es template standalone, no usa `header.html`). | Incluir condicional `{% if request.GET.kiosk %}...{% endif %}` para cargar override. | P0 |
| — | Inputs de email y password: verificar que estén a 48px. | Aplicar `pos-kiosk.css` override `input { min-height: 3rem; font-size: 1rem; }`. | P0 |
| — | Link "¿Olvidaste tu contraseña?" y checkbox "Recordarme" con target chico. | Agrandar a 48x48 en kiosko. | P1 |

---

## Resumen de impacto estimado

| Fix | Pantallas afectadas | Efecto esperado |
|---|---|---|
| `pos-kiosk.css` + switch `pos_kiosk` | 7 | Todos los targets ≥ 48px, fuentes ≥ 16px, sin estados hover colgados. **Principal** mejora visible. |
| Omitir `menu.html` en POS | 7 | -163 KB de HTML, -50+ items de JS dropdown, menos repaint. |
| Borrar `libs/chart.js/docs/` | — (build) | collectstatic 5–10 s más rápido, manifest más chico. |
| ManifestStaticFilesStorage + immutable | Todas | Segunda visita: CSS/JS/fonts 0 bytes (cache inmutable). |
| cached.Loader templates | Todas | Render de `ticket_venta.html` y `generacionVentas.html` ~30–50% más rápido bajo carga. |
| Chrome --kiosk + watchdog | — (runtime) | Sin barras, sin gestos accidentales, auto-recovery si Chrome cae. |

---

## Lo que NO se toca (scope fuera)

- Ticket impreso térmico 80mm (fonts 10–12px se mantienen: se imprimen en papel real).
- Backend MySQL/PostgreSQL performance: ya corre en servidor remoto capaz.
- Gunicorn/Waitress en POS: irrelevante, no hay Django corriendo ahí.
- Migraciones, `statsmodels`, `reportlab`, `openpyxl`: los usa el backoffice, no el POS.
