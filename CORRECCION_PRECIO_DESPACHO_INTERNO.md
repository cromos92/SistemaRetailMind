# ✅ CORRECCIÓN: Precio para Despacho Interno

## ❌ PROBLEMA

Despacho INTERNO usaba `costo + sobreprecio` ($42,990), pero la regla de negocio correcta es usar **SOLO el sobreprecio**.

---

## ✅ REGLAS DE NEGOCIO CORRECTAS

### **DESPACHO INTERNO** (Entre sucursales propias)
**Precio a usar**: **SOLO Sobreprecio**

**Razón**: Es una transferencia interna, se factura solo el margen de ganancia, no el costo.

**Ejemplo:**
```
Producto:
- Costo: $19,990
- Sobreprecio: $23,000

Despacho INTERNO:
Precio Unit. = $23,000 (SOLO sobreprecio) ✅
Subtotal = Cantidad × $23,000
```

### **DESPACHO EXTERNO** (A proveedores)
**Precio a usar**: **SOLO Costo**

**Razón**: Es para proveedores, se factura al costo sin margen.

**Ejemplo:**
```
Despacho EXTERNO:
Precio Unit. = $19,990 (SOLO costo) ✅
Subtotal = Cantidad × $19,990
```

---

## 🔧 CORRECCIÓN APLICADA

**Archivo**: `retailmind/app/templates/vistas/modulo_documentos/emisionDTE.html`  
**Función**: `getPrecioSegunMetodo` (Línea ~3067)

### **Código ANTES:**

```javascript
if (selectedMethod === 'interno') {
    const costo = parseInt(product.costo || 0);
    const sobreprecio = parseInt(product.sobreprecio || 0);
    const precioInterno = costo + sobreprecio;  // ❌
    
    return precioInterno;  // Retornaba $42,990
}
```

### **Código DESPUÉS:** ✅

```javascript
if (selectedMethod === 'interno') {
    // DESPACHO INTERNO: Usar SOLO el sobreprecio
    const sobreprecio = parseInt(product.sobreprecio || 0);
    
    console.log(`💰 Despacho INTERNO: SOLO sobreprecio = ${sobreprecio}`);
    console.log(`   (NO usar costo: ${product.costo}, NO usar precio_venta: ${product.precio_venta})`);
    
    // SIEMPRE retornar SOLO sobreprecio
    return sobreprecio;  // Retorna $23,000
}
```

---

## 📊 COMPARACIÓN

### **Producto Ejemplo:**
- Costo: $19,990
- Sobreprecio: $23,000
- Precio Venta Público: $42,990

| Tipo Despacho | ANTES | AHORA |
|---------------|-------|-------|
| **INTERNO** | $42,990 (costo + sobreprecio) ❌ | $23,000 (solo sobreprecio) ✅ |
| **EXTERNO** | $19,990 (solo costo) ✅ | $19,990 (solo costo) ✅ |

---

## 🎨 TABLA DE DETALLE

### **Despacho INTERNO:**

```
┌─────────┬─────────────┬──────────────┬────────────┐
│ Costo   │ Sobreprecio │ Precio Unit. │ Subtotal   │
├─────────┼─────────────┼──────────────┼────────────┤
│ $19,990 │  $23,000    │  **$23,000** │  $23,000   │ ← 1 unidad
│ $19,990 │  $23,000    │  **$23,000** │  $46,000   │ ← 2 unidades
│ $19,990 │  $23,000    │  **$23,000** │  $69,000   │ ← 3 unidades
└─────────┴─────────────┴──────────────┴────────────┘

Precio Unit. = Solo Sobreprecio ($23,000) ✅
```

### **Despacho EXTERNO:**

