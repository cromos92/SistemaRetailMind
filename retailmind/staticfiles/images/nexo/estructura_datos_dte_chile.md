# ESTRUCTURA DE DATOS PARA EMISIÓN DE DOCUMENTOS TRIBUTARIOS ELECTRÓNICOS (DTE) - CHILE

**Última Actualización**: Basado en archivos MSG Acepta 2016

---

## ÍNDICE DE TIPOS DE DOCUMENTOS

| Código | Tipo de Documento |
|--------|-------------------|
| 33 | Factura Electrónica |
| 34 | Factura No Afecta o Exenta Electrónica |
| 39 | Boleta Electrónica |
| 41 | Boleta No Afecta o Exenta Electrónica |
| 52 | Guía de Despacho Electrónica |
| 56 | Nota de Débito Electrónica |
| 61 | Nota de Crédito Electrónica |
| 46 | Factura de Compra Electrónica |

---

## ESTRUCTURA GENERAL PARA TODOS LOS DOCUMENTOS

### 1. ENCABEZADO - IDENTIFICACIÓN DEL DOCUMENTO (IdDoc)

```
Campo                           | Tipo    | Largo | TAG XML          | Obligatorio | Descripción
--------------------------------|---------|-------|------------------|-------------|-------------------------------------------
Tipo de documento               | Number  | 2     | <TipoDTE>        | SÍ          | 33, 34, 39, 41, 52, 56, 61, 46
Folio del Documento             | Number  | 10    | <Folio>          | SÍ          | Número correlativo autorizado por el SII
Fecha de Emisión                | Char    | 10    | <FchEmis>        | SÍ          | Formato: YYYY-MM-DD
Indicador de no rebaja          | Number  | 1     | <IndNoRebaja>    | NO          | Solo NC sin derecho a rebaja (valor: 1)
Tipo de despacho                | Number  | 1     | <TipoDespacho>   | NO          | 1:receptor, 2:emisor a receptor, 3:emisor a otros
Indicador de traslado           | Number  | 1     | <IndTraslado>    | NO          | Para guías de despacho (1-9, ver tabla)
Forma de pago                   | Number  | 1     | <FmaPago>        | NO          | 1:Contado, 2:Crédito, 3:Sin costo
Fecha de Vencimiento            | Char    | 10    | <FchVenc>        | NO          | Formato: YYYY-MM-DD
Indicador de servicio           | Number  | 1     | <IndServicio>    | NO          | 1-5 (ver tabla de servicios)
Timestamp                       | Char    | 19    | <TmstFirma>      | NO          | Formato: YYYY-MM-DDTHH:MI:SS
```

**VALORES DEL INDICADOR DE TRASLADO (Para Guías de Despacho):**
- 1: Operación constituye venta
- 2: Ventas por efectuar
- 3: Consignaciones
- 4: Entrega gratuita
- 5: Traslados internos
- 6: Otros traslados no venta
- 7: Guía de devolución
- 8: Traslado para exportación (no venta)
- 9: Venta para Exportación

**VALORES DEL INDICADOR DE SERVICIO:**
- 1: Factura de servicios periódicos domiciliarios
- 2: Factura de otros servicios periódicos
- 3: Factura de Servicios
- 4: Servicios de Hotelería
- 5: Servicio de Transporte Terrestre Internacional

---

### 2. DATOS DEL EMISOR

```
Campo                           | Tipo    | Largo | TAG XML          | Obligatorio | Descripción
--------------------------------|---------|-------|------------------|-------------|-------------------------------------------
RUT Emisor                      | Char    | 10    | <RUTEmisor>      | SÍ          | Formato: XXXXXXXX-X (con guión)
Razón Social                    | Char    | 100   | <RznSoc>         | SÍ          | Nombre o razón social del emisor
Giro del Negocio                | Char    | 80    | <GiroEmis>       | SÍ          | Giro comercial del emisor
Código de Actividad Económica   | Number  | 6     | <Acteco>         | SÍ          | Código ACTECO (máximo 4 códigos)
Sucursal                        | Char    | 20    | <Sucursal>       | NO          | Nombre de la sucursal
Código Sucursal SII             | Number  | 9     | <CdgSIISucur>    | NO          | Código registrado en el SII
Dirección Origen                | Char    | 60    | <DirOrigen>      | NO          | Dirección de despacho de mercaderías
Comuna Origen                   | Char    | 20    | <CmnaOrigen>     | NO          | Comuna de origen
Ciudad Origen                   | Char    | 20    | <CiudadOrigen>   | NO          | Ciudad de origen
Código del Vendedor             | Char    | 60    | <CdgVendedor>    | NO          | Identificador del vendedor
Teléfono Emisor                 | Char    | 20    | <Telefono>       | NO          | Hasta 2 repeticiones
```

