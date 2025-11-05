# 🎯 CASOS DE USO: SISTEMA DE COMPRAS Y PRODUCTOS

## 📦 CASO 1: PRIMERA COMPRA DE ZAPATILLAS NIKE

### **Contexto:**
Compra de 30 pares de zapatillas Nike Air Max en 3 tallas diferentes.

### **Paso 1: Crear Compra Global** 🛒

**Usuario:** Gerente de Compras  
**Ubicación:** `/app/verGestionCompras/`

```
COMPRA #1234
├─ Nombre: "Compra Zapatillas Verano 2025"
├─ Temporada: "Verano 2025"
├─ Proveedor: Nike Chile S.A.
├─ Fecha: 15/01/2025
└─ Observaciones: "Primera compra de la temporada"
```

### **Paso 2: Subir CSV con Productos** 📄

**Archivo:** `compra_nike_enero.csv`

```csv
Nombre,Descripcion,Marca,Color,Genero,Costo,PrecioSugerido,Stock,Talla
Nike Air Max,Zapatilla deportiva running,Nike,Negro,Unisex,25000,45000,10,40
Nike Air Max,Zapatilla deportiva running,Nike,Negro,Unisex,25000,45000,12,41
Nike Air Max,Zapatilla deportiva running,Nike,Negro,Unisex,25000,45000,8,42
```

**Resultado en BD:**

```python
# Se crea 1 Compras_Producto (agrupa por atributos comunes)
Compras_Producto #501
├─ nombre: "Nike Air Max"
├─ descripcion: "Zapatilla deportiva running"
├─ atributo1: "Nike"        # Marca
├─ atributo2: "Negro"       # Color
├─ atributo3: "Unisex"      # Género
├─ costo: 25000
└─ precioSugerido: 45000

# Se crean 3 Compras_Producto_Talla (una por talla)
Compras_Producto_Talla #801
├─ compra_producto: #501
├─ talla: "40"
└─ stock: 10

Compras_Producto_Talla #802
├─ compra_producto: #501
├─ talla: "41"
└─ stock: 12

Compras_Producto_Talla #803
├─ compra_producto: #501
├─ talla: "42"
└─ stock: 8
```

### **Paso 3: Recepcionar Mercadería** 📥

**Fecha:** 25/01/2025 (10 días después)  
**Usuario:** Bodeguero  
**Ubicación:** `/app/verGestionCompras/` → Modal "Recepcionar"

**Escenario:** Llegan solo 28 de 30 pares (2 faltantes en talla 42)

```javascript
// Datos de recepción
{
    compra_id: 1234,
    recepciones: [
        {
            compra_producto_talla_id: 801,
            recepcionado: 10,          // ✅ Llegaron todos (talla 40)
            factura_id: 5001
        },
        {
            compra_producto_talla_id: 802,
            recepcionado: 12,          // ✅ Llegaron todos (talla 41)
            factura_id: 5001
        },
        {
            compra_producto_talla_id: 803,
            recepcionado: 6,           // ⚠️ Faltaron 2 (talla 42)
            factura_id: 5001
        }
    ]
}
```

**Resultado en BD:**

```python
# Se crean 3 Productos_Recepcionados
Productos_Recepcionados #901
├─ compra_producto_talla: #801 (talla 40)
├─ producto_talla: NULL             # ⚠️ Aún no se crea producto
├─ stockArribado: 10
├─ dte: FAC-5001
├─ fecha_recepcion: 2025-01-25 14:30:00
└─ recepcionado_por: "Pedro Bodeguero"

Productos_Recepcionados #902
├─ compra_producto_talla: #802 (talla 41)
├─ stockArribado: 12
└─ ...

Productos_Recepcionados #903
├─ compra_producto_talla: #803 (talla 42)
├─ stockArribado: 6              # ⚠️ Solo 6 de 8
├─ cantidad_esperada: 8
├─ cantidad_faltante: 2
└─ observaciones: "Faltaron 2 pares en el despacho"
```

### **Paso 4: Crear Productos en Catálogo** 🏭

**Ubicación:** `/app/verGestionProducto/`  
**Usuario:** Encargado de Productos

**Vista de Productos Pendientes:**

