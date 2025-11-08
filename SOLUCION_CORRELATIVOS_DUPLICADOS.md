# Solución: Correlativos Duplicados

## Error Detectado
```
Error: Error al cargar correlativos: llave duplicada viola restricción de unicidad 
«app_correlativo_sucursal_id_tipo_dte_c673ee07_uniq» 
DETAIL: Ya existe la llave (sucursal_id, tipo_dte)=(2, COMPRA).
```

## Causa
El modelo `Correlativo` tiene una restricción de unicidad en `unique_together = ['sucursal', 'tipo_dte']`, lo que significa que **no puede haber dos registros con la misma sucursal y tipo de DTE**.

En algún momento se crearon registros duplicados en la base de datos, violando esta restricción.

## Soluciones

### Opción 1: Usar el Comando de Gestión de Django (RECOMENDADO)

He creado un comando que detecta y elimina duplicados de forma segura.

#### 1.1. Ver qué duplicados existen (sin hacer cambios):
```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py limpiar_correlativos_duplicados --dry-run
```

#### 1.2. Limpiar duplicados con confirmación interactiva:
```bash
python manage.py limpiar_correlativos_duplicados
```

#### 1.3. Limpiar duplicados automáticamente (sin pedir confirmación):
```bash
python manage.py limpiar_correlativos_duplicados --auto-fix
```

**El comando:**
- ✅ Mantiene el registro más reciente (mayor ID)
- ✅ Elimina los registros antiguos duplicados
- ✅ Muestra información detallada de cada duplicado
- ✅ Permite revisar antes de eliminar (modo interactivo)

---

### Opción 2: Desde Django Shell

```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py shell
```

Luego ejecutar:

```python
from app.models import Correlativo
from django.db.models import Count

# 1. Encontrar duplicados
duplicados = Correlativo.objects.values('sucursal_id', 'tipo_dte').annotate(
    count=Count('id')
).filter(count__gt=1)

print(f"Grupos de duplicados encontrados: {len(duplicados)}")
for dup in duplicados:
    print(f"  - Sucursal: {dup['sucursal_id']}, Tipo: {dup['tipo_dte']}, Total: {dup['count']}")

# 2. Ver detalles del duplicado específico (sucursal_id=2, tipo_dte='COMPRA')
correlativos_dup = Correlativo.objects.filter(sucursal_id=2, tipo_dte='COMPRA').order_by('-id')
print(f"\nRegistros duplicados para Sucursal 2, COMPRA:")
for corr in correlativos_dup:
    print(f"  ID: {corr.id} | Inicio: {corr.inicio} | Termino: {corr.termino} | Disponibles: {corr.disponibles}")

# 3. Eliminar duplicados (mantener el más reciente)
# IMPORTANTE: Revisar los IDs antes de eliminar
print("\nManteniendo el registro más reciente (mayor ID)...")
correlativos_a_eliminar = correlativos_dup[1:]  # Todos excepto el primero (más reciente)
for corr in correlativos_a_eliminar:
    print(f"  Eliminando ID: {corr.id}...")
    corr.delete()

print("✅ Duplicados eliminados")

# 4. Verificar que no queden duplicados
duplicados_restantes = Correlativo.objects.values('sucursal_id', 'tipo_dte').annotate(
    count=Count('id')
).filter(count__gt=1)
print(f"\nDuplicados restantes: {len(duplicados_restantes)}")
```

---

### Opción 3: SQL Directo (Para PostgreSQL)

**⚠️ PRECAUCIÓN: Hacer backup antes de ejecutar**

#### 3.1. Identificar duplicados:
```sql
-- Ver todos los correlativos duplicados
SELECT 
    sucursal_id, 
    tipo_dte, 
    COUNT(*) as total
FROM app_correlativo
GROUP BY sucursal_id, tipo_dte
HAVING COUNT(*) > 1;
```

#### 3.2. Ver detalles de los duplicados:
```sql
-- Ver detalles del caso específico
SELECT 
    id,
    sucursal_id,
    tipo_dte,
    inicio,
    termino,
    alias,
    fecha_actualizacion
FROM app_correlativo
WHERE sucursal_id = 2 AND tipo_dte = 'COMPRA'
ORDER BY id DESC;
```

