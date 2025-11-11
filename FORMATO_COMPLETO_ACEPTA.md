# 📋 FORMATO COMPLETO TXT ACEPTA - TODAS LAS FUNCIONALIDADES

## 🎯 ESTRUCTURA COMPLETA

```
LÍNEA 1:  IdDoc
LÍNEA 2:  Emisor
LÍNEA 3:  Receptor
LÍNEA 4:  Transporte
LÍNEA 5:  Totales
[LÍNEA 6]: Descuento/Recargo Global (OPCIONAL) ← Si hay descuento global
~
PRODUCTOS
~
REFERENCIAS (OPCIONAL) ← Si hay referencias
~
~
INFO ADICIONAL (con monto en letras)
~
\
```

---

## 📍 UBICACIONES EXACTAS

### 1. DESCUENTO/RECARGO GLOBAL
**Ubicación:** DESPUÉS de Línea 5, ANTES del primer `~`

```
180375|0|19|34271|214646|||||||||||||}     ← Línea 5 (Totales)
D|DESCUENTO GLOBAL|10000||}                ← Descuento global
~                                          ← Separador
|Item PRODUCTO A|...|                      ← Productos
```

**Formato:**
```
D|DESCUENTO GLOBAL|10000||}    ← Descuento
R|RECARGO ENVIO|5000||}        ← Recargo
```

### 2. REFERENCIAS
**Ubicación:** DESPUÉS de productos y primer `~`, ANTES de `~~`

```
|Item PRODUCTO B|...|Item|}     ← Último producto
~                               ← Primer separador
801|| OC-98765 | 2025-11-05|| |}  ← Referencia 1
52|| GD-5432 | 2025-11-08|| |}   ← Referencia 2
~                               ← Segundo separador
~                               ← Tercer separador
USUARIO|||MONTO LETRAS|...      ← Info adicional
```

**Formato:**
```
801|| OC-98765 | 2025-11-05|| |}     ← Orden Compra
52|| GD-5432 | 2025-11-08|| |}       ← Guía Despacho
33|12345|2025-11-01|1|ANULA FACTURA|} ← Factura (para NC)
```

---

## 📄 EJEMPLOS COMPLETOS

### EJEMPLO 1: Factura Simple (sin descuentos ni referencias)
```
33|4578|2025-11-10||2|1|1||}
78503140-7|EMPRESA DEMO LTDA|COMERCIO AL POR MENOR|521000|||AV. PRINCIPAL 123|SANTIAGO|SANTIAGO|USUARIO|}
18312585-0||CLIENTE EJEMPLO S.A.|COMERCIO||CALLE COMERCIO 456|PROVIDENCIA|SANTIAGO|||}
|||||}
190375|0|19|36171|226546|||||||||||||}
~
|Item PRODUCTO EJEMPLO A||10|UN|15000|||150000|Item|}
|Item PRODUCTO EJEMPLO B||5|UN|8500|5.00|2125|40375|Item|}
~
~
~
USUARIO|||CIENTO NOVENTA MIL PESOS  |||||||HP LaserJet Professional P1102w|4|}
~
\
```

### EJEMPLO 2: Factura CON Descuento Global
```
33|4578|2025-11-10||2|1|1||}
78503140-7|EMPRESA DEMO LTDA|...|USUARIO|}
18312585-0||CLIENTE EJEMPLO S.A.|...||}
|||||}
190375|0|19|36171|226546|||||||||||||}
D|DESCUENTO GLOBAL|10000||}             ← DESCUENTO AQUÍ
~
|Item PRODUCTO EJEMPLO A||10|UN|15000|||150000|Item|}
|Item PRODUCTO EJEMPLO B||5|UN|8500|5.00|2125|40375|Item|}
~
~
~
USUARIO|||CIENTO OCHENTA MIL PESOS  |||||||HP LaserJet Professional P1102w|4|}
~
\
```

### EJEMPLO 3: Factura CON Referencia (Orden de Compra)
```
33|4578|2025-11-10||2|1|1||}
78503140-7|EMPRESA DEMO LTDA|...|USUARIO|}
18312585-0||CLIENTE EJEMPLO S.A.|...||}
|||||}
190375|0|19|36171|226546|||||||||||||}
~
|Item PRODUCTO EJEMPLO A||10|UN|15000|||150000|Item|}
|Item PRODUCTO EJEMPLO B||5|UN|8500|5.00|2125|40375|Item|}
~
801|| OC-98765 | 2025-11-05|| |}        ← REFERENCIA AQUÍ
~
~
USUARIO|||CIENTO NOVENTA MIL PESOS  |||||||HP LaserJet Professional P1102w|4|}
~
\
```

