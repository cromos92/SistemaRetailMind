"""
Módulo de Ventas - RetailMind
Contiene todas las vistas relacionadas con ventas, tickets, vendedores y POS
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model

User = get_user_model()
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
from .views import obtener_siguiente_correlativo, obtener_correlativo_existente, consumir_stock_fifo

# Importar servicios de Transbank
from .services.transbank_sdk_service import (
    run_transbank_operation, test_pos_connection, 
    execute_pos_sale, get_available_ports, cancel_pos_sale
)

from .models import (
    Ticket, Ticket_Productos, TicketDetallePago, TicketReferencia, Vendedor, Producto, Producto_Talla,
    Sucursal, EmpresaUser, Empresa, Movimientos_Producto, LoteProducto, Dte, Dte_Productos, Dte_Detalle_Pago,
    Correlativo, ESTADO_TICKET_CHOICES, METODO_PAGO_TICKET_CHOICES, TIPO_DOCUMENTO_CHOICES,
    ArqueoCaja, ESTADO_ARQUEO_CHOICES, RESULTADO_REVISION_CHOICES, GrupoDeposito, DepositoBancario,
    ObservacionArqueo, LogAccionCaja, log_accion_caja,
    ConfiguracionPOS, TransaccionPOS, LogPOS,
    TIPO_POS_CHOICES, ESTADO_TRANSACCION_POS_CHOICES, TIPO_TARJETA_CHOICES,
    # Modelos de Cambios y Devoluciones
    CambioDevolucion, CambioDevolucionDetalle, PagoCambioDevolucion, HistorialCambioDevolucion,
    TIPO_OPERACION_CAMBIO_CHOICES, ESTADO_CAMBIO_CHOICES, MOTIVO_CAMBIO_CHOICES, CONDICION_PRODUCTO_CHOICES,
    METODO_DEVOLUCION_NC_CHOICES,
)


# ========== GESTIÓN DE VENDEDORES ==========

@login_required
def gestion_vendedores(request):
    """Vista principal para gestión de vendedores"""
    # Obtener todas las sucursales disponibles
    sucursales = Sucursal.objects.all().order_by('alias')
    
    context = {
        'sucursales': sucursales
    }
    
    return render(request, 'vistas/modulo_administracion/gestion_vendedores.html', context)


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
                Q(codigo_vendedor__icontains=search) |
                Q(correo__icontains=search) |
                Q(rut__icontains=search)
            )
        
        # Ordenar
        queryset = queryset.order_by('nombre')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        vendedores_page = paginator.get_page(page)
        
        # Serializar datos
        vendedores_data = []
        for vendedor in vendedores_page:
            sucursales_list = list(vendedor.sucursales.all().values('id', 'alias'))
            vendedores_data.append({
                'id': vendedor.id,
                'codigo_vendedor': vendedor.codigo_vendedor,
                'nombre': vendedor.nombre,
                'rut': vendedor.rut,
                'correo': vendedor.correo,
                'comision': float(vendedor.comision) if vendedor.comision else 0,
                'fecha_nacimiento': vendedor.fecha_nacimiento.strftime('%Y-%m-%d') if vendedor.fecha_nacimiento else '',
                'sucursales': sucursales_list,
                'sucursales_nombres': ', '.join([s['alias'] for s in sucursales_list]) if sucursales_list else 'Sin asignar',
                'activo': True,  # Por defecto, ya que el modelo no tiene este campo
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
            campos_requeridos = ['codigo_vendedor', 'nombre']
            for campo in campos_requeridos:
                if not data.get(campo):
                    return JsonResponse({
                        'success': False,
                        'error': f'El campo {campo} es requerido'
                    }, status=400)
            
            # Verificar que el código no exista
            if Vendedor.objects.filter(codigo_vendedor=data['codigo_vendedor']).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'Ya existe un vendedor con ese código'
                }, status=400)
            
            # Crear vendedor
            vendedor = Vendedor.objects.create(
                codigo_vendedor=data['codigo_vendedor'],
                nombre=data['nombre'],
                rut=data.get('rut', ''),
                correo=data.get('correo', ''),
                comision=data.get('comision', 0),
                fecha_nacimiento=data.get('fecha_nacimiento') or None
            )
            
            # Asignar sucursales
            if 'sucursales' in data and data['sucursales']:
                sucursales_ids = data['sucursales'] if isinstance(data['sucursales'], list) else [data['sucursales']]
                vendedor.sucursales.set(sucursales_ids)
            
            sucursales_list = list(vendedor.sucursales.all().values('id', 'alias'))
            
            return JsonResponse({
                'success': True,
                'message': 'Vendedor creado exitosamente',
                'vendedor': {
                    'id': vendedor.id,
                    'codigo_vendedor': vendedor.codigo_vendedor,
                    'nombre': vendedor.nombre,
                    'rut': vendedor.rut,
                    'correo': vendedor.correo,
                    'comision': float(vendedor.comision),
                    'fecha_nacimiento': vendedor.fecha_nacimiento.strftime('%Y-%m-%d') if vendedor.fecha_nacimiento else '',
                    'sucursales': sucursales_list,
                    'sucursales_nombres': ', '.join([s['alias'] for s in sucursales_list]) if sucursales_list else 'Sin asignar',
                    'activo': True
                }
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
        print("📝 guardar_cliente_pos - datos recibidos:", data)
        vendedor_id = data.get('id')
        
        if not vendedor_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de vendedor requerido'
            }, status=400)
        
        vendedor = get_object_or_404(Vendedor, id=vendedor_id)
        
        # Validar campos requeridos
        campos_requeridos = ['codigo_vendedor', 'nombre']
        for campo in campos_requeridos:
            if not data.get(campo):
                return JsonResponse({
                    'success': False,
                    'error': f'El campo {campo} es requerido'
                }, status=400)
        
        # Verificar que el código no exista en otro vendedor
        if Vendedor.objects.filter(codigo_vendedor=data['codigo_vendedor']).exclude(id=vendedor_id).exists():
            return JsonResponse({
                'success': False,
                'error': 'Ya existe otro vendedor con ese código'
            }, status=400)
        
        # Actualizar vendedor
        vendedor.codigo_vendedor = data['codigo_vendedor']
        vendedor.nombre = data['nombre']
        vendedor.rut = data.get('rut', '')
        vendedor.correo = data.get('correo', '')
        vendedor.comision = data.get('comision', 0)
        vendedor.fecha_nacimiento = data.get('fecha_nacimiento') or None
        vendedor.save()
        
        # Actualizar sucursales
        if 'sucursales' in data:
            sucursales_ids = data['sucursales'] if isinstance(data['sucursales'], list) else ([data['sucursales']] if data['sucursales'] else [])
            vendedor.sucursales.set(sucursales_ids)
        
        sucursales_list = list(vendedor.sucursales.all().values('id', 'alias'))
        
        return JsonResponse({
            'success': True,
            'message': 'Vendedor actualizado exitosamente',
            'vendedor': {
                'id': vendedor.id,
                'codigo_vendedor': vendedor.codigo_vendedor,
                'nombre': vendedor.nombre,
                'rut': vendedor.rut,
                'correo': vendedor.correo,
                'comision': float(vendedor.comision),
                'fecha_nacimiento': vendedor.fecha_nacimiento.strftime('%Y-%m-%d') if vendedor.fecha_nacimiento else '',
                'sucursales': sucursales_list,
                'sucursales_nombres': ', '.join([s['alias'] for s in sucursales_list]) if sucursales_list else 'Sin asignar',
                'activo': True
            }
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
    # Obtener sucursal actual del usuario (intentar ambas variables de sesión)
    sucursal_actual_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
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
    
    # Validar que existe correlativo para tickets
    tiene_correlativo = False
    correlativo_info = None
    
    # DEBUG: Imprimir información de sesión
    print(f"🔍 DEBUG ticket_venta:")
    print(f"  - idSucursalActual: {request.session.get('idSucursalActual')}")
    print(f"  - sucursalActual: {request.session.get('sucursalActual')}")
    print(f"  - sucursal_actual_id final: {sucursal_actual_id}")
    print(f"  - sucursal_actual objeto: {sucursal_actual}")
    
    if sucursal_actual:
        print(f"  - Buscando correlativo TICKET para sucursal: {sucursal_actual.alias} (ID: {sucursal_actual.id})")
        try:
            correlativo = Correlativo.objects.get(
                sucursal=sucursal_actual,
                tipo_dte='TICKET'
            )
            print(f"  - ✅ Correlativo encontrado: ID={correlativo.id}, inicio={correlativo.inicio}, termino={correlativo.termino}")
            print(f"  - puede_emitir()={correlativo.puede_emitir()}")
            
            tiene_correlativo = correlativo.puede_emitir()
            correlativo_info = {
                'disponibles': correlativo.disponibles,
                'inicio': correlativo.inicio,
                'termino': correlativo.termino,
                'estado': correlativo.estado
            }
        except Correlativo.DoesNotExist:
            print(f"  - ❌ Correlativo NO encontrado para TICKET")
            tiene_correlativo = False
            correlativo_info = None
    else:
        print(f"  - ⚠️ No hay sucursal_actual definida")
    
    # Obtener vendedores de la sucursal actual (solo activos)
    if sucursal_actual:
        # Obtener vendedores activos asignados a esta sucursal
        vendedores = Vendedor.objects.filter(
            sucursales=sucursal_actual,
            activo=True
        ).order_by('nombre')
        
        # Si no hay vendedores asignados a la sucursal, buscar por empresa
        if not vendedores.exists() and sucursal_actual.empresa:
            vendedores = Vendedor.objects.filter(
                empresa=sucursal_actual.empresa,
                activo=True
            ).order_by('nombre')
    else:
        # Si no hay sucursal seleccionada, mostrar solo vendedores activos
        vendedores = Vendedor.objects.filter(activo=True).order_by('nombre')
    
    context = {
        'sucursal_actual': sucursal_actual,
        'empresa_actual_nombre': empresa_actual_nombre,
        'vendedores': vendedores,
        'sucursales_disponibles': sucursales_disponibles,
        'necesita_seleccionar_sucursal': not sucursal_actual,
        'tiene_correlativo': tiene_correlativo,
        'correlativo_info': correlativo_info,
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
            'producto',
            'producto__categoria',
            'producto__atributo1',
            'producto__atributo2',
            'producto__atributo3',
            'producto__atributo4'
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
                'articulo': producto_talla.producto.articulo,
                'descripcion': producto_talla.producto.descripcion or '',
                'talla': producto_talla.talla if producto_talla.talla else 'Sin talla',
                'precio_venta': float(producto_talla.producto.precioventa),
                'stock': stock_actual,
                'marca': producto_talla.producto.atributo1.valor if producto_talla.producto.atributo1 else '',
                'color': producto_talla.producto.atributo2.valor if producto_talla.producto.atributo2 else '',
                'material': producto_talla.producto.atributo3.valor if producto_talla.producto.atributo3 else '',
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


@require_GET
@login_required
def buscar_productos_pos_avanzado(request):
    """
    Búsqueda avanzada de productos para POS
    Permite buscar por SKU, marca, artículo, color, etc.
    Solo retorna productos con stock disponible en la sucursal actual
    """
    sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    
    if not sucursal_id:
        return JsonResponse({
            'success': False,
            'error': 'No hay sucursal activa'
        })
    
    # Parámetros de búsqueda
    search_term = request.GET.get('search', '').strip()
    
    if not search_term or len(search_term) < 2:
        return JsonResponse({
            'success': False,
            'error': 'Ingrese al menos 2 caracteres para buscar'
        })
    
    try:
        # Buscar productos en la sucursal actual
        productos_query = Producto_Talla.objects.filter(
            producto__sucursal_id=sucursal_id
        ).select_related(
            'producto',
            'producto__categoria',
            'producto__atributo1',
            'producto__atributo2',
            'producto__atributo3',
            'producto__atributo4'
        )

        # Separar el término en palabras para búsqueda multi-palabra
        palabras = search_term.split()

        if len(palabras) > 1:
            # Búsqueda multi-palabra: cada palabra debe coincidir en algún campo
            for palabra in palabras:
                palabra = palabra.strip()
                if not palabra:
                    continue
                productos_query = productos_query.filter(
                    Q(sku__icontains=palabra) |
                    Q(producto__articulo__icontains=palabra) |
                    Q(producto__atributo1__valor__icontains=palabra) |
                    Q(producto__atributo2__valor__icontains=palabra) |
                    Q(producto__atributo3__valor__icontains=palabra) |
                    Q(producto__atributo4__valor__icontains=palabra) |
                    Q(producto__categoria__nombre__icontains=palabra) |
                    Q(talla__icontains=palabra)
                )
        else:
            # Búsqueda de un solo término
            productos_query = productos_query.filter(
                Q(sku__icontains=search_term) |
                Q(producto__articulo__icontains=search_term) |
                Q(producto__atributo1__valor__icontains=search_term) |
                Q(producto__atributo2__valor__icontains=search_term) |
                Q(producto__atributo3__valor__icontains=search_term) |
                Q(producto__atributo4__valor__icontains=search_term) |
                Q(producto__categoria__nombre__icontains=search_term) |
                Q(talla__icontains=search_term)
            )

        # Filtrar solo productos con stock > 0
        # Iterar hasta conseguir 30 con stock, revisando hasta 200 candidatos
        productos_con_stock = []
        for pt in productos_query[:200]:
            stock_actual = pt.stock_sucursal(sucursal_id)
            if stock_actual > 0:
                productos_con_stock.append({
                    'id': pt.id,
                    'sku': pt.sku,
                    'articulo': pt.producto.articulo,
                    'descripcion': pt.producto.descripcion or '',
                    'marca': pt.producto.atributo1.valor if pt.producto.atributo1 else '',
                    'color': pt.producto.atributo2.valor if pt.producto.atributo2 else '',
                    'material': pt.producto.atributo3.valor if pt.producto.atributo3 else '',
                    'talla': pt.talla if pt.talla else 'Sin talla',
                    'stock': stock_actual,
                    'precio_venta': float(pt.producto.precioventa),
                    'categoria': pt.producto.categoria.nombre if pt.producto.categoria else ''
                })
                if len(productos_con_stock) >= 30:
                    break

        return JsonResponse({
            'success': True,
            'productos': productos_con_stock,
            'total': len(productos_con_stock)
        })
        
    except Exception as e:
        print(f"Error en búsqueda avanzada: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar productos: {str(e)}'
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
                    'nombre': pt.producto.articulo,
                    'talla': pt.talla if pt.talla else 'Sin talla',
                    'precio_venta': float(pt.precio_venta),
                    'stock': stock,
                    'marca': pt.producto.atributo1.valor if pt.producto.atributo1 else '',
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
            
            # Obtener límite de descuento del rol del usuario
            # Usamos Max para obtener el valor más alto guardado (todos deberían ser iguales)
            from .models import PermisoRol
            from django.db.models import Max
            limite_descuento_rol = 0
            if request.user.is_authenticated:
                rol_usuario = getattr(request.user, 'rol', None)
                if rol_usuario:
                    resultado = PermisoRol.objects.filter(rol=rol_usuario).aggregate(
                        max_limite=Max('limite_descuento_porcentaje')
                    )
                    if resultado['max_limite'] is not None:
                        limite_descuento_rol = float(resultado['max_limite'])
            
            # Validar descuentos por producto contra el límite del rol
            for item in productos:
                descuento_unitario = item.get('descuento_unitario', 0)
                precio_unitario = item.get('precio_unitario', 0)
                
                if descuento_unitario > 0 and precio_unitario > 0:
                    porcentaje_descuento = (descuento_unitario / precio_unitario) * 100
                    
                    # Validar que no exceda el límite del rol
                    if porcentaje_descuento > limite_descuento_rol and limite_descuento_rol > 0:
                        return JsonResponse({
                            'success': False,
                            'error': f'El descuento aplicado ({porcentaje_descuento:.1f}%) excede el límite permitido para tu rol ({limite_descuento_rol}%). Producto: {item.get("articulo", "")}'
                        })
                    
                    # Si el límite es 0, no permitir ningún descuento
                    if limite_descuento_rol == 0:
                        return JsonResponse({
                            'success': False,
                            'error': 'No tienes permisos para aplicar descuentos. Contacta al administrador.'
                        })
            
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
                
                # Validar cantidad
                cantidad = item.get('cantidad', 0)
                
                # Validar que la cantidad sea un número entero positivo
                if not isinstance(cantidad, int) or cantidad < 1:
                    raise ValidationError(
                        f'Cantidad inválida para {producto_talla.sku}: debe ser un número entero positivo mayor a 0'
                    )
                
                # Verificar stock
                stock_actual = producto_talla.stock_sucursal(sucursal_id)
                if stock_actual < cantidad:
                    raise ValidationError(
                        f'Stock insuficiente para {producto_talla.sku}. Solicitado: {cantidad}, Disponible: {stock_actual}'
                    )
                
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
            'correlativo': correlativo,
            'ticket_data': construir_ticket_data(ticket)
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
        
        # Validar que existe correlativo antes de crear el ticket
        try:
            correlativo_obj = Correlativo.objects.get(
                sucursal=sucursal,
                tipo_dte='TICKET'
            )
            if not correlativo_obj.puede_emitir():
                return JsonResponse({
                    'success': False, 
                    'error': f'No hay correlativos disponibles para TICKET en {sucursal.alias}. Por favor, configure un nuevo rango de correlativos.'
                })
        except Correlativo.DoesNotExist:
            return JsonResponse({
                'success': False, 
                'error': f'No existe correlativo configurado para TICKET en {sucursal.alias}. Por favor, configure un correlativo antes de crear tickets.'
            })
        
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
            
            # Validar cantidad
            cantidad = item.get('cantidad', 0)
            
            # Validar que la cantidad sea un número entero positivo
            if not isinstance(cantidad, int) or cantidad < 1:
                raise ValidationError(
                    f'Cantidad inválida para {producto_talla.sku}: debe ser un número entero positivo mayor a 0'
                )
            
            # Verificar stock disponible
            stock_actual = producto_talla.stock_sucursal(sucursal_id)
            if stock_actual < cantidad:
                raise ValidationError(
                    f'Stock insuficiente para {producto_talla.sku}. Solicitado: {cantidad}, Disponible: {stock_actual}'
                )
            
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

def _get_qz_config(sucursal_id):
    """Retorna el dict qz_config para la sucursal dada (o defaults si no hay)."""
    if not sucursal_id:
        return {'habilitado': False, 'nombre_impresora': 'EPSON TM-T20II'}
    try:
        suc = Sucursal.objects.get(id=sucursal_id)
        return {
            'habilitado': getattr(suc, 'usar_qz_tray', False),
            'nombre_impresora': getattr(suc, 'nombre_impresora_termica', 'EPSON TM-T20II') or 'EPSON TM-T20II',
        }
    except Sucursal.DoesNotExist:
        return {'habilitado': False, 'nombre_impresora': 'EPSON TM-T20II'}


@login_required
def pos_dashboard(request):
    """Vista principal del dashboard POS"""
    # Obtener choices para los selects
    sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
    
    # Obtener configuración POS guardada (para auto-conectar)
    config_pos = None
    if sucursal_id:
        config_pos = ConfiguracionPOS.objects.filter(
            sucursal_id=sucursal_id,
            tipo_pos='SDK_SERIAL',
            activo=True
        ).first()
    
    # Obtener límite de descuento del rol del usuario
    # Usamos Max para obtener el valor más alto guardado (todos deberían ser iguales)
    limite_descuento_rol = 0
    if request.user.is_authenticated:
        from .models import PermisoRol
        from django.db.models import Max
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario:
            resultado = PermisoRol.objects.filter(rol=rol_usuario).aggregate(
                max_limite=Max('limite_descuento_porcentaje')
            )
            if resultado['max_limite'] is not None:
                limite_descuento_rol = float(resultado['max_limite'])
    
    # Verificar si el usuario es administrador
    es_admin = getattr(request.user, 'rol', '') in ['administrador', 'administracion']

    context = {
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
        'estado_ticket_choices': ESTADO_TICKET_CHOICES,
        'config_pos': config_pos,
        'limite_descuento_rol': limite_descuento_rol,
        'es_admin': es_admin,
        'qz_config': _get_qz_config(sucursal_id),
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
        
        # Tipos de documento para ventas al público (electrónicos y papel)
        tipos_documento = [
            'BOLETA_ELECTRONICA', 
            'BOLETA_PAPEL',
            'FACTURA_ELECTRONICA'
        ]
        
        correlativos_info = {}
        
        for tipo in tipos_documento:
            # Mapear nombres para la base de datos
            tipo_db = tipo
            if tipo == 'BOLETA_ELECTRONICA':
                tipo_db = 'BOLETA ELECTRONICA'
            elif tipo == 'BOLETA_PAPEL':
                tipo_db = 'BOLETA PAPEL'
            elif tipo == 'FACTURA_ELECTRONICA':
                tipo_db = 'FACTURA ELECTRONICA'
            
            try:
                correlativo = obtener_correlativo_existente(sucursal, tipo_db)
                if not correlativo:
                    raise Correlativo.DoesNotExist()
                
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
                    correlativo = obtener_correlativo_existente(sucursal, tipo_db)
                    if not correlativo:
                        raise Correlativo.DoesNotExist()
                    
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


def _check_stock_ticket(ticket, sucursal_id):
    """
    Revisa si algún producto del ticket tiene stock insuficiente en la sucursal.
    Retorna dict con 'tiene_stock_insuf' y 'productos_stock_insuf'.
    Asume que ticket_productos, ProductoTalla y producto ya están prefetch_related.
    """
    problemas = []
    for tp in ticket.ticket_productos.all():
        pt = tp.ProductoTalla
        if not pt:
            continue
        stock_real = pt.stock_sucursal(sucursal_id)
        cantidad_pedida = tp.stock  # campo 'stock' en Ticket_Productos = cantidad
        if stock_real < cantidad_pedida:
            articulo = pt.producto.articulo if pt.producto else 'Sin nombre'
            problemas.append({
                'sku': str(pt.sku),
                'articulo': articulo,
                'talla': str(pt.talla or ''),
                'stock_real': stock_real,
                'cantidad_pedida': int(cantidad_pedida),
            })
    return {
        'tiene_stock_insuf': len(problemas) > 0,
        'productos_stock_insuf': problemas,
    }


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
        hoy = timezone.localdate()
        inicio_dia = timezone.make_aware(datetime.combine(hoy, time.min))
        fin_dia = timezone.make_aware(datetime.combine(hoy, time.max))

        # Filtro base por sucursal y fecha
        base_filter = Q(sucursal_id=sucursal_id, created_at__range=[inicio_dia, fin_dia])

        # Estadísticas del día
        tickets_hoy = Ticket.objects.filter(base_filter)
        
        # Separar tickets de venta normal vs tickets de cambio/devolución
        tickets_venta = tickets_hoy.exclude(modulo_origen='CAMBIO_DEVOLUCION')
        tickets_cambio_dia = tickets_hoy.filter(modulo_origen='CAMBIO_DEVOLUCION')
        
        # Ventas del día (solo tickets de venta normal pagados)
        ventas_hoy = tickets_venta.filter(estado='PAGADO').aggregate(
            total=Sum('total')
        )['total'] or 0

        # Contadores por estado (solo tickets de venta normal)
        tickets_pendientes = tickets_venta.filter(estado='PENDIENTE').count()
        tickets_pagados = tickets_venta.filter(estado='PAGADO').count()
        
        # Promedio de venta (solo tickets de venta normal)
        promedio_venta = 0
        if tickets_pagados > 0:
            promedio_venta = ventas_hoy / tickets_pagados

        # Solo tickets PENDIENTES del día (últimos 20) - INCLUYENDO AMBOS TIPOS
        tickets_recientes = tickets_hoy.filter(estado='PENDIENTE').select_related(
            'vendedor', 'sucursal'
        ).prefetch_related(
            'ticket_productos',
            'ticket_productos__ProductoTalla',
            'ticket_productos__ProductoTalla__producto',
        ).order_by('-created_at')[:20]
        
        tickets_data = []
        for ticket in tickets_recientes:
            # Determinar tipo de ticket
            if ticket.modulo_origen == 'CAMBIO_DEVOLUCION':
                tipo_ticket = 'Diferencia Cambio'
                tipo_ticket_class = 'warning'
                # Determinar si es cobro o devolución
                if 'A DEVOLVER AL CLIENTE' in (ticket.observaciones or ''):
                    tipo_detalle = 'Devolución'
                elif 'A COBRAR AL CLIENTE' in (ticket.observaciones or ''):
                    tipo_detalle = 'Cobro'
                else:
                    tipo_detalle = 'Cambio Directo'
            else:
                tipo_ticket = 'Venta'
                tipo_ticket_class = 'primary'
                tipo_detalle = 'Venta Normal'
            
            tickets_data.append({
                'correlativo': ticket.correlativo,
                'hora': ticket.created_at.strftime('%H:%M'),
                'cliente_nombre': ticket.cliente_nombre or 'Sin nombre',
                'cliente_rut': ticket.cliente_rut or '',
                'vendedor_nombre': f"{ticket.vendedor.codigo_vendedor} - {ticket.vendedor.nombre}" if ticket.vendedor else 'Sin vendedor',
                'total': int(ticket.total or 0),
                'estado': ticket.estado,
                'tipo_ticket': tipo_ticket,
                'tipo_ticket_class': tipo_ticket_class,
                'tipo_detalle': tipo_detalle,
                'modulo_origen': ticket.modulo_origen,
                **_check_stock_ticket(ticket, sucursal_id),
            })

        # Tickets pendientes para el wizard (con más detalles)
        tickets_pendientes_query = tickets_hoy.filter(estado='PENDIENTE').select_related('vendedor').prefetch_related(
            'ticket_productos',
            'ticket_productos__ProductoTalla',
            'ticket_productos__ProductoTalla__producto',
        )[:10]
        tickets_pendientes_data = []
        for ticket in tickets_pendientes_query:
            # Determinar tipo de ticket
            if ticket.modulo_origen == 'CAMBIO_DEVOLUCION':
                tipo_ticket = 'Diferencia Cambio'
                tipo_ticket_class = 'warning'
            else:
                tipo_ticket = 'Venta'
                tipo_ticket_class = 'primary'
            
            tickets_pendientes_data.append({
                'correlativo': ticket.correlativo,
                'cliente_nombre': ticket.cliente_nombre or 'Sin nombre',
                'cliente_rut': ticket.cliente_rut or '',
                'vendedor_nombre': f"{ticket.vendedor.codigo_vendedor} - {ticket.vendedor.nombre}" if ticket.vendedor else 'Sin vendedor',
                'total': int(ticket.total or 0),
                'hora': ticket.created_at.strftime('%H:%M'),
                'productos_count': ticket.productos.count() if hasattr(ticket, 'productos') else 0,
                'tipo_ticket': tipo_ticket,
                'tipo_ticket_class': tipo_ticket_class,
                'modulo_origen': ticket.modulo_origen,
                **_check_stock_ticket(ticket, sucursal_id),
            })

        # TICKETS DE CAMBIOS/DEVOLUCIONES PENDIENTES (sin límite de fecha)
        tickets_cambio_pendientes = Ticket.objects.filter(
            sucursal_id=sucursal_id,
            estado='PENDIENTE',
            modulo_origen='CAMBIO_DEVOLUCION'
        ).select_related('vendedor').order_by('-created_at')[:20]
        
        tickets_cambio_data = []
        for ticket in tickets_cambio_pendientes:
            # Determinar tipo de operación
            if 'A DEVOLVER AL CLIENTE' in (ticket.observaciones or ''):
                tipo_op = 'DEVOLUCION'
                icono = '💵'
            elif 'A COBRAR AL CLIENTE' in (ticket.observaciones or ''):
                tipo_op = 'COBRO'
                icono = '💰'
            else:
                tipo_op = 'DIRECTO'
                icono = '🔄'
            
            tickets_cambio_data.append({
                'correlativo': ticket.correlativo,
                'cliente_nombre': ticket.cliente_nombre or 'Sin nombre',
                'total': int(ticket.total or 0),
                'tipo_operacion': tipo_op,
                'icono': icono,
                'metodo_pago': ticket.metodo_pago,
                'fecha': ticket.fecha.strftime('%d/%m/%Y'),
                'hora': ticket.created_at.strftime('%H:%M'),
            })

        return JsonResponse({
            'success': True,
            'stats': {
                'ventas_hoy': int(ventas_hoy),
                'tickets_pendientes': tickets_pendientes,
                'tickets_pagados': tickets_pagados,
                'promedio_venta': int(promedio_venta),
                'tickets_cambio_pendientes': len(tickets_cambio_data),
                'sucursal_nombre': (lambda s: s['alias'] or s['nombre'] if s else '')(Sucursal.objects.filter(id=sucursal_id).values('alias', 'nombre').first()),
            },
            'tickets': tickets_data,
            'tickets_pendientes': tickets_pendientes_data,
            'tickets_cambio': tickets_cambio_data
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
        eliminar_diferencia = data.get('eliminar_diferencia', False)
        
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
        
        # ===== ELIMINACIÓN DE DIFERENCIA DE CAMBIO (Solo Admin) =====
        if eliminar_diferencia:
            # Verificar que sea administrador
            if getattr(request.user, 'rol', '') not in ['administrador', 'administracion']:
                return JsonResponse({
                    'success': False,
                    'error': '⛔ Solo los administradores pueden eliminar diferencias de cambio'
                })
            
            # Verificar que sea un ticket de cambio/devolución
            if ticket.modulo_origen != 'CAMBIO_DEVOLUCION':
                return JsonResponse({
                    'success': False,
                    'error': 'Este ticket no es de cambio/devolución'
                })
            
            # Verificar que esté pendiente
            if ticket.estado != 'PENDIENTE':
                return JsonResponse({
                    'success': False,
                    'error': f'Solo se pueden eliminar diferencias de tickets pendientes. Estado actual: {ticket.estado}'
                })
            
            with transaction.atomic():
                # Marcar el ticket como completado sin cobro/devolución
                ticket.estado = 'PAGADO'  # Marcamos como "pagado" para que ya no aparezca como pendiente
                ticket.metodo_pago = 'ELIMINADO_ADMIN'
                ticket.observaciones = (ticket.observaciones or '') + f'\n\n🔐 [DIFERENCIA ELIMINADA POR ADMIN] {timezone.now().strftime("%Y-%m-%d %H:%M")}\n' + \
                                       f'Usuario: {request.user.username}\nMotivo: {motivo}'
                ticket.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Diferencia del ticket #{correlativo} eliminada por administrador',
                'ticket_id': ticket.id
            })
        
        # ===== ANULACIÓN NORMAL DE TICKET =====
        # No permitir anulación directa de tickets de cambio/devolución
        if ticket.modulo_origen == 'CAMBIO_DEVOLUCION':
            return JsonResponse({
                'success': False,
                'error': 'Los tickets de cambio/devolución no se pueden anular. Use la opción "Eliminar diferencia" desde la sección de Cambios (solo Admin).'
            })
        
        # Verificar que esté en estado PENDIENTE
        if ticket.estado != 'PENDIENTE':
            return JsonResponse({
                'success': False,
                'error': f'Solo se pueden anular tickets pendientes. Estado actual: {ticket.estado}'
            })
        
        with transaction.atomic():
            # Si es ticket de cambio/devolución, el stock ya fue ajustado
            # Solo revertir stock si es un ticket normal
            if ticket.modulo_origen != 'CAMBIO_DEVOLUCION':
                productos_ticket = Ticket_Productos.objects.filter(idTicket=ticket)
                
                for item in productos_ticket:
                    # Crear movimiento de devolución de stock
                    # ✅ Usar DTE si está disponible, si no usar correlativo del ticket
                    referencia = f'ANULACION_DTE_{ticket.folio_dte}' if ticket.folio_dte else f'ANULACION_TICKET_{ticket.correlativo}'
                    Movimientos_Producto.objects.create(
                        ProductoTalla=item.ProductoTalla,
                        cantidad=item.stock,  # Positivo para devolver al inventario
                        costo=item.ProductoTalla.producto.costo,
                        precio=int(item.precio),
                        concepto='ANULACION_TICKET',
                        tipo_movimiento='INGRESO',
                        responsable=request.user.username,
                        observaciones=f'Anulación de ticket #{ticket.correlativo} - Motivo: {motivo}',
                        referencia_externa=referencia
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
    from app.models import Cliente

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
    from app.models import Cliente

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
            'precio': tp.precio,  # Alias para compatibilidad con frontend
            'precio_original': tp.precio_original,
            'descuento_unitario': tp.descuento_unitario,
            'porcentaje_descuento': float(tp.porcentaje_descuento or 0),
            'subtotal': tp.subtotal,
            'costo_fifo': tp.costo_fifo,
            'lotes_utilizados': tp.lotes_utilizados,
            'stock_actual': producto_talla.stock if producto_talla else None,
            'stock': producto_talla.stock_sucursal(ticket.sucursal_id) if producto_talla else 0,  # Stock real de la sucursal del ticket
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
            'numero_orden_compra': pago.numero_orden_compra or '',
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
        'modulo_origen': ticket.modulo_origen,  # ✅ Agregar módulo de origen para identificar tickets de cambio
        'metodo_pago_principal': ticket.metodo_pago,
        'total_pagado': total_pagado,
        'saldo_por_pagar': saldo_por_pagar,
        'responsable': ticket.responsable,
        'sucursal': {
            'alias': sucursal.alias,
            'nombre': getattr(sucursal, 'nombreSucursal', None) or sucursal.alias or '',
            'direccion': sucursal.direccion,
            'empresa': empresa.nombre if empresa else '',
            'rut_empresa': empresa.rut if empresa else ''
        },
        'vendedor': {
            'nombre': ticket.vendedor.nombre if ticket.vendedor else '',
            'codigo': ticket.vendedor.codigo_vendedor if ticket.vendedor else ''
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

    # Primero buscar en la sucursal activa
    ticket = (
        Ticket.objects
        .select_related('sucursal', 'vendedor')
        .prefetch_related('ticket_productos__ProductoTalla__producto', 'pagos')
        .filter(sucursal_id=sucursal_id, correlativo=correlativo)
        .first()
    )

    # Si no se encuentra, buscar en todas las sucursales del usuario
    # (para casos de cotizaciones facturadas desde otra sucursal)
    if not ticket:
        # Buscar en cualquier sucursal que el usuario tenga acceso
        ticket = (
            Ticket.objects
            .select_related('sucursal', 'vendedor')
            .prefetch_related('ticket_productos__ProductoTalla__producto', 'pagos')
            .filter(correlativo=correlativo)
            .order_by('-fecha', '-hora')  # El más reciente primero
            .first()
        )

    # Si sigue sin encontrarse, intentar buscar por folio_dte
    # (cuando se llama desde gestionVentasDocumentos pasando el número de DTE en lugar del correlativo de ticket)
    if not ticket:
        ticket = (
            Ticket.objects
            .select_related('sucursal', 'vendedor')
            .prefetch_related('ticket_productos__ProductoTalla__producto', 'pagos')
            .filter(folio_dte=correlativo)
            .order_by('-fecha', '-hora')
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


@login_required
@require_POST
def crear_ticket_pendiente_pos(request):
    """Crear un ticket PENDIENTE vacío desde el POS Dashboard para iniciar una nueva venta."""
    try:
        sucursal_id = (
            request.session.get('idSucursalActual')
            or request.session.get('sucursalActual')
        )
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'No hay sucursal activa en la sesión'}, status=400)

        sucursal = get_object_or_404(Sucursal, id=sucursal_id)

        vendedor = Vendedor.objects.filter(sucursales=sucursal, activo=True).first()
        if not vendedor:
            vendedor = Vendedor.objects.filter(
                empresa=sucursal.empresa, activo=True
            ).first() if hasattr(sucursal, 'empresa') and sucursal.empresa else None
        if not vendedor:
            return JsonResponse({
                'success': False,
                'error': 'No hay vendedores activos configurados para esta sucursal'
            }, status=400)

        with transaction.atomic():
            correlativo = obtener_siguiente_correlativo(sucursal, 'TICKET')
            ticket = Ticket.objects.create(
                correlativo=correlativo,
                sucursal=sucursal,
                vendedor=vendedor,
                subTotal=0,
                descuento=0,
                total=0,
                estado='PENDIENTE',
                responsable=request.user.username,
                modulo_origen='POS',
            )

        return JsonResponse({
            'success': True,
            'ticket': construir_ticket_data(ticket),
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error al crear ticket: {str(e)}'}, status=500)


def generar_dte_desde_ticket(ticket, tipo_documento, usuario, cotizacion=None):
    """
    Generar DTE (Boleta o Factura Electrónica) desde un Ticket
    Genera tanto el registro en BD como el archivo TXT para Acepta
    
    Args:
        cotizacion: Objeto Cotizacion_Empresa opcional para usar sus descripciones en el TXT
    """
    from decimal import Decimal
    
    # Mapear tipo de documento
    tipo_dte_map = {
        'BOLETA_ELECTRONICA': 'BOLETA ELECTRONICA',
        'BOLETA_PAPEL': 'BOLETA PAPEL',
        'FACTURA_ELECTRONICA': 'FACTURA ELECTRONICA',
    }
    
    tipo_dte = tipo_dte_map.get(tipo_documento, 'BOLETA ELECTRONICA')
    es_boleta = 'BOLETA' in tipo_dte
    
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
            )
    
    # Obtener siguiente correlativo para el DTE
    correlativo_dte = obtener_siguiente_correlativo(ticket.sucursal, tipo_dte)
    
    # Calcular montos - Los precios del ticket YA INCLUYEN IVA
    total_con_iva = Decimal(ticket.total or 0)
    descuento = Decimal(ticket.descuento or 0)
    
    # Descomponer el total para obtener neto e IVA
    # Total = Neto + IVA, donde IVA = Neto * 0.19
    # Total = Neto * 1.19
    # Neto = Total / 1.19
    neto = (total_con_iva / Decimal('1.19')).quantize(Decimal('0'))
    iva = total_con_iva - neto
    total = total_con_iva
    
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
        hora=ticket.hora,
        referencias=f'TICKET-{ticket.correlativo}'
    )
    
    # Copiar productos del ticket al DTE
    for tp in ticket.ticket_productos.all():
        if tp.ProductoTalla:
            costo_unitario = tp.ProductoTalla.producto.costo if tp.ProductoTalla.producto else 0
            sobreprecio_unitario = tp.ProductoTalla.producto.sobreprecio if tp.ProductoTalla.producto else 0
            descripcion_prod = (tp.ProductoTalla.producto.descripcion or tp.ProductoTalla.producto.articulo) if tp.ProductoTalla.producto else (tp.descripcion_linea or '')
        else:
            costo_unitario = 0
            sobreprecio_unitario = 0
            descripcion_prod = tp.descripcion_linea or 'Ítem pendiente de despacho'

        dcto_unit = tp.descuento_unitario or 0
        dcto_pct = float(tp.porcentaje_descuento or 0)
        dcto_monto_linea = dcto_unit * tp.stock if dcto_unit else 0

        Dte_Productos.objects.create(
            dte=dte,
            productoTalla=tp.ProductoTalla,
            stock=tp.stock,
            costo=costo_unitario,
            sobreprecio=sobreprecio_unitario,
            precio=tp.precio,
            precio_unitario=tp.precio,
            descuento_pct=dcto_pct if dcto_pct > 0 else None,
            descuento_monto=dcto_monto_linea if dcto_monto_linea > 0 else None,
            monto_item=tp.subtotal,
            descripcion=descripcion_prod[:255],
            es_pendiente_despacho=tp.es_pendiente_despacho,
        )
    
    # Copiar métodos de pago
    for pago in ticket.pagos.all():
        Dte_Detalle_Pago.objects.create(
            dte=dte,
            metodo_pago=pago.metodo_pago,
            monto=pago.monto,
            tipo_tarjeta=pago.tipo_tarjeta or '',
            voucher=pago.voucher or '',
            notas=pago.notas or ''
        )
    
    # ✅ Actualizar movimientos del ticket para que también referencien el DTE
    from .models import Movimientos_Producto
    movimientos_ticket = Movimientos_Producto.objects.filter(ticket=ticket, dte__isnull=True)
    for mov in movimientos_ticket:
        mov.dte = dte
        mov.observaciones = f"{mov.observaciones or ''} - DTE {dte.tipo_documento} #{dte.numero_documento}".strip(' -')
        # ✅ Actualizar referencia_externa con el número de DTE
        mov.referencia_externa = f'DTE_{dte.numero_documento}'
        mov.save()
    
    # ✅ Actualizar el campo folio_dte del ticket
    ticket.folio_dte = dte.numero_documento
    ticket.dte_generado = True
    ticket.dte_fecha_generacion = timezone.now()
    ticket.save()
    
    print(f"✓ DTE generado: {dte.tipo_documento} #{dte.numero_documento} | {movimientos_ticket.count()} movimientos actualizados")
    
    # ✅ Generar archivo TXT para Acepta (solo para documentos electrónicos, no para BOLETA PAPEL)
    archivo_txt_data = None
    
    # Solo generar TXT si NO es BOLETA PAPEL
    if tipo_dte != 'BOLETA PAPEL':
        try:
            from .views_modulo_documentos import generar_txt_dte_acepta
            
            # Preparar datos para TXT
            empresa = ticket.sucursal.empresa
            
            # Preparar información de métodos de pago
            metodos_pago_info = []
            for pago in ticket.pagos.all():
                metodo_nombre = dict(METODO_PAGO_TICKET_CHOICES).get(pago.metodo_pago, pago.metodo_pago)
                metodos_pago_info.append(f"{metodo_nombre}: ${pago.monto:,}")
            metodos_pago_texto = ' | '.join(metodos_pago_info) if metodos_pago_info else 'EFECTIVO'
            
            # ✅ DETECTAR SI ES TICKET DE CAMBIO/DEVOLUCIÓN
            es_ticket_cambio = (ticket.modulo_origen == 'CAMBIO_DEVOLUCION')
            
            # Preparar productos para el TXT
            productos_txt = []
            
            if es_ticket_cambio:
                print(f"🔄 Generando TXT para TICKET DE CAMBIO - Productos con precio $0, solo diferencia")
                
                # Para tickets de cambio: mostrar productos con precio $0
                for tp in ticket.ticket_productos.all():
                    if tp.ProductoTalla is None:
                        productos_txt.append({
                            'sku': tp.cotizacion_detalle_id or 'PEND',
                            'nombre': (tp.descripcion_linea or 'Ítem pendiente')[:80],
                            'descripcion': '',
                            'cantidad': tp.stock,
                            'precio_unitario': 0,
                            'total': 0
                        })
                        continue
                    producto = tp.ProductoTalla.producto
                    # Usar descripción del producto si existe, sino artículo
                    nombre_producto = producto.descripcion if producto and producto.descripcion else producto.articulo
                    
                    productos_txt.append({
                        'sku': tp.ProductoTalla.sku,
                        'nombre': nombre_producto[:80],
                        'descripcion': '',  # Dejar vacío para evitar duplicados
                        'cantidad': tp.stock,
                        'precio_unitario': 0,  # ✅ PRECIO $0 para productos en cambio
                        'total': 0
                    })
                
                # Agregar ítem "DIFERENCIA DE CAMBIO" con el total (si es positivo)
                diferencia = int(ticket.total or 0)
                if diferencia > 0:
                    productos_txt.append({
                        'sku': 'DIF',
                        'nombre': 'DIFERENCIA DE CAMBIO',
                        'descripcion': '',
                        'cantidad': 1,
                        'precio_unitario': diferencia,
                        'total': diferencia
                    })
                else:
                    # Si es negativo o cero, agregar con $0
                    productos_txt.append({
                        'sku': 'DIF',
                        'nombre': 'DIFERENCIA DE CAMBIO',
                        'descripcion': '',
                        'cantidad': 1,
                        'precio_unitario': 0,
                        'total': 0
                    })
            else:
                # Ticket normal: productos con sus precios reales
                
                # ✅ Si viene de cotización, crear mapa de descripciones por SKU
                descripciones_cotizacion = {}
                if cotizacion:
                    try:
                        # Obtener descripciones de los items de la cotización
                        for item in cotizacion.items.all().prefetch_related('skus_asociados__producto_talla'):
                            for sku_rel in item.skus_asociados.all():
                                if sku_rel.producto_talla:
                                    # Usar la descripción del item de la cotización
                                    descripciones_cotizacion[sku_rel.producto_talla.sku] = item.descripcion
                        print(f"📋 Descripciones de cotización cargadas: {len(descripciones_cotizacion)} productos")
                    except Exception as e:
                        print(f"⚠️ Error al cargar descripciones de cotización: {e}")
                
                for tp in ticket.ticket_productos.all():
                    if tp.ProductoTalla is None:
                        # Ítem manual / pendiente de despacho — usar descripción de línea
                        sku = tp.cotizacion_detalle_id or 'PEND'
                        nombre_producto = tp.descripcion_linea or 'Ítem pendiente de despacho'
                    else:
                        producto = tp.ProductoTalla.producto
                        sku = tp.ProductoTalla.sku
                        
                        # ✅ PRIORIDAD: 1) Descripción de cotización, 2) Descripción del producto, 3) Artículo
                        if sku in descripciones_cotizacion and descripciones_cotizacion[sku]:
                            nombre_producto = descripciones_cotizacion[sku]
                            print(f"  📄 SKU {sku}: usando descripción de cotización: {nombre_producto[:40]}")
                        elif producto and producto.descripcion:
                            nombre_producto = producto.descripcion
                        else:
                            nombre_producto = producto.articulo if producto else str(sku)
                    
                    if not es_boleta:
                        precio_unitario_txt = int(round(Decimal(tp.precio) / Decimal('1.19')))
                        monto_descuento_txt = int(round(Decimal(tp.descuento_unitario * tp.stock) / Decimal('1.19'))) if tp.descuento_unitario else 0
                        monto_item_txt = int(round(Decimal(tp.subtotal) / Decimal('1.19')))
                    else:
                        precio_unitario_txt = tp.precio
                        monto_descuento_txt = 0
                        monto_item_txt = tp.precio * tp.stock
                    
                    productos_txt.append({
                        'sku': sku,
                        'nombre': nombre_producto[:80],
                        'descripcion': '',
                        'cantidad': tp.stock,
                        'precio_unitario': precio_unitario_txt,
                        'descuento_pct': float(tp.porcentaje_descuento) if tp.porcentaje_descuento else 0,
                        'monto_descuento': monto_descuento_txt,
                        'total': monto_item_txt
                    })
            
            # Importar función de limpieza para eliminar acentos y caracteres especiales
            from .views_modulo_documentos import limpiar_texto
            
            # Datos del documento - ✅ Aplicar limpiar_texto para eliminar acentos y Ñ
            datos_txt = {
                'documento': {
                    'tipo_documento': 39 if es_boleta else 33,  # 39=Boleta, 33=Factura
                    'folio': dte.numero_documento,
                    'fecha_emision': dte.fecha_emision.strftime('%Y-%m-%d'),
                    'forma_pago': 1,  # Contado
                    'ind_servicio': 3,  # Venta y servicios (para boleta)
                    'timestamp': timezone.now().strftime('%Y-%m-%dT%H:%M:%S')
                },
                'emisor': {
                    'rut': empresa.rut,
                    'razon_social': limpiar_texto(empresa.razon_social or empresa.nombre),
                    'giro': limpiar_texto(empresa.giro or 'Sin giro'),
                    'acteco': empresa.acteco or '',
                    'direccion': limpiar_texto(empresa.direccion or ''),
                    'comuna': limpiar_texto(empresa.comuna or ''),
                    'ciudad': limpiar_texto(empresa.ciudad or ''),
                    'codigo_vendedor': limpiar_texto(ticket.vendedor.codigo_vendedor if ticket.vendedor else 'VENDEDOR'),
                    'nombre_vendedor': limpiar_texto(ticket.vendedor.nombre if ticket.vendedor else 'Sin vendedor'),
                    'metodos_pago': limpiar_texto(metodos_pago_texto),
                    'correlativo_ticket': ticket.correlativo,
                    'telefono': empresa.contacto1 or '',
                    'nombre_impresora_boleta': getattr(ticket.sucursal, 'nombre_impresora_boleta', 'boleta') or 'boleta',
                    'nombre_impresora_factura': getattr(ticket.sucursal, 'nombre_impresora_factura', 'factura') or 'factura',
                    'sucursal': limpiar_texto(ticket.sucursal.alias if ticket.sucursal else ''),
                },
                'receptor': {
                    'rut': receptor.rut if receptor and not es_boleta else '66666666-6',  # Consumidor final para boletas
                    'razon_social': limpiar_texto(receptor.razon_social if receptor and not es_boleta else 'CONSUMIDOR FINAL'),
                    'giro': limpiar_texto(receptor.giro if receptor and not es_boleta else ''),
                    'direccion': limpiar_texto(receptor.direccion if receptor and not es_boleta else ''),
                    'comuna': limpiar_texto(receptor.comuna if receptor and not es_boleta else ''),
                    'ciudad': limpiar_texto(receptor.ciudad if receptor and not es_boleta else '')
                },
                'totales': {
                    'monto_neto': int(neto),
                    'monto_exento': 0,
                    'tasa_iva': 19,
                    'iva': int(iva),
                    'monto_total': int(total),
                    'descuento_global': 0
                },
                'detalle': [],
                'observaciones': ticket.observaciones or '',
                'observaciones_adicionales': ticket.observaciones_adicionales or ''
            }
            
            for prod_txt in productos_txt:
                sku_str = str(prod_txt.get('sku', ''))
                datos_txt['detalle'].append({
                    'codigo': limpiar_texto(sku_str[:35]),
                    'sku': limpiar_texto(sku_str),
                    'nombre': limpiar_texto(prod_txt['nombre']),
                    'descripcion': limpiar_texto(prod_txt.get('descripcion', '')),
                    'cantidad': prod_txt['cantidad'],
                    'unidad': 'UN',
                    'precio_unitario': prod_txt['precio_unitario'],
                    'descuento_pct': prod_txt.get('descuento_pct', 0),
                    'monto_descuento': prod_txt.get('monto_descuento', 0),
                    'monto_item': prod_txt['total']
                })
            
            # Detect discounts (per-item or global) and add Tabla 4 block + fix total
            descuento_items = sum(
                (tp.descuento_unitario or 0) * tp.stock
                for tp in ticket.ticket_productos.all()
            )
            descuento_ticket = int(ticket.descuento or 0)
            descuento_efectivo = descuento_items if descuento_items > 0 else descuento_ticket

            suma_items_txt = sum(d['monto_item'] for d in datos_txt['detalle'])

            if descuento_efectivo > 0:
                datos_txt['descuentos_recargos'] = [{
                    'tpo_mov': 'D',
                    'glosa_dr': 'Descuento',
                    'tpo_valor': '$',
                    'valor_dr': descuento_efectivo,
                }]
                total_correcto = suma_items_txt - descuento_efectivo
                if total_correcto > 0:
                    datos_txt['totales']['monto_total'] = total_correcto
                print(f"TXT: Descuento ${descuento_efectivo:,} aplicado. Items: ${suma_items_txt:,}, Total: ${total_correcto:,}")
            elif suma_items_txt > int(total) and int(total) > 0:
                diferencia_desc = suma_items_txt - int(total)
                print(f"TXT: Items sum ({suma_items_txt}) > Total ({int(total)}). Adding discount section: ${diferencia_desc:,}")
                datos_txt['descuentos_recargos'] = [{
                    'tpo_mov': 'D',
                    'glosa_dr': 'Descuento',
                    'tpo_valor': '$',
                    'valor_dr': diferencia_desc,
                }]
            
            # Generar TXT
            contenido_txt = generar_txt_dte_acepta(datos_txt)
            
            # Preparar datos del archivo para retornar
            nombre_archivo = f"{tipo_dte.replace(' ', '_')}_{dte.numero_documento}_{ticket.correlativo}.txt"
            archivo_txt_data = {
                'contenido': contenido_txt,
                'nombre_archivo': nombre_archivo
            }
            
            print(f"✓ Archivo TXT generado: {nombre_archivo}")
            
        except Exception as e:
            print(f"⚠ Error al generar TXT: {str(e)}")
            import traceback
            traceback.print_exc()
            # No fallar la generación del DTE por error en TXT
    
    # Guardar datos del TXT en el DTE para referencia
    dte.archivo_txt_data = archivo_txt_data
    
    return dte


@login_required
@require_http_methods(["POST"])
def registrar_pagos_ticket(request, correlativo):
    """Registrar pagos para un ticket en POS"""
    print(f"🔍🔍🔍 DEBUG: ===== INICIO registrar_pagos_ticket - Ticket #{correlativo} =====")
    
    sucursal_id = (
        request.session.get('idSucursalActual')
        or request.session.get('sucursalActual')
        or request.session.get('idSucursalActualPOS')
    )
    if not sucursal_id:
        return JsonResponse({'success': False, 'error': 'No hay sucursal activa en la sesión'}, status=400)

    # =========================================================================
    # NUEVO: Manejar cotizaciones cargadas como ticket
    # Si el correlativo empieza con "COT-", es una cotización que necesita
    # crear un ticket nuevo antes de procesar el pago
    # =========================================================================
    es_cotizacion = str(correlativo).startswith('COT-')
    cotizacion_obj = None
    productos_ya_creados_desde_cotizacion = False  # Bandera para evitar duplicar productos
    
    if es_cotizacion:
        print(f"📋 Detectada cotización: {correlativo}")
        try:
            payload_check = json.loads(request.body or '{}')
            cotizacion_id = payload_check.get('cotizacion_id')
            
            if not cotizacion_id:
                return JsonResponse({
                    'success': False, 
                    'error': 'Cotización detectada pero falta cotizacion_id en el payload'
                }, status=400)
            
            # Importar modelo de cotización
            from .models import Cotizacion_Empresa, Historial_Cotizacion
            
            # ✅ Buscar la cotización por ID (sin filtrar por sucursal activa)
            cotizacion_obj = Cotizacion_Empresa.objects.filter(id=cotizacion_id).first()
            
            if not cotizacion_obj:
                return JsonResponse({
                    'success': False, 
                    'error': f'Cotización {cotizacion_id} no encontrada'
                }, status=404)
            
            # ✅ IMPORTANTE: Usar la sucursal de la COTIZACIÓN, no la de la sesión
            # La cotización se debe facturar en su sucursal original
            sucursal_id = cotizacion_obj.sucursal_id
            
            # ✅ Verificar si la cotización ya fue facturada
            if cotizacion_obj.facturada:
                return JsonResponse({
                    'success': False, 
                    'error': f'La cotización {cotizacion_obj.numero_cotizacion} ya fue facturada con documento {cotizacion_obj.numero_factura}'
                }, status=400)
            
            if not cotizacion_obj.esta_vigente:
                return JsonResponse({
                    'success': False, 
                    'error': 'La cotización no está vigente o está vencida'
                }, status=400)
            
            sucursal = get_object_or_404(Sucursal, id=sucursal_id)
            
            # Obtener productos de la cotización
            productos_cotizacion = payload_check.get('productos', [])
            if not productos_cotizacion:
                return JsonResponse({
                    'success': False, 
                    'error': 'No hay productos en la cotización'
                }, status=400)
            
            # ✅ VALIDAR STOCK ANTES DE CREAR EL TICKET
            # IMPORTANTE: Usar producto_talla_id si está disponible, ya que el SKU puede existir
            # en múltiples sucursales y .first() retornaría el incorrecto
            productos_sin_stock = []
            
            for prod_data in productos_cotizacion:
                producto_talla = None
                
                # Skip pending items — they have no SKU and don't need stock validation
                if prod_data.get('es_pendiente_despacho'):
                    continue
                
                # ✅ PRIMERO: Intentar obtener por producto_talla_id (más preciso)
                producto_talla_id = prod_data.get('producto_talla_id')
                if producto_talla_id:
                    producto_talla = Producto_Talla.objects.filter(id=producto_talla_id).first()
                
                # FALLBACK: Si no hay producto_talla_id, buscar por SKU
                if not producto_talla:
                    sku = prod_data.get('sku')
                    if sku:
                        # Filtrar por SKU Y por sucursal de la cotización para evitar ambigüedad
                        producto_talla = Producto_Talla.objects.filter(
                            sku=sku,
                            producto__sucursal_id=sucursal_id
                        ).first()
                        # Si no existe en la sucursal, buscar global (compatibilidad)
                        if not producto_talla:
                            producto_talla = Producto_Talla.objects.filter(sku=sku).first()
                
                if producto_talla:
                    stock_disponible = producto_talla.stock_sucursal(sucursal_id)
                    cantidad_requerida = int(prod_data.get('cantidad', 1))
                    sku_display = prod_data.get('sku') or producto_talla.sku
                    
                    if stock_disponible < cantidad_requerida:
                        productos_sin_stock.append({
                            'sku': str(sku_display),
                            'nombre': producto_talla.producto.articulo if producto_talla.producto else 'Sin nombre',
                            'stock_disponible': stock_disponible,
                            'cantidad_requerida': cantidad_requerida
                        })
            
            if productos_sin_stock:
                detalle = ', '.join([f"SKU {p['sku']}: {p['stock_disponible']}/{p['cantidad_requerida']}" for p in productos_sin_stock])
                print(f"❌ Stock insuficiente: {detalle}")
                return JsonResponse({
                    'success': False,
                    'error': f'Stock insuficiente para facturar. {detalle}',
                    'error_tipo': 'STOCK_INSUFICIENTE',
                    'productos_sin_stock': productos_sin_stock
                }, status=400)
            
            print(f"✅ Stock validado correctamente")
            
            # Crear ticket desde la cotización
            print(f"🔄 Creando ticket desde cotización {cotizacion_obj.numero_cotizacion}")
            
            # Obtener siguiente correlativo para ticket
            nuevo_correlativo = obtener_siguiente_correlativo(sucursal, 'TICKET')
            
            # Calcular totales
            subtotal_calc = sum(p.get('subtotal', 0) for p in productos_cotizacion)
            total = int(cotizacion_obj.total)
            
            # Datos del cliente
            datos_cliente = payload_check.get('cliente', {})
            
            # Obtener vendedor (usar el de la cotización o buscar uno por defecto)
            vendedor = cotizacion_obj.vendedor
            if not vendedor:
                # Buscar vendedor activo asignado a esta sucursal (ManyToMany)
                vendedor = Vendedor.objects.filter(sucursales=sucursal, activo=True).first()
                if not vendedor:
                    # Buscar cualquier vendedor de la sucursal
                    vendedor = Vendedor.objects.filter(sucursales=sucursal).first()
                    if not vendedor:
                        return JsonResponse({
                            'success': False, 
                            'error': 'No hay vendedores configurados para esta sucursal'
                        }, status=400)
            
            # Crear el ticket
            ticket = Ticket.objects.create(
                correlativo=nuevo_correlativo,
                sucursal=sucursal,
                vendedor=vendedor,
                subTotal=int(subtotal_calc),  # Campo es subTotal con T mayúscula
                descuento=int(cotizacion_obj.descuento or 0),
                total=total,
                estado='PENDIENTE',
                responsable=request.user.username,  # Campo obligatorio
                observaciones=cotizacion_obj.observaciones or '',
                observaciones_adicionales=f'Facturación de cotización {cotizacion_obj.numero_cotizacion}. {cotizacion_obj.descripcion or ""}',
                cliente_nombre=datos_cliente.get('nombre', cotizacion_obj.cliente.nombre),
                cliente_rut=formatear_rut(datos_cliente.get('rut', cotizacion_obj.cliente.rut)),
                cliente_giro=datos_cliente.get('giro', cotizacion_obj.cliente.giro or ''),
                cliente_direccion=datos_cliente.get('direccion', cotizacion_obj.cliente.direccion or ''),
                cliente_comuna=datos_cliente.get('comuna', cotizacion_obj.cliente.comuna or ''),
                cliente_ciudad=datos_cliente.get('ciudad', cotizacion_obj.cliente.ciudad or ''),
                cliente_email=datos_cliente.get('email', cotizacion_obj.cliente.correoIntercambio or ''),
                cliente_email_facturacion=datos_cliente.get('email_facturacion', cotizacion_obj.cliente.correoAdministrador or ''),
                modulo_origen='POS'  # Usar POS ya que COTIZACION no existe en choices
            )
            
            # Crear productos del ticket
            # ✅ IMPORTANTE: Usar producto_talla_id si está disponible
            for prod_data in productos_cotizacion:
                producto_talla = None
                sku_display = prod_data.get('sku', 'N/A')

                cantidad = int(prod_data.get('cantidad', 1))
                precio = int(prod_data.get('precio_unitario', prod_data.get('precio', 0)))
                descuento = int(prod_data.get('descuento_unitario', 0))
                subtotal_prod = int(prod_data.get('subtotal', cantidad * precio))

                # Ítem pendiente de despacho (sin SKU) — crear línea manual
                if prod_data.get('es_pendiente_despacho'):
                    Ticket_Productos.objects.create(
                        idTicket=ticket,
                        ProductoTalla=None,
                        stock=cantidad,
                        precio=precio,
                        precio_original=precio,
                        descuento_unitario=descuento,
                        subtotal=subtotal_prod,
                        porcentaje_descuento=0,
                        descripcion_linea=prod_data.get('articulo') or prod_data.get('descripcion') or 'Ítem pendiente',
                        es_pendiente_despacho=True,
                        cotizacion_detalle_id=prod_data.get('cotizacion_item_id'),
                    )
                    print(f"  📋 Ítem pendiente de despacho agregado: {prod_data.get('articulo')} x{cantidad}")
                    continue
                
                # ✅ PRIMERO: Intentar obtener por producto_talla_id (más preciso)
                producto_talla_id = prod_data.get('producto_talla_id')
                if producto_talla_id:
                    producto_talla = Producto_Talla.objects.filter(id=producto_talla_id).first()
                
                # FALLBACK: Si no hay producto_talla_id, buscar por SKU
                if not producto_talla:
                    sku = prod_data.get('sku')
                    if sku:
                        # Filtrar por SKU Y por sucursal de la cotización
                        producto_talla = Producto_Talla.objects.filter(
                            sku=sku,
                            producto__sucursal_id=sucursal_id
                        ).first()
                        # Si no existe en la sucursal, buscar global (compatibilidad)
                        if not producto_talla:
                            producto_talla = Producto_Talla.objects.filter(sku=sku).first()
                        sku_display = sku
                
                if not producto_talla:
                    print(f"⚠️ ProductoTalla no encontrado para ID {producto_talla_id} / SKU {sku_display}")
                    continue
                
                cantidad = int(prod_data.get('cantidad', 1))
                precio = int(prod_data.get('precio_unitario', prod_data.get('precio', 0)))
                descuento = int(prod_data.get('descuento_unitario', 0))
                subtotal_prod = int(prod_data.get('subtotal', cantidad * precio))
                
                Ticket_Productos.objects.create(
                    idTicket=ticket,
                    ProductoTalla=producto_talla,
                    stock=cantidad,
                    precio=precio,
                    precio_original=precio,
                    descuento_unitario=descuento,
                    subtotal=subtotal_prod,
                    porcentaje_descuento=0
                )
                print(f"  ✅ Producto agregado: SKU {producto_talla.sku} (ID: {producto_talla.id}) x{cantidad}")
            
            # ✅ NUEVO: Actualizar datos de la Empresa si el usuario completó campos faltantes
            empresa_cliente = cotizacion_obj.cliente
            empresa_actualizada = False
            campos_actualizados = []
            
            # Solo actualizar si hay datos nuevos que la empresa no tenía
            if datos_cliente.get('giro') and not empresa_cliente.giro:
                empresa_cliente.giro = datos_cliente.get('giro')
                empresa_actualizada = True
                campos_actualizados.append('giro')
            
            if datos_cliente.get('direccion') and not empresa_cliente.direccion:
                empresa_cliente.direccion = datos_cliente.get('direccion')
                empresa_actualizada = True
                campos_actualizados.append('direccion')
            
            if datos_cliente.get('comuna') and not empresa_cliente.comuna:
                empresa_cliente.comuna = datos_cliente.get('comuna')
                empresa_actualizada = True
                campos_actualizados.append('comuna')
            
            if datos_cliente.get('ciudad') and not empresa_cliente.ciudad:
                empresa_cliente.ciudad = datos_cliente.get('ciudad')
                empresa_actualizada = True
                campos_actualizados.append('ciudad')
            
            if datos_cliente.get('email') and not empresa_cliente.correoIntercambio:
                empresa_cliente.correoIntercambio = datos_cliente.get('email')
                empresa_actualizada = True
                campos_actualizados.append('email')
            
            if datos_cliente.get('telefono') and not empresa_cliente.contacto1:
                empresa_cliente.contacto1 = datos_cliente.get('telefono')
                empresa_actualizada = True
                campos_actualizados.append('telefono')
            
            if empresa_actualizada:
                empresa_cliente.save()
                print(f"✅ Empresa {empresa_cliente.rut} actualizada con campos: {', '.join(campos_actualizados)}")
            
            # Actualizar correlativo para el resto del proceso
            correlativo = nuevo_correlativo
            # Marcar que los productos ya fueron creados (evitar duplicados)
            productos_ya_creados_desde_cotizacion = True
            print(f"✅ Ticket #{correlativo} creado desde cotización {cotizacion_obj.numero_cotizacion}")
            
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Formato JSON inválido'}, status=400)
        except Exception as e:
            print(f"❌ Error creando ticket desde cotización: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': f'Error al procesar cotización: {str(e)}'}, status=500)
    
    # =========================================================================
    # FIN: Manejo de cotizaciones
    # =========================================================================

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

    # ✅ Guardar datos de referencia ÚNICA (Retrocompatibilidad)
    ticket.referencia_tipo = payload.get('referencia_tipo') or None
    ticket.referencia_folio = payload.get('referencia_folio') or None
    if payload.get('referencia_fecha'):
        from datetime import datetime
        try:
            ticket.referencia_fecha = datetime.strptime(payload.get('referencia_fecha'), '%Y-%m-%d').date()
        except:
            ticket.referencia_fecha = None
    else:
        ticket.referencia_fecha = None
    
    # ✅ Procesar MÚLTIPLES REFERENCIAS (Nuevo sistema)
    referencias_payload = payload.get('referencias', [])
    if referencias_payload and isinstance(referencias_payload, list) and len(referencias_payload) > 0:
        # Eliminar referencias anteriores
        ticket.referencias.all().delete()
        
        # Crear nuevas referencias
        for ref_data in referencias_payload:
            try:
                from datetime import datetime
                fecha_ref = datetime.strptime(ref_data.get('fecha'), '%Y-%m-%d').date()
                
                TicketReferencia.objects.create(
                    ticket=ticket,
                    tipo_documento=ref_data.get('tipo_documento', ''),
                    folio=ref_data.get('folio', ''),
                    fecha=fecha_ref,
                    observaciones=ref_data.get('observaciones', '')
                )
            except Exception as e:
                import traceback
                print(f"⚠️ Error al crear referencia: {e}")
                print(f"⚠️ Traceback: {traceback.format_exc()}")
                continue

    nuevo_estado = payload.get('estado')
    if nuevo_estado and nuevo_estado in dict(ESTADO_TICKET_CHOICES):
        ticket.estado = nuevo_estado
    
    # ✅ NUEVO: Si es cotización y tiene pagos válidos, marcar automáticamente como PAGADO
    if productos_ya_creados_desde_cotizacion and ticket.estado == 'PENDIENTE':
        pagos_payload = payload.get('pagos', [])
        total_pagado = sum(int(p.get('monto', 0)) for p in pagos_payload if p.get('monto'))
        if total_pagado >= ticket.total:
            print(f"✅ Cotización con pagos completos (${total_pagado:,} >= ${ticket.total:,}), marcando ticket como PAGADO")
            ticket.estado = 'PAGADO'

    metodo_principal = payload.get('metodo_pago_principal')
    if metodo_principal and metodo_principal in dict(METODO_PAGO_TICKET_CHOICES):
        ticket.metodo_pago = metodo_principal

    # Guardar condición de pago DTE elegida por el usuario (1=Contado, 2=Crédito)
    condicion_pago_dte = payload.get('condicion_pago_dte')
    if condicion_pago_dte in (1, 2):
        import json as _json
        try:
            notas = _json.loads(ticket.observaciones_adicionales or '{}')
        except (ValueError, TypeError):
            notas = {}
        notas['condicion_pago_dte'] = condicion_pago_dte
        ticket.observaciones_adicionales = _json.dumps(notas)

    correlativo_confirmacion = payload.get('correlativo')
    # Solo validar correlativo si NO es una cotización (las cotizaciones tienen formato COT-xxx)
    if correlativo_confirmacion and not str(correlativo_confirmacion).startswith('COT-'):
        try:
            if int(correlativo_confirmacion) != ticket.correlativo:
                return JsonResponse({'success': False, 'error': 'Correlativo no coincide con el ticket cargado'}, status=400)
        except ValueError:
            pass  # Si no se puede convertir a int, ignorar validación

    # ✅ NUEVO: Procesar productos actualizados (incluye productos agregados como bolsas)
    # IMPORTANTE: Si es cotización, los productos ya fueron creados al crear el ticket
    # IMPORTANTE: Si es cambio/devolución, NO se permiten cambios en los productos
    productos_payload = payload.get('productos', [])
    if ticket.modulo_origen == 'CAMBIO_DEVOLUCION':
        print(f"⏭️ Saltando procesamiento de productos (ticket de cambio/devolución — productos bloqueados)")
    elif productos_ya_creados_desde_cotizacion:
        print(f"⏭️ Saltando procesamiento de productos (ya creados desde cotización)")
    elif productos_payload and isinstance(productos_payload, list):
        print(f"📦 Procesando {len(productos_payload)} líneas del payload")

        from collections import defaultdict

        # --- Agrupar Ticket_Productos existentes por SKU (lista, no dict) ---
        existentes_por_sku = defaultdict(list)
        pt_por_sku = {}
        for tp in ticket.ticket_productos.select_related('ProductoTalla', 'ProductoTalla__producto').all():
            if tp.ProductoTalla is not None:
                existentes_por_sku[tp.ProductoTalla.sku].append(tp)
                pt_por_sku[tp.ProductoTalla.sku] = tp.ProductoTalla

        # --- Validar stock total por SKU (solo cuando la cantidad total cambia) ---
        cantidades_payload_sku = defaultdict(int)
        for pd in productos_payload:
            s = pd.get('sku', '')
            if s:
                cantidades_payload_sku[s] += int(pd.get('cantidad', 1))

        for sku_val, cant_payload in cantidades_payload_sku.items():
            cant_existente = sum(tp.stock for tp in existentes_por_sku.get(sku_val, []))
            if cant_payload > cant_existente:
                pt = pt_por_sku.get(sku_val)
                if not pt:
                    pt = Producto_Talla.objects.filter(
                        sku=sku_val, producto__sucursal_id=ticket.sucursal_id
                    ).select_related('producto').first()
                if pt:
                    stock_real = pt.stock_sucursal(ticket.sucursal_id)
                    if cant_payload > stock_real:
                        print(f"  ❌ Stock insuficiente total SKU {sku_val}: solicitado={cant_payload}, disponible={stock_real}")
                        return JsonResponse({
                            'success': False,
                            'error': f'Stock insuficiente para SKU {sku_val} ({pt.producto.articulo}). '
                                     f'Disponible: {stock_real}, Solicitado: {cant_payload}.',
                            'error_tipo': 'STOCK_INSUFICIENTE',
                            'sku': str(sku_val),
                            'stock_disponible': stock_real,
                            'stock_requerido': cant_payload,
                        }, status=400)

        ids_existentes_usados = set()

        for prod_data in productos_payload:
            sku = prod_data.get('sku', '')
            if not sku:
                continue

            cantidad = int(prod_data.get('cantidad', 1))
            precio_unitario = int(prod_data.get('precio_unitario', 0))
            precio_original_payload = int(prod_data.get('precio_original', precio_unitario))
            descuento_unitario = int(prod_data.get('descuento_unitario', 0))
            subtotal = int(prod_data.get('subtotal', cantidad * precio_unitario))
            porcentaje_descuento = round((descuento_unitario / precio_unitario) * 100, 2) if precio_unitario > 0 and descuento_unitario > 0 else 0

            # Buscar un TP existente del mismo SKU que aún no haya sido emparejado
            tp_match = None
            candidatos = existentes_por_sku.get(sku, [])
            for tp_c in candidatos:
                if tp_c.id not in ids_existentes_usados:
                    tp_match = tp_c
                    break

            if tp_match:
                ids_existentes_usados.add(tp_match.id)
                algo_cambio = (
                    tp_match.stock != cantidad
                    or tp_match.precio != precio_unitario
                    or tp_match.descuento_unitario != descuento_unitario
                    or tp_match.subtotal != subtotal
                    or tp_match.precio_original != precio_original_payload
                )
                if algo_cambio:
                    if precio_unitario != precio_original_payload:
                        print(f"  📝 Actualizando línea #{tp_match.id} SKU {sku}: cant={tp_match.stock}→{cantidad}, precio={tp_match.precio}→{precio_unitario} (original: {precio_original_payload})")
                    else:
                        print(f"  📝 Actualizando línea #{tp_match.id} SKU {sku}: cant={tp_match.stock}→{cantidad}, dcto={tp_match.descuento_unitario}→{descuento_unitario}")
                    tp_match.stock = cantidad
                    tp_match.precio = precio_unitario
                    tp_match.precio_original = precio_original_payload
                    tp_match.descuento_unitario = descuento_unitario
                    tp_match.porcentaje_descuento = porcentaje_descuento
                    tp_match.subtotal = subtotal
                    tp_match.save()
            else:
                producto_talla_id = prod_data.get('producto_talla_id')
                producto_talla = None
                if producto_talla_id:
                    producto_talla = Producto_Talla.objects.filter(id=producto_talla_id).first()
                if not producto_talla:
                    producto_talla = Producto_Talla.objects.filter(sku=sku).first()

                if producto_talla:
                    print(f"  ➕ Creando nueva línea SKU {sku}: cantidad={cantidad}, precio={precio_unitario}, precio_original={precio_original_payload}")
                    tp_nuevo = Ticket_Productos.objects.create(
                        idTicket=ticket,
                        ProductoTalla=producto_talla,
                        stock=cantidad,
                        precio=precio_unitario,
                        precio_original=precio_original_payload,
                        descuento_unitario=descuento_unitario,
                        subtotal=subtotal,
                        porcentaje_descuento=porcentaje_descuento
                    )
                    ids_existentes_usados.add(tp_nuevo.id)
                else:
                    print(f"  ⚠️ ProductoTalla no encontrado para SKU {sku}")

        # Eliminar líneas huérfanas (existían en DB pero ya no están en el payload)
        for sku_list in existentes_por_sku.values():
            for tp_orphan in sku_list:
                if tp_orphan.id not in ids_existentes_usados:
                    print(f"  🗑️ Eliminando línea huérfana #{tp_orphan.id} SKU {tp_orphan.ProductoTalla.sku}")
                    tp_orphan.delete()

        # Recalcular totales del ticket — authoritative server-side calc
        todas_lineas = list(ticket.ticket_productos.all())
        nuevo_descuento_prod = sum((tp.descuento_unitario or 0) * tp.stock for tp in todas_lineas)

        # Recalculate each line's subtotal so we never trust a faulty
        # frontend value (e.g. JS `0 || gross` gives gross instead of 0).
        for tp in todas_lineas:
            correcto = (tp.precio - (tp.descuento_unitario or 0)) * tp.stock
            if tp.subtotal != correcto:
                print(f"  🔧 Corrigiendo subtotal SKU {tp.ProductoTalla.sku if tp.ProductoTalla else '?'}: {tp.subtotal} → {correcto}")
                tp.subtotal = correcto
                tp.save(update_fields=['subtotal'])

        nuevo_subtotal = sum(tp.subtotal for tp in todas_lineas)
        if nuevo_descuento_prod > 0:
            ticket.descuento = nuevo_descuento_prod
            ticket.total = nuevo_subtotal
        else:
            descuento_previo = ticket.descuento or 0
            ticket.total = nuevo_subtotal - descuento_previo
        print(f"  💰 Nuevo total del ticket: ${ticket.total:,} (descuento: ${ticket.descuento or 0:,})")

    pagos = payload.get('pagos') or []
    ids_existentes = list(ticket.pagos.values_list('id', flat=True))

    # Procesar pagos (sin transaction.atomic anidado para evitar TransactionManagementError)
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
                numero_orden_compra=pago.get('numero_orden_compra'),
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
                numero_orden_compra=pago.get('numero_orden_compra'),
                monto=monto,
                notas=pago.get('notas', ''),
            )

    if ids_existentes:
        TicketDetallePago.objects.filter(id__in=ids_existentes, ticket=ticket).delete()
    
    # Verificar si el ticket cambió de PENDIENTE a PAGADO
    estado_anterior_obj = Ticket.objects.get(id=ticket.id)
    estado_anterior = estado_anterior_obj.estado
    ticket_se_pago = (estado_anterior == 'PENDIENTE' and ticket.estado == 'PAGADO')

    ticket.save()

    # Si el ticket se acaba de pagar, consumir stock FIFO y crear movimientos
    # ⚠️ IMPORTANTE: NO descontar stock si el ticket viene de CAMBIO_DEVOLUCION
    # porque el stock ya se ajustó al aprobar el cambio
    print(f"🔍 DEBUG PAGO: Ticket #{ticket.correlativo}, modulo_origen='{ticket.modulo_origen}', ticket_se_pago={ticket_se_pago}")
    
    if ticket_se_pago and ticket.modulo_origen != 'CAMBIO_DEVOLUCION':
        print(f"🔍 DEBUG: Iniciando descuento de stock para ticket #{ticket.correlativo}")
        for tp in ticket.ticket_productos.all():
            # Saltar ítems sin ProductoTalla (pendientes de despacho)
            if tp.ProductoTalla is None:
                print(f"⏭️ Ítem manual sin stock: {tp.descripcion_linea} x{tp.stock} — sin descuento")
                continue

            # Usar stock_sucursal para obtener el stock real de la sucursal
            stock_antes = tp.ProductoTalla.stock_sucursal(ticket.sucursal_id)
            print(f"🔍 DEBUG: SKU {tp.ProductoTalla.sku} - Stock ANTES: {stock_antes}, A descontar: {tp.stock}")
            
            # Verificar que hay stock disponible en la sucursal
            if stock_antes < tp.stock:
                error_msg = f'Stock insuficiente para SKU {tp.ProductoTalla.sku}. Disponible: {stock_antes}, Requerido: {tp.stock}'
                print(f"❌ {error_msg}")
                # Revertir estado del ticket a PENDIENTE
                ticket.estado = 'PENDIENTE'
                ticket.save()
                return JsonResponse({
                    'success': False, 
                    'error': error_msg,
                    'error_tipo': 'STOCK_INSUFICIENTE',
                    'sku': str(tp.ProductoTalla.sku),
                    'stock_disponible': stock_antes,
                    'stock_requerido': tp.stock
                }, status=400)
            
            # Intentar consumir stock FIFO
            try:
                # Consumir stock FIFO (esto crea automáticamente el movimiento de EGRESO)
                # ✅ No pasar referencia_externa para que consumir_stock_fifo use DTE si está disponible
                consumir_stock_fifo(
                    producto_talla=tp.ProductoTalla,
                    cantidad_requerida=tp.stock,
                    responsable=request.user.username,
                    ticket=ticket,
                    observaciones=f'Pago de ticket #{ticket.correlativo}',
                    referencia_externa=None  # Dejamos que consumir_stock_fifo determine la referencia correcta
                )
                
                # Recargar para ver el stock actualizado
                tp.ProductoTalla.refresh_from_db()
                stock_despues = tp.ProductoTalla.stock
                print(f"✓ Stock consumido FIFO: SKU {tp.ProductoTalla.sku} - Stock ANTES: {stock_antes}, Stock DESPUÉS: {stock_despues}, Diferencia: {stock_antes - stock_despues}")
                
            except Exception as e:
                print(f"❌ Error FIFO para {tp.ProductoTalla.sku}: {str(e)}")
                print(f"🔍 Verificando stock después del error FIFO...")
                
                # Recargar stock para verificar si FIFO ya lo descontó
                tp.ProductoTalla.refresh_from_db()
                stock_despues_error = tp.ProductoTalla.stock
                print(f"🔍 Stock después del error: {stock_despues_error}")
                
                # ⚠️ CRÍTICO: Solo descontar manualmente si FIFO NO descontó
                if stock_despues_error == stock_antes:
                    print(f"✓ FIFO no descontó, procediendo con descuento manual")
                    # Crear movimiento manual si falla FIFO
                    # ✅ Usar DTE si está disponible, si no usar correlativo del ticket
                    referencia = f'DTE_{ticket.folio_dte}' if ticket.folio_dte else f'TICKET_{ticket.correlativo}'
                    Movimientos_Producto.objects.create(
                        ticket=ticket,
                        ProductoTalla=tp.ProductoTalla,
                        sucursal_origen=ticket.sucursal,
                        cantidad=-tp.stock,  # Negativo para egreso
                        costo=tp.ProductoTalla.producto.costo,
                        precio=tp.precio,
                        sobreprecio=tp.ProductoTalla.producto.sobreprecio if hasattr(tp.ProductoTalla.producto, 'sobreprecio') else 0,
                        concepto='VENTA_DIRECTA',
                        tipo_movimiento='EGRESO',
                        responsable=request.user.username,
                        observaciones=f'Venta ticket #{ticket.correlativo} - Consumo manual (FIFO no disponible)',
                        referencia_externa=referencia,
                        fecha=timezone.localdate(),
                        hora=timezone.localtime().time()
                    )
                    # Actualizar stock manualmente
                    tp.ProductoTalla.stock -= tp.stock
                    tp.ProductoTalla.save()
                    print(f"⚠ Stock consumido manualmente: {tp.ProductoTalla.sku} -{tp.stock} (Stock: {stock_antes} → {tp.ProductoTalla.stock})")
                else:
                    print(f"⚠️ ADVERTENCIA: FIFO ya descontó parcialmente. Stock: {stock_antes} → {stock_despues_error}")
                    # No descontar de nuevo, solo crear movimiento de registro
                    # ✅ Usar DTE si está disponible, si no usar correlativo del ticket
                    referencia = f'DTE_{ticket.folio_dte}' if ticket.folio_dte else f'TICKET_{ticket.correlativo}'
                    Movimientos_Producto.objects.create(
                        ticket=ticket,
                        ProductoTalla=tp.ProductoTalla,
                        sucursal_origen=ticket.sucursal,
                        cantidad=-tp.stock,
                        costo=tp.ProductoTalla.producto.costo,
                        precio=tp.precio,
                        sobreprecio=tp.ProductoTalla.producto.sobreprecio if hasattr(tp.ProductoTalla.producto, 'sobreprecio') else 0,
                        concepto='VENTA_DIRECTA',
                        tipo_movimiento='EGRESO',
                        responsable=request.user.username,
                        observaciones=f'Venta ticket #{ticket.correlativo} - Movimiento de registro (FIFO parcial)',
                        referencia_externa=referencia,
                        fecha=timezone.localdate(),
                        hora=timezone.localtime().time()
                    )
                    print(f"✓ Movimiento de registro creado sin descuento adicional")
    elif ticket_se_pago and ticket.modulo_origen == 'CAMBIO_DEVOLUCION':
        print(f"ℹ️  TICKET DE CAMBIO/DEVOLUCIÓN #{ticket.correlativo}: Stock ya fue ajustado al aprobar el cambio. No se descuenta nuevamente.")
        
        # Auto-completar el CambioDevolucion asociado
        cambio_asociado = CambioDevolucion.objects.filter(
            ticket_nuevo=ticket,
            estado__in=['EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE']
        ).first()
        
        if not cambio_asociado:
            cambio_asociado = CambioDevolucion.objects.filter(
                ticket_diferencia=ticket,
                estado__in=['EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE']
            ).first()
        
        if cambio_asociado:
            estado_anterior_cd = cambio_asociado.estado
            cambio_asociado.estado = 'COMPLETADO'
            cambio_asociado.fecha_completado = timezone.now()
            if estado_anterior_cd == 'EJECUTADO_COBRO_PENDIENTE':
                cambio_asociado.fecha_pago_diferencia = timezone.now()
            cambio_asociado.save()
            
            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio_asociado,
                accion='COMPLETADO_AUTO',
                estado_anterior=estado_anterior_cd,
                estado_nuevo='COMPLETADO',
                usuario=request.user,
                descripcion=f'Cambio completado automáticamente al procesar pago del ticket #{ticket.correlativo}.',
                datos_adicionales={
                    'ticket_pagado': ticket.correlativo,
                    'monto_pagado': float(ticket.total),
                }
            )
            print(f"✅ CambioDevolucion #{cambio_asociado.numero_operacion} completado automáticamente (era {estado_anterior_cd})")
    
    # Guardar o actualizar cliente en la base de datos si tiene datos
    if datos_cliente and datos_cliente.get('rut') and datos_cliente.get('nombre'):
        guardar_o_actualizar_cliente(datos_cliente, request.user)
    
    # Generar DTE si el tipo de documento lo requiere
    tipo_documento_seleccionado = payload.get('tipo_documento', '')
    dte_generado = None
    
    if tipo_documento_seleccionado in ['BOLETA_ELECTRONICA', 'BOLETA_PAPEL', 'FACTURA_ELECTRONICA'] and ticket.estado == 'PAGADO':
        try:
            print(f"🔍 DEBUG: Generando DTE para ticket #{ticket.correlativo}")
            
            # ✅ CRÍTICO: Refrescar el ticket desde la BD para tener los pagos actualizados
            ticket.refresh_from_db()
            
            # Verificar pagos ANTES de generar DTE
            pagos_count = ticket.pagos.count()
            print(f"🔍 DEBUG: Ticket tiene {pagos_count} pago(s) registrado(s)")
            for pago in ticket.pagos.all():
                print(f"  - {pago.metodo_pago}: ${pago.monto:,}")
            
            # Verificar stock ANTES de generar DTE
            for tp in ticket.ticket_productos.all():
                if tp.ProductoTalla is None:
                    continue
                print(f"🔍 DEBUG PRE-DTE: SKU {tp.ProductoTalla.sku} - Stock: {tp.ProductoTalla.stock}")
            
            # ✅ Pasar cotizacion_obj para usar sus descripciones en el TXT
            dte_generado = generar_dte_desde_ticket(ticket, tipo_documento_seleccionado, request.user, cotizacion=cotizacion_obj)
            print(f"✓ DTE generado: {dte_generado.tipo_documento} #{dte_generado.numero_documento}")
            
            # Verificar stock DESPUÉS de generar DTE
            for tp in ticket.ticket_productos.all():
                if tp.ProductoTalla is None:
                    continue
                tp.ProductoTalla.refresh_from_db()
                print(f"🔍 DEBUG POST-DTE: SKU {tp.ProductoTalla.sku} - Stock: {tp.ProductoTalla.stock}")
            
            # =========================================================================
            # NUEVO: Marcar cotización como facturada si viene de una cotización
            # =========================================================================
            if cotizacion_obj and dte_generado:
                try:
                    from .models import Historial_Cotizacion
                    
                    # Usar solo el número de documento (el campo numero_factura tiene max_length=20)
                    numero_documento_corto = str(dte_generado.numero_documento)[:20]
                    numero_documento_completo = f"{dte_generado.tipo_documento} #{dte_generado.numero_documento}"
                    cotizacion_obj.marcar_como_facturada(numero_documento_corto)
                    
                    # Registrar en historial
                    Historial_Cotizacion.objects.create(
                        cotizacion=cotizacion_obj,
                        usuario=request.user,
                        accion='FACTURADA',
                        descripcion=f'Cotización facturada desde POS. Documento: {numero_documento_completo}. Ticket: #{ticket.correlativo}',
                        ip_address=request.META.get('REMOTE_ADDR', '')
                    )
                    print(f"✅ Cotización {cotizacion_obj.numero_cotizacion} marcada como facturada")
                except Exception as cot_error:
                    print(f"⚠️ Error al marcar cotización como facturada: {str(cot_error)}")
                
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
        
        # Incluir datos del archivo TXT si se generó
        if hasattr(dte_generado, 'archivo_txt_data') and dte_generado.archivo_txt_data:
            response_data['archivo_txt'] = dte_generado.archivo_txt_data
    
    # Incluir info de cotización si fue facturada desde una cotización
    if cotizacion_obj:
        response_data['cotizacion_facturada'] = {
            'id': cotizacion_obj.id,
            'numero_cotizacion': cotizacion_obj.numero_cotizacion,
            'numero_factura': cotizacion_obj.numero_factura
        }
    
    print(f"🔍🔍🔍 DEBUG: ===== FIN registrar_pagos_ticket - Ticket #{correlativo} =====")
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

    user_rol = getattr(request.user, 'rol', '') or ''
    es_admin = user_rol == 'administrador'

    context = {
        'sucursal_actual': sucursal_actual,
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
        'estado_ticket_choices': ESTADO_TICKET_CHOICES,
        'tipo_documento_choices': TIPO_DOCUMENTO_CHOICES,
        'qz_config': _get_qz_config(sucursal_actual_id),
        'user_rol': user_rol,
        'es_admin': es_admin,
    }
    return render(request, 'vistas/modulo_ventas/gestionVentasDocumentos.html', context)


@login_required
@require_GET
def listar_documentos_ventas(request):
    """API para listar documentos de ventas (tickets, boletas, facturas)"""
    try:
        # Función helper para convertir códigos de método de pago a nombres legibles
        def obtener_nombre_metodo_pago(codigo):
            """Convierte el código del método de pago a un nombre legible"""
            nombres_metodos = {
                'EFECTIVO': 'Efectivo',
                'TARJETA_DEBITO': 'Tarjeta Débito',
                'TARJETA_CREDITO': 'Tarjeta Crédito',
                'TRANSFERENCIA': 'Transferencia',
                'CHEQUE': 'Cheque',
                'OTRO': 'Otro',
                'TBK_POS_INTEGRADO': 'Transbank POS',
                'TBK_MANUAL': 'Transbank Manual',
                'TBK_DEBITO_POS': 'TBK Débito POS',
                'TBK_CREDITO_POS': 'TBK Crédito POS',
                'TBK_PREPAGO_POS': 'TBK Prepago POS',
                'TARJETA_COMERCIAL': 'Tarjeta Comercial',
                'VENTA_INTERNET': 'Venta por Internet',
                'ORDEN_COMPRA': 'Orden de Compra',
                'CREDITO_TRABAJADOR': 'Crédito Trabajador',
                'CREDITO_EXTERNO': 'Crédito Externo',
            }
            return nombres_metodos.get(codigo, codigo)

        def agrupar_metodos_pago(pagos):
            """Agrupa pagos por método y suma montos."""
            agrupados = {}
            for pago in pagos:
                metodo = pago.get('metodo') or ''
                metodo_display = pago.get('metodo_display') or metodo
                tipo_tarjeta = pago.get('tipo_tarjeta') or ''
                key = (metodo, metodo_display, tipo_tarjeta)
                if key not in agrupados:
                    agrupados[key] = {
                        'metodo': metodo,
                        'metodo_display': metodo_display,
                        'monto': 0,
                        'voucher': '',
                        'tipo_tarjeta': tipo_tarjeta,
                        'notas': '',
                        '_vouchers': set(),
                        '_notas': set(),
                    }
                agrupados[key]['monto'] += pago.get('monto') or 0
                if pago.get('voucher'):
                    agrupados[key]['_vouchers'].add(str(pago['voucher']))
                if pago.get('notas'):
                    agrupados[key]['_notas'].add(str(pago['notas']))

            resultado = []
            for item in agrupados.values():
                if item['_vouchers']:
                    item['voucher'] = ', '.join(sorted(item['_vouchers']))
                if item['_notas']:
                    item['notas'] = ' | '.join(sorted(item['_notas']))
                item.pop('_vouchers', None)
                item.pop('_notas', None)
                resultado.append(item)
            return resultado
        
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
        monto_min_raw = request.GET.get('monto_min', '').strip()
        monto_max_raw = request.GET.get('monto_max', '').strip()
        monto_min = int(monto_min_raw) if monto_min_raw.isdigit() else None
        monto_max = int(monto_max_raw) if monto_max_raw.isdigit() else None

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
            elif tipo_documento == 'BOLETA_PAPEL':
                dtes_filtrados = dtes_filtrados.filter(tipo_documento='BOLETA PAPEL')
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
        
        # Filtro por rango de monto
        if monto_min is not None:
            dtes_filtrados = dtes_filtrados.filter(monto_con_iva__gte=monto_min)
        if monto_max is not None:
            dtes_filtrados = dtes_filtrados.filter(monto_con_iva__lte=monto_max)
        
        # Procesar DTEs filtrados
        for dte in dtes_filtrados:
            # Obtener productos del DTE
            productos = []
            subtotal_bruto = 0
            for dp in dte.dte_productos.all():
                linea_subtotal = dp.precio * dp.stock
                subtotal_bruto += linea_subtotal
                dcto_monto = int(dp.descuento_monto or 0)
                productos.append({
                    'sku': dp.productoTalla.sku if dp.productoTalla else '',
                    'nombre': dp.productoTalla.producto.articulo if dp.productoTalla and dp.productoTalla.producto else dp.descripcion,
                    'talla': dp.productoTalla.talla if dp.productoTalla else '',
                    'cantidad': dp.stock,
                    'precio_unitario': dp.precio,
                    'subtotal': linea_subtotal,
                    'descuento_monto': dcto_monto,
                    'monto_item': dp.monto_item or (linea_subtotal - dcto_monto),
                    'costo': dp.costo,
                    'sobreprecio': dp.sobreprecio,
                })
            
            # Obtener métodos de pago y sumar lo cobrado
            metodos_pago_raw = []
            total_pagos = 0
            for pago in dte.dte_asociado.all():
                total_pagos += pago.monto or 0
                metodos_pago_raw.append({
                    'metodo': pago.metodo_pago,
                    'metodo_display': obtener_nombre_metodo_pago(pago.metodo_pago),
                    'monto': pago.monto,
                    'voucher': pago.voucher or '',
                    'tipo_tarjeta': pago.tipo_tarjeta or '',
                    'notas': getattr(pago, 'notas', ''),
                })
            metodos_pago = agrupar_metodos_pago(metodos_pago_raw)

            monto_lista = int(dte.monto_con_iva or 0)
            total_real = total_pagos if total_pagos > 0 else monto_lista

            # Descuento efectivo (prioridad: campo guardado → diferencia con pagos → diferencia con productos)
            descuento_guardado = int(dte.descuento or 0)
            if descuento_guardado > 0:
                descuento_efectivo = descuento_guardado
            elif total_pagos > 0 and total_pagos < monto_lista:
                descuento_efectivo = monto_lista - total_pagos
            else:
                descuento_efectivo = max(0, subtotal_bruto - monto_lista)
            
            # Mapear estado DTE
            estado_display = 'PAGADO' if dte.estado_dte == 'EMITIDO' else dte.estado_dte
            
            # Crear datetime con zona horaria para DTEs
            from datetime import time as dt_time
            fecha_dt = timezone.datetime.combine(dte.fecha_emision, dt_time.min)
            created_at_dte = timezone.make_aware(fecha_dt) if timezone.is_naive(fecha_dt) else fecha_dt
            
            # Mapear tipo de documento para mostrar correctamente
            if dte.tipo_documento == 'BOLETA ELECTRONICA':
                tipo_display = 'BOLETA ELECTRONICA'
            elif dte.tipo_documento == 'BOLETA PAPEL':
                tipo_display = 'BOLETA PAPEL'
            elif dte.tipo_documento == 'FACTURA ELECTRONICA':
                tipo_display = 'FACTURA ELECTRONICA'
            elif dte.tipo_documento == 'FACTURA EXENTA':
                tipo_display = 'FACTURA EXENTA'
            else:
                tipo_display = dte.tipo_documento
            
            # Generar string de métodos de pago con información adicional
            metodos_pago_str_list = []
            for p in metodos_pago:
                texto_pago = p['metodo_display']
                # Agregar tipo de tarjeta si existe (para tarjetas de crédito/débito o plataforma para internet)
                if p['tipo_tarjeta']:
                    if p['metodo'] == 'VENTA_INTERNET':
                        texto_pago += f" ({p['tipo_tarjeta']})"
                    elif 'TARJETA' in p['metodo']:
                        texto_pago += f" ({p['tipo_tarjeta']})"
                metodos_pago_str_list.append(texto_pago)
            
            metodos_pago_str = ', '.join(metodos_pago_str_list) if metodos_pago_str_list else 'Sin pagos'
            
            documentos_data.append({
                'id': dte.id,
                'tipo': tipo_display,
                'tipo_documento': dte.tipo_documento,  # Campo adicional con el valor original
                'numero': dte.numero_documento,
                'fecha': dte.fecha_emision,
                'cliente_nombre': dte.receptor.nombre if dte.receptor else 'Sin nombre',
                'cliente_rut': dte.receptor.rut if dte.receptor else '',
                'cliente_giro': dte.receptor.giro if dte.receptor else '',
                'cliente_email': dte.receptor.correoVendedor if dte.receptor else '',
                'cliente_direccion': dte.receptor.direccion if dte.receptor else '',
                'cliente_comuna': dte.receptor.comuna if dte.receptor else '',
                'vendedor_nombre': f"{dte.vendedor.codigo_vendedor} - {dte.vendedor.nombre}" if dte.vendedor else 'Sin vendedor',
                'total': total_real,
                'subtotal_bruto': subtotal_bruto,
                'monto_neto': int(dte.monto_neto or 0),
                'descuento': descuento_efectivo,
                'estado': estado_display,
                'created_at': created_at_dte,
                'productos': productos,
                'metodos_pago': metodos_pago,
                'total_productos': len(productos),
                'metodos_pago_str': metodos_pago_str,
            })

        # Obtener parámetros de ordenamiento
        orden_campo = request.GET.get('orden_campo', 'fecha')
        orden_direccion = request.GET.get('orden_direccion', 'desc')
        reverse_order = (orden_direccion == 'desc')
        
        # Mapeo de campos de ordenamiento
        orden_map = {
            'fecha': 'created_at',
            'tipo_documento': 'tipo',
            'numero_documento': 'numero',
            'cliente_nombre': 'cliente_nombre',
            'vendedor_nombre': 'vendedor_nombre',
            'total': 'total',
            'estado': 'estado',
        }
        
        campo_ordenar = orden_map.get(orden_campo, 'created_at')
        
        # Ordenar documentos
        try:
            if campo_ordenar == 'total':
                documentos_data.sort(key=lambda x: x.get(campo_ordenar, 0) or 0, reverse=reverse_order)
            elif campo_ordenar == 'numero':
                documentos_data.sort(key=lambda x: int(x.get(campo_ordenar, 0) or 0), reverse=reverse_order)
            else:
                documentos_data.sort(key=lambda x: str(x.get(campo_ordenar, '') or '').lower(), reverse=reverse_order)
        except (TypeError, ValueError) as e:
            # Si hay problemas de comparación, ordenar por fecha como fallback
            print(f"Error al ordenar por {campo_ordenar}: {e}")
            documentos_data.sort(key=lambda x: x['created_at'], reverse=True)

        # Paginación manual
        total_documentos = len(documentos_data)
        inicio = (page - 1) * per_page
        fin = inicio + per_page
        documentos_paginados = documentos_data[inicio:fin]

        # Calcular estadísticas (solo DTEs)
        total_ventas = sum(doc['total'] for doc in documentos_data)
        total_pendientes = len([doc for doc in documentos_data if doc['estado'] == 'PENDIENTE'])
        total_facturas = len([doc for doc in documentos_data if 'FACTURA' in doc['tipo']])
        total_boletas = len([doc for doc in documentos_data if 'BOLETA' in doc['tipo']])
        total_boletas_electronicas = len([doc for doc in documentos_data if doc['tipo'] == 'BOLETA ELECTRONICA'])
        total_boletas_papel = len([doc for doc in documentos_data if doc['tipo'] == 'BOLETA PAPEL'])

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
                'total_boletas_electronicas': total_boletas_electronicas,
                'total_boletas_papel': total_boletas_papel,
            }
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener documentos: {str(e)}'
        })


@login_required
@require_GET
def exportar_documentos_ventas_excel(request):
    """
    API para exportar documentos de ventas (DTEs) a Excel
    Utiliza los mismos filtros que listar_documentos_ventas
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        
        # Función helper para convertir códigos de método de pago
        def obtener_nombre_metodo_pago(codigo):
            nombres_metodos = {
                'EFECTIVO': 'Efectivo',
                'TARJETA_DEBITO': 'Tarjeta Débito',
                'TARJETA_CREDITO': 'Tarjeta Crédito',
                'TRANSFERENCIA': 'Transferencia',
                'CHEQUE': 'Cheque',
                'OTRO': 'Otro',
                'TBK_POS_INTEGRADO': 'Transbank POS',
                'TBK_MANUAL': 'Transbank Manual',
                'TBK_DEBITO_POS': 'TBK Débito POS',
                'TBK_CREDITO_POS': 'TBK Crédito POS',
                'TBK_PREPAGO_POS': 'TBK Prepago POS',
                'TARJETA_COMERCIAL': 'Tarjeta Comercial',
                'VENTA_INTERNET': 'Venta por Internet',
                'ORDEN_COMPRA': 'Orden de Compra',
                'CREDITO_TRABAJADOR': 'Crédito Trabajador',
                'CREDITO_EXTERNO': 'Crédito Externo',
            }
            return nombres_metodos.get(codigo, codigo)
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay sucursal seleccionada'
            })
        
        # Obtener nombre de sucursal
        try:
            sucursal = Sucursal.objects.get(id=sucursal_id)
            sucursal_nombre = sucursal.alias or sucursal.nombre
        except Sucursal.DoesNotExist:
            sucursal_nombre = 'Sucursal'
        
        # Parámetros de filtro (los mismos que listar_documentos_ventas)
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        tipo_documento = request.GET.get('tipo_documento')
        estado = request.GET.get('estado')
        metodo_pago = request.GET.get('metodo_pago')
        buscar = request.GET.get('buscar', '').strip()
        
        # Parámetros de ordenamiento
        orden_campo = request.GET.get('orden_campo', 'fecha')
        orden_direccion = request.GET.get('orden_direccion', 'desc')
        
        # Query base de DTEs
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
        if tipo_documento:
            if tipo_documento == 'BOLETA_ELECTRONICA':
                dtes_query = dtes_query.filter(tipo_documento='BOLETA ELECTRONICA')
            elif tipo_documento == 'BOLETA_PAPEL':
                dtes_query = dtes_query.filter(tipo_documento='BOLETA PAPEL')
            elif tipo_documento == 'FACTURA_ELECTRONICA':
                dtes_query = dtes_query.filter(tipo_documento='FACTURA ELECTRONICA')
            elif tipo_documento == 'FACTURA_EXENTA':
                dtes_query = dtes_query.filter(tipo_documento='FACTURA EXENTA')
        
        # Filtrar por estado
        if estado:
            estado_dte_map = {
                'PENDIENTE': 'PENDIENTE',
                'PAGADO': 'EMITIDO',
                'ANULADO': 'ANULADO'
            }
            if estado in estado_dte_map:
                dtes_query = dtes_query.filter(estado_dte=estado_dte_map[estado])
        
        # Filtrar por método de pago
        if metodo_pago:
            dtes_query = dtes_query.filter(dte_asociado__metodo_pago=metodo_pago).distinct()
        
        # Filtro de búsqueda
        if buscar:
            dtes_query = dtes_query.filter(
                Q(numero_documento__icontains=buscar) |
                Q(receptor__nombre__icontains=buscar) |
                Q(receptor__rut__icontains=buscar) |
                Q(vendedor__nombre__icontains=buscar) |
                Q(dte_productos__productoTalla__sku__icontains=buscar) |
                Q(dte_productos__productoTalla__producto__articulo__icontains=buscar)
            ).distinct()
        
        # Recolectar datos de documentos
        documentos_data = []
        for dte in dtes_query:
            # Obtener métodos de pago
            metodos_pago_list = []
            for pago in dte.dte_asociado.all():
                nombre_metodo = obtener_nombre_metodo_pago(pago.metodo_pago)
                if pago.tipo_tarjeta:
                    nombre_metodo += f" ({pago.tipo_tarjeta})"
                metodos_pago_list.append(nombre_metodo)
            metodos_pago_str = ', '.join(metodos_pago_list) if metodos_pago_list else 'Sin pagos'
            
            # Mapear estado DTE
            estado_display = 'PAGADO' if dte.estado_dte == 'EMITIDO' else dte.estado_dte
            
            # Crear datetime para ordenamiento
            from datetime import time as dt_time
            fecha_dt = timezone.datetime.combine(dte.fecha_emision, dt_time.min)
            created_at_dte = timezone.make_aware(fecha_dt) if timezone.is_naive(fecha_dt) else fecha_dt
            
            documentos_data.append({
                'fecha': dte.fecha_emision,
                'created_at': created_at_dte,
                'tipo': dte.tipo_documento,
                'numero': dte.numero_documento,
                'cliente_nombre': dte.receptor.nombre if dte.receptor else 'Sin nombre',
                'cliente_rut': dte.receptor.rut if dte.receptor else '',
                'vendedor_nombre': f"{dte.vendedor.codigo_vendedor} - {dte.vendedor.nombre}" if dte.vendedor else 'Sin vendedor',
                'neto': int(dte.monto_total or 0),
                'iva': int(dte.iva or 0),
                'total': int(dte.monto_con_iva or 0),
                'metodos_pago': metodos_pago_str,
                'estado': estado_display,
            })
        
        # Ordenar documentos
        orden_map = {
            'fecha': 'created_at',
            'tipo_documento': 'tipo',
            'numero_documento': 'numero',
            'cliente_nombre': 'cliente_nombre',
            'vendedor_nombre': 'vendedor_nombre',
            'total': 'total',
            'estado': 'estado',
        }
        campo_ordenar = orden_map.get(orden_campo, 'created_at')
        reverse_order = (orden_direccion == 'desc')
        
        try:
            if campo_ordenar == 'total':
                documentos_data.sort(key=lambda x: x.get(campo_ordenar, 0) or 0, reverse=reverse_order)
            elif campo_ordenar == 'numero':
                documentos_data.sort(key=lambda x: int(x.get(campo_ordenar, 0) or 0), reverse=reverse_order)
            else:
                documentos_data.sort(key=lambda x: str(x.get(campo_ordenar, '') or '').lower(), reverse=reverse_order)
        except (TypeError, ValueError):
            documentos_data.sort(key=lambda x: x['created_at'], reverse=True)
        
        # Crear Excel
        wb = Workbook()
        ws = wb.active
        ws.title = "Documentos de Ventas"
        
        # Estilos
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        title_font = Font(bold=True, size=14)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título
        ws['A1'] = f"DOCUMENTOS DE VENTAS - {sucursal_nombre.upper()}"
        ws['A1'].font = title_font
        ws.merge_cells('A1:K1')
        
        # Periodo
        periodo_texto = "Todos los documentos"
        if fecha_desde and fecha_hasta:
            periodo_texto = f"Período: {fecha_desde} al {fecha_hasta}"
        elif fecha_desde:
            periodo_texto = f"Desde: {fecha_desde}"
        elif fecha_hasta:
            periodo_texto = f"Hasta: {fecha_hasta}"
        ws['A2'] = periodo_texto
        ws['A2'].font = Font(italic=True)
        
        # Encabezados
        headers = [
            "Fecha", "Tipo Documento", "Número", "Cliente", "RUT", 
            "Vendedor", "Neto", "IVA", "Total", "Método Pago", "Estado"
        ]
        
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=4, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')
            cell.border = border
        
        # Datos
        total_neto = 0
        total_iva = 0
        total_general = 0
        
        for row_idx, doc in enumerate(documentos_data, 5):
            ws.cell(row=row_idx, column=1, value=doc['fecha'].strftime('%d/%m/%Y') if doc['fecha'] else '').border = border
            ws.cell(row=row_idx, column=2, value=doc['tipo']).border = border
            ws.cell(row=row_idx, column=3, value=doc['numero']).border = border
            ws.cell(row=row_idx, column=4, value=doc['cliente_nombre']).border = border
            ws.cell(row=row_idx, column=5, value=doc['cliente_rut']).border = border
            ws.cell(row=row_idx, column=6, value=doc['vendedor_nombre']).border = border
            
            cell_neto = ws.cell(row=row_idx, column=7, value=doc['neto'])
            cell_neto.number_format = '#,##0'
            cell_neto.border = border
            
            cell_iva = ws.cell(row=row_idx, column=8, value=doc['iva'])
            cell_iva.number_format = '#,##0'
            cell_iva.border = border
            
            cell_total = ws.cell(row=row_idx, column=9, value=doc['total'])
            cell_total.number_format = '#,##0'
            cell_total.border = border
            
            ws.cell(row=row_idx, column=10, value=doc['metodos_pago']).border = border
            ws.cell(row=row_idx, column=11, value=doc['estado']).border = border
            
            total_neto += doc['neto']
            total_iva += doc['iva']
            total_general += doc['total']
        
        # Fila de totales
        if documentos_data:
            row_totales = len(documentos_data) + 5
            ws.cell(row=row_totales, column=6, value="TOTALES:").font = Font(bold=True)
            
            cell_total_neto = ws.cell(row=row_totales, column=7, value=total_neto)
            cell_total_neto.number_format = '#,##0'
            cell_total_neto.font = Font(bold=True)
            
            cell_total_iva = ws.cell(row=row_totales, column=8, value=total_iva)
            cell_total_iva.number_format = '#,##0'
            cell_total_iva.font = Font(bold=True)
            
            cell_total_general = ws.cell(row=row_totales, column=9, value=total_general)
            cell_total_general.number_format = '#,##0'
            cell_total_general.font = Font(bold=True)
        
        # Ajustar anchos de columna
        column_widths = [12, 22, 12, 35, 15, 25, 12, 12, 12, 25, 12]
        for i, width in enumerate(column_widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        
        # Generar respuesta
        from io import BytesIO
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        # Nombre del archivo
        fecha_actual = timezone.now().strftime('%Y%m%d_%H%M%S')
        filename = f"documentos_ventas_{fecha_actual}.xlsx"
        
        response = HttpResponse(
            output.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar documentos: {str(e)}'
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
                fecha_emision=data.get('fecha_emision', timezone.localdate()),
                fecha_vencimiento=data.get('fecha_emision', timezone.localdate()),
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

            # Copiar productos del ticket a la factura (con descuentos)
            for ticket_producto in ticket.ticket_productos.all():
                costo_unitario = ticket_producto.ProductoTalla.producto.costo
                sobreprecio_unitario = ticket_producto.ProductoTalla.producto.sobreprecio
                
                dcto_u = ticket_producto.descuento_unitario or 0
                dcto_p = float(ticket_producto.porcentaje_descuento or 0)
                dcto_linea = dcto_u * ticket_producto.stock if dcto_u else 0

                Dte_Productos.objects.create(
                    dte=factura,
                    productoTalla=ticket_producto.ProductoTalla,
                    descripcion=f"{ticket_producto.ProductoTalla.producto.articulo} - {ticket_producto.ProductoTalla.talla}",
                    costo=costo_unitario,
                    sobreprecio=sobreprecio_unitario,
                    precio=ticket_producto.precio,
                    precio_unitario=ticket_producto.precio,
                    descuento_pct=dcto_p if dcto_p > 0 else None,
                    descuento_monto=dcto_linea if dcto_linea > 0 else None,
                    monto_item=ticket_producto.subtotal,
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
                    monto=pago_ticket.monto,
                    notas=pago_ticket.notas or ''
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

        def obtener_nombre_metodo_pago(codigo):
            nombres_metodos = {
                'EFECTIVO': 'Efectivo',
                'TARJETA_DEBITO': 'Tarjeta Débito',
                'TARJETA_CREDITO': 'Tarjeta Crédito',
                'TRANSFERENCIA': 'Transferencia',
                'CHEQUE': 'Cheque',
                'OTRO': 'Otro',
                'TBK_POS_INTEGRADO': 'Transbank POS',
                'TBK_MANUAL': 'Transbank Manual',
                'TBK_DEBITO_POS': 'TBK Débito POS',
                'TBK_CREDITO_POS': 'TBK Crédito POS',
                'TBK_PREPAGO_POS': 'TBK Prepago POS',
                'TARJETA_COMERCIAL': 'Tarjeta Comercial',
                'VENTA_INTERNET': 'Venta por Internet',
                'ORDEN_COMPRA': 'Orden de Compra',
                'CREDITO_TRABAJADOR': 'Crédito Trabajador',
                'CREDITO_EXTERNO': 'Crédito Externo',
            }
            return nombres_metodos.get(codigo, codigo)
        
        if tipo_documento == 'TICKET':
            documento = get_object_or_404(Ticket, id=documento_id)
            
            # Obtener productos del ticket
            productos = []
            for tp in documento.ticket_productos.select_related('ProductoTalla__producto').all():
                if tp.ProductoTalla:
                    productos.append({
                        'sku': tp.ProductoTalla.sku,
                        'nombre': tp.ProductoTalla.producto.articulo if tp.ProductoTalla.producto else (tp.descripcion_linea or ''),
                        'talla': tp.ProductoTalla.talla,
                        'cantidad': tp.stock,
                        'precio_unitario': tp.precio,
                        'subtotal': tp.subtotal,
                    })
                else:
                    productos.append({
                        'sku': '',
                        'nombre': tp.descripcion_linea or 'Ítem pendiente de despacho',
                        'talla': '',
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
            pagos_raw = []
            for pago in documento.dte_asociado.all():
                pagos_raw.append({
                    'metodo': pago.metodo_pago,
                    'metodo_display': obtener_nombre_metodo_pago(pago.metodo_pago),
                    'monto': pago.monto,
                    'voucher': pago.voucher or '',
                    'tipo_tarjeta': pago.tipo_tarjeta or '',
                    'notas': pago.notas or '',
                })

            # Agrupar pagos por método y sumar montos
            pagos = []
            agrupados = {}
            for pago in pagos_raw:
                metodo = pago.get('metodo') or ''
                metodo_display = pago.get('metodo_display') or metodo
                tipo_tarjeta = pago.get('tipo_tarjeta') or ''
                key = (metodo, metodo_display, tipo_tarjeta)
                if key not in agrupados:
                    agrupados[key] = {
                        'metodo': metodo,
                        'metodo_display': metodo_display,
                        'monto': 0,
                        'voucher': '',
                        'tipo_tarjeta': tipo_tarjeta,
                        'notas': '',
                        '_vouchers': set(),
                        '_notas': set(),
                    }
                agrupados[key]['monto'] += pago.get('monto') or 0
                if pago.get('voucher'):
                    agrupados[key]['_vouchers'].add(str(pago['voucher']))
                if pago.get('notas'):
                    agrupados[key]['_notas'].add(str(pago['notas']))

            for item in agrupados.values():
                if item['_vouchers']:
                    item['voucher'] = ', '.join(sorted(item['_vouchers']))
                if item['_notas']:
                    item['notas'] = ' | '.join(sorted(item['_notas']))
                item.pop('_vouchers', None)
                item.pop('_notas', None)
                pagos.append(item)
            
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
                        if tp.ProductoTalla is None:
                            continue  # Sin stock que devolver para ítems manuales
                        # Crear movimiento de devolución de stock
                        # ✅ Usar DTE si está disponible, si no usar correlativo del ticket
                        referencia = f'ANULACION_DTE_{documento.folio_dte}' if documento.folio_dte else f'ANULACION_TICKET_{documento.correlativo}'
                        Movimientos_Producto.objects.create(
                            ticket=documento,
                            ProductoTalla=tp.ProductoTalla,
                            cantidad=tp.stock,  # Cantidad positiva para devolver
                            costo=tp.ProductoTalla.producto.costo if tp.ProductoTalla.producto else 0,
                            precio=tp.precio,
                            concepto='DEVOLUCION_CLIENTE',
                            tipo_movimiento='INGRESO',
                            responsable=request.user.username,
                            observaciones=f'Anulación ticket #{documento.correlativo}',
                            referencia_externa=referencia
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


@login_required
@require_POST
def editar_dte_boleta_papel(request):
    """
    Permite a un administrador corregir la fecha de pago (fecha_emision)
    y el número de documento de una BOLETA PAPEL (boleta manual).

    Solo usuarios con rol 'administrador' pueden ejecutar esta acción.
    """
    try:
        if getattr(request.user, 'rol', '') != 'administrador':
            return JsonResponse({
                'success': False,
                'error': 'Solo administradores pueden editar boletas papel'
            }, status=403)

        data = json.loads(request.body)
        documento_id = data.get('documento_id')
        nuevo_numero = data.get('numero_documento')
        nueva_fecha = data.get('fecha_emision')

        if not documento_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de documento requerido'
            })

        try:
            nuevo_numero = int(nuevo_numero)
        except (TypeError, ValueError):
            return JsonResponse({
                'success': False,
                'error': 'Número de documento inválido'
            })

        if nuevo_numero <= 0:
            return JsonResponse({
                'success': False,
                'error': 'El número de documento debe ser un entero positivo'
            })

        from datetime import datetime as _dt
        try:
            fecha_parsed = _dt.strptime(str(nueva_fecha).strip(), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return JsonResponse({
                'success': False,
                'error': 'Fecha inválida. Formato esperado YYYY-MM-DD'
            })

        with transaction.atomic():
            dte = Dte.objects.select_for_update().filter(id=documento_id).first()
            if not dte:
                return JsonResponse({
                    'success': False,
                    'error': 'Documento no encontrado'
                })

            if dte.tipo_documento != 'BOLETA PAPEL':
                return JsonResponse({
                    'success': False,
                    'error': 'Solo se pueden editar documentos tipo BOLETA PAPEL'
                })

            if dte.estado_dte == 'ANULADO':
                return JsonResponse({
                    'success': False,
                    'error': 'No se puede editar una boleta anulada'
                })

            # Verificar duplicado de número en la misma sucursal (BOLETA PAPEL)
            if nuevo_numero != dte.numero_documento:
                existe_duplicado = Dte.objects.filter(
                    sucursal_id=dte.sucursal_id,
                    tipo_documento='BOLETA PAPEL',
                    numero_documento=nuevo_numero,
                ).exclude(id=dte.id).exists()
                if existe_duplicado:
                    return JsonResponse({
                        'success': False,
                        'error': f'Ya existe otra BOLETA PAPEL con el número {nuevo_numero} en esta sucursal'
                    })

            numero_anterior = dte.numero_documento
            fecha_anterior = dte.fecha_emision

            dte.numero_documento = nuevo_numero
            dte.fecha_emision = fecha_parsed
            # Para boletas papel el pago es al contado: vencimiento = emisión
            dte.fecha_vencimiento = fecha_parsed
            dte.save(update_fields=['numero_documento', 'fecha_emision', 'fecha_vencimiento'])

        return JsonResponse({
            'success': True,
            'message': 'Boleta papel actualizada correctamente',
            'documento': {
                'id': dte.id,
                'numero_documento': dte.numero_documento,
                'fecha_emision': dte.fecha_emision.strftime('%Y-%m-%d'),
                'numero_anterior': numero_anterior,
                'fecha_anterior': fecha_anterior.strftime('%Y-%m-%d') if fecha_anterior else None,
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
            'error': f'Error al editar boleta papel: {str(e)}'
        })


# ========== CUADRATURA Y ARQUEO DE CAJA ==========

@login_required
def revision_arqueos(request):
    """Vista de supervisión: revisión de arqueos, comprobantes bancarios, depósitos."""
    sucursal_actual_id = request.session.get('sucursalActual') or request.session.get('idSucursalActual')
    sucursal_actual = None
    if sucursal_actual_id:
        try:
            sucursal_actual = Sucursal.objects.get(id=sucursal_actual_id)
        except Sucursal.DoesNotExist:
            pass
    if not sucursal_actual:
        return redirect('dashboard')

    rol_usuario = getattr(request.user, 'rol', None)
    es_supervisor = rol_usuario in ['administrador', 'administracion']
    if not es_supervisor:
        return redirect('cuadratura_caja')

    return render(request, 'vistas/modulo_ventas/revisionArqueos.html', {
        'sucursal_actual': sucursal_actual,
    })


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
    
    # Obtener rol del usuario
    rol_usuario = getattr(request.user, 'rol', None)
    
    # Verificar si el usuario es administrador (para permisos de reabrir arqueos)
    es_administrador = rol_usuario == 'administrador'

    # Verificar si tiene permisos de supervisión (administrador o administración)
    es_supervisor = rol_usuario in ['administrador', 'administracion']

    # Cajero/vendedor/jefe_local: puede declarar depósitos pero no confirmarlos
    es_cajero = not es_supervisor and rol_usuario in ['cajero', 'vendedor', 'jefe_local']

    # Permiso de reabrir: administrador (siempre) o jefe_local/administracion (con tolerancia)
    puede_reabrir = rol_usuario in ['administrador', 'jefe_local', 'administracion']

    # Tolerancia de días para crear arqueos hacia atrás
    if rol_usuario in ('cajero', 'vendedor'):
        dias_tolerancia_arqueo = 2
    elif rol_usuario == 'jefe_local':
        dias_tolerancia_arqueo = 3
    else:
        dias_tolerancia_arqueo = 30  # admin/administración: hasta 30 días

    context = {
        'sucursal_actual': sucursal_actual,
        'es_administrador': es_administrador,
        'es_supervisor': es_supervisor,
        'es_cajero': es_cajero,
        'puede_reabrir': puede_reabrir,
        'rol_usuario': rol_usuario or 'sin_rol',
        'dias_tolerancia_arqueo': dias_tolerancia_arqueo,
        'qz_config': _get_qz_config(sucursal_actual_id),
    }
    return render(request, 'vistas/modulo_ventas/cuadraturaCaja.html', context)


def _calcular_cuadratura_data(sucursal, fecha_str):
    """
    Función helper para calcular datos de cuadratura.
    Puede ser usada tanto por el endpoint POST como por el exportador Excel.
    
    Args:
        sucursal: Instancia de Sucursal
        fecha_str: Fecha en formato 'YYYY-MM-DD'
    
    Returns:
        dict: Datos de cuadratura calculados
    """
    from datetime import datetime
    from datetime import time as dt_time
    
    # Convertir fecha string a date object
    fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    
    # Crear datetime para filtros con timezone aware
    inicio_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.min))
    fin_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.max))
    
    # Inicializar totales
    cuadratura_data = {
        'fecha_cuadratura': fecha_str,
        'total_efectivo': 0,
        'total_tarjeta_debito': 0,
        'total_tarjeta_credito': 0,
        'total_transbank': 0,
        # Tarjetas Comerciales (solo Hites)
        'total_hites': 0,
        'total_tarjetas_comerciales': 0,
        # Venta Internet (Falabella, Paris, Ripley, MercadoPago, Klap)
        'total_falabella': 0,
        'total_paris': 0,
        'total_ripley': 0,
        'total_mercadopago': 0,
        'total_klap': 0,
        'total_venta_internet': 0,
        # Otros
        'total_transferencia': 0,
        'total_cheque': 0,
        'total_convenio': 0,
        'total_credito_trabajador': 0,
        'total_credito_externo': 0,
        'total_orden_compra': 0,
        'total_nota_credito': 0,
        'total_descuentos': 0,  # Descuentos aplicados
        # Documentos
        'total_tickets': 0,
        'total_boletas': 0,
        'total_boletas_electronicas': 0,
        'total_boletas_papel': 0,
        'total_facturas': 0,
        'total_facturas_exentas': 0,
        'total_notas_credito': 0,
        'total_nc_efectivo': 0,
        'total_nc_transferencia': 0,
        'cantidad_notas_credito': 0,
        'cantidad_tickets': 0,
        'cantidad_boletas': 0,
        'cantidad_boletas_electronicas': 0,
        'cantidad_boletas_papel': 0,
        'cantidad_facturas': 0,
        'cantidad_facturas_exentas': 0,
        'venta_total': 0,
    }
    
    # ========== PROCESAR TICKETS ==========
    # Usar campo `fecha` (DateField, auto_now) en vez de `created_at` (DateTimeField)
    # porque `fecha` se actualiza al pagar el ticket, mientras que `created_at`
    # refleja cuando se creó (posiblemente como PENDIENTE en otro momento).
    tickets_del_dia = Ticket.objects.filter(
        sucursal=sucursal,
        fecha=fecha_obj,
        estado='PAGADO'
    ).prefetch_related('pagos')
    
    for ticket in tickets_del_dia:
        cuadratura_data['total_tickets'] += ticket.total or 0
        cuadratura_data['cantidad_tickets'] += 1
        
        # Procesar pagos del ticket
        for pago in ticket.pagos.all():
            metodo = pago.metodo_pago
            tipo_tarjeta = (pago.tipo_tarjeta or '').upper()
            monto = pago.monto or 0
            
            if metodo == 'EFECTIVO':
                cuadratura_data['total_efectivo'] += monto
            elif metodo == 'TARJETA_DEBITO':
                # ✅ TARJETA_DEBITO se considera Transbank (datos migrados y genéricos)
                cuadratura_data['total_tarjeta_debito'] += monto
                cuadratura_data['total_transbank'] += monto
            elif metodo == 'TARJETA_CREDITO':
                # ✅ TARJETA_CREDITO se considera Transbank (datos migrados y genéricos)
                cuadratura_data['total_tarjeta_credito'] += monto
                cuadratura_data['total_transbank'] += monto
            elif metodo == 'TBK_DEBITO_POS':
                # ✅ Transbank POS Débito
                cuadratura_data['total_tarjeta_debito'] += monto
                cuadratura_data['total_transbank'] += monto
            elif metodo == 'TBK_CREDITO_POS':
                # ✅ Transbank POS Crédito
                cuadratura_data['total_tarjeta_credito'] += monto
                cuadratura_data['total_transbank'] += monto
            elif metodo == 'TBK_PREPAGO_POS':
                # ✅ Transbank POS Prepago (va a débito por convención)
                cuadratura_data['total_tarjeta_debito'] += monto
                cuadratura_data['total_transbank'] += monto
            elif metodo == 'TBK_POS_INTEGRADO' or metodo == 'TBK_MANUAL':
                # ✅ Transbank genérico (datos históricos)
                cuadratura_data['total_transbank'] += monto
            elif metodo == 'TRANSFERENCIA':
                cuadratura_data['total_transferencia'] += monto
            elif metodo == 'CHEQUE':
                cuadratura_data['total_cheque'] += monto
            elif metodo == 'CONVENIO':
                cuadratura_data['total_convenio'] += monto
            elif metodo == 'CREDITO_TRABAJADOR':
                cuadratura_data['total_credito_trabajador'] += monto
            elif metodo == 'CREDITO_EXTERNO':
                cuadratura_data['total_credito_externo'] += monto
            elif metodo == 'ORDEN_COMPRA':
                cuadratura_data['total_orden_compra'] += monto
            elif metodo == 'TARJETA_COMERCIAL':
                # Clasificar por tipo_tarjeta
                cuadratura_data['total_tarjetas_comerciales'] += monto
                if 'HITES' in tipo_tarjeta:
                    cuadratura_data['total_hites'] += monto
            elif metodo == 'VENTA_INTERNET':
                cuadratura_data['total_venta_internet'] += monto
                # ✅ Clasificar por tipo_tarjeta (igual que con DTEs)
                if 'FALABELLA' in tipo_tarjeta:
                    cuadratura_data['total_falabella'] += monto
                elif 'PARIS' in tipo_tarjeta:
                    cuadratura_data['total_paris'] += monto
                elif 'RIPLEY' in tipo_tarjeta:
                    cuadratura_data['total_ripley'] += monto
                elif 'MERCADO' in tipo_tarjeta or 'MERCADOPAGO' in tipo_tarjeta:
                    cuadratura_data['total_mercadopago'] += monto
                elif 'KLAP' in tipo_tarjeta:
                    cuadratura_data['total_klap'] += monto
                else:
                    # Si no tiene tipo_tarjeta específico, va a MercadoPago por defecto
                    cuadratura_data['total_mercadopago'] += monto
    
    # ========== PROCESAR DTEs (FACTURAS/BOLETAS ELECTRÓNICAS) ==========
    # Obtener folios de DTEs que ya tienen ticket asociado para evitar duplicar pagos
    folios_tickets = Ticket.objects.filter(
        sucursal=sucursal,
        fecha=fecha_obj,
        folio_dte__isnull=False
    ).values_list('folio_dte', flat=True)
    
    dtes_del_dia = Dte.objects.filter(
        sucursal=sucursal,
        fecha_emision=fecha_obj,
        estado_dte__in=['EMITIDO', 'ACEPTADO'],
        tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO', 'DEVOLUCION']
    ).prefetch_related('dte_asociado')
    
    folios_tickets_set = set(folios_tickets)

    for dte in dtes_del_dia:
        monto_dte = dte.monto_con_iva or 0
        tiene_ticket_asociado = dte.numero_documento in folios_tickets_set
        
        # Calcular suma de pagos para detectar descuentos
        suma_pagos_dte = sum((p.monto or 0) for p in dte.dte_asociado.all())
        descuento_dte = max(0, monto_dte - suma_pagos_dte)
        
        # Usar monto real pagado (con descuento aplicado) para cuadratura
        monto_real = suma_pagos_dte if suma_pagos_dte > 0 else monto_dte
        
        # NC siempre se procesan (para restarlas)
        if dte.tipo_documento == 'NOTA DE CREDITO':
            cuadratura_data['total_notas_credito'] += monto_dte
            cuadratura_data['cantidad_notas_credito'] += 1
            pagos_nc = dte.dte_asociado.all()
            tiene_efectivo = pagos_nc.filter(metodo_pago='EFECTIVO').exists()
            if tiene_efectivo:
                cuadratura_data['total_nc_efectivo'] += monto_dte
            else:
                cuadratura_data['total_nc_transferencia'] += monto_dte
        elif not tiene_ticket_asociado:
            # Solo contar montos DTE si NO tienen ticket asociado (evita doble conteo)
            if dte.tipo_documento == 'BOLETA ELECTRONICA':
                cuadratura_data['total_boletas_electronicas'] += monto_real
                cuadratura_data['cantidad_boletas_electronicas'] += 1
                cuadratura_data['total_descuentos'] += descuento_dte
            elif dte.tipo_documento == 'BOLETA PAPEL':
                cuadratura_data['total_boletas_papel'] += monto_real
                cuadratura_data['cantidad_boletas_papel'] += 1
                cuadratura_data['total_descuentos'] += descuento_dte
            elif dte.tipo_documento == 'FACTURA ELECTRONICA':
                cuadratura_data['total_facturas'] += monto_real
                cuadratura_data['cantidad_facturas'] += 1
                cuadratura_data['total_descuentos'] += descuento_dte
            elif dte.tipo_documento == 'FACTURA EXENTA':
                cuadratura_data['total_facturas_exentas'] += monto_real
                cuadratura_data['cantidad_facturas_exentas'] += 1
                cuadratura_data['total_descuentos'] += descuento_dte
        else:
            # Ticket con DTE: solo registrar cantidades de documentos (no montos)
            if dte.tipo_documento == 'BOLETA ELECTRONICA':
                cuadratura_data['cantidad_boletas_electronicas'] += 1
            elif dte.tipo_documento == 'BOLETA PAPEL':
                cuadratura_data['cantidad_boletas_papel'] += 1
            elif dte.tipo_documento == 'FACTURA ELECTRONICA':
                cuadratura_data['cantidad_facturas'] += 1
            elif dte.tipo_documento == 'FACTURA EXENTA':
                cuadratura_data['cantidad_facturas_exentas'] += 1
        
        # Procesar pagos del DTE SOLO si no tiene ticket asociado
        if not tiene_ticket_asociado:
            for pago in dte.dte_asociado.all():
                metodo = pago.metodo_pago or ''
                tipo_tarjeta = pago.tipo_tarjeta or ''
                monto = pago.monto or 0
                
                metodo_upper = metodo.upper()
                tarjeta_upper = tipo_tarjeta.upper()
                
                # Efectivo
                if metodo_upper == 'EFECTIVO':
                    cuadratura_data['total_efectivo'] += monto
                
                # Transbank Débito (solo por método, tipo_tarjeta no importa)
                elif metodo_upper in ['TBK_DEBITO_POS', 'TARJETA_DEBITO']:
                    cuadratura_data['total_tarjeta_debito'] += monto
                    cuadratura_data['total_transbank'] += monto
                
                # Transbank Crédito (solo por método, tipo_tarjeta no importa)
                elif metodo_upper in ['TBK_CREDITO_POS', 'TARJETA_CREDITO']:
                    cuadratura_data['total_tarjeta_credito'] += monto
                    cuadratura_data['total_transbank'] += monto
                
                # Transbank Prepago
                elif metodo_upper == 'TBK_PREPAGO_POS':
                    cuadratura_data['total_tarjeta_debito'] += monto
                    cuadratura_data['total_transbank'] += monto
                
                # Transbank genérico
                elif metodo_upper in ['TBK_POS_INTEGRADO', 'TBK_MANUAL']:
                    cuadratura_data['total_transbank'] += monto
                
                # Transferencia
                elif 'TRANSFERENCIA' in metodo_upper:
                    cuadratura_data['total_transferencia'] += monto
                
                # Cheque
                elif 'CHEQUE' in metodo_upper:
                    cuadratura_data['total_cheque'] += monto
                
                # Convenio
                elif metodo_upper == 'CONVENIO':
                    cuadratura_data['total_convenio'] += monto
                
                # Crédito trabajador
                elif metodo_upper == 'CREDITO_TRABAJADOR':
                    cuadratura_data['total_credito_trabajador'] += monto
                
                # Crédito externo
                elif metodo_upper == 'CREDITO_EXTERNO':
                    cuadratura_data['total_credito_externo'] += monto
                
                # Orden de compra
                elif metodo_upper == 'ORDEN_COMPRA' or ('ORDEN' in metodo_upper and 'COMPRA' in metodo_upper):
                    cuadratura_data['total_orden_compra'] += monto
                
                # Tarjeta Comercial
                elif metodo_upper == 'TARJETA_COMERCIAL':
                    cuadratura_data['total_tarjetas_comerciales'] += monto
                    if 'HITES' in tarjeta_upper:
                        cuadratura_data['total_hites'] += monto
                
                # Venta Internet - buscar en tipo_tarjeta para clasificar
                elif metodo_upper == 'VENTA_INTERNET':
                    cuadratura_data['total_venta_internet'] += monto
                    # Clasificar por tipo_tarjeta
                    if 'FALABELLA' in tarjeta_upper:
                        cuadratura_data['total_falabella'] += monto
                    elif 'PARIS' in tarjeta_upper:
                        cuadratura_data['total_paris'] += monto
                    elif 'RIPLEY' in tarjeta_upper:
                        cuadratura_data['total_ripley'] += monto
                    elif 'MERCADO' in tarjeta_upper:
                        cuadratura_data['total_mercadopago'] += monto
                    elif 'KLAP' in tarjeta_upper:
                        cuadratura_data['total_klap'] += monto
    
    # ========== CALCULAR TOTALES GENERALES ==========
    # Tarjetas comerciales: solo Hites
    cuadratura_data['total_tarjetas_comerciales'] = cuadratura_data['total_hites']
    
    # Alias para compatibilidad con frontend
    cuadratura_data['total_visa_mc_amex'] = cuadratura_data['total_tarjeta_credito']
    
    # Venta Internet ya se calcula en el loop, pero asegurar el total
    # (ya se suma en cada if de venta internet arriba)
    
    cuadratura_data['venta_total'] = (
        cuadratura_data['total_tickets'] +
        cuadratura_data['total_boletas_electronicas'] +
        cuadratura_data['total_boletas_papel'] +
        cuadratura_data['total_facturas'] +
        cuadratura_data['total_facturas_exentas'] -
        cuadratura_data['total_notas_credito']
    )

    # NC en efectivo resta del efectivo teórico de caja
    cuadratura_data['total_efectivo'] -= cuadratura_data['total_nc_efectivo']
    
    return cuadratura_data


@login_required
@require_POST
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
        
        # Usar la función helper para calcular los datos
        cuadratura_data = _calcular_cuadratura_data(sucursal, fecha_cuadratura)

        # Ocultar efectivo teórico para cajeros (anti-fraude: conteo ciego)
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario in ('cajero', 'vendedor'):
            cuadratura_data['total_efectivo'] = None
            cuadratura_data['modo_conteo_ciego'] = True
        else:
            cuadratura_data['modo_conteo_ciego'] = False

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
        # Primero crear con valores básicos
        arqueo = ArqueoCaja(
            fecha_arqueo=fecha_obj,
            sucursal=sucursal,
            usuario_responsable=request.user,
            
            # Totales teóricos del sistema
            total_efectivo_teorico=efectivo_teorico,
            total_tarjeta_debito_teorico=cuadratura_completa.get('total_tarjeta_debito', 0),
            total_tarjeta_credito_teorico=cuadratura_completa.get('total_tarjeta_credito', 0),
            total_transbank_teorico=cuadratura_completa.get('total_transbank', 0),
            # Tarjetas Comerciales (solo Hites)
            total_hites_teorico=cuadratura_completa.get('total_hites', 0),
            total_tarjetas_comerciales_teorico=cuadratura_completa.get('total_tarjetas_comerciales', 0),
            # Venta Internet (Falabella, Paris, Ripley, MercadoPago, Klap)
            total_falabella_teorico=cuadratura_completa.get('total_falabella', 0),
            total_paris_teorico=cuadratura_completa.get('total_paris', 0),
            total_ripley_teorico=cuadratura_completa.get('total_ripley', 0),
            total_mercadopago_teorico=cuadratura_completa.get('total_mercadopago', 0),
            total_klap_teorico=cuadratura_completa.get('total_klap', 0),
            total_venta_internet_teorico=cuadratura_completa.get('total_venta_internet', 0),
            # Otros
            total_transferencia_teorico=cuadratura_completa.get('total_transferencia', 0),
            total_credito_trabajador_teorico=cuadratura_completa.get('total_credito_trabajador', 0),
            
            # Cierre POS
            numero_lote_pos=numero_lote,
            
            # Observaciones
            observaciones=observaciones,

            fondo_fijo_snapshot=sucursal.fondo_fijo_caja,
            fecha_cierre=timezone.now()
        )
        
        # Guardar primero para obtener el ID
        arqueo.save()
        
        # Ahora actualizar los campos que no deben ser recalculados usando update()
        # para evitar que el método save() recalcule el efectivo físico desde las denominaciones
        diferencia_efectivo = efectivo_real - (efectivo_teorico + sucursal.fondo_fijo_caja)
        diferencia_transbank = cierre_pos - cuadratura_completa.get('total_transbank', 0)
        estado_final = 'CERRADO' if diferencia_efectivo == 0 and diferencia_transbank == 0 else 'CON_DIFERENCIAS'
        
        ArqueoCaja.objects.filter(id=arqueo.id).update(
            total_efectivo_fisico=efectivo_real,
            diferencia_efectivo=diferencia_efectivo,
            cierre_pos_fisico=cierre_pos,
            diferencia_transbank=diferencia_transbank,
            estado=estado_final
        )
        
        # Recargar para obtener valores actualizados
        arqueo.refresh_from_db()
        
        print(f"✅ Arqueo creado ID={arqueo.id}:")
        print(f"   - Efectivo teórico: {arqueo.total_efectivo_teorico}")
        print(f"   - Efectivo físico: {arqueo.total_efectivo_fisico}")
        print(f"   - Cierre POS: {arqueo.cierre_pos_fisico}")
        print(f"   - Transbank teórico: {arqueo.total_transbank_teorico}")
        
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
        
        log_accion_caja(request, 'GUARDAR_CONTEO', arqueo)

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
def eliminar_cuadratura(request, arqueo_id):
    """Eliminar una cuadratura existente"""
    try:
        # Verificar permisos
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ('administrador', 'administracion'):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para eliminar cuadraturas. Se requiere rol de Administración o Administrador.'
            }, status=403)

        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')

        arqueo = get_object_or_404(
            ArqueoCaja,
            id=arqueo_id,
            sucursal_id=sucursal_id
        )

        if arqueo.estado not in ('ABIERTO',):
            return JsonResponse({
                'success': False,
                'error': f'Solo se pueden eliminar arqueos en estado Abierto. Estado actual: {arqueo.get_estado_display()}'
            })

        # Guardar info antes de eliminar
        fecha_arqueo = arqueo.fecha_arqueo.strftime('%d/%m/%Y')

        # Registrar auditoría antes de eliminar
        log_accion_caja(request, 'ELIMINAR_ARQUEO', arqueo, fecha=fecha_arqueo)

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
def obtener_sucursales(request):
    """Obtener listado de todas las sucursales"""
    try:
        sucursales = Sucursal.objects.all().order_by('alias')
        
        sucursales_data = []
        for suc in sucursales:
            sucursales_data.append({
                'id': suc.id,
                'alias': suc.alias,
                'nombre': suc.nombre,
                'direccion': suc.direccion
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener sucursales: {str(e)}'
        })


@login_required
@require_GET
def listar_cuadraturas(request):
    """Listar cuadraturas/arqueos con filtros"""
    try:
        # Obtener filtros
        fecha_filtro = request.GET.get('fecha')
        sucursal_actual_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        sucursal_filtro = request.GET.get('sucursal')  # Se ignora, solo se usa para depuración

        if not sucursal_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay una sucursal activa en la sesión'
            }, status=400)
        
        if sucursal_filtro and str(sucursal_filtro) != str(sucursal_actual_id):
            print(f"⚠️ Ignorando filtro de sucursal ({sucursal_filtro}); se usa la sucursal de la sesión ({sucursal_actual_id})")
        
        # Query base — siempre restringida a la sucursal de la sesión
        arqueos = (
            ArqueoCaja.objects
            .filter(sucursal_id=sucursal_actual_id)
            .select_related('usuario_responsable', 'sucursal')
            .prefetch_related('depositos')
        )
        
        # Aplicar filtro de fecha si existe
        if fecha_filtro:
            from datetime import datetime
            fecha_obj = datetime.strptime(fecha_filtro, '%Y-%m-%d').date()
            arqueos = arqueos.filter(fecha_arqueo=fecha_obj)
        else:
            # Por defecto, mostrar TODO el mes actual
            hoy = timezone.localdate()
            primer_dia_mes = hoy.replace(day=1)
            arqueos = arqueos.filter(
                fecha_arqueo__gte=primer_dia_mes,
                fecha_arqueo__lte=hoy
            )
        
        # Ordenar por fecha descendente (de más reciente a más antigua)
        arqueos = arqueos.order_by('-fecha_arqueo')
        
        # Serializar datos
        from datetime import datetime, time as dt_time
        
        datos = []
        for arqueo in arqueos:
            # RECALCULAR TEÓRICOS EN TIEMPO REAL
            fecha_obj = arqueo.fecha_arqueo
            inicio_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.min))
            fin_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.max))
            
            # Recalcular efectivo teórico del día
            tickets_dia = Ticket.objects.filter(
                sucursal_id=sucursal_actual_id,
                created_at__gte=inicio_dia,
                created_at__lte=fin_dia,
                estado__in=['PENDIENTE_PAGO', 'PAGADO', 'PARCIALMENTE_PAGADO']
            ).exclude(estado='ANULADO')
            
            efectivo_teorico_actualizado = 0
            transbank_teorico_actualizado = 0
            convenio_teorico_actualizado = 0
            credito_trabajador_teorico_actualizado = 0
            credito_externo_teorico_actualizado = 0
            
            for ticket in tickets_dia:
                pagos = TicketDetallePago.objects.filter(ticket=ticket)
                for pago in pagos:
                    monto = pago.monto or 0
                    if pago.metodo_pago == 'EFECTIVO':
                        efectivo_teorico_actualizado += monto
                    elif pago.metodo_pago in ['TARJETA_DEBITO', 'TARJETA_CREDITO', 'TARJETA']:
                        transbank_teorico_actualizado += monto
                    elif pago.metodo_pago == 'CONVENIO':
                        convenio_teorico_actualizado += monto
                    elif pago.metodo_pago == 'CREDITO_TRABAJADOR':
                        credito_trabajador_teorico_actualizado += monto
                    elif pago.metodo_pago == 'CREDITO_EXTERNO':
                        credito_externo_teorico_actualizado += monto
            
            # DTEs del día
            dtes_dia = Dte.objects.filter(
                sucursal_id=sucursal_actual_id,
                fecha_emision=fecha_obj
            ).exclude(estado_dte='ANULADO')
            
            for dte in dtes_dia:
                # Obtener los pagos asociados al DTE
                pagos_dte = dte.dte_asociado.all()
                for pago in pagos_dte:
                    monto = pago.monto or 0
                    if pago.metodo_pago == 'EFECTIVO':
                        efectivo_teorico_actualizado += monto
                    elif pago.metodo_pago in ['TARJETA_DEBITO', 'TARJETA_CREDITO', 'TARJETA']:
                        transbank_teorico_actualizado += monto
                    elif pago.metodo_pago == 'CONVENIO':
                        convenio_teorico_actualizado += monto
                    elif pago.metodo_pago == 'CREDITO_TRABAJADOR':
                        credito_trabajador_teorico_actualizado += monto
                    elif pago.metodo_pago == 'CREDITO_EXTERNO':
                        credito_externo_teorico_actualizado += monto
            
            # Usar properties del modelo para cálculos correctos
            total_depositos = arqueo.total_depositos
            efectivo_fisico = arqueo.total_efectivo_fisico
            
            # LÓGICA CORRECTA:
            # - Físico es lo que declaró la cajera (para control interno)
            # - Depósitos es lo que está en el banco (realidad)
            # - La diferencia real es: (Físico + Depósitos) - Teórico
            total_efectivo_real = efectivo_fisico + total_depositos
            
            # Considerar fondo fijo de caja chica
            fondo_fijo = arqueo.fondo_fijo_snapshot

            # Diferencia de efectivo: Lo que realmente hay (físico + depósitos) vs lo teórico + fondo fijo
            diferencia_efectivo_actualizada = total_efectivo_real - (efectivo_teorico_actualizado + fondo_fijo)

            # Diferencia de cajero (solo informativa): Físico vs (Teórico + Fondo Fijo)
            diferencia_cajero = efectivo_fisico - (efectivo_teorico_actualizado + fondo_fijo)
            
            # Diferencia Transbank
            diferencia_transbank_actualizada = arqueo.cierre_pos_fisico - transbank_teorico_actualizado
            
            # Diferencia total
            diferencia_total_actualizada = diferencia_efectivo_actualizada + diferencia_transbank_actualizada
            
            arqueo_data = {
                'id': arqueo.id,
                'fecha_arqueo': arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
                'sucursal': arqueo.sucursal.alias if arqueo.sucursal else 'N/A',
                'sucursal_id': arqueo.sucursal.id if arqueo.sucursal else None,
                'usuario': arqueo.usuario_responsable.get_full_name() or arqueo.usuario_responsable.username,
                'efectivo_teorico': efectivo_teorico_actualizado,  # ACTUALIZADO EN TIEMPO REAL
                'efectivo_fisico': efectivo_fisico,  # Lo que declaró la cajera
                'total_depositos': total_depositos,  # Lo que se depositó en el banco
                'total_efectivo_real': total_efectivo_real,  # Físico + Depósitos
                'diferencia_efectivo': diferencia_efectivo_actualizada,  # Diferencia real: (Físico + Depósitos) - Teórico
                'diferencia_efectivo_real': diferencia_efectivo_actualizada,  # Mismo valor
                'diferencia_cajero': diferencia_cajero,  # Solo informativa: Físico - Teórico
                'total_transbank_teorico': transbank_teorico_actualizado,  # ACTUALIZADO EN TIEMPO REAL
                'total_convenio_teorico': convenio_teorico_actualizado,
                'total_credito_trabajador_teorico': credito_trabajador_teorico_actualizado,
                'total_credito_externo_teorico': credito_externo_teorico_actualizado,
                'cierre_pos_fisico': arqueo.cierre_pos_fisico,  # NO CAMBIA (lo que ingresó)
                'numero_lote_pos': arqueo.numero_lote_pos or '',
                'diferencia_transbank': diferencia_transbank_actualizada,  # RECALCULADA
                'diferencia_total_real': diferencia_total_actualizada,  # RECALCULADA
                'estado': arqueo.get_estado_display(),
                'estado_codigo': arqueo.estado,
                'observaciones': arqueo.observaciones,
                'cantidad_depositos': arqueo.depositos.count(),
                'fondo_fijo': fondo_fijo,
            }
            
            # Debug del primer arqueo
            if len(datos) == 0:
                print(f"📋 Primer arqueo en listar_cuadraturas ID={arqueo.id}:")
                print(f"   - Efectivo teórico GUARDADO: {arqueo.total_efectivo_teorico}")
                print(f"   - Efectivo teórico ACTUALIZADO: {efectivo_teorico_actualizado}")
                print(f"   - Efectivo físico: {arqueo.total_efectivo_fisico}")
                print(f"   - Cierre POS físico: {arqueo.cierre_pos_fisico}")
                print(f"   - Transbank teórico GUARDADO: {arqueo.total_transbank_teorico}")
                print(f"   - Transbank teórico ACTUALIZADO: {transbank_teorico_actualizado}")
            
            datos.append(arqueo_data)
        
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
    """Obtener detalle completo de un arqueo específico - RECALCULA VALORES TEÓRICOS EN TIEMPO REAL"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']
        
        qs = ArqueoCaja.objects.select_related('usuario_responsable', 'sucursal').prefetch_related('depositos')
        if es_supervisor:
            arqueo = get_object_or_404(qs, id=arqueo_id)
            sucursal_id = arqueo.sucursal_id
        else:
            arqueo = get_object_or_404(qs, id=arqueo_id, sucursal_id=sucursal_id)
        
        # ========== RECALCULAR VALORES TEÓRICOS EN TIEMPO REAL ==========
        from datetime import time as dt_time, datetime
        
        fecha_obj = arqueo.fecha_arqueo
        sucursal = arqueo.sucursal
        
        # Crear datetime para filtros con timezone aware
        inicio_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.min))
        fin_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.max))
        
        # Inicializar totales RECALCULADOS
        total_efectivo_teorico = 0
        total_tarjeta_debito_teorico = 0
        total_tarjeta_credito_teorico = 0
        total_transbank_teorico = 0
        total_hites_teorico = 0
        total_falabella_teorico = 0
        total_paris_teorico = 0
        total_ripley_teorico = 0
        total_mercadopago_teorico = 0
        total_klap_teorico = 0
        total_venta_internet_teorico = 0
        total_transferencia_teorico = 0
        total_credito_trabajador_teorico = 0
        total_convenio_teorico = 0
        total_credito_externo_teorico = 0
        
        # ========== PROCESAR TICKETS ==========
        tickets_del_dia = Ticket.objects.filter(
            sucursal=sucursal,
            fecha=fecha_obj,
            estado='PAGADO'
        ).prefetch_related('pagos')

        for ticket in tickets_del_dia:
            # Procesar pagos del ticket
            for pago in ticket.pagos.all():
                metodo = pago.metodo_pago
                monto = pago.monto or 0
                
                if metodo == 'EFECTIVO':
                    total_efectivo_teorico += monto
                elif metodo == 'TARJETA_DEBITO':
                    total_tarjeta_debito_teorico += monto
                    total_transbank_teorico += monto
                elif metodo == 'TARJETA_CREDITO':
                    # ✅ TARJETA_CREDITO se considera Transbank (datos migrados y genéricos)
                    total_tarjeta_credito_teorico += monto
                elif metodo == 'TBK_DEBITO_POS':
                    # ✅ Transbank POS Débito
                    total_tarjeta_debito_teorico += monto
                elif metodo == 'TBK_CREDITO_POS':
                    # ✅ Transbank POS Crédito
                    total_tarjeta_credito_teorico += monto
                elif metodo == 'TBK_PREPAGO_POS':
                    # ✅ Transbank POS Prepago (va a débito por convención)
                    total_tarjeta_debito_teorico += monto
                    total_transbank_teorico += monto
                elif metodo == 'TBK_POS_INTEGRADO' or metodo == 'TBK_MANUAL':
                    total_transbank_teorico += monto
                elif metodo == 'TRANSFERENCIA':
                    total_transferencia_teorico += monto
                elif metodo == 'CREDITO_TRABAJADOR':
                    total_credito_trabajador_teorico += monto
                elif metodo == 'CONVENIO':
                    total_convenio_teorico += monto
                elif metodo == 'CREDITO_EXTERNO':
                    total_credito_externo_teorico += monto
                elif metodo == 'TARJETA_COMERCIAL':
                    total_hites_teorico += monto
                elif metodo == 'VENTA_INTERNET':
                    total_venta_internet_teorico += monto
                    total_mercadopago_teorico += monto
        
        # ========== PROCESAR DTEs (FACTURAS/BOLETAS ELECTRÓNICAS) ==========
        # Evitar doble conteo de DTEs que ya tienen ticket asociado
        folios_tickets = Ticket.objects.filter(
            sucursal=sucursal,
            fecha=fecha_obj,
            folio_dte__isnull=False
        ).values_list('folio_dte', flat=True)
        
        dtes_del_dia = Dte.objects.filter(
            sucursal=sucursal,
            fecha_emision=fecha_obj,
            estado_dte__in=['EMITIDO', 'ACEPTADO'],
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
        ).prefetch_related('dte_asociado')

        for dte in dtes_del_dia:
            # Omitir pagos si el DTE ya fue contado por ticket asociado
            if dte.numero_documento in folios_tickets:
                continue
            
            # Procesar pagos del DTE
            for pago in dte.dte_asociado.all():
                metodo = pago.metodo_pago or ''
                tipo_tarjeta = pago.tipo_tarjeta or ''
                monto = pago.monto or 0
                
                metodo_upper = metodo.upper()
                tarjeta_upper = tipo_tarjeta.upper()
                
                # Efectivo
                if metodo_upper == 'EFECTIVO' or 'EFECTIVO' in metodo_upper:
                    total_efectivo_teorico += monto
                
                # Transbank Débito (solo por método, tipo_tarjeta no importa)
                elif metodo_upper in ['TBK_DEBITO_POS', 'TARJETA_DEBITO']:
                    total_tarjeta_debito_teorico += monto
                    total_transbank_teorico += monto
                
                # Transbank Crédito (solo por método, tipo_tarjeta no importa)
                elif metodo_upper in ['TBK_CREDITO_POS', 'TARJETA_CREDITO']:
                    total_tarjeta_credito_teorico += monto
                    total_transbank_teorico += monto
                
                # Transbank Prepago
                elif metodo_upper == 'TBK_PREPAGO_POS':
                    total_tarjeta_debito_teorico += monto
                    total_transbank_teorico += monto
                
                # Transbank genérico
                elif metodo_upper in ['TBK_POS_INTEGRADO', 'TBK_MANUAL']:
                    total_transbank_teorico += monto
                
                # Transferencia
                elif 'TRANSFERENCIA' in metodo_upper:
                    total_transferencia_teorico += monto
                
                # Convenio
                elif 'CONVENIO' in metodo_upper:
                    total_convenio_teorico += monto
                
                # Crédito externo
                elif 'CREDITO_EXTERNO' in metodo_upper or 'CREDITO EXTERNO' in metodo_upper:
                    total_credito_externo_teorico += monto
                
                # Crédito trabajador
                elif metodo_upper == 'CREDITO_TRABAJADOR':
                    total_credito_trabajador_teorico += monto
                
                # Tarjeta Comercial: solo Hites
                elif metodo_upper == 'TARJETA_COMERCIAL' or 'HITES' in tarjeta_upper:
                    total_hites_teorico += monto
                
                # Venta Internet: Falabella, Paris, Ripley, MercadoPago, Klap
                elif metodo_upper == 'VENTA_INTERNET' or 'INTERNET' in metodo_upper:
                    total_venta_internet_teorico += monto
                    if 'FALABELLA' in tarjeta_upper:
                        total_falabella_teorico += monto
                    elif 'PARIS' in tarjeta_upper:
                        total_paris_teorico += monto
                    elif 'RIPLEY' in tarjeta_upper:
                        total_ripley_teorico += monto
                    elif 'MERCADO' in tarjeta_upper:
                        total_mercadopago_teorico += monto
                    elif 'KLAP' in tarjeta_upper:
                        total_klap_teorico += monto
        
        # ========== CALCULAR TOTALES ==========
        total_tarjetas_comerciales_teorico = total_hites_teorico
        
        # Calcular diferencias ACTUALIZADAS
        diferencia_efectivo = arqueo.total_efectivo_fisico - total_efectivo_teorico
        diferencia_transbank = arqueo.cierre_pos_fisico - total_transbank_teorico
        
        # Serializar depositos
        depositos_data = []
        for deposito in arqueo.depositos.all():
            depositos_data.append({
                'id': deposito.id,
                'fecha_deposito': deposito.fecha_deposito.strftime('%d/%m/%Y'),
                'fecha_deposito_iso': deposito.fecha_deposito.strftime('%Y-%m-%d'),
                'monto': deposito.monto,
                'monto_declarado': deposito.monto_declarado,
                'monto_confirmado': deposito.monto_confirmado,
                'diferencia_deposito': deposito.diferencia_deposito,
                'verificado': deposito.verificado,
                'banco': deposito.banco,
                'banco_display': deposito.get_banco_display(),
                'numero_comprobante': deposito.numero_comprobante,
                'observaciones': deposito.observaciones,
                'declarado_por': deposito.declarado_por.get_full_name() if deposito.declarado_por else '',
                'fecha_declaracion': deposito.fecha_declaracion.strftime('%d/%m/%Y %H:%M') if deposito.fecha_declaracion else '',
                'verificado_por': deposito.verificado_por.get_full_name() if deposito.verificado_por else '',
                'registrado_por': deposito.registrado_por.username if deposito.registrado_por else '',
                'fecha_registro': deposito.fecha_registro.strftime('%d/%m/%Y %H:%M') if deposito.fecha_registro else ''
            })
        
        # Calcular venta total
        venta_total = (total_efectivo_teorico + total_transbank_teorico +
                      total_tarjetas_comerciales_teorico + total_venta_internet_teorico +
                      total_transferencia_teorico + total_credito_trabajador_teorico +
                      total_convenio_teorico + total_credito_externo_teorico)
        
        arqueo_data = {
            'id': arqueo.id,
            'fecha_arqueo': arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
            'usuario': arqueo.usuario_responsable.get_full_name() or arqueo.usuario_responsable.username,
            'sucursal': arqueo.sucursal.alias if arqueo.sucursal else 'N/A',
            'estado': arqueo.estado,
            'observaciones': arqueo.observaciones,
            
            # Totales RECALCULADOS
            'venta_total': venta_total,
            'total_efectivo_teorico': total_efectivo_teorico,
            'total_efectivo_fisico': arqueo.total_efectivo_fisico,
            'diferencia_efectivo': diferencia_efectivo,
            
            # Transbank POS RECALCULADO
            'total_transbank_teorico': total_transbank_teorico,
            'cierre_pos_fisico': arqueo.cierre_pos_fisico,
            'diferencia_transbank': diferencia_transbank,
            'numero_lote_pos': arqueo.numero_lote_pos or '',
            
            # Transbank Detalle RECALCULADO
            'total_tarjeta_debito_teorico': total_tarjeta_debito_teorico,
            'total_tarjeta_credito_teorico': total_tarjeta_credito_teorico,
            
            # Tarjetas Comerciales RECALCULADO (solo Hites)
            'total_hites_teorico': total_hites_teorico,
            'total_tarjetas_comerciales_teorico': total_tarjetas_comerciales_teorico,
            
            # Venta Internet RECALCULADO
            'total_falabella_teorico': total_falabella_teorico,
            'total_paris_teorico': total_paris_teorico,
            'total_ripley_teorico': total_ripley_teorico,
            'total_mercadopago_teorico': total_mercadopago_teorico,
            'total_klap_teorico': total_klap_teorico,
            'total_venta_internet_teorico': total_venta_internet_teorico,
            
            # Otros RECALCULADO
            'total_transferencia_teorico': total_transferencia_teorico,
            'total_credito_trabajador_teorico': total_credito_trabajador_teorico,
            'total_convenio_teorico': total_convenio_teorico,
            'total_credito_externo_teorico': total_credito_externo_teorico,
            
            # Depósitos
            'depositos': depositos_data,
            # Revisión
            'resultado_revision': getattr(arqueo, 'resultado_revision', 'PENDIENTE'),
            'observaciones_supervisor': arqueo.observaciones_supervisor or '',
            'supervisor': arqueo.supervisor_revision.get_full_name() if arqueo.supervisor_revision else '',
            'fecha_revision': arqueo.fecha_revision.strftime('%d/%m/%Y %H:%M') if arqueo.fecha_revision else '',
            # Bitácora
            'bitacora': [{
                'id': obs.id,
                'tipo': obs.tipo,
                'tipo_display': obs.get_tipo_display(),
                'texto': obs.texto,
                'usuario': obs.usuario.get_full_name() or obs.usuario.username,
                'fecha': obs.fecha.strftime('%d/%m/%Y %H:%M'),
                'visible_para_cajera': obs.visible_para_cajera,
            } for obs in arqueo.bitacora.all()[:20]],
        }
        
        print(f"✅ Detalle de arqueo #{arqueo_id} - Valores RECALCULADOS en tiempo real")
        print(f"   Efectivo teórico RECALCULADO: ${total_efectivo_teorico}")
        print(f"   Transbank teórico RECALCULADO: ${total_transbank_teorico}")
        print(f"   Venta total RECALCULADA: ${venta_total}")
        
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
def agregar_deposito_arqueo(request):
    """Agregar un depósito bancario a un arqueo existente"""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        arqueo_id = request.POST.get('arqueo_id')
        fecha_deposito = request.POST.get('fecha_deposito')
        monto = int(request.POST.get('monto', 0))
        banco = request.POST.get('banco')
        numero_comprobante = request.POST.get('numero_comprobante', '')
        observaciones = request.POST.get('observaciones', '')
        
        # Validaciones
        if not arqueo_id or not fecha_deposito or not monto or not banco:
            return JsonResponse({
                'success': False,
                'error': 'Faltan datos requeridos'
            })
        
        # Obtener el arqueo
        arqueo = get_object_or_404(
            ArqueoCaja,
            id=arqueo_id,
            sucursal_id=sucursal_id
        )
        
        # Crear el depósito
        from datetime import datetime
        fecha_obj = datetime.strptime(fecha_deposito, '%Y-%m-%d').date()
        
        deposito = DepositoBancario.objects.create(
            arqueo=arqueo,
            fecha_deposito=fecha_obj,
            monto=monto,
            banco=banco,
            numero_comprobante=numero_comprobante,
            observaciones=observaciones,
            registrado_por=request.user
        )
        
        # Usar properties del modelo para cálculos
        total_depositos = arqueo.total_depositos
        efectivo_en_caja = arqueo.efectivo_en_caja
        diferencia_efectivo_real = arqueo.diferencia_efectivo_real
        diferencia_total_real = arqueo.diferencia_total_real
        
        # Recalcular estado basado en la diferencia REAL (considerando depósitos)
        if abs(diferencia_efectivo_real) <= 1000 and abs(arqueo.diferencia_transbank) <= 1000:
            arqueo.estado = 'CERRADO'
        else:
            arqueo.estado = 'CON_DIFERENCIAS'
        
        arqueo.save()
        
        print(f"✅ Depósito agregado al arqueo {arqueo_id}: ${monto}")
        print(f"   Total depósitos: ${total_depositos}")
        print(f"   Efectivo físico contado: ${arqueo.total_efectivo_fisico}")
        print(f"   Efectivo en caja (después de depósitos): ${efectivo_en_caja}")
        print(f"   Efectivo teórico: ${arqueo.total_efectivo_teorico}")
        print(f"   Diferencia efectivo REAL: ${diferencia_efectivo_real}")
        print(f"   Diferencia total REAL: ${diferencia_total_real}")
        print(f"   Nuevo estado: {arqueo.estado}")
        
        return JsonResponse({
            'success': True,
            'message': 'Depósito agregado correctamente',
            'deposito': {
                'id': deposito.id,
                'fecha': deposito.fecha_deposito.strftime('%d/%m/%Y'),
                'monto': deposito.monto,
                'banco': deposito.get_banco_display()
            },
            'arqueo_actualizado': {
                'total_depositos': total_depositos,
                'efectivo_fisico': arqueo.total_efectivo_fisico,
                'efectivo_en_caja': efectivo_en_caja,
                'efectivo_teorico': arqueo.total_efectivo_teorico,
                'diferencia_efectivo_real': diferencia_efectivo_real,
                'diferencia_transbank': arqueo.diferencia_transbank,
                'diferencia_total_real': diferencia_total_real,
                'estado': arqueo.get_estado_display(),
                'estado_codigo': arqueo.estado
            }
        })
        
    except Exception as e:
        import traceback
        print(f"Error al agregar depósito: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


@login_required
@require_POST
def eliminar_deposito_bancario(request):
    """Eliminar un depósito bancario específico"""
    try:
        data = json.loads(request.body)
        deposito_id = data.get('deposito_id')
        
        if not deposito_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de depósito requerido'
            })
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        # Obtener el depósito
        deposito = get_object_or_404(DepositoBancario, id=deposito_id)
        
        # Verificar que el arqueo pertenece a la sucursal actual
        if deposito.arqueo.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para eliminar este depósito'
            })
        
        arqueo = deposito.arqueo
        monto_eliminado = deposito.monto
        
        # Eliminar el depósito
        deposito.delete()
        
        # Usar properties del modelo para cálculos
        total_depositos = arqueo.total_depositos
        efectivo_en_caja = arqueo.efectivo_en_caja
        diferencia_efectivo_real = arqueo.diferencia_efectivo_real
        diferencia_total_real = arqueo.diferencia_total_real
        
        # Recalcular estado basado en la diferencia REAL (considerando depósitos)
        if abs(diferencia_efectivo_real) <= 1000 and abs(arqueo.diferencia_transbank) <= 1000:
            arqueo.estado = 'CERRADO'
        else:
            arqueo.estado = 'CON_DIFERENCIAS'
        
        arqueo.save()
        
        print(f"✅ Depósito {deposito_id} eliminado del arqueo {arqueo.id}: ${monto_eliminado}")
        print(f"   Total depósitos restantes: ${total_depositos}")
        print(f"   Efectivo en caja (después de depósitos): ${efectivo_en_caja}")
        print(f"   Diferencia efectivo REAL: ${diferencia_efectivo_real}")
        print(f"   Diferencia total REAL: ${diferencia_total_real}")
        print(f"   Nuevo estado: {arqueo.estado}")
        
        return JsonResponse({
            'success': True,
            'message': 'Depósito eliminado correctamente',
            'arqueo_actualizado': {
                'total_depositos': total_depositos,
                'efectivo_fisico': arqueo.total_efectivo_fisico,
                'efectivo_en_caja': efectivo_en_caja,
                'efectivo_teorico': arqueo.total_efectivo_teorico,
                'diferencia_efectivo_real': diferencia_efectivo_real,
                'diferencia_transbank': arqueo.diferencia_transbank,
                'diferencia_total_real': diferencia_total_real,
                'estado': arqueo.get_estado_display(),
                'estado_codigo': arqueo.estado
            }
        })
        
    except Exception as e:
        import traceback
        print(f"Error al eliminar depósito: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


@login_required
@require_POST
def declarar_deposito(request):
    """
    El cajero declara un depósito con comprobante bancario.
    Soporta FormData (multipart) para subir imagen del comprobante.
    Permite múltiples depósitos por arqueo (ej: efectivo + cheque).
    """
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')

        # Soportar tanto JSON como FormData
        if request.content_type and 'multipart' in request.content_type:
            arqueo_id = request.POST.get('arqueo_id')
            monto_declarado = int(request.POST.get('monto_declarado', 0))
            tipo_medio = request.POST.get('tipo_medio', 'EFECTIVO')
            banco = request.POST.get('banco', 'ESTADO')
            numero_comprobante = request.POST.get('numero_comprobante', '')
            observaciones = request.POST.get('observaciones', '')
            imagen_comprobante = request.FILES.get('imagen_comprobante')
        else:
            data = json.loads(request.body)
            arqueo_id = data.get('arqueo_id')
            monto_declarado = int(data.get('monto_declarado', 0))
            tipo_medio = data.get('tipo_medio', 'EFECTIVO')
            banco = data.get('banco', 'ESTADO')
            numero_comprobante = data.get('numero_comprobante', '')
            observaciones = data.get('observaciones', '')
            imagen_comprobante = None

        if not arqueo_id or monto_declarado <= 0:
            return JsonResponse({'success': False, 'error': 'Se requiere arqueo_id y monto_declarado > 0'})

        if tipo_medio not in ('EFECTIVO', 'CHEQUE'):
            return JsonResponse({'success': False, 'error': 'tipo_medio debe ser EFECTIVO o CHEQUE'})

        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id, sucursal_id=sucursal_id)

        # Validar que no se declare más de lo posible
        from django.db.models import Sum
        total_ya_declarado = arqueo.depositos.aggregate(total=Sum('monto_declarado'))['total'] or 0
        max_depositable = arqueo.total_efectivo_fisico + arqueo.total_cheque_teorico
        if max_depositable > 0 and (total_ya_declarado + monto_declarado) > max_depositable * 1.1:
            return JsonResponse({
                'success': False,
                'error': f'El total declarado (${total_ya_declarado + monto_declarado:,}) excede el máximo depositable (${max_depositable:,})'
            })

        from django.utils import timezone as tz
        deposito = DepositoBancario(
            arqueo=arqueo,
            fecha_deposito=arqueo.fecha_arqueo,
            monto=0,
            monto_declarado=monto_declarado,
            monto_confirmado=0,
            tipo_medio=tipo_medio,
            banco=banco,
            numero_comprobante=numero_comprobante,
            observaciones=observaciones,
            declarado_por=request.user,
            fecha_declaracion=tz.now(),
            verificado=False,
            registrado_por=request.user,
        )
        if imagen_comprobante:
            deposito.imagen_comprobante = imagen_comprobante
        deposito.save()

        log_accion_caja(request, 'DECLARAR_DEPOSITO', arqueo, monto=monto_declarado, tipo_medio=tipo_medio)

        return JsonResponse({
            'success': True,
            'message': 'Depósito declarado exitosamente. El supervisor deberá confirmarlo.',
            'deposito': {
                'id': deposito.id,
                'monto_declarado': deposito.monto_declarado,
                'tipo_medio': deposito.tipo_medio,
                'tipo_medio_display': deposito.get_tipo_medio_display(),
                'banco': deposito.banco,
                'banco_display': deposito.get_banco_display(),
                'numero_comprobante': deposito.numero_comprobante,
                'tiene_imagen': bool(deposito.imagen_comprobante),
                'declarado_por': request.user.get_full_name() or request.user.username,
                'fecha_declaracion': deposito.fecha_declaracion.strftime('%d/%m/%Y %H:%M'),
                'verificado': False,
            }
        })

    except Exception as e:
        import traceback
        print(f"Error declarar_deposito: {e}\n{traceback.format_exc()}")
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def finalizar_declaracion(request):
    """
    El cajero señala que terminó de declarar todos sus depósitos (efectivo, cheque, etc.)
    Transiciona el arqueo de CERRADO/CON_DIFERENCIAS → DEPOSITO_DECLARADO.
    """
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        data = json.loads(request.body)
        arqueo_id = data.get('arqueo_id')

        if not arqueo_id:
            return JsonResponse({'success': False, 'error': 'Se requiere arqueo_id'})

        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id, sucursal_id=sucursal_id)

        # Verificar que el arqueo está en estado válido para finalizar declaración
        if arqueo.estado not in ('CERRADO', 'CON_DIFERENCIAS'):
            return JsonResponse({
                'success': False,
                'error': f'El arqueo debe estar Cerrado o Con Diferencias para finalizar declaración. Estado actual: {arqueo.get_estado_display()}'
            })

        # Verificar que exista al menos un depósito declarado
        depositos_declarados = arqueo.depositos.filter(monto_declarado__gt=0)
        if not depositos_declarados.exists():
            return JsonResponse({'success': False, 'error': 'Debe declarar al menos un depósito antes de finalizar'})

        # Transicionar estado
        ArqueoCaja.objects.filter(id=arqueo.id).update(estado='DEPOSITO_DECLARADO')
        arqueo.refresh_from_db()

        # Resumen de depósitos
        resumen = []
        for dep in depositos_declarados:
            resumen.append({
                'id': dep.id,
                'tipo_medio': dep.get_tipo_medio_display(),
                'monto_declarado': dep.monto_declarado,
                'banco': dep.get_banco_display(),
                'numero_comprobante': dep.numero_comprobante,
                'tiene_imagen': bool(dep.imagen_comprobante),
            })

        return JsonResponse({
            'success': True,
            'message': 'Declaración finalizada. Los depósitos serán revisados por el supervisor.',
            'estado': arqueo.estado,
            'estado_display': arqueo.get_estado_display(),
            'depositos': resumen,
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'})
    except Exception as e:
        import traceback
        print(f"Error finalizar_declaracion: {e}\n{traceback.format_exc()}")
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def confirmar_deposito(request, deposito_id):
    """
    El supervisor confirma un depósito declarado por el cajero.
    Los datos bancarios (banco, comprobante, imagen) ya vienen del cajero.
    El supervisor solo verifica visualmente, confirma monto y agrega observaciones si hay discrepancia.
    """
    try:
        if request.method not in ('POST', 'PATCH'):
            return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)

        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']

        if not es_supervisor:
            return JsonResponse({'success': False, 'error': 'Sin permisos. Solo supervisores pueden confirmar depósitos.'}, status=403)

        deposito = get_object_or_404(DepositoBancario, id=deposito_id)
        if deposito.arqueo.sucursal_id != int(sucursal_id):
            return JsonResponse({'success': False, 'error': 'Depósito no pertenece a su sucursal'}, status=403)

        # El supervisor confirma o ajusta el monto
        monto_confirmado = int(request.POST.get('monto_confirmado', request.POST.get('monto', 0)))
        observaciones_supervisor = request.POST.get('observaciones_supervisor', request.POST.get('observaciones', ''))

        # Fecha de depósito: usar la existente o una nueva si el supervisor la proporciona
        fecha_deposito = request.POST.get('fecha_deposito') or request.POST.get('fecha')

        if monto_confirmado <= 0:
            return JsonResponse({'success': False, 'error': 'Se requiere monto_confirmado > 0'})

        from datetime import datetime
        from django.utils import timezone as tz

        if fecha_deposito:
            fecha_obj = datetime.strptime(fecha_deposito, '%Y-%m-%d').date()
            deposito.fecha_deposito = fecha_obj

        deposito.monto = monto_confirmado
        deposito.monto_confirmado = monto_confirmado

        # Supervisor puede sobreescribir banco/comprobante si es necesario
        banco_override = request.POST.get('banco')
        if banco_override:
            deposito.banco = banco_override
        numero_comprobante_override = request.POST.get('numero_comprobante')
        if numero_comprobante_override:
            deposito.numero_comprobante = numero_comprobante_override

        if observaciones_supervisor:
            deposito.observaciones_supervisor = observaciones_supervisor

        if 'imagen_comprobante' in request.FILES:
            deposito.imagen_comprobante = request.FILES['imagen_comprobante']

        deposito.verificado = True
        deposito.verificado_por = request.user
        deposito.fecha_verificacion = tz.now()
        deposito.save()

        log_accion_caja(request, 'CONFIRMAR_DEPOSITO', deposito.arqueo, monto=monto_confirmado)

        arqueo = deposito.arqueo
        total_depositos = arqueo.total_depositos
        efectivo_en_caja = arqueo.efectivo_en_caja
        diferencia_efectivo_real = arqueo.diferencia_efectivo_real

        # Verificar si todos los depósitos del arqueo están confirmados
        todos_confirmados = not arqueo.depositos.filter(verificado=False, monto_declarado__gt=0).exists()

        if todos_confirmados and arqueo.estado == 'DEPOSITO_DECLARADO':
            # Todos los depósitos confirmados → transicionar a DEPOSITO_CONFIRMADO
            ArqueoCaja.objects.filter(id=arqueo.id).update(estado='DEPOSITO_CONFIRMADO')
            arqueo.refresh_from_db()
        elif todos_confirmados:
            # Flujo legacy: si no pasó por DEPOSITO_DECLARADO, usar lógica original
            if abs(diferencia_efectivo_real) <= 1000 and abs(arqueo.diferencia_transbank) <= 1000:
                ArqueoCaja.objects.filter(id=arqueo.id).update(estado='CERRADO')
            else:
                ArqueoCaja.objects.filter(id=arqueo.id).update(estado='CON_DIFERENCIAS')
            arqueo.refresh_from_db()

        return JsonResponse({
            'success': True,
            'message': 'Depósito confirmado correctamente',
            'deposito': {
                'id': deposito.id,
                'monto_declarado': deposito.monto_declarado,
                'monto_confirmado': deposito.monto_confirmado,
                'diferencia': deposito.diferencia_deposito,
                'banco': deposito.get_banco_display(),
                'numero_comprobante': deposito.numero_comprobante,
                'verificado': True,
                'verificado_por': request.user.get_full_name() or request.user.username,
            },
            'arqueo_actualizado': {
                'total_depositos': total_depositos,
                'efectivo_en_caja': efectivo_en_caja,
                'diferencia_efectivo_real': diferencia_efectivo_real,
                'estado': arqueo.get_estado_display(),
                'estado_codigo': arqueo.estado,
            }
        })

    except Exception as e:
        import traceback
        print(f"Error confirmar_deposito: {e}\n{traceback.format_exc()}")
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def obtener_depositos_pendientes(request):
    """Retorna depósitos declarados pero sin verificar para el panel del supervisor."""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        fecha_str = request.GET.get('fecha')

        qs = DepositoBancario.objects.filter(
            arqueo__sucursal_id=sucursal_id,
            verificado=False,
            monto_declarado__gt=0,
        ).select_related('arqueo', 'declarado_por').order_by('-fecha_declaracion')

        if fecha_str:
            qs = qs.filter(arqueo__fecha_arqueo=fecha_str)

        depositos = []
        for d in qs:
            depositos.append({
                'id': d.id,
                'arqueo_id': d.arqueo_id,
                'fecha_arqueo': d.arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
                'monto_declarado': d.monto_declarado,
                'tipo_medio': d.tipo_medio,
                'tipo_medio_display': d.get_tipo_medio_display(),
                'banco': d.banco,
                'banco_display': d.get_banco_display(),
                'numero_comprobante': d.numero_comprobante,
                'tiene_imagen': bool(d.imagen_comprobante),
                'imagen_url': d.imagen_comprobante.url if d.imagen_comprobante else '',
                'declarado_por': d.declarado_por.get_full_name() if d.declarado_por else '—',
                'fecha_declaracion': d.fecha_declaracion.strftime('%d/%m/%Y %H:%M') if d.fecha_declaracion else '—',
                'observaciones': d.observaciones,
            })

        return JsonResponse({'success': True, 'depositos': depositos, 'total': len(depositos)})

    except Exception as e:
        import traceback
        print(f"Error obtener_depositos_pendientes: {e}\n{traceback.format_exc()}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== DEPÓSITO MULTI-DÍA ==========

@login_required
@require_GET
def listar_arqueos_para_deposito(request):
    """
    Retorna arqueos de la sucursal que tienen efectivo pendiente de depositar.
    Se usa en el modal de depósito multi-día para elegir qué días incluir.
    """
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'Sin sucursal'})

        arqueos = ArqueoCaja.objects.filter(
            sucursal_id=sucursal_id,
            estado__in=['CERRADO', 'CON_DIFERENCIAS', 'ABIERTO'],
        ).order_by('-fecha_arqueo')[:60]

        resultado = []
        for a in arqueos:
            efectivo_teorico = a.total_efectivo_teorico
            total_depositado = a.total_depositos
            pendiente = efectivo_teorico - total_depositado
            resultado.append({
                'id': a.id,
                'fecha': a.fecha_arqueo.strftime('%Y-%m-%d'),
                'fecha_display': a.fecha_arqueo.strftime('%d/%m/%Y'),
                'efectivo_teorico': efectivo_teorico,
                'efectivo_fisico': a.total_efectivo_fisico,
                'total_depositado': total_depositado,
                'pendiente_depositar': pendiente,
                'estado': a.get_estado_display(),
            })

        return JsonResponse({'success': True, 'arqueos': resultado})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
@csrf_exempt
def crear_deposito_multidia(request):
    """
    Crea un GrupoDeposito (1 comprobante bancario) con desglose por día.
    Valida que la suma del desglose coincida con el monto del comprobante.
    """
    try:
        import json
        from datetime import datetime

        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'Sin sucursal'})

        sucursal = get_object_or_404(Sucursal, id=sucursal_id)

        fecha_deposito = request.POST.get('fecha_deposito')
        monto_total = int(request.POST.get('monto_total', 0))
        banco = request.POST.get('banco', 'ESTADO')
        numero_comprobante = request.POST.get('numero_comprobante', '')
        observaciones = request.POST.get('observaciones', '')
        desglose_json = request.POST.get('desglose', '[]')

        if not fecha_deposito or monto_total <= 0:
            return JsonResponse({'success': False, 'error': 'Fecha y monto son requeridos'})

        desglose = json.loads(desglose_json)
        if not desglose:
            return JsonResponse({'success': False, 'error': 'Debe incluir al menos un día en el desglose'})

        suma_desglose = sum(int(d.get('monto', 0)) for d in desglose)
        if suma_desglose != monto_total:
            return JsonResponse({
                'success': False,
                'error': f'La suma del desglose (${suma_desglose:,}) no coincide con el monto del comprobante (${monto_total:,})'
            })

        fecha_obj = datetime.strptime(fecha_deposito, '%Y-%m-%d').date()

        grupo = GrupoDeposito.objects.create(
            sucursal=sucursal,
            fecha_deposito=fecha_obj,
            monto_total=monto_total,
            banco=banco,
            numero_comprobante=numero_comprobante,
            observaciones=observaciones,
            registrado_por=request.user,
        )

        if 'imagen_comprobante' in request.FILES:
            grupo.imagen_comprobante = request.FILES['imagen_comprobante']
            grupo.save()

        depositos_creados = []
        for item in desglose:
            arqueo_id = int(item['arqueo_id'])
            monto_dia = int(item['monto'])

            arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id, sucursal=sucursal)

            dep = DepositoBancario.objects.create(
                arqueo=arqueo,
                grupo=grupo,
                fecha_deposito=fecha_obj,
                monto=monto_dia,
                banco=banco,
                numero_comprobante=numero_comprobante,
                observaciones=f"Depósito multi-día (Grupo #{grupo.id})",
                registrado_por=request.user,
            )
            depositos_creados.append({
                'id': dep.id,
                'arqueo_id': arqueo.id,
                'fecha_arqueo': arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
                'monto': dep.monto,
            })

            if abs(arqueo.diferencia_efectivo_real) <= 1000 and abs(arqueo.diferencia_transbank) <= 1000:
                arqueo.estado = 'CERRADO'
            else:
                arqueo.estado = 'CON_DIFERENCIAS'
            arqueo.save()

        return JsonResponse({
            'success': True,
            'message': f'Depósito multi-día registrado exitosamente ({len(depositos_creados)} días)',
            'grupo_id': grupo.id,
            'depositos': depositos_creados,
        })

    except Exception as e:
        import traceback
        print(f"Error crear_deposito_multidia: {e}\n{traceback.format_exc()}")
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_GET
def detalle_grupo_deposito(request, grupo_id):
    """Retorna el detalle de un grupo de depósito con su desglose por día."""
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        grupo = get_object_or_404(GrupoDeposito, id=grupo_id, sucursal_id=sucursal_id)

        depositos = []
        for d in grupo.depositos.select_related('arqueo').all():
            depositos.append({
                'id': d.id,
                'arqueo_id': d.arqueo_id,
                'fecha_arqueo': d.arqueo.fecha_arqueo.strftime('%d/%m/%Y'),
                'monto': d.monto,
                'efectivo_teorico': d.arqueo.total_efectivo_teorico,
            })

        return JsonResponse({
            'success': True,
            'grupo': {
                'id': grupo.id,
                'fecha_deposito': grupo.fecha_deposito.strftime('%d/%m/%Y'),
                'monto_total': grupo.monto_total,
                'banco': grupo.get_banco_display(),
                'numero_comprobante': grupo.numero_comprobante,
                'observaciones': grupo.observaciones,
                'esta_cuadrado': grupo.esta_cuadrado,
                'suma_desglose': grupo.suma_desglose,
                'diferencia': grupo.diferencia,
                'cantidad_dias': grupo.cantidad_dias,
                'registrado_por': grupo.registrado_por.get_full_name() or grupo.registrado_por.username,
                'fecha_registro': grupo.fecha_registro.strftime('%d/%m/%Y %H:%M'),
            },
            'depositos': depositos,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


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
        print(f"📝 Editando arqueo {arqueo_id}. Datos recibidos: {data}")
        print(f"📊 Valores ANTES: Efectivo físico={arqueo.total_efectivo_fisico}, Cierre POS={arqueo.cierre_pos_fisico}")
        
        # Actualizar efectivo físico
        if 'efectivo_real' in data:
            arqueo.total_efectivo_fisico = data['efectivo_real']
            arqueo.diferencia_efectivo = arqueo.total_efectivo_fisico - arqueo.total_efectivo_teorico
            print(f"✅ Efectivo actualizado: Físico={arqueo.total_efectivo_fisico}, Teórico={arqueo.total_efectivo_teorico}, Diferencia={arqueo.diferencia_efectivo}")
        
        # Actualizar cierre POS
        if 'cierre_pos' in data:
            arqueo.cierre_pos_fisico = data['cierre_pos']
            arqueo.diferencia_transbank = arqueo.cierre_pos_fisico - arqueo.total_transbank_teorico
            print(f"✅ Cierre POS actualizado: Físico={arqueo.cierre_pos_fisico}, Teórico={arqueo.total_transbank_teorico}, Diferencia={arqueo.diferencia_transbank}")
        
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
        
        # IMPORTANTE: Usar update() en lugar de save() para evitar que el método save() 
        # del modelo recalcule automáticamente el total_efectivo_fisico desde las denominaciones
        ArqueoCaja.objects.filter(id=arqueo.id).update(
            total_efectivo_fisico=arqueo.total_efectivo_fisico,
            diferencia_efectivo=arqueo.diferencia_efectivo,
            cierre_pos_fisico=arqueo.cierre_pos_fisico,
            diferencia_transbank=arqueo.diferencia_transbank,
            numero_lote_pos=arqueo.numero_lote_pos,
            observaciones=arqueo.observaciones,
            estado=arqueo.estado
        )
        
        # Recargar el objeto para verificar
        arqueo.refresh_from_db()
        
        print(f"💾 Arqueo guardado con update(). Valores DESPUÉS:")
        print(f"   - Efectivo físico: {arqueo.total_efectivo_fisico}")
        print(f"   - Cierre POS: {arqueo.cierre_pos_fisico}")
        print(f"   - Diferencia efectivo: {arqueo.diferencia_efectivo}")
        print(f"   - Diferencia Transbank: {arqueo.diferencia_transbank}")
        print(f"   - Estado: {arqueo.estado}")
        
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
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Usar la función helper directamente para calcular la cuadratura
        cuadratura_data = _calcular_cuadratura_data(sucursal, fecha)
        
        # Convertir fecha string a date object
        from datetime import datetime
        fecha_obj = datetime.strptime(fecha, '%Y-%m-%d').date()
        
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
            fecha=fecha_obj,
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
                'cliente': ticket.cliente_nombre if ticket.cliente_nombre else 'Cliente General',
                'metodo_pago': metodo_pago,
                'total': ticket.total,
                'estado': ticket.estado
            })
        
        # ========== OBTENER BOLETAS ELECTRÓNICAS ==========
        boletas_del_dia = Dte.objects.filter(
            sucursal=sucursal,
            fecha_emision=fecha_obj,
            tipo_documento='BOLETA ELECTRONICA',
            tipo_transaccion='VENTA_PUBLICO',
            estado_dte__in=['EMITIDO', 'ACEPTADO']
        ).select_related('receptor').order_by('fecha_emision', 'hora')
        
        boletas_data = []
        for boleta in boletas_del_dia:
            # Calcular monto IVA
            monto_iva = boleta.monto_con_iva - boleta.monto_neto
            
            boletas_data.append({
                'id': boleta.id,
                'folio': boleta.numero_documento,
                'hora': boleta.hora.strftime('%H:%M:%S') if boleta.hora else 'N/A',
                'rut_cliente': boleta.receptor.rut if boleta.receptor else '66666666-6',
                'razon_social': boleta.receptor.razon_social if boleta.receptor else 'Cliente General',
                'monto_neto': float(boleta.monto_neto),
                'monto_iva': float(monto_iva),
                'monto_total': float(boleta.monto_con_iva),
                'estado': boleta.estado_dte
            })
        
        # ========== OBTENER FACTURAS ELECTRÓNICAS ==========
        facturas_del_dia = Dte.objects.filter(
            sucursal=sucursal,
            fecha_emision=fecha_obj,
            tipo_documento__in=['FACTURA ELECTRONICA', 'FACTURA EXENTA'],
            tipo_transaccion='VENTA_PUBLICO',
            estado_dte__in=['EMITIDO', 'ACEPTADO']
        ).select_related('receptor').order_by('fecha_emision', 'hora')
        
        facturas_data = []
        for factura in facturas_del_dia:
            # Calcular monto IVA
            monto_iva = factura.monto_con_iva - factura.monto_neto
            
            facturas_data.append({
                'id': factura.id,
                'folio': factura.numero_documento,
                'hora': factura.hora.strftime('%H:%M:%S') if factura.hora else 'N/A',
                'rut_cliente': factura.receptor.rut if factura.receptor else 'N/A',
                'razon_social': factura.receptor.razon_social if factura.receptor else 'Cliente',
                'monto_neto': float(factura.monto_neto),
                'monto_iva': float(monto_iva),
                'monto_total': float(factura.monto_con_iva),
                'estado': factura.estado_dte,
                'tipo': factura.tipo_documento
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
    """API para listar arqueos históricos con indicadores mensuales"""
    try:
        from datetime import datetime, date, timedelta
        from calendar import monthrange
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        # Supervisores pueden consultar otra sucursal via query param
        sucursal_override = request.GET.get('sucursal_id')
        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']
        if sucursal_override and es_supervisor:
            sucursal_id = sucursal_override
        
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
        
        # ========== CALCULAR INDICADORES MENSUALES ==========
        hoy = timezone.localdate()
        primer_dia_mes = date(hoy.year, hoy.month, 1)
        ultimo_dia_mes = date(hoy.year, hoy.month, monthrange(hoy.year, hoy.month)[1])
        
        # Días hábiles del mes (lunes a sábado = 0-5)
        dias_habiles = []
        dia_actual = primer_dia_mes
        while dia_actual <= min(hoy, ultimo_dia_mes):
            if dia_actual.weekday() < 6:  # Lunes a Sábado
                dias_habiles.append(dia_actual)
            dia_actual += timedelta(days=1)
        
        total_dias_habiles = len(dias_habiles)
        
        # Arqueos del mes actual
        arqueos_mes = ArqueoCaja.objects.filter(
            sucursal_id=sucursal_id,
            fecha_arqueo__gte=primer_dia_mes,
            fecha_arqueo__lte=hoy
        )
        
        fechas_con_arqueo = set(arqueos_mes.values_list('fecha_arqueo', flat=True))
        
        # Calcular indicadores
        arqueos_realizados = len(fechas_con_arqueo)
        arqueos_pendientes = arqueos_mes.filter(estado='ABIERTO').count()
        arqueos_con_diferencias = arqueos_mes.filter(estado='CON_DIFERENCIAS').count()
        arqueos_cerrados = arqueos_mes.filter(estado='CERRADO').count()
        
        # Días faltantes (días hábiles sin arqueo)
        dias_faltantes = [d for d in dias_habiles if d not in fechas_con_arqueo]
        arqueos_faltantes = len(dias_faltantes)
        
        # Totales de diferencias del mes
        total_diferencia_efectivo = sum(a.diferencia_efectivo for a in arqueos_mes)
        total_diferencia_transbank = sum(a.diferencia_transbank for a in arqueos_mes)
        
        # Indicadores adicionales para control de depósitos y revisión
        arqueos_revisados = arqueos_mes.filter(estado='REVISADO').count()
        arqueos_sin_revision = arqueos_mes.exclude(estado__in=['ABIERTO', 'REVISADO']).count()
        depositos_pendientes_conf = DepositoBancario.objects.filter(
            arqueo__sucursal_id=sucursal_id,
            arqueo__fecha_arqueo__gte=primer_dia_mes,
            verificado=False,
            monto_declarado__gt=0
        ).count()
        # Total depositado vs teórico del mes (control real)
        from django.db.models import Sum
        total_depositado_mes = DepositoBancario.objects.filter(
            arqueo__sucursal_id=sucursal_id,
            arqueo__fecha_arqueo__gte=primer_dia_mes,
            arqueo__fecha_arqueo__lte=hoy,
            verificado=True,
        ).aggregate(total=Sum('monto_confirmado'))['total'] or 0
        total_teorico_efectivo_mes = sum(a.total_efectivo_teorico for a in arqueos_mes)

        indicadores_mensuales = {
            'mes_actual': hoy.strftime('%B %Y'),
            'dias_habiles': total_dias_habiles,
            'arqueos_realizados': arqueos_realizados,
            'arqueos_faltantes': arqueos_faltantes,
            'arqueos_pendientes': arqueos_pendientes,
            'arqueos_con_diferencias': arqueos_con_diferencias,
            'arqueos_cerrados': arqueos_cerrados,
            'porcentaje_cumplimiento': round((arqueos_realizados / total_dias_habiles * 100) if total_dias_habiles > 0 else 0, 1),
            'dias_faltantes': [d.strftime('%Y-%m-%d') for d in dias_faltantes[:10]],
            'total_diferencia_efectivo': total_diferencia_efectivo,
            'total_diferencia_transbank': total_diferencia_transbank,
            # Nuevos indicadores
            'arqueos_revisados': arqueos_revisados,
            'arqueos_sin_revision': arqueos_sin_revision,
            'porcentaje_revisados': round((arqueos_revisados / arqueos_realizados * 100) if arqueos_realizados > 0 else 0, 1),
            'depositos_pendientes_confirmacion': depositos_pendientes_conf,
            'total_depositado_mes': total_depositado_mes,
            'total_teorico_efectivo_mes': total_teorico_efectivo_mes,
            'diferencia_depositos_mes': total_depositado_mes - total_teorico_efectivo_mes,
        }
        
        print(f"📊 Indicadores mes: {indicadores_mensuales}")
        
        # ========== CONSTRUIR QUERYSET PRINCIPAL ==========
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
                # Transbank
                'transbank_teorico': arqueo.total_transbank_teorico,
                'transbank_fisico': arqueo.cierre_pos_fisico,
                'diferencia_transbank': arqueo.diferencia_transbank,
                'debito_teorico': arqueo.total_tarjeta_debito_teorico,
                'debito_fisico': arqueo.cierre_debito_fisico,
                'diferencia_debito': arqueo.diferencia_debito,
                'credito_teorico': arqueo.total_tarjeta_credito_teorico,
                'credito_fisico': arqueo.cierre_credito_fisico,
                'diferencia_credito': arqueo.diferencia_credito,
                'numero_lote': arqueo.numero_lote_pos or '',
                # Otros
                'observaciones': arqueo.observaciones or '',
                'supervisor': arqueo.supervisor_revision.username if arqueo.supervisor_revision else '',
                'fecha_cierre': arqueo.fecha_cierre.strftime('%d/%m/%Y %H:%M') if arqueo.fecha_cierre else '',
                'tiene_comprobante': arqueo.depositos.filter(verificado=True, numero_comprobante__gt='').exists(),
                'total_depositado_verificado': sum(d.monto for d in arqueo.depositos.filter(verificado=True)),
                # Resumen de depósitos para workflow visual
                'depositos_declarados': arqueo.depositos.filter(monto_declarado__gt=0).count(),
                'depositos_confirmados': arqueo.depositos.filter(verificado=True).count(),
                'depositos_pendientes': arqueo.depositos.filter(verificado=False, monto_declarado__gt=0).count(),
                'tiene_depositos': arqueo.depositos.filter(monto_declarado__gt=0).exists(),
                'reaperturas': arqueo.historial_reaperturas.count(),
                # === CONTROL POR DEPÓSITO BANCARIO (control real) ===
                'total_deposito_efectivo': arqueo.total_depositado_efectivo_verificado,
                'total_deposito_cheque': arqueo.total_depositado_cheque_verificado,
                'diferencia_deposito_vs_teorico': arqueo.diferencia_deposito_vs_teorico,
                'diferencia_cheques_vs_teorico': arqueo.diferencia_cheques_vs_teorico,
                'estado_deposito': arqueo.estado_deposito,
                # === REVISIÓN Y URGENCIA ===
                'dias_sin_revision': arqueo.dias_sin_revision,
                'requiere_revision_urgente': arqueo.requiere_revision_urgente,
                # === METADATA CONTEO ===
                'modo_conteo': arqueo.modo_conteo,
                'requiere_revision_express': arqueo.requiere_revision_express,
                'fondo_fijo': arqueo.fondo_fijo_snapshot,
                # === OBSERVACIONES ===
                'observaciones_diferencia': arqueo.observaciones_diferencia or '',
                'categoria_diferencia': arqueo.categoria_diferencia or '',
                'observaciones_supervisor': arqueo.observaciones_supervisor or '',
                # === RESULTADO REVISIÓN ===
                'resultado_revision': getattr(arqueo, 'resultado_revision', 'PENDIENTE'),
                'resultado_revision_display': dict(RESULTADO_REVISION_CHOICES).get(getattr(arqueo, 'resultado_revision', 'PENDIENTE'), 'Pendiente'),
                'cantidad_observaciones': arqueo.bitacora.count() if hasattr(arqueo, 'bitacora') else 0,
                'ultima_obs_supervisor': '',
            })
        
        return JsonResponse({
            'success': True,
            'arqueos': arqueos_data,
            'indicadores_mensuales': indicadores_mensuales,
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
def corregir_arqueos_express(request):
    """Corregir arqueos que fueron guardados incorrectamente en modo Express"""
    try:
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario != 'administrador':
            return JsonResponse({
                'success': False,
                'error': 'Solo el Administrador puede usar la corrección express.'
            }, status=403)

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
                log_accion_caja(request, 'CORREGIR_EXPRESS', arqueo)
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

        # Validar que la fecha no sea futura y esté dentro del rango permitido por rol
        from datetime import datetime, date as dt_date
        fecha_obj = datetime.strptime(fecha_arqueo, '%Y-%m-%d').date()
        hoy = timezone.localdate()

        if fecha_obj > hoy:
            return JsonResponse({
                'success': False,
                'error': 'No puede crear arqueos para fechas futuras'
            })

        dias_atras = (hoy - fecha_obj).days
        rol_usuario = getattr(request.user, 'rol', None)

        # Tolerancia por rol: cajero/vendedor=2 días, jefe_local=3, admin/administración=sin límite
        if rol_usuario in ('cajero', 'vendedor'):
            max_dias = 2
        elif rol_usuario == 'jefe_local':
            max_dias = 3
        else:  # administrador, administracion
            max_dias = 0  # sin límite

        if max_dias > 0 and dias_atras > max_dias:
            return JsonResponse({
                'success': False,
                'error': f'Solo puede crear arqueos de los últimos {max_dias} días. Han pasado {dias_atras} días.'
            })

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
        # Función auxiliar para convertir valores a int (los Decimal vienen como strings del JSON)
        def to_int(value):
            if value is None:
                return 0
            try:
                return int(float(value))
            except (ValueError, TypeError):
                return 0
        
        arqueo = ArqueoCaja.objects.create(
            fecha_arqueo=fecha_arqueo,
            sucursal=sucursal,
            usuario_responsable=request.user,
            
            # Totales teóricos de la cuadratura
            # Tarjetas Comerciales (solo Hites)
            total_hites_teorico=to_int(cuadratura_data.get('total_hites', 0)),
            total_tarjetas_comerciales_teorico=to_int(cuadratura_data.get('total_tarjetas_comerciales', 0)),
            
            total_efectivo_teorico=to_int(cuadratura_data.get('total_efectivo', 0)),
            
            # Venta Internet (Falabella, Paris, Ripley, MercadoPago, Klap)
            total_falabella_teorico=to_int(cuadratura_data.get('total_falabella', 0)),
            total_paris_teorico=to_int(cuadratura_data.get('total_paris', 0)),
            total_ripley_teorico=to_int(cuadratura_data.get('total_ripley', 0)),
            total_mercadopago_teorico=to_int(cuadratura_data.get('total_mercadopago', 0)),
            total_klap_teorico=to_int(cuadratura_data.get('total_klap', 0)),
            total_venta_internet_teorico=to_int(cuadratura_data.get('total_venta_internet', 0)),
            
            total_tarjeta_debito_teorico=to_int(cuadratura_data.get('total_tarjeta_debito', 0)),
            total_tarjeta_credito_teorico=to_int(cuadratura_data.get('total_tarjeta_credito', 0)),
            total_transbank_teorico=to_int(cuadratura_data.get('total_transbank', 0)),
            total_transferencia_teorico=to_int(cuadratura_data.get('total_transferencia', 0)),
            total_cheque_teorico=to_int(cuadratura_data.get('total_cheque', 0)),
            total_convenio_teorico=to_int(cuadratura_data.get('total_convenio', 0)),
            total_credito_trabajador_teorico=to_int(cuadratura_data.get('total_credito_trabajador', 0)),
            
            total_tickets_teorico=to_int(cuadratura_data.get('total_tickets', 0)),
            total_boletas_electronicas_teorico=to_int(cuadratura_data.get('total_boletas_electronicas', 0)),
            total_facturas_teorico=to_int(cuadratura_data.get('total_facturas', 0)),
            total_facturas_exentas_teorico=to_int(cuadratura_data.get('total_facturas_exentas', 0)),
            total_notas_credito_teorico=to_int(cuadratura_data.get('total_notas_credito', 0)),
            
            cantidad_tickets=to_int(cuadratura_data.get('cantidad_tickets', 0)),
            cantidad_boletas_electronicas=to_int(cuadratura_data.get('cantidad_boletas_electronicas', 0)),
            cantidad_facturas=to_int(cuadratura_data.get('cantidad_facturas', 0)),
            cantidad_facturas_exentas=to_int(cuadratura_data.get('cantidad_facturas_exentas', 0)),
            
            venta_total_teorica=to_int(cuadratura_data.get('venta_total', 0)),

            fondo_fijo_snapshot=sucursal.fondo_fijo_caja,
            estado='ABIERTO'
        )
        
        log_accion_caja(request, 'GENERAR_CUADRATURA', arqueo)

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

        arqueo.timestamp_conteo_fisico = timezone.now()
        arqueo.modo_conteo = 'EXPRESS' if modo_express else 'DETALLADO'
        if modo_express:
            arqueo.requiere_revision_express = True

        # Observaciones
        arqueo.observaciones = data.get('observaciones', '')
        arqueo.observaciones_diferencia = data.get('observaciones_diferencia', '')
        
        # Datos de Transbank (cierre POS)
        cierre_debito = int(data.get('cierre_debito', 0))
        cierre_credito = int(data.get('cierre_credito', 0))
        numero_lote = data.get('numero_lote', '')
        
        # Calcular total y diferencias de Transbank
        cierre_pos_total = cierre_debito + cierre_credito
        diferencia_debito = cierre_debito - arqueo.total_tarjeta_debito_teorico
        diferencia_credito = cierre_credito - arqueo.total_tarjeta_credito_teorico
        diferencia_transbank = cierre_pos_total - arqueo.total_transbank_teorico
        
        print(f"💳 Transbank - Débito: {cierre_debito} (teórico: {arqueo.total_tarjeta_debito_teorico})")
        print(f"💳 Transbank - Crédito: {cierre_credito} (teórico: {arqueo.total_tarjeta_credito_teorico})")
        print(f"💳 Transbank - Total: {cierre_pos_total} (teórico: {arqueo.total_transbank_teorico})")
        
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
                monedas_1=arqueo.monedas_1,
                # Transbank
                cierre_debito_fisico=cierre_debito,
                cierre_credito_fisico=cierre_credito,
                cierre_pos_fisico=cierre_pos_total,
                numero_lote_pos=numero_lote,
                diferencia_debito=diferencia_debito,
                diferencia_credito=diferencia_credito,
                diferencia_transbank=diferencia_transbank
            )
            print(f"💾 Guardado en modo Express - Total físico: {arqueo.total_efectivo_fisico}, Diferencia: {arqueo.diferencia_efectivo}")
        else:
            # En modo detallado, save() calculará automáticamente el total físico y diferencia
            # Pero primero guardamos los valores de Transbank
            arqueo.cierre_debito_fisico = cierre_debito
            arqueo.cierre_credito_fisico = cierre_credito
            arqueo.cierre_pos_fisico = cierre_pos_total
            arqueo.numero_lote_pos = numero_lote
            arqueo.diferencia_debito = diferencia_debito
            arqueo.diferencia_credito = diferencia_credito
            arqueo.diferencia_transbank = diferencia_transbank
            arqueo.save()
            print(f"💾 Guardado en modo Detallado - Total físico calculado: {arqueo.total_efectivo_fisico}")
        
        # Recargar el objeto para obtener los valores actualizados
        arqueo.refresh_from_db()

        log_accion_caja(request, 'GUARDAR_CONTEO', arqueo)

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
            obs = (arqueo.observaciones_diferencia or '').strip()
            if len(obs) < 20 or len(set(obs.split())) < 3:
                return JsonResponse({
                    'success': False,
                    'error': f'Debe agregar observaciones detalladas (mínimo 20 caracteres y 3 palabras distintas) para diferencias mayores a $500 (actual: ${diferencia_absoluta:,})'
                })
        else:
            print(f"ℹ️ Diferencia menor - No requiere observaciones obligatorias: ${diferencia_absoluta}")
        
        # Cerrar arqueo
        fecha_cierre = timezone.now()
        
        # Determinar estado final
        if arqueo.diferencia_efectivo == 0:
            estado_final = 'CERRADO'
        else:
            estado_final = 'CON_DIFERENCIAS'
        
        # Usar update() en lugar de save() para NO recalcular el total_efectivo_fisico
        # Esto es crítico para mantener el valor correcto en modo Express
        ArqueoCaja.objects.filter(id=arqueo.id).update(
            fecha_cierre=fecha_cierre,
            estado=estado_final
        )
        
        # Recargar para obtener valores actualizados
        arqueo.refresh_from_db()
        
        print(f"✅ Arqueo cerrado exitosamente - Estado final: {arqueo.estado}")

        log_accion_caja(request, 'CERRAR_ARQUEO', arqueo)

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


# ========== FUNCIONES DE SUPERVISIÓN (ADMINISTRACIÓN/ADMINISTRADOR) ==========

@login_required
@require_POST
def revisar_arqueo(request):
    """
    Revisar y aprobar un arqueo (solo supervisores: administración/administrador)
    Soporta resultado_revision: OK, OK_CON_OBS, REQUIERE_ACCION
    """
    try:
        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']
        
        if not es_supervisor:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para revisar arqueos. Se requiere rol de Administración o Administrador.'
            })
        
        data = json.loads(request.body)
        arqueo_id = data.get('arqueo_id')
        observaciones_supervisor = data.get('observaciones', '')
        aprobar = data.get('aprobar', True)
        resultado = data.get('resultado_revision', '')
        
        if not arqueo_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de arqueo requerido'
            })
        
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)
        
        if resultado == 'REQUIERE_ACCION':
            if not observaciones_supervisor or len(observaciones_supervisor.strip()) < 10:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe explicar qué acción se requiere (mínimo 10 caracteres).'
                })
            nuevo_estado = arqueo.estado
            resultado_rev = 'REQUIERE_ACCION'
            accion_texto = 'marcado como requiere acción'
        elif resultado == 'OK_CON_OBS':
            if not observaciones_supervisor:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe incluir observaciones al aprobar con notas.'
                })
            nuevo_estado = 'REVISADO'
            resultado_rev = 'OK_CON_OBS'
            accion_texto = 'aprobado con observaciones'
        elif resultado == 'OK' or aprobar:
            nuevo_estado = 'REVISADO'
            resultado_rev = 'OK'
            accion_texto = 'aprobado'
        else:
            nuevo_estado = arqueo.estado
            resultado_rev = 'PENDIENTE'
            accion_texto = 'marcado como pendiente de revisión'
        
        ArqueoCaja.objects.filter(id=arqueo.id).update(
            estado=nuevo_estado,
            supervisor_revision=request.user,
            fecha_revision=timezone.now(),
            observaciones_supervisor=observaciones_supervisor,
            resultado_revision=resultado_rev
        )
        
        if observaciones_supervisor:
            ObservacionArqueo.objects.create(
                arqueo=arqueo,
                usuario=request.user,
                tipo='SUPERVISOR',
                texto=observaciones_supervisor,
                visible_para_cajera=True
            )
        
        arqueo.refresh_from_db()
        
        log_accion_caja(request, 'REVISAR_ARQUEO', arqueo)

        return JsonResponse({
            'success': True,
            'message': f'Arqueo {accion_texto} exitosamente',
            'arqueo': {
                'id': arqueo.id,
                'estado': arqueo.estado,
                'estado_display': arqueo.get_estado_display(),
                'resultado_revision': arqueo.resultado_revision,
                'supervisor': request.user.username,
                'fecha_revision': arqueo.fecha_revision.strftime('%d/%m/%Y %H:%M') if arqueo.fecha_revision else ''
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
            'error': f'Error al revisar arqueo: {str(e)}'
        })


@login_required
@require_POST
def crear_observacion_arqueo(request):
    """Agregar observación a la bitácora de un arqueo (cajera o supervisor)."""
    try:
        data = json.loads(request.body)
        arqueo_id = data.get('arqueo_id')
        texto = (data.get('texto') or '').strip()

        if not arqueo_id or not texto:
            return JsonResponse({'success': False, 'error': 'Arqueo y texto son requeridos.'})
        if len(texto) < 5:
            return JsonResponse({'success': False, 'error': 'La observación debe tener al menos 5 caracteres.'})

        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)

        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']
        tipo = 'SUPERVISOR' if es_supervisor else 'CAJERA'
        visible = data.get('visible_para_cajera', True)

        obs = ObservacionArqueo.objects.create(
            arqueo=arqueo,
            usuario=request.user,
            tipo=tipo,
            texto=texto,
            visible_para_cajera=visible,
        )

        return JsonResponse({
            'success': True,
            'message': 'Observación registrada.',
            'observacion': {
                'id': obs.id,
                'tipo': obs.tipo,
                'tipo_display': obs.get_tipo_display(),
                'texto': obs.texto,
                'usuario': request.user.get_full_name() or request.user.username,
                'fecha': obs.fecha.strftime('%d/%m/%Y %H:%M'),
                'visible_para_cajera': obs.visible_para_cajera,
            }
        })
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def obtener_bitacora_arqueo(request, arqueo_id):
    """Obtener la bitácora completa de observaciones de un arqueo."""
    try:
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)

        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']

        qs = arqueo.bitacora.select_related('usuario').all()
        if not es_supervisor:
            qs = qs.filter(visible_para_cajera=True)

        observaciones = [{
            'id': obs.id,
            'tipo': obs.tipo,
            'tipo_display': obs.get_tipo_display(),
            'texto': obs.texto,
            'usuario': obs.usuario.get_full_name() or obs.usuario.username,
            'fecha': obs.fecha.strftime('%d/%m/%Y %H:%M'),
            'visible_para_cajera': obs.visible_para_cajera,
        } for obs in qs[:50]]

        return JsonResponse({
            'success': True,
            'observaciones': observaciones,
            'resultado_revision': getattr(arqueo, 'resultado_revision', 'PENDIENTE'),
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def obtener_bloqueos_arqueo(request, fecha):
    """Retornar lista de bloqueos activos para cerrar un día."""
    try:
        from datetime import date as dt_date
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'Sin sucursal.'})

        fecha_obj = dt_date.fromisoformat(fecha)
        bloqueos = []
        completados = []

        try:
            arqueo = ArqueoCaja.objects.get(fecha_arqueo=fecha_obj, sucursal_id=sucursal_id)
        except ArqueoCaja.DoesNotExist:
            bloqueos.append({
                'codigo': 'SIN_ARQUEO',
                'titulo': 'Arqueo no iniciado',
                'descripcion': 'No se ha creado el arqueo para este día.',
                'bloqueante': True,
                'icono': 'ri-calendar-close-line',
            })
            return JsonResponse({'success': True, 'bloqueos': bloqueos, 'completados': completados})

        tiene_conteo = arqueo.total_efectivo_fisico > 0 or arqueo.modo_conteo == 'EXPRESS'
        if arqueo.estado == 'ABIERTO' and not tiene_conteo:
            bloqueos.append({
                'codigo': 'SIN_CONTEO',
                'titulo': 'Falta conteo de efectivo',
                'descripcion': 'Debe contar el efectivo físico en caja.',
                'bloqueante': True,
                'icono': 'ri-money-dollar-circle-line',
            })
        else:
            completados.append({
                'codigo': 'CONTEO_OK',
                'titulo': 'Conteo de efectivo realizado',
                'icono': 'ri-check-line',
            })

        if abs(arqueo.diferencia_efectivo) > 500 and not arqueo.observaciones_diferencia:
            bloqueos.append({
                'codigo': 'SIN_EXPLICACION',
                'titulo': 'Diferencia > $500 sin explicar',
                'descripcion': f'Diferencia de ${abs(arqueo.diferencia_efectivo):,}. Debe agregar observaciones (min 20 chars, 3 palabras).',
                'bloqueante': True,
                'icono': 'ri-error-warning-line',
            })
        elif abs(arqueo.diferencia_efectivo) > 500:
            completados.append({
                'codigo': 'EXPLICACION_OK',
                'titulo': 'Diferencia explicada',
                'icono': 'ri-check-line',
            })

        if arqueo.estado in ['CERRADO', 'CON_DIFERENCIAS', 'DEPOSITO_DECLARADO', 'DEPOSITO_CONFIRMADO', 'REVISADO']:
            completados.append({
                'codigo': 'CIERRE_OK',
                'titulo': 'Arqueo cerrado',
                'icono': 'ri-check-double-line',
            })
        elif arqueo.estado == 'ABIERTO':
            bloqueos.append({
                'codigo': 'SIN_CIERRE',
                'titulo': 'Arqueo aún abierto',
                'descripcion': 'Complete el conteo y cierre el arqueo.',
                'bloqueante': False,
                'icono': 'ri-lock-line',
            })

        tiene_deposito = arqueo.depositos.filter(monto_declarado__gt=0).exists() or arqueo.depositos.filter(verificado=True).exists()
        deposito_confirmado = arqueo.depositos.filter(verificado=True).exists()
        if deposito_confirmado:
            completados.append({
                'codigo': 'DEPOSITO_OK',
                'titulo': 'Depósito confirmado',
                'icono': 'ri-bank-line',
            })
        elif tiene_deposito:
            completados.append({
                'codigo': 'DEPOSITO_DECLARADO',
                'titulo': 'Depósito declarado (pendiente confirmación)',
                'icono': 'ri-time-line',
            })
        elif arqueo.estado not in ['ABIERTO']:
            bloqueos.append({
                'codigo': 'SIN_DEPOSITO',
                'titulo': 'Depósito pendiente',
                'descripcion': 'Declare el depósito bancario del efectivo.',
                'bloqueante': False,
                'icono': 'ri-bank-line',
            })

        resultado_rev = getattr(arqueo, 'resultado_revision', 'PENDIENTE')
        if resultado_rev == 'REQUIERE_ACCION':
            ultima_obs = arqueo.bitacora.filter(tipo='SUPERVISOR').first()
            bloqueos.append({
                'codigo': 'REQUIERE_ACCION',
                'titulo': 'El supervisor requiere acción',
                'descripcion': ultima_obs.texto[:120] if ultima_obs else 'Revise las observaciones del supervisor.',
                'bloqueante': False,
                'icono': 'ri-alarm-warning-line',
            })
        elif resultado_rev in ['OK', 'OK_CON_OBS']:
            completados.append({
                'codigo': 'REVISION_OK',
                'titulo': 'Revisado por supervisor',
                'icono': 'ri-shield-check-line',
            })

        return JsonResponse({
            'success': True,
            'bloqueos': bloqueos,
            'completados': completados,
            'estado': arqueo.estado,
            'resultado_revision': resultado_rev,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def registrar_comprobante_supervisor(request):
    """
    Registrar comprobante de pago bancario (solo supervisores)
    Permite adjuntar imagen del comprobante
    """
    try:
        # Verificar permisos de supervisor
        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']
        
        if not es_supervisor:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para registrar comprobantes. Se requiere rol de Administración o Administrador.'
            })
        
        arqueo_id = request.POST.get('arqueo_id')
        monto = int(request.POST.get('monto', 0))
        banco = request.POST.get('banco', 'ESTADO')
        numero_comprobante = request.POST.get('numero_comprobante', '')
        observaciones = request.POST.get('observaciones', '')
        fecha_deposito = request.POST.get('fecha_deposito')
        imagen = request.FILES.get('imagen_comprobante')
        
        if not arqueo_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de arqueo requerido'
            })
        
        if monto <= 0:
            return JsonResponse({
                'success': False,
                'error': 'El monto debe ser mayor a 0'
            })
        
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)
        
        # Validar que no tenga ya un comprobante bancario verificado
        comprobante_existente = DepositoBancario.objects.filter(
            arqueo=arqueo,
            verificado=True,
            numero_comprobante__gt=''
        ).exists()
        if comprobante_existente:
            return JsonResponse({
                'success': False,
                'error': f'Este arqueo ({arqueo.fecha_arqueo.strftime("%d/%m/%Y")}) ya tiene un comprobante bancario registrado. '
                         'Si necesita corregirlo, primero elimine el existente.'
            })
        
        # Convertir fecha
        from datetime import datetime
        if fecha_deposito:
            fecha_dep = datetime.strptime(fecha_deposito, '%Y-%m-%d').date()
        else:
            fecha_dep = arqueo.fecha_arqueo
        
        # Crear depósito bancario
        deposito = DepositoBancario.objects.create(
            arqueo=arqueo,
            fecha_deposito=fecha_dep,
            monto=monto,
            banco=banco,
            numero_comprobante=numero_comprobante,
            observaciones=observaciones,
            imagen_comprobante=imagen,
            registrado_por=request.user,
            verificado=True,  # Registrado por supervisor = verificado automáticamente
            verificado_por=request.user,
            fecha_verificacion=timezone.now()
        )
        
        print(f"💰 Comprobante bancario registrado - Arqueo {arqueo_id}, Monto: ${monto:,}, Banco: {banco}")
        
        return JsonResponse({
            'success': True,
            'message': f'Comprobante de ${monto:,} registrado exitosamente',
            'deposito': {
                'id': deposito.id,
                'monto': deposito.monto,
                'banco': deposito.get_banco_display(),
                'numero_comprobante': deposito.numero_comprobante,
                'tiene_imagen': bool(deposito.imagen_comprobante)
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al registrar comprobante: {str(e)}'
        })


@login_required
@require_POST
def verificar_deposito(request):
    """
    Verificar un depósito bancario (solo supervisores)
    """
    try:
        # Verificar permisos de supervisor
        rol_usuario = getattr(request.user, 'rol', None)
        es_supervisor = rol_usuario in ['administrador', 'administracion']
        
        if not es_supervisor:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para verificar depósitos.'
            })
        
        data = json.loads(request.body)
        deposito_id = data.get('deposito_id')
        
        if not deposito_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de depósito requerido'
            })
        
        deposito = get_object_or_404(DepositoBancario, id=deposito_id)
        
        DepositoBancario.objects.filter(id=deposito_id).update(
            verificado=True,
            verificado_por=request.user,
            fecha_verificacion=timezone.now()
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Depósito verificado exitosamente'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al verificar depósito: {str(e)}'
        })


@login_required
@require_GET
def obtener_depositos_arqueo(request, arqueo_id):
    """Obtener depósitos bancarios de un arqueo"""
    try:
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)
        
        depositos = DepositoBancario.objects.filter(arqueo=arqueo).order_by('-fecha_deposito')
        
        depositos_data = []
        for dep in depositos:
            depositos_data.append({
                'id': dep.id,
                'fecha_deposito': dep.fecha_deposito.strftime('%d/%m/%Y'),
                'monto': dep.monto,
                'banco': dep.banco,
                'banco_display': dep.get_banco_display(),
                'numero_comprobante': dep.numero_comprobante,
                'observaciones': dep.observaciones,
                'tiene_imagen': bool(dep.imagen_comprobante),
                'imagen_url': dep.imagen_comprobante.url if dep.imagen_comprobante else None,
                'verificado': dep.verificado,
                'verificado_por': dep.verificado_por.username if dep.verificado_por else None,
                'registrado_por': dep.registrado_por.username,
                'fecha_registro': dep.fecha_registro.strftime('%d/%m/%Y %H:%M')
            })
        
        return JsonResponse({
            'success': True,
            'depositos': depositos_data,
            'total': sum(d['monto'] for d in depositos_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener depósitos: {str(e)}'
        })


@login_required
@require_GET
def verificar_ventas_post_cierre(request):
    """
    Verificar si hay ventas registradas después de cerrar el arqueo del día.
    Este es un caso común donde el cajero cierra el arqueo pero sigue vendiendo.
    """
    try:
        fecha_str = request.GET.get('fecha')
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not fecha_str or not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Fecha y sucursal requeridas'
            })
        
        from datetime import datetime, time as dt_time
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Buscar arqueo cerrado para la fecha
        arqueo = ArqueoCaja.objects.filter(
            sucursal=sucursal,
            fecha_arqueo=fecha_obj,
            estado__in=['CERRADO', 'CON_DIFERENCIAS', 'REVISADO']
        ).first()
        
        if not arqueo or not arqueo.fecha_cierre:
            return JsonResponse({
                'success': True,
                'tiene_ventas_post_cierre': False,
                'message': 'No hay arqueo cerrado para verificar'
            })
        
        hora_cierre = arqueo.fecha_cierre
        
        # Buscar tickets creados después del cierre del arqueo (mismo día)
        fin_del_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.max))
        
        tickets_post_cierre = Ticket.objects.filter(
            sucursal=sucursal,
            created_at__gt=hora_cierre,
            created_at__lte=fin_del_dia,
            estado='PAGADO'
        ).select_related().order_by('created_at')
        
        # También buscar DTEs post-cierre
        dtes_post_cierre = Dte.objects.filter(
            sucursal=sucursal,
            fecha_emision=fecha_obj,  # fecha_emision es DateField, no necesita __date
            created_at__gt=hora_cierre,
            created_at__lte=fin_del_dia
        ).exclude(
            tipo_documento__in=['NOTA DE CREDITO', 'GUIA']
        ).order_by('created_at')
        
        cantidad_tickets = tickets_post_cierre.count()
        cantidad_dtes = dtes_post_cierre.count()
        cantidad_total = cantidad_tickets + cantidad_dtes
        
        if cantidad_total == 0:
            return JsonResponse({
                'success': True,
                'tiene_ventas_post_cierre': False,
                'arqueo_id': arqueo.id,
                'hora_cierre': hora_cierre.strftime('%H:%M:%S')
            })
        
        # Calcular monto total de ventas post-cierre
        monto_tickets = sum(t.total or 0 for t in tickets_post_cierre)
        monto_dtes = sum(d.monto_con_iva or 0 for d in dtes_post_cierre)
        monto_total = monto_tickets + monto_dtes
        
        # Generar detalle
        detalle = []
        
        for ticket in tickets_post_cierre[:10]:  # Limitar a 10 para el modal
            metodos_pago = ', '.join([p.metodo_pago for p in ticket.pagos.all()]) if ticket.pagos.exists() else 'N/A'
            detalle.append({
                'hora': ticket.created_at.strftime('%H:%M:%S'),
                'tipo_documento': 'Ticket',
                'numero': ticket.numero_documento or ticket.id,
                'metodo_pago': metodos_pago,
                'monto': ticket.total or 0
            })
        
        for dte in dtes_post_cierre[:10]:
            metodos_pago = ', '.join([p.metodo_pago for p in dte.dte_asociado.all()]) if dte.dte_asociado.exists() else 'N/A'
            detalle.append({
                'hora': dte.created_at.strftime('%H:%M:%S') if dte.created_at else '-',
                'tipo_documento': dte.tipo_documento,
                'numero': dte.numero_documento,
                'metodo_pago': metodos_pago,
                'monto': dte.monto_con_iva or 0
            })
        
        # Ordenar detalle por hora
        detalle.sort(key=lambda x: x['hora'])
        
        return JsonResponse({
            'success': True,
            'tiene_ventas_post_cierre': True,
            'arqueo_id': arqueo.id,
            'hora_cierre': hora_cierre.strftime('%H:%M:%S'),
            'cantidad_ventas': cantidad_total,
            'cantidad_tickets': cantidad_tickets,
            'cantidad_dtes': cantidad_dtes,
            'monto_total': monto_total,
            'monto_tickets': monto_tickets,
            'monto_dtes': monto_dtes,
            'detalle': detalle,
            'mensaje': f'Se encontraron {cantidad_total} ventas por ${monto_total:,} después del cierre a las {hora_cierre.strftime("%H:%M")}'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al verificar ventas post-cierre: {str(e)}'
        })


@login_required
@require_POST
def reabrir_arqueo(request):
    """
    Reabrir un arqueo cerrado para incluir ventas post-cierre.
    Recalcula automáticamente los totales teóricos.
    Permisos con tolerancia de días:
    - administrador: sin límite (configurable via ParametroGlobal)
    - jefe_local / administracion: dentro de N días (default 2)
    Requiere justificación obligatoria. Crea registro de auditoría.
    """
    try:
        from datetime import date as dt_date
        from app.models.caja import HistorialReaperturaArqueo
        from app.models.precios import ParametroGlobal

        rol_usuario = getattr(request.user, 'rol', None)

        data = json.loads(request.body)
        fecha_str = data.get('fecha')
        justificacion = (data.get('justificacion') or '').strip()
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')

        if not fecha_str or not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Fecha y sucursal requeridas'
            })

        if not justificacion or len(justificacion) < 10:
            return JsonResponse({
                'success': False,
                'error': 'Debe ingresar una justificación de al menos 10 caracteres'
            })

        from datetime import datetime
        fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)

        # Verificar permisos y tolerancia de días
        dias_desde_arqueo = (timezone.localdate() - fecha_obj).days

        if rol_usuario == 'administrador':
            param = ParametroGlobal.objects.filter(nombre='DIAS_TOLERANCIA_REAPERTURA_ADMIN').first()
            max_dias = param.valor_entero if param else 0  # 0 = ilimitado
            if max_dias > 0 and dias_desde_arqueo > max_dias:
                return JsonResponse({
                    'success': False,
                    'error': f'Solo puede reabrir arqueos de los últimos {max_dias} días'
                })
        elif rol_usuario in ['jefe_local', 'administracion']:
            param = ParametroGlobal.objects.filter(nombre='DIAS_TOLERANCIA_REAPERTURA_JEFE_LOCAL').first()
            max_dias = param.valor_entero if param else 2
            if dias_desde_arqueo > max_dias:
                return JsonResponse({
                    'success': False,
                    'error': f'Solo puede reabrir arqueos de los últimos {max_dias} días. Han pasado {dias_desde_arqueo} días.'
                })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para reabrir arqueos'
            })

        # Buscar arqueo cerrado para la fecha
        arqueo = ArqueoCaja.objects.filter(
            sucursal=sucursal,
            fecha_arqueo=fecha_obj,
            estado__in=['CERRADO', 'CON_DIFERENCIAS', 'DEPOSITO_DECLARADO', 'DEPOSITO_CONFIRMADO', 'REVISADO']
        ).first()

        if not arqueo:
            return JsonResponse({
                'success': False,
                'error': 'No se encontró un arqueo cerrado para reabrir'
            })

        # Guardar el estado anterior para el log
        estado_anterior = arqueo.estado

        # Crear registro de auditoría
        HistorialReaperturaArqueo.objects.create(
            arqueo=arqueo,
            usuario=request.user,
            estado_anterior=estado_anterior,
            justificacion=justificacion,
        )

        log_accion_caja(request, 'REABRIR_ARQUEO', arqueo, justificacion=justificacion)

        # Reabrir el arqueo
        arqueo.estado = 'ABIERTO'
        arqueo.fecha_cierre = None
        nombre_usuario = request.user.get_full_name() or request.user.username
        arqueo.observaciones = (arqueo.observaciones or '') + f'\n[REABIERTO {timezone.now().strftime("%d/%m/%Y %H:%M")} por {nombre_usuario}] {justificacion}. Estado anterior: {estado_anterior}'
        
        # Recalcular totales teóricos incluyendo las nuevas ventas
        # Esto reutiliza la lógica de generar_cuadratura_caja
        from datetime import time as dt_time
        inicio_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.min))
        fin_dia = timezone.make_aware(datetime.combine(fecha_obj, dt_time.max))
        
        # Resetear totales
        arqueo.total_efectivo_teorico = 0
        arqueo.total_tarjeta_debito_teorico = 0
        arqueo.total_tarjeta_credito_teorico = 0
        arqueo.total_transbank_teorico = 0
        arqueo.total_transferencia_teorico = 0
        arqueo.total_cheque_teorico = 0
        arqueo.total_convenio_teorico = 0
        arqueo.total_hites_teorico = 0
        arqueo.total_tarjetas_comerciales_teorico = 0
        arqueo.total_venta_internet_teorico = 0
        arqueo.total_tickets_teorico = 0
        arqueo.total_boletas_electronicas_teorico = 0
        arqueo.total_facturas_teorico = 0
        arqueo.total_notas_credito_teorico = 0
        
        # Procesar tickets del día completo
        tickets_del_dia = Ticket.objects.filter(
            sucursal=sucursal,
            fecha=fecha_obj,
            estado='PAGADO'
        ).prefetch_related('pagos')
        
        for ticket in tickets_del_dia:
            arqueo.total_tickets_teorico += ticket.total or 0
            
            for pago in ticket.pagos.all():
                metodo = pago.metodo_pago
                monto = pago.monto or 0
                
                if metodo == 'EFECTIVO':
                    arqueo.total_efectivo_teorico += monto
                elif metodo == 'TARJETA_DEBITO':
                    # ✅ TARJETA_DEBITO se considera Transbank (datos migrados y genéricos)
                    arqueo.total_tarjeta_debito_teorico += monto
                    arqueo.total_transbank_teorico += monto
                elif metodo == 'TARJETA_CREDITO':
                    # ✅ TARJETA_CREDITO se considera Transbank (datos migrados y genéricos)
                    arqueo.total_tarjeta_credito_teorico += monto
                    arqueo.total_transbank_teorico += monto
                elif metodo == 'TBK_DEBITO_POS':
                    # ✅ Transbank POS Débito
                    arqueo.total_tarjeta_debito_teorico += monto
                    arqueo.total_transbank_teorico += monto
                elif metodo == 'TBK_CREDITO_POS':
                    # ✅ Transbank POS Crédito
                    arqueo.total_tarjeta_credito_teorico += monto
                    arqueo.total_transbank_teorico += monto
                elif metodo == 'TBK_PREPAGO_POS':
                    # ✅ Transbank POS Prepago (va a débito por convención)
                    arqueo.total_tarjeta_debito_teorico += monto
                    arqueo.total_transbank_teorico += monto
                elif metodo in ['TBK_POS_INTEGRADO', 'TBK_MANUAL']:
                    # ✅ Transbank genérico (datos históricos)
                    arqueo.total_transbank_teorico += monto
                elif metodo == 'TRANSFERENCIA':
                    arqueo.total_transferencia_teorico += monto
                elif metodo == 'CHEQUE':
                    arqueo.total_cheque_teorico += monto
                elif metodo == 'CONVENIO':
                    arqueo.total_convenio_teorico += monto
                elif metodo == 'TARJETA_COMERCIAL':
                    arqueo.total_hites_teorico += monto
                elif metodo == 'VENTA_INTERNET':
                    arqueo.total_venta_internet_teorico += monto
        
        # Procesar DTEs
        folios_tickets = Ticket.objects.filter(
            sucursal=sucursal,
            fecha=fecha_obj
        ).exclude(folio_dte__isnull=True).values_list('folio_dte', flat=True)
        
        dtes_del_dia = Dte.objects.filter(
            sucursal=sucursal,
            fecha_emision=fecha_obj  # fecha_emision es DateField, no necesita __date
        ).exclude(
            numero_documento__in=folios_tickets
        ).prefetch_related('dte_asociado')
        
        for dte in dtes_del_dia:
            monto_dte = dte.monto_con_iva or 0
            
            if dte.tipo_documento == 'BOLETA ELECTRONICA':
                arqueo.total_boletas_electronicas_teorico += monto_dte
            elif dte.tipo_documento == 'FACTURA ELECTRONICA':
                arqueo.total_facturas_teorico += monto_dte
            elif dte.tipo_documento == 'NOTA DE CREDITO':
                arqueo.total_notas_credito_teorico += monto_dte
                pagos_nc = dte.dte_asociado.all()
                tiene_efectivo = pagos_nc.filter(metodo_pago='EFECTIVO').exists()
                if tiene_efectivo:
                    arqueo.total_efectivo_teorico -= monto_dte
        
        # Actualizar totales agregados
        arqueo.total_tarjetas_comerciales_teorico = arqueo.total_hites_teorico
        
        arqueo.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Arqueo reabierto exitosamente',
            'arqueo': {
                'id': arqueo.id,
                'estado': arqueo.estado,
                'total_efectivo_teorico': arqueo.total_efectivo_teorico,
                'total_tickets_teorico': arqueo.total_tickets_teorico,
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
            'error': f'Error al reabrir arqueo: {str(e)}'
        })


@login_required
@require_POST
def cancelar_arqueo(request):
    """Cancelar un arqueo abierto (eliminar)"""
    try:
        data = json.loads(request.body)
        arqueo_id = data.get('arqueo_id')
        
        if not arqueo_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de arqueo requerido'
            })
        
        arqueo = get_object_or_404(ArqueoCaja, id=arqueo_id)
        
        # Solo se puede cancelar arqueos abiertos
        if arqueo.estado != 'ABIERTO':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden cancelar arqueos en estado ABIERTO'
            })
        
        # Verificar que el usuario tiene permiso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if arqueo.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permiso para cancelar este arqueo'
            })
        
        # Eliminar el arqueo
        arqueo_fecha = arqueo.fecha_arqueo
        arqueo.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Arqueo del {arqueo_fecha.strftime("%d/%m/%Y")} cancelado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al cancelar arqueo: {str(e)}'
        })


@login_required
@require_GET
def analisis_fraude_caja(request):
    """
    Análisis de patrones sospechosos en arqueos de caja.
    Solo accesible para administrador/administración.
    """
    try:
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ('administrador', 'administracion'):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver el análisis de fraude.'
            }, status=403)

        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        usuario_id = request.GET.get('usuario_id')
        meses = int(request.GET.get('meses', 3))

        from app.services.analisis_caja import AnalisisFraudeCaja
        servicio = AnalisisFraudeCaja()

        if usuario_id:
            resultado = servicio.analizar_cajero(int(usuario_id), sucursal_id, meses)
            return JsonResponse({'success': True, 'tipo': 'cajero', 'analisis': resultado})
        elif sucursal_id:
            resultados = servicio.analizar_sucursal(int(sucursal_id), meses)
            return JsonResponse({'success': True, 'tipo': 'sucursal', 'analisis': resultados})
        else:
            return JsonResponse({'success': False, 'error': 'Se requiere sucursal_id o usuario_id'})

    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error en análisis: {str(e)}'})


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
                    'hites': arqueo.total_hites_teorico,
                    'total': arqueo.total_tarjetas_comerciales_teorico,
                },
                'venta_internet': {
                    'falabella': arqueo.total_falabella_teorico,
                    'paris': arqueo.total_paris_teorico,
                    'ripley': arqueo.total_ripley_teorico,
                    'mercadopago': arqueo.total_mercadopago_teorico,
                    'klap': arqueo.total_klap_teorico,
                    'total': arqueo.total_venta_internet_teorico,
                },
                'transbank': {
                    'debito': arqueo.total_tarjeta_debito_teorico,
                    'credito': arqueo.total_tarjeta_credito_teorico,
                    'total': arqueo.total_transbank_teorico,
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
            
            # Cierre Transbank
            'cierre_transbank': {
                'debito_fisico': arqueo.cierre_debito_fisico,
                'credito_fisico': arqueo.cierre_credito_fisico,
                'total_fisico': arqueo.cierre_pos_fisico,
                'numero_lote': arqueo.numero_lote_pos,
                'diferencia_debito': arqueo.diferencia_debito,
                'diferencia_credito': arqueo.diferencia_credito,
                'diferencia_total': arqueo.diferencia_transbank,
            },
            
            # Diferencias
            'diferencias': {
                'efectivo': arqueo.diferencia_efectivo,
                'transbank': arqueo.diferencia_transbank,
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
            'resultado_revision': getattr(arqueo, 'resultado_revision', 'PENDIENTE'),
            
            # Fechas
            'fecha_cierre': arqueo.fecha_cierre.strftime('%d/%m/%Y %H:%M') if arqueo.fecha_cierre else '',
            
            # Bitácora visible para cajera
            'bitacora': [{
                'id': obs.id,
                'tipo': obs.tipo,
                'tipo_display': obs.get_tipo_display(),
                'texto': obs.texto,
                'usuario': obs.usuario.get_full_name() or obs.usuario.username,
                'fecha': obs.fecha.strftime('%d/%m/%Y %H:%M'),
            } for obs in arqueo.bitacora.filter(visible_para_cajera=True)[:20]],
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
def validar_password_usuario(request):
    """Validar contraseña del usuario actual para autorizaciones"""
    try:
        data = json.loads(request.body)
        password = data.get('password')
        
        if not password:
            return JsonResponse({
                'success': False,
                'error': 'Contraseña requerida'
            })
        
        # Validar contraseña del usuario actual
        usuario = request.user
        
        if usuario.check_password(password):
            return JsonResponse({
                'success': True,
                'usuario': usuario.username,
                'mensaje': 'Contraseña correcta'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'Contraseña incorrecta'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en validación: {str(e)}'
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
    
    # ✅ Obtener tickets de cambio PENDIENTES de cobro/devolución
    tickets_cambio_pendientes = Ticket.objects.filter(
        sucursal=sucursal_actual,
        modulo_origen='CAMBIO_DEVOLUCION',
        estado='PENDIENTE'
    ).select_related('vendedor').order_by('-created_at')[:20]
    
    tickets_cambio_data = []
    for ticket in tickets_cambio_pendientes:
        # Determinar tipo de operación
        if 'A DEVOLVER AL CLIENTE' in (ticket.observaciones or ''):
            tipo_op = 'DEVOLUCION'
            icono = '💵'
            texto = 'Devolver'
            clase = 'success'
        elif 'A COBRAR AL CLIENTE' in (ticket.observaciones or ''):
            tipo_op = 'COBRO'
            icono = '💰'
            texto = 'Cobrar'
            clase = 'danger'
        else:
            tipo_op = 'DIRECTO'
            icono = '🔄'
            texto = 'Cambio'
            clase = 'info'
        
        tickets_cambio_data.append({
            'correlativo': ticket.correlativo,
            'cliente_nombre': ticket.cliente_nombre or 'Cliente General',
            'cliente_rut': ticket.cliente_rut or '',
            'total': int(ticket.total or 0),
            'tipo_operacion': tipo_op,
            'icono': icono,
            'texto': texto,
            'clase': clase,
            'vendedor': ticket.vendedor.nombre if ticket.vendedor else '-',
            'fecha': ticket.fecha.strftime('%d/%m/%Y') if ticket.fecha else '-',
            'hora': ticket.created_at.strftime('%H:%M') if ticket.created_at else '-',
            'observaciones': ticket.observaciones or '',
        })
    
    # Contar pendientes de revisión gerencial
    revision_pendiente_count = CambioDevolucion.objects.filter(
        sucursal=sucursal_actual,
        requiere_revision_gerencial=True,
        revisado_por_gerencia__isnull=True,
    ).count()

    context = {
        'sucursal_actual': sucursal_actual,
        'tipo_operacion_choices': TIPO_OPERACION_CAMBIO_CHOICES,
        'estado_choices': ESTADO_CAMBIO_CHOICES,
        'motivo_choices': MOTIVO_CAMBIO_CHOICES,
        'condicion_producto_choices': CONDICION_PRODUCTO_CHOICES,
        'metodo_pago_choices': METODO_PAGO_TICKET_CHOICES,
        'tickets_cambio_pendientes': tickets_cambio_data,
        'total_tickets_pendientes': len(tickets_cambio_data),
        'qz_config': _get_qz_config(sucursal_actual_id),
        'user_rol': getattr(request.user, 'rol', ''),
        'revision_pendiente_count': revision_pendiente_count,
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
            'ticket_original', 'ticket_nuevo', 'sucursal', 'solicitado_por', 'aprobado_por',
            'autorizado_por_usuario', 'revisado_por_gerencia', 'nota_credito',
        ).prefetch_related(
            'detalles__producto_original__ProductoTalla__producto',
            'detalles__producto_nuevo__producto',
            'pagos'
        ).filter(sucursal_id=sucursal_id)

        # Aplicar filtros (fecha, tipo, búsqueda — sin estado todavía)
        if fecha_desde:
            queryset = queryset.filter(fecha_solicitud__date__gte=fecha_desde)
        if fecha_hasta:
            queryset = queryset.filter(fecha_solicitud__date__lte=fecha_hasta)
        if tipo_operacion:
            queryset = queryset.filter(tipo_operacion=tipo_operacion)
        if buscar:
            queryset = queryset.filter(
                Q(numero_operacion__icontains=buscar) |
                Q(ticket_original__correlativo__icontains=buscar) |
                Q(ticket_original__cliente_nombre__icontains=buscar) |
                Q(ticket_original__cliente_rut__icontains=buscar) |
                Q(observaciones_cliente__icontains=buscar) |
                Q(observaciones_vendedor__icontains=buscar)
            )

        # Conteos por estado (antes de aplicar filtro de estado del tab)
        conteos_tab = {
            'todos': queryset.count(),
            'solicitados': queryset.filter(estado='SOLICITADO').count(),
            'aprobados': queryset.filter(estado='APROBADO').count(),
            'por_cobrar': queryset.filter(estado__in=['EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE']).count(),
            'completados': queryset.filter(estado='COMPLETADO').count(),
            'cancelados': queryset.filter(estado__in=['CANCELADO', 'RECHAZADO', 'REVERTIDO']).count(),
        }

        # Ahora aplicar filtro de estado del tab activo
        if estado:
            if estado == 'CANCELADO':
                queryset = queryset.filter(estado__in=['CANCELADO', 'RECHAZADO', 'REVERTIDO'])
            elif estado == 'EJECUTADO_COBRO_PENDIENTE':
                queryset = queryset.filter(estado__in=['EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE'])
            else:
                queryset = queryset.filter(estado=estado)

        # Paginación
        paginator = Paginator(queryset, per_page)
        cambios_page = paginator.get_page(page)

        # Serializar datos
        cambios_data = []
        for cambio in cambios_page:
            detalles = cambio.detalles.all()
            total_productos_devueltos = sum(1 for d in detalles if d.producto_original_id)
            total_productos_nuevos = sum(1 for d in detalles if d.producto_nuevo_id)
            cant_devueltos = sum(d.cantidad_original for d in detalles if d.producto_original_id)
            cant_nuevos = sum(d.cantidad_nueva for d in detalles if d.producto_nuevo_id)

            productos_resumen = []
            for d in detalles[:3]:
                if d.producto_original_id:
                    try:
                        nombre = d.producto_original.ProductoTalla.producto.articulo
                    except Exception:
                        nombre = 'Producto'
                    productos_resumen.append({'nombre': nombre[:30], 'tipo': 'devuelto', 'cantidad': d.cantidad_original})
                if d.producto_nuevo_id:
                    try:
                        nombre = d.producto_nuevo.producto.articulo
                    except Exception:
                        nombre = 'Producto'
                    productos_resumen.append({'nombre': nombre[:30], 'tipo': 'nuevo', 'cantidad': d.cantidad_nueva})

            solicitante_nombre = ''
            if cambio.solicitado_por:
                solicitante_nombre = cambio.solicitado_por.get_full_name() or cambio.solicitado_por.username

            ticket_pendiente_pago = False
            if cambio.ticket_nuevo and cambio.ticket_nuevo.estado == 'PENDIENTE':
                ticket_pendiente_pago = True

            cambios_data.append({
                'id': cambio.id,
                'numero_operacion': cambio.numero_operacion,
                'fecha_solicitud': cambio.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
                'fecha_solicitud_iso': cambio.fecha_solicitud.isoformat(),
                'tipo_operacion': cambio.tipo_operacion,
                'tipo_operacion_display': cambio.get_tipo_operacion_display(),
                'estado': cambio.estado,
                'estado_display': cambio.get_estado_display(),
                'ticket_original': cambio.ticket_original.correlativo,
                'ticket_nuevo': {
                    'id': cambio.ticket_nuevo.id,
                    'correlativo': cambio.ticket_nuevo.correlativo,
                    'estado': cambio.ticket_nuevo.estado,
                    'estado_display': cambio.ticket_nuevo.get_estado_display(),
                    'metodo_pago': cambio.ticket_nuevo.metodo_pago,
                    'tipo_dte': cambio.ticket_nuevo.tipo_dte or ''
                } if cambio.ticket_nuevo else None,
                'ticket_pendiente_pago': ticket_pendiente_pago,
                'cliente_nombre': cambio.ticket_original.cliente_nombre or 'Sin nombre',
                'cliente_rut': cambio.ticket_original.cliente_rut or '',
                'monto_original': float(cambio.monto_original),
                'monto_nuevo': float(cambio.monto_nuevo),
                'diferencia_monto': float(cambio.diferencia_monto),
                'motivo_principal': cambio.get_motivo_principal_display(),
                'motivo_principal_codigo': cambio.motivo_principal,
                'solicitado_por': cambio.solicitado_por.username,
                'solicitado_por_nombre': solicitante_nombre,
                'aprobado_por': cambio.aprobado_por.username if cambio.aprobado_por else '',
                'fecha_aprobacion': cambio.fecha_aprobacion.strftime('%d/%m/%Y %H:%M') if cambio.fecha_aprobacion else '',
                'fecha_completado': cambio.fecha_completado.strftime('%d/%m/%Y %H:%M') if cambio.fecha_completado else '',
                'fecha_limite': cambio.fecha_limite_cambio.strftime('%d/%m/%Y'),
                'dias_desde_venta': cambio.dias_desde_venta,
                'dentro_del_plazo': cambio.dentro_del_plazo,
                'puede_completar': cambio.puede_completar,
                'requiere_pago_adicional': cambio.requiere_pago_adicional,
                'genera_devolucion': cambio.genera_devolucion,
                'total_productos_devueltos': total_productos_devueltos,
                'total_productos_nuevos': total_productos_nuevos,
                'cant_devueltos': cant_devueltos,
                'cant_nuevos': cant_nuevos,
                'productos_resumen': productos_resumen,
                'requiere_autorizacion': cambio.requiere_autorizacion,
                'autorizado_excepcion': cambio.autorizado_excepcion,
                'cobro_pendiente': cambio.cobro_pendiente,
                'devolucion_pendiente': cambio.devolucion_pendiente,
                'tiene_obligacion_pendiente': cambio.tiene_obligacion_pendiente,
                # Nuevos campos de trazabilidad
                'es_fuera_de_plazo': cambio.es_fuera_de_plazo,
                'dias_fuera_de_plazo': cambio.dias_fuera_de_plazo,
                'tipo_cambio_especial': cambio.tipo_cambio_especial,
                'es_autorizacion_cross_branch': cambio.es_autorizacion_cross_branch,
                'es_cambio_concepto': cambio.es_cambio_concepto,
                'autorizado_por_usuario': cambio.autorizado_por_usuario.get_full_name() if cambio.autorizado_por_usuario else None,
                'score_riesgo': cambio.score_riesgo,
                'requiere_revision_gerencial': cambio.requiere_revision_gerencial,
                'revisado_por_gerencia': cambio.revisado_por_gerencia.get_full_name() if cambio.revisado_por_gerencia else None,
                # Nota de Crédito
                'nc_generada': cambio.nc_generada,
                'metodo_devolucion': cambio.metodo_devolucion,
                'metodo_devolucion_display': cambio.get_metodo_devolucion_display() if cambio.metodo_devolucion != 'SIN_NC' else '',
                'nota_credito_numero': cambio.nota_credito.numero_documento if cambio.nota_credito_id else None,
            })

        total_diferencia = queryset.aggregate(
            total=Sum('diferencia_monto')
        )['total'] or 0

        cambios_fuera_plazo = queryset.filter(es_fuera_de_plazo=True).count()
        cambios_cross_branch = queryset.filter(es_autorizacion_cross_branch=True).count()
        cambios_revision_pendiente = queryset.filter(
            requiere_revision_gerencial=True,
            revisado_por_gerencia__isnull=True,
        ).count()

        return JsonResponse({
            'success': True,
            'cambios': cambios_data,
            'conteos_tab': conteos_tab,
            'pagination': {
                'current_page': cambios_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': cambios_page.has_next(),
                'has_previous': cambios_page.has_previous(),
            },
            'estadisticas': {
                'total_cambios': conteos_tab['todos'],
                'cambios_pendientes': conteos_tab['solicitados'],
                'cambios_aprobados': conteos_tab['aprobados'],
                'cambios_por_cobrar': conteos_tab['por_cobrar'],
                'cambios_completados': conteos_tab['completados'],
                'total_diferencia': float(total_diferencia),
                'cambios_fuera_plazo': cambios_fuera_plazo,
                'cambios_cross_branch': cambios_cross_branch,
                'cambios_revision_pendiente': cambios_revision_pendiente,
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
                
                # Primero: intentar encontrar el ticket ORIGINAL del POS (tiene descuentos correctos)
                if dte_original.referencias and 'TICKET-' in dte_original.referencias:
                    try:
                        corr_orig = dte_original.referencias.split('TICKET-')[1].strip().split()[0]
                        ticket_original = Ticket.objects.filter(
                            correlativo=corr_orig,
                            sucursal_id=sucursal_id,
                            estado='PAGADO'
                        ).first()
                    except Exception:
                        pass
                
                # Si no se encontró el original, buscar ticket de referencia existente
                if not ticket_original:
                    ticket_original = Ticket.objects.filter(
                        observaciones__icontains=f'para DTE #{documento_numero} -'
                    ).first()
                
                if not ticket_original:
                    ticket_original = Ticket.objects.filter(
                        observaciones__icontains=f'DTE #{documento_numero} -'
                    ).first()
                
                if not ticket_original:
                    # Crear ticket de referencia desde el DTE
                    
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
                    
                    # Copiar productos del DTE al ticket y crear mapeo (con descuentos)
                    mapeo_productos = {}  # dte_producto_id → ticket_producto_id
                    es_boleta_ref = dte_original.tipo_documento in ['39', '41', 'BOLETA ELECTRONICA', 'BOLETA EXENTA']
                    
                    for dp in dte_original.dte_productos.all():
                        dcto_u = 0
                        if dp.descuento_monto and dp.stock and dp.stock > 0:
                            dcto_u = int(dp.descuento_monto / dp.stock)
                        elif es_boleta_ref and dp.monto_item and dp.stock and dp.stock > 0:
                            precio_ef = int(dp.monto_item / dp.stock)
                            if precio_ef < dp.precio:
                                dcto_u = dp.precio - precio_ef
                        sub = (dp.precio - dcto_u) * dp.stock

                        tp = Ticket_Productos.objects.create(
                            idTicket=ticket_original,
                            ProductoTalla=dp.productoTalla,
                            stock=dp.stock,
                            precio=dp.precio,
                            precio_original=dp.precio,
                            descuento_unitario=dcto_u,
                            subtotal=sub,
                            porcentaje_descuento=dp.descuento_pct or 0,
                            descripcion_linea=dp.descripcion if not dp.productoTalla else None,
                            es_pendiente_despacho=dp.es_pendiente_despacho,
                        )
                        mapeo_productos[dp.id] = tp.id
                    
                    # Guardar mapeo en la sesión o en el ticket para referencia
                    ticket_original.observaciones += f"\n[MAPEO_PRODUCTOS: {mapeo_productos}]"
                    ticket_original.save()

                    
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
        fecha_base_plazo = None

        # Caso 1: el backend recibió el DTE directamente
        if dte_original:
            fecha_base_plazo = dte_original.fecha_emision

        # Caso 2: el frontend indica que el documento original era un DTE
        if not fecha_base_plazo:
            dte_numero_original = data.get('dte_numero_original')
            if dte_numero_original:
                try:
                    dte_query = Dte.objects.filter(numero_documento=dte_numero_original)
                    if sucursal:
                        dte_query = dte_query.filter(sucursal=sucursal)
                    dte_ref = dte_query.order_by('-fecha_emision').first()
                    if dte_ref:
                        fecha_base_plazo = dte_ref.fecha_emision
                except Exception:
                    pass

        # Caso 3: ticket puro → usar fecha del ticket
        if not fecha_base_plazo:
            fecha_base_plazo = ticket_original.fecha

        fecha_limite = fecha_base_plazo + timedelta(days=30)
        fuera_de_plazo = timezone.localdate() > fecha_limite
        
        # Permitir cambios fuera de plazo SOLO con autorización de supervisor
        supervisor_username = data.get('supervisor_username', '').strip()
        supervisor_password = data.get('supervisor_password', '')
        # Retrocompatibilidad: si solo viene codigo_autorizacion_supervisor, usar como password
        if not supervisor_password and data.get('codigo_autorizacion_supervisor'):
            supervisor_password = data.get('codigo_autorizacion_supervisor')
        supervisor_autorizo = False
        supervisor = None
        dias_fuera = 0

        if fuera_de_plazo:
            dias_fuera = (timezone.localdate() - fecha_limite).days

            if not supervisor_password:
                return JsonResponse({
                    'success': False,
                    'error': f'El plazo para cambios venció el {fecha_limite.strftime("%d/%m/%Y")}',
                    'requiere_autorizacion': True,
                    'fecha_limite': fecha_limite.strftime('%d/%m/%Y'),
                    'fecha_compra': fecha_base_plazo.strftime('%d/%m/%Y'),
                    'dias_transcurridos': (timezone.localdate() - fecha_base_plazo).days,
                    'dias_fuera_de_plazo': dias_fuera,
                })

            from django.contrib.auth import authenticate
            from django.contrib.auth.models import User

            # Autenticación segura con username directo (O(1) en vez de O(n))
            if supervisor_username:
                supervisor = authenticate(username=supervisor_username, password=supervisor_password)
            else:
                # Fallback: intentar con username más comunes (email, rut)
                for field in ['username', 'email']:
                    try:
                        user_obj = User.objects.filter(
                            is_active=True, **{field: supervisor_password}
                        ).first()
                        if user_obj:
                            break
                    except Exception:
                        pass
                # Si no se proporcionó username, autenticar por password con intento limitado
                if not supervisor:
                    supervisor = authenticate(username=supervisor_username, password=supervisor_password) if supervisor_username else None

            if supervisor:
                # Verificar rol de supervisor
                rol = getattr(supervisor, 'rol', '')
                tiene_rol = rol in ['administrador', 'administracion', 'jefe_local']
                tiene_grupo = supervisor.groups.filter(
                    name__in=['Supervisor', 'Administrador', 'Encargado', 'Gerente']
                ).exists()
                if not tiene_rol and not tiene_grupo:
                    supervisor = None

            if not supervisor:
                return JsonResponse({
                    'success': False,
                    'error': 'Credenciales inválidas o el usuario no tiene permisos de supervisor. Ingrese usuario y contraseña del supervisor.',
                    'requiere_autorizacion': True,
                })

            supervisor_autorizo = True
        
        # Validar que no existan cambios con obligaciones financieras pendientes para este ticket
        cambios_con_pago_pendiente = CambioDevolucion.objects.filter(
            ticket_original=ticket_original,
            estado__in=['EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE']
        ).first()
        
        if not cambios_con_pago_pendiente:
            # También verificar el patrón legacy: COMPLETADO pero ticket_nuevo PENDIENTE
            cambios_con_pago_pendiente = CambioDevolucion.objects.filter(
                ticket_original=ticket_original,
                estado='COMPLETADO',
                ticket_nuevo__estado='PENDIENTE'
            ).first()
        
        if cambios_con_pago_pendiente:
            return JsonResponse({
                'success': False,
                'error': f'Existe un cambio anterior ({cambios_con_pago_pendiente.numero_operacion}) con pago/devolución pendiente para este documento. '
                         f'Debe completar esa obligación antes de crear un nuevo cambio.'
            })
        
        with transaction.atomic():
            # Cambios por concepto: monto viene directamente del frontend
            es_concepto = tipo_operacion in ('CAMBIO_CONCEPTO', 'DEVOLUCION_CONCEPTO')
            if es_concepto:
                monto_original_calculado = int(data.get('concepto_monto_original', 0))
            else:
                # Calcular monto_original basado en el precio efectivo (con descuento aplicado)
                monto_original_calculado = 0
            for item in productos_cambio:
                if es_concepto:
                    break  # No iterar productos para cambios por concepto
                try:
                    ticket_producto = Ticket_Productos.objects.get(
                        idTicket=ticket_original,
                        id=item['ticket_producto_id']
                    )
                    cantidad_cambio = item.get('cantidad', 0)
                    precio_efectivo = ticket_producto.precio - (ticket_producto.descuento_unitario or 0)
                    monto_original_calculado += precio_efectivo * cantidad_cambio
                except Ticket_Productos.DoesNotExist:
                    pass
            
            # Crear cambio/devolución con el monto correcto
            obs_vendedor = data.get('observaciones_vendedor', '')
            if supervisor_autorizo:
                obs_vendedor = f'[AUTORIZADO FUERA DE PLAZO por {supervisor.get_full_name() or supervisor.username}] {obs_vendedor}'.strip()

            # Determinar tipo especial y cross-branch
            tipo_especial = 'NORMAL'
            es_cross_branch = False
            sucursal_supervisor = None

            if fuera_de_plazo:
                tipo_especial = 'FUERA_PLAZO'

            if tipo_operacion in ('CAMBIO_CONCEPTO', 'DEVOLUCION_CONCEPTO'):
                tipo_especial = 'CONCEPTO'

            if supervisor:
                # Obtener sucursal del supervisor
                try:
                    from .models import PerfilUsuario
                    perfil_sup = getattr(supervisor, 'perfil', None)
                    if perfil_sup:
                        sucursal_supervisor = perfil_sup.sucursal
                    if not sucursal_supervisor:
                        sucursal_supervisor = getattr(supervisor, 'sucursal', None)
                except Exception:
                    pass
                es_cross_branch = sucursal_supervisor and sucursal_supervisor.id != sucursal.id

            # Crear registro de autorización con trazabilidad completa
            registro_auth = None
            if supervisor_autorizo:
                from .models import RegistroAutorizacion
                registro_auth = RegistroAutorizacion.objects.create(
                    usuario_solicitante=request.user,
                    usuario_autorizador=supervisor,
                    tipo_operacion='APROBACION_CAMBIO',
                    descripcion=f'Autorización fuera de plazo ({dias_fuera} días) por {supervisor.get_full_name() or supervisor.username}',
                    ip_origen=request.META.get('REMOTE_ADDR'),
                    exitoso=True,
                    sucursal_solicitante=sucursal,
                    sucursal_autorizador=sucursal_supervisor,
                    es_cross_branch=es_cross_branch,
                    requiere_revision=es_cross_branch or dias_fuera > 15,
                    datos_adicionales={
                        'dias_fuera_de_plazo': dias_fuera,
                        'fecha_limite': fecha_limite.strftime('%Y-%m-%d'),
                        'fecha_compra': fecha_base_plazo.strftime('%Y-%m-%d'),
                        'supervisor_username': supervisor.username,
                        'supervisor_sucursal': str(sucursal_supervisor) if sucursal_supervisor else None,
                    }
                )

            # Determinar si requiere revisión gerencial (auto-escalamiento)
            requiere_revision = (
                fuera_de_plazo or
                es_cross_branch or
                monto_original_calculado > 200000  # Umbral configurable
            )

            cambio = CambioDevolucion.objects.create(
                ticket_original=ticket_original,
                sucursal=sucursal,
                tipo_operacion=tipo_operacion,
                monto_original=monto_original_calculado,
                motivo_principal=motivo_principal,
                observaciones_cliente=data.get('observaciones_cliente', ''),
                observaciones_vendedor=obs_vendedor,
                solicitado_por=request.user,
                requiere_autorizacion=True if supervisor_autorizo else data.get('requiere_autorizacion', False),
                fecha_limite_cambio=fecha_limite,
                # Nuevos campos de trazabilidad
                autorizado_por_usuario=supervisor if supervisor_autorizo else None,
                sucursal_autorizador=sucursal_supervisor if supervisor_autorizo else None,
                es_autorizacion_cross_branch=es_cross_branch,
                es_fuera_de_plazo=fuera_de_plazo,
                dias_fuera_de_plazo=dias_fuera if fuera_de_plazo else 0,
                tipo_cambio_especial=tipo_especial,
                registro_autorizacion=registro_auth,
                es_cambio_concepto=tipo_operacion in ('CAMBIO_CONCEPTO', 'DEVOLUCION_CONCEPTO'),
                concepto_descripcion=data.get('concepto_descripcion', ''),
                concepto_monto_original=data.get('concepto_monto_original'),
                documento_referencia_legacy=data.get('documento_referencia_legacy', ''),
                requiere_revision_gerencial=requiere_revision,
            )

            # Vincular registro de autorización al cambio
            if registro_auth:
                registro_auth.cambio_devolucion = cambio
                registro_auth.save(update_fields=['cambio_devolucion'])
            
            # Procesar productos
            monto_nuevo_total = 0
            monto_original_real = 0  # Recalcular para asegurar consistencia
            productos_procesados = set()  # Para evitar duplicar devoluciones
            
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
                
                # ✅ CORREGIDO: Detectar si es un producto nuevo ADICIONAL
                es_producto_adicional = item.get('es_producto_adicional', False) or (cantidad_cambio == 0 and item.get('producto_nuevo_id'))
                
                # Solo validar y contar devolución si NO es producto adicional
                if not es_producto_adicional:
                    if cantidad_cambio <= 0 or cantidad_cambio > ticket_producto.stock:
                        raise ValidationError(f'Cantidad inválida para {ticket_producto.ProductoTalla.producto.articulo}')
                    
                    # Solo sumar al monto original si no hemos procesado este producto ya
                    producto_key = f"{ticket_producto.id}_{cantidad_cambio}"
                    if producto_key not in productos_procesados:
                        precio_efectivo = ticket_producto.precio - (ticket_producto.descuento_unitario or 0)
                        monto_original_real += precio_efectivo * cantidad_cambio
                        productos_procesados.add(producto_key)
                
                # Producto nuevo (si es cambio)
                producto_nuevo = None
                precio_nuevo = 0
                cantidad_nueva = 0
                
                if tipo_operacion in ['CAMBIO_SIMPLE', 'CAMBIO_CON_DIFERENCIA']:
                    producto_nuevo_id = item.get('producto_nuevo_id')
                    if producto_nuevo_id:
                        try:
                            producto_nuevo = Producto_Talla.objects.get(id=producto_nuevo_id)
                            precio_catalogo = producto_nuevo.producto.precioventa
                            
                            # ✅ Usar el precio enviado desde el frontend si es mayor o igual al precio catálogo
                            precio_enviado = item.get('precio_nuevo', 0)
                            if precio_enviado and precio_enviado >= precio_catalogo:
                                precio_nuevo = precio_enviado
                            else:
                                precio_nuevo = precio_catalogo
                            
                            # Usar la cantidad enviada o 1 por defecto
                            cantidad_nueva = item.get('cantidad_nueva', 1) or 1
                            monto_nuevo_total += precio_nuevo * cantidad_nueva
                        except Producto_Talla.DoesNotExist:
                            raise ValidationError(f'Producto nuevo no encontrado')
                
                # ✅ Crear detalle del cambio
                # Separar la creación según sea devolución, cambio o producto adicional
                
                if es_producto_adicional:
                    # Producto ADICIONAL: solo producto nuevo, sin devolución asociada
                    if producto_nuevo:
                        detalle = CambioDevolucionDetalle.objects.create(
                            cambio_devolucion=cambio,
                            producto_original=None,  # No hay producto original
                            cantidad_original=0,
                            producto_nuevo=producto_nuevo,
                            cantidad_nueva=cantidad_nueva,
                            precio_nuevo=precio_nuevo,
                            precio_original_unitario=0,
                            condicion_producto='PERFECTO',
                            apto_para_venta=True,
                            observaciones=item.get('observaciones', '') + ' [PRODUCTO ADICIONAL]'
                        )
                else:
                    # Producto con DEVOLUCIÓN (puede o no tener producto nuevo asociado)
                    if cantidad_cambio > 0:
                        precio_efectivo_unitario = ticket_producto.precio - (ticket_producto.descuento_unitario or 0)
                        detalle = CambioDevolucionDetalle.objects.create(
                            cambio_devolucion=cambio,
                            producto_original=ticket_producto,
                            cantidad_original=cantidad_cambio,
                            producto_nuevo=producto_nuevo,
                            cantidad_nueva=cantidad_nueva,
                            precio_nuevo=precio_nuevo,
                            precio_original_unitario=precio_efectivo_unitario,
                            condicion_producto=item.get('condicion_producto', 'PERFECTO'),
                            apto_para_venta=item.get('apto_para_venta', True),
                            observaciones=item.get('observaciones', '')
                        )
            
            # ✅ Usar el monto original recalculado para mayor precisión
            if monto_original_real > 0:
                cambio.monto_original = monto_original_real
            
            cambio.monto_nuevo = monto_nuevo_total
            cambio.diferencia_monto = monto_nuevo_total - float(cambio.monto_original)
            
            # VALIDACIÓN: En CAMBIOS no se permite diferencia a favor del cliente
            if tipo_operacion in ['CAMBIO_SIMPLE', 'CAMBIO_CON_DIFERENCIA']:
                if cambio.diferencia_monto < 0:
                    raise ValidationError(
                        f'En un CAMBIO no se permite que los productos nuevos tengan menor valor que los devueltos. '
                        f'Diferencia: ${abs(cambio.diferencia_monto):,.0f} a favor del cliente. '
                        f'Para devolver dinero al cliente, use DEVOLUCIÓN en lugar de CAMBIO.'
                    )
            
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
                'solicitado_por', 'aprobado_por', 'nota_credito'
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
            'cobro_pendiente': cambio.cobro_pendiente,
            'devolucion_pendiente': cambio.devolucion_pendiente,
            'tiene_obligacion_pendiente': cambio.tiene_obligacion_pendiente,
            
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

            # Trazabilidad y control
            'es_fuera_de_plazo': cambio.es_fuera_de_plazo,
            'dias_fuera_de_plazo': cambio.dias_fuera_de_plazo,
            'tipo_cambio_especial': cambio.tipo_cambio_especial,
            'es_autorizacion_cross_branch': cambio.es_autorizacion_cross_branch,
            'es_cambio_concepto': cambio.es_cambio_concepto,
            'concepto_descripcion': cambio.concepto_descripcion or '',
            'autorizado_por_usuario': cambio.autorizado_por_usuario.get_full_name() if cambio.autorizado_por_usuario else None,
            'sucursal_autorizador': cambio.sucursal_autorizador.alias if cambio.sucursal_autorizador else None,
            'score_riesgo': cambio.score_riesgo,
            'requiere_revision_gerencial': cambio.requiere_revision_gerencial,
            'revisado_por_gerencia': cambio.revisado_por_gerencia.get_full_name() if cambio.revisado_por_gerencia else None,
            'fecha_revision_gerencia': cambio.fecha_revision_gerencia.strftime('%d/%m/%Y %H:%M') if cambio.fecha_revision_gerencia else None,
            'notas_revision_gerencia': cambio.notas_revision_gerencia or '',

            # Tickets
            'ticket_original': {
                'correlativo': cambio.ticket_original.correlativo,
                'fecha': cambio.ticket_original.fecha.strftime('%d/%m/%Y'),
                'total': float(cambio.ticket_original.total),
                'cliente_nombre': cambio.ticket_original.cliente_nombre or '',
                'cliente_rut': cambio.ticket_original.cliente_rut or '',
                'vendedor': cambio.ticket_original.vendedor.nombre if cambio.ticket_original.vendedor else '',
                'vendedor_id': cambio.ticket_original.vendedor.id if cambio.ticket_original.vendedor else None,
                'vendedor_codigo': cambio.ticket_original.vendedor.codigo_vendedor if cambio.ticket_original.vendedor else '',
            },
            'ticket_nuevo': {
                'correlativo': cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else '',
                'total': float(cambio.ticket_nuevo.total) if cambio.ticket_nuevo else 0,
            } if cambio.ticket_nuevo else None,

            # Nota de Crédito
            'nc_generada': cambio.nc_generada,
            'metodo_devolucion': cambio.metodo_devolucion,
            'metodo_devolucion_display': cambio.get_metodo_devolucion_display() if cambio.metodo_devolucion != 'SIN_NC' else '',
            'fecha_nc': cambio.fecha_nc.strftime('%d/%m/%Y %H:%M') if cambio.fecha_nc else None,
            'nota_credito': {
                'id': cambio.nota_credito.id,
                'numero': cambio.nota_credito.numero_documento,
                'monto': float(cambio.nota_credito.monto_con_iva),
                'fecha': cambio.nota_credito.fecha_emision.strftime('%d/%m/%Y'),
                'estado': cambio.nota_credito.estado_dte,
            } if cambio.nota_credito else None,
        }
        
        # Detalles de productos
        productos_detalle = []
        for detalle in cambio.detalles.all():
            producto_original = detalle.producto_original
            
            # Manejar producto original (puede ser NULL para productos adicionales)
            producto_original_data = None
            if producto_original:
                producto_original_data = {
                    'sku': producto_original.ProductoTalla.sku,
                    'articulo': producto_original.ProductoTalla.producto.articulo,
                    'descripcion': producto_original.ProductoTalla.producto.descripcion,
                    'talla': producto_original.ProductoTalla.talla,
                    'cantidad_original': producto_original.stock,
                    'precio_unitario': float(producto_original.precio),
                }
            
            productos_detalle.append({
                'id': detalle.id,
                'producto_original': producto_original_data,
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
        for hist in cambio.historial.order_by('timestamp'):
            historial_data.append({
                'id': hist.id,
                'accion': hist.accion,
                'accion_display': hist.get_accion_display(),
                'estado_anterior': hist.estado_anterior or '',
                'estado_nuevo': hist.estado_nuevo or '',
                'usuario': hist.usuario.username if hist.usuario else '-',
                'descripcion': hist.descripcion,
                'timestamp': hist.timestamp.strftime('%d/%m/%Y %H:%M:%S'),
                'datos_adicionales': hist.datos_adicionales or {},
            })
        
        # Datos del ticket nuevo para impresión
        ticket_data = None
        if cambio.ticket_nuevo:
            ticket_data = construir_ticket_data(cambio.ticket_nuevo)
            ticket_data['es_ticket_cambio'] = True
            ticket_data['numero_operacion']            = cambio.numero_operacion
            ticket_data['ticket_original_correlativo'] = (
                cambio.ticket_original.correlativo if cambio.ticket_original else None
            )
            ticket_data['tipo_operacion']         = cambio.tipo_operacion
            ticket_data['tipo_operacion_display']  = cambio.get_tipo_operacion_display()
            ticket_data['monto_original']  = int(cambio.monto_original)
            ticket_data['monto_nuevo']     = int(cambio.monto_nuevo)
            ticket_data['diferencia_monto'] = int(cambio.diferencia_monto)

        return JsonResponse({
            'success': True,
            'cambio': cambio_data,
            'productos': productos_detalle,
            'pagos': pagos_data,
            'historial': historial_data,
            'ticket_data': ticket_data  # Datos completos del ticket para impresión
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener detalle: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def cancelar_cambio_devolucion(request):
    """Cancelar una solicitud de cambio/devolución"""
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        
        if not cambio_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de cambio requerido'
            })
        
        cambio = get_object_or_404(CambioDevolucion, id=cambio_id)
        
        # Verificar que sea de la sucursal actual
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if cambio.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para cancelar este cambio'
            })
        
        # Se puede cancelar si está en SOLICITADO o APROBADO
        if cambio.estado not in ['SOLICITADO', 'APROBADO']:
            return JsonResponse({
                'success': False,
                'error': f'No se puede cancelar un cambio en estado {cambio.get_estado_display()}'
            })
        
        # Cancelar el cambio
        cambio.estado = 'CANCELADO'
        cambio.observaciones_aprobacion = f'Cancelado por {request.user.username} el {timezone.now().strftime("%d/%m/%Y %H:%M")}'
        cambio.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Cambio {cambio.numero_operacion} cancelado correctamente'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al cancelar cambio: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def revertir_cambio_devolucion(request):
    """Revertir un cambio completado cuyo ticket aún no fue pagado.
    Deshace todos los movimientos de stock y cancela el ticket pendiente."""
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        motivo = data.get('motivo', '')

        if not cambio_id:
            return JsonResponse({'success': False, 'error': 'ID de cambio requerido'})

        cambio = get_object_or_404(CambioDevolucion, id=cambio_id)

        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if cambio.sucursal_id != int(sucursal_id):
            return JsonResponse({'success': False, 'error': 'No tiene acceso a este cambio'})

        estados_revertibles = ['COMPLETADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE', 'EJECUTADO']
        if cambio.estado not in estados_revertibles:
            return JsonResponse({'success': False, 'error': f'Solo se pueden revertir cambios ejecutados o completados, estado actual: {cambio.get_estado_display()}'})

        if not cambio.ticket_nuevo or cambio.ticket_nuevo.estado not in ('PENDIENTE', 'PAGADO'):
            if cambio.ticket_nuevo and cambio.ticket_nuevo.estado == 'PAGADO' and cambio.estado == 'COMPLETADO':
                return JsonResponse({'success': False, 'error': 'Este cambio ya fue pagado y completado, no se puede revertir'})
            if not cambio.ticket_nuevo:
                return JsonResponse({'success': False, 'error': 'Este cambio no tiene ticket asociado para revertir'})

        sucursal = get_object_or_404(Sucursal, id=sucursal_id)

        with transaction.atomic():
            # 1) Revertir EGRESOS: devolver stock de productos nuevos que fueron entregados
            for item in cambio.detalles.all():
                if item.producto_nuevo and item.cantidad_nueva:
                    producto_talla_nuevo = item.producto_nuevo
                    producto_talla_nuevo.stock += item.cantidad_nueva
                    producto_talla_nuevo.save()

                    Movimientos_Producto.objects.create(
                        ProductoTalla=producto_talla_nuevo,
                        tipo_movimiento='INGRESO',
                        concepto='REVERSION_CAMBIO',
                        cantidad=item.cantidad_nueva,
                        responsable=request.user.username,
                        sucursal_destino=sucursal,
                        precio=int(item.precio_nuevo),
                        costo=0,
                        estado='COMPLETADO',
                        observaciones=f'REVERSION - Cambio #{cambio.numero_operacion}. Producto nuevo devuelto al stock.'
                    )

            # 2) Revertir INGRESOS: descontar stock de productos devueltos que se habían re-ingresado
            for item in cambio.detalles.filter(producto_original__isnull=False, cantidad_original__gt=0):
                if item.apto_para_venta:
                    producto_talla_devuelto = item.producto_original.ProductoTalla
                    producto_talla_devuelto.stock -= item.cantidad_original
                    producto_talla_devuelto.save()

                    Movimientos_Producto.objects.create(
                        ProductoTalla=producto_talla_devuelto,
                        tipo_movimiento='EGRESO',
                        concepto='REVERSION_CAMBIO',
                        cantidad=item.cantidad_original,
                        responsable=request.user.username,
                        sucursal_origen=sucursal,
                        precio=int(item.precio_original_unitario),
                        costo=0,
                        estado='COMPLETADO',
                        observaciones=f'REVERSION - Cambio #{cambio.numero_operacion}. Se revierte ingreso de devolución.'
                    )

            # 3) Cancelar ticket pendiente
            ticket = cambio.ticket_nuevo
            ticket.estado = 'ANULADO'
            ticket.observaciones = (ticket.observaciones or '') + f'\n\nANULADO por reversión de cambio #{cambio.numero_operacion}'
            ticket.save()

            # 4) Cancelar ticket de diferencia si existe
            if cambio.ticket_diferencia and cambio.ticket_diferencia.estado == 'PENDIENTE':
                cambio.ticket_diferencia.estado = 'ANULADO'
                cambio.ticket_diferencia.observaciones = (cambio.ticket_diferencia.observaciones or '') + f'\n\nANULADO por reversión de cambio #{cambio.numero_operacion}'
                cambio.ticket_diferencia.save()

            # 5) Cambiar estado del cambio
            estado_anterior = cambio.estado
            cambio.estado = 'REVERTIDO'
            cambio.save()

            # 6) Registrar historial
            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                accion='REVERTIDO',
                estado_anterior=estado_anterior,
                estado_nuevo='REVERTIDO',
                usuario=request.user,
                descripcion=f'Cambio revertido por {request.user.get_full_name() or request.user.username}. Stock restaurado, ticket #{ticket.correlativo} anulado.' + (f' Motivo: {motivo}' if motivo else ''),
                datos_adicionales={
                    'motivo': motivo,
                    'ticket_anulado': ticket.correlativo,
                    'fecha_reversion': timezone.now().isoformat()
                }
            )

        return JsonResponse({
            'success': True,
            'message': f'Cambio {cambio.numero_operacion} revertido exitosamente. Stock restaurado.',
            'nuevo_estado': 'REVERTIDO'
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': f'Error al revertir cambio: {str(e)}'})


@login_required
@require_POST
@csrf_exempt
def aprobar_cambio_devolucion(request):
    """Aprobar o rechazar una solicitud de cambio/devolución.
    NOTA: Esta función es el camino alternativo (solo aprueba, no genera ticket ni mueve stock).
    La UI principal usa aprobar_cambio_generar_ticket() que hace todo en un solo paso.
    Se mantiene para compatibilidad con posibles integraciones externas."""
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
        
        # Verificar plazo (permitir si fue autorizado fuera de plazo)
        fue_autorizado_fuera_plazo = '[AUTORIZADO FUERA DE PLAZO' in (cambio.observaciones_vendedor or '')
        if not cambio.dentro_del_plazo and not fue_autorizado_fuera_plazo:
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
def ejecutar_cambio_devolucion(request):
    """Ejecutar un cambio/devolución aprobado: generar tickets y movimientos de inventario.
    NOTA: Camino alternativo desde estado APROBADO. La UI principal usa
    aprobar_cambio_generar_ticket() que combina aprobación + ejecución en un paso."""
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
        
        # Verificar que se puede ejecutar
        if not cambio.puede_ejecutar:
            return JsonResponse({
                'success': False,
                'error': 'Este cambio no puede ser ejecutado en su estado actual'
            })
        
        # VALIDAR STOCK ANTES DE EJECUTAR (usa stock_sucursal cuando está disponible)
        for detalle in cambio.detalles.filter(producto_nuevo__isnull=False):
            try:
                stock_disponible = detalle.producto_nuevo.stock_sucursal(sucursal_id)
            except Exception:
                stock_disponible = detalle.producto_nuevo.stock
            if stock_disponible < detalle.cantidad_nueva:
                return JsonResponse({
                    'success': False,
                    'error': f'No hay stock disponible para {detalle.producto_nuevo.sku} - {detalle.producto_nuevo.producto.articulo} Talla {detalle.producto_nuevo.talla}. Disponible: {stock_disponible}, Requerido: {detalle.cantidad_nueva}'
                })
        
        with transaction.atomic():
            # Procesar según el tipo de operación
            if cambio.tipo_operacion in ['CAMBIO_SIMPLE', 'CAMBIO_CON_DIFERENCIA']:
                # Es un cambio - crear nuevo ticket si hay productos nuevos
                productos_nuevos = cambio.detalles.filter(producto_nuevo__isnull=False)
                
                if productos_nuevos.exists():
                    # Crear nuevo ticket
                    correlativo_nuevo = obtener_siguiente_correlativo(cambio.sucursal, 'TICKET_CAMBIO')
                    
                    ticket_nuevo = Ticket.objects.create(
                        correlativo=correlativo_nuevo,
                        vendedor=cambio.ticket_original.vendedor,
                        sucursal=cambio.sucursal,
                        subTotal=int(cambio.monto_nuevo),
                        descuento=0,
                        total=int(cambio.monto_nuevo),
                        estado='PAGADO',
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
            # Filtrar solo detalles con producto_original (excluye productos adicionales)
            for detalle in cambio.detalles.filter(producto_original__isnull=False, cantidad_original__gt=0):
                # 1. DEVOLVER stock del producto original (INGRESO)
                if detalle.apto_para_venta:
                    Movimientos_Producto.objects.create(
                        ProductoTalla=detalle.producto_original.ProductoTalla,
                        cantidad=detalle.cantidad_original,
                        costo=detalle.producto_original.ProductoTalla.producto.costo,
                        precio=int(detalle.precio_original_unitario),
                        concepto='DEVOLUCION_CLIENTE',
                        tipo_movimiento='INGRESO',
                        responsable=request.user.username,
                        observaciones=f'Devolución por cambio {cambio.numero_operacion} - Ticket #{cambio.ticket_original.correlativo}',
                        referencia_externa=cambio.numero_operacion,
                        ticket=cambio.ticket_original
                    )
                    
                    # Actualizar stock del producto devuelto
                    detalle.producto_original.ProductoTalla.stock += detalle.cantidad_original
                    detalle.producto_original.ProductoTalla.save()
                else:
                    # Producto no apto: registrar sin devolver stock
                    Movimientos_Producto.objects.create(
                        ProductoTalla=detalle.producto_original.ProductoTalla,
                        cantidad=0,
                        costo=detalle.producto_original.ProductoTalla.producto.costo,
                        precio=int(detalle.precio_original_unitario),
                        concepto='DEVOLUCION_CLIENTE',
                        tipo_movimiento='AJUSTE',
                        responsable=request.user.username,
                        observaciones=f'Devolución NO APTA - {cambio.numero_operacion}',
                        referencia_externa=cambio.numero_operacion,
                        ticket=cambio.ticket_original
                    )
                
                # 2. DESCONTAR stock del producto nuevo (EGRESO con FIFO)
                if detalle.producto_nuevo:
                    # Verificar stock
                    if detalle.producto_nuevo.stock < detalle.cantidad_nueva:
                        raise ValidationError(f'Stock insuficiente para {detalle.producto_nuevo.producto.articulo}')
                    
                    # Consumir stock FIFO
                    consumir_stock_fifo(
                        producto_talla=detalle.producto_nuevo,
                        cantidad_requerida=detalle.cantidad_nueva,
                        responsable=request.user.username,
                        ticket=cambio.ticket_nuevo,
                        observaciones=f'Cambio {cambio.numero_operacion}',
                        referencia_externa=cambio.numero_operacion
                    )
            
            # Si hay diferencia positiva, crear ticket de diferencia
            ticket_diferencia = None
            if cambio.diferencia_monto > 0:
                correlativo_diferencia = obtener_siguiente_correlativo(cambio.sucursal, 'TICKET_CAMBIO')
                
                ticket_diferencia = Ticket.objects.create(
                    correlativo=correlativo_diferencia,
                    vendedor=cambio.ticket_original.vendedor,
                    sucursal=cambio.sucursal,
                    subTotal=int(cambio.diferencia_monto),
                    descuento=0,
                    total=int(cambio.diferencia_monto),
                    estado='PENDIENTE',
                    responsable=request.user.username,
                    cliente_nombre=cambio.ticket_original.cliente_nombre,
                    cliente_rut=cambio.ticket_original.cliente_rut,
                    cliente_email=cambio.ticket_original.cliente_email,
                    cliente_telefono=cambio.ticket_original.cliente_telefono,
                    observaciones=f'💰 DIFERENCIA DE PRECIO - Cambio {cambio.numero_operacion}\nTicket Original: #{cambio.ticket_original.correlativo}'
                )
                
                cambio.ticket_diferencia = ticket_diferencia
                cambio.estado = 'EJECUTADO_COBRO_PENDIENTE'
            elif cambio.diferencia_monto < 0:
                cambio.estado = 'EJECUTADO_DEVOL_PENDIENTE'
            else:
                cambio.estado = 'COMPLETADO'
            
            # Marcar fecha de ejecución
            cambio.fecha_ejecucion = timezone.now()
            cambio.save()
            
            # Crear historial
            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                accion='EJECUTADO',
                estado_anterior='APROBADO',
                estado_nuevo=cambio.estado,
                usuario=request.user,
                descripcion=f'Cambio ejecutado - Movimientos de inventario realizados',
                datos_adicionales={
                    'ticket_nuevo': cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else None,
                    'ticket_diferencia': ticket_diferencia.correlativo if ticket_diferencia else None,
                    'diferencia_monto': float(cambio.diferencia_monto),
                    'fecha_ejecucion': timezone.now().isoformat()
                }
            )
        
        # Preparar respuesta
        response_data = {
            'success': True,
            'message': 'Cambio ejecutado exitosamente',
            'cambio_id': cambio.id,
            'numero_operacion': cambio.numero_operacion,
            'ticket_nuevo': cambio.ticket_nuevo.correlativo if cambio.ticket_nuevo else None,
            'ticket_diferencia': ticket_diferencia.correlativo if ticket_diferencia else None,
            'diferencia_monto': float(cambio.diferencia_monto),
            'estado_final': cambio.get_estado_display(),
            'requiere_cobro': cambio.diferencia_monto > 0,
            'cobro_pendiente': cambio.estado == 'EJECUTADO_COBRO_PENDIENTE'
        }
        
        # Datos del ticket nuevo para impresión
        if cambio.ticket_nuevo:
            response_data['ticket_data'] = construir_ticket_data(cambio.ticket_nuevo)
        
        return JsonResponse(response_data)
        
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
            'error': f'Error al ejecutar cambio: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def registrar_pago_diferencia(request):
    """Registrar el pago de la diferencia de precio de un cambio ejecutado"""
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        metodo_pago = data.get('metodo_pago')
        monto = data.get('monto')
        referencia_pago = data.get('referencia_pago', '')
        numero_autorizacion = data.get('numero_autorizacion', '')
        observaciones = data.get('observaciones', '')
        
        if not all([cambio_id, metodo_pago, monto]):
            return JsonResponse({
                'success': False,
                'error': 'Faltan datos requeridos'
            })
        
        cambio = get_object_or_404(CambioDevolucion, id=cambio_id)
        
        # Verificar acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if cambio.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a este cambio'
            })
        
        # Verificar que tenga cobro pendiente
        if not cambio.cobro_pendiente:
            return JsonResponse({
                'success': False,
                'error': 'Este cambio no tiene un cobro de diferencia pendiente'
            })
        
        # Verificar monto
        if float(monto) != float(cambio.diferencia_monto):
            return JsonResponse({
                'success': False,
                'error': f'El monto debe ser ${cambio.diferencia_monto:,}'
            })
        
        with transaction.atomic():
            # Crear registro de pago
            PagoCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                tipo_pago='PAGO_DIFERENCIA',
                metodo_pago=metodo_pago,
                monto=monto,
                referencia_pago=referencia_pago,
                numero_autorizacion=numero_autorizacion,
                procesado_por=request.user,
                observaciones=observaciones
            )
            
            # Actualizar ticket de diferencia a PAGADO
            if cambio.ticket_diferencia:
                cambio.ticket_diferencia.estado = 'PAGADO'
                cambio.ticket_diferencia.fecha_pago = timezone.now()
                cambio.ticket_diferencia.save()
                
                # Crear pago en el ticket
                TicketDetallePago.objects.create(
                    ticket=cambio.ticket_diferencia,
                    metodo_pago=metodo_pago,
                    monto=int(monto),
                    voucher=referencia_pago,
                    notas=observaciones
                )
            
            # Cambiar estado del cambio a COMPLETADO
            cambio.estado = 'COMPLETADO'
            cambio.fecha_pago_diferencia = timezone.now()
            cambio.fecha_completado = timezone.now()
            cambio.save()
            
            # Crear historial
            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                accion='COBRO_DIFERENCIA',
                estado_anterior='EJECUTADO_COBRO_PENDIENTE',
                estado_nuevo='COMPLETADO',
                usuario=request.user,
                descripcion=f'Diferencia de ${monto:,} cobrada por {request.user.username}',
                datos_adicionales={
                    'metodo_pago': metodo_pago,
                    'monto': float(monto),
                    'referencia_pago': referencia_pago,
                    'numero_autorizacion': numero_autorizacion,
                    'fecha_pago': timezone.now().isoformat()
                }
            )
        
        return JsonResponse({
            'success': True,
            'message': f'Pago de ${monto:,} registrado exitosamente',
            'cambio_id': cambio.id,
            'ticket_diferencia': cambio.ticket_diferencia.correlativo if cambio.ticket_diferencia else None,
            'estado_final': cambio.get_estado_display(),
            'estado_final_codigo': cambio.estado
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al registrar pago: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def aprobar_cambio_generar_ticket(request):
    """Aprobar cambio/devolución y generar ticket de venta automáticamente"""
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        vendedor_id = data.get('vendedor_id')
        observaciones = data.get('observaciones', '')
        
        if not all([cambio_id, vendedor_id]):
            return JsonResponse({
                'success': False,
                'error': 'ID de cambio y vendedor requeridos'
            })
        
        # Obtener cambio
        cambio = get_object_or_404(CambioDevolucion, id=cambio_id)
        
        # Verificar acceso
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        if cambio.sucursal_id != int(sucursal_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene acceso a este cambio'
            })
        
        # Verificar estado
        if cambio.estado not in ('SOLICITADO', 'APROBADO'):
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden aprobar/ejecutar cambios en estado Solicitado o Aprobado'
            })
        
        # Validar vendedor (vendedor_id es el ID del modelo Vendedor, no User)
        try:
            vendedor_obj = Vendedor.objects.get(id=vendedor_id)
        except Vendedor.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Vendedor no encontrado'
            })
        
        # Validar stock de productos nuevos
        # IMPORTANTE: Considerar que los productos devueltos se suman al stock primero
        stock_ajustes = {}  # Dict para rastrear ajustes de stock por producto_talla_id
        
        # 1. Primero, calcular el stock que se va a recuperar de las devoluciones
        # Filtrar solo detalles con producto_original (excluye productos adicionales)
        for item in cambio.detalles.filter(producto_original__isnull=False, cantidad_original__gt=0):
            producto_talla_devuelto = item.producto_original.ProductoTalla
            if producto_talla_devuelto.id not in stock_ajustes:
                stock_ajustes[producto_talla_devuelto.id] = 0
            stock_ajustes[producto_talla_devuelto.id] += item.cantidad_original
        
        # 2. Ahora validar stock de productos nuevos considerando las devoluciones
        for item in cambio.detalles.all():
            if item.producto_nuevo and item.cantidad_nueva:
                producto_talla = item.producto_nuevo
                stock_actual = producto_talla.stock_sucursal(sucursal_id)
                
                # Sumar el stock que se va a recuperar si este producto también se está devolviendo
                stock_recuperado = stock_ajustes.get(producto_talla.id, 0)
                stock_disponible = stock_actual + stock_recuperado
                
                if stock_disponible < item.cantidad_nueva:
                    return JsonResponse({
                        'success': False,
                        'error': f'Stock insuficiente para {producto_talla.producto.articulo} - Talla {producto_talla.talla}. Disponible: {stock_disponible}'
                    })
        
        print(f"🚀 Iniciando transacción atómica para aprobar cambio #{cambio.id}")
        
        with transaction.atomic():
            # Aprobar el cambio (solo si no está ya aprobado)
            if cambio.estado == 'SOLICITADO':
                print(f"1️⃣ Aprobando cambio...")
                cambio.aprobar_cambio(request.user, observaciones)
                print(f"   ✅ Cambio aprobado, estado: {cambio.estado}")
            else:
                print(f"1️⃣ Cambio ya en estado {cambio.estado}, continuando ejecución...")
            
            # GENERAR TICKET DE VENTA
            # Obtener correlativo usando la función centralizada
            sucursal = get_object_or_404(Sucursal, id=sucursal_id)
            
            print(f"2️⃣ Obteniendo correlativo para sucursal {sucursal.alias}...")
            try:
                nuevo_correlativo = obtener_siguiente_correlativo(sucursal, 'TICKET')
                print(f"   ✅ Correlativo obtenido: #{nuevo_correlativo}")
            except Exception as e:
                print(f"   ❌ Error al obtener correlativo: {str(e)}")
                raise  # Re-lanzar para hacer rollback
            
            # Calcular totales
            total_devuelto = cambio.monto_original
            total_nuevo = cambio.monto_nuevo
            diferencia = total_nuevo - total_devuelto
            
            print(f"3️⃣ Creando ticket #{nuevo_correlativo}...")
            print(f"   Sucursal: {sucursal.alias}")
            print(f"   Vendedor: {vendedor_obj.nombre}")
            print(f"   Total: ${abs(diferencia)}")
            print(f"   Diferencia: ${diferencia} ({'a cobrar' if diferencia > 0 else 'a devolver'})")
            
            try:
                # Determinar estado según la diferencia
                if diferencia > 0:
                    # Cliente debe pagar
                    estado_ticket = 'PENDIENTE'
                    metodo_pago_ticket = 'PENDIENTE_COBRO'
                    tipo_documento = 'TICKET_COBRO_CAMBIO'
                elif diferencia < 0:
                    # Se devuelve dinero al cliente
                    estado_ticket = 'PENDIENTE'
                    metodo_pago_ticket = 'PENDIENTE_DEVOLUCION'
                    tipo_documento = 'TICKET_DEVOLUCION'
                else:
                    # Sin diferencia - cambio directo
                    estado_ticket = 'PAGADO'
                    metodo_pago_ticket = 'SIN_DIFERENCIA'
                    tipo_documento = 'TICKET_CAMBIO_DIRECTO'
                
                ticket = Ticket.objects.create(
                    correlativo=nuevo_correlativo,
                    sucursal=sucursal,
                    vendedor=vendedor_obj,
                    responsable=request.user.get_full_name() or request.user.username,
                    cliente_nombre=cambio.ticket_original.cliente_nombre if cambio.ticket_original else 'Cliente General',
                    cliente_rut=cambio.ticket_original.cliente_rut if cambio.ticket_original else '',
                    subTotal=int(abs(diferencia)),
                    total=int(abs(diferencia)),
                    descuento=0,
                    estado=estado_ticket,
                    metodo_pago=metodo_pago_ticket,
                    modulo_origen='CAMBIO_DEVOLUCION',
                    tipo_dte=tipo_documento,  # Usar tipo_dte para identificar
                    observaciones=f'🔄 CAMBIO/DEVOLUCIÓN #{cambio.numero_operacion}\n' +
                                 f'📋 Ticket Original: #{cambio.ticket_original.correlativo}\n\n' +
                                 f'📦 Productos devueltos: ${int(total_devuelto):,}\n' +
                                 f'✨ Productos nuevos: ${int(total_nuevo):,}\n' +
                                 f'💰 Diferencia: ${int(diferencia):,}\n\n' +
                                 (f'💵 A DEVOLVER AL CLIENTE: ${abs(int(diferencia)):,}\n\n' if diferencia < 0 else 
                                  f'💰 A COBRAR AL CLIENTE: ${int(diferencia):,}\n\n' if diferencia > 0 else 
                                  f'✅ SIN DIFERENCIA - CAMBIO DIRECTO\n\n') +
                                 (observaciones if observaciones else '')
                )
                print(f"   ✅ Ticket creado con ID: {ticket.id}, Correlativo: {ticket.correlativo}")
            except Exception as e:
                print(f"   ❌ Error al crear ticket: {str(e)}")
                import traceback
                traceback.print_exc()
                raise  # Re-lanzar para hacer rollback
            
            # Agregar productos al ticket usando el modelo Ticket_Productos
            print(f"4️⃣ Agregando productos al ticket...")
            print(f"   Detalles del cambio: {cambio.detalles.count()}")
            
            try:
                # ✅ CORREGIDO: Agrupar productos para evitar duplicados
                # Estructura: { producto_talla_id: { 'producto': obj, 'cantidad': n, 'precio': p, 'subtotal': s } }
                productos_devueltos_agrupados = {}
                productos_nuevos_agrupados = {}
                
                # AGRUPAR PRODUCTOS DEVUELTOS (con precio negativo)
                # Filtrar solo detalles con producto_original (excluye productos adicionales)
                for item in cambio.detalles.filter(producto_original__isnull=False, cantidad_original__gt=0):
                    producto_talla = item.producto_original.ProductoTalla
                    pt_id = producto_talla.id
                    precio = abs(int(item.precio_original_unitario or 0))
                    
                    if pt_id in productos_devueltos_agrupados:
                        # Sumar cantidad al existente
                        productos_devueltos_agrupados[pt_id]['cantidad'] += item.cantidad_original
                        productos_devueltos_agrupados[pt_id]['subtotal'] += precio * item.cantidad_original
                    else:
                        productos_devueltos_agrupados[pt_id] = {
                            'producto': producto_talla,
                            'cantidad': item.cantidad_original,
                            'precio': precio,
                            'subtotal': precio * item.cantidad_original
                        }
                
                # AGRUPAR PRODUCTOS NUEVOS (con precio positivo)
                for item in cambio.detalles.all():
                    if item.producto_nuevo and item.cantidad_nueva and item.cantidad_nueva > 0:
                        producto_talla = item.producto_nuevo
                        pt_id = producto_talla.id
                        precio = int(item.precio_nuevo or producto_talla.producto.precioventa)
                        
                        if pt_id in productos_nuevos_agrupados:
                            # Sumar cantidad al existente
                            productos_nuevos_agrupados[pt_id]['cantidad'] += item.cantidad_nueva
                            productos_nuevos_agrupados[pt_id]['subtotal'] += precio * item.cantidad_nueva
                        else:
                            productos_nuevos_agrupados[pt_id] = {
                                'producto': producto_talla,
                                'cantidad': item.cantidad_nueva,
                                'precio': precio,
                                'subtotal': precio * item.cantidad_nueva
                            }
                
                # CREAR REGISTROS DE PRODUCTOS DEVUELTOS
                for pt_id, data in productos_devueltos_agrupados.items():
                    print(f"   → Devuelto: {data['producto'].producto.articulo} x{data['cantidad']}")
                    Ticket_Productos.objects.create(
                        idTicket=ticket,
                        ProductoTalla=data['producto'],
                        stock=data['cantidad'],
                        precio=-data['precio'],  # Negativo
                        precio_original=-data['precio'],
                        descuento_unitario=0,
                        subtotal=-data['subtotal']
                    )
                
                # CREAR REGISTROS DE PRODUCTOS NUEVOS
                for pt_id, data in productos_nuevos_agrupados.items():
                    print(f"   → Nuevo: {data['producto'].producto.articulo} x{data['cantidad']}")
                    Ticket_Productos.objects.create(
                        idTicket=ticket,
                        ProductoTalla=data['producto'],
                        stock=data['cantidad'],
                        precio=data['precio'],
                        precio_original=data['precio'],
                        descuento_unitario=0,
                        subtotal=data['subtotal']
                    )
                
                total_productos = len(productos_devueltos_agrupados) + len(productos_nuevos_agrupados)
                print(f"   ✅ {total_productos} productos agregados al ticket (agrupados de {cambio.detalles.count()} detalles)")
            except Exception as e:
                print(f"   ❌ Error al agregar productos: {str(e)}")
                import traceback
                traceback.print_exc()
                raise
            
            # EJECUTAR MOVIMIENTOS DE INVENTARIO AUTOMÁTICAMENTE
            print(f"5️⃣ Ejecutando movimientos de inventario...")
            
            try:
                # 1. ENTRADA: Productos devueltos vuelven al inventario (SOLO SI ESTÁN APTOS)
                # Filtrar solo detalles con producto_original (excluye productos adicionales)
                for item in cambio.detalles.filter(producto_original__isnull=False, cantidad_original__gt=0):
                    producto_talla_devuelto = item.producto_original.ProductoTalla
                    
                    # ✅ Solo sumar stock si el producto está apto para venta
                    if item.apto_para_venta:
                        producto_talla_devuelto.stock += item.cantidad_original
                        producto_talla_devuelto.save()
                        
                        # Registrar movimiento de entrada
                        Movimientos_Producto.objects.create(
                            ProductoTalla=producto_talla_devuelto,
                            tipo_movimiento='INGRESO',
                            concepto='DEVOLUCION',
                            cantidad=item.cantidad_original,
                            responsable=request.user.username,
                            sucursal_destino=sucursal,
                            precio=int(item.precio_original_unitario),
                            costo=0,
                            estado='COMPLETADO',
                            observaciones=f'Devolución - Cambio #{cambio.numero_operacion}. Condición: {item.get_condicion_producto_display()}. APTO PARA VENTA.'
                        )
                        print(f"   ✅ INGRESO: {producto_talla_devuelto.sku} +{item.cantidad_original} (APTO)")
                    else:
                        # Producto NO APTO - Solo registrar sin sumar stock
                        Movimientos_Producto.objects.create(
                            ProductoTalla=producto_talla_devuelto,
                            tipo_movimiento='AJUSTE',
                            concepto='DEVOLUCION_NO_APTA',
                            cantidad=0,  # No suma stock
                            responsable=request.user.username,
                            sucursal_destino=sucursal,
                            precio=int(item.precio_original_unitario),
                            costo=0,
                            estado='COMPLETADO',
                            observaciones=f'Devolución NO APTA - Cambio #{cambio.numero_operacion}. Condición: {item.get_condicion_producto_display()}. NO SE SUMA AL INVENTARIO.'
                        )
                        print(f"   ⚠️ NO APTO: {producto_talla_devuelto.sku} (sin movimiento de stock)")
                
                # 2. SALIDA: Productos nuevos entregados al cliente (FIFO)
                for item in cambio.detalles.all():
                    if item.producto_nuevo and item.cantidad_nueva:
                        try:
                            consumir_stock_fifo(
                                producto_talla=item.producto_nuevo,
                                cantidad_requerida=item.cantidad_nueva,
                                responsable=request.user.username,
                                ticket=ticket,
                                observaciones=f'Entrega - Cambio #{cambio.numero_operacion}',
                                referencia_externa=cambio.numero_operacion
                            )
                        except Exception as e_fifo:
                            print(f"   ⚠️ FIFO falló para {item.producto_nuevo.sku}, usando decremento directo: {e_fifo}")
                            item.producto_nuevo.stock -= item.cantidad_nueva
                            item.producto_nuevo.save()
                            Movimientos_Producto.objects.create(
                                ProductoTalla=item.producto_nuevo,
                                tipo_movimiento='EGRESO',
                                concepto='VENTA',
                                cantidad=item.cantidad_nueva,
                                responsable=request.user.username,
                                sucursal_origen=sucursal,
                                ticket=ticket,
                                precio=int(item.precio_nuevo),
                                costo=0,
                                estado='COMPLETADO',
                                observaciones=f'Entrega - Cambio #{cambio.numero_operacion}. Fallback sin FIFO.'
                            )
                
                print(f"   ✅ Movimientos de inventario ejecutados")
            except Exception as e:
                print(f"   ❌ Error en movimientos de inventario: {str(e)}")
                import traceback
                traceback.print_exc()
                raise
            
            # Vincular ticket nuevo al cambio
            print(f"6️⃣ Vinculando ticket al cambio...")
            cambio.ticket_nuevo = ticket
            estado_anterior_cambio = cambio.estado

            # Determinar estado final según diferencia y estado del ticket
            diferencia = float(cambio.diferencia_monto)
            if ticket.estado == 'PENDIENTE' and diferencia > 0:
                cambio.estado = 'EJECUTADO_COBRO_PENDIENTE'
                cambio.fecha_ejecucion = timezone.now()
            elif ticket.estado == 'PENDIENTE' and diferencia < 0:
                cambio.estado = 'EJECUTADO_DEVOL_PENDIENTE'
                cambio.fecha_ejecucion = timezone.now()
            else:
                cambio.estado = 'COMPLETADO'
                cambio.fecha_ejecucion = timezone.now()
                cambio.fecha_completado = timezone.now()

            cambio.save()
            print(f"   ✅ Cambio actualizado: Estado={cambio.estado}, Ticket nuevo ID={ticket.id}")
            
            # Crear historial
            print(f"7️⃣ Creando historial...")
            accion_historial = 'APROBADO_Y_EJECUTADO'
            if cambio.estado == 'EJECUTADO_COBRO_PENDIENTE':
                accion_historial = 'EJECUTADO_COBRO_PENDIENTE'
            elif cambio.estado == 'EJECUTADO_DEVOL_PENDIENTE':
                accion_historial = 'EJECUTADO_DEVOL_PENDIENTE'

            HistorialCambioDevolucion.objects.create(
                cambio_devolucion=cambio,
                accion=accion_historial,
                estado_anterior=estado_anterior_cambio,
                estado_nuevo=cambio.estado,
                usuario=request.user,
                descripcion=f'Cambio aprobado y ejecutado por {request.user.get_full_name() or request.user.username}. Ticket #{nuevo_correlativo} generado. Movimientos de inventario realizados.',
                datos_adicionales={
                    'observaciones': observaciones,
                    'vendedor_id': vendedor_id,
                    'vendedor_nombre': vendedor_obj.nombre,
                    'vendedor_codigo': vendedor_obj.codigo_vendedor,
                    'ticket_generado': nuevo_correlativo,
                    'fecha_aprobacion': timezone.now().isoformat()
                }
            )
            print(f"   ✅ Historial creado")
            
            print(f"")
            print(f"{'='*60}")
            print(f"✅ OPERACIÓN EJECUTADA EXITOSAMENTE")
            print(f"{'='*60}")
            print(f"   Ticket generado: #{nuevo_correlativo} (ID: {ticket.id})")
            print(f"   Cambio: #{cambio.numero_operacion} (Estado: {cambio.estado})")
            print(f"   Inventario: Actualizado")
            if cambio.estado != 'COMPLETADO':
                print(f"   Pendiente: {'Cobro' if diferencia > 0 else 'Devolución'} de ${abs(int(diferencia)):,}")
            print(f"{'='*60}")
        
        # Construir datos del ticket para impresión
        ticket_data = construir_ticket_data(ticket)

        # ── Contexto adicional del cambio para el ticket visual ──────────────
        ticket_data['es_ticket_cambio'] = True
        ticket_data['numero_operacion']          = cambio.numero_operacion
        ticket_data['ticket_original_correlativo'] = (
            cambio.ticket_original.correlativo if cambio.ticket_original else None
        )
        ticket_data['tipo_operacion']  = cambio.tipo_operacion
        ticket_data['tipo_operacion_display'] = cambio.get_tipo_operacion_display()
        ticket_data['monto_original']  = int(cambio.monto_original)
        ticket_data['monto_nuevo']     = int(cambio.monto_nuevo)
        ticket_data['diferencia_monto'] = int(cambio.diferencia_monto)

        return JsonResponse({
            'success': True,
            'message': 'Cambio aprobado, ticket generado e inventario actualizado',
            'ticket_id': ticket.id,
            'ticket_correlativo': nuevo_correlativo,
            'diferencia_cobrar': cambio.diferencia_monto,
            'nuevo_estado': cambio.estado,
            'nuevo_estado_display': cambio.get_estado_display(),
            'cobro_pendiente': cambio.cobro_pendiente,
            'devolucion_pendiente': cambio.devolucion_pendiente,
            'ticket_data': ticket_data
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        import traceback
        print(f"Error al aprobar cambio y generar ticket: {e}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def validar_codigo_vendedor(request):
    """Validar código de vendedor para cambios/devoluciones"""
    try:
        data = json.loads(request.body)
        codigo = data.get('codigo', '').strip()
        
        if not codigo:
            return JsonResponse({
                'success': False,
                'error': 'Código requerido'
            })
        
        # Buscar vendedor por código en el modelo Vendedor
        vendedor_obj = Vendedor.objects.filter(codigo_vendedor=codigo).first()
        
        if not vendedor_obj:
            return JsonResponse({
                'success': False,
                'error': 'Código de vendedor no encontrado'
            })
        
        # El vendedor existe, retornar sus datos
        return JsonResponse({
            'success': True,
            'vendedor': {
                'id': vendedor_obj.id,
                'nombre_completo': vendedor_obj.nombre or f'Vendedor {vendedor_obj.codigo_vendedor}',
                'codigo': vendedor_obj.codigo_vendedor
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        print(f"Error al validar vendedor: {e}")
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


# ========== CÓDIGOS DE AUTORIZACIÓN DINÁMICOS ==========

@login_required
def obtener_codigo_autorizacion_actual(request):
    """
    Obtiene el código de autorización dinámico actual.
    Solo accesible para usuarios con rol 'administrador' o 'jefe_local'
    """
    try:
        from .models import CodigoAutorizacionDinamico
        
        # Verificar que el usuario tenga el rol apropiado
        rol_usuario = getattr(request.user, 'rol', None)
        
        if rol_usuario not in ['administrador', 'jefe_local']:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para acceder a los códigos de autorización',
                'requiere_rol': 'Administrador o Jefe Local'
            }, status=403)
        
        # Obtener o generar el código actual para este supervisor
        codigo_obj = CodigoAutorizacionDinamico.obtener_codigo_actual(request.user)
        
        if not codigo_obj:
            return JsonResponse({
                'success': False,
                'error': 'No se pudo generar el código de autorización'
            }, status=500)
        
        # Calcular tiempo restante usando hora de Chile
        import pytz
        ahora_utc = timezone.now()
        chile_tz = pytz.timezone('America/Santiago')
        ahora = ahora_utc.astimezone(chile_tz)
        tiempo_restante = codigo_obj.fecha_hora_fin - ahora
        minutos_restantes = int(tiempo_restante.total_seconds() / 60)
        
        return JsonResponse({
            'success': True,
            'codigo': {
                'codigo': codigo_obj.codigo,
                'valido_desde': codigo_obj.fecha_hora_inicio.strftime('%H:%M'),
                'valido_hasta': codigo_obj.fecha_hora_fin.strftime('%H:%M'),
                'minutos_restantes': minutos_restantes,
                'fecha_actual': ahora.strftime('%d/%m/%Y %H:%M:%S')
            }
        })
        
    except Exception as e:
        print(f"Error al obtener código de autorización: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener código: {str(e)}'
        }, status=500)


@login_required
@require_POST
@csrf_exempt
def validar_codigo_autorizacion(request):
    """
    Valida un código de autorización dinámico ingresado por el usuario.
    Incluye protección anti brute-force: máximo 5 intentos fallidos en 15 minutos.
    """
    try:
        from .models import CodigoAutorizacionDinamico, RegistroAutorizacion
        
        # Protección anti brute-force: max 5 intentos fallidos en 15 minutos
        hace_15_min = timezone.now() - timezone.timedelta(minutes=15)
        intentos_fallidos = RegistroAutorizacion.objects.filter(
            usuario_solicitante=request.user,
            exitoso=False,
            fecha_hora__gte=hace_15_min
        ).count()
        
        if intentos_fallidos >= 5:
            return JsonResponse({
                'success': False,
                'error': 'Demasiados intentos fallidos. Intente nuevamente en 15 minutos.'
            }, status=429)
        
        data = json.loads(request.body)
        codigo_ingresado = data.get('codigo', '').strip()
        tipo_operacion = data.get('tipo_operacion', 'APROBACION_CAMBIO')
        cambio_id = data.get('cambio_id', None)
        
        if not codigo_ingresado:
            return JsonResponse({
                'success': False,
                'error': 'Debe ingresar un código de autorización'
            })
        
        # Validar el código
        es_valido, mensaje, codigo_obj = CodigoAutorizacionDinamico.validar_codigo(codigo_ingresado)
        
        # Registrar el intento de autorización con trazabilidad del supervisor
        try:
            cambio_obj = None
            if cambio_id:
                cambio_obj = CambioDevolucion.objects.get(id=cambio_id)
            
            supervisor = codigo_obj.generado_por if (es_valido and codigo_obj) else None
            
            registro = RegistroAutorizacion.objects.create(
                codigo_usado=codigo_obj if es_valido else None,
                usuario_solicitante=request.user,
                usuario_autorizador=supervisor,
                tipo_operacion=tipo_operacion,
                descripcion=f"{'Autorización exitosa' if es_valido else 'Intento fallido'}: {mensaje}",
                ip_origen=request.META.get('REMOTE_ADDR'),
                exitoso=es_valido,
                cambio_devolucion=cambio_obj,
                datos_adicionales={
                    'codigo_ingresado': codigo_ingresado,
                    'mensaje': mensaje,
                    'supervisor_id': supervisor.id if supervisor else None,
                    'supervisor_nombre': supervisor.get_full_name() if supervisor else None
                }
            )
        except Exception as e:
            print(f"Error al registrar autorización: {e}")
        
        if not es_valido:
            return JsonResponse({
                'success': False,
                'error': mensaje
            })
        
        # Marcar el código como usado (un solo uso por código)
        codigo_obj.marcar_como_usado()
        
        return JsonResponse({
            'success': True,
            'mensaje': 'Código de autorización validado correctamente',
            'codigo': {
                'codigo': codigo_obj.codigo,
                'valido_hasta': codigo_obj.fecha_hora_fin.strftime('%H:%M'),
                'supervisor': codigo_obj.generado_por.get_full_name() if codigo_obj.generado_por else None
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        print(f"Error al validar código de autorización: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Error al validar código: {str(e)}'
        })


@login_required
@require_POST
@csrf_exempt
def completar_cambio_devolucion(request):
    """Completar un cambio/devolución aprobado.
    NOTA: Camino alternativo. La UI principal usa aprobar_cambio_generar_ticket()."""
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
                    correlativo_nuevo = obtener_siguiente_correlativo(cambio.sucursal, 'TICKET_CAMBIO')
                    
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
            # Filtrar solo detalles con producto_original (excluye productos adicionales)
            for detalle in cambio.detalles.filter(producto_original__isnull=False, cantidad_original__gt=0):
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
            ticket_data['es_ticket_cambio'] = True
            ticket_data['numero_operacion']            = cambio.numero_operacion
            ticket_data['ticket_original_correlativo'] = (
                cambio.ticket_original.correlativo if cambio.ticket_original else None
            )
            ticket_data['tipo_operacion']         = cambio.tipo_operacion
            ticket_data['tipo_operacion_display']  = cambio.get_tipo_operacion_display()
            ticket_data['monto_original']  = int(cambio.monto_original)
            ticket_data['monto_nuevo']     = int(cambio.monto_nuevo)
            ticket_data['diferencia_monto'] = int(cambio.diferencia_monto)
        
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
        tipo_dte = request.GET.get('tipo_dte', '').strip()  # 33, 39, 34, etc.
        
        
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
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
            # Buscar DTE - check count for informative errors
            query_debug = Dte.objects.filter(numero_documento=numero)
            
            if not query_debug.exists():
                return JsonResponse({
                    'success': False,
                    'error': f'DTE #{numero} no encontrado en el sistema.'
                })
            
            # Buscar DTE con filtros para ventas
            query = Dte.objects.select_related('vendedor', 'receptor', 'sucursal').prefetch_related(
                'dte_productos__productoTalla__producto'
            ).filter(
                numero_documento=numero,
                sucursal_id=sucursal_id
            )
            
            # Filtrar por tipo de transacción (solo ventas)
            query = query.filter(tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'])
            
            # Filtrar por tipo de DTE específico si se proporcionó
            if tipo_dte:
                query = query.filter(tipo_documento=tipo_dte)
            
            if fecha_compra:
                query = query.filter(fecha_emision=fecha_compra)
            
            dte = query.first()
            
            if not dte:
                # Verificar primero si existe el DTE con el número
                dte_existe = Dte.objects.filter(numero_documento=numero).first()
                
                if not dte_existe:
                    return JsonResponse({
                        'success': False,
                        'error': f'DTE #{numero} no encontrado en el sistema.'
                    })
                
                # Verificar si existe en otra sucursal
                if dte_existe.sucursal_id != sucursal_id:
                    return JsonResponse({
                        'success': False,
                        'error': f'DTE #{numero} pertenece a otra sucursal ({dte_existe.sucursal.alias}). Solo puede procesar documentos de la sucursal actual.'
                    })
                
                # El DTE existe, verificar por qué no pasó los filtros
                # Verificar tipo de transacción
                if dte_existe.tipo_transaccion not in ['VENTA', 'VENTA_PUBLICO']:
                    return JsonResponse({
                        'success': False,
                        'error': f'DTE #{numero} es tipo "{dte_existe.tipo_transaccion}". Solo se permiten cambios de documentos de VENTA o VENTA_PUBLICO.'
                    })
                
                # Si llegó aquí, el tipo es correcto pero otros filtros no coinciden
                # Verificar si hay múltiples DTEs con ese número
                query_disponibles = Dte.objects.filter(
                    numero_documento=numero,
                    sucursal_id=sucursal_id,
                    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
                )
                
                if fecha_compra and tipo_dte:
                    # Usuario especificó fecha y tipo, pero no se encontró
                    return JsonResponse({
                        'success': False,
                        'error': f'No se encontró DTE #{numero} tipo {tipo_dte} con fecha {fecha_compra}. Verifique los datos.'
                    })
                
                count_dtes = query_disponibles.count()
                
                if count_dtes > 1:
                    # Hay múltiples DTEs, crear tabla informativa
                    dtes_info = []
                    tipo_dte_nombres = {
                        '33': 'Factura Electrónica',
                        '34': 'Factura Exenta',
                        '39': 'Boleta Electrónica',
                        '41': 'Boleta Exenta',
                        '61': 'Nota de Crédito',
                        '56': 'Nota de Débito'
                    }
                    
                    for d in query_disponibles.all():
                        tipo_nombre = tipo_dte_nombres.get(d.tipo_documento, f'Tipo {d.tipo_documento}')
                        dtes_info.append({
                            'tipo': d.tipo_documento,
                            'tipo_nombre': tipo_nombre,
                            'fecha': d.fecha_emision.strftime('%d/%m/%Y'),
                            'monto': f'${int(d.monto_con_iva):,}'
                        })
                    
                    # Crear mensaje con tabla HTML
                    tabla_html = '<div class="table-responsive"><table class="table table-sm table-bordered">'
                    tabla_html += '<thead><tr><th>Tipo DTE</th><th>Código</th><th>Fecha</th><th>Monto</th></tr></thead><tbody>'
                    
                    for info in dtes_info:
                        tabla_html += f'<tr><td><strong>{info["tipo_nombre"]}</strong></td><td>{info["tipo"]}</td><td>{info["fecha"]}</td><td>{info["monto"]}</td></tr>'
                    
                    tabla_html += '</tbody></table></div>'
                    
                    return JsonResponse({
                        'success': False,
                        'error': f'Se encontraron {count_dtes} documentos con el número #{numero}',
                        'multiple_documents': True,
                        'documents_html': tabla_html,
                        'message': 'Por favor, seleccione el tipo de DTE específico (Boleta o Factura) en el formulario de búsqueda.'
                    })
                else:
                    # Un solo DTE pero no coincide con la fecha
                    return JsonResponse({
                        'success': False,
                        'error': f'DTE #{numero} encontrado con fecha {dte_existe.fecha_emision.strftime("%d/%m/%Y")}, pero usted buscó con fecha {fecha_compra}. Corrija la fecha de compra.'
                    })
            
            # Verificar que esté emitido/pagado
            if dte.estado_dte not in ['EMITIDO', 'ACEPTADO']:
                return JsonResponse({
                    'success': False,
                    'error': 'Solo se pueden procesar cambios de documentos emitidos'
                })
            
            # Crear o buscar ticket de referencia para el DTE
            from datetime import timedelta
            fecha_limite = dte.fecha_emision + timedelta(days=30)
            dentro_del_plazo = timezone.localdate() <= fecha_limite
            
            # Intentar encontrar el ticket ORIGINAL del POS (fuente con descuentos correctos)
            ticket_original_pos = None
            if dte.referencias and 'TICKET-' in dte.referencias:
                try:
                    corr_original = dte.referencias.split('TICKET-')[1].strip().split()[0]
                    ticket_original_pos = Ticket.objects.filter(
                        correlativo=corr_original,
                        sucursal_id=sucursal_id,
                        estado='PAGADO'
                    ).first()
                except Exception:
                    pass

            # Si encontramos el ticket original del POS, usarlo directamente
            if ticket_original_pos:
                ticket_referencia = ticket_original_pos
            else:
                # Buscar si ya existe un ticket de referencia asociado a este DTE
                ticket_referencia = Ticket.objects.filter(
                    observaciones__icontains=f'DTE #{dte.numero_documento}'
                ).first()
                
                if ticket_referencia:
                    # Repair: sync discounts from DTE or original ticket
                    for dp in dte.dte_productos.all():
                        if not dp.productoTalla:
                            continue
                        dcto_unit = 0
                        if dp.descuento_monto and dp.stock and dp.stock > 0:
                            dcto_unit = int(dp.descuento_monto / dp.stock)
                        if dcto_unit > 0:
                            tp_ref = ticket_referencia.ticket_productos.filter(
                                ProductoTalla=dp.productoTalla,
                                stock=dp.stock,
                                descuento_unitario=0
                            ).first()
                            if tp_ref:
                                tp_ref.descuento_unitario = dcto_unit
                                tp_ref.subtotal = (dp.precio - dcto_unit) * dp.stock
                                tp_ref.porcentaje_descuento = dp.descuento_pct or 0
                                tp_ref.save()
            
            if not ticket_referencia:
                # Crear ticket de referencia
                from .views import obtener_siguiente_correlativo
                correlativo_ticket = obtener_siguiente_correlativo(dte.sucursal, 'TICKET')
                
                ticket_referencia = Ticket.objects.create(
                    correlativo=correlativo_ticket,
                    vendedor=dte.vendedor,
                    sucursal=dte.sucursal,
                    subTotal=int(dte.monto_neto),
                    descuento=int(dte.descuento) if dte.descuento else 0,
                    total=int(dte.monto_con_iva),
                    estado='PAGADO',
                    responsable=dte.responsable,
                    cliente_nombre=dte.receptor.razon_social if dte.receptor else '',
                    cliente_rut=dte.receptor.rut if dte.receptor else '',
                    cliente_email=dte.receptor.correoVendedor if dte.receptor else '',
                    cliente_telefono='',
                    cliente_giro=dte.receptor.giro if dte.receptor else '',
                    cliente_direccion=dte.receptor.direccion if dte.receptor else '',
                    cliente_comuna=dte.receptor.comuna if dte.receptor else '',
                    cliente_ciudad=dte.receptor.ciudad if dte.receptor else '',
                    observaciones=f'Ticket de referencia para DTE #{dte.numero_documento} - {dte.tipo_documento}'
                )
                
                # Copiar productos del DTE al ticket (con descuentos)
                es_boleta = dte.tipo_documento in ['39', '41', 'BOLETA ELECTRONICA', 'BOLETA EXENTA']
                for dp in dte.dte_productos.all():
                    precio_lista = dp.precio
                    dcto_unitario = 0

                    if dp.descuento_monto and dp.stock and dp.stock > 0:
                        dcto_unitario = int(dp.descuento_monto / dp.stock)
                    elif es_boleta and dp.monto_item and dp.stock and dp.stock > 0:
                        precio_efectivo_por_unidad = int(dp.monto_item / dp.stock)
                        if precio_efectivo_por_unidad < dp.precio:
                            dcto_unitario = dp.precio - precio_efectivo_por_unidad

                    subtotal = (precio_lista - dcto_unitario) * dp.stock

                    Ticket_Productos.objects.create(
                        idTicket=ticket_referencia,
                        ProductoTalla=dp.productoTalla,
                        stock=dp.stock,
                        precio=precio_lista,
                        precio_original=precio_lista,
                        descuento_unitario=dcto_unitario,
                        subtotal=subtotal,
                        porcentaje_descuento=dp.descuento_pct or 0,
                        descripcion_linea=dp.descripcion if not dp.productoTalla else None,
                        es_pendiente_despacho=dp.es_pendiente_despacho,
                    )
                
            # 🔄 SEGUIR LA CADENA DE CAMBIOS HASTA EL TICKET MÁS RECIENTE
            # Incluye COMPLETADO y estados ejecutados con ticket_nuevo existente
            ticket_actual = ticket_referencia
            tickets_visitados = set()
            ticket_original_correlativo = ticket_referencia.correlativo

            while ticket_actual.id not in tickets_visitados:
                tickets_visitados.add(ticket_actual.id)

                cambio_siguiente = CambioDevolucion.objects.filter(
                    ticket_original=ticket_actual,
                    estado__in=['COMPLETADO', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE'],
                    ticket_nuevo__isnull=False
                ).order_by('-fecha_ejecucion', '-fecha_completado').first()

                if cambio_siguiente and cambio_siguiente.ticket_nuevo:
                    ticket_actual = cambio_siguiente.ticket_nuevo
                else:
                    break

            ticket_referencia = ticket_actual  # Usar el ticket más reciente
            fue_redirigido_dte = (ticket_referencia.correlativo != ticket_original_correlativo)
            
            # Obtener productos del ticket (con IDs correctos de Ticket_Productos)
            # ✅ CORREGIDO: Mostrar TODOS los productos, incluyendo los ya cambiados
            # ❌ EXCLUIR productos con precio negativo (ítems de devolución de cambios anteriores)
            productos_data = []
            productos_disponibles_count = 0
            
            for tp in ticket_referencia.ticket_productos.filter(precio__gt=0, stock__gt=0):
                # Verificar si ya fue cambiado/devuelto
                cantidad_ya_cambiada = CambioDevolucionDetalle.objects.filter(
                    producto_original=tp,
                    cambio_devolucion__estado__in=['SOLICITADO', 'APROBADO', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE', 'COMPLETADO']
                ).aggregate(
                    total=Sum('cantidad_original')
                )['total'] or 0
                
                cantidad_disponible = max(0, tp.stock - cantidad_ya_cambiada)
                
                if cantidad_disponible > 0:
                    productos_disponibles_count += 1
                
                # ✅ Incluir TODOS los productos (disponibles y ya cambiados)
                if tp.ProductoTalla:
                    descuento = tp.descuento_unitario or 0
                    precio_pagado = tp.precio - descuento
                    
                    productos_data.append({
                        'id': tp.id,
                        'sku': tp.ProductoTalla.sku,
                        'articulo': tp.ProductoTalla.producto.articulo if tp.ProductoTalla.producto else (tp.descripcion_linea or ''),
                        'descripcion': tp.ProductoTalla.producto.descripcion if tp.ProductoTalla.producto else (tp.descripcion_linea or ''),
                        'talla': tp.ProductoTalla.talla,
                        'cantidad_original': tp.stock,
                        'cantidad_ya_cambiada': cantidad_ya_cambiada,
                        'cantidad_disponible': cantidad_disponible,
                        'precio_unitario': float(precio_pagado),
                        'precio_lista': float(tp.precio),
                        'descuento_unitario': float(descuento),
                        'tiene_descuento': descuento > 0,
                        'subtotal': float(precio_pagado * tp.stock),
                        'ya_cambiado': cantidad_disponible == 0,
                    })
                else:
                    # Ítems pendientes de despacho no aplican para cambios/devoluciones
                    pass
            
            return JsonResponse({
                'success': True,
                'documento': {
                    'id': ticket_referencia.id,
                    'tipo': 'DTE',
                    'numero_documento': dte.numero_documento,
                    'tipo_documento': dte.tipo_documento,
                    'correlativo': ticket_referencia.correlativo,
                    'correlativo_original': ticket_original_correlativo if fue_redirigido_dte else ticket_referencia.correlativo,
                    'fue_redirigido': fue_redirigido_dte,
                    'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                    'total': float(dte.monto_con_iva),
                    'vendedor': dte.vendedor.nombre if dte.vendedor else 'Sin vendedor',
                    'cliente_nombre': dte.receptor.razon_social if dte.receptor else 'Sin nombre',
                    'cliente_rut': dte.receptor.rut if dte.receptor else '',
                    'fecha_limite_cambio': fecha_limite.strftime('%d/%m/%Y'),
                    'dentro_del_plazo': dentro_del_plazo,
                    'dias_transcurridos': (timezone.localdate() - dte.fecha_emision).days,
                    'productos': productos_data,
                    'productos_disponibles': productos_disponibles_count,
                    'puede_cambiar': productos_disponibles_count > 0,
                }
            })
        elif tipo_documento == 'ticket':
            # Buscar directamente por correlativo de ticket
            ticket = Ticket.objects.filter(
                correlativo=numero,
                sucursal_id=sucursal_id
            ).first()
            
            if not ticket:
                return JsonResponse({
                    'success': False,
                    'error': f'Ticket #{numero} no encontrado en esta sucursal'
                })
            
            return buscar_ticket_para_cambio_response(ticket, request)
        
        elif tipo_documento == 'ticket_cambio':
            # Buscar por Ticket de Cambio (número del ticket original)
            
            # El ticket de cambio puede tener varios formatos:
            # 1. Nuevo formato: TC-{SUCURSAL}-{TICKET}-{FECHA} (ej: TC-SUC1-123-250120)
            # 2. Formato anterior: TC-{TICKET} (ej: TC-123)
            # 3. Solo número: 123
            numero_limpio = numero.upper().strip()
            numero_ticket = None
            fecha_extraida = None
            
            if numero_limpio.startswith('TC-'):
                partes = numero_limpio.split('-')
                if len(partes) >= 4:
                    # Nuevo formato: TC-SUCURSAL-TICKET-FECHA
                    # TC-SUC1-123-250120 → ticket=123, fecha=2025-01-20
                    numero_ticket = partes[2]  # El tercer elemento es el número de ticket
                    # Extraer fecha del formato YYMMDD
                    if len(partes[3]) == 6:
                        try:
                            fecha_str = partes[3]  # YYMMDD
                            year = 2000 + int(fecha_str[0:2])
                            month = int(fecha_str[2:4])
                            day = int(fecha_str[4:6])
                            fecha_extraida = f"{year}-{month:02d}-{day:02d}"
                            print(f"  📅 Fecha extraída del código: {fecha_extraida}")
                        except:
                            pass
                elif len(partes) == 2:
                    # Formato anterior: TC-123
                    numero_ticket = partes[1]
                else:
                    numero_ticket = numero_limpio.replace('TC-', '')
            else:
                # Solo número
                numero_ticket = numero_limpio
            
            # Si se extrajo fecha del código y no se proporcionó fecha_compra, usarla
            if fecha_extraida and not fecha_compra:
                fecha_compra = fecha_extraida
                print(f"  📅 Usando fecha extraída del código de cambio: {fecha_compra}")
            
            # Buscar el ticket original
            ticket_query = Ticket.objects.select_related(
                'vendedor', 'sucursal'
            ).prefetch_related(
                'ticket_productos__ProductoTalla__producto',
                'cambios_devoluciones'
            ).filter(
                correlativo=numero_ticket,
                sucursal_id=sucursal_id
            )
            
            if fecha_compra:
                ticket_query = ticket_query.filter(fecha=fecha_compra)
            
            ticket = ticket_query.first()
            
            if not ticket:
                # Buscar sin filtro de fecha para dar mejor mensaje
                ticket_sin_fecha = Ticket.objects.filter(
                    correlativo=numero_ticket,
                    sucursal_id=sucursal_id
                ).first()
                
                if ticket_sin_fecha:
                    return JsonResponse({
                        'success': False,
                        'error': f'Ticket de Cambio #{numero_ticket} encontrado con fecha {ticket_sin_fecha.fecha.strftime("%d/%m/%Y")}, pero usted buscó con fecha {fecha_compra}. Corrija la fecha.'
                    })
                
                return JsonResponse({
                    'success': False,
                    'error': f'Ticket de Cambio #{numero_ticket} no encontrado. Verifique el número del ticket y la sucursal.'
                })
            
            # Seguir la cadena de cambios hasta el ticket más reciente
            # Incluye COMPLETADO y estados ejecutados con ticket_nuevo existente
            ticket_actual = ticket
            tickets_visitados = set()
            ticket_original_correlativo = ticket.correlativo

            while ticket_actual.id not in tickets_visitados:
                tickets_visitados.add(ticket_actual.id)

                cambio_siguiente = CambioDevolucion.objects.filter(
                    ticket_original=ticket_actual,
                    estado__in=['COMPLETADO', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE'],
                    ticket_nuevo__isnull=False
                ).order_by('-fecha_ejecucion', '-fecha_completado').first()

                if cambio_siguiente and cambio_siguiente.ticket_nuevo:
                    print(f"🔄 Ticket #{ticket_actual.correlativo} → Siguiente: #{cambio_siguiente.ticket_nuevo.correlativo} (estado: {cambio_siguiente.estado})")
                    ticket_actual = cambio_siguiente.ticket_nuevo
                else:
                    break

            fue_redirigido = (ticket_actual.correlativo != ticket_original_correlativo)
            ticket = ticket_actual

            # Verificar estado y plazo - permitir PAGADO y PENDIENTE (tickets de cambio pueden estar pendientes)
            if ticket.estado not in ('PAGADO', 'PENDIENTE'):
                return JsonResponse({
                    'success': False,
                    'error': f'El ticket referenciado está en estado "{ticket.get_estado_display()}". No se puede procesar.'
                })

            from datetime import timedelta
            fecha_limite = ticket.fecha + timedelta(days=30) if ticket.fecha else timezone.localdate() + timedelta(days=30)
            dentro_del_plazo = timezone.localdate() <= fecha_limite

            # Obtener productos disponibles
            # Filtrar precio > 0 para excluir ítems de devolución (precio negativo) de cambios anteriores
            productos_data = []
            productos_disponibles_count = 0

            for tp in ticket.ticket_productos.filter(precio__gt=0, stock__gt=0):
                if tp.ProductoTalla is None:
                    continue

                cantidad_ya_cambiada = CambioDevolucionDetalle.objects.filter(
                    producto_original=tp,
                    cambio_devolucion__estado__in=['SOLICITADO', 'APROBADO', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE', 'COMPLETADO']
                ).aggregate(
                    total=Sum('cantidad_original')
                )['total'] or 0

                cantidad_disponible = max(0, tp.stock - cantidad_ya_cambiada)

                if cantidad_disponible > 0:
                    productos_disponibles_count += 1

                # Incluir TODOS los productos (disponibles y ya cambiados) para visibilidad
                descuento = tp.descuento_unitario or 0
                precio_pagado = tp.precio - descuento

                productos_data.append({
                    'id': tp.id,
                    'sku': tp.ProductoTalla.sku,
                    'articulo': tp.ProductoTalla.producto.articulo if tp.ProductoTalla.producto else (tp.descripcion_linea or ''),
                    'descripcion': tp.ProductoTalla.producto.descripcion if tp.ProductoTalla.producto else (tp.descripcion_linea or ''),
                    'talla': tp.ProductoTalla.talla,
                    'cantidad_original': tp.stock,
                    'cantidad_ya_cambiada': cantidad_ya_cambiada,
                    'cantidad_disponible': cantidad_disponible,
                    'precio_unitario': float(precio_pagado),
                    'precio_lista': float(tp.precio),
                    'descuento_unitario': float(descuento),
                    'tiene_descuento': descuento > 0,
                    'subtotal': float(precio_pagado * tp.stock),
                    'ya_cambiado': cantidad_disponible == 0,
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
            
            return JsonResponse({
                'success': True,
                'documento': {
                    'id': ticket.id,
                    'tipo': 'TICKET_CAMBIO',
                    'correlativo': ticket.correlativo,
                    'correlativo_original': ticket_original_correlativo if fue_redirigido else ticket.correlativo,
                    'fue_redirigido': fue_redirigido,
                    'fecha': ticket.fecha.strftime('%d/%m/%Y'),
                    'hora': ticket.hora.strftime('%H:%M') if ticket.hora else '',
                    'total': float(ticket.total),
                    'estado': ticket.estado,
                    'vendedor': ticket.vendedor.nombre if ticket.vendedor else '',
                    'cliente_nombre': ticket.cliente_nombre or '',
                    'cliente_rut': ticket.cliente_rut or '',
                    'fecha_limite_cambio': fecha_limite.strftime('%d/%m/%Y'),
                    'dentro_del_plazo': dentro_del_plazo,
                    'dias_transcurridos': (timezone.localdate() - ticket.fecha).days,
                    'productos': productos_data,
                    'productos_disponibles': productos_disponibles_count,
                    'cambios_anteriores': cambios_anteriores,
                    'puede_cambiar': productos_disponibles_count > 0,
                }
            })
        
        else:
            # Buscar Ticket (lógica existente)
            return buscar_ticket_para_cambio_original(request, numero, fecha_compra, sucursal_id)
        
    except Exception as e:
        import traceback
        print(f"❌ ERROR en buscar_documento_cambio:")
        print(f"   Mensaje: {str(e)}")
        print(f"   Traceback completo:")
        traceback.print_exc()
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
    
    # 🔄 SEGUIR LA CADENA DE CAMBIOS HASTA EL TICKET MÁS RECIENTE
    ticket_actual = ticket
    tickets_visitados = set()  # Para evitar loops infinitos
    
    while ticket_actual.id not in tickets_visitados:
        tickets_visitados.add(ticket_actual.id)
        
        # Buscar si este ticket tiene cambios completados que generaron un nuevo ticket
        cambio_completado = CambioDevolucion.objects.filter(
            ticket_original=ticket_actual,
            estado='COMPLETADO',
            ticket_nuevo__isnull=False
        ).order_by('-fecha_completado').first()
        
        if cambio_completado and cambio_completado.ticket_nuevo:
            ticket_actual = cambio_completado.ticket_nuevo
        else:
            # No hay más cambios, este es el ticket actual
            break
    
    # Si el ticket cambió, informar al usuario
    if ticket_actual.correlativo != correlativo:
        ticket = ticket_actual  # Usar el ticket más reciente
    
    # Llamar a la función original con toda la lógica
    return buscar_ticket_para_cambio_response(ticket, request)


def buscar_ticket_para_cambio_response(ticket, request):
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
    dentro_del_plazo = timezone.localdate() <= fecha_limite
    
    # Obtener productos del ticket
    # ✅ CORREGIDO: Mostrar TODOS los productos, incluyendo los ya cambiados
    # ❌ EXCLUIR productos con precio negativo (ítems de devolución de cambios anteriores)
    productos_data = []
    productos_disponibles_count = 0
    
    for tp in ticket.ticket_productos.filter(precio__gt=0, stock__gt=0):
        cantidad_ya_cambiada = CambioDevolucionDetalle.objects.filter(
            producto_original=tp,
            cambio_devolucion__estado__in=['SOLICITADO', 'APROBADO', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE', 'EJECUTADO_DEVOL_PENDIENTE', 'COMPLETADO']
        ).aggregate(
            total=Sum('cantidad_original')
        )['total'] or 0
        
        cantidad_disponible = max(0, tp.stock - cantidad_ya_cambiada)
        
        if cantidad_disponible > 0:
            productos_disponibles_count += 1
        
        # ✅ Incluir TODOS los productos (disponibles y ya cambiados), omitir ítems sin SKU
        if tp.ProductoTalla is None:
            continue
        
        descuento = tp.descuento_unitario or 0
        precio_pagado = tp.precio - descuento
        
        productos_data.append({
            'id': tp.id,
            'sku': tp.ProductoTalla.sku,
            'articulo': tp.ProductoTalla.producto.articulo if tp.ProductoTalla.producto else (tp.descripcion_linea or ''),
            'descripcion': tp.ProductoTalla.producto.descripcion if tp.ProductoTalla.producto else (tp.descripcion_linea or ''),
            'talla': tp.ProductoTalla.talla,
            'cantidad_original': tp.stock,
            'cantidad_ya_cambiada': cantidad_ya_cambiada,
            'cantidad_disponible': cantidad_disponible,
            'precio_unitario': float(precio_pagado),
            'precio_lista': float(tp.precio),
            'descuento_unitario': float(descuento),
            'tiene_descuento': descuento > 0,
            'subtotal': float(precio_pagado * tp.stock),
            'ya_cambiado': cantidad_disponible == 0,
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
    
    # Verificar si hubo redirección
    ticket_original_buscado = request.GET.get('correlativo') if hasattr(request, 'GET') else None
    fue_redirigido = (ticket_original_buscado and 
                     str(ticket.correlativo) != str(ticket_original_buscado))
    
    ticket_data = {
        'id': ticket.id,
        'tipo': 'TICKET',
        'correlativo': ticket.correlativo,
        'correlativo_original': ticket_original_buscado if fue_redirigido else ticket.correlativo,
        'fue_redirigido': fue_redirigido,
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
        'dias_transcurridos': (timezone.localdate() - ticket.fecha).days,
        'productos': productos_data,
        'productos_disponibles': productos_disponibles_count,
        'cambios_anteriores': cambios_anteriores,
        'puede_cambiar': productos_disponibles_count > 0,
    }
    
    return JsonResponse({
        'success': True,
        'documento': ticket_data
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
        
        return buscar_ticket_para_cambio_response(ticket, request)
        
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
            'producto', 'producto__atributo1', 'producto__atributo2'
        ).filter(
            Q(sku__icontains=termino) |
            Q(producto__articulo__icontains=termino) |
            Q(producto__descripcion__icontains=termino) |
            Q(producto__atributo1__valor__icontains=termino) |
            Q(producto__atributo2__valor__icontains=termino),
            producto__sucursal_id=sucursal_id,
            stock__gt=0
        )[:100]  # Límite ampliado para cubrir marcas con muchos modelos/tallas
        
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
        tipo_documento = data.get('tipo_documento', 'BOLETA_ELECTRONICA')
        
        # Datos adicionales
        giro = data.get('giro', '').strip()
        direccion = data.get('direccion', '').strip()
        comuna = data.get('comuna', '').strip()
        ciudad = data.get('ciudad', '').strip()
        telefono_secundario = data.get('telefono_secundario', '').strip()
        email_facturacion = data.get('email_facturacion', '').strip()
        
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
            cliente = Empresa.objects.filter(rut=rut).order_by('-id').first()
        
        if cliente:
            # Actualizar datos existentes para empresas/personas
            if nombre:
                cliente.nombre = nombre
                cliente.razon_social = nombre

            if email or email_facturacion:
                cliente.correoVendedor = email or cliente.correoVendedor or ''
                cliente.correoAdministrador = email_facturacion or cliente.correoAdministrador or ''

            if telefono or telefono_secundario:
                cliente.contacto1 = telefono or cliente.contacto1 or ''
                cliente.contacto2 = telefono_secundario or cliente.contacto2 or ''

            # Actualizar campos tributarios (para facturas o si vienen valores)
            if tipo_documento == 'FACTURA_ELECTRONICA' or giro or direccion or comuna or ciudad:
                if giro:
                    cliente.giro = giro
                if direccion:
                    cliente.direccion = direccion
                if comuna:
                    cliente.comuna = comuna
                if ciudad:
                    cliente.ciudad = ciudad

            cliente.save()
            print(f"✅ Cliente actualizado (ID {cliente.id}) - giro: {cliente.giro}")
        else:
            # Crear nuevo registro (empresa) solo si hay RUT
            if not rut:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe proporcionar un RUT para crear un nuevo cliente'
                })

            cliente = Empresa.objects.create(
                nombre=nombre or f'Cliente {rut}',
                rut=rut,
                nombre_fantasia=nombre or '',
                razon_social=nombre or '',
                giro=giro,
                direccion=direccion,
                comuna=comuna,
                ciudad=ciudad,
                esProveedor=False,
                correoVendedor=email or '',
                correoAdministrador=email_facturacion or '',
                correoIntercambio='',
                contacto1=telefono or '',
                contacto2=telefono_secundario or '',
            )
            print(f"✅ Cliente creado (ID {cliente.id}) - giro: {cliente.giro}")
        
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


# ========== DASHBOARD DE VENTAS ==========

@login_required
def dashboard_ventas(request):
    """Vista principal del dashboard de ventas - NEXO Design System"""
    return render(request, 'vistas/modulo_dashboards/dashboard_ventas_nexo.html')


@login_required
def dashboard_ventas_mejorado(request):
    """Vista del dashboard de ventas mejorado - NEXO Design System"""
    return render(request, 'vistas/modulo_dashboards/dashboard_ventas_nexo.html')


@require_GET
@login_required
def obtener_indicadores_globales_ventas(request):
    """
    API para obtener indicadores globales de ventas
    Incluye: ventas totales, ticket promedio, cantidad ventas, crecimiento
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        vendedor_id = request.GET.get('vendedor_id')
        metodo_pago = request.GET.get('metodo_pago')
        estado = request.GET.get('estado', '')  # Vacío por defecto para mostrar todos
        periodo_comparacion = request.GET.get('periodo_comparacion', 'mes_anterior')
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            try:
                fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Formato de fecha inválido. Use YYYY-MM-DD'
                }, status=400)
        
        # Construir queryset base
        queryset = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        
        # Aplicar filtro de estado - por defecto solo tickets PAGADOS
        if estado:
            queryset = queryset.filter(estado=estado)
        else:
            queryset = queryset.filter(estado='PAGADO')
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        if vendedor_id:
            queryset = queryset.filter(vendedor_id=vendedor_id)
        
        if metodo_pago:
            # Filtrar por método de pago desde TicketDetallePago
            tickets_con_metodo = TicketDetallePago.objects.filter(
                metodo_pago=metodo_pago,
                ticket__fecha__gte=fecha_inicio,
                ticket__fecha__lte=fecha_fin
            ).values_list('ticket_id', flat=True).distinct()
            queryset = queryset.filter(id__in=tickets_con_metodo)
        
        # Calcular métricas del período actual
        ventas_totales = queryset.aggregate(total=Sum('total'))['total'] or 0
        cantidad_ventas = queryset.count()
        ticket_promedio = ventas_totales / cantidad_ventas if cantidad_ventas > 0 else 0
        
        # Calcular métricas del período de comparación
        if periodo_comparacion == 'mes_anterior':
            dias_diferencia = (fecha_fin - fecha_inicio).days
            fecha_comp_fin = fecha_inicio - timedelta(days=1)
            fecha_comp_inicio = fecha_comp_fin - timedelta(days=dias_diferencia)
        elif periodo_comparacion == 'mes_mismo_anio_anterior':
            fecha_comp_inicio = fecha_inicio.replace(year=fecha_inicio.year - 1)
            fecha_comp_fin = fecha_fin.replace(year=fecha_fin.year - 1)
        else:  # semana_anterior
            fecha_comp_fin = fecha_inicio - timedelta(days=1)
            fecha_comp_inicio = fecha_comp_fin - timedelta(days=6)
        
        # Queryset de comparación
        queryset_comp = Ticket.objects.filter(
            fecha__gte=fecha_comp_inicio,
            fecha__lte=fecha_comp_fin
        )
        
        if estado:
            queryset_comp = queryset_comp.filter(estado=estado)
        if sucursal_id:
            queryset_comp = queryset_comp.filter(sucursal_id=sucursal_id)
        if vendedor_id:
            queryset_comp = queryset_comp.filter(vendedor_id=vendedor_id)
        if metodo_pago:
            tickets_comp_metodo = TicketDetallePago.objects.filter(
                metodo_pago=metodo_pago,
                ticket__fecha__gte=fecha_comp_inicio,
                ticket__fecha__lte=fecha_comp_fin
            ).values_list('ticket_id', flat=True).distinct()
            queryset_comp = queryset_comp.filter(id__in=tickets_comp_metodo)
        
        ventas_comp = queryset_comp.aggregate(total=Sum('total'))['total'] or 0
        cantidad_comp = queryset_comp.count()
        ticket_comp = ventas_comp / cantidad_comp if cantidad_comp > 0 else 0
        
        # Calcular crecimientos
        crecimiento_ventas = ((ventas_totales - ventas_comp) / ventas_comp * 100) if ventas_comp > 0 else 0
        crecimiento_cantidad = ((cantidad_ventas - cantidad_comp) / cantidad_comp * 100) if cantidad_comp > 0 else 0
        crecimiento_ticket = ((ticket_promedio - ticket_comp) / ticket_comp * 100) if ticket_comp > 0 else 0
        
        # Cambios y devoluciones
        cambios = CambioDevolucion.objects.filter(
            fecha_solicitud__date__gte=fecha_inicio,
            fecha_solicitud__date__lte=fecha_fin
        )
        
        if sucursal_id:
            cambios = cambios.filter(sucursal_id=sucursal_id)
        
        cantidad_cambios = cambios.count()
        ratio_cambios = (cantidad_cambios / cantidad_ventas * 100) if cantidad_ventas > 0 else 0
        
        # Descuentos aplicados en el periodo
        ticket_ids_list = queryset.values_list('id', flat=True)
        lineas_qs = Ticket_Productos.objects.filter(idTicket_id__in=ticket_ids_list)
        desc_agg = lineas_qs.aggregate(
            descuento_total=Sum(ExpressionWrapper(F('stock') * F('descuento_unitario'), output_field=DecimalField())),
            descuento_prom_pct=Avg('porcentaje_descuento'),
        )
        descuento_total = float(desc_agg['descuento_total'] or 0)
        descuento_prom_pct = float(desc_agg['descuento_prom_pct'] or 0)
        
        # Ventas por tipo de documento
        ventas_con_factura = queryset.filter(tipo_dte__in=['FACTURA_ELECTRONICA', 'FACTURA_EXENTA']).count()
        ventas_con_boleta = queryset.filter(tipo_dte='BOLETA_ELECTRONICA').count()
        tickets_offline = queryset.filter(created_offline=True).count()
        
        # Evolución diaria de ventas - Asegurar que haya datos para todos los días del período
        evolucion_diaria = queryset.values('fecha').annotate(
            total=Sum('total'),
            cantidad=Count('id')
        ).order_by('fecha')
        
        # Crear diccionario con todas las fechas del período
        fecha_actual = fecha_inicio
        todas_fechas = {}
        while fecha_actual <= fecha_fin:
            todas_fechas[fecha_actual] = {'total': 0, 'cantidad': 0}
            fecha_actual += timedelta(days=1)
        
        # Llenar con datos reales
        for item in evolucion_diaria:
            todas_fechas[item['fecha']] = {
                'total': float(item['total'] or 0),
                'cantidad': item['cantidad']
            }
        
        # Convertir a lista ordenada
        evolucion_data = [
            {
                'fecha': fecha.strftime('%d/%m'),
                'total': datos['total'],
                'cantidad': datos['cantidad']
            }
            for fecha, datos in sorted(todas_fechas.items())
        ]
        
        return JsonResponse({
            'success': True,
            'ventas_totales': float(ventas_totales),
            'cantidad_ventas': cantidad_ventas,
            'ticket_promedio': float(ticket_promedio),
            'cantidad_cambios': cantidad_cambios,
            'ratio_cambios': float(ratio_cambios),
            'crecimiento_ventas': float(crecimiento_ventas),
            'crecimiento_cantidad': float(crecimiento_cantidad),
            'crecimiento_ticket': float(crecimiento_ticket),
            'evolucion_diaria': evolucion_data,
            'descuento_total': descuento_total,
            'descuento_prom_pct': round(descuento_prom_pct, 2),
            'ventas_con_factura': ventas_con_factura,
            'ventas_con_boleta': ventas_con_boleta,
            'tickets_offline': tickets_offline,
            'periodo': {
                'inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fin': fecha_fin.strftime('%d/%m/%Y')
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener indicadores globales: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_ventas_por_vendedor(request):
    """
    API para obtener ventas por vendedor con métricas individuales
    Incluye: ranking, comisiones, participación
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        estado = request.GET.get('estado', '')  # Vacío por defecto
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            try:
                fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Formato de fecha inválido'
                }, status=400)
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        
        # Solo aplicar filtro de estado si tiene valor
        if estado:
            queryset = queryset.filter(estado=estado)
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Calcular total general para participación
        total_general = queryset.aggregate(total=Sum('total'))['total'] or 0
        
        # Agrupar por vendedor
        ventas_vendedor = queryset.values(
            'vendedor__id',
            'vendedor__codigo_vendedor',
            'vendedor__nombre',
            'vendedor__comision'
        ).annotate(
            total_vendido=Sum('total'),
            cantidad_ventas=Count('id'),
            ticket_promedio=Avg('total')
        ).order_by('-total_vendido')
        
        vendedores_data = []
        top_vendedores = []
        
        for idx, venta in enumerate(ventas_vendedor):
            total_vendido = float(venta['total_vendido'] or 0)
            cantidad_ventas = venta['cantidad_ventas']
            ticket_promedio = float(venta['ticket_promedio'] or 0)
            comision_porcentaje = float(venta['vendedor__comision'] or 0)
            comision_total = total_vendido * (comision_porcentaje / 100)
            participacion = (total_vendido / total_general * 100) if total_general > 0 else 0
            
            # Calcular rendimiento (basado en participación relativa)
            if idx == 0 and total_vendido > 0:
                rendimiento = 100
            elif total_vendido > 0 and ventas_vendedor[0]['total_vendido']:
                rendimiento = (total_vendido / float(ventas_vendedor[0]['total_vendido']) * 100)
            else:
                rendimiento = 0
            
            vendedor_info = {
                'id': venta['vendedor__id'],
                'codigo': venta['vendedor__codigo_vendedor'] or 'S/C',
                'nombre': venta['vendedor__nombre'] or 'Sin nombre',
                'cantidad_ventas': cantidad_ventas,
                'total_vendido': total_vendido,
                'ticket_promedio': ticket_promedio,
                'comision_porcentaje': comision_porcentaje,
                'comision_total': comision_total,
                'participacion': float(participacion),
                'rendimiento': float(rendimiento)
            }
            
            vendedores_data.append(vendedor_info)
            
            # Top 10 vendedores para gráfico
            if idx < 10:
                top_vendedores.append({
                    'nombre': venta['vendedor__nombre'] or 'Sin nombre',
                    'total': total_vendido
                })
        
        return JsonResponse({
            'success': True,
            'vendedores': vendedores_data,
            'top_vendedores': top_vendedores,
            'total_vendedores': len(vendedores_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener ventas por vendedor: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_sucursales_dashboard(request):
    """
    API para obtener lista de sucursales para filtros del dashboard.
    Usa la utilidad centralizada de permisos para determinar visibilidad.
    """
    try:
        from .utils_permisos import obtener_sucursales_usuario, usuario_puede_ver_todas_sucursales

        sucursales = obtener_sucursales_usuario(request.user)

        sucursales_data = []
        for sucursal in sucursales:
            sucursales_data.append({
                'id': sucursal.id,
                'nombre': sucursal.alias,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion or ''
            })

        empresa_user = EmpresaUser.objects.filter(
            user=request.user,
            active=True
        ).first()

        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data,
            'es_admin': usuario_puede_ver_todas_sucursales(request.user),
            'sucursal_actual': empresa_user.sucursal_id if empresa_user else None
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener sucursales: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_ventas_por_sucursal(request):
    """
    API para obtener análisis comparativo de ventas por sucursal
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        estado = request.GET.get('estado', '')  # Vacío por defecto
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            try:
                fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Formato de fecha inválido'
                }, status=400)
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        
        # Solo aplicar filtro de estado si tiene valor
        if estado:
            queryset = queryset.filter(estado=estado)
        
        # Consultar ventas por sucursal
        ventas_sucursal = queryset.values(
            'sucursal__id',
            'sucursal__alias'
        ).annotate(
            total_ventas=Sum('total'),
            cantidad=Count('id')
        ).order_by('-total_ventas')
        
        sucursales_data = []
        for venta in ventas_sucursal:
            total = float(venta['total_ventas'] or 0)
            cantidad = venta['cantidad']
            ticket_promedio = total / cantidad if cantidad > 0 else 0
            
            sucursales_data.append({
                'id': venta['sucursal__id'],
                'sucursal': venta['sucursal__alias'] or 'Sin nombre',
                'total': total,
                'cantidad': cantidad,
                'ticket_promedio': ticket_promedio
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener ventas por sucursal: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_ventas_por_metodo_pago(request):
    """
    API para obtener distribución de ventas por método de pago
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        estado = request.GET.get('estado', '')  # Vacío por defecto
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            try:
                fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Formato de fecha inválido'
                }, status=400)
        
        # Construir queryset
        queryset = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        
        # Solo aplicar filtro de estado si tiene valor
        if estado:
            queryset = queryset.filter(estado=estado)
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Obtener IDs de tickets que cumplen con los filtros
        ticket_ids = queryset.values_list('id', flat=True)
        
        # Agrupar por método de pago desde TicketDetallePago
        ventas_metodo = TicketDetallePago.objects.filter(
            ticket_id__in=ticket_ids
        ).values('metodo_pago').annotate(
            total=Sum('monto'),
            cantidad=Count('id')
        ).order_by('-total')
        
        metodos_data = []
        total_general = 0
        
        for metodo in ventas_metodo:
            total = float(metodo['total'] or 0)
            total_general += total
            
            # Obtener nombre legible del método
            metodo_nombre = dict(METODO_PAGO_TICKET_CHOICES).get(
                metodo['metodo_pago'], 
                metodo['metodo_pago']
            )
            
            metodos_data.append({
                'metodo': metodo_nombre,
                'codigo': metodo['metodo_pago'],
                'total': total,
                'cantidad': metodo['cantidad']
            })
        
        # Calcular porcentajes
        for metodo in metodos_data:
            metodo['porcentaje'] = (metodo['total'] / total_general * 100) if total_general > 0 else 0
        
        return JsonResponse({
            'success': True,
            'metodos_pago': metodos_data,
            'total': total_general
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener ventas por método de pago: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_analisis_cambios_devoluciones(request):
    """
    API para obtener análisis de cambios y devoluciones
    Incluye: ratio, motivos, impacto financiero
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        
        # Consultar cambios y devoluciones
        queryset = CambioDevolucion.objects.filter(
            fecha_solicitud__date__gte=fecha_inicio,
            fecha_solicitud__date__lte=fecha_fin
        )
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Métricas generales
        total_cambios = queryset.count()
        monto_total = queryset.aggregate(
            total=Sum('monto_original')
        )['total'] or 0
        
        # Total de ventas para calcular ratio
        ventas_total = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin,
            estado='PAGADO'
        )
        
        if sucursal_id:
            ventas_total = ventas_total.filter(sucursal_id=sucursal_id)
        
        cantidad_ventas = ventas_total.count()
        ratio = (total_cambios / cantidad_ventas * 100) if cantidad_ventas > 0 else 0
        
        # Análisis por motivo (desde CambioDevolucion)
        motivos_cambio = queryset.filter(
            motivo_principal__isnull=False
        ).values('motivo_principal').annotate(
            cantidad=Count('id')
        ).order_by('-cantidad')
        
        motivos_data = []
        for item in motivos_cambio:
            if item['motivo_principal']:
                motivo_nombre = dict(MOTIVO_CAMBIO_CHOICES).get(
                    item['motivo_principal'],
                    item['motivo_principal']
                )
                motivos_data.append({
                    'motivo': motivo_nombre,
                    'cantidad': item['cantidad']
                })
        
        # Análisis por tipo de operación
        por_tipo = queryset.values('tipo_operacion').annotate(
            cantidad=Count('id'),
            monto=Sum('monto_original')
        )
        
        tipos_data = []
        for tipo in por_tipo:
            tipo_nombre = dict(TIPO_OPERACION_CAMBIO_CHOICES).get(
                tipo['tipo_operacion'],
                tipo['tipo_operacion']
            )
            tipos_data.append({
                'tipo': tipo_nombre,
                'cantidad': tipo['cantidad'],
                'monto': float(tipo['monto'] or 0)
            })
        
        # Análisis por estado
        por_estado = queryset.values('estado').annotate(
            cantidad=Count('id')
        )
        
        estados_data = []
        for estado in por_estado:
            estado_nombre = dict(ESTADO_CAMBIO_CHOICES).get(
                estado['estado'],
                estado['estado']
            )
            estados_data.append({
                'estado': estado_nombre,
                'cantidad': estado['cantidad']
            })
        
        return JsonResponse({
            'success': True,
            'total_cambios': total_cambios,
            'monto_total': float(monto_total),
            'ratio': float(ratio),
            'por_motivo': motivos_data,
            'por_tipo': tipos_data,
            'por_estado': estados_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener análisis de cambios: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_analisis_fraude_cambios(request):
    """
    API para obtener análisis de detección de fraude en cambios y devoluciones.
    Solo accesible para administradores, jefes locales y administración.
    """
    try:
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ['administrador', 'jefe_local', 'administracion']:
            return JsonResponse({'success': False, 'error': 'No tiene permisos para acceder a esta información'}, status=403)

        from .services.fraud_detection import (
            detectar_vendedores_alto_retorno,
            detectar_productos_multiples_cambios,
            detectar_perdidas_no_apto,
            detectar_cambios_fuera_plazo,
            detectar_patrones_cross_branch,
        )

        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')

        if fecha_inicio and fecha_fin:
            from datetime import datetime
            fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        else:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)

        vendedores = detectar_vendedores_alto_retorno(sucursal_id, fecha_inicio, fecha_fin)
        productos = detectar_productos_multiples_cambios(sucursal_id, fecha_inicio, fecha_fin)
        perdidas = detectar_perdidas_no_apto(sucursal_id, fecha_inicio, fecha_fin)
        fuera_plazo = detectar_cambios_fuera_plazo(sucursal_id, fecha_inicio, fecha_fin)
        cross_branch = detectar_patrones_cross_branch(fecha_inicio, fecha_fin)

        alertas_vendedores = len([v for v in vendedores if v['alerta']])
        alertas_productos = len(productos)
        alertas_total = alertas_vendedores + alertas_productos + (1 if perdidas['total_items'] > 0 else 0) + (1 if fuera_plazo['total'] > 0 else 0) + (1 if cross_branch['pendientes_revision'] > 0 else 0)

        return JsonResponse({
            'success': True,
            'alertas_total': alertas_total,
            'vendedores_alto_retorno': vendedores[:10],
            'productos_multiples_cambios': productos[:10],
            'perdidas_no_apto': perdidas,
            'cambios_fuera_plazo': fuera_plazo,
            'patrones_cross_branch': cross_branch,
            'periodo': {
                'fecha_inicio': fecha_inicio.strftime('%Y-%m-%d'),
                'fecha_fin': fecha_fin.strftime('%Y-%m-%d'),
            },
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': f'Error al obtener análisis de fraude: {str(e)}'}, status=500)


@require_GET
@login_required
def obtener_analisis_cambios_avanzado(request):
    """
    API para obtener análisis avanzado completo de cambios y devoluciones.
    Solo accesible para administradores, jefes locales y administración.
    """
    try:
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ['administrador', 'jefe_local', 'administracion']:
            return JsonResponse({'success': False, 'error': 'No tiene permisos para acceder a esta información'}, status=403)

        from .services.fraud_detection import obtener_analisis_avanzado

        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')

        if fecha_inicio and fecha_fin:
            from datetime import datetime
            fecha_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        else:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)

        analisis = obtener_analisis_avanzado(sucursal_id, fecha_inicio, fecha_fin)
        analisis['success'] = True
        return JsonResponse(analisis)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': f'Error: {str(e)}'}, status=500)


@require_GET
@login_required
def listar_autorizaciones_cross_branch(request):
    """
    Lista autorizaciones cross-branch para revisión gerencial.
    """
    try:
        from .models import RegistroAutorizacion

        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ['administrador', 'jefe_local', 'administracion']:
            return JsonResponse({'success': False, 'error': 'No tiene permisos'}, status=403)

        estado = request.GET.get('estado', 'todos')
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')

        qs = RegistroAutorizacion.objects.filter(
            es_cross_branch=True, exitoso=True,
        ).select_related(
            'usuario_solicitante', 'usuario_autorizador',
            'sucursal_solicitante', 'sucursal_autorizador',
            'cambio_devolucion', 'revisado_por',
        ).order_by('-fecha_hora')

        if estado == 'pendientes':
            qs = qs.filter(requiere_revision=True, revisado_por__isnull=True)
        elif estado == 'revisados':
            qs = qs.filter(revisado_por__isnull=False)

        if fecha_desde:
            qs = qs.filter(fecha_hora__date__gte=fecha_desde)
        if fecha_hasta:
            qs = qs.filter(fecha_hora__date__lte=fecha_hasta)

        registros = []
        for r in qs[:50]:
            registros.append({
                'id': r.id,
                'fecha': r.fecha_hora.strftime('%d/%m/%Y %H:%M'),
                'usuario_solicitante': r.usuario_solicitante.get_full_name() or r.usuario_solicitante.username if r.usuario_solicitante else 'N/A',
                'usuario_autorizador': r.usuario_autorizador.get_full_name() or r.usuario_autorizador.username if r.usuario_autorizador else 'N/A',
                'sucursal_solicitante': r.sucursal_solicitante.alias if r.sucursal_solicitante else 'N/A',
                'sucursal_autorizador': r.sucursal_autorizador.alias if r.sucursal_autorizador else 'N/A',
                'tipo_operacion': r.get_tipo_operacion_display(),
                'descripcion': r.descripcion,
                'cambio_id': r.cambio_devolucion_id,
                'cambio_numero': r.cambio_devolucion.numero_operacion if r.cambio_devolucion else None,
                'requiere_revision': r.requiere_revision,
                'revisado': r.revisado_por is not None,
                'revisado_por': r.revisado_por.get_full_name() if r.revisado_por else None,
                'fecha_revision': r.fecha_revision.strftime('%d/%m/%Y %H:%M') if r.fecha_revision else None,
                'notas_revision': r.notas_revision,
            })

        pendientes = RegistroAutorizacion.objects.filter(
            es_cross_branch=True, exitoso=True,
            requiere_revision=True, revisado_por__isnull=True,
        ).count()

        return JsonResponse({
            'success': True,
            'registros': registros,
            'pendientes_revision': pendientes,
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
@csrf_exempt
def revisar_autorizacion(request, registro_id):
    """Marca una autorización cross-branch como revisada."""
    try:
        from .models import RegistroAutorizacion

        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ['administrador', 'jefe_local', 'administracion']:
            return JsonResponse({'success': False, 'error': 'No tiene permisos'}, status=403)

        registro = RegistroAutorizacion.objects.get(id=registro_id)
        data = json.loads(request.body)

        registro.revisado_por = request.user
        registro.fecha_revision = timezone.now()
        registro.notas_revision = data.get('notas', '')
        registro.save(update_fields=['revisado_por', 'fecha_revision', 'notas_revision'])

        return JsonResponse({'success': True, 'mensaje': 'Autorización marcada como revisada'})
    except RegistroAutorizacion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Registro no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@login_required
def obtener_cola_revision_gerencial(request):
    """Obtiene la cola de cambios que requieren revisión gerencial."""
    try:
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ['administrador', 'jefe_local', 'administracion']:
            return JsonResponse({'success': False, 'error': 'No tiene permisos'}, status=403)

        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')

        qs = CambioDevolucion.objects.filter(
            requiere_revision_gerencial=True,
            revisado_por_gerencia__isnull=True,
        ).select_related(
            'ticket_original', 'sucursal', 'solicitado_por', 'autorizado_por_usuario',
        ).order_by('-fecha_solicitud')

        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)

        items = []
        for c in qs[:50]:
            items.append({
                'id': c.id,
                'numero_operacion': c.numero_operacion,
                'tipo_operacion': c.get_tipo_operacion_display(),
                'estado': c.get_estado_display(),
                'monto_original': float(c.monto_original),
                'diferencia_monto': float(c.diferencia_monto),
                'es_fuera_de_plazo': c.es_fuera_de_plazo,
                'dias_fuera_de_plazo': c.dias_fuera_de_plazo,
                'es_cross_branch': c.es_autorizacion_cross_branch,
                'tipo_especial': c.tipo_cambio_especial,
                'score_riesgo': c.score_riesgo,
                'solicitado_por': c.solicitado_por.get_full_name() or c.solicitado_por.username,
                'autorizado_por': c.autorizado_por_usuario.get_full_name() if c.autorizado_por_usuario else None,
                'sucursal': c.sucursal.alias if c.sucursal else '',
                'fecha': c.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
                'ticket_original': c.ticket_original.correlativo if c.ticket_original else '',
                'motivo': c.get_motivo_principal_display(),
            })

        return JsonResponse({'success': True, 'items': items, 'total_pendientes': qs.count()})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
@csrf_exempt
def revisar_cambio_gerencial(request):
    """Marca un cambio como revisado por gerencia."""
    try:
        rol_usuario = getattr(request.user, 'rol', None)
        if rol_usuario not in ['administrador', 'jefe_local', 'administracion']:
            return JsonResponse({'success': False, 'error': 'No tiene permisos'}, status=403)

        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        notas = data.get('notas', '')

        cambio = CambioDevolucion.objects.get(id=cambio_id)
        cambio.revisado_por_gerencia = request.user
        cambio.fecha_revision_gerencia = timezone.now()
        cambio.notas_revision_gerencia = notas
        cambio.save(update_fields=['revisado_por_gerencia', 'fecha_revision_gerencia', 'notas_revision_gerencia'])

        HistorialCambioDevolucion.objects.create(
            cambio_devolucion=cambio,
            usuario=request.user,
            accion='MODIFICADO',
            estado_anterior=cambio.estado,
            estado_nuevo=cambio.estado,
            descripcion=f'Revisión gerencial completada. Notas: {notas[:200]}',
        )

        return JsonResponse({'success': True, 'mensaje': 'Cambio marcado como revisado por gerencia'})
    except CambioDevolucion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cambio no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@login_required
def exportar_cambios_devoluciones(request):
    """Exporta listado de cambios y devoluciones a Excel."""
    try:
        import io
        from django.http import HttpResponse

        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')

        qs = CambioDevolucion.objects.select_related(
            'ticket_original', 'sucursal', 'solicitado_por',
            'aprobado_por', 'autorizado_por_usuario',
        ).order_by('-fecha_solicitud')

        if fecha_desde:
            qs = qs.filter(fecha_solicitud__date__gte=fecha_desde)
        if fecha_hasta:
            qs = qs.filter(fecha_solicitud__date__lte=fecha_hasta)
        if sucursal_id:
            qs = qs.filter(sucursal_id=sucursal_id)

        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Cambios y Devoluciones"

            header_font = Font(bold=True, color="FFFFFF", size=11)
            header_fill = PatternFill(start_color="0066FF", end_color="0066FF", fill_type="solid")
            header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
            thin_border = Border(
                left=Side(style='thin'), right=Side(style='thin'),
                top=Side(style='thin'), bottom=Side(style='thin'),
            )

            headers = [
                'N Operacion', 'Fecha', 'Tipo', 'Estado', 'Tipo Especial',
                'Sucursal', 'Cliente', 'RUT', 'Ticket Original',
                'Monto Original', 'Monto Nuevo', 'Diferencia',
                'Motivo', 'Solicitado Por', 'Aprobado Por',
                'Fuera Plazo', 'Dias Fuera', 'Cross-Branch',
                'Autorizado Por', 'Score Riesgo', 'Observaciones',
            ]
            for col_num, header in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_num, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align
                cell.border = thin_border

            alert_fill = PatternFill(start_color="FFE0E0", end_color="FFE0E0", fill_type="solid")
            for row_num, cambio in enumerate(qs[:5000], 2):
                row_data = [
                    cambio.numero_operacion,
                    cambio.fecha_solicitud.strftime('%d/%m/%Y %H:%M') if cambio.fecha_solicitud else '',
                    cambio.get_tipo_operacion_display(),
                    cambio.get_estado_display(),
                    cambio.tipo_cambio_especial,
                    cambio.sucursal.alias if cambio.sucursal else '',
                    cambio.ticket_original.cliente_nombre if cambio.ticket_original else '',
                    cambio.ticket_original.cliente_rut if cambio.ticket_original else '',
                    cambio.ticket_original.correlativo if cambio.ticket_original else '',
                    float(cambio.monto_original),
                    float(cambio.monto_nuevo),
                    float(cambio.diferencia_monto),
                    cambio.get_motivo_principal_display(),
                    cambio.solicitado_por.get_full_name() if cambio.solicitado_por else '',
                    cambio.aprobado_por.get_full_name() if cambio.aprobado_por else '',
                    'Si' if cambio.es_fuera_de_plazo else 'No',
                    cambio.dias_fuera_de_plazo,
                    'Si' if cambio.es_autorizacion_cross_branch else 'No',
                    cambio.autorizado_por_usuario.get_full_name() if cambio.autorizado_por_usuario else '',
                    cambio.score_riesgo,
                    cambio.observaciones_vendedor or '',
                ]
                for col_num, value in enumerate(row_data, 1):
                    cell = ws.cell(row=row_num, column=col_num, value=value)
                    cell.border = thin_border
                    if cambio.es_fuera_de_plazo or cambio.score_riesgo >= 50:
                        cell.fill = alert_fill

            for col in ws.columns:
                max_length = 0
                for cell in col:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
                ws.column_dimensions[col[0].column_letter].width = min(max_length + 2, 40)

            buffer = io.BytesIO()
            wb.save(buffer)
            buffer.seek(0)

            response = HttpResponse(
                buffer.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = f'attachment; filename="cambios_devoluciones_{timezone.now().strftime("%Y%m%d_%H%M")}.xlsx"'
            return response

        except ImportError:
            import csv
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="cambios_devoluciones_{timezone.now().strftime("%Y%m%d_%H%M")}.csv"'
            response.write('\ufeff')
            writer = csv.writer(response)
            writer.writerow(['N Operacion', 'Fecha', 'Tipo', 'Estado', 'Sucursal', 'Monto Original', 'Diferencia', 'Motivo', 'Fuera Plazo', 'Score Riesgo'])
            for cambio in qs[:5000]:
                writer.writerow([
                    cambio.numero_operacion,
                    cambio.fecha_solicitud.strftime('%d/%m/%Y %H:%M') if cambio.fecha_solicitud else '',
                    cambio.get_tipo_operacion_display(),
                    cambio.get_estado_display(),
                    cambio.sucursal.alias if cambio.sucursal else '',
                    float(cambio.monto_original),
                    float(cambio.diferencia_monto),
                    cambio.get_motivo_principal_display(),
                    'Si' if cambio.es_fuera_de_plazo else 'No',
                    cambio.score_riesgo,
                ])
            return response

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_GET
@login_required
def obtener_estado_cuadraturas(request):
    """
    API para obtener estado de cuadraturas de caja
    Incluye: exitosas, con diferencias, pendientes
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        
        # Consultar arqueos/cuadraturas
        queryset = ArqueoCaja.objects.filter(
            fecha_arqueo__gte=fecha_inicio,
            fecha_arqueo__lte=fecha_fin
        )
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        total_cuadraturas = queryset.count()
        
        # Calcular diferencias
        cuadraturas_con_datos = []
        exitosas = 0
        con_diferencias = 0
        
        for arqueo in queryset:
            # Calcular diferencia en efectivo
            total_conteo = (
                (arqueo.billetes_20000 * 20000) +
                (arqueo.billetes_10000 * 10000) +
                (arqueo.billetes_5000 * 5000) +
                (arqueo.billetes_2000 * 2000) +
                (arqueo.billetes_1000 * 1000) +
                (arqueo.monedas_500 * 500) +
                (arqueo.monedas_100 * 100) +
                (arqueo.monedas_50 * 50) +
                (arqueo.monedas_10 * 10)
            )
            
            diferencia = total_conteo - arqueo.total_efectivo_teorico
            
            cuadraturas_con_datos.append({
                'id': arqueo.id,
                'fecha': arqueo.fecha_arqueo,
                'diferencia': abs(diferencia)
            })
            
            if abs(diferencia) <= 1000:  # Tolerancia de $1000
                exitosas += 1
            else:
                con_diferencias += 1
        
        # Cuadraturas pendientes (días sin cuadratura)
        dias_periodo = (fecha_fin - fecha_inicio).days + 1
        pendientes = max(0, dias_periodo - total_cuadraturas)
        
        # Calcular diferencia total y promedio
        diferencia_total = sum(c['diferencia'] for c in cuadraturas_con_datos)
        promedio_diferencia = diferencia_total / len(cuadraturas_con_datos) if cuadraturas_con_datos else 0
        
        return JsonResponse({
            'success': True,
            'exitosas': exitosas,
            'con_diferencias': con_diferencias,
            'pendientes': pendientes,
            'total': total_cuadraturas,
            'diferencia_total': float(diferencia_total),
            'promedio_diferencia': float(promedio_diferencia)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener estado de cuadraturas: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_productos_mas_vendidos(request):
    """
    API para obtener los productos más vendidos
    Incluye: cantidades, montos, participación
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        estado = request.GET.get('estado', '')  # Vacío por defecto
        limite = int(request.GET.get('limite', 20))
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            try:
                fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Formato de fecha inválido'
                }, status=400)
        
        # Construir queryset base de tickets
        tickets = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        
        # Solo aplicar filtro de estado si tiene valor, por defecto mostrar solo PAGADO si no se especifica
        if estado:
            tickets = tickets.filter(estado=estado)
        else:
            tickets = tickets.filter(estado='PAGADO')  # Por defecto solo PAGADO para productos vendidos
        
        if sucursal_id:
            tickets = tickets.filter(sucursal_id=sucursal_id)
        
        # Obtener IDs de tickets
        ticket_ids = tickets.values_list('id', flat=True)
        
        # Consultar productos vendidos
        productos_vendidos = Ticket_Productos.objects.filter(
            idTicket_id__in=ticket_ids
        ).values(
            'ProductoTalla__sku',
            'ProductoTalla__producto__articulo',
            'ProductoTalla__producto__descripcion',
            'ProductoTalla__producto__categoria__nombre'
        ).annotate(
            cantidad_vendida=Sum('stock'),
            total_ventas=Sum(
                ExpressionWrapper(
                    F('stock') * F('precio'),
                    output_field=DecimalField()
                )
            )
        ).order_by('-cantidad_vendida')[:limite]
        
        # Calcular total general para participación
        total_general = sum(float(p['total_ventas'] or 0) for p in productos_vendidos)
        
        productos_data = []
        for producto in productos_vendidos:
            cantidad = producto['cantidad_vendida'] or 0
            total_ventas = float(producto['total_ventas'] or 0)
            precio_promedio = total_ventas / cantidad if cantidad > 0 else 0
            participacion = (total_ventas / total_general * 100) if total_general > 0 else 0
            
            productos_data.append({
                'sku': producto['ProductoTalla__sku'],
                'nombre': producto['ProductoTalla__producto__articulo'] or 'Sin nombre',
                'descripcion': producto['ProductoTalla__producto__descripcion'] or '',
                'categoria': producto['ProductoTalla__producto__categoria__nombre'] or 'Sin categoría',
                'cantidad': cantidad,
                'total_ventas': total_ventas,
                'precio_promedio': precio_promedio,
                'participacion': float(participacion)
            })
        
        return JsonResponse({
            'success': True,
            'productos': productos_data,
            'total_productos': len(productos_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener productos más vendidos: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_tendencias_ventas(request):
    """
    API para obtener tendencias de ventas
    Incluye: ventas por hora, día de la semana, evolución temporal
    """
    try:
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        estado = request.GET.get('estado', '')  # Vacío por defecto
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            try:
                fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
                fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'Formato de fecha inválido'
                }, status=400)
        
        # Construir queryset base
        queryset = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        
        # Solo aplicar filtro de estado si tiene valor, por defecto mostrar solo PAGADO
        if estado:
            queryset = queryset.filter(estado=estado)
        else:
            queryset = queryset.filter(estado='PAGADO')  # Por defecto solo PAGADO para tendencias
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Ventas por hora del día
        ventas_por_hora = [0] * 24
        for ticket in queryset:
            if ticket.hora:
                hora = ticket.hora.hour
                ventas_por_hora[hora] += ticket.total
        
        por_hora_data = [
            {'hora': i, 'total': float(ventas_por_hora[i])}
            for i in range(24)
        ]
        
        # Ventas por día de la semana (0=Lunes, 6=Domingo)
        ventas_por_dia = [0] * 7
        for ticket in queryset:
            dia_semana = ticket.fecha.weekday()
            ventas_por_dia[dia_semana] += ticket.total
        
        return JsonResponse({
            'success': True,
            'por_hora': por_hora_data,
            'por_dia_semana': [float(x) for x in ventas_por_dia]
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener tendencias de ventas: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_indicadores_avanzados_ventas(request):
    """
    API para obtener indicadores avanzados de retail con datos reales.
    Calcula: Margen Bruto (FIFO), Sell-Through Rate, Rotacion, Dias de Stock,
    GMROI, Descuento Promedio, Costo de Ventas real.
    """
    try:
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        estado = request.GET.get('estado', '')

        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()

        tickets_qs = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        if estado:
            tickets_qs = tickets_qs.filter(estado=estado)
        else:
            tickets_qs = tickets_qs.filter(estado='PAGADO')
        if sucursal_id:
            tickets_qs = tickets_qs.filter(sucursal_id=sucursal_id)

        ticket_ids = tickets_qs.values_list('id', flat=True)

        lineas = Ticket_Productos.objects.filter(idTicket_id__in=ticket_ids)

        agg = lineas.aggregate(
            ingresos=Sum(ExpressionWrapper(F('stock') * F('precio'), output_field=DecimalField())),
            costo_ventas=Sum(ExpressionWrapper(F('stock') * F('costo_fifo'), output_field=DecimalField())),
            unidades_vendidas=Sum('stock'),
            descuento_prom=Avg('porcentaje_descuento'),
            descuento_total_monto=Sum(ExpressionWrapper(F('stock') * F('descuento_unitario'), output_field=DecimalField())),
        )

        ingresos = float(agg['ingresos'] or 0)
        costo_ventas = float(agg['costo_ventas'] or 0)
        unidades_vendidas = int(agg['unidades_vendidas'] or 0)
        descuento_promedio = float(agg['descuento_prom'] or 0)
        descuento_total_monto = float(agg['descuento_total_monto'] or 0)

        margen_bruto = ingresos - costo_ventas
        margen_pct = (margen_bruto / ingresos * 100) if ingresos > 0 else 0

        stock_filter = {}
        if sucursal_id:
            stock_filter['producto__sucursal_id'] = sucursal_id
        stock_actual = Producto_Talla.objects.filter(
            stock__gt=0, **stock_filter
        ).aggregate(
            total_unidades=Sum('stock'),
        )
        stock_total_unidades = int(stock_actual['total_unidades'] or 0)

        sell_through = 0
        if (unidades_vendidas + stock_total_unidades) > 0:
            sell_through = (unidades_vendidas / (unidades_vendidas + stock_total_unidades)) * 100

        dias_periodo = max(1, (fecha_fin - fecha_inicio).days + 1)

        if stock_total_unidades > 0 and unidades_vendidas > 0:
            venta_diaria = unidades_vendidas / dias_periodo
            dias_stock = stock_total_unidades / venta_diaria
            rotacion_periodo = unidades_vendidas / stock_total_unidades
            rotacion_anualizada = rotacion_periodo * (365 / dias_periodo)
        else:
            dias_stock = 0
            rotacion_periodo = 0
            rotacion_anualizada = 0

        inventario_costo_est = stock_total_unidades * (costo_ventas / unidades_vendidas) if unidades_vendidas > 0 else 0
        gmroi = (margen_bruto / inventario_costo_est) if inventario_costo_est > 0 else 0

        return JsonResponse({
            'success': True,
            'ingresos': ingresos,
            'costo_ventas': costo_ventas,
            'margen_bruto': margen_bruto,
            'margen_pct': round(margen_pct, 2),
            'unidades_vendidas': unidades_vendidas,
            'stock_actual': stock_total_unidades,
            'sell_through': round(sell_through, 2),
            'rotacion_periodo': round(rotacion_periodo, 2),
            'rotacion_anualizada': round(rotacion_anualizada, 2),
            'dias_stock': round(dias_stock, 1),
            'gmroi': round(gmroi, 2),
            'descuento_promedio': round(descuento_promedio, 2),
            'descuento_total_monto': descuento_total_monto,
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener indicadores avanzados: {str(e)}'
        }, status=500)


@require_GET
@login_required
def obtener_estado_operacional_ventas(request):
    """
    API para obtener el estado operacional completo del modulo de ventas.
    Cubre: tickets por estado, ventas por modulo, POS, cambios/devoluciones,
    depositos, DTEs pendientes, regularizaciones.
    """
    try:
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')

        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()

        tickets_qs = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin
        )
        if sucursal_id:
            tickets_qs = tickets_qs.filter(sucursal_id=sucursal_id)

        # --- Tickets por estado ---
        tickets_por_estado = list(
            tickets_qs.values('estado').annotate(
                cantidad=Count('id'),
                monto=Sum('total')
            ).order_by('estado')
        )
        total_tickets = tickets_qs.count()
        anulados = tickets_qs.filter(estado='ANULADO').count()
        pct_anulados = (anulados / total_tickets * 100) if total_tickets > 0 else 0

        pendientes_pago = tickets_qs.filter(estado='PENDIENTE')
        pendientes_count = pendientes_pago.count()
        pendientes_monto = float(pendientes_pago.aggregate(t=Sum('total'))['t'] or 0)

        # --- Ventas por modulo de origen ---
        por_modulo = list(
            tickets_qs.values('modulo_origen').annotate(
                cantidad=Count('id'),
                monto=Sum('total')
            ).order_by('-monto')
        )
        for item in por_modulo:
            item['monto'] = float(item['monto'] or 0)

        # --- Ventas por tipo DTE ---
        por_tipo_dte = list(
            tickets_qs.exclude(tipo_dte__isnull=True).values('tipo_dte').annotate(
                cantidad=Count('id')
            ).order_by('-cantidad')
        )

        # --- Tickets offline ---
        tickets_offline = tickets_qs.filter(created_offline=True).count()

        # --- Transacciones POS ---
        pos_qs = TransaccionPOS.objects.filter(
            fecha_inicio__date__gte=fecha_inicio,
            fecha_inicio__date__lte=fecha_fin
        )
        if sucursal_id:
            pos_qs = pos_qs.filter(configuracion_pos__sucursal_id=sucursal_id)

        pos_por_estado = list(
            pos_qs.values('estado').annotate(
                cantidad=Count('id'),
                monto=Sum('monto')
            ).order_by('estado')
        )
        for item in pos_por_estado:
            item['monto'] = float(item['monto'] or 0)

        pos_total = pos_qs.count()
        pos_completadas = pos_qs.filter(estado='COMPLETADA').count()
        pos_tasa_exito = (pos_completadas / pos_total * 100) if pos_total > 0 else 0

        # --- Cambios y Devoluciones ---
        cambios_qs = CambioDevolucion.objects.filter(
            fecha_solicitud__date__gte=fecha_inicio,
            fecha_solicitud__date__lte=fecha_fin
        )
        if sucursal_id:
            cambios_qs = cambios_qs.filter(sucursal_id=sucursal_id)

        cambios_por_estado = list(
            cambios_qs.values('estado').annotate(
                cantidad=Count('id'),
                monto=Sum('monto_original')
            ).order_by('estado')
        )
        for item in cambios_por_estado:
            item['monto'] = float(item['monto'] or 0)
            item['estado_display'] = dict(ESTADO_CAMBIO_CHOICES).get(item['estado'], item['estado'])

        cambios_por_tipo = list(
            cambios_qs.values('tipo_operacion').annotate(
                cantidad=Count('id'),
                monto=Sum('monto_original')
            ).order_by('-cantidad')
        )
        for item in cambios_por_tipo:
            item['monto'] = float(item['monto'] or 0)
            item['tipo_display'] = dict(TIPO_OPERACION_CAMBIO_CHOICES).get(item['tipo_operacion'], item['tipo_operacion'])

        motivos = list(
            cambios_qs.filter(motivo_principal__isnull=False)
            .values('motivo_principal')
            .annotate(cantidad=Count('id'))
            .order_by('-cantidad')[:5]
        )
        for item in motivos:
            item['motivo_display'] = dict(MOTIVO_CAMBIO_CHOICES).get(item['motivo_principal'], item['motivo_principal'])

        cambios_monto_total = float(cambios_qs.aggregate(t=Sum('monto_original'))['t'] or 0)
        cambios_pendientes_aprobacion = cambios_qs.filter(estado__in=['SOLICITADO', 'EN_PROCESO']).count()

        # --- Depositos Bancarios ---
        depositos_qs = DepositoBancario.objects.filter(
            fecha_deposito__gte=fecha_inicio,
            fecha_deposito__lte=fecha_fin
        )
        if sucursal_id:
            depositos_qs = depositos_qs.filter(arqueo__sucursal_id=sucursal_id)

        depositos_verificados = depositos_qs.filter(verificado=True).count()
        depositos_pendientes = depositos_qs.filter(verificado=False).count()
        depositos_monto_verificado = float(
            depositos_qs.filter(verificado=True).aggregate(t=Sum('monto'))['t'] or 0
        )
        depositos_monto_pendiente = float(
            depositos_qs.filter(verificado=False).aggregate(t=Sum('monto'))['t'] or 0
        )

        # --- DTEs pendientes ---
        dtes_pendientes = Dte.objects.filter(
            estado_dte='EMITIDO',
            tipo_transaccion='TRASPASO'
        ).count()

        # --- Regularizaciones pendientes ---
        from .models import Solicitud_Regularizacion
        regularizaciones_pendientes = Solicitud_Regularizacion.objects.filter(
            estado__in=['PENDIENTE', 'EN_REVISION']
        ).count()

        return JsonResponse({
            'success': True,
            'tickets': {
                'por_estado': [{
                    'estado': t['estado'],
                    'cantidad': t['cantidad'],
                    'monto': float(t['monto'] or 0)
                } for t in tickets_por_estado],
                'total': total_tickets,
                'anulados': anulados,
                'pct_anulados': round(pct_anulados, 2),
                'pendientes_pago': pendientes_count,
                'pendientes_monto': pendientes_monto,
            },
            'modulo_origen': por_modulo,
            'tipo_dte': por_tipo_dte,
            'tickets_offline': tickets_offline,
            'pos': {
                'por_estado': pos_por_estado,
                'total': pos_total,
                'completadas': pos_completadas,
                'tasa_exito': round(pos_tasa_exito, 2),
            },
            'cambios_devoluciones': {
                'por_estado': cambios_por_estado,
                'por_tipo': cambios_por_tipo,
                'motivos_principales': motivos,
                'monto_total': cambios_monto_total,
                'pendientes_aprobacion': cambios_pendientes_aprobacion,
            },
            'depositos': {
                'verificados': depositos_verificados,
                'pendientes': depositos_pendientes,
                'monto_verificado': depositos_monto_verificado,
                'monto_pendiente': depositos_monto_pendiente,
            },
            'dtes_pendientes': dtes_pendientes,
            'regularizaciones_pendientes': regularizaciones_pendientes,
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener estado operacional: {str(e)}'
        }, status=500)


@require_GET
@login_required
def exportar_dashboard_ventas_excel(request):
    """
    API para exportar dashboard de ventas a Excel
    Incluye todas las métricas e indicadores
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
        
        # Obtener parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        sucursal_id = request.GET.get('sucursal_id')
        
        # Validar fechas
        if not fecha_inicio or not fecha_fin:
            fecha_fin = timezone.localdate()
            fecha_inicio = fecha_fin - timedelta(days=30)
        else:
            fecha_inicio = timezone.datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
            fecha_fin = timezone.datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        
        # Crear workbook
        wb = Workbook()
        
        # Estilos
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF")
        title_font = Font(bold=True, size=14)
        
        # ===== HOJA 1: RESUMEN EJECUTIVO =====
        ws1 = wb.active
        ws1.title = "Resumen Ejecutivo"
        
        ws1['A1'] = "DASHBOARD DE VENTAS - RESUMEN EJECUTIVO"
        ws1['A1'].font = title_font
        ws1['A2'] = f"Período: {fecha_inicio.strftime('%d/%m/%Y')} - {fecha_fin.strftime('%d/%m/%Y')}"
        
        # Obtener datos de indicadores globales
        queryset = Ticket.objects.filter(
            fecha__gte=fecha_inicio,
            fecha__lte=fecha_fin,
            estado='PAGADO'
        )
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        ventas_totales = queryset.aggregate(total=Sum('total'))['total'] or 0
        cantidad_ventas = queryset.count()
        ticket_promedio = ventas_totales / cantidad_ventas if cantidad_ventas > 0 else 0
        
        ws1['A4'] = "INDICADORES PRINCIPALES"
        ws1['A4'].font = header_font
        ws1['A4'].fill = header_fill
        
        ws1['A5'] = "Ventas Totales"
        ws1['B5'] = f"${ventas_totales:,.0f}"
        ws1['A6'] = "Cantidad de Ventas"
        ws1['B6'] = cantidad_ventas
        ws1['A7'] = "Ticket Promedio"
        ws1['B7'] = f"${ticket_promedio:,.0f}"
        
        # ===== HOJA 2: VENTAS POR VENDEDOR =====
        ws2 = wb.create_sheet("Ventas por Vendedor")
        
        headers_vendedor = ["Código", "Vendedor", "Cant. Ventas", "Total Vendido", 
                           "Ticket Promedio", "Comisión %", "Comisión Total", "% Participación"]
        
        for col, header in enumerate(headers_vendedor, 1):
            cell = ws2.cell(1, col, header)
            cell.font = header_font
            cell.fill = header_fill
        
        ventas_vendedor = queryset.values(
            'vendedor__codigo_vendedor',
            'vendedor__nombre',
            'vendedor__comision'
        ).annotate(
            total_vendido=Sum('total'),
            cantidad_ventas=Count('id'),
            ticket_promedio=Avg('total')
        ).order_by('-total_vendido')
        
        row = 2
        for venta in ventas_vendedor:
            total_vendido = float(venta['total_vendido'] or 0)
            comision_porcentaje = float(venta['vendedor__comision'] or 0)
            comision_total = total_vendido * (comision_porcentaje / 100)
            participacion = (total_vendido / ventas_totales * 100) if ventas_totales > 0 else 0
            
            ws2.cell(row, 1, venta['vendedor__codigo_vendedor'])
            ws2.cell(row, 2, venta['vendedor__nombre'])
            ws2.cell(row, 3, venta['cantidad_ventas'])
            ws2.cell(row, 4, f"${total_vendido:,.0f}")
            ws2.cell(row, 5, f"${float(venta['ticket_promedio']):,.0f}")
            ws2.cell(row, 6, f"{comision_porcentaje:.2f}%")
            ws2.cell(row, 7, f"${comision_total:,.0f}")
            ws2.cell(row, 8, f"{participacion:.2f}%")
            row += 1
        
        # ===== HOJA 3: PRODUCTOS MÁS VENDIDOS =====
        ws3 = wb.create_sheet("Productos Más Vendidos")
        
        headers_productos = ["#", "SKU", "Producto", "Categoría", "Cantidad", "Total Ventas", 
                            "Precio Promedio", "% Participación"]
        
        for col, header in enumerate(headers_productos, 1):
            cell = ws3.cell(1, col, header)
            cell.font = header_font
            cell.fill = header_fill
        
        ticket_ids = queryset.values_list('id', flat=True)
        
        productos_vendidos = Ticket_Productos.objects.filter(
            idTicket_id__in=ticket_ids
        ).values(
            'ProductoTalla__sku',
            'ProductoTalla__producto__articulo',
            'ProductoTalla__producto__categoria__nombre'
        ).annotate(
            cantidad_vendida=Sum('stock'),
            total_ventas=Sum(
                ExpressionWrapper(
                    F('stock') * F('precio'),
                    output_field=DecimalField()
                )
            )
        ).order_by('-cantidad_vendida')[:50]
        
        total_productos = sum(float(p['total_ventas'] or 0) for p in productos_vendidos)
        
        row = 2
        for idx, producto in enumerate(productos_vendidos, 1):
            total_ventas_prod = float(producto['total_ventas'] or 0)
            cantidad = producto['cantidad_vendida'] or 0
            precio_prom = total_ventas_prod / cantidad if cantidad > 0 else 0
            participacion = (total_ventas_prod / total_productos * 100) if total_productos > 0 else 0
            
            ws3.cell(row, 1, idx)
            ws3.cell(row, 2, producto['ProductoTalla__sku'])
            ws3.cell(row, 3, producto['ProductoTalla__producto__articulo'])
            ws3.cell(row, 4, producto['ProductoTalla__producto__categoria__nombre'] or 'Sin categoría')
            ws3.cell(row, 5, cantidad)
            ws3.cell(row, 6, f"${total_ventas_prod:,.0f}")
            ws3.cell(row, 7, f"${precio_prom:,.0f}")
            ws3.cell(row, 8, f"{participacion:.2f}%")
            row += 1
        
        # Ajustar ancho de columnas
        for ws in [ws1, ws2, ws3]:
            for col in range(1, 10):
                ws.column_dimensions[get_column_letter(col)].width = 15
        
        # Generar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="Dashboard_Ventas_{fecha_inicio}_{fecha_fin}.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar dashboard: {str(e)}'
        }, status=500)


# ========== NOTA DE CRÉDITO DESDE DEVOLUCIONES ==========

@login_required
@require_POST
@transaction.atomic
def generar_nc_devolucion(request):
    """
    Genera una Nota de Crédito (NC) a partir de un CambioDevolucion completado.
    La NC se vincula al DTE original del ticket y afecta la cuadratura de caja
    según el método de devolución elegido (efectivo caja o transferencia bancaria).
    """
    from datetime import date
    from decimal import Decimal
    from collections import defaultdict
    from .views_modulo_documentos import generar_txt_nota_credito_acepta, limpiar_texto
    import logging
    logger = logging.getLogger(__name__)

    try:
        body = json.loads(request.body)
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)

    cambio_id = body.get('cambio_devolucion_id')
    metodo_devolucion = body.get('metodo_devolucion')

    if not cambio_id:
        return JsonResponse({'success': False, 'error': 'ID de cambio/devolución requerido'}, status=400)
    if metodo_devolucion not in ('EFECTIVO_CAJA', 'TRANSFERENCIA_BANCARIA'):
        return JsonResponse({'success': False, 'error': 'Método de devolución inválido. Use EFECTIVO_CAJA o TRANSFERENCIA_BANCARIA'}, status=400)

    # Validar permisos: solo admin, administracion, jefe_local
    try:
        empresa_user = EmpresaUser.objects.get(user=request.user, active=True)
    except EmpresaUser.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Usuario no tiene empresa asignada'}, status=403)

    rol = getattr(empresa_user, 'rol', None) or getattr(request.user, 'rol', '')
    if rol not in ('administrador', 'administracion', 'jefe_local'):
        return JsonResponse({'success': False, 'error': 'No tiene permisos para generar Notas de Crédito'}, status=403)

    # Obtener CambioDevolucion
    try:
        cambio = CambioDevolucion.objects.select_related(
            'ticket_original', 'sucursal'
        ).prefetch_related('detalles__producto_original__ProductoTalla__producto').get(id=cambio_id)
    except CambioDevolucion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cambio/Devolución no encontrado'}, status=404)

    # Validar estado
    estados_validos = ('COMPLETADO', 'EJECUTADO_DEVOL_PENDIENTE', 'EJECUTADO', 'EJECUTADO_COBRO_PENDIENTE')
    if cambio.estado not in estados_validos:
        return JsonResponse({
            'success': False,
            'error': f'El cambio/devolución debe estar en estado completado o ejecutado. Estado actual: {cambio.get_estado_display()}'
        }, status=400)

    # Validar tipo de operación (solo devoluciones)
    tipos_devolucion = ('DEVOLUCION_TOTAL', 'DEVOLUCION_PARCIAL')
    if cambio.tipo_operacion not in tipos_devolucion:
        return JsonResponse({
            'success': False,
            'error': 'Solo se puede generar NC para devoluciones (total o parcial)'
        }, status=400)

    # Validar que no tenga NC generada
    if cambio.nc_generada:
        return JsonResponse({
            'success': False,
            'error': 'Ya se generó una Nota de Crédito para esta devolución',
            'nota_credito_id': cambio.nota_credito_id
        }, status=400)

    # Validar sucursal del usuario
    sucursal_id = request.session.get('idSucursalActual')
    if cambio.sucursal_id != int(sucursal_id):
        return JsonResponse({'success': False, 'error': 'El cambio/devolución no pertenece a su sucursal actual'}, status=403)

    # Buscar DTE original del ticket
    ticket_original = cambio.ticket_original
    dte_original = None

    if ticket_original.folio_dte:
        dte_original = Dte.objects.filter(
            numero_documento=ticket_original.folio_dte,
            sucursal=cambio.sucursal,
            tipo_documento__in=['BOLETA ELECTRONICA', 'FACTURA ELECTRONICA', 'BOLETA PAPEL'],
            estado_dte__in=['EMITIDO', 'ACEPTADO']
        ).first()

    if not dte_original:
        dte_original = Dte.objects.filter(
            sucursal=cambio.sucursal,
            tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO'],
            tipo_documento__in=['BOLETA ELECTRONICA', 'FACTURA ELECTRONICA', 'BOLETA PAPEL'],
            estado_dte__in=['EMITIDO', 'ACEPTADO'],
            fecha_emision=ticket_original.fecha
        ).order_by('-id').first()

    empresa_id = request.session.get('idEmpresaActual')
    empresa = Empresa.objects.get(id=empresa_id)

    # Calcular monto de la NC según los detalles de la devolución
    detalles = cambio.detalles.all()
    monto_devolucion = abs(cambio.diferencia_monto) if cambio.diferencia_monto < 0 else cambio.monto_original

    if cambio.tipo_operacion == 'DEVOLUCION_PARCIAL':
        monto_devolucion = sum(
            abs(d.precio_original_unitario * d.cantidad_original)
            for d in detalles if d.cantidad_original > 0
        )

    monto_neto = int(round(monto_devolucion / Decimal('1.19')))
    iva = int(monto_devolucion) - monto_neto
    monto_con_iva = int(monto_devolucion)

    # Obtener correlativo para NC
    numero_nc = obtener_siguiente_correlativo(cambio.sucursal, 'NOTA DE CREDITO')

    # Determinar tipo SII del documento original para la referencia
    tipo_sii_original = 39  # boleta por defecto
    folio_original = ''
    fecha_original = timezone.localdate().strftime('%Y-%m-%d')

    if dte_original:
        if 'FACTURA' in dte_original.tipo_documento:
            tipo_sii_original = 33
        elif 'BOLETA' in dte_original.tipo_documento:
            tipo_sii_original = 39
        folio_original = str(dte_original.numero_documento)
        fecha_original = dte_original.fecha_emision.strftime('%Y-%m-%d')
    elif ticket_original.folio_dte:
        folio_original = str(ticket_original.folio_dte)
        fecha_original = ticket_original.fecha.strftime('%Y-%m-%d')

    referencias_json = json.dumps([{
        'tipo_documento': tipo_sii_original,
        'folio': folio_original,
        'fecha': fecha_original,
        'razon': '1'
    }])

    # Crear el DTE tipo NC
    nc = Dte.objects.create(
        emisor=empresa,
        receptor=dte_original.receptor if dte_original and dte_original.receptor else None,
        tipo_documento='NOTA DE CREDITO',
        numero_documento=numero_nc,
        monto_neto=monto_neto,
        monto_con_iva=monto_con_iva,
        descuento=0,
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=0,
        unidades_productos=sum(d.cantidad_original for d in detalles if d.cantidad_original > 0),
        estado_dte='EMITIDO',
        estado_pago='PAGADO',
        tipo_transaccion='DEVOLUCION',
        responsable=request.user.username,
        sucursal=cambio.sucursal,
        hora=timezone.localtime().time(),
        es_nota_credito=True,
        documento_afectado=dte_original,
        motivo_nc=f"Devolución {cambio.get_tipo_operacion_display()} - {cambio.numero_operacion}. Motivo: {cambio.get_motivo_principal_display()}",
        referencias=referencias_json,
    )

    # Crear líneas de productos en la NC
    for detalle in detalles:
        if detalle.cantidad_original <= 0:
            continue
        if detalle.producto_original and detalle.producto_original.ProductoTalla:
            pt = detalle.producto_original.ProductoTalla
            Dte_Productos.objects.create(
                dte=nc,
                productoTalla=pt,
                descripcion=pt.producto.articulo if pt.producto else '',
                costo=0,
                sobreprecio=0,
                precio=detalle.precio_original_unitario,
                stock=detalle.cantidad_original,
                activo=True
            )

    # Crear detalle de pago de la NC según método de devolución
    metodo_pago_nc = 'EFECTIVO' if metodo_devolucion == 'EFECTIVO_CAJA' else 'TRANSFERENCIA'
    Dte_Detalle_Pago.objects.create(
        dte=nc,
        metodo_pago=metodo_pago_nc,
        monto=monto_con_iva,
    )

    # Actualizar CambioDevolucion
    cambio.nota_credito = nc
    cambio.nc_generada = True
    cambio.metodo_devolucion = metodo_devolucion
    cambio.fecha_nc = timezone.now()
    cambio.save(update_fields=['nota_credito', 'nc_generada', 'metodo_devolucion', 'fecha_nc'])

    # Registrar en historial
    HistorialCambioDevolucion.objects.create(
        cambio_devolucion=cambio,
        accion='NC_GENERADA',
        estado_anterior=cambio.estado,
        estado_nuevo=cambio.estado,
        usuario=request.user,
        descripcion=f"Nota de Crédito #{numero_nc} generada. Método: {cambio.get_metodo_devolucion_display()}. Monto: ${monto_con_iva:,}",
        datos_adicionales={
            'nc_id': nc.id,
            'nc_numero': numero_nc,
            'metodo_devolucion': metodo_devolucion,
            'monto_nc': monto_con_iva,
            'dte_original_id': dte_original.id if dte_original else None,
        }
    )

    # Generar TXT Acepta
    contenido_txt = None
    nombre_archivo = f"NC_61_{numero_nc}_{nc.fecha_emision.strftime('%Y%m%d')}.txt"
    try:
        productos_agrupados = defaultdict(lambda: {
            'tallas': [], 'cantidad_total': 0, 'precio': 0,
            'monto_total': 0, 'articulo': '', 'marca': '', 'color': ''
        })
        for dp in nc.dte_productos.select_related('productoTalla__producto'):
            producto = dp.productoTalla.producto
            key = producto.articulo
            g = productos_agrupados[key]
            talla_nombre = str(dp.productoTalla.talla) if hasattr(dp.productoTalla, 'talla') and dp.productoTalla.talla else 'U'
            g['tallas'].append(f"{dp.stock}:{talla_nombre}")
            g['cantidad_total'] += dp.stock
            g['precio'] = dp.precio
            g['monto_total'] += dp.stock * dp.precio
            g['articulo'] = producto.articulo
            if not g['marca'] and producto.atributo1:
                g['marca'] = producto.atributo1.valor
            if not g['color'] and producto.atributo2:
                g['color'] = producto.atributo2.valor

        detalle_txt = []
        for articulo, g in productos_agrupados.items():
            tallas_str = ' '.join(g['tallas'])
            marca_limpia = limpiar_texto(g['marca'] or '')
            color_limpio = limpiar_texto(g['color'] or '')
            marca_color = f"{marca_limpia} {color_limpio}".strip()
            nombre_final = f"{marca_color} {tallas_str}".strip() if marca_color else tallas_str
            detalle_txt.append({
                'nombre': limpiar_texto(nombre_final),
                'descripcion': '',
                'cantidad': g['cantidad_total'],
                'unidad': 'UN',
                'precio_unitario': g['precio'],
                'descuento_pct': 0,
                'monto_descuento': 0,
                'monto_item': g['monto_total'],
                'codigo': limpiar_texto(g['articulo'])
            })

        referencias_nc = json.loads(nc.referencias) if isinstance(nc.referencias, str) else []

        datos_txt = {
            'documento': {
                'tipo_documento': 61,
                'folio': nc.numero_documento,
                'fecha_emision': nc.fecha_emision.strftime('%Y-%m-%d'),
                'fecha_vencimiento': nc.fecha_vencimiento.strftime('%Y-%m-%d'),
                'forma_pago': 1,
                'timestamp': timezone.now().strftime('%Y-%m-%dT%H:%M:%S')
            },
            'emisor': {
                'rut': empresa.rut,
                'razon_social': limpiar_texto(empresa.razon_social or ''),
                'giro': limpiar_texto(empresa.giro or ''),
                'acteco': empresa.acteco or '',
                'direccion': limpiar_texto(cambio.sucursal.direccion if cambio.sucursal else empresa.direccion or ''),
                'comuna': limpiar_texto(empresa.comuna or ''),
                'ciudad': limpiar_texto(empresa.ciudad or ''),
                'codigo_vendedor': limpiar_texto(request.user.username or 'USUARIO'),
                'sucursal': limpiar_texto(cambio.sucursal.alias if cambio.sucursal else ''),
                'telefono': empresa.contacto1 or '',
            },
            'receptor': {
                'rut': nc.receptor.rut if nc.receptor else '66666666-6',
                'razon_social': limpiar_texto(nc.receptor.razon_social if nc.receptor else 'CONSUMIDOR FINAL'),
                'giro': limpiar_texto(nc.receptor.giro if nc.receptor else ''),
                'direccion': limpiar_texto(nc.receptor.direccion if nc.receptor else ''),
                'comuna': limpiar_texto(nc.receptor.comuna if nc.receptor else ''),
                'ciudad': limpiar_texto(nc.receptor.ciudad if nc.receptor else ''),
            },
            'totales': {
                'monto_neto': monto_neto,
                'monto_exento': 0,
                'tasa_iva': 19,
                'iva': iva,
                'monto_total': monto_con_iva,
                'descuento_global': 0
            },
            'detalle': detalle_txt,
            'referencias': referencias_nc
        }

        contenido_txt = generar_txt_nota_credito_acepta(datos_txt)

        import os
        ruta_nc = os.path.join('MEDIA', 'documentos_electronicos', 'nc')
        os.makedirs(ruta_nc, exist_ok=True)
        with open(os.path.join(ruta_nc, nombre_archivo), 'w', encoding='utf-8') as f:
            f.write(contenido_txt)

    except Exception as e:
        logger.error(f"Error generando TXT Acepta para NC #{numero_nc}: {e}")
        contenido_txt = None

    return JsonResponse({
        'success': True,
        'message': f'Nota de Crédito #{numero_nc} generada exitosamente',
        'data': {
            'nc_id': nc.id,
            'nc_numero': numero_nc,
            'nc_monto': monto_con_iva,
            'metodo_devolucion': metodo_devolucion,
            'metodo_devolucion_display': dict(METODO_DEVOLUCION_NC_CHOICES).get(metodo_devolucion, ''),
            'cambio_id': cambio.id,
            'numero_operacion': cambio.numero_operacion,
            'txt_generado': contenido_txt is not None,
            'nombre_archivo_txt': nombre_archivo if contenido_txt else None,
        }
    })


@login_required
@require_GET
def detalle_nc_devolucion(request, cambio_id):
    """
    Retorna el detalle de la Nota de Crédito asociada a un CambioDevolucion.
    """
    try:
        cambio = CambioDevolucion.objects.select_related(
            'nota_credito', 'nota_credito__emisor', 'nota_credito__receptor',
            'ticket_original', 'sucursal'
        ).get(id=cambio_id)
    except CambioDevolucion.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cambio/Devolución no encontrado'}, status=404)

    sucursal_id = request.session.get('idSucursalActual')
    if cambio.sucursal_id != int(sucursal_id):
        return JsonResponse({'success': False, 'error': 'Sin acceso a esta operación'}, status=403)

    if not cambio.nc_generada or not cambio.nota_credito:
        return JsonResponse({
            'success': True,
            'nc_generada': False,
            'data': None
        })

    nc = cambio.nota_credito
    productos_nc = []
    for dp in nc.dte_productos.select_related('productoTalla__producto'):
        producto = dp.productoTalla.producto if dp.productoTalla else None
        productos_nc.append({
            'articulo': producto.articulo if producto else dp.descripcion,
            'talla': str(dp.productoTalla.talla) if dp.productoTalla and hasattr(dp.productoTalla, 'talla') else '',
            'cantidad': dp.stock,
            'precio_unitario': float(dp.precio),
            'subtotal': float(dp.precio * dp.stock),
        })

    return JsonResponse({
        'success': True,
        'nc_generada': True,
        'data': {
            'nc_id': nc.id,
            'nc_numero': nc.numero_documento,
            'nc_fecha': nc.fecha_emision.strftime('%d/%m/%Y'),
            'nc_monto_neto': float(nc.monto_neto),
            'nc_iva': float(nc.monto_con_iva - nc.monto_neto),
            'nc_monto_total': float(nc.monto_con_iva),
            'nc_estado': nc.estado_dte,
            'metodo_devolucion': cambio.metodo_devolucion,
            'metodo_devolucion_display': cambio.get_metodo_devolucion_display(),
            'fecha_nc': cambio.fecha_nc.strftime('%d/%m/%Y %H:%M') if cambio.fecha_nc else None,
            'numero_operacion': cambio.numero_operacion,
            'motivo': nc.motivo_nc,
            'productos': productos_nc,
            'documento_afectado': nc.documento_afectado.numero_documento if nc.documento_afectado else None,
        }
    })
