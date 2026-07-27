# Plan AllConnected — qué arreglar, en qué orden y qué no tocar

**Fecha:** 2026-07-27
**Alcance:** el hub omnicanal AllConnected y su vínculo con RetailMind (ERP), en las dos bases de producción.
**Método:** lectura del código que corre (no de READMEs ni docstrings) + consultas de solo lectura a las dos bases de producción. Se recibieron nueve informes previos; **cada cifra que aparece acá fue re-medida por mí**. Donde los informes se contradecían fui al dato y resolví: las correcciones están en la §3.9.
**No se modificó ni un archivo de ninguno de los dos repos. Ninguna consulta escribió.**

Documento previo, complementario (no se repite acá): `docs/ANALISIS_SYNC_STOCK_ALLCONNECTED.md`.

---

## 1. Veredicto en una página

# FRÁGIL, con una zona ROTA y una puerta abierta

**¿Se puede confiar en este hub?** Depende de para qué, y la respuesta hay que darla partida en tres:

| Frente | Veredicto | Por qué |
|---|---|---|
| **Stock que se publica** | **Frágil pero funcional** | El número es correcto de madrugada y se degrada durante el día. Medido hoy a media tarde: entre 99 y 639 SKUs con stock fantasma por canal de marketplace, contra **10 SKUs** en el único canal con sync horario. Funciona; está grueso. |
| **Pedidos hacia el ERP** | **Roto para marketplaces** | Los pedidos de Paris, Ripley y Walmart tardan **una mediana de 17 a 19 horas** en llegar a RetailMind. Los de las tiendas propias tardan **0,34 h**. La diferencia no es de infraestructura: es que a los tres marketplaces nadie les cableó la llamada. |
| **Catálogo y precios** | **Roto** | La importación nocturna del catálogo **no deja rastro desde el 20-jul (Realsport) y desde el 29-may (Paola)**. Hay $44,7 M de mercadería de Paola que el hub literalmente no puede ver. Nada propaga precios: toda la operación de precios de julio se hizo con scripts sueltos a mano. |
| **Seguridad** | **Puerta abierta** | `/accounts/register/` está abierto: cualquiera se crea una cuenta y entra al dashboard. Además hay **174 rutas no-admin sin ninguna verificación**, dos de ellas de escritura y anónimas. |

**La buena noticia, que importa tanto como lo anterior:** el transporte está sano. La API de RetailMind responde bien, el pull de stock tiene defensas correctas contra respuestas parciales, el webhook de facturación funciona con latencia de décimas de segundo, la ingesta de pedidos es idempotente de verdad (604 pedidos Paris = 604 números distintos, cero duplicados) y **el patrón correcto ya está implementado y probado en producción** en los dos ecommerce propios. Casi todo lo que hay que hacer es copiar algo que ya funciona al lado.

**La mala noticia estructural:** nada de todo esto avisa cuando falla. Paola lleva 59 días sin catálogo, Paris rechaza 2.960 SKUs cada noche desde el 30-jun, cuatro tareas de precios corren 59 veces cada una sin escribir jamás una fila — y los tableros están todos en verde. **El sistema no tiene un solo watchdog de stock ni de catálogo.**

### Dónde está la plata (últimos 90 días, medido)

| Canal | Pedidos | Monto | Sync de stock | Espejo de catálogo |
|---|---:|---:|---|---|
| Paris CalzadosPaola (3) | 596 | **$20.003.814** | diaria 01:30 | **59 días de atraso** |
| realsport.cl (29) | 340 | $18.190.207 | **horaria** ✅ | 8 días |
| Paris Realsport (4) | 313 | $16.454.191 | diaria 23:05 | 8 días |
| RIPLEY REALSPORT (6) | 231 | $14.189.320 | diaria 23:10 | 8 días |
| calzadospaola.cl (31) | 145 | $5.337.315 | diaria 00:50 | **59 días** |
| Wallmart CalzadosPaola (10) | 69 | $2.291.593 | diaria 02:35 | **59 días** |
| Wallmart Realsport (27) | 25 | $935.457 | diaria 23:20 | 8 días |
| Shopify Realsport (2) / Paola (1) | 40 | $2.167.784 | **apagado** | — canal muerto |
| Ripley CalzadosPaola (5) | **0** | $0 | **ninguna** | último pedido **2025-11-20** |

**$77,4 M en 90 días ≈ $25,8 M/mes** pasan por este hub. De eso, **$53,9 M (70 %) entra por los tres marketplaces que dependen de que alguien apriete un botón** para que el pedido llegue al ERP.

---

## 2. Cómo funciona hoy, de verdad

Lo documentado y lo que corre no coinciden. Este es el mapa real, verificado en el código vivo y en `django_celery_beat_periodictask` de producción.

```
┌──────────────────────────────────── RETAILMIND (ERP) — fuente de verdad ────────────────────────────────────┐
│  Producto_Talla.stock (stock plano)   ·   Producto.precioventa   ·   PedidoEcommerce   ·   DTE/boletas       │
└───┬──────────────▲────────────────────────▲──────────────────────────▲──────────────────────┬───────────────┘
    │              │                        │                          │                      │
 (A) PULL       (B) PUSH                 (C) PULL                   (D) PUSH               (E) PUSH
 de stock       de pedidos               de pedidos                 factura                de stock
 y catálogo     AC→RM                    RM→AC (botón)              RM→AC                  tiempo real
    │              │                        │                          │                      │
    ▼              │                        │                          ▼                      ▼
┌────────────────────────────── ALLCONNECTED (hub) ───────────────────────────────────────────────────────────┐
│                                                                                                             │
│  A) STOCK  ── VIVO, es el canal principal ────────────────────────────────────────────────────────────────  │
│     beat → sync_paris / sync_ripley / sync_walmart / sync_django_ecommerce                                  │
│     1. lee espejo local VariacionCanal.stock_canal del canal destino                                        │
│     2. POST /api/stock/global/ a RM en chunks de 5.000 SKUs  (en vivo, NO usa el espejo de catálogo)         │
│     3. diff en memoria: stock_target = max(0, stock_rm).  SIN colchón, SIN reserva, SIN stock comprometido   │
│     4. push al canal  +  escribe el espejo local                                                            │
│     ⚠ el join RM↔canal es por IGUALDAD DE STRING DE SKU numérico. Nada más.                                 │
│     ⏱ Paris RS 17-21 s · Paris PAO 31-37 s · Ripley 22-29 s · Walmart 1,5-8,5 s · ecommerce 1,4-2,3 s       │
│     📅 6 de 7 canales: 1 vez al día (23:05 → 02:35).  realsport.cl: cada hora 08-22.                        │
│                                                                                                             │
│  A') CATÁLOGO ── MUERTO ──────────────────────────────────────────────────────────────────────────────────  │
│     beat 23:00/23:45 → retailmind.importar_completo → GET /api/skus/ → ArticuloRetailMind + SkuRetailMind    │
│     ❌ canal 28 sin una sola corrida desde 2026-07-20 · canal 30 desde 2026-05-29. Beat dispara igual.       │
│     La identidad del artículo es `articulo||marca||color||genero||CATEGORIA` (client.py:624)                 │
│                                                                                                             │
│  A'') PRECIOS ── NO EXISTE PROPAGACIÓN ──────────────────────────────────────────────────────────────────    │
│     ProductoCanal.precio_venta es un LIBRO LOCAL. Nada lo copia desde el ERP, en ninguna dirección.          │
│     Se escribe con: el Excel de descuentos, el editor individual, la lectura de vuelta del marketplace,      │
│     y — lo que realmente pasó en julio — scripts sueltos de la raíz corridos a mano.                         │
│                                                                                                             │
│  B) PEDIDOS AC→RM ── VIVO SOLO PARA LOS DOS ECOMMERCE PROPIOS ────────────────────────────────────────────   │
│     django_ecommerce/order_sync.py:267 → notificar_retailmind → asignar_ticket_rm → POST /app/api/…/pedidos/ │
│     ✅ mediana 0,33-0,35 h    ❌ Paris/Ripley/Walmart: la ruta que corre no llama a nadie → 17-19 h          │
│                                                                                                             │
│  C) PEDIDOS RM→AC ── VIVO PERO MANUAL ────────────────────────────────────────────────────────────────────   │
│     GET /app/pedidos/pendientes/ disparado por un BOTÓN en RetailMind. Es lo que rescata los marketplaces.   │
│     El ERP nunca devuelve el numero_ticket_rm → la cola "pendiente" nunca drena: 25.690 filas desde 2023.    │
│                                                                                                             │
│  D) FACTURAS RM→AC ── VIVO Y SANO ────────────────────────────────────────────────────────────────────────   │
│     POST /system/webhooks/retailmind/factura/  ·  5.953 eventos desde 2026-06-12  ·  latencia p50 0,22 s     │
│     ⚠ 5.182 de esos eventos (87 %) son boletas de mostrador que no pueden matchear nunca.                    │
│                                                                                                             │
│  E) STOCK TIEMPO REAL RM→AC ── NUNCA OPERÓ ───────────────────────────────────────────────────────────────   │
│     El receptor existe y está completo. En toda la historia: 39.137 eventos STOCK_UPDATE, CERO del canal     │
│     28 o 30, y se cortan el 2026-05-31 (era HoldingTebes/Laravel). Falta la variable en el ERP.              │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
       │            │             │            │              │
       ▼            ▼             ▼            ▼              ▼
    Paris       Ripley        Walmart     realsport.cl   calzadospaola.cl
   (3 y 4)      (5 y 6)      (10 y 27)        (29)             (31)
```

