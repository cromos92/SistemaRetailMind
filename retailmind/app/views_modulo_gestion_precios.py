"""
Módulo de Gestión de Precios - RetailMind
Sistema avanzado de gestión de precios con recomendaciones inteligentes
"""

from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET, require_POST
from django.db.models import Sum, F, Q, Avg, Count, ExpressionWrapper, DecimalField, Min, Max
from django.core.paginator import Paginator
from django.utils import timezone
from django.db import transaction
from decimal import Decimal
import json
from datetime import datetime, timedelta

from .models import (
    Producto, Producto_Talla, LoteProducto, Categoria, AtributoOpcion,
    Sucursal, Movimientos_Producto, Ticket_Productos, Ticket,
    CambioPrecioPendiente, NotificacionCambioPrecio
)


# ========== VISTAS PRINCIPALES ==========

@login_required
def gestion_precios_view(request):
    """Vista principal del módulo de gestión de precios"""
    return render(request, 'vistas/modulo_existencias/gestion_precios.html')


@login_required
def revisar_cambios_precios_view(request):
    """Vista para revisar y aprobar cambios de precios pendientes"""
    return render(request, 'vistas/modulo_existencias/revisar_cambios_precios.html')


@login_required
def edicion_rapida_precios_view(request):
    """Vista de edición rápida con navegación por Tab"""
    return render(request, 'vistas/modulo_existencias/edicion_rapida_precios.html')


# ========== ESTADÍSTICAS GENERALES ==========

@require_GET
@login_required
def obtener_estadisticas(request):
    """Obtener estadísticas generales para el dashboard"""
    try:
        sucursal_id = request.session.get('idSucursalActual')
        
        # Filtrar por sucursal si está seleccionada
        queryset_productos = Producto.objects.all()
        queryset_tallas = Producto_Talla.objects.all()
        
        if sucursal_id:
            queryset_productos = queryset_productos.filter(sucursal_id=sucursal_id)
            queryset_tallas = queryset_tallas.filter(producto__sucursal_id=sucursal_id)
        
        # Total de productos activos
        total_productos = queryset_tallas.count()
        
        # Valor total del inventario
        valor_inventario = 0
        suma_margenes = 0
        count_margenes = 0
        
        for pt in queryset_tallas:
            # Calcular valor del inventario basado en lotes FIFO
            lotes = LoteProducto.objects.filter(
                producto_talla=pt,
                cantidad_disponible__gt=0,
                activo=True
            )
            
            for lote in lotes:
                valor_inventario += lote.cantidad_disponible * lote.costo_unitario
                
                # Calcular margen
                if lote.precio_venta_unitario > 0:
                    margen = ((lote.precio_venta_unitario - lote.costo_unitario) / lote.precio_venta_unitario) * 100
                    suma_margenes += margen
                    count_margenes += 1
        
        margen_promedio = suma_margenes / count_margenes if count_margenes > 0 else 0
        
        # Inventario antiguo (más de 365 días)
        fecha_limite = timezone.now() - timedelta(days=365)
        inventario_antiguo = LoteProducto.objects.filter(
            fecha_ingreso__lt=fecha_limite,
            cantidad_disponible__gt=0,
            activo=True
        )
        
        if sucursal_id:
            inventario_antiguo = inventario_antiguo.filter(
                producto_talla__producto__sucursal_id=sucursal_id
            )
        
        inventario_antiguo_count = inventario_antiguo.count()
        
        return JsonResponse({
            'success': True,
            'total_productos': total_productos,
            'valor_inventario': float(valor_inventario),
            'margen_promedio': float(margen_promedio),
            'inventario_antiguo': inventario_antiguo_count
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener estadísticas: {str(e)}'
        })


# ========== BÚSQUEDA Y FILTRADO DE PRODUCTOS ==========

