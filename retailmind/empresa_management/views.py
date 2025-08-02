from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import csv
from datetime import datetime, timedelta

from .models import (
    Empresa, Sucursal, ContactoEmpresa, Cliente, 
    Proveedor, LogEmpresa, LogCliente
)

# ========== VISTAS PARA EMPRESAS ==========

@login_required
def lista_empresas(request):
    """Vista para listar empresas con filtros y paginación"""
    
    # Obtener parámetros de filtro
    search = request.GET.get('search', '')
    tipo_empresa = request.GET.get('tipo_empresa', '')
    activo = request.GET.get('activo', '')
    orden = request.GET.get('orden', 'nombre')
    
    # Query base
    empresas = Empresa.objects.all()
    
    # Aplicar filtros
    if search:
        empresas = empresas.filter(
            Q(nombre__icontains=search) |
            Q(rut__icontains=search) |
            Q(nombre_fantasia__icontains=search) |
            Q(razon_social__icontains=search) |
            Q(giro__icontains=search)
        )
    
    if tipo_empresa:
        empresas = empresas.filter(tipo_empresa=tipo_empresa)
    
    if activo != '':
        empresas = empresas.filter(activo=activo == 'true')
    
    # Aplicar ordenamiento
    if orden == 'nombre':
        empresas = empresas.order_by('nombre')
    elif orden == 'rut':
        empresas = empresas.order_by('rut')
    elif orden == 'fecha_creacion':
        empresas = empresas.order_by('-fecha_creacion')
    elif orden == 'tipo':
        empresas = empresas.order_by('tipo_empresa', 'nombre')
    
    # Paginación
    paginator = Paginator(empresas, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estadísticas
    total_empresas = empresas.count()
    empresas_activas = empresas.filter(activo=True).count()
    empresas_inactivas = empresas.filter(activo=False).count()
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'tipo_empresa': tipo_empresa,
        'activo': activo,
        'orden': orden,
        'total_empresas': total_empresas,
        'empresas_activas': empresas_activas,
        'empresas_inactivas': empresas_inactivas,
        'tipos_empresa': Empresa.TIPO_EMPRESA_CHOICES,
    }
    
    return render(request, 'vistas/lista_empresas.html', context)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def crear_empresa(request):
    """Crear nueva empresa via AJAX"""
    
    try:
        data = json.loads(request.body)
        
        with transaction.atomic():
            # Crear empresa
            empresa = Empresa(
                nombre=data['nombre'],
                rut=data['rut'],
                nombre_fantasia=data.get('nombre_fantasia', ''),
                razon_social=data.get('razon_social', ''),
                giro=data.get('giro', ''),
                direccion=data.get('direccion', ''),
                comuna=data.get('comuna', ''),
                ciudad=data.get('ciudad', ''),
                region=data.get('region', ''),
                codigo_postal=data.get('codigo_postal', ''),
                telefono=data.get('telefono', ''),
                email=data.get('email', ''),
                sitio_web=data.get('sitio_web', ''),
                tipo_empresa=data.get('tipo_empresa', 'CLIENTE'),
                esProveedor=data.get('esProveedor', False),
                correoVendedor=data.get('correoVendedor', ''),
                correoIntercambio=data.get('correoIntercambio', ''),
                correoAdministrador=data.get('correoAdministrador', ''),
                observaciones=data.get('observaciones', ''),
                created_by=request.user
            )
            empresa.full_clean()
            empresa.save()
            
            # Crear log
            LogEmpresa.objects.create(
                empresa=empresa,
                usuario=request.user,
                accion='CREAR',
                descripcion=f'Empresa "{empresa.nombre}" creada',
                datos_nuevos={
                    'nombre': empresa.nombre,
                    'rut': empresa.rut,
                    'tipo_empresa': empresa.tipo_empresa,
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Empresa "{empresa.nombre}" creada exitosamente',
                'empresa': {
                    'id': empresa.id,
                    'nombre': empresa.nombre,
                    'rut': empresa.rut,
                    'tipo_empresa': empresa.get_tipo_empresa_display(),
                    'activo': empresa.activo,
                }
            })
            
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'message': 'Error de validación',
            'errors': e.message_dict
        }, status=400)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al crear empresa: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def editar_empresa(request, empresa_id):
    """Editar empresa existente via AJAX"""
    
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        data = json.loads(request.body)
        
        # Guardar datos anteriores para el log
        datos_anteriores = {
            'nombre': empresa.nombre,
            'rut': empresa.rut,
            'tipo_empresa': empresa.tipo_empresa,
            'activo': empresa.activo,
        }
        
        with transaction.atomic():
            # Actualizar campos
            empresa.nombre = data['nombre']
            empresa.rut = data['rut']
            empresa.nombre_fantasia = data.get('nombre_fantasia', '')
            empresa.razon_social = data.get('razon_social', '')
            empresa.giro = data.get('giro', '')
            empresa.direccion = data.get('direccion', '')
            empresa.comuna = data.get('comuna', '')
            empresa.ciudad = data.get('ciudad', '')
            empresa.region = data.get('region', '')
            empresa.codigo_postal = data.get('codigo_postal', '')
            empresa.telefono = data.get('telefono', '')
            empresa.email = data.get('email', '')
            empresa.sitio_web = data.get('sitio_web', '')
            empresa.tipo_empresa = data.get('tipo_empresa', 'CLIENTE')
            empresa.esProveedor = data.get('esProveedor', False)
            empresa.correoVendedor = data.get('correoVendedor', '')
            empresa.correoIntercambio = data.get('correoIntercambio', '')
            empresa.correoAdministrador = data.get('correoAdministrador', '')
            empresa.observaciones = data.get('observaciones', '')
            empresa.updated_by = request.user
            
            empresa.full_clean()
            empresa.save()
            
            # Crear log
            LogEmpresa.objects.create(
                empresa=empresa,
                usuario=request.user,
                accion='EDITAR',
                descripcion=f'Empresa "{empresa.nombre}" modificada',
                datos_anteriores=datos_anteriores,
                datos_nuevos={
                    'nombre': empresa.nombre,
                    'rut': empresa.rut,
                    'tipo_empresa': empresa.tipo_empresa,
                    'activo': empresa.activo,
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Empresa "{empresa.nombre}" actualizada exitosamente',
                'empresa': {
                    'id': empresa.id,
                    'nombre': empresa.nombre,
                    'rut': empresa.rut,
                    'tipo_empresa': empresa.get_tipo_empresa_display(),
                    'activo': empresa.activo,
                }
            })
            
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'message': 'Error de validación',
            'errors': e.message_dict
        }, status=400)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al actualizar empresa: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def eliminar_empresa(request, empresa_id):
    """Eliminar empresa via AJAX"""
    
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        with transaction.atomic():
            # Verificar si tiene registros relacionados
            if empresa.sucursales.exists():
                return JsonResponse({
                    'success': False,
                    'message': 'No se puede eliminar la empresa porque tiene sucursales asociadas'
                }, status=400)
            
            if empresa.clientes.exists():
                return JsonResponse({
                    'success': False,
                    'message': 'No se puede eliminar la empresa porque tiene clientes asociados'
                }, status=400)
            
            nombre_empresa = empresa.nombre
            
            # Crear log antes de eliminar
            LogEmpresa.objects.create(
                empresa=empresa,
                usuario=request.user,
                accion='ELIMINAR',
                descripcion=f'Empresa "{nombre_empresa}" eliminada',
                datos_anteriores={
                    'nombre': empresa.nombre,
                    'rut': empresa.rut,
                    'tipo_empresa': empresa.tipo_empresa,
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            empresa.delete()
            
            return JsonResponse({
                'success': True,
                'message': f'Empresa "{nombre_empresa}" eliminada exitosamente'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al eliminar empresa: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def activar_desactivar_empresa(request, empresa_id):
    """Activar o desactivar empresa via AJAX"""
    
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        with transaction.atomic():
            empresa.activo = not empresa.activo
            empresa.updated_by = request.user
            empresa.save()
            
            accion = 'ACTIVAR' if empresa.activo else 'DESACTIVAR'
            estado = 'activada' if empresa.activo else 'desactivada'
            
            # Crear log
            LogEmpresa.objects.create(
                empresa=empresa,
                usuario=request.user,
                accion=accion,
                descripcion=f'Empresa "{empresa.nombre}" {estado}',
                datos_nuevos={'activo': empresa.activo},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Empresa "{empresa.nombre}" {estado} exitosamente',
                'activo': empresa.activo
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al cambiar estado de empresa: {str(e)}'
        }, status=500)

@login_required
def detalle_empresa(request, empresa_id):
    """Vista detallada de empresa"""
    
    empresa = get_object_or_404(Empresa, id=empresa_id)
    
    # Obtener datos relacionados
    sucursales = empresa.sucursales.all()
    contactos = empresa.contactos.all()
    clientes = empresa.clientes.all()
    
    # Obtener logs recientes
    logs = empresa.logs.all()[:10]
    
    context = {
        'empresa': empresa,
        'sucursales': sucursales,
        'contactos': contactos,
        'clientes': clientes,
        'logs': logs,
    }
    
    return render(request, 'empresa_management/detalle_empresa.html', context)

@login_required
def exportar_empresas(request):
    """Exportar empresas a CSV"""
    
    # Obtener parámetros de filtro
    search = request.GET.get('search', '')
    tipo_empresa = request.GET.get('tipo_empresa', '')
    activo = request.GET.get('activo', '')
    
    # Query base
    empresas = Empresa.objects.all()
    
    # Aplicar filtros
    if search:
        empresas = empresas.filter(
            Q(nombre__icontains=search) |
            Q(rut__icontains=search) |
            Q(nombre_fantasia__icontains=search)
        )
    
    if tipo_empresa:
        empresas = empresas.filter(tipo_empresa=tipo_empresa)
    
    if activo != '':
        empresas = empresas.filter(activo=activo == 'true')
    
    # Crear respuesta CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="empresas_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    writer = csv.writer(response)
    
    # Encabezados
    writer.writerow([
        'ID', 'Nombre', 'RUT', 'Nombre Fantasía', 'Razón Social', 'Giro',
        'Dirección', 'Comuna', 'Ciudad', 'Región', 'Código Postal',
        'Teléfono', 'Email', 'Sitio Web', 'Tipo Empresa', 'Es Proveedor',
        'Correo Vendedor', 'Correo Intercambio', 'Correo Administrador',
        'Fecha Creación', 'Activo', 'Observaciones'
    ])
    
    # Datos
    for empresa in empresas:
        writer.writerow([
            empresa.id,
            empresa.nombre,
            empresa.rut,
            empresa.nombre_fantasia,
            empresa.razon_social,
            empresa.giro,
            empresa.direccion,
            empresa.comuna,
            empresa.ciudad,
            empresa.region,
            empresa.codigo_postal,
            empresa.telefono,
            empresa.email,
            empresa.sitio_web,
            empresa.get_tipo_empresa_display(),
            'Sí' if empresa.esProveedor else 'No',
            empresa.correoVendedor,
            empresa.correoIntercambio,
            empresa.correoAdministrador,
            empresa.fecha_creacion.strftime('%d/%m/%Y'),
            'Sí' if empresa.activo else 'No',
            empresa.observaciones
        ])
    
    return response

@login_required
def dashboard_empresas(request):
    """Dashboard con estadísticas de empresas"""
    
    # Estadísticas generales
    total_empresas = Empresa.objects.count()
    empresas_activas = Empresa.objects.filter(activo=True).count()
    empresas_inactivas = Empresa.objects.filter(activo=False).count()
    
    # Por tipo de empresa
    por_tipo = Empresa.objects.values('tipo_empresa').annotate(
        count=Count('id')
    ).order_by('tipo_empresa')
    
    # Empresas creadas en los últimos 30 días
    fecha_limite = timezone.now().date() - timedelta(days=30)
    empresas_recientes = Empresa.objects.filter(
        fecha_creacion__gte=fecha_limite
    ).count()
    
    # Top 5 empresas con más sucursales
    top_empresas_sucursales = Empresa.objects.annotate(
        num_sucursales=Count('sucursales')
    ).filter(num_sucursales__gt=0).order_by('-num_sucursales')[:5]
    
    # Empresas sin contactos
    empresas_sin_contactos = Empresa.objects.filter(
        contactos__isnull=True
    ).count()
    
    context = {
        'total_empresas': total_empresas,
        'empresas_activas': empresas_activas,
        'empresas_inactivas': empresas_inactivas,
        'por_tipo': por_tipo,
        'empresas_recientes': empresas_recientes,
        'top_empresas_sucursales': top_empresas_sucursales,
        'empresas_sin_contactos': empresas_sin_contactos,
    }
    
    return render(request, 'empresa_management/dashboard_empresas.html', context)

# ========== VISTAS PARA SUCURSALES ==========

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def crear_sucursal(request, empresa_id):
    """Crear nueva sucursal via AJAX"""
    
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        data = json.loads(request.body)
        
        with transaction.atomic():
            sucursal = Sucursal(
                empresa=empresa,
                alias=data['alias'],
                nombre=data.get('nombre', ''),
                direccion=data.get('direccion', ''),
                comuna=data.get('comuna', ''),
                ciudad=data.get('ciudad', ''),
                telefono=data.get('telefono', ''),
                email=data.get('email', ''),
            )
            sucursal.full_clean()
            sucursal.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Sucursal "{sucursal.alias}" creada exitosamente',
                'sucursal': {
                    'id': sucursal.id,
                    'alias': sucursal.alias,
                    'nombre': sucursal.nombre,
                    'direccion': sucursal.direccion,
                    'activa': sucursal.activa,
                }
            })
            
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'message': 'Error de validación',
            'errors': e.message_dict
        }, status=400)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al crear sucursal: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def eliminar_sucursal(request, sucursal_id):
    """Eliminar sucursal via AJAX"""
    
    try:
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        with transaction.atomic():
            alias_sucursal = sucursal.alias
            sucursal.delete()
            
            return JsonResponse({
                'success': True,
                'message': f'Sucursal "{alias_sucursal}" eliminada exitosamente'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al eliminar sucursal: {str(e)}'
        }, status=500)

