# GUÍA RÁPIDA - DATOS NECESARIOS PARA EMITIR DTE EN CHILE

## 📋 DATOS BÁSICOS QUE SIEMPRE DEBES TENER

### Para TODOS los documentos necesitas:

**1. IDENTIFICACIÓN DEL DOCUMENTO**
- Tipo de documento (33, 34, 39, 41, 52, 56, 61)
- Folio (número correlativo autorizado)
- Fecha de emisión (formato: YYYY-MM-DD)

**2. DATOS DEL EMISOR (Tu empresa)**
- RUT (con guión: 12345678-9)
- Razón Social
- Giro comercial
- Código ACTECO
- Dirección, comuna, ciudad

**3. DATOS DEL RECEPTOR (Tu cliente)**
- RUT (con guión: 12345678-9)
- Razón Social o nombre
- Dirección (recomendado)

**4. DETALLE DE PRODUCTOS/SERVICIOS**
Para cada línea necesitas:
- Nombre del producto/servicio
- Cantidad
- Unidad de medida (UN, KG, MT, etc.)
- Precio unitario
- Descuento (si aplica)

**5. TOTALES**
- Monto Neto (suma de productos afectos)
- IVA 19%
- Monto Total

---

## 📝 TIPOS DE DOCUMENTOS Y SUS CÓDIGOS

| Código | Documento | Para qué se usa |
|--------|-----------|-----------------|
| **33** | Factura Electrónica | Ventas B2B con IVA |
| **34** | Factura Exenta | Ventas B2B sin IVA |
| **39** | Boleta Electrónica | Ventas a consumidor final con IVA |
| **41** | Boleta Exenta | Ventas a consumidor final sin IVA |
| **52** | Guía de Despacho | Traslado de mercaderías |
| **56** | Nota de Débito | Aumentar valor de factura anterior |
| **61** | Nota de Crédito | Anular/disminuir factura anterior |

---

## 🎯 PLANTILLAS DE PROMPT POR TIPO DE DOCUMENTO

### FACTURA ELECTRÓNICA (33)
```
Emitir factura electrónica:
- Folio: [número]
- Fecha: [YYYY-MM-DD]
- Forma pago: Contado/Crédito

Emisor:
- RUT: [XX.XXX.XXX-X]
- Razón Social: [nombre empresa]
- Giro: [actividad]

Receptor:
- RUT: [XX.XXX.XXX-X]
- Razón Social: [nombre cliente]

Productos:
1. [nombre], cant: [#], unidad: [UN/KG], precio: $[####]
2. [nombre], cant: [#], unidad: [UN/KG], precio: $[####]
```

### BOLETA ELECTRÓNICA (39)
```
Emitir boleta electrónica:
- Folio: [número]
- Fecha: [YYYY-MM-DD]

Emisor:
- RUT: [XX.XXX.XXX-X]
- Razón Social: [nombre empresa]

Receptor:
- RUT: 66.666.666-6 (consumidor final)

Productos:
1. [nombre], cant: [#], precio: $[####]
2. [nombre], cant: [#], precio: $[####]

Total: $[####]
```

### GUÍA DE DESPACHO (52)
```
Emitir guía de despacho:
- Folio: [número]
- Fecha: [YYYY-MM-DD]
- Tipo traslado: [1-9, ver códigos]

Emisor:
- RUT: [XX.XXX.XXX-X]
- Dirección origen: [bodega/sucursal]

Receptor:
- RUT: [XX.XXX.XXX-X]
- Dirección destino: [destino]

Transporte:
- Patente: [XXXX00]
- RUT Chofer: [XX.XXX.XXX-X]

Productos:
1. [nombre], cantidad: [#], unidad: [UN/KG/CAJA]
```

### NOTA DE CRÉDITO (61)
```
Emitir nota de crédito:
- Folio: [número]
- Fecha: [YYYY-MM-DD]
- Referencia: Factura N° [####] del [fecha]
- Motivo: [devolución/anulación/descuento]

Emisor:
- RUT: [XX.XXX.XXX-X]

Receptor:
- RUT: [XX.XXX.XXX-X]

Detalle:
1. [nombre], cant: -[#], precio: $[####] (en negativo)
```

---

## ⚠️ ERRORES COMUNES A EVITAR

1. **RUT sin guión o sin dígito verificador**
   ❌ 123456789 
   ✅ 12.345.678-9

2. **Fecha en formato incorrecto**
   ❌ 05/11/2025 o 05-11-2025
   ✅ 2025-11-05

3. **Monto Total mal calculado**
   ❌ No incluir el IVA
   ✅ Total = Neto + IVA (19%)

4. **Falta de datos obligatorios**
   - Cantidad sin unidad de medida
   - Precio sin especificar
   - RUT del receptor vacío

5. **Folio fuera de rango**
   - Verificar que el folio esté dentro del rango del CAF autorizado

---

## 🔢 CÓDIGOS ÚTILES

### Indicadores de Traslado (Guías de Despacho)
- **1** = Venta
- **2** = Ventas por efectuar  
- **3** = Consignaciones
- **4** = Entrega gratuita
- **5** = Traslados internos
- **7** = Devolución

### Forma de Pago
- **1** = Contado
- **2** = Crédito
- **3** = Sin costo (gratuita)

### Indicador de Servicio
- **1** = Servicios periódicos domiciliarios
- **2** = Otros servicios periódicos
- **3** = Servicios generales

### Indicador de Exención (en productos)
- **1** = Producto exento de IVA
- **2** = No facturable
- **4** = No venta (no se factura)

---

## 💡 TIPS IMPORTANTES

1. **Para consumidor final en boletas**: usar RUT 66.666.666-6

2. **Tasa IVA actual**: 19% (se escribe como 19.00)

3. **Certificado digital**: Verificar que esté vigente antes de emitir

4. **Folios**: Asegurarse de tener CAF (Código Autorización Folios) vigente

5. **Montos**: Siempre en pesos chilenos (enteros, sin centavos)

6. **Descuentos**: Se pueden aplicar por línea o globales

7. **Notas de Crédito/Débito**: Siempre referenciar el documento original

8. **Guías de Despacho**: Incluir datos de transporte si el despacho es por cuenta del emisor

---

## 📞 DATOS DE CONTACTO ÚTILES

- **SII (Servicio de Impuestos Internos)**: www.sii.cl
- **Mesa de Ayuda SII**: 223951000
- **Documentación Acepta**: Consultar con tu proveedor del servicio

---

## ✅ CHECKLIST RÁPIDO ANTES DE EMITIR

- [ ] RUT emisor y receptor válidos (con guión)
- [ ] Folio dentro del rango autorizado
- [ ] Fecha en formato YYYY-MM-DD
- [ ] Al menos 1 producto en el detalle
- [ ] Cada producto tiene: nombre, cantidad, unidad, precio
- [ ] Cálculo de IVA correcto (19%)
- [ ] Monto Total = Neto + IVA
- [ ] Certificado digital vigente
- [ ] Para NC/ND: incluir referencia al documento original

---

**¿Necesitas más detalles?** Consulta el documento completo "estructura_datos_dte_chile.md"
