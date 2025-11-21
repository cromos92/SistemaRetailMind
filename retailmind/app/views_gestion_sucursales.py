"""
Vistas para la gestión de sucursales
Permite crear, editar, listar y eliminar sucursales
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib import messages

from .models import Sucursal, Empresa, EmpresaUser
import logging

logger = logging.getLogger(__name__)


@login_required
def gestion_sucursales(request):
    """Vista principal para gestión de sucursales"""
    try:
        # Obtener empresa del usuario actual
        empresa_user = EmpresaUser.objects.select_related('empresa').filter(
            user=request.user
        ).first()
        
        if not empresa_user:
            messages.error(request, 'No tienes una empresa asignada')
            return redirect('verHome')
        
        empresa = empresa_user.empresa
        
        context = {
            'empresa': empresa,
            'titulo': 'Gestión de Sucursales',
            'puede_crear': True,  # Aquí podrías agregar permisos específicos
        }
        
        return render(request, 'vistas/modulo_configuracion/gestion_sucursales.html', context)
        
    except Exception as e:
        logger.error(f"Error en gestion_sucursales: {str(e)}")
        messages.error(request, f'Error al cargar la gestión de sucursales: {str(e)}')
        return redirect('verHome')


@require_GET
@login_required
def listar_sucursales_tabla(request):
    """API para listar sucursales con paginación y búsqueda"""
    try:
        # Obtener empresa del usuario
        empresa_user = EmpresaUser.objects.select_related('empresa').filter(
            user=request.user
        ).first()
        
        if not empresa_user:
            return JsonResponse({
                'success': False,
                'error': 'No tienes una empresa asignada'
            })
        
        # Obtener parámetros de búsqueda
        search = request.GET.get('search', '').strip()
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))
        
        # Construir query
        sucursales = Sucursal.objects.filter(
            empresa=empresa_user.empresa
        ).select_related('empresa')
        
        # Aplicar búsqueda (solo alias y dirección)
        if search:
            sucursales = sucursales.filter(
                Q(alias__icontains=search) | Q(direccion__icontains=search)
            )
        
        # Ordenar por alias
        sucursales = sucursales.order_by('alias')
        
        # Paginar
        paginator = Paginator(sucursales, page_size)
        page_obj = paginator.get_page(page)
        
        # Preparar datos (solo campos básicos)
        data = []
        for sucursal in page_obj:
            data.append({
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
                'empresa_nombre': sucursal.empresa.nombre if sucursal.empresa else 'N/A',
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': data,
            'pagination': {
                'current_page': page_obj.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'page_size': page_size,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous(),
            }
        })
        
    except Exception as e:
        logger.error(f"Error en listar_sucursales_tabla: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


@require_POST
@login_required
def crear_sucursal(request):
    """Crear una nueva sucursal"""
    try:
        # Obtener empresa del usuario
        empresa_user = EmpresaUser.objects.select_related('empresa').filter(
            user=request.user
        ).first()
        
        if not empresa_user:
            return JsonResponse({
                'success': False,
                'error': 'No tienes una empresa asignada'
            })
        
        # Obtener datos del formulario
        data = request.POST
        
        # Validar campos obligatorios
        if not data.get('alias'):
            return JsonResponse({
                'success': False,
                'error': 'El alias es obligatorio'
            })
        
        if not data.get('direccion'):
            return JsonResponse({
                'success': False,
                'error': 'La dirección es obligatoria'
            })
        
        # Verificar si ya existe una sucursal con ese alias
        if Sucursal.objects.filter(
            empresa=empresa_user.empresa,
            alias=data.get('alias')
        ).exists():
            return JsonResponse({
                'success': False,
                'error': f'Ya existe una sucursal con el alias "{data.get("alias")}"'
            })
        
        with transaction.atomic():
            # Crear sucursal
            sucursal = Sucursal.objects.create(
                empresa=empresa_user.empresa,
                alias=data.get('alias'),
                direccion=data.get('direccion'),
            )
            
            logger.info(f"Sucursal creada: {sucursal.alias} (ID: {sucursal.id}) por usuario {request.user.username}")
            
            return JsonResponse({
                'success': True,
                'message': f'Sucursal "{sucursal.alias}" creada exitosamente',
                'sucursal': {
                    'id': sucursal.id,
                    'alias': sucursal.alias,
                    'direccion': sucursal.direccion,
                }
            })
            
    except Exception as e:
        logger.error(f"Error en crear_sucursal: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


@require_GET
@login_required
def obtener_sucursal(request, sucursal_id):
    """Obtener detalles de una sucursal específica"""
    try:
        # Obtener empresa del usuario
        empresa_user = EmpresaUser.objects.select_related('empresa').filter(
            user=request.user
        ).first()
        
        if not empresa_user:
            return JsonResponse({
                'success': False,
                'error': 'No tienes una empresa asignada'
            })
        
        # Obtener sucursal
        sucursal = get_object_or_404(
            Sucursal,
            id=sucursal_id,
            empresa=empresa_user.empresa
        )
        
        return JsonResponse({
            'success': True,
            'sucursal': {
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
            }
        })
        
    except Exception as e:
        logger.error(f"Error en obtener_sucursal: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


@require_POST
@login_required
def editar_sucursal(request, sucursal_id):
    """Editar una sucursal existente"""
    try:
        # Obtener empresa del usuario
        empresa_user = EmpresaUser.objects.select_related('empresa').filter(
            user=request.user
        ).first()
        
        if not empresa_user:
            return JsonResponse({
                'success': False,
                'error': 'No tienes una empresa asignada'
            })
        
        # Obtener sucursal
        sucursal = get_object_or_404(
            Sucursal,
            id=sucursal_id,
            empresa=empresa_user.empresa
        )
        
        # Obtener datos del formulario
        data = request.POST
        
        # Validar campos obligatorios
        if not data.get('alias'):
            return JsonResponse({
                'success': False,
                'error': 'El alias es obligatorio'
            })
        
        if not data.get('direccion'):
            return JsonResponse({
                'success': False,
                'error': 'La dirección es obligatoria'
            })
        
        # Verificar si el alias ya existe en otra sucursal
        if Sucursal.objects.filter(
            empresa=empresa_user.empresa,
            alias=data.get('alias')
        ).exclude(id=sucursal_id).exists():
            return JsonResponse({
                'success': False,
                'error': f'Ya existe otra sucursal con el alias "{data.get("alias")}"'
            })
        
        with transaction.atomic():
            # Actualizar sucursal
            sucursal.alias = data.get('alias')
            sucursal.direccion = data.get('direccion')
            sucursal.save()
            
            logger.info(f"Sucursal actualizada: {sucursal.alias} (ID: {sucursal.id}) por usuario {request.user.username}")
            
            return JsonResponse({
                'success': True,
                'message': f'Sucursal "{sucursal.alias}" actualizada exitosamente',
                'sucursal': {
                    'id': sucursal.id,
                    'alias': sucursal.alias,
                    'direccion': sucursal.direccion,
                }
            })
            
    except Exception as e:
        logger.error(f"Error en editar_sucursal: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


@require_POST
@login_required
def eliminar_sucursal(request, sucursal_id):
    """Eliminar (desactivar) una sucursal"""
    try:
        # Obtener empresa del usuario
        empresa_user = EmpresaUser.objects.select_related('empresa').filter(
            user=request.user
        ).first()
        
        if not empresa_user:
            return JsonResponse({
                'success': False,
                'error': 'No tienes una empresa asignada'
            })
        
        # Obtener sucursal
        sucursal = get_object_or_404(
            Sucursal,
            id=sucursal_id,
            empresa=empresa_user.empresa
        )
        
        # Verificar si la sucursal tiene datos relacionados importantes
        # (Esto es para decidir si se desactiva o se elimina físicamente)
        tiene_productos = sucursal.productos.exists() if hasattr(sucursal, 'productos') else False
        tiene_tickets = sucursal.tickets.exists() if hasattr(sucursal, 'tickets') else False
        tiene_correlat = sucursal.correlativo_set.exists()
        
        with transaction.atomic():
            if tiene_productos or tiene_tickets or tiene_correlat:
                # Si tiene datos relacionados, no se puede eliminar
                return JsonResponse({
                    'success': False,
                    'error': f'No se puede eliminar la sucursal "{sucursal.alias}" porque tiene datos relacionados (productos, tickets o correlativos)'
                })
            else:
                # Si no tiene datos relacionados, se puede eliminar físicamente
                alias = sucursal.alias
                sucursal.delete()
                mensaje = f'Sucursal "{alias}" eliminada exitosamente'
                logger.info(f"Sucursal eliminada: {alias} (ID: {sucursal_id}) por usuario {request.user.username}")
            
            return JsonResponse({
                'success': True,
                'message': mensaje
            })
            
    except Exception as e:
        logger.error(f"Error en eliminar_sucursal: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        })


