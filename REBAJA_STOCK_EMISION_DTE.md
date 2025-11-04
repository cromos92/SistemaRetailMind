# 📦 Sistema de Rebaja de Stock en Emisión de DTE

## 📋 Descripción General

El sistema de emisión de DTE maneja la rebaja de stock de manera diferente según el **Método de Despacho** seleccionado:

---

## 🚚 Despacho Externo (Venta a Cliente)

### Comportamiento
- ✅ **Stock se reduce INMEDIATAMENTE**
- ✅ Utiliza método **FIFO (First In, First Out)**
- ✅ Movimiento registrado como **VENTA COMPLETADA**

### Proceso Detallado

1. **Validación de Stock**
   - Se verifica que haya stock suficiente antes de procesar
   - Si no hay stock, se rechaza la operación

2. **Consumo FIFO**
   ```python
   consumir_stock_fifo(
       producto_talla=talla,
       cantidad_requerida=cantidad,
       responsable=request.user.username,
       ticket=None,
       observaciones=f"Venta DTE #{numero_documento} - Cliente: {receptor.nombre}",
       referencia_externa=f"DTE_{numero_documento}"
   )
   ```

3. **¿Qué hace el FIFO?**
   - Busca los lotes más antiguos (por `fecha_ingreso`)
   - Consume del lote más antiguo primero
   - Si un lote no tiene suficiente cantidad, consume de varios lotes
   - Calcula el costo promedio ponderado basado en los lotes consumidos
   - **Reduce automáticamente el stock en `Producto_Talla.stock`**

4. **Movimiento Creado**
   - **Tipo:** `EGRESO`
   - **Concepto:** `VENTA_MAYORISTA`
   - **Cantidad:** Negativa (ej: -50)
   - **Estado:** `COMPLETADO`
   - **Observaciones:** "Venta DTE #XXXX - Cliente: Nombre Cliente"

5. **Trazabilidad**
   - Se registra qué lotes se consumieron
   - Se guarda el costo FIFO de cada producto
   - Se crea referencia al DTE emitido

### Ejemplo Práctico

```
📦 Producto: Zapatilla Nike Air - Talla 42
Stock disponible: 100 unidades

Emisión DTE Externo: 30 unidades

Lotes disponibles (FIFO):
┌─────────────────────────────────────────────┐
│ Lote 1: 20 unidades | Costo: $10.000      │ ← Se consume primero
│ Lote 2: 50 unidades | Costo: $12.000      │ ← Se consume lo restante
│ Lote 3: 30 unidades | Costo: $11.000      │
└─────────────────────────────────────────────┘

Resultado:
- ✅ Lote 1: 20 unidades consumidas (agotado)
- ✅ Lote 2: 10 unidades consumidas (quedan 40)
- ❌ Stock final: 70 unidades
- 💰 Costo total: (20 × $10.000) + (10 × $12.000) = $320.000
```

---

## 🏢 Despacho Interno (Traspaso entre Sucursales)

### Comportamiento
- ✅ **Stock se reduce INMEDIATAMENTE en origen**
- ✅ Se crea movimiento **EGRESO** con estado `PENDIENTE_RECEPCION`
- ⏳ Stock **NO aumenta en destino** hasta que se confirme la recepción
- ✅ El stock en destino aumenta cuando la sucursal destino **confirma la recepción**

### Proceso Detallado

1. **Validación y Reducción de Stock en Origen**
   - Se verifica que haya stock suficiente
   - ✅ **El stock se REDUCE INMEDIATAMENTE** en la sucursal origen
   - La mercadería ya salió físicamente del inventario origen

2. **Creación de Movimiento de Salida**
   ```python
   # ✅ Reducir stock inmediatamente
   talla.stock -= cantidad
   talla.save()
   
   # Crear movimiento de egreso
   Movimientos_Producto.objects.create(
       dte=dte,
       ProductoTalla=talla,
       sucursal_origen=sucursal,
       sucursal_destino=sucursal_destino,
       cantidad=-cantidad,  # Negativo porque es egreso
       concepto='TRASPASO_SALIDA',
       tipo_movimiento='EGRESO',  # EGRESO porque ya salió
       estado='PENDIENTE_RECEPCION',  # Pendiente de que destino lo reciba
       responsable=request.user.username,
       observaciones=f"Traspaso DTE #{numero_documento} - Stock reducido al emitir"
   )
   ```

