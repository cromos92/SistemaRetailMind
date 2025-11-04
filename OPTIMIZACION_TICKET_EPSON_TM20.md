# Optimización de Tickets para Impresora Térmica Epson TM-20

## 📋 Resumen de Optimizaciones

Se ha optimizado el sistema de impresión de tickets en el módulo de ventas (`ticket_venta.html`) específicamente para la impresora térmica **Epson TM-20**.

### ✨ Mejoras Implementadas

1. **Reducción de tamaño de papel**
   - Ancho optimizado: **58mm** (estándar para TM-20)
   - Papel anterior: 80mm

2. **Optimización de fuentes**
   - Fuente principal: `9px` (reducido desde `11px`)
   - Fuente header: `11px` (reducido desde `13px`)
   - Fuente footer: `7px` (reducido desde `9px`)
   - Fuente items: `8px` (reducido desde `10px`)

3. **Reducción de espaciado**
   - Padding body: `2mm 3mm` (reducido desde `5mm`)
   - Márgenes entre elementos: reducidos 40%
   - Line-height: `1.3` (optimizado)

4. **Código de barras compacto**
   - Altura: `35px` (reducido desde `50px`)
   - Width: `1.5` (reducido desde `2`)
   - Márgenes: `5px` (reducido desde `10px`)

5. **Estructura mejorada**
   - 3 líneas por producto (compacto)
   - Truncamiento inteligente de textos
   - Mejor uso del espacio disponible

---

## 🔧 Configuración por Tipo de Papel

### Opción 1: Papel de 58mm (Recomendado - Ya configurado)

✅ **Configuración actual** - Ideal para la mayoría de casos

```javascript
body {
    width: 58mm;
    font-size: 9px;
    padding: 2mm 3mm;
}
```

**Características:**
- Máximo de ~32 caracteres por línea
- Ahorro de papel ~27%
- Impresión más rápida
- Menor costo de papel

---

### Opción 2: Papel de 80mm (Si necesitas más espacio)

Si necesitas nombres de productos más largos o más información, puedes cambiar a 80mm:

**Cambios necesarios en `ticket_venta.html` (líneas 1354-1363):**

```javascript
body {
    font-family: 'Courier New', Courier, monospace;
    font-size: 10px;        // Aumentar a 10px
    width: 80mm;            // Cambiar a 80mm
    max-width: 80mm;        // Cambiar a 80mm
    padding: 3mm 4mm;       // Aumentar padding
    background: #fff;
    color: #000;
    line-height: 1.4;       // Aumentar line-height
}
```

**Y en la sección @media print (líneas 1507-1516):**

```javascript
@media print {
    body { 
        width: 80mm;        // Cambiar a 80mm
        padding: 2mm 4mm;   // Ajustar padding
    }
    @page { 
        size: 80mm auto;    // Ya está correcto
        margin: 0;
    }
}
```

**Y ajustar límites de truncamiento (líneas 1313-1320):**

```javascript
const productosHtml = productos.map((producto) => {
    const cant = producto.cantidad || 0;
    const articulo = truncar(producto.articulo, 30);  // Aumentar de 22 a 30
    const desc = truncar(producto.descripcion, 25);   // Aumentar de 18 a 25
    const talla = truncar(producto.talla || '-', 5);  // Mantener o aumentar
    // ...
});
```

---

## 📏 Tabla Comparativa de Anchos

| Ancho Papel | Caracteres/Línea | Ahorro Papel | Velocidad | Recomendado Para |
|-------------|------------------|--------------|-----------|------------------|
| **58mm** ✓  | ~32 chars        | 27%          | Más rápida | Retail general, alta rotación |
| 80mm        | ~42 chars        | 0% (base)    | Normal    | Productos con nombres largos |

---

## 🎨 Personalización Adicional

### Cambiar tamaño de fuente global

En la línea **1356** de `ticket_venta.html`:

```javascript
// Más pequeño (máximo ahorro)
font-size: 8px;

// Actual (balanceado)
font-size: 9px;

// Más grande (mejor legibilidad)
font-size: 10px;
```

### Ajustar altura del código de barras

En las líneas **1587-1596**:

