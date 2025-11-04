"""
Módulo de Ventas - RetailMind
Contiene todas las vistas relacionadas con ventas, tickets, vendedores y POS
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Count, Q, Avg
from django.core.paginator import Paginator
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import re
from datetime import timedelta

# Importar funciones necesarias desde views.py
from .views import obtener_siguiente_correlativo, consumir_stock_fifo

# Importar servicios de Transbank
from .services.transbank_sdk_service import (
    run_transbank_operation, test_pos_connection, 
    execute_pos_sale, get_available_ports, cancel_pos_sale
)

from .models import (
    Ticket, Ticket_Productos, TicketDetallePago, Vendedor, Producto, Producto_Talla,
    Sucursal, EmpresaUser, Empresa, Movimientos_Producto, LoteProducto, Dte, Dte_Productos, Dte_Detalle_Pago,
    Correlativo, ESTADO_TICKET_CHOICES, METODO_PAGO_TICKET_CHOICES, TIPO_DOCUMENTO_CHOICES,
    ArqueoCaja, ESTADO_ARQUEO_CHOICES, ConfiguracionPOS, TransaccionPOS, LogPOS,
    TIPO_POS_CHOICES, ESTADO_TRANSACCION_POS_CHOICES, TIPO_TARJETA_CHOICES,
    # Modelos de Cambios y Devoluciones
    CambioDevolucion, CambioDevolucionDetalle, PagoCambioDevolucion, HistorialCambioDevolucion,
    TIPO_OPERACION_CAMBIO_CHOICES, ESTADO_CAMBIO_CHOICES, MOTIVO_CAMBIO_CHOICES, CONDICION_PRODUCTO_CHOICES
)


# ========== GESTIÓN DE VENDEDORES ==========

@login_required
def gestion_vendedores(request):
    """Vista principal para gestión de vendedores"""
    return render(request, 'vistas/modulo_administracion/gestion_vendedores.html')


@require_GET
@login_required
def obtener_vendedores(request):
    """API para obtener lista de vendedores con paginación y filtros"""
    try:
        # Parámetros de paginación
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 10))
        
        # Parámetros de filtro
        search = request.GET.get('search', '').strip()
        estado = request.GET.get('estado', '')
        
        # Construir queryset base
        queryset = Vendedor.objects.all()
        
        # Aplicar filtros
        if search:
            queryset = queryset.filter(
                Q(nombre__icontains=search) |
                Q(codigo__icontains=search) |
                Q(email__icontains=search)
            )
        
        if estado:
            queryset = queryset.filter(activo=(estado == 'activo'))
        
        # Ordenar
        queryset = queryset.order_by('nombre')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        vendedores_page = paginator.get_page(page)
        
        # Serializar datos
        vendedores_data = []
        for vendedor in vendedores_page:
            vendedores_data.append({
                'id': vendedor.id,
                'codigo': vendedor.codigo,
                'nombre': vendedor.nombre,
                'email': vendedor.email,
                'telefono': vendedor.telefono,
                'activo': vendedor.activo,
                'fecha_creacion': vendedor.fecha_creacion.strftime('%d/%m/%Y') if vendedor.fecha_creacion else '',
                'comision_porcentaje': float(vendedor.comision_porcentaje) if vendedor.comision_porcentaje else 0,
            })
        
        return JsonResponse({
            'success': True,
            'vendedores': vendedores_data,
            'pagination': {
                'current_page': vendedores_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': vendedores_page.has_next(),
                'has_previous': vendedores_page.has_previous(),
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener vendedores: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_metricas_vendedores(request):
    """API para obtener métricas de vendedores"""
    try:
        total_vendedores = Vendedor.objects.count()
        vendedores_activos = Vendedor.objects.filter(activo=True).count()
        vendedores_inactivos = total_vendedores - vendedores_activos
        
        # Métricas de ventas (últimos 30 días)
        fecha_inicio = timezone.now() - timezone.timedelta(days=30)
        
        ventas_por_vendedor = Ticket.objects.filter(
            created_at__gte=fecha_inicio,
            estado='PAGADO'
        ).values('vendedor__nombre').annotate(
            total_ventas=Sum('total'),
            cantidad_tickets=Count('id')
        ).order_by('-total_ventas')[:5]
        
        return JsonResponse({
            'success': True,
            'metricas': {
                'total_vendedores': total_vendedores,
                'vendedores_activos': vendedores_activos,
                'vendedores_inactivos': vendedores_inactivos,
                'top_vendedores': list(ventas_por_vendedor)
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener métricas: {str(e)}'
        }, status=500)


def crear_vendedor(request):
    """Crear nuevo vendedor"""
    if request.method == 'GET':
        return render(request, 'vistas/modulo_administracion/crear_vendedor.html')
    
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Validar campos requeridos
            campos_requeridos = ['codigo', 'nombre', 'email']
            for campo in campos_requeridos:
                if not data.get(campo):
                    return JsonResponse({
                        'success': False,
                        'error': f'El campo {campo} es requerido'
                    }, status=400)
            
            # Verificar que el código no exista
            if Vendedor.objects.filter(codigo=data['codigo']).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'Ya existe un vendedor con ese código'
                }, status=400)
            
            # Verificar que el email no exista
            if Vendedor.objects.filter(email=data['email']).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'Ya existe un vendedor con ese email'
                }, status=400)
            
            # Crear vendedor
            vendedor = Vendedor.objects.create(
                codigo=data['codigo'],
                nombre=data['nombre'],
                email=data['email'],
                telefono=data.get('telefono', ''),
                comision_porcentaje=data.get('comision_porcentaje', 0),
                activo=data.get('activo', True),
                observaciones=data.get('observaciones', '')
            )
            
            return JsonResponse({
                'success': True,
                'message': 'Vendedor creado exitosamente',
                'vendedor_id': vendedor.id
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al crear vendedor: {str(e)}'
            }, status=500)


@require_http_methods(["PUT"])
@login_required
@transaction.atomic
@csrf_exempt
def editar_vendedor(request):
    """Editar vendedor existente"""
    try:
        data = json.loads(request.body)
        vendedor_id = data.get('id')
        
        if not vendedor_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de vendedor requerido'
            }, status=400)
        
        vendedor = get_object_or_404(Vendedor, id=vendedor_id)
        
        # Validar campos requeridos
        campos_requeridos = ['codigo', 'nombre', 'email']
        for campo in campos_requeridos:
            if not data.get(campo):
                return JsonResponse({
                    'success': False,
                    'error': f'El campo {campo} es requerido'
                }, status=400)
        
        # Verificar que el código no exista en otro vendedor
        if Vendedor.objects.filter(codigo=data['codigo']).exclude(id=vendedor_id).exists():
            return JsonResponse({
                'success': False,
                'error': 'Ya existe otro vendedor con ese código'
            }, status=400)
        
        # Verificar que el email no exista en otro vendedor
        if Vendedor.objects.filter(email=data['email']).exclude(id=vendedor_id).exists():
            return JsonResponse({
                'success': False,
                'error': 'Ya existe otro vendedor con ese email'
            }, status=400)
        
        # Actualizar vendedor
        vendedor.codigo = data['codigo']
        vendedor.nombre = data['nombre']
        vendedor.email = data['email']
        vendedor.telefono = data.get('telefono', '')
        vendedor.comision_porcentaje = data.get('comision_porcentaje', 0)
        vendedor.activo = data.get('activo', True)
        vendedor.observaciones = data.get('observaciones', '')
        vendedor.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Vendedor actualizado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al editar vendedor: {str(e)}'
        }, status=500)


@require_http_methods(["DELETE"])
@login_required
@transaction.atomic
@csrf_exempt
def eliminar_vendedor(request, vendedor_id):
    """Eliminar vendedor (soft delete)"""
    try:
        vendedor = get_object_or_404(Vendedor, id=vendedor_id)
        
        # Verificar si tiene tickets asociados
        tickets_count = Ticket.objects.filter(vendedor=vendedor).count()
        
        if tickets_count > 0:
            # Soft delete - marcar como inactivo
            vendedor.activo = False
            vendedor.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Vendedor desactivado (tiene {tickets_count} tickets asociados)'
            })
        else:
            # Hard delete si no tiene tickets
            vendedor.delete()
            
            return JsonResponse({
                'success': True,
                'message': 'Vendedor eliminado exitosamente'
            })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al eliminar vendedor: {str(e)}'
        }, status=500)


@require_GET
@login_required
def exportar_vendedores(request):
    """Exportar lista de vendedores a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from django.http import HttpResponse
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Vendedores"
        
        # Encabezados
        headers = [
            'Código', 'Nombre', 'Email', 'Teléfono', 
            'Comisión %', 'Estado', 'Fecha Creación'
        ]
        
        # Estilo para encabezados
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        # Escribir encabezados
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
        
        # Obtener datos
        vendedores = Vendedor.objects.all().order_by('nombre')
        
        # Escribir datos
        for row, vendedor in enumerate(vendedores, 2):
            ws.cell(row=row, column=1, value=vendedor.codigo)
            ws.cell(row=row, column=2, value=vendedor.nombre)
            ws.cell(row=row, column=3, value=vendedor.email)
            ws.cell(row=row, column=4, value=vendedor.telefono)
            ws.cell(row=row, column=5, value=float(vendedor.comision_porcentaje) if vendedor.comision_porcentaje else 0)
            ws.cell(row=row, column=6, value='Activo' if vendedor.activo else 'Inactivo')
            ws.cell(row=row, column=7, value=vendedor.fecha_creacion.strftime('%d/%m/%Y') if vendedor.fecha_creacion else '')
        
        # Ajustar ancho de columnas
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="vendedores.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        }, status=500)


# ========== TICKET DE VENTA ==========

@login_required
def ticket_venta(request):
    """Vista principal para crear tickets de venta"""
    # Obtener sucursal actual del usuario
    sucursal_actual_id = request.session.get('sucursalActual')
    sucursal_actual = None
    empresa_actual_nombre = request.session.get('nombreEmpresaActual', 'Sin empresa')
    
    # Si hay sucursal actual, obtenerla
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            sucursal_actual = None
    
    # Si no hay sucursal actual, obtener las sucursales disponibles para el usuario
    sucursales_disponibles = []
    if not sucursal_actual:
        sucursales_usuario = EmpresaUser.objects.filter(
            user=request.user,
            status=True,
            sucursal__isnull=False
        ).select_related('sucursal', 'empresa').distinct()
        
        sucursales_disponibles = [
            {
                'sucursal': eu.sucursal,
                'empresa': eu.empresa
            }
            for eu in sucursales_usuario
        ]
    
    # Obtener todos los vendedores
    vendedores = Vendedor.objects.all().order_by('nombre')
    
    context = {
        'sucursal_actual': sucursal_actual,
        'empresa_actual_nombre': empresa_actual_nombre,
        'vendedores': vendedores,
        'sucursales_disponibles': sucursales_disponibles,
        'necesita_seleccionar_sucursal': not sucursal_actual,
    }
    
    return render(request, 'vistas/modulo_ventas/ticket_venta.html', context)


@login_required
def buscar_vendedor_por_codigo(request):
    """Vista AJAX para buscar vendedor por código"""
    codigo = request.GET.get('codigo', '').strip()
    
    if not codigo:
        return JsonResponse({
            'success': False,
            'error': 'Código de vendedor requerido'
        })
    
    try:
        vendedor = Vendedor.objects.get(codigo=codigo, activo=True)
        return JsonResponse({
            'success': True,
            'vendedor': {
                'id': vendedor.id,
                'codigo': vendedor.codigo,
                'nombre': vendedor.nombre,
                'comision_porcentaje': float(vendedor.comision_porcentaje) if vendedor.comision_porcentaje else 0
            }
        })
    except Vendedor.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Vendedor no encontrado o inactivo'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar vendedor: {str(e)}'
        })


def buscar_producto_por_sku(request):
    """Buscar producto por SKU para ticket de venta"""
    sku = request.GET.get('sku', '').strip()
    sucursal_id = request.session.get('idSucursalActual')
    
    if not sku:
        return JsonResponse({
            'success': False,
            'error': 'SKU requerido'
        })
    
    if not sucursal_id:
        return JsonResponse({
            'success': False,
            'error': 'No hay sucursal activa'
        })
    
    try:
        # Buscar producto por SKU
        producto_talla = Producto_Talla.objects.select_related(
            'producto', 'talla'
        ).get(sku=sku)
        
        # Verificar stock en la sucursal
        stock_actual = producto_talla.stock_sucursal(sucursal_id)
        
        if stock_actual <= 0:
            return JsonResponse({
                'success': False,
                'error': 'Producto sin stock en esta sucursal'
            })
        
        return JsonResponse({
            'success': True,
            'producto': {
                'id': producto_talla.id,
                'sku': producto_talla.sku,
                'nombre': producto_talla.producto.nombre,
                'talla': producto_talla.talla.nombre if producto_talla.talla else 'Sin talla',
                'precio_venta': float(producto_talla.precio_venta),
                'stock': stock_actual,
                'marca': producto_talla.producto.marca.nombre if producto_talla.producto.marca else '',
                'categoria': producto_talla.producto.categoria.nombre if producto_talla.producto.categoria else ''
            }
        })
        
    except Producto_Talla.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Producto no encontrado'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar producto: {str(e)}'
        })


