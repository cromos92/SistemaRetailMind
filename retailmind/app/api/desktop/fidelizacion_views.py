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
        resultado = giftcard_service.validar(codigo, monto, pin=pin)
        return Response({'success': True, **resultado})
