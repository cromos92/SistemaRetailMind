# EJEMPLOS DE ARCHIVOS TXT PARA SISTEMA ACEPTA

## FORMATO GENERAL

Cada archivo TXT contiene las líneas separadas por el delimitador configurado (generalmente pipe "|")

```
LÍNEA 1: Identificación del Documento
LÍNEA 2: Datos del Emisor  
LÍNEA 3: Datos del Receptor
LÍNEA 4: Datos de Transporte (opcional)
LÍNEA 5: Totales
LÍNEAS 6+: Detalle de productos (una línea por producto)
```

---

## EJEMPLO 1: FACTURA ELECTRÓNICA (Tipo 33)

**Archivo: factura_12345.txt**

```
33|12345|2025-11-05|||1||||||
76123456-7|EMPRESA DEMO LTDA|VENTA AL POR MAYOR DE PRODUCTOS ALIMENTICIOS|462100|CASA MATRIZ|001|AV. PRINCIPAL 123|SANTIAGO|SANTIAGO|VENDEDOR001|+56912345678|||
77654321-K|CLI001|CLIENTE EJEMPLO S.A.|COMERCIO AL POR MENOR||CALLE COMERCIO 456|PROVIDENCIA|SANTIAGO||
||||
192500||19.00|36575|229075|2025-11-05T14:30:00|||||||||||||||||
|PRODUCTO A|Descripción del Producto A|10.000000|UN|15000.000000||0|150000|
|PRODUCTO B|Descripción del Producto B|5.000000|KG|8500.000000|5.00|2125|40375|
```

**Explicación línea por línea:**

**Línea 1 - IdDoc:**
- Campo 1: Tipo documento = 33 (Factura Electrónica)
- Campo 2: Folio = 12345
- Campo 3: Fecha emisión = 2025-11-05
- Campo 4-6: Vacíos (indicadores opcionales)
- Campo 7: Forma de pago = 1 (Contado)
- Campos 8-12: Vacíos (opcionales)

**Línea 2 - Emisor:**
- Campo 1: RUT = 76123456-7
- Campo 2: Razón Social = EMPRESA DEMO LTDA
- Campo 3: Giro = VENTA AL POR MAYOR DE PRODUCTOS ALIMENTICIOS
- Campo 4: ACTECO = 462100
- Campo 5: Sucursal = CASA MATRIZ
- Campo 6: Código Sucursal = 001
- Campo 7: Dirección = AV. PRINCIPAL 123
- Campo 8: Comuna = SANTIAGO
- Campo 9: Ciudad = SANTIAGO
- Campo 10: Código Vendedor = VENDEDOR001
- Campo 11: Teléfono = +56912345678

**Línea 3 - Receptor:**
- Campo 1: RUT = 77654321-K
- Campo 2: Código interno = CLI001
- Campo 3: Razón Social = CLIENTE EJEMPLO S.A.
- Campo 4: Giro = COMERCIO AL POR MENOR
- Campo 6: Dirección = CALLE COMERCIO 456
- Campo 7: Comuna = PROVIDENCIA
- Campo 8: Ciudad = SANTIAGO

**Línea 4 - Transporte:**
- Vacía (no aplica para factura sin despacho)

**Línea 5 - Totales:**
- Campo 1: Monto Neto = 192500
- Campo 3: Tasa IVA = 19.00
- Campo 4: IVA = 36575
- Campo 5: Monto Total = 229075
- Campo 6: Timestamp = 2025-11-05T14:30:00

**Línea 6 - Detalle Producto 1:**
- Campo 1: Indicador Exención = vacío (producto afecto)
- Campo 2: Nombre = PRODUCTO A
- Campo 3: Descripción = Descripción del Producto A
- Campo 4: Cantidad = 10.000000
- Campo 5: Unidad = UN
- Campo 6: Precio = 15000.000000
- Campo 7: Descuento % = vacío
- Campo 8: Monto descuento = 0
- Campo 9: Monto Item = 150000

