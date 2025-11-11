# 💰 DESCUENTO GLOBAL - CÓMO FUNCIONA

## ✅ FUNCIONAMIENTO CORRECTO

El descuento global se muestra de **DOS formas** en el TXT:

---

## 1️⃣ EN LOS TOTALES (Línea 5) - YA APLICADO

```
180375|0|19|34271|214646|||||||||||||}
↑      ↑  ↑  ↑     ↑
│      │  │  │     └─ Total: $214,646 (con descuento aplicado)
│      │  │  └─────── IVA: $34,271 (sobre neto con descuento)
│      │  └────────── Tasa IVA: 19
│      └───────────── Exento: 0
└──────────────────── Neto: $180,375 (CON descuento aplicado)
```

**Cálculo:**
```
Productos:        $190,375
Descuento:        -$10,000
Neto Final:       $180,375  ← Este va en línea 5
IVA (19%):        $34,271
Total:            $214,646
```

---

## 2️⃣ LÍNEA INFORMATIVA - MUESTRA EL DESCUENTO

```
D|DESCUENTO GLOBAL|10000||}
│ │                │      │
│ │                │      └─ Cierre ||}
│ │                └──────── Valor: $10,000
│ └───────────────────────── Glosa: DESCUENTO GLOBAL
└─────────────────────────── Tipo: D (Descuento)
```

Esta línea es **informativa** y muestra explícitamente cuánto se descontó.

---

## 📄 ESTRUCTURA COMPLETA DEL TXT

```
33|4578|2025-11-10||2|1|1|2025-11-10|}                          ← Línea 1: IdDoc
78503140-7|EMPRESA DEMO LTDA|...|USUARIO|}                       ← Línea 2: Emisor
18312585-0||CLIENTE EJEMPLO S.A.|...||}                          ← Línea 3: Receptor
|||||}                                                            ← Línea 4: Transporte
180375|0|19|34271|214646|||||||||||||}                          ← Línea 5: Totales (CON descuento)
D|DESCUENTO GLOBAL|10000||}                                     ← Línea INFORMATIVA del descuento
~
|Item PRODUCTO EJEMPLO A||10|UN|15000|||150000|Item|}
|Item PRODUCTO EJEMPLO B||5|UN|8500|5.00|2125|40375|Item|}
~
801|| OC-98765 | 2025-11-05|| |}
~
~
USUARIO|||CIENTO OCHENTA MIL PESOS  |||||||HP LaserJet Professional P1102w|4|}
~
\
```

---

## 🔢 MATEMÁTICA COMPLETA

### Sin descuento:
```
Producto A: 10 x $15,000 = $150,000
Producto B: 5 x $8,500 = $42,500 (antes del desc. línea)
            Desc 5%: -$2,125
            Neto: $40,375
────────────────────────────────────
Subtotal:              $190,375
Descuento Global:      $0
────────────────────────────────────
Neto:                  $190,375
IVA (19%):             $36,171
────────────────────────────────────
TOTAL:                 $226,546
```

**TXT:**
```
190375|0|19|36171|226546|||||||||||||}  ← Sin línea D|
```

### Con descuento global de $10,000:
```
Producto A: 10 x $15,000 = $150,000
Producto B: 5 x $8,500 = $42,500
            Desc 5%: -$2,125
            Neto: $40,375
────────────────────────────────────
Subtotal:              $190,375
Descuento Global:      -$10,000  ← Se resta aquí
────────────────────────────────────
Neto Final:            $180,375
IVA (19%):             $34,271
────────────────────────────────────
TOTAL:                 $214,646
```

**TXT:**
```
180375|0|19|34271|214646|||||||||||||}  ← Totales con descuento aplicado
D|DESCUENTO GLOBAL|10000||}              ← Línea informativa
```

---

## 📊 COMPARACIÓN

| Situación | Línea 5 (Totales) | Línea Informativa | Observación |
|-----------|-------------------|-------------------|-------------|
| **Sin descuento** | `190375\|0\|19\|36171\|226546\|...\|}` | (no existe) | Normal |
| **Con descuento** | `180375\|0\|19\|34271\|214646\|...\|}` | `D\|DESCUENTO GLOBAL\|10000\|\|}` | Con info |

---

## ✅ IMPLEMENTADO

Ya agregué el código para que:

1. **Línea 5:** Contiene totales CON descuento aplicado
2. **Línea D|...:** Muestra cuánto fue el descuento (informativo)
3. Aparece SOLO si hay descuento > 0

---

## 🚀 PROBAR

1. **Limpiar caché del navegador** (Ctrl + Shift + Delete)
2. **Cerrar y abrir navegador**
3. Ir a interfaz (Ctrl + Shift + R)
4. Cargar Ejemplo (descuento de $10,000)
5. Generar TXT

---

## 📄 RESULTADO ESPERADO

```
180375|0|19|34271|214646|||||||||||||}  ← Totales
D|DESCUENTO GLOBAL|10000||}              ← Línea informativa del descuento
~
|Item PRODUCTO EJEMPLO A|...|
```

---

**¡Listo! Ahora el descuento aparece como línea informativa.** ✅
