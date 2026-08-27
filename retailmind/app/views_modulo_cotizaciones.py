"""
Vistas para el módulo de cotizaciones a empresas
"""

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, Count
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from datetime import datetime, timedelta, date
from decimal import Decimal
import json
import logging
from io import BytesIO

# ReportLab para generación de PDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

from .models import (
    Cotizacion_Empresa, Cotizacion_Empresa_Detalle, Cotizacion_Empresa_Detalle_SKU,
    Historial_Cotizacion, Empresa, Sucursal, Producto_Talla, Vendedor,
    Movimientos_Producto, Dte_Productos, PermisoRol,
)

logger = logging.getLogger('app')


def _esta_vencida(cotizacion, hoy=None):
    """¿La cotización ya pasó su fecha de validez?

    `Cotizacion_Empresa.estado` SOLO pasa a VENCIDA dentro de `save()`, y una
    cotización que nadie vuelve a guardar se queda en VIGENTE para siempre. En
    producción eso deja cotizaciones "vigentes" con 156 días de vencidas, el
    filtro "Vencida" sin resultados y el botón Facturar habilitado sobre
    documentos que el backend después rechaza. Esta función es la vigencia real,
    calculada por fecha y no por el campo guardado.
    """
    if cotizacion.facturada or cotizacion.estado in (
        Cotizacion_Empresa.ESTADO_ANULADA, Cotizacion_Empresa.ESTADO_FACTURADA,
    ):
        return False
    if not cotizacion.fecha_validez:
        return False
    return cotizacion.fecha_validez < (hoy or timezone.localdate())


def _estado_efectivo(cotizacion, hoy=None):
    """Estado que hay que MOSTRARLE al usuario (vigencia calculada por fecha)."""
    if _esta_vencida(cotizacion, hoy):
        return Cotizacion_Empresa.ESTADO_VENCIDA
    return cotizacion.estado


def _cuadratura_item(item):
    """(pendientes, despachadas_post_factura) del ítem, en UNIDADES.

    Espejo EXACTO en Python de
    `Cotizacion_Empresa_Detalle.unidades_pendientes_despacho`:

        pendientes = cantidad − cubiertas_al_facturar − despachadas_post_factura

    Se calcula acá, y no llamando a la property, porque la property dispara un
    aggregate por ítem (y el listado abría con cientos de round-trips contra la
    base remota). Trabaja sobre `skus_asociados` ya prefetcheado y sobre
    `producto_existente_id`, que es una columna del propio ítem: NO agrega ni
    una sola consulta.

    OJO: la versión anterior de este helper replicaba la fórmula VIEJA (si el
    ítem no "nacía pendiente" devolvía 0 sin mirar cuántas unidades respaldaban
    realmente los SKUs). Eso hacía que el listado y el detalle mostraran 0
    pendientes mientras el modelo — que es quien gobierna `asignar_sku_post_factura`
    y `validar_despacho_cotizacion` — veía saldo. Caso real: COT-202607-0001,
    línea de 5 uds con 1 sola respaldada → listado 0, modelo 4.
    """
    cantidad = item.cantidad or 0
    filas = list(item.skus_asociados.all())
    post_factura = sum((s.cantidad or 0) for s in filas if s.asignado_post_factura)

    if item.es_producto_pendiente or item.sku_asignado_post_factura:
        # `nacio_pendiente`: se facturó sin SKU, no salió nada con el ticket.
        cubiertas = 0
    else:
        previas = [s for s in filas if not s.asignado_post_factura]
        if previas:
            cubiertas = sum((s.cantidad or 0) for s in previas)
        else:
            # Enlace legacy (un solo SKU por línea, antes de que existiera
            # Cotizacion_Empresa_Detalle_SKU): se asume cobertura total, igual
            # que el modelo. Inventar pendientes sobre datos históricos
            # reabriría despachos ya cerrados.
            cubiertas = cantidad if item.producto_existente_id else 0

    return max(0, cantidad - cubiertas - post_factura), post_factura


def _cuadratura_despacho(cotizacion):
    """(facturadas, pendientes, despachadas_post_factura) en UNIDADES.

    Misma aritmética que las properties del modelo
    (`unidades_facturadas` / `unidades_pendientes_despacho`), pero resuelta en
    Python sobre los ítems ya prefetcheados (ver `_cuadratura_item`).
    """
    facturadas = 0
    pendientes = 0
    post_factura = 0
    for item in cotizacion.items.all():
        facturadas += item.cantidad or 0
        pend_item, post_item = _cuadratura_item(item)
        pendientes += pend_item
        post_factura += post_item
    return facturadas, pendientes, post_factura


def _resolver_skus_esperados(textos_sku, sucursal_id):
    """{texto_sku: {datos del Producto_Talla}} para los que existen de verdad.

    `Cotizacion_Empresa_Detalle.sku_producto_pendiente` es texto libre: se
    escribe a mano al cotizar y nadie lo cruza con el catálogo. Resultado: el
    operador del despacho diferido ve "SKU esperado: 40312" sin saber si ese SKU
    existe, y tiene que buscarlo a mano de nuevo.

    Resuelve TODOS los textos en una sola consulta (los ítems de una cotización
    son pocos, pero una query por ítem contra la base remota se nota) y solo
    devuelve los que pertenecen a la sucursal consultada — el stock es por
    sucursal, así que un SKU de otra no sirve para despachar.
    """
    from app.models import Producto_Talla as _PT

    numericos = {}
    for texto in textos_sku:
        limpio = (texto or '').strip()
        if limpio and limpio.isdigit():
            numericos[int(limpio)] = limpio

    if not numericos:
        return {}

    qs = (
        _PT.objects
        .filter(sku__in=list(numericos.keys()))
        .select_related('producto', 'producto__atributo1')
    )
    if sucursal_id:
        qs = qs.filter(producto__sucursal_id=int(sucursal_id))

    resultado = {}
    for pt in qs:
        texto = numericos.get(pt.sku)
        if not texto or texto in resultado:
            continue
        producto = pt.producto
        resultado[texto] = {
            'producto_talla_id': pt.id,
            'sku': str(pt.sku),
            'nombre': producto.articulo if producto else 'Sin nombre',
            'talla': pt.talla or 'N/A',
            'marca': (
                producto.atributo1.valor
                if producto and producto.atributo1 else ''
            ),
            'stock': pt.stock_sucursal(sucursal_id) if sucursal_id else (pt.stock or 0),
        }
    return resultado


def _documento_vivo(cotizacion):
    """(ok, mensaje) — ¿el DTE de la cotización sigue respaldando la venta?

    Ni `asignar_sku_pendiente` ni `revertir_sku_despachado` miraban el estado
    del documento: bastaba que la cotización estuviera FACTURADA. Con el flujo
    de eliminación de documentos eso es concreto — el DTE puede estar
    descartado o ANULADO y el sistema seguiría sacando stock "para completarlo",
    dejando mercadería entregada sin ningún documento que la respalde.
    """
    dte = cotizacion.dte
    if dte is None:
        return False, (
            'La cotización no tiene documento tributario enlazado: no se puede '
            'mover stock contra ella. Verifique en Gestión de DTE.'
        )
    if getattr(dte, 'descartado', False):
        return False, (
            f'El documento {dte.tipo_documento} #{dte.numero_documento} fue '
            f'ELIMINADO. Reabra la cotización y vuelva a facturarla antes de '
            f'despachar.'
        )
    if (dte.estado_dte or '').upper().strip() == 'ANULADO':
        return False, (
            f'El documento {dte.tipo_documento} #{dte.numero_documento} está '
            f'ANULADO. Reabra la cotización y vuelva a facturarla antes de '
            f'despachar.'
        )
    return True, ''


def _puede_validar_despacho(request):
    """¿El usuario puede dar el OK final al despacho de una cotización?

    Gobernado por el permiso granular `gestion_cotizaciones.puede_aprobar`
    (configurable en /permisos/gestion/), mismo patrón que la aprobación de
    devoluciones por garantía. La sucursal activa puede restringirlo vía
    PermisoSucursal."""
    return PermisoRol.tiene_permiso(
        request.user, 'gestion_cotizaciones', 'puede_aprobar',
        sucursal_id=request.session.get('idSucursalActual'),
    )


# ==================== VISTAS PRINCIPALES ====================

@login_required
def gestion_cotizaciones(request):
    """
    Vista principal de gestión de cotizaciones
    """
    context = {
        'titulo': 'Gestión de Cotizaciones',
        'user': request.user,
        'sucursal_actual': request.session.get('alias', 'Sin sucursal'),
    }
    return render(request, 'vistas/modulo_documentos/gestion_cotizaciones.html', context)


# ==================== HELPERS DE VENDEDOR ====================

def _sucursal_activa_id(request):
    """ID de la sucursal activa en sesión (las dos claves históricas)."""
    return request.session.get('idSucursalActual') or request.session.get('sucursalActual')


def _vendedores_de_sucursal(sucursal):
    """
    Vendedores que pueden firmar una cotización de `sucursal`.

    Reutiliza el criterio del módulo de ventas (empresa + sucursal, con los
    fallbacks legacy) para que el vendedor elegido acá sea siempre uno válido
    en el POS al momento de facturar.
    """
    from .views_modulo_ventas import _vendedores_elegibles_para_sucursal
    return _vendedores_elegibles_para_sucursal(sucursal).order_by('nombre')


@login_required
@require_http_methods(["GET"])
def listar_vendedores_cotizacion(request):
    """API: vendedores elegibles para la sucursal activa (selector del modal)."""
    try:
        sucursal_id = _sucursal_activa_id(request)
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })

        sucursal = get_object_or_404(Sucursal, pk=sucursal_id)
        vendedores = [
            {
                'id': v.id,
                'nombre': v.nombre or v.codigo_vendedor,
                'codigo_vendedor': v.codigo_vendedor or '',
            }
            for v in _vendedores_de_sucursal(sucursal)
        ]

        return JsonResponse({
            'success': True,
            'vendedores': vendedores,
            'sucursal': {'id': sucursal.id, 'alias': sucursal.alias},
        })

    except Exception as e:
        logger.exception("Error en listar_vendedores_cotizacion")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _resolver_vendedor(vendedor_id, sucursal):
    """
    Valida que `vendedor_id` sea un vendedor elegible de `sucursal`.

    Devuelve (vendedor, None) o (None, mensaje_error).
    """
    if not vendedor_id:
        return None, 'Debe seleccionar un vendedor'

    vendedor = _vendedores_de_sucursal(sucursal).filter(pk=vendedor_id).first()
    if not vendedor:
        return None, 'El vendedor seleccionado no pertenece a esta sucursal o está inactivo'
    return vendedor, None


# ==================== HELPERS DE EVALUACIÓN ====================

def evaluar_items_cotizacion(cotizacion, sucursal_id):
    """
    Evalúa los ítems de una cotización contra el inventario de `sucursal_id`.

    Fuente única de verdad para "¿esta cotización se puede facturar?": la usan
    el listado (badges de la grilla) y el pre-flight de facturación, para que
    ambos muestren exactamente los mismos números.

    Devuelve dict con:
        total_items, items_con_sku, items_sin_sku, items_sin_stock,
        problemas_stock, items_cobertura_parcial
    """
    total_items = 0
    items_con_sku = 0
    items_sin_sku = 0
    items_sin_stock = 0
    items_cobertura_parcial = 0
    problemas_stock = []

    for item in cotizacion.items.all():
        total_items += 1
        skus_asociados = list(item.skus_asociados.all())
        tiene_sku = bool(skus_asociados) or item.producto_existente is not None

        if not tiene_sku:
            items_sin_sku += 1
            continue

        items_con_sku += 1

        if skus_asociados:
            # Unidades del ítem realmente cubiertas por SKUs.
            #
            # Antes este bloque replicaba el bug del POS: si la cobertura era
            # parcial, validaba el stock del PRIMER SKU por `item.cantidad`
            # (porque eso era lo que el POS descontaba). Ya no: el POS respeta la
            # cantidad de cada fila y las unidades sin respaldo se facturan como
            # despacho diferido, así que el pre-flight valida lo mismo —
            # `sku_rel.cantidad` por fila. La cobertura parcial se sigue
            # contando para avisarla en la grilla.
            cubiertas = sum(s.cantidad for s in skus_asociados)
            cobertura_parcial = cubiertas != item.cantidad
            if cobertura_parcial:
                items_cobertura_parcial += 1

            for sku_rel in skus_asociados:
                if not sku_rel.producto_talla:
                    continue
                requerido = sku_rel.cantidad or 0
                if requerido <= 0:
                    continue
                stock_actual = sku_rel.producto_talla.stock_sucursal(sucursal_id)
                if stock_actual < requerido:
                    items_sin_stock += 1
                    problemas_stock.append({
                        'sku': str(sku_rel.producto_talla.sku),
                        'descripcion': item.descripcion[:30],
                        'stock': stock_actual,
                        'requerido': requerido,
                    })
        elif item.producto_existente:
            # Compatibilidad con el modelo anterior (sin Detalle_SKU)
            stock_actual = item.producto_existente.stock_sucursal(sucursal_id)
            if stock_actual < item.cantidad:
                items_sin_stock += 1
                problemas_stock.append({
                    'sku': str(item.producto_existente.sku),
                    'descripcion': item.descripcion[:30],
                    'stock': stock_actual,
                    'requerido': item.cantidad,
                })

    return {
        'total_items': total_items,
        'items_con_sku': items_con_sku,
        'items_sin_sku': items_sin_sku,
        'items_sin_stock': items_sin_stock,
        'items_cobertura_parcial': items_cobertura_parcial,
        'problemas_stock': problemas_stock,
    }


def _validar_cobertura_skus(items_data):
    """Un ítem se factura completo o no se factura: la suma de las cantidades de
    sus SKUs tiene que ser exactamente su cantidad (o no tener SKUs y salir por
    despacho diferido).

    Sin esta regla se guardan ítems "a medias" (5 uds cotizadas, 1 asignada a
    SKU) que el POS factura entero contra el primer SKU: sobreventa de stock que
    nadie validó y despacho diferido que no se entera. Ya pasó en producción.

    Devuelve un mensaje de error o None.
    """
    for idx, item_data in enumerate(items_data, start=1):
        skus = item_data.get('skus') or []
        if not skus:
            continue  # ítem sin SKU: despacho diferido, es un flujo válido
        try:
            cantidad = int(item_data.get('cantidad') or 0)
            asignada = sum(int(s.get('cantidad') or 0) for s in skus)
        except (TypeError, ValueError):
            return f'Ítem {idx}: cantidades inválidas'
        if asignada != cantidad:
            descripcion = (item_data.get('descripcion') or '')[:40]
            return (
                f'Ítem {idx} ("{descripcion}"): cotizas {cantidad} unidad(es) pero '
                f'tienes {asignada} asignada(s) a SKU. Asigna las {cantidad} '
                f'unidades, o quita los SKUs y déjalo como ítem pendiente '
                f'(se facturará con despacho diferido).'
            )
    return None


# ==================== APIs DE LISTADO Y CONSULTA ====================

