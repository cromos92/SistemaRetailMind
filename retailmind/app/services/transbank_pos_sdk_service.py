"""
Servicio POS usando Transbank POS SDK (Conexión Serial Directa)
Sin persistencia en base de datos - Operaciones en memoria
Con auto-conexión, reintentos y manejo mejorado de errores
"""

from transbank import POSIntegrado
from transbank.error.transbank_exception import TransbankException
import logging

logger = logging.getLogger(__name__)


class POSService:
    """
    Singleton para gestión de terminal POS Transbank
    Maneja conexión serial directa sin base de datos
    Incluye auto-detección de puertos y reintentos
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.pos = POSIntegrado()
            cls._instance.pos.timeout = 150  # 150 segundos (2.5 minutos)
            cls._instance.puerto_conectado = None
            cls._instance.baudrate_actual = None
            logger.info("Instancia POSService creada")
        return cls._instance

    def listar_puertos(self, solo_disponibles=True):
        """
        Retorna lista de puertos seriales disponibles
        
        Args:
            solo_disponibles (bool): Si True, filtra solo puertos accesibles (excluye Bluetooth/virtuales)
        
        Returns:
            list: Lista de puertos COM/ttyUSB disponibles
        """
        try:
            # Obtener todos los puertos usando el SDK
            todos_puertos = self.pos.list_ports()
            
            if not solo_disponibles:
                logger.info(f"Puertos (todos): {todos_puertos}")
                return todos_puertos
            
            # Filtrar solo puertos realmente disponibles
            import serial
            puertos_disponibles = []
            
            for puerto_info in todos_puertos:
                puerto = puerto_info['port']
                descripcion = puerto_info['description']
                
                # Excluir puertos Bluetooth y virtuales
                if 'bluetooth' in descripcion.lower() or 'bt' in descripcion.lower():
                    logger.debug(f"Excluido (Bluetooth): {puerto} - {descripcion}")
                    continue
                
                # Intentar abrir el puerto para verificar que está disponible
                try:
                    ser = serial.Serial(puerto, timeout=0.1)
                    ser.close()
                    puertos_disponibles.append(puerto_info)
                    logger.debug(f"Puerto disponible: {puerto} - {descripcion}")
                except (OSError, serial.SerialException) as e:
                    logger.debug(f"Puerto no disponible: {puerto} - {str(e)}")
                    continue
            
            logger.info(f"Puertos disponibles (filtrados): {puertos_disponibles}")
            return puertos_disponibles
            
        except Exception as e:
            logger.error(f"Error listando puertos: {str(e)}")
            raise Exception(f"Error al listar puertos: {e}")

    def autoconectar(self):
        """
        Intenta conectar automáticamente al POS.
        Prueba todos los puertos disponibles con diferentes baudrates.
        
        Returns:
            dict: {'conectado': True, 'puerto': 'COM9', 'baudrate': 115200}
            
        Raises:
            Exception: Si no se puede conectar en ningún puerto
        """
        puertos = self.listar_puertos(solo_disponibles=True)
        baudrates = [115200, 9600, 19200, 38400, 57600]
        
        if not puertos:
            raise Exception("No se encontraron puertos disponibles")
        
        for puerto_info in puertos:
            puerto = puerto_info['port']
            logger.info(f"Intentando conectar a {puerto}")
            
            for baudrate in baudrates:
                try:
                    logger.info(f"  Probando baudrate {baudrate}")
                    resultado = self.pos.open_port(port=puerto, baud_rate=baudrate)
                    
                    if resultado:
                        # Verificar si realmente está conectado con POLL
                        try:
                            if self.pos.poll():
                                self.puerto_conectado = puerto
                                self.baudrate_actual = baudrate
                                logger.info(f"✅ Conectado exitosamente a {puerto} @ {baudrate}")
                                return {
                                    'conectado': True,
                                    'puerto': puerto,
                                    'baudrate': baudrate,
                                    'descripcion': puerto_info.get('description', '')
                                }
                        except:
                            # Si poll falla, cerrar y seguir intentando
                            try:
                                self.pos.close_port()
                            except:
                                pass
                            continue
                    
                except Exception as e:
                    logger.warning(f"  Fallo en {puerto} @ {baudrate}: {e}")
                    continue
        
        raise Exception("No se pudo conectar al POS en ningún puerto")
    
    def conectar(self, puerto, baud_rate=115200):
        """
        Abre conexión con el POS en puerto específico.
        Incluye verificación con POLL.
        
        Args:
            puerto (str): Puerto serial (ej: 'COM3', '/dev/ttyUSB0')
            baud_rate (int): Velocidad de conexión (default: 115200)
            
        Returns:
            bool: True si conexión exitosa y verificada
            
        Raises:
            Exception: Si hay error en la conexión
        """
        try:
            logger.info(f"Intentando conectar a {puerto} con baudrate {baud_rate}")
            resultado = self.pos.open_port(port=puerto, baud_rate=baud_rate)
            
            if resultado:
                # Verificar conexión con POLL
                try:
                    if self.pos.poll():
                        self.puerto_conectado = puerto
                        self.baudrate_actual = baud_rate
                        logger.info(f"✅ Conectado y verificado: {puerto} @ {baud_rate}")
                        return True
                except Exception as poll_error:
                    logger.warning(f"Puerto abierto pero POLL falló: {poll_error}")
                    try:
                        self.pos.close_port()
                    except:
                        pass
                    raise Exception("Puerto abierto pero POS no responde a POLL")
            
            return False
        except TransbankException as e:
            logger.error(f"Error Transbank al conectar: {e}")
            raise Exception(f"Error al conectar: {e}")
    
    def conectar_con_reintentos(self, puerto, max_intentos=3):
        """
        Intenta conectar múltiples veces con diferentes baudrates.
        
        Args:
            puerto (str): Puerto a intentar
            max_intentos (int): Número máximo de intentos (default: 3)
            
        Returns:
            dict: {'conectado': True, 'puerto': 'COM9', 'baudrate': 115200, 'intentos': 2}
            
        Raises:
            Exception: Si no se puede conectar después de todos los intentos
        """
        baudrates = [115200, 9600, 19200, 38400, 57600]
        
        for intento in range(max_intentos):
            for baudrate in baudrates:
                try:
                    logger.info(f"Intento {intento + 1}/{max_intentos} - Puerto: {puerto} @ {baudrate}")
                    
                    if self.conectar(puerto, baudrate):
                        return {
                            'conectado': True,
                            'puerto': puerto,
                            'baudrate': baudrate,
                            'intentos': intento + 1
                        }
                except Exception as e:
                    logger.warning(f"Intento fallido: {e}")
                    continue
        
        raise Exception(f"No se pudo conectar después de {max_intentos} intentos")

    def desconectar(self):
        """
        Cierra conexión con el POS
        
        Returns:
            bool: True si desconexión exitosa
        """
        try:
            resultado = self.pos.close_port()
            if resultado:
                logger.info(f"Desconectado de {self.puerto_conectado}")
                self.puerto_conectado = None
                self.baudrate_actual = None
            return resultado
        except Exception as e:
            logger.error(f"Error al desconectar: {str(e)}")
            return False
    
    def obtener_info_puerto(self):
        """
        Retorna información del puerto actual y configuración
        
        Returns:
            dict: {'puerto_conectado': 'COM9', 'baudrate': 115200, 'timeout': 150}
        """
        return {
            'puerto_conectado': self.puerto_conectado,
            'baudrate': self.baudrate_actual,
            'timeout': self.pos.timeout
        }

    def verificar_conexion(self):
        """
        Ejecuta POLL para verificar si POS está conectado
        
        Returns:
            bool: True si POS responde correctamente
        """
        try:
            respuesta = self.pos.poll()
            logger.info(f"POLL exitoso: {respuesta}")
            return True
        except TransbankException as e:
            logger.error(f"Error en POLL: {str(e)}")
            return False

    def cargar_llaves(self):
        """
        Carga llaves en el POS (requerido una vez al día o tras conectar)
        Proceso: El POS se conecta a Transbank y descarga las llaves de seguridad.
        Puede tardar 30-60 segundos.
        
        Returns:
            dict: Respuesta del POS con resultado de carga de llaves
                {
                    'function_code': 810,
                    'response_code': 0,  # 0 = éxito
                    'commerce_code': '597020000541',
                    'terminal_id': 'ABC123',
                    ...
                }
            
        Raises:
            TransbankException: Si hay error cargando llaves
        """
        try:
            # Guardar timeout original
            timeout_original = self.pos.timeout
            
            # Aumentar timeout temporalmente para load_keys (puede tardar 60+ segundos)
            self.pos.timeout = 90  # 90 segundos para carga de llaves
            
            logger.info("Iniciando carga de llaves en el POS...")
            logger.info("⏳ Esto puede tardar 30-60 segundos - El POS se conecta a Transbank")
            
            # Ejecutar load_keys del SDK
            respuesta = self.pos.load_keys()
            
            # Restaurar timeout original
            self.pos.timeout = timeout_original
            
            # Verificar response_code
            response_code = respuesta.get('response_code', -1)
            
            if response_code == 0:
                logger.info(f"✅ Llaves cargadas exitosamente")
                logger.info(f"   Commerce Code: {respuesta.get('commerce_code', 'N/A')}")
                logger.info(f"   Terminal ID: {respuesta.get('terminal_id', 'N/A')}")
            else:
                logger.warning(f"⚠️ Load keys retornó código: {response_code}")
            
            logger.info(f"Respuesta completa: {respuesta}")
            return respuesta
            
        except TransbankException as e:
            # Restaurar timeout en caso de error
            try:
                self.pos.timeout = timeout_original
            except:
                pass
            
            logger.error(f"❌ Error cargando llaves: {str(e)}")
            raise
        except Exception as e:
            # Restaurar timeout en caso de error genérico
            try:
                self.pos.timeout = timeout_original
            except:
                pass
            
            logger.error(f"❌ Error inesperado cargando llaves: {str(e)}")
            raise TransbankException(f"Error cargando llaves: {e}")

    def venta(self, monto, ticket, con_callback=False, callback_func=None):
        """
        Procesa venta en el POS
        
        Args:
            monto (int): Monto en pesos chilenos sin decimales
            ticket (str): Identificador único de la venta
            con_callback (bool): Si se envían mensajes intermedios
            callback_func (callable): Función callback para mensajes intermedios
            
        Returns:
            dict: Respuesta del POS con detalles de la transacción
            
        Raises:
            TransbankException: Si hay error en la venta
        """
        try:
            logger.info(f"Iniciando venta: ${monto} - Ticket: {ticket}")
            
            if con_callback and callback_func:
                respuesta = self.pos.sale(monto, ticket, send_status=True, callback=callback_func)
            else:
                respuesta = self.pos.sale(monto, ticket)
            
            logger.info(f"Venta completada: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error en venta: {str(e)}")
            raise

    def venta_multicodigo(self, monto, ticket, commerce_code, con_callback=False, callback_func=None):
        """
        Venta con código de comercio específico
        
        Args:
            monto (int): Monto en pesos chilenos
            ticket (str): Identificador único
            commerce_code (int): Código de comercio
            con_callback (bool): Enviar mensajes intermedios
            callback_func (callable): Función callback
            
        Returns:
            dict: Respuesta del POS
        """
        try:
            logger.info(f"Venta multicodigo: ${monto} - Ticket: {ticket} - Commerce: {commerce_code}")
            
            if con_callback and callback_func:
                respuesta = self.pos.multicode_sale(monto, ticket, commerce_code, send_status=True, callback=callback_func)
            else:
                respuesta = self.pos.multicode_sale(monto, ticket, commerce_code)
            
            logger.info(f"Venta multicodigo completada: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error en venta multicodigo: {str(e)}")
            raise

    def ultima_venta(self):
        """
        Consulta última venta realizada
        
        Returns:
            dict: Información de la última venta
        """
        try:
            respuesta = self.pos.last_sale()
            logger.info(f"Última venta obtenida: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error obteniendo última venta: {str(e)}")
            raise

    def ultima_venta_multicodigo(self, enviar_voucher=True):
        """
        Consulta última venta multicodigo
        
        Args:
            enviar_voucher (bool): Si se imprime voucher en POS
            
        Returns:
            dict: Información de última venta multicodigo
        """
        try:
            respuesta = self.pos.multicode_last_sale(enviar_voucher)
            logger.info(f"Última venta multicodigo obtenida: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error obteniendo última venta multicodigo: {str(e)}")
            raise

    def anular(self, operation_id):
        """
        Anula venta por operation_id
        
        Args:
            operation_id (int): ID de operación a anular
            
        Returns:
            dict: Respuesta de anulación
        """
        try:
            logger.info(f"Anulando operación: {operation_id}")
            respuesta = self.pos.refund(operation_id)
            logger.info(f"Anulación completada: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error en anulación: {str(e)}")
            raise

    def totales(self):
        """
        Consulta totales del día
        
        Returns:
            dict: Totales de ventas del día
        """
        try:
            respuesta = self.pos.totals()
            logger.info(f"Totales obtenidos: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error obteniendo totales: {str(e)}")
            raise

    def detalles(self, imprimir_en_pos=False):
        """
        Consulta detalles de ventas
        
        Args:
            imprimir_en_pos (bool): Si True imprime en POS, si False en caja
            
        Returns:
            dict: Detalles de ventas
        """
        try:
            respuesta = self.pos.details(print_on_pos=imprimir_en_pos)
            logger.info(f"Detalles obtenidos: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error obteniendo detalles: {str(e)}")
            raise

    def cerrar_dia(self):
        """
        Cierra operaciones del día (cierre de caja)
        
        Returns:
            dict: Respuesta de cierre
        """
        try:
            logger.info("Iniciando cierre de día")
            respuesta = self.pos.close()
            logger.info(f"Cierre completado: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error en cierre de día: {str(e)}")
            raise

