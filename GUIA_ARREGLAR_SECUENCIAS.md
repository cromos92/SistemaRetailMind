# 🗄️ GUÍA: Arreglar Secuencias de PostgreSQL en Producción

## 📋 Problema

Después de migrar tu base de datos local a producción, aparecen errores de secuencias como:

```
duplicate key value violates unique constraint
Key (id)=(123) already exists
```

## ✅ Solución Creada

He creado **2 scripts** para resolver esto automáticamente:

---

## 🔧 OPCIÓN 1: SQL Script (Recomendado para producción)

### Archivo: `fix_all_sequences.sql`

Este script:
- ✅ Detecta **TODAS** las secuencias automáticamente
- ✅ Corrige solo las que necesitan ajuste
- ✅ Genera reporte detallado
- ✅ Maneja errores sin interrumpir
- ✅ Muestra resumen al final

### Cómo usar:

#### **Método 1: En tu gestor de DB (pgAdmin, DBeaver, etc.)**

1. Abre `fix_all_sequences.sql`
2. Copia todo el contenido
3. Pégalo en una nueva consulta en tu gestor
4. Ejecuta
5. Revisa el reporte

#### **Método 2: Desde terminal**

```bash
# Conectarte a tu base de datos de producción
psql -U tu_usuario -d nombre_database -f fix_all_sequences.sql

# O si usas Railway/Render
psql $DATABASE_URL -f fix_all_sequences.sql
```

---

## 🐍 OPCIÓN 2: Script Python/Django

### Archivo: `fix_sequences_django.py`

Este script:
- ✅ Usa la conexión de Django automáticamente
- ✅ No necesitas credenciales manuales
- ✅ Salida formateada en colores
- ✅ Funciona local y en producción

### Cómo usar:

```bash
# En local o en el servidor de producción
python fix_sequences_django.py
```

---

## 📊 Qué hace el script

### Paso 1: Detecta todas las secuencias

```sql
SELECT 
    s.sequencename,
    c.relname as tablename,
    a.attname as columnname
FROM pg_sequences s
WHERE s.schemaname = 'public'
```

### Paso 2: Para cada secuencia

1. Obtiene el **valor máximo** de la columna: `MAX(id)`
2. Obtiene el **valor actual** de la secuencia: `last_value`
3. Compara:
   - Si `last_value <= max_id` → **CORRIGE** (`setval(max_id + 1)`)
   - Si `last_value > max_id` → **SKIP** (ya está correcto)

### Paso 3: Genera reporte

```
========================================
RESUMEN DE CORRECCIÓN
========================================
Total de secuencias procesadas: 72
Secuencias corregidas: 15
Secuencias con errores: 0
Secuencias ya correctas: 57
```

---

## 🎯 Tablas que se corrigen automáticamente

El script detecta y corrige **TODAS** tus tablas, incluyendo:

```
✓ app_empresa_id_seq
✓ app_sucursal_id_seq
✓ app_vendedor_id_seq
✓ app_producto_id_seq
✓ app_producttalla_id_seq
✓ app_ticket_id_seq
✓ app_dte_id_seq
✓ app_compras_id_seq
✓ app_traspaso_id_seq
✓ app_ajusteinventario_id_seq
✓ app_cotizacion_id_seq
✓ app_arqueocaja_id_seq
✓ ... y 60+ más
```

**Total**: Las 72+ tablas de tu aplicación.

---

## 📝 Ejemplo de Salida

```sql
========================================
INICIANDO CORRECCIÓN DE SECUENCIAS
========================================

[OK] app_empresa_id_seq (app_empresa.id)
     Anterior: 1 -> Nuevo: 3 (Max ID: 2)

[SKIP] app_sucursal_id_seq (app_sucursal.id)
       Ya está correcto (Current: 10, Max ID: 5)

[OK] app_producto_id_seq (app_producto.id)
     Anterior: 50 -> Nuevo: 156 (Max ID: 155)

... (continúa con todas las tablas)

========================================
RESUMEN DE CORRECCIÓN
========================================
Total de secuencias procesadas: 72
Secuencias corregidas: 15
Secuencias con errores: 0
Secuencias ya correctas: 57

========================================
VERIFICACIÓN FINAL
========================================
ÉXITO: Todas las secuencias se procesaron correctamente.
```