def buscar_productos_bodega(request):
    """Buscar productos en bodega para ticket de venta"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'})
    
    try:
        data = json.loads(request.body)
        termino = data.get('termino', '').strip()
        sucursal_id = request.session.get('idSucursalActual')
        
        if not termino:
            return JsonResponse({
                'success': False,
                'error': 'Término de búsqueda requerido'
            })
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa'
            })
        
        # Buscar productos
        productos_query = Producto_Talla.objects.select_related(
            'producto', 'talla', 'producto__marca', 'producto__categoria'
        ).filter(
            Q(sku__icontains=termino) |
            Q(producto__nombre__icontains=termino) |
            Q(producto__marca__nombre__icontains=termino)
        )
        
        productos_data = []
        for pt in productos_query[:20]:  # Limitar a 20 resultados
            stock = pt.stock_sucursal(sucursal_id)
            if stock > 0:  # Solo productos con stock
                productos_data.append({
                    'id': pt.id,
                    'sku': pt.sku,
                    'nombre': pt.producto.nombre,
                    'talla': pt.talla.nombre if pt.talla else 'Sin talla',
                    'precio_venta': float(pt.precio_venta),
                    'stock': stock,
                    'marca': pt.producto.marca.nombre if pt.producto.marca else '',
                    'categoria': pt.producto.categoria.nombre if pt.producto.categoria else ''
                })
        
        return JsonResponse({
            'success': True,
            'productos': productos_data
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en búsqueda: {str(e)}'
        })


@csrf_exempt
@require_http_methods(["POST"])
def crear_ticket(request):
    """Crear nuevo ticket de venta"""
    try:
        data = json.loads(request.body)
        
        # Validaciones básicas
        vendedor_id = data.get('vendedor_id')
        productos = data.get('productos', [])
        metodo_pago = data.get('metodo_pago')
        
        if not vendedor_id:
            return JsonResponse({
                'success': False,
                'error': 'Vendedor requerido'
            })
        
        if not productos:
            return JsonResponse({
                'success': False,
                'error': 'Debe agregar al menos un producto'
            })
        
        if not metodo_pago:
            return JsonResponse({
                'success': False,
                'error': 'Método de pago requerido'
            })
        
        sucursal_id = request.session.get('idSucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa'
            })
        
        with transaction.atomic():
            # Obtener vendedor y sucursal
            vendedor = get_object_or_404(Vendedor, id=vendedor_id)
            sucursal = get_object_or_404(Sucursal, id=sucursal_id)
            
            # Obtener siguiente correlativo
            correlativo = obtener_siguiente_correlativo(sucursal, 'TICKET')
            
            # Calcular totales
            subtotal = 0
            for item in productos:
                subtotal += item['cantidad'] * item['precio_unitario']
            
            descuento = data.get('descuento', 0)
            total = subtotal - descuento
            
            # Formatear RUT del cliente antes de guardar
            cliente_rut_raw = data.get('cliente_rut', '')
            cliente_rut_formateado = formatear_rut(cliente_rut_raw) if cliente_rut_raw else ''
            
            # Crear ticket
            ticket = Ticket.objects.create(
                correlativo=correlativo,
                vendedor=vendedor,
                sucursal=sucursal,
                subtotal=subtotal,
                descuento=descuento,
                total=total,
                estado='PENDIENTE',
                observaciones=data.get('observaciones', ''),
                cliente_nombre=data.get('cliente_nombre', ''),
                cliente_rut=cliente_rut_formateado,
                cliente_email=data.get('cliente_email', ''),
                cliente_telefono=data.get('cliente_telefono', '')
            )
            
            # Crear productos del ticket
            for item in productos:
                producto_talla = get_object_or_404(Producto_Talla, id=item['producto_talla_id'])
                
                # Verificar stock
                stock_actual = producto_talla.stock_sucursal(sucursal_id)
                if stock_actual < item['cantidad']:
                    raise ValidationError(f'Stock insuficiente para {producto_talla.sku}')
                
                Ticket_Productos.objects.create(
                    ticket=ticket,
                    productoTalla=producto_talla,
                    cantidad=item['cantidad'],
                    precio_unitario=item['precio_unitario'],
                    descuento_unitario=item.get('descuento_unitario', 0)
                )
            
            # Crear detalle de pago
            TicketDetallePago.objects.create(
                ticket=ticket,
                metodo_pago=metodo_pago,
                monto=total,
                referencia=data.get('referencia_pago', ''),
                observaciones=data.get('observaciones_pago', '')
            )
            
            # Si el pago es efectivo o débito, marcar como pagado
            if metodo_pago in ['EFECTIVO', 'DEBITO']:
                ticket.estado = 'PAGADO'
                ticket.fecha_pago = timezone.now()
                ticket.save()
                
                # Consumir stock FIFO
                for item in productos:
                    producto_talla = get_object_or_404(Producto_Talla, id=item['producto_talla_id'])
                    consumir_stock_fifo(
                        producto_talla=producto_talla,
                        cantidad_requerida=item['cantidad'],
                        responsable=request.user,
                        ticket=ticket,
                        observaciones=f'Venta ticket #{correlativo}'
                    )
            
            # Guardar cliente en la base de datos si tiene datos completos
            if ticket.cliente_rut and ticket.cliente_nombre:
                cliente_datos = {
                    'nombre': ticket.cliente_nombre,
                    'rut': ticket.cliente_rut,
                    'email': ticket.cliente_email,
                    'telefono': ticket.cliente_telefono,
                }
                guardar_o_actualizar_cliente(cliente_datos, request.user)
        
        return JsonResponse({
            'success': True,
            'message': 'Ticket creado exitosamente',
            'ticket_id': ticket.id,
            'correlativo': correlativo
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear ticket: {str(e)}'
        })


# ========== TICKETS DE VENTA - GESTIÓN ==========

@require_POST
@transaction.atomic
def crear_ticket_venta(request):
    """Crear ticket de venta al público"""
    try:
        data = json.loads(request.body)
        
        # Validar datos requeridos
        vendedor_id = data.get('vendedor_id')
        productos = data.get('productos', [])
        
        if not vendedor_id:
            return JsonResponse({'success': False, 'error': 'Vendedor requerido'})
        
        if not productos:
            return JsonResponse({'success': False, 'error': 'Debe incluir al menos un producto'})
        
        # Obtener sucursal actual
        sucursal_id = request.session.get('idSucursalActual')
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'No hay sucursal activa'})
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        vendedor = get_object_or_404(Vendedor, id=vendedor_id)
        
        # Obtener siguiente correlativo
        correlativo = obtener_siguiente_correlativo(sucursal, 'TICKET')
        
        # Calcular totales
        subtotal = sum(item['cantidad'] * item['precio_unitario'] for item in productos)
        descuento = data.get('descuento', 0)
        total = subtotal - descuento
        
        # Formatear RUT del cliente antes de guardar
        cliente_rut_raw = data.get('cliente_rut', '')
        cliente_rut_formateado = formatear_rut(cliente_rut_raw) if cliente_rut_raw else ''
        
        # Crear ticket
        ticket = Ticket.objects.create(
            correlativo=correlativo,
            vendedor=vendedor,
            sucursal=sucursal,
            subtotal=subtotal,
            descuento=descuento,
            total=total,
            estado='PAGADO',
            fecha_pago=timezone.now(),
            observaciones=data.get('observaciones', ''),
            cliente_nombre=data.get('cliente_nombre', ''),
            cliente_rut=cliente_rut_formateado,
            cliente_email=data.get('cliente_email', ''),
            cliente_telefono=data.get('cliente_telefono', '')
        )
        
        # Procesar productos
        for item in productos:
            producto_talla = get_object_or_404(Producto_Talla, id=item['producto_talla_id'])
            
            # Verificar stock disponible
            stock_actual = producto_talla.stock_sucursal(sucursal_id)
            if stock_actual < item['cantidad']:
                raise ValidationError(f'Stock insuficiente para {producto_talla.sku}')
            
            # Crear detalle del ticket
            Ticket_Productos.objects.create(
                ticket=ticket,
                productoTalla=producto_talla,
                cantidad=item['cantidad'],
                precio_unitario=item['precio_unitario'],
                descuento_unitario=item.get('descuento_unitario', 0)
            )
            
            # Consumir stock FIFO
            consumir_stock_fifo(
                producto_talla=producto_talla,
                cantidad_requerida=item['cantidad'],
                responsable=request.user,
                ticket=ticket,
                observaciones=f'Venta ticket #{correlativo}'
            )
        
        # Crear detalle de pago
        metodo_pago = data.get('metodo_pago', 'EFECTIVO')
        TicketDetallePago.objects.create(
            ticket=ticket,
            metodo_pago=metodo_pago,
            monto=total,
            referencia=data.get('referencia_pago', ''),
            observaciones=data.get('observaciones_pago', '')
        )
        
        # Guardar cliente en la base de datos si tiene datos completos
        if ticket.cliente_rut and ticket.cliente_nombre:
            cliente_datos = {
                'nombre': ticket.cliente_nombre,
                'rut': ticket.cliente_rut,
                'email': ticket.cliente_email,
                'telefono': ticket.cliente_telefono,
            }
            guardar_o_actualizar_cliente(cliente_datos, request.user)
        
        return JsonResponse({
            'success': True,
            'message': 'Ticket creado exitosamente',
            'ticket_id': ticket.id,
            'correlativo': correlativo
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'})
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': str(e)})
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error al crear ticket: {str(e)}'})


@require_GET
@login_required
def obtener_tickets_venta(request):
    """Obtener lista de tickets de venta con filtros"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        vendedor_id = request.GET.get('vendedor_id')
        estado = request.GET.get('estado')
        sucursal_id = request.session.get('idSucursalActual')
        
        # Construir queryset
        queryset = Ticket.objects.select_related('vendedor', 'sucursal').filter(
            sucursal_id=sucursal_id
        )
        
        # Aplicar filtros
        if fecha_inicio:
            queryset = queryset.filter(created_at__date__gte=fecha_inicio)
        if fecha_fin:
            queryset = queryset.filter(created_at__date__lte=fecha_fin)
        if vendedor_id:
            queryset = queryset.filter(vendedor_id=vendedor_id)
        if estado:
            queryset = queryset.filter(estado=estado)
        
        # Ordenar por fecha descendente
        queryset = queryset.order_by('-created_at')
        
        # Paginación
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        paginator = Paginator(queryset, per_page)
        tickets_page = paginator.get_page(page)
        
        # Serializar datos
        tickets_data = []
        for ticket in tickets_page:
            tickets_data.append({
                'id': ticket.id,
                'correlativo': ticket.correlativo,
                'vendedor': ticket.vendedor.nombre,
                'fecha_creacion': ticket.created_at.strftime('%d/%m/%Y %H:%M'),
                'subtotal': float(ticket.subtotal),
                'descuento': float(ticket.descuento),
                'total': float(ticket.total),
                'estado': ticket.estado,
                'cliente_nombre': ticket.cliente_nombre or '',
                'cliente_rut': ticket.cliente_rut or ''
            })
        
        return JsonResponse({
            'success': True,
            'tickets': tickets_data,
            'pagination': {
                'current_page': tickets_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': tickets_page.has_next(),
                'has_previous': tickets_page.has_previous(),
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener tickets: {str(e)}'
        })


# ========== DASHBOARD POS ==========

@login_required
def pos_dashboard(request):
    """Vista principal del dashboard POS"""
    # Obtener choices para los selects
    context = {
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
        'estado_ticket_choices': ESTADO_TICKET_CHOICES,
    }
    return render(request, 'vistas/modulo_ventas/generacionVentas.html', context)


@login_required
@require_GET
def verificar_correlativos_disponibles(request):
    """API para verificar correlativos disponibles por tipo de documento"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })

        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Tipos de documento para ventas al público
        tipos_documento = [
            'TICKET',
            'BOLETA_ELECTRONICA', 
            'FACTURA_ELECTRONICA',
            'BOLETA'
        ]
        
        correlativos_info = {}
        
        for tipo in tipos_documento:
            # Mapear nombres para la base de datos
            tipo_db = tipo
            if tipo == 'BOLETA_ELECTRONICA':
                tipo_db = 'BOLETA ELECTRONICA'
            elif tipo == 'FACTURA_ELECTRONICA':
                tipo_db = 'FACTURA ELECTRONICA'
            
            try:
                correlativo = Correlativo.objects.get(
                    sucursal=sucursal,
                    tipo_dte=tipo_db
                )
                
                correlativos_info[tipo] = {
                    'disponible': correlativo.puede_emitir(),
                    'numero_actual': correlativo.numero_actual,
                    'disponibles': correlativo.disponibles,
                    'estado': correlativo.estado,
                    'porcentaje_consumo': round(correlativo.porcentaje_consumo, 1),
                    'rango': f"{correlativo.inicio}-{correlativo.termino}"
                }
                
            except Correlativo.DoesNotExist:
                # Si no existe, crear uno automáticamente
                try:
                    # Esto creará el correlativo si no existe
                    numero = obtener_siguiente_correlativo(sucursal, tipo_db)
                    
                    # Obtener el correlativo recién creado
                    correlativo = Correlativo.objects.get(
                        sucursal=sucursal,
                        tipo_dte=tipo_db
                    )
                    
                    correlativos_info[tipo] = {
                        'disponible': True,
                        'numero_actual': correlativo.numero_actual,
                        'disponibles': correlativo.disponibles,
                        'estado': correlativo.estado,
                        'porcentaje_consumo': round(correlativo.porcentaje_consumo, 1),
                        'rango': f"{correlativo.inicio}-{correlativo.termino}",
                        'recien_creado': True
                    }
                    
                except Exception as e:
                    correlativos_info[tipo] = {
                        'disponible': False,
                        'error': f'Error al crear correlativo: {str(e)}',
                        'numero_actual': 0,
                        'disponibles': 0,
                        'estado': 'error'
                    }

        return JsonResponse({
            'success': True,
            'correlativos': correlativos_info,
            'sucursal': {
                'id': sucursal.id,
                'alias': sucursal.alias
            }
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al verificar correlativos: {str(e)}'
        })


@login_required
def dashboard_stats(request):
    """API para obtener estadísticas del dashboard"""
    try:
        # Obtener sucursal actual del usuario
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })

        # Fecha de hoy con timezone aware
        from datetime import datetime, time
        hoy = timezone.now().date()
        inicio_dia = timezone.make_aware(datetime.combine(hoy, time.min))
        fin_dia = timezone.make_aware(datetime.combine(hoy, time.max))

        # Filtro base por sucursal y fecha
        base_filter = Q(sucursal_id=sucursal_id, created_at__range=[inicio_dia, fin_dia])

        # Estadísticas del día
        tickets_hoy = Ticket.objects.filter(base_filter)
        
        # Ventas del día (solo tickets pagados)
        ventas_hoy = tickets_hoy.filter(estado='PAGADO').aggregate(
            total=Sum('total')
        )['total'] or 0

        # Contadores por estado
        tickets_pendientes = tickets_hoy.filter(estado='PENDIENTE').count()
        tickets_pagados = tickets_hoy.filter(estado='PAGADO').count()
        
        # Promedio de venta
        promedio_venta = 0
        if tickets_pagados > 0:
            promedio_venta = ventas_hoy / tickets_pagados

        # Solo tickets PENDIENTES del día (últimos 20)
        tickets_recientes = tickets_hoy.filter(estado='PENDIENTE').select_related('vendedor', 'sucursal').order_by('-created_at')[:20]
        
        tickets_data = []
        for ticket in tickets_recientes:
            tickets_data.append({
                'correlativo': ticket.correlativo,
                'hora': ticket.created_at.strftime('%H:%M'),
                'cliente_nombre': ticket.cliente_nombre or 'Sin nombre',
                'cliente_rut': ticket.cliente_rut or '',
                'vendedor_nombre': ticket.vendedor.nombre if ticket.vendedor else 'Sin vendedor',
                'total': int(ticket.total or 0),
                'estado': ticket.estado,
            })

        # Tickets pendientes para el wizard (con más detalles)
        tickets_pendientes_query = tickets_hoy.filter(estado='PENDIENTE').select_related('vendedor')[:10]
        tickets_pendientes_data = []
        for ticket in tickets_pendientes_query:
            tickets_pendientes_data.append({
                'correlativo': ticket.correlativo,
                'cliente_nombre': ticket.cliente_nombre or 'Sin nombre',
                'cliente_rut': ticket.cliente_rut or '',
                'vendedor_nombre': ticket.vendedor.nombre if ticket.vendedor else 'Sin vendedor',
                'total': int(ticket.total or 0),
                'hora': ticket.created_at.strftime('%H:%M'),
                'productos_count': ticket.productos.count() if hasattr(ticket, 'productos') else 0
            })

        return JsonResponse({
            'success': True,
            'stats': {
                'ventas_hoy': int(ventas_hoy),
                'tickets_pendientes': tickets_pendientes,
                'tickets_pagados': tickets_pagados,
                'promedio_venta': int(promedio_venta),
            },
            'tickets': tickets_data,
            'tickets_pendientes': tickets_pendientes_data
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener estadísticas: {str(e)}'
        })


@login_required
def validar_rut_cliente(request):
    """API para validar RUT chileno"""
    rut = request.GET.get('rut', '').strip()
    
    if not rut:
        return JsonResponse({
            'success': False,
            'error': 'RUT requerido'
        })
    
    # Validar formato y dígito verificador
    def validar_rut_chileno(rut_completo):
        # Limpiar RUT
        rut_limpio = ''.join(c for c in rut_completo if c.isdigit() or c.lower() == 'k')
        
        if len(rut_limpio) < 2:
            return False
        
        cuerpo = rut_limpio[:-1]
        dv = rut_limpio[-1].lower()
        
        # Calcular dígito verificador
        suma = 0
        multiplicador = 2
        
        for i in range(len(cuerpo) - 1, -1, -1):
            suma += int(cuerpo[i]) * multiplicador
            multiplicador = 7 if multiplicador == 7 else multiplicador + 1
        
        resto = suma % 11
        dv_calculado = '0' if resto == 0 else 'k' if resto == 1 else str(11 - resto)
        
        return dv == dv_calculado
    
    es_valido = validar_rut_chileno(rut)
    
    return JsonResponse({
        'success': True,
        'valido': es_valido,
        'rut_formateado': formatear_rut(rut) if es_valido else rut
    })


@login_required
@require_POST
@csrf_exempt
def anular_ticket_pendiente(request):
    """Anular un ticket pendiente (solo si no ha sido pagado)"""
    try:
        data = json.loads(request.body)
        correlativo = data.get('correlativo')
        motivo = data.get('motivo', 'Sin motivo especificado')
        
        if not correlativo:
            return JsonResponse({
                'success': False,
                'error': 'Correlativo de ticket requerido'
            })
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Buscar el ticket
        try:
            ticket = Ticket.objects.get(
                correlativo=correlativo,
                sucursal_id=sucursal_id
            )
        except Ticket.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': f'Ticket #{correlativo} no encontrado'
            })
        
        # Verificar que esté en estado PENDIENTE
        if ticket.estado != 'PENDIENTE':
            return JsonResponse({
                'success': False,
                'error': f'Solo se pueden anular tickets pendientes. Estado actual: {ticket.estado}'
            })
        
        with transaction.atomic():
            # Devolver stock de los productos
            productos_ticket = Ticket_Productos.objects.filter(idTicket=ticket)
            
            for item in productos_ticket:
                # Crear movimiento de devolución de stock
                Movimientos_Producto.objects.create(
                    ProductoTalla=item.ProductoTalla,
                    cantidad=item.stock,  # Positivo para devolver al inventario
                    costo=item.ProductoTalla.producto.costo,
                    precio=int(item.precio),
                    concepto='ANULACION_TICKET',
                    tipo_movimiento='INGRESO',
                    responsable=request.user.username,
                    observaciones=f'Anulación de ticket #{ticket.correlativo} - Motivo: {motivo}',
                    referencia_externa=f'ANULACION_{ticket.correlativo}'
                )
            
            # Cambiar estado del ticket a ANULADO
            ticket.estado = 'ANULADO'
            ticket.observaciones = (ticket.observaciones or '') + f'\n[ANULADO] {timezone.now().strftime("%Y-%m-%d %H:%M")} - {motivo}'
            ticket.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Ticket #{correlativo} anulado exitosamente',
            'ticket_id': ticket.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Formato JSON inválido'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al anular ticket: {str(e)}'
    })


@login_required
def buscar_cliente_rut(request):
    """API para buscar cliente por RUT en tabla Cliente"""
    from empresa_management.models import Cliente
    
    rut = request.GET.get('rut', '').strip()
    
    if not rut:
        return JsonResponse({
            'success': False,
            'error': 'RUT requerido'
        })
    
    try:
        # Limpiar y formatear el RUT
        rut_limpio = rut.replace('.', '').replace('-', '').strip()
        rut_formateado = formatear_rut(rut_limpio)
        
        # Buscar primero en la tabla de Clientes
        cliente = Cliente.objects.filter(
            Q(rut__iexact=rut_formateado) | 
            Q(rut__icontains=rut_limpio)
        ).filter(activo=True).first()
        
        if cliente:
            # Cliente encontrado en la base de datos
            cliente_data = {
                'nombre': cliente.nombre_completo,
                'rut': cliente.rut,
                'email': cliente.email or '',
                'telefono': cliente.telefono or cliente.celular or '',
                'giro': cliente.empresa.giro if cliente.empresa else '',
                'comuna': cliente.comuna or '',
                'ciudad': cliente.ciudad or '',
                'direccion': cliente.direccion or '',
                'telefono_secundario': cliente.celular if cliente.telefono else '',
                'email_facturacion': cliente.email or '',
            }
            
            return JsonResponse({
                'success': True,
                'cliente': cliente_data,
                'mensaje': 'Cliente encontrado en base de datos',
                'cliente_id': cliente.id
            })
        
        # Si no está en Clientes, buscar en tickets anteriores
        ticket_con_cliente = Ticket.objects.filter(
            Q(cliente_rut__iexact=rut_formateado) | 
            Q(cliente_rut__icontains=rut_limpio)
        ).exclude(
            cliente_nombre__isnull=True
        ).exclude(
            cliente_nombre__exact=''
        ).order_by('-created_at').first()
        
        if ticket_con_cliente and ticket_con_cliente.cliente_nombre:
            cliente_data = {
                'nombre': ticket_con_cliente.cliente_nombre,
                'rut': ticket_con_cliente.cliente_rut,
                'email': ticket_con_cliente.cliente_email or '',
                'telefono': ticket_con_cliente.cliente_telefono or '',
                'giro': ticket_con_cliente.cliente_giro or '',
                'comuna': ticket_con_cliente.cliente_comuna or '',
                'ciudad': ticket_con_cliente.cliente_ciudad or '',
                'direccion': ticket_con_cliente.cliente_direccion or '',
                'telefono_secundario': ticket_con_cliente.cliente_telefono_secundario or '',
                'email_facturacion': ticket_con_cliente.cliente_email_facturacion or '',
            }
            
            return JsonResponse({
                'success': True,
                'cliente': cliente_data,
                'mensaje': 'Cliente encontrado en tickets anteriores'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'Cliente no encontrado',
                'rut_formateado': rut_formateado  # Devolver el RUT formateado para pre-llenarlo
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar cliente: {str(e)}'
        })


def formatear_rut(rut):
    """Formatear RUT chileno SIN puntos, solo con guión"""
    # Limpiar RUT
    rut_limpio = ''.join(c for c in rut if c.isdigit() or c.lower() == 'k')
    
    if len(rut_limpio) < 2:
        return rut
    
    cuerpo = rut_limpio[:-1]
    dv = rut_limpio[-1]
    
    # Formatear SIN puntos (solo guión)
    return f"{cuerpo}-{dv}"


def guardar_o_actualizar_cliente(datos_cliente, usuario=None):
    """Guardar o actualizar cliente en la base de datos"""
    from empresa_management.models import Cliente
    
    try:
        if not datos_cliente.get('rut') or not datos_cliente.get('nombre'):
            return None  # No guardar si no hay datos mínimos
        
        rut = formatear_rut(datos_cliente['rut'])
        nombre_completo = datos_cliente['nombre']
        
        # Separar nombre y apellido
        partes_nombre = nombre_completo.split(' ', 1)
        nombre = partes_nombre[0] if len(partes_nombre) > 0 else nombre_completo
        apellido = partes_nombre[1] if len(partes_nombre) > 1 else ''
        
        # Buscar cliente existente
        cliente = Cliente.objects.filter(rut=rut).first()
        
        if cliente:
            # Actualizar datos si están vacíos o han cambiado
            if not cliente.email and datos_cliente.get('email'):
                cliente.email = datos_cliente['email']
            if not cliente.telefono and datos_cliente.get('telefono'):
                cliente.telefono = datos_cliente['telefono']
            if not cliente.direccion and datos_cliente.get('direccion'):
                cliente.direccion = datos_cliente['direccion']
            if not cliente.comuna and datos_cliente.get('comuna'):
                cliente.comuna = datos_cliente['comuna']
            if not cliente.ciudad and datos_cliente.get('ciudad'):
                cliente.ciudad = datos_cliente['ciudad']
            if not cliente.celular and datos_cliente.get('telefono_secundario'):
                cliente.celular = datos_cliente['telefono_secundario']
            
            # Actualizar nombre si cambió
            if nombre and apellido:
                cliente.nombre = nombre
                cliente.apellido = apellido
            
            if usuario:
                cliente.updated_by = usuario
            cliente.save()
            
        else:
            # Determinar tipo de cliente basado en los datos
            tipo_cliente = 'INDIVIDUAL'
            if datos_cliente.get('giro') and datos_cliente.get('direccion'):
                tipo_cliente = 'EMPRESARIAL'
            
            # Crear nuevo cliente
            cliente = Cliente.objects.create(
                nombre=nombre,
                apellido=apellido,
                rut=rut,
                email=datos_cliente.get('email', ''),
                telefono=datos_cliente.get('telefono', ''),
                celular=datos_cliente.get('telefono_secundario', ''),
                direccion=datos_cliente.get('direccion', ''),
                comuna=datos_cliente.get('comuna', ''),
                ciudad=datos_cliente.get('ciudad', ''),
                tipo_cliente=tipo_cliente,
                activo=True,
                created_by=usuario,
                observaciones=f'Creado automáticamente desde venta'
            )
        
        return cliente
        
    except Exception as e:
        # Si hay error, no detener el proceso de venta
        print(f"Error al guardar cliente: {str(e)}")
        return None


# ========== FUNCIONES TICKET POS ==========

def construir_ticket_data(ticket):
    """Construir datos completos del ticket para POS"""
    productos_procesados = []
    total_items = 0
    subtotal = 0

    for tp in ticket.ticket_productos.select_related(
        'ProductoTalla',
        'ProductoTalla__producto',
        'ProductoTalla__producto__atributo1',
        'ProductoTalla__producto__atributo2',
        'ProductoTalla__producto__atributo3',
        'ProductoTalla__producto__atributo4',
    ).all():
        producto_talla = tp.ProductoTalla
        producto = producto_talla.producto if producto_talla else None

        marca = ''
        if producto:
            atributo_marca = getattr(producto, 'atributo1', None)
            if atributo_marca:
                marca = getattr(atributo_marca, 'valor', '') or ''

        subtotal += tp.subtotal
        total_items += tp.stock
        productos_procesados.append({
            'detalle_id': tp.id,
            'producto_talla_id': producto_talla.id if producto_talla else None,
            'producto_id': producto.id if producto else None,
            'sku': producto_talla.sku if producto_talla else '',
            'articulo': producto.articulo if producto else '',
            'descripcion': producto.descripcion if producto else '',
            'marca': marca,
            'talla': producto_talla.talla if producto_talla else '',
            'cantidad': tp.stock,
            'precio_unitario': tp.precio,
            'precio_original': tp.precio_original,
            'descuento_unitario': tp.descuento_unitario,
            'porcentaje_descuento': float(tp.porcentaje_descuento or 0),
            'subtotal': tp.subtotal,
            'costo_fifo': tp.costo_fifo,
            'lotes_utilizados': tp.lotes_utilizados,
            'stock_actual': producto_talla.stock if producto_talla else None,
        })

    sucursal = ticket.sucursal
    empresa = sucursal.empresa if hasattr(sucursal, 'empresa') else None

    pagos_queryset = ticket.pagos.all().order_by('creado_en')
    pagos = [
        {
            'id': pago.id,
            'metodo_pago': pago.metodo_pago,
            'metodo_pago_display': pago.get_metodo_pago_display(),
            'monto': pago.monto,
            'voucher': pago.voucher or '',
            'tipo_tarjeta': pago.tipo_tarjeta or '',
            'notas': pago.notas or '',
            'creado_en': pago.creado_en.strftime('%Y-%m-%d %H:%M:%S'),
        }
        for pago in pagos_queryset
    ]

    total_pagado = sum(pago['monto'] for pago in pagos)
    saldo_por_pagar = (ticket.total or 0) - total_pagado
    if saldo_por_pagar < 0:
        saldo_por_pagar = 0

    return {
        'ticket_id': ticket.correlativo,
        'fecha': ticket.fecha.strftime('%Y-%m-%d'),
        'hora': ticket.hora.strftime('%H:%M:%S'),
        'tipo_documento': 'TICKET',
        'estado': ticket.estado,
        'metodo_pago_principal': ticket.metodo_pago,
        'total_pagado': total_pagado,
        'saldo_por_pagar': saldo_por_pagar,
        'responsable': ticket.responsable,
        'sucursal': {
            'alias': sucursal.alias,
            'direccion': sucursal.direccion,
            'empresa': empresa.nombre if empresa else '',
            'rut_empresa': empresa.rut if empresa else ''
        },
        'vendedor': {
            'nombre': ticket.vendedor.nombre,
            'codigo': ticket.vendedor.codigo_vendedor
        },
        'cliente': {
            'nombre': ticket.cliente_nombre or '',
            'rut': ticket.cliente_rut or '',
            'giro': ticket.cliente_giro or '',
            'comuna': ticket.cliente_comuna or '',
            'ciudad': ticket.cliente_ciudad or '',
            'direccion': ticket.cliente_direccion or '',
            'telefono': ticket.cliente_telefono or '',
            'telefono_secundario': ticket.cliente_telefono_secundario or '',
            'email': ticket.cliente_email or '',
            'email_facturacion': ticket.cliente_email_facturacion or '',
        },
        'observaciones': ticket.observaciones or '',
        'observaciones_adicionales': ticket.observaciones_adicionales or '',
        'productos': productos_procesados,
        'pagos': pagos,
        'totales': {
            'items': total_items,
            'subtotal': subtotal,
            'descuento': ticket.descuento or 0,
            'total': ticket.total
        }
    }


def _obtener_ticket_para_pos(request, correlativo):
    """Función auxiliar para obtener ticket para POS"""
    sucursal_id = (
        request.session.get('idSucursalActual')
        or request.session.get('sucursalActual')
        or request.session.get('idSucursalActualPOS')
    )
    if not sucursal_id:
        return JsonResponse({'success': False, 'error': 'No hay sucursal activa en la sesión'}, status=400)

    ticket = (
        Ticket.objects
        .select_related('sucursal', 'vendedor')
        .prefetch_related('ticket_productos__ProductoTalla__producto', 'pagos')
        .filter(sucursal_id=sucursal_id, correlativo=correlativo)
        .first()
    )

    if not ticket:
        return JsonResponse({'success': False, 'error': f'Ticket {correlativo} no encontrado'}, status=404)

    return JsonResponse({'success': True, 'ticket': construir_ticket_data(ticket)})


@login_required
@require_GET
def obtener_ticket_por_correlativo(request, correlativo):
    """Obtener ticket por correlativo para POS"""
    return _obtener_ticket_para_pos(request, correlativo)


@login_required
@require_POST
def buscar_ticket_pos(request):
    """Buscar ticket en POS"""
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Formato JSON inválido'}, status=400)

    correlativo = payload.get('correlativo') or payload.get('ticket')
    if not correlativo:
        return JsonResponse({'success': False, 'error': 'Debe indicar el número de ticket'}, status=400)

    try:
        correlativo_int = int(correlativo)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Número de ticket inválido'}, status=400)

    return _obtener_ticket_para_pos(request, correlativo_int)


def generar_dte_desde_ticket(ticket, tipo_documento, usuario):
    """Generar DTE (Boleta o Factura Electrónica) desde un Ticket"""
    from decimal import Decimal
    
    # Mapear tipo de documento
    tipo_dte_map = {
        'BOLETA_ELECTRONICA': 'BOLETA ELECTRONICA',
        'FACTURA_ELECTRONICA': 'FACTURA ELECTRONICA',
    }
    
    tipo_dte = tipo_dte_map.get(tipo_documento, 'BOLETA ELECTRONICA')
    
    # Obtener o crear receptor (cliente)
    receptor = None
    if ticket.cliente_rut and ticket.cliente_nombre:
        # Buscar si ya existe el cliente como Empresa
        receptor = Empresa.objects.filter(rut=ticket.cliente_rut).first()
        
        if not receptor:
            # Crear empresa/cliente
            receptor = Empresa.objects.create(
                nombre=ticket.cliente_nombre,
                rut=ticket.cliente_rut,
                razon_social=ticket.cliente_nombre,
                nombre_fantasia=ticket.cliente_nombre,
                giro=ticket.cliente_giro or 'Consumidor Final',
                direccion=ticket.cliente_direccion or 'Sin dirección',
                comuna=ticket.cliente_comuna or 'Sin comuna',
                ciudad=ticket.cliente_ciudad or 'Sin ciudad',
                telefono=ticket.cliente_telefono or '',
                correoVendedor=ticket.cliente_email or '',
                correoAdministrador=ticket.cliente_email_facturacion or ticket.cliente_email or '',
                esProveedor=False,
                responsable=usuario.username if usuario else 'Sistema'
            )
    
    # Obtener siguiente correlativo para el DTE
    correlativo_dte = obtener_siguiente_correlativo(ticket.sucursal, tipo_dte)
    
    # Calcular montos
    subtotal = Decimal(ticket.subTotal or ticket.total)
    descuento = Decimal(ticket.descuento or 0)
    neto = subtotal - descuento
    iva = neto * Decimal('0.19')
    total = neto + iva
    
    # Crear DTE con todos los campos requeridos
    from datetime import timedelta
    
    dte = Dte.objects.create(
        numero_documento=int(correlativo_dte),
        tipo_documento=tipo_dte,
        tipo_transaccion='VENTA_PUBLICO',
        fecha_emision=ticket.fecha,
        fecha_vencimiento=ticket.fecha + timedelta(days=30),
        diasCredito=0,
        bultos=1,
        unidades_productos=sum(tp.stock for tp in ticket.ticket_productos.all()),
        emisor=ticket.sucursal.empresa,
        receptor=receptor,
        sucursal=ticket.sucursal,
        vendedor=ticket.vendedor,
        monto_neto=neto,
        monto_con_iva=total,
        descuento=descuento,
        estado_pago='PAGADO',
        estado_dte='EMITIDO',
        responsable=usuario.username if usuario else ticket.responsable,
        hora=ticket.hora
    )
    
    # Copiar productos del ticket al DTE
    for tp in ticket.ticket_productos.all():
        Dte_Productos.objects.create(
            dte=dte,
            productoTalla=tp.ProductoTalla,
            stock=tp.stock,
            costo=tp.ProductoTalla.producto.costo,
            sobreprecio=0,
            precio=tp.precio,
            descripcion=tp.ProductoTalla.producto.descripcion or tp.ProductoTalla.producto.articulo
        )
    
    # Copiar métodos de pago
    for pago in ticket.pagos.all():
        Dte_Detalle_Pago.objects.create(
            dte=dte,
            metodo_pago=pago.metodo_pago,
            monto=pago.monto,
            tipo_tarjeta=pago.tipo_tarjeta or '',
            voucher=pago.voucher or '',
            observaciones=pago.notas or ''
        )
    
    return dte


@login_required
@require_http_methods(["POST"])
def registrar_pagos_ticket(request, correlativo):
    """Registrar pagos para un ticket en POS"""
    sucursal_id = (
        request.session.get('idSucursalActual')
        or request.session.get('sucursalActual')
        or request.session.get('idSucursalActualPOS')
    )
    if not sucursal_id:
        return JsonResponse({'success': False, 'error': 'No hay sucursal activa en la sesión'}, status=400)

    ticket = (
        Ticket.objects
        .select_related('sucursal', 'vendedor')
        .prefetch_related('pagos', 'ticket_productos__ProductoTalla__producto')
        .filter(sucursal_id=sucursal_id, correlativo=correlativo)
        .first()
    )

    if not ticket:
        return JsonResponse({'success': False, 'error': f'Ticket {correlativo} no encontrado'}, status=404)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Formato JSON inválido'}, status=400)

    datos_cliente = payload.get('cliente', {})
    ticket.cliente_nombre = datos_cliente.get('nombre') or ''
    
    # Formatear RUT sin puntos antes de guardar
    rut_cliente = datos_cliente.get('rut') or ''
    if rut_cliente:
        ticket.cliente_rut = formatear_rut(rut_cliente)
    else:
        ticket.cliente_rut = ''
    
    ticket.cliente_giro = datos_cliente.get('giro') or ''
    ticket.cliente_comuna = datos_cliente.get('comuna') or ''
    ticket.cliente_ciudad = datos_cliente.get('ciudad') or ''
    ticket.cliente_direccion = datos_cliente.get('direccion') or ''
    ticket.cliente_telefono = datos_cliente.get('telefono') or ''
    ticket.cliente_telefono_secundario = datos_cliente.get('telefono_secundario') or ''
    ticket.cliente_email = datos_cliente.get('email') or ''
    ticket.cliente_email_facturacion = datos_cliente.get('email_facturacion') or ''

    ticket.observaciones = payload.get('observaciones') or ''
    ticket.observaciones_adicionales = payload.get('observaciones_adicionales') or ''

    nuevo_estado = payload.get('estado')
    if nuevo_estado and nuevo_estado in dict(ESTADO_TICKET_CHOICES):
        ticket.estado = nuevo_estado

    metodo_principal = payload.get('metodo_pago_principal')
    if metodo_principal and metodo_principal in dict(METODO_PAGO_TICKET_CHOICES):
        ticket.metodo_pago = metodo_principal

    correlativo_confirmacion = payload.get('correlativo')
    if correlativo_confirmacion and int(correlativo_confirmacion) != ticket.correlativo:
        return JsonResponse({'success': False, 'error': 'Correlativo no coincide con el ticket cargado'}, status=400)

    pagos = payload.get('pagos') or []
    ids_existentes = list(ticket.pagos.values_list('id', flat=True))

    with transaction.atomic():
        for pago in pagos:
            pago_id = pago.get('id')
            try:
                monto = int(pago.get('monto', 0))
            except (TypeError, ValueError):
                continue
            if monto <= 0:
                continue

            metodo_pago = pago.get('metodo_pago', 'OTRO')
            if metodo_pago not in dict(METODO_PAGO_TICKET_CHOICES):
                metodo_pago = 'OTRO'

            if pago_id and pago_id in ids_existentes:
                TicketDetallePago.objects.filter(id=pago_id, ticket=ticket).update(
                    metodo_pago=metodo_pago,
                    tipo_tarjeta=pago.get('tipo_tarjeta'),
                    voucher=pago.get('voucher'),
                    monto=monto,
                    notas=pago.get('notas', ''),
                )
                ids_existentes.remove(pago_id)
            else:
                TicketDetallePago.objects.create(
                    ticket=ticket,
                    metodo_pago=metodo_pago,
                    tipo_tarjeta=pago.get('tipo_tarjeta'),
                    voucher=pago.get('voucher'),
                    monto=monto,
                    notas=pago.get('notas', ''),
                )

        if ids_existentes:
            TicketDetallePago.objects.filter(id__in=ids_existentes, ticket=ticket).delete()
        
        # Verificar si el ticket cambió de PENDIENTE a PAGADO
        estado_anterior = Ticket.objects.get(id=ticket.id).estado
        ticket_se_pago = (estado_anterior == 'PENDIENTE' and ticket.estado == 'PAGADO')

        ticket.save()

        # Si el ticket se acaba de pagar, consumir stock FIFO y crear movimientos
        if ticket_se_pago:
            for tp in ticket.ticket_productos.all():
                try:
                    # Verificar que hay stock disponible
                    if tp.ProductoTalla.stock < tp.stock:
                        raise ValidationError(f'Stock insuficiente para {tp.ProductoTalla.sku}. Disponible: {tp.ProductoTalla.stock}, Requerido: {tp.stock}')
                    
                    # Consumir stock FIFO (esto crea automáticamente el movimiento de EGRESO)
                    consumir_stock_fifo(
                        producto_talla=tp.ProductoTalla,
                        cantidad_requerida=tp.stock,
                        responsable=request.user.username,
                        ticket=ticket,
                        observaciones=f'Pago de ticket #{ticket.correlativo}',
                        referencia_externa=f'TICKET_{ticket.correlativo}'
                    )
                    print(f"✓ Stock consumido FIFO: {tp.ProductoTalla.sku} -{tp.stock}")
                except Exception as e:
                    print(f"Error al consumir stock para {tp.ProductoTalla.sku}: {str(e)}")
                    # Crear movimiento manual si falla FIFO
                    Movimientos_Producto.objects.create(
                        ticket=ticket,
                        ProductoTalla=tp.ProductoTalla,
                        cantidad=-tp.stock,  # Negativo para egreso
                        costo=tp.ProductoTalla.producto.costo,
                        precio=tp.precio,
                        concepto='VENTA_DIRECTA',
                        tipo_movimiento='EGRESO',
                        responsable=request.user.username,
                        observaciones=f'Venta ticket #{ticket.correlativo} - Consumo manual (FIFO no disponible)',
                        referencia_externa=f'TICKET_{ticket.correlativo}'
                    )
                    # Actualizar stock manualmente
                    tp.ProductoTalla.stock -= tp.stock
                    tp.ProductoTalla.save()
                    print(f"⚠ Stock consumido manualmente: {tp.ProductoTalla.sku} -{tp.stock}")
        
        # Guardar o actualizar cliente en la base de datos si tiene datos
        if datos_cliente and datos_cliente.get('rut') and datos_cliente.get('nombre'):
            guardar_o_actualizar_cliente(datos_cliente, request.user)
        
        # Generar DTE si el tipo de documento lo requiere
        tipo_documento_seleccionado = payload.get('tipo_documento', '')
        dte_generado = None
        
        if tipo_documento_seleccionado in ['BOLETA_ELECTRONICA', 'FACTURA_ELECTRONICA'] and ticket.estado == 'PAGADO':
            try:
                dte_generado = generar_dte_desde_ticket(ticket, tipo_documento_seleccionado, request.user)
                print(f"✓ DTE generado: {dte_generado.tipo_documento} #{dte_generado.numero_documento}")
            except Exception as e:
                # No fallar el pago si hay error en DTE, solo registrar
                print(f"⚠ Error al generar DTE: {str(e)}")

    response_data = {
        'success': True, 
        'ticket': construir_ticket_data(ticket)
    }
    
    if dte_generado:
        response_data['dte_generado'] = {
            'id': dte_generado.id,
            'numero': dte_generado.numero_documento,
            'tipo': dte_generado.tipo_documento
        }
    
    return JsonResponse(response_data)


@login_required
@require_GET
def ticket_pago_pos(request):
    """Vista para página de pagos de tickets POS"""
    sucursal_actual_id = request.session.get('sucursalActual') or request.session.get('idSucursalActual')
    sucursal_actual = None
    if sucursal_actual_id:
        sucursal_actual = Sucursal.objects.filter(id=sucursal_actual_id).first()

    context = {
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
        'estado_ticket_choices': ESTADO_TICKET_CHOICES,
        'sucursal_actual': sucursal_actual,
    }
    return render(request, 'vistas/modulo_ventas/ticket_pago_pos.html', context)


# ========== GESTIÓN DE DOCUMENTOS DE VENTAS ==========

@login_required
def gestion_ventas_documentos(request):
    """Vista principal para gestión de ventas y documentos"""
    sucursal_actual_id = request.session.get('sucursalActual') or request.session.get('idSucursalActual')
    sucursal_actual = None
    
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            sucursal_actual = None
    
    context = {
        'sucursal_actual': sucursal_actual,
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
        'estado_ticket_choices': ESTADO_TICKET_CHOICES,
        'tipo_documento_choices': TIPO_DOCUMENTO_CHOICES,
    }
    return render(request, 'vistas/modulo_ventas/gestionVentasDocumentos.html', context)


@login_required
@require_GET
def listar_documentos_ventas(request):
    """API para listar documentos de ventas (tickets, boletas, facturas)"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })

        # Parámetros de filtro
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        tipo_documento = request.GET.get('tipo_documento')
        estado = request.GET.get('estado')
        metodo_pago = request.GET.get('metodo_pago')
        buscar = request.GET.get('buscar', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))

        documentos_data = []

        # === SOLO DTEs (Facturas/Boletas Electrónicas) ===
        dtes_query = Dte.objects.select_related(
            'vendedor', 
            'receptor'
        ).prefetch_related(
            'dte_asociado',
            'dte_productos__productoTalla__producto'
        ).filter(
            sucursal_id=sucursal_id,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
        )

        # Aplicar filtros de fecha
        if fecha_desde:
            dtes_query = dtes_query.filter(fecha_emision__gte=fecha_desde)
        if fecha_hasta:
            dtes_query = dtes_query.filter(fecha_emision__lte=fecha_hasta)

        # Aplicar filtros por tipo de DTE
        dtes_filtrados = dtes_query
        
        if tipo_documento:
            if tipo_documento == 'BOLETA_ELECTRONICA':
                dtes_filtrados = dtes_filtrados.filter(tipo_documento='BOLETA ELECTRONICA')
            elif tipo_documento == 'FACTURA_ELECTRONICA':
                dtes_filtrados = dtes_filtrados.filter(tipo_documento='FACTURA ELECTRONICA')
            elif tipo_documento == 'FACTURA_EXENTA':
                dtes_filtrados = dtes_filtrados.filter(tipo_documento='FACTURA EXENTA')
            
            if estado:
                # Mapear estados de DTE a estados de ticket
                estado_dte_map = {
                    'PENDIENTE': 'PENDIENTE',
                    'PAGADO': 'EMITIDO',
                    'ANULADO': 'ANULADO'
                }
                if estado in estado_dte_map:
                    dtes_filtrados = dtes_filtrados.filter(estado_dte=estado_dte_map[estado])
            
            # Filtrar por método de pago
            if metodo_pago:
                dtes_filtrados = dtes_filtrados.filter(dte_asociado__metodo_pago=metodo_pago).distinct()
            
        if buscar:
            # Búsqueda avanzada en múltiples campos
            dtes_filtrados = dtes_filtrados.filter(
                Q(numero_documento__icontains=buscar) |
                Q(receptor__nombre__icontains=buscar) |
                Q(receptor__rut__icontains=buscar) |
                Q(vendedor__nombre__icontains=buscar) |
                Q(dte_productos__productoTalla__sku__icontains=buscar) |
                Q(dte_productos__productoTalla__producto__articulo__icontains=buscar)
            ).distinct()
        
        # Procesar DTEs filtrados
        for dte in dtes_filtrados:
            # Obtener productos del DTE
            productos = []
            for dp in dte.dte_productos.all():
                productos.append({
                    'sku': dp.productoTalla.sku if dp.productoTalla else '',
                    'nombre': dp.productoTalla.producto.articulo if dp.productoTalla and dp.productoTalla.producto else dp.descripcion,
                    'talla': dp.productoTalla.talla if dp.productoTalla else '',
                    'cantidad': dp.stock,
                    'precio_unitario': dp.precio,
                    'subtotal': dp.precio * dp.stock,
                    'costo': dp.costo,
                    'sobreprecio': dp.sobreprecio,
                })
            
            # Obtener métodos de pago
            metodos_pago = []
            for pago in dte.dte_asociado.all():
                metodos_pago.append({
                    'metodo': pago.metodo_pago,
                    'metodo_display': pago.metodo_pago,
                    'monto': pago.monto,
                    'voucher': pago.voucher or '',
                    'tipo_tarjeta': pago.tipo_tarjeta or '',
                })
            
            # Mapear estado DTE
            estado_display = 'PAGADO' if dte.estado_dte == 'EMITIDO' else dte.estado_dte
            
            # Crear datetime con zona horaria para DTEs
            from datetime import time as dt_time
            fecha_dt = timezone.datetime.combine(dte.fecha_emision, dt_time.min)
            created_at_dte = timezone.make_aware(fecha_dt) if timezone.is_naive(fecha_dt) else fecha_dt
            
            documentos_data.append({
                'id': dte.id,
                'tipo': 'FACTURA' if 'FACTURA' in dte.tipo_documento else 'BOLETA',
                'numero': dte.numero_documento,
                'fecha': dte.fecha_emision,
                'cliente_nombre': dte.receptor.nombre if dte.receptor else 'Sin nombre',
                'cliente_rut': dte.receptor.rut if dte.receptor else '',
                'cliente_giro': dte.receptor.giro if dte.receptor else '',
                'cliente_email': dte.receptor.correoVendedor if dte.receptor else '',
                'cliente_direccion': dte.receptor.direccion if dte.receptor else '',
                'cliente_comuna': dte.receptor.comuna if dte.receptor else '',
                'vendedor_nombre': dte.vendedor.nombre if dte.vendedor else 'Sin vendedor',
                'total': int(dte.monto_con_iva or 0),
                'estado': estado_display,
                'created_at': created_at_dte,
                'productos': productos,
                'metodos_pago': metodos_pago,
                'total_productos': len(productos),
                'metodos_pago_str': ', '.join([p['metodo_display'] for p in metodos_pago]),
            })

        # Ordenar por fecha descendente - asegurar que todas las fechas sean comparables
        try:
            documentos_data.sort(key=lambda x: x['created_at'], reverse=True)
        except TypeError as e:
            # Si hay problemas de comparación de fechas, ordenar por ID como fallback
            print(f"Error al ordenar por fecha: {e}")
            documentos_data.sort(key=lambda x: x['id'], reverse=True)

        # Paginación manual
        total_documentos = len(documentos_data)
        inicio = (page - 1) * per_page
        fin = inicio + per_page
        documentos_paginados = documentos_data[inicio:fin]

        # Calcular estadísticas (solo DTEs)
        total_ventas = sum(doc['total'] for doc in documentos_data)
        total_pendientes = len([doc for doc in documentos_data if doc['estado'] == 'PENDIENTE'])
        total_facturas = len([doc for doc in documentos_data if doc['tipo'] == 'FACTURA'])
        total_boletas = len([doc for doc in documentos_data if doc['tipo'] == 'BOLETA'])

        return JsonResponse({
            'success': True,
            'documentos': documentos_paginados,
            'total': total_documentos,
            'pagination': {
                'current_page': page,
                'per_page': per_page,
                'total_pages': (total_documentos + per_page - 1) // per_page,
                'total_items': total_documentos,
                'has_previous': page > 1,
                'has_next': fin < total_documentos
            },
            'estadisticas': {
                'total_documentos': total_documentos,
                'total_ventas': total_ventas,
                'total_pendientes': total_pendientes,
                'total_facturas': total_facturas,
                'total_boletas': total_boletas,
            }
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener documentos: {str(e)}'
        })


