# 📅 FECHA DE VENCIMIENTO EN TXT ACEPTA

## ✅ TU GENERADOR FUNCIONA CORRECTAMENTE

La fecha de vencimiento está **vacía** porque tu documento es **CONTADO**.

---

## 📋 FORMAS DE PAGO Y FECHA DE VENCIMIENTO

| Forma Pago | Código | Fecha Vencimiento | Ejemplo |
|------------|--------|-------------------|---------|
| **Contado** | 1 | ❌ Vacía (opcional) | `33\|12345\|2025-11-08\|\|2\|1\|1\|\|}` |
| **Crédito** | 2 | ✅ Obligatoria | `33\|12345\|2025-11-08\|\|2\|1\|2\|2025-12-08\|}` |
| **Sin Costo** | 3 | ❌ Vacía | `33\|12345\|2025-11-08\|\|2\|1\|3\|\|}` |

---

## 🔍 ESTRUCTURA LÍNEA 1 (IdDoc)

```
33|12345|2025-11-08||2|1|1|2025-12-08|}
│  │     │           │ │ │  │
│  │     │           │ │ │  └─ Fecha Vencimiento (solo si crédito)
│  │     │           │ │ └──── Forma Pago (1=Contado, 2=Crédito, 3=Sin costo)
│  │     │           │ └────── Indicador Traslado
│  │     │           └──────── Tipo Despacho
│  │     └──────────────────── Fecha Emisión
│  └────────────────────────── Folio
└───────────────────────────── Tipo Documento
```

---

## 💡 EJEMPLOS

### Ejemplo 1: Factura CONTADO (tu caso actual)
```
33|12345|2025-11-08||2|1|1||}
                          ^^
                          Vacío = OK
```

### Ejemplo 2: Factura CRÉDITO 30 días
```
33|12345|2025-11-08||2|1|2|2025-12-08|}
                          │ │
                          │ └─ Fecha: Hoy + 30 días
                          └─── Forma pago: 2 (Crédito)
```

### Ejemplo 3: Boleta CONTADO
```
39|5678|2025-11-08||2|1|1||}
                         ^^
                         Vacío = OK
```

---

## 🧪 PROBAR CON CRÉDITO (Opcional)

Si quieres generar con fecha de vencimiento:

### En la interfaz:

1. Ve a: `http://localhost:8000/app/configuracion/interfaz-prueba-acepta/`
2. Clic en "Cargar Ejemplo"
3. **Cambia "Forma de Pago"** de "Contado" a "Crédito"
4. El campo "Fecha Vencimiento" aparecerá activo
5. Ingresa una fecha futura (ej: 30 días después)
6. Genera el TXT

### Resultado esperado:
```
33|12345|2025-11-08||2|1|2|2025-12-08|}
                          │ │
                          │ └─ Fecha vencimiento incluida
                          └─── 2 = Crédito
```

---

## ✅ RESUMEN

Tu archivo actual:
```
33|12345|2025-11-08||2|1|1||}
```

Es **CORRECTO** porque:
- ✅ Forma de pago = 1 (Contado)
- ✅ Fecha vencimiento vacía (esperado para contado)
- ✅ Formato cumple con especificación Acepta

---

## 📚 REFERENCIA RÁPIDA

**Campo 7 en Línea 1 (IdDoc):**
- **Posición:** Séptimo campo
- **Nombre:** Fecha de Vencimiento
- **Formato:** YYYY-MM-DD o vacío
- **Obligatorio:** Solo si forma_pago = 2 (Crédito)
- **Opcional:** Si forma_pago = 1 (Contado) o 3 (Sin costo)

---

**Estado:** ✅ Tu generador funciona perfectamente  
**Fecha Vencimiento:** Opcional, implementada correctamente  
**Próximo paso:** ¡Usar en producción! 🚀

