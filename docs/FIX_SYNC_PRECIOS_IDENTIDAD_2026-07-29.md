# Fix: la sincronización de precios usaba una clave que confundía productos

Fecha: 2026-07-29. Estado: CÓDIGO IMPLEMENTADO. Pendiente: correr tests +
script de remediación de datos.

## El incidente (verificado contra producción)

El 25-07-2026 se crearon **GUANTES** EVERLAST ROJO UNISEX en EDEL (ficha
138379, tallas 12/14/16, $44.990) reutilizando el código `009283623`, que ya
era de unas **ZAPATILLAS BOX ELITE 2** EVERLAST ROJO UNISEX.

El detector de duplicados hizo bien su trabajo (Guantes ≠ Zapatillas, creó
ficha nueva). El problema fue la **sincronización automática de precios**:
matcheaba por `articulo + atributo1 + atributo2` (código + marca + color),
sin género ni categoría. Encontró las zapatillas ROJO de NICK2 y les puso el
precio de los guantes:

    HistorialCambioPrecio: ficha 136745 (NICK2) $109.990 → $44.990
    motivo: "Sincronización automática desde creación de producto en EDEL"

Resultado: **8 pares de zapatillas vendiéndose $65.000 bajo precio** (~$520.000
de exposición). La ficha de zapatillas de EDEL (136744) se salvó solo porque el
sync excluye la sucursal de origen — lo que dejó las dos bodegas divergentes y
el POS marcando distinto según la sucursal de la sesión.

Nota: el género NO discrimina este caso (ambos UNISEX). La **categoría** es la
que distingue Guantes de Zapatillas, por eso entra en la clave.

## El fix

Dos helpers nuevos en [utils_producto_match.py](../retailmind/app/utils_producto_match.py),
que dejan la identidad de precios alineada con la identidad de deduplicación
(`fichas_por_identidad`):

- `qs_fichas_identidad_otras_sucursales(...)` — el MISMO producto en otras
  sucursales: código (`iexact`) + marca + color + **género + categoría**.
- `qs_fichas_codigo_otra_identidad(...)` — las "casi-coincidencias": mismo
  código+marca+color pero otro género/categoría. Son las que el sync ya NO
  toca.
- `resumen_casi_coincidencias(qs)` — texto corto para los logs.

Aplicado en los **6** lugares que usaban la clave corta:

| Archivo | Función | Rol |
|---|---|---|
| `views.py` | `crear_producto_desde_recepcion` | sync al crear por recepción |
| `views.py` | `crear_producto_manual` | **sync que causó el incidente** |
| `views_modulo_gestion_precios.py` | `actualizar_precio` | sync de edición rápida |
| `views_modulo_gestion_precios.py` | `sincronizar_sucursales` | sync masivo |
| `views_modulo_gestion_precios.py` | `buscar_productos` | badge "Sincronización" |
| `views_modulo_gestion_precios.py` | `buscar_productos_similares_sucursales` | detalle por sucursal |

Los dos últimos son de *display*: se cambiaron a propósito para que el badge
que promete "Sincronización" liste exactamente las fichas que el sync tocará.

## El sync nunca calla

Estrechar la clave introduce un riesgo real dado el estado del catálogo: si el
MISMO producto está mal categorizado en otra bodega, deja de recibir el sync y
los precios divergen en silencio. Por eso las casi-coincidencias:

- se registran con `logger.warning` en los 3 puntos de escritura, y
- `actualizar_precio` las devuelve en `no_sincronizadas`, y la edición rápida
  muestra un aviso amarillo ("N ficha(s) con el mismo código NO se
  sincronizaron — tienen otra categoría o género").

Así el mismo aviso sirve para las dos causas: código reutilizado por productos
distintos, o ficha mal categorizada.

## Pendiente

1. Tests: `python manage.py test app.tests.test_sync_precio_identidad -v 2`
   (5 tests; reproducen el caso guantes/zapatillas).
2. Remediación de datos del incidente:
   `python manage.py shell -c "exec(open('_fix_codigo_guantes_precio_zapatillas.py', encoding='utf-8').read())"`
   (DRY-RUN validado: renombra los guantes a `009283623G` y restaura $109.990
   en la ficha 136745 + sus 6 lotes; editar `DRY_RUN = False` para aplicar).
