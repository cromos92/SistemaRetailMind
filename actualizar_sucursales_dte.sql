-- ============================================================================
-- SCRIPT PARA ACTUALIZAR SUCURSALES EN DTEs
-- Ejecutar en PostgreSQL
-- ============================================================================

-- Mapeo de sucursales:
-- EDEL = 1, PAO1 = 2, PAO2 = 3, PAO3 = 4, PAO4 = 5
-- NICK1 = 6, NICK2 = 7, NICK3 = 8, GILD = 9, PA00 = 10
-- EDEL FALLADOS = 11, IMP = 12

-- ============================================================================
-- VERIFICACIÓN INICIAL
-- ============================================================================

-- Ver estado actual
SELECT 'DTEs sin sucursal:' as info, COUNT(*) as cantidad FROM app_dte WHERE sucursal_id IS NULL;
SELECT 'DTEs con sucursal:' as info, COUNT(*) as cantidad FROM app_dte WHERE sucursal_id IS NOT NULL;

-- Ver sucursales disponibles
SELECT id, alias, empresa_id FROM app_sucursal ORDER BY id;

-- ============================================================================
-- OPCIÓN 1: ACTUALIZAR DONDE EL EMISOR TIENE UNA SOLA SUCURSAL
-- (Esto es seguro y automático)
-- ============================================================================

UPDATE app_dte d
SET sucursal_id = (
    SELECT s.id 
    FROM app_sucursal s 
    WHERE s.empresa_id = d.emisor_id 
    LIMIT 1
)
WHERE sucursal_id IS NULL
AND (
    SELECT COUNT(*) 
    FROM app_sucursal s 
    WHERE s.empresa_id = d.emisor_id
) = 1;

-- ============================================================================
-- OPCIÓN 2: ASIGNAR PRIMERA SUCURSAL DE LA EMPRESA (si hay múltiples)
-- CUIDADO: Esto asigna la primera sucursal si hay más de una
-- ============================================================================

UPDATE app_dte d
SET sucursal_id = (
    SELECT s.id 
    FROM app_sucursal s 
    WHERE s.empresa_id = d.emisor_id 
    ORDER BY s.id
    LIMIT 1
)
WHERE sucursal_id IS NULL
AND d.emisor_id IS NOT NULL;

-- ============================================================================
-- VERIFICACIÓN FINAL
-- ============================================================================

SELECT 'DTEs sin sucursal después:' as info, COUNT(*) as cantidad FROM app_dte WHERE sucursal_id IS NULL;
SELECT 'DTEs con sucursal después:' as info, COUNT(*) as cantidad FROM app_dte WHERE sucursal_id IS NOT NULL;

-- Ver distribución por sucursal
SELECT 
    s.alias as sucursal,
    COUNT(d.id) as cantidad_dtes
FROM app_dte d
JOIN app_sucursal s ON d.sucursal_id = s.id
GROUP BY s.alias
ORDER BY cantidad_dtes DESC;
