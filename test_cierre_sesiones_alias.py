"""
Script de prueba para verificar el cierre automático de sesiones
al cambiar el alias de una sucursal.

Uso:
    python manage.py shell < test_cierre_sesiones_alias.py
    
O en el shell:
    python manage.py shell
    >>> exec(open('test_cierre_sesiones_alias.py').read())
"""

from django.contrib.sessions.models import Session
from django.contrib.auth import get_user_model
from retailmind.app.models import Sucursal, EmpresaUser
from retailmind.app.views_gestion_sucursales import invalidar_sesiones_sucursal
from django.utils import timezone
import json

User = get_user_model()

def crear_sesion_test(usuario, sucursal_id):
    """Crea una sesión de prueba para un usuario con una sucursal específica"""
    from django.contrib.sessions.backends.db import SessionStore
    
    session = SessionStore()
    session['_auth_user_id'] = str(usuario.id)
    session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
    session['_auth_user_hash'] = usuario.get_session_auth_hash()
    session['idSucursalActual'] = sucursal_id
    session['alias'] = 'ALIAS_TEST'
    session.save()
    
    return session.session_key

def test_cierre_sesiones():
    """
    Test completo del cierre de sesiones al cambiar alias
    """
    print("=" * 80)
    print("🧪 TEST: Cierre automático de sesiones al cambiar alias de sucursal")
    print("=" * 80)
    
    # 1. Obtener o crear una sucursal de prueba
    print("\n📋 PASO 1: Preparando datos de prueba...")
    sucursal = Sucursal.objects.first()
    
    if not sucursal:
        print("❌ No hay sucursales en la BD. Crea al menos una primero.")
        return
    
    print(f"✅ Sucursal seleccionada: {sucursal.alias} (ID: {sucursal.id})")
    
    # 2. Crear sesiones de prueba
    print("\n📋 PASO 2: Creando sesiones de prueba...")
    usuarios = User.objects.filter(is_active=True)[:3]
    
    if len(usuarios) < 1:
        print("❌ No hay usuarios activos en la BD.")
        return
    
    sesiones_creadas = []
    for usuario in usuarios:
        session_key = crear_sesion_test(usuario, sucursal.id)
        sesiones_creadas.append(session_key)
        print(f"   ✅ Sesión creada para {usuario.username}: {session_key[:10]}...")
    
    print(f"\n📊 Total sesiones creadas: {len(sesiones_creadas)}")
    
    # 3. Verificar que las sesiones existen
    print("\n📋 PASO 3: Verificando sesiones activas...")
    sesiones_activas = Session.objects.filter(
        session_key__in=sesiones_creadas,
        expire_date__gte=timezone.now()
    ).count()
    print(f"   ✅ Sesiones activas encontradas: {sesiones_activas}/{len(sesiones_creadas)}")
    
    # 4. Llamar a la función de invalidación
    print("\n📋 PASO 4: Ejecutando invalidar_sesiones_sucursal()...")
    alias_anterior = sucursal.alias
    sesiones_cerradas = invalidar_sesiones_sucursal(sucursal.id, alias_anterior)
    print(f"   ✅ Sesiones cerradas: {sesiones_cerradas}")
    
    # 5. Verificar que las sesiones fueron eliminadas
    print("\n📋 PASO 5: Verificando que las sesiones fueron eliminadas...")
    sesiones_restantes = Session.objects.filter(
        session_key__in=sesiones_creadas
    ).count()
    print(f"   ✅ Sesiones restantes: {sesiones_restantes}/{len(sesiones_creadas)}")
    
    # 6. Resultado final
    print("\n" + "=" * 80)
    if sesiones_restantes == 0:
        print("✅ TEST EXITOSO: Todas las sesiones fueron cerradas correctamente")
    else:
        print(f"⚠️ TEST PARCIAL: {sesiones_restantes} sesiones no fueron cerradas")
    print("=" * 80)
    
    return {
        'sesiones_creadas': len(sesiones_creadas),
        'sesiones_cerradas': sesiones_cerradas,
        'sesiones_restantes': sesiones_restantes,
        'exitoso': sesiones_restantes == 0
    }

