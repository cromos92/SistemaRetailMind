# ✅ FECHA DE VENCIMIENTO IMPLEMENTADA

## 🎯 PROBLEMA RESUELTO

**Antes:**
```
33|12345|2025-11-10||2|1|2||}  ← Sin fecha vencimiento
                          ^^
```

**Ahora:**
```
33|12345|2025-11-10||2|1|2|2025-12-10|}  ← Con fecha vencimiento
                          │
                          └─ 30 días después
```

---

## ✨ FUNCIONALIDAD AGREGADA

### Campo Fecha de Vencimiento

**Comportamiento:**
- Se **muestra automáticamente** cuando seleccionas "Crédito"
- Se **oculta** cuando seleccionas "Contado" o "Sin Costo"
- **Sugiere automáticamente** 30 días después de la fecha de emisión

---

## 🎯 CÓMO FUNCIONA

### 1. Forma de Pago = CONTADO (1)
```
Campo "Fecha Vencimiento": OCULTO
TXT generado: 33|12345|2025-11-10||2|1|1||}
                                      │  ^^
                                      │  Sin fecha
                                      └─ Contado
```

### 2. Forma de Pago = CRÉDITO (2)
```
Campo "Fecha Vencimiento": VISIBLE (sugiere +30 días)
TXT generado: 33|12345|2025-11-10||2|1|2|2025-12-10|}
                                      │  │
                                      │  └─ Fecha vencimiento
                                      └─ Crédito
```

### 3. Forma de Pago = SIN COSTO (3)
```
Campo "Fecha Vencimiento": OCULTO
TXT generado: 33|12345|2025-11-10||2|1|3||}
                                      │  ^^
                                      │  Sin fecha
                                      └─ Sin costo
```

---

## 🧪 PROBAR AHORA

### Paso 1: Reiniciar y Acceder
```bash
cd C:\DjangoProyects\retailmind\SistemaRetailMind\retailmind
python manage.py runserver
```

```
http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
```

### Paso 2: Cargar Ejemplo
1. Clic en "Cargar Ejemplo"
2. **VERÁS:**
   - Forma de Pago: **CRÉDITO** (cambiado)
   - Campo "Fecha Vencimiento": **VISIBLE**
   - Fecha sugerida: **30 días después**

### Paso 3: Cambiar Forma de Pago
1. Cambia a "Contado"
2. El campo de fecha vencimiento **desaparece**
3. Cambia a "Crédito"
4. El campo **reaparece** con fecha sugerida

### Paso 4: Generar TXT
1. Con forma de pago en "Crédito"
2. Verifica que la fecha esté llena
3. Clic en "Generar Archivo TXT"
4. Abre el archivo

### Paso 5: Verificar el TXT
```
33|12345|2025-11-10||2|1|2|2025-12-10|}
                          │  │
                          │  └─ ✅ FECHA VENCIMIENTO PRESENTE
                          └─ 2 = Crédito
```

---

## 📋 CAMPOS DE LA LÍNEA 1 (IdDoc)

```
33|12345|2025-11-10||2|1|2|2025-12-10|}
│  │     │           │ │ │  │
│  │     │           │ │ │  └─ 8. Fecha Vencimiento ✅
│  │     │           │ │ └──── 7. Forma Pago (2=Crédito)
│  │     │           │ └────── 6. Ind. Traslado
│  │     │           └──────── 5. Tipo Despacho
│  │     └──────────────────── 3. Fecha Emisión
│  └────────────────────────── 2. Folio
└───────────────────────────── 1. Tipo Documento
```

---

## 💡 EJEMPLOS

### Ejemplo 1: Factura Contado
```
Forma de Pago: Contado
Fecha Vencimiento: (campo oculto)

TXT:
33|12345|2025-11-10||2|1|1||}
                          ^^
```

### Ejemplo 2: Factura Crédito 30 días
```
Forma de Pago: Crédito
Fecha Emisión: 2025-11-10
Fecha Vencimiento: 2025-12-10 (30 días después)

TXT:
33|12345|2025-11-10||2|1|2|2025-12-10|}
                          │
                          └─ 30 días después
```

### Ejemplo 3: Factura Crédito 60 días
```
Forma de Pago: Crédito
Fecha Emisión: 2025-11-10
Fecha Vencimiento: 2026-01-09 (60 días después) - Editar manualmente

TXT:
33|12345|2025-11-10||2|1|2|2026-01-09|}
                          │
                          └─ 60 días después
```

### Ejemplo 4: Boleta Sin Costo
```
Forma de Pago: Sin Costo
Fecha Vencimiento: (campo oculto)

TXT:
39|5678|2025-11-10||2|1|3||}
                         ^^
```

---

## 🎨 INTERFAZ VISUAL

### Cuando seleccionas "Contado":
```
┌─────────────────┬─────────────────┬─────────────────┐
│ Folio           │ Fecha Emisión   │ Forma de Pago   │
│ 12345           │ 2025-11-10      │ [Contado ▼]     │
└─────────────────┴─────────────────┴─────────────────┘
```

### Cuando seleccionas "Crédito":
```
┌─────────────┬──────────────┬──────────────┬─────────────────────┐
│ Folio       │ Fecha Emisión│ Forma Pago   │ Fecha Vencimiento   │
│ 12345       │ 2025-11-10   │ [Crédito ▼] │ 2025-12-10          │
│             │              │              │ Solo para Crédito   │
└─────────────┴──────────────┴──────────────┴─────────────────────┘
```

---

## ⚙️ CONFIGURACIÓN AUTOMÁTICA

### Fecha Sugerida
Por defecto, al seleccionar "Crédito", se sugiere **30 días** después de la fecha de emisión.

**Puedes cambiar esto editando:**
```javascript
// En interfaz_prueba_acepta.html
function toggleFechaVencimiento() {
    ...
    fecha.setDate(fecha.getDate() + 30);  // ← Cambiar 30 por otro valor
    ...
}
```

**Ejemplos:**
- `+ 15` = 15 días (crédito corto)
- `+ 30` = 30 días (crédito normal)
- `+ 60` = 60 días (crédito largo)
- `+ 90` = 90 días (crédito extendido)

---

## ✅ VALIDACIONES

### Al generar TXT:
- ✅ Si forma_pago = 2 (Crédito) → Fecha vencimiento se incluye
- ✅ Si forma_pago = 1 (Contado) → Fecha vencimiento vacía
- ✅ Si forma_pago = 3 (Sin Costo) → Fecha vencimiento vacía

### Validación SII:
- ✅ Fecha vencimiento debe ser >= fecha emisión
- ✅ Formato: YYYY-MM-DD
- ✅ Obligatoria solo para crédito

---

## 📊 RESUMEN DE CAMBIOS

### Archivos modificados:
1. ✅ `interfaz_prueba_acepta.html`
   - Campo fecha vencimiento agregado
   - Función `toggleFechaVencimiento()`
   - Ejemplo actualizado a Crédito

2. ✅ `generador_txt_acepta.js`
   - Parámetro `fechaVencimiento` agregado
   - Procesamiento en `crearFacturaElectronica()`

3. ✅ `views_modulo_documentos.py`
   - Ya procesaba correctamente la fecha en línea 1

### Funcionalidades:
- ✅ Campo se muestra/oculta automáticamente
- ✅ Fecha sugerida automática (+30 días)
- ✅ Se envía al backend correctamente
- ✅ Se incluye en el TXT generado
- ✅ Formato correcto Acepta

---

## 🎓 TIPS

### Tip 1: Créditos cortos (15 días)
```
1. Selecciona "Crédito"
2. Edita manualmente la fecha a 15 días
3. Genera el TXT
```

### Tip 2: Créditos largos (90 días)
```
1. Selecciona "Crédito"
2. Edita manualmente la fecha a 90 días
3. Genera el TXT
```

### Tip 3: Verificar fecha
```
Fecha Emisión: 2025-11-10
Fecha Vencimiento: 2025-12-10
Diferencia: 30 días ✅
```

---

## 🚀 ESTADO ACTUAL

### Implementado:
- ✅ Campo fecha vencimiento
- ✅ Mostrar/ocultar automático
- ✅ Fecha sugerida (+30 días)
- ✅ Integración con backend
- ✅ Formato TXT correcto
- ✅ Debugging activo
- ✅ Ejemplo con crédito

### Listo para:
- ✅ Generar facturas a crédito
- ✅ Especificar días de crédito
- ✅ Cumplir normativa SII
- ✅ Producción

---

**Fecha:** Noviembre 10, 2025  
**Versión:** 4.0 - Fecha Vencimiento  
**Estado:** ✅ IMPLEMENTADO Y FUNCIONANDO

