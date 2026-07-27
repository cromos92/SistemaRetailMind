# Análisis de la sincronización de stock RetailMind ↔ AllConnected

**Fecha:** 2026-07-26
**Alcance:** RetailMind (ERP, fuente de verdad) → AllConnected (hub) → Shopify / Paris / Ripley / Walmart / ecommerce propio
**Método:** lectura del código desplegado + consultas de solo lectura a las dos bases de datos de producción (RetailMind en DigitalOcean, AllConnected `allconected`).
**Nota:** este informe es una síntesis de 8 análisis paralelos, pero **todas las cifras que aparecen acá fueron re-verificadas directamente contra producción**. Donde los análisis se contradecían, fui al dato. Las correcciones están marcadas explícitamente en la sección 6.

---

## 1. Veredicto de conjunto

# FRÁGIL — no confiable durante el horario de venta, confiable de madrugada

La respuesta directa a "¿puedo confiar en la sincronización de stock?" es **no, no durante el día**.

El sistema publica un número de stock **correcto una vez al día y progresivamente falso durante las siguientes 8 a 12 horas**. No está roto —el transporte funciona, la reconciliación nocturna cierra la brecha, los endpoints responden— pero está **grueso**: la unidad de tiempo del diseño es el día, y el negocio vende por minuto.

Los tres hechos que sostienen el veredicto:

| Hecho verificado | Evidencia |
|---|---|
| **6 de los 7 canales vivos sincronizan 1 vez al día.** Solo `realsport.cl` tiene sync horario. | `django_celery_beat_periodictask` en prod: `realsport-stock-ecommerce-canal-29-horario` (cron `0 8-22`, 301 corridas). Todos los demás: `5 23`, `10 23`, `20 23`, `50 0`, `30 1`, `35 2`. |
| **No existe ninguna reserva de stock.** Un pedido pagado no bloquea la unidad en RetailMind. Dos canales pueden vender —y venden— la misma unidad. | `stock_seguridad` existe en `RelacionCanales` (system/models.py:131) pero **0 ocurrencias** en toda la ruta de sync RetailMind (`grep -c stock_seguridad system/marketplaces/retailmind/tasks.py` → `0`). |
| **52 pedidos pagados por el cliente, $3.009.703, que RetailMind no puede facturar** porque el SKU no tiene ni una unidad en toda la empresa. | Medición propia contra prod, ver §2.1. 44 de junio, 7 de julio, 1 de abril. Sigue ocurriendo. |

**En qué SÍ se puede confiar** (esto importa tanto como lo anterior):

- La bajada de stock en el ERP es sana: mediana de **62 s** entre el cobro y el movimiento de kardex.
- La reconciliación nocturna converge de verdad: Walmart 16.053/16.053 SKUs iguales a RM, Ripley 10.159/10.159 en la corrida del 26-jul.
- El canal con sync horario funciona: 301 corridas, ~2,9 s cada una, y es **el único con cero incidencias de sobreventa registradas**. La solución ya está probada en producción — solo falta copiarla.
- La API expone el stock **plano** (`Producto_Talla.stock`), no los lotes FIFO. Es la decisión correcta y hay que mantenerla (ver §5, "qué NO hacer").
- El endpoint de stock global es 1 sola query, y `_pull_stock_rm_global` no interpreta un stock ausente como 0 — buena defensa contra apagar el catálogo por una respuesta parcial.

---

## 2. Hallazgos ordenados por plata

### 2.1 — $3.009.703 en pedidos pagados que no se pueden surtir · CRÍTICO

El síntoma más caro y el más fácil de auditar. De los 168 pedidos en estado `PENDIENTE`, **52 tienen al menos un ítem cuyo SKU no tiene stock en ninguna sucursal de la empresa**.

| Métrica | Valor |
|---|---|
| Pedidos pagados no surtibles | **52** |
| Monto | **$3.009.703** |
| Distribución | jun-2026: 44 · jul-2026: 7 · abr-2026: 1 |
| Sub-estado | RECIBIDO 28 · ASIGNADO 24 |

