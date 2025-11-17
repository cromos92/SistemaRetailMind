# ⚙️ Configuración de SMTP para Envío de Emails

## 📧 Requisito para Sistema de Requerimientos

Para que el sistema pueda enviar emails automáticos a los proveedores, necesitas configurar SMTP en Django.

---

## 🔧 CONFIGURACIÓN EN SETTINGS.PY

### Opción 1: Gmail (Recomendado para desarrollo)

Agrega al final de `retailmind/settings.py`:

```python
# ========== CONFIGURACIÓN DE EMAIL ==========
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'tuempresa@gmail.com'  # ← Cambiar por tu email
EMAIL_HOST_PASSWORD = 'tu_app_password'   # ← Ver instrucciones abajo
DEFAULT_FROM_EMAIL = 'RetailMind <noreply@tuempresa.com>'
```

#### Obtener App Password de Gmail:

1. Ir a https://myaccount.google.com/
2. Seguridad → Verificación en 2 pasos (activar si no está)
3. Contraseñas de aplicaciones
4. Seleccionar "Correo" y "Otro"
5. Escribir "RetailMind Django"
6. Copiar la contraseña generada (16 caracteres)
7. Usar esa contraseña en `EMAIL_HOST_PASSWORD`

---

### Opción 2: Outlook/Hotmail

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp-mail.outlook.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'tuempresa@outlook.com'
EMAIL_HOST_PASSWORD = 'tu_contraseña'
DEFAULT_FROM_EMAIL = 'RetailMind <noreply@tuempresa.com>'
```

---

### Opción 3: Servidor SMTP Personalizado

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'mail.tudominio.com'
EMAIL_PORT = 587  # o 465 para SSL
EMAIL_USE_TLS = True  # o EMAIL_USE_SSL = True
EMAIL_HOST_USER = 'noreply@tudominio.com'
EMAIL_HOST_PASSWORD = 'contraseña_smtp'
DEFAULT_FROM_EMAIL = 'RetailMind <noreply@tudominio.com>'
```

---

### Opción 4: SendGrid (Para producción)

```python
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.sendgrid.net'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'apikey'
EMAIL_HOST_PASSWORD = 'SG.tu_api_key_aqui'  # API Key de SendGrid
DEFAULT_FROM_EMAIL = 'RetailMind <noreply@tuempresa.com>'
```

---

## 🧪 PROBAR LA CONFIGURACIÓN

### Método 1: Django Shell

```bash
# Activar venv
cd C:\DjangoProyects\retailmind\SistemaRetailMind
.\venv\Scripts\activate
cd retailmind

# Abrir shell
python manage.py shell
```

```python
# En el shell de Django
from django.core.mail import send_mail

# Enviar email de prueba
send_mail(
    subject='Prueba de Email RetailMind',
    message='Este es un email de prueba del sistema.',
    from_email='RetailMind <noreply@tuempresa.com>',
    recipient_list=['tu_email_personal@gmail.com'],
    fail_silently=False,
)

# Deberías ver: 1
# Y recibir el email en tu bandeja
```

### Método 2: Vista de Test

Crea un archivo `test_email.py`:

```python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.core.mail import EmailMessage
from django.conf import settings

email = EmailMessage(
    subject='Test Email RetailMind',
    body='<h1>Email de Prueba</h1><p>Si recibes esto, SMTP está funcionando correctamente.</p>',
    from_email=settings.DEFAULT_FROM_EMAIL,
    to=['destinatario@example.com'],  # ← Cambiar por email real
)
email.content_subtype = 'html'
email.send()

print("Email enviado!")
```

Ejecutar:
```bash
python test_email.py
```

---

## 🚨 SOLUCIÓN DE PROBLEMAS

### Error: SMTPAuthenticationError

**Problema**: Credenciales incorrectas

**Solución**:
- Gmail: Usa App Password, NO tu contraseña normal
- Verifica que EMAIL_HOST_USER sea correcto
- Verifica que EMAIL_HOST_PASSWORD sea correcto

### Error: SMTPServerDisconnected

**Problema**: Configuración de servidor incorrecta

**Solución**:
- Verifica EMAIL_HOST
- Verifica EMAIL_PORT (587 para TLS, 465 para SSL)
- Cambia EMAIL_USE_TLS por EMAIL_USE_SSL si usas 465

### Error: [Errno 11001] getaddrinfo failed

**Problema**: No hay conexión a internet o host incorrecto

