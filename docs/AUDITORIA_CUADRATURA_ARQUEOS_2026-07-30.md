# Auditoría — Cuadratura de Caja y Revisión de Arqueos

**Fecha:** 30-07-2026
**Alcance:** `/app/ventas/cuadratura-caja/` y `/app/ventas/revision-arqueos/`
**Archivos:** `views_modulo_ventas.py` (bloque caja ≈3.900 líneas), `models/caja.py`,
`cuadraturaCaja.html` (5.114 líneas), `revisionArqueos.html` (1.486 líneas)

---

## 0. La pregunta del usuario

> *"siento repetitivo el arqueo, confirmarlo"*

**No es una sensación. Está medido.** El módulo tiene **9 actos distintos de
"confirmar"**, **5 endpoints que crean un depósito** (3 de ellos saltándose el
flujo oficial de 3 pasos), y **7 rutas que recalculan los mismos teóricos** con
**4 fórmulas de diferencia que se contradicen entre sí**.

Un día normal exige **7 POST de escritura, ~16 interacciones, 4 modales y 2
roles**. El mínimo funcional son 2 POST y 5 clics.

Y hay tres bucles que **no se pueden cerrar nunca**, que son la causa real de la
repetición:

1. **`REQUIERE_ACCION` no tenía salida.** `revisar_arqueo` deja el `estado`
   intacto a propósito, y `dias_sin_revision` sólo devolvía 0 si
   `estado == 'REVISADO'`. Un arqueo que el supervisor YA revisó y marcó para
   corrección seguía contando días, escalaba a **CRÍTICO con animación de
   pulso** y volvía a pedir revisión todos los días, para siempre.
2. **"Aprobar OK" estaba bloqueado casi siempre.** La puerta exigía que el
   depósito verificado igualara el efectivo teórico del día (±$1.000). En la
   operación real eso casi nunca se cumple: el depósito va con rezago, un
   comprobante cubre varios días (`GrupoDeposito` existe justo para eso) y parte
   del efectivo queda como fondo fijo. El supervisor quedaba obligado a escribir
   una justificación a mano **cada día con efectivo**.
3. **Sin acción masiva ni vista multi-sucursal.** Un mes de 13 sucursales son
   ~340 arqueos: entre **700 y 1.700 clics**, más 13 cambios de sucursal
   (≈91 queries y ~20 s de red sólo en ida y vuelta).

---

## 1. Hallazgos por severidad

### P0 — Corrección de datos / seguridad

| # | Hallazgo | Ubicación | Estado |
|---|---|---|---|
| 1 | **Borrar un depósito destruía el conteo express.** `eliminar_deposito_bancario` usaba `arqueo.save()`, y `ArqueoCaja.save()` recalcula `total_efectivo_fisico` sumando billetes y monedas — que en modo EXPRESS están todos en 0. Dejaba el efectivo contado en $0 y la diferencia en `-(teórico+fondo)`. Era el único endpoint del módulo que se saltaba la regla de usar `.update()`; todos los demás lo documentan | `views_modulo_ventas.py:10185` | ✅ **corregido** |
| 2 | **Cualquier supervisor aprobaba arqueos de cualquier empresa.** `revisar_arqueo` hacía `get_object_or_404(ArqueoCaja, id=...)` sin filtrar sucursal (IDOR) | `revisar_arqueo` | ✅ **corregido** |
| 3 | **Un administrador podía crear, contar, cerrar y aprobar su propio arqueo.** `crear_arqueo` no tiene gate de rol y `revisar_arqueo` no comparaba responsable vs revisor: el control de cuatro ojos existía sólo de palabra | `revisar_arqueo` | ✅ **corregido** |
| 4 | **XSS en la bitácora y las observaciones.** `obs.texto`, `observaciones_supervisor` y `observaciones` se concatenaban crudos a `innerHTML`. Es texto libre escrito por personal de tienda que se ejecuta en el navegador del supervisor | `revisionArqueos.html` | ✅ **corregido** |
| 5 | **`cancelar_arqueo` BORRA el arqueo** (y por CASCADE sus depósitos, bitácora e historial) con sólo `@login_required`, sin rol y sin auditoría. Su gemelo correcto —`eliminar_cuadratura`, con rol + `log_accion_caja`— está **muerto** | `views_modulo_ventas.py:12986` | ⚠️ **pendiente** (ver §4) |
| 6 | **`crear_deposito_multidia` tiene `@csrf_exempt`** y crea N depósitos `verificado=True` | `views_modulo_ventas.py` | ⚠️ **pendiente** |

