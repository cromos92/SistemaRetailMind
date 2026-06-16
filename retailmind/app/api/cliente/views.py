"""
API REST para CLIENTES finales (app móvil de fidelización).

Endpoints bajo /api/v1/cliente/. Estilo APIView de DRF (igual que api/desktop/).
Toda la lógica delega en `cliente_app_service` y `fidelizacion_service` — las
vistas solo validan input, fijan auth/throttle y serializan.

Auth: las vistas protegidas fijan `authentication_classes = [ClienteJWTAuthentication]`
para sobrescribir el default global (esto quita SessionAuthentication → sin CSRF
en los POST móviles) y `permission_classes = [IsClienteApp]`.
"""
import logging

from django.utils import timezone

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.pagination import PageNumberPagination

from app.models import MovimientoPuntos, GiftCard
from app.services import cliente_app_service, fidelizacion_service

from .authentication import ClienteJWTAuthentication
from .permissions import IsClienteApp
from .serializers import (
    SolicitarOTPSerializer,
    VerificarOTPSerializer,
    RefreshSerializer,
    LogoutSerializer,
    PerfilClienteSerializer,
    CuentaPuntosSerializer,
    MovimientoPuntosSerializer,
    GiftCardClienteSerializer,
)

logger = logging.getLogger('app')


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ========== AUTH ==========

class SolicitarOTPView(APIView):
    """
    POST /api/v1/cliente/auth/solicitar-otp/
    Body: { "rut": "12.345.678-9", "canal": "EMAIL" }

    Envía un OTP si el cliente existe y tiene canal. Respuesta SIEMPRE genérica
    (anti-enumeración). También sirve como "vincular": crea la CuentaClienteApp
    del cliente existente si aún no la tiene. NO crea clientes nuevos.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'otp_solicitar'

    def post(self, request):
        serializer = SolicitarOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        resultado = cliente_app_service.solicitar_otp(
            data['rut'], canal=data.get('canal', 'EMAIL'), ip=_client_ip(request),
        )
        return Response({'success': True, **resultado})


# Alias semántico: "vincular" es solicitar el primer OTP para reclamar la cuenta.
class VincularCuentaView(SolicitarOTPView):
    """
    POST /api/v1/cliente/auth/vincular/

    Igual que solicitar-otp pero pensado para el primer login (reclamar la cuenta
    de un Cliente que ya existe en el CRM). Misma respuesta genérica.
    """
    throttle_scope = 'vincular_cliente'


class VerificarOTPView(APIView):
    """
    POST /api/v1/cliente/auth/verificar-otp/
    Body: { "rut": "12.345.678-9", "codigo": "123456" }

    Verifica el OTP y devuelve { access, refresh, expires_at, cliente }.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'otp_verificar'

    def post(self, request):
        serializer = VerificarOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            tokens = cliente_app_service.verificar_otp(
                data['rut'], data['codigo'],
                ip=_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
            )
        except cliente_app_service.ClienteAppError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, **tokens})


class RefreshView(APIView):
    """
    POST /api/v1/cliente/auth/refresh/
    Body: { "refresh": "<token>" }

    Rota el refresh y devuelve un access nuevo.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            tokens = cliente_app_service.refrescar_tokens(
                serializer.validated_data['refresh'], ip=_client_ip(request),
            )
        except cliente_app_service.ClienteAppError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_401_UNAUTHORIZED)
        return Response({'success': True, **tokens})


class LogoutView(APIView):
    """
    POST /api/v1/cliente/auth/logout/
    Body: { "refresh": "<token>" }  (opcional)

    Revoca la familia del refresh token (cierra sesión).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cliente_app_service.logout(serializer.validated_data.get('refresh', ''))
        return Response({'success': True})


# ========== DATOS DEL CLIENTE ==========

class SaldoPuntosView(APIView):
    """
    GET /api/v1/cliente/puntos/saldo/

    Saldo, valor en pesos y puntos por vencer del cliente autenticado.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        info = fidelizacion_service.consultar_saldo(cliente=request.user.cliente)
        return Response({'success': True, **info})


class MovimientosPuntosView(APIView):
    """
    GET /api/v1/cliente/puntos/movimientos/?page=1&page_size=20

    Historial de movimientos de puntos del cliente (paginado).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        cuenta = getattr(request.user.cliente, 'cuenta_puntos', None)
        if not cuenta:
            return Response({'success': True, 'count': 0, 'results': []})

        qs = (
            MovimientoPuntos.objects
            .filter(cuenta=cuenta)
            .order_by('-fecha')
        )
        paginator = PageNumberPagination()
        paginator.page_size = 20
        paginator.page_size_query_param = 'page_size'
        paginator.max_page_size = 100
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = MovimientoPuntosSerializer(page, many=True)
        resp = paginator.get_paginated_response(serializer.data)
        resp.data = {'success': True, **resp.data}
        return resp


class GiftCardsView(APIView):
    """
    GET /api/v1/cliente/giftcards/

    Gift cards del cliente autenticado.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        qs = (
            GiftCard.objects
            .filter(cliente=request.user.cliente)
            .order_by('-fecha_emision')
        )
        serializer = GiftCardClienteSerializer(qs, many=True)
        return Response({'success': True, 'results': serializer.data})


class PerfilView(APIView):
    """
    GET   /api/v1/cliente/perfil/   → perfil del cliente
    PATCH /api/v1/cliente/perfil/   → edita email/celular

    Al cambiar email/celular se resetea la verificación del canal.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        serializer = PerfilClienteSerializer(request.user.cliente)
        return Response({'success': True, 'perfil': serializer.data})

    def patch(self, request):
        cliente = request.user.cliente
        cuenta = request.user
        serializer = PerfilClienteSerializer(cliente, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        email_nuevo = serializer.validated_data.get('email')
        celular_nuevo = serializer.validated_data.get('celular')
        reset_fields = []
        if email_nuevo is not None and email_nuevo != cliente.email:
            cuenta.email_verificado = False
            reset_fields.append('email_verificado')
        if celular_nuevo is not None and celular_nuevo != cliente.celular:
            cuenta.celular_verificado = False
            reset_fields.append('celular_verificado')

        serializer.save()
        if reset_fields:
            reset_fields.append('updated_at')
            cuenta.save(update_fields=reset_fields)

        return Response({'success': True, 'perfil': PerfilClienteSerializer(cliente).data})


class CarnetView(APIView):
    """
    GET /api/v1/cliente/carnet/

    Devuelve el RUT del cliente como payload para que la app dibuje el QR/código
    de barras con el que el cajero lo identifica (el POS resuelve por RUT).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        cliente = request.user.cliente
        rut = cliente.rut or ''
        return Response({
            'success': True,
            'rut': fidelizacion_service.normalizar_rut(rut),
            'rut_formateado': _formatear_rut(rut),
            'nombre_completo': cliente.nombre_completo,
            'qr_payload': fidelizacion_service.normalizar_rut(rut),
        })


def _formatear_rut(rut):
    """Formatea un RUT a 'XX.XXX.XXX-D'. Devuelve el original si no se puede."""
    norm = fidelizacion_service.normalizar_rut(rut)
    if len(norm) < 2:
        return rut
    cuerpo, dv = norm[:-1], norm[-1]
    if not cuerpo.isdigit():
        return rut
    cuerpo_fmt = f"{int(cuerpo):,}".replace(',', '.')
    return f"{cuerpo_fmt}-{dv}"
