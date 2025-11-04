# 🔍 Análisis: Cómo Funciona la Recepción de DTE

## 📋 Flujo Completo de Emisión → Recepción

### 1️⃣ EMISIÓN DTE (En EDEL - Sucursal Origen)

**URL:** `http://localhost:8000/app/emisionDTE/`

#### Acción del Usuario:
1. Selecciona **Despacho Interno**
2. Selecciona **Sucursal Destino**: NICK1
3. Agrega productos y cantidades
4. Emite el documento

#### Lo que hace el sistema:
```python
# views.py - Función emitir_dte() - Línea 6786-6812

# ✅ Reduce stock INMEDIATAMENTE
talla.stock -= cantidad
talla.save()

# ✅ Crea el DTE
dte = Dte.objects.create(
    emisor=empresa_EDEL,
    receptor=empresa_NICK1,
    numero_documento=12345,
    tipo_documento='FACTURA ELECTRONICA',
    tipo_transaccion='TRASPASO',  # ← Importante
    estado_dte='EMITIDO',
    sucursal=sucursal_EDEL,
    fecha_recepcion=None,  # ← NULL hasta que se recepcione
    ...
)

# ✅ Crea movimiento de EGRESO
Movimientos_Producto.objects.create(
    dte=dte,
    ProductoTalla=talla,
    sucursal_origen=sucursal_EDEL,
    sucursal_destino=sucursal_NICK1,
    cantidad=-50,  # Negativo = egreso
    concepto='TRASPASO_SALIDA',  # ← Importante
    tipo_movimiento='EGRESO',  # ← Importante
    estado='PENDIENTE_RECEPCION',  # ← Importante
    ...
)
```

#### Estado en Base de Datos:
```sql
-- Tabla: app_dte
id | emisor_id | receptor_id | numero_documento | tipo_transaccion | estado_dte | sucursal_id | fecha_recepcion
1  | 1 (EDEL)  | 2 (NICK1)   | 12345           | TRASPASO         | EMITIDO    | 1 (EDEL)    | NULL

-- Tabla: app_movimientos_producto
id | dte_id | ProductoTalla_id | sucursal_origen_id | sucursal_destino_id | cantidad | concepto        | tipo_movimiento | estado
1  | 1      | 123              | 1 (EDEL)           | 2 (NICK1)           | -50      | TRASPASO_SALIDA | EGRESO         | PENDIENTE_RECEPCION

-- Tabla: app_producto_talla
id  | sku      | stock
123 | ZAP-42   | 150   (era 200, se redujo 50 al emitir)
```

---

### 2️⃣ BÚSQUEDA DE DTE PARA RECEPCIONAR (En NICK1 - Sucursal Destino)

**URL:** `http://localhost:8000/app/recepcion-dte/`

#### Query Principal (Líneas 79-95):
```python
# views.py - Función recepciones_pendientes_api()

sucursal_destino_id = request.session.get('idSucursalActual')  # = 2 (NICK1)

queryset = Dte.objects.filter(
    # ✅ Filtro 1: Solo traspasos
    tipo_transaccion='TRASPASO',
    
    # ✅ Filtro 2: Solo emitidos (no anulados, no completados aún)
    estado_dte='EMITIDO',
    
    # ✅ Filtro 3: Que NO hayan sido recepcionados aún
    fecha_recepcion__isnull=True,
    
    # ✅ Filtro 4: Que tengan movimientos de salida
    dte_movimientos__concepto='TRASPASO_SALIDA',
    
    # ✅ Filtro 5: Que sean egresos
    dte_movimientos__tipo_movimiento='EGRESO',
    
    # ✅ Filtro 6: Que estén pendientes de recepción
    dte_movimientos__estado='PENDIENTE_RECEPCION',
    
    # ✅ Filtro 7: Que la sucursal destino sea la actual (NICK1)
    dte_movimientos__sucursal_destino_id=sucursal_destino_id  # = 2
)
```

#### ¿Por qué esta query funciona?

**Relación entre modelos:**

```
DTE (id=1)
  ├─ tipo_transaccion = 'TRASPASO'
  ├─ estado_dte = 'EMITIDO'
  ├─ fecha_recepcion = NULL
  ├─ sucursal_id = 1 (EDEL - origen)
  └─ dte_movimientos (related_name en Movimientos_Producto)
      └─ Movimientos_Producto (id=1)
          ├─ concepto = 'TRASPASO_SALIDA'
          ├─ tipo_movimiento = 'EGRESO'
          ├─ estado = 'PENDIENTE_RECEPCION'
          ├─ sucursal_origen_id = 1 (EDEL)
          └─ sucursal_destino_id = 2 (NICK1) ← MATCH!
```

**Resultado:** El DTE #12345 aparece en la lista de recepciones pendientes de NICK1 ✅

#### Doble Verificación (Líneas 131-139):
```python
# Por cada DTE encontrado, verificar nuevamente los movimientos
movimientos_salida = [
    mov for mov in dte.dte_movimientos.all()
    if mov.concepto == 'TRASPASO_SALIDA'
    and mov.estado == 'PENDIENTE_RECEPCION'  # ✅ CORREGIDO
    and mov.sucursal_destino_id == sucursal_destino_id
]

if not movimientos_salida:
    continue  # Saltar este DTE si no tiene movimientos válidos
```

