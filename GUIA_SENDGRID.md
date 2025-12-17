# 📧 Guía Completa: Configurar SendGrid

## 🎯 Paso a Paso en SendGrid

### **Paso 1: Verificar Email** ✅
```
Ya completado ✓
```

### **Paso 2: Seleccionar Método de Envío**

**Elige:** **SMTP relay**

**Razones:**
- ✅ Integración directa con Django
- ✅ No requiere librerías adicionales
- ✅ Configuración simple
- ✅ Compatible con código existente

**NO elegir API** (requiere más configuración)

### **Paso 3: Obtener Credenciales SMTP**

SendGrid te dará:
```
SMTP Server: smtp.sendgrid.net
Port: 587
TLS: Yes
Username: apikey
Password: SG.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**IMPORTANTE:** Copia el API Key (Password) - solo se muestra una vez!

## 🔧 **Configurar en RetailMind**

### **Método 1: Variables de Entorno** (Recomendado)

#### **Windows PowerShell (como Admin):**

```powershell
setx EMAIL_HOST "smtp.sendgrid.net"
setx EMAIL_PORT "587"
setx EMAIL_HOST_USER "apikey"
setx EMAIL_HOST_PASSWORD "SG.tu-api-key-completa-aqui"
setx DEFAULT_FROM_EMAIL "noreply@retailmind.cl"
```

#### **Luego reinicia PowerShell y el servidor**

### **Método 2: Archivo .env** (Más fácil)

1. **Crear archivo `.env` en la raíz:**

```
c:\DjangoProyects\retailmind\SistemaRetailMind\.env
```

2. **Contenido del archivo:**

```env
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=SG.tu-api-key-aqui
DEFAULT_FROM_EMAIL=noreply@retailmind.cl
```

3. **Instalar python-dotenv:**

```bash
cd c:\DjangoProyects\retailmind\SistemaRetailMind
.\venv\Scripts\pip.exe install python-dotenv
```

4. **Agregar en settings.py (al inicio):**

```python
from dotenv import load_dotenv
load_dotenv()
```

### **Método 3: Directamente en settings.py** (Solo pruebas)

```python
# En settings.py:
EMAIL_HOST = 'smtp.sendgrid.net'
EMAIL_PORT = 587
EMAIL_HOST_USER = 'apikey'
EMAIL_HOST_PASSWORD = 'SG.tu-api-key-aqui'  # ⚠️ Pegar tu API key
DEFAULT_FROM_EMAIL = 'noreply@retailmind.cl'
```

## 📧 **Verificar el Email "From"**

SendGrid requiere que uses un email **verificado**:

### **Opción A: Single Sender Verification** (Gratis)

1. En SendGrid → Settings → Sender Authentication
2. Verify a Single Sender
3. Llenar formulario con:
   ```
   From Name: RetailMind
   From Email: tu-email@gmail.com (o el que verificaste)
   ```
4. Verificar email
5. Usar ese email en `DEFAULT_FROM_EMAIL`

### **Opción B: Domain Authentication** (Profesional)

Requiere tu propio dominio (retailmind.cl)

## 🚀 **Probar el Envío**

### **Paso 1: Reiniciar Servidor**

```bash
# Presiona Ctrl+C en el terminal
# Luego:
py .\manage.py runserver
```

### **Paso 2: Probar en SendGrid**

SendGrid tiene un botón **"Send Test Email"**:
1. Clic en "Send Test Email"
2. Verifica que llegue a tu email
3. ✅ Si llega, la configuración es correcta

### **Paso 3: Probar en RetailMind**

1. Ve a: `http://localhost:8000/users/gestion/`
2. Selecciona un usuario
3. Clic en "Resetear Contraseña"
4. Verificar:
   - ✅ Si llega email → Configuración correcta
   - ⚠️ Si muestra en pantalla → Verifica configuración

## 🔍 **Debug de Configuración**

### **Verificar Variables:**

```python
# En el terminal de Django:
from django.conf import settings
print(f"EMAIL_HOST: {settings.EMAIL_HOST}")
print(f"EMAIL_PORT: {settings.EMAIL_PORT}")
print(f"EMAIL_HOST_USER: {settings.EMAIL_HOST_USER}")
print(f"DEFAULT_FROM_EMAIL: {settings.DEFAULT_FROM_EMAIL}")
```

### **Verificar Conexión:**

```python
# En Django shell:
from django.core.mail import send_mail

send_mail(
    'Test',
    'Este es un email de prueba',
    'noreply@retailmind.cl',
    ['tu-email@gmail.com'],
    fail_silently=False,
)
```

## ⚠️ **Problemas Comunes**

### **1. Email no llega:**
```
Solución:
- Verificar API Key correcta
- Verificar FROM email esté verificado en SendGrid
- Revisar carpeta SPAM
```

### **2. Error de autenticación:**
```
Solución:
- Username debe ser: "apikey" (literal)
- Password debe ser: "SG.xxxxxxxx" (tu API key)
```

### **3. FROM email inválido:**
```
Solución:
- Verificar el email en SendGrid primero
- Usar el mismo email verificado
```

## 📊 **Resumen de Configuración**

**Lo que necesitas de SendGrid:**
```
Server: smtp.sendgrid.net
Port: 587
Username: apikey
Password: SG.xxxxxxxxxx (tu API key)
From Email: email@verificado.com
```

**Configurar en:**
```
1. Variables de entorno del sistema, O
2. Archivo .env, O
3. Directamente en settings.py (temporal)
```

**Reiniciar:**
```
Servidor Django (Ctrl+C y volver a iniciar)
```

**Probar:**
```
Resetear contraseña de un usuario
```

## ✅ **Recomendación**

**Para empezar rápido:**
1. Copia tu API Key de SendGrid
2. Pégala directamente en settings.py (temporal)
3. Reinicia servidor
4. Prueba resetear contraseña
5. Si funciona, mueve a variables de entorno

¿En qué paso estás ahora? ¿Ya tienes el API Key de SendGrid? 😊