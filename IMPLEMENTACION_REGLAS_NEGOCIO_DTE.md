# ✅ REGLAS DE NEGOCIO IMPLEMENTADAS: Emisión DTE

## 📋 REGLAS IMPLEMENTADAS

### ✅ **Regla 1: Precios Según Tipo de Despacho**

#### **Despacho INTERNO** (Entre sucursales propias)
**Precio a usar**: `Costo + Sobreprecio` (Precio de Venta completo)

**Razón**: Es una venta interna entre sucursales de la misma empresa, debe incluir el margen de ganancia.

#### **Despacho EXTERNO** (A proveedores/clientes)
**Precio a usar**: Solo `Costo`

**Razón**: Es un despacho a proveedores, se factura al costo sin margen.

---

### ✅ **Regla 2: Validación de Sucursales para Guías**

#### **Empresas Normales**
Solo pueden enviar guías a sucursales de **su misma empresa**.

**Razón**: Las guías son para traslados internos dentro de la misma organización.

#### **EDEL GILD (Excepción Especial)**
Puede enviar guías a **CUALQUIER sucursal** del sistema.

**Razón**: EDEL GILD tiene permisos especiales para operar con múltiples empresas.

---

## 🔧 IMPLEMENTACIÓN TÉCNICA

### **1. Función getPrecioSegunMetodo**

**Archivo**: `retailmind/app/templates/vistas/modulo_documentos/emisionDTE.html`  
**Línea**: ~2990

#### **Código Implementado:**

```javascript
function getPrecioSegunMetodo(product) {
    if (selectedMethod === 'interno') {
        // DESPACHO INTERNO: Precio de venta completo
        const costo = parseInt(product.costo || 0);
        const sobreprecio = parseInt(product.sobreprecio || 0);
        const precioVenta = costo + sobreprecio;
        
        console.log(`💰 Despacho INTERNO: costo (${costo}) + sobreprecio (${sobreprecio}) = ${precioVenta}`);
        
        return parseInt(product.precio_venta || precioVenta || 0);
        
    } else if (selectedMethod === 'externo') {
        // DESPACHO EXTERNO: Solo costo
        const costo = parseInt(product.costo || 0);
        
        console.log(`💰 Despacho EXTERNO: solo costo = ${costo}`);
        
        return costo;
    }
    
    return parseInt(product.precio_venta || 0);
}
```

#### **Ejemplo de Uso:**

```
Producto:
- Costo: $50,000
- Sobreprecio: $20,000
- Precio Venta: $70,000

Despacho INTERNO:
→ Precio usado: $70,000 (costo + sobreprecio)

Despacho EXTERNO:
→ Precio usado: $50,000 (solo costo)
```

---

### **2. Función loadSucursalesDestino**

**Archivo**: `retailmind/app/templates/vistas/modulo_documentos/emisionDTE.html`  
**Línea**: ~2562

#### **Código Implementado:**

```javascript
function loadSucursalesDestino() {
    let filtroEmpresa = 'misma'; // Por defecto
    
    if (selectedConcept === 'guia') {
        // Verificar si es EDEL GILD
        const empresaActual = '{{ empresa_actual.razon_social|default:"" }}';
        const esEdelGild = empresaActual.toUpperCase().includes('EDEL') && 
                          empresaActual.toUpperCase().includes('GILD');
        
        if (esEdelGild) {
            console.log('🏢 Empresa EDEL GILD: puede enviar a CUALQUIER sucursal');
            filtroEmpresa = 'todas';
        } else {
            console.log('🏢 Empresa normal: solo sucursales de la misma empresa');
            filtroEmpresa = 'misma';
        }
    }
    
    // Cargar sucursales con el filtro apropiado
    $.ajax({
        url: '/app/obtener_sucursales/',
        data: { filtro_empresa: filtroEmpresa }
        ...
    });
}
```

#### **Lógica de Validación:**

```
SI tipo_documento == 'guia':
    SI empresa == 'EDEL GILD':
        → Mostrar TODAS las sucursales
    SINO:
        → Mostrar solo sucursales de la misma empresa
SI tipo_documento == 'factura':
    → Mostrar solo misma empresa
```

