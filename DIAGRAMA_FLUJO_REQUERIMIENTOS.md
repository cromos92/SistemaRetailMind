# 📊 Diagrama de Flujo - Sistema de Requerimientos

## 🎯 Flujo Completo Propuesto

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          INICIO DEL PROCESO                               │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
              VENDEDOR/CAJERO                   CLIENTE
              (En Sucursal)                   (Con Problema)
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │   CREAR REQUERIMIENTO         │
                    │   - Buscar documento          │
                    │   - Seleccionar producto      │
                    │   - Datos del cliente         │
                    │   - Adjuntar fotos            │
                    │   - Descripción problema      │
                    └───────────────┬───────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │   ESTADO: PENDIENTE 🟡        │
                    │   Tiempo max: 24 horas        │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │  NOTIFICACIÓN AUTOMÁTICA      │
                    │  → Supervisor de Sucursal     │
                    └───────────────┬───────────────┘
                                    ▼
        ┌───────────────────────────────────────────────────┐
        │           SUPERVISOR REVISA                        │
        │           (Jefe de Sucursal)                       │
        └───────────────────┬───────────────────────────────┘
                            │
                ┌───────────┼───────────┐
                ▼           ▼           ▼
        ┌──────────┐ ┌──────────┐ ┌──────────────┐
        │ RECHAZAR │ │ APROBAR  │ │ ESCALAR A    │
        │          │ │ (Simple) │ │ ADMIN        │
        └────┬─────┘ └────┬─────┘ └──────┬───────┘
             │            │                │
             │            │                ▼
             │            │      ┌──────────────────────┐
             │            │      │ ESTADO: EN_REVISION  │
             │            │      │ Admin Central Revisa │
             │            │      └──────┬───────────────┘
             │            │             │
             │            │    ┌────────┼────────┐
             │            │    ▼        ▼        ▼
             │            │  APROBAR  RECHAZAR  ENVIAR A
             │            │           PROVEEDOR
             │            │                      │
             ▼            ▼                      ▼
        ┌─────────────────────────┐  ┌──────────────────────────┐
        │ ESTADO: RECHAZADO 🔴    │  │ ESTADO: ESPERANDO        │
        │ - Motivo registrado     │  │ PROVEEDOR 🟣             │
        │ - Cliente notificado    │  │ - Email enviado          │
        │ - Caso cerrado          │  │ - Fotos adjuntas         │
        └─────────────────────────┘  │ - Tracking activo        │
                                     └──────────┬───────────────┘
                                                │
                                     ┌──────────┴──────────┐
                                     │  CONTADOR DE DÍAS   │
                                     │  - 0-3 días: 🟢     │
                                     │  - 4-7 días: 🟡     │
                                     │  - 8+ días: 🔴      │
                                     │  - 15+ días: ALERTA │
                                     └──────────┬──────────┘
                                                │
                                     ┌──────────┴──────────┐
                                     │  PROVEEDOR RESPONDE │
                                     └──────────┬──────────┘
                                                │
                                   ┌────────────┼────────────┐
                                   ▼                         ▼
                        ┌──────────────────┐    ┌──────────────────┐
                        │ APROBADO POR     │    │ RECHAZADO POR    │
                        │ PROVEEDOR ✅     │    │ PROVEEDOR ❌     │
                        └────────┬─────────┘    └────────┬─────────┘
                                 │                       │
                                 ▼                       ▼
                        ┌─────────────────┐   ┌──────────────────┐
                        │ ESTADO:         │   │ ESTADO:          │
                        │ APROBADO 🟢     │   │ RECHAZADO 🔴     │
                        └────────┬────────┘   └──────────────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │ ESTADO:          │
                        │ EN_PROCESO 🔵    │
                        │ - Cambio         │
                        │ - Devolución     │
                        │ - Reparación     │
                        └────────┬─────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │ ESTADO:          │
                        │ COMPLETADO ✅    │
                        │ - Resolución     │
                        │ - Satisfacción   │
                        │ - Cierre caso    │
                        └──────────────────┘
