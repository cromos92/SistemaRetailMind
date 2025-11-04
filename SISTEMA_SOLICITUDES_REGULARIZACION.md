# 🔄 Sistema de Solicitudes de Regularización

## 📋 Contexto del Problema

### Situación Actual:
- **RECEPTOR** recibe mercadería con problemas (faltantes, dañados, parciales)
- En `/app/regularizar-recepciones/` solo aparece "Solución nota de crédito"
- **PROBLEMA:** El receptor NO puede emitir documentos hacia el emisor

### Concepto Clave:
```
EMISOR (quien envía) → puede emitir DTEs → RECEPTOR (quien recibe)
RECEPTOR              ✗ NO puede emitir ✗   → EMISOR
```

---

## 🎯 Solución: Sistema de Solicitudes Bidireccional

### Arquitectura del Sistema:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FLUJO DE REGULARIZACIÓN                      │
└─────────────────────────────────────────────────────────────────────┘

1️⃣ RECEPCIÓN CON PROBLEMAS
   RECEPTOR → Detecta problema al recepcionar
   Estado: "EN_REGULARIZACIÓN"
   
2️⃣ SOLICITUD DE SOLUCIÓN
   RECEPTOR → Crea solicitud de regularización
   Opciones:
   ├─ A) Nota de Crédito (devolver dinero)
   ├─ B) Reenvío de faltantes
   └─ C) Cambio por otro producto
   
3️⃣ NOTIFICACIÓN AL EMISOR
   Sistema → Notifica al EMISOR sobre la solicitud
   Estado: "PENDIENTE_APROBACION_EMISOR"
   
4️⃣ REVISIÓN Y DECISIÓN
   EMISOR → Revisa solicitud en su panel
   Opciones:
   ├─ Aprobar y ejecutar
   ├─ Rechazar (con motivo)
   └─ Proponer alternativa
   
5️⃣ EJECUCIÓN
   EMISOR → Ejecuta la solución aprobada
   └─ Emite NC / Nuevo DTE / DTE de cambio
   
6️⃣ CONFIRMACIÓN
   RECEPTOR → Confirma recepción de la solución
   Estado: "REGULARIZADO"
```

---

## 🗃️ Modelo de Datos

### Nueva Tabla: `Solicitud_Regularizacion`

```python
class Solicitud_Regularizacion(models.Model):
    """
    Solicitud creada por el RECEPTOR para regularizar productos con problemas
    """
    
    # Identificación
    numero_solicitud = models.CharField(max_length=20, unique=True)
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    
    # Relaciones
    dte_original = models.ForeignKey(Dte, on_delete=models.CASCADE, 
                                     related_name='solicitudes_regularizacion')
    producto_recepcionado = models.ForeignKey(Productos_Recepcionados, 
                                              on_delete=models.CASCADE)
    
    # Partes involucradas
    sucursal_solicitante = models.ForeignKey(Sucursal, on_delete=models.CASCADE,
                                             related_name='solicitudes_enviadas')  # RECEPTOR
    sucursal_emisora = models.ForeignKey(Sucursal, on_delete=models.CASCADE,
                                         related_name='solicitudes_recibidas')  # EMISOR
    usuario_solicita = models.CharField(max_length=100)
    
    # Problema detectado
    tipo_problema = models.CharField(max_length=50, choices=[
        ('FALTANTE', 'Faltante'),
        ('DANADO', 'Dañado'),
        ('PARCIAL', 'Recepción Parcial'),
        ('INCORRECTO', 'Producto Incorrecto')
    ])
    cantidad_problema = models.IntegerField()
    descripcion_problema = models.TextField()
    evidencia_foto = models.ImageField(upload_to='evidencias_problemas/', 
                                       null=True, blank=True)
    
    # Solución solicitada por RECEPTOR
    tipo_solucion_solicitada = models.CharField(max_length=50, choices=[
        ('NOTA_CREDITO', 'Nota de Crédito'),
        ('REENVIO', 'Reenvío del mismo producto'),
        ('CAMBIO_PRODUCTO', 'Cambio por otro producto'),
        ('AJUSTE_CANTIDAD', 'Ajustar solo cantidad')
    ])
    
    # Para caso de CAMBIO_PRODUCTO
    producto_cambio_solicitado = models.ForeignKey(ProductoTalla, 
                                                   on_delete=models.SET_NULL,
                                                   null=True, blank=True,
                                                   related_name='solicitudes_como_reemplazo')
    cantidad_cambio_solicitada = models.IntegerField(null=True, blank=True)
    
    # Respuesta del EMISOR
    estado = models.CharField(max_length=50, choices=[
        ('PENDIENTE', 'Pendiente de Revisión'),
        ('EN_REVISION', 'En Revisión por Emisor'),
        ('APROBADA', 'Aprobada'),
        ('RECHAZADA', 'Rechazada'),
        ('EJECUTADA', 'Solución Ejecutada'),
        ('COMPLETADA', 'Completada y Confirmada')
    ], default='PENDIENTE')
    
    fecha_revision = models.DateTimeField(null=True, blank=True)
    usuario_revisa = models.CharField(max_length=100, null=True, blank=True)
    decision_emisor = models.TextField(null=True, blank=True)
    
    # Solución alternativa propuesta por EMISOR
    tipo_solucion_aprobada = models.CharField(max_length=50, null=True, blank=True)
    producto_cambio_aprobado = models.ForeignKey(ProductoTalla,
                                                 on_delete=models.SET_NULL,
                                                 null=True, blank=True,
                                                 related_name='solicitudes_aprobadas')
    cantidad_cambio_aprobada = models.IntegerField(null=True, blank=True)
    
    # Ejecución
    fecha_ejecucion = models.DateTimeField(null=True, blank=True)
    dte_solucion = models.ForeignKey(Dte, on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name='es_solucion_de')  # NC o nuevo DTE
    
    # Confirmación del RECEPTOR
    fecha_confirmacion = models.DateTimeField(null=True, blank=True)
    usuario_confirma = models.CharField(max_length=100, null=True, blank=True)
    conformidad = models.BooleanField(null=True, blank=True)
    observaciones_finales = models.TextField(null=True, blank=True)
    
    class Meta:
        db_table = 'solicitudes_regularizacion'
        ordering = ['-fecha_solicitud']
