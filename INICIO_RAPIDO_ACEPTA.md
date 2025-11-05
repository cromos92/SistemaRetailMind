# ⚡ INICIO RÁPIDO - GENERADOR TXT ACEPTA

## 🎯 En 3 Pasos

### 1️⃣ Accede a la Interfaz
```
URL: http://localhost:8000/app/configuracion/interfaz-prueba-acepta/
```

### 2️⃣ Haz Clic en "Cargar Ejemplo"
Se cargarán datos de prueba automáticamente

### 3️⃣ Haz Clic en "Generar Archivo TXT"
¡Listo! El archivo se descarga automáticamente

---

## 🎨 Interfaz Visual

La interfaz incluye:
- ✅ Selección visual de tipo de documento
- ✅ Formulario completo auto-validado
- ✅ Cálculo automático de totales
- ✅ Botón de ejemplo para pruebas
- ✅ Descarga instantánea

---

## 📝 Tipos de Documentos Disponibles

| Código | Documento | Uso |
|--------|-----------|-----|
| **33** | Factura Electrónica | Venta B2B con IVA |
| **39** | Boleta Electrónica | Consumidor final |
| **52** | Guía de Despacho | Traslado de mercadería |
| **61** | Nota de Crédito | Anulación/devolución |
| **34** | Factura Exenta | Sin IVA |
| **41** | Boleta Exenta | Consumidor final sin IVA |

---

## 💻 Para Programadores

### JavaScript
```javascript
const datos = GeneradorTXTAcepta.crearFacturaElectronica({
    folio: 12345,
    fechaEmision: '2025-11-05',
    emisor: {...},
    receptor: {...},
    productos: [...]
});

await GeneradorTXTAcepta.generarTXT(datos);
```

### Python
```python
from retailmind.app.views_modulo_documentos import generar_txt_dte_acepta

contenido = generar_txt_dte_acepta(datos)
```

---

## 📚 Documentación Completa

1. **INSTRUCCIONES_INTERFAZ_PRUEBA_ACEPTA.md** - Guía de la interfaz web
2. **MODULO_GENERACION_TXT_ACEPTA.md** - Documentación técnica
3. **README_MODULO_TXT_ACEPTA.md** - Resumen ejecutivo
4. **ejemplos_uso_generador_txt.py** - 9 ejemplos Python

---

## ✅ ¿Todo Funciona?

Prueba ahora:
1. Navega a `/app/configuracion/interfaz-prueba-acepta/` o usa el menú: Configuración → Interfaz Prueba Acepta
2. Clic en "Cargar Ejemplo"
3. Clic en "Generar Archivo TXT"
4. ✅ ¡Archivo descargado!

---

## 🎉 ¡Listo!

El módulo está **100% funcional** y listo para usar.

**Tiempo de prueba:** 30 segundos  
**Complejidad:** ⭐ Muy fácil  
**Estado:** ✅ Producción Ready

