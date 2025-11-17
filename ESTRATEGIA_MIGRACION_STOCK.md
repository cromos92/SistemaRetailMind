# 📦 Estrategia de Migración de Stock - Sistema Híbrido

## 🎯 Objetivo

Migrar datos históricos de inventario SIN necesidad de crear millones de movimientos, utilizando un **sistema híbrido** que permite trabajar con datos legacy y nuevos datos simultáneamente.

---

## 🔄 Cómo Funciona el Sistema Híbrido

### Método `stock_sucursal(sucursal_id)` en modelo `Producto_Talla`

```python
def stock_sucursal(self, sucursal_id):
    # 1. Verifica si el producto tiene movimientos registrados
    tiene_movimientos = self.movimientos_productos_talla.exists()
    
    if tiene_movimientos:
        # SISTEMA NUEVO: Calcula desde movimientos
        return calcular_desde_movimientos()
    else:
        # SISTEMA LEGACY: Usa campo 'stock' directo
        if self.producto.sucursal_id == sucursal_id:
            return self.stock
        else:
            return 0
```

### ✅ Ventajas

1. **No requiere migración inmediata**: Productos migrados funcionan con su campo `stock` actual
2. **Transición gradual**: Nuevos productos usan sistema de movimientos desde día 1
3. **Cero downtime**: Sistema funciona durante y después de la migración
4. **Auditoría completa**: Productos convertidos tienen historial completo desde la conversión

---

## 📋 Proceso de Migración Recomendado

### **FASE 1: Migración de Datos Básicos** ✅ (Ya hecho)

Migrar desde sistema antiguo:
- ✅ Productos
- ✅ Producto_Talla con campo `stock` poblado
- ✅ Sucursales
- ✅ Empresas
- ✅ Usuarios

**Los productos funcionan INMEDIATAMENTE con el campo `stock` directo**

---

### **FASE 2: Operación con Sistema Híbrido** (RECOMENDADO para inicio)

**Durante los primeros meses:**
- Productos migrados: Usan campo `stock` legacy
- Nuevos ingresos: Crean movimientos automáticamente
- Nuevas ventas: Crean movimientos automáticamente
- Sistema funciona normalmente SIN conversión masiva

**Ejemplo:**
```
Producto A (migrado, stock=10):
  - Primera venta → Crea primer movimiento (EGRESO -2)
  - Ahora el producto usa sistema de movimientos
  - Stock calculado: 10 (inicial) - 2 = 8

Producto B (nuevo):
  - Desde día 1 usa movimientos
  - Stock siempre calculado desde movimientos
```

---

### **FASE 3: Conversión Gradual** (OPCIONAL, cuando estés listo)

Convertir productos legacy al sistema de movimientos usando el comando de gestión.

#### Opción A: Conversión por Sucursal

```bash
# 1. Simular primero (ver qué se haría)
python manage.py convertir_stock_legacy --sucursal 1 --dry-run

# 2. Ejecutar conversión real
python manage.py convertir_stock_legacy --sucursal 1

# 3. Repetir para cada sucursal
python manage.py convertir_stock_legacy --sucursal 2
python manage.py convertir_stock_legacy --sucursal 3
```

#### Opción B: Conversión Selectiva

```bash
# Convertir un producto específico
python manage.py convertir_stock_legacy --producto 143829

# Convertir productos sin movimientos (los que aún son legacy)
python manage.py convertir_stock_legacy --all --skip-existing
```

#### Opción C: Conversión Total (Usar con PRECAUCIÓN)

```bash
# Simular TODA la conversión
python manage.py convertir_stock_legacy --all --dry-run

# Ejecutar conversión completa (puede tardar mucho)
python manage.py convertir_stock_legacy --all
```

---

## ⚙️ Opciones del Comando

| Parámetro | Descripción |
|-----------|-------------|
| `--all` | Convertir TODOS los productos |
| `--sucursal ID` | Convertir solo productos de una sucursal |
| `--producto ID` | Convertir un producto específico |
| `--dry-run` | Simular sin hacer cambios reales |
| `--skip-existing` | Saltar productos que ya tienen movimientos |

---

## 📊 Ejemplo de Uso Paso a Paso

### 1️⃣ Ver qué se convertiría en Sucursal 1

```bash
python manage.py convertir_stock_legacy --sucursal 1 --dry-run
```

**Salida esperada:**
```
🔍 MODO DRY-RUN: No se harán cambios reales

📦 Procesando productos de sucursal 1 (1234 tallas)
  ✅ VU4024T-34 (VU4024T - Talla 34): Stock legacy = 1 en Bodega Central
  ✅ VU4024T-35 (VU4024T - Talla 35): Stock legacy = 1 en Bodega Central
  ⏭️  VU4025T-36: Stock = 0, saltando...
  ...

📊 RESUMEN DE CONVERSIÓN:
  ✅ Procesados: 856
  📝 Movimientos que se crearían: 856
  ⏭️  Saltados: 378
  ❌ Errores: 0

⚠️  Esto fue una simulación. Ejecuta sin --dry-run para aplicar cambios.
```

### 2️⃣ Ejecutar conversión real

```bash
python manage.py convertir_stock_legacy --sucursal 1
```

### 3️⃣ Verificar en Base de Datos

```sql
-- Ver movimientos creados
SELECT * FROM app_movimientos_producto 
WHERE concepto = 'INGRESO_INICIAL' 
AND responsable = 'Sistema - Migración'
ORDER BY created_at DESC
LIMIT 20;
```

---

## 🚨 Consideraciones Importantes

### ⚠️ NO es necesario convertir TODO de una vez

El sistema híbrido permite:
- Trabajar con productos legacy indefinidamente
- Convertir cuando sea conveniente
- Priorizar productos con más movimiento

### ✅ Productos se convierten automáticamente

Cuando un producto legacy:
- Recibe su primera venta → Se crea movimiento EGRESO
- Recibe su primer ingreso → Se crea movimiento INGRESO
- **A partir de ese momento usa el sistema nuevo**

### 📈 Recomendación de Migración

**Para una empresa con 50,000 productos:**

1. **Mes 1-2**: Operar con sistema híbrido sin conversión
2. **Mes 3**: Convertir productos de alta rotación (Top 20%)
3. **Mes 4-6**: Convertir productos de rotación media
4. **Mes 6+**: Convertir resto gradualmente o dejar en legacy

**Productos de baja rotación pueden quedarse en legacy indefinidamente**

---

## 🔍 Diagnóstico del Sistema

### Ver productos legacy vs nuevos

```python
# En Django shell
from app.models import Producto_Talla

# Productos legacy (sin movimientos)
legacy = Producto_Talla.objects.filter(
    movimientos_productos_talla__isnull=True,
    stock__gt=0
).distinct().count()

# Productos en sistema nuevo (con movimientos)
nuevos = Producto_Talla.objects.filter(
    movimientos_productos_talla__isnull=False
).distinct().count()

print(f"Legacy: {legacy}, Nuevos: {nuevos}")
```

---

## 📞 Soporte

Si tienes dudas sobre la estrategia de migración:
1. Ejecuta siempre `--dry-run` primero
2. Comienza con una sucursal pequeña de prueba
3. Verifica los resultados antes de continuar

---

## 🎉 Resumen

✅ **Sistema híbrido ACTIVO**: Ya puedes operar normalmente
✅ **Conversión OPCIONAL**: Hazla cuando estés listo
✅ **Sin presión**: El sistema funciona con datos legacy indefinidamente
✅ **Transición gradual**: Convierte productos poco a poco

