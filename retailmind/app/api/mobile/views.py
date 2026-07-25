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

from app.models import (
    CodigoAutorizacionDinamico,
    Movimientos_Producto,
    OpcionMenu,
    PermisoRol,
    Producto_Talla,
    Sucursal,
    EmpresaUser,
)
from app.views import registrar_movimiento_producto


def _puede_ajustar_stock(user, sucursal_id):
    """
    Permiso para el ajuste rápido de stock desde móvil.

    Si la opción de menú 'ajuste_stock_rapido' está sembrada, se respeta el
    permiso fino (PermisoRol/PermisoUsuario/PermisoSucursal — el mismo de la
    página web). Si NO está sembrada, fallback por rol: sin esto, un deploy
    sin la opción en BD dejaría el endpoint en 403 para todos (mismo patrón
    del incidente de permisos de Liquidación de jul-2026).
    """
    if OpcionMenu.objects.filter(codigo="ajuste_stock_rapido", activo=True).exists():
        return PermisoRol.tiene_permiso(
            user, "ajuste_stock_rapido", "puede_ver", sucursal_id=sucursal_id
        )
    return getattr(user, "rol", None) in ("administrador", "jefe_local")


def _resolver_sucursal_movil(request, data):
    """
    Resuelve la sucursal para un request móvil JWT.

    Prioridad:
    1. `sucursal_id` del body — la sucursal elegida en el login de la app —
       validando que el usuario tenga acceso (EmpresaUser con status=True,
       o rol administrador).
    2. Fallback: la EmpresaUser activa (flag de la sesión web), como antes.

    Devuelve (sucursal, error_response). Solo uno de los dos es no-None.
    """
    sucursal_id = data.get("sucursal_id")
    if sucursal_id:
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        es_admin = getattr(request.user, "rol", None) == "administrador"
        tiene_acceso = es_admin or EmpresaUser.objects.filter(
            user=request.user, sucursal_id=sucursal.id, status=True
        ).exists()
        if not tiene_acceso:
            return None, Response(
                {"success": False, "error": "No tienes acceso a esta sucursal"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return sucursal, None

    empresa_user = (
        EmpresaUser.objects.filter(user=request.user, active=True, status=True)
        .select_related("sucursal")
        .first()
    )
    if empresa_user and empresa_user.sucursal:
        return empresa_user.sucursal, None

    return None, Response(
        {"success": False, "error": "No hay sucursal activa para el usuario"},
        status=status.HTTP_400_BAD_REQUEST,
    )


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

        # Los datetimes del código vienen aware (UTC con USE_TZ) cuando el
        # código ya existía en BD: convertir a hora Chile ANTES de formatear,
        # o valido_desde/valido_hasta saldrían corridos en 3-4 horas.
        inicio = codigo_obj.fecha_hora_inicio
        fin = codigo_obj.fecha_hora_fin
        if timezone.is_aware(inicio):
            inicio = inicio.astimezone(chile_tz)
        if timezone.is_aware(fin):
            fin = fin.astimezone(chile_tz)

        tiempo_restante = codigo_obj.fecha_hora_fin - (
            ahora if timezone.is_aware(codigo_obj.fecha_hora_fin) else ahora.replace(tzinfo=None)
        )
        minutos_restantes = int(tiempo_restante.total_seconds() / 60)

        return Response(
            {
                "success": True,
                "codigo": {
                    "codigo": codigo_obj.codigo,
                    "valido_desde": inicio.strftime("%H:%M"),
                    "valido_hasta": fin.strftime("%H:%M"),
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

    Body:
        - sku, concepto, cantidad, observaciones
        - sucursal_id (opcional): sucursal elegida en el login de la app;
          se valida el acceso. Sin él, se usa la EmpresaUser activa.
        - request_id (opcional): clave de idempotencia. Reintentar el mismo
          request_id NO duplica el movimiento.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = request.data or {}

        sku_raw = str(data.get("sku", "")).strip()
        concepto = str(data.get("concepto", "")).strip()
        observaciones = str(data.get("observaciones", "")).strip()
        request_id = str(data.get("request_id", "")).strip()[:64]

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

        sucursal, error = _resolver_sucursal_movil(request, data)
        if error:
            return error

        # Mismo permiso fino que la página web de ajuste rápido
        # (opción 'ajuste_stock_rapido' en el sistema de permisos por rol/sucursal).
        if not _puede_ajustar_stock(request.user, sucursal.id):
            return Response(
                {"success": False, "error": "No tienes permiso para ajustar stock"},
                status=status.HTTP_403_FORBIDDEN,
            )

        referencia = (
            f"AJUSTE_STOCK_RAPIDO:{request_id}" if request_id else "AJUSTE_STOCK_RAPIDO"
        )

        # Idempotencia: si el mismo request_id ya se registró (reintento tras
        # timeout de red), devolver el movimiento existente sin duplicar.
        if request_id:
            existente = (
                Movimientos_Producto.objects.filter(referencia_externa=referencia)
                .select_related("ProductoTalla", "ProductoTalla__producto")
                .first()
            )
            if existente:
                pt = existente.ProductoTalla
                return Response(
                    {
                        "success": True,
                        "message": "Ajuste ya registrado (reintento ignorado)",
                        "idempotente": True,
                        "movimiento_id": existente.id,
                        "sku": pt.sku if pt else sku,
                        "producto": pt.producto.articulo if pt else "",
                        "nuevo_stock": pt.stock_sucursal(sucursal.id) if pt else None,
                    },
                    status=status.HTTP_200_OK,
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
            referencia_externa=referencia,
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
