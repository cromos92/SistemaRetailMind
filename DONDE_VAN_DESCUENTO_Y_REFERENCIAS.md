# 📍 UBICACIÓN: Descuento Global y Referencias en TXT

## 📋 ESTRUCTURA COMPLETA DEL TXT

```
LÍNEA 1:  IdDoc          (tipo, folio, fechas, etc.)
LÍNEA 2:  Emisor         (RUT, razón social, etc.)
LÍNEA 3:  Receptor       (RUT, razón social, etc.)
LÍNEA 4:  Transporte     (patente, destino, etc.)
LÍNEA 5:  Totales        (neto, IVA, total, DESCUENTO GLOBAL ✅)
LÍNEA 6+: Referencias    (OC, Guías, etc.) ✅
~
PRODUCTOS
~
~
~
INFO ADICIONAL
~
\
```

---

## 1️⃣ DESCUENTO GLOBAL → LÍNEA 5 (Totales)

### Ubicación exacta:
**Campo 11 de la Línea 5**

```
190375|0|19|36171|226546|||||10000|||}
│      │  │  │     │      │   │
│      │  │  │     │      │   └─ Campo 11: DESCUENTO GLOBAL ✅
│      │  │  │     │      └───── Campos 6-10 (vacíos)
│      │  │  │     └────────────  Campo 5: Monto Total
│      │  │  └──────────────────  Campo 4: IVA
│      │  └─────────────────────  Campo 3: Tasa IVA
│      └────────────────────────  Campo 2: Monto Exento
└───────────────────────────────  Campo 1: Monto Neto
```

### Ejemplo SIN descuento:
```
190375|0|19|36171|226546|||||||||}
                          ↑↑↑↑↑
                          Sin descuento (campos vacíos)
```

### Ejemplo CON descuento de $10,000:
```
180375|0|19|34271|214646|||||10000|||}
                          ↑    ↑
                          │    └─ Descuento: $10,000
                          └────── 5 campos vacíos antes
```

---

## 2️⃣ REFERENCIAS → DESPUÉS DE LÍNEA 5, ANTES DE PRODUCTOS

### Ubicación exacta:
**Entre Totales y el separador ~**

```
LÍNEA 5:  190375|0|19|36171|226546|||||||||}  ← Totales
REF 1:    801|OC-98765|2025-11-03||}          ← REFERENCIA 1 ✅
REF 2:    52|GD-5432|2025-11-05||}            ← REFERENCIA 2 ✅
~                                              ← Separador antes de productos
PROD 1:   |PROD001 PRODUCTO A|...|}
```

### Formato de cada referencia:
```
801|OC-98765|2025-11-03||}
│   │        │          │
│   │        │          └─ Razón (solo NC/ND) + cierre }
│   │        └──────────── Fecha del documento
│   └───────────────────── Folio/Número
└───────────────────────── Tipo documento (801=OC, 52=Guía, etc.)
```

---

## 📄 EJEMPLO COMPLETO CON TODO

### Con Descuento Global + Referencias:

```
33|12345|2025-11-10||2|1|2|2025-12-10|}                          ← 1. IdDoc
76337843-8|EMPRESA DEMO LTDA|COMERCIO AL POR MENOR|521000|||AV. PRINCIPAL 123|SANTIAGO|SANTIAGO|USUARIO|}  ← 2. Emisor
77654321-K||CLIENTE EJEMPLO S.A.|COMERCIO||CALLE COMERCIO 456|PROVIDENCIA|SANTIAGO|||}  ← 3. Receptor
||||||}                                                           ← 4. Transporte
180375|0|19|34271|214646|||||10000|||}                          ← 5. Totales (CON DESCUENTO $10,000)
801|OC-98765|2025-11-03||}                                      ← 6. Referencia OC
52|GD-5432|2025-11-05||}                                        ← 7. Referencia Guía
~                                                                 ← Separador
|PROD001 PRODUCTO EJEMPLO A||10.000000|UN|15000.000000||0|150000|PROD001|}  ← Producto 1
|PROD002 PRODUCTO EJEMPLO B||5.000000|UN|8500.000000||2125|40375|PROD002|}  ← Producto 2
~
~
~
USUARIO|||DOSCIENTOS CATORCE MIL PESOS|||||||HP LaserJet|4|}    ← Info adicional
~
\
```

---

## 🔍 DESGLOSE DETALLADO

### LÍNEA 5 - Totales (con descuento):

