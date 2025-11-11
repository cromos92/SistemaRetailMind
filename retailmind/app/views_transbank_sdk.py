"""
API REST para integración Transbank POS SDK
Endpoints sin persistencia en base de datos
"""

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from .services.transbank_pos_sdk_service import POSService
from transbank.error.transbank_exception import TransbankException
from .models import ConfiguracionPOS, TransaccionPOS, Sucursal, TicketDetallePago, Ticket
import logging

logger = logging.getLogger(__name__)

# Instancia singleton del servicio POS
pos_service = POSService()


@login_required
def gestion_transbank_pos_sdk(request):
    """
    Vista principal de gestión Transbank POS SDK
    Interfaz simplificada con métodos esenciales del SDK
    """
    context = {}
    
    try:
        # Obtener configuración guardada si existe
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if sucursal_id:
            config_guardada = ConfiguracionPOS.objects.filter(
                sucursal_id=sucursal_id,
                tipo_pos='SDK_SERIAL',
                activo=True
            ).first()
            context['config_guardada'] = config_guardada
    except Exception as e:
        logger.warning(f"No se pudo cargar configuración: {e}")
        context['config_guardada'] = None
    
    return render(request, 'vistas/transbank_pos_simple.html', context)


@api_view(['GET'])
def listar_puertos(request):
    """
    GET /app/pos/transbank/puertos/?todos=false
    
    Retorna lista de puertos seriales disponibles
    
    Query params:
        todos (bool): Si es 'true', lista todos los puertos. Si es 'false' o no se envía, 
                     solo lista puertos disponibles (excluye Bluetooth/virtuales)
    
    Response:
        {
            "success": true,
            "puertos": [
                {"port": "COM9", "description": "VX 520 GPRS Terminal"},
                ...
            ]
        }
    """
    try:
        # Verificar si se solicitan todos los puertos o solo disponibles
        listar_todos = request.query_params.get('todos', 'false').lower() == 'true'
        solo_disponibles = not listar_todos
        
        puertos = pos_service.listar_puertos(solo_disponibles=solo_disponibles)
        
        return Response({
            'success': True,
            'puertos': puertos,
            'filtrado': solo_disponibles
        })
    except Exception as e:
        logger.error(f"Error listando puertos: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@login_required
def autoconectar(request):
    """
    POST /app/pos/transbank/autoconectar/
    
    Auto-conecta al POS y GUARDA la configuración en DB
    
    Response:
        {
            "success": true,
            "conectado": true,
            "puerto": "COM9",
            "baudrate": 115200,
            "descripcion": "VX 520 GPRS Terminal",
            "config_id": 123
        }
    """
    try:
        # Auto-conectar con el SDK
        resultado = pos_service.autoconectar()
        
        # Guardar configuración en DB
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if sucursal_id:
            with db_transaction.atomic():
                sucursal = Sucursal.objects.get(id=sucursal_id)
                
                # Actualizar o crear configuración
                config, created = ConfiguracionPOS.objects.update_or_create(
                    sucursal=sucursal,
                    tipo_pos='SDK_SERIAL',
                    defaults={
                        'nombre': f'VX520-{resultado["puerto"]}',
                        'puerto_conexion': resultado['puerto'],
                        'velocidad_conexion': resultado['baudrate'],
                        'activo': True,
                        'es_principal': True,
                        'estado_conexion': 'CONECTADO',
                        'observaciones': f'Auto-detectado: {resultado.get("descripcion", "")}'
                    }
                )
                
                logger.info(f"Configuración {'creada' if created else 'actualizada'}: {config.id}")
                resultado['config_id'] = config.id
                resultado['guardado_en_db'] = True
        else:
            resultado['guardado_en_db'] = False
            logger.warning("No hay sucursal en sesión - configuración no guardada")
        
        return Response({
            'success': True,
            **resultado
        })
    except Exception as e:
        logger.error(f"Error en autoconexión: {str(e)}")
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
            "success": true,
            "conectado": true,
            "puerto": "COM3",
            "baud_rate": 115200
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
def conectar_con_reintentos(request):
    """
    POST /app/pos/transbank/conectar-reintentos/
    
    Intenta conectar a un puerto específico con múltiples reintentos
    probando diferentes baudrates.
    
    Body:
        {
            "puerto": "COM9",
            "max_intentos": 3  // opcional, default 3
        }
    
    Response:
        {
            "success": true,
            "conectado": true,
            "puerto": "COM9",
            "baudrate": 115200,
            "intentos": 2
        }
    """
    puerto = request.data.get('puerto')
    max_intentos = request.data.get('max_intentos', 3)
    
    if not puerto:
        return Response({
            'success': False,
            'error': 'Puerto requerido'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        resultado = pos_service.conectar_con_reintentos(puerto, max_intentos)
        return Response({
            'success': True,
            **resultado
        })
    except Exception as e:
        logger.error(f"Error conectando con reintentos: {str(e)}")
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
            "success": true,
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


@api_view(['GET'])
def obtener_info_puerto(request):
    """
    GET /app/pos/transbank/info-puerto/
    
    Obtiene información del puerto actual y configuración
    
    Response:
        {
            "success": true,
            "puerto_conectado": "COM9",
            "baudrate": 115200,
            "timeout": 150
        }
    """
    try:
        info = pos_service.obtener_info_puerto()
        return Response({
            'success': True,
            **info
        })
    except Exception as e:
        logger.error(f"Error obteniendo info puerto: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def cargar_llaves(request):
    """
    POST /app/pos/transbank/cargar-llaves/
    
    Carga llaves en el POS (ejecutar 1 vez al día o tras conectar).
    El POS se conecta a Transbank y descarga las llaves de seguridad.
    IMPORTANTE: Puede tardar 30-60 segundos.
    
    Response exitosa (response_code = 0):
        {
            "success": true,
            "carga_exitosa": true,
            "function_code": 810,
            "response_code": 0,
            "commerce_code": "597020000541",
            "terminal_id": "ABC123",
            "mensaje": "Llaves cargadas correctamente"
        }
    
    Response con error (response_code != 0):
        {
            "success": true,
            "carga_exitosa": false,
            "response_code": 5,
            "mensaje": "Error en carga de llaves - verificar conexión del POS"
        }
    """
    try:
        logger.info("🔑 Iniciando carga de llaves...")
        resultado = pos_service.cargar_llaves()
        
        # Verificar si la carga fue exitosa
        response_code = resultado.get('response_code', -1)
        carga_exitosa = response_code == 0
        
        if carga_exitosa:
            mensaje = "Llaves cargadas correctamente"
        else:
            mensaje = f"Error en carga de llaves - Código: {response_code}"
        
        return Response({
            'success': True,
            'carga_exitosa': carga_exitosa,
            'mensaje': mensaje,
            **resultado
        })
        
    except TransbankException as e:
        logger.error(f"❌ Error cargando llaves: {str(e)}")
        return Response({
            'success': False,
            'carga_exitosa': False,
            'error': str(e),
            'causa': str(e.__cause__) if e.__cause__ else None,
            'mensaje': 'Excepción al cargar llaves - Verificar conexión con POS'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    except Exception as e:
        logger.error(f"❌ Error inesperado: {str(e)}")
        return Response({
            'success': False,
            'carga_exitosa': False,
            'error': str(e),
            'mensaje': 'Error inesperado al cargar llaves'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@login_required
def venta(request):
    """
    POST /app/pos/transbank/venta/
    
    Procesa venta con SDK y GUARDA en DB (TransaccionPOS)
    
    Body:
        {
            "monto": 25000,
            "ticket": "TKT123",
            "ticket_id": 456  // opcional - ID del ticket en DB
        }
    """
    monto = request.data.get('monto')
    ticket_str = request.data.get('ticket')
    ticket_id = request.data.get('ticket_id')
    web_serial = request.data.get('web_serial', False)
    respuesta_pos = request.data.get('respuesta_pos')
    
    if not monto or not ticket_str:
        return Response({
            'success': False,
            'error': 'Monto y ticket requeridos'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Si viene de Web Serial API (producción), usar esa respuesta
        if web_serial and respuesta_pos:
            resultado = respuesta_pos
            logger.info(f"Venta procesada con Web Serial API: {resultado}")
        else:
            # Procesar venta con SDK Python
            resultado = pos_service.venta(int(monto), str(ticket_str))
        
        # Determinar si fue exitosa
        response_code = str(resultado.get('response_code', ''))
        es_exitosa = response_code in ['0', '00']
        
        # Guardar en DB si fue exitosa
        if es_exitosa:
            sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
            
            if sucursal_id:
                with db_transaction.atomic():
                    # Obtener configuración POS
                    config = ConfiguracionPOS.objects.filter(
                        sucursal_id=sucursal_id,
                        tipo_pos='SDK_SERIAL',
                        activo=True
                    ).first()
                    
                    # Obtener ticket si existe
                    ticket_obj = None
                    if ticket_id:
                        ticket_obj = Ticket.objects.filter(
                            id=ticket_id,
                            sucursal_id=sucursal_id
                        ).first()
                    
                    # Crear TransaccionPOS
                    transaccion = TransaccionPOS.objects.create(
                        configuracion_pos=config if config else None,
                        ticket=ticket_obj,
                        monto=monto,
                        tipo_transaccion='VENTA',
                        estado='APROBADA',
                        codigo_autorizacion=resultado.get('authorization_code', ''),
                        numero_operacion=str(resultado.get('operation_number', '')),
                        tipo_tarjeta=resultado.get('card_type', 'DESCONOCIDO'),
                        ultimos_4_digitos=str(resultado.get('card_number', ''))[-4:] if resultado.get('card_number') else '',
                        nombre_tarjeta=resultado.get('card_brand', ''),
                        numero_cuotas=resultado.get('installments', 1),
                        codigo_comercio=resultado.get('commerce_code', ''),
                        terminal_id=resultado.get('terminal_id', ''),
                        usuario_operador=request.user,
                        observaciones=f'Ticket POS: {ticket_str}'
                    )
                    
                    logger.info(f"TransaccionPOS creada: {transaccion.id}")
                    resultado['transaccion_id'] = transaccion.id
                    resultado['guardado_en_db'] = True
        
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

