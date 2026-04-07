"""
Views Ecommerce
===============
API endpoint para recibir pedidos desde VicentAllEcommercesConected
y vista de gestión para facturarlos directamente.
"""
import json
import logging
import uuid
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
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
    Acepta header 'X-RetailMind-Key' o query param 'api_key'.
    Compara contra la variable de entorno RETAILMIND_API_KEY.
    """
    from django.conf import settings
    api_key_esperada = getattr(settings, 'RETAILMIND_API_KEY', None)
    if not api_key_esperada:
        return True  # Si no está configurada, no bloquear (compatibilidad)
    api_key_recibida = (
        request.headers.get('X-RetailMind-Key')
        or request.GET.get('api_key', '')
    )
    return api_key_recibida == api_key_esperada


def _generar_numero_ticket_rm():
    """Genera un número de ticket RM único: RM-XXXXXXXX."""
    base = uuid.uuid4().hex[:8].upper()
    while PedidoEcommerce.objects.filter(numero_ticket_rm=f'RM-{base}').exists():
        base = uuid.uuid4().hex[:8].upper()
    return f'RM-{base}'


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

    # Campos obligatorios
    numero_pedido_canal = data.get('numero_pedido_canal', '').strip()
    canal_origen = data.get('canal_origen', '').strip().upper()
    sucursal_id = data.get('sucursal_id')
    cliente_nombre = data.get('cliente_nombre', '').strip()

    if not numero_pedido_canal:
        return JsonResponse({'ok': False, 'error': 'numero_pedido_canal es obligatorio'}, status=400)
    if not canal_origen:
        return JsonResponse({'ok': False, 'error': 'canal_origen es obligatorio'}, status=400)
    if not sucursal_id:
        return JsonResponse({'ok': False, 'error': 'sucursal_id es obligatorio'}, status=400)
    if not cliente_nombre:
        return JsonResponse({'ok': False, 'error': 'cliente_nombre es obligatorio'}, status=400)

    try:
        sucursal = Sucursal.objects.get(id=sucursal_id)
    except Sucursal.DoesNotExist:
        return JsonResponse({'ok': False, 'error': f'Sucursal {sucursal_id} no encontrada'}, status=400)

    # Verificar si ya existe un pedido para este canal+número (idempotente)
    existente = PedidoEcommerce.objects.filter(
        numero_pedido_canal=numero_pedido_canal,
        canal_origen=canal_origen,
    ).first()
    if existente:
        return JsonResponse({
            'ok': True,
            'numero_ticket_rm': existente.numero_ticket_rm,
            'pedido_ecommerce_id': existente.id,
            'ya_existia': True,
        })

    try:
        pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm=_generar_numero_ticket_rm(),
            numero_pedido_canal=numero_pedido_canal,
            canal_origen=canal_origen,
            sucursal=sucursal,
            rut_empresa=data.get('rut_empresa', '') or '',
            cliente_nombre=cliente_nombre,
            cliente_email=data.get('cliente_email', ''),
            cliente_documento=data.get('cliente_documento', ''),
            subtotal=data.get('subtotal', 0),
            descuento=data.get('descuento', 0),
            costo_envio=data.get('costo_envio', 0),
            total=data.get('total', 0),
            items=data.get('items', []),
            direccion_envio=data.get('direccion_envio', ''),
        )
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)

    return JsonResponse({
        'ok': True,
        'numero_ticket_rm': pedido.numero_ticket_rm,
        'pedido_ecommerce_id': pedido.id,
        'ya_existia': False,
    }, status=201)


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

    return JsonResponse({
        'ok': True,
        'numero_ticket_rm': pedido.numero_ticket_rm,
        'estado': pedido.estado,
        'canal_origen': pedido.canal_origen,
        'cliente_nombre': pedido.cliente_nombre,
        'total': str(pedido.total),
        'ticket_id': pedido.ticket_id,
        'dte_id': pedido.dte_id,
    })


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

    def get_queryset(self):
        qs = PedidoEcommerce.objects.select_related('sucursal', 'ticket', 'ticket__sucursal', 'dte').order_by('-fecha_recepcion')

        # Filtrar por empresa del usuario (a través del rut_empresa del pedido)
        # Si el usuario tiene sucursal activa, filtra por el RUT de su empresa
        user = self.request.user
        if not user.is_superuser:
            try:
                from app.models import EmpresaUser
                empresa_user = EmpresaUser.objects.filter(user=user).select_related('empresa__sucursales_app').first()
                if empresa_user and empresa_user.empresa:
                    rut_empresa_usuario = empresa_user.empresa.rut or ''
                    if rut_empresa_usuario:
                        qs = qs.filter(
                            django_models.Q(rut_empresa=rut_empresa_usuario) | django_models.Q(rut_empresa='')
                        )
            except Exception:
                pass

        estado = self.request.GET.get('estado', 'PENDIENTE')
        if estado:
            qs = qs.filter(estado=estado)

        canal = self.request.GET.get('canal', '')
        if canal:
            qs = qs.filter(canal_origen=canal)

        # Si el usuario pasa filtro explícito de sucursal, aplicarlo
        sucursal_id = self.request.GET.get('sucursal_id', '')
        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)

        q = self.request.GET.get('q', '').strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(numero_ticket_rm__icontains=q) |
                Q(numero_pedido_canal__icontains=q) |
                Q(cliente_nombre__icontains=q) |
                Q(cliente_documento__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from app.models import CANAL_ECOMMERCE_CHOICES, ESTADO_PEDIDO_ECOMMERCE_CHOICES
        context['sucursales'] = Sucursal.objects.filter(activa=True).order_by('nombre')
        context['canales_choices'] = CANAL_ECOMMERCE_CHOICES
        context['estados_choices'] = ESTADO_PEDIDO_ECOMMERCE_CHOICES
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

        if accion == 'vincular_ticket':
            ticket_id = request.POST.get('ticket_id', '').strip()
            if ticket_id:
                try:
                    ticket = Ticket.objects.get(id=ticket_id, sucursal=pedido.sucursal)
                    pedido.ticket = ticket
                    pedido.estado = 'FACTURADO'
                    pedido.fecha_facturacion = timezone.now()
                    pedido.facturado_por = request.user
                    pedido.save(update_fields=['ticket', 'estado', 'fecha_facturacion', 'facturado_por'])
                    messages.success(request, f'Pedido {pedido.numero_ticket_rm} vinculado al Ticket #{ticket_id}.')
                except Ticket.DoesNotExist:
                    messages.error(request, 'Ticket no encontrado en la sucursal del pedido.')
            else:
                messages.error(request, 'Ingresa un ID de Ticket.')

        elif accion == 'cancelar':
            pedido.estado = 'CANCELADO'
            pedido.facturado_por = request.user
            pedido.save(update_fields=['estado', 'facturado_por'])
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
    METODO_PAGO_POR_CANAL = {
        'SHOPIFY': 'VENTA_INTERNET', 'PARIS': 'TRANSFERENCIA',
        'RIPLEY': 'TRANSFERENCIA', 'WALMART': 'TRANSFERENCIA', 'OTRO': 'VENTA_INTERNET',
    }
    from app.views import obtener_siguiente_correlativo
    from app.views_modulo_ventas import generar_dte_desde_ticket
    pedido = get_object_or_404(PedidoEcommerce.objects.filter(estado='PENDIENTE'), id=pedido_id)

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
            metodo_pago = METODO_PAGO_POR_CANAL.get(pedido.canal_origen, 'VENTA_INTERNET')
            TicketDetallePago.objects.create(ticket=ticket, metodo_pago=metodo_pago, monto=int(pedido.total or 0), notas=f'Pago {pedido.canal_origen}')
            ticket.estado = 'PAGADO'
            ticket.save(update_fields=['estado'])
            dte = generar_dte_desde_ticket(ticket, tipo_documento, request.user)
            pedido.ticket = ticket
            pedido.dte = dte
            pedido.estado = 'FACTURADO'
            pedido.sucursal = sucursal  # actualizar sucursal a la de sesión
            pedido.fecha_facturacion = timezone.now()
            pedido.facturado_por = request.user
            pedido.save(update_fields=['ticket', 'dte', 'estado', 'sucursal', 'fecha_facturacion', 'facturado_por'])
        return JsonResponse({
            'ok': True,
            'numero_ticket_rm': pedido.numero_ticket_rm,
            'ticket_correlativo': ticket.correlativo,
            'dte_id': dte.id,
            'dte_numero': dte.numero_documento,
            'dte_tipo': dte.tipo_documento,
            'archivo_txt': getattr(dte, 'archivo_txt_data', None),
        })
    except Exception as exc:
        logger.error('Error facturando pedido individual %s: %s', pedido_id, exc, exc_info=True)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Facturación masiva — crea Ticket + DTE por cada pedido seleccionado
# ---------------------------------------------------------------------------

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
    from app.views_modulo_productos import consumir_stock_fifo

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
    for item in items:
        sku = (item.get('sku') or '').strip()
        nombre = (item.get('nombre') or '').strip()
        cantidad = int(item.get('cantidad') or 1)
        precio = int(item.get('precio_unitario') or 0)
        subtotal_linea = precio * cantidad

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

        Ticket_Productos.objects.create(
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

        # Descontar stock y registrar movimiento de EGRESO
        if producto_talla:
            try:
                consumir_stock_fifo(
                    producto_talla=producto_talla,
                    cantidad_requerida=cantidad,
                    responsable=responsable,
                    ticket=ticket,
                    observaciones=f'Venta ecommerce {pedido.canal_origen} #{pedido.numero_pedido_canal} | RM: {pedido.numero_ticket_rm}',
                )
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
                    concepto='VENTA_DIRECTA',
                    tipo_movimiento='EGRESO',
                    responsable=responsable,
                    observaciones=f'Venta ecommerce {pedido.canal_origen} #{pedido.numero_pedido_canal} — FIFO no disponible',
                    referencia_externa=f'RM_{pedido.numero_ticket_rm}',
                    fecha=timezone.now().date(),
                    hora=timezone.now().time(),
                )
                producto_talla.stock = max(0, producto_talla.stock - cantidad)
                producto_talla.save(update_fields=['stock'])
        else:
            logger.warning(
                'SKU %s del pedido %s no encontrado en sucursal %s — sin rebaje de stock',
                sku, pedido.numero_ticket_rm, sucursal.id,
            )

    return ticket


@login_required
@csrf_exempt
def facturar_ecommerce_masivo(request):
    """
    POST /api/ecommerce/facturar-masivo/

    Crea Ticket + DTE automáticamente para cada pedido ecommerce seleccionado.
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

    # Mapa canal → método de pago
    METODO_PAGO_POR_CANAL = {
        'SHOPIFY':  'VENTA_INTERNET',
        'PARIS':    'TRANSFERENCIA',
        'RIPLEY':   'TRANSFERENCIA',
        'WALMART':  'TRANSFERENCIA',
        'OTRO':     'VENTA_INTERNET',
    }

    pedidos = PedidoEcommerce.objects.filter(id__in=pedido_ids, estado='PENDIENTE').select_related('sucursal')

    from app.views import obtener_siguiente_correlativo
    from app.views_modulo_ventas import generar_dte_desde_ticket

    resultados = []
    exitosos = 0
    fallidos = 0

    for pedido in pedidos:
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

                metodo_pago = METODO_PAGO_POR_CANAL.get(pedido.canal_origen, 'VENTA_INTERNET')
                TicketDetallePago.objects.create(
                    ticket=ticket,
                    metodo_pago=metodo_pago,
                    monto=int(pedido.total or 0),
                    notas=f'Pago marketplace {pedido.canal_origen}',
                )

                ticket.estado = 'PAGADO'
                ticket.save(update_fields=['estado'])

                dte = generar_dte_desde_ticket(ticket, tipo_documento, request.user)

                pedido.ticket = ticket
                pedido.dte = dte
                pedido.estado = 'FACTURADO'
                pedido.sucursal = sucursal  # actualizar a sucursal de sesión
                pedido.fecha_facturacion = timezone.now()
                pedido.facturado_por = request.user
                pedido.save(update_fields=['ticket', 'dte', 'estado', 'sucursal', 'fecha_facturacion', 'facturado_por'])

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
                'advertencias': [],
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