---

### **3. Función updateSucursalInfo**

**Archivo**: `retailmind/app/templates/vistas/modulo_documentos/emisionDTE.html`  
**Línea**: ~2646

#### **Mensajes Informativos:**

```javascript
function updateSucursalInfo() {
    const empresaActual = '{{ empresa_actual.razon_social|default:"" }}';
    const esEdelGild = empresaActual.toUpperCase().includes('EDEL') && 
                       empresaActual.toUpperCase().includes('GILD');
    
    if (selectedConcept === 'guia') {
        if (esEdelGild) {
            // Mensaje especial para EDEL GILD
            infoContainer.html(`
                <i class="bi bi-star"></i>
                <strong>EDEL GILD:</strong> Puedes enviar a CUALQUIER sucursal
                (privilegio especial)
            `).addClass('alert-success');
        } else {
            // Mensaje normal para otras empresas
            infoContainer.html(`
                <i class="bi bi-truck"></i>
                <strong>Guía:</strong> Solo sucursales de tu misma empresa
                (traslados internos)
            `).addClass('alert-info');
        }
    }
}
```

---

## 📊 COMPARACIÓN ANTES/DESPUÉS

### **Precios:**

| Tipo Despacho | ANTES | DESPUÉS |
|---------------|-------|---------|
| **Interno** | Sobreprecio | Costo + Sobreprecio ✅ |
| **Externo** | Costo | Costo ✅ |

**Ejemplo:**
```
Producto: Costo $50,000 + Sobreprecio $20,000 = PV $70,000

ANTES:
- Interno: $20,000 (solo sobreprecio) ❌
- Externo: $50,000 (costo) ✅

DESPUÉS:
- Interno: $70,000 (costo + sobreprecio) ✅
- Externo: $50,000 (costo) ✅
```

### **Sucursales Disponibles:**

| Empresa | Tipo Doc | ANTES | DESPUÉS |
|---------|----------|-------|---------|
| **Normal** | Guía | Todas | Solo misma empresa ✅ |
| **EDEL GILD** | Guía | Todas | Todas (privilegio) ✅ |
| **Cualquiera** | Factura | Misma | Misma ✅ |

---

## 🎨 MENSAJES VISUALES

### **Para Empresa Normal (Guía):**

```
┌────────────────────────────────────────────────────────┐
│ ℹ️ Guía de Despacho - Despacho Interno                │
│                                                        │
│ Solo puedes enviar a sucursales de TU MISMA EMPRESA   │
│ (traslados internos)                                   │
│                                                        │
│ Las guías solo pueden enviarse entre sucursales de    │
│ la misma empresa. El documento quedará pendiente.     │
└────────────────────────────────────────────────────────┘
[Fondo azul - alert-info]
```

### **Para EDEL GILD (Guía):**

```
┌────────────────────────────────────────────────────────┐
│ ⭐ Guía de Despacho - EDEL GILD                       │
│                                                        │
│ Puedes enviar a CUALQUIER SUCURSAL del sistema        │
│ (privilegio especial)                                  │
│                                                        │
│ EDEL GILD tiene permisos para hacer guías a           │
│ cualquier sucursal. El documento quedará pendiente.   │
└────────────────────────────────────────────────────────┘
[Fondo verde - alert-success]
```

---

## 🧪 CASOS DE PRUEBA

### **Test 1: Precios en Despacho Interno**

**Datos:**
- Producto: Costo $50,000, Sobreprecio $20,000
- Tipo: Despacho Interno
- Método: Interno

**Pasos:**
1. Seleccionar "Despacho Interno"
2. Buscar producto
3. Abrir modal de tallas
4. Verificar precio mostrado

**Resultado Esperado:**
```
✅ Precio mostrado: $70,000 (no $20,000)
✅ Log en consola: "Despacho INTERNO: costo (50000) + sobreprecio (20000) = 70000"
```

### **Test 2: Precios en Despacho Externo**

**Datos:**
- Producto: Costo $50,000, Sobreprecio $20,000
- Tipo: Despacho Externo
- Método: Externo

**Pasos:**
1. Seleccionar "Despacho Externo"
2. Buscar mismo producto
3. Verificar precio

