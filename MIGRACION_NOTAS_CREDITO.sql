-- ============================================================================
-- MIGRACIÓN: Sistema Mixto de Regularización con Notas de Crédito
-- ============================================================================
-- Fecha: 2025-10-27
-- Descripción: Agrega soporte para Notas de Crédito automáticas en 
--              regularizaciones entre empresas diferentes
-- ============================================================================

-- PASO 1: Agregar campos para Notas de Crédito en tabla app_dte
-- ----------------------------------------------------------------------------

ALTER TABLE app_dte 
ADD COLUMN IF NOT EXISTS es_nota_credito BOOLEAN DEFAULT FALSE;

ALTER TABLE app_dte 
ADD COLUMN IF NOT EXISTS documento_afectado_id INTEGER NULL;

ALTER TABLE app_dte 
ADD COLUMN IF NOT EXISTS motivo_nc TEXT NULL;

-- Crear foreign key para documento_afectado (self-reference)
ALTER TABLE app_dte 
ADD CONSTRAINT fk_dte_documento_afectado 
    FOREIGN KEY (documento_afectado_id) 
    REFERENCES app_dte(id) 
    ON DELETE SET NULL;

-- PASO 2: Crear índices para optimizar consultas
-- ----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_dte_es_nota_credito 
ON app_dte(es_nota_credito) 
WHERE es_nota_credito = TRUE;

CREATE INDEX IF NOT EXISTS idx_dte_documento_afectado 
ON app_dte(documento_afectado_id) 
WHERE documento_afectado_id IS NOT NULL;

-- PASO 3: Agregar comentarios
-- ----------------------------------------------------------------------------

COMMENT ON COLUMN app_dte.es_nota_credito IS 'Indica si este DTE es una Nota de Crédito';
COMMENT ON COLUMN app_dte.documento_afectado_id IS 'DTE original que esta NC está afectando/corrigiendo';
COMMENT ON COLUMN app_dte.motivo_nc IS 'Motivo o razón de la Nota de Crédito';

-- PASO 4: Verificación
-- ----------------------------------------------------------------------------

-- Ver estructura actualizada
SELECT 
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_name = 'app_dte'
  AND column_name IN ('es_nota_credito', 'documento_afectado_id', 'motivo_nc')
ORDER BY ordinal_position;

-- ============================================================================
-- CONSULTAS ÚTILES DESPUÉS DE LA MIGRACIÓN
-- ============================================================================

-- Ver todas las Notas de Crédito generadas
SELECT 
    nc.id,
    nc.numero_documento AS numero_nc,
    nc.fecha_emision,
    nc.motivo_nc,
    original.numero_documento AS dte_original,
    nc.monto_con_iva,
    nc.emisor_id,
    nc.receptor_id
FROM app_dte nc
LEFT JOIN app_dte original ON nc.documento_afectado_id = original.id
WHERE nc.es_nota_credito = TRUE
ORDER BY nc.fecha_emision DESC;

-- Ver DTEs con sus NCs relacionadas
SELECT 
    d.numero_documento AS dte_original,
    d.tipo_documento,
    d.fecha_emision,
    d.monto_con_iva AS monto_original,
    COUNT(nc.id) AS cantidad_nc,
    COALESCE(SUM(nc.monto_con_iva), 0) AS total_nc
FROM app_dte d
LEFT JOIN app_dte nc ON nc.documento_afectado_id = d.id AND nc.es_nota_credito = TRUE
WHERE d.tipo_transaccion = 'TRASPASO'
  AND d.estado_dte IN ('RECEPCIONADO_PARCIAL', 'EN_REGULARIZACION')
GROUP BY d.id
HAVING COUNT(nc.id) > 0
ORDER BY d.fecha_emision DESC;

-- Ver productos que generaron NC
SELECT 
    pr.id,
    d.numero_documento AS dte_original,
    nc.numero_documento AS nota_credito,
    pt.sku,
    pr.cantidad_esperada,
    pr.stockArribado AS cantidad_recibida,
    pr.cantidad_faltante,
    pr.estado,
    pr.observaciones
FROM app_productos_recepcionados pr
INNER JOIN app_dte d ON pr.dte_id = d.id
LEFT JOIN app_dte nc ON nc.documento_afectado_id = d.id AND nc.es_nota_credito = TRUE
LEFT JOIN app_producto_talla pt ON pr.producto_talla_id = pt.id
WHERE pr.estado IN ('RECEPCIONADO_PARCIAL', 'RECEPCIONADO_DANADO', 'FALTANTE')
  AND nc.id IS NOT NULL
ORDER BY pr.fecha_recepcion DESC;

-- ============================================================================
-- ROLLBACK (si es necesario)
-- ============================================================================
/*
ALTER TABLE app_dte DROP CONSTRAINT IF EXISTS fk_dte_documento_afectado;
ALTER TABLE app_dte DROP COLUMN IF EXISTS es_nota_credito;
ALTER TABLE app_dte DROP COLUMN IF EXISTS documento_afectado_id;
ALTER TABLE app_dte DROP COLUMN IF EXISTS motivo_nc;
DROP INDEX IF EXISTS idx_dte_es_nota_credito;
DROP INDEX IF EXISTS idx_dte_documento_afectado;
*/

-- ============================================================================
-- NOTAS IMPORTANTES
-- ============================================================================
-- 
-- 1. Esta migración agrega soporte para Notas de Crédito automáticas
-- 2. El sistema detecta automáticamente si emisor != receptor
-- 3. Si son empresas diferentes, genera NC automática
-- 4. Si es misma empresa, solo hace ajuste interno
-- 5. Compatible con normativa SII
-- 
-- PRÓXIMO PASO:
-- Ejecutar este SQL y reiniciar el servidor Django
-- 
-- ============================================================================

