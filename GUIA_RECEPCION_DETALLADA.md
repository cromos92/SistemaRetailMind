# 📦 Guía de Uso: Sistema de Recepción Detallada

## 🎯 Qué Cambió

**ANTES:**
- ✅ o ❌ Todo o nada
- Sin control de problemas
- Si un producto falta, debes rechazar todo

**AHORA:**
- ✅ Recepción parcial
- ⚠️ Control de problemas por producto
- 📝 Observaciones detalladas
- 📊 Seguimiento de regularizaciones

---

## 🚀 Cómo Usar

### 1. Acceder a Recepción de DTEs

```
http://localhost:8000/app/recepcion-dte/
```

Verás los DTEs pendientes dirigidos a tu sucursal.

---

### 2. Ver Detalle del DTE

Haz clic en **"Ver Detalle"** de cualquier DTE pendiente.

Se abrirá el **Modal de Verificación** con:
- ✅ Todos los productos marcados como OK por defecto
- 🔍 Búsqueda rápida por SKU
- 📊 Resumen en tiempo real

---

### 3. Verificar Productos

#### **Opción A: TODO LLEGÓ BIEN** ✅
1. No hagas nada (todo viene marcado OK)
2. Click en **"Confirmar Recepción Completa"**
3. Confirmar
4. ✅ Stock se actualiza automáticamente

#### **Opción B: HAY PROBLEMAS** ⚠️

##### **Método 1: Cambiar Cantidad**
1. Cambia la cantidad en la columna "Recibido"
2. El sistema auto-calcula el faltante
3. El estado cambia a "Parcial" automáticamente

**Ejemplo:**
```
Esperado: 10
Recibido: 8   ← Cambias esto
Estado: Parcial ⚠️ (auto-detectado)
Faltante: 2 (auto-calculado)
```

##### **Método 2: Detallar Problema Completo**
1. Click en el botón **⚠️** (última columna)
2. Se abre modal "Detallar Problema"
3. Completa:
   - Cantidad Recepcionada
   - Cantidad Dañada
   - Cantidad Faltante
   - Estado (OK, Parcial, Dañado, Faltante)
   - Observaciones

**Ejemplo:**
```
Producto: Zapatilla Nike Talla 42
Esperado: 10
Recepcionado: 8
Dañado: 2
Faltante: 0
Estado: RECEPCIONADO_DANADO
Obs: "2 unidades con caja rota"
```

##### **Método 3: Desmarcar Checkbox**
1. Desmarca el checkbox en la columna "OK"
2. Se abre automáticamente el modal de problemas
3. Detalla el problema

---

### 4. Usar Acciones Rápidas

#### **"Marcar todos OK"**
- Marca todos los productos como recepcionados correctamente
- Útil cuando todo llegó bien

#### **"Desmarcar todos"**
- Desmarca todos
- Útil para empezar a verificar uno por uno

#### **Búsqueda**
- Filtra productos por SKU, descripción o talla
- En tiempo real
- Útil cuando hay 50+ productos

---

### 5. Revisar Resumen

El resumen se actualiza en tiempo real:

```
┌──────────────────────────────────────┐
│ OK ✅: 45 productos                  │
│ Parcial ⚠️: 3 productos              │
│ Dañados 🔴: 0 productos              │
│ Faltantes ❌: 2 productos            │
└──────────────────────────────────────┘
```

---

### 6. Confirmar Recepción

#### **Si TODO está OK:**
- Botón verde: **"Confirmar Recepción Completa"**
- Todos los productos se recepcionan
- Estado DTE: `RECEPCIONADO_COMPLETO`

#### **Si HAY PROBLEMAS:**
- Aparece botón amarillo: **"Recepcionar con Problemas"**
- Opciones:
  - **"Recepcionar Solo OK"**: Solo los productos OK (deja los problemas para después)
  - **"Recepcionar con Problemas"**: Recepciona todo (OK y problemas)

---

### 7. Después de Confirmar

El sistema muestra:

```
✅ Recepción Completa (o Parcial)

Productos OK: 45
Con problemas: 5
Total esperado: 50
Total recepcionado: 48

[Entendido]
```

---

## 📊 Estados y Significados

### Estados de Producto

| Estado | Significado | Stock se actualiza | Requiere acción |
|--------|------------|-------------------|----------------|
| **RECEPCIONADO_OK** | Llegó bien ✅ | ✅ Sí | ❌ No |
| **RECEPCIONADO_PARCIAL** | Cantidad incorrecta ⚠️ | ✅ Parcial | ✅ Regularizar |
| **RECEPCIONADO_DANADO** | Con defectos 🔴 | ❌ No | ✅ Regularizar |
| **FALTANTE** | No llegó ❌ | ❌ No | ✅ Regularizar |
| **EN_REGULARIZACION** | Esperando solución ⏳ | ❌ No | ✅ Pendiente |
| **REGULARIZADO** | Resuelto ✅ | ✅ Sí | ❌ No |

