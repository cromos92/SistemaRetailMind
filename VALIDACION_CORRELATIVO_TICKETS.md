# Validación de Correlativo para Tickets de Venta

## Descripción
Se ha implementado un sistema de validación que **impide crear tickets de venta si no existe un correlativo configurado** para la sucursal actual.

## Cambios Realizados

### 1. Backend - `views_modulo_ventas.py`

#### Vista `ticket_venta()` (Líneas 396-460)
**Modificación:** Se agregó validación de correlativo al cargar la página.

```python
# Validar que existe correlativo para tickets
tiene_correlativo = False
correlativo_info = None
if sucursal_actual:
    try:
        correlativo = Correlativo.objects.get(
            sucursal=sucursal_actual,
            tipo_dte='TICKET'
        )
        tiene_correlativo = correlativo.puede_emitir()
        correlativo_info = {
            'disponibles': correlativo.disponibles,
            'inicio': correlativo.inicio,
            'termino': correlativo.termino,
            'estado': correlativo.estado
        }
    except Correlativo.DoesNotExist:
        tiene_correlativo = False
        correlativo_info = None
```

**Contexto enviado al template:**
- `tiene_correlativo`: Boolean que indica si hay correlativo disponible
- `correlativo_info`: Diccionario con información del correlativo (disponibles, inicio, termino, estado)

#### Vista `crear_ticket_venta()` (Líneas 772-813)
**Modificación:** Se agregó validación antes de crear el ticket.

```python
# Validar que existe correlativo antes de crear el ticket
try:
    correlativo_obj = Correlativo.objects.get(
        sucursal=sucursal,
        tipo_dte='TICKET'
    )
    if not correlativo_obj.puede_emitir():
        return JsonResponse({
            'success': False, 
            'error': f'No hay correlativos disponibles para TICKET en {sucursal.alias}. Por favor, configure un nuevo rango de correlativos.'
        })
except Correlativo.DoesNotExist:
    return JsonResponse({
        'success': False, 
        'error': f'No existe correlativo configurado para TICKET en {sucursal.alias}. Por favor, configure un correlativo antes de crear tickets.'
    })
```

**Mensajes de error:**
- Si no existe correlativo: "No existe correlativo configurado para TICKET en [Sucursal]"
- Si el correlativo está agotado: "No hay correlativos disponibles para TICKET en [Sucursal]"

### 2. Frontend - `ticket_venta.html`

#### Alertas Visuales (Líneas 62-99)
Se agregaron 3 tipos de alertas según el estado del correlativo:

**Alerta PELIGRO (Sin correlativo):**
```html
{% if sucursal_actual and not tiene_correlativo %}
<div class="alert alert-danger">
    No existe un correlativo configurado para TICKET en esta sucursal.
    No podrá crear tickets de venta hasta que se configure un correlativo.
</div>
{% endif %}
```

**Alerta ADVERTENCIA (Correlativo crítico):**
```html
{% elif sucursal_actual and correlativo_info and correlativo_info.estado == 'critico' %}
<div class="alert alert-warning">
    El correlativo de tickets está en estado crítico.
    Quedan {{ correlativo_info.disponibles }} correlativos disponibles.
</div>
{% endif %}
```

**Alerta PELIGRO (Correlativo agotado):**
```html
{% elif sucursal_actual and correlativo_info and correlativo_info.estado == 'agotado' %}
<div class="alert alert-danger">
    El correlativo de tickets está agotado.
    No hay correlativos disponibles.
</div>
{% endif %}
```

#### JavaScript - Validación del Botón (Líneas 475-492)
```javascript
const tieneCorrelativo = {{ tiene_correlativo|yesno:"true,false" }};

// Validar correlativo al cargar la página
if (!tieneCorrelativo) {
    $('#btnImprimirTicket').prop('disabled', true)
        .removeClass('btn-success')
        .addClass('btn-secondary')
        .attr('title', 'No hay correlativo disponible');
}
```

#### JavaScript - Validación en `procesarTicket()` (Líneas 1343-1352)
```javascript
function procesarTicket() {
    // Validar que existe correlativo antes de procesar
    if (!tieneCorrelativo) {
        Swal.fire({
            icon: 'error',
            title: 'Sin correlativo',
            text: 'No existe un correlativo configurado para TICKET en esta sucursal. Por favor, contacte al administrador.',
            confirmButtonText: 'Entendido'
        });
        return;
    }
    // ... resto del código
}
```

