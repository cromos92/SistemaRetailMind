import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'retailmind'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'retailmind.settings')
django.setup()

from django.conf import settings

print("\n===== CONFIGURACION BASE DE DATOS =====\n")

db = settings.DATABASES['default']

print("Motor:", db.get('ENGINE', 'N/A'))
print("Nombre:", db.get('NAME', 'N/A'))
print("Host:", db.get('HOST', 'N/A'))
print("Puerto:", db.get('PORT', 'N/A'))
print("Usuario:", db.get('USER', 'N/A'))

if 'DATABASE_URL' in os.environ:
    url = os.environ.get('DATABASE_URL', '')
    # Ocultar password
    if '@' in url:
        partes = url.split('@')
        print("\nDATABASE_URL configurada (con password oculta)")
    else:
        print("\nDATABASE_URL:", url[:50] + "...")
else:
    print("\nDATABASE_URL: No configurada (usando SQLite)")

print("\n======================================\n")


