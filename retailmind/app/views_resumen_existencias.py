"""
Módulo de Resumen de Existencias - RetailMind
Vista para mostrar resumen de existencias agrupado por sucursal
Con soporte para inventario histórico y agrupación por categoría
"""

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from decimal import Decimal
from datetime import datetime, date
from django.db.models import Sum, Q
from django.utils import timezone
import json

from .models import Sucursal, Producto, Producto_Talla, Movimientos_Producto, Categoria, EmpresaUser


@login_required
def ver_resumen_existencias(request):
    """Vista principal del reporte de resumen de existencias"""
    return render(request, 'vistas/modulo_reportes/resumen_existencias.html')


def calcular_stock_historico(talla, sucursal_id, fecha_corte):
    """
    Calcula el stock de una talla en una sucursal a una fecha específica.

    Fórmula: stock_en_fecha = stock_actual - ingresos_después + abs(egresos_después)

    Los movimientos posteriores a fecha_corte se revierten:
    - Ingresos ocurridos después → se restan (esos pares aún no habían llegado)
    - Egresos ocurridos después → se suman (esos pares todavía estaban en esa fecha)
      Las cantidades de egresos se almacenan como negativas, de ahí el abs().

    Args:
        talla: Instancia de Producto_Talla
        sucursal_id: ID de la sucursal
        fecha_corte: Fecha hasta la cual calcular (date object)

    Returns:
        int: Stock calculado a esa fecha
    """
    # Stock actual
    stock_actual = talla.stock
    
    # Si es la fecha de hoy, retornar stock actual
    if fecha_corte >= timezone.localdate():
        return max(0, stock_actual)
    
    # Calcular movimientos DESPUÉS de la fecha de corte
    # Ingresos después de la fecha (los restamos del stock actual)
    ingresos_posteriores = Movimientos_Producto.objects.filter(
        ProductoTalla=talla,
        fecha__gt=fecha_corte,
        estado='COMPLETADO'
    ).filter(
        Q(sucursal_destino_id=sucursal_id) & 
        (Q(tipo_movimiento='INGRESO') | Q(concepto='TRASPASO_ENTRADA'))
    ).aggregate(total=Sum('cantidad'))['total'] or 0
    
    # Egresos después de la fecha (los sumamos al stock actual, porque egresos son negativos)
    egresos_posteriores = Movimientos_Producto.objects.filter(
        ProductoTalla=talla,
        fecha__gt=fecha_corte,
        estado='COMPLETADO'
    ).filter(
        Q(sucursal_origen_id=sucursal_id) &
        (Q(tipo_movimiento='EGRESO') | Q(concepto='TRASPASO_SALIDA'))
    ).aggregate(total=Sum('cantidad'))['total'] or 0
    
    # Stock histórico = Stock actual - ingresos posteriores + egresos posteriores
    # Los ingresos después de la fecha aumentaron el stock actual → los restamos
    # Los egresos después de la fecha disminuyeron el stock actual → los sumamos de vuelta
    # cantidad de egresos se almacena como negativa, por eso usamos abs()
    stock_historico = stock_actual - ingresos_posteriores + abs(egresos_posteriores)
    
    return max(0, stock_historico)


