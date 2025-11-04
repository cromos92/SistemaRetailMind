# 🧾 Sistema de Emisión de DTE

## 📋 Descripción

Sistema completo para la emisión de Documentos Tributarios Electrónicos (DTE) con las siguientes características:

### ✨ Funcionalidades Principales

1. **Configuración de Documento**
   - ✅ Método de Despacho: Interno vs Externo
   - ✅ Tipo de Documento: Factura Electrónica vs Guía de Despacho
   - ✅ Selección de receptor (cliente)

2. **Búsqueda de Productos**
   - ✅ Búsqueda por SKU, descripción, marca
   - ✅ Filtros por marca, categoría, tipo de talla
   - ✅ Paginación para grandes volúmenes
   - ✅ Visualización de stock en tiempo real

3. **Selección de Tallas**
   - ✅ Modal dedicado para selección de tallas
   - ✅ Opción "Seleccionar Todas"
   - ✅ Control de cantidad por talla
   - ✅ Validación de stock disponible

4. **Detalle y Totales**
   - ✅ Tabla interactiva con productos seleccionados
   - ✅ Cálculo automático de subtotales, IVA y total
   - ✅ Edición de cantidades en tiempo real
   - ✅ Contador de unidades totales

5. **Emisión y Control**
   - ✅ Vista previa del documento
   - ✅ Validaciones completas
   - ✅ Actualización automática de stock
   - ✅ Registro de movimientos de inventario

## 🚀 Instalación y Configuración

### 1. Archivos Creados/Modificados

```
retailmind/
├── app/
│   ├── templates/vistas/modulo_documentos/
│   │   └── emisionDTE.html                    # ✅ Nuevo
│   ├── management/commands/
│   │   └── inicializar_datos_dte.py          # ✅ Nuevo
│   ├── views.py                               # ✅ Modificado
│   └── urls.py                                # ✅ Modificado
└── app/templates/layout/
    └── menu.html                              # ✅ Modificado
```

### 2. Inicializar Datos de Prueba

```bash
# Ejecutar desde la carpeta del proyecto Django
python manage.py inicializar_datos_dte
```

Este comando creará:
- 1 Cliente de ejemplo
- 4 Productos con tallas y stock
- Marcas: Nike, Adidas, Puma, Reebok, Converse
- Colores: Negro, Blanco, Azul, Rojo, Verde, Gris
- Géneros: Hombre, Mujer, Unisex, Niño, Niña
- Categorías: Zapatillas Deportivas, Casuales, Botas, etc.

### 3. Acceso al Sistema

1. Inicia sesión en el sistema
2. Ve a **Módulo Documentos** → **Emisión DTE**
3. ¡Listo para usar! 🎉

## 📱 Cómo Usar el Sistema

### Paso 1: Configuración
1. Selecciona **Método de Despacho** (Interno/Externo)
2. Selecciona **Tipo de Documento** (Factura/Guía)
3. Elige el **Receptor** del documento
4. Confirma la **Fecha de Emisión**

### Paso 2: Búsqueda de Productos
1. Usa el buscador para encontrar productos
2. Aplica filtros por marca, categoría o tipo de talla
3. Navega por los resultados paginados
4. Haz clic en **"Seleccionar"** en el producto deseado

### Paso 3: Selección de Tallas
1. En el modal, selecciona las tallas deseadas
2. Ajusta las cantidades para cada talla
3. Usa **"Seleccionar Todas"** si necesitas todas las tallas
4. Confirma la selección

### Paso 4: Revisión y Emisión
1. Revisa el detalle en la tabla
2. Verifica los totales calculados automáticamente
3. Agrega observaciones si es necesario
4. Usa **"Vista Previa"** para revisar el documento
5. Haz clic en **"Emitir DTE"** para finalizar

## 🔧 Endpoints API

### Principales Endpoints

