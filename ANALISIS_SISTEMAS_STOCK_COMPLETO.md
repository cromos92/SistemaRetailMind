# 📊 ANÁLISIS COMPLETO: Sistemas de Stock en RetailMind

## 🎯 RESUMEN EJECUTIVO

El sistema **SÍ usa ambos sistemas de stock de forma COORDINADA**:

1. **Campo `Producto_Talla.stock`** (Stock Legacy) - Se actualiza manualmente
2. **Tabla `Movimientos_Producto`** (Stock por Movimientos) - Sistema moderno

**CONCLUSIÓN:** ✅ El sistema está **bien diseñado** y usa **AMBOS** sistemas de forma complementaria.

---

## 📋 ANÁLISIS POR MÓDULO

### 1. **Creación de Productos** (`/app/verGestionProducto/`)

**Archivo:** `views.py` - Función `crear_producto()` (línea 7901)

**¿Qué hace?**

```python
for talla_data in data['tallas']:
    # ✅ 1. Crea la talla con STOCK LEGACY
    producto_talla = Producto_Talla.objects.create(
        producto=producto,
        sku=obtener_siguiente_sku(),
        stock=talla_data['stock'],  # ← ASIGNA STOCK LEGACY
        talla=talla_data['talla']
    )

    # ✅ 2. Crea el MOVIMIENTO de ingreso inicial
    Movimientos_Producto.objects.create(
        ProductoTalla=producto_talla,
        costo=producto.costo,
        sobreprecio=producto.sobreprecio,
        precio=producto.precioventa,
        concepto='Ingreso Inicial',
        tipo_movimiento='INGRESO',  # ← REGISTRA EN MOVIMIENTOS
        responsable=responsable
    )
```

**Resultado:**
- ✅ Asigna `stock` legacy inicial
- ✅ Crea movimiento de ingreso en `Movimientos_Producto`
- ✅ **AMBOS sistemas quedan sincronizados**

---

### 2. **Emisión de DTE a otra sucursal** (`/app/emisionDTE/`)

**Archivo:** `views.py` - Función `emitir_dte()` (línea 14388)

**Despacho Externo (Venta a cliente):**
```python
if metodo_despacho == 'externo':
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=talla,
        sucursal_origen=sucursal,
        sucursal_destino=None,
        cantidad=-cantidad,  # ✅ Negativo (egreso)
        concepto='VENTA_MAYORISTA',
        tipo_movimiento='EGRESO',
        estado='COMPLETADO',
        ...
    )
    
    # ✅ NO modifica talla.stock
    # El stock se calcula desde movimientos
```

**Despacho Interno (Traspaso entre sucursales):**
```python
else:  # metodo_despacho == 'interno'
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=talla,
        sucursal_origen=sucursal,
        sucursal_destino=sucursal_destino,
        cantidad=-cantidad,  # ✅ Negativo (egreso)
        concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO',
        estado='COMPLETADO',
        ...
    )
    
    # ✅ NO modifica talla.stock
    # El stock se calcula desde movimientos
```

**Resultado:**
- ✅ Crea movimiento de EGRESO en sucursal origen
- ❌ **NO modifica** `talla.stock` legacy
- ✅ El stock real se calcula con `talla.stock_sucursal(sucursal_id)`
- ⚠️ **IMPORTANTE:** Solo crea el egreso en origen. El ingreso en destino se crea al recepcionar.

---

### 3. **Recepción de DTE** (`/app/regularizar-recepciones/`)

**Archivo:** `views.py` - Función `confirmar_recepcion_api()` (línea 322)

```python
# Al recepcionar productos en sucursal destino:

# 1. Crea movimiento de INGRESO
movimientos_a_crear.append(Movimientos_Producto(
    dte=dte,
    ProductoTalla=talla_destino,
    sucursal_origen=dte.sucursal,
    sucursal_destino=sucursal_destino,
    cantidad=cantidad_a_ingresar,  # ✅ Positivo (ingreso)
    concepto='TRASPASO_ENTRADA',
    tipo_movimiento='INGRESO',
    estado='COMPLETADO',
    ...
))

# 2. Actualiza STOCK LEGACY con F() expression (thread-safe)
for sku, cantidad in tallas_a_actualizar.items():
    talla = tallas_destino_existentes[sku]
    Producto_Talla.objects.filter(id=talla.id).update(
        stock=F('stock') + cantidad  # ✅ ACTUALIZA STOCK LEGACY
    )
```