#### 3.3. Eliminar duplicados (mantener el más reciente):
```sql
-- Eliminar duplicados manteniendo el registro con mayor ID (más reciente)
WITH duplicados AS (
    SELECT 
        id,
        ROW_NUMBER() OVER (
            PARTITION BY sucursal_id, tipo_dte 
            ORDER BY id DESC
        ) as rn
    FROM app_correlativo
)
DELETE FROM app_correlativo
WHERE id IN (
    SELECT id 
    FROM duplicados 
    WHERE rn > 1
);
```

O para el caso específico:
```sql
-- Eliminar duplicados de COMPRA en sucursal 2 (mantener el más reciente)
DELETE FROM app_correlativo
WHERE id IN (
    SELECT id
    FROM app_correlativo
    WHERE sucursal_id = 2 AND tipo_dte = 'COMPRA'
    ORDER BY id ASC
    LIMIT (
        SELECT COUNT(*) - 1
        FROM app_correlativo
        WHERE sucursal_id = 2 AND tipo_dte = 'COMPRA'
    )
);
```

---

## Prevención de Duplicados Futuros

### 1. La función `obtener_siguiente_correlativo()` ya maneja esto correctamente:

```python
def obtener_siguiente_correlativo(sucursal, tipo):
    correlativo, created = Correlativo.objects.get_or_create(
        tipo_dte=tipo,
        sucursal=sucursal,
        defaults={
            'inicio': 1, 
            'termino': 999999, 
            'alias': f'{tipo}_{sucursal.alias}',
            'responsable': 'Sistema'
        }
    )
    return correlativo.obtener_siguiente_numero()
```

**`get_or_create()`** previene duplicados automáticamente.

### 2. La vista `guardar_correlativo()` también valida:

```python
# Verificar si ya existe un correlativo para esta combinación
existing_query = Correlativo.objects.filter(
    sucursal=sucursal,
    tipo_dte=tipo_documento
)

if correlativo_id:
    existing_query = existing_query.exclude(id=correlativo_id)

if existing_query.exists():
    return JsonResponse({
        'success': False,
        'message': 'Ya existe un correlativo para esta sucursal y tipo de documento'
    })
```

---

## Pasos Recomendados (EN ORDEN)

### 1️⃣ Verificar duplicados:
```bash
python manage.py limpiar_correlativos_duplicados --dry-run
```

### 2️⃣ Limpiar duplicados:
```bash
python manage.py limpiar_correlativos_duplicados --auto-fix
```

### 3️⃣ Verificar que la página carga correctamente:
```
http://localhost:8000/app/gestion-correlativos/
```

### 4️⃣ Verificar los correlativos en Django Admin:
```
http://localhost:8000/admin/app/correlativo/
```

---

## Verificación Post-Limpieza

Ejecutar en Django Shell:
```python
from app.models import Correlativo
from django.db.models import Count

# Verificar que no hay duplicados
duplicados = Correlativo.objects.values('sucursal_id', 'tipo_dte').annotate(
    count=Count('id')
).filter(count__gt=1)

if duplicados.count() == 0:
    print("✅ ¡Base de datos limpia! No hay duplicados.")
else:
    print(f"❌ Aún hay {duplicados.count()} grupos de duplicados")
    for dup in duplicados:
        print(f"   - Sucursal: {dup['sucursal_id']}, Tipo: {dup['tipo_dte']}")
```

---

## Notas Importantes

1. **El comando mantiene el registro más reciente** (mayor ID)
2. **Los duplicados antiguos se eliminan** de forma segura
3. **El modelo ya tiene la restricción de unicidad** para prevenir futuros duplicados
4. **`get_or_create()` es seguro** y no creará duplicados

---

**Fecha:** 7 de Noviembre, 2025
**Problema:** Correlativos duplicados en base de datos
**Solución:** Comando de limpieza + prevención automática

