# 🔄 Lógica de Cambios y Devoluciones Múltiples

## 📋 Resumen

El sistema permite **cambios parciales múltiples** sobre el mismo documento (Ticket/DTE), siempre que haya productos disponibles y se esté dentro del plazo de 30 días.

---

## ✅ Reglas de Negocio

### 1. **Cambios Parciales Permitidos**
- ✅ Puedes hacer múltiples cambios del mismo documento
- ✅ Solo se consumen los productos que realmente se cambian
- ✅ Los productos no cambiados quedan disponibles para futuros cambios

### 2. **Estados de Cambio**
El sistema solo cuenta como "ya cambiado" los cambios en estos estados:
- ✅ **APROBADO** - Cambio autorizado, pendiente de completar
- ✅ **COMPLETADO** - Cambio finalizado, productos ya movidos en inventario

Los cambios en estos estados NO bloquean nuevos cambios:
- ⏳ **SOLICITADO** - Aún no aprobado, NO consume productos
- ❌ **RECHAZADO** - Rechazado, NO consume productos
- ❌ **CANCELADO** - Cancelado, NO consume productos

### 3. **Cálculo de Disponibilidad**
```python
cantidad_disponible = cantidad_original - cantidad_ya_cambiada_aprobada_o_completada
```

**Ejemplo práctico:**
- Compra original: **5 pares de zapatos** (Talla 9)
- Primer cambio (COMPLETADO): **2 pares** → Quedan **3 disponibles**
- Segundo cambio (SOLICITADO): **1 par** → Aún quedan **3 disponibles** (no se descuenta hasta aprobar)
- Si se aprueba el segundo: Quedarían **2 disponibles**

---

## 🎯 Casos de Uso

### Caso 1: Cambio Parcial Simple
```
📦 Compra: 3 productos (A, B, C) - Ticket #123

🔄 Cambio 1 (Día 5):
   - Devuelve: Producto A
   - Estado: SOLICITADO → APROBADO → COMPLETADO
   - Disponibles: B, C

🔄 Cambio 2 (Día 10):
   - Devuelve: Producto B
   - Estado: SOLICITADO
   - Disponibles: C (+ B si rechazan cambio 2)

✅ RESULTADO: Sistema funciona correctamente
```

### Caso 2: Múltiples Cambios Pendientes
```
📦 Compra: 2 productos (A, B) - Ticket #456

🔄 Cambio 1 (Día 3):
   - Devuelve: Producto A
   - Estado: SOLICITADO (pendiente aprobación)

🔄 Cambio 2 (Día 4):
   - Devuelve: Producto B
   - Estado: SOLICITADO (pendiente aprobación)

⚠️ ADVERTENCIA: Sistema permite crear cambio 2
   - Producto A: Disponible (cambio 1 aún no aprobado)
   - Producto B: Disponible

✅ RESULTADO: Sistema permite, mostrará alerta informativa
```

### Caso 3: Todos los Productos Cambiados
```
📦 Compra: 2 productos (A, B) - Ticket #789

🔄 Cambio 1 (Día 5):
   - Devuelve: Productos A y B
   - Estado: COMPLETADO

🔄 Intento de Cambio 2:
   - Busca Ticket #789
   
❌ BLOQUEADO: "Todos los productos ya fueron cambiados"
   - Muestra lista de cambios anteriores
   - Explica que no quedan productos disponibles
```

### Caso 4: Fuera de Plazo
```
📦 Compra: 1 producto (A) - Ticket #999
   Fecha: 01/01/2025

🔄 Intento de Cambio (Día 35):
   - Busca Ticket #999
   - Fecha actual: 05/02/2025

❌ BLOQUEADO: "Plazo de cambio vencido"
   - Fecha límite: 31/01/2025
   - Días transcurridos: 35
   - Plazo permitido: 30 días
```

---

## 🖥️ Experiencia de Usuario

### Cuando SÍ puede hacer otro cambio:
1. Busca el documento
2. Sistema muestra:
   - ✅ Productos disponibles para cambio
   - ℹ️ Alerta informativa si hay cambios anteriores
   - 📊 Lista de cambios pendientes/completados
3. Puede continuar normalmente
4. Solo verá productos con `cantidad_disponible > 0`

### Cuando NO puede hacer otro cambio:
1. Busca el documento
2. Sistema analiza el motivo:
   - ⏰ **Fuera de plazo**: Muestra fechas y días transcurridos
   - 📦 **Sin productos disponibles**: Muestra lista de cambios anteriores
   - ❌ **No pagado**: Muestra mensaje de error
3. Muestra mensaje específico según el problema
4. Ofrece detalles completos del historial

---

## 🔍 Sistema de Escaneo de Productos

### Propósito
Validar que los productos físicos que el cliente trae **realmente correspondan** al documento.

### Funcionamiento
```
1. Cliente trae productos para cambiar
2. Vendedor escanea los SKUs reales
3. Sistema valida:
   ✅ SKU existe en el documento
   ✅ Producto tiene cantidad disponible
   ✅ No fue cambiado completamente
   
4. Si SKU no pertenece al documento:
   ⚠️ Alerta visual y sonora
   ❌ No permite agregarlo
   
5. Si SKU es válido:
   ✓ Lo agrega a lista de escaneados
   ✓ Permite ajustar cantidad
   ✓ Botón "Aplicar Selección" marca automáticamente
```

