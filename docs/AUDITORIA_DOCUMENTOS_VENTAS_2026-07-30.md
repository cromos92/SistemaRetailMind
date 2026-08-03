# Auditoría — Gestión de Documentos de Ventas (`/app/ventas/documentos/`)

**Fecha:** 2026-07-30
**Alcance:** `views_modulo_ventas.py` (`gestion_ventas_documentos`, `listar_documentos_ventas`,
`exportar_documentos_ventas_excel`, `convertir_ticket_a_factura`, `detalle_documento_venta`,
`anular_documento_venta`, `eliminar_documento_venta`, `editar_dte_boleta_papel`, `crear_dte_manual`)
+ `templates/vistas/modulo_ventas/gestionVentasDocumentos.html` (3.898 líneas).

**Método:** análisis 100 % estático. No se ejecutó `manage.py` ni se consultó la BD
(el `.env` local apunta a producción). Todo hallazgo marcado CONFIRMADO fue verificado
leyendo el código citado.

---

## Estado de aplicación (2026-08-03)

Se aplicó **solo el subconjunto sin riesgo**. Nada de lo que cambia permisos o cifras fue tocado.

| Hallazgo | Estado |
|---|---|
| 4 — N+1 por `ONLY_DTE_PAGO` sin `'notas'` | ✅ **APLICADO** |
| 4 — tope de `per_page` (+ aviso de truncado en "Copiar Tabla") | ✅ **APLICADO** |
| 11 — export a Excel (campos, `descartado=False`, prefetch muerto, URL sin `/app`) | ✅ **APLICADO** |
| 1 — clave del middleware de permisos | ⛔ **NO aplicado** — activa un permiso nunca aplicado; requiere probar antes con un rol no-administrador para no dejar en 403 al personal de tienda |
| 2, 3 — `anular_documento_venta` y `convertir_ticket_a_factura` | ⛔ **NO aplicado** |
| 5 — botón "Cambio" imprime el ticket de otra venta | ⛔ **NO aplicado** |
| 6-10 — universo del queryset, NC, ANULADOS, columna Total | ⛔ **NO aplicado** — requiere decidir primero qué debe contar la pantalla |
| 12-18 — UX, filtros muertos, FIFO en eliminar, folio por RUT | ⛔ **NO aplicado** |

Verificación de lo aplicado: `py_compile` PASS, `manage.py check` PASS (0 issues),
`node --check` del fragmento JS PASS, y smoke sin tocar la BD — la URL del export resuelve a
`/app/api/ventas/exportar-documentos/`, el template compila, y `query.deferred_loading` confirma
que `notas` ya se carga con la fila.

---

## P0 — Arreglar primero

### 1. El módulo entero está fuera del sistema de permisos

`middleware_permisos.py:69` mapea la clave `/app/gestion-ventas-documentos/`, pero la URL real
es `/app/ventas/documentos/` (`urls.py:898`). `obtener_codigo_opcion()` hace match por substring
y ninguna clave del mapa está contenida en ese path, así que devuelve `None`, y el middleware
hace `return None` — acceso permitido (`middleware_permisos.py:474-477`).

El permiso `gestion_documentos_ventas` **sí existe** y **sí** esconde el ítem del menú
(`layout/menu.html:2426`). Es el patrón "solo se esconde el botón": revocar el permiso desde
`/app/permisos/gestion/` no bloquea nada. Cualquier usuario autenticado abre la pantalla y
consume todas sus APIs.

**Fix:**
```python
# middleware_permisos.py — reemplazar la clave rota
'/app/ventas/documentos/': 'gestion_documentos_ventas',
'/app/api/ventas/documento': 'gestion_documentos_ventas',
```
Enumerar los paths de API; `/app/api/ventas/` a secas taparía los endpoints del dashboard.

---

### 2. `anular_documento_venta` — sin scoping, sin permiso, y el stock nunca vuelve

`views_modulo_ventas.py:6171-6229`. Cuatro defectos encadenados:

