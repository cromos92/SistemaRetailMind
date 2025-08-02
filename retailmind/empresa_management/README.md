# Sistema de Gestión de Empresas y Clientes - RetailMind

## Descripción

Este módulo proporciona un sistema completo de gestión de empresas y clientes para el proyecto RetailMind, incluyendo funcionalidades de CRUD, validaciones, logging, reportes y dashboards.

## Características Principales

### 🏢 Gestión de Empresas
- **CRUD completo** de empresas con validaciones
- **Validación de RUT chileno** automática
- **Tipos de empresa**: Cliente, Proveedor, Cliente y Proveedor
- **Gestión de sucursales** por empresa
- **Contactos empresariales** con diferentes tipos
- **Logging completo** de todas las operaciones
- **Exportación a CSV** con filtros

### 👥 Gestión de Clientes
- **CRUD completo** de clientes individuales
- **Validación de RUT chileno** opcional
- **Tipos de cliente**: Individual, Empresarial, Mayorista, Distribuidor
- **Asociación con empresas** opcional
- **Información demográfica** (género, fecha nacimiento)
- **Logging completo** de todas las operaciones
- **Reportes por empresa**

### 📊 Dashboards y Reportes
- **Dashboard de empresas** con estadísticas
- **Dashboard de clientes** con métricas
- **Reportes por empresa** con análisis detallado
- **Filtros avanzados** y búsqueda
- **Paginación** para grandes volúmenes de datos

### 🔒 Seguridad y Auditoría
- **Logging automático** de todas las operaciones
- **Trazabilidad completa** de cambios
- **Validaciones de seguridad** en formularios
- **Protección CSRF** en todas las operaciones
- **Control de acceso** basado en autenticación

## Instalación

### 1. Agregar la aplicación a INSTALLED_APPS

```python
# settings.py
INSTALLED_APPS = [
    # ... otras apps
    'empresa_management.apps.EmpresaManagementConfig',
]
```

### 2. Incluir las URLs

```python
# urls.py principal
from django.urls import path, include

urlpatterns = [
    # ... otras URLs
    path('empresa/', include('empresa_management.urls')),
]
```

### 3. Ejecutar migraciones

```bash
python manage.py makemigrations empresa_management
python manage.py migrate
```

### 4. Crear superusuario (opcional)

```bash
python manage.py createsuperuser
```

## Modelos

### Empresa
```python
class Empresa(models.Model):
    nombre = models.CharField(max_length=100)
    rut = models.CharField(max_length=20, validators=[...])
    nombre_fantasia = models.CharField(max_length=255)
    razon_social = models.CharField(max_length=255)
    giro = models.CharField(max_length=255)
    # ... más campos
```

### Cliente
```python
class Cliente(models.Model):
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    rut = models.CharField(max_length=20, validators=[...])
    email = models.EmailField()
    # ... más campos
```

### Sucursal
```python
class Sucursal(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    alias = models.CharField(max_length=100)
    direccion = models.CharField(max_length=255)
    # ... más campos
```

### ContactoEmpresa
```python
class ContactoEmpresa(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100)
    cargo = models.CharField(max_length=100)
    # ... más campos
```

## URLs Disponibles

### Empresas
- `GET /empresa/empresas/` - Lista de empresas
- `GET /empresa/empresas/dashboard/` - Dashboard de empresas
- `POST /empresa/empresas/crear/` - Crear empresa
- `POST /empresa/empresas/{id}/editar/` - Editar empresa
- `POST /empresa/empresas/{id}/eliminar/` - Eliminar empresa
- `POST /empresa/empresas/{id}/activar-desactivar/` - Activar/desactivar
- `GET /empresa/empresas/{id}/detalle/` - Detalle de empresa
- `GET /empresa/empresas/exportar/` - Exportar a CSV

### Clientes
- `GET /empresa/clientes/` - Lista de clientes
- `GET /empresa/clientes/dashboard/` - Dashboard de clientes
- `POST /empresa/clientes/crear/` - Crear cliente
- `POST /empresa/clientes/{id}/editar/` - Editar cliente
- `POST /empresa/clientes/{id}/eliminar/` - Eliminar cliente
- `POST /empresa/clientes/{id}/activar-desactivar/` - Activar/desactivar
- `GET /empresa/clientes/{id}/detalle/` - Detalle de cliente
- `GET /empresa/clientes/exportar/` - Exportar a CSV
- `GET /empresa/clientes/buscar/` - Búsqueda AJAX

