"""
Módulo de Compras - RetailMind
Contiene todas las vistas relacionadas con compras, DTEs de compras, recepciones y proveedores
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
import csv
from decimal import Decimal

from .models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Dte, Dte_Detalle_Pago, 
    Dte_Productos, Empresa, Producto, Producto_Talla, Productos_Recepcionados,
    Sucursal, EmpresaUser, Movimientos_Producto, LoteProducto
)


# ========== GESTIÓN DE COMPRAS ==========

@login_required
def verGestionCompras(request):
    """Vista principal para gestión de compras"""
    empresas = Empresa.objects.all()
    return render(request, 'vistas/modulo_compras/gestionCompras.html', {'empresas': empresas})


def crear_compra(request):
    """Crear nueva compra"""
    if request.method == 'POST':
        try:
            # Obtener datos del formulario
            empresa_id = request.POST.get('empresa_id')
            fecha_compra = request.POST.get('fecha_compra')
            numero_factura = request.POST.get('numero_factura')
            total = request.POST.get('total')
            observaciones = request.POST.get('observaciones', '')
            
            # Validaciones
            if not all([empresa_id, fecha_compra, numero_factura, total]):
                return JsonResponse({
                    'success': False,
                    'error': 'Todos los campos son requeridos'
                })
            
            # Crear la compra
            compra = Compras.objects.create(
                empresa_id=empresa_id,
                fecha_compra=fecha_compra,
                numero_factura=numero_factura,
                total=Decimal(total),
                observaciones=observaciones,
                usuario_creacion=request.user
            )
            
            return JsonResponse({
                'success': True,
                'message': 'Compra creada exitosamente',
                'compra_id': compra.id
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al crear compra: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


@require_GET
def obtener_compras_por_anio(request):
    """Obtener compras filtradas por año"""
    try:
        anio = request.GET.get('anio', timezone.now().year)
        empresa_id = request.GET.get('empresa_id')
        
        queryset = Compras.objects.filter(
            fecha__year=anio
        ).select_related('empresa')
        
        if empresa_id:
            queryset = queryset.filter(empresa_id=empresa_id)
        
        compras = queryset.order_by('-fecha')
        
        compras_data = []
        for compra in compras:
            compras_data.append({
                'id': compra.id,
                'nombre': compra.nombre,
                'empresa': compra.empresa.nombre,
                'fecha': compra.fecha.strftime('%d/%m/%Y'),
                'temporada': compra.temporada,
                'responsable': compra.responsable,
                'correlativo': compra.correlativo
            })
        
        return JsonResponse({
            'success': True,
            'compras': compras_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener compras: {str(e)}'
        })


def importar_csv_compra(request):
    """Importar compras desde archivo CSV"""
    if request.method == 'POST':
        try:
            csv_file = request.FILES.get('csv_file')
            if not csv_file:
                return JsonResponse({
                    'success': False,
                    'error': 'Archivo CSV requerido'
                })
            
            # Validar formato
            if not csv_file.name.endswith('.csv'):
                return JsonResponse({
                    'success': False,
                    'error': 'El archivo debe ser formato CSV'
                })
            
            # Leer archivo CSV
            decoded_file = csv_file.read().decode('utf-8').splitlines()
            reader = csv.DictReader(decoded_file)
            
            compras_creadas = 0
            errores = []
            
            with transaction.atomic():
                for row_num, row in enumerate(reader, start=2):
                    try:
                        # Validar campos requeridos
                        campos_requeridos = ['empresa_id', 'fecha_compra', 'numero_factura', 'total']
                        for campo in campos_requeridos:
                            if not row.get(campo):
                                errores.append(f'Fila {row_num}: Campo {campo} requerido')
                                continue
                        
                        # Crear compra
                        Compras.objects.create(
                            empresa_id=row['empresa_id'],
                            fecha_compra=row['fecha_compra'],
                            numero_factura=row['numero_factura'],
                            total=Decimal(row['total']),
                            observaciones=row.get('observaciones', ''),
                            usuario_creacion=request.user
                        )
                        compras_creadas += 1
                        
                    except Exception as e:
                        errores.append(f'Fila {row_num}: {str(e)}')
            
            return JsonResponse({
                'success': True,
                'message': f'{compras_creadas} compras importadas exitosamente',
                'errores': errores
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al importar CSV: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


def recepcionar_compra(request):
    """Recepcionar productos de una compra"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            compra_id = data.get('compra_id')
            productos = data.get('productos', [])
            
            if not compra_id:
                return JsonResponse({
                    'success': False,
                    'error': 'ID de compra requerido'
                })
            
            if not productos:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe incluir al menos un producto'
                })
            
            compra = get_object_or_404(Compras, id=compra_id)
            sucursal_id = request.session.get('idSucursalActual')
            
            with transaction.atomic():
                for item in productos:
                    producto_talla = get_object_or_404(Producto_Talla, id=item['producto_talla_id'])
                    cantidad = item['cantidad']
                    costo_unitario = Decimal(item['costo_unitario'])
                    precio_venta = Decimal(item.get('precio_venta', 0))
                    
                    # Crear registro de recepción
                    Productos_Recepcionados.objects.create(
                        compra=compra,
                        producto_talla=producto_talla,
                        cantidad_recepcionada=cantidad,
                        costo_unitario=costo_unitario,
                        precio_venta_sugerido=precio_venta,
                        usuario_recepcion=request.user,
                        fecha_recepcion=timezone.now()
                    )
                    
                    # Crear lote FIFO
                    from .views import crear_lote_producto
                    crear_lote_producto(
                        producto_talla=producto_talla,
                        cantidad=cantidad,
                        costo_unitario=costo_unitario,
                        sobreprecio_unitario=0,
                        precio_venta_unitario=precio_venta,
                        observaciones=f'Recepción compra #{compra.numero_factura}'
                    )
                    
                    # Registrar movimiento
                    from .views import registrar_movimiento_producto
                    registrar_movimiento_producto(
                        producto_talla=producto_talla,
                        concepto='COMPRA',
                        cantidad=cantidad,
                        responsable=request.user,
                        observaciones=f'Recepción compra #{compra.numero_factura}'
                    )
                
                # Actualizar estado de la compra
                compra.estado = 'RECEPCIONADA'
                compra.fecha_recepcion = timezone.now()
                compra.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Compra recepcionada exitosamente'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al recepcionar compra: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