```

---

## 🎨 Interfaces de Usuario

### 1️⃣ Panel del RECEPTOR (quien recibe)

#### Vista: `/app/regularizar-recepciones/`

```html
┌─────────────────────────────────────────────────────────────┐
│ 📦 Productos con Problemas                                  │
├─────────────────────────────────────────────────────────────┤
│ DTE #1234 - Sucursal Origen: EDEL                          │
│                                                             │
│ Producto: Zapatilla Nike Talla 42                          │
│ Esperado: 10 | Recepcionado: 7 | Faltante: 3              │
│                                                             │
│ [🔧 Solicitar Solución]                                     │
└─────────────────────────────────────────────────────────────┘
```

#### Modal: Solicitar Solución

```html
┌─────────────────────────────────────────────────────────────┐
│ 📝 Solicitud de Regularización                              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Producto: Zapatilla Nike Talla 42                          │
│ Problema: Faltante - 3 unidades                            │
│                                                             │
│ ¿Qué solución solicitas?                                   │
│                                                             │
│ ○ Nota de Crédito                                          │
│   └─ Monto a devolver: $59.970                            │
│                                                             │
│ ○ Reenvío de faltantes                                     │
│   └─ Solicitar reenvío de 3 unidades del mismo producto   │
│                                                             │
│ ○ Cambio por otro producto                                 │
│   └─ [Buscar producto...] 🔍                               │
│      Producto seleccionado: Zapatilla Adidas T42          │
│      Cantidad: [3] ▼                                       │
│      Stock disponible en EDEL: 15 unidades                │
│                                                             │
│ Descripción del problema: (opcional)                        │
│ ┌─────────────────────────────────────────────────┐        │
│ │ [Texto libre para detallar el problema]         │        │
│ └─────────────────────────────────────────────────┘        │
│                                                             │
│ Adjuntar foto (opcional):                                  │
│ [📷 Subir imagen]                                          │
│                                                             │
│                    [Cancelar] [Enviar Solicitud]           │
└─────────────────────────────────────────────────────────────┘
```

#### Vista: Mis Solicitudes

```html
┌─────────────────────────────────────────────────────────────┐
│ 📋 Mis Solicitudes de Regularización                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ #SOL-001 | DTE #1234                                       │
│ Zapatilla Nike T42 - 3 faltantes                           │
│ Solución: Cambio por Adidas T42                            │
│ Estado: [🟡 Pendiente Revisión]                            │
│ Enviado: 3 Nov 2024                                        │
│                                                             │
│ #SOL-002 | DTE #1235                                       │
│ Polera Básica M - 5 dañadas                                │
│ Solución: Nota de Crédito                                  │
│ Estado: [🟢 Aprobada - Ejecutada]                          │
│ NC #45 emitida                                             │
│ [✅ Confirmar Recepción]                                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

