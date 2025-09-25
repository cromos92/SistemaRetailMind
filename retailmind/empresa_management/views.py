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

# Importar modelos de app (donde están las empresas reales)
from app.models import Empresa, Sucursal

# Importar modelos locales si existen
try:
    from .models import ContactoEmpresa, Cliente, Proveedor, LogEmpresa, LogCliente
except ImportError:
    # Si no existen estos modelos, crear clases vacías para evitar errores
    ContactoEmpresa = None
    Cliente = None
    Proveedor = None
    LogEmpresa = None
    LogCliente = None

# ========== VISTAS PARA EMPRESAS ==========

@login_required
def lista_empresas(request):
    """Vista para listar empresas con filtros y paginación"""
    
    # Obtener parámetros de filtro
    search = request.GET.get('search', '')
    tipo_empresa = request.GET.get('tipo', '')  # Cambiar para coincidir con el JS
    estado = request.GET.get('estado', '')  # Cambiar para coincidir con el JS
    orden = request.GET.get('ordenar', 'nombre')  # Cambiar para coincidir con el JS
    page_size = int(request.GET.get('page_size', 25))
    
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
    
    # Filtro por tipo (usando esProveedor)
    if tipo_empresa == 'proveedor':
        empresas = empresas.filter(esProveedor=True)
    elif tipo_empresa == 'cliente':
        empresas = empresas.filter(esProveedor=False)
    
    # El modelo app.Empresa no tiene campo activo, así que ignoramos este filtro por ahora
    
    # Aplicar ordenamiento
    if orden == 'nombre':
        empresas = empresas.order_by('nombre')
    elif orden == 'rut':
        empresas = empresas.order_by('rut')
    elif orden == 'fecha':
        empresas = empresas.order_by('id')  # Usar ID como proxy de fecha de creación
    elif orden == 'sucursales':
        empresas = empresas.annotate(num_sucursales=Count('sucursal_set')).order_by('-num_sucursales')
    
    # Paginación
    paginator = Paginator(empresas, page_size)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Si es una request AJAX, devolver JSON
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
        empresas_data = []
        for empresa in page_obj:
            # Contar sucursales relacionadas
            try:
                num_sucursales = empresa.sucursal_set.count()
            except:
                num_sucursales = 0
            
            # Para contactos, usar 0 por ahora ya que no existe en app.models
            num_contactos = 0
            
            # Determinar tipo basado en esProveedor
            if empresa.esProveedor:
                tipo_display = 'Proveedor'
                tipo = 'proveedor'
            else:
                tipo_display = 'Cliente'
                tipo = 'cliente'
            
            empresas_data.append({
                'id': empresa.id,
                'rut': empresa.rut or '',
                'razon_social': empresa.razon_social or '',
                'nombre_fantasia': empresa.nombre_fantasia or '',
                'nombre': empresa.nombre or '',
                'tipo': tipo,
                'get_tipo_display': tipo_display,
                'giro_comercial': empresa.giro or '',
                'email': '',  # No existe en app.models
                'telefono': '',  # No existe en app.models
                'direccion': empresa.direccion or '',
                'ciudad': empresa.ciudad or '',
                'region': '',  # No existe en app.models
                'codigo_postal': '',  # No existe en app.models
                'sitio_web': '',  # No existe en app.models
                'activo': True,  # Asumir que todas están activas
                'fecha_creacion': None,  # No existe en app.models
                'fecha_actualizacion': None,  # No existe en app.models
                'num_sucursales': num_sucursales,
                'num_contactos': num_contactos,
            })
        
        return JsonResponse({
            'success': True,
            'empresas': empresas_data,
            'total_registros': paginator.count,
            'total_paginas': paginator.num_pages,
            'pagina_actual': page_obj.number,
        })
    
    # Para requests normales, devolver el template
    context = {
        'page_obj': page_obj,
        'search': search,
        'tipo_empresa': tipo_empresa,
        'estado': estado,
        'orden': orden,
        'tipos_empresa': [
            ('cliente', 'Cliente'),
            ('proveedor', 'Proveedor'),
        ],
    }
    
    return render(request, 'empresa_management/lista_empresas.html', context)

# ========== VISTAS PARA SUCURSALES ==========

