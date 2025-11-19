"""
Vistas para el módulo de gestión de permisos
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Count, Q
from django.views.decorators.http import require_http_methods
from .models import ModuloSistema, OpcionMenu, PermisoRol, ConfiguracionPermisoGlobal
from users.models import Usuario
from .decorators import solo_administrador
import json


@login_required
@solo_administrador
def gestion_permisos(request):
    """
    Vista principal de gestión de permisos
    Muestra una interfaz para administrar permisos por rol
    """
    # Obtener todos los módulos con sus opciones
    modulos = ModuloSistema.objects.filter(activo=True).prefetch_related('opciones').order_by('orden')
    
    # Obtener roles disponibles
    roles = PermisoRol.ROLES_CHOICES
    
    # Obtener estadísticas
    total_modulos = modulos.count()
    total_opciones = OpcionMenu.objects.filter(activo=True).count()
    total_permisos = PermisoRol.objects.count()
    
    context = {
        'modulos': modulos,
        'roles': roles,
        'total_modulos': total_modulos,
        'total_opciones': total_opciones,
        'total_permisos': total_permisos,
    }
    
    return render(request, 'gestion_permisos/index.html', context)


@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_permisos_rol(request):
    """
    API para obtener todos los permisos de un rol específico
    """
    rol = request.GET.get('rol')
    
    if not rol:
        return JsonResponse({'error': 'Rol no especificado'}, status=400)
    
    # Obtener el límite de descuento para el rol
    permiso_rol = PermisoRol.objects.filter(rol=rol).first()
    limite_descuento = float(permiso_rol.limite_descuento_porcentaje) if permiso_rol else 0
    
    # Obtener todos los módulos con sus opciones
    modulos_data = []
    modulos = ModuloSistema.objects.filter(activo=True).prefetch_related('opciones').order_by('orden')
    
    for modulo in modulos:
        opciones_data = []
        opciones = modulo.opciones.filter(activo=True, padre__isnull=True).order_by('orden')
        
        for opcion in opciones:
            # Buscar permisos existentes
            permiso = PermisoRol.objects.filter(rol=rol, opcion_menu=opcion).first()
            
            opcion_info = {
                'id': opcion.id,
                'codigo': opcion.codigo,
                'nombre': opcion.nombre,
                'url_name': opcion.url_name,
                'url_path': opcion.url_path,
                'icono': opcion.icono,
                'es_submenu': opcion.es_submenu,
                'permisos': {
                    'puede_ver': permiso.puede_ver if permiso else False,
                    'puede_crear': permiso.puede_crear if permiso else False,
                    'puede_editar': permiso.puede_editar if permiso else False,
                    'puede_eliminar': permiso.puede_eliminar if permiso else False,
                    'puede_exportar': permiso.puede_exportar if permiso else False,
                    'puede_aprobar': permiso.puede_aprobar if permiso else False,
                }
            }
            
            # Si tiene subopciones, incluirlas
            if opcion.es_submenu:
                subopciones_data = []
                subopciones = opcion.hijos.filter(activo=True).order_by('orden')
                
                for subopcion in subopciones:
                    permiso_sub = PermisoRol.objects.filter(rol=rol, opcion_menu=subopcion).first()
                    subopciones_data.append({
                        'id': subopcion.id,
                        'codigo': subopcion.codigo,
                        'nombre': subopcion.nombre,
                        'url_name': subopcion.url_name,
                        'url_path': subopcion.url_path,
                        'icono': subopcion.icono,
                        'permisos': {
                            'puede_ver': permiso_sub.puede_ver if permiso_sub else False,
                            'puede_crear': permiso_sub.puede_crear if permiso_sub else False,
                            'puede_editar': permiso_sub.puede_editar if permiso_sub else False,
                            'puede_eliminar': permiso_sub.puede_eliminar if permiso_sub else False,
                            'puede_exportar': permiso_sub.puede_exportar if permiso_sub else False,
                            'puede_aprobar': permiso_sub.puede_aprobar if permiso_sub else False,
                        }
                    })
                
                opcion_info['subopciones'] = subopciones_data
            
            opciones_data.append(opcion_info)
        
        modulos_data.append({
            'id': modulo.id,
            'codigo': modulo.codigo,
            'nombre': modulo.nombre,
            'descripcion': modulo.descripcion,
            'icono': modulo.icono,
            'opciones': opciones_data
        })
    
    return JsonResponse({
        'success': True,
        'rol': rol,
        'limite_descuento': limite_descuento,
        'modulos': modulos_data
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permiso(request):
    """
    API para guardar o actualizar un permiso específico
    """
    try:
        data = json.loads(request.body)
        
        rol = data.get('rol')
        opcion_id = data.get('opcion_id')
        tipo_permiso = data.get('tipo_permiso')
        valor = data.get('valor', False)
        
        if not all([rol, opcion_id, tipo_permiso]):
            return JsonResponse({
                'error': True,
                'mensaje': 'Faltan parámetros requeridos'
            }, status=400)
        
        # Obtener la opción del menú
        opcion = get_object_or_404(OpcionMenu, id=opcion_id)
        
        # Obtener o crear el permiso
        permiso, created = PermisoRol.objects.get_or_create(
            rol=rol,
            opcion_menu=opcion,
            defaults={tipo_permiso: valor}
        )
        
        # Si ya existía, actualizar
        if not created:
            setattr(permiso, tipo_permiso, valor)
            permiso.save()
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Permiso {"creado" if created else "actualizado"} correctamente',
            'permiso': {
                'rol': permiso.rol,
                'opcion': permiso.opcion_menu.nombre,
                tipo_permiso: valor
            }
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': True,
            'mensaje': 'Error en el formato de los datos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al guardar permiso: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permisos_masivos(request):
    """
    API para guardar múltiples permisos de una vez
    """
    try:
        data = json.loads(request.body)
        
        rol = data.get('rol')
        permisos_data = data.get('permisos', [])
        limite_descuento = data.get('limite_descuento')
        
        if not rol or not permisos_data:
            return JsonResponse({
                'error': True,
                'mensaje': 'Rol o permisos no especificados'
            }, status=400)
        
        # Validar límite de descuento
        if limite_descuento is not None:
            try:
                limite_descuento = float(limite_descuento)
                if limite_descuento < 0 or limite_descuento > 100:
                    return JsonResponse({
                        'error': True,
                        'mensaje': 'El límite de descuento debe estar entre 0 y 100'
                    }, status=400)
            except (ValueError, TypeError):
                return JsonResponse({
                    'error': True,
                    'mensaje': 'El límite de descuento debe ser un número válido'
                }, status=400)
        
        permisos_actualizados = 0
        permisos_creados = 0
        
        for permiso_item in permisos_data:
            opcion_id = permiso_item.get('opcion_id')
            permisos_valores = permiso_item.get('permisos', {})
            
            opcion = OpcionMenu.objects.filter(id=opcion_id).first()
            if not opcion:
                continue
            
            permiso, created = PermisoRol.objects.get_or_create(
                rol=rol,
                opcion_menu=opcion
            )
            
            # Actualizar todos los permisos
            permiso.puede_ver = permisos_valores.get('puede_ver', False)
            permiso.puede_crear = permisos_valores.get('puede_crear', False)
            permiso.puede_editar = permisos_valores.get('puede_editar', False)
            permiso.puede_eliminar = permisos_valores.get('puede_eliminar', False)
            permiso.puede_exportar = permisos_valores.get('puede_exportar', False)
            permiso.puede_aprobar = permisos_valores.get('puede_aprobar', False)
            
            # Actualizar límite de descuento si se proporcionó
            if limite_descuento is not None:
                permiso.limite_descuento_porcentaje = limite_descuento
            
            permiso.save()
            
            if created:
                permisos_creados += 1
            else:
                permisos_actualizados += 1
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Permisos guardados correctamente',
            'creados': permisos_creados,
            'actualizados': permisos_actualizados
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': True,
            'mensaje': 'Error en el formato de los datos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al guardar permisos: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
@require_http_methods(["POST"])
def copiar_permisos_rol(request):
    """
    API para copiar todos los permisos de un rol a otro
    """
    try:
        data = json.loads(request.body)
        
        rol_origen = data.get('rol_origen')
        rol_destino = data.get('rol_destino')
        sobrescribir = data.get('sobrescribir', False)
        
        if not rol_origen or not rol_destino:
            return JsonResponse({
                'error': True,
                'mensaje': 'Roles origen y destino son requeridos'
            }, status=400)
        
        if rol_origen == rol_destino:
            return JsonResponse({
                'error': True,
                'mensaje': 'El rol origen y destino no pueden ser el mismo'
            }, status=400)
        
        # Obtener permisos del rol origen
        permisos_origen = PermisoRol.objects.filter(rol=rol_origen)
        
        # Obtener el límite de descuento del rol origen
        primer_permiso_origen = permisos_origen.first()
        limite_descuento_origen = primer_permiso_origen.limite_descuento_porcentaje if primer_permiso_origen else 0
        
        permisos_creados = 0
        permisos_actualizados = 0
        permisos_omitidos = 0
        
        for permiso_origen in permisos_origen:
            permiso_destino, created = PermisoRol.objects.get_or_create(
                rol=rol_destino,
                opcion_menu=permiso_origen.opcion_menu
            )
            
            if created or sobrescribir:
                permiso_destino.puede_ver = permiso_origen.puede_ver
                permiso_destino.puede_crear = permiso_origen.puede_crear
                permiso_destino.puede_editar = permiso_origen.puede_editar
                permiso_destino.puede_eliminar = permiso_origen.puede_eliminar
                permiso_destino.puede_exportar = permiso_origen.puede_exportar
                permiso_destino.puede_aprobar = permiso_origen.puede_aprobar
                permiso_destino.limite_descuento_porcentaje = limite_descuento_origen
                permiso_destino.save()
                
                if created:
                    permisos_creados += 1
                else:
                    permisos_actualizados += 1
            else:
                permisos_omitidos += 1
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Permisos copiados de {rol_origen} a {rol_destino}',
            'creados': permisos_creados,
            'actualizados': permisos_actualizados,
            'omitidos': permisos_omitidos
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': True,
            'mensaje': 'Error en el formato de los datos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al copiar permisos: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
def gestionar_modulos_opciones(request):
    """
    Vista para gestionar módulos y opciones del sistema
    """
    modulos = ModuloSistema.objects.all().order_by('orden')
    opciones = OpcionMenu.objects.all().select_related('modulo', 'padre').order_by('modulo__orden', 'orden')
    
    context = {
        'modulos': modulos,
        'opciones': opciones,
    }
    
    return render(request, 'gestion_permisos/modulos_opciones.html', context)


@login_required
@solo_administrador
def estadisticas_permisos(request):
    """
    Vista de estadísticas y análisis de permisos
    """
    # Obtener estadísticas por rol
    estadisticas_roles = []
    
    for rol_codigo, rol_nombre in PermisoRol.ROLES_CHOICES:
        total_opciones = OpcionMenu.objects.filter(activo=True).count()
        permisos_rol = PermisoRol.objects.filter(rol=rol_codigo)
        
        estadisticas_roles.append({
            'codigo': rol_codigo,
            'nombre': rol_nombre,
            'total_permisos': permisos_rol.count(),
            'puede_ver': permisos_rol.filter(puede_ver=True).count(),
            'puede_crear': permisos_rol.filter(puede_crear=True).count(),
            'puede_editar': permisos_rol.filter(puede_editar=True).count(),
            'puede_eliminar': permisos_rol.filter(puede_eliminar=True).count(),
            'puede_exportar': permisos_rol.filter(puede_exportar=True).count(),
            'puede_aprobar': permisos_rol.filter(puede_aprobar=True).count(),
            'total_opciones': total_opciones,
            'cobertura': round((permisos_rol.count() / total_opciones * 100) if total_opciones > 0 else 0, 1)
        })
    
    # Usuarios por rol
    usuarios_por_rol = []
    for rol_codigo, rol_nombre in PermisoRol.ROLES_CHOICES:
        count = Usuario.objects.filter(rol=rol_codigo, es_activo=True).count()
        usuarios_por_rol.append({
            'codigo': rol_codigo,
            'nombre': rol_nombre,
            'total': count
        })
    
    context = {
        'estadisticas_roles': estadisticas_roles,
        'usuarios_por_rol': usuarios_por_rol,
    }
    
    return render(request, 'gestion_permisos/estadisticas.html', context)

