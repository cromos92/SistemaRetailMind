# 🎯 Plan de Implementación - Flujo Completo de Requerimientos

## 📅 Fecha: 17 de Noviembre, 2024

---

## 🎭 ROLES Y PERMISOS

### Rol 1: VENDEDOR / CAJERO
**Ubicación**: Sucursal (punto de venta)

**Permisos**:
- ✅ Crear requerimientos
- ✅ Ver sus propios requerimientos
- ✅ Ver requerimientos de su sucursal
- ✅ Agregar fotos
- ✅ Agregar comentarios
- ❌ NO puede cambiar estados
- ❌ NO puede enviar a proveedor
- ❌ NO puede aprobar/rechazar

**Casos de uso**:
- Cliente llega con problema
- Vendedor crea requerimiento
- Adjunta fotos del producto
- Lo envía para revisión

---

### Rol 2: SUPERVISOR / JEFE DE SUCURSAL
**Ubicación**: Sucursal (gestión local)

**Permisos**:
- ✅ Ver todos los requerimientos de su sucursal
- ✅ Revisar requerimientos pendientes
- ✅ Aprobar o rechazar requerimientos simples
- ✅ Marcar como "En Revisión"
- ✅ Agregar comentarios de revisión
- ✅ Solicitar más información al vendedor
- ⚠️ Puede enviar a Administrador Central si requiere aprobación mayor
- ❌ NO puede enviar directamente a proveedor

**Casos de uso**:
- Revisa requerimientos de su sucursal
- Aprueba cambios/devoluciones simples
- Escala casos complejos a administración

---

### Rol 3: ADMINISTRADOR CENTRAL / GERENTE
**Ubicación**: Oficina central

**Permisos**:
- ✅ Ver TODOS los requerimientos de TODAS las sucursales
- ✅ Revisar casos escalados
- ✅ Aprobar/Rechazar requerimientos
- ✅ Enviar correo a proveedor
- ✅ Hacer seguimiento de respuestas
- ✅ Marcar como "Esperando Proveedor"
- ✅ Registrar respuesta del proveedor
- ✅ Completar requerimientos
- ✅ Ver estadísticas globales
- ✅ Exportar reportes

**Casos de uso**:
- Gestiona requerimientos de todas las sucursales
- Contacta proveedores para garantías
- Hace seguimiento de casos complejos
- Genera reportes para gerencia

---

### Rol 4: PROVEEDOR (Opcional - Portal Externo)
**Ubicación**: Acceso externo (futuro)

**Permisos**:
- ✅ Ver requerimientos enviados a él
- ✅ Responder requerimientos
- ✅ Aprobar/Rechazar garantías
- ✅ Adjuntar documentos
- ❌ NO puede ver otros proveedores

---

## 🔄 FLUJO DE ESTADOS

### Estados Definidos (8 estados)

```
┌─────────────────────────────────────────────────────────────────┐
│                    CICLO DE VIDA DEL REQUERIMIENTO              │
└─────────────────────────────────────────────────────────────────┘

1. PENDIENTE (Inicial)
   ↓
2. EN_REVISION (Supervisor revisando)
   ↓
   ├─→ RECHAZADO (No procede)
   │   └─→ FIN
   │
   ├─→ APROBADO (Procede sin proveedor)
   │   └─→ EN_PROCESO → COMPLETADO
   │
   └─→ ESPERANDO_PROVEEDOR (Requiere proveedor)
       ↓
       ├─→ APROBADO (Proveedor acepta)
       │   └─→ EN_PROCESO → COMPLETADO
       │
       └─→ RECHAZADO (Proveedor rechaza)
           └─→ FIN (o CANCELADO)
```

---

## 📊 ESTADOS DETALLADOS

### 1️⃣ PENDIENTE
**Descripción**: Requerimiento recién creado, esperando revisión inicial

**Quién lo asigna**: Sistema (automático al crear)

**Acciones disponibles**:
- 👤 **Vendedor**: Puede editar, agregar fotos, cancelar
- 👨‍💼 **Supervisor**: Marcar como "En Revisión"
- 👔 **Administrador**: Marcar como "En Revisión" o pasar directo a otros estados

**Color UI**: 🟡 Amarillo (Warning)

**Tiempo esperado**: < 24 horas

---

### 2️⃣ EN_REVISION
**Descripción**: Supervisor o administrador está revisando el caso

