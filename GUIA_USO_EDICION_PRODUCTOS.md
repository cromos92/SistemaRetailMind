# GUÍA DE USO: SISTEMA DE EDICIÓN DE PRODUCTOS Y GESTIÓN DE STOCK

## 📖 Introducción

Este documento describe cómo utilizar el sistema de edición de productos y gestión de stock en RetailMind. El sistema permite:

- ✅ Editar información de productos existentes
- ✅ Modificar variaciones/tallas
- ✅ Ajustar stock con control FIFO
- ✅ Ver historial completo de movimientos
- ✅ Mantener trazabilidad de cambios

---

## 🚀 ACCESO AL SISTEMA

### 1. Acceder a Gestión de Productos

1. Inicie sesión en RetailMind
2. Navegue a **Menú Principal** → **Productos** → **Gestión de Productos**
3. URL directa: `http://localhost:8000/app/verGestionProducto/`

### 2. Buscar un Producto

- Use el **buscador** para encontrar productos por nombre, código o SKU
- Aplique **filtros** por categoría, marca o estado
- La tabla mostrará todos los productos disponibles

---

## ✏️ EDITAR UN PRODUCTO

### Abrir el Modal de Edición

1. En la tabla de productos, localice el producto que desea editar
2. Haga clic en el botón **Editar** (ícono de lápiz) en la columna de acciones
3. Se abrirá el modal de edición con dos pestañas:
   - **Datos Generales**: Información básica del producto
   - **Variaciones / Tallas**: Gestión de stock por talla

### Pestaña: Datos Generales

En esta pestaña puede modificar:

#### Información Básica
- **Nombre del Producto** **(requerido)**: Nombre descriptivo del producto
- **Categoría**: Clasificación del producto
- **Descripción**: Texto descriptivo adicional

#### Atributos
- **Marca**: Marca del producto (Nike, Adidas, etc.)
- **Color**: Color principal
- **Género**: Hombre, Mujer, Unisex, Niño, etc.
- **Otro Atributo**: Campo adicional personalizable

#### Precios Base
- **Costo** **(requerido)**: Costo de adquisición del producto
- **Sobreprecio**: Margen adicional
- **Precio Venta** **(requerido)**: Precio final de venta
- **Precio Sugerido**: Precio sugerido (opcional)

> 💡 **Tip**: Al ingresar Costo y Sobreprecio, el Precio Venta se calcula automáticamente

#### Guardar Cambios

1. Revise que todos los campos obligatorios estén completos
2. Haga clic en **Guardar Cambios**
3. El sistema validará los datos y guardará las modificaciones
4. Recibirá una confirmación de éxito

---

## 📦 GESTIONAR VARIACIONES Y STOCK

### Pestaña: Variaciones / Tallas

Esta pestaña muestra todas las variaciones (tallas) del producto con su stock actual.

#### Información Mostrada

Para cada variación se muestra:
- **Talla**: Número o código de talla
- **SKU**: Código único de la variación
- **Stock**: Cantidad disponible actual
  - 🟢 Verde: Stock normal (≥ 5 unidades)
  - 🟡 Amarillo: Stock bajo (< 5 unidades)
  - 🔴 Rojo: Sin stock (0 unidades)
- **Acciones disponibles**:
  - 📦 **Ajustar Stock**: Modificar cantidad
  - 🕐 **Ver Historial**: Movimientos de esta variación
  - 📑 **Ver Lotes**: Detalles de lotes FIFO

---

## 📊 AJUSTAR STOCK

### ¿Cuándo Ajustar Stock?

Utilice esta función para:
- ✅ Ingresar mercadería no registrada
- ✅ Corregir diferencias de inventario
- ✅ Registrar mermas o pérdidas
- ✅ Realizar ajustes por recuento físico

### Procedimiento de Ajuste

#### 1. Abrir Modal de Ajuste

1. En la pestaña **Variaciones / Tallas**, localice la talla a ajustar
2. Haga clic en el botón **Ajustar** (📦)
3. Se abrirá el modal "Ajustar Stock"

#### 2. Información Mostrada

El modal muestra:
- **Talla**: Identificador de la variación
- **Stock Actual**: Cantidad disponible antes del ajuste

#### 3. Seleccionar Tipo de Ajuste

Elija entre dos opciones:

##### ↑ ENTRADA (Incrementar Stock)

Use esta opción cuando necesite **agregar** mercadería al stock.

**Campos requeridos:**
- ✅ **Cantidad**: Unidades a ingresar (ej: 10)
- ✅ **Costo Unitario**: Costo de compra por unidad (ej: $50.000)
- ✅ **Sobreprecio Unitario**: Margen adicional (ej: $10.000)
- ✅ **Precio Venta Unitario**: Precio de venta (ej: $70.000)
- ⚪ **Número de Lote**: Código identificador (opcional, se genera automático)
- ✅ **Motivo**: Descripción del ajuste (mínimo 10 caracteres)

**Ejemplo de motivo:**
```
"Ingreso de mercadería por compra directa sin DTE, proveedor local"
"Corrección por recuento físico trimestral, había 10 unidades no registradas"
```

**¿Qué hace el sistema?**
1. Crea un nuevo **lote FIFO** con la cantidad y costos especificados
2. Registra un **movimiento** tipo "AJUSTE_POSITIVO"
3. Incrementa el stock total de la variación
4. Registra al usuario responsable del ajuste

##### ↓ SALIDA (Decrementar Stock)

Use esta opción cuando necesite **restar** mercadería del stock.

**Campos requeridos:**
- ✅ **Cantidad**: Unidades a descontar (ej: 3)
- ✅ **Motivo**: Descripción del ajuste (mínimo 10 caracteres)

**Ejemplo de motivo:**
```
"Producto dañado durante almacenamiento, se descarta"
"Merma por robo detectado en inventario"
"Producto usado como muestra para cliente mayorista"
```

**¿Qué hace el sistema?**
1. Consume stock usando método **FIFO** (First In, First Out)
   - Se descuenta primero del lote más antiguo
2. Registra un **movimiento** tipo "AJUSTE_NEGATIVO"
3. Decrementa el stock total de la variación
4. Registra al usuario responsable del ajuste

**⚠️ Validación:** El sistema NO permitirá una salida si la cantidad solicitada es mayor al stock disponible.

#### 4. Verificar Stock Resultante

- El sistema muestra en tiempo real el **Stock Resultante** después del ajuste
- Colores:
  - 🟢 Verde: Stock saludable (≥ 5)
  - 🟡 Amarillo: Stock bajo (< 5)
  - 🔴 Rojo: Stock negativo o insuficiente

#### 5. Confirmar Ajuste

1. Revise todos los datos ingresados
2. Verifique el stock resultante
3. Haga clic en **Confirmar Ajuste**
4. Confirme la acción en el diálogo de confirmación
5. El sistema procesará el ajuste y mostrará el resultado

---

## 🕐 VER HISTORIAL DE MOVIMIENTOS

### Acceder al Historial

1. En la pestaña **Variaciones / Tallas**, localice la talla
2. Haga clic en el botón **Ver Historial** (🕐)
3. Se abrirá el modal con el historial completo

### Información del Historial

El historial muestra:
- **Fecha**: Fecha y hora exacta del movimiento
- **Concepto**: Tipo de movimiento
  - `AJUSTE_POSITIVO`: Entrada manual
  - `AJUSTE_NEGATIVO`: Salida manual
  - `VENTA_PUBLICO`: Venta al público
  - `RECEPCION_COMPRA`: Recepción de compra
  - `TRASPASO_ENTRADA`: Traspaso desde otra sucursal
  - `TRASPASO_SALIDA`: Traspaso a otra sucursal
  - Y más...
- **Cantidad**: 
  - ↑ Verde con flecha arriba: Ingreso
  - ↓ Rojo con flecha abajo: Salida
- **Responsable**: Usuario que realizó el movimiento
- **Observaciones**: Motivo o detalles adicionales

### Filtrar Historial

(Funcionalidad futura)
- Por rango de fechas
- Por tipo de concepto
- Por usuario responsable

---

## 📑 VER LOTES FIFO

### ¿Qué es FIFO?

FIFO (First In, First Out) es un método de gestión de inventario donde:
- La mercadería que entra primero es la que sale primero
- Cada ingreso crea un "lote" con su costo específico
- Las ventas/salidas consumen primero los lotes más antiguos

### Acceder a Lotes

1. En la pestaña **Variaciones / Tallas**, localice la talla
2. Haga clic en el botón **Ver Lotes** (📑)
3. Se abrirá la página de lotes del producto

