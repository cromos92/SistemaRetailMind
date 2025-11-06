# ⚡ INICIO RÁPIDO: Edición de Productos y Stock

## 🎯 Objetivo
Comenzar a usar el sistema de edición de productos en **menos de 5 minutos**.

---

## ✅ Pre-requisitos

Antes de comenzar, asegúrese de tener:
- ✅ Servidor Django corriendo
- ✅ Base de datos con productos existentes
- ✅ Usuario con permisos de edición
- ✅ Navegador web moderno (Chrome, Firefox, Edge)

---

## 🚀 Pasos Rápidos

### 1. Iniciar Servidor (si no está corriendo)

```bash
# Navegar al directorio del proyecto
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind

# Activar entorno virtual (si aplica)
# venv\Scripts\activate

# Iniciar servidor
python manage.py runserver
```

### 2. Acceder al Sistema

1. Abra su navegador
2. Navegue a: `http://localhost:8000/app/verGestionProducto/`
3. Inicie sesión si es necesario

### 3. Editar su Primer Producto

#### Paso A: Buscar Producto
```
1. Use el buscador en la página
2. Busque por nombre, código o SKU
3. Localice el producto en la tabla
```

#### Paso B: Abrir Editor
```
1. Haga clic en el botón "Editar" (ícono lápiz) 
   - Si no aparece, agregue el botón manualmente (ver más abajo)
2. Se abrirá el modal de edición
```

#### Paso C: Modificar Datos
```
Pestaña "Datos Generales":
- Cambie el nombre, descripción o categoría
- Modifique los precios
- Actualice atributos (marca, color, etc.)

Clic en "Guardar Cambios"
```

### 4. Ajustar Stock (Primer Ajuste)

#### Ejemplo: Agregar 10 Unidades

```
1. En el modal de edición, vaya a pestaña "Variaciones / Tallas"
2. Localice la talla (ej: "38")
3. Clic en botón "Ajustar Stock"
4. Configure:
   - Tipo: ENTRADA
   - Cantidad: 10
   - Costo Unitario: 50000
   - Precio Venta: 70000
   - Motivo: "Ajuste de prueba - ingreso inicial"
5. Clic en "Confirmar Ajuste"
```

#### Ejemplo: Quitar 5 Unidades

```
1. Mismo proceso anterior
2. Configure:
   - Tipo: SALIDA
   - Cantidad: 5
   - Motivo: "Ajuste de prueba - salida de ejemplo"
3. Clic en "Confirmar Ajuste"
```

### 5. Ver Historial

```
1. En pestaña "Variaciones / Tallas"
2. Clic en botón "Ver Historial" (ícono reloj)
3. Verá todos los movimientos registrados
```

---

## 🔧 Si el Botón "Editar" No Aparece

Si no ve el botón "Editar" en la tabla de productos, agregue manualmente la funcionalidad:

### Opción 1: Llamar desde Consola del Navegador

```javascript
// Abra la consola del navegador (F12)
// Pegue este código para probar con un producto específico:

abrirModalEdicionProducto(1); // Reemplace "1" con el ID del producto
```

### Opción 2: Agregar Botón Manualmente

En la tabla de productos, agregue botón en la columna de acciones:

```html
<button type="button" class="btn btn-sm btn-warning" 
        onclick="abrirModalEdicionProducto(PRODUCTO_ID)">
    <i class="fas fa-edit"></i> Editar
</button>
```

Reemplace `PRODUCTO_ID` con el ID real del producto.

---

## 📋 Checklist de Verificación

Después de los primeros pasos, verifique:

- [ ] ✅ El modal de edición se abre correctamente
- [ ] ✅ Los datos del producto se cargan en el modal
- [ ] ✅ Las variaciones aparecen en la tabla
- [ ] ✅ Puedo guardar cambios en datos generales
- [ ] ✅ Puedo hacer un ajuste de ENTRADA
- [ ] ✅ Puedo hacer un ajuste de SALIDA
- [ ] ✅ El historial muestra los movimientos
- [ ] ✅ El stock se actualiza correctamente

---

## 🐛 Problemas Comunes

### Error: "No se pudo cargar el producto"

**Causa**: El producto no existe o no tiene permisos  
**Solución**: Verifique que el ID del producto sea correcto y que tenga permisos

### Error: "Stock insuficiente"

**Causa**: Intenta hacer una salida mayor al stock disponible  
**Solución**: Reduzca la cantidad o verifique el stock actual