# ========== GESTIÓN DE DTEs DE COMPRAS ==========

@login_required
def verGestionDteCompras(request):
    """Vista principal para gestión de DTEs de compras"""
    return render(request, 'vistas/modulo_compras/gestionDteCompras.html')


def obtener_dte(request, dte_id):
    """Obtener detalles de un DTE específico"""
    try:
        dte = get_object_or_404(Dte, id=dte_id)
        
        # Obtener productos del DTE
        productos = []
        for dte_producto in dte.dte_productos.all():
            productos.append({
                'id': dte_producto.id,
                'producto_nombre': dte_producto.productoTalla.producto.nombre,
                'sku': dte_producto.productoTalla.sku,
                'talla': dte_producto.productoTalla.talla.nombre if dte_producto.productoTalla.talla else '',
                'cantidad': dte_producto.cantidad,
                'precio_unitario': float(dte_producto.precio_unitario),
                'total_linea': float(dte_producto.cantidad * dte_producto.precio_unitario)
            })
        
        # Obtener pagos
        pagos = []
        for pago in dte.dte_detalle_pago.all():
            pagos.append({
                'id': pago.id,
                'fecha_pago': pago.fecha_pago.strftime('%d/%m/%Y'),
                'monto': float(pago.monto),
                'metodo_pago': pago.metodo_pago,
                'referencia': pago.referencia or '',
                'observaciones': pago.observaciones or ''
            })
        
        dte_data = {
            'id': dte.id,
            'numero_dte': dte.numero_dte,
            'tipo_documento': dte.tipo_documento,
            'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
            'emisor': dte.emisor.nombre,
            'receptor': dte.receptor.nombre if dte.receptor else '',
            'subtotal': float(dte.subtotal),
            'iva': float(dte.iva),
            'total': float(dte.total),
            'estado_dte': dte.estado_dte,
            'productos': productos,
            'pagos': pagos
        }
        
        return JsonResponse({
            'success': True,
            'dte': dte_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener DTE: {str(e)}'
        })


