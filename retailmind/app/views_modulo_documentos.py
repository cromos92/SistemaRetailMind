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
    Dte, Dte_Productos, Dte_Detalle_Pago, Correlativo, Empresa, Sucursal, EmpresaUser,
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
        for dte_producto in dte.dte_productos.select_related('productoTalla__producto'):
            pt = dte_producto.productoTalla
            productos.append({
                'id': dte_producto.id,
                'sku': pt.sku if pt else None,
                'nombre_producto': pt.producto.articulo if pt and pt.producto else dte_producto.descripcion,
                'talla': pt.talla if pt else 'Sin talla',
                'cantidad': dte_producto.stock,
                'precio_unitario': float(dte_producto.precio),
                'descuento_monto': float(dte_producto.descuento_monto or 0),
                'total_linea': float(dte_producto.monto_item or (dte_producto.precio * dte_producto.stock))
            })
        
        # Obtener pagos si existen
        pagos = []
        for pago in Dte_Detalle_Pago.objects.filter(dte=dte):
            pagos.append({
                'id': pago.id,
                'monto': float(pago.monto),
                'metodo_pago': pago.metodo_pago,
                'voucher': pago.voucher or '',
                'tipo_tarjeta': pago.tipo_tarjeta or '',
                'notas': pago.notas or ''
            })
        
        # Obtener movimientos asociados
        movimientos = []
        for movimiento in dte.dte_movimientos.select_related('producto_talla__producto', 'sucursal_origen', 'sucursal_destino'):
            movimientos.append({
                'id': movimiento.id,
                'concepto': movimiento.concepto,
                'tipo_movimiento': movimiento.tipo_movimiento,
                'cantidad': movimiento.cantidad,
                'producto': movimiento.producto_talla.producto.articulo if movimiento.producto_talla else '',
                'sku': movimiento.producto_talla.sku if movimiento.producto_talla else '',
                'sucursal_origen': movimiento.sucursal_origen.alias if movimiento.sucursal_origen else '',
                'sucursal_destino': movimiento.sucursal_destino.alias if movimiento.sucursal_destino else '',
                'estado': movimiento.estado,
                'fecha_creacion': movimiento.fecha_creacion.strftime('%d/%m/%Y %H:%M')
            })
        
        monto_neto = float(dte.monto_neto or 0)
        monto_con_iva = float(dte.monto_con_iva or 0)
        iva = monto_con_iva - monto_neto

        dte_data = {
            'id': dte.id,
            'numero_documento': dte.numero_documento,
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
            'monto_neto': monto_neto,
            'descuento': float(dte.descuento or 0),
            'iva': iva,
            'monto_con_iva': monto_con_iva,
            'estado_dte': dte.estado_dte,
            'estado_pago': dte.estado_pago,
            'fecha_recepcion': dte.fecha_recepcion.strftime('%d/%m/%Y') if dte.fecha_recepcion else None,
            'referencias': dte.referencias or '',
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
                    'nombre': pt.producto.articulo,
                    'categoria': pt.producto.categoria.nombre if pt.producto.categoria else '',
                    'marca': pt.producto.atributo1.valor if pt.producto.atributo1 else '',
                    'talla': pt.talla if pt.talla else 'Sin talla',
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
# NOTA: Esta función fue eliminada porque está duplicada en views.py
# La función activa está en views.py línea 8754 y es usada en urls.py
# Esta versión estaba HUÉRFANA (no se usaba en ninguna URL)


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
    sucursal_id = request.session.get('idSucursalActual')
    sucursal_actual = None
    if sucursal_id:
        try:
            from .models import Sucursal
            sucursal_actual = Sucursal.objects.get(id=sucursal_id)
        except Exception:
            pass
    context = {
        'qz_config': {
            'habilitado': getattr(sucursal_actual, 'usar_qz_tray', False) if sucursal_actual else False,
            'nombre_impresora': (
                getattr(sucursal_actual, 'nombre_impresora_termica', 'EPSON TM-T20II') or 'EPSON TM-T20II'
            ) if sucursal_actual else 'EPSON TM-T20II',
        },
    }
    return render(request, 'vistas/modulo_administracion/gestion_creditos.html', context)


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
    Limpia un texto eliminando caracteres especiales problemáticos para Acepta TXT
    - Elimina acentos (á, é, í, ó, ú → a, e, i, o, u)
    - Reemplaza Ñ por N
    - Elimina caracteres especiales
    
    Args:
        texto (str): Texto a limpiar
        max_length (int): Longitud máxima permitida
        
    Returns:
        str: Texto limpio sin caracteres especiales
    """
    if not texto:
        return ''
    
    # Convertir a string
    texto = str(texto)
    
    # === REEMPLAZAR ACENTOS Y CARACTERES ESPECIALES ===
    # Vocales con acento minúsculas
    texto = texto.replace('á', 'a')
    texto = texto.replace('é', 'e')
    texto = texto.replace('í', 'i')
    texto = texto.replace('ó', 'o')
    texto = texto.replace('ú', 'u')
    texto = texto.replace('ü', 'u')
    
    # Vocales con acento mayúsculas
    texto = texto.replace('Á', 'A')
    texto = texto.replace('É', 'E')
    texto = texto.replace('Í', 'I')
    texto = texto.replace('Ó', 'O')
    texto = texto.replace('Ú', 'U')
    texto = texto.replace('Ü', 'U')
    
    # Ñ → N
    texto = texto.replace('ñ', 'n')
    texto = texto.replace('Ñ', 'N')
    
    # Otros caracteres especiales comunes
    texto = texto.replace('ª', 'a')
    texto = texto.replace('º', 'o')
    texto = texto.replace('°', '')
    texto = texto.replace('´', '')
    texto = texto.replace('`', '')
    texto = texto.replace('"', '')
    texto = texto.replace('"', '')
    texto = texto.replace(''', '')
    texto = texto.replace(''', '')
    texto = texto.replace('«', '')
    texto = texto.replace('»', '')
    
    # === REEMPLAZAR CARACTERES DE CONTROL ===
    texto = texto.replace('|', '')  # El pipe es el separador de Acepta
    texto = texto.replace('\n', ' ')
    texto = texto.replace('\r', ' ')
    texto = texto.replace('\t', ' ')
    texto = texto.replace('\\', '')
    texto = texto.replace('~', '')  # Caracteres especiales de Acepta
    texto = texto.replace('}', '')
    texto = texto.replace('{', '')
    
    # Eliminar espacios múltiples
    import re
    texto = re.sub(r'\s+', ' ', texto)
    
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


# =====================================================================
# PARSER DE TXT ACEPTA
# =====================================================================

def _campo_o_none(valor):
    """Convierte campo vacío del TXT en None, numérico en int/float según corresponda."""
    if valor is None or valor.strip() == '':
        return None
    valor = valor.strip()
    try:
        if '.' in valor:
            return float(valor)
        return int(valor)
    except ValueError:
        return valor


def parsear_txt_acepta(contenido_txt):
    """
    Parsea un archivo TXT en formato Acepta y retorna un dict con la misma
    estructura que usan las funciones de generación.

    Soporta:
      - Boleta  (39/41)  — sin transporte, detalle sin campos descuento por ítem
      - Factura (33/34/43/52) — con transporte, detalle con descuento_pct/monto_descuento
      - Nota de Crédito (61) — similar a factura

    Returns:
        dict con claves: documento, emisor, receptor, transporte (factura),
                         totales, detalle, descuentos_recargos, referencias, observaciones
    """
    lineas_raw = contenido_txt.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    lineas = [l for l in lineas_raw if l.strip()]

    # Separar en secciones por ~
    secciones = []
    seccion_actual = []
    for linea in lineas:
        if linea.strip() == '~':
            secciones.append(seccion_actual)
            seccion_actual = []
        elif linea.strip() == '\\':
            secciones.append(seccion_actual)
            break
        else:
            seccion_actual.append(linea)
    else:
        if seccion_actual:
            secciones.append(seccion_actual)

    def split_campos(linea):
        """Divide por pipe y elimina cierre }"""
        campos = linea.split('|')
        if campos and campos[-1].strip() == '}':
            campos = campos[:-1]
        if campos and campos[-1].strip() == '':
            campos = campos[:-1]
        return campos

    resultado = {
        'documento': {},
        'emisor': {},
        'receptor': {},
        'transporte': {},
        'totales': {},
        'detalle': [],
        'descuentos_recargos': [],
        'referencias': [],
        'observaciones': '',
    }

    if not secciones or not secciones[0]:
        return resultado

    # --- SECCION CABECERA (antes del primer ~) ---
    cabecera = secciones[0]

    # Linea 1: documento
    if len(cabecera) >= 1:
        c = split_campos(cabecera[0])
        tipo_doc = _campo_o_none(c[0]) if len(c) > 0 else None
        resultado['documento'] = {
            'tipo_documento': tipo_doc,
            'folio': _campo_o_none(c[1]) if len(c) > 1 else None,
            'fecha_emision': _campo_o_none(c[2]) if len(c) > 2 else None,
        }

    es_boleta = tipo_doc in (39, 41)
    es_nc = tipo_doc == 61

    # Linea 2: emisor
    if len(cabecera) >= 2:
        c = split_campos(cabecera[1])
        resultado['emisor'] = {
            'rut': _campo_o_none(c[0]) if len(c) > 0 else None,
            'razon_social': _campo_o_none(c[1]) if len(c) > 1 else None,
            'giro': _campo_o_none(c[2]) if len(c) > 2 else None,
            'acteco': _campo_o_none(c[3]) if len(c) > 3 else None,
        }

    # Linea 3: receptor
    if len(cabecera) >= 3:
        c = split_campos(cabecera[2])
        resultado['receptor'] = {
            'rut': _campo_o_none(c[0]) if len(c) > 0 else None,
            'razon_social': _campo_o_none(c[2]) if len(c) > 2 else None,
            'giro': _campo_o_none(c[3]) if len(c) > 3 else None,
        }

    if es_boleta:
        # Boleta: linea 4 = totales (no hay transporte)
        if len(cabecera) >= 4:
            c = split_campos(cabecera[3])
            resultado['totales'] = {
                'monto_total': _campo_o_none(c[1]) if len(c) > 1 else None,
            }
    else:
        # Factura/NC: linea 4 = transporte, linea 5 = totales
        if len(cabecera) >= 4:
            c = split_campos(cabecera[3])
            resultado['transporte'] = {
                'patente': _campo_o_none(c[0]) if len(c) > 0 else None,
                'rut_transportista': _campo_o_none(c[1]) if len(c) > 1 else None,
            }
        if len(cabecera) >= 5:
            c = split_campos(cabecera[4])
            resultado['totales'] = {
                'monto_neto': _campo_o_none(c[0]) if len(c) > 0 else None,
                'monto_exento': _campo_o_none(c[1]) if len(c) > 1 else None,
                'tasa_iva': _campo_o_none(c[2]) if len(c) > 2 else None,
                'iva': _campo_o_none(c[3]) if len(c) > 3 else None,
                'monto_total': _campo_o_none(c[4]) if len(c) > 4 else None,
            }

    # --- SECCIONES DESPUES DEL PRIMER ~ ---
    # Seccion 1: detalle productos
    if len(secciones) > 1:
        for linea in secciones[1]:
            c = split_campos(linea)
            if not c:
                continue
            if es_boleta:
                # INT1|codigo||nombre||cantidad|unidad|precio|monto|}
                resultado['detalle'].append({
                    'tipo': _campo_o_none(c[0]) if len(c) > 0 else None,
                    'codigo': _campo_o_none(c[1]) if len(c) > 1 else None,
                    'nombre': _campo_o_none(c[3]) if len(c) > 3 else None,
                    'cantidad': _campo_o_none(c[5]) if len(c) > 5 else None,
                    'unidad': _campo_o_none(c[6]) if len(c) > 6 else None,
                    'precio_unitario': _campo_o_none(c[7]) if len(c) > 7 else None,
                    'monto_item': _campo_o_none(c[8]) if len(c) > 8 else None,
                })
            else:
                # ind_exe|nombre|desc|qty|unidad|precio|dcto_pct|dcto_monto|monto|codigo|}
                resultado['detalle'].append({
                    'indicador_exencion': _campo_o_none(c[0]) if len(c) > 0 else None,
                    'nombre': _campo_o_none(c[1]) if len(c) > 1 else None,
                    'descripcion': _campo_o_none(c[2]) if len(c) > 2 else None,
                    'cantidad': _campo_o_none(c[3]) if len(c) > 3 else None,
                    'unidad': _campo_o_none(c[4]) if len(c) > 4 else None,
                    'precio_unitario': _campo_o_none(c[5]) if len(c) > 5 else None,
                    'descuento_pct': _campo_o_none(c[6]) if len(c) > 6 else None,
                    'monto_descuento': _campo_o_none(c[7]) if len(c) > 7 else None,
                    'monto_item': _campo_o_none(c[8]) if len(c) > 8 else None,
                    'codigo': _campo_o_none(c[9]) if len(c) > 9 else None,
                })

    # Seccion 2: descuentos/recargos globales (factura) o observaciones (boleta)
    if len(secciones) > 2:
        for linea in secciones[2]:
            c = split_campos(linea)
            if not c:
                continue
            tpo_mov = (c[0] or '').strip() if len(c) > 0 else ''
            if tpo_mov in ('D', 'R'):
                resultado['descuentos_recargos'].append({
                    'tpo_mov': tpo_mov,
                    'glosa_dr': _campo_o_none(c[1]) if len(c) > 1 else None,
                    'tpo_valor': _campo_o_none(c[2]) if len(c) > 2 else None,
                    'valor_dr': _campo_o_none(c[3]) if len(c) > 3 else None,
                    'ind_exe_dr': _campo_o_none(c[4]) if len(c) > 4 else None,
                })
            else:
                resultado['observaciones'] = linea

    # Secciones restantes: referencias, observaciones
    for sec_idx in range(3, len(secciones)):
        for linea in secciones[sec_idx]:
            c = split_campos(linea)
            if not c:
                continue
            first = (c[0] or '').strip() if c else ''
            if first.isdigit() and int(first) in (33, 34, 39, 41, 52, 61):
                resultado['referencias'].append({
                    'tipo_documento': _campo_o_none(c[0]),
                    'folio': _campo_o_none(c[2]) if len(c) > 2 else None,
                    'fecha': _campo_o_none(c[3]) if len(c) > 3 else None,
                })
            elif first in ('D', 'R'):
                resultado['descuentos_recargos'].append({
                    'tpo_mov': first,
                    'glosa_dr': _campo_o_none(c[1]) if len(c) > 1 else None,
                    'tpo_valor': _campo_o_none(c[2]) if len(c) > 2 else None,
                    'valor_dr': _campo_o_none(c[3]) if len(c) > 3 else None,
                    'ind_exe_dr': _campo_o_none(c[4]) if len(c) > 4 else None,
                })
            elif not resultado['observaciones'] and len(c) >= 3:
                resultado['observaciones'] = linea

    return resultado


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
    # ✅ Usar alias de sucursal en lugar de codigo_vendedor
    alias_sucursal = limpiar_texto(emisor.get('sucursal', ''), 60) or emisor.get('codigo_vendedor', '') or 'USUARIO'
    linea2 = [
        formatear_rut(emisor.get('rut', '')),
        limpiar_texto(emisor.get('razon_social', ''), 100),
        limpiar_texto(emisor.get('giro', ''), 80),
        str(emisor.get('acteco', '')),
        '',  '', '',  # Campos vacíos
        limpiar_texto(emisor.get('direccion', ''), 60),
        limpiar_texto(emisor.get('comuna', ''), 20),
        limpiar_texto(emisor.get('ciudad', ''), 20),
        alias_sucursal,  # ✅ CAMBIO: Usar alias de sucursal
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
    # ✅ Usar alias de sucursal en lugar de codigo_vendedor
    vendedor_codigo = emisor.get('sucursal', '') or emisor.get('codigo_vendedor', '') or 'USUARIO'
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
    # Formato: tipo|codigo||nombre_con_sku||cantidad|unidad|precio|monto|}
    # El campo 'nombre' (posición 4) tiene máx 80 chars en Acepta.
    # Se concatena SKU + nombre para que se visualice en la boleta impresa
    # (igual que hace la factura electrónica). Se respeta el límite total de 80 chars.
    MAX_NOMBRE_BOLETA = 80
    for index, item in enumerate(datos['detalle'], start=1):
        # SKU limitado a 20 chars para dejar espacio al nombre dentro de los 80 totales.
        codigo_item = limpiar_texto(item.get('codigo', ''), 20) or limpiar_texto(item.get('sku', ''), 20) or 'PROD001'
        # Nombre con el espacio restante (80 - largo SKU - 1 espacio separador)
        espacio_nombre = max(1, MAX_NOMBRE_BOLETA - len(codigo_item) - 1)
        nombre = limpiar_texto(item.get('nombre', ''), espacio_nombre)
        nombre_con_sku = f"{codigo_item} {nombre}".strip()[:MAX_NOMBRE_BOLETA]
        cantidad_val = int(item.get('cantidad', 0))
        precio_val = int(item.get('precio_unitario', 0))
        monto_val = int(item.get('monto_item', 0))

        linea_prod = [
            'INT1',           # Tipo interno
            codigo_item,      # SKU (campo 2, también queda en el estructurado)
            '',               # Desc vacía
            nombre_con_sku,   # Nombre visible en la impresión = "SKU Nombre"
            '',               # Campo vacío
            str(cantidad_val),
            limpiar_texto(item.get('unidad', 'UN'), 4),
            str(precio_val),
            str(monto_val),
            '}'
        ]
        lineas.append(separador.join(linea_prod))
    
    # ===== SEPARADOR =====
    lineas.append('~')
    
    # ===== DESCUENTOS / RECARGOS GLOBALES (Tabla 4 Boleta) =====
    # Debe ir ANTES de observaciones en formato Acepta
    descuentos_recargos = datos.get('descuentos_recargos', [])
    descuento_global = totales.get('descuento_global', 0)

    if descuentos_recargos:
        for dr in descuentos_recargos:
            linea_dr = separador.join([
                str(dr.get('tpo_mov', 'D')),
                limpiar_texto(str(dr.get('glosa_dr', 'Descuento')), 45),
                str(dr.get('tpo_valor', '$')),
                formatear_monto(dr.get('valor_dr', 0)),
                str(dr.get('ind_exe_dr', '')) if dr.get('ind_exe_dr') else '',
                '}'
            ])
            lineas.append(linea_dr)
        lineas.append('~')
    elif descuento_global and descuento_global > 0:
        linea_desc = separador.join(['D', 'Descuento Global', '$', formatear_monto(descuento_global), '', '}'])
        lineas.append(linea_desc)
        lineas.append('~')
    
    # ===== OBSERVACIONES CON FORMATO ESPECIAL =====
    vendedor_codigo = emisor.get('sucursal', '') or emisor.get('codigo_vendedor', '') or 'USUARIO'
    vendedor_nombre = emisor.get('nombre_vendedor', '') or 'Sin vendedor'
    correlativo = doc.get('folio', '')
    correlativo_ticket = emisor.get('correlativo_ticket', '')
    metodos_pago = emisor.get('metodos_pago', '')
    
    nombre_impresora = emisor.get('nombre_impresora_boleta', 'boleta') or 'boleta'

    # La observación impresa en la boleta ahora incluye información enriquecida de pagos
    # (tipo de tarjeta, autorización Transbank, terminal/operación).
    # Se limita el largo total a ~400 chars como margen seguro del campo observación de Acepta.
    MAX_OBSERVACION = 400
    observacion = f"^ Vendedor: {vendedor_nombre} (Cod: {vendedor_codigo}) ^ Ticket: {correlativo_ticket} ^ DTE: {correlativo} ^ Pago: {metodos_pago} "
    if len(observacion) > MAX_OBSERVACION:
        observacion = observacion[:MAX_OBSERVACION - 3] + '...'
    
    linea_obs = [
        vendedor_codigo,
        '', '',
        observacion,
        '', '', '',
        nombre_impresora,
        '4',
        '}'
    ]
    lineas.append(separador.join(linea_obs))
    
    # ===== SEPARADOR =====
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
    elif tipo_doc == 52:
        logger.warning(f"🔍 Detectado tipo GUÍA DE DESPACHO ({tipo_doc}), usando formato de factura")
        # Guía de Despacho usa el mismo formato que Factura, solo cambia el tipo
        # Se procesa con el código de factura normal
    
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
    # ✅ Usar alias de sucursal en lugar de codigo_vendedor
    alias_sucursal = limpiar_texto(emisor.get('sucursal', ''), 60) or emisor.get('codigo_vendedor', '') or 'USUARIO'
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
        alias_sucursal,  # ✅ CAMBIO: Usar alias de sucursal
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
    
    # ===== DESCUENTOS / RECARGOS GLOBALES (Tabla 3) =====
    descuentos_recargos = datos.get('descuentos_recargos', [])
    descuento_global = totales.get('descuento_global', 0)

    if descuentos_recargos:
        for dr in descuentos_recargos:
            linea_dr = separador.join([
                str(dr.get('tpo_mov', 'D')),
                limpiar_texto(str(dr.get('glosa_dr', 'Descuento')), 45),
                str(dr.get('tpo_valor', '$')),
                formatear_monto(dr.get('valor_dr', 0)),
                str(dr.get('ind_exe_dr', '')) if dr.get('ind_exe_dr') else '',
                '}'
            ])
            lineas.append(linea_dr)
    elif descuento_global and descuento_global > 0:
        linea_descuento = separador.join(['D', 'Descuento', '$', formatear_monto(descuento_global), '1', '}'])
        lineas.append(linea_descuento)

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
    # ✅ Usar alias de sucursal en lugar de código vendedor
    vendedor_codigo = datos.get('emisor', {}).get('sucursal', '') or datos.get('emisor', {}).get('codigo_vendedor', '') or 'USUARIO'
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
    
    total_productos = sum(int(item.get('cantidad') or 0) for item in datos.get('detalle', []))
    observaciones_generales = datos.get('observaciones_adicionales') or datos.get('observaciones') or ''
    observaciones_generales = limpiar_texto(observaciones_generales, 200)
    observacion_texto = ''
    if tipo_doc == 33 and observaciones_generales:
        observacion_texto = f"  {observaciones_generales}"

    info_texto = f"{monto_letras}  total Productos: {total_productos}{observacion_texto}"
    info_texto = limpiar_texto(info_texto, 1000)
    
    # ✅ CORRECCIÓN: Línea final con formato correcto
    # Formato real: vendedor|||observacion  |||||||impresora|4|}
    # Ejemplo: King Angulo|||CINCO MILLONES...PESOS (Total Art 51)  |||||||FACTURA MATTA 2438|4|}
    # ✅ Obtener nombre de impresora para FACTURAS desde configuración de sucursal
    nombre_impresora_factura = datos.get('emisor', {}).get('nombre_impresora_factura', 'factura') or 'factura'
    
    info_adicional = [
        vendedor_codigo,  # 1. Código vendedor
        '',  # 2. Campo vacío
        '',  # 3. Campo vacío
        info_texto,  # 4. Observación/Monto con info adicional
        '', '', '', '', '', '', '',  # 5-11. 7 campos vacíos
        nombre_impresora_factura,  # 12. Impresora (configurable por sucursal)
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
    
    # Validar que no sea BOLETA PAPEL (no genera TXT)
    if tipo_dte == 'BOLETA_PAPEL' or tipo_dte == 'BOLETA PAPEL':
        raise ValidationError('Las Boletas de Papel no generan archivo TXT')
    
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
    # Determinar forma de pago para el DTE
    # 1=Contado (pago inmediato), 2=Crédito (pago diferido), 3=Sin costo
    # Primero se intenta leer el valor que el usuario eligió en el POS (guardado en observaciones_adicionales).
    # Si no está almacenado, se auto-detecta desde los métodos de pago del ticket.
    METODOS_CREDITO_DTE = {'CREDITO_TRABAJADOR', 'CREDITO_EXTERNO', 'CONVENIO', 'ORDEN_COMPRA'}
    forma_pago_dte = None
    try:
        import json as _json
        notas = _json.loads(ticket.observaciones_adicionales or '{}')
        if isinstance(notas, dict) and notas.get('condicion_pago_dte') in (1, 2):
            forma_pago_dte = notas['condicion_pago_dte']
    except (ValueError, TypeError):
        pass
    if forma_pago_dte is None:
        metodos_ticket = set(ticket.pagos.values_list('metodo_pago', flat=True))
        forma_pago_dte = 2 if metodos_ticket & METODOS_CREDITO_DTE else 1

    documento = {
        'tipo_documento': 39 if 'BOLETA' in tipo_dte else 33,
        'folio': siguiente_folio,
        'fecha_emision': ticket.fecha.strftime('%Y-%m-%d'),
        'forma_pago': forma_pago_dte,
        'timestamp': timezone.now().strftime('%Y-%m-%dT%H:%M:%S')
    }
    
    # Preparar datos del emisor - ✅ Aplicar limpiar_texto para eliminar acentos y Ñ
    emisor = {
        'rut': empresa.rut,
        'razon_social': limpiar_texto(empresa.razon_social or ''),
        'giro': limpiar_texto(empresa.giro or ''),
        'acteco': empresa.acteco or '',
        'direccion': limpiar_texto(empresa.direccion or ''),
        'comuna': limpiar_texto(empresa.comuna or ''),
        'ciudad': limpiar_texto(empresa.ciudad or ''),
        'codigo_vendedor': limpiar_texto(ticket.responsable or 'USUARIO'),
        'sucursal': limpiar_texto(ticket.sucursal.alias if ticket.sucursal else ''),
        'telefono': empresa.contacto1 or '',
        'nombre_impresora_boleta': getattr(ticket.sucursal, 'nombre_impresora_boleta', 'boleta') if ticket.sucursal else 'boleta',
        'nombre_impresora_factura': getattr(ticket.sucursal, 'nombre_impresora_factura', 'factura') if ticket.sucursal else 'factura',
    }
    
    # Preparar datos del receptor - ✅ Aplicar limpiar_texto
    if 'FACTURA' in tipo_dte and ticket.cliente_rut:
        # Factura con cliente específico
        receptor = {
            'rut': ticket.cliente_rut,
            'razon_social': limpiar_texto(ticket.cliente_nombre or 'CLIENTE'),
            'giro': limpiar_texto(ticket.cliente_giro or ''),
            'direccion': limpiar_texto(ticket.cliente_direccion or ''),
            'comuna': limpiar_texto(ticket.cliente_comuna or ''),
            'ciudad': limpiar_texto(ticket.cliente_ciudad or '')
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
    
    # Preparar productos - ✅ Aplicar limpiar_texto para eliminar acentos y Ñ
    detalle = []
    for item in ticket.ticket_productos.all():
        producto_talla = item.ProductoTalla
        producto = producto_talla.producto
        
        # Construir nombre limpio sin caracteres especiales
        articulo_limpio = limpiar_texto(producto.articulo or '')
        marca_limpia = limpiar_texto(producto.atributo1.valor if producto.atributo1 else '')
        talla_limpia = limpiar_texto(str(producto_talla.talla) if producto_talla.talla else '')
        nombre_limpio = f"{articulo_limpio} {marca_limpia} {talla_limpia}".strip()
        
        # Precios almacenados en BD son IVA-inclusive (precio de venta al público).
        # - Facturas: requieren valores NETO (sin IVA) por línea; el IVA se muestra en los totales.
        # - Boletas: usan precio IVA-inclusive. El formato Acepta para boletas no tiene campo
        #   de descuento por línea (INT1|cod||nom||qty|UN|precio|monto|}), por lo que se debe
        #   usar el precio unitario YA descontado para mantener precio × qty == monto.
        if 'FACTURA' in tipo_dte:
            precio_unitario_txt   = int(round(Decimal(item.precio) / Decimal('1.19')))
            monto_descuento_txt   = int(round(Decimal(item.descuento_unitario * item.stock) / Decimal('1.19'))) if item.descuento_unitario else 0
            monto_item_txt        = int(round(Decimal(item.subtotal) / Decimal('1.19')))
        else:
            # Para boletas: precio unitario con descuento aplicado para que precio×qty = monto
            precio_unitario_txt   = item.precio - item.descuento_unitario  # precio neto de descuento
            monto_descuento_txt   = 0  # no aplica en formato boleta (no hay campo dedicado)
            monto_item_txt        = item.subtotal  # (precio - descuento) × qty, IVA-inclusive
        
        detalle.append({
            'codigo': limpiar_texto(producto.articulo or f'PROD{producto.id}'),
            'sku': limpiar_texto(str(producto_talla.sku) if producto_talla.sku else ''),
            'nombre': nombre_limpio,
            'descripcion': limpiar_texto(producto.descripcion or ''),
            'cantidad': item.stock,
            'unidad': 'UN',
            'precio_unitario': precio_unitario_txt,
            'descuento_pct': float(item.porcentaje_descuento) if item.porcentaje_descuento else 0,
            'monto_descuento': monto_descuento_txt,
            'monto_item': monto_item_txt
        })
    
    # Calcular totales
    # ticket.total is always the NET amount the customer pays (IVA-inclusive).
    # Per-item discounts are already reflected in tp.subtotal, so we must NOT
    # subtract ticket.descuento again (that would double-count).
    total_con_iva = int(ticket.total or 0)

    neto = int(round(Decimal(total_con_iva) / Decimal('1.19')))
    iva = total_con_iva - neto
    total = total_con_iva
    
    totales = {
        'monto_neto': neto,
        'monto_exento': 0,
        'tasa_iva': 19,
        'iva': iva,
        'monto_total': total,
        'descuento_global': 0
    }
    
    # Preparar referencias (sistema nuevo de múltiples referencias)
    referencias = []
    
    # ✅ PRIORITARIO: Leer referencias del nuevo modelo (múltiples)
    referencias_modelo = ticket.referencias.all()
    if referencias_modelo.exists():
        for ref in referencias_modelo:
            referencias.append({
                'tipo_documento': ref.tipo_documento,
                'folio': ref.folio,
                'fecha': ref.fecha.strftime('%Y-%m-%d'),
                'razon': ''
            })
    # ⚠️ FALLBACK: Si no hay referencias nuevas, usar campos antiguos (retrocompatibilidad)
    elif ticket.referencia_tipo and ticket.referencia_folio:
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
        fecha = formatear_fecha(datos['documento'].get('fecha_emision', timezone.localdate()))
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
        
        # Validar que no sea BOLETA PAPEL (no genera TXT)
        if dte.tipo_documento == 'BOLETA PAPEL':
            return JsonResponse({
                'success': False,
                'error': 'Las Boletas de Papel no generan archivo TXT'
            }, status=400)
        
        # Mapear tipo de documento a código numérico
        tipo_mapping = {
            'FACTURA_ELECTRONICA': 33,
            'FACTURA ELECTRONICA': 33,
            'FACTURA_EXENTA': 34,
            'FACTURA EXENTA': 34,
            'BOLETA_ELECTRONICA': 39,
            'BOLETA ELECTRONICA': 39,
            'BOLETA_EXENTA': 41,
            'BOLETA EXENTA': 41,
            'GUIA_DESPACHO': 52,
            'GUIA DESPACHO': 52,
            'GUIA': 52,  # ✅ Para traspasos internos
            'NOTA_CREDITO': 61,
            'NOTA DE CREDITO': 61
        }
        tipo_numerico = tipo_mapping.get(dte.tipo_documento, 33)
        
        # Calcular IVA desde monto_con_iva y monto_neto
        es_exenta = tipo_numerico == 34
        if es_exenta:
            iva_calculado = 0
        else:
            iva_calculado = int(dte.monto_con_iva - dte.monto_neto)
        
        # ✅ CORREGIDO: Buscar sucursal_destino para usar su dirección en lugar de la empresa receptora
        sucursal_destino = None
        movimiento_con_destino = dte.dte_movimientos.filter(
            sucursal_destino__isnull=False
        ).select_related('sucursal_destino').first()
        if movimiento_con_destino:
            sucursal_destino = movimiento_con_destino.sucursal_destino
        
        # Construir diccionario de datos desde el DTE
        # ✅ Aplicar limpiar_texto para eliminar acentos y caracteres especiales
        datos = {
            'documento': {
                'tipo_documento': tipo_numerico,
                'folio': dte.numero_documento,  # ✅ Campo correcto
                'fecha_emision': dte.fecha_emision.strftime('%Y-%m-%d'),
                'fecha_vencimiento': dte.fecha_vencimiento.strftime('%Y-%m-%d') if dte.fecha_vencimiento else '',
                'forma_pago': 1 if dte.fecha_vencimiento == dte.fecha_emision else 2,
                'timestamp': timezone.now().strftime('%Y-%m-%dT%H:%M:%S')
            },
            'emisor': {
                'rut': dte.emisor.rut,
                'razon_social': limpiar_texto(dte.emisor.razon_social or ''),
                'giro': limpiar_texto(dte.emisor.giro or ''),
                'acteco': dte.emisor.acteco or '',
                'direccion': limpiar_texto(dte.sucursal.direccion if dte.sucursal else dte.emisor.direccion or ''),
                'comuna': limpiar_texto(dte.emisor.comuna or ''),
                'ciudad': limpiar_texto(dte.emisor.ciudad or ''),
                'codigo_vendedor': limpiar_texto(dte.responsable or 'USUARIO'),
                'sucursal': limpiar_texto(dte.sucursal.alias if dte.sucursal else ''),
                'telefono': dte.emisor.contacto1 or '',
                'nombre_impresora_boleta': getattr(dte.sucursal, 'nombre_impresora_boleta', 'boleta') if dte.sucursal else 'boleta',
                'nombre_impresora_factura': getattr(dte.sucursal, 'nombre_impresora_factura', 'factura') if dte.sucursal else 'factura',
            },
            'receptor': {
                'rut': dte.receptor.rut if dte.receptor else '66666666-6',
                'razon_social': limpiar_texto(dte.receptor.razon_social if dte.receptor else 'CONSUMIDOR FINAL'),
                'giro': limpiar_texto(dte.receptor.giro if dte.receptor else ''),
                'direccion': limpiar_texto(sucursal_destino.direccion if sucursal_destino and sucursal_destino.direccion else (dte.receptor.direccion if dte.receptor else '')),
                'comuna': limpiar_texto(dte.receptor.comuna if dte.receptor else ''),
                'ciudad': limpiar_texto(dte.receptor.ciudad if dte.receptor else ''),
                'sucursal': limpiar_texto(sucursal_destino.alias if sucursal_destino else '')
            },
            'totales': {
                'monto_neto': 0 if es_exenta else int(dte.monto_neto),
                'monto_exento': int(dte.monto_neto) if es_exenta else 0,
                'tasa_iva': 0 if es_exenta else 19,
                'iva': iva_calculado,
                'monto_total': int(dte.monto_con_iva),
                'descuento_global': int(dte.descuento) if dte.descuento else 0
            },
            'detalle': [],
            'referencias': []
        }
        
        from collections import defaultdict
        productos_agrupados = defaultdict(lambda: {
            'tallas': [],
            'cantidad_total': 0,
            'precio': 0,
            'monto_total': 0,
            'descuento_monto_total': 0,
            'descuento_pct': 0,
            'producto': None,
            'articulo': '',
            'marca': '',
            'color': ''
        })

        for dte_producto in dte.dte_productos.select_related('productoTalla__producto'):
            if dte_producto.productoTalla is None:
                # Item de concepto (sin mercadería)
                datos['detalle'].append({
                    'nombre': limpiar_texto(dte_producto.descripcion or 'Concepto'),
                    'descripcion': '',
                    'cantidad': dte_producto.stock,
                    'unidad': 'UN',
                    'precio_unitario': dte_producto.precio_unitario or dte_producto.precio,
                    'descuento_pct': float(dte_producto.descuento_pct) if dte_producto.descuento_pct else 0,
                    'monto_descuento': int(dte_producto.descuento_monto or 0),
                    'monto_item': dte_producto.monto_item or (dte_producto.stock * dte_producto.precio),
                    'codigo': 'SRV',
                    'indicador_exencion': 1 if tipo_numerico == 34 else '',
                })
                continue

            producto = dte_producto.productoTalla.producto
            producto_talla = dte_producto.productoTalla
            articulo_key = producto.articulo

            grupo = productos_agrupados[articulo_key]

            talla_nombre = str(producto_talla.talla) if hasattr(producto_talla, 'talla') and producto_talla.talla else 'U'
            grupo['tallas'].append(f"{dte_producto.stock}:{talla_nombre}")
            grupo['cantidad_total'] += dte_producto.stock
            grupo['precio'] = dte_producto.precio_unitario or dte_producto.precio
            grupo['monto_total'] += dte_producto.monto_item or (dte_producto.stock * dte_producto.precio)
            grupo['descuento_monto_total'] += int(dte_producto.descuento_monto or 0)
            if dte_producto.descuento_pct:
                grupo['descuento_pct'] = float(dte_producto.descuento_pct)
            grupo['producto'] = producto
            grupo['articulo'] = producto.articulo

            if not grupo['marca'] and producto.atributo1:
                grupo['marca'] = producto.atributo1.valor
            if not grupo['color'] and producto.atributo2:
                grupo['color'] = producto.atributo2.valor

        for articulo, grupo in productos_agrupados.items():
            tallas_str = ' '.join(grupo['tallas'])
            marca_limpia = limpiar_texto(grupo['marca'] or '')
            color_limpio = limpiar_texto(grupo['color'] or '')
            marca_color = f"{marca_limpia} {color_limpio}".strip() if marca_limpia or color_limpio else ''
            nombre_final = f"{marca_color} {tallas_str}".strip() if marca_color else tallas_str

            datos['detalle'].append({
                'nombre': limpiar_texto(nombre_final),
                'descripcion': '',
                'cantidad': grupo['cantidad_total'],
                'unidad': 'UN',
                'precio_unitario': grupo['precio'],
                'descuento_pct': grupo['descuento_pct'],
                'monto_descuento': grupo['descuento_monto_total'],
                'monto_item': grupo['monto_total'],
                'codigo': limpiar_texto(grupo['articulo'])
            })

        # Poblar descuentos/recargos globales desde BD
        from .models import DescuentoRecargo
        drs = dte.descuentos_recargos.all()
        if drs.exists():
            datos['descuentos_recargos'] = [
                {
                    'tpo_mov': dr.tpo_mov,
                    'glosa_dr': dr.glosa_dr,
                    'tpo_valor': dr.tpo_valor,
                    'valor_dr': dr.valor_dr,
                    'ind_exe_dr': dr.ind_exe_dr,
                }
                for dr in drs
            ]

        # Agregar referencias si existen
        if dte.referencias:
            try:
                import json as json_lib
                refs = json_lib.loads(dte.referencias) if isinstance(dte.referencias, str) else []
                datos['referencias'] = refs
            except:
                pass
        
        # Generar TXT
        contenido_txt = generar_txt_dte_acepta(datos)
        
        # Crear nombre del archivo
        nombre_archivo = f"dte_{tipo_numerico}_{dte.numero_documento}_{dte.fecha_emision.strftime('%Y%m%d')}.txt"
        
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


@require_POST
@login_required
def importar_txt_acepta_api(request):
    """
    Parsea un archivo TXT de Acepta y retorna JSON estructurado.
    Acepta multipart (campo 'archivo') o JSON (campo 'contenido').
    """
    try:
        contenido = None

        if request.content_type and 'multipart' in request.content_type:
            archivo = request.FILES.get('archivo')
            if not archivo:
                return JsonResponse({'success': False, 'error': 'No se recibió archivo'}, status=400)
            contenido = archivo.read().decode('utf-8', errors='replace')
        else:
            data = json.loads(request.body or '{}')
            contenido = data.get('contenido', '')

        if not contenido or not contenido.strip():
            return JsonResponse({'success': False, 'error': 'Contenido del TXT vacío'}, status=400)

        resultado = parsear_txt_acepta(contenido)

        return JsonResponse({'success': True, 'data': resultado})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error al parsear TXT: {str(e)}'}, status=500)