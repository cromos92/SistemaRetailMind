# Fix: creación de productos con fecha histórica + manejo de productos existentes

## Problema que resuelve

El endpoint `crear_producto` (usado por la creación manual y la creación desde recepción) tenía 3 problemas:

1. **Fecha de creación siempre = `now()`**: aunque la recepción de mercadería era de meses atrás, el producto quedaba fechado el día de la creación en el sistema.
2. **El movimiento inicial usaba `default=django_date_today`**: la fecha del primer ingreso quedaba siempre en el día actual, no en la fecha del DTE/recepción.
3. **Sin protección contra duplicados**: si el frontend olvidaba llamar `verificar_existencia_producto`, se podían crear dos `Producto` para el mismo `(articulo, sucursal)`.

## Cambios aplicados

### 1. `retailmind/app/views.py` — función `crear_producto()` (línea 16376)

Refactor completo manteniendo compatibilidad con todas las llamadas existentes:

- **Nuevo parámetro opcional `fecha_creacion`** (datetime/date). Si no se pasa, usa `timezone.now()` como antes.
- **`Producto.objects.get_or_create()`** en lugar de `Producto.objects.create()`:
  - Si el producto ya existe (mismo `articulo` + `sucursal`) → retorna el existente, **mantiene su `fecha_creacion` original**.
  - Si es nuevo → se crea con `auto_now_add=now()` y luego un `update()` lo sobrescribe con `fecha_creacion` recibida.
- **`Producto_Talla.objects.get_or_create()`** por `(producto, talla)`:
  - Si la talla ya existe → suma el nuevo stock al existente.
  - Si es nueva → la crea con SKU nuevo.
- **`Movimientos_Producto.objects.create()`** ahora pasa explícitamente:
  - `fecha = fecha_creacion.date()`
  - `hora = fecha_creacion.time()`
  - `sucursal_origen = data['sucursal']` (antes quedaba NULL)
  - `concepto`: `'Ingreso Inicial'` solo si producto y talla son ambos nuevos; `'Recepción Compra'` si es ingreso a stock existente.

### 2. `retailmind/app/views_modulo_existencias.py` — `crear_producto_desde_recepcion()` (línea 261)

Ahora pasa explícitamente la fecha de la recepción:

```python
producto = crear_producto(
    data,
    request.user,
    fecha_creacion=recepcion.fecha_recepcion,  # ← histórica del DTE
)
```

## Comportamiento esperado tras el fix

| Escenario | `Producto.fecha_creacion` | `Movimientos_Producto.fecha` | Notas |
|---|---|---|---|
| Crear producto manual (sin pasar fecha) | `now()` | `today()` | Igual que antes — producto nuevo creado hoy |
| Crear desde recepción de DTE de hoy | `fecha_recepcion` (hoy) | `fecha_recepcion` (hoy) | Iguales |
| Crear desde recepción de DTE pasado (ej. 2 meses atrás) | `fecha_recepcion` (pasada) | `fecha_recepcion` (pasada) | **Antes**: ambos quedaban en hoy. **Ahora**: ambos quedan con la fecha real |
| Recepcionar producto que YA existe | **Sin cambios** (mantiene la original) | `fecha_recepcion` del nuevo movimiento | El producto no se duplica, solo suma stock y registra movimiento histórico |
| Talla nueva en producto existente | **Sin cambios** | `fecha_recepcion` | La talla se crea, el producto mantiene su fecha original |

## Compatibilidad con el fix histórico

Este cambio es **complementario** al `corregir_fecha_creacion_productos.py` que ya corrió:

- Productos antiguos (migrados con fecha 2026-04-15) → ya fueron corregidos con `MIN(Movimientos_Producto.fecha)`.
- Productos nuevos (creados a partir de hoy) → tendrán fecha correcta automáticamente.
- **No hay que volver a correr el comando histórico** — solo aplica este fix de código.

## Validación recomendada

1. **Test manual: crear producto desde una recepción**
   - Antes: el producto creado mostraba fecha de hoy.
   - Ahora: debe mostrar la `fecha_recepcion` del DTE.

2. **Test manual: recepcionar mismo articulo+sucursal dos veces**
   - El segundo intento NO debe crear duplicado.
   - Debe sumar stock a la talla existente y crear un nuevo `Movimientos_Producto`.
   - `Producto.fecha_creacion` debe seguir igual que la primera vez.

3. **Test SQL de comprobación**:
   ```sql
   -- Productos creados después de aplicar este fix con fecha histórica preservada
   SELECT p.id, p.articulo, p.fecha_creacion::date AS fecha_creacion, 
          MIN(mp.fecha) AS primer_mov
   FROM app_producto p
   JOIN app_producto_talla pt ON pt.producto_id = p.id
   JOIN app_movimientos_producto mp ON mp."ProductoTalla_id" = pt.id
   WHERE p.fecha_creacion >= '2026-05-14'  -- fecha de aplicación del fix
   GROUP BY p.id, p.articulo, p.fecha_creacion
   HAVING p.fecha_creacion::date = MIN(mp.fecha)
   LIMIT 10;
   ```
   Esperado: la fecha del producto coincide con la del primer movimiento.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Frontend que dependía de "el endpoint siempre crea producto nuevo" | El endpoint sigue retornando un `Producto`, solo que ahora puede ser uno existente. La respuesta JSON es la misma (`producto_id`). |
| Llamadas legacy a `crear_producto(data, user)` sin el tercer parámetro | El parámetro `fecha_creacion=None` es opcional. Comportamiento backward-compatible: cae en `now()` igual que antes. |
| Recepción con `fecha_recepcion=NULL` | El `if fecha_creacion is None` cae en `now()` correctamente. Sin crash. |

## Archivos modificados

- [retailmind/app/views.py](retailmind/app/views.py) — función `crear_producto` líneas 16375-16463
- [retailmind/app/views_modulo_existencias.py](retailmind/app/views_modulo_existencias.py) — endpoint `crear_producto_desde_recepcion` líneas 278-292

## Aplicación en producción

Solo requiere despliegue de código (no toca DB):

1. `git add retailmind/app/views.py retailmind/app/views_modulo_existencias.py`
2. `git commit -m "fix: crear_producto preserva fecha histórica y deduplica por (articulo, sucursal)"`
3. Push y deploy normal.

No requiere migración. No requiere ejecutar ningún comando.
