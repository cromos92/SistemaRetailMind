# 🚀 Sistema de Gestión de Usuarios - Olagreetings

Sistema completo de gestión de usuarios para Django con validación de RUT chileno, envío de correos automáticos, logs de acceso y permisos granulares.

## ✨ Características Principales

### 🔐 **Seguridad y Autenticación**
- ✅ Modelo de usuario personalizado con campos extendidos
- ✅ Validación completa de RUT chileno
- ✅ Sistema de permisos granulares
- ✅ Logs automáticos de acceso
- ✅ Bloqueo por intentos fallidos
- ✅ Headers de seguridad automáticos

### 📧 **Notificaciones por Correo**
- ✅ Envío automático de credenciales al crear usuarios
- ✅ Reset de contraseñas con envío por correo
- ✅ Templates HTML y texto plano
- ✅ Configuración flexible de SMTP

### 🎛️ **Gestión de Usuarios**
- ✅ CRUD completo de usuarios
- ✅ Activación/desactivación de cuentas
- ✅ Gestión de permisos individuales
- ✅ Búsqueda y filtros avanzados
- ✅ Exportación a CSV
- ✅ Paginación optimizada

### 📊 **Monitoreo y Auditoría**
- ✅ Logs detallados de acceso
- ✅ Métricas de usuarios
- ✅ Auditoría de acciones importantes
- ✅ Reportes automáticos

## 📋 Requisitos

- Python 3.8+
- Django 4.0+
- Base de datos compatible (PostgreSQL, MySQL, SQLite)

## 🛠️ Instalación

### 1. **Configurar el Modelo de Usuario**

En tu `settings.py` principal:

```python
# Configuración del modelo de usuario personalizado
AUTH_USER_MODEL = 'usuarios.Usuario'

# Configuración de correo electrónico
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'  # Cambiar según tu proveedor
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'tu-email@gmail.com'
EMAIL_HOST_PASSWORD = 'tu-password-app'
DEFAULT_FROM_EMAIL = 'Olagreetings <tu-email@gmail.com>'

# Configuración de seguridad
PASSWORD_RESET_TIMEOUT = 86400  # 24 horas
SESSION_COOKIE_AGE = 3600  # 1 hora
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
```

### 2. **Agregar URLs**

En tu `urls.py` principal:

```python
from django.urls import path, include

urlpatterns = [
    # ... otras URLs ...
    path('usuarios/', include('usuarios.urls')),
]
```

### 3. **Configurar Middleware**

En tu `settings.py`:

```python
MIDDLEWARE = [
    # ... otros middleware ...
    'usuarios.middleware.LogAccesoMiddleware',
    'usuarios.middleware.SeguridadMiddleware',
    'usuarios.middleware.BloqueoIntentosFallidosMiddleware',
    'usuarios.middleware.AuditoriaMiddleware',
]
```

### 4. **Ejecutar Migraciones**

```bash
python manage.py makemigrations usuarios
python manage.py migrate
```

### 5. **Crear Superusuario**

```bash
python manage.py crear_superusuario_olagreetings --username admin --email admin@olagreetings.com --first-name Administrador --last-name Sistema --empresa Olagreetings
```

## 🎯 Uso del Sistema

### **Acceso a la Gestión de Usuarios**

1. Inicia sesión como superusuario o usuario con permisos
2. Navega a `/usuarios/gestion/`
3. Gestiona usuarios desde la interfaz web

### **Crear un Nuevo Usuario**

1. Haz clic en "Crear Usuario"
2. Completa los campos obligatorios:
   - Nombre de usuario
   - Email
   - Nombre y apellido
   - RUT (opcional, con validación)
3. Configura los permisos necesarios
4. Guarda el usuario
5. Las credenciales se enviarán automáticamente por correo

### **Gestionar Usuarios Existentes**

- **Editar**: Modifica información del usuario
- **Resetear Password**: Genera nueva contraseña y envía por correo
- **Activar/Desactivar**: Cambia el estado de la cuenta
- **Eliminar**: Elimina el usuario (con validaciones)

