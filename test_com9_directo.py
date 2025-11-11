"""
Test directo con COM9 - VX 520 Terminal
Ejecutar: python test_com9_directo.py
"""

from transbank import POSIntegrado
from transbank.error.transbank_exception import TransbankException
import time

def separador(texto):
    print("\n" + "="*60)
    print(f"  {texto}")
    print("="*60)

def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║      TEST DIRECTO COM9 - VX 520 GPRS Terminal                ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    puerto = "COM9"
    baudrate = 115200
    
    separador("PASO 1: Crear instancia POS")
    pos = POSIntegrado()
    pos.timeout = 30  # 30 segundos timeout
    print(f"✅ POSIntegrado creado")
    print(f"   Timeout configurado: {pos.timeout} segundos")
    
    separador("PASO 2: Abrir puerto COM9")
    print(f"Puerto: {puerto}")
    print(f"Baudrate: {baudrate}")
    print(f"Abriendo...")
    
    try:
        resultado = pos.open_port(port=puerto, baud_rate=baudrate)
        print(f"Resultado open_port: {resultado}")
        
        if resultado:
            print(f"✅ Puerto COM9 abierto exitosamente")
        else:
            print(f"❌ No se pudo abrir COM9")
            return
            
    except Exception as e:
        print(f"❌ Error abriendo puerto: {e}")
        return
    
    separador("PASO 3: POLL - Verificar comunicación")
    print("Ejecutando POLL...")
    print("⏳ Esperando respuesta del POS (max 30 segundos)...")
    
    try:
        resultado_poll = pos.poll()
        print(f"\n✅ POLL EXITOSO!")
        print(f"   Resultado: {resultado_poll}")
        
    except TransbankException as e:
        print(f"\n❌ POLL FALLÓ - TransbankException:")
        print(f"   Error: {e}")
        print(f"   Causa: {e.__cause__}")
        print("\n💡 SOLUCIÓN:")
        print("   1. Verifica que el POS esté ENCENDIDO")
        print("   2. Verifica que esté en modo 'POS INTEGRADO'")
        print("      (En el POS: MENU → Configuración → Modo POS Integrado)")
        print("   3. Reinicia el POS y vuelve a intentar")
        pos.close_port()
        return
    except Exception as e:
        print(f"\n❌ Error inesperado en POLL: {e}")
        pos.close_port()
        return
    
    separador("PASO 4: Cargar Llaves")
    print("⚠️  IMPORTANTE:")
    print("   1. El POS mostrará: '¿Desea cargar llaves criptográficas?'")
    print("   2. PRESIONA 'SÍ' o 'ACEPTAR' en el POS físico")
    print("   3. Espera 30-60 segundos (el POS se conecta a Transbank)")
    print("\n¿Continuar con carga de llaves? (s/N): ", end='')
    
    try:
        respuesta = input().strip().lower()
    except:
        respuesta = 'n'
    
    if respuesta == 's':
        print("\nEjecutando load_keys()...")
        print("⏳ Esperando respuesta (puede tardar 60+ segundos)...")
        
        # Aumentar timeout para load_keys
        pos.timeout = 90
        
        try:
            resultado_keys = pos.load_keys()
            
            print(f"\n✅ LOAD KEYS COMPLETADO!")
            print(f"   Respuesta: {resultado_keys}")
            
            if isinstance(resultado_keys, dict):
                response_code = resultado_keys.get('response_code', -1)
                
                if response_code == 0:
                    print(f"\n   🎉 LLAVES CARGADAS EXITOSAMENTE!")
                    print(f"   Commerce Code: {resultado_keys.get('commerce_code', 'N/A')}")
                    print(f"   Terminal ID: {resultado_keys.get('terminal_id', 'N/A')}")
                    print(f"   Function Code: {resultado_keys.get('function_code', 'N/A')}")
                else:
                    print(f"\n   ⚠️  Response Code: {response_code}")
                    print(f"   El POS respondió pero con código de error")
                    print(f"   Posible causa: Usuario canceló o POS sin conexión Transbank")
            
        except TransbankException as e:
            print(f"\n❌ Error en load_keys:")
            print(f"   {e}")
            print(f"   Causa: {e.__cause__}")
            print("\n💡 Posibles causas:")
            print("   - Usuario presionó 'NO' en el POS")
            print("   - Timeout (tardó más de 90 segundos)")
            print("   - POS sin conexión a internet/GPRS")
        except Exception as e:
            print(f"\n❌ Error inesperado: {e}")
    else:
        print("\nOmitiendo test de carga de llaves")
    
    separador("PASO 5: Cerrar puerto")
    try:
        pos.close_port()
        print("✅ Puerto cerrado correctamente")
    except:
        print("⚠️  Error cerrando puerto (ignorado)")
    
    separador("RESUMEN")
    print("""
✅ TU CONFIGURACIÓN CORRECTA:
   Puerto: COM9
   Baudrate: 115200
   Terminal: VX 520 GPRS Terminal

📝 USAR EN LA API:
   curl -X POST http://localhost:8000/app/pos/transbank/conectar/ \\
     -H "Content-Type: application/json" \\
     -d '{"puerto": "COM9", "baud_rate": 115200}'

🔑 SOBRE CARGA DE LLAVES:
   - Es NORMAL que el POS pida confirmación
   - Debes presionar SÍ en el POS físico
   - Luego esperar 30-60 segundos
   - El POS se conecta a Transbank y descarga llaves
    """)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Test interrumpido")
    except Exception as e:
        print(f"\n❌ Error: {e}")

