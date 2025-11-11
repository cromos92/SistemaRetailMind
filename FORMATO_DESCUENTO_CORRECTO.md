# ✅ FORMATO DESCUENTO GLOBAL CORREGIDO

## 🎯 FORMATO REAL DE ACEPTA

Según el ejemplo proporcionado, el descuento global tiene **6 campos**:

```
D|Descuento|$|10000|1||}
│ │         │ │     │ │
│ │         │ │     │ └─ Campo 6: Cierre ||}
│ │         │ │     └─── Campo 5: Tipo descuento (1)
│ │         │ └───────── Campo 4: Valor (10000)
│ │         └─────────── Campo 3: Tipo valor ($ = pesos, % = porcentaje)
│ └───────────────────── Campo 2: Glosa (descripción)
└─────────────────────── Campo 1: Tipo (D = Descuento, R = Recargo)
```

---

## 📋 CAMPOS DEL DESCUENTO

### Campo 1: Tipo
- `D` = Descuento
- `R` = Recargo

### Campo 2: Glosa
- Texto descriptivo
- Ejemplo: "Descuento", "Desc. Cliente VIP", "Promoción"

### Campo 3: Tipo de Valor
- `$` = Monto en pesos
- `%` = Porcentaje

### Campo 4: Valor
- Número del descuento
- Si es `$`: monto en pesos (ej: 10000)
- Si es `%`: porcentaje (ej: 10)

### Campo 5: Tipo de Descuento
- `1` = Descuento sobre subtotal
- `2` = Descuento no afecto
- `3` = Descuento exento

### Campo 6: Cierre
- `||}` = Cierre estándar

---

## 📊 EJEMPLOS

### Descuento de $10,000:
```
D|Descuento|$|10000|1||}
```

### Descuento del 10%:
```
D|Descuento|%|10|1||}
```

### Descuento especial cliente:
```
D|Desc. Cliente VIP|$|25000|1||}
```

### Recargo por envío:
```
R|Recargo Envio|$|5000|1||}
```

---

## 🔧 CORRECCIÓN APLICADA

### Antes (incorrecto):
```python
linea_descuento = f"D|DESCUENTO GLOBAL|{descuento_global}||}}"
```

**Resultado:**
```
D|DESCUENTO GLOBAL|10000||}  ← Solo 4 campos
```

### Ahora (correcto):
```python
linea_descuento = f"D|Descuento|$|{descuento_global}|1||}}"
```

**Resultado:**
```
D|Descuento|$|10000|1||}  ← 6 campos como en ejemplo
```

---

## 📄 TXT COMPLETO GENERADO

```
33|4578|2025-11-10||2|1|1|2025-11-10|}
78503140-7|EMPRESA DEMO LTDA|COMERCIO AL POR MENOR|521000|||AV. PRINCIPAL 123|SANTIAGO|SANTIAGO|USUARIO|}
18312585-0||CLIENTE EJEMPLO S.A.|COMERCIO||CALLE COMERCIO 456|PROVIDENCIA|SANTIAGO|||}
|||||}
180375|0|19|34271|214646|||||||||||||}
D|Descuento|$|10000|1||}                ← FORMATO CORRECTO
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

## ✅ CÁLCULO DE TOTALES

### Productos:
- Producto A: 10 x $15,000 = $150,000
- Producto B: 5 x $8,500 = $42,500
  - Descuento 5%: -$2,125
  - Neto: $40,375
- **Subtotal:** $190,375

### Descuento Global:
- **-$10,000** (se muestra en línea `D|...`)

### Totales en Línea 5:
- **Neto:** $180,375 (190375 - 10000)
- **IVA (19%):** $34,271
- **Total:** $214,646

---

## 🚀 PROBAR

1. Reinicia el servidor
2. Limpia caché navegador (Ctrl + Shift + Delete)
3. Recarga página (Ctrl + Shift + R)
4. Cargar Ejemplo
5. Generar TXT

---

## 👀 EN LA TERMINAL DEBERÍAS VER:

```python
✅ Agregando línea informativa de descuento: 10000
🔍 DEBUG - Línea descuento generada: D|Descuento|$|10000|1||}
```

---

**Ahora el formato es exacto al ejemplo que me mostraste.** ✅