### Las tres cosas que hay que entender para no equivocarse

1. **El stock NO usa el espejo de catálogo.** Pide a RetailMind en vivo y matchea por SKU numérico. Por eso la identidad del catálogo se rompió entera sin apagar una sola publicación. Es la decisión de diseño que salvó el negocio estas semanas.
2. **El precio del canal no es el precio del ERP y nunca lo fue.** No hay un "sync de precios roto": no hay sync de precios. Hay un libro local que se edita con Excel y scripts.
3. **La única infraestructura que serializa el trabajo es un worker con `--concurrency=1`.** No hay locks en ninguna de las 4 tareas de stock. Hoy no se pisan por suerte, no por diseño.

---

## 3. Los problemas ordenados por plata

### 3.1 · Registro público abierto + 174 rutas sin ninguna verificación — **CRÍTICO**

`/accounts/register/` (`system/auth_views.py:161`, ruta en `vicentEcommerces/urls.py:54`) crea el usuario con `create_user`, lo loguea y lo redirige a `/system/dashboard/`. Sin invitación, sin dominio permitido, sin aprobación.

Barrido propio del URLconf (715 rutas, 548 fuera de `/admin/`): **249 sin decorador de autenticación, y 174 de esas sin ningún chequeo dentro del cuerpo tampoco**. Distribución: `system/api_views.py` 86, `system/webhooks/views.py` 18, `walmart/api/views.py` 14, `system/views.py` 13, `shopify/views.py` 11.

Lo que más duele no son las de lectura, son **dos escrituras anónimas**:

| Endpoint | Archivo | Qué hace sin credencial |
|---|---|---|
| `POST /system/relacion/eliminar/` | `system/views.py:2300` (`csrf_exempt`, sin auth) | Borra una `RelacionCanales` por id → **apaga el sync de stock de un marketplace** |
| `POST /system/relacion/toggle-activo/` | `system/views.py:2312` (`csrf_exempt`, sin auth) | Desactiva la misma relación |
| `POST /system/api/canal/<id>/paris/push-stock/` | `system/api_views.py:1455` (`csrf_exempt`, sin auth) | Empuja stock arbitrario a Paris |

Son 16 relaciones: enumerables con un `for` de 1 a 30.

**Costo si se explota:** todo el negocio online, $25,8 M/mes. Y `sys_canal` guarda `api_key`, `api_secret` y `access_token` de Shopify, Paris, Ripley y Walmart — un usuario registrado por la puerta abierta queda dentro del panel.

**Dato que baja la urgencia de "incendio" a "cerrar hoy":** la tabla de usuarios tiene **9 cuentas, ninguna creada en los últimos 90 días, la última del 2025-08-07**. Nadie ha usado la puerta todavía.

### 3.2 · Los pedidos de marketplace tardan 17-19 h en llegar al ERP — **CRÍTICO**

Medición propia cruzando las dos bases por `numero_pedido_canal`, últimos 45 días:

| Canal | n | Mediana | p90 | Máximo | Nunca llegó |
|---|---:|---:|---:|---:|---:|
| Paris Realsport | 154 | **19,43 h** | 43,66 h | 74,25 h | 0 |
| Paris CalzadosPaola | 212 | **19,04 h** | 44,04 h | 73,08 h | 0 |
| Wallmart Realsport | 14 | **19,28 h** | 22,63 h | 42,10 h | 0 |
| RIPLEY REALSPORT | 89 | **17,17 h** | 40,02 h | 62,02 h | **3** |
| Wallmart CalzadosPaola | 47 | **16,49 h** | 38,62 h | 46,37 h | 0 |
| CALZADOSPAOLA DJANGO | 112 | **0,35 h** | 16,87 h | 19,45 h | 1 |
| realsport.cl | 135 | **0,33 h** | 19,08 h | 44,64 h | 1 |

**Causa raíz, verificada:** `ParisOrderSyncService.notificar_retailmind()` existe (`paris/order_sync.py:298`) y sus gemelos en ripley y walmart, pero **la ruta que corre es otra**: beat → `sync_paris_orders_task` → `_sincronizar_pedidos_paris_historicos` (`system/orders/api/views.py:4566`, y `:5402` / `:6040` para Ripley y Walmart). Esas tres funciones crean el `Pedido` y **nunca llaman a `asignar_ticket_rm`**. Solo `django_ecommerce/order_sync.py:267` lo hace.

**Prueba en los datos:** de 519 pedidos de marketplace de los últimos 45 días, **todos tienen `estado_envio_erp='PENDIENTE'` con `intentos_envio_erp=0`** — nunca se intentó. Los 223 de los ecommerce propios están en `OK` (o `ERROR` tras intentarlo).

**El detalle que abarata el arreglo:** ya existe una tarea de reintento cada 30 min (`reintentar_envio_retailmind_task`, `system/orders/tasks.py:975`, 1.575 corridas) que hace exactamente lo que falta — pero su filtro es `estado_envio_erp='ERROR'`, y los pedidos de marketplace están en `'PENDIENTE'`. **Incluir `PENDIENTE` dentro de la ventana de 48 h es una línea.** Hoy eso alcanzaría a 34 pedidos.

**Costo:** $53,9 M en 90 días entrando por el camino lento. El pedido pasa 17-19 h sin existir en el ERP: no descuenta stock, no aparece para preparar, y el stock que se publica esa noche todavía lo cuenta como disponible. Además **4 pedidos de 45 días nunca llegaron**, y hay **26 pedidos en `ERROR` permanente por $764.508** (timeouts y HTTP 500 del ERP del 14 al 19-jun) que dejaron de reintentarse al pasar la ventana.

### 3.3 · La importación de catálogo está muerta: 59 días en Paola, 8 en Realsport — **CRÍTICO**

| Canal | Última corrida con registro | Último artículo escrito | Último SKU escrito |
|---|---|---|---|
| 28 · RETAILMIND-REALSPORT | 2026-07-20 03:00 (COMPLETADA) | 2026-07-19 | 2026-07-14 |
| 30 · RETAILMIND-PAOLA | **2026-05-29 15:17** (única COMPLETADA de su historia) | **2026-05-29** | **2026-05-29** |

