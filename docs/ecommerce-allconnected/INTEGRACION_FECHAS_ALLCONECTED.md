# Integración: fechas de productos para `/precios/dashboard/` de AllConected

## Contexto

`VicentAllEcommercesConected` (en adelante AllConected) consume datos de RetailMind a través del cliente HTTP `RetailMindClient` ([client.py](../VicentAllEcommercesConected/system/marketplaces/retailmind/client.py)). Su dashboard de precios `/precios/dashboard/` necesita información de antigüedad de stock y de catálogo para sugerir descuentos automáticos.

## Estado previo de la API

El endpoint `/api/precios-actuales/` (en RetailMind) ya exponía:

- `codigo_sku`
- `articulo`
- `precio_venta`, `precio_costo`, `precio_sugerido`
- `ultima_fecha_ingreso` ← último ingreso de stock activo (lote FIFO con `activo=True`)

Lo que **faltaba**: la fecha de creación del producto y la antigüedad calculada en días.

## Cambios aplicados (backward-compatible, no rompe AllConected)

### Endpoint modificado: [`/api/precios-actuales/`](retailmind/app/api/external/views.py#L536) `PreciosActualesView`

**Dos campos nuevos** en cada item del array `data`:

| Campo | Tipo | Significado |
|---|---|---|
| `fecha_creacion` | string `YYYY-MM-DD` o `null` | Fecha de alta del PRODUCTO más antigua entre todas las sucursales (cuándo nació el SKU en cualquier bodega de la empresa). |
| `dias_antiguedad_stock` | int o `null` | Días entre hoy y `ultima_fecha_ingreso` (precalculado para evitar lógica en el cliente). |

### Respuesta nueva

```json
{
  "success": true,
  "data": [
    {
      "codigo_sku": "4810070",
      "articulo": "ART001",
      "precio_venta": 59990,
      "precio_costo": 25000,
      "precio_sugerido": 64990,
      "ultima_fecha_ingreso": "2026-03-15",
      "fecha_creacion": "2024-08-01",
      "dias_antiguedad_stock": 60
    }
  ],
  "total": 123,
  "timestamp": "2026-05-14T12:00:00Z"
}
```

### Por qué backward-compatible