**Razón de esta doble verificación:**
- El `.distinct()` en el query puede traer duplicados
- Asegura que el movimiento sea exactamente para esta sucursal
- Evita mostrar DTEs que no corresponden

---

### 3️⃣ CONFIRMACIÓN DE RECEPCIÓN (En NICK1)

**URL:** `POST /app/dte/confirmar_recepcion/`

#### Payload:
```json
{
  "dte_id": 1,
  "observaciones_recepcion": "Recibido conforme"
}
```

#### Lo que hace el sistema (Líneas 297-399):
```python
# views.py - Función confirmar_recepcion_api()

with transaction.atomic():
    # ✅ 1. Actualizar DTE
    dte.fecha_recepcion = hoy.date()
    dte.hora = hoy.time()
    dte.estado_dte = 'ACEPTADO'
    dte.save()
    
    # ✅ 2. Marcar movimientos de salida como COMPLETADOS
    movimientos_salida = Movimientos_Producto.objects.filter(
        dte=dte,
        concepto='TRASPASO_SALIDA',
        estado='PENDIENTE_RECEPCION'
    )
    movimientos_salida.update(estado='COMPLETADO')
    
    # ✅ 3. Aumentar stock en destino
    for detalle in dte.dte_productos.all():
        producto_talla = detalle.productoTalla
        cantidad = detalle.stock
        
        producto_talla.stock += cantidad  # 100 + 50 = 150
        producto_talla.save()
    
    # ✅ 4. Crear movimiento de INGRESO en destino
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=producto_talla,
        sucursal_origen=sucursal_EDEL,
        sucursal_destino=sucursal_NICK1,
        cantidad=+50,  # Positivo = ingreso
        concepto='TRASPASO_ENTRADA',
        tipo_movimiento='INGRESO',
        estado='COMPLETADO',
        responsable='usuario_nick1',
        ...
    )
```

#### Estado Final en Base de Datos:
```sql
-- Tabla: app_dte
id | fecha_recepcion | estado_dte
1  | 2025-10-27      | ACEPTADO

-- Tabla: app_movimientos_producto
id | dte_id | sucursal_origen_id | sucursal_destino_id | cantidad | concepto           | tipo_movimiento | estado
1  | 1      | 1 (EDEL)           | 2 (NICK1)           | -50      | TRASPASO_SALIDA    | EGRESO         | COMPLETADO
2  | 1      | 1 (EDEL)           | 2 (NICK1)           | +50      | TRASPASO_ENTRADA   | INGRESO        | COMPLETADO

-- Tabla: app_producto_talla (En EDEL - sin cambios)
id  | sku      | stock
123 | ZAP-42   | 150   (ya se había reducido al emitir)

-- Tabla: app_producto_talla (En NICK1 - aumenta)
id  | sku      | stock
456 | ZAP-42   | 150   (era 100, aumentó 50 al recepcionar)
```

---

## 🔑 Campos Clave para el Flujo

### Tabla `app_dte`:
| Campo | Valor al Emitir | Valor al Recepcionar | Uso en Query |
|-------|----------------|---------------------|--------------|
| `tipo_transaccion` | `'TRASPASO'` | Sin cambio | ✅ Filtro principal |
| `estado_dte` | `'EMITIDO'` | `'ACEPTADO'` | ✅ Filtro (solo EMITIDO) |
| `fecha_recepcion` | `NULL` | `'2025-10-27'` | ✅ Filtro (`isnull=True`) |
| `sucursal_id` | Sucursal ORIGEN | Sin cambio | ℹ️ Informativo |

### Tabla `app_movimientos_producto`:
| Campo | Valor al Emitir | Valor al Recepcionar |
|-------|----------------|---------------------|
| `concepto` | `'TRASPASO_SALIDA'` | Sin cambio (se crea nuevo `'TRASPASO_ENTRADA'`) |
| `tipo_movimiento` | `'EGRESO'` | Sin cambio (nuevo es `'INGRESO'`) |
| `estado` | `'PENDIENTE_RECEPCION'` | `'COMPLETADO'` |
| `cantidad` | `-50` (negativo) | Sin cambio (nuevo es `+50`) |
| `sucursal_origen_id` | ID de EDEL | Sin cambio |
| `sucursal_destino_id` | ID de NICK1 | ✅ **CLAVE PARA QUERY** |

---

## 🐛 Bug Corregido

**ANTES (Línea 134):**
```python
and mov.estado == 'PENDIENTE'  # ❌ Estado antiguo
```

**AHORA (Línea 134):**
```python
and mov.estado == 'PENDIENTE_RECEPCION'  # ✅ Estado nuevo
```

**Problema que causaba:**
- El query principal encontraba el DTE
- Pero el filtro secundario no encontraba el movimiento
- Resultado: `continue` y el DTE no se mostraba en la lista