### Información de Lotes

Para cada lote se muestra:
- **Número de Lote**: Código único
- **Cantidad Inicial**: Unidades originales del lote
- **Cantidad Disponible**: Unidades restantes
- **Costo Unitario**: Costo de compra
- **Precio Venta**: Precio asignado
- **Fecha Creación**: Cuándo se creó el lote
- **Fecha Vencimiento**: Si aplica
- **DTE Origen**: Documento asociado si existe

---

## ⚠️ VALIDACIONES Y RESTRICCIONES

### Validaciones del Sistema

#### Al Editar Producto
- ✅ El nombre no puede estar vacío
- ✅ El costo debe ser ≥ 0
- ✅ El sobreprecio debe ser ≥ 0
- ✅ El precio de venta debe ser > 0
- ✅ Las categorías y atributos deben existir

#### Al Ajustar Stock (ENTRADA)
- ✅ La cantidad debe ser > 0
- ✅ El costo unitario debe ser > 0
- ✅ El precio de venta unitario debe ser > 0
- ✅ El motivo debe tener al menos 10 caracteres

#### Al Ajustar Stock (SALIDA)
- ✅ La cantidad debe ser > 0
- ✅ La cantidad debe ser ≤ stock disponible
- ✅ El motivo debe tener al menos 10 caracteres

### Restricciones

- ❌ NO se puede editar la talla de una variación existente (solo crear nueva)
- ❌ NO se puede eliminar una variación con stock > 0
- ❌ NO se puede eliminar una variación con movimientos registrados
- ❌ NO se pueden hacer salidas mayores al stock disponible

---

## 🔐 PERMISOS Y SEGURIDAD

### Niveles de Acceso

1. **Ver Productos**: Todos los usuarios autenticados
2. **Editar Productos**: Gerentes y Administradores
3. **Ajustar Stock**: Gerentes y Administradores

### Auditoría

Todos los cambios quedan registrados:
- ✅ Usuario que realizó el cambio
- ✅ Fecha y hora exacta
- ✅ Tipo de cambio realizado
- ✅ Valores anteriores y nuevos (en historial)
- ✅ Motivo del cambio

---

## 💡 CASOS DE USO COMUNES

### Caso 1: Recibir Mercadería sin DTE

**Situación**: Compró 20 zapatillas talla 40 a un proveedor local sin factura.

**Procedimiento:**
1. Busque el producto "Zapatilla Nike Air Max"
2. Haga clic en **Editar**
3. Vaya a la pestaña **Variaciones / Tallas**
4. Localice la **Talla 40**
5. Haga clic en **Ajustar Stock**
6. Seleccione **ENTRADA**
7. Ingrese:
   - Cantidad: `20`
   - Costo Unitario: `$45.000`
   - Sobreprecio: `$15.000`
   - Precio Venta: `$60.000`
   - Motivo: `"Compra directa proveedor local, 20 unidades talla 40, factura pendiente"`
8. Haga clic en **Confirmar Ajuste**

**Resultado:**
- ✅ Se crea un lote FIFO de 20 unidades
- ✅ El stock pasa de X a X+20
- ✅ Queda registrado el movimiento con su motivo

---

### Caso 2: Producto Dañado

**Situación**: Se detectaron 3 unidades dañadas de talla 42 que deben descartarse.

**Procedimiento:**
1. Busque el producto
2. Edite → Pestaña **Variaciones / Tallas**
3. Localice **Talla 42**
4. **Ajustar Stock**
5. Seleccione **SALIDA**
6. Ingrese:
   - Cantidad: `3`
   - Motivo: `"Producto dañado durante almacenamiento, humedad detectada, se descartan 3 unidades"`
7. **Confirmar Ajuste**

**Resultado:**
- ✅ Se consumen 3 unidades del lote más antiguo (FIFO)
- ✅ El stock se reduce en 3
- ✅ Queda registro de la merma con motivo

---

### Caso 3: Corrección de Inventario

**Situación**: Al hacer inventario físico, hay 5 unidades más de lo que indica el sistema.

