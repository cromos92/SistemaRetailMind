# 📋 ANÁLISIS: Regularización de Recepciones y Manejo de Stock

## 🎯 URL Analizada
`http://localhost:8000/app/regularizar-recepciones/`

---

## 📊 FUNCIONES DE REGULARIZACIÓN

### 1. **Emitir Nota de Crédito (NC) por Productos con Problemas**

**Archivo:** `views.py` - Líneas 1687-1890

**¿Qué hace?**

Cuando se emite una NC por productos dañados/faltantes:

```python
# 1. Devuelve stock a bodega ORIGEN (emisor)
producto_origen = recepcion.producto_talla
if producto_origen:
    stock_antes = producto_origen.stock
    producto_origen.stock += cantidad_nc  # ✅ Devolver unidades
    producto_origen.save()
    
    # 2. Crea movimiento de INGRESO en bodega origen
    Movimientos_Producto.objects.create(
        dte=nota_credito,
        ProductoTalla=producto_origen,
        sucursal_origen=None,  # No hay origen (es devolución)
        sucursal_destino=dte_original.sucursal,  # Vuelve a bodega emisora
        cantidad=cantidad_nc,  # ✅ Positivo (ingreso)
        concepto='DEVOLUCION_NC',
        tipo_movimiento='INGRESO',
        estado='COMPLETADO',
        ...
    )
```

**✅ Sistema usado:**
- **SÍ actualiza** `producto_origen.stock` legacy
- **SÍ crea** movimiento de INGRESO
- **Ambos sistemas sincronizados**

---

### 2. **Cambio por Producto Nuevo**

**Archivo:** `views.py` - Líneas 1924-2157

**¿Qué hace?**

Cuando se cambia un producto dañado por uno nuevo:

```python
# OPCIÓN A: Producto de cambio es DIFERENTE al problemático
if producto_envio_id != recepcion.producto_talla_id:
    # 1. Devuelve el problema (NC automática)
    producto_problema = recepcion.producto_talla
    stock_antes = producto_problema.stock
    producto_problema.stock += cantidad_problema  # ✅ Devolver
    producto_problema.save()
    
    # Crea movimiento INGRESO (devolución)
    Movimientos_Producto.objects.create(
        ProductoTalla=producto_problema,
        cantidad=cantidad_problema,  # ✅ Positivo
        concepto='DEVOLUCION_CAMBIO',
        tipo_movimiento='INGRESO',
        ...
    )
    
    # 2. Despacha el nuevo (Guía nueva)
    producto_nuevo = Producto_Talla.objects.get(id=producto_envio_id)
    stock_antes_nuevo = producto_nuevo.stock
    producto_nuevo.stock -= cantidad_envio  # ✅ Rebaja stock
    producto_nuevo.save()
    
    # Crea movimiento EGRESO (envío)
    Movimientos_Producto.objects.create(
        ProductoTalla=producto_nuevo,
        cantidad=-cantidad_envio,  # ✅ Negativo
        concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO',
        ...
    )
```

**OPCIÓN B: Producto de cambio es el MISMO (solo completa faltante)**
```python
else:
    # Solo envía más del mismo producto
    producto_mismo = recepcion.producto_talla
    stock_antes = producto_mismo.stock
    producto_mismo.stock -= cantidad_envio  # ✅ Rebaja stock
    producto_mismo.save()
    
    # Crea movimiento EGRESO
    Movimientos_Producto.objects.create(
        ProductoTalla=producto_mismo,
        cantidad=-cantidad_envio,  # ✅ Negativo
        concepto='TRASPASO_COMPLEMENTO',
        tipo_movimiento='EGRESO',
        ...
    )
```

**✅ Sistema usado:**
- **SÍ actualiza** `stock` legacy (tanto ingreso como egreso)
- **SÍ crea** movimientos correspondientes
- **Ambos sistemas sincronizados**

---

### 3. **Solicitud de NC Masiva (múltiples productos)**

**Archivo:** `views.py` - Líneas 2300-2615

**¿Qué hace?**

Cuando se rechaza múltiples productos y se solicita NC:

```python
# Para CADA producto rechazado:
for prod_nc in productos_nc:
    recepcion = prod_nc['recepcion']
    cantidad = prod_nc['cantidad']
    precio_unitario = int(prod_nc['precio_unitario'])
    
    # 1. Devuelve stock a bodega origen
    producto_origen = recepcion.producto_talla
    if producto_origen:
        stock_antes = producto_origen.stock
        producto_origen.stock += cantidad  # ✅ Devolver unidades
        producto_origen.save()
        
        # 2. Crea movimiento INGRESO
        Movimientos_Producto.objects.create(
            dte=nota_credito,
            ProductoTalla=producto_origen,
            sucursal_origen=None,
            sucursal_destino=dte_original.sucursal,
            cantidad=cantidad,  # ✅ Positivo (ingreso)
            concepto='DEVOLUCION_NC_MASIVA',
            tipo_movimiento='INGRESO',
            estado='COMPLETADO',
            ...
        )
```