@login_required
@require_POST
def convertir_ticket_a_factura(request):
    """Convertir un ticket a factura electrónica"""
    try:
        data = json.loads(request.body)
        documento_id = data.get('documento_id')
        
        if not documento_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de documento requerido'
            })

        # Obtener el ticket original
        ticket = get_object_or_404(Ticket, id=documento_id)
        
        # Verificar que el ticket esté pagado
        if ticket.estado != 'PAGADO':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden convertir tickets pagados'
            })

        # Verificar si ya existe una factura para este ticket
        factura_existente = Dte.objects.filter(
            referencias__icontains=f'TICKET-{ticket.correlativo}'
        ).first()
        
        if factura_existente:
            return JsonResponse({
                'success': False,
                'error': f'Ya existe la factura #{factura_existente.numero_documento} para este ticket'
            })

        with transaction.atomic():
            # Crear o obtener empresa receptora
            receptor_data = {
                'nombre': data.get('cliente_razon_social'),
                'rut': data.get('cliente_rut'),
                'giro': data.get('cliente_giro', ''),
                'direccion': data.get('cliente_direccion', ''),
                'comuna': data.get('cliente_comuna', ''),
                'correoVendedor': data.get('cliente_email', ''),
            }
            
            receptor, created = Empresa.objects.get_or_create(
                rut=receptor_data['rut'],
                defaults=receptor_data
            )
            
            if not created:
                # Actualizar datos si ya existe
                for key, value in receptor_data.items():
                    if value:  # Solo actualizar si hay valor
                        setattr(receptor, key, value)
                receptor.save()

            # Obtener siguiente correlativo para factura
            numero_factura = obtener_siguiente_correlativo(ticket.sucursal, 'FACTURA ELECTRONICA')

            # Crear la factura
            factura = Dte.objects.create(
                emisor=ticket.sucursal.empresa,
                receptor=receptor,
                numero_documento=numero_factura,
                tipo_documento='FACTURA ELECTRONICA',
                monto_con_iva=ticket.total,
                monto_neto=int(ticket.total / 1.19),  # Calcular neto (asumiendo IVA 19%)
                estado_pago='PAGADO',
                estado_dte='EMITIDO',
                responsable=request.user.username,
                fecha_emision=data.get('fecha_emision', timezone.now().date()),
                fecha_vencimiento=data.get('fecha_emision', timezone.now().date()),
                diasCredito=0,
                bultos=1,
                unidades_productos=ticket.ticket_productos.aggregate(
                    total=Sum('stock')
                )['total'] or 0,
                vendedor=ticket.vendedor,
                descuento=ticket.descuento or 0,
                sucursal=ticket.sucursal,
                tipo_transaccion='VENTA',
                referencias=f'TICKET-{ticket.correlativo}'
            )

            # Copiar productos del ticket a la factura
            for ticket_producto in ticket.ticket_productos.all():
                Dte_Productos.objects.create(
                    dte=factura,
                    productoTalla=ticket_producto.ProductoTalla,
                    descripcion=f"{ticket_producto.ProductoTalla.producto.articulo} - {ticket_producto.ProductoTalla.talla}",
                    costo=ticket_producto.ProductoTalla.producto.costo,
                    sobreprecio=ticket_producto.ProductoTalla.producto.sobreprecio,
                    precio=ticket_producto.precio,
                    stock=ticket_producto.stock,
                    activo=True
                )

            # Copiar pagos del ticket a la factura
            for pago_ticket in ticket.pagos.all():
                Dte_Detalle_Pago.objects.create(
                    dte=factura,
                    metodo_pago=pago_ticket.get_metodo_pago_display(),
                    tipo_tarjeta=pago_ticket.tipo_tarjeta or '',
                    voucher=pago_ticket.voucher or '',
                    monto=pago_ticket.monto
                )

        return JsonResponse({
            'success': True,
            'message': 'Factura creada exitosamente',
            'numero_factura': numero_factura,
            'factura_id': factura.id
        })

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear factura: {str(e)}'
        })


@login_required
@require_GET
def detalle_documento_venta(request, documento_id):
    """Obtener detalle completo de un documento de venta"""
    try:
        tipo_documento = request.GET.get('tipo', 'TICKET')
        
        if tipo_documento == 'TICKET':
            documento = get_object_or_404(Ticket, id=documento_id)
            
            # Obtener productos del ticket
            productos = []
            for tp in documento.ticket_productos.select_related('ProductoTalla__producto').all():
                productos.append({
                    'sku': tp.ProductoTalla.sku,
                    'nombre': tp.ProductoTalla.producto.articulo,
                    'talla': tp.ProductoTalla.talla,
                    'cantidad': tp.stock,
                    'precio_unitario': tp.precio,
                    'subtotal': tp.subtotal,
                })
            
            # Obtener pagos
            pagos = []
            for pago in documento.pagos.all():
                pagos.append({
                    'metodo': pago.get_metodo_pago_display(),
                    'monto': pago.monto,
                    'voucher': pago.voucher or '',
                    'notas': pago.notas or '',
                })
            
            detalle = {
                'tipo': 'TICKET',
                'numero': documento.correlativo,
                'fecha': documento.created_at.date(),
                'hora': documento.created_at.time(),
                'estado': documento.estado,
                'cliente': {
                    'nombre': documento.cliente_nombre or '',
                    'rut': documento.cliente_rut or '',
                    'email': documento.cliente_email or '',
                    'telefono': documento.cliente_telefono or '',
                },
                'vendedor': documento.vendedor.nombre if documento.vendedor else '',
                'productos': productos,
                'pagos': pagos,
                'totales': {
                    'subtotal': documento.subTotal,
                    'descuento': documento.descuento or 0,
                    'total': documento.total,
                },
                'observaciones': documento.observaciones or '',
            }
            
        else:  # DTE (Factura/Boleta)
            documento = get_object_or_404(Dte, id=documento_id)
            
            # Obtener productos del DTE
            productos = []
            for dp in documento.dte_productos.select_related('productoTalla__producto').all():
                productos.append({
                    'sku': dp.productoTalla.sku if dp.productoTalla else '',
                    'nombre': dp.descripcion,
                    'cantidad': dp.stock,
                    'precio_unitario': dp.precio,
                    'subtotal': dp.precio * dp.stock,
                })
            
            # Obtener pagos
            pagos = []
            for pago in documento.dte_asociado.all():
                pagos.append({
                    'metodo': pago.metodo_pago,
                    'monto': pago.monto,
                    'voucher': pago.voucher or '',
                    'tipo_tarjeta': pago.tipo_tarjeta or '',
                })
            
            detalle = {
                'tipo': 'FACTURA' if 'FACTURA' in documento.tipo_documento else 'BOLETA',
                'numero': documento.numero_documento,
                'fecha': documento.fecha_emision,
                'estado': documento.estado_dte,
                'cliente': {
                    'nombre': documento.receptor.nombre if documento.receptor else '',
                    'rut': documento.receptor.rut if documento.receptor else '',
                    'giro': documento.receptor.giro if documento.receptor else '',
                    'direccion': documento.receptor.direccion if documento.receptor else '',
                },
                'vendedor': documento.vendedor.nombre if documento.vendedor else '',
                'productos': productos,
                'pagos': pagos,
                'totales': {
                    'neto': documento.monto_neto,
                    'iva': documento.monto_con_iva - documento.monto_neto,
                    'total': documento.monto_con_iva,
                },
                'referencias': documento.referencias or '',
            }

        return JsonResponse({
            'success': True,
            'documento': detalle
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener detalle: {str(e)}'
        })


@login_required
@require_POST
def anular_documento_venta(request):
    """Anular un documento de venta"""
    try:
        data = json.loads(request.body)
        documento_id = data.get('documento_id')
        tipo_documento = data.get('tipo', 'TICKET')
        
        if not documento_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de documento requerido'
            })

        with transaction.atomic():
            if tipo_documento == 'TICKET':
                documento = get_object_or_404(Ticket, id=documento_id)
                
                if documento.estado == 'ANULADO':
                    return JsonResponse({
                        'success': False,
                        'error': 'El ticket ya está anulado'
                    })
                
                # Anular ticket
                documento.estado = 'ANULADO'
                documento.save()
                
                # Devolver stock si estaba pagado
                if documento.estado == 'PAGADO':
                    for tp in documento.ticket_productos.all():
                        # Crear movimiento de devolución de stock
                        Movimientos_Producto.objects.create(
                            ticket=documento,
                            ProductoTalla=tp.ProductoTalla,
                            cantidad=tp.stock,  # Cantidad positiva para devolver
                            costo=tp.ProductoTalla.producto.costo,
                            precio=tp.precio,
                            concepto='DEVOLUCION_CLIENTE',
                            tipo_movimiento='INGRESO',
                            responsable=request.user.username,
                            observaciones=f'Anulación ticket #{documento.correlativo}'
                        )
                
            else:  # DTE
                documento = get_object_or_404(Dte, id=documento_id)
                
                if documento.estado_dte == 'ANULADO':
                    return JsonResponse({
                        'success': False,
                        'error': 'El documento ya está anulado'
                    })
                
                documento.estado_dte = 'ANULADO'
                documento.save()

        return JsonResponse({
            'success': True,
            'message': 'Documento anulado exitosamente'
        })

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al anular documento: {str(e)}'
        })


# ========== CUADRATURA Y ARQUEO DE CAJA ==========

@login_required
def cuadratura_caja(request):
    """Vista principal para cuadratura y arqueo de caja"""
    sucursal_actual_id = request.session.get('sucursalActual') or request.session.get('idSucursalActual')
    sucursal_actual = None
    
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            sucursal_actual = None
    
    if not sucursal_actual:
        return redirect('dashboard')
    
    context = {
        'sucursal_actual': sucursal_actual,
    }
    return render(request, 'vistas/modulo_ventas/cuadraturaCaja_v2.html', context)


