# Comparación Migración MySQL (Laravel) → PostgreSQL (Django)

> Base MySQL: `dbHoldingTebes` — Tabla principal: **`talla`**
> Base PostgreSQL: `retail` — Tablas: **`app_producto`**, **`app_producto_talla`**, **`app_sucursal`**

---

## 1. Resumen General — Conteos Totales

### MySQL

```sql
SELECT 'Total registros talla' AS concepto, COUNT(*) AS total FROM talla
UNION ALL
SELECT 'Con codigo_asociado (SKU)', COUNT(*) FROM talla WHERE codigo_asociado IS NOT NULL
UNION ALL
SELECT 'Productos agrupados', COUNT(DISTINCT CONCAT(IFNULL(articulo,''), '|', IFNULL(alias,''), '|', IFNULL(marca,''), '|', IFNULL(color,''))) FROM talla WHERE articulo IS NOT NULL
UNION ALL
SELECT 'SKUs unicos', COUNT(DISTINCT codigo_asociado) FROM talla WHERE codigo_asociado IS NOT NULL
UNION ALL
SELECT 'Sucursales (alias)', COUNT(DISTINCT alias) FROM talla
UNION ALL
SELECT 'Categorias (familia)', COUNT(DISTINCT familia) FROM talla WHERE familia IS NOT NULL
UNION ALL
SELECT 'Marcas', COUNT(DISTINCT marca) FROM talla WHERE marca IS NOT NULL
UNION ALL
SELECT 'Colores', COUNT(DISTINCT color) FROM talla WHERE color IS NOT NULL
UNION ALL
SELECT 'Stock total', SUM(stock) FROM talla WHERE codigo_asociado IS NOT NULL
UNION ALL
SELECT 'Clientes', COUNT(*) FROM cliente
UNION ALL
SELECT 'Vendedores', COUNT(*) FROM vendedores
UNION ALL
SELECT 'Movimientos', COUNT(*) FROM movimiento_productos
UNION ALL
SELECT 'DTEs', COUNT(*) FROM dte
UNION ALL
SELECT 'Productos DTE', COUNT(*) FROM productos_dte;
```

### PostgreSQL

```sql
SELECT 'Productos' AS concepto, COUNT(*) AS total FROM app_producto
UNION ALL
SELECT 'SKUs (producto_talla)', COUNT(*) FROM app_producto_talla
UNION ALL
SELECT 'Sucursales', COUNT(*) FROM app_sucursal
UNION ALL
SELECT 'Categorias', COUNT(*) FROM app_categoria
UNION ALL
SELECT 'Opciones atributo', COUNT(*) FROM app_atributoopcion
UNION ALL
SELECT 'Stock total', SUM(stock) FROM app_producto_talla
UNION ALL
SELECT 'Empresas (incluye clientes)', COUNT(*) FROM app_empresa
UNION ALL
SELECT 'Vendedores', COUNT(*) FROM app_vendedor
UNION ALL
SELECT 'Movimientos', COUNT(*) FROM app_movimientos_producto
UNION ALL
SELECT 'DTEs', COUNT(*) FROM app_dte
UNION ALL
SELECT 'Productos DTE', COUNT(*) FROM app_dte_productos;
```

---

## 2. Stock Total por Sucursal

### MySQL

```sql
SELECT
    alias                       AS sucursal,
    COUNT(DISTINCT CONCAT(IFNULL(articulo,''), '|', IFNULL(marca,''), '|', IFNULL(color,'')))
                                AS productos,
    COUNT(*)                    AS tallas_skus,
    SUM(stock)                  AS stock_total,
    SUM(CASE WHEN stock > 0 THEN 1 ELSE 0 END) AS skus_con_stock,
    SUM(CASE WHEN stock = 0 THEN 1 ELSE 0 END) AS skus_sin_stock
FROM talla
WHERE codigo_asociado IS NOT NULL
GROUP BY alias
ORDER BY alias;
```

### PostgreSQL