**✅ Sistema usado:**
- **SÍ actualiza** `stock` legacy para cada producto
- **SÍ crea** movimiento de INGRESO para cada producto
- **Ambos sistemas sincronizados**

---

### 4. **Descartar Producto sin NC (simplemente no lo reciben)**

**Archivo:** `views.py` - Función `confirmar_recepcion_api()` línea 322

**¿Qué hace?**

Cuando se marca un producto como "dañado" con `cantidad_danada > 0`:

```python
# En la recepción, calcula cantidad a ingresar:
cantidad_a_ingresar = cantidad_recepcionada - cantidad_danada

if cantidad_a_ingresar <= 0:
    continue  # ❌ NO ingresa stock, NO crea movimiento
```

**⚠️ IMPORTANTE:**
- Si `cantidad_danada = cantidad_esperada`, **NO se ingresa nada**
- **NO crea movimiento** en destino
- El movimiento de EGRESO en origen **queda pendiente** hasta que se regularice
- **Stock en origen YA salió** (se rebajó al emitir)
- **Stock en destino NO entra**

**Para regularizar después:**
- Emisor debe generar NC manualmente (esto devuelve el stock al origen)
- O enviar producto de reemplazo (rebaja nuevamente)

---

## 🔧 ANÁLISIS: ¿QUÉ STOCK USA REGULARIZACIÓN?

### ✅ CONFIRMACIÓN: Usa AMBOS sistemas sincronizados

| Operación | Stock Legacy | Movimientos | ¿Sincronizado? |
|-----------|--------------|-------------|----------------|
| **Emitir NC** | ✅ Actualiza `+cantidad` | ✅ Crea INGRESO | ✅ SÍ |
| **Cambio por nuevo (diferente)** | ✅ Actualiza ambos (`+problema`, `-nuevo`) | ✅ Crea ambos (INGRESO + EGRESO) | ✅ SÍ |
| **Cambio por mismo** | ✅ Actualiza `-cantidad` | ✅ Crea EGRESO | ✅ SÍ |
| **NC Masiva** | ✅ Actualiza `+cantidad` cada uno | ✅ Crea INGRESO cada uno | ✅ SÍ |
| **Descartar sin NC** | ❌ No modifica | ❌ No crea movimiento | ⚠️ Queda descuadrado hasta regularizar |

---

## 📊 FLUJO COMPLETO: Producto con Problema

### Escenario: Producto llega dañado

**1. Emisor envía:** 10 unidades
```python
# Al emitir DTE:
- Stock origen ANTES del fix: NO se actualizaba ❌
- Stock origen DESPUÉS del fix: SÍ se actualiza con F() ✅
- Movimiento: EGRESO -10 (origen)
```

**2. Receptor recepciona:** 8 OK, 2 dañadas
```python
# Al confirmar recepción:
- Ingresa solo 8 unidades
- Movimiento: INGRESO +8 (destino)
- Stock destino: +8
- Las 2 dañadas NO ingresan
```

**3. Receptor solicita NC por 2 dañadas**
```python
# Al emitir NC:
- Stock origen: +2 (devuelve las dañadas)
- Movimiento: INGRESO +2 (origen)
- Crea NC por 2 unidades
```

**RESULTADO FINAL:**
- Origen: -10 + 2 = **-8** (correcto, envió 10, le devolvieron 2)
- Destino: +8 (correcto, recibió solo las buenas)
- Total: 0 (balance correcto)

---

## 🎯 CONCLUSIÓN

### ✅ EL SISTEMA DE REGULARIZACIÓN ESTÁ CORRECTO

**Maneja correctamente el stock en:**
1. ✅ Nota de Crédito (devuelve stock a origen)
2. ✅ Cambio por producto nuevo (rebaja nuevo, devuelve problema)
3. ✅ Cambio por mismo producto (rebaja para enviar más)
4. ✅ NC Masiva (procesa múltiples devoluciones)

**Usa ambos sistemas sincronizados:**
- ✅ Actualiza `Producto_Talla.stock` legacy
- ✅ Crea movimientos en `Movimientos_Producto`
- ✅ Usa `F()` para operaciones thread-safe (donde corresponde)

---

## ⚠️ ÚNICA MEJORA NECESARIA

**Ya aplicada en la corrección anterior:**

El problema estaba en la **emisión de DTE** (cuando se envía), NO en la regularización.

**ANTES (problema):**
- Emisión: Solo creaba movimiento, NO actualizaba stock legacy ❌
- Regularización: SÍ actualizaba ambos ✅

**DESPUÉS (corregido):**
- Emisión: Crea movimiento Y actualiza stock legacy ✅
- Regularización: SÍ actualiza ambos ✅

**Resultado:** TODO el sistema ahora usa ambos sistemas de stock de forma sincronizada.

---

## 📝 NO SE REQUIEREN CAMBIOS EN REGULARIZACIÓN

El módulo de regularización **YA está bien implementado** y **NO necesita modificaciones**.

La única corrección necesaria era en la emisión de DTEs, que ya fue aplicada.
