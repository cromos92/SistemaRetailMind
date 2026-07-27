"""
Views para API móvil (JWT).
"""

import logging

from datetime import date

import pytz

from django.core.cache import cache, caches
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from app.models import (
    CodigoAutorizacionDinamico,
    HistorialCambioPrecio,
    LoteProducto,
    Movimientos_Producto,
    OpcionMenu,
    PermisoRol,
    Producto,
    ProductoAtributoValor,
    Producto_Talla,
    Sucursal,
    EmpresaUser,
)
from app.utils_producto_match import normalizar_articulo
from app.utils_tallas import clave_orden_talla
from app.views import registrar_movimiento_producto

from .serializers import (
    ActualizarProductoSerializer,
    NOMBRE_ATRIBUTO_COLOR,
    NOMBRE_ATRIBUTO_GENERO,
    NOMBRE_ATRIBUTO_GENERO_ALT,
    NOMBRE_ATRIBUTO_MARCA,
    VerificarEtiquetaSerializer,
    construir_catalogo,
    obtener_atributo_especialidad,
    resolver_categoria,
    resolver_especialidades,
    resolver_opcion_atributo,
    serializar_coincidencia,
    serializar_producto,
    version_catalogo,
)

logger = logging.getLogger('app')


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


# ══════════════════════════════════════════════════════════════════════════
#  VERIFICADOR DE ETIQUETAS (appNexoStaff)
#
#  El vendedor escanea el código de barras o fotografía la etiqueta; el OCR
#  corre en el teléfono y aquí llegan los valores ya extraídos. El backend
#  busca el producto, compara etiqueta vs sistema y —con permiso— permite
#  corregir precio y recategorizar.
# ══════════════════════════════════════════════════════════════════════════

# Permiso de la web para editar precios ("Gestión de Precios"). Se exige
# `puede_editar`, no `puede_ver`: mirar el módulo no habilita corregir un
# precio desde el celular.
CODIGO_PERMISO_EDICION = "edicion_rapida_precios"

# Ventana del respaldo en cache para la idempotencia (ver ProductoActualizarView).
TTL_IDEMPOTENCIA_CACHE = 900  # 15 minutos

# Catálogo de selectores: se sirve desde el cache `catalogo` que el proyecto ya
# tiene configurado para esto ("TTL largo, invalidación manual", 15 min).
CLAVE_CACHE_CATALOGO = "nexo_movil_catalogo_v1"
TTL_CATALOGO = 900  # segundos; alineado con el TTL del alias `catalogo`

# Tope de filas en `otras_coincidencias` (un artículo numérico como '10' puede
# arrastrar cientos de tallas). `coincidencias` sigue dando el total real.
LIMITE_OTRAS_COINCIDENCIAS = 50


def _puede_editar_producto(user, sucursal_id):
    """
    Permiso para corregir precio / recategorizar desde móvil.

    Mismo patrón que `_puede_ajustar_stock`: si la opción de menú está
    sembrada se respeta el permiso fino (PermisoRol/PermisoUsuario/
    PermisoSucursal, el mismo de la página web); si NO está sembrada, fallback
    por rol para que un deploy sin la opción en BD no deje el endpoint en 403
    para todos (incidente de permisos de Liquidación, jul-2026).
    """
    if OpcionMenu.objects.filter(codigo=CODIGO_PERMISO_EDICION, activo=True).exists():
        return PermisoRol.tiene_permiso(
            user, CODIGO_PERMISO_EDICION, "puede_editar", sucursal_id=sucursal_id
        )
    return getattr(user, "rol", None) in ("administrador", "jefe_local")


def _orden_talla(producto_talla):
    """Clave de orden para tallas: numéricas primero y en orden natural."""
    return clave_orden_talla(producto_talla.talla)


