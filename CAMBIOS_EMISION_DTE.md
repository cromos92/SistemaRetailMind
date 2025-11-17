# 🔧 Cambios Realizados - Sistema de Emisión DTE

**Fecha:** 2025-11-12  
**Issue:** Error de stock inconsistente al emitir DTE + Funciones duplicadas

---

## 🐛 Problemas Identificados y Resueltos

### **PROBLEMA 1: Inconsistencia entre Frontend y Backend**

**Síntoma:**
- Frontend mostraba: "Stock: 1, Disponible: 1" ✅
- Backend rechazaba: "Stock insuficiente: 0" ❌

**Causa Raíz:**
```python
# Frontend usaba:
stock = talla.stock  # Campo directo de BD

# Backend validaba con:
stock = talla.stock_sucursal(sucursal_id)  # Calculado desde movimientos
```

**Solución Aplicada:** ✅
- Ambos ahora usan `stock_sucursal()` para consistencia total
- Se implementó **sistema híbrido** compatible con migración de datos

---

### **PROBLEMA 2: Funciones Duplicadas**

Se encontraron múltiples funciones con el mismo nombre en diferentes archivos:

| Función | Ubicación | Estado |
|---------|-----------|--------|
| `emitir_dte` | `views.py:8754` | ✅ ACTIVA (en URLs) |
| `emitir_dte` | `views_modulo_documentos.py:793` | ❌ ELIMINADA |
| `buscar_productos_bodega` | `views.py:8442` | ✅ ACTIVA (actualizada) |
| `buscar_productos_bodega` | `views.py:9651` | ⚠️ Renombrada `_DUPLICADA_NO_USAR` |
| `buscar_productos_bodega` | `views_modulo_documentos.py:716` | ⚠️ Posible uso módulo docs |
| `buscar_productos_bodega` | `views_modulo_ventas.py:570` | ⚠️ Posible uso módulo ventas |

---

## 📝 Archivos Modificados

### 1. `app/models.py`

**Cambio:** Método `stock_sucursal()` ahora es **híbrido**

```python
def stock_sucursal(self, sucursal_id):
    """
    SISTEMA HÍBRIDO:
    - Si tiene movimientos → Calcula desde movimientos
    - Si NO tiene movimientos → Usa campo 'stock' directo (legacy)
    """
    tiene_movimientos = self.movimientos_productos_talla.exists()
    
    if tiene_movimientos:
        # Calcular desde movimientos (sistema nuevo)
        return calcular_desde_movimientos()
    else:
        # Usar campo stock directo (datos migrados)
        if self.producto.sucursal_id == sucursal_id:
            return self.stock
        else:
            return 0
```

**Beneficios:**
- ✅ Compatible con datos migrados (sin movimientos)
- ✅ Transición gradual al sistema de movimientos
- ✅ No requiere crear millones de movimientos históricos

---

### 2. `app/views.py`

**Función:** `buscar_productos_bodega()` (línea 8442)

**Cambios:**
```python
# ANTES:
tallas.filter(stock__gt=0)
'stock': talla.stock  # Campo directo

# AHORA:
for talla in tallas:
    stock_real = talla.stock_sucursal(sucursal_id)  # Método consistente
    if stock_real > 0:
        # Solo incluir tallas con stock REAL
```

**Cambios adicionales:**
- ✅ Agregado campo `'sku'` en respuesta
- ✅ Agregado campo `'color'` en respuesta
- ✅ Agregado campo `'tallas_detalle'` para compatibilidad
- ✅ Solo devuelve productos con stock REAL disponible

**Función:** `emitir_dte()` (línea 8754)

**Cambios:**
- ✅ Agregados múltiples puntos de debug
- ✅ Mejor manejo de errores con traceback
- ✅ Validaciones mejoradas

---

### 3. `app/views_modulo_documentos.py`

**Cambios:**
- ❌ Eliminada función `emitir_dte()` duplicada (línea 793)
- ✅ Agregado comentario indicando que está en `views.py`

---

### 4. Nuevo Archivo: `app/management/commands/convertir_stock_legacy.py`

**Descripción:** Comando de Django para convertir stock legacy a movimientos

**Uso:**
```bash
# Ver simulación
python manage.py convertir_stock_legacy --sucursal 1 --dry-run

# Convertir sucursal específica
python manage.py convertir_stock_legacy --sucursal 1

# Convertir producto específico
python manage.py convertir_stock_legacy --producto 143829

# Convertir todos (usar con precaución)
python manage.py convertir_stock_legacy --all --skip-existing
```

**Opciones:**
- `--all`: Convertir todos los productos
- `--sucursal ID`: Convertir productos de una sucursal
- `--producto ID`: Convertir un producto específico
- `--dry-run`: Simular sin hacer cambios
- `--skip-existing`: Saltar productos que ya tienen movimientos

