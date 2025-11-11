"""
Servicio POS usando Transbank POS SDK (Conexión Serial Directa)
Sin persistencia en base de datos - Operaciones en memoria
"""

from transbank import POSIntegrado
from transbank.error.transbank_exception import TransbankException
import logging

logger = logging.getLogger(__name__)


class POSService:
    """
    Singleton para gestión de terminal POS Transbank
    Maneja conexión serial directa sin base de datos
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.pos = POSIntegrado()
            cls._instance.pos.timeout = 150  # 150 segundos (2.5 minutos)
            cls._instance.puerto_conectado = None
            logger.info("Instancia POSService creada")
        return cls._instance

    def listar_puertos(self):
        """
        Retorna lista de puertos seriales disponibles
        
        Returns:
            list: Lista de puertos COM/ttyUSB disponibles
        """
        try:
            puertos = self.pos.list_ports()
            logger.info(f"Puertos disponibles: {puertos}")
            return puertos
        except Exception as e:
            logger.error(f"Error listando puertos: {str(e)}")
            raise Exception(f"Error al listar puertos: {e}")

    def conectar(self, puerto, baud_rate=115200):
        """
        Abre conexión con el POS
        
        Args:
            puerto (str): Puerto serial (ej: 'COM3', '/dev/ttyUSB0')
            baud_rate (int): Velocidad de conexión (default: 115200)
            
        Returns:
            bool: True si conexión exitosa, False en caso contrario
            
        Raises:
            Exception: Si hay error en la conexión
        """
        try:
            resultado = self.pos.open_port(port=puerto, baudrate=baud_rate)
            if resultado:
                self.puerto_conectado = puerto
                logger.info(f"Conectado exitosamente a {puerto} @ {baud_rate}")
            return resultado
        except TransbankException as e:
            logger.error(f"Error al conectar a {puerto}: {str(e)}")
            raise Exception(f"Error al conectar: {e}")

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
            return resultado
        except Exception as e:
            logger.error(f"Error al desconectar: {str(e)}")
            return False

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
        
        Returns:
            dict: Respuesta del POS con resultado de carga de llaves
            
        Raises:
            TransbankException: Si hay error cargando llaves
        """
        try:
            respuesta = self.pos.load_keys()
            logger.info(f"Llaves cargadas exitosamente: {respuesta}")
            return respuesta
        except TransbankException as e:
            logger.error(f"Error cargando llaves: {str(e)}")
            raise

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

