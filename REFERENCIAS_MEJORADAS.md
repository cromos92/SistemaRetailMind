# ✅ REFERENCIAS MEJORADAS EN LA INTERFAZ

## 🎯 MEJORAS IMPLEMENTADAS

Se ha actualizado la interfaz de referencias para facilitar la creación de **Notas de Crédito** y otros documentos.

---

## 📋 TIPOS DE DOCUMENTOS ORGANIZADOS

### Para Anular (NC/ND):
- **33** - Factura Electrónica
- **34** - Factura Exenta  
- **39** - Boleta Electrónica
- **41** - Boleta Exenta
- **52** - Guía de Despacho

### Documentos Comerciales:
- **801** - Orden de Compra
- **802** - Nota de Pedido
- **803** - Contrato
- **HES** - Hoja Entrada Servicio
- **SET** - Set de Pruebas

---

## 🔧 RAZONES DE REFERENCIA

Para Notas de Crédito (obligatorio):

| Código | Descripción | Uso |
|--------|-------------|-----|
| **1** | Anula Documento de Referencia | NC que anula completamente |
| **2** | Corrige Texto del Documento | NC que corrige descripciones |
| **3** | Corrige Montos | NC que corrige valores |

---

## 📄 EJEMPLOS DE USO

### Ejemplo 1: NC que anula Factura

**Configuración:**
- Tipo Documento: **Nota de Crédito (61)**
- Referencia:
  - Tipo: **33 - Factura Electrónica**
  - Folio: **4578**
  - Fecha: **2025-11-05**
  - Razón: **1 - Anula Documento**

**TXT Generado:**
```
61|234|2025-11-10||2|1|1|2025-11-10|}
...
~
|Item PRODUCTO A||10|UN|15000|||150000|Item|}
~
33||4578|2025-11-05|1|}  ← Referencia que anula factura 4578
~
USUARIO |||CIENTO CINCUENTA MIL PESOS (Total Art 150000)  |||||||factura 4578|4|}
```

### Ejemplo 2: NC que anula Boleta

**Configuración:**
- Tipo Documento: **Nota de Crédito (61)**
- Referencia:
  - Tipo: **39 - Boleta Electrónica**
  - Folio: **355001**
  - Fecha: **2025-11-08**
  - Razón: **1 - Anula Documento**

**TXT Generado:**
```
61|234|2025-11-10||2|1|1|2025-11-10|}
...
~
|Item PRODUCTO A||10|UN|15000|||150000|Item|}
~
39||355001|2025-11-08|1|}  ← Referencia que anula boleta 355001
~
USUARIO |||CIENTO CINCUENTA MIL PESOS (Total Art 150000)  |||||||boleta 355001|4|}
```

### Ejemplo 3: Factura con Orden de Compra

**Configuración:**
- Tipo Documento: **Factura Electrónica (33)**
- Referencia:
  - Tipo: **801 - Orden de Compra**
  - Folio: **OC-98765**
  - Fecha: **2025-11-05**
  - Razón: (vacío - no aplica)

**TXT Generado:**
```
33|4578|2025-11-10||2|1|1|2025-11-10|}
...
~
801|| OC-98765 | 2025-11-05|| |}  ← Referencia a OC
~
USUARIO|||CIENTO OCHENTA MIL PESOS  |||||||HP LaserJet|4|}
```

### Ejemplo 4: NC que corrige montos

**Configuración:**
- Tipo Documento: **Nota de Crédito (61)**
- Referencia:
  - Tipo: **33 - Factura Electrónica**
  - Folio: **4578**
  - Fecha: **2025-11-05**
  - Razón: **3 - Corrige Montos**

**TXT Generado:**
```
61|234|2025-11-10||2|1|1|2025-11-10|}
...
~
33||4578|2025-11-05|3|}  ← Referencia que corrige montos de factura 4578
~
```

---

## 🎯 GUÍA DE USO

### Para crear una NC que anula una Factura:

1. Seleccionar **"Nota de Crédito"** (tipo 61)
2. Ingresar datos del documento
3. En **Referencias**:
   - Tipo: **33 - Factura Electrónica**
   - Folio: (número de la factura a anular)
   - Fecha: (fecha de la factura original)
   - Razón: **1 - Anula Documento**
4. Generar TXT

### Para crear una NC que anula una Boleta:

1. Seleccionar **"Nota de Crédito"** (tipo 61)
2. Ingresar datos del documento
3. En **Referencias**:
   - Tipo: **39 - Boleta Electrónica**
   - Folio: (número de la boleta a anular)
   - Fecha: (fecha de la boleta original)
   - Razón: **1 - Anula Documento**
4. Generar TXT

### Para crear una Factura con Orden de Compra:

1. Seleccionar **"Factura Electrónica"** (tipo 33)
2. Ingresar datos del documento
3. En **Referencias**:
   - Tipo: **801 - Orden de Compra**
   - Folio: (número de la OC)
   - Fecha: (fecha de la OC)
   - Razón: (dejar vacío)
4. Generar TXT

---

## ✅ MEJORAS APLICADAS

### 1. Tipos organizados por categoría
- **Para Anular:** Tipos 33, 34, 39, 41, 52
- **Documentos Comerciales:** Tipos 801, 802, 803, HES, SET

### 2. Códigos numéricos visibles
- Ahora muestra: **"33 - Factura Electrónica"**
- Antes: Solo "Factura"

### 3. Ayuda contextual
- Indica que la razón es obligatoria para NC/ND
- Diferencia entre referencias para NC vs Facturas

---

## 🚀 PROBAR AHORA

### Test 1: NC que anula Factura
1. Seleccionar "Nota de Crédito"
2. Cargar Ejemplo
3. Ir a Referencias
4. Tipo: **33 - Factura Electrónica**
5. Folio: **4578**
6. Razón: **1 - Anula Documento**
7. Generar TXT
8. Verificar: `33||4578|2025-XX-XX|1|}`

### Test 2: NC que anula Boleta
1. Tipo: **39 - Boleta Electrónica**
2. Folio: **355001**
3. Razón: **1 - Anula Documento**
4. Generar TXT
5. Verificar: `39||355001|2025-XX-XX|1|}`

---

**Ahora las referencias están optimizadas para NC que anulan Facturas o Boletas.** ✅

¿Quieres que actualice el ejemplo de carga para que muestre una NC con referencia?