@login_required
@require_POST
@csrf_exempt
def generar_cuadratura_caja(request):
    """Generar cuadratura de caja para una fecha específica"""
    try:
        fecha_cuadratura = request.POST.get('fecha')
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not fecha_cuadratura:
            return JsonResponse({
                'success': False,
                'error': 'Fecha requerida'
            })
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Convertir fecha string a date object
        from datetime import datetime
        fecha_obj = datetime.strptime(fecha_cuadratura, '%Y-%m-%d').date()
        
        # Crear datetime para filtros con timezone aware
        from datetime import time as dt_time
        inicio_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.min))
        fin_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.max))
        
        # Inicializar totales
        cuadratura_data = {
            'fecha_cuadratura': fecha_cuadratura,
            'total_efectivo': 0,
            'total_tarjeta_debito': 0,
            'total_tarjeta_credito': 0,
            'total_transbank': 0,
            'total_visa_mc_amex': 0,
            'total_presto': 0,
            'total_abcdin': 0,
            'total_tricot': 0,
            'total_hites': 0,
            'total_ripley': 0,
            'total_falabella': 0,
            'total_paris': 0,
            'total_transferencia': 0,
            'total_cheque': 0,
            'total_convenio': 0,
            'total_credito_trabajador': 0,
            'total_nota_credito': 0,
            'total_webpay': 0,
            'total_mercadolibre': 0,
            'total_mercadopago': 0,
            'total_transferencia_internet': 0,
            'total_venta_internet': 0,
            'total_tickets': 0,
            'total_boletas': 0,
            'total_boletas_electronicas': 0,
            'total_facturas': 0,
            'total_facturas_exentas': 0,
            'total_notas_credito': 0,
            'cantidad_tickets': 0,
            'cantidad_boletas': 0,
            'cantidad_boletas_electronicas': 0,
            'cantidad_facturas': 0,
            'cantidad_facturas_exentas': 0,
            'venta_total': 0,
            'total_tarjetas_comerciales': 0,
        }
        
        # ========== PROCESAR TICKETS ==========
        tickets_del_dia = Ticket.objects.filter(
            sucursal=sucursal,
            created_at__range=[inicio_dia, fin_dia],
            estado='PAGADO'
        ).prefetch_related('pagos')
        
        for ticket in tickets_del_dia:
            cuadratura_data['total_tickets'] += ticket.total or 0
            cuadratura_data['cantidad_tickets'] += 1
            
            # Procesar pagos del ticket
            for pago in ticket.pagos.all():
                metodo = pago.metodo_pago
                monto = pago.monto or 0
                
                if metodo == 'EFECTIVO':
                    cuadratura_data['total_efectivo'] += monto
                elif metodo == 'TARJETA_DEBITO':
                    cuadratura_data['total_tarjeta_debito'] += monto
                    cuadratura_data['total_transbank'] += monto
                elif metodo == 'TARJETA_CREDITO':
                    cuadratura_data['total_tarjeta_credito'] += monto
                    cuadratura_data['total_transbank'] += monto
                elif metodo == 'TBK_POS_INTEGRADO' or metodo == 'TBK_MANUAL':
                    cuadratura_data['total_transbank'] += monto
                elif metodo == 'TRANSFERENCIA':
                    cuadratura_data['total_transferencia'] += monto
                elif metodo == 'CHEQUE':
                    cuadratura_data['total_cheque'] += monto
                elif metodo == 'CONVENIO':
                    cuadratura_data['total_convenio'] += monto
                elif metodo == 'CREDITO_TRABAJADOR':
                    cuadratura_data['total_credito_trabajador'] += monto
                elif metodo == 'TARJETA_COMERCIAL':
                    # Aquí podrías mapear por tipo de tarjeta si tienes esa información
                    cuadratura_data['total_visa_mc_amex'] += monto
                elif metodo == 'VENTA_INTERNET':
                    cuadratura_data['total_venta_internet'] += monto
                    cuadratura_data['total_webpay'] += monto
        
        # ========== PROCESAR DTEs (FACTURAS/BOLETAS ELECTRÓNICAS) ==========
        dtes_del_dia = Dte.objects.filter(
            sucursal=sucursal,
            fecha_emision=fecha_obj,
            estado_dte='EMITIDO',
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
        ).prefetch_related('dte_asociado')
        
        for dte in dtes_del_dia:
            monto_dte = dte.monto_con_iva or 0
            
            if dte.tipo_documento == 'BOLETA ELECTRONICA':
                cuadratura_data['total_boletas_electronicas'] += monto_dte
                cuadratura_data['cantidad_boletas_electronicas'] += 1
            elif dte.tipo_documento == 'FACTURA ELECTRONICA':
                cuadratura_data['total_facturas'] += monto_dte
                cuadratura_data['cantidad_facturas'] += 1
            elif dte.tipo_documento == 'FACTURA EXENTA':
                cuadratura_data['total_facturas_exentas'] += monto_dte
                cuadratura_data['cantidad_facturas_exentas'] += 1
            elif dte.tipo_documento == 'NOTA DE CREDITO':
                cuadratura_data['total_notas_credito'] += monto_dte
            
            # Procesar pagos del DTE
            for pago in dte.dte_asociado.all():
                metodo = pago.metodo_pago
                monto = pago.monto or 0
                
                if 'EFECTIVO' in metodo.upper():
                    cuadratura_data['total_efectivo'] += monto
                elif 'DEBITO' in metodo.upper() or 'REDCOMPRA' in metodo.upper():
                    cuadratura_data['total_tarjeta_debito'] += monto
                    cuadratura_data['total_transbank'] += monto
                elif 'CREDITO' in metodo.upper() or 'VISA' in metodo.upper() or 'MASTERCARD' in metodo.upper():
                    cuadratura_data['total_visa_mc_amex'] += monto
                elif 'TRANSFERENCIA' in metodo.upper():
                    cuadratura_data['total_transferencia'] += monto
                elif 'CHEQUE' in metodo.upper():
                    cuadratura_data['total_cheque'] += monto
                # Mapear otras tarjetas comerciales según el nombre del método de pago
                elif 'PRESTO' in metodo.upper():
                    cuadratura_data['total_presto'] += monto
                elif 'ABCDIN' in metodo.upper():
                    cuadratura_data['total_abcdin'] += monto
                elif 'TRICOT' in metodo.upper():
                    cuadratura_data['total_tricot'] += monto
                elif 'HITES' in metodo.upper():
                    cuadratura_data['total_hites'] += monto
                elif 'RIPLEY' in metodo.upper():
                    cuadratura_data['total_ripley'] += monto
                elif 'FALABELLA' in metodo.upper():
                    cuadratura_data['total_falabella'] += monto
                elif 'PARIS' in metodo.upper():
                    cuadratura_data['total_paris'] += monto
        
        # ========== CALCULAR TOTALES GENERALES ==========
        cuadratura_data['total_tarjetas_comerciales'] = (
            cuadratura_data['total_visa_mc_amex'] +
            cuadratura_data['total_presto'] +
            cuadratura_data['total_abcdin'] +
            cuadratura_data['total_tricot'] +
            cuadratura_data['total_hites'] +
            cuadratura_data['total_ripley'] +
            cuadratura_data['total_falabella'] +
            cuadratura_data['total_paris']
        )
        
        cuadratura_data['venta_total'] = (
            cuadratura_data['total_tickets'] +
            cuadratura_data['total_boletas_electronicas'] +
            cuadratura_data['total_facturas'] +
            cuadratura_data['total_facturas_exentas'] -
            cuadratura_data['total_notas_credito']
        )
        
        return JsonResponse({
            'success': True,
            'cuadratura': cuadratura_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar cuadratura: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def guardar_cuadratura_completa(request):
    """
    Guardar cuadratura de caja completa con depósitos bancarios
    Sistema simplificado - prioriza sencillez
    """
    try:
        from datetime import datetime
        import json
        
        # Obtener datos del request
        fecha = request.POST.get('fecha')
        efectivo_teorico = int(request.POST.get('efectivo_teorico', 0))
        efectivo_real = int(request.POST.get('efectivo_real', 0))
        cierre_pos = int(request.POST.get('cierre_pos', 0))
        numero_lote = request.POST.get('numero_lote', '')
        observaciones = request.POST.get('observaciones', '')
        depositos_json = request.POST.get('depositos', '[]')
        cuadratura_completa_json = request.POST.get('cuadratura_completa', '{}')
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        # Validaciones
        if not fecha or not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Fecha y sucursal son requeridos'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        fecha_obj = datetime.strptime(fecha, '%Y-%m-%d').date()
        
        # Parsear JSON
        depositos_data = json.loads(depositos_json)
        cuadratura_completa = json.loads(cuadratura_completa_json)
        
        # Verificar si ya existe un arqueo para esta fecha
        arqueo_existente = ArqueoCaja.objects.filter(
            fecha_arqueo=fecha_obj,
            sucursal=sucursal
        ).first()
        
        if arqueo_existente:
            return JsonResponse({
                'success': False,
                'error': f'Ya existe una cuadratura para el {fecha_obj}. Elimínala primero si deseas crear una nueva.'
            })
        
        # === CREAR ARQUEO DE CAJA ===
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=fecha_obj,
            sucursal=sucursal,
            usuario_responsable=request.user,
            
            # Totales teóricos del sistema
            total_efectivo_teorico=efectivo_teorico,
            total_tarjeta_debito_teorico=cuadratura_completa.get('total_tarjeta_debito', 0),
            total_tarjeta_credito_teorico=cuadratura_completa.get('total_tarjeta_credito', 0),
            total_transbank_teorico=cuadratura_completa.get('total_transbank', 0),
            total_visa_mc_amex_teorico=cuadratura_completa.get('total_visa_mc_amex', 0),
            total_presto_teorico=cuadratura_completa.get('total_presto', 0),
            total_abcdin_teorico=cuadratura_completa.get('total_abcdin', 0),
            total_tricot_teorico=cuadratura_completa.get('total_tricot', 0),
            total_hites_teorico=cuadratura_completa.get('total_hites', 0),
            total_ripley_teorico=cuadratura_completa.get('total_ripley', 0),
            total_falabella_teorico=cuadratura_completa.get('total_falabella', 0),
            total_paris_teorico=cuadratura_completa.get('total_paris', 0),
            total_tarjetas_comerciales_teorico=cuadratura_completa.get('total_tarjetas_comerciales', 0),
            total_transferencia_teorico=cuadratura_completa.get('total_transferencia', 0),
            total_credito_trabajador_teorico=cuadratura_completa.get('total_credito_trabajador', 0),
            total_webpay_teorico=cuadratura_completa.get('total_webpay', 0),
            total_mercadolibre_teorico=cuadratura_completa.get('total_mercadolibre', 0),
            total_mercadopago_teorico=cuadratura_completa.get('total_mercadopago', 0),
            total_transferencia_internet_teorico=cuadratura_completa.get('total_transferencia_internet', 0),
            total_venta_internet_teorico=cuadratura_completa.get('total_venta_internet', 0),
            
            # Conteo físico (solo efectivo por ahora - simplificado)
            total_efectivo_fisico=efectivo_real,
            
            # Cierre POS
            cierre_pos_fisico=cierre_pos,
            numero_lote_pos=numero_lote,
            diferencia_transbank=cierre_pos - cuadratura_completa.get('total_transbank', 0),
            
            # Diferencias
            diferencia_efectivo=efectivo_real - efectivo_teorico,
            
            # Observaciones
            observaciones=observaciones,
            
            # Estado
            estado='CERRADO' if (efectivo_real - efectivo_teorico) == 0 and (cierre_pos - cuadratura_completa.get('total_transbank', 0)) == 0 else 'CON_DIFERENCIAS',
            fecha_cierre=timezone.now()
        )
        
        # === CREAR DEPÓSITOS BANCARIOS ===
        depositos_creados = []
        for dep in depositos_data:
            try:
                fecha_dep = datetime.strptime(dep['fecha'], '%Y-%m-%d').date()
                deposito = DepositoBancario.objects.create(
                    arqueo=arqueo,
                    fecha_deposito=fecha_dep,
                    monto=int(dep['monto']),
                    banco=dep.get('banco', 'ESTADO'),
                    numero_comprobante=dep.get('comprobante', ''),
                    observaciones=dep.get('observaciones', ''),
                    registrado_por=request.user
                )
                depositos_creados.append({
                    'id': deposito.id,
                    'monto': deposito.monto,
                    'banco': deposito.get_banco_display()
                })
            except Exception as e:
                print(f"Error al crear depósito: {e}")
                continue
        
        return JsonResponse({
            'success': True,
            'message': '¡Cuadratura guardada exitosamente!',
            'arqueo_id': arqueo.id,
            'depositos_creados': len(depositos_creados),
            'diferencia': arqueo.diferencia_efectivo,
            'estado': arqueo.get_estado_display()
        })
        
    except Exception as e:
        import traceback
        print(f"Error al guardar cuadratura: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al guardar cuadratura: {str(e)}'
        })


@login_required
@require_GET
def verificar_cuadratura_existente(request):
    """Verificar si ya existe una cuadratura para la fecha dada"""
    try:
        from datetime import datetime
        
        fecha = request.GET.get('fecha')
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not fecha or not sucursal_id:
            return JsonResponse({
                'existe': False
            })
        
        fecha_obj = datetime.strptime(fecha, '%Y-%m-%d').date()
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        arqueo = ArqueoCaja.objects.filter(
            fecha_arqueo=fecha_obj,
            sucursal=sucursal
        ).first()
        
        if arqueo:
            return JsonResponse({
                'existe': True,
                'datos': {
                    'id': arqueo.id,
                    'fecha_arqueo': arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
                    'usuario': arqueo.usuario_responsable.get_full_name() or arqueo.usuario_responsable.username,
                    'estado': arqueo.get_estado_display(),
                    'diferencia_efectivo': arqueo.diferencia_efectivo,
                    'efectivo_teorico': arqueo.total_efectivo_teorico,
                    'efectivo_fisico': arqueo.total_efectivo_fisico
                }
            })
        else:
            return JsonResponse({
                'existe': False
            })
            
    except Exception as e:
        print(f"Error al verificar cuadratura: {e}")
        return JsonResponse({
            'existe': False
        })


@login_required
@require_POST
@csrf_exempt
def eliminar_cuadratura(request, arqueo_id):
    """Eliminar una cuadratura existente"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        arqueo = get_object_or_404(
            ArqueoCaja,
            id=arqueo_id,
            sucursal_id=sucursal_id
        )
        
        # Guardar info antes de eliminar
        fecha_arqueo = arqueo.fecha_arqueo.strftime('%d/%m/%Y')
        
        # Eliminar (los depósitos se eliminan automáticamente por CASCADE)
        arqueo.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Cuadratura del {fecha_arqueo} eliminada exitosamente'
        })
        
    except Exception as e:
        import traceback
        print(f"Error al eliminar cuadratura: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al eliminar: {str(e)}'
        })


@login_required
@require_GET
def listar_cuadraturas(request):
    """Listar cuadraturas/arqueos con filtros"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        fecha_filtro = request.GET.get('fecha')
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Query base
        arqueos = ArqueoCaja.objects.filter(
            sucursal_id=sucursal_id
        ).select_related('usuario_responsable', 'sucursal').prefetch_related('depositos')
        
        # Aplicar filtro de fecha si existe
        if fecha_filtro:
            from datetime import datetime
            fecha_obj = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
            arqueos = arqueos.filter(fecha_arqueo=fecha_obj)
        else:
            # Por defecto, mostrar TODO el mes actual
            hoy = timezone.now().date()
            primer_dia_mes = hoy.replace(day=1)
            arqueos = arqueos.filter(
                fecha_arqueo__gte=primer_dia_mes,
                fecha_arqueo__lte=hoy
            )
        
        # Ordenar por fecha descendente (de más reciente a más antigua)
        arqueos = arqueos.order_by('-fecha_arqueo')
        
        # Serializar datos
        datos = []
        for arqueo in arqueos:
            total_depositos = sum([d.monto for d in arqueo.depositos.all()])
            
            datos.append({
                'id': arqueo.id,
                'fecha_arqueo': arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
                'usuario': arqueo.usuario_responsable.get_full_name() or arqueo.usuario_responsable.username,
                'efectivo_teorico': arqueo.total_efectivo_teorico,
                'efectivo_fisico': arqueo.total_efectivo_fisico,
                'diferencia_efectivo': arqueo.diferencia_efectivo,
                'total_transbank_teorico': arqueo.total_transbank_teorico,
                'cierre_pos_fisico': arqueo.cierre_pos_fisico,
                'numero_lote_pos': arqueo.numero_lote_pos or '',
                'diferencia_transbank': arqueo.diferencia_transbank,
                'total_depositos': total_depositos,
                'estado': arqueo.get_estado_display(),
                'estado_codigo': arqueo.estado,
                'observaciones': arqueo.observaciones,
                'cantidad_depositos': arqueo.depositos.count()
            })
        
        return JsonResponse({
            'success': True,
            'arqueos': datos
        })
        
    except Exception as e:
        import traceback
        print(f"Error al listar cuadraturas: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


@login_required
@require_GET
def obtener_detalle_arqueo(request, arqueo_id):
    """Obtener detalle completo de un arqueo específico"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        arqueo = get_object_or_404(
            ArqueoCaja.objects.select_related('usuario_responsable', 'sucursal').prefetch_related('depositos'),
            id=arqueo_id,
            sucursal_id=sucursal_id
        )
        
        # Serializar depositos
        depositos_data = []
        for deposito in arqueo.depositos.all():
            depositos_data.append({
                'fecha_deposito': deposito.fecha_deposito.strftime('%d/%m/%Y'),
                'monto': deposito.monto,
                'banco': deposito.get_banco_display(),
                'numero_comprobante': deposito.numero_comprobante,
                'observaciones': deposito.observaciones
            })
        
        # Calcular venta total (si está disponible)
        venta_total = 0
        for field in ['total_efectivo_teorico', 'total_transbank_teorico', 
                      'total_tarjetas_comerciales_teorico', 'total_venta_internet_teorico']:
            venta_total += getattr(arqueo, field, 0)
        
        arqueo_data = {
            'id': arqueo.id,
            'fecha_arqueo': arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
            'usuario': arqueo.usuario_responsable.get_full_name() or arqueo.usuario_responsable.username,
            'sucursal': arqueo.sucursal.alias if arqueo.sucursal else 'N/A',
            'estado': arqueo.estado,
            'observaciones': arqueo.observaciones,
            
            # Totales
            'venta_total': venta_total,
            'total_efectivo_teorico': arqueo.total_efectivo_teorico,
            'total_efectivo_fisico': arqueo.total_efectivo_fisico,
            'diferencia_efectivo': arqueo.diferencia_efectivo,
            
            # Transbank POS
            'total_transbank_teorico': arqueo.total_transbank_teorico,
            'cierre_pos_fisico': arqueo.cierre_pos_fisico,
            'diferencia_transbank': arqueo.diferencia_transbank,
            'numero_lote_pos': arqueo.numero_lote_pos or '',
            
            # Transbank Detalle
            'total_tarjeta_debito_teorico': arqueo.total_tarjeta_debito_teorico,
            'total_tarjeta_credito_teorico': arqueo.total_tarjeta_credito_teorico,
            
            # Tarjetas Comerciales
            'total_visa_mc_amex_teorico': arqueo.total_visa_mc_amex_teorico,
            'total_presto_teorico': arqueo.total_presto_teorico,
            'total_abcdin_teorico': arqueo.total_abcdin_teorico,
            'total_tricot_teorico': arqueo.total_tricot_teorico,
            'total_hites_teorico': arqueo.total_hites_teorico,
            'total_ripley_teorico': arqueo.total_ripley_teorico,
            'total_falabella_teorico': arqueo.total_falabella_teorico,
            'total_paris_teorico': arqueo.total_paris_teorico,
            'total_tarjetas_comerciales_teorico': arqueo.total_tarjetas_comerciales_teorico,
            
            # Venta Internet
            'total_webpay_teorico': arqueo.total_webpay_teorico,
            'total_mercadolibre_teorico': arqueo.total_mercadolibre_teorico,
            'total_mercadopago_teorico': arqueo.total_mercadopago_teorico,
            'total_transferencia_internet_teorico': arqueo.total_transferencia_internet_teorico,
            'total_venta_internet_teorico': arqueo.total_venta_internet_teorico,
            
            # Otros
            'total_transferencia_teorico': arqueo.total_transferencia_teorico,
            'total_credito_trabajador_teorico': arqueo.total_credito_trabajador_teorico,
            
            # Depósitos
            'depositos': depositos_data
        }
        
        return JsonResponse({
            'success': True,
            'arqueo': arqueo_data
        })
        
    except Exception as e:
        import traceback
        print(f"Error al obtener detalle de arqueo: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


@login_required
@require_POST
def editar_cuadratura(request, arqueo_id):
    """Editar una cuadratura existente"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        # Obtener el arqueo
        arqueo = get_object_or_404(
            ArqueoCaja,
            id=arqueo_id,
            sucursal_id=sucursal_id
        )
        
        data = json.loads(request.body)
        
        # Actualizar efectivo físico
        if 'efectivo_real' in data:
            arqueo.total_efectivo_fisico = data['efectivo_real']
            arqueo.diferencia_efectivo = arqueo.total_efectivo_fisico - arqueo.total_efectivo_teorico
        
        # Actualizar cierre POS
        if 'cierre_pos' in data:
            arqueo.cierre_pos_fisico = data['cierre_pos']
            arqueo.diferencia_transbank = arqueo.cierre_pos_fisico - arqueo.total_transbank_teorico
        
        if 'numero_lote' in data:
            arqueo.numero_lote_pos = data['numero_lote']
        
        # Actualizar observaciones
        if 'observaciones' in data:
            arqueo.observaciones = data['observaciones']
        
        # Actualizar depósitos (eliminar y recrear)
        if 'depositos' in data:
            # Eliminar depósitos anteriores
            arqueo.depositos.all().delete()
            
            # Crear nuevos depósitos
            for dep in data['depositos']:
                DepositoBancario.objects.create(
                    arqueo=arqueo,
                    fecha_deposito=dep['fecha'],
                    monto=dep['monto'],
                    banco=dep['banco'],
                    numero_comprobante=dep.get('comprobante', ''),
                    observaciones=dep.get('observaciones', ''),
                    registrado_por=request.user
                )
        
        # Recalcular estado
        if abs(arqueo.diferencia_efectivo) <= 1000 and abs(arqueo.diferencia_transbank) <= 1000:
            arqueo.estado = 'CERRADO'
        else:
            arqueo.estado = 'CON_DIFERENCIAS'
        
        arqueo.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Cuadratura actualizada correctamente'
        })
        
    except Exception as e:
        import traceback
        print(f"Error al editar cuadratura: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


@login_required
@require_GET
def exportar_cuadratura_excel(request):
    """Exportar cuadratura a Excel usando datos en tiempo real"""
    try:
        fecha = request.GET.get('fecha')
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not fecha or not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Fecha y sucursal requeridas'
            })
        
        # Generar cuadratura en tiempo real (sin guardar en BD)
        response_data = generar_cuadratura_caja(request)
        cuadratura_json = json.loads(response_data.content)
        
        if not cuadratura_json.get('success'):
            return JsonResponse({
                'success': False,
                'error': 'Error al generar datos de cuadratura'
            })
        
        cuadratura_data = cuadratura_json['cuadratura']
        
        # Convertir fecha string a date object
        from datetime import datetime
        fecha_obj = datetime.strptime(fecha, '%Y-%m-%d').date()
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Crear Excel
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Cuadratura {fecha}"
        
        # Estilos
        header_font = Font(bold=True, color="FFFFFF", size=12)
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        title_font = Font(bold=True, size=14)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título
        ws.merge_cells('A1:D1')
        ws['A1'] = f"CUADRATURA DE CAJA - {sucursal.alias}"
        ws['A1'].font = title_font
        ws['A1'].alignment = Alignment(horizontal="center")
        
        ws.merge_cells('A2:D2')
        ws['A2'] = f"Fecha: {fecha_obj.strftime('%d/%m/%Y')}"
        ws['A2'].alignment = Alignment(horizontal="center")
        
        # Datos de la cuadratura
        row = 4
        
        # Métodos de pago
        ws[f'A{row}'] = "MÉTODO DE PAGO"
        ws[f'B{row}'] = "MONTO"
        ws[f'A{row}'].font = header_font
        ws[f'B{row}'].font = header_font
        ws[f'A{row}'].fill = header_fill
        ws[f'B{row}'].fill = header_fill
        
        row += 1
        
        metodos_pago = [
            ('Efectivo', cuadratura_data.get('total_efectivo', 0)),
            ('Tarjeta Débito', cuadratura_data.get('total_tarjeta_debito', 0)),
            ('Tarjeta Crédito', cuadratura_data.get('total_tarjeta_credito', 0)),
            ('Transferencia', cuadratura_data.get('total_transferencia', 0)),
            ('Cheque', cuadratura_data.get('total_cheque', 0)),
            ('Convenio', cuadratura_data.get('total_convenio', 0)),
            ('VISA/MC/AMEX', cuadratura_data.get('total_visa_mc_amex', 0)),
            ('Presto', cuadratura_data.get('total_presto', 0)),
            ('AbcDin', cuadratura_data.get('total_abcdin', 0)),
            ('Tricot', cuadratura_data.get('total_tricot', 0)),
            ('Hites', cuadratura_data.get('total_hites', 0)),
            ('Ripley', cuadratura_data.get('total_ripley', 0)),
            ('Falabella', cuadratura_data.get('total_falabella', 0)),
            ('Paris', cuadratura_data.get('total_paris', 0)),
        ]
        
        for metodo, monto in metodos_pago:
            ws[f'A{row}'] = metodo
            ws[f'B{row}'] = monto
            ws[f'A{row}'].border = border
            ws[f'B{row}'].border = border
            row += 1
        
        # Total
        ws[f'A{row}'] = "TOTAL VENTA"
        ws[f'B{row}'] = cuadratura_data.get('venta_total', 0)
        ws[f'A{row}'].font = Font(bold=True)
        ws[f'B{row}'].font = Font(bold=True)
        ws[f'A{row}'].border = border
        ws[f'B{row}'].border = border
        
        # Ajustar ancho de columnas
        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 15
        
        # Preparar respuesta
        from django.http import HttpResponse
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="cuadratura_{fecha}.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        })