Ejemplos reales: pedido 21 ($68.990, SKU 4810851 stock 0), pedido 24 ($74.980, SKU 4833585 stock 0), pedido 32 ($84.990, SKU 4785637 stock 0).

Que 44 sean de junio y sigan `PENDIENTE` significa que **no se resuelven solos**: quedan atascados hasta que alguien los cancela a mano. El costo real es mayor que los $3,0M — incluye el reembolso, la penalización del marketplace y el cliente perdido.

> **Consulta de verificación** (read-only): cruzar `app_pedido_ecommerce.items->>'sku'` de los pedidos `PENDIENTE` contra `SUM(GREATEST(app_producto_talla.stock,0))` agrupado por SKU.

### 2.2 — La ventana de desfase: 8,5 a 12 horas en 6 de 7 canales · CRÍTICO

Horarios reales leídos de la tabla de beat en producción (no de `settings.py`, que está desactualizado). Zona America/Santiago:

| Canal | Empresa | Cron | Corridas | Ventana de exposición |
|---|---|---|---|---|
| **realsport.cl (29)** | Realsport | **cada hora 08–22** | 301 | **~0,5 h** ← sano |
| Paris (4) | Realsport | 23:05 | 66 | ~8,6 h |
| Ripley (6) | Realsport | 23:10 | 66 | ~8,5 h |
| Walmart (27) | Realsport | 23:20 | 65 | ~8,6 h + feed |
| calzadospaola.cl (31) | Paola | 00:50 | 56 | ~10,1 h |
| Paris (3) | Paola | 01:30 | 56 | ~10,8 h |
| Walmart (10) | Paola | 02:35 | 56 | ~11,8 h + feed |
| Shopify (1) y (2) | ambas | **deshabilitado** | 0 / — | canal muerto |

Peor caso: una venta en tienda Paola a las 10:00 sigue publicada en Walmart hasta las 02:35 del día siguiente, más 5–10 min de feed XML ≈ **16 h 45 min**.

El contraste es la prueba: el único canal con sync horario es el único sin incidencias de sobreventa registradas.

### 2.3 — Paris rechaza un tercio del stock, todas las noches, en silencio · ALTO

Datos de `sys_importaciones_marketplace`, corridas del 25 y 26 de julio:

| Fecha | Canal | Enviados | Aplicados | **Rechazados** | % | Estado que muestra el tablero |
|---|---|---|---|---|---|---|
| 07-26 03:05 | Paris Realsport | 9.061 | 6.101 | **2.960** | 32,7% | ✅ COMPLETADO |
| 07-26 05:30 | Paris Paola | 17.598 | 15.431 | **2.167** | 12,3% | ✅ COMPLETADO |
| 07-25 03:05 | Paris Realsport | 8.997 | 6.024 | **2.973** | 33,0% | ✅ COMPLETADO |
| 07-25 03:13 | Ripley Realsport | 14.812 | 10.159 | **4.653** | 31,4% | ✅ COMPLETADO |

Son ~5.100 SKUs por noche cuyo stock en Paris **quedó congelado en un valor desconocido**, y el sistema lo reporta como éxito. Paris es además el canal con más pedidos.

Dos matices importantes que corrigen el diagnóstico inicial:

- **Los rechazados SÍ se reintentan.** Las cuatro tareas usan `force: bool = True` por defecto (tasks.py:3542, 3853, 4281, 4561), o sea re-empujan el catálogo completo cada noche. No quedan excluidos para siempre.
- **Pero nunca lo logran.** Si se reintentan todas las noches y siguen fallando en el mismo volumen, la causa es estructural (SKUs que no existen en el seller de Paris, o un error de formato), no transitoria. **Nadie está mirando por qué.**

