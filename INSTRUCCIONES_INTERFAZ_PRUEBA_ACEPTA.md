# 🎨 INSTRUCCIONES - INTERFAZ DE PRUEBA ACEPTA

## 🌐 Acceso a la Interfaz

La interfaz de prueba del generador de archivos TXT para Acepta está disponible en:

```
URL: http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
```

**Ruta completa en producción:**
```
https://tu-dominio.com/app/configuracion/interfaz-prueba-acepta/
```

**Para desarrollo local:**
```
http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
http://127.0.0.1:8000/app/configuracion/interfaz-prueba-acepta/
```

**Desde el menú:**
```
Configuración → Interfaz Prueba Acepta
```

---

## 📋 Características de la Interfaz

### ✨ Funcionalidades

1. **Selección de Tipo de Documento**
   - Factura Electrónica (33)
   - Boleta Electrónica (39)
   - Guía de Despacho (52)
   - Nota de Crédito (61)
   - Factura Exenta (34)
   - Boleta Exenta (41)

2. **Formulario Completo**
   - Datos del documento (folio, fecha, forma de pago)
   - Datos del emisor (RUT, razón social, giro, etc.)
   - Datos del receptor
   - Productos/servicios (dinámico, agregar/eliminar)
   - Cálculo automático de totales

3. **Acciones Disponibles**
   - ✅ Calcular totales automáticamente
   - ✅ Cargar datos de ejemplo
   - ✅ Limpiar formulario
   - ✅ Generar y descargar archivo TXT

---

## 🎯 Guía de Uso Paso a Paso

### 1️⃣ Acceder a la Interfaz
```
Navega a: /app/configuracion/interfaz-prueba-acepta/
O desde el menú: Configuración → Interfaz Prueba Acepta
```

### 2️⃣ Seleccionar Tipo de Documento
Haz clic en una de las tarjetas de tipo de documento (la seleccionada se resaltará en color púrpura)

### 3️⃣ Completar Datos del Documento
- **Folio**: Número correlativo (obligatorio)
- **Fecha de Emisión**: Se precarga con la fecha actual
- **Forma de Pago**: Contado/Crédito/Sin Costo

### 4️⃣ Datos del Emisor (Tu Empresa)
Campos obligatorios marcados con *:
- RUT Emisor
- Razón Social
- Giro

### 5️⃣ Datos del Receptor (Cliente)
- RUT Receptor (usar 66.666.666-6 para consumidor final)
- Razón Social
- Otros datos opcionales

### 6️⃣ Agregar Productos
1. Completa los datos del primer producto
2. Haz clic en "+ Agregar Producto" para más líneas
3. Puedes eliminar productos con el botón de basura

### 7️⃣ Calcular Totales
Haz clic en el botón "Calcular" para ver:
- Subtotal Neto
- IVA (19%)
- Total

### 8️⃣ Generar Archivo
Haz clic en "Generar Archivo TXT" y el archivo se descargará automáticamente

---

## 🚀 Inicio Rápido con Ejemplo

### Opción Rápida: Usar Datos de Ejemplo

1. Accede a la interfaz
2. Haz clic en el botón **"Cargar Ejemplo"**
3. Se cargarán datos de prueba completos
4. Haz clic en **"Calcular"** para ver totales
5. Haz clic en **"Generar Archivo TXT"**
6. ¡Listo! El archivo se descarga automáticamente

**Tiempo estimado: 30 segundos**

---

## 📱 Interfaz Responsive

La interfaz está diseñada para funcionar en:
- ✅ Desktop (resolución completa)
- ✅ Tablet (diseño adaptado)
- ✅ Mobile (menú colapsable)

---

## 🎨 Diseño Visual

### Características del Diseño:
- **Colores**: Gradiente púrpura moderno
- **Estilo**: Cards con sombras y hover effects
- **Iconos**: Font Awesome 6.0
- **Framework**: Bootstrap 5.1.3
- **Responsive**: Totalmente adaptable

### Elementos Visuales:
- 📊 Cards de selección con hover animado
- 🎯 Campos agrupados por sección
- ✨ Gradientes modernos
- 🔢 Cálculo en tiempo real
- 📥 Descarga automática

---

## 🔧 Integración en el Sistema

### Agregar Link en el Menú Principal

Si quieres agregar un enlace permanente en tu menú:

```html
<!-- En tu template de menú/navbar -->
<a href="{% url 'interfaz_prueba_acepta' %}" class="nav-link">
    <i class="fas fa-file-invoice"></i>
    Generador TXT Acepta
</a>
```

### Agregar como Tarjeta en Dashboard

```html
<div class="col-md-4">
    <div class="card">
        <div class="card-body">
            <h5 class="card-title">
                <i class="fas fa-file-invoice"></i>
                Generador TXT Acepta
            </h5>
            <p class="card-text">
                Genera archivos TXT para el sistema de facturación Acepta
            </p>
            <a href="{% url 'interfaz_prueba_acepta' %}" class="btn btn-primary">
                Abrir Interfaz
            </a>
        </div>
    </div>
</div>
```

---

## 📝 Ejemplos de Uso

