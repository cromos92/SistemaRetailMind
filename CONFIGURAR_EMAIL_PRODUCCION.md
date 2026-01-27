# 📧 Guía: Configurar Variables de Entorno para Email en Producción

## ✅ Cambios Realizados

He modificado el archivo `settings.py` para que **ahora use variables de entorno** en lugar de credenciales hardcodeadas.

### Antes (❌ Hardcodeado):
```python
EMAIL_HOST = 'smtp.mailersend.net'
EMAIL_HOST_USER = 'MS_hBDdVA@test-zkq340eke90gd796.mlsender.net'
EMAIL_HOST_PASSWORD = 'mssp.6Ju4Glc.7dnvo4do7m6g5r86.ioWUg6N'
```

### Ahora (✅ Variables de entorno):
```python
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.mailersend.net')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'MS_hBDdVA@...')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', 'mssp...')
```

---

## 🚀 Cómo Configurar en Producción

### **OPCIÓN 1: Gmail (Recomendado)** ⭐

#### Paso 1: Generar App Password de Gmail

1. Ve a https://myaccount.google.com/security
2. Habilita **"Verificación en 2 pasos"**
3. Busca **"Contraseñas de aplicaciones"**
4. Selecciona **"Correo"** y genera una nueva
5. Copia el código de 16 dígitos (formato: `xxxx xxxx xxxx xxxx`)

#### Paso 2: Configurar en Railway/Render

En tu plataforma de hosting, agrega estas variables:

```bash
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=tu-email@gmail.com
EMAIL_HOST_PASSWORD=xxxx xxxx xxxx xxxx
DEFAULT_FROM_EMAIL=tu-email@gmail.com
```

#### Paso 3: Reiniciar el servidor

Después de agregar las variables, **reinicia el servidor** para que tome los cambios.

---

### **OPCIÓN 2: MailerSend** (Si prefieres seguir usándolo)

#### Paso 1: Verificar dominio

1. Ve a https://app.mailersend.com/domains
2. Agrega y verifica tu dominio
3. Configura los registros DNS (SPF, DKIM, etc.)

#### Paso 2: Generar token de PRODUCCIÓN

1. Ve a https://app.mailersend.com/settings/api-keys
2. Crea un nuevo **API Key** (no uses el de prueba)
3. Copia el token SMTP

#### Paso 3: Configurar variables

```bash
EMAIL_HOST=smtp.mailersend.net
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=MS_xxxxx@tudominio.mlsender.net
EMAIL_HOST_PASSWORD=tu_token_nuevo
DEFAULT_FROM_EMAIL=MS_xxxxx@tudominio.mlsender.net
```

⚠️ **IMPORTANTE**: Reemplaza el token de **PRUEBA** (`test-zkq340eke90gd796`) por uno de **PRODUCCIÓN**.

---

## 🎯 Cómo Agregar Variables según tu Plataforma

### **Railway**
```bash
# Opción 1: Desde la web
1. Ve a tu proyecto en Railway
2. Click en tu servicio
3. Ve a "Variables"
4. Click "New Variable"
5. Agrega cada variable (EMAIL_HOST, EMAIL_PORT, etc.)
6. El servidor se reiniciará automáticamente

# Opción 2: Desde CLI
railway variables set EMAIL_HOST=smtp.gmail.com
railway variables set EMAIL_PORT=587
railway variables set EMAIL_USE_TLS=True
railway variables set EMAIL_HOST_USER=tu-email@gmail.com
railway variables set EMAIL_HOST_PASSWORD="xxxx xxxx xxxx xxxx"
railway variables set DEFAULT_FROM_EMAIL=tu-email@gmail.com
```

### **Render**
```bash
# Desde el dashboard web:
1. Ve a tu servicio
2. Environment → Environment Variables
3. Add Environment Variable
4. Agrega cada variable
5. Click "Save Changes"
```

### **Heroku**
```bash
# Desde CLI:
heroku config:set EMAIL_HOST=smtp.gmail.com --app tu-app
heroku config:set EMAIL_PORT=587 --app tu-app
heroku config:set EMAIL_USE_TLS=True --app tu-app
heroku config:set EMAIL_HOST_USER=tu-email@gmail.com --app tu-app
heroku config:set EMAIL_HOST_PASSWORD="xxxx xxxx xxxx xxxx" --app tu-app
heroku config:set DEFAULT_FROM_EMAIL=tu-email@gmail.com --app tu-app
```