**Resultado:**
- ✅ Crea movimiento de INGRESO en sucursal destino
- ✅ **SÍ actualiza** `talla.stock` legacy usando `F()` (thread-safe)
- ✅ Ambos sistemas quedan sincronizados

---

### 4. **Ajustes Manuales de Stock** (`views_edicion_productos.py`)

**Entrada de Stock:**
```python
# Crear movimiento
Movimientos_Producto.objects.create(
    ProductoTalla=variacion,
    cantidad=cantidad,  # ✅ Positivo
    concepto='AJUSTE_POSITIVO',
    estado='COMPLETADO',
    ...
)

# Actualizar stock legacy
variacion.stock += cantidad  # ✅ ACTUALIZA STOCK LEGACY
variacion.save()
```

**Salida de Stock:**
```python
# Consumir stock FIFO
consumir_stock_fifo(
    producto_talla=variacion,
    cantidad_requerida=cantidad,
    ...
)

# Actualizar stock legacy
variacion.stock -= cantidad  # ✅ ACTUALIZA STOCK LEGACY
variacion.save()
```

**Resultado:**
- ✅ Crea movimiento (INGRESO o EGRESO)
- ✅ Actualiza `talla.stock` legacy
- ✅ Ambos sistemas sincronizados

---

## 🔍 DIAGNÓSTICO: ¿Por qué NO se ve la rebaja de stock?

### Escenario 1: Emisión de DTE Interno (Traspaso)

**Flujo correcto:**

1. **Origen emite DTE** → Crea movimiento EGRESO en origen
   - ✅ Movimiento creado: `cantidad = -X`
   - ❌ **NO actualiza** `talla.stock` legacy
   - ✅ Stock por movimientos: `stock_sucursal(origen)` **SÍ disminuye**
   - ❌ Stock legacy: `talla.stock` **NO cambia**

2. **Destino recepciona** → Crea movimiento INGRESO en destino
   - ✅ Movimiento creado: `cantidad = +X`
   - ✅ **SÍ actualiza** `talla.stock` legacy en destino
   - ✅ Stock por movimientos: `stock_sucursal(destino)` aumenta
   - ✅ Stock legacy destino: se actualiza

### 🐛 PROBLEMA IDENTIFICADO

**En emisión de DTE (origen):**
- ✅ Crea movimiento de EGRESO (correcto)
- ❌ NO actualiza `talla.stock` legacy
- ✅ `stock_sucursal(origen)` SÍ disminuye
- ❌ Si la interfaz muestra `talla.stock` legacy, **NO se verá el cambio**

**En recepción (destino):**
- ✅ Crea movimiento de INGRESO (correcto)
- ✅ SÍ actualiza `talla.stock` legacy
- ✅ Todo correcto en destino

---

## 🔧 SOLUCIÓN

### Opción 1: Actualizar Stock Legacy en Emisión de DTE (Recomendada)

**Modificar `views.py` línea ~14714:**

```python
else:
    # DESPACHO INTERNO: Crear movimiento de traspaso (salida en origen)
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=talla,
        sucursal_origen=sucursal,
        sucursal_destino=sucursal_destino,
        cantidad=-cantidad,
        concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO',
        estado='COMPLETADO',
        responsable=request.user.username,
        observaciones=f"Traspaso DTE #{numero_documento} - Origen: {sucursal.alias} → Destino: {sucursal_destino.alias}"
    )
    
    # ✅ AGREGAR: Actualizar stock legacy en origen
    Producto_Talla.objects.filter(id=talla.id).update(
        stock=F('stock') - cantidad
    )
    
    print(f"✓ Movimiento de EGRESO creado: {talla.sku} -{cantidad} desde {sucursal.alias}")
    print(f"✓ Stock legacy actualizado en {sucursal.alias}")
```