def test_sesiones_otras_sucursales():
    """
    Test para verificar que NO se cierran sesiones de otras sucursales
    """
    print("\n" + "=" * 80)
    print("🧪 TEST: Verificar que NO se cierran sesiones de otras sucursales")
    print("=" * 80)
    
    sucursales = Sucursal.objects.all()[:2]
    
    if len(sucursales) < 2:
        print("❌ Necesitas al menos 2 sucursales para este test")
        return
    
    sucursal_1 = sucursales[0]
    sucursal_2 = sucursales[1]
    
    print(f"\n📋 Sucursal 1: {sucursal_1.alias} (ID: {sucursal_1.id})")
    print(f"📋 Sucursal 2: {sucursal_2.alias} (ID: {sucursal_2.id})")
    
    # Crear sesiones para ambas sucursales
    usuarios = User.objects.filter(is_active=True)[:4]
    
    sesiones_suc1 = []
    sesiones_suc2 = []
    
    print("\n📋 Creando sesiones...")
    for i, usuario in enumerate(usuarios[:2]):
        session_key = crear_sesion_test(usuario, sucursal_1.id)
        sesiones_suc1.append(session_key)
        print(f"   ✅ Sesión Suc1 ({sucursal_1.alias}): {session_key[:10]}...")
    
    for i, usuario in enumerate(usuarios[2:4]):
        session_key = crear_sesion_test(usuario, sucursal_2.id)
        sesiones_suc2.append(session_key)
        print(f"   ✅ Sesión Suc2 ({sucursal_2.alias}): {session_key[:10]}...")
    
    # Invalidar solo sesiones de sucursal 1
    print(f"\n📋 Invalidando sesiones de {sucursal_1.alias}...")
    cerradas = invalidar_sesiones_sucursal(sucursal_1.id, sucursal_1.alias)
    print(f"   ✅ Sesiones cerradas: {cerradas}")
    
    # Verificar
    restantes_suc1 = Session.objects.filter(session_key__in=sesiones_suc1).count()
    restantes_suc2 = Session.objects.filter(session_key__in=sesiones_suc2).count()
    
    print(f"\n📊 Resultados:")
    print(f"   Sesiones Suc1 restantes: {restantes_suc1}/{len(sesiones_suc1)}")
    print(f"   Sesiones Suc2 restantes: {restantes_suc2}/{len(sesiones_suc2)}")
    
    # Limpiar sesiones de suc2
    for session_key in sesiones_suc2:
        try:
            Session.objects.filter(session_key=session_key).delete()
        except:
            pass
    
    print("\n" + "=" * 80)
    if restantes_suc1 == 0 and restantes_suc2 == len(sesiones_suc2):
        print("✅ TEST EXITOSO: Solo se cerraron sesiones de la sucursal correcta")
    else:
        print("⚠️ TEST FALLIDO: Se cerraron sesiones incorrectas")
    print("=" * 80)

def mostrar_sesiones_activas():
    """
    Muestra todas las sesiones activas con información de sucursal
    """
    print("\n" + "=" * 80)
    print("📊 SESIONES ACTIVAS ACTUALES")
    print("=" * 80)
    
    sesiones = Session.objects.filter(expire_date__gte=timezone.now())
    
    print(f"\nTotal sesiones activas: {sesiones.count()}")
    
    for i, sesion in enumerate(sesiones[:10], 1):
        try:
            datos = sesion.get_decoded()
            sucursal_id = datos.get('idSucursalActual', 'N/A')
            alias = datos.get('alias', 'N/A')
            user_id = datos.get('_auth_user_id', 'N/A')
            
            print(f"\n{i}. Session: {sesion.session_key[:12]}...")
            print(f"   User ID: {user_id}")
            print(f"   Sucursal ID: {sucursal_id}")
            print(f"   Alias: {alias}")
            print(f"   Expira: {sesion.expire_date.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            print(f"\n{i}. Session: {sesion.session_key[:12]}... (Error al decodificar: {e})")
    
    if sesiones.count() > 10:
        print(f"\n... y {sesiones.count() - 10} sesiones más")
    
    print("=" * 80)

# Ejecutar tests
if __name__ == '__main__':
    print("\n" + "🚀" * 40)
    print("INICIANDO TESTS DE CIERRE DE SESIONES")
    print("🚀" * 40)
    
    # Mostrar sesiones actuales
    mostrar_sesiones_activas()
    
    # Test principal
    resultado = test_cierre_sesiones()
    
    # Test de selectividad (solo si hay múltiples sucursales)
    test_sesiones_otras_sucursales()
    
    # Mostrar sesiones finales
    mostrar_sesiones_activas()
    
    print("\n" + "🏁" * 40)
    print("TESTS COMPLETADOS")
    print("🏁" * 40)