def obtener_dte_compras(request):
    """Obtener lista de DTEs de compras con filtros"""
    try:
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        proveedor_id = request.GET.get('proveedor_id')
        estado = request.GET.get('estado')
        tipo_documento = request.GET.get('tipo_documento')
        
        # Construir queryset
        queryset = Dte.objects.filter(
            tipo_transaccion='COMPRA'
        ).select_related('emisor', 'receptor')
        
        # Aplicar filtros
        if fecha_inicio:
            queryset = queryset.filter(fecha_emision__gte=fecha_inicio)
        if fecha_fin:
            queryset = queryset.filter(fecha_emision__lte=fecha_fin)
        if proveedor_id:
            queryset = queryset.filter(emisor_id=proveedor_id)
        if estado:
            queryset = queryset.filter(estado_dte=estado)
        if tipo_documento:
            queryset = queryset.filter(tipo_documento=tipo_documento)
        
        # Ordenar por fecha descendente
        queryset = queryset.order_by('-fecha_emision')
        
        # Paginación
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        paginator = Paginator(queryset, per_page)
        dtes_page = paginator.get_page(page)
        
        # Serializar datos
        dtes_data = []
        for dte in dtes_page:
            dtes_data.append({
                'id': dte.id,
                'numero_dte': dte.numero_dte,
                'tipo_documento': dte.tipo_documento,
                'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
                'emisor': dte.emisor.nombre,
                'subtotal': float(dte.subtotal),
                'iva': float(dte.iva),
                'total': float(dte.total),
                'estado_dte': dte.estado_dte,
                'fecha_recepcion': dte.fecha_recepcion.strftime('%d/%m/%Y') if dte.fecha_recepcion else None
            })
        
        return JsonResponse({
            'success': True,
            'dtes': dtes_data,
            'pagination': {
                'current_page': dtes_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': dtes_page.has_next(),
                'has_previous': dtes_page.has_previous(),
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener DTEs: {str(e)}'
        })


def crearDteCompras(request):
    """Crear nuevo DTE de compras"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Validar datos requeridos
            emisor_id = data.get('emisor_id')
            tipo_documento = data.get('tipo_documento')
            numero_dte = data.get('numero_dte')
            productos = data.get('productos', [])
            
            if not all([emisor_id, tipo_documento, numero_dte]):
                return JsonResponse({
                    'success': False,
                    'error': 'Emisor, tipo de documento y número DTE son requeridos'
                })
            
            if not productos:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe incluir al menos un producto'
                })
            
            with transaction.atomic():
                # Calcular totales
                subtotal = sum(
                    Decimal(item['cantidad']) * Decimal(item['precio_unitario']) 
                    for item in productos
                )
                iva = subtotal * Decimal('0.19')  # 19% IVA
                total = subtotal + iva
                
                # Crear DTE
                dte = Dte.objects.create(
                    numero_dte=numero_dte,
                    tipo_documento=tipo_documento,
                    tipo_transaccion='COMPRA',
                    fecha_emision=data.get('fecha_emision', timezone.now().date()),
                    emisor_id=emisor_id,
                    receptor_id=data.get('receptor_id'),
                    subtotal=subtotal,
                    iva=iva,
                    total=total,
                    estado_dte='EMITIDO',
                    observaciones=data.get('observaciones', '')
                )
                
                # Crear productos del DTE
                for item in productos:
                    producto_talla = get_object_or_404(Producto_Talla, id=item['producto_talla_id'])
                    
                    Dte_Productos.objects.create(
                        dte=dte,
                        productoTalla=producto_talla,
                        cantidad=item['cantidad'],
                        precio_unitario=item['precio_unitario'],
                        descuento_unitario=item.get('descuento_unitario', 0)
                    )
            
            return JsonResponse({
                'success': True,
                'message': 'DTE creado exitosamente',
                'dte_id': dte.id
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al crear DTE: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


@login_required
def empresas_proveedoras(request):
    """Obtener lista de empresas proveedoras"""
    try:
        empresas = Empresa.objects.filter(
            esProveedor=True
        ).order_by('nombre')
        
        empresas_data = []
        for empresa in empresas:
            empresas_data.append({
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut
            })
        
        return JsonResponse(empresas_data, safe=False)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener empresas: {str(e)}'
        })


def cargarDteCompra(request):
    """Cargar DTE de compra desde archivo XML"""
    if request.method == 'POST':
        try:
            xml_file = request.FILES.get('xml_file')
            if not xml_file:
                return JsonResponse({
                    'success': False,
                    'error': 'Archivo XML requerido'
                })
            
            # Validar formato
            if not xml_file.name.endswith('.xml'):
                return JsonResponse({
                    'success': False,
                    'error': 'El archivo debe ser formato XML'
                })
            
            # TODO: Implementar parser XML para DTEs
            # Por ahora retornamos éxito simulado
            
            return JsonResponse({
                'success': True,
                'message': 'DTE cargado exitosamente (funcionalidad en desarrollo)'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al cargar DTE: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


# ========== GESTIÓN DE PAGOS DTE ==========

def registrarPagoDTE(request):
    """Registrar pago para un DTE"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            dte_id = data.get('dte_id')
            monto = data.get('monto')
            metodo_pago = data.get('metodo_pago')
            fecha_pago = data.get('fecha_pago')
            
            if not all([dte_id, monto, metodo_pago, fecha_pago]):
                return JsonResponse({
                    'success': False,
                    'error': 'Todos los campos son requeridos'
                })
            
            dte = get_object_or_404(Dte, id=dte_id)
            
            # Crear detalle de pago
            pago = Dte_Detalle_Pago.objects.create(
                dte=dte,
                fecha_pago=fecha_pago,
                monto=Decimal(monto),
                metodo_pago=metodo_pago,
                referencia=data.get('referencia', ''),
                observaciones=data.get('observaciones', '')
            )
            
            # Verificar si el DTE está completamente pagado
            total_pagado = dte.dte_detalle_pago.aggregate(
                total=Sum('monto')
            )['total'] or 0
            
            if total_pagado >= dte.total:
                dte.estado_dte = 'PAGADO'
                dte.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Pago registrado exitosamente',
                'pago_id': pago.id
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
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


def obtenerDetallePago(request, dte_id):
    """Obtener detalles de pagos de un DTE"""
    try:
        dte = get_object_or_404(Dte, id=dte_id)
        
        pagos = []
        for pago in dte.dte_detalle_pago.all().order_by('-fecha_pago'):
            pagos.append({
                'id': pago.id,
                'fecha_pago': pago.fecha_pago.strftime('%d/%m/%Y'),
                'monto': float(pago.monto),
                'metodo_pago': pago.metodo_pago,
                'referencia': pago.referencia or '',
                'observaciones': pago.observaciones or ''
            })
        
        total_pagado = sum(pago['monto'] for pago in pagos)
        saldo_pendiente = float(dte.total) - total_pagado
        
        return JsonResponse({
            'success': True,
            'dte': {
                'id': dte.id,
                'numero_dte': dte.numero_dte,
                'total': float(dte.total),
                'total_pagado': total_pagado,
                'saldo_pendiente': saldo_pendiente
            },
            'pagos': pagos
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener pagos: {str(e)}'
        })


def pagosDTE(request, dte_id):
    """Vista para gestionar pagos de un DTE"""
    try:
        dte = get_object_or_404(Dte, id=dte_id)
        context = {
            'dte': dte
        }
        return render(request, 'vistas/modulo_compras/pagos_dte.html', context)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


def eliminarPago(request, pago_id):
    """Eliminar un pago de DTE"""
    if request.method == 'DELETE':
        try:
            pago = get_object_or_404(Dte_Detalle_Pago, id=pago_id)
            dte = pago.dte
            
            pago.delete()
            
            # Recalcular estado del DTE
            total_pagado = dte.dte_detalle_pago.aggregate(
                total=Sum('monto')
            )['total'] or 0
            
            if total_pagado < dte.total:
                dte.estado_dte = 'EMITIDO'
                dte.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Pago eliminado exitosamente'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al eliminar pago: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


def detallePago(request, pago_id):
    """Obtener detalles de un pago específico"""
    try:
        pago = get_object_or_404(Dte_Detalle_Pago, id=pago_id)
        
        pago_data = {
            'id': pago.id,
            'dte_id': pago.dte.id,
            'fecha_pago': pago.fecha_pago.strftime('%Y-%m-%d'),
            'monto': float(pago.monto),
            'metodo_pago': pago.metodo_pago,
            'referencia': pago.referencia or '',
            'observaciones': pago.observaciones or ''
        }
        
        return JsonResponse({
            'success': True,
            'pago': pago_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener pago: {str(e)}'
        })


def editarPago(request, pago_id):
    """Editar un pago existente"""
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            pago = get_object_or_404(Dte_Detalle_Pago, id=pago_id)
            
            # Actualizar campos
            pago.fecha_pago = data.get('fecha_pago', pago.fecha_pago)
            pago.monto = Decimal(data.get('monto', pago.monto))
            pago.metodo_pago = data.get('metodo_pago', pago.metodo_pago)
            pago.referencia = data.get('referencia', pago.referencia)
            pago.observaciones = data.get('observaciones', pago.observaciones)
            pago.save()
            
            # Recalcular estado del DTE
            dte = pago.dte
            total_pagado = dte.dte_detalle_pago.aggregate(
                total=Sum('monto')
            )['total'] or 0
            
            if total_pagado >= dte.total:
                dte.estado_dte = 'PAGADO'
            else:
                dte.estado_dte = 'EMITIDO'
            dte.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Pago actualizado exitosamente'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al editar pago: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


# ========== NOTAS DE CRÉDITO ==========

def notasCredito(request, dte_id):
    """Vista para gestionar notas de crédito de un DTE"""
    try:
        dte = get_object_or_404(Dte, id=dte_id)
        context = {
            'dte': dte
        }
        return render(request, 'vistas/modulo_compras/notas_credito.html', context)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        })


