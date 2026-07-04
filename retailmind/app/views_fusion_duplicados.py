"""
Fusión y re-etiquetado de productos duplicados.

Caso de negocio: existen dos fichas (A y B) que son el MISMO producto físico
(duplicado de catálogo). Se decide conservar A y vaciar B: el stock de B se
transfiere talla a talla hacia A a través del servicio de inventario (stock
plano + lotes FIFO + kardex, atómico, con referencia común de fusión) y se
generan etiquetas del SKU de A para re-etiquetar las unidades físicas de B.

Reglas:
  - Ambos productos deben pertenecer a la SUCURSAL ACTIVA de la sesión
    (mover stock entre sucursales es un traspaso, no una fusión).
  - Solo se transfieren tallas con par exacto (talla normalizada igual en
    A y B). Las tallas de B sin par en A se reportan y NO se tocan.
  - Kardex: EGRESO en B e INGRESO en A con concepto CORRECCION_STOCK y
    referencia_externa común FUSION-<timestamp>-<A>-<B> (auditable de punta
    a punta con verificar_drift_inventario / reconciliadores).
  - Opcional: marcar B con excluir_de_analitica=True para que deje de
    contaminar reportes mientras se depura del catálogo.
"""
import json
import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from app.models import Producto, Producto_Talla, Sucursal

logger = logging.getLogger('app')


def _normalizar_talla(talla):
    return (talla or '').strip().upper()


def _payload_producto(producto):
    tallas = []
    for t in producto.producto_talla.all().order_by('talla'):
        tallas.append({
            'id': t.id,
            'talla': t.talla or '-',
            'sku': str(t.sku),
            'stock': t.stock or 0,
        })
    return {
        'id': producto.id,
        'articulo': producto.articulo,
        'descripcion': producto.descripcion or '',
        'marca': producto.atributo1.valor if producto.atributo1 else '-',
        'color': producto.atributo2.valor if producto.atributo2 else '-',
        'costo': int(producto.costo or 0),
        'precio_venta': int(producto.precioventa or 0),
        'stock_total': sum(t['stock'] for t in tallas),
        'tallas': tallas,
    }


@login_required
def ver_fusion_duplicados(request):
    """Página de fusión y re-etiquetado de duplicados."""
    sucursal_id = request.session.get('idSucursalActual')
    sucursal = Sucursal.objects.filter(id=sucursal_id).first() if sucursal_id else None
    return render(request, 'vistas/modulo_existencias/fusion_duplicados.html', {
        'sucursal_actual': sucursal,
    })


@require_GET
@login_required
def api_buscar_producto_fusion(request):
    """Busca productos de la sucursal activa por artículo/descripción o SKU."""
    q = (request.GET.get('q') or '').strip()
    sucursal_id = request.session.get('idSucursalActual')
    if not sucursal_id:
        return JsonResponse({'success': False, 'error': 'No hay sucursal activa en la sesión.'})
    if len(q) < 2:
        return JsonResponse({'success': True, 'resultados': []})

    qs = Producto.objects.filter(sucursal_id=sucursal_id).select_related(
        'atributo1', 'atributo2'
    ).prefetch_related('producto_talla')

    if q.isdigit():
        qs = qs.filter(producto_talla__sku=int(q)).distinct()
    else:
        from django.db.models import Q
        qs = qs.filter(
            Q(articulo__icontains=q) | Q(descripcion__icontains=q)
        )

    resultados = [_payload_producto(p) for p in qs.order_by('articulo')[:12]]
    return JsonResponse({'success': True, 'resultados': resultados})