### 2️⃣ Panel del EMISOR (quien envió)

#### Vista: `/app/solicitudes-regularizacion-recibidas/` (NUEVA)

```html
┌─────────────────────────────────────────────────────────────┐
│ 📬 Solicitudes de Regularización Recibidas                  │
├─────────────────────────────────────────────────────────────┤
│ Filtros: [Pendientes ▼] [NICK1 ▼] [Última semana ▼]       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ 🔴 #SOL-001 - NICK1 solicita                               │
│ DTE #1234 emitido el 1 Nov                                 │
│ Problema: 3 Zapatillas Nike T42 faltantes                  │
│ Solicita: Cambio por Adidas T42 (Stock: 15 ✓)             │
│ [Ver Detalle] [Revisar]                                    │
│                                                             │
│ 🟡 #SOL-003 - BODEGA2 solicita                             │
│ DTE #1240 emitido el 2 Nov                                 │
│ Problema: 10 Poleras M dañadas                             │
│ Solicita: Nota de Crédito $49.990                          │
│ [Ver Detalle] [Revisar]                                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### Modal: Revisar Solicitud

```html
┌─────────────────────────────────────────────────────────────┐
│ 🔍 Revisar Solicitud #SOL-001                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ 📦 Información del Problema                                 │
│ ───────────────────────────────────────────────────────    │
│ DTE Original: #1234                                        │
│ Fecha Emisión: 1 Nov 2024                                  │
│ Destino: NICK1                                             │
│                                                             │
│ Producto: Zapatilla Nike Air T42                           │
│ Esperado: 10 | Recepcionado: 7 | Faltante: 3              │
│                                                             │
│ Evidencia: [📷 Ver foto]                                   │
│ Descripción: "Caja llegó abierta, faltaban 3 unidades"    │
│                                                             │
│ ───────────────────────────────────────────────────────    │
│ 💡 Solución Solicitada                                      │
│ ───────────────────────────────────────────────────────    │
│ Tipo: Cambio por otro producto                             │
│ Producto solicitado: Zapatilla Adidas Stan T42            │
│ Cantidad: 3 unidades                                       │
│ Stock actual: 15 ✅                                         │
│                                                             │
│ ───────────────────────────────────────────────────────    │
│ 🎯 Tu Decisión                                              │
│ ───────────────────────────────────────────────────────    │
│                                                             │
│ ○ Aprobar solución solicitada                              │
│   └─ Se enviará DTE de cambio: 3x Adidas Stan T42         │
│      Valor del cambio: $59.970                             │
│                                                             │
│ ○ Aprobar con modificación                                 │
│   └─ Producto alternativo: [Buscar...] 🔍                  │
│      Cantidad: [___] ▼                                     │
│                                                             │
│ ○ Proponer Nota de Crédito en su lugar                    │
│   └─ Monto: $59.970                                        │
│                                                             │
│ ○ Rechazar solicitud                                       │
│   └─ Motivo: [Texto obligatorio]                           │
│                                                             │
│ Observaciones internas:                                    │
│ ┌─────────────────────────────────────────────────┐        │
│ │                                                  │        │
│ └─────────────────────────────────────────────────┘        │
│                                                             │
│                    [Cancelar] [Enviar Decisión]            │
└─────────────────────────────────────────────────────────────┘
```

#### Vista: Ejecutar Solución Aprobada

```html
┌─────────────────────────────────────────────────────────────┐
│ ✅ Ejecutar Solución #SOL-001                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Estado: Aprobada - Pendiente de Ejecución                  │
│                                                             │
│ Solución: Cambio de producto                                │
│ ├─ Producto original: Nike Air T42 (3 uds)                 │
│ └─ Producto reemplazo: Adidas Stan T42 (3 uds)            │
│                                                             │
│ 📄 Se emitirá:                                              │
│ ├─ Nota de Crédito #NC-123 por producto original          │
│ │   Monto: $59.970                                         │
│ └─ DTE Interno #1245 con producto de cambio               │
│     Destino: NICK1                                         │
│     Detalle: 3x Adidas Stan T42                            │
│                                                             │
│ [🚀 Ejecutar y Emitir Documentos]                          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔧 Flujos Específicos por Tipo de Solución

