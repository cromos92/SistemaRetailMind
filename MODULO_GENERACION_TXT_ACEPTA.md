# MÓDULO DE GENERACIÓN DE ARCHIVOS TXT PARA ACEPTA

## 📋 Descripción General

Este módulo permite generar archivos TXT con el formato requerido por el sistema **Acepta** para la emisión de Documentos Tributarios Electrónicos (DTE) en Chile.

El módulo está compuesto por:
- **Backend (Python/Django)**: Funciones de generación y validación en `views_modulo_documentos.py`
- **Frontend (JavaScript)**: Utilidades para facilitar el envío de datos en `generador_txt_acepta.js`
- **URLs**: Endpoints REST para la generación de archivos

---

## 🎯 Características

✅ Generación de archivos TXT para todos los tipos de DTE:
- Factura Electrónica (33)
- Factura Exenta (34)
- Boleta Electrónica (39)
- Boleta Exenta (41)
- Guía de Despacho (52)
- Nota de Débito (56)
- Nota de Crédito (61)

✅ Validación completa de datos obligatorios
✅ Formato automático de RUTs, fechas y montos
✅ Generación desde datos JSON o desde DTEs existentes
✅ Descarga automática del archivo generado

---

## 📂 Archivos del Módulo

### Backend
- **Archivo**: `retailmind/app/views_modulo_documentos.py`
- **Funciones principales**:
  - `generar_txt_dte_acepta(datos)` - Función core de generación
  - `generar_txt_acepta_api(request)` - API endpoint para JSON
  - `generar_txt_desde_dte_existente(request)` - Genera desde DTE en BD
  - Funciones auxiliares de formateo y validación

### Frontend
- **Archivo**: `retailmind/app/static/js/generador_txt_acepta.js`
- **Objeto**: `GeneradorTXTAcepta`
- **Métodos principales**:
  - `crearFacturaElectronica(params)`
  - `crearBoletaElectronica(params)`
  - `crearGuiaDespacho(params)`
  - `crearNotaCredito(params)`
  - `generarTXT(datos)`
  - `generarTXTDesdeDTE(dteId)`

### URLs
- `/documentos/generar-txt-acepta/` - Generar desde datos JSON
- `/documentos/generar-txt-desde-dte/` - Generar desde DTE existente

---

## 🚀 Guía de Uso

### Opción 1: Generar desde JavaScript (Recomendado)

#### Ejemplo 1: Factura Electrónica

```javascript
// 1. Preparar los datos
const datosFactura = GeneradorTXTAcepta.crearFacturaElectronica({
    folio: 12345,
    fechaEmision: '2025-11-05',
    formaPago: GeneradorTXTAcepta.FORMAS_PAGO.CREDITO,
    
    emisor: {
        rut: '76123456-7',
        razon_social: 'MI EMPRESA LTDA',
        giro: 'VENTA AL POR MENOR',
        acteco: '521000',
        direccion: 'AV. PRINCIPAL 123',
        comuna: 'SANTIAGO',
        ciudad: 'SANTIAGO',
        telefono: '+56912345678'
    },
    
    receptor: {
        rut: '77654321-K',
        razon_social: 'CLIENTE EJEMPLO S.A.',
        giro: 'COMERCIO',
        direccion: 'CALLE COMERCIO 456',
        comuna: 'PROVIDENCIA',
        ciudad: 'SANTIAGO'
    },
    
    productos: [
        {
            nombre: 'PRODUCTO A',
            descripcion: 'Descripción del producto A',
            cantidad: 10,
            unidad: 'UN',
            precio_unitario: 15000,
            descuento_unitario: 0
        },
        {
            nombre: 'PRODUCTO B',
            descripcion: 'Descripción del producto B',
            cantidad: 5,
            unidad: 'KG',
            precio_unitario: 8500,
            descuento_unitario: 425,  // 5% de descuento
            descuento_pct: 5
        }
    ],
    
    descuentoGlobal: 0
});

// 2. Validar (opcional pero recomendado)
const validacion = GeneradorTXTAcepta.validarDatos(datosFactura);
if (!validacion.valido) {
    console.error('Errores de validación:', validacion.errores);
    alert('Por favor corrija los siguientes errores:\n' + validacion.errores.join('\n'));
    return;
}

// 3. Generar y descargar el archivo TXT
const resultado = await GeneradorTXTAcepta.generarTXT(datosFactura);

if (resultado.success) {
    console.log('Archivo TXT generado exitosamente');
    alert('Archivo descargado correctamente');
} else {
    console.error('Error:', resultado.error);
    alert('Error al generar archivo: ' + resultado.error);
}
```