Beat dispara las dos todas las noches (59 corridas cada una, última hoy 03:00 y 03:45 UTC). Los workers están vivos: el sync de stock de las 23:05 se registra el mismo día.

**Causa raíz — la encontré, y no es la que decían los informes.** Dos evidencias que encajan:

1. Las filas atascadas de canal 30 (**2.950 en `EN_PROGRESO`**) están todas paradas en el mismo mensaje: **`"Cargando artículos existentes…"`**. Esa línea (`services.py:1106-1117`) hace, sin `defer` ni `iterator`:
   ```python
   existentes_art = {o.codigo_articulo: o for o in ArticuloRetailMind.objects.filter(canal_origen=self.canal)}
   existentes_sku = {o.codigo_sku:      o for o in SkuRetailMind.objects.filter(canal_origen=self.canal)}
   ```
   Para Paola son **121.138 instancias Django**, cada una con un `datos_raw_api` que pesa **107 MB solo comprimido en Postgres** (`avg 1.126 bytes/fila`). Deserializado a dicts de Python es varias veces eso.
2. El worker corre con **`--max-memory-per-child=450000`** (450 MB) y `--concurrency=1` (`start_workers.sh`), tres procesos en un contenedor de 2 GB. Y la tarea lleva **`acks_late=False`** explícito (`tasks.py:156-157`): si el hijo se recicla a mitad, **la tarea se pierde sin reintento y sin dejar rastro**.

Encaja con el orden de muerte: **Paola (99.480 SKUs) murió primero, el 29-may. Realsport (74.105 SKUs) aguantó hasta el 20-jul — justo después de que la recategorización le agregara 10.608 artículos nuevos el 19-jul** y con ellos ~11 MB más de JSON en el prefetch.

Las 4 filas en `ERROR` que sí quedaron registradas confirman el otro borde: **`HTTP 504`** y **`Timeout después de 30s (intento 3)`** contra `/api/skus/`, con `RetailMindClient.TIMEOUT = 30`.

**Encima, la import borra su propia evidencia por diseño:** el registro `SincronizacionRetailMind` se crea **dentro** del `transaction.atomic()` y **después** de la etapa de snapshot de precios (`tasks.py:202-209`); y las estadísticas nunca se persisten porque `finalizar()` hace `save(update_fields=['estado','progreso','fecha_fin'])`. **En 10.087 corridas históricas: 0 con `articulos_nuevos>0`, 0 con `articulos_actualizados>0`.**

**Costo medido, cruzando las dos bases hoy:**

| | Realsport (28) | CalzadosPaola (30) |
|---|---:|---:|
| SKUs en el ERP | 74.106 | 100.084 |
| SKUs en el espejo | 74.105 | 99.480 |
| **Ausentes del espejo** | 1 | **604** |
| — de esos, con stock | 0 | **551 · 1.309 unidades** |
| — **valor a precio de venta** | $0 | **$44.743.910** |
| SKUs con precio desactualizado en el espejo | 423 (0,6 %) | **3.695 (3,7 %)** |
| SKUs con costo desactualizado | 288 | 1.006 |

**$44,7 M de mercadería de Paola no se pueden publicar en ningún canal**, porque el hub no sabe que existen. Y toda la herramienta de precios de Paola (que lee `ArticuloRetailMind.precio_base`) corre sobre una foto del 29 de mayo.

### 3.4 · El espejo se marca sincronizado con lo que Paris rechazó — **ALTO**

Corridas de stock, medición propia sobre las últimas 8 noches:

| Canal | Payload | Aplicados | **Rechazados** | % | Filas que igual se marcan OK |
|---|---:|---:|---:|---:|---:|
| Paris Realsport (4) | 9.061 | 6.101 | **2.960** | **32,7 %** | **13.173** |
| Paris CalzadosPaola (3) | 17.598 | 15.431 | **2.167** | **12,3 %** | **24.630** |
| RIPLEY REALSPORT (6) | 10.159 | 10.159 | 0 | 0 % | 10.159 |
| Wallmart Realsport (27) | 7.224 | 7.224 | 0 | 0 % | (lo escribe el monitor) |
| Wallmart CalzadosPaola (10) | 16.053 | 16.053 | 0 | 0 % | (idem) |

Los números de Paris son **idénticos noche tras noche** (2.960 exactos siete noches seguidas): el rechazo es estructural, no transitorio.

`sync_retailmind_paris_task` escribe `VariacionCanal.stock_canal` para **todo el payload** con la condición `if paris_ok and payload` (`tasks.py:3765-3779`), donde `paris_ok` significa "ningún lote dio error HTTP", no "Paris aplicó el SKU".

**Y el dato exacto está a mano y se tira:** `actualizar_stock_por_sku_seller` devuelve `stock_info` = la lista de SKUs que Paris confirmó (`paris/views.py:1373-1378`); la tarea lee solo el contador `updated` y descarta la lista.

**Hoy el daño está tapado** porque las tareas corren con `force=True` y re-empujan todo. **El día que se ponga `force=false` —que es lo natural al subir la cadencia— esos 5.127 SKUs quedan congelados en Paris para siempre y en silencio.** Es el prerrequisito de todo lo demás.

Ripley, en cambio, lo hace bien: excluye del update local lo que Mirakl rechazó (`tasks.py:4115-4119`). El patrón correcto ya está escrito en el mismo archivo.

### 3.5 · Stock fantasma: la medición que justifica la cadencia horaria — **ALTO**

Comparación SKU a SKU del espejo de cada canal contra el stock vivo del ERP, hecha hoy a media tarde (o sea, con 14-16 h de deriva desde la última corrida nocturna):

| Canal | SKUs publicados | Iguales al ERP | **Fantasma** (canal > ERP) | **Unidades fantasma** | Sub-publicado |
|---|---:|---:|---:|---:|---:|
| Paris Realsport (4) | 5.278 | 4.638 | **639 (12,1 %)** | **1.635** | 0 |
| Paris CalzadosPaola (3) | 15.303 | 14.820 | **475** | **1.526** | 2 |
| RIPLEY REALSPORT (6) | 9.892 | 9.716 | 174 | 290 | 2 |
| Wallmart CalzadosPaola (10) | 16.034 | 15.866 | 166 | 614 | 2 |
| Wallmart Realsport (27) | 7.184 | 7.085 | 99 | 141 | 0 |
| calzadospaola.cl (31) — diario | 15.572 | 15.527 | 43 | 43 | 2 |
| **realsport.cl (29) — HORARIO** | 11.619 | 11.604 | **10** | **28** | 0 |
| *Ripley CalzadosPaola (5) — muerto* | *19.277* | *16.647* | *2.208* | *3.203* | *398* |
| *Shopify Paola (1) / Realsport (2) — muertos* | *24.179* | *19.843* | *3.801* | *6.426* | *534* |

**Esta tabla es el argumento entero para la Fase 2**, y está medida, no estimada: el canal horario tiene **10 SKUs fantasma**; el canal diario del mismo tipo tiene 43; los marketplaces diarios tienen entre 99 y 639.

**Daño realizado, medido en el ERP:** **50 pedidos pagados por $2.921.733 tienen al menos un ítem cuyo SKU no tiene una sola unidad en toda la empresa**, y siguen en `PENDIENTE`. Reparto por mes: **junio 42 pedidos / $2.514.941 · julio 7 / $337.802 · abril 1**. Junio fue un pico anómalo (coincide con el lanzamiento de realsport.cl el 03-jun y con 98 cancelaciones por $4,9 M ese mes); julio corre a ~3 % de los pedidos. **La cifra viva no son $3 M mensuales: son ~$340 k al mes más un atasco de junio de $2,5 M que nadie ha limpiado.**

Ojo con la lectura fácil: **el canal horario es el que más pedidos no surtibles acumula (19)**. La cadencia reduce el fantasma, no lo elimina, porque **no hay ninguna reserva de stock**: un pedido pagado no bloquea la unidad en el ERP, y dos canales pueden vender —y venden— la misma.

### 3.6 · Precios: no hay propagación, hay Excel y scripts — **ALTO**

No existe una sola línea que copie `Producto.precioventa` a `ProductoCanal.precio_venta`. Verificado por barrido de todas las escrituras al campo.

Medición propia: de los productos publicados cuyo precio cambió en el ERP en los últimos 60 días, **cuántos siguen con exactamente el precio anterior**:

| Canal | Productos con cambio de precio (60 d) | **Congelados en el precio viejo** | Alineados al nuevo |
|---|---:|---:|---:|
| Paris CalzadosPaola | 881 | **767 (87 %)** | 15 |
| Wallmart CalzadosPaola | 1.082 | **764 (71 %)** | 25 |
| CALZADOSPAOLA DJANGO | 900 | **558 (62 %)** | 15 |
| RIPLEY REALSPORT | 973 | 362 (37 %) | 86 |
| Wallmart Realsport | 825 | 297 (36 %) | 82 |
| realsport.cl | 913 | 283 (31 %) | 131 |
| Paris Realsport | 607 | 271 (45 %) | 220 |

**Pero la dirección importa y corrige el informe previo.** A nivel de publicación (no de fila de SKU), el desglose real es:

| Situación | Publicaciones | Qué significa |
|---|---:|---|
| El ERP **bajó** el precio y el canal quedó caro | **459** | **Venta perdida**: online más caro que la tienda tras una rebaja |
| El ERP **subió** el precio y el canal quedó barato | **28** | Margen perdido (real, pero marginal) |
| Publicación por debajo del **costo directo** | **9** | 8 en Paris CalzadosPaola |

**Descarto la cifra de "$30.968.000 de brecha" del informe previo** (ver §3.9). El problema de precios es un problema de **proceso** (todo es manual y no queda registro), no un agujero de $31 M.

**Y hay cuatro tareas de verificación de precios que corren todas las mañanas y no hacen absolutamente nada:**

- `sincronizar_precios_walmart_task` filtra `tipo_marketplace__codigo='WALMART'` (`system/precios/tasks.py:676`) pero **en la base el código es `'WALLMART'`, con doble L**. La relación sale `None` y la tarea retorna en la primera línea. 59 corridas × 2 canales.
- `sincronizar_precios_ripley_task` indexa por `pc.sku_canal` (id de producto Mirakl) y lo busca contra un mapa que viene por `shop_sku` (SKU de variante, numérico): **cero coincidencias**. 59 corridas × 2 canales.
- **Prueba definitiva: 0 de 26.181 `ProductoCanal` tienen `ultima_lectura_precio`, `precio_marketplace` o `discrepancia_precio` en `metadatos_canal`.** La verificación automática de precios nunca ha detectado nada en toda la vida del sistema.
- `verificar-precios-retailmind-cada-hora` (la que compararía contra el ERP) está `enabled=False` con **0 corridas**.

### 3.7 · La identidad del catálogo se rompió entera con la recategorización — **ALTO (latente)**

`codigo_articulo` es `articulo||marca||color||genero||CATEGORIA` (`client.py:624`). La recategorización v1.2 del ERP reescribió la categoría de casi todo el catálogo → cada producto pasó a tener una clave nueva → el import crea un artículo nuevo y el viejo queda huérfano con sus vínculos, su guía de talla y su `sku_canal` publicado.

Medición propia, recalculando la clave desde el ERP vivo:

| | Realsport (28) | CalzadosPaola (30) |
|---|---:|---:|
| Artículos en el espejo | 30.114 | 21.658 |
| **Huérfanos (clave que el ERP ya no emite)** | **19.706** | **21.454** |
| De esos, huérfanos "duros" (el producto ya no existe ni por los 4 primeros campos) | 66 | 245 |
| Vínculos vivos | 5.461 | 10.623 |
| **Vínculos que cuelgan de una identidad muerta** | **5.450** | **10.622** |

**16.072 de 16.084 vínculos vivos (99,9 %) apuntan a una identidad que el ERP ya no emite.** Solo 134 son huérfanos reales.

**Por qué esto todavía no ha roto nada:** el stock viaja por SKU numérico y no toca esta cadena. Lo que sí queda inutilizable es **toda operación de catálogo**: publicar, actualizar nombre o foto, asignar guía de talla, despublicar, auditar.

Efecto colateral verificado: **10.859 artículos del canal 28 (36 %) no tienen ni un SKU hijo** y declaran 31.718 tallas que no existen, porque el update de SKU nunca reasigna `articulo_id` (`services.py:1263-1291`). Matiz importante que corrige el informe previo: **ningún vínculo vivo apunta a esos artículos**, así que hoy no rompen nada — son una mina para cuando alguien intente publicar desde ahí (saldría un producto sin variantes, imposible de comprar).

### 3.8 · El resto, en orden

| # | Problema | Cifra medida | Gravedad |
|---|---|---|---|
| a | **Cero watchdogs de stock y catálogo.** Los tres que existen cubren pedidos. `_finalizar_importacion_stock` marca COMPLETADO sin mirar `fallidas`. | Paris rechaza desde el 30-jun, Paola sin catálogo desde el 29-may, 4 tareas de precios inútiles desde siempre: todo en verde | ALTO |
| b | **Filas duplicadas en el espejo por (canal, SKU).** El dict del diff se arma sin `order_by`: el valor que gana es el que devuelva Postgres. | canal 3: 6.144 duplicadas · canal 4: 2.397 · canal 10: 1.289 · canal 2: 486 | MEDIO |
| c | **Cero locks en las 4 tareas de stock**, con 4 endpoints manuales que las disparan. La única protección es `--concurrency=1`. El patrón de lock ya existe en el repo, aplicado a publicación (`tasks.py:4882`). | ciclo leer→push→escribir de hasta 37 s sin transacción | MEDIO |
| d | **Ripley excluye 5.943 SKUs por un flag de 7 días**, 2.213 de ellos con stock. Es un diseño deliberado y razonable, pero nadie mide el número. | **6.234 unidades** fuera del canal | MEDIO |
| e | **El webhook de facturas es sano pero 87 % ruido**, y 51 anulaciones de ecommerce no encontraron su pedido. | 5.182 de 5.953 eventos son boletas de mostrador · **51 notas de crédito** donde AC sigue creyendo que el pedido está facturado | MEDIO |
| f | **La ruta del ecommerce traduce "RM no devolvió este SKU" a stock 0** (`tasks.py:4689` y `:4710-4714`), y es la única sin el guard de cero masivo que sí tiene Walmart. Las otras tres heredan la defensa correcta de `_pull_stock_rm_global`. | latente: `sin_match=0` hoy, pero expone 11.720 SKUs de canal 29 15 veces al día | MEDIO |
| g | **El canal horario gasta el 83 % de cada corrida en 12 artículos zombis** que fallan hace semanas, y nunca excluye los `not_found_variants` (46 por corrida) del update local. | 12 de 12-16 productos por corrida · trabajo real: 0 a 4 productos/hora | MEDIO |
| h | **`stock_seguridad` está en el formulario de relaciones y no lo lee nadie** en la ruta de RetailMind. Las 16 relaciones valen 0. | poner un colchón desde la UI hoy no tiene ningún efecto | MEDIO |
| i | **Canales muertos consumiendo trabajo y ensuciando toda medición.** | Ripley CalzadosPaola (5): último pedido **2025-11-20**, con 3.260 publicaciones, 19.291 filas de espejo y **3.214 vínculos** · Shopify (1 y 2): últimos pedidos may/jun | MEDIO |
| j | **La cola "pendiente de ERP" nunca drena**: el ERP ingesta y no devuelve el `numero_ticket_rm`. | 25.690 filas, la más antigua de **2023-11-20** | BAJO (sin plata: la ingesta es idempotente) |
| k | **11 corridas de stock colgadas en PROCESANDO** en 35 días, sin una alerta. | Ripley 4, Paris 3, Realsport 6 | BAJO |
| l | **RetailMind corre en producción con `DEBUG=True`.** Su `settings.py:33` no tiene override de ambiente; AllConnected sí lo tiene (`settings.py:76-89`, fuerza `False` en producción). | corrobora el dato: AllConnected capturó respuestas `HTTP 500` del ERP con la página HTML de Django, no el "Server Error" plano | ALTO (fuera de este frente, ya está en `docs/SEGURIDAD_URGENTE_2026-07-25.md`) |
| m | **Dos validadores de entrada de AllConnected fallan abiertos**: `_validar_key_stock_retailmind` (`webhooks/views.py:56-57`) devuelve `True` si no hay ninguna clave configurada; y el pull de pedidos acepta la clave **por querystring** (`?api_key=`) y compara con `!=` en vez de `compare_digest` (`orders/retailmind_connector.py:529-532`). El lado RetailMind está bien: falla cerrado, solo header, `compare_digest`. | un deploy sin la variable deja la puerta abierta sin que nada avise | MEDIO |

### 3.9 · Descartado — lo que NO hay que perseguir

Estos hallazgos vinieron en los informes y **no sobreviven a la verificación**. Los dejo escritos para que nadie los reviva:

| Afirmación | Qué verifiqué | Veredicto |
|---|---|---|
| "$30.968.000 de brecha de precios · 4.180 SKUs más caros online" | La cifra suma diferencias unitarias por **fila de SKU**, y el espejo tiene hasta 6.144 filas duplicadas por canal. A nivel de publicación son **459 casos** de "online más caro tras una rebaja". | **Descartado como cifra.** El problema de proceso es real; el monto no. |
| "684 publicaciones bajo el piso de margen, 19 bajo costo" | El "piso de margen" (1,64× costo) es una constante de un script suelto, no una política del negocio. Bajo **costo directo**, deduplicando por publicación: **9**. | **Redimensionado a 9.** |
| "La gestión de canales está abierta a anónimos" | `CanalCreateView`, `CanalUpdateView` y `CanalDeleteView` **sí** están protegidas con `@method_decorator(user_passes_test(is_superuser or grupo Administrador))`. El detector del informe no ve `method_decorator` sobre clases. | **Falso.** Las credenciales de marketplace no están expuestas por ahí. |
| "Ripley rechaza 4.653 SKUs todas las noches en silencio" | Es una **exclusión deliberada de 7 días** por el flag `sin_oferta_mirakl`. Las corridas del 26 y 27-jul reportan `fallidas: 0` y `fallas_reales: 0`. | **Falso.** Lo real es que 6.234 unidades quedan fuera del canal y nadie tiene el número. |
| "Los canales de Paola leen de HoldingTebes en vez de RetailMind" | Ver §5. Las tareas HoldingTebes están **deshabilitadas desde 2026-05-31**; los canales 3, 10 y 31 leen de RetailMind canal 30 (57 corridas cada uno); y **no existe ni un vínculo de artículos de Paola hacia el canal Shopify**. | **Falso / obsoleto.** |
| "AllConnected corre con DEBUG=True en producción" | `settings.py:76-89` fuerza `DEBUG=False` en ambiente productivo, y la respuesta trae HSTS + X-Frame-Options. El `.env` con `DEBUG=True` es la config local. **El que sí corre con DEBUG es RetailMind.** | **Invertido.** |
| "Los 10.859 artículos sin SKUs rompen las publicaciones" | **Cero vínculos vivos** apuntan a ellos. | **Riesgo futuro, no daño actual.** |
| "25.690 pedidos en cola perpetua corrompen datos" | La ingesta del ERP es idempotente: 604 filas Paris = 604 `numero_pedido_canal` distintos, sin duplicados. | **Real pero sin plata.** Es un indicador inservible, no una pérdida. |
| "El colchón `stock_seguridad` es un toggle que hay que activar" | 0 ocurrencias en toda la ruta de RetailMind. Requiere código. | **Precisión, no descarte.** |

---

## 4. Plan por fases

Ordenado por **impacto sobre esfuerzo**, no por prolijidad técnica. Cada fase dice qué NO tocar mientras corre.

---

### FASE 0 — Hoy, sin desplegar una línea de código

Todo lo de acá es configuración, variables de entorno o filas de base de datos.

| # | Acción | Dónde | Verificación | Riesgo |
|---|---|---|---|---|
| **0.1** | **Bloquear `/accounts/register/` en el proxy/WAF** (Cloudflare o DigitalOcean), devolviendo 403. | Config del proxy. Ningún archivo. | La ruta devuelve 403; `auth_user` sigue en 9 filas. | Nulo — nadie legítimo se registra desde ahí (última alta: 2025-08-07). |
| **0.2** | **`DEBUG=False` en el ambiente de RetailMind** + revisar `ALLOWED_HOSTS`. | Variables de entorno del ERP. | Un 404 en el ERP deja de mostrar la página técnica de Django. | Si `ALLOWED_HOSTS` queda mal, el sitio devuelve 400 a todo. Probar `/api/health/` antes de cortar tráfico. |
| **0.3** | **Subir la cadencia de `calzadospaola.cl` (canal 31) a horaria**, copiando exactamente los kwargs del canal 29 (`{"canal_retailmind_id":30,"canal_ecom_id":31,"force":false}`, cron `0 8-22`). | Una fila en `django_celery_beat_periodictask`. | El fantasma del canal 31 baja de 43 SKUs hacia el rango del canal 29 (10). | Bajo: usa la ruta de ecommerce, que **sí** excluye los `not_found_products` del update local. Es la única cadencia que se puede subir sin arreglar antes el espejo. |
| **0.4** | **Desactivar las relaciones de canales muertos**: las 6 que salen de los canales origen 1 y 2 (Shopify/HoldingTebes) y la 18 (RM Paola → Shopify Paola). Dejar activas solo las 9 que salen de 28 y 30 hacia canales vivos. | 7 filas en `sys_relacion_canales`. | Las corridas de descuentos dejan de enumerar canales muertos. | Bajo, y **reversible con un UPDATE**. Ver §5. |
| **0.5** | **Decidir sobre Ripley CalzadosPaola (canal 5).** Sin pedidos desde el **2025-11-20**, con 3.260 publicaciones y 3.214 vínculos que ensucian toda medición de "sin enlazar". Confirmar con Ripley si la tienda existe; si no, marcar el canal como INACTIVO en las herramientas. | Confirmación comercial + estado del canal. | El denominador de "publicaciones sin sync" baja de 19.277 filas. | **No borrar publicaciones todavía** — solo excluir el canal de las mediciones. |
| **0.6** | **Limpiar los 26 pedidos en `ERROR` y los 50 no surtibles a mano.** Son de junio, llevan 40 días atascados y ninguna tarea los va a tomar. | Operación, no código. | `estado_envio_erp='ERROR'` baja de 26 a 0; los `PENDIENTE` no surtibles de junio se cancelan o se surten. | Nulo. |

**Qué NO tocar en Fase 0:** no subir a horaria ninguna cadencia de Paris, Ripley o Walmart (ver 1.1). No borrar filas de `sys_producto_canal` ni de `sys_variacion_canal`.

---

### FASE 1 — La semana que viene · solo AllConnected, no toca RetailMind

Esta fase no interfiere con los agentes que están trabajando en el ERP.

#### 1.1 · Escribir el espejo solo con lo que el canal confirmó ← **prerrequisito, no opcional**

