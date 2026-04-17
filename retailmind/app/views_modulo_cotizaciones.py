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
    Movimientos_Producto,
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
        
        # Construir query base
        cotizaciones = Cotizacion_Empresa.objects.filter(sucursal_id=sucursal_id)
        
        # Aplicar filtros
        if fecha_desde:
            cotizaciones = cotizaciones.filter(fecha_emision__gte=fecha_desde)
        
        if fecha_hasta:
            cotizaciones = cotizaciones.filter(fecha_emision__lte=fecha_hasta)
        
        if estado:
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
        
        # Calcular estadísticas
        estadisticas = {
            'total': cotizaciones.count(),
            'vigentes': cotizaciones.filter(estado=Cotizacion_Empresa.ESTADO_VIGENTE).count(),
            'monto_total': cotizaciones.aggregate(Sum('total'))['total__sum'] or 0,
            'facturadas': cotizaciones.filter(estado=Cotizacion_Empresa.ESTADO_FACTURADA).count(),
            'anuladas': cotizaciones.filter(estado=Cotizacion_Empresa.ESTADO_ANULADA).count(),
        }
        
        # Paginación
        total = cotizaciones.count()
        inicio = (page - 1) * per_page
        fin = inicio + per_page
        cotizaciones_paginadas = cotizaciones[inicio:fin]
        
        # Serializar datos
        cotizaciones_data = []
        for cot in cotizaciones_paginadas:
            # Contar items con y sin SKU asociado, y verificar stock
            total_items = cot.items.count()
            items_con_sku = 0
            items_sin_sku = 0
            items_sin_stock = 0  # Items con SKU pero sin stock suficiente
            problemas_stock = []  # Detalle de problemas de stock
            
            for item in cot.items.all():
                # Un item tiene SKU si tiene skus_asociados o producto_existente
                skus_asociados = item.skus_asociados.all()
                tiene_sku = skus_asociados.exists() or item.producto_existente is not None
                
                if tiene_sku:
                    items_con_sku += 1
                    
                    # Verificar stock de cada SKU asociado (usando stock por sucursal)
                    if skus_asociados.exists():
                        for sku_rel in skus_asociados:
                            if sku_rel.producto_talla:
                                # Usar stock_sucursal para obtener stock real de la sucursal
                                stock_actual = sku_rel.producto_talla.stock_sucursal(sucursal_id)
                                cantidad_requerida = sku_rel.cantidad
                                if stock_actual < cantidad_requerida:
                                    items_sin_stock += 1
                                    problemas_stock.append({
                                        'sku': str(sku_rel.producto_talla.sku),
                                        'descripcion': item.descripcion[:30],
                                        'stock': stock_actual,
                                        'requerido': cantidad_requerida
                                    })
                    elif item.producto_existente:
                        # Compatibilidad con modelo anterior
                        stock_actual = item.producto_existente.stock_sucursal(sucursal_id)
                        if stock_actual < item.cantidad:
                            items_sin_stock += 1
                            problemas_stock.append({
                                'sku': str(item.producto_existente.sku),
                                'descripcion': item.descripcion[:30],
                                'stock': stock_actual,
                                'requerido': item.cantidad
                            })
                else:
                    items_sin_sku += 1
            
            # Solo puede facturar si:
            # - Tiene items
            # - NO está ya facturada
            # - Está vigente
            # - (items_sin_sku se tolera: el usuario confirmará despacho diferido)
            esta_facturada = cot.facturada or cot.estado == Cotizacion_Empresa.ESTADO_FACTURADA
            esta_vigente = cot.estado == Cotizacion_Empresa.ESTADO_VIGENTE
            puede_facturar = total_items > 0 and items_sin_stock == 0 and not esta_facturada and esta_vigente
            
            # Motivo por el que no puede facturar
            motivo_no_facturar = None
            if esta_facturada:
                motivo_no_facturar = f'Ya facturada: {cot.numero_factura or "Sin número"}'
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
                'vendedor_nombre': cot.vendedor.nombre if cot.vendedor else 'Sin vendedor',
                'estado': cot.estado,
                'facturada': esta_facturada,
                'numero_factura': cot.numero_factura or '',
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
                'items_pendientes_despacho': (
                    cot.items.filter(
                        es_producto_pendiente=False,
                        sku_asignado_post_factura=False,
                        producto_existente__isnull=True,
                    ).count()
                    if esta_facturada else 0
                ),
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
        print(f"Error en listar_cotizaciones: {str(e)}")
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
        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)
        
        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver esta cotización'
            }, status=403)
        
        # Serializar items
        items_data = []
        print(f"📋 Cargando detalle de cotización {cotizacion.numero_cotizacion}")
        
        for item in cotizacion.items.all().order_by('numero_linea'):
            # Obtener TODOS los SKUs asociados del nuevo modelo
            skus_asociados = []
            for sku_rel in item.skus_asociados.all():
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
            
            print(f"  📦 Item {item.numero_linea}: {len(skus_asociados)} SKUs encontrados")
            
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
                print(f"    ✅ SKUs: {[s['sku'] for s in skus_asociados]}")

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
            })
        
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
            'dias_restantes': cotizacion.dias_restantes,
        }
        
        return JsonResponse({
            'success': True,
            'cotizacion': cotizacion_data
        })
        
    except Exception as e:
        print(f"Error en detalle_cotizacion: {str(e)}")
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
        
        # Generar número de cotización
        numero_cotizacion = generar_numero_cotizacion(sucursal)
        
        # Calcular fecha de validez
        fecha_emision_str = data.get('fecha_emision')
        dias_validez = int(data.get('dias_validez', 30))
        
        fecha_emision = datetime.strptime(fecha_emision_str, '%Y-%m-%d').date() if fecha_emision_str else timezone.localdate()
        fecha_validez = fecha_emision + timedelta(days=dias_validez)
        
        # Crear cotización
        cotizacion = Cotizacion_Empresa.objects.create(
            sucursal=sucursal,
            cliente=cliente,
            usuario_creador=request.user,
            numero_cotizacion=numero_cotizacion,
            fecha_emision=fecha_emision,
            fecha_validez=fecha_validez,
            dias_validez=dias_validez,
            descripcion=data.get('descripcion', ''),
            observaciones=data.get('observaciones', ''),
        )
        
        # Crear items
        items_data = data.get('items', [])
        print(f"📦 Creando {len(items_data)} items para cotización {numero_cotizacion}")
        
        for idx, item_data in enumerate(items_data, start=1):
            # Obtener SKUs asociados si existen
            skus = item_data.get('skus', [])
            print(f"  📋 Item {idx}: {item_data.get('descripcion', '')[:30]}... - {len(skus)} SKUs")
            
            # Determinar si tiene productos asociados
            tiene_skus = skus and len(skus) > 0
            nombre_producto_pendiente = None
            
            if not tiene_skus:
                # Es un producto pendiente (sin SKU asociado)
                nombre_producto_pendiente = item_data.get('descripcion', '')[:255]
            
            # Crear el detalle del item
            detalle = Cotizacion_Empresa_Detalle.objects.create(
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
            
            # Guardar TODOS los SKUs asociados
            if tiene_skus:
                for sku_data in skus:
                    producto_talla_id = sku_data.get('producto_talla_id')
                    if producto_talla_id:
                        Cotizacion_Empresa_Detalle_SKU.objects.create(
                            detalle=detalle,
                            producto_talla_id=producto_talla_id,
                            cantidad=int(sku_data.get('cantidad', 1)),
                            costo_unitario=Decimal(str(sku_data.get('costo', 0))),
                            precio_unitario=Decimal(str(item_data['precio_unitario'])),
                        )
                        print(f"    ✅ SKU guardado: {sku_data.get('sku')} x{sku_data.get('cantidad')}")
        
        # Recalcular totales
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
        print(f"Error en crear_cotizacion: {str(e)}")
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

        # Solo se pueden editar cotizaciones vigentes
        if cotizacion.estado != Cotizacion_Empresa.ESTADO_VIGENTE:
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden editar cotizaciones vigentes'
            })

        print(f"📝 Editando cotización {cotizacion.numero_cotizacion}")

        # Actualizar datos generales
        cotizacion.descripcion = data.get('descripcion', cotizacion.descripcion)
        cotizacion.observaciones = data.get('observaciones', cotizacion.observaciones)
        
        # Actualizar cliente si se envía
        cliente_id = data.get('cliente_id')
        if cliente_id:
            cotizacion.cliente_id = cliente_id
        
        cotizacion.save()

        # Actualizar items si se envían
        items_data = data.get('items', [])
        if items_data:
            print(f"📦 Actualizando {len(items_data)} items")
            
            # Eliminar items anteriores (esto también elimina los SKUs por cascade)
            cotizacion.items.all().delete()
            
            # Crear nuevos items
            for idx, item_data in enumerate(items_data, start=1):
                # Obtener SKUs asociados si existen
                skus = item_data.get('skus', [])
                print(f"  📋 Item {idx}: {item_data.get('descripcion', '')[:30]}... - {len(skus)} SKUs")

                # Determinar si tiene productos asociados
                tiene_skus = skus and len(skus) > 0
                nombre_producto_pendiente = None

                if not tiene_skus:
                    # Es un producto pendiente (sin SKU asociado)
                    nombre_producto_pendiente = item_data.get('descripcion', '')[:255]
                    print(f"    ⚠️ Producto manual/pendiente")

                # Crear el detalle del item
                detalle = Cotizacion_Empresa_Detalle.objects.create(
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

                # Guardar TODOS los SKUs asociados
                if tiene_skus:
                    for sku_data in skus:
                        producto_talla_id = sku_data.get('producto_talla_id')
                        if producto_talla_id:
                            Cotizacion_Empresa_Detalle_SKU.objects.create(
                                detalle=detalle,
                                producto_talla_id=producto_talla_id,
                                cantidad=int(sku_data.get('cantidad', 1)),
                                costo_unitario=Decimal(str(sku_data.get('costo', 0))),
                                precio_unitario=Decimal(str(item_data['precio_unitario'])),
                            )
                            print(f"    ✅ SKU guardado: {sku_data.get('sku')} x{sku_data.get('cantidad')}")

            # Recalcular totales
            cotizacion.calcular_totales()

        # Registrar en historial
        Historial_Cotizacion.objects.create(
            cotizacion=cotizacion,
            usuario=request.user,
            accion='MODIFICADA',
            descripcion=f'Cotización modificada por {request.user.username}',
            ip_address=get_client_ip(request)
        )

        print(f"✅ Cotización {cotizacion.numero_cotizacion} actualizada exitosamente")

        return JsonResponse({
            'success': True,
            'message': 'Cotización actualizada exitosamente'
        })

    except Exception as e:
        print(f"❌ Error en editar_cotizacion: {str(e)}")
        import traceback
        traceback.print_exc()
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
        print(f"Error en anular_cotizacion: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_http_methods(["POST"])
def convertir_cotizacion_factura(request):
    """
    API para convertir una cotización en factura.

    Acepta `forzar_con_pendientes=True` para facturar aunque haya ítems sin SKU.
    En ese caso la cotización queda con estado_despacho=PENDIENTE y los ítems
    sin SKU se resolverán después desde la sección "Despacho diferido".
    """
    try:
        data = json.loads(request.body)
        cotizacion_id       = data.get('cotizacion_id')
        forzar_con_pendientes = data.get('forzar_con_pendientes', False)

        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)

        # Verificar que pertenece a la sucursal del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if sucursal_id and cotizacion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para convertir esta cotización'
            }, status=403)

        # Verificar que está vigente
        if not cotizacion.esta_vigente:
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden facturar cotizaciones vigentes'
            })

        # Contar ítems sin SKU
        items_sin_sku = 0
        for item in cotizacion.items.all():
            tiene_sku = item.skus_asociados.exists() or item.producto_existente is not None
            if not tiene_sku:
                items_sin_sku += 1

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

        # Facturar
        tiene_pendientes = items_sin_sku > 0
        numero_factura = f"F-{cotizacion.numero_cotizacion}"
        cotizacion.marcar_como_facturada(numero_factura, tiene_pendientes=tiene_pendientes)

        # Registrar en historial
        descripcion_hist = f'Cotización convertida a factura {numero_factura}'
        if tiene_pendientes:
            descripcion_hist += f'. {items_sin_sku} ítem(s) con despacho diferido pendiente.'
        Historial_Cotizacion.objects.create(
            cotizacion=cotizacion,
            usuario=request.user,
            accion='FACTURADA',
            descripcion=descripcion_hist,
            datos_nuevos={'numero_factura': numero_factura, 'items_pendientes': items_sin_sku},
            ip_address=get_client_ip(request)
        )

        return JsonResponse({
            'success': True,
            'message': 'Cotización convertida a factura exitosamente',
            'numero_factura': numero_factura,
            'tiene_despacho_pendiente': tiene_pendientes,
            'estado_despacho': cotizacion.estado_despacho,
        })

    except Exception as e:
        print(f"Error en convertir_cotizacion_factura: {str(e)}")
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
        
        print(f"🔄 Cargando cotización {cotizacion.numero_cotizacion} como ticket para POS")
        
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
            # Intentar obtener el producto desde los SKUs asociados
            sku_rel = item.skus_asociados.first()
            
            if sku_rel and sku_rel.producto_talla:
                pt = sku_rel.producto_talla
                producto = pt.producto
                
                # Verificar stock disponible en la sucursal actual
                stock_actual = pt.stock_sucursal(sucursal_id)
                cantidad_requerida = item.cantidad
                
                if stock_actual < cantidad_requerida:
                    items_sin_stock.append({
                        'descripcion': item.descripcion,
                        'sku': str(pt.sku),
                        'stock_actual': stock_actual,
                        'cantidad_requerida': cantidad_requerida
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
                    # Información adicional de cotización
                    'cotizacion_item_id': item.id,
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

        print(f"✅ Cotización cargada: {len(productos)} productos con SKU, {len(items_pendientes)} pendientes, total ${totales['total']:,.0f}")
        
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
        print(f"❌ Error en cargar_cotizacion_como_ticket: {str(e)}")
        import traceback
        traceback.print_exc()
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
    3. Decrementa Producto_Talla.stock.
    4. Crea Movimientos_Producto (EGRESO / DESPACHO_COTIZACION).
    5. Registra Historial_Cotizacion (SKU_ASIGNADO).
    6. Actualiza estado_despacho de la cotización.
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

        # Validar que la cotización esté FACTURADA
        if cotizacion.estado != Cotizacion_Empresa.ESTADO_FACTURADA:
            return JsonResponse({
                'success': False,
                'error': 'Solo se puede asignar SKU a cotizaciones ya facturadas'
            }, status=400)

        # Validar que el ítem realmente está pendiente
        tiene_sku = detalle.skus_asociados.exists() or (
            detalle.producto_existente is not None and not detalle.sku_asignado_post_factura
        )
        if tiene_sku and not detalle.es_producto_pendiente:
            return JsonResponse({
                'success': False,
                'error': 'Este ítem ya tiene un SKU asignado'
            }, status=400)

        if cantidad <= 0 or cantidad > detalle.cantidad:
            return JsonResponse({
                'success': False,
                'error': f'La cantidad debe estar entre 1 y {detalle.cantidad}'
            }, status=400)

        producto_talla = get_object_or_404(Producto_Talla, pk=producto_talla_id)

        # Verificar stock disponible
        stock_actual = producto_talla.stock_sucursal(sucursal_id) if sucursal_id else producto_talla.stock
        if stock_actual < cantidad:
            return JsonResponse({
                'success': False,
                'error': f'Stock insuficiente. Disponible: {stock_actual}, requerido: {cantidad}'
            }, status=400)

        from django.db import transaction
        with transaction.atomic():
            # 1. Crear/actualizar Cotizacion_Empresa_Detalle_SKU
            Cotizacion_Empresa_Detalle_SKU.objects.create(
                detalle=detalle,
                producto_talla=producto_talla,
                cantidad=cantidad,
                costo_unitario=producto_talla.producto.costo if producto_talla.producto else 0,
                precio_unitario=detalle.precio_unitario,
            )

            # 2. Vincular producto_existente en el detalle y marcar como resuelto
            detalle.producto_existente = producto_talla
            detalle.es_producto_pendiente = False
            detalle.sku_asignado_post_factura = True
            detalle.fecha_asignacion_sku = timezone.now()
            detalle.usuario_asignacion_sku = request.user
            detalle.save(update_fields=[
                'producto_existente', 'es_producto_pendiente',
                'sku_asignado_post_factura', 'fecha_asignacion_sku', 'usuario_asignacion_sku'
            ])

            # 3. Decrementar stock
            producto_talla.stock -= cantidad
            producto_talla.save(update_fields=['stock'])

            # 4. Crear movimiento de inventario
            sucursal_obj = cotizacion.sucursal
            Movimientos_Producto.objects.create(
                ProductoTalla=producto_talla,
                sucursal_origen=sucursal_obj,
                cantidad=-cantidad,
                costo=producto_talla.producto.costo if producto_talla.producto else 0,
                precio=int(detalle.precio_unitario),
                concepto='DESPACHO_COTIZACION',
                tipo_movimiento='EGRESO',
                estado='COMPLETADO',
                responsable=request.user.get_full_name() or request.user.username,
                referencia_externa=cotizacion.numero_cotizacion,
                observaciones=f'Despacho diferido cotización {cotizacion.numero_cotizacion} - {detalle.descripcion[:80]}',
                fecha=timezone.localdate(),
                hora=timezone.localtime().time(),
            )

            # 5. Registrar historial
            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='SKU_ASIGNADO',
                descripcion=(
                    f'SKU {producto_talla.sku} asignado al ítem "{detalle.descripcion[:60]}" '
                    f'(x{cantidad}). Stock descontado.'
                ),
                datos_nuevos={
                    'detalle_id': detalle.id,
                    'producto_talla_id': producto_talla.id,
                    'sku': str(producto_talla.sku),
                    'cantidad': cantidad,
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
            'message': f'SKU {producto_talla.sku} asignado correctamente',
            'estado_despacho': cotizacion.estado_despacho,
            'despacho_completado': cotizacion.estado_despacho == Cotizacion_Empresa.DESPACHO_COMPLETADO,
            'sku': str(producto_talla.sku),
            'nombre_producto': producto_talla.producto.articulo if producto_talla.producto else '',
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
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

        print(f"🔍 Búsqueda de productos - Query: '{query}', Stock: {filtro_stock}, Sucursal: {sucursal_id}")

        if not query or len(query) < 2:
            return JsonResponse({
                'success': True,
                'productos': []
            })

        # Construir filtros de búsqueda
        # SKU es BigIntegerField, así que buscamos si es numérico
        filtros = Q(producto__articulo__icontains=query) | Q(producto__descripcion__icontains=query)

        # Si el query es numérico, buscar también por SKU
        if query.isdigit():
            filtros |= Q(sku=int(query))  # Búsqueda exacta
            filtros |= Q(sku__gte=int(query), sku__lt=int(query) + 10000)  # Rango aproximado

        # Buscar por marca (atributo1)
        filtros |= Q(producto__atributo1__valor__icontains=query)

        # Buscar por talla
        filtros |= Q(talla__icontains=query)

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
        
        # Ordenar: primero los que tienen stock en la sucursal, luego por nombre
        productos_con_stock.sort(key=lambda x: (-x['stock_sucursal'], x['producto_talla'].producto.articulo if x['producto_talla'].producto else ''))
        
        # Paginación
        pagina = int(request.GET.get('pagina', 1))
        por_pagina = int(request.GET.get('por_pagina', 12))  # 12 por página por defecto
        
        total_productos = len(productos_con_stock)
        total_paginas = (total_productos + por_pagina - 1) // por_pagina  # Redondeo hacia arriba
        
        # Calcular offset
        offset = (pagina - 1) * por_pagina
        productos_paginados = productos_con_stock[offset:offset + por_pagina]

        print(f"✅ Productos encontrados: {total_productos} | Página {pagina}/{total_paginas} | Sucursal: {sucursal_id}")
        
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
            print(f"📦 {producto_info['nombre']} | SKU:{producto_info['sku']} | Stock:{stock} | Costo:${costo} | PV:${precio_venta} | Margen:{margen_porcentaje}%")
        
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
        print(f"❌ Error en buscar_productos_cotizacion: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== FUNCIONES AUXILIARES ====================

def generar_numero_cotizacion(sucursal):
    """
    Genera un número único de cotización
    """
    fecha_actual = timezone.localdate()
    prefijo = f"COT-{fecha_actual.year}{fecha_actual.month:02d}"
    
    # Buscar el último número de cotización con este prefijo
    ultima_cotizacion = Cotizacion_Empresa.objects.filter(
        numero_cotizacion__startswith=prefijo
    ).order_by('-numero_cotizacion').first()
    
    if ultima_cotizacion:
        # Extraer el número y sumar 1
        ultimo_numero = int(ultima_cotizacion.numero_cotizacion.split('-')[-1])
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
        print(f"Error en actualizar_email_cliente: {str(e)}")
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
        print(f"Error en crear_cliente_cotizacion: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== GENERACIÓN DE PDF ====================

@login_required
def cotizacion_pdf(request, cotizacion_id):
    """
    Genera un PDF profesional de la cotización
    """
    try:
        cotizacion = get_object_or_404(Cotizacion_Empresa, pk=cotizacion_id)
        
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
        # Número de cotización grande
        elements.append(Paragraph(f"COTIZACIÓN", style_title))
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
        elements.append(Paragraph("📋 INFORMACIÓN DEL CLIENTE", style_section))
        
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
            elements.append(Paragraph("📝 DESCRIPCIÓN", style_section))
            elements.append(Paragraph(cotizacion.descripcion, style_normal))
            elements.append(Spacer(1, 15))
        
        # ===== DETALLE DE ITEMS =====
        elements.append(Paragraph("🛒 DETALLE DE PRODUCTOS/SERVICIOS", style_section))
        
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
            elements.append(Paragraph("📌 OBSERVACIONES", style_section))
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
        
        # Crear respuesta HTTP
        response = HttpResponse(content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="cotizacion_{cotizacion.numero_cotizacion}.pdf"'
        response.write(pdf)
        
        return response
        
    except Exception as e:
        print(f"Error generando PDF: {str(e)}")
        import traceback
        traceback.print_exc()
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
        
        # Obtener nombre de la sucursal/empresa
        nombre_empresa = cotizacion.sucursal.nombre if cotizacion.sucursal else "Nuestra Empresa"
        
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
            msg.send(fail_silently=False)
            
            # Registrar en historial
            Historial_Cotizacion.objects.create(
                cotizacion=cotizacion,
                usuario=request.user,
                accion='ENVIADA_EMAIL',
                descripcion=f'Cotización enviada por correo a {email_destino}',
                ip_address=get_client_ip(request)
            )
            
            print(f"✅ Cotización {cotizacion.numero_cotizacion} enviada a {email_destino}")
            
            return JsonResponse({
                'success': True,
                'message': f'Cotización enviada exitosamente a {email_destino}'
            })
            
        except Exception as mail_error:
            print(f"❌ Error enviando correo: {str(mail_error)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({
                'success': False,
                'error': f'Error al enviar el correo: {str(mail_error)}'
            }, status=500)
        
    except Exception as e:
        print(f"❌ Error en enviar_cotizacion_correo: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