@require_GET
@login_required
def buscar_productos(request):
    """Buscar productos con filtros avanzados (agrupados por producto, no por talla)"""
    try:
        # Parámetros de búsqueda
        search = request.GET.get('search', '').strip()
        categoria_id = request.GET.get('categoria')
        marca_id = request.GET.get('marca')
        sucursal_id = request.GET.get('sucursal') or request.session.get('idSucursalActual')
        precio_min = request.GET.get('precio_min')
        precio_max = request.GET.get('precio_max')
        margen_min = request.GET.get('margen_min')
        stock_min = request.GET.get('stock_min')
        antiguedad = request.GET.get('antiguedad')
        anio = request.GET.get('anio')
        
        # Paginación
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 50))
        
        # Construir queryset base - AGRUPADO POR PRODUCTO
        queryset = Producto.objects.select_related(
            'categoria',
            'atributo1',
            'atributo2',
            'sucursal'
        ).prefetch_related('producto_talla').all()
        
        # Filtro de búsqueda por texto
        if search:
            queryset = queryset.filter(
                Q(articulo__icontains=search) |
                Q(descripcion__icontains=search) |
                Q(producto_talla__sku__icontains=search)
            ).distinct()
        
        # Filtro por categoría
        if categoria_id:
            queryset = queryset.filter(categoria_id=categoria_id)
        
        # Filtro por marca (atributo1)
        if marca_id:
            queryset = queryset.filter(atributo1_id=marca_id)
        
        # Filtro por sucursal
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Preparar datos para respuesta
        productos_data = []
        
        for producto in queryset:
            # Obtener todas las tallas del producto
            tallas = producto.producto_talla.all()
            
            if not tallas.exists():
                continue
            
            # Calcular totales de todas las tallas
            stock_total = 0
            costo_total_ponderado = 0
            cantidad_total = 0
            fecha_ingreso_mas_antiguo = None
            tallas_list = []
            
            for pt in tallas:
                stock_total += pt.stock
                tallas_list.append(pt.talla)
                
                # Calcular datos de lotes para cada talla
                lotes = LoteProducto.objects.filter(
                    producto_talla=pt,
                    cantidad_disponible__gt=0,
                    activo=True
                )
                
                for lote in lotes:
                    costo_total_ponderado += lote.cantidad_disponible * lote.costo_unitario
                    cantidad_total += lote.cantidad_disponible
                    
                    if fecha_ingreso_mas_antiguo is None or lote.fecha_ingreso < fecha_ingreso_mas_antiguo:
                        fecha_ingreso_mas_antiguo = lote.fecha_ingreso
            
            if cantidad_total == 0:
                continue
            
            # Filtro por stock mínimo
            if stock_min and stock_total < int(stock_min):
                continue
            
            costo_promedio = costo_total_ponderado / cantidad_total
            precio_venta = producto.precioventa
            
            # Aplicar filtros de precio
            if precio_min and precio_venta < float(precio_min):
                continue
            if precio_max and precio_venta > float(precio_max):
                continue
            
            # Calcular margen
            margen = ((precio_venta - costo_promedio) / precio_venta * 100) if precio_venta > 0 else 0
            
            # Aplicar filtro de margen
            if margen_min and margen < float(margen_min):
                continue
            
            # Calcular antigüedad
            dias_inventario = 0
            if fecha_ingreso_mas_antiguo:
                dias_inventario = (timezone.now() - fecha_ingreso_mas_antiguo).days
            
            # Aplicar filtro de antigüedad
            if antiguedad:
                if antiguedad == 'nuevo' and dias_inventario >= 180:
                    continue
                elif antiguedad == 'medio' and (dias_inventario < 180 or dias_inventario >= 365):
                    continue
                elif antiguedad == 'antiguo' and dias_inventario < 365:
                    continue
            
            # Aplicar filtro de año
            if anio and fecha_ingreso_mas_antiguo:
                if fecha_ingreso_mas_antiguo.year != int(anio):
                    continue
            
            # Agregar a resultados
            productos_data.append({
                'id': producto.id,  # ID del producto (no de la talla)
                'sku': ', '.join([str(t.sku) for t in tallas[:3]]),  # Primeros 3 SKUs
                'nombre': producto.articulo,
                'talla': f"{len(tallas_list)} tallas: {', '.join(str(t) for t in tallas_list[:5])}",  # Mostrar tallas
                'categoria': producto.categoria.nombre if producto.categoria else None,
                'marca': producto.atributo1.valor if producto.atributo1 else None,
                'sucursal': producto.sucursal.alias,
                'costo': float(costo_promedio),
                'precio_venta': float(precio_venta),
                'stock': stock_total,
                'dias_inventario': dias_inventario,
                'margen': float(margen),
                'cantidad_tallas': len(tallas_list)
            })
        
        # Paginación manual
        paginator = Paginator(productos_data, per_page)
        page_obj = paginator.get_page(page)
        
        return JsonResponse({
            'success': True,
            'productos': list(page_obj),
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
            'error': f'Error al buscar productos: {str(e)}'
        })


# ========== SISTEMA DE RECOMENDACIONES INTELIGENTES ==========