### EJEMPLO 4: Factura CON TODO (Descuento + Referencia)
```
33|4578|2025-11-10||2|1|1||}
78503140-7|EMPRESA DEMO LTDA|...|USUARIO|}
18312585-0||CLIENTE EJEMPLO S.A.|...||}
|||||}
190375|0|19|36171|226546|||||||||||||}
D|DESCUENTO GLOBAL|10000||}             ← Descuento
~
|Item PRODUCTO EJEMPLO A||10|UN|15000|||150000|Item|}
|Item PRODUCTO EJEMPLO B||5|UN|8500|5.00|2125|40375|Item|}
~
801|| OC-98765 | 2025-11-05|| |}        ← Referencia
~
~
USUARIO|||CIENTO OCHENTA MIL PESOS  |||||||HP LaserJet Professional P1102w|4|}
~
\
```

### EJEMPLO 5: Nota de Crédito (con referencia obligatoria)
```
61|234|2025-11-10||2|1|1||}
78503140-7|EMPRESA DEMO LTDA|...|USUARIO|}
18312585-0||CLIENTE EJEMPLO S.A.|...||}
|||||}
-190375|0|19|-36171|-226546|||||||||||||}  ← Montos NEGATIVOS
~
|Item PRODUCTO EJEMPLO A||-10|UN|-15000|||-150000|Item|}  ← Cantidad y montos NEGATIVOS
~
33|4578|2025-11-05|1|ANULA FACTURA 4578|}  ← Referencia OBLIGATORIA
~
~
USUARIO|||DOSCIENTOS VEINTISEIS MIL PESOS  |||||||HP LaserJet Professional P1102w|4|}
~
\
```

---

## 🔧 CAMPOS DE REFERENCIA

```
[TipoDoc]|[Folio]|[Fecha]|[CodRef]|[Razon]|}
```

### Campos:
1. **TipoDoc:** Tipo de documento (33, 52, 801, etc.)
2. **Folio:** Número del documento
3. **Fecha:** Fecha del documento (YYYY-MM-DD)
4. **CodRef:** Código de referencia
5. **Razon:** Razón/descripción

### Códigos de Referencia:
- **1** = Anula documento
- **2** = Corrige texto
- **3** = Corrige montos
- **0** = Sin razón específica (para facturas que referencian OC/Guías)

### Tipos de Documento:
- **33** = Factura Electrónica
- **34** = Factura Exenta
- **52** = Guía de Despacho
- **61** = Nota de Crédito
- **801** = Orden de Compra
- **802** = Nota de Pedido
- **803** = Contrato
- **HES** = Hoja Entrada Servicio
- **SET** = Set Pruebas

---

## 💰 CAMPOS DE DESCUENTO

### Descuento por Línea (en productos):
```
|Cod Nombre||Cant|Unid|Precio|Desc%|DescMonto|MontoTotal|Cod|}
                               └────┘ └────────┘
                               Campo6  Campo7
```

### Descuento Global (línea separada):
```
D|DESCUENTO GLOBAL|10000||}
│ │                │      │
│ │                │      └─ Otro moneda + cierre
│ │                └──────── Valor del descuento
│ └───────────────────────── Glosa/descripción
└─────────────────────────── Tipo (D=Descuento, R=Recargo)
```

---

## ✅ IMPLEMENTACIÓN ACTUAL

Ya implementé:
- ✅ Descuentos por línea (campos 6-7 de productos)
- ✅ Descuento global (línea después de totales)
- ✅ Referencias (después de ~~)
- ✅ Monto en letras completo
- ✅ Todos los cierres `|}`

---

## 🚀 PROBAR CON DESCUENTO

1. Recarga la interfaz
2. Cargar Ejemplo
3. Agregar descuento: $10,000
4. Generar TXT
5. Verificar que aparezca:
```
D|DESCUENTO GLOBAL|10000||}
```

---

**Documentación guardada en: `FORMATO_COMPLETO_ACEPTA.md`**

¡El formato está completo y correcto! 🎉
