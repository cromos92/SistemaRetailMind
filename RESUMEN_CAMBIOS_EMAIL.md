# 📧 RESUMEN: Configuración de Email - Cambios Realizados

## ✅ Lo que hice

He analizado tu configuración de email y realizado los siguientes cambios:

### 1. **Modificado `settings.py`** ✅
- **Antes**: Credenciales hardcodeadas directamente en el código
- **Ahora**: Usa variables de entorno con `os.environ.get()`

**Archivo modificado**: `retailmind/retailmind/settings.py` (líneas 226-254)

### 2. **Actualizado `.env`** ✅
Agregué las variables de email al archivo `.env` local con valores por defecto.

### 3. **Creado `.env.example`** ✅
Plantilla documentada para que sepas qué variables configurar.

### 4. **Creado guías completas** ✅
- `CONFIGURAR_EMAIL_PRODUCCION.md` - Instrucciones paso a paso
- `GUIA_SOLUCION_ERROR_CORREO_PRODUCCION.md` - Solución al error actual

---

## 🎯 Qué debes hacer AHORA en producción

### **PASO 1: Configura las variables en tu hosting**

En tu plataforma (Railway/Render/Heroku), agrega estas 6 variables:

#### **OPCIÓN A: Gmail** (Recomendado - 5 minutos)

```bash
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=tu-email@gmail.com
EMAIL_HOST_PASSWORD=xxxx xxxx xxxx xxxx  # App Password
DEFAULT_FROM_EMAIL=tu-email@gmail.com
```

**Cómo generar App Password:**
1. https://myaccount.google.com/security
2. Habilita verificación en 2 pasos
3. "Contraseñas de aplicaciones" → Genera una nueva
4. Copia el código de 16 dígitos

#### **OPCIÓN B: MailerSend** (Requiere configuración)

```bash
EMAIL_HOST=smtp.mailersend.net
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=MS_xxxxx@tudominio.mlsender.net
EMAIL_HOST_PASSWORD=tu_token_produccion
DEFAULT_FROM_EMAIL=MS_xxxxx@tudominio.mlsender.net
```

⚠️ **IMPORTANTE**: 
- Usa un token de **PRODUCCIÓN**, no de prueba
- Verifica tu dominio en https://app.mailersend.com/domains

---

### **PASO 2: Reinicia el servidor**

Después de agregar las variables, **reinicia tu servidor** en la plataforma de hosting.

---

### **PASO 3: Prueba la configuración**

Visita: `https://retail.webappsolutions.cl/app/verResetPassword/`

Si funciona: ✅ Correo configurado correctamente

Si aún falla: Ve a **Troubleshooting** abajo.

---

## 📝 Cómo agregar variables según tu plataforma

### **Railway**
```bash
1. Ve a tu proyecto → Variables
2. Click "New Variable"
3. Agrega: EMAIL_HOST = smtp.gmail.com
4. Repite para las otras 5 variables
5. El servidor se reinicia automáticamente
```

### **Render**
```bash
1. Ve a tu servicio → Environment
2. Add Environment Variable
3. Agrega cada variable
4. Click "Save Changes"
```

### **Heroku**
```bash
heroku config:set EMAIL_HOST=smtp.gmail.com --app tu-app
heroku config:set EMAIL_PORT=587 --app tu-app
heroku config:set EMAIL_USE_TLS=True --app tu-app
heroku config:set EMAIL_HOST_USER=tu-email@gmail.com --app tu-app
heroku config:set EMAIL_HOST_PASSWORD="xxxx xxxx xxxx xxxx" --app tu-app
heroku config:set DEFAULT_FROM_EMAIL=tu-email@gmail.com --app tu-app
```

---

## 🔧 Troubleshooting

### Si el error persiste:

#### 1. **Verifica que las variables estén configuradas**
```bash
# Railway
railway variables

# Heroku
heroku config --app tu-app
```

