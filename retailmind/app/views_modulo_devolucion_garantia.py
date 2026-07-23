"""
Módulo Devolución de Dinero por Garantía - RetailMind

Vistas HTML (render) + APIs JSON, 100% function-based. Flujo en dos pasos:

  - Un usuario con acceso al módulo (permiso `devolucion_garantia`) CREA la
    solicitud (busca el DTE, elige líneas por cantidad o por monto parcial,
    registra el cliente real como receptor). Queda PENDIENTE, sin NC.
  - Un administrador la ANALIZA y responde: aprueba (decide el impacto en
    caja y ahí se genera la NC 61 + TXT Acepta) o rechaza con motivo. El
    solicitante puede anular su propia solicitud pendiente.

La lógica de negocio vive en `app/services/devolucion_garantia_service.py`.
"""
import json
import logging
from datetime import datetime

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import Q

from .decorators import requiere_permiso, requiere_rol
from .models import Sucursal, DevolucionGarantia
from .services import devolucion_garantia_service as service

logger = logging.getLogger('app')


def _sucursal_actual(request):
    sid = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    if not sid:
        return None
    return Sucursal.objects.filter(id=sid).first()


def _es_admin(request):
    return getattr(request.user, 'rol', '') == 'administrador'


def _cargar_devolucion(devolucion_id, sucursal, extra_select=None):
    """Carga una DevolucionGarantia aislada por sucursal (anti-IDOR)."""
    select = ['dte_original', 'receptor', 'nota_credito', 'sucursal',
              'solicitado_por', 'autorizado_por', 'anulada_por']
    if extra_select:
        select += extra_select
    return get_object_or_404(
        DevolucionGarantia.objects.select_related(*select),
        id=devolucion_id, sucursal=sucursal,
    )


# ========== VISTAS HTML ==========

@requiere_permiso('devolucion_garantia', 'puede_ver')
def modulo_devolucion_garantia(request):
    """Listado / registro de solicitudes de devolución por garantía."""
    context = {
        'sucursal_actual': _sucursal_actual(request),
        'estado_choices': DevolucionGarantia._meta.get_field('estado').choices,
        'puede_aprobar': _es_admin(request),
        'usuario_id': request.user.id,
    }
    return render(request, 'vistas/modulo_ventas/devolucion_garantia.html', context)


