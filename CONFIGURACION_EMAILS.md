# 📧 Configuración de Envío de Emails

## ✅ **Solución Implementada (Sin Email)**

**Ahora cuando reseteas una contraseña:**
- ✅ Se genera contraseña nueva
- ✅ Se guarda en la base de datos
- ✅ Si email NO está configurado → **Muestra contraseña en pantalla**
- ✅ Botón para copiar al portapapeles
- ✅ Puedes enviársela al usuario por WhatsApp, SMS, etc.

**Diálogo que verás:**
```
┌────────────────────────────────────────┐
│ ⚠️ Contraseña Reseteada                │
├────────────────────────────────────────┤
│ Usuario: Juan Pérez                    │
│ Email: juan@email.com                  │
├────────────────────────────────────────┤
│ ⚠️ Email no configurado               │
│ La contraseña no pudo enviarse.        │
│                                        │
│ Nueva contraseña temporal:             │
│ [a8Kd92jP4mN1]  [📋 Copiar]          │
│                                        │
│ Copia y envíala al usuario de         │
│ forma segura.                          │
│                                        │
│          [Entendido]                   │
└────────────────────────────────────────┘
```

## 🎯 Opciones Disponibles

### **Opción 1: Gmail SMTP** ⭐ (Recomendado - Gratis)

**Pasos para configurar:**

#### **1. Crear App Password en Gmail**

1. Ve a tu cuenta de Gmail
2. Ir a: https://myaccount.google.com/security
3. Activar "Verificación en 2 pasos" (si no está activada)
4. Buscar "Contraseñas de aplicaciones"
5. Crear contraseña para "Correo" / "Otra aplicación"
6. Gmail te dará un código de 16 caracteres: `abcd efgh ijkl mnop`

#### **2. Configurar en settings.py**

Ya está agregado en tu `settings.py`:

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'tu-email@gmail.com'  # ← Cambiar
EMAIL_HOST_PASSWORD = 'abcdefghijklmnop'  # ← App Password sin espacios
DEFAULT_FROM_EMAIL = 'RetailMind <noreply@retailmind.cl>'
```

#### **3. Variables de Entorno (Más Seguro)**

Crea un archivo `.env` en la raíz:

```env
EMAIL_USER=tu-email@gmail.com
EMAIL_PASSWORD=abcdefghijklmnop
```

Instalar python-dotenv:
```bash
pip install python-dotenv
```

En `settings.py`:
```python
from dotenv import load_dotenv
load_dotenv()

EMAIL_HOST_USER = os.environ.get('EMAIL_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD')
```

### **Opción 2: SendGrid** (API Moderna - 100 emails/día gratis)

#### **1. Crear cuenta en SendGrid**
- https://sendgrid.com/
- Plan gratuito: 100 emails/día

#### **2. Instalar librería**
```bash
pip install sendgrid-django
```

#### **3. Configurar en settings.py**
```python
EMAIL_BACKEND = 'sendgrid_backend.SendgridBackend'
SENDGRID_API_KEY = 'SG.xxxxxxxxxx'
DEFAULT_FROM_EMAIL = 'noreply@tudominio.com'
```

### **Opción 3: Console Backend** (Solo Desarrollo)

**Para probar sin enviar emails reales:**

```python
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
```

**Resultado:**
- ✅ Los emails se "envían" pero aparecen en el terminal
- ✅ Perfecto para desarrollo
- ✅ No necesita configuración

### **Opción 4: Alternativa Temporal** (Sin Email)

Modificar la función para mostrar la contraseña en pantalla:

```python
# En users/views.py
@require_POST
@login_required
@csrf_exempt
def resetear_password(request, usuario_id):
    try:
        usuario = get_object_or_404(Usuario, id=usuario_id)
        
        # Generar nueva contraseña
        nueva_password = get_random_string(12)
        usuario.set_password(nueva_password)
        usuario.save()
        
        # En lugar de enviar correo, retornar la contraseña
        return JsonResponse({
            'success': True,
            'message': f'Contraseña reseteada exitosamente',
            'nueva_password': nueva_password,  # ⭐ Mostrar en pantalla
            'usuario_email': usuario.email
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
```

## 🎯 **Recomendación por Caso**

### **Para Producción:**
```
1. Gmail SMTP (si tienes Gmail empresarial)
2. SendGrid (si envías muchos correos)
3. Mailgun, AWS SES (alternativas profesionales)
```

### **Para Desarrollo:**
```
1. Console Backend (ver en terminal)
2. Mostrar contraseña en pantalla
3. Guardar en archivo de texto
```

### **Para Demo/Testing:**
```
Mostrar contraseña directamente en el navegador
Sin necesidad de email
```

## 🚀 **Solución Rápida: Gmail SMTP**

### **Paso 1: Configurar Gmail**
```
1. Gmail → Seguridad
2. Verificación en 2 pasos: Activar
3. Contraseñas de aplicación: Crear
4. Copiar código: "abcd efgh ijkl mnop"
```

### **Paso 2: Actualizar settings.py**
```python
EMAIL_HOST_USER = 'tu-gmail@gmail.com'
EMAIL_HOST_PASSWORD = 'abcdefghijklmnop'  # Sin espacios
```

### **Paso 3: Reiniciar servidor**
```
Ctrl+C
py .\manage.py runserver
```

### **Paso 4: Probar**
```
1. Resetear contraseña de un usuario
2. Verificar email
3. ✅ Debería llegar el correo
```

## 💡 **Solución Temporal (Sin Email)**

Si quieres una solución inmediata sin configurar email, puedo modificar la función para que **muestre la contraseña en pantalla** en lugar de enviarla por correo.

¿Qué prefieres?
1. **Configurar Gmail SMTP** (te guío paso a paso)
2. **Mostrar contraseña en pantalla** (sin email, temporal)
3. **Console Backend** (para desarrollo)

¿Cuál opción te sirve más? 😊