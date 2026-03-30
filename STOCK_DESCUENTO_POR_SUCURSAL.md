# Stock por Sucursal — Diseño y Reglas de Negocio

> **Propósito:** Documento de referencia para implementar cualquier operación que descuente o modifique stock
> (venta boleta, factura, traspaso, devolución, ajuste).
> Reutilizar en futuros prompts para mantener consistencia.

---

## 1. Diseño del Modelo

### Regla fundamental
> **Un mismo producto físico genera UN `Producto` distinto por cada sucursal.**

```
MySQL talla (fuente):
  sku=4831847  articulo=009283628048  alias=EDEL   stock=0
  sku=4831847  articulo=009283628048  alias=NICK2  stock=24
  sku=4831847  articulo=009283628048  alias=NICK1  stock=5

Django (resultado del import):
  app_producto
    id=134767  articulo=009283628048  sucursal_id=1(EDEL)
    id=134768  articulo=009283628048  sucursal_id=7(NICK2)
    id=134769  articulo=009283628048  sucursal_id=6(NICK1)

  app_producto_talla
    sku=4831847  producto_id=134767  stock=0   talla=00  ← EDEL
    sku=4831847  producto_id=134768  stock=24  talla=00  ← NICK2
    sku=4831847  producto_id=134769  stock=5   talla=00  ← NICK1
```

### Modelos clave

| Modelo | Campo crítico | Descripción |
|--------|--------------|-------------|
| `Producto` | `sucursal = FK(Sucursal)` | Define a qué sucursal pertenece el producto |
| `Producto_Talla` | `stock = IntegerField` | **Fuente de verdad del stock actual** |
| `Producto_Talla` | `producto = FK(Producto)` | Enlaza talla → producto → sucursal |
| `Movimientos_Producto` | `ProductoTalla = FK(Producto_Talla)` | Registra cada movimiento de stock |
| `Movimientos_Producto` | `sucursal_origen = FK(Sucursal)` | Sucursal que origina el movimiento |

---

## 2. Cómo se Valida el Stock por Sucursal

Antes de cualquier operación, se llama a `stock_sucursal(sucursal_id)`:

```python
# app/models/catalogo.py
def stock_sucursal(self, sucursal_id):
    if sucursal_id is not None:
        sucursal_id = int(sucursal_id)
    # Solo retorna stock si el producto pertenece a ESTA sucursal
    if self.producto and self.producto.sucursal_id == sucursal_id:
        return max(0, self.stock or 0)
    return 0  # ← producto de otra sucursal = stock 0
```

**Obtener `sucursal_id` de la sesión:**
```python
sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
```

**Obtener `Producto_Talla` por SKU filtrado a la sucursal activa:**
```python
producto_talla = Producto_Talla.objects.select_related(
    'producto', 'producto__sucursal'
).get(
    sku=sku,
    producto__sucursal_id=sucursal_id  # ← CRÍTICO: filtrar por sucursal
)
stock_actual = producto_talla.stock_sucursal(sucursal_id)
if stock_actual <= 0:
    raise ValueError('Sin stock en esta sucursal')
```

> ⚠️ **Nunca** buscar solo por `sku=sku` sin filtrar `producto__sucursal_id`.
> El mismo SKU puede existir en múltiples sucursales con stocks distintos.

---

## 3. Cómo se Descuenta el Stock

### Patrón estándar (VENTA / BOLETA / FACTURA)

```python
from django.db.models import F

# 1. Validar stock
stock_actual = producto_talla.stock_sucursal(sucursal_id)
if stock_actual < cantidad:
    raise ValueError(f'Stock insuficiente. Disponible: {stock_actual}, Solicitado: {cantidad}')

# 2. Crear movimiento de egreso (registro histórico)
Movimientos_Producto.objects.create(
    ProductoTalla=producto_talla,
    sucursal_origen=sucursal,
    sucursal_destino=None,
    cantidad=-cantidad,           # ← NEGATIVO para egresos
    costo=producto_talla.producto.costo,
    sobreprecio=producto_talla.producto.sobreprecio,
    precio=precio_venta,
    concepto='VENTA_PUBLICO',     # o 'VENTA_MAYORISTA'
    tipo_movimiento='EGRESO',
    estado='COMPLETADO',
    responsable=request.user.username,
    dte=dte,                      # o ticket=ticket
)

# 3. Descontar stock (atómico con F() para evitar race conditions)
Producto_Talla.objects.filter(id=producto_talla.id).update(
    stock=F('stock') - cantidad
)
```

> ✅ Siempre usar `F('stock') - cantidad` (operación atómica en BD).
> ❌ Nunca `producto_talla.stock -= cantidad; producto_talla.save()` (race condition).

---

## 4. Conceptos y Tipos de Movimiento