```
┌─────────────────────────────────────────────────────────────────┐
│ PRODUCTOS PENDIENTES DE CREAR                                   │
├─────────────────────────────────────────────────────────────────┤
│ Nombre         │ Marca │ Color │ Estado    │ Stock │ Acción    │
├─────────────────────────────────────────────────────────────────┤
│ Nike Air Max   │ Nike  │ Negro │ Pendiente │ 28    │ [Crear]   │
└─────────────────────────────────────────────────────────────────┘
```

**Al hacer clic en "Crear":**

```javascript
// Modal de creación
{
    producto_compra_id: 501,
    articulo: "Nike Air Max",
    descripcion: "Zapatilla deportiva running",
    atributo1_id: 15,        // Nike (de AtributoOpcion)
    atributo2_id: 48,        // Negro
    atributo3_id: 3,         // Unisex
    categoria_id: 2,         // Zapatillas
    costo: 25000,
    sobreprecio: 5000,
    precioventa: 45000,
    
    // Tallas (se generan SKUs automáticamente)
    tallas: [
        { talla: "40", stock: 10, sku: 100001 },
        { talla: "41", stock: 12, sku: 100002 },
        { talla: "42", stock: 6,  sku: 100003 }
    ]
}
```

**Resultado en BD:**

```python
# 1. Se crea Producto principal
Producto #1501
├─ articulo: "Nike Air Max"
├─ descripcion: "Zapatilla deportiva running"
├─ atributo1: FK → AtributoOpcion #15 (Nike)
├─ atributo2: FK → AtributoOpcion #48 (Negro)
├─ atributo3: FK → AtributoOpcion #3 (Unisex)
├─ categoria: FK → Categoria #2 (Zapatillas)
├─ sucursal: FK → Sucursal #1 (Casa Matriz)
├─ costo: 25000
├─ sobreprecio: 5000
├─ precioventa: 45000
└─ precioSugerido: 45000

# 2. Se crean 3 Producto_Talla
Producto_Talla #2001
├─ producto: #1501
├─ sku: 100001
├─ talla: "40"
└─ stock: 10

Producto_Talla #2002
├─ producto: #1501
├─ sku: 100002
├─ talla: "41"
└─ stock: 12

Producto_Talla #2003
├─ producto: #1501
├─ sku: 100003
├─ talla: "42"
└─ stock: 6

# 3. Se crean 3 Movimientos_Producto
Movimientos_Producto #3001
├─ ProductoTalla: #2001 (SKU 100001)
├─ dte: #5001 (FAC-5001)
├─ cantidad: 10
├─ costo: 25000
├─ precio: 45000
├─ concepto: "INGRESO_INICIAL"
├─ tipo_movimiento: "INGRESO"
├─ responsable: "Admin"
└─ fecha: 2025-01-25

Movimientos_Producto #3002
├─ ProductoTalla: #2002
├─ cantidad: 12
└─ ...

Movimientos_Producto #3003
├─ ProductoTalla: #2003
├─ cantidad: 6
└─ ...

# 4. Se crean 3 Lotes FIFO
ProductoLoteFIFO #4001
├─ producto_talla: #2001
├─ cantidad_inicial: 10
├─ cantidad_disponible: 10
├─ costo_unitario: 25000
├─ precio_venta_unitario: 45000
└─ fecha_creacion: 2025-01-25

# ... (similar para talla 41 y 42)

# 5. Se actualizan Productos_Recepcionados (LINKEO)
UPDATE Productos_Recepcionados
SET producto_talla_id = 2001  # ← AQUÍ SE LINKEA
WHERE id = 901;

UPDATE Productos_Recepcionados
SET producto_talla_id = 2002
WHERE id = 902;

UPDATE Productos_Recepcionados
SET producto_talla_id = 2003
WHERE id = 903;
```

### **Estado Final:**

```
TRAZABILIDAD COMPLETA:
═══════════════════════════════════════════════════════════════

Compra #1234 "Compra Zapatillas Verano 2025"
│
├─ Compras_Producto #501 "Nike Air Max"
│   │
│   ├─ Compras_Producto_Talla #801 (Talla 40)
│   │   │
│   │   └─ Productos_Recepcionados #901
│   │       ├─ stockArribado: 10
│   │       ├─ dte: FAC-5001
│   │       └─ producto_talla: #2001 ✅ LINKEADO
│   │           │
│   │           ├─ Producto_Talla #2001 (SKU 100001)
│   │           │   ├─ stock: 10
│   │           │   └─ Movimientos_Producto #3001 ✅
│   │           │       └─ Lote FIFO #4001 ✅
│   │
│   ├─ Compras_Producto_Talla #802 (Talla 41)
│   │   └─ Productos_Recepcionados #902 → Producto_Talla #2002 ✅
│   │
│   └─ Compras_Producto_Talla #803 (Talla 42)
│       └─ Productos_Recepcionados #903 → Producto_Talla #2003 ✅
```

