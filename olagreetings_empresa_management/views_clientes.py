from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum
from django.utils import timezone
import json
from datetime import datetime, timedelta

from .models import Empresa, Cliente, LogEmpresa

# ========== VISTAS DE CLIENTES ==========

@login_required
def gestion_clientes(request):
    """Vista principal para gestión de clientes"""
    return render(request, 'empresas/gestion_clientes.html')

@require_GET
@login_required
def listar_clientes(request):
    """Obtener lista de clientes con filtros y paginación"""
    try:
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))
        search = request.GET.get('search', '').strip()
        categoria = request.GET.get('categoria', '')
        estado = request.GET.get('estado', '')
        vendedor = request.GET.get('vendedor', '')
        
        # Obtener empresas que son clientes
        empresas_clientes = Empresa.objects.filter(
            tipo_empresa__in=['CLIENTE', 'CLIENTE_PROVEEDOR']
        )
        
        # Aplicar filtros de búsqueda
        if search:
            empresas_clientes = empresas_clientes.filter(
                Q(nombre__icontains=search) |
                Q(rut__icontains=search) |
                Q(nombre_fantasia__icontains=search) |
                Q(ciudad__icontains=search) |
                Q(contacto_principal__icontains=search)
            )
        
        if estado == 'activo':
            empresas_clientes = empresas_clientes.filter(es_activa=True)
        elif estado == 'inactivo':
            empresas_clientes = empresas_clientes.filter(es_activa=False)
        
        empresas_clientes = empresas_clientes.order_by('nombre')
        
        # Paginación
        paginator = Paginator(empresas_clientes, page_size)
        empresas_page = paginator.get_page(page)
        
        # Preparar datos con información de cliente
        clientes_data = []
        for empresa in empresas_page:
            # Obtener información específica del cliente si existe
            cliente_info = None
            try:
                cliente_info = empresa.cliente
            except Cliente.DoesNotExist:
                pass
            
            clientes_data.append({
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut,
                'nombre_fantasia': empresa.nombre_fantasia,
                'ciudad': empresa.ciudad,
                'telefono': empresa.telefono,
                'email_contacto': empresa.email_contacto,
                'contacto_principal': empresa.contacto_principal,
                'es_activa': empresa.es_activa,
                'fecha_creacion': empresa.fecha_creacion.strftime('%d/%m/%Y'),
                'tipo_cliente': cliente_info.tipo_cliente if cliente_info else 'EMPRESA',
                'categoria': cliente_info.categoria if cliente_info else 'D',
                'estado_cliente': cliente_info.estado if cliente_info else 'ACTIVO',
                'vendedor_asignado': cliente_info.vendedor_asignado.nombre if cliente_info and cliente_info.vendedor_asignado else '',
                'fecha_primer_compra': cliente_info.fecha_primer_compra.strftime('%d/%m/%Y') if cliente_info and cliente_info.fecha_primer_compra else '',
                'fecha_ultima_compra': cliente_info.fecha_ultima_compra.strftime('%d/%m/%Y') if cliente_info and cliente_info.fecha_ultima_compra else '',
                'total_compras': float(cliente_info.total_compras) if cliente_info else 0,
                'numero_compras': cliente_info.numero_compras if cliente_info else 0,
                'limite_credito': float(cliente_info.limite_credito_cliente) if cliente_info else 0,
                'saldo_actual': float(cliente_info.saldo_actual) if cliente_info else 0,
                'antiguedad_dias': cliente_info.calcular_antiguedad() if cliente_info else 0,
                'promedio_compra': float(cliente_info.calcular_promedio_compra()) if cliente_info else 0,
                'credito_disponible': float(cliente_info.get_credito_disponible()) if cliente_info else 0,
            })
        
        # Calcular métricas
        total_clientes = empresas_clientes.count()
        clientes_activos = empresas_clientes.filter(es_activa=True).count()
        clientes_categoria_a = empresas_clientes.filter(cliente__categoria='A').count()
        total_ventas = sum(float(emp.cliente.total_compras) for emp in empresas_clientes if hasattr(emp, 'cliente') and emp.cliente)
        
        return JsonResponse({
            'success': True,
            'clientes': clientes_data,
            'pagination': {
                'page': page,
                'total_pages': paginator.num_pages,
                'total_count': paginator.count,
                'has_previous': empresas_page.has_previous(),
                'has_next': empresas_page.has_next(),
            },
            'metricas': {
                'total_clientes': total_clientes,
                'clientes_activos': clientes_activos,
                'clientes_categoria_a': clientes_categoria_a,
                'total_ventas': total_ventas
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_POST
@login_required
@csrf_exempt
@transaction.atomic
def crear_cliente(request):
    """Crear nuevo cliente"""
    try:
        data = json.loads(request.body)
        
        # Primero crear la empresa
        from .views import validar_campos_empresa, validar_rut_chileno
        
        # Validar campos básicos de empresa
        es_valido, errores = validar_campos_empresa(data)
        if not es_valido:
            return JsonResponse({
                'success': False,
                'error': ' | '.join(errores)
            }, status=400)
        
        # Verificar RUT único
        if Empresa.objects.filter(rut=data['rut']).exists():
            return JsonResponse({
                'success': False,
                'error': 'Ya existe una empresa con ese RUT'
            }, status=400)
        
        # Crear empresa como cliente
        empresa = Empresa.objects.create(
            nombre=data['nombre'].strip(),
            rut=data['rut'].strip(),
            nombre_fantasia=data.get('nombre_fantasia', '').strip(),
            razon_social=data.get('razon_social', '').strip(),
            giro=data.get('giro', '').strip(),
            direccion=data.get('direccion', '').strip(),
            comuna=data.get('comuna', '').strip(),
            ciudad=data.get('ciudad', '').strip(),
            region=data.get('region', '').strip(),
            telefono=data.get('telefono', '').strip(),
            correo_vendedor=data.get('correo_vendedor', '').strip(),
            tipo_empresa='CLIENTE',
            es_activa=data.get('es_activa', True),
            contacto_principal=data.get('contacto_principal', '').strip(),
            email_contacto=data.get('email_contacto', '').strip(),
            limite_credito=data.get('limite_credito', 0),
        )
        
        # Crear información específica del cliente
        cliente = Cliente.objects.create(
            empresa=empresa,
            tipo_cliente=data.get('tipo_cliente', 'EMPRESA'),
            categoria=data.get('categoria', 'D'),
            estado=data.get('estado', 'ACTIVO'),
            limite_credito_cliente=data.get('limite_credito', 0),
            fuente_cliente=data.get('fuente_cliente', '').strip(),
            referido_por=data.get('referido_por', '').strip(),
            notas_comerciales=data.get('notas_comerciales', '').strip(),
        )
        
        # Registrar log
        from .views import registrar_log_empresa
        registrar_log_empresa(request, empresa, 'CREAR')
        
        return JsonResponse({
            'success': True,
            'message': f'Cliente {empresa.nombre} creado exitosamente',
            'cliente_id': empresa.id
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_http_methods(["GET", "PUT"])
@login_required
@csrf_exempt
@transaction.atomic
def gestionar_cliente(request, cliente_id):
    """Obtener o editar un cliente específico"""
    try:
        empresa = get_object_or_404(Empresa, id=cliente_id)
        
        if request.method == 'GET':
            # Obtener información del cliente
            try:
                cliente_info = empresa.cliente
            except Cliente.DoesNotExist:
                cliente_info = None
            
            return JsonResponse({
                'success': True,
                'cliente': {
                    # Información de empresa
                    'id': empresa.id,
                    'nombre': empresa.nombre,
                    'rut': empresa.rut,
                    'nombre_fantasia': empresa.nombre_fantasia,
                    'razon_social': empresa.razon_social,
                    'giro': empresa.giro,
                    'direccion': empresa.direccion,
                    'comuna': empresa.comuna,
                    'ciudad': empresa.ciudad,
                    'region': empresa.region,
                    'telefono': empresa.telefono,
                    'email_contacto': empresa.email_contacto,
                    'contacto_principal': empresa.contacto_principal,
                    'es_activa': empresa.es_activa,
                    'limite_credito': float(empresa.limite_credito),
                    # Información específica del cliente
                    'tipo_cliente': cliente_info.tipo_cliente if cliente_info else 'EMPRESA',
                    'categoria': cliente_info.categoria if cliente_info else 'D',
                    'estado': cliente_info.estado if cliente_info else 'ACTIVO',
                    'vendedor_asignado': cliente_info.vendedor_asignado.id if cliente_info and cliente_info.vendedor_asignado else None,
                    'fecha_primer_compra': cliente_info.fecha_primer_compra.strftime('%Y-%m-%d') if cliente_info and cliente_info.fecha_primer_compra else None,
                    'fecha_ultima_compra': cliente_info.fecha_ultima_compra.strftime('%Y-%m-%d') if cliente_info and cliente_info.fecha_ultima_compra else None,
                    'total_compras': float(cliente_info.total_compras) if cliente_info else 0,
                    'numero_compras': cliente_info.numero_compras if cliente_info else 0,
                    'limite_credito_cliente': float(cliente_info.limite_credito_cliente) if cliente_info else 0,
                    'saldo_actual': float(cliente_info.saldo_actual) if cliente_info else 0,
                    'fuente_cliente': cliente_info.fuente_cliente if cliente_info else '',
                    'referido_por': cliente_info.referido_por if cliente_info else '',
                    'notas_comerciales': cliente_info.notas_comerciales if cliente_info else '',
                }
            })
        
        elif request.method == 'PUT':
            data = json.loads(request.body)
            
            # Actualizar información de empresa
            campos_empresa = [
                'nombre', 'rut', 'nombre_fantasia', 'razon_social', 'giro',
                'direccion', 'comuna', 'ciudad', 'region', 'telefono',
                'email_contacto', 'contacto_principal', 'es_activa', 'limite_credito'
            ]
            
            for campo in campos_empresa:
                if campo in data:
                    setattr(empresa, campo, data[campo])
            
            empresa.save()
            
            # Actualizar o crear información específica del cliente
            try:
                cliente_info = empresa.cliente
            except Cliente.DoesNotExist:
                cliente_info = Cliente.objects.create(empresa=empresa)
            
            campos_cliente = [
                'tipo_cliente', 'categoria', 'estado', 'fecha_primer_compra',
                'fecha_ultima_compra', 'total_compras', 'numero_compras',
                'limite_credito_cliente', 'saldo_actual', 'fuente_cliente',
                'referido_por', 'notas_comerciales'
            ]
            
            for campo in campos_cliente:
                if campo in data:
                    setattr(cliente_info, campo, data[campo])
            
            cliente_info.save()
            
            # Registrar log
            from .views import registrar_log_empresa
            registrar_log_empresa(request, empresa, 'EDITAR')
            
            return JsonResponse({
                'success': True,
                'message': f'Cliente {empresa.nombre} actualizado exitosamente'
            })
    
    except Empresa.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Cliente no encontrado'
        }, status=404)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_GET
@login_required
def obtener_cliente(request, cliente_id):
    """Obtener información de un cliente específico por su ID (solo lectura)"""
    try:
        empresa = get_object_or_404(Empresa, id=cliente_id)
        try:
            cliente_info = empresa.cliente
        except Cliente.DoesNotExist:
            cliente_info = None
        return JsonResponse({
            'success': True,
            'cliente': {
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut,
                'nombre_fantasia': empresa.nombre_fantasia,
                'razon_social': empresa.razon_social,
                'giro': empresa.giro,
                'direccion': empresa.direccion,
                'comuna': empresa.comuna,
                'ciudad': empresa.ciudad,
                'region': empresa.region,
                'telefono': empresa.telefono,
                'email_contacto': empresa.email_contacto,
                'contacto_principal': empresa.contacto_principal,
                'es_activa': empresa.es_activa,
                'limite_credito': float(empresa.limite_credito),
                'tipo_cliente': cliente_info.tipo_cliente if cliente_info else 'EMPRESA',
                'categoria': cliente_info.categoria if cliente_info else 'D',
                'estado': cliente_info.estado if cliente_info else 'ACTIVO',
                'vendedor_asignado': cliente_info.vendedor_asignado.id if cliente_info and cliente_info.vendedor_asignado else None,
                'fecha_primer_compra': cliente_info.fecha_primer_compra.strftime('%Y-%m-%d') if cliente_info and cliente_info.fecha_primer_compra else None,
                'fecha_ultima_compra': cliente_info.fecha_ultima_compra.strftime('%Y-%m-%d') if cliente_info and cliente_info.fecha_ultima_compra else None,
                'total_compras': float(cliente_info.total_compras) if cliente_info else 0,
                'numero_compras': cliente_info.numero_compras if cliente_info else 0,
                'limite_credito_cliente': float(cliente_info.limite_credito_cliente) if cliente_info else 0,
                'saldo_actual': float(cliente_info.saldo_actual) if cliente_info else 0,
                'fuente_cliente': cliente_info.fuente_cliente if cliente_info else '',
                'referido_por': cliente_info.referido_por if cliente_info else '',
                'notas_comerciales': cliente_info.notas_comerciales if cliente_info else '',
            }
        })
    except Empresa.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cliente no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@require_GET
@login_required
def dashboard_clientes(request):
    """Dashboard con métricas de clientes"""
    try:
        # Obtener estadísticas generales
        total_clientes = Empresa.objects.filter(tipo_empresa__in=['CLIENTE', 'CLIENTE_PROVEEDOR']).count()
        clientes_activos = Empresa.objects.filter(
            tipo_empresa__in=['CLIENTE', 'CLIENTE_PROVEEDOR'],
            es_activa=True
        ).count()
        
        # Estadísticas por categoría
        categorias = {
            'A': Cliente.objects.filter(categoria='A').count(),
            'B': Cliente.objects.filter(categoria='B').count(),
            'C': Cliente.objects.filter(categoria='C').count(),
            'D': Cliente.objects.filter(categoria='D').count(),
        }
        
        # Top 10 clientes por ventas
        top_clientes = Cliente.objects.filter(
            total_compras__gt=0
        ).order_by('-total_compras')[:10]
        
        top_clientes_data = []
        for cliente in top_clientes:
            top_clientes_data.append({
                'nombre': cliente.empresa.nombre,
                'total_compras': float(cliente.total_compras),
                'numero_compras': cliente.numero_compras,
                'categoria': cliente.categoria,
                'antiguedad_dias': cliente.calcular_antiguedad()
            })
        
        # Clientes nuevos este mes
        inicio_mes = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        clientes_nuevos_mes = Empresa.objects.filter(
            tipo_empresa__in=['CLIENTE', 'CLIENTE_PROVEEDOR'],
            fecha_creacion__gte=inicio_mes
        ).count()
        
        # Clientes inactivos (sin compras en 90 días)
        fecha_limite = timezone.now().date() - timedelta(days=90)
        clientes_inactivos = Cliente.objects.filter(
            fecha_ultima_compra__lt=fecha_limite
        ).count()
        
        return JsonResponse({
            'success': True,
            'metricas': {
                'total_clientes': total_clientes,
                'clientes_activos': clientes_activos,
                'clientes_nuevos_mes': clientes_nuevos_mes,
                'clientes_inactivos': clientes_inactivos,
                'categorias': categorias,
                'top_clientes': top_clientes_data
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@require_GET
@login_required
def reporte_clientes(request):
    """Generar reporte detallado de clientes"""
    try:
        # Parámetros del reporte
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        categoria = request.GET.get('categoria', '')
        
        # Filtrar clientes
        clientes = Cliente.objects.all()
        
        if fecha_inicio:
            clientes = clientes.filter(fecha_primer_compra__gte=fecha_inicio)
        if fecha_fin:
            clientes = clientes.filter(fecha_primer_compra__lte=fecha_fin)
        if categoria:
            clientes = clientes.filter(categoria=categoria)
        
        # Preparar datos del reporte
        reporte_data = []
        for cliente in clientes:
            reporte_data.append({
                'nombre': cliente.empresa.nombre,
                'rut': cliente.empresa.rut,
                'categoria': cliente.categoria,
                'estado': cliente.estado,
                'fecha_primer_compra': cliente.fecha_primer_compra.strftime('%d/%m/%Y') if cliente.fecha_primer_compra else '',
                'fecha_ultima_compra': cliente.fecha_ultima_compra.strftime('%d/%m/%Y') if cliente.fecha_ultima_compra else '',
                'total_compras': float(cliente.total_compras),
                'numero_compras': cliente.numero_compras,
                'promedio_compra': float(cliente.calcular_promedio_compra()),
                'antiguedad_dias': cliente.calcular_antiguedad(),
                'limite_credito': float(cliente.limite_credito_cliente),
                'saldo_actual': float(cliente.saldo_actual),
                'credito_disponible': float(cliente.get_credito_disponible()),
                'vendedor': cliente.vendedor_asignado.nombre if cliente.vendedor_asignado else '',
            })
        
        return JsonResponse({
            'success': True,
            'reporte': reporte_data,
            'total_registros': len(reporte_data)
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500) 