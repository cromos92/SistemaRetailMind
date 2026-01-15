# TABLA COMPARATIVA - DOCUMENTOS TRIBUTARIOS ELECTRÓNICOS

## COMPARACIÓN RÁPIDA DE TIPOS DE DTE

| Característica | Factura (33) | Factura Exenta (34) | Boleta (39) | Boleta Exenta (41) | Guía Despacho (52) | Nota Crédito (61) | Nota Débito (56) |
|----------------|-------------|---------------------|-------------|-------------------|-------------------|------------------|-----------------|
| **Tipo de Operación** | Venta B2B con IVA | Venta B2B sin IVA | Venta a consumidor final con IVA | Venta a consumidor final sin IVA | Traslado de mercaderías | Anulación/devolución | Cobro adicional |
| **RUT Receptor** | RUT empresa real | RUT empresa real | Puede ser 66.666.666-6 | Puede ser 66.666.666-6 | RUT real | RUT real | RUT real |
| **Incluye IVA** | ✅ SÍ (19%) | ❌ NO | ✅ SÍ (19%) | ❌ NO | ❌ NO | Según doc original | Según doc original |
| **Datos Receptor Completos** | ✅ Obligatorios | ✅ Obligatorios | ⚠️ Opcionales | ⚠️ Opcionales | ✅ Obligatorios | ✅ Obligatorios | ✅ Obligatorios |
| **Detalle de Productos** | ✅ Con precios | ✅ Con precios | ✅ Con precios | ✅ Con precios | Puede ser sin precios | ✅ Con precios | ✅ Con precios |
| **Datos de Transporte** | ❌ No requerido | ❌ No requerido | ❌ No requerido | ❌ No requerido | ✅ Requerido | ❌ No requerido | ❌ No requerido |
| **Referencia a Doc Anterior** | ❌ No | ❌ No | ❌ No | ❌ No | ⚠️ Opcional | ✅ Obligatorio | ✅ Obligatorio |
| **Montos en Negativo** | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ❌ NO | ✅ SÍ | ❌ NO |
| **Da Derecho a Crédito Fiscal** | ✅ SÍ | ❌ NO | ❌ NO | ❌ NO | ❌ NO | Según caso | Según caso |

---

## CAMPOS OBLIGATORIOS POR TIPO DE DOCUMENTO

### ✅ = Obligatorio | ⚠️ = Condicional | ❌ = No aplica

| Campo | Factura (33) | Factura Exenta (34) | Boleta (39) | Boleta Exenta (41) | Guía (52) | NC (61) | ND (56) |
|-------|-------------|---------------------|-------------|-------------------|----------|---------|---------|
| **ENCABEZADO** |
| Tipo documento | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Folio | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Fecha emisión | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Indicador no rebaja | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ |
| Tipo despacho | ⚠️ | ⚠️ | ❌ | ❌ | ✅ | ❌ | ❌ |
| Indicador traslado | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| Forma de pago | ⚠️ | ⚠️ | ❌ | ❌ | ❌ | ⚠️ | ⚠️ |
| Fecha vencimiento | ⚠️ | ⚠️ | ❌ | ❌ | ❌ | ⚠️ | ⚠️ |
| Indicador servicio | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ | ⚠️ | ⚠️ |
| **EMISOR** |
| RUT | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Razón Social | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Giro | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| ACTECO | ✅ | ✅ | ⚠️ | ⚠️ | ✅ | ✅ | ✅ |
| Dirección | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ | ⚠️ | ⚠️ |
| **RECEPTOR** |
| RUT | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Razón Social | ✅ | ✅ | ⚠️ | ⚠️ | ✅ | ✅ | ✅ |
| Dirección | ⚠️ | ⚠️ | ❌ | ❌ | ✅ | ⚠️ | ⚠️ |
| **TRANSPORTE** |
| Patente | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ❌ |
| RUT Transportista | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ❌ |
| Dirección destino | ❌ | ❌ | ❌ | ❌ | ⚠️ | ❌ | ❌ |
| **DETALLE** |
| Nombre item | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cantidad | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Unidad medida | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Precio unitario | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Monto item | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| Indicador exención | ❌ | ✅ | ❌ | ✅ | ⚠️ | ⚠️ | ⚠️ |
| **TOTALES** |
| Monto Neto | ✅ | ❌ | ✅ | ❌ | ❌ | ⚠️ | ⚠️ |
| Monto Exento | ❌ | ✅ | ❌ | ✅ | ❌ | ⚠️ | ⚠️ |
| Tasa IVA | ✅ | ❌ | ✅ | ❌ | ❌ | ⚠️ | ⚠️ |
| IVA | ✅ | ❌ | ✅ | ❌ | ❌ | ⚠️ | ⚠️ |
| Monto Total | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ |

---

## CASOS DE USO TÍPICOS

### 📦 VENTA DE PRODUCTOS

| Situación | Documento a Emitir |
|-----------|-------------------|
| Venta a otra empresa (B2B) con IVA | Factura Electrónica (33) |
| Venta a otra empresa (B2B) sin IVA | Factura Exenta (34) |
| Venta en local a consumidor final con IVA | Boleta Electrónica (39) |
| Venta en local a consumidor final sin IVA | Boleta Exenta (41) |
| Envío de productos a cliente (con o sin venta) | Guía de Despacho (52) |
| Devolución de productos | Nota de Crédito (61) |
| Cobro adicional por error u omisión | Nota de Débito (56) |

### 📋 SERVICIOS

| Situación | Documento a Emitir |
|-----------|-------------------|
| Servicio a empresa con IVA | Factura Electrónica (33) |
| Servicio a empresa sin IVA (salud, educación) | Factura Exenta (34) |
| Servicio a consumidor final con IVA | Boleta Electrónica (39) |
| Servicio a consumidor final sin IVA | Boleta Exenta (41) |
| Anulación de servicio facturado | Nota de Crédito (61) |
| Cobro adicional por servicio extra | Nota de Débito (56) |

### 🚚 TRANSPORTE Y LOGÍSTICA

| Situación | Documento a Emitir |
|-----------|-------------------|
| Despacho de productos vendidos | Factura (33) + Guía (52) |
| Traslado entre bodegas propias | Guía de Despacho (52) |
| Envío en consignación | Guía de Despacho (52) |
| Devolución de mercadería | Guía de Despacho (52) + Nota de Crédito (61) |

---

## FLUJOS DE TRABAJO COMUNES

### FLUJO 1: Venta con Despacho
```
1. Emitir FACTURA ELECTRÓNICA (33)
2. Emitir GUÍA DE DESPACHO (52) que referencia la factura
3. Cliente recibe productos
```

### FLUJO 2: Venta sin Despacho (Retiro en Local)
```
1. Emitir FACTURA ELECTRÓNICA (33)
2. Cliente retira en local
```

### FLUJO 3: Venta a Consumidor Final
```
1. Emitir BOLETA ELECTRÓNICA (39)
2. Entregar productos
```

### FLUJO 4: Devolución Total
```
1. Cliente devuelve productos
2. Emitir NOTA DE CRÉDITO (61) que referencia factura original
3. Anula completamente la factura
```

### FLUJO 5: Devolución Parcial
```
1. Cliente devuelve algunos productos
2. Emitir NOTA DE CRÉDITO (61) parcial que referencia factura
3. Ajusta el monto de la factura original
```

### FLUJO 6: Cobro Adicional
```
1. Se detecta error en precio facturado (menor al real)
2. Emitir NOTA DE DÉBITO (56) que referencia factura
3. Suma el monto adicional a la factura original
```

### FLUJO 7: Consignación
```
1. Emitir GUÍA DE DESPACHO (52) con Ind. Traslado = 3
2. Productos quedan en consignación
3. Cuando se vende: Emitir FACTURA (33) que referencia la guía
```

---

## DIFERENCIAS CLAVE ENTRE DOCUMENTOS SIMILARES

### Factura (33) vs Factura Exenta (34)
| Aspecto | Factura (33) | Factura Exenta (34) |
|---------|-------------|---------------------|
| IVA | ✅ Se cobra 19% | ❌ No se cobra |
| Campo IndExe | No se usa | = 1 en cada producto |
| Monto Neto | > 0 | = 0 |
| Monto Exento | = 0 | > 0 |
| Crédito Fiscal | Sí da derecho | No da derecho |
| Uso típico | Productos/servicios afectos | Salud, educación, exportación |