```sql
SELECT
    s.alias                     AS sucursal,
    COUNT(DISTINCT p.id)        AS productos,
    COUNT(pt.id)                AS tallas_skus,
    SUM(pt.stock)               AS stock_total,
    SUM(CASE WHEN pt.stock > 0 THEN 1 ELSE 0 END) AS skus_con_stock,
    SUM(CASE WHEN pt.stock = 0 THEN 1 ELSE 0 END) AS skus_sin_stock
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
JOIN app_sucursal s ON p.sucursal_id = s.id
GROUP BY s.alias
ORDER BY s.alias;
```

---

## 3. Productos Agrupados por Sucursal y Marca

### MySQL

```sql
SELECT
    alias                       AS sucursal,
    IFNULL(marca, 'SIN MARCA')  AS marca,
    COUNT(DISTINCT articulo)    AS productos,
    COUNT(*)                    AS tallas,
    SUM(stock)                  AS stock_total
FROM talla
WHERE articulo IS NOT NULL
GROUP BY alias, marca
ORDER BY alias, stock_total DESC;
```

### PostgreSQL

```sql
SELECT
    s.alias                             AS sucursal,
    COALESCE(ao.valor, 'SIN MARCA')     AS marca,
    COUNT(DISTINCT p.id)                AS productos,
    COUNT(pt.id)                        AS tallas,
    SUM(pt.stock)                       AS stock_total
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
JOIN app_sucursal s ON p.sucursal_id = s.id
LEFT JOIN app_atributoopcion ao ON p.atributo1_id = ao.id
GROUP BY s.alias, ao.valor
ORDER BY s.alias, stock_total DESC;
```

---

## 4. Tallas por Producto — Detalle de SKUs

### MySQL

```sql
SELECT
    alias                       AS sucursal,
    articulo,
    descripcion,
    marca,
    color,
    COUNT(*)                    AS cantidad_tallas,
    SUM(stock)                  AS stock_total,
    GROUP_CONCAT(CONCAT(talla, ':', stock) ORDER BY talla SEPARATOR ' | ') AS detalle_tallas
FROM talla
WHERE codigo_asociado IS NOT NULL
GROUP BY alias, articulo, descripcion, marca, color
ORDER BY alias, stock_total DESC
LIMIT 50;
```

### PostgreSQL

```sql
SELECT
    s.alias                     AS sucursal,
    p.articulo,
    p.descripcion,
    ao_marca.valor              AS marca,
    ao_color.valor              AS color,
    COUNT(pt.id)                AS cantidad_tallas,
    SUM(pt.stock)               AS stock_total,
    STRING_AGG(pt.talla || ':' || pt.stock, ' | ' ORDER BY pt.talla) AS detalle_tallas
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
JOIN app_sucursal s ON p.sucursal_id = s.id
LEFT JOIN app_atributoopcion ao_marca ON p.atributo1_id = ao_marca.id
LEFT JOIN app_atributoopcion ao_color ON p.atributo2_id = ao_color.id
GROUP BY s.alias, p.articulo, p.descripcion, ao_marca.valor, ao_color.valor
ORDER BY s.alias, stock_total DESC
LIMIT 50;
```

---

## 5. Distribución de Tallas (Curva de Tallas)

### MySQL

```sql
SELECT
    talla,
    COUNT(*)                    AS cantidad_skus,
    SUM(stock)                  AS stock_total,
    COUNT(DISTINCT articulo)    AS productos_distintos
FROM talla
WHERE codigo_asociado IS NOT NULL
GROUP BY talla
ORDER BY stock_total DESC;
```

### PostgreSQL

```sql
SELECT
    pt.talla,
    COUNT(*)                    AS cantidad_skus,
    SUM(pt.stock)               AS stock_total,
    COUNT(DISTINCT p.id)        AS productos_distintos
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
GROUP BY pt.talla
ORDER BY stock_total DESC;
```

---

## 6. Categorías (Familia) por Sucursal

### MySQL

```sql
SELECT
    alias                       AS sucursal,
    IFNULL(familia, 'SIN FAMILIA') AS categoria,
    COUNT(DISTINCT articulo)    AS productos,
    COUNT(*)                    AS tallas,
    SUM(stock)                  AS stock_total
FROM talla
WHERE codigo_asociado IS NOT NULL
GROUP BY alias, familia
ORDER BY alias, stock_total DESC;
```

### PostgreSQL