**Quién lo asigna**: Supervisor o Administrador

**Acciones disponibles**:
- 👨‍💼 **Supervisor**: 
  - Aprobar (si es simple)
  - Rechazar (si no procede)
  - Escalar a Administrador (si es complejo)
  - Solicitar más información
- 👔 **Administrador**: 
  - Aprobar
  - Rechazar
  - Enviar a Proveedor
  - Solicitar documentación adicional

**Color UI**: 🔵 Azul (Info)

**Tiempo esperado**: < 48 horas

---

### 3️⃣ ESPERANDO_PROVEEDOR
**Descripción**: Correo enviado al proveedor, esperando respuesta

**Quién lo asigna**: Administrador (al enviar correo)

**Acciones disponibles**:
- 👔 **Administrador**: 
  - Registrar respuesta del proveedor
  - Re-enviar correo
  - Ver historial de comunicaciones
  - Cancelar si no responde
- 🏢 **Proveedor** (futuro):
  - Ver detalles del requerimiento
  - Aprobar garantía
  - Rechazar con motivo
  - Adjuntar documentos

**Color UI**: 🟣 Púrpura (Primary)

**Tiempo esperado**: 5-10 días hábiles

**Seguimiento**:
- Fecha de envío registrada
- Contador de días sin respuesta
- Alertas si pasa de X días

---

### 4️⃣ APROBADO
**Descripción**: Requerimiento aprobado, listo para resolución

**Quién lo asigna**: 
- Supervisor (casos simples)
- Administrador (casos complejos)
- Sistema (al recibir aprobación de proveedor)

**Acciones disponibles**:
- 👔 **Administrador**: 
  - Marcar como "En Proceso"
  - Asignar a responsable
  - Programar fecha de resolución

**Color UI**: 🟢 Verde (Success)

**Siguiente paso**: Pasar a EN_PROCESO

---

### 5️⃣ RECHAZADO
**Descripción**: Requerimiento no procede o fue rechazado por proveedor

**Quién lo asigna**:
- Supervisor
- Administrador
- Sistema (al recibir rechazo de proveedor)

**Información requerida**:
- ⚠️ **Motivo del rechazo** (obligatorio)
- 📝 Comentario explicativo
- 📄 Documentos de soporte (opcional)

**Acciones disponibles**:
- 👔 **Administrador**: 
  - Ver motivo de rechazo
  - Reabrir si hay nueva información
  - Marcar como Cancelado

**Color UI**: 🔴 Rojo (Danger)

**Estado final**: Generalmente terminal

---

### 6️⃣ EN_PROCESO
**Descripción**: Se está ejecutando la solución (cambio, devolución, reparación)

**Quién lo asigna**: Administrador o Supervisor

**Acciones disponibles**:
- 👨‍💼 **Supervisor/Administrador**: 
  - Actualizar progreso
  - Agregar notas de seguimiento
  - Completar cuando esté listo

**Color UI**: 🔵 Azul (Info)

**Tiempo esperado**: Variable según tipo

---

### 7️⃣ COMPLETADO
**Descripción**: Requerimiento resuelto satisfactoriamente

**Quién lo asigna**: Supervisor o Administrador

**Información requerida**:
- ✅ **Resolución final** (obligatorio)
- 📅 Fecha de resolución
- 📝 Comentarios finales
- 📊 Satisfacción del cliente (opcional)

**Acciones disponibles**:
- 📊 Ver en reportes
- 📧 Notificar al cliente (opcional)
- ⭐ Calificar resolución

**Color UI**: 🟢 Verde Oscuro (Success)

**Estado final**: Terminal

---

### 8️⃣ CANCELADO
**Descripción**: Requerimiento cancelado por cliente o por error

**Quién lo asigna**: Cualquier rol con permisos

**Motivos comunes**:
- Cliente desistió
- Creado por error
- Duplicado
- Cliente no se presentó

**Color UI**: ⚫ Gris (Secondary)

**Estado final**: Terminal

---

## 📧 SISTEMA DE CORREOS

### Envío a Proveedor

**Campos de Email**:
```python
{
    "para": proveedor.correoVendedor,
    "cc": proveedor.correoAdministrador,
    "asunto": "Requerimiento #{numero} - {tipo}",
    "adjuntos": [fotos del requerimiento],
    "plantilla": "email_requerimiento_proveedor.html"
}
```

