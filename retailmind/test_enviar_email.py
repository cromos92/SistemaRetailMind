"""
Script para probar envío de email
"""
import os
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.core.mail import send_mail
from django.conf import settings

print("=" * 60)
print("PROBANDO ENVÍO DE EMAIL")
print("=" * 60)

print(f"\n📧 Configuración actual:")
print(f"   HOST: {settings.EMAIL_HOST}")
print(f"   PORT: {settings.EMAIL_PORT}")
print(f"   USER: {settings.EMAIL_HOST_USER}")
print(f"   FROM: {settings.DEFAULT_FROM_EMAIL}")
print(f"   TLS: {settings.EMAIL_USE_TLS}")

print("\n🚀 Enviando email de prueba...")

try:
    send_mail(
        subject='Prueba de Email - RetailMind',
        message='Este es un email de prueba desde RetailMind. Si recibes este mensaje, la configuración está funcionando correctamente. ✅',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=['jav.teb@gmail.com'],
        fail_silently=False,
    )
    
    print("\n" + "=" * 60)
    print("✅ ¡EMAIL ENVIADO EXITOSAMENTE!")
    print("=" * 60)
    print("📬 Revisa la bandeja de entrada de: jav.teb@gmail.com")
    print("💡 También revisa la carpeta de SPAM por si acaso")
    
except Exception as e:
    print("\n" + "=" * 60)
    print("❌ ERROR AL ENVIAR EMAIL")
    print("=" * 60)
    print(f"Error: {str(e)}")
    print("\n🔍 POSIBLES CAUSAS:")
    print("1. Token de MailerSend incorrecto o expirado")
    print("2. Dominio no verificado en MailerSend")
    print("3. Variables de entorno no cargadas")
    print("4. Servidor SMTP bloqueado por firewall")
    print("\n💡 SOLUCIÓN: Verifica el token en MailerSend dashboard")
