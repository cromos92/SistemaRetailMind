# Corrección de `fecha_creacion` en productos migrados

## Problema

El comando [`migrate_from_vicent.py`](retailmind/app/management/commands/migrate_from_vicent.py) importó los productos desde MySQL legacy sin preservar la fecha original. Como [`Producto.fecha_creacion`](retailmind/app/models/catalogo.py#L144) tiene `auto_now_add=True`, Django asignó `timezone.now()` a todos los registros (la fecha de la migración: `2026-04-15`).

Esto deja **el 100% del catálogo con `fecha_creacion` incorrecta**: todos los productos aparecen como creados el día de la migración, perdiendo su fecha real de alta.

## Solución

Recuperar la fecha real de cada producto desde el **primer `Movimientos_Producto.fecha`** asociado vía `Producto_Talla`. Esto funciona porque [`migrate_from_laravel.py`](retailmind/app/management/commands/migrate_from_laravel.py#L1620) **sí preservó** las fechas originales al importar los movimientos desde MySQL.

Implementación: management command [`corregir_fecha_creacion_productos.py`](retailmind/app/management/commands/corregir_fecha_creacion_productos.py).

- Calcula `MIN(Movimientos_Producto.fecha)` por producto en una sola query agregada.
- Solo actualiza productos donde la fecha del primer movimiento es **anterior** a la `fecha_creacion` actual (margen configurable).
- Bypassa `auto_now_add` usando `QuerySet.update()`.
- Soporta `--dry-run` por defecto y `--apply` para escribir.

## Precisión esperada

Verificación contra MySQL legacy (con [`verificar_fecha_creacion_vs_mysql.py`](retailmind/app/management/commands/verificar_fecha_creacion_vs_mysql.py)):

- **~97% match exacto** con MySQL filtrando por sucursal.
- Los ~3% restantes son productos cuya talla con SKU duplicado entre sucursales fue migrada con movimientos contaminados de otra sucursal — problema aislado y de bajo impacto para esta corrección.

## Estado validado en local

| Métrica | Valor |
|---|---|
| Productos totales | ~137K |
| Con `fecha_creacion = 2026-04-15` antes del fix | 100% (137K) |
| Actualizables (con movimientos asociados) | ~102K |
| Sin movimientos (quedan con fecha de migración) | ~35K |
| Match exacto contra MySQL post-fix | 96.9% |
| Mismatches > 1 día | <3% |

---

## Plan de aplicación en producción

### Pre-requisitos

- [ ] Acceso a la DB de producción (`db-postgresql-...-do-user-...ondigitalocean.com:25060`).
- [ ] El código en producción debe tener disponibles los 2 management commands:
  - `corregir_fecha_creacion_productos.py`
  - `verificar_fecha_creacion_vs_mysql.py` (solo para validación)
- [ ] Variables MySQL en `.env` de producción (solo si vas a correr el verificador).
- [ ] Ventana de mantenimiento sugerida — no es estrictamente necesaria, pero el `--apply` corre ~5 minutos con 102K UPDATEs y consume conexiones.

### Paso 1 — Backup de la columna (OBLIGATORIO en producción)

Conectado a producción con `psql` o el cliente de tu preferencia:

```sql
ALTER TABLE app_producto ADD COLUMN fecha_creacion_backup TIMESTAMPTZ;
UPDATE app_producto SET fecha_creacion_backup = fecha_creacion;

-- Verifica que se respaldó todo
SELECT COUNT(*) AS total, COUNT(fecha_creacion_backup) AS respaldados FROM app_producto;
```

Los dos números deben coincidir.

### Paso 2 — Verificar estado inicial

```sql
SELECT EXTRACT(YEAR FROM fecha_creacion)::int AS anio, COUNT(*) AS productos
FROM app_producto
GROUP BY anio
ORDER BY anio;
```

Esperado: casi todos en `2026` (la fecha de la migración). Si no es así, **detente** y revisa — algo cambió desde local.

### Paso 3 — Dry-run

Conectado al entorno de producción (Railway / DigitalOcean App / equivalente con la `DATABASE_URL` apuntando a prod):

```bash
python manage.py corregir_fecha_creacion_productos
```

Verifica que el output tenga:
- `~102,000 productos con movimientos asociados`
- `~102,000 candidatos a actualizar`
- Muestra de 15 productos con fechas correctas (años 2018-2025)
- `⚠ DRY-RUN — no se aplicaron cambios.`

Si los números no se parecen a local, **detente y revisa**.

### Paso 4 — Apply

```bash
python manage.py corregir_fecha_creacion_productos --apply
```

Tarda 2-8 minutos. Output esperado: `✓ N productos actualizados.`

Mientras corre, NO ejecutar otras operaciones masivas sobre `app_producto`.

### Paso 5 — Validar distribución por año

```sql
SELECT EXTRACT(YEAR FROM fecha_creacion)::int AS anio, COUNT(*) AS productos
FROM app_producto
GROUP BY anio
ORDER BY anio;
```

Distribución esperada (números aproximados según local):

| Año | Productos aprox. |
|---|---|
| 2013 | ~12 (outliers del legacy) |
| 2018 | ~12,500 |
| 2019 | ~13,100 |
| 2020 | ~12,400 |
| 2021 | ~17,700 |
| 2022 | ~13,800 |
| 2023 | ~11,300 |
| 2024 | ~9,800 |
| 2025 | ~9,700 |
| 2026 | ~36,500 (sin movs + nativos post-migración) |

### Paso 6 — Validar contra MySQL (opcional pero recomendado)

```bash
python manage.py verificar_fecha_creacion_vs_mysql --por-anio 30 --fecha-max 2026-03-01 --mostrar-mismatches 30
```

Espera ver:
- `Match exacto: ~96-97%`
- Mismatches concentrados en productos con SKU duplicado entre sucursales

### Paso 7 — Smoke test funcional

- Abrir la UI: `/app/verMovimientosProducto/` para un par de productos antiguos y verificar que la fecha de creación se ve coherente.
- Revisar dashboards que dependan de `fecha_creacion` (rotación, antigüedad de catálogo).
- Confirmar que no hay errores en logs.

### Paso 8 — Limpieza (después de ~7 días sin incidencias)

```sql
-- Solo después de confirmar que todo funciona correctamente
ALTER TABLE app_producto DROP COLUMN fecha_creacion_backup;
```

---

## Rollback (si algo sale mal)

Si en cualquier paso después del `--apply` notas que algo está incorrecto:

```sql
BEGIN;

UPDATE app_producto 
SET fecha_creacion = fecha_creacion_backup 
WHERE fecha_creacion_backup IS NOT NULL;

-- Verifica antes de commitear
SELECT EXTRACT(YEAR FROM fecha_creacion)::int AS anio, COUNT(*) FROM app_producto GROUP BY anio ORDER BY anio;

-- Si todo bien:
COMMIT;
-- Si no:
ROLLBACK;
```

---

## Apéndice — Comandos opcionales avanzados

### Limitar a un solo producto (para pruebas puntuales)

```bash
python manage.py corregir_fecha_creacion_productos --producto-id 12345 --apply
```

### Procesar una muestra acotada antes de full apply

```bash
python manage.py corregir_fecha_creacion_productos --limit 100 --apply
```

### Cambiar el margen mínimo de diferencia (default: 1 día)

```bash
python manage.py corregir_fecha_creacion_productos --margen-dias 7 --apply
```

Útil si quieres ser más conservador y no tocar productos donde la diferencia es mínima.

---

## Problemas conocidos NO resueltos por este fix

- **~12% de movimientos asignados a `Producto_Talla` incorrecto** entre sucursales (cuando el SKU se repite). Afecta reportes/dashboards por sucursal, pero **no** la `fecha_creacion`. Es un problema separado documentado aparte.

## Archivos relevantes

- Comando de corrección: [`retailmind/app/management/commands/corregir_fecha_creacion_productos.py`](retailmind/app/management/commands/corregir_fecha_creacion_productos.py)
- Comando de verificación: [`retailmind/app/management/commands/verificar_fecha_creacion_vs_mysql.py`](retailmind/app/management/commands/verificar_fecha_creacion_vs_mysql.py)
- Modelo afectado: [`retailmind/app/models/catalogo.py:144`](retailmind/app/models/catalogo.py#L144)
- Migración original con el bug: [`retailmind/app/management/commands/migrate_from_vicent.py:589`](retailmind/app/management/commands/migrate_from_vicent.py#L589)
