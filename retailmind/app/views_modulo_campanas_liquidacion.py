"""
Campañas de liquidación — gestión masiva de precios de liquidación y promos
NxM (2x1, 3x2) sobre conjuntos de productos.

FBV (patrón del proyecto). La lógica de aplicar/revertir precios vive en
app/services/campanas_service.py. Estas vistas orquestan el workflow
Borrador → Activa → Finalizada y exponen el endpoint de promos NxM vigentes
que consume el POS web.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from .models import (
    CampanaLiquidacion, CampanaLiquidacionProducto, EmpresaUser, Producto,
    Producto_Talla, Sucursal,
)
from .services import campanas_service

logger = logging.getLogger('app')


def _sucursales_usuario_ids(request):
    empresas = EmpresaUser.objects.filter(
        user=request.user, status=True
    ).values_list('empresa_id', flat=True)
    return list(Sucursal.objects.filter(empresa_id__in=empresas)
                .values_list('id', flat=True))


def _campana_dict(c, incluir_items=False):
    d = {
        'id': c.id, 'nombre': c.nombre, 'descripcion': c.descripcion,
        'estado': c.estado, 'estado_label': c.get_estado_display(),
        'tipo_regla': c.tipo_regla, 'tipo_label': c.get_tipo_regla_display(),
        'valor_porcentaje': float(c.valor_porcentaje) if c.valor_porcentaje is not None else None,
        'valor_precio_fijo': c.valor_precio_fijo,
        'nxm_n': c.nxm_n, 'nxm_m': c.nxm_m,
        'fecha_inicio': c.fecha_inicio.strftime('%Y-%m-%d %H:%M') if c.fecha_inicio else None,
        'fecha_fin': c.fecha_fin.strftime('%Y-%m-%d %H:%M') if c.fecha_fin else None,
        'respetar_piso_costo': c.respetar_piso_costo,
        'sucursales': list(c.sucursales.values_list('id', flat=True)),
        'n_items': getattr(c, 'n_items', None),
        'fecha_creacion': c.fecha_creacion.strftime('%Y-%m-%d %H:%M') if c.fecha_creacion else None,
    }
    if incluir_items:
        d['items'] = [{
            'id': it.id, 'producto_id': it.producto_id,
            'articulo': it.producto.articulo, 'descripcion': it.producto.descripcion,
            'sucursal': it.producto.sucursal.alias,
            'precio_actual': it.producto.precioventa,
            'precio_original': it.precio_original,
            'precio_liquidacion': it.precio_liquidacion,
            'estado': it.estado, 'estado_label': it.get_estado_display(),
            'observaciones': it.observaciones,
        } for it in c.items.select_related('producto', 'producto__sucursal').all()]
    return d


@login_required
def ver_campanas_liquidacion(request):
    return render(request, 'vistas/modulo_campanas/campanas_liquidacion.html', {})


@require_GET
@login_required
def listar_campanas_liquidacion(request):
    """Lista de campañas + KPIs por estado."""
    try:
        qs = (CampanaLiquidacion.objects
              .annotate(n_items=Count('items'))
              .order_by('-fecha_creacion'))
        estado = request.GET.get('estado')
        if estado:
            qs = qs.filter(estado=estado)
        campanas = [_campana_dict(c) for c in qs[:200]]
        kpis = {row['estado']: row['n'] for row in
                CampanaLiquidacion.objects.values('estado')
                .annotate(n=Count('id'))}
        return JsonResponse({'success': True, 'campanas': campanas, 'kpis': kpis})
    except Exception as e:
        logger.exception('Error listando campañas de liquidación')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _validar_regla(tipo, data):
    """Valida los parámetros de la regla. Devuelve (ok, error, kwargs)."""
    kwargs = {'valor_porcentaje': None, 'valor_precio_fijo': None,
              'nxm_n': None, 'nxm_m': None}
    if tipo == 'PORCENTAJE':
        try:
            pct = float(data.get('valor_porcentaje'))
        except (TypeError, ValueError):
            return False, 'Porcentaje inválido.', kwargs
        if not (1 <= pct <= 99):
            return False, 'El porcentaje debe estar entre 1 y 99.', kwargs
        kwargs['valor_porcentaje'] = pct
    elif tipo == 'PRECIO_FIJO':
        try:
            precio = int(data.get('valor_precio_fijo'))
        except (TypeError, ValueError):
            return False, 'Precio fijo inválido.', kwargs
        if precio <= 0:
            return False, 'El precio fijo debe ser mayor a 0.', kwargs
        kwargs['valor_precio_fijo'] = precio
    elif tipo == 'NXM':
        try:
            n, m = int(data.get('nxm_n')), int(data.get('nxm_m'))
        except (TypeError, ValueError):
            return False, 'Valores NxM inválidos.', kwargs
        if m < 1 or n <= m:
            return False, 'NxM: "lleva" (n) debe ser mayor que "paga" (m) ≥ 1.', kwargs
        kwargs['nxm_n'], kwargs['nxm_m'] = n, m
    else:
        return False, f'Tipo de regla desconocido: {tipo}', kwargs
    return True, None, kwargs


@require_POST
@login_required
def crear_campana_liquidacion(request):
    """Crea una campaña en BORRADOR con sus items (producto_ids del drill-down)."""
    try:
        data = json.loads(request.body or '{}')
        nombre = (data.get('nombre') or '').strip()
        tipo = data.get('tipo_regla')
        if not nombre:
            return JsonResponse({'success': False, 'error': 'El nombre es obligatorio.'}, status=400)
        ok, err, regla_kwargs = _validar_regla(tipo, data)
        if not ok:
            return JsonResponse({'success': False, 'error': err}, status=400)

        fecha_inicio = parse_datetime(data.get('fecha_inicio') or '') or timezone.now()
        fecha_fin = parse_datetime(data.get('fecha_fin') or '') if data.get('fecha_fin') else None
        if fecha_fin and fecha_fin <= fecha_inicio:
            return JsonResponse({'success': False, 'error': 'La fecha de fin debe ser posterior al inicio.'}, status=400)

        producto_ids = data.get('producto_ids') or []
        if not producto_ids:
            return JsonResponse({'success': False, 'error': 'Selecciona al menos un producto.'}, status=400)

        suc_permitidas = set(_sucursales_usuario_ids(request))
        productos = list(Producto.objects.filter(
            id__in=producto_ids, sucursal_id__in=suc_permitidas
        ).values_list('id', flat=True))
        if not productos:
            return JsonResponse({'success': False, 'error': 'Ningún producto válido en tus sucursales.'}, status=400)

        sucursal_ids = data.get('sucursal_ids') or []
        sucursal_ids = [s for s in sucursal_ids if s in suc_permitidas]

        with transaction.atomic():
            campana = CampanaLiquidacion.objects.create(
                nombre=nombre, descripcion=(data.get('descripcion') or '').strip() or None,
                tipo_regla=tipo, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
                respetar_piso_costo=bool(data.get('respetar_piso_costo', True)),
                creado_por=request.user, **regla_kwargs,
            )
            if sucursal_ids:
                campana.sucursales.set(sucursal_ids)
            else:
                # Por defecto, las sucursales de los productos elegidos.
                suc_de_productos = Producto.objects.filter(
                    id__in=productos).values_list('sucursal_id', flat=True).distinct()
                campana.sucursales.set(list(suc_de_productos))
            CampanaLiquidacionProducto.objects.bulk_create([
                CampanaLiquidacionProducto(campana=campana, producto_id=pid)
                for pid in productos
            ])
        logger.info('Campaña #%s creada por %s con %s productos',
                    campana.pk, request.user, len(productos))
        return JsonResponse({'success': True, 'campana_id': campana.pk,
                             'n_items': len(productos)})
    except Exception as e:
        logger.exception('Error creando campaña de liquidación')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@login_required
def detalle_campana_liquidacion(request, campana_id):
    try:
        campana = (CampanaLiquidacion.objects
                   .annotate(n_items=Count('items'))
                   .get(id=campana_id))
        return JsonResponse({'success': True, 'campana': _campana_dict(campana, incluir_items=True)})
    except CampanaLiquidacion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Campaña no encontrada.'}, status=404)
    except Exception as e:
        logger.exception('Error en detalle de campaña')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@login_required
def modificar_productos_campana(request, campana_id):
    """Agregar/quitar productos de una campaña en BORRADOR."""
    try:
        campana = CampanaLiquidacion.objects.get(id=campana_id)
        if campana.estado != 'BORRADOR':
            return JsonResponse({'success': False, 'error': 'Solo se pueden editar productos en campañas en borrador.'}, status=400)
        data = json.loads(request.body or '{}')
        agregar = data.get('agregar') or []
        quitar = data.get('quitar') or []
        suc_permitidas = set(_sucursales_usuario_ids(request))
        if quitar:
            campana.items.filter(producto_id__in=quitar).delete()
        if agregar:
            validos = Producto.objects.filter(
                id__in=agregar, sucursal_id__in=suc_permitidas
            ).values_list('id', flat=True)
            existentes = set(campana.items.values_list('producto_id', flat=True))
            nuevos = [pid for pid in validos if pid not in existentes]
            CampanaLiquidacionProducto.objects.bulk_create([
                CampanaLiquidacionProducto(campana=campana, producto_id=pid)
                for pid in nuevos
            ])
        n = campana.items.count()
        return JsonResponse({'success': True, 'n_items': n})
    except CampanaLiquidacion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Campaña no encontrada.'}, status=404)
    except Exception as e:
        logger.exception('Error modificando productos de campaña')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@login_required
def activar_campana_liquidacion(request, campana_id):
    """Valida colisiones y aplica la campaña (baja precios / activa NxM)."""
    try:
        campana = CampanaLiquidacion.objects.get(id=campana_id)
        if campana.estado not in ('BORRADOR', 'ACTIVA'):
            return JsonResponse({'success': False, 'error': f'La campaña está {campana.get_estado_display()}.'}, status=400)

        # Colisión: producto ya activo en OTRA campaña.
        producto_ids = list(campana.items.values_list('producto_id', flat=True))
        conflictos = list(CampanaLiquidacionProducto.objects.filter(
            producto_id__in=producto_ids, activo=True,
        ).exclude(campana_id=campana.id)
            .select_related('producto', 'campana')
            .values('producto__articulo', 'campana__nombre', 'campana_id')[:50])
        if conflictos:
            return JsonResponse({
                'success': False,
                'error': 'Hay productos ya activos en otra campaña.',
                'conflictos': conflictos,
            }, status=409)

        ip = request.META.get('REMOTE_ADDR')
        try:
            res = campanas_service.aplicar_precios_campana(campana, usuario=request.user, ip=ip)
        except IntegrityError:
            return JsonResponse({'success': False, 'error': 'Colisión de campaña activa (constraint). Revisa productos duplicados.'}, status=409)
        return JsonResponse({'success': True, 'resultado': res})
    except CampanaLiquidacion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Campaña no encontrada.'}, status=404)
    except Exception as e:
        logger.exception('Error activando campaña')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@login_required
def cerrar_campana_liquidacion(request, campana_id):
    """Reversión manual: restaura precios y finaliza la campaña."""
    try:
        campana = CampanaLiquidacion.objects.get(id=campana_id)
        if campana.estado != 'ACTIVA':
            return JsonResponse({'success': False, 'error': 'Solo se pueden cerrar campañas activas.'}, status=400)
        ip = request.META.get('REMOTE_ADDR')
        res = campanas_service.revertir_precios_campana(
            campana, usuario=request.user, ip=ip, motivo='Cierre manual')
        return JsonResponse({'success': True, 'resultado': res})
    except CampanaLiquidacion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Campaña no encontrada.'}, status=404)
    except Exception as e:
        logger.exception('Error cerrando campaña')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@login_required
def obtener_promos_activas(request):
    """Promos NxM vigentes de una sucursal, con sus SKUs. Consumido por el POS.

    La vigencia se evalúa aquí (fecha_inicio<=now<=fecha_fin|null): una
    campaña vencida que el scheduler aún no cerró NO devuelve promos.
    """
    try:
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        if not sucursal_id:
            return JsonResponse({'success': True, 'promos': []})
        ahora = timezone.now()
        items = (CampanaLiquidacionProducto.objects.filter(
            activo=True, campana__estado='ACTIVA', campana__tipo_regla='NXM',
            campana__fecha_inicio__lte=ahora, campana__sucursales__id=sucursal_id,
        ).filter(Q(campana__fecha_fin__isnull=True) | Q(campana__fecha_fin__gte=ahora))
            .select_related('campana')
            .values('campana_id', 'campana__nombre', 'campana__nxm_n',
                    'campana__nxm_m', 'producto_id'))

        prod_ids = {it['producto_id'] for it in items}
        skus_por_prod = {}
        for pt in Producto_Talla.objects.filter(
                producto_id__in=prod_ids).values('producto_id', 'sku'):
            skus_por_prod.setdefault(pt['producto_id'], []).append(pt['sku'])

        promos_map = {}
        for it in items:
            cid = it['campana_id']
            p = promos_map.setdefault(cid, {
                'campana_id': cid, 'nombre': it['campana__nombre'],
                'n': it['campana__nxm_n'], 'm': it['campana__nxm_m'],
                'productos': [],
            })
            p['productos'].append({
                'producto_id': it['producto_id'],
                'skus': skus_por_prod.get(it['producto_id'], []),
            })
        return JsonResponse({'success': True, 'promos': list(promos_map.values())})
    except Exception as e:
        logger.exception('Error obteniendo promos activas')
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
