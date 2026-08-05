"""
Views Ecommerce
===============
API endpoint para recibir pedidos desde VicentAllEcommercesConected
y vista de gestión para facturarlos directamente.
"""
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import ListView
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.db import models as django_models

from app.models import (
    PedidoEcommerce, Sucursal, Ticket, Dte,
    Ticket_Productos, TicketDetallePago, Producto_Talla, Vendedor,
    HistorialPedidoEcommerce, MetricaAsignacionPedido,
    SUB_ESTADO_PEDIDO_CHOICES, TRANSICIONES_SUB_ESTADO,
    SUB_ESTADOS_BLOQUEADOS_PICKING,
    PermisoRol,
)
from app.utils_ventas import persistir_costeo_fifo


def _verificar_permiso_ecommerce(request, tipo_permiso):
    """
    Verifica un permiso específico sobre ecommerce_pedidos_todos.
    Retorna None si tiene permiso, o un JsonResponse 403 si no.
    """
    sucursal_id = request.session.get('idSucursalActual')
    if not PermisoRol.tiene_permiso(request.user, 'ecommerce_pedidos_todos', tipo_permiso, sucursal_id=sucursal_id):
        return JsonResponse({
            'ok': False,
            'error': f'No tiene permiso para esta acción (requiere {tipo_permiso})',
        }, status=403)
    return None


# Marketplace ecommerce → plataforma de "Venta por Internet" (campo tipo_tarjeta).
# El POS registra estas ventas como VENTA_INTERNET + plataforma; la cuadratura de
# caja las categoriza por tipo_tarjeta (case-insensitive). NO usar TRANSFERENCIA.
# Ver generacionVentas.html (opciones) y cuadraturaCaja.html (categorías).
PLATAFORMA_INTERNET_POR_CANAL = {
    'SHOPIFY': 'Shopify',
    'PARIS': 'Paris',
    'RIPLEY': 'Ripley',
    'WALMART': 'Walmart',
    'OTRO': 'Internet',
}

# Tipos de documento de VENTA (mismos que VentasView._TIPOS_VENTA en
# api/external/views.py). Se usan al resolver el DTE de un ticket por folio:
# los folios son secuencias independientes por tipo, así que sin acotar el
# tipo se podría enlazar una factura/NC con el mismo número.
TIPOS_VENTA_DTE = (
    'BOLETA ELECTRONICA',
    'BOLETA PAPEL',
    'FACTURA ELECTRONICA',
    'FACTURA EXENTA',
)


# Alias de canal que AllConnected / los marketplaces mandan con variantes de
# nombre (mayúsc., separadores, errores). Se mapean al código canónico de
# CANAL_ECOMMERCE_CHOICES para que el listado de pedidos y la cuadratura de caja
# clasifiquen el marketplace correcto. La clave de búsqueda se normaliza quitando
# espacios/guiones/puntos. AMPLIAR según lo que revele el diagnóstico de pedidos
# (ver comando `corregir_canal_walmart`). NO se infiere un canal distinto cuando
# el valor entrante ya es válido y sin alias conocido (no adivinamos).
CANAL_ALIAS = {
    'WALLMART': 'WALMART',   # doble L (error frecuente)
    'WALMARTCL': 'WALMART',  # WALMART_CL / WALMART-CL / WALMART.CL
    'WMT': 'WALMART',
    'WALMARTCHILE': 'WALMART',
    'LIDER': 'WALMART',      # Walmart Chile opera como "Líder"
    'PARIS': 'PARIS',
    'MERCADOLIBRE': 'MERCADO',
    'MERCADOPAGO': 'MERCADO',
}


def _normalizar_canal(valor):
    """Normaliza ``canal_origen`` entrante a un código canónico.

    Aplica strip + upper y resuelve alias conocidos (WALLMART, WALMART_CL, ...)
    al valor canónico de ``CANAL_ECOMMERCE_CHOICES``. Si no hay alias, devuelve
    el valor en mayúsculas tal cual (no se adivina un canal distinto).
    """
    base = (valor or '').strip().upper()
    clave = base.replace(' ', '').replace('-', '').replace('_', '').replace('.', '')
    return CANAL_ALIAS.get(clave, base)


def _crear_pago_ecommerce(ticket, pedido):
    """
    Crea el TicketDetallePago de un pedido ecommerce como VENTA_INTERNET con la
    plataforma del marketplace en `tipo_tarjeta` y el N° de pedido del canal en
    `voucher`. Así el DTE y la cuadratura de caja lo registran como venta por
    internet del canal correspondiente (Paris/Ripley/Walmart/Shopify) y no como
    una transferencia genérica.
    """
    plataforma = PLATAFORMA_INTERNET_POR_CANAL.get(pedido.canal_origen, 'Internet')
    return TicketDetallePago.objects.create(
        ticket=ticket,
        metodo_pago='VENTA_INTERNET',
        tipo_tarjeta=plataforma,
        voucher=(pedido.numero_pedido_canal or '')[:100],
        monto=int(pedido.total or 0),
        notas=f'Pago {pedido.canal_origen} #{pedido.numero_pedido_canal}',
    )


# ---------------------------------------------------------------------------
# Helper: validar items del pedido contra productos de la sucursal
# ---------------------------------------------------------------------------

def _validar_items_pedido(pedido, sucursal=None):
    """
    Retorna lista de dicts con cada item enriquecido:
      - producto_talla: objeto o None
      - encontrado: bool  (True solo si existe Y tiene stock >= cantidad pedida)
      - stock_disponible: int
      - sin_stock: bool  (producto existe pero stock == 0)
      - stock_insuficiente: bool  (producto existe, stock > 0 pero < cantidad pedida)
      - producto_talla_id: int o None (override guardado en el item)

    sucursal: si se pasa, busca productos en esa sucursal en lugar de pedido.sucursal.
    """
    suc = sucursal or pedido.sucursal
    resultado = []
    for item in (pedido.items or []):
        sku = (item.get('sku') or '').strip()
        cantidad = int(item.get('cantidad') or 1)
        pt_id_override = item.get('producto_talla_id')
        producto_talla = None
        if pt_id_override:
            producto_talla = Producto_Talla.objects.filter(
                id=pt_id_override, producto__sucursal=suc
            ).select_related('producto').first()
        if not producto_talla and sku:
            try:
                producto_talla = Producto_Talla.objects.filter(
                    sku=int(sku), producto__sucursal=suc
                ).select_related('producto').first()
            except (ValueError, TypeError):
                pass
        stock_disp = producto_talla.stock if producto_talla else 0
        tiene_stock = producto_talla is not None and stock_disp >= cantidad
        sin_stock = producto_talla is not None and stock_disp == 0
        stock_insuficiente = producto_talla is not None and 0 < stock_disp < cantidad

        # Comparación de precios: precio canal vs precio RM (precio público maestro)
        precio_canal = 0
        try:
            precio_canal = int(float(item.get('precio_unitario') or 0))
        except (ValueError, TypeError):
            precio_canal = 0
        precio_rm = 0
        costo_rm = 0
        descuento_canal = 0
        pct_descuento = 0
        margen_canal_pct = None     # margen % sobre precio canal
        margen_rm_pct = None        # margen % sobre precio RM (referencia)
        MARGEN_BAJO_UMBRAL = 20     # % — debajo de esto se considera margen bajo

        if producto_talla and producto_talla.producto:
            try:
                precio_rm = int(producto_talla.producto.precioventa or 0)
            except (ValueError, TypeError):
                precio_rm = 0
            try:
                costo_rm = int(producto_talla.producto.costo or 0)
            except (ValueError, TypeError):
                costo_rm = 0

            if precio_rm > 0 and precio_canal > 0:
                descuento_canal = precio_rm - precio_canal

                if precio_rm > 0:
                    if descuento_canal >= 0:
                        pct_descuento = round(descuento_canal / precio_rm * 100, 1)
                    else:
                        pct_descuento = round(descuento_canal / precio_rm * 100, 1)  # negativo = sobre precio

            if costo_rm > 0 and precio_canal > 0:
                margen_canal_pct = round((precio_canal - costo_rm) / precio_canal * 100, 1)
            if costo_rm > 0 and precio_rm > 0:
                margen_rm_pct = round((precio_rm - costo_rm) / precio_rm * 100, 1)

        # Clasificación del caso de precio
        precio_clase = 'sin_info'
        if precio_rm > 0 and precio_canal > 0:
            if precio_canal > precio_rm:
                precio_clase = 'sobre_precio'       # vendió más caro que RM → positivo
            elif precio_canal == precio_rm:
                precio_clase = 'precio_igual'
            elif margen_canal_pct is not None and margen_canal_pct < MARGEN_BAJO_UMBRAL:
                precio_clase = 'bajo_margen'        # bajo RM Y margen bajo → alerta
            else:
                precio_clase = 'descuento_ok'       # bajo RM pero margen aceptable → info

        resultado.append({
            **item,
            'producto_talla': producto_talla,
            'encontrado': tiene_stock,
            'sin_stock': sin_stock,
            'stock_insuficiente': stock_insuficiente,
            'stock_disponible': stock_disp,
            'cantidad_pedida': cantidad,
            'producto_nombre_rm': (
                producto_talla.producto.descripcion or producto_talla.producto.articulo
                if producto_talla and producto_talla.producto else None
            ),
            'producto_talla_id_guardado': pt_id_override,
            # Comparación de precios
            'precio_canal': precio_canal,
            'precio_rm': precio_rm,
            'costo_rm': costo_rm,
            'descuento_canal': descuento_canal,
            'pct_descuento': pct_descuento,
            'margen_canal_pct': margen_canal_pct,
            'margen_rm_pct': margen_rm_pct,
            'precio_clase': precio_clase,
            'margen_bajo_umbral': MARGEN_BAJO_UMBRAL,
        })
    return resultado

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: obtener sucursal activa de la sesión
# ---------------------------------------------------------------------------

def _get_session_sucursal(request):
    """
    Lee la sucursal activa de la sesión del usuario.
    Retorna (sucursal_obj, None) si existe, o (None, JsonResponse_error) si no.
    """
    sid = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    if not sid:
        return None, JsonResponse(
            {'ok': False, 'error': 'No hay sucursal activa en sesión. Selecciona una sucursal primero.'},
            status=400
        )
    try:
        return Sucursal.objects.get(id=sid), None
    except Sucursal.DoesNotExist:
        return None, JsonResponse(
            {'ok': False, 'error': f'Sucursal de sesión (id={sid}) no encontrada.'},
            status=400
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verificar_api_key(request):
    """
    Verifica que la solicitud tenga un API key válida.

    Reglas (endurecidas 2026-07-26 — ver docs/SEGURIDAD_URGENTE_2026-07-25.md §4.2):

    * **Falla cerrado**: si ``RETAILMIND_API_KEY`` no está configurada, se
      RECHAZA. Antes devolvía ``True``, dejando abiertos endpoints
      ``@csrf_exempt`` y sin login que crean tickets y queman folios del SII.
    * **Solo por header** ``X-RetailMind-Key`` (que es lo que usa AllConnected).
      El query param ``?api_key=`` queda desactivado porque la clave termina en
      logs de acceso, proxies e historial. Si algún integrador legacy todavía la
      manda así, se puede reactivar temporalmente con la variable de entorno
      ``ECOMMERCE_API_KEY_ALLOW_QUERYSTRING=true`` (deja WARNING en el log).
    * Comparación con ``hmac.compare_digest`` (tiempo constante).
    """
    import hmac
    import os

    from django.conf import settings

    api_key_esperada = getattr(settings, 'RETAILMIND_API_KEY', None) or ''
    if not api_key_esperada:
        logger.error(
            'RETAILMIND_API_KEY no está configurada: se rechaza la petición a %s. '
            'Configurar la variable de entorno para habilitar la API de ecommerce.',
            request.path,
        )
        return False

    api_key_recibida = request.headers.get('X-RetailMind-Key') or ''

    if not api_key_recibida:
        permitir_qs = os.environ.get(
            'ECOMMERCE_API_KEY_ALLOW_QUERYSTRING', ''
        ).strip().lower() in ('1', 'true', 'yes', 'si', 'sí')
        if permitir_qs:
            api_key_recibida = request.GET.get('api_key', '') or ''
            if api_key_recibida:
                logger.warning(
                    'API key recibida por query string en %s (modo compatibilidad). '
                    'Migrar el integrador al header X-RetailMind-Key.',
                    request.path,
                )

    if not api_key_recibida:
        return False

    return hmac.compare_digest(str(api_key_recibida), str(api_key_esperada))


def _generar_numero_ticket_rm():
    """Genera un número de ticket RM único: RM-XXXXXXXX."""
    base = uuid.uuid4().hex[:8].upper()
    while PedidoEcommerce.objects.filter(numero_ticket_rm=f'RM-{base}').exists():
        base = uuid.uuid4().hex[:8].upper()
    return f'RM-{base}'


def _respuesta_pedido_existente(existente, correlativo_in='', correlativo_numero_in=None):
    """Respuesta idempotente para un pedido ya ingresado."""
    if correlativo_in and correlativo_in != (existente.correlativo or ''):
        existente.correlativo = correlativo_in
        existente.correlativo_numero = correlativo_numero_in
        existente.save(update_fields=['correlativo', 'correlativo_numero'])
    return {
        'ok': True,
        'numero_ticket_rm': existente.numero_ticket_rm,
        'pedido_ecommerce_id': existente.id,
        'ya_existia': True,
        'correlativo': existente.correlativo,
        'status': 200,
    }


def _ingestar_pedido_dict(data):
    """
    Valida y crea (o recupera, idempotente) un PedidoEcommerce a partir de un dict
    con el shape del body de ``POST /api/ecommerce/pedidos/``.

    Reutilizado por el endpoint POST (``api_recibir_pedido_ecommerce``) y por el
    pull desde AllConnected (``allconnected_pedidos_service``). NO lanza: los
    errores se devuelven en el dict.

    Devuelve un dict listo para ``JsonResponse`` con una clave extra ``status``
    (código HTTP sugerido) que el caller HTTP puede extraer con ``.pop('status')``.
    """
    # Campos obligatorios
    numero_pedido_canal = (data.get('numero_pedido_canal') or '').strip()
    canal_origen = _normalizar_canal(data.get('canal_origen'))
    sucursal_id = data.get('sucursal_id')
    cliente_nombre = (data.get('cliente_nombre') or '').strip()

    if not numero_pedido_canal:
        return {'ok': False, 'error': 'numero_pedido_canal es obligatorio', 'status': 400}
    if not canal_origen:
        return {'ok': False, 'error': 'canal_origen es obligatorio', 'status': 400}
    if not sucursal_id:
        return {'ok': False, 'error': 'sucursal_id es obligatorio', 'status': 400}
    if not cliente_nombre:
        return {'ok': False, 'error': 'cliente_nombre es obligatorio', 'status': 400}

    try:
        sucursal = Sucursal.objects.select_related('empresa').get(id=sucursal_id)
    except Sucursal.DoesNotExist:
        return {'ok': False, 'error': f'Sucursal {sucursal_id} no encontrada', 'status': 400}

    # Validar que la sucursal pertenezca a la empresa del payload (si se provee)
    rut_payload = (data.get('rut_empresa') or '').replace('.', '').replace(' ', '').upper().strip()
    if rut_payload:
        try:
            rut_sucursal = (sucursal.empresa.rut or '').replace('.', '').replace(' ', '').upper().strip()
        except Exception:
            rut_sucursal = ''
        if not rut_sucursal or rut_sucursal != rut_payload:
            return {
                'ok': False,
                'error': f'Sucursal {sucursal_id} no pertenece a la empresa {data.get("rut_empresa")}',
                'status': 400,
            }

    # Folio de despacho que AllConnected imprime en la etiqueta. Puede llegar
    # vacío (aún sin imprimir) y, como el endpoint es idempotente/re-consultable,
    # llegar con valor en un pull posterior.
    correlativo_in = (data.get('correlativo') or '').strip()
    correlativo_numero_in = data.get('correlativo_numero')
    try:
        correlativo_numero_in = (
            int(correlativo_numero_in) if correlativo_numero_in not in (None, '') else None
        )
    except (TypeError, ValueError):
        correlativo_numero_in = None

    # Verificar si ya existe un pedido para este canal+número (idempotente)
    existente = PedidoEcommerce.objects.filter(
        numero_pedido_canal=numero_pedido_canal,
        canal_origen=canal_origen,
    ).first()
    if existente:
        # Actualizar el folio si AllConnected ya lo asignó y antes estaba vacío
        # (o cambió). NUNCA pisar un folio ya seteado con un valor vacío entrante.
        return _respuesta_pedido_existente(existente, correlativo_in, correlativo_numero_in)

    try:
        # Validar stock en la sucursal para determinar sub-estado inicial
        items_data = data.get('items', [])
        pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm=_generar_numero_ticket_rm(),
            numero_pedido_canal=numero_pedido_canal,
            numero_pedido_origen=data.get('numero_pedido', '') or '',
            correlativo=correlativo_in,
            correlativo_numero=correlativo_numero_in,
            canal_origen=canal_origen,
            sucursal=sucursal,
            sucursal_original=sucursal,
            rut_empresa=data.get('rut_empresa', '') or '',
            cliente_nombre=cliente_nombre,
            cliente_email=data.get('cliente_email', ''),
            cliente_documento=data.get('cliente_documento', ''),
            coupon_code=(data.get('coupon_code', '') or ''),
            from_app=bool(data.get('from_app')),
            subtotal=data.get('subtotal', 0),
            descuento=data.get('descuento', 0),
            impuestos=data.get('impuestos', 0),
            costo_envio=data.get('costo_envio', 0),
            total=data.get('total', 0),
            items=items_data,
            direccion_envio=data.get('direccion_envio', ''),
        )

        # Verificar stock para determinar sub-estado
        items_val = _validar_items_pedido(pedido, sucursal=sucursal)
        todos_con_stock = all(iv['encontrado'] for iv in items_val) if items_val else False
        items_sin = sum(1 for iv in items_val if not iv['encontrado'])

        if todos_con_stock:
            pedido.sub_estado = 'ASIGNADO'
            pedido.fecha_asignacion = timezone.now()
        else:
            pedido.sub_estado = 'RECIBIDO'
        pedido.save(update_fields=['sub_estado', 'fecha_asignacion'])

        # Crear historial inicial
        HistorialPedidoEcommerce.objects.create(
            pedido=pedido,
            estado_anterior='',
            estado_nuevo='PENDIENTE',
            sub_estado_anterior='',
            sub_estado_nuevo=pedido.sub_estado,
            sucursal_nueva=sucursal,
            tipo_evento='CAMBIO_ESTADO',
            motivo='Pedido recibido desde API',
        )

        # Crear métrica de asignación inicial
        MetricaAsignacionPedido.objects.create(
            pedido=pedido,
            sucursal_asignada=sucursal,
            todos_items_con_stock=todos_con_stock,
            items_sin_stock=items_sin,
        )

        # Compra de la app (puntos): si el pedido ya viene PAGADO y trae cupón
        # PTS-, confirmar los puntos por la vía rápida. La facturación lo
        # reconfirma idempotentemente (es la red de seguridad si llega sin pagar).
        _estado_pagado = (data.get('estado') or '').strip().upper() in (
            'PREPARANDO', 'CONFIRMADO', 'ENVIADO', 'ENTREGADO', 'COMPLETADO', 'PAGADO',
        )
        if _estado_pagado and (pedido.coupon_code or '').upper().startswith('PTS-'):
            try:
                from app.services import fidelizacion_service
                fidelizacion_service.conciliar_reserva_por_pedido(
                    pedido, pedido.coupon_code, pedido.descuento)
            except Exception:
                logger.exception('Conciliación de puntos (ingesta) falló para %s',
                                 numero_pedido_canal)

    except IntegrityError:
        existente = PedidoEcommerce.objects.filter(
            numero_pedido_canal=numero_pedido_canal,
            canal_origen=canal_origen,
        ).first()
        if existente:
            return _respuesta_pedido_existente(existente, correlativo_in, correlativo_numero_in)
        return {'ok': False, 'error': 'Pedido duplicado no pudo recuperarse', 'status': 409}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'status': 500}

    return {
        'ok': True,
        'numero_ticket_rm': pedido.numero_ticket_rm,
        'pedido_ecommerce_id': pedido.id,
        'ya_existia': False,
        'correlativo': pedido.correlativo,
        'sub_estado': pedido.sub_estado,
        'todos_items_con_stock': todos_con_stock,
        'status': 201,
    }