@login_required
def listar_sucursales(request, empresa_id):
    """Listar sucursales de una empresa específica"""
    empresa = get_object_or_404(Empresa, id=empresa_id)
    sucursales = Sucursal.objects.filter(empresa=empresa)
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        sucursales_data = []
        for sucursal in sucursales:
            sucursales_data.append({
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
                'empresa_id': sucursal.empresa.id,
                'empresa_nombre': sucursal.empresa.nombre,
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data,
            'empresa': {
                'id': empresa.id,
                'nombre': empresa.nombre,
                'razon_social': empresa.razon_social,
            }
        })
    
    context = {
        'empresa': empresa,
        'sucursales': sucursales,
    }
    return render(request, 'empresa_management/sucursales_empresa.html', context)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def crear_sucursal(request, empresa_id):
    """Crear nueva sucursal para una empresa"""
    print(f"🚀 INICIO crear_sucursal - empresa_id: {empresa_id}")
    try:
        print(f"📋 Buscando empresa con ID: {empresa_id}")
        empresa = get_object_or_404(Empresa, id=empresa_id)
        print(f"✅ Empresa encontrada: {empresa.nombre}")
        
        # Debug: imprimir información de la request
        print(f"📡 Content-Type: {request.content_type}")
        print(f"📡 Request body: {request.body}")
        
        print(f"🔄 Parseando JSON...")
        data = json.loads(request.body)
        print(f"✅ JSON parseado: {data}")
        
        # Validaciones
        alias = data.get('alias', '').strip()
        direccion = data.get('direccion', '').strip()
        
        if not alias:
            return JsonResponse({
                'success': False,
                'error': 'El alias de la sucursal es obligatorio'
            })
        
        if not direccion:
            return JsonResponse({
                'success': False,
                'error': 'La dirección de la sucursal es obligatoria'
            })
        
        # Verificar que no exista otra sucursal con el mismo alias en la empresa
        if Sucursal.objects.filter(empresa=empresa, alias=alias).exists():
            return JsonResponse({
                'success': False,
                'error': f'Ya existe una sucursal con el alias "{alias}" en esta empresa'
            })
        
        # Validar longitud de campos
        if len(alias) > 100:
            return JsonResponse({
                'success': False,
                'error': 'El alias no puede tener más de 100 caracteres'
            })
        
        if len(direccion) > 100:
            return JsonResponse({
                'success': False,
                'error': 'La dirección no puede tener más de 100 caracteres'
            })
        
        # Crear la sucursal
        sucursal = Sucursal.objects.create(
            empresa=empresa,
            alias=alias,
            direccion=direccion
        )
        
        return JsonResponse({
            'success': True,
            'message': f'Sucursal "{alias}" creada exitosamente',
            'sucursal': {
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
                'empresa_id': sucursal.empresa.id,
                'empresa_nombre': sucursal.empresa.nombre,
            }
        })
        
    except json.JSONDecodeError as e:
        print(f"Error JSON: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': f'Datos JSON inválidos: {str(e)}'
        })
    except Exception as e:
        print(f"Error general: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Error al crear sucursal: {str(e)}'
        })

@login_required
@csrf_exempt
@require_http_methods(["PUT"])
def editar_sucursal(request, sucursal_id):
    """Editar una sucursal existente"""
    sucursal = get_object_or_404(Sucursal, id=sucursal_id)
    
    try:
        data = json.loads(request.body)
        
        # Validaciones
        alias = data.get('alias', '').strip()
        direccion = data.get('direccion', '').strip()
        
        if not alias:
            return JsonResponse({
                'success': False,
                'error': 'El alias de la sucursal es obligatorio'
            })
        
        if not direccion:
            return JsonResponse({
                'success': False,
                'error': 'La dirección de la sucursal es obligatoria'
            })
        
        # Validar longitud de campos
        if len(alias) > 100:
            return JsonResponse({
                'success': False,
                'error': 'El alias no puede tener más de 100 caracteres'
            })
        
        if len(direccion) > 100:
            return JsonResponse({
                'success': False,
                'error': 'La dirección no puede tener más de 100 caracteres'
            })
        
        # Verificar que no exista otra sucursal con el mismo alias en la empresa (excluyendo la actual)
        if Sucursal.objects.filter(empresa=sucursal.empresa, alias=alias).exclude(id=sucursal.id).exists():
            return JsonResponse({
                'success': False,
                'error': f'Ya existe otra sucursal con el alias "{alias}" en esta empresa'
            })
        
        # Actualizar la sucursal
        sucursal.alias = alias
        sucursal.direccion = direccion
        sucursal.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Sucursal "{alias}" actualizada exitosamente',
            'sucursal': {
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
                'empresa_id': sucursal.empresa.id,
                'empresa_nombre': sucursal.empresa.nombre,
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Datos JSON inválidos'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al actualizar sucursal: {str(e)}'
        })