- **Sin sucursal ni `tipo_transaccion`.** `get_object_or_404(Dte, id=documento_id)` permite marcar
  `ANULADO` cualquier DTE del sistema, incluida una factura de COMPRA. Sin `descartado=True`,
  así que sigue en el listado pero desaparece de la cuadratura: plata que se esfuma del arqueo
  sin NC, sin devolver stock y sin rastro de auditoría.
- **Devolución de stock es código muerto.** Asigna `documento.estado = 'ANULADO'` (:6195) y en la
  línea siguiente pregunta `if documento.estado == 'PAGADO'` (:6199) — jamás cierto.
- **El default `tipo='TICKET'` destruye un ticket ajeno.** El frontend manda solo `documento_id`
  (`template:1741`), y ese id es de un `Dte`. Con el default, el endpoint anula el `Ticket` que
  casualmente tenga ese id.
- **`documento.save()` mueve la fecha del ticket.** `Ticket.fecha`/`hora` son `auto_now=True`
  (`models/ventas.py:187-188`). Por eso `editar_dte_boleta_papel` usa `.update()`.

**Atenuante:** `anularDocumento` está definida pero **ningún botón la invoca** (verificado: solo la
definición en `template:1731`, sin call sites). El riesgo es por HTTP directo.

**Recomendación:** el endpoint es huérfano. Borrarlo junto con la función JS y la opción
"Anulado" del filtro, igual que se hizo con los endpoints de traspaso anónimos. Si se conserva:
`@requiere_permiso`, filtro por sucursal + `tipo_transaccion`, `select_for_update()`, y mover la
devolución de stock antes de setear el estado.

---

### 3. `convertir_ticket_a_factura` — emite folios ajenos y reescribe el maestro de Empresas

`views_modulo_ventas.py:5795-5945`.

- **IDOR + quema de folio SII.** El ticket se busca sin filtro de sucursal (:5810) y luego
  `obtener_siguiente_correlativo` incrementa el correlativo real de esa empresa creando un `Dte`
  EMITIDO. Cualquier usuario autenticado puede emitir facturas a nombre de EDEL, PA00, etc.
- **Mass-assignment sobre `Empresa`** (:5844-5854): si el RUT ya existe se hace `setattr` de
  `nombre`, `giro`, `direccion`, `comuna`, `correoVendedor` con lo que venga en el POST. `Empresa`
  es la misma tabla de los **emisores**: se puede cambiar la razón social que sale impresa en los DTE.
  Además no valida el RUT (`validar_rut_chileno` no se usa).
- **`fecha_emision` sin parsear** (:5870): fecha arbitraria, futura o de un período ya declarado,
  en un documento tributario.
- **Anti-duplicado frágil:** `referencias__icontains=f'TICKET-{correlativo}'` — `TICKET-1` matchea
  `TICKET-100`. Sin `select_for_update()`: dos POST simultáneos queman 2 folios.

También huérfano (`convertirAFactura` en `template:1675`, sin call sites; los elementos del DOM que
referencia no existen).

---

### 4. N+1 invisible: `ONLY_DTE_PAGO` no incluye `'notas'`

`utils_ventas.py:178-180` omite `'notas'`, pero el bucle del listado lee `pago.notas`
(`views_modulo_ventas.py:5376`). El campo queda **diferido** y cada acceso dispara un
`refresh_from_db` → **una query por cada pago de la página**. El `getattr(pago, 'notas', '')` no
protege: el `DeferredAttribute.__get__` resuelve con éxito, nunca lanza `AttributeError`.

Con `documentosPorPagina = 100` (`template:1182`) y ~220 ms de RTT: **~120 queries ≈ 26 s**.
Con el selector en 500: ~132 s. Con el botón "Copiar Tabla" (`per_page = 99999`,
`template:3072`, sin tope en el backend): minutos.

`ONLY_DTE_PRODUCTO` en cambio está correcto — verificado campo por campo.

