"""
Template tags para sistema de permisos
Permite verificar permisos y filtrar menús en templates
"""
from django import template
from django.db.models import Q
from app.models import PermisoRol, PermisoUsuario, OpcionMenu, ModuloSistema
from app.utils_permisos import puede_cambiar_sucursal as _puede_cambiar_sucursal

register = template.Library()


def _opciones_ids_usuario(user, modulo=None, padre=None, sucursal_id=None):
    """
    Helper: retorna IDs de OpcionMenu que un usuario puede ver,
    considerando PermisoUsuario overrides + PermisoRol.
    """
    filtro_opcion = Q(activo=True)
    if modulo:
        filtro_opcion &= Q(modulo=modulo)
    if padre is not None:
        filtro_opcion &= Q(padre=padre)
    elif padre is None and modulo:
        filtro_opcion &= Q(padre__isnull=True)

    todas_opciones = OpcionMenu.objects.filter(filtro_opcion)

    if sucursal_id:
        return {
            opcion.id
            for opcion in todas_opciones
            if PermisoRol.tiene_permiso(user, opcion.codigo, 'puede_ver', sucursal_id=sucursal_id)
        }

    # IDs con puede_ver=True por rol
    ids_por_rol = set(PermisoRol.objects.filter(
        rol=user.rol,
        puede_ver=True,
        opcion_menu__in=todas_opciones
    ).values_list('opcion_menu_id', flat=True))

    # IDs con override usuario: puede_ver=True (agregar)
    ids_usuario_grant = set(PermisoUsuario.objects.filter(
        usuario=user,
        puede_ver=True,
        opcion_menu__in=todas_opciones
    ).values_list('opcion_menu_id', flat=True))

    # IDs con override usuario: puede_ver=False (quitar)
    ids_usuario_deny = set(PermisoUsuario.objects.filter(
        usuario=user,
        puede_ver=False,
        opcion_menu__in=todas_opciones
    ).values_list('opcion_menu_id', flat=True))

    return (ids_por_rol | ids_usuario_grant) - ids_usuario_deny


@register.simple_tag(takes_context=True)
def tiene_permiso(context, codigo_opcion, tipo_permiso='puede_ver'):
    """
    Verifica si el usuario tiene un permiso específico (por rol y por sucursal)
    
    Uso en template:
        {% tiene_permiso 'dashboard_ventas' 'puede_ver' as puede_ver_dashboard %}
        {% if puede_ver_dashboard %}
            <a href="...">Dashboard Ventas</a>
        {% endif %}
    """
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return False
    
    # Obtener la sucursal actual de la sesión
    sucursal_id = request.session.get('idSucursalActual')
    
    return PermisoRol.tiene_permiso(
        request.user,
        codigo_opcion,
        tipo_permiso,
        sucursal_id=sucursal_id
    )


@register.simple_tag(takes_context=True)
def puede_ver_opcion_tag(context, codigo_opcion):
    """
    Tag que verifica si el usuario puede ver una opción (por rol Y por sucursal).
    
    Uso en template:
        {% puede_ver_opcion_tag 'dashboard_ventas' as puede_ver %}
        {% if puede_ver %}...{% endif %}
    """
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return False
    
    sucursal_id = request.session.get('idSucursalActual')
    return PermisoRol.tiene_permiso(request.user, codigo_opcion, 'puede_ver', sucursal_id=sucursal_id)


@register.filter
def puede_ver_opcion(user, codigo_opcion):
    """
    Filtro para verificar si el usuario puede ver una opción.
    NOTE: This filter cannot access the request/session, so it only checks role permissions.
    The middleware handles sucursal-level blocking at the URL level.
    
    Uso en template:
        {% if user|puede_ver_opcion:'dashboard_ventas' %}
            <a href="...">Dashboard Ventas</a>
        {% endif %}
    """
    if not user or not user.is_authenticated:
        return False
    
    return PermisoRol.tiene_permiso(user, codigo_opcion, 'puede_ver')