@login_required
@require_http_methods(["GET"])
def listar_cotizaciones(request):
    """
    API para listar cotizaciones con filtros
    """
    try:
        
        # Obtener parámetros de filtro
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        estado = request.GET.get('estado', '')
        cliente_id = request.GET.get('cliente_id', '')
        buscar = request.GET.get('buscar', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        # Obtener sucursal de la sesión
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        hoy = timezone.localdate()

        # Construir query base
        cotizaciones = (
            Cotizacion_Empresa.objects
            .filter(sucursal_id=sucursal_id)
            .select_related('cliente', 'vendedor', 'dte', 'despacho_validado_por')
            # `producto` incluido a propósito: Producto_Talla.stock_sucursal()
            # lo consulta para saber si el SKU es de esta sucursal, y sin el
            # prefetch eso es una query por cada SKU de cada fila del listado.
            .prefetch_related(
                'items__skus_asociados__producto_talla__producto',
                'items__producto_existente__producto',
            )
        )

        # Aplicar filtros
        if fecha_desde:
            cotizaciones = cotizaciones.filter(fecha_emision__gte=fecha_desde)

        if fecha_hasta:
            cotizaciones = cotizaciones.filter(fecha_emision__lte=fecha_hasta)

        if estado:
            # La vigencia se filtra por FECHA, no por el campo guardado: el
            # estado solo se recalcula dentro de save(), así que hay
            # cotizaciones VIGENTE en la base que llevan meses vencidas. Sin
            # esto el filtro "Vencida" no devolvía nunca nada.
            if estado == Cotizacion_Empresa.ESTADO_VIGENTE:
                cotizaciones = cotizaciones.filter(
                    estado=Cotizacion_Empresa.ESTADO_VIGENTE,
                    fecha_validez__gte=hoy,
                )
            elif estado == Cotizacion_Empresa.ESTADO_VENCIDA:
                cotizaciones = cotizaciones.filter(
                    Q(estado=Cotizacion_Empresa.ESTADO_VENCIDA)
                    | Q(estado=Cotizacion_Empresa.ESTADO_VIGENTE,
                        fecha_validez__lt=hoy)
                )
            else:
                cotizaciones = cotizaciones.filter(estado=estado)

        if cliente_id:
            cotizaciones = cotizaciones.filter(cliente_id=cliente_id)
        
        if buscar:
            cotizaciones = cotizaciones.filter(
                Q(numero_cotizacion__icontains=buscar) |
                Q(cliente__nombre__icontains=buscar) |
                Q(cliente__rut__icontains=buscar) |
                Q(descripcion__icontains=buscar) |
                Q(items__descripcion__icontains=buscar)
            ).distinct()
        
        # Ordenar
        cotizaciones = cotizaciones.order_by('-fecha_emision', '-numero_cotizacion')
        
        # Calcular estadísticas.
        # 1) "Vigentes" se cuenta por FECHA: antes contaba el campo `estado`,
        #    así que una cotización vencida hace meses seguía sumando vigente.
        # 2) Se agrega sobre una queryset SIN el join a items: al buscar texto
        #    la consulta hace JOIN con `items` y un Sum('total') ahí suma el
        #    total una vez por línea que calce (montos inflados).
        # 3) Todo en UN solo aggregate: la base es remota y cada consulta
        #    costaba ~250 ms; eran 6 viajes solo para la barra de KPIs.
        _q_vigente = Q(estado=Cotizacion_Empresa.ESTADO_VIGENTE, fecha_validez__gte=hoy)
        _q_vencida = (
            Q(estado=Cotizacion_Empresa.ESTADO_VENCIDA)
            | Q(estado=Cotizacion_Empresa.ESTADO_VIGENTE, fecha_validez__lt=hoy)
        )
        _base_stats = Cotizacion_Empresa.objects.filter(
            pk__in=cotizaciones.values('pk')
        )
        # Los alias NO pueden llamarse `total`: chocan con el campo `total` del
        # modelo y Django responde "Cannot compute Sum('total')".
        _agg = _base_stats.aggregate(
            n_total=Count('id'),
            n_vigentes=Count('id', filter=_q_vigente),
            n_vencidas=Count('id', filter=_q_vencida),
            n_facturadas=Count('id', filter=Q(estado=Cotizacion_Empresa.ESTADO_FACTURADA)),
            n_anuladas=Count('id', filter=Q(estado=Cotizacion_Empresa.ESTADO_ANULADA)),
            m_total=Sum('total'),
            m_vigente=Sum('total', filter=_q_vigente),
            m_vencido=Sum('total', filter=_q_vencida),
        )
        estadisticas = {
            'total': _agg['n_total'] or 0,
            'vigentes': _agg['n_vigentes'] or 0,
            'vencidas': _agg['n_vencidas'] or 0,
            # monto_total = todo lo listado (incluye anuladas y vencidas);
            # monto_vigente = plata realmente viva. El frontend muestra ambos.
            'monto_total': _agg['m_total'] or 0,
            'monto_vigente': _agg['m_vigente'] or 0,
            'monto_vencido': _agg['m_vencido'] or 0,
            'facturadas': _agg['n_facturadas'] or 0,
            'anuladas': _agg['n_anuladas'] or 0,
        }

        # Paginación
        total = estadisticas['total']
        inicio = (page - 1) * per_page
        fin = inicio + per_page
        cotizaciones_paginadas = cotizaciones[inicio:fin]
        
        # Permiso de validación (una sola vez, aplica a toda la lista)
        puede_validar = _puede_validar_despacho(request)
        # Reabrir una cotización facturada es corrección documental: solo
        # administrador. Se resuelve una vez y no por fila.
        from .views_modulo_ventas import _usuario_es_administrador_activo
        es_administrador = _usuario_es_administrador_activo(request.user)

        # Serializar datos
        cotizaciones_data = []
        for cot in cotizaciones_paginadas:
            # Contar items con y sin SKU asociado, y verificar stock
            _eval = evaluar_items_cotizacion(cot, sucursal_id)
            total_items = _eval['total_items']
            items_con_sku = _eval['items_con_sku']
            items_sin_sku = _eval['items_sin_sku']
            items_sin_stock = _eval['items_sin_stock']
            problemas_stock = _eval['problemas_stock']

            # Solo puede facturar si:
            # - Tiene items
            # - NO está ya facturada
            # - Está vigente DE VERDAD (por fecha, no por el campo guardado)
            # - (items_sin_sku se tolera: el usuario confirmará despacho diferido)
            esta_facturada = cot.facturada or cot.estado == Cotizacion_Empresa.ESTADO_FACTURADA
            esta_vencida = _esta_vencida(cot, hoy)
            estado_efectivo = _estado_efectivo(cot, hoy)
            # OJO: `esta_vigente` miraba solo `cot.estado`, mientras que el
            # backend de facturación usa la property `esta_vigente` del modelo
            # (que sí mira la fecha). Resultado: el botón "Convertir a Factura"
            # aparecía habilitado y al pulsarlo respondía "Solo se pueden
            # facturar cotizaciones vigentes". Ahora ambos dicen lo mismo.
            esta_vigente = (
                cot.estado == Cotizacion_Empresa.ESTADO_VIGENTE and not esta_vencida
            )
            puede_facturar = total_items > 0 and items_sin_stock == 0 and not esta_facturada and esta_vigente

            # Motivo por el que no puede facturar
            motivo_no_facturar = None
            if esta_facturada:
                motivo_no_facturar = f'Ya facturada: {cot.numero_factura or "Sin número"}'
            elif esta_vencida:
                motivo_no_facturar = (
                    f'Vencida hace {abs(cot.dias_restantes)} día(s). '
                    f'Edítela y renueve los días de validez para poder facturarla.'
                )
            elif not esta_vigente:
                motivo_no_facturar = f'Estado: {cot.get_estado_display()}'
            elif items_sin_stock > 0:
                motivo_no_facturar = f'{items_sin_stock} producto(s) sin stock suficiente'
            elif items_sin_sku > 0:
                motivo_no_facturar = f'{items_sin_sku} ítem(s) sin SKU (se facturará con despacho diferido)'

            cotizaciones_data.append({
                'id': cot.id,
                'numero_cotizacion': cot.numero_cotizacion,
                'fecha_emision': cot.fecha_emision.strftime('%Y-%m-%d'),
                'fecha_validez': cot.fecha_validez.strftime('%Y-%m-%d'),
                'cliente_nombre': cot.cliente.nombre,
                'cliente_rut': cot.cliente.rut,
                'cliente_email': getattr(cot.cliente, 'correoIntercambio', ''),
                'vendedor_id': cot.vendedor_id,
                'vendedor_nombre': cot.vendedor.nombre if cot.vendedor else 'Sin vendedor',
                'estado': cot.estado,
                # Estado que se debe MOSTRAR (VENCIDA calculada por fecha)
                'estado_efectivo': estado_efectivo,
                'esta_vencida': esta_vencida,
                'facturada': esta_facturada,
                'numero_factura': cot.numero_factura or '',
                # Documento tributario real. Una facturada sin `dte` es una
                # cotización "zombi": dice FACTURADA con un número que no
                # respalda ningún DTE (caso real en producción).
                'dte_id': cot.dte_id,
                'documento': (
                    f'{cot.dte.tipo_documento} #{cot.dte.numero_documento}'
                    if cot.dte_id else ''
                ),
                'sin_dte_enlazado': bool(esta_facturada and not cot.dte_id),
                # ¿El documento que la respalda dejó de existir? Se resuelve
                # sobre el `dte` ya traído por select_related: cero queries
                # extra. Es la señal que habilita el botón "Reabrir"; los guards
                # duros (stock devuelto, despachos revertidos) los revalida
                # `reabrir_cotizacion` bajo lock, no el listado.
                'documento_muerto': bool(
                    esta_facturada and (
                        not cot.dte_id
                        or cot.dte.descartado
                        or (cot.dte.estado_dte or '').upper().strip() == 'ANULADO'
                    )
                ),
                'documento_estado_dte': (cot.dte.estado_dte or '') if cot.dte_id else '',
                'documento_descartado': bool(cot.dte_id and cot.dte.descartado),
                'items_cobertura_parcial': _eval['items_cobertura_parcial'],
                'monto_total': float(cot.total),
                'total_items': total_items,
                'items_con_sku': items_con_sku,
                'items_sin_sku': items_sin_sku,
                'items_sin_stock': items_sin_stock,
                'problemas_stock': problemas_stock,
                'puede_facturar': puede_facturar,
                'motivo_no_facturar': motivo_no_facturar,
                'descripcion': cot.descripcion or '',
                'dias_restantes': cot.dias_restantes,
                # Despacho diferido
                'estado_despacho': cot.estado_despacho,
                'tiene_despacho_pendiente': (
                    esta_facturada and
                    cot.estado_despacho in (
                        Cotizacion_Empresa.DESPACHO_PENDIENTE,
                        Cotizacion_Empresa.DESPACHO_PARCIAL,
                    )
                ),
                # Ítems con saldo por despachar, contados por UNIDADES (mismo
                # criterio que Cotizacion_Empresa.actualizar_estado_despacho()):
                # contarlos por el flag `es_producto_pendiente` dejaba fuera al
                # ítem que sí tenía SKU pero cubría menos unidades que las
                # facturadas. Se resuelve sobre los ítems ya prefetcheados (un
                # .filter() aquí volvería a pegarle a la base por cada fila).
                'items_pendientes_despacho': (
                    sum(
                        1 for it in cot.items.all()
                        if _cuadratura_item(it)[0] > 0
                    )
                    if esta_facturada else 0
                ),
            })

            # Cuadratura por UNIDADES (facturado vs despachado) + OK admin.
            # Solo tiene sentido en facturadas; en el resto va en 0 para que
            # el frontend no pinte badges.
            if esta_facturada:
                # ¿Hubo despachos DESPUÉS de facturar? Solo esos se pueden
                # revertir; sirve para dejar accesible la corrección de un SKU
                # mal despachado aunque ya no queden unidades pendientes.
                uds_facturadas, uds_pendientes, uds_post_factura = _cuadratura_despacho(cot)
                cotizaciones_data[-1].update({
                    'unidades_despachadas_post_factura': uds_post_factura,
                    'tiene_despachos_post_factura': uds_post_factura > 0,
                    'unidades_facturadas': uds_facturadas,
                    'unidades_despachadas': uds_facturadas - uds_pendientes,
                    'unidades_pendientes': uds_pendientes,
                    # Por UNIDADES, no por estado_despacho guardado: un parcial
                    # cerrado en falso por el flujo viejo (estado COMPLETADO
                    # stale) debe recuperar el botón de despacho.
                    'tiene_despacho_pendiente': uds_pendientes > 0,
                    'despacho_validado': cot.despacho_validado,
                    'despacho_validado_por': (
                        (cot.despacho_validado_por.get_full_name()
                         or cot.despacho_validado_por.username)
                        if cot.despacho_validado_por else ''
                    ),
                    'puede_validar_despacho': (
                        puede_validar and not cot.despacho_validado
                        and uds_pendientes == 0
                    ),
                    # Botón "Reabrir para re-facturar": documento muerto +
                    # administrador + sin unidades de despacho diferido afuera.
                    # Lo último se puede evaluar acá porque `uds_post_factura`
                    # ya está calculado sobre los ítems prefetcheados.
                    'puede_reabrir': (
                        es_administrador
                        and cotizaciones_data[-1]['documento_muerto']
                    ),
                })
            else:
                cotizaciones_data[-1].update({
                    'unidades_facturadas': 0,
                    'unidades_despachadas': 0,
                    'unidades_pendientes': 0,
                    'unidades_despachadas_post_factura': 0,
                    'tiene_despachos_post_factura': False,
                    'despacho_validado': False,
                    'despacho_validado_por': '',
                    'puede_validar_despacho': False,
                    'puede_reabrir': False,
                })
        
        return JsonResponse({
            'success': True,
            'cotizaciones': cotizaciones_data,
            'total': total,
            'estadisticas': estadisticas,
            'page': page,
            'per_page': per_page
        })
        
    except Exception as e:
        logger.exception("Error en listar_cotizaciones")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["GET"])