---

## 🔄 CASO 2: PRODUCTO QUE VUELVE A LLEGAR (2DA COMPRA)

### **Contexto:**
2 meses después, se hace una segunda compra del mismo producto, pero con **precio aumentado**.

### **Nueva Compra:**

**Fecha:** 20/03/2025  
**Proveedor:** Nike Chile S.A.  
**CSV:**

```csv
Nombre,Descripcion,Marca,Color,Genero,Costo,PrecioSugerido,Stock,Talla
Nike Air Max,Zapatilla deportiva running,Nike,Negro,Unisex,27000,48000,15,40
Nike Air Max,Zapatilla deportiva running,Nike,Negro,Unisex,27000,48000,18,41
Nike Air Max,Zapatilla deportiva running,Nike,Negro,Unisex,27000,48000,10,42
```

**⚠️ Notas:**
- Costo subió de $25,000 → $27,000 (+8%)
- Precio sugerido subió de $45,000 → $48,000 (+6.7%)
- Cantidades diferentes

### **Paso 1: Recepción (20/03/2025)**

```python
# Se crean nuevas recepciones
Productos_Recepcionados #1001
├─ compra_producto_talla: #1201 (nueva compra, talla 40)
├─ stockArribado: 15
├─ dte: FAC-5150
├─ producto_talla: NULL  # ⚠️ Aún sin linkear
└─ fecha_recepcion: 2025-03-20
```

### **Paso 2: Al Ir a Crear Producto** 🤔

**SITUACIÓN ACTUAL EN TU SISTEMA:**
```javascript
// El usuario ve en la tabla de "Productos Para Crear":
{
    nombre: "Nike Air Max",
    marca: "Nike",
    color: "Negro",
    genero: "Unisex",
    stock_total: 43,  // 15 + 18 + 10
    costo: 27000,     // Nuevo costo
    estado: "Pendiente"
}
```

**⚠️ PROBLEMA:** 
El sistema NO detecta automáticamente que este producto ya existe.

### **ESCENARIOS POSIBLES:**

#### **Escenario A: Usuario Crea Producto Duplicado** ❌

```python
# Se crearía un segundo producto idéntico:
Producto #1600 (DUPLICADO)
├─ articulo: "Nike Air Max"  # ⚠️ Mismo nombre
├─ atributo1: Nike           # ⚠️ Mismos atributos
├─ atributo2: Negro
├─ atributo3: Unisex
├─ costo: 27000             # Costo nuevo
├─ precioventa: 48000       # Precio nuevo
└─ ...

# Ahora tendrías 2 productos iguales:
SKU 100001 - Nike Air Max Negro 40 - $25,000 - Stock: 10
SKU 100201 - Nike Air Max Negro 40 - $27,000 - Stock: 15  ❌
```

**PROBLEMA:**
- Productos duplicados en catálogo
- Confusión en ventas
- Inventario dividido artificialmente

#### **Escenario B: Usuario Busca y Actualiza Existente** ✅

**PROCESO MANUAL ACTUAL:**

1. Usuario busca en catálogo si existe
2. Encuentra Producto #1501
3. Decide qué hacer:

**Opción 1: Actualizar Costo/Precio**
```python
# Actualizar producto existente
Producto #1501
├─ costo: 27000           # ← Cambiado de 25000
├─ precioventa: 48000     # ← Cambiado de 45000
└─ ...

# Agregar stock a tallas existentes
Producto_Talla #2001 (talla 40)
├─ stock: 10 + 15 = 25   # ← Suma

# Crear nuevo movimiento
Movimientos_Producto #3010
├─ ProductoTalla: #2001
├─ cantidad: 15           # Nueva entrada
├─ costo: 27000          # ⚠️ Nuevo costo
├─ concepto: "COMPRA"
└─ dte: FAC-5150

# Crear nuevo lote FIFO
ProductoLoteFIFO #4010
├─ producto_talla: #2001
├─ cantidad_inicial: 15
├─ costo_unitario: 27000  # ← Costo más alto
└─ ...
```

