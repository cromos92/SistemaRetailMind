"""
Script de diagnóstico para Transbank POS
Ejecutar con: python diagnostico_pos.py
"""

from transbank import POSIntegrado
from transbank.error.transbank_exception import TransbankException
import sys
import time


def separador(texto):
    print("\n" + "="*60)
    print(f"  {texto}")
    print("="*60)


def test_listar_puertos():
    separador("TEST 1: Listar Puertos Disponibles")
    try:
        pos = POSIntegrado()
        puertos = pos.list_ports()
        
        if puertos:
            print(f"✅ Se encontraron {len(puertos)} puerto(s):")
            for i, puerto_info in enumerate(puertos, 1):
                if isinstance(puerto_info, dict):
                    print(f"   {i}. {puerto_info.get('port')} - {puerto_info.get('description', 'N/A')}")
                else:
                    print(f"   {i}. {puerto_info}")
            return puertos
        else:
            print("❌ No se encontraron puertos")
            print("\n💡 SOLUCIONES:")
            print("   - Verifica que el POS esté conectado por USB")
            print("   - En Linux: sudo usermod -a -G dialout $USER (luego reiniciar)")
            print("   - En Windows: Verifica en Administrador de Dispositivos")
            print("   - Instala drivers: pip install pyserial")
            return []
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return []


def test_info_puerto(puerto):
    separador(f"TEST 2: Información del Puerto {puerto}")
    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        
        for p in ports:
            if p.device == puerto:
                print(f"✅ Información detallada:")
                print(f"   Device: {p.device}")
                print(f"   Name: {p.name}")
                print(f"   Description: {p.description}")
                print(f"   HWID: {p.hwid}")
                print(f"   VID: {p.vid}")
                print(f"   PID: {p.pid}")
                print(f"   Serial Number: {p.serial_number}")
                print(f"   Location: {p.location}")
                print(f"   Manufacturer: {p.manufacturer}")
                print(f"   Product: {p.product}")
                print(f"   Interface: {p.interface}")
                return True
        
        print(f"⚠️  No se encontró información detallada")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False


def test_abrir_puerto(puerto, baudrate):
    separador(f"TEST 3: Abrir Puerto {puerto} @ {baudrate}")
    try:
        pos = POSIntegrado()
        print(f"Intentando abrir puerto...")
        # El SDK usa 'baud_rate' (con guión bajo)
        resultado = pos.open_port(port=puerto, baud_rate=baudrate)
        
        if resultado:
            print(f"✅ Puerto abierto exitosamente")
            return pos
        else:
            print(f"❌ No se pudo abrir el puerto")
            return None
    except TransbankException as e:
        print(f"❌ TransbankException: {e}")
        print(f"   Causa: {e.__cause__}")
        return None
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return None


def test_poll(pos):
    separador("TEST 4: POLL - Verificar Comunicación")
    try:
        print("Ejecutando POLL...")
        resultado = pos.poll()
        
        print(f"Resultado POLL: {resultado}")
        
        if resultado:
            print("✅ POS responde correctamente - CONECTADO")
            if isinstance(resultado, dict):
                print(f"   Respuesta completa: {resultado}")
            return True
        else:
            print("❌ POS no responde")
            print("\n💡 SOLUCIONES:")
            print("   - Verifica que el POS esté ENCENDIDO")
            print("   - Verifica que esté en modo 'POS INTEGRADO'")
            print("   - Revisa el cable USB")
            print("   - Prueba otro baudrate")
            return False
    except TransbankException as e:
        print(f"❌ TransbankException en POLL: {e}")
        print(f"   Causa: {e.__cause__}")
        print("\n💡 El puerto se abrió pero el POS no responde")
        print("   - Cambia el POS a modo 'POS INTEGRADO'")
        print("   - Verifica el baudrate correcto")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False


def test_load_keys(pos):
    separador("TEST 5: Cargar Llaves")
    try:
        print("⚠️  ATENCIÓN: El POS puede pedir confirmación en pantalla")
        print("   Si aparece '¿Desea cargar llaves criptográficas?'")
        print("   PRESIONA SÍ/ACEPTAR en el POS físico")
        print("\nEjecutando load_keys()...")
        print("Esperando respuesta (puede tardar 60+ segundos)...")
        
        resultado = pos.load_keys()
        
        print(f"✅ Comando load_keys ejecutado")
        print(f"   Respuesta: {resultado}")
        
        if isinstance(resultado, dict):
            response_code = resultado.get('response_code', -1)
            if response_code == 0:
                print(f"   ✅ Llaves cargadas exitosamente")
                print(f"   Commerce Code: {resultado.get('commerce_code', 'N/A')}")
                print(f"   Terminal ID: {resultado.get('terminal_id', 'N/A')}")
            else:
                print(f"   ⚠️  Response code: {response_code}")
                print(f"   Puede que el usuario haya cancelado o haya un error")
        
        return True
    except TransbankException as e:
        print(f"❌ TransbankException: {e}")
        print(f"   Causa: {e.__cause__}")
        print("\n💡 Posibles causas:")
        print("   - Usuario canceló en el POS")
        print("   - POS sin conexión a Transbank")
        print("   - Timeout (esperó más de 90 segundos)")
        return False
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False