**Ahora funciona correctamente:**
- Query principal encuentra el DTE ✅
- Filtro secundario encuentra el movimiento ✅
- DTE se muestra en la lista de recepciones pendientes ✅

---

## 📊 Diagrama del Flujo

```
┌─────────────────────────────────────────────────────────────┐
│                   EMISIÓN (EDEL)                            │
├─────────────────────────────────────────────────────────────┤
│ 1. Usuario emite DTE interno a NICK1                        │
│ 2. Stock EDEL: 200 → 150 (-50) ✅                          │
│ 3. Crea DTE:                                                │
│    - tipo_transaccion = 'TRASPASO'                          │
│    - estado_dte = 'EMITIDO'                                 │
│    - fecha_recepcion = NULL                                 │
│ 4. Crea Movimiento:                                         │
│    - concepto = 'TRASPASO_SALIDA'                           │
│    - tipo_movimiento = 'EGRESO'                             │
│    - estado = 'PENDIENTE_RECEPCION'                         │
│    - sucursal_destino_id = NICK1                            │
│    - cantidad = -50                                         │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│              CONSULTA RECEPCIONES (NICK1)                   │
├─────────────────────────────────────────────────────────────┤
│ 1. Usuario en NICK1 accede a /app/recepcion-dte/           │
│ 2. Query busca DTEs:                                        │
│    WHERE tipo_transaccion = 'TRASPASO'                      │
│      AND estado_dte = 'EMITIDO'                             │
│      AND fecha_recepcion IS NULL                            │
│      AND dte_movimientos.concepto = 'TRASPASO_SALIDA'       │
│      AND dte_movimientos.tipo_movimiento = 'EGRESO'         │
│      AND dte_movimientos.estado = 'PENDIENTE_RECEPCION'     │
│      AND dte_movimientos.sucursal_destino_id = NICK1        │
│ 3. Encuentra DTE #12345 ✅                                  │
│ 4. Muestra en lista de pendientes                           │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│           CONFIRMACIÓN RECEPCIÓN (NICK1)                    │
├─────────────────────────────────────────────────────────────┤
│ 1. Usuario confirma recepción                               │
│ 2. Actualiza DTE:                                           │
│    - fecha_recepcion = HOY                                  │
│    - estado_dte = 'ACEPTADO'                                │
│ 3. Actualiza Movimiento Salida:                             │
│    - estado = 'COMPLETADO'                                  │
│ 4. Stock NICK1: 100 → 150 (+50) ✅                         │
│ 5. Crea Movimiento Entrada:                                 │
│    - concepto = 'TRASPASO_ENTRADA'                          │
│    - tipo_movimiento = 'INGRESO'                            │
│    - estado = 'COMPLETADO'                                  │
│    - cantidad = +50                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ Verificación del Sistema

Para verificar que todo funciona:

### 1. Emitir DTE en EDEL
```sql
-- Verificar que se creó el movimiento
SELECT * FROM app_movimientos_producto 
WHERE concepto = 'TRASPASO_SALIDA' 
  AND estado = 'PENDIENTE_RECEPCION'
  AND sucursal_destino_id = 2;  -- ID de NICK1
```

### 2. Ver DTE en NICK1
```sql
-- Verificar que el DTE aparece en la query
SELECT d.id, d.numero_documento, d.tipo_transaccion, d.estado_dte, d.fecha_recepcion
FROM app_dte d
INNER JOIN app_movimientos_producto mp ON mp.dte_id = d.id
WHERE d.tipo_transaccion = 'TRASPASO'
  AND d.estado_dte = 'EMITIDO'
  AND d.fecha_recepcion IS NULL
  AND mp.concepto = 'TRASPASO_SALIDA'
  AND mp.tipo_movimiento = 'EGRESO'
  AND mp.estado = 'PENDIENTE_RECEPCION'
  AND mp.sucursal_destino_id = 2;  -- ID de NICK1
```

### 3. Después de Recepcionar
```sql
-- Verificar que se completó
SELECT * FROM app_dte WHERE id = 1;
-- fecha_recepcion debe tener fecha, estado_dte = 'ACEPTADO'

-- Verificar movimientos
SELECT * FROM app_movimientos_producto WHERE dte_id = 1;
-- Debe haber 2 registros:
--   1. TRASPASO_SALIDA | EGRESO | COMPLETADO | -50
--   2. TRASPASO_ENTRADA | INGRESO | COMPLETADO | +50
```

---

## 🎯 Resumen

**El sistema funciona así:**

1. ✅ Al **emitir** DTE interno → Stock se reduce en ORIGEN
2. ✅ La **query** busca DTEs con `estado='PENDIENTE_RECEPCION'` y `sucursal_destino_id=ACTUAL`
3. ✅ Al **recepcionar** → Stock aumenta en DESTINO
4. ✅ Los movimientos quedan `COMPLETADO` en ambas sucursales

**Campos clave:**
- `dte.tipo_transaccion = 'TRASPASO'`
- `dte.fecha_recepcion IS NULL`
- `movimiento.estado = 'PENDIENTE_RECEPCION'`
- `movimiento.sucursal_destino_id = sucursal_actual`

Todo está **correctamente configurado** ahora ✅