@require_GET
@login_required
def obtener_resumen_existencias(request):
    """API para obtener resumen de existencias agrupado por sucursal"""
    try:
        # Parámetros de filtro
        marca_id = request.GET.get('marca_id')
        categoria_id = request.GET.get('categoria_id')
        fecha_corte_str = request.GET.get('fecha_corte')  # Formato: YYYY-MM-DD
        agrupar_por = request.GET.get('agrupar_por', 'sucursal')  # sucursal | categoria
        
        # Parsear fecha de corte
        fecha_corte = None
        es_historico = False
        if fecha_corte_str:
            try:
                fecha_corte = datetime.strptime(fecha_corte_str, '%Y-%m-%d').date()
                es_historico = fecha_corte < timezone.localdate()
            except ValueError:
                pass
        
        # Si es agrupación por categoría
        if agrupar_por == 'categoria':
            return _resumen_por_categoria(request, marca_id, fecha_corte, es_historico, empresas_usuario)
        
        # Filtrar por sucursales del usuario (seguridad multi-tenant)
        empresas_usuario = EmpresaUser.objects.filter(
            user=request.user,
            status=True
        ).values_list('empresa_id', flat=True)

        # Agrupación por sucursal (default)
        sucursales = Sucursal.objects.filter(empresa_id__in=empresas_usuario).order_by('alias')
        resumen_sucursales = []
        
        for sucursal in sucursales:
            # Filtrar productos de esta sucursal (excluye productos marcados como "excluir de analítica")
            queryset_productos = Producto.objects.filter(
                sucursal_id=sucursal.id,
                excluir_de_analitica=False,
            ).select_related('atributo1', 'categoria').prefetch_related('producto_talla')
            
            # Aplicar filtros opcionales
            if marca_id:
                queryset_productos = queryset_productos.filter(atributo1_id=marca_id)
            
            if categoria_id:
                queryset_productos = queryset_productos.filter(categoria_id=categoria_id)
            
            # Inicializar totales
            total_pares = 0
            total_costo = Decimal('0.00')
            total_precio_interno = Decimal('0.00')
            total_precio_venta = Decimal('0.00')
            
            # Calcular totales
            for producto in queryset_productos:
                for talla in producto.producto_talla.all():
                    # Usar stock histórico si hay fecha de corte
                    if es_historico and fecha_corte:
                        stock = calcular_stock_historico(talla, sucursal.id, fecha_corte)
                    else:
                        stock = talla.stock
                    
                    if stock > 0:
                        total_pares += stock
                        
                        if producto.costo:
                            total_costo += (Decimal(str(producto.costo)) * stock)
                        
                        # Precio interno = costo + sobreprecio (CORREGIDO)
                        precio_interno_unitario = Decimal(str(producto.costo or 0)) + Decimal(str(producto.sobreprecio or 0))
                        total_precio_interno += (precio_interno_unitario * stock)
                        
                        if producto.precioventa:
                            total_precio_venta += (Decimal(str(producto.precioventa)) * stock)
            
            # Solo agregar sucursales con stock
            if total_pares > 0:
                resumen_sucursales.append({
                    'sucursal_id': sucursal.id,
                    'sucursal': sucursal.alias,
                    'direccion': sucursal.direccion or '-',
                    'total_pares': total_pares,
                    'total_costo': float(total_costo),
                    'total_precio_interno': float(total_precio_interno),
                    'total_precio_venta': float(total_precio_venta),
                })
        
        # Calcular totales generales
        total_general = {
            'pares': sum(s['total_pares'] for s in resumen_sucursales),
            'costo': sum(s['total_costo'] for s in resumen_sucursales),
            'precio_interno': sum(s['total_precio_interno'] for s in resumen_sucursales),
            'precio_venta': sum(s['total_precio_venta'] for s in resumen_sucursales),
        }
        
        fecha_info = f"al {fecha_corte.strftime('%d/%m/%Y')}" if es_historico else "actual"
        print(f"📊 Resumen generado para {len(resumen_sucursales)} sucursales ({fecha_info})")
        print(f"📈 Total general de pares: {total_general['pares']}")
        
        return JsonResponse({
            'success': True,
            'datos': resumen_sucursales,
            'total_general': total_general,
            'es_historico': es_historico,
            'fecha_corte': fecha_corte_str if es_historico else None,
            'agrupar_por': 'sucursal'
        })
        
    except Exception as e:
        import traceback
        print(f"❌ Error en resumen de existencias: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener resumen: {str(e)}'
        })