### **Exportar Usuarios**

1. Haz clic en "Exportar CSV"
2. Se descargará un archivo con todos los usuarios
3. Incluye información completa excepto contraseñas

## 🔧 Configuración Avanzada

### **Configuración de Correo**

Para Gmail, necesitas una "Contraseña de aplicación":

1. Ve a tu cuenta de Google
2. Seguridad > Verificación en dos pasos
3. Contraseñas de aplicación
4. Genera una nueva contraseña para la aplicación

### **Configuración de Permisos**

Los permisos se pueden configurar individualmente:

- `puede_crear_usuarios`: Crear nuevos usuarios
- `puede_editar_usuarios`: Editar usuarios existentes
- `puede_eliminar_usuarios`: Eliminar usuarios
- `is_staff`: Acceso al admin de Django
- `is_superuser`: Todos los permisos

### **Validación de RUT**

El sistema incluye validación completa de RUT chileno:

- Formato: 12345678-9 o 1234567-K
- Validación de dígito verificador
- Limpieza automática de puntos y guiones

## 📊 Logs y Auditoría

### **Logs de Acceso**

Se registran automáticamente:

- Usuario que accede
- Fecha y hora
- Dirección IP
- User Agent
- Éxito o fallo del acceso

### **Auditoría de Acciones**

Se registran las siguientes acciones:

- Creación de usuarios
- Edición de usuarios
- Eliminación de usuarios
- Reset de contraseñas
- Cambios de estado

## 🔒 Seguridad

### **Medidas Implementadas**

- Headers de seguridad automáticos
- Bloqueo por intentos fallidos
- Validación de RUT
- Logs de auditoría
- Permisos granulares
- Contraseñas temporales seguras

### **Configuración de Seguridad**

```python
# Headers de seguridad
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: [configurado automáticamente]
```

## 📧 Templates de Correo

### **Personalización**

Los templates están en:
- `templates/usuarios/emails/credenciales_usuario.html`
- `templates/usuarios/emails/nueva_password.html`

### **Variables Disponibles**

- `{{ usuario }}`: Objeto del usuario
- `{{ password }}`: Contraseña temporal
- `{{ fecha }}`: Fecha de la acción
- `{{ request.scheme }}://{{ request.get_host }}`: URL del sistema

## 🚨 Solución de Problemas

### **Error de Correo**

1. Verifica la configuración SMTP
2. Asegúrate de usar contraseña de aplicación (Gmail)
3. Revisa los logs de Django

### **Error de Validación de RUT**

1. Verifica el formato del RUT
2. Asegúrate de que el dígito verificador sea correcto
3. Revisa que no esté duplicado

### **Error de Permisos**

1. Verifica que el usuario tenga los permisos necesarios
2. Asegúrate de que sea superusuario si es necesario
3. Revisa la configuración de permisos

## 📈 Métricas Disponibles

- Total de usuarios
- Usuarios activos/inactivos
- Superusuarios
- Usuarios nuevos del mes
- Últimos accesos

## 🔄 Comandos de Gestión

### **Crear Superusuario**

```bash
python manage.py crear_superusuario_olagreetings --username admin --email admin@olagreetings.com
```

### **Limpiar Logs Antiguos**

```bash
python manage.py shell
>>> from usuarios.models import LogAcceso
>>> from django.utils import timezone
>>> from datetime import timedelta
>>> LogAcceso.objects.filter(fecha_acceso__lt=timezone.now() - timedelta(days=90)).delete()
```

## 🤝 Contribución

1. Fork el proyecto
2. Crea una rama para tu feature
3. Commit tus cambios
4. Push a la rama
5. Abre un Pull Request

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Ver el archivo `LICENSE` para más detalles.

## 📞 Soporte

Para soporte técnico o preguntas:

- Email: soporte@olagreetings.com
- Documentación: [docs.olagreetings.com](https://docs.olagreetings.com)
- Issues: [GitHub Issues](https://github.com/olagreetings/usuarios/issues)

---

**¡Disfruta usando el Sistema de Gestión de Usuarios de Olagreetings! 🎉** 