"""
Módulo de Requerimientos - RetailMind
Contiene todas las vistas relacionadas con requerimientos, solicitudes y gestión de necesidades
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
    Producto, Producto_Talla, Sucursal, EmpresaUser, Empresa, 
    Movimientos_Producto, LoteProducto
)


# ========== GESTIÓN DE REQUERIMIENTOS ==========

@login_required
def modulo_requerimientos(request):
    """Vista principal del módulo de requerimientos"""
    return render(request, 'vistas/modulo requerimientos/gestionPedidosNuevos.html')


@login_required
def crear_requerimiento(request):
    """Vista para crear nuevo requerimiento"""
    if request.method == 'GET':
        return render(request, 'vistas/modulo requerimientos/crear_requerimiento.html')
    
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Validar datos requeridos
            tipo_requerimiento = data.get('tipo_requerimiento')
            prioridad = data.get('prioridad', 'MEDIA')
            descripcion = data.get('descripcion')
            productos = data.get('productos', [])
            
            if not all([tipo_requerimiento, descripcion]):
                return JsonResponse({
                    'success': False,
                    'error': 'Tipo de requerimiento y descripción son requeridos'
                })
            
            # TODO: Implementar modelo de Requerimiento
            # Por ahora retornamos éxito simulado
            
            return JsonResponse({
                'success': True,
                'message': 'Requerimiento creado exitosamente (funcionalidad en desarrollo)',
                'requerimiento_id': 1
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al crear requerimiento: {str(e)}'
            })


@login_required
def listar_requerimientos(request):
    """Listar requerimientos con filtros"""
    try:
        # Parámetros de filtro
        estado = request.GET.get('estado')
        prioridad = request.GET.get('prioridad')
        tipo = request.GET.get('tipo')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        
        # TODO: Implementar consulta real cuando exista el modelo
        # Por ahora retornamos datos simulados
        
        requerimientos_data = [
            {
                'id': 1,
                'tipo': 'STOCK',
                'descripcion': 'Requerimiento de stock para productos de temporada',
                'estado': 'PENDIENTE',
                'prioridad': 'ALTA',
                'fecha_creacion': '15/12/2024',
                'solicitante': 'Juan Pérez',
                'sucursal': 'Sucursal Centro'
            },
            {
                'id': 2,
                'tipo': 'PRODUCTO',
                'descripcion': 'Solicitud de nuevos productos para la línea deportiva',
                'estado': 'EN_PROCESO',
                'prioridad': 'MEDIA',
                'fecha_creacion': '14/12/2024',
                'solicitante': 'María González',
                'sucursal': 'Sucursal Norte'
            }
        ]
        
        return JsonResponse({
            'success': True,
            'requerimientos': requerimientos_data,
            'pagination': {
                'current_page': 1,
                'total_pages': 1,
                'total_items': len(requerimientos_data),
                'has_next': False,
                'has_previous': False,
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener requerimientos: {str(e)}'
        })


@login_required
def detalle_requerimiento(request, requerimiento_id):
    """Obtener detalles de un requerimiento específico"""
    try:
        # TODO: Implementar consulta real cuando exista el modelo
        # Por ahora retornamos datos simulados
        
        requerimiento_data = {
            'id': requerimiento_id,
            'tipo': 'STOCK',
            'descripcion': 'Requerimiento de stock para productos de temporada',
            'estado': 'PENDIENTE',
            'prioridad': 'ALTA',
            'fecha_creacion': '15/12/2024 10:30',
            'fecha_limite': '20/12/2024',
            'solicitante': {
                'id': 1,
                'nombre': 'Juan Pérez',
                'email': 'juan.perez@empresa.com'
            },
            'sucursal': {
                'id': 1,
                'nombre': 'Sucursal Centro'
            },
            'productos_solicitados': [
                {
                    'sku': 'PROD001-M',
                    'nombre': 'Camiseta Deportiva',
                    'talla': 'M',
                    'cantidad_solicitada': 50,
                    'stock_actual': 10,
                    'observaciones': 'Para temporada de verano'
                },
                {
                    'sku': 'PROD002-L',
                    'nombre': 'Pantalón Deportivo',
                    'talla': 'L',
                    'cantidad_solicitada': 30,
                    'stock_actual': 5,
                    'observaciones': 'Color azul preferentemente'
                }
            ],
            'observaciones': 'Requerimiento urgente para temporada alta',
            'historial': [
                {
                    'fecha': '15/12/2024 10:30',
                    'accion': 'CREADO',
                    'usuario': 'Juan Pérez',
                    'comentario': 'Requerimiento creado'
                }
            ]
        }
        
        return JsonResponse({
            'success': True,
            'requerimiento': requerimiento_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener requerimiento: {str(e)}'
        })


@login_required
@require_POST
def actualizar_estado_requerimiento(request, requerimiento_id):
    """Actualizar estado de un requerimiento"""
    try:
        data = json.loads(request.body)
        
        nuevo_estado = data.get('estado')
        comentario = data.get('comentario', '')
        
        if not nuevo_estado:
            return JsonResponse({
                'success': False,
                'error': 'Nuevo estado es requerido'
            })
        
        # TODO: Implementar actualización real cuando exista el modelo
        
        return JsonResponse({
            'success': True,
            'message': 'Estado actualizado exitosamente (funcionalidad en desarrollo)'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al actualizar estado: {str(e)}'
        })


@login_required
def aprobar_requerimiento(request, requerimiento_id):
    """Aprobar un requerimiento"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            comentario_aprobacion = data.get('comentario', '')
            productos_aprobados = data.get('productos_aprobados', [])
            
            # TODO: Implementar lógica de aprobación real
            
            return JsonResponse({
                'success': True,
                'message': 'Requerimiento aprobado exitosamente (funcionalidad en desarrollo)'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al aprobar requerimiento: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


@login_required
def rechazar_requerimiento(request, requerimiento_id):
    """Rechazar un requerimiento"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            motivo_rechazo = data.get('motivo', '')
            
            if not motivo_rechazo:
                return JsonResponse({
                    'success': False,
                    'error': 'Motivo de rechazo es requerido'
                })
            
            # TODO: Implementar lógica de rechazo real
            
            return JsonResponse({
                'success': True,
                'message': 'Requerimiento rechazado exitosamente (funcionalidad en desarrollo)'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al rechazar requerimiento: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


# ========== ANÁLISIS DE REQUERIMIENTOS ==========

@login_required
def analisis_stock_critico(request):
    """Análisis de productos con stock crítico para generar requerimientos automáticos"""
    try:
        sucursal_id = request.session.get('idSucursalActual')
        umbral_critico = int(request.GET.get('umbral', 5))
        
        productos_criticos = []
        
        # Obtener productos con stock bajo
        for pt in Producto_Talla.objects.filter(activo=True):
            if sucursal_id:
                stock_actual = pt.stock_sucursal(sucursal_id)
            else:
                stock_actual = pt.stock_total()
            
            if stock_actual <= umbral_critico:
                # Calcular promedio de ventas (últimos 30 días)
                fecha_inicio = timezone.now() - timezone.timedelta(days=30)
                
                from .models import Ticket_Productos
                ventas_periodo = Ticket_Productos.objects.filter(
                    productoTalla=pt,
                    ticket__created_at__gte=fecha_inicio,
                    ticket__estado='PAGADO'
                ).aggregate(
                    total_vendido=Sum('cantidad')
                )['total_vendido'] or 0
                
                promedio_diario = ventas_periodo / 30 if ventas_periodo > 0 else 0
                dias_stock = stock_actual / promedio_diario if promedio_diario > 0 else float('inf')
                
                productos_criticos.append({
                    'sku': pt.sku,
                    'nombre': pt.producto.nombre,
                    'categoria': pt.producto.categoria.nombre if pt.producto.categoria else '',
                    'talla': pt.talla.nombre if pt.talla else 'Sin talla',
                    'stock_actual': stock_actual,
                    'ventas_30_dias': ventas_periodo,
                    'promedio_diario': round(promedio_diario, 2),
                    'dias_stock': round(dias_stock, 1) if dias_stock != float('inf') else 'Sin ventas',
                    'sugerencia_pedido': max(int(promedio_diario * 30), 10) if promedio_diario > 0 else 10
                })
        
        # Ordenar por días de stock (menor primero)
        productos_criticos.sort(key=lambda x: x['dias_stock'] if isinstance(x['dias_stock'], (int, float)) else 999)
        
        return JsonResponse({
            'success': True,
            'productos_criticos': productos_criticos[:50],  # Limitar a 50 productos
            'resumen': {
                'total_productos_criticos': len(productos_criticos),
                'umbral_usado': umbral_critico
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en análisis de stock crítico: {str(e)}'
        })


@login_required
def generar_requerimiento_automatico(request):
    """Generar requerimiento automático basado en análisis de stock"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            productos_seleccionados = data.get('productos', [])
            tipo_requerimiento = data.get('tipo', 'STOCK_CRITICO')
            prioridad = data.get('prioridad', 'ALTA')
            
            if not productos_seleccionados:
                return JsonResponse({
                    'success': False,
                    'error': 'Debe seleccionar al menos un producto'
                })
            
            # TODO: Implementar creación automática de requerimiento
            
            return JsonResponse({
                'success': True,
                'message': f'Requerimiento automático generado para {len(productos_seleccionados)} productos (funcionalidad en desarrollo)',
                'requerimiento_id': 999
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al generar requerimiento automático: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


@login_required
def analisis_tendencias_demanda(request):
    """Análisis de tendencias de demanda para requerimientos predictivos"""
    try:
        # Parámetros de análisis
        dias_analisis = int(request.GET.get('dias', 90))
        categoria_id = request.GET.get('categoria_id')
        
        fecha_inicio = timezone.now() - timezone.timedelta(days=dias_analisis)
        
        # Análisis por categoría
        tendencias = []
        
        categorias_query = Categoria.objects.filter(activo=True)
        if categoria_id:
            categorias_query = categorias_query.filter(id=categoria_id)
        
        for categoria in categorias_query:
            from .models import Ticket_Productos
            
            # Ventas por semana
            ventas_semanales = []
            for semana in range(int(dias_analisis / 7)):
                fecha_semana_inicio = fecha_inicio + timezone.timedelta(weeks=semana)
                fecha_semana_fin = fecha_semana_inicio + timezone.timedelta(days=6)
                
                ventas_semana = Ticket_Productos.objects.filter(
                    productoTalla__producto__categoria=categoria,
                    ticket__created_at__range=[fecha_semana_inicio, fecha_semana_fin],
                    ticket__estado='PAGADO'
                ).aggregate(
                    total_vendido=Sum('cantidad'),
                    total_ingresos=Sum(F('cantidad') * F('precio_unitario'))
                )
                
                ventas_semanales.append({
                    'semana': semana + 1,
                    'fecha_inicio': fecha_semana_inicio.strftime('%d/%m/%Y'),
                    'cantidad_vendida': ventas_semana['total_vendido'] or 0,
                    'ingresos': float(ventas_semana['total_ingresos'] or 0)
                })
            
            # Calcular tendencia (simple: comparar primera mitad vs segunda mitad)
            primera_mitad = sum(v['cantidad_vendida'] for v in ventas_semanales[:len(ventas_semanales)//2])
            segunda_mitad = sum(v['cantidad_vendida'] for v in ventas_semanales[len(ventas_semanales)//2:])
            
            if primera_mitad > 0:
                tendencia_porcentaje = ((segunda_mitad - primera_mitad) / primera_mitad) * 100
            else:
                tendencia_porcentaje = 0
            
            tendencias.append({
                'categoria': categoria.nombre,
                'ventas_semanales': ventas_semanales,
                'tendencia_porcentaje': round(tendencia_porcentaje, 2),
                'tendencia_direccion': 'CRECIENTE' if tendencia_porcentaje > 5 else 'DECRECIENTE' if tendencia_porcentaje < -5 else 'ESTABLE',
                'total_vendido_periodo': primera_mitad + segunda_mitad
            })
        
        return JsonResponse({
            'success': True,
            'tendencias': tendencias,
            'parametros': {
                'dias_analisis': dias_analisis,
                'fecha_inicio': fecha_inicio.strftime('%d/%m/%Y'),
                'fecha_fin': timezone.now().strftime('%d/%m/%Y')
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error en análisis de tendencias: {str(e)}'
        })


# ========== REQUERIMIENTOS POR SUCURSAL ==========

@login_required
def requerimientos_por_sucursal(request):
    """Obtener requerimientos específicos por sucursal"""
    try:
        sucursal_id = request.GET.get('sucursal_id') or request.session.get('idSucursalActual')
        
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'Sucursal requerida'
            })
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # TODO: Implementar consulta real cuando exista el modelo
        # Por ahora retornamos datos simulados específicos de la sucursal
        
        requerimientos_sucursal = [
            {
                'id': 1,
                'tipo': 'STOCK',
                'descripcion': f'Requerimiento de stock para {sucursal.nombre}',
                'estado': 'PENDIENTE',
                'prioridad': 'ALTA',
                'fecha_creacion': '15/12/2024',
                'productos_count': 5
            },
            {
                'id': 2,
                'tipo': 'TRASPASO',
                'descripcion': f'Solicitud de traspaso hacia {sucursal.nombre}',
                'estado': 'APROBADO',
                'prioridad': 'MEDIA',
                'fecha_creacion': '14/12/2024',
                'productos_count': 3
            }
        ]
        
        return JsonResponse({
            'success': True,
            'sucursal': {
                'id': sucursal.id,
                'nombre': sucursal.nombre
            },
            'requerimientos': requerimientos_sucursal
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener requerimientos por sucursal: {str(e)}'
        })


@login_required
def crear_requerimiento_traspaso(request):
    """Crear requerimiento específico para traspaso entre sucursales"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            sucursal_origen_id = data.get('sucursal_origen_id')
            sucursal_destino_id = data.get('sucursal_destino_id')
            productos = data.get('productos', [])
            justificacion = data.get('justificacion', '')
            
            if not all([sucursal_origen_id, sucursal_destino_id, productos]):
                return JsonResponse({
                    'success': False,
                    'error': 'Sucursales y productos son requeridos'
                })
            
            if sucursal_origen_id == sucursal_destino_id:
                return JsonResponse({
                    'success': False,
                    'error': 'Las sucursales origen y destino deben ser diferentes'
                })
            
            # Validar que las sucursales existan
            sucursal_origen = get_object_or_404(Sucursal, id=sucursal_origen_id)
            sucursal_destino = get_object_or_404(Sucursal, id=sucursal_destino_id)
            
            # TODO: Implementar creación real de requerimiento de traspaso
            
            return JsonResponse({
                'success': True,
                'message': f'Requerimiento de traspaso creado: {sucursal_origen.nombre} → {sucursal_destino.nombre} (funcionalidad en desarrollo)',
                'requerimiento_id': 888
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Datos JSON inválidos'
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al crear requerimiento de traspaso: {str(e)}'
            })
    
    return JsonResponse({'success': False, 'error': 'Método no permitido'})


# ========== DASHBOARD DE REQUERIMIENTOS ==========

@login_required
def dashboard_requerimientos(request):
    """Vista del dashboard de requerimientos"""
    return render(request, 'vistas/modulo_dashboards/dashboard_requerimientos.html')


@login_required
def obtener_metricas_requerimientos(request):
    """Obtener métricas del dashboard de requerimientos"""
    try:
        # TODO: Implementar métricas reales cuando exista el modelo
        # Por ahora retornamos datos simulados
        
        metricas = {
            'total_requerimientos': 25,
            'pendientes': 8,
            'en_proceso': 12,
            'completados': 5,
            'rechazados': 0,
            'requerimientos_urgentes': 3,
            'tiempo_promedio_resolucion': 4.5,  # días
            'tasa_aprobacion': 95.2  # porcentaje
        }
        
        # Requerimientos por tipo
        por_tipo = [
            {'tipo': 'STOCK', 'cantidad': 15},
            {'tipo': 'PRODUCTO', 'cantidad': 6},
            {'tipo': 'TRASPASO', 'cantidad': 4}
        ]
        
        # Requerimientos por sucursal
        por_sucursal = [
            {'sucursal': 'Sucursal Centro', 'cantidad': 10},
            {'sucursal': 'Sucursal Norte', 'cantidad': 8},
            {'sucursal': 'Sucursal Sur', 'cantidad': 7}
        ]
        
        # Evolución mensual (últimos 6 meses)
        evolucion_mensual = [
            {'mes': 'Jul 2024', 'creados': 18, 'completados': 16},
            {'mes': 'Ago 2024', 'creados': 22, 'completados': 20},
            {'mes': 'Sep 2024', 'creados': 19, 'completados': 18},
            {'mes': 'Oct 2024', 'creados': 25, 'completados': 23},
            {'mes': 'Nov 2024', 'creados': 28, 'completados': 25},
            {'mes': 'Dic 2024', 'creados': 15, 'completados': 8}  # Mes actual
        ]
        
        return JsonResponse({
            'success': True,
            'metricas': metricas,
            'por_tipo': por_tipo,
            'por_sucursal': por_sucursal,
            'evolucion_mensual': evolucion_mensual
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener métricas: {str(e)}'
        })


@login_required
def exportar_requerimientos(request):
    """Exportar requerimientos a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Requerimientos"
        
        # Encabezados
        headers = [
            'ID', 'Tipo', 'Descripción', 'Estado', 'Prioridad',
            'Fecha Creación', 'Solicitante', 'Sucursal'
        ]
        
        # Estilo para encabezados
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        
        # Escribir encabezados
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
        
        # TODO: Obtener datos reales cuando exista el modelo
        # Por ahora usamos datos simulados
        requerimientos_data = [
            [1, 'STOCK', 'Requerimiento de stock para productos de temporada', 'PENDIENTE', 'ALTA', '15/12/2024', 'Juan Pérez', 'Sucursal Centro'],
            [2, 'PRODUCTO', 'Solicitud de nuevos productos para la línea deportiva', 'EN_PROCESO', 'MEDIA', '14/12/2024', 'María González', 'Sucursal Norte']
        ]
        
        # Escribir datos
        for row, data in enumerate(requerimientos_data, 2):
            for col, value in enumerate(data, 1):
                ws.cell(row=row, column=col, value=value)
        
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
        response['Content-Disposition'] = 'attachment; filename="requerimientos.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        })


# ========== CONFIGURACIÓN DE REQUERIMIENTOS ==========

@login_required
def configuracion_requerimientos(request):
    """Vista de configuración del módulo de requerimientos"""
    return render(request, 'vistas/modulo requerimientos/configuracion_requerimientos.html')


@login_required
def obtener_configuracion_requerimientos(request):
    """Obtener configuración actual del módulo de requerimientos"""
    try:
        # TODO: Implementar configuración real
        # Por ahora retornamos configuración simulada
        
        configuracion = {
            'umbral_stock_critico': 5,
            'dias_analisis_tendencias': 90,
            'auto_generar_requerimientos': True,
            'notificar_requerimientos_urgentes': True,
            'tiempo_limite_aprobacion': 48,  # horas
            'requiere_aprobacion_gerencia': True,
            'tipos_requerimiento_activos': ['STOCK', 'PRODUCTO', 'TRASPASO'],
            'niveles_prioridad': ['BAJA', 'MEDIA', 'ALTA', 'URGENTE']
        }
        
        return JsonResponse({
            'success': True,
            'configuracion': configuracion
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener configuración: {str(e)}'
        })


@login_required
@require_POST
def guardar_configuracion_requerimientos(request):
    """Guardar configuración del módulo de requerimientos"""
    try:
        data = json.loads(request.body)
        
        # TODO: Implementar guardado real de configuración
        
        return JsonResponse({
            'success': True,
            'message': 'Configuración guardada exitosamente (funcionalidad en desarrollo)'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al guardar configuración: {str(e)}'
        })


# ========== NOTIFICACIONES DE REQUERIMIENTOS ==========

@login_required
def obtener_notificaciones_requerimientos(request):
    """Obtener notificaciones pendientes de requerimientos"""
    try:
        # TODO: Implementar notificaciones reales
        # Por ahora retornamos notificaciones simuladas
        
        notificaciones = [
            {
                'id': 1,
                'tipo': 'REQUERIMIENTO_PENDIENTE',
                'titulo': 'Requerimiento pendiente de aprobación',
                'mensaje': 'El requerimiento #001 está pendiente de aprobación desde hace 2 días',
                'fecha': '15/12/2024 10:30',
                'prioridad': 'ALTA',
                'leida': False,
                'url': '/app/requerimientos/1/'
            },
            {
                'id': 2,
                'tipo': 'STOCK_CRITICO',
                'titulo': 'Stock crítico detectado',
                'mensaje': '5 productos tienen stock crítico y requieren atención',
                'fecha': '15/12/2024 09:15',
                'prioridad': 'MEDIA',
                'leida': False,
                'url': '/app/requerimientos/analisis-stock-critico/'
            }
        ]
        
        return JsonResponse({
            'success': True,
            'notificaciones': notificaciones,
            'total_no_leidas': sum(1 for n in notificaciones if not n['leida'])
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener notificaciones: {str(e)}'
        })


@login_required
@require_POST
def marcar_notificacion_leida(request, notificacion_id):
    """Marcar una notificación como leída"""
    try:
        # TODO: Implementar marcado real de notificación
        
        return JsonResponse({
            'success': True,
            'message': 'Notificación marcada como leída (funcionalidad en desarrollo)'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al marcar notificación: {str(e)}'
        })