@require_GET
@login_required
def obtener_recomendaciones(request, producto_id):
    """
    Sistema de recomendaciones inteligentes de precio
    Analiza múltiples factores para sugerir el precio óptimo
    Trabaja a nivel de producto (todas las tallas)
    """
    try:
        producto = Producto.objects.select_related('categoria', 'atributo1', 'sucursal').get(id=producto_id)
        tallas = producto.producto_talla.all()
        
        if not tallas.exists():
            return JsonResponse({
                'success': False,
                'error': 'No hay tallas para este producto'
            })
        
        # Obtener todos los lotes de todas las tallas
        lotes = LoteProducto.objects.filter(
            producto_talla__producto=producto,
            activo=True
        ).order_by('fecha_ingreso')
        
        lotes_disponibles = lotes.filter(cantidad_disponible__gt=0)
        
        if not lotes_disponibles.exists():
            return JsonResponse({
                'success': False,
                'error': 'No hay lotes disponibles para este producto'
            })
        
        # === ANÁLISIS ACTUAL ===
        costo_total = 0
        cantidad_total = 0
        precio_venta_promedio = 0
        dias_total = 0
        
        for lote in lotes_disponibles:
            costo_total += lote.cantidad_disponible * lote.costo_unitario
            cantidad_total += lote.cantidad_disponible
            precio_venta_promedio += lote.precio_venta_unitario
            dias_inventario = (timezone.now() - lote.fecha_ingreso).days
            dias_total += dias_inventario * lote.cantidad_disponible
        
        costo_promedio = costo_total / cantidad_total if cantidad_total > 0 else 0
        precio_actual = precio_venta_promedio / lotes_disponibles.count() if lotes_disponibles.count() > 0 else 0
        margen_actual = ((precio_actual - costo_promedio) / precio_actual * 100) if precio_actual > 0 else 0
        dias_promedio = dias_total / cantidad_total if cantidad_total > 0 else 0
        
        # === ANÁLISIS DE VENTAS (ÚLTIMOS 30 DÍAS) ===
        fecha_inicio = timezone.now() - timedelta(days=30)
        
        # Sumar ventas de todas las tallas del producto
        ventas_recientes = Ticket_Productos.objects.filter(
            ProductoTalla__producto=producto,
            idTicket__fecha__gte=fecha_inicio.date(),
            idTicket__estado='PAGADO'
        ).aggregate(
            total_vendido=Sum('stock')
        )
        
        ventas_30_dias = ventas_recientes['total_vendido'] or 0
        
        # Ventas de los 30 días anteriores para comparar tendencia
        fecha_inicio_anterior = fecha_inicio - timedelta(days=30)
        ventas_anteriores = Ticket_Productos.objects.filter(
            ProductoTalla__producto=producto,
            idTicket__fecha__gte=fecha_inicio_anterior.date(),
            idTicket__fecha__lt=fecha_inicio.date(),
            idTicket__estado='PAGADO'
        ).aggregate(
            total_vendido=Sum('stock')
        )
        
        ventas_periodo_anterior = ventas_anteriores['total_vendido'] or 0
        
        # === CALCULAR FACTORES DE AJUSTE ===
        
        # Factor 1: Antigüedad del Inventario
        factor_antiguedad = 0
        if dias_promedio > 365:  # Más de 1 año
            factor_antiguedad = -0.20  # Descuento del 20%
        elif dias_promedio > 180:  # 6 meses a 1 año
            factor_antiguedad = -0.10  # Descuento del 10%
        elif dias_promedio > 90:  # 3 a 6 meses
            factor_antiguedad = -0.05  # Descuento del 5%
        else:  # Menos de 3 meses
            factor_antiguedad = 0  # Sin ajuste
        
        # Factor 2: Rotación de Inventario
        if cantidad_total > 0:
            dias_para_agotar = (cantidad_total / (ventas_30_dias / 30)) if ventas_30_dias > 0 else 999
        else:
            dias_para_agotar = 0
        
        factor_rotacion = 0
        velocidad_rotacion = "Sin ventas"
        
        if ventas_30_dias == 0:
            factor_rotacion = -0.15  # Sin ventas, bajar precio
            velocidad_rotacion = "Sin ventas (descuento necesario)"
        elif dias_para_agotar < 30:
            factor_rotacion = 0.05  # Se vende rápido, puede subir precio
            velocidad_rotacion = "Muy rápida (alta demanda)"
        elif dias_para_agotar < 90:
            factor_rotacion = 0  # Rotación normal
            velocidad_rotacion = "Normal"
        elif dias_para_agotar < 180:
            factor_rotacion = -0.05  # Rotación lenta
            velocidad_rotacion = "Lenta"
        else:
            factor_rotacion = -0.10  # Muy lenta
            velocidad_rotacion = "Muy lenta"
        
        # Factor 3: Tendencia de Ventas
        tendencia_ventas = "Estable"
        if ventas_periodo_anterior > 0:
            variacion = ((ventas_30_dias - ventas_periodo_anterior) / ventas_periodo_anterior) * 100
            if variacion > 20:
                tendencia_ventas = "Creciente (+{:.0f}%)".format(variacion)
            elif variacion < -20:
                tendencia_ventas = "Decreciente ({:.0f}%)".format(variacion)
                factor_rotacion -= 0.05  # Ajuste adicional si ventas están cayendo
            else:
                tendencia_ventas = "Estable"
        elif ventas_30_dias > 0:
            tendencia_ventas = "Nuevas ventas"
        
        # Factor 4: Nivel de Stock
        factor_stock = 0
        if cantidad_total < 3:
            factor_stock = 0.10  # Poco stock, puede subir precio
        elif cantidad_total < 10:
            factor_stock = 0  # Stock normal
        elif cantidad_total < 50:
            factor_stock = -0.05  # Mucho stock
        else:
            factor_stock = -0.10  # Stock excesivo
        
        # === CALCULAR PRECIO RECOMENDADO ===
        
        # Factor combinado
        factor_total = factor_antiguedad + factor_rotacion + factor_stock
        
        # Aplicar factor al precio actual
        precio_recomendado = precio_actual * (1 + factor_total)
        
        # Asegurar margen mínimo del 10%
        precio_minimo_margen = costo_promedio / 0.90  # 10% margen mínimo
        if precio_recomendado < precio_minimo_margen:
            precio_recomendado = precio_minimo_margen
        
        # Redondeo psicológico (terminar en 90 o 490)
        precio_recomendado = int(precio_recomendado)
        ultimo_digito = precio_recomendado % 1000
        
        if ultimo_digito < 490:
            precio_recomendado = (precio_recomendado // 1000) * 1000 + 490
        else:
            precio_recomendado = (precio_recomendado // 1000) * 1000 + 990
        
        # Calcular margen recomendado
        margen_recomendado = ((precio_recomendado - costo_promedio) / precio_recomendado * 100) if precio_recomendado > 0 else 0
        
        # === JUSTIFICACIÓN ===
        justificacion_partes = []
        
        if factor_antiguedad < 0:
            justificacion_partes.append(f"Inventario antiguo ({int(dias_promedio)} días)")
        
        if factor_rotacion > 0:
            justificacion_partes.append("Alta rotación")
        elif factor_rotacion < -0.05:
            justificacion_partes.append("Baja rotación")
        
        if factor_stock < 0:
            justificacion_partes.append("Stock elevado")
        elif factor_stock > 0:
            justificacion_partes.append("Stock limitado")
        
        if ventas_30_dias == 0:
            justificacion_partes.append("Sin ventas recientes")
        
        if not justificacion_partes:
            justificacion = "Precio óptimo según análisis de mercado"
        else:
            justificacion = "Ajuste por: " + ", ".join(justificacion_partes)
        
        # === RESPUESTA ===
        skus_list = [str(t.sku) for t in tallas]
        return JsonResponse({
            'success': True,
            'recomendaciones': {
                'producto_nombre': producto.articulo,
                'sku': ', '.join(skus_list[:3]) + (f' (+{len(skus_list)-3} más)' if len(skus_list) > 3 else ''),
                
                # Análisis actual
                'precio_actual': float(precio_actual),
                'costo_promedio': float(costo_promedio),
                'margen_actual': float(margen_actual),
                'stock_actual': int(cantidad_total),
                'dias_promedio_inventario': int(dias_promedio),
                
                # Análisis de ventas
                'ventas_30_dias': int(ventas_30_dias),
                'velocidad_rotacion': velocidad_rotacion,
                'tendencia_ventas': tendencia_ventas,
                
                # Factores de ajuste
                'factor_antiguedad': float(factor_antiguedad),
                'factor_rotacion': float(factor_rotacion),
                'factor_stock': float(factor_stock),
                'factor_total': float(factor_total),
                
                # Recomendación
                'precio_recomendado': float(precio_recomendado),
                'margen_recomendado': float(margen_recomendado),
                'justificacion': justificacion,
                'variacion_porcentual': float(((precio_recomendado - precio_actual) / precio_actual * 100) if precio_actual > 0 else 0)
            }
        })
        
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Producto no encontrado'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al generar recomendaciones: {str(e)}'
        })