**Campos Específicos para Guías de Despacho:**
```
Campo                                  | Tipo    | Largo | TAG XML          | Obligatorio | Descripción
---------------------------------------|---------|-------|------------------|-------------|-------------------------------------------
Código Emisor Traslado Excepcional    | Number  | 1     | <CdgTraslado>    | NO*         | 1:Exportador, 2:Agente Aduana, 3:Vendedor, 4:Autorizado SII
Folio Autorización                     | Number  | 5     | <FolioAut>       | NO*         | Nº Resolución SII (si CdgTraslado=4)
Fecha Autorización                     | Char    | 10    | <FchAut>         | NO*         | Fecha de la resolución (si CdgTraslado=4)
```
*Obligatorio si Indicador de traslado = 8 o 9

---

### 3. DATOS DEL RECEPTOR

```
Campo                           | Tipo    | Largo | TAG XML          | Obligatorio | Descripción
--------------------------------|---------|-------|------------------|-------------|-------------------------------------------
RUT Receptor                    | Char    | 10    | <RUTRecep>       | SÍ          | Formato: XXXXXXXX-X (55.555.555-5 para exportación)
Código Interno del Receptor     | Char    | 20    | <CdgIntRecep>    | NO          | Código interno del cliente
Razón Social                    | Char    | 100   | <RznSocRecep>    | SÍ          | Nombre o razón social del receptor
Giro del Receptor               | Char    | 40    | <GiroRecep>      | NO          | Giro comercial del receptor
Contacto                        | Char    | 80    | <Contacto>       | NO          | Nombre y teléfono de contacto
Dirección Comercial             | Char    | 70    | <DirRecep>       | NO          | Dirección legal del receptor
Comuna Comercial                | Char    | 20    | <CmnaRecep>      | NO          | Comuna del receptor
Ciudad Comercial                | Char    | 20    | <CiudadRecep>    | NO          | Ciudad del receptor
Ciudad Postal                   | Char    | 20    | <CiudadPostal>   | NO          | Ciudad postal
```

---

### 4. DATOS DE TRANSPORTE (Opcional - Para Guías de Despacho)

```
Campo                           | Tipo    | Largo | TAG XML          | Obligatorio | Descripción
--------------------------------|---------|-------|------------------|-------------|-------------------------------------------
Patente                         | Char    | 8     | <Patente>        | NO*         | Patente del vehículo
RUT del Transportista           | Char    | 10    | <RUTTrans>       | NO*         | RUT del chofer que realiza el transporte
Dirección Destino               | Char    | 70    | <DirDest>        | NO          | Si destino es distinto del receptor
Comuna Destino                  | Char    | 20    | <CmnaDest>       | NO          | Comuna de destino
Ciudad Destino                  | Char    | 20    | <CiudadDest>     | NO          | Ciudad de destino
```
*Relevante si Tipo de despacho = 2 o 3

---

### 5. TOTALES DEL DOCUMENTO

```
Campo                                | Tipo    | Largo | TAG XML          | Obligatorio | Descripción
-------------------------------------|---------|-------|------------------|-------------|-------------------------------------------
Monto Neto                           | Number  | 18    | <MntNeto>        | SÍ          | Suma ítems afectos - descuentos + recargos
Monto Exento                         | Number  | 18    | <MntExe>         | NO          | Suma ítems exentos - descuentos + recargos
Tasa IVA                             | Number  | 7     | <TasaIVA>        | SÍ*         | 19.00 (nunca va en cero si hay monto neto)
IVA                                  | Number  | 18    | <IVA>            | SÍ*         | 19% del monto neto
Monto Total                          | Number  | 18    | <MntTotal>       | SÍ          | neto + exento + iva + imptos. adicionales
IVA No Retenido                      | Number  | 18    | <IVANoRet>       | NO          | Solo para Facturas de Compra con retención
Monto No Facturable                  | Number  | 18    | <MontoNF>        | NO          | Productos no facturables
```

