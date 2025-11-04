# 🔄 Plan de Integración: Sistema de Solicitudes

## 📊 Análisis del Sistema Actual

### ✅ Ya Implementado:

1. **Modelo `Productos_Recepcionados`**
   - Registra cada producto al recepcionar
   - Estados: `RECEPCIONADO_OK`, `RECEPCIONADO_PARCIAL`, `RECEPCIONADO_DANADO`, `FALTANTE`, `EN_REGULARIZACION`, `REGULARIZADO`
   - Campos: cantidad_esperada, stockArribado, cantidad_danada, cantidad_faltante

2. **Vista `/app/regularizar-recepciones/`**
   - Lista productos con problemas
   - Filtros por estado, proveedor, búsqueda
   - Muestra DTE, producto, SKU, talla, cantidades, estado

3. **Modal de Regularización**
   - 3 tipos de regularización:
     - **AJUSTAR**: Cambia cantidad (genera NC automática si aplica)
     - **CAMBIAR_TALLA**: Cambia a otra talla del mismo producto
     - **CAMBIAR_PRODUCTO**: Cambia por otro producto diferente
   
4. **Endpoint `regularizar_producto_api`**
   - Procesa las 3 tipos de regularización
   - Ajusta stock automáticamente
   - Genera NC automática cuando `requiere_nota_credito_check()`
   - Actualiza estado del DTE si todo está regularizado

### ❌ Problema Identificado:

```python
# Código actual en regularizar_producto_api (línea 805-840)
elif tipo_regularizacion == 'CAMBIAR_PRODUCTO':
    # El RECEPTOR cambia el producto directamente
    nuevo_producto_talla.stock += cantidad  # ❌ Ingresa stock sin documento
    nuevo_producto_talla.save()
    
    # Crea movimiento pero NO hay DTE de respaldo
    Movimientos_Producto.objects.create(
        concepto='REGULARIZACION_CAMBIO_PRODUCTO',  # ❌ No es un traspaso formal
        tipo_movimiento='INGRESO',
        ...
    )
```

**Problemas:**
1. El receptor ingresa stock sin documento formal
2. No pasa por el emisor para aprobación
3. No genera NC por el producto original
4. No genera nuevo DTE con el producto de cambio
5. El emisor no sabe que debe enviar el producto de cambio
6. No hay flujo de comunicación entre sucursales

---

## 🎯 Solución: Sistema Híbrido

### Flujo según Tipo de Traspaso:

```
┌─────────────────────────────────────────────────────────────┐
│           TRASPASO INTERNO (Misma Empresa)                  │
├─────────────────────────────────────────────────────────────┤
│ Regularización DIRECTA (sistema actual)                     │
│ ✅ AJUSTAR, CAMBIAR_TALLA, CAMBIAR_PRODUCTO                │
│ ✅ Stock se ajusta inmediatamente                           │
│ ✅ Sin solicitudes (confianza interna)                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│           TRASPASO ENTRE EMPRESAS                            │
├─────────────────────────────────────────────────────────────┤
│ Regularización por SOLICITUD (nuevo sistema)                │
│ 1️⃣ Receptor crea SOLICITUD                                  │
│ 2️⃣ Emisor REVISA y APRUEBA                                  │
│ 3️⃣ Emisor EJECUTA (emite NC + nuevo DTE)                   │
│ 4️⃣ Receptor RECEPCIONA normalmente                          │
│ 5️⃣ Sistema AUTO-CONFIRMA solicitud                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔧 Cambios Necesarios

### 1. Actualizar Modal de Regularización

**En `regularizar_recepciones.html` línea ~200:**

```javascript
function abrirModalRegularizar(productoId) {
    // ... código actual ...
    
    // NUEVO: Verificar si requiere solicitud
    const requiereSolicitud = productoRecepcionado.dte.requiere_nota_credito;
    
    if (requiereSolicitud && tipoRegularizacion === 'CAMBIAR_PRODUCTO') {
        // Mostrar opción de "SOLICITAR CAMBIO" en lugar de cambio directo
        mostrarOpcionSolicitud();
    } else {
        // Flujo actual (traspaso interno)
        mostrarOpcionDirecta();
    }
}
```

**UI Actualizada:**

```html
<!-- Panel Cambiar Producto MODIFICADO -->
<div id="panelCambiarProducto" class="regularizacion-panel">
    <div class="card bg-light">
        <div class="card-body">
            <!-- NUEVO: Verificación de tipo -->
            <div id="infoTipoRegularizacion" class="alert alert-info mb-3">
                <!-- Se llena dinámicamente -->
            </div>
            
            <!-- Opción A: Traspaso Interno (mismo comportamiento actual) -->
            <div id="opcionCambioDirecto" style="display:none;">
                <h6 class="card-title">Cambiar por otro Producto</h6>
                <p class="small text-muted">El proveedor envió un producto diferente</p>
                <!-- ... código actual de búsqueda y selección ... -->
            </div>
            
            <!-- Opción B: Entre Empresas (NUEVO) -->
            <div id="opcionSolicitarCambio" style="display:none;">
                <h6 class="card-title">
                    📨 Solicitar Cambio de Producto
                </h6>
                <div class="alert alert-warning">
                    <i class="bi bi-info-circle me-2"></i>
                    <strong>Este es un traspaso entre empresas diferentes.</strong><br>
                    Debes crear una SOLICITUD que el emisor deberá aprobar.
                </div>
                
                <p class="small">
                    El emisor (<strong id="nombreEmisor">-</strong>) revisará tu solicitud y:
                </p>
                <ul class="small">
                    <li>Aprobará y enviará el producto solicitado</li>
                    <li>Emitirá una Nota de Crédito por el producto original</li>
                    <li>Emitirá un nuevo DTE con el producto de cambio</li>
                </ul>
                
                <!-- Búsqueda de producto (mismo UI pero diferente flujo) -->
                <div class="mb-3">
                    <label class="form-label">Buscar Producto Disponible en Emisor</label>
                    <div class="input-group">
                        <input type="text" class="form-control" id="buscarProductoSolicitud" 
                               placeholder="SKU, nombre...">
                        <button class="btn btn-primary" onclick="buscarProductosEmisor()">
                            <i class="bi bi-search"></i> Buscar
                        </button>
                    </div>
                    <small class="text-info">
                        <i class="bi bi-lightbulb"></i> 
                        Solo se mostrarán productos con stock en <strong id="sucursalEmisor">-</strong>
                    </small>
                </div>
                
                <div id="resultadosProductosEmisor" style="max-height: 250px; overflow-y: auto;">
                    <!-- Resultados de búsqueda -->
                </div>
                
                <!-- Producto seleccionado -->
                <div id="productoSolicitudSeleccionado" class="mt-3"></div>
                
                <!-- Justificación -->
                <div class="mt-3">
                    <label class="form-label">
                        <i class="bi bi-chat-left-text me-1"></i>
                        Justificación de la solicitud <span class="text-danger">*</span>
                    </label>
                    <textarea class="form-control" id="justificacionSolicitud" rows="3"
                              placeholder="Ej: El producto original llegó dañado. Prefiero este modelo porque tenemos más demanda..."></textarea>
                    <small class="text-muted">Explica por qué solicitas este cambio</small>
                </div>
                
                <!-- Evidencia (opcional) -->
                <div class="mt-3">
                    <label class="form-label">
                        <i class="bi bi-camera me-1"></i>
                        Adjuntar evidencia (opcional)
                    </label>
                    <input type="file" class="form-control" id="evidenciaFoto" accept="image/*">
                    <small class="text-muted">Foto del producto dañado, caja, etc.</small>
                </div>
            </div>
        </div>
    </div>
