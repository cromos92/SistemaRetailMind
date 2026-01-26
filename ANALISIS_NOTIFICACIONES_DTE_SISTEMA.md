# 🔔 ANÁLISIS: Sistema de Notificaciones de DTEs Pendientes

## 🎯 RESUMEN

El sistema actual tiene **2 mecanismos de notificación**:

1. **`NotificacionDTE`** - Notificaciones entre empresas diferentes
2. **Alertas en Navbar** - DTEs pendientes de recepcionar (misma empresa, diferentes sucursales)

---

## 📊 ANÁLISIS DEL SISTEMA ACTUAL

### 1. **Notificaciones entre Empresas Diferentes** (`NotificacionDTE`)

**Modelo:** `models.py` línea 5017

**¿Cuándo se crean?**
- Cuando una empresa emite DTE a OTRA empresa diferente
- Cuando hay problemas de regularización

**¿Cómo se eliminan?**
- Se pueden descartar manualmente
- Se eliminan automáticamente cuando el DTE cambia de estado (signals.py línea 40)

**¿Para qué sirven?**
- Notificar ventas entre empresas del grupo empresarial
- Notificar problemas que requieren regularización

---

### 2. **Alertas de DTEs Pendientes en Navbar** (Tu caso)

**Modelo:** `DteAlertaDescartada` - models.py línea 5219

**¿Cómo funciona?**

```python
# views.py línea 17820-17839
# Obtiene DTEs pendientes de recepcionar
dtes_query = Dte.objects.filter(
    tipo_transaccion='TRASPASO',
    estado_dte='EMITIDO',  # ← Solo DTEs emitidos
    dte_movimientos__concepto='TRASPASO_SALIDA',
    dte_movimientos__sucursal_destino_id=sucursal_destino_id
).exclude(
    alertas_descartadas__usuario_id=usuario_id  # ← Excluye descartadas por el usuario
)
```

**¿Cómo se genera la alerta?**
- NO se guarda en BD como notificación
- Se calcula **dinámicamente** cada vez que cargas el menú
- Consulta DTEs con `estado_dte='EMITIDO'` y `tipo_transaccion='TRASPASO'`

**¿Cómo se descarta?**

```python
# views.py línea 17923
# Crea registro en DteAlertaDescartada
DteAlertaDescartada.objects.get_or_create(
    dte=dte,
    usuario=request.user,
    defaults={'sucursal_id': sucursal_destino_id}
)
```

**¿Cuándo desaparece la alerta?**
- Cuando el usuario la descarta manualmente (crea registro en `DteAlertaDescartada`)
- Cuando el DTE cambia de estado de `'EMITIDO'` a otro (ej: `'RECEPCIONADO_COMPLETO'`)

---

## 🔍 TU PREGUNTA: ¿Cómo vincular con Recepción de DTE?

### Sistema Actual:

```
1. Se emite DTE a sucursal destino
   ↓
2. Aparece en navbar como alerta (se calcula dinámicamente)
   ↓
3. Usuario puede descartar alerta (sin recepcionar)
   ↓
4. Alerta desaparece pero DTE sigue pendiente
   ↓
5. DTE sigue apareciendo en /app/recepcion-dte/ ✅
```

### Problema Identificado:

**El usuario puede descartar la alerta SIN recepcionar el DTE.**

Esto significa:
- ✅ El DTE sigue pendiente (correcto)
- ✅ Aparece en `/app/recepcion-dte/` (correcto)
- ❌ NO aparece en la navbar (el usuario la descartó)
- ⚠️ El usuario puede olvidar recepcionar

---

## 🔧 SOLUCIÓN PROPUESTA

### Opción 1: Eliminar el botón de descartar para DTEs de TRASPASO (Recomendada)

**Justificación:**
- Los traspasos internos SON OBLIGATORIOS de recepcionar
- No tiene sentido "descartar" algo que DEBES recepcionar
- La alerta solo debe desaparecer cuando SE RECEPCIONE

**Implementación:**

**1. Modificar `menu.html` línea ~865:**

```html
<!-- ANTES: -->
<button type="button" class="btn btn-sm btn-ghost-secondary position-absolute end-0 top-50 translate-middle-y me-2" 
        onclick="event.stopPropagation(); descartarNotificacionDTE(${dte.id})" 
        title="Descartar">
    <i class="ri-close-line fs-16"></i>
</button>

<!-- DESPUÉS: Eliminar el botón completamente -->
<!-- Las alertas solo desaparecen al recepcionar -->
```

**2. Cambiar texto de "sin notificaciones":**

```html
<!-- ANTES: -->
<p class="text-muted fs-12 mb-0">No tienes DTEs pendientes de recepcionar</p>

<!-- DESPUÉS: -->
<p class="text-muted fs-12 mb-0">
    ✅ No tienes DTEs pendientes de recepcionar<br>
    <small>Las alertas se eliminan automáticamente al confirmar recepción</small>
</p>
```

---

### Opción 2: Limpiar descartes al recepcionar (Alternativa)

Si quieres **mantener** el botón de descartar pero que la alerta **vuelva a aparecer** si el usuario no recepciona:

**1. Al confirmar recepción (`views.py` línea 322):**

```python
# Agregar después de actualizar el DTE (línea ~569):
# Eliminar todos los descartes de este DTE
DteAlertaDescartada.objects.filter(dte=dte).delete()
```