#### Ejemplo 2: Boleta Electrónica

```javascript
const datosBoleta = GeneradorTXTAcepta.crearBoletaElectronica({
    folio: 5678,
    fechaEmision: new Date(), // Fecha actual
    formaPago: GeneradorTXTAcepta.FORMAS_PAGO.CONTADO,
    
    emisor: {
        rut: '76123456-7',
        razon_social: 'MI NEGOCIO SPA',
        giro: 'VENTA DE PRODUCTOS VARIOS'
    },
    
    receptor: null,  // Se usará consumidor final por defecto (66666666-6)
    
    productos: [
        {
            nombre: 'SERVICIO DE INSTALACION',
            cantidad: 1,
            precio_unitario: 50000
        },
        {
            nombre: 'PRODUCTO X',
            cantidad: 3,
            precio_unitario: 12000
        }
    ]
});

await GeneradorTXTAcepta.generarTXT(datosBoleta);
```

#### Ejemplo 3: Guía de Despacho

```javascript
const datosGuia = GeneradorTXTAcepta.crearGuiaDespacho({
    folio: 789,
    fechaEmision: '2025-11-05',
    indicadorTraslado: GeneradorTXTAcepta.INDICADORES_TRASLADO.OPERACION_VENTA,
    
    emisor: {
        rut: '76123456-7',
        razon_social: 'DISTRIBUIDORA DEMO LTDA',
        giro: 'DISTRIBUCION DE ALIMENTOS',
        direccion: 'BODEGA CENTRAL AV LOGISTICA 500',
        comuna: 'QUILICURA',
        ciudad: 'QUILICURA'
    },
    
    receptor: {
        rut: '77654321-K',
        razon_social: 'SUPERMERCADO ABC S.A.',
        direccion: 'CALLE COMPRAS 200',
        comuna: 'LAS CONDES',
        ciudad: 'LAS CONDES'
    },
    
    transporte: {
        patente: 'ABCD12',
        rut_transportista: '12345678-9',
        direccion_destino: 'CALLE COMPRAS 200',
        comuna_destino: 'LAS CONDES',
        ciudad_destino: 'LAS CONDES'
    },
    
    productos: [
        {
            nombre: 'CAJA PRODUCTO A 12 UNIDADES',
            cantidad: 50,
            unidad: 'CAJA'
        },
        {
            nombre: 'CAJA PRODUCTO B 24 UNIDADES',
            cantidad: 30,
            unidad: 'CAJA'
        }
    ]
});

await GeneradorTXTAcepta.generarTXT(datosGuia);
```

#### Ejemplo 4: Nota de Crédito

```javascript
const datosNC = GeneradorTXTAcepta.crearNotaCredito({
    folio: 234,
    fechaEmision: '2025-11-05',
    
    emisor: {
        rut: '76123456-7',
        razon_social: 'MI EMPRESA LTDA',
        giro: 'VENTA AL POR MENOR'
    },
    
    receptor: {
        rut: '77654321-K',
        razon_social: 'CLIENTE EJEMPLO S.A.'
    },
    
    productos: [
        {
            nombre: 'PRODUCTO A - DEVOLUCION',
            cantidad: 10,  // Se convertirá automáticamente a negativo
            unidad: 'UN',
            precio_unitario: 15000
        }
    ]
});

await GeneradorTXTAcepta.generarTXT(datosNC);
```

#### Ejemplo 5: Generar desde DTE Existente

```javascript
// Si ya tienes un DTE en la base de datos, puedes generar el TXT directamente
const dteId = 123; // ID del DTE

const resultado = await GeneradorTXTAcepta.generarTXTDesdeDTE(dteId);

if (resultado.success) {
    console.log('Archivo TXT generado desde DTE existente');
} else {
    console.error('Error:', resultado.error);
}
```

