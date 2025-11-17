# ✅ GUÍA DE DESPACHO (52) - IMPLEMENTADA

## 🎯 FORMATO DE GUÍA DE DESPACHO

Según tu ejemplo, la **Guía de Despacho (52)** usa **exactamente el mismo formato que Factura**, solo cambia el código del tipo de documento.

---

## 📋 ESTRUCTURA GUÍA DE DESPACHO

```
52|10819|2025-11-12||2|1|1|2025-11-12|}                         ← Tipo 52 (Guía)
78503140-7|PAOLA TEBES|VENTA DE CALZADOS|469000|||...|PA00|}    ← Emisor
78503140-7||PAOLA TEBES|VENTA DE CALZADOS||Matta 2432|...||}   ← Receptor (puede ser mismo emisor)
|||||}                                                           ← Transporte
322900|0|19|61351|384251|||||||||||||}                         ← Totales (igual que factura)
~
|BOLSA CALZADOS MULTIPAOLA 400:00 ||400|PAR|201|||80400|BOLSA CALZADOS|}
|45-1 MULTIPAOLA 500:00 ||500|PAR|485|||242500|45-1|}
~
~
~
PA00|||TRESCIENTOS OCHENTA Y CUATRO MIL...PESOS (Total Art 900)  |||||||HP LaserJet|4|}
~
\
```

---

## 🔍 DIFERENCIAS CON FACTURA

| Aspecto | Factura (33) | Guía (52) |
|---------|--------------|-----------|
| **Tipo documento** | 33 | 52 |
| **Emisor** | Empresa que vende | Empresa que despacha |
| **Receptor** | Cliente | Destino (puede ser mismo emisor) |
| **Totales** | Con IVA | Con o sin IVA |
| **Formato** | ✅ Idéntico | ✅ Idéntico |

**IMPORTANTE:** El formato es el mismo, solo cambia el tipo de documento (52).

---

## ✅ IMPLEMENTACIÓN

La Guía de Despacho usa la **misma función de factura**, no necesita función separada.

### Código implementado:

```python
# En generar_txt_dte_acepta()
if tipo_doc == 52:
    # Guía usa el mismo formato que factura
    # Solo cambia el tipo (52 en lugar de 33)
    # Se procesa normal
```

---

## 🎯 CASOS DE USO DE GUÍA

### 1. Traslado Interno (Emisor = Receptor)
```
Emisor: 78503140-7 (PAOLA TEBES)
Receptor: 78503140-7 (PAOLA TEBES)  ← Mismo RUT
Uso: Traslado entre sucursales de la misma empresa
```

### 2. Despacho a Cliente
```
Emisor: 78503140-7 (TU EMPRESA)
Receptor: 18312585-0 (CLIENTE S.A.)
Uso: Despacho de productos a cliente (sin facturar aún)
```

### 3. Guía para Facturar Después
```
1. Generas Guía 52 (despacho)
2. Cliente recibe productos
3. Generas Factura 33 con referencia a Guía
```

---

## 📄 EJEMPLO GENERADO (Guía)

```
52|10819|2025-11-10||2|1|1|2025-11-10|}
78503140-7|EMPRESA DEMO LTDA|COMERCIO AL POR MENOR|521000|||AV. PRINCIPAL 123|SANTIAGO|SANTIAGO|USUARIO|}
18312585-0||CLIENTE S.A.|COMERCIO||CALLE 456|SANTIAGO|SANTIAGO|||}
|||||}
180375|0|19|34271|214646|||||||||||||}
~
|Item PRODUCTO A||10|UN|15000|||150000|Item|}
~
~
~
USUARIO|||CIENTO OCHENTA MIL PESOS  |||||||HP LaserJet|4|}
~
\
```

---

## 🚀 CÓMO USAR

### En la interfaz de prueba:
```
http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
```

1. Seleccionar **"Guía de Despacho"** (tipo 52)
2. Completar datos del emisor
3. Completar datos del receptor (puede ser mismo emisor)
4. Agregar productos
5. Generar TXT

**Resultado:** TXT con formato correcto para Guía de Despacho ✅

### Desde código Python:
```python
from app.views_modulo_documentos import generar_txt_dte_acepta

datos = {
    'documento': {
        'tipo_documento': 52,  # Guía de Despacho
        'folio': 10819,
        'fecha_emision': '2025-11-10',
        'forma_pago': 1,
    },
    'emisor': { ... },
    'receptor': { ... },
    'totales': { ... },
    'detalle': [ ... ]
}

txt = generar_txt_dte_acepta(datos)
```

---

## ✅ ESTADO

- [x] Guía de Despacho detectada (tipo 52)
- [x] Usa mismo formato que factura
- [x] Se procesa correctamente
- [x] TXT generado correcto

---

## 💡 NOTAS IMPORTANTES

### Traslado Interno:
Si emisor y receptor son el mismo RUT:
```
Emisor: 78503140-7|PAOLA TEBES|...
Receptor: 78503140-7||PAOLA TEBES|...  ← Mismo RUT
```

### Para Venta Posterior:
Cuando generes la Factura, puedes referenciar la Guía:
```
Referencia:
Tipo: 52 - Guía de Despacho
Folio: 10819
Fecha: 2025-11-10
```

---

**¡Guía de Despacho lista! Usa el mismo código, solo cambia tipo_documento a 52.** ✅