```

---

## 👥 DIAGRAMA DE ROLES

```
┌─────────────────────────────────────────────────────────────┐
│                    ESTRUCTURA DE ROLES                       │
└─────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────┐
        │    ADMINISTRADOR CENTRAL         │
        │    - Ve TODO                     │
        │    - Gestiona proveedores        │
        │    - Aprueba/Rechaza todo        │
        │    - Genera reportes             │
        └────────────┬─────────────────────┘
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐
    │Sucursal │ │Sucursal │ │Sucursal │
    │   A     │ │   B     │ │   C     │
    └────┬────┘ └────┬────┘ └────┬────┘
         │           │           │
    ┌────┴────┐ ┌───┴────┐ ┌────┴────┐
    │SUPERVISOR│SUPERVISOR│SUPERVISOR│
    │- Ve su  ││- Ve su  ││- Ve su   │
    │  sucursal││ sucursal││ sucursal │
    │- Aprueba││- Aprueba││- Aprueba │
    │  simples││ simples ││ simples  │
    │- Escala ││- Escala ││- Escala  │
    └────┬────┘└────┬────┘└────┬─────┘
         │          │          │
    ┌────┴───┐ ┌───┴───┐ ┌────┴────┐
    │VENDEDOR│ │VENDEDOR│VENDEDOR  │
    │VENDEDOR│ │VENDEDOR│VENDEDOR  │
    │- Crea  │ │- Crea  │- Crea    │
    │- Ve sus│ │- Ve sus│- Ve sus  │
    │  casos │ │ casos  │ casos    │
    └────────┘ └────────┘└──────────┘
```

---

## 📧 FLUJO DE COMUNICACIÓN CON PROVEEDOR

```
┌──────────────────────────────────────────────────────────────┐
│               COMUNICACIÓN CON PROVEEDOR                      │
└──────────────────────────────────────────────────────────────┘

ADMINISTRADOR                    SISTEMA                   PROVEEDOR
     │                              │                          │
     │ 1. Click "Enviar a          │                          │
     │    Proveedor"                │                          │
     ├─────────────────────────────→│                          │
     │                              │                          │
     │                              │ 2. Generar email HTML    │
     │                              │    con fotos             │
     │                              │                          │
     │                              │ 3. Enviar correo         │
     │                              ├─────────────────────────→│
     │                              │                          │
     │                              │ 4. Cambiar estado:       │
     │                              │    ESPERANDO_PROVEEDOR   │
     │                              │                          │
     │                              │ 5. Iniciar contador      │
     │                              │    de días               │
     │                              │                          │
     │                              │                          │ 6. Proveedor
     │                              │                          │    revisa email
     │                              │                          │    y fotos
     │                              │                          │
     │                              │ 7. Proveedor responde    │
     │                              │←─────────────────────────┤
     │                              │    (Email o Portal)      │
     │                              │                          │
     │ 8. Notificación de          │                          │
     │    respuesta                 │                          │
     │←─────────────────────────────┤                          │
     │                              │                          │
     │ 9. Registra respuesta        │                          │
     │    en sistema                │                          │
     ├─────────────────────────────→│                          │
     │                              │                          │
     │                              │ 10. Cambiar estado:      │
     │                              │     APROBADO/RECHAZADO   │
     │                              │                          │

Si pasan > 7 días sin respuesta:
     │                              │                          │
     │ ALERTA: Sin respuesta        │                          │
     │←─────────────────────────────┤                          │
     │                              │                          │
     │ Opción: Enviar recordatorio  │                          │
     ├─────────────────────────────→│                          │
     │                              ├─────────────────────────→│
     │                              │   RECORDATORIO           │
