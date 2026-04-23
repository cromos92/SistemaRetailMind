"""
Vistas para el módulo de gestión de permisos
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Q, Max
from django.views.decorators.http import require_http_methods
from .models import ModuloSistema, OpcionMenu, PermisoRol, ConfiguracionPermisoGlobal, PermisoSucursal, PermisoUsuario, Sucursal, EmpresaUser
from users.models import Usuario
from .decorators import solo_administrador
from .utils_permisos import (
    obtener_sucursales_usuario,
    obtener_configuracion_rango_arqueo,
    guardar_configuracion_rango_arqueo,
)
from decimal import Decimal, InvalidOperation
from datetime import datetime
from django.utils import timezone
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
    total_overrides = PermisoUsuario.objects.count()

    # Usuarios activos para el selector de permisos por usuario
    usuarios_activos = Usuario.objects.filter(
        es_activo=True
    ).order_by('first_name', 'last_name', 'username')

    context = {
        'modulos': modulos,
        'roles': roles,
        'total_modulos': total_modulos,
        'total_opciones': total_opciones,
        'total_permisos': total_permisos,
        'total_overrides': total_overrides,
        'usuarios_activos': usuarios_activos,
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
    
    # Obtener el límite de descuento máximo para el rol
    # Usamos Max para obtener el valor más alto guardado (todos deberían ser iguales, pero por consistencia)
    resultado = PermisoRol.objects.filter(rol=rol).aggregate(
        max_limite=Max('limite_descuento_porcentaje')
    )
    limite_descuento = float(resultado['max_limite']) if resultado['max_limite'] is not None else 0
    
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
        'configuracion_arqueo': obtener_configuracion_rango_arqueo(rol),
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
        configuracion_arqueo = data.get('configuracion_arqueo') or {}
        
        if not rol:
            return JsonResponse({
                'error': True,
                'mensaje': 'Rol no especificado'
            }, status=400)

        config_arqueo_guardada = None
        
        # Validar límite de descuento y convertir a Decimal
        if limite_descuento is not None:
            try:
                # Convertir a Decimal para consistencia con el modelo
                limite_descuento = Decimal(str(limite_descuento))
                if limite_descuento < 0 or limite_descuento > 100:
                    return JsonResponse({
                        'error': True,
                        'mensaje': 'El límite de descuento debe estar entre 0 y 100'
                    }, status=400)
            except (ValueError, TypeError, InvalidOperation):
                return JsonResponse({
                    'error': True,
                    'mensaje': 'El límite de descuento debe ser un número válido'
                }, status=400)
        
        permisos_actualizados = 0
        permisos_creados = 0
        limite_efectivo = limite_descuento if limite_descuento is not None else Decimal('0')
        
        # PRIMERO: Si se proporcionó límite de descuento, actualizar TODOS los permisos existentes del rol
        # Esto asegura que el límite se guarde incluso si hay permisos previos
        permisos_existentes_count = PermisoRol.objects.filter(rol=rol).update(
            limite_descuento_porcentaje=limite_efectivo
        )
        if permisos_existentes_count > 0:
            permisos_actualizados = permisos_existentes_count
        
        # SEGUNDO: Procesar los permisos individuales
        if permisos_data:
            for permiso_item in permisos_data:
                opcion_id = permiso_item.get('opcion_id')
                permisos_valores = permiso_item.get('permisos', {})
                
                opcion = OpcionMenu.objects.filter(id=opcion_id).first()
                if not opcion:
                    continue
                
                permiso, created = PermisoRol.objects.get_or_create(
                    rol=rol,
                    opcion_menu=opcion,
                    defaults={
                        'limite_descuento_porcentaje': limite_efectivo
                    }
                )
                
                # Actualizar todos los permisos booleanos
                permiso.puede_ver = permisos_valores.get('puede_ver', False)
                permiso.puede_crear = permisos_valores.get('puede_crear', False)
                permiso.puede_editar = permisos_valores.get('puede_editar', False)
                permiso.puede_eliminar = permisos_valores.get('puede_eliminar', False)
                permiso.puede_exportar = permisos_valores.get('puede_exportar', False)
                
                # Siempre actualizar límite de descuento
                permiso.limite_descuento_porcentaje = limite_efectivo
                
                permiso.save()
                
                if created:
                    permisos_creados += 1
        
        # TERCERO: Si no hay permisos para el rol, crear al menos uno para almacenar el límite de descuento
        permisos_rol_existentes = PermisoRol.objects.filter(rol=rol)
        if not permisos_rol_existentes.exists():
            # Si no hay permisos existentes, crear uno con la primera opción disponible
            primera_opcion = OpcionMenu.objects.filter(activo=True).first()
            if primera_opcion:
                PermisoRol.objects.create(
                    rol=rol,
                    opcion_menu=primera_opcion,
                    limite_descuento_porcentaje=limite_efectivo,
                    puede_ver=False,
                    puede_crear=False,
                    puede_editar=False,
                    puede_eliminar=False,
                    puede_exportar=False
                )
                permisos_creados += 1

        if configuracion_arqueo:
            try:
                config_arqueo_guardada = guardar_configuracion_rango_arqueo(
                    rol=rol,
                    tipo=configuracion_arqueo.get('tipo'),
                    valor=configuracion_arqueo.get('valor'),
                    usuario=request.user,
                )
            except ValueError as exc:
                return JsonResponse({
                    'error': True,
                    'mensaje': str(exc),
                }, status=400)
        else:
            config_arqueo_guardada = obtener_configuracion_rango_arqueo(rol)
        
        # Convertir Decimal a float para JSON
        limite_para_json = float(limite_efectivo)
        
        return JsonResponse({
            'success': True,
            'mensaje': (
                'Permisos guardados correctamente '
                f'(Límite descuento: {limite_para_json}% | '
                f'Rango arqueo: {config_arqueo_guardada["label"]})'
            ),
            'creados': permisos_creados,
            'actualizados': permisos_actualizados,
            'limite_descuento_guardado': limite_para_json,
            'configuracion_arqueo_guardada': config_arqueo_guardada,
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
        
        # Obtener el límite de descuento máximo del rol origen
        resultado = permisos_origen.aggregate(max_limite=Max('limite_descuento_porcentaje'))
        limite_descuento_origen = resultado['max_limite'] if resultado['max_limite'] is not None else 0
        
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
                permiso_destino.limite_descuento_porcentaje = limite_descuento_origen
                permiso_destino.save()
                
                if created:
                    permisos_creados += 1
                else:
                    permisos_actualizados += 1
            else:
                permisos_omitidos += 1
        
        guardar_configuracion_rango_arqueo(
            rol=rol_destino,
            tipo=obtener_configuracion_rango_arqueo(rol_origen)['tipo'],
            valor=obtener_configuracion_rango_arqueo(rol_origen)['valor'],
            usuario=request.user,
        )

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


# ========== PERMISOS POR SUCURSAL ==========

@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_sucursales_permisos(request):
    """
    API para obtener todas las sucursales disponibles para configurar permisos
    """
    try:
        sucursales = Sucursal.objects.all().order_by('alias')
        
        sucursales_data = []
        for suc in sucursales:
            # Contar cuántas opciones tiene configuradas
            permisos_count = PermisoSucursal.objects.filter(sucursal=suc).count()
            deshabilitados_count = PermisoSucursal.objects.filter(sucursal=suc, habilitado=False).count()
            
            sucursales_data.append({
                'id': suc.id,
                'alias': suc.alias,
                'direccion': suc.direccion,
                'tipo_sucursal': suc.tipo_sucursal if hasattr(suc, 'tipo_sucursal') else 'N/A',
                'tipo_sucursal_display': suc.get_tipo_sucursal_display() if hasattr(suc, 'tipo_sucursal') else 'N/A',
                'permisos_configurados': permisos_count,
                'opciones_deshabilitadas': deshabilitados_count,
            })
        
        return JsonResponse({
            'success': True,
            'sucursales': sucursales_data
        })
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al obtener sucursales: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_permisos_sucursal(request):
    """
    API para obtener todos los permisos de una sucursal específica
    """
    sucursal_id = request.GET.get('sucursal_id')
    
    if not sucursal_id:
        return JsonResponse({'error': 'Sucursal no especificada'}, status=400)
    
    try:
        sucursal = Sucursal.objects.get(id=sucursal_id)
    except Sucursal.DoesNotExist:
        return JsonResponse({'error': 'Sucursal no encontrada'}, status=404)
    
    # Obtener todos los módulos con sus opciones
    modulos_data = []
    modulos = ModuloSistema.objects.filter(activo=True).prefetch_related('opciones').order_by('orden')
    
    for modulo in modulos:
        opciones_data = []
        opciones = modulo.opciones.filter(activo=True, padre__isnull=True).order_by('orden')
        
        for opcion in opciones:
            # Buscar permisos existentes para la sucursal
            permiso = PermisoSucursal.objects.filter(sucursal=sucursal, opcion_menu=opcion).first()
            
            opcion_info = {
                'id': opcion.id,
                'codigo': opcion.codigo,
                'nombre': opcion.nombre,
                'url_name': opcion.url_name,
                'icono': opcion.icono,
                'es_submenu': opcion.es_submenu,
                'permisos': {
                    'habilitado': permiso.habilitado if permiso else True,
                    'puede_crear': permiso.puede_crear if permiso else True,
                    'puede_editar': permiso.puede_editar if permiso else True,
                    'puede_eliminar': permiso.puede_eliminar if permiso else False,
                    'puede_exportar': permiso.puede_exportar if permiso else True,
                },
                'notas': permiso.notas if permiso else ''
            }
            
            # Si tiene subopciones, incluirlas
            if opcion.es_submenu:
                subopciones_data = []
                subopciones = opcion.hijos.filter(activo=True).order_by('orden')
                
                for subopcion in subopciones:
                    permiso_sub = PermisoSucursal.objects.filter(sucursal=sucursal, opcion_menu=subopcion).first()
                    subopciones_data.append({
                        'id': subopcion.id,
                        'codigo': subopcion.codigo,
                        'nombre': subopcion.nombre,
                        'url_name': subopcion.url_name,
                        'icono': subopcion.icono,
                        'permisos': {
                            'habilitado': permiso_sub.habilitado if permiso_sub else True,
                            'puede_crear': permiso_sub.puede_crear if permiso_sub else True,
                            'puede_editar': permiso_sub.puede_editar if permiso_sub else True,
                            'puede_eliminar': permiso_sub.puede_eliminar if permiso_sub else False,
                            'puede_exportar': permiso_sub.puede_exportar if permiso_sub else True,
                        },
                        'notas': permiso_sub.notas if permiso_sub else ''
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
        'sucursal': {
            'id': sucursal.id,
            'alias': sucursal.alias,
            'direccion': sucursal.direccion,
            'tipo_sucursal': sucursal.tipo_sucursal if hasattr(sucursal, 'tipo_sucursal') else 'N/A',
        },
        'modulos': modulos_data
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permisos_sucursal(request):
    """
    API para guardar múltiples permisos de una sucursal de una vez
    """
    try:
        data = json.loads(request.body)
        
        sucursal_id = data.get('sucursal_id')
        permisos_data = data.get('permisos', [])
        
        if not sucursal_id:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursal no especificada'
            }, status=400)
        
        try:
            sucursal = Sucursal.objects.get(id=sucursal_id)
        except Sucursal.DoesNotExist:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursal no encontrada'
            }, status=404)
        
        permisos_actualizados = 0
        permisos_creados = 0
        
        for permiso_item in permisos_data:
            opcion_id = permiso_item.get('opcion_id')
            permisos_valores = permiso_item.get('permisos', {})
            notas = permiso_item.get('notas', '')
            
            opcion = OpcionMenu.objects.filter(id=opcion_id).first()
            if not opcion:
                continue
            
            permiso, created = PermisoSucursal.objects.get_or_create(
                sucursal=sucursal,
                opcion_menu=opcion
            )
            
            # Actualizar todos los permisos
            permiso.habilitado = permisos_valores.get('habilitado', True)
            permiso.puede_crear = permisos_valores.get('puede_crear', True)
            permiso.puede_editar = permisos_valores.get('puede_editar', True)
            permiso.puede_eliminar = permisos_valores.get('puede_eliminar', False)
            permiso.puede_exportar = permisos_valores.get('puede_exportar', True)
            permiso.notas = notas
            
            permiso.save()
            
            if created:
                permisos_creados += 1
            else:
                permisos_actualizados += 1
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Permisos guardados correctamente para {sucursal.alias}',
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
def copiar_permisos_sucursal(request):
    """
    API para copiar permisos de una sucursal a otra
    """
    try:
        data = json.loads(request.body)
        
        sucursal_origen_id = data.get('sucursal_origen_id')
        sucursal_destino_id = data.get('sucursal_destino_id')
        sobrescribir = data.get('sobrescribir', False)
        
        if not sucursal_origen_id or not sucursal_destino_id:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursales origen y destino son requeridas'
            }, status=400)
        
        if sucursal_origen_id == sucursal_destino_id:
            return JsonResponse({
                'error': True,
                'mensaje': 'La sucursal origen y destino no pueden ser la misma'
            }, status=400)
        
        try:
            sucursal_origen = Sucursal.objects.get(id=sucursal_origen_id)
            sucursal_destino = Sucursal.objects.get(id=sucursal_destino_id)
        except Sucursal.DoesNotExist:
            return JsonResponse({
                'error': True,
                'mensaje': 'Una de las sucursales no existe'
            }, status=404)
        
        # Obtener permisos de la sucursal origen
        permisos_origen = PermisoSucursal.objects.filter(sucursal=sucursal_origen)
        
        permisos_creados = 0
        permisos_actualizados = 0
        permisos_omitidos = 0
        
        for permiso_origen in permisos_origen:
            permiso_destino, created = PermisoSucursal.objects.get_or_create(
                sucursal=sucursal_destino,
                opcion_menu=permiso_origen.opcion_menu
            )
            
            if created or sobrescribir:
                permiso_destino.habilitado = permiso_origen.habilitado
                permiso_destino.puede_crear = permiso_origen.puede_crear
                permiso_destino.puede_editar = permiso_origen.puede_editar
                permiso_destino.puede_eliminar = permiso_origen.puede_eliminar
                permiso_destino.puede_exportar = permiso_origen.puede_exportar
                permiso_destino.notas = f"Copiado de {sucursal_origen.alias}"
                permiso_destino.save()
                
                if created:
                    permisos_creados += 1
                else:
                    permisos_actualizados += 1
            else:
                permisos_omitidos += 1
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Permisos copiados de {sucursal_origen.alias} a {sucursal_destino.alias}',
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
@require_http_methods(["POST"])
def aplicar_plantilla_tipo_sucursal(request):
    """
    API para aplicar una plantilla de permisos basada en el tipo de sucursal.
    Tipos: CENTRO_DISTRIBUCION, VENDEDORA, MIXTA
    """
    try:
        data = json.loads(request.body)
        
        sucursal_id = data.get('sucursal_id')
        tipo_plantilla = data.get('tipo_plantilla')  # 'CENTRO_DISTRIBUCION', 'VENDEDORA', 'MIXTA'
        
        if not sucursal_id or not tipo_plantilla:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursal y tipo de plantilla son requeridos'
            }, status=400)
        
        try:
            sucursal = Sucursal.objects.get(id=sucursal_id)
        except Sucursal.DoesNotExist:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursal no encontrada'
            }, status=404)
        
        # Definir plantillas de permisos por tipo de sucursal
        # Códigos de opciones que se restringen según el tipo
        PLANTILLAS = {
            'VENDEDORA': {
                # Sucursal vendedora: NO puede crear productos ni hacer compras
                'deshabilitar': [
                    'compras_gestion',
                    'compras_dte',
                    'compras_importacion',
                    'productos_crear',
                    'productos_importar',
                    'recepcion_dte',
                    'regularizar_recepciones',
                ],
                'solo_lectura': [
                    'productos_gestion',
                    'dashboard_compras',
                ],
            },
            'CENTRO_DISTRIBUCION': {
                # Centro de distribución: NO puede hacer ventas POS
                'deshabilitar': [
                    'pos_dashboard',
                    'ticket_venta',
                    'cuadratura_caja',
                    'ventas_documentos',
                    'cambios_devoluciones',
                ],
                'solo_lectura': [
                    'dashboard_ventas',
                ],
            },
            'MIXTA': {
                # Sucursal mixta: Todo habilitado
                'deshabilitar': [],
                'solo_lectura': [],
            },
        }
        
        plantilla = PLANTILLAS.get(tipo_plantilla)
        if not plantilla:
            return JsonResponse({
                'error': True,
                'mensaje': f'Tipo de plantilla no válido: {tipo_plantilla}'
            }, status=400)
        
        permisos_actualizados = 0
        
        # Primero, habilitar todas las opciones
        PermisoSucursal.objects.filter(sucursal=sucursal).update(
            habilitado=True,
            puede_crear=True,
            puede_editar=True,
            puede_exportar=True
        )
        
        # Aplicar deshabilitaciones
        for codigo in plantilla.get('deshabilitar', []):
            opcion = OpcionMenu.objects.filter(codigo=codigo, activo=True).first()
            if opcion:
                permiso, _ = PermisoSucursal.objects.get_or_create(
                    sucursal=sucursal,
                    opcion_menu=opcion
                )
                permiso.habilitado = False
                permiso.puede_crear = False
                permiso.puede_editar = False
                permiso.notas = f"Deshabilitado por plantilla {tipo_plantilla}"
                permiso.save()
                permisos_actualizados += 1
        
        # Aplicar solo lectura
        for codigo in plantilla.get('solo_lectura', []):
            opcion = OpcionMenu.objects.filter(codigo=codigo, activo=True).first()
            if opcion:
                permiso, _ = PermisoSucursal.objects.get_or_create(
                    sucursal=sucursal,
                    opcion_menu=opcion
                )
                permiso.habilitado = True
                permiso.puede_crear = False
                permiso.puede_editar = False
                permiso.puede_eliminar = False
                permiso.notas = f"Solo lectura por plantilla {tipo_plantilla}"
                permiso.save()
                permisos_actualizados += 1
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Plantilla "{tipo_plantilla}" aplicada a {sucursal.alias}',
            'permisos_actualizados': permisos_actualizados
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': True,
            'mensaje': 'Error en el formato de los datos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al aplicar plantilla: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
@require_http_methods(["POST"])
def restablecer_permisos_sucursal(request):
    """
    API para restablecer todos los permisos de una sucursal (eliminar restricciones)
    """
    try:
        data = json.loads(request.body)
        
        sucursal_id = data.get('sucursal_id')
        
        if not sucursal_id:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursal no especificada'
            }, status=400)
        
        try:
            sucursal = Sucursal.objects.get(id=sucursal_id)
        except Sucursal.DoesNotExist:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursal no encontrada'
            }, status=404)
        
        # Eliminar todos los permisos configurados para la sucursal
        count, _ = PermisoSucursal.objects.filter(sucursal=sucursal).delete()
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Permisos restablecidos para {sucursal.alias}. Se eliminaron {count} configuraciones.',
            'eliminados': count
        })
    
    except json.JSONDecodeError:
        return JsonResponse({
            'error': True,
            'mensaje': 'Error en el formato de los datos'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al restablecer permisos: {str(e)}'
        }, status=500)


# ========== EXPORTAR / IMPORTAR PERMISOS ==========

@login_required
@solo_administrador
@require_http_methods(["GET"])
def exportar_permisos_rol(request):
    """
    API para exportar todos los permisos de un rol en formato JSON
    """
    rol = request.GET.get('rol')
    
    if not rol:
        return JsonResponse({'error': 'Rol no especificado'}, status=400)
    
    try:
        # Obtener todos los permisos del rol
        permisos = PermisoRol.objects.filter(rol=rol).select_related('opcion_menu', 'opcion_menu__modulo')
        
        # Obtener el límite de descuento
        resultado = permisos.aggregate(max_limite=Max('limite_descuento_porcentaje'))
        limite_descuento = float(resultado['max_limite']) if resultado['max_limite'] is not None else 0
        
        # Construir la estructura de exportación
        permisos_data = []
        for permiso in permisos:
            permisos_data.append({
                'opcion_codigo': permiso.opcion_menu.codigo,
                'opcion_nombre': permiso.opcion_menu.nombre,
                'modulo_codigo': permiso.opcion_menu.modulo.codigo if permiso.opcion_menu.modulo else None,
                'permisos': {
                    'puede_ver': permiso.puede_ver,
                    'puede_crear': permiso.puede_crear,
                    'puede_editar': permiso.puede_editar,
                    'puede_eliminar': permiso.puede_eliminar,
                    'puede_exportar': permiso.puede_exportar,
                }
            })
        
        # Estructura del archivo de exportación
        export_data = {
            'version': '1.0',
            'tipo': 'permisos_rol',
            'fecha_exportacion': timezone.localtime().isoformat(),
            'rol': rol,
            'rol_nombre': dict(PermisoRol.ROLES_CHOICES).get(rol, rol),
            'limite_descuento': limite_descuento,
            'total_permisos': len(permisos_data),
            'permisos': permisos_data
        }
        
        # Crear respuesta como archivo descargable
        response = HttpResponse(
            json.dumps(export_data, indent=2, ensure_ascii=False),
            content_type='application/json'
        )
        filename = f'permisos_{rol}_{timezone.localtime().strftime("%Y%m%d_%H%M%S")}.json'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al exportar permisos: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
@require_http_methods(["GET"])
def exportar_todos_permisos(request):
    """
    API para exportar todos los permisos de todos los roles en formato JSON
    """
    try:
        roles_data = []
        
        for rol_codigo, rol_nombre in PermisoRol.ROLES_CHOICES:
            permisos = PermisoRol.objects.filter(rol=rol_codigo).select_related('opcion_menu', 'opcion_menu__modulo')
            
            # Obtener el límite de descuento del rol
            resultado = permisos.aggregate(max_limite=Max('limite_descuento_porcentaje'))
            limite_descuento = float(resultado['max_limite']) if resultado['max_limite'] is not None else 0
            
            permisos_data = []
            for permiso in permisos:
                permisos_data.append({
                    'opcion_codigo': permiso.opcion_menu.codigo,
                    'opcion_nombre': permiso.opcion_menu.nombre,
                    'modulo_codigo': permiso.opcion_menu.modulo.codigo if permiso.opcion_menu.modulo else None,
                    'permisos': {
                        'puede_ver': permiso.puede_ver,
                        'puede_crear': permiso.puede_crear,
                        'puede_editar': permiso.puede_editar,
                        'puede_eliminar': permiso.puede_eliminar,
                        'puede_exportar': permiso.puede_exportar,
                    }
                })
            
            roles_data.append({
                'rol': rol_codigo,
                'rol_nombre': rol_nombre,
                'limite_descuento': limite_descuento,
                'total_permisos': len(permisos_data),
                'permisos': permisos_data
            })
        
        # Estructura del archivo de exportación completo
        export_data = {
            'version': '1.0',
            'tipo': 'permisos_completos',
            'fecha_exportacion': timezone.localtime().isoformat(),
            'total_roles': len(roles_data),
            'roles': roles_data
        }
        
        # Crear respuesta como archivo descargable
        response = HttpResponse(
            json.dumps(export_data, indent=2, ensure_ascii=False),
            content_type='application/json'
        )
        filename = f'permisos_completos_{timezone.localtime().strftime("%Y%m%d_%H%M%S")}.json'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al exportar permisos: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
@require_http_methods(["POST"])
def importar_permisos(request):
    """
    API para importar permisos desde un archivo JSON
    Soporta importación de un solo rol o de todos los roles
    """
    try:
        # Verificar si hay archivo subido o datos JSON directos
        if request.FILES.get('archivo'):
            archivo = request.FILES['archivo']
            contenido = archivo.read().decode('utf-8')
            data = json.loads(contenido)
        else:
            data = json.loads(request.body)
        
        # Validar estructura del archivo
        version = data.get('version')
        tipo = data.get('tipo')
        
        if not version or not tipo:
            return JsonResponse({
                'error': True,
                'mensaje': 'Archivo de importación inválido: falta versión o tipo'
            }, status=400)
        
        sobrescribir = data.get('sobrescribir', True)
        resultados = {
            'roles_procesados': 0,
            'permisos_creados': 0,
            'permisos_actualizados': 0,
            'permisos_omitidos': 0,
            'errores': []
        }
        
        if tipo == 'permisos_rol':
            # Importar permisos de un solo rol
            rol = data.get('rol')
            rol_destino = request.POST.get('rol_destino') or data.get('rol_destino') or rol
            
            if not rol:
                return JsonResponse({
                    'error': True,
                    'mensaje': 'No se especificó el rol en el archivo'
                }, status=400)
            
            # Validar que el rol destino sea válido
            roles_validos = [r[0] for r in PermisoRol.ROLES_CHOICES]
            if rol_destino not in roles_validos:
                return JsonResponse({
                    'error': True,
                    'mensaje': f'Rol destino inválido: {rol_destino}'
                }, status=400)
            
            limite_descuento = Decimal(str(data.get('limite_descuento', 0)))
            permisos_data = data.get('permisos', [])
            
            resultado_rol = _importar_permisos_rol(rol_destino, permisos_data, limite_descuento, sobrescribir)
            resultados['roles_procesados'] = 1
            resultados['permisos_creados'] = resultado_rol['creados']
            resultados['permisos_actualizados'] = resultado_rol['actualizados']
            resultados['permisos_omitidos'] = resultado_rol['omitidos']
            resultados['errores'] = resultado_rol['errores']
            
        elif tipo == 'permisos_completos':
            # Importar permisos de todos los roles
            roles_data = data.get('roles', [])
            
            for rol_data in roles_data:
                rol = rol_data.get('rol')
                limite_descuento = Decimal(str(rol_data.get('limite_descuento', 0)))
                permisos_data = rol_data.get('permisos', [])
                
                resultado_rol = _importar_permisos_rol(rol, permisos_data, limite_descuento, sobrescribir)
                resultados['roles_procesados'] += 1
                resultados['permisos_creados'] += resultado_rol['creados']
                resultados['permisos_actualizados'] += resultado_rol['actualizados']
                resultados['permisos_omitidos'] += resultado_rol['omitidos']
                resultados['errores'].extend(resultado_rol['errores'])
        else:
            return JsonResponse({
                'error': True,
                'mensaje': f'Tipo de archivo no soportado: {tipo}'
            }, status=400)
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Importación completada: {resultados["roles_procesados"]} rol(es) procesado(s)',
            'resultados': resultados
        })
        
    except json.JSONDecodeError as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al leer el archivo JSON: {str(e)}'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al importar permisos: {str(e)}'
        }, status=500)


def _importar_permisos_rol(rol, permisos_data, limite_descuento, sobrescribir):
    """
    Función auxiliar para importar permisos de un rol específico
    """
    resultado = {
        'creados': 0,
        'actualizados': 0,
        'omitidos': 0,
        'errores': []
    }
    
    # Actualizar límite de descuento en permisos existentes si sobrescribir está activo
    if sobrescribir:
        PermisoRol.objects.filter(rol=rol).update(limite_descuento_porcentaje=limite_descuento)
    
    for permiso_item in permisos_data:
        opcion_codigo = permiso_item.get('opcion_codigo')
        permisos_valores = permiso_item.get('permisos', {})
        
        # Buscar la opción por código
        opcion = OpcionMenu.objects.filter(codigo=opcion_codigo, activo=True).first()
        
        if not opcion:
            resultado['errores'].append(f'Opción no encontrada: {opcion_codigo}')
            continue
        
        try:
            permiso, created = PermisoRol.objects.get_or_create(
                rol=rol,
                opcion_menu=opcion,
                defaults={
                    'limite_descuento_porcentaje': limite_descuento
                }
            )
            
            if created or sobrescribir:
                permiso.puede_ver = permisos_valores.get('puede_ver', False)
                permiso.puede_crear = permisos_valores.get('puede_crear', False)
                permiso.puede_editar = permisos_valores.get('puede_editar', False)
                permiso.puede_eliminar = permisos_valores.get('puede_eliminar', False)
                permiso.puede_exportar = permisos_valores.get('puede_exportar', False)
                permiso.limite_descuento_porcentaje = limite_descuento
                permiso.save()
                
                if created:
                    resultado['creados'] += 1
                else:
                    resultado['actualizados'] += 1
            else:
                resultado['omitidos'] += 1
                
        except Exception as e:
            resultado['errores'].append(f'Error en opción {opcion_codigo}: {str(e)}')
    
    return resultado


# ========== EXPORTAR / IMPORTAR PERMISOS SUCURSAL ==========

@login_required
@solo_administrador
@require_http_methods(["GET"])
def exportar_permisos_sucursal(request):
    """
    API para exportar permisos de una sucursal en formato JSON
    """
    sucursal_id = request.GET.get('sucursal_id')
    
    if not sucursal_id:
        return JsonResponse({'error': 'Sucursal no especificada'}, status=400)
    
    try:
        sucursal = Sucursal.objects.get(id=sucursal_id)
        permisos = PermisoSucursal.objects.filter(sucursal=sucursal).select_related('opcion_menu', 'opcion_menu__modulo')
        
        permisos_data = []
        for permiso in permisos:
            permisos_data.append({
                'opcion_codigo': permiso.opcion_menu.codigo,
                'opcion_nombre': permiso.opcion_menu.nombre,
                'modulo_codigo': permiso.opcion_menu.modulo.codigo if permiso.opcion_menu.modulo else None,
                'permisos': {
                    'habilitado': permiso.habilitado,
                    'puede_crear': permiso.puede_crear,
                    'puede_editar': permiso.puede_editar,
                    'puede_eliminar': permiso.puede_eliminar,
                    'puede_exportar': permiso.puede_exportar,
                },
                'notas': permiso.notas
            })
        
        export_data = {
            'version': '1.0',
            'tipo': 'permisos_sucursal',
            'fecha_exportacion': timezone.localtime().isoformat(),
            'sucursal': {
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
                'tipo_sucursal': sucursal.tipo_sucursal if hasattr(sucursal, 'tipo_sucursal') else None,
            },
            'total_permisos': len(permisos_data),
            'permisos': permisos_data
        }
        
        response = HttpResponse(
            json.dumps(export_data, indent=2, ensure_ascii=False),
            content_type='application/json'
        )
        filename = f'permisos_sucursal_{sucursal.alias.replace(" ", "_")}_{timezone.localtime().strftime("%Y%m%d_%H%M%S")}.json'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        
        return response
        
    except Sucursal.DoesNotExist:
        return JsonResponse({'error': 'Sucursal no encontrada'}, status=404)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al exportar permisos de sucursal: {str(e)}'
        }, status=500)


@login_required
@solo_administrador
@require_http_methods(["POST"])
def importar_permisos_sucursal(request):
    """
    API para importar permisos de sucursal desde un archivo JSON
    """
    try:
        sucursal_destino_id = request.POST.get('sucursal_id') or request.GET.get('sucursal_id')
        
        if not sucursal_destino_id:
            return JsonResponse({
                'error': True,
                'mensaje': 'Debe especificar la sucursal destino'
            }, status=400)
        
        try:
            sucursal_destino = Sucursal.objects.get(id=sucursal_destino_id)
        except Sucursal.DoesNotExist:
            return JsonResponse({
                'error': True,
                'mensaje': 'Sucursal destino no encontrada'
            }, status=404)
        
        # Leer archivo o datos JSON
        if request.FILES.get('archivo'):
            archivo = request.FILES['archivo']
            contenido = archivo.read().decode('utf-8')
            data = json.loads(contenido)
        else:
            data = json.loads(request.body)
        
        # Validar estructura
        if data.get('tipo') != 'permisos_sucursal':
            return JsonResponse({
                'error': True,
                'mensaje': 'Tipo de archivo no compatible con permisos de sucursal'
            }, status=400)
        
        sobrescribir = data.get('sobrescribir', True)
        permisos_data = data.get('permisos', [])
        
        resultados = {
            'creados': 0,
            'actualizados': 0,
            'omitidos': 0,
            'errores': []
        }
        
        for permiso_item in permisos_data:
            opcion_codigo = permiso_item.get('opcion_codigo')
            permisos_valores = permiso_item.get('permisos', {})
            notas = permiso_item.get('notas', '')
            
            opcion = OpcionMenu.objects.filter(codigo=opcion_codigo, activo=True).first()
            
            if not opcion:
                resultados['errores'].append(f'Opción no encontrada: {opcion_codigo}')
                continue
            
            try:
                permiso, created = PermisoSucursal.objects.get_or_create(
                    sucursal=sucursal_destino,
                    opcion_menu=opcion
                )
                
                if created or sobrescribir:
                    permiso.habilitado = permisos_valores.get('habilitado', True)
                    permiso.puede_crear = permisos_valores.get('puede_crear', True)
                    permiso.puede_editar = permisos_valores.get('puede_editar', True)
                    permiso.puede_eliminar = permisos_valores.get('puede_eliminar', False)
                    permiso.puede_exportar = permisos_valores.get('puede_exportar', True)
                    permiso.notas = f"Importado: {notas}" if notas else "Importado desde archivo"
                    permiso.save()
                    
                    if created:
                        resultados['creados'] += 1
                    else:
                        resultados['actualizados'] += 1
                else:
                    resultados['omitidos'] += 1
                    
            except Exception as e:
                resultados['errores'].append(f'Error en opción {opcion_codigo}: {str(e)}')
        
        return JsonResponse({
            'success': True,
            'mensaje': f'Permisos importados a {sucursal_destino.alias}',
            'resultados': resultados
        })

    except json.JSONDecodeError as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al leer el archivo JSON: {str(e)}'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'error': True,
            'mensaje': f'Error al importar permisos de sucursal: {str(e)}'
        }, status=500)


# ========== PERMISOS POR USUARIO ==========

@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_usuarios_permisos(request):
    """API para obtener usuarios activos con info de overrides"""
    try:
        usuarios = Usuario.objects.filter(es_activo=True).order_by('first_name', 'last_name')
        usuarios_data = []
        for u in usuarios:
            overrides_count = PermisoUsuario.objects.filter(usuario=u).count()
            sucursales_asignadas = EmpresaUser.objects.filter(
                user=u, status=True, sucursal__isnull=False
            ).select_related('sucursal').values_list('sucursal__alias', flat=True)

            usuarios_data.append({
                'id': u.id,
                'username': u.username,
                'nombre': u.get_full_name() or u.username,
                'rol': u.rol,
                'rol_display': u.get_rol_display() if hasattr(u, 'get_rol_display') else u.rol,
                'overrides': overrides_count,
                've_todas_sucursales': PermisoUsuario.usuario_ve_todas_sucursales(u),
                'sucursales_asignadas': list(sucursales_asignadas),
            })

        return JsonResponse({'success': True, 'usuarios': usuarios_data})
    except Exception as e:
        return JsonResponse({'error': True, 'mensaje': str(e)}, status=500)


@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_permisos_usuario(request):
    """API para obtener overrides de un usuario específico"""
    usuario_id = request.GET.get('usuario_id')
    if not usuario_id:
        return JsonResponse({'error': True, 'mensaje': 'usuario_id requerido'}, status=400)

    try:
        usuario = Usuario.objects.get(id=usuario_id)
    except Usuario.DoesNotExist:
        return JsonResponse({'error': True, 'mensaje': 'Usuario no encontrado'}, status=404)

    ve_todas = PermisoUsuario.usuario_ve_todas_sucursales(usuario)

    modulos_data = []
    modulos = ModuloSistema.objects.filter(activo=True).prefetch_related('opciones').order_by('orden')

    for modulo in modulos:
        opciones_data = []
        opciones = modulo.opciones.filter(activo=True, padre__isnull=True).order_by('orden')

        for opcion in opciones:
            override = PermisoUsuario.objects.filter(usuario=usuario, opcion_menu=opcion).first()
            permiso_rol = PermisoRol.objects.filter(rol=usuario.rol, opcion_menu=opcion).first()

            opcion_info = {
                'id': opcion.id,
                'codigo': opcion.codigo,
                'nombre': opcion.nombre,
                'icono': opcion.icono,
                'es_submenu': opcion.es_submenu,
                'permisos_rol': {
                    'puede_ver': permiso_rol.puede_ver if permiso_rol else False,
                    'puede_crear': permiso_rol.puede_crear if permiso_rol else False,
                    'puede_editar': permiso_rol.puede_editar if permiso_rol else False,
                    'puede_eliminar': permiso_rol.puede_eliminar if permiso_rol else False,
                    'puede_exportar': permiso_rol.puede_exportar if permiso_rol else False,
                },
                'overrides': {
                    'puede_ver': override.puede_ver if override else None,
                    'puede_crear': override.puede_crear if override else None,
                    'puede_editar': override.puede_editar if override else None,
                    'puede_eliminar': override.puede_eliminar if override else None,
                    'puede_exportar': override.puede_exportar if override else None,
                },
                'notas': override.notas if override else '',
            }

            if opcion.es_submenu:
                subopciones_data = []
                for sub in opcion.hijos.filter(activo=True).order_by('orden'):
                    sub_override = PermisoUsuario.objects.filter(usuario=usuario, opcion_menu=sub).first()
                    sub_rol = PermisoRol.objects.filter(rol=usuario.rol, opcion_menu=sub).first()
                    subopciones_data.append({
                        'id': sub.id,
                        'codigo': sub.codigo,
                        'nombre': sub.nombre,
                        'icono': sub.icono,
                        'permisos_rol': {
                            'puede_ver': sub_rol.puede_ver if sub_rol else False,
                            'puede_crear': sub_rol.puede_crear if sub_rol else False,
                            'puede_editar': sub_rol.puede_editar if sub_rol else False,
                            'puede_eliminar': sub_rol.puede_eliminar if sub_rol else False,
                            'puede_exportar': sub_rol.puede_exportar if sub_rol else False,
                        },
                        'overrides': {
                            'puede_ver': sub_override.puede_ver if sub_override else None,
                            'puede_crear': sub_override.puede_crear if sub_override else None,
                            'puede_editar': sub_override.puede_editar if sub_override else None,
                            'puede_eliminar': sub_override.puede_eliminar if sub_override else None,
                            'puede_exportar': sub_override.puede_exportar if sub_override else None,
                        },
                        'notas': sub_override.notas if sub_override else '',
                    })
                opcion_info['subopciones'] = subopciones_data

            opciones_data.append(opcion_info)

        modulos_data.append({
            'id': modulo.id,
            'codigo': modulo.codigo,
            'nombre': modulo.nombre,
            'icono': modulo.icono,
            'opciones': opciones_data,
        })

    return JsonResponse({
        'success': True,
        'usuario': {
            'id': usuario.id,
            'username': usuario.username,
            'nombre': usuario.get_full_name() or usuario.username,
            'rol': usuario.rol,
            'rol_display': usuario.get_rol_display() if hasattr(usuario, 'get_rol_display') else usuario.rol,
        },
        've_todas_sucursales': ve_todas,
        'modulos': modulos_data,
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permisos_usuario(request):
    """API para guardar overrides de permisos de un usuario"""
    try:
        data = json.loads(request.body)
        usuario_id = data.get('usuario_id')
        permisos_data = data.get('permisos', [])
        ve_todas_sucursales = data.get('ve_todas_sucursales', False)

        if not usuario_id:
            return JsonResponse({'error': True, 'mensaje': 'usuario_id requerido'}, status=400)

        try:
            usuario = Usuario.objects.get(id=usuario_id)
        except Usuario.DoesNotExist:
            return JsonResponse({'error': True, 'mensaje': 'Usuario no encontrado'}, status=404)

        creados = 0
        actualizados = 0
        eliminados = 0

        for item in permisos_data:
            opcion_id = item.get('opcion_id')
            overrides = item.get('overrides', {})

            opcion = OpcionMenu.objects.filter(id=opcion_id).first()
            if not opcion:
                continue

            all_none = all(v is None for v in overrides.values())
            if all_none:
                deleted, _ = PermisoUsuario.objects.filter(
                    usuario=usuario, opcion_menu=opcion
                ).delete()
                eliminados += deleted
                continue

            permiso, created = PermisoUsuario.objects.get_or_create(
                usuario=usuario,
                opcion_menu=opcion
            )
            permiso.puede_ver = overrides.get('puede_ver')
            permiso.puede_crear = overrides.get('puede_crear')
            permiso.puede_editar = overrides.get('puede_editar')
            permiso.puede_eliminar = overrides.get('puede_eliminar')
            permiso.puede_exportar = overrides.get('puede_exportar')
            permiso.puede_ver_todas_sucursales = ve_todas_sucursales
            permiso.notas = item.get('notas', '')
            permiso.save()

            if created:
                creados += 1
            else:
                actualizados += 1

        PermisoUsuario.objects.filter(usuario=usuario).update(
            puede_ver_todas_sucursales=ve_todas_sucursales
        )

        if ve_todas_sucursales and not PermisoUsuario.objects.filter(usuario=usuario).exists():
            primera_opcion = OpcionMenu.objects.filter(activo=True).first()
            if primera_opcion:
                PermisoUsuario.objects.create(
                    usuario=usuario,
                    opcion_menu=primera_opcion,
                    puede_ver_todas_sucursales=True
                )
                creados += 1

        return JsonResponse({
            'success': True,
            'mensaje': 'Permisos de usuario guardados',
            'creados': creados,
            'actualizados': actualizados,
            'eliminados': eliminados,
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': True, 'mensaje': 'JSON invalido'}, status=400)
    except Exception as e:
        return JsonResponse({'error': True, 'mensaje': str(e)}, status=500)


@login_required
@solo_administrador
@require_http_methods(["POST"])
def eliminar_permisos_usuario(request):
    """API para eliminar todos los overrides de un usuario"""
    try:
        data = json.loads(request.body)
        usuario_id = data.get('usuario_id')

        if not usuario_id:
            return JsonResponse({'error': True, 'mensaje': 'usuario_id requerido'}, status=400)

        deleted, _ = PermisoUsuario.objects.filter(usuario_id=usuario_id).delete()

        return JsonResponse({
            'success': True,
            'mensaje': f'{deleted} overrides eliminados',
            'eliminados': deleted,
        })
    except Exception as e:
        return JsonResponse({'error': True, 'mensaje': str(e)}, status=500)


@login_required
@solo_administrador
@require_http_methods(["POST"])
def copiar_permisos_usuario(request):
    """API para copiar overrides de un usuario a otro"""
    try:
        data = json.loads(request.body)
        origen_id = data.get('usuario_origen_id')
        destino_id = data.get('usuario_destino_id')

        if not origen_id or not destino_id:
            return JsonResponse({'error': True, 'mensaje': 'IDs origen y destino requeridos'}, status=400)

        if str(origen_id) == str(destino_id):
            return JsonResponse({'error': True, 'mensaje': 'Origen y destino no pueden ser iguales'}, status=400)

        permisos_origen = PermisoUsuario.objects.filter(usuario_id=origen_id)
        creados = 0
        actualizados = 0

        for p_orig in permisos_origen:
            p_dest, created = PermisoUsuario.objects.get_or_create(
                usuario_id=destino_id,
                opcion_menu=p_orig.opcion_menu
            )
            p_dest.puede_ver = p_orig.puede_ver
            p_dest.puede_crear = p_orig.puede_crear
            p_dest.puede_editar = p_orig.puede_editar
            p_dest.puede_eliminar = p_orig.puede_eliminar
            p_dest.puede_exportar = p_orig.puede_exportar
            p_dest.puede_ver_todas_sucursales = p_orig.puede_ver_todas_sucursales
            p_dest.notas = f"Copiado de usuario ID {origen_id}"
            p_dest.save()

            if created:
                creados += 1
            else:
                actualizados += 1

        return JsonResponse({
            'success': True,
            'mensaje': f'Permisos copiados: {creados} creados, {actualizados} actualizados',
            'creados': creados,
            'actualizados': actualizados,
        })
    except Exception as e:
        return JsonResponse({'error': True, 'mensaje': str(e)}, status=500)
