# Módulo de Cotizaciones a Empresas - Instrucciones de Implementación

## ✅ Archivos Creados

1. **Modelos** (`retailmind/app/models.py`):
   - `Cotizacion`: Modelo principal de cotizaciones
   - `CotizacionDetalle`: Detalles/items de cada cotización
   - `HistorialCotizacion`: Registro de cambios y acciones

2. **Vistas** (`retailmind/app/views_modulo_cotizaciones.py`):
   - Vista principal de gestión
   - APIs para CRUD de cotizaciones
   - APIs de búsqueda de productos
   - Funciones para anular y convertir a factura

3. **Templates** (`retailmind/app/templates/vistas/modulo_documentos/gestion_cotizaciones.html`):
   - Interfaz completa con filtros, estadísticas y listado
   - Modal para crear/editar cotizaciones
   - Modal para ver detalles completos
   - Modal de búsqueda de productos

4. **URLs** (`retailmind/app/urls.py`):
   - Rutas configuradas para todas las vistas y APIs

5. **Menú** (`retailmind/app/templates/layout/menu.html`):
   - Icono y enlace agregado al menú de documentos

---

## 🔧 Pasos para Completar la Implementación

### 1. Crear y Aplicar Migraciones

```bash
# Crear las migraciones para los nuevos modelos
python manage.py makemigrations

# Aplicar las migraciones a la base de datos
python manage.py migrate
```

### 2. Registrar Modelos en el Admin (Opcional)

Edita `retailmind/app/admin.py` y agrega:

```python
from .models import Cotizacion, CotizacionDetalle, HistorialCotizacion

@admin.register(Cotizacion)
class CotizacionAdmin(admin.ModelAdmin):
    list_display = ['numero_cotizacion', 'cliente', 'fecha_emision', 'fecha_validez', 'estado', 'total']
    list_filter = ['estado', 'fecha_emision', 'sucursal']
    search_fields = ['numero_cotizacion', 'cliente__nombre', 'cliente__rut']
    readonly_fields = ['created_at', 'updated_at']

@admin.register(CotizacionDetalle)
class CotizacionDetalleAdmin(admin.ModelAdmin):
    list_display = ['cotizacion', 'numero_linea', 'descripcion', 'cantidad', 'precio_unitario', 'subtotal']
    list_filter = ['es_producto_pendiente']
    search_fields = ['descripcion', 'cotizacion__numero_cotizacion']

@admin.register(HistorialCotizacion)
class HistorialCotizacionAdmin(admin.ModelAdmin):
    list_display = ['cotizacion', 'accion', 'usuario', 'timestamp']
    list_filter = ['accion', 'timestamp']
    readonly_fields = ['timestamp']
```

### 3. Verificar Permisos

Asegúrate de que los usuarios tengan los permisos necesarios para:
- Ver cotizaciones
- Crear cotizaciones
- Editar cotizaciones
- Anular cotizaciones
- Convertir a factura

---

## 📋 Funcionalidades Implementadas

### ✅ Gestión de Cotizaciones

1. **Crear Cotizaciones**:
   - Selección de cliente/empresa
   - Configuración de días de validez
   - Descripción general y observaciones
   - Agregar múltiples items
   - Asociar productos existentes o pendientes

2. **Listado con Filtros**:
   - Filtrar por fecha
   - Filtrar por estado (Vigente, Vencida, Facturada, Anulada)
   - Filtrar por cliente
   - Búsqueda en tiempo real
   - Paginación

3. **Estadísticas**:
   - Total de cotizaciones
   - Cotizaciones vigentes
   - Monto total
   - Cotizaciones facturadas

4. **Detalles Completos**:
   - Información del cliente
   - Lista de items cotizados
   - Productos asociados (existentes o pendientes)
   - Días restantes de validez
   - Historial de cambios

### ✅ Control de Validez

- **Tiempo de validez configurable**: Cada cotización tiene días de validez configurables
- **Cálculo automático**: El sistema calcula automáticamente la fecha de vencimiento
- **Estados automáticos**: Las cotizaciones vencidas se marcan automáticamente
- **Indicadores visuales**: Colores que muestran días restantes (verde, amarillo, rojo)

### ✅ Productos

1. **Productos Existentes**:
   - Buscar y asociar productos del inventario
   - Ver stock disponible
   - Precio sugerido del sistema

2. **Productos Pendientes**:
   - Agregar productos que aún no están en el sistema
   - Marcar como "producto pendiente"
   - Registrar fecha estimada de llegada
   - SKU esperado y descripción

### ✅ Facturación

- **Convertir a Factura**: 
  - Solo cotizaciones vigentes pueden facturarse
  - Se marca automáticamente como "Facturada"
  - Se registra número de factura y fecha
  - Queda registro en el historial

### ✅ Historial y Auditoría