El `force=True` también explica por qué el "con_diferencia" es de 9.061 y no converge: no son 9.061 cambios reales de stock. En la corrida del 26-jul, `sin_cambio = 9.022`, o sea **solo 39 SKUs tenían una diferencia real**; los otros 9.022 se re-empujan a propósito para revertir ediciones manuales en el portal del seller. Ese diseño es correcto — no es un bug.

### 2.4 — El espejo local miente sobre lo que el canal aceptó · ALTO

`tasks.py:3776` actualiza `VariacionCanal.stock_canal` para **todo el payload** con la sola condición `if paris_ok and payload`, donde `paris_ok` significa "ningún batch dio error HTTP" — no "Paris aplicó el SKU".

Verificado en los datos: corrida del 26-jul con `exitosas=6.101` y `bd_local = {'variaciones_canal': 13.173}`. Se marcaron **13.173 filas como sincronizadas cuando el canal solo confirmó 6.101**.

Hoy el daño está contenido por `force=True` (se re-empuja todo igual). **Pero el canal horario de realsport.cl corre con `force=false`** (visible en sus kwargs), y ahí el espejo mentiroso sí excluye SKUs del diff. Si se replica el sync horario a los demás canales con `force=false` —que es lo natural, para no empujar 9.000 SKUs cada hora— **este bug se vuelve crítico de inmediato**. Hay que arreglarlo *antes* de la Fase 1, no después.

### 2.5 — El endpoint incremental está estructuralmente roto · ALTO

`app/api/external/views.py:251-255` filtra por `Producto_Talla.updated_at` (`auto_now=True`).

Pero **toda** la mecánica de stock del ERP usa `QuerySet.update()`, que por diseño de Django **no ejecuta `Field.pre_save` y por lo tanto no toca `auto_now`**:

```
app/services/inventario_service.py:115   .update(stock=F('stock') + cantidad)
app/services/inventario_service.py:165   .update(stock=F('stock') - cantidad)
app/views.py:22384                       .update(stock=F('stock') - cantidad_requerida)   ← el cobro del POS
app/views.py:2104, 6572, 17477, 26661, 26703  … y ~30 sitios más
```

La venta baja el stock y `updated_at` queda como estaba. El endpoint responde `200 / success: true` con una lista vacía — **indistinguible de "no pasó nada"**.

Impacto: bloquea el camino barato hacia la sincronización frecuente. Mientras no se arregle, la única opción viable es el snapshot completo (3,2 MB por llamada), que es justamente lo que limita la frecuencia y sostiene la ventana de 8-12 h.

### 2.6 — Ninguna alerta cuando la sincronización falla · ALTO

`_finalizar_importacion_stock` marca `COMPLETADO` si el dict trae `success`, sin mirar `fallidas`. No hay una sola `AlertaSistema` en las tareas de RetailMind. Los watchdogs que existen (`watchdog_ingesta_pedidos`, `watchdog_envio_erp`, `watchdog_pedidos_atascados`) cubren **pedidos, no stock**.

Consecuencia medible: llevan semanas ~5.100 SKUs/noche fallando en Paris con el tablero en verde, y nadie se enteró.

### 2.7 — El push en tiempo real existe y está apagado · MEDIO

RetailMind tiene un `post_save` sobre `Movimientos_Producto` (`app/signals.py:76`) que llama a `notificar_cambio_stock` hacia `ALLCONNECTED_WEBHOOK_URL`. **Esa variable no existe en el `.env`** (solo están `ALLCONNECTED_API_KEY` y `ALLCONNECTED_API_BASE_URL`), y `_get_config()` hace `return` silencioso si está vacía.

Es el único mecanismo tiempo-real del diseño y **nunca operó**. Ver §5: encenderlo tal cual sería un error.

### 2.8 — La API key está en un repositorio público · CRÍTICO (fuera de banda)

No es un problema de sincronización, pero es el único hallazgo explotable por un tercero **hoy**, así que encabeza el plan.

