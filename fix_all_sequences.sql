-- ==============================================================================
-- SCRIPT DE CORRECCIÓN DE SECUENCIAS POSTGRESQL
-- ==============================================================================
-- Descripción: Resetea todas las secuencias de las tablas para evitar errores
--              de duplicate key después de migrar datos.
-- 
-- Uso: psql -U usuario -d nombre_base_datos -f fix_all_sequences.sql
-- 
-- Fecha: 2026-01-26
-- ==============================================================================

-- Crear tabla temporal para log
CREATE TEMP TABLE sequence_fix_log (
    tabla TEXT,
    columna TEXT,
    secuencia TEXT,
    valor_anterior BIGINT,
    valor_nuevo BIGINT,
    estado TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
);

-- ==============================================================================
-- PASO 1: FIX AUTOMÁTICO DE TODAS LAS SECUENCIAS
-- ==============================================================================

DO $$
DECLARE
    r RECORD;
    v_max_id BIGINT;
    v_current_val BIGINT;
    v_new_val BIGINT;
    v_count INTEGER := 0;
    v_fixed INTEGER := 0;
    v_errors INTEGER := 0;
BEGIN
    RAISE NOTICE '========================================';
    RAISE NOTICE 'INICIANDO CORRECCIÓN DE SECUENCIAS';
    RAISE NOTICE '========================================';
    RAISE NOTICE '';
    
    -- Iterar sobre todas las secuencias del schema público
    FOR r IN (
        SELECT 
            s.sequencename,
            s.schemaname,
            c.relname as tablename,
            a.attname as columnname
        FROM pg_sequences s
        JOIN pg_class seq_class ON seq_class.relname = s.sequencename
        JOIN pg_depend d ON d.objid = seq_class.oid
        JOIN pg_class c ON c.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
        WHERE s.schemaname = 'public'
        ORDER BY c.relname, a.attname
    ) LOOP
        v_count := v_count + 1;
        
        BEGIN
            -- Obtener el valor máximo actual de la columna
            EXECUTE format('SELECT COALESCE(MAX(%I), 0) FROM %I.%I',
                r.columnname, r.schemaname, r.tablename)
            INTO v_max_id;
            
            -- Obtener el valor actual de la secuencia
            EXECUTE format('SELECT last_value FROM %I.%I',
                r.schemaname, r.sequencename)
            INTO v_current_val;
            
            -- Calcular el nuevo valor (máximo + 1)
            v_new_val := v_max_id + 1;
            
            -- Solo ajustar si el valor actual es menor o igual al máximo
            IF v_current_val <= v_max_id THEN
                -- Ajustar la secuencia
                EXECUTE format('SELECT setval(''%I.%I'', %s, false)',
                    r.schemaname, r.sequencename, v_new_val);
                
                RAISE NOTICE '[OK] % (%.%) - Anterior: %, Nuevo: %, Max ID: %',
                    r.sequencename, r.tablename, r.columnname,
                    v_current_val, v_new_val, v_max_id;
                
                -- Registrar en log
                INSERT INTO sequence_fix_log (tabla, columna, secuencia, valor_anterior, valor_nuevo, estado)
                VALUES (r.tablename, r.columnname, r.sequencename, v_current_val, v_new_val, 'CORREGIDO');
                
                v_fixed := v_fixed + 1;
            ELSE
                RAISE NOTICE '[SKIP] % (%.%) - Ya está correcto (Current: %, Max ID: %)',
                    r.sequencename, r.tablename, r.columnname,
                    v_current_val, v_max_id;
                
                -- Registrar en log
                INSERT INTO sequence_fix_log (tabla, columna, secuencia, valor_anterior, valor_nuevo, estado)
                VALUES (r.tablename, r.columnname, r.sequencename, v_current_val, v_current_val, 'OK');
            END IF;
            
        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors + 1;
            RAISE NOTICE '[ERROR] % (%.%) - Error: %',
                r.sequencename, r.tablename, r.columnname, SQLERRM;
            
            -- Registrar error en log
            INSERT INTO sequence_fix_log (tabla, columna, secuencia, valor_anterior, valor_nuevo, estado)
            VALUES (r.tablename, r.columnname, r.sequencename, NULL, NULL, 'ERROR: ' || SQLERRM);
        END;
    END LOOP;
    
    RAISE NOTICE '';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'RESUMEN DE CORRECCIÓN';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Total de secuencias procesadas: %', v_count;
    RAISE NOTICE 'Secuencias corregidas: %', v_fixed;
    RAISE NOTICE 'Secuencias con errores: %', v_errors;
    RAISE NOTICE 'Secuencias ya correctas: %', v_count - v_fixed - v_errors;
    RAISE NOTICE '';
END $$;

-- ==============================================================================
-- PASO 2: MOSTRAR REPORTE DETALLADO
-- ==============================================================================

SELECT 
    tabla,
    columna,
    secuencia,
    valor_anterior as "Valor Anterior",
    valor_nuevo as "Valor Nuevo",
    estado as "Estado"
FROM sequence_fix_log
ORDER BY 
    CASE 
        WHEN estado = 'ERROR' THEN 1
        WHEN estado = 'CORREGIDO' THEN 2
        ELSE 3
    END,
    tabla;

-- ==============================================================================
-- PASO 3: VERIFICACIÓN FINAL
-- ==============================================================================

DO $$
DECLARE
    v_problemas INTEGER := 0;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'VERIFICACIÓN FINAL';
    RAISE NOTICE '========================================';
    
    SELECT COUNT(*) INTO v_problemas
    FROM sequence_fix_log
    WHERE estado LIKE 'ERROR%';
    
    IF v_problemas > 0 THEN
        RAISE NOTICE 'ADVERTENCIA: Se encontraron % secuencias con errores.', v_problemas;
        RAISE NOTICE 'Revise el reporte anterior para más detalles.';
    ELSE
        RAISE NOTICE 'ÉXITO: Todas las secuencias se procesaron correctamente.';
    END IF;
    RAISE NOTICE '';
END $$;

-- ==============================================================================
-- CONSULTA ADICIONAL: Ver todas las secuencias y sus valores actuales
-- ==============================================================================
-- Descomente la siguiente consulta si desea ver el estado actual de todas las secuencias:

/*
SELECT 
    s.sequencename as "Secuencia",
    c.relname as "Tabla",
    a.attname as "Columna",
    s.last_value as "Último Valor",
    (SELECT MAX(a.attname::text)::bigint FROM c.relname) as "Max ID en Tabla"
FROM pg_sequences s
JOIN pg_class seq_class ON seq_class.relname = s.sequencename
JOIN pg_depend d ON d.objid = seq_class.oid
JOIN pg_class c ON c.oid = d.refobjid
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
WHERE s.schemaname = 'public'
ORDER BY c.relname, a.attname;
*/
