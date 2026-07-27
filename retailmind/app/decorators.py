"""
Decoradores para protección de vistas basados en permisos por rol
"""
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from .models import PermisoRol


def requiere_permiso(codigo_opcion, tipo_permiso='puede_ver', redirigir_a='bienvenida'):
    """
    Decorador para proteger vistas según permisos del rol del usuario Y de la sucursal activa
    
    Args:
        codigo_opcion (str): Código de la opción del menú (ej: 'dashboard_ventas', 'pos_dashboard')
        tipo_permiso (str): Tipo de permiso a verificar (puede_ver, puede_crear, puede_editar, puede_eliminar, puede_exportar, puede_aprobar)
        redirigir_a (str): Nombre de la URL a donde redirigir si no tiene permiso
    
    Uso:
        @requiere_permiso('dashboard_ventas', 'puede_ver')
        def mi_vista(request):
            ...
        
        @requiere_permiso('pos_dashboard', 'puede_crear')
        def crear_venta(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            # Obtener la sucursal actual de la sesión
            sucursal_id = request.session.get('idSucursalActual')
            
            # Verificar permiso (por rol y por sucursal)
            tiene_permiso = PermisoRol.tiene_permiso(
                usuario=request.user,
                codigo_opcion=codigo_opcion,
                tipo_permiso=tipo_permiso,
                sucursal_id=sucursal_id
            )
            
            if not tiene_permiso:
                # Determinar si es petición AJAX o normal
                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
                    return JsonResponse({
                        'error': True,
                        'mensaje': f'No tienes permiso para realizar esta acción. Se requiere permiso: {tipo_permiso}'
                    }, status=403)
                else:
                    messages.error(
                        request,
                        f'⚠️ No tienes permiso para acceder a esta funcionalidad. '
                        f'Contacta al administrador si crees que deberías tener acceso.'
                    )
                    return redirect(redirigir_a)
            
            return view_func(request, *args, **kwargs)
        
        return _wrapped_view
    return decorator


def requiere_alguno_de_los_permisos(*opciones, redirigir_a='bienvenida'):
    """
    Autoriza si el usuario cumple AL MENOS UNA de las opciones indicadas.

    Existe para los endpoints que sirven a más de un flujo, donde exigir el
    permiso del módulo completo deja fuera a quien sólo necesita la capacidad
    puntual. Caso que lo motivó: la caja consulta la ficha del cliente por RUT
    al cobrar; exigir 'fidelizacion_cuentas' (que además abre el listado de
    TODOS los clientes) dejaba a los vendedores con 403 en el cobro, aunque sí
    tienen permiso sobre 'ticket_venta'. Con esto el acceso queda atado a la
    capacidad —operar la caja— y no a poder navegar el maestro de clientes.

    Args:
        *opciones: códigos de opción, o tuplas (codigo_opcion, tipo_permiso).
                   Un código suelto se interpreta como (codigo, 'puede_ver').
        redirigir_a (str): URL a la que redirigir si no cumple ninguna.

    Uso:
        @requiere_alguno_de_los_permisos('fidelizacion_cuentas', 'ticket_venta')
        def api_consultar_saldo_puntos(request):
            ...

        @requiere_alguno_de_los_permisos(
            ('fidelizacion_cuentas', 'puede_ver'), ('pos_dashboard', 'puede_crear'))
        def otra_vista(request):
            ...
    """
    normalizadas = [
        (op, 'puede_ver') if isinstance(op, str) else (op[0], op[1])
        for op in opciones
    ]

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            sucursal_id = request.session.get('idSucursalActual')

            tiene_permiso = any(
                PermisoRol.tiene_permiso(
                    usuario=request.user,
                    codigo_opcion=codigo,
                    tipo_permiso=tipo,
                    sucursal_id=sucursal_id,
                )
                for codigo, tipo in normalizadas
            )

            if not tiene_permiso:
                requeridos = ' o '.join(f'{c}.{t}' for c, t in normalizadas)
                if (request.headers.get('x-requested-with') == 'XMLHttpRequest'
                        or request.content_type == 'application/json'):
                    return JsonResponse({
                        'error': True,
                        'mensaje': f'No tienes permiso para realizar esta acción. '
                                   f'Se requiere alguno de: {requeridos}'
                    }, status=403)
                messages.error(
                    request,
                    '⚠️ No tienes permiso para acceder a esta funcionalidad. '
                    'Contacta al administrador si crees que deberías tener acceso.'
                )
                return redirect(redirigir_a)

            return view_func(request, *args, **kwargs)

        return _wrapped_view
    return decorator


def requiere_rol(*roles_permitidos):
    """
    Decorador simple para verificar que el usuario tenga uno de los roles especificados
    
    Args:
        *roles_permitidos: Lista de roles permitidos ('administrador', 'jefe_local', 'cajero', 'vendedor')
    
    Uso:
        @requiere_rol('administrador')
        def vista_solo_admin(request):
            ...
        
        @requiere_rol('administrador', 'jefe_local')
        def vista_admin_y_jefe(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            # Verificar rol
            if hasattr(request.user, 'rol') and request.user.rol in roles_permitidos:
                return view_func(request, *args, **kwargs)
            
            # Sin permiso
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    'error': True,
                    'mensaje': 'No tienes el rol necesario para acceder a esta funcionalidad.'
                }, status=403)
            else:
                messages.error(
                    request,
                    '⚠️ No tienes el rol necesario para acceder a esta funcionalidad.'
                )
                return redirect('bienvenida')
        
        return _wrapped_view
    return decorator


