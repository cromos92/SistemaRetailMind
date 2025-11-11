# ✅ FUNCIONALIDAD DTE EN POS - IMPLEMENTADA

## 🎯 ¿QUÉ SE IMPLEMENTÓ?

Sistema completo para generar **Boletas y Facturas Electrónicas** desde el módulo de ventas/POS.

---

## 📋 COMPONENTES IMPLEMENTADOS

### 1. **Campos en Modelo Empresa** ✅
```python
acteco = CharField(20)      # Código actividad económica SII
contacto1 = CharField(100)  # Teléfono/email principal
contacto2 = CharField(100)  # Teléfono/email secundario
```

### 2. **Campos en Modelo Ticket** ✅
```python
# Tipo de documento
tipo_dte = 'TICKET' | 'BOLETA_ELECTRONICA' | 'FACTURA_ELECTRONICA'
folio_dte = IntegerField  # Folio asignado

# Referencia a documentos comerciales (OC, Guías)
referencia_tipo = '801' | '52' | '803'
referencia_folio = CharField
referencia_fecha = DateField

# Estado del DTE
dte_generado = Boolean
dte_fecha_generacion = DateTime
dte_xml_path = CharField  # Ruta del XML de Acepta
dte_pdf_url = CharField   # URL del PDF de Acepta
```

### 3. **Función generar_dte_desde_ticket()** ✅
Convierte un Ticket a formato TXT de Acepta

**Funcionalidades:**
- ✅ Lee datos del ticket
- ✅ Obtiene datos de la empresa (con acteco)
- ✅ Asigna folio automáticamente
- ✅ Convierte productos del ticket
- ✅ Calcula totales (con descuento global)
- ✅ Incluye referencias a OC/Guías
- ✅ Actualiza ticket (dte_generado, folio_dte)
- ✅ Incrementa correlativo

### 4. **API Endpoint** ✅
```
POST /app/documentos/generar-dte-ticket/
```

**Request:**
```json
{
    "ticket_id": 123,
    "tipo_dte": "BOLETA_ELECTRONICA"
}
```

**Response:** Archivo TXT descargable

---

## 🔢 PARA QUÉ SIRVEN LOS CAMPOS EN BD

### ✅ **NO se guarda el TXT** (se descarga directo)

### ✅ **SÍ se guarda:**

#### 1. Trazabilidad
```python
# Saber QUÉ se emitió para cada venta
ticket.tipo_dte = 'BOLETA_ELECTRONICA'
ticket.folio_dte = 4578
```

#### 2. Evitar duplicados
```python
if ticket.dte_generado:
    return "Ya tiene DTE: Folio {ticket.folio_dte}"
```

#### 3. Referencias comerciales
```python
# Cliente dio OC → la guardamos
ticket.referencia_tipo = '801'
ticket.referencia_folio = 'OC-98765'
```

#### 4. Integración con Acepta
```python
# Guardar rutas después de subir a Acepta
ticket.dte_xml_path = 'C:\\Acepta\\...\\39-4578.xml'
ticket.dte_pdf_url = 'http://acepta.com/...'
```

#### 5. Reportes y consultas
```python
# Facturas del mes
Ticket.objects.filter(tipo_dte='FACTURA_ELECTRONICA', fecha__month=11)

# Sin DTE generado
Ticket.objects.filter(dte_generado=False)

# Con referencia a OC
Ticket.objects.filter(referencia_tipo='801')
```

---

## 🚀 CÓMO USAR (Desde código)

### Ejemplo 1: Generar Boleta desde Ticket

```python
from app.views_modulo_documentos import generar_dte_desde_ticket

# Generar boleta electrónica
contenido_txt, nombre_archivo = generar_dte_desde_ticket(
    ticket_id=123,
    tipo_dte='BOLETA_ELECTRONICA'
)

# El archivo TXT está en contenido_txt
# El ticket ya tiene folio_dte asignado
# El correlativo ya se incrementó
```

### Ejemplo 2: Generar Factura con OC

```python
# Primero actualizar ticket con la referencia
ticket = Ticket.objects.get(id=123)
ticket.referencia_tipo = '801'  # Orden de Compra
ticket.referencia_folio = 'OC-98765'
ticket.referencia_fecha = date(2025, 11, 5)
ticket.save()

# Generar factura
contenido_txt, nombre_archivo = generar_dte_desde_ticket(
    ticket_id=123,
    tipo_dte='FACTURA_ELECTRONICA'
)

# El TXT incluirá la referencia a la OC
```

---

## 📊 FLUJO COMPLETO

```
1. Usuario crea venta en POS
   └─ Ticket creado (tipo_dte='TICKET')

2. Usuario decide facturar
   └─ Selecciona tipo: BOLETA_ELECTRONICA o FACTURA_ELECTRONICA

3. (Opcional) Agrega referencia a OC
   └─ ticket.referencia_tipo = '801'
   └─ ticket.referencia_folio = 'OC-98765'

4. Genera DTE
   └─ Se llama a generar_dte_desde_ticket()
   └─ Se asigna folio automáticamente
   └─ Se genera el TXT
   └─ Se actualiza ticket:
       - dte_generado = True
       - folio_dte = 4578
       - dte_fecha_generacion = now()

5. Se descarga TXT

6. Usuario sube a Acepta
   └─ Acepta genera XML y PDF

7. (Opcional) Guardar rutas
   └─ ticket.dte_xml_path = ruta_xml
   └─ ticket.dte_pdf_url = url_pdf
```

---

## 🎯 PRÓXIMOS PASOS

### Ya implementado:
- [x] Modelo Empresa con acteco, contacto1, contacto2
- [x] Modelo Ticket con campos DTE
- [x] Función generar_dte_desde_ticket()
- [x] API endpoint /generar-dte-ticket/
- [x] Migraciones aplicadas

### Por implementar:
- [ ] Botón "Generar DTE" en interfaz POS
- [ ] Selector de tipo de DTE
- [ ] Campos de referencia opcional
- [ ] JavaScript para llamar al API

---

## 📝 EJEMPLO DE USO DESDE JAVASCRIPT

```javascript
// En el POS, después de guardar la venta
async function generarDTE(ticketId, tipoDTE) {
    const response = await fetch('/app/documentos/generar-dte-ticket/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({
            ticket_id: ticketId,
            tipo_dte: tipoDTE  // 'BOLETA_ELECTRONICA' o 'FACTURA_ELECTRONICA'
        })
    });
    
    if (response.ok) {
        // Descargar archivo
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = response.headers.get('Content-Disposition').split('filename=')[1].replace(/"/g, '');
        a.click();
        
        alert('DTE generado correctamente');
    } else {
        const error = await response.json();
        alert('Error: ' + error.error);
    }
}
```

---

**Función y endpoint implementados. ¿Quieres que agregue la interfaz en el POS ahora?** 🚀

