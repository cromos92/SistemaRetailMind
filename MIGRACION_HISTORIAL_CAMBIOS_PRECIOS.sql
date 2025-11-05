-- ===============================================
-- MIGRACIÓN: Historial de Cambios de Precios
-- Fecha: Noviembre 2025
-- Descripción: Registro de auditoría de todos los cambios de precio
-- ===============================================

-- Tabla: app_historialcambioprecio
CREATE TABLE IF NOT EXISTS app_historialcambioprecio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL,
    precio_anterior INTEGER NOT NULL,
    precio_nuevo INTEGER NOT NULL,
    diferencia INTEGER NOT NULL,
    porcentaje_cambio DECIMAL(10, 2) NOT NULL,
    motivo TEXT,
    tipo_cambio VARCHAR(50) NOT NULL DEFAULT 'MANUAL',
    usuario_id INTEGER,
    fecha_cambio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(39),
    tallas_afectadas INTEGER NOT NULL DEFAULT 0,
    lotes_afectados INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (producto_id) REFERENCES app_producto (id) ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES auth_user (id) ON DELETE SET NULL
);

-- Índices para optimizar búsquedas
CREATE INDEX IF NOT EXISTS idx_historial_precio_producto_fecha 
    ON app_historialcambioprecio (producto_id, fecha_cambio DESC);
CREATE INDEX IF NOT EXISTS idx_historial_precio_usuario_fecha 
    ON app_historialcambioprecio (usuario_id, fecha_cambio DESC);
CREATE INDEX IF NOT EXISTS idx_historial_precio_tipo 
    ON app_historialcambioprecio (tipo_cambio);
CREATE INDEX IF NOT EXISTS idx_historial_precio_fecha 
    ON app_historialcambioprecio (fecha_cambio DESC);

-- ===============================================
-- Verificación
-- ===============================================

SELECT 'Migración de historial completada exitosamente' as mensaje;
SELECT COUNT(*) as total_registros FROM app_historialcambioprecio;