def solo_administrador(view_func):
    """
    Decorador de atajo para vistas que solo pueden acceder administradores
    
    Uso:
        @solo_administrador
        def vista_admin(request):
            ...
    """
    return requiere_rol('administrador')(view_func)


def solo_administrador_o_jefe(view_func):
    """
    Decorador de atajo para vistas accesibles por administradores y jefes de local
    
    Uso:
        @solo_administrador_o_jefe
        def vista_gestion(request):
            ...
    """
    return requiere_rol('administrador', 'jefe_local')(view_func)


def verificar_permisos_multiples(*permisos):
    """
    Decorador para verificar múltiples permisos a la vez
    El usuario debe tener TODOS los permisos especificados
    
    Args:
        *permisos: Tuplas de (codigo_opcion, tipo_permiso)
    
    Uso:
        @verificar_permisos_multiples(
            ('dashboard_ventas', 'puede_ver'),
            ('exportar_ventas', 'puede_exportar')
        )
        def exportar_dashboard_ventas(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped_view(request, *args, **kwargs):
            # Verificar todos los permisos
            sucursal_id = request.session.get('idSucursalActual')
            for codigo_opcion, tipo_permiso in permisos:
                if not PermisoRol.tiene_permiso(request.user, codigo_opcion, tipo_permiso, sucursal_id=sucursal_id):
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                        return JsonResponse({
                            'error': True,
                            'mensaje': f'No tienes todos los permisos necesarios. Falta: {tipo_permiso} en {codigo_opcion}'
                        }, status=403)
                    else:
                        messages.error(
                            request,
                            '⚠️ No tienes todos los permisos necesarios para realizar esta acción.'
                        )
                        return redirect('bienvenida')
            
            return view_func(request, *args, **kwargs)
        
        return _wrapped_view
    return decorator


def agregar_permisos_contexto(view_func):
    """
    Decorador que agrega automáticamente los permisos del usuario al contexto
    Útil para vistas que renderizan templates
    
    Uso:
        @agregar_permisos_contexto
        def mi_vista(request):
            # request.permisos_usuario ya está disponible
            ...
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated:
            # Agregar permisos al request para que estén disponibles en la vista
            request.permisos_usuario = PermisoRol.opciones_disponibles_para_usuario(request.user)
        else:
            request.permisos_usuario = []
        
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view

