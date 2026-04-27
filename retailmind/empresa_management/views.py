from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum, Value
from django.db.models.functions import Replace
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import csv
from datetime import datetime, timedelta

from app.models import (
    Empresa, Sucursal, ContactoEmpresa, LogEmpresa,
)

# Utilidad interna para sanitizar valores de campos CharField
def _clean_char_field(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    return str(value)


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
        empresas = empresas.annotate(num_sucursales=Count('sucursales_app')).order_by('-num_sucursales')
    
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
                num_sucursales = empresa.sucursales_app.count()
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
                'email': empresa.correoVendedor or '',
                'telefono': empresa.contacto1 or '',
                'contacto1': empresa.contacto1 or '',
                'contacto2': empresa.contacto2 or '',
                'acteco': empresa.acteco or '',
                'correoVendedor': empresa.correoVendedor or '',
                'correoIntercambio': empresa.correoIntercambio or '',
                'correoAdministrador': empresa.correoAdministrador or '',
                'direccion': empresa.direccion or '',
                'comuna': empresa.comuna or '',
                'ciudad': empresa.ciudad or '',
                'region': empresa.region or '',
                'codigo_postal': empresa.codigo_postal or '',
                'sitio_web': empresa.sitio_web or '',
                'activo': empresa.activo,
                'fecha_creacion': empresa.created_at.isoformat() if empresa.created_at else None,
                'fecha_actualizacion': empresa.updated_at.isoformat() if empresa.updated_at else None,
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


@login_required
def buscar_empresa_ajax(request):
    """API para buscar empresa por RUT o nombre para auto-completar en facturas"""
    try:
        termino = request.GET.get('q', '').strip()
        termino_normalizado = termino.replace('.', '').replace('-', '').replace(' ', '')
        
        if len(termino_normalizado) < 3 and len(termino) < 3:
            return JsonResponse({
                'success': False,
                'error': 'Ingrese al menos 3 caracteres'
            })
        
        # Buscar empresa por RUT (normalizado) o nombre
        empresas_query = Empresa.objects.annotate(
            rut_normalizado=Replace(
                Replace(
                    Replace('rut', Value('.'), Value('')),
                    Value('-'), Value('')
                ),
                Value(' '), Value('')
            )
        ).filter(
            Q(rut_normalizado__icontains=termino_normalizado) |
            Q(rut__icontains=termino) |
            Q(nombre__icontains=termino) |
            Q(razon_social__icontains=termino) |
            Q(nombre_fantasia__icontains=termino)
        ).order_by('-id')
        
        # Contar total antes de hacer slice
        total_encontradas = empresas_query.count()
        
        # Limitar resultados
        empresas = list(empresas_query[:10])
        
        if empresas:
            empresa = empresas[0]  # Tomar la primera coincidencia
            print(f"🔍 Empresa encontrada: {empresa.id} - {empresa.rut} - {empresa.giro}")
            
            return JsonResponse({
                'success': True,
                'empresa': {
                    'id': empresa.id,
                    'nombre': empresa.nombre,
                    'razon_social': empresa.razon_social,
                    'nombre_fantasia': empresa.nombre_fantasia,
                    'rut': empresa.rut,
                    'giro': empresa.giro,
                    'direccion': empresa.direccion,
                    'comuna': empresa.comuna,
                    'ciudad': empresa.ciudad,
                    'telefono': empresa.contacto1 or '',  # Usar contacto1 en lugar de telefono
                    'email': empresa.correoVendedor or empresa.correoAdministrador or '',
                    'acteco': empresa.acteco or '',
                },
                'total_encontradas': total_encontradas
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'No se encontraron empresas'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar empresa: {str(e)}'
        })

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
                'error': 'El RUT es obligatorio'
            }, status=400)
        
        if not data.get('razon_social'):
            return JsonResponse({
                'success': False,
                'error': 'La razón social es obligatoria'
            }, status=400)
        
        # Determinar si es proveedor basado en el tipo
        tipo = data.get('tipo', 'cliente')
        es_proveedor = tipo in ['proveedor', 'ambos']

        correo_vendedor = data.get('correoVendedor') or data.get('email')
        
        # Crear empresa usando solo los campos que existen en app.models.Empresa
        empresa = Empresa(
            nombre=data.get('razon_social', ''),  # Usar razon_social como nombre
            rut=data.get('rut', ''),
            nombre_fantasia=data.get('nombre_fantasia', ''),
            razon_social=data.get('razon_social', ''),
            giro=data.get('giro') or data.get('giro_comercial', ''),  # Aceptar ambos nombres
            direccion=data.get('direccion', ''),
            comuna=data.get('comuna', ''),
            ciudad=data.get('ciudad', ''),
            region=data.get('region') or None,
            codigo_postal=data.get('codigo_postal') or None,
            esProveedor=es_proveedor,
            correoVendedor=_clean_char_field(correo_vendedor),
            correoIntercambio=_clean_char_field(data.get('correoIntercambio')),
            correoAdministrador=_clean_char_field(data.get('correoAdministrador')),
            acteco=_clean_char_field(data.get('acteco')),
            contacto1=_clean_char_field(data.get('contacto1') or data.get('telefono')),
            contacto2=_clean_char_field(data.get('contacto2')),
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
                'nombre_fantasia': empresa.nombre_fantasia,
                'giro': empresa.giro,
                'direccion': empresa.direccion,
                'comuna': empresa.comuna,
                'ciudad': empresa.ciudad,
                'region': empresa.region or '',
                'codigo_postal': empresa.codigo_postal or '',
                'telefono': empresa.contacto1 or '',
                'email': empresa.correoVendedor or empresa.correoAdministrador or '',
                'acteco': empresa.acteco or '',
                'tipo_display': tipo_display,
                'esProveedor': empresa.esProveedor,
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear empresa: {str(e)}'
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
                'error': 'ID de empresa requerido'
            }, status=400)
        
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        # Validaciones básicas
        if not data.get('rut'):
            return JsonResponse({
                'success': False,
                'error': 'El RUT es obligatorio'
            }, status=400)
        
        if not data.get('razon_social'):
            return JsonResponse({
                'success': False,
                'error': 'La razón social es obligatoria'
            }, status=400)
        
        # Determinar si es proveedor basado en el tipo
        tipo = data.get('tipo', 'cliente')
        es_proveedor = tipo in ['proveedor', 'ambos']

        correo_vendedor = data.get('correoVendedor') or data.get('email')
        
        # Actualizar campos usando solo los campos que existen en app.models.Empresa
        empresa.nombre = data.get('razon_social', '')  # Usar razon_social como nombre
        empresa.rut = data.get('rut', '')
        empresa.nombre_fantasia = data.get('nombre_fantasia', '')
        empresa.razon_social = data.get('razon_social', '')
        empresa.giro = data.get('giro') or data.get('giro_comercial', '')
        empresa.direccion = data.get('direccion', '')
        empresa.comuna = data.get('comuna', '')
        empresa.ciudad = data.get('ciudad', '')
        empresa.region = data.get('region') or None
        empresa.codigo_postal = data.get('codigo_postal') or None
        empresa.esProveedor = es_proveedor
        empresa.correoVendedor = _clean_char_field(correo_vendedor)
        empresa.correoIntercambio = _clean_char_field(data.get('correoIntercambio'))
        empresa.correoAdministrador = _clean_char_field(data.get('correoAdministrador'))
        empresa.acteco = _clean_char_field(data.get('acteco'))
        empresa.contacto1 = _clean_char_field(data.get('contacto1') or data.get('telefono'))
        empresa.contacto2 = _clean_char_field(data.get('contacto2'))
        
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
                'giro': empresa.giro,
                'direccion': empresa.direccion,
                'comuna': empresa.comuna,
                'ciudad': empresa.ciudad,
                'region': empresa.region or '',
                'codigo_postal': empresa.codigo_postal or '',
                'tipo_display': tipo_display,
                'esProveedor': empresa.esProveedor,
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al actualizar empresa: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["DELETE"])
def eliminar_empresa(request, empresa_id):
    """Eliminar empresa via AJAX"""
    
    try:
        empresa = get_object_or_404(Empresa, id=empresa_id)
        
        # Verificar si tiene registros relacionados
        if empresa.sucursales_app.exists():
            return JsonResponse({
                'success': False,
                'error': 'No se puede eliminar la empresa porque tiene sucursales asociadas. Elimina las sucursales primero.'
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
            'error': f'Error al eliminar empresa: {str(e)}'
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
    sucursales = empresa.sucursales_app.all()
    contactos = empresa.contactos_crm.all()
    clientes = empresa.clientes_crm.all()
    
    # Obtener logs recientes
    logs = empresa.logs_crm.all()[:10]
    
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
    tipo = request.GET.get('tipo', '')
    
    # Query base
    empresas = Empresa.objects.all()
    
    # Aplicar filtros
    if search:
        empresas = empresas.filter(
            Q(nombre__icontains=search) |
            Q(rut__icontains=search) |
            Q(nombre_fantasia__icontains=search) |
            Q(razon_social__icontains=search)
        )
    
    # Filtro por tipo (usando esProveedor)
    if tipo == 'proveedor':
        empresas = empresas.filter(esProveedor=True)
    elif tipo == 'cliente':
        empresas = empresas.filter(esProveedor=False)
    
    # Crear respuesta CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="empresas_{timezone.localtime().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    writer = csv.writer(response)
    
    # Encabezados (solo campos que existen en app.models.Empresa)
    writer.writerow([
        'ID', 'Nombre', 'RUT', 'Nombre Fantasía', 'Razón Social', 'Giro',
        'Dirección', 'Comuna', 'Ciudad', 'Tipo', 'Código Acteco',
        'Contacto 1', 'Contacto 2',
        'Correo Vendedor', 'Correo Intercambio', 'Correo Administrador'
    ])
    
    # Datos
    for empresa in empresas:
        tipo_display = 'Proveedor' if empresa.esProveedor else 'Cliente'
        writer.writerow([
            empresa.id,
            empresa.nombre or '',
            empresa.rut or '',
            empresa.nombre_fantasia or '',
            empresa.razon_social or '',
            empresa.giro or '',
            empresa.direccion or '',
            empresa.comuna or '',
            empresa.ciudad or '',
            tipo_display,
            empresa.acteco or '',
            empresa.contacto1 or '',
            empresa.contacto2 or '',
            empresa.correoVendedor or '',
            empresa.correoIntercambio or '',
            empresa.correoAdministrador or ''
        ])
    
    return response

@login_required
def exportar_empresas_con_sucursales(request):
    """Exportar empresas CON sucursales a CSV (una fila por sucursal)"""
    
    # Solo empresas que tienen sucursales
    empresas = Empresa.objects.filter(sucursales_app__isnull=False).distinct().prefetch_related('sucursales_app')
    
    # Crear respuesta CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="empresas_con_sucursales_{timezone.localtime().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    writer = csv.writer(response)
    
    # Encabezados
    writer.writerow([
        'empresa_rut', 'empresa_nombre', 'empresa_nombre_fantasia', 'empresa_razon_social', 
        'empresa_giro', 'empresa_direccion', 'empresa_comuna', 'empresa_ciudad',
        'empresa_tipo', 'empresa_acteco', 'empresa_contacto1', 'empresa_contacto2',
        'empresa_correo_vendedor', 'empresa_correo_intercambio', 'empresa_correo_administrador',
        'sucursal_alias', 'sucursal_direccion', 'sucursal_tipo', 'sucursal_es_centro_distribucion',
        'sucursal_margen_sobreprecio'
    ])
    
    # Datos - una fila por cada sucursal
    for empresa in empresas:
        tipo_empresa = 'proveedor' if empresa.esProveedor else 'cliente'
        
        for sucursal in empresa.sucursales_app.all():
            writer.writerow([
                empresa.rut or '',
                empresa.nombre or '',
                empresa.nombre_fantasia or '',
                empresa.razon_social or '',
                empresa.giro or '',
                empresa.direccion or '',
                empresa.comuna or '',
                empresa.ciudad or '',
                tipo_empresa,
                empresa.acteco or '',
                empresa.contacto1 or '',
                empresa.contacto2 or '',
                empresa.correoVendedor or '',
                empresa.correoIntercambio or '',
                empresa.correoAdministrador or '',
                sucursal.alias or '',
                sucursal.direccion or '',
                sucursal.tipo_sucursal or 'VENDEDORA',
                '1' if sucursal.es_centro_distribucion else '0',
                str(sucursal.margen_sobreprecio_default or 0)
            ])
    
    return response


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def importar_empresas_con_sucursales(request):
    """Importar empresas con sucursales desde CSV"""
    
    try:
        if 'archivo' not in request.FILES:
            return JsonResponse({
                'success': False,
                'error': 'No se proporcionó archivo CSV'
            }, status=400)
        
        archivo = request.FILES['archivo']
        
        # Verificar extensión
        if not archivo.name.endswith('.csv'):
            return JsonResponse({
                'success': False,
                'error': 'El archivo debe ser CSV'
            }, status=400)
        
        # Leer CSV
        try:
            contenido = archivo.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            contenido = archivo.read().decode('latin-1')
        
        reader = csv.DictReader(contenido.splitlines())
        
        empresas_creadas = 0
        empresas_actualizadas = 0
        sucursales_creadas = 0
        sucursales_actualizadas = 0
        errores = []
        
        # Agrupar por empresa (RUT)
        empresas_dict = {}
        
        for idx, row in enumerate(reader, start=2):
            try:
                rut = row.get('empresa_rut', '').strip()
                if not rut:
                    errores.append(f'Fila {idx}: RUT de empresa vacío')
                    continue
                
                # Si no hemos procesado esta empresa, guardarla
                if rut not in empresas_dict:
                    empresas_dict[rut] = {
                        'datos': row,
                        'sucursales': []
                    }
                
                # Agregar sucursal si tiene alias
                alias_sucursal = row.get('sucursal_alias', '').strip()
                if alias_sucursal:
                    empresas_dict[rut]['sucursales'].append({
                        'alias': alias_sucursal,
                        'direccion': row.get('sucursal_direccion', '').strip(),
                        'tipo_sucursal': row.get('sucursal_tipo', 'VENDEDORA').strip(),
                        'es_centro_distribucion': row.get('sucursal_es_centro_distribucion', '0').strip() == '1',
                        'margen_sobreprecio': row.get('sucursal_margen_sobreprecio', '0').strip()
                    })
                    
            except Exception as e:
                errores.append(f'Fila {idx}: {str(e)}')
        
        # Crear/actualizar empresas y sucursales
        with transaction.atomic():
            for rut, data in empresas_dict.items():
                row = data['datos']
                
                # Buscar o crear empresa
                empresa, created = Empresa.objects.get_or_create(
                    rut=rut,
                    defaults={
                        'nombre': row.get('empresa_nombre', row.get('empresa_razon_social', '')),
                        'nombre_fantasia': row.get('empresa_nombre_fantasia', ''),
                        'razon_social': row.get('empresa_razon_social', ''),
                        'giro': row.get('empresa_giro', ''),
                        'direccion': row.get('empresa_direccion', ''),
                        'comuna': row.get('empresa_comuna', ''),
                        'ciudad': row.get('empresa_ciudad', ''),
                        'esProveedor': row.get('empresa_tipo', 'cliente').lower() == 'proveedor',
                        'acteco': row.get('empresa_acteco', ''),
                        'contacto1': row.get('empresa_contacto1', ''),
                        'contacto2': row.get('empresa_contacto2', ''),
                        'correoVendedor': row.get('empresa_correo_vendedor', ''),
                        'correoIntercambio': row.get('empresa_correo_intercambio', ''),
                        'correoAdministrador': row.get('empresa_correo_administrador', ''),
                    }
                )
                
                if created:
                    empresas_creadas += 1
                else:
                    empresas_actualizadas += 1
                
                # Crear/actualizar sucursales
                for suc_data in data['sucursales']:
                    try:
                        margen = float(suc_data['margen_sobreprecio'] or 0)
                    except:
                        margen = 0
                    
                    sucursal, suc_created = Sucursal.objects.get_or_create(
                        empresa=empresa,
                        alias=suc_data['alias'],
                        defaults={
                            'direccion': suc_data['direccion'],
                            'tipo_sucursal': suc_data['tipo_sucursal'],
                            'es_centro_distribucion': suc_data['es_centro_distribucion'],
                            'margen_sobreprecio_default': margen
                        }
                    )
                    
                    if suc_created:
                        sucursales_creadas += 1
                    else:
                        # Actualizar datos de sucursal existente
                        sucursal.direccion = suc_data['direccion']
                        sucursal.tipo_sucursal = suc_data['tipo_sucursal']
                        sucursal.es_centro_distribucion = suc_data['es_centro_distribucion']
                        sucursal.margen_sobreprecio_default = margen
                        sucursal.save()
                        sucursales_actualizadas += 1
        
        return JsonResponse({
            'success': True,
            'message': f'Importación completada',
            'detalle': {
                'empresas_creadas': empresas_creadas,
                'empresas_actualizadas': empresas_actualizadas,
                'sucursales_creadas': sucursales_creadas,
                'sucursales_actualizadas': sucursales_actualizadas,
                'errores': errores[:20]  # Limitar errores mostrados
            }
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Error al importar: {str(e)}'
        }, status=500)


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
    fecha_limite = timezone.localdate() - timedelta(days=30)
    empresas_recientes = Empresa.objects.filter(
        created_at__date__gte=fecha_limite
    ).count()
    
    # Top 5 empresas con más sucursales
    top_empresas_sucursales = Empresa.objects.annotate(
        num_sucursales=Count('sucursales_app')
    ).filter(num_sucursales__gt=0).order_by('-num_sucursales')[:5]
    
    # Empresas sin contactos
    empresas_sin_contactos = Empresa.objects.filter(
        contactos_crm__isnull=True
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
@require_http_methods(["POST", "DELETE"])
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
            'error': f'Error al eliminar sucursal: {str(e)}'
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
        
        # Contar sucursales (usando sucursales_app ya que es el related_name del modelo)
        try:
            num_sucursales = empresa.sucursales_app.count()
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
                'telefono': empresa.contacto1 or '',
                'direccion': empresa.direccion or '',
                'comuna': empresa.comuna or '',
                'ciudad': empresa.ciudad or '',
                'region': empresa.region or '',
                'codigo_postal': empresa.codigo_postal or '',
                'sitio_web': empresa.sitio_web or '',
                'representante_legal': '',
                'activo': empresa.activo,
                'fecha_creacion': empresa.created_at.isoformat() if empresa.created_at else None,
                'fecha_actualizacion': empresa.updated_at.isoformat() if empresa.updated_at else None,
                'num_sucursales': num_sucursales,
                'num_contactos': num_contactos,
                'correoVendedor': empresa.correoVendedor or '',
                'correoIntercambio': empresa.correoIntercambio or '',
                'correoAdministrador': empresa.correoAdministrador or '',
                'contacto1': empresa.contacto1 or '',
                'contacto2': empresa.contacto2 or '',
                'acteco': empresa.acteco or '',
            }
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener empresa: {str(e)}'
        }, status=500) 