### P1 — KPIs que mentían

| # | KPI | Qué hacía | Estado |
|---|---|---|---|
| 7 | **Dif. Efectivo** | `Sum('diferencia_efectivo')` **con signo**: un sobrante de $50.000 tapaba un faltante de $50.000 y el indicador marcaba $0 | ✅ ahora `exposicion_efectivo` = \|faltantes\| + \|sobrantes\|, sin compensar |
| 8 | **Dif. Transbank** | Incluía arqueos con `cierre_pos_fisico = 0`, restando el Transbank teórico completo → faltante inventado del tamaño de la venta con tarjeta | ✅ sólo sobre arqueos que informaron cierre POS + contador aparte |
| 9 | **Con Diferencias** | `Count(estado='CON_DIFERENCIAS')` — **se autoborraba**: al aprobar, el estado pasa a `REVISADO`, así que un día con $80.000 de faltante desaparecía del KPI apenas se revisaba, y el mes cerraba en 0 con la caja igual de descuadrada | ✅ se cuenta por MONTO, no por estado |
| 10 | **Cumplimiento** | Podía superar **100%**: el numerador contaba arqueos de domingo y el denominador excluía los domingos (`weekday() < 6`) | ✅ topado, y el denominador ahora son **días con venta real**, no calendario |
| 11 | **Los 8 KPIs ignoraban el filtro de fecha** | Se calculaban siempre sobre el mes en curso mientras la tabla respetaba el rango. El template lo había parcheado con un cartel: *"no dependen del rango de fechas"* | ✅ todos responden al período y a las sucursales consultadas |
| 12 | **Cuadran al peso** | Se calculaba en cliente sobre las filas ya filtradas: con el tab "Cerrados" daba **100%** siempre; con "Con Diferencias", **0%** siempre | ✅ se calcula en backend sobre el período |
| 13 | **Días hábiles** | Lunes a sábado por calendario: los domingos con venta nunca aparecían como faltantes, y a una sucursal cerrada los lunes se le inventaba un faltante | ✅ día operativo = día con venta pagada |

### P2 — Funcionalidad ausente

| # | Hallazgo | Estado |
|---|---|---|
| 14 | **Sin acción masiva de ningún tipo** (~700-1.700 clics/mes) | ✅ endpoint `revisar-lote` + selección en la tabla |
| 15 | **Sin vista multi-sucursal**: 13 recargas manuales | ✅ pill "Todas" |
| 16 | **`resultado_revision` se filtraba en JavaScript** sobre las 100 filas descargadas → "Requiere acción" sólo encontraba los de la primera página | ✅ server-side |
| 17 | **Los filtros de estado y de resultado se anulaban entre sí** — no se podía pedir "con diferencias Y pendiente", que es justo la cola del supervisor | ✅ un tab fija ambos ejes |
| 18 | **Faltaban 8 filtros**: monto, sobrante/faltante, express, días sin revisar, depósito pendiente, cajero, sin explicación, multi-sucursal | ✅ todos server-side |
| 19 | **Sin paginación en frontend** pese a existir en backend: con >100 arqueos el resto desaparecía en silencio | ✅ paginación real |
| 20 | **`dias_faltantes` se calculaba y nunca se mostraba**: el supervisor veía "3 sin arqueo" sin saber cuáles | ✅ alerta con el detalle |
| 21 | **Panel de depósitos pendientes inalcanzable en Cuadratura**: nacía oculto y su único disparador era un botón dentro del propio panel oculto | ✅ se carga en el init |

### P3 — Bugs de UI

