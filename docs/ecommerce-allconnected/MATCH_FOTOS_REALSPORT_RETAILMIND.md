# Match de productos: realsport.cl ↔ SistemaRetailMind

Documenta cómo RetailMind asocia las fotos de portada que vive en realsport.cl
con los productos del sistema. Cómo es el flujo, por qué cuesta hacer match
1-a-1, qué estrategias usa el sync, y cómo verificarlo.

---

## Resumen ejecutivo

- **realsport.cl** tiene ~1095 productos con foto WebP en S3.
- **RetailMind** tiene 63.664 articulos distintos (todas las empresas).
- Match logrado: **99.3% (1087/1095)**.
- Estrategia ganadora: **el SKU de realsport corresponde a `Producto_Talla.sku`,
  no a `Producto.articulo`**. RetailMind deduce el `articulo` padre y guarda la
  foto enlazada a ese articulo (no a la talla).
- Las URLs viven en una tabla aparte (`FotoPortadaArticulo`). El modelo
  `Producto` no se toca.

---

## Contexto: identificadores en cada sistema

### En RetailMind

```
Producto (catalogo.py)
  ├─ articulo          ← string del modelo. Se repite N veces (una fila por sucursal).
  ├─ descripcion
  ├─ sucursal → empresa.rut
  └─ Producto_Talla (FK related_name='producto_talla')
        ├─ sku         ← BigInteger. Único por (producto × talla × color).
        └─ talla
```

Un mismo **modelo de zapatilla** (ej. `articulo="AIRMAX-90"`) puede tener:
- 7 filas en `Producto` (una por sucursal)
- 30 filas en `Producto_Talla` (talla 38, 39, 40, ... × sucursal)

Cada `Producto_Talla.sku` es un BigInteger único en el sistema (7-10 dígitos).

### En realsport.cl

```
Product (catalog/models.py)
  ├─ sku               ← CharField unique. AllConected lo manda como string.
  └─ ProductImage (related_name='images')
        ├─ image       ← S3 / DigitalOcean Spaces
        ├─ is_primary  ← portada
        └─ thumbnail   ← WebP 400x400 (django-imagekit)
```

**Un `Product` en realsport.cl = una variante específica de un modelo**
(combinación de modelo + color, generalmente). Por eso realsport tiene ~1000
productos y RetailMind 63k articulos: realsport solo expone lo que se vende
online, ya pre-procesado.

### En AllConected (intermediario)

`AllConected` orquesta el flujo. Para cada empresa de RetailMind que tiene un
canal-realsport configurado:

1. Lee `GET /api/skus/?rut_empresa=XX-X` de RetailMind
   ([retailmind/app/api/external/views.py:81](retailmind/app/api/external/views.py#L81)).
2. Construye payload (`_build_payload_from_articulo_rm` en
   `publisher.py:269`).
3. Pushea `POST/PATCH /api/v1/products/<sku>/` a realsport.cl
   ([client.py:107](../VicentAllEcommercesConected/system/marketplaces/django_ecommerce/client.py#L107)).

**Lo crítico**: AllConected pushea con `Product.sku = codigo_articulo` de
`ArticuloRetailMind`, que se popula desde `Producto_Talla.sku` (BigInt
convertido a string), **no desde `Producto.articulo`**.

---

## El problema del match

A simple vista, debería haber sido un match directo:

```
realsport.Product.sku  ↔  retailmind.Producto.articulo
```

Pero al correr el primer sync, el resultado fue **0 match de 1095**. Ejemplos
de SKUs vistos en realsport vs articulos vistos en RetailMind:

| realsport.Product.sku | retailmind.Producto.articulo |
|---|---|
| `4799588` | `NK-AV003-12` |
| `4789355` | `DEPACUANT076` |
| `4760686` | `CT4063-400` |
| `4813031` | `VN0A5EM7187-OL0` |

Conclusión: **son identificadores de espacios distintos**. Los SKUs de
realsport son códigos numéricos cortos (BigInt como string); los articulos de
RetailMind son códigos del proveedor/fabricante (alfanuméricos con guiones).

Hipótesis: realsport.Product.sku = retailmind.Producto_Talla.sku.

**Verificación**:

```sql
-- Tomar un SKU de realsport y buscarlo en Producto_Talla:
SELECT producto_id, talla
FROM app_producto_talla
WHERE sku = 4799588;
-- → match: existe, pertenece a un Producto con articulo='ZM22-1552-90'
```

Hipótesis confirmada.

---

## Las 3 estrategias en cascada

El sync ([app/services/realsport_imagenes_service.py:266+](retailmind/app/services/realsport_imagenes_service.py#L266))
prueba en orden:

### 1️⃣ Match exacto contra `Producto.articulo`

```python
if sku in articulos_exactos:
    articulo_real = sku
    match_exacto += 1
```

Cuándo aplica: si AllConected algún día pushea con `articulo` literal (no es
el caso actual).

### 2️⃣ Match flexible (trim + uppercase) contra `Producto.articulo`

```python
articulo_real = articulos_norm.get(sku.strip().upper())
if articulo_real:
    match_flexible += 1
```

Cuándo aplica: cubre normalizaciones comunes (mayúsculas, espacios). Tampoco
aplica en el caso realsport actual, pero queda para otros canales futuros.

### 3️⃣ Match por `Producto_Talla.sku` → articulo padre ⭐

```python
articulo_real = talla_sku_a_articulo.get(sku.strip())
if articulo_real:
    match_por_talla += 1
```

**Esta es la que matchea con realsport.cl hoy** (1087 de 1095, 99.3%).

Se construye un diccionario `{str(Producto_Talla.sku): Producto.articulo}`
recorriendo toda la tabla `Producto_Talla` (≈335k filas) y para cada SKU de
realsport se busca el articulo padre.

**Importante**: la foto se guarda contra el `articulo` (no contra el SKU de
talla). Así una sola foto sirve para todas las tallas y sucursales del mismo
modelo.

---

## Almacenamiento del resultado

```python
# app/models/configuracion.py
class FotoPortadaArticulo(models.Model):
    articulo     = CharField(max_length=200, db_index=True)
    url_foto     = URLField(max_length=500)
    origen       = FK(CredencialesEcommerce)
    es_principal = BooleanField(default=True)
    sync_at      = DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('articulo', 'origen')
```

Una fila por `(articulo, origen)`. Soporta múltiples ecommerces de origen sin
duplicar.

---

## Resolución en runtime (cuando se muestra la foto)

Helper único:
[`resolver_foto_portada_url(articulo, empresa_id)`](retailmind/app/services/realsport_imagenes_service.py#L211)

Lógica:

```
1. ¿Existe FotoPortadaArticulo con articulo=X y origen.empresa_id == producto.empresa?
   SÍ → devolver esa URL (es la foto "propia" de la empresa)
2. NO → ¿Existe alguna FotoPortadaArticulo activa con articulo=X?
   SÍ → devolver la de mayor origen.prioridad
3. NO → string vacío (template muestra placeholder SVG)
```

Cacheado en Redis con TTL 1h. Cache key hasheada con sha1 para soportar
articulos con espacios (`MAGIC BLOCK GAME`) o caracteres especiales que
Memcached rechazaría.

---

## Diagrama del flujo completo

```
┌────────────────────┐                       ┌──────────────────────┐
│   realsport.cl     │                       │   SistemaRetailMind  │
│   (productos web)  │                       │   (sistema interno)  │
└─────────┬──────────┘                       └──────────┬───────────┘
          │                                              │
          │ ProductImage.is_primary                     │ Producto.articulo
          │ → thumbnail WebP S3                         │ → identidad del modelo
          │                                              │
          │ GET /api/v1/products/images/                 │ Producto_Talla.sku
          │   ?page=1&page_size=500                      │ → identidad de variante
          │   X-AllConnected-Key: <key>                  │
          ▼                                              ▼
    ┌──────────────────────────────────────────────────────────┐
    │   app/services/realsport_imagenes_service.py             │
    │                                                          │
    │   sincronizar_credencial():                              │
    │     1. traer_catalogo_portadas() — pagina realsport      │
    │     2. por cada (sku, url):                              │
    │        ├─ ¿sku ∈ Producto.articulo?     → match_exacto   │
    │        ├─ ¿sku.upper() ∈ articulos?     → match_flexible │
    │        └─ ¿sku ∈ Producto_Talla.sku?    → match_por_talla│
    │              ↓                                           │
    │           FotoPortadaArticulo.update_or_create(          │
    │             articulo=<articulo padre>,                   │
    │             url_foto=url,                                │
    │             origen=cred_realsport,                       │
    │           )                                              │
    └──────────────────────────────────────────────────────────┘
                              │
                              ▼
                  ┌────────────────────────┐
                  │ FotoPortadaArticulo    │
                  │ (tabla en RetailMind)  │
                  └───────────┬────────────┘
                              │
                              │ resolver_foto_portada_url(articulo, empresa)
                              ▼
                  ┌────────────────────────────────────┐
                  │ Pantallas que muestran la foto:    │
                  │  • /app/ticket-venta/              │
                  │  • /app/productos-sucursal/        │
                  │  • /app/pos-dashboard/             │
                  │  • /app/emisionDTE/                │
                  │  • + lightbox compartido           │
                  └────────────────────────────────────┘
```

---

## Comandos prácticos

### Verificar match contra una credencial (read-only)

```powershell
py manage.py verificar_match_realsport --codigo realsport --muestra 30
```

Muestra:
- Estadísticas por cada estrategia (% de match)
- 30 SKUs lado a lado con la estrategia que matcheó
- 10 articulos de RetailMind para comparar formato
- Veredicto: qué estrategia recomienda

### Sincronizar catálogo completo

```powershell
py manage.py sincronizar_fotos_ecommerce --codigo realsport
```

Salida típica:

```
>>> Realsport Django (realsport) — empresa IMPORTADORA NICOLE ANDREA
  Modo catálogo completo (page_size=500). Tirando del ecommerce...
  página 1: total ecommerce=1095, recibidos=500
  página 2: total ecommerce=1095, recibidos=500
  página 3: total ecommerce=1095, recibidos=95
  paginas=3  procesados=1095  con_foto=1087  (exacto=0, flexible=0, talla=1087)  sin_match=8
```

### Sincronizar un solo articulo (debug)

```powershell
py manage.py sincronizar_fotos_ecommerce --codigo realsport --articulo ZM22-1552-90
```

Este usa el modo **lookup puntual** (`?skus=ZM22-1552-90` en el endpoint),
que solo funciona si el endpoint remoto encuentra el SKU literal — útil para
testear.

---

## Decisiones de diseño y por qué

| Decisión | Por qué |
|---|---|
| **Guardar solo la URL, no descargar la imagen** | realsport ya genera WebP optimizado en S3. Descargar duplica almacenamiento y nos obligaría a actualizar cuando cambien. La URL apunta directo al CDN. |
| **Foto enlazada a `articulo`, no a `Producto`** | `Producto` se repite N veces (una por sucursal). Enlazar al articulo (string) significa 1 foto = N sucursales sin duplicar. |
| **Tabla `FotoPortadaArticulo` aparte, no campo en `Producto`** | Soporta múltiples ecommerces de origen (cuando llegue paola.cl). Y no fuerza migración pesada del modelo `Producto` (que tiene 155+ migraciones). |
| **Resolución en runtime con cache 1h** | Permite que cuando se agrega un nuevo ecommerce o cambia una foto, las pantallas reflejen el cambio en máximo 1 hora sin redeploy. |
| **3 estrategias en cascada (no solo Producto_Talla.sku)** | Robustez: si mañana cambian el formato en AllConected o llega otro ecommerce con otro formato de sku, el match flexible cubre transformaciones comunes (case/trim) sin código nuevo. |
| **Sync via `subprocess.run` en lugar de Celery** | Para 1000 productos tarda 15-25s — manejable en una request. Si crece a 10k+, conviene Celery. Hoy no es problema. |
| **Endpoint pull (realsport → RetailMind)** en lugar de push | RetailMind decide cuándo sincronizar. realsport no necesita conocer endpoints de RetailMind. |

---

## Cómo agregar otro ecommerce (paola.cl mañana)

Cero código:

1. Login con cuenta admin en RetailMind.
2. Configuración → Integraciones Ecommerce → Nueva integración:
   - codigo: `paola`
   - tipo: `paola.cl`
   - empresa: la empresa propietaria (RUT)
   - url_api: `https://paola.cl`
   - api_key: la `ALLCONNECTED_API_KEY` configurada del lado de paola
   - header_name: `X-AllConnected-Key` (default)
3. Botón "Probar conexión" → debería responder OK.
4. Botón "Sincronizar ahora" o cron diario.

Las 3 estrategias de match aplican igual. Si paola tiene un formato de SKU
distinto al de realsport, el `verificar_match_realsport --codigo paola`
revela qué estrategia usar.

---

## Cuando una empresa no tiene su propio ecommerce

Si EMPRESA X comparte un articulo con NICOL pero no tiene ecommerce
configurado:

```python
resolver_foto_portada_url('ZAP-123', empresa_id=X)
  → no encuentra origen.empresa=X
  → fallback: cualquier origen activo ordenado por prioridad
  → encuentra la foto que NICOL sincronizó desde realsport
  → la muestra
```

Esto es **comportamiento intencional**: una foto de Nike Air Max sirve para
cualquier empresa que venda Nike Air Max, sin importar quién la sincronizó.

Para aislar estrictamente (que EMPRESA X NO vea las fotos de NICOL) habría
que cambiar la lógica de fallback en
[`resolver_foto_portada_url`](retailmind/app/services/realsport_imagenes_service.py#L211).

---

## Los 8 productos sin match

De los 1095 productos en realsport.cl, 8 no matchearon. Causas probables:

1. Productos eliminados/migrados de RetailMind pero todavía existentes en
   realsport (debería limpiarse del lado de realsport).
2. SKUs creados manualmente en el admin de realsport sin pasar por
   AllConected.
3. Variantes que se sincronizaron a realsport y después la `Producto_Talla`
   correspondiente se eliminó.

Diagnosticar uno específico:

```powershell
py manage.py shell
>>> from app.models import Producto_Talla
>>> Producto_Talla.objects.filter(sku=<el_sku_huerfano>).exists()
False  # → confirma que ya no existe en RetailMind
```

---

## Archivos clave del sistema

| Archivo | Rol |
|---|---|
| [retailmind/app/services/realsport_imagenes_service.py](retailmind/app/services/realsport_imagenes_service.py) | Service: probar_conexion, traer_catalogo_portadas, sincronizar_credencial, resolver_foto_portada_url |
| [retailmind/app/models/configuracion.py](retailmind/app/models/configuracion.py) | Modelos CredencialesEcommerce + FotoPortadaArticulo |
| [retailmind/app/management/commands/sincronizar_fotos_ecommerce.py](retailmind/app/management/commands/sincronizar_fotos_ecommerce.py) | Command CLI de sync |
| [retailmind/app/management/commands/verificar_match_realsport.py](retailmind/app/management/commands/verificar_match_realsport.py) | Command CLI de diagnóstico |
| [retailmind/app/views_modulo_configuracion.py](retailmind/app/views_modulo_configuracion.py) | Pantalla CRUD de credenciales |
| [retailmind/app/templatetags/imagenes_producto.py](retailmind/app/templatetags/imagenes_producto.py) | Template tag `{% foto_portada producto %}` |
| [retailmind/app/static/js/foto_lightbox.js](retailmind/app/static/js/foto_lightbox.js) | Lightbox vanilla JS reusable |
| [realsport.cl/apps/api/views.py](../realsport.cl/apps/api/views.py) (endpoint `product_cover_images_view`) | Endpoint que realsport expone |

---

## Última verificación

- Sync ejecutado: **2026-05-14 17:30** (zona `America/Santiago`)
- Resultado: **1087 fotos sincronizadas** (99.3%)
- Tiempo de ejecución: ~15 segundos
- Productos sin match: 8