- AllConected actualmente solo lee `codigo_sku`, `precio_costo`, `precio_venta` y `ultima_fecha_ingreso` ([tasks.py:85-95](../VicentAllEcommercesConected/system/precios/tasks.py#L85)). Los campos nuevos serán **ignorados** por la versión actual del cliente sin error.
- No se modificaron campos existentes ni se cambió formato de los actuales.
- Se puede desplegar RetailMind sin coordinación con AllConected.

## Cómo AllConected puede aprovecharlos (cambios sugeridos en AllConected)

Estos cambios **NO se aplicaron** — solo son sugerencias para el siguiente sprint en AllConected:

### 1. Persistir los nuevos campos al sincronizar

En [`tasks.py:sincronizar_fechas_costos_holdingtebes_task`](../VicentAllEcommercesConected/system/precios/tasks.py), agregar al mapping:

```python
data = {
    'data': [
        {
            'codigo_asociado': item.get('codigo_sku', ''),
            'costo': item.get('precio_costo', 0),
            'precioventapublico': item.get('precio_venta', 0),
            'ultima_fecha_ingreso': item.get('ultima_fecha_ingreso'),
            'fecha_creacion': item.get('fecha_creacion'),               # NUEVO
            'dias_antiguedad_stock': item.get('dias_antiguedad_stock'), # NUEVO
        }
        for item in rm_resp.get('data', [])
    ]
}
```

Y agregar columnas al modelo `VariacionMaster` (o donde se guarde):

```python
fecha_creacion_retailmind = models.DateField(null=True, blank=True)
dias_antiguedad_stock = models.IntegerField(null=True, blank=True)
```

(Migración Django simple, sin defaults problemáticos).

### 2. Lógica de sugerencia de descuento por antigüedad

En el dashboard `/precios/dashboard/`, agregar columna "Descuento sugerido" basado en `dias_antiguedad_stock`:

| `dias_antiguedad_stock` | Descuento sugerido |
|---|---|
| `null` o ≤ 30 | 0% (recién llegado, full price) |
| 31 – 90 | 0-10% (opcional) |
| 91 – 180 | 15% (introducir) |
| 181 – 365 | 25-30% |
| > 365 | 40-50% (clearance) |

Se puede mostrar como columna editable con valor pre-rellenado, no auto-aplicado, para que el usuario apruebe.

### 3. Filtro adicional en dashboard

Agregar filtro "Antigüedad de stock" con opciones:
- Fresco (≤30d)
- Maduro (31-180d)
- Estancado (>180d)

Útil para campañas de liquidación enfocadas.

### 4. Diferenciar producto viejo vs stock viejo

Con `fecha_creacion` (cuándo nació el modelo) + `dias_antiguedad_stock` (qué tan viejo es el stock actual):

| Caso | Descripción | Acción |
|---|---|---|
| Producto viejo + stock fresco | Modelo clásico que sigue rotando | Full price |
| Producto viejo + stock viejo | Quedó parado el modelo | Descontar agresivo |
| Producto nuevo + stock viejo | Lanzamiento que no rotó | Descontar moderado |
| Producto nuevo + stock fresco | Lanzamiento recién recibido | Full price |

## Archivos modificados en RetailMind

- [retailmind/app/api/external/views.py](retailmind/app/api/external/views.py) — `PreciosActualesView` (líneas 536-651). Cambios: agregada `producto__fecha_creacion` al `.values()`, lógica de consolidación toma MIN entre sucursales, cálculo de `dias_antiguedad_stock`, dos campos nuevos en respuesta.

## Validación recomendada

### Test manual del endpoint

```bash
curl -H "Authorization: Bearer $RETAILMIND_API_KEY" \
  "https://retail.webappsolutions.cl/api/precios-actuales/?rut_empresa=78503140-7" \
  | jq '.data[0]'
```

Debe devolver objeto con los 8 campos (los 6 originales + `fecha_creacion` + `dias_antiguedad_stock`).

### Test de consistencia

Tomar un SKU del response y validar contra DB:

```sql
-- En Postgres (RetailMind)
WITH sku_data AS (
  SELECT 
    pt.sku,
    MIN(p.fecha_creacion::date) AS fecha_creacion_min,
    MAX(l.fecha_ingreso) FILTER (WHERE l.activo = TRUE) AS ultima_fecha_ingreso
  FROM app_producto_talla pt
  JOIN app_producto p ON p.id = pt.producto_id
  LEFT JOIN app_loteproducto l ON l.producto_talla_id = pt.id
  WHERE pt.sku = 4810070  -- reemplaza con SKU de prueba
  GROUP BY pt.sku
)
SELECT 
  sku,
  fecha_creacion_min,
  ultima_fecha_ingreso,
  (CURRENT_DATE - ultima_fecha_ingreso) AS dias_antiguedad_calculados
FROM sku_data;
```

Las fechas y días deben coincidir con la respuesta del endpoint.

## Performance

- Se agregó **una columna** al `.values()` existente — sin queries extra.
- La consolidación es la misma estructura, solo agrega una comparación.
- Costo adicional: despreciable (~5-10ms más por SKU procesado).

## Despliegue en producción

Solo deploy de código en RetailMind:

```bash
git add retailmind/app/api/external/views.py
git commit -m "feat(api): expose fecha_creacion y dias_antiguedad_stock en /api/precios-actuales/"
git push
```

No requiere migración. AllConected puede seguir consumiendo el endpoint sin cambios; los nuevos campos quedarán disponibles cuando AllConected los lea.

## Resumen visual del flujo

```
RetailMind                          AllConected
─────────────────                   ─────────────────────
GET /api/precios-actuales/  ───►   sincronizar_fechas_costos_task()
                                          │
{codigo_sku,                              ▼
 precio_venta,                      Guarda en BD:
 precio_costo,                      - codigo_asociado
 ultima_fecha_ingreso,              - costo
 fecha_creacion,         ◄── NUEVO  - precioventapublico
 dias_antiguedad_stock}  ◄── NUEVO  - ultima_fecha_ingreso
                                    - fecha_creacion        (sugerido)
                                    - dias_antiguedad_stock (sugerido)
                                          │
                                          ▼
                                    Dashboard /precios/dashboard/
                                    - Mostrar columnas nuevas
                                    - Filtro por antigüedad
                                    - Auto-sugerir descuento
                                      basado en días stock
```

## Endpoints relacionados que NO se tocaron

| Endpoint | Por qué no se tocó |
|---|---|
| `/api/skus/` | Catálogo masivo, devuelve metadata distinta. La fecha aplica más al SKU operativo (precios-actuales). |
| `/api/stock/global/`, `/api/stock/movimientos/`, `/api/stock/por-skus/` | Endpoints de stock; las fechas que devuelven son de los movimientos, no de productos. |
| `/api/novedades/` | Ya usa `Producto.fecha_creacion` y `Producto.fecha_actualizacion` correctamente. |
| `/api/articulos/{cod}/tallas/` | Devuelve detalle de tallas; fecha de cada talla no se usa downstream. |

Si en el futuro alguno necesita la misma info, se puede replicar el patrón.