3. **¿Por qué se reduce inmediatamente?**
   - ✅ La mercadería YA salió físicamente de la sucursal origen
   - ✅ El stock en sistema debe reflejar el stock físico real
   - ✅ Evita que se venda mercadería que ya está en tránsito
   - ✅ Mejor control de inventario y trazabilidad

4. **¿Cuándo aumenta el stock en destino?**
   - Cuando la sucursal destino accede a `/app/recepcion-dte/`
   - Revisa los documentos pendientes de recepción
   - Confirma la recepción del DTE
   - **Entonces se ejecuta:**
     - ✅ INGRESO en sucursal destino (aumenta stock)
     - ✅ Se crea movimiento de `TRASPASO_ENTRADA` en destino
     - ✅ Movimiento de salida cambia a `COMPLETADO`

### Ejemplo Práctico

```
📦 Producto: Polera Adidas - Talla M
Stock Sucursal Casa Matriz: 200 unidades

Emisión DTE Interno (Casa Matriz → Sucursal Mall): 50 unidades

Estado INMEDIATAMENTE después de emitir DTE:
┌─────────────────────────────────────────────┐
│ Casa Matriz (Origen):                       │
│   Stock: 150 (-50) ✅ REDUCIDO AL EMITIR    │
│   Movimiento: TRASPASO_SALIDA               │
│             Tipo: EGRESO                    │
│             Estado: PENDIENTE_RECEPCION     │
│                                             │
│ Sucursal Mall (Destino):                    │
│   Stock: 100 (sin cambios) ⏳               │
│   Movimiento: Ninguno aún                   │
│   📦 Mercadería en tránsito: 50 unidades    │
└─────────────────────────────────────────────┘

Estado DESPUÉS de confirmar recepción:
┌─────────────────────────────────────────────┐
│ Casa Matriz (Origen):                       │
│   Stock: 150 (sin cambios) ✅               │
│   Movimiento: TRASPASO_SALIDA (COMPLETADO)  │
│                                             │
│ Sucursal Mall (Destino):                    │
│   Stock: 150 (+50) ✅ AUMENTADO AL RECEPCIONAR │
│   Movimiento: TRASPASO_ENTRADA (INGRESO)    │
│             Estado: COMPLETADO              │
└─────────────────────────────────────────────┘
```

---

## 📊 Comparación Rápida

| Característica | Despacho Externo | Despacho Interno |
|---------------|-----------------|-----------------|
| **Stock se reduce en origen** | ✅ Inmediatamente | ✅ Inmediatamente |
| **Stock aumenta en destino** | N/A (no hay destino) | ⏳ Al confirmar recepción |
| **Usa FIFO** | ✅ Sí | ❌ No |
| **Estado inicial** | COMPLETADO | PENDIENTE_RECEPCION |
| **Tipo movimiento origen** | EGRESO | EGRESO |
| **Tipo movimiento destino** | N/A | INGRESO (al confirmar) |
| **Concepto** | VENTA_MAYORISTA | TRASPASO_SALIDA |
| **Requiere confirmación** | ❌ No | ✅ Sí (para ingresar en destino) |
| **Riesgo sobreventa** | ❌ No | ❌ No (stock ya reducido) |

---

## 🔍 Verificación en el Código

### Archivo: `retailmind/app/views.py`
### Función: `emitir_dte()` (líneas 6515-6815)

