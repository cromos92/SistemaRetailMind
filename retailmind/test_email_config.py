#!/usr/bin/env python
"""
Script de prueba para verificar la configuración de email.

Uso:
    python test_email_config.py

Este script:
1. Verifica que las variables de entorno estén configuradas
2. Intenta enviar un correo de prueba
3. Reporta cualquier error con detalles
"""

import os
import sys
import django

# Configurar Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.core.mail import send_mail
from django.conf import settings


def print_header(text):
    """Imprime un header formateado"""
    print("\n" + "="*60)
    print(f"  {text}")
    print("="*60)


def print_config():
    """Muestra la configuración actual de email"""
    print_header("CONFIGURACIÓN DE EMAIL")
    
    config = {
        'EMAIL_BACKEND': settings.EMAIL_BACKEND,
        'EMAIL_HOST': settings.EMAIL_HOST,
        'EMAIL_PORT': settings.EMAIL_PORT,
        'EMAIL_USE_TLS': settings.EMAIL_USE_TLS,
        'EMAIL_HOST_USER': settings.EMAIL_HOST_USER,
        'EMAIL_HOST_PASSWORD': '***' + settings.EMAIL_HOST_PASSWORD[-4:] if settings.EMAIL_HOST_PASSWORD else 'NO CONFIGURADO',
        'DEFAULT_FROM_EMAIL': settings.DEFAULT_FROM_EMAIL,
    }
    
    for key, value in config.items():
        print(f"  {key:<20} = {value}")


def check_env_vars():
    """Verifica que las variables de entorno estén configuradas"""
    print_header("VERIFICACIÓN DE VARIABLES DE ENTORNO")
    
    required_vars = [
        'EMAIL_HOST',
        'EMAIL_PORT',
        'EMAIL_HOST_USER',
        'EMAIL_HOST_PASSWORD',
    ]
    
    all_ok = True
    for var in required_vars:
        value = os.environ.get(var)
        if value:
            # Ocultar password
            if 'PASSWORD' in var:
                display_value = '***' + value[-4:]
            else:
                display_value = value
            print(f"  ✓ {var:<20} = {display_value}")
        else:
            print(f"  ✗ {var:<20} = NO CONFIGURADO (usando valor por defecto)")
            all_ok = False
    
    return all_ok


def test_email_sending():
    """Intenta enviar un correo de prueba"""
    print_header("PRUEBA DE ENVÍO DE CORREO")
    
    # Solicitar email destino
    print("\n¿A qué email quieres enviar el correo de prueba?")
    recipient = input(f"(Presiona Enter para usar {settings.EMAIL_HOST_USER}): ").strip()
    
    if not recipient:
        recipient = settings.EMAIL_HOST_USER
    
    print(f"\n📧 Enviando correo de prueba a: {recipient}")
    print("⏳ Espera un momento...")
    
    try:
        send_mail(
            subject='🧪 Test de correo - NEXO RetailMind',
            message='''
Este es un correo de prueba desde tu sistema NEXO RetailMind.

Si recibiste este correo, significa que tu configuración de email está funcionando correctamente.

Configuración usada:
- Host: {}
- Puerto: {}
- Usuario: {}

¡Tu sistema está listo para enviar correos de recuperación de contraseña!

---
Equipo de Desarrollo
NEXO RetailMind
            '''.format(settings.EMAIL_HOST, settings.EMAIL_PORT, settings.EMAIL_HOST_USER),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False
        )
        
        print("\n✅ ¡ÉXITO! Correo enviado correctamente.")
        print(f"   Revisa tu bandeja de entrada en: {recipient}")
        return True
        
    except Exception as e:
        print("\n❌ ERROR al enviar el correo:")
        print(f"   Tipo de error: {type(e).__name__}")
        print(f"   Mensaje: {str(e)}")
        
        # Dar sugerencias según el tipo de error
        error_str = str(e).lower()
        
        print("\n💡 POSIBLES SOLUCIONES:")
        
        if 'authentication' in error_str or '535' in error_str:
            print("  • Verifica que EMAIL_HOST_USER y EMAIL_HOST_PASSWORD sean correctos")
            print("  • Si usas Gmail, asegúrate de usar un App Password, no tu contraseña normal")
            print("  • Genera uno en: https://myaccount.google.com/security")
        
        elif 'connection' in error_str or 'refused' in error_str:
            print("  • Verifica que EMAIL_HOST y EMAIL_PORT sean correctos")
            print("  • Asegúrate de que no haya firewall bloqueando el puerto 587")
            print("  • Intenta con EMAIL_PORT=465 y EMAIL_USE_SSL=True")
        
        elif 'domain' in error_str or 'verified' in error_str:
            print("  • Si usas MailerSend, verifica tu dominio en:")
            print("    https://app.mailersend.com/domains")
            print("  • O cambia a Gmail que no requiere verificación")
        
        else:
            print("  • Revisa los logs para más detalles")
            print("  • Verifica que todas las variables de entorno estén configuradas")
        
        return False


def main():
    """Función principal"""
    print("\n" + "🔧 TEST DE CONFIGURACIÓN DE EMAIL - NEXO RETAILMIND".center(60, " "))
    
    # 1. Mostrar configuración
    print_config()
    
    # 2. Verificar variables de entorno
    env_ok = check_env_vars()
    
    if not env_ok:
        print("\n⚠️  ADVERTENCIA: Algunas variables no están configuradas.")
        print("   Se usarán los valores por defecto del settings.py")
    
    # 3. Preguntar si continuar
    print("\n" + "-"*60)
    continuar = input("\n¿Quieres enviar un correo de prueba? (s/n): ").lower()
    
    if continuar not in ['s', 'si', 'sí', 'y', 'yes']:
        print("\n👋 Test cancelado.")
        return 0
    
    # 4. Intentar enviar correo
    success = test_email_sending()
    
    # 5. Resumen final
    print_header("RESUMEN")
    
    if success:
        print("\n  ✅ TODO FUNCIONA CORRECTAMENTE")
        print("  ✅ Tu configuración de email está lista para producción")
        print("\n  📝 Próximos pasos:")
        print("     1. Configura estas mismas variables en tu servidor de producción")
        print("     2. Reinicia el servidor")
        print("     3. Prueba la recuperación de contraseña")
    else:
        print("\n  ❌ HAY PROBLEMAS CON LA CONFIGURACIÓN")
        print("  📝 Revisa las sugerencias de arriba")
        print("  📚 Consulta: CONFIGURAR_EMAIL_PRODUCCION.md")
    
    print("\n")
    return 0 if success else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n👋 Test cancelado por el usuario.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n❌ Error inesperado: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
