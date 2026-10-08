"""
Faltantes por revisar — después de aplicar una toma de inventario.

El faltante ya se descontó del stock. El jefe de local recorre la lista
(ordenada por urgencia), busca cada producto y reporta:
  - «Encontrado» con cuántas unidades aparecieron, o
  - «No está» (faltante confirmado).
Un administrador (Maestro / Administrador / Jefe) repone al stock lo
encontrado con un clic: entra como AJUSTE_INVENTARIO_ENTRADA con referencia a
la toma y lote FIFO al costo del corte, igual que un sobrante de la toma.

Permiso propio `revision_faltantes_inventario`, no el de Gestión de
Inventarios: aquel comparte permiso con Fusionar Duplicados (mueve stock con
solo Ver), así que dárselo al jefe de local le abría también eso.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .decorators import requiere_permiso
from .models import (
    Producto_Talla, TomaInventarioDetalle, TomaInventarioLog, es_rol_administrador,
)
from .services import informe_toma_inventario as informe_toma
from .utils_permisos import obtener_sucursales_usuario, puede_ver_sucursal
from .views_gestion_inventarios import _inventario_del_usuario

logger = logging.getLogger('app')

OPCION = 'revision_faltantes_inventario'
ORDEN_URGENCIA = {'ALTA': 0, 'MEDIA': 1, 'BAJA': 2}
ESTADOS_FILTRO = {
    'pendiente': '',
    'encontrado': 'ENCONTRADO',
    'confirmado': 'CONFIRMADO',
    'repuesto': 'REPUESTO',
}


def _urgencia(faltante, valor_venta):
    """Qué buscar primero: varias unidades o mucha plata a precio de venta."""
    if faltante >= 3 or valor_venta >= 60000:
        return 'ALTA'
    if faltante >= 2 or valor_venta >= 25000:
        return 'MEDIA'
    return 'BAJA'


def _faltantes_qs(sucursales_ids):
    """Líneas de tomas APLICADAS cuyo faltante ya se descontó del stock."""
    return (
        TomaInventarioDetalle.objects
        .filter(toma_inventario__estado='COMPLETADO', toma_inventario__sucursal_id__in=sucursales_ids,
                contado=True, excluir_de_analisis=False, ajuste_aplicado=True, diferencia__lt=0)
        .select_related('toma_inventario', 'toma_inventario__sucursal', 'producto_talla__producto',
                        'revision_por', 'reposicion_por')
    )


def _nombre(usuario):
    return (usuario.get_full_name() or usuario.username) if usuario else ''


def _fecha(momento):
    return timezone.localtime(momento).strftime('%d/%m/%Y %H:%M') if momento else ''


def _serializar(d):
    faltante = -d.diferencia
    precio = float(d.precio_venta_sistema or 0)
    costo = float(d.costo_unitario_sistema or 0)
    valor_venta = faltante * precio
    observaciones = d.observaciones or ''
    return {
        'id': d.id,
        'toma_id': d.toma_inventario_id,
        'numero': d.toma_inventario.numero_inventario,
        'sucursal': d.toma_inventario.sucursal.alias,
        'sku': d.sku,
        'articulo': d.producto_nombre,
        'descripcion': d.producto_talla.producto.descripcion if d.producto_talla_id else '',
        'talla': d.talla_nombre or '',
        'marca': d.marca_nombre or '',
        'sistema': d.stock_sistema_ajustado,
        'contado': d.stock_fisico,
        'faltante': faltante,
        'recontado': d.stock_reconteo is not None,
        'motivo': ('No apareció en la pistola' if informe_toma.OBSERVACION_NO_APARECIO in observaciones
                   else 'Se contaron menos'),
        'valor_costo': faltante * costo,
        'valor_venta': valor_venta,
        'urgencia': _urgencia(faltante, valor_venta),
        'stock_actual': d.producto_talla.stock if d.producto_talla_id else None,
        'revision_estado': d.revision_estado,
        'revision_cantidad': d.revision_cantidad,
        'revision_nota': d.revision_nota,
        'revision_por': _nombre(d.revision_por),
        'revision_fecha': _fecha(d.revision_fecha),
        'reposicion_por': _nombre(d.reposicion_por),
        'reposicion_fecha': _fecha(d.reposicion_fecha),
    }


def _sucursales_pedidas(request):
    """La sucursal elegida en la pantalla ('todas' = todas las del usuario); sin
    elegir, la activa de la sesión. Siempre dentro de las que el usuario ve."""
    permitidas = list(obtener_sucursales_usuario(request.user).values_list('id', flat=True))
    pedida = (request.GET.get('sucursal') or '').strip()
    if pedida == 'todas':
        return permitidas
    if not pedida:
        pedida = str(request.session.get('idSucursalActual') or '')
    if pedida.isdigit() and int(pedida) in permitidas and puede_ver_sucursal(request.user, int(pedida)):
        return [int(pedida)]
    return permitidas


def _detalle_del_usuario(request, detalle_id):
    """Línea de faltante (bloqueada) de una toma aplicada a la que el usuario tiene acceso."""
    d = (
        TomaInventarioDetalle.objects.select_for_update(of=('self',))
        .filter(id=detalle_id, toma_inventario__estado='COMPLETADO', contado=True,
                excluir_de_analisis=False, ajuste_aplicado=True, diferencia__lt=0)
        .select_related('toma_inventario', 'toma_inventario__sucursal')
        .first()
    )
    if d is None or _inventario_del_usuario(request, d.toma_inventario_id) is None:
        return None
    return d


def _log(detalle, usuario, descripcion, datos):
    TomaInventarioLog.objects.create(
        toma_inventario=detalle.toma_inventario, tipo_accion='MODIFICACION', usuario=usuario,
        descripcion=descripcion, datos_adicionales=datos,
    )


# ==============================================================================
# PANTALLA
# ==============================================================================

@login_required
def revision_faltantes(request):
    activa = request.session.get('idSucursalActual')
    return render(request, 'vistas/modulo_existencias/revision_faltantes.html', {
        'sucursales_revision': list(
            obtener_sucursales_usuario(request.user).values('id', 'alias', 'direccion').order_by('alias')
        ),
        'sucursal_activa_id': int(activa) if str(activa or '').isdigit() else None,
        'puede_reponer': es_rol_administrador(request.user),
        'toma_inicial': request.GET.get('toma') or '',
    })


@require_GET
@login_required
def api_revision_faltantes(request):
    """Faltantes de tomas aplicadas, por urgencia. Filtros: sucursal, toma, estado."""
    try:
        qs = _faltantes_qs(_sucursales_pedidas(request))
        toma = (request.GET.get('toma') or '').strip()
        if toma.isdigit():
            qs = qs.filter(toma_inventario_id=int(toma))

        todos = [_serializar(d) for d in qs[:3000]]
        resumen = {
            'lineas': len(todos),
            'unidades': sum(i['faltante'] for i in todos),
            'valor_venta': sum(i['valor_venta'] for i in todos),
            'valor_costo': sum(i['valor_costo'] for i in todos),
            'pendiente': sum(1 for i in todos if not i['revision_estado']),
            'encontrado': sum(1 for i in todos if i['revision_estado'] == 'ENCONTRADO'),
            'confirmado': sum(1 for i in todos if i['revision_estado'] == 'CONFIRMADO'),
            'repuesto': sum(1 for i in todos if i['revision_estado'] == 'REPUESTO'),
            'urgentes_pendientes': sum(1 for i in todos if not i['revision_estado'] and i['urgencia'] == 'ALTA'),
        }
        tomas = {}
        for i in todos:
            tomas.setdefault(i['toma_id'], {'id': i['toma_id'], 'numero': i['numero'], 'sucursal': i['sucursal'], 'lineas': 0})
            tomas[i['toma_id']]['lineas'] += 1

        estado = (request.GET.get('estado') or '').strip()
        items = todos if estado not in ESTADOS_FILTRO else [
            i for i in todos if i['revision_estado'] == ESTADOS_FILTRO[estado]
        ]
        items.sort(key=lambda i: (ORDEN_URGENCIA[i['urgencia']], -i['valor_venta'], i['articulo'], i['talla']))
        return JsonResponse({
            'success': True,
            'items': items,
            'resumen': resumen,
            'tomas': sorted(tomas.values(), key=lambda t: t['numero'], reverse=True),
            'puede_reponer': es_rol_administrador(request.user),
        })
    except Exception as e:
        logger.error(f'Error al listar faltantes por revisar: {e}')
        return JsonResponse({'success': False, 'error': str(e)})


# ==============================================================================
# JEFE DE LOCAL: reportar
# ==============================================================================

@require_POST
@login_required
@requiere_permiso(OPCION, 'puede_editar')
@transaction.atomic
def api_reportar_faltante(request, detalle_id):
    """
    Body: {"estado": "ENCONTRADO" | "CONFIRMADO" | "", "cantidad": n, "nota": "..."}
    ENCONTRADO exige cantidad entre 1 y lo descontado. "" deshace el reporte.
    No mueve stock: solo deja el aviso para el administrador.
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos inválidos'})
    estado = (data.get('estado') or '').strip().upper()
    if estado not in ('ENCONTRADO', 'CONFIRMADO', ''):
        return JsonResponse({'success': False, 'error': 'Estado no válido'})

    d = _detalle_del_usuario(request, detalle_id)
    if d is None:
        return JsonResponse({'success': False, 'error': 'Faltante no encontrado o sin acceso'}, status=404)
    if d.revision_estado == 'REPUESTO':
        return JsonResponse({'success': False, 'error': 'Este faltante ya se repuso al stock: no se puede cambiar'})

    faltante = -d.diferencia
    cantidad = None
    if estado == 'ENCONTRADO':
        try:
            cantidad = int(data.get('cantidad'))
        except (TypeError, ValueError):
            return JsonResponse({'success': False, 'error': 'Indique cuántas unidades encontró'})
        if not 1 <= cantidad <= faltante:
            return JsonResponse({'success': False, 'error': f'La cantidad debe estar entre 1 y {faltante} (lo que se descontó)'})
    nota = (data.get('nota') or '').strip()[:255]

    ahora = timezone.now()
    TomaInventarioDetalle.objects.filter(pk=d.pk).update(
        revision_estado=estado, revision_cantidad=cantidad, revision_nota=nota,
        revision_por=request.user if estado else None, revision_fecha=ahora if estado else None,
    )
    texto = {
        'ENCONTRADO': f'encontrado ({cantidad} u.): falta reponer',
        'CONFIRMADO': 'confirmado: no está',
        '': 'reporte deshecho',
    }[estado]
    _log(d, request.user, f'Revisión de faltante {d.sku} {d.producto_nombre}: {texto}' + (f' — {nota}' if nota else ''),
         {'detalle_id': d.id, 'estado': estado, 'cantidad': cantidad, 'nota': nota})
    d.refresh_from_db()
    return JsonResponse({'success': True, 'item': _serializar(d)})