def _tallas_de_articulo(sucursal_id, texto):
    """Todas las Producto_Talla del artículo en la sucursal.

    Se prueba el texto tal cual y su forma normalizada (mayúsculas, sin
    acentos, espacios colapsados — `normalizar_articulo`, la misma canonización
    que usan creación manual y recepción). Ambas variantes van en la MISMA
    query porque el histórico migrado todavía tiene códigos con acentos que la
    forma normalizada no matchearía.
    """
    crudo = (texto or "").strip()
    normalizado = normalizar_articulo(crudo)
    return list(
        Producto_Talla.objects.filter(
            Q(producto__articulo__iexact=crudo)
            | Q(producto__articulo__iexact=normalizado),
            producto__sucursal_id=sucursal_id,
        ).select_related(
            "producto",
            "producto__categoria",
            "producto__categoria__padre",
            "producto__atributo1",
            "producto__atributo2",
            "producto__atributo3",
        )
    )


class ProductoBuscarView(APIView):
    """
    GET /api/v1/mobile/producto/buscar/?q=<texto>&sucursal_id=<id>

    Busca el producto que corresponde a una etiqueta. El mismo `q` sirve para
    SKU, código de barras y código de artículo — aquí se detecta cuál es.

    El código de barras impreso ES el SKU (`app/api/sync/serializers.py` mapea
    `codigo_barra -> sku`).

    Precedencia (ver el detalle en el cuerpo del método):
      - numérico sin ceros a la izquierda → gana SKU
      - numérico con ceros a la izquierda ('003') → gana ARTÍCULO
      - no numérico → ARTÍCULO
    Si la otra rama también matchea, `ambiguo: true` e `interpretado_como`
    dicen qué se eligió, y los resultados de la rama perdedora igual viajan en
    `otras_coincidencias`.

    Cuando el artículo tiene varias tallas, se devuelve como `producto` la que
    tenga stock en la sucursal (la que el vendedor tiene en la mano) y el resto
    va en `otras_coincidencias`, acotado a LIMITE_OTRAS_COINCIDENCIAS.

    "No existe" NO es un error: responde 200 con `encontrado: false` para que
    la app muestre "no existe" en vez de una pantalla de error.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = str(request.query_params.get("q", "")).strip()
        if not q:
            return Response(
                {"success": False, "error": "Debe indicar qué buscar (parámetro q)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sucursal, error = _resolver_sucursal_movil(request, request.query_params)
        if error:
            return error

        # Misma forma que la respuesta con resultados: la app no debería tener
        # que lidiar con claves ausentes según el caso.
        vacio = {"encontrado": False, "coincidencias": 0,
                 "interpretado_como": None, "ambiguo": False, "truncado": False,
                 "producto": None, "otras_coincidencias": []}

        # ── Precedencia SKU vs artículo ──
        # `q` numérico suele ser un código de barras escaneado (el barcode ES
        # el SKU), pero 27.016 productos tienen `articulo` puramente numérico,
        # así que la colisión es cotidiana, no un caso de borde:
        #   - '003' → Postgres castea a 3 y devolvía el SKU 3, un producto sin
        #     ninguna relación. Con ceros a la izquierda el texto es un código
        #     de artículo, no un código de barras: gana ARTÍCULO.
        #   - '10'  → existe como SKU (un BOTIN) y como artículo de 20
        #     productos distintos en EDEL. Gana SKU, porque escanear es el uso
        #     principal y debe ser exacto...
        # ...pero ganar en silencio dejaría al vendedor sin salida, así que
        # cuando la otra rama TAMBIÉN matchea se marca `ambiguo` y sus
        # resultados se anexan a `otras_coincidencias` para que la app ofrezca
        # elegir. La precedencia decide quién va en `producto`, no qué se ve.
        numerico = q.isdigit()
        ceros_iniciales = numerico and len(q) > 1 and q.startswith("0")
        prefiere_sku = numerico and not ceros_iniciales

        por_sku = self._buscar_por_sku(q, sucursal.id) if numerico else None
        por_articulo = _tallas_de_articulo(sucursal.id, q)

        if por_sku is None and not por_articulo:
            return Response(vacio, status=status.HTTP_200_OK)

        principal = None
        candidatos = []
        interpretado = None

        if prefiere_sku and por_sku is not None:
            interpretado = "sku"
            principal = por_sku
            # Las demás tallas del MISMO producto, por si escaneó la etiqueta
            # equivocada del par.
            candidatos = list(
                Producto_Talla.objects.filter(producto_id=principal.producto_id)
                .exclude(id=principal.id)
                .select_related("producto")
            )
            candidatos.sort(key=_orden_talla)
            candidatos += sorted(por_articulo, key=_orden_talla)
        elif por_articulo:
            interpretado = "articulo"
            # La talla que el vendedor tiene en la mano es la que tiene stock.
            principal = max(por_articulo,
                            key=lambda pt: pt.stock_sucursal(sucursal.id))
            candidatos = sorted(
                [pt for pt in por_articulo if pt.id != principal.id],
                key=_orden_talla)
            if por_sku is not None:
                candidatos.append(por_sku)
        else:
            interpretado = "sku"
            principal = por_sku
            candidatos = sorted(
                Producto_Talla.objects.filter(producto_id=principal.producto_id)
                .exclude(id=principal.id)
                .select_related("producto"),
                key=_orden_talla)

        ambiguo = bool(numerico and por_sku is not None and por_articulo)

        # Deduplicar conservando el orden y sin repetir el principal.
        vistos = {principal.id}
        unicos = []
        for pt in candidatos:
            if pt.id in vistos:
                continue
            vistos.add(pt.id)
            unicos.append(pt)

        total = len(unicos) + 1
        # Un artículo como '10' puede arrastrar cientos de tallas: se acota el
        # cuerpo pero `coincidencias` sigue informando el total real.
        candidatos = unicos[:LIMITE_OTRAS_COINCIDENCIAS]

        return Response(
            {
                "encontrado": True,
                "coincidencias": total,
                "interpretado_como": interpretado,
                "ambiguo": ambiguo,
                "truncado": total - 1 > LIMITE_OTRAS_COINCIDENCIAS,
                "producto": serializar_producto(principal, sucursal.id),
                "otras_coincidencias": [
                    serializar_coincidencia(pt, sucursal.id) for pt in candidatos
                ],
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _buscar_por_sku(q, sucursal_id):
        try:
            sku = int(q)
        except (ValueError, OverflowError):
            return None
        return (
            Producto_Talla.objects.filter(sku=sku, producto__sucursal_id=sucursal_id)
            .select_related("producto", "producto__categoria",
                            "producto__categoria__padre", "producto__atributo1",
                            "producto__atributo2", "producto__atributo3")
            .first()
        )


class ProductoCatalogoView(APIView):
    """
    GET /api/v1/mobile/producto/catalogo/

    Todo lo necesario para armar los selectores de recategorización en un solo
    llamado: categorías (árbol PLANO con `padre_id`), especialidades, marcas,
    colores y géneros.

    Permiso: el mismo que `buscar` — cualquier autenticado con acceso a la
    sucursal. Ver el catálogo no es corregir precios: exigir `puede_editar`
    dejaría a quien solo consulta sin poder ni ver en qué categoría está hoy
    el producto.

    Cacheo:
      - Servidor: alias de cache `catalogo` (el que el proyecto ya reserva para
        catálogo de productos), 15 min.
      - Cliente: `Cache-Control: private, max-age=900` + `ETag`.
      - Revalidación: `If-None-Match: <etag>` o `?desde_version=<etag>` →
        304 sin cuerpo. El ETag se deriva del CONTENIDO, así que es estable
        entre workers y cambia solo si el catálogo cambió de verdad.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # La sucursal no filtra el catálogo (categorías y atributos son
        # globales), pero se valida igual para no exponer el catálogo a un
        # usuario que pide una sucursal que no le corresponde.
        _, error = _resolver_sucursal_movil(request, request.query_params)
        if error:
            return error

        cache_catalogo = caches["catalogo"]
        guardado = cache_catalogo.get(CLAVE_CACHE_CATALOGO)
        if guardado is None:
            payload = construir_catalogo()
            version = version_catalogo(payload)
            guardado = {"payload": payload, "version": version}
            cache_catalogo.set(CLAVE_CACHE_CATALOGO, guardado, TTL_CATALOGO)

        payload, version = guardado["payload"], guardado["version"]
        etag = f'"{version}"'

        recibido = (request.headers.get("If-None-Match", "").strip()
                    or str(request.query_params.get("desde_version", "")).strip())
        if recibido and recibido.strip('"') == version:
            respuesta = Response(status=status.HTTP_304_NOT_MODIFIED)
        else:
            respuesta = Response({**payload, "version": version},
                                 status=status.HTTP_200_OK)

        respuesta["ETag"] = etag
        respuesta["Cache-Control"] = f"private, max-age={TTL_CATALOGO}"
        return respuesta