### A) NOTA DE CRÉDITO

```
RECEPTOR:
1. Solicita NC por $X
2. Espera aprobación

EMISOR:
3. Revisa y aprueba
4. Emite NC desde sistema SII
5. NC queda asociada al DTE original
6. Estado: EJECUTADA

RECEPTOR:
7. Ve NC en su sistema
8. Confirma recepción
9. Estado: COMPLETADA
```

### B) REENVÍO DE FALTANTES

```
RECEPTOR:
1. Solicita reenvío de 5 unidades del mismo producto
2. Espera aprobación

EMISOR:
3. Revisa stock disponible
4. Aprueba
5. Emite NUEVO DTE interno con las 5 unidades
6. DTE queda vinculado a la solicitud
7. Despacha mercadería

RECEPTOR:
8. Recibe nuevo DTE en /app/recepcion-dte/
9. Recepciona normalmente
10. Sistema auto-confirma la solicitud
11. Estado: COMPLETADA
```

### C) CAMBIO POR OTRO PRODUCTO 🌟

**Este es el más complejo**

```
RECEPTOR (NICK1):
1. Solicita cambio: 
   - Tenía que recibir: 10x Nike Air T42
   - Recibió: 7x Nike Air T42 (faltan 3)
   - Solicita cambio por: 3x Adidas Stan T42
   - Busca en catálogo y selecciona producto destino
   - Sistema verifica stock en EMISOR

SISTEMA:
2. Valida que producto solicitado exista en inventario de EMISOR
3. Muestra stock disponible

EMISOR (EDEL):
4. Revisa solicitud
5. Ve que tiene stock de Adidas Stan T42
6. Aprueba cambio
7. Sistema prepara DOS documentos:
   a) NC por producto original (3x Nike Air T42)
   b) Nuevo DTE con producto de cambio (3x Adidas Stan T42)

8. EMISOR ejecuta la solución:
   - Emite NC #NC-123
   - Emite DTE interno #1245 hacia NICK1
   - Stock EDEL se reduce en 3x Adidas Stan T42
   - Despacha mercadería

RECEPTOR (NICK1):
9. Recibe notificación de solución ejecutada
10. Ve NC emitida en su favor
11. Ve nuevo DTE #1245 en /app/recepcion-dte/
12. Recepciona el DTE con las Adidas Stan T42
13. Stock NICK1 aumenta en 3x Adidas Stan T42
14. Sistema auto-confirma la solicitud
15. Estado: COMPLETADA
```

**Ventaja:** El receptor NO necesita "ingresar" el producto manualmente, 
llega como un DTE normal que recepciona con el flujo estándar.

---

## 📊 Estados de la Solicitud

```
PENDIENTE 
  ↓
EN_REVISION (Emisor está revisando)
  ↓
  ├─→ RECHAZADA (fin del flujo, receptor puede crear nueva)
  │
  └─→ APROBADA
        ↓
      EJECUTADA (documentos emitidos, mercadería despachada)
        ↓
      COMPLETADA (receptor confirmó recepción)
```

---

## 🔔 Sistema de Notificaciones

### Para RECEPTOR:
- ✅ "Tu solicitud #SOL-001 fue aprobada"
- ❌ "Tu solicitud #SOL-002 fue rechazada: [motivo]"
- 📦 "Solución ejecutada: Recibirás DTE #1245"
- 💰 "NC #NC-123 emitida a tu favor"

### Para EMISOR:
- 🔔 "Nueva solicitud de NICK1: cambio de producto"
- ⏰ "Tienes 3 solicitudes pendientes de revisión"
- ✅ "Solicitud #SOL-001 confirmada por receptor"

---

## 🎯 Ventajas del Sistema

1. **Trazabilidad Completa**
   - Cada problema tiene una solicitud formal
   - Historial de decisiones
   - Evidencias fotográficas

2. **Respeta Jerarquía**
   - Receptor SOLICITA
   - Emisor APRUEBA y EJECUTA
   - Receptor CONFIRMA

3. **Flexible**
   - Emisor puede proponer alternativas
   - Negociación posible

4. **Automatizado**
   - Auto-vinculación de documentos
   - Auto-confirmación cuando aplica
   - Estados automáticos

5. **Auditable**
   - Registro de fechas y usuarios
   - Observaciones en cada paso
   - Documentos vinculados

---

## 📝 Próximos Pasos de Implementación

