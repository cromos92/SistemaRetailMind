"""
Módulo de Impresión de Etiquetas Zebra - RetailMind
Permite imprimir etiquetas de productos desde documentos (DTEs, Traspasos, Recepciones)
o manualmente por artículo/SKU.
"""

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Sum, Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from django.db import transaction
import json
from decimal import Decimal
from datetime import datetime

from .models import (
    Dte, Dte_Productos, Sucursal, Producto_Talla, Producto, 
    Traspaso, Traspaso_Detalle, Ticket, Ticket_Productos,
    Productos_Recepcionados, EmpresaUser, Empresa,
    HistorialImpresionEtiqueta, DetalleImpresionEtiqueta
)


# ========== VISTA PRINCIPAL ==========

@login_required
def gestion_etiquetas_zebra(request):
    """Vista principal para gestión de impresión de etiquetas Zebra"""
    
    # Obtener sucursales del usuario
    empresa_actual_id = request.session.get('idEmpresaActual')
    sucursal_actual_id = request.session.get('idSucursalActual')
    
    sucursales = Sucursal.objects.filter(empresa_id=empresa_actual_id).order_by('alias')
    
    # Obtener últimas impresiones
    ultimas_impresiones = HistorialImpresionEtiqueta.objects.filter(
        sucursal__empresa_id=empresa_actual_id
    ).order_by('-fecha_impresion')[:10]
    
    context = {
        'sucursales': sucursales,
        'sucursal_actual_id': sucursal_actual_id,
        'ultimas_impresiones': ultimas_impresiones,
    }
    
    return render(request, 'vistas/modulo_existencias/gestion_etiquetas_zebra.html', context)


# ========== APIS DE DOCUMENTOS ==========