# ==============================================================================
# ADMINISTRADOR: reponer al stock lo encontrado
# ==============================================================================

@require_POST
@login_required
@requiere_permiso(OPCION, 'puede_editar')
@transaction.atomic
def api_reponer_faltante(request, detalle_id):
    """
    Ingresa al stock lo que el jefe de local encontró. Body opcional
    {"cantidad": n} para reponer otra cantidad (entre 1 y lo descontado).
    Idempotente: la línea queda REPUESTO y un segundo intento se rechaza.
    """
    from .views import registrar_movimiento_producto
    from .views_modulo_productos import crear_lote_producto

    if not es_rol_administrador(request.user):
        return JsonResponse({'success': False, 'error': 'Solo un administrador repone al stock'}, status=403)
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = {}

    d = _detalle_del_usuario(request, detalle_id)
    if d is None:
        return JsonResponse({'success': False, 'error': 'Faltante no encontrado o sin acceso'}, status=404)
    if d.revision_estado == 'REPUESTO':
        return JsonResponse({'success': False, 'error': 'Este faltante ya se repuso al stock'})
    if d.revision_estado != 'ENCONTRADO' or not d.revision_cantidad:
        return JsonResponse({'success': False, 'error': 'Primero el jefe de local debe reportarlo como encontrado'})

    faltante = -d.diferencia
    try:
        cantidad = int(data.get('cantidad') or d.revision_cantidad)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Cantidad inválida'})
    if not 1 <= cantidad <= faltante:
        return JsonResponse({'success': False, 'error': f'La cantidad debe estar entre 1 y {faltante}'})

    toma = d.toma_inventario
    pt = Producto_Talla.objects.select_for_update().select_related('producto').get(pk=d.producto_talla_id)
    observacion = (f'Encontrado después del inventario {toma.numero_inventario} '
                   f'(reportó {_nombre(d.revision_por) or "jefe de local"}, repuso {_nombre(request.user)})')
    # Igual que un sobrante de la toma: lote FIFO al costo del corte + kardex con referencia
    lote = crear_lote_producto(
        producto_talla=pt, cantidad=cantidad, costo_unitario=d.costo_unitario_sistema,
        sobreprecio_unitario=0, precio_venta_unitario=d.precio_venta_sistema, observaciones=observacion,
    )
    movimiento = registrar_movimiento_producto(
        producto_talla=pt, concepto='AJUSTE_INVENTARIO_ENTRADA', cantidad=cantidad, responsable=request.user,
        sucursal_destino=toma.sucursal, observaciones=observacion,
        referencia_externa=toma.numero_inventario, crear_lote_fifo=False,
    )
    if lote is not None and movimiento is not None:
        lote.movimiento = movimiento
        lote.save(update_fields=['movimiento'])

    TomaInventarioDetalle.objects.filter(pk=d.pk).update(
        revision_estado='REPUESTO', revision_cantidad=cantidad,
        reposicion_por=request.user, reposicion_fecha=timezone.now(),
    )
    _log(d, request.user, f'Faltante {d.sku} {d.producto_nombre}: {cantidad} u. repuestas al stock (encontradas después del inventario)',
         {'detalle_id': d.id, 'cantidad': cantidad, 'movimiento_id': getattr(movimiento, 'id', None)})
    d.refresh_from_db()
    item = _serializar(d)
    item['stock_actual'] = Producto_Talla.objects.get(pk=d.producto_talla_id).stock
    return JsonResponse({'success': True, 'item': item})
