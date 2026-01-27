"""
API REST para integración Transbank POS con Web Serial API
El POS se conecta desde el navegador, el backend solo guarda transacciones
"""

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .services.transbank_simple_service import TransbankPersistenceService
from .models import ConfiguracionPOS
import logging

logger = logging.getLogger(__name__)

# Servicio de persistencia
persistence_service = TransbankPersistenceService()


@login_required
def gestion_transbank_pos_sdk(request):
    """
    Vista principal de gestión Transbank POS con Web Serial API
    La conexión al POS se hace desde el navegador
    """
    context = {}
    
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if sucursal_id:
            # Obtener configuración guardada si existe
            config_guardada = ConfiguracionPOS.objects.filter(
                sucursal_id=sucursal_id,
                tipo_pos='SDK_SERIAL',
                activo=True
            ).first()
            context['config_guardada'] = config_guardada
    except Exception as e:
        logger.warning(f"No se pudo cargar configuración: {e}")
        context['config_guardada'] = None
    
    return render(request, 'vistas/transbank_pos_sdk_oficial.html', context)


@login_required  
def gestion_transbank_pos_manual(request):
    """
    Vista con implementación manual (desarrollo)
    """
    context = {}
    
    try:
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


# ==================== ENDPOINTS ACTIVOS ====================

@api_view(['POST'])
@login_required
def autoconectar(request):
    """
    POST /app/pos/transbank/autoconectar/
    
    GUARDA la configuración del POS detectado desde el navegador
    
    Body:
        {
            "port": "COM9",
            "baudrate": 115200,
            "descripcion": "VX 520 GPRS Terminal"
        }
    """
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not sucursal_id:
            return Response({
                'success': False,
                'error': 'No hay sucursal en sesión'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Guardar configuración usando el servicio
        data = {
            'port': request.data.get('port', ''),
            'baudrate': request.data.get('baudrate', 115200),
            'nombre': f'POS-{request.data.get("port", "USB")}',
            'observaciones': f'Auto-detectado: {request.data.get("descripcion", "")}'
        }
        
        resultado = persistence_service.guardar_configuracion_pos(data, sucursal_id)
        
        return Response(resultado)
        
    except Exception as e:
        logger.error(f"Error en autoconectar: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@login_required
def venta(request):
    """
    POST /app/pos/transbank/venta/
    
    GUARDA una transacción procesada desde el navegador
    La venta se procesa en el navegador con Web Serial API,
    este endpoint solo guarda el resultado en la base de datos
    
    Body:
        {
            "amount": 25000,
            "ticket": "TKT123",
            "ticket_id": 456,
            "successful": true,
            "authorizationCode": "123456",
            "responseCode": 0,
            "operationNumber": "789",
            "cardType": "DB",
            "last4Digits": "1234",
            "cardBrand": "VISA",
            "sharesNumber": 0,
            "commerceCode": "597020000541",
            "terminalId": "ABC123",
            "accountingDate": "20260127",
            "realDate": "2026-01-27",
            "realTime": "15:30:45"
        }
    """
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not sucursal_id:
            return Response({
                'success': False,
                'error': 'No hay sucursal en sesión'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Guardar transacción usando el servicio
        resultado = persistence_service.guardar_transaccion(
            data=request.data,
            sucursal_id=sucursal_id,
            user=request.user
        )
        
        if resultado['success']:
            return Response(resultado)
        else:
            return Response(resultado, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error guardando venta: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==================== ENDPOINTS DEPRECADOS ====================
# Los siguientes endpoints ya NO se usan porque la comunicación
# con el POS se hace desde JavaScript con Web Serial API

@api_view(['GET'])
def listar_puertos(request):
    """DEPRECATED: Los puertos se listan desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Los puertos se obtienen con Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def conectar(request):
    """DEPRECATED: La conexión se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def conectar_con_reintentos(request):
    """DEPRECATED: La conexión se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def desconectar(request):
    """DEPRECATED: La desconexión se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def verificar(request):
    """DEPRECATED: La verificación (POLL) se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def obtener_info_puerto(request):
    """DEPRECATED: La info del puerto se obtiene desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def cargar_llaves(request):
    """DEPRECATED: Cargar llaves se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def venta_multicodigo(request):
    """DEPRECATED: Venta multicodigo se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def ultima_venta(request):
    """DEPRECATED: Última venta se consulta desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def anular(request):
    """DEPRECATED: Anulación se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def totales(request):
    """DEPRECATED: Totales se consultan desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def detalles(request):
    """DEPRECATED: Detalles se consultan desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def cerrar_dia(request):
    """DEPRECATED: Cierre de día se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)
