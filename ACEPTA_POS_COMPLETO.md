# ✅ ACEPTA INTEGRADO EN POS - IMPLEMENTACIÓN COMPLETA

## 🎯 ¿QUÉ SE IMPLEMENTÓ?

Sistema completo de facturación electrónica con Acepta integrado en el módulo de ventas/POS.

---

## 📋 COMPONENTES IMPLEMENTADOS

### 1. **Modelo Empresa** ✅
```python
acteco = CharField(20)      # Código actividad económica (opcional)
contacto1 = CharField(100)  # Teléfono/email principal (opcional)
contacto2 = CharField(100)  # Teléfono/email secundario (opcional)
```

### 2. **Modelo Ticket** ✅
```python
# Tipo de documento
tipo_dte = 'TICKET' | 'BOLETA' | 'BOLETA_ELECTRONICA' | 'FACTURA_ELECTRONICA'
folio_dte = IntegerField

# Referencias a documentos comerciales (SOLO para facturas)
referencia_tipo = '801' | '52' | '803' | 'HES'
referencia_folio = CharField
referencia_fecha = DateField

# Estado DTE
dte_generado = Boolean
dte_fecha_generacion = DateTime
dte_xml_path = CharField
dte_pdf_url = CharField
```

### 3. **Función generar_dte_desde_ticket()** ✅
Convierte un Ticket a formato TXT de Acepta

### 4. **API /documentos/generar-dte-ticket/** ✅
Endpoint para generar DTEs desde tickets

### 5. **Interfaz en POS** ✅
- Selector de tipo de documento (ya existía)
- **Sección de referencias** (NUEVO - solo para facturas)
- Guardado automático de referencias

---

## 🎨 INTERFAZ EN EL POS

### Selector de Tipo de Documento (Ya existía):
```
[ ] Boleta Electrónica (39)  ← Consumidor final
[ ] Factura Electrónica (33) ← Empresas con RUT
[ ] Boleta Papel            ← No genera TXT
```

### Sección de Referencias (NUEVO - solo para Facturas):
```
┌─────────────────────────────────────────────────┐
│ 🔗 Referencia a Documento Comercial (Opcional) │
├─────────────────────────────────────────────────┤
│ Tipo:  [801 - Orden de Compra      ▼]         │
│ Folio: [OC-98765                    ]          │
│ Fecha: [2025-11-05                  ]          │
│                                                 │
│ ℹ Si el cliente entregó una OC, Guía o        │
│   Contrato, puede referenciarla aquí.          │
└─────────────────────────────────────────────────┘
```

**Esta sección:**
- ✅ Se muestra SOLO cuando seleccionas "Factura Electrónica"
- ✅ Se oculta para Boletas
- ✅ Es opcional

---

## 🔧 CÓMO FUNCIONA

### Caso 1: Venta Simple (Boleta)

```
1. Usuario selecciona "Boleta Electrónica"
2. Sección de referencias → OCULTA
3. Completa datos del cliente
4. Finaliza venta
5. Ticket guardado con tipo_dte='BOLETA_ELECTRONICA'
```

**Para generar el DTE:**
```python
contenido_txt, archivo = generar_dte_desde_ticket(
    ticket_id=123,
    tipo_dte='BOLETA_ELECTRONICA'
)
```

**TXT generado:**
```
39|4578|2025-11-10|3|||2025-11-10||}
78503140-7|EMPRESA DEMO LTDA|...|SANTIAGO|}
66666666-6|||||||}
|178500|||||}
~
INT1|Item||PRODUCTO A||10|UN|15000|150000|}
~
USUARIO|||^ Vendedor: USUARIO...||||boleta|4|}
~
\
```

### Caso 2: Factura con Orden de Compra

```
1. Usuario selecciona "Factura Electrónica"
2. Sección de referencias → VISIBLE
3. Completa datos del cliente (empresa)
4. Completa referencia:
   - Tipo: 801 - Orden de Compra
   - Folio: OC-98765
   - Fecha: 2025-11-05
5. Finaliza venta
6. Ticket guardado con:
   - tipo_dte='FACTURA_ELECTRONICA'
   - referencia_tipo='801'
   - referencia_folio='OC-98765'
   - referencia_fecha='2025-11-05'
```

**Para generar el DTE:**
```python
contenido_txt, archivo = generar_dte_desde_ticket(
    ticket_id=123,
    tipo_dte='FACTURA_ELECTRONICA'
)
```

**TXT generado (con referencia):**
```
33|4578|2025-11-10||2|1|1|2025-11-10|}
78503140-7|EMPRESA DEMO LTDA|COMERCIO AL POR MENOR|521000|||...|USUARIO|}
18312585-0||CLIENTE S.A.|...||}
|||||}
180375|0|19|34271|214646|||||||||||||}
~
|Item PRODUCTO A||10|UN|15000|||150000|Item|}
~
801|| OC-98765 | 2025-11-05|| |}  ← REFERENCIA A OC
~
USUARIO|||CIENTO OCHENTA MIL PESOS  |||||||HP LaserJet|4|}
~
\
```

---

## 📊 TIPOS DE REFERENCIAS SOPORTADAS

| Tipo | Código | Uso |
|------|--------|-----|
| **Orden de Compra** | 801 | Cliente dio OC |
| **Guía de Despacho** | 52 | Productos ya despachados |
| **Contrato** | 803 | Venta por contrato |
| **HES** | HES | Hoja Entrada Servicio |

**IMPORTANTE:** Estas referencias son para **documentos comerciales**, NO para anular.

---

## 💻 USAR LA FUNCIONALIDAD

### Desde la interfaz web (POS):

1. Ir a: `http://localhost:8000/app/pos-dashboard/`
2. Crear una venta
3. En "Tipo de Documento" seleccionar:
   - **Boleta Electrónica** (para consumidor final)
   - **Factura Electrónica** (para empresa)
4. Si es Factura:
   - Aparece sección de referencias
   - (Opcional) Agregar OC, Guía, etc.
5. Finalizar venta
6. **Generar DTE** (siguiente paso a implementar: botón)

### Desde código Python:

```python
from app.views_modulo_documentos import generar_dte_desde_ticket

# Generar DTE desde un ticket
contenido_txt, nombre_archivo = generar_dte_desde_ticket(
    ticket_id=123,
    tipo_dte='FACTURA_ELECTRONICA'  # o 'BOLETA_ELECTRONICA'
)

# contenido_txt contiene el TXT listo para Acepta
# El ticket ya tiene folio_dte asignado
# El correlativo ya se incrementó
```

---

## 🚀 PRÓXIMOS PASOS

### Ya implementado:
- [x] Campos en modelos (Empresa y Ticket)
- [x] Migraciones aplicadas
- [x] Función generar_dte_desde_ticket()
- [x] API endpoint
- [x] Sección de referencias en interfaz
- [x] Mostrar/ocultar referencias según tipo
- [x] Guardado de referencias en BD

### Por implementar (opcional):
- [ ] Botón "Generar DTE" en la interfaz
- [ ] Modal de confirmación al generar
- [ ] Indicador visual de "DTE Generado"
- [ ] Botón para re-descargar TXT

---

## ✅ RESUMEN

**El sistema está LISTO para usarse:**

1. ✅ Seleccionar tipo de documento (ya funciona)
2. ✅ Agregar referencia a OC (ya funciona - solo facturas)
3. ✅ Guardar venta con referencias (ya funciona)
4. ✅ Generar TXT desde el ticket (función lista)
5. ⏳ Interfaz visual para generar (opcional - puede hacerse por código)

**Puedes generar DTEs desde Python ahora mismo.**

---

**¿Quieres que agregue también el botón visual para generar DTEs desde la interfaz del POS?** 🎨