`retailmind/.env` está trackeado en git y el repo es público. La clave viva abre el catálogo, los **costos** y las ventas de las 4 empresas. Como `rut_empresa` es un parámetro libre y no un scope derivado de la credencial, se puede leer cualquier empresa —incluidas las que ni se publican— y ningún endpoint declara throttling.

Ya existe `docs/SEGURIDAD_URGENTE_2026-07-25.md` con el plan de rotación. Esto va primero y va en paralelo, no compite con lo demás.

### 2.9 — Descartados por falta de evidencia

Para que el dueño no gaste tiempo acá:

- **"Los centros de distribución inflan el stock publicado"** → hoy es **falso**. 0 SKUs publicados tienen su stock solo en un CD. Es riesgo latente (7.910 u en IMP, 4.740 u en PA00 a un enlace de distancia), no daño actual.
- **"Los 3.787 movimientos VENTA_PUBLICO sin ticket son merma escondida"** → **descartado con datos**. El 100% tiene `referencia_externa` con prefijo `MIG:`: son ventas del Laravel migradas, y se cortan el 2026-04-16. Cero unidades de merma escondida ahí.
- **"La divergencia plano-vs-lotes FIFO del 21% produce sobreventa"** → **no sobre lo publicado**. Sobre los SKUs efectivamente publicados la divergencia es de 149 SKUs / 349 unidades (0,5%), y va en dirección conservadora.

---

## 3. La merma: respuesta directa

### ¿Está bien tratada? **No. No se está midiendo.**

El sistema tiene el vocabulario completo para registrar merma (`PERDIDA_ROBO`, `PERDIDA_DETERIORO`, `DONACION_ENTREGADA`, `AJUSTE_INVENTARIO_SALIDA` en `app/models/ventas.py:72-93`). **La operación no lo usa.**

Cifras verificadas directamente contra producción:

| Consulta | Resultado |
|---|---|
| `PERDIDA_ROBO` en **toda la historia de la base** | **0 movimientos** |
| `AJUSTE_INVENTARIO_SALIDA` en toda la historia | **0 movimientos** |
| `DONACION_ENTREGADA` en toda la historia | **0 movimientos** |
| `PERDIDA_DETERIORO` en toda la historia | **2 movimientos, 21 unidades** |
| Tomas de inventario por estado | **5, todas en BORRADOR** |
| Movimientos de merma sin observaciones (desde 17-abr) | **45 de 45 — el 100%** |

**Una empresa de retail de calzado con 6 tiendas declaró 21 unidades de pérdida en toda su historia y cero robos.** Eso no es una operación sin merma: es una operación que no la registra.

### Qué se registra en realidad

Los últimos 100 días con RetailMind operando solo (desde el corte del Laravel el 2026-04-17):

| Concepto | Movs | Unidades | Costo |
|---|---|---|---|
| `AJUSTE_NEGATIVO` (genérico, sin causa) | 43 | −209 | $3.089.400 |
| `PERDIDA_DETERIORO` (única causa declarada) | 2 | −21 | $1.045.290 |
| `CORRECCION_STOCK` | 2 | −4 | $0 |
| **Total merma registrada** | **47** | **−234** | **$4.134.690** |

Denominador del mismo período: **24.088 unidades vendidas / $280.865.458 al costo**.

> **Merma registrada = 0,97% de las unidades vendidas / 1,47% del costo de venta.**

Por sucursal: NICK2 127 u ($3.267.002) · NICK1 65 u · PAO4 26 u · PAO3 8 u · PAO2 3 u · PAO1 1 u.

**El 91% de la merma entra como "Ajuste Negativo" sin causa, y el 100% sin una sola línea de observación.** Un robo es hoy literalmente indistinguible de un error de digitación.

### ¿Cuánto está perdiendo sin saberlo?

**La respuesta honesta es que no se puede saber todavía, y eso mismo es el hallazgo.** No voy a inventar una cifra. Lo que sí está medido:

1. **$4.134.690 en 100 días es un piso, no un total.** Es lo que alguien se tomó el trabajo de registrar a mano.
2. **Existe una medición física real y nadie la aplicó.** La toma `INV-6-20260115-001` (NICK1) contó 4.301 SKUs de 37.373 (**11,5% de la tienda**) y encontró **151 unidades de mercadería real faltante, $1.320.862 al costo** (excluyendo 3 SKUs de servicio —ENVIOS, BOLSA, "45-1"— que distorsionan con 12.440 u). Está en BORRADOR desde el 15 de enero. Si esa proporción se sostuviera en el resto del catálogo, el orden de magnitud de la merma de **una sola tienda** sería varias veces el total registrado por las seis.
3. **Cualquier usuario logueado puede dar de baja stock sin permiso, sin motivo y sin aprobación.** `ajuste_stock_rapido` (views.py:7816) solo tiene `@login_required`, y el ítem del menú (`layout/menu.html:2013`) no tiene guard de permiso. Es la operación con mayor riesgo de fraude interno del ERP y es la única sensible sin control.

### Qué falta exactamente

| Falta | Consecuencia hoy |
|---|---|
| Causa obligatoria en el ajuste de stock | 91% de la merma sin causa |
| Observaciones obligatorias en egresos | 100% sin explicación |
| Permiso + aprobación sobre `ajuste_stock_rapido` | Cualquiera puede castigar inventario |
| Cerrar el ciclo de toma de inventario | La única merma medida (151 u) lleva 6 meses sin aplicar |
| Un informe de merma con denominador | El dueño no tiene el número |

---

## 4. Plan de 3 fases

Ordenado por relación impacto/costo. **Fase 0 corre en paralelo desde ya.**

### Fase 0 — Seguridad (paralelo, no compite)

| | |
|---|---|
| **Qué** | Rotar `RETAILMIND_API_KEY` y el resto del `.env`; `git rm --cached retailmind/.env`; coordinar el cambio con AllConnected en la misma ventana. |
| **Archivos** | `retailmind/.env`, `.gitignore`, `.env` de AllConnected. Ninguno de código. |
| **Riesgo** | Si la clave se rota sin coordinar, el sync se cae entero. Hacerlo en ventana nocturna con los dos lados a la vista. |
| **Verificación** | `git ls-files \| grep .env` vacío; `GET /api/health/` con la clave vieja → 401; una corrida de sync manual con la nueva → 200. |
| **Plan existente** | `docs/SEGURIDAD_URGENTE_2026-07-25.md` |

### Fase 1 — Cerrar la ventana (bajo costo, todo en AllConnected)

Esta fase **no toca RetailMind**, así que no interfiere con el trabajo en curso.

**1.1 — Arreglar el espejo antes de tocar la cadencia** *(prerrequisito, no opcional)*
- Actualizar `VariacionCanal.stock_canal` solo para los SKUs que el canal confirmó, no para todo el payload.
- Archivo: `system/marketplaces/retailmind/tasks.py:3765-3776` (y sus equivalentes en Ripley/Walmart/ecommerce).
- **Por qué primero:** con `force=True` el bug está tapado; al bajar a sync horario con `force=false` se destapa y empieza a excluir SKUs del diff en silencio.

**1.2 — Replicar el sync horario a los 6 canales restantes**
- Crear una `PeriodicTask` por canal, cron `0 8-22`, `force: false`, minutos escalonados (:00 Paris RS, :10 Ripley RS, :20 Walmart RS, :30 Paris PAO, :40 Walmart PAO, :50 calzadospaola).
- Archivos: **ninguno** — son filas en `django_celery_beat_periodictask`. Es configuración, no código.
- Empezar por Walmart y calzadospaola.cl, que hoy muestran 0 discrepancias.

**1.3 — Watchdog de stock**
- Alerta si `fallidas > 5%` del payload, o si una tarea de stock no dejó registro en N horas.
- Archivo: `system/marketplaces/retailmind/tasks.py` (copiar el patrón `watchdog_*_task` existente, con su dedup de alertas).

**1.4 — Diagnóstico read-only: por qué Paris rechaza 2.960 SKUs**
- Capturar la respuesta cruda de Paris para una muestra y clasificar el motivo. Sin esto, el 33% se arrastra a 15 corridas diarias en vez de 1.

| | |
|---|---|
| **Riesgo** | Bajo. Más llamadas a RM (~90 requests extra/día, despreciable: la corrida de canal 29 tarda 2,9 s para 11.715 SKUs). El riesgo real es Paris: si se sube a 15 corridas/día sin resolver 1.4, rechaza lo mismo 15 veces. |
| **Verificación** | (a) la exposición media baja de 8-12 h a ~0,5 h; (b) `con_diferencia` por corrida cae a decenas, como en canal 29; (c) `bd_local.variaciones_canal ≤ exitosas` en todas las corridas; (d) los pedidos nuevos no surtibles se detienen. |

### Fase 2 — Que el número publicado sea el número vendible (RetailMind)

Requiere coordinación con los agentes que están tocando el repo.

**2.1 — `stock_publicable` en los 3 endpoints de stock**
- `GREATEST(SUM(stock), 0)` − comprometido, devuelto **junto** al `stock_total` actual durante una fase de convivencia (no en reemplazo).
- Comprometido = `PendienteDespacho` abierto + `PedidoEcommerce` no facturado + tickets `PENDIENTE` recientes.
- Archivo: `retailmind/app/api/external/views.py` (líneas 260, 348-363, 462-475).

**2.2 — Colchón de seguridad configurable por canal**
- Corrección importante: `stock_seguridad` **existe en el modelo `RelacionCanales` pero tiene 0 ocurrencias en la ruta de sync de RetailMind**. No es un toggle, hay que cablearlo.
- Archivo: `system/marketplaces/retailmind/tasks.py`, en el bucle del diff: `stock_target = max(0, int(stock_rm) - rel.stock_seguridad)`.
- Empezar con 1 unidad en los SKUs de rotación alta. Un colchón de 1 unidad neutraliza la mayor parte de la sobreventa de "última unidad" a costo de venta casi nulo.

**2.3 — Arreglar el incremental**
- Filtrar por `Movimientos_Producto.fecha` en vez de `Producto_Talla.updated_at`.
- Devolver `generated_at` y `modo: snapshot|incremental` en los 3 endpoints.
- Archivo: `retailmind/app/api/external/views.py:251-255`.

| | |
|---|---|
| **Riesgo** | **Doble descuento**: si algún flujo ya rebaja stock al crear el pedido, descontar comprometido lo restaría dos veces. Verificar flujo por flujo antes de activar. Por eso 2.1 devuelve ambos campos y no reemplaza nada. |
| **Verificación** | Comparar `stock_total` vs `stock_publicable` en producción durante 1 semana sin consumirlo; el delta esperado es ~0,3-0,5%. Si es mucho mayor, hay doble conteo. |

### Fase 3 — Medir la merma de verdad

**3.1 — Causa y motivo obligatorios**
- Sacar `AJUSTE_NEGATIVO` de las opciones seleccionables (dejarlo solo como valor legacy de lectura) y ofrecer causas cerradas. Agregar `PERDIDA_EXTRAVIO` a `CONCEPTO_MOVIMIENTO_CHOICES`.
- Validar en el POST que `observaciones` no venga vacío en cualquier egreso.
- Archivos: `retailmind/app/views.py` (~7768 y ~7935), `app/models/ventas.py`.

**3.2 — Permiso y aprobación**
- Decorador de permisos sobre `ajuste_stock_rapido` + guard en `layout/menu.html:2013`. Aprobación tipo `puede_aprobar` sobre un umbral (> 5 u o > $200.000).
- **Debe ir con un comando aditivo que siembre el permiso, corrido ANTES del deploy** — si no, queda en 403 para todos (ya pasó con Liquidación).

**3.3 — Cerrar el ciclo de conteo**
- **Cancelar formalmente las 5 tomas de enero** y programar un conteo nuevo (ver §5).
- Guard que impida cerrar una toma con detalles en `ajuste_aplicado=False`.

**3.4 — Informe de merma mensual**
- Movimientos con `cantidad < 0` y concepto en la lista de merma, por mes × sucursal × concepto, con unidades, costo y precio de venta perdido.
- Denominador fijo: unidades y costo de venta del mismo período.
- KPI de cabecera: **merma % sobre costo de venta**. Línea base ya medida: **0,97% / 1,47%**.
- Excluir `CORRECCION_STOCK` y `AJUSTE_NEGATIVO` del cálculo (documentarlos como "ajustes técnicos") y excluir los SKUs de servicio por lista.

| | |
|---|---|
| **Riesgo** | Fricción operativa (mitigable con causas predefinidas en un select, no texto libre). Y un riesgo **interpretativo**: ver §5. |
| **Verificación** | A 60 días, el % de merma con causa declarada debe pasar de 9% a >90%, y los movimientos sin observaciones de 100% a ~0%. |

---

## 5. Qué NO hay que hacer

**1. No aplicar las 5 tomas de inventario de enero.**
Los conteos son del 2026-01-15 y hay 6 meses de ventas encima. Aplicarlas inyectaría ~12.500 unidades de ajuste calculadas sobre un stock base que ya no existe. **Cancelarlas con motivo y hacer un conteo nuevo.** El valor de esas tomas es diagnóstico (nos dijeron que hay merma), no operativo.

**2. No cambiar la API para que lea los lotes FIFO en vez del stock plano.**
Es tentador porque los lotes "parecen" más correctos. Sería un error: hay 7.017 unidades con stock plano sin respaldo FIFO, y esas unidades **desaparecerían del canal de golpe**. El plano es el campo que siempre se actualiza; el consumo de lotes es best-effort dentro de un `try/except`. Para publicar, el plano es el número correcto. El drift plano-vs-lotes es un problema de **valorización**, no de sobreventa, y se arregla por otro lado.

**3. No encender `ALLCONNECTED_WEBHOOK_URL` tal como está el código.**
`notificar_stock_a_allconnected` lanza **un thread daemon y un POST por cada `Movimientos_Producto` creado**. Cualquier recepción masiva o comando de migración bombardearía AllConnected con miles de requests. Antes hay que batchear por ticket/DTE y ponerle un flag de habilitación como el que ya protege al webhook de facturas. Encenderlo primero para un solo RUT.

**4. No poner `force=false` en Paris/Ripley/Walmart antes de arreglar el espejo (1.1).**
Hoy `force=True` es lo único que compensa el espejo mentiroso. Cambiar el flag primero convertiría un bug latente en pérdida de sincronización silenciosa sobre miles de SKUs.

**5. No confiar en `/api/stock/movimientos/?fecha_desde=` hasta arreglarlo.**
Devuelve `200 / success: true` con lista vacía cuando en realidad no vio los cambios. Un consumidor que lo use para refrescar stock **nunca ve la venta que acaba de ocurrir** y no recibe ninguna señal de error.

**6. No leer la primera subida de la merma como que la operación empeoró.**
Cuando entre en vigor la causa obligatoria, la merma registrada va a pasar de 21 unidades a cientos. **Eso no es deterioro: es que recién se empezó a medir.** Hay que comunicarlo así antes de publicar el primer informe, o el número va a generar la conclusión equivocada.

**7. No paginar `/api/precios-actuales/` sin versionar el contrato.**
Tarda 54,8 s y devuelve 28,85 MB, así que hay que arreglarlo — pero paginar rompe al consumidor actual. Negociar el cambio, no aplicarlo de sorpresa.

**8. No tratar el 21% de descuadre `SUM(movimientos) ≠ stock` como un problema de la sincronización.**
Sobre los SKUs efectivamente publicados el desvío es de 0,5% y va en dirección conservadora. Es un problema real de kardex y valorización, pero **no es la causa de la sobreventa** y arreglarlo no cierra la ventana. No mezclar los dos frentes.

---

## 6. Correcciones a los análisis previos

Tres afirmaciones de los informes de origen resultaron incorrectas al verificarlas. Las dejo anotadas porque son del tipo que se propaga:

| Afirmación | Realidad verificada |
|---|---|
| "La merma de las tiendas Paola no llega a sus canales porque leen de HoldingTebes" | **Falso.** El beat de producción muestra `paola-stock-paris-canal-3`, `paola-stock-walmart-canal-10` y `paola-stock-ecommerce-canal-31` leyendo de RetailMind canal 30 (56 corridas cada uno). Las tareas de HoldingTebes están **deshabilitadas** desde el 2026-05-31. El informe se basó en `settings.py`, que está desactualizado: producción usa `DatabaseScheduler`. |
| "2.043 SKUs publicados con 4.876 unidades fantasma, medido en `VariacionMaster`" | **Tabla equivocada.** Los 7 canales vivos **no leen `VariacionMaster`**: consultan RM en vivo y diffean contra `VariacionCanal.stock_canal`. `VariacionMaster` alimenta `django_ecommerce/publisher.py` (publicación de productos nuevos), donde estar desactualizado desde el 19-jul sí importa, pero el impacto es mucho más acotado que el declarado. |
| "Los SKUs que Paris rechaza nunca se reintentan porque entran en `sin_cambio`" | **Falso para Paris/Ripley/Walmart.** Las cuatro tareas llevan `force: bool = True` por defecto: re-empujan el catálogo completo cada noche. El problema real no es que no se reintenten — es que **se reintentan todas las noches y siguen fallando**, sin que nadie investigue por qué. |

Y una precisión sobre una propuesta: **`stock_seguridad` no es un interruptor**. Existe en el modelo `RelacionCanales` desde la migración inicial, pero `grep -c stock_seguridad system/marketplaces/retailmind/tasks.py` devuelve **0**. Usarlo requiere una línea de código en el bucle del diff — barata, pero código.

---

## 7. Resumen ejecutivo

| # | Problema | Costo medido | Fase |
|---|---|---|---|
| 1 | Clave de API en repo público | Catálogo + costos + ventas de 4 empresas expuestos | 0 |
| 2 | Pedidos pagados no surtibles | **$3.009.703** · 52 pedidos | 1 |
| 3 | Ventana de 8-12 h en 6 de 7 canales | Causa raíz del #2 | 1 |
| 4 | Paris/Ripley rechazan ~33% en silencio | ~5.100 SKUs/noche congelados | 1 |
| 5 | El espejo miente sobre lo aceptado | 13.173 marcados vs 6.101 confirmados | 1 |
| 6 | Sin alertas de sincronización | Semanas de fallas con tablero en verde | 1 |
| 7 | Sin reserva ni colchón de stock | Dos canales venden la misma unidad | 2 |
| 8 | Incremental roto (`updated_at`) | Bloquea el sync frecuente barato | 2 |
| 9 | **Merma no medida** | **21 u declaradas en toda la historia** | 3 |

**La conclusión operativa:** el problema más caro no necesita tocar RetailMind. La Fase 1 completa vive en AllConnected, es mayoritariamente configuración, y la solución **ya está probada en producción con 301 corridas** en el único canal que la tiene.

**La conclusión sobre la merma:** no es que se pierda poco. Es que no se sabe cuánto se pierde. Una tienda contada al 11,5% arrojó 151 unidades faltantes ($1.320.862) que llevan seis meses sin aplicar, mientras el sistema entero declara 21 unidades de pérdida en toda su historia.