**Contenido del Email**:
```
Estimado proveedor {nombre_proveedor},

Se ha generado un requerimiento de {tipo} con los siguientes detalles:

Número: {numero_requerimiento}
Sucursal: {sucursal}
Fecha: {fecha_creacion}

PRODUCTO:
- SKU: {sku}
- Nombre: {nombre_producto}
- Documento: {tipo_documento} N° {numero_documento}
- Fecha Compra: {fecha_compra}

CLIENTE:
- Nombre: {cliente_nombre}
- RUT: {cliente_rut}
- Contacto: {cliente_telefono}

MOTIVO:
{motivo}

DESCRIPCIÓN:
{descripcion_problema}

[Ver Fotos Adjuntas]

Por favor, responda este requerimiento ingresando al siguiente enlace:
{url_respuesta_proveedor}

O responda directamente a este correo.

Saludos cordiales,
{usuario_nombre}
{empresa_nombre}
```

---

### Seguimiento de Respuestas

**Campos en Modelo Requerimiento**:
```python
# Ya existen:
correo_enviado_proveedor: bool
fecha_envio_proveedor: datetime
respuesta_proveedor: text
fecha_respuesta_proveedor: datetime

# A AGREGAR:
correo_proveedor_destino: varchar(200)  # Para saber a quién se envió
intentos_envio: int                      # Cuántas veces se envió
ultimo_recordatorio: datetime            # Fecha último recordatorio
dias_sin_respuesta: property             # Calculado
```

**Tracking**:
- 📧 Email enviado: `correo_enviado_proveedor = True`
- 📅 Fecha registro: `fecha_envio_proveedor = timezone.now()`
- ⏰ Contador: Días sin respuesta (calculado)
- 🔔 Alerta: Si > 7 días sin respuesta
- 🔄 Recordatorio: Botón para re-enviar

---

## 🎯 PLAN DE IMPLEMENTACIÓN

### FASE 1: Estados y Transiciones (1-2 días)

**Archivos a modificar**:
- ✅ `models.py` - Agregar campos de seguimiento
- ✅ `views_modulo_requerimientos.py` - Funciones de transición
- ✅ `detalle_requerimiento.html` - Botones según estado/rol

**Tareas**:
1. ✅ Crear función `puede_cambiar_estado(user, requerimiento, nuevo_estado)`
2. ✅ Crear función `transicion_permitida(estado_actual, estado_nuevo)`
3. ✅ Agregar validación de permisos por rol
4. ✅ Crear matriz de transiciones permitidas

**Matriz de Transiciones**:
```python
TRANSICIONES_PERMITIDAS = {
    'PENDIENTE': ['EN_REVISION', 'CANCELADO'],
    'EN_REVISION': ['APROBADO', 'RECHAZADO', 'ESPERANDO_PROVEEDOR', 'CANCELADO'],
    'ESPERANDO_PROVEEDOR': ['APROBADO', 'RECHAZADO'],
    'APROBADO': ['EN_PROCESO', 'CANCELADO'],
    'EN_PROCESO': ['COMPLETADO', 'CANCELADO'],
    'RECHAZADO': [],  # Estado final
    'COMPLETADO': [],  # Estado final
    'CANCELADO': [],  # Estado final
}
```

---

### FASE 2: Sistema de Correos (2-3 días)

**Archivos a crear/modificar**:
- ✅ `templates/emails/requerimiento_proveedor.html` - Plantilla de email
- ✅ `views_modulo_requerimientos.py` - Función `enviar_a_proveedor()`
- ✅ `models.py` - Campos de tracking de email

**Tareas**:

#### 2.1 Plantilla de Email
```html
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial; }
        .header { background: #405189; color: white; padding: 20px; }
        .content { padding: 20px; }
        .producto { background: #f8f9fa; padding: 15px; }
        .fotos { display: flex; gap: 10px; }
        .btn { background: #0ab39c; color: white; padding: 10px 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h2>Requerimiento de {{ tipo }} #{{ numero }}</h2>
    </div>
    <div class="content">
        <!-- Contenido detallado -->
    </div>
</body>
</html>
```