@login_required
def descargar_txt_dte_ecommerce(request, dte_id):
    """
    GET /app/ecommerce/dte/<dte_id>/txt/
    Regenera y descarga el archivo TXT Acepta de un DTE ya emitido.
    """
    from app.models import Dte
    dte = get_object_or_404(Dte.objects.select_related('sucursal', 'emisor', 'receptor', 'vendedor'), id=dte_id)

    try:
        from app.views_modulo_ventas import generar_dte_desde_ticket
        from app.views_modulo_documentos import generar_txt_dte_acepta, limpiar_texto
        from decimal import Decimal

        ticket = dte.ticket_set.first() or getattr(dte, 'vendedor_ticket', None)

        # Regenerar TXT a partir del DTE existente
        empresa = dte.emisor
        receptor = dte.receptor
        es_boleta = 'BOLETA' in dte.tipo_documento

        neto = dte.monto_neto
        total = dte.monto_con_iva
        iva = total - neto

        metodos_pago_info = []
        for pago in dte.dte_detalle_pagos.all():
            from app.models import METODO_PAGO_TICKET_CHOICES
            metodo_nombre = dict(METODO_PAGO_TICKET_CHOICES).get(pago.metodo_pago, pago.metodo_pago)
            metodos_pago_info.append(f"{metodo_nombre}: ${pago.monto:,}")
        metodos_pago_texto = ' | '.join(metodos_pago_info) if metodos_pago_info else 'VENTA INTERNET'

        productos_txt = []
        for dp in dte.dte_productos.all():
            if dp.productoTalla:
                sku = str(dp.productoTalla.sku)
                nombre = (dp.productoTalla.producto.descripcion or dp.productoTalla.producto.articulo) if dp.productoTalla.producto else sku
            else:
                sku = 'ITEM'
                nombre = dp.descripcion or 'Ítem ecommerce'
            productos_txt.append({
                'sku': sku,
                'nombre': nombre[:80],
                'cantidad': dp.stock,
                'precio_unitario': int(dp.precio),
                'total': int(dp.precio * dp.stock),
            })

        datos_txt = {
            'documento': {
                'tipo_documento': 39 if es_boleta else 33,
                'folio': dte.numero_documento,
                'fecha_emision': dte.fecha_emision.strftime('%Y-%m-%d'),
                'forma_pago': 1,
                'ind_servicio': 3,
                'timestamp': dte.fecha_emision.strftime('%Y-%m-%dT%H:%M:%S'),
            },
            'emisor': {
                'rut': empresa.rut,
                'razon_social': limpiar_texto(empresa.razon_social or empresa.nombre),
                'giro': limpiar_texto(empresa.giro or 'Sin giro'),
                'acteco': empresa.acteco or '',
                'direccion': limpiar_texto(empresa.direccion or ''),
                'comuna': limpiar_texto(empresa.comuna or ''),
                'ciudad': limpiar_texto(empresa.ciudad or ''),
                'codigo_vendedor': limpiar_texto(str(dte.vendedor.codigo_vendedor) if dte.vendedor else '1000'),
                'nombre_vendedor': limpiar_texto(dte.vendedor.nombre if dte.vendedor else 'Venta Internet'),
                'metodos_pago': limpiar_texto(metodos_pago_texto),
                'correlativo_ticket': dte.referencias or '',
                'telefono': empresa.contacto1 or '',
                'nombre_impresora_boleta': getattr(dte.sucursal, 'nombre_impresora_boleta', 'boleta') or 'boleta',
                'nombre_impresora_factura': getattr(dte.sucursal, 'nombre_impresora_factura', 'factura') or 'factura',
                'sucursal': limpiar_texto(dte.sucursal.alias if dte.sucursal else ''),
            },
            'receptor': {
                'rut': receptor.rut if receptor and not es_boleta else '66666666-6',
                'razon_social': limpiar_texto(receptor.razon_social if receptor and not es_boleta else 'CONSUMIDOR FINAL'),
                'giro': limpiar_texto(receptor.giro if receptor and not es_boleta else ''),
                'direccion': limpiar_texto(receptor.direccion if receptor and not es_boleta else ''),
                'comuna': limpiar_texto(receptor.comuna if receptor and not es_boleta else ''),
                'ciudad': limpiar_texto(receptor.ciudad if receptor and not es_boleta else ''),
            },
            'totales': {
                'monto_neto': int(neto),
                'monto_exento': 0,
                'tasa_iva': 19,
                'iva': int(iva),
                'monto_total': int(total),
            },
            'detalle': [
                {
                    'codigo': limpiar_texto(str(p['sku'])[:35]),
                    'sku': limpiar_texto(str(p['sku'])),
                    'nombre': limpiar_texto(p['nombre']),
                    'descripcion': '',
                    'cantidad': p['cantidad'],
                    'unidad': 'UN',
                    'precio_unitario': p['precio_unitario'],
                    'monto_item': p['total'],
                }
                for p in productos_txt
            ],
            'observaciones': '',
            'observaciones_adicionales': '',
        }

        contenido_txt = generar_txt_dte_acepta(datos_txt)
        nombre_archivo = f"{dte.tipo_documento.replace(' ', '_')}_{dte.numero_documento}.txt"

        from django.http import HttpResponse
        response = HttpResponse(contenido_txt, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
        return response

    except Exception as e:
        logger.error('Error regenerando TXT para DTE %s: %s', dte_id, e, exc_info=True)
        from django.http import HttpResponse
        return HttpResponse(f'Error generando TXT: {e}', status=500, content_type='text/plain')