---

### Opción 2: Generar desde Python

#### Uso Directo de la Función

```python
from decimal import Decimal
from django.utils import timezone
from retailmind.app.views_modulo_documentos import generar_txt_dte_acepta

# Preparar diccionario de datos
datos = {
    'documento': {
        'tipo_documento': 33,  # Factura Electrónica
        'folio': 12345,
        'fecha_emision': '2025-11-05',
        'forma_pago': 1,  # Contado
    },
    'emisor': {
        'rut': '76123456-7',
        'razon_social': 'MI EMPRESA LTDA',
        'giro': 'VENTA AL POR MENOR',
        'acteco': '521000',
        'direccion': 'AV. PRINCIPAL 123',
        'comuna': 'SANTIAGO',
        'ciudad': 'SANTIAGO',
    },
    'receptor': {
        'rut': '77654321-K',
        'razon_social': 'CLIENTE EJEMPLO S.A.',
        'giro': 'COMERCIO',
    },
    'totales': {
        'monto_neto': Decimal('192500'),
        'tasa_iva': Decimal('19.00'),
        'iva': Decimal('36575'),
        'monto_total': Decimal('229075'),
    },
    'detalle': [
        {
            'nombre': 'PRODUCTO A',
            'descripcion': 'Descripción del producto A',
            'cantidad': Decimal('10'),
            'unidad': 'UN',
            'precio_unitario': Decimal('15000'),
            'monto_descuento': Decimal('0'),
            'monto_item': Decimal('150000'),
        }
    ]
}

# Generar TXT
try:
    contenido_txt = generar_txt_dte_acepta(datos)
    
    # Guardar en archivo
    with open('factura_12345.txt', 'w', encoding='utf-8') as f:
        f.write(contenido_txt)
    
    print("Archivo TXT generado exitosamente")
    
except ValidationError as e:
    print(f"Error de validación: {e}")
except Exception as e:
    print(f"Error: {e}")
```

#### Uso desde Vista Django

```python
from django.http import JsonResponse
from retailmind.app.views_modulo_documentos import generar_txt_desde_dte_existente

# En tu vista
def mi_vista(request):
    # ... tu lógica ...
    
    # Llamar a la función de generación
    return generar_txt_desde_dte_existente(request)
```

---

### Opción 3: Llamada API REST

#### Con fetch (JavaScript)

```javascript
// Preparar datos
const datos = {
    documento: {
        tipo_documento: 33,
        folio: 12345,
        fecha_emision: '2025-11-05'
    },
    // ... resto de datos ...
};

// Enviar solicitud
const response = await fetch('/documentos/generar-txt-acepta/', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
    },
    body: JSON.stringify(datos)
});

if (response.ok) {
    // Descargar archivo
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'factura.txt';
    a.click();
} else {
    const error = await response.json();
    console.error('Error:', error);
}
```

#### Con jQuery

```javascript
$.ajax({
    url: '/documentos/generar-txt-acepta/',
    type: 'POST',
    contentType: 'application/json',
    data: JSON.stringify(datos),
    headers: {
        'X-CSRFToken': getCookie('csrftoken')
    },
    xhrFields: {
        responseType: 'blob'
    },
    success: function(blob) {
        // Descargar archivo
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'factura.txt';
        a.click();
    },
    error: function(xhr) {
        console.error('Error:', xhr.responseText);
    }
});
```

#### Con Python requests

```python
import requests
import json

# Preparar datos
datos = {
    'documento': {...},
    'emisor': {...},
    # ...
}

# Enviar solicitud
response = requests.post(
    'http://tu-servidor.com/documentos/generar-txt-acepta/',
    json=datos,
    cookies={'csrftoken': 'tu-csrf-token'}
)

if response.status_code == 200:
    # Guardar archivo
    with open('factura.txt', 'wb') as f:
        f.write(response.content)
    print("Archivo descargado")
else:
    print("Error:", response.json())
```

---

## 📊 Estructura de Datos Completa