# ---------------------------------------------------------------------------
# API — Recepción de pedidos externos
# ---------------------------------------------------------------------------

@csrf_exempt
def api_recibir_pedido_ecommerce(request):
    """
    POST /api/ecommerce/pedidos/

    Recibe un pedido de VicentAllEcommercesConected y crea un PedidoEcommerce.

    Body JSON esperado:
    {
        "numero_pedido_canal": "...",
        "canal_origen": "SHOPIFY|PARIS|RIPLEY|WALMART|OTRO",
        "sucursal_id": <int>,
        "cliente_nombre": "...",
        "cliente_email": "...",          (opcional)
        "cliente_documento": "...",      (opcional, RUT para DTE)
        "subtotal": 0.0,
        "descuento": 0.0,
        "impuestos": 0.0,              (opcional; trazabilidad)
        "costo_envio": 0.0,
        "total": 0.0,
        "items": [
            {"sku": "...", "nombre": "...", "cantidad": 1, "precio_unitario": 0.0}
        ],
        "direccion_envio": "..."         (opcional)
    }

    Respuesta exitosa:
    {
        "ok": true,
        "numero_ticket_rm": "RM-XXXXXXXX",
        "pedido_ecommerce_id": <int>
    }
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)

    if not _verificar_api_key(request):
        return JsonResponse({'ok': False, 'error': 'API key inválida'}, status=401)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Body JSON inválido'}, status=400)

    resultado = _ingestar_pedido_dict(data)
    status = resultado.pop('status', 200)
    return JsonResponse(resultado, status=status)


@csrf_exempt
def api_pedido_pagado(request):
    """
    POST /api/ecommerce/pedidos/pagado/   (Header X-RetailMind-Key)

    Aviso idempotente de que un pedido de ecommerce pasó a PAGADO. Dispara la
    conciliación de puntos de la app (débito real por el descuento del cupón
    PTS-) aunque el pedido se haya ingerido ANTES de pagarse — cierra el hueco de
    que la conciliación quedara dependiendo solo de la facturación manual.
    No falla si el pedido aún no se ingirió o no usó puntos.

    Body: { "canal_origen": "...", "numero_pedido_canal": "..." }
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)
    if not _verificar_api_key(request):
        return JsonResponse({'ok': False, 'error': 'API key inválida'}, status=401)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Body JSON inválido'}, status=400)

    numero = (data.get('numero_pedido_canal') or '').strip()
    canal = _normalizar_canal(data.get('canal_origen'))
    if not numero or not canal:
        return JsonResponse(
            {'ok': False, 'error': 'numero_pedido_canal y canal_origen son obligatorios'},
            status=400)

    pedido = PedidoEcommerce.objects.filter(
        numero_pedido_canal=numero, canal_origen=canal).first()
    if pedido is None:
        # Aún no ingerido: la ingesta lo conciliará cuando llegue pagado.
        return JsonResponse({'ok': True, 'conciliado': False,
                             'detalle': 'pedido aún no ingerido'})

    conciliado = False
    if (pedido.coupon_code or '').upper().startswith('PTS-'):
        try:
            from app.services import fidelizacion_service
            fidelizacion_service.conciliar_reserva_por_pedido(
                pedido, pedido.coupon_code, pedido.descuento)
            conciliado = True
        except Exception:
            logger.exception('api_pedido_pagado: conciliación falló para %s', numero)
    return JsonResponse({'ok': True, 'conciliado': conciliado})


@csrf_exempt
def api_asignar_ticket_rm(request):
    """
    GET /api/ecommerce/pedidos/<numero_ticket_rm>/
    Retorna los datos del PedidoEcommerce para que VicentAllEcommercesConected
    confirme o consulte el estado.
    """
    if request.method != 'GET':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)

    if not _verificar_api_key(request):
        return JsonResponse({'ok': False, 'error': 'API key inválida'}, status=401)

    numero = request.GET.get('numero_ticket_rm', '').strip()
    if not numero:
        return JsonResponse({'ok': False, 'error': 'numero_ticket_rm requerido'}, status=400)

    try:
        pedido = PedidoEcommerce.objects.select_related('sucursal', 'ticket', 'dte').get(
            numero_ticket_rm=numero
        )
    except PedidoEcommerce.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Pedido no encontrado'}, status=404)

    respuesta = {
        'ok': True,
        'cliente_nombre': pedido.cliente_nombre,
        'total': str(pedido.total),
    }
    respuesta.update(_tracking_pedido_dict(pedido))
    return JsonResponse(respuesta)


def _tracking_pedido_dict(pedido):
    """Estado operativo del pedido para AllConnected (consultar + batch).

    Solo AGREGAR claves aquí: el cliente de AC lee la respuesta
    defensivamente y versiones viejas siguen consumiendo las claves
    originales (estado / ticket_id / dte_id).
    """
    def _iso(dt):
        return timezone.localtime(dt).isoformat() if dt else None

    suc = pedido.sucursal
    return {
        'numero_ticket_rm': pedido.numero_ticket_rm,
        'estado': pedido.estado,
        'sub_estado': pedido.sub_estado,
        'sub_estado_display': pedido.get_sub_estado_display(),
        'canal_origen': pedido.canal_origen,
        'sucursal': {
            'id': pedido.sucursal_id,
            'alias': (suc.nombre or suc.alias or '') if suc else '',
        },
        'fechas': {
            'recepcion': _iso(pedido.fecha_recepcion),
            'asignacion': _iso(pedido.fecha_asignacion),
            'impresion_guia': _iso(pedido.fecha_impresion_guia),
            'inicio_preparacion': _iso(pedido.fecha_inicio_preparacion),
            'listo_despacho': _iso(pedido.fecha_listo_despacho),
            'facturacion': _iso(pedido.fecha_facturacion),
        },
        'ticket_id': pedido.ticket_id,
        'dte_id': pedido.dte_id,
    }


@csrf_exempt
def api_estado_pedidos_batch(request):
    """POST /app/api/ecommerce/pedidos/estado-batch/

    Estado operativo (avance en tienda) de hasta 300 pedidos por
    `numero_ticket_rm`, para que AllConnected refresque su columna
    "Tienda (RM)" sin consultar de a uno. Misma API key que el resto
    de la API ecommerce.

    Body: {"tickets": ["<numero_ticket_rm>", ...]}
    Respuesta: {"ok": true, "pedidos": {"<ticket>": {...}}, "no_encontrados": [...]}
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    if not _verificar_api_key(request):
        return JsonResponse({'ok': False, 'error': 'API key inválida'}, status=401)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    tickets = data.get('tickets') or []
    if not isinstance(tickets, list) or not tickets:
        return JsonResponse({'ok': False, 'error': 'tickets debe ser una lista no vacía'}, status=400)
    if len(tickets) > 300:
        return JsonResponse({'ok': False, 'error': 'Máximo 300 tickets por consulta'}, status=400)

    tickets = [str(t).strip() for t in tickets if str(t).strip()]
    pedidos = (
        PedidoEcommerce.objects
        .filter(numero_ticket_rm__in=tickets)
        .select_related('sucursal')
    )
    por_ticket = {p.numero_ticket_rm: _tracking_pedido_dict(p) for p in pedidos}
    return JsonResponse({
        'ok': True,
        'pedidos': por_ticket,
        'no_encontrados': [t for t in tickets if t not in por_ticket],
    })


@csrf_exempt
def api_cancelar_pedido_ecommerce(request):
    """
    POST /api/ecommerce/pedidos/cancelar/

    AllConnected avisa que un pedido fue CANCELADO en el canal. RM lo marca
    CANCELADO para que salga de la lista PENDIENTE y no se pueda facturar
    (ambas vistas de facturación filtran estado='PENDIENTE').

    Auth: header X-RetailMind-Key (igual que la recepción de pedidos).

    Body JSON:
        {
            "numero_pedido_canal": "...",
            "canal_origen": "SHOPIFY|PARIS|RIPLEY|WALMART|...",
            "motivo": "..."   (opcional)
        }

    Respuestas:
        200 {ok:true, estado:'CANCELADO', ya_cancelado:bool}
        409 {ok:false, error:'ya facturado', dte_id}  -> requiere nota de crédito
        404 {ok:false, error:'no encontrado'}
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)

    if not _verificar_api_key(request):
        return JsonResponse({'ok': False, 'error': 'API key inválida'}, status=401)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Body JSON inválido'}, status=400)

    numero_pedido_canal = (data.get('numero_pedido_canal') or '').strip()
    canal_origen = _normalizar_canal(data.get('canal_origen'))
    if not numero_pedido_canal or not canal_origen:
        return JsonResponse(
            {'ok': False, 'error': 'numero_pedido_canal y canal_origen son obligatorios'},
            status=400,
        )

    pedido = PedidoEcommerce.objects.filter(
        numero_pedido_canal=numero_pedido_canal,
        canal_origen=canal_origen,
    ).first()
    if not pedido:
        return JsonResponse({'ok': False, 'error': 'Pedido no encontrado'}, status=404)

    # Idempotente: si ya está cancelado, OK
    if pedido.estado == 'CANCELADO':
        return JsonResponse({'ok': True, 'estado': 'CANCELADO', 'ya_cancelado': True})

    # Si ya se facturó (DTE emitido), no se puede cancelar sin nota de crédito
    if pedido.estado == 'FACTURADO' or pedido.dte_id:
        return JsonResponse({
            'ok': False,
            'error': 'El pedido ya fue facturado (DTE emitido). Requiere nota de crédito para anular.',
            'estado': pedido.estado,
            'dte_id': pedido.dte_id,
        }, status=409)

    estado_anterior = pedido.estado
    sub_estado_anterior = pedido.sub_estado
    motivo = (data.get('motivo') or 'Cancelado en el canal (AllConnected)')[:255]

    pedido.estado = 'CANCELADO'
    pedido.sub_estado = 'CANCELADO_CLIENTE'
    pedido.save(update_fields=['estado', 'sub_estado'])

    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=estado_anterior,
        estado_nuevo='CANCELADO',
        sub_estado_anterior=sub_estado_anterior,
        sub_estado_nuevo='CANCELADO_CLIENTE',
        tipo_evento='CAMBIO_ESTADO',
        motivo=motivo,
    )

    return JsonResponse({'ok': True, 'estado': 'CANCELADO', 'ya_cancelado': False})


@csrf_exempt
def api_cambio_producto_pedido(request):
    """
    POST /api/ecommerce/pedidos/cambio-producto/

    AllConnected avisa que las LÍNEAS de un pedido cambiaron (sustitución de
    producto por cacho / sin stock). RM reemplaza ``items``, re-matchea los SKUs
    contra el stock de la sucursal y recalcula el sub-estado.

    Por qué existe: el pedido se registra en RM apenas entra (``PedidoEcommerce``
    en estado PENDIENTE, sin DTE y sin mover stock) y se factura DESPUÉS, a mano,
    desde la UI. Entre esos dos momentos el operador de AllConnected todavía puede
    sustituir un producto, pero el POST de creación es idempotente por
    (canal_origen, numero_pedido_canal) y DESCARTA los items nuevos en silencio —
    o sea que sin este endpoint RM facturaba el producto viejo.

    Auth: header X-RetailMind-Key (igual que el resto de la API de ecommerce).

    Body JSON:
        {
            "numero_ticket_rm": "RM-XXXXXXXX",           (uno de los dos)
            "numero_pedido_canal": "...",                 (uno de los dos)
            "canal_origen": "REALSPORT|PAOLA|..."         (si se usa el anterior)
            "items": [{"sku": "...", "nombre": "...",
                       "cantidad": 1, "precio_unitario": 12990}, ...],
            "subtotal"/"descuento"/"impuestos"/"costo_envio"/"total"  (opcionales)
        }

    Respuestas:
        200 {ok:true, numero_ticket_rm, sub_estado, todos_items_con_stock, items_sin_stock}
        409 {ok:false, error:'ya facturado', dte_id}  -> requiere nota de crédito
        409 {ok:false, error:'cancelado'}
        404 {ok:false, error:'no encontrado'}
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)

    if not _verificar_api_key(request):
        return JsonResponse({'ok': False, 'error': 'API key inválida'}, status=401)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Body JSON inválido'}, status=400)

    items_nuevos = data.get('items')
    if not isinstance(items_nuevos, list) or not items_nuevos:
        return JsonResponse(
            {'ok': False, 'error': 'items es obligatorio y debe ser una lista no vacía'},
            status=400,
        )

    # Localización: se aceptan LAS DOS claves. Los endpoints existentes son
    # asimétricos (crear/cancelar/pagado usan canal+numero_pedido_canal;
    # consultar usa numero_ticket_rm) y no conviene repetir el problema.
    numero_ticket_rm = (data.get('numero_ticket_rm') or '').strip()
    numero_pedido_canal = (data.get('numero_pedido_canal') or '').strip()
    canal_origen = _normalizar_canal(data.get('canal_origen'))

    pedido = None
    if numero_ticket_rm:
        pedido = PedidoEcommerce.objects.filter(numero_ticket_rm=numero_ticket_rm).first()
    if pedido is None and numero_pedido_canal and canal_origen:
        pedido = PedidoEcommerce.objects.filter(
            numero_pedido_canal=numero_pedido_canal,
            canal_origen=canal_origen,
        ).first()
    if pedido is None:
        if not numero_ticket_rm and not (numero_pedido_canal and canal_origen):
            return JsonResponse(
                {'ok': False,
                 'error': 'Indicá numero_ticket_rm, o numero_pedido_canal + canal_origen'},
                status=400,
            )
        return JsonResponse({'ok': False, 'error': 'Pedido no encontrado'}, status=404)

    # Ya facturado: el DTE está emitido y el stock salió por FIFO. Un cambio de
    # línea acá dejaría la boleta y la venta descuadradas — va por nota de crédito.
    if pedido.estado == 'FACTURADO' or pedido.dte_id:
        return JsonResponse({
            'ok': False,
            'error': 'El pedido ya fue facturado (DTE emitido). El cambio de producto '
                     'requiere nota de crédito.',
            'estado': pedido.estado,
            'dte_id': pedido.dte_id,
        }, status=409)

    if pedido.estado == 'CANCELADO':
        return JsonResponse({
            'ok': False,
            'error': 'El pedido está cancelado; no se pueden cambiar sus líneas.',
            'estado': pedido.estado,
        }, status=409)

    # Normalizar los items al shape que espera _validar_items_pedido
    # (sku / cantidad / precio_unitario, más el nombre para mostrar).
    items_normalizados = []
    for it in items_nuevos:
        if not isinstance(it, dict):
            continue
        try:
            cantidad = int(it.get('cantidad') or 1)
        except (TypeError, ValueError):
            cantidad = 1
        try:
            precio_unitario = float(it.get('precio_unitario') or 0)
        except (TypeError, ValueError):
            precio_unitario = 0
        items_normalizados.append({
            'sku': str(it.get('sku') or '').strip(),
            'nombre': (it.get('nombre') or it.get('nombre_producto') or '').strip(),
            'cantidad': max(cantidad, 1),
            'precio_unitario': precio_unitario,
        })

    if not items_normalizados:
        return JsonResponse(
            {'ok': False, 'error': 'Ningún item válido en el payload'}, status=400
        )

    items_anteriores = pedido.items or []
    sub_estado_anterior = pedido.sub_estado

    with transaction.atomic():
        pedido = PedidoEcommerce.objects.select_for_update().get(pk=pedido.pk)

        # Revalidar bajo lock: entre el chequeo de arriba y el UPDATE alguien
        # pudo haber facturado el pedido desde la UI.
        if pedido.estado == 'FACTURADO' or pedido.dte_id:
            return JsonResponse({
                'ok': False,
                'error': 'El pedido se facturó mientras se procesaba el cambio.',
                'estado': pedido.estado,
                'dte_id': pedido.dte_id,
            }, status=409)

        pedido.items = items_normalizados

        # Totales: si AllConnected los manda, mandan ellos (es la fuente de
        # verdad del monto cobrado). Si no, se recalcula el subtotal desde las
        # líneas y el total se rearma con la misma fórmula del pedido.
        def _dec(clave, actual):
            valor = data.get(clave)
            if valor in (None, ''):
                return actual
            try:
                return Decimal(str(valor))
            except (InvalidOperation, TypeError, ValueError):
                return actual

        subtotal_calculado = sum(
            Decimal(str(i['precio_unitario'])) * i['cantidad'] for i in items_normalizados
        )
        pedido.subtotal = _dec('subtotal', subtotal_calculado)
        pedido.descuento = _dec('descuento', pedido.descuento)
        pedido.impuestos = _dec('impuestos', pedido.impuestos)
        pedido.costo_envio = _dec('costo_envio', pedido.costo_envio)
        total_calculado = (
            pedido.subtotal - pedido.descuento + pedido.impuestos + pedido.costo_envio
        )
        pedido.total = _dec('total', total_calculado)

        # Re-matchear contra el stock de la sucursal, igual que en la ingesta.
        items_val = _validar_items_pedido(pedido, sucursal=pedido.sucursal)
        todos_con_stock = all(iv['encontrado'] for iv in items_val) if items_val else False
        items_sin = sum(1 for iv in items_val if not iv['encontrado'])

        if todos_con_stock:
            pedido.sub_estado = 'ASIGNADO'
            if not pedido.fecha_asignacion:
                pedido.fecha_asignacion = timezone.now()
        else:
            pedido.sub_estado = 'RECIBIDO'

        pedido.save(update_fields=[
            'items', 'subtotal', 'descuento', 'impuestos', 'costo_envio', 'total',
            'sub_estado', 'fecha_asignacion',
        ])

        # El historial no tiene campo JSON, así que el detalle del cambio va en
        # `motivo` (TextField) — es la única traza de qué se sustituyó.
        skus_antes = ', '.join(str(i.get('sku') or '?') for i in items_anteriores) or '—'
        skus_despues = ', '.join(i['sku'] or '?' for i in items_normalizados) or '—'
        motivo_base = (data.get('motivo')
                       or 'Cambio de producto notificado por AllConnected')
        HistorialPedidoEcommerce.objects.create(
            pedido=pedido,
            estado_anterior=pedido.estado,
            estado_nuevo=pedido.estado,
            sub_estado_anterior=sub_estado_anterior,
            sub_estado_nuevo=pedido.sub_estado,
            tipo_evento='CAMBIO_ESTADO',
            motivo=(
                f'{motivo_base}. SKUs antes: [{skus_antes}] → después: [{skus_despues}]. '
                f'Stock completo: {"si" if todos_con_stock else "NO"}.'
            ),
        )

    logger.info(
        'Cambio de producto RM %s: %d items (%s)',
        pedido.numero_ticket_rm, len(items_normalizados), pedido.sub_estado,
    )
    return JsonResponse({
        'ok': True,
        'numero_ticket_rm': pedido.numero_ticket_rm,
        'estado': pedido.estado,
        'sub_estado': pedido.sub_estado,
        'todos_items_con_stock': todos_con_stock,
        'items_sin_stock': items_sin,
        'total': str(pedido.total),
    })


@login_required
def traer_pedidos_allconnected(request):
    """
    POST /ecommerce/pedidos/traer/

    Botón "Traer pedidos": RetailMind sale a consultar (pull) los pedidos
    pendientes a AllConnected y los ingresa, reutilizando la lógica de recepción
    (``_ingestar_pedido_dict``).

    Si el pull no está configurado (sin ``ALLCONNECTED_API_BASE_URL``), devuelve
    ``{ok: True, configurado: False}`` y la UI solo refresca la tabla con los
    pedidos ya recibidos por push.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)

    deny = _verificar_permiso_ecommerce(request, 'puede_crear')
    if deny:
        return deny

    # Rango de fechas opcional (YYYY-MM-DD). Si no viene, AllConnected usa el mes actual.
    desde = hasta = None
    try:
        if request.body:
            body = json.loads(request.body)
            desde = (body.get('desde') or '').strip() or None
            hasta = (body.get('hasta') or '').strip() or None
    except (json.JSONDecodeError, ValueError):
        pass

    # Import lazy para evitar import circular en la carga del módulo.
    from app.services import allconnected_pedidos_service

    rut_empresa = request.session.get('rutEmpresaActual')
    resultado = allconnected_pedidos_service.traer_pedidos_pendientes(
        rut_empresa=rut_empresa, desde=desde, hasta=hasta,
    )
    return JsonResponse(resultado)