**RESULTADO:**
```
Producto_Talla #2001 (SKU 100001 - Talla 40)
├─ Stock actual: 25
│
├─ Lote #4001 (Viejo)
│   ├─ Cantidad disponible: 10
│   ├─ Costo unitario: $25,000
│   └─ Fecha: 2025-01-25
│
└─ Lote #4010 (Nuevo)
    ├─ Cantidad disponible: 15
    ├─ Costo unitario: $27,000
    └─ Fecha: 2025-03-20
```

**Al Vender:**
```python
# Venta de 12 pares talla 40
# FIFO consume lotes más antiguos primero:

# Se consumen 10 del Lote #4001 (viejo, $25,000)
costo_vendido_1 = 10 × $25,000 = $250,000

# Se consumen 2 del Lote #4010 (nuevo, $27,000)
costo_vendido_2 = 2 × $27,000 = $54,000

# Costo total de la venta
costo_total = $304,000
costo_promedio = $304,000 / 12 = $25,333

# Stock restante: 13
# Lote #4001: 0 (agotado)
# Lote #4010: 13 (quedan del nuevo)
```

---

## 💡 CASO 3: CONSULTAS DE TRAZABILIDAD

### **Pregunta 1: ¿Qué llegó de la Compra #1234?**

```python
# Query
recepciones = Productos_Recepcionados.objects.filter(
    compra_producto_talla__compra_producto__compras_id=1234
).select_related(
    'compra_producto_talla__compra_producto',
    'producto_talla',
    'dte'
)

# Resultado
for r in recepciones:
    print(f"""
    Producto: {r.compra_producto_talla.compra_producto.nombre}
    Talla: {r.compra_producto_talla.talla}
    Esperado: {r.compra_producto_talla.stock}
    Recibido: {r.stockArribado}
    Factura: {r.dte.numero_documento if r.dte else 'N/A'}
    Estado: {'Creado' if r.producto_talla else 'Pendiente'}
    SKU: {r.producto_talla.sku if r.producto_talla else 'N/A'}
    """)
```

**Output:**
```
Producto: Nike Air Max
Talla: 40
Esperado: 10
Recibido: 10
Factura: FAC-5001
Estado: Creado
SKU: 100001

Producto: Nike Air Max
Talla: 41
Esperado: 12
Recibido: 12
Factura: FAC-5001
Estado: Creado
SKU: 100002

Producto: Nike Air Max
Talla: 42
Esperado: 8
Recibido: 6  ⚠️ FALTANTE
Factura: FAC-5001
Estado: Creado
SKU: 100003
```

### **Pregunta 2: ¿Cuántas veces llegó Nike Air Max Talla 40?**

```python
# Query para detectar múltiples recepciones
recepciones_multiples = Productos_Recepcionados.objects.filter(
    compra_producto_talla__compra_producto__nombre__icontains='Nike Air Max',
    compra_producto_talla__talla='40'
).select_related('dte').order_by('fecha_recepcion')

# Resultado
print(f"Nike Air Max Talla 40 llegó {recepciones_multiples.count()} veces:")
for i, r in enumerate(recepciones_multiples, 1):
    print(f"""
    Recepción #{i}:
    - Fecha: {r.fecha_recepcion}
    - Cantidad: {r.stockArribado}
    - Factura: {r.dte.numero_documento}
    - Costo unitario: ${r.compra_producto_talla.compra_producto.costo:,}
    """)
```

**Output:**
```
Nike Air Max Talla 40 llegó 2 veces:

Recepción #1:
- Fecha: 2025-01-25
- Cantidad: 10
- Factura: FAC-5001
- Costo unitario: $25,000

Recepción #2:
- Fecha: 2025-03-20
- Cantidad: 15
- Factura: FAC-5150
- Costo unitario: $27,000
```

### **Pregunta 3: ¿Qué productos están pendientes de crear?**