</div>
```

### 2. Actualizar Endpoint `regularizar_producto_api`

**En `views.py` línea ~805:**

```python
elif tipo_regularizacion == 'CAMBIAR_PRODUCTO':
    # Verificar si requiere solicitud
    requiere_solicitud = recepcion.dte.requiere_nota_credito_check()
    
    if requiere_solicitud:
        # NUEVO FLUJO: Crear solicitud en lugar de cambio directo
        from .models import Solicitud_Regularizacion
        
        nuevo_producto_id = data.get('nuevo_producto_id')
        justificacion = data.get('justificacion', '')
        evidencia = data.get('evidencia_foto', None)
        
        if not nuevo_producto_id or not justificacion:
            return JsonResponse({
                'success': False,
                'error': 'Debe seleccionar el producto y justificar la solicitud'
            }, status=400)
        
        nuevo_producto_talla = get_object_or_404(Producto_Talla, id=nuevo_producto_id)
        
        # Crear solicitud
        solicitud = Solicitud_Regularizacion.objects.create(
            numero_solicitud=generar_numero_solicitud(),
            dte_original=recepcion.dte,
            producto_recepcionado=recepcion,
            sucursal_solicitante=recepcion.dte.receptor,  # Quien recibe
            sucursal_emisora=recepcion.dte.emisor,  # Quien envió
            usuario_solicita=usuario,
            tipo_problema='INCORRECTO' if recepcion.stockArribado > 0 else 'FALTANTE',
            cantidad_problema=recepcion.cantidad_faltante or recepcion.cantidad_esperada,
            descripcion_problema=justificacion,
            evidencia_foto=evidencia,
            tipo_solucion_solicitada='CAMBIO_PRODUCTO',
            producto_cambio_solicitado=nuevo_producto_talla,
            cantidad_cambio_solicitada=recepcion.stockArribado or recepcion.cantidad_esperada,
            estado='PENDIENTE'
        )
        
        # Actualizar estado del producto recepcionado
        recepcion.estado = 'EN_SOLICITUD_REGULARIZACION'
        recepcion.observaciones = (recepcion.observaciones or '') + f"\n[{hoy.strftime('%Y-%m-%d %H:%M')}] Solicitud #{solicitud.numero_solicitud} creada - Cambio por {nuevo_producto_talla.sku}"
        recepcion.save()
        
        # Notificar al emisor
        notificar_nueva_solicitud(solicitud)
        
        return JsonResponse({
            'success': True,
            'message': f'Solicitud #{solicitud.numero_solicitud} creada correctamente',
            'tipo': 'SOLICITUD_CREADA',
            'numero_solicitud': solicitud.numero_solicitud,
            'requiere_aprobacion': True
        })
    
    else:
        # FLUJO ACTUAL (traspaso interno - mismo código que ya existe)
        nuevo_producto_id = data.get('nuevo_producto_id')
        nuevo_producto_talla = get_object_or_404(Producto_Talla, id=nuevo_producto_id)
        cantidad = recepcion.stockArribado or recepcion.cantidad_esperada
        
        # Ingresar el nuevo producto (SOLO para traspasos internos)
        nuevo_producto_talla.stock += cantidad
        nuevo_producto_talla.save()
        
        # ... resto del código actual ...
```

### 3. Nueva Vista: Panel del Emisor

**Nueva URL en `urls.py`:**

```python
path('solicitudes-regularizacion/', views.solicitudes_regularizacion_emisor, name='solicitudes_regularizacion_emisor'),
path('dte/revisar_solicitud/<int:solicitud_id>/', views.revisar_solicitud_api, name='revisar_solicitud_api'),
path('dte/ejecutar_solucion/<int:solicitud_id>/', views.ejecutar_solucion_api, name='ejecutar_solucion_api'),
```

**Nueva vista en `views.py`:**

```python
@login_required
def solicitudes_regularizacion_emisor(request):
    """Panel para que el EMISOR revise solicitudes recibidas"""
    return render(request, 'vistas/modulo_compras/solicitudes_recibidas.html')