# ---------------------------------------------------------------------------
# Scoping y filtros compartidos (listado, KPIs, paneles y export CSV)
# ---------------------------------------------------------------------------

def _scope_empresa_pedidos(qs, user):
    """Acota un queryset de PedidoEcommerce a las empresas del usuario.

    Los administradores (y quienes tienen el flag de "ver todas las sucursales")
    ven todo. El resto ve los pedidos de **todas** sus empresas asignadas vía
    ``EmpresaUser(status=True)``, más los que llegaron sin RUT (legacy).

    Antes se resolvía con un ``EmpresaUser.objects.filter(user=user).first()``
    sin ``status`` ni ``order_by``: para los 8 usuarios que tienen más de una
    empresa asignada, la empresa elegida era arbitraria (podía incluso ser una
    con ``status=False``), así que veían los pedidos de una sola de sus empresas
    y no siempre la misma.
    """
    if getattr(user, 'rol', '') == 'administrador':
        return qs
    try:
        from app.models import EmpresaUser, PermisoUsuario
        if PermisoUsuario.usuario_ve_todas_sucursales(user):
            return qs

        ruts = [
            r for r in EmpresaUser.objects.filter(user=user, status=True)
            .values_list('empresa__rut', flat=True).distinct()
            if r
        ]
        if ruts:
            qs = qs.filter(
                django_models.Q(rut_empresa__in=ruts)
                | django_models.Q(rut_empresa='')
            )
        else:
            logger.warning(
                'Usuario %s no tiene ninguna empresa activa (EmpresaUser status=True): '
                'no se pudo acotar el listado de pedidos ecommerce.', user,
            )
    except Exception:  # pragma: no cover - defensivo, no debe tumbar la pantalla
        logger.exception('No se pudo aplicar el scope de empresa a pedidos ecommerce')
    return qs


def _scope_sucursal_pedidos(qs, request):
    """Aplica el filtro de sucursal: explícita, por defecto la de sesión, o todas."""
    sucursal_id = request.GET.get('sucursal_id', '')
    ver_todas = request.GET.get('ver_todas', '')
    if sucursal_id:
        return qs.filter(sucursal_id=sucursal_id)
    if not ver_todas:
        session_suc = (
            request.session.get('idSucursalActual')
            or request.session.get('sucursalActual')
        )
        if session_suc:
            return qs.filter(sucursal_id=session_suc)
    return qs


# Mensaje único para todo lo que queda bloqueado mientras el pedido está
# marcado SIN_STOCK (guía, facturación individual y masiva).
_MSG_BLOQUEO_SIN_STOCK = (
    'Este pedido está marcado SIN STOCK y la incidencia sigue abierta. '
    'Resuélvelo primero: reasígnalo a otra sucursal, o usa "Reactivar" si el '
    'producto apareció.'
)


def _bloqueo_por_estado_canal(pedido):
    """Razón para NO facturar según el último estado sincronizado del canal.

    Devuelve un string de error o None. Solo bloquea cuando hay información
    POSITIVA de AllConnected (``estado_canal`` no vacío): los pedidos nunca
    sincronizados siguen facturables como siempre (compatibilidad).

      - CANCELADO/DEVUELTO/REEMBOLSADO → cinturón: la sync ya los marca
        CANCELADO local, pero si alguien factura entre medio, esto lo corta.
      - ENVIADO/EN_TRANSITO/ENTREGADO → ya despachado: la venta se documentó
        por concepto fuera del módulo (la sync lo cierra como
        FACTURADO_EXTERNO); boletearlo acá sería doble documento.
      - PENDIENTE → el canal aún no confirma el pago del pedido; boletearlo
        sería facturar una venta que puede no concretarse.
    """
    from app.services.allconnected_pedidos_service import (
        ESTADOS_CANAL_CANCELADOS, ESTADOS_CANAL_DESPACHADOS,
    )

    ec = (pedido.estado_canal or '').upper()
    if not ec:
        return None
    if ec in ESTADOS_CANAL_CANCELADOS:
        return (f'El canal reporta este pedido como {ec} (AllConnected). '
                f'No corresponde facturarlo.')
    if ec in ESTADOS_CANAL_DESPACHADOS:
        return (f'El canal reporta este pedido como {ec}: ya fue despachado y la '
                f'venta se documenta por concepto fuera del módulo. Facturarlo acá '
                f'generaría doble documento (la sincronización lo cierra sola).')
    if ec == 'PENDIENTE':
        fecha = pedido.fecha_sync_estado_canal
        cuando = timezone.localtime(fecha).strftime('%d/%m %H:%M') if fecha else '—'
        return ('El canal aún no confirma el pago de este pedido '
                f'(estado PENDIENTE en AllConnected, sync {cuando}). '
                'Usa "Traer pedidos" para re-sincronizar cuando se confirme.')
    return None


# Condición de "boleta emitida con la cabecera en cero": el DTE existe, pero el
# header quedó con 0 unidades y/o $0 aunque las líneas sí tengan datos. Son las
# boletas que salen del cuadre y que el SII recibe en 0 unidades.
# Ver `python manage.py diagnostico_pedidos_cantidad` (solo lectura) y
# docs/RESUMEN_AUDITORIA_ERP_2026-07-25.md.
_Q_DTE_CABECERA_CERO = (
    django_models.Q(dte__unidades_productos=0)
    | django_models.Q(dte__unidades_productos__isnull=True)
    | django_models.Q(dte__monto_con_iva=0)
    | django_models.Q(dte__monto_con_iva__isnull=True)
)


def _filtrar_pedidos_dte_cero(qs):
    """Pedidos FACTURADOS cuyo DTE quedó con unidades y/o monto en 0 en la cabecera."""
    return qs.filter(estado='FACTURADO', dte__isnull=False).filter(_Q_DTE_CABECERA_CERO)


def _parse_fecha_param(valor):
    """'YYYY-MM-DD' → date, o None si viene vacío/ inválido."""
    valor = (valor or '').strip()
    if not valor:
        return None
    from datetime import datetime as _dt
    try:
        return _dt.strptime(valor, '%Y-%m-%d').date()
    except ValueError:
        return None


def _aplicar_filtros_pedidos(qs, params):
    """Filtros de negocio del listado (compartidos con el export CSV).

    ``params`` es un ``QueryDict``/dict con: estado, canal, sub_estado, q,
    desde, hasta (por ``fecha_recepcion``) y problema (``dte_cero``).
    """
    estado = params.get('estado', '')
    if estado:
        qs = qs.filter(estado=estado)

    canal = params.get('canal', '')
    if canal:
        qs = qs.filter(canal_origen=canal)

    sub_estado = params.get('sub_estado', '')
    if sub_estado:
        qs = qs.filter(sub_estado=sub_estado)

    desde = _parse_fecha_param(params.get('desde', ''))
    if desde:
        qs = qs.filter(fecha_recepcion__date__gte=desde)
    hasta = _parse_fecha_param(params.get('hasta', ''))
    if hasta:
        qs = qs.filter(fecha_recepcion__date__lte=hasta)

    if params.get('problema', '') == 'dte_cero':
        qs = _filtrar_pedidos_dte_cero(qs)

    q = (params.get('q', '') or '').strip()
    if q:
        qs = qs.filter(
            django_models.Q(numero_ticket_rm__icontains=q)
            | django_models.Q(numero_pedido_canal__icontains=q)
            | django_models.Q(numero_pedido_origen__icontains=q)
            | django_models.Q(correlativo__icontains=q)
            | django_models.Q(cliente_nombre__icontains=q)
            | django_models.Q(cliente_documento__icontains=q)
        )
    return qs


