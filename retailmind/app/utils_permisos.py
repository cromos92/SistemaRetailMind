"""
Utilidades centralizadas para permisos y filtrado de sucursales.
El flag is_superuser de Django NO otorga privilegios. Solo importa el sistema de roles y permisos.
"""
from .models import Sucursal, EmpresaUser, PermisoUsuario


def obtener_sucursales_usuario(usuario):
    """
    Retorna un queryset de sucursales a las que el usuario tiene acceso.

    Lógica:
    - Administradores: todas las sucursales activas
    - Usuarios con flag puede_ver_todas_sucursales: todas las sucursales activas
    - Demás usuarios: solo las sucursales asignadas via EmpresaUser
    """
    if getattr(usuario, 'rol', '') == 'administrador':
        return Sucursal.objects.filter(activa=True).order_by('alias')

    if PermisoUsuario.usuario_ve_todas_sucursales(usuario):
        return Sucursal.objects.filter(activa=True).order_by('alias')

    sucursal_ids = EmpresaUser.objects.filter(
        user=usuario,
        status=True,
        sucursal__isnull=False
    ).values_list('sucursal_id', flat=True)

    return Sucursal.objects.filter(id__in=sucursal_ids, activa=True).order_by('alias')


def puede_ver_sucursal(usuario, sucursal_id):
    """
    Verifica si un usuario puede acceder a datos de una sucursal específica.
    """
    if not sucursal_id:
        return True

    if getattr(usuario, 'rol', '') == 'administrador':
        return True

    if PermisoUsuario.usuario_ve_todas_sucursales(usuario):
        return True

    return EmpresaUser.objects.filter(
        user=usuario,
        sucursal_id=sucursal_id,
        status=True
    ).exists()


def filtrar_queryset_por_sucursal(queryset, usuario, request, campo_sucursal='sucursal_id'):
    """
    Aplica filtro de sucursal a un queryset de forma consistente.
    """
    sucursal_id = request.GET.get('sucursal_id')

    if sucursal_id:
        if puede_ver_sucursal(usuario, sucursal_id):
            return queryset.filter(**{campo_sucursal: sucursal_id})
        else:
            sucursal_sesion = request.session.get('idSucursalActual')
            if sucursal_sesion:
                return queryset.filter(**{campo_sucursal: sucursal_sesion})
            return queryset.none()

    if usuario_puede_ver_todas_sucursales(usuario):
        return queryset

    sucursal_sesion = request.session.get('idSucursalActual')
    if sucursal_sesion:
        return queryset.filter(**{campo_sucursal: sucursal_sesion})
    return queryset.none()


def usuario_puede_ver_todas_sucursales(usuario):
    """
    Verifica si un usuario puede ver datos de todas las sucursales.
    """
    if getattr(usuario, 'rol', '') == 'administrador':
        return True
    return PermisoUsuario.usuario_ve_todas_sucursales(usuario)


def obtener_contexto_sucursales(usuario, request):
    """
    Retorna un dict de contexto para templates que necesitan info de sucursales.
    """
    sucursal_activa_id = request.session.get('idSucursalActual')
    sucursal_activa_nombre = request.session.get('nombreSucursalActual', '')
    puede_ver_todas = usuario_puede_ver_todas_sucursales(usuario)

    return {
        'sucursal_activa_id': sucursal_activa_id,
        'sucursal_activa_nombre': sucursal_activa_nombre,
        'puede_ver_todas_sucursales': puede_ver_todas,
    }