**Fix (1 línea, el mejor ratio de la auditoría):**
```python
# utils_ventas.py:178
ONLY_DTE_PAGO = (
    'id', 'dte_id', 'metodo_pago', 'monto', 'voucher', 'tipo_tarjeta', 'notas', 'fecha_pago',
)
```
Y cambiar `getattr(pago, 'notas', '')` por `pago.notas` para que un `only()` incompleto falle
ruidosamente. Añadir tope: `per_page = max(1, min(int(...), 500))`.

---

### 5. El botón "Cambio" imprime el ticket de OTRA venta

`template:1539` pasa `doc.numero` — el **folio del DTE** — a un endpoint que busca por
**correlativo de ticket** (`views_modulo_ventas.py:2694`). El backend conoce el problema (el
comentario de :2726 lo dice textualmente), pero el fallback por `folio_dte` está **tercero**,
después de dos búsquedas por correlativo, y **la segunda no filtra por sucursal**.

Folios y correlativos son enteros del mismo orden de magnitud: la colisión es lo normal.

**Qué ve el usuario:** se imprime un Ticket de Cambio con productos, tallas y SKUs de una venta
ajena, válido 15 días, con código `TC-*`. El vendedor no tiene cómo notarlo.

**Fix:** el payload ya trae `ticket_correlativo` y `ticket_id` (:5449-5456). Usar
`imprimirTicketCambioDesdeDoc(${doc.ticket_correlativo || 'null'}, ...)` y **ocultar el botón
cuando sea null** (hoy se pinta en todas las filas, incluidas facturas y anuladas).

---

## P1 — Los números que muestra la pantalla

### 6. Tres definiciones incompatibles de "venta"

| | Cuadratura de Caja<br>`views_modulo_ventas.py:8028` | Reportes<br>`views_modulo_reportes.py:95` | **Esta pantalla**<br>`views_modulo_ventas.py:5143` |
|---|---|---|---|
| `tipo_transaccion` | `VENTA_PUBLICO`, `DEVOLUCION`, `ANULACION` | `VENTA`, `VENTA_PUBLICO`, `DEVOLUCION`, `ANULACION` | `VENTA`, `VENTA_PUBLICO` |
| `estado_dte` | `EMITIDO`, `ACEPTADO` | `EMITIDO`, `ACEPTADO`, `ANULADO` | **todos** |
| NC | restan | **restan** | **no existen** |

El propio código documenta en `views_modulo_ventas.py:8003-8008` que `tipo_transaccion='VENTA'`
se reserva para DTE emitidos **fuera del POS**. Incluirlo mete al KPI despachos valorizados a
costo, documentos por concepto y facturas de compensación de compras — ninguna es venta de mesón.
Las conversiones ticket→factura también quedan como `'VENTA'`, y su dinero **ya está contado en
el ticket** (doble conteo).

El criterio canónico ya existe en `_queryset_dtes_ventas_vendedor` desde la auditoría de reportes
de julio. Esta pantalla nunca se alineó.

### 7. Las Notas de Crédito no restan — y algunas SUMAN

Doble error de signo:

- **Las NC del POS quedan fuera.** Se crean con `tipo_transaccion` = `DEVOLUCION` / `ANULACION` /
  `TRASPASO`, ninguno en el filtro. El listado es **bruto de devoluciones**. `es_nota_credito` sí
  se consulta (:5449) pero solo para pintar la bandera `tiene_nc`; jamás netea plata.
- **Y hay NC que suman en positivo.** CONFIRMADO: `emitir_dte_concepto` (`views.py:26204`) acepta
  `NOTA DE CREDITO` y `NOTA DE DEBITO` entre sus `tipos_validos`, y las crea con
  `tipo_transaccion='VENTA'` (:26309) y `monto_con_iva` **positivo** (:26300). Como el queryset no
  filtra por `tipo_documento`, entran al listado y **se suman** al KPI.

**Chequeo de 10 segundos en producción, sin tocar la BD:** abrir la pantalla con rango amplio y
comparar "Total DTEs" contra "Facturas + Boletas". Si no cuadran, la diferencia son exactamente
las NC/ND/guías que inflan el "Total Ventas".

### 8. Los ANULADOS suman completos