def _panel_estado_sincronizacion(qs_empresa):
    """Estado de la sincronización con el hub (AllConnected), solo lectura.

    Devuelve un dict con:
      - ``configurado`` / ``host``: si el pull está habilitado y contra quién.
      - ``canales``: una fila por canal con pedidos de 24 h / 7 d, último
        recibido, pendientes y errores de las últimas 24 h.
      - ``errores``: los pedidos con error de las últimas 24 h (máx. 15).
      - ``total_errores_24h`` / ``recibidos_24h`` / ``ultimo_global``.

    Cuesta 2 consultas agregadas sobre el mismo scope de empresa del listado.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.db.models import Count, Max

    ahora = timezone.now()
    hace_24h = ahora - timedelta(hours=24)
    hace_7d = ahora - timedelta(days=7)

    base_url = (getattr(settings, 'ALLCONNECTED_API_BASE_URL', '') or '').strip()
    host = ''
    if base_url:
        try:
            from urllib.parse import urlparse
            host = urlparse(base_url).netloc or base_url
        except Exception:  # pragma: no cover
            host = base_url

    filas = list(
        qs_empresa.values('canal_origen')
        .annotate(
            recibidos_24h=Count('id', filter=django_models.Q(fecha_recepcion__gte=hace_24h)),
            recibidos_7d=Count('id', filter=django_models.Q(fecha_recepcion__gte=hace_7d)),
            pendientes=Count('id', filter=django_models.Q(estado='PENDIENTE')),
            errores_24h=Count(
                'id',
                filter=django_models.Q(estado='ERROR', fecha_recepcion__gte=hace_24h),
            ),
            ultimo=Max('fecha_recepcion'),
        )
        .order_by('canal_origen')
    )

    canales = []
    for fila in filas:
        ultimo = fila['ultimo']
        horas = None
        if ultimo:
            horas = (ahora - ultimo).total_seconds() / 3600.0
        if fila['errores_24h']:
            estado_salud = 'error'
        elif horas is None or horas > 48:
            estado_salud = 'inactivo'
        elif horas > 24:
            estado_salud = 'atrasado'
        else:
            estado_salud = 'ok'
        canales.append({
            'canal': fila['canal_origen'],
            'recibidos_24h': fila['recibidos_24h'],
            'recibidos_7d': fila['recibidos_7d'],
            'pendientes': fila['pendientes'],
            'errores_24h': fila['errores_24h'],
            'ultimo': ultimo,
            'horas_desde_ultimo': round(horas, 1) if horas is not None else None,
            'estado_salud': estado_salud,
        })

    errores = list(
        qs_empresa.filter(fecha_recepcion__gte=hace_24h)
        .filter(django_models.Q(estado='ERROR') | ~django_models.Q(error_detalle=''))
        .only(
            'id', 'numero_ticket_rm', 'numero_pedido_canal', 'canal_origen',
            'estado', 'sub_estado', 'error_detalle', 'fecha_recepcion',
        )
        .order_by('-fecha_recepcion')[:15]
    )

    return {
        'configurado': bool(base_url),
        'host': host,
        'canales': canales,
        'errores': errores,
        'total_errores_24h': sum(c['errores_24h'] for c in canales),
        'recibidos_24h': sum(c['recibidos_24h'] for c in canales),
        'ultimo_global': max(
            (c['ultimo'] for c in canales if c['ultimo']), default=None,
        ),
    }


# ---------------------------------------------------------------------------
# Vista de gestión — Operador lista y factura pedidos
# ---------------------------------------------------------------------------

class PedidosEcommerceListView(LoginRequiredMixin, ListView):
    """
    Lista de pedidos online pendientes de facturación.
    El operador puede marcarlos y abrir el flujo de DTE pre-llenado.
    """
    model = PedidoEcommerce
    template_name = 'app/ecommerce/pedidos_ecommerce_list.html'
    context_object_name = 'pedidos'
    paginate_by = 30

    def get_template_names(self):
        # Filtrado/paginación AJAX: devolver SOLO el parcial de la tabla
        # (sin layout) para que el front lo intercambie en #tabla-container.
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['app/ecommerce/_pedidos_tabla.html']
        return [self.template_name]

    def _scope_empresa(self, qs):
        """Scope de empresa (sin sucursal). Lo usan los paneles transversales
        (estado de sincronización y alerta de boletas en cero), que no deben
        depender de la sucursal activa."""
        return _scope_empresa_pedidos(qs, self.request.user)

    def _scope_empresa_sucursal(self, qs):
        """Aplica el scope de empresa del usuario + sucursal (sesión/explícita/'ver
        todas'). Compartido por el listado y por los KPIs (que cuentan sobre el
        mismo scope pero en todos los estados)."""
        return _scope_sucursal_pedidos(self._scope_empresa(qs), self.request)

    def get_queryset(self):
        qs = PedidoEcommerce.objects.select_related('sucursal', 'ticket', 'ticket__sucursal', 'dte').order_by('-fecha_recepcion')
        qs = self._scope_empresa_sucursal(qs)

        # `estado` conserva el default PENDIENTE del listado; el resto de los
        # filtros son compartidos con el export CSV.
        params = self.request.GET.copy()
        if 'estado' not in self.request.GET:
            params['estado'] = 'PENDIENTE'
        return _aplicar_filtros_pedidos(qs, params)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from app.models import CANAL_ECOMMERCE_CHOICES, ESTADO_PEDIDO_ECOMMERCE_CHOICES
        context['sucursales'] = Sucursal.objects.filter(activa=True).order_by('nombre')
        context['canales_choices'] = CANAL_ECOMMERCE_CHOICES
        context['estados_choices'] = ESTADO_PEDIDO_ECOMMERCE_CHOICES
        context['sub_estados_choices'] = SUB_ESTADO_PEDIDO_CHOICES
        context['sub_estado_filtro'] = self.request.GET.get('sub_estado', '')

        # KPIs del encabezado — solo en render completo (el parcial AJAX no los usa).
        # Cuentan sobre el MISMO scope de empresa + sucursal que el listado, en
        # todos los estados (1 query agregada).
        if self.request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            from django.db.models import Count, Sum
            agg = self._scope_empresa_sucursal(PedidoEcommerce.objects.all()).aggregate(
                pendientes=Count('id', filter=django_models.Q(estado='PENDIENTE')),
                facturados=Count('id', filter=django_models.Q(estado='FACTURADO')),
                cancelados=Count('id', filter=django_models.Q(estado='CANCELADO')),
                monto_pendiente=Sum('total', filter=django_models.Q(estado='PENDIENTE')),
            )
            context['kpi_pendientes'] = agg['pendientes'] or 0
            context['kpi_facturados'] = agg['facturados'] or 0
            context['kpi_cancelados'] = agg['cancelados'] or 0
            context['kpi_monto_pendiente'] = agg['monto_pendiente'] or 0

            # ── Panel: estado de la sincronización con AllConnected ──────────
            # Solo lectura, sobre el scope de EMPRESA (no de sucursal): si el
            # hub deja de mandar pedidos hay que verlo aunque el operador esté
            # mirando una sola tienda.
            qs_empresa = self._scope_empresa(PedidoEcommerce.objects.all())
            try:
                context['panel_sync'] = _panel_estado_sincronizacion(qs_empresa)
            except Exception:  # pragma: no cover - el panel nunca tumba la pantalla
                logger.exception('No se pudo construir el panel de sincronización ecommerce')
                context['panel_sync'] = None

        context['tipos_documento_choices'] = [
            ('BOLETA_ELECTRONICA', 'Boleta Electrónica'),
            ('BOLETA_PAPEL', 'Boleta Papel'),
            ('FACTURA_ELECTRONICA', 'Factura Electrónica'),
        ]
        # Vendedor fijo para ecommerce
        try:
            context['vendedor_ecommerce'] = Vendedor.objects.get(codigo_vendedor=1000)
        except Vendedor.DoesNotExist:
            context['vendedor_ecommerce'] = None
        # Mapa visual canal → método de pago
        context['metodo_pago_por_canal'] = {
            'SHOPIFY': 'Venta por Internet',
            'PARIS': 'Transferencia',
            'RIPLEY': 'Transferencia',
            'WALMART': 'Transferencia',
            'OTRO': 'Venta por Internet',
        }
        context['estado_filtro'] = self.request.GET.get('estado', 'PENDIENTE')
        context['canal_filtro'] = self.request.GET.get('canal', '')
        context['q'] = self.request.GET.get('q', '')
        context['desde_filtro'] = self.request.GET.get('desde', '')
        context['hasta_filtro'] = self.request.GET.get('hasta', '')
        context['problema_filtro'] = self.request.GET.get('problema', '')

        # ¿Hay algún filtro distinto del default? Lo usa el estado vacío de la
        # tabla para distinguir "no hay nada" de "no hay nada CON ESTOS filtros".
        context['filtros_activos'] = any([
            context['canal_filtro'],
            context['sub_estado_filtro'],
            context['q'],
            context['desde_filtro'],
            context['hasta_filtro'],
            context['problema_filtro'],
            self.request.GET.get('sucursal_id', ''),
            context['estado_filtro'] != 'PENDIENTE',
        ])

        # Sucursal de sesión activa
        session_suc_id = str(
            self.request.session.get('idSucursalActual')
            or self.request.session.get('sucursalActual')
            or ''
        )
        context['session_suc_id'] = session_suc_id
        context['session_suc_alias'] = self.request.session.get('alias', '')
        try:
            if session_suc_id:
                context['sucursal_sesion_obj'] = Sucursal.objects.get(pk=session_suc_id)
            else:
                context['sucursal_sesion_obj'] = None
        except Sucursal.DoesNotExist:
            context['sucursal_sesion_obj'] = None

        # El filtro de sucursal activo es solo el explícito en GET
        context['sucursal_filtro'] = self.request.GET.get('sucursal_id', '')
        context['ver_todas'] = self.request.GET.get('ver_todas', '')

        # ── Chequeo de stock por pedido contra la sucursal de sesión ──────────
        # Solo se ejecuta cuando hay sucursal activa y se están viendo PENDIENTES.
        # Construye un set con los IDs de pedidos que tienen al menos un ítem
        # sin stock o con stock insuficiente (stock < cantidad pedida).
        pedidos_sin_stock_ids = set()
        sucursal_sesion = context.get('sucursal_sesion_obj')
        estado_filtro_actual = context.get('estado_filtro', 'PENDIENTE')
        if sucursal_sesion and estado_filtro_actual in ('PENDIENTE', ''):
            page_pedidos = context.get('pedidos', [])
            # Recopilar SKUs y cantidades requeridas por pedido
            # sku_reqs[sku_int] = {pedido_id: cantidad_max_requerida}
            sku_reqs = {}
            pt_reqs = {}
            for pedido in page_pedidos:
                for item in (pedido.items or []):
                    sku_raw = (item.get('sku') or '').strip()
                    cantidad = int(item.get('cantidad') or 1)
                    pt_override = item.get('producto_talla_id')
                    if pt_override:
                        pt_int = int(pt_override)
                        if pt_int not in pt_reqs:
                            pt_reqs[pt_int] = {}
                        pt_reqs[pt_int][pedido.id] = pt_reqs[pt_int].get(pedido.id, 0) + cantidad
                    elif sku_raw:
                        try:
                            sku_int = int(sku_raw)
                            if sku_int not in sku_reqs:
                                sku_reqs[sku_int] = {}
                            sku_reqs[sku_int][pedido.id] = sku_reqs[sku_int].get(pedido.id, 0) + cantidad
                        except (ValueError, TypeError):
                            pedidos_sin_stock_ids.add(pedido.id)

            # Consulta batch por SKU → obtener stock disponible
            if sku_reqs:
                stock_por_sku = dict(
                    Producto_Talla.objects.filter(
                        sku__in=list(sku_reqs.keys()),
                        producto__sucursal=sucursal_sesion,
                    ).values_list('sku', 'stock')
                )
                for sku_int, pids_cantidades in sku_reqs.items():
                    disp = stock_por_sku.get(sku_int, 0) or 0
                    for pid, cant in pids_cantidades.items():
                        if disp < cant:
                            pedidos_sin_stock_ids.add(pid)

            # Consulta batch por producto_talla_id override → obtener stock disponible
            if pt_reqs:
                stock_por_pt = dict(
                    Producto_Talla.objects.filter(
                        id__in=list(pt_reqs.keys()),
                        producto__sucursal=sucursal_sesion,
                    ).values_list('id', 'stock')
                )
                for pt_id, pids_cantidades in pt_reqs.items():
                    disp = stock_por_pt.get(pt_id, 0) or 0
                    for pid, cant in pids_cantidades.items():
                        if disp < cant:
                            pedidos_sin_stock_ids.add(pid)

        context['pedidos_sin_stock_ids'] = pedidos_sin_stock_ids

        # QZ Tray config para impresión térmica masiva
        from app.views_modulo_ventas import _get_qz_config
        suc_id = str(
            self.request.session.get('idSucursalActual')
            or self.request.session.get('sucursalActual')
            or ''
        )
        context['qz_config'] = _get_qz_config(suc_id)

        return context


@login_required
def pedido_ecommerce_detalle(request, pedido_id):
    """
    Detalle de un PedidoEcommerce con opción de marcarlo como facturado
    o vincularle un ticket/DTE existente.
    """
    pedido = get_object_or_404(
        PedidoEcommerce.objects.select_related('sucursal', 'ticket', 'dte'),
        id=pedido_id,
    )
    if request.method == 'POST':
        accion = request.POST.get('accion')
        sucursal_id = request.session.get('idSucursalActual')

        if accion == 'vincular_ticket':
            if not PermisoRol.tiene_permiso(request.user, 'ecommerce_pedidos_todos', 'puede_crear', sucursal_id=sucursal_id):
                messages.error(request, 'No tiene permiso para vincular tickets.')
                return redirect('pedido_ecommerce_detalle', pedido_id=pedido.id)
            ticket_id = request.POST.get('ticket_id', '').strip()
            if ticket_id:
                try:
                    ticket = Ticket.objects.get(id=ticket_id, sucursal=pedido.sucursal)
                    pedido.ticket = ticket
                    # Vincular también el DTE emitido desde ese ticket (mismo
                    # join sucursal+folio que usa la API de conciliación). Sin
                    # esto la boleta no cruza con el pedido en GET /api/ventas/
                    # ni en el webhook de facturación (queda sin numero_ticket_rm
                    # ni folio_despacho).
                    #
                    # Los folios son secuencias independientes POR TIPO de
                    # documento: en una sucursal coexisten BOLETA ELECTRONICA #N,
                    # FACTURA #N y NOTA DE CREDITO #N. Restringimos a los mismos
                    # tipos de venta que GET /api/ventas/ lista (y excluimos NC y
                    # RECHAZADO) para no enlazar el pedido a un documento ajeno.
                    if not pedido.dte_id and ticket.dte_generado and ticket.folio_dte:
                        pedido.dte = (
                            Dte.objects
                            .filter(
                                sucursal=ticket.sucursal,
                                numero_documento=ticket.folio_dte,
                                tipo_documento__in=TIPOS_VENTA_DTE,
                                tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
                                es_nota_credito=False,
                                descartado=False,
                            )
                            .exclude(estado_dte='RECHAZADO')
                            .order_by('-id')
                            .first()
                        )
                    pedido.estado = 'FACTURADO'
                    pedido.fecha_facturacion = timezone.now()
                    pedido.facturado_por = request.user
                    pedido.save(update_fields=['ticket', 'dte', 'estado', 'fecha_facturacion', 'facturado_por'])
                    messages.success(request, f'Pedido {pedido.numero_ticket_rm} vinculado al Ticket #{ticket_id}.')
                except Ticket.DoesNotExist:
                    messages.error(request, 'Ticket no encontrado en la sucursal del pedido.')
            else:
                messages.error(request, 'Ingresa un ID de Ticket.')

        elif accion == 'cancelar':
            if not PermisoRol.tiene_permiso(request.user, 'ecommerce_pedidos_todos', 'puede_eliminar', sucursal_id=sucursal_id):
                messages.error(request, 'No tiene permiso para cancelar pedidos.')
                return redirect('pedido_ecommerce_detalle', pedido_id=pedido.id)
            sub_est_ant = pedido.sub_estado
            pedido.estado = 'CANCELADO'
            pedido.sub_estado = 'CANCELADO_CLIENTE'
            pedido.facturado_por = request.user
            pedido.save(update_fields=['estado', 'sub_estado', 'facturado_por'])
            HistorialPedidoEcommerce.objects.create(
                pedido=pedido,
                estado_anterior='PENDIENTE',
                estado_nuevo='CANCELADO',
                sub_estado_anterior=sub_est_ant,
                sub_estado_nuevo='CANCELADO_CLIENTE',
                usuario=request.user,
                tipo_evento='CAMBIO_ESTADO',
                motivo='Cancelado manualmente por operador',
            )
            messages.warning(request, f'Pedido {pedido.numero_ticket_rm} cancelado.')
            return redirect('pedidos_ecommerce_list')

        return redirect('pedido_ecommerce_detalle', pedido_id=pedido.id)

    context = {
        'pedido': pedido,
        'items': pedido.items or [],
    }

    # Usar sucursal de sesión para validar stock; si no hay sesión, caer en pedido.sucursal
    sucursal_sesion, _ = _get_session_sucursal(request)
    sucursal_validacion = sucursal_sesion or pedido.sucursal
    items_validados = _validar_items_pedido(pedido, sucursal=sucursal_validacion)
    context['items_validados'] = items_validados
    context['puede_facturar'] = bool(pedido.items) and all(iv['encontrado'] for iv in items_validados)
    context['sucursal_sesion'] = sucursal_sesion
    context['sucursal_validacion'] = sucursal_validacion

    # Resumen de diferencia de precios canal vs RM
    total_canal = sum(
        (iv.get('precio_canal') or 0) * (iv.get('cantidad_pedida') or 1)
        for iv in items_validados if iv.get('precio_rm')
    )
    total_rm = sum(
        (iv.get('precio_rm') or 0) * (iv.get('cantidad_pedida') or 1)
        for iv in items_validados if iv.get('precio_rm')
    )
    context['total_precios_canal'] = total_canal
    context['total_precios_rm'] = total_rm
    context['diferencia_total_precios'] = total_rm - total_canal
    context['hay_diferencia_precios'] = any(iv.get('descuento_canal', 0) != 0 for iv in items_validados if iv.get('precio_rm'))
    context['hay_bajo_margen'] = any(iv.get('precio_clase') == 'bajo_margen' for iv in items_validados)

    # Sucursales activas para reasignación
    context['sucursales'] = Sucursal.objects.filter(activa=True).order_by('nombre')

    # QZ Tray config para impresión térmica
    from app.views_modulo_ventas import _get_qz_config
    suc_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    context['qz_config'] = _get_qz_config(suc_id)

    return render(request, 'app/ecommerce/pedido_ecommerce_detalle.html', context)


# ---------------------------------------------------------------------------
# API — Buscar productos para match manual de SKU
# ---------------------------------------------------------------------------

@login_required
def api_buscar_producto_match(request, pedido_id):
    """GET /app/ecommerce/pedidos/<id>/buscar-producto/?q=texto"""
    pedido = get_object_or_404(PedidoEcommerce, id=pedido_id)
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'resultados': []})

    # Usar sucursal de sesión; si no hay, caer en pedido.sucursal
    sucursal_sesion, _ = _get_session_sucursal(request)
    sucursal = sucursal_sesion or pedido.sucursal

    from django.db.models import Q
    qs = Producto_Talla.objects.filter(
        producto__sucursal=sucursal,
        stock__gt=0,          # solo productos con stock disponible
    ).select_related('producto').filter(
        Q(producto__articulo__icontains=q) |
        Q(producto__descripcion__icontains=q) |
        Q(sku__icontains=q)
    ).order_by('sku')[:20]
    return JsonResponse({'resultados': [
        {
            'id': pt.id,
            'sku': pt.sku,
            'nombre': pt.producto.descripcion or pt.producto.articulo if pt.producto else str(pt.sku),
            'stock': pt.stock,
        }
        for pt in qs
    ]})


@login_required
@csrf_exempt
def api_guardar_match_sku(request, pedido_id):
    """POST /app/ecommerce/pedidos/<id>/guardar-match/"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_editar')
    if denegado:
        return denegado
    pedido = get_object_or_404(PedidoEcommerce.objects.filter(estado='PENDIENTE'), id=pedido_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)
    item_idx = data.get('item_idx')
    producto_talla_id = data.get('producto_talla_id')
    if item_idx is None or producto_talla_id is None:
        return JsonResponse({'ok': False, 'error': 'item_idx y producto_talla_id requeridos'}, status=400)
    items = pedido.items or []
    if item_idx < 0 or item_idx >= len(items):
        return JsonResponse({'ok': False, 'error': 'item_idx fuera de rango'}, status=400)
    try:
        # Validar en sesión sucursal primero; caer en pedido.sucursal si no hay sesión
        sucursal_sesion, _ = _get_session_sucursal(request)
        sucursal = sucursal_sesion or pedido.sucursal
        pt = Producto_Talla.objects.select_related('producto').get(
            id=producto_talla_id, producto__sucursal=sucursal
        )
    except Producto_Talla.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Producto no encontrado en la sucursal'}, status=400)
    nombre_rm = pt.producto.descripcion or pt.producto.articulo if pt.producto else str(pt.sku)
    items[item_idx]['producto_talla_id'] = pt.id
    items[item_idx]['nombre_rm'] = nombre_rm
    pedido.items = items
    pedido.save(update_fields=['items'])
    return JsonResponse({'ok': True, 'producto_talla_id': pt.id, 'sku_rm': pt.sku, 'nombre_rm': nombre_rm, 'stock': pt.stock})


