# Checklist manual · POS táctil Celeron J1900

Se ejecuta **en el equipo destino** (1920x1080 táctil 10 puntos, Win10 LTSC).
Marca cada item en vivo: si alguno falla, abrir issue con el screenshot.

## 0. Pre-requisitos

- [ ] Windows al 100% de escalado DPI (Configuración → Pantalla → Escala = 100%).
- [ ] Chrome actualizado (última stable o ESR ≥ 120).
- [ ] Resolución nativa 1920x1080 a 60 Hz.
- [ ] POS conectado a red y `https://retail.webappsolutions.cl` responde.
- [ ] Cookie de sesión válida (usuario POS ya logueado al menos una vez).

## 1. Activación del modo kiosko

- [ ] Abrir Chrome con el acceso directo `NEXO POS` del escritorio.
- [ ] La barra de dirección **no** está visible.
- [ ] La pantalla abre directo en `ticket-venta/?kiosk=1`.
- [ ] El sidebar izquierdo (163 KB de menú) **no** aparece.
- [ ] Inspeccionar (F12 desde otro equipo por RDP) → `<body>` tiene la clase `pos-kiosk`.
- [ ] Inspeccionar `<head>` → `<meta name="viewport" content="width=1920, ...">`.
- [ ] Inspeccionar Network → `pos-kiosk.css` carga con status 200.

## 2. Targets táctiles (usar el dedo, no mouse)

Regla: cada target debe poder tocarse **con el índice** sin equivocarse y sin
pedir "zoom" al usuario.

- [ ] Botón `Cobrar` ≥ 56 px de alto. Se siente firme al tocar.
- [ ] Inputs de búsqueda de producto ≥ 48 px.
- [ ] Fila de carrito: cada item ocupa ≥ 48 px de alto.
- [ ] Botón `+` / `−` de cantidad en el carrito ≥ 48 x 48 px.
- [ ] Botón de borrar producto: no se dispara sin querer al scrollear.
- [ ] Teclado numérico del cobro: cada celda ≥ 64 x 64 px.
- [ ] Selección de vendedor (tarjetas): tocar una marca `selected` y **no queda pegada** en `hover`.
- [ ] Label `OBLIGATORIO` y `OPCIONAL` es legible (≥ 12 px).

## 3. Flujo completo end-to-end

Cronometrar cada paso.

- [ ] Login (PIN o user/pass). Objetivo: < 3 s desde touch hasta dashboard.
- [ ] Ir a `Ticket de venta`. Objetivo: TTI < 2 s.
- [ ] Buscar 1 producto por nombre, agregarlo. Scroll fluido, sin lag.
- [ ] Buscar por código de barras (si hay pistola): el input debe tener foco y auto-enter.
- [ ] Cambiar cantidad tocando `+` 3 veces. Número se actualiza sin parpadeo.
- [ ] Elegir método de pago. Cada tarjeta responde con `:active` (no hover).
- [ ] Confirmar pago efectivo. Modal SweetAlert: botones ≥ 52 px, texto ≥ 16 px.
- [ ] Ticket se imprime (o previsualiza). Verificar font 10-12 px **solo en el papel**, no en pantalla.
- [ ] Ir a `Cuadratura caja`. Ingresar denominaciones. Inputs ≥ 48 px, texto 18 px.
- [ ] Ir a `Devoluciones/Cambios`. Tabs ≥ 48 px. Selección de motivo responde al tocar.
- [ ] Emitir DTE de prueba. Wizard: step counter ≥ 48 px.

## 4. Performance en el equipo real

- [ ] Abrir `chrome://inspect/#devices` desde otro equipo (RDP o LAN), o `chrome://system`.
- [ ] Task Manager de Chrome: pestaña POS < **500 MB RAM**.
- [ ] Task Manager de Windows: RAM libre > **1 GB** con el POS cargado y 1 ticket en curso.
- [ ] DevTools → Performance → grabar 10 s de uso (agregar producto + scroll). FPS ≥ 30.
- [ ] DevTools → Network, primera carga: total wire < **2 MB**, requests < **40**.
- [ ] DevTools → Network, segunda carga (reload con caché): wire < **50 KB** (solo HTML; CSS/JS inmutables).
- [ ] `scripts\check_pos_bundle.py` retorna exit 0.

## 5. Kiosko y bloqueos

- [ ] `F11` no hace nada (bloqueado por políticas).
- [ ] `Alt+F4` no cierra la ventana.
- [ ] `Ctrl+W` no cierra la pestaña.
- [ ] `Tecla Windows` no abre el menú inicio.
- [ ] `Ctrl+Alt+Del` solo ofrece bloquear/cerrar sesión (no Task Manager).
- [ ] Click derecho táctil (press-hold) no abre menú contextual.
- [ ] Cerrar Chrome manualmente (desde Task Manager por RDP): el watchdog lo relanza en < 20 s.
- [ ] Reboot del equipo → arranca directo al POS sin pedir password (autologin).

## 6. Verificación posterior a un despliegue

- [ ] Hacer `collectstatic` en el servidor remoto.
- [ ] Ver que en el HTML servido los CSS vengan con hash (ej. `app.min.abc123.css`).
- [ ] Cache-Control de esos assets: `public, max-age=31536000, immutable`.
- [ ] Segundo reload del POS: Network panel muestra CSS/JS con `(disk cache)` o `(memory cache)`.

## 7. Cuando algo se ve raro

Si un botón se ve chico o un label se corta:

1. Anotar archivo + línea del CSS que lo gobierna (click derecho en DevTools → Copy selector).
2. Buscar si ya está cubierto en `retailmind/app/static/css/pos-kiosk.css`.
3. Si no, agregar un override con selector scopeado a `body.pos-kiosk ...`. Nunca editar el CSS original del módulo, porque afecta a backoffice.
4. Ver `DIAGNOSTICO_POS_TOUCH.md` → sección del template correspondiente.

---

**Umbrales duros de aceptación:**

| Métrica | Target | Rojo |
|---|---|---|
| TTI pantalla principal | < 2 s | > 4 s |
| RAM proceso Chrome POS | < 500 MB | > 900 MB |
| RAM libre del sistema | > 1 GB | < 500 MB |
| FPS scroll carrito | ≥ 30 | < 20 |
| Targets táctiles primarios | ≥ 48 px | < 44 px |