# ========== ACTUALIZACIÓN DE PRECIOS ==========

@require_POST
@login_required
@transaction.atomic
def actualizar_precio(request):
    """Actualizar precio de un producto (todas las tallas)"""
    try:
        data = json.loads(request.body)
        producto_id = data.get('producto_id')
        nuevo_precio = data.get('nuevo_precio')
        
        if not producto_id or not nuevo_precio:
            return JsonResponse({
                'success': False,
                'error': 'Parámetros incompletos'
            })
        
        # Convertir a entero
        nuevo_precio = int(nuevo_precio)
        
        producto = Producto.objects.get(id=producto_id)
        
        # Actualizar precio base del producto
        producto.precioventa = nuevo_precio
        producto.save()
        
        # Actualizar precio en lotes activos de TODAS las tallas
        lotes_actualizados = LoteProducto.objects.filter(
            producto_talla__producto=producto,
            cantidad_disponible__gt=0,
            activo=True
        ).update(precio_venta_unitario=nuevo_precio)
        
        # Contar tallas actualizadas
        tallas_actualizadas = producto.producto_talla.count()
        
        return JsonResponse({
            'success': True,
            'message': f'Precio actualizado para {tallas_actualizadas} tallas',
            'lotes_actualizados': lotes_actualizados,
            'tallas_actualizadas': tallas_actualizadas
        })
        
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Producto no encontrado'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al actualizar precio: {str(e)}'
        })


# ========== MODIFICACIÓN MASIVA ==========

@require_POST
@login_required
@transaction.atomic
def modificacion_masiva(request):
    """Modificar precios de múltiples productos de forma masiva (actualiza todas las tallas)"""
    try:
        data = json.loads(request.body)
        productos_ids = data.get('productos', [])
        tipo_modificacion = data.get('tipo_modificacion')
        valor = Decimal(str(data.get('valor', 0)))
        
        if not productos_ids or not tipo_modificacion:
            return JsonResponse({
                'success': False,
                'error': 'Parámetros incompletos'
            })
        
        productos_actualizados = 0
        tallas_actualizadas_total = 0
        
        for producto_id in productos_ids:
            try:
                producto = Producto.objects.get(id=producto_id)
                
                # Obtener precio actual del producto
                precio_actual = Decimal(str(producto.precioventa))
                
                # Obtener costo promedio de todos los lotes
                lotes = LoteProducto.objects.filter(
                    producto_talla__producto=producto,
                    cantidad_disponible__gt=0,
                    activo=True
                )
                
                if not lotes.exists():
                    continue
                
                costo_promedio = lotes.aggregate(
                    promedio=Avg('costo_unitario')
                )['promedio'] or 0
                costo_promedio = Decimal(str(costo_promedio))
                
                # Calcular nuevo precio según tipo
                if tipo_modificacion == 'fixed':
                    nuevo_precio = valor
                elif tipo_modificacion == 'percentage':
                    nuevo_precio = precio_actual * (Decimal('1') + (valor / Decimal('100')))
                elif tipo_modificacion == 'amount':
                    nuevo_precio = precio_actual + valor
                elif tipo_modificacion == 'margin':
                    # Precio = Costo / (1 - Margen/100)
                    nuevo_precio = costo_promedio / (Decimal('1') - (valor / Decimal('100')))
                else:
                    continue
                
                # Validar precio mínimo (costo + 10%)
                precio_minimo = costo_promedio * Decimal('1.1')
                
                if nuevo_precio < precio_minimo:
                    nuevo_precio = precio_minimo
                
                # Convertir a entero (los precios son IntegerField)
                nuevo_precio_int = int(nuevo_precio)
                
                # Actualizar producto principal
                producto.precioventa = nuevo_precio_int
                producto.save()
                
                # Actualizar TODOS los lotes de TODAS las tallas
                lotes_actualizados = LoteProducto.objects.filter(
                    producto_talla__producto=producto,
                    cantidad_disponible__gt=0,
                    activo=True
                ).update(precio_venta_unitario=nuevo_precio_int)
                
                # Contar tallas
                tallas_count = producto.producto_talla.count()
                tallas_actualizadas_total += tallas_count
                
                productos_actualizados += 1
                
            except Producto.DoesNotExist:
                continue
        
        return JsonResponse({
            'success': True,
            'message': f'{productos_actualizados} productos actualizados ({tallas_actualizadas_total} tallas)',
            'productos_actualizados': productos_actualizados,
            'tallas_actualizadas': tallas_actualizadas_total
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en modificación masiva: {str(e)}'
        })