def detalle_cotizacion(request, cotizacion_id):
    """
    API para obtener detalles completos de una cotización
    """
    try:
        cotizacion = get_object_or_404(
            Cotizacion_Empresa.objects
            .select_related('cliente', 'vendedor', 'dte')
            .prefetch_related('items__skus_asociados__producto_talla__producto'),
            pk=cotizacion_id,
        )

        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver esta cotización'
            }, status=403)

        # Serializar items
        items_data = []
        logger.debug("Cargando detalle de cotizacion %s", cotizacion.numero_cotizacion)

        # ¿Los "SKU esperado" anotados a mano existen en el catálogo de esta
        # sucursal? Se resuelve de una sola vez para toda la cotización. El
        # modal de despacho lo usa para ofrecer el SKU directamente en vez de
        # obligar a buscarlo a ciegas.
        skus_esperados_match = _resolver_skus_esperados(
            [i.sku_producto_pendiente for i in cotizacion.items.all()],
            sucursal_id,
        )

        # sorted() en Python sobre el prefetch: .order_by() dispararía una
        # consulta nueva por ítem y tiraría el prefetch a la basura.
        for item in sorted(cotizacion.items.all(), key=lambda i: i.numero_linea or 0):
            # Relación ya prefetcheada
            filas_sku = list(item.skus_asociados.all())
            # Cuadratura del ítem por el MISMO helper que usa el listado (las
            # properties del modelo hacen un aggregate por ítem: 2 round-trips
            # extra por línea). Antes acá se repetía la fórmula vieja a mano y
            # el modal "Asignar SKU" filtra por `unidades_pendientes > 0`: con
            # 0 no ofrecía nada que despachar aunque el backend sí aceptaba el
            # saldo.
            uds_pendientes_item, uds_despachadas_item = _cuadratura_item(item)
            # Obtener TODOS los SKUs asociados del nuevo modelo
            skus_asociados = []
            for sku_rel in filas_sku:
                if sku_rel.producto_talla:
                    pt = sku_rel.producto_talla
                    producto = pt.producto
                    costo = int(sku_rel.costo_unitario) if sku_rel.costo_unitario else (int(producto.costo) if producto and producto.costo else 0)
                    precio = int(producto.precioventa) if producto and producto.precioventa else 0
                    margen = round(((precio - costo) / precio) * 100, 1) if precio > 0 and costo > 0 else 0
                    
                    skus_asociados.append({
                        'id': pt.id,
                        'sku': str(pt.sku),
                        'nombre': producto.articulo if producto else 'Sin nombre',
                        'talla': pt.talla or 'N/A',
                        'marca': producto.atributo1.valor if producto and producto.atributo1 else '',
                        'costo': costo,
                        'precio': precio,
                        'stock': pt.stock_sucursal(sucursal_id),
                        'margen_porcentaje': margen,
                        'cantidad': sku_rel.cantidad  # Cantidad de este SKU específico
                    })
            
            logger.debug(
                "Detalle cotizacion %s item=%s skus=%s",
                cotizacion.numero_cotizacion,
                item.numero_linea,
                len(skus_asociados),
            )
            
            # Para compatibilidad, producto_data será el primer SKU (si existe)
            producto_data = skus_asociados[0] if skus_asociados else None
            
            # Si no hay SKUs en el nuevo modelo, intentar con producto_existente (compatibilidad)
            if not producto_data and item.producto_existente:
                pt = item.producto_existente
                producto = pt.producto
                costo = int(producto.costo) if producto and producto.costo else 0
                precio = int(producto.precioventa) if producto and producto.precioventa else 0
                margen = round(((precio - costo) / precio) * 100, 1) if precio > 0 and costo > 0 else 0
                
                producto_data = {
                    'id': pt.id,
                    'sku': str(pt.sku),
                    'nombre': producto.articulo if producto else 'Sin nombre',
                    'talla': pt.talla or 'N/A',
                    'marca': producto.atributo1.valor if producto and producto.atributo1 else '',
                    'costo': costo,
                    'precio': precio,
                    'stock': pt.stock_sucursal(sucursal_id),
                    'margen_porcentaje': margen,
                    'cantidad': item.cantidad
                }
                skus_asociados = [producto_data]
            
            if producto_data:
                logger.debug(
                    "Detalle cotizacion %s item=%s sku_list=%s",
                    cotizacion.numero_cotizacion,
                    item.numero_linea,
                    [s['sku'] for s in skus_asociados],
                )

            items_data.append({
                'id': item.id,
                'numero_linea': item.numero_linea,
                'descripcion': item.descripcion,
                'cantidad': item.cantidad,
                'precio_unitario': float(item.precio_unitario),
                'subtotal': float(item.subtotal),
                'producto_nombre': item.nombre_producto,
                'producto_sku': item.sku_producto,
                'producto_existente_id': item.producto_existente_id,
                'producto_data': producto_data,  # Primer SKU para compatibilidad
                'skus_asociados': skus_asociados,  # TODOS los SKUs
                'es_producto_pendiente': item.es_producto_pendiente,
                'sku_asignado_post_factura': item.sku_asignado_post_factura,
                'nombre_producto_pendiente': item.nombre_producto_pendiente or '',
                'sku_producto_pendiente': item.sku_producto_pendiente or '',
                'tiene_sku': bool(skus_asociados),
                'tiene_stock': item.tiene_stock_suficiente,
                'stock_disponible': item.stock_disponible,
                'observaciones': item.observaciones or '',
                # Cuadratura por unidades (despacho diferido parcial)
                'unidades_despachadas': uds_despachadas_item,
                'unidades_pendientes': uds_pendientes_item,
                # Unidades del ítem realmente cubiertas por SKUs. Si no llegan
                # a `cantidad`, la diferencia se factura como despacho diferido
                # (el POS ya NO le carga el excedente al primer SKU).
                'unidades_cubiertas_sku': sum(s.cantidad for s in filas_sku),
                # El SKU anotado a mano al cotizar, si resultó existir de verdad
                # en el catálogo de esta sucursal. None = no existe o no se
                # escribió: hay que buscarlo.
                'sku_esperado_match': skus_esperados_match.get(
                    (item.sku_producto_pendiente or '').strip()
                ),
            })
        
        _uds_fact, _uds_pend, _uds_post = _cuadratura_despacho(cotizacion)

        # Datos de la cotización
        cotizacion_data = {
            'id': cotizacion.id,
            'numero_cotizacion': cotizacion.numero_cotizacion,
            'fecha_emision': cotizacion.fecha_emision.strftime('%Y-%m-%d'),
            'fecha_validez': cotizacion.fecha_validez.strftime('%Y-%m-%d'),
            'dias_validez': cotizacion.dias_validez,
            'descripcion': cotizacion.descripcion or '',
            'observaciones': cotizacion.observaciones or '',
            'cliente_id': cotizacion.cliente.id,  # ID del cliente para selección directa
            'cliente_nombre': cotizacion.cliente.nombre,
            'cliente_rut': cotizacion.cliente.rut,
            'cliente_email': getattr(cotizacion.cliente, 'correoIntercambio', ''),
            'cliente_telefono': '',  # Agregar si existe en el modelo
            'vendedor_id': cotizacion.vendedor_id,
            'vendedor_nombre': cotizacion.vendedor.nombre if cotizacion.vendedor else 'Sin vendedor',
            'estado': cotizacion.estado,
            'subtotal': float(cotizacion.subtotal),
            'descuento': float(cotizacion.descuento),
            'impuesto': float(cotizacion.impuesto),
            'monto_total': float(cotizacion.total),
            'facturada': cotizacion.facturada,
            'numero_factura': cotizacion.numero_factura or '',
            'items': items_data,
            'esta_vigente': cotizacion.esta_vigente,
            'esta_vencida': _esta_vencida(cotizacion),
            'estado_efectivo': _estado_efectivo(cotizacion),
            'dias_restantes': cotizacion.dias_restantes,
            # Documento tributario real (una facturada sin DTE es una zombi)
            'dte_id': cotizacion.dte_id,
            'documento': (
                f'{cotizacion.dte.tipo_documento} #{cotizacion.dte.numero_documento}'
                if cotizacion.dte_id else ''
            ),
            'sin_dte_enlazado': bool(cotizacion.facturada and not cotizacion.dte_id),
            # Cuadratura por unidades + validación (solo aplica a facturadas)
            'estado_despacho': cotizacion.estado_despacho or '',
            'unidades_facturadas': _uds_fact if cotizacion.facturada else 0,
            'unidades_pendientes': _uds_pend if cotizacion.facturada else 0,
            'unidades_despachadas_post_factura': _uds_post if cotizacion.facturada else 0,
            'despacho_validado': cotizacion.despacho_validado,
        }
        
        return JsonResponse({
            'success': True,
            'cotizacion': cotizacion_data
        })
        
    except Exception as e:
        logger.exception("Error en detalle_cotizacion")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== APIs DE CREACIÓN Y EDICIÓN ====================

@login_required
@require_http_methods(["POST"])
def crear_cotizacion(request):
    """
    API para crear una nueva cotización
    """
    try:
        data = json.loads(request.body)
        
        # Obtener sucursal de la sesión
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        sucursal = get_object_or_404(Sucursal, pk=sucursal_id)
        
        # Validar datos requeridos
        cliente_id = data.get('cliente_id')
        if not cliente_id:
            return JsonResponse({
                'success': False,
                'error': 'Debe seleccionar un cliente'
            })
        
        cliente = get_object_or_404(Empresa, pk=cliente_id)

        # Vendedor de la sucursal activa: obligatorio. Sin él, al facturar el
        # POS caía a `Vendedor.objects.filter(...).first()` y el DTE (y la
        # comisión) se lo llevaba un vendedor arbitrario.
        vendedor, error_vendedor = _resolver_vendedor(data.get('vendedor_id'), sucursal)
        if error_vendedor:
            return JsonResponse({'success': False, 'error': error_vendedor})

        # Calcular fecha de validez
        fecha_emision_str = data.get('fecha_emision')
        dias_validez = int(data.get('dias_validez', 30))

        fecha_emision = datetime.strptime(fecha_emision_str, '%Y-%m-%d').date() if fecha_emision_str else timezone.localdate()
        fecha_validez = fecha_emision + timedelta(days=dias_validez)

        items_data = data.get('items', [])

        error_cobertura = _validar_cobertura_skus(items_data)
        if error_cobertura:
            return JsonResponse({'success': False, 'error': error_cobertura})

        # Todo dentro de una transacción: si falla a mitad no queda una
        # cotización sin ítems (y el lock del correlativo vive hasta el commit).
        from django.db import transaction
        with transaction.atomic():
            numero_cotizacion = generar_numero_cotizacion(sucursal)

            cotizacion = Cotizacion_Empresa.objects.create(
                sucursal=sucursal,
                cliente=cliente,
                vendedor=vendedor,
                usuario_creador=request.user,
                numero_cotizacion=numero_cotizacion,
                fecha_emision=fecha_emision,
                fecha_validez=fecha_validez,
                dias_validez=dias_validez,
                descripcion=data.get('descripcion', ''),
                observaciones=data.get('observaciones', ''),
            )

            logger.debug(
                "Creando items para cotizacion %s: total_items=%s",
                numero_cotizacion, len(items_data),
            )

            for idx, item_data in enumerate(items_data, start=1):
                # Obtener SKUs asociados si existen
                skus = item_data.get('skus', [])

                # Determinar si tiene productos asociados
                tiene_skus = bool(skus)
                nombre_producto_pendiente = None

                if not tiene_skus:
                    # Es un producto pendiente (sin SKU asociado)
                    nombre_producto_pendiente = item_data.get('descripcion', '')[:255]

                # Crear el detalle del item. `recalcular_cotizacion=False`: los
                # totales se calculan una sola vez al final (evita el O(n²)).
                detalle = Cotizacion_Empresa_Detalle(
                    cotizacion=cotizacion,
                    numero_linea=idx,
                    descripcion=item_data['descripcion'],
                    cantidad=int(item_data['cantidad']),
                    precio_unitario=Decimal(str(item_data['precio_unitario'])),
                    producto_existente_id=skus[0].get('producto_talla_id') if tiene_skus else None,
                    es_producto_pendiente=not tiene_skus,
                    nombre_producto_pendiente=nombre_producto_pendiente,
                    observaciones=item_data.get('observaciones', ''),
                )
                detalle.save(recalcular_cotizacion=False)

                # Guardar TODOS los SKUs asociados
                if tiene_skus:
                    Cotizacion_Empresa_Detalle_SKU.objects.bulk_create([
                        Cotizacion_Empresa_Detalle_SKU(
                            detalle=detalle,
                            producto_talla_id=sku_data['producto_talla_id'],
                            cantidad=int(sku_data.get('cantidad', 1)),
                            costo_unitario=Decimal(str(sku_data.get('costo', 0))),
                            precio_unitario=Decimal(str(item_data['precio_unitario'])),
                        )
                        for sku_data in skus
                        if sku_data.get('producto_talla_id')
                    ])

            # Recalcular totales (una sola vez, con todos los ítems ya escritos)
            cotizacion.calcular_totales()

            # Crear registro en historial
            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='CREADA',
                descripcion=f'Cotización creada por {request.user.username}',
                ip_address=get_client_ip(request)
            )

        return JsonResponse({
            'success': True,
            'message': 'Cotización creada exitosamente',
            'cotizacion_id': cotizacion.id,
            'numero_cotizacion': cotizacion.numero_cotizacion
        })
        
    except Exception as e:
        logger.exception("Error en crear_cotizacion")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def editar_cotizacion(request, cotizacion_id):
    """
    API para editar una cotización existente (incluye items y productos asociados)
    """
    try:
        data = json.loads(request.body)
        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)

        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para editar esta cotización'
            }, status=403)

        # Se editan las vigentes y las VENCIDAS (para poder renovarles la
        # validez). Antes solo VIGENTE, y como `save()` degrada sola a VENCIDA
        # al pasar la fecha, editar una cotización vencida la dejaba VENCIDA y
        # a partir de ahí era ineditable e infacturable para siempre: quedaba
        # muerta sin más salida que anularla.
        if cotizacion.facturada or cotizacion.estado in (
            Cotizacion_Empresa.ESTADO_FACTURADA, Cotizacion_Empresa.ESTADO_ANULADA,
        ):
            return JsonResponse({
                'success': False,
                'error': (
                    'No se puede editar una cotización '
                    f'{cotizacion.get_estado_display().lower()}'
                )
            })

        logger.debug("Editando cotizacion %s", cotizacion.numero_cotizacion)

        estaba_vencida = _esta_vencida(cotizacion)

        # Actualizar datos generales
        cotizacion.descripcion = data.get('descripcion', cotizacion.descripcion)
        cotizacion.observaciones = data.get('observaciones', cotizacion.observaciones)

        # Vigencia: el formulario SIEMPRE envía fecha_emision y dias_validez,
        # pero acá se ignoraban en silencio. Se podía "renovar" una cotización
        # vencida, ver el mensaje de éxito y que la fecha de validez no se
        # moviera un día. Ahora se aplican y se recalcula fecha_validez.
        fecha_emision_str = data.get('fecha_emision')
        if fecha_emision_str:
            try:
                cotizacion.fecha_emision = datetime.strptime(
                    fecha_emision_str, '%Y-%m-%d'
                ).date()
            except (TypeError, ValueError):
                return JsonResponse({
                    'success': False, 'error': 'Fecha de emisión inválida'
                })
        dias_validez_raw = data.get('dias_validez')
        if dias_validez_raw not in (None, ''):
            try:
                dias_validez = int(dias_validez_raw)
            except (TypeError, ValueError):
                return JsonResponse({
                    'success': False, 'error': 'Días de validez inválidos'
                })
            if dias_validez < 1:
                return JsonResponse({
                    'success': False, 'error': 'Los días de validez deben ser 1 o más'
                })
            cotizacion.dias_validez = dias_validez
        cotizacion.fecha_validez = (
            cotizacion.fecha_emision + timedelta(days=cotizacion.dias_validez)
        )

        # Si la nueva vigencia alcanza hasta hoy, la cotización revive.
        # `save()` solo sabe degradar (VIGENTE→VENCIDA), nunca al revés.
        renovada = False
        if cotizacion.fecha_validez >= timezone.localdate():
            if cotizacion.estado == Cotizacion_Empresa.ESTADO_VENCIDA or estaba_vencida:
                renovada = True
            cotizacion.estado = Cotizacion_Empresa.ESTADO_VIGENTE

        # Actualizar cliente si se envía
        cliente_id = data.get('cliente_id')
        if cliente_id:
            cotizacion.cliente_id = cliente_id

        # Vendedor: se puede reasignar mientras la cotización esté vigente.
        # Se exige siempre para que las cotizaciones antiguas sin vendedor
        # queden reparadas al primer guardado.
        vendedor, error_vendedor = _resolver_vendedor(
            data.get('vendedor_id'), cotizacion.sucursal
        )
        if error_vendedor:
            return JsonResponse({'success': False, 'error': error_vendedor})
        cotizacion.vendedor = vendedor

        items_data = data.get('items', [])

        error_cobertura = _validar_cobertura_skus(items_data)
        if error_cobertura:
            return JsonResponse({'success': False, 'error': error_cobertura})

        # Atómico: el borrado + recreación de ítems no puede quedar a medias
        # (dejaría la cotización sin líneas y con totales viejos).
        from django.db import transaction
        with transaction.atomic():
            cotizacion.save()

            if items_data:
                logger.debug(
                    "Actualizando items de cotizacion %s: total_items=%s",
                    cotizacion.numero_cotizacion, len(items_data),
                )

                # Eliminar items anteriores (esto también elimina los SKUs por cascade)
                cotizacion.items.all().delete()

                # Crear nuevos items
                for idx, item_data in enumerate(items_data, start=1):
                    # Obtener SKUs asociados si existen
                    skus = item_data.get('skus', [])

                    # Determinar si tiene productos asociados
                    tiene_skus = bool(skus)
                    nombre_producto_pendiente = None

                    if not tiene_skus:
                        # Es un producto pendiente (sin SKU asociado)
                        nombre_producto_pendiente = item_data.get('descripcion', '')[:255]

                    # `recalcular_cotizacion=False`: totales una sola vez al final.
                    detalle = Cotizacion_Empresa_Detalle(
                        cotizacion=cotizacion,
                        numero_linea=idx,
                        descripcion=item_data['descripcion'],
                        cantidad=int(item_data['cantidad']),
                        precio_unitario=Decimal(str(item_data['precio_unitario'])),
                        producto_existente_id=skus[0].get('producto_talla_id') if tiene_skus else None,
                        es_producto_pendiente=not tiene_skus,
                        nombre_producto_pendiente=nombre_producto_pendiente,
                        observaciones=item_data.get('observaciones', ''),
                    )
                    detalle.save(recalcular_cotizacion=False)

                    # Guardar TODOS los SKUs asociados
                    if tiene_skus:
                        Cotizacion_Empresa_Detalle_SKU.objects.bulk_create([
                            Cotizacion_Empresa_Detalle_SKU(
                                detalle=detalle,
                                producto_talla_id=sku_data['producto_talla_id'],
                                cantidad=int(sku_data.get('cantidad', 1)),
                                costo_unitario=Decimal(str(sku_data.get('costo', 0))),
                                precio_unitario=Decimal(str(item_data['precio_unitario'])),
                            )
                            for sku_data in skus
                            if sku_data.get('producto_talla_id')
                        ])

                # Recalcular totales
                cotizacion.calcular_totales()

            # Registrar en historial
            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='MODIFICADA',
                descripcion=(
                    f'Cotización modificada por {request.user.username}'
                    + (f'. Vigencia renovada hasta {cotizacion.fecha_validez} '
                       f'({cotizacion.dias_validez} días desde '
                       f'{cotizacion.fecha_emision}).' if renovada else '')
                ),
                datos_nuevos={
                    'fecha_emision': cotizacion.fecha_emision.isoformat(),
                    'fecha_validez': cotizacion.fecha_validez.isoformat(),
                    'dias_validez': cotizacion.dias_validez,
                    'renovada': renovada,
                },
                ip_address=get_client_ip(request)
            )

        logger.info(
            "Cotizacion actualizada exitosamente: numero=%s renovada=%s validez=%s",
            cotizacion.numero_cotizacion, renovada, cotizacion.fecha_validez,
        )

        return JsonResponse({
            'success': True,
            'message': (
                'Cotización actualizada exitosamente'
                + (f'. Vigencia renovada hasta el '
                   f'{cotizacion.fecha_validez.strftime("%d/%m/%Y")}.'
                   if renovada else '')
            ),
            'renovada': renovada,
            'fecha_validez': cotizacion.fecha_validez.strftime('%Y-%m-%d'),
        })

    except Exception as e:
        logger.exception("Error en editar_cotizacion")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== APIs DE ACCIONES ====================