```

---

## ⏱️ TIEMPOS ESPERADOS POR ESTADO

```
ESTADO                  TIEMPO ESPERADO       ALERTA SI EXCEDE
──────────────────────────────────────────────────────────────
PENDIENTE               < 24 horas            > 2 días
EN_REVISION             < 48 horas            > 3 días
ESPERANDO_PROVEEDOR     5-10 días hábiles     > 15 días
APROBADO                < 24 horas            > 2 días
EN_PROCESO              Variable (3-30 días)  Según tipo
COMPLETADO              N/A                   N/A
RECHAZADO               N/A                   N/A
CANCELADO               N/A                   N/A
```

---

## 📱 NOTIFICACIONES PROPUESTAS

### Email Automáticos

```
EVENTO                           DESTINATARIO              PLANTILLA
─────────────────────────────────────────────────────────────────────────
Nuevo requerimiento              Supervisor                nuevo_req.html
Requerimiento sin revisar > 2d   Supervisor + Admin        alerta_pendiente.html
Enviado a proveedor              Proveedor                 req_proveedor.html
Proveedor sin respuesta > 7d     Administrador             alerta_sin_resp.html
Proveedor respondió              Administrador             resp_proveedor.html
Requerimiento aprobado           Vendedor + Cliente        aprobado.html
Requerimiento rechazado          Vendedor + Cliente        rechazado.html
Requerimiento completado         Vendedor + Cliente        completado.html
```

### Notificaciones en Sistema (Campana 🔔)

```javascript
// En navbar
<span class="badge bg-danger">3</span>  // 3 notificaciones

Dropdown:
├─ 🟡 Req #001 pendiente de revisión (2 días)
├─ 🔴 Req #015 sin respuesta de proveedor (12 días)  
└─ 🟢 Req #008 fue aprobado por proveedor
```

---

## 🎨 PROPUESTA DE UI - DETALLE CON ACCIONES

```html
┌────────────────────────────────────────────────────────────────┐
│ REQ-20241117-0001 - Garantía Zapatillas Nike                  │
│ [En Revisión 🔵] [Alta 🔴] [2 días ⏰]                        │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │  INFORMACIÓN DEL CASO                                     │ │
│ │  Producto: Zapatillas Nike Air Max (SKU: 12345)          │ │
│ │  Cliente: Juan Pérez (12345678-9)                        │ │
│ │  Documento: Boleta Electrónica #456                      │ │
│ │  Motivo: Desprendimiento de suela                        │ │
│ │  [Ver 3 fotos adjuntas]                                  │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                                │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │  ACCIONES DISPONIBLES (Administrador)                     │ │
│ ├──────────────────────────────────────────────────────────┤ │
│ │  [✅ Aprobar Internamente]  [❌ Rechazar]                │ │
│ │  [📧 Enviar a Proveedor Nike]                            │ │
│ │  [👤 Asignar a: _________]  [💬 Agregar Comentario]     │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                                │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │  SEGUIMIENTO DE PROVEEDOR                                 │ │
│ ├──────────────────────────────────────────────────────────┤ │
│ │  Estado: No enviado                                       │ │
│ │  ┌─────────────────────────────────────────────────────┐ │ │
│ │  │ Proveedor: Nike Chile                                │ │ │
│ │  │ Email: nike@proveedor.cl                             │ │ │
│ │  │ [📧 Enviar Ahora]                                    │ │ │
│ │  └─────────────────────────────────────────────────────┘ │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                                │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │  HISTORIAL (Timeline)                                     │ │
│ ├──────────────────────────────────────────────────────────┤ │
│ │  ● 17/11 10:30 - CREADO por María (Vendedor)            │ │
│ │  ● 17/11 14:20 - EN_REVISION por Carlos (Supervisor)     │ │
│ │  ● 17/11 15:00 - Comentario: "Verificar fecha compra"   │ │
│ └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