### Ventajas
- ✅ Previene fraude
- ✅ Valida físicamente los productos
- ✅ Más rápido que selección manual
- ✅ Feedback inmediato (visual + sonoro)
- ✅ Modo mixto: escaneo + selección manual

---

## 📊 Información Visible al Usuario

### En la tarjeta de información del documento:
```
┌─────────────────────────────────────────┐
│ Ticket #: 123                           │
│ Fecha: 04/11/2025                       │
│ Cliente: Juan Pérez                     │
│ Total: $149.916                         │
│ Vendedor: María                         │
│ ─────────────────────────────────────── │
│ Días transcurridos: 5                   │
│ Fecha límite: 04/12/2025               │
│ Estado: ✓ Dentro del plazo             │
│ Cambios anteriores: 1                   │
│   • 🕐 1 pendiente                     │
│   • ℹ️ 2 productos disponibles        │
└─────────────────────────────────────────┘
```

### Mensajes Informativos

#### ✅ Puede continuar (con cambios anteriores):
```
┌──────────────────────────────────────────────┐
│ ℹ️ Cambios Parciales Detectados             │
├──────────────────────────────────────────────┤
│ Este documento tiene 1 cambio(s) anterior(es)│
│                                              │
│ Estado actual:                               │
│ • CD-2-202511-0005 - Cambio Simple          │
│   Estado: Solicitado | Fecha: 04/11/2025    │
│                                              │
│ ✓ Puede continuar. Aún quedan               │
│   2 producto(s) disponible(s) para cambio.  │
│                                              │
│ 💡 Nota: El sistema permite múltiples       │
│    cambios parciales hasta agotar todos     │
│    los productos del documento original.    │
└──────────────────────────────────────────────┘
```

#### ❌ No puede continuar (productos agotados):
```
┌──────────────────────────────────────────────┐
│ ℹ️ Productos Agotados                       │
├──────────────────────────────────────────────┤
│ Todos los productos de este documento       │
│ ya fueron cambiados                          │
│                                              │
│ 📋 Documento: #123                          │
│ 📅 Fecha de compra: 04/11/2025              │
│ 🔄 Cambios realizados: 2                    │
│                                              │
│ • CD-2-202511-0005 - Cambio Simple          │
│   Estado: Completado | Fecha: 05/11/2025    │
│                                              │
│ • CD-2-202511-0008 - Devolución             │
│   Estado: Completado | Fecha: 06/11/2025    │
│                                              │
│ 💡 Nota: Los cambios parciales permiten     │
│    realizar múltiples cambios hasta agotar  │
│    todos los productos del documento.       │
└──────────────────────────────────────────────┘
```

---

## 🛠️ Implementación Técnica

### Backend (views_modulo_ventas.py)
```python
# Calcular cantidad ya cambiada
cantidad_ya_cambiada = CambioDevolucionDetalle.objects.filter(
    producto_original=tp,
    cambio_devolucion__estado__in=['APROBADO', 'COMPLETADO']  # ⚠️ Solo estos estados
).aggregate(
    total=Sum('cantidad_original')
)['total'] or 0

# Calcular disponibles
cantidad_disponible = tp.stock - cantidad_ya_cambiada

# Solo mostrar si hay disponibles
if cantidad_disponible > 0:
    productos_data.append({...})
```

### Frontend (JavaScript)
```javascript
// Validación al buscar documento
if (response.documento.puede_cambiar) {
    // Permitir continuar
    $('#btn-siguiente').show();
    
    // Si hay cambios anteriores, informar
    if (cambiosAnteriores.length > 0) {
        mostrarAlertaInformativa();
    }
} else {
    // Determinar razón específica
    if (!dentro_del_plazo) {
        mostrarErrorPlazo();
    } else if (productos.length === 0) {
        mostrarErrorProductosAgotados();
    }
}
```

---

## ✅ Resumen de Mejoras Implementadas

1. ✅ **Corrección de cálculo de diferencia**
   - Ahora calcula basándose solo en productos seleccionados
   - No en el total del ticket

2. ✅ **Sistema de escaneo de productos**
   - Valida SKUs físicos contra el documento
   - Previene fraude
   - Feedback visual y sonoro

3. ✅ **Mensajes informativos mejorados**
   - Explica exactamente por qué no puede cambiar
   - Muestra historial de cambios anteriores
   - Indica productos disponibles

4. ✅ **Soporte para cambios parciales múltiples**
   - Permite múltiples cambios del mismo documento
   - Rastrea disponibilidad por producto
   - Muestra alertas cuando hay cambios pendientes

---

## 📝 Notas Adicionales

- El plazo de cambio es configurable (actualmente 30 días)
- Solo se procesan cambios de tickets/DTEs en estado PAGADO
- Los cambios SOLICITADOS no consumen disponibilidad hasta ser APROBADOS
- El sistema previene cambios donde el cliente recibiría dinero de vuelta (política configurable)

---

**Última actualización:** 05/11/2025