```javascript
JsBarcode("#barcode", ticketId, {
    format: "CODE128",
    width: 1.5,         // 1.0 = muy delgado, 2.0 = grueso
    height: 35,         // 30-50px recomendado
    displayValue: true,
    fontSize: 12,       // Tamaño número debajo
    margin: 5,
    marginTop: 2,
    marginBottom: 2
});
```

### Modificar políticas en footer

En las líneas **1574-1580**:

```javascript
<div class="footer">
    <div><strong>¡GRACIAS POR SU COMPRA!</strong></div>
    <div>Cambios hasta 15 días</div>          // ← Modificar aquí
    <div>* Sin devolución de dinero</div>     // ← Modificar aquí
    ${sucursal.telefono ? '<div>Tel: ' + sucursal.telefono + '</div>' : ''}
    <div style="margin-top:3px;font-size:6px;">RetailMind POS</div>
</div>
```

---

## 🖨️ Configuración de la Impresora

### Configuración Recomendada en Windows

1. **Panel de Control** → **Dispositivos e Impresoras**
2. Clic derecho en **EPSON TM-T20** → **Preferencias de impresión**
3. Configurar:
   - **Tamaño de papel**: 58mm (o crear tamaño personalizado)
   - **Orientación**: Vertical
   - **Márgenes**: 0mm en todos los lados
   - **Calidad**: Normal o Alta
   - **Velocidad**: Estándar

### Crear Tamaño de Papel Personalizado

Si no aparece 58mm en opciones:

1. **Panel de Control** → **Dispositivos e Impresoras**
2. Clic en **Servidores de impresión** (arriba)
3. Pestaña **Formularios**
4. ✓ **Crear un nuevo formulario**
5. Configurar:
   - Nombre: `Ticket 58mm`
   - Ancho: `5.8 cm`
   - Alto: `29.7 cm` (o `Sin límite`)
   - Márgenes: `0 cm` en todos

---

## 📊 Ejemplo de Ticket Optimizado

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ||||||||||||||||||||||
      0000000123
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        MI TIENDA
      Sucursal Centro
    Av. Principal #123
    RUT: 12.345.678-9
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ticket:    #000123
Fecha:     03/11/2025
Hora:      14:30
Vendedor:  Juan Pérez
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
2x Polera Básica
   Algodón 100% T:M
   @$9.990    $19.980

1x Pantalón Denim
   Azul Oscuro T:32
   @$29.990   $29.990
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Items:           3
TOTAL:      $49.970
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ¡GRACIAS POR SU COMPRA!
   Cambios hasta 15 días
  * Sin devolución de dinero
     Tel: 2-2345-6789
       RetailMind POS
```

---

## 🔍 Solución de Problemas

### Problema: Texto cortado en los bordes

**Solución:** Aumentar padding horizontal
```javascript
padding: 2mm 4mm;  // Aumentar el segundo valor
```

### Problema: Ticket muy largo

**Solución:** 
1. Reducir tamaño de fuente a `8px`
2. Reducir line-height a `1.2`
3. Reducir márgenes entre secciones

### Problema: Código de barras no se lee

**Solución:** Aumentar parámetros del código de barras
```javascript
width: 2,      // Aumentar grosor
height: 45,    // Aumentar altura
margin: 8,     // Aumentar margen
```

### Problema: Fuente muy pequeña, difícil de leer

**Solución:** Cambiar a 80mm con fuente de 10px (ver Opción 2)

---

## 📝 Notas Importantes

1. ✅ Los cambios ya están aplicados para **58mm**
2. ✅ El diseño es **responsive** y se adapta automáticamente
3. ✅ Compatible con navegadores modernos
4. ✅ Soporta impresión directa desde el navegador
5. ⚠️ Para 80mm, seguir instrucciones de "Opción 2"

---

## 🚀 Archivos Modificados

- `retailmind/app/templates/vistas/modulo_ventas/ticket_venta.html`
  - Líneas 1281-1301: Función `imprimirTicketTermico()` optimizada
  - Líneas 1303-1620: Función `generarHtmlTicket()` mejorada
  - CSS completamente optimizado para 58mm

---

## 📞 Soporte

Para cambios adicionales o problemas, revisar:
1. Este documento primero
2. Comentarios en el código (líneas 1347-1516)
3. Logs del navegador (F12) durante impresión

---

**Fecha de optimización:** 03/11/2025  
**Versión:** 2.0 - Epson TM-20 Optimized  
**Desarrollador:** RetailMind System

