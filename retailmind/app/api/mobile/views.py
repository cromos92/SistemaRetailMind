"""
Views para API móvil (JWT).
"""

import pytz

from django.utils import timezone
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from app.models import CodigoAutorizacionDinamico, Producto_Talla, Sucursal, EmpresaUser
from app.views import registrar_movimiento_producto


class CodigoAutorizacionActualView(APIView):
    """
    GET /api/v1/mobile/codigo-autorizacion/actual/

    Obtiene el código de autorización dinámico actual usando JWT.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Verificar rol (igual que en la vista web)
        rol_usuario = getattr(request.user, "rol", None)
        if rol_usuario not in ["administrador", "jefe_local"]:
            return Response(
                {
                    "success": False,
                    "error": "No tiene permisos para acceder a los códigos de autorización",
                    "requiere_rol": "Administrador o Jefe Local",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        codigo_obj = CodigoAutorizacionDinamico.obtener_codigo_actual(request.user)
        if not codigo_obj:
            return Response(
                {"success": False, "error": "No se pudo generar el código de autorización"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Hora Chile para consistencia con el frontend web
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone("America/Santiago")
        ahora = ahora_utc.astimezone(chile_tz)

        tiempo_restante = codigo_obj.fecha_hora_fin - ahora
        minutos_restantes = int(tiempo_restante.total_seconds() / 60)

        return Response(
            {
                "success": True,
                "codigo": {
                    "codigo": codigo_obj.codigo,
                    "valido_desde": codigo_obj.fecha_hora_inicio.strftime("%H:%M"),
                    "valido_hasta": codigo_obj.fecha_hora_fin.strftime("%H:%M"),
                    "minutos_restantes": minutos_restantes,
                    "fecha_actual": ahora.strftime("%d/%m/%Y %H:%M:%S"),
                },
            },
            status=status.HTTP_200_OK,
        )


class AjusteStockRapidoView(APIView):
    """
    POST /api/v1/mobile/ajuste-stock-rapido/

    Ajuste rápido de stock por SKU y concepto usando JWT.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data or {}

        sku_raw = str(data.get("sku", "")).strip()
        concepto = str(data.get("concepto", "")).strip()
        observaciones = str(data.get("observaciones", "")).strip()

        try:
            cantidad = int(data.get("cantidad", 0))
        except (TypeError, ValueError):
            cantidad = 0

        conceptos_permitidos = [
            "INGRESO_INICIAL",
            "DEVOLUCION_CLIENTE",
            "DEVOLUCION_PROVEEDOR",
            "REGULARIZACION_TRASPASO",
            "AJUSTE_POSITIVO",
            "AJUSTE_NEGATIVO",
            "PERDIDA_ROBO",
            "PERDIDA_DETERIORO",
            "DONACION_RECIBIDA",
            "DONACION_ENTREGADA",
            "CAMBIO_PRODUCTO_ENTRADA",
            "CAMBIO_PRODUCTO_SALIDA",
        ]
        conceptos_egreso = {
            "AJUSTE_NEGATIVO",
            "PERDIDA_ROBO",
            "PERDIDA_DETERIORO",
            "DONACION_ENTREGADA",
            "DEVOLUCION_PROVEEDOR",
            "CAMBIO_PRODUCTO_SALIDA",
        }

        if not sku_raw:
            return Response({"success": False, "error": "Debe ingresar un SKU"}, status=status.HTTP_400_BAD_REQUEST)
        if not concepto or concepto not in conceptos_permitidos:
            return Response({"success": False, "error": "Seleccione un concepto válido"}, status=status.HTTP_400_BAD_REQUEST)
        if cantidad <= 0:
            return Response({"success": False, "error": "La cantidad debe ser mayor a 0"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            sku = int(sku_raw)
        except ValueError:
            return Response({"success": False, "error": "SKU inválido"}, status=status.HTTP_400_BAD_REQUEST)

        # Determinar sucursal activa para el usuario (similar a la sesión web)
        sucursal = None
        empresa_user = (
            EmpresaUser.objects.filter(user=request.user, active=True)
            .select_related("sucursal")
            .first()
        )
        if empresa_user and empresa_user.sucursal:
            sucursal = empresa_user.sucursal
        else:
            sucursal_id = data.get("sucursal_id")
            if sucursal_id:
                sucursal = get_object_or_404(Sucursal, id=sucursal_id)

        if not sucursal:
            return Response(
                {"success": False, "error": "No hay sucursal activa para el usuario"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        producto_talla = (
            Producto_Talla.objects.select_related("producto", "producto__sucursal")
            .filter(sku=sku, producto__sucursal_id=sucursal.id)
            .first()
        )
        if not producto_talla:
            return Response(
                {"success": False, "error": f"No se encontró SKU {sku} en la sucursal actual"},
                status=status.HTTP_404_NOT_FOUND,
            )

        es_egreso = concepto in conceptos_egreso
        cantidad_mov = -cantidad if es_egreso else cantidad

        if es_egreso:
            stock_disponible = producto_talla.stock_sucursal(sucursal.id)
            if stock_disponible < cantidad:
                return Response(
                    {"success": False, "error": f"Stock insuficiente. Disponible: {stock_disponible}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        responsable = request.user.get_full_name() or request.user.username
        movimiento = registrar_movimiento_producto(
            producto_talla=producto_talla,
            concepto=concepto,
            cantidad=cantidad_mov,
            responsable=responsable,
            sucursal_origen=sucursal,
            sucursal_destino=sucursal,
            observaciones=observaciones,
            referencia_externa="AJUSTE_STOCK_RAPIDO",
        )

        return Response(
            {
                "success": True,
                "message": "Ajuste registrado correctamente",
                "movimiento_id": movimiento.id,
                "sku": producto_talla.sku,
                "producto": producto_talla.producto.articulo,
                "nuevo_stock": producto_talla.stock_sucursal(sucursal.id),
            },
            status=status.HTTP_200_OK,
        )
