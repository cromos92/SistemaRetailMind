"""
Vistas para el módulo de cotizaciones a empresas
"""

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db.models import Q, Sum, Count
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from datetime import datetime, timedelta, date
from decimal import Decimal
import json

from .models import (
    Cotizacion_Empresa, Cotizacion_Empresa_Detalle, Historial_Cotizacion,
    Empresa, Sucursal, Producto_Talla, Vendedor
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
        }
        
        # Paginación
        total = cotizaciones.count()
        inicio = (page - 1) * per_page
        fin = inicio + per_page
        cotizaciones_paginadas = cotizaciones[inicio:fin]
        
        # Serializar datos
        cotizaciones_data = []
        for cot in cotizaciones_paginadas:
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
                'monto_total': float(cot.total),
                'total_items': cot.items.count(),
                'descripcion': cot.descripcion or '',
                'dias_restantes': cot.dias_restantes,
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
        for item in cotizacion.items.all().order_by('numero_linea'):
            items_data.append({
                'id': item.id,
                'numero_linea': item.numero_linea,
                'descripcion': item.descripcion,
                'cantidad': item.cantidad,
                'precio_unitario': float(item.precio_unitario),
                'subtotal': float(item.subtotal),
                'producto_nombre': item.nombre_producto,
                'producto_sku': item.sku_producto,
                'es_producto_pendiente': item.es_producto_pendiente,
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
        
        fecha_emision = datetime.strptime(fecha_emision_str, '%Y-%m-%d').date() if fecha_emision_str else date.today()
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
        for idx, item_data in enumerate(items_data, start=1):
            Cotizacion_Empresa_Detalle.objects.create(
                cotizacion=cotizacion,
                numero_linea=idx,
                descripcion=item_data['descripcion'],
                cantidad=int(item_data['cantidad']),
                precio_unitario=Decimal(str(item_data['precio_unitario'])),
                producto_existente_id=item_data.get('producto_id') if item_data.get('producto_id') else None,
                es_producto_pendiente=not bool(item_data.get('producto_id')),
            )
        
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
    API para editar una cotización existente
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
        
        # Actualizar datos
        cotizacion.descripcion = data.get('descripcion', cotizacion.descripcion)
        cotizacion.observaciones = data.get('observaciones', cotizacion.observaciones)
        cotizacion.save()
        
        # Registrar en historial
        Historial_Cotizacion.objects.create(
            cotizacion=cotizacion,
            usuario=request.user,
            accion='MODIFICADA',
            descripcion=f'Cotización modificada por {request.user.username}',
            ip_address=get_client_ip(request)
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Cotización actualizada exitosamente'
        })
        
    except Exception as e:
        print(f"Error en editar_cotizacion: {str(e)}")
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
    API para convertir una cotización en factura
    """
    try:
        data = json.loads(request.body)
        cotizacion_id = data.get('cotizacion_id')
        
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
        
        # Aquí deberías implementar la lógica para crear la factura
        # Por ahora solo marcaremos como facturada
        numero_factura = f"F-{cotizacion.numero_cotizacion}"
        
        cotizacion.marcar_como_facturada(numero_factura)
        
        # Registrar en historial
        Historial_Cotizacion.objects.create(
            cotizacion=cotizacion,
            usuario=request.user,
            accion='FACTURADA',
            descripcion=f'Cotización convertida a factura {numero_factura}',
            ip_address=get_client_ip(request)
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Cotización convertida a factura exitosamente',
            'numero_factura': numero_factura
        })
        
    except Exception as e:
        print(f"Error en convertir_cotizacion_factura: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== APIs DE BÚSQUEDA ====================

@login_required
@require_http_methods(["GET"])
def buscar_productos_cotizacion(request):
    """
    API para buscar productos para asociar a la cotización
    """
    try:
        query = request.GET.get('q', '').strip()
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not query or len(query) < 2:
            return JsonResponse({
                'success': True,
                'productos': []
            })
        
        # Buscar productos
        productos = Producto_Talla.objects.filter(
            Q(producto__sku__icontains=query) |
            Q(producto__articulo__icontains=query) |
            Q(producto__marca__nombre__icontains=query)
        ).select_related('producto', 'producto__marca')[:20]
        
        # Serializar
        productos_data = []
        for pt in productos:
            # Obtener stock del producto
            stock = pt.stock if hasattr(pt, 'stock') else 0
            
            # Obtener precio (necesitarás ajustar según tu lógica de precios)
            precio = 0
            if hasattr(pt.producto, 'precio_venta'):
                precio = float(pt.producto.precio_venta)
            
            productos_data.append({
                'id': pt.id,
                'nombre': pt.producto.articulo if pt.producto else 'Sin nombre',
                'sku': str(pt.producto.sku) if pt.producto else 'N/A',
                'marca': pt.producto.marca.nombre if (pt.producto and pt.producto.marca) else 'Sin marca',
                'talla': pt.talla if pt.talla else 'N/A',
                'precio': precio,
                'stock': stock,
            })
        
        return JsonResponse({
            'success': True,
            'productos': productos_data
        })
        
    except Exception as e:
        print(f"Error en buscar_productos_cotizacion: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# ==================== FUNCIONES AUXILIARES ====================

def generar_numero_cotizacion(sucursal):
    """
    Genera un número único de cotización
    """
    fecha_actual = date.today()
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