# ========== VISTAS PARA CONTACTOS ==========

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def crear_contacto(request, empresa_id):
    """Crear nuevo contacto via AJAX"""
    
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        data = json.loads(request.body)
        
        with transaction.atomic():
            contacto = ContactoEmpresa(
                empresa=empresa,
                nombre=data['nombre'],
                cargo=data.get('cargo', ''),
                email=data.get('email', ''),
                telefono=data.get('telefono', ''),
                celular=data.get('celular', ''),
                tipo_contacto=data.get('tipo_contacto', 'PRINCIPAL'),
            )
            contacto.full_clean()
            contacto.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Contacto "{contacto.nombre}" creado exitosamente',
                'contacto': {
                    'id': contacto.id,
                    'nombre': contacto.nombre,
                    'cargo': contacto.cargo,
                    'email': contacto.email,
                    'telefono': contacto.telefono,
                    'tipo_contacto': contacto.get_tipo_contacto_display(),
                    'activo': contacto.activo,
                }
            })
            
    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'message': 'Error de validación',
            'errors': e.message_dict
        }, status=400)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al crear contacto: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def eliminar_contacto(request, contacto_id):
    """Eliminar contacto via AJAX"""
    
    try:
        contacto = get_object_or_404(ContactoEmpresa, id=contacto_id)
        
        with transaction.atomic():
            nombre_contacto = contacto.nombre
            contacto.delete()
            
            return JsonResponse({
                'success': True,
                'message': f'Contacto "{nombre_contacto}" eliminado exitosamente'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al eliminar contacto: {str(e)}'
        }, status=500)