@login_required
@require_GET
def obtener_documentos_etiquetas(request):
    """
    Obtener documentos disponibles para generar etiquetas
    Incluye indicador de si ya fueron impresos
    """
    try:
        # Parámetros de filtro
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        tipo_documento = request.GET.get('tipo_documento', 'todos')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        search = request.GET.get('search', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        empresa_actual_id = request.session.get('idEmpresaActual')
        
        if not empresa_actual_id:
            return JsonResponse({
                'success': False,
                'error': 'No hay empresa activa en la sesión'
            })
        
        documentos = []
        
        # Obtener IDs de documentos ya impresos
        docs_impresos = set(
            HistorialImpresionEtiqueta.objects.filter(
                sucursal__empresa_id=empresa_actual_id,
                completado=True
            ).values_list('tipo_origen', 'documento_id')
        )
        
        # ===== 1. DTEs de COMPRA (Facturas de proveedores) =====
        if tipo_documento in ['todos', 'compra', 'dte_compra']:
            dtes_compra = Dte.objects.filter(
                receptor__id=empresa_actual_id,
                tipo_transaccion='COMPRA'
            ).select_related('emisor', 'receptor', 'sucursal')
            
            if sucursal_id:
                dtes_compra = dtes_compra.filter(sucursal_id=sucursal_id)
            
            if fecha_inicio:
                dtes_compra = dtes_compra.filter(fecha_emision__gte=fecha_inicio)
            if fecha_fin:
                dtes_compra = dtes_compra.filter(fecha_emision__lte=fecha_fin)
            
            if search:
                dtes_compra = dtes_compra.filter(
                    Q(numero_documento__icontains=search) |
                    Q(emisor__razon_social__icontains=search)
                )
            
            for dte in dtes_compra.order_by('-fecha_emision')[:100]:
                # Contar productos
                productos_count = Dte_Productos.objects.filter(dte=dte).aggregate(
                    total_unidades=Sum('stock')
                )['total_unidades'] or 0
                
                # Verificar si ya fue impreso
                ya_impreso = ('DTE_COMPRA', dte.id) in docs_impresos
                
                # Obtener última impresión si existe
                ultima_impresion = None
                if ya_impreso:
                    hist = HistorialImpresionEtiqueta.objects.filter(
                        tipo_origen='DTE_COMPRA',
                        documento_id=dte.id
                    ).first()
                    if hist:
                        ultima_impresion = hist.fecha_impresion.strftime('%d/%m/%Y %H:%M')
                
                documentos.append({
                    'id': dte.id,
                    'tipo': 'DTE_COMPRA',
                    'tipo_display': f'{dte.tipo_documento}',
                    'numero': dte.numero_documento,
                    'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                    'fecha_sort': dte.fecha_emision.strftime('%Y-%m-%d'),
                    'origen': dte.emisor.razon_social if dte.emisor else 'Sin proveedor',
                    'destino': dte.sucursal.alias if dte.sucursal else 'Sin sucursal',
                    'total_productos': productos_count,
                    'monto': float(dte.monto_con_iva),
                    'estado': dte.estado_dte,
                    'ya_impreso': ya_impreso,
                    'ultima_impresion': ultima_impresion
                })
        
        # ===== 2. DTEs de TRASPASO INTERNO =====
        if tipo_documento in ['todos', 'traspaso', 'dte_traspaso']:
            dtes_traspaso = Dte.objects.filter(
                Q(emisor_id=empresa_actual_id) | Q(receptor_id=empresa_actual_id),
                tipo_transaccion='TRASPASO'
            ).select_related('emisor', 'receptor', 'sucursal')
            
            if sucursal_id:
                dtes_traspaso = dtes_traspaso.filter(
                    Q(sucursal_id=sucursal_id) |
                    Q(dte_movimientos__sucursal_destino_id=sucursal_id)
                ).distinct()
            
            if fecha_inicio:
                dtes_traspaso = dtes_traspaso.filter(fecha_emision__gte=fecha_inicio)
            if fecha_fin:
                dtes_traspaso = dtes_traspaso.filter(fecha_emision__lte=fecha_fin)
            
            if search:
                dtes_traspaso = dtes_traspaso.filter(numero_documento__icontains=search)
            
            for dte in dtes_traspaso.order_by('-fecha_emision')[:100]:
                productos_count = Dte_Productos.objects.filter(dte=dte).aggregate(
                    total_unidades=Sum('stock')
                )['total_unidades'] or 0
                
                # Obtener sucursal destino del movimiento
                sucursal_destino = dte.sucursal.alias if dte.sucursal else ''
                mov = dte.dte_movimientos.filter(sucursal_destino__isnull=False).first()
                if mov and mov.sucursal_destino:
                    sucursal_destino = mov.sucursal_destino.alias
                
                # Verificar si ya fue impreso
                ya_impreso = ('DTE_TRASPASO', dte.id) in docs_impresos
                
                ultima_impresion = None
                if ya_impreso:
                    hist = HistorialImpresionEtiqueta.objects.filter(
                        tipo_origen='DTE_TRASPASO',
                        documento_id=dte.id
                    ).first()
                    if hist:
                        ultima_impresion = hist.fecha_impresion.strftime('%d/%m/%Y %H:%M')
                
                documentos.append({
                    'id': dte.id,
                    'tipo': 'DTE_TRASPASO',
                    'tipo_display': f'{dte.tipo_documento} (Traspaso)',
                    'numero': dte.numero_documento,
                    'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                    'fecha_sort': dte.fecha_emision.strftime('%Y-%m-%d'),
                    'origen': dte.emisor.razon_social if dte.emisor else 'Sin origen',
                    'destino': sucursal_destino,
                    'total_productos': productos_count,
                    'monto': float(dte.monto_con_iva),
                    'estado': dte.estado_dte,
                    'ya_impreso': ya_impreso,
                    'ultima_impresion': ultima_impresion
                })
        
        # ===== 3. TRASPASOS INTERNOS (modelo Traspaso) =====
        if tipo_documento in ['todos', 'traspaso_interno']:
            traspasos = Traspaso.objects.filter(
                Q(sucursal_origen__empresa_id=empresa_actual_id) |
                Q(sucursal_destino__empresa_id=empresa_actual_id)
            ).select_related('sucursal_origen', 'sucursal_destino')
            
            if sucursal_id:
                traspasos = traspasos.filter(
                    Q(sucursal_origen_id=sucursal_id) |
                    Q(sucursal_destino_id=sucursal_id)
                )
            
            if fecha_inicio:
                traspasos = traspasos.filter(fecha_solicitud__gte=fecha_inicio)
            if fecha_fin:
                traspasos = traspasos.filter(fecha_solicitud__lte=fecha_fin)
            
            if search:
                traspasos = traspasos.filter(id__icontains=search)
            
            for traspaso in traspasos.order_by('-fecha_solicitud')[:100]:
                productos_count = Traspaso_Detalle.objects.filter(
                    traspaso=traspaso
                ).aggregate(total=Sum('cantidad_solicitada'))['total'] or 0
                
                ya_impreso = ('TRASPASO_INTERNO', traspaso.id) in docs_impresos
                
                ultima_impresion = None
                if ya_impreso:
                    hist = HistorialImpresionEtiqueta.objects.filter(
                        tipo_origen='TRASPASO_INTERNO',
                        documento_id=traspaso.id
                    ).first()
                    if hist:
                        ultima_impresion = hist.fecha_impresion.strftime('%d/%m/%Y %H:%M')
                
                documentos.append({
                    'id': traspaso.id,
                    'tipo': 'TRASPASO_INTERNO',
                    'tipo_display': 'Traspaso Interno',
                    'numero': f'TR-{traspaso.id}',
                    'fecha': traspaso.fecha_solicitud.strftime('%d/%m/%Y'),
                    'fecha_sort': traspaso.fecha_solicitud.strftime('%Y-%m-%d'),
                    'origen': traspaso.sucursal_origen.alias if traspaso.sucursal_origen else '',
                    'destino': traspaso.sucursal_destino.alias if traspaso.sucursal_destino else '',
                    'total_productos': productos_count,
                    'monto': 0,
                    'estado': traspaso.estado,
                    'ya_impreso': ya_impreso,
                    'ultima_impresion': ultima_impresion
                })
        
        # Ordenar por fecha descendente
        documentos.sort(key=lambda x: x['fecha_sort'], reverse=True)
        
        # Paginación
        total_documentos = len(documentos)
        inicio = (page - 1) * per_page
        fin = inicio + per_page
        documentos_paginados = documentos[inicio:fin]
        
        return JsonResponse({
            'success': True,
            'documentos': documentos_paginados,
            'pagination': {
                'current_page': page,
                'total_pages': (total_documentos + per_page - 1) // per_page,
                'total_items': total_documentos,
                'has_next': fin < total_documentos,
                'has_previous': page > 1
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener documentos: {str(e)}'
        })


@login_required
@require_GET
def obtener_productos_documento(request, tipo_documento, documento_id):
    """
    Obtener productos de un documento específico para generar etiquetas
    SIEMPRE usa precio de venta al público (precioventa del Producto)
    """
    try:
        sucursal_actual_id = request.session.get('idSucursalActual')
        sucursal_actual = Sucursal.objects.filter(id=sucursal_actual_id).first()
        
        productos = []
        documento_info = {}
        
        if tipo_documento == 'DTE_COMPRA' or tipo_documento == 'DTE_TRASPASO':
            # Obtener DTE
            dte = get_object_or_404(Dte, id=documento_id)
            
            documento_info = {
                'tipo': tipo_documento,
                'numero': dte.numero_documento,
                'fecha': dte.fecha_emision.strftime('%d/%m/%Y'),
                'proveedor': dte.emisor.razon_social if dte.emisor else '',
                'sucursal_destino': dte.sucursal.alias if dte.sucursal else ''
            }
            
            # Obtener productos del DTE
            for dte_prod in Dte_Productos.objects.filter(dte=dte).select_related(
                'productoTalla__producto'
            ):
                producto_talla = dte_prod.productoTalla
                producto = producto_talla.producto
                
                # Obtener datos del producto
                marca = producto.atributo1.valor if producto.atributo1 else ''
                color = producto.atributo2.valor if producto.atributo2 else ''
                
                # SIEMPRE usar precio de venta al público (precioventa del Producto)
                precio_venta = producto.precioventa
                
                productos.append({
                    'id': dte_prod.id,
                    'producto_talla_id': producto_talla.id,
                    'sku': str(producto_talla.sku),
                    'articulo': producto.articulo,
                    'descripcion': producto.descripcion[:20] if producto.descripcion else producto.articulo[:20],
                    'marca': marca[:10] if marca else '',
                    'talla': str(producto_talla.talla) if producto_talla.talla else '',
                    'color': color[:10] if color else '',
                    'cantidad': dte_prod.stock,
                    'precio': float(precio_venta),  # PRECIO DE VENTA PÚBLICO
                    'sucursal': sucursal_actual.alias if sucursal_actual else '',
                    'factura': str(dte.numero_documento),
                    'seleccionado': True  # Por defecto seleccionados
                })
        
        elif tipo_documento == 'TRASPASO_INTERNO':
            # Obtener Traspaso
            traspaso = get_object_or_404(Traspaso, id=documento_id)
            
            documento_info = {
                'tipo': tipo_documento,
                'numero': f'TR-{traspaso.id}',
                'fecha': traspaso.fecha_solicitud.strftime('%d/%m/%Y'),
                'proveedor': traspaso.sucursal_origen.alias if traspaso.sucursal_origen else '',
                'sucursal_destino': traspaso.sucursal_destino.alias if traspaso.sucursal_destino else ''
            }
            
            # Obtener productos del traspaso
            for detalle in Traspaso_Detalle.objects.filter(
                traspaso=traspaso
            ).select_related('producto_talla__producto'):
                producto_talla = detalle.producto_talla
                producto = producto_talla.producto
                
                marca = producto.atributo1.valor if producto.atributo1 else ''
                color = producto.atributo2.valor if producto.atributo2 else ''
                
                # SIEMPRE usar precio de venta al público
                precio_venta = producto.precioventa
                
                productos.append({
                    'id': detalle.id,
                    'producto_talla_id': producto_talla.id,
                    'sku': str(producto_talla.sku),
                    'articulo': producto.articulo,
                    'descripcion': producto.descripcion[:20] if producto.descripcion else producto.articulo[:20],
                    'marca': marca[:10] if marca else '',
                    'talla': str(producto_talla.talla) if producto_talla.talla else '',
                    'color': color[:10] if color else '',
                    'cantidad': detalle.cantidad_solicitada,
                    'precio': float(precio_venta),  # PRECIO DE VENTA PÚBLICO
                    'sucursal': traspaso.sucursal_destino.alias if traspaso.sucursal_destino else '',
                    'factura': f'TR-{traspaso.id}',
                    'seleccionado': True
                })
        
        # Verificar si este documento ya fue impreso antes
        historial_previo = HistorialImpresionEtiqueta.objects.filter(
            tipo_origen=tipo_documento,
            documento_id=documento_id,
            completado=True
        ).order_by('-fecha_impresion')
        
        impresiones_previas = []
        for hist in historial_previo[:5]:  # Últimas 5 impresiones
            impresiones_previas.append({
                'fecha': hist.fecha_impresion.strftime('%d/%m/%Y %H:%M'),
                'usuario': hist.usuario.username if hist.usuario else 'Sistema',
                'total_etiquetas': hist.total_etiquetas
            })
        
        # Calcular totales
        total_productos = len(productos)
        total_etiquetas = sum(p['cantidad'] for p in productos)
        
        return JsonResponse({
            'success': True,
            'documento': documento_info,
            'productos': productos,
            'resumen': {
                'total_productos': total_productos,
                'total_etiquetas': total_etiquetas
            },
            'impresiones_previas': impresiones_previas,
            'ya_impreso': len(impresiones_previas) > 0
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener productos: {str(e)}'
        })


# ========== BÚSQUEDA MANUAL DE PRODUCTOS ==========

@login_required
@require_GET  
def buscar_producto_etiqueta(request):
    """
    Buscar productos para generar etiquetas manualmente (sin documento)
    SIEMPRE usa precio de venta al público
    """
    try:
        termino = request.GET.get('termino', '').strip()
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        
        if not termino or len(termino) < 2:
            return JsonResponse({
                'success': False,
                'error': 'Ingrese al menos 2 caracteres para buscar'
            })
        
        # Buscar por SKU, artículo o descripción
        productos = Producto_Talla.objects.filter(
            Q(sku__icontains=termino) |
            Q(producto__articulo__icontains=termino) |
            Q(producto__descripcion__icontains=termino)
        ).select_related(
            'producto', 
            'producto__atributo1', 
            'producto__atributo2'
        )[:30]
        
        sucursal = Sucursal.objects.filter(id=sucursal_id).first()
        
        productos_data = []
        for pt in productos:
            producto = pt.producto
            marca = producto.atributo1.valor if producto.atributo1 else ''
            color = producto.atributo2.valor if producto.atributo2 else ''
            
            # SIEMPRE usar precio de venta al público
            precio_venta = producto.precioventa
            
            productos_data.append({
                'id': pt.id,
                'producto_talla_id': pt.id,
                'sku': str(pt.sku),
                'articulo': producto.articulo,
                'descripcion': producto.descripcion[:20] if producto.descripcion else producto.articulo[:20],
                'marca': marca[:10] if marca else '',
                'talla': str(pt.talla) if pt.talla else '',
                'color': color[:10] if color else '',
                'precio': float(precio_venta),  # PRECIO DE VENTA PÚBLICO
                'sucursal': sucursal.alias if sucursal else '',
                'cantidad': 1,  # Por defecto 1 etiqueta
                'seleccionado': True
            })
        
        return JsonResponse({
            'success': True,
            'productos': productos_data,
            'total': len(productos_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en búsqueda: {str(e)}'
        })


# ========== GENERACIÓN DE ETIQUETAS ==========

@login_required
@require_POST
@transaction.atomic
def generar_datos_etiquetas(request):
    """
    Generar datos de etiquetas en formato JSON para enviar a Zebra Browser Print
    Registra la impresión en el historial
    """
    try:
        data = json.loads(request.body)
        
        productos_seleccionados = data.get('productos', [])
        fecha_impresion = data.get('fecha_impresion', timezone.now().strftime('%d-%m-%Y'))
        numero_documento = data.get('numero_documento', '')
        sucursal_nombre = data.get('sucursal', '')
        tipo_origen = data.get('tipo_origen', 'MANUAL')
        documento_id = data.get('documento_id')
        registrar_historial = data.get('registrar_historial', True)
        
        if not productos_seleccionados:
            return JsonResponse({
                'success': False,
                'error': 'No hay productos seleccionados para generar etiquetas'
            })
        
        etiquetas = []
        detalles_historial = []
        
        for item in productos_seleccionados:
            cantidad = int(item.get('cantidad_etiquetas', item.get('cantidad', 1)))
            
            # Formatear precio
            precio = item.get('precio', 0)
            try:
                precio_int = int(float(precio))
                precio_formateado = f"{precio_int:,}".replace(',', '.')
            except:
                precio_formateado = str(precio)
            
            # Guardar para historial
            detalles_historial.append({
                'producto_talla_id': item.get('producto_talla_id'),
                'sku': str(item.get('sku', '')),
                'articulo': item.get('articulo', ''),
                'descripcion': item.get('descripcion', ''),
                'marca': item.get('marca', ''),
                'talla': str(item.get('talla', '')),
                'color': item.get('color', ''),
                'precio_impreso': int(float(precio)),
                'cantidad_etiquetas': cantidad
            })
            
            # Generar etiquetas según la cantidad
            for i in range(cantidad):
                etiquetas.append({
                    'sucursal': (item.get('sucursal', sucursal_nombre) or '')[:8],
                    'desc': (item.get('descripcion', item.get('articulo', '')) or '')[:15],
                    'marca': (item.get('marca', '') or '')[:10],
                    'sku': str(item.get('sku', ''))[:10],
                    'art': (item.get('articulo', '') or '')[:8],
                    'precio': precio_formateado[:10],
                    'factura': str(numero_documento)[:8],
                    'talla': str(item.get('talla', ''))[:4],
                    'color': (item.get('color', '') or '')[:10],
                    'fecha': fecha_impresion
                })
        
        # Registrar en historial
        historial_id = None
        if registrar_historial:
            sucursal_actual_id = request.session.get('idSucursalActual')
            
            historial = HistorialImpresionEtiqueta.objects.create(
                tipo_origen=tipo_origen,
                documento_id=documento_id,
                numero_documento=numero_documento,
                sucursal_id=sucursal_actual_id,
                usuario=request.user,
                total_productos=len(detalles_historial),
                total_etiquetas=len(etiquetas),
                completado=True
            )
            historial_id = historial.id
            
            # Guardar detalles
            for det in detalles_historial:
                DetalleImpresionEtiqueta.objects.create(
                    historial=historial,
                    producto_talla_id=det['producto_talla_id'],
                    sku=det['sku'],
                    articulo=det['articulo'],
                    descripcion=det['descripcion'],
                    marca=det['marca'],
                    talla=det['talla'],
                    color=det['color'],
                    precio_impreso=det['precio_impreso'],
                    cantidad_etiquetas=det['cantidad_etiquetas']
                )
        
        # Validaciones y alertas
        alertas = []
        
        if len(etiquetas) > 500:
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'Se generarán {len(etiquetas)} etiquetas. Esto puede tomar varios minutos.'
            })
        
        # Verificar etiquetas sin SKU
        sin_sku = sum(1 for e in etiquetas if not e['sku'])
        if sin_sku > 0:
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'{sin_sku} etiquetas no tienen código SKU y no generarán código de barras.'
            })
        
        # Verificar etiquetas sin precio
        sin_precio = sum(1 for e in etiquetas if not e['precio'] or e['precio'] == '0')
        if sin_precio > 0:
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'{sin_precio} etiquetas no tienen precio definido.'
            })
        
        return JsonResponse({
            'success': True,
            'etiquetas': etiquetas,
            'total_etiquetas': len(etiquetas),
            'total_filas': (len(etiquetas) + 1) // 2,
            'alertas': alertas,
            'historial_id': historial_id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar datos de etiquetas: {str(e)}'
        })