### **Vercel**
```bash
# Desde el dashboard:
1. Ve a tu proyecto
2. Settings → Environment Variables
3. Add New Variable
4. Agrega cada variable
5. Redeploy la aplicación
```

---

## 🧪 Verificar la Configuración

### Método 1: Ver variables configuradas

```bash
# Railway
railway variables

# Heroku
heroku config --app tu-app

# Render
# (Solo desde el dashboard web)
```

### Método 2: Agregar endpoint de prueba

Agrega esta vista temporal en `views.py`:

```python
def test_email_config(request):
    """Vista temporal para probar configuración de correo"""
    from django.core.mail import send_mail
    from django.conf import settings
    from django.http import JsonResponse
    import traceback
    
    try:
        # Intentar enviar correo
        send_mail(
            subject='🧪 Test de correo NEXO',
            message='Este es un correo de prueba desde producción.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.EMAIL_HOST_USER],  # Se envía a sí mismo
            fail_silently=False
        )
        
        return JsonResponse({
            'success': True,
            'message': '✅ Correo enviado exitosamente',
            'config': {
                'EMAIL_HOST': settings.EMAIL_HOST,
                'EMAIL_PORT': settings.EMAIL_PORT,
                'EMAIL_HOST_USER': settings.EMAIL_HOST_USER,
                'EMAIL_USE_TLS': settings.EMAIL_USE_TLS,
                'DEFAULT_FROM_EMAIL': settings.DEFAULT_FROM_EMAIL,
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e),
            'error_type': type(e).__name__,
            'traceback': traceback.format_exc()
        }, status=500)
```

Luego en `urls.py`:

```python
urlpatterns = [
    # ... otras rutas
    path('test-email-config/', views.test_email_config, name='test_email_config'),
]
```

Visita: `https://retail.webappsolutions.cl/test-email-config/`

---

## 📋 Checklist de Migración

- [x] Modificar `settings.py` para usar variables de entorno
- [x] Actualizar archivo `.env` local con las variables
- [x] Crear `.env.example` como plantilla
- [ ] **Generar App Password de Gmail** (si usas Gmail)
- [ ] **Configurar variables en producción** (Railway/Render/etc.)
- [ ] **Reiniciar servidor de producción**
- [ ] **Probar endpoint `/test-email-config/`**
- [ ] **Probar recuperación de contraseña real**
- [ ] **Eliminar endpoint de prueba** (después de verificar)

---

## 🔧 Troubleshooting

### Error: "Authentication failed"
```
✗ Problema: Credenciales incorrectas
✓ Solución: 
  - Gmail: Verifica que usaste App Password, NO tu contraseña normal
  - MailerSend: Genera un token nuevo de producción
```

### Error: "Connection refused"
```
✗ Problema: Puerto bloqueado o host incorrecto
✓ Solución:
  - Verifica que EMAIL_HOST sea correcto
  - Asegúrate que el firewall no bloquee puerto 587
  - Prueba con puerto 465 (SSL) si 587 no funciona
```

### Error: "Domain not verified"
```
✗ Problema: Dominio no verificado en MailerSend
✓ Solución:
  - Ve a https://app.mailersend.com/domains
  - Verifica tu dominio
  - Configura registros DNS (SPF, DKIM)
  - O cambia a Gmail que no requiere verificación
```

### Las variables no se aplican
```
✗ Problema: El servidor no se reinició
✓ Solución:
  - Reinicia manualmente el servidor
  - Verifica que las variables estén en el entorno correcto
  - Asegúrate de no tener typos en los nombres
```

---

## 📞 Resumen Rápido

### Para usar Gmail (5 minutos):
1. Genera App Password: https://myaccount.google.com/security
2. Configura 6 variables en tu plataforma de hosting
3. Reinicia el servidor
4. Prueba con `/test-email-config/`
5. ✅ Listo!

### Para MailerSend:
1. Verifica tu dominio: https://app.mailersend.com/domains
2. Genera token de producción
3. Configura variables
4. Reinicia servidor
5. ✅ Listo!

---

## 💡 Recomendación Final

**Usa Gmail para empezar**, es más simple y confiable:
- ✅ No requiere verificación de dominio
- ✅ 500 correos/día gratis
- ✅ Setup en 5 minutos
- ✅ Muy confiable

Luego, si necesitas más volumen, migra a MailerSend o SendGrid.