**Cuando se envía a proveedor**:
```html
┌──────────────────────────────────────────────────────────┐
│  SEGUIMIENTO DE PROVEEDOR                                 │
├──────────────────────────────────────────────────────────┤
│  ✅ Correo enviado a: nike@proveedor.cl                  │
│  📅 Fecha envío: 17/11/2024 16:30                        │
│  ⏰ Días sin respuesta: 2 días 🟢                        │
│                                                           │
│  [📧 Re-enviar Correo]  [📝 Registrar Respuesta]        │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ 💡 TIP: Puede registrar la respuesta manualmente    │ │
│  │     o el proveedor puede responder por email        │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘
```

**Cuando pasan 8+ días sin respuesta**:
```html
┌──────────────────────────────────────────────────────────┐
│  ⚠️  ALERTA: SIN RESPUESTA DEL PROVEEDOR                │
├──────────────────────────────────────────────────────────┤
│  ⏰ Han pasado 12 días sin respuesta                     │
│  📧 Último envío: 05/11/2024                             │
│                                                           │
│  ACCIONES SUGERIDAS:                                      │
│  [📧 Enviar Recordatorio]                                │
│  [📞 Llamar al Proveedor]                                │
│  [❌ Marcar como Rechazado]                              │
│  [🔄 Cambiar Proveedor]                                  │
└──────────────────────────────────────────────────────────┘
```

---

## 📋 VISTA DE GESTIÓN (Dashboard)

```
┌────────────────────────────────────────────────────────────────┐
│  GESTIÓN DE REQUERIMIENTOS - Panel de Administrador           │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │PENDIENTES│ │ESPERANDO │ │SIN RESP. │ │POR       │       │
│  │          │ │PROVEEDOR │ │+7 DÍAS   │ │COMPLETAR │       │
│  │    15    │ │    8     │ │    3     │ │    12    │       │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘       │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ FILTROS:                                                  │ │
│  │ [Todo ▼] [En Revisión ▼] [Todas Sucursales ▼]           │ │
│  │ [Buscar: ___________]  [📊 Exportar]                    │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  REQUERIMIENTOS CRÍTICOS (Sin respuesta > 10 días):           │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ REQ#  │ TIPO      │ PROVEEDOR │ DÍAS  │ ACCIONES        │ │
│  ├──────────────────────────────────────────────────────────┤ │
│  │ 015   │ Garantía  │ Nike      │ 12 🔴 │ [📧][📞][❌]  │ │
│  │ 023   │ Devolución│ Adidas    │ 11 🔴 │ [📧][📞][❌]  │ │
│  │ 031   │ Cambio    │ Puma      │ 10 🔴 │ [📧][📞][❌]  │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                                │
│  REQUERIMIENTOS RECIENTES:                                     │
│  [Tabla completa con paginación...]                           │
└────────────────────────────────────────────────────────────────┘
```

---

## 🔧 FUNCIONES A IMPLEMENTAR

### Función 1: Validar Permisos
```python
def usuario_puede_realizar_accion(user, requerimiento, accion):
    """
    Valida si el usuario puede realizar una acción sobre el requerimiento
    """
    # Obtener rol del usuario
    if user.is_superuser:
        rol = 'administrador'
    elif user.groups.filter(name='Supervisor').exists():
        rol = 'supervisor'
    else:
        rol = 'vendedor'
    
    # Validar según rol y acción
    if rol == 'vendedor':
        # Solo puede editar sus propios requerimientos pendientes
        if accion == 'editar':
            return (requerimiento.usuario_creador == user and 
                    requerimiento.estado == 'PENDIENTE')
        return False
    
    elif rol == 'supervisor':
        # Puede gestionar requerimientos de su sucursal
        if accion in ['revisar', 'aprobar_simple', 'rechazar']:
            sucursales = user.empresauser_set.first().sucursal
            return requerimiento.sucursal == sucursales
        return False
    
    elif rol == 'administrador':
        # Puede hacer todo
        return True
    
    return False
```

---