```python
# Query
pendientes = Productos_Recepcionados.objects.filter(
    producto_talla__isnull=True  # No tienen producto creado
).select_related(
    'compra_producto_talla__compra_producto'
).values(
    'compra_producto_talla__compra_producto__nombre',
    'compra_producto_talla__compra_producto__atributo1',
    'compra_producto_talla__compra_producto__atributo2'
).annotate(
    total_stock=Sum('stockArribado'),
    valor_total=Sum(
        F('stockArribado') * F('compra_producto_talla__compra_producto__costo')
    )
)

# Resultado
print("PRODUCTOS PENDIENTES DE CREAR:")
print("=" * 80)
for p in pendientes:
    print(f"""
    {p['nombre']} ({p['atributo1']} - {p['atributo2']})
    Stock total: {p['total_stock']}
    Valor inventario: ${p['valor_total']:,}
    """)
```

### **Pregunta 4: Historial completo de un SKU**

```python
# Query de movimientos de un SKU
sku = '100001'
producto_talla = Producto_Talla.objects.get(sku=sku)

movimientos = Movimientos_Producto.objects.filter(
    ProductoTalla=producto_talla
).select_related('dte', 'ticket').order_by('fecha', 'hora')

print(f"HISTORIAL DE SKU {sku} - {producto_talla.producto.articulo}")
print("=" * 100)
print(f"{'Fecha':<12} {'Concepto':<20} {'Cant':<6} {'Costo':<10} {'Doc':<15} {'Stock':<8}")
print("-" * 100)

stock_acumulado = 0
for m in movimientos:
    stock_acumulado += m.cantidad
    doc = m.dte.numero_documento if m.dte else (
        m.ticket.correlativo if m.ticket else 'N/A'
    )
    print(f"{m.fecha} {m.concepto:<20} {m.cantidad:>5} ${m.costo:<9,} {doc:<15} {stock_acumulado:>7}")
```

**Output:**
```
HISTORIAL DE SKU 100001 - Nike Air Max
════════════════════════════════════════════════════════════════════════════════════════════════
Fecha        Concepto             Cant   Costo      Doc             Stock   
────────────────────────────────────────────────────────────────────────────────────────────────
2025-01-25   INGRESO_INICIAL        10   $25,000    FAC-5001             10
2025-02-05   VENTA                  -2   $25,000    TKT-001               8
2025-02-12   VENTA                  -1   $25,000    TKT-045               7
2025-03-20   COMPRA                 15   $27,000    FAC-5150             22
2025-03-25   VENTA                  -3   $25,000    TKT-089              19
2025-04-01   AJUSTE_NEGATIVO        -1   $25,000    N/A                  18
2025-04-10   VENTA                  -5   $27,000    TKT-120              13
```

---

## 🎨 CASO 4: FLUJO COMPLETO CON INTERFAZ

### **Dashboard de Compras**

```
┌────────────────────────────────────────────────────────────────────┐
│ 📦 GESTIÓN DE COMPRAS - ENERO 2025                                 │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  [Crear Compra]  [Importar CSV]  [Ver Dashboard]                  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ COMPRAS DEL AÑO 2025                                         │ │
│  ├──────────────────────────────────────────────────────────────┤ │
│  │ ID   │ Nombre             │ Proveedor   │ Estado   │ Acción  │ │
│  ├──────────────────────────────────────────────────────────────┤ │
│  │ 1234 │ Zapatillas Verano  │ Nike Chile  │ ✅ Total │ [Ver]   │ │
│  │      │                    │             │          │ [📦]    │ │
│  ├──────────────────────────────────────────────────────────────┤ │
│  │ 1235 │ Ropa Deportiva     │ Adidas      │ ⚠️ Parc. │ [Ver]   │ │
│  │      │                    │             │          │ [📦]    │ │
│  └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘

Al hacer clic en [📦] (Recepcionar):

┌────────────────────────────────────────────────────────────────────┐
│ 📥 RECEPCIONAR COMPRA #1234                                        │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  Nombre: Compra Zapatillas Verano 2025                            │
│  Proveedor: Nike Chile S.A.                                       │
│  Factura: [Seleccionar DTE ▼] FAC-5001                           │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ Producto       │ Talla │ Pedido │ Recibido │ Estado          │ │
│  ├──────────────────────────────────────────────────────────────┤ │
│  │ Nike Air Max   │ 40    │ 10     │ [10   ]  │ ✅ Completo     │ │
│  │ Nike Air Max   │ 41    │ 12     │ [12   ]  │ ✅ Completo     │ │
│  │ Nike Air Max   │ 42    │ 8      │ [6    ]  │ ⚠️ Faltante (2) │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  Observaciones:                                                   │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ Faltaron 2 pares talla 42 en el despacho                  │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                    │
│            [Cancelar]  [Guardar Recepción]                        │
└────────────────────────────────────────────────────────────────────┘
```