# ========== SINCRONIZACIÓN MULTI-SUCURSAL ==========

@require_POST
@login_required
@transaction.atomic
def sincronizar_sucursales(request):
    """Sincronizar precios de productos similares en múltiples sucursales (todas las tallas)"""
    try:
        data = json.loads(request.body)
        productos_ids = data.get('productos', [])
        sucursales_destino = data.get('sucursales_destino', [])
        ajuste_porcentual = Decimal(str(data.get('ajuste_porcentual', 0)))
        
        if not productos_ids or not sucursales_destino:
            return JsonResponse({
                'success': False,
                'error': 'Parámetros incompletos'
            })
        
        productos_sincronizados = 0
        sucursales_afectadas = set()
        
        for producto_id in productos_ids:
            try:
                producto_origen = Producto.objects.get(id=producto_id)
                
                # Obtener precio del producto origen
                precio_origen = Decimal(str(producto_origen.precioventa))
                
                # Buscar productos similares en otras sucursales
                for sucursal_id in sucursales_destino:
                    # Buscar productos con mismo nombre y atributos
                    productos_similares = Producto.objects.filter(
                        articulo=producto_origen.articulo,
                        atributo1=producto_origen.atributo1,
                        atributo2=producto_origen.atributo2,
                        sucursal_id=sucursal_id
                    )
                    
                    for prod_similar in productos_similares:
                        # Calcular precio ajustado
                        nuevo_precio = precio_origen * (Decimal('1') + (ajuste_porcentual / Decimal('100')))
                        nuevo_precio_int = int(nuevo_precio)
                        
                        # Actualizar producto
                        prod_similar.precioventa = nuevo_precio_int
                        prod_similar.save()
                        
                        # Actualizar lotes de TODAS las tallas
                        LoteProducto.objects.filter(
                            producto_talla__producto=prod_similar,
                            cantidad_disponible__gt=0,
                            activo=True
                        ).update(precio_venta_unitario=nuevo_precio_int)
                        
                        productos_sincronizados += 1
                        sucursales_afectadas.add(sucursal_id)
                        
            except Producto.DoesNotExist:
                continue
        
        return JsonResponse({
            'success': True,
            'message': f'{productos_sincronizados} productos sincronizados',
            'productos_sincronizados': productos_sincronizados,
            'sucursales_afectadas': len(sucursales_afectadas)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en sincronización: {str(e)}'
        })


# ========== ANÁLISIS DE INVENTARIO ANTIGUO ==========

@require_GET
@login_required
def analisis_inventario_antiguo(request):
    """Análisis detallado de inventario antiguo con recomendaciones"""
    try:
        sucursal_id = request.session.get('idSucursalActual')
        
        # Fechas de corte
        fecha_6_meses = timezone.now() - timedelta(days=180)
        fecha_12_meses = timezone.now() - timedelta(days=365)
        
        queryset = LoteProducto.objects.filter(
            cantidad_disponible__gt=0,
            activo=True
        ).select_related('producto_talla__producto')
        
        if sucursal_id:
            queryset = queryset.filter(producto_talla__producto__sucursal_id=sucursal_id)
        
        # Categorizar por antigüedad
        inventario_antiguo = []
        
        for lote in queryset:
            dias = (timezone.now() - lote.fecha_ingreso).days
            
            if dias >= 180:  # Más de 6 meses
                valor_inventario = lote.cantidad_disponible * lote.costo_unitario
                
                # Sugerencia de descuento basado en antigüedad
                if dias >= 730:  # 2 años
                    descuento_sugerido = 40
                elif dias >= 365:  # 1 año
                    descuento_sugerido = 25
                else:  # 6 meses
                    descuento_sugerido = 15
                
                precio_sugerido = lote.precio_venta_unitario * (1 - descuento_sugerido / 100)
                
                inventario_antiguo.append({
                    'sku': lote.producto_talla.sku,
                    'producto': lote.producto_talla.producto.articulo,
                    'lote': lote.numero_lote,
                    'cantidad': lote.cantidad_disponible,
                    'dias_inventario': dias,
                    'precio_actual': float(lote.precio_venta_unitario),
                    'costo': float(lote.costo_unitario),
                    'valor_inventario': float(valor_inventario),
                    'descuento_sugerido': descuento_sugerido,
                    'precio_sugerido': float(precio_sugerido),
                    'categoria_edad': 'Crítico' if dias >= 365 else 'Atención'
                })
        
        # Ordenar por antigüedad descendente
        inventario_antiguo.sort(key=lambda x: x['dias_inventario'], reverse=True)
        
        # Calcular totales
        total_valor = sum(item['valor_inventario'] for item in inventario_antiguo)
        total_items = len(inventario_antiguo)
        
        return JsonResponse({
            'success': True,
            'inventario_antiguo': inventario_antiguo[:100],  # Top 100
            'resumen': {
                'total_items': total_items,
                'valor_total': float(total_valor),
                'promedio_dias': sum(item['dias_inventario'] for item in inventario_antiguo) / total_items if total_items > 0 else 0
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al analizar inventario antiguo: {str(e)}'
        })


# ========== ENDPOINTS AUXILIARES ==========

@require_GET
@login_required
def listar_categorias(request):
    """Listar todas las categorías activas"""
    try:
        categorias = Categoria.objects.all().order_by('nombre')
        
        categorias_data = []
        for cat in categorias:
            categorias_data.append({
                'id': cat.id,
                'nombre': cat.nombre,
                'padre_id': cat.padre_id if cat.padre else None
            })
        
        return JsonResponse({
            'success': True,
            'categorias': categorias_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al listar categorías: {str(e)}'
        })


@require_GET
@login_required
def listar_atributos(request):
    """Listar opciones de atributos (marcas, colores, etc.)"""
    try:
        tipo = request.GET.get('tipo', 'marca')
        
        # Mapear tipo a nombre de atributo
        tipo_map = {
            'marca': 'Marca',
            'color': 'Color',
            'genero': 'Género',
            'otro': 'Otro'
        }
        
        atributo_nombre = tipo_map.get(tipo, 'Marca')
        
        # Buscar el atributo
        from .models import Productos_Atributos
        try:
            atributo = Productos_Atributos.objects.get(nombre__iexact=atributo_nombre)
            opciones = AtributoOpcion.objects.filter(atributo=atributo).order_by('valor')
            
            opciones_data = []
            for opcion in opciones:
                opciones_data.append({
                    'id': opcion.id,
                    'valor': opcion.valor
                })
            
            return JsonResponse({
                'success': True,
                'opciones': opciones_data
            })
            
        except Productos_Atributos.DoesNotExist:
            return JsonResponse({
                'success': True,
                'opciones': []
            })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al listar atributos: {str(e)}'
        })