### Función 2: Enviar a Proveedor (Mejorada)
```python
@login_required
@require_POST
def enviar_requerimiento_proveedor(request, requerimiento_id):
    """
    Envía requerimiento al proveedor por correo con tracking
    """
    requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
    
    # Validar permisos
    if not usuario_puede_realizar_accion(request.user, requerimiento, 'enviar_proveedor'):
        return JsonResponse({'success': False, 'error': 'No tiene permisos'}, status=403)
    
    if not requerimiento.proveedor:
        return JsonResponse({'success': False, 'error': 'No hay proveedor asignado'}, status=400)
    
    try:
        # Preparar contexto para email
        context = {
            'requerimiento': requerimiento,
            'fotos': requerimiento.fotos.all(),
            'empresa': requerimiento.sucursal.empresa,
            'url_respuesta': f"{settings.SITE_URL}/proveedor/responder/{generar_token(requerimiento)}/",
        }
        
        # Renderizar HTML
        html_content = render_to_string('emails/requerimiento_proveedor.html', context)
        
        # Crear email
        email = EmailMessage(
            subject=f'Requerimiento #{requerimiento.numero_requerimiento} - {requerimiento.get_tipo_display()}',
            body=html_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[requerimiento.proveedor.correoVendedor],
            cc=[requerimiento.proveedor.correoAdministrador] if requerimiento.proveedor.correoAdministrador else [],
            reply_to=[request.user.email] if request.user.email else [],
        )
        email.content_subtype = 'html'
        
        # Adjuntar fotos
        for foto in requerimiento.fotos.all():
            if foto.imagen and default_storage.exists(foto.imagen.name):
                email.attach_file(foto.imagen.path)
        
        # Enviar
        email.send(fail_silently=False)
        
        # Actualizar requerimiento
        with transaction.atomic():
            requerimiento.correo_enviado_proveedor = True
            requerimiento.fecha_envio_proveedor = timezone.now()
            requerimiento.correo_proveedor_destino = requerimiento.proveedor.correoVendedor
            requerimiento.intentos_envio = (requerimiento.intentos_envio or 0) + 1
            requerimiento.estado = 'ESPERANDO_PROVEEDOR'
            requerimiento.save()
            
            # Registrar en historial
            HistorialRequerimiento.objects.create(
                requerimiento=requerimiento,
                accion='ENVIADO_A_PROVEEDOR',
                estado_anterior=requerimiento.estado,
                estado_nuevo='ESPERANDO_PROVEEDOR',
                comentario=f'Correo enviado a {requerimiento.proveedor.nombre} ({requerimiento.correo_proveedor_destino})',
                usuario=request.user
            )
        
        return JsonResponse({
            'success': True,
            'message': f'Requerimiento enviado a {requerimiento.proveedor.nombre}',
            'fecha_envio': requerimiento.fecha_envio_proveedor.strftime('%d/%m/%Y %H:%M')
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al enviar: {str(e)}'
        }, status=500)
```

---

### Función 3: Registrar Respuesta Proveedor
```python
@login_required
@require_POST
def registrar_respuesta_proveedor_manual(request, requerimiento_id):
    """
    Permite al admin registrar manualmente la respuesta del proveedor
    """
    data = json.loads(request.body)
    requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
    
    # Validar permisos
    if not usuario_puede_realizar_accion(request.user, requerimiento, 'registrar_respuesta'):
        return JsonResponse({'success': False, 'error': 'No tiene permisos'}, status=403)
    
    respuesta = data.get('respuesta')
    decision = data.get('decision')  # 'APROBADO' o 'RECHAZADO'
    
    if not respuesta or not decision:
        return JsonResponse({'success': False, 'error': 'Faltan datos'}, status=400)
    
    with transaction.atomic():
        requerimiento.respuesta_proveedor = respuesta
        requerimiento.fecha_respuesta_proveedor = timezone.now()
        requerimiento.estado = decision
        requerimiento.save()
        
        # Historial
        HistorialRequerimiento.objects.create(
            requerimiento=requerimiento,
            accion='RESPUESTA_PROVEEDOR',
            estado_anterior='ESPERANDO_PROVEEDOR',
            estado_nuevo=decision,
            comentario=f'Proveedor {requerimiento.proveedor.nombre} respondió: {respuesta[:100]}...',
            usuario=request.user
        )
        
        # Notificar al vendedor que creó el requerimiento
        notificar_usuario(
            requerimiento.usuario_creador,
            f'El proveedor respondió tu requerimiento #{requerimiento.numero_requerimiento}',
            'success' if decision == 'APROBADO' else 'warning'
        )
    
    return JsonResponse({
        'success': True,
        'message': f'Respuesta registrada: {decision}'
    })
```

