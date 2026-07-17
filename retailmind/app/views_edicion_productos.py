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
    Categoria, AtributoOpcion, Productos_Atributos, Sucursal,
    Ticket_Productos, Dte_Productos, PermisoRol
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
            'producto_nombre': producto_talla.producto.articulo,
            'talla': producto_talla.talla,
            'sku': producto_talla.sku,
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
            'excluir_de_analitica': producto.excluir_de_analitica,
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
    Actualizar datos del producto base y SINCRONIZAR A TODAS LAS SUCURSALES
    
    Comportamiento:
    - Actualiza el producto seleccionado
    - Busca TODOS los productos con el mismo artículo en TODAS las sucursales
    - Actualiza descripción, categoría, atributos y precios en todos ellos
    - Registra historial de cambios de precio
    - Actualiza lotes FIFO activos con el nuevo precio
    """
    try:
        producto = get_object_or_404(Producto.objects.select_related('sucursal'), id=producto_id)
        
        # Guardar valores anteriores para comparar
        articulo_anterior = producto.articulo
        precio_anterior = producto.precioventa
        sucursal_origen = producto.sucursal
        
        # Parsear datos según el método
        if request.method == 'PUT':
            data = json.loads(request.body)
        else:  # POST
            data = json.loads(request.body)
        
        # Opción para propagar a todas las sucursales (por defecto True)
        propagar_sucursales = data.get('propagar_sucursales', True)
        
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
            precioSugerido = int(data.get('precioSugerido', 0))
            
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
        
        # Obtener categoría
        categoria = None
        categoria_id = data.get('categoria_id')
        if categoria_id:
            try:
                categoria = Categoria.objects.get(id=categoria_id)
            except Categoria.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': f'Categoría con ID {categoria_id} no encontrada'
                }, status=400)
        
        # Obtener atributos
        atributos = {}
        for i in range(1, 5):
            atributo_key = f'atributo{i}_id'
            atributo_id = data.get(atributo_key)
            
            if atributo_id:
                try:
                    atributos[f'atributo{i}'] = AtributoOpcion.objects.get(id=atributo_id)
                except AtributoOpcion.DoesNotExist:
                    return JsonResponse({
                        'success': False,
                        'error': f'Atributo con ID {atributo_id} no encontrado'
                    }, status=400)
            else:
                atributos[f'atributo{i}'] = None
        
        # ========== BUSCAR TODOS LOS PRODUCTOS CON MISMO ARTÍCULO ==========
        if propagar_sucursales:
            # Buscar por artículo original (antes de cambiar)
            productos_a_actualizar = Producto.objects.filter(
                articulo=articulo_anterior
            ).select_related('sucursal')
        else:
            # Solo el producto actual
            productos_a_actualizar = Producto.objects.filter(id=producto_id)
        
        productos_actualizados = 0
        sucursales_afectadas = set()
        lotes_totales_actualizados = 0
        historial_registrado = False
        
        # Importar modelos necesarios
        from .models import LoteProducto
        try:
            from .models import HistorialCambioPrecio
            tiene_historial = True
        except ImportError:
            tiene_historial = False
        
        # ========== ACTUALIZAR TODOS LOS PRODUCTOS ==========
        for prod in productos_a_actualizar:
            precio_prod_anterior = prod.precioventa
            
            # Actualizar campos básicos
            prod.articulo = articulo
            prod.descripcion = data.get('descripcion', '')
            prod.costo = costo
            prod.sobreprecio = sobreprecio
            prod.precioventa = precioventa
            prod.precioSugerido = precioSugerido
            prod.categoria = categoria
            
            # Actualizar atributos
            prod.atributo1 = atributos.get('atributo1')
            prod.atributo2 = atributos.get('atributo2')
            prod.atributo3 = atributos.get('atributo3')
            prod.atributo4 = atributos.get('atributo4')
            
            # Actualizar flag analítica (solo si viene en el payload explícitamente)
            if 'excluir_de_analitica' in data:
                prod.excluir_de_analitica = bool(data['excluir_de_analitica'])
            
            prod.save()
            productos_actualizados += 1
            
            if prod.sucursal:
                sucursales_afectadas.add(prod.sucursal.alias)
            
            # Actualizar lotes FIFO activos de este producto
            lotes_actualizados = LoteProducto.objects.filter(
                producto_talla__producto=prod,
                cantidad_disponible__gt=0,
                activo=True
            ).update(
                precio_venta_unitario=precioventa,
                costo_unitario=costo,
                sobreprecio_unitario=sobreprecio
            )
            lotes_totales_actualizados += lotes_actualizados
            
            # Registrar historial si cambió el precio
            if tiene_historial and precio_prod_anterior != precioventa:
                diferencia = precioventa - precio_prod_anterior
                porcentaje = (diferencia / precio_prod_anterior * 100) if precio_prod_anterior > 0 else 0
                
                try:
                    HistorialCambioPrecio.objects.create(
                        producto=prod,
                        precio_anterior=precio_prod_anterior,
                        precio_nuevo=precioventa,
                        diferencia=diferencia,
                        porcentaje_cambio=porcentaje,
                        motivo=f'Sincronización global desde {sucursal_origen.alias if sucursal_origen else "Sistema"}',
                        tipo_cambio='SINCRONIZACION_GLOBAL',
                        usuario=request.user,
                        ip_address=request.META.get('REMOTE_ADDR'),
                        tallas_afectadas=prod.producto_talla.count(),
                        lotes_afectados=lotes_actualizados
                    )
                    historial_registrado = True
                except Exception:
                    pass  # Si falla el historial, continuar
        
        # Construir mensaje de respuesta
        if productos_actualizados > 1:
            mensaje = f'Producto actualizado en {productos_actualizados} sucursales: {", ".join(sorted(sucursales_afectadas))}'
        else:
            mensaje = 'Producto actualizado exitosamente'
        
        return JsonResponse({
            'success': True,
            'message': mensaje,
            'producto': {
                'id': producto.id,
                'articulo': articulo,
                'precioventa': precioventa
            },
            'sincronizacion': {
                'productos_actualizados': productos_actualizados,
                'sucursales_afectadas': list(sucursales_afectadas),
                'lotes_actualizados': lotes_totales_actualizados,
                'historial_registrado': historial_registrado
            },
            'precio_cambio': precio_anterior != precioventa
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

def _json_permiso_denegado_gestion_producto():
    return JsonResponse({
        'success': False,
        'error': 'No tienes permiso para ajustar stock de productos.'
    }, status=403)


def _tiene_permiso_editar_gestion_producto(request):
    sucursal_id = request.session.get('idSucursalActual')
    return PermisoRol.tiene_permiso(
        request.user,
        'gestion_producto',
        tipo_permiso='puede_editar',
        sucursal_id=sucursal_id
    )


def _consumir_lotes_fifo_ajuste(producto_talla, cantidad):
    """
    Consume lotes FIFO disponibles para una salida de corrección.
    No crea movimientos ni toca stock; eso queda centralizado en
    registrar_movimiento_producto.
    """
    cantidad_pendiente = cantidad
    lotes_consumidos = []

    lotes = (
        LoteProducto.objects.select_for_update()
        .filter(
            producto_talla=producto_talla,
            activo=True,
            agotado=False,
            cantidad_disponible__gt=0,
        )
        .order_by('fecha_ingreso', 'id')
    )

    for lote in lotes:
        if cantidad_pendiente <= 0:
            break

        cantidad_lote = min(cantidad_pendiente, lote.cantidad_disponible)
        lote.cantidad_disponible -= cantidad_lote
        lote.save(update_fields=['cantidad_disponible', 'agotado', 'updated_at'])
        lotes_consumidos.append({
            'lote_id': lote.id,
            'cantidad': cantidad_lote,
        })
        cantidad_pendiente -= cantidad_lote

    return lotes_consumidos, cantidad - cantidad_pendiente


@require_GET
@login_required
def preview_salida_stock_producto(request):
    """
    Devuelve el stock actual de las variaciones seleccionadas antes de aplicar
    una salida/corrección por artículo y sucursal.
    """
    if not _tiene_permiso_editar_gestion_producto(request):
        return _json_permiso_denegado_gestion_producto()

    ids_param = request.GET.get('producto_talla_ids', '')
    ids = []
    for raw_id in ids_param.split(','):
        raw_id = raw_id.strip()
        if raw_id.isdigit():
            ids.append(int(raw_id))

    if not ids:
        return JsonResponse({
            'success': False,
            'error': 'Debes seleccionar al menos una variación.'
        }, status=400)

    variaciones = (
        Producto_Talla.objects
        .filter(id__in=ids)
        .select_related('producto', 'producto__sucursal')
        .order_by('producto__sucursal__alias', 'talla', 'id')
    )

    data = []
    for variacion in variaciones:
        producto = variacion.producto
        data.append({
            'producto_talla_id': variacion.id,
            'producto_id': producto.id if producto else None,
            'articulo': producto.articulo if producto else '',
            'descripcion': producto.descripcion if producto else '',
            'sucursal_id': producto.sucursal_id if producto else None,
            'sucursal_alias': producto.sucursal.alias if producto and producto.sucursal else '',
            'talla': variacion.talla,
            'sku': variacion.sku,
            'stock': max(0, int(variacion.stock or 0)),
        })

    return JsonResponse({
        'success': True,
        'variaciones': data,
    })


@require_POST
@login_required
@transaction.atomic
def aplicar_salida_stock_producto(request):
    """
    Aplica una salida de stock individual o masiva por artículo/sucursal.
    Recibe items [{producto_talla_id, cantidad}] y registra movimientos
    CORRECCION_STOCK negativos con trazabilidad.
    """
    if not _tiene_permiso_editar_gestion_producto(request):
        return _json_permiso_denegado_gestion_producto()

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)

    items = data.get('items') or []
    motivo = (data.get('motivo') or '').strip()
    referencia = (data.get('referencia') or '').strip()

    if not isinstance(items, list) or not items:
        return JsonResponse({
            'success': False,
            'error': 'Debes ingresar al menos una cantidad a descontar.'
        }, status=400)

    if len(motivo) < 10:
        return JsonResponse({
            'success': False,
            'error': 'El motivo es obligatorio y debe tener al menos 10 caracteres.'
        }, status=400)

    cantidades = {}
    for item in items:
        try:
            variacion_id = int(item.get('producto_talla_id'))
            cantidad = int(item.get('cantidad'))
        except (TypeError, ValueError):
            return JsonResponse({
                'success': False,
                'error': 'Las variaciones y cantidades deben ser números válidos.'
            }, status=400)

        if cantidad <= 0:
            return JsonResponse({
                'success': False,
                'error': 'Todas las cantidades deben ser mayores a 0.'
            }, status=400)

        cantidades[variacion_id] = cantidades.get(variacion_id, 0) + cantidad

    variaciones = {
        v.id: v for v in (
            Producto_Talla.objects.select_for_update()
            .filter(id__in=cantidades.keys())
            .select_related('producto', 'producto__sucursal')
        )
    }

    faltantes = set(cantidades.keys()) - set(variaciones.keys())
    if faltantes:
        return JsonResponse({
            'success': False,
            'error': 'Una o más variaciones seleccionadas no existen.'
        }, status=404)

    for variacion_id, cantidad in cantidades.items():
        variacion = variaciones[variacion_id]
        stock_actual = int(variacion.stock or 0)
        if cantidad > stock_actual:
            producto = variacion.producto
            nombre = producto.articulo if producto else variacion.sku
            return JsonResponse({
                'success': False,
                'error': (
                    f'Stock insuficiente para {nombre} talla {variacion.talla}. '
                    f'Disponible: {stock_actual}, solicitado: {cantidad}.'
                )
            }, status=400)

    from .views import registrar_movimiento_producto

    referencia_final = referencia or f'CORRECCION_STOCK_{timezone.now().strftime("%Y%m%d%H%M%S")}'
    movimientos = []
    total_descontado = 0
    total_lotes_consumidos = 0

    for variacion_id, cantidad in cantidades.items():
        variacion = variaciones[variacion_id]
        lotes_consumidos, cantidad_fifo = _consumir_lotes_fifo_ajuste(variacion, cantidad)
        total_lotes_consumidos += len(lotes_consumidos)

        observaciones = motivo
        if cantidad_fifo < cantidad:
            observaciones = (
                f'{motivo} | FIFO parcial: {cantidad_fifo}/{cantidad} unidades '
                'con lotes disponibles.'
            )

        movimiento = registrar_movimiento_producto(
            producto_talla=variacion,
            concepto='CORRECCION_STOCK',
            cantidad=-cantidad,
            responsable=request.user,
            sucursal_origen=variacion.producto.sucursal if variacion.producto else None,
            sucursal_destino=variacion.producto.sucursal if variacion.producto else None,
            observaciones=observaciones,
            referencia_externa=referencia_final,
            crear_lote_fifo=False,
            consumir_lotes=False  # los lotes ya se consumieron arriba (FIFO ajuste)
        )
        movimiento.tipo_movimiento = 'EGRESO'
        movimiento.save(update_fields=['tipo_movimiento', 'updated_at'])

        variacion.refresh_from_db(fields=['stock'])
        movimientos.append({
            'movimiento_id': movimiento.id,
            'producto_talla_id': variacion.id,
            'talla': variacion.talla,
            'sku': variacion.sku,
            'cantidad': cantidad,
            'nuevo_stock': variacion.stock,
            'lotes_consumidos': len(lotes_consumidos),
        })
        total_descontado += cantidad

    return JsonResponse({
        'success': True,
        'message': f'Se descontaron {total_descontado} unidad(es) correctamente.',
        'referencia': referencia_final,
        'movimientos': movimientos,
        'total_lotes_consumidos': total_lotes_consumidos,
    })

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
                responsable=request.user.username,
                observaciones=motivo,
                estado='COMPLETADO',
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
                responsable=request.user.username,
                observaciones=motivo,
                referencia_externa=f'AJUSTE_MANUAL_{timezone.now().strftime("%Y%m%d%H%M%S")}'
            )

            # El consumir_stock_fifo ya crea el movimiento, pero vamos a obtenerlo
            # para retornar su ID
            ultimo_movimiento = Movimientos_Producto.objects.filter(
                ProductoTalla=variacion,
                responsable=request.user.username
            ).order_by('-id').first()

            # consumir_stock_fifo (via registrar_movimiento_producto) ya
            # descontó el stock plano; solo refrescar para responder el valor.
            variacion.refresh_from_db(fields=['stock'])
            
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


# ========== LISTAR PRODUCTOS EXCLUIDOS DE ANALÍTICA ==========

@require_GET
@login_required
def listar_productos_excluidos(request):
    """
    Devuelve todos los productos con `excluir_de_analitica=True`,
    agrupados por artículo para evitar duplicados entre sucursales.

    Parámetros opcionales:
      - q: texto de búsqueda (coincide con articulo, descripcion o SKU).
    """
    try:
        q = (request.GET.get('q') or '').strip()

        qs = Producto.objects.filter(excluir_de_analitica=True).select_related(
            'sucursal', 'categoria', 'atributo1', 'atributo2', 'atributo3'
        )

        if q:
            filtro = Q(articulo__icontains=q) | Q(descripcion__icontains=q)
            if q.isdigit():
                filtro |= Q(producto_talla__sku=int(q))
            qs = qs.filter(filtro).distinct()

        agrupados = {}
        for prod in qs.order_by('articulo', 'sucursal__alias'):
            key = prod.articulo
            if key not in agrupados:
                agrupados[key] = {
                    'articulo': prod.articulo,
                    'descripcion': prod.descripcion or '',
                    'categoria': prod.categoria.nombre if prod.categoria else '',
                    'marca': prod.atributo1.valor if prod.atributo1 else '',
                    'color': prod.atributo2.valor if prod.atributo2 else '',
                    'genero': prod.atributo3.valor if prod.atributo3 else '',
                    'precioventa': prod.precioventa,
                    'producto_ids': [],
                    'sucursales': [],
                }
            agrupados[key]['producto_ids'].append(prod.id)
            if prod.sucursal:
                alias = prod.sucursal.alias
                if alias not in agrupados[key]['sucursales']:
                    agrupados[key]['sucursales'].append(alias)

        data = sorted(agrupados.values(), key=lambda x: x['articulo'].lower())

        return JsonResponse({
            'success': True,
            'total': len(data),
            'productos': data,
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al listar excluidos: {str(e)}'
        }, status=500)


# ========== EXCLUIR/INCLUIR MASIVAMENTE DE ANALÍTICA POR SUCURSAL ==========

@require_POST
@login_required
@transaction.atomic
def excluir_analitica_masivo(request):
    """
    Excluir o incluir de la analítica. Acepta dos modos:
      - Por producto_ids: { "producto_ids": [1, 2, 3], "excluir": true|false, "propagar": true|false }
      - Por sucursal:     { "sucursal_id": <int|"todas">, "excluir": true|false }

    Si propagar=True (default) y se usa producto_ids, busca el articulo de cada producto
    y aplica el cambio a TODOS los Producto con el mismo nombre en cualquier sucursal.
    """
    try:
        data = json.loads(request.body)
        excluir = bool(data.get('excluir', True))
        propagar = data.get('propagar', True)  # propagar a todas las sucursales por defecto
        producto_ids = data.get('producto_ids')
        sucursal_id = data.get('sucursal_id')

        if producto_ids is not None:
            if not isinstance(producto_ids, list) or len(producto_ids) == 0:
                return JsonResponse({'success': False, 'error': 'producto_ids debe ser una lista no vacía'}, status=400)

            if propagar:
                # Obtener los nombres de artículo de los productos seleccionados
                articulos = list(
                    Producto.objects.filter(id__in=producto_ids)
                    .values_list('articulo', flat=True)
                    .distinct()
                )
                # Actualizar TODOS los productos con esos nombres en cualquier sucursal
                qs = Producto.objects.filter(articulo__in=articulos)
                nombre_scope = f'{len(articulos)} artículo(s) — todas las sucursales'
            else:
                qs = Producto.objects.filter(id__in=producto_ids)
                nombre_scope = f'{len(producto_ids)} producto(s) seleccionado(s)'

        elif sucursal_id is not None:
            qs = Producto.objects.all()
            if str(sucursal_id) != 'todas':
                try:
                    sucursal_obj = Sucursal.objects.get(id=sucursal_id)
                except Sucursal.DoesNotExist:
                    return JsonResponse({'success': False, 'error': 'Sucursal no encontrada'}, status=404)
                qs = qs.filter(sucursal=sucursal_obj)
                nombre_scope = sucursal_obj.alias
            else:
                nombre_scope = 'Todas las sucursales'
        else:
            return JsonResponse({'success': False, 'error': 'Se requiere producto_ids o sucursal_id'}, status=400)

        cantidad = qs.update(excluir_de_analitica=excluir)
        accion = 'excluidos de' if excluir else 'incluidos en'
        return JsonResponse({
            'success': True,
            'message': f'{cantidad} registros {accion} la analítica ({nombre_scope})',
            'cantidad': cantidad,
            'excluir': excluir,
            'propagado': bool(propagar),
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': f'Error: {str(e)}'}, status=500)


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


# ========== IMPACTO DE RECATEGORIZACIÓN ==========

@require_GET
@login_required
def obtener_impacto_recategorizacion(request, producto_id):
    """
    Calcula cuántos registros se verían afectados al cambiar atributos de un producto.
    Los movimientos/ventas/DTEs referencian Producto_Talla → Producto vía FK,
    por lo que cambiar atributos en Producto se refleja automáticamente.
    Este endpoint solo muestra el alcance para que el usuario tenga visibilidad.
    """
    try:
        producto = get_object_or_404(Producto, id=producto_id)

        # Encontrar todos los productos con el mismo artículo (en todas las sucursales)
        productos_mismo_articulo = Producto.objects.filter(
            articulo=producto.articulo
        ).select_related('sucursal')

        producto_ids = list(productos_mismo_articulo.values_list('id', flat=True))
        sucursales = list(
            productos_mismo_articulo.exclude(sucursal__isnull=True)
            .values_list('sucursal__alias', flat=True).distinct()
        )

        # Producto_Tallas asociadas
        talla_ids = list(
            Producto_Talla.objects.filter(producto_id__in=producto_ids)
            .values_list('id', flat=True)
        )

        # Contar movimientos
        total_movimientos = Movimientos_Producto.objects.filter(
            ProductoTalla_id__in=talla_ids
        ).count()

        # Contar tickets de venta
        total_tickets = Ticket_Productos.objects.filter(
            ProductoTalla_id__in=talla_ids
        ).count()

        # Contar líneas DTE
        total_dtes = Dte_Productos.objects.filter(
            productoTalla_id__in=talla_ids
        ).count()

        return JsonResponse({
            'success': True,
            'impacto': {
                'total_sucursales': len(sucursales),
                'sucursales': sucursales,
                'total_productos': len(producto_ids),
                'total_tallas': len(talla_ids),
                'total_movimientos': total_movimientos,
                'total_tickets': total_tickets,
                'total_dtes': total_dtes,
            }
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al calcular impacto: {str(e)}'
        }, status=500)


# ========== EDICIÓN MASIVA DE PRODUCTOS ==========

@require_POST
@login_required
@transaction.atomic
def actualizar_productos_masivo(request):
    """
    Actualiza campos seleccionados en múltiples productos a la vez.
    Solo modifica los campos que vienen con 'aplicar_<campo>': True.
    Los movimientos históricos NO se modifican: al estar vinculados por FK
    (Movimiento → Producto_Talla → Producto), las consultas reflejan
    automáticamente los nuevos atributos del producto.
    Las cantidades, precios y fechas almacenadas en cada movimiento permanecen intactas.
    """
    try:
        data = json.loads(request.body)
        producto_ids = data.get('producto_ids', [])
        campos = data.get('campos', {})
        # La edición SIEMPRE debe alcanzar el código en todas las sucursales
        # (política 2026-07: una variante no puede quedar distinta por bodega).
        propagar = data.get('propagar_sucursales', True)

        if not producto_ids:
            return JsonResponse({'success': False, 'error': 'No se seleccionaron productos'}, status=400)
        if not campos:
            return JsonResponse({'success': False, 'error': 'No se seleccionaron campos a modificar'}, status=400)

        productos_base = Producto.objects.filter(id__in=producto_ids).select_related('sucursal')
        if not productos_base.exists():
            return JsonResponse({'success': False, 'error': 'Productos no encontrados'}, status=404)

        if propagar:
            articulos = list(productos_base.values_list('articulo', flat=True).distinct())
            productos = Producto.objects.filter(articulo__in=articulos).select_related('sucursal')
        else:
            productos = productos_base

        update_kwargs = {}
        campos_aplicados = []

        if campos.get('aplicar_categoria'):
            cat_id = campos.get('categoria_id')
            if cat_id:
                try:
                    cat = Categoria.objects.get(id=cat_id)
                    update_kwargs['categoria'] = cat
                    campos_aplicados.append('categoría')
                except Categoria.DoesNotExist:
                    return JsonResponse({'success': False, 'error': 'Categoría no encontrada'}, status=400)
            else:
                update_kwargs['categoria'] = None
                campos_aplicados.append('categoría')

        for i in range(1, 5):
            key = f'aplicar_atributo{i}'
            if campos.get(key):
                attr_id = campos.get(f'atributo{i}_id')
                if attr_id:
                    try:
                        attr = AtributoOpcion.objects.get(id=attr_id)
                        update_kwargs[f'atributo{i}'] = attr
                    except AtributoOpcion.DoesNotExist:
                        return JsonResponse({'success': False, 'error': f'Atributo {i} no encontrado'}, status=400)
                else:
                    update_kwargs[f'atributo{i}'] = None
                nombres = {1: 'marca', 2: 'color', 3: 'género', 4: 'otro'}
                campos_aplicados.append(nombres.get(i, f'atributo{i}'))

        if campos.get('aplicar_precios'):
            try:
                costo = int(campos.get('costo', 0))
                sobreprecio = int(campos.get('sobreprecio', 0))
                precioventa = int(campos.get('precioventa', 0))
                if precioventa <= 0:
                    return JsonResponse({'success': False, 'error': 'Precio de venta debe ser mayor a 0'}, status=400)
                update_kwargs['costo'] = costo
                update_kwargs['sobreprecio'] = sobreprecio
                update_kwargs['precioventa'] = precioventa
                campos_aplicados.append('precios')
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'error': 'Valores de precio inválidos'}, status=400)

        # ── Especialidades v1.2 (multi-etiqueta, atributo "Especialidad") ──
        # Modos: agregar (suma sin tocar las existentes) | reemplazar (deja solo
        # las seleccionadas) | quitar (elimina las seleccionadas).
        esp_modo = None
        esp_attr = None
        esp_opciones = []
        if campos.get('aplicar_especialidad'):
            from .models import Productos_Atributos, ProductoAtributoValor
            esp_modo = campos.get('especialidad_modo', 'agregar')
            if esp_modo not in ('agregar', 'reemplazar', 'quitar'):
                return JsonResponse({'success': False, 'error': 'Modo de especialidad inválido'}, status=400)
            esp_attr = Productos_Atributos.objects.filter(nombre__iexact='Especialidad').first()
            if esp_attr is None:
                return JsonResponse({'success': False, 'error': 'El atributo Especialidad no existe (corre sembrar_taxonomia_v12)'}, status=400)
            esp_ids = [e for e in (campos.get('especialidad_ids') or []) if e]
            esp_opciones = list(AtributoOpcion.objects.filter(atributo=esp_attr, id__in=esp_ids))
            if not esp_opciones and esp_modo != 'reemplazar':
                return JsonResponse({'success': False, 'error': 'No se seleccionaron especialidades'}, status=400)
            campos_aplicados.append(f'especialidades ({esp_modo})')

        if not update_kwargs and not esp_modo:
            return JsonResponse({'success': False, 'error': 'No hay campos válidos para actualizar'}, status=400)

        count = 0
        sucursales_set = set()
        lotes_total = 0

        for prod in productos:
            if update_kwargs:
                for field, value in update_kwargs.items():
                    setattr(prod, field, value)
                prod.save()
            count += 1
            if prod.sucursal:
                sucursales_set.add(prod.sucursal.alias)

            if 'precioventa' in update_kwargs:
                lotes = LoteProducto.objects.filter(
                    producto_talla__producto=prod,
                    cantidad_disponible__gt=0,
                    activo=True
                ).update(
                    precio_venta_unitario=update_kwargs['precioventa'],
                    costo_unitario=update_kwargs.get('costo', prod.costo),
                    sobreprecio_unitario=update_kwargs.get('sobreprecio', prod.sobreprecio),
                )
                lotes_total += lotes

        # ── Aplicar especialidades en bloque ──
        if esp_modo:
            from .models import ProductoAtributoValor
            prod_ids_all = list(productos.values_list('id', flat=True))
            ids_sel = [o.id for o in esp_opciones]
            if esp_modo == 'reemplazar':
                ProductoAtributoValor.objects.filter(
                    producto_id__in=prod_ids_all, atributo=esp_attr
                ).exclude(opcion_id__in=ids_sel).delete()
            if esp_modo == 'quitar':
                ProductoAtributoValor.objects.filter(
                    producto_id__in=prod_ids_all, atributo=esp_attr, opcion_id__in=ids_sel
                ).delete()
            else:  # agregar / reemplazar: asegurar presencia sin duplicar
                existentes = set(ProductoAtributoValor.objects.filter(
                    producto_id__in=prod_ids_all, atributo=esp_attr, opcion_id__in=ids_sel
                ).values_list('producto_id', 'opcion_id'))
                nuevos = [ProductoAtributoValor(producto_id=pid, atributo=esp_attr, opcion_id=oid)
                          for pid in prod_ids_all for oid in ids_sel
                          if (pid, oid) not in existentes]
                if nuevos:
                    ProductoAtributoValor.objects.bulk_create(nuevos)

        talla_ids = list(
            Producto_Talla.objects.filter(producto__in=productos).values_list('id', flat=True)
        )
        movimientos_afectados = Movimientos_Producto.objects.filter(ProductoTalla_id__in=talla_ids).count()
        tickets_afectados = Ticket_Productos.objects.filter(ProductoTalla_id__in=talla_ids).count()

        return JsonResponse({
            'success': True,
            'message': f'{count} producto(s) actualizados',
            'detalle': {
                'productos_actualizados': count,
                'campos_aplicados': campos_aplicados,
                'sucursales': list(sucursales_set),
                'lotes_actualizados': lotes_total,
                'movimientos_vinculados': movimientos_afectados,
                'tickets_vinculados': tickets_afectados,
            }
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
@login_required
def preview_edicion_masiva(request):
    """
    Calcula el impacto de una edición masiva antes de aplicarla.
    """
    try:
        data = json.loads(request.body)
        producto_ids = data.get('producto_ids', [])
        propagar = data.get('propagar_sucursales', True)

        productos_base = Producto.objects.filter(id__in=producto_ids).select_related('sucursal')

        if propagar:
            articulos = list(productos_base.values_list('articulo', flat=True).distinct())
            productos = Producto.objects.filter(articulo__in=articulos).select_related('sucursal')
        else:
            productos = productos_base

        prod_ids = list(productos.values_list('id', flat=True))
        sucursales = list(
            productos.exclude(sucursal__isnull=True)
            .values_list('sucursal__alias', flat=True).distinct()
        )
        talla_ids = list(
            Producto_Talla.objects.filter(producto_id__in=prod_ids).values_list('id', flat=True)
        )
        movimientos = Movimientos_Producto.objects.filter(ProductoTalla_id__in=talla_ids).count()
        tickets = Ticket_Productos.objects.filter(ProductoTalla_id__in=talla_ids).count()
        dtes = Dte_Productos.objects.filter(productoTalla_id__in=talla_ids).count()

        return JsonResponse({
            'success': True,
            'impacto': {
                'productos_seleccionados': len(producto_ids),
                'productos_total': len(prod_ids),
                'sucursales': sucursales,
                'total_tallas': len(talla_ids),
                'total_movimientos': movimientos,
                'total_tickets': tickets,
                'total_dtes': dtes,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