**Ventajas:**
- ✅ Mantiene sincronizados ambos sistemas
- ✅ La interfaz muestra el cambio inmediatamente
- ✅ Usa `F()` para evitar race conditions
- ✅ Consistente con el resto del sistema

**Desventajas:**
- ⚠️ Mantener dos sistemas de stock (pero ya se hace así en otros módulos)

---

### Opción 2: Usar SOLO Stock por Movimientos en Interfaz

**Modificar las vistas que consultan productos** para usar `stock_sucursal()`:

**Antes:**
```python
'stock': talla.stock  # Stock legacy (global)
```

**Después:**
```python
'stock': talla.stock_sucursal(sucursal_id)  # Stock por movimientos
```

**Ventajas:**
- ✅ Sistema más moderno y preciso
- ✅ Stock diferenciado por sucursal
- ✅ No necesita sincronización

**Desventajas:**
- ⚠️ Requiere modificar TODAS las vistas que muestren stock
- ⚠️ Puede ser más lento (calcula en cada consulta)
- ⚠️ Productos legacy sin movimientos mostrarían stock 0

---

## 📊 COMPARACIÓN DE SISTEMAS

| Aspecto | Stock Legacy (`talla.stock`) | Stock por Movimientos (`stock_sucursal()`) |
|---------|------------------------------|-------------------------------------------|
| **Ubicación** | Campo directo en `Producto_Talla` | Calculado desde `Movimientos_Producto` |
| **Por sucursal** | ❌ No (es global) | ✅ Sí |
| **Rendimiento** | ✅ Muy rápido (campo directo) | ⚠️ Más lento (suma agregada) |
| **Historial** | ❌ No guarda historial | ✅ Sí (todos los movimientos) |
| **Trazabilidad** | ❌ No | ✅ Completa |
| **Actualización** | Manual (`stock +=/-`) | Automática (suma movimientos) |
| **Usado en creación** | ✅ Sí | ✅ Sí |
| **Usado en emisión DTE** | ❌ No | ✅ Sí |
| **Usado en recepción** | ✅ Sí | ✅ Sí |
| **Usado en ajustes** | ✅ Sí | ✅ Sí |

---

## 🎯 RECOMENDACIÓN FINAL

### ✅ SOLUCIÓN HÍBRIDA (Mantener ambos sistemas sincronizados)

**Justificación:**
- El sistema **YA usa ambos** en la mayoría de operaciones
- Solo falta sincronizar en **emisión de DTE**
- Es la solución más simple y consistente
- No requiere refactorizar toda la aplicación

### 📝 Cambios Necesarios:

**1. Modificar `views.py` línea ~14714 (emisión DTE interno):**
```python
# Agregar después de crear el movimiento:
Producto_Talla.objects.filter(id=talla.id).update(
    stock=F('stock') - cantidad
)
```

**2. Modificar `views.py` línea ~14692 (emisión DTE externo):**
```python
# Agregar después de crear el movimiento:
Producto_Talla.objects.filter(id=talla.id).update(
    stock=F('stock') - cantidad
)
```

**Resultado:** Ambos sistemas quedarán **100% sincronizados** en TODAS las operaciones.

---

## 🧪 PRUEBA DE VERIFICACIÓN

### Antes del cambio:
1. Producto en sucursal origen: `talla.stock = 100`
2. Emitir DTE de 10 unidades
3. Verificar: `talla.stock` sigue siendo `100` ❌
4. Verificar: `talla.stock_sucursal(origen)` es `90` ✅

### Después del cambio:
1. Producto en sucursal origen: `talla.stock = 100`
2. Emitir DTE de 10 unidades
3. Verificar: `talla.stock` es `90` ✅
4. Verificar: `talla.stock_sucursal(origen)` es `90` ✅

---

## 📋 RESUMEN DE HALLAZGOS

✅ **Sistema de creación:** Usa ambos sistemas ✅
✅ **Sistema de recepción:** Usa ambos sistemas ✅
✅ **Sistema de ajustes:** Usa ambos sistemas ✅
❌ **Sistema de emisión DTE:** Solo usa movimientos (falta actualizar legacy)

**Conclusión:** Solo falta sincronizar el stock legacy en la emisión de DTEs.