```sql
SELECT
    s.alias                             AS sucursal,
    COALESCE(c.nombre, 'SIN FAMILIA')   AS categoria,
    COUNT(DISTINCT p.id)                AS productos,
    COUNT(pt.id)                        AS tallas,
    SUM(pt.stock)                       AS stock_total
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
JOIN app_sucursal s ON p.sucursal_id = s.id
LEFT JOIN app_categoria c ON p.categoria_id = c.id
GROUP BY s.alias, c.nombre
ORDER BY s.alias, stock_total DESC;
```

---

## 7. Precios — Validar Costo, Sobreprecio, Precio Venta

### MySQL

```sql
SELECT
    alias                           AS sucursal,
    COUNT(*)                        AS total_registros,
    ROUND(AVG(costo), 0)           AS costo_promedio,
    ROUND(AVG(preciointerno), 0)   AS precio_interno_prom,
    ROUND(AVG(precioventapublico), 0) AS precio_venta_prom,
    SUM(stock * costo)             AS valor_inventario_costo,
    SUM(stock * precioventapublico) AS valor_inventario_venta
FROM talla
WHERE codigo_asociado IS NOT NULL
GROUP BY alias
ORDER BY alias;
```

### PostgreSQL

```sql
SELECT
    s.alias                         AS sucursal,
    COUNT(*)                        AS total_registros,
    ROUND(AVG(p.costo), 0)         AS costo_promedio,
    ROUND(AVG(p.sobreprecio + p.costo), 0) AS precio_interno_prom,
    ROUND(AVG(p.precioventa), 0)   AS precio_venta_prom,
    SUM(pt.stock * p.costo)        AS valor_inventario_costo,
    SUM(pt.stock * p.precioventa)  AS valor_inventario_venta
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
JOIN app_sucursal s ON p.sucursal_id = s.id
GROUP BY s.alias
ORDER BY s.alias;
```

---

## 8. SKUs Específicos — Verificar Registros Puntuales

### MySQL (buscar un articulo)

```sql
SELECT
    codigo_asociado AS sku,
    articulo,
    descripcion,
    marca,
    color,
    talla,
    stock,
    costo,
    preciointerno,
    precioventapublico,
    alias AS sucursal
FROM talla
WHERE articulo = 'TU_ARTICULO_AQUI'
ORDER BY alias, talla;
```

### PostgreSQL (buscar el mismo articulo)

```sql
SELECT
    pt.sku,
    p.articulo,
    p.descripcion,
    ao_marca.valor      AS marca,
    ao_color.valor      AS color,
    pt.talla,
    pt.stock,
    p.costo,
    p.sobreprecio,
    p.precioventa,
    s.alias             AS sucursal
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
JOIN app_sucursal s ON p.sucursal_id = s.id
LEFT JOIN app_atributoopcion ao_marca ON p.atributo1_id = ao_marca.id
LEFT JOIN app_atributoopcion ao_color ON p.atributo2_id = ao_color.id
WHERE p.articulo = 'TU_ARTICULO_AQUI'
ORDER BY s.alias, pt.talla;
```

---

## 9. Registros sin Migrar — Lo que se omitió

### MySQL: SKUs sin alias (no tienen sucursal → se omiten)

```sql
SELECT COUNT(*) AS skus_sin_sucursal
FROM talla
WHERE codigo_asociado IS NOT NULL
  AND (alias IS NULL OR alias = '');
```

### MySQL: SKUs con alias que no existe en el mapeo

```sql
SELECT
    alias,
    COUNT(*) AS registros
FROM talla
WHERE codigo_asociado IS NOT NULL
  AND alias NOT IN ('PA00','PAO0','PAO1','PAO2','PAO3','PAO4','EDEL','EDEL FALLADOS','GILD','IMP','NICK1','NICK2','NICK3')
GROUP BY alias
ORDER BY registros DESC;
```

### MySQL: Tallas sin articulo

```sql
SELECT COUNT(*) AS sin_articulo
FROM talla
WHERE articulo IS NULL OR articulo = '';
```

---

## 10. Movimientos por Sucursal

### MySQL

```sql
SELECT
    alias                       AS sucursal,
    tipo_movimiento,
    COUNT(*)                    AS cantidad,
    SUM(cantidad)               AS unidades
FROM movimiento_productos
GROUP BY alias, tipo_movimiento
ORDER BY alias, tipo_movimiento;
```

