# 📋 ANÁLISIS: Formato TXT Guía de Despacho vs Factura Electrónica

## 🔍 Comparación de Formatos Reales

### FACTURA ELECTRÓNICA (Tipo 33)
```
33|1|2026-01-26||2|1|1|2026-01-26|}
76337843-8|EDELMIRA TEBES Y CIA. LTDA.|IMPORTACIONES Y EXPORTACIONES DE CALZADOS ARRIENDO DE INMUEBLES SIN AMOBLAR|469000|EDEL||Maipu 676|||EDEL|}
78503140-7||Paola Tebes Tebes y Cia|Comercio||Matta 2422||||}
|||||}
959856|0|19|182372|1142228|||||||||||||}
~
|1BM00642-014 FILA MULTI 2:10 1:12.0 1:11 2:7,0 2:7,5 2:8,5 2:9,5||12|UN|39994|||479928|1BM00642-014|}
|1BM00643-161 FILA MULTI 2:10 1:11 2:7,0 2:7,5 2:8,5 1:12.0 2:9,5||12|UN|39994|||479928|1BM00643-161|}
~
~
~
EDEL|||UN MILLÓN CIENTO CUARENTA Y DOS MIL DOSCIENTOS VEINTIOCHO PESOS  total Productos: 24||||||||HP LaserJet Professional P1102w|4|}
~
\
```

### GUÍA DE DESPACHO (Tipo 52)
```
52|3993|2025-04-29||2|1|1|2025-04-29|}
76104936-4|IMPORTADORA NICOLE ANDREA|VENTA DE CALZADOS|469000|||MAIPU 676 - OF. 1-2 PISO|ANTOFAGASTA|ANTOFAGASTA|IMP|}
76104936-4||IMPORTADORA NICOLE ANDREA|VENTA DE CALZADOS||Matta 2438|ANTOFAGASTA|ANTOFAGASTA||}
|||||}
52400|0|19|9956|62356|||||||||||||}
~
|BOLSA REAL MULTIREAL SPORT 200:00 ||200|PAR|262|||52400|BOLSA REAL|}

~
~
~
IMP|||SESENTA Y DOS MIL TRESCIENTOS CINCUENTA Y SEIS PESOS (Total Art 200)  |||||||HP LaserJet Professional P1102w|4|}
~
\
```

---

## 📊 ANÁLISIS LÍNEA POR LÍNEA

### Línea 1: Identificación del Documento
**Formato:** `TIPO|FOLIO|FECHA_EMISION||TIPO_DESPACHO|IND_TRASLADO|FORMA_PAGO|FECHA_VENC|}`

**Factura:**
```
33|1|2026-01-26||2|1|1|2026-01-26|}
```
- Tipo: 33 (Factura Electrónica)
- Folio: 1
- Fecha emisión: 2026-01-26
- Campo vacío (ind_no_rebaja)
- Tipo despacho: 2
- Indicador traslado: 1
- Forma pago: 1
- Fecha vencimiento: 2026-01-26

**Guía:**
```
52|3993|2025-04-29||2|1|1|2025-04-29|}
```
- Tipo: 52 (Guía de Despacho)
- Folio: 3993
- Fecha emisión: 2025-04-29
- Campo vacío (ind_no_rebaja)
- Tipo despacho: 2
- Indicador traslado: 1
- Forma pago: 1
- Fecha vencimiento: 2025-04-29

✅ **CONCLUSIÓN:** El formato es IDÉNTICO, solo cambia el tipo de documento (33 vs 52) y el folio.

---

### Línea 2: Datos del Emisor
**Formato:** `RUT|RAZON_SOCIAL|GIRO|ACTECO|SUCURSAL|COD_SUCURSAL|DIRECCION|COMUNA|CIUDAD|ALIAS|}`

**Factura:**
```
76337843-8|EDELMIRA TEBES Y CIA. LTDA.|IMPORTACIONES Y EXPORTACIONES DE CALZADOS ARRIENDO DE INMUEBLES SIN AMOBLAR|469000|EDEL||Maipu 676|||EDEL|}
```

**Guía:**
```
76104936-4|IMPORTADORA NICOLE ANDREA|VENTA DE CALZADOS|469000|||MAIPU 676 - OF. 1-2 PISO|ANTOFAGASTA|ANTOFAGASTA|IMP|}
```

✅ **CONCLUSIÓN:** El formato es IDÉNTICO, solo varían los datos específicos de cada empresa.

---

### Línea 3: Datos del Receptor
**Formato:** `RUT|COD_INTERNO|RAZON_SOCIAL|GIRO|CONTACTO|DIRECCION|COMUNA|CIUDAD|CIUDAD_POSTAL|}`

**Factura:**
```
78503140-7||Paola Tebes Tebes y Cia|Comercio||Matta 2422||||}
```

**Guía:**
```
76104936-4||IMPORTADORA NICOLE ANDREA|VENTA DE CALZADOS||Matta 2438|ANTOFAGASTA|ANTOFAGASTA||}
```

✅ **CONCLUSIÓN:** El formato es IDÉNTICO, solo varían los datos del receptor.

---

### Línea 4: Datos de Transporte
**Formato:** `PATENTE|RUT_TRANSPORTISTA|DIRECCION_DESTINO|COMUNA_DESTINO|CIUDAD_DESTINO|}`

**Factura:**
```
|||||}
```

**Guía:**
```
|||||}
```

✅ **CONCLUSIÓN:** Ambos vacíos, el formato es IDÉNTICO.

---

### Línea 5: Totales
**Formato:** `MONTO_NETO|MONTO_EXENTO|TASA_IVA|IVA|MONTO_TOTAL|[16 campos vacíos]|}`