```python
# Línea 6748-6784: DESPACHO EXTERNO
if metodo_despacho == 'externo':
    # DESPACHO EXTERNO: Usar FIFO para consumir stock (venta real)
    if talla.stock < cantidad:
        raise ValueError(f"Stock insuficiente...")
    
    # Consumir stock usando FIFO (esto crea automáticamente el movimiento)
    try:
        costo_consumido, lotes_usados = consumir_stock_fifo(
            producto_talla=talla,
            cantidad_requerida=cantidad,
            responsable=request.user.username,
            ...
        )

# Línea 6786-6812: DESPACHO INTERNO
else:
    # DESPACHO INTERNO: Reducir stock INMEDIATAMENTE y crear movimiento
    
    # Validar que hay stock suficiente
    if talla.stock < cantidad:
        raise ValueError(f"Stock insuficiente...")
    
    # ✅ REDUCIR STOCK INMEDIATAMENTE en sucursal origen
    talla.stock -= cantidad
    talla.save()
    print(f"✓ Stock reducido en origen INMEDIATAMENTE: {talla.sku} -{cantidad}")
    
    # Crear movimiento de egreso en sucursal origen
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=talla,
        sucursal_origen=sucursal,
        sucursal_destino=sucursal_destino,
        cantidad=-cantidad,  # Negativo porque es egreso
        concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO',  # ← EGRESO porque ya salió
        estado='PENDIENTE_RECEPCION',  # ← Pendiente de recepción en destino
        ...
    )
```

---

## 🎯 Conclusión

El sistema maneja inteligentemente la rebaja de stock según el contexto:

- **Ventas externas:** Stock se reduce inmediatamente con FIFO ✅
- **Traspasos internos:** Stock se reserva y reduce solo al confirmar recepción ⏳

Esto garantiza:
- ✅ Trazabilidad completa
- ✅ Control de inventario preciso
- ✅ Prevención de pérdidas
- ✅ Auditoría de movimientos

---

## 💡 Recomendaciones para Despacho Interno

Para un correcto manejo de traspasos internos:

1. **Confirmar recepciones rápidamente**
   - La sucursal destino debe acceder a `/app/recepcion-dte/` y confirmar cuanto antes
   - Esto ingresa el stock en destino y completa el traspaso

2. **Consultar mercadería en tránsito**
   - Verificar movimientos con estado `PENDIENTE_RECEPCION`
   - Estos representan mercadería que ya salió de origen pero no ha llegado a destino

3. **Comunicación entre sucursales**
   - Informar a las sucursales cuando se emita un DTE interno
   - Confirmar que la mercadería llegó físicamente antes de confirmar en el sistema

4. **Reporte de mercadería en tránsito**
   - Crear consulta SQL que muestre:
     - Stock actual en sucursal
     - Mercadería en tránsito saliente
     - Mercadería en tránsito entrante

### Consulta SQL sugerida:
```sql
SELECT 
    pt.sku,
    pt.stock AS stock_actual,
    COALESCE(SUM(CASE WHEN mp.estado = 'PENDIENTE_RECEPCION' 
                      AND mp.tipo_movimiento = 'EGRESO' 
                      AND mp.concepto = 'TRASPASO_SALIDA'
                 THEN ABS(mp.cantidad) ELSE 0 END), 0) AS en_transito_saliendo,
    COALESCE(SUM(CASE WHEN mp.estado = 'PENDIENTE_RECEPCION' 
                      AND mp.sucursal_destino_id = [ID_SUCURSAL]
                 THEN ABS(mp.cantidad) ELSE 0 END), 0) AS en_transito_llegando
FROM app_producto_talla pt
LEFT JOIN app_movimientos_producto mp ON pt.id = mp.ProductoTalla_id
GROUP BY pt.id, pt.sku, pt.stock;
```

---

## 📞 Soporte

Para más información sobre el sistema de emisión de DTE, consulta:
- `SISTEMA_EMISION_DTE.md`
- `retailmind/app/views.py` - Función `emitir_dte()` (líneas 6515-6815)
- `retailmind/app/views.py` - Función `consumir_stock_fifo()` (líneas 3715-3793)
- `/app/recepcion-dte/` - Vista para confirmar recepciones de DTE internos

