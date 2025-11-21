"""
Módulo de Resumen de Existencias - RetailMind
Vista para mostrar resumen de existencias agrupado por sucursal
"""

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from decimal import Decimal
import json

from .models import Sucursal, Producto


@login_required
def ver_resumen_existencias(request):
    """Vista principal del reporte de resumen de existencias"""
    return render(request, 'vistas/modulo_reportes/resumen_existencias.html')


@require_GET
@login_required
def obtener_resumen_existencias(request):
    """API para obtener resumen de existencias agrupado por sucursal"""
    try:
        # Parámetros de filtro opcionales
        marca_id = request.GET.get('marca_id')
        categoria_id = request.GET.get('categoria_id')
        
        # Obtener todas las sucursales
        sucursales = Sucursal.objects.all().order_by('alias')
        
        resumen_sucursales = []
        
        for sucursal in sucursales:
            # Filtrar productos de esta sucursal
            queryset_productos = Producto.objects.filter(
                sucursal_id=sucursal.id
            ).select_related('atributo1', 'categoria').prefetch_related('producto_talla')
            
            # Aplicar filtros opcionales
            if marca_id:
                queryset_productos = queryset_productos.filter(atributo1_id=marca_id)
            
            if categoria_id:
                queryset_productos = queryset_productos.filter(categoria_id=categoria_id)
            
            # Inicializar totales
            total_pares = 0
            total_costo = Decimal('0.00')
            total_precio_interno = Decimal('0.00')  # Sobreprecio
            total_precio_venta = Decimal('0.00')
            
            # Calcular totales
            for producto in queryset_productos:
                for talla in producto.producto_talla.all():
                    stock = talla.stock
                    
                    if stock > 0:
                        total_pares += stock
                        
                        # Costo total
                        if producto.costo:
                            total_costo += (Decimal(str(producto.costo)) * stock)
                        
                        # Precio interno (sobreprecio)
                        if producto.sobreprecio:
                            total_precio_interno += (Decimal(str(producto.sobreprecio)) * stock)
                        
                        # Precio venta total
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
        
        print(f"📊 Resumen generado para {len(resumen_sucursales)} sucursales")
        print(f"📈 Total general de pares: {total_general['pares']}")
        
        return JsonResponse({
            'success': True,
            'datos': resumen_sucursales,
            'total_general': total_general
        })
        
    except Exception as e:
        import traceback
        print(f"❌ Error en resumen de existencias: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener resumen: {str(e)}'
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
        
        # Reutilizar la función de obtención de datos
        temp_request = request
        response_data = obtener_resumen_existencias(temp_request)
        datos = json.loads(response_data.content)
        
        if not datos.get('success'):
            return JsonResponse({
                'success': False,
                'error': 'No se pudieron obtener los datos'
            })
        
        datos_resumen = datos.get('datos', [])
        total_general = datos.get('total_general', {})
        
        if not datos_resumen:
            return JsonResponse({
                'success': False,
                'error': 'No hay datos para exportar'
            })
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Resumen Existencias"
        
        # Estilos
        header_fill = PatternFill(start_color="667EEA", end_color="667EEA", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=12)
        total_fill = PatternFill(start_color="FFC107", end_color="FFC107", fill_type="solid")
        total_font = Font(bold=True, size=12)
        border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )
        
        # Título
        ws.merge_cells('A1:F1')
        cell = ws['A1']
        cell.value = "RESUMEN DE EXISTENCIAS POR SUCURSAL"
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF", size=14)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[1].height = 30
        
        # Encabezados
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
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="resumen_existencias.xlsx"'
        
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