@login_required
@require_http_methods(["POST"])
def anular_cotizacion(request):
    """
    API para anular una cotización
    """
    try:
        data = json.loads(request.body)
        cotizacion_id = data.get('cotizacion_id')
        motivo = data.get('motivo', 'Anulada por el usuario')
        
        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)
        
        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para anular esta cotización'
            }, status=403)
        
        # No se pueden anular cotizaciones ya facturadas
        if cotizacion.facturada:
            return JsonResponse({
                'success': False,
                'error': 'No se puede anular una cotización que ya fue facturada'
            })
        
        # Anular cotización
        cotizacion.anular(request.user, motivo)
        
        # Registrar en historial
        Historial_Cotizacion.objects.create(
            cotizacion=cotizacion,
            usuario=request.user,
            accion='ANULADA',
            descripcion=f'Cotización anulada. Motivo: {motivo}',
            ip_address=get_client_ip(request)
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Cotización anulada exitosamente'
        })
        
    except Exception as e:
        logger.exception("Error en anular_cotizacion")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def reabrir_cotizacion(request):
    """
    Devuelve a VIGENTE una cotización FACTURADA cuyo documento tributario ya
    fue eliminado o anulado, para poder re-facturarla.

    Body JSON:
        cotizacion_id – ID de Cotizacion_Empresa
        motivo        – texto obligatorio (5+ caracteres), queda en el historial
        dias_validez  – opcional, días de vigencia a devolverle si ya venció

    Por qué existe: el módulo no factura (lo hace el POS al cobrar), así que
    cuando el DTE se elimina o se anula por NC total la cotización queda
    marcada FACTURADA apuntando a un documento muerto — y desde ahí no se puede
    re-facturar (`cargar_cotizacion_como_ticket` exige `esta_vigente`), ni
    editar, ni anular. Era un callejón sin salida sin herramienta de salida.

    Solo administradores: es una corrección sobre el árbol documental. Los
    guards que impiden reabrir con stock afuera viven en
    `app/services/cotizacion_reapertura.py` y se revalidan bajo lock.
    """
    try:
        from .views_modulo_ventas import _usuario_es_administrador_activo
        from .services.cotizacion_reapertura import (
            evaluar_reapertura, reabrir_cotizacion as _reabrir,
        )

        if not _usuario_es_administrador_activo(request.user):
            return JsonResponse({
                'success': False,
                'error': 'Solo un administrador puede reabrir una cotización facturada'
            }, status=403)

        data = json.loads(request.body)
        cotizacion_id = data.get('cotizacion_id')
        motivo = (data.get('motivo') or '').strip()
        dias_validez = data.get('dias_validez')

        if not cotizacion_id:
            return JsonResponse({'success': False, 'error': 'Falta cotizacion_id'}, status=400)
        if len(motivo) < 5:
            return JsonResponse({
                'success': False,
                'error': 'Debe indicar el motivo de la reapertura (mínimo 5 caracteres)'
            }, status=400)

        cotizacion = get_object_or_404(
            Cotizacion_Empresa.objects
            .select_related('dte', 'sucursal', 'cliente')
            .prefetch_related('items__skus_asociados'),
            pk=cotizacion_id,
        )

        sucursal_id = _sucursal_activa_id(request)
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos sobre esta cotización'
            }, status=403)

        evaluacion = evaluar_reapertura(cotizacion)
        if not evaluacion['ok']:
            return JsonResponse({
                'success': False,
                'error': evaluacion['bloqueos'][0],
                'bloqueos': evaluacion['bloqueos'],
            }, status=400)

        ok, payload = _reabrir(cotizacion, request.user, motivo, dias_validez=dias_validez)
        if not ok:
            return JsonResponse({
                'success': False,
                'error': (payload.get('bloqueos') or ['No se pudo reabrir'])[0],
                'bloqueos': payload.get('bloqueos', []),
            }, status=400)

        return JsonResponse({
            'success': True,
            'message': (
                f'{payload["numero_cotizacion"]} reabierta: vuelve a estar '
                f'{payload["estado"]} hasta el {payload["fecha_validez"]}. '
                f'Ya se puede facturar de nuevo desde el POS.'
            ),
            **payload,
        })

    except Exception as e:
        logger.exception("Error al reabrir cotizacion")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def convertir_cotizacion_factura(request):
    """
    Pre-flight de facturación: valida que la cotización se pueda facturar y
    devuelve la URL del POS donde se emite el documento tributario real.

    ⚠️ ESTA VISTA NO MUTA LA COTIZACIÓN — es de solo lectura a propósito.

    Antes marcaba `facturada=True` con un `numero_factura` inventado
    ("F-COT-…") ANTES de redirigir al POS. Eso dejaba la cotización en un
    estado imposible: `esta_vigente` pasaba a False, así que
    `cargar_cotizacion_como_ticket` la rechazaba ("Solo se pueden facturar
    cotizaciones vigentes") y `registrar_pagos_ticket` también ("ya fue
    facturada"). Resultado: cotización FACTURADA sin DTE, sin stock
    descontado, imposible de facturar y de anular (`anular_cotizacion`
    bloquea las facturadas).

    Quien marca la cotización como facturada es `registrar_pagos_ticket`,
    DESPUÉS de emitir el DTE real (ver views_modulo_ventas.py).

    Acepta `forzar_con_pendientes=True` para permitir ítems sin SKU: esos se
    resuelven después desde "Despacho diferido".
    """
    try:
        data = json.loads(request.body)
        cotizacion_id         = data.get('cotizacion_id')
        forzar_con_pendientes = data.get('forzar_con_pendientes', False)

        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)

        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para convertir esta cotización'
            }, status=403)

        # Verificar que está vigente. El mensaje distingue el caso "venció"
        # del caso "no está vigente", porque el vencimiento SÍ tiene salida:
        # renovar los días de validez desde el botón Editar.
        if not cotizacion.esta_vigente:
            if _esta_vencida(cotizacion):
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'La cotización venció el '
                        f'{cotizacion.fecha_validez.strftime("%d/%m/%Y")} '
                        f'(hace {abs(cotizacion.dias_restantes)} días). '
                        f'Edítela y renueve los días de validez para poder facturarla.'
                    ),
                    'error_tipo': 'VENCIDA',
                    'fecha_validez': cotizacion.fecha_validez.strftime('%Y-%m-%d'),
                    'dias_vencida': abs(cotizacion.dias_restantes),
                })
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden facturar cotizaciones vigentes'
            })

        # Sin vendedor no se puede facturar: el DTE (y la comisión) terminaría
        # asignado a un vendedor arbitrario. Ver el fallback en
        # registrar_pagos_ticket.
        if not cotizacion.vendedor_id:
            return JsonResponse({
                'success': False,
                'error': (
                    'La cotización no tiene vendedor asignado. Edítela y '
                    'seleccione el vendedor antes de facturar.'
                ),
                'error_tipo': 'SIN_VENDEDOR',
            })

        # Evaluar SKU y stock contra la sucursal DE LA COTIZACIÓN: es la que
        # usará registrar_pagos_ticket al facturar, no la de la sesión.
        evaluacion = evaluar_items_cotizacion(cotizacion, cotizacion.sucursal_id)
        items_sin_sku   = evaluacion['items_sin_sku']
        items_sin_stock = evaluacion['items_sin_stock']

        if evaluacion['total_items'] == 0:
            return JsonResponse({
                'success': False,
                'error': 'La cotización no tiene ítems.'
            })

        if items_sin_stock > 0:
            detalle = ', '.join(
                f"SKU {p['sku']}: {p['stock']}/{p['requerido']}"
                for p in evaluacion['problemas_stock']
            )
            return JsonResponse({
                'success': False,
                'error': f'Stock insuficiente para facturar. {detalle}',
                'error_tipo': 'STOCK_INSUFICIENTE',
                'items_sin_stock': items_sin_stock,
                'problemas_stock': evaluacion['problemas_stock'],
            })

        # Si hay pendientes y el usuario no confirmó, pedir confirmación al frontend
        if items_sin_sku > 0 and not forzar_con_pendientes:
            return JsonResponse({
                'success': False,
                'requiere_confirmacion': True,
                'items_sin_sku': items_sin_sku,
                'message': (
                    f'{items_sin_sku} ítem(s) no tienen SKU asociado. '
                    'Puede facturar ahora y asignar el stock cuando los productos lleguen '
                    '(despacho diferido). ¿Desea continuar?'
                ),
            })

        # Traza sin cambiar el estado: la cotización sigue VIGENTE hasta que el
        # POS emita el DTE.
        Historial_Cotizacion.objects.create(
            cotizacion=cotizacion,
            usuario=request.user,
            accion='MODIFICADA',
            descripcion=(
                f'Enviada al POS para facturar por {request.user.username}'
                + (f'. {items_sin_sku} ítem(s) irán con despacho diferido.'
                   if items_sin_sku else '')
            ),
            datos_nuevos={'items_sin_sku': items_sin_sku},
            ip_address=get_client_ip(request)
        )

        logger.info(
            "Cotizacion enviada a POS para facturar numero=%s items_sin_sku=%s",
            cotizacion.numero_cotizacion, items_sin_sku,
        )

        return JsonResponse({
            'success': True,
            'message': 'Abriendo el Punto de Venta para emitir el documento',
            'redirect_url': f'/app/pos-dashboard/?cotizacion_id={cotizacion.id}',
            'items_sin_sku': items_sin_sku,
            'tiene_despacho_pendiente': items_sin_sku > 0,
        })

    except Exception as e:
        logger.exception("Error en convertir_cotizacion_factura")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== API PARA INTEGRACIÓN CON POS ====================