#### 2.2 Función Enviar Email
```python
def enviar_a_proveedor(request, requerimiento_id):
    requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
    
    # Preparar email
    context = {
        'requerimiento': requerimiento,
        'fotos': requerimiento.fotos.all(),
        'url_respuesta': generar_token_respuesta(requerimiento),
    }
    
    html_message = render_to_string('emails/requerimiento_proveedor.html', context)
    
    # Adjuntar fotos
    email = EmailMessage(
        subject=f'Requerimiento #{requerimiento.numero_requerimiento}',
        body=html_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[requerimiento.proveedor.correoVendedor],
        cc=[requerimiento.proveedor.correoAdministrador],
    )
    email.content_subtype = "html"
    
    # Adjuntar fotos
    for foto in requerimiento.fotos.all():
        email.attach_file(foto.imagen.path)
    
    # Enviar
    email.send()
    
    # Actualizar requerimiento
    requerimiento.correo_enviado_proveedor = True
    requerimiento.fecha_envio_proveedor = timezone.now()
    requerimiento.estado = 'ESPERANDO_PROVEEDOR'
    requerimiento.save()
```

#### 2.3 Tracking de Respuestas
```python
# Agregar a models.py
@property
def dias_sin_respuesta(self):
    if not self.fecha_envio_proveedor:
        return 0
    if self.fecha_respuesta_proveedor:
        delta = self.fecha_respuesta_proveedor - self.fecha_envio_proveedor
    else:
        delta = timezone.now() - self.fecha_envio_proveedor
    return delta.days

@property
def requiere_recordatorio(self):
    return self.dias_sin_respuesta > 7 and not self.fecha_respuesta_proveedor
```

---

### FASE 3: Interfaz de Gestión por Roles (2-3 días)

**Archivos a modificar**:
- ✅ `detalle_requerimiento.html` - Botones dinámicos
- ✅ `gestion_requerimientos.html` - Filtros por rol
- ✅ `views_modulo_requerimientos.py` - Lógica de permisos

**Componentes UI**:

#### 3.1 Panel de Acciones Dinámico
```html
<div class="card">
    <div class="card-header">Acciones Disponibles</div>
    <div class="card-body">
        <!-- VENDEDOR -->
        {% if user_rol == 'vendedor' and req.estado == 'PENDIENTE' %}
            <button onclick="editarRequerimiento()">Editar</button>
            <button onclick="cancelarRequerimiento()">Cancelar</button>
        {% endif %}
        
        <!-- SUPERVISOR -->
        {% if user_rol == 'supervisor' %}
            {% if req.estado == 'PENDIENTE' %}
                <button onclick="marcarEnRevision()">Revisar</button>
            {% elif req.estado == 'EN_REVISION' %}
                <button onclick="aprobarSimple()">Aprobar</button>
                <button onclick="rechazar()">Rechazar</button>
                <button onclick="escalarAdmin()">Escalar a Admin</button>
            {% endif %}
        {% endif %}
        
        <!-- ADMINISTRADOR -->
        {% if user_rol == 'administrador' %}
            {% if req.estado == 'EN_REVISION' %}
                <button onclick="aprobar()">Aprobar</button>
                <button onclick="rechazar()">Rechazar</button>
                <button onclick="enviarProveedor()">Enviar a Proveedor</button>
            {% elif req.estado == 'ESPERANDO_PROVEEDOR' %}
                <button onclick="registrarRespuesta()">Registrar Respuesta</button>
                <button onclick="reenviarCorreo()">Re-enviar Correo</button>
            {% elif req.estado == 'APROBADO' %}
                <button onclick="marcarEnProceso()">Iniciar Proceso</button>
            {% elif req.estado == 'EN_PROCESO' %}
                <button onclick="completar()">Completar</button>
            {% endif %}
        {% endif %}
    </div>
</div>
```

#### 3.2 Indicadores Visuales
```html
<!-- Badge de tiempo -->
<span class="badge badge-{% if dias > 7 %}danger{% elif dias > 3 %}warning{% else %}success{% endif %}">
    {{ dias }} días
</span>

<!-- Alerta de seguimiento -->
{% if req.estado == 'ESPERANDO_PROVEEDOR' and req.requiere_recordatorio %}
    <div class="alert alert-warning">
        <i class="ri-alarm-warning-line"></i>
        <strong>¡Atención!</strong> Han pasado {{ req.dias_sin_respuesta }} días sin respuesta del proveedor.
        <button onclick="reenviarRecordatorio()">Enviar Recordatorio</button>
    </div>
{% endif %}
```

