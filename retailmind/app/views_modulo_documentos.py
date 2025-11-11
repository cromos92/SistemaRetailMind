"""
Módulo de Documentos - RetailMind
Contiene todas las vistas relacionadas con DTEs, correlativos, emisión de documentos
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
from decimal import Decimal

from .models import (
    Dte, Dte_Productos, Correlativo, Empresa, Sucursal, EmpresaUser,
    Producto_Talla, Movimientos_Producto, TIPO_DOCUMENTO_CHOICES,
    CreditoTrabajador, PagoCreditoTrabajador, FirmaCreditoTrabajador,
    Vendedor, ESTADO_CREDITO_CHOICES, TIPO_CREDITO_CHOICES, METODO_PAGO_TICKET_CHOICES
)


# ========== GESTIÓN DE DTEs ==========

@login_required
def gestion_dte(request):
    """Vista para mostrar la página de gestión de DTEs de venta"""
    return render(request, 'vistas/modulo_administracion/gestion_dte.html')


@login_required
@require_GET
def detalle_dte(request, dte_id):
    """Obtener detalles de un DTE específico"""
    try:
        dte = get_object_or_404(Dte, id=dte_id)
        
        # Verificar permisos (usuario debe tener acceso a la empresa)
        empresa_actual_id = request.session.get('idEmpresaActual')
        if not empresa_actual_id or (dte.emisor_id != empresa_actual_id and dte.receptor_id != empresa_actual_id):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver este DTE'
            }, status=403)
        
        # Obtener productos del DTE
        productos = []
        for dte_producto in dte.dte_productos.select_related('productoTalla__producto', 'productoTalla__talla'):
            productos.append({
                'id': dte_producto.id,
                'sku': dte_producto.productoTalla.sku,
                'nombre_producto': dte_producto.productoTalla.producto.nombre,
                'talla': dte_producto.productoTalla.talla.nombre if dte_producto.productoTalla.talla else 'Sin talla',
                'cantidad': dte_producto.cantidad,
                'precio_unitario': float(dte_producto.precio_unitario),
                'descuento_unitario': float(dte_producto.descuento_unitario),
                'total_linea': float(dte_producto.cantidad * (dte_producto.precio_unitario - dte_producto.descuento_unitario))
            })
        
        # Obtener pagos si existen
        pagos = []
        if hasattr(dte, 'dte_detalle_pago'):
            for pago in dte.dte_detalle_pago.all():
                pagos.append({
                    'id': pago.id,
                    'fecha_pago': pago.fecha_pago.strftime('%d/%m/%Y'),
                    'monto': float(pago.monto),
                    'metodo_pago': pago.metodo_pago,
                    'referencia': pago.referencia or '',
                    'observaciones': pago.observaciones or ''
                })
        
        # Obtener movimientos asociados
        movimientos = []
        for movimiento in dte.dte_movimientos.select_related('producto_talla__producto', 'sucursal_origen', 'sucursal_destino'):
            movimientos.append({
                'id': movimiento.id,
                'concepto': movimiento.concepto,
                'tipo_movimiento': movimiento.tipo_movimiento,
                'cantidad': movimiento.cantidad,
                'producto': movimiento.producto_talla.producto.nombre if movimiento.producto_talla else '',
                'sku': movimiento.producto_talla.sku if movimiento.producto_talla else '',
                'sucursal_origen': movimiento.sucursal_origen.nombre if movimiento.sucursal_origen else '',
                'sucursal_destino': movimiento.sucursal_destino.nombre if movimiento.sucursal_destino else '',
                'estado': movimiento.estado,
                'fecha_creacion': movimiento.fecha_creacion.strftime('%d/%m/%Y %H:%M')
            })
        
        dte_data = {
            'id': dte.id,
            'numero_dte': dte.numero_dte,
            'tipo_documento': dte.tipo_documento,
            'tipo_transaccion': dte.tipo_transaccion,
            'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
            'fecha_vencimiento': dte.fecha_vencimiento.strftime('%d/%m/%Y') if dte.fecha_vencimiento else None,
            'emisor': {
                'id': dte.emisor.id,
                'nombre': dte.emisor.nombre,
                'rut': dte.emisor.rut
            } if dte.emisor else None,
            'receptor': {
                'id': dte.receptor.id,
                'nombre': dte.receptor.nombre,
                'rut': dte.receptor.rut
            } if dte.receptor else None,
            'subtotal': float(dte.subtotal),
            'descuento_global': float(dte.descuento_global),
            'iva': float(dte.iva),
            'total': float(dte.total),
            'estado_dte': dte.estado_dte,
            'fecha_recepcion': dte.fecha_recepcion.strftime('%d/%m/%Y %H:%M') if dte.fecha_recepcion else None,
            'observaciones': dte.observaciones or '',
            'productos': productos,
            'pagos': pagos,
            'movimientos': movimientos
        }
        
        return JsonResponse({
            'success': True,
            'dte': dte_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener DTE: {str(e)}'
        }, status=500)


@login_required
@require_POST
def cargar_dte_ventas(request):
    """Cargar DTEs de ventas con filtros y paginación"""
    try:
        data = json.loads(request.body)
        
        # Parámetros de filtro
        fecha_inicio = data.get('fecha_inicio')
        fecha_fin = data.get('fecha_fin')
        tipo_documento = data.get('tipo_documento')
        estado_dte = data.get('estado_dte')
        cliente_id = data.get('cliente_id')
        numero_dte = data.get('numero_dte')
        
        # Parámetros de paginación
        page = int(data.get('page', 1))
        per_page = int(data.get('per_page', 20))
        
        # Obtener empresa actual
        empresa_actual_id = request.session.get('idEmpresaActual')
        if not empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa en la sesión'
            }, status=400)
        
        # Construir queryset base
        queryset = Dte.objects.filter(
            tipo_transaccion='VENTA',
            emisor_id=empresa_actual_id
        ).select_related('emisor', 'receptor', 'sucursal')
        
        # Aplicar filtros
        if fecha_inicio:
            queryset = queryset.filter(fecha_emision__gte=fecha_inicio)
        
        if fecha_fin:
            queryset = queryset.filter(fecha_emision__lte=fecha_fin)
        
        if tipo_documento:
            queryset = queryset.filter(tipo_documento=tipo_documento)
        
        if estado_dte:
            queryset = queryset.filter(estado_dte=estado_dte)
        
        if cliente_id:
            queryset = queryset.filter(receptor_id=cliente_id)
        
        if numero_dte:
            queryset = queryset.filter(numero_dte__icontains=numero_dte)
        
        # Ordenar por fecha descendente
        queryset = queryset.order_by('-fecha_emision', '-id')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        dtes_page = paginator.get_page(page)
        
        # Serializar datos
        dtes_data = []
        for dte in dtes_page:
            # Calcular total pagado si tiene pagos
            total_pagado = 0
            if hasattr(dte, 'dte_detalle_pago'):
                total_pagado = dte.dte_detalle_pago.aggregate(
                    total=Sum('monto')
                )['total'] or 0
            
            dtes_data.append({
                'id': dte.id,
                'numero_dte': dte.numero_dte,
                'tipo_documento': dte.tipo_documento,
                'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y'),
                'fecha_vencimiento': dte.fecha_vencimiento.strftime('%d/%m/%Y') if dte.fecha_vencimiento else '',
                'cliente': dte.receptor.nombre if dte.receptor else 'Sin cliente',
                'cliente_rut': dte.receptor.rut if dte.receptor else '',
                'subtotal': float(dte.subtotal),
                'iva': float(dte.iva),
                'total': float(dte.total),
                'total_pagado': float(total_pagado),
                'saldo_pendiente': float(dte.total - total_pagado),
                'estado_dte': dte.estado_dte,
                'sucursal': dte.sucursal.nombre if dte.sucursal else '',
                'observaciones': dte.observaciones or ''
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
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al cargar DTEs: {str(e)}'
        }, status=500)


# ========== GESTIÓN DE CORRELATIVOS ==========

@login_required
def gestion_correlativos(request):
    """Vista principal para gestión de correlativos"""
    from .models import Correlativo, Sucursal, TIPO_DOCUMENTO_CHOICES
    
    # Obtener la empresa actual
    empresa_actual_id = request.session.get('idEmpresaActual')
    
    # Obtener todas las sucursales de la empresa
    sucursales = Sucursal.objects.filter(empresa_id=empresa_actual_id).order_by('alias')
    
    # Obtener todos los correlativos de todas las sucursales de la empresa
    correlativos = Correlativo.objects.filter(
        sucursal__empresa_id=empresa_actual_id
    ).select_related('sucursal').order_by('sucursal__alias', 'tipo_dte')
    
    # Aplicar filtros si existen
    filtro_sucursal = request.GET.get('sucursal')
    filtro_tipo_documento = request.GET.get('tipo_documento')
    filtro_estado = request.GET.get('estado')
    
    if filtro_sucursal:
        correlativos = correlativos.filter(sucursal_id=filtro_sucursal)
    
    if filtro_tipo_documento:
        correlativos = correlativos.filter(tipo_dte=filtro_tipo_documento)
    
    # Filtrar por estado calculado
    if filtro_estado:
        correlativos_filtrados = []
        for correlativo in correlativos:
            if filtro_estado == 'activo' and correlativo.estado == 'activo':
                correlativos_filtrados.append(correlativo)
            elif filtro_estado == 'agotado' and correlativo.estado == 'agotado':
                correlativos_filtrados.append(correlativo)
            elif filtro_estado == 'proximo_agotarse' and correlativo.estado == 'critico':
                correlativos_filtrados.append(correlativo)
        correlativos = correlativos_filtrados
    
    # Calcular estadísticas
    total_correlativos = len(correlativos) if isinstance(correlativos, list) else correlativos.count()
    
    correlativos_activos = 0
    correlativos_proximos_agotar = 0
    correlativos_agotados = 0
    
    for correlativo in correlativos:
        estado = correlativo.estado
        if estado == 'activo':
            correlativos_activos += 1
        elif estado == 'critico':
            correlativos_proximos_agotar += 1
        elif estado == 'agotado':
            correlativos_agotados += 1
    
    context = {
        'correlativos': correlativos,
        'sucursales': sucursales,
        'tipos_documento': TIPO_DOCUMENTO_CHOICES,
        'total_correlativos': total_correlativos,
        'correlativos_activos': correlativos_activos,
        'correlativos_proximos_agotar': correlativos_proximos_agotar,
        'correlativos_agotados': correlativos_agotados,
    }
    
    return render(request, 'vistas/modulo_administracion/gestion_correlativos.html', context)


@login_required
@require_POST
def guardar_correlativo(request):
    """Crear o actualizar correlativo"""
    try:
        data = json.loads(request.body)
        
        # Validar datos requeridos
        sucursal_id = data.get('sucursal_id')
        tipo_documento = data.get('tipo_documento')
        serie = data.get('serie', '')
        numero_inicial = data.get('numero_inicial')
        numero_final = data.get('numero_final')
        
        if not all([sucursal_id, tipo_documento, numero_inicial, numero_final]):
            return JsonResponse({
                'success': False,
                'error': 'Sucursal, tipo de documento, número inicial y final son requeridos'
            }, status=400)
        
        # Validar que el usuario tenga acceso a la sucursal
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        empresa_actual_id = request.session.get('idEmpresaActual')
        
        if sucursal.empresa_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para esta sucursal'
            }, status=403)
        
        # Validar rangos
        numero_inicial = int(numero_inicial)
        numero_final = int(numero_final)
        
        if numero_inicial >= numero_final:
            return JsonResponse({
                'success': False,
                'error': 'El número inicial debe ser menor al número final'
            }, status=400)
        
        # Verificar si ya existe un correlativo activo para esta combinación
        correlativo_existente = Correlativo.objects.filter(
            sucursal=sucursal,
            tipo_documento=tipo_documento,
            serie=serie,
            activo=True
        ).first()
        
        if correlativo_existente:
            # Actualizar correlativo existente
            correlativo_existente.numero_final = numero_final
            correlativo_existente.fecha_vencimiento = data.get('fecha_vencimiento')
            correlativo_existente.observaciones = data.get('observaciones', '')
            correlativo_existente.save()
            
            correlativo = correlativo_existente
            mensaje = 'Correlativo actualizado exitosamente'
        else:
            # Crear nuevo correlativo
            correlativo = Correlativo.objects.create(
                sucursal=sucursal,
                tipo_documento=tipo_documento,
                serie=serie,
                numero_inicial=numero_inicial,
                numero_actual=numero_inicial,
                numero_final=numero_final,
                fecha_vencimiento=data.get('fecha_vencimiento'),
                observaciones=data.get('observaciones', ''),
                activo=True
            )
            mensaje = 'Correlativo creado exitosamente'
        
        return JsonResponse({
            'success': True,
            'message': mensaje,
            'correlativo_id': correlativo.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except ValueError as e:
        return JsonResponse({
            'success': False,
            'error': 'Los números inicial y final deben ser enteros válidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al guardar correlativo: {str(e)}'
        }, status=500)


@login_required
@require_GET
def obtener_correlativo(request, correlativo_id):
    """Obtener detalles de un correlativo específico"""
    try:
        correlativo = get_object_or_404(Correlativo, id=correlativo_id)
        
        # Verificar permisos
        empresa_actual_id = request.session.get('idEmpresaActual')
        if correlativo.sucursal.empresa_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver este correlativo'
            }, status=403)
        
        # Calcular estadísticas
        numeros_utilizados = correlativo.numero_actual - correlativo.numero_inicial
        numeros_disponibles = correlativo.numero_final - correlativo.numero_actual + 1
        porcentaje_utilizado = (numeros_utilizados / (correlativo.numero_final - correlativo.numero_inicial + 1)) * 100
        
        correlativo_data = {
            'id': correlativo.id,
            'sucursal': {
                'id': correlativo.sucursal.id,
                'nombre': correlativo.sucursal.nombre
            },
            'tipo_documento': correlativo.tipo_documento,
            'serie': correlativo.serie,
            'numero_inicial': correlativo.numero_inicial,
            'numero_actual': correlativo.numero_actual,
            'numero_final': correlativo.numero_final,
            'fecha_creacion': correlativo.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
            'fecha_vencimiento': correlativo.fecha_vencimiento.strftime('%d/%m/%Y') if correlativo.fecha_vencimiento else None,
            'activo': correlativo.activo,
            'observaciones': correlativo.observaciones or '',
            'estadisticas': {
                'numeros_utilizados': numeros_utilizados,
                'numeros_disponibles': numeros_disponibles,
                'porcentaje_utilizado': round(porcentaje_utilizado, 2)
            }
        }
        
        return JsonResponse({
            'success': True,
            'correlativo': correlativo_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener correlativo: {str(e)}'
        }, status=500)


@login_required
@require_POST
def renovar_correlativo(request):
    """Renovar un correlativo (crear nuevo rango)"""
    try:
        data = json.loads(request.body)
        
        correlativo_id = data.get('correlativo_id')
        nuevo_numero_final = data.get('nuevo_numero_final')
        fecha_vencimiento = data.get('fecha_vencimiento')
        
        if not all([correlativo_id, nuevo_numero_final]):
            return JsonResponse({
                'success': False,
                'error': 'ID de correlativo y nuevo número final son requeridos'
            }, status=400)
        
        correlativo = get_object_or_404(Correlativo, id=correlativo_id)
        
        # Verificar permisos
        empresa_actual_id = request.session.get('idEmpresaActual')
        if correlativo.sucursal.empresa_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para renovar este correlativo'
            }, status=403)
        
        # Validar nuevo número final
        nuevo_numero_final = int(nuevo_numero_final)
        if nuevo_numero_final <= correlativo.numero_final:
            return JsonResponse({
                'success': False,
                'error': 'El nuevo número final debe ser mayor al actual'
            }, status=400)
        
        # Actualizar correlativo
        correlativo.numero_final = nuevo_numero_final
        correlativo.fecha_vencimiento = fecha_vencimiento
        correlativo.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Correlativo renovado exitosamente'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except ValueError:
        return JsonResponse({
            'success': False,
            'error': 'El nuevo número final debe ser un entero válido'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al renovar correlativo: {str(e)}'
        }, status=500)


@login_required
@require_GET
def historial_correlativo(request, correlativo_id):
    """Obtener historial de uso de un correlativo"""
    try:
        correlativo = get_object_or_404(Correlativo, id=correlativo_id)
        
        # Verificar permisos
        empresa_actual_id = request.session.get('idEmpresaActual')
        if correlativo.sucursal.empresa_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver este historial'
            }, status=403)
        
        # Obtener DTEs que usaron este correlativo
        dtes_historial = Dte.objects.filter(
            sucursal=correlativo.sucursal,
            tipo_documento=correlativo.tipo_documento
        ).order_by('-fecha_emision')[:50]  # Últimos 50 documentos
        
        historial_data = []
        for dte in dtes_historial:
            historial_data.append({
                'id': dte.id,
                'numero_dte': dte.numero_dte,
                'fecha_emision': dte.fecha_emision.strftime('%d/%m/%Y %H:%M'),
                'cliente': dte.receptor.nombre if dte.receptor else 'Sin cliente',
                'total': float(dte.total),
                'estado_dte': dte.estado_dte
            })
        
        return JsonResponse({
            'success': True,
            'historial': historial_data,
            'correlativo': {
                'tipo_documento': correlativo.tipo_documento,
                'serie': correlativo.serie,
                'numero_actual': correlativo.numero_actual,
                'numero_final': correlativo.numero_final
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener historial: {str(e)}'
        }, status=500)


# ========== EMISIÓN DE DTEs ==========

@login_required
def emision_dte(request):
    """Vista principal para emisión de DTEs"""
    return render(request, 'vistas/modulo_documentos/emisionDTE.html')


@login_required
def empresas_clientes(request):
    """Obtener lista de empresas clientes"""
    try:
        # Obtener empresas que pueden ser clientes
        empresas = Empresa.objects.filter(
            es_cliente=True,
            activo=True
        ).order_by('nombre')
        
        empresas_data = []
        for empresa in empresas:
            empresas_data.append({
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut,
                'email': empresa.email or '',
                'telefono': empresa.telefono or '',
                'direccion': empresa.direccion or ''
            })
        
        return JsonResponse({
            'success': True,
            'empresas': empresas_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener empresas clientes: {str(e)}'
        })


@login_required
def obtener_marcas(request):
    """Obtener lista de marcas para filtros"""
    try:
        from .models import Marca
        
        marcas = Marca.objects.filter(activo=True).order_by('nombre')
        
        marcas_data = []
        for marca in marcas:
            marcas_data.append({
                'id': marca.id,
                'nombre': marca.nombre
            })
        
        return JsonResponse({
            'success': True,
            'marcas': marcas_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener marcas: {str(e)}'
        })


@login_required
def obtener_categorias(request):
    """Obtener lista de categorías para filtros"""
    try:
        from .models import Categoria
        
        categorias = Categoria.objects.filter(activo=True).order_by('nombre')
        
        categorias_data = []
        for categoria in categorias:
            categorias_data.append({
                'id': categoria.id,
                'nombre': categoria.nombre,
                'padre_id': categoria.padre.id if categoria.padre else None
            })
        
        return JsonResponse({
            'success': True,
            'categorias': categorias_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener categorías: {str(e)}'
        })


@login_required
def obtener_sucursales(request):
    """Obtener sucursales del usuario actual"""
    try:
        empresa_actual_id = request.session.get('idEmpresaActual')
        
        if not empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa'
            })
        
        # Obtener sucursales de la empresa actual donde el usuario tiene acceso
        sucursales_usuario = EmpresaUser.objects.filter(
            user=request.user,
            empresa_id=empresa_actual_id,
            status=True,
            sucursal__isnull=False
        ).select_related('sucursal')
        
        sucursales_data = []
        for eu in sucursales_usuario:
            sucursales_data.append({
                'id': eu.sucursal.id,
                'nombre': eu.sucursal.nombre,
                'direccion': eu.sucursal.direccion or '',
                'activo': eu.sucursal.activo
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


@require_POST
@login_required
def buscar_productos_bodega(request):
    """Buscar productos en bodega para emisión de DTEs"""
    try:
        data = json.loads(request.body)
        
        termino = data.get('termino', '').strip()
        sucursal_id = data.get('sucursal_id') or request.session.get('idSucursalActual')
        categoria_id = data.get('categoria_id')
        marca_id = data.get('marca_id')
        
        if not termino:
            return JsonResponse({
                'success': False,
                'error': 'Término de búsqueda requerido'
            })
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Sucursal requerida'
            })
        
        # Construir queryset
        queryset = Producto_Talla.objects.select_related(
            'producto', 'producto__categoria', 'producto__marca', 'talla'
        ).filter(
            Q(sku__icontains=termino) |
            Q(producto__nombre__icontains=termino) |
            Q(producto__codigo__icontains=termino)
        )
        
        # Aplicar filtros adicionales
        if categoria_id:
            queryset = queryset.filter(producto__categoria_id=categoria_id)
        
        if marca_id:
            queryset = queryset.filter(producto__marca_id=marca_id)
        
        # Limitar resultados
        queryset = queryset[:20]
        
        productos_data = []
        for pt in queryset:
            # Calcular stock en la sucursal
            stock_sucursal = pt.stock_sucursal(sucursal_id)
            
            if stock_sucursal > 0:  # Solo productos con stock
                productos_data.append({
                    'id': pt.id,
                    'sku': pt.sku,
                    'nombre': pt.producto.nombre,
                    'categoria': pt.producto.categoria.nombre if pt.producto.categoria else '',
                    'marca': pt.producto.marca.nombre if pt.producto.marca else '',
                    'talla': pt.talla.nombre if pt.talla else 'Sin talla',
                    'precio_venta': float(pt.precio_venta),
                    'stock_disponible': stock_sucursal
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


@require_POST
@login_required
def emitir_dte(request):
    """Emitir un nuevo DTE"""
    try:
        data = json.loads(request.body)
        
        # Validar datos requeridos
        tipo_documento = data.get('tipo_documento')
        cliente_id = data.get('cliente_id')
        sucursal_id = data.get('sucursal_id')
        productos = data.get('productos', [])
        
        if not all([tipo_documento, sucursal_id]):
            return JsonResponse({
                'success': False,
                'error': 'Tipo de documento y sucursal son requeridos'
            })
        
        if not productos:
            return JsonResponse({
                'success': False,
                'error': 'Debe incluir al menos un producto'
            })
        
        # Verificar permisos
        empresa_actual_id = request.session.get('idEmpresaActual')
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        if sucursal.empresa_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para esta sucursal'
            }, status=403)
        
        with transaction.atomic():
            # Obtener siguiente correlativo
            from .views import obtener_siguiente_correlativo
            numero_dte = obtener_siguiente_correlativo(sucursal, tipo_documento)
            
            # Calcular totales
            subtotal = 0
            for item in productos:
                cantidad = Decimal(item['cantidad'])
                precio_unitario = Decimal(item['precio_unitario'])
                descuento_unitario = Decimal(item.get('descuento_unitario', 0))
                subtotal += cantidad * (precio_unitario - descuento_unitario)
            
            descuento_global = Decimal(data.get('descuento_global', 0))
            subtotal_con_descuento = subtotal - descuento_global
            iva = subtotal_con_descuento * Decimal('0.19')  # 19% IVA
            total = subtotal_con_descuento + iva
            
            # Crear DTE
            dte = Dte.objects.create(
                numero_dte=numero_dte,
                tipo_documento=tipo_documento,
                tipo_transaccion='VENTA',
                fecha_emision=timezone.now().date(),
                fecha_vencimiento=data.get('fecha_vencimiento'),
                emisor_id=empresa_actual_id,
                receptor_id=cliente_id,
                sucursal=sucursal,
                subtotal=subtotal,
                descuento_global=descuento_global,
                iva=iva,
                total=total,
                estado_dte='EMITIDO',
                observaciones=data.get('observaciones', '')
            )
            
            # Crear productos del DTE
            for item in productos:
                producto_talla = get_object_or_404(Producto_Talla, id=item['producto_talla_id'])
                cantidad = int(item['cantidad'])
                
                # Verificar stock
                stock_disponible = producto_talla.stock_sucursal(sucursal_id)
                if stock_disponible < cantidad:
                    raise ValidationError(f'Stock insuficiente para {producto_talla.sku}')
                
                Dte_Productos.objects.create(
                    dte=dte,
                    productoTalla=producto_talla,
                    cantidad=cantidad,
                    precio_unitario=item['precio_unitario'],
                    descuento_unitario=item.get('descuento_unitario', 0)
                )
                
                # Registrar movimiento de salida
                from .views import registrar_movimiento_producto
                registrar_movimiento_producto(
                    producto_talla=producto_talla,
                    concepto='VENTA',
                    cantidad=-cantidad,  # Negativo para salida
                    responsable=request.user,
                    dte=dte,
                    observaciones=f'Venta DTE #{numero_dte}'
                )
                
                # Consumir stock FIFO
                from .views import consumir_stock_fifo
                consumir_stock_fifo(
                    producto_talla=producto_talla,
                    cantidad_requerida=cantidad,
                    responsable=request.user,
                    observaciones=f'Venta DTE #{numero_dte}',
                    referencia_externa=numero_dte
                )
        
        return JsonResponse({
            'success': True,
            'message': 'DTE emitido exitosamente',
            'dte_id': dte.id,
            'numero_dte': numero_dte
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
            'error': f'Error al emitir DTE: {str(e)}'
        })


# ========== FUNCIONES DE DEBUG ==========

@login_required
def debug_session(request):
    """Vista de debug para verificar datos de sesión"""
    try:
        session_data = {
            'user_id': request.user.id,
            'username': request.user.username,
            'idEmpresaActual': request.session.get('idEmpresaActual'),
            'nombreEmpresaActual': request.session.get('nombreEmpresaActual'),
            'idSucursalActual': request.session.get('idSucursalActual'),
            'sucursalActual': request.session.get('sucursalActual'),
            'all_session_keys': list(request.session.keys())
        }
        
        return JsonResponse({
            'success': True,
            'session_data': session_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en debug: {str(e)}'
        })


def debug_user_empresas(request):
    """Vista de debug para verificar empresas del usuario"""
    try:
        if not request.user.is_authenticated:
            return JsonResponse({
                'success': False,
                'error': 'Usuario no autenticado'
            })
        
        empresas_usuario = EmpresaUser.objects.filter(
            user=request.user,
            status=True
        ).select_related('empresa', 'sucursal')
        
        empresas_data = []
        for eu in empresas_usuario:
            empresas_data.append({
                'empresa_id': eu.empresa.id,
                'empresa_nombre': eu.empresa.nombre,
                'sucursal_id': eu.sucursal.id if eu.sucursal else None,
                'sucursal_nombre': eu.sucursal.nombre if eu.sucursal else None,
                'rol': eu.rol,
                'status': eu.status
            })
        
        return JsonResponse({
            'success': True,
            'user_id': request.user.id,
            'username': request.user.username,
            'empresas': empresas_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en debug: {str(e)}'
        })


# ========== MÓDULO DE CRÉDITOS A TRABAJADORES ==========

@login_required
def gestion_creditos_documentos(request):
    """Vista principal para gestión de créditos a trabajadores desde módulo documentos"""
    return render(request, 'vistas/modulo_administracion/gestion_creditos.html')


@login_required
def interfaz_prueba_acepta(request):
    """Interfaz de prueba para generador de archivos TXT Acepta"""
    return render(request, 'vistas/modulo_administracion/interfaz_prueba_acepta.html')


# ========== MÓDULO DE GENERACIÓN DE ARCHIVOS TXT PARA ACEPTA ==========

def formatear_rut(rut):
    """
    Formatea un RUT al formato requerido por Acepta: XXXXXXXX-X
    
    Args:
        rut (str): RUT en cualquier formato (con o sin puntos/guión)
        
    Returns:
        str: RUT formateado como XXXXXXXX-X
    """
    if not rut:
        return ''
    
    # Eliminar puntos, guiones y espacios
    rut = str(rut).replace('.', '').replace('-', '').replace(' ', '').upper()
    
    # Separar cuerpo y dígito verificador
    if len(rut) >= 2:
        cuerpo = rut[:-1]
        dv = rut[-1]
        return f"{cuerpo}-{dv}"
    
    return rut


def formatear_fecha(fecha):
    """
    Formatea una fecha al formato requerido por Acepta: YYYY-MM-DD
    
    Args:
        fecha: Objeto date, datetime o string
        
    Returns:
        str: Fecha en formato YYYY-MM-DD
    """
    if not fecha:
        return ''
    
    if isinstance(fecha, str):
        return fecha
    
    # Si es date o datetime
    return fecha.strftime('%Y-%m-%d')


def formatear_timestamp(fecha_hora):
    """
    Formatea un timestamp al formato requerido por Acepta: YYYY-MM-DDTHH:MM:SS
    
    Args:
        fecha_hora: Objeto datetime o string
        
    Returns:
        str: Timestamp en formato YYYY-MM-DDTHH:MM:SS
    """
    if not fecha_hora:
        return ''
    
    if isinstance(fecha_hora, str):
        return fecha_hora
    
    return fecha_hora.strftime('%Y-%m-%dT%H:%M:%S')


def formatear_monto(monto):
    """
    Formatea un monto eliminando separadores de miles y usando punto decimal
    
    Args:
        monto: Número decimal o string
        
    Returns:
        str: Monto formateado sin separadores de miles
    """
    if monto is None or monto == '':
        return '0'
    
    # Convertir a Decimal para precisión
    if isinstance(monto, str):
        monto = monto.strip()
        if not monto:
            return '0'
        monto = Decimal(monto.replace(',', ''))
    else:
        monto = Decimal(str(monto))
    
    # Formatear sin separadores de miles
    return str(int(monto))


def formatear_decimal(numero, enteros=12, decimales=6):
    """
    Formatea un número decimal con cantidad específica de decimales
    
    Args:
        numero: Número a formatear
        enteros (int): Cantidad de enteros
        decimales (int): Cantidad de decimales
        
    Returns:
        str: Número formateado
    """
    if numero is None or numero == '':
        return '0.' + '0' * decimales
    
    if isinstance(numero, str):
        numero = numero.strip()
        if not numero:
            return '0.' + '0' * decimales
        numero = Decimal(numero)
    else:
        numero = Decimal(str(numero))
    
    # Formatear con decimales especificados
    formato = f"{{:.{decimales}f}}"
    return formato.format(numero)


def limpiar_texto(texto, max_length=None):
    """
    Limpia un texto eliminando caracteres especiales problemáticos
    
    Args:
        texto (str): Texto a limpiar
        max_length (int): Longitud máxima permitida
        
    Returns:
        str: Texto limpio
    """
    if not texto:
        return ''
    
    # Convertir a string
    texto = str(texto)
    
    # Reemplazar caracteres problemáticos
    texto = texto.replace('|', '')  # El pipe es el separador
    texto = texto.replace('\n', ' ')
    texto = texto.replace('\r', ' ')
    texto = texto.replace('\t', ' ')
    
    # Truncar si es necesario
    if max_length and len(texto) > max_length:
        texto = texto[:max_length]
    
    return texto.strip()


def validar_datos_dte_acepta(datos):
    """
    Valida que los datos mínimos requeridos estén presentes
    
    Args:
        datos (dict): Diccionario con los datos del DTE
        
    Returns:
        tuple: (bool, str) - (es_válido, mensaje_error)
    """
    # Validar datos del documento
    if 'documento' not in datos:
        return False, "Falta la sección 'documento'"
    
    doc = datos['documento']
    if not doc.get('tipo_documento'):
        return False, "Falta el tipo de documento"
    if not doc.get('folio'):
        return False, "Falta el folio"
    if not doc.get('fecha_emision'):
        return False, "Falta la fecha de emisión"
    
    # Validar datos del emisor
    if 'emisor' not in datos:
        return False, "Falta la sección 'emisor'"
    
    emisor = datos['emisor']
    if not emisor.get('rut'):
        return False, "Falta el RUT del emisor"
    if not emisor.get('razon_social'):
        return False, "Falta la razón social del emisor"
    if not emisor.get('giro'):
        return False, "Falta el giro del emisor"
    
    # Validar datos del receptor
    if 'receptor' not in datos:
        return False, "Falta la sección 'receptor'"
    
    receptor = datos['receptor']
    if not receptor.get('rut'):
        return False, "Falta el RUT del receptor"
    if not receptor.get('razon_social'):
        return False, "Falta la razón social del receptor"
    
    # Validar totales
    if 'totales' not in datos:
        return False, "Falta la sección 'totales'"
    
    totales = datos['totales']
    if totales.get('monto_total') is None:
        return False, "Falta el monto total"
    
    # Validar productos
    if 'detalle' not in datos or not datos['detalle']:
        return False, "Debe incluir al menos un producto en el detalle"
    
    for i, item in enumerate(datos['detalle']):
        if not item.get('nombre'):
            return False, f"Falta el nombre del producto en línea {i+1}"
        if item.get('cantidad') is None:
            return False, f"Falta la cantidad en línea {i+1}"
        if item.get('precio_unitario') is None:
            return False, f"Falta el precio unitario en línea {i+1}"
    
    return True, "OK"


def generar_txt_nota_credito_acepta(datos):
    """
    Genera el contenido de un archivo TXT para NOTAS DE CRÉDITO (tipo 61) en formato Acepta
    En Chile las NC usan montos POSITIVOS (el tipo 61 indica que es NC)
    """
    # Validar datos
    es_valido, mensaje = validar_datos_dte_acepta(datos)
    if not es_valido:
        raise ValidationError(f"Error en validación de datos: {mensaje}")
    
    separador = '|'
    lineas = []
    
    import logging
    logger = logging.getLogger(__name__)
    
    doc = datos['documento']
    emisor = datos['emisor']
    receptor = datos['receptor']
    totales = datos['totales']
    
    # ===== LÍNEA 1: IdDoc NC =====
    fecha_emision = formatear_fecha(doc.get('fecha_emision', ''))
    fecha_vencimiento = formatear_fecha(doc.get('fecha_vencimiento', ''))
    if not fecha_vencimiento:
        fecha_vencimiento = fecha_emision
    
    linea1 = [
        str(doc.get('tipo_documento', '')),  # 61
        str(doc.get('folio', '')),
        fecha_emision,
        '',
        str(doc.get('tipo_despacho', '2')),
        str(doc.get('ind_traslado', '1')),
        str(doc.get('forma_pago', '1')),
        fecha_vencimiento,
        '}'
    ]
    lineas.append(separador.join(linea1))
    
    # ===== LÍNEA 2: EMISOR (completo con usuario) =====
    linea2 = [
        formatear_rut(emisor.get('rut', '')),
        limpiar_texto(emisor.get('razon_social', ''), 100),
        limpiar_texto(emisor.get('giro', ''), 80),
        str(emisor.get('acteco', '')),
        '',  '', '',  # Campos vacíos
        limpiar_texto(emisor.get('direccion', ''), 60),
        limpiar_texto(emisor.get('comuna', ''), 20),
        limpiar_texto(emisor.get('ciudad', ''), 20),
        limpiar_texto(emisor.get('codigo_vendedor', ''), 60) or 'USUARIO',
        '}'
    ]
    lineas.append(separador.join(linea2))
    
    # ===== LÍNEA 3: RECEPTOR (completo) =====
    linea3 = [
        formatear_rut(receptor.get('rut', '')),
        limpiar_texto(receptor.get('codigo_interno', ''), 20),
        limpiar_texto(receptor.get('razon_social', ''), 100),
        limpiar_texto(receptor.get('giro', ''), 40),
        limpiar_texto(receptor.get('contacto', ''), 80),
        limpiar_texto(receptor.get('direccion', ''), 70),
        limpiar_texto(receptor.get('comuna', ''), 20),
        limpiar_texto(receptor.get('ciudad', ''), 20),
        '',
        '}'
    ]
    lineas.append(separador.join(linea3))
    
    # ===== LÍNEA 4: TRANSPORTE =====
    lineas.append('||||' + "|}")
    
    # ===== LÍNEA 5: TOTALES (POSITIVOS) =====
    # ✅ En Chile las NC usan montos POSITIVOS
    tasa_iva_str = '19' if totales.get('monto_neto', 0) else ''
    
    linea5 = [
        formatear_monto(abs(totales.get('monto_neto', 0))),  # POSITIVO
        formatear_monto(totales.get('monto_exento', '')),
        tasa_iva_str,
        formatear_monto(abs(totales.get('iva', 0))),  # POSITIVO
        formatear_monto(abs(totales.get('monto_total', 0))),  # POSITIVO
        '', '', '', '', '', '', '', '', '', '', '', '',
        '}'
    ]
    lineas.append(separador.join(linea5))
    
    # ===== SEPARADOR =====
    lineas.append('~')
    
    # ===== PRODUCTOS (cantidades y montos POSITIVOS) =====
    for index, item in enumerate(datos['detalle'], start=1):
        codigo_item = limpiar_texto(item.get('codigo', ''), 35) or limpiar_texto(item.get('sku', ''), 35) or 'Item'
        nombre_con_codigo = f"{codigo_item} {limpiar_texto(item.get('nombre', ''), 80)}"
        
        # ✅ Cantidades y montos POSITIVOS
        cantidad_val = abs(int(item.get('cantidad', 0)))
        precio_val = abs(int(item.get('precio_unitario', 0)))
        monto_val = abs(int(item.get('monto_item', 0)))
        
        linea_detalle = [
            str(item.get('indicador_exencion', '')),
            nombre_con_codigo,
            limpiar_texto(item.get('descripcion', ''), 1000),
            str(cantidad_val),  # POSITIVO
            limpiar_texto(item.get('unidad', 'UN'), 4),
            str(precio_val),  # POSITIVO
            formatear_decimal(item.get('descuento_pct', ''), 3, 2) if item.get('descuento_pct') else '',
            formatear_monto(item.get('monto_descuento', 0)) if item.get('monto_descuento') else '',
            str(monto_val),  # POSITIVO
            codigo_item,
            '}'
        ]
        lineas.append(separador.join(linea_detalle))
    
    # ===== SEPARADORES =====
    # ✅ NC necesita 2 separadores antes de referencias
    lineas.append('~')
    lineas.append('~')
    
    # ===== REFERENCIA OBLIGATORIA =====
    # NC siempre debe tener referencia al documento que anula/corrige
    referencias = datos.get('referencias', [])
    if referencias and len(referencias) > 0:
        for ref in referencias:
            tipo_ref = str(ref.get('tipo_documento', ''))
            folio_ref = str(ref.get('folio', ''))
            fecha_ref = formatear_fecha(ref.get('fecha', ''))
            cod_ref = str(ref.get('razon', '')) or '1'  # 1=anula, 3=corrige montos
            
            # Formato SIN espacios: 33||12345|2025-11-05|1|}
            linea_ref = [
                tipo_ref,
                '',
                folio_ref,
                fecha_ref,
                cod_ref,
                '}'
            ]
            lineas.append(separador.join(linea_ref))
    
    lineas.append('~')
    
    # ===== LÍNEA OBSERVACIONES =====
    vendedor_codigo = emisor.get('codigo_vendedor', '') or 'USUARIO'
    monto_total = abs(int(totales.get('monto_total', 0)))
    monto_neto = abs(int(totales.get('monto_neto', 0)))
    
    # Convertir monto a letras (POSITIVO, sin centavos)
    try:
        from num2words import num2words
        monto_letras = num2words(monto_total, lang='es').upper()
        monto_letras = f"{monto_letras} PESOS (Total Art {monto_neto})"
        monto_letras = monto_letras.replace('  ', ' ').strip()
    except:
        monto_letras = f"{monto_total} PESOS (Total Art {monto_neto})"
    
    # Referencia en observaciones
    folio_ref = referencias[0].get('folio', '') if referencias else ''
    impresora_texto = f"factura {folio_ref}" if folio_ref else 'factura'
    
    info_adicional = [
        f"{vendedor_codigo} ",
        '', '',
        f"{monto_letras}  ",
        '', '', '', '', '', '', '',
        impresora_texto,
        '4',
        '}'
    ]
    lineas.append(separador.join(info_adicional))
    
    # ===== CIERRE =====
    lineas.append('~')
    lineas.append('\\')
    
    return '\n'.join(lineas)


def generar_txt_boleta_acepta(datos):
    """
    Genera el contenido de un archivo TXT para BOLETAS (tipo 39/41) en formato Acepta
    Las boletas tienen estructura DIFERENTE a las facturas
    """
    # Validar datos
    es_valido, mensaje = validar_datos_dte_acepta(datos)
    if not es_valido:
        raise ValidationError(f"Error en validación de datos: {mensaje}")
    
    separador = '|'
    lineas = []
    
    import logging
    logger = logging.getLogger(__name__)
    
    doc = datos['documento']
    emisor = datos['emisor']
    receptor = datos['receptor']
    totales = datos['totales']
    
    # ===== LÍNEA 1: IdDoc BOLETA =====
    # Formato: 39|folio|fecha|ind_servicio|||fecha||}
    fecha_emision = formatear_fecha(doc.get('fecha_emision', ''))
    ind_servicio = doc.get('ind_servicio', '3')  # 3 = Boleta de venta y servicios
    
    linea1 = [
        str(doc.get('tipo_documento', '')),  # 39 o 41
        str(doc.get('folio', '')),
        fecha_emision,
        str(ind_servicio),
        '', '',  # Campos vacíos
        fecha_emision,  # Fecha vencimiento = fecha emisión
        '',  # Campo vacío
        '}'
    ]
    lineas.append(separador.join(linea1))
    
    # ===== LÍNEA 2: EMISOR (CON acteco, direccion, comuna, ciudad) =====
    # ✅ CORRECCIÓN 1: Incluir todos los campos
    linea2 = [
        formatear_rut(emisor.get('rut', '')),
        limpiar_texto(emisor.get('razon_social', ''), 100),
        limpiar_texto(emisor.get('giro', ''), 80),
        str(emisor.get('acteco', '')),
        limpiar_texto(emisor.get('direccion', ''), 60),
        limpiar_texto(emisor.get('comuna', ''), 20),
        limpiar_texto(emisor.get('ciudad', ''), 20),
        '}'  # Termina aquí (SIN usuario)
    ]
    lineas.append(separador.join(linea2))
    
    # ===== LÍNEA 3: RECEPTOR (solo RUT) =====
    # ✅ CORRECCIÓN 4: Usar consumidor final por defecto
    rut_receptor = receptor.get('rut', '') if receptor.get('rut') else '66666666-6'
    linea3 = [
        formatear_rut(rut_receptor),
        '', '', '', '', '', '',  # 6 campos vacíos
        '}'
    ]
    lineas.append(separador.join(linea3))
    
    # ✅ CORRECCIÓN: Totales en línea 4 para boletas (no hay línea de transporte)
    # Formato: |total|||||}
    linea4_totales = [
        '',  # Campo vacío
        formatear_monto(totales.get('monto_total', 0)),
        '', '', '',  # ✅ 3 campos vacíos = 4 pipes total
        '}'
    ]
    lineas.append(separador.join(linea4_totales))
    
    # ===== SEPARADOR =====
    lineas.append('~')
    
    # ===== PRODUCTOS (formato boleta) =====
    # Formato: tipo|codigo||nombre||cantidad|unidad|precio|monto|}
    for index, item in enumerate(datos['detalle'], start=1):
        codigo_item = limpiar_texto(item.get('codigo', ''), 35) or limpiar_texto(item.get('sku', ''), 35) or 'PROD001'
        nombre = limpiar_texto(item.get('nombre', ''), 80)
        cantidad_val = int(item.get('cantidad', 0))
        precio_val = int(item.get('precio_unitario', 0))
        monto_val = int(item.get('monto_item', 0))
        
        linea_prod = [
            'INT1',  # Tipo interno
            codigo_item,
            '',  # Desc vacía
            nombre,
            '',  # Campo vacío
            str(cantidad_val),
            limpiar_texto(item.get('unidad', 'UN'), 4),
            str(precio_val),
            str(monto_val),
            '}'
        ]
        lineas.append(separador.join(linea_prod))
    
    # ===== SEPARADOR =====
    lineas.append('~')
    
    # ===== OBSERVACIONES CON FORMATO ESPECIAL =====
    # ✅ CORRECCIÓN 3: 4 pipes antes de boleta, no 5
    vendedor_codigo = emisor.get('codigo_vendedor', '') or 'USUARIO'
    correlativo = doc.get('folio', '')
    observacion = f"^ Vendedor: {vendedor_codigo} ^ Correlativo Interno: {correlativo} "
    
    linea_obs = [
        vendedor_codigo,
        '', '',
        observacion,
        '', '', '',  # ✅ 3 campos vacíos = 4 pipes
        'boleta',
        '4',
        '}'
    ]
    lineas.append(separador.join(linea_obs))
    
    # ===== SEPARADOR =====
    lineas.append('~')
    
    # ===== DESCUENTO GLOBAL (si existe) =====
    descuento_global = totales.get('descuento_global', 0)
    if descuento_global and descuento_global > 0:
        linea_desc = f"1|D|Descuento Global|$|{formatear_monto(descuento_global)}" + "|}"
        lineas.append(linea_desc)
        lineas.append('~')
    
    # ===== CIERRE =====
    lineas.append('\\')
    
    return '\n'.join(lineas)


def generar_txt_dte_acepta(datos):
    """
    Genera el contenido de un archivo TXT para el sistema Acepta
    Detecta automáticamente si es factura o boleta y usa el formato correcto
    
    Args:
        datos (dict): Diccionario con la estructura completa del DTE
        
    Estructura esperada del diccionario:
    {
        'documento': {
            'tipo_documento': int (33, 34, 39, 52, 61),
            'folio': int,
            'fecha_emision': date/str,
            'ind_no_rebaja': int (opcional),
            'tipo_despacho': int (opcional),
            'ind_traslado': int (opcional),
            'forma_pago': int (opcional, 1=Contado, 2=Crédito, 3=Sin costo),
            'fecha_vencimiento': date/str (opcional),
            'ind_servicio': int (opcional),
            'timestamp': datetime/str (opcional)
        },
        'emisor': {
            'rut': str,
            'razon_social': str,
            'giro': str,
            'acteco': str (opcional),
            'sucursal': str (opcional),
            'codigo_sucursal': str (opcional),
            'direccion': str (opcional),
            'comuna': str (opcional),
            'ciudad': str (opcional),
            'codigo_vendedor': str (opcional),
            'telefono': str (opcional)
        },
        'receptor': {
            'rut': str,
            'codigo_interno': str (opcional),
            'razon_social': str,
            'giro': str (opcional),
            'contacto': str (opcional),
            'direccion': str (opcional),
            'comuna': str (opcional),
            'ciudad': str (opcional)
        },
        'transporte': {  # Opcional, para guías de despacho
            'patente': str (opcional),
            'rut_transportista': str (opcional),
            'direccion_destino': str (opcional),
            'comuna_destino': str (opcional),
            'ciudad_destino': str (opcional)
        },
        'totales': {
            'monto_neto': Decimal,
            'monto_exento': Decimal (opcional),
            'tasa_iva': Decimal (default 19.00),
            'iva': Decimal,
            'monto_total': Decimal,
            'timestamp': datetime/str (opcional)
        },
        'detalle': [
            {
                'indicador_exencion': int (opcional, 1-6),
                'nombre': str,
                'descripcion': str (opcional),
                'cantidad': Decimal,
                'unidad': str (UN, KG, etc),
                'precio_unitario': Decimal,
                'descuento_pct': Decimal (opcional),
                'monto_descuento': Decimal (opcional),
                'monto_item': Decimal
            }
        ]
    }
    
    Returns:
        str: Contenido del archivo TXT con formato Acepta
    """
    # Validar datos
    es_valido, mensaje = validar_datos_dte_acepta(datos)
    if not es_valido:
        raise ValidationError(f"Error en validación de datos: {mensaje}")
    
    # ✅ Detectar tipo de documento y usar función específica
    tipo_doc = datos.get('documento', {}).get('tipo_documento')
    import logging
    logger = logging.getLogger(__name__)
    
    if tipo_doc in [39, 41]:
        logger.warning(f"🔍 Detectado tipo BOLETA ({tipo_doc}), usando formato específico de boletas")
        return generar_txt_boleta_acepta(datos)
    elif tipo_doc == 61:
        logger.warning(f"🔍 Detectado tipo NOTA DE CRÉDITO ({tipo_doc}), usando formato específico de NC")
        return generar_txt_nota_credito_acepta(datos)
    
    separador = '|'
    lineas = []
    
    # ===== LÍNEA 1: IDENTIFICACIÓN DEL DOCUMENTO =====
    doc = datos['documento']
    
    # DEBUG: Verificar folio en generación de línea
    import logging
    logger = logging.getLogger(__name__)
    folio_raw = doc.get('folio', '')
    folio_str = str(folio_raw)
    logger.warning(f"🔍 DEBUG generar_txt - Folio raw: {folio_raw} (tipo: {type(folio_raw)})")
    logger.warning(f"🔍 DEBUG generar_txt - Folio convertido a str: {folio_str}")
    
    # ===== LÍNEA 1: IdDoc - Formato ACEPTA REAL =====
    fecha_emision = formatear_fecha(doc.get('fecha_emision', ''))
    fecha_vencimiento = formatear_fecha(doc.get('fecha_vencimiento', ''))
    
    # ✅ SIEMPRE poner fecha vencimiento (usar fecha emisión si no hay)
    if not fecha_vencimiento:
        fecha_vencimiento = fecha_emision
    
    linea1 = [
        str(doc.get('tipo_documento', '')),              # 1. Tipo documento
        str(doc.get('folio', '')),                       # 2. Folio
        fecha_emision,                                    # 3. Fecha emisión
        '',                                              # 4. Indicador no rebaja
        str(doc.get('tipo_despacho', '2')),              # 5. Tipo despacho (default 2)
        str(doc.get('ind_traslado', '1')),               # 6. Indicador traslado (default 1)
        str(doc.get('forma_pago', '1')),                 # 7. Forma pago (default 1)
        fecha_vencimiento,                                # 8. Fecha vencimiento
        '}'                                              # 9. ✅ CIERRE CON }
    ]
    
    # DEBUG: Ver la línea 1 completa
    linea1_completa = separador.join(linea1)
    logger.warning(f"🔍 DEBUG - Línea 1 generada: {linea1_completa}")
    
    lineas.append(linea1_completa)
    
    # ===== LÍNEA 2: DATOS DEL EMISOR =====
    emisor = datos['emisor']
    linea2 = [
        formatear_rut(emisor.get('rut', '')),
        limpiar_texto(emisor.get('razon_social', ''), 100),
        limpiar_texto(emisor.get('giro', ''), 80),
        str(emisor.get('acteco', '')),
        limpiar_texto(emisor.get('sucursal', ''), 20),
        str(emisor.get('codigo_sucursal', '')),
        limpiar_texto(emisor.get('direccion', ''), 60),
        limpiar_texto(emisor.get('comuna', ''), 20),
        limpiar_texto(emisor.get('ciudad', ''), 20),
        limpiar_texto(emisor.get('codigo_vendedor', ''), 60) or 'USUARIO',
        '}'  # ✅ CIERRE CON }
    ]
    lineas.append(separador.join(linea2))
    
    # ===== LÍNEA 3: DATOS DEL RECEPTOR =====
    receptor = datos['receptor']
    linea3 = [
        formatear_rut(receptor.get('rut', '')),
        limpiar_texto(receptor.get('codigo_interno', ''), 20),
        limpiar_texto(receptor.get('razon_social', ''), 100),
        limpiar_texto(receptor.get('giro', ''), 40),
        limpiar_texto(receptor.get('contacto', ''), 80),
        limpiar_texto(receptor.get('direccion', ''), 70),
        limpiar_texto(receptor.get('comuna', ''), 20),
        limpiar_texto(receptor.get('ciudad', ''), 20),
        '',  # Ciudad postal
        '}'  # ✅ CIERRE CON }
    ]
    lineas.append(separador.join(linea3))
    
    # ===== LÍNEA 4: DATOS DE TRANSPORTE (Opcional) =====
    transporte = datos.get('transporte', {})
    linea4 = [
        limpiar_texto(transporte.get('patente', ''), 8),
        limpiar_texto(transporte.get('rut_transportista', ''), 20),
        limpiar_texto(transporte.get('direccion_destino', ''), 70),
        limpiar_texto(transporte.get('comuna_destino', ''), 20),
        limpiar_texto(transporte.get('ciudad_destino', ''), 20),
        '}'  # ✅ CIERRE CON }
    ]
    lineas.append(separador.join(linea4))
    
    # ===== LÍNEA 5: TOTALES =====
    # ✅ PROBLEMA 2: EXACTAMENTE 16 pipes después del total
    totales = datos['totales']
    tasa_iva_str = '19' if totales.get('monto_neto', 0) else ''
    
    linea5 = [
        formatear_monto(totales.get('monto_neto', 0)),      # 1. Monto neto
        formatear_monto(totales.get('monto_exento', '')),   # 2. Monto exento
        tasa_iva_str,                                        # 3. Tasa IVA (19)
        formatear_monto(totales.get('iva', 0)),             # 4. IVA
        formatear_monto(totales.get('monto_total', 0)),     # 5. Monto total
        '', '', '', '', '', '', '', '', '', '', '', '',  # ✅ 12 campos más = 16 pipes total
        '}'  # ✅ CIERRE CON }
    ]
    lineas.append(separador.join(linea5))
    
    # ===== SEPARADOR ANTES DE PRODUCTOS =====
    lineas.append('~')
    
    # ===== LÍNEAS 6+: DETALLE DE PRODUCTOS =====
    # ✅ CORREGIDO: Cada producto incluye código al inicio y al final, termina con }
    logger.warning(f"🔍 DEBUG - Procesando {len(datos['detalle'])} productos")
    for index, item in enumerate(datos['detalle'], start=1):
        # Generar código del producto si no existe
        codigo_item = limpiar_texto(item.get('codigo', ''), 35) or limpiar_texto(item.get('sku', ''), 35)
        if not codigo_item:
            codigo_item = f"Item"  # Código genérico si no existe
        
        # ✅ FORMATO REAL: Código AL INICIO del nombre
        nombre_con_codigo = f"{codigo_item} {limpiar_texto(item.get('nombre', ''), 80)}"
        
        # ✅ Formatear cantidad y precio como enteros
        cantidad_val = item.get('cantidad', 0)
        precio_val = item.get('precio_unitario', 0)
        
        # ✅ PROBLEMA 3: EXACTAMENTE 9 campos por producto + cierre }
        linea_detalle = [
            str(item.get('indicador_exencion', '')),     # 1. Indicador exención
            nombre_con_codigo,                            # 2. Código + Nombre
            limpiar_texto(item.get('descripcion', ''), 1000),  # 3. Descripción
            str(int(cantidad_val)) if cantidad_val else '',  # 4. Cantidad (entero)
            limpiar_texto(item.get('unidad', 'UN'), 4),  # 5. Unidad
            str(int(precio_val)) if precio_val else '',  # 6. Precio unitario (entero)
            formatear_decimal(item.get('descuento_pct', ''), 3, 2) if item.get('descuento_pct') else '',  # 7. Desc %
            formatear_monto(item.get('monto_descuento', 0)) if item.get('monto_descuento') else '',  # 8. Monto descuento
            formatear_monto(item.get('monto_item', 0)),  # 9. Monto item
            codigo_item,  # 10. Código producto al FINAL
            '}'  # 11. ✅ CIERRE CON }
        ]
        linea_producto = separador.join(linea_detalle)
        logger.warning(f"🔍 DEBUG - Producto {index}: {nombre_con_codigo[:30]}... → {linea_producto[:50]}...")
        lineas.append(linea_producto)
    
    # ===== PRIMER SEPARADOR =====
    lineas.append('~')
    
    # ===== DESCUENTO GLOBAL (Opcional) =====
    descuento_global = totales.get('descuento_global', 0)
    
    if descuento_global and descuento_global > 0:
        # CON descuento global
        logger.warning(f"✅ Agregando línea de descuento global: {descuento_global}")
        linea_descuento = f"D|Descuento|$|{formatear_monto(descuento_global)}|1|" + "|}"
        lineas.append(linea_descuento)
    
    # ===== SEPARADOR =====
    # ✅ CORRECCIÓN: NO agregar línea vacía |||||||}
    lineas.append('~')
    
    # ===== REFERENCIAS A OTROS DOCUMENTOS (Opcional) =====
    referencias = datos.get('referencias', [])
    logger.warning(f"🔍 DEBUG - Procesando referencias: {len(referencias)} refs")
    
    if referencias and len(referencias) > 0:
        logger.warning(f"🔍 DEBUG - Agregando {len(referencias)} referencias al TXT")
        for idx, ref in enumerate(referencias):
            logger.warning(f"🔍 DEBUG - Referencia {idx+1}: tipo={ref.get('tipo_documento')}, folio={ref.get('folio')}")
            # ✅ Formato correcto: 801|| folio | fecha|| |}
            fecha_ref = formatear_fecha(ref.get('fecha', ''))
            folio_ref = str(ref.get('folio', ''))
            tipo_ref = str(ref.get('tipo_documento', ''))
            
            linea_ref = [
                tipo_ref,  # 1. Tipo documento
                '',  # 2. Campo vacío
                f" {folio_ref} ",  # 3. Folio CON espacios
                f" {fecha_ref}",  # 4. Fecha CON espacio
                '',  # 5. Campo vacío
                '}'  # 6. Cierre
            ]
            linea_ref_completa = separador.join(linea_ref)
            logger.warning(f"🔍 DEBUG - Línea referencia: '{linea_ref_completa}'")
            lineas.append(linea_ref_completa)
    else:
        logger.warning(f"🔍 DEBUG - NO hay referencias para agregar")
    
    # ===== SEPARADOR FINAL ANTES DE INFO =====
    lineas.append('~')
    
    # ===== LÍNEA INFORMACIÓN ADICIONAL =====
    # ✅ PROBLEMA 4 y 5: Monto en letras completo y pipes correctos
    vendedor_codigo = datos.get('emisor', {}).get('codigo_vendedor', '') or 'USUARIO'
    monto_total = totales.get('monto_total', 0)
    
    # Convertir monto a letras (COMPLETO)
    try:
        from num2words import num2words
        # Usar solo 'cardinal' para obtener el número en palabras sin "pesos"
        monto_letras = num2words(int(monto_total), lang='es').upper()
        monto_letras = f"{monto_letras} PESOS"
        # Limpiar formatos no deseados
        monto_letras = monto_letras.replace('  ', ' ')  # Dobles espacios
        monto_letras = monto_letras.strip()
        logger.warning(f"✅ Monto convertido a letras: {monto_letras}")
    except Exception as e:
        logger.warning(f"⚠️ Error al convertir monto a letras: {e}")
        monto_letras = f"{int(monto_total)} PESOS"
    
    # ✅ CORRECCIÓN: Línea final con formato correcto
    # Formato real: vendedor|||observacion  |||||||impresora|4|}
    # Ejemplo: King Angulo|||CINCO MILLONES...PESOS (Total Art 51)  |||||||FACTURA MATTA 2438|4|}
    info_adicional = [
        vendedor_codigo,  # 1. Código vendedor
        '',  # 2. Campo vacío
        '',  # 3. Campo vacío
        f"{monto_letras}  ",  # 4. Observación/Monto con 2 espacios al final
        '', '', '', '', '', '', '',  # 5-11. 7 campos vacíos
        'HP LaserJet Professional P1102w',  # 12. Impresora
        '4',  # 13. Copias
        '}'  # 14. Cierre
    ]
    logger.warning(f"🔍 DEBUG - Línea final: vendedor={vendedor_codigo}, monto={monto_letras}")
    lineas.append(separador.join(info_adicional))
    
    # ===== LÍNEAS FINALES =====
    # ✅ CORREGIDO: ~ y luego \
    lineas.append('~')
    lineas.append('\\')
    
    # Unir todas las líneas con salto de línea
    contenido_txt = '\n'.join(lineas)
    
    return contenido_txt


def generar_dte_desde_ticket(ticket_id, tipo_dte='BOLETA_ELECTRONICA', sucursal_id=None):
    """
    Genera un archivo TXT de Acepta desde un Ticket de venta
    
    Args:
        ticket_id (int): ID o correlativo del ticket
        tipo_dte (str): BOLETA_ELECTRONICA o FACTURA_ELECTRONICA
        sucursal_id (int): ID de la sucursal (opcional)
        
    Returns:
        tuple: (contenido_txt, nombre_archivo)
    """
    from .models import Ticket, Empresa
    from django.shortcuts import get_object_or_404
    from decimal import Decimal
    
    # Intentar obtener ticket por ID primero, si falla intentar por correlativo
    try:
        ticket = Ticket.objects.get(id=ticket_id)
    except Ticket.DoesNotExist:
        # Si no existe por ID, intentar por correlativo y sucursal
        if sucursal_id:
            ticket = get_object_or_404(Ticket, correlativo=ticket_id, sucursal_id=sucursal_id)
        else:
            raise ValidationError(f"No se encontró el ticket {ticket_id}")
    
    # Obtener empresa (emisor)
    empresa = ticket.sucursal.empresa
    
    # Obtener correlativo para el tipo de DTE
    from .models import Correlativo
    tipo_correlativo = tipo_dte.replace('_', ' ')  # BOLETA ELECTRONICA
    
    correlativo_obj = Correlativo.objects.filter(
        sucursal=ticket.sucursal,
        tipo_dte=tipo_correlativo
    ).first()
    
    if not correlativo_obj:
        raise ValidationError(f"No hay correlativo configurado para {tipo_correlativo} en {ticket.sucursal}")
    
    # Obtener siguiente folio
    siguiente_folio = correlativo_obj.numero_actual + 1
    
    # Preparar datos del documento
    documento = {
        'tipo_documento': 39 if 'BOLETA' in tipo_dte else 33,
        'folio': siguiente_folio,
        'fecha_emision': ticket.fecha.strftime('%Y-%m-%d'),
        'forma_pago': 1,  # Contado
        'timestamp': timezone.now().strftime('%Y-%m-%dT%H:%M:%S')
    }
    
    # Preparar datos del emisor
    emisor = {
        'rut': empresa.rut,
        'razon_social': empresa.razon_social,
        'giro': empresa.giro,
        'acteco': empresa.acteco or '',
        'direccion': empresa.direccion,
        'comuna': empresa.comuna,
        'ciudad': empresa.ciudad,
        'codigo_vendedor': ticket.responsable or 'USUARIO',
        'telefono': empresa.contacto1 or ''
    }
    
    # Preparar datos del receptor
    if 'FACTURA' in tipo_dte and ticket.cliente_rut:
        # Factura con cliente específico
        receptor = {
            'rut': ticket.cliente_rut,
            'razon_social': ticket.cliente_nombre or 'CLIENTE',
            'giro': ticket.cliente_giro or '',
            'direccion': ticket.cliente_direccion or '',
            'comuna': ticket.cliente_comuna or '',
            'ciudad': ticket.cliente_ciudad or ''
        }
    else:
        # Boleta o consumidor final
        receptor = {
            'rut': '66666666-6',
            'razon_social': 'CONSUMIDOR FINAL',
            'giro': '',
            'direccion': '',
            'comuna': '',
            'ciudad': ''
        }
    
    # Preparar productos
    detalle = []
    for item in ticket.ticket_productos.all():
        producto_talla = item.ProductoTalla
        producto = producto_talla.producto
        
        detalle.append({
            'codigo': producto.codigo or f'PROD{producto.id}',
            'sku': producto.sku or '',
            'nombre': f"{producto.nombre} {producto_talla.marca.nombre if producto_talla.marca else ''} {producto_talla.talla}",
            'descripcion': producto.descripcion or '',
            'cantidad': item.stock,
            'unidad': 'UN',
            'precio_unitario': item.precio,
            'descuento_pct': float(item.porcentaje_descuento) if item.porcentaje_descuento else 0,
            'monto_descuento': item.descuento_unitario * item.stock if item.descuento_unitario else 0,
            'monto_item': item.subtotal
        })
    
    # Calcular totales
    subtotal = sum(item['monto_item'] for item in detalle)
    descuento_global = ticket.descuento or 0
    neto = subtotal - descuento_global
    iva = int(neto * Decimal('0.19'))
    total = neto + iva
    
    totales = {
        'monto_neto': neto,
        'monto_exento': 0,
        'tasa_iva': 19,
        'iva': iva,
        'monto_total': total,
        'descuento_global': descuento_global
    }
    
    # Preparar referencias (si hay)
    referencias = []
    if ticket.referencia_tipo and ticket.referencia_folio:
        referencias.append({
            'tipo_documento': ticket.referencia_tipo,
            'folio': ticket.referencia_folio,
            'fecha': ticket.referencia_fecha.strftime('%Y-%m-%d') if ticket.referencia_fecha else '',
            'razon': ''
        })
    
    # Estructura completa para generar TXT
    datos = {
        'documento': documento,
        'emisor': emisor,
        'receptor': receptor,
        'totales': totales,
        'detalle': detalle,
        'referencias': referencias
    }
    
    # Generar TXT
    contenido_txt = generar_txt_dte_acepta(datos)
    
    # Actualizar ticket
    ticket.tipo_dte = tipo_dte
    ticket.folio_dte = siguiente_folio
    ticket.dte_generado = True
    ticket.dte_fecha_generacion = timezone.now()
    ticket.save()
    
    # Actualizar correlativo
    correlativo_obj.numero_actual = siguiente_folio
    correlativo_obj.save()
    
    # Nombre del archivo
    tipo_codigo = documento['tipo_documento']
    fecha_str = ticket.fecha.strftime('%Y%m%d')
    nombre_archivo = f"dte_{tipo_codigo}_{siguiente_folio}_{fecha_str}.txt"
    
    return contenido_txt, nombre_archivo


@require_POST
@login_required
def generar_txt_acepta_api(request):
    """
    API endpoint para generar archivo TXT de Acepta desde datos JSON
    
    Recibe:
        JSON con estructura de datos del DTE
        
    Retorna:
        - Success: Archivo TXT descargable
        - Error: JSON con detalles del error
    """
    try:
        # Parsear datos del request
        datos = json.loads(request.body)
        
        # DEBUG: Verificar qué valor de folio estamos recibiendo
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"🔍 DEBUG - Folio recibido: {datos.get('documento', {}).get('folio')}")
        logger.warning(f"🔍 DEBUG - Tipo de dato: {type(datos.get('documento', {}).get('folio'))}")
        logger.warning(f"🔍 DEBUG - Datos documento completos: {datos.get('documento')}")
        logger.warning(f"🔍 DEBUG - Descuento global: {datos.get('totales', {}).get('descuento_global', 0)}")
        logger.warning(f"🔍 DEBUG - Referencias recibidas: {datos.get('referencias', [])}")
        logger.warning(f"🔍 DEBUG - Cantidad de referencias: {len(datos.get('referencias', []))}")
        
        # Generar contenido TXT
        contenido_txt = generar_txt_dte_acepta(datos)
        
        # Crear nombre del archivo
        tipo_doc = datos['documento'].get('tipo_documento', 'XX')
        folio = datos['documento'].get('folio', '0000')
        fecha = formatear_fecha(datos['documento'].get('fecha_emision', timezone.now().date()))
        nombre_archivo = f"dte_{tipo_doc}_{folio}_{fecha.replace('-', '')}.txt"
        
        # Retornar como archivo descargable
        response = HttpResponse(contenido_txt, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
        
        return response
        
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'JSON inválido'
        }, status=400)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar archivo TXT: {str(e)}'
        }, status=500)


@require_POST
@login_required
def generar_dte_desde_ticket_api(request):
    """
    API para generar DTE desde un Ticket de venta del POS
    
    Recibe:
        JSON con { ticket_id: int, tipo_dte: str }
        
    Retorna:
        Archivo TXT descargable
    """
    try:
        data = json.loads(request.body)
        ticket_id = data.get('ticket_id')
        tipo_dte = data.get('tipo_dte', 'BOLETA_ELECTRONICA')
        
        if not ticket_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de ticket requerido'
            }, status=400)
        
        # Obtener sucursal de la sesión
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        # Generar DTE (ticket_id puede ser ID o correlativo)
        contenido_txt, nombre_archivo = generar_dte_desde_ticket(ticket_id, tipo_dte, sucursal_id)
        
        # Retornar como archivo descargable
        response = HttpResponse(contenido_txt, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
        
        return response
        
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar DTE: {str(e)}'
        }, status=500)


@require_POST
@login_required
def generar_txt_desde_dte_existente(request):
    """
    Genera un archivo TXT de Acepta a partir de un DTE existente en la base de datos
    
    Recibe:
        JSON con { dte_id: int }
        
    Retorna:
        Archivo TXT descargable
    """
    try:
        data = json.loads(request.body)
        dte_id = data.get('dte_id')
        
        if not dte_id:
            return JsonResponse({
                'success': False,
                'error': 'ID de DTE requerido'
            }, status=400)
        
        # Obtener el DTE
        dte = get_object_or_404(Dte, id=dte_id)
        
        # Verificar permisos
        empresa_actual_id = request.session.get('idEmpresaActual')
        if dte.emisor_id != empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para este DTE'
            }, status=403)
        
        # Construir diccionario de datos desde el DTE
        datos = {
            'documento': {
                'tipo_documento': dte.tipo_documento,
                'folio': dte.numero_dte,
                'fecha_emision': dte.fecha_emision,
                'fecha_vencimiento': dte.fecha_vencimiento,
                'forma_pago': 1 if dte.fecha_vencimiento == dte.fecha_emision else 2,  # 1=Contado, 2=Crédito
                'timestamp': timezone.now()
            },
            'emisor': {
                'rut': dte.emisor.rut,
                'razon_social': dte.emisor.nombre,
                'giro': dte.emisor.giro or '',
                'direccion': dte.emisor.direccion or '',
                'comuna': dte.emisor.comuna or '',
                'ciudad': dte.emisor.ciudad or '',
                'telefono': dte.emisor.telefono or ''
            },
            'receptor': {
                'rut': dte.receptor.rut,
                'razon_social': dte.receptor.nombre,
                'giro': dte.receptor.giro or '',
                'direccion': dte.receptor.direccion or '',
                'comuna': dte.receptor.comuna or '',
                'ciudad': dte.receptor.ciudad or ''
            },
            'totales': {
                'monto_neto': dte.subtotal - (dte.descuento_global or 0),
                'monto_exento': 0,
                'tasa_iva': Decimal('19.00'),
                'iva': dte.iva,
                'monto_total': dte.total
            },
            'detalle': []
        }
        
        # Agregar productos
        for dte_producto in dte.dte_productos.select_related('productoTalla__producto', 'productoTalla__talla'):
            datos['detalle'].append({
                'nombre': dte_producto.productoTalla.producto.nombre,
                'descripcion': f"{dte_producto.productoTalla.producto.nombre} - Talla {dte_producto.productoTalla.talla.nombre if dte_producto.productoTalla.talla else 'Única'}",
                'cantidad': dte_producto.cantidad,
                'unidad': 'UN',
                'precio_unitario': dte_producto.precio_unitario,
                'descuento_pct': 0,
                'monto_descuento': dte_producto.descuento_unitario * dte_producto.cantidad,
                'monto_item': dte_producto.cantidad * (dte_producto.precio_unitario - dte_producto.descuento_unitario),
                'sku': dte_producto.productoTalla.sku,  # Agregar SKU
                'codigo': dte_producto.productoTalla.sku  # Código del producto
            })
        
        # Generar TXT
        contenido_txt = generar_txt_dte_acepta(datos)
        
        # Crear nombre del archivo
        nombre_archivo = f"dte_{dte.tipo_documento}_{dte.numero_dte}_{dte.fecha_emision.strftime('%Y%m%d')}.txt"
        
        # Retornar como archivo descargable
        response = HttpResponse(contenido_txt, content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{nombre_archivo}"'
        
        return response
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'JSON inválido'
        }, status=400)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar archivo TXT: {str(e)}'
        }, status=500)