**Solución**:
- Verifica conexión a internet
- Verifica EMAIL_HOST (sin http://, solo el dominio)
- Prueba hacer ping al servidor SMTP

### Email se envía pero no llega

**Problema**: Filtros de spam

**Solución**:
- Revisa carpeta de Spam
- Configura SPF y DKIM en tu dominio
- Usa un servidor SMTP reputado (SendGrid, Mailgun)

---

## 🔒 SEGURIDAD

### Variables de Entorno (Recomendado)

**NO guardes contraseñas en settings.py**

Usa variables de entorno:

```python
# settings.py
import os
from decouple import config  # pip install python-decouple

EMAIL_HOST_USER = config('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD')
```

Crea archivo `.env` en la raíz:
```
EMAIL_HOST_USER=tuempresa@gmail.com
EMAIL_HOST_PASSWORD=tu_app_password
```

Agrega `.env` a `.gitignore`:
```
.env
```

---

## 📊 CONFIGURACIONES POR AMBIENTE

### Desarrollo (Local)

```python
# Para testing local sin enviar emails reales
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# Esto imprime los emails en la consola en lugar de enviarlos
```

### Staging (Pruebas)

```python
# Usar Gmail o Outlook para pruebas
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
# ... resto de configuración
```

### Producción

```python
# Usar servicio profesional (SendGrid, AWS SES, etc.)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.sendgrid.net'
# ... resto de configuración
```

---

## 🎯 CONFIGURACIÓN RECOMENDADA

### Para Empezar (HOY):

```python
# En settings.py - AL FINAL DEL ARCHIVO

# ========== EMAIL CONFIGURATION ==========
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'retailmind@tuempresa.com'  # ← CAMBIAR
EMAIL_HOST_PASSWORD = 'xxxx xxxx xxxx xxxx'    # ← APP PASSWORD
DEFAULT_FROM_EMAIL = 'RetailMind <noreply@tuempresa.com>'

# Emails de administración (reciben errores)
ADMINS = [
    ('Admin Principal', 'admin@tuempresa.com'),
]
```

---

## ✅ CHECKLIST DE CONFIGURACIÓN

### Paso a Paso:

- [ ] 1. Decidir qué servicio SMTP usar
- [ ] 2. Obtener credenciales (email + contraseña/API key)
- [ ] 3. Agregar configuración a settings.py
- [ ] 4. Reiniciar servidor Django
- [ ] 5. Probar con Django shell
- [ ] 6. Enviar email de prueba
- [ ] 7. Verificar recepción
- [ ] 8. Probar desde sistema de requerimientos
- [ ] 9. Enviar a proveedor real
- [ ] 10. Confirmar que llega con fotos

---

## 📧 EJEMPLO DE EMAIL QUE SE ENVIARÁ

```
De: RetailMind <noreply@tuempresa.com>
Para: proveedor@nike.cl
CC: admin.proveedor@nike.cl
Asunto: Requerimiento de Garantía - REQ-20241117-0001

[Header con logo]
🛡️ Requerimiento de Garantía
N° REQ-20241117-0001

Estimado proveedor Nike Chile,

Se ha generado un requerimiento que requiere de su atención...

📋 INFORMACIÓN GENERAL
Tipo: Garantía
Prioridad: Alta
Sucursal: Santiago Centro
Fecha: 17/11/2024

📦 PRODUCTO AFECTADO
SKU: 4819942
Producto: ZAPATILLAS NIKE AIR MAX
Documento: Boleta Electrónica N° 26
Fecha Compra: 15/11/2024

👤 DATOS DEL CLIENTE
Nombre: Juan Pérez
RUT: 18.312.585-9
Teléfono: +56912345678

❗ DESCRIPCIÓN DEL PROBLEMA
Motivo: Desprendimiento de suela
Descripción: Cliente reporta que la suela se está despegando...

📸 FOTOS ADJUNTAS (3)
[Fotos adjuntas como archivos]

¿Procede este requerimiento?
Por favor, revise y responda indicando si aprueba o rechaza.

Puede responder directamente a este correo.

Saludos,
RetailMind - Santiago Centro
```

---

## 🎉 ¡LISTO PARA USAR!

Una vez configurado el SMTP, el sistema:

✅ Enviará emails automáticamente  
✅ Adjuntará fotos  
✅ Hará seguimiento  
✅ Alertará si no hay respuesta  
✅ Todo quedará registrado  

---

**¿Necesitas ayuda con la configuración?** Sigue esta guía paso a paso.

**¿Ya configuraste?** Prueba con `python manage.py shell` y el comando de arriba.

**¿Todo funciona?** ¡Felicidades! El sistema está completo y operativo. 🚀

