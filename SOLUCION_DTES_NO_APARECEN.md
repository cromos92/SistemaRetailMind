# 🔧 Solución: DTEs no aparecen en Recepción

## 🐛 Problema

Al entrar a `http://localhost:8000/app/recepcion-dte/` no aparecen los DTEs para recepcionar.

## 🔍 Causa

Los DTEs emitidos **ANTES** del cambio en el código tienen:
- `estado = 'PENDIENTE'` (estado antiguo)
- `tipo_movimiento = 'TRASPASO'` (tipo antiguo)

Pero el nuevo sistema busca:
- `estado = 'PENDIENTE_RECEPCION'` (estado nuevo)
- `tipo_movimiento = 'EGRESO'` (tipo nuevo)

## ✅ Solución

Tienes **2 opciones**:

---

### **OPCIÓN 1: Actualizar DTEs Existentes** (Si ya tienes DTEs emitidos)

Si ya emitiste DTEs internos antes del cambio, ejecuta este SQL:

```sql
UPDATE app_movimientos_producto
SET 
    estado = 'PENDIENTE_RECEPCION',
    tipo_movimiento = 'EGRESO'
WHERE estado = 'PENDIENTE'
  AND concepto = 'TRASPASO_SALIDA'
  AND tipo_movimiento = 'TRASPASO';
```

**Cómo ejecutar:**
1. Abre tu gestor de base de datos (pgAdmin, DBeaver, etc.)
2. Ejecuta el SQL de arriba
3. Refresca `http://localhost:8000/app/recepcion-dte/`
4. ✅ Deberían aparecer los DTEs

---

### **OPCIÓN 2: Emitir Nuevo DTE** (Más fácil para probar)

Si NO tienes DTEs antiguos o quieres probar con uno nuevo:

1. **Emitir DTE Interno:**
   - Ir a: `http://localhost:8000/app/emisionDTE/`
   - Seleccionar **Despacho Interno**
   - Seleccionar Sucursal Destino (ej: NICK1)
   - Agregar productos
   - Emitir

2. **Verificar Recepción:**
   - Cambiar a la sucursal destino (NICK1)
   - Ir a: `http://localhost:8000/app/recepcion-dte/`
   - ✅ Debería aparecer el DTE recién emitido

---

## 🔍 Diagnóstico (Opcional)

Para verificar qué está pasando en tu base de datos, puedes ejecutar:

### **SQL de Diagnóstico:**

```sql
-- Ver DTEs de traspaso
SELECT 
    d.id,
    d.numero_documento,
    d.tipo_transaccion,
    d.estado_dte,
    d.fecha_recepcion,
    d.fecha_emision
FROM app_dte d
WHERE d.tipo_transaccion = 'TRASPASO'
ORDER BY d.fecha_emision DESC
LIMIT 10;

-- Ver movimientos y sus estados
SELECT 
    m.id,
    m.dte_id,
    d.numero_documento,
    m.concepto,
    m.tipo_movimiento,
    m.estado,
    so.alias AS origen,
    sd.alias AS destino
FROM app_movimientos_producto m
INNER JOIN app_dte d ON m.dte_id = d.id
LEFT JOIN app_sucursal so ON m.sucursal_origen_id = so.id
LEFT JOIN app_sucursal sd ON m.sucursal_destino_id = sd.id
WHERE m.concepto = 'TRASPASO_SALIDA'
ORDER BY m.id DESC
LIMIT 10;

-- Ver cuántos tienen estado antiguo vs nuevo
SELECT 
    estado,
    tipo_movimiento,
    COUNT(*) AS cantidad
FROM app_movimientos_producto
WHERE concepto = 'TRASPASO_SALIDA'
GROUP BY estado, tipo_movimiento;
```

**Resultado esperado:**

Si tienes DTEs antiguos verás:
```
estado                | tipo_movimiento | cantidad
---------------------|-----------------|----------
PENDIENTE            | TRASPASO        | 5        ← Antiguo (no aparecen)
PENDIENTE_RECEPCION  | EGRESO          | 0        ← Nuevo (aparecen)
```

Después de ejecutar el UPDATE:
```
estado                | tipo_movimiento | cantidad
---------------------|-----------------|----------
PENDIENTE_RECEPCION  | EGRESO          | 5        ← Todo actualizado ✅
```

---

## 📋 Cambios Realizados en el Código

### ✅ Eliminado Filtro "Sucursal Origen"
- Ahora solo tienes 3 filtros:
  - ✅ Tipo de Documento
  - ✅ Fecha desde
  - ✅ Fecha hasta

### ✅ Estados Corregidos
Se actualizaron 3 lugares en `views.py`:
- Línea 134: Filtro de movimientos
- Línea 210: Carga de sucursales origen
- Línea 352: Confirmación de recepción

---

## 🎯 Verificación Final

Para verificar que todo funciona:

1. **Emitir DTE:**
   - Sucursal EDEL → Emite DTE a NICK1
   - ✅ Stock EDEL se reduce inmediatamente

2. **Recepcionar DTE:**
   - Sucursal NICK1 → Abre `/app/recepcion-dte/`
   - ✅ Ve el DTE emitido por EDEL
   - Confirma recepción
   - ✅ Stock NICK1 aumenta

3. **Verificar Base de Datos:**
```sql
-- Ver el DTE recepcionado
SELECT * FROM app_dte 
WHERE numero_documento = 12345;  -- Reemplaza con tu número
-- fecha_recepcion debe tener fecha ✅

-- Ver movimientos completados
SELECT * FROM app_movimientos_producto 
WHERE dte_id = (SELECT id FROM app_dte WHERE numero_documento = 12345);
-- Deberían haber 2:
-- 1. TRASPASO_SALIDA | EGRESO | COMPLETADO
-- 2. TRASPASO_ENTRADA | INGRESO | COMPLETADO
```

---

## 📞 Archivos Creados

1. `DIAGNOSTICO_RECEPCION_DTE.py` - Script Python para diagnosticar
2. `ACTUALIZAR_ESTADOS_MOVIMIENTOS.sql` - SQL completo con ejemplos
3. `SOLUCION_DTES_NO_APARECEN.md` - Este documento

---

## 💡 Resumen Rápido

**Si tienes DTEs antiguos:**
```sql
UPDATE app_movimientos_producto
SET estado = 'PENDIENTE_RECEPCION', tipo_movimiento = 'EGRESO'
WHERE estado = 'PENDIENTE' AND concepto = 'TRASPASO_SALIDA';
```

**Si NO tienes DTEs:**
- Emite uno nuevo desde `/app/emisionDTE/`
- Ya se creará con el estado correcto automáticamente ✅

---

**Fecha:** 2025-10-27  
**Estado:** ✅ Código actualizado, filtro eliminado

