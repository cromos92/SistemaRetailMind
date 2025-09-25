# Despliegue en Railway - RetailMind

## Preparación del Proyecto

El proyecto ya está configurado para Railway con los siguientes archivos:

- `Procfile` - Comandos de Railway
- `railway.json` - Configuración de Railway
- `nixpacks.toml` - Configuración de build
- `requirements.txt` - Dependencias de Python
- `env.example` - Variables de entorno de ejemplo

## Pasos para Desplegar en Railway

### 1. Crear cuenta en Railway
- Ve a [railway.app](https://railway.app)
- Crea una cuenta o inicia sesión

### 2. Crear nuevo proyecto
- Haz clic en "New Project"
- Selecciona "Deploy from GitHub repo"
- Conecta tu repositorio de GitHub

### 3. Configurar Base de Datos
- En tu proyecto de Railway, haz clic en "Add Service"
- Selecciona "PostgreSQL"
- Railway creará automáticamente la variable `DATABASE_URL`

### 4. Configurar Variables de Entorno
En la sección "Variables" de tu proyecto, agrega:

```
SECRET_KEY=tu-clave-secreta-muy-segura-aqui
DEBUG=False
ALLOWED_HOSTS=tu-dominio.railway.app
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=tu-email@gmail.com
EMAIL_HOST_PASSWORD=tu-password-de-app
```

### 5. Generar SECRET_KEY
Puedes generar una nueva SECRET_KEY ejecutando:
```python
from django.core.management.utils import get_random_secret_key
print(get_random_secret_key())
```

### 6. Desplegar
- Railway detectará automáticamente que es un proyecto Django
- El build se ejecutará automáticamente
- Las migraciones se ejecutarán automáticamente

### 7. Configurar Dominio (Opcional)
- En la sección "Settings" > "Domains"
- Puedes configurar un dominio personalizado

## Variables de Entorno Importantes

### Automáticas (Railway las configura):
- `DATABASE_URL` - URL de conexión a PostgreSQL
- `RAILWAY_ENVIRONMENT` - Indica que está en Railway
- `RAILWAY_PUBLIC_DOMAIN` - Dominio público de la app

### Manuales (debes configurar):
- `SECRET_KEY` - Clave secreta de Django
- `DEBUG` - Debe ser `False` en producción
- `EMAIL_HOST_USER` - Tu email para envío de correos
- `EMAIL_HOST_PASSWORD` - Password de aplicación de Gmail

## Comandos Útiles

### Ver logs:
```bash
railway logs
```

### Ejecutar comandos en Railway:
```bash
railway run python manage.py createsuperuser
railway run python manage.py collectstatic
```

### Conectar a la base de datos:
```bash
railway connect postgresql
```

## Notas Importantes

1. **Archivos estáticos**: Se sirven con WhiteNoise
2. **Base de datos**: PostgreSQL proporcionada por Railway
3. **Migraciones**: Se ejecutan automáticamente en cada deploy
4. **SSL**: Railway proporciona HTTPS automáticamente
5. **Logs**: Disponibles en el dashboard de Railway

## Solución de Problemas

### Error de CSRF:
Si tienes errores de CSRF, verifica que `RAILWAY_PUBLIC_DOMAIN` esté en `CSRF_TRUSTED_ORIGINS`

### Error de archivos estáticos:
Ejecuta `python manage.py collectstatic` manualmente si es necesario

### Error de base de datos:
Verifica que las migraciones se hayan ejecutado correctamente