### PostgreSQL

```sql
SELECT
    s.alias                     AS sucursal,
    m.tipo_movimiento,
    COUNT(*)                    AS cantidad,
    SUM(m.cantidad)             AS unidades
FROM app_movimientos_producto m
LEFT JOIN app_sucursal s ON m.sucursal_origen_id = s.id
GROUP BY s.alias, m.tipo_movimiento
ORDER BY s.alias, m.tipo_movimiento;
```

---

## 11. DTEs por Sucursal

### MySQL

```sql
SELECT
    bodega_inicio               AS sucursal,
    tipo_documento,
    COUNT(*)                    AS total_dtes,
    SUM(monto_total)            AS monto_total
FROM dte
GROUP BY bodega_inicio, tipo_documento
ORDER BY bodega_inicio, tipo_documento;
```

### PostgreSQL

```sql
SELECT
    s.alias                     AS sucursal,
    d.tipo_documento,
    COUNT(*)                    AS total_dtes,
    SUM(d.monto_con_iva)       AS monto_total
FROM app_dte d
LEFT JOIN app_sucursal s ON d.sucursal_id = s.id
GROUP BY s.alias, d.tipo_documento
ORDER BY s.alias, d.tipo_documento;
```

---

## Mapeo de Tablas Rápido

| MySQL (Laravel)       | PostgreSQL (Django)                       | Notas                                       |
|-----------------------|-------------------------------------------|---------------------------------------------|
| `talla`               | `app_producto` + `app_producto_talla`     | Se agrupa por articulo/alias/marca/color    |
| `talla.codigo_asociado` | `app_producto_talla.sku`                | SKU único por talla                         |
| `talla.alias`         | `app_sucursal.alias`                      | Mapeo fijo en `EMPRESA_RUT_MAP`             |
| `talla.familia`       | `app_categoria.nombre`                    | Cada familia = 1 categoría                  |
| `talla.marca`         | `app_atributoopcion` (atributo=Marca)     | `producto.atributo1_id`                     |
| `talla.color`         | `app_atributoopcion` (atributo=Color)     | `producto.atributo2_id`                     |
| `talla.sexo`          | `app_atributoopcion` (atributo=Sexo)      | `producto.atributo3_id`                     |
| `talla.costo`         | `app_producto.costo`                      | Directo                                     |
| `talla.preciointerno` | `app_producto.costo + sobreprecio`        | `sobreprecio = preciointerno - costo`       |
| `talla.precioventapublico` | `app_producto.precioventa`           | Directo                                     |
| `cliente`             | `app_empresa`                             | Clientes se crean como Empresa              |
| `vendedores`          | `app_vendedor`                            | M2M con sucursales                          |
| `movimiento_productos`| `app_movimientos_producto`                | Egresos → cantidad negativa en Django       |
| `dte`                 | `app_dte`                                 | `monto_total` → `monto_con_iva`            |
| `productos_dte`       | `app_dte_productos`                       | Vinculado por DTE + SKU                     |

---

## Sucursales Válidas (Mapeo Fijo)

| Alias          | Empresa              | RUT          | Dirección     |
|----------------|----------------------|--------------|---------------|
| PA00 / PAO0    | Vicent Paola         | 78503140-7   | Maipu 676     |
| PAO1           | Vicent Paola         | 78503140-7   | Maipu 668     |
| PAO2           | Vicent Paola         | 78503140-7   | Matta 2422    |
| PAO3           | Vicent Paola         | 78503140-7   | Matta 2432    |
| PAO4           | Vicent Paola         | 78503140-7   | Matta 2458    |
| EDEL           | Edelmira Tebes Ltda  | 76337843-8   | Maipu 676     |
| EDEL FALLADOS  | Edelmira Tebes Ltda  | 76337843-8   | Maipu 676     |
| GILD           | Edelmira Gilda Tebes | 7397811-4    | Maipu 676     |
| IMP            | Importadora Nicolas  | 76104936-4   | Maipu 676     |
| NICK1          | Importadora Nicolas  | 76104936-4   | Matta 2479    |
| NICK2          | Importadora Nicolas  | 76104936-4   | Matta 2438    |
| NICK3          | Importadora Nicolas  | 76104936-4   | Matta 2418    |
