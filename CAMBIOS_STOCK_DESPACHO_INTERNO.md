# 🔧 Corrección: Stock se Reduce Inmediatamente en Despacho Interno

## 📋 Resumen del Cambio

**Antes:** El stock NO se reducía al emitir un DTE interno, solo se reducía cuando la sucursal destino confirmaba la recepción.

**Ahora:** El stock **SÍ se reduce INMEDIATAMENTE** al emitir cualquier tipo de documento, independiente de si es despacho interno o externo.

---

## ✅ Cambios Realizados

### 1. **Código Backend** (`retailmind/app/views.py`)

#### Líneas 6786-6812: Emisión DTE Interno
**ANTES:**
```python
# NO reducir stock aún - se hará cuando la sucursal destino confirme
# Crear movimiento de egreso pendiente
Movimientos_Producto.objects.create(
    ...
    estado='PENDIENTE',  # Stock NO se reduce
)
```

**AHORA:**
```python
# ✅ REDUCIR STOCK INMEDIATAMENTE en sucursal origen
talla.stock -= cantidad
talla.save()
print(f"✓ Stock reducido en origen INMEDIATAMENTE: {talla.sku} -{cantidad}")

# Crear movimiento de egreso (stock ya reducido)
Movimientos_Producto.objects.create(
    ...
    tipo_movimiento='EGRESO',  # EGRESO porque ya salió
    estado='PENDIENTE_RECEPCION',  # Pendiente de que destino reciba
)
```

#### Líneas 348-356: Recepción DTE
**ANTES:**
```python
# Reducir stock en sucursal origen (ahora que se confirmó)
for mov_salida in movimientos_salida:
    mov_salida.ProductoTalla.stock -= abs(mov_salida.cantidad)
    mov_salida.ProductoTalla.save()
```

**AHORA:**
```python
# Marcar movimientos como COMPLETADOS (stock ya se había reducido al emitir)
movimientos_salida = Movimientos_Producto.objects.filter(
    dte=dte,
    concepto='TRASPASO_SALIDA',
    estado='PENDIENTE_RECEPCION'  # ← Nuevo estado
)
movimientos_salida.update(estado='COMPLETADO')
# YA NO reduce stock aquí porque ya se redujo al emitir
```

#### Líneas 79-95: Query de DTEs Pendientes
**ANTES:**
```python
Dte.objects.filter(
    ...
    dte_movimientos__tipo_movimiento='TRASPASO',
    dte_movimientos__estado='PENDIENTE',
)
```

**AHORA:**
```python
Dte.objects.filter(
    ...
    dte_movimientos__tipo_movimiento='EGRESO',  # EGRESO
    dte_movimientos__estado='PENDIENTE_RECEPCION',  # Nuevo estado
)
```

### 2. **Modelo** (`retailmind/app/models.py`)

#### Líneas 390-397: Nuevo Estado
```python
ESTADO_MOVIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('PENDIENTE_RECEPCION', 'Pendiente de Recepción'),  # ← NUEVO
    ('APROBADO', 'Aprobado'),
    ('RECHAZADO', 'Rechazado'),
    ('ANULADO', 'Anulado'),
    ('COMPLETADO', 'Completado'),
]
```

### 3. **Interfaz** (`emisionDTE.html`)

#### Tarjeta de Despacho Interno
**ANTES:**
```html
<small class="text-warning">
    <i class="bi bi-clock-history me-1"></i>Stock queda pendiente
</small>
```

**AHORA:**
```html
<small class="text-danger">
    <i class="bi bi-arrow-down-circle me-1"></i>Stock se reduce
</small>
```

#### Resumen de Impacto en Stock
**ANTES:**
```javascript
El stock NO se reducirá inmediatamente.
⚠️ El stock seguirá mostrándose como disponible...
```

**AHORA:**
```javascript
El stock se reducirá INMEDIATAMENTE en la sucursal origen.
📤 Salida inmediata: El stock de la sucursal origen se reduce al emitir.
📥 Ingreso pendiente: La sucursal destino debe recepcionar para aumentar su stock.
```

### 4. **Documentación** (`REBAJA_STOCK_EMISION_DTE.md`)

- ✅ Actualizado comportamiento de despacho interno
- ✅ Corregido ejemplo práctico
- ✅ Actualizada tabla comparativa
- ✅ Corregidas recomendaciones
- ✅ Actualizado código de verificación

---

## 📊 Flujo Completo Actualizado

### Escenario: EDEL emite DTE interno de 50 unidades hacia NICK1

#### **Paso 1: Emisión (En EDEL)**
```
Usuario en EDEL: Emite DTE interno → NICK1 (50 unidades)

✅ Acción inmediata:
   - Stock EDEL: 200 → 150 (-50) ✅
   - Movimiento creado:
     * Concepto: TRASPASO_SALIDA
     * Tipo: EGRESO
     * Estado: PENDIENTE_RECEPCION
     * Cantidad: -50
```

#### **Paso 2: En Tránsito**
```
Estado actual:
┌─────────────────────────────────────┐
│ EDEL (Origen):                      │
│   Stock: 150 ✅                     │
│   Movimiento: PENDIENTE_RECEPCION   │
│                                     │
│ NICK1 (Destino):                    │
│   Stock: 100 (sin cambios) ⏳       │
│   📦 Esperando recepción            │
└─────────────────────────────────────┘
```

#### **Paso 3: Recepción (En NICK1)**
```
Usuario en NICK1: Accede a /app/recepcion-dte/
                 Confirma recepción del DTE

✅ Acción al confirmar:
   - Stock EDEL: 150 (sin cambios) ✅
   - Movimiento EDEL: PENDIENTE_RECEPCION → COMPLETADO
   
   - Stock NICK1: 100 → 150 (+50) ✅
   - Movimiento NICK1 creado:
     * Concepto: TRASPASO_ENTRADA
     * Tipo: INGRESO
     * Estado: COMPLETADO
     * Cantidad: +50
```

---

## 🎯 Beneficios del Cambio

1. ✅ **Stock refleja realidad física**
   - Si la mercadería salió de EDEL, el stock debe disminuir

2. ✅ **Evita sobreventa**
   - No se puede vender mercadería que ya está en tránsito

3. ✅ **Consistencia**
   - Todos los documentos (internos y externos) reducen stock al emitir

4. ✅ **Mejor control de inventario**
   - Stock en sistema = Stock físico real

---

## ⚠️ Importante para Usuarios

### Para Sucursal ORIGEN (que emite):
- El stock se **reduce inmediatamente** al emitir
- No puedes cancelar el DTE una vez emitido sin ajuste manual

### Para Sucursal DESTINO (que recibe):
- **DEBES** confirmar la recepción en `/app/recepcion-dte/`
- Solo al confirmar, tu stock aumentará
- La mercadería aparece como "en tránsito" hasta que confirmes

---

## 🔍 Cómo Verificar que Funciona

1. **Emitir DTE Interno:**
   - EDEL tiene 200 unidades
   - Emite DTE a NICK1 por 50 unidades
   - ✅ Verificar: Stock EDEL debe quedar en 150

2. **Revisar Movimientos:**
   - Buscar movimiento en EDEL
   - ✅ Verificar: Tipo = EGRESO, Estado = PENDIENTE_RECEPCION

3. **Recepcionar en Destino:**
   - NICK1 tiene 100 unidades
   - Confirma recepción del DTE
   - ✅ Verificar: Stock NICK1 debe quedar en 150

4. **Verificar Completado:**
   - ✅ Movimiento EDEL: Estado = COMPLETADO
   - ✅ Movimiento NICK1: Tipo = INGRESO, Estado = COMPLETADO

---

## 📞 Archivos Modificados

1. `retailmind/app/views.py` - Lógica de emisión y recepción
2. `retailmind/app/models.py` - Nuevo estado PENDIENTE_RECEPCION
3. `retailmind/app/templates/vistas/modulo_documentos/emisionDTE.html` - Interfaz
4. `REBAJA_STOCK_EMISION_DTE.md` - Documentación actualizada

---

## ✅ Estado

- [x] Código backend corregido
- [x] Modelo actualizado con nuevo estado
- [x] Interfaz actualizada
- [x] Documentación actualizada
- [x] Sin errores de linting

**Fecha de corrección:** 2025-10-27
**Solicitado por:** Usuario
**Implementado por:** Asistente AI