### Fase 1: Base de Datos
1. Crear modelo `Solicitud_Regularizacion`
2. Migrar base de datos
3. Crear relaciones con DTEs y productos

### Fase 2: Panel RECEPTOR
1. Botón "Solicitar Solución" en regularizar-recepciones
2. Modal con opciones de solución
3. Selector de productos para cambio
4. Vista "Mis Solicitudes"

### Fase 3: Panel EMISOR
1. Vista nueva: solicitudes-regularizacion-recibidas
2. Modal de revisión
3. Sistema de aprobación/rechazo
4. Ejecución automática de documentos

### Fase 4: Automatizaciones
1. Vinculación automática NC → Solicitud
2. Vinculación DTE nuevo → Solicitud
3. Auto-confirmación al recepcionar
4. Notificaciones por email/sistema

### Fase 5: Reportes
1. Dashboard de solicitudes
2. Métricas de tiempo de resolución
3. Análisis de productos problemáticos
4. KPIs de servicio entre sucursales

---

## 💡 Caso de Uso Completo

**Escenario:** EDEL envía 10 Nike Air T42 a NICK1, pero solo llegan 7.

1. **NICK1 recepciona:**
   - Marca: Esperado 10, Recepcionado 7, Faltante 3
   - Stock NICK1 aumenta en 7 (no 10)

2. **NICK1 en Regularizar Recepciones:**
   - Ve el producto con problema
   - Click "Solicitar Solución"
   - Elige "Cambio por otro producto"
   - Busca "Adidas Stan T42"
   - Ve que EDEL tiene 15 en stock ✅
   - Solicita 3 unidades
   - Describe: "Prefiero Adidas porque tengo más demanda"
   - Envía solicitud #SOL-001

3. **EDEL recibe notificación:**
   - Email: "NICK1 solicita cambio de producto"
   - Entra a panel de solicitudes recibidas
   - Revisa #SOL-001
   - Ve que tiene 15 Adidas Stan T42
   - Ve foto de la caja dañada
   - Aprueba el cambio

4. **EDEL ejecuta solución:**
   - Click "Ejecutar Solución"
   - Sistema genera:
     * NC #NC-456 por 3x Nike Air T42 ($59.970)
     * DTE #1250 con 3x Adidas Stan T42 hacia NICK1
   - Stock EDEL disminuye: Adidas Stan T42: 15 → 12
   - Despacha las 3 Adidas a NICK1

5. **NICK1 recibe:**
   - Ve notificación: "Solución ejecutada"
   - Ve NC #NC-456 en su favor
   - Ve DTE #1250 en recepciones pendientes
   - Recepciona DTE #1250 normalmente
   - Stock NICK1 aumenta: Adidas Stan T42 +3
   - Sistema auto-confirma solicitud #SOL-001
   - Estado: COMPLETADA ✅

**Resultado:**
- NICK1 tiene el producto que necesita
- EDEL mantuvo buena relación con NICK1
- Todo documentado y trazable
- Stock correcto en ambas sucursales

---

## 🎨 Wireframes Adicionales

### Búsqueda de Producto para Cambio

```html
┌─────────────────────────────────────────────────────────────┐
│ 🔍 Buscar Producto de Reemplazo                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ [Buscar por SKU, nombre, código...] 🔍                     │
│                                                             │
│ Filtros: [Zapatillas ▼] [Talla: 42 ▼] [Marca: Todas ▼]   │
│                                                             │
│ ┌─────────────────────────────────────────────────────┐    │
│ │ □ Adidas Stan Smith T42                            │    │
│ │   SKU: ADS-ST-42 | Stock en EDEL: 15 ✅           │    │
│ │   Precio: $19.990                                   │    │
│ ├─────────────────────────────────────────────────────┤    │
│ │ □ Puma Suede T42                                    │    │
│ │   SKU: PUM-SU-42 | Stock en EDEL: 3 ⚠️            │    │
│ │   Precio: $22.990                                   │    │
│ ├─────────────────────────────────────────────────────┤    │
│ │ □ Reebok Classic T42                                │    │
│ │   SKU: RBK-CL-42 | Stock en EDEL: 0 ❌             │    │
│ │   Precio: $18.990 (Sin stock)                       │    │
│ └─────────────────────────────────────────────────────┘    │
│                                                             │
│                    [Cancelar] [Seleccionar]                │
└─────────────────────────────────────────────────────────────┘
```

---

¿Quieres que empiece a implementar este sistema? 🚀