```
┌─────────┬─────────────┬──────────────┬────────────┐
│ Costo   │ Sobreprecio │ Precio Unit. │ Subtotal   │
├─────────┼─────────────┼──────────────┼────────────┤
│ $19,990 │  $23,000    │  **$19,990** │  $19,990   │ ← 1 unidad
│ $19,990 │  $23,000    │  **$19,990** │  $39,980   │ ← 2 unidades
└─────────┴─────────────┴──────────────┴────────────┘

Precio Unit. = Solo Costo ($19,990) ✅
```

---

## 📋 MATRIZ DE PRECIOS FINAL

| Tipo Despacho | Concepto | Precio Usado | Ejemplo |
|---------------|----------|--------------|---------|
| **INTERNO** | Transferencia entre sucursales | **Solo Sobreprecio** | $23,000 |
| **EXTERNO** | Despacho a proveedores | **Solo Costo** | $19,990 |

---

## 🧪 VERIFICACIÓN

### **Test 1: Despacho Interno**

```
PASOS:
1. http://localhost:8000/app/emisionDTE/
2. Seleccionar "Despacho Interno"
3. Abrir consola (F12)
4. Buscar producto
5. Agregar al detalle

LOGS ESPERADOS:
💰 getPrecioSegunMetodo - selectedMethod: interno
💰 Product prices: {precio_venta: 42990, sobreprecio: 23000, costo: 19990}
💰 Despacho INTERNO: SOLO sobreprecio = 23000
   (NO usar costo: 19990, NO usar precio_venta: 42990)

RESULTADO EN TABLA:
Costo: $19,990
Sobreprecio: $23,000
Precio Unit.: **$23,000** ✅ (solo sobreprecio)
Subtotal (1 unid): $23,000 ✅
```

### **Test 2: Despacho Externo**

```
PASOS:
1. Cambiar a "Despacho Externo"
2. Buscar mismo producto
3. Agregar al detalle

RESULTADO EN TABLA:
Costo: $19,990
Sobreprecio: $23,000
Precio Unit.: **$19,990** ✅ (solo costo)
Subtotal (1 unid): $19,990 ✅
```

---

## 💡 RAZONAMIENTO DE NEGOCIO

### **¿Por qué SOLO sobreprecio para interno?**

```
Despacho INTERNO = Transferencia entre sucursales de la misma empresa

La sucursal origen:
- Ya pagó el costo ($19,990)
- Ese costo ya está registrado en su contabilidad
- Al transferir, solo cobra el margen ($23,000)

La sucursal destino:
- Paga el sobreprecio ($23,000)
- Cuando venda al público, cobrará $42,990
- Su margen será: $42,990 - $23,000 = $19,990 (recupera el costo)
```

### **¿Por qué SOLO costo para externo?**

```
Despacho EXTERNO = Devolución/envío a proveedor

Al proveedor:
- Se le factura al costo ($19,990)
- Sin margen de ganancia
- Precio de recompra/devolución
```

---

## ✅ RESULTADO FINAL

Con esta corrección:

| Tipo | Producto | Costo | Sobreprecio | Precio Unit. Usado | Subtotal (×1) |
|------|----------|-------|-------------|--------------------|---------------|
| **INTERNO** | SANDALIA | $19,990 | $23,000 | **$23,000** | $23,000 |
| **EXTERNO** | SANDALIA | $19,990 | $23,000 | **$19,990** | $19,990 |

---

## 🚀 PRUEBA AHORA

```bash
# 1. Refrescar la página
http://localhost:8000/app/emisionDTE/

# 2. Seleccionar "Despacho Interno"

# 3. Agregar producto al detalle

# 4. Verificar en tabla de detalle:
- Costo: $19,990
- Sobreprecio: $23,000
- Precio Unit.: $23,000 ✅ (SOLO sobreprecio)
- Subtotal: $23,000 × cantidad

# 5. Cambiar a "Despacho Externo"

# 6. Agregar mismo producto

# 7. Verificar:
- Precio Unit.: $19,990 ✅ (SOLO costo)
```

---

**¡Corregido! Ahora Despacho INTERNO usa SOLO el sobreprecio ($23,000).** ✅