@login_required
@require_GET
def obtener_solicitudes_recibidas(request):
    """API para obtener solicitudes recibidas por el emisor"""
    sucursal_id = request.session.get('idSucursalActual')
    
    solicitudes = Solicitud_Regularizacion.objects.filter(
        sucursal_emisora_id=sucursal_id,
        estado__in=['PENDIENTE', 'EN_REVISION']
    ).select_related(
        'dte_original',
        'producto_recepcionado',
        'sucursal_solicitante',
        'producto_cambio_solicitado__producto'
    ).order_by('-fecha_solicitud')
    
    items = []
    for sol in solicitudes:
        items.append({
            'id': sol.id,
            'numero_solicitud': sol.numero_solicitud,
            'fecha': sol.fecha_solicitud.strftime('%Y-%m-%d %H:%M'),
            'dte_numero': sol.dte_original.numero_documento,
            'sucursal_solicita': sol.sucursal_solicitante.alias,
            'producto_original': sol.producto_recepcionado.producto_talla.sku,
            'producto_solicitado': sol.producto_cambio_solicitado.sku if sol.producto_cambio_solicitado else None,
            'cantidad': sol.cantidad_cambio_solicitada,
            'stock_disponible': sol.producto_cambio_solicitado.stock if sol.producto_cambio_solicitado else 0,
            'descripcion': sol.descripcion_problema,
            'evidencia': sol.evidencia_foto.url if sol.evidencia_foto else None,
            'estado': sol.estado
        })
    
    return JsonResponse({'success': True, 'items': items})

