from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Count, Sum, Avg
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.exceptions import ValidationError
from django.db import transaction
import json
import csv
import logging
from datetime import datetime, timedelta

from app.models import Cliente, LogCliente, Empresa

logger = logging.getLogger(__name__)


# ---- Helpers compartidos ----
def _opt(value):
    """Devuelve None para valores vacíos, en otro caso un string sin espacios.

    Útil para CharField/EmailField nullable: evita guardar '' donde el modelo
    espera NULL o un valor real.
    """
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def _parse_fecha(value):
    """Parsea 'YYYY-MM-DD'. Devuelve None si vacío o inválido."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None


_TIPO_CLIENTE_ALIAS = {
    'NATURAL': 'INDIVIDUAL',
    'PERSONA': 'INDIVIDUAL',
    'INDIVIDUAL': 'INDIVIDUAL',
    'JURIDICA': 'EMPRESARIAL',
    'JURÍDICA': 'EMPRESARIAL',
    'EMPRESA': 'EMPRESARIAL',
    'EMPRESARIAL': 'EMPRESARIAL',
    'MAYORISTA': 'MAYORISTA',
    'DISTRIBUIDOR': 'DISTRIBUIDOR',
    'EMPLEADO': 'EMPLEADO',
    'CREDITO_EXTERNO': 'CREDITO_EXTERNO',
}


def _normalizar_tipo_cliente(valor):
    """Normaliza el tipo de cliente del frontend a un valor válido del modelo."""
    if not valor:
        return 'INDIVIDUAL'
    candidato = _TIPO_CLIENTE_ALIAS.get(str(valor).strip().upper())
    if candidato:
        return candidato
    validos = {c[0] for c in Cliente.TIPO_CLIENTE_CHOICES}
    upper = str(valor).strip().upper()
    return upper if upper in validos else 'INDIVIDUAL'


def _errores_validation(exc):
    """Extrae errores de un ValidationError de forma segura.

    Devuelve siempre un dict {campo: [mensajes]}.
    """
    try:
        return exc.message_dict
    except AttributeError:
        msgs = getattr(exc, 'messages', None) or [str(exc)]
        return {'__all__': list(msgs)}

# ========== VISTAS PARA CLIENTES ==========

@login_required
def lista_clientes(request):
    """Vista para listar clientes con filtros y paginación (HTML y JSON)"""
    
    # Obtener parámetros de filtro
    search = request.GET.get('search', '')
    tipo_cliente = request.GET.get('tipo_cliente', '') or request.GET.get('tipo', '')
    empresa_id = request.GET.get('empresa', '')
    activo = request.GET.get('activo', '') or request.GET.get('estado', '')
    orden = request.GET.get('orden', 'apellido') or request.GET.get('ordenar', 'apellido')
    
    # Parámetros de paginación AJAX
    page_size = request.GET.get('page_size', None)
    page = request.GET.get('page', 1)
    
    # Query base - TODOS los clientes de TODAS las sucursales
    clientes = Cliente.objects.select_related('empresa').all()
    
    # Aplicar filtros
    if search:
        clientes = clientes.filter(
            Q(nombre__icontains=search) |
            Q(apellido__icontains=search) |
            Q(rut__icontains=search) |
            Q(email__icontains=search) |
            Q(empresa__nombre__icontains=search)
        )
    
    if tipo_cliente:
        if tipo_cliente in ['natural', 'INDIVIDUAL']:
            clientes = clientes.filter(tipo_cliente='INDIVIDUAL')
        elif tipo_cliente in ['juridica', 'EMPRESA']:
            clientes = clientes.filter(tipo_cliente='EMPRESA')
        else:
            clientes = clientes.filter(tipo_cliente=tipo_cliente)
    
    if empresa_id:
        clientes = clientes.filter(empresa_id=empresa_id)
    
    if activo != '':
        if activo in ['activo', 'true', 'True', '1']:
            clientes = clientes.filter(activo=True)
        elif activo in ['inactivo', 'false', 'False', '0']:
            clientes = clientes.filter(activo=False)
    
    # Aplicar ordenamiento
    if orden in ['apellido', 'nombre']:
        clientes = clientes.order_by('apellido', 'nombre')
    elif orden == 'rut':
        clientes = clientes.order_by('rut')
    elif orden in ['fecha_creacion', 'fecha']:
        clientes = clientes.order_by('-created_at')
    elif orden == 'tipo':
        clientes = clientes.order_by('tipo_cliente', 'apellido', 'nombre')
    elif orden in ['empresa', 'ventas']:
        clientes = clientes.order_by('empresa__nombre', 'apellido', 'nombre')
    else:
        clientes = clientes.order_by('apellido', 'nombre')
    
    # Si es una solicitud AJAX (tiene page_size), devolver JSON
    if page_size:
        try:
            page_size = int(page_size)
            page = int(page)
        except ValueError:
            page_size = 25
            page = 1
        
        # Paginación
        paginator = Paginator(clientes, page_size)
        page_obj = paginator.get_page(page)
        
        # Serializar clientes
        clientes_data = []
        for cliente in page_obj:
            clientes_data.append({
                'id': cliente.id,
                'nombre': cliente.nombre_completo if hasattr(cliente, 'nombre_completo') else f"{cliente.nombre} {cliente.apellido}".strip(),
                'rut': cliente.rut or '',
                'tipo': cliente.tipo_cliente,
                'get_tipo_display': cliente.get_tipo_cliente_display() if hasattr(cliente, 'get_tipo_cliente_display') else cliente.tipo_cliente,
                'email': cliente.email or '',
                'telefono': cliente.telefono or cliente.celular or '',
                'empresa': cliente.empresa.nombre if cliente.empresa else '',
                'activo': cliente.activo,
                'created_at': cliente.created_at.strftime('%Y-%m-%d %H:%M') if cliente.created_at else '',
            })
        
        return JsonResponse({
            'success': True,
            'clientes': clientes_data,
            'total_registros': paginator.count,
            'total_paginas': paginator.num_pages,
            'pagina_actual': page,
        })
    
    # Si no es AJAX, devolver HTML normal
    paginator = Paginator(clientes, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estadísticas
    total_clientes = clientes.count()
    clientes_activos = clientes.filter(activo=True).count()
    clientes_inactivos = clientes.filter(activo=False).count()
    
    # Empresas para filtro
    empresas = Empresa.objects.filter(activo=True).order_by('nombre')
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'tipo_cliente': tipo_cliente,
        'empresa_id': empresa_id,
        'activo': activo,
        'orden': orden,
        'total_clientes': total_clientes,
        'clientes_activos': clientes_activos,
        'clientes_inactivos': clientes_inactivos,
        'tipos_cliente': Cliente.TIPO_CLIENTE_CHOICES,
        'empresas': empresas,
    }
    
    return render(request, 'empresa_management/lista_clientes.html', context)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def crear_cliente(request):
    """Crear nuevo cliente via AJAX"""

    try:
        try:
            data = json.loads(request.body or b'{}')
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'message': 'Datos JSON inválidos'
            }, status=400)

        nombre = (data.get('nombre') or '').strip()
        apellido = (data.get('apellido') or '').strip()
        if not nombre or not apellido:
            return JsonResponse({
                'success': False,
                'message': 'Nombre y apellido son obligatorios',
                'errors': {
                    'nombre': ['Requerido'] if not nombre else [],
                    'apellido': ['Requerido'] if not apellido else [],
                }
            }, status=400)

        with transaction.atomic():
            # Obtener empresa si se especifica
            empresa = None
            empresa_id = data.get('empresa_id')
            if empresa_id:
                try:
                    empresa = Empresa.objects.get(id=empresa_id)
                except Empresa.DoesNotExist:
                    return JsonResponse({
                        'success': False,
                        'message': 'La empresa seleccionada no existe',
                        'errors': {'empresa_id': ['No encontrada']}
                    }, status=400)

            cliente = Cliente(
                nombre=nombre,
                apellido=apellido,
                rut=_opt(data.get('rut')),
                email=_opt(data.get('email')),
                telefono=_opt(data.get('telefono')),
                celular=_opt(data.get('celular')),
                direccion=_opt(data.get('direccion')),
                comuna=_opt(data.get('comuna')),
                ciudad=_opt(data.get('ciudad')),
                fecha_nacimiento=_parse_fecha(data.get('fecha_nacimiento')),
                genero=_opt(data.get('genero')),
                tipo_cliente=_normalizar_tipo_cliente(
                    data.get('tipo_cliente') or data.get('tipo')
                ),
                empresa=empresa,
                observaciones=_opt(data.get('observaciones')),
                created_by=request.user,
            )

            cliente.full_clean()
            cliente.save()

            LogCliente.objects.create(
                cliente=cliente,
                usuario=request.user,
                accion='CREAR',
                descripcion=f'Cliente "{cliente.nombre_completo}" creado',
                datos_nuevos={
                    'nombre': cliente.nombre,
                    'apellido': cliente.apellido,
                    'rut': cliente.rut,
                    'tipo_cliente': cliente.tipo_cliente,
                    'empresa': empresa.nombre if empresa else None,
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )

            return JsonResponse({
                'success': True,
                'message': f'Cliente "{cliente.nombre_completo}" creado exitosamente',
                'cliente': {
                    'id': cliente.id,
                    'nombre': cliente.nombre,
                    'apellido': cliente.apellido,
                    'nombre_completo': cliente.nombre_completo,
                    'rut': cliente.rut,
                    'email': cliente.email,
                    'tipo_cliente': cliente.get_tipo_cliente_display(),
                    'empresa': empresa.nombre if empresa else None,
                    'activo': cliente.activo,
                }
            })

    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'message': 'Error de validación',
            'errors': _errores_validation(e),
        }, status=400)

    except Exception as e:
        logger.exception('Error inesperado al crear cliente')
        return JsonResponse({
            'success': False,
            'message': f'Error al crear cliente: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST", "PUT"])
def editar_cliente(request):
    """Editar cliente existente via AJAX"""

    try:
        try:
            data = json.loads(request.body or b'{}')
        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'message': 'Datos JSON inválidos'
            }, status=400)

        cliente_id = data.get('cliente_id') or data.get('id')
        if not cliente_id:
            return JsonResponse({
                'success': False,
                'message': 'ID de cliente no proporcionado'
            }, status=400)

        try:
            cliente = Cliente.objects.get(id=cliente_id)
        except Cliente.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': 'Cliente no encontrado'
            }, status=404)

        datos_anteriores = {
            'nombre': cliente.nombre,
            'apellido': cliente.apellido,
            'rut': cliente.rut,
            'tipo_cliente': cliente.tipo_cliente,
            'empresa': cliente.empresa.nombre if cliente.empresa else None,
            'activo': cliente.activo,
        }

        with transaction.atomic():
            # Mantener la empresa actual por defecto; solo cambia si llega empresa_id
            empresa = cliente.empresa
            empresa_id = data.get('empresa_id')
            if empresa_id:
                try:
                    empresa = Empresa.objects.get(id=empresa_id)
                except Empresa.DoesNotExist:
                    return JsonResponse({
                        'success': False,
                        'message': 'La empresa seleccionada no existe',
                        'errors': {'empresa_id': ['No encontrada']}
                    }, status=400)

            # Nombre/apellido: aceptar separados o "nombre completo" en `nombre`
            apellido_in = (data.get('apellido') or '').strip()
            nombre_in = (data.get('nombre') or '').strip()
            if apellido_in:
                cliente.nombre = nombre_in
                cliente.apellido = apellido_in
            elif nombre_in:
                partes = nombre_in.split(' ', 1)
                cliente.nombre = partes[0]
                cliente.apellido = partes[1] if len(partes) > 1 else cliente.apellido

            # Solo actualizar campos presentes en el payload (PATCH-like)
            if 'rut' in data:
                cliente.rut = _opt(data.get('rut'))
            if 'email' in data:
                cliente.email = _opt(data.get('email'))
            if 'telefono' in data:
                cliente.telefono = _opt(data.get('telefono'))
            if 'celular' in data:
                cliente.celular = _opt(data.get('celular'))
            if 'direccion' in data:
                cliente.direccion = _opt(data.get('direccion'))
            if 'comuna' in data:
                cliente.comuna = _opt(data.get('comuna'))
            if 'ciudad' in data:
                cliente.ciudad = _opt(data.get('ciudad'))
            if 'observaciones' in data:
                cliente.observaciones = _opt(data.get('observaciones'))
            if 'genero' in data:
                cliente.genero = _opt(data.get('genero'))

            tipo_in = data.get('tipo_cliente') or data.get('tipo')
            if tipo_in:
                cliente.tipo_cliente = _normalizar_tipo_cliente(tipo_in)

            if 'fecha_nacimiento' in data:
                cliente.fecha_nacimiento = _parse_fecha(data.get('fecha_nacimiento'))

            cliente.empresa = empresa
            cliente.updated_by = request.user

            cliente.full_clean()
            cliente.save()

            LogCliente.objects.create(
                cliente=cliente,
                usuario=request.user,
                accion='EDITAR',
                descripcion=f'Cliente "{cliente.nombre_completo}" modificado',
                datos_anteriores=datos_anteriores,
                datos_nuevos={
                    'nombre': cliente.nombre,
                    'apellido': cliente.apellido,
                    'rut': cliente.rut,
                    'tipo_cliente': cliente.tipo_cliente,
                    'empresa': empresa.nombre if empresa else None,
                    'activo': cliente.activo,
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )

            return JsonResponse({
                'success': True,
                'message': f'Cliente "{cliente.nombre_completo}" actualizado exitosamente',
                'cliente': {
                    'id': cliente.id,
                    'nombre': cliente.nombre,
                    'apellido': cliente.apellido,
                    'nombre_completo': cliente.nombre_completo,
                    'rut': cliente.rut,
                    'email': cliente.email,
                    'tipo_cliente': cliente.get_tipo_cliente_display(),
                    'empresa': empresa.nombre if empresa else None,
                    'activo': cliente.activo,
                }
            })

    except ValidationError as e:
        return JsonResponse({
            'success': False,
            'message': 'Error de validación',
            'errors': _errores_validation(e),
        }, status=400)

    except Exception as e:
        logger.exception('Error inesperado al editar cliente')
        return JsonResponse({
            'success': False,
            'message': f'Error al actualizar cliente: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def eliminar_cliente(request, cliente_id):
    """Eliminar cliente via AJAX"""
    
    try:
        cliente = get_object_or_404(Cliente, id=cliente_id)
        
        with transaction.atomic():
            nombre_cliente = cliente.nombre_completo
            
            # Crear log antes de eliminar
            LogCliente.objects.create(
                cliente=cliente,
                usuario=request.user,
                accion='ELIMINAR',
                descripcion=f'Cliente "{nombre_cliente}" eliminado',
                datos_anteriores={
                    'nombre': cliente.nombre,
                    'apellido': cliente.apellido,
                    'rut': cliente.rut,
                    'tipo_cliente': cliente.tipo_cliente,
                    'empresa': cliente.empresa.nombre if cliente.empresa else None,
                },
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            cliente.delete()
            
            return JsonResponse({
                'success': True,
                'message': f'Cliente "{nombre_cliente}" eliminado exitosamente'
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al eliminar cliente: {str(e)}'
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def activar_desactivar_cliente(request, cliente_id):
    """Activar o desactivar cliente via AJAX"""
    
    try:
        cliente = get_object_or_404(Cliente, id=cliente_id)
        
        with transaction.atomic():
            cliente.activo = not cliente.activo
            cliente.updated_by = request.user
            cliente.save()
            
            accion = 'ACTIVAR' if cliente.activo else 'DESACTIVAR'
            estado = 'activado' if cliente.activo else 'desactivado'
            
            # Crear log
            LogCliente.objects.create(
                cliente=cliente,
                usuario=request.user,
                accion=accion,
                descripcion=f'Cliente "{cliente.nombre_completo}" {estado}',
                datos_nuevos={'activo': cliente.activo},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Cliente "{cliente.nombre_completo}" {estado} exitosamente',
                'activo': cliente.activo
            })
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error al cambiar estado de cliente: {str(e)}'
        }, status=500)

@login_required
def detalle_cliente(request, cliente_id):
    """Vista detallada de cliente"""
    
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    # Obtener logs recientes
    logs = cliente.logs.all()[:10]
    
    context = {
        'cliente': cliente,
        'logs': logs,
    }
    
    return render(request, 'empresa_management/detalle_cliente.html', context)

@login_required
def exportar_clientes(request):
    """Exportar clientes a CSV"""
    
    # Obtener parámetros de filtro
    search = request.GET.get('search', '')
    tipo_cliente = request.GET.get('tipo_cliente', '')
    empresa_id = request.GET.get('empresa', '')
    activo = request.GET.get('activo', '')
    
    # Query base
    clientes = Cliente.objects.select_related('empresa').all()
    
    # Aplicar filtros
    if search:
        clientes = clientes.filter(
            Q(nombre__icontains=search) |
            Q(apellido__icontains=search) |
            Q(rut__icontains=search) |
            Q(email__icontains=search)
        )
    
    if tipo_cliente:
        clientes = clientes.filter(tipo_cliente=tipo_cliente)
    
    if empresa_id:
        clientes = clientes.filter(empresa_id=empresa_id)
    
    if activo != '':
        clientes = clientes.filter(activo=activo == 'true')
    
    # Crear respuesta CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="clientes_{timezone.localtime().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    writer = csv.writer(response)
    
    # Encabezados
    writer.writerow([
        'ID', 'Nombre', 'Apellido', 'RUT', 'Email', 'Teléfono', 'Celular',
        'Dirección', 'Comuna', 'Ciudad', 'Fecha Nacimiento', 'Género',
        'Tipo Cliente', 'Empresa Asociada', 'Fecha Creación', 'Activo', 'Observaciones'
    ])
    
    # Datos
    for cliente in clientes:
        writer.writerow([
            cliente.id,
            cliente.nombre,
            cliente.apellido,
            cliente.rut,
            cliente.email,
            cliente.telefono,
            cliente.celular,
            cliente.direccion,
            cliente.comuna,
            cliente.ciudad,
            cliente.fecha_nacimiento.strftime('%d/%m/%Y') if cliente.fecha_nacimiento else '',
            cliente.get_genero_display() if cliente.genero else '',
            cliente.get_tipo_cliente_display(),
            cliente.empresa.nombre if cliente.empresa else '',
            cliente.created_at.strftime('%d/%m/%Y %H:%M'),
            'Sí' if cliente.activo else 'No',
            cliente.observaciones
        ])
    
    return response

@login_required
def dashboard_clientes(request):
    """Dashboard con estadísticas de clientes"""
    
    # Estadísticas generales
    total_clientes = Cliente.objects.count()
    clientes_activos = Cliente.objects.filter(activo=True).count()
    clientes_inactivos = Cliente.objects.filter(activo=False).count()
    
    # Por tipo de cliente
    por_tipo = Cliente.objects.values('tipo_cliente').annotate(
        count=Count('id')
    ).order_by('tipo_cliente')
    
    # Clientes creados en los últimos 30 días
    fecha_limite = timezone.localdate() - timedelta(days=30)
    clientes_recientes = Cliente.objects.filter(
        created_at__date__gte=fecha_limite
    ).count()
    
    # Top 5 empresas con más clientes
    top_empresas_clientes = Empresa.objects.annotate(
        num_clientes=Count('clientes_crm')
    ).filter(num_clientes__gt=0).order_by('-num_clientes')[:5]
    
    # Clientes sin empresa asociada
    clientes_sin_empresa = Cliente.objects.filter(empresa__isnull=True).count()
    
    # Distribución por género
    por_genero = Cliente.objects.values('genero').annotate(
        count=Count('id')
    ).exclude(genero='').order_by('genero')
    
    # Clientes sin email
    clientes_sin_email = Cliente.objects.filter(email='').count()
    
    # Clientes sin teléfono
    clientes_sin_telefono = Cliente.objects.filter(
        Q(telefono='') & Q(celular='')
    ).count()
    
    context = {
        'total_clientes': total_clientes,
        'clientes_activos': clientes_activos,
        'clientes_inactivos': clientes_inactivos,
        'por_tipo': por_tipo,
        'clientes_recientes': clientes_recientes,
        'top_empresas_clientes': top_empresas_clientes,
        'clientes_sin_empresa': clientes_sin_empresa,
        'por_genero': por_genero,
        'clientes_sin_email': clientes_sin_email,
        'clientes_sin_telefono': clientes_sin_telefono,
    }
    
    return render(request, 'empresa_management/dashboard_clientes.html', context)

@login_required
def reporte_clientes_empresa(request, empresa_id):
    """Reporte detallado de clientes por empresa"""
    
    empresa = get_object_or_404(Empresa, id=empresa_id)
    clientes = empresa.clientes_crm.all()
    
    # Estadísticas
    total_clientes = clientes.count()
    clientes_activos = clientes.filter(activo=True).count()
    clientes_inactivos = clientes.filter(activo=False).count()
    
    # Por tipo de cliente
    por_tipo = clientes.values('tipo_cliente').annotate(
        count=Count('id')
    ).order_by('tipo_cliente')
    
    # Por género
    por_genero = clientes.values('genero').annotate(
        count=Count('id')
    ).exclude(genero='').order_by('genero')
    
    # Clientes recientes (últimos 30 días)
    fecha_limite = timezone.localdate() - timedelta(days=30)
    clientes_recientes = clientes.filter(
        created_at__date__gte=fecha_limite
    ).count()
    
    # Clientes sin información de contacto
    clientes_sin_email = clientes.filter(email='').count()
    clientes_sin_telefono = clientes.filter(
        Q(telefono='') & Q(celular='')
    ).count()
    
    context = {
        'empresa': empresa,
        'clientes': clientes,
        'total_clientes': total_clientes,
        'clientes_activos': clientes_activos,
        'clientes_inactivos': clientes_inactivos,
        'por_tipo': por_tipo,
        'por_genero': por_genero,
        'clientes_recientes': clientes_recientes,
        'clientes_sin_email': clientes_sin_email,
        'clientes_sin_telefono': clientes_sin_telefono,
    }
    
    return render(request, 'empresa_management/reporte_clientes_empresa.html', context)

@login_required
def buscar_clientes_ajax(request):
    """Búsqueda de clientes para autocompletado"""
    
    search = request.GET.get('q', '')
    empresa_id = request.GET.get('empresa_id', '')
    
    if len(search) < 2:
        return JsonResponse({'results': []})
    
    clientes = Cliente.objects.filter(activo=True)
    
    if empresa_id:
        clientes = clientes.filter(empresa_id=empresa_id)
    
    clientes = clientes.filter(
        Q(nombre__icontains=search) |
        Q(apellido__icontains=search) |
        Q(rut__icontains=search) |
        Q(email__icontains=search)
    )[:10]
    
    results = []
    for cliente in clientes:
        results.append({
            'id': cliente.id,
            'text': f"{cliente.nombre_completo} ({cliente.rut or 'Sin RUT'})",
            'nombre': cliente.nombre,
            'apellido': cliente.apellido,
            'rut': cliente.rut,
            'email': cliente.email,
            'telefono': cliente.telefono,
            'empresa': cliente.empresa.nombre if cliente.empresa else None,
        })
    
    return JsonResponse({'results': results}) 

@login_required
def obtener_cliente(request, cliente_id):
    """Obtener información de un cliente específico por su ID"""
    try:
        cliente = get_object_or_404(Cliente, id=cliente_id)
        
        return JsonResponse({
            'success': True,
            'cliente': {
                'id': cliente.id,
                'nombre': f"{cliente.nombre} {cliente.apellido}".strip(),
                'rut': cliente.rut or '',
                'tipo': cliente.tipo_cliente,
                'get_tipo_display': cliente.get_tipo_cliente_display() if hasattr(cliente, 'get_tipo_cliente_display') else cliente.tipo_cliente,
                'email': cliente.email or '',
                'telefono': cliente.telefono or cliente.celular or '',
                'direccion': cliente.direccion or '',
                'ciudad': cliente.ciudad or '',
                'region': '',  # El modelo no tiene región, usar comuna
                'codigo_postal': '',  # El modelo no tiene código postal
                'empresa': cliente.empresa.nombre if cliente.empresa else '',
                'cargo': '',  # El modelo no tiene cargo
                'categoria': '',  # El modelo no tiene categoría de cliente
                'limite_credito': 0,  # El modelo no tiene límite de crédito directo
                'activo': cliente.activo,
                'fecha_creacion': cliente.created_at.strftime('%Y-%m-%d %H:%M') if cliente.created_at else None,
                'fecha_actualizacion': cliente.updated_at.strftime('%Y-%m-%d %H:%M') if hasattr(cliente, 'updated_at') and cliente.updated_at else None,
                'total_compras': 0,
                'total_ventas': 0,
                # Campos adicionales del modelo Cliente
                'apellido': cliente.apellido or '',
                'nombre_solo': cliente.nombre or '',
                'celular': cliente.celular or '',
                'comuna': cliente.comuna or '',
                'fecha_nacimiento': cliente.fecha_nacimiento.strftime('%Y-%m-%d') if cliente.fecha_nacimiento else '',
                'genero': cliente.genero or '',
                'tipo_cliente': cliente.tipo_cliente,
                'observaciones': cliente.observaciones or '',
                'empresa_id': cliente.empresa.id if cliente.empresa else None,
            }
        })
    except Cliente.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cliente no encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500) 