@require_GET
@login_required
def listar_sucursales(request):
    """Listar todas las sucursales"""
    try:
        sucursales = Sucursal.objects.all().order_by('alias')
        
        sucursales_data = []
        for suc in sucursales:
            sucursales_data.append({
                'id': suc.id,
                'alias': suc.alias,
                'direccion': suc.direccion
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al listar sucursales: {str(e)}'
        })


# ========== SISTEMA DE APROBACIÓN DE CAMBIOS DE PRECIOS ==========

@require_POST
@login_required
@transaction.atomic
def proponer_cambio_precio(request):
    """
    Proponer un cambio de precio (crea registro pendiente en lugar de aplicar directamente)
    Trabaja a nivel de producto (todas las tallas)
    """
    try:
        data = json.loads(request.body)
        producto_id = data.get('producto_id')
        nuevo_precio = int(data.get('nuevo_precio'))
        motivo = data.get('motivo', '')
        tipo_cambio = data.get('tipo_cambio', 'INDIVIDUAL')
        prioridad = data.get('prioridad', 'MEDIA')
        dias_vencimiento = int(data.get('dias_vencimiento', 7))
        
        if not producto_id or not nuevo_precio:
            return JsonResponse({
                'success': False,
                'error': 'Parámetros incompletos'
            })
        
        producto = Producto.objects.select_related('sucursal').get(id=producto_id)
        
        # Obtener primera talla del producto (para el registro)
        primera_talla = producto.producto_talla.first()
        if not primera_talla:
            return JsonResponse({
                'success': False,
                'error': 'Producto sin tallas definidas'
            })
        
        # Obtener precio actual
        precio_actual = producto.precioventa
        
        # Calcular diferencia y porcentaje
        diferencia = nuevo_precio - precio_actual
        porcentaje_cambio = (diferencia / precio_actual * 100) if precio_actual > 0 else 0
        
        # Crear cambio pendiente (usamos primera talla como referencia, pero afecta a todas)
        cambio = CambioPrecioPendiente.objects.create(
            producto_talla=primera_talla,
            sucursal=producto.sucursal,
            precio_anterior=precio_actual,
            precio_nuevo=nuevo_precio,
            diferencia=diferencia,
            porcentaje_cambio=porcentaje_cambio,
            tipo_cambio=tipo_cambio,
            estado='PENDIENTE',
            motivo=motivo,
            creado_por=request.user,
            prioridad=prioridad,
            fecha_vencimiento=timezone.now() + timedelta(days=dias_vencimiento)
        )
        
        # Crear notificación para usuarios de la sucursal
        from .models import EmpresaUser
        usuarios_sucursal = EmpresaUser.objects.filter(
            sucursal=producto.sucursal,
            status=True
        ).select_related('user')
        
        tallas_count = producto.producto_talla.count()
        mensaje = f"Nuevo cambio de precio propuesto para {producto.articulo} ({tallas_count} tallas). " \
                  f"Precio actual: ${precio_actual:,} → Nuevo: ${nuevo_precio:,} ({porcentaje_cambio:+.1f}%)"
        
        for empresa_user in usuarios_sucursal:
            if empresa_user.user != request.user:  # No notificar al creador
                NotificacionCambioPrecio.objects.create(
                    cambio_precio=cambio,
                    usuario=empresa_user.user,
                    tipo='NUEVA',
                    mensaje=mensaje
                )
        
        cambio.notificado = True
        cambio.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Cambio de precio propuesto correctamente',
            'cambio_id': cambio.id,
            'notificaciones_enviadas': usuarios_sucursal.count() - 1
        })
        
    except Producto.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Producto no encontrado'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al proponer cambio: {str(e)}'
        })


