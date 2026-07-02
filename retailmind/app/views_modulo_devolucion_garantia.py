"""
Módulo Devolución de Dinero por Garantía - RetailMind

Vistas HTML (render) + APIs JSON, 100% function-based. Flujo de un solo
actor (jefe_local/administrador): busca el DTE con el problema, elige el
producto, registra/valida al cliente real como receptor, y genera la Nota
de Crédito en el mismo paso.

La lógica de negocio vive en `app/services/devolucion_garantia_service.py`.
"""
import json
import logging

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.db.models import Q

from .decorators import requiere_rol
from .models import Sucursal, DevolucionGarantia
from .services import devolucion_garantia_service as service

logger = logging.getLogger('app')

# 'administracion' es un rol distinto de 'administrador' en este sistema
# (ver PermisoRol.ROLES_CHOICES) y ya tiene puede_aprobar=True por diseño
# en inicializar_permisos.py — mismo criterio que generar_nc_devolucion
# (views_modulo_ventas.py), el módulo hermano de Cambios y Devoluciones.
solo_administrador_o_jefe = requiere_rol('administrador', 'administracion', 'jefe_local')


def _sucursal_actual(request):
    sid = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    if not sid:
        return None
    return Sucursal.objects.filter(id=sid).first()


# ========== VISTAS HTML ==========

@login_required
@solo_administrador_o_jefe
def modulo_devolucion_garantia(request):
    """Listado / registro de devoluciones por garantía (modal embebido)."""
    context = {
        'sucursal_actual': _sucursal_actual(request),
        'estado_choices': DevolucionGarantia._meta.get_field('estado').choices,
    }
    return render(request, 'vistas/modulo_ventas/devolucion_garantia.html', context)


@login_required
@solo_administrador_o_jefe
def detalle_devolucion_garantia(request, devolucion_id):
    """Detalle de una devolución por garantía con su NC asociada."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)

    devolucion = get_object_or_404(
        DevolucionGarantia.objects.select_related(
            'dte_original', 'receptor', 'nota_credito', 'sucursal', 'autorizado_por',
        ).prefetch_related('detalles__dte_producto__productoTalla__producto'),
        id=devolucion_id, sucursal=sucursal,
    )
    context = {'devolucion': devolucion}
    return render(request, 'vistas/modulo_ventas/detalle_devolucion_garantia.html', context)


# ========== APIs JSON ==========

@require_GET
@login_required
@solo_administrador_o_jefe
def api_buscar_dte_devolucion_garantia(request):
    """Busca la boleta/factura por folio y devuelve sus productos + receptor actual."""
    folio = (request.GET.get('folio') or '').strip()
    if not folio:
        return JsonResponse({'success': False, 'error': 'Folio requerido'}, status=400)

    sucursal = _sucursal_actual(request)
    try:
        dte = service.buscar_dte_para_devolucion(folio, sucursal=sucursal)
    except service.DevolucionGarantiaError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=404)

    productos = []
    for dp in dte.dte_productos.all():
        disponible = service.cantidad_disponible_para_devolucion(dp)
        if disponible <= 0:
            continue
        productos.append({
            'dte_producto_id': dp.id,
            'sku': dp.productoTalla.sku if dp.productoTalla else '',
            'articulo': dp.productoTalla.producto.articulo if dp.productoTalla and dp.productoTalla.producto else dp.descripcion,
            'talla': dp.productoTalla.talla if dp.productoTalla else '',
            'cantidad_vendida': dp.stock,
            'cantidad_disponible': disponible,
            'precio_unitario': dp.precio,
        })

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
@login_required
@solo_administrador_o_jefe
def api_generar_devolucion_garantia(request):
    """Registra la devolución por garantía y genera la Nota de Crédito."""
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
        # Mismo criterio de aislamiento por sucursal que el resto del módulo
        # (ver fix de IDOR en detalle_devolucion_garantia): un requerimiento
        # de otra sucursal simplemente no se vincula, sin filtrar por error.
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

        devolucion, nc, contenido_txt = service.generar_devolucion_garantia(
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
        logger.exception("Error al generar devolución por garantía")
        return JsonResponse({'success': False, 'error': f'Error inesperado: {e}'}, status=500)

    return JsonResponse({
        'success': True,
        'message': f'Devolución {devolucion.numero_operacion} registrada. NC #{nc.numero_documento} generada.',
        'data': {
            'devolucion_id': devolucion.id,
            'numero_operacion': devolucion.numero_operacion,
            'nc_id': nc.id,
            'nc_numero': nc.numero_documento,
            'monto_total': float(devolucion.monto_total),
            'txt_generado': contenido_txt is not None,
            'receptor': {
                'id': receptor.id,
                'rut': receptor.rut,
                'nombre': receptor.nombre,
            },
            'requerimiento_vinculado': requerimiento.numero_requerimiento if requerimiento else None,
        },
    })


@require_GET
@login_required
@solo_administrador_o_jefe
def api_listar_devoluciones_garantia(request):
    """Listado paginado de devoluciones por garantía con filtros."""
    sucursal = _sucursal_actual(request)
    if not sucursal:
        return JsonResponse({'success': False, 'error': 'No hay sucursal seleccionada'}, status=400)

    qs = DevolucionGarantia.objects.select_related(
        'dte_original', 'receptor', 'nota_credito', 'autorizado_por',
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
        'fecha': d.created_at.strftime('%d/%m/%Y %H:%M'),
        'autorizado_por': d.autorizado_por.username if d.autorizado_por else '',
    } for d in pagina]

    return JsonResponse({
        'success': True,
        'data': data,
        'total': paginator.count,
        'pagina_actual': pagina.number,
        'total_paginas': paginator.num_pages,
    })
