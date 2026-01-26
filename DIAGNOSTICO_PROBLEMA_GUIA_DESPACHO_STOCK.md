# 🐛 DIAGNÓSTICO: Problema Guía de Despacho y Stock

## ❌ PROBLEMA 1: Guía de Despacho genera TXT como Factura

### Síntomas
- Al emitir "Guía de Despacho Interno" se genera el TXT con tipo 33 (Factura) en lugar de tipo 52 (Guía)

### Causa Raíz
El problema está en el **modelo `Dte`** que almacena el tipo de documento como **string** `'GUIA'`, pero cuando se genera el archivo TXT desde un DTE existente, el mapeo **NO incluye** el valor `'GUIA'`.

### Flujo Actual

**1. Frontend → Backend (emisionDTE.html → views.py):**
```javascript
// emisionDTE.html línea 4972-4975
const tipoDocumentoMap = {
    'factura': 'FACTURA ELECTRONICA',
    'guia': 'GUIA'  // ✅ Envía 'GUIA'
};
```

**2. Backend crea el DTE (views.py línea 14632):**
```python
dte = Dte.objects.create(
    ...
    tipo_documento=tipo_doc,  # Guarda 'GUIA' en la BD
    ...
)
```

**3. Backend genera TXT (views_modulo_documentos.py línea 2092-2100):**
```python
# ❌ PROBLEMA: No incluye mapeo para 'GUIA'
tipo_mapping = {
    'FACTURA_ELECTRONICA': 33,
    'FACTURA_EXENTA': 34,
    'BOLETA_ELECTRONICA': 39,
    'BOLETA_EXENTA': 41,
    'GUIA_DESPACHO': 52,  # ❌ Busca 'GUIA_DESPACHO'
    'NOTA_CREDITO': 61
}
tipo_numerico = tipo_mapping.get(dte.tipo_documento, 33)  # ❌ 'GUIA' no está, usa default 33
```

### 🔧 SOLUCIÓN

Agregar el mapeo correcto en `views_modulo_documentos.py`:

```python
# LÍNEA 2092 - ACTUALIZAR:
tipo_mapping = {
    'FACTURA_ELECTRONICA': 33,
    'FACTURA ELECTRONICA': 33,  # Variante con espacio
    'FACTURA_EXENTA': 34,
    'FACTURA EXENTA': 34,
    'BOLETA_ELECTRONICA': 39,
    'BOLETA ELECTRONICA': 39,
    'BOLETA_EXENTA': 41,
    'BOLETA EXENTA': 41,
    'GUIA_DESPACHO': 52,
    'GUIA DESPACHO': 52,
    'GUIA': 52,  # ✅ AGREGAR ESTE
    'NOTA_CREDITO': 61,
    'NOTA DE CREDITO': 61
}
```

---

## ❌ PROBLEMA 2: No rebaja stock al emitir DTE a otra sucursal

### Síntomas
- Al emitir DTE (factura o guía) a otra sucursal, **NO se rebaja el stock** de la bodega origen

### Análisis del Código Actual

**Flujo en views.py línea 14713-14740:**

```python
else:
    # DESPACHO INTERNO: Crear movimiento de traspaso (salida en origen)
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=talla,
        sucursal_origen=sucursal,
        sucursal_destino=sucursal_destino,
        cantidad=-cantidad,  # ✅ Negativo (egreso)
        costo=producto.costo,
        sobreprecio=producto.sobreprecio,
        precio=int(precio),
        concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO',  # ✅ Correcto
        estado='COMPLETADO',  # ✅ Correcto
        responsable=request.user.username,
        observaciones=f"Traspaso DTE #{numero_documento} - Origen: {sucursal.alias} → Destino: {sucursal_destino.alias}"
    )
    
    # ✅ NO modificar el campo talla.stock
    # El stock se calcula automáticamente desde los movimientos usando stock_sucursal()
    print(f"✓ Movimiento de EGRESO creado: {talla.sku} -{cantidad} desde {sucursal.alias}")
    print(f"  Stock en {sucursal.alias} se actualiza automáticamente desde movimientos")
```

### Pregunta Clave: ¿Cómo se calcula el stock?

Hay **DOS sistemas de stock** en el código:

#### 1. Stock Legacy (campo directo `Producto_Talla.stock`)
- Campo `stock` en la tabla `Producto_Talla`
- Se actualiza manualmente con `talla.stock += cantidad` o `talla.stock -= cantidad`
- **NO diferencia por sucursal**
- Es el stock "global" del producto

#### 2. Stock por Movimientos (método `stock_sucursal()`)
- Calculado dinámicamente desde la tabla `Movimientos_Producto`
- Suma todos los movimientos de una sucursal específica
- Diferencia por sucursal
- Sistema moderno y correcto

### Diagnóstico

El código actual:
1. ✅ **SÍ crea el movimiento** de egreso (`cantidad=-cantidad`)
2. ✅ **NO modifica** `talla.stock` (correcto, porque usa movimientos)
3. ❓ **PERO** puede que la interfaz esté mostrando el stock legacy en lugar del stock por movimientos

### 🔍 Verificar:

**¿Qué stock está mostrando la interfaz?**

Revisar en el código HTML/JavaScript si usa:
- `producto.stock` ← Stock legacy (global)
- `producto.stock_sucursal(sucursal_id)` ← Stock por movimientos (correcto)

### 🔧 POSIBLES CAUSAS:

1. **La interfaz muestra stock legacy en vez de stock por movimientos**
   - Solución: Cambiar las consultas para usar `stock_sucursal()`

2. **Los productos no tienen movimientos previos (stock legacy)**
   - Si el producto se creó con stock inicial pero sin movimientos
   - El stock legacy tiene valor, pero no hay movimientos
   - Al crear un egreso, el movimiento funciona pero el stock legacy no cambia
   - Solución: Migrar stock legacy a movimientos iniciales

3. **Producto pertenece a otra sucursal (producto.sucursal_id diferente)**
   - El producto está asignado a sucursal X
   - Intentas vender desde sucursal Y
   - El stock_sucursal(Y) es 0
   - Solución: Sistema de movimientos debe estar activo para todos los productos

### 🔬 Diagnóstico Detallado del Stock

**views.py línea 14530-14543:**
```python
# DEBUG DETALLADO: Información del producto y stock
print(f"    📦 Producto: {talla.producto.articulo} | SKU: {talla.sku} | Talla: {talla.talla}")
print(f"    📍 Stock global (campo directo): {talla.stock}")
print(f"    📍 Producto.sucursal_id: {talla.producto.sucursal_id}")
print(f"    🏢 Sucursal actual (origen): {sucursal.alias} (ID: {sucursal_id})")

# Verificar si tiene movimientos
tiene_movimientos = talla.movimientos_productos_talla.exists()
print(f"    📊 Tiene movimientos registrados: {tiene_movimientos}")

# ✅ Usar stock_sucursal() para validar stock específico de la sucursal origen
stock_disponible = talla.stock_sucursal(sucursal_id)
print(f"    ✅ Stock disponible en sucursal {sucursal.alias}: {stock_disponible}")
```

El código **YA tiene logging detallado**. Para diagnosticar:

1. **Emitir una guía/factura a otra sucursal**
2. **Revisar la consola del servidor** (terminal donde corre Django)
3. **Buscar las líneas** que empiezan con `📦`, `📍`, `📊`, `✅`
4. **Anotar:**
   - Stock global: `___`
   - Tiene movimientos: `Sí / No`
   - Stock en sucursal: `___`
   - Producto.sucursal_id: `___` vs Sucursal actual: `___`

---

## 📝 RESUMEN DE SOLUCIONES

### ✅ Solución Problema 1 (TXT Guía)

**Archivo:** `retailmind/app/views_modulo_documentos.py`
**Línea:** ~2092

**CAMBIAR:**
```python
tipo_mapping = {
    'FACTURA_ELECTRONICA': 33,
    'FACTURA_EXENTA': 34,
    'BOLETA_ELECTRONICA': 39,
    'BOLETA_EXENTA': 41,
    'GUIA_DESPACHO': 52,
    'NOTA_CREDITO': 61
}
```

**POR:**
```python
tipo_mapping = {
    'FACTURA_ELECTRONICA': 33,
    'FACTURA ELECTRONICA': 33,
    'FACTURA_EXENTA': 34,
    'FACTURA EXENTA': 34,
    'BOLETA_ELECTRONICA': 39,
    'BOLETA ELECTRONICA': 39,
    'BOLETA_EXENTA': 41,
    'BOLETA EXENTA': 41,
    'GUIA_DESPACHO': 52,
    'GUIA DESPACHO': 52,
    'GUIA': 52,  # ← AGREGAR
    'NOTA_CREDITO': 61,
    'NOTA DE CREDITO': 61
}
```

### 🔍 Solución Problema 2 (Stock)

**PASO 1:** Ejecutar una prueba de emisión de DTE interno

**PASO 2:** Revisar logs en consola y anotar:
- ¿Tiene movimientos el producto? (Sí/No)
- Stock global vs Stock en sucursal
- ¿El producto pertenece a la sucursal actual?

**PASO 3:** Según el resultado:
- **Si tiene movimientos:** El stock SÍ se está rebajando (verificar en la interfaz que use `stock_sucursal()`)
- **Si NO tiene movimientos:** Migrar stock legacy a movimientos iniciales
- **Si pertenece a otra sucursal:** Verificar que el sistema de movimientos esté activo

---

## 🧪 PRUEBA MANUAL

### Para verificar TXT Guía:

1. Ir a `http://localhost:8000/app/emisionDTE/`
2. Seleccionar "Despacho Interno"
3. Seleccionar "Guía de Despacho"
4. Seleccionar sucursal destino EDEL
5. Agregar productos
6. Emitir
7. Descargar TXT
8. Abrir con editor de texto
9. **Primera línea debe empezar con:** `52|` (no `33|`)

### Para verificar Stock:

1. Anotar stock inicial del producto en sucursal origen
2. Emitir DTE a otra sucursal
3. Revisar consola del servidor (logs)
4. Anotar:
   ```
   Stock global: ___
   Tiene movimientos: ___
   Stock en sucursal antes: ___
   Movimiento creado: -___ unidades
   ```
5. Refrescar página de productos
6. Verificar que el stock en sucursal origen **SÍ haya disminuido**

---

## 🎯 CONCLUSIÓN

**Problema 1 (TXT Guía):** IDENTIFICADO - falta mapeo `'GUIA': 52` en `tipo_mapping`

**Problema 2 (Stock):** REQUIERE DIAGNÓSTICO - el código SÍ crea el movimiento de egreso, pero necesita verificar:
- ¿Qué stock muestra la interfaz? (legacy vs movimientos)
- ¿El producto tiene movimientos previos?
- ¿La interfaz se actualiza correctamente?