@requiere_permiso('devolucion_garantia', 'puede_ver')
def detalle_devolucion_garantia(request, devolucion_id):
    """Detalle de una solicitud de devolución con su NC asociada (si la tiene)."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)

    devolucion = get_object_or_404(
        DevolucionGarantia.objects.select_related(
            'dte_original', 'receptor', 'nota_credito', 'sucursal',
            'solicitado_por', 'autorizado_por', 'anulada_por',
        ).prefetch_related('detalles__dte_producto__productoTalla__producto'),
        id=devolucion_id, sucursal=sucursal,
    )
    context = {'devolucion': devolucion}
    return render(request, 'vistas/modulo_ventas/detalle_devolucion_garantia.html', context)


# ========== APIs JSON — SOLICITANTE ==========

@require_GET
@requiere_permiso('devolucion_garantia', 'puede_ver')
def api_buscar_dte_devolucion_garantia(request):
    """Busca la boleta/factura por folio y devuelve sus líneas + disponibilidad."""
    folio = (request.GET.get('folio') or '').strip()
    if not folio:
        return JsonResponse({'success': False, 'error': 'Folio requerido'}, status=400)

    sucursal = _sucursal_actual(request)
    try:
        dte = service.buscar_dte_para_devolucion(folio, sucursal=sucursal)
    except service.DevolucionGarantiaError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=404)

    base_es_bruto = service._base_es_bruto(dte)

    productos = []
    for dp in dte.dte_productos.all():
        disp = service.calcular_disponible_linea(dp, dte=dte)
        if disp['cantidad_disponible'] <= 0 and disp['monto_disponible'] <= 0:
            continue
        productos.append({
            'dte_producto_id': dp.id,
            'sku': dp.productoTalla.sku if dp.productoTalla else '',
            'articulo': dp.productoTalla.producto.articulo if dp.productoTalla and dp.productoTalla.producto else dp.descripcion,
            'talla': dp.productoTalla.talla if dp.productoTalla else '',
            'cantidad_vendida': disp['cantidad_vendida'],
            'cantidad_disponible': disp['cantidad_disponible'],
            'monto_disponible': disp['monto_disponible'],
            'monto_linea_vigente': disp['monto_linea_vigente'],
            'precio_unitario_con_iva': disp['precio_unitario_con_iva'],
        })

    saldo = service.saldo_documento(dte)
    receptor = dte.receptor
    es_generico = (not receptor) or (receptor.rut or '').replace('.', '') in ('66666666-6', '666666666')

    return JsonResponse({
        'success': True,
        'dte': {
            'id': dte.id,
            'folio': dte.numero_documento,
            'tipo_documento': dte.get_tipo_documento_display(),
            'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
            'total': float(dte.monto_con_iva),
            'base_precios': 'BRUTO' if base_es_bruto else 'NETO',
            'monto_restante_nc': saldo['monto_restante'],
            'sucursal_id': dte.sucursal_id,
            'sucursal': dte.sucursal.alias if dte.sucursal else '',
        },
        'receptor_actual': {
            'rut': receptor.rut if receptor else '',
            'nombre': receptor.nombre if receptor else '',
        } if receptor else None,
        'receptor_es_generico': es_generico,
        'productos': productos,
    })


@require_POST
@requiere_permiso('devolucion_garantia', 'puede_crear')
def api_generar_devolucion_garantia(request):
    """Crea una SOLICITUD de devolución por garantía (queda PENDIENTE, sin NC)."""
    try:
        body = json.loads(request.body or '{}')
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    folio = (body.get('folio_dte') or '').strip()
    productos = body.get('productos') or []
    motivo = (body.get('motivo') or '').strip() or 'Garantía aprobada'
    requerimiento_id = body.get('requerimiento_id')

    if not folio:
        return JsonResponse({'success': False, 'error': 'folio_dte es requerido'}, status=400)
    if not productos:
        return JsonResponse({'success': False, 'error': 'Debe indicar al menos un producto'}, status=400)

    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)

    requerimiento = None
    if requerimiento_id:
        from .models import Requerimiento
        # Mismo aislamiento por sucursal que el resto del módulo: un
        # requerimiento de otra sucursal simplemente no se vincula.
        requerimiento = Requerimiento.objects.filter(id=requerimiento_id, sucursal=sucursal).first()

    try:
        dte_original = service.buscar_dte_para_devolucion(folio, sucursal=sucursal)

        receptor = service.resolver_o_crear_receptor(
            cliente_id=body.get('cliente_id'),
            rut=body.get('rut', ''),
            nombre=body.get('nombre', ''),
            giro=body.get('giro', ''),
            direccion=body.get('direccion', ''),
            comuna=body.get('comuna', ''),
            ciudad=body.get('ciudad', ''),
            email=body.get('email', ''),
            telefono=body.get('telefono', ''),
        )

        devolucion = service.crear_solicitud_devolucion(
            dte_original=dte_original,
            sucursal=sucursal,
            receptor=receptor,
            motivo=motivo,
            usuario=request.user,
            detalles=productos,
            requerimiento=requerimiento,
        )
    except service.DevolucionGarantiaError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error al crear solicitud de devolución por garantía")
        return JsonResponse({'success': False, 'error': f'Error inesperado: {e}'}, status=500)

    return JsonResponse({
        'success': True,
        'message': f'Solicitud {devolucion.numero_operacion} creada. Queda pendiente de aprobación.',
        'data': {
            'devolucion_id': devolucion.id,
            'numero_operacion': devolucion.numero_operacion,
            'estado': devolucion.estado,
            'monto_total': float(devolucion.monto_total),
            'receptor': {
                'id': receptor.id,
                'rut': receptor.rut,
                'nombre': receptor.nombre,
            },
            'requerimiento_vinculado': requerimiento.numero_requerimiento if requerimiento else None,
        },
    })


@require_POST
@requiere_permiso('devolucion_garantia', 'puede_ver')
def api_anular_solicitud_devolucion_garantia(request, devolucion_id):
    """Anula una solicitud PENDIENTE (el service valida solicitante-o-admin)."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)
    _cargar_devolucion(devolucion_id, sucursal)

    try:
        devolucion = service.anular_solicitud(devolucion_id=devolucion_id, usuario=request.user)
    except service.DevolucionGarantiaError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({
        'success': True,
        'message': f'Solicitud {devolucion.numero_operacion} anulada.',
    })