**Línea 7 - Detalle Producto 2:**
- Campo 1: Indicador Exención = vacío
- Campo 2: Nombre = PRODUCTO B
- Campo 3: Descripción = Descripción del Producto B
- Campo 4: Cantidad = 5.000000
- Campo 5: Unidad = KG
- Campo 6: Precio = 8500.000000
- Campo 7: Descuento % = 5.00
- Campo 8: Monto descuento = 2125
- Campo 9: Monto Item = 40375

---

## EJEMPLO 2: BOLETA ELECTRÓNICA (Tipo 39)

**Archivo: boleta_5678.txt**

```
39|5678|2025-11-05||||||3||2025-11-05T16:45:00
76123456-7|MI NEGOCIO SPA|VENTA DE PRODUCTOS VARIOS|||||||||
66666666-6||CONSUMIDOR FINAL|||||||
||||
86000||19.00|16340|102340||||||||||||||||||||||
|SERVICIO DE INSTALACION||1.000000|UN|50000.000000||0|50000|
|PRODUCTO X||3.000000|UN|12000.000000||0|36000|
```

**Explicación:**

**Línea 1 - IdDoc:**
- Tipo = 39 (Boleta Electrónica)
- Folio = 5678
- Fecha = 2025-11-05
- Indicador Servicio = 3 (Factura de Servicios)
- Timestamp = 2025-11-05T16:45:00

**Línea 2 - Emisor:**
- RUT = 76123456-7
- Razón Social = MI NEGOCIO SPA
- Giro = VENTA DE PRODUCTOS VARIOS

**Línea 3 - Receptor:**
- RUT = 66666666-6 (consumidor final genérico)
- Razón Social = CONSUMIDOR FINAL

**Línea 5 - Totales:**
- Monto Neto = 86000
- Tasa IVA = 19.00
- IVA = 16340
- Monto Total = 102340

---

## EJEMPLO 3: GUÍA DE DESPACHO (Tipo 52)

**Archivo: guia_789.txt**

```
52|789|2025-11-05||2|1||||||
76123456-7|DISTRIBUIDORA DEMO LTDA|DISTRIBUCION DE ALIMENTOS|462100|||BODEGA CENTRAL AV LOGISTICA 500|QUILICURA|QUILICURA|||
77654321-K||SUPERMERCADO ABC S.A.||||||
ABCD12|12345678-9|CALLE COMPRAS 200|LAS CONDES|LAS CONDES
0|0|||0|||||||||||||||||||||||||
|CAJA PRODUCTO A 12 UNIDADES||50.000000|CAJA|||0|0|
|CAJA PRODUCTO B 24 UNIDADES||30.000000|CAJA|||0|0|
```

**Explicación:**

**Línea 1 - IdDoc:**
- Tipo = 52 (Guía de Despacho)
- Folio = 789
- Fecha = 2025-11-05
- Tipo Despacho = 2 (por cuenta del emisor al receptor)
- Indicador Traslado = 1 (Operación constituye venta)

**Línea 2 - Emisor:**
- RUT = 76123456-7
- Razón Social = DISTRIBUIDORA DEMO LTDA
- Giro = DISTRIBUCION DE ALIMENTOS
- ACTECO = 462100
- Dirección Origen = BODEGA CENTRAL AV LOGISTICA 500
- Comuna = QUILICURA

**Línea 3 - Receptor:**
- RUT = 77654321-K
- Razón Social = SUPERMERCADO ABC S.A.

**Línea 4 - Transporte:**
- Patente = ABCD12
- RUT Transportista = 12345678-9
- Dirección Destino = CALLE COMPRAS 200
- Comuna Destino = LAS CONDES
- Ciudad Destino = LAS CONDES

**Línea 5 - Totales:**
- Todos en 0 (la guía solo documenta el traslado, no valores)

**Detalle:**
- Productos con cantidad y unidad, pero sin precios ni montos

---

## EJEMPLO 4: NOTA DE CRÉDITO (Tipo 61)

**Archivo: nc_234.txt**

```
61|234|2025-11-05|||||||||||
76123456-7|EMPRESA DEMO LTDA|VENTA AL POR MAYOR DE PRODUCTOS ALIMENTICIOS|462100||||||VENDEDOR001|
77654321-K||CLIENTE EJEMPLO S.A.||||||
||||
-192500||19.00|-36575|-229075||||||||||||||||||||||
|PRODUCTO A - DEVOLUCION||10.000000|UN|-15000.000000||0|-150000|
|PRODUCTO B - DEVOLUCION||5.000000|KG|-8500.000000||0|-42500|
```