### **Dashboard de Productos**

```
┌────────────────────────────────────────────────────────────────────┐
│ 🏭 GESTIÓN DE PRODUCTOS                                            │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  📊 RESUMEN                                                        │
│  ┌──────────┬──────────┬──────────┬─────────────┐                │
│  │ Totales  │ Creados  │Pendientes│ Valor Pend. │                │
│  ├──────────┼──────────┼──────────┼─────────────┤                │
│  │   156    │   128    │    28    │ $15,600,000 │                │
│  └──────────┴──────────┴──────────┴─────────────┘                │
│                                                                    │
│  FILTROS: [Estado: Pendientes ▼] [Año: 2025 ▼]                   │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ Producto     │ Marca │ Stock │ Costo    │ Estado  │ Acción   │ │
│  ├──────────────────────────────────────────────────────────────┤ │
│  │ Nike Air Max │ Nike  │ 28    │ $25,000  │ ⚠️ Pend │ [Crear]  │ │
│  │ Polera Run   │ Adidas│ 50    │ $8,000   │ ⚠️ Pend │ [Crear]  │ │
│  │ Short Sport  │ Puma  │ 30    │ $12,000  │ ✅ Cread│ [Ver]    │ │
│  └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘

Al hacer clic en [Crear]:

┌────────────────────────────────────────────────────────────────────┐
│ ➕ CREAR PRODUCTO DESDE RECEPCIÓN                                  │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ⚠️ ALERTA: Se encontró 1 producto similar                        │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ ⚠️ Nike Air Max - Negro - Unisex                           │   │
│  │ SKU: 100001, 100002, 100003                                │   │
│  │ Stock actual: 18 unidades                                  │   │
│  │ Costo actual: $25,000                                      │   │
│  │                                                             │   │
│  │ Opciones:                                                   │   │
│  │ ( ) Actualizar producto existente (agregar stock)          │   │
│  │ (•) Crear como producto nuevo                              │   │
│  │                                                             │   │
│  │ [Ver Producto Existente]  [Continuar]                      │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                    │
│  INFORMACIÓN DEL PRODUCTO                                         │
│  Nombre:       [Nike Air Max                              ]       │
│  Descripción:  [Zapatilla deportiva running              ]       │
│                                                                    │
│  ATRIBUTOS                                                         │
│  Marca:        [Nike         ▼]                                   │
│  Color:        [Negro        ▼]                                   │
│  Género:       [Unisex       ▼]                                   │
│  Categoría:    [Zapatillas   ▼]                                   │
│                                                                    │
│  PRECIOS                                                           │
│  Costo:        [$27,000  ] ⚠️ +8% vs. último                      │
│  Sobreprecio:  [$5,000   ]                                        │
│  Precio Venta: [$48,000  ] ⚠️ +6.7% vs. último                    │
│                           Margen: 77.7%                            │
│                                                                    │
│  TALLAS Y STOCK                                                    │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ Talla │ Stock │ SKU    │ Estado                           │   │
│  ├────────────────────────────────────────────────────────────┤   │
│  │ 40    │ 15    │[Auto ] │ ✅ Disponible                    │   │
│  │ 41    │ 18    │[Auto ] │ ✅ Disponible                    │   │
│  │ 42    │ 10    │[Auto ] │ ✅ Disponible                    │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                    │
│            [Cancelar]  [Crear Producto]                           │
└────────────────────────────────────────────────────────────────────┘
```

---

## 📈 CASO 5: ANÁLISIS Y REPORTES

### **Reporte: Rotación FIFO**

```sql
-- Productos con múltiples lotes
SELECT 
    pt.sku,
    p.articulo,
    pt.talla,
    pt.stock as stock_actual,
    COUNT(pf.id) as num_lotes,
    MIN(pf.costo_unitario) as costo_minimo,
    MAX(pf.costo_unitario) as costo_maximo,
    SUM(pf.cantidad_disponible) as stock_en_lotes
FROM Producto_Talla pt
JOIN Producto p ON pt.producto_id = p.id
LEFT JOIN ProductoLoteFIFO pf ON pf.producto_talla_id = pt.id
WHERE pf.cantidad_disponible > 0
GROUP BY pt.id
HAVING num_lotes > 1
ORDER BY num_lotes DESC;
```