@require_GET
@requiere_permiso('devolucion_garantia', 'puede_ver')
def api_listar_devoluciones_garantia(request):
    """Listado paginado de solicitudes de devolución con filtros."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)

    qs = DevolucionGarantia.objects.select_related(
        'dte_original', 'receptor', 'nota_credito', 'solicitado_por', 'autorizado_por',
    ).filter(sucursal=sucursal)

    busqueda = (request.GET.get('q') or '').strip()
    if busqueda:
        qs = qs.filter(
            Q(numero_operacion__icontains=busqueda) |
            Q(receptor__rut__icontains=busqueda) |
            Q(receptor__nombre__icontains=busqueda) |
            Q(nota_credito__numero_documento__icontains=busqueda)
        )

    estado = (request.GET.get('estado') or '').strip()
    if estado:
        qs = qs.filter(estado=estado)

    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 20))
    paginator = Paginator(qs, per_page)
    pagina = paginator.get_page(page)

    es_admin = _es_admin(request)
    uid = request.user.id

    data = [{
        'id': d.id,
        'numero_operacion': d.numero_operacion,
        'estado': d.estado,
        'estado_display': d.get_estado_display(),
        'dte_folio': d.dte_original.numero_documento,
        'receptor_nombre': d.receptor.nombre,
        'receptor_rut': d.receptor.rut,
        'monto_total': float(d.monto_total),
        'nc_numero': d.nota_credito.numero_documento if d.nota_credito else None,
        'metodo_devolucion': d.get_metodo_devolucion_display() if d.metodo_devolucion else '',
        'fecha': d.created_at.strftime('%d/%m/%Y %H:%M'),
        'solicitado_por': d.solicitado_por.username if d.solicitado_por else '',
        'autorizado_por': d.autorizado_por.username if d.autorizado_por else '',
        'puede_anular': d.estado == 'PENDIENTE' and (es_admin or d.solicitado_por_id == uid),
    } for d in pagina]

    pendientes = DevolucionGarantia.objects.filter(sucursal=sucursal, estado='PENDIENTE').count()

    return JsonResponse({
        'success': True,
        'data': data,
        'total': paginator.count,
        'pendientes': pendientes,
        'pagina_actual': pagina.number,
        'total_paginas': paginator.num_pages,
    })


# ========== APIs JSON — APROBADOR (solo administrador) ==========

@require_GET
@requiere_rol('administrador')
def api_detalle_solicitud_devolucion_garantia(request, devolucion_id):
    """Detalle completo de una solicitud para el panel de aprobación, con
    re-chequeo de disponibilidad por línea (marca conflictos)."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)

    devolucion = get_object_or_404(
        DevolucionGarantia.objects.select_related(
            'dte_original', 'receptor', 'sucursal', 'solicitado_por',
        ).prefetch_related('detalles__dte_producto__productoTalla'),
        id=devolucion_id, sucursal=sucursal,
    )

    dte = devolucion.dte_original
    lineas = []
    for det in devolucion.detalles.all():
        dp = det.dte_producto
        disp = service.calcular_disponible_linea(dp, dte=dte, excluir_devolucion_id=devolucion.id)
        if det.modo == 'MONTO':
            solicitado = int(det.monto or det.subtotal or 0)
            conflicto = solicitado > disp['monto_disponible']
        else:
            solicitado = int(det.cantidad or 0)
            conflicto = solicitado > disp['cantidad_disponible']
        lineas.append({
            'descripcion': dp.descripcion,
            'sku': dp.productoTalla.sku if dp.productoTalla else '',
            'modo': det.modo,
            'cantidad': det.cantidad,
            'monto': float(det.monto) if det.monto is not None else None,
            'subtotal': float(det.subtotal),
            'disponible_cantidad': disp['cantidad_disponible'],
            'disponible_monto': disp['monto_disponible'],
            'conflicto': conflicto,
        })

    return JsonResponse({
        'success': True,
        'data': {
            'id': devolucion.id,
            'numero_operacion': devolucion.numero_operacion,
            'estado': devolucion.estado,
            'motivo': devolucion.motivo,
            'monto_total': float(devolucion.monto_total),
            'solicitado_por': devolucion.solicitado_por.username if devolucion.solicitado_por else '',
            'fecha_solicitud': devolucion.created_at.strftime('%d/%m/%Y %H:%M'),
            'receptor': {
                'rut': devolucion.receptor.rut,
                'nombre': devolucion.receptor.nombre,
                'giro': devolucion.receptor.giro,
                'direccion': devolucion.receptor.direccion,
            },
            'dte': {
                'folio': dte.numero_documento,
                'tipo': dte.get_tipo_documento_display(),
                'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                'total': float(dte.monto_con_iva),
                'monto_restante_nc': service.saldo_documento(dte, excluir_devolucion_id=devolucion.id)['monto_restante'],
            },
            'lineas': lineas,
            'hay_conflicto': any(l['conflicto'] for l in lineas),
        },
    })