def _resumen_por_categoria(request, marca_id, fecha_corte, es_historico, empresas_usuario):
    """Genera resumen agrupado por categoría/departamento"""
    try:
        # Sucursales del usuario
        sucursales_ids = list(Sucursal.objects.filter(
            empresa_id__in=empresas_usuario
        ).values_list('id', flat=True))

        # Obtener todas las categorías raíz (departamentos)
        categorias = Categoria.objects.filter(padre__isnull=True).order_by('nombre')

        resumen_categorias = []

        for categoria in categorias:
            # IDs de esta categoría y sus subcategorías
            categoria_ids = [categoria.id]
            for sub in categoria.subcategorias.all():
                categoria_ids.append(sub.id)
                # Subcategorías de segundo nivel
                for subsub in sub.subcategorias.all():
                    categoria_ids.append(subsub.id)

            # Filtrar productos de estas categorías (solo sucursales del usuario)
            queryset_productos = Producto.objects.filter(
                categoria_id__in=categoria_ids,
                excluir_de_analitica=False,
                sucursal_id__in=sucursales_ids,
            ).select_related('atributo1', 'categoria', 'sucursal').prefetch_related('producto_talla')
            
            # Aplicar filtro de marca si existe
            if marca_id:
                queryset_productos = queryset_productos.filter(atributo1_id=marca_id)
            
            # Inicializar totales
            total_pares = 0
            total_costo = Decimal('0.00')
            total_precio_interno = Decimal('0.00')
            total_precio_venta = Decimal('0.00')
            sucursales_set = set()
            
            # Calcular totales
            for producto in queryset_productos:
                for talla in producto.producto_talla.all():
                    # Usar stock histórico si hay fecha de corte
                    if es_historico and fecha_corte:
                        stock = calcular_stock_historico(talla, producto.sucursal_id, fecha_corte)
                    else:
                        stock = talla.stock
                    
                    if stock > 0:
                        total_pares += stock
                        sucursales_set.add(producto.sucursal.alias if producto.sucursal else 'Sin Sucursal')
                        
                        if producto.costo:
                            total_costo += (Decimal(str(producto.costo)) * stock)
                        
                        # Precio interno = costo + sobreprecio (CORREGIDO)
                        precio_interno_unitario = Decimal(str(producto.costo or 0)) + Decimal(str(producto.sobreprecio or 0))
                        total_precio_interno += (precio_interno_unitario * stock)
                        
                        if producto.precioventa:
                            total_precio_venta += (Decimal(str(producto.precioventa)) * stock)
            
            # Solo agregar categorías con stock
            if total_pares > 0:
                resumen_categorias.append({
                    'categoria_id': categoria.id,
                    'categoria': categoria.nombre,
                    'num_sucursales': len(sucursales_set),
                    'total_pares': total_pares,
                    'total_costo': float(total_costo),
                    'total_precio_interno': float(total_precio_interno),
                    'total_precio_venta': float(total_precio_venta),
                })
        
        # Calcular totales generales
        total_general = {
            'pares': sum(c['total_pares'] for c in resumen_categorias),
            'costo': sum(c['total_costo'] for c in resumen_categorias),
            'precio_interno': sum(c['total_precio_interno'] for c in resumen_categorias),
            'precio_venta': sum(c['total_precio_venta'] for c in resumen_categorias),
        }
        
        return JsonResponse({
            'success': True,
            'datos': resumen_categorias,
            'total_general': total_general,
            'es_historico': es_historico,
            'fecha_corte': fecha_corte.strftime('%Y-%m-%d') if es_historico and fecha_corte else None,
            'agrupar_por': 'categoria'
        })
        
    except Exception as e:
        import traceback
        print(f"❌ Error en resumen por categoría: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener resumen por categoría: {str(e)}'
        })