- **Archivos:** `system/marketplaces/retailmind/tasks.py:3765-3779` (Paris) y `:4247-4260` (monitor de Walmart).
- **Qué:** recorrer `res_paris['results'][i]['result']['stock_info']`, armar el set de SKUs aceptados y pasarle **solo ese subconjunto** a `_bulk_update_stock_local_por_canal`. Loguear los rechazados. En Walmart, no escribir el espejo si `items_failed > 0`. Ripley ya lo hace bien (`tasks.py:4115-4119`) — copiar ese patrón.
- **Esfuerzo:** bajo. El dato ya viaja en la respuesta y se descarta.
- **Verificación:** `bd_local.variaciones_canal ≤ exitosas` en toda corrida. Hoy es **13.173 vs 6.101**.
- **Por qué primero:** con `force=True` el bug está tapado. Al bajar a horario con `force=false` se destapa y empieza a congelar SKUs en silencio.

#### 1.2 · Que los pedidos de marketplace lleguen al ERP en minutos

- **Paso A (una línea, se puede hacer ya):** en `reintentar_envio_retailmind_task` (`system/orders/tasks.py:975-981`), incluir `estado_envio_erp='PENDIENTE'` además de `'ERROR'`, manteniendo la ventana de 48 h y el tope de intentos. Con eso, cada 30 min se recogen los pedidos que la ruta de marketplace deja sin enviar. Hoy alcanzaría a 34 pedidos.
- **Paso B (el arreglo de fondo):** llamar a `asignar_ticket_rm` desde `_sincronizar_pedidos_paris_historicos`, `_ripley_` y `_walmart_` (`system/orders/api/views.py:4566`, `:5402`, `:6040`), **después** de crear los `DetallePedido`, igual que `django_ecommerce/order_sync.py:255-267`. Y borrar los tres `notificar_retailmind` de los `order_sync.py` que nadie invoca.
- **Esfuerzo:** bajo. **Impacto:** mediana de 19 h → ~0,35 h sobre ~350 pedidos/mes y $53,9 M/trimestre.
- **Riesgo:** los tres canales necesitan `retailmind_sucursal_id` (o la lista multi-sucursal) en `configuracion_extra`; sin eso los pedidos caen en `PENDIENTE_CONFIG` en masa. **Verificarlo canal por canal antes de activar.**
- **Verificación:** la mediana por canal de la tabla §3.2 baja de 17-19 h a menos de 1 h.

#### 1.3 · Resucitar la importación de catálogo y hacerla auditable

- **Archivos:** `system/marketplaces/retailmind/services.py:1106-1131`, `tasks.py:202-209`, `models.py:381-385`, `client.py:49-51`.
- **Qué, en este orden:**
  1. **La memoria** (la causa raíz): reemplazar los dos dicts completos por `.only('id','codigo_articulo')` / `.only('id','codigo_sku','articulo_id')` con `.iterator(chunk_size=…)`, **excluyendo `datos_raw_api`**. Solo eso saca ~200 MB del proceso.
  2. **La observabilidad:** crear el `SincronizacionRetailMind` **fuera** del `transaction.atomic()` y **antes** de la etapa de snapshot; que `finalizar()` persista también los contadores y el resumen; ponerle el decorador `@_track_importacion_rm`.
  3. **El timeout:** subir el timeout del cliente solo para `/api/skus/` (hoy 30 s, y hay 504 registrados), o pedir el catálogo paginado.
  4. Solo si con 1-3 sigue muriendo: subir `--max-memory-per-child` en `start_workers.sh`.
- **Esfuerzo:** medio. **Impacto:** desbloquea $44,7 M de mercadería de Paola que hoy no se puede publicar, y devuelve una base de precios que no tenga 59 días.
- **Verificación:** `sys_sincronizacion_retailmind` vuelve a tener una fila `COMPLETADA` diaria por canal **con `articulos_actualizados > 0`** (hoy: 0 de 10.087 corridas históricas), y `max(fecha_actualizacion)` de `sys_sku_retailmind` es de hoy.

#### 1.4 · Watchdog de stock y de catálogo

- **Archivo:** `system/marketplaces/retailmind/tasks.py`, copiando `watchdog_envio_erp_task` de `system/orders/tasks.py` (que ya funciona, 788 corridas, con dedup de alertas).
- **Alertar cuando:** `fallidas > 5 %` del payload · `not_found_products > 20 %` de lo empujado · un canal lleva > 26 h sin corrida de stock `COMPLETADO` · un canal RetailMind lleva > 26 h sin importación de catálogo `COMPLETADA` · una `ImportacionMarketplace` lleva > 1 h en `PROCESANDO`.
- Y arreglar `_finalizar_importacion_stock` (`tasks.py:37-52`) para que no marque `COMPLETADO` ignorando `fallidas`.
- **Esfuerzo:** bajo. **Verificación:** disparar a mano contra el estado de hoy debería producir al menos 3 alertas (Paris, catálogo canal 30, catálogo canal 28).

#### 1.5 · Cerrar la puerta con código

- Quitar la ruta `/accounts/register/` o exigir invitación (`system/auth_views.py:161`, `vicentEcommerces/urls.py:54`).
- Middleware `login_required` global con allowlist explícita: `/health/`, `/accounts/*`, `/system/webhooks/*` (tienen su propia firma) y `/app/pedidos/pendientes/`. **Desplegarlo primero en modo log-only durante 48 h** (registrar qué habría bloqueado) y recién después hacerlo cumplir.
- Poner auth a los tres endpoints de escritura anónimos (`system/views.py:2300`, `:2312`, `system/api_views.py:1455`).
- Cerrar los dos validadores que fallan abiertos (`webhooks/views.py:56-57`, `orders/retailmind_connector.py:529-532`): fallar cerrado, solo header, `hmac.compare_digest`. El lado RetailMind ya es el ejemplo a copiar.

**Qué NO tocar en Fase 1:** no cambiar la clave de identidad del catálogo (Fase 3). No borrar filas duplicadas del espejo. No subir cadencias hasta que 1.1 esté verificado en producción durante al menos 3 noches.

---

### FASE 2 — Cerrar la ventana de exposición y ordenar precios

#### 2.1 · Cadencia horaria a los 5 canales de marketplace

- **Archivos: ninguno.** Son filas en `django_celery_beat_periodictask`, `force: false`, cron `0 8-22`, minutos escalonados (`:00` Paris RS · `:10` Ripley RS · `:20` Walmart RS · `:30` Paris PAO · `:40` Walmart PAO).
- **Orden de encendido:** primero Walmart y Ripley (0 rechazos hoy, espejo convergido), después Paris. **Paris solo cuando 1.1 esté verificado y se sepa por qué rechaza 2.960 SKUs.**
- **Verificación:** el fantasma de la tabla §3.5 baja de 639/475/174/166/99 hacia el rango de 10-40, medido a media tarde.
- **Riesgo:** ~90 llamadas extra por día a RetailMind. Despreciable: la corrida horaria del canal 29 tarda 1,4-2,3 s.

#### 2.2 · Diagnóstico y limpieza de lo que cada canal rechaza

- **Paris:** con el log de rechazados de 1.1, clasificar por qué. 2.960 SKUs estables desde el 30-jun no es un error transitorio.
- **Ecommerce:** los 12 artículos zombis y los 46 `not_found_variants` por corrida. Excluirlos con un contador `not_found_consecutivos` en metadatos, mismo patrón que el `sin_oferta_mirakl` de Ripley. Y excluir los `not_found_variants` del update local (hoy solo se excluyen los productos).
- **Ripley:** exponer las 6.234 unidades excluidas por el flag como un número visible, no como un efecto colateral.

#### 2.3 · Arreglar o apagar las cuatro tareas de precios inútiles