@login_required
@require_http_methods(["GET"])
def cargar_cotizacion_como_ticket(request, cotizacion_id):
    """
    API para cargar una cotización en formato compatible con el POS Dashboard.
    Transforma los datos de la cotización al formato esperado por ticketActual.
    """
    try:
        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)
        
        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para acceder a esta cotización'
            }, status=403)
        
        # Verificar que está vigente
        if not cotizacion.esta_vigente:
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden facturar cotizaciones vigentes'
            })
        
        logger.debug("Cargando cotizacion como ticket POS: numero=%s", cotizacion.numero_cotizacion)
        
        # Obtener datos del cliente (Empresa)
        cliente = cotizacion.cliente
        cliente_data = {
            'rut': cliente.rut or '',
            'nombre': cliente.nombre or '',
            'razon_social': cliente.razon_social or cliente.nombre or '',
            'giro': cliente.giro or '',
            'direccion': cliente.direccion or '',
            'comuna': cliente.comuna or '',
            'ciudad': cliente.ciudad or '',
            'email': cliente.correoIntercambio or cliente.correoVendedor or '',
            'email_facturacion': cliente.correoAdministrador or cliente.correoIntercambio or '',
            'telefono': cliente.contacto1 or '',  # Usar contacto1 como teléfono principal
            'telefono_secundario': cliente.contacto2 or '',
        }
        
        # Construir lista de productos en formato POS
        productos = []
        items_pendientes = []   # ítems sin SKU — despacho diferido
        items_sin_stock = []    # ítems CON SKU pero sin stock suficiente
        
        for item in cotizacion.items.all().order_by('numero_linea'):
            # SKUs asociados al ítem. Un ítem puede repartirse entre varios
            # (ej. "Balón x5" = SKU A x4 + SKU B x1).
            filas_sku = [s for s in item.skus_asociados.all() if s.producto_talla]

            if filas_sku:
                # Cada SKU va en su propia línea con SU cantidad.
                #
                # Antes, con UNA sola fila de SKU se le cargaba `item.cantidad`
                # completa "por compatibilidad histórica". Eso es exactamente el
                # bug de la cobertura parcial: un ítem de 5 uds respaldado por 1
                # sola unidad de un SKU hacía que el POS descontara 5 de ese SKU
                # y, además, `unidades_cubiertas_al_facturar` leía 1 — así que el
                # módulo seguía ofreciendo despachar las 4 restantes y las mismas
                # unidades salían dos veces. Caso real: COT-202607-0001.
                #
                # `_validar_cobertura_skus` ya impide crear/editar cotizaciones
                # con cobertura parcial, así que esto solo cambia el
                # comportamiento sobre datos históricos — que es justo donde
                # estaba el descuadre.
                cubiertas = sum((s.cantidad or 0) for s in filas_sku)
                # Cobertura exacta con una sola fila: se conserva el subtotal
                # cotizado tal cual (incluye el descuento de línea), en vez de
                # recalcularlo por unidad.
                cobertura_exacta_simple = (
                    len(filas_sku) == 1 and cubiertas == item.cantidad
                )
                for sku_rel in filas_sku:
                    pt = sku_rel.producto_talla
                    producto = pt.producto
                    cantidad_linea = (
                        item.cantidad if cobertura_exacta_simple
                        else (sku_rel.cantidad or 0)
                    )
                    if cantidad_linea <= 0:
                        continue

                    stock_actual = pt.stock_sucursal(sucursal_id)
                    if stock_actual < cantidad_linea:
                        items_sin_stock.append({
                            'descripcion': item.descripcion,
                            'sku': str(pt.sku),
                            'stock_actual': stock_actual,
                            'cantidad_requerida': cantidad_linea
                        })

                    productos.append({
                        'sku': str(pt.sku),
                        'producto_talla_id': pt.id,
                        'articulo': producto.articulo if producto else item.descripcion,
                        'descripcion': producto.descripcion if producto else '',
                        'marca': producto.atributo1.valor if producto and producto.atributo1 else '',
                        'talla': pt.talla or 'N/A',
                        'cantidad': cantidad_linea,
                        'precio': float(item.precio_unitario),
                        'precio_unitario': float(item.precio_unitario),
                        'subtotal': (
                            float(item.subtotal) if cobertura_exacta_simple
                            else float(item.precio_unitario) * cantidad_linea
                        ),
                        'stock': stock_actual,
                        'descuento_unitario': 0,
                        'costo': float(producto.costo) if producto and producto.costo else 0,
                        # Información adicional de cotización
                        'cotizacion_item_id': item.id,
                    })

                # Datos antiguos: SKUs que no cubren toda la cantidad del ítem.
                # Las unidades sin respaldo van como despacho diferido en vez de
                # descontarse de un SKU que no las tiene. Ya no se exige `multi`:
                # el caso de UNA sola fila que cubre de menos era precisamente el
                # que quedaba sin línea de pendiente y sobrevendía el SKU.
                if cubiertas < item.cantidad:
                    faltantes = item.cantidad - cubiertas
                    items_pendientes.append({
                        'detalle_id': item.id,
                        'descripcion': item.descripcion,
                        'nombre_pendiente': item.nombre_producto_pendiente or item.descripcion,
                        'sku_esperado': 'N/A',
                        'cantidad': faltantes,
                        'precio_unitario': float(item.precio_unitario),
                        'subtotal': float(item.precio_unitario) * faltantes,
                        'fecha_llegada_estimada': None,
                    })
                    productos.append({
                        'sku': None,
                        'producto_talla_id': None,
                        'articulo': item.descripcion,
                        'descripcion': item.nombre_producto_pendiente or item.descripcion,
                        'marca': '',
                        'talla': '',
                        'cantidad': faltantes,
                        'precio': float(item.precio_unitario),
                        'precio_unitario': float(item.precio_unitario),
                        'subtotal': float(item.precio_unitario) * faltantes,
                        'stock': 0,
                        'descuento_unitario': 0,
                        'costo': 0,
                        'cotizacion_item_id': item.id,
                        'es_pendiente_despacho': True,
                    })
            elif item.producto_existente:
                # Compatibilidad con modelo anterior
                pt = item.producto_existente
                producto = pt.producto
                stock_actual = pt.stock_sucursal(sucursal_id)
                
                if stock_actual < item.cantidad:
                    items_sin_stock.append({
                        'descripcion': item.descripcion,
                        'sku': str(pt.sku),
                        'stock_actual': stock_actual,
                        'cantidad_requerida': item.cantidad
                    })
                
                productos.append({
                    'sku': str(pt.sku),
                    'producto_talla_id': pt.id,
                    'articulo': producto.articulo if producto else item.descripcion,
                    'descripcion': producto.descripcion if producto else '',
                    'marca': producto.atributo1.valor if producto and producto.atributo1 else '',
                    'talla': pt.talla or 'N/A',
                    'cantidad': item.cantidad,
                    'precio': float(item.precio_unitario),
                    'precio_unitario': float(item.precio_unitario),
                    'subtotal': float(item.subtotal),
                    'stock': stock_actual,
                    'descuento_unitario': 0,
                    'costo': float(producto.costo) if producto and producto.costo else 0,
                    'cotizacion_item_id': item.id,
                })
            else:
                # Producto pendiente — se incluye en AMBAS listas:
                # 1) En 'productos' como línea manual para que aparezca en el DTE
                # 2) En 'items_pendientes' para mostrar info al usuario
                item_pendiente = {
                    'detalle_id': item.id,
                    'descripcion': item.descripcion,
                    'nombre_pendiente': item.nombre_producto_pendiente or item.descripcion,
                    'sku_esperado': item.sku_producto_pendiente or 'N/A',
                    'cantidad': item.cantidad,
                    'precio_unitario': float(item.precio_unitario),
                    'subtotal': float(item.subtotal),
                    'fecha_llegada_estimada': (
                        item.fecha_llegada_estimada.strftime('%Y-%m-%d')
                        if item.fecha_llegada_estimada else None
                    ),
                }
                items_pendientes.append(item_pendiente)

                # Agregar a productos como línea manual (sin SKU/stock, solo descripción)
                productos.append({
                    'sku': None,
                    'producto_talla_id': None,
                    'articulo': item.descripcion,
                    'descripcion': item.nombre_producto_pendiente or item.descripcion,
                    'marca': '',
                    'talla': '',
                    'cantidad': item.cantidad,
                    'precio': float(item.precio_unitario),
                    'precio_unitario': float(item.precio_unitario),
                    'subtotal': float(item.subtotal),
                    'stock': 0,
                    'descuento_unitario': 0,
                    'costo': 0,
                    'cotizacion_item_id': item.id,
                    'es_pendiente_despacho': True,
                })
        
        # Calcular totales (solo ítems con SKU)
        total_items = sum(p['cantidad'] for p in productos)
        subtotal = sum(p['subtotal'] for p in productos)
        
        totales = {
            'items': total_items,
            'subtotal': subtotal,
            'descuento': float(cotizacion.descuento),
            'total': float(cotizacion.total)
        }
        
        # Construir objeto ticket compatible con POS
        # Nota: numero_cotizacion ya tiene formato "COT-202601-0001", no duplicar prefijo
        ticket_data = {
            'correlativo': cotizacion.numero_cotizacion,
            'cotizacion_id': cotizacion.id,
            'numero_cotizacion': cotizacion.numero_cotizacion,
            'es_cotizacion': True,
            'cliente': cliente_data,
            'productos': productos,
            'items_pendientes': items_pendientes,
            'totales': totales,
            'observaciones': cotizacion.observaciones or '',
            'observaciones_adicionales': cotizacion.descripcion or '',
            'vendedor_nombre': cotizacion.vendedor.nombre if cotizacion.vendedor else '',
            'fecha_emision': cotizacion.fecha_emision.strftime('%Y-%m-%d'),
            'fecha_validez': cotizacion.fecha_validez.strftime('%Y-%m-%d'),
            'dias_restantes': cotizacion.dias_restantes,
        }
        
        # Advertencias de stock insuficiente (ya no incluye pendientes como error)
        advertencias = []
        if items_sin_stock:
            for item in items_sin_stock:
                advertencias.append(
                    f"'{item['descripcion']}' (SKU: {item['sku']}): "
                    f"Stock actual {item['stock_actual']}, requerido {item['cantidad_requerida']}"
                )
        
        # Información sobre ítems pendientes de despacho
        avisos_pendientes = []
        for item in items_pendientes:
            avisos_pendientes.append(
                f"'{item['descripcion']}' (x{item['cantidad']}) — pendiente de SKU/despacho diferido"
            )

        logger.info(
            "Cotizacion cargada como ticket POS: numero=%s productos=%s pendientes=%s total=%s",
            cotizacion.numero_cotizacion,
            len(productos),
            len(items_pendientes),
            totales['total'],
        )
        
        return JsonResponse({
            'success': True,
            'ticket': ticket_data,
            'advertencias': advertencias,
            'tiene_advertencias': len(advertencias) > 0,
            'avisos_pendientes': avisos_pendientes,
            'tiene_pendientes': len(items_pendientes) > 0,
            'total_pendientes': len(items_pendientes),
        })
        
    except Exception as e:
        logger.exception("Error en cargar_cotizacion_como_ticket")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== DESPACHO DIFERIDO ====================

