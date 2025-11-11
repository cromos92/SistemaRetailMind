#!/usr/bin/env python
"""
Script de prueba para Transbank POS SDK
Ejecutar: python test_transbank_sdk.py
"""

import requests
import json
import sys

# Base URL
BASE_URL = "http://localhost:8000/app/pos/transbank"

def print_response(response, title="Respuesta"):
    """Imprime respuesta formateada"""
    print(f"\n{'='*60}")
    print(f"🔹 {title}")
    print(f"{'='*60}")
    print(f"Status Code: {response.status_code}")
    try:
        data = response.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except:
        print(response.text)
    print(f"{'='*60}\n")

def test_1_listar_puertos():
    """Test 1: Listar puertos disponibles"""
    print("\n🔍 TEST 1: Listando puertos disponibles...")
    try:
        response = requests.get(f"{BASE_URL}/puertos/")
        print_response(response, "Puertos Disponibles")
        return response.json().get('puertos', [])
    except Exception as e:
        print(f"❌ Error: {e}")
        return []

def test_2_conectar(puerto="COM3"):
    """Test 2: Conectar al POS"""
    print(f"\n🔌 TEST 2: Conectando al puerto {puerto}...")
    try:
        payload = {
            "puerto": puerto,
            "baud_rate": 115200
        }
        response = requests.post(
            f"{BASE_URL}/conectar/",
            json=payload
        )
        print_response(response, f"Conexión a {puerto}")
        return response.json().get('success', False)
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_3_verificar():
    """Test 3: Verificar conexión (POLL)"""
    print("\n✅ TEST 3: Verificando conexión con POLL...")
    try:
        response = requests.get(f"{BASE_URL}/verificar/")
        print_response(response, "Verificación de Conexión")
        return response.json().get('conectado', False)
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_4_cargar_llaves():
    """Test 4: Cargar llaves"""
    print("\n🔑 TEST 4: Cargando llaves en el POS...")
    try:
        response = requests.post(f"{BASE_URL}/cargar-llaves/")
        print_response(response, "Carga de Llaves")
        return response.json().get('response_code') == 0
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_5_venta(monto=1000, ticket="TEST001"):
    """Test 5: Venta de prueba"""
    print(f"\n💳 TEST 5: Procesando venta de ${monto} - Ticket: {ticket}...")
    print("⚠️  IMPORTANTE: Pase una tarjeta en el POS cuando se le solicite")
    try:
        payload = {
            "monto": monto,
            "ticket": ticket,
            "con_mensajes": False
        }
        response = requests.post(
            f"{BASE_URL}/venta/",
            json=payload,
            timeout=180  # 3 minutos de timeout
        )
        print_response(response, "Resultado de Venta")
        data = response.json()
        if data.get('success') and data.get('response_code') == 0:
            return data.get('operation_number')
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def test_6_ultima_venta():
    """Test 6: Consultar última venta"""
    print("\n📄 TEST 6: Consultando última venta...")
    try:
        response = requests.get(f"{BASE_URL}/ultima-venta/")
        print_response(response, "Última Venta")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_7_totales():
    """Test 7: Consultar totales"""
    print("\n📊 TEST 7: Consultando totales del día...")
    try:
        response = requests.get(f"{BASE_URL}/totales/")
        print_response(response, "Totales del Día")
        return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_8_desconectar():
    """Test 8: Desconectar"""
    print("\n🔌 TEST 8: Desconectando del POS...")
    try:
        response = requests.post(f"{BASE_URL}/desconectar/")
        print_response(response, "Desconexión")
        return response.json().get('success', False)
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def main():
    """Ejecutar todos los tests"""
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║   🧪 SUITE DE PRUEBAS TRANSBANK POS SDK                 ║
    ║   Sistema RetailMind - Integración Directa              ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    print("⚠️  IMPORTANTE: Asegúrese de que:")
    print("   1. El servidor Django esté corriendo (python manage.py runserver)")
    print("   2. El terminal POS esté conectado y encendido")
    print("   3. El POS esté en modo 'POS Integrado'")
    
    input("\n✋ Presione ENTER para continuar...")
    
    # Test 1: Listar puertos
    puertos = test_1_listar_puertos()
    if not puertos:
        print("❌ No se encontraron puertos disponibles")
        print("   Verifique que el POS esté conectado")
        return
    
    # Seleccionar puerto
    print(f"\n📍 Puertos disponibles: {puertos}")
    puerto = input(f"Ingrese el puerto a usar (default: {puertos[0] if puertos else 'COM3'}): ").strip()
    if not puerto:
        puerto = puertos[0] if puertos else "COM3"
    
    # Test 2: Conectar
    if not test_2_conectar(puerto):
        print("❌ No se pudo conectar al POS")
        return
    
    # Test 3: Verificar
    if not test_3_verificar():
        print("⚠️  El POS no responde al POLL")
        print("   Continúe bajo su propio riesgo...")
    
    # Test 4: Cargar llaves
    print("\n⚠️  CARGAR LLAVES (requerido 1 vez al día)")
    cargar = input("¿Desea cargar llaves ahora? (s/N): ").strip().lower()
    if cargar == 's':
        test_4_cargar_llaves()
    
    # Test 5: Venta
    print("\n💳 PRUEBA DE VENTA")
    realizar_venta = input("¿Desea realizar una venta de prueba? (s/N): ").strip().lower()
    operation_id = None
    if realizar_venta == 's':
        monto = input("Monto (default: 1000): ").strip()
        monto = int(monto) if monto else 1000
        operation_id = test_5_venta(monto)
        
        if operation_id:
            print(f"\n✅ Venta exitosa! Operation ID: {operation_id}")
    
    # Test 6: Última venta
    consultar = input("\n¿Desea consultar la última venta? (s/N): ").strip().lower()
    if consultar == 's':
        test_6_ultima_venta()
    
    # Test 7: Totales
    consultar_totales = input("\n¿Desea consultar totales del día? (s/N): ").strip().lower()
    if consultar_totales == 's':
        test_7_totales()
    
    # Test 8: Desconectar
    print("\n🔌 DESCONEXIÓN")
    desconectar = input("¿Desea desconectar del POS? (S/n): ").strip().lower()
    if desconectar != 'n':
        test_8_desconectar()
    
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║   ✅ PRUEBAS COMPLETADAS                                 ║
    ╚══════════════════════════════════════════════════════════╝
    """)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Pruebas interrumpidas por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error fatal: {e}")
        sys.exit(1)