- `system/precios/tasks.py:676`: `'WALMART'` → `'WALLMART'` (o aceptar ambos, como ya hacen las líneas 814 y 907 del mismo archivo).
- `system/precios/tasks.py:603-619`: indexar por `sku_variacion_canal` en vez de `pc.sku_canal`.
- **Si no se van a arreglar esta semana, apagarlas.** Una tarea verde que no hace nada es peor que no tenerla.
- **Verificación:** `ProductoCanal` con `ultima_lectura_precio` pasa de **0 de 26.181** a la mayoría del canal.

#### 2.4 · Cablear `stock_seguridad`

- `stock_target = max(0, int(stock_rm) - rel.stock_seguridad)` en el bucle del diff de las 4 tareas, cargando la `RelacionCanales` también en la rama donde el canal destino viene por id explícito (que es como lo llama beat en las 7 tareas periódicas).
- Desplegar con los 16 valores en 0 (comportamiento idéntico al actual) y subir a 1 unidad canal por canal, midiendo.

#### 2.5 · Locks de idempotencia

- `cache.add(key, ttl)` por (tarea, canal destino) en las 4 tareas de stock, reusando el patrón de `publicar_paris_task` (`tasks.py:4882`). TTL de 2× el `soft_time_limit`, liberación en `finally`.

**Qué NO tocar en Fase 2:** no tocar la identidad del catálogo. No bajar `force` a `false` en Paris antes de 1.1. No deduplicar filas del espejo antes de tener el dry-run.

---

### FASE 3 — La deuda estructural

#### 3.1 · Sacar la categoría de la clave de identidad

- **En dos tiempos, nunca de golpe:**
  1. **Re-anclaje con la clave actual:** comando en dry-run que, para cada artículo huérfano con vínculos vivos, encuentre su gemelo por los 4 primeros segmentos y mueva `VinculoProductoCanal`, guía de talla y `SkuRetailMind` al artículo vivo. CSV auditable. Alcance: 16.072 vínculos.
  2. **Recién después**, cambiar `codigo_articulo` a `articulo||marca||color||genero` (`client.py:624`), manteniendo `sku_canal` como un campo propio **ya no derivado de la clave** — porque si cambia la clave cambia el SKU con que los productos están publicados en los ecommerce.
- **Empezar por Realsport**, que tiene el espejo más reciente.

#### 3.2 · Reasignar `articulo_id` en el update de SKUs

- `services.py:1263-1291`: cuando un `codigo_sku` ya existe pero pertenece a otro artículo del mismo canal, reasignar `articulo_id` y agregarlo al `bulk_update`. Tres líneas. Arregla los 10.859 artículos sin hijos y evita que la reparación de 3.1 los vuelva a generar.

#### 3.3 · Que el import marque como ausente lo que el ERP dejó de emitir

- Soft-delete reversible, **nunca borrado**, exigiendo N corridas consecutivas sin ver el artículo y copiando la guarda que ya usa `reconciliacion_productos_task` (si el pull viene vacío o cae bajo un umbral, abortar y alertar).

#### 3.4 · Cerrar el ciclo del pull de pedidos

- Que RetailMind devuelva el `numero_ticket_rm` de lo que ingesta, con un UPDATE idempotente condicionado a `numero_ticket_rm=''`. Con 1.2 hecho, el pull queda como red de seguridad y no como vía principal, pero igual hay que cerrarle el ciclo para que el indicador operativo sirva.
- **No hacer un backfill masivo de los 25.690** sin antes decidir cuáles son históricos reales.

---

## 5. La decisión estructural: Paola y HoldingTebes

**Primero, el hecho: la premisa está obsoleta. Los canales de Paola ya NO leen de HoldingTebes.**

Verificado en producción hoy:

- `paola-stock-paris-canal-3`, `paola-stock-walmart-canal-10` y `paola-stock-ecommerce-canal-31` leen de **RetailMind canal 30**, con 57 corridas cada una y `last_run_at` de hoy.
- Las tareas de HoldingTebes (`paris-sync-stock-holding-canal-3/4`, `ripley-sync-stock-holding-canal-6`, `shopify-monitor-all-canales`, `sincronizacion-nocturna-holdingtebes`) están **`enabled=False`**, con último run el **2026-05-31**.
- **No existe ni un solo `VinculoProductoCanal` desde artículos de Paola hacia el canal Shopify.** El motor de descuentos, que usaría `pc_shopify.precio_venta` como precio base si existiera (`system/precios/tasks.py:1320-1323`), cae siempre al fallback `ArticuloRetailMind.precio_base`.

**Entonces, ¿qué queda de HoldingTebes?** Restos de configuración, no comportamiento: **7 relaciones activas** que apuntan a o desde canales muertos (las 6 con origen en los canales 1 y 2, y la 18 `RETAILMIND-PAOLA → CalzadosPaola.cl`). Están inertes hoy, pero son exactamente lo que resucitaría el comportamiento viejo si alguien vuelve a habilitar una tarea, y son lo que hace que cualquier diagnóstico apurado concluya "Paola lee de HoldingTebes".

### El problema REAL de Paola es otro, y es más caro

| Dato | Realsport | **CalzadosPaola** |
|---|---:|---:|
| Antigüedad del espejo de catálogo | 8 días | **59 días** |
| SKUs del ERP invisibles para el hub | 1 | **604** |
| Unidades / valor de venta invisibles | 0 | **1.309 u · $44.743.910** |
| SKUs con precio desactualizado en el espejo | 423 (0,6 %) | **3.695 (3,7 %)** |
| Ventas 90 d por canales de la empresa | $49,8 M | **$27,6 M** |

Paola factura $27,6 M cada 90 días **con un catálogo de mayo**. Todo su descuento, todo su piso de costo y todo su enlace de productos se calculan sobre datos del 29 de mayo.

### Recomendación

**No hay que "migrar Paola de HoldingTebes a RetailMind": ya está migrada. Hay que terminar el trabajo, y eso son dos acciones concretas.**

1. **Hoy (Fase 0.4):** desactivar las 7 relaciones legacy hacia/desde los canales 1, 2 y 5. Es un `UPDATE` de 7 filas, reversible, y elimina de raíz la posibilidad de que HoldingTebes vuelva por la puerta de atrás. **Costo: 10 minutos. Riesgo: bajo y reversible.**
2. **Fase 1.3:** arreglar la importación de catálogo del canal 30, que es lo que realmente tiene a Paola operando a ciegas. **Costo: medio. Beneficio: $44,7 M de mercadería que hoy no puede entrar a ningún canal.**

**Lo que NO recomiendo:** montar un proyecto de "unificación de arquitectura" o resucitar `ProductoMaster`/`VariacionMaster` como hub. Esa arquitectura ya está muerta en la ruta viva —las 4 tareas de stock no leen `VariacionMaster`, y su última escritura es del 19-jul— y el modelo que sí funciona (`VinculoProductoCanal`, con constraint XOR e índices por los tres lados) ya está en producción con 16.344 vínculos. **Reforzar el que funciona, no revivir el que no.**

---

## 6. Qué NO hacer

1. **No poner `force=false` en Paris, Ripley o Walmart antes de arreglar el espejo (1.1).** Hoy `force=True` es lo único que compensa que el espejo mienta sobre lo aceptado. Cambiar el flag primero convierte un bug latente en pérdida de sincronización silenciosa sobre 5.127 SKUs.
2. **No subir Paris a cadencia horaria mientras rechace el 33 %.** Rechazaría lo mismo 15 veces al día en vez de 1, y enterraría la señal justo cuando se necesita.
3. **No encender el push de stock en tiempo real (`ALLCONNECTED_WEBHOOK_URL`) tal como está.** El emisor manda **un POST por cada `Movimientos_Producto` creado**, con un SKU por payload, desde un thread daemon. El receptor no encola nada: dentro del mismo request itera los canales destino y hace HTTP en vivo contra los marketplaces. Con las relaciones de hoy, una venta de 3 líneas dispararía 3 POST y hasta 15 llamadas en línea; una recepción de mercadería lo multiplica por miles. Antes hay que batchear por ticket/DTE, encolar del lado receptor y ponerle un flag de habilitación como el que ya protege al webhook de facturas.
4. **No cambiar la clave de identidad del catálogo antes del re-anclaje.** Al cambiar la clave cambia el `sku_canal` con que los productos están publicados en realsport.cl y calzadospaola.cl. Primero re-anclar con la clave actual, después desacoplar.
5. **No borrar filas duplicadas de `VariacionCanal` sin dry-run.** Son 12.700 repartidas en 7 canales y arrastran vínculos y metadatos (como el flag `sin_oferta_mirakl` de Ripley). Hoy el `bulk_update` las mantiene alineadas por accidente; el orden correcto es: primero determinismo (`order_by`), después la unique constraint, después la limpieza.
6. **No cambiar `/api/stock/global/` para que lea los lotes FIFO.** El plano es el campo que siempre se actualiza; el consumo de lotes es best-effort. Cambiarlo apagaría publicaciones de golpe. El drift plano-vs-FIFO es un problema de valorización, no de sobreventa.
7. **No hacer un backfill masivo de los 25.690 pedidos `PENDIENTE`.** El más antiguo es de 2023. Primero hay que decidir cuáles son históricos reales; el indicador está roto, los datos no.
8. **No confiar en `/api/stock/movimientos/?fecha_desde=`.** Devuelve `200 / success: true` con lista vacía cuando en realidad no vio los cambios (`Producto_Talla.updated_at` está muerto porque todo el ERP baja stock con `queryset.update()`). Un consumidor que lo use nunca ve la venta que acaba de ocurrir y no recibe ninguna señal de error. La única corrida registrada de `STOCK_INCREMENTAL` en toda la historia es del 2026-05-23.
9. **No borrar todavía las publicaciones del canal 5 (Ripley CalzadosPaola).** Sin pedidos desde noviembre de 2025, pero antes de tocar 3.260 publicaciones hay que confirmar con Ripley si la tienda existe. Excluirlo de las mediciones sí; borrar no.
10. **No leer la baja de "SKUs sincronizados" como un retroceso** cuando entre 1.1. El número va a caer de 13.173 a ~6.100 en Paris porque **recién ahí va a decir la verdad**. Comunicarlo antes de que alguien vea el gráfico.
11. **No arreglar las 4 tareas de precios "por prolijidad" si no se van a usar.** Apagarlas es una decisión legítima y mejor que dejarlas verdes sin hacer nada.

---

## 7. Cómo saber si mejoró

Cuatro indicadores, con su línea base medida hoy. Todos se calculan con consultas de solo lectura sobre las dos bases; ninguno requiere instrumentación nueva.

### KPI 1 · Latencia mediana pedido → ERP, por canal

*Cruce de `sys_pedido.fecha_pedido` contra `app_pedido_ecommerce.fecha_recepcion` por `numero_pedido_canal`, ventana de 45 días.*

| Canal | Hoy | Meta |
|---|---:|---:|
| Paris Realsport | **19,43 h** | < 1 h |
| Paris CalzadosPaola | **19,04 h** | < 1 h |
| Wallmart Realsport | **19,28 h** | < 1 h |
| RIPLEY REALSPORT | **17,17 h** | < 1 h |
| Wallmart CalzadosPaola | **16,49 h** | < 1 h |
| realsport.cl / calzadospaola.cl | 0,33 / 0,35 h | mantener |
| **Pedidos que nunca llegaron (45 d)** | **4** | 0 |

### KPI 2 · SKUs con stock fantasma, medido a las 16:00

*`VariacionCanal.stock_canal > SUM(Producto_Talla.stock)` del ERP, sobre publicaciones activas.*

| Canal | Hoy (SKUs / unidades) | Meta |
|---|---:|---:|
| Paris Realsport | **639 / 1.635** | < 50 |
| Paris CalzadosPaola | **475 / 1.526** | < 50 |
| RIPLEY REALSPORT | 174 / 290 | < 50 |
| Wallmart CalzadosPaola | 166 / 614 | < 50 |
| Wallmart Realsport | 99 / 141 | < 50 |
| calzadospaola.cl | 43 / 43 | < 20 |
| realsport.cl (referencia sana) | **10 / 28** | mantener |

### KPI 3 · Honestidad del espejo y edad del catálogo

| Métrica | Hoy | Meta |
|---|---:|---:|
| `bd_local.variaciones_canal` vs `exitosas` — Paris Realsport | **13.173 vs 6.101** | `bd_local ≤ exitosas` |
| SKUs rechazados por Paris cada noche | **2.960 + 2.167** | < 5 % del payload |
| Edad del espejo de catálogo, canal 28 | **8 días** | < 36 h |
| Edad del espejo de catálogo, canal 30 | **59 días** | < 36 h |
| Corridas de catálogo con `articulos_actualizados > 0` | **0 de 10.087** | ≥ 1 diaria por canal |
| SKUs del ERP invisibles para el hub (Paola) | **604 · $44,7 M** | 0 |

### KPI 4 · Daño realizado

| Métrica | Hoy | Meta |
|---|---:|---:|
| Pedidos pagados no surtibles, acumulados | **50 · $2.921.733** | 0 acumulados |
| — de ellos, del mes en curso | **7 · $337.802** | < 3/mes |
| Pedidos en `estado_envio_erp='ERROR'` | **26 · $764.508** | 0 |
| Alertas de stock/catálogo emitidas por el sistema | **0, en toda su historia** | > 0 cuando corresponda |

> El último indicador es el más importante de los cuatro. **Hoy el sistema no es capaz de avisar que está fallando**, y por eso Paola lleva 59 días sin catálogo, Paris rechaza 5.127 SKUs cada noche desde el 30 de junio y cuatro tareas de precios corren todas las mañanas sin escribir una fila — con todos los tableros en verde. Mientras ese número siga en cero, cualquier arreglo de este plan se puede volver a romper en silencio.

---

## Anexo · Archivos que concentran el trabajo

| Archivo | Qué hay ahí |
|---|---|
| `system/marketplaces/retailmind/tasks.py` | Las 4 tareas de stock (`:3535`, `:3846`, `:4275`, `:4555`), la import de catálogo (`:156`), el pull defensivo (`:3491`), el guard de cero masivo de Walmart (`:4408`) |
| `system/marketplaces/retailmind/services.py` | `sincronizar_completa` (`:76`), el prefetch que consume la memoria (`:1106-1117`), el update de SKUs sin reasignar padre (`:1263-1291`) |
| `system/marketplaces/retailmind/client.py` | Clave compuesta de identidad (`:624`), `TIMEOUT=30` (`:49-51`) |
| `system/orders/api/views.py` | Las 3 rutas vivas de pedidos de marketplace que no llaman al ERP (`:4566`, `:5402`, `:6040`) |
| `system/orders/tasks.py` | `reintentar_envio_retailmind_task` (`:975`), los 3 watchdogs de pedidos que sirven de plantilla |
| `system/orders/retailmind_connector.py` | `asignar_ticket_rm` (`:240`), pull de pendientes y su auth débil (`:529`) |
| `system/precios/tasks.py` | El typo `WALMART`/`WALLMART` (`:676`), la clave equivocada de Ripley (`:603-619`), el precio base del descuento (`:1320-1323`) |
| `system/views.py` / `system/api_views.py` | Los endpoints de escritura anónimos (`:2300`, `:2312`, `api_views.py:1455`) |
| `system/auth_views.py` | `register_view` (`:161`) |
| `system/webhooks/views.py` | Validador que falla abierto (`:41-60`), receptor del webhook de stock nunca usado (`:1699`), webhook de facturas (`:2839`) |
| `start_workers.sh` | `--max-memory-per-child=450000`, `--concurrency=1` |