### El modal no se abre

**Causa**: Error de JavaScript o falta cargar el script  
**Solución**: 
1. Abra consola del navegador (F12)
2. Verifique si hay errores
3. Recargue la página con Ctrl+F5

### No se cargan categorías/atributos

**Causa**: No hay datos en la base de datos  
**Solución**: Cree categorías y atributos desde Django Admin

---

## 💡 Ejemplos Prácticos

### Ejemplo 1: Cambiar Precio de Producto

```
Situación: Necesito aumentar el precio de $50.000 a $60.000

Pasos:
1. Editar producto
2. Pestaña "Datos Generales"
3. Campo "Precio Venta": cambiar a 60000
4. Guardar Cambios
```

### Ejemplo 2: Corregir Stock Después de Inventario

```
Situación: El inventario físico indica 25 unidades, pero el sistema marca 20

Pasos:
1. Editar producto → Pestaña "Variaciones"
2. Ajustar Stock → ENTRADA
3. Cantidad: 5
4. Costos: usar costo promedio actual
5. Motivo: "Corrección por inventario físico - diferencia de 5 unidades"
6. Confirmar
```

### Ejemplo 3: Registrar Producto Dañado

```
Situación: Se dañaron 2 unidades y deben descartarse

Pasos:
1. Editar producto → Pestaña "Variaciones"
2. Ajustar Stock → SALIDA
3. Cantidad: 2
4. Motivo: "Producto dañado por humedad, se descartan 2 unidades"
5. Confirmar
```

---

## 📊 Flujo Visual Rápido

```
┌─────────────────────────────────────────────────────┐
│ 1. Acceder a Gestión de Productos                  │
│    http://localhost:8000/app/verGestionProducto/   │
└──────────────────┬──────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────┐
│ 2. Buscar y Seleccionar Producto                   │
│    [Buscador] → Encontrar → [Editar]              │
└──────────────────┬──────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────┐
│ 3. Modal de Edición                                 │
│    Tab 1: Datos Generales → Modificar → Guardar   │
│    Tab 2: Variaciones → Ajustar Stock             │
└──────────────────┬──────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────┐
│ 4. Ajuste de Stock                                  │
│    ENTRADA: Agregar stock + crear lote FIFO       │
│    SALIDA: Quitar stock con consumo FIFO          │
└──────────────────┬──────────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────────┐
│ 5. Verificación                                     │
│    Ver Historial → Confirmar movimiento           │
│    Verificar stock actualizado                     │
└─────────────────────────────────────────────────────┘
```

---

## 🎓 Siguientes Pasos

Una vez que haya probado las funciones básicas:

1. **Profundizar**: Lea la [Guía de Usuario Completa](GUIA_USO_EDICION_PRODUCTOS.md)
2. **Explorar**: Revise el [Plan Técnico](PLAN_EDICION_PRODUCTOS_Y_STOCK.md)
3. **Personalizar**: Ajuste según las necesidades de su negocio
4. **Capacitar**: Entrene a otros usuarios del sistema

---

## 📞 ¿Necesita Ayuda?

Si encuentra problemas:
1. Revise la sección "Solución de Problemas" en [GUIA_USO_EDICION_PRODUCTOS.md](GUIA_USO_EDICION_PRODUCTOS.md)
2. Verifique los logs del servidor Django
3. Consulte la documentación técnica

---

## ✅ Checklist de Primera Vez

Para verificar que todo funciona:

```
Primera Edición de Producto:
☐ Cambié el nombre de un producto
☐ Modifiqué el precio de venta
☐ Guardé los cambios exitosamente

Primera Entrada de Stock:
☐ Hice un ajuste de ENTRADA
☐ Ingresé cantidad y costos
☐ Escribí un motivo descriptivo
☐ El stock aumentó correctamente

Primera Salida de Stock:
☐ Hice un ajuste de SALIDA
☐ Escribí un motivo descriptivo
☐ El stock disminuyó correctamente

Verificación:
☐ Vi el historial de movimientos
☐ Confirme que aparecen mis ajustes
☐ Verifiqué el usuario responsable
☐ Comprobé las fechas/horas
```

---

**¡Listo para comenzar! 🚀**

Abra su navegador y acceda a:
```
http://localhost:8000/app/verGestionProducto/
```

**Tiempo estimado**: 5-10 minutos para probar todo

