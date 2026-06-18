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

from app.models import MovimientoPuntos, GiftCard, ReservaPuntos, CanjeVale
from app.services import (
    cliente_app_service,
    fidelizacion_service,
    catalogo_cliente_service,
    ecommerce_cupon_service,
    ecommerce_checkout_service,
)

from .authentication import ClienteJWTAuthentication
from .permissions import IsClienteApp
from .serializers import (
    SolicitarOTPSerializer,
    VerificarOTPSerializer,
    RefreshSerializer,
    LogoutSerializer,
    CotizarCanjeSerializer,
    ReservarPuntosSerializer,
    IniciarCheckoutSerializer,
    GenerarValeCanjeSerializer,
    PerfilClienteSerializer,
    CuentaPuntosSerializer,
    MovimientoPuntosSerializer,
    GiftCardClienteSerializer,
    CanjeValeSerializer,
)

logger = logging.getLogger('app')


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ========== BASES CON THROTTLE (protección de carga de la app) ==========

class _ClienteView(APIView):
    """Base autenticada de la app (sin throttle)."""
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]


class _ClienteCatalogoView(_ClienteView):
    """Navegación de catálogo: throttle app_catalogo (protege al ecommerce vía proxy)."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'app_catalogo'


class _ClienteConsultaView(_ClienteView):
    """Lecturas livianas: throttle app_consulta."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'app_consulta'


class _ClienteCheckoutView(_ClienteView):
    """Escrituras de checkout (reservar/iniciar): throttle app_checkout."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'app_checkout'


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
            data['identificador'], canal=data.get('canal', 'EMAIL'),
            ip=_client_ip(request),
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
                data['identificador'], data['codigo'],
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


class CotizarCanjeView(_ClienteConsultaView):
    """
    POST /api/v1/cliente/puntos/cotizar/
    Body: { "puntos": 200 }

    Devuelve el valor en pesos de esos puntos y el saldo disponible para reservar.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def post(self, request):
        serializer = CotizarCanjeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            info = fidelizacion_service.cotizar_canje(
                request.user.cliente, serializer.validated_data['puntos'],
            )
        except fidelizacion_service.FidelizacionError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, **info})