class ProductoVerificarEtiquetaView(APIView):
    """
    POST /api/v1/mobile/producto/verificar-etiqueta/

    Compara los datos leídos de la etiqueta contra el sistema.

    Body: {producto_talla_id, precio_etiqueta, fecha_etiqueta?, sucursal_id}

    El precio se compara contra el PRECIO VIGENTE, que en este ERP es siempre
    `Producto.precioventa`: las campañas de liquidación %/precio-fijo
    reescriben ese campo al activarse (ver `aplicar_precios_campana`), así que
    no hay un "precio de oferta" paralelo que consultar. Las promos NxM no
    tocan el precio y por eso no afectan el veredicto.

    Veredictos: OK | DESACTUALIZADA | SIN_PRECIO_SISTEMA | FECHA_DISCREPA.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerificarEtiquetaSerializer(data=request.data or {})
        if not serializer.is_valid():
            return Response(
                {"success": False, "error": "Datos inválidos",
                 "detalle": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        datos = serializer.validated_data

        sucursal, error = _resolver_sucursal_movil(request, datos)
        if error:
            return error

        producto_talla = (
            Producto_Talla.objects.filter(id=datos["producto_talla_id"])
            .select_related("producto", "producto__categoria",
                            "producto__categoria__padre", "producto__atributo1",
                            "producto__atributo2", "producto__atributo3")
            .first()
        )
        if producto_talla is None:
            return Response(
                {"success": False, "error": "El producto indicado no existe"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if producto_talla.producto.sucursal_id != sucursal.id:
            return Response(
                {"success": False,
                 "error": "El producto no pertenece a la sucursal indicada"},
                status=status.HTTP_403_FORBIDDEN,
            )

        payload = serializar_producto(producto_talla, sucursal.id)
        precio_sistema = payload["precio_vigente"]
        precio_etiqueta = int(datos["precio_etiqueta"])

        diferencias = []
        veredicto = "OK"

        if precio_sistema <= 0:
            veredicto = "SIN_PRECIO_SISTEMA"
            diferencias.append({
                "campo": "precio",
                "etiqueta": precio_etiqueta,
                "sistema": precio_sistema,
                "delta": None,
                "delta_pct": None,
            })
        elif precio_etiqueta != precio_sistema:
            veredicto = "DESACTUALIZADA"
            delta = precio_sistema - precio_etiqueta
            diferencias.append({
                "campo": "precio",
                "etiqueta": precio_etiqueta,
                "sistema": precio_sistema,
                "delta": delta,
                "delta_pct": round(delta / precio_sistema * 100, 1),
            })

        # La fecha impresa se contrasta con la última entrada real de mercadería:
        # si el SKU volvió a ingresar después de imprimir la etiqueta, la
        # etiqueta es de un lote anterior.
        fecha_etiqueta = datos.get("fecha_etiqueta")
        if fecha_etiqueta:
            fecha_sistema = payload["ultima_fecha_ingreso"]
            if fecha_sistema and fecha_etiqueta.isoformat() != fecha_sistema:
                delta_dias = (fecha_etiqueta - date.fromisoformat(fecha_sistema)).days
                diferencias.append({
                    "campo": "fecha",
                    "etiqueta": fecha_etiqueta.isoformat(),
                    "sistema": fecha_sistema,
                    "delta": delta_dias,
                    "delta_pct": None,
                })
                if veredicto == "OK":
                    veredicto = "FECHA_DISCREPA"

        return Response(
            {
                "coincide": veredicto == "OK",
                "veredicto": veredicto,
                "diferencias": diferencias,
                "producto": payload,
            },
            status=status.HTTP_200_OK,
        )


class ProductoActualizarView(APIView):
    """
    POST /api/v1/mobile/producto/actualizar/

    Corrige el precio y/o recategoriza un producto desde la app.

    Body: {producto_id, sucursal_id, request_id} + SOLO los campos a cambiar:
    `precio_venta`, `categoria_id`, `especialidades_ids`, `marca`, `color`,
    `genero`.

    Requiere permiso explícito de edición de precios (ver
    `_puede_editar_producto`), no basta con estar autenticado.

    Idempotencia — `request_id` (obligatorio):
      1. Anclaje durable en BD: el `HistorialCambioPrecio` que deja el cambio
         de precio lleva el token `[req:<request_id>]` en `motivo`. Si ese
         registro ya existe, se devuelve el mismo resultado sin volver a
         escribir. Es la única escritura no-idempotente del endpoint.
      2. Respaldo en cache (mismo worker) para reintentos de cambios que solo
         tocan atributos.
      3. Las escrituras de atributos son asignaciones a un valor objetivo y
         las especialidades se reemplazan por conjunto: repetirlas no duplica
         nada ni corrompe estado.

    `resultado` dice qué pasó realmente, porque `cambios: []` es ambiguo:
      - `APLICADO`     → se escribió ahora (`idempotencia_verificada: true`).
      - `REPETIDO`     → se detectó el reintento y NO se escribió
                         (`idempotencia_verificada: true`).
      - `SIN_CAMBIOS`  → el producto ya estaba así. NO se puede distinguir
                         "no había nada que cambiar" de "reintento de un cambio
                         que solo tocó atributos" — esos no dejan ancla durable.
                         Se marca con `idempotencia_verificada: false` para que
                         la interfaz no muestre ambos casos igual.
    """

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ActualizarProductoSerializer(data=request.data or {})
        if not serializer.is_valid():
            return Response(
                {"ok": False, "error": "Datos inválidos",
                 "detalle": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        datos = serializer.validated_data
        enviados = request.data or {}
        request_id = datos["request_id"].strip()[:64]

        sucursal, error = _resolver_sucursal_movil(request, datos)
        if error:
            return error

        if not _puede_editar_producto(request.user, sucursal.id):
            return Response(
                {"ok": False,
                 "error": "No tienes permiso para editar precios ni categorías "
                          "de productos"},
                status=status.HTTP_403_FORBIDDEN,
            )

        producto = (
            Producto.objects.filter(id=datos["producto_id"])
            .select_related("sucursal", "categoria", "categoria__padre",
                            "atributo1", "atributo2", "atributo3")
            .first()
        )
        if producto is None:
            return Response(
                {"ok": False, "error": "El producto indicado no existe"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if producto.sucursal_id != sucursal.id:
            return Response(
                {"ok": False,
                 "error": "El producto no pertenece a la sucursal indicada"},
                status=status.HTTP_403_FORBIDDEN,
            )

        clave_cache = f"nexo_etiq_actualizar:{request_id}"
        token_request = f"[req:{request_id}]"

        # ── Idempotencia: anclaje durable en el historial de precios ──
        historial_previo = HistorialCambioPrecio.objects.filter(
            producto_id=producto.id, motivo__contains=token_request
        ).first()
        if historial_previo is not None:
            return Response(
                self._respuesta_repetida(producto, sucursal, [{
                    "campo": "precio_venta",
                    "antes": historial_previo.precio_anterior,
                    "despues": historial_previo.precio_nuevo,
                }]),
                status=status.HTTP_200_OK,
            )

        # ── Idempotencia: respaldo en cache (cubre cambios sin precio) ──
        cacheada = cache.get(clave_cache)
        if cacheada is not None:
            return Response(
                self._respuesta_repetida(producto, sucursal, cacheada),
                status=status.HTTP_200_OK,
            )

        # ── Validar TODO antes de escribir nada ──
        nueva_categoria = None
        if "categoria_id" in enviados and datos.get("categoria_id") is not None:
            nueva_categoria, err = resolver_categoria(datos["categoria_id"])
            if err:
                return Response({"ok": False, "error": err},
                                status=status.HTTP_400_BAD_REQUEST)

        opciones_atributo = {}
        for campo, nombre_attr, alterno in (
            ("marca", NOMBRE_ATRIBUTO_MARCA, None),
            ("color", NOMBRE_ATRIBUTO_COLOR, None),
            ("genero", NOMBRE_ATRIBUTO_GENERO, NOMBRE_ATRIBUTO_GENERO_ALT),
        ):
            if campo in enviados and datos.get(campo):
                opcion, err = resolver_opcion_atributo(datos[campo], nombre_attr, alterno)
                if err:
                    return Response({"ok": False, "error": err},
                                    status=status.HTTP_400_BAD_REQUEST)
                opciones_atributo[campo] = opcion

        especialidades = None
        if "especialidades_ids" in enviados:
            especialidades, err = resolver_especialidades(
                datos.get("especialidades_ids") or [])
            if err:
                return Response({"ok": False, "error": err},
                                status=status.HTTP_400_BAD_REQUEST)

        nuevo_precio = None
        if "precio_venta" in enviados:
            nuevo_precio = int(datos["precio_venta"])
            if nuevo_precio <= 0:
                return Response(
                    {"ok": False, "error": "El precio de venta debe ser mayor a 0"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            cambios = self._aplicar(
                request=request,
                producto_id=producto.id,
                sucursal=sucursal,
                nuevo_precio=nuevo_precio,
                nueva_categoria=nueva_categoria,
                opciones_atributo=opciones_atributo,
                especialidades=especialidades,
                token_request=token_request,
            )
        except Exception:
            logger.exception(
                "Error actualizando producto %s desde móvil (req=%s)",
                producto.id, request_id,
            )
            return Response(
                {"ok": False, "error": "No se pudo guardar el cambio"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        cache.set(clave_cache, cambios, TTL_IDEMPOTENCIA_CACHE)

        producto.refresh_from_db()

        if cambios:
            cuerpo = {
                "ok": True,
                "resultado": "APLICADO",
                "idempotente": False,
                # Hubo escritura ahora: no fue una repetición, sin ambigüedad.
                "idempotencia_verificada": True,
                "mensaje": "Cambios guardados",
                "cambios": cambios,
            }
        else:
            # El producto ya estaba en el estado pedido. NO se puede distinguir
            # "no había nada que cambiar" de "es el reintento de un cambio que
            # solo tocó atributos" (ese caso no deja ancla durable en BD; ver
            # docstring). Se marca explícito para que la app no muestre ambas
            # situaciones igual.
            cuerpo = {
                "ok": True,
                "resultado": "SIN_CAMBIOS",
                "idempotente": False,
                "idempotencia_verificada": False,
                "mensaje": ("El producto ya estaba así. Si acabas de guardar, "
                            "es posible que el cambio ya se hubiera aplicado."),
                "cambios": [],
            }

        cuerpo["producto"] = self._payload_producto(producto, sucursal)
        return Response(cuerpo, status=status.HTTP_200_OK)

    # ───────────────────────── helpers ─────────────────────────

    @transaction.atomic
    def _aplicar(self, request, producto_id, sucursal, nuevo_precio,
                 nueva_categoria, opciones_atributo, especialidades,
                 token_request):
        """Aplica los cambios y deja la auditoría de precio. Todo o nada."""
        # Sin `select_related`: Postgres rechaza FOR UPDATE sobre el lado
        # nullable de un outer join (categoria/atributo1..3 son nullables).
        producto = Producto.objects.select_for_update().get(id=producto_id)
        cambios = []

        # ── Atributos: marca / color / género ──
        mapa_campos = {"marca": "atributo1", "color": "atributo2",
                       "genero": "atributo3"}
        for campo, opcion in opciones_atributo.items():
            attr_field = mapa_campos[campo]
            actual = getattr(producto, attr_field)
            if actual and actual.id == opcion.id:
                continue
            cambios.append({
                "campo": campo,
                "antes": actual.valor if actual else None,
                "despues": opcion.valor,
            })
            setattr(producto, attr_field, opcion)

        # ── Categoría (árbol v1.2: padre o hija, ambos válidos) ──
        if nueva_categoria is not None and producto.categoria_id != nueva_categoria.id:
            cambios.append({
                "campo": "categoria_id",
                "antes": producto.categoria_id,
                "despues": nueva_categoria.id,
            })
            producto.categoria = nueva_categoria

        # ── Precio de venta ──
        precio_anterior = int(producto.precioventa or 0)
        cambio_precio = nuevo_precio is not None and nuevo_precio != precio_anterior
        if cambio_precio:
            cambios.append({
                "campo": "precio_venta",
                "antes": precio_anterior,
                "despues": nuevo_precio,
            })
            producto.precioventa = nuevo_precio

        producto.save()

        lotes_actualizados = 0
        if cambio_precio:
            # Espejo del precio en los lotes vivos, igual que la web
            # (`actualizar_precio` en views_modulo_gestion_precios).
            lotes_actualizados = LoteProducto.objects.filter(
                producto_talla__producto=producto,
                cantidad_disponible__gt=0,
                activo=True,
            ).update(precio_venta_unitario=nuevo_precio)

        # ── Especialidades v1.2 (reemplazo por conjunto) ──
        if especialidades is not None:
            attr_esp = obtener_atributo_especialidad()
            if attr_esp is not None:
                ids_nuevos = {o.id for o in especialidades}
                ids_actuales = set(
                    ProductoAtributoValor.objects.filter(
                        producto=producto, atributo=attr_esp
                    ).values_list("opcion_id", flat=True)
                )
                if ids_nuevos != ids_actuales:
                    cambios.append({
                        "campo": "especialidades_ids",
                        "antes": sorted(ids_actuales),
                        "despues": sorted(ids_nuevos),
                    })
                    ProductoAtributoValor.objects.filter(
                        producto=producto, atributo=attr_esp
                    ).exclude(opcion_id__in=ids_nuevos).delete()
                    nuevas = [
                        ProductoAtributoValor(producto=producto, atributo=attr_esp,
                                              opcion_id=oid)
                        for oid in ids_nuevos - ids_actuales
                    ]
                    if nuevas:
                        ProductoAtributoValor.objects.bulk_create(nuevas)

        # ── Auditoría del cambio de precio (modelo ya existente) ──
        # Solo se escribe si el precio REALMENTE cambió: el módulo de auditoría
        # de precios cuenta filas (`Count('id')`) para sus KPIs, y una fila con
        # diferencia 0 inflaría los indicadores por usuario.
        if cambio_precio:
            diferencia = nuevo_precio - precio_anterior
            porcentaje = (diferencia / precio_anterior * 100) if precio_anterior > 0 else 0
            otros = [c["campo"] for c in cambios if c["campo"] != "precio_venta"]
            detalle = f" (además: {', '.join(otros)})" if otros else ""
            HistorialCambioPrecio.objects.create(
                producto=producto,
                precio_anterior=precio_anterior,
                precio_nuevo=nuevo_precio,
                diferencia=diferencia,
                porcentaje_cambio=porcentaje,
                motivo=(f"Verificador de etiquetas (app móvil){detalle} "
                        f"{token_request}"),
                tipo_cambio="MANUAL",
                usuario=request.user,
                ip_address=request.META.get("REMOTE_ADDR"),
                tallas_afectadas=producto.producto_talla.count(),
                lotes_afectados=lotes_actualizados,
            )
            # Sin flechas unicode: el handler de consola de Windows (cp1252)
            # revienta con UnicodeEncodeError y se pierde la línea de log.
            logger.info(
                "Precio corregido desde movil: producto=%s %s -> %s usuario=%s suc=%s",
                producto.id, precio_anterior, nuevo_precio,
                request.user.username, sucursal.id,
            )

        return cambios

    def _payload_producto(self, producto, sucursal):
        """Payload del producto usando la talla más representativa."""
        tallas = list(
            Producto_Talla.objects.filter(producto_id=producto.id)
            .select_related("producto", "producto__categoria",
                            "producto__categoria__padre", "producto__atributo1",
                            "producto__atributo2", "producto__atributo3")
        )
        if not tallas:
            return None
        principal = max(tallas, key=lambda pt: pt.stock_sucursal(sucursal.id))
        return serializar_producto(principal, sucursal.id)

    def _respuesta_repetida(self, producto, sucursal, cambios):
        """Reintento detectado con certeza: no se escribió nada."""
        producto.refresh_from_db()
        return {
            "ok": True,
            "resultado": "REPETIDO",
            "idempotente": True,
            "idempotencia_verificada": True,
            "mensaje": "Este cambio ya estaba guardado (reintento ignorado)",
            "cambios": cambios,
            "producto": self._payload_producto(producto, sucursal),
        }
