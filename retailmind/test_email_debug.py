"""
Script de diagnóstico para verificar configuración de email
"""
import os
from dotenv import load_dotenv
from pathlib import Path

# Cargar variables de entorno
BASE_DIR = Path(__file__).resolve().parent
env_path = BASE_DIR / '.env'

print("=" * 60)
print("DIAGNÓSTICO DE CONFIGURACIÓN DE EMAIL")
print("=" * 60)
print(f"\n📁 Directorio base: {BASE_DIR}")
print(f"📄 Archivo .env: {env_path}")
print(f"✅ ¿Existe .env?: {env_path.exists()}")

# Cargar .env
load_dotenv(env_path)

print("\n" + "=" * 60)
print("VARIABLES DE ENTORNO CARGADAS:")
print("=" * 60)
print(f"EMAIL_HOST: {os.environ.get('EMAIL_HOST', 'NO DEFINIDO')}")
print(f"EMAIL_PORT: {os.environ.get('EMAIL_PORT', 'NO DEFINIDO')}")
print(f"EMAIL_USE_TLS: {os.environ.get('EMAIL_USE_TLS', 'NO DEFINIDO')}")
print(f"EMAIL_HOST_USER: {os.environ.get('EMAIL_HOST_USER', 'NO DEFINIDO')}")
print(f"EMAIL_HOST_PASSWORD: {'*' * 20 if os.environ.get('EMAIL_HOST_PASSWORD') else 'NO DEFINIDO'}")
print(f"DEFAULT_FROM_EMAIL: {os.environ.get('DEFAULT_FROM_EMAIL', 'NO DEFINIDO')}")

print("\n" + "=" * 60)
print("PROBANDO CONEXIÓN SMTP...")
print("=" * 60)

try:
    import smtplib
    from email.mime.text import MIMEText
    
    host = os.environ.get('EMAIL_HOST')
    port = int(os.environ.get('EMAIL_PORT', 587))
    user = os.environ.get('EMAIL_HOST_USER')
    password = os.environ.get('EMAIL_HOST_PASSWORD')
    
    print(f"\n🔌 Conectando a {host}:{port}...")
    
    server = smtplib.SMTP(host, port, timeout=10)
    server.set_debuglevel(0)  # Cambiar a 1 para ver detalles
    server.starttls()
    
    print(f"🔐 Autenticando con usuario: {user}...")
    server.login(user, password)
    
    print("✅ ¡CONEXIÓN EXITOSA!")
    print("✅ Autenticación correcta")
    
    server.quit()
    
    print("\n" + "=" * 60)
    print("RESULTADO: La configuración de email está CORRECTA ✅")
    print("=" * 60)
    
except Exception as e:
    print(f"\n❌ ERROR: {str(e)}")
    print("\n" + "=" * 60)
    print("POSIBLES CAUSAS:")
    print("=" * 60)
    print("1. Token/contraseña incorrecta")
    print("2. Servidor SMTP bloqueado por firewall")
    print("3. Credenciales expiradas")
    print("4. Variables de entorno no cargadas correctamente")