El queryset no excluye `estado_dte='ANULADO'` y `anular_documento_venta` no toca `monto_con_iva`
ni borra los `Dte_Detalle_Pago`. La pantalla heredó media convención de reportes (incluir ANULADO)
sin la otra mitad (restar la NC que lo anuló) → neto `+monto` en vez de `0`.

### 9. La columna "Total" no muestra el total

`template:1529` renderiza `doc.subtotal_bruto || doc.total` — la suma de líneas **antes** de
descuentos. Coexisten cuatro cifras para el mismo documento: la celda (bruto), el KPI
(`_total_real` = pagos), el modal (`doc.total`), el Excel (`monto_con_iva`) y el ordenamiento de
esa misma columna (`monto_con_iva`).

Peor: la base de `Dte_Productos.precio` **no es homogénea**. `emitir_dte`/`emitir_dte_concepto`
guardan líneas **netas**; ticket→boleta y `convertir_ticket_a_factura` las guardan **con IVA**.
`normalizar_detalle_para_tipo` normaliza solo en memoria para el TXT, nunca persiste. En una
factura emitida por despacho externo la fila muestra el **neto** y el KPI cuenta el **bruto**:
19 % de desfase.

### 10. `_total_real` = pagos: el abono parcial inventa un descuento

Cascada en :5402-5408. Una factura a crédito de $1.000.000 con un abono de $200.000 muestra
**total $200.000** y **"descuento −$800.000"**. `registrarPago` (`views.py:12640`) crea el pago por
cualquier monto y deja `estado_pago='Abonado'`.

Además, `agregarNotaCredito` (`views.py:12863`) inserta un `Dte_Detalle_Pago` con
`metodo_pago='Nota de Crédito'` y monto **positivo**. `pagosDTE` (`views.py:12686`) los excluye
explícitamente como "instrumentos no-efectivo" junto a las compensaciones — **el subquery de
:5252 no excluye ninguno**, así que inflan `_total_real`.

### 11. El export a Excel nunca ha funcionado

`views_modulo_ventas.py:5640-5641` lee `dte.monto_total` y `dte.iva`. El modelo `Dte` **no tiene**
esos campos ni propiedades (solo `monto_neto` y `monto_con_iva`, `models/dte.py:89-90`;
verificado también contra migraciones). El primer documento lanza `AttributeError`, lo traga el
`except Exception` de :5787 y el frontend — que abre con `window.open` — muestra un blob JSON.

Y aun arreglándolo, diverge del listado en cuatro puntos: no filtra `descartado=False`, ignora
`monto_min`/`monto_max`, hace match exacto de `metodo_pago` (pierde los `TBK_*` agrupados), y usa
`monto_con_iva` en vez de `_total_real`.

**Fix:** extraer el queryset a un helper compartido `_queryset_documentos_ventas(request)` que
ambas vistas llamen — mismo patrón que resolvió el descuadre en la auditoría de reportes.

---

## P2 — Fricción y correcciones puntuales

### 12. Tres `DOMContentLoaded` anidados que nunca corren

`template:2924, 2946, 3003` se registran **dentro** del handler que abre en :1178 y cierra en :3734,
o sea durante el despacho del propio evento. La especificación DOM clona la lista de listeners
antes de invocarla → los tres bloques están muertos. Consecuencias:

- **Crear un DTE manual con "Venta por Internet" es imposible.** El bloque de :2924 es el que
  muestra `#dteManualWrapTipoTarjeta`; queda con su `display:none` de :524, y `crearDteManual()`
  valida en :2752 que el campo esté lleno. Lo mismo con el voucher: el dato se pierde en silencio.
- **Ningún Select2 se inicializa.** `#dteManualVendedor` queda como `<select>` nativo con **todos
  los vendedores activos del holding** (la vista los devuelve sin filtrar por sucursal, :5024),
  sin buscador.

**Fix:** sacar los tres bloques del handler exterior y ejecutarlos inline al final del mismo
(el script va después del footer, el DOM ya está parseado).

### 13. Filtros muertos y engañosos