**Para Boletas Electrónicas (Servicios Periódicos):**
```
Total Período                    | Number  | 18    | (ver especif.)   | NO          | Total del período
Saldo Anterior                   | Number  | 18    | (ver especif.)   | NO          | Saldo del período anterior
Valor a Pagar                    | Number  | 18    | (ver especif.)   | NO          | Monto a pagar
```

---

### 6. IMPUESTOS Y RETENCIONES ADICIONALES (Hasta 6 repeticiones)

```
Campo                           | Tipo    | Largo | TAG XML          | Obligatorio | Descripción
--------------------------------|---------|-------|------------------|-------------|-------------------------------------------
Código Tipo Impuesto            | Char    | 3     | <TipoImp>        | NO          | Ver tabla de códigos de impuestos
Tasa Impuesto                   | Number  | 5     | <TasaImp>        | NO          | Porcentaje del impuesto
Monto Impuesto                  | Number  | 18    | <MontoImp>       | NO          | Valor del impuesto o retención
```

**CÓDIGOS DE IMPUESTOS MÁS COMUNES:**
- 14: IVA de margen de comercialización
- 15: IVA retenido total
- 17: IVA ANTICIPADO FAENAMIENTO CARNE (5%)
- 18: IVA ANTICIPADO CARNE (5%)
- 19: IVA ANTICIPADO HARINA (12%)
- 23: Impuesto Adicional Art 37 letras a,b,c (15%)
- 24: Licores, Piscos, Whisky (31.5%)
- 25: Vinos (20.5%)
- 26: Cervezas y bebidas alcohólicas (20.5%)
- 27: Bebidas analcohólicas y minerales (10%)
- 271: Bebidas analcohólicas alto contenido azúcar (18%)
- 28: Impuesto Específico Diesel (1,5 UTM)
- 30/301: IVA RETENIDO LEGUMBRES (13%)
- 31: IVA RETENIDO SILVESTRES (Total)
- 32/321: IVA RETENIDO GANADO (8%)
- 33/331: IVA RETENIDO MADERA (8%)
- 34/341: IVA RETENIDO TRIGO (11%)

**Campos Especiales:**
```
Crédito Especial 65% Emp. Constructoras | Number  | 18    | <CredEC>         | NO          | IVA x 0.65
Monto Base Faenamiento Carne            | Number  | 18    | <MntBase>        | NO          | Monto informado
Monto Base Margen Comercialización      | Number  | 18    | <MntMargenCom>   | NO          | Monto informado
Valor Neto Comisiones y Otros Cargos    | Number  | 18    | <ValComNeto>     | NO          | Suma comisiones
Valor Neto Comisiones Exentas           | Number  | 18    | <ValComExe>      | NO          | Comisiones exentas
IVA Comisiones                          | Number  | 18    | <ValComIVA>      | NO          | IVA de comisiones
```

---

### 7. DETALLE DE PRODUCTOS/SERVICIOS (Líneas de Detalle)

**Se repite por cada línea de producto o servicio:**

```
Campo                           | Tipo    | Largo | TAG XML            | Obligatorio | Descripción
--------------------------------|---------|-------|--------------------|-------------|-------------------------------------------
Indicador de Exención           | Number  | 1     | <IndExe>           | NO          | 1-6 (ver tabla de indicadores)
Nombre del Item                 | Char    | 80    | <NmbItem>          | SÍ          | Nombre del producto o servicio
Descripción adicional           | Char    | 1000  | <DscItem>          | NO          | Descripción extendida
Cantidad                        | Number  | 18    | <QtyItem>          | SÍ*         | 12 enteros, 6 decimales
Unidad de Medida                | Char    | 4     | <UnmdItem>         | SÍ*         | UN, KG, MT, etc.
Precio Unitario                 | Number  | 18    | <PrcItem>          | SÍ          | 12 enteros, 6 decimales
Porcentaje de Descuento         | Number  | 5     | <DescuentoPct>     | NO          | 3 enteros, 2 decimales
Monto del Descuento             | Number  | 18    | <DescuentoMonto>   | NO          | Valor en pesos
Monto Item                      | Number  | 18    | <MontoItem>        | SÍ          | (Precio x Cantidad) - Descuento + Recargo
```

