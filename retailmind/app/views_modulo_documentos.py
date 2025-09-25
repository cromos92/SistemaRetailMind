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
    return render(request, 'vistas/modulo_administracion/gestion_correlativos.html')


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
    return render(request, 'vistas/modulo_administracion/emisionDTE.html')


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