**Resultado Esperado:**
```
✅ Precio mostrado: $50,000 (solo costo)
✅ Log en consola: "Despacho EXTERNO: solo costo = 50000"
```

### **Test 3: Sucursales para Empresa Normal**

**Datos:**
- Empresa: Cualquiera (NO EDEL GILD)
- Tipo documento: Guía
- Método: Interno

**Pasos:**
1. Seleccionar "Guía de Despacho"
2. Ver dropdown de sucursales

**Resultado Esperado:**
```
✅ Solo aparecen sucursales de la MISMA empresa
✅ Mensaje azul: "Solo puedes enviar a sucursales de tu misma empresa"
✅ Log: "Empresa normal: solo sucursales de la misma empresa"
```

### **Test 4: Sucursales para EDEL GILD**

**Datos:**
- Empresa: EDEL GILD
- Tipo documento: Guía
- Método: Interno

**Pasos:**
1. Seleccionar "Guía de Despacho"
2. Ver dropdown de sucursales

**Resultado Esperado:**
```
✅ Aparecen TODAS las sucursales del sistema
✅ Mensaje verde: "EDEL GILD: Puedes enviar a CUALQUIER sucursal"
✅ Log: "Empresa EDEL GILD detectada: puede enviar a CUALQUIER sucursal"
```

---

## 💡 LÓGICA DE NEGOCIO

### **Precios:**

```
┌─────────────────┬─────────────────────────────────┐
│ Tipo Despacho   │ Precio Usado                    │
├─────────────────┼─────────────────────────────────┤
│ INTERNO         │ Costo + Sobreprecio (PV)        │
│ (Sucursales     │ Incluye margen de ganancia      │
│  propias)       │                                 │
├─────────────────┼─────────────────────────────────┤
│ EXTERNO         │ Solo Costo                      │
│ (Proveedores)   │ Sin margen de ganancia          │
└─────────────────┴─────────────────────────────────┘
```

### **Sucursales:**

```
┌─────────────────┬────────────────────────────────┐
│ Empresa         │ Sucursales Disponibles (Guía)  │
├─────────────────┼────────────────────────────────┤
│ EDEL GILD       │ ⭐ TODAS (privilegio especial) │
├─────────────────┼────────────────────────────────┤
│ Otras empresas  │ Solo de la misma empresa       │
└─────────────────┴────────────────────────────────┘
```

---

## 📝 LOGS DE DEBUG

### **Al Calcular Precio:**

```javascript
// Despacho INTERNO:
💰 getPrecioSegunMetodo - selectedMethod: interno
💰 Product prices: {precio_venta: 70000, sobreprecio: 20000, costo: 50000}
💰 Despacho INTERNO: costo (50000) + sobreprecio (20000) = 70000

// Despacho EXTERNO:
💰 getPrecioSegunMetodo - selectedMethod: externo
💰 Product prices: {precio_venta: 70000, sobreprecio: 20000, costo: 50000}
💰 Despacho EXTERNO: solo costo = 50000
```

### **Al Cargar Sucursales:**

```javascript
// Empresa Normal:
🔄 Cargando sucursales destino...
🏢 Empresa normal: solo sucursales de la misma empresa
📋 Tipo documento: guia, Filtro empresa: misma

// EDEL GILD:
🔄 Cargando sucursales destino...
🏢 Empresa EDEL GILD detectada: puede enviar a CUALQUIER sucursal
📋 Tipo documento: guia, Filtro empresa: todas
```

---

## ✅ BENEFICIOS

### **Para el Negocio:**
1. ✅ **Precios correctos**: Despachos internos incluyen margen
2. ✅ **Control de costos**: Externos solo al costo
3. ✅ **Seguridad**: Solo sucursales autorizadas
4. ✅ **Flexibilidad**: EDEL GILD con privilegios

### **Para el Usuario:**
1. ✅ **Claridad**: Mensajes explican las reglas
2. ✅ **Prevención**: No puede seleccionar sucursales incorrectas
3. ✅ **Transparencia**: Ve precios correctos desde el inicio
4. ✅ **Feedback**: Logs indican qué está pasando