def agregarNotaCredito(request):
    """Agregar nota de crédito a un DTE"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # TODO: Implementar lógica de notas de crédito
            
            return JsonResponse({
                'success': True,
                'message': 'Nota de crédito agregada (funcionalidad en desarrollo)'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al agregar nota de crédito: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


def eliminarNotaCredito(request, nc_id):
    """Eliminar nota de crédito"""
    if request.method == 'DELETE':
        try:
            # TODO: Implementar eliminación de nota de crédito
            
            return JsonResponse({
                'success': True,
                'message': 'Nota de crédito eliminada (funcionalidad en desarrollo)'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al eliminar nota de crédito: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


def eliminar_dte(request, dte_id):
    """Eliminar DTE de compras"""
    if request.method == 'DELETE':
        try:
            dte = get_object_or_404(Dte, id=dte_id)
            
            # Verificar si se puede eliminar
            if dte.estado_dte == 'PAGADO':
                return JsonResponse({
                    'success': False,
                    'error': 'No se puede eliminar un DTE pagado'
                })
            
            # Verificar si tiene recepciones asociadas
            if dte.fecha_recepcion:
                return JsonResponse({
                    'success': False,
                    'error': 'No se puede eliminar un DTE que ya fue recepcionado'
                })
            
            dte.delete()
            
            return JsonResponse({
                'success': True,
                'message': 'DTE eliminado exitosamente'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al eliminar DTE: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


# ========== DASHBOARDS DE COMPRAS ==========

@login_required
@require_GET
def dashboard_compras_estrategico(request):
    """Dashboard estratégico de compras con métricas avanzadas basado en datos reales"""
    try:
        from datetime import datetime
        
        # Parámetros de filtro
        anio = request.GET.get('anio', datetime.now().year)
        temporada = request.GET.get('temporada', '')
        proveedor_id = request.GET.get('proveedor', '')
        responsable = request.GET.get('responsable', '')
        
        # Query base para compras
        compras_query = Compras.objects.filter(fecha__year=anio)
        
        if temporada:
            compras_query = compras_query.filter(temporada__icontains=temporada)
        if proveedor_id:
            compras_query = compras_query.filter(empresa_id=proveedor_id)
        if responsable:
            compras_query = compras_query.filter(responsable=responsable)
        
        # Obtener IDs de compras para filtrar
        compras_ids = list(compras_query.values_list('id', flat=True))
        
        # ===== CÁLCULO DE MÉTRICAS REALES =====
        
        # 1. Total de compras y unidades
        total_compras = compras_query.count()
        
        # 2. Productos y tallas de las compras
        productos_compras = Compras_Producto.objects.filter(compras__in=compras_ids)
        total_productos = productos_compras.count()
        
        tallas_compras = Compras_Producto_Talla.objects.filter(
            compra_producto__compras__in=compras_ids
        )
        
        # 3. Total de unidades esperadas
        total_unidades_esperadas = tallas_compras.aggregate(
            total=Sum('stock')
        )['total'] or 0
        
        # 4. Total de unidades recepcionadas
        recepciones = Productos_Recepcionados.objects.filter(
            compra_producto_talla__compra_producto__compras__in=compras_ids
        )
        total_unidades_recepcionadas = recepciones.aggregate(
            total=Sum('stockArribado')
        )['total'] or 0
        
        # 5. Cálculo de cumplimiento (% recepcionado vs esperado)
        cumplimiento_general = 0
        if total_unidades_esperadas > 0:
            cumplimiento_general = round((total_unidades_recepcionadas / total_unidades_esperadas) * 100, 1)
        
        # 6. Cálculo de inversión total (costo)
        inversion_total = productos_compras.aggregate(
            total=Sum(F('costo') * F('compras_producto_talla__stock'))
        )['total'] or 0
        
        # 7. Valor esperado de venta (precio sugerido)
        valor_venta_esperado = productos_compras.aggregate(
            total=Sum(F('precioSugerido') * F('compras_producto_talla__stock'))
        )['total'] or 0
        
        # 8. Cálculo de ROI promedio (basado en precio sugerido vs costo)
        roi_promedio = 0
        if inversion_total > 0:
            ganancia_esperada = valor_venta_esperado - inversion_total
            roi_promedio = round((ganancia_esperada / inversion_total) * 100, 1)
        
        # 9. Rotación de inventario (estimada - productos con recepciones)
        productos_con_recepcion = recepciones.values('producto_talla').distinct().count()
        rotacion_inventario = round(productos_con_recepcion / max(total_productos, 1), 2)
        
        # 10. Precisión de pronóstico (% de cumplimiento de recepciones)
        precision_pronostico = cumplimiento_general  # Similar al cumplimiento
        
        # ===== CUMPLIMIENTO POR PROVEEDOR =====
        cumplimiento_proveedores = []
        proveedores = compras_query.values('empresa__id', 'empresa__nombre').distinct()
        
        for proveedor in proveedores:
            compras_proveedor = compras_query.filter(empresa_id=proveedor['empresa__id'])
            compras_proveedor_ids = list(compras_proveedor.values_list('id', flat=True))
            
            tallas_proveedor = Compras_Producto_Talla.objects.filter(
                compra_producto__compras__in=compras_proveedor_ids
            )
            esperadas_proveedor = tallas_proveedor.aggregate(total=Sum('stock'))['total'] or 0
            
            recepciones_proveedor = Productos_Recepcionados.objects.filter(
                compra_producto_talla__compra_producto__compras__in=compras_proveedor_ids
            )
            recepcionadas_proveedor = recepciones_proveedor.aggregate(total=Sum('stockArribado'))['total'] or 0
            
            cumplimiento = 0
            if esperadas_proveedor > 0:
                cumplimiento = round((recepcionadas_proveedor / esperadas_proveedor) * 100, 1)
            
            cumplimiento_proveedores.append({
                'proveedor': proveedor['empresa__nombre'],
                'cumplimiento': cumplimiento
            })
        
        # ===== ROI POR TEMPORADA =====
        roi_temporadas = []
        temporadas = compras_query.values('temporada').distinct()
        
        for temp in temporadas:
            if not temp['temporada']:
                continue
                
            compras_temporada = compras_query.filter(temporada=temp['temporada'])
            compras_temp_ids = list(compras_temporada.values_list('id', flat=True))
            
            productos_temp = Compras_Producto.objects.filter(compras__in=compras_temp_ids)
            
            inversion_temp = productos_temp.aggregate(
                total=Sum(F('costo') * F('compras_producto_talla__stock'))
            )['total'] or 0
            
            valor_temp = productos_temp.aggregate(
                total=Sum(F('precioSugerido') * F('compras_producto_talla__stock'))
            )['total'] or 0
            
            roi_temp = 0
            if inversion_temp > 0:
                ganancia_temp = valor_temp - inversion_temp
                roi_temp = round((ganancia_temp / inversion_temp) * 100, 1)
            
            roi_temporadas.append({
                'temporada': temp['temporada'],
                'roi': roi_temp
            })
        
        # ===== RENDIMIENTO DETALLADO POR COMPRA =====
        rendimiento_detallado = []
        
        for compra in compras_query[:10]:  # Limitar a las primeras 10
            # Unidades de esta compra
            tallas_compra = Compras_Producto_Talla.objects.filter(
                compra_producto__compras=compra
            )
            unidades_esperadas = tallas_compra.aggregate(total=Sum('stock'))['total'] or 0
            
            # Recepciones de esta compra
            recepciones_compra = Productos_Recepcionados.objects.filter(
                compra_producto_talla__compra_producto__compras=compra
            )
            unidades_recibidas = recepciones_compra.aggregate(total=Sum('stockArribado'))['total'] or 0
            
            # Cumplimiento
            cumplimiento_compra = 0
            if unidades_esperadas > 0:
                cumplimiento_compra = round((unidades_recibidas / unidades_esperadas) * 100, 1)
            
            # ROI de la compra
            productos_compra = Compras_Producto.objects.filter(compras=compra)
            inversion_compra = productos_compra.aggregate(
                total=Sum(F('costo') * F('compras_producto_talla__stock'))
            )['total'] or 0
            
            valor_compra = productos_compra.aggregate(
                total=Sum(F('precioSugerido') * F('compras_producto_talla__stock'))
            )['total'] or 0
            
            roi_compra = 0
            if inversion_compra > 0:
                ganancia_compra = valor_compra - inversion_compra
                roi_compra = round((ganancia_compra / inversion_compra) * 100, 1)
            
            # Determinar estado
            estado = 'Pendiente'
            if cumplimiento_compra >= 100:
                estado = 'Completado'
            elif cumplimiento_compra >= 80:
                estado = 'Pendiente'
            else:
                estado = 'Retrasado'
            
            rendimiento_detallado.append({
                'nombre': compra.nombre,
                'proveedor': compra.empresa.nombre,
                'temporada': compra.temporada,
                'cumplimiento': cumplimiento_compra,
                'roi': roi_compra,
                'rotacion': round(unidades_recibidas / max(unidades_esperadas, 1), 2),
                'precision': cumplimiento_compra,
                'estado': estado
            })
        
        # ===== ALERTAS =====
        alertas = []
        
        if cumplimiento_general < 80:
            alertas.append({
                'mensaje': f'Cumplimiento general bajo ({cumplimiento_general}%). Revisar procesos de recepción.'
            })
        
        compras_sin_recepcion = compras_query.count() - Compras.objects.filter(
            id__in=compras_ids,
            compras_producto__compras_producto_talla__productos_recepcionados__isnull=False
        ).distinct().count()
        
        if compras_sin_recepcion > 0:
            alertas.append({
                'mensaje': f'{compras_sin_recepcion} compra(s) sin recepción registrada.'
            })
        
        if roi_promedio < 15:
            alertas.append({
                'mensaje': f'ROI promedio bajo ({roi_promedio}%). Revisar precios y costos.'
            })
        
        # ===== RECOMENDACIONES =====
        recomendaciones = []
        
        if cumplimiento_general < 90:
            recomendaciones.append({
                'mensaje': 'Implementar seguimiento más estricto de recepciones para mejorar cumplimiento.'
            })
        
        if rotacion_inventario < 0.5:
            recomendaciones.append({
                'mensaje': 'Optimizar gestión de inventario para aumentar rotación de productos.'
            })
        
        if len(cumplimiento_proveedores) > 0:
            proveedores_bajo_cumplimiento = [p for p in cumplimiento_proveedores if p['cumplimiento'] < 80]
            if proveedores_bajo_cumplimiento:
                recomendaciones.append({
                    'mensaje': f'Revisar desempeño de {len(proveedores_bajo_cumplimiento)} proveedor(es) con bajo cumplimiento.'
                })
        
        # ===== TENDENCIAS (simuladas por ahora) =====
        tendencias = {
            'trend_cumplimiento': 5.2 if cumplimiento_general >= 80 else -3.5,
            'trend_roi': 8.5 if roi_promedio >= 20 else -2.1,
            'trend_rotacion': 0,
            'trend_precision': 2.3 if precision_pronostico >= 80 else -4.2
        }
        
        # ===== RESPUESTA FINAL =====
        response_data = {
            'cumplimiento_general': cumplimiento_general,
            'roi_promedio': roi_promedio,
            'rotacion_inventario': rotacion_inventario,
            'precision_pronostico': precision_pronostico,
            'cumplimiento_proveedores': cumplimiento_proveedores,
            'roi_temporadas': roi_temporadas,
            'rendimiento_detallado': rendimiento_detallado,
            'alertas': alertas,
            'recomendaciones': recomendaciones,
            **tendencias,
            # Métricas adicionales
            'metricas_adicionales': {
                'total_compras': total_compras,
                'total_productos': total_productos,
                'total_unidades_esperadas': total_unidades_esperadas,
                'total_unidades_recepcionadas': total_unidades_recepcionadas,
                'inversion_total': float(inversion_total) if inversion_total else 0,
                'valor_venta_esperado': float(valor_venta_esperado) if valor_venta_esperado else 0
            }
        }
        
        return JsonResponse(response_data)
        
    except Exception as e:
        import traceback
        return JsonResponse({
            'success': False,
            'error': f'Error al generar dashboard: {str(e)}',
            'traceback': traceback.format_exc()
        }, status=500)


@require_GET
def exportar_dashboard_compras(request):
    """Exportar datos del dashboard de compras a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        
        # Obtener datos del dashboard
        dashboard_response = dashboard_compras_estrategico(request)
        dashboard_data = json.loads(dashboard_response.content)['dashboard']
        
        # Crear workbook
        wb = openpyxl.Workbook()
        
        # Hoja de métricas generales
        ws_metricas = wb.active
        ws_metricas.title = "Métricas Generales"
        
        # Escribir métricas
        metricas = dashboard_data['metricas_generales']
        ws_metricas.append(['Métrica', 'Valor'])
        ws_metricas.append(['Total Compras', metricas['total_compras']])
        ws_metricas.append(['Monto Total', metricas['monto_total']])
        ws_metricas.append(['Promedio por Compra', metricas['promedio_compra']])
        ws_metricas.append(['DTEs Pendientes', metricas['dtes_pendientes']])
        ws_metricas.append(['Monto Pendiente', metricas['monto_pendiente']])
        
        # Hoja de compras por proveedor
        ws_proveedores = wb.create_sheet("Compras por Proveedor")
        ws_proveedores.append(['Proveedor', 'Total Compras', 'Monto Total'])
        
        for item in dashboard_data['compras_por_proveedor']:
            ws_proveedores.append([
                item['proveedor'],
                item['total_compras'],
                item['monto_total']
            ])
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="dashboard_compras.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        })