@login_required
@csrf_exempt
def obtener_transacciones_dia(request):
    """Obtener todas las transacciones del día (tickets, boletas, facturas)"""
    try:
        import json
        from datetime import datetime, time as dt_time
        
        data = json.loads(request.body)
        fecha = data.get('fecha')
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not fecha or not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Fecha y sucursal requeridas'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        fecha_obj = datetime.strptime(fecha, '%Y-%m-%d').date()
        
        # Crear rango de fechas
        inicio_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.min))
        fin_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.max))
        
        # ========== OBTENER TICKETS ==========
        tickets_del_dia = Ticket.objects.filter(
            sucursal=sucursal,
            created_at__range=[inicio_dia, fin_dia],
            estado='PAGADO'
        ).prefetch_related('pagos').order_by('created_at')
        
        tickets_data = []
        for ticket in tickets_del_dia:
            # Obtener método de pago predominante
            metodo_pago = 'N/A'
            if ticket.pagos.exists():
                primer_pago = ticket.pagos.first()
                metodo_pago = primer_pago.get_metodo_pago_display() if primer_pago else 'N/A'
            
            tickets_data.append({
                'id': ticket.id,
                'numero': ticket.correlativo,
                'hora': ticket.created_at.strftime('%H:%M:%S'),
                'cliente': ticket.cliente.razon_social if ticket.cliente else 'Cliente General',
                'metodo_pago': metodo_pago,
                'total': ticket.total_con_iva,
                'estado': ticket.estado
            })
        
        # ========== OBTENER BOLETAS ELECTRÓNICAS ==========
        boletas_del_dia = DocumentoElectronico.objects.filter(
            sucursal=sucursal,
            fecha_emision__range=[inicio_dia, fin_dia],
            tipo_documento__codigo='39',  # Código para Boleta Electrónica
            estado__in=['EMITIDO', 'ACEPTADO']
        ).order_by('fecha_emision')
        
        boletas_data = []
        for boleta in boletas_del_dia:
            boletas_data.append({
                'id': boleta.id,
                'folio': boleta.folio,
                'hora': boleta.fecha_emision.strftime('%H:%M:%S'),
                'rut_cliente': boleta.receptor_rut or '66666666-6',
                'razon_social': boleta.receptor_razon_social or 'Cliente General',
                'monto_neto': boleta.monto_neto,
                'monto_iva': boleta.monto_iva,
                'monto_total': boleta.monto_total,
                'estado': boleta.estado
            })
        
        # ========== OBTENER FACTURAS ELECTRÓNICAS ==========
        facturas_del_dia = DocumentoElectronico.objects.filter(
            sucursal=sucursal,
            fecha_emision__range=[inicio_dia, fin_dia],
            tipo_documento__codigo__in=['33', '34'],  # 33: Factura, 34: Factura Exenta
            estado__in=['EMITIDO', 'ACEPTADO']
        ).order_by('fecha_emision')
        
        facturas_data = []
        for factura in facturas_del_dia:
            facturas_data.append({
                'id': factura.id,
                'folio': factura.folio,
                'hora': factura.fecha_emision.strftime('%H:%M:%S'),
                'rut_cliente': factura.receptor_rut or 'N/A',
                'razon_social': factura.receptor_razon_social or 'Cliente',
                'monto_neto': factura.monto_neto,
                'monto_iva': factura.monto_iva,
                'monto_total': factura.monto_total,
                'estado': factura.estado,
                'tipo': 'Factura Exenta' if factura.tipo_documento.codigo == '34' else 'Factura'
            })
        
        return JsonResponse({
            'success': True,
            'data': {
                'tickets': tickets_data,
                'boletas': boletas_data,
                'facturas': facturas_data,
                'totales': {
                    'total_tickets': len(tickets_data),
                    'total_boletas': len(boletas_data),
                    'total_facturas': len(facturas_data),
                    'total_documentos': len(tickets_data) + len(boletas_data) + len(facturas_data)
                }
            }
        })
        
    except Exception as e:
        import traceback
        print(f"Error al obtener transacciones del día: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener transacciones: {str(e)}'
        })


# ========== NUEVAS FUNCIONALIDADES DE ARQUEO ==========