---

## 🚀 Proceso Completo en Producción

### 1. **Backup de la base de datos** (Importante!)

```bash
# Railway
railway run pg_dump > backup_antes_de_fix.sql

# Heroku
heroku pg:backups:capture --app tu-app

# O manualmente
pg_dump -U usuario -d database > backup.sql
```

### 2. **Ejecutar el script**

```bash
# Opción A: SQL
psql $DATABASE_URL -f fix_all_sequences.sql

# Opción B: Python
python fix_sequences_django.py
```

### 3. **Verificar**

Prueba crear un nuevo registro en tu aplicación:
- Crear un nuevo producto
- Crear un nuevo ticket
- Crear una nueva empresa

Si todo funciona sin errores → ✅ Listo!

---

## 🧪 Probar en Local Primero (Recomendado)

```bash
# 1. Backup local
pg_dump -U postgres -d newDBNexo > backup_local.sql

# 2. Ejecutar script
python fix_sequences_django.py

# 3. Probar crear registros en tu app local
```

---

## 📋 Checklist

- [ ] Hacer backup de la base de datos
- [ ] Elegir método (SQL o Python)
- [ ] Ejecutar script
- [ ] Revisar reporte de resultados
- [ ] Probar crear nuevos registros
- [ ] Verificar que no hay errores

---

## 🔍 Verificar Manualmente (Opcional)

Si quieres ver el estado de las secuencias:

```sql
-- Ver todas las secuencias y sus valores
SELECT 
    s.sequencename as "Secuencia",
    c.relname as "Tabla",
    a.attname as "Columna",
    s.last_value as "Último Valor"
FROM pg_sequences s
JOIN pg_class seq_class ON seq_class.relname = s.sequencename
JOIN pg_depend d ON d.objid = seq_class.oid
JOIN pg_class c ON c.oid = d.refobjid
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
WHERE s.schemaname = 'public'
ORDER BY c.relname;
```

---

## 🆘 Troubleshooting

### Error: "permission denied for table"
```bash
Causa: Sin permisos
Solución: Ejecuta como superusuario o con rol adecuado
```

### Error: "sequence does not exist"
```bash
Causa: Secuencia no encontrada
Solución: El script lo maneja automáticamente, solo genera warning
```

### Algunas secuencias no se corrigen
```bash
Causa: Ya están correctas o hay registros eliminados
Solución: Revisa el reporte, el script las marca como [SKIP]
```

---

## 💡 ¿Por qué pasa esto?

Cuando migras datos con `INSERT` directo (sin usar Django ORM), las secuencias no se actualizan automáticamente:

```sql
-- ❌ Esto NO actualiza la secuencia
INSERT INTO app_producto (id, nombre, precio) VALUES (100, 'Producto', 1000);

-- ✅ Esto SÍ actualiza la secuencia
INSERT INTO app_producto (nombre, precio) VALUES ('Producto', 1000);
```

Por eso después de migrar necesitas resetear las secuencias.

---

## 🎯 Resumen

1. **Script SQL**: `fix_all_sequences.sql` - Cópialo y ejecuta en tu gestor DB
2. **Script Python**: `fix_sequences_django.py` - Ejecútalo desde terminal
3. **Corrige**: Todas las 72+ tablas automáticamente
4. **Reporte**: Muestra qué se corrigió y qué estaba OK

**Elige el método que prefieras, ambos hacen lo mismo.**

---

## 📞 Siguiente Paso

```bash
# 1. Haz backup
pg_dump > backup.sql

# 2. Ejecuta el script
psql -f fix_all_sequences.sql

# 3. Prueba tu app
# ¡Listo!
```

---

**¿Dudas sobre las secuencias? Ejecuta cualquiera de los scripts y revisa el reporte. Te mostrará exactamente qué se corrigió.**