@login_required
def verDashboardCompras(request):
    """Vista principal del dashboard de compras"""
    return render(request, 'vistas/modulo_dashboards/dashboard_compras_estrategico.html')


@login_required
def verDiagnosticoCompras(request):
    """Vista para diagnóstico de datos de compras"""
    return render(request, 'vistas/modulo_compras/diagnostico_compras.html')


@login_required
def diagnostico_datos_compras(request):
    """API para diagnóstico de calidad de datos de compras"""
    try:
        # Análisis de calidad de datos
        total_compras = Compras.objects.count()
        compras_sin_proveedor = Compras.objects.filter(empresa__isnull=True).count()
        compras_sin_fecha = Compras.objects.filter(fecha_compra__isnull=True).count()
        compras_sin_total = Compras.objects.filter(total__isnull=True).count()
        
        # DTEs con problemas
        dtes_sin_productos = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            dte_productos__isnull=True
        ).count()
        
        dtes_sin_emisor = Dte.objects.filter(
            tipo_transaccion='COMPRA',
            emisor__isnull=True
        ).count()
        
        diagnostico = {
            'compras': {
                'total': total_compras,
                'sin_proveedor': compras_sin_proveedor,
                'sin_fecha': compras_sin_fecha,
                'sin_total': compras_sin_total,
                'calidad_score': max(0, 100 - (
                    (compras_sin_proveedor + compras_sin_fecha + compras_sin_total) * 100 / max(total_compras, 1)
                ))
            },
            'dtes': {
                'sin_productos': dtes_sin_productos,
                'sin_emisor': dtes_sin_emisor
            }
        }
        
        return JsonResponse({
            'success': True,
            'diagnostico': diagnostico
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en diagnóstico: {str(e)}'
        })


def obtenerDetalleComprasPorParametros(request):
    """Obtener detalle de compras por parámetros específicos"""
    # Placeholder para funcionalidad futura
    return JsonResponse({
        'success': True,
        'message': 'Funcionalidad en desarrollo'
    })