**Procedimiento:**
1. Edite el producto → **Variaciones / Tallas**
2. **Ajustar Stock** → **ENTRADA**
3. Ingrese:
   - Cantidad: `5`
   - Costo Unitario: Usar costo promedio actual
   - Precio Venta: Usar precio actual
   - Motivo: `"Corrección por inventario físico trimestral, diferencia de 5 unidades no registradas, posible falla en registro de compra anterior"`
4. **Confirmar Ajuste**

---

### Caso 4: Cambiar Precio de un Producto

**Situación**: Necesita aumentar el precio de venta de $70.000 a $75.000.

**Procedimiento:**
1. Edite el producto
2. En la pestaña **Datos Generales**
3. Modifique el campo **Precio Venta** a `75000`
4. Haga clic en **Guardar Cambios**

**Resultado:**
- ✅ El precio base del producto se actualiza
- ✅ Las nuevas ventas usarán el nuevo precio
- ✅ Los lotes antiguos mantienen su precio histórico

> 📝 **Nota**: El cambio de precio NO afecta los lotes FIFO existentes. Solo afecta nuevos ingresos y el precio mostrado en el sistema.

---

## ❓ PREGUNTAS FRECUENTES (FAQ)

### ¿Puedo editar el SKU de una variación?

Sí, pero actualmente esta funcionalidad está limitada. Se recomienda no cambiar SKUs para mantener consistencia.

### ¿Qué pasa si hago un ajuste de salida mayor al stock?

El sistema rechazará la operación y mostrará un error: "Stock insuficiente. Disponible: X, solicitado: Y"

### ¿Puedo eliminar una variación?

Solo si:
- ✅ El stock es 0
- ✅ No tiene movimientos registrados

De lo contrario, el sistema rechazará la eliminación.

### ¿Cómo veo quién hizo un ajuste?

En el **Historial de Movimientos**, la columna "Responsable" muestra el usuario que realizó cada cambio.

### ¿Puedo deshacer un ajuste de stock?

No directamente. Debe hacer un ajuste contrario:
- Si hizo una ENTRADA por error → Haga una SALIDA con el mismo motivo explicando la corrección
- Si hizo una SALIDA por error → Haga una ENTRADA para reponerlo

### ¿Los ajustes afectan el costo promedio?

Sí. Los ajustes de ENTRADA crean nuevos lotes que modifican el costo promedio ponderado del inventario.

### ¿Qué es el "Stock Resultante"?

Es la cantidad de stock que quedará después de aplicar el ajuste. Se calcula automáticamente mientras ingresa los datos.

---

## 🐛 SOLUCIÓN DE PROBLEMAS

### Problema: No puedo ver el botón "Editar"

**Causa**: No tiene permisos suficientes  
**Solución**: Contacte al administrador para que le otorgue permisos de edición de productos

### Problema: Al guardar dice "Error al actualizar producto"

**Causa**: Datos inválidos o campos requeridos vacíos  
**Solución**: 
1. Revise que el nombre no esté vacío
2. Verifique que el precio de venta sea > 0
3. Asegúrese de que los atributos seleccionados existan

### Problema: No se carga el historial de movimientos

**Causa**: Error de conexión o timeout  
**Solución**: 
1. Refresque la página
2. Intente nuevamente
3. Si persiste, contacte soporte técnico

### Problema: El stock resultante aparece en rojo

**Causa**: La cantidad de salida es mayor al stock disponible  
**Solución**: Reduzca la cantidad o verifique el stock actual

### Problema: No puedo hacer un ajuste de entrada

**Causa**: Falta completar campos requeridos  
**Solución**: 
1. Verifique que ingresó: Cantidad, Costo Unitario, Precio Venta
2. Asegúrese de que el motivo tenga al menos 10 caracteres

---

## 📞 SOPORTE TÉCNICO

Para asistencia adicional:
- 📧 Email: soporte@retailmind.com
- 📱 Teléfono: +56 9 XXXX XXXX
- 💬 Chat en línea: Disponible en el sistema

---

## 📚 DOCUMENTOS RELACIONADOS

- [Plan de Implementación de Edición de Productos](PLAN_EDICION_PRODUCTOS_Y_STOCK.md)
- [Guía del Sistema FIFO](GUIA_SISTEMA_FIFO.md) *(si existe)*
- [Manual de Usuario General](MANUAL_USUARIO.md) *(si existe)*

---

**Última actualización:** 2024-11-06  
**Versión:** 1.0  
**Autor:** Sistema RetailMind

