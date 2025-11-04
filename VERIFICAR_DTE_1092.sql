-- ============================================================================
-- SQL para diagnosticar por qué DTE 1092 no aparece en PAO1
-- ============================================================================

-- 1. Ver el DTE completo
SELECT 
    'DTE 1092' AS info,
    id,
    numero_documento,
    tipo_documento,
    tipo_transaccion,
    estado_dte,
    fecha_recepcion,
    referencias
FROM app_dte
WHERE numero_documento = 1092;

-- 2. Ver TODOS los movimientos del DTE 1092
SELECT 
    'MOVIMIENTOS DTE 1092' AS info,
    m.id AS movimiento_id,
    m.dte_id,
    m.concepto,
    m.tipo_movimiento,
    m.estado,
    m.cantidad,
    so.id AS sucursal_origen_id,
    so.alias AS sucursal_origen,
    sd.id AS sucursal_destino_id,
    sd.alias AS sucursal_destino,
    m.observaciones
FROM app_movimientos_producto m
LEFT JOIN app_sucursal so ON m.sucursal_origen_id = so.id
LEFT JOIN app_sucursal sd ON m.sucursal_destino_id = sd.id
WHERE m.dte_id = (SELECT id FROM app_dte WHERE numero_documento = 1092);

-- 3. Verificar si cumple con TODAS las condiciones del query
SELECT 
    'VERIFICACIÓN CONDICIONES' AS info,
    d.id,
    d.numero_documento,
    CASE WHEN d.tipo_transaccion = 'TRASPASO' THEN '✓' ELSE 'X - Es: ' || d.tipo_transaccion END AS tipo_transaccion,
    CASE WHEN d.estado_dte = 'EMITIDO' THEN '✓' ELSE 'X - Es: ' || d.estado_dte END AS estado_dte,
    CASE WHEN d.fecha_recepcion IS NULL THEN '✓' ELSE 'X - Tiene fecha: ' || d.fecha_recepcion END AS fecha_recepcion,
    CASE WHEN EXISTS(
        SELECT 1 FROM app_movimientos_producto 
        WHERE dte_id = d.id 
          AND concepto = 'TRASPASO_SALIDA'
    ) THEN '✓' ELSE 'X' END AS tiene_traspaso_salida,
    CASE WHEN EXISTS(
        SELECT 1 FROM app_movimientos_producto 
        WHERE dte_id = d.id 
          AND tipo_movimiento = 'EGRESO'
    ) THEN '✓' ELSE 'X - Ver columna tipo_mov' END AS tiene_egreso,
    CASE WHEN EXISTS(
        SELECT 1 FROM app_movimientos_producto 
        WHERE dte_id = d.id 
          AND estado = 'PENDIENTE_RECEPCION'
    ) THEN '✓' ELSE 'X - Ver columna estado' END AS tiene_pendiente_recepcion,
    CASE WHEN EXISTS(
        SELECT 1 FROM app_movimientos_producto 
        WHERE dte_id = d.id 
          AND sucursal_destino_id = 7  -- PAO1
    ) THEN '✓' ELSE 'X - Ver columna destino' END AS va_a_pao1
FROM app_dte d
WHERE d.numero_documento = 1092;

-- 4. Ver estado actual de movimientos (columnas separadas para diagnóstico)
SELECT 
    'ESTADO MOVIMIENTOS' AS info,
    m.concepto,
    m.tipo_movimiento AS tipo_mov,
    m.estado,
    m.sucursal_destino_id AS destino_id
FROM app_movimientos_producto m
WHERE m.dte_id = (SELECT id FROM app_dte WHERE numero_documento = 1092);

-- ============================================================================
-- SOLUCIÓN: Si alguna condición falla, ejecutar estos UPDATEs
-- ============================================================================

-- Si tipo_transaccion != 'TRASPASO':
-- UPDATE app_dte 
-- SET tipo_transaccion = 'TRASPASO' 
-- WHERE numero_documento = 1092;

-- Si tipo_movimiento != 'EGRESO':
-- UPDATE app_movimientos_producto
-- SET tipo_movimiento = 'EGRESO'
-- WHERE dte_id = (SELECT id FROM app_dte WHERE numero_documento = 1092)
--   AND concepto = 'TRASPASO_SALIDA';

-- Si estado != 'PENDIENTE_RECEPCION':
-- UPDATE app_movimientos_producto
-- SET estado = 'PENDIENTE_RECEPCION'
-- WHERE dte_id = (SELECT id FROM app_dte WHERE numero_documento = 1092)
--   AND concepto = 'TRASPASO_SALIDA';