```
GET  /app/emisionDTE/                    # Vista principal
GET  /app/empresas_clientes/             # Lista de clientes
GET  /app/obtener_marcas/                # Lista de marcas
GET  /app/obtener_categorias/            # Lista de categorías
POST /app/buscar_productos_bodega/       # Búsqueda de productos
POST /app/emitir_dte/                    # Procesar emisión
```

### Ejemplo de Búsqueda de Productos

```javascript
// POST /app/buscar_productos_bodega/
{
    "search": "Nike",
    "marca": 1,
    "categoria": 2,
    "tipo_talla": "CL",
    "page": 1,
    "page_size": 10
}
```

### Ejemplo de Emisión de DTE

```javascript
// POST /app/emitir_dte/
{
    "metodo_despacho": "interno",
    "tipo_documento": "factura",
    "receptor_id": 1,
    "fecha_emision": "2024-01-15",
    "observaciones": "Entrega urgente",
    "detalle_productos": [
        {
            "talla_id": 123,
            "cantidad": 2,
            "precio": 65000
        }
    ]
}
```

## 🎨 Características de Diseño

### Interfaz Moderna
- ✅ Diseño responsive con Bootstrap 5
- ✅ Iconos Bootstrap Icons
- ✅ Animaciones suaves y transiciones
- ✅ Loading states y feedback visual
- ✅ Colores consistentes con el sistema

### UX Optimizada
- ✅ Selectores visuales intuitivos
- ✅ Validaciones en tiempo real
- ✅ Mensajes de error claros
- ✅ Confirmaciones de acciones importantes
- ✅ Vista previa antes de emitir

## 🔒 Seguridad y Validaciones

### Validaciones del Sistema
- ✅ Verificación de stock disponible
- ✅ Validación de datos obligatorios
- ✅ Verificación de sesión activa
- ✅ Control de permisos por usuario
- ✅ Transacciones atómicas

### Integridad de Datos
- ✅ Actualización automática de stock
- ✅ Registro de movimientos de inventario
- ✅ Numeración correlativa de documentos
- ✅ Cálculos precisos de totales e IVA

## 📊 Integración con el Sistema

### Modelos Utilizados
- `Dte` - Documento principal
- `Dte_Productos` - Detalle de productos
- `Movimientos_Producto` - Trazabilidad de stock
- `Producto` y `Producto_Talla` - Inventario
- `Empresa` - Emisor y receptor

### Flujo de Datos
1. **Selección** → Validación de stock
2. **Emisión** → Creación de DTE
3. **Actualización** → Reducción de stock
4. **Registro** → Movimiento de inventario
5. **Confirmación** → Número de documento

## 🐛 Solución de Problemas

### Problemas Comunes

**Error: "No hay sucursal activa"**
- Verifica que el usuario tenga una sucursal asignada en la sesión

**Error: "Stock insuficiente"**
- El stock se valida en tiempo real al emitir el DTE

**No aparecen productos**
- Ejecuta el comando `inicializar_datos_dte` para crear datos de prueba

**Error en la búsqueda**
- Verifica que existan productos con stock > 0 en la sucursal actual

## 🚀 Próximas Mejoras

### Funcionalidades Planificadas
- [ ] Integración con SII para envío automático
- [ ] Generación de PDF del documento
- [ ] Códigos de barras en productos
- [ ] Descuentos por línea y globales
- [ ] Múltiples formas de pago
- [ ] Plantillas de documentos personalizables

### Optimizaciones
- [ ] Cache de búsquedas frecuentes
- [ ] Búsqueda por código de barras
- [ ] Autocompletado inteligente
- [ ] Exportación a Excel/PDF

## 📞 Soporte

Para reportar problemas o solicitar nuevas funcionalidades, contacta al equipo de desarrollo.

---

**¡El sistema está listo para usar! 🎉**

Navega a **Módulo Documentos** → **Emisión DTE** y comienza a emitir tus documentos electrónicos.