@login_required
@require_http_methods(["POST"])
def asignar_sku_pendiente(request):
    """
    Asigna un SKU (Producto_Talla) a un ítem pendiente de una cotización ya FACTURADA.

    Body JSON:
        detalle_id        – ID de Cotizacion_Empresa_Detalle
        producto_talla_id – ID de Producto_Talla a vincular
        cantidad          – cantidad a despachar (debe ser <= item.cantidad)

    Al asignar:
    1. Vincula el Producto_Talla al detalle.
    2. Crea Cotizacion_Empresa_Detalle_SKU.
    3. Decrementa Producto_Talla.stock (bajo lock) y consume lotes FIFO.
    4. Crea Movimientos_Producto (EGRESO / DESPACHO_COTIZACION) enlazado al DTE.
    5. Completa la línea del DTE que quedó sin SKU (productoTalla + costo).
    6. Registra Historial_Cotizacion (SKU_ASIGNADO).
    7. Actualiza estado_despacho de la cotización.

    El inventario se mueve SIEMPRE contra `cotizacion.sucursal`, no contra la
    sucursal de la sesión: la de sesión solo decide permisos. Antes se validaba
    el stock contra una y se descontaba contra la otra.
    """
    try:
        data = json.loads(request.body)
        detalle_id        = data.get('detalle_id')
        producto_talla_id = data.get('producto_talla_id')
        cantidad          = int(data.get('cantidad', 1))

        if not detalle_id or not producto_talla_id:
            return JsonResponse({'success': False, 'error': 'Faltan parámetros requeridos'}, status=400)

        detalle = get_object_or_404(Cotizacion_Empresa_Detalle, pk=detalle_id)
        cotizacion = detalle.cotizacion

        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')

        # Validar pertenencia a sucursal
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({'success': False, 'error': 'No tiene permisos sobre esta cotización'}, status=403)

        # Sucursal autoritativa para todo el movimiento de inventario.
        sucursal_inventario_id = cotizacion.sucursal_id

        # Validar que la cotización esté FACTURADA
        if cotizacion.estado != Cotizacion_Empresa.ESTADO_FACTURADA:
            return JsonResponse({
                'success': False,
                'error': 'Solo se puede asignar SKU a cotizaciones ya facturadas'
            }, status=400)

        # ...y que el documento que la respalda siga vivo. Sin este guard se
        # podía sacar stock contra un DTE eliminado o anulado: mercadería
        # entregada sin documento y sin forma de facturarla.
        doc_ok, doc_error = _documento_vivo(cotizacion)
        if not doc_ok:
            return JsonResponse({'success': False, 'error': doc_error}, status=400)

        # Validar contra el SALDO pendiente en unidades, no contra flags.
        # Antes un despacho parcial (facturado 5, despachado 2) cerraba el
        # ítem con es_producto_pendiente=False y las 3 uds restantes quedaban
        # sin salida de stock para siempre (descuadre facturado vs sacado).
        saldo_pendiente = detalle.unidades_pendientes_despacho
        if saldo_pendiente <= 0:
            if detalle.nacio_pendiente:
                error_msg = 'Este ítem ya está completamente despachado'
            else:
                error_msg = 'Este ítem tenía SKU al facturar (el stock salió con el ticket)'
            return JsonResponse({'success': False, 'error': error_msg}, status=400)

        if cantidad <= 0 or cantidad > saldo_pendiente:
            return JsonResponse({
                'success': False,
                'error': (
                    f'La cantidad debe estar entre 1 y {saldo_pendiente} '
                    f'(facturadas: {detalle.cantidad}, ya despachadas: '
                    f'{detalle.unidades_despachadas_post_factura})'
                )
            }, status=400)

        producto_talla = get_object_or_404(
            Producto_Talla.objects.select_related('producto'), pk=producto_talla_id
        )

        # El producto debe pertenecer a la sucursal de la cotización: el stock
        # es por sucursal (Producto_Talla.stock_sucursal devuelve 0 si el
        # producto es de otra), así que despachar uno ajeno descontaría de un
        # inventario que no corresponde.
        if (producto_talla.producto
                and producto_talla.producto.sucursal_id != sucursal_inventario_id):
            return JsonResponse({
                'success': False,
                'error': (
                    f'El SKU {producto_talla.sku} no pertenece a la sucursal de '
                    f'la cotización ({cotizacion.sucursal.alias}).'
                )
            }, status=400)

        from django.db import transaction
        with transaction.atomic():
            # Relectura bajo lock: sin esto, dos despachos concurrentes del
            # mismo SKU leen el mismo stock y ambos pasan la validación.
            producto_talla = (
                Producto_Talla.objects
                .select_for_update()
                .select_related('producto')
                .get(pk=producto_talla.pk)
            )
            stock_actual = producto_talla.stock_sucursal(sucursal_inventario_id)
            if stock_actual < cantidad:
                return JsonResponse({
                    'success': False,
                    'error': f'Stock insuficiente. Disponible: {stock_actual}, requerido: {cantidad}'
                }, status=400)

            costo_unitario = (
                producto_talla.producto.costo
                if producto_talla.producto and producto_talla.producto.costo
                else 0
            )

            # 1. Crear Cotizacion_Empresa_Detalle_SKU con marca post-factura
            # (la marca separa estos despachos de los SKUs asociados al crear
            # la cotización — es la base de la cuadratura por unidades).
            Cotizacion_Empresa_Detalle_SKU.objects.create(
                detalle=detalle,
                producto_talla=producto_talla,
                cantidad=cantidad,
                costo_unitario=costo_unitario,
                precio_unitario=detalle.precio_unitario,
                asignado_post_factura=True,
            )

            # 2. Cerrar el ítem SOLO si con este despacho se completa el saldo.
            # Si queda saldo, el ítem sigue pendiente y reaparece en el modal
            # con las unidades restantes.
            saldo_restante = saldo_pendiente - cantidad
            item_completado = saldo_restante == 0
            if item_completado:
                detalle.producto_existente = producto_talla
                detalle.es_producto_pendiente = False
                detalle.sku_asignado_post_factura = True
                detalle.fecha_asignacion_sku = timezone.now()
                detalle.usuario_asignacion_sku = request.user
                # recalcular_cotizacion=False: asignar el SKU no cambia montos, y
                # recalcular acá reescribiría los totales de un documento ya emitido.
                detalle.save(
                    update_fields=[
                        'producto_existente', 'es_producto_pendiente',
                        'sku_asignado_post_factura', 'fecha_asignacion_sku',
                        'usuario_asignacion_sku',
                    ],
                    recalcular_cotizacion=False,
                )

            # 3. Decrementar stock (y consumir lotes FIFO para no dejar la
            # capa de lotes inflada — best-effort, no bloquea el despacho)
            producto_talla.stock -= cantidad
            producto_talla.save(update_fields=['stock'])
            try:
                from app.services.inventario_service import consumir_lotes_fifo
                consumir_lotes_fifo(producto_talla, cantidad, usar_lock=True)
            except Exception as e_lotes:
                logger.warning(
                    'Despacho cotización: lotes FIFO no consumidos sku=%s cantidad=%s: %s',
                    producto_talla.sku, cantidad, e_lotes,
                )

            # 4. Crear movimiento de inventario, enlazado al DTE emitido.
            # Sin `dte` el egreso quedaba huérfano del documento y ningún
            # reporte que cruce movimientos por DTE lo veía.
            dte_cotizacion = cotizacion.dte
            Movimientos_Producto.objects.create(
                ProductoTalla=producto_talla,
                dte=dte_cotizacion,
                sucursal_origen=cotizacion.sucursal,
                cantidad=-cantidad,
                costo=costo_unitario,
                precio=int(detalle.precio_unitario),
                concepto='DESPACHO_COTIZACION',
                tipo_movimiento='EGRESO',
                estado='COMPLETADO',
                responsable=request.user.get_full_name() or request.user.username,
                referencia_externa=cotizacion.numero_cotizacion,
                observaciones=(
                    f'Despacho diferido cotización {cotizacion.numero_cotizacion} - '
                    f'{detalle.descripcion[:80]}'
                    + (f' - DTE {dte_cotizacion.tipo_documento} #{dte_cotizacion.numero_documento}'
                       if dte_cotizacion else '')
                ),
                fecha=timezone.localdate(),
                hora=timezone.localtime().time(),
            )

            # 4b. Completar la línea del DTE que se emitió sin SKU — SOLO al
            # completar el ítem. Si no se hace, `Dte_Productos.costo` queda en
            # 0 y el margen del documento sale inflado para siempre. Con
            # despacho parcial la línea espera: se completa cuando salen todas
            # las unidades, usando el SKU principal (mayor cantidad despachada)
            # y el costo PROMEDIO PONDERADO de los despachos (la línea puede
            # haberse cubierto con varios SKUs de costos distintos).
            lineas_dte_actualizadas = 0
            if dte_cotizacion and item_completado:
                despachos = list(
                    detalle.skus_asociados
                    .filter(asignado_post_factura=True)
                    .select_related('producto_talla__producto')
                )
                total_uds = sum(d.cantidad for d in despachos) or 1
                costo_ponderado = int(round(
                    sum(float(d.costo_unitario) * d.cantidad for d in despachos) / total_uds
                ))
                sku_principal = max(despachos, key=lambda d: d.cantidad).producto_talla or producto_talla
                lineas_dte_actualizadas = Dte_Productos.objects.filter(
                    dte=dte_cotizacion,
                    cotizacion_detalle_id=detalle.id,
                    es_pendiente_despacho=True,
                ).update(
                    productoTalla=sku_principal,
                    costo=costo_ponderado,
                    sobreprecio=(
                        sku_principal.producto.sobreprecio
                        if sku_principal.producto and sku_principal.producto.sobreprecio
                        else 0
                    ),
                    es_pendiente_despacho=False,
                )
                if not lineas_dte_actualizadas:
                    # DTE anterior a la migración (sin cotizacion_detalle_id) o
                    # línea ya resuelta: no es un error, pero conviene saberlo.
                    logger.info(
                        'Despacho diferido: sin línea de DTE para completar '
                        'cotizacion=%s detalle=%s dte=%s',
                        cotizacion.numero_cotizacion, detalle.id,
                        dte_cotizacion.numero_documento,
                    )
            elif not dte_cotizacion:
                logger.warning(
                    'Despacho diferido sin DTE enlazado cotizacion=%s '
                    '(facturada antes de la FK Cotizacion_Empresa.dte)',
                    cotizacion.numero_cotizacion,
                )
            # (si hay DTE pero el ítem quedó parcial, la línea se completa
            # cuando salga la última unidad)

            # 5. Registrar historial (con saldo para poder auditar parciales)
            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='SKU_ASIGNADO',
                descripcion=(
                    f'SKU {producto_talla.sku} despachado para "{detalle.descripcion[:60]}" '
                    f'(x{cantidad} de {detalle.cantidad} facturadas'
                    + (f', quedan {saldo_restante} pendientes' if saldo_restante else ', ítem completado')
                    + f'). Stock descontado en {cotizacion.sucursal.alias}.'
                    + (f' Línea del DTE #{dte_cotizacion.numero_documento} completada.'
                       if lineas_dte_actualizadas else '')
                ),
                datos_nuevos={
                    'detalle_id': detalle.id,
                    'producto_talla_id': producto_talla.id,
                    'sku': str(producto_talla.sku),
                    'cantidad': cantidad,
                    'saldo_pendiente': saldo_restante,
                    'sucursal_id': cotizacion.sucursal_id,
                    'dte_id': dte_cotizacion.id if dte_cotizacion else None,
                    'lineas_dte_actualizadas': lineas_dte_actualizadas,
                },
                ip_address=get_client_ip(request),
            )

            # 6. Recalcular estado_despacho de la cotización
            cotizacion.actualizar_estado_despacho()

            # Si todos los ítems están despachados, registrar historial de completado
            if cotizacion.estado_despacho == Cotizacion_Empresa.DESPACHO_COMPLETADO:
                Historial_Cotizacion.objects.create(
                    cotizacion=cotizacion,
                    usuario=request.user,
                    accion='DESPACHO_COMPLETADO',
                    descripcion='Todos los ítems pendientes han sido despachados. Despacho completado.',
                    ip_address=get_client_ip(request),
                )

        return JsonResponse({
            'success': True,
            'message': (
                f'SKU {producto_talla.sku} despachado (x{cantidad})'
                + (f'. Quedan {saldo_restante} unidades pendientes de este ítem'
                   if saldo_restante else '')
            ),
            'estado_despacho': cotizacion.estado_despacho,
            'despacho_completado': cotizacion.estado_despacho == Cotizacion_Empresa.DESPACHO_COMPLETADO,
            'item_completado': item_completado,
            'saldo_pendiente_item': saldo_restante,
            'sku': str(producto_talla.sku),
            'nombre_producto': producto_talla.producto.articulo if producto_talla.producto else '',
        })

    except Exception as e:
        logger.exception("Error al asignar SKU a detalle de cotizacion")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def revertir_sku_despachado(request):
    """
    Deshace una asignación de SKU post-factura mal hecha (SKU equivocado).

    Body JSON:
        detalle_id – ID de Cotizacion_Empresa_Detalle
        motivo     – texto obligatorio, queda en el historial
        sku_id     – opcional: ID de Cotizacion_Empresa_Detalle_SKU a revertir.
                     Sin él se revierten TODOS los despachos post-factura del
                     ítem (comportamiento histórico).

    No existía forma de corregir un SKU asignado por error: `editar_cotizacion`
    bloquea las cotizaciones facturadas y `asignar_sku_pendiente` solo acepta
    ítems sin SKU. Esta vista devuelve el ítem a "pendiente" para poder volver a
    asignarlo con el flujo normal.

    Reversa SELECTIVA (`sku_id`): un ítem despachado en dos tandas con SKUs
    distintos obligaba a revertir las dos para corregir una, y había que rehacer
    el despacho que estaba bien. Con `sku_id` se revierte solo esa fila y el
    ítem queda con el saldo pendiente correspondiente.

    Compensa en vez de borrar: crea un Movimientos_Producto de INGRESO que
    revierte el egreso (los movimientos son base de auditoría, no se eliminan).

    OJO: acá NO se exige que el DTE siga vivo, al revés que en
    `asignar_sku_pendiente`. Revertir los despachos es justamente el paso previo
    obligatorio para reabrir una cotización cuyo documento se eliminó; exigir un
    documento vivo dejaría esa mercadería trabada para siempre.

    Solo administradores: es una corrección sobre un documento ya emitido.
    """
    try:
        from .views_modulo_ventas import _usuario_es_administrador_activo

        if not _usuario_es_administrador_activo(request.user):
            return JsonResponse({
                'success': False,
                'error': 'Solo un administrador puede corregir un SKU ya despachado'
            }, status=403)

        data = json.loads(request.body)
        detalle_id = data.get('detalle_id')
        motivo = (data.get('motivo') or '').strip()
        sku_id = data.get('sku_id')

        if not detalle_id:
            return JsonResponse({'success': False, 'error': 'Falta detalle_id'}, status=400)
        if not motivo:
            return JsonResponse({
                'success': False,
                'error': 'Debe indicar el motivo de la corrección'
            }, status=400)

        detalle = get_object_or_404(Cotizacion_Empresa_Detalle, pk=detalle_id)
        cotizacion = detalle.cotizacion

        sucursal_id = _sucursal_activa_id(request)
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos sobre esta cotización'
            }, status=403)

        # Solo se revierten asignaciones hechas DESPUÉS de facturar: un SKU que
        # venía en la cotización original se corrige editándola antes de facturar.
        # OJO: con despacho parcial el ítem puede tener despachos post-factura
        # sin estar cerrado (sku_asignado_post_factura=False), por eso el gate
        # mira las FILAS post-factura y no el flag del detalle.
        if not detalle.skus_asociados.filter(asignado_post_factura=True).exists():
            return JsonResponse({
                'success': False,
                'error': 'Este ítem no tiene despachos post-factura para revertir'
            }, status=400)

        from django.db import transaction
        with transaction.atomic():
            # Solo las filas post-factura: las asociaciones creadas al cotizar
            # no movieron stock y no deben reintegrarse.
            filas_qs = (
                Cotizacion_Empresa_Detalle_SKU.objects
                .filter(detalle=detalle, asignado_post_factura=True)
                .select_related('producto_talla__producto')
            )
            if sku_id:
                filas_qs = filas_qs.filter(pk=sku_id)
            filas_sku = list(filas_qs)

            if sku_id and not filas_sku:
                return JsonResponse({
                    'success': False,
                    'error': (
                        'Ese despacho no existe o no pertenece a este ítem '
                        '(puede haberse revertido ya).'
                    )
                }, status=400)

            reintegrados = []
            for fila in filas_sku:
                pt = fila.producto_talla
                if not pt:
                    continue

                pt_lock = (
                    Producto_Talla.objects
                    .select_for_update()
                    .select_related('producto')
                    .get(pk=pt.pk)
                )
                pt_lock.stock = (pt_lock.stock or 0) + fila.cantidad
                pt_lock.save(update_fields=['stock'])

                Movimientos_Producto.objects.create(
                    ProductoTalla=pt_lock,
                    dte=cotizacion.dte,
                    sucursal_destino=cotizacion.sucursal,
                    cantidad=fila.cantidad,
                    costo=int(fila.costo_unitario or 0),
                    precio=int(fila.precio_unitario or 0),
                    concepto='DESPACHO_COTIZACION',
                    tipo_movimiento='INGRESO',
                    estado='COMPLETADO',
                    responsable=request.user.get_full_name() or request.user.username,
                    referencia_externa=cotizacion.numero_cotizacion,
                    observaciones=(
                        f'Reversa de despacho diferido cotización '
                        f'{cotizacion.numero_cotizacion} - {detalle.descripcion[:60]} - '
                        f'Motivo: {motivo[:80]}'
                    ),
                    fecha=timezone.localdate(),
                    hora=timezone.localtime().time(),
                )
                reintegrados.append({'sku': str(pt_lock.sku), 'cantidad': fila.cantidad})

            # Volver la línea del DTE a "pendiente de despacho".
            # Se hace SIEMPRE que se revierta algo: la línea se completa (con
            # SKU principal y costo ponderado) recién cuando el ítem queda
            # totalmente despachado, así que mientras haya saldo tiene que
            # estar marcada como pendiente.
            if cotizacion.dte:
                Dte_Productos.objects.filter(
                    dte=cotizacion.dte,
                    cotizacion_detalle_id=detalle.id,
                ).update(
                    productoTalla=None,
                    costo=0,
                    sobreprecio=0,
                    es_pendiente_despacho=True,
                )

            # Los vínculos SKU no son auditoría (el historial guarda el rastro):
            # se eliminan SOLO los post-factura revertidos para que esas
            # unidades vuelvan a estar realmente pendientes (las asociaciones
            # originales de la cotización no movieron stock y se conservan).
            Cotizacion_Empresa_Detalle_SKU.objects.filter(
                pk__in=[f.pk for f in filas_sku]
            ).delete()

            # ¿Quedó algún despacho post-factura vivo? Con reversa selectiva el
            # ítem puede seguir parcialmente despachado: en ese caso NO se lo
            # devuelve entero a "pendiente", solo se reabre el saldo revertido.
            # Marcarlo pendiente igual borraría el rastro del despacho que sigue
            # siendo válido y descuadraría la cuadratura por unidades.
            quedan_despachos = (
                Cotizacion_Empresa_Detalle_SKU.objects
                .filter(detalle=detalle, asignado_post_factura=True)
                .exists()
            )

            if quedan_despachos:
                # El ítem vuelve a tener saldo pero sigue teniendo despachos
                # vigentes.
                #
                # `sku_asignado_post_factura` NO se toca: junto con
                # `es_producto_pendiente` forma `nacio_pendiente`, que es lo que
                # le dice a la cuadratura "este ítem se facturó SIN SKU, así que
                # con el ticket no salió nada". Bajarlo acá hacía que
                # `unidades_cubiertas_al_facturar` cayera en la rama legacy y
                # devolviera `cantidad` completa (porque `producto_existente`
                # estaba seteado): el saldo pendiente salía 0 y las unidades
                # revertidas quedaban invisibles.
                #
                # `producto_existente` sí se limpia: apuntaba al SKU que se
                # acaba de revertir.
                detalle.producto_existente = None
                detalle.es_producto_pendiente = True
                detalle.save(
                    update_fields=['producto_existente', 'es_producto_pendiente'],
                    recalcular_cotizacion=False,
                )
            else:
                detalle.producto_existente = None
                detalle.es_producto_pendiente = True
                detalle.sku_asignado_post_factura = False
                detalle.fecha_asignacion_sku = None
                detalle.usuario_asignacion_sku = None
                detalle.save(
                    update_fields=[
                        'producto_existente', 'es_producto_pendiente',
                        'sku_asignado_post_factura', 'fecha_asignacion_sku',
                        'usuario_asignacion_sku',
                    ],
                    recalcular_cotizacion=False,
                )

            # Si el despacho ya tenía el OK del administrador, la reversa lo
            # invalida: la cuadratura dejó de estar vigente.
            validacion_invalidada = cotizacion.invalidar_validacion_despacho()

            detalle_reintegro = ', '.join(
                '{} x{}'.format(r['sku'], r['cantidad']) for r in reintegrados
            ) or 'ninguno'

            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='SKU_ASIGNADO',
                descripcion=(
                    f'REVERSA de SKU en "{detalle.descripcion[:60]}". '
                    f'Stock reintegrado: {detalle_reintegro}. '
                    f'Motivo: {motivo[:120]}'
                    + (' Se invalidó el OK de despacho del administrador.'
                       if validacion_invalidada else '')
                ),
                datos_anteriores={'reintegrados': reintegrados},
                datos_nuevos={
                    'detalle_id': detalle.id,
                    'motivo': motivo[:200],
                    'validacion_invalidada': validacion_invalidada,
                },
                ip_address=get_client_ip(request),
            )

            cotizacion.actualizar_estado_despacho()

        logger.info(
            'SKU despachado revertido cotizacion=%s detalle=%s por=%s motivo=%s',
            cotizacion.numero_cotizacion, detalle.id, request.user.username, motivo[:60],
        )

        return JsonResponse({
            'success': True,
            'message': 'SKU revertido. El ítem volvió a quedar pendiente de despacho.',
            'reintegrados': reintegrados,
            'estado_despacho': cotizacion.estado_despacho,
        })

    except Exception as e:
        logger.exception("Error al revertir SKU despachado")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def validar_despacho_cotizacion(request):
    """
    OK final del Administrador al despacho de una cotización facturada.

    Body JSON:
        cotizacion_id – ID de Cotizacion_Empresa

    Confirma formalmente que las unidades FACTURADAS coinciden con las
    DESPACHADAS (salidas de stock). Requiere el permiso granular
    `gestion_cotizaciones.puede_aprobar`. La validación se invalida sola si
    después se revierte un despacho (ver revertir_sku_despachado).
    """
    try:
        if not _puede_validar_despacho(request):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permiso para validar despachos (gestion_cotizaciones.puede_aprobar)'
            }, status=403)

        data = json.loads(request.body)
        cotizacion_id = data.get('cotizacion_id')
        if not cotizacion_id:
            return JsonResponse({'success': False, 'error': 'Falta cotizacion_id'}, status=400)

        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)

        sucursal_id = _sucursal_activa_id(request)
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos sobre esta cotización'
            }, status=403)

        if not cotizacion.facturada:
            return JsonResponse({
                'success': False,
                'error': 'Solo se puede validar el despacho de una cotización facturada'
            }, status=400)

        if cotizacion.despacho_validado:
            return JsonResponse({
                'success': False,
                'error': 'El despacho de esta cotización ya fue validado'
            }, status=400)

        facturadas = cotizacion.unidades_facturadas
        pendientes = cotizacion.unidades_pendientes_despacho
        despachadas = facturadas - pendientes

        if pendientes > 0:
            return JsonResponse({
                'success': False,
                'error': (
                    f'La cuadratura no cierra: facturadas {facturadas} uds, '
                    f'despachadas {despachadas} uds ({pendientes} pendientes). '
                    f'Complete el despacho antes de validar.'
                ),
                'unidades_facturadas': facturadas,
                'unidades_despachadas': despachadas,
                'unidades_pendientes': pendientes,
            }, status=400)

        from django.db import transaction
        with transaction.atomic():
            cotizacion.despacho_validado = True
            cotizacion.despacho_validado_por = request.user
            cotizacion.fecha_validacion_despacho = timezone.now()
            # Asegurar coherencia del estado por si quedó desactualizado.
            cotizacion.estado_despacho = Cotizacion_Empresa.DESPACHO_COMPLETADO
            cotizacion.save(update_fields=[
                'despacho_validado', 'despacho_validado_por',
                'fecha_validacion_despacho', 'estado_despacho',
            ])

            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='DESPACHO_VALIDADO',
                descripcion=(
                    f'OK del administrador al despacho: {facturadas} uds facturadas '
                    f'= {despachadas} uds despachadas. Cuadratura confirmada.'
                ),
                datos_nuevos={
                    'unidades_facturadas': facturadas,
                    'unidades_despachadas': despachadas,
                },
                ip_address=get_client_ip(request),
            )

        logger.info(
            'Despacho validado cotizacion=%s por=%s (%s uds)',
            cotizacion.numero_cotizacion, request.user.username, facturadas,
        )

        return JsonResponse({
            'success': True,
            'message': f'Despacho de {cotizacion.numero_cotizacion} validado ({facturadas} uds)',
            'despacho_validado': True,
            'validado_por': request.user.get_full_name() or request.user.username,
        })

    except Exception as e:
        logger.exception("Error al validar despacho de cotizacion")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ==================== APIs DE BÚSQUEDA ====================