---

### FASE 4: Dashboard de Gestión (2 días)

**Archivo**: `gestionar_requerimientos.html`

**Componentes**:

#### 4.1 Filtros por Rol
```javascript
// Supervisor ve solo su sucursal
if (rol === 'supervisor') {
    filtros.sucursal_id = sucursal_actual;
}

// Administrador ve todo
if (rol === 'administrador') {
    filtros.todas_sucursales = true;
}
```

#### 4.2 Vistas Rápidas
```html
<div class="row">
    <!-- Card: Requieren Atención -->
    <div class="col-md-3">
        <div class="card bg-soft-warning">
            <div class="card-body">
                <h3>{{ pendientes_revision }}</h3>
                <p>Requieren Revisión</p>
                <a href="?estado=PENDIENTE">Ver todos</a>
            </div>
        </div>
    </div>
    
    <!-- Card: Esperando Proveedor -->
    <div class="col-md-3">
        <div class="card bg-soft-primary">
            <div class="card-body">
                <h3>{{ esperando_proveedor }}</h3>
                <p>Esperando Proveedor</p>
                <a href="?estado=ESPERANDO_PROVEEDOR">Ver todos</a>
            </div>
        </div>
    </div>
    
    <!-- Card: Sin Respuesta > 7 días -->
    <div class="col-md-3">
        <div class="card bg-soft-danger">
            <div class="card-body">
                <h3>{{ sin_respuesta_7dias }}</h3>
                <p>Sin Respuesta +7 días</p>
                <a href="?alerta=sin_respuesta">Ver todos</a>
            </div>
        </div>
    </div>
    
    <!-- Card: Por Completar -->
    <div class="col-md-3">
        <div class="card bg-soft-success">
            <div class="card-body">
                <h3>{{ por_completar }}</h3>
                <p>Listos para Completar</p>
                <a href="?estado=EN_PROCESO">Ver todos</a>
            </div>
        </div>
    </div>
</div>
```

#### 4.3 Tabla con Acciones Rápidas
```html
<table>
    <tr>
        <td>REQ-001</td>
        <td>Garantía</td>
        <td><span class="badge badge-warning">Esperando Proveedor</span></td>
        <td>
            <span class="badge badge-danger">12 días sin respuesta</span>
        </td>
        <td>
            <button onclick="verDetalle(1)">Ver</button>
            <button onclick="reenviarRecordatorio(1)">Recordar</button>
        </td>
    </tr>
</table>
```

---

## 🔔 SISTEMA DE NOTIFICACIONES

### Alertas Automáticas

```python
# En models.py o signals.py
def verificar_alertas_requerimientos():
    """
    Ejecutar diariamente (Celery task o cron)
    """
    # 1. Requerimientos sin revisar > 2 días
    pendientes = Requerimiento.objects.filter(
        estado='PENDIENTE',
        fecha_creacion__lt=timezone.now() - timedelta(days=2)
    )
    for req in pendientes:
        notificar_supervisor(req, "Requerimiento pendiente de revisión")
    
    # 2. Proveedores sin respuesta > 7 días
    sin_respuesta = Requerimiento.objects.filter(
        estado='ESPERANDO_PROVEEDOR',
        fecha_envio_proveedor__lt=timezone.now() - timedelta(days=7),
        fecha_respuesta_proveedor__isnull=True
    )
    for req in sin_respuesta:
        notificar_administrador(req, "Proveedor no ha respondido")
    
    # 3. Requerimientos en proceso > 10 días
    proceso_largo = Requerimiento.objects.filter(
        estado='EN_PROCESO',
        fecha_actualizacion__lt=timezone.now() - timedelta(days=10)
    )
    for req in proceso_largo:
        notificar_administrador(req, "Requerimiento en proceso hace mucho tiempo")
```

---

## 📊 DASHBOARD DE MÉTRICAS

### KPIs Importantes

