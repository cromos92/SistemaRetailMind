-- ============================================================================
-- CONSULTAS PARA VERIFICAR DTEs SIN BODEGA Y ASIGNACIÓN POR RUT_EMISOR
-- ============================================================================

-- 1. Ver DTEs con bodega_inicio = "0" o vacío/NULL
SELECT 
    bodega_inicio,
    COUNT(*) as total
FROM dte 
WHERE bodega_inicio IS NULL 
   OR bodega_inicio = '' 
   OR bodega_inicio = '0'
GROUP BY bodega_inicio;

-- 2. Ver DTEs sin bodega, agrupados por RUT_EMISOR
-- Esto muestra a qué empresa pertenecen los DTEs sin sucursal
SELECT 
    rut_emisor,
    bodega_inicio,
    COUNT(*) as total
FROM dte 
WHERE bodega_inicio IS NULL 
   OR bodega_inicio = '' 
   OR bodega_inicio = '0'
GROUP BY rut_emisor, bodega_inicio
ORDER BY total DESC;

-- 3. Ver ejemplos de DTEs sin bodega (primeros 20)
SELECT 
    ID,
    n_documento,
    tipo_documento,
    rut_emisor,
    rut_cliente,
    bodega_inicio,
    bodega_destino,
    monto_total,
    fecha_emision
FROM dte 
WHERE bodega_inicio IS NULL 
   OR bodega_inicio = '' 
   OR bodega_inicio = '0'
LIMIT 20;

-- 4. MAPEO PROPUESTO: RUT_EMISOR -> SUCURSAL POR DEFECTO
-- Esta consulta muestra cómo se asignaría cada DTE sin bodega
SELECT 
    d.rut_emisor,
    d.bodega_inicio,
    COUNT(*) as total_dtes,
    CASE 
        WHEN d.rut_emisor = '78503140-7' THEN 'PAO1 (Maipu 668)'
        WHEN d.rut_emisor = '76104936-4' THEN 'NICK1 (Matta 2479)'
        WHEN d.rut_emisor = '7397811-4' THEN 'GILD (Maipu 676)'
        ELSE 'SIN MAPEO'
    END as sucursal_asignada
FROM dte d
WHERE d.bodega_inicio IS NULL 
   OR d.bodega_inicio = '' 
   OR d.bodega_inicio = '0'
GROUP BY d.rut_emisor, d.bodega_inicio
ORDER BY total_dtes DESC;

-- 5. Verificar que TODOS los rut_emisor tienen mapeo
-- (no debería haber "SIN MAPEO")
SELECT DISTINCT rut_emisor
FROM dte 
WHERE (bodega_inicio IS NULL OR bodega_inicio = '' OR bodega_inicio = '0')
AND rut_emisor NOT IN ('78503140-7', '76104936-4', '7397811-4');

-- 6. Ver distribución completa de bodega_inicio vs rut_emisor
-- Para entender la relación entre empresa y sucursales
SELECT 
    rut_emisor,
    bodega_inicio,
    COUNT(*) as total
FROM dte
GROUP BY rut_emisor, bodega_inicio
ORDER BY rut_emisor, total DESC;

-- 7. RESUMEN FINAL: Total de DTEs que se podrían corregir
SELECT 
    COUNT(*) as total_sin_bodega,
    SUM(CASE WHEN rut_emisor IN ('78503140-7', '76104936-4', '7397811-4') THEN 1 ELSE 0 END) as pueden_corregirse,
    SUM(CASE WHEN rut_emisor NOT IN ('78503140-7', '76104936-4', '7397811-4') THEN 1 ELSE 0 END) as sin_mapeo
FROM dte 
WHERE bodega_inicio IS NULL 
   OR bodega_inicio = '' 
   OR bodega_inicio = '0';