@login_required
@require_http_methods(["GET"])
def buscar_productos_cotizacion(request):
    """
    API para buscar productos para asociar a la cotización
    Incluye información de costo y margen para análisis de rentabilidad
    """
    try:
        query = request.GET.get('q', '').strip()
        filtro_stock = request.GET.get('stock', 'todos')  # 'todos', 'con_stock', 'sin_stock'
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')

        logger.debug(
            "Busqueda productos cotizacion: query=%s filtro_stock=%s sucursal_id=%s",
            query,
            filtro_stock,
            sucursal_id,
        )

        if not query or len(query) < 2:
            return JsonResponse({
                'success': True,
                'productos': []
            })

        # ------------------------------------------------------------------
        # Búsqueda MULTI-TÉRMINO.
        #
        # Antes el query entero se buscaba como una sola cadena, así que
        # "nike 38" o "zapatilla running negro" no encontraban nada: ningún
        # campo contiene los dos términos juntos. Ahora cada término tiene que
        # calzar en ALGÚN campo (AND entre términos, OR entre campos), que es
        # como busca el modal de "Crear Manual".
        #
        # Importa especialmente en el despacho diferido: el operador tiene la
        # descripción libre del ítem cotizado ("BALON FUTBOL Nº5") y necesita
        # llegar al SKU real con esas palabras.
        # ------------------------------------------------------------------
        terminos = [t for t in query.split() if t]
        filtros = Q()
        for termino in terminos:
            por_termino = (
                Q(producto__articulo__icontains=termino)
                | Q(producto__descripcion__icontains=termino)
                # Marca
                | Q(producto__atributo1__valor__icontains=termino)
                | Q(talla__icontains=termino)
            )
            # SKU es BigIntegerField: solo tiene sentido si el término es numérico.
            if termino.isdigit():
                por_termino |= Q(sku=int(termino))  # exacta
                por_termino |= Q(
                    sku__gte=int(termino), sku__lt=int(termino) + 10000
                )  # rango aproximado (prefijo)
            filtros &= por_termino

        # ✅ FILTRAR SOLO PRODUCTOS DE LA SUCURSAL ACTIVA
        if sucursal_id:
            filtros &= Q(producto__sucursal_id=int(sucursal_id))

        # Ejecutar búsqueda base
        productos_query = Producto_Talla.objects.filter(filtros).select_related(
            'producto',
            'producto__atributo1',  # Marca
            'producto__atributo2',  # Color
            'producto__categoria'
        ).order_by('producto__articulo').distinct()
        
        # Convertir a lista y calcular stock por sucursal para cada producto
        # Esto es necesario porque stock_sucursal() es un método de Python, no un campo de BD
        productos_con_stock = []
        for pt in productos_query:
            stock_sucursal = pt.stock_sucursal(sucursal_id) if sucursal_id else (pt.stock or 0)
            productos_con_stock.append({
                'producto_talla': pt,
                'stock_sucursal': stock_sucursal
            })
        
        # Aplicar filtro de stock según la sucursal actual
        if filtro_stock == 'con_stock':
            productos_con_stock = [p for p in productos_con_stock if p['stock_sucursal'] > 0]
        elif filtro_stock == 'sin_stock':
            productos_con_stock = [p for p in productos_con_stock if p['stock_sucursal'] <= 0]
        
        # Ordenar: el SKU exacto primero (si el operador lo pegó tal cual, es
        # lo que busca), después los que tienen stock en la sucursal, después
        # por nombre.
        sku_exacto = int(query) if query.isdigit() else None
        productos_con_stock.sort(key=lambda x: (
            0 if (sku_exacto is not None and x['producto_talla'].sku == sku_exacto) else 1,
            -x['stock_sucursal'],
            x['producto_talla'].producto.articulo if x['producto_talla'].producto else '',
        ))
        
        # Paginación
        pagina = int(request.GET.get('pagina', 1))
        por_pagina = int(request.GET.get('por_pagina', 12))  # 12 por página por defecto
        
        total_productos = len(productos_con_stock)
        total_paginas = (total_productos + por_pagina - 1) // por_pagina  # Redondeo hacia arriba
        
        # Calcular offset
        offset = (pagina - 1) * por_pagina
        productos_paginados = productos_con_stock[offset:offset + por_pagina]

        logger.debug(
            "Productos cotizacion encontrados: total=%s pagina=%s total_paginas=%s sucursal_id=%s",
            total_productos,
            pagina,
            total_paginas,
            sucursal_id,
        )
        
        # Serializar
        productos_data = []
        for item in productos_paginados:
            pt = item['producto_talla']
            # Stock ya calculado por sucursal
            stock = item['stock_sucursal']
            
            # Obtener precios y costo
            costo = 0
            sobreprecio = 0
            precio_venta = 0
            precio_sugerido = 0
            
            if pt.producto:
                costo = int(pt.producto.costo) if pt.producto.costo else 0
                sobreprecio = int(pt.producto.sobreprecio) if pt.producto.sobreprecio else 0
                precio_venta = int(pt.producto.precioventa) if pt.producto.precioventa else 0
                precio_sugerido = int(pt.producto.precioSugerido) if pt.producto.precioSugerido else precio_venta
            
            # Calcular margen real (basado en precio de venta)
            margen_porcentaje = 0
            if precio_venta > 0 and costo > 0:
                margen_porcentaje = round(((precio_venta - costo) / precio_venta) * 100, 1)
            
            # Calcular margen sobre costo
            markup_porcentaje = 0
            if costo > 0:
                markup_porcentaje = round(((precio_venta - costo) / costo) * 100, 1)
            
            # Obtener marca desde atributo1
            marca = 'Sin marca'
            if pt.producto and pt.producto.atributo1:
                marca = pt.producto.atributo1.valor
            
            # Obtener color desde atributo2
            color = ''
            if pt.producto and pt.producto.atributo2:
                color = pt.producto.atributo2.valor
            
            # Construir nombre descriptivo
            nombre = pt.producto.articulo if pt.producto else 'Sin nombre'
            if color:
                nombre = f"{nombre} - {color}"
            
            # Obtener descripción del producto
            descripcion = ''
            if pt.producto and pt.producto.descripcion:
                descripcion = pt.producto.descripcion
            
            producto_info = {
                'id': pt.id,
                'nombre': nombre,
                'descripcion': descripcion,
                'sku': str(pt.sku),
                'marca': marca,
                'talla': pt.talla if pt.talla else 'N/A',
                'stock': stock,
                # Precios y costos
                'costo': costo,
                'sobreprecio': sobreprecio,
                'precio': precio_venta,
                'precio_sugerido': precio_sugerido,
                # Márgenes calculados
                'margen_porcentaje': margen_porcentaje,
                'markup_porcentaje': markup_porcentaje,
                'utilidad': precio_venta - costo,  # Ganancia por unidad
            }
            
            productos_data.append(producto_info)
            logger.debug(
                "Producto cotizacion listado: nombre=%s sku=%s stock=%s costo=%s precio_venta=%s margen=%s",
                producto_info['nombre'],
                producto_info['sku'],
                stock,
                costo,
                precio_venta,
                margen_porcentaje,
            )
        
        return JsonResponse({
            'success': True,
            'productos': productos_data,
            'paginacion': {
                'pagina_actual': pagina,
                'total_paginas': total_paginas,
                'total_productos': total_productos,
                'por_pagina': por_pagina,
                'tiene_anterior': pagina > 1,
                'tiene_siguiente': pagina < total_paginas
            }
        })
        
    except Exception as e:
        logger.exception("Error en buscar_productos_cotizacion")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== FUNCIONES AUXILIARES ====================

def generar_numero_cotizacion(sucursal):
    """
    Genera un número único de cotización para el mes en curso.

    Toma el último número del prefijo bajo `select_for_update()`: es un
    read-max-then-write sobre un campo `unique=True`, así que sin lock dos
    guardados simultáneos (o un doble clic) calculaban el mismo número y el
    segundo reventaba con IntegrityError 500.

    Debe llamarse dentro de una transacción (el lock vive hasta el commit).
    """
    fecha_actual = timezone.localdate()
    prefijo = f"COT-{fecha_actual.year}{fecha_actual.month:02d}"

    ultima_cotizacion = (
        Cotizacion_Empresa.objects
        .select_for_update()
        .filter(numero_cotizacion__startswith=prefijo)
        .order_by('-numero_cotizacion')
        .first()
    )

    if ultima_cotizacion:
        try:
            ultimo_numero = int(ultima_cotizacion.numero_cotizacion.split('-')[-1])
        except (ValueError, IndexError):
            logger.warning(
                "Número de cotización con formato inesperado: %s",
                ultima_cotizacion.numero_cotizacion,
            )
            ultimo_numero = Cotizacion_Empresa.objects.filter(
                numero_cotizacion__startswith=prefijo
            ).count()
        nuevo_numero = ultimo_numero + 1
    else:
        nuevo_numero = 1

    return f"{prefijo}-{nuevo_numero:04d}"


