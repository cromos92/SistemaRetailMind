# Módulo de Requerimientos de Garantías - RetailMind

## 📋 Descripción

Módulo completo para la gestión de requerimientos de garantías, devoluciones y reclamos desde cualquier sucursal del sistema RetailMind.

## ✨ Características Principales

### 1. **Creación de Requerimientos**
- Formulario completo con:
  - Información del producto (SKU, nombre)
  - Documento de venta (boleta, factura, número, fecha)
  - Datos del cliente (RUT, nombre, teléfono, email)
  - Descripción detallada del problema
  - Adjuntar hasta 5 fotos del producto/problema
  - Selección de proveedor
  - Configuración de prioridad

### 2. **Gestión y Seguimiento**
- Lista completa de requerimientos con filtros avanzados
- Estados del requerimiento:
  - Pendiente
  - En Revisión
  - Esperando Respuesta Proveedor
  - Aprobado
  - Rechazado
  - En Proceso
  - Completado
  - Cancelado

### 3. **Comunicación con Proveedores**
- Envío automático de correos electrónicos a proveedores
- Registro de respuestas de proveedores
- Seguimiento de fechas de envío y respuesta

### 4. **Historial Completo**
- Registro de todas las acciones realizadas
- Seguimiento de cambios de estado
- Comentarios y observaciones
- Auditoría completa del proceso

### 5. **Visualización y Reportes**
- Dashboard con estadísticas en tiempo real
- Vista detallada de cada requerimiento
- Exportación a Excel de requerimientos
- Filtrado por estado, tipo, sucursal, fechas

## 🚀 Instalación

El módulo ya está completamente integrado en RetailMind. Los pasos de instalación fueron:

1. **Modelos creados:**
   - `Requerimiento`: Modelo principal
   - `FotoRequerimiento`: Almacenamiento de fotos
   - `HistorialRequerimiento`: Registro de cambios

2. **Migraciones aplicadas:**
   ```bash
   python manage.py makemigrations
   python manage.py migrate
   ```

3. **Dependencias instaladas:**
   - Pillow (para manejo de imágenes)

## 📱 Uso del Módulo

### Acceso al Módulo

El módulo está disponible en el menú lateral:
- **Modulo Requerimientos**
  - Lista de Requerimientos
  - Crear Requerimiento
  - Gestionar Requerimientos

### Crear un Nuevo Requerimiento

1. Acceder a **Modulo Requerimientos > Crear Requerimiento**
2. Completar el formulario:
   - **Tipo**: Garantía, Devolución, Cambio, Reclamo, Consulta
   - **Prioridad**: Baja, Media, Alta, Urgente
   - **SKU**: Ingresar SKU del producto (puede buscar con el botón)
   - **Documento**: Tipo y número de boleta/factura (opcional)
   - **Cliente**: Datos completos del cliente
   - **Descripción**: Motivo y descripción detallada
   - **Fotos**: Adjuntar hasta 5 fotos
   - **Proveedor**: Seleccionar proveedor (opcional)
3. Click en **Crear Requerimiento**

### Gestionar Requerimientos

1. Acceder a **Modulo Requerimientos > Lista de Requerimientos**
2. Ver estadísticas en las tarjetas superiores
3. Usar filtros para buscar requerimientos específicos
4. Click en un requerimiento para ver detalle completo
5. Desde el detalle puede:
   - Cambiar el estado
   - Enviar al proveedor por correo
   - Registrar respuesta del proveedor
   - Completar el requerimiento con resolución

### Flujo de Trabajo Típico

```
1. SUCURSAL crea requerimiento → Estado: PENDIENTE
   ↓
2. ADMINISTRADOR revisa → Estado: EN_REVISION
   ↓
3. ADMINISTRADOR envía a proveedor → Estado: ESPERANDO_PROVEEDOR
   ↓
4. PROVEEDOR responde (por correo)
   ↓
5. ADMINISTRADOR registra respuesta → Estado: APROBADO/RECHAZADO
   ↓
6. ADMINISTRADOR procesa → Estado: EN_PROCESO
   ↓
7. ADMINISTRADOR completa → Estado: COMPLETADO
```

## 🔗 Endpoints API

### Endpoints Principales

- `GET /app/requerimientos/` - Vista lista de requerimientos
- `GET /app/requerimientos/crear/` - Formulario de creación
- `GET /app/requerimientos/<id>/` - Detalle de requerimiento
- `GET /app/requerimientos/gestionar/` - Panel de gestión

### APIs REST

- `POST /app/api/requerimientos/crear/` - Crear requerimiento
- `GET /app/api/requerimientos/listar/` - Listar con filtros
- `GET /app/api/requerimientos/<id>/` - Obtener detalle
- `POST /app/api/requerimientos/<id>/actualizar-estado/` - Cambiar estado
- `POST /app/api/requerimientos/<id>/enviar-proveedor/` - Enviar a proveedor
- `POST /app/api/requerimientos/<id>/respuesta-proveedor/` - Registrar respuesta
- `POST /app/api/requerimientos/<id>/completar/` - Completar requerimiento
- `GET /app/api/requerimientos/buscar-producto/` - Buscar por SKU
- `GET /app/api/requerimientos/estadisticas/` - Obtener estadísticas
- `GET /app/api/requerimientos/exportar/` - Exportar a Excel