@require_GET
@login_required
def obtener_indicadores_precios_pendientes(request):
    """
    Obtener indicadores de precios pendientes para el dashboard de ventas
    """
    try:
        sucursal_id = request.session.get('idSucursalActual')
        
        # Filtrar por sucursal si está definida
        queryset = CambioPrecioPendiente.objects.all()
        
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        # Contar por estado
        total_pendientes = queryset.filter(estado='PENDIENTE').count()
        total_revisados = queryset.filter(estado='REVISADO').count()
        total_aprobados = queryset.filter(estado='APROBADO').count()
        total_rechazados = queryset.filter(estado='RECHAZADO').count()
        
        # Cambios urgentes (prioridad alta o vencidos)
        cambios_urgentes = queryset.filter(
            Q(prioridad__in=['ALTA', 'URGENTE']) | 
            Q(fecha_vencimiento__lt=timezone.now(), estado='PENDIENTE')
        ).count()
        
        # Cambios sin revisar (más de 3 días)
        fecha_limite = timezone.now() - timedelta(days=3)
        sin_revisar_antiguos = queryset.filter(
            estado='PENDIENTE',
            fecha_creacion__lt=fecha_limite
        ).count()
        
        # Últimos cambios pendientes (top 5)
        ultimos_cambios = queryset.filter(
            estado='PENDIENTE'
        ).select_related(
            'producto_talla__producto',
            'creado_por',
            'sucursal'
        ).order_by('-fecha_creacion')[:5]
        
        cambios_data = []
        for cambio in ultimos_cambios:
            cambios_data.append({
                'id': cambio.id,
                'sku': cambio.producto_talla.sku,
                'producto': cambio.producto_talla.producto.articulo,
                'precio_anterior': float(cambio.precio_anterior),
                'precio_nuevo': float(cambio.precio_nuevo),
                'porcentaje_cambio': float(cambio.porcentaje_cambio),
                'dias_pendiente': cambio.dias_pendiente,
                'prioridad': cambio.prioridad,
                'creado_por': cambio.creado_por.username if cambio.creado_por else 'Sistema',
                'fecha_creacion': cambio.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'requiere_atencion': cambio.requiere_atencion
            })
        
        return JsonResponse({
            'success': True,
            'indicadores': {
                'total_pendientes': total_pendientes,
                'total_revisados': total_revisados,
                'total_aprobados': total_aprobados,
                'total_rechazados': total_rechazados,
                'cambios_urgentes': cambios_urgentes,
                'sin_revisar_antiguos': sin_revisar_antiguos,
                'requiere_atencion': cambios_urgentes + sin_revisar_antiguos
            },
            'ultimos_cambios': cambios_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener indicadores: {str(e)}'
        })


@require_GET
@login_required
def listar_cambios_pendientes(request):
    """
    Listar todos los cambios de precio pendientes con filtros
    """
    try:
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        estado = request.GET.get('estado')
        prioridad = request.GET.get('prioridad')
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        queryset = CambioPrecioPendiente.objects.select_related(
            'producto_talla__producto',
            'sucursal',
            'creado_por',
            'revisado_por',
            'aprobado_por'
        )
        
        # Filtros
        if sucursal_id:
            queryset = queryset.filter(sucursal_id=sucursal_id)
        
        if estado:
            queryset = queryset.filter(estado=estado)
        
        if prioridad:
            queryset = queryset.filter(prioridad=prioridad)
        
        queryset = queryset.order_by('-fecha_creacion')
        
        # Paginación
        paginator = Paginator(queryset, per_page)
        page_obj = paginator.get_page(page)
        
        cambios_data = []
        for cambio in page_obj:
            producto = cambio.producto_talla.producto
            tallas_count = producto.producto_talla.count()
            tallas_list = [t.talla for t in producto.producto_talla.all()[:5]]
            
            cambios_data.append({
                'id': cambio.id,
                'sku': cambio.producto_talla.sku,
                'producto': producto.articulo,
                'tallas_count': tallas_count,
                'tallas_preview': ', '.join(str(t) for t in tallas_list) + (f' (+{tallas_count-5} más)' if tallas_count > 5 else ''),
                'sucursal': cambio.sucursal.alias,
                'precio_anterior': float(cambio.precio_anterior),
                'precio_nuevo': float(cambio.precio_nuevo),
                'diferencia': float(cambio.diferencia),
                'porcentaje_cambio': float(cambio.porcentaje_cambio),
                'tipo_cambio': cambio.get_tipo_cambio_display(),
                'estado': cambio.estado,
                'estado_display': cambio.get_estado_display(),
                'prioridad': cambio.prioridad,
                'motivo': cambio.motivo or '',
                'creado_por': cambio.creado_por.username if cambio.creado_por else 'Sistema',
                'revisado_por': cambio.revisado_por.username if cambio.revisado_por else None,
                'aprobado_por': cambio.aprobado_por.username if cambio.aprobado_por else None,
                'fecha_creacion': cambio.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'fecha_revision': cambio.fecha_revision.strftime('%d/%m/%Y %H:%M') if cambio.fecha_revision else None,
                'fecha_aprobacion': cambio.fecha_aprobacion.strftime('%d/%m/%Y %H:%M') if cambio.fecha_aprobacion else None,
                'dias_pendiente': cambio.dias_pendiente,
                'esta_vencido': cambio.esta_vencido,
                'requiere_atencion': cambio.requiere_atencion
            })
        
        return JsonResponse({
            'success': True,
            'cambios': cambios_data,
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
            'error': f'Error al listar cambios: {str(e)}'
        })


@require_POST
@login_required
@transaction.atomic
def revisar_cambio_precio(request):
    """
    Marcar un cambio de precio como revisado
    """
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        observaciones = data.get('observaciones', '')
        
        cambio = CambioPrecioPendiente.objects.get(id=cambio_id)
        
        if cambio.estado != 'PENDIENTE':
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden revisar cambios pendientes'
            })
        
        cambio.estado = 'REVISADO'
        cambio.revisado_por = request.user
        cambio.fecha_revision = timezone.now()
        cambio.observaciones_revision = observaciones
        cambio.save()
        
        # Notificar al creador
        if cambio.creado_por and cambio.creado_por != request.user:
            NotificacionCambioPrecio.objects.create(
                cambio_precio=cambio,
                usuario=cambio.creado_por,
                tipo='REVISION',
                mensaje=f"Tu cambio de precio para {cambio.producto_talla.producto.articulo} ha sido revisado por {request.user.username}"
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Cambio marcado como revisado'
        })
        
    except CambioPrecioPendiente.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Cambio no encontrado'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al revisar cambio: {str(e)}'
        })