**INDICADORES DE EXENCIÓN:**
- 1: No afecto o exento de IVA
- 2: Producto o servicio no es facturable
- 3: Garantía de depósito por envases
- 4: Ítem No Venta (no será facturado)
- 5: Item a rebajar (para guías que rebajan guía anterior)
- 6: Producto o servicio no facturable negativo

*Obligatorio para facturas de venta, compra, notas que indican emisor opera como agente retenedor

---

## ESTRUCTURAS ESPECÍFICAS POR TIPO DE DOCUMENTO

### FACTURA ELECTRÓNICA (33)

**Datos Mínimos Obligatorios:**
- Encabezado: Tipo documento (33), Folio, Fecha emisión
- Emisor: RUT, Razón Social, Giro, ACTECO
- Receptor: RUT, Razón Social
- Detalle: Al menos 1 línea con Nombre Item, Cantidad, Unidad, Precio, Monto
- Totales: Monto Neto, Tasa IVA, IVA, Monto Total

**Uso:** Ventas afectas a IVA entre empresas

---

### FACTURA NO AFECTA O EXENTA (34)

**Datos Mínimos Obligatorios:**
- Igual que Factura Electrónica (33)
- En Detalle: Indicador de Exención = 1
- Totales: Monto Exento (en lugar de Monto Neto), NO se incluye IVA

**Uso:** Ventas exentas o no afectas a IVA

---

### BOLETA ELECTRÓNICA (39)

**Datos Mínimos Obligatorios:**
- Encabezado: Tipo documento (39), Folio, Fecha emisión, Indicador Servicio
- Emisor: RUT, Razón Social, Giro
- Receptor: RUT puede ser genérico (66.666.666-6 para consumidor final)
- Detalle: Al menos 1 línea
- Totales: Monto Neto, IVA, Monto Total

**Campos Adicionales para Servicios Periódicos:**
- Periodo Desde
- Periodo Hasta
- Total Período, Saldo Anterior, Valor a Pagar

**Uso:** Ventas a consumidores finales

---

### BOLETA EXENTA (41)

**Datos Mínimos Obligatorios:**
- Similar a Boleta Electrónica (39)
- En Detalle: Indicador de Exención = 1
- Totales: Monto Exento, NO se incluye IVA

**Uso:** Ventas exentas a consumidores finales

---

### GUÍA DE DESPACHO ELECTRÓNICA (52)

**Datos Mínimos Obligatorios:**
- Encabezado: Tipo documento (52), Folio, Fecha emisión, Tipo Despacho, Indicador Traslado
- Emisor: RUT, Razón Social, Giro, ACTECO, Dirección Origen, Comuna Origen
- Receptor: RUT, Razón Social, Dirección
- Transporte: Patente, RUT Transportista (si corresponde), Dirección Destino
- Detalle: Al menos 1 línea con productos despachados

**Campos Especiales:**
- Si Indicador Traslado = 8 o 9: Código Emisor Traslado Excepcional
- Si Código Traslado = 4: Folio Autorización y Fecha Autorización

**Uso:** Acompañar mercaderías en tránsito

---

### NOTA DE DÉBITO ELECTRÓNICA (56)

**Datos Mínimos Obligatorios:**
- Similar a Factura Electrónica (33)
- Debe referenciar el documento original que se está ajustando

**Uso:** Aumentar el valor de una factura previamente emitida

---

### NOTA DE CRÉDITO ELECTRÓNICA (61)

**Datos Mínimos Obligatorios:**
- Similar a Factura Electrónica (33)
- Indicador de no rebaja (si corresponde)
- Debe referenciar el documento original que se está anulando o ajustando

**Uso:** Anular o disminuir el valor de una factura previamente emitida

---

## FORMATO DEL ARCHIVO TXT DE ENTRADA

El archivo TXT debe tener la siguiente estructura de líneas:

```
LÍNEA 1: DATOS DEL DOCUMENTO (CABECERA - IdDoc)
    Tipo|Folio|FechaEmision|IndNoRebaja|TipoDespacho|...

LÍNEA 2: DATOS DEL EMISOR
    RUT|RazonSocial|Giro|Acteco|Sucursal|...

LÍNEA 3: DATOS DEL RECEPTOR
    RUT|RazonSocial|Giro|Direccion|Comuna|Ciudad|...

LÍNEA 4: DATOS DE TRANSPORTE (Opcional)
    Patente|RUTTransportista|DireccionDestino|...

LÍNEA 5: TOTALES
    MontoNeto|MontoExento|TasaIVA|IVA|MontoTotal|...

LÍNEAS 6+: DETALLE (Una línea por cada producto/servicio)
    IndExe|NombreItem|Descripcion|Cantidad|Unidad|Precio|Descuento%|MontoDesc|MontoItem|...
```

**NOTAS IMPORTANTES:**
- Separador de campos: pipe (|) o el delimitador configurado
- Los campos vacíos deben mantener el separador
- Cada línea corresponde a una sección del documento
- Las líneas de detalle se repiten según cantidad de productos

---

## VALIDACIONES IMPORTANTES

1. **RUT**: Debe tener formato XXXXXXXX-X con guión y dígito verificador
2. **Fechas**: Formato YYYY-MM-DD
3. **Montos**: 
   - Monto Total = Monto Neto + Monto Exento + IVA + Impuestos Adicionales
   - IVA = Monto Neto * (Tasa IVA / 100)
4. **Folio**: Debe estar dentro del rango autorizado por el CAF (Código de Autorización de Folios)
5. **Tasa IVA**: Actualmente 19% (19.00)

---

## DIRECTORIOS DEL SISTEMA ACEPTA

### Ambiente de Producción
```
/Acepta/DTEService/custodium.com/dte-produccion/
    ├── etc/
    │   ├── licencia.xml
    │   ├── ca4xml/ (configuración)
    │   ├── ca4upd/ (configuración)
    │   └── cert/
    │       ├── caf/ (Códigos de Autorización de Folios - formato .pem)
    │       └── pki/ (Certificados de firma electrónica)
    └── var/
        └── ca4xml/
            └── output/
                ├── done/ (documentos procesados correctamente)
                ├── errors/ (documentos con error)
                ├── dump/ (XML detalle de errores)
                ├── log/ (logs de procesamiento)
                ├── archivos-pdf/ (PDFs generados)
                ├── resultados/ (CSV con resultados diarios)
                ├── testing/ (respaldo XML timbrados)
                ├── tmp/ (temporales)
                ├── upload/ (cola de espera - NO BORRAR)
                └── queue/ (BD interna ca4xml)
```

### Ambiente de Pruebas
```
/Acepta/DTEService/custodium.com/dte-pruebas/
    [Misma estructura que producción]
```

---

## RESPUESTA DE PROCESAMIENTO

Una vez procesado el documento, el sistema genera una respuesta con:

```
Estado | RUT Emisor | Tipo Doc | Folio | Fecha Emisión | RUT Receptor | URL
```

**Estados Posibles:**
- **OK**: Documento emitido correctamente
- **ERROR**: Error en el procesamiento (se detalla el error)
- **EMITIDO**: Documento generado exitosamente
- **EMITIDO-SIN IMPRESION**: Documento emitido pero sin PDF

---

## BITÁCORA DE PROCESAMIENTO

El sistema mantiene una bitácora con:

- TimeStamp del procesamiento (HH:MM:SS)
- Estado del procesamiento
- Archivo procesado
- Datos del documento (RUT emisor, tipo, folio, fecha, receptor)
- URL del documento electrónico
- Ruta del PDF generado
- Ruta del log de errores (si corresponde)

---

## EJEMPLO DE PROMPT PARA SOLICITAR EMISIÓN

### Ejemplo 1: Factura Electrónica

```
"Necesito emitir una FACTURA ELECTRÓNICA (tipo 33) con los siguientes datos:

DOCUMENTO:
- Folio: 12345
- Fecha de emisión: 2025-11-05
- Forma de pago: Crédito (30 días)

EMISOR:
- RUT: 76.123.456-7
- Razón Social: EMPRESA DEMO LTDA
- Giro: VENTA AL POR MAYOR DE PRODUCTOS ALIMENTICIOS
- ACTECO: 462100
- Dirección: AV. PRINCIPAL 123, SANTIAGO
- Comuna: SANTIAGO
- Teléfono: +56912345678

RECEPTOR:
- RUT: 77.654.321-K
- Razón Social: CLIENTE EJEMPLO S.A.
- Giro: COMERCIO AL POR MENOR
- Dirección: CALLE COMERCIO 456
- Comuna: PROVIDENCIA

DETALLE DE PRODUCTOS:
1. Producto A, cantidad: 10, unidad: UN, precio unitario: 15000
2. Producto B, cantidad: 5, unidad: KG, precio unitario: 8500, descuento 5%

TOTALES:
- Subtotal Neto: [calculado]
- IVA 19%: [calculado]
- Total: [calculado]
"
```