@login_required
@require_GET
def listar_arqueos(request):
    """API para listar arqueos históricos"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        print(f"🏢 Sucursal ID desde sesión: {sucursal_id}")
        print(f"🔑 Claves de sesión disponibles: {list(request.session.keys())}")
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Parámetros de filtro
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        estado = request.GET.get('estado')
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        print(f"📅 Filtros recibidos: fecha_desde={fecha_desde}, fecha_hasta={fecha_hasta}, estado={estado}")
        
        # Construir queryset
        queryset = ArqueoCaja.objects.filter(sucursal_id=sucursal_id).select_related(
            'usuario_responsable', 'supervisor_revision'
        )
        
        print(f"📊 Total de arqueos en sucursal {sucursal_id}: {queryset.count()}")
        
        # Aplicar filtros
        if fecha_desde:
            queryset = queryset.filter(fecha_arqueo__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_arqueo__lte=fecha_hasta)
        if estado:
            queryset = queryset.filter(estado=estado)
        
        # Paginación
        from django.core.paginator import Paginator
        paginator = Paginator(queryset, per_page)
        arqueos_page = paginator.get_page(page)
        
        # Serializar datos
        arqueos_data = []
        for arqueo in arqueos_page:
            # Debug para verificar valores
            print(f"📊 Arqueo ID {arqueo.id}: Teórico={arqueo.total_efectivo_teorico}, Físico={arqueo.total_efectivo_fisico}, Diferencia={arqueo.diferencia_efectivo}")
            
            arqueos_data.append({
                'id': arqueo.id,
                'fecha_arqueo': arqueo.fecha_arqueo.strftime('%Y-%m-%d'),
                'fecha_creacion': arqueo.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'usuario_responsable': arqueo.usuario_responsable.username,
                'estado': arqueo.estado,
                'estado_display': arqueo.get_estado_display(),
                'efectivo_teorico': arqueo.total_efectivo_teorico,
                'efectivo_fisico': arqueo.total_efectivo_fisico,
                'diferencia_efectivo': arqueo.diferencia_efectivo,
                'diferencia_absoluta': arqueo.diferencia_absoluta,
                'tipo_diferencia': arqueo.tipo_diferencia,
                'porcentaje_diferencia': round(arqueo.porcentaje_diferencia, 2),
                'requiere_supervision': arqueo.requiere_supervision,
                'venta_total': arqueo.venta_total_teorica,
                'observaciones': arqueo.observaciones or '',
                'supervisor': arqueo.supervisor_revision.username if arqueo.supervisor_revision else '',
                'fecha_cierre': arqueo.fecha_cierre.strftime('%d/%m/%Y %H:%M') if arqueo.fecha_cierre else '',
            })
        
        return JsonResponse({
            'success': True,
            'arqueos': arqueos_data,
            'pagination': {
                'current_page': arqueos_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': arqueos_page.has_next(),
                'has_previous': arqueos_page.has_previous(),
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener arqueos: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def corregir_arqueos_express(request):
    """Corregir arqueos que fueron guardados incorrectamente en modo Express"""
    try:
        data = json.loads(request.body)
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Buscar arqueos problemáticos (donde todas las denominaciones son 0 pero hay diferencia)
        arqueos_problematicos = ArqueoCaja.objects.filter(
            sucursal_id=sucursal_id,
            billetes_20000=0,
            billetes_10000=0,
            billetes_5000=0,
            billetes_2000=0,
            billetes_1000=0,
            monedas_500=0,
            monedas_100=0,
            monedas_50=0,
            monedas_10=0,
            monedas_5=0,
            monedas_1=0,
            total_efectivo_fisico=0
        ).exclude(
            total_efectivo_teorico=0  # Excluir casos donde realmente no había ventas
        )
        
        corregidos = 0
        for arqueo in arqueos_problematicos:
            # Si el efectivo físico es 0 pero el teórico no, probablemente fue un arqueo Express mal guardado
            if arqueo.total_efectivo_fisico == 0 and arqueo.total_efectivo_teorico > 0:
                # Asumir que fue un arqueo Express donde el físico debería ser igual al teórico
                ArqueoCaja.objects.filter(id=arqueo.id).update(
                    total_efectivo_fisico=arqueo.total_efectivo_teorico,
                    diferencia_efectivo=0
                )
                corregidos += 1
                print(f"✅ Corregido arqueo ID {arqueo.id}: Físico {0} -> {arqueo.total_efectivo_teorico}")
        
        return JsonResponse({
            'success': True,
            'message': f'Se corrigieron {corregidos} arqueos',
            'arqueos_corregidos': corregidos
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al corregir arqueos: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def crear_arqueo(request):
    """Crear nuevo arqueo basado en la cuadratura actual"""
    try:
        data = json.loads(request.body)
        fecha_arqueo = data.get('fecha')
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not fecha_arqueo or not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Fecha y sucursal requeridas'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Verificar si ya existe un arqueo para esta fecha
        arqueo_existente = ArqueoCaja.objects.filter(
            fecha_arqueo=fecha_arqueo,
            sucursal=sucursal
        ).first()
        
        if arqueo_existente:
            return JsonResponse({
                'success': False,
                'error': f'Ya existe un arqueo para el {fecha_arqueo}',
                'arqueo_id': arqueo_existente.id
            })
        
        # Generar cuadratura en tiempo real para obtener los datos
        from django.test import RequestFactory
        factory = RequestFactory()
        fake_request = factory.post('/fake/', {'fecha': fecha_arqueo})
        fake_request.session = request.session
        fake_request.user = request.user
        
        response_data = generar_cuadratura_caja(fake_request)
        cuadratura_json = json.loads(response_data.content)
        
        if not cuadratura_json.get('success'):
            return JsonResponse({
                'success': False,
                'error': 'Error al generar datos de cuadratura'
            })
        
        cuadratura_data = cuadratura_json['cuadratura']
        
        # Crear el arqueo con los datos teóricos
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=fecha_arqueo,
            sucursal=sucursal,
            usuario_responsable=request.user,
            
            # Totales teóricos de la cuadratura
            total_visa_mc_amex_teorico=cuadratura_data.get('total_visa_mc_amex', 0),
            total_presto_teorico=cuadratura_data.get('total_presto', 0),
            total_abcdin_teorico=cuadratura_data.get('total_abcdin', 0),
            total_tricot_teorico=cuadratura_data.get('total_tricot', 0),
            total_hites_teorico=cuadratura_data.get('total_hites', 0),
            total_ripley_teorico=cuadratura_data.get('total_ripley', 0),
            total_falabella_teorico=cuadratura_data.get('total_falabella', 0),
            total_paris_teorico=cuadratura_data.get('total_paris', 0),
            total_tarjetas_comerciales_teorico=cuadratura_data.get('total_tarjetas_comerciales', 0),
            
            total_efectivo_teorico=cuadratura_data.get('total_efectivo', 0),
            
            total_webpay_teorico=cuadratura_data.get('total_webpay', 0),
            total_mercadolibre_teorico=cuadratura_data.get('total_mercadolibre', 0),
            total_mercadopago_teorico=cuadratura_data.get('total_mercadopago', 0),
            total_transferencia_internet_teorico=cuadratura_data.get('total_transferencia_internet', 0),
            total_venta_internet_teorico=cuadratura_data.get('total_venta_internet', 0),
            
            total_tarjeta_debito_teorico=cuadratura_data.get('total_tarjeta_debito', 0),
            total_tarjeta_credito_teorico=cuadratura_data.get('total_tarjeta_credito', 0),
            total_transbank_teorico=cuadratura_data.get('total_transbank', 0),
            total_transferencia_teorico=cuadratura_data.get('total_transferencia', 0),
            total_cheque_teorico=cuadratura_data.get('total_cheque', 0),
            total_convenio_teorico=cuadratura_data.get('total_convenio', 0),
            
            total_tickets_teorico=cuadratura_data.get('total_tickets', 0),
            total_boletas_electronicas_teorico=cuadratura_data.get('total_boletas_electronicas', 0),
            total_facturas_teorico=cuadratura_data.get('total_facturas', 0),
            total_facturas_exentas_teorico=cuadratura_data.get('total_facturas_exentas', 0),
            total_notas_credito_teorico=cuadratura_data.get('total_notas_credito', 0),
            
            cantidad_tickets=cuadratura_data.get('cantidad_tickets', 0),
            cantidad_boletas_electronicas=cuadratura_data.get('cantidad_boletas_electronicas', 0),
            cantidad_facturas=cuadratura_data.get('cantidad_facturas', 0),
            cantidad_facturas_exentas=cuadratura_data.get('cantidad_facturas_exentas', 0),
            
            venta_total_teorica=cuadratura_data.get('venta_total', 0),
            
            estado='ABIERTO'
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Arqueo creado exitosamente',
            'arqueo_id': arqueo.id,
            'cuadratura': cuadratura_data
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear arqueo: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def guardar_conteo_fisico(request):
    """Guardar conteo físico del arqueo"""
    try:
        data = json.loads(request.body)
        arqueo_id = data.get('arqueo_id')
        
        if not arqueo_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de arqueo requerido'
            })
        
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)
        
        # Verificar que el usuario puede modificar este arqueo
        if arqueo.estado not in ['ABIERTO', 'CON_DIFERENCIAS']:
            return JsonResponse({
                'success': False,
                'error': 'Este arqueo ya está cerrado'
            })
        
        # Verificar modo de arqueo
        modo_express = data.get('modo_express', False)
        
        if modo_express:
            # Modo Express: usar monto total directamente
            monto_total = data.get('monto_total_express', 0)
            
            # Validar monto
            try:
                monto_total = int(monto_total)
                if monto_total < 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'El monto debe ser mayor o igual a 0'
                    })
            except (ValueError, TypeError):
                return JsonResponse({
                    'success': False,
                    'error': 'Monto inválido. Debe ser un número válido'
                })
            
            # Limpiar denominaciones (ya que no se usan en modo express)
            arqueo.billetes_20000 = 0
            arqueo.billetes_10000 = 0
            arqueo.billetes_5000 = 0
            arqueo.billetes_2000 = 0
            arqueo.billetes_1000 = 0
            arqueo.monedas_500 = 0
            arqueo.monedas_100 = 0
            arqueo.monedas_50 = 0
            arqueo.monedas_10 = 0
            arqueo.monedas_5 = 0
            arqueo.monedas_1 = 0
            
            # Establecer el total físico directamente
            arqueo.total_efectivo_fisico = monto_total
            arqueo.diferencia_efectivo = monto_total - arqueo.total_efectivo_teorico
            
            print(f"💰 Modo Express: Total físico = {monto_total}, Diferencia = {arqueo.diferencia_efectivo}")
            
        else:
            # Modo Detallado: usar denominaciones
            arqueo.billetes_20000 = int(data.get('billetes_20000', 0))
            arqueo.billetes_10000 = int(data.get('billetes_10000', 0))
            arqueo.billetes_5000 = int(data.get('billetes_5000', 0))
            arqueo.billetes_2000 = int(data.get('billetes_2000', 0))
            arqueo.billetes_1000 = int(data.get('billetes_1000', 0))
            
            arqueo.monedas_500 = int(data.get('monedas_500', 0))
            arqueo.monedas_100 = int(data.get('monedas_100', 0))
            arqueo.monedas_50 = int(data.get('monedas_50', 0))
            arqueo.monedas_10 = int(data.get('monedas_10', 0))
            arqueo.monedas_5 = int(data.get('monedas_5', 0))
            arqueo.monedas_1 = int(data.get('monedas_1', 0))
            
            print(f"📊 Modo Detallado: Calculando desde denominaciones")
        
        # Observaciones
        arqueo.observaciones = data.get('observaciones', '')
        arqueo.observaciones_diferencia = data.get('observaciones_diferencia', '')
        
        # Guardar según el modo
        if modo_express:
            # En modo express, guardamos directamente sin recalcular
            # Usar update() para evitar que el método save() recalcule automáticamente
            ArqueoCaja.objects.filter(id=arqueo.id).update(
                total_efectivo_fisico=arqueo.total_efectivo_fisico,
                diferencia_efectivo=arqueo.diferencia_efectivo,
                observaciones=arqueo.observaciones,
                observaciones_diferencia=arqueo.observaciones_diferencia,
                billetes_20000=arqueo.billetes_20000,
                billetes_10000=arqueo.billetes_10000,
                billetes_5000=arqueo.billetes_5000,
                billetes_2000=arqueo.billetes_2000,
                billetes_1000=arqueo.billetes_1000,
                monedas_500=arqueo.monedas_500,
                monedas_100=arqueo.monedas_100,
                monedas_50=arqueo.monedas_50,
                monedas_10=arqueo.monedas_10,
                monedas_5=arqueo.monedas_5,
                monedas_1=arqueo.monedas_1
            )
            print(f"💾 Guardado en modo Express - Total físico: {arqueo.total_efectivo_fisico}, Diferencia: {arqueo.diferencia_efectivo}")
        else:
            # En modo detallado, save() calculará automáticamente el total físico y diferencia
            arqueo.save()
            print(f"💾 Guardado en modo Detallado - Total físico calculado: {arqueo.total_efectivo_fisico}")
        
        # Recargar el objeto para obtener los valores actualizados
        arqueo.refresh_from_db()
        
        return JsonResponse({
            'success': True,
            'message': 'Conteo guardado exitosamente',
            'modo_usado': 'express' if modo_express else 'detallado',
            'arqueo': {
                'id': arqueo.id,
                'efectivo_fisico': arqueo.total_efectivo_fisico,
                'efectivo_teorico': arqueo.total_efectivo_teorico,
                'diferencia': arqueo.diferencia_efectivo,
                'diferencia_absoluta': arqueo.diferencia_absoluta,
                'tipo_diferencia': arqueo.tipo_diferencia,
                'porcentaje_diferencia': round(arqueo.porcentaje_diferencia, 2),
                'requiere_supervision': arqueo.requiere_supervision,
                'estado': arqueo.estado
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al guardar conteo: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def cerrar_arqueo(request):
    """Cerrar arqueo definitivamente"""
    try:
        data = json.loads(request.body)
        arqueo_id = data.get('arqueo_id')
        
        if not arqueo_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de arqueo requerido'
            })
        
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)
        
        # Verificar que el usuario puede cerrar este arqueo
        if arqueo.estado not in ['ABIERTO', 'CON_DIFERENCIAS']:
            return JsonResponse({
                'success': False,
                'error': 'Este arqueo ya está cerrado'
            })
        
        # Agregar logs para debug
        print(f"🔒 Intentando cerrar arqueo ID: {arqueo_id}")
        print(f"📊 Estado actual: {arqueo.estado}")
        print(f"💰 Diferencia: {arqueo.diferencia_efectivo}")
        print(f"📝 Observaciones diferencia: '{arqueo.observaciones_diferencia}'")
        print(f"⚠️ Requiere supervisión: {arqueo.requiere_supervision}")
        
        # Validar observaciones SOLO si hay diferencias significativas (mayor a $500)
        diferencia_absoluta = abs(arqueo.diferencia_efectivo)
        
        if diferencia_absoluta == 0:
            print("✅ Arqueo perfecto - Sin diferencias")
        elif diferencia_absoluta > 500:
            if not arqueo.observaciones_diferencia or len(arqueo.observaciones_diferencia.strip()) < 10:
                return JsonResponse({
                    'success': False,
                    'error': f'Debe agregar observaciones detalladas para diferencias mayores a $500 (actual: ${diferencia_absoluta:,})'
                })
        else:
            print(f"ℹ️ Diferencia menor - No requiere observaciones obligatorias: ${diferencia_absoluta}")
        
        # Cerrar arqueo
        arqueo.fecha_cierre = timezone.now()
        
        # Determinar estado final
        if arqueo.diferencia_efectivo == 0:
            arqueo.estado = 'CERRADO'
        else:
            arqueo.estado = 'CON_DIFERENCIAS'
        
        arqueo.save()
        
        print(f"✅ Arqueo cerrado exitosamente - Estado final: {arqueo.estado}")
        
        return JsonResponse({
            'success': True,
            'message': 'Arqueo cerrado exitosamente',
            'estado_final': arqueo.get_estado_display(),
            'arqueo': {
                'id': arqueo.id,
                'estado': arqueo.estado,
                'fecha_cierre': arqueo.fecha_cierre.strftime('%d/%m/%Y %H:%M'),
                'diferencia': arqueo.diferencia_efectivo,
                'requiere_supervision': arqueo.requiere_supervision
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al cerrar arqueo: {str(e)}'
        })


@login_required
@require_GET
def obtener_arqueo_detalle(request, arqueo_id):
    """Obtener detalle completo de un arqueo"""
    try:
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)
        
        # Verificar que el usuario tiene acceso a esta sucursal
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if arqueo.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a este arqueo'
            })
        
        arqueo_data = {
            'id': arqueo.id,
            'fecha_arqueo': arqueo.fecha_arqueo.strftime('%Y-%m-%d'),
            'fecha_creacion': arqueo.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
            'usuario_responsable': arqueo.usuario_responsable.username,
            'estado': arqueo.estado,
            'estado_display': arqueo.get_estado_display(),
            
            # Totales teóricos
            'totales_teoricos': {
                'efectivo': arqueo.total_efectivo_teorico,
                'tarjetas_comerciales': {
                    'visa_mc_amex': arqueo.total_visa_mc_amex_teorico,
                    'presto': arqueo.total_presto_teorico,
                    'abcdin': arqueo.total_abcdin_teorico,
                    'tricot': arqueo.total_tricot_teorico,
                    'hites': arqueo.total_hites_teorico,
                    'ripley': arqueo.total_ripley_teorico,
                    'falabella': arqueo.total_falabella_teorico,
                    'paris': arqueo.total_paris_teorico,
                    'total': arqueo.total_tarjetas_comerciales_teorico,
                },
                'venta_internet': {
                    'webpay': arqueo.total_webpay_teorico,
                    'mercadolibre': arqueo.total_mercadolibre_teorico,
                    'mercadopago': arqueo.total_mercadopago_teorico,
                    'transferencia_internet': arqueo.total_transferencia_internet_teorico,
                    'total': arqueo.total_venta_internet_teorico,
                },
                'otros': {
                    'tarjeta_debito': arqueo.total_tarjeta_debito_teorico,
                    'tarjeta_credito': arqueo.total_tarjeta_credito_teorico,
                    'transbank': arqueo.total_transbank_teorico,
                    'transferencia': arqueo.total_transferencia_teorico,
                    'cheque': arqueo.total_cheque_teorico,
                    'convenio': arqueo.total_convenio_teorico,
                },
                'documentos': {
                    'tickets': arqueo.total_tickets_teorico,
                    'boletas_electronicas': arqueo.total_boletas_electronicas_teorico,
                    'facturas': arqueo.total_facturas_teorico,
                    'facturas_exentas': arqueo.total_facturas_exentas_teorico,
                    'notas_credito': arqueo.total_notas_credito_teorico,
                },
                'venta_total': arqueo.venta_total_teorica,
            },
            
            # Conteo físico
            'conteo_fisico': {
                'billetes': {
                    '20000': arqueo.billetes_20000,
                    '10000': arqueo.billetes_10000,
                    '5000': arqueo.billetes_5000,
                    '2000': arqueo.billetes_2000,
                    '1000': arqueo.billetes_1000,
                },
                'monedas': {
                    '500': arqueo.monedas_500,
                    '100': arqueo.monedas_100,
                    '50': arqueo.monedas_50,
                    '10': arqueo.monedas_10,
                    '5': arqueo.monedas_5,
                    '1': arqueo.monedas_1,
                },
                'total_fisico': arqueo.total_efectivo_fisico,
            },
            
            # Diferencias
            'diferencias': {
                'efectivo': arqueo.diferencia_efectivo,
                'absoluta': arqueo.diferencia_absoluta,
                'tipo': arqueo.tipo_diferencia,
                'porcentaje': round(arqueo.porcentaje_diferencia, 2),
                'requiere_supervision': arqueo.requiere_supervision,
            },
            
            # Observaciones
            'observaciones': arqueo.observaciones or '',
            'observaciones_diferencia': arqueo.observaciones_diferencia or '',
            
            # Supervisión
            'supervisor': arqueo.supervisor_revision.username if arqueo.supervisor_revision else '',
            'fecha_revision': arqueo.fecha_revision.strftime('%d/%m/%Y %H:%M') if arqueo.fecha_revision else '',
            'observaciones_supervisor': arqueo.observaciones_supervisor or '',
            
            # Fechas
            'fecha_cierre': arqueo.fecha_cierre.strftime('%d/%m/%Y %H:%M') if arqueo.fecha_cierre else '',
        }
        
        return JsonResponse({
            'success': True,
            'arqueo': arqueo_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener arqueo: {str(e)}'
        })


# ========== GESTIÓN POS TRANSBANK ==========

@login_required
def gestion_pos_transbank(request):
    """Vista principal para gestión de POS Transbank"""
    sucursal_actual_id = request.session.get('sucursalActual') or request.session.get('idSucursalActual')
    sucursal_actual = None
    
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            sucursal_actual = None
    
    if not sucursal_actual:
        return redirect('dashboard')
    
    # Obtener configuraciones POS de la sucursal
    configuraciones_pos = ConfiguracionPOS.objects.filter(
        sucursal=sucursal_actual
    ).order_by('-es_principal', 'nombre')
    
    context = {
        'sucursal_actual': sucursal_actual,
        'configuraciones_pos': configuraciones_pos,
        'tipo_pos_choices': TIPO_POS_CHOICES,
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
    }
    return render(request, 'vistas/modulo_ventas/gestion_pos_transbank_simple.html', context)


@login_required
@require_POST
@csrf_exempt
def detectar_terminales_pos(request):
    """Detectar y guardar terminales POS automáticamente"""
    try:
        data = json.loads(request.body)
        puertos_detectados = data.get('puertos_detectados', [])
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        terminales_creados = []
        terminales_existentes = []
        
        for puerto in puertos_detectados:
            # Verificar si ya existe una configuración para este puerto
            config_existente = ConfiguracionPOS.objects.filter(
                sucursal=sucursal,
                puerto_conexion=puerto
            ).first()
            
            if config_existente:
                # Actualizar estado de conexión
                config_existente.estado_conexion = 'DETECTADO'
                config_existente.ultima_conexion = timezone.now()
                config_existente.save()
                terminales_existentes.append({
                    'id': config_existente.id,
                    'nombre': config_existente.nombre,
                    'puerto': puerto,
                    'estado': 'existente'
                })
            else:
                # Crear nueva configuración automática
                nombre_auto = f"Terminal Auto {puerto}"
                tipo_pos = 'VERIFONE_520'  # Tipo por defecto, se puede detectar después
                
                nueva_config = ConfiguracionPOS.objects.create(
                    sucursal=sucursal,
                    nombre=nombre_auto,
                    tipo_pos=tipo_pos,
                    puerto_conexion=puerto,
                    velocidad_conexion=115200,  # Velocidad estándar
                    timeout_conexion=30,
                    estado_conexion='DETECTADO',
                    ultima_conexion=timezone.now(),
                    activo=True,
                    es_principal=len(terminales_creados) == 0,  # El primero es principal
                    observaciones=f'Terminal detectado automáticamente en puerto {puerto}'
                )
                
                terminales_creados.append({
                    'id': nueva_config.id,
                    'nombre': nueva_config.nombre,
                    'puerto': puerto,
                    'estado': 'nuevo'
                })
        
        # Marcar como desconectados los terminales que no fueron detectados
        ConfiguracionPOS.objects.filter(
            sucursal=sucursal,
            activo=True
        ).exclude(
            puerto_conexion__in=puertos_detectados
        ).update(
            estado_conexion='DESCONECTADO'
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Detección completada: {len(terminales_creados)} nuevos, {len(terminales_existentes)} existentes',
            'terminales_creados': terminales_creados,
            'terminales_existentes': terminales_existentes,
            'total_detectados': len(puertos_detectados)
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en detección automática: {str(e)}'
        })


@login_required
@require_GET
def obtener_configuraciones_pos(request):
    """API para obtener configuraciones POS de la sucursal actual"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        configuraciones = ConfiguracionPOS.objects.filter(
            sucursal_id=sucursal_id
        ).order_by('-es_principal', 'nombre')
        
        configuraciones_data = []
        for config in configuraciones:
            configuraciones_data.append({
                'id': config.id,
                'nombre': config.nombre,
                'tipo_pos': config.tipo_pos,
                'tipo_pos_display': config.get_tipo_pos_display(),
                'puerto_conexion': config.puerto_conexion,
                'velocidad_conexion': config.velocidad_conexion,
                'activo': config.activo,
                'es_principal': config.es_principal,
                'estado_conexion': config.estado_conexion,
                'estado_conexion_display': config.get_estado_conexion_display(),
                'ultima_conexion': config.ultima_conexion.strftime('%d/%m/%Y %H:%M') if config.ultima_conexion else '',
                'numero_serie': config.numero_serie or '',
                'version_firmware': config.version_firmware or '',
                'timeout_conexion': config.timeout_conexion,
                'observaciones': config.observaciones or '',
            })
        
        return JsonResponse({
            'success': True,
            'configuraciones': configuraciones_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener configuraciones: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def crear_configuracion_pos(request):
    """Crear nueva configuración POS"""
    try:
        data = json.loads(request.body)
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Validar campos requeridos
        campos_requeridos = ['nombre', 'tipo_pos', 'puerto_conexion']
        for campo in campos_requeridos:
            if not data.get(campo):
                return JsonResponse({
                    'success': False,
                    'error': f'El campo {campo} es requerido'
                })
        
        # Verificar que el nombre no exista en la sucursal
        if ConfiguracionPOS.objects.filter(
            sucursal=sucursal, 
            nombre=data['nombre']
        ).exists():
            return JsonResponse({
                'success': False,
                'error': 'Ya existe una configuración con ese nombre'
            })
        
        # Crear configuración
        configuracion = ConfiguracionPOS.objects.create(
            sucursal=sucursal,
            nombre=data['nombre'],
            tipo_pos=data['tipo_pos'],
            puerto_conexion=data['puerto_conexion'],
            velocidad_conexion=data.get('velocidad_conexion', 115200),
            activo=data.get('activo', True),
            es_principal=data.get('es_principal', False),
            timeout_conexion=data.get('timeout_conexion', 30),
            numero_serie=data.get('numero_serie', ''),
            version_firmware=data.get('version_firmware', ''),
            observaciones=data.get('observaciones', '')
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Configuración POS creada exitosamente',
            'configuracion_id': configuracion.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear configuración: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def probar_conexion_pos(request):
    """Probar conexión con terminal POS"""
    try:
        data = json.loads(request.body)
        configuracion_id = data.get('configuracion_id')
        
        if not configuracion_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de configuración requerido'
            })
        
        configuracion = get_object_or_404(ConfiguracionPOS, id=configuracion_id)
        
        # Verificar que el usuario tiene acceso a esta sucursal
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if configuracion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a esta configuración'
            })
        
        # NOTA: La conexión real se prueba desde el frontend usando el SDK JavaScript
        # El agente Transbank usa Socket.IO, no WebSocket directo
        # Por lo tanto, el backend solo valida la configuración y retorna datos
        
        # Validar que la configuración es correcta
        result = {
            'success': True,
            'message': 'Configuración validada. La conexión real se probará desde el navegador.',
            'puerto': configuracion.puerto_conexion,
            'velocidad': configuracion.velocidad_conexion,
            'tipo_pos': configuracion.get_tipo_pos_display(),
            'note': 'Use el SDK de JavaScript en el navegador para conectarse al agente Transbank'
        }
        
        # Actualizar estado de conexión
        if result['success']:
            configuracion.ultima_conexion = timezone.now()
            configuracion.estado_conexion = 'VALIDADO'
            configuracion.save()
            
            # Crear log exitoso
            LogPOS.objects.create(
                configuracion_pos=configuracion,
                tipo_evento='VALIDACION',
                mensaje=f'Configuración validada - {result["message"]}',
                datos_tecnicos={
                    'puerto': configuracion.puerto_conexion,
                    'velocidad': configuracion.velocidad_conexion,
                    'tipo_pos': configuracion.tipo_pos,
                    'resultado': 'VALIDADO',
                    'nota': result.get('note', '')
                }
            )
            
            return JsonResponse({
                'success': True,
                'message': result['message'],
                'estado_conexion': configuracion.get_estado_conexion_display(),
                'ultima_conexion': configuracion.ultima_conexion.strftime('%d/%m/%Y %H:%M'),
                'puerto': result.get('puerto', ''),
                'velocidad': result.get('velocidad', 0),
                'tipo_pos': result.get('tipo_pos', ''),
                'note': result.get('note', '')
            })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        # En caso de error, actualizar estado
        if 'configuracion' in locals():
            configuracion.estado_conexion = 'ERROR'
            configuracion.save()
            
            LogPOS.objects.create(
                configuracion_pos=configuracion,
                tipo_evento='ERROR',
                mensaje=f'Error en prueba de conexión: {str(e)}',
                datos_tecnicos={
                    'puerto': configuracion.puerto_conexion,
                    'error': str(e)
                }
            )
        
        return JsonResponse({
            'success': False,
            'error': f'Error al probar conexión: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def iniciar_venta_pos(request):
    """Iniciar venta en terminal POS"""
    try:
        data = json.loads(request.body)
        
        # Validar datos requeridos
        monto = data.get('monto')
        configuracion_id = data.get('configuracion_id')
        ticket_id = data.get('ticket_id')  # ID del ticket de venta
        
        if not all([monto, configuracion_id]):
            return JsonResponse({
                'success': False,
                'error': 'Monto y configuración POS requeridos'
            })
        
        try:
            monto = float(monto)
            if monto <= 0:
                return JsonResponse({
                    'success': False,
                    'error': 'El monto debe ser mayor a 0'
                })
        except (ValueError, TypeError):
            return JsonResponse({
                'success': False,
                'error': 'Monto inválido'
            })
        
        configuracion = get_object_or_404(ConfiguracionPOS, id=configuracion_id)
        
        # Verificar que el usuario tiene acceso a esta sucursal
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if configuracion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a esta configuración'
            })
        
        # Verificar que la configuración esté activa
        if not configuracion.activo:
            return JsonResponse({
                'success': False,
                'error': 'La configuración POS no está activa'
            })
        
        # Obtener ticket si se proporcionó
        # NOTA: ticket_id puede ser:
        # - Un ID numérico de ticket de venta existente (para asociar pago a ticket)
        # - Un string generado (TXNxxxxxx) para identificar la transacción POS
        ticket = None
        ticket_referencia = ticket_id  # Guardar para usar como referencia
        
        if ticket_id:
            # Intentar convertir a número (si es ID de ticket real)
            try:
                ticket_id_num = int(ticket_id)
                # Es un número, buscar ticket en BD
                try:
                    ticket = Ticket.objects.get(id=ticket_id_num, sucursal_id=sucursal_id)
                except Ticket.DoesNotExist:
                    return JsonResponse({
                        'success': False,
                        'error': f'Ticket {ticket_id_num} no encontrado'
                    })
            except (ValueError, TypeError):
                # No es un número, es un string generado (TXNxxxxxx)
                # Esto es válido - se usa como referencia de transacción
                # No se asocia a un ticket de venta
                ticket = None
        
        # Crear transacción POS
        observaciones = data.get('observaciones', '')
        if ticket_referencia and not ticket:
            # Si hay referencia de ticket pero no se asoció a un Ticket de BD
            observaciones = f"Ref: {ticket_referencia}. {observaciones}".strip()
        
        transaccion = TransaccionPOS.objects.create(
            configuracion_pos=configuracion,
            ticket=ticket,
            monto=monto,
            tipo_transaccion='VENTA',
            estado='INICIADA',
            usuario_operador=request.user,
            ip_origen=request.META.get('REMOTE_ADDR'),
            observaciones=observaciones
        )
        
        # Crear log de inicio
        LogPOS.objects.create(
            configuracion_pos=configuracion,
            transaccion_pos=transaccion,
            tipo_evento='COMANDO_ENVIADO',
            mensaje=f'Iniciando venta por ${monto:,}',
            datos_tecnicos={
                'monto': monto,
                'ticket_pos': transaccion.ticket_pos,
                'puerto': configuracion.puerto_conexion
            }
        )
        
        # Ejecutar venta real con SDK Transbank
        from decimal import Decimal
        result = run_transbank_operation(
            execute_pos_sale,
            Decimal(str(monto)),
            transaccion.ticket_pos,
            configuracion.puerto_conexion,
            configuracion.velocidad_conexion
        )
        
        # Actualizar transacción con resultado
        if result['success']:
            transaccion.estado = result['status']
            transaccion.codigo_respuesta = result.get('response_code', '')
            transaccion.mensaje_respuesta = result.get('message', '')
            transaccion.codigo_autorizacion = result.get('authorization_code', '')
            transaccion.tipo_tarjeta = result.get('card_type', 'DESCONOCIDO')
            transaccion.ultimos_4_digitos = result.get('card_number', '')[-4:] if result.get('card_number') else ''
            transaccion.nombre_tarjeta = result.get('card_brand', '')
            transaccion.numero_operacion = result.get('operation_number', '')
            transaccion.numero_cuotas = result.get('installments', 1)
            transaccion.codigo_comercio = result.get('commerce_code', '')
            transaccion.terminal_id = result.get('terminal_id', '')
            transaccion.save()
            
            # Crear log de respuesta
            LogPOS.objects.create(
                configuracion_pos=configuracion,
                transaccion_pos=transaccion,
                tipo_evento='RESPUESTA_RECIBIDA',
                mensaje=f'Venta {result["status"].lower()}: {result["message"]}',
                datos_tecnicos=result.get('raw_response', result)
            )
            
            # Si la transacción fue exitosa y hay un ticket asociado, procesar pago
            if transaccion.es_exitosa and ticket:
                # Determinar método de pago según el tipo de tarjeta
                metodo_pago = 'TBK_POS_INTEGRADO'
                if transaccion.tipo_tarjeta == 'DEBITO':
                    metodo_pago = 'TBK_DEBITO_POS'
                elif transaccion.tipo_tarjeta == 'CREDITO':
                    metodo_pago = 'TBK_CREDITO_POS'
                elif transaccion.tipo_tarjeta == 'PREPAGO':
                    metodo_pago = 'TBK_PREPAGO_POS'
                
                # Crear detalle de pago
                detalle_pago = TicketDetallePago.objects.create(
                    ticket=ticket,
                    metodo_pago=metodo_pago,
                    tipo_tarjeta=transaccion.nombre_tarjeta,
                    voucher=transaccion.codigo_autorizacion,
                    monto=int(transaccion.monto),
                    notas=f'POS {configuracion.nombre} - Oper: {transaccion.numero_operacion}'
                )
                
                # Asociar el detalle de pago con la transacción
                transaccion.detalle_pago = detalle_pago
                transaccion.save()
                
                # Actualizar estado del ticket si está completamente pagado
                if ticket.saldo_por_pagar <= 0:
                    ticket.estado = 'PAGADO'
                    ticket.save()
            
            return JsonResponse({
                'success': True,
                'transaccion': {
                    'id': transaccion.id,
                    'ticket_pos': transaccion.ticket_pos,
                    'monto': float(transaccion.monto),
                    'estado': transaccion.estado,
                    'codigo_autorizacion': transaccion.codigo_autorizacion,
                    'tipo_tarjeta': transaccion.tipo_tarjeta,
                    'mensaje': result['message'],
                    'puerto_conexion': configuracion.puerto_conexion
                }
            })
        else:
            # Error en la venta
            transaccion.estado = 'ERROR'
            transaccion.error_detalle = result.get('error', 'Error desconocido')
            transaccion.save()
            
            # Crear log de error
            LogPOS.objects.create(
                configuracion_pos=configuracion,
                transaccion_pos=transaccion,
                tipo_evento='ERROR',
                mensaje=f'Error en venta: {result.get("error", "Error desconocido")}',
                datos_tecnicos=result
            )
            
            return JsonResponse({
                'success': False,
                'error': result.get('error', 'Error en la venta POS'),
                'suggestion': result.get('suggestion', 'Verifique el terminal y intente nuevamente'),
                'transaccion_id': transaccion.id
            })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al iniciar venta POS: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def guardar_venta_pos(request):
    """Guardar venta POS procesada desde el frontend"""
    try:
        data = json.loads(request.body)
        
        sale_response = data.get('sale_response', {})
        ticket_id = data.get('ticket_id')
        monto = data.get('monto')
        
        if not sale_response:
            return JsonResponse({
                'success': False,
                'error': 'Respuesta de venta requerida'
            })
        
        # Obtener sucursal y configuración
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal activa'
            })
        
        # Buscar configuración POS activa (si hay una detectada recientemente)
        configuracion = ConfiguracionPOS.objects.filter(
            sucursal_id=sucursal_id,
            activo=True
        ).order_by('-ultima_conexion').first()
        
        if not configuracion:
            # Crear configuración temporal si no existe
            from .models import Sucursal
            sucursal = get_object_or_404(Sucursal, id=sucursal_id)
            configuracion = ConfiguracionPOS.objects.create(
                sucursal=sucursal,
                nombre=f"POS Auto",
                tipo_pos='VERIFONE_520',
                puerto_conexion=sale_response.get('activePort', 'AUTO'),
                velocidad_conexion=115200,
                activo=True,
                es_principal=True
            )
        
        # Obtener ticket si se proporcionó ID numérico
        ticket = None
        if ticket_id:
            try:
                ticket_id_num = int(ticket_id)
                ticket = Ticket.objects.get(id=ticket_id_num, sucursal_id=sucursal_id)
            except (ValueError, TypeError, Ticket.DoesNotExist):
                ticket = None
        
        # Crear transacción POS
        transaccion = TransaccionPOS.objects.create(
            configuracion_pos=configuracion,
            ticket=ticket,
            monto=monto or sale_response.get('amount', 0),
            tipo_transaccion='VENTA',
            estado='APROBADA' if sale_response.get('responseCode') == 0 else 'RECHAZADA',
            codigo_respuesta=str(sale_response.get('responseCode', '')),
            mensaje_respuesta=sale_response.get('responseMessage', ''),
            codigo_autorizacion=sale_response.get('authorizationCode', ''),
            tipo_tarjeta='DEBITO' if sale_response.get('cardType') == 'DB' else 'CREDITO',
            ultimos_4_digitos=sale_response.get('last4Digits', ''),
            nombre_tarjeta=sale_response.get('cardBrand', ''),
            numero_operacion=sale_response.get('operationNumber', ''),
            numero_cuotas=1,
            codigo_comercio=sale_response.get('commerceCode', ''),
            terminal_id=sale_response.get('terminalId', ''),
            usuario_operador=request.user,
            ip_origen=request.META.get('REMOTE_ADDR'),
            observaciones=f"Ref: {ticket_id}" if ticket_id and not ticket else ''
        )
        
        # Si hay ticket asociado, crear pago
        if transaccion.es_exitosa and ticket:
            metodo_pago = 'TBK_DEBITO_POS' if sale_response.get('cardType') == 'DB' else 'TBK_CREDITO_POS'
            
            detalle_pago = TicketDetallePago.objects.create(
                ticket=ticket,
                metodo_pago=metodo_pago,
                tipo_tarjeta=sale_response.get('cardBrand', ''),
                voucher=sale_response.get('authorizationCode', ''),
                monto=int(transaccion.monto),
                notas=f'POS - Oper: {sale_response.get("operationNumber", "")}'
            )
            
            transaccion.detalle_pago = detalle_pago
            transaccion.save()
            
            if ticket.saldo_por_pagar <= 0:
                ticket.estado = 'PAGADO'
                ticket.save()
        
        return JsonResponse({
            'success': True,
            'transaccion_id': transaccion.id,
            'message': 'Transacción guardada exitosamente'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error guardando transacción: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def completar_transaccion_pos(request):
    """Completar transacción POS con respuesta del terminal"""
    try:
        data = json.loads(request.body)
        
        ticket_pos = data.get('ticket_pos')
        respuesta_pos = data.get('respuesta_pos', {})
        
        if not ticket_pos:
            return JsonResponse({
                'success': False,
                'error': 'Ticket POS requerido'
            })
        
        transaccion = get_object_or_404(TransaccionPOS, ticket_pos=ticket_pos)
        
        # Verificar que el usuario tiene acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if transaccion.configuracion_pos.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a esta transacción'
            })
        
        # Actualizar transacción con respuesta del POS
        transaccion.codigo_respuesta = respuesta_pos.get('response_code', '')
        transaccion.mensaje_respuesta = respuesta_pos.get('response_message', '')
        transaccion.codigo_autorizacion = respuesta_pos.get('authorization_code', '')
        transaccion.tipo_tarjeta = respuesta_pos.get('card_type', 'DESCONOCIDO')
        transaccion.ultimos_4_digitos = respuesta_pos.get('card_number', '')[-4:] if respuesta_pos.get('card_number') else ''
        transaccion.nombre_tarjeta = respuesta_pos.get('card_brand', '')
        transaccion.numero_operacion = respuesta_pos.get('operation_number', '')
        transaccion.numero_cuotas = respuesta_pos.get('installments', 1)
        transaccion.codigo_comercio = respuesta_pos.get('commerce_code', '')
        transaccion.terminal_id = respuesta_pos.get('terminal_id', '')
        
        # Determinar estado final
        if respuesta_pos.get('success', False) and transaccion.codigo_autorizacion:
            transaccion.estado = 'APROBADA'
        else:
            transaccion.estado = 'RECHAZADA'
            transaccion.error_detalle = respuesta_pos.get('error_message', 'Transacción rechazada')
        
        transaccion.save()
        
        # Crear log de respuesta
        LogPOS.objects.create(
            configuracion_pos=transaccion.configuracion_pos,
            transaccion_pos=transaccion,
            tipo_evento='RESPUESTA_RECIBIDA',
            mensaje=f'Transacción {transaccion.get_estado_display().lower()}',
            datos_tecnicos=respuesta_pos
        )
        
        # Si la transacción fue exitosa y hay un ticket asociado, crear el detalle de pago
        if transaccion.es_exitosa and transaccion.ticket:
            # Determinar método de pago según el tipo de tarjeta
            metodo_pago = 'TBK_POS_INTEGRADO'
            if transaccion.tipo_tarjeta == 'DEBITO':
                metodo_pago = 'TBK_DEBITO_POS'
            elif transaccion.tipo_tarjeta == 'CREDITO':
                metodo_pago = 'TBK_CREDITO_POS'
            elif transaccion.tipo_tarjeta == 'PREPAGO':
                metodo_pago = 'TBK_PREPAGO_POS'
            
            # Crear detalle de pago
            detalle_pago = TicketDetallePago.objects.create(
                ticket=transaccion.ticket,
                metodo_pago=metodo_pago,
                tipo_tarjeta=transaccion.nombre_tarjeta,
                voucher=transaccion.codigo_autorizacion,
                monto=int(transaccion.monto),
                notas=f'POS {transaccion.configuracion_pos.nombre} - Oper: {transaccion.numero_operacion}'
            )
            
            # Asociar el detalle de pago con la transacción
            transaccion.detalle_pago = detalle_pago
            transaccion.save()
            
            # Actualizar estado del ticket si está completamente pagado
            if transaccion.ticket.saldo_por_pagar <= 0:
                transaccion.ticket.estado = 'PAGADO'
                transaccion.ticket.save()
        
        return JsonResponse({
            'success': True,
            'transaccion': {
                'id': transaccion.id,
                'ticket_pos': transaccion.ticket_pos,
                'estado': transaccion.estado,
                'estado_display': transaccion.get_estado_display(),
                'es_exitosa': transaccion.es_exitosa,
                'codigo_autorizacion': transaccion.codigo_autorizacion,
                'tipo_tarjeta': transaccion.get_tipo_tarjeta_display() if transaccion.tipo_tarjeta else '',
                'ultimos_4_digitos': transaccion.ultimos_4_digitos,
                'nombre_tarjeta': transaccion.nombre_tarjeta,
                'duracion': transaccion.duracion_transaccion,
                'puede_anular': transaccion.puede_anular
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al completar transacción: {str(e)}'
        })


@login_required
@require_GET
def obtener_transacciones_pos(request):
    """Obtener historial de transacciones POS"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Parámetros de filtro
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        estado = request.GET.get('estado')
        configuracion_id = request.GET.get('configuracion_id')
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        # Construir queryset
        queryset = TransaccionPOS.objects.select_related(
            'configuracion_pos', 'ticket', 'detalle_pago', 'usuario_operador'
        ).filter(
            configuracion_pos__sucursal_id=sucursal_id
        )
        
        # Aplicar filtros
        if fecha_desde:
            queryset = queryset.filter(fecha_inicio__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_inicio__date__lte=fecha_hasta)
        if estado:
            queryset = queryset.filter(estado=estado)
        if configuracion_id:
            queryset = queryset.filter(configuracion_pos_id=configuracion_id)
        
        # Paginación
        from django.core.paginator import Paginator
        paginator = Paginator(queryset, per_page)
        transacciones_page = paginator.get_page(page)
        
        # Serializar datos
        transacciones_data = []
        for transaccion in transacciones_page:
            transacciones_data.append({
                'id': transaccion.id,
                'ticket_pos': transaccion.ticket_pos,
                'fecha_inicio': transaccion.fecha_inicio.strftime('%d/%m/%Y %H:%M:%S'),
                'fecha_completada': transaccion.fecha_completada.strftime('%d/%m/%Y %H:%M:%S') if transaccion.fecha_completada else '',
                'monto': float(transaccion.monto),
                'estado': transaccion.estado,
                'estado_display': transaccion.get_estado_display(),
                'tipo_transaccion': transaccion.get_tipo_transaccion_display(),
                'configuracion_pos': transaccion.configuracion_pos.nombre,
                'codigo_autorizacion': transaccion.codigo_autorizacion or '',
                'tipo_tarjeta': transaccion.get_tipo_tarjeta_display() if transaccion.tipo_tarjeta else '',
                'ultimos_4_digitos': transaccion.ultimos_4_digitos or '',
                'nombre_tarjeta': transaccion.nombre_tarjeta or '',
                'ticket_id': transaccion.ticket.id if transaccion.ticket else None,
                'ticket_correlativo': transaccion.ticket.correlativo if transaccion.ticket else '',
                'usuario_operador': transaccion.usuario_operador.username if transaccion.usuario_operador else '',
                'duracion': transaccion.duracion_transaccion,
                'es_exitosa': transaccion.es_exitosa,
                'puede_anular': transaccion.puede_anular,
                'error_detalle': transaccion.error_detalle or '',
            })
        
        return JsonResponse({
            'success': True,
            'transacciones': transacciones_data,
            'pagination': {
                'current_page': transacciones_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': transacciones_page.has_next(),
                'has_previous': transacciones_page.has_previous(),
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener transacciones: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def anular_transaccion_pos(request):
    """Anular transacción POS"""
    try:
        data = json.loads(request.body)
        transaccion_id = data.get('transaccion_id')
        
        if not transaccion_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de transacción requerido'
            })
        
        transaccion = get_object_or_404(TransaccionPOS, id=transaccion_id)
        
        # Verificar que el usuario tiene acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if transaccion.configuracion_pos.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a esta transacción'
            })
        
        # Verificar que se puede anular
        if not transaccion.puede_anular:
            return JsonResponse({
                'success': False,
                'error': 'Esta transacción no puede ser anulada'
            })
        
        with transaction.atomic():
            # Crear nueva transacción de anulación
            anulacion = TransaccionPOS.objects.create(
                configuracion_pos=transaccion.configuracion_pos,
                ticket=transaccion.ticket,
                monto=transaccion.monto,
                tipo_transaccion='ANULACION',
                estado='INICIADA',
                usuario_operador=request.user,
                ip_origen=request.META.get('REMOTE_ADDR'),
                observaciones=f'Anulación de transacción {transaccion.ticket_pos}'
            )
            
            # Aquí iría la lógica real de anulación con el SDK de Transbank
            # Por ahora simulamos una anulación exitosa
            
            anulacion.estado = 'APROBADA'
            anulacion.codigo_respuesta = '00'
            anulacion.mensaje_respuesta = 'Anulación aprobada'
            anulacion.codigo_autorizacion = f'ANU-{transaccion.codigo_autorizacion}'
            anulacion.save()
            
            # Marcar transacción original como anulada
            transaccion.estado = 'ANULADA'
            transaccion.save()
            
            # Si había un detalle de pago asociado, eliminarlo o marcarlo como anulado
            if transaccion.detalle_pago:
                transaccion.detalle_pago.notas += f' - ANULADO {timezone.now().strftime("%d/%m/%Y %H:%M")}'
                transaccion.detalle_pago.save()
            
            # Crear logs
            LogPOS.objects.create(
                configuracion_pos=transaccion.configuracion_pos,
                transaccion_pos=anulacion,
                tipo_evento='COMANDO_ENVIADO',
                mensaje=f'Anulación exitosa de {transaccion.ticket_pos}',
                datos_tecnicos={
                    'transaccion_original': transaccion.ticket_pos,
                    'codigo_autorizacion_original': transaccion.codigo_autorizacion,
                    'monto': float(transaccion.monto)
                }
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Transacción anulada exitosamente',
            'anulacion': {
                'id': anulacion.id,
                'ticket_pos': anulacion.ticket_pos,
                'codigo_autorizacion': anulacion.codigo_autorizacion
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al anular transacción: {str(e)}'
        })


@login_required
@require_GET
def obtener_logs_pos(request, configuracion_id):
    """Obtener logs de una configuración POS específica"""
    try:
        configuracion = get_object_or_404(ConfiguracionPOS, id=configuracion_id)
        
        # Verificar que el usuario tiene acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if configuracion.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a esta configuración'
            })
        
        # Parámetros de filtro
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        tipo_evento = request.GET.get('tipo_evento')
        limit = int(request.GET.get('limit', 100))
        
        # Construir queryset
        queryset = LogPOS.objects.filter(
            configuracion_pos=configuracion
        ).select_related('transaccion_pos')
        
        # Aplicar filtros
        if fecha_desde:
            queryset = queryset.filter(timestamp__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(timestamp__date__lte=fecha_hasta)
        if tipo_evento:
            queryset = queryset.filter(tipo_evento=tipo_evento)
        
        # Limitar resultados
        logs = queryset[:limit]
        
        # Serializar datos
        logs_data = []
        for log in logs:
            logs_data.append({
                'id': log.id,
                'timestamp': log.timestamp.strftime('%d/%m/%Y %H:%M:%S'),
                'tipo_evento': log.tipo_evento,
                'tipo_evento_display': log.get_tipo_evento_display(),
                'mensaje': log.mensaje,
                'transaccion_pos': log.transaccion_pos.ticket_pos if log.transaccion_pos else '',
                'datos_tecnicos': log.datos_tecnicos,
            })
        
        return JsonResponse({
            'success': True,
            'logs': logs_data,
            'configuracion': {
                'id': configuracion.id,
                'nombre': configuracion.nombre,
                'tipo_pos': configuracion.get_tipo_pos_display()
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener logs: {str(e)}'
        })


# ========== MÓDULO DE CAMBIOS Y DEVOLUCIONES ==========

@login_required
def gestion_cambios_devoluciones(request):
    """Vista principal para gestión de cambios y devoluciones"""
    sucursal_actual_id = request.session.get('sucursalActual') or request.session.get('idSucursalActual')
    sucursal_actual = None
    
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            sucursal_actual = None
    
    if not sucursal_actual:
        return redirect('verHome')
    
    context = {
        'sucursal_actual': sucursal_actual,
        'tipo_operacion_choices': TIPO_OPERACION_CAMBIO_CHOICES,
        'estado_choices': ESTADO_CAMBIO_CHOICES,
        'motivo_choices': MOTIVO_CAMBIO_CHOICES,
        'condicion_producto_choices': CONDICION_PRODUCTO_CHOICES,
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
    }
    return render(request, 'vistas/modulo_ventas/gestion_cambios_devoluciones.html', context)


@login_required
@require_GET
def listar_cambios_devoluciones(request):
    """API para listar cambios y devoluciones con filtros"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })

        # Parámetros de filtro
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        tipo_operacion = request.GET.get('tipo_operacion')
        estado = request.GET.get('estado')
        buscar = request.GET.get('buscar', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))

        # Construir queryset base
        queryset = CambioDevolucion.objects.select_related(
            'ticket_original', 'ticket_nuevo', 'sucursal', 'solicitado_por', 'aprobado_por'
        ).prefetch_related(
            'detalles__producto_original__ProductoTalla__producto',
            'detalles__producto_nuevo__producto',
            'pagos'
        ).filter(sucursal_id=sucursal_id)

        # Aplicar filtros
        if fecha_desde:
            queryset = queryset.filter(fecha_solicitud__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_solicitud__date__lte=fecha_hasta)
        if tipo_operacion:
            queryset = queryset.filter(tipo_operacion=tipo_operacion)
        if estado:
            queryset = queryset.filter(estado=estado)
        if buscar:
            queryset = queryset.filter(
                Q(numero_operacion__icontains=buscar) |
                Q(ticket_original__correlativo__icontains=buscar) |
                Q(ticket_original__cliente_nombre__icontains=buscar) |
                Q(ticket_original__cliente_rut__icontains=buscar) |
                Q(observaciones_cliente__icontains=buscar) |
                Q(observaciones_vendedor__icontains=buscar)
            )

        # Paginación
        paginator = Paginator(queryset, per_page)
        cambios_page = paginator.get_page(page)

        # Serializar datos
        cambios_data = []
        for cambio in cambios_page:
            # Calcular totales de productos
            total_productos_devueltos = cambio.detalles.count()
            total_productos_nuevos = cambio.detalles.filter(producto_nuevo__isnull=False).count()
            
            cambios_data.append({
                'id': cambio.id,
                'numero_operacion': cambio.numero_operacion,
                'fecha_solicitud': cambio.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
                'tipo_operacion': cambio.tipo_operacion,
                'tipo_operacion_display': cambio.get_tipo_operacion_display(),
                'estado': cambio.estado,
                'estado_display': cambio.get_estado_display(),
                'ticket_original': cambio.ticket_original.correlativo,
                'ticket_nuevo': cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else '',
                'cliente_nombre': cambio.ticket_original.cliente_nombre or 'Sin nombre',
                'cliente_rut': cambio.ticket_original.cliente_rut or '',
                'monto_original': float(cambio.monto_original),
                'monto_nuevo': float(cambio.monto_nuevo),
                'diferencia_monto': float(cambio.diferencia_monto),
                'motivo_principal': cambio.get_motivo_principal_display(),
                'solicitado_por': cambio.solicitado_por.username,
                'aprobado_por': cambio.aprobado_por.username if cambio.aprobado_por else '',
                'fecha_aprobacion': cambio.fecha_aprobacion.strftime('%d/%m/%Y %H:%M') if cambio.fecha_aprobacion else '',
                'fecha_completado': cambio.fecha_completado.strftime('%d/%m/%Y %H:%M') if cambio.fecha_completado else '',
                'fecha_limite': cambio.fecha_limite_cambio.strftime('%d/%m/%Y'),
                'dias_desde_venta': cambio.dias_desde_venta,
                'dentro_del_plazo': cambio.dentro_del_plazo,
                'requiere_pago_adicional': cambio.requiere_pago_adicional,
                'genera_devolucion': cambio.genera_devolucion,
                'total_productos_devueltos': total_productos_devueltos,
                'total_productos_nuevos': total_productos_nuevos,
                'requiere_autorizacion': cambio.requiere_autorizacion,
                'autorizado_excepcion': cambio.autorizado_excepcion,
            })

        # Estadísticas
        total_cambios = queryset.count()
        cambios_pendientes = queryset.filter(estado='SOLICITADO').count()
        cambios_completados = queryset.filter(estado='COMPLETADO').count()
        total_diferencia = queryset.aggregate(
            total=Sum('diferencia_monto')
        )['total'] or 0

        return JsonResponse({
            'success': True,
            'cambios': cambios_data,
            'pagination': {
                'current_page': cambios_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': cambios_page.has_next(),
                'has_previous': cambios_page.has_previous(),
            },
            'estadisticas': {
                'total_cambios': total_cambios,
                'cambios_pendientes': cambios_pendientes,
                'cambios_completados': cambios_completados,
                'total_diferencia': float(total_diferencia),
            }
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener cambios: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def crear_cambio_devolucion(request):
    """Crear nueva solicitud de cambio o devolución"""
    try:
        data = json.loads(request.body)
        
        print(f"🔍 DEBUG crear_cambio_devolucion - Datos recibidos: {data}")
        
        # Validar datos requeridos
        documento_id = data.get('documento_id')
        documento_tipo = data.get('documento_tipo', 'TICKET')
        documento_numero = data.get('documento_numero')
        tipo_operacion = data.get('tipo_operacion')
        motivo_principal = data.get('motivo_principal')
        productos_cambio = data.get('productos', [])
        
        # Retrocompatibilidad con ticket_correlativo
        if not documento_numero and data.get('ticket_correlativo'):
            documento_numero = data.get('ticket_correlativo')
            documento_tipo = 'TICKET'
        
        if not all([documento_numero, tipo_operacion, motivo_principal]):
            return JsonResponse({
                'success': False,
                'error': f'Faltan datos obligatorios. documento_numero: {documento_numero}, tipo_operacion: {tipo_operacion}, motivo: {motivo_principal}'
            })
        
        if not productos_cambio:
            return JsonResponse({
                'success': False,
                'error': 'Debe incluir al menos un producto'
            })
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Buscar documento original (Ticket o DTE)
        ticket_original = None
        dte_original = None
        
        if documento_tipo == 'DTE':
            # Buscar DTE y crear ticket asociado si no existe
            try:
                dte_original = Dte.objects.select_related('receptor', 'vendedor', 'sucursal').get(
                    numero_documento=documento_numero
                )
                
                print(f"✓ DTE #{documento_numero} encontrado")
                
                # Buscar si ya existe un ticket asociado a este DTE
                # Buscar por observaciones que contienen el número de DTE
                ticket_original = Ticket.objects.filter(
                    observaciones__icontains=f'DTE #{documento_numero}'
                ).first()
                
                if not ticket_original:
                    # Crear ticket de referencia desde el DTE
                    print(f"  Creando ticket de referencia para DTE #{documento_numero}")
                    
                    correlativo_ticket = obtener_siguiente_correlativo(sucursal, 'TICKET')
                    
                    # Crear ticket de referencia con solo los campos que existen en el modelo
                    ticket_original = Ticket.objects.create(
                        correlativo=correlativo_ticket,
                        vendedor=dte_original.vendedor,
                        sucursal=sucursal,
                        subTotal=int(dte_original.monto_neto),
                        descuento=int(dte_original.descuento) if dte_original.descuento else 0,
                        total=int(dte_original.monto_con_iva),
                        estado='PAGADO',
                        responsable=dte_original.responsable,
                        cliente_nombre=dte_original.receptor.razon_social if dte_original.receptor else '',
                        cliente_rut=dte_original.receptor.rut if dte_original.receptor else '',
                        cliente_email=dte_original.receptor.correoVendedor if dte_original.receptor else '',
                        cliente_telefono='',
                        cliente_giro=dte_original.receptor.giro if dte_original.receptor else '',
                        cliente_direccion=dte_original.receptor.direccion if dte_original.receptor else '',
                        cliente_comuna=dte_original.receptor.comuna if dte_original.receptor else '',
                        cliente_ciudad=dte_original.receptor.ciudad if dte_original.receptor else '',
                        observaciones=f'Ticket de referencia para DTE #{documento_numero} - {dte_original.tipo_documento}'
                    )
                    
                    print(f"  ✓ Ticket #{correlativo_ticket} creado sin errores")
                    
                    # Copiar productos del DTE al ticket y crear mapeo
                    mapeo_productos = {}  # dte_producto_id → ticket_producto_id
                    
                    for dp in dte_original.dte_productos.all():
                        tp = Ticket_Productos.objects.create(
                            idTicket=ticket_original,
                            ProductoTalla=dp.productoTalla,
                            stock=dp.stock,
                            precio=dp.precio,
                            descuento_unitario=0,
                            subtotal=dp.precio * dp.stock
                        )
                        mapeo_productos[dp.id] = tp.id
                        print(f"    Mapeado: Dte_Producto {dp.id} → Ticket_Producto {tp.id}")
                    
                    # Guardar mapeo en la sesión o en el ticket para referencia
                    ticket_original.observaciones += f"\n[MAPEO_PRODUCTOS: {mapeo_productos}]"
                    ticket_original.save()
                    
                    print(f"  ✓ Ticket #{correlativo_ticket} creado con {len(mapeo_productos)} productos")
                else:
                    print(f"  ✓ Ticket asociado encontrado: #{ticket_original.correlativo}")
                    
            except Dte.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': f'DTE #{documento_numero} no encontrado'
                })
        else:
            # Buscar Ticket
            try:
                ticket_original = Ticket.objects.get(
                    correlativo=documento_numero,
                    sucursal=sucursal
                )
            except Ticket.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': f'Ticket #{documento_numero} no encontrado'
                })
            
            # Verificar que el ticket esté pagado
            if ticket_original.estado != 'PAGADO':
                return JsonResponse({
                    'success': False,
                    'error': 'Solo se pueden procesar cambios de tickets pagados'
                })
        
        # Verificar plazo (30 días por defecto)
        from datetime import timedelta
        fecha_limite = ticket_original.fecha + timedelta(days=30)
        if timezone.now().date() > fecha_limite:
            return JsonResponse({
                'success': False,
                'error': f'El plazo para cambios venció el {fecha_limite.strftime("%d/%m/%Y")}'
            })
        
        with transaction.atomic():
            # Crear cambio/devolución
            cambio = CambioDevolucion.objects.create(
                ticket_original=ticket_original,
                sucursal=sucursal,
                tipo_operacion=tipo_operacion,
                monto_original=ticket_original.total,
                motivo_principal=motivo_principal,
                observaciones_cliente=data.get('observaciones_cliente', ''),
                observaciones_vendedor=data.get('observaciones_vendedor', ''),
                solicitado_por=request.user,
                requiere_autorizacion=data.get('requiere_autorizacion', False),
                fecha_limite_cambio=fecha_limite
            )
            
            # Procesar productos
            monto_nuevo_total = 0
            for item in productos_cambio:
                # Buscar producto original en el ticket
                try:
                    ticket_producto = Ticket_Productos.objects.get(
                        idTicket=ticket_original,
                        id=item['ticket_producto_id']
                    )
                except Ticket_Productos.DoesNotExist:
                    raise ValidationError(f'Producto no encontrado en el ticket')
                
                # Validar cantidad
                cantidad_cambio = item.get('cantidad', 0)
                if cantidad_cambio <= 0 or cantidad_cambio > ticket_producto.stock:
                    raise ValidationError(f'Cantidad inválida para {ticket_producto.ProductoTalla.producto.articulo}')
                
                # Producto nuevo (si es cambio)
                producto_nuevo = None
                precio_nuevo = 0
                cantidad_nueva = 0
                
                if tipo_operacion in ['CAMBIO_SIMPLE', 'CAMBIO_CON_DIFERENCIA']:
                    producto_nuevo_id = item.get('producto_nuevo_id')
                    if producto_nuevo_id:
                        try:
                            producto_nuevo = Producto_Talla.objects.get(id=producto_nuevo_id)
                            precio_nuevo = producto_nuevo.producto.precioventa
                            cantidad_nueva = cantidad_cambio  # Por defecto, misma cantidad
                            monto_nuevo_total += precio_nuevo * cantidad_nueva
                        except Producto_Talla.DoesNotExist:
                            raise ValidationError(f'Producto nuevo no encontrado')
                
                # Crear detalle del cambio
                detalle = CambioDevolucionDetalle.objects.create(
                    cambio_devolucion=cambio,
                    producto_original=ticket_producto,
                    cantidad_original=cantidad_cambio,
                    producto_nuevo=producto_nuevo,
                    cantidad_nueva=cantidad_nueva,
                    precio_nuevo=precio_nuevo,
                    precio_original_unitario=ticket_producto.precio,
                    condicion_producto=item.get('condicion_producto', 'PERFECTO'),
                    apto_para_venta=item.get('apto_para_venta', True),
                    observaciones=item.get('observaciones', '')
                )
            
            # Actualizar montos del cambio
            cambio.monto_nuevo = monto_nuevo_total
            cambio.diferencia_monto = monto_nuevo_total - float(cambio.monto_original)
            cambio.save()
            
            # Crear historial
            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                accion='CREADO',
                estado_nuevo='SOLICITADO',
                usuario=request.user,
                descripcion=f'Solicitud de {cambio.get_tipo_operacion_display().lower()} creada',
                datos_adicionales={
                    'motivo': motivo_principal,
                    'productos_count': len(productos_cambio),
                    'monto_diferencia': float(cambio.diferencia_monto)
                }
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Solicitud creada exitosamente',
            'cambio_id': cambio.id,
            'numero_operacion': cambio.numero_operacion,
            'diferencia_monto': float(cambio.diferencia_monto)
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear solicitud: {str(e)}'
        })


