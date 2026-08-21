"""
API Desktop - Fidelización y Gift Cards
=======================================

Endpoints que consume el cliente Tauri (NEXO POS) para:
- Consultar el saldo de puntos de un cliente por RUT (al cobrar).
- Consultar el saldo/estado de una gift card por código.
- Validar una gift card antes de aceptarla como pago (sin descontar).

La acumulación/consumo reales ocurren cuando el desktop sincroniza el ticket
y corre el hook de `registrar_pagos_ticket`. Aquí solo se consulta/valida.

Toda la lógica delega en los servicios (sin duplicar reglas).
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from app.models import Sucursal
from app.services import fidelizacion_service, giftcard_service

from .permissions import IsAuthorizedDevice


class SaldoPuntosView(APIView):
    """
    GET /api/v1/desktop/fidelizacion/saldo/?rut=12345678-9

    Devuelve el saldo de puntos del cliente, su valor en pesos y los puntos
    próximos a vencer.
    """
    permission_classes = [IsAuthenticated, IsAuthorizedDevice]

    def get(self, request):
        rut = (request.query_params.get('rut') or '').strip()
        if not rut:
            return Response({'success': False, 'error': 'RUT requerido.'},
                            status=status.HTTP_400_BAD_REQUEST)
        info = fidelizacion_service.consultar_saldo(rut=rut)
        return Response({'success': True, **info})


class ValeCanjeConsultaView(APIView):
    """
    GET /api/v1/desktop/canje/<codigo>/

    Valida (sin debitar) un vale de canje con código para que el POS muestre su
    valor antes de aplicarlo. Devuelve estado, puntos, valor en pesos y a qué
    cliente pertenece.
    """
    permission_classes = [IsAuthenticated, IsAuthorizedDevice]

    def get(self, request, codigo):
        info = fidelizacion_service.validar_vale(codigo)
        if not info.get('existe'):
            return Response({'success': False, 'error': 'El código no existe.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({'success': True, **info})


class ValeCanjeAplicarView(APIView):
    """
    POST /api/v1/desktop/canje/aplicar/
    Body: { "codigo": "RM-XXXXXXXX", "sucursal_id": 3 }

    Canjea el vale: debita los puntos del cliente (FIFO) y devuelve el
    `valor_pesos` que el POS debe aplicar como descuento. Idempotente por código:
    repetirlo no vuelve a debitar. El usuario que canjea queda registrado.
    """
    permission_classes = [IsAuthenticated, IsAuthorizedDevice]

    def post(self, request):
        codigo = (request.data.get('codigo') or '').strip()
        sucursal = None
        sucursal_id = request.data.get('sucursal_id')
        if sucursal_id:
            sucursal = Sucursal.objects.filter(pk=sucursal_id).first()
        try:
            resultado = fidelizacion_service.canjear_vale(
                codigo, sucursal=sucursal, usuario=request.user,
            )
        except fidelizacion_service.FidelizacionError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, **resultado})


class GiftCardConsultaView(APIView):
    """
    GET /api/v1/desktop/giftcards/<codigo>/

    Devuelve estado y saldo de la gift card.
    """
    permission_classes = [IsAuthenticated, IsAuthorizedDevice]

    def get(self, request, codigo):
        try:
            info = giftcard_service.consultar_saldo(codigo)
            return Response({'success': True, **info})
        except giftcard_service.GiftCardError as e:
            return Response({'success': False, 'error': str(e)},
                            status=status.HTTP_404_NOT_FOUND)


class GiftCardValidarView(APIView):
    """
    POST /api/v1/desktop/giftcards/validar/
    Body: { "codigo": "...", "monto": 1234, "pin": "opcional" }

    Valida (sin descontar) que la gift card pueda cubrir `monto`.
    """
    permission_classes = [IsAuthenticated, IsAuthorizedDevice]

    def post(self, request):
        codigo = (request.data.get('codigo') or '').strip()
        monto = request.data.get('monto') or 0
        pin = request.data.get('pin')
        try:
            monto = int(monto)
        except (TypeError, ValueError):
            monto = 0
        # La sucursal es OBLIGATORIA para evaluar el ámbito por empresa de la
        # tarjeta: sin ella, `validar` es fail-closed y rechazaría toda gift
        # card acotada. El cliente Tauri ya manda `x-sucursal-id` (header CORS
        # permitido); se acepta también en el body como respaldo.
        sucursal_id = (
            request.headers.get('x-sucursal-id')
            or request.data.get('sucursal_id')
        )
        sucursal = None
        if sucursal_id and str(sucursal_id).isdigit():
            sucursal = Sucursal.objects.filter(id=int(sucursal_id)).first()
        resultado = giftcard_service.validar(codigo, monto, pin=pin, sucursal=sucursal)
        return Response({'success': True, **resultado})