# ========== HISTORIAL ==========

@login_required
@require_GET
def obtener_historial_impresiones(request):
    """Obtener historial de impresiones de etiquetas"""
    try:
        empresa_actual_id = request.session.get('idEmpresaActual')
        sucursal_id = request.GET.get('sucursal_id')
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        queryset = HistorialImpresionEtiqueta.objects.filter(
            sucursal__empresa_id=empresa_actual_id
        ).select_related('sucursal', 'usuario')
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        queryset = queryset.order_by('-fecha_impresion')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(page)
        
        historial_data = []
        for hist in page_obj:
            historial_data.append({
                'id': hist.id,
                'fecha': hist.fecha_impresion.strftime('%d/%m/%Y %H:%M'),
                'tipo_origen': hist.tipo_origen,
                'numero_documento': hist.numero_documento,
                'sucursal': hist.sucursal.alias if hist.sucursal else '',
                'usuario': hist.usuario.username if hist.usuario else 'Sistema',
                'total_productos': hist.total_productos,
                'total_etiquetas': hist.total_etiquetas,
                'completado': hist.completado
            })
        
        return JsonResponse({
            'success': True,
            'historial': historial_data,
            'pagination': {
                'current_page': page_obj.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous()
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener historial: {str(e)}'
        })


@login_required
@require_GET
def obtener_detalle_impresion(request, historial_id):
    """Obtener detalle de una impresión específica"""
    try:
        historial = get_object_or_404(HistorialImpresionEtiqueta, id=historial_id)
        
        detalles = []
        for det in historial.detalles.all():
            detalles.append({
                'sku': det.sku,
                'articulo': det.articulo,
                'marca': det.marca,
                'talla': det.talla,
                'color': det.color,
                'precio_impreso': det.precio_impreso,
                'cantidad_etiquetas': det.cantidad_etiquetas
            })
        
        return JsonResponse({
            'success': True,
            'historial': {
                'id': historial.id,
                'fecha': historial.fecha_impresion.strftime('%d/%m/%Y %H:%M'),
                'tipo_origen': historial.tipo_origen,
                'numero_documento': historial.numero_documento,
                'total_productos': historial.total_productos,
                'total_etiquetas': historial.total_etiquetas,
                'usuario': historial.usuario.username if historial.usuario else 'Sistema'
            },
            'detalles': detalles
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener detalle: {str(e)}'
        })


@login_required
@require_GET
def obtener_sucursales_usuario(request):
    """Obtener sucursales disponibles para el usuario actual"""
    try:
        empresa_actual_id = request.session.get('idEmpresaActual')
        
        sucursales = Sucursal.objects.filter(
            empresa_id=empresa_actual_id
        ).order_by('alias')
        
        sucursales_data = [{
            'id': s.id,
            'nombre': s.alias,
            'direccion': s.direccion or ''
        } for s in sucursales]
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener sucursales: {str(e)}'
        })
