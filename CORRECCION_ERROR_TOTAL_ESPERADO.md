# 🐛 Corrección: Error en Confirmar Recepción con Productos Parciales

## ❌ Error Reportado

```python
NameError: name 'total_esperado' is not defined
```

**Contexto:** Al confirmar recepción con productos con observaciones (faltantes/parciales), el sistema procesaba correctamente pero fallaba al generar la respuesta JSON.

---

## 🔍 Análisis del Error

### Error Principal

```python
File "views.py", line 590, in confirmar_recepcion_api
    'total_esperado': total_esperado,
                      ^^^^^^^^^^^^^^
NameError: name 'total_esperado' is not defined
```

**Causa:** Las variables `total_esperado` y `total_recepcionado` no estaban definidas antes de usarlas en la respuesta JSON.

### Error Secundario

```python
django.db.transaction.TransactionManagementError: 
The rollback flag doesn't work outside of an 'atomic' block.
```

**Causa:** El bloque `except` intentaba hacer rollback con `transaction.set_rollback(True)`, pero la función no estaba decorada con `@transaction.atomic`.

---

## ✅ Soluciones Implementadas

### 1. Inicializar Variables de Totales

**Ubicación:** `views.py` - Función `confirmar_recepcion_api()` - Línea ~406

**ANTES:**
```python
# FASE 2: Procesar productos (preparar bulk inserts)
recepciones_a_crear = []
movimientos_a_crear = []
tallas_a_actualizar = {}
productos_con_problemas_data = []

for prod_data in productos_recepcion:
    # ...
    cantidad_recepcionada = int(prod_data.get('cantidad_recepcionada', 0))
    cantidad_esperada = int(prod_data.get('cantidad_esperada', 0))
    # ... (no se acumulaban los totales)
```

**DESPUÉS:**
```python
# FASE 2: Procesar productos (preparar bulk inserts)
recepciones_a_crear = []
movimientos_a_crear = []
tallas_a_actualizar = {}
productos_con_problemas_data = []

# ✅ Variables para el resumen
total_esperado = 0
total_recepcionado = 0

for prod_data in productos_recepcion:
    # ...
    cantidad_recepcionada = int(prod_data.get('cantidad_recepcionada', 0))
    cantidad_esperada = int(prod_data.get('cantidad_esperada', 0))
    # ...
    
    # ✅ Acumular totales
    total_esperado += cantidad_esperada
    total_recepcionado += cantidad_recepcionada
```

**Beneficio:** Ahora se calculan los totales correctamente para cada producto procesado.

---

### 2. Remover Rollback Incorrecto

**Ubicación:** `views.py` - Función `confirmar_recepcion_api()` - Línea ~594

**ANTES:**
```python
except Exception as e:
    import traceback
    traceback.print_exc()
    transaction.set_rollback(True)  # ❌ Error: no está en bloque atomic
    return JsonResponse({
        'success': False,
        'error': f'Error al confirmar recepción: {str(e)}'
    }, status=500)
```

**DESPUÉS:**
```python
except Exception as e:
    import traceback
    traceback.print_exc()
    # ✅ Rollback automático - la transacción ya está en with transaction.atomic()
    return JsonResponse({
        'success': False,
        'error': f'Error al confirmar recepción: {str(e)}'
    }, status=500)
```

**Explicación:**
- La función ya tiene un bloque `with transaction.atomic()` interno (línea ~393)
- No necesita `transaction.set_rollback(True)` porque el rollback es automático
- Al salir del bloque `with` por una excepción, Django hace rollback automáticamente

---

## 📊 Respuesta JSON Corregida

### Estructura de la Respuesta

```json
{
  "success": true,
  "message": "Recepción procesada. 3 producto(s) requieren atención.",
  "estado_dte": "RECEPCIONADO_PARCIAL",
  "productos_ok": 17,
  "productos_problemas": 3,
  "total_esperado": 45,
  "total_recepcionado": 42
}
```

