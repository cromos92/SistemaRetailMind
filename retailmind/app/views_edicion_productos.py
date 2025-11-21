"""
Módulo de Edición de Productos y Gestión de Stock - RetailMind
Contiene todas las vistas relacionadas con la edición de productos, variaciones y ajustes de stock
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, Http404
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.db.models import Sum, F, Q
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
import json
from decimal import Decimal

from .models import (
    Producto, Producto_Talla, Movimientos_Producto, LoteProducto,
    Categoria, AtributoOpcion, Productos_Atributos, Sucursal
)


# ========== UTILIDAD: OBTENER PRODUCTO DESDE TALLA ==========

@require_GET
@login_required
def obtener_producto_desde_talla(request, talla_id):
    """
    Obtener el ID del producto principal desde un Producto_Talla
    Útil para cuando solo tenemos el ID de una variación/talla
    """
    try:
        producto_talla = get_object_or_404(Producto_Talla, id=talla_id)
        
        return JsonResponse({
            'success': True,
            'producto_id': producto_talla.producto.id,
            'producto_nombre': producto_talla.producto.articulo
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener producto: {str(e)}'
        }, status=500)


# ========== OBTENER PRODUCTO PARA EDICIÓN ==========

@require_GET
@login_required
def obtener_producto_edicion(request, producto_id):
    """
    Obtener todos los datos de un producto para edición
    Incluye producto base, variaciones/tallas, stock y lotes
    """
    try:
        producto = get_object_or_404(
            Producto.objects.select_related(
                'categoria', 'sucursal', 'guia_talla',
                'atributo1', 'atributo2', 'atributo3', 'atributo4'
            ),
            id=producto_id
        )
        
        # Datos del producto base
        producto_data = {
            'id': producto.id,
            'articulo': producto.articulo,
            'descripcion': producto.descripcion or '',
            'categoria_id': producto.categoria.id if producto.categoria else None,
            'categoria_nombre': producto.categoria.nombre if producto.categoria else '',
            'sucursal_id': producto.sucursal.id if producto.sucursal else None,
            'sucursal_nombre': producto.sucursal.alias if producto.sucursal else '',  # Corrección: alias en lugar de nombre
            'atributo1_id': producto.atributo1.id if producto.atributo1 else None,
            'atributo1_nombre': producto.atributo1.valor if producto.atributo1 else '',
            'atributo2_id': producto.atributo2.id if producto.atributo2 else None,
            'atributo2_nombre': producto.atributo2.valor if producto.atributo2 else '',
            'atributo3_id': producto.atributo3.id if producto.atributo3 else None,
            'atributo3_nombre': producto.atributo3.valor if producto.atributo3 else '',
            'atributo4_id': producto.atributo4.id if producto.atributo4 else None,
            'atributo4_nombre': producto.atributo4.valor if producto.atributo4 else '',
            'costo': producto.costo,
            'sobreprecio': producto.sobreprecio,
            'precioventa': producto.precioventa,
            'precioSugerido': producto.precioSugerido or 0,
            'tipo_talla': producto.tipo_talla,
            'guia_talla_id': producto.guia_talla.id if producto.guia_talla else None,
        }
        
        # Obtener variaciones/tallas
        variaciones = []
        for pt in producto.producto_talla.all():
            # Calcular stock usando lógica híbrida
            stock_total = pt.stock_total() if hasattr(pt, 'stock_total') else pt.stock
            
            # Obtener lotes FIFO (si existen)
            try:
                lotes_activos = LoteProducto.objects.filter(
                    producto_talla=pt,
                    activo=True
                ).order_by('created_at')
            except NameError:
                # Si LoteProducto no está importado o no existe
                lotes_activos = []
            
            # Obtener información de lotes
            lotes_data = []
            for lote in lotes_activos:
                lotes_data.append({
                    'id': lote.id,
                    'numero_lote': lote.numero_lote,
                    'cantidad_inicial': lote.cantidad_inicial,
                    'cantidad_disponible': lote.cantidad_disponible,
                    'costo_unitario': float(lote.costo_unitario),
                    'sobreprecio_unitario': float(lote.sobreprecio_unitario),
                    'precio_venta_unitario': float(lote.precio_venta_unitario),
                    'fecha_creacion': lote.created_at.strftime('%d/%m/%Y %H:%M'),
                    'fecha_vencimiento': lote.fecha_vencimiento.strftime('%d/%m/%Y') if lote.fecha_vencimiento else None,
                })
            
            variaciones.append({
                'id': pt.id,
                'sku': pt.sku,
                'talla': pt.talla,
                'stock_db': pt.stock,  # Stock guardado en BD (Legacy)
                'stock_total': stock_total,  # Stock real disponible (Híbrido)
                'activo': True,
                'lotes': lotes_data
            })
        
        return JsonResponse({
            'success': True,
            'producto': producto_data,
            'variaciones': variaciones
        })
        
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Producto no encontrado'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener producto: {str(e)}'
        }, status=500)


# ========== ACTUALIZAR PRODUCTO BASE ==========

@require_http_methods(["PUT", "POST"])
@login_required
@transaction.atomic
def actualizar_producto(request, producto_id):
    """
    Actualizar datos del producto base
    No afecta a las variaciones/tallas
    """
    try:
        producto = get_object_or_404(Producto, id=producto_id)
        
        # Parsear datos según el método
        if request.method == 'PUT':
            data = json.loads(request.body)
        else:  # POST
            data = json.loads(request.body)
        
        # Validaciones
        articulo = data.get('articulo', '').strip()
        if not articulo:
            return JsonResponse({
                'success': False,
                'error': 'El nombre del producto es requerido'
            }, status=400)
        
        # Validar precios
        try:
            costo = int(data.get('costo', 0))
            sobreprecio = int(data.get('sobreprecio', 0))
            precioventa = int(data.get('precioventa', 0))
            
            if costo < 0:
                raise ValueError('El costo no puede ser negativo')
            if sobreprecio < 0:
                raise ValueError('El sobreprecio no puede ser negativo')
            if precioventa <= 0:
                raise ValueError('El precio de venta debe ser mayor a 0')
                
        except ValueError as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=400)
        
        # Actualizar campos básicos
        producto.articulo = articulo
        producto.descripcion = data.get('descripcion', '')
        producto.costo = costo
        producto.sobreprecio = sobreprecio
        producto.precioventa = precioventa
        producto.precioSugerido = int(data.get('precioSugerido', 0))
        
        # Actualizar categoría
        categoria_id = data.get('categoria_id')
        if categoria_id:
            try:
                producto.categoria = Categoria.objects.get(id=categoria_id)
            except Categoria.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': f'Categoría con ID {categoria_id} no encontrada'
                }, status=400)
        
        # Actualizar atributos
        for i in range(1, 5):
            atributo_key = f'atributo{i}_id'
            atributo_id = data.get(atributo_key)
            
            if atributo_id:
                try:
                    atributo = AtributoOpcion.objects.get(id=atributo_id)
                    setattr(producto, f'atributo{i}', atributo)
                except AtributoOpcion.DoesNotExist:
                    return JsonResponse({
                        'success': False,
                        'error': f'Atributo con ID {atributo_id} no encontrado'
                    }, status=400)
            else:
                setattr(producto, f'atributo{i}', None)
        
        # Guardar cambios
        producto.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Producto actualizado exitosamente',
            'producto': {
                'id': producto.id,
                'articulo': producto.articulo,
                'precioventa': producto.precioventa
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
            'error': f'Error al actualizar producto: {str(e)}'
        }, status=500)


# ========== ACTUALIZAR VARIACIÓN/TALLA ==========

@require_http_methods(["PUT", "POST"])
@login_required
@transaction.atomic
def actualizar_variacion(request, variacion_id):
    """
    Actualizar datos de una variación/talla específica
    Permite cambiar SKU (si es único) y estado activo
    NO permite cambiar la talla ni el stock (usar ajustar_stock para eso)
    """
    try:
        variacion = get_object_or_404(Producto_Talla, id=variacion_id)
        
        # Parsear datos
        if request.method == 'PUT':
            data = json.loads(request.body)
        else:  # POST
            data = json.loads(request.body)
        
        # Actualizar SKU si se proporciona
        nuevo_sku = data.get('sku')
        if nuevo_sku is not None:
            try:
                nuevo_sku = int(nuevo_sku)
                
                # Validar que el SKU sea único (excepto para este mismo registro)
                if Producto_Talla.objects.filter(sku=nuevo_sku).exclude(id=variacion_id).exists():
                    return JsonResponse({
                        'success': False,
                        'error': f'El SKU {nuevo_sku} ya está en uso por otro producto'
                    }, status=400)
                
                variacion.sku = nuevo_sku
                
            except ValueError:
                return JsonResponse({
                    'success': False,
                    'error': 'El SKU debe ser un número válido'
                }, status=400)
        
        # Guardar cambios
        variacion.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Variación actualizada exitosamente',
            'variacion': {
                'id': variacion.id,
                'sku': variacion.sku,
                'talla': variacion.talla
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
            'error': f'Error al actualizar variación: {str(e)}'
        }, status=500)


# ========== AJUSTAR STOCK ==========

@require_POST
@login_required
@transaction.atomic
def ajustar_stock(request, variacion_id):
    """
    Ajustar stock de una variación (entrada o salida)
    - ENTRADA: Crea nuevo lote FIFO y registra movimiento
    - SALIDA: Consume stock FIFO y registra movimiento
    """
    try:
        variacion = get_object_or_404(Producto_Talla, id=variacion_id)
        data = json.loads(request.body)
        
        # Validar datos requeridos
        tipo_ajuste = data.get('tipo_ajuste', '').upper()
        cantidad = data.get('cantidad')
        motivo = data.get('motivo', '').strip()
        
        # Validaciones básicas
        if tipo_ajuste not in ['ENTRADA', 'SALIDA']:
            return JsonResponse({
                'success': False,
                'error': 'Tipo de ajuste debe ser ENTRADA o SALIDA'
            }, status=400)
        
        if not cantidad or cantidad <= 0:
            return JsonResponse({
                'success': False,
                'error': 'La cantidad debe ser mayor a 0'
            }, status=400)
        
        if not motivo or len(motivo) < 10:
            return JsonResponse({
                'success': False,
                'error': 'El motivo es obligatorio y debe tener al menos 10 caracteres'
            }, status=400)
        
        cantidad = int(cantidad)
        
        # Obtener sucursal actual
        sucursal_id = request.session.get('idSucursalActual')
        if sucursal_id:
            sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        else:
            sucursal = variacion.producto.sucursal
        
        # ===== AJUSTE DE ENTRADA =====
        if tipo_ajuste == 'ENTRADA':
            # Validar que se proporcionen los costos
            try:
                costo_unitario = Decimal(str(data.get('costo_unitario', 0)))
                sobreprecio_unitario = Decimal(str(data.get('sobreprecio_unitario', 0)))
                precio_venta_unitario = Decimal(str(data.get('precio_venta_unitario', 0)))
                
                if costo_unitario <= 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'El costo unitario debe ser mayor a 0 para ajustes de entrada'
                    }, status=400)
                    
                if precio_venta_unitario <= 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'El precio de venta unitario debe ser mayor a 0 para ajustes de entrada'
                    }, status=400)
                    
            except (ValueError, TypeError):
                return JsonResponse({
                    'success': False,
                    'error': 'Los valores de costos deben ser números válidos'
                }, status=400)
            
            # Generar número de lote
            numero_lote = data.get('numero_lote', '').strip()
            if not numero_lote:
                import uuid
                numero_lote = f"AJUSTE-{uuid.uuid4().hex[:8].upper()}"
            
            # Crear lote FIFO
            from .views_modulo_productos import crear_lote_producto
            lote = crear_lote_producto(
                producto_talla=variacion,
                cantidad=cantidad,
                costo_unitario=costo_unitario,
                sobreprecio_unitario=sobreprecio_unitario,
                precio_venta_unitario=precio_venta_unitario,
                numero_lote=numero_lote,
                fecha_vencimiento=data.get('fecha_vencimiento'),
                observaciones=f'Ajuste manual: {motivo}'
            )
            
            # Registrar movimiento
            movimiento = Movimientos_Producto.objects.create(
                ProductoTalla=variacion,
                sucursal_origen=sucursal,
                sucursal_destino=sucursal,
                cantidad=cantidad,
                costo=int(costo_unitario),
                sobreprecio=int(sobreprecio_unitario),
                precio=int(precio_venta_unitario),
                concepto='AJUSTE_POSITIVO',
                responsable=request.user,
                observaciones=motivo,
                estado='COMPLETADO',
                fecha_hora=timezone.now()
            )
            
            # Actualizar stock en Producto_Talla
            variacion.stock += cantidad
            variacion.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Entrada de {cantidad} unidades registrada exitosamente',
                'nuevo_stock': variacion.stock,
                'movimiento_id': movimiento.id,
                'lote_id': lote.id,
                'numero_lote': lote.numero_lote
            })
        
        # ===== AJUSTE DE SALIDA =====
        elif tipo_ajuste == 'SALIDA':
            # Validar stock disponible
            stock_disponible = sum(
                lote.cantidad_disponible 
                for lote in LoteProducto.objects.filter(
                    producto_talla=variacion,
                    activo=True,
                    cantidad_disponible__gt=0
                )
            )
            
            if cantidad > stock_disponible:
                return JsonResponse({
                    'success': False,
                    'error': f'Stock insuficiente. Disponible: {stock_disponible}, solicitado: {cantidad}'
                }, status=400)
            
            # Consumir stock FIFO
            from .views_modulo_productos import consumir_stock_fifo
            lotes_consumidos = consumir_stock_fifo(
                producto_talla=variacion,
                cantidad_requerida=cantidad,
                responsable=request.user,
                observaciones=motivo,
                referencia_externa=f'AJUSTE_MANUAL_{timezone.now().strftime("%Y%m%d%H%M%S")}'
            )
            
            # El consumir_stock_fifo ya crea el movimiento, pero vamos a obtenerlo
            # para retornar su ID
            ultimo_movimiento = Movimientos_Producto.objects.filter(
                ProductoTalla=variacion,
                responsable=request.user
            ).order_by('-fecha_hora').first()
            
            # Actualizar stock en Producto_Talla
            variacion.stock -= cantidad
            variacion.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Salida de {cantidad} unidades registrada exitosamente',
                'nuevo_stock': variacion.stock,
                'movimiento_id': ultimo_movimiento.id if ultimo_movimiento else None,
                'lotes_consumidos': len(lotes_consumidos)
            })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al ajustar stock: {str(e)}'
        }, status=500)


# ========== HISTORIAL DE MOVIMIENTOS ==========

@require_GET
@login_required
def obtener_historial_movimientos(request, variacion_id):
    """
    Obtener historial de movimientos de stock de una variación
    """
    try:
        variacion = get_object_or_404(Producto_Talla, id=variacion_id)
        
        # Parámetros de filtro
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        limit = int(request.GET.get('limit', 50))
        
        # Construir queryset
        queryset = Movimientos_Producto.objects.filter(
            ProductoTalla=variacion
        ).select_related('responsable', 'dte', 'ticket', 'sucursal_origen', 'sucursal_destino')
        
        # Aplicar filtros de fecha
        if fecha_inicio:
            queryset = queryset.filter(fecha_hora__date__gte=fecha_inicio)
        if fecha_fin:
            queryset = queryset.filter(fecha_hora__date__lte=fecha_fin)
        
        # Ordenar por fecha descendente y limitar
        queryset = queryset.order_by('-fecha_hora')[:limit]
        
        # Serializar movimientos
        movimientos_data = []
        for mov in queryset:
            movimientos_data.append({
                'id': mov.id,
                'fecha_hora': mov.fecha_hora.strftime('%d/%m/%Y %H:%M:%S'),
                'concepto': mov.concepto,
                'concepto_display': dict(mov._meta.get_field('concepto').choices).get(mov.concepto, mov.concepto) if hasattr(mov, 'concepto') else mov.concepto,
                'cantidad': mov.cantidad,
                'costo': mov.costo,
                'precio': mov.precio,
                'responsable': mov.responsable.get_full_name() or mov.responsable.username if mov.responsable else 'Sistema',
                'sucursal_origen': mov.sucursal_origen.alias if mov.sucursal_origen else '',  # Corrección: alias en lugar de nombre
                'sucursal_destino': mov.sucursal_destino.alias if mov.sucursal_destino else '',  # Corrección: alias en lugar de nombre
                'observaciones': mov.observaciones or '',
                'estado': mov.estado,
                'dte_numero': mov.dte.numero_dte if mov.dte else None,
                'ticket_numero': mov.ticket.numero if mov.ticket else None,
            })
        
        return JsonResponse({
            'success': True,
            'movimientos': movimientos_data,
            'total': len(movimientos_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener historial: {str(e)}'
        }, status=500)


# ========== ELIMINAR/DESACTIVAR VARIACIÓN ==========

@require_POST
@login_required
@transaction.atomic
def eliminar_variacion(request, variacion_id):
    """
    Eliminar una variación/talla
    Solo se permite si no tiene stock ni movimientos asociados
    """
    try:
        variacion = get_object_or_404(Producto_Talla, id=variacion_id)
        
        # Verificar que no tenga stock
        stock_total = sum(
            lote.cantidad_disponible 
            for lote in LoteProducto.objects.filter(
                producto_talla=variacion,
                activo=True
            )
        )
        
        if stock_total > 0:
            return JsonResponse({
                'success': False,
                'error': f'No se puede eliminar la variación porque tiene {stock_total} unidades en stock'
            }, status=400)
        
        # Verificar que no tenga movimientos
        tiene_movimientos = Movimientos_Producto.objects.filter(
            ProductoTalla=variacion
        ).exists()
        
        if tiene_movimientos:
            return JsonResponse({
                'success': False,
                'error': 'No se puede eliminar la variación porque tiene movimientos registrados'
            }, status=400)
        
        # Guardar info antes de eliminar
        producto_id = variacion.producto.id
        talla = variacion.talla
        
        # Eliminar variación
        variacion.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Variación (Talla {talla}) eliminada exitosamente',
            'producto_id': producto_id
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al eliminar variación: {str(e)}'
        }, status=500)