@register.filter
def puede_crear_en(user, codigo_opcion):
    """
    Filtro para verificar si el usuario puede crear en una opción
    """
    if not user or not user.is_authenticated:
        return False
    
    return PermisoRol.tiene_permiso(user, codigo_opcion, 'puede_crear')


@register.filter
def puede_editar_en(user, codigo_opcion):
    """
    Filtro para verificar si el usuario puede editar en una opción
    """
    if not user or not user.is_authenticated:
        return False
    
    return PermisoRol.tiene_permiso(user, codigo_opcion, 'puede_editar')


@register.filter
def puede_eliminar_en(user, codigo_opcion):
    """
    Filtro para verificar si el usuario puede eliminar en una opción
    """
    if not user or not user.is_authenticated:
        return False
    
    return PermisoRol.tiene_permiso(user, codigo_opcion, 'puede_eliminar')


@register.filter
def puede_exportar_en(user, codigo_opcion):
    """
    Filtro para verificar si el usuario puede exportar en una opción
    """
    if not user or not user.is_authenticated:
        return False
    
    return PermisoRol.tiene_permiso(user, codigo_opcion, 'puede_exportar')


@register.filter
def puede_aprobar_en(user, codigo_opcion):
    """
    Filtro para verificar si el usuario puede aprobar en una opción
    """
    if not user or not user.is_authenticated:
        return False
    
    return PermisoRol.tiene_permiso(user, codigo_opcion, 'puede_aprobar')


@register.inclusion_tag('partials/menu_modulo.html', takes_context=True)
def mostrar_modulo(context, modulo_codigo):
    """
    Template tag de inclusión para mostrar un módulo completo del menú
    con sus opciones filtradas por permisos
    
    Uso en template:
        {% mostrar_modulo 'ventas' %}
    """
    request = context.get('request')
    user = request.user if request else None
    
    try:
        modulo = ModuloSistema.objects.get(codigo=modulo_codigo, activo=True)
        
        if user and user.is_authenticated:
            sucursal_id = request.session.get('idSucursalActual') if request else None
            opciones_ids = _opciones_ids_usuario(user, modulo=modulo, padre=None, sucursal_id=sucursal_id)
            opciones = modulo.opciones.filter(
                id__in=opciones_ids,
                activo=True,
                padre__isnull=True
            )
        else:
            opciones = []
        
        return {
            'modulo': modulo,
            'opciones': opciones,
            'user': user,
            'request': request
        }
    except ModuloSistema.DoesNotExist:
        return {
            'modulo': None,
            'opciones': [],
            'user': user,
            'request': request
        }


@register.simple_tag(takes_context=True)
def obtener_modulos_usuario(context):
    """
    Obtiene todos los módulos disponibles para el usuario actual
    
    Uso en template:
        {% obtener_modulos_usuario as modulos %}
        {% for modulo in modulos %}
            ...
        {% endfor %}
    """
    request = context.get('request')
    user = request.user if request else None
    
    if not user or not user.is_authenticated:
        return []
    
    # Obtener módulos con al menos una opción visible (rol + usuario overrides + sucursal)
    sucursal_id = request.session.get('idSucursalActual') if request else None
    opciones_ids = _opciones_ids_usuario(user, sucursal_id=sucursal_id)
    modulos_ids = OpcionMenu.objects.filter(
        id__in=opciones_ids,
        modulo__activo=True
    ).values_list('modulo_id', flat=True).distinct()

    return ModuloSistema.objects.filter(
        id__in=modulos_ids,
        activo=True
    ).order_by('orden')