#### Mejora en Manejo de Errores (Líneas 1394-1411)
```javascript
.then(response => {
    if (response.success) {
        // ... código de éxito
    } else {
        Swal.fire({
            icon: 'error',
            title: 'Error al crear ticket',
            text: response.error || response.message || 'No se pudo crear el ticket',
            confirmButtonText: 'Entendido'
        });
    }
})
.catch((error) => {
    console.error('Error:', error);
    Swal.fire({
        icon: 'error',
        title: 'Error de conexión',
        text: 'Error al comunicarse con el servidor',
        confirmButtonText: 'Entendido'
    });
});
```

## Validaciones Implementadas

### Nivel 1: Al Cargar la Página
- ✅ Verifica si existe correlativo en la base de datos
- ✅ Muestra alerta visual según el estado del correlativo
- ✅ Deshabilita el botón "Imprimir Ticket" si no hay correlativo

### Nivel 2: Antes de Procesar (Frontend)
- ✅ Valida en JavaScript antes de enviar datos al servidor
- ✅ Muestra mensaje de error con SweetAlert2

### Nivel 3: En el Servidor (Backend)
- ✅ Valida que existe el correlativo en la base de datos
- ✅ Valida que el correlativo puede emitir (inicio <= termino)
- ✅ Retorna mensaje de error descriptivo

## Estados del Correlativo

1. **activo**: Más de 100 correlativos disponibles ✅
2. **critico**: 100 o menos correlativos disponibles ⚠️
3. **agotado**: 0 correlativos disponibles ❌

## Flujo de Validación

```
Usuario accede a /app/ticket-venta/
    ↓
Backend verifica correlativo
    ↓
    ├─→ NO HAY CORRELATIVO → Muestra alerta roja + Botón deshabilitado
    ├─→ CRÍTICO (≤100) → Muestra alerta amarilla + Botón habilitado
    ├─→ AGOTADO (0) → Muestra alerta roja + Botón deshabilitado
    └─→ ACTIVO (>100) → Sin alerta + Botón habilitado
    ↓
Usuario intenta crear ticket
    ↓
Validación JavaScript
    ↓
    ├─→ NO HAY CORRELATIVO → Mensaje de error (no envía al servidor)
    └─→ HAY CORRELATIVO → Envía al servidor
        ↓
    Validación Backend
        ↓
        ├─→ NO EXISTE CORRELATIVO → Error: "No existe correlativo configurado"
        ├─→ CORRELATIVO AGOTADO → Error: "No hay correlativos disponibles"
        └─→ CORRELATIVO OK → Crea ticket exitosamente
```

## Cómo Configurar un Correlativo

Para que el sistema permita crear tickets, se debe configurar un correlativo en el modelo `Correlativo`:

```python
from app.models import Correlativo, Sucursal

sucursal = Sucursal.objects.get(id=1)  # Cambiar por ID de sucursal

Correlativo.objects.create(
    sucursal=sucursal,
    tipo_dte='TICKET',
    inicio=1,
    termino=999999,
    alias=f'TICKET_{sucursal.alias}',
    responsable='Administrador'
)
```

O desde el panel de administración de Django:
1. Ir a `/admin/app/correlativo/`
2. Agregar nuevo correlativo
3. Configurar:
   - Sucursal: [Seleccionar sucursal]
   - Tipo DTE: TICKET
   - Inicio: 1
   - Término: 999999

## Mensajes de Usuario

### Sin Correlativo Configurado
```
¡Atención! No existe un correlativo configurado para TICKET en esta sucursal.
No podrá crear tickets de venta hasta que se configure un correlativo. 
Por favor, contacte al administrador.
```

### Correlativo Crítico
```
¡Advertencia! El correlativo de tickets está en estado crítico.
Quedan XX correlativos disponibles (Rango: X - Y)
```

### Correlativo Agotado
```
¡Error! El correlativo de tickets está agotado.
No hay correlativos disponibles. Por favor, configure un nuevo rango de correlativos.
```

## Archivos Modificados

1. **Backend:**
   - `retailmind/app/views_modulo_ventas.py` (Vista `ticket_venta` y `crear_ticket_venta`)

2. **Frontend:**
   - `retailmind/app/templates/vistas/modulo_ventas/ticket_venta.html`

## Pruebas Recomendadas

1. ✅ Acceder sin correlativo configurado → Debe mostrar alerta y botón deshabilitado
2. ✅ Intentar crear ticket sin correlativo → Debe mostrar error
3. ✅ Crear correlativo con pocos números (≤100) → Debe mostrar alerta amarilla
4. ✅ Agotar correlativo → Debe mostrar alerta roja y no permitir crear tickets
5. ✅ Crear correlativo con números suficientes → Debe permitir crear tickets

---

**Fecha de implementación:** 7 de Noviembre, 2025
**Versión:** 1.0
**Desarrollado por:** AI Assistant (Claude Sonnet 4.5)