### Sucursales y Contactos
- `POST /empresa/empresas/{id}/sucursales/crear/` - Crear sucursal
- `POST /empresa/sucursales/{id}/eliminar/` - Eliminar sucursal
- `POST /empresa/empresas/{id}/contactos/crear/` - Crear contacto
- `POST /empresa/contactos/{id}/eliminar/` - Eliminar contacto

## Uso de la API

### Crear Empresa
```javascript
fetch('/empresa/empresas/crear/', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
    },
    body: JSON.stringify({
        nombre: 'Empresa Ejemplo SPA',
        rut: '12.345.678-9',
        nombre_fantasia: 'Empresa Ejemplo',
        razon_social: 'Empresa Ejemplo Sociedad Por Acciones',
        giro: 'Comercio al por menor',
        direccion: 'Av. Principal 123',
        comuna: 'Santiago',
        ciudad: 'Santiago',
        tipo_empresa: 'CLIENTE',
        email: 'contacto@empresa.cl',
        telefono: '+56 2 2345 6789'
    })
})
.then(response => response.json())
.then(data => {
    if (data.success) {
        console.log('Empresa creada:', data.message);
    } else {
        console.error('Error:', data.message);
    }
});
```

### Crear Cliente
```javascript
fetch('/empresa/clientes/crear/', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
    },
    body: JSON.stringify({
        nombre: 'Juan',
        apellido: 'Pérez',
        rut: '12.345.678-9',
        email: 'juan.perez@email.com',
        telefono: '+56 9 1234 5678',
        tipo_cliente: 'INDIVIDUAL',
        empresa_id: 1  // opcional
    })
})
.then(response => response.json())
.then(data => {
    if (data.success) {
        console.log('Cliente creado:', data.message);
    } else {
        console.error('Error:', data.message);
    }
});
```

## Validaciones

### RUT Chileno
El sistema incluye validación automática de RUT chileno con el siguiente formato:
- Formato: `12.345.678-9` o `12345678-9`
- Validación del dígito verificador
- Verificación de unicidad

### Email
- Validación de formato de email
- Verificación de unicidad opcional

### Campos Requeridos
- **Empresa**: nombre, rut, giro, direccion, comuna, ciudad
- **Cliente**: nombre, apellido

## Logging y Auditoría

### LogEmpresa
Registra todas las operaciones realizadas en empresas:
- Creación, edición, eliminación
- Activación/desactivación
- Datos anteriores y nuevos
- Usuario que realizó la acción
- IP y User Agent

### LogCliente
Registra todas las operaciones realizadas en clientes:
- Creación, edición, eliminación
- Activación/desactivación
- Datos anteriores y nuevos
- Usuario que realizó la acción
- IP y User Agent

## Admin de Django

El sistema incluye configuración completa del admin de Django con:

### EmpresaAdmin
- Lista con filtros y búsqueda
- Inlines para sucursales, contactos y clientes
- Fieldsets organizados por categorías
- Validaciones automáticas

### ClienteAdmin
- Lista con filtros y búsqueda
- Enlaces a empresas asociadas
- Fieldsets organizados por categorías
- Validaciones automáticas

### LogAdmin
- Visualización de logs (solo lectura)
- Filtros por acción, fecha, usuario
- Búsqueda en descripciones

## Configuración Adicional

### Personalización de Validaciones
```python
# models.py
def clean(self):
    from django.core.exceptions import ValidationError
    
    # Validación personalizada
    if self.rut and not validar_rut_chileno(self.rut):
        raise ValidationError({'rut': 'RUT inválido'})
```

### Personalización de Permisos
```python
# views.py
from django.contrib.auth.decorators import login_required, permission_required

@login_required
@permission_required('empresa_management.add_empresa')
def crear_empresa(request):
    # ... código
```

## Troubleshooting

### Error de Migración
Si encuentras errores de migración:
```bash
python manage.py makemigrations empresa_management --empty
python manage.py makemigrations empresa_management
python manage.py migrate
```

### Error de Validación de RUT
Verifica que el RUT tenga el formato correcto:
- Formato: `12.345.678-9`
- Dígito verificador válido
- No duplicado en la base de datos

### Error de CSRF
Asegúrate de incluir el token CSRF en las peticiones AJAX:
```javascript
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}
```

## Contribución

Para contribuir al desarrollo:

1. Fork el repositorio
2. Crea una rama para tu feature
3. Implementa los cambios
4. Agrega tests
5. Envía un pull request

## Licencia

Este módulo es parte del proyecto RetailMind y está sujeto a la misma licencia.

## Soporte

Para soporte técnico o preguntas:
- Crear un issue en el repositorio
- Contactar al equipo de desarrollo
- Revisar la documentación de Django 