### Documento (obligatorio)
```javascript
documento: {
    tipo_documento: int,        // 33, 34, 39, 41, 52, 56, 61 (obligatorio)
    folio: int,                 // Número correlativo (obligatorio)
    fecha_emision: 'YYYY-MM-DD', // (obligatorio)
    ind_no_rebaja: int,         // Solo para NC sin rebaja (opcional)
    tipo_despacho: int,         // 1, 2 o 3 (opcional)
    ind_traslado: int,          // 1-9 para guías (opcional)
    forma_pago: int,            // 1=Contado, 2=Crédito, 3=Sin costo (opcional)
    fecha_vencimiento: 'YYYY-MM-DD', // (opcional)
    ind_servicio: int,          // 1-5 para servicios (opcional)
    timestamp: 'YYYY-MM-DDTHH:MM:SS' // (opcional, se genera auto)
}
```

### Emisor (obligatorio)
```javascript
emisor: {
    rut: string,              // Formato XX.XXX.XXX-X o XXXXXXXX-X (obligatorio)
    razon_social: string,     // Máx 100 caracteres (obligatorio)
    giro: string,             // Máx 80 caracteres (obligatorio)
    acteco: string,           // Código actividad económica (opcional)
    sucursal: string,         // Nombre sucursal (opcional)
    codigo_sucursal: string,  // Código SII (opcional)
    direccion: string,        // Máx 60 caracteres (opcional)
    comuna: string,           // Máx 20 caracteres (opcional)
    ciudad: string,           // Máx 20 caracteres (opcional)
    codigo_vendedor: string,  // Máx 60 caracteres (opcional)
    telefono: string          // Máx 20 caracteres (opcional)
}
```

### Receptor (obligatorio)
```javascript
receptor: {
    rut: string,            // Formato XX.XXX.XXX-X (obligatorio)
    codigo_interno: string, // Código de cliente (opcional)
    razon_social: string,   // Máx 100 caracteres (obligatorio)
    giro: string,           // Máx 40 caracteres (opcional)
    contacto: string,       // Nombre y teléfono (opcional)
    direccion: string,      // Máx 70 caracteres (opcional)
    comuna: string,         // Máx 20 caracteres (opcional)
    ciudad: string          // Máx 20 caracteres (opcional)
}
```

### Transporte (opcional - para guías)
```javascript
transporte: {
    patente: string,          // Máx 8 caracteres
    rut_transportista: string, // Formato XX.XXX.XXX-X
    direccion_destino: string, // Máx 70 caracteres
    comuna_destino: string,    // Máx 20 caracteres
    ciudad_destino: string     // Máx 20 caracteres
}
```

### Totales (obligatorio)
```javascript
totales: {
    monto_neto: Decimal,     // Suma productos afectos (obligatorio)
    monto_exento: Decimal,   // Suma productos exentos (opcional)
    tasa_iva: Decimal,       // Default 19.00 (obligatorio si hay monto_neto)
    iva: Decimal,            // 19% del monto_neto (obligatorio si hay monto_neto)
    monto_total: Decimal,    // neto + exento + iva (obligatorio)
    timestamp: string        // (opcional)
}
```

### Detalle (obligatorio - al menos 1 producto)
```javascript
detalle: [
    {
        indicador_exencion: int,  // 1-6 (opcional, 1=exento)
        nombre: string,           // Máx 80 caracteres (obligatorio)
        descripcion: string,      // Máx 1000 caracteres (opcional)
        cantidad: Decimal,        // (obligatorio)
        unidad: string,           // UN, KG, MT, etc (obligatorio)
        precio_unitario: Decimal, // (obligatorio)
        descuento_pct: Decimal,   // Porcentaje 0-100 (opcional)
        monto_descuento: Decimal, // Monto en pesos (opcional)
        monto_item: Decimal       // Total de la línea (obligatorio)
    }
]
```

---

## ⚠️ Validaciones y Errores Comunes

### Validaciones Automáticas

El módulo valida automáticamente:
- ✅ Presencia de campos obligatorios
- ✅ Formato correcto de RUTs (con guión)
- ✅ Formato de fechas (YYYY-MM-DD)
- ✅ Al menos un producto en el detalle
- ✅ Longitud máxima de campos de texto
- ✅ Caracteres especiales problemáticos

### Errores Comunes