**Explicación:**

**Línea 1 - IdDoc:**
- Tipo = 61 (Nota de Crédito)
- Folio = 234
- Fecha = 2025-11-05

**Línea 5 - Totales:**
- Todos los montos en NEGATIVO
- Monto Neto = -192500
- IVA = -36575
- Monto Total = -229075

**Detalle:**
- Precios unitarios y montos en NEGATIVO
- Indicar en descripción que es devolución/anulación

---

## EJEMPLO 5: FACTURA EXENTA (Tipo 34)

**Archivo: factura_exenta_999.txt**

```
34|999|2025-11-05|||||||||||
76123456-7|FUNDACION EJEMPLO|SERVICIOS EDUCACIONALES|853100||||||RESPONSABLE01|
77654321-K||INSTITUCION EDUCATIVA||||||
||||
0|150000|||150000||||||||||||||||||||||
1|SERVICIO EDUCATIVO||10.000000|UN|15000.000000||0|150000|
```

**Explicación:**

**Línea 1 - IdDoc:**
- Tipo = 34 (Factura Exenta)

**Línea 5 - Totales:**
- Monto Neto = 0 (no hay productos afectos)
- Monto Exento = 150000
- NO hay IVA
- Monto Total = 150000

**Detalle:**
- Campo 1: Indicador Exención = 1 (producto exento)
- El resto igual que factura normal

---

## NOTAS IMPORTANTES SOBRE EL FORMATO TXT

### Separadores
- El separador por defecto es el pipe "|"
- Mantener el separador incluso en campos vacíos
- Ejemplo: `|||` = 3 campos vacíos

### Decimales
- Para cantidades y precios: usar punto como separador decimal
- Formato: 12 enteros, 6 decimales (ejemplo: 10.000000)
- Para porcentajes: 3 enteros, 2 decimales (ejemplo: 19.00)

### Fechas
- Formato obligatorio: YYYY-MM-DD
- Para timestamp: YYYY-MM-DDTHH:MI:SS

### RUT
- Siempre con guión: 12345678-9
- No usar puntos de miles: ❌ 12.345.678-9

### Montos
- Sin separadores de miles
- Sin signo $ 
- Decimales con punto
- Para NC: usar números negativos con signo -

### Caracteres Especiales
- Evitar ñ, tildes en campos críticos
- Si es necesario, usar UTF-8
- Evitar caracteres como: & < > " '

### Orden de los Campos
- Respetar estrictamente el orden de los campos
- No omitir líneas (dejar vacías si no aplican)
- Las líneas de detalle pueden ser múltiples

---

## VALIDACIÓN BÁSICA DE ARCHIVOS TXT

Antes de enviar el archivo al sistema Acepta, verificar:

1. **Estructura correcta**
   - Mínimo 5 líneas (hasta Totales)
   - Al menos 1 línea de detalle

2. **Formato de datos**
   - RUT con guión
   - Fechas YYYY-MM-DD
   - Montos sin símbolos

3. **Cálculos correctos**
   - IVA = Monto Neto × 0.19
   - Total = Neto + Exento + IVA
   - Monto Item = (Precio × Cantidad) - Descuento

4. **Datos obligatorios**
   - Tipo documento
   - Folio
   - Fecha
   - RUT emisor y receptor
   - Razón social emisor
   - Al menos 1 producto

5. **Coherencia**
   - El tipo de documento coincide con el contenido
   - Los montos son coherentes con el tipo de documento
   - Para NC/ND: montos en negativo/positivo según corresponda

---

## HERRAMIENTAS RECOMENDADAS

### Para generar archivos TXT:
- Excel/Calc: Guardar como CSV con delimitador |
- Scripts Python: usando pandas o csv
- Sistemas ERP propios con exportación a TXT

### Para validar:
- Editor de texto plano (Notepad++, VSCode)
- Verificar encoding UTF-8
- Comprobar que no hay saltos de línea incorrectos

---

**¿Necesitas más información?** Consulta el documento completo con todas las especificaciones.