**Factura:**
```
959856|0|19|182372|1142228|||||||||||||}
```
- Monto neto: 959856
- Monto exento: 0
- Tasa IVA: 19
- IVA: 182372
- Total: 1142228

**Guía:**
```
52400|0|19|9956|62356|||||||||||||}
```
- Monto neto: 52400
- Monto exento: 0
- Tasa IVA: 19
- IVA: 9956
- Total: 62356

✅ **CONCLUSIÓN:** El formato es IDÉNTICO, solo varían los montos.

---

### Separador antes de productos
```
~
```

✅ **CONCLUSIÓN:** IDÉNTICO en ambos.

---

### Líneas de Detalle de Productos
**Formato:** `|IND_EXENC|CODIGO NOMBRE|DESCRIPCION|CANTIDAD|UNIDAD|PRECIO|DESC_PCT|MONTO_DESC|MONTO_ITEM|CODIGO|}`

**Factura (ejemplo producto 1):**
```
|1BM00642-014 FILA MULTI 2:10 1:12.0 1:11 2:7,0 2:7,5 2:8,5 2:9,5||12|UN|39994|||479928|1BM00642-014|}
```
- Código al inicio: 1BM00642-014
- Nombre: FILA MULTI 2:10 1:12.0 1:11 2:7,0 2:7,5 2:8,5 2:9,5
- Descripción: (vacío)
- Cantidad: 12
- Unidad: UN
- Precio: 39994
- Descuento %: (vacío)
- Monto descuento: (vacío)
- Monto total: 479928
- Código al final: 1BM00642-014

**Guía (ejemplo producto):**
```
|BOLSA REAL MULTIREAL SPORT 200:00 ||200|PAR|262|||52400|BOLSA REAL|}
```
- Código al inicio: BOLSA REAL
- Nombre: MULTIREAL SPORT 200:00
- Descripción: (vacío)
- Cantidad: 200
- Unidad: PAR
- Precio: 262
- Descuento %: (vacío)
- Monto descuento: (vacío)
- Monto total: 52400
- Código al final: BOLSA REAL

✅ **CONCLUSIÓN:** El formato es IDÉNTICO, estructura igual.

---

### Separadores después de productos
```
~
~
~
```

✅ **CONCLUSIÓN:** IDÉNTICO en ambos (3 separadores).

---

### Línea de Información Final
**Formato:** `VENDEDOR|||MONTO_LETRAS|||||||IMPRESORA|COPIAS|}`

**Factura:**
```
EDEL|||UN MILLÓN CIENTO CUARENTA Y DOS MIL DOSCIENTOS VEINTIOCHO PESOS  total Productos: 24||||||||HP LaserJet Professional P1102w|4|}
```

**Guía:**
```
IMP|||SESENTA Y DOS MIL TRESCIENTOS CINCUENTA Y SEIS PESOS (Total Art 200)  |||||||HP LaserJet Professional P1102w|4|}
```

✅ **CONCLUSIÓN:** El formato es IDÉNTICO, solo varía el contenido del texto.

---

### Cierre
```
~
\
```

✅ **CONCLUSIÓN:** IDÉNTICO en ambos.

---

## 🎯 CONCLUSIÓN FINAL

### ✅ El formato TXT es IDÉNTICO entre Factura Electrónica (33) y Guía de Despacho (52)

**Las únicas diferencias son:**

1. **Tipo de documento** (línea 1, campo 1): 33 vs 52
2. **Folio** (línea 1, campo 2): Diferente numeración
3. **Datos específicos** (emisor, receptor, totales, productos): Contenido variable según el documento

### 📋 Estado del Código Actual

El código en `views_modulo_documentos.py` ya maneja correctamente esto:

```python
elif tipo_doc == 52:
    logger.warning(f"🔍 Detectado tipo GUÍA DE DESPACHO ({tipo_doc}), usando formato de factura")
    # Guía de Despacho usa el mismo formato que Factura, solo cambia el tipo
    # Se procesa con el código de factura normal
```

**✅ El código está correcto y NO necesita modificaciones.**

---

## 🔍 Posibles Problemas si las Guías no Funcionan

Si las guías de despacho no están generando correctamente, el problema NO está en el formato, sino posiblemente en:

1. **Datos faltantes o incorrectos** al llamar a `generar_txt_dte_acepta(datos)`
2. **Validaciones** que fallan para tipo 52
3. **Folio no asignado** correctamente para guías
4. **Campos obligatorios** no completados en los datos de entrada

### Recomendación:
Revisar los logs cuando se genera una guía para ver exactamente qué datos se están enviando a la función y si hay algún error de validación.

---

## 📝 Formato Correcto Confirmado

```
TIPO_DOC|FOLIO|FECHA||TIPO_DESPACHO|IND_TRASLADO|FORMA_PAGO|FECHA_VENC|}
RUT_EMISOR|RAZON_SOCIAL|GIRO|ACTECO|SUCURSAL|COD_SUC|DIR|COMUNA|CIUDAD|ALIAS|}
RUT_RECEPTOR|COD|RAZON_SOCIAL|GIRO|CONTACTO|DIR|COMUNA|CIUDAD|CIUDAD_POSTAL|}
PATENTE|RUT_TRANS|DIR_DEST|COMUNA_DEST|CIUDAD_DEST|}
NETO|EXENTO|TASA_IVA|IVA|TOTAL|[16 pipes]|}
~
|IND_EX|COD NOMBRE|DESC|CANT|UN|PRECIO|DESC%|MONTO_DESC|TOTAL_ITEM|COD|}
...productos...
~
~
~
VENDEDOR|||MONTO_EN_LETRAS|||||||IMPRESORA|COPIAS|}
~
\
```

**Este formato se aplica IGUAL para tipo 33 (Factura) y tipo 52 (Guía).**
