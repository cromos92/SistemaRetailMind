# ⚡ Comandos de Migración Ultra-Rápida

## 🚀 TODO EN UNO - COPIAR Y PEGAR (IMPORTACIÓN COMPLETA)

```powershell
cd C:\DjangoProyects\retailmind\SistemaRetailMind
..\venv\Scripts\python.exe manage.py clean_migration_data --confirm && ..\venv\Scripts\python.exe manage.py migrate_from_laravel --batch-size 2000
```

## 🎯 TODO EN UNO - IMPORTACIÓN PARCIAL (Sin DTEs)

```powershell
cd C:\DjangoProyects\retailmind\SistemaRetailMind
..\venv\Scripts\python.exe manage.py clean_migration_data --confirm && ..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos --batch-size 2000
```

---

## 📋 COMANDOS PASO A PASO

### Paso 1: Detener proceso actual (si está corriendo)
```
Ctrl + C
```

### Paso 2: Navegar al directorio
```powershell
cd C:\DjangoProyects\retailmind\SistemaRetailMind
```

### Paso 3: Limpiar datos (ULTRA RÁPIDO con TRUNCATE)
```powershell
..\venv\Scripts\python.exe manage.py clean_migration_data --confirm
```

### Paso 4: Ejecutar migración optimizada
```powershell
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos
```

### Paso 5: Ver errores (después de que termine)
```powershell
type migration_errors.log
```

---

## 🎯 VARIANTES DE VELOCIDAD

### Para servidores POTENTES (más rápido)
```powershell
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos --batch-size 2000
```

### Para servidores NORMALES (balanceado)
```powershell
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos --batch-size 1000
```

### Para servidores MODESTOS (más lento pero seguro)
```powershell
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos --batch-size 500
```

---

## 🧪 PARA PRUEBAS RÁPIDAS (pocos datos)

```powershell
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos --limit 1000
```

---

## ⏱️ TIEMPOS ESTIMADOS (con optimizaciones)

| Tabla | Registros | Tiempo Estimado |
|-------|-----------|----------------|
| Empresas | 2 | < 1 seg |
| Clientes | 5,000 | ~20-30 seg |
| Sucursales | 8 | < 1 seg |
| Atributos | 500 | ~5 seg |
| Categorías | 12 | < 1 seg |
| Productos | 136,944 | ~5-8 min |
| Producto_Talla | 150,000 | ~3-5 min |
| Movimientos | 585,623 | ~15-20 min |
| **TOTAL** | ~880,000 | **~25-35 minutos** |

---

## 📊 OPTIMIZACIONES APLICADAS

### 1. Limpieza Ultra-Rápida (clean_migration_data)
- ✅ Usa **TRUNCATE** en lugar de DELETE (10-100x más rápido)
- ✅ Desactiva triggers temporalmente
- ✅ Resetea secuencias automáticamente
- ✅ Acepta `--confirm` para no pedir confirmación

**Antes:** ~5-10 minutos para limpiar
**Ahora:** ~5-10 segundos para limpiar

### 2. Migración Optimizada (migrate_from_laravel)
- ✅ **Bulk Create**: Inserta 1000-2000 registros a la vez
- ✅ **Sistema de Caché**: Pre-carga sucursales, categorías, productos
- ✅ **Select Related**: Pre-carga relaciones FK
- ✅ **Pre-verificación**: Evita consultas repetitivas
- ✅ **Logging inteligente**: Solo registra cada N errores
- ✅ **Archivo de errores**: Guarda en migration_errors.log

**Antes:** ~3-4 horas
**Ahora:** ~25-35 minutos

---

## 🔍 VERIFICAR DATOS DESPUÉS

### Ver estadísticas de migración
```powershell
..\venv\Scripts\python.exe manage.py dbshell
```

Luego en PostgreSQL:
```sql
-- Ver conteo de registros
SELECT 'Empresas' as tabla, COUNT(*) as cantidad FROM app_empresa
UNION ALL
SELECT 'Sucursales', COUNT(*) FROM app_sucursal
UNION ALL
SELECT 'Productos', COUNT(*) FROM app_producto
UNION ALL
SELECT 'Producto_Talla', COUNT(*) FROM app_producto_talla
UNION ALL
SELECT 'Movimientos', COUNT(*) FROM app_movimientos_producto;

-- Ver productos duplicados (debe ser 0)
SELECT articulo, COUNT(*) 
FROM app_producto 
GROUP BY articulo, sucursal_id, atributo1_id, atributo2_id 
HAVING COUNT(*) > 1;

-- Ver productos con precio 0
SELECT COUNT(*) as total_precio_cero
FROM app_producto 
WHERE precioventa = 0;
```

