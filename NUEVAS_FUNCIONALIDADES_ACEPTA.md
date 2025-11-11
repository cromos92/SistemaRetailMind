# ✨ NUEVAS FUNCIONALIDADES - Generador TXT Acepta

## 🎉 FUNCIONALIDADES AGREGADAS

Se han agregado **2 nuevas funcionalidades** al generador de archivos TXT para Acepta:

---

## 1. 💰 DESCUENTOS / RECARGOS GLOBALES

### ¿Qué es?
Permite aplicar un descuento global sobre el total de la factura (además de los descuentos por producto).

### Campos disponibles:

#### Descuento Global en Pesos
```
Ejemplo: $10,000
Resultado: Se descuentan $10,000 del subtotal
```

#### Descuento Global en Porcentaje
```
Ejemplo: 5%
Resultado: Se descuenta el 5% del subtotal
```

#### Tipo de Descuento
- **Opción 1:** Descuento Global Afecto (con IVA)
- **Opción 2:** Descuento Global No Afecto
- **Opción 3:** Descuento Global Exento

### Cómo funciona:

**Sin descuento global:**
```
Productos: $190,375
IVA (19%): $36,171
Total: $226,546
```

**Con descuento global de $10,000:**
```
Productos: $190,375
Descuento: -$10,000
Neto: $180,375
IVA (19%): $34,271
Total: $214,646
```

**Con descuento global de 5%:**
```
Productos: $190,375
Descuento (5%): -$9,519
Neto: $180,856
IVA (19%): $34,363
Total: $215,219
```

### En la interfaz:
1. Ingresa los productos
2. Ve a la sección "Descuentos / Recargos Globales"
3. Ingresa el monto en $ o el porcentaje
4. Haz clic en "Calcular"
5. Los totales se actualizan automáticamente

---

## 2. 🔗 REFERENCIAS A OTROS DOCUMENTOS

### ¿Qué es?
Permite referenciar otros documentos relacionados con la factura (Órdenes de Compra, Guías, HES, etc.).

### Tipos de documentos que puedes referenciar:

| Tipo | Código | Uso Común |
|------|--------|-----------|
| **Orden de Compra** | 801 | Factura generada por OC del cliente |
| **Nota de Pedido** | 802 | Pedido del cliente |
| **Contrato** | 803 | Contrato marco |
| **Guía de Despacho** | 52 | Guía previa de productos |
| **Factura** | 33 | Factura anterior |
| **Factura Exenta** | 34 | Factura exenta anterior |
| **HES** | HES | Hoja de Entrada al Servicio |
| **SET** | SET | Set de Pruebas |
| **Guía Manual** | 50 | Guía de despacho manual |

### Campos de la referencia:

#### 1. Tipo de Documento
Selecciona el tipo de documento que estás referenciando.

#### 2. Folio / Número
Número o folio del documento.
```
Ejemplos:
- OC-12345
- GD-9876
- HES-2024-001
```

#### 3. Fecha del Documento
Fecha de emisión del documento referenciado.

#### 4. Razón de Referencia (Solo para NC/ND)
- **Opción 1:** Anula documento de referencia
- **Opción 2:** Corrige texto del documento
- **Opción 3:** Corrige montos

### Ejemplos de uso:

#### Ejemplo 1: Factura con Orden de Compra
```
Factura Electrónica #12345
Referencia: Orden de Compra OC-98765
```

**En el TXT:**
```
33|12345|2025-11-08||2|1|1||}
...
801|OC-98765|2025-11-03||}  ← Referencia a OC
~
```

#### Ejemplo 2: Factura con Guía de Despacho
```
Factura Electrónica #12345
Referencia: Guía de Despacho 5432
```

**En el TXT:**
```
33|12345|2025-11-08||2|1|1||}
...
52|5432|2025-11-07||}  ← Referencia a Guía
~
```

#### Ejemplo 3: Nota de Crédito que anula Factura
```
Nota de Crédito #234
Referencia: Factura #12345
Razón: Anula documento de referencia
```

**En el TXT:**
```
61|234|2025-11-08||2|1|1||}
...
33|12345|2025-11-05|1|}  ← Referencia con razón 1 (Anula)
~
```

### En la interfaz:
1. Ve a la sección "Referencias a Otros Documentos"
2. Haz clic en "Agregar Referencia"
3. Selecciona el tipo de documento
4. Ingresa el folio/número
5. Ingresa la fecha (opcional)
6. Selecciona razón (solo para NC/ND)
7. Puedes agregar múltiples referencias

---

## 📋 FORMATO EN EL ARCHIVO TXT

### Estructura completa con nuevas funcionalidades:

```
33|12345|2025-11-08||2|1|1||}                              ← IdDoc
76337843-8|EMPRESA DEMO LTDA|...|USUARIO|}                 ← Emisor
77654321-K||CLIENTE EJEMPLO S.A.|...||}                    ← Receptor
||||||}                                                     ← Transporte
180375|0|19|34271|214646|||||||||}                         ← Totales (con descuento)
801|OC-98765|2025-11-03||}                                ← Referencia a OC
~
|PROD001 PRODUCTO EJEMPLO A||10.000000|UN|15000...|PROD001|}
|PROD002 PRODUCTO EJEMPLO B||5.000000|UN|8500...|PROD002|}
~
~
~
USUARIO|||DOSCIENTOS CATORCE MIL PESOS|...|HP LaserJet|4|}
~
\
```

---

## 🧪 PROBAR LAS NUEVAS FUNCIONALIDADES

### Prueba 1: Descuento Global

1. Carga el ejemplo (botón "Cargar Ejemplo")
2. Ve a "Descuentos / Recargos Globales"
3. Ingresa: Descuento Global ($): `10000`
4. Haz clic en "Calcular"
5. Observa cómo cambian los totales
6. Genera el TXT
7. Verifica que el total sea correcto

### Prueba 2: Referencia a Orden de Compra

1. Carga el ejemplo
2. Ve a "Referencias a Otros Documentos"
3. Ya viene una referencia de ejemplo (OC-98765)
4. Genera el TXT
5. Abre el archivo
6. Verifica que después de totales aparezca:
   ```
   801|OC-98765|2025-XX-XX||}
   ```

### Prueba 3: Múltiples Referencias

1. Carga el ejemplo
2. Agrega más referencias:
   - Referencia 1: Orden de Compra OC-98765
   - Referencia 2: Guía de Despacho GD-5432
   - Referencia 3: HES HES-2024-001
3. Genera el TXT
4. Verifica que aparezcan las 3 referencias

---

## 💡 CASOS DE USO REALES

### Caso 1: Factura por Orden de Compra
```
Cliente envía OC → Emisor genera Factura → Referencia a OC
Beneficio: Trazabilidad entre documentos
```

### Caso 2: Factura después de Despacho
```
Emisor despacha (Guía 52) → Cliente recibe → Emisor factura
Referencia: Guía de Despacho
Beneficio: Relaciona factura con despacho
```

### Caso 3: Nota de Crédito
```
Factura con error → Emisor anula con NC
Referencia: Factura original con razón "Anula"
Beneficio: Cumplimiento normativo SII
```

### Caso 4: Descuento por Volumen
```
Cliente compra gran volumen → Descuento global 10%
Beneficio: Descuento aplicado sobre el total
```

---

## ✅ CHECKLIST DE VERIFICACIÓN

### Descuentos Globales:
- [ ] Campo de descuento en $ funciona
- [ ] Campo de descuento en % funciona
- [ ] Totales se recalculan correctamente
- [ ] IVA se aplica después del descuento
- [ ] TXT generado muestra totales correctos

### Referencias:
- [ ] Botón "Agregar Referencia" funciona
- [ ] Tipos de documento disponibles
- [ ] Folio/número se captura
- [ ] Fecha se captura
- [ ] Razón (NC/ND) disponible
- [ ] Múltiples referencias funcionan
- [ ] TXT generado incluye referencias
- [ ] Formato correcto en TXT

---

## 📚 DOCUMENTACIÓN RELACIONADA

1. **CORRECCIONES_APLICADAS_TXT_ACEPTA.md** - Formato TXT corregido
2. **MODULO_GENERACION_TXT_ACEPTA.md** - Documentación técnica completa
3. **DEBUG_FOLIO_ACEPTA.md** - Guía de debugging

---

## 🎓 TIPS Y MEJORES PRÁCTICAS

### Descuentos Globales:
- ✅ Usa descuento en $ para montos fijos
- ✅ Usa descuento en % para descuentos proporcionales
- ✅ Siempre haz clic en "Calcular" después de cambiar descuentos
- ⚠️ El descuento no puede ser mayor que el subtotal

### Referencias:
- ✅ Siempre incluye la fecha del documento referenciado
- ✅ Usa el código correcto (801 para OC, no "OC")
- ✅ Para NC/ND, especifica la razón
- ✅ Puedes referenciar múltiples documentos
- ⚠️ El folio debe coincidir con el documento real

---

## 🚀 ESTADO ACTUAL

- ✅ Descuentos globales implementados
- ✅ Referencias a documentos implementadas
- ✅ Interfaz visual actualizada
- ✅ Backend Python procesando correctamente
- ✅ Formato TXT correcto
- ✅ Debugging activo
- ✅ Ejemplo con referencia incluido

---

**Fecha:** Noviembre 8, 2025  
**Versión:** 3.0 - Descuentos y Referencias  
**Estado:** ✅ LISTO PARA USAR

