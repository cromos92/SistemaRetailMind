# 💰 Mejora: Importar DTEs con Monto Total (con IVA)

## 🎯 Problema Original

**Antes:**
- ❌ Solo aceptaba `monto_neto`
- ❌ Tenías que calcular manualmente el neto
- ❌ Error común: confundir monto total con monto neto

## ✅ Solución Implementada

**Ahora:**
- ✅ Acepta `monto_con_iva` (monto total de la factura)
- ✅ También acepta `monto_neto` si lo prefieres
- ✅ Calcula automáticamente el valor faltante

## 📊 Cómo Funciona

### **Opción 1: Usar Monto con IVA (Recomendado)** ⭐

**Formato CSV:**
```csv
rut_proveedor,numero_documento,tipo_documento,fecha_emision,monto_con_iva,dias_credito,bultos,unidades,referencias
76123456-7,12345,33,2024-12-11,119000,30,2,50,OC-001
```

**Cálculo Automático:**
```
Input: monto_con_iva = 119,000
       ↓
Cálculo: monto_neto = 119,000 / 1.19 = 100,000
         iva = 119,000 - 100,000 = 19,000
       ↓
Guardado: monto_neto = 100,000
          monto_con_iva = 119,000
```

### **Opción 2: Usar Monto Neto**

**Formato CSV:**
```csv
rut_proveedor,numero_documento,tipo_documento,fecha_emision,monto_neto,dias_credito,bultos,unidades,referencias
76123456-7,12345,33,2024-12-11,100000,30,2,50,OC-001
```

**Cálculo Automático:**
```
Input: monto_neto = 100,000
       ↓
Cálculo: iva = 100,000 × 0.19 = 19,000
         total = 100,000 + 19,000 = 119,000
       ↓
Guardado: monto_neto = 100,000
          monto_con_iva = 119,000
```

## 🎁 Beneficios

### **Más Flexible:**
- ✅ Usa el dato que tengas disponible
- ✅ No necesitas calculadora
- ✅ Menos errores

### **Más Realista:**
- ✅ En facturas reales, el monto destacado es el total (con IVA)
- ✅ Es el número que ves primero
- ✅ No necesitas sacar calculadora

### **Inteligente:**
- ✅ Prioriza `monto_neto` si está presente
- ✅ Usa `monto_con_iva` si no hay neto
- ✅ Calcula automáticamente el faltante

## 📝 Ejemplos Prácticos

### **Ejemplo 1: Factura de $119.000**

```csv
rut_proveedor,numero_documento,monto_con_iva
76123456-7,12345,119000
```

**Resultado:**
```
✅ DTE creado
   Monto Neto: $100.000 (calculado)
   IVA: $19.000 (calculado)
   Total: $119.000 (ingresado)
```

### **Ejemplo 2: Factura de $100.000 + IVA**

```csv
rut_proveedor,numero_documento,monto_neto
76123456-7,12345,100000
```

**Resultado:**
```
✅ DTE creado
   Monto Neto: $100.000 (ingresado)
   IVA: $19.000 (calculado)
   Total: $119.000 (calculado)
```

### **Ejemplo 3: Archivo Mixto**

```csv
rut_proveedor,numero_documento,monto_con_iva,monto_neto
76123456-7,12345,119000,
77234567-8,12346,,100000
```

**Resultado:**
```
✅ Fila 1: Usa monto_con_iva (119,000) → Calcula neto
✅ Fila 2: Usa monto_neto (100,000) → Calcula total
```

## 🔧 Nombres de Columna Aceptados

### **Para Monto con IVA:**
- `monto_con_iva` ⭐ Recomendado
- `total`
- `monto_total`

### **Para Monto Neto:**
- `monto_neto` ⭐ Recomendado
- `subtotal`

**El sistema busca en ese orden y usa el primero que encuentre.**

## 📊 Formato Actualizado

### **Modo RUT (9 columnas):**
```csv
rut_proveedor,numero_documento,tipo_documento,fecha_emision,monto_con_iva,dias_credito,bultos,unidades,referencias
76123456-7,12345,33,2024-12-11,119000,30,2,50,Orden de Compra 001
77234567-8,12346,33,2024-12-10,297500,45,5,100,Orden de Compra 002
```

### **Modo ID (9 columnas):**
```csv
id_proveedor,numero_documento,tipo_documento,fecha_emision,monto_con_iva,dias_credito,bultos,unidades,referencias
1,12345,33,2024-12-11,119000,30,2,50,Orden de Compra 001
2,12346,33,2024-12-10,297500,45,5,100,Orden de Compra 002
```

## 💡 Casos de Uso

### **Caso 1: Tienes el Monto Total**

La factura dice: **TOTAL: $119.000**

```
1. Descargar formato
2. Columna "monto_con_iva": 119000
3. Importar
✅ Sistema calcula neto: $100.000
✅ Sistema calcula IVA: $19.000
```

### **Caso 2: Tienes el Desglose**

La factura dice: **Neto: $100.000, IVA: $19.000, Total: $119.000**

**Opción A (más fácil):**
```
Columna "monto_con_iva": 119000
```

**Opción B:**
```
Columna "monto_neto": 100000
```

Ambas funcionan! ✅

### **Caso 3: Exportación de Otro Sistema**

Tu sistema antiguo exporta el monto total:

```csv
proveedor,factura,total
76123456-7,12345,119000
```

Solo renombrar columnas:
```csv
rut_proveedor,numero_documento,monto_con_iva
76123456-7,12345,119000
```

¡Y listo! ✅

## 🔍 Validación

El sistema valida:
```
✓ Debe tener monto_con_iva O monto_neto
✓ No puede estar vacío
✓ Debe ser un número válido
✓ Puede tener comas (se convierten a puntos)
```

## 📈 Comparación

| Aspecto | Antes | Ahora |
|---------|-------|-------|
| Campo obligatorio | `monto_neto` | `monto_con_iva` o `monto_neto` |
| Cálculo | IVA y total | Neto o total según entrada |
| Flexibilidad | Baja | Alta ⭐ |
| Errores | Frecuentes | Reducidos ⭐ |
| Uso común | Poco intuitivo | Muy intuitivo ⭐ |

## ✅ Ventajas

### **Para el Usuario:**
- 🎯 Usa el dato más visible de la factura (el total)
- 📊 No necesita calculadora
- ⚡ Más rápido de completar
- ✅ Menos errores

### **Para el Sistema:**
- 🔄 Mantiene cálculos precisos
- 💯 IVA exacto (usando división)
- 📐 Redondeo correcto
- ✅ Integridad de datos

## 🎉 Resumen

**Cambio Principal:**
```
❌ Antes: Obligatorio "monto_neto"
✅ Ahora: "monto_con_iva" (recomendado) O "monto_neto"
```

**Formato de ejemplo actualizado:**
- ✅ Usa `monto_con_iva` por defecto
- ✅ Ejemplos con montos totales reales
- ✅ Más intuitivo para usuarios

**¡Listo para usar!** 🚀

Ahora puedes importar DTEs usando directamente el monto total de tus facturas, sin necesidad de calcular el monto neto.