# ========== VISTAS ADICIONALES PARA AJAX ==========

@login_required
def obtener_empresa(request, empresa_id):
    """Obtener datos de una empresa para AJAX"""
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        # Contar sucursales y contactos
        num_sucursales = empresa.sucursales.count()
        num_contactos = empresa.contactos.count()
        
        return JsonResponse({
            'success': True,
            'empresa': {
                'id': empresa.id,
                'rut': empresa.rut,
                'razon_social': empresa.razon_social,
                'nombre_fantasia': empresa.nombre_fantasia,
                'tipo': empresa.tipo,
                'get_tipo_display': empresa.get_tipo_display(),
                'giro_comercial': empresa.giro_comercial,
                'email': empresa.email,
                'telefono': empresa.telefono,
                'direccion': empresa.direccion,
                'ciudad': empresa.ciudad,
                'region': empresa.region,
                'codigo_postal': empresa.codigo_postal,
                'sitio_web': empresa.sitio_web,
                'representante_legal': empresa.representante_legal,
                'activo': empresa.activo,
                'fecha_creacion': empresa.fecha_creacion.isoformat() if empresa.fecha_creacion else None,
                'fecha_actualizacion': empresa.fecha_actualizacion.isoformat() if empresa.fecha_actualizacion else None,
                'num_sucursales': num_sucursales,
                'num_contactos': num_contactos,
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener empresa: {str(e)}'
        }, status=500) 