@login_required
@require_POST
@transaction.atomic
def aprobar_solicitud_api(request, solicitud_id):
    """El EMISOR aprueba una solicitud"""
    try:
        solicitud = get_object_or_404(Solicitud_Regularizacion, id=solicitud_id)
        data = json.loads(request.body or '{}')
        
        decision = data.get('decision')  # 'APROBAR', 'RECHAZAR', 'MODIFICAR'
        observaciones = data.get('observaciones', '')
        
        if decision == 'APROBAR':
            solicitud.estado = 'APROBADA'
            solicitud.fecha_revision = timezone.now()
            solicitud.usuario_revisa = request.user.username
            solicitud.decision_emisor = observaciones
            solicitud.tipo_solucion_aprobada = solicitud.tipo_solucion_solicitada
            solicitud.producto_cambio_aprobado = solicitud.producto_cambio_solicitado
            solicitud.cantidad_cambio_aprobada = solicitud.cantidad_cambio_solicitada
            solicitud.save()
            
            # Notificar al receptor
            notificar_solicitud_aprobada(solicitud)
            
            mensaje = f'Solicitud #{solicitud.numero_solicitud} aprobada'
        
        elif decision == 'RECHAZAR':
            if not observaciones:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe especificar el motivo del rechazo'
                }, status=400)
            
            solicitud.estado = 'RECHAZADA'
            solicitud.fecha_revision = timezone.now()
            solicitud.usuario_revisa = request.user.username
            solicitud.decision_emisor = observaciones
            solicitud.save()
            
            # Actualizar producto recepcionado
            solicitud.producto_recepcionado.estado = 'EN_REGULARIZACION'
            solicitud.producto_recepcionado.save()
            
            mensaje = f'Solicitud #{solicitud.numero_solicitud} rechazada'
        
        return JsonResponse({
            'success': True,
            'message': mensaje,
            'estado_nuevo': solicitud.estado
        })
    
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
@require_POST
@transaction.atomic
def ejecutar_solucion_api(request, solicitud_id):
    """El EMISOR ejecuta la solución aprobada (emite NC + nuevo DTE)"""
    try:
        solicitud = get_object_or_404(Solicitud_Regularizacion, id=solicitud_id)
        
        if solicitud.estado != 'APROBADA':
            return JsonResponse({
                'success': False,
                'error': 'La solicitud no está aprobada'
            }, status=400)
        
        with transaction.atomic():
            # 1. Generar Nota de Crédito por producto original
            from .utils import generar_nota_credito_automatica
            
            productos_afectados = [{
                'dte_producto_id': solicitud.producto_recepcionado.dte_producto.id,
                'cantidad_faltante': solicitud.cantidad_problema,
                'observaciones': f"Regularización Sol #{solicitud.numero_solicitud}"
            }]
            
            nc = generar_nota_credito_automatica(
                dte_original=solicitud.dte_original,
                productos_afectados=productos_afectados,
                usuario=request.user.username,
                motivo=f"Cambio de producto - Solicitud #{solicitud.numero_solicitud}"
            )
            
            # 2. Generar nuevo DTE con producto de cambio
            nuevo_dte = Dte.objects.create(
                numero_documento=generar_numero_dte(),
                tipo_documento='GUIA',
                tipo_transaccion='TRASPASO',
                emisor=solicitud.sucursal_emisora.empresa,
                receptor=solicitud.sucursal_solicitante.empresa,
                sucursal=solicitud.sucursal_emisora,
                fecha_emision=timezone.now().date(),
                estado_dte='EMITIDO',
                referencias=f"Solución a solicitud #{solicitud.numero_solicitud} - Cambio de producto"
            )
            
            # Agregar producto de cambio al DTE
            Dte_Productos.objects.create(
                dte=nuevo_dte,
                productoTalla=solicitud.producto_cambio_aprobado,
                descripcion=solicitud.producto_cambio_aprobado.producto.articulo,
                stock=solicitud.cantidad_cambio_aprobada,
                precio=solicitud.producto_cambio_aprobado.producto.precioventa,
                costo=solicitud.producto_cambio_aprobado.producto.costo
            )
            
            # Reducir stock en emisor
            solicitud.producto_cambio_aprobado.stock -= solicitud.cantidad_cambio_aprobada
            solicitud.producto_cambio_aprobado.save()
            
            # Crear movimiento de egreso
            Movimientos_Producto.objects.create(
                dte=nuevo_dte,
                ProductoTalla=solicitud.producto_cambio_aprobado,
                sucursal_origen=solicitud.sucursal_emisora,
                sucursal_destino=solicitud.sucursal_solicitante,
                cantidad=-solicitud.cantidad_cambio_aprobada,
                concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO',
                estado='PENDIENTE_RECEPCION',
                responsable=request.user.username,
                observaciones=f"Cambio por solicitud #{solicitud.numero_solicitud}"
            )
            
            # 3. Actualizar solicitud
            solicitud.estado = 'EJECUTADA'
            solicitud.fecha_ejecucion = timezone.now()
            solicitud.dte_solucion = nuevo_dte
            solicitud.save()
            
            # Notificar al receptor
            notificar_solucion_ejecutada(solicitud)
        
        return JsonResponse({
            'success': True,
            'message': 'Solución ejecutada correctamente',
            'nc_numero': nc.numero_documento,
            'dte_nuevo': nuevo_dte.numero_documento
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
```

### 4. Auto-Confirmación al Recepcionar

**En `confirmar_recepcion_api` (línea ~305):**

```python
# Al final del procesamiento de recepción, agregar:

# Verificar si hay solicitudes pendientes de confirmación
solicitudes_pendientes = Solicitud_Regularizacion.objects.filter(
    dte_solucion=dte,  # El DTE que se acaba de recepcionar
    estado='EJECUTADA'
)

for solicitud in solicitudes_pendientes:
    solicitud.estado = 'COMPLETADA'
    solicitud.fecha_confirmacion = hoy
    solicitud.usuario_confirma = usuario
    solicitud.conformidad = True
    solicitud.save()
    
    # Actualizar producto recepcionado original
    solicitud.producto_recepcionado.estado = 'REGULARIZADO'
    solicitud.producto_recepcionado.fecha_regularizacion = hoy
    solicitud.producto_recepcionado.save()
```

---

## 📊 Flujo Completo Integrado

### Ejemplo: NICK1 recibe de EDEL (empresas diferentes)

```
1. NICK1 recepciona DTE #1234
   ├─ Esperaba: 10x Nike Air T42
   ├─ Recibió: 7x Nike Air T42
   └─ Stock NICK1: Nike +7
   
2. NICK1 va a /app/regularizar-recepciones/
   ├─ Ve producto con problema
   ├─ Click "Regularizar"
   └─ Sistema detecta: "Entre empresas diferentes"
   
3. NICK1 crea SOLICITUD
   ├─ Selecciona tipo: "Cambio de producto"
   ├─ Busca productos en EDEL
   ├─ Selecciona: Adidas Stan T42 (stock EDEL: 15)
   ├─ Justifica: "Tengo más demanda de Adidas"
   ├─ Adjunta foto de caja dañada
   └─ Envía solicitud #SOL-001
   
4. EDEL recibe notificación
   └─ Email: "NICK1 solicita cambio de producto"
   
5. EDEL revisa en /app/solicitudes-regularizacion/
   ├─ Ve solicitud #SOL-001
   ├─ Verifica stock de Adidas Stan T42: 15 ✅
   ├─ Revisa foto adjunta
   └─ Aprueba solicitud
   
6. EDEL ejecuta solución
   ├─ Sistema genera automáticamente:
   │  ├─ NC #NC-123 por 3x Nike Air T42
   │  └─ DTE #1250 con 3x Adidas Stan T42 → NICK1
   ├─ Stock EDEL: Adidas -3 (15→12)
   └─ EDEL despacha físicamente las Adidas
   
7. NICK1 recibe DTE #1250
   ├─ Aparece en /app/recepcion-dte/ como cualquier otro
   ├─ Recepciona normalmente
   ├─ Stock NICK1: Adidas +3
   └─ Sistema AUTO-CONFIRMA solicitud #SOL-001
   
8. Estado Final
   ├─ Solicitud #SOL-001: COMPLETADA
   ├─ Producto original: REGULARIZADO
   ├─ NC emitida y registrada
   └─ Stock correcto en ambas sucursales
```

---

## ✅ Ventajas de esta Integración

1. **Respeta código existente**
   - Traspasos internos siguen funcionando igual
   - Solo agrega validación para entre empresas

2. **Reutiliza componentes**
   - Mismo modal de regularización
   - Misma búsqueda de productos
   - Mismo flujo de recepción

3. **Trazabilidad completa**
   - Solicitudes formales
   - Documentos (NC + DTE)
   - Movimientos de stock correctos

4. **UX mejorada**
   - Sistema detecta automáticamente el tipo
   - UI se adapta según corresponda
   - Mensajes claros de qué esperar

5. **Escalable**
   - Fácil agregar nuevos tipos de solución
   - Panel de reportes futuro
   - Métricas de servicio

---

## 🚀 Orden de Implementación

### Fase 1: Modelo (1 día)
- [ ] Crear modelo `Solicitud_Regularizacion`
- [ ] Migración de BD
- [ ] Métodos helper (generar_numero_solicitud, etc.)

### Fase 2: Actualizar Regularización (2 días)
- [ ] Modificar modal para detectar tipo de traspaso
- [ ] Agregar UI de "Solicitar Cambio"
- [ ] Actualizar `regularizar_producto_api`
- [ ] Endpoint búsqueda productos en emisor

### Fase 3: Panel Emisor (2 días)
- [ ] Vista `solicitudes_recibidas.html`
- [ ] Endpoint `obtener_solicitudes_recibidas`
- [ ] Modal de revisión
- [ ] Endpoint `aprobar_solicitud_api`

### Fase 4: Ejecución (2 días)
- [ ] Endpoint `ejecutar_solucion_api`
- [ ] Generación automática de NC
- [ ] Generación automática de DTE nuevo
- [ ] Actualización de stocks

### Fase 5: Auto-confirmación (1 día)
- [ ] Modificar `confirmar_recepcion_api`
- [ ] Auto-cierre de solicitudes
- [ ] Actualización de estados

### Fase 6: Notificaciones (1 día)
- [ ] Sistema de notificaciones en app
- [ ] Emails opcionales
- [ ] Badges de pendientes

### Fase 7: Reportes (1 día)
- [ ] Dashboard de solicitudes
- [ ] Métricas de tiempo de respuesta
- [ ] Análisis de productos problemáticos

---

**Total estimado: 10 días de desarrollo**

¿Empezamos por la Fase 1 (Modelo)? 🚀

