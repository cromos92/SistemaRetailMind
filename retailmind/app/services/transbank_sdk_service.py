"""
Servicio de integración real con SDK Transbank POS Integrado
Maneja la comunicación WebSocket con el agente desktop de Transbank
"""

import asyncio
import websockets
import json
import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal
from django.utils import timezone
from django.conf import settings

logger = logging.getLogger(__name__)


class TransbankSDKService:
    """
    Servicio para comunicación real con SDK Transbank POS Integrado
    """
    
    def __init__(self):
        self.websocket_url = getattr(settings, 'TRANSBANK_WEBSOCKET_URL', 'ws://localhost:8090')
        self.connection = None
        self.is_connected = False
        self.timeout = 30  # segundos
        
    async def connect(self) -> bool:
        """
        Conectar al agente desktop de Transbank
        """
        try:
            # Usar asyncio.wait_for para manejar el timeout
            self.connection = await asyncio.wait_for(
                websockets.connect(self.websocket_url),
                timeout=self.timeout
            )
            self.is_connected = True
            logger.info("Conectado al agente Transbank POS")
            return True
            
        except asyncio.TimeoutError:
            logger.error("Timeout conectando al agente Transbank")
            return False
        except ConnectionRefusedError:
            logger.error("Conexión rechazada - Agente Transbank no disponible")
            return False
        except Exception as e:
            logger.error(f"Error conectando al agente Transbank: {str(e)}")
            return False
    
    async def disconnect(self):
        """
        Desconectar del agente desktop
        """
        if self.connection and not self.connection.closed:
            await self.connection.close()
        self.is_connected = False
        logger.info("Desconectado del agente Transbank")
    
    async def send_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enviar comando al agente y esperar respuesta
        """
        if not self.is_connected:
            connected = await self.connect()
            if not connected:
                return {
                    'success': False,
                    'error': 'No se pudo conectar al agente Transbank'
                }
        
        try:
            # Enviar comando
            command_json = json.dumps(command)
            await self.connection.send(command_json)
            logger.debug(f"Comando enviado: {command}")
            
            # Esperar respuesta con timeout
            response_json = await asyncio.wait_for(
                self.connection.recv(),
                timeout=self.timeout
            )
            
            response = json.loads(response_json)
            logger.debug(f"Respuesta recibida: {response}")
            
            return response
            
        except asyncio.TimeoutError:
            logger.error("Timeout esperando respuesta del agente")
            return {
                'success': False,
                'error': 'Timeout esperando respuesta del POS'
            }
        except websockets.exceptions.ConnectionClosed:
            logger.error("Conexión cerrada inesperadamente")
            self.is_connected = False
            return {
                'success': False,
                'error': 'Conexión perdida con el agente POS'
            }
        except Exception as e:
            logger.error(f"Error enviando comando: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_ports(self) -> Dict[str, Any]:
        """
        Obtener puertos disponibles
        """
        command = {
            "action": "getPorts"
        }
        
        response = await self.send_command(command)
        
        if response.get('success'):
            return {
                'success': True,
                'ports': response.get('ports', []),
                'message': 'Puertos obtenidos exitosamente'
            }
        else:
            return {
                'success': False,
                'error': response.get('error', 'Error obteniendo puertos'),
                'ports': []
            }
    
    async def open_port(self, port: str, baud_rate: int = 115200) -> Dict[str, Any]:
        """
        Abrir puerto de comunicación con terminal POS
        """
        command = {
            "action": "openPort",
            "port": port,
            "baudRate": baud_rate
        }
        
        response = await self.send_command(command)
        
        if response.get('success'):
            return {
                'success': True,
                'message': f'Puerto {port} abierto exitosamente',
                'port': port,
                'baud_rate': baud_rate
            }
        else:
            return {
                'success': False,
                'error': response.get('error', f'Error abriendo puerto {port}'),
                'suggestion': self._get_port_error_suggestion(response.get('error', ''))
            }
    
    async def close_port(self) -> Dict[str, Any]:
        """
        Cerrar puerto de comunicación
        """
        command = {
            "action": "closePort"
        }
        
        response = await self.send_command(command)
        return response
    
    async def test_connection(self, port: str, baud_rate: int = 115200) -> Dict[str, Any]:
        """
        Probar conexión con terminal POS
        """
        try:
            # Obtener puertos disponibles
            ports_response = await self.get_ports()
            if not ports_response['success']:
                return ports_response
            
            available_ports = ports_response['ports']
            if port not in available_ports:
                return {
                    'success': False,
                    'error': f'Puerto {port} no disponible',
                    'available_ports': available_ports,
                    'suggestion': 'Verifique que el terminal esté conectado y el puerto sea correcto'
                }
            
            # Intentar abrir puerto
            open_response = await self.open_port(port, baud_rate)
            if not open_response['success']:
                return open_response
            
            # Enviar comando de prueba (poll)
            poll_command = {
                "action": "poll"
            }
            
            poll_response = await self.send_command(poll_command)
            
            # Cerrar puerto
            await self.close_port()
            
            if poll_response.get('success'):
                return {
                    'success': True,
                    'message': 'Conexión exitosa con terminal POS',
                    'port': port,
                    'terminal_info': poll_response.get('terminalInfo', {}),
                    'available_ports': available_ports
                }
            else:
                return {
                    'success': False,
                    'error': 'Terminal no responde',
                    'suggestion': 'Verifique que el terminal esté encendido y funcionando'
                }
                
        except Exception as e:
            logger.error(f"Error en prueba de conexión: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def do_sale(
        self, 
        amount: Decimal, 
        ticket: str, 
        port: str, 
        baud_rate: int = 115200,
        send_status: bool = True
    ) -> Dict[str, Any]:
        """
        Realizar venta en terminal POS
        """
        try:
            # Abrir puerto
            open_response = await self.open_port(port, baud_rate)
            if not open_response['success']:
                return open_response
            
            # Convertir monto a centavos (Transbank espera centavos)
            amount_cents = int(amount * 100)
            
            # Comando de venta
            sale_command = {
                "action": "doSale",
                "amount": amount_cents,
                "ticket": ticket,
                "sendStatus": send_status
            }
            
            logger.info(f"Iniciando venta POS: ${amount} - Ticket: {ticket}")
            
            # Enviar comando de venta
            sale_response = await self.send_command(sale_command)
            
            # Procesar respuesta
            if sale_response.get('success'):
                # Extraer información de la respuesta
                result = self._process_sale_response(sale_response)
                result['port'] = port
                result['ticket'] = ticket
                result['amount'] = float(amount)
                
                logger.info(f"Venta POS completada: {result['status']} - {ticket}")
                return result
            else:
                error_msg = sale_response.get('error', 'Error desconocido en venta')
                logger.error(f"Error en venta POS: {error_msg}")
                
                return {
                    'success': False,
                    'status': 'ERROR',
                    'error': error_msg,
                    'suggestion': self._get_sale_error_suggestion(error_msg)
                }
                
        except Exception as e:
            logger.error(f"Excepción en venta POS: {str(e)}")
            return {
                'success': False,
                'status': 'ERROR',
                'error': str(e)
            }
        finally:
            # Siempre cerrar puerto
            try:
                await self.close_port()
            except:
                pass
    
    async def cancel_sale(self) -> Dict[str, Any]:
        """
        Cancelar venta en progreso
        """
        command = {
            "action": "cancelSale"
        }
        
        response = await self.send_command(command)
        
        if response.get('success'):
            return {
                'success': True,
                'message': 'Venta cancelada exitosamente'
            }
        else:
            return {
                'success': False,
                'error': response.get('error', 'Error cancelando venta')
            }
    
    def _process_sale_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Procesar respuesta de venta del POS
        """
        try:
            response_code = response.get('responseCode', '')
            
            # Mapear códigos de respuesta Transbank
            if response_code == '00':
                status = 'APROBADA'
                message = 'Transacción aprobada'
            elif response_code in ['01', '02', '03', '04', '05', '06', '07']:
                status = 'RECHAZADA'
                message = self._get_rejection_message(response_code)
            elif response_code == '96':
                status = 'ERROR'
                message = 'Error del sistema POS'
            elif response_code == '97':
                status = 'TIMEOUT'
                message = 'Timeout en la transacción'
            else:
                status = 'ERROR'
                message = f'Código de respuesta desconocido: {response_code}'
            
            # Extraer información de la tarjeta
            card_type = self._determine_card_type(response.get('cardType', ''))
            
            return {
                'success': status == 'APROBADA',
                'status': status,
                'message': message,
                'response_code': response_code,
                'authorization_code': response.get('authorizationCode', ''),
                'card_type': card_type,
                'card_number': response.get('cardNumber', ''),
                'card_brand': response.get('cardBrand', ''),
                'operation_number': response.get('operationNumber', ''),
                'commerce_code': response.get('commerceCode', ''),
                'terminal_id': response.get('terminalId', ''),
                'installments': response.get('installments', 1),
                'raw_response': response
            }
            
        except Exception as e:
            logger.error(f"Error procesando respuesta de venta: {str(e)}")
            return {
                'success': False,
                'status': 'ERROR',
                'message': f'Error procesando respuesta: {str(e)}',
                'raw_response': response
            }
    
    def _determine_card_type(self, card_type_str: str) -> str:
        """
        Determinar tipo de tarjeta basado en respuesta del POS
        """
        card_type_upper = card_type_str.upper()
        
        if 'DEBITO' in card_type_upper or 'DEBIT' in card_type_upper:
            return 'DEBITO'
        elif 'CREDITO' in card_type_upper or 'CREDIT' in card_type_upper:
            return 'CREDITO'
        elif 'PREPAGO' in card_type_upper or 'PREPAID' in card_type_upper:
            return 'PREPAGO'
        else:
            return 'DESCONOCIDO'
    
    def _get_rejection_message(self, response_code: str) -> str:
        """
        Obtener mensaje de rechazo basado en código
        """
        rejection_messages = {
            '01': 'Tarjeta no válida',
            '02': 'Código de autorización inválido',
            '03': 'Comercio no válido',
            '04': 'Retener tarjeta',
            '05': 'Transacción no autorizada',
            '06': 'Error en la tarjeta',
            '07': 'Retener tarjeta - condiciones especiales'
        }
        
        return rejection_messages.get(response_code, f'Transacción rechazada - Código: {response_code}')
    
    def _get_port_error_suggestion(self, error: str) -> str:
        """
        Obtener sugerencia para errores de puerto
        """
        error_lower = error.lower()
        
        if 'access denied' in error_lower or 'permission' in error_lower:
            return 'Ejecute la aplicación como administrador o verifique permisos del puerto'
        elif 'port not found' in error_lower or 'no such file' in error_lower:
            return 'Verifique que el puerto existe y el terminal esté conectado'
        elif 'port in use' in error_lower or 'busy' in error_lower:
            return 'El puerto está siendo usado por otra aplicación. Cierre otras aplicaciones POS'
        elif 'timeout' in error_lower:
            return 'Verifique la conexión del terminal y la velocidad de conexión'
        else:
            return 'Verifique la conexión física del terminal y que esté encendido'
    
    def _get_sale_error_suggestion(self, error: str) -> str:
        """
        Obtener sugerencia para errores de venta
        """
        error_lower = error.lower()
        
        if 'timeout' in error_lower:
            return 'La transacción tardó demasiado. Verifique la conexión del terminal'
        elif 'cancelled' in error_lower or 'cancelada' in error_lower:
            return 'La transacción fue cancelada por el usuario'
        elif 'card' in error_lower and 'error' in error_lower:
            return 'Error leyendo la tarjeta. Intente nuevamente'
        elif 'communication' in error_lower:
            return 'Error de comunicación con el terminal. Verifique la conexión'
        else:
            return 'Error en la transacción. Verifique el terminal y intente nuevamente'


# Función de conveniencia para uso síncrono
def run_transbank_operation(operation_func, *args, **kwargs):
    """
    Ejecutar operación Transbank de forma síncrona
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        sdk = TransbankSDKService()
        result = loop.run_until_complete(operation_func(sdk, *args, **kwargs))
        
        loop.close()
        return result
        
    except Exception as e:
        logger.error(f"Error ejecutando operación Transbank: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }


# Funciones específicas para cada operación
async def test_pos_connection(sdk: TransbankSDKService, port: str, baud_rate: int = 115200):
    """Probar conexión POS"""
    return await sdk.test_connection(port, baud_rate)


async def execute_pos_sale(sdk: TransbankSDKService, amount: Decimal, ticket: str, port: str, baud_rate: int = 115200):
    """Ejecutar venta POS"""
    return await sdk.do_sale(amount, ticket, port, baud_rate)


async def get_available_ports(sdk: TransbankSDKService):
    """Obtener puertos disponibles"""
    return await sdk.get_ports()


async def cancel_pos_sale(sdk: TransbankSDKService):
    """Cancelar venta POS"""
    return await sdk.cancel_sale()