### Factura (33) vs Boleta (39)
| Aspecto | Factura (33) | Boleta (39) |
|---------|-------------|-------------|
| Receptor | Empresa (RUT real) | Consumidor (puede ser genérico) |
| Datos receptor | Completos obligatorios | Mínimos |
| Crédito fiscal | Sí da derecho | No da derecho |
| Uso | B2B | B2C |
| Dirección receptor | Requerida | Opcional |

### Nota de Crédito vs Nota de Débito
| Aspecto | NC (61) | ND (56) |
|---------|---------|---------|
| Efecto | Disminuye o anula | Aumenta |
| Montos | Negativos | Positivos |
| Uso típico | Devolución, descuento | Error en precio, cargos extra |
| IVA | Se resta | Se suma |

---

## MATRIZ DE DECISIÓN: ¿QUÉ DOCUMENTO DEBO EMITIR?

### Paso 1: ¿Qué tipo de operación es?
- **Venta** → Continuar a Paso 2
- **Traslado sin venta** → GUÍA DE DESPACHO (52)
- **Corrección de documento anterior** → Continuar a Paso 5

### Paso 2: ¿Quién es el cliente?
- **Empresa u organización** → Continuar a Paso 3
- **Consumidor final** → Continuar a Paso 4

### Paso 3: Venta a Empresa - ¿Tiene IVA?
- **Con IVA (19%)** → FACTURA ELECTRÓNICA (33)
- **Sin IVA (exento)** → FACTURA EXENTA (34)

### Paso 4: Venta a Consumidor - ¿Tiene IVA?
- **Con IVA (19%)** → BOLETA ELECTRÓNICA (39)
- **Sin IVA (exento)** → BOLETA EXENTA (41)

### Paso 5: Corrección - ¿Qué tipo de ajuste?
- **Anular o disminuir** → NOTA DE CRÉDITO (61)
- **Aumentar o cobrar más** → NOTA DE DÉBITO (56)

---

## RESUMEN DE MONTOS POR TIPO DE DOCUMENTO

| Documento | Monto Neto | Monto Exento | IVA | Total |
|-----------|-----------|-------------|-----|-------|
| Factura (33) | Suma productos afectos | 0 | Neto × 19% | Neto + IVA |
| Factura Exenta (34) | 0 | Suma productos exentos | 0 | Monto Exento |
| Boleta (39) | Suma productos afectos | 0 | Neto × 19% | Neto + IVA |
| Boleta Exenta (41) | 0 | Suma productos exentos | 0 | Monto Exento |
| Guía (52) | 0 | 0 | 0 | 0 o según caso |
| Nota Crédito (61) | **Negativo** o 0 | **Negativo** o 0 | **Negativo** o 0 | **Negativo** |
| Nota Débito (56) | Positivo o 0 | Positivo o 0 | Positivo o 0 | Positivo |

---

## VALIDACIONES COMUNES POR TIPO

### Factura Electrónica (33)
- ✅ Monto Neto > 0
- ✅ IVA = Monto Neto × 0.19
- ✅ Monto Exento = 0
- ✅ Total = Neto + IVA
- ✅ RUT Receptor válido y real

### Factura Exenta (34)
- ✅ Monto Neto = 0
- ✅ IVA = 0
- ✅ Monto Exento > 0
- ✅ Total = Monto Exento
- ✅ IndExe = 1 en todos los productos

### Boleta Electrónica (39)
- ✅ Monto Neto > 0
- ✅ IVA = Monto Neto × 0.19
- ✅ Total = Neto + IVA
- ⚠️ RUT Receptor puede ser 66.666.666-6

### Guía de Despacho (52)
- ✅ Tipo Despacho definido (1, 2 o 3)
- ✅ Indicador Traslado definido (1-9)
- ✅ Datos de transporte si despacho = 2 o 3
- ⚠️ Montos pueden ser 0

### Nota de Crédito (61)
- ✅ Montos en NEGATIVO
- ✅ Referencia a documento original
- ✅ Total negativo = -(Neto + IVA)
- ⚠️ Puede tener IndNoRebaja = 1

---

**Para más información detallada, consulta los documentos complementarios.**
