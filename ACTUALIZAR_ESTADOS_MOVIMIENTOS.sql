-- ============================================================================
-- Script SQL para actualizar movimientos de TRASPASO a nuevo estado
-- ============================================================================
-- 
-- PROBLEMA: Los DTEs internos emitidos antes del cambio tienen estado='PENDIENTE'
--           pero el nuevo sistema busca estado='PENDIENTE_RECEPCION'
--
-- SOLUCIÓN: Actualizar todos los movimientos de traspaso al nuevo estado
-- ============================================================================

-- 1. Ver cuántos movimientos se van a actualizar (PREVIEW)
SELECT 
    'PREVIEW: Movimientos que se actualizarán' AS info,
    COUNT(*) AS total,
    COUNT(DISTINCT dte_id) AS dtes_afectados
FROM app_movimientos_producto
WHERE estado = 'PENDIENTE'
  AND concepto = 'TRASPASO_SALIDA'
  AND tipo_movimiento = 'TRASPASO';

-- 2. Ver el detalle de los que se van a actualizar
SELECT 
    id AS movimiento_id,
    dte_id,
    concepto,
    tipo_movimiento,
    estado AS estado_actual,
    'PENDIENTE_RECEPCION' AS estado_nuevo,
    'EGRESO' AS tipo_nuevo,
    (SELECT numero_documento FROM app_dte WHERE id = dte_id) AS numero_dte
FROM app_movimientos_producto
WHERE estado = 'PENDIENTE'
  AND concepto = 'TRASPASO_SALIDA'
  AND tipo_movimiento = 'TRASPASO'
ORDER BY id DESC
LIMIT 10;

-- 3. ACTUALIZAR (descomentar para ejecutar)
-- ⚠️ IMPORTANTE: Haz un backup antes de ejecutar este UPDATE

UPDATE app_movimientos_producto
SET 
    estado = 'PENDIENTE_RECEPCION',  -- Nuevo estado
    tipo_movimiento = 'EGRESO'        -- Nuevo tipo (porque el stock ya se redujo)
WHERE estado = 'PENDIENTE'
  AND concepto = 'TRASPASO_SALIDA'
  AND tipo_movimiento = 'TRASPASO';

-- 4. Verificar que se actualizaron correctamente
SELECT 
    'VERIFICACIÓN: Movimientos actualizados' AS info,
    COUNT(*) AS total,
    COUNT(DISTINCT dte_id) AS dtes_afectados
FROM app_movimientos_producto
WHERE estado = 'PENDIENTE_RECEPCION'
  AND concepto = 'TRASPASO_SALIDA'
  AND tipo_movimiento = 'EGRESO';

-- 5. Listar DTEs que ahora deberían aparecer en recepción
SELECT 
    d.id,
    d.numero_documento,
    d.tipo_documento,
    d.tipo_transaccion,
    d.estado_dte,
    d.fecha_emision,
    d.fecha_recepcion,
    s_origen.alias AS sucursal_origen,
    s_destino.alias AS sucursal_destino,
    m.estado AS estado_movimiento
FROM app_dte d
INNER JOIN app_movimientos_producto m ON m.dte_id = d.id
LEFT JOIN app_sucursal s_origen ON m.sucursal_origen_id = s_origen.id
LEFT JOIN app_sucursal s_destino ON m.sucursal_destino_id = s_destino.id
WHERE d.tipo_transaccion = 'TRASPASO'
  AND d.estado_dte = 'EMITIDO'
  AND d.fecha_recepcion IS NULL
  AND m.concepto = 'TRASPASO_SALIDA'
  AND m.tipo_movimiento = 'EGRESO'
  AND m.estado = 'PENDIENTE_RECEPCION'
ORDER BY d.fecha_emision DESC
LIMIT 20;

-- ============================================================================
-- NOTAS IMPORTANTES:
-- ============================================================================
-- 
-- 1. Este script actualiza movimientos que ya existen en la base de datos
--    con el formato antiguo al nuevo formato.
--
-- 2. Solo afecta a movimientos de TRASPASO_SALIDA que están PENDIENTES.
--
-- 3. Después de ejecutar, los DTEs deberían aparecer en /app/recepcion-dte/
--
-- 4. Si emites nuevos DTEs después del cambio en el código, estos ya se
--    crearán automáticamente con el estado correcto.
--
-- ============================================================================