### Ejemplo 1: Factura Electrónica Simple
1. Seleccionar "Factura Electrónica (33)"
2. Folio: `12345`
3. Emisor RUT: `76.123.456-7`
4. Emisor Razón Social: `MI EMPRESA LTDA`
5. Emisor Giro: `COMERCIO`
6. Receptor RUT: `77.654.321-K`
7. Receptor Razón Social: `CLIENTE ABC`
8. Producto: `SERVICIO A`, Cantidad: `1`, Precio: `100000`
9. Calcular y Generar

### Ejemplo 2: Boleta Consumidor Final
1. Seleccionar "Boleta Electrónica (39)"
2. Folio: `5678`
3. Emisor (completar datos)
4. Receptor RUT: `66.666.666-6`
5. Receptor Razón Social: `CONSUMIDOR FINAL`
6. Productos (agregar los que necesites)
7. Calcular y Generar

### Ejemplo 3: Nota de Crédito
1. Seleccionar "Nota de Crédito (61)"
2. Completar datos de emisor y receptor
3. Agregar productos (los montos se convertirán a negativos automáticamente)
4. Generar

---

## ⚠️ Validaciones Automáticas

La interfaz valida:
- ✅ Campos obligatorios (*, no pueden estar vacíos)
- ✅ Al menos un producto agregado
- ✅ Formato correcto de datos
- ✅ Coherencia de valores

Si algo falta, verás un mensaje de alerta indicando qué corregir.

---

## 💾 Archivo Generado

### Nombre del Archivo
```
dte_[TIPO]_[FOLIO]_[FECHA].txt
```

**Ejemplo:**
```
dte_33_12345_20251105.txt
```

### Ubicación
El archivo se descarga automáticamente en tu carpeta de descargas del navegador.

### Formato
- Encoding: UTF-8
- Separador: Pipe (|)
- Estructura: 5+ líneas según especificación Acepta

---

## 🐛 Solución de Problemas

### Problema: "No se descarga el archivo"
**Solución:** Verifica que tu navegador permita descargas automáticas

### Problema: "Error al generar archivo"
**Solución:** Revisa que todos los campos obligatorios (*) estén completos

### Problema: "Caracteres raros en el archivo"
**Solución:** El archivo usa UTF-8, ábrelo con un editor que soporte este encoding

### Problema: "Los totales no se calculan"
**Solución:** Asegúrate de hacer clic en el botón "Calcular"

---

## 🔒 Seguridad

- ✅ Requiere login (decorador `@login_required`)
- ✅ Protección CSRF en todas las peticiones
- ✅ Validación de datos en servidor
- ✅ Sanitización de textos

---

## 📊 Flujo Completo del Proceso

```
1. Usuario accede a /app/configuracion/interfaz-prueba-acepta/
              ↓
2. Completa formulario con datos del DTE
              ↓
3. Hace clic en "Generar Archivo TXT"
              ↓
4. JavaScript recopila datos del formulario
              ↓
5. Valida datos con GeneradorTXTAcepta.validarDatos()
              ↓
6. Envía POST a /documentos/generar-txt-acepta/
              ↓
7. Backend genera archivo TXT
              ↓
8. Retorna archivo con headers de descarga
              ↓
9. Navegador descarga archivo automáticamente
              ↓
10. Usuario puede subir el archivo a Acepta
```

---

## 🎓 Tips y Recomendaciones

### ✨ Mejores Prácticas

1. **Usar el botón "Cargar Ejemplo"**
   - Ideal para familiarizarse con la interfaz
   - Datos pre-completados correctamente

2. **Calcular totales antes de generar**
   - Verifica que los montos sean correctos
   - Evita errores en el archivo TXT

3. **Guardar datos frecuentes**
   - Para emisores que usas siempre, considera anotar los datos
   - Puedes crear plantillas en un documento aparte

4. **Verificar RUT del receptor**
   - Para boletas usar: 66.666.666-6
   - Para facturas usar el RUT real del cliente

5. **Probar primero en ambiente de certificación**
   - Genera archivos de prueba
   - Valida con Acepta antes de producción

---

## 📞 Soporte

### Documentación Relacionada
- `MODULO_GENERACION_TXT_ACEPTA.md` - Documentación técnica completa
- `README_MODULO_TXT_ACEPTA.md` - Resumen ejecutivo
- `ejemplos_uso_generador_txt.py` - Ejemplos Python

### Archivos de Referencia Acepta
- `estructura_datos_dte_chile.md`
- `guia_rapida_dte.md`
- `ejemplos_txt_acepta.md`

---

## ✅ Checklist de Uso

Antes de generar un archivo TXT, verifica:

- [ ] Tipo de documento correcto seleccionado
- [ ] Folio dentro del rango autorizado
- [ ] Fecha de emisión correcta
- [ ] RUT emisor válido
- [ ] Razón social emisor completa
- [ ] Giro emisor especificado
- [ ] RUT receptor válido
- [ ] Al menos 1 producto agregado
- [ ] Productos con nombre, cantidad y precio
- [ ] Totales calculados
- [ ] Todo se ve correcto visualmente

---

## 🚀 ¡Listo para Usar!

La interfaz está completamente funcional y lista para generar archivos TXT para Acepta.

**URL de acceso:**
```
/app/configuracion/interfaz-prueba-acepta/
```

**O desde el menú:**
```
Configuración → Interfaz Prueba Acepta
```

**¡Comienza generando tus primeros archivos TXT ahora!** 🎉

---

**Última actualización:** Noviembre 2025  
**Versión:** 1.0  
**Estado:** ✅ Funcional

