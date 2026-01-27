# 🔧 Solución: Error al enviar correos en Producción

## ❌ Problema

Al intentar recuperar contraseña en producción (`https://retail.webappsolutions.cl/app/verResetPassword/`), se recibe el error:

```json
{
    "success": false,
    "errors": "Error al enviar el correo. Intenta de nuevo."
}
```

## 🔍 Diagnóstico

El error ocurre en la línea **4647** de `views.py` cuando intenta enviar el correo mediante `send_mail()`.

### ✅ **ACTUALIZACIÓN: Configuración modificada**

Ya he actualizado tu `settings.py` para que **use variables de entorno**. Ahora solo necesitas:

1. **Configurar las variables en tu plataforma de hosting**
2. **Reiniciar el servidor**

Ver archivo: `CONFIGURAR_EMAIL_PRODUCCION.md` para instrucciones completas.

### Causas del error (antes de la configuración):

1. **Credenciales SMTP incorrectas o expiradas**
2. **Firewall bloqueando el puerto 587**
3. **Dominio no verificado en MailerSend**
4. **Variables de entorno no configuradas en producción**
5. **Límite de envío excedido**

---

## ✅ Soluciones

### **SOLUCIÓN 1: Verificar logs del servidor en producción** 🔴 URGENTE

Primero, necesitas ver el error exacto en los logs de producción:

```bash
# Si usas Railway o similar
railway logs --tail

# O si tienes acceso SSH
tail -f /var/log/tu-aplicacion/django.log
```

El error debe aparecer con un mensaje más específico como:
- `SMTPAuthenticationError: (535, b'Authentication failed')`
- `SMTPConnectError: (421, b'Service not available')`
- `SMTPException: Domain not verified`

---

### **SOLUCIÓN 2: Verificar configuración SMTP en producción**

#### Archivo: `retailmind/settings.py` (líneas 227-233)

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.mailersend.net'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'MS_hBDdVA@test-zkq340eke90gd796.mlsender.net'
EMAIL_HOST_PASSWORD = 'mssp.6Ju4Glc.7dnvo4do7m6g5r86.ioWUg6N'
DEFAULT_FROM_EMAIL = 'MS_hBDdVA@test-zkq340eke90gd796.mlsender.net'
```

#### ⚠️ IMPORTANTE:

Esta configuración usa credenciales **hardcodeadas** directamente en el código. Esto funciona localmente pero puede fallar en producción si:

1. **El dominio no está verificado** en MailerSend
2. **Las credenciales expiraron** o fueron revocadas
3. **El token es de PRUEBA** (test-zkq340eke90gd796) y no de producción

---

### **SOLUCIÓN 3: Configurar variables de entorno** ⭐ RECOMENDADO

#### Paso 1: Modificar `settings.py`

```python
import os

# ===== CONFIGURACIÓN DE CORREO =====
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER)

# Logging de errores de correo
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': 'email_errors.log',
        },
    },
    'loggers': {
        'django.core.mail': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': True,
        },
    },
}
```

#### Paso 2: Configurar variables en Railway/Render

En tu plataforma de hosting, agregar estas variables:

**OPCIÓN A: Gmail** (Más confiable)

```bash
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=tu-email@gmail.com
EMAIL_HOST_PASSWORD=xxxx-xxxx-xxxx-xxxx  # App Password, NO tu contraseña normal
DEFAULT_FROM_EMAIL=tu-email@gmail.com
```

**Cómo generar App Password de Gmail:**
1. Ve a https://myaccount.google.com/security
2. Habilita verificación en 2 pasos
3. Ve a "Contraseñas de aplicaciones"
4. Genera una nueva para "Correo"
5. Copia el código de 16 dígitos

**OPCIÓN B: MailerSend** (Si prefieres usar MailerSend)

```bash
EMAIL_HOST=smtp.mailersend.net
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=MS_xxxxxx@trial-xxxxx.mlsender.net
EMAIL_HOST_PASSWORD=tu_token_de_mailersend
DEFAULT_FROM_EMAIL=MS_xxxxxx@trial-xxxxx.mlsender.net
```

**⚠️ IMPORTANTE para MailerSend:**
- Verifica tu dominio en https://app.mailersend.com/domains
- Usa un token de **PRODUCCIÓN**, no de prueba
- Asegúrate de tener límites disponibles

---

### **SOLUCIÓN 4: Test rápido para debug**

Agrega esta vista temporal para probar el correo directamente:

```python
# En views.py
def test_email(request):
    """Vista temporal para probar configuración de correo"""
    from django.core.mail import send_mail
    from django.conf import settings
    from django.http import JsonResponse
    
    try:
        send_mail(
            subject='Test de correo NEXO',
            message='Este es un test de correo.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=['tu-email@gmail.com'],  # Cambia esto
            fail_silently=False
        )
        return JsonResponse({
            'success': True,
            'message': 'Correo enviado exitosamente',
            'config': {
                'EMAIL_HOST': settings.EMAIL_HOST,
                'EMAIL_PORT': settings.EMAIL_PORT,
                'EMAIL_HOST_USER': settings.EMAIL_HOST_USER,
                'DEFAULT_FROM_EMAIL': settings.DEFAULT_FROM_EMAIL,
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e),
            'type': type(e).__name__
        }, status=500)

# En urls.py
urlpatterns = [
    # ... otras rutas
    path('test-email/', views.test_email, name='test_email'),
]
```

Luego visita: `https://retail.webappsolutions.cl/test-email/`

---

### **SOLUCIÓN 5: Alternativa - Usar servicio de terceros**

Si nada funciona, puedes usar servicios más robustos:

#### **SendGrid** (Gratuito hasta 100 correos/día)

```python
# pip install sendgrid

EMAIL_BACKEND = 'sendgrid_backend.SendgridBackend'
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
```

#### **Amazon SES** (0.10 USD por 1000 correos)

```python
EMAIL_BACKEND = 'django_ses.SESBackend'
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_SES_REGION_NAME = 'us-east-1'
AWS_SES_REGION_ENDPOINT = 'email.us-east-1.amazonaws.com'
```

---

## 🚀 Plan de Acción Inmediato

### 1. **Debug del error (5 min)**
```bash
# En producción, ver los logs
railway logs --tail
# O
heroku logs --tail
```

### 2. **Solución rápida (10 min)**
- Cambiar a Gmail con App Password
- Configurar variables de entorno
- Reiniciar el servidor

### 3. **Verificación (2 min)**
- Visitar `/test-email/` para confirmar
- Probar recuperación de contraseña real

---

## 📋 Checklist de Verificación

- [ ] Ver logs de producción para identificar error exacto
- [ ] Verificar que las credenciales SMTP sean válidas
- [ ] Confirmar que el dominio está verificado (si usas MailerSend)
- [ ] Comprobar que el puerto 587 no esté bloqueado
- [ ] Configurar variables de entorno en hosting
- [ ] Reiniciar servidor después de cambios
- [ ] Probar con `/test-email/`
- [ ] Probar flujo completo de recuperación

---

## 🆘 Si nada funciona

1. **Contactar soporte de MailerSend**: https://www.mailersend.com/help
2. **Migrar a Gmail temporalmente** (más confiable para empezar)
3. **Revisar firewall del servidor** (puede bloquear puerto 587)

---

## 📞 Necesitas ayuda adicional?

Dame acceso a:
1. Los logs completos del error
2. Tu plataforma de hosting (Railway/Render/etc.)
3. Confirmación de si tienes dominio verificado

Y te ayudo a resolverlo paso a paso.