## 📊 Modelos de Datos

### Requerimiento

```python
- numero_requerimiento: CharField (autogenerado: REQ-YYYYMMDD-XXXX)
- tipo: CharField (GARANTIA, DEVOLUCION, CAMBIO, RECLAMO, CONSULTA)
- estado: CharField (8 estados posibles)
- prioridad: CharField (BAJA, MEDIA, ALTA, URGENTE)
- sucursal: ForeignKey(Sucursal)
- usuario_creador: ForeignKey(User)
- producto_talla: ForeignKey(Producto_Talla)
- sku: CharField
- nombre_producto: CharField
- numero_boleta: CharField
- cliente_nombre: CharField
- cliente_rut: CharField
- cliente_telefono: CharField
- cliente_email: EmailField
- motivo: TextField
- descripcion_problema: TextField
- proveedor: ForeignKey(Empresa)
- respuesta_proveedor: TextField
- resolucion: TextField
- fecha_creacion: DateTimeField
```

### FotoRequerimiento

```python
- requerimiento: ForeignKey(Requerimiento)
- imagen: ImageField
- descripcion: CharField
- orden: IntegerField (1-5)
- fecha_subida: DateTimeField
```

### HistorialRequerimiento

```python
- requerimiento: ForeignKey(Requerimiento)
- accion: CharField
- estado_anterior: CharField
- estado_nuevo: CharField
- comentario: TextField
- usuario: ForeignKey(User)
- fecha: DateTimeField
```

## 🎨 Pantallas del Módulo

### 1. Lista de Requerimientos
- Dashboard con estadísticas
- Tabla con todos los requerimientos
- Filtros avanzados
- Exportación a Excel

### 2. Crear Requerimiento
- Formulario completo en 2 columnas
- Búsqueda de productos por SKU
- Upload de hasta 5 fotos con preview
- Panel lateral con información y ayuda

### 3. Detalle de Requerimiento
- Información completa del requerimiento
- Timeline de historial
- Galería de fotos
- Acciones disponibles según estado
- Información de proveedor y respuesta

## ⚙️ Configuración

### Correo Electrónico

Para que funcione el envío de correos a proveedores, configure en `settings.py`:

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'tu_email@gmail.com'
EMAIL_HOST_PASSWORD = 'tu_contraseña'
DEFAULT_FROM_EMAIL = 'tu_email@gmail.com'
```

### Archivos Media

Las fotos se guardan en:
- Ruta: `media/requerimientos/fotos/YYYY/MM/DD/`
- Configuración en `settings.py`:
  ```python
  MEDIA_URL = '/media/'
  MEDIA_ROOT = BASE_DIR / 'media'
  ```

## 🔐 Permisos

- **Cualquier usuario**: Puede crear requerimientos desde su sucursal
- **Administradores**: Pueden gestionar todos los requerimientos
- **Superusuarios**: Acceso total al módulo

## 📝 Admin de Django

Los modelos están registrados en el admin de Django con:
- Filtros avanzados
- Búsqueda por múltiples campos
- Vistas inline para fotos e historial
- Campos readonly para auditoría

Acceso: `/admin/app/requerimiento/`

## 🛠️ Personalización

### Agregar Nuevos Tipos de Requerimiento

Editar en `models.py`:

```python
TIPO_REQUERIMIENTO_CHOICES = [
    ('GARANTIA', 'Garantía'),
    ('DEVOLUCION', 'Devolución'),
    ('TU_NUEVO_TIPO', 'Tu Descripción'),
]
```

### Agregar Nuevos Estados

Editar en `models.py`:

```python
ESTADO_REQUERIMIENTO_CHOICES = [
    ('PENDIENTE', 'Pendiente'),
    ('TU_NUEVO_ESTADO', 'Tu Descripción'),
]
```

## 📞 Soporte

Para problemas o dudas sobre el módulo:
- Revisar los logs de Django
- Verificar configuración de MEDIA_ROOT
- Verificar configuración de EMAIL
- Revisar permisos de usuario

## ✅ Checklist de Implementación

- [x] Modelos creados (Requerimiento, FotoRequerimiento, HistorialRequerimiento)
- [x] Admin de Django configurado
- [x] Vistas y APIs creadas
- [x] Templates HTML completados
- [x] URLs configuradas
- [x] Menú actualizado
- [x] Migraciones aplicadas
- [x] Pillow instalado
- [x] Documentación creada

## 🎯 Próximas Mejoras Sugeridas

1. **Notificaciones en tiempo real** con WebSockets
2. **App móvil** para crear requerimientos desde el campo
3. **Dashboard avanzado** con gráficos de tendencias
4. **Integración con WhatsApp** para notificaciones
5. **Generación automática de PDF** para imprimir requerimientos
6. **Firma digital** del cliente
7. **Código QR** para seguimiento del requerimiento
8. **Chat interno** entre sucursal y administrador

---

**Desarrollado para RetailMind** - Sistema de Gestión Retail Integral
**Fecha**: Noviembre 2024
**Versión**: 1.0.0