### Estados de DTE

| Estado | Significado | Siguiente paso |
|--------|------------|---------------|
| **EMITIDO** | Enviado, esperando | Recepcionar |
| **RECEPCIONADO_COMPLETO** | Todo OK ✅ | Ninguno |
| **RECEPCIONADO_PARCIAL** | Con problemas ⚠️ | Regularizar |
| **EN_REGULARIZACION** | Resolviendo ⏳ | Regularizar |
| **RECHAZADO** | No aceptado ❌ | Revisar con origen |

---

## 💡 Casos de Uso Comunes

### Caso 1: Todo Llegó Bien
```
1. Abrir modal
2. Verificar visualmente (opcional)
3. Click "Confirmar Recepción Completa"
4. ✅ Listo
```

### Caso 2: Falta 1 Producto
```
1. Abrir modal
2. Buscar el producto faltante
3. Cambiar "Recibido" a 0
4. Click botón ⚠️ para detallar
5. Estado: FALTANTE
6. Obs: "No llegó en el envío"
7. Guardar
8. Click "Recepcionar con Problemas"
9. ✅ Se recepciona lo que llegó, lo faltante queda pendiente
```

### Caso 3: Productos Dañados
```
1. Abrir modal
2. Buscar el producto dañado
3. Click botón ⚠️
4. Cantidad Recepcionada: 10
5. Cantidad Dañada: 2
6. Estado: RECEPCIONADO_DANADO
7. Obs: "2 unidades con caja rota"
8. Guardar
9. Click "Recepcionar con Problemas"
10. ✅ Solo 8 unidades OK se ingresan al stock
```

### Caso 4: 50 Productos (Lista Larga)
```
1. Abrir modal
2. Usar búsqueda: "NIKE" → Filtra solo productos Nike
3. Verificar grupo por grupo
4. Usar "Marcar todos OK" si es necesario
5. Hacer scroll para ver todos
6. Confirmar
```

---

## 🔍 Consultas SQL Útiles

### Ver Recepciones con Problemas
```sql
SELECT 
    pr.id,
    d.numero_documento,
    pt.sku,
    pr.cantidad_esperada,
    pr.stockArribado AS cantidad_recepcionada,
    pr.cantidad_danada,
    pr.cantidad_faltante,
    pr.estado,
    pr.observaciones
FROM app_productos_recepcionados pr
INNER JOIN app_dte d ON pr.dte_id = d.id
INNER JOIN app_producto_talla pt ON pr.producto_talla_id = pt.id
WHERE pr.estado IN ('RECEPCIONADO_PARCIAL', 'RECEPCIONADO_DANADO', 'FALTANTE', 'EN_REGULARIZACION')
ORDER BY pr.fecha DESC;
```

### Ver DTEs Parcialmente Recepcionados
```sql
SELECT 
    id,
    numero_documento,
    tipo_documento,
    estado_dte,
    fecha_emision,
    fecha_recepcion
FROM app_dte
WHERE estado_dte = 'RECEPCIONADO_PARCIAL'
ORDER BY fecha_recepcion DESC;
```

---

## ✅ Checklist de Implementación

- [x] Modelo `Productos_Recepcionados` expandido
- [x] Estados de DTE actualizados
- [x] Estados de Producto creados
- [x] Vista `confirmar_recepcion_api` actualizada
- [x] Modal mejorado con scroll
- [x] Búsqueda en tiempo real
- [x] Checkboxes y cantidades
- [x] Modal de problemas
- [x] Proceso de 2 pasos
- [x] Resumen dinámico
- [x] SQL de migración
- [ ] Vista de regularizaciones (próximo paso)

---

## 📞 Archivos Creados/Modificados

1. `retailmind/app/models.py` - Modelo expandido
2. `retailmind/app/views.py` - Vista actualizada
3. `retailmind/app/templates/vistas/modulo_compras/recepcion_dte.html` - Modal mejorado
4. `MIGRACION_SISTEMA_RECEPCION_DETALLADA.sql` - SQL
5. `SISTEMA_RECEPCION_DETALLADA.md` - Arquitectura
6. `GUIA_RECEPCION_DETALLADA.md` - Este documento

---

## 🎯 Próximo Paso

Crear vista para **Gestión de Regularizaciones**:
- Ver productos pendientes de regularización
- Resolver problemas (actualizar cantidades)
- Marcar como regularizado
- Actualizar stock

---

**Fecha:** 2025-10-27  
**Versión:** 2.0 - Sistema Detallado  
**Estado:** ✅ Implementado y funcional