```python
# 1. Tiempo Promedio de Resolución
def tiempo_promedio_resolucion():
    completados = Requerimiento.objects.filter(
        estado='COMPLETADO',
        fecha_resolucion__isnull=False
    )
    tiempos = []
    for req in completados:
        delta = req.fecha_resolucion - req.fecha_creacion
        tiempos.append(delta.days)
    return sum(tiempos) / len(tiempos) if tiempos else 0

# 2. Tasa de Aprobación de Proveedores
def tasa_aprobacion_proveedores():
    total = Requerimiento.objects.filter(
        estado__in=['APROBADO', 'RECHAZADO'],
        correo_enviado_proveedor=True
    ).count()
    aprobados = Requerimiento.objects.filter(
        estado='APROBADO',
        correo_enviado_proveedor=True
    ).count()
    return (aprobados / total * 100) if total > 0 else 0

# 3. Tiempo Promedio de Respuesta Proveedor
def tiempo_promedio_respuesta_proveedor():
    respondidos = Requerimiento.objects.filter(
        fecha_envio_proveedor__isnull=False,
        fecha_respuesta_proveedor__isnull=False
    )
    tiempos = []
    for req in respondidos:
        delta = req.fecha_respuesta_proveedor - req.fecha_envio_proveedor
        tiempos.append(delta.days)
    return sum(tiempos) / len(tiempos) if tiempos else 0
```

**Visualización**:
```
┌─────────────────────────────────────────┐
│ 📊 MÉTRICAS DE REQUERIMIENTOS          │
├─────────────────────────────────────────┤
│ Tiempo Promedio Resolución: 5.2 días   │
│ Tasa Aprobación Proveedor:  78%        │
│ Tiempo Respuesta Proveedor: 3.1 días   │
│ Satisfacción Cliente:       92%        │
└─────────────────────────────────────────┘
```

---

## 🛠️ IMPLEMENTACIÓN TÉCNICA

### Migración de Base de Datos

```python
# 0XXX_agregar_campos_seguimiento.py
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('app', 'ultima_migracion'),
    ]
    
    operations = [
        migrations.AddField(
            model_name='requerimiento',
            name='correo_proveedor_destino',
            field=models.CharField(max_length=200, blank=True, null=True),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='intentos_envio',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='ultimo_recordatorio',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='requerimiento',
            name='asignado_a',
            field=models.ForeignKey(
                'auth.User',
                on_delete=models.SET_NULL,
                null=True,
                blank=True,
                related_name='requerimientos_asignados'
            ),
        ),
    ]
```

---

### Permisos con Django Groups

```python
# En management/commands/setup_permisos_requerimientos.py
from django.contrib.auth.models import Group, Permission

# Crear grupos
vendedor_group = Group.objects.create(name='Vendedor Sucursal')
supervisor_group = Group.objects.create(name='Supervisor Sucursal')
admin_group = Group.objects.create(name='Administrador Central')

# Asignar permisos
vendedor_perms = [
    'add_requerimiento',
    'view_requerimiento',
    'change_requerimiento_propio',
]

supervisor_perms = vendedor_perms + [
    'view_all_requerimientos_sucursal',
    'change_estado_requerimiento_simple',
]

admin_perms = supervisor_perms + [
    'view_all_requerimientos',
    'change_estado_requerimiento',
    'delete_requerimiento',
    'enviar_proveedor',
]
```

---

## 📧 PORTAL DE RESPUESTA PARA PROVEEDORES

### Opción A: Por Email Directo

**Ventajas**:
- ✅ Simple para el proveedor
- ✅ No requiere login
- ✅ Respuesta por email normal

**Implementación**:
```python
# Proveedor responde el email
# Sistema detecta respuesta (webhook o polling)
# Actualiza requerimiento automáticamente
```

---

### Opción B: Portal Web con Token

**Ventajas**:
- ✅ Más control
- ✅ Puede ver fotos, detalles
- ✅ Puede aprobar/rechazar con botones

**Implementación**:
```python
# Generar token único
import secrets
token = secrets.token_urlsafe(32)

# Guardar token
TokenRespuesta.objects.create(
    requerimiento=requerimiento,
    token=token,
    valido_hasta=timezone.now() + timedelta(days=30)
)

# URL en email
url = f"https://tudominio.com/proveedor/responder/{token}/"

# Vista sin login
def responder_requerimiento_proveedor(request, token):
    token_obj = get_object_or_404(TokenRespuesta, token=token, vigente=True)
    requerimiento = token_obj.requerimiento
    
    # Mostrar formulario de respuesta
    # Guardar respuesta
    # Notificar administrador
```

---

## 🎨 MOCKUP DE INTERFAZ

### Vista Detalle con Acciones por Rol

```
┌────────────────────────────────────────────────────────────┐
│ REQ-20241117-0001 - Garantía                              │
│ [Pendiente] [Alta Prioridad] [3 días]                    │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ [Producto Info] [Cliente Info] [Descripción] [Fotos]     │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ ACCIONES (Para Administrador):                            │
│                                                            │
│ [📝 Revisar]  [✅ Aprobar]  [❌ Rechazar]                │
│ [📧 Enviar a Proveedor]  [👤 Asignar a...]              │
│                                                            │
├────────────────────────────────────────────────────────────┤
│ HISTORIAL:                                                 │
│ ● 17/11 10:30 - CREADO - Juan Pérez (Vendedor)           │
│ ● 17/11 14:20 - EN_REVISION - María González (Supervisor) │
│ ● 18/11 09:15 - Comentario agregado - María González     │
└────────────────────────────────────────────────────────────┘
```

---

## 📋 CHECKLIST DE IMPLEMENTACIÓN

### Sprint 1: Estados y Permisos (Semana 1)
- [ ] Agregar campos de seguimiento al modelo
- [ ] Crear migración
- [ ] Implementar matriz de transiciones
- [ ] Crear función de validación de permisos
- [ ] Actualizar API de cambio de estado
- [ ] Testing de transiciones

### Sprint 2: Sistema de Correos (Semana 2)
- [ ] Crear plantilla HTML de email
- [ ] Implementar función enviar_a_proveedor()
- [ ] Configurar adjuntos de fotos
- [ ] Implementar registro de respuesta
- [ ] Crear sistema de recordatorios
- [ ] Testing de envío de emails

### Sprint 3: Interfaz por Roles (Semana 3)
- [ ] Botones dinámicos según rol
- [ ] Panel de acciones contextual
- [ ] Alertas y notificaciones
- [ ] Filtros por rol
- [ ] Dashboard de gestión
- [ ] Testing de permisos UI

### Sprint 4: Reportes y Métricas (Semana 4)
- [ ] KPIs calculados
- [ ] Gráficos de tendencias
- [ ] Exportación de reportes
- [ ] Dashboard ejecutivo
- [ ] Testing de reportes

---

## 💡 RECOMENDACIONES

### Prioridad ALTA (Implementar Primero)
1. ✅ Sistema de estados con transiciones validadas
2. ✅ Permisos por rol (vendedor/supervisor/admin)
3. ✅ Botones dinámicos según estado
4. ✅ Envío de correo a proveedor
5. ✅ Registro de respuesta del proveedor

### Prioridad MEDIA (Semana 2-3)
6. Dashboard de gestión por rol
7. Alertas automáticas
8. Recordatorios de seguimiento
9. Métricas básicas
10. Portal de respuesta para proveedores

### Prioridad BAJA (Futuro)
11. Reportes avanzados
12. Integración con WhatsApp
13. Notificaciones push
14. App móvil para proveedores
15. Dashboard ejecutivo

---

## 🎯 PROPUESTA DE IMPLEMENTACIÓN INMEDIATA

### ¿Qué hacer AHORA? (1-2 días)

**Opción 1: Implementación Mínima Viable (MVP)**
```
✅ Agregar validación de permisos básica
✅ Botones según rol en detalle_requerimiento.html
✅ Función enviar_a_proveedor() mejorada
✅ Campos de seguimiento en modelo
✅ UI para registrar respuesta del proveedor
```

**Opción 2: Implementación Completa (1 semana)**
```
Todo lo anterior +
✅ Dashboard de gestión
✅ Alertas y notificaciones
✅ Reportes básicos
✅ Portal de respuesta
```

---

## 🤔 ¿QUÉ PREFIERES?

**Pregunta 1**: ¿Quieres que implemente el **MVP inmediato** (2 días) o el **completo** (1 semana)?

**Pregunta 2**: ¿Tienes configurado el **envío de emails** en Django (SMTP)?

**Pregunta 3**: ¿Los supervisores son un **grupo de Django** o tienen un **campo en el modelo**?

**Pregunta 4**: ¿Prefieres **portal web para proveedores** o solo **respuesta por email**?

---

Puedo empezar inmediatamente con lo que decidas. ¿Con cuál empezamos? 🚀