def get_client_ip(request):
    """
    Obtiene la IP del cliente
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


@login_required
@require_http_methods(["POST"])
def actualizar_email_cliente(request):
    """
    API para actualizar rápidamente el email de un cliente
    """
    try:
        data = json.loads(request.body)
        cliente_id = data.get('cliente_id')
        email = data.get('email', '').strip()
        
        if not cliente_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de cliente no proporcionado'
            })
        
        if not email:
            return JsonResponse({
                'success': False,
                'error': 'Email no proporcionado'
            })
        
        # Validar formato de email básico
        import re
        email_regex = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
        if not re.match(email_regex, email):
            return JsonResponse({
                'success': False,
                'error': 'Formato de email inválido'
            })
        
        # Obtener y actualizar el cliente
        cliente = get_object_or_404(Empresa, pk=cliente_id)
        cliente.correoIntercambio = email
        cliente.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Email actualizado correctamente',
            'cliente_id': cliente.id,
            'email': email
        })
        
    except Exception as e:
        logger.exception("Error en actualizar_email_cliente")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def crear_cliente_cotizacion(request):
    """
    API para crear un nuevo cliente/empresa desde el módulo de cotizaciones
    """
    try:
        data = json.loads(request.body)
        
        # Validar datos requeridos
        rut = data.get('rut', '').strip()
        nombre = data.get('nombre', '').strip()
        giro = data.get('giro', '').strip()
        direccion = data.get('direccion', '').strip()
        comuna = data.get('comuna', '').strip()
        ciudad = data.get('ciudad', '').strip()
        
        if not all([rut, nombre, giro, direccion, comuna, ciudad]):
            return JsonResponse({
                'success': False,
                'error': 'Todos los campos obligatorios deben estar completos'
            })
        
        # Verificar si ya existe un cliente con ese RUT
        if Empresa.objects.filter(rut=rut).exists():
            return JsonResponse({
                'success': False,
                'error': f'Ya existe una empresa con el RUT {rut}'
            })
        
        # Crear el cliente
        cliente = Empresa.objects.create(
            rut=rut,
            nombre=nombre,
            nombre_fantasia=data.get('nombre_fantasia', nombre),
            razon_social=data.get('razon_social', nombre),
            giro=giro,
            direccion=direccion,
            comuna=comuna,
            ciudad=ciudad,
            correoIntercambio=data.get('correoIntercambio', ''),
            correoVendedor=data.get('correoVendedor', ''),
            correoAdministrador=data.get('correoAdministrador', ''),
            esProveedor=False  # Siempre es cliente, no proveedor
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Cliente creado exitosamente',
            'cliente_id': cliente.id,
            'cliente': {
                'id': cliente.id,
                'nombre': cliente.nombre,
                'rut': cliente.rut,
                'razon_social': cliente.razon_social
            }
        })
        
    except Exception as e:
        logger.exception("Error en crear_cliente_cotizacion")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== GENERACIÓN DE PDF ====================

def _nombre_empresa_cotizacion(cotizacion):
    """Nombre del emisor para PDF/correo, con fallbacks.

    `Sucursal.nombre` es nullable: sin fallback el correo/PDF mostraba
    literalmente "None"."""
    sucursal = cotizacion.sucursal
    if not sucursal:
        return 'Nuestra Empresa'
    return (
        sucursal.nombre
        or (sucursal.empresa.razon_social if sucursal.empresa_id else '')
        or sucursal.alias
        or 'Nuestra Empresa'
    )


def _generar_pdf_cotizacion(cotizacion):
    """
    Genera el PDF de la cotización y devuelve los bytes.

    Reutilizado por la vista de descarga (`cotizacion_pdf`) y por el envío
    por correo (`enviar_cotizacion_correo`, que lo adjunta). Sin emojis en
    los títulos: Helvetica no tiene esos glifos y ReportLab los renderizaba
    como cuadros negros.
    """
    try:
        # Crear buffer para el PDF
        buffer = BytesIO()
        
        # Crear documento
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=50,
            leftMargin=50,
            topMargin=50,
            bottomMargin=50
        )
        
        # Estilos
        styles = getSampleStyleSheet()
        
        # Estilos personalizados
        style_title = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1a365d'),
            spaceAfter=20,
            alignment=TA_CENTER,
        )
        
        style_subtitle = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=12,
            textColor=colors.HexColor('#4a5568'),
            alignment=TA_CENTER,
            spaceAfter=30,
        )
        
        style_section = ParagraphStyle(
            'SectionTitle',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#2d3748'),
            spaceBefore=20,
            spaceAfter=10,
            borderColor=colors.HexColor('#0066ff'),
            borderWidth=0,
            borderPadding=5,
        )
        
        style_normal = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#2d3748'),
            leading=14,
        )
        
        style_small = ParagraphStyle(
            'CustomSmall',
            parent=styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#718096'),
        )
        
        # Lista de elementos
        elements = []
        
        # ===== ENCABEZADO =====
        # Emisor + número de cotización grande
        elements.append(Paragraph(_nombre_empresa_cotizacion(cotizacion), style_subtitle))
        elements.append(Paragraph("COTIZACIÓN", style_title))
        elements.append(Paragraph(f"<b>{cotizacion.numero_cotizacion}</b>", style_subtitle))
        
        # Información de fechas y estado
        fecha_emision = cotizacion.fecha_emision.strftime('%d/%m/%Y')
        fecha_validez = cotizacion.fecha_validez.strftime('%d/%m/%Y')
        
        # Tabla de encabezado con info
        header_data = [
            ['Fecha Emisión:', fecha_emision, 'Estado:', cotizacion.estado],
            ['Válida hasta:', fecha_validez, 'Días Validez:', str(cotizacion.dias_validez)],
        ]
        
        header_table = Table(header_data, colWidths=[90, 120, 90, 120])
        header_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#718096')),
            ('TEXTCOLOR', (2, 0), (2, -1), colors.HexColor('#718096')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#2d3748')),
            ('TEXTCOLOR', (3, 0), (3, -1), colors.HexColor('#2d3748')),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
            ('FONTNAME', (3, 0), (3, -1), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 20))
        
        # Línea separadora
        line_data = [[''] * 4]
        line_table = Table(line_data, colWidths=[130, 130, 130, 130])
        line_table.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 2, colors.HexColor('#0066ff')),
        ]))
        elements.append(line_table)
        elements.append(Spacer(1, 20))
        
        # ===== INFORMACIÓN DEL CLIENTE =====
        elements.append(Paragraph("INFORMACIÓN DEL CLIENTE", style_section))
        
        cliente_data = [
            ['Razón Social:', cotizacion.cliente.nombre],
            ['RUT:', cotizacion.cliente.rut],
            ['Dirección:', f"{cotizacion.cliente.direccion or 'No registrada'}, {cotizacion.cliente.comuna or ''}, {cotizacion.cliente.ciudad or ''}"],
            ['Email:', cotizacion.cliente.correoIntercambio or 'No registrado'],
        ]
        
        cliente_table = Table(cliente_data, colWidths=[100, 400])
        cliente_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#718096')),
            ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#2d3748')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f7fafc')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(cliente_table)
        elements.append(Spacer(1, 20))
        
        # ===== DESCRIPCIÓN =====
        if cotizacion.descripcion:
            elements.append(Paragraph("DESCRIPCIÓN", style_section))
            elements.append(Paragraph(cotizacion.descripcion, style_normal))
            elements.append(Spacer(1, 15))
        
        # ===== DETALLE DE ITEMS =====
        elements.append(Paragraph("DETALLE DE PRODUCTOS/SERVICIOS", style_section))
        
        # Cabecera de tabla (nota: precios incluyen IVA)
        items_header = ['#', 'Descripción', 'Cant.', 'P. Unit. (IVA)', 'Subtotal']
        items_data = [items_header]
        
        # Items
        for idx, item in enumerate(cotizacion.items.all().order_by('numero_linea'), 1):
            items_data.append([
                str(idx),
                item.descripcion[:50] + ('...' if len(item.descripcion) > 50 else ''),
                str(item.cantidad),
                f"${item.precio_unitario:,.0f}",
                f"${item.subtotal:,.0f}",
            ])
        
        items_table = Table(items_data, colWidths=[30, 250, 50, 85, 85])
        items_table.setStyle(TableStyle([
            # Encabezado
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
            
            # Cuerpo
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#2d3748')),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),
            ('ALIGN', (2, 1), (2, -1), 'CENTER'),
            ('ALIGN', (3, 1), (-1, -1), 'RIGHT'),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
            ('TOPPADDING', (0, 1), (-1, -1), 8),
            
            # Bordes
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            
            # Filas alternas
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7fafc')]),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 20))
        
        # ===== TOTALES =====
        # Nota: subtotal = neto (sin IVA), impuesto = IVA, total = bruto (con IVA)
        totales_data = [
            ['', '', '', 'Neto:', f"${cotizacion.subtotal:,.0f}"],
        ]
        
        if cotizacion.descuento > 0:
            totales_data.append(['', '', '', 'Descuento:', f"-${cotizacion.descuento:,.0f}"])
        
        totales_data.append(['', '', '', 'IVA (19%):', f"${cotizacion.impuesto:,.0f}"])
        totales_data.append(['', '', '', 'TOTAL:', f"${cotizacion.total:,.0f}"])
        
        totales_table = Table(totales_data, colWidths=[30, 250, 50, 85, 85])
        totales_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -2), 'Helvetica'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (3, 0), (3, -1), colors.HexColor('#718096')),
            ('TEXTCOLOR', (4, 0), (4, -2), colors.HexColor('#2d3748')),
            ('TEXTCOLOR', (3, -1), (3, -1), colors.HexColor('#1a365d')),
            ('TEXTCOLOR', (4, -1), (4, -1), colors.HexColor('#0066ff')),
            ('FONTSIZE', (0, -1), (-1, -1), 12),
            ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, -1), (-1, -1), 10),
            ('LINEABOVE', (3, -1), (-1, -1), 2, colors.HexColor('#0066ff')),
        ]))
        elements.append(totales_table)
        elements.append(Spacer(1, 30))
        
        # ===== OBSERVACIONES =====
        if cotizacion.observaciones:
            elements.append(Paragraph("OBSERVACIONES", style_section))
            elements.append(Paragraph(cotizacion.observaciones, style_normal))
            elements.append(Spacer(1, 20))
        
        # ===== PIE DE PÁGINA =====
        footer_text = f"""
        <para alignment="center">
        <font size="9" color="#718096">
        Esta cotización tiene una validez de {cotizacion.dias_validez} días a partir de la fecha de emisión.<br/>
        Los precios incluyen IVA. • Documento generado automáticamente el {timezone.now().strftime('%d/%m/%Y %H:%M')}.
        </font>
        </para>
        """
        elements.append(Spacer(1, 30))
        elements.append(Paragraph(footer_text, style_small))
        
        # Generar PDF
        doc.build(elements)

        # Obtener el valor del buffer
        pdf = buffer.getvalue()
        buffer.close()
        return pdf

    except Exception:
        logger.exception(
            "Error generando PDF de cotizacion %s",
            getattr(cotizacion, 'numero_cotizacion', cotizacion.pk if cotizacion else '?'),
        )
        raise


@login_required
def cotizacion_pdf(request, cotizacion_id):
    """Descarga/visualización del PDF de la cotización."""
    try:
        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)
        pdf = _generar_pdf_cotizacion(cotizacion)

        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="cotizacion_{cotizacion.numero_cotizacion}.pdf"'
        response.write(pdf)
        return response

    except Exception as e:
        return HttpResponse(f"Error generando PDF: {str(e)}", status=500)


# ==================== ENVÍO DE COTIZACIÓN POR CORREO ====================

@login_required
@require_http_methods(["POST"])
def enviar_cotizacion_correo(request, cotizacion_id):
    """
    Envía la cotización por correo electrónico con formato profesional.
    Solo muestra descripción, precio y validez (sin SKU ni talla).
    """
    try:
        data = json.loads(request.body)
        email_destino = data.get('email_destino', '').strip()
        
        if not email_destino:
            return JsonResponse({
                'success': False,
                'error': 'Debe proporcionar un correo de destino'
            })
        
        # Validar formato de email
        import re
        email_regex = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
        if not re.match(email_regex, email_destino):
            return JsonResponse({
                'success': False,
                'error': 'El formato del correo no es válido'
            })
        
        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)
        
        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para enviar esta cotización'
            }, status=403)
        
        # Obtener datos de la cotización
        items = cotizacion.items.all().order_by('numero_linea')
        
        # Formatear moneda
        def formatear_moneda(valor):
            return f"${valor:,.0f}".replace(",", ".")
        
        # Construir tabla de productos (solo descripción, cantidad, precio)
        items_html = ""
        for item in items:
            subtotal = item.cantidad * item.precio_unitario
            items_html += f"""
            <tr>
                <td style="padding: 12px 16px; border-bottom: 1px solid #e5e7eb; font-size: 14px; color: #374151;">
                    {item.descripcion}
                </td>
                <td style="padding: 12px 16px; border-bottom: 1px solid #e5e7eb; text-align: center; font-size: 14px; color: #374151;">
                    {item.cantidad}
                </td>
                <td style="padding: 12px 16px; border-bottom: 1px solid #e5e7eb; text-align: right; font-size: 14px; color: #374151;">
                    {formatear_moneda(item.precio_unitario)}
                </td>
                <td style="padding: 12px 16px; border-bottom: 1px solid #e5e7eb; text-align: right; font-size: 14px; font-weight: 600; color: #0066FF;">
                    {formatear_moneda(subtotal)}
                </td>
            </tr>
            """
        
        # Calcular días restantes de validez
        dias_restantes = (cotizacion.fecha_validez - timezone.localdate()).days
        validez_color = "#22c55e" if dias_restantes >= 0 else "#ef4444"
        validez_texto = f"{dias_restantes} días restantes" if dias_restantes >= 0 else f"Vencida hace {abs(dias_restantes)} días"
        
        # Obtener nombre de la sucursal/empresa (con fallbacks — sucursal.nombre
        # es nullable y sin esto el correo decía literalmente "None")
        nombre_empresa = _nombre_empresa_cotizacion(cotizacion)
        
        # HTML del correo profesional
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f3f4f6;">
            <div style="max-width: 650px; margin: 0 auto; padding: 20px;">
                
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #0066FF 0%, #0052CC 100%); border-radius: 16px 16px 0 0; padding: 32px; text-align: center;">
                    <h1 style="color: white; margin: 0 0 8px 0; font-size: 28px; font-weight: 700;">COTIZACIÓN</h1>
                    <p style="color: rgba(255,255,255,0.9); margin: 0; font-size: 18px; font-weight: 500;">{cotizacion.numero_cotizacion}</p>
                </div>
                
                <!-- Contenido principal -->
                <div style="background: white; padding: 32px; border-radius: 0 0 16px 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    
                    <!-- Información de validez -->
                    <div style="background: {validez_color}15; border-left: 4px solid {validez_color}; padding: 16px; border-radius: 8px; margin-bottom: 24px;">
                        <p style="margin: 0; color: {validez_color}; font-weight: 600; font-size: 14px;">
                            ⏰ Válida hasta: {cotizacion.fecha_validez.strftime('%d/%m/%Y')} ({validez_texto})
                        </p>
                    </div>
                    
                    <!-- Información del cliente -->
                    <div style="background: #f8fafc; border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                        <h3 style="color: #374151; margin: 0 0 12px 0; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">
                            📋 Datos del Cliente
                        </h3>
                        <p style="margin: 0 0 8px 0; font-size: 16px; color: #1f2937;"><strong>{cotizacion.cliente.nombre}</strong></p>
                        <p style="margin: 0 0 4px 0; font-size: 14px; color: #6b7280;">RUT: {cotizacion.cliente.rut}</p>
                        {f'<p style="margin: 0; font-size: 14px; color: #6b7280;">{cotizacion.cliente.direccion}, {cotizacion.cliente.comuna}</p>' if cotizacion.cliente.direccion else ''}
                    </div>
                    
                    <!-- Descripción si existe -->
                    {f'''
                    <div style="background: #eff6ff; border-radius: 12px; padding: 16px; margin-bottom: 24px;">
                        <h4 style="color: #1e40af; margin: 0 0 8px 0; font-size: 13px; text-transform: uppercase;">📝 Descripción</h4>
                        <p style="margin: 0; color: #374151; font-size: 14px;">{cotizacion.descripcion}</p>
                    </div>
                    ''' if cotizacion.descripcion else ''}
                    
                    <!-- Tabla de productos -->
                    <h3 style="color: #374151; margin: 0 0 16px 0; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">
                        🛒 Detalle de Productos
                    </h3>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
                        <thead>
                            <tr style="background: #1a1a2e;">
                                <th style="padding: 14px 16px; text-align: left; color: white; font-size: 13px; font-weight: 600; border-radius: 8px 0 0 0;">Descripción</th>
                                <th style="padding: 14px 16px; text-align: center; color: white; font-size: 13px; font-weight: 600;">Cant.</th>
                                <th style="padding: 14px 16px; text-align: right; color: white; font-size: 13px; font-weight: 600;">P. Unit.</th>
                                <th style="padding: 14px 16px; text-align: right; color: white; font-size: 13px; font-weight: 600; border-radius: 0 8px 0 0;">Subtotal</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items_html}
                        </tbody>
                    </table>
                    
                    <!-- Totales -->
                    <div style="background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%); border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 8px 0; font-size: 14px; color: #6b7280;">Subtotal (Neto)</td>
                                <td style="padding: 8px 0; text-align: right; font-size: 14px; color: #374151;">{formatear_moneda(cotizacion.subtotal)}</td>
                            </tr>
                            {f'''
                            <tr>
                                <td style="padding: 8px 0; font-size: 14px; color: #22c55e;">Descuento</td>
                                <td style="padding: 8px 0; text-align: right; font-size: 14px; color: #22c55e;">-{formatear_moneda(cotizacion.descuento)}</td>
                            </tr>
                            ''' if cotizacion.descuento > 0 else ''}
                            <tr>
                                <td style="padding: 8px 0; font-size: 14px; color: #6b7280;">IVA (19%)</td>
                                <td style="padding: 8px 0; text-align: right; font-size: 14px; color: #374151;">{formatear_moneda(cotizacion.impuesto)}</td>
                            </tr>
                            <tr style="border-top: 2px solid #0066FF;">
                                <td style="padding: 16px 0 8px 0; font-size: 18px; font-weight: 700; color: #1a1a2e;">TOTAL</td>
                                <td style="padding: 16px 0 8px 0; text-align: right; font-size: 22px; font-weight: 700; color: #0066FF;">{formatear_moneda(cotizacion.total)}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <!-- Observaciones si existen -->
                    {f'''
                    <div style="background: #fef3c7; border-left: 4px solid #f59e0b; padding: 16px; border-radius: 8px; margin-bottom: 24px;">
                        <h4 style="color: #92400e; margin: 0 0 8px 0; font-size: 13px; text-transform: uppercase;">⚠️ Observaciones</h4>
                        <p style="margin: 0; color: #78350f; font-size: 14px;">{cotizacion.observaciones}</p>
                    </div>
                    ''' if cotizacion.observaciones else ''}
                    
                    <!-- Nota final -->
                    <div style="text-align: center; padding-top: 16px; border-top: 1px solid #e5e7eb;">
                        <p style="color: #374151; font-size: 13px; margin: 0 0 8px 0;">
                            <strong>📎 Se adjunta la cotización en PDF</strong>
                        </p>
                        <p style="color: #9ca3af; font-size: 12px; margin: 0 0 8px 0;">
                            Esta cotización tiene una validez de {cotizacion.dias_validez} días desde su emisión.
                        </p>
                        <p style="color: #9ca3af; font-size: 12px; margin: 0;">
                            Los precios incluyen IVA • Cotización generada el {cotizacion.fecha_emision.strftime('%d/%m/%Y')}
                        </p>
                    </div>
                </div>
                
                <!-- Footer -->
                <div style="text-align: center; padding: 24px;">
                    <p style="color: #6b7280; font-size: 13px; margin: 0 0 8px 0;">
                        <strong>{nombre_empresa}</strong>
                    </p>
                    <p style="color: #9ca3af; font-size: 11px; margin: 0;">
                        Este correo fue enviado automáticamente • No responda a este mensaje
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Versión texto plano del correo
        text_content = f"""
COTIZACIÓN {cotizacion.numero_cotizacion}
=========================================

Válida hasta: {cotizacion.fecha_validez.strftime('%d/%m/%Y')} ({validez_texto})

CLIENTE
-------
{cotizacion.cliente.nombre}
RUT: {cotizacion.cliente.rut}

{f"DESCRIPCIÓN: {cotizacion.descripcion}" if cotizacion.descripcion else ""}

DETALLE DE PRODUCTOS
--------------------
"""
        for item in items:
            subtotal = item.cantidad * item.precio_unitario
            text_content += f"• {item.descripcion}\n  Cantidad: {item.cantidad} | Precio: {formatear_moneda(item.precio_unitario)} | Subtotal: {formatear_moneda(subtotal)}\n\n"
        
        text_content += f"""
TOTALES
-------
Subtotal (Neto): {formatear_moneda(cotizacion.subtotal)}
{f"Descuento: -{formatear_moneda(cotizacion.descuento)}" if cotizacion.descuento > 0 else ""}
IVA (19%): {formatear_moneda(cotizacion.impuesto)}
TOTAL: {formatear_moneda(cotizacion.total)}

{f"OBSERVACIONES: {cotizacion.observaciones}" if cotizacion.observaciones else ""}

---
Se adjunta la cotización en PDF.
Esta cotización tiene una validez de {cotizacion.dias_validez} días desde su emisión.
Los precios incluyen IVA.
        """

        # Enviar correo
        try:
            subject = f"Cotización {cotizacion.numero_cotizacion} - {nombre_empresa}"

            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email_destino]
            )
            msg.attach_alternative(html_content, "text/html")

            # Adjuntar el MISMO PDF que se descarga desde la web. Si el PDF
            # falla, el correo igual sale (el cuerpo HTML tiene el detalle
            # completo) y queda el error en el log.
            try:
                pdf_bytes = _generar_pdf_cotizacion(cotizacion)
                msg.attach(
                    f'Cotizacion_{cotizacion.numero_cotizacion}.pdf',
                    pdf_bytes,
                    'application/pdf',
                )
            except Exception:
                logger.exception(
                    'No se pudo adjuntar el PDF al correo de cotizacion %s',
                    cotizacion.numero_cotizacion,
                )

            msg.send(fail_silently=False)
            
            # Registrar en historial
            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='ENVIADA_EMAIL',
                descripcion=f'Cotización enviada por correo a {email_destino}',
                ip_address=get_client_ip(request)
            )
            
            logger.info("Cotizacion enviada por correo: numero=%s destino=%s", cotizacion.numero_cotizacion, email_destino)
            
            return JsonResponse({
                'success': True,
                'message': f'Cotización enviada exitosamente a {email_destino}'
            })
            
        except Exception as mail_error:
            logger.exception("Error enviando correo de cotizacion numero=%s", cotizacion.numero_cotizacion)
            return JsonResponse({
                'success': False,
                'error': f'Error al enviar el correo: {str(mail_error)}'
            }, status=500)
        
    except Exception as e:
        logger.exception("Error en enviar_cotizacion_correo")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