- **"Pendiente" nunca devuelve nada.** `'PENDIENTE'` no está en `ESTADO_DTE_CHOICES`
  (`models/dte.py:31-41`, verificado); ningún código lo escribe. La opción del dropdown y el KPI
  `total_pendientes` son muertos. Debería mapear a `estado_pago='PENDIENTE'`, que sí existe.
- **"Pagado" pierde los ACEPTADO.** Mapea solo a `EMITIDO`, pero
  `manage.py marcar_dtes_migrados_aceptados` escribe `ACEPTADO` masivamente. Usar `__in`.
- **El filtro de monto filtra un número que no se ve.** `monto_min_raw.isdigit()` descarta en
  silencio decimales y negativos que el `<input type="number">` sí entrega; y filtra sobre
  `monto_con_iva` mientras la columna muestra `subtotal_bruto`.
- **El placeholder promete de más.** Dice "talla, voucher" (`template:76`); el backend no busca
  ninguno de los dos. El caso "el cliente trae el voucher de Transbank" es el de mostrador.

### 14. `fecha_pago` existe hace tiempo y el código lo ignora

`views_modulo_ventas.py:5377` comenta que "`Dte_Detalle_Pago` no tiene campo `fecha` propio" —
**incorrecto**: `models/dte.py:408` define `fecha_pago` con help_text "Permite fechas pasadas
(retroactivos) y futuras (cheques a fecha)", y `registrarPago` lo puebla. El código estampa
`fecha_emision` del DTE en su lugar: una factura a 30 días aparece pagada el día de emisión.

### 15. `tiene_nc` se calcula, se envía y el frontend lo ignora

El backend hace una consulta dedicada (:5342) con el comentario "el modal de edición necesita
avisarlo antes de dejar tocar el folio". `doc.tiene_nc` no aparece en ninguna parte del template.
El usuario abre Editar, cambia el folio, confirma el Swal, escribe el motivo obligatorio de 5
caracteres, y recién ahí el backend lo rechaza.

### 16. Otros

- **Sin columna Estado.** `obtenerClaseEstado()` está definida y nunca se usa; un DTE anulado es
  indistinguible de uno válido.
- **Sin estado de carga** al paginar/ordenar/filtrar: la tabla muestra datos viejos sin señal.
- **Los selects y fechas no auto-aplican**, pero el buscador y los montos sí (debounce 500/600 ms).
  Comportamiento mixto en la misma barra.
- **Los filtros no viven en la URL:** F5 pierde todo y el botón Atrás saca de la pantalla.
- **El deeplink `?buscar=` del ecommerce está roto:** `leerParamsURL()` (:1277) corre antes que
  `inicializarFechas()` (:1336), que fija el rango a hoy → solo encuentra DTE emitidos hoy.
- **La barra dice "Solo consulta"** (:173) mientras ofrece Editar (reasigna folios y mueve períodos
  tributarios) y Eliminar (devuelve stock). Etiqueta falsa. La pantalla tampoco tiene título.
- **`{{ request.user.empresa }}` sin `|escapejs`** (:3805): un apóstrofo en el nombre produce un
  SyntaxError que mata todo el bloque final del script.
- **`title="${doc.cliente_nombre}"` sin escapar** (:1519): una razón social con comilla doble
  rompe la fila; el mismo valor entra por `innerHTML` sin sanitizar.
- **CSS responsive muerto:** las media queries de :1162-1172 apuntan a 5 clases que no existen en
  el markup. El `:root` de :560 es copia literal de `nexo-design-system.css`, que ya se carga
  globalmente, y las ~500 líneas siguientes pisan con `!important` clases globales de Bootstrap
  sin scope de módulo.
- **Cero reuso del design system:** ni `module-header`, ni `kpi-card`, ni `pagination-controls`,
  ni `quick-filter-btn`. Gradientes hardcodeados en atributos `style` inline.
- **No se puede ordenar sin mouse:** los `<th class="sortable">` no tienen `tabindex` ni `aria-sort`.

### 17. `eliminar_documento_venta` — bien construido, dos huecos

Tiene rol validado en servidor, `transaction.atomic()`, `select_for_update()` y guarda de
idempotencia. Pero:

- **Escribe stock plano sin tocar lotes FIFO** (:6372, :6415) — el patrón exacto que generó el
  drift `Producto_Talla.stock` vs `LoteProducto` ya documentado. Debería usar
  `services.inventario_service.ingresar()`, ya importado en el archivo.
- **Doble devolución si el DTE ya tiene NC:** solo salta el stock si el documento **es** una NC.
  Una boleta ya devuelta por NC vuelve a sumar las unidades al eliminarse.

### 18. `crear_dte_manual` — folio único por sucursal en vez de por RUT

`views_modulo_ventas.py:7668` valida `(sucursal, tipo_documento, numero_documento)`. El SII asigna
folios por **RUT + tipo** (CAF), como explica el docstring de `_validar_folio_destino_dte`, que
este endpoint no usa. Con sucursales que comparten RUT (IMP con los NICK, PA00 con los PAO) se
puede crear un folio ya emitido por la hermana: es el escenario del incidente PAO4/PAO3.
Además el `.exists()` está fuera del `atomic()` y no hay constraint único en BD.

---

## Hipótesis refutadas (verificadas contra el fuente, no son bugs)

- **Off-by-one por zona horaria:** `fecha_emision` es `DateField` (`models/dte.py:94`), no
  DateTimeField → la comparación es de fecha pura, sin conversión TZ. El frontend construye las
  fechas en local y compensa `getTimezoneOffset()`. Correcto.
- **Fan-out por JOIN en `Sum('_total_real')`:** verificado en
  `django/db/models/sql/query.py:436-467` (Django 4.2.2 instalado): cuando el queryset lleva
  `.distinct()`, Django envuelve en subquery conservando `default_cols`, el `SELECT DISTINCT`
  interno incluye `dte.id` y las filas duplicadas colapsan. El `Sum` es exacto.
- **Sin rango de fechas por defecto:** sí lo hay — `inicializarFechas()` (`template:1336`) fija
  hoy–hoy antes de la primera carga. Pero el backend no tiene guarda: basta vaciar el input para
  escanear todo el histórico.
- **Bug de closure / `$` antes de jQuery:** no existe. El `<script>` va después del include del
  footer y las 17 funciones llamadas por `onclick` están expuestas con `window.X = function`.
- **`main-content` duplicando el margin-left:** el template no lo usa. Correcto.

---

## Orden de ataque sugerido

| # | Fix | Esfuerzo | Gana |
|---|---|---|---|
| 1 | `'notas'` + `'fecha_pago'` en `ONLY_DTE_PAGO` | 1 línea | ~120 queries / ~26 s por carga |
| 2 | Tope de `per_page` a 500 | 1 línea | Cierra el DoS del "Copiar Tabla" |
| 3 | Corregir la clave del `middleware_permisos` | 2 líneas | Cierra el módulo entero |
| 4 | Borrar `anular_documento_venta` + `convertir_ticket_a_factura` y su JS | ~120 líneas menos | Elimina 2 P0 de raíz |
| 5 | `monto_neto`/`iva` en el export + `descartado=False` | 3 líneas | Desbloquea una función caída |
| 6 | `ticket_correlativo` en el botón "Cambio" + ocultarlo si es null | 2 líneas | Deja de imprimir ventas ajenas |
| 7 | Sacar los 3 `DOMContentLoaded` anidados | mover bloques | Desbloquea DTE manual + Select2 |
| 8 | Alinear el queryset al criterio canónico de reportes | ~15 líneas | KPI deja de mentir |
| 9 | Renderizar `doc.total` en la columna Total | 1 línea | Una sola cifra por documento |
| 10 | Helper `_queryset_documentos_ventas` compartido con el export | ~30 líneas | Excel = pantalla |

Los ítems 1-3 son una tarde y cubren el riesgo de seguridad y el 90 % del tiempo de respuesta.
Los ítems 8-10 requieren decidir primero **qué debe contar** esta pantalla (facturación histórica
como reportes, o caja como cuadratura); esa decisión es de negocio, no técnica.