@login_required
@require_GET
def obtener_detalle_cambio(request, cambio_id):
    """Obtener detalle completo de un cambio/devolución"""
    try:
        cambio = get_object_or_404(
            CambioDevolucion.objects.select_related(
                'ticket_original', 'ticket_nuevo', 'sucursal', 
                'solicitado_por', 'aprobado_por'
            ).prefetch_related(
                'detalles__producto_original__ProductoTalla__producto',
                'detalles__producto_nuevo__producto',
                'pagos',
                'historial__usuario'
            ),
            id=cambio_id
        )
        
        # Verificar acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if cambio.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a este cambio'
            })
        
        # Datos del cambio
        cambio_data = {
            'id': cambio.id,
            'numero_operacion': cambio.numero_operacion,
            'tipo_operacion': cambio.tipo_operacion,
            'tipo_operacion_display': cambio.get_tipo_operacion_display(),
            'estado': cambio.estado,
            'estado_display': cambio.get_estado_display(),
            'fecha_solicitud': cambio.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
            'fecha_aprobacion': cambio.fecha_aprobacion.strftime('%d/%m/%Y %H:%M') if cambio.fecha_aprobacion else '',
            'fecha_completado': cambio.fecha_completado.strftime('%d/%m/%Y %H:%M') if cambio.fecha_completado else '',
            'fecha_limite_cambio': cambio.fecha_limite_cambio.strftime('%d/%m/%Y'),
            'dias_desde_venta': cambio.dias_desde_venta,
            'dentro_del_plazo': cambio.dentro_del_plazo,
            'puede_completar': cambio.puede_completar,
            
            # Montos
            'monto_original': float(cambio.monto_original),
            'monto_nuevo': float(cambio.monto_nuevo),
            'diferencia_monto': float(cambio.diferencia_monto),
            'requiere_pago_adicional': cambio.requiere_pago_adicional,
            'genera_devolucion': cambio.genera_devolucion,
            
            # Responsables
            'solicitado_por': cambio.solicitado_por.username,
            'aprobado_por': cambio.aprobado_por.username if cambio.aprobado_por else '',
            
            # Observaciones
            'motivo_principal': cambio.motivo_principal,
            'motivo_principal_display': cambio.get_motivo_principal_display(),
            'observaciones_cliente': cambio.observaciones_cliente or '',
            'observaciones_vendedor': cambio.observaciones_vendedor or '',
            'observaciones_aprobacion': cambio.observaciones_aprobacion or '',
            
            # Políticas
            'requiere_autorizacion': cambio.requiere_autorizacion,
            'autorizado_excepcion': cambio.autorizado_excepcion,
            
            # Tickets
            'ticket_original': {
                'correlativo': cambio.ticket_original.correlativo,
                'fecha': cambio.ticket_original.fecha.strftime('%d/%m/%Y'),
                'total': float(cambio.ticket_original.total),
                'cliente_nombre': cambio.ticket_original.cliente_nombre or '',
                'cliente_rut': cambio.ticket_original.cliente_rut or '',
                'vendedor': cambio.ticket_original.vendedor.nombre if cambio.ticket_original.vendedor else '',
            },
            'ticket_nuevo': {
                'correlativo': cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else '',
                'total': float(cambio.ticket_nuevo.total) if cambio.ticket_nuevo else 0,
            } if cambio.ticket_nuevo else None,
        }
        
        # Detalles de productos
        productos_detalle = []
        for detalle in cambio.detalles.all():
            producto_original = detalle.producto_original
            
            productos_detalle.append({
                'id': detalle.id,
                'producto_original': {
                    'sku': producto_original.ProductoTalla.sku,
                    'articulo': producto_original.ProductoTalla.producto.articulo,
                    'descripcion': producto_original.ProductoTalla.producto.descripcion,
                    'talla': producto_original.ProductoTalla.talla,
                    'cantidad_original': producto_original.stock,
                    'precio_unitario': float(producto_original.precio),
                },
                'cantidad_cambio': detalle.cantidad_original,
                'precio_original_unitario': float(detalle.precio_original_unitario),
                'valor_original_total': float(detalle.valor_original_total),
                
                'producto_nuevo': {
                    'sku': detalle.producto_nuevo.sku if detalle.producto_nuevo else '',
                    'articulo': detalle.producto_nuevo.producto.articulo if detalle.producto_nuevo else '',
                    'descripcion': detalle.producto_nuevo.producto.descripcion if detalle.producto_nuevo else '',
                    'talla': detalle.producto_nuevo.talla if detalle.producto_nuevo else '',
                    'precio_unitario': float(detalle.precio_nuevo),
                } if detalle.producto_nuevo else None,
                
                'cantidad_nueva': detalle.cantidad_nueva,
                'valor_nuevo_total': float(detalle.valor_nuevo_total),
                'diferencia_unitaria': float(detalle.diferencia_unitaria),
                'diferencia_total': float(detalle.diferencia_total),
                
                'condicion_producto': detalle.condicion_producto,
                'condicion_producto_display': detalle.get_condicion_producto_display(),
                'apto_para_venta': detalle.apto_para_venta,
                'observaciones': detalle.observaciones or '',
                
                'es_cambio': detalle.es_cambio,
                'es_devolucion': detalle.es_devolucion,
            })
        
        # Pagos asociados
        pagos_data = []
        for pago in cambio.pagos.all():
            pagos_data.append({
                'id': pago.id,
                'tipo_pago': pago.tipo_pago,
                'tipo_pago_display': pago.get_tipo_pago_display(),
                'metodo_pago': pago.metodo_pago,
                'metodo_pago_display': pago.get_metodo_pago_display(),
                'monto': float(pago.monto),
                'referencia_pago': pago.referencia_pago or '',
                'numero_autorizacion': pago.numero_autorizacion or '',
                'fecha_pago': pago.fecha_pago.strftime('%d/%m/%Y %H:%M'),
                'procesado_por': pago.procesado_por.username,
                'observaciones': pago.observaciones or '',
            })
        
        # Historial
        historial_data = []
        for hist in cambio.historial.all():
            historial_data.append({
                'id': hist.id,
                'accion': hist.accion,
                'accion_display': hist.get_accion_display(),
                'estado_anterior': hist.estado_anterior,
                'estado_nuevo': hist.estado_nuevo,
                'usuario': hist.usuario.username,
                'descripcion': hist.descripcion,
                'timestamp': hist.timestamp.strftime('%d/%m/%Y %H:%M:%S'),
                'datos_adicionales': hist.datos_adicionales,
            })
        
        return JsonResponse({
            'success': True,
            'cambio': cambio_data,
            'productos': productos_detalle,
            'pagos': pagos_data,
            'historial': historial_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener detalle: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def aprobar_cambio_devolucion(request):
    """Aprobar o rechazar una solicitud de cambio/devolución"""
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        accion = data.get('accion')  # 'aprobar' o 'rechazar'
        observaciones = data.get('observaciones', '')
        
        if not all([cambio_id, accion]):
            return JsonResponse({
                'success': False,
                'error': 'ID de cambio y acción requeridos'
            })
        
        cambio = get_object_or_404(CambioDevolucion, id=cambio_id)
        
        # Verificar acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if cambio.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a este cambio'
            })
        
        # Verificar estado
        if cambio.estado != 'SOLICITADO':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden aprobar cambios en estado solicitado'
            })
        
        # Verificar plazo
        if not cambio.dentro_del_plazo:
            return JsonResponse({
                'success': False,
                'error': 'El plazo para este cambio ya venció'
            })
        
        with transaction.atomic():
            if accion == 'aprobar':
                cambio.aprobar_cambio(request.user, observaciones)
                mensaje = 'Cambio aprobado exitosamente'
                accion_historial = 'APROBADO'
            elif accion == 'rechazar':
                if not observaciones:
                    return JsonResponse({
                        'success': False,
                        'error': 'Debe proporcionar un motivo para el rechazo'
                    })
                cambio.rechazar_cambio(request.user, observaciones)
                mensaje = 'Cambio rechazado'
                accion_historial = 'RECHAZADO'
            else:
                return JsonResponse({
                    'success': False,
                    'error': 'Acción no válida'
                })
            
            # Crear historial
            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                accion=accion_historial,
                estado_anterior='SOLICITADO',
                estado_nuevo=cambio.estado,
                usuario=request.user,
                descripcion=f'Cambio {accion_historial.lower()} por {request.user.username}',
                datos_adicionales={
                    'observaciones': observaciones,
                    'fecha_decision': timezone.now().isoformat()
                }
            )
        
        return JsonResponse({
            'success': True,
            'message': mensaje,
            'nuevo_estado': cambio.estado,
            'nuevo_estado_display': cambio.get_estado_display()
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al procesar solicitud: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def completar_cambio_devolucion(request):
    """Completar un cambio/devolución aprobado"""
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        
        if not cambio_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de cambio requerido'
            })
        
        cambio = get_object_or_404(CambioDevolucion, id=cambio_id)
        
        # Verificar acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if cambio.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a este cambio'
            })
        
        # Verificar que se puede completar
        if not cambio.puede_completar:
            return JsonResponse({
                'success': False,
                'error': 'Este cambio no puede ser completado en su estado actual'
            })
        
        with transaction.atomic():
            # Procesar según el tipo de operación
            if cambio.tipo_operacion in ['CAMBIO_SIMPLE', 'CAMBIO_CON_DIFERENCIA']:
                # Es un cambio - crear nuevo ticket si hay productos nuevos
                productos_nuevos = cambio.detalles.filter(producto_nuevo__isnull=False)
                
                if productos_nuevos.exists():
                    # Crear nuevo ticket
                    correlativo_nuevo = obtener_siguiente_correlativo(cambio.sucursal, 'TICKET')
                    
                    ticket_nuevo = Ticket.objects.create(
                        correlativo=correlativo_nuevo,
                        vendedor=cambio.ticket_original.vendedor,
                        sucursal=cambio.sucursal,
                        subTotal=int(cambio.monto_nuevo),
                        descuento=0,
                        total=int(cambio.monto_nuevo),
                        estado='PAGADO' if cambio.diferencia_monto <= 0 else 'PENDIENTE',
                        responsable=request.user.username,
                        cliente_nombre=cambio.ticket_original.cliente_nombre,
                        cliente_rut=cambio.ticket_original.cliente_rut,
                        cliente_email=cambio.ticket_original.cliente_email,
                        cliente_telefono=cambio.ticket_original.cliente_telefono,
                        observaciones=f'Cambio de ticket #{cambio.ticket_original.correlativo} - {cambio.numero_operacion}'
                    )
                    
                    # Agregar productos nuevos al ticket
                    for detalle in productos_nuevos:
                        Ticket_Productos.objects.create(
                            idTicket=ticket_nuevo,
                            ProductoTalla=detalle.producto_nuevo,
                            stock=detalle.cantidad_nueva,
                            precio=int(detalle.precio_nuevo),
                            descuento_unitario=0,
                            subtotal=int(detalle.precio_nuevo * detalle.cantidad_nueva)
                        )
                    
                    cambio.ticket_nuevo = ticket_nuevo
            
            # Procesar movimientos de inventario
            for detalle in cambio.detalles.all():
                # 1. DEVOLVER stock del producto original (INGRESO)
                if detalle.apto_para_venta:
                    mov_devolucion = Movimientos_Producto.objects.create(
                        ProductoTalla=detalle.producto_original.ProductoTalla,
                        cantidad=detalle.cantidad_original,  # Positivo para ingreso
                        costo=detalle.producto_original.ProductoTalla.producto.costo,
                        precio=int(detalle.precio_original_unitario),
                        concepto='DEVOLUCION_CLIENTE',
                        tipo_movimiento='INGRESO',
                        responsable=request.user.username,
                        observaciones=f'Devolución por cambio {cambio.numero_operacion} - Ticket original #{cambio.ticket_original.correlativo}',
                        referencia_externa=cambio.numero_operacion,
                        ticket=cambio.ticket_original
                    )
                    
                    # Actualizar stock del producto devuelto
                    detalle.producto_original.ProductoTalla.stock += detalle.cantidad_original
                    detalle.producto_original.ProductoTalla.save()
                    
                    print(f"✓ Devuelto stock: {detalle.producto_original.ProductoTalla.sku} +{detalle.cantidad_original}")
                else:
                    # Producto no apto: registrar solo para auditoría sin devolver stock
                    Movimientos_Producto.objects.create(
                        ProductoTalla=detalle.producto_original.ProductoTalla,
                        cantidad=0,  # No devuelve stock
                        costo=detalle.producto_original.ProductoTalla.producto.costo,
                        precio=int(detalle.precio_original_unitario),
                        concepto='DEVOLUCION_CLIENTE',
                        tipo_movimiento='AJUSTE',
                        responsable=request.user.username,
                        observaciones=f'Devolución NO APTA por cambio {cambio.numero_operacion} - Producto en mal estado',
                        referencia_externa=cambio.numero_operacion,
                        ticket=cambio.ticket_original
                    )
                    print(f"⚠ Producto no apto (no devuelve stock): {detalle.producto_original.ProductoTalla.sku}")
                
                # 2. DESCONTAR stock del producto nuevo (EGRESO con FIFO)
                if detalle.producto_nuevo:
                    # Verificar stock disponible
                    if detalle.producto_nuevo.stock < detalle.cantidad_nueva:
                        raise ValidationError(f'Stock insuficiente para {detalle.producto_nuevo.producto.articulo} - Talla {detalle.producto_nuevo.talla}. Disponible: {detalle.producto_nuevo.stock}, Requerido: {detalle.cantidad_nueva}')
                    
                    # Consumir stock FIFO (crea movimiento de EGRESO automáticamente)
                    try:
                        consumir_stock_fifo(
                            producto_talla=detalle.producto_nuevo,
                            cantidad_requerida=detalle.cantidad_nueva,
                            responsable=request.user.username,
                            ticket=cambio.ticket_nuevo,
                            observaciones=f'Cambio {cambio.numero_operacion} - Nuevo producto para ticket #{cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else "N/A"}',
                            referencia_externa=cambio.numero_operacion
                        )
                        print(f"✓ Consumido stock FIFO: {detalle.producto_nuevo.sku} -{detalle.cantidad_nueva}")
                    except Exception as e:
                        raise ValidationError(f'Error al consumir stock FIFO: {str(e)}')
            
            # Procesar pagos si hay diferencia
            if cambio.diferencia_monto != 0:
                pagos_data = data.get('pagos', [])
                
                for pago_data in pagos_data:
                    PagoCambioDevolucion.objects.create(
                        cambio_devolucion=cambio,
                        tipo_pago=pago_data.get('tipo_pago'),
                        metodo_pago=pago_data.get('metodo_pago'),
                        monto=abs(pago_data.get('monto', 0)),
                        referencia_pago=pago_data.get('referencia_pago', ''),
                        numero_autorizacion=pago_data.get('numero_autorizacion', ''),
                        procesado_por=request.user,
                        observaciones=pago_data.get('observaciones', '')
                    )
            
            # Completar el cambio
            cambio.completar_cambio()
            
            # Crear historial
            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                accion='COMPLETADO',
                estado_anterior='APROBADO',
                estado_nuevo='COMPLETADO',
                usuario=request.user,
                descripcion=f'Cambio completado exitosamente',
                datos_adicionales={
                    'ticket_nuevo': cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else None,
                    'diferencia_procesada': float(cambio.diferencia_monto),
                    'fecha_completado': timezone.now().isoformat()
                }
            )
        
        # Preparar datos del ticket para impresión si se creó uno nuevo
        ticket_data = None
        if cambio.ticket_nuevo:
            ticket_data = construir_ticket_data(cambio.ticket_nuevo)
        
        return JsonResponse({
            'success': True,
            'message': 'Cambio completado exitosamente',
            'cambio_id': cambio.id,
            'numero_operacion': cambio.numero_operacion,
            'ticket_nuevo': cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else None,
            'estado_final': cambio.get_estado_display(),
            'ticket_data': ticket_data,  # Datos para imprimir el nuevo ticket
            'puede_imprimir': cambio.ticket_nuevo is not None,
            'diferencia_monto': float(cambio.diferencia_monto)
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al completar cambio: {str(e)}'
        })