class ReservarPuntosView(_ClienteCheckoutView):
    """
    POST /api/v1/cliente/puntos/reservar/
    Body: { "puntos": 200, "tienda": "paola", "idempotency_key": "<uuid>" }

    Reserva (bloqueo lógico) los puntos y crea el cupón equivalente en el
    ecommerce destino. NO debita el saldo (eso ocurre al confirmar el pago).
    Devuelve { reserva_id, codigo_cupon, valor_pesos, expira_en }.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def post(self, request):
        serializer = ReservarPuntosSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        tienda = data['tienda'].strip().lower()
        idem = ((data.get('idempotency_key') or '').strip()
                or request.headers.get('Idempotency-Key') or None)

        cred = ecommerce_cupon_service.resolver_credencial(tienda)
        if cred is None:
            return Response({'success': False, 'error': f'Tienda no disponible: {tienda}.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            reserva = fidelizacion_service.reservar_puntos(
                request.user.cliente, data['puntos'],
                tienda=tienda, empresa=cred.empresa, idempotency_key=idem,
            )
        except fidelizacion_service.FidelizacionError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)

        # Materializar la reserva como cupón en el ecommerce (solo si sigue viva).
        if reserva.estado == 'RESERVADA':
            resultado = ecommerce_cupon_service.crear_cupon_puntos(
                tienda, reserva.codigo_cupon, reserva.valor_pesos, reserva.expira_en,
            )
            if not resultado.get('ok'):
                fidelizacion_service.liberar_reserva(reserva, motivo='cupon_fallido')
                return Response(
                    {'success': False,
                     'error': resultado.get('error',
                                            'No se pudieron aplicar los puntos en la tienda.')},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        return Response({
            'success': True,
            'reserva_id': reserva.id,
            'codigo_cupon': reserva.codigo_cupon,
            'valor_pesos': reserva.valor_pesos,
            'puntos': reserva.puntos_reservados,
            'estado': reserva.estado,
            'expira_en': reserva.expira_en.isoformat(),
        })


class IniciarCheckoutView(_ClienteCheckoutView):
    """
    POST /api/v1/cliente/checkout/iniciar/
    Body: { "reserva_id": 1, "items": [{"sku","quantity"}], "first_name", "last_name", "phone" }

    Valida la reserva (viva y del cliente) y pide al ecommerce una URL de checkout
    con el carrito + el cupón de puntos + el RUT precargados. Devuelve { checkout_url }.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def post(self, request):
        serializer = IniciarCheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        cliente = request.user.cliente

        reserva = None
        tienda = (data.get('tienda') or '').strip().lower()
        coupon_code = ''
        if data.get('reserva_id'):
            reserva = ReservaPuntos.objects.filter(
                pk=data['reserva_id'], cliente=cliente).first()
            if reserva is None:
                return Response({'success': False, 'error': 'Reserva no encontrada.'},
                                status=status.HTTP_404_NOT_FOUND)
            if reserva.estado != 'RESERVADA' or reserva.expira_en <= timezone.now():
                return Response(
                    {'success': False, 'error': 'La reserva expiró. Vuelve a intentar.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            tienda = reserva.tienda
            coupon_code = reserva.codigo_cupon

        if not tienda:
            return Response({'success': False, 'error': 'Falta indicar la tienda.'},
                            status=status.HTTP_400_BAD_REQUEST)

        items = [{'sku': it['sku'], 'quantity': it['quantity']} for it in data['items']]
        resultado = ecommerce_checkout_service.iniciar_checkout(
            tienda=tienda,
            items=items,
            coupon_code=coupon_code,
            email=cliente.email or '',
            first_name=data['first_name'],
            last_name=data['last_name'],
            phone=data['phone'],
            rut=cliente.rut or '',
        )
        if not resultado.get('ok'):
            return Response(
                {'success': False,
                 'error': resultado.get('error', 'No se pudo iniciar el pago en la tienda.')},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({
            'success': True,
            'reserva_id': reserva.id if reserva else None,
            'checkout_url': resultado['checkout_url'],
        })


class CheckoutEstadoView(_ClienteConsultaView):
    """
    GET /api/v1/cliente/checkout/estado/<reserva_id>/

    Estado de la reserva y, si ya llegó el pedido pagado, su número y los puntos
    consumidos. La app consulta esto tras el WebView (fuente de verdad: el backend).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request, reserva_id):
        reserva = ReservaPuntos.objects.filter(
            pk=reserva_id, cliente=request.user.cliente).first()
        if reserva is None:
            return Response({'success': False, 'error': 'Reserva no encontrada.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({
            'success': True,
            'estado_reserva': reserva.estado,
            'order_number': reserva.order_number or None,
            'puntos_consumidos': reserva.puntos_consumidos,
            'pagado': reserva.estado == 'CONFIRMADA',
        })


class CancelarCheckoutView(_ClienteCheckoutView):
    """
    POST /api/v1/cliente/checkout/cancelar/<reserva_id>/

    Libera la reserva de puntos y desactiva su cupón cuando el cliente abandona
    el WebView o el pago falla. No toca una reserva ya pagada (CONFIRMADA).
    Idempotente.
    """
    def post(self, request, reserva_id):
        reserva = ReservaPuntos.objects.filter(
            pk=reserva_id, cliente=request.user.cliente).first()
        if reserva is None:
            return Response({'success': False, 'error': 'Reserva no encontrada.'},
                            status=status.HTTP_404_NOT_FOUND)
        reserva = fidelizacion_service.cancelar_reserva(reserva)
        return Response({'success': True, 'estado': reserva.estado})


# ========== CANJE CON CÓDIGO (vale para tienda física) ==========

class GenerarValeCanjeView(_ClienteCheckoutView):
    """
    POST /api/v1/cliente/canje/generar/
    Body: { "puntos": 500, "idempotency_key": "<uuid>" }

    Convierte puntos en un vale con código de un solo uso para canjear en tienda
    física. NO debita el saldo: lo compromete hasta canjear/expirar. Devuelve el
    vale (código + valor en pesos + vencimiento). Throttle app_checkout.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def post(self, request):
        serializer = GenerarValeCanjeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idem = ((data.get('idempotency_key') or '').strip()
                or request.headers.get('Idempotency-Key') or None)
        try:
            vale = fidelizacion_service.generar_vale_canje(
                request.user.cliente, data['puntos'], idempotency_key=idem,
            )
        except fidelizacion_service.FidelizacionError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, 'vale': CanjeValeSerializer(vale).data})


class MisCanjesView(_ClienteConsultaView):
    """
    GET /api/v1/cliente/canje/mis-canjes/?estado=PENDIENTE

    Vales de canje del cliente. Sin filtro devuelve todos (recientes primero).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        cuenta = getattr(request.user.cliente, 'cuenta_puntos', None)
        # Autolimpieza: refleja como EXPIRADO los vencidos antes de listar.
        fidelizacion_service._expirar_vales_de_cuenta(cuenta)
        qs = CanjeVale.objects.filter(cliente=request.user.cliente)
        estado = (request.query_params.get('estado') or '').strip().upper()
        if estado:
            qs = qs.filter(estado=estado)
        serializer = CanjeValeSerializer(qs.order_by('-created_at'), many=True)
        return Response({'success': True, 'results': serializer.data})


class CanjeDetalleView(_ClienteConsultaView):
    """
    GET /api/v1/cliente/canje/<codigo>/

    Estado actual de un vale (la app refresca tras presentarlo en caja). Solo
    vales del cliente autenticado.
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request, codigo):
        vale = CanjeVale.objects.filter(
            codigo=(codigo or '').strip().upper(),
            cliente=request.user.cliente).first()
        if vale is None:
            return Response({'success': False, 'error': 'Vale no encontrado.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({'success': True, 'vale': CanjeValeSerializer(vale).data})


class AnularCanjeView(_ClienteCheckoutView):
    """
    POST /api/v1/cliente/canje/<codigo>/anular/

    El cliente anula un vale PENDIENTE y recupera los puntos comprometidos. No
    toca un vale ya canjeado. Idempotente.
    """
    def post(self, request, codigo):
        vale = CanjeVale.objects.filter(
            codigo=(codigo or '').strip().upper(),
            cliente=request.user.cliente).first()
        if vale is None:
            return Response({'success': False, 'error': 'Vale no encontrado.'},
                            status=status.HTTP_404_NOT_FOUND)
        vale = fidelizacion_service.anular_vale(vale, motivo='anulado por cliente')
        return Response({'success': True, 'estado': vale.estado})


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


# ========== CATÁLOGO / TIENDA ==========

class CatalogoListView(_ClienteCatalogoView):
    """
    GET /api/v1/cliente/catalogo/?tienda=paola&page=1&page_size=20

    Lista de productos de la tienda indicada. RetailMind hace de proxy del
    catálogo del ecommerce (la app nunca lleva la API key del ecommerce).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        qp = request.query_params
        try:
            data = catalogo_cliente_service.listar_productos(
                qp.get('tienda', ''),
                page=qp.get('page', 1),
                page_size=qp.get('page_size', 20),
                categoria=qp.get('categoria'),
                q=qp.get('q'),
                destacados=qp.get('destacados') in ('1', 'true', 'True'),
            )
        except catalogo_cliente_service.CatalogoError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, **data})


class CategoriasView(_ClienteCatalogoView):
    """
    GET /api/v1/cliente/catalogo/categorias/?tienda=paola

    Categorías de la tienda (proxy del ecommerce).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request):
        try:
            data = catalogo_cliente_service.listar_categorias(
                request.query_params.get('tienda', ''))
        except catalogo_cliente_service.CatalogoError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, **data})


class CatalogoDetalleView(_ClienteCatalogoView):
    """
    GET /api/v1/cliente/catalogo/<sku>/?tienda=paola

    Detalle de un producto por SKU (con variantes).
    """
    authentication_classes = [ClienteJWTAuthentication]
    permission_classes = [IsClienteApp]

    def get(self, request, sku):
        try:
            producto = catalogo_cliente_service.detalle_producto(
                request.query_params.get('tienda', ''), sku,
            )
        except catalogo_cliente_service.CatalogoError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({'success': True, 'producto': producto})


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
