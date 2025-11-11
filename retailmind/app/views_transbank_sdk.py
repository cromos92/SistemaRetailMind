"""
API REST para integración Transbank POS SDK
Endpoints sin persistencia en base de datos
"""

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .services.transbank_pos_sdk_service import POSService
from transbank.error.transbank_exception import TransbankException
import logging

logger = logging.getLogger(__name__)

# Instancia singleton del servicio POS
pos_service = POSService()


@api_view(['GET'])
def listar_puertos(request):
    """
    GET /app/pos/transbank/puertos/
    
    Retorna lista de puertos seriales disponibles
    
    Response:
        {
            "puertos": ["COM3", "COM4", ...]
        }
    """
    try:
        puertos = pos_service.listar_puertos()
        return Response({
            'success': True,
            'puertos': puertos
        })
    except Exception as e:
        logger.error(f"Error listando puertos: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def conectar(request):
    """
    POST /app/pos/transbank/conectar/
    
    Body:
        {
            "puerto": "COM3",
            "baud_rate": 115200  // opcional, default 115200
        }
    
    Response:
        {
            "conectado": true,
            "puerto": "COM3"
        }
    """
    puerto = request.data.get('puerto')
    baud_rate = request.data.get('baud_rate', 115200)
    
    if not puerto:
        return Response({
            'success': False,
            'error': 'Puerto requerido'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        resultado = pos_service.conectar(puerto, baud_rate)
        return Response({
            'success': True,
            'conectado': resultado,
            'puerto': puerto,
            'baud_rate': baud_rate
        })
    except Exception as e:
        logger.error(f"Error conectando: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def desconectar(request):
    """
    POST /app/pos/transbank/desconectar/
    
    Cierra la conexión con el POS
    
    Response:
        {
            "desconectado": true
        }
    """
    try:
        resultado = pos_service.desconectar()
        return Response({
            'success': True,
            'desconectado': resultado
        })
    except Exception as e:
        logger.error(f"Error desconectando: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def verificar(request):
    """
    GET /app/pos/transbank/verificar/
    
    Verifica conexión con POLL
    
    Response:
        {
            "conectado": true
        }
    """
    try:
        conectado = pos_service.verificar_conexion()
        return Response({
            'success': True,
            'conectado': conectado
        })
    except Exception as e:
        logger.error(f"Error verificando conexión: {str(e)}")
        return Response({
            'success': False,
            'conectado': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def cargar_llaves(request):
    """
    POST /app/pos/transbank/cargar-llaves/
    
    Carga llaves en el POS (ejecutar 1 vez al día)
    
    Response:
        {
            "function_code": 810,
            "response_code": 0,
            "commerce_code": "597020000541",
            "terminal_id": "ABC123",
            ...
        }
    """
    try:
        resultado = pos_service.cargar_llaves()
        return Response({
            'success': True,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error cargando llaves: {str(e)}")
        return Response({
            'success': False,
            'error': str(e),
            'causa': str(e.__cause__) if e.__cause__ else None
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def venta(request):
    """
    POST /app/pos/transbank/venta/
    
    Body:
        {
            "monto": 25000,
            "ticket": "TKT123",
            "con_mensajes": false  // opcional
        }
    
    Response (exitosa):
        {
            "function_code": 200,
            "response_code": 0,
            "commerce_code": "597020000541",
            "terminal_id": "ABC123",
            "ticket": "TKT123",
            "authorization_code": "123456",
            "amount": 25000,
            "card_number": "************1234",
            "operation_number": 83,
            "card_type": "CR",
            ...
        }
    """
    monto = request.data.get('monto')
    ticket = request.data.get('ticket')
    con_mensajes = request.data.get('con_mensajes', False)
    
    if not monto or not ticket:
        return Response({
            'success': False,
            'error': 'Monto y ticket requeridos'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        mensajes_intermedios = []
        
        def callback(response):
            """Callback para mensajes intermedios del POS"""
            mensaje = {
                'mensaje': response.get('response_message', 'Procesando...'),
                'datos': response
            }
            mensajes_intermedios.append(mensaje)
            logger.debug(f"Mensaje intermedio: {mensaje}")
        
        if con_mensajes:
            resultado = pos_service.venta(int(monto), str(ticket), True, callback)
            resultado['mensajes_intermedios'] = mensajes_intermedios
        else:
            resultado = pos_service.venta(int(monto), str(ticket))
        
        # Determinar si fue exitosa (response_code = 0)
        es_exitosa = resultado.get('response_code') == 0
        
        return Response({
            'success': es_exitosa,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error en venta: {str(e)}")
        return Response({
            'success': False,
            'error': str(e),
            'causa': str(e.__cause__) if e.__cause__ else None
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def venta_multicodigo(request):
    """
    POST /app/pos/transbank/venta-multicodigo/
    
    Body:
        {
            "monto": 25000,
            "ticket": "TKT123",
            "commerce_code": 597020000541
        }
    
    Response: Similar a venta normal
    """
    monto = request.data.get('monto')
    ticket = request.data.get('ticket')
    commerce_code = request.data.get('commerce_code')
    
    if not all([monto, ticket, commerce_code]):
        return Response({
            'success': False,
            'error': 'Monto, ticket y commerce_code requeridos'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        resultado = pos_service.venta_multicodigo(int(monto), str(ticket), int(commerce_code))
        es_exitosa = resultado.get('response_code') == 0
        
        return Response({
            'success': es_exitosa,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error en venta multicodigo: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def ultima_venta(request):
    """
    GET /app/pos/transbank/ultima-venta/
    
    Consulta la última venta realizada
    
    Response: Similar a respuesta de venta
    """
    try:
        resultado = pos_service.ultima_venta()
        return Response({
            'success': True,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error obteniendo última venta: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def anular(request):
    """
    POST /app/pos/transbank/anular/
    
    Body:
        {
            "operation_id": 83
        }
    
    Response:
        {
            "function_code": 1200,
            "response_code": 0,
            "commerce_code": "597020000541",
            "terminal_id": "ABC123",
            "authorization_code": "123456",
            ...
        }
    """
    operation_id = request.data.get('operation_id')
    
    if not operation_id:
        return Response({
            'success': False,
            'error': 'operation_id requerido'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        resultado = pos_service.anular(int(operation_id))
        es_exitosa = resultado.get('response_code') == 0
        
        return Response({
            'success': es_exitosa,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error en anulación: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def totales(request):
    """
    GET /app/pos/transbank/totales/
    
    Consulta totales del día
    
    Response:
        {
            "function_code": 710,
            "response_code": 0,
            "tx_count": 15,
            "tx_total": 450000,
            ...
        }
    """
    try:
        resultado = pos_service.totales()
        return Response({
            'success': True,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error obteniendo totales: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
def detalles(request):
    """
    GET /app/pos/transbank/detalles/?imprimir_en_pos=false
    
    Consulta detalles de ventas
    
    Query params:
        imprimir_en_pos (bool): Si True imprime en POS, si False en caja
    
    Response: Detalles de transacciones
    """
    imprimir_en_pos = request.query_params.get('imprimir_en_pos', 'false').lower() == 'true'
    
    try:
        resultado = pos_service.detalles(imprimir_en_pos)
        return Response({
            'success': True,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error obteniendo detalles: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def cerrar_dia(request):
    """
    POST /app/pos/transbank/cerrar-dia/
    
    Cierra operaciones del día (cierre de caja)
    
    Response:
        {
            "function_code": 510,
            "response_code": 0,
            ...
        }
    """
    try:
        resultado = pos_service.cerrar_dia()
        es_exitosa = resultado.get('response_code') == 0
        
        return Response({
            'success': es_exitosa,
            **resultado
        })
    except TransbankException as e:
        logger.error(f"Error en cierre de día: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