---

## 🚀 CÓMO PROBAR

### **Prueba de Precios:**

```
PASO 1: Despacho Interno
────────────────────────
1. http://localhost:8000/app/emisionDTE/
2. Seleccionar "Despacho Interno"
3. F12 (abrir consola)
4. Buscar producto
5. Abrir modal de tallas
6. Ver precio en columna "Precio"

Verificar:
✅ Precio = Costo + Sobreprecio
✅ Log muestra cálculo

PASO 2: Despacho Externo
─────────────────────────
1. Seleccionar "Despacho Externo"
2. Buscar MISMO producto
3. Ver precio

Verificar:
✅ Precio = Solo Costo (menor que interno)
✅ Log muestra "solo costo"
```

### **Prueba de Sucursales:**

```
PASO 1: Empresa Normal con Guía
────────────────────────────────
1. Login con empresa normal (no EDEL GILD)
2. Ir a emisión DTE
3. Seleccionar "Despacho Interno"
4. Tipo: "Guía de Despacho"
5. Ver dropdown de sucursales

Verificar:
✅ Solo aparecen sucursales de tu misma empresa
✅ Mensaje azul: "Solo puedes enviar a sucursales de tu misma empresa"
✅ Log: "Empresa normal: solo sucursales de la misma empresa"

PASO 2: EDEL GILD con Guía
───────────────────────────
1. Login con EDEL GILD
2. Ir a emisión DTE
3. Seleccionar "Despacho Interno"
4. Tipo: "Guía de Despacho"
5. Ver dropdown de sucursales

Verificar:
✅ Aparecen TODAS las sucursales del sistema
✅ Mensaje verde: "EDEL GILD: Puedes enviar a CUALQUIER sucursal"
✅ Log: "Empresa EDEL GILD detectada"
```

---

## 📊 MATRIZ DE REGLAS

| Tipo Despacho | Tipo Doc | Empresa | Precio Usado | Sucursales Disponibles |
|---------------|----------|---------|--------------|------------------------|
| Interno | Guía | Normal | Costo + Sobreprecio | Solo misma empresa |
| Interno | Guía | EDEL GILD | Costo + Sobreprecio | Todas las sucursales ⭐ |
| Interno | Factura | Cualquiera | Costo + Sobreprecio | Solo misma empresa |
| Externo | Cualquiera | Cualquiera | Solo Costo | N/A (es para clientes) |

---

## 🔍 VERIFICACIÓN EN LOGS

### **Para Empresa Normal:**

```
🔄 Cargando sucursales destino...
🏢 Empresa normal: solo sucursales de la misma empresa
📋 Tipo documento: guia, Filtro empresa: misma
✅ Cargadas 3 sucursales destino
```

### **Para EDEL GILD:**

```
🔄 Cargando sucursales destino...
🏢 Empresa EDEL GILD detectada: puede enviar a CUALQUIER sucursal
📋 Tipo documento: guia, Filtro empresa: todas
✅ Cargadas 15 sucursales destino
```

---

## ✅ ARCHIVOS MODIFICADOS

| Archivo | Función | Cambio |
|---------|---------|--------|
| `emisionDTE.html` | `getPrecioSegunMetodo` | Interno usa costo+sobreprecio |
| `emisionDTE.html` | `loadSucursalesDestino` | Filtro según empresa |
| `emisionDTE.html` | `updateSucursalInfo` | Mensajes según empresa |

**Total**: 1 archivo, 3 funciones modificadas, ~40 líneas

---

## 🎯 RESUMEN

### **Implementado:**

1. ✅ **Precios correctos**:
   - Interno = Precio Venta (costo + sobreprecio)
   - Externo = Solo Costo

2. ✅ **Validación de sucursales**:
   - Normal = Solo misma empresa
   - EDEL GILD = Todas las sucursales

3. ✅ **Mensajes claros**:
   - Usuario sabe qué puede hacer
   - Colores según privilegios

4. ✅ **Logs detallados**:
   - Cálculos de precios visibles
   - Filtros de sucursales explicados

---

**¡Listo para probar en `http://localhost:8000/app/emisionDTE/`!** 🚀