| # | Hallazgo | Estado |
|---|---|---|
| 22 | **`ReferenceError: $ is not defined`.** jQuery lo carga `footer.html`, incluido DESPUÉS del script del módulo. Un `$(document).on(...)` a nivel superior abortaba **todas** las sentencias top-level siguientes. Se perdían en silencio: el clic en las filas del Resumen de Caja, dos previews de imagen y —el más grave— **el aviso al supervisor de que el monto confirmado difiere del declarado** | ✅ envueltos en `waitForJQuery` |
| 23 | **Conteo de billetes desbordaba en tablet.** El wizard es `modal-xl`, pero Bootstrap sólo le da 800px desde 992px: entre 576 y 991px mide 500px, y cada fila necesita ~270px en dos columnas. La regla CSS que lo arreglaba (`.denominacion-row`) apuntaba a una clase que **no existía en el markup** | ✅ clase añadida a las 11 filas + `col-lg-6` |
| 24 | **Chip "● NUEVO" en arqueos cerrados hace semanas** (un `OK_CON_OBS` caía a la rama final de `getUrgencyLevel`) | ✅ corregido |
| 25 | **Todo en rojo**: `diferencia !== 0` pintaba rojo, así que $1 se veía igual que $80.000 | ✅ tolerancia única |
| 26 | **Panel de depósitos mostraba OTRA sucursal**: no enviaba `sucursal_id` y el endpoint caía a la sucursal de sesión | ✅ corregido |
| 27 | **7 `fetch` sin `.catch`**: ante un fallo de red el spinner giraba para siempre sin mensaje | ✅ corregido en la carga principal |
| 28 | **Redefinía el design system**: un `:root` local con 16 tokens en valores distintos (`--nexo-error` #FF4757 vs #FF4D4D), afectando a toda la página. Y usaba `--nexo-gray-200`, **nunca definido** → badge sin fondo y bitácora sin separadores | ✅ sólo el token faltante |
| 29 | Reloj repintando el DOM 86.400 veces por jornada | ✅ cada 30 s |

---

## 2. Las 4 fórmulas de diferencia (raíz de las contradicciones)

`ArqueoCaja` expone cuatro nociones de "diferencia" y cada consumidor usaba una:

| Nombre | Fórmula | Tolerancia según quién la mire |
|---|---|---|
| `diferencia_efectivo` (campo) | `físico − (teórico + fondo_fijo)` | $0 exacto en `cerrar_arqueo` |
| `diferencia_efectivo_real` | `(físico − depósitos − fondo) − teórico` | ±$1.000 en los 4 endpoints de depósito |
| `diferencia_deposito_vs_teorico` | `depositado_verificado − teórico` | ±$1.000 |
| `diferencia_total_real` | la anterior + `diferencia_transbank` | — |

**Contradicciones concretas que esto producía:**

- **Depositar la plata volvía el día "con diferencias".** `diferencia_efectivo_real`
  resta los depósitos al efectivo físico, pero el conteo se hace ANTES de
  depositar → descontaba la plata dos veces. Un día perfectamente cuadrado
  pasaba a `CON_DIFERENCIAS` por el solo hecho de haber depositado, mientras el
  listado seguía mostrando diferencia $0 en la misma fila.
- **Un faltante real de $999 se blanqueaba.** `cerrar_arqueo` lo marcaba
  `CON_DIFERENCIAS` (exige $0), pero cualquier operación de depósito posterior
  recalculaba con tolerancia ±$1.000 y lo pasaba a `CERRADO`.
- **Supervisor y cajero veían números distintos.** `obtener_detalle_arqueo`
  (pantalla del supervisor) usa `físico − teórico` **sin fondo fijo**;
  `obtener_arqueo_detalle` (pantalla del cajero) usa el campo con fondo fijo. Y
  el segundo **persiste** su versión mientras el primero no: **abrir una u otra
  pantalla primero determinaba qué quedaba escrito en la base.**

**Corregido:** tres constantes únicas en `views_modulo_ventas.py`
(`TOLERANCIA_ARQUEO_EFECTIVO = 500`, `TOLERANCIA_ARQUEO_DEPOSITO = 1000`,
`UMBRAL_ARQUEO_CATEGORIA = 5000`) y un campo `veredicto` por fila
(`CUADRA` / `FALTANTE` / `SOBRANTE`) que todas las pantallas consumen.

> **Sigue abierto:** unificar las 4 fórmulas en el modelo. Antes de tocarlas hay
> que decidir **si el depósito es un estado del arqueo o un atributo** — hoy son
> 2 de los 6 estados y 4 endpoints laterales los pisotean.

---

## 3. Qué se cambió

### Backend — `views_modulo_ventas.py`

- **`listar_arqueos` reescrito.** Indicadores sobre el período y las sucursales
  consultadas; 9 filtros nuevos server-side; multi-sucursal (`sucursal_id=all`
  o lista) autorizado contra las sucursales del usuario; orden configurable;
  paginación real.
- **N+1 eliminado.** El `aggregate` de fallback de depósitos corría **dentro**
  del loop: hasta 100 queries extra por página. Ahora es una consulta agrupada.
- **`count()` sin doble agregación.** Se cuenta sobre un queryset sin
  anotaciones; las anotaciones caras (`Exists` + 2 `Count(distinct)`) se aplican
  sólo a las filas de la página.
- **`revisar_arqueo`**: scoping por sucursal, bloqueo de auto-revisión, y la
  puerta del depósito pasa de muro a **paso franqueable con motivo obligatorio**
  registrado en bitácora y auditoría.
- **`revisar_arqueos_lote`** (nuevo, `POST /app/api/arqueo/revisar-lote/`):
  aprueba sólo lo que no tiene nada que juzgar (cuadra dentro de tolerancia, no
  abierto, sin veredicto, sin depósitos pendientes, y el revisor no es el
  responsable). Cada aprobación deja su `LogAccionCaja` y su observación.
- **`obtener_depositos_pendientes`** acepta `sucursal_id` validado.

### Modelo — `models/caja.py`

- **`revisado`** (nuevo): la fuente de verdad es `resultado_revision`, no el
  estado. Es el fix del bucle infinito de `REQUIERE_ACCION`.
- **`dias_sin_revision` / `requiere_revision_urgente`** ahora dependen de él.
- **`dias_accion_pendiente`** (nuevo): días desde que se pidió la corrección —
  mide el tiempo de respuesta de la tienda, que es lo que hay que perseguir.

> Son properties: **no requieren migración**.

### Frontend

- **`revisionArqueos.html`**: bandeja de alertas accionables (cada chip es una
  cola con su filtro), 9 KPIs → 4 con significado, filtros avanzados, selección
  y aprobación en lote, paginación, XSS cerrado, `:root` saneado, CSS muerto
  eliminado.
- **`cuadraturaCaja.html`**: `ReferenceError` de jQuery, panel de depósitos
  visible, conteo usable en tablet, reloj a 30 s.

---

## 4. Pendiente (decisiones que no tomé solo)

1. **`cuadraturaCaja_v2.html` — 187 KB, template muerto.** Ningún `render()` lo
   referencia. Arrastra **6 endpoints huérfanos con la URL viva y accesible**:
   `guardar_cuadratura_completa` (crea arqueos con teóricos que vienen del
   navegador, sin rol), `editar_cuadratura` (`depositos.all().delete()` y
   recrea, sin rol y sin chequeo de estado), `agregar_deposito_arqueo`,
   `verificar_cuadratura_existente`, `eliminar_cuadratura`, `listar_cuadraturas`.
   **Borrar el template y esas rutas es la limpieza de mayor impacto/riesgo del
   módulo**, pero requiere tu visto bueno.
2. **`verificar_deposito`** — endpoint muerto que marcaría depósitos como
   verificados con `monto = 0` y sin disparar el signal de cache. Bomba inerte.
3. **`cancelar_arqueo`** — borra sin rol ni auditoría (P0 #5). El reemplazo
   correcto ya existe y está muerto (`eliminar_cuadratura`).
4. **`GIFTCARD`, `OTRO` y `MULTIPLE` no tienen rama** en el clasificador de
   `_calcular_cuadratura_data` y no hay `else`: esa plata entra en
   `total_tickets` pero no en ningún desglose por método. `GIFTCARD` está vivo.
5. **`DEVOLUCION` y `ANULACION` no están en `Dte.tipo_transaccion.choices`**
   aunque la cuadratura filtra por ellos. Fix trivial, sin migración de datos.
6. **`Ticket.fecha` es `auto_now`**: cualquier `save()` posterior mueve el
   ticket de día, y con él su plata en la cuadratura. Riesgo estructural.
7. **Cero tests** del ciclo de vida del arqueo, depósitos, permisos y del
   servicio de fraude. Sólo está cubierto el borde NC↔cuadratura (19 tests).
8. **~40 endpoints `/app/api/arqueo/*` y `/app/api/cuadratura/*` fuera de
   `URL_PERMISO_MAP`**: el middleware los deja pasar y la seguridad depende de
   que cada vista recuerde su `if rol in [...]`.

---

## 5. Reducción esperada del trabajo repetido

| | Antes | Después |
|---|---|---|
| Aprobar un mes de 13 sucursales | 700-1.700 clics + 13 cambios de sucursal | 1 vista + selección + 1 clic |
| Arqueo `REQUIERE_ACCION` | crítico parpadeante **para siempre** | se cierra al resolverse |
| Aprobar OK un día con efectivo | bloqueado → justificación a mano cada día | aprobable, con motivo sólo si falta el depósito |
| KPIs vs tabla | períodos distintos (mes vs filtro) | el mismo |
| Consultar 13 sucursales | 13 recargas, ~91 queries | 1 consulta |
| Filtro "Requiere acción" | sólo los de la primera página | todos |