@require_POST
@login_required
def api_ejecutar_fusion(request):
    """Transfiere el stock de B → A talla a talla y deja B en cero.

    Body JSON: {mantener_id, eliminar_id, excluir_eliminado: bool}
    Respuesta: tallas transferidas (con SKU de A para etiquetar), tallas de B
    sin par en A (no tocadas) y la referencia de auditoría.
    """
    from app.services.inventario_service import egresar, ingresar

    try:
        data = json.loads(request.body)
        mantener_id = int(data.get('mantener_id') or 0)
        eliminar_id = int(data.get('eliminar_id') or 0)
        excluir_eliminado = bool(data.get('excluir_eliminado', True))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'Payload inválido.'}, status=400)

    sucursal_id = request.session.get('idSucursalActual')
    if not sucursal_id:
        return JsonResponse({'success': False, 'error': 'No hay sucursal activa en la sesión.'})
    if not mantener_id or not eliminar_id:
        return JsonResponse({'success': False, 'error': 'Debes seleccionar ambos productos.'}, status=400)
    if mantener_id == eliminar_id:
        return JsonResponse({'success': False, 'error': 'El producto a mantener y el a eliminar no pueden ser el mismo.'}, status=400)

    try:
        prod_a = Producto.objects.select_related('sucursal').get(id=mantener_id)
        prod_b = Producto.objects.select_related('sucursal').get(id=eliminar_id)
    except Producto.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Producto no encontrado.'}, status=404)

    if prod_a.sucursal_id != sucursal_id or prod_b.sucursal_id != sucursal_id:
        return JsonResponse({
            'success': False,
            'error': 'Ambos productos deben pertenecer a la sucursal activa '
                     '(para mover stock entre sucursales usa un traspaso).',
        }, status=400)

    tallas_a = {_normalizar_talla(t.talla): t for t in prod_a.producto_talla.all()}
    tallas_b = list(prod_b.producto_talla.all())

    pares = []       # (talla_b, talla_a, cantidad)
    sin_par = []     # tallas de B con stock pero sin par en A
    for tb in tallas_b:
        stock_b = tb.stock or 0
        if stock_b <= 0:
            continue
        ta = tallas_a.get(_normalizar_talla(tb.talla))
        if ta is None:
            sin_par.append({'talla': tb.talla or '-', 'stock': stock_b, 'sku': str(tb.sku)})
        else:
            pares.append((tb, ta, stock_b))

    if not pares:
        return JsonResponse({
            'success': False,
            'error': 'El producto a eliminar no tiene stock en tallas que existan '
                     'en el producto a mantener. Nada que transferir.',
            'sin_par': sin_par,
        }, status=400)

    referencia = 'FUSION-{}-{}-{}'.format(
        timezone.localtime().strftime('%Y%m%d%H%M%S'), prod_a.id, prod_b.id
    )
    usuario = request.user.username

    transferidas = []
    try:
        # Todo-o-nada: si una talla falla, se revierte la fusión completa.
        with transaction.atomic():
            for tb, ta, cantidad in pares:
                egresar(
                    tb, cantidad, 'CORRECCION_STOCK', usuario,
                    sucursal_origen=prod_b.sucursal,
                    precio_unitario=int(prod_b.precioventa or 0),
                    observaciones=(
                        f'Fusión duplicado: stock re-etiquetado hacia '
                        f'{prod_a.articulo} (id {prod_a.id}, sku {ta.sku})'
                    ),
                    referencia_externa=referencia,
                )
                ingresar(
                    ta, cantidad, 'CORRECCION_STOCK', usuario,
                    sucursal_destino=prod_a.sucursal,
                    costo_unitario=int(prod_a.costo or 0),
                    sobreprecio_unitario=int(prod_a.sobreprecio or 0),
                    precio_unitario=int(prod_a.precioventa or 0),
                    observaciones=(
                        f'Fusión duplicado: stock recibido desde '
                        f'{prod_b.articulo} (id {prod_b.id}, sku {tb.sku})'
                    ),
                    referencia_externa=referencia,
                )
                ta.refresh_from_db(fields=['stock'])
                transferidas.append({
                    'talla': ta.talla or '-',
                    'cantidad': cantidad,
                    'sku_destino': str(ta.sku),
                    'stock_final_a': ta.stock,
                    'precio': int(prod_a.precioventa or 0),
                })

            if excluir_eliminado:
                Producto.objects.filter(id=prod_b.id).update(excluir_de_analitica=True)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception:
        logger.exception(
            'Error en fusión de duplicados A=%s B=%s ref=%s',
            prod_a.id, prod_b.id, referencia,
        )
        return JsonResponse({
            'success': False,
            'error': 'Error al ejecutar la fusión; no se aplicó ningún cambio.',
        }, status=500)

    logger.info(
        'Fusión duplicados OK ref=%s A=%s(%s) B=%s(%s) tallas=%s unidades=%s user=%s',
        referencia, prod_a.id, prod_a.articulo, prod_b.id, prod_b.articulo,
        len(transferidas), sum(t['cantidad'] for t in transferidas), usuario,
    )

    return JsonResponse({
        'success': True,
        'referencia': referencia,
        'articulo_destino': prod_a.articulo,
        'marca_destino': prod_a.atributo1.valor if prod_a.atributo1 else '-',
        'transferidas': transferidas,
        'sin_par': sin_par,
        'excluido_de_analitica': excluir_eliminado,
    })