---

## 🧪 Instrucciones de Prueba

### 1️⃣ Reiniciar Servidor

```bash
# Detener servidor actual (Ctrl+C)
# Reiniciar
python manage.py runserver
```

### 2️⃣ Limpiar Caché del Navegador

- Chrome/Edge: `Ctrl + Shift + Del`
- Marcar "Imágenes y archivos en caché"
- Borrar

### 3️⃣ Recargar Página

- Ir a: `http://localhost:8000/app/emisionDTE/`
- Forzar recarga: `Ctrl + F5`

### 4️⃣ Buscar Productos

**Caso A: Producto SIN movimientos (datos migrados)**
- Buscar cualquier producto migrado
- Debería mostrar el `stock` del campo directo
- Al emitir DTE, debería funcionar correctamente

**Caso B: Producto CON movimientos**
- Buscar producto que tenga movimientos registrados
- Debería mostrar stock calculado desde movimientos
- Al emitir DTE, debería validar contra el mismo valor

### 5️⃣ Emitir DTE de Prueba

1. Seleccionar método despacho: **Interno**
2. Seleccionar tipo documento: **GUIA DE DESPACHO**
3. Seleccionar sucursal destino
4. Buscar y agregar productos
5. Click en "Emitir DTE"

**Resultado esperado:**
- ✅ Si hay stock real: DTE se emite correctamente
- ✅ Si NO hay stock real: Error claro "Stock insuficiente"
- ❌ NO debería haber inconsistencia entre búsqueda y validación

---

## 📊 Verificación en Base de Datos

### Ver productos con/sin movimientos

```sql
-- Productos CON movimientos (sistema nuevo)
SELECT p.articulo, pt.sku, pt.stock, COUNT(m.id) as num_movimientos
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
LEFT JOIN app_movimientos_producto m ON m.ProductoTalla_id = pt.id
GROUP BY p.id, pt.id
HAVING COUNT(m.id) > 0
LIMIT 20;

-- Productos SIN movimientos (sistema legacy)
SELECT p.articulo, pt.sku, pt.stock
FROM app_producto_talla pt
JOIN app_producto p ON pt.producto_id = p.id
LEFT JOIN app_movimientos_producto m ON m.ProductoTalla_id = pt.id
WHERE pt.stock > 0
GROUP BY p.id, pt.id
HAVING COUNT(m.id) = 0
LIMIT 20;
```

---

## 🎯 Estrategia de Migración

Ver documento completo: `ESTRATEGIA_MIGRACION_STOCK.md`

**Resumen:**
1. **NO es necesario** convertir todos los productos inmediatamente
2. Sistema funciona con datos legacy indefinidamente
3. Productos se convierten gradualmente o automáticamente al usarse
4. Usa comando `convertir_stock_legacy` solo cuando estés listo

---

## 🔍 Troubleshooting

### Problema: No aparecen productos en búsqueda

**Causa:** Productos no tienen stock real en esa sucursal

**Solución:**
```python
# En Django shell
from app.models import Producto_Talla

# Verificar stock de un producto
talla = Producto_Talla.objects.get(sku='4824824')
print(f"Stock campo directo: {talla.stock}")
print(f"Stock calculado sucursal 1: {talla.stock_sucursal(1)}")
print(f"Tiene movimientos: {talla.movimientos_productos_talla.exists()}")

# Ver movimientos del producto
for mov in talla.movimientos_productos_talla.all():
    print(f"  {mov.concepto}: {mov.cantidad} ({mov.estado})")
```

### Problema: Stock muestra 0 pero debería haber stock

**Causa:** Producto tiene movimientos pero no en esa sucursal

**Solución:**
1. Verificar que `producto.sucursal_id` coincida con sucursal buscada
2. Si son datos migrados sin movimientos, el stock debería aparecer
3. Si tiene movimientos, verificar que sean `COMPLETADO` y en sucursal correcta

---

## ✅ Checklist de Verificación Post-Deploy

- [ ] Servidor reiniciado
- [ ] Caché del navegador limpiado
- [ ] Búsqueda de productos funciona
- [ ] Stock mostrado es consistente
- [ ] Emisión DTE funciona sin error de stock
- [ ] Productos legacy funcionan (sin movimientos)
- [ ] Productos nuevos funcionan (con movimientos)
- [ ] Logs del servidor no muestran errores

---

## 📞 Soporte

Si encuentras algún problema:

1. **Revisar logs del servidor** (consola donde corre Django)
2. **Copiar el error completo** incluido el traceback
3. **Verificar en BD** el estado del producto problemático
4. **Ejecutar diagnóstico** en Django shell

---

**Estado:** ✅ Cambios aplicados y documentados  
**Próximo paso:** Pruebas en entorno de desarrollo

