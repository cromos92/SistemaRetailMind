# 📦 Sistema de Recepción Detallada para DTEs

## 🎯 Objetivo

Transformar el proceso de recepción de DTEs de un sistema simple "todo o nada" a un sistema robusto que permita:

- ✅ Recepciones parciales (45 de 50 productos)
- ✅ Marcar productos con problemas (dañados, faltantes)
- ✅ Observaciones por producto
- ✅ Estados intermedios (EN_REGULARIZACIÓN)
- ✅ Scroll optimizado para muchos productos
- ✅ Búsqueda rápida por SKU

---

## 🏗️ Arquitectura

### Modelo REUTILIZADO: `Productos_Recepcionados`

**Antes** (solo compras):
```python
compra_producto_talla
stockArribado  
fecha
```

**Ahora** (compras + traspasos):
```python
# Legacy (compras)
compra_producto_talla  # Nullable ahora

# Nuevo (traspasos)
dte                    # DTE de traspaso
dte_producto          # Producto específico
cantidad_esperada     # Cantidad original
cantidad_danada       # Con defectos
cantidad_faltante     # No llegaron
estado                # Ver estados abajo
observaciones         # Problemas/notas
recepcionado_por      # Usuario
fecha_regularizacion  # Si hubo problemas
```

### Estados DTE

```python
EMITIDO                → Enviado, esperando recepción
ACEPTADO              → Legacy (mantener compatibilidad)
RECEPCIONADO_COMPLETO → Todo llegó OK ✅
RECEPCIONADO_PARCIAL  → Algunos problemas ⚠️
EN_REGULARIZACION     → Esperando solución
RECHAZADO             → No se acepta
ANULADO               → Anulado
```

### Estados Producto

```python
PENDIENTE             → Esperando recepción
RECEPCIONADO_OK       → Llegó bien ✅
RECEPCIONADO_PARCIAL  → Cantidad incorrecta ⚠️
RECEPCIONADO_DANADO   → Con daños ⚠️
FALTANTE              → No llegó ❌
EN_REGULARIZACION     → Esperando reposición
REGULARIZADO          → Problema resuelto ✅
```

---

## 📋 Proceso de 2 Pasos

### PASO 1: Verificación (Modal Mejorado)

```
┌──────────────────────────────────────────────────────┐
│ 📦 Verificar Recepción - DTE #1092                   │
├──────────────────────────────────────────────────────┤
│ 🔍 Búsqueda: [_____________] 🔎                      │
│                                                       │
│ ┌────────────────────────────────────────────────┐   │
│ │ ☑ SKU-001  Zapatilla Nike 42  [10/10] ✅      │   │
│ │ ☑ SKU-002  Polera Adidas M    [5/5]   ✅      │   │
│ │ ☐ SKU-003  Short Puma L       [2/3]   ⚠️      │   │
│ │   └─ Obs: [Solo llegaron 2________]            │   │
│ │ ☐ SKU-004  Gorro Nike U       [6/8]   ⚠️      │   │
│ │   └─ Obs: [2 unidades dañadas_]                │   │
│ │ ...                                             │   │
│ └────────────────────────────────────────────────┘   │
│                                                       │
│ 📊 Resumen:                                           │
│ • Total: 50 | OK: 45 ✅ | Problemas: 3 ⚠️ | Falta: 2 │
│                                                       │
│ [← Cancelar] [Recepcionar Parcial] [✓ Todo OK]      │
└──────────────────────────────────────────────────────┘
```

### PASO 2: Confirmación

```
┌──────────────────────────────────────────────────────┐
│ ⚠️ Confirmar Recepción Parcial                       │
├──────────────────────────────────────────────────────┤
│                                                       │
│ ✅ 45 productos OK → Stock se actualizará             │
│ ⚠️ 3 con problemas → Quedarán EN_REGULARIZACIÓN      │
│ ❌ 2 faltantes → Se creará INCIDENCIA                │
│                                                       │
│ Estado DTE: RECEPCIONADO_PARCIAL                     │
│                                                       │
│ [← Volver] [✓ Confirmar]                             │
└──────────────────────────────────────────────────────┘
```

---

## 🔧 Instalación

### 1. Ejecutar Migración SQL

```bash
# En tu gestor de BD (pgAdmin, DBeaver, etc.)
# Ejecutar: MIGRACION_SISTEMA_RECEPCION_DETALLADA.sql
```

### 2. Verificar Campos

```sql
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_name = 'app_productos_recepcionados'
ORDER BY ordinal_position;
```

Deberías ver:
- ✅ `dte_producto_id`
- ✅ `cantidad_esperada`
- ✅ `cantidad_danada`
- ✅ `cantidad_faltante`
- ✅ `estado`
- ✅ `observaciones`
- ✅ `recepcionado_por`

---

## 📊 Flujo Completo

### Emisión (Ya implementado)

```python
# EDEL → PAO1 (50 productos)
dte = Dte.objects.create(
    tipo_transaccion='TRASPASO',
    estado_dte='EMITIDO',
    ...
)

# Stock EDEL se reduce INMEDIATAMENTE ✅
talla.stock -= cantidad
```

### Recepción Detallada (Nuevo)

```python
# PAO1 ve el DTE en /app/recepcion-dte/
# Abre modal de verificación
# Marca productos:
#   - 45 OK ✅
#   - 3 con problemas ⚠️
#   - 2 faltantes ❌

# Al confirmar se ejecuta:
for producto in productos:
    if producto.ok:
        # Stock PAO1 aumenta
        talla.stock += cantidad
        
        # Registra recepción
        Productos_Recepcionados.objects.create(
            dte=dte,
            dte_producto=dte_producto,
            producto_talla=talla,
            stockArribado=cantidad,
            cantidad_esperada=cantidad,
            estado='RECEPCIONADO_OK',
            recepcionado_por='usuario_pao1'
        )
    
    elif producto.problemas:
        # NO aumenta stock aún
        Productos_Recepcionados.objects.create(
            ...
            stockArribado=cantidad_real,
            cantidad_esperada=cantidad_esperada,
            cantidad_danada=2,
            estado='RECEPCIONADO_DANADO',
            observaciones='2 unidades con defecto'
        )

# Estado final DTE
if todo_ok:
    dte.estado_dte = 'RECEPCIONADO_COMPLETO'
elif algunos_problemas:
    dte.estado_dte = 'RECEPCIONADO_PARCIAL'
```

### Regularización (Pendiente implementar)

```python
# Vista para resolver problemas
# /app/regularizar-recepciones/

# Cuando se regulariza:
recepcion = Productos_Recepcionados.objects.get(...)
recepcion.estado = 'REGULARIZADO'
recepcion.fecha_regularizacion = now()
recepcion.regularizado_por = 'usuario'
recepcion.save()

# Stock se actualiza
talla.stock += cantidad_faltante
```

---

## 🎨 Componentes Frontend

### 1. Modal Mejorado (HTML)

**Características:**
- ✅ Scroll infinito
- ✅ Búsqueda por SKU
- ✅ Checkboxes por producto
- ✅ Input de cantidad por producto
- ✅ Textarea de observaciones
- ✅ Resumen en tiempo real
- ✅ Validación de cantidades

### 2. JavaScript

**Funciones principales:**
```javascript
verificarRecepcion(dte_id)        // Abre modal
marcarProducto(producto_id, ok)   // Toggle checkbox
actualizarCantidad(producto_id)   // Validar cantidad
buscarProducto(query)             // Filtrar lista
calcularResumen()                 // Actualizar contadores
confirmarRecepcion()              // Paso 2
procesarRecepcion()               // POST al backend
```

---

## 📡 API Endpoints

### `POST /app/dte/verificar_recepcion/`
Preparar datos para modal de verificación

**Request:**
```json
{
  "dte_id": 123
}
```

**Response:**
```json
{
  "dte": {...},
  "productos": [
    {
      "id": 1,
      "sku": "SKU-001",
      "descripcion": "Zapatilla Nike",
      "talla": "42",
      "cantidad_esperada": 10,
      "precio": 50000
    }
  ]
}
```

### `POST /app/dte/confirmar_recepcion/`
Procesar recepción detallada

**Request:**
```json
{
  "dte_id": 123,
  "productos": [
    {
      "dte_producto_id": 1,
      "cantidad_recepcionada": 10,
      "cantidad_danada": 0,
      "cantidad_faltante": 0,
      "estado": "RECEPCIONADO_OK",
      "observaciones": ""
    },
    {
      "dte_producto_id": 2,
      "cantidad_recepcionada": 2,
      "cantidad_danada": 1,
      "cantidad_faltante": 2,
      "estado": "RECEPCIONADO_PARCIAL",
      "observaciones": "Faltaron 2 unidades"
    }
  ],
  "observaciones_generales": "Recibido con problemas menores"
}
```

**Response:**
```json
{
  "success": true,
  "estado_dte": "RECEPCIONADO_PARCIAL",
  "productos_ok": 45,
  "productos_problemas": 5,
  "message": "Recepción procesada. 5 productos requieren regularización"
}
```

---

## 🚀 Estado de Implementación

- [x] Modelo expandido (Productos_Recepcionados)
- [x] Estados DTE actualizados
- [x] Estados Producto creados
- [x] SQL de migración
- [ ] Vista confirmar_recepcion_api actualizada
- [ ] Modal mejorado (HTML + CSS)
- [ ] JavaScript de verificación
- [ ] Vista de regularizaciones
- [ ] Tests

---

## 📞 Archivos del Sistema

1. `MIGRACION_SISTEMA_RECEPCION_DETALLADA.sql` - SQL manual
2. `SISTEMA_RECEPCION_DETALLADA.md` - Este documento
3. `retailmind/app/models.py` - Modelo expandido
4. `retailmind/app/views.py` - Vista actualizada (pendiente)
5. `retailmind/app/templates/vistas/modulo_compras/recepcion_dte.html` - Modal mejorado (pendiente)

---

**Próximo paso:** Ejecutar el SQL y continuar con la implementación de la vista actualizada.