@register.simple_tag(takes_context=True)
def obtener_opciones_modulo(context, modulo_codigo):
    """
    Obtiene las opciones de un módulo específico que el usuario puede ver
    
    Uso en template:
        {% obtener_opciones_modulo 'ventas' as opciones_ventas %}
    """
    request = context.get('request')
    user = request.user if request else None
    
    if not user or not user.is_authenticated:
        return []
    
    try:
        modulo = ModuloSistema.objects.get(codigo=modulo_codigo, activo=True)
        
        sucursal_id = request.session.get('idSucursalActual') if request else None
        opciones_ids = _opciones_ids_usuario(user, modulo=modulo, sucursal_id=sucursal_id)
        return OpcionMenu.objects.filter(
            id__in=opciones_ids,
            activo=True
        ).order_by('orden')
    except ModuloSistema.DoesNotExist:
        return []


@register.simple_tag
def rol_display(user):
    """
    Obtiene el nombre amigable del rol del usuario
    
    Uso:
        {% rol_display user %}
    """
    if not user or not user.is_authenticated:
        return "Invitado"
    
    if hasattr(user, 'get_rol_display'):
        return user.get_rol_display()
    
    if hasattr(user, 'rol'):
        roles_dict = {
            'administrador': 'Administrador',
            'administracion': 'Administración',
            'jefe_local': 'Jefe Local',
            'cajero': 'Cajero',
            'vendedor': 'Vendedor',
        }
        return roles_dict.get(user.rol, user.rol)
    
    return "Usuario"


@register.filter
def es_administrador(user):
    """
    Verifica si el usuario es administrador
    """
    if not user or not user.is_authenticated:
        return False
    
    return hasattr(user, 'rol') and user.rol == 'administrador'


@register.filter(name='puede_cambiar_sucursal')
def puede_cambiar_sucursal_filter(user):
    """
    Verifica si el usuario puede cambiar su empresa/sucursal activa.

    Resuelve en el mismo orden que el backend (ver app.utils_permisos):
      1. Rol 'administrador' -> siempre True.
      2. PermisoUsuario > PermisoRol sobre la opción 'cambiar_empresa'.

    Uso en template:
        {% if user|puede_cambiar_sucursal %}
            <a href="{% url 'cambiar_empresa' %}">Cambiar sucursal</a>
        {% endif %}
    """
    return _puede_cambiar_sucursal(user)


@register.filter
def es_jefe_o_admin(user):
    """
    Verifica si el usuario es jefe local o administrador
    """
    if not user or not user.is_authenticated:
        return False
    
    return hasattr(user, 'rol') and user.rol in ['administrador', 'jefe_local']


@register.simple_tag(takes_context=True)
def obtener_subopciones(context, opcion_codigo):
    """
    Obtiene las subopciones de una opción padre que el usuario puede ver
    
    Uso:
        {% obtener_subopciones 'dashboard_existencias' as subopciones %}
    """
    request = context.get('request')
    user = request.user if request else None
    
    if not user or not user.is_authenticated:
        return []
    
    try:
        opcion_padre = OpcionMenu.objects.get(codigo=opcion_codigo, activo=True)
        
        sucursal_id = request.session.get('idSucursalActual') if request else None
        subopciones_ids = _opciones_ids_usuario(user, padre=opcion_padre, sucursal_id=sucursal_id)
        return OpcionMenu.objects.filter(
            id__in=subopciones_ids,
            activo=True
        ).order_by('orden')
    except OpcionMenu.DoesNotExist:
        return []


@register.simple_tag(takes_context=True)
def contar_opciones_disponibles(context, user, modulo_codigo):
    """
    Cuenta cuántas opciones tiene disponibles un usuario en un módulo
    Útil para saber si mostrar o no un módulo completo
    
    Uso:
        {% contar_opciones_disponibles user 'ventas' as total_opciones %}
        {% if total_opciones > 0 %}
            <!-- Mostrar módulo -->
        {% endif %}
    """
    if not user or not user.is_authenticated:
        return 0
    
    try:
        modulo = ModuloSistema.objects.get(codigo=modulo_codigo, activo=True)
        request = context.get('request')
        sucursal_id = request.session.get('idSucursalActual') if request else None
        return len(_opciones_ids_usuario(user, modulo=modulo, sucursal_id=sucursal_id))
    except ModuloSistema.DoesNotExist:
        return 0