### Ejemplo 2: Boleta Electrónica

```
"Necesito emitir una BOLETA ELECTRÓNICA (tipo 39) con los siguientes datos:

DOCUMENTO:
- Folio: 5678
- Fecha de emisión: 2025-11-05
- Indicador de servicio: 3 (Factura de Servicios)

EMISOR:
- RUT: 76.123.456-7
- Razón Social: MI NEGOCIO SPA
- Giro: VENTA DE PRODUCTOS VARIOS

RECEPTOR:
- RUT: 66.666.666-6 (consumidor final)
- Razón Social: CLIENTE GENERAL

DETALLE:
1. Servicio de instalación, cantidad: 1, precio: 50000
2. Producto X, cantidad: 3, precio: 12000

TOTALES:
- Neto: 86000
- IVA: 16340
- Total: 102340
"
```

### Ejemplo 3: Guía de Despacho

```
"Necesito emitir una GUÍA DE DESPACHO ELECTRÓNICA (tipo 52) con los siguientes datos:

DOCUMENTO:
- Folio: 789
- Fecha de emisión: 2025-11-05
- Tipo de despacho: 2 (por cuenta del emisor al receptor)
- Indicador de traslado: 1 (Operación constituye venta)

EMISOR:
- RUT: 76.123.456-7
- Razón Social: DISTRIBUIDORA DEMO LTDA
- Dirección Origen: BODEGA CENTRAL, AV. LOGISTICA 500, QUILICURA

RECEPTOR:
- RUT: 77.654.321-K
- Razón Social: SUPERMERCADO ABC
- Dirección: CALLE COMPRAS 200, LAS CONDES

TRANSPORTE:
- Patente: ABCD12
- RUT Transportista: 12.345.678-9
- Dirección Destino: CALLE COMPRAS 200, LAS CONDES

DETALLE:
1. Caja Producto A (12 unidades), cantidad: 50 cajas, peso: 500 KG
2. Caja Producto B (24 unidades), cantidad: 30 cajas, peso: 300 KG
"
```

### Ejemplo 4: Nota de Crédito

```
"Necesito emitir una NOTA DE CRÉDITO ELECTRÓNICA (tipo 61) con los siguientes datos:

DOCUMENTO:
- Folio: 234
- Fecha de emisión: 2025-11-05
- Referencia: Factura Nº 12345 del 01-11-2025

EMISOR:
- RUT: 76.123.456-7
- Razón Social: EMPRESA DEMO LTDA

RECEPTOR:
- RUT: 77.654.321-K
- Razón Social: CLIENTE EJEMPLO S.A.

MOTIVO: Anulación total de Factura 12345 por error en el precio

DETALLE:
1. Producto A, cantidad: -10, precio unitario: 15000, monto: -150000
2. Producto B, cantidad: -5, precio unitario: 8500, monto: -42500

TOTALES:
- Subtotal Neto: -192500
- IVA 19%: -36575
- Total: -229075
"
```

---

## CHECKLIST ANTES DE EMITIR

- [ ] Verificar que el RUT emisor y receptor sean válidos
- [ ] Confirmar que el folio esté dentro del rango autorizado (CAF vigente)
- [ ] Validar que las fechas estén en formato correcto (YYYY-MM-DD)
- [ ] Revisar que los cálculos de IVA y totales sean correctos
- [ ] Asegurar que los productos tengan cantidad, unidad y precio
- [ ] Para guías de despacho: verificar datos de transporte
- [ ] Para notas de crédito/débito: incluir referencia al documento original
- [ ] Verificar que el certificado digital esté vigente
- [ ] Confirmar que haya folios disponibles para el tipo de documento

---

**FIN DEL DOCUMENTO**