| Operación | `concepto` | `tipo_movimiento` | `cantidad` |
|-----------|-----------|-------------------|-----------|
| Venta boleta/ticket | `VENTA_PUBLICO` | `EGRESO` | negativo |
| Venta factura | `VENTA_MAYORISTA` | `EGRESO` | negativo |
| Traspaso salida | `TRASPASO_SALIDA` | `EGRESO` | negativo |
| Traspaso entrada | `TRASPASO_ENTRADA` | `INGRESO` | positivo |
| Devolución cliente | `DEVOLUCION_CLIENTE` | `INGRESO` | positivo |
| Compra/recepción | `RECEPCION_COMPRA` | `INGRESO` | positivo |
| Ajuste positivo | `AJUSTE_POSITIVO` | `INGRESO` | positivo |
| Ajuste negativo | `AJUSTE_NEGATIVO` | `EGRESO` | negativo |

---

## 5. Cadena de FKs en una Venta

```
Ticket (boleta)
  └── TicketProducto
        └── ProductoTalla ──→ Producto ──→ Sucursal (debe coincidir con sesión)
              └── stock (campo directo, fuente de verdad)

DTE (factura/guía)
  └── Dte_Productos
        └── productoTalla ──→ Producto ──→ Sucursal (debe coincidir con DTE.sucursal)
              └── stock (campo directo, fuente de verdad)

Movimientos_Producto
  └── ProductoTalla ──→ Producto ──→ Sucursal
  └── sucursal_origen (debe ser la misma sucursal del producto)
  └── dte / ticket (opcional, referencia al documento)
```

---

## 6. Regla Anti-Cruce de Sucursales

> **El `Producto_Talla` usado en cualquier operación SIEMPRE debe pertenecer a la misma
> sucursal activa en sesión.**

**Validación obligatoria antes de procesar:**
```python
if producto_talla.producto.sucursal_id != int(sucursal_id):
    raise ValueError(
        f'Producto pertenece a {producto_talla.producto.sucursal.alias}, '
        f'no a la sucursal activa.'
    )
```

**Por qué existe este riesgo:** El mismo SKU puede estar en múltiples sucursales.
Si se busca solo por `sku` sin filtrar sucursal, se puede obtener el `Producto_Talla`
de otra sucursal y descontar stock del lugar equivocado.

---

## 7. Sincronización con Import (migrate_from_laravel)

- `migrate_producto_talla` usa `bulk_create` para **insertar** nuevos y `bulk_update` para **actualizar stock** de existentes.
- `preload_caches()` carga `cache_producto_talla` = `dict["sku:alias" → Producto_Talla]`.
- `find_producto_talla_fast(sku, alias)` busca primero por `"sku:alias"` (preciso) y hace fallback a `by_sku` (riesgo de cruce si alias es None).
- Los `Dte_Productos` con cruce de sucursal ocurren cuando el `Producto_Talla` de la sucursal correcta no existe al momento de importar `dte_productos`.

**Fix de Dte_Productos cruzados:**
```sql
-- 1. Detectar
SELECT s_dte.alias AS sucursal_dte, s_prod.alias AS sucursal_producto, COUNT(*) AS n
FROM app_dte_productos dp
JOIN app_dte d             ON dp.dte_id = d.id
JOIN app_producto_talla pt ON dp."productoTalla_id" = pt.id
JOIN app_producto p        ON pt.producto_id = p.id
JOIN app_sucursal s_dte    ON d.sucursal_id = s_dte.id
JOIN app_sucursal s_prod   ON p.sucursal_id = s_prod.id
WHERE d.sucursal_id != p.sucursal_id
GROUP BY s_dte.alias, s_prod.alias ORDER BY n DESC;

-- 2. Eliminar cruzados
DELETE FROM app_dte_productos
WHERE id IN (
    SELECT dp.id FROM app_dte_productos dp
    JOIN app_dte d             ON dp.dte_id = d.id
    JOIN app_producto_talla pt ON dp."productoTalla_id" = pt.id
    JOIN app_producto p        ON pt.producto_id = p.id
    WHERE d.sucursal_id IS NOT NULL AND p.sucursal_id != d.sucursal_id
);

-- 3. Re-importar (con caché fresco tras haber creado los Producto_Talla faltantes)
-- python manage.py migrate_from_laravel --tables dte_productos --no-input
```

---

## 8. Checklist para Implementar Nueva Operación con Stock

- [ ] Obtener `sucursal_id` de `request.session.get('idSucursalActual')`
- [ ] Buscar `Producto_Talla` con `producto__sucursal_id=sucursal_id` (nunca solo por sku)
- [ ] Validar `stock_sucursal(sucursal_id) >= cantidad`
- [ ] Crear `Movimientos_Producto` con `concepto` y `tipo_movimiento` correctos, `cantidad` negativa para egresos
- [ ] Actualizar stock con `Producto_Talla.objects.filter(id=...).update(stock=F('stock') - cantidad)`
- [ ] Envolver todo en `transaction.atomic()` para garantizar consistencia
- [ ] Si es DTE: vincular `Movimientos_Producto.dte = dte`
- [ ] Si es Ticket/Boleta: vincular `Movimientos_Producto.ticket = ticket`