---

## 📄 VER LOG DE ERRORES

### Ver todos los errores
```powershell
type migration_errors.log
```

### Contar tipos de errores
```powershell
findstr "ProductoTalla no encontrado" migration_errors.log | find /c /v ""
```

### Buscar error específico
```powershell
findstr "SKU: 12345" migration_errors.log
```

---

## 💡 TIPS IMPORTANTES

### ✅ Hacer ANTES de migrar:
1. Cerrar otros procesos pesados
2. Verificar conexión estable a MySQL
3. Tener al menos 4GB RAM libre
4. Ejecutar en horario de baja demanda

### ✅ Hacer DESPUÉS de migrar:
1. Revisar `migration_errors.log`
2. Verificar conteo de registros en PostgreSQL
3. Actualizar estadísticas de PostgreSQL:
   ```sql
   ANALYZE;
   ```
4. Verificar productos con precio 0 si es necesario

### ❌ NO hacer:
1. NO interrumpir el proceso (está en transacción)
2. NO ejecutar múltiples migraciones simultáneas
3. NO ejecutar sin limpiar datos anteriores

---

## 🆘 TROUBLESHOOTING

### Error: "Out of Memory"
```powershell
# Reducir batch size
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos --batch-size 500
```

### Error: "Too Many Connections"
Verifica límite en PostgreSQL:
```sql
SHOW max_connections;  -- Debe ser > 20
```

### Proceso muy lento
1. Verifica que no haya índices innecesarios
2. Revisa uso de CPU/RAM
3. Asegúrate de tener SSD (no HDD)

### Productos duplicados
```powershell
# Si encuentras duplicados, limpia todo y vuelve a empezar
..\venv\Scripts\python.exe manage.py clean_migration_data --confirm
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos
```

---

## ⚙️ CONFIGURACIÓN POSTGRESQL RECOMENDADA

Edita `postgresql.conf`:

```ini
# Memoria
shared_buffers = 256MB              # 25% de RAM disponible
work_mem = 16MB                     # Para operaciones de ordenamiento
maintenance_work_mem = 128MB        # Para bulk inserts
effective_cache_size = 1GB          # Para query planner

# Checkpoint
checkpoint_completion_target = 0.9  # Distribuir escritura
wal_buffers = 16MB                  # Buffer de WAL

# Autovacuum (desactivar durante migración si quieres)
autovacuum = off                    # Desactivar durante migración
```

**Recuerda reactivar autovacuum después:**
```sql
ALTER SYSTEM SET autovacuum = on;
SELECT pg_reload_conf();
```

---

## 📈 MONITOREAR PROGRESO

Durante la migración verás:

```
[INFO] Pre-cargando cachés...
  ✓ 8 sucursales en caché
  ✓ 12 categorías en caché

[PROGRESO] 10000/585623 (1.7%)
[PROGRESO] 50000/585623 (8.5%)
[PROGRESO] 100000/585623 (17.1%)
[PROGRESO] 200000/585623 (34.1%)
[PROGRESO] 300000/585623 (51.2%)
[PROGRESO] 400000/585623 (68.3%)
[PROGRESO] 500000/585623 (85.4%)
[PROGRESO] 585623/585623 (100.0%)

📊 Total de errores: 145
⚠️  Se encontraron 145 errores
📄 Detalles completos en: migration_errors.log
⏱️  Tiempo total de ejecución: 0:28:43

✅ MIGRACIÓN COMPLETADA EXITOSAMENTE
```

---

## 🎯 RESUMEN RÁPIDO

```powershell
# 1. Detener proceso actual: Ctrl + C

# 2. TODO EN UNO:
cd C:\DjangoProyects\retailmind\SistemaRetailMind
..\venv\Scripts\python.exe manage.py clean_migration_data --confirm
..\venv\Scripts\python.exe manage.py migrate_from_laravel --tables empresas clientes sucursales atributos categorias productos producto_talla movimientos

# 3. Ver errores después:
type migration_errors.log
```

---

**Última actualización:** 2024-11-19  
**Versión:** 3.0 (Ultra-optimizada)  
**Mejora de velocidad:** 6-8x más rápido que versión original