@login_required
@csrf_exempt
def api_facturar_pedido_individual(request, pedido_id):
    """POST /app/ecommerce/pedidos/<id>/facturar/"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_crear')
    if denegado:
        return denegado
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)
    tipo_documento = data.get('tipo_documento', 'BOLETA_ELECTRONICA').strip()

    # Sucursal activa de sesión (obligatoria para facturar)
    sucursal, err = _get_session_sucursal(request)
    if err:
        return err

    # Validar datos receptor para Factura Electrónica
    datos_receptor = data.get('datos_receptor') or {}
    if tipo_documento == 'FACTURA_ELECTRONICA':
        faltantes = []
        for campo in ('rut', 'razon_social', 'giro', 'direccion', 'comuna', 'ciudad'):
            if not str(datos_receptor.get(campo, '')).strip():
                faltantes.append(campo)
        if faltantes:
            return JsonResponse({
                'ok': False,
                'error': f"Factura Electrónica requiere: {', '.join(faltantes)}.",
            }, status=400)
        import re
        rut_val = datos_receptor.get('rut', '').strip()
        if not re.match(r'^\d{7,8}-[\dkK]$', rut_val):
            return JsonResponse({
                'ok': False,
                'error': f"RUT con formato inválido: '{rut_val}'. Usa el formato 76123456-7.",
            }, status=400)

    try:
        vendedor = Vendedor.objects.get(codigo_vendedor=1000)
    except Vendedor.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Vendedor código 1000 no encontrado'}, status=400)
    from app.views import obtener_siguiente_correlativo
    from app.views_modulo_ventas import generar_dte_desde_ticket
    pedido = get_object_or_404(PedidoEcommerce.objects.filter(estado='PENDIENTE'), id=pedido_id)

    # El estado del CANAL manda: cancelado o sin pago confirmado no se factura.
    bloqueo_canal = _bloqueo_por_estado_canal(pedido)
    if bloqueo_canal:
        return JsonResponse({'ok': False, 'error': bloqueo_canal}, status=409)

    # Quiebre de stock reportado a AllConnected: no facturar hasta resolverlo.
    if pedido.sub_estado in SUB_ESTADOS_BLOQUEADOS_PICKING:
        return JsonResponse({'ok': False, 'error': _MSG_BLOQUEO_SIN_STOCK}, status=409)

    # Validar items contra la sucursal de sesión: todos deben existir Y tener stock suficiente
    items_val = _validar_items_pedido(pedido, sucursal=sucursal)
    sin_match = [iv for iv in items_val if not iv['encontrado']]
    if sin_match:
        detalles = []
        for iv in sin_match:
            sku_str = iv.get('sku', '?')
            cant = iv.get('cantidad_pedida', 1)
            disp = iv.get('stock_disponible', 0)
            if iv.get('stock_insuficiente'):
                detalles.append(f"SKU {sku_str} (necesita {cant}, disponible {disp})")
            elif iv.get('sin_stock'):
                detalles.append(f"SKU {sku_str} (sin stock)")
            else:
                detalles.append(f"SKU {sku_str} (no encontrado)")
        return JsonResponse({
            'ok': False,
            'error': f"Stock insuficiente: {', '.join(detalles)}. Usa el buscador para asignar productos con stock.",
        }, status=400)
    try:
        with transaction.atomic():
            correlativo = obtener_siguiente_correlativo(sucursal, 'TICKET')
            ticket = _crear_ticket_desde_pedido(
                pedido, vendedor, correlativo,
                responsable=request.user.username,
                sucursal=sucursal,
                datos_receptor=datos_receptor,
            )
            _crear_pago_ecommerce(ticket, pedido)
            ticket.estado = 'PAGADO'
            ticket.save(update_fields=['estado'])
            dte = generar_dte_desde_ticket(ticket, tipo_documento, request.user)
            # TXT Acepta canónico (idéntico a documentos), sobreescribe el bespoke.
            _regenerar_txt_acepta_ecommerce(dte)
            sub_estado_anterior = pedido.sub_estado
            pedido.ticket = ticket
            pedido.dte = dte
            pedido.estado = 'FACTURADO'
            pedido.sub_estado = 'FACTURADO_OK'
            pedido.sucursal = sucursal
            pedido.fecha_facturacion = timezone.now()
            pedido.facturado_por = request.user
            pedido.save(update_fields=['ticket', 'dte', 'estado', 'sub_estado', 'sucursal', 'fecha_facturacion', 'facturado_por'])

            # Historial de facturación
            HistorialPedidoEcommerce.objects.create(
                pedido=pedido,
                estado_anterior='PENDIENTE',
                estado_nuevo='FACTURADO',
                sub_estado_anterior=sub_estado_anterior,
                sub_estado_nuevo='FACTURADO_OK',
                sucursal_nueva=sucursal,
                usuario=request.user,
                tipo_evento='FACTURACION',
                motivo=f'Facturado individual como {tipo_documento}',
            )

            # Actualizar métrica con tiempo de procesamiento
            tiempo_min = None
            if pedido.fecha_recepcion:
                delta = timezone.now() - pedido.fecha_recepcion
                tiempo_min = int(delta.total_seconds() / 60)
            MetricaAsignacionPedido.objects.filter(pedido=pedido).update(
                tiempo_procesamiento_min=tiempo_min,
            )

        # Fidelización de la compra de la app, FUERA de la transacción de la
        # boleta (la boleta ya está emitida; los puntos son secundarios y no
        # deben tumbarla). Ambas operaciones son idempotentes:
        #   - conciliar: debita los puntos por el descuento real (cupón PTS-).
        #   - acumular: suma puntos por la parte en dinero (ticket.total ya viene
        #     neto del descuento, así que acumula solo sobre lo pagado).
        try:
            from .services import fidelizacion_service
            if (pedido.coupon_code or '').upper().startswith('PTS-'):
                fidelizacion_service.conciliar_reserva_por_pedido(
                    pedido, pedido.coupon_code, pedido.descuento)
            if pedido.from_app:
                fidelizacion_service.acumular_puntos_por_venta(ticket, usuario=request.user)
        except Exception:
            logger.exception('Fidelización app falló tras facturar pedido %s', pedido_id)

        return JsonResponse({
            'ok': True,
            'numero_ticket_rm': pedido.numero_ticket_rm,
            'ticket_correlativo': ticket.correlativo,
            'dte_id': dte.id,
            'dte_numero': dte.numero_documento,
            'dte_tipo': dte.tipo_documento,
            'archivo_txt': getattr(dte, 'archivo_txt_data', None),
            'txt_error': getattr(dte, '_txt_error', None),
        })
    except Exception as exc:
        logger.error('Error facturando pedido individual %s: %s', pedido_id, exc, exc_info=True)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Facturación masiva — crea Ticket + DTE por cada pedido seleccionado
# ---------------------------------------------------------------------------

def _distribuir_diferencia_en_lineas(lineas, diff, ticket, fallback_descripcion='AJUSTE'):
    """
    Reparte ``diff`` (CLP entero, > 0) ENTRE las líneas de producto reales en vez
    de crear una línea "AJUSTE" sin producto. Pondera por ``precio_rm × cantidad``
    (fallback: por cantidad). Mantiene ``precio × cantidad == subtotal`` por línea y
    garantiza que la suma de incrementos sea EXACTAMENTE ``diff`` (clave para que la
    boleta cuadre con el SII).

    ``lineas``: lista de dicts ``{'tp': Ticket_Productos, 'qty': int, 'precio_rm': int}``.
    Si no hay líneas o no se puede repartir, cae a una línea de reconciliación con
    ``fallback_descripcion`` (red de seguridad — no debería ocurrir en pedidos normales).
    """
    if not lineas:
        Ticket_Productos.objects.create(
            ProductoTalla=None, idTicket=ticket, stock=1, precio=diff,
            descuento_unitario=0, subtotal=diff, precio_original=diff,
            porcentaje_descuento=0, descripcion_linea=fallback_descripcion,
        )
        return

    pesos = [max(int(l.get('precio_rm') or 0), 0) * max(int(l['qty']), 1) for l in lineas]
    if sum(pesos) == 0:
        pesos = [max(int(l['qty']), 1) for l in lineas]   # fallback: por cantidad
    total_peso = sum(pesos) or len(lineas)

    # 1) Repartir diff por línea (mayor-resto) → los delta suman EXACTO diff.
    deltas = [diff * p // total_peso for p in pesos]
    resto = diff - sum(deltas)
    orden = sorted(range(len(lineas)), key=lambda i: pesos[i], reverse=True)
    for k in range(resto):
        deltas[orden[k % len(orden)]] += 1

    # 2) Aplicar deltas manteniendo precio×qty == subtotal. La indivisibilidad de
    #    qty > 1 deja "bolsa" de pesos que se reabsorbe en el paso 3.
    bolsa = 0
    for i, l in enumerate(lineas):
        tp = l['tp']
        qty = max(int(l['qty']), 1)
        nuevo_sub = (int(tp.precio) * qty) + deltas[i]
        precio_u = nuevo_sub // qty
        bolsa += nuevo_sub - precio_u * qty
        tp.precio = precio_u
        tp.precio_original = precio_u
        tp.subtotal = precio_u * qty
        tp.save(update_fields=['precio', 'precio_original', 'subtotal'])

    # 3) Reabsorber la bolsa (unos pocos pesos por redondeo de qty>1) en una línea
    #    de qty == 1; si no hay ninguna, una mini-línea residual (caso muy raro).
    if bolsa > 0:
        absorbibles = [l for l in lineas if int(l['qty']) == 1]
        if absorbibles:
            tp = max(absorbibles, key=lambda l: int(l['tp'].precio))['tp']
            tp.precio = int(tp.precio) + bolsa
            tp.precio_original = tp.precio
            tp.subtotal = tp.precio
            tp.save(update_fields=['precio', 'precio_original', 'subtotal'])
        else:
            Ticket_Productos.objects.create(
                ProductoTalla=None, idTicket=ticket, stock=1, precio=bolsa,
                descuento_unitario=0, subtotal=bolsa, precio_original=bolsa,
                porcentaje_descuento=0, descripcion_linea=fallback_descripcion,
            )


def _reducir_lineas_a_total(ticket, total_objetivo):
    """Recorta proporcionalmente las líneas del ticket para que sumen EXACTO
    ``total_objetivo`` (CLP entero, > 0). Espejo de
    ``_distribuir_diferencia_en_lineas`` para el caso inverso: los ítems del canal
    suman MÁS que el total realmente cobrado (ej. Walmart manda precios de lista
    pero el ``total`` del pedido es menor). Sin esto, el DTE se sobre-facturaba.

    Pondera el recorte por el subtotal actual de cada línea (``precio × qty``), así
    que las líneas en $0 no se tocan y NINGUNA queda en negativo. Mantiene
    ``precio × qty == subtotal`` por línea y reabsorbe la bolsa de redondeo (de
    qty > 1) para que la suma sea EXACTA = ``total_objetivo``.
    """
    tps = list(ticket.ticket_productos.all())
    pesos = [max(int(tp.precio) * max(int(tp.stock), 1), 0) for tp in tps]
    suma = sum(pesos)
    # Si las líneas ya cuadran (o están por debajo) o todas están en $0, no hay
    # nada que recortar de forma proporcional.
    if suma <= 0 or total_objetivo >= suma:
        return

    # 1) Subtotal objetivo por línea (mayor-resto) → suman EXACTO total_objetivo.
    objetivos = [total_objetivo * p // suma for p in pesos]
    resto = total_objetivo - sum(objetivos)
    orden = sorted(range(len(tps)), key=lambda i: pesos[i], reverse=True)
    for k in range(resto):
        objetivos[orden[k % len(orden)]] += 1

    # 2) Aplicar manteniendo precio × qty == subtotal. La indivisibilidad de
    #    qty > 1 deja "bolsa" de pesos que se reabsorbe en el paso 3.
    bolsa = 0
    for i, tp in enumerate(tps):
        qty = max(int(tp.stock), 1)
        nuevo_sub = objetivos[i]
        precio_u = nuevo_sub // qty
        bolsa += nuevo_sub - precio_u * qty
        tp.precio = precio_u
        tp.precio_original = precio_u
        tp.subtotal = precio_u * qty
        tp.save(update_fields=['precio', 'precio_original', 'subtotal'])

    # 3) Reabsorber la bolsa (unos pocos pesos por redondeo de qty > 1) en una
    #    línea de qty == 1; si no hay ninguna, en la de mayor precio (rompe
    #    levemente precio×qty pero conserva la suma total exacta).
    if bolsa > 0:
        unitarias = [tp for tp in tps if int(tp.stock) == 1 and int(tp.precio) > 0]
        objetivo_tp = (
            max(unitarias, key=lambda t: int(t.precio)) if unitarias
            else max(tps, key=lambda t: int(t.precio) * max(int(t.stock), 1))
        )
        objetivo_tp.subtotal = int(objetivo_tp.subtotal) + bolsa
        if int(objetivo_tp.stock) == 1:
            objetivo_tp.precio = int(objetivo_tp.precio) + bolsa
            objetivo_tp.precio_original = objetivo_tp.precio
            objetivo_tp.save(update_fields=['precio', 'precio_original', 'subtotal'])
        else:
            objetivo_tp.save(update_fields=['subtotal'])


def _crear_ticket_desde_pedido(pedido, vendedor, correlativo, responsable='ECOMMERCE', sucursal=None, datos_receptor=None):
    """
    Crea un Ticket en estado PENDIENTE con sus Ticket_Productos a partir
    de un PedidoEcommerce, descuenta stock via FIFO y registra movimientos.

    sucursal: sucursal donde crear el ticket (sesión activa). Si no se pasa,
              se usa pedido.sucursal como fallback.
    datos_receptor: dict con rut, razon_social, giro, direccion, comuna, ciudad
                    para Factura Electrónica.

    Devuelve el Ticket creado.
    """
    # Usar la consumir_stock_fifo del POS (views.py): crea el Movimientos_Producto
    # con tipo_movimiento='EGRESO' y concepto='VENTA_PUBLICO' explícitos y actualiza
    # Producto_Talla.stock atómicamente con F(). La variante de views_modulo_productos
    # delega en registrar_movimiento_producto sin pasar tipo_movimiento, lo que dejaba
    # el default 'INGRESO' del modelo y etiquetaba mal los egresos de internet.
    from app.views import consumir_stock_fifo

    sucursal = sucursal or pedido.sucursal
    total = int(pedido.total or 0)
    subtotal = int(pedido.subtotal or total)
    dr = datos_receptor or {}

    ticket = Ticket.objects.create(
        vendedor=vendedor,
        sucursal=sucursal,
        correlativo=correlativo,
        estado='PENDIENTE',
        subTotal=subtotal,
        descuento=int(pedido.descuento or 0),
        total=total,
        cliente_nombre=dr.get('razon_social') or pedido.cliente_nombre or '',
        cliente_rut=dr.get('rut') or pedido.cliente_documento or '',
        cliente_email=pedido.cliente_email or '',
        cliente_giro=dr.get('giro') or '',
        cliente_direccion=dr.get('direccion') or '',
        cliente_comuna=dr.get('comuna') or '',
        cliente_ciudad=dr.get('ciudad') or '',
        modulo_origen='ECOMMERCE',
        responsable='ECOMMERCE',
        observaciones=f'Pedido {pedido.canal_origen} #{pedido.numero_pedido_canal} | RM: {pedido.numero_ticket_rm}',
    )

    items = pedido.items or []
    total_lineas = 0
    lineas_producto = []  # líneas con producto real, para distribuir el AJUSTE
    for item in items:
        sku = (item.get('sku') or '').strip()
        nombre = (item.get('nombre') or '').strip()
        cantidad = int(item.get('cantidad') or 1)
        precio = int(item.get('precio_unitario') or 0)
        subtotal_linea = precio * cantidad
        total_lineas += subtotal_linea

        # Buscar producto: primero override guardado, luego por SKU
        producto_talla = None
        pt_id_override = item.get('producto_talla_id')
        if pt_id_override:
            try:
                producto_talla = Producto_Talla.objects.select_related('producto').get(
                    id=pt_id_override, producto__sucursal=sucursal
                )
            except Producto_Talla.DoesNotExist:
                producto_talla = None
        if not producto_talla and sku:
            try:
                sku_int = int(sku)
                producto_talla = Producto_Talla.objects.filter(
                    sku=sku_int,
                    producto__sucursal=sucursal,
                ).first()
            except (ValueError, TypeError):
                producto_talla = None

        tp_obj = Ticket_Productos.objects.create(
            ProductoTalla=producto_talla,
            idTicket=ticket,
            stock=cantidad,
            precio=precio,
            descuento_unitario=0,
            subtotal=subtotal_linea,
            precio_original=precio,
            porcentaje_descuento=0,
            descripcion_linea=nombre if not producto_talla else '',
        )
        if producto_talla:
            try:
                precio_rm_linea = int(producto_talla.producto.precioventa or 0)
            except (ValueError, TypeError):
                precio_rm_linea = 0
            lineas_producto.append({'tp': tp_obj, 'qty': cantidad, 'precio_rm': precio_rm_linea})

        # Descontar stock y registrar movimiento de EGRESO
        if producto_talla:
            try:
                costo_total_fifo, lotes_fifo = consumir_stock_fifo(
                    producto_talla=producto_talla,
                    cantidad_requerida=cantidad,
                    responsable=responsable,
                    ticket=ticket,
                    observaciones=f'Venta ecommerce {pedido.canal_origen} #{pedido.numero_pedido_canal} | RM: {pedido.numero_ticket_rm}',
                )
                # Trazabilidad: guardar de qué lotes (y DTE de compra) salió la línea.
                persistir_costeo_fifo(tp_obj, costo_total_fifo, lotes_fifo)
            except Exception as fifo_err:
                # Si FIFO falla (stock insuficiente en lotes), descuento manual
                logger.warning(
                    'FIFO falló para SKU %s en pedido %s: %s — descuento manual',
                    sku, pedido.numero_ticket_rm, fifo_err,
                )
                from app.models import Movimientos_Producto
                Movimientos_Producto.objects.create(
                    ticket=ticket,
                    ProductoTalla=producto_talla,
                    sucursal_origen=sucursal,
                    cantidad=-cantidad,
                    costo=producto_talla.producto.costo,
                    precio=precio,
                    sobreprecio=getattr(producto_talla.producto, 'sobreprecio', 0),
                    concepto='VENTA_PUBLICO',
                    tipo_movimiento='EGRESO',
                    responsable=responsable,
                    observaciones=f'Venta ecommerce {pedido.canal_origen} #{pedido.numero_pedido_canal} — FIFO no disponible',
                    referencia_externa=f'RM_{pedido.numero_ticket_rm}',
                    fecha=timezone.localdate(),
                    hora=timezone.localtime().time(),
                )
                producto_talla.stock = max(0, producto_talla.stock - cantidad)
                producto_talla.save(update_fields=['stock'])
                # Consumir los lotes que existan aunque el FIFO completo haya
                # fallado (evita dejar la capa de lotes inflada).
                try:
                    from app.services.inventario_service import consumir_lotes_fifo
                    consumir_lotes_fifo(producto_talla, cantidad, usar_lock=False)
                except Exception as e_lotes:
                    logger.warning(
                        'Ecommerce fallback: lotes FIFO no consumidos sku=%s cantidad=%s: %s',
                        sku, cantidad, e_lotes,
                    )
        else:
            logger.warning(
                'SKU %s del pedido %s no encontrado en sucursal %s — sin rebaje de stock',
                sku, pedido.numero_ticket_rm, sucursal.id,
            )

    # Costo de envío como línea de DESPACHO (afecto a IVA). El DTE se calcula
    # desde las líneas reales del ticket, así que sin esta línea el envío se
    # perdía y la boleta quedaba subfacturada (ej. pedido con envío $3.353
    # facturaba solo el producto). No mueve stock (es un servicio, sin SKU).
    costo_envio = int(pedido.costo_envio or 0)
    if costo_envio > 0:
        Ticket_Productos.objects.create(
            ProductoTalla=None,
            idTicket=ticket,
            stock=1,
            precio=costo_envio,
            descuento_unitario=0,
            subtotal=costo_envio,
            precio_original=costo_envio,
            porcentaje_descuento=0,
            descripcion_linea='DESPACHO',
        )
        total_lineas += costo_envio

    # ── Regla de oro de montos ──────────────────────────────────────────────
    # El `total` del payload es el GRAN TOTAL real que cobró el marketplace y es
    # AUTORITATIVO en todos los canales (coincide con el PDF de AllConnected). El
    # desglose (subtotal/descuento/impuestos/costo_envio) NO es homogéneo entre
    # marketplaces (Walmart subtotal neto, Shopify bruto, Paris solo total…), así
    # que NUNCA reconstruimos el total desde las partes.
    #
    # El DTE en RM se calcula desde las líneas del ticket, por lo que reconciliamos
    # las líneas para que sumen EXACTO el `total`: si ítems + despacho no lo cubren
    # (ej. Paris solo informa total, o redondeos), agregamos una línea de AJUSTE
    # por la diferencia. Así el DTE queda con monto_total = total y, vía
    # generar_dte_desde_ticket, neto = round(total/1.19), iva = total - neto.
    total_autoritativo = int(round(float(pedido.total or 0)))
    if total_autoritativo > 0:
        diff = total_autoritativo - total_lineas
        if diff > 0:
            # Falta monto para llegar al total del canal. En vez de una línea
            # "AJUSTE" sin producto, repartimos la diferencia ENTRE las líneas de
            # producto reales (ponderado por precio RM × cantidad), de modo que la
            # boleta muestre solo productos que suman el total. Mantiene
            # precio×cantidad == subtotal por línea y la suma exacta = total.
            _distribuir_diferencia_en_lineas(lineas_producto, diff, ticket)
            total_lineas += diff
        elif diff < 0:
            # Líneas > total del canal: el marketplace mandó precios de lista que
            # suman MÁS que lo realmente cobrado (caso típico de Walmart). El total
            # del canal es AUTORITATIVO (regla de oro), así que recortamos las
            # líneas proporcionalmente para que sumen EXACTO `total_autoritativo`,
            # sin emitir líneas negativas. Antes el DTE se quedaba en la suma de
            # líneas y la boleta salía sobre-facturada respecto al monto de la tabla.
            logger.warning(
                'Pedido %s: ítems+despacho (%s) exceden el total del canal (%s). '
                'Recortando líneas al total del canal.',
                pedido.numero_ticket_rm, total_lineas, total_autoritativo,
            )
            _reducir_lineas_a_total(ticket, total_autoritativo)
            total_lineas = total_autoritativo
        final_total = total_autoritativo
    else:
        # Sin total fiable (no debería pasar): caer a la suma de líneas.
        final_total = total_lineas

    # El descuento ya está incorporado en el precio cobrado de los ítems y en el
    # total; no se vuelve a aplicar (descuento=0) para no descontar dos veces.
    ticket.subTotal = final_total
    ticket.total = final_total
    ticket.descuento = 0
    ticket.save(update_fields=['subTotal', 'total', 'descuento'])

    return ticket


def _print_data_pedido(pedido, ticket, sucursal):
    """
    Datos que el ticket térmico de pedido ecommerce necesita para imprimir
    (los lee `_generarEscPosEcommerce` en _qz_tray_module.html). Sin esto el
    front imprimía total $0 y sin líneas. Las líneas salen del ticket recién
    creado por `_crear_ticket_desde_pedido` (ya traen precio/subtotal correctos).
    """
    empresa = getattr(sucursal, 'empresa', None)
    productos = []
    for tp in ticket.ticket_productos.select_related('ProductoTalla__producto').all():
        if tp.ProductoTalla and tp.ProductoTalla.producto:
            nombre = (
                tp.ProductoTalla.producto.descripcion
                or tp.ProductoTalla.producto.articulo
                or ''
            )
            sku = tp.ProductoTalla.sku
        else:
            nombre = tp.descripcion_linea or 'Ítem'
            sku = ''
        productos.append({
            'sku': sku,
            'nombre': nombre,
            'cantidad': tp.stock,
            'precio_unitario': tp.precio,
        })
    return {
        'canal_origen': pedido.canal_origen,
        'numero_pedido_canal': pedido.numero_pedido_canal or '',
        'cliente_documento': pedido.cliente_documento or '',
        'total': int(ticket.total or 0),
        'sucursal': {
            'empresa': empresa.nombre if empresa else '',
            'rut_empresa': empresa.rut if empresa else '',
            'alias': sucursal.alias or '',
            'direccion': sucursal.direccion or '',
        },
        'productos': productos,
    }


@login_required
@csrf_exempt
def facturar_ecommerce_masivo(request):
    """
    POST /api/ecommerce/facturar-masivo/

    Crea Ticket + DTE automáticamente para cada pedido ecommerce seleccionado.

    Requiere permiso puede_crear sobre ecommerce_pedidos_todos.
    - Sucursal: siempre la activa en sesión del operador
    - Vendedor: siempre codigo_vendedor=1000 (Venta Internet)
    - Método de pago: se deduce del canal_origen de cada pedido
    - Solo factura pedidos donde TODOS los items tienen stock en la sucursal de sesión

    Body JSON:
    {
        "pedido_ids": [1, 2, 3],
        "tipo_documento": "BOLETA_ELECTRONICA|BOLETA_PAPEL|FACTURA_ELECTRONICA"
    }
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Método no permitido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_crear')
    if denegado:
        return denegado

    # Sucursal activa de sesión (obligatoria)
    sucursal, err = _get_session_sucursal(request)
    if err:
        return err

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'Body JSON inválido'}, status=400)

    pedido_ids = data.get('pedido_ids', [])
    tipo_documento = data.get('tipo_documento', 'BOLETA_ELECTRONICA').strip()

    if not pedido_ids:
        return JsonResponse({'ok': False, 'error': 'Selecciona al menos un pedido'}, status=400)

    # Vendedor fijo: codigo_vendedor=1000 (Venta Internet)
    try:
        vendedor = Vendedor.objects.get(codigo_vendedor=1000)
    except Vendedor.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Vendedor código 1000 (Venta Internet) no encontrado'}, status=400)

    pedidos = PedidoEcommerce.objects.filter(id__in=pedido_ids, estado='PENDIENTE').select_related('sucursal')

    from app.views import obtener_siguiente_correlativo
    from app.views_modulo_ventas import generar_dte_desde_ticket

    resultados = []
    exitosos = 0
    fallidos = 0

    for pedido in pedidos:
        # El estado del CANAL manda: cancelado o sin pago confirmado no se factura.
        # Idem un quiebre de stock ya reportado a AllConnected.
        bloqueo_canal = (
            _bloqueo_por_estado_canal(pedido)
            or (_MSG_BLOQUEO_SIN_STOCK if pedido.sub_estado in SUB_ESTADOS_BLOQUEADOS_PICKING else None)
        )
        if bloqueo_canal:
            resultados.append({
                'pedido_id': pedido.id,
                'ok': False,
                'numero_ticket_rm': pedido.numero_ticket_rm,
                'cliente': pedido.cliente_nombre,
                'error': bloqueo_canal,
            })
            fallidos += 1
            continue

        # Validar que todos los items tengan stock suficiente en la sucursal de sesión
        items_val = _validar_items_pedido(pedido, sucursal=sucursal)
        sin_stock = [iv for iv in items_val if not iv['encontrado']]
        if sin_stock:
            detalles = []
            for iv in sin_stock:
                sku_str = iv.get('sku', '?')
                cant = iv.get('cantidad_pedida', 1)
                disp = iv.get('stock_disponible', 0)
                if iv.get('stock_insuficiente'):
                    detalles.append(f"SKU {sku_str} (necesita {cant}, disponible {disp})")
                elif iv.get('sin_stock'):
                    detalles.append(f"SKU {sku_str} (sin stock)")
                else:
                    detalles.append(f"SKU {sku_str} (no encontrado en {sucursal.alias})")
            resultados.append({
                'pedido_id': pedido.id,
                'ok': False,
                'numero_ticket_rm': pedido.numero_ticket_rm,
                'cliente': pedido.cliente_nombre,
                'error': f"Stock insuficiente: {', '.join(detalles)}",
            })
            fallidos += 1
            continue

        try:
            with transaction.atomic():
                correlativo = obtener_siguiente_correlativo(sucursal, 'TICKET')

                ticket = _crear_ticket_desde_pedido(
                    pedido, vendedor, correlativo,
                    responsable=request.user.username,
                    sucursal=sucursal,
                )

                _crear_pago_ecommerce(ticket, pedido)

                ticket.estado = 'PAGADO'
                ticket.save(update_fields=['estado'])

                dte = generar_dte_desde_ticket(ticket, tipo_documento, request.user)
                # TXT Acepta canónico (idéntico a documentos), sobreescribe el bespoke.
                _regenerar_txt_acepta_ecommerce(dte)

                sub_estado_ant = pedido.sub_estado
                pedido.ticket = ticket
                pedido.dte = dte
                pedido.estado = 'FACTURADO'
                pedido.sub_estado = 'FACTURADO_OK'
                pedido.sucursal = sucursal
                pedido.fecha_facturacion = timezone.now()
                pedido.facturado_por = request.user
                pedido.save(update_fields=['ticket', 'dte', 'estado', 'sub_estado', 'sucursal', 'fecha_facturacion', 'facturado_por'])

                HistorialPedidoEcommerce.objects.create(
                    pedido=pedido,
                    estado_anterior='PENDIENTE',
                    estado_nuevo='FACTURADO',
                    sub_estado_anterior=sub_estado_ant,
                    sub_estado_nuevo='FACTURADO_OK',
                    sucursal_nueva=sucursal,
                    usuario=request.user,
                    tipo_evento='FACTURACION',
                    motivo=f'Facturado masivo como {tipo_documento}',
                )

                tiempo_min = None
                if pedido.fecha_recepcion:
                    delta = timezone.now() - pedido.fecha_recepcion
                    tiempo_min = int(delta.total_seconds() / 60)
                MetricaAsignacionPedido.objects.filter(pedido=pedido).update(
                    tiempo_procesamiento_min=tiempo_min,
                )

            # Fidelización de la compra de la app (fuera de la transacción de la
            # boleta; idempotente). MISMO hook que la facturación individual: sin
            # esto, un pedido de app facturado por la vía masiva perdía para
            # siempre la acumulación de puntos por la parte en dinero.
            try:
                from .services import fidelizacion_service
                if (pedido.coupon_code or '').upper().startswith('PTS-'):
                    fidelizacion_service.conciliar_reserva_por_pedido(
                        pedido, pedido.coupon_code, pedido.descuento)
                if pedido.from_app:
                    fidelizacion_service.acumular_puntos_por_venta(
                        ticket, usuario=request.user)
            except Exception:
                logger.exception(
                    'Fidelización app falló al facturar (masivo) pedido %s', pedido.id)

            resultados.append({
                'pedido_id': pedido.id,
                'ok': True,
                'numero_ticket_rm': pedido.numero_ticket_rm,
                'ticket_correlativo': ticket.correlativo,
                'dte_id': dte.id,
                'dte_numero': dte.numero_documento,
                'dte_tipo': dte.tipo_documento,
                'cliente': pedido.cliente_nombre,
                'archivo_txt': getattr(dte, 'archivo_txt_data', None),
                'txt_error': getattr(dte, '_txt_error', None),
                'advertencias': [],
                # Datos para imprimir el ticket térmico (evita total $0 / sin líneas)
                **_print_data_pedido(pedido, ticket, sucursal),
            })
            exitosos += 1

        except Exception as exc:
            logger.error('Error facturando pedido %s: %s', pedido.id, exc, exc_info=True)
            resultados.append({
                'pedido_id': pedido.id,
                'ok': False,
                'numero_ticket_rm': pedido.numero_ticket_rm,
                'cliente': pedido.cliente_nombre,
                'error': str(exc),
            })
            fallidos += 1

    # IDs que ya no eran PENDIENTE
    ids_procesados = {p.id for p in pedidos}
    for pid in pedido_ids:
        if pid not in ids_procesados:
            resultados.append({
                'pedido_id': pid,
                'ok': False,
                'error': 'Pedido no encontrado o ya facturado/cancelado',
            })
            fallidos += 1

    return JsonResponse({
        'ok': fallidos == 0,
        'total': len(pedido_ids),
        'exitosos': exitosos,
        'fallidos': fallidos,
        'resultados': resultados,
    })