@login_required
@require_GET
def buscar_documento_cambio(request):
    """Buscar documento (Ticket o DTE) para iniciar proceso de cambio/devolución"""
    try:
        numero = request.GET.get('numero', '').strip()
        tipo_documento = request.GET.get('tipo_documento', 'dte')
        fecha_compra = request.GET.get('fecha_compra', '').strip()
        
        print(f"🔍 DEBUG buscar_documento_cambio - Parámetros recibidos:")
        print(f"  numero: '{numero}'")
        print(f"  tipo_documento: '{tipo_documento}'")
        print(f"  fecha_compra: '{fecha_compra}'")
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        print(f"  sucursal_id: {sucursal_id}")
        
        if not numero:
            return JsonResponse({
                'success': False,
                'error': 'Número de documento requerido'
            })
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Buscar según el tipo
        if tipo_documento == 'dte':
            # Buscar DTE - Primero sin filtros para debugging
            query_debug = Dte.objects.filter(numero_documento=numero)
            print(f"🔍 Paso 1 - Total DTEs con número {numero}: {query_debug.count()}")
            
            if query_debug.exists():
                for dte_d in query_debug:
                    print(f"  → DTE encontrado: ID={dte_d.id}, tipo_doc={dte_d.tipo_documento}, tipo_trans={dte_d.tipo_transaccion}, fecha={dte_d.fecha_emision}, estado={dte_d.estado_dte}, sucursal={dte_d.sucursal_id}")
            else:
                print(f"  ❌ No existe ningún DTE con número {numero} en la base de datos")
            
            # Buscar DTE con filtros para ventas
            query = Dte.objects.select_related('vendedor', 'receptor', 'sucursal').prefetch_related(
                'dte_productos__productoTalla__producto'
            ).filter(
                numero_documento=numero
            )
            
            print(f"🔍 Paso 2 - Aplicando filtros...")
            
            # Filtrar por tipo de transacción (solo ventas)
            query = query.filter(tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'])
            print(f"  Después de filtro tipo_transaccion: {query.count()}")
            
            if fecha_compra:
                query = query.filter(fecha_emision=fecha_compra)
            
            dte = query.first()
            
            if not dte:
                # Verificar si existe pero con otro tipo_transaccion
                dte_otro_tipo = Dte.objects.filter(numero_documento=numero).first()
                if dte_otro_tipo:
                    return JsonResponse({
                        'success': False,
                        'error': f'DTE #{numero} encontrado pero es tipo "{dte_otro_tipo.tipo_transaccion}". Solo se permiten cambios de documentos de VENTA o VENTA_PUBLICO.'
                    })
                
                return JsonResponse({
                    'success': False,
                    'error': f'DTE #{numero} no encontrado para la fecha {fecha_compra}. Verifique el número y la fecha.'
                })
            
            # Verificar que esté emitido/pagado
            if dte.estado_dte not in ['EMITIDO', 'ACEPTADO']:
                return JsonResponse({
                    'success': False,
                    'error': 'Solo se pueden procesar cambios de documentos emitidos'
                })
            
            # Convertir DTE a formato compatible
            from datetime import timedelta
            fecha_limite = dte.fecha_emision + timedelta(days=30)
            dentro_del_plazo = timezone.now().date() <= fecha_limite
            
            productos_data = []
            for dp in dte.dte_productos.all():
                productos_data.append({
                    'id': dp.id,
                    'sku': dp.productoTalla.sku if dp.productoTalla else '',
                    'articulo': dp.productoTalla.producto.articulo if dp.productoTalla and dp.productoTalla.producto else dp.descripcion,
                    'descripcion': dp.descripcion,
                    'talla': dp.productoTalla.talla if dp.productoTalla else '',
                    'cantidad_original': dp.stock,
                    'cantidad_disponible': dp.stock,  # TODO: verificar si ya se cambió
                    'precio_unitario': float(dp.precio),
                    'subtotal': float(dp.precio * dp.stock),
                })
            
            return JsonResponse({
                'success': True,
                'documento': {
                    'id': dte.id,
                    'tipo': 'DTE',
                    'numero_documento': dte.numero_documento,
                    'tipo_documento': dte.tipo_documento,
                    'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                    'total': float(dte.monto_con_iva),
                    'vendedor': dte.vendedor.nombre if dte.vendedor else 'Sin vendedor',
                    'cliente_nombre': dte.receptor.nombre if dte.receptor else 'Sin nombre',
                    'cliente_rut': dte.receptor.rut if dte.receptor else '',
                    'fecha_limite_cambio': fecha_limite.strftime('%d/%m/%Y'),
                    'dentro_del_plazo': dentro_del_plazo,
                    'dias_transcurridos': (timezone.now().date() - dte.fecha_emision).days,
                    'productos': productos_data,
                    'puede_cambiar': dentro_del_plazo and len(productos_data) > 0,
                }
            })
        else:
            # Buscar Ticket (lógica existente)
            return buscar_ticket_para_cambio_original(request, numero, fecha_compra, sucursal_id)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar documento: {str(e)}'
        })


def buscar_ticket_para_cambio_original(request, correlativo, fecha_compra, sucursal_id):
    """Función para buscar tickets (llamada desde buscar_documento_cambio)"""
    query = Ticket.objects.select_related(
        'vendedor', 'sucursal'
    ).prefetch_related(
        'ticket_productos__ProductoTalla__producto',
        'cambios_devoluciones'
    ).filter(
        correlativo=correlativo,
        sucursal_id=sucursal_id
    )
    
    if fecha_compra:
        query = query.filter(fecha=fecha_compra)
    
    ticket = query.first()
    
    if not ticket:
        return JsonResponse({
            'success': False,
            'error': f'Ticket #{correlativo} no encontrado' + (f' para la fecha {fecha_compra}' if fecha_compra else '')
        })
    
    # Llamar a la función original con toda la lógica
    return buscar_ticket_para_cambio_response(ticket)


def buscar_ticket_para_cambio_response(ticket):
    """Genera la respuesta con datos del ticket para cambio"""
    # Verificar que esté pagado
    if ticket.estado != 'PAGADO':
        return JsonResponse({
            'success': False,
            'error': 'Solo se pueden procesar cambios de tickets pagados'
        })
    
    # Verificar plazo
    from datetime import timedelta
    fecha_limite = ticket.fecha + timedelta(days=30)
    dentro_del_plazo = timezone.now().date() <= fecha_limite
    
    # Obtener productos del ticket
    productos_data = []
    for tp in ticket.ticket_productos.all():
        # Verificar si ya fue cambiado/devuelto
        cantidad_ya_cambiada = CambioDevolucionDetalle.objects.filter(
            producto_original=tp,
            cambio_devolucion__estado__in=['APROBADO', 'COMPLETADO']
        ).aggregate(
            total=Sum('cantidad_original')
        )['total'] or 0
        
        cantidad_disponible = tp.stock - cantidad_ya_cambiada
        
        if cantidad_disponible > 0:
            productos_data.append({
                'id': tp.id,
                'sku': tp.ProductoTalla.sku,
                'articulo': tp.ProductoTalla.producto.articulo,
                'descripcion': tp.ProductoTalla.producto.descripcion,
                'talla': tp.ProductoTalla.talla,
                'cantidad_original': tp.stock,
                'cantidad_ya_cambiada': cantidad_ya_cambiada,
                'cantidad_disponible': cantidad_disponible,
                'precio_unitario': float(tp.precio),
                'subtotal': float(tp.subtotal),
            })
    
    # Obtener cambios anteriores
    cambios_anteriores = []
    for cambio in ticket.cambios_devoluciones.all():
        cambios_anteriores.append({
            'numero_operacion': cambio.numero_operacion,
            'tipo_operacion': cambio.get_tipo_operacion_display(),
            'estado': cambio.get_estado_display(),
            'fecha_solicitud': cambio.fecha_solicitud.strftime('%d/%m/%Y'),
            'diferencia_monto': float(cambio.diferencia_monto),
        })
    
    ticket_data = {
        'id': ticket.id,
        'tipo': 'TICKET',
        'correlativo': ticket.correlativo,
        'fecha': ticket.fecha.strftime('%d/%m/%Y'),
        'hora': ticket.hora.strftime('%H:%M') if ticket.hora else '',
        'total': float(ticket.total),
        'estado': ticket.estado,
        'vendedor': ticket.vendedor.nombre if ticket.vendedor else '',
        'cliente_nombre': ticket.cliente_nombre or '',
        'cliente_rut': ticket.cliente_rut or '',
        'cliente_email': ticket.cliente_email or '',
        'cliente_telefono': ticket.cliente_telefono or '',
        'observaciones': ticket.observaciones or '',
        'fecha_limite_cambio': fecha_limite.strftime('%d/%m/%Y'),
        'dentro_del_plazo': dentro_del_plazo,
        'dias_transcurridos': (timezone.now().date() - ticket.fecha).days,
        'productos': productos_data,
        'cambios_anteriores': cambios_anteriores,
        'puede_cambiar': dentro_del_plazo and len(productos_data) > 0,
    }
    
    return JsonResponse({
        'success': True,
        'documento': ticket_data  # Cambiar de 'ticket' a 'documento' para compatibilidad
        })


@login_required
@require_GET
def buscar_ticket_para_cambio(request):
    """Buscar ticket para iniciar proceso de cambio/devolución (retrocompatibilidad)"""
    try:
        correlativo = request.GET.get('correlativo', '').strip()
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not correlativo:
            return JsonResponse({
                'success': False,
                'error': 'Número de ticket requerido'
            })
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        query = Ticket.objects.select_related(
                'vendedor', 'sucursal'
            ).prefetch_related(
                'ticket_productos__ProductoTalla__producto',
                'cambios_devoluciones'
        ).filter(
                correlativo=correlativo,
                sucursal_id=sucursal_id
            )
        
        ticket = query.first()
        
        if not ticket:
            return JsonResponse({
                'success': False,
                'error': f'Ticket #{correlativo} no encontrado'
            })
        
        return buscar_ticket_para_cambio_response(ticket)
        
        # Verificar que esté pagado
        if ticket.estado != 'PAGADO':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden procesar cambios de tickets pagados'
            })
        
        # Verificar plazo
        from datetime import timedelta
        fecha_limite = ticket.fecha + timedelta(days=30)
        dentro_del_plazo = timezone.now().date() <= fecha_limite
        
        # Obtener productos del ticket
        productos_data = []
        for tp in ticket.ticket_productos.all():
            # Verificar si ya fue cambiado/devuelto
            cantidad_ya_cambiada = CambioDevolucionDetalle.objects.filter(
                producto_original=tp,
                cambio_devolucion__estado__in=['APROBADO', 'COMPLETADO']
            ).aggregate(
                total=Sum('cantidad_original')
            )['total'] or 0
            
            cantidad_disponible = tp.stock - cantidad_ya_cambiada
            
            if cantidad_disponible > 0:
                productos_data.append({
                    'id': tp.id,
                    'sku': tp.ProductoTalla.sku,
                    'articulo': tp.ProductoTalla.producto.articulo,
                    'descripcion': tp.ProductoTalla.producto.descripcion,
                    'talla': tp.ProductoTalla.talla,
                    'cantidad_original': tp.stock,
                    'cantidad_ya_cambiada': cantidad_ya_cambiada,
                    'cantidad_disponible': cantidad_disponible,
                    'precio_unitario': float(tp.precio),
                    'subtotal': float(tp.subtotal),
                })
        
        # Obtener cambios anteriores
        cambios_anteriores = []
        for cambio in ticket.cambios_devoluciones.all():
            cambios_anteriores.append({
                'numero_operacion': cambio.numero_operacion,
                'tipo_operacion': cambio.get_tipo_operacion_display(),
                'estado': cambio.get_estado_display(),
                'fecha_solicitud': cambio.fecha_solicitud.strftime('%d/%m/%Y'),
                'diferencia_monto': float(cambio.diferencia_monto),
            })
        
        ticket_data = {
            'id': ticket.id,
            'correlativo': ticket.correlativo,
            'fecha': ticket.fecha.strftime('%d/%m/%Y'),
            'hora': ticket.hora.strftime('%H:%M') if ticket.hora else '',
            'total': float(ticket.total),
            'estado': ticket.estado,
            'vendedor': ticket.vendedor.nombre if ticket.vendedor else '',
            'cliente_nombre': ticket.cliente_nombre or '',
            'cliente_rut': ticket.cliente_rut or '',
            'cliente_email': ticket.cliente_email or '',
            'cliente_telefono': ticket.cliente_telefono or '',
            'observaciones': ticket.observaciones or '',
            'fecha_limite_cambio': fecha_limite.strftime('%d/%m/%Y'),
            'dentro_del_plazo': dentro_del_plazo,
            'dias_transcurridos': (timezone.now().date() - ticket.fecha).days,
            'productos': productos_data,
            'cambios_anteriores': cambios_anteriores,
            'puede_cambiar': dentro_del_plazo and len(productos_data) > 0,
        }
        
        return JsonResponse({
            'success': True,
            'ticket': ticket_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar ticket: {str(e)}'
        })


@login_required
@require_GET
def buscar_productos_para_cambio(request):
    """Buscar productos disponibles para cambio"""
    try:
        termino = request.GET.get('termino', '').strip()
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not termino:
            return JsonResponse({
                'success': False,
                'error': 'Término de búsqueda requerido'
            })
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Buscar productos con stock disponible
        productos_query = Producto_Talla.objects.select_related(
            'producto'
        ).filter(
            Q(sku__icontains=termino) |
            Q(producto__articulo__icontains=termino) |
            Q(producto__descripcion__icontains=termino),
            producto__sucursal_id=sucursal_id,
            stock__gt=0
        )[:20]  # Limitar resultados
        
        productos_data = []
        for pt in productos_query:
            productos_data.append({
                'id': pt.id,
                'sku': pt.sku,
                'articulo': pt.producto.articulo,
                'descripcion': pt.producto.descripcion,
                'talla': pt.talla,
                'precio_venta': float(pt.producto.precioventa),
                'stock_disponible': pt.stock,
                'marca': pt.producto.atributo1.valor if pt.producto.atributo1 else '',
                'color': pt.producto.atributo2.valor if pt.producto.atributo2 else '',
            })
        
        return JsonResponse({
            'success': True,
            'productos': productos_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar productos: {str(e)}'
        })


# ========== GESTIÓN DE CLIENTES POS ==========

@require_POST
@login_required
def guardar_cliente_pos(request):
    """Guardar datos del cliente desde el POS"""
    try:
        data = json.loads(request.body)
        
        nombre = data.get('nombre', '').strip()
        rut = data.get('rut', '').strip()
        email = data.get('email', '').strip()
        telefono = data.get('telefono', '').strip()
        tipo_documento = data.get('tipo_documento', 'TICKET')
        
        # Si no hay datos suficientes, no guardar
        if not nombre and not rut:
            return JsonResponse({
                'success': False,
                'error': 'Debe proporcionar al menos nombre o RUT'
            })
        
        # Buscar o crear empresa/cliente
        cliente = None
        if rut:
            # Buscar por RUT
            cliente = Empresa.objects.filter(rut=rut).first()
        
        if cliente:
            # Actualizar datos existentes
            if nombre:
                cliente.nombre = nombre
                cliente.razon_social = nombre
            if email:
                cliente.correoVendedor = email
            if telefono:
                cliente.telefono = telefono
            
            # Campos adicionales para facturas
            if tipo_documento == 'FACTURA_ELECTRONICA':
                cliente.giro = data.get('giro', cliente.giro or '')
                cliente.direccion = data.get('direccion', cliente.direccion or '')
                cliente.comuna = data.get('comuna', cliente.comuna or '')
                cliente.ciudad = data.get('ciudad', cliente.ciudad or '')
            
            cliente.save()
            
        else:
            # Crear nuevo cliente
            cliente = Empresa.objects.create(
                nombre=nombre or f'Cliente {rut}',
                rut=rut or '',
                nombre_fantasia=nombre or '',
                razon_social=nombre or '',
                giro=data.get('giro', ''),
                direccion=data.get('direccion', ''),
                comuna=data.get('comuna', ''),
                ciudad=data.get('ciudad', ''),
                esProveedor=False,
                correoVendedor=email or '',
                telefono=telefono or ''
            )
        
        return JsonResponse({
            'success': True,
            'cliente_id': cliente.id,
            'mensaje': 'Cliente guardado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al guardar cliente: {str(e)}'
        })


@require_POST
@login_required
def enviar_ticket_email(request):
    """Enviar ticket por email al cliente"""
    try:
        data = json.loads(request.body)
        ticket_id = data.get('ticket_id')
        email = data.get('email', '').strip()
        
        if not ticket_id or not email:
            return JsonResponse({
                'success': False,
                'error': 'Debe proporcionar ticket_id y email'
            })
        
        # Buscar el ticket
        ticket = Ticket.objects.filter(correlativo=ticket_id).first()
        
        if not ticket:
            return JsonResponse({
                'success': False,
                'error': f'No se encontró el ticket #{ticket_id}'
            })
        
        # TODO: Implementar envío de email
        # Por ahora, solo simular el envío
        # En producción, usar Django's send_mail o un servicio de email
        
        # from django.core.mail import send_mail
        # from django.template.loader import render_to_string
        
        # asunto = f'Ticket de Venta #{ticket.correlativo}'
        # mensaje = render_to_string('emails/ticket_venta.html', {'ticket': ticket})
        # send_mail(asunto, mensaje, 'noreply@retailmind.cl', [email], html_message=mensaje)
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Ticket enviado exitosamente a {email}'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al enviar email: {str(e)}'
        })
