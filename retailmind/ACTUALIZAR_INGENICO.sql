-- =====================================================
-- ACTUALIZAR TERMINALES A INGENICO DESK 3500
-- Ejecutar este script en tu base de datos SQLite/PostgreSQL
-- =====================================================

-- Ver estado ACTUAL de los terminales
SELECT 
    id,
    nombre,
    tipo_pos,
    puerto_conexion,
    activo
FROM app_configuracionpos
ORDER BY id;

-- =====================================================
-- ACTUALIZAR TODOS LOS TERMINALES A INGENICO_DESK
-- =====================================================

UPDATE app_configuracionpos 
SET tipo_pos = 'INGENICO_DESK'
WHERE tipo_pos = 'VERIFONE_VX520';

-- O si quieres actualizar TODOS sin importar el tipo actual:
-- UPDATE app_configuracionpos SET tipo_pos = 'INGENICO_DESK';

-- =====================================================
-- VERIFICAR CAMBIOS
-- =====================================================

SELECT 
    id,
    nombre,
    tipo_pos AS 'Tipo Actualizado',
    puerto_conexion,
    activo,
    CASE 
        WHEN tipo_pos = 'INGENICO_DESK' THEN '✅ ACTUALIZADO'
        ELSE '❌ NO ACTUALIZADO'
    END AS Estado
FROM app_configuracionpos
ORDER BY id;

-- =====================================================
-- COMMITS PARA GUARDAR CAMBIOS
-- =====================================================

-- Para SQLite (default en Django):
-- Los cambios se guardan automáticamente

-- Para PostgreSQL:
-- COMMIT;

-- =====================================================
-- INFORMACIÓN ADICIONAL
-- =====================================================

-- Ver todos los tipos de POS disponibles:
-- VERIFONE_VX520 → Verifone VX520
-- INGENICO_3500  → Ingenico 3500
-- INGENICO_DESK  → Ingenico DESK 3500/5000
-- OTRO           → Otro tipo

-- =====================================================
-- ROLLBACK si algo sale mal:
-- =====================================================

-- Para volver a Verifone:
-- UPDATE app_configuracionpos SET tipo_pos = 'VERIFONE_VX520';

-- =====================================================
-- FIN DEL SCRIPT
-- =====================================================

-- Para ejecutar este script:
-- 1. Abre DB Browser for SQLite (si usas SQLite)
-- 2. Abre tu base de datos: db.sqlite3
-- 3. Ve a la pestaña "Ejecutar SQL"
-- 4. Pega este script
-- 5. Ejecuta (F5)
-- 6. Verifica los resultados
-- 7. Refresca la página web

