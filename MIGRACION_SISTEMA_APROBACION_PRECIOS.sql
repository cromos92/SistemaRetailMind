-- ===============================================
-- MIGRACIÓN: Sistema de Aprobación de Cambios de Precios
-- Fecha: Noviembre 2025
-- Descripción: Agrega tablas para workflow de aprobación de cambios de precios
-- ===============================================

-- Tabla: app_cambiopreciopendiente
CREATE TABLE IF NOT EXISTS app_cambiopreciopendiente (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_talla_id INTEGER NOT NULL,
    sucursal_id INTEGER NOT NULL,
    precio_anterior INTEGER NOT NULL,
    precio_nuevo INTEGER NOT NULL,
    diferencia INTEGER NOT NULL,
    porcentaje_cambio DECIMAL(10, 2) NOT NULL,
    tipo_cambio VARCHAR(20) NOT NULL DEFAULT 'INDIVIDUAL',
    estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    motivo TEXT,
    recomendacion_sistema JSON,
    creado_por_id INTEGER,
    revisado_por_id INTEGER,
    aprobado_por_id INTEGER,
    fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_revision DATETIME,
    fecha_aprobacion DATETIME,
    fecha_aplicacion DATETIME,
    fecha_vencimiento DATETIME,
    observaciones_revision TEXT,
    observaciones_aprobacion TEXT,
    notificado BOOLEAN NOT NULL DEFAULT 0,
    prioridad VARCHAR(10) NOT NULL DEFAULT 'MEDIA',
    FOREIGN KEY (producto_talla_id) REFERENCES app_producto_talla (id),
    FOREIGN KEY (sucursal_id) REFERENCES app_sucursal (id),
    FOREIGN KEY (creado_por_id) REFERENCES auth_user (id),
    FOREIGN KEY (revisado_por_id) REFERENCES auth_user (id),
    FOREIGN KEY (aprobado_por_id) REFERENCES auth_user (id)
);

-- Índices para optimizar búsquedas
CREATE INDEX IF NOT EXISTS idx_cambio_precio_estado_sucursal 
    ON app_cambiopreciopendiente (estado, sucursal_id);
CREATE INDEX IF NOT EXISTS idx_cambio_precio_fecha_creacion 
    ON app_cambiopreciopendiente (fecha_creacion DESC);
CREATE INDEX IF NOT EXISTS idx_cambio_precio_producto 
    ON app_cambiopreciopendiente (producto_talla_id);

-- Tabla: app_notificacioncambioprecio
CREATE TABLE IF NOT EXISTS app_notificacioncambioprecio (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cambio_precio_id INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL,
    tipo VARCHAR(20) NOT NULL,
    mensaje TEXT NOT NULL,
    leida BOOLEAN NOT NULL DEFAULT 0,
    fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_lectura DATETIME,
    FOREIGN KEY (cambio_precio_id) REFERENCES app_cambiopreciopendiente (id) ON DELETE CASCADE,
    FOREIGN KEY (usuario_id) REFERENCES auth_user (id) ON DELETE CASCADE
);

-- Índices para notificaciones
CREATE INDEX IF NOT EXISTS idx_notif_usuario_leida 
    ON app_notificacioncambioprecio (usuario_id, leida);
CREATE INDEX IF NOT EXISTS idx_notif_fecha_creacion 
    ON app_notificacioncambioprecio (fecha_creacion DESC);

-- ===============================================
-- Datos de ejemplo (opcional - comentado)
-- ===============================================

-- Descomentar para insertar datos de prueba:
/*
INSERT INTO app_cambiopreciopendiente 
(producto_talla_id, sucursal_id, precio_anterior, precio_nuevo, diferencia, porcentaje_cambio, 
 tipo_cambio, estado, motivo, prioridad, fecha_creacion)
VALUES 
(1, 1, 10000, 8500, -1500, -15.00, 'RECOMENDACION', 'PENDIENTE', 
 'Inventario antiguo - Más de 1 año sin ventas', 'ALTA', CURRENT_TIMESTAMP);
*/

-- ===============================================
-- Verificación
-- ===============================================

SELECT 'Migración completada exitosamente' as mensaje;
SELECT COUNT(*) as total_cambios FROM app_cambiopreciopendiente;
SELECT COUNT(*) as total_notificaciones FROM app_notificacioncambioprecio;