# ---------------------------------------------------------------------------
# Regenerar / descargar TXT de un DTE ya emitido
# ---------------------------------------------------------------------------

def _regenerar_txt_acepta_ecommerce(dte):
    """Reescribe el atributo transitorio ``dte.archivo_txt_data`` usando el
    generador CANONICO (``construir_datos_txt_desde_dte``) — el mismo de
    /app/ventas/documentos/ y del botón de re-descarga — para que el TXT que se
    auto-descarga al facturar un pedido coincida exactamente con ellos.

    NO toca la rama POS de ``generar_dte_desde_ticket`` (compartida con el POS):
    solo lee el DTE ya persistido y setea un atributo en memoria
    (``archivo_txt_data`` no es campo de BD), así que es seguro dentro del
    ``transaction.atomic()`` de la facturación. Si algo falla se conserva el
    valor previo y nunca rompe la emisión.
    """
    if not dte or getattr(dte, 'tipo_documento', '') == 'BOLETA PAPEL':
        return
    try:
        from app.views_modulo_documentos import construir_datos_txt_desde_dte, generar_txt_dte_acepta
        datos = construir_datos_txt_desde_dte(dte)
        contenido = generar_txt_dte_acepta(datos)
        nombre = f"{dte.tipo_documento.replace(' ', '_')}_{dte.numero_documento}.txt"
        dte.archivo_txt_data = {'contenido': contenido, 'nombre_archivo': nombre}
    except Exception as e:
        logger.error(
            'No se pudo regenerar TXT canonico ecommerce dte=%s: %s',
            getattr(dte, 'id', '?'), e, exc_info=True,
        )
        # Se conserva el archivo_txt_data previo: nunca rompemos la facturación.