```
Campo  Valor    Descripción
─────  ───────  ────────────────────────────
  1    180375   Monto Neto (después de desc.)
  2    0        Monto Exento
  3    19       Tasa IVA
  4    34271    IVA
  5    214646   Monto Total
  6             IVA No Retenido
  7             Monto No Facturable
  8             Total Período
  9             Saldo Anterior
 10             Valor a Pagar
 11    10000    ✅ DESCUENTO GLOBAL
 12             Tipo Descuento Global
 13             Recargo Global
 14             Tipo Recargo Global
 15    }        Cierre
```

### REFERENCIAS (múltiples):

```
Ref #  Tipo  Folio      Fecha        Razón  
─────  ────  ─────────  ───────────  ─────
  1    801   OC-98765   2025-11-03   
  2    52    GD-5432    2025-11-05   
  3    HES   HES-2024   2025-11-01   

TXT:
801|OC-98765|2025-11-03||}
52|GD-5432|2025-11-05||}
HES|HES-2024|2025-11-01||}
```

---

## 🧪 CÓMO VERIFICAR

### 1. Generar TXT con descuento:
```
1. Cargar ejemplo
2. Ingresar descuento: $10,000
3. Generar TXT
4. Buscar línea 5: ....|10000|||}
                        ↑
                        Debe aparecer
```

### 2. Generar TXT con referencias:
```
1. Cargar ejemplo (ya viene con OC)
2. Agregar más referencias
3. Generar TXT
4. Buscar después de totales:
   801|OC-98765|...||}
   52|GD-5432|...||}
```

### 3. Generar TXT con AMBOS:
```
1. Cargar ejemplo
2. Ingresar descuento: $10,000
3. Ya tiene referencia OC-98765
4. Generar TXT
5. Verificar:
   - Línea 5: ....|10000|||}  ✅
   - Después: 801|OC-98765|...|}  ✅
```

---

## 📊 COMPARACIÓN

### SIN descuento ni referencias:
```
190375|0|19|36171|226546|||||||||}  ← Totales (sin descuento)
~                                   ← Directo a productos
|PROD001 ...|
```

### CON descuento SIN referencias:
```
180375|0|19|34271|214646|||||10000|||}  ← Totales (con descuento)
~                                        ← Directo a productos
|PROD001 ...|
```

### SIN descuento CON referencias:
```
190375|0|19|36171|226546|||||||||}  ← Totales (sin descuento)
801|OC-98765|2025-11-03||}          ← Referencias
~                                   ← Luego productos
|PROD001 ...|
```

### CON descuento Y referencias:
```
180375|0|19|34271|214646|||||10000|||}  ← Totales (con descuento)
801|OC-98765|2025-11-03||}              ← Referencias
52|GD-5432|2025-11-05||}
~                                       ← Luego productos
|PROD001 ...|
```

---

## ✅ IMPLEMENTACIÓN ACTUAL

### Backend Python (`views_modulo_documentos.py`):

**Línea 5 - Totales:**
```python
# Descuento global (si existe)
descuento_global = totales.get('descuento_global', 0)
descuento_global_str = formatear_monto(descuento_global) if descuento_global else ''

linea5 = [
    formatear_monto(totales.get('monto_neto', 0)),
    formatear_monto(totales.get('monto_exento', '')),
    tasa_iva_str,
    formatear_monto(totales.get('iva', 0)),
    formatear_monto(totales.get('monto_total', 0)),
    '', '', '', '', '',
    descuento_global_str,  # ✅ Campo 11
    '', '', '', '',
    '}'
]
```

**Referencias:**
```python
referencias = datos.get('referencias', [])
if referencias:
    for ref in referencias:
        linea_ref = [
            str(ref.get('tipo_documento', '')),
            str(ref.get('folio', '')),
            formatear_fecha(ref.get('fecha', '')),
            str(ref.get('razon', '')),
            '}'
        ]
        lineas.append(separador.join(linea_ref))
```

---

## 🎯 RESUMEN

| Elemento | Ubicación | Campo/Línea |
|----------|-----------|-------------|
| **Descuento Global** | Línea 5 (Totales) | Campo 11 |
| **Referencias** | Después de Línea 5 | Líneas adicionales |
| **Productos** | Después de ~ | Múltiples líneas |

---

## 🚀 PROBAR AHORA

1. Reinicia el servidor
2. Carga el ejemplo
3. Agrega descuento: $10,000
4. Ya tiene referencia: OC-98765
5. Genera TXT
6. Verifica que ambos aparezcan ✅

---

**Fecha:** Noviembre 10, 2025  
**Versión:** 5.0 - Descuento y Referencias  
**Estado:** ✅ IMPLEMENTADO CORRECTAMENTE