@require_POST
@login_required
@transaction.atomic
def aprobar_cambio_precio(request):
    """
    Aprobar un cambio de precio y aplicarlo a todas las tallas del producto
    """
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        observaciones = data.get('observaciones', '')
        
        cambio = CambioPrecioPendiente.objects.select_related('producto_talla__producto').get(id=cambio_id)
        
        if cambio.estado not in ['PENDIENTE', 'REVISADO']:
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden aprobar cambios pendientes o revisados'
            })
        
        # Aprobar
        cambio.estado = 'APROBADO'
        cambio.aprobado_por = request.user
        cambio.fecha_aprobacion = timezone.now()
        cambio.observaciones_aprobacion = observaciones
        
        # Aplicar el cambio al producto principal
        producto = cambio.producto_talla.producto
        producto.precioventa = cambio.precio_nuevo
        producto.save()
        
        # Actualizar lotes activos de TODAS las tallas del producto
        lotes_actualizados = LoteProducto.objects.filter(
            producto_talla__producto=producto,
            cantidad_disponible__gt=0,
            activo=True
        ).update(precio_venta_unitario=cambio.precio_nuevo)
        
        # Contar tallas afectadas
        tallas_afectadas = producto.producto_talla.count()
        
        cambio.estado = 'APLICADO'
        cambio.fecha_aplicacion = timezone.now()
        cambio.save()
        
        # Notificar al creador
        if cambio.creado_por and cambio.creado_por != request.user:
            NotificacionCambioPrecio.objects.create(
                cambio_precio=cambio,
                usuario=cambio.creado_por,
                tipo='APROBACION',
                mensaje=f"Tu cambio de precio para {producto.articulo} ha sido aprobado y aplicado a {tallas_afectadas} tallas"
            )
        
        return JsonResponse({
            'success': True,
            'message': f'Cambio aprobado y aplicado a {tallas_afectadas} tallas',
            'tallas_afectadas': tallas_afectadas,
            'lotes_actualizados': lotes_actualizados
        })
        
    except CambioPrecioPendiente.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Cambio no encontrado'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al aprobar cambio: {str(e)}'
        })


@require_POST
@login_required
@transaction.atomic
def rechazar_cambio_precio(request):
    """
    Rechazar un cambio de precio
    """
    try:
        data = json.loads(request.body)
        cambio_id = data.get('cambio_id')
        observaciones = data.get('observaciones', 'Cambio rechazado')
        
        cambio = CambioPrecioPendiente.objects.select_related('producto_talla__producto').get(id=cambio_id)
        
        if cambio.estado not in ['PENDIENTE', 'REVISADO']:
            return JsonResponse({
                'success': False,
                'error': 'Solo se pueden rechazar cambios pendientes o revisados'
            })
        
        cambio.estado = 'RECHAZADO'
        cambio.aprobado_por = request.user
        cambio.fecha_aprobacion = timezone.now()
        cambio.observaciones_aprobacion = observaciones
        cambio.save()
        
        # Notificar al creador
        if cambio.creado_por and cambio.creado_por != request.user:
            NotificacionCambioPrecio.objects.create(
                cambio_precio=cambio,
                usuario=cambio.creado_por,
                tipo='RECHAZO',
                mensaje=f"Tu cambio de precio para {cambio.producto_talla.producto.articulo} ha sido rechazado. Motivo: {observaciones}"
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Cambio rechazado'
        })
        
    except CambioPrecioPendiente.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Cambio no encontrado'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al rechazar cambio: {str(e)}'
        })


@require_GET
@login_required
def obtener_notificaciones_precio(request):
    """
    Obtener notificaciones de cambios de precio para el usuario actual
    """
    try:
        solo_no_leidas = request.GET.get('solo_no_leidas', 'false') == 'true'
        limit = int(request.GET.get('limit', 10))
        
        queryset = NotificacionCambioPrecio.objects.filter(
            usuario=request.user
        ).select_related('cambio_precio__producto_talla__producto')
        
        if solo_no_leidas:
            queryset = queryset.filter(leida=False)
        
        queryset = queryset.order_by('-fecha_creacion')[:limit]
        
        notificaciones_data = []
        for notif in queryset:
            notificaciones_data.append({
                'id': notif.id,
                'cambio_id': notif.cambio_precio.id,
                'tipo': notif.get_tipo_display(),
                'mensaje': notif.mensaje,
                'leida': notif.leida,
                'fecha_creacion': notif.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'producto': notif.cambio_precio.producto_talla.producto.articulo
            })
        
        total_no_leidas = NotificacionCambioPrecio.objects.filter(
            usuario=request.user,
            leida=False
        ).count()
        
        return JsonResponse({
            'success': True,
            'notificaciones': notificaciones_data,
            'total_no_leidas': total_no_leidas
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener notificaciones: {str(e)}'
        })


@require_POST
@login_required
def marcar_notificacion_leida(request):
    """
    Marcar una notificación como leída
    """
    try:
        data = json.loads(request.body)
        notificacion_id = data.get('notificacion_id')
        
        notificacion = NotificacionCambioPrecio.objects.get(
            id=notificacion_id,
            usuario=request.user
        )
        
        notificacion.marcar_leida()
        
        return JsonResponse({
            'success': True,
            'message': 'Notificación marcada como leída'
        })
        
    except NotificacionCambioPrecio.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Notificación no encontrada'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al marcar notificación: {str(e)}'
        })