def test_con_todos_baudrates(puerto):
    separador(f"TEST 6: Probar Todos los Baudrates en {puerto}")
    baudrates = [115200, 9600, 19200, 38400, 57600, 4800, 2400, 1200]
    
    for baudrate in baudrates:
        print(f"\n--- Probando baudrate: {baudrate} ---")
        pos = test_abrir_puerto(puerto, baudrate)
        
        if pos:
            if test_poll(pos):
                print(f"\n🎉 ¡ÉXITO! Baudrate correcto: {baudrate}")
                pos.close_port()
                return baudrate
            pos.close_port()
            time.sleep(0.5)
    
    print(f"\n❌ No se pudo conectar con ningún baudrate")
    return None


def test_pyserial_directo(puerto, baudrate):
    separador(f"TEST 7: Conexión Directa con pyserial")
    try:
        import serial
        print(f"Intentando conexión directa a {puerto} @ {baudrate}...")
        
        ser = serial.Serial(
            port=puerto,
            baudrate=baudrate,
            timeout=2,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE
        )
        
        if ser.is_open:
            print(f"✅ Puerto abierto con pyserial")
            print(f"   Baudrate: {ser.baudrate}")
            print(f"   Timeout: {ser.timeout}")
            print(f"   Bytesize: {ser.bytesize}")
            print(f"   Parity: {ser.parity}")
            print(f"   Stopbits: {ser.stopbits}")
            
            # Verificar si hay datos disponibles
            print("\nVerificando comunicación...")
            time.sleep(0.5)
            
            if ser.in_waiting > 0:
                response = ser.read(ser.in_waiting)
                print(f"✅ Datos disponibles: {response.hex()}")
            else:
                print("⚠️  No hay datos esperando")
            
            ser.close()
            return True
        else:
            print("❌ No se pudo abrir el puerto")
            return False
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return False


def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║         DIAGNÓSTICO TRANSBANK POS - PYTHON SDK               ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    # TEST 1: Listar puertos
    puertos = test_listar_puertos()
    
    if not puertos:
        print("\n❌ No se pueden continuar los tests sin puertos disponibles")
        sys.exit(1)
    
    # Extraer nombres de puertos
    nombres_puertos = []
    for p in puertos:
        if isinstance(p, dict):
            nombres_puertos.append(p.get('port'))
        else:
            nombres_puertos.append(p)
    
    # TEST 2 y 3: Para cada puerto encontrado
    for puerto in nombres_puertos:
        test_info_puerto(puerto)
        
        # TEST 3: Intentar abrir con baudrate por defecto
        pos = test_abrir_puerto(puerto, 115200)
        
        if pos:
            # TEST 4: POLL
            if test_poll(pos):
                # TEST 5: Load Keys (opcional)
                print("\n¿Deseas probar la carga de llaves? (s/N): ", end='')
                try:
                    respuesta = input().strip().lower()
                    if respuesta == 's':
                        test_load_keys(pos)
                except:
                    print("Saltando test de carga de llaves...")
                
                pos.close_port()
                
                print("\n" + "🎉"*30)
                print(f"✅ POS FUNCIONAL en {puerto} @ 115200")
                print("🎉"*30)
                break
            else:
                pos.close_port()
                # TEST 6: Probar otros baudrates
                baudrate_correcto = test_con_todos_baudrates(puerto)
                if baudrate_correcto:
                    break
        else:
            # TEST 6: Si no abrió, probar con todos los baudrates
            baudrate_correcto = test_con_todos_baudrates(puerto)
            if baudrate_correcto:
                break
        
        # TEST 7: Último recurso - pyserial directo
        test_pyserial_directo(puerto, 115200)
    
    separador("RESUMEN DE DIAGNÓSTICO")
    print("""
Si llegaste aquí y no funcionó:

1. ✅ Verifica que el POS esté ENCENDIDO
2. ✅ Verifica que esté en modo 'POS INTEGRADO' (no autónomo)
3. ✅ Verifica el cable USB (prueba otro cable)
4. ✅ En Linux ejecuta: sudo usermod -a -G dialout $USER
5. ✅ Reinicia el POS
6. ✅ Verifica drivers del fabricante (Verifone/Ingenico)

Para más ayuda visita: https://www.transbankdevelopers.cl
    """)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Diagnóstico interrumpido por el usuario")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error fatal: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