#### 2. **Agrega endpoint de prueba temporal**

En `views.py`, agrega:

```python
def test_email_debug(request):
    """Vista temporal para debug de email"""
    from django.core.mail import send_mail
    from django.conf import settings
    from django.http import JsonResponse
    import traceback
    
    try:
        send_mail(
            subject='Test NEXO',
            message='Test desde producción',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.EMAIL_HOST_USER],
            fail_silently=False
        )
        return JsonResponse({
            'success': True,
            'message': 'Correo enviado',
            'config': {
                'EMAIL_HOST': settings.EMAIL_HOST,
                'EMAIL_PORT': settings.EMAIL_PORT,
                'EMAIL_HOST_USER': settings.EMAIL_HOST_USER,
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

En `urls.py`:
```python
path('test-email-debug/', views.test_email_debug, name='test_email_debug'),
```

Visita: `https://retail.webappsolutions.cl/test-email-debug/`

Esto te mostrará el **error exacto** y la configuración actual.

#### 3. **Revisa los logs de producción**

```bash
# Railway
railway logs --tail

# Heroku
heroku logs --tail --app tu-app

# Render
# Ve al dashboard → Logs
```

Los logs mostrarán el error específico de SMTP.

---

## 📋 Checklist Final

- [x] ✅ Modificado `settings.py` para usar variables de entorno
- [x] ✅ Actualizado `.env` local
- [x] ✅ Creado documentación completa
- [ ] ⏳ **Generar App Password de Gmail** (o token MailerSend)
- [ ] ⏳ **Configurar 6 variables en producción**
- [ ] ⏳ **Reiniciar servidor de producción**
- [ ] ⏳ **Probar recuperación de contraseña**

---

## 💡 Mi Recomendación

**Usa Gmail para empezar**, es lo más rápido y confiable:

1. ✅ Setup en 5 minutos
2. ✅ No requiere verificación de dominio
3. ✅ 500 correos/día gratis
4. ✅ Muy confiable y estable

Sigue estos pasos:
1. Ve a https://myaccount.google.com/security
2. Habilita verificación en 2 pasos
3. Genera App Password
4. Configura las 6 variables en tu hosting
5. Reinicia el servidor
6. ✅ Listo!

---

## 📚 Archivos de Referencia

1. **`CONFIGURAR_EMAIL_PRODUCCION.md`** - Guía paso a paso completa
2. **`GUIA_SOLUCION_ERROR_CORREO_PRODUCCION.md`** - Solución detallada del error
3. **`.env.example`** - Plantilla de variables de entorno
4. **`.env`** - Tu archivo local (ya actualizado)

---

## ❓ Preguntas Frecuentes

### ¿Por qué mi token de MailerSend no funciona?

El token actual es de **PRUEBA** (`test-zkq340eke90gd796`). Necesitas:
1. Verificar tu dominio en MailerSend
2. Generar un token de **PRODUCCIÓN**
3. Configurar registros DNS

**Es más fácil usar Gmail para empezar.**

### ¿Necesito cambiar algo en el código?

**NO**, ya modifiqué todo lo necesario. Solo necesitas:
1. Configurar variables en tu hosting
2. Reiniciar el servidor

### ¿Funcionará en local?

**SÍ**, porque dejé valores por defecto en `os.environ.get()`. Tu archivo `.env` local tiene las credenciales actuales como fallback.

### ¿Qué pasa si no configuro las variables?

Usará los valores por defecto del `.env` local, pero en producción **seguirá fallando** con las credenciales de prueba de MailerSend.

---

## 🚀 Siguiente Paso

**Configura las 6 variables en tu plataforma de hosting y reinicia el servidor.**

Si tienes dudas o el error persiste, envíame:
1. Los logs del error
2. El resultado del endpoint `/test-email-debug/`
3. Qué plataforma de hosting usas

¡Y te ayudo a resolverlo!