**Campos agregados:**
- `total_esperado`: Suma de todas las cantidades esperadas
- `total_recepcionado`: Suma de todas las cantidades recepcionadas

---

## 🧪 Testing

### Escenarios de Prueba

| # | Escenario | total_esperado | total_recepcionado | Estado Esperado |
|---|-----------|----------------|---------------------|-----------------|
| 1 | Todos OK | 50 | 50 | RECEPCIONADO_COMPLETO |
| 2 | Algunos faltantes | 50 | 45 | RECEPCIONADO_PARCIAL |
| 3 | Algunos dañados | 50 | 48 (50-2) | RECEPCIONADO_PARCIAL |
| 4 | Mix (OK + problemas) | 50 | 47 | RECEPCIONADO_PARCIAL |

### Cómo Probar

1. **Abrir Recepción DTE:** `http://localhost:8000/app/recepcion-dte/`
2. **Seleccionar un DTE** de la lista
3. **Marcar productos con problemas:**
   - Desmarcar algunos productos
   - Agregar observaciones
   - Ajustar cantidades recibidas
4. **Confirmar recepción**
5. **Verificar:**
   - ✅ No hay error `NameError`
   - ✅ Respuesta JSON completa
   - ✅ Stock actualizado correctamente
   - ✅ Estado DTE correcto

---

## 📝 Log de Ejemplo Exitoso

```
✓ 4784669 +1 en PAO3 (2 → 3)
✓ 4784672 +1 en PAO3 (2 → 3)
✓ 4784674 +1 en PAO3 (0 → 1)
✓ 4784675 +2 en PAO3 (0 → 2)
✓ 4784677 +1 en PAO3 (0 → 1)
✓ DTE #1 - RECEPCIONADO_PARCIAL (OK:17, Problemas:3)
✓ Notificación enviada a PAO2
[200] POST /app/dte/confirmar_recepcion/
```

---

## 🔧 Archivos Modificados

### `views.py` - Función `confirmar_recepcion_api()`

**Cambio 1:** Líneas ~406-410
- ✅ Agregadas variables `total_esperado` y `total_recepcionado`
- ✅ Acumulación de totales en el loop

**Cambio 2:** Líneas ~594-600
- ✅ Removido `transaction.set_rollback(True)`
- ✅ Simplificado manejo de excepciones

---

## 💡 Explicación Técnica

### Por Qué Funcionaba Parcialmente

1. **La recepción SÍ se procesaba correctamente:**
   - Productos recepcionados ✅
   - Stock actualizado ✅
   - Movimientos registrados ✅
   - Estado DTE actualizado ✅

2. **El error ocurría AL FINAL:**
   - Al generar la respuesta JSON
   - Las variables no existían
   - Python lanzaba `NameError`

3. **Resultado:**
   - La transacción se completaba (ya estaba fuera del bloque `with`)
   - El frontend recibía error 500
   - Pero los datos YA estaban guardados

### Gestión de Transacciones en Django

```python
# ✅ CORRECTO: Rollback automático
with transaction.atomic():
    # ... operaciones de BD ...
    if hay_error:
        raise Exception("Error")  # Rollback automático

# ❌ INCORRECTO: Intentar rollback fuera
try:
    # ... sin transaction.atomic() ...
    pass
except:
    transaction.set_rollback(True)  # ❌ Error!
```

**Regla:** Solo usa `transaction.set_rollback()` DENTRO de un bloque `atomic()`.

---

## 🎉 Resultado

✅ **Error corregido completamente**

**Ahora funciona:**
- ✅ Recepciones completas (todos OK)
- ✅ Recepciones parciales (con problemas)
- ✅ Respuesta JSON completa con totales
- ✅ Sin errores `NameError`
- ✅ Sin errores `TransactionManagementError`

---

**Fecha:** 21 de enero de 2026  
**Tipo de corrección:** Bug fix - Variables no definidas  
**Impacto:** Crítico - Bloqueaba confirmación de recepciones parciales