Esto ya NO es necesario porque cuando el DTE cambia a `'RECEPCIONADO_COMPLETO'`, automáticamente deja de aparecer en la query.

**2. Agregar recordatorio periódico:**

Crear un comando que limpie descartes de DTEs con más de 7 días:

```python
# management/commands/limpiar_descartes_dte_antiguos.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from app.models import DteAlertaDescartada, Dte

class Command(BaseCommand):
    help = 'Limpia descartes de DTEs que aún están pendientes después de 7 días'

    def handle(self, *args, **options):
        limite = timezone.now() - timedelta(days=7)
        
        # Buscar descartes antiguos cuyo DTE sigue EMITIDO
        descartes = DteAlertaDescartada.objects.filter(
            fecha_descartada__lt=limite,
            dte__estado_dte='EMITIDO'
        )
        
        cantidad = descartes.count()
        descartes.delete()
        
        self.stdout.write(
            self.style.SUCCESS(f'✓ {cantidad} descartes antiguos eliminados (DTEs vuelven a alertar)')
        )
```

---

### Opción 3: Sistema mixto (Balance)

**Mantener descartar pero:**
1. Agregar contador de días en la alerta
2. Si pasan más de 7 días, la alerta vuelve a aparecer automáticamente
3. Mostrar mensaje: "Este DTE lleva X días sin recepcionar"

---

## 🎯 RECOMENDACIÓN

### ✅ Opción 1: **Eliminar botón de descartar para traspasos internos**

**Razones:**
1. Los traspasos internos son **operativos obligatorios**
2. Descartar no tiene sentido lógico (el stock YA salió de origen)
3. Fuerza al usuario a recepcionar (buena práctica)
4. Implementación simple (solo quitar botón)
5. La alerta desaparece automáticamente al recepcionar

**Implementación:**
- Quitar botón en `menu.html`
- La query automáticamente deja de mostrar el DTE cuando se recepciona
- El estado cambia de `'EMITIDO'` → `'RECEPCIONADO_COMPLETO'`

---

## 📋 FLUJO ACTUAL vs PROPUESTO

### ACTUAL:
```
1. DTE emitido → Alerta aparece ✅
2. Usuario descarta → Alerta desaparece ❌ (sin recepcionar)
3. DTE sigue EMITIDO → Aparece en recepción ✅
4. Usuario puede olvidar recepcionar ⚠️
```

### PROPUESTO:
```
1. DTE emitido → Alerta aparece ✅
2. Usuario NO puede descartar (sin botón) ✅
3. Usuario DEBE ir a recepción → Alerta visible ✅
4. Usuario recepciona → Estado cambia a RECEPCIONADO ✅
5. Query automática deja de mostrar el DTE → Alerta desaparece ✅
```

---

## 🔄 INTEGRACIÓN CON RECEPCIÓN DE DTE

### Ya está integrado correctamente:

**Navbar Alerta:**
- Muestra DTEs con `estado_dte='EMITIDO'` y `tipo_transaccion='TRASPASO'`
- Se calcula desde `obtener_dtes_pendientes_recibir()` (views.py línea 17802)

**Página Recepción (`/app/recepcion-dte/`):**
- Muestra los MISMOS DTEs (misma query)
- Al confirmar recepción, el estado cambia
- La alerta desaparece automáticamente

**El vínculo ya existe ✅**

---

## 💡 MEJORA ADICIONAL SUGERIDA

Agregar link directo desde la alerta a la recepción:

```html
<!-- En menu.html, cambiar el div por un enlace: -->
<a href="/app/recepcion-dte/?dte_id=${dte.id}" 
   class="text-reset notification-item d-block dropdown-item position-relative">
    <!-- contenido de la notificación -->
</a>
```

Esto permite al usuario hacer click en la alerta y llegar directamente a la recepción de ese DTE específico.

---

## 🎯 RESUMEN DE CAMBIOS PROPUESTOS

### Cambio 1: Eliminar botón descartar (menu.html línea ~865)
```html
<!-- ELIMINAR ESTE CÓDIGO: -->
<button type="button" class="btn btn-sm btn-ghost-secondary position-absolute end-0 top-50 translate-middle-y me-2" 
        onclick="event.stopPropagation(); descartarNotificacionDTE(${dte.id})" 
        title="Descartar">
    <i class="ri-close-line fs-16"></i>
</button>
```

### Cambio 2: Convertir alerta en enlace clicable
```html
<!-- Cambiar el div por: -->
<a href="/app/recepcion-dte/?dte_id=${dte.id}" 
   class="text-reset notification-item d-block dropdown-item position-relative" 
   id="dte-notif-${dte.id}">
    <!-- contenido -->
</a>
```

### Resultado:
- ✅ Usuario NO puede descartar alertas de DTEs pendientes
- ✅ Usuario hace click en alerta → Va directo a recepción
- ✅ Usuario recepciona → Alerta desaparece automáticamente
- ✅ Sistema más robusto y lógico

---

## 📝 ARCHIVOS A MODIFICAR

| Archivo | Línea | Acción |
|---------|-------|--------|
| `app/templates/layout/menu.html` | ~865 | Eliminar botón descartar |
| `app/templates/layout/menu.html` | ~836 | Cambiar div por enlace |
| `app/templates/layout/menu.html` | ~970 | ~~Eliminar función `descartarNotificacionDTE()`~~ Dejar por si se usa en otro lado |

**Opcional:** Si quieres mantener la función de descartar pero solo para ciertos casos, puedo agregar lógica condicional.