@require_GET
@login_required
def exportar_resumen_existencias_excel(request):
    """Exportar resumen de existencias a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
        
        # Obtener datos del reporte
        marca_id = request.GET.get('marca_id')
        categoria_id = request.GET.get('categoria_id')
        fecha_corte = request.GET.get('fecha_corte')
        agrupar_por = request.GET.get('agrupar_por', 'sucursal')
        
        # Reutilizar la función de obtención de datos
        response_data = obtener_resumen_existencias(request)
        datos = json.loads(response_data.content)
        
        if not datos.get('success'):
            return JsonResponse({
                'success': False,
                'error': 'No se pudieron obtener los datos'
            })
        
        datos_resumen = datos.get('datos', [])
        total_general = datos.get('total_general', {})
        es_historico = datos.get('es_historico', False)
        
        if not datos_resumen:
            return JsonResponse({
                'success': False,
                'error': 'No hay datos para exportar'
            })
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Resumen Existencias"
        
        # Estilos NEXO
        header_fill = PatternFill(start_color="0066FF", end_color="0066FF", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=12)
        total_fill = PatternFill(start_color="00D4AA", end_color="00D4AA", fill_type="solid")
        total_font = Font(bold=True, size=12, color="FFFFFF")
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título
        titulo = "RESUMEN DE EXISTENCIAS"
        if agrupar_por == 'categoria':
            titulo += " POR CATEGORÍA"
        else:
            titulo += " POR SUCURSAL"
        
        if es_historico and fecha_corte:
            titulo += f" - Al {fecha_corte}"
        
        ws.merge_cells('A1:F1')
        cell = ws['A1']
        cell.value = titulo
        cell.fill = PatternFill(start_color="1A1A2E", end_color="1A1A2E", fill_type="solid")
        cell.font = Font(bold=True, color="FFFFFF", size=14)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 30
        
        # Encabezados dinámicos según agrupación
        if agrupar_por == 'categoria':
            headers = ['Categoría', 'Sucursales', 'Total Pares', 'Total Costo', 'Total Precio Interno', 'Total Precio Venta']
        else:
            headers = ['Sucursal', 'Dirección', 'Total Pares', 'Total Costo', 'Total Precio Interno', 'Total Precio Venta']
        
        for idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=2, column=idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = border
        
        ws.row_dimensions[2].height = 25
        
        # Datos
        fila = 3
        for item in datos_resumen:
            if agrupar_por == 'categoria':
                ws.cell(row=fila, column=1, value=item['categoria']).border = border
                ws.cell(row=fila, column=2, value=item['num_sucursales']).border = border
            else:
                ws.cell(row=fila, column=1, value=item['sucursal']).border = border
                ws.cell(row=fila, column=2, value=item['direccion']).border = border
            
            cell = ws.cell(row=fila, column=3, value=item['total_pares'])
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila, column=4, value=item['total_costo'])
            cell.number_format = '#,##0'
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila, column=5, value=item['total_precio_interno'])
            cell.number_format = '#,##0'
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            cell = ws.cell(row=fila, column=6, value=item['total_precio_venta'])
            cell.number_format = '#,##0'
            cell.alignment = Alignment(horizontal='right')
            cell.border = border
            
            fila += 1
        
        # Fila de TOTALES
        ws.cell(row=fila, column=1, value="TOTALES:").font = total_font
        ws.cell(row=fila, column=1).fill = total_fill
        ws.cell(row=fila, column=1).border = border
        ws.cell(row=fila, column=1).alignment = Alignment(horizontal='right', vertical='center')
        
        ws.cell(row=fila, column=2, value="").fill = total_fill
        ws.cell(row=fila, column=2).border = border
        
        cell = ws.cell(row=fila, column=3, value=total_general.get('pares', 0))
        cell.font = total_font
        cell.fill = total_fill
        cell.alignment = Alignment(horizontal='right')
        cell.border = border
        
        cell = ws.cell(row=fila, column=4, value=total_general.get('costo', 0))
        cell.font = total_font
        cell.fill = total_fill
        cell.number_format = '#,##0'
        cell.alignment = Alignment(horizontal='right')
        cell.border = border
        
        cell = ws.cell(row=fila, column=5, value=total_general.get('precio_interno', 0))
        cell.font = total_font
        cell.fill = total_fill
        cell.number_format = '#,##0'
        cell.alignment = Alignment(horizontal='right')
        cell.border = border
        
        cell = ws.cell(row=fila, column=6, value=total_general.get('precio_venta', 0))
        cell.font = total_font
        cell.fill = total_fill
        cell.number_format = '#,##0'
        cell.alignment = Alignment(horizontal='right')
        cell.border = border
        
        ws.row_dimensions[fila].height = 25
        
        # Ajustar anchos de columna
        ws.column_dimensions['A'].width = 25
        ws.column_dimensions['B'].width = 35
        ws.column_dimensions['C'].width = 15
        ws.column_dimensions['D'].width = 18
        ws.column_dimensions['E'].width = 22
        ws.column_dimensions['F'].width = 20
        
        # Nombre del archivo
        filename = "resumen_existencias"
        if agrupar_por == 'categoria':
            filename += "_por_categoria"
        if es_historico and fecha_corte:
            filename += f"_{fecha_corte}"
        filename += ".xlsx"
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        import traceback
        print(f"❌ Error al exportar a Excel: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        })


@require_GET
@login_required
def verificar_disponibilidad_historico(request):
    """
    Verifica si hay suficientes movimientos registrados para calcular inventario histórico.
    Útil para mostrar alertas al usuario.
    """
    try:
        # Contar movimientos por tipo
        from django.db.models.functions import TruncMonth
        
        total_movimientos = Movimientos_Producto.objects.count()
        
        # Rango de fechas disponible
        primer_movimiento = Movimientos_Producto.objects.order_by('fecha').first()
        ultimo_movimiento = Movimientos_Producto.objects.order_by('-fecha').first()
        
        if total_movimientos == 0:
            return JsonResponse({
                'success': True,
                'disponible': False,
                'mensaje': 'No hay movimientos registrados. El inventario histórico no está disponible.',
                'total_movimientos': 0,
                'fecha_inicio': None,
                'fecha_fin': None
            })
        
        return JsonResponse({
            'success': True,
            'disponible': True,
            'total_movimientos': total_movimientos,
            'fecha_inicio': primer_movimiento.fecha.strftime('%Y-%m-%d') if primer_movimiento else None,
            'fecha_fin': ultimo_movimiento.fecha.strftime('%Y-%m-%d') if ultimo_movimiento else None,
            'mensaje': f'Historial disponible desde {primer_movimiento.fecha.strftime("%d/%m/%Y")} hasta {ultimo_movimiento.fecha.strftime("%d/%m/%Y")}'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })
