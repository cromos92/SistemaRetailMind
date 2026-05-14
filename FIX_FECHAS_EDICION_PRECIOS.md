# Fix: fechas de creación y último despacho en edición rápida de precios

## Contexto

En `/app/gestion-precios/edicion-rapida/`, cada producto en la lista de búsqueda muestra ahora dos fechas adicionales para tomar mejores decisiones de precio:

1. **`fecha_creacion`** — fecha real de alta del producto en su sucursal (corregida por `corregir_fecha_creacion_productos`).
2. **`fecha_ultimo_despacho`** — la última vez que llegó stock a **cualquier bodega** del mismo articulo (incluye toda la red: sucursal actual + similares).

## Por qué cada fecha

| Fecha | Para qué sirve |
|---|---|
| `fecha_creacion` | Antigüedad del producto en mi tienda (¿es nuevo o lleva años?). |
| `fecha_ultimo_despacho` | Frescura del stock en la red (¿llegó hace 5 días o hace 1 año?). Clave para descuentos: **stock que no se reabastece = candidato a liquidación**. |

Estas dos juntas dan el cuadro completo:
- Producto antiguo + último despacho reciente → sigue rotando, no descontar.
- Producto antiguo + último despacho muy viejo → quedó parado, descontar.
- Producto nuevo + último despacho reciente → lanzamiento, full price.

## Cambios aplicados

### 1. Backend — [retailmind/app/views_modulo_gestion_precios.py:387](retailmind/app/views_modulo_gestion_precios.py#L387) (`buscar_productos`)

Después del cálculo de productos similares, se agrega:

```python
# fecha_creacion del producto en ESTA sucursal (campo directo).
fecha_creacion_local = producto.fecha_creacion.date() if producto.fecha_creacion else None

# fecha_ultimo_despacho: último INGRESO de stock considerando TODAS
# las bodegas (sucursal actual + similares con mismo articulo + atributos).
productos_red_ids = list(productos_similares.values_list('id', flat=True)) + [producto.id]
agg_ingreso = Movimientos_Producto.objects.filter(
    ProductoTalla__producto_id__in=productos_red_ids,
    tipo_movimiento='INGRESO',
).aggregate(ultima_fecha=Max('fecha'))
fecha_ultimo_despacho_red = agg_ingreso['ultima_fecha']
dias_desde_ultimo_despacho = (
    (timezone.localdate() - fecha_ultimo_despacho_red).days
    if fecha_ultimo_despacho_red else None
)
```

Y se agregan a la respuesta JSON tres nuevos campos por producto:

```python
'fecha_creacion': '14/05/2026',
'fecha_ultimo_despacho': '02/03/2026',
'dias_desde_ultimo_despacho': 73,
```

### 2. Frontend — [retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html:2466](retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html#L2466) (función `renderizarResultadosBusqueda`)

Dos badges nuevos en la card del producto, color codificados según frescura:

| Días desde último despacho | Color del badge | Significado |
|---|---|---|
| ≤ 30 | 🟢 Verde | Stock fresco, recién repuesto |
| 31 – 180 | 🟡 Amarillo | Empieza a envejecer |
| > 180 | 🔴 Rojo | Stock estancado, candidato a descuento |

El badge "Alta:" siempre aparece en azul (informativo, no requiere acción).

## Lógica del cálculo

`fecha_ultimo_despacho` toma TODAS las sucursales con el mismo `(articulo, atributo1, atributo2)` — la "red" del producto — y calcula `MAX(Movimientos_Producto.fecha)` filtrado por `tipo_movimiento='INGRESO'`.

`tipo_movimiento='INGRESO'` cubre todos los conceptos de entrada de stock:
- `RECEPCION_COMPRA` — recepción de proveedor en CDs
- `TRASPASO_ENTRADA` — traspaso desde otra sucursal
- `INGRESO_INICIAL` — alta de producto
- `DEVOLUCION_CLIENTE` — devolución
- `AJUSTE_POSITIVO` — ajustes manuales positivos

Si quisieras ser más quirúrgico (ej. solo despachos desde CD a vendedora, ignorando devoluciones), se puede agregar un filtro adicional `concepto__in=['TRASPASO_ENTRADA', 'RECEPCION_COMPRA']`.

## Performance

Cada producto en el resultado agrega una query `aggregate(MAX)` adicional. Para una página de 50 productos: 50 queries extra. En la práctica son rápidas porque:
- `Movimientos_Producto` tiene índice en `ProductoTalla_id`.
- Filtra por `tipo_movimiento='INGRESO'` (índice combinado disponible).

Si se vuelve cuello de botella se puede pre-agregar en bloque con un `Subquery` o un `prefetch_related` con annotation. Por ahora la simplicidad pesa más.

## Compatibilidad

- **Sin migración de DB** — solo lee campos existentes.
- **Sin cambios en endpoints existentes** — solo agrega 3 campos nuevos al payload.
- **Backward-compatible** — el frontend antiguo que no use estos campos los ignora.

## Aplicación en producción

Solo despliegue de código, sin migración:

```
git add retailmind/app/views_modulo_gestion_precios.py \
        retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html
git commit -m "feat: muestra fecha_creacion y fecha_ultimo_despacho en edicion rapida de precios"
```

Push y deploy normal.

## Validación visual

1. Entrar a `/app/gestion-precios/edicion-rapida/`.
2. Buscar cualquier producto.
3. En cada card debe aparecer:
   - Badge azul "Alta: DD/MM/YYYY" (fecha_creacion del producto).
   - Badge verde/amarillo/rojo "Último despacho: DD/MM/YYYY · hace Nd".
4. Para validar el cálculo: tomar un producto, anotar la fecha mostrada, y correr en Postgres:
   ```sql
   SELECT MAX(mp.fecha)
   FROM app_movimientos_producto mp
   JOIN app_producto_talla pt ON pt.id = mp."ProductoTalla_id"
   JOIN app_producto p ON p.id = pt.producto_id
   WHERE p.articulo = 'ARTICULO_AQUI'
     AND mp.tipo_movimiento = 'INGRESO';
   ```
   El `MAX(fecha)` debe coincidir con la mostrada en el badge.

## Archivos modificados

- [retailmind/app/views_modulo_gestion_precios.py](retailmind/app/views_modulo_gestion_precios.py) — endpoint `buscar_productos`
- [retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html](retailmind/app/templates/vistas/modulo_existencias/edicion_rapida_precios.html) — renderizado de cards