@login_required
@csrf_exempt
@require_http_methods(["DELETE"])
def eliminar_sucursal(request, sucursal_id):
    """Eliminar una sucursal"""
    sucursal = get_object_or_404(Sucursal, id=sucursal_id)
    
    try:
        alias = sucursal.alias
        empresa_nombre = sucursal.empresa.nombre
        sucursal.delete()
        
        return JsonResponse({
            'success': True,
            'message': f'Sucursal "{alias}" de {empresa_nombre} eliminada exitosamente'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al eliminar sucursal: {str(e)}'
        })

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def crear_empresa(request):
    """Crear nueva empresa via AJAX"""
    
    try:
        data = json.loads(request.body)
        
        # Validaciones básicas
        if not data.get('rut'):
            return JsonResponse({
                'success': False,
                'message': 'El RUT es obligatorio'
            }, status=400)
        
        if not data.get('razon_social'):
            return JsonResponse({
                'success': False,
                'message': 'La razón social es obligatoria'
            }, status=400)
        
        # Determinar si es proveedor basado en el tipo
        tipo = data.get('tipo', 'cliente')
        es_proveedor = tipo == 'proveedor'
        
        # Crear empresa usando solo los campos que existen en app.models.Empresa
        empresa = Empresa(
            nombre=data.get('razon_social', ''),  # Usar razon_social como nombre
            rut=data.get('rut', ''),
            nombre_fantasia=data.get('nombre_fantasia', ''),
            razon_social=data.get('razon_social', ''),
            giro=data.get('giro_comercial', ''),
            direccion=data.get('direccion', ''),
            comuna=data.get('ciudad', ''),  # Usar ciudad como comuna
            ciudad=data.get('ciudad', ''),
            esProveedor=es_proveedor,
            correoVendedor=data.get('email', ''),  # Usar email como correoVendedor
            correoIntercambio=data.get('correoIntercambio', ''),
            correoAdministrador=data.get('correoAdministrador', ''),
        )
        
        empresa.save()
        
        # Determinar tipo para la respuesta
        tipo_display = 'Proveedor' if es_proveedor else 'Cliente'
        
        return JsonResponse({
            'success': True,
            'message': f'Empresa "{empresa.razon_social}" creada exitosamente',
            'empresa': {
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut,
                'razon_social': empresa.razon_social,
                'tipo_display': tipo_display,
                'esProveedor': empresa.esProveedor,
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al crear empresa: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["PUT"])
def editar_empresa(request):
    """Editar empresa existente via AJAX"""
    
    try:
        data = json.loads(request.body)
        empresa_id = data.get('empresa_id')
        
        if not empresa_id:
            return JsonResponse({
                'success': False,
                'message': 'ID de empresa requerido'
            }, status=400)
        
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        # Validaciones básicas
        if not data.get('rut'):
            return JsonResponse({
                'success': False,
                'message': 'El RUT es obligatorio'
            }, status=400)
        
        if not data.get('razon_social'):
            return JsonResponse({
                'success': False,
                'message': 'La razón social es obligatoria'
            }, status=400)
        
        # Determinar si es proveedor basado en el tipo
        tipo = data.get('tipo', 'cliente')
        es_proveedor = tipo == 'proveedor'
        
        # Actualizar campos usando solo los campos que existen en app.models.Empresa
        empresa.nombre = data.get('razon_social', '')  # Usar razon_social como nombre
        empresa.rut = data.get('rut', '')
        empresa.nombre_fantasia = data.get('nombre_fantasia', '')
        empresa.razon_social = data.get('razon_social', '')
        empresa.giro = data.get('giro_comercial', '')
        empresa.direccion = data.get('direccion', '')
        empresa.comuna = data.get('ciudad', '')  # Usar ciudad como comuna
        empresa.ciudad = data.get('ciudad', '')
        empresa.esProveedor = es_proveedor
        empresa.correoVendedor = data.get('email', '')  # Usar email como correoVendedor
        empresa.correoIntercambio = data.get('correoIntercambio', '')
        empresa.correoAdministrador = data.get('correoAdministrador', '')
        
        empresa.save()
        
        # Determinar tipo para la respuesta
        tipo_display = 'Proveedor' if es_proveedor else 'Cliente'
        
        return JsonResponse({
            'success': True,
            'message': f'Empresa "{empresa.razon_social}" actualizada exitosamente',
            'empresa': {
                'id': empresa.id,
                'nombre': empresa.nombre,
                'rut': empresa.rut,
                'razon_social': empresa.razon_social,
                'tipo_display': tipo_display,
                'esProveedor': empresa.esProveedor,
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al actualizar empresa: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["DELETE"])
def eliminar_empresa(request, empresa_id):
    """Eliminar empresa via AJAX"""
    
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
            # Verificar si tiene registros relacionados
        if empresa.sucursal_set.exists():
                return JsonResponse({
                    'success': False,
                    'message': 'No se puede eliminar la empresa porque tiene sucursales asociadas'
                }, status=400)
            
        nombre_empresa = empresa.razon_social or empresa.nombre
        
        # Eliminar la empresa (sin log ya que LogEmpresa no existe)
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
    empresas_activas = total_empresas  # Asumir que todas están activas
    empresas_inactivas = 0  # No hay campo activo en app.models
    
    # Contar sucursales y contactos totales
    try:
        total_sucursales = Sucursal.objects.count()
    except:
        total_sucursales = 0
    
    # No hay ContactoEmpresa en app.models
    total_contactos = 0
    
    # Si es una request AJAX, devolver JSON
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({
            'success': True,
            'metricas': {
                'total_empresas': total_empresas,
                'empresas_activas': empresas_activas,
                'empresas_inactivas': empresas_inactivas,
                'total_sucursales': total_sucursales,
                'total_contactos': total_contactos,
            }
        })
    
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
        'total_sucursales': total_sucursales,
        'total_contactos': total_contactos,
    }
    
    return render(request, 'empresa_management/dashboard_empresas.html', context)

# ========== FUNCIÓN DUPLICADA ELIMINADA ==========
# La función crear_sucursal ya está definida arriba en la línea 184

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
        
        # Contar sucursales (usando sucursal_set ya que es la relación correcta)
        try:
            num_sucursales = empresa.sucursal_set.count()
        except:
            num_sucursales = 0
        
        # No hay contactos en app.models
        num_contactos = 0
        
        # Determinar tipo basado en esProveedor
        if empresa.esProveedor:
            tipo_display = 'Proveedor'
            tipo = 'proveedor'
        else:
            tipo_display = 'Cliente'
            tipo = 'cliente'
        
        return JsonResponse({
            'success': True,
            'empresa': {
                'id': empresa.id,
                'rut': empresa.rut or '',
                'razon_social': empresa.razon_social or '',
                'nombre_fantasia': empresa.nombre_fantasia or '',
                'nombre': empresa.nombre or '',
                'tipo': tipo,
                'get_tipo_display': tipo_display,
                'giro_comercial': empresa.giro or '',
                'email': empresa.correoVendedor or '',  # Usar correoVendedor como email
                'telefono': '',  # No existe en app.models
                'direccion': empresa.direccion or '',
                'ciudad': empresa.ciudad or '',
                'region': '',  # No existe en app.models
                'codigo_postal': '',  # No existe en app.models
                'sitio_web': '',  # No existe en app.models
                'representante_legal': '',  # No existe en app.models
                'activo': True,  # Asumir que todas están activas
                'fecha_creacion': None,  # No existe en app.models
                'fecha_actualizacion': None,  # No existe en app.models
                'num_sucursales': num_sucursales,
                'num_contactos': num_contactos,
                'correoVendedor': empresa.correoVendedor or '',
                'correoIntercambio': empresa.correoIntercambio or '',
                'correoAdministrador': empresa.correoAdministrador or '',
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener empresa: {str(e)}'
        }, status=500) 