#### Error 1: "Falta el RUT del emisor"
```javascript
// ❌ Incorrecto
emisor: {
    razon_social: 'MI EMPRESA'
}

// ✅ Correcto
emisor: {
    rut: '76123456-7',
    razon_social: 'MI EMPRESA',
    giro: 'COMERCIO'
}
```

#### Error 2: "Debe incluir al menos un producto"
```javascript
// ❌ Incorrecto
productos: []

// ✅ Correcto
productos: [
    {
        nombre: 'PRODUCTO A',
        cantidad: 1,
        precio_unitario: 1000
    }
]
```

#### Error 3: Formato de fecha incorrecto
```javascript
// ❌ Incorrecto
fecha_emision: '05/11/2025'

// ✅ Correcto
fecha_emision: '2025-11-05'
```

#### Error 4: RUT sin formato correcto
```javascript
// ❌ Incorrecto
rut: '761234567'

// ✅ Correcto (se formatea automáticamente)
rut: '76123456-7'
// o
rut: '76.123.456-7' // Se limpia automáticamente
```

---

## 🔧 Integración en tu Sistema

### En Formularios HTML

```html
<!-- Incluir el script JavaScript -->
<script src="{% static 'js/generador_txt_acepta.js' %}"></script>

<form id="formFactura">
    <!-- Tus campos del formulario -->
    <input type="number" name="folio" id="folio" required>
    <input type="date" name="fecha" id="fecha" required>
    <!-- ... más campos ... -->
    
    <button type="button" onclick="generarFacturaTXT()">
        Generar Archivo TXT
    </button>
</form>

<script>
async function generarFacturaTXT() {
    // Recopilar datos del formulario
    const folio = document.getElementById('folio').value;
    const fecha = document.getElementById('fecha').value;
    
    // Crear estructura de datos
    const datos = GeneradorTXTAcepta.crearFacturaElectronica({
        folio: parseInt(folio),
        fechaEmision: fecha,
        emisor: { /* ... */ },
        receptor: { /* ... */ },
        productos: [ /* ... */ ]
    });
    
    // Generar TXT
    const resultado = await GeneradorTXTAcepta.generarTXT(datos);
    
    if (resultado.success) {
        alert('Archivo generado correctamente');
    } else {
        alert('Error: ' + resultado.error);
    }
}
</script>
```

### En Sistema de Gestión de DTEs

```javascript
// Botón en tabla de DTEs
<button onclick="generarTXTDTE({{ dte.id }})">
    <i class="fas fa-file-download"></i> Descargar TXT
</button>

<script>
async function generarTXTDTE(dteId) {
    try {
        const resultado = await GeneradorTXTAcepta.generarTXTDesdeDTE(dteId);
        
        if (!resultado.success) {
            throw new Error(resultado.error);
        }
        
        // Mostrar notificación de éxito
        mostrarNotificacion('Archivo descargado correctamente', 'success');
        
    } catch (error) {
        console.error('Error:', error);
        mostrarNotificacion('Error al generar archivo: ' + error.message, 'error');
    }
}
</script>
```

---

## 📚 Referencia de Constantes

### Tipos de Documento
```javascript
GeneradorTXTAcepta.TIPOS_DOCUMENTO.FACTURA_ELECTRONICA     // 33
GeneradorTXTAcepta.TIPOS_DOCUMENTO.FACTURA_EXENTA          // 34
GeneradorTXTAcepta.TIPOS_DOCUMENTO.BOLETA_ELECTRONICA      // 39
GeneradorTXTAcepta.TIPOS_DOCUMENTO.BOLETA_EXENTA           // 41
GeneradorTXTAcepta.TIPOS_DOCUMENTO.GUIA_DESPACHO           // 52
GeneradorTXTAcepta.TIPOS_DOCUMENTO.NOTA_DEBITO             // 56
GeneradorTXTAcepta.TIPOS_DOCUMENTO.NOTA_CREDITO            // 61
```

### Formas de Pago
```javascript
GeneradorTXTAcepta.FORMAS_PAGO.CONTADO      // 1
GeneradorTXTAcepta.FORMAS_PAGO.CREDITO      // 2
GeneradorTXTAcepta.FORMAS_PAGO.SIN_COSTO    // 3
```

### Indicadores de Traslado
```javascript
GeneradorTXTAcepta.INDICADORES_TRASLADO.OPERACION_VENTA       // 1
GeneradorTXTAcepta.INDICADORES_TRASLADO.VENTAS_POR_EFECTUAR   // 2
GeneradorTXTAcepta.INDICADORES_TRASLADO.CONSIGNACIONES        // 3
GeneradorTXTAcepta.INDICADORES_TRASLADO.ENTREGA_GRATUITA      // 4
GeneradorTXTAcepta.INDICADORES_TRASLADO.TRASLADOS_INTERNOS    // 5
GeneradorTXTAcepta.INDICADORES_TRASLADO.OTROS_NO_VENTA        // 6
GeneradorTXTAcepta.INDICADORES_TRASLADO.GUIA_DEVOLUCION       // 7
GeneradorTXTAcepta.INDICADORES_TRASLADO.TRASLADO_EXPORTACION  // 8
GeneradorTXTAcepta.INDICADORES_TRASLADO.VENTA_EXPORTACION     // 9
```

---

## 🎓 Mejores Prácticas

### 1. Siempre Validar Antes de Enviar
```javascript
const validacion = GeneradorTXTAcepta.validarDatos(datos);
if (!validacion.valido) {
    console.error('Errores:', validacion.errores);
    return;
}
```

### 2. Manejar Errores Apropiadamente
```javascript
try {
    const resultado = await GeneradorTXTAcepta.generarTXT(datos);
    if (!resultado.success) {
        throw new Error(resultado.error);
    }
} catch (error) {
    console.error('Error:', error);
    // Mostrar mensaje al usuario
}
```

### 3. Usar Constantes en Lugar de Números Mágicos
```javascript
// ❌ Evitar
tipo_documento: 33

// ✅ Preferir
tipo_documento: GeneradorTXTAcepta.TIPOS_DOCUMENTO.FACTURA_ELECTRONICA
```

### 4. Proporcionar Descripciones Detalladas
```javascript
// ✅ Buena práctica
{
    nombre: 'POLERA MANGA CORTA',
    descripcion: 'POLERA MANGA CORTA TALLA M COLOR AZUL MARCA ACME',
    // ...
}
```

### 5. Verificar Folios Antes de Generar
```javascript
// Verificar que el folio esté en el rango autorizado
if (folio < folioMinimo || folio > folioMaximo) {
    alert('Folio fuera de rango autorizado');
    return;
}
```

---

## 🐛 Solución de Problemas

### Problema: "CSRF token missing"
**Solución**: Asegúrate de incluir el token CSRF en tus peticiones POST
```javascript
headers: {
    'X-CSRFToken': GeneradorTXTAcepta.getCSRFToken()
}
```

### Problema: "Error 400 - JSON inválido"
**Solución**: Verifica que estás enviando JSON válido
```javascript
body: JSON.stringify(datos)  // Importante: stringify
```

### Problema: "Archivo no se descarga"
**Solución**: Verifica que el content-type sea 'text/plain' y que el blob se cree correctamente

### Problema: "Caracteres especiales corruptos"
**Solución**: El módulo limpia automáticamente caracteres problemáticos, pero asegúrate de usar UTF-8

---

## 📞 Soporte y Documentación Adicional

Para más información sobre el formato Acepta, consulta:
- `estructura_datos_dte_chile.md` - Especificaciones técnicas completas
- `guia_rapida_dte.md` - Guía rápida de referencia
- `ejemplos_txt_acepta.md` - Ejemplos de archivos TXT
- `tabla_comparativa_dte.md` - Comparación entre tipos de DTE

---

## ✅ Checklist de Implementación

- [ ] Incluir script JavaScript en tu template
- [ ] Configurar URLs en tu proyecto
- [ ] Probar generación de cada tipo de documento
- [ ] Implementar manejo de errores
- [ ] Validar archivos TXT generados
- [ ] Documentar uso para tu equipo
- [ ] Configurar sistema Acepta para recibir archivos
- [ ] Probar en ambiente de certificación SII
- [ ] Implementar en producción

---

**¡Listo para usar!** 🎉

Ahora puedes generar archivos TXT para Acepta de forma fácil y confiable.

