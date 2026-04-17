"""
Views para sincronización bidireccional App Desktop
===================================================
"""

from django.utils import timezone
from django.db.models import Prefetch

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from app.models import Producto, Categoria, Vendedor, Producto_Talla
from app.models_sync import SyncLog

from app.api.desktop.permissions import (
    IsAuthorizedDevice,
    IsMinimumAppVersion,
    IsSameSucursal,
    CanSyncTickets,
)

from .serializers import (
    ProductoSyncSerializer,
    CategoriaSyncSerializer,
    VendedorSyncSerializer,
    ConfiguracionSyncSerializer,
    TicketsBatchUploadSerializer,
    TicketsSyncResponseSerializer,
    ProductosSyncResponseSerializer,
    CuadraturasBatchUploadSerializer,
)
from .services import TicketSyncService, ProductoSyncService, CuadraturaSyncService


class ProductosSyncView(APIView):
    """
    GET /api/v1/sync/productos/
    
    Sincroniza productos desde servidor hacia desktop.
    
    Query params:
        - since: ISO datetime para obtener solo actualizados
        - sucursal_id: ID de sucursal (opcional)
        - page: número de página (default 1)
        - page_size: tamaño de página (default 100, max 500)
    """
    
    permission_classes = [IsAuthenticated]  # Solo requiere JWT válido
    
    def get(self, request):
        from app.models import EmpresaUser
        
        # Obtener sucursal del usuario o del query param
        sucursal_id = request.query_params.get('sucursal_id')
        sucursal = None
        dispositivo = getattr(request, 'dispositivo', None)
        
        if sucursal_id:
            # Si se envía sucursal_id en query params
            from app.models import Sucursal
            sucursal = Sucursal.objects.filter(id=sucursal_id).first()
        elif dispositivo:
            # Si hay dispositivo autorizado
            sucursal = dispositivo.sucursal
        else:
            # Obtener del EmpresaUser
            empresa_user = EmpresaUser.objects.filter(
                user=request.user,
                status=True
            ).select_related('sucursal').first()
            if empresa_user and empresa_user.sucursal:
                sucursal = empresa_user.sucursal
        
        if not sucursal:
            return Response({
                'error': 'no_sucursal',
                'detail': 'No se pudo determinar la sucursal. Envía sucursal_id como parámetro.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Parsear parámetros
        since_str = request.query_params.get('since')
        since = None
        if since_str:
            from django.utils.dateparse import parse_datetime
            since = parse_datetime(since_str)
        
        page = int(request.query_params.get('page', 1))
        page_size = min(int(request.query_params.get('page_size', 100)), 500)
        
        # Iniciar log
        sync_log = SyncLog.iniciar(
            tipo='productos_down',
            dispositivo=dispositivo,
            sucursal=sucursal,
            usuario=request.user,
            version_app=request.headers.get('X-App-Version'),
            ip=self._get_client_ip(request)
        )
        
        try:
            # Obtener productos
            service = ProductoSyncService(sucursal)
            resultado = service.obtener_productos_actualizados(
                since=since,
                page=page,
                page_size=page_size
            )
            
            # Serializar
            productos_data = ProductoSyncSerializer(
                resultado['productos'], many=True
            ).data
            
            response_data = {
                'productos': productos_data,
                'deleted_ids': resultado['deleted_ids'],
                'sync_timestamp': resultado['sync_timestamp'].isoformat(),
                'total': resultado['total'],
                'page': resultado['page'],
                'page_size': resultado['page_size'],
                'has_more': resultado['has_more'],
            }
            
            # Finalizar log
            sync_log.finalizar(
                exitoso=True,
                registros_procesados=len(productos_data)
            )
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            sync_log.finalizar(exitoso=False, error=str(e))
            return Response(
                {'error': 'sync_error', 'detail': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class CategoriasSyncView(APIView):
    """
    GET /api/v1/sync/categorias/
    
    Sincroniza categorías desde servidor hacia desktop.
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        since_str = request.query_params.get('since')
        
        # Obtener todas las categorías
        categorias = Categoria.objects.all().select_related('padre')
        
        # TODO: Filtrar por fecha si se implementa updated_at
        
        categorias_data = CategoriaSyncSerializer(categorias, many=True).data
        
        # IDs eliminados (si implementas soft delete)
        deleted_ids = []
        
        return Response({
            'categorias': categorias_data,
            'deleted_ids': deleted_ids,
            'sync_timestamp': timezone.now().isoformat(),
        }, status=status.HTTP_200_OK)


class VendedoresSyncView(APIView):
    """
    GET /api/v1/sync/vendedores/
    
    Sincroniza vendedores autorizados en la sucursal.
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from app.models import EmpresaUser
        
        # Obtener sucursal
        sucursal_id = request.query_params.get('sucursal_id')
        sucursal = None
        dispositivo = getattr(request, 'dispositivo', None)
        
        if sucursal_id:
            from app.models import Sucursal
            sucursal = Sucursal.objects.filter(id=sucursal_id).first()
        elif dispositivo:
            sucursal = dispositivo.sucursal
        else:
            empresa_user = EmpresaUser.objects.filter(
                user=request.user, status=True
            ).select_related('sucursal').first()
            if empresa_user:
                sucursal = empresa_user.sucursal
        
        # Obtener vendedores de la sucursal
        vendedores = Vendedor.objects.filter(
            sucursales=sucursal,
            activo=True
        ).distinct()
        
        vendedores_data = VendedorSyncSerializer(vendedores, many=True).data
        
        return Response({
            'vendedores': vendedores_data,
            'sync_timestamp': timezone.now().isoformat(),
        }, status=status.HTTP_200_OK)


class ConfiguracionSyncView(APIView):
    """
    GET /api/v1/sync/configuracion/
    
    Obtiene configuración completa para la sucursal.
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from app.models import EmpresaUser
        
        # Obtener sucursal
        sucursal_id = request.query_params.get('sucursal_id')
        sucursal = None
        dispositivo = getattr(request, 'dispositivo', None)
        
        if sucursal_id:
            from app.models import Sucursal
            sucursal = Sucursal.objects.filter(id=sucursal_id).select_related('empresa').first()
        elif dispositivo:
            sucursal = dispositivo.sucursal
        else:
            empresa_user = EmpresaUser.objects.filter(
                user=request.user, status=True
            ).select_related('sucursal', 'sucursal__empresa').first()
            if empresa_user:
                sucursal = empresa_user.sucursal
        
        if not sucursal:
            return Response({
                'error': 'no_sucursal',
                'detail': 'No se pudo determinar la sucursal'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        empresa = sucursal.empresa
        
        # Obtener correlativo actual de tickets
        from app.models import Correlativo, Ticket
        
        correlativo_ticket = 1
        correlativo_boleta = None
        
        try:
            corr_obj = Correlativo.objects.get(sucursal=sucursal, tipo_dte='TICKET')
            correlativo_ticket = corr_obj.inicio
        except Correlativo.DoesNotExist:
            # Obtener del último ticket
            ultimo = Ticket.objects.filter(sucursal=sucursal).order_by('-correlativo').first()
            if ultimo:
                correlativo_ticket = ultimo.correlativo + 1
        
        try:
            corr_boleta = Correlativo.objects.get(sucursal=sucursal, tipo_dte='BOLETA ELECTRONICA')
            correlativo_boleta = corr_boleta.inicio
        except Correlativo.DoesNotExist:
            pass
        
        config_data = {
            'sucursal_id': sucursal.id,
            'sucursal_nombre': sucursal.alias,
            'sucursal_direccion': sucursal.direccion,
            
            'empresa_nombre': empresa.nombre,
            'empresa_rut': empresa.rut,
            'empresa_razon_social': empresa.razon_social,
            'empresa_giro': empresa.giro,
            'empresa_direccion': empresa.direccion,
            'empresa_comuna': empresa.comuna,
            'empresa_ciudad': empresa.ciudad,
            
            # Configuración de impresora/POS (customizable)
            'impresora_tipo': None,  # TODO: agregar campo a modelo
            'impresora_puerto': None,
            'pos_tipo': None,
            'pos_puerto': None,
            
            # Correlativos
            'correlativo_ticket_actual': correlativo_ticket,
            'correlativo_boleta_actual': correlativo_boleta,
            
            # Políticas
            'max_descuento_porcentaje': 50,  # TODO: hacer configurable
            'permite_venta_sin_stock': False,
            'requiere_cliente_factura': True,
        }
        
        return Response(config_data, status=status.HTTP_200_OK)


class TicketsSyncView(APIView):
    """
    POST /api/v1/sync/tickets/
    
    Sincroniza tickets desde desktop hacia servidor.
    
    Maneja:
    - Idempotencia (si local_id ya existe, retorna existente)
    - Validación de stock (marca requiere_revision si hay problemas)
    - Generación de correlativos
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        from app.models import EmpresaUser
        
        # Obtener sucursal
        sucursal_id = request.data.get('sucursal_id')
        sucursal = None
        dispositivo = getattr(request, 'dispositivo', None)
        
        if sucursal_id:
            from app.models import Sucursal
            sucursal = Sucursal.objects.filter(id=sucursal_id).first()
        elif dispositivo:
            sucursal = dispositivo.sucursal
        else:
            empresa_user = EmpresaUser.objects.filter(
                user=request.user, status=True
            ).select_related('sucursal').first()
            if empresa_user:
                sucursal = empresa_user.sucursal
        
        if not sucursal:
            return Response({
                'error': 'no_sucursal',
                'detail': 'No se pudo determinar la sucursal. Envía sucursal_id.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validar datos de entrada
        serializer = TicketsBatchUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': 'validation_error', 'details': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Procesar tickets
        service = TicketSyncService(
            sucursal=sucursal,
            dispositivo=dispositivo,
            usuario=request.user
        )
        
        resultado = service.procesar_tickets(serializer.validated_data['tickets'])
        
        return Response(resultado, status=status.HTTP_200_OK)


class CuadraturasSyncView(APIView):
    """
    POST /api/v1/sync/cuadraturas/
    
    Sincroniza cuadraturas/cierres de caja desde desktop.
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        from app.models import EmpresaUser
        
        # Obtener sucursal
        sucursal_id = request.data.get('sucursal_id')
        sucursal = None
        dispositivo = getattr(request, 'dispositivo', None)
        
        if sucursal_id:
            from app.models import Sucursal
            sucursal = Sucursal.objects.filter(id=sucursal_id).first()
        elif dispositivo:
            sucursal = dispositivo.sucursal
        else:
            empresa_user = EmpresaUser.objects.filter(
                user=request.user, status=True
            ).select_related('sucursal').first()
            if empresa_user:
                sucursal = empresa_user.sucursal
        
        if not sucursal:
            return Response({
                'error': 'no_sucursal',
                'detail': 'No se pudo determinar la sucursal'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validar datos
        serializer = CuadraturasBatchUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': 'validation_error', 'details': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Procesar
        service = CuadraturaSyncService(
            sucursal=sucursal,
            dispositivo=dispositivo,
            usuario=request.user
        )
        
        resultado = service.procesar_cuadraturas(
            serializer.validated_data['cuadraturas']
        )
        
        return Response(resultado, status=status.HTTP_200_OK)


class MovimientosCajaSyncView(APIView):
    """
    POST /api/v1/sync/movimientos-caja/
    
    Sincroniza movimientos de caja individuales.
    """
    
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        # Los movimientos se procesan junto con las cuadraturas
        # Este endpoint es para movimientos sueltos si se necesitan
        
        return Response({
            'message': 'Usar /api/v1/sync/cuadraturas/ para sincronizar movimientos junto con cuadraturas'
        }, status=status.HTTP_200_OK)


class SyncStatusDetailView(APIView):
    """
    GET /api/v1/sync/status/detail/
    
    Estado detallado de sincronización (requiere auth).
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from app.models import EmpresaUser
        
        dispositivo = getattr(request, 'dispositivo', None)
        
        # Obtener sucursal
        if dispositivo:
            sucursal = dispositivo.sucursal
        else:
            empresa_user = EmpresaUser.objects.filter(
                user=request.user, status=True
            ).select_related('sucursal').first()
            sucursal = empresa_user.sucursal if empresa_user else None
        
        if not sucursal:
            return Response({
                'error': 'no_sucursal',
                'detail': 'No se pudo determinar la sucursal'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Obtener últimos logs de sync
        ultimos_logs = SyncLog.objects.filter(
            dispositivo=dispositivo
        ).order_by('-timestamp_inicio')[:10]
        
        logs_data = [{
            'tipo': log.get_tipo_display(),
            'estado': log.estado,
            'registros': log.registros_procesados,
            'timestamp': log.timestamp_inicio.isoformat(),
            'exitoso': log.exitoso,
        } for log in ultimos_logs]
        
        # Estadísticas
        from app.models import Ticket
        from django.db.models import Count
        from datetime import timedelta
        
        hoy = timezone.localdate()
        
        tickets_hoy = Ticket.objects.filter(
            sucursal=sucursal,
            fecha=hoy,
            created_offline=True
        ).count()
        
        tickets_pendientes_revision = Ticket.objects.filter(
            sucursal=sucursal,
            requiere_revision=True
        ).count()
        
        return Response({
            'dispositivo': {
                'nombre': dispositivo.nombre,
                'ultima_sync': dispositivo.ultima_sincronizacion.isoformat() if dispositivo.ultima_sincronizacion else None,
                'ultimo_acceso': dispositivo.ultimo_acceso.isoformat() if dispositivo.ultimo_acceso else None,
            },
            'estadisticas': {
                'tickets_sync_hoy': tickets_hoy,
                'tickets_pendientes_revision': tickets_pendientes_revision,
            },
            'ultimos_logs': logs_data,
            'server_time': timezone.now().isoformat(),
        }, status=status.HTTP_200_OK)
