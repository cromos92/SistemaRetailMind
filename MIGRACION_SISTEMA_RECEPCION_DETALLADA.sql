-- ============================================================================
-- MIGRACIÓN: Sistema de Recepción Detallada para DTEs
-- ============================================================================
-- Fecha: 2025-10-27
-- Descripción: Expande modelo Productos_Recepcionados para soportar 
--              recepciones parciales con control de problemas
-- ============================================================================

-- PASO 1: Aumentar max_length de estado_dte para nuevos estados
-- ----------------------------------------------------------------------------
ALTER TABLE app_dte 
ALTER COLUMN estado_dte TYPE VARCHAR(30);

COMMENT ON COLUMN app_dte.estado_dte IS 'Estados: EMITIDO, ACEPTADO, RECEPCIONADO_COMPLETO, RECEPCIONADO_PARCIAL, EN_REGULARIZACION, RECHAZADO, ANULADO';

-- PASO 2: Agregar nuevos campos a Productos_Recepcionados
-- ----------------------------------------------------------------------------

-- Hacer compra_producto_talla nullable (para traspasos)
ALTER TABLE app_productos_recepcionados 
ALTER COLUMN compra_producto_talla_id DROP NOT NULL;

-- Agregar campos para traspasos internos
ALTER TABLE app_productos_recepcionados 
ADD COLUMN IF NOT EXISTS dte_producto_id INTEGER NULL,
ADD CONSTRAINT fk_productos_recepcionados_dte_producto 
    FOREIGN KEY (dte_producto_id) REFERENCES app_dte_productos(id) ON DELETE CASCADE;

-- Agregar campos de cantidad
ALTER TABLE app_productos_recepcionados
ADD COLUMN IF NOT EXISTS cantidad_esperada INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS cantidad_danada INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS cantidad_faltante INTEGER DEFAULT 0;

-- Agregar estado de recepción
ALTER TABLE app_productos_recepcionados
ADD COLUMN IF NOT EXISTS estado VARCHAR(30) DEFAULT 'RECEPCIONADO_OK';

-- Agregar observaciones
ALTER TABLE app_productos_recepcionados
ADD COLUMN IF NOT EXISTS observaciones TEXT NULL;

-- Agregar campos de auditoría
ALTER TABLE app_productos_recepcionados
ADD COLUMN IF NOT EXISTS fecha_recepcion TIMESTAMP NULL,
ADD COLUMN IF NOT EXISTS recepcionado_por VARCHAR(100) NULL,
ADD COLUMN IF NOT EXISTS fecha_regularizacion TIMESTAMP NULL,
ADD COLUMN IF NOT EXISTS regularizado_por VARCHAR(100) NULL;

-- PASO 3: Crear índices para optimizar consultas
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_productos_recepcionados_dte_estado 
ON app_productos_recepcionados(dte_id, estado);

CREATE INDEX IF NOT EXISTS idx_productos_recepcionados_estado 
ON app_productos_recepcionados(estado);

CREATE INDEX IF NOT EXISTS idx_productos_recepcionados_fecha 
ON app_productos_recepcionados(fecha);

CREATE INDEX IF NOT EXISTS idx_productos_recepcionados_dte_producto 
ON app_productos_recepcionados(dte_producto_id);

-- PASO 4: Actualizar registros existentes con valores por defecto
-- ----------------------------------------------------------------------------

-- Para recepciones existentes de compras, marcar como RECEPCIONADO_OK
UPDATE app_productos_recepcionados
SET estado = 'RECEPCIONADO_OK',
    cantidad_esperada = stockArribado
WHERE compra_producto_talla_id IS NOT NULL
  AND estado IS NULL;

-- PASO 5: Agregar comentarios a las columnas
-- ----------------------------------------------------------------------------
COMMENT ON COLUMN app_productos_recepcionados.compra_producto_talla_id IS 'Para recepciones de compras (legacy)';
COMMENT ON COLUMN app_productos_recepcionados.dte_id IS 'Para recepciones de traspasos internos';
COMMENT ON COLUMN app_productos_recepcionados.dte_producto_id IS 'Producto específico del DTE de traspaso';
COMMENT ON COLUMN app_productos_recepcionados.stockArribado IS 'Cantidad recepcionada (nombre legacy)';
COMMENT ON COLUMN app_productos_recepcionados.cantidad_esperada IS 'Cantidad original esperada';
COMMENT ON COLUMN app_productos_recepcionados.cantidad_danada IS 'Cantidad recibida con daños';
COMMENT ON COLUMN app_productos_recepcionados.cantidad_faltante IS 'Cantidad que no llegó';
COMMENT ON COLUMN app_productos_recepcionados.estado IS 'Estado: PENDIENTE, RECEPCIONADO_OK, RECEPCIONADO_PARCIAL, RECEPCIONADO_DANADO, FALTANTE, EN_REGULARIZACION, REGULARIZADO';
COMMENT ON COLUMN app_productos_recepcionados.observaciones IS 'Observaciones sobre problemas o incidencias';

-- PASO 6: Verificación
-- ----------------------------------------------------------------------------
-- Verificar que las columnas se agregaron correctamente
SELECT 
    column_name,
    data_type,
    character_maximum_length,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'app_productos_recepcionados'
ORDER BY ordinal_position;

-- Verificar estado_dte actualizado
SELECT 
    column_name,
    data_type,
    character_maximum_length
FROM information_schema.columns
WHERE table_name = 'app_dte'
  AND column_name = 'estado_dte';

-- ============================================================================
-- ROLLBACK (si algo sale mal)
-- ============================================================================
/*
-- Revertir cambios en Productos_Recepcionados
ALTER TABLE app_productos_recepcionados
DROP COLUMN IF EXISTS dte_producto_id,
DROP COLUMN IF EXISTS cantidad_esperada,
DROP COLUMN IF EXISTS cantidad_danada,
DROP COLUMN IF EXISTS cantidad_faltante,
DROP COLUMN IF EXISTS estado,
DROP COLUMN IF EXISTS observaciones,
DROP COLUMN IF EXISTS fecha_recepcion,
DROP COLUMN IF EXISTS recepcionado_por,
DROP COLUMN IF EXISTS fecha_regularizacion,
DROP COLUMN IF EXISTS regularizado_por;

ALTER TABLE app_productos_recepcionados 
ALTER COLUMN compra_producto_talla_id SET NOT NULL;

-- Revertir cambios en Dte
ALTER TABLE app_dte 
ALTER COLUMN estado_dte TYPE VARCHAR(20);

-- Eliminar índices
DROP INDEX IF EXISTS idx_productos_recepcionados_dte_estado;
DROP INDEX IF EXISTS idx_productos_recepcionados_estado;
DROP INDEX IF EXISTS idx_productos_recepcionados_fecha;
DROP INDEX IF EXISTS idx_productos_recepcionados_dte_producto;
*/

-- ============================================================================
-- NOTAS IMPORTANTES
-- ============================================================================
-- 
-- 1. Esta migración es compatible con datos existentes
-- 2. Las recepciones de compras existentes seguirán funcionando
-- 3. Los nuevos campos permiten recepciones parciales con problemas
-- 4. Los índices mejoran el rendimiento de consultas de recepción
-- 
-- PRÓXIMOS PASOS:
-- 1. Ejecutar este SQL en tu base de datos
-- 2. Actualizar la vista confirmar_recepcion_api()
-- 3. Crear modal mejorado con checkboxes
-- 4. Implementar proceso de 2 pasos
-- 
-- ============================================================================