@require_GET
@requiere_rol('administrador')
def api_impacto_caja_devolucion_garantia(request, devolucion_id):
    """Previsualiza el impacto en cuadratura de caja de aprobar con un método/fecha."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)

    devolucion = _cargar_devolucion(devolucion_id, sucursal)

    metodo = (request.GET.get('metodo') or '').strip()
    if metodo not in dict(service.METODO_DEVOLUCION_DG_CHOICES):
        return JsonResponse({'success': False, 'error': 'Método inválido'}, status=400)

    fecha_str = (request.GET.get('fecha') or '').strip()
    fecha_imp = None
    if fecha_str:
        try:
            fecha_imp = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Fecha inválida (use YYYY-MM-DD)'}, status=400)

    preview = service.impacto_caja_preview(devolucion=devolucion, metodo=metodo, fecha_imputacion=fecha_imp)
    return JsonResponse({'success': True, 'data': preview})


@require_POST
@requiere_rol('administrador')
def api_aprobar_devolucion_garantia(request, devolucion_id):
    """Aprueba una solicitud: genera la NC 61 + TXT con el impacto en caja elegido."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)
    _cargar_devolucion(devolucion_id, sucursal)

    try:
        body = json.loads(request.body or '{}')
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    metodo = (body.get('metodo_devolucion') or '').strip()
    observaciones = (body.get('observaciones') or '').strip()
    fecha_str = (body.get('fecha_imputacion') or '').strip()
    fecha_imp = None
    if fecha_str:
        try:
            fecha_imp = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Fecha de imputación inválida (use YYYY-MM-DD)'}, status=400)

    try:
        devolucion, nc, contenido_txt = service.aprobar_devolucion(
            devolucion_id=devolucion_id,
            aprobador=request.user,
            metodo_devolucion=metodo,
            fecha_imputacion=fecha_imp,
            observaciones=observaciones,
        )
    except service.DevolucionGarantiaError as e:
        # La solicitud queda PENDIENTE: el aprobador decide rechazar o reintentar.
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.exception("Error al aprobar devolución por garantía %s", devolucion_id)
        return JsonResponse({'success': False, 'error': f'Error inesperado: {e}'}, status=500)

    return JsonResponse({
        'success': True,
        'message': f'Devolución {devolucion.numero_operacion} aprobada. NC #{nc.numero_documento} generada.',
        'data': {
            'nc_id': nc.id,
            'nc_numero': nc.numero_documento,
            'monto_total': float(devolucion.monto_total),
            'txt_generado': contenido_txt is not None,
            'nc_txt_url': reverse('descargar_txt_nc_api', args=[nc.id]),
        },
    })


@require_POST
@requiere_rol('administrador')
def api_rechazar_devolucion_garantia(request, devolucion_id):
    """Rechaza una solicitud PENDIENTE con motivo obligatorio."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)
    _cargar_devolucion(devolucion_id, sucursal)

    try:
        body = json.loads(request.body or '{}')
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    motivo_rechazo = (body.get('motivo_rechazo') or '').strip()

    try:
        devolucion = service.rechazar_devolucion(
            devolucion_id=devolucion_id,
            aprobador=request.user,
            motivo_rechazo=motivo_rechazo,
        )
    except service.DevolucionGarantiaError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({
        'success': True,
        'message': f'Solicitud {devolucion.numero_operacion} rechazada.',
    })