**Resultado:**
```
┌─────────┬──────────────┬───────┬──────────┬───────────┬──────────────┬──────────────┬───────────────┐
│ SKU     │ Artículo     │ Talla │ Stock    │ Num Lotes │ Costo Mínimo │ Costo Máximo │ Stock en Lotes│
├─────────┼──────────────┼───────┼──────────┼───────────┼──────────────┼──────────────┼───────────────┤
│ 100001  │ Nike Air Max │ 40    │ 25       │ 2         │ $25,000      │ $27,000      │ 25            │
│ 100002  │ Nike Air Max │ 41    │ 30       │ 2         │ $25,000      │ $27,000      │ 30            │
│ 100003  │ Nike Air Max │ 42    │ 16       │ 2         │ $25,000      │ $27,000      │ 16            │
└─────────┴──────────────┴───────┴──────────┴───────────┴──────────────┴──────────────┴───────────────┘
```

### **Reporte: Productos con Discrepancias**

```sql
-- Productos donde llegó menos de lo esperado
SELECT 
    cp.nombre,
    cpt.talla,
    cpt.stock as esperado,
    pr.stockArribado as recibido,
    (cpt.stock - pr.stockArribado) as faltante,
    pr.dte_id,
    pr.observaciones
FROM Productos_Recepcionados pr
JOIN Compras_Producto_Talla cpt ON pr.compra_producto_talla_id = cpt.id
JOIN Compras_Producto cp ON cpt.compra_producto_id = cp.id
WHERE cpt.stock != pr.stockArribado;
```

**Resultado:**
```
┌──────────────┬───────┬──────────┬──────────┬──────────┬────────┬─────────────────────┐
│ Nombre       │ Talla │ Esperado │ Recibido │ Faltante │ DTE    │ Observaciones       │
├──────────────┼───────┼──────────┼──────────┼──────────┼────────┼─────────────────────┤
│ Nike Air Max │ 42    │ 8        │ 6        │ 2        │ 5001   │ Faltaron en despacho│
│ Polera Run   │ M     │ 20       │ 18       │ 2        │ 5002   │ 2 con manchas       │
│ Short Sport  │ L     │ 15       │ 17       │ -2       │ 5003   │ Enviaron de más     │
└──────────────┴───────┴──────────┴──────────┴──────────┴────────┴─────────────────────┘
```

### **Reporte: Valorización de Inventario**

```python
# Calcular valor real del inventario con FIFO
from django.db.models import Sum, F

valor_fifo = ProductoLoteFIFO.objects.filter(
    cantidad_disponible__gt=0
).aggregate(
    valor_total=Sum(F('cantidad_disponible') * F('costo_unitario'))
)

print(f"Valor Inventario FIFO: ${valor_fifo['valor_total']:,}")

# Comparar con costo actual del producto
valor_actual = Producto_Talla.objects.aggregate(
    valor_total=Sum(F('stock') * F('producto__costo'))
)

print(f"Valor con Costo Actual: ${valor_actual['valor_total']:,}")

diferencia = valor_fifo['valor_total'] - valor_actual['valor_total']
print(f"Diferencia: ${diferencia:,}")
```

**Output:**
```
Valor Inventario FIFO:  $5,245,000
Valor con Costo Actual: $5,380,000
Diferencia:             -$135,000

⚠️ El inventario vale $135,000 menos con FIFO
   porque se están consumiendo primero los lotes más baratos.
```

---

## 🎯 CONCLUSIÓN DE CASOS DE USO

### **Lo que el sistema HACE correctamente:**

1. ✅ Registra todas las recepciones con detalle
2. ✅ Mantiene trazabilidad completa desde CSV hasta venta
3. ✅ Sistema FIFO funcional para múltiples costos
4. ✅ Detecta faltantes y sobrantes
5. ✅ Vincula todo mediante `Productos_Recepcionados`

### **Lo que el sistema NECESITA:**

1. ⚠️ Detección automática de productos duplicados
2. ⚠️ Sugerencias al recibir producto existente
3. ⚠️ Política clara de actualización de precios
4. ⚠️ Alertas visuales de productos similares
5. ⚠️ Dashboard de discrepancias y faltantes

---

**Generado:** 05 de Noviembre 2025  
**Para:** Sistema RetailMind