@login_required
def descargar_txt_dte_ecommerce(request, dte_id):
    """
    GET /app/ecommerce/dte/<dte_id>/txt/
    Regenera y descarga el archivo TXT Acepta de un DTE ya emitido.
    """
    from django.http import HttpResponse
    from app.models import Dte
    from app.views_modulo_documentos import construir_datos_txt_desde_dte, generar_txt_dte_acepta

    # Misma barrera mínima que el resto del módulo: la URL /app/ecommerce/dte/
    # queda FUERA del mapa del middleware de permisos (que matchea por
    # /app/ecommerce/pedidos/), así que sin esto bastaba estar logueado.
    denegado = _verificar_permiso_ecommerce(request, 'puede_ver')
    if denegado:
        return denegado

    dte = get_object_or_404(
        Dte.objects.select_related('sucursal', 'emisor', 'receptor', 'vendedor'),
        id=dte_id,
    )

    # BOLETA PAPEL no genera TXT Acepta (paridad con el endpoint de documentos).
    if dte.tipo_documento == 'BOLETA PAPEL':
        return HttpResponse(
            'Las Boletas de Papel no generan archivo TXT',
            status=400, content_type='text/plain',
        )

    try:
        # Fuente UNICA de verdad: el MISMO generador canónico que usa
        # /app/ventas/documentos/. Lee monto_item / descuento_monto /
        # descuento_pct de Dte_Productos, normaliza el detalle a neto (factura) o
        # total (boleta) y arrastra los descuentos globales. Antes este endpoint
        # recalculaba precio/1.19 por línea e ignoraba monto_item y los
        # descuentos, por lo que los precios/descuentos del TXT no coincidían con
        # el DTE mostrado en documentos.
        datos = construir_datos_txt_desde_dte(dte)
        contenido_txt = generar_txt_dte_acepta(datos)
        nombre_archivo = f"{dte.tipo_documento.replace(' ', '_')}_{dte.numero_documento}.txt"

        response = HttpResponse(contenido_txt, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
        return response

    except Exception as e:
        logger.error('Error regenerando TXT para DTE %s: %s', dte_id, e, exc_info=True)
        return HttpResponse(f'Error generando TXT: {e}', status=500, content_type='text/plain')


@login_required
def descargar_txts_zip_ecommerce(request):
    """
    GET /app/ecommerce/dte/txts-zip/?ids=1,2,3

    Genera UN solo ZIP con los TXT Acepta de los DTEs indicados (mismo
    generador canónico que la descarga individual). Motivo: la facturación
    masiva disparaba una descarga por boleta y el navegador bloquea las
    descargas automáticas después de la primera (permiso "Descargas
    automáticas"), por lo que parte de los TXT nunca llegaba al disco del
    operador ni, por tanto, a Acepta. Una descarga única no tiene ese problema.

    Los DTEs que fallen al regenerar no rompen el ZIP: se listan en un
    _ERRORES.txt dentro del archivo. BOLETA PAPEL se excluye (no genera TXT).
    """
    from django.http import HttpResponse
    import io
    import zipfile
    from app.models import Dte
    from app.views_modulo_documentos import construir_datos_txt_desde_dte, generar_txt_dte_acepta

    denegado = _verificar_permiso_ecommerce(request, 'puede_ver')
    if denegado:
        return denegado

    MAX_ZIP_DTES = 500
    ids_raw = request.GET.get('ids', '')
    try:
        dte_ids = []
        for trozo in ids_raw.split(','):
            trozo = trozo.strip()
            if not trozo:
                continue
            valor = int(trozo)
            # Fuera del rango de BigAutoField: PostgreSQL lo rechazaría con un
            # 500 no controlado en el id__in; mejor 400 igual que 'abc'.
            if valor <= 0 or valor > 9223372036854775807:
                raise ValueError(trozo)
            dte_ids.append(valor)
    except (ValueError, TypeError):
        return HttpResponse('Parámetro ids inválido', status=400, content_type='text/plain')
    if not dte_ids:
        return HttpResponse('Indica al menos un DTE (?ids=1,2,3)', status=400, content_type='text/plain')

    # Dedupe ANTES de la cota (ids repetidos no deben consumir cupo) y aviso
    # explícito si se trunca: este endpoint existe para eliminar TXT perdidos
    # en silencio, no puede reintroducirlos por la puerta de atrás.
    dte_ids = list(dict.fromkeys(dte_ids))
    omitidos = max(0, len(dte_ids) - MAX_ZIP_DTES)
    dte_ids = dte_ids[:MAX_ZIP_DTES]

    dtes = (
        Dte.objects
        .filter(id__in=dte_ids)
        .exclude(tipo_documento='BOLETA PAPEL')
        .select_related('sucursal', 'emisor', 'receptor', 'vendedor')
    )

    buffer = io.BytesIO()
    errores = []
    if omitidos:
        logger.warning('ZIP TXT ecommerce: %s ids sobre la cota de %s — se omiten', omitidos, MAX_ZIP_DTES)
        errores.append(
            f'AVISO: se pidieron {len(dte_ids) + omitidos} DTEs pero este ZIP solo incluye '
            f'los primeros {MAX_ZIP_DTES}. Los {omitidos} restantes NO están acá: '
            f'descárgalos en otra tanda para no dejar boletas sin subir a Acepta.'
        )
    agregados = 0
    nombres_usados = set()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for dte in dtes:
            try:
                datos = construir_datos_txt_desde_dte(dte)
                contenido = generar_txt_dte_acepta(datos)
                nombre = f"{dte.tipo_documento.replace(' ', '_')}_{dte.numero_documento}.txt"
                # Mismo folio en otra sucursal/tipo normalizado: desambiguar.
                if nombre in nombres_usados:
                    nombre = f"{dte.tipo_documento.replace(' ', '_')}_{dte.numero_documento}_id{dte.id}.txt"
                nombres_usados.add(nombre)
                zf.writestr(nombre, contenido)
                agregados += 1
            except Exception as e:
                logger.error('ZIP TXT ecommerce: DTE %s falló: %s', dte.id, e, exc_info=True)
                errores.append(f"DTE id={dte.id} folio={dte.numero_documento}: {e}")
        if errores:
            zf.writestr('_ERRORES.txt', '\n'.join(errores))

    if agregados == 0:
        return HttpResponse(
            'Ningún TXT pudo generarse para los DTEs indicados.',
            status=404 if not errores else 500,
            content_type='text/plain',
        )

    nombre_zip = f"txt_acepta_{timezone.localdate():%Y%m%d}_{agregados}docs.zip"
    response = HttpResponse(buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{nombre_zip}"'
    return response


# ---------------------------------------------------------------------------
# API — Cambiar sub-estado de pedido
# ---------------------------------------------------------------------------

@login_required
@csrf_exempt
def api_cambiar_sub_estado(request, pedido_id):
    """POST /app/ecommerce/pedidos/<id>/sub-estado/"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_editar')
    if denegado:
        return denegado

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    nuevo_sub_estado = data.get('sub_estado', '').strip()
    motivo = data.get('motivo', '').strip()

    if not nuevo_sub_estado:
        return JsonResponse({'ok': False, 'error': 'sub_estado es obligatorio'}, status=400)

    pedido = get_object_or_404(PedidoEcommerce.objects.filter(estado='PENDIENTE'), id=pedido_id)

    if not pedido.puede_transicionar_sub_estado(nuevo_sub_estado):
        permitidos = TRANSICIONES_SUB_ESTADO.get(pedido.sub_estado, [])
        return JsonResponse({
            'ok': False,
            'error': f"No se puede cambiar de '{pedido.sub_estado}' a '{nuevo_sub_estado}'. Transiciones permitidas: {permitidos}",
        }, status=400)

    sub_estado_anterior = pedido.sub_estado
    pedido.sub_estado = nuevo_sub_estado
    ahora = timezone.now()
    if nuevo_sub_estado == 'ASIGNADO' and not pedido.fecha_asignacion:
        pedido.fecha_asignacion = ahora
        pedido.asignado_por = request.user
    # Timestamps de picking: solo la PRIMERA vez que se alcanza cada etapa
    # (retroceder y volver a avanzar no debe pisar la medición original).
    if nuevo_sub_estado == 'EN_PREPARACION' and not pedido.fecha_inicio_preparacion:
        pedido.fecha_inicio_preparacion = ahora
    if nuevo_sub_estado == 'LISTO_DESPACHO' and not pedido.fecha_listo_despacho:
        pedido.fecha_listo_despacho = ahora
    pedido.save(update_fields=[
        'sub_estado', 'fecha_asignacion', 'asignado_por',
        'fecha_inicio_preparacion', 'fecha_listo_despacho',
    ])

    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        sub_estado_anterior=sub_estado_anterior,
        sub_estado_nuevo=nuevo_sub_estado,
        usuario=request.user,
        tipo_evento='CAMBIO_ESTADO',
        motivo=motivo or f'Sub-estado cambiado a {nuevo_sub_estado}',
    )

    return JsonResponse({
        'ok': True,
        'sub_estado': pedido.sub_estado,
        'sub_estado_anterior': sub_estado_anterior,
    })


# ---------------------------------------------------------------------------
# API — Imprimir guía de preparación (picking en tienda)
# ---------------------------------------------------------------------------

@login_required
@csrf_exempt
def api_imprimir_guia_preparacion(request, pedido_id):
    """POST /app/ecommerce/pedidos/<id>/imprimir-guia/

    Registra la impresión de la guía de preparación y, si el pedido estaba
    ASIGNADO, lo transiciona a EN_PREPARACION: imprimir la guía ES el inicio
    del picking, así el rastro sale gratis sin pasos extra para la tienda.
    Reimprimir es idempotente (no duplica la transición ni pisa la primera
    fecha). Devuelve el print_data que consume `imprimirConQZ` (modo
    ECOMMERCE con `es_guia`, que rotula "GUIA DE PREPARACION").
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_editar')
    if denegado:
        return denegado

    pedido = get_object_or_404(
        PedidoEcommerce.objects.select_related('sucursal', 'sucursal__empresa'),
        id=pedido_id, estado='PENDIENTE',
    )

    # Sin pago confirmado (o cancelado/despachado según el canal): imprimir la
    # guía sería sacar stock para una venta que no corresponde preparar.
    bloqueo_canal = _bloqueo_por_estado_canal(pedido)
    if bloqueo_canal:
        return JsonResponse({'ok': False, 'error': bloqueo_canal}, status=409)

    if pedido.sub_estado in SUB_ESTADOS_BLOQUEADOS_PICKING:
        return JsonResponse({'ok': False, 'error': _MSG_BLOQUEO_SIN_STOCK}, status=409)

    resultado = _registrar_guia_preparacion(pedido, request.user)
    return JsonResponse({'ok': True, **resultado})


def _registrar_guia_preparacion(pedido, user):
    """Registra la impresión de la guía de un pedido y arma su print_data.

    Compartido por la impresión individual y la masiva por sucursal: sella la
    primera impresión, transiciona ASIGNADO→EN_PREPARACION (idempotente) y
    devuelve {sub_estado, transiciono, print_data} listo para `imprimirConQZ`.
    """
    ahora = timezone.now()
    update_fields = []
    if not pedido.fecha_impresion_guia:
        pedido.fecha_impresion_guia = ahora
        pedido.guia_impresa_por = user
        update_fields += ['fecha_impresion_guia', 'guia_impresa_por']

    transiciono = False
    sub_estado_anterior = pedido.sub_estado
    if pedido.sub_estado == 'ASIGNADO':
        pedido.sub_estado = 'EN_PREPARACION'
        if not pedido.fecha_inicio_preparacion:
            pedido.fecha_inicio_preparacion = ahora
        update_fields += ['sub_estado', 'fecha_inicio_preparacion']
        transiciono = True

    if update_fields:
        pedido.save(update_fields=update_fields)

    if transiciono:
        HistorialPedidoEcommerce.objects.create(
            pedido=pedido,
            estado_anterior=pedido.estado,
            estado_nuevo=pedido.estado,
            sub_estado_anterior=sub_estado_anterior,
            sub_estado_nuevo='EN_PREPARACION',
            usuario=user,
            tipo_evento='CAMBIO_ESTADO',
            motivo='Guía de preparación impresa (inicio de picking)',
        )

    empresa = getattr(pedido.sucursal, 'empresa', None)
    productos = []
    for item in (pedido.items or []):
        productos.append({
            'sku': str(item.get('sku') or ''),
            'nombre': item.get('nombre') or item.get('descripcion') or 'Ítem',
            'talla': str(item.get('talla') or ''),
            'cantidad': item.get('cantidad') or 1,
            'precio_unitario': item.get('precio_unitario') or 0,
        })

    return {
        'sub_estado': pedido.sub_estado,
        'transiciono': transiciono,
        'print_data': {
            'modulo_origen': 'ECOMMERCE',
            'es_guia': True,
            'numero_ticket_rm': pedido.numero_ticket_rm,
            'canal_origen': pedido.canal_origen,
            'numero_pedido_canal': pedido.numero_pedido_canal or '',
            'folio_despacho': pedido.correlativo or '',
            'cliente_nombre': pedido.cliente_nombre or '',
            'cliente_documento': pedido.cliente_documento or '',
            'direccion_envio': (pedido.direccion_envio or '')[:80],
            'total': int(pedido.total or 0),
            'fecha': timezone.localtime(ahora).strftime('%d/%m/%Y %H:%M'),
            'sucursal': {
                'empresa': (getattr(empresa, 'razon_social', '') or getattr(empresa, 'nombre', '') or '') if empresa else '',
                'rut_empresa': (getattr(empresa, 'rut', '') or '') if empresa else '',
                'alias': pedido.sucursal.nombre or pedido.sucursal.alias or '',
                'direccion': pedido.sucursal.direccion or '',
            },
            'productos': productos,
        },
    }


# Tope duro de la impresión masiva: evita mandar cientos de tickets a la
# térmica por un clic (si hay más, se avisa y se re-ejecuta).
MAX_GUIAS_MASIVAS = 60


@login_required
@csrf_exempt
def api_imprimir_guias_sucursal(request):
    """POST /app/ecommerce/pedidos/imprimir-guias-sucursal/

    "Imprimir TODO lo por preparar de mi sucursal" en un clic, sin seleccionar
    fila por fila (la selección por checkbox solo alcanza la página visible).

    Alcance: pedidos PENDIENTES de la sucursal ACTIVA en sesión con sub-estado
    ASIGNADO o EN_PREPARACION (los RECIBIDO no tienen stock confirmado y los
    LISTO_DESPACHO ya terminaron picking). Por defecto solo los que aún no
    tienen guía impresa — así el botón es re-ejecutable sin duplicar papel;
    body {"incluir_reimpresiones": true} imprime también los ya impresos.

    Registra cada guía con la MISMA lógica que la impresión individual
    (transición a EN_PREPARACION incluida) y devuelve los print_data para que
    el front los mande a QZ en secuencia.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_editar')
    if denegado:
        return denegado

    sucursal, err = _get_session_sucursal(request)
    if err:
        return err

    incluir_reimpresiones = False
    try:
        if request.body:
            incluir_reimpresiones = bool(json.loads(request.body).get('incluir_reimpresiones'))
    except (json.JSONDecodeError, ValueError):
        pass

    from app.services.allconnected_pedidos_service import (
        ESTADOS_CANAL_CANCELADOS, ESTADOS_CANAL_DESPACHADOS,
    )

    qs = (
        PedidoEcommerce.objects
        .select_related('sucursal', 'sucursal__empresa')
        .filter(
            sucursal=sucursal,
            estado='PENDIENTE',
            sub_estado__in=['ASIGNADO', 'EN_PREPARACION'],
        )
        # Bloqueados por el estado del canal quedan FUERA del lote: sin pago
        # confirmado (PENDIENTE) no se saca stock, y cancelados/despachados
        # los cierra la sincronización — imprimirles guía sería picking basura.
        .exclude(estado_canal__in=list(ESTADOS_CANAL_CANCELADOS)
                 + list(ESTADOS_CANAL_DESPACHADOS) + ['PENDIENTE'])
        .order_by('fecha_recepcion')  # los más antiguos primero
    )
    if not incluir_reimpresiones:
        qs = qs.filter(fecha_impresion_guia__isnull=True)

    pedidos = list(qs[:MAX_GUIAS_MASIVAS + 1])
    truncado = len(pedidos) > MAX_GUIAS_MASIVAS
    pedidos = pedidos[:MAX_GUIAS_MASIVAS]

    guias = []
    for pedido in pedidos:
        resultado = _registrar_guia_preparacion(pedido, request.user)
        guias.append({
            'pedido_id': pedido.id,
            'numero_ticket_rm': pedido.numero_ticket_rm,
            **resultado,
        })

    return JsonResponse({
        'ok': True,
        'total': len(guias),
        'truncado': truncado,
        'max': MAX_GUIAS_MASIVAS,
        'sucursal': sucursal.nombre or sucursal.alias or '',
        'guias': guias,
    })


# ---------------------------------------------------------------------------
# API — Quiebre de stock en tienda (marca en RM + aviso a AllConnected)
# ---------------------------------------------------------------------------

def _items_faltantes_pedido(pedido):
    """SKUs del pedido que hoy no se pueden servir desde su sucursal.

    Se manda a AllConnected como detalle de la incidencia para que central vea
    QUÉ falta sin tener que entrar a RM. Si la validación falla por lo que sea,
    devuelve lista vacía: el aviso vale igual.
    """
    try:
        validados = _validar_items_pedido(pedido, sucursal=pedido.sucursal)
    except Exception:  # pragma: no cover - el detalle nunca tumba el aviso
        logger.exception('No se pudo calcular el detalle de faltantes de %s',
                         pedido.numero_ticket_rm)
        return []
    faltantes = []
    for iv in validados:
        if iv.get('encontrado'):
            continue
        faltantes.append({
            'sku': str(iv.get('sku') or ''),
            'nombre': iv.get('nombre') or '',
            'cantidad_pedida': iv.get('cantidad_pedida') or 0,
            'stock_disponible': iv.get('stock_disponible') or 0,
        })
    return faltantes


@login_required
@csrf_exempt
def api_marcar_sin_stock(request, pedido_id):
    """POST /app/ecommerce/pedidos/<id>/sin-stock/

    La tienda fue a buscar el producto y NO estaba. El pedido:

      - queda en sub-estado SIN_STOCK (sigue PENDIENTE, pero sale del flujo de
        picking: no se le imprime guía ni se puede facturar);
      - se reporta a AllConnected, que abre su incidencia operativa SIN_STOCK
        (bloquea re-envío/impresión/etiqueta allá, deja bitácora "Problemas
        Inventario" y dispara alerta a central).

    El aviso es best-effort: si AllConnected no responde, el pedido IGUAL queda
    marcado acá con `sin_stock_avisado_ac=False` y el mismo endpoint reintenta
    el aviso si se vuelve a llamar sobre un pedido ya marcado.

    Body: {"motivo": "texto libre (opcional)"}
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_editar')
    if denegado:
        return denegado

    try:
        data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    motivo = (data.get('motivo') or '').strip()[:255]

    pedido = get_object_or_404(
        PedidoEcommerce.objects.select_related('sucursal'),
        id=pedido_id, estado='PENDIENTE',
    )

    ya_estaba = pedido.sub_estado == 'SIN_STOCK'
    if not ya_estaba and pedido.sub_estado not in ('ASIGNADO', 'EN_PREPARACION', 'RECIBIDO'):
        return JsonResponse({
            'ok': False,
            'error': f'No se puede marcar sin stock desde "{pedido.get_sub_estado_display()}".',
        }, status=400)

    sub_estado_anterior = pedido.sub_estado
    if not ya_estaba:
        pedido.sub_estado = 'SIN_STOCK'
        pedido.sin_stock_motivo = motivo or 'Sin stock en tienda'
        pedido.save(update_fields=['sub_estado', 'sin_stock_motivo'])
        HistorialPedidoEcommerce.objects.create(
            pedido=pedido,
            estado_anterior=pedido.estado,
            estado_nuevo=pedido.estado,
            sub_estado_anterior=sub_estado_anterior,
            sub_estado_nuevo='SIN_STOCK',
            usuario=request.user,
            tipo_evento='ERROR',
            motivo=f'Sin stock en tienda: {pedido.sin_stock_motivo}',
        )
    elif motivo:
        pedido.sin_stock_motivo = motivo
        pedido.save(update_fields=['sin_stock_motivo'])

    # Aviso a AllConnected (nunca lanza). Si ya estaba avisado, el endpoint
    # remoto es idempotente y responde `ya_existia`.
    from app.services.allconnected_pedidos_service import reportar_sin_stock
    aviso = reportar_sin_stock(
        pedido,
        motivo=pedido.sin_stock_motivo,
        items=_items_faltantes_pedido(pedido),
    )
    if bool(aviso.get('ok')) != pedido.sin_stock_avisado_ac:
        pedido.sin_stock_avisado_ac = bool(aviso.get('ok'))
        pedido.save(update_fields=['sin_stock_avisado_ac'])

    return JsonResponse({
        'ok': True,
        'ya_estaba': ya_estaba,
        'sub_estado': pedido.sub_estado,
        'sub_estado_display': pedido.get_sub_estado_display(),
        'motivo': pedido.sin_stock_motivo,
        'avisado_allconnected': pedido.sin_stock_avisado_ac,
        'aviso_detalle': aviso.get('detalle', ''),
    })


@login_required
@csrf_exempt
def api_reactivar_sin_stock(request, pedido_id):
    """POST /app/ecommerce/pedidos/<id>/reactivar/

    El producto apareció (o llegó por traspaso): el pedido vuelve a ASIGNADO y
    la tienda puede reimprimir la guía.

    NO cierra la incidencia en AllConnected: allá el cierre exige el permiso de
    resolución y es una decisión de central (puede haber avisado al cliente o
    reasignado). Se avisa por el historial y por la respuesta.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_editar')
    if denegado:
        return denegado

    try:
        data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        data = {}
    motivo = (data.get('motivo') or '').strip()[:255]

    pedido = get_object_or_404(
        PedidoEcommerce.objects.filter(estado='PENDIENTE', sub_estado='SIN_STOCK'),
        id=pedido_id,
    )

    pedido.sub_estado = 'ASIGNADO'
    pedido.sin_stock_motivo = ''
    pedido.sin_stock_avisado_ac = False
    pedido.save(update_fields=['sub_estado', 'sin_stock_motivo', 'sin_stock_avisado_ac'])

    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        sub_estado_anterior='SIN_STOCK',
        sub_estado_nuevo='ASIGNADO',
        usuario=request.user,
        tipo_evento='CAMBIO_ESTADO',
        motivo=motivo or 'Reactivado: el stock apareció en tienda',
    )

    return JsonResponse({
        'ok': True,
        'sub_estado': pedido.sub_estado,
        'sub_estado_display': pedido.get_sub_estado_display(),
        'aviso': ('El pedido volvió al flujo de picking. La incidencia en '
                  'AllConnected la cierra central cuando corresponda.'),
    })


# ---------------------------------------------------------------------------
# API — Reasignar pedido a otra sucursal
# ---------------------------------------------------------------------------

@login_required
@csrf_exempt
def api_reasignar_pedido(request, pedido_id):
    """POST /app/ecommerce/pedidos/<id>/reasignar/"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)
    denegado = _verificar_permiso_ecommerce(request, 'puede_editar')
    if denegado:
        return denegado

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    nueva_sucursal_id = data.get('sucursal_id')
    motivo = data.get('motivo', '').strip()

    if not nueva_sucursal_id:
        return JsonResponse({'ok': False, 'error': 'sucursal_id es obligatorio'}, status=400)

    try:
        nueva_sucursal = Sucursal.objects.select_related('empresa').get(id=nueva_sucursal_id, activa=True)
    except Sucursal.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Sucursal no encontrada o inactiva'}, status=400)

    pedido = get_object_or_404(
        PedidoEcommerce.objects.select_related('sucursal', 'sucursal__empresa').filter(estado='PENDIENTE'),
        id=pedido_id,
    )
    sucursal_anterior = pedido.sucursal

    if sucursal_anterior.id == nueva_sucursal.id:
        return JsonResponse({'ok': False, 'error': 'La sucursal destino es la misma que la actual'}, status=400)

    # Solo sucursales de la MISMA empresa: la boleta debe salir con el RUT del
    # canal que vendió. Cruzar de empresa emitiría el DTE con otro emisor.
    empresa_actual = getattr(sucursal_anterior, 'empresa', None)
    if empresa_actual and nueva_sucursal.empresa_id != empresa_actual.id:
        return JsonResponse({
            'ok': False,
            'error': (f'{nueva_sucursal.nombre or nueva_sucursal.alias} pertenece a otra empresa '
                      f'({nueva_sucursal.empresa.razon_social or nueva_sucursal.empresa.nombre}). '
                      f'Solo se puede reasignar dentro de {empresa_actual.razon_social or empresa_actual.nombre}.'),
        }, status=400)

    # Validar stock en nueva sucursal
    items_val = _validar_items_pedido(pedido, sucursal=nueva_sucursal)
    todos_con_stock = all(iv['encontrado'] for iv in items_val) if items_val else False
    items_sin = sum(1 for iv in items_val if not iv['encontrado'])

    sub_estado_anterior = pedido.sub_estado
    pedido.sucursal = nueva_sucursal
    pedido.sub_estado = 'ASIGNADO' if todos_con_stock else 'RECIBIDO'
    pedido.fecha_asignacion = timezone.now()
    pedido.asignado_por = request.user
    # Reasignar es una de las formas de resolver el quiebre: el pedido sale de
    # SIN_STOCK. (La incidencia en AllConnected la cierra central.)
    pedido.sin_stock_motivo = ''
    pedido.sin_stock_avisado_ac = False
    pedido.save(update_fields=['sucursal', 'sub_estado', 'fecha_asignacion', 'asignado_por',
                               'sin_stock_motivo', 'sin_stock_avisado_ac'])

    HistorialPedidoEcommerce.objects.create(
        pedido=pedido,
        estado_anterior=pedido.estado,
        estado_nuevo=pedido.estado,
        sub_estado_anterior=sub_estado_anterior,
        sub_estado_nuevo=pedido.sub_estado,
        sucursal_anterior=sucursal_anterior,
        sucursal_nueva=nueva_sucursal,
        usuario=request.user,
        tipo_evento='REASIGNACION',
        motivo=motivo or 'Reasignación manual',
    )

    MetricaAsignacionPedido.objects.create(
        pedido=pedido,
        sucursal_asignada=nueva_sucursal,
        fue_reasignado=True,
        motivo_reasignacion='MANUAL',
        todos_items_con_stock=todos_con_stock,
        items_sin_stock=items_sin,
    )

    return JsonResponse({
        'ok': True,
        'sucursal_nueva': {'id': nueva_sucursal.id, 'nombre': nueva_sucursal.nombre or nueva_sucursal.alias},
        'sub_estado': pedido.sub_estado,
        'todos_items_con_stock': todos_con_stock,
        'items_sin_stock': items_sin,
    })


# ---------------------------------------------------------------------------
# API — Sugerir mejor sucursal para un pedido
# ---------------------------------------------------------------------------

@login_required
def api_sugerir_sucursal(request, pedido_id):
    """GET /app/ecommerce/pedidos/<id>/sugerir-sucursal/"""
    pedido = get_object_or_404(
        PedidoEcommerce.objects.select_related('sucursal', 'sucursal__empresa').filter(estado='PENDIENTE'),
        id=pedido_id,
    )

    # Solo sucursales de la MISMA empresa del pedido: cruzar de empresa
    # emitiría la boleta con otro RUT (mismo guard que api_reasignar_pedido).
    sucursales = Sucursal.objects.filter(activa=True).order_by('nombre')
    empresa_pedido = getattr(pedido.sucursal, 'empresa', None)
    if empresa_pedido:
        sucursales = sucursales.filter(empresa=empresa_pedido)

    # Filtrar por empresa del usuario si no es admin
    user = request.user
    if getattr(user, 'rol', '') != 'administrador':
        try:
            from app.models import EmpresaUser
            eu = EmpresaUser.objects.filter(user=user).select_related('empresa').first()
            if eu and eu.empresa:
                sucursales = sucursales.filter(empresa=eu.empresa)
        except Exception:
            pass

    ranking = []
    for suc in sucursales:
        items_val = _validar_items_pedido(pedido, sucursal=suc)
        total_items = len(items_val)
        items_ok = sum(1 for iv in items_val if iv['encontrado'])
        items_sin = total_items - items_ok

        # Cobertura porcentual
        cobertura = (items_ok / total_items * 100) if total_items > 0 else 0

        # Profundidad de stock: mínimo ratio stock_disponible/cantidad
        ratios = []
        for iv in items_val:
            cant = iv.get('cantidad_pedida', 1)
            disp = iv.get('stock_disponible', 0)
            if cant > 0:
                ratios.append(disp / cant)
        min_ratio = min(ratios) if ratios else 0

        # Carga de trabajo: pedidos PENDIENTE en esta sucursal
        carga = PedidoEcommerce.objects.filter(sucursal=suc, estado='PENDIENTE').count()

        # Score
        score = cobertura + min(min_ratio, 5) * 10 - carga * 2

        ranking.append({
            'sucursal_id': suc.id,
            'nombre': suc.nombre or suc.alias,
            'items_con_stock': items_ok,
            'items_sin_stock': items_sin,
            'total_items': total_items,
            'cobertura_pct': round(cobertura, 1),
            'profundidad_stock': round(min_ratio, 2),
            'carga_pendientes': carga,
            'score': round(score, 1),
            'es_actual': suc.id == pedido.sucursal_id,
        })

    ranking.sort(key=lambda x: x['score'], reverse=True)

    return JsonResponse({
        'ok': True,
        'pedido_id': pedido.id,
        'ranking': ranking,
    })


# ---------------------------------------------------------------------------
# API — Distribución masiva de pedidos
# ---------------------------------------------------------------------------

@login_required
@csrf_exempt
def api_distribuir_pedidos(request):
    """POST /app/ecommerce/pedidos/distribuir/"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST requerido'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'JSON inválido'}, status=400)

    pedido_ids = data.get('pedido_ids', [])
    estrategia = data.get('estrategia', 'stock')
    sucursal_manual_id = data.get('sucursal_id')

    if not pedido_ids:
        return JsonResponse({'ok': False, 'error': 'Selecciona al menos un pedido'}, status=400)

    pedidos = PedidoEcommerce.objects.filter(id__in=pedido_ids, estado='PENDIENTE').select_related('sucursal')

    # Obtener sucursales activas
    sucursales = list(Sucursal.objects.filter(activa=True))
    user = request.user
    if getattr(user, 'rol', '') != 'administrador':
        try:
            from app.models import EmpresaUser
            eu = EmpresaUser.objects.filter(user=user).select_related('empresa').first()
            if eu and eu.empresa:
                sucursales = [s for s in sucursales if s.empresa_id == eu.empresa_id]
        except Exception:
            pass

    resultados = []

    for pedido in pedidos:
        mejor_sucursal = None
        motivo_r = 'DISTRIBUCION_AUTO'

        if estrategia == 'manual':
            if not sucursal_manual_id:
                resultados.append({'pedido_id': pedido.id, 'ok': False, 'error': 'sucursal_id requerido para estrategia manual'})
                continue
            try:
                mejor_sucursal = Sucursal.objects.get(id=sucursal_manual_id, activa=True)
            except Sucursal.DoesNotExist:
                resultados.append({'pedido_id': pedido.id, 'ok': False, 'error': 'Sucursal no encontrada'})
                continue
            motivo_r = 'MANUAL'

        elif estrategia == 'stock':
            mejor_score = -999
            for suc in sucursales:
                items_val = _validar_items_pedido(pedido, sucursal=suc)
                total_items = len(items_val)
                items_ok = sum(1 for iv in items_val if iv['encontrado'])
                cobertura = (items_ok / total_items * 100) if total_items > 0 else 0
                ratios = []
                for iv in items_val:
                    cant = iv.get('cantidad_pedida', 1)
                    disp = iv.get('stock_disponible', 0)
                    if cant > 0:
                        ratios.append(disp / cant)
                min_ratio = min(ratios) if ratios else 0
                carga = PedidoEcommerce.objects.filter(sucursal=suc, estado='PENDIENTE').count()
                score = cobertura + min(min_ratio, 5) * 10 - carga * 2
                if score > mejor_score:
                    mejor_score = score
                    mejor_sucursal = suc

        elif estrategia == 'carga':
            # Round-robin: asignar a la sucursal con menos pendientes que tenga stock
            sucursales_con_stock = []
            for suc in sucursales:
                items_val = _validar_items_pedido(pedido, sucursal=suc)
                if all(iv['encontrado'] for iv in items_val):
                    carga = PedidoEcommerce.objects.filter(sucursal=suc, estado='PENDIENTE').count()
                    sucursales_con_stock.append((suc, carga))
            if sucursales_con_stock:
                sucursales_con_stock.sort(key=lambda x: x[1])
                mejor_sucursal = sucursales_con_stock[0][0]

        if not mejor_sucursal:
            resultados.append({
                'pedido_id': pedido.id,
                'ok': False,
                'numero_ticket_rm': pedido.numero_ticket_rm,
                'error': 'No se encontró sucursal con stock disponible',
            })
            continue

        if mejor_sucursal.id == pedido.sucursal_id:
            resultados.append({
                'pedido_id': pedido.id,
                'ok': True,
                'numero_ticket_rm': pedido.numero_ticket_rm,
                'sucursal': mejor_sucursal.nombre or mejor_sucursal.alias,
                'cambio': False,
            })
            continue

        sucursal_anterior = pedido.sucursal
        items_val = _validar_items_pedido(pedido, sucursal=mejor_sucursal)
        todos_con_stock = all(iv['encontrado'] for iv in items_val)
        items_sin = sum(1 for iv in items_val if not iv['encontrado'])

        sub_estado_anterior = pedido.sub_estado
        pedido.sucursal = mejor_sucursal
        pedido.sub_estado = 'ASIGNADO' if todos_con_stock else 'RECIBIDO'
        pedido.fecha_asignacion = timezone.now()
        pedido.asignado_por = request.user
        pedido.save(update_fields=['sucursal', 'sub_estado', 'fecha_asignacion', 'asignado_por'])

        HistorialPedidoEcommerce.objects.create(
            pedido=pedido,
            estado_anterior=pedido.estado,
            estado_nuevo=pedido.estado,
            sub_estado_anterior=sub_estado_anterior,
            sub_estado_nuevo=pedido.sub_estado,
            sucursal_anterior=sucursal_anterior,
            sucursal_nueva=mejor_sucursal,
            usuario=request.user,
            tipo_evento='REASIGNACION',
            motivo=f'Distribución automática ({estrategia})',
        )

        MetricaAsignacionPedido.objects.create(
            pedido=pedido,
            sucursal_asignada=mejor_sucursal,
            fue_reasignado=True,
            motivo_reasignacion=motivo_r,
            todos_items_con_stock=todos_con_stock,
            items_sin_stock=items_sin,
        )

        resultados.append({
            'pedido_id': pedido.id,
            'ok': True,
            'numero_ticket_rm': pedido.numero_ticket_rm,
            'sucursal': mejor_sucursal.nombre or mejor_sucursal.alias,
            'cambio': True,
            'todos_items_con_stock': todos_con_stock,
        })

    exitosos = sum(1 for r in resultados if r.get('ok'))
    fallidos = sum(1 for r in resultados if not r.get('ok'))

    return JsonResponse({
        'ok': fallidos == 0,
        'total': len(pedido_ids),
        'exitosos': exitosos,
        'fallidos': fallidos,
        'resultados': resultados,
    })


# ---------------------------------------------------------------------------
# API — Historial de un pedido
# ---------------------------------------------------------------------------

@login_required
def api_historial_pedido(request, pedido_id):
    """GET /app/ecommerce/pedidos/<id>/historial/"""
    pedido = get_object_or_404(PedidoEcommerce, id=pedido_id)
    historial = pedido.historial.select_related('usuario', 'sucursal_anterior', 'sucursal_nueva').order_by('-fecha')

    entries = []
    for h in historial[:50]:
        entries.append({
            'tipo_evento': h.tipo_evento,
            'estado_anterior': h.estado_anterior,
            'estado_nuevo': h.estado_nuevo,
            'sub_estado_anterior': h.sub_estado_anterior,
            'sub_estado_nuevo': h.sub_estado_nuevo,
            'sucursal_anterior': (h.sucursal_anterior.nombre or h.sucursal_anterior.alias) if h.sucursal_anterior else '',
            'sucursal_nueva': (h.sucursal_nueva.nombre or h.sucursal_nueva.alias) if h.sucursal_nueva else '',
            'usuario': h.usuario.username if h.usuario else '',
            'motivo': h.motivo,
            'fecha': h.fecha.strftime('%d/%m/%Y %H:%M'),
        })

    return JsonResponse({'ok': True, 'historial': entries})


# ---------------------------------------------------------------------------
# Dashboard — Métricas de asignación
# ---------------------------------------------------------------------------

@login_required
def ecommerce_dashboard_asignacion(request):
    """GET /app/ecommerce/dashboard-asignacion/"""
    from django.db.models import Count, Avg, Q, F
    from datetime import timedelta

    # Filtros
    dias = int(request.GET.get('dias', 30))
    canal = request.GET.get('canal', '')
    fecha_desde = timezone.now() - timedelta(days=dias)

    # Base queryset
    metricas_qs = MetricaAsignacionPedido.objects.filter(fecha__gte=fecha_desde)
    pedidos_qs = PedidoEcommerce.objects.filter(fecha_recepcion__gte=fecha_desde)

    if canal:
        pedidos_qs = pedidos_qs.filter(canal_origen=canal)
        metricas_qs = metricas_qs.filter(pedido__canal_origen=canal)

    # Filtrar por empresa del usuario
    user = request.user
    if getattr(user, 'rol', '') != 'administrador':
        try:
            from app.models import EmpresaUser
            eu = EmpresaUser.objects.filter(user=user).select_related('empresa').first()
            if eu and eu.empresa:
                rut = eu.empresa.rut or ''
                if rut:
                    pedidos_qs = pedidos_qs.filter(
                        django_models.Q(rut_empresa=rut) | django_models.Q(rut_empresa='')
                    )
                    metricas_qs = metricas_qs.filter(
                        django_models.Q(pedido__rut_empresa=rut) | django_models.Q(pedido__rut_empresa='')
                    )
        except Exception:
            pass

    # ── Tiempos de picking por sucursal (sobre PedidoEcommerce, no métricas):
    # T1 reacción (asignación→impresión guía), T2 picking (inicio→listo),
    # T3 espera factura (listo→facturación) + adopción de la guía.
    from django.db.models import DurationField, ExpressionWrapper

    def _dur(a, b):
        return ExpressionWrapper(F(a) - F(b), output_field=DurationField())

    def _min(td):
        return round(td.total_seconds() / 60) if td else None

    picking_por_suc = {
        fila['sucursal_id']: fila
        for fila in pedidos_qs.values('sucursal_id').annotate(
            total=Count('id'),
            con_guia=Count('id', filter=Q(fecha_impresion_guia__isnull=False)),
            listos=Count('id', filter=Q(fecha_listo_despacho__isnull=False)),
            t1=Avg(_dur('fecha_impresion_guia', 'fecha_asignacion'),
                   filter=Q(fecha_impresion_guia__isnull=False, fecha_asignacion__isnull=False)),
            t2=Avg(_dur('fecha_listo_despacho', 'fecha_inicio_preparacion'),
                   filter=Q(fecha_listo_despacho__isnull=False, fecha_inicio_preparacion__isnull=False)),
            t3=Avg(_dur('fecha_facturacion', 'fecha_listo_despacho'),
                   filter=Q(fecha_facturacion__isnull=False, fecha_listo_despacho__isnull=False)),
        )
    }

    # KPIs por sucursal
    sucursales_data = []
    sucursales = Sucursal.objects.filter(activa=True).order_by('nombre')
    for suc in sucursales:
        m_suc = metricas_qs.filter(sucursal_asignada=suc)
        total = m_suc.count()
        pk = picking_por_suc.get(suc.id)
        if total == 0 and not pk:
            continue
        reasignados = m_suc.filter(fue_reasignado=True).count()
        sin_stock = m_suc.filter(todos_items_con_stock=False).count()
        avg_tiempo = m_suc.filter(tiempo_procesamiento_min__isnull=False).aggregate(
            avg=Avg('tiempo_procesamiento_min')
        )['avg']

        pedidos_suc = pk['total'] if pk else 0
        con_guia = pk['con_guia'] if pk else 0
        listos = pk['listos'] if pk else 0
        sucursales_data.append({
            'sucursal_id': suc.id,
            'nombre': suc.nombre or suc.alias,
            'total_pedidos': total,
            'reasignados': reasignados,
            'tasa_reasignacion': round(reasignados / total * 100, 1) if total > 0 else 0,
            'sin_stock': sin_stock,
            'tasa_sin_stock': round(sin_stock / total * 100, 1) if total > 0 else 0,
            'tiempo_promedio_min': round(avg_tiempo or 0, 0),
            'alerta': (reasignados / total * 100) > 20 if total > 0 else False,
            # Picking en tienda
            'pedidos_periodo': pedidos_suc,
            'pct_con_guia': round(con_guia / pedidos_suc * 100, 1) if pedidos_suc else 0,
            'pct_listos': round(listos / pedidos_suc * 100, 1) if pedidos_suc else 0,
            't1_min': _min(pk['t1']) if pk else None,
            't2_min': _min(pk['t2']) if pk else None,
            't3_min': _min(pk['t3']) if pk else None,
        })

    # Ordenar por score (menos reasignaciones = mejor)
    sucursales_data.sort(key=lambda x: x['tasa_reasignacion'])

    # ── Atrasados AHORA (en vivo, no depende del rango de días): pedidos
    # PENDIENTES que llevan demasiado sin avanzar. SLA configurable por env.
    import os
    SLA_SIN_PREPARAR_H = int(os.environ.get('ECOM_SLA_PREPARAR_HORAS', '4'))
    SLA_SIN_LISTO_H = int(os.environ.get('ECOM_SLA_LISTO_HORAS', '8'))
    ahora = timezone.now()
    lim_prep = ahora - timedelta(hours=SLA_SIN_PREPARAR_H)
    lim_listo = ahora - timedelta(hours=SLA_SIN_LISTO_H)

    qs_pend = PedidoEcommerce.objects.filter(estado='PENDIENTE').select_related('sucursal')
    if canal:
        qs_pend = qs_pend.filter(canal_origen=canal)
    if getattr(user, 'rol', '') != 'administrador':
        try:
            from app.models import EmpresaUser
            eu = EmpresaUser.objects.filter(user=user).select_related('empresa').first()
            if eu and eu.empresa and (eu.empresa.rut or ''):
                qs_pend = qs_pend.filter(
                    django_models.Q(rut_empresa=eu.empresa.rut) | django_models.Q(rut_empresa='')
                )
        except Exception:
            pass

    atrasados = []
    qs_atrasados = qs_pend.filter(
        Q(sub_estado='ASIGNADO', fecha_asignacion__lt=lim_prep)
        | Q(sub_estado='EN_PREPARACION', fecha_inicio_preparacion__lt=lim_listo)
    ).order_by('fecha_recepcion')[:50]
    for p in qs_atrasados:
        ref = p.fecha_asignacion if p.sub_estado == 'ASIGNADO' else p.fecha_inicio_preparacion
        atrasados.append({
            'id': p.id,
            'numero_ticket_rm': p.numero_ticket_rm,
            'canal_origen': p.canal_origen,
            'cliente_nombre': p.cliente_nombre,
            'sucursal': p.sucursal.nombre or p.sucursal.alias if p.sucursal else '',
            'sub_estado': p.get_sub_estado_display(),
            'horas_estancado': round((ahora - ref).total_seconds() / 3600, 1) if ref else None,
        })

    # KPIs globales
    total_pedidos = pedidos_qs.count()
    facturados = pedidos_qs.filter(estado='FACTURADO').count()
    cancelados = pedidos_qs.filter(estado='CANCELADO').count()
    pendientes = pedidos_qs.filter(estado='PENDIENTE').count()

    context = {
        'sucursales_data': sucursales_data,
        'total_pedidos': total_pedidos,
        'facturados': facturados,
        'cancelados': cancelados,
        'pendientes': pendientes,
        'dias': dias,
        'canal_filtro': canal,
        'canales_choices': [('SHOPIFY', 'Shopify'), ('PARIS', 'Paris'), ('RIPLEY', 'Ripley'), ('WALMART', 'Walmart'), ('OTRO', 'Otro')],
        'atrasados': atrasados,
        'sla_preparar_h': SLA_SIN_PREPARAR_H,
        'sla_listo_h': SLA_SIN_LISTO_H,
    }
    return render(request, 'app/ecommerce/dashboard_asignacion.html', context)


# ---------------------------------------------------------------------------
# Export CSV de pedidos filtrados
# ---------------------------------------------------------------------------

@login_required
def exportar_pedidos_csv(request):
    """GET /app/ecommerce/pedidos/exportar-csv/"""
    import csv
    from django.http import HttpResponse

    qs = PedidoEcommerce.objects.select_related('sucursal', 'ticket', 'dte').order_by('-fecha_recepcion')

    # Mismo scope que el listado: empresa del usuario + sucursal (explícita,
    # de sesión o "ver todas"). Antes el CSV NO acotaba por empresa y exportaba
    # los pedidos de todo el holding.
    qs = _scope_empresa_pedidos(qs, request.user)
    qs = _scope_sucursal_pedidos(qs, request)
    qs = _aplicar_filtros_pedidos(qs, request.GET)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="pedidos_ecommerce.csv"'
    response.write('\ufeff')  # BOM for Excel UTF-8

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'N Ticket RM', 'Folio Despacho', 'N Pedido Canal', 'Canal', 'Cliente', 'RUT/Doc',
        'Sucursal', 'Total', 'Estado', 'Sub-estado', 'Fecha Recepcion',
        'Fecha Facturacion', 'Ticket #', 'DTE #',
        # Cabecera del DTE: permite detectar en Excel las boletas emitidas con
        # unidades y/o monto en 0 (ver alerta del listado).
        'DTE Unidades (cab.)', 'DTE Monto (cab.)',
        # Picking en tienda: línea de tiempo + duraciones por etapa (min)
        'Fecha Asignacion', 'Fecha Impresion Guia', 'Guia Impresa Por',
        'Fecha Inicio Prep.', 'Fecha Listo Despacho',
        'T1 Reaccion (min)', 'T2 Picking (min)', 'T3 Espera Factura (min)',
    ])

    def _f(dt):
        return dt.strftime('%d/%m/%Y %H:%M') if dt else ''

    for p in qs.select_related('guia_impresa_por')[:5000]:
        writer.writerow([
            p.numero_ticket_rm,
            p.correlativo,
            p.numero_pedido_canal,
            p.canal_origen,
            p.cliente_nombre,
            p.cliente_documento,
            p.sucursal.nombre or p.sucursal.alias if p.sucursal else '',
            int(p.total or 0),
            p.estado,
            p.sub_estado,
            _f(p.fecha_recepcion),
            _f(p.fecha_facturacion),
            p.ticket.correlativo if p.ticket else '',
            p.dte.numero_documento if p.dte else '',
            p.dte.unidades_productos if p.dte else '',
            int(p.dte.monto_con_iva or 0) if p.dte else '',
            _f(p.fecha_asignacion),
            _f(p.fecha_impresion_guia),
            (p.guia_impresa_por.username if p.guia_impresa_por else ''),
            _f(p.fecha_inicio_preparacion),
            _f(p.fecha_listo_despacho),
            p.minutos_reaccion if p.minutos_reaccion is not None else '',
            p.minutos_picking if p.minutos_picking is not None else '',
            p.minutos_espera_factura if p.minutos_espera_factura is not None else '',
        ])

    return response