---

## 📊 REPORTES SUGERIDOS

### Reporte 1: Por Proveedor
```
PROVEEDOR     TOTAL  APROBADOS  RECHAZADOS  TASA APROB.  TIEMPO RESP.
────────────────────────────────────────────────────────────────────
Nike Chile      45      38          7          84.4%       3.2 días
Adidas Chile    32      28          4          87.5%       2.8 días
Puma Sports     18      12          6          66.7%       5.1 días
```

### Reporte 2: Por Sucursal
```
SUCURSAL   TOTAL  PENDIENTES  EN PROCESO  COMPLETADOS  % ÉXITO
──────────────────────────────────────────────────────────────
Santiago     125      8           15          98         78.4%
Viña         89       5           10          72         80.9%
Concepción   67       3            8          54         80.6%
```

### Reporte 3: Tiempo de Resolución
```
TIPO          CASOS  PROMEDIO    MÁS RÁPIDO  MÁS LENTO
───────────────────────────────────────────────────────
Garantía       145    5.2 días    1 día       45 días
Devolución     89     2.1 días    1 día       15 días
Cambio         67     1.8 días    0.5 días    8 días
Reclamo        34     8.5 días    2 días      60 días
```

---

## 🎯 RESUMEN EJECUTIVO

### Lo que YA tienes:
- ✅ Modelos de requerimientos creados
- ✅ Estados definidos (8 estados)
- ✅ Historial de cambios
- ✅ Fotos adjuntas
- ✅ Campos para tracking de proveedor

### Lo que FALTA implementar:

#### CRÍTICO (Hacer AHORA):
1. 🔴 Validación de permisos por rol
2. 🔴 Botones dinámicos según estado
3. 🔴 Función mejorada enviar_a_proveedor()
4. 🔴 Registrar respuesta del proveedor
5. 🔴 Alertas de seguimiento

#### IMPORTANTE (Próxima semana):
6. 🟡 Dashboard de gestión por rol
7. 🟡 Notificaciones automáticas
8. 🟡 Plantillas de email
9. 🟡 Reportes básicos
10. 🟡 Métricas de KPI

#### DESEABLE (Futuro):
11. 🟢 Portal para proveedores
12. 🟢 Integración WhatsApp
13. 🟢 Firma digital
14. 🟢 App móvil
15. 🟢 BI/Analytics avanzado

---

## 💬 PREGUNTAS PARA TI

1. **¿Tienes configurado SMTP para enviar emails?**
   - ✅ Sí → Podemos implementar emails ya
   - ❌ No → Necesitamos configurarlo primero

2. **¿Cómo identificas roles? (Supervisor/Admin)**
   - Opción A: Django Groups
   - Opción B: Campo en modelo Usuario
   - Opción C: Por permisos específicos

3. **¿Quieres empezar con MVP o implementación completa?**
   - MVP: Solo lo crítico (2-3 días)
   - Completo: Todo el plan (1-2 semanas)

4. **¿Respuesta de proveedores:**
   - Opción A: Solo por email (manual)
   - Opción B: Portal web con token
   - Opción C: Ambas opciones

---

**Dime tu respuesta y empezamos inmediatamente con la implementación** 🚀