- Registro de todas las acciones:
  - Creación
  - Modificación
  - Anulación
  - Facturación
  - Envío al cliente
- Incluye usuario, fecha, hora y IP
- Datos antes y después del cambio (JSON)

---

## 🎨 Características de la Interfaz

1. **Dashboard con Estadísticas**: Métricas visuales en tiempo real
2. **Filtros Avanzados**: Múltiples criterios de búsqueda
3. **Búsqueda en Tiempo Real**: Búsqueda instantánea mientras escribes
4. **Indicadores de Vigencia**: 
   - 🟢 Verde: Más de 7 días restantes
   - 🟡 Amarillo: 1-7 días restantes  
   - 🔴 Rojo: Vencida
5. **Modales Interactivos**: Para crear, editar y ver detalles
6. **Responsive**: Funciona en desktop y móvil
7. **Exportación**: Botón para exportar a Excel (pendiente implementación)

---

## 🔐 Flujo de Procesos

```
1. CREACIÓN
   └─> Usuario crea cotización
       └─> Selecciona cliente
       └─> Define días de validez
       └─> Agrega items (productos existentes o pendientes)
       └─> Sistema calcula totales automáticamente
       └─> Estado: VIGENTE

2. GESTIÓN
   ├─> Editar (solo vigentes)
   ├─> Anular (con motivo)
   └─> Ver detalles completos

3. FACTURACIÓN
   └─> Convertir a factura (solo vigentes)
       └─> Sistema marca como FACTURADA
       └─> Registra número de factura
       └─> Crea historial
       └─> Estado: FACTURADA

4. VENCIMIENTO AUTOMÁTICO
   └─> Sistema revisa fechas
       └─> Si fecha_validez < hoy
           └─> Estado: VENCIDA
```

---

## 🚀 Mejoras Futuras Sugeridas

1. **Integración con Facturación Electrónica**:
   - Generar DTE directamente desde la cotización
   - Enviar por email automáticamente

2. **Plantillas de Cotización**:
   - Guardar cotizaciones frecuentes como plantillas
   - Clonar cotizaciones existentes

3. **Notificaciones**:
   - Email automático al cliente
   - Alertas de cotizaciones por vencer
   - Recordatorio de seguimiento

4. **Reportes**:
   - Reporte de conversión (cotizaciones → facturas)
   - Análisis de productos más cotizados
   - Tiempo promedio de conversión

5. **Workflow de Aprobación**:
   - Aprobaciones para montos altos
   - Múltiples niveles de autorización

6. **Versiones**:
   - Mantener versiones de una misma cotización
   - Comparar cambios entre versiones

7. **Firma Digital**:
   - Permitir que el cliente firme digitalmente
   - PDF con firma electrónica

---

## 🐛 Testing Recomendado

1. **Crear cotización** con productos existentes
2. **Crear cotización** con productos pendientes
3. **Crear cotización** mixta (existentes + pendientes)
4. **Editar cotización** vigente
5. **Intentar editar** cotización facturada (debe fallar)
6. **Anular cotización** con motivo
7. **Convertir a factura** cotización vigente
8. **Intentar facturar** cotización vencida (debe fallar)
9. **Buscar cotizaciones** por diferentes filtros
10. **Verificar cálculo automático** de totales e impuestos

---

## 📝 Notas Importantes

1. **IVA**: El sistema calcula 19% de IVA automáticamente (configuración Chile)
2. **Numeración**: Los números de cotización se generan automáticamente con formato `COT-YYYYMM-XXXX`
3. **Permisos**: Verifica que el usuario tenga permisos en la sucursal activa
4. **Stock**: Para productos existentes, deberías implementar la lógica de obtención de stock real
5. **PDF**: La generación de PDF está pendiente de implementación

---

## 🎯 Próximos Pasos

1. Ejecutar migraciones
2. Probar la creación de cotizaciones
3. Verificar el flujo completo
4. Implementar generación de PDF (opcional)
5. Implementar envío por email (opcional)
6. Agregar al módulo de reportes

---

## ✨ Características Destacadas del Sistema

- ✅ **Control de validez temporal**: Cada cotización tiene tiempo de vida configurable
- ✅ **Productos flexibles**: Soporta productos existentes y pendientes de llegada
- ✅ **Trazabilidad completa**: Historial de todas las acciones
- ✅ **Cálculos automáticos**: Subtotales, IVA y totales calculados automáticamente
- ✅ **Flujo de facturación**: Conversión directa a factura
- ✅ **Interfaz moderna**: Diseño profesional y responsive
- ✅ **Búsqueda avanzada**: Múltiples filtros y búsqueda en tiempo real

---

¡El módulo está listo para usar! 🎉

Para cualquier duda o mejora, consulta la documentación de Django y los modelos implementados.

