"""
Utilidades centralizadas para permisos y filtrado de sucursales.
El flag is_superuser de Django NO otorga privilegios. Solo importa el sistema de roles y permisos.
"""
from calendar import monthrange
from datetime import timedelta

from django.utils import timezone

from .models import (
    Sucursal,
    EmpresaUser,
    PermisoUsuario,
    PermisoRol,
    ConfiguracionPermisoGlobal,
)


ARQUEO_RANGO_DEFAULTS = {
    'cajero': {'tipo': 'dias', 'valor': 2},
    'vendedor': {'tipo': 'dias', 'valor': 2},
    'jefe_local': {'tipo': 'dias', 'valor': 3},
    'administracion': {'tipo': 'dias', 'valor': 30},
    'administrador': {'tipo': 'dias', 'valor': 30},
}


def _normalizar_tipo_rango_arqueo(tipo):
    return tipo if tipo in {'dias', 'meses'} else 'dias'


def _normalizar_valor_rango_arqueo(valor, valor_default):
    try:
        valor_int = int(valor)
    except (TypeError, ValueError):
        valor_int = int(valor_default)
    return max(1, valor_int)


def _clave_rango_arqueo(rol, sufijo):
    return f'ARQUEO_RANGO_{(rol or "sin_rol").upper()}_{sufijo}'


def restar_meses_fecha(fecha_base, meses):
    """
    Resta meses calendario preservando el día cuando sea posible.
    Si el mes destino no tiene ese día, usa el último día válido.
    """
    if meses <= 0:
        return fecha_base

    total_meses = fecha_base.year * 12 + (fecha_base.month - 1) - meses
    anio = total_meses // 12
    mes = total_meses % 12 + 1
    ultimo_dia = monthrange(anio, mes)[1]
    dia = min(fecha_base.day, ultimo_dia)
    return fecha_base.replace(year=anio, month=mes, day=dia)


def formatear_rango_arqueo(tipo, valor):
    unidad = 'día' if valor == 1 else 'días'
    if tipo == 'meses':
        unidad = 'mes' if valor == 1 else 'meses'
    return f'{valor} {unidad}'


def calcular_fecha_minima_rango_arqueo(fecha_referencia, tipo, valor):
    tipo_normalizado = _normalizar_tipo_rango_arqueo(tipo)
    valor_normalizado = _normalizar_valor_rango_arqueo(valor, 1)

    if tipo_normalizado == 'meses':
        return restar_meses_fecha(fecha_referencia, valor_normalizado)
    return fecha_referencia - timedelta(days=valor_normalizado)


def obtener_configuracion_rango_arqueo(rol, fecha_referencia=None):
    """
    Obtiene la configuración vigente del rango permitido para crear arqueos
    históricos según rol. La configuración se guarda en
    `ConfiguracionPermisoGlobal` para administrarse desde permisos.
    """
    fecha_ref = fecha_referencia or timezone.localdate()
    defaults = ARQUEO_RANGO_DEFAULTS.get(rol or '', ARQUEO_RANGO_DEFAULTS['cajero'])

    clave_tipo = _clave_rango_arqueo(rol, 'TIPO')
    clave_valor = _clave_rango_arqueo(rol, 'VALOR')

    tipo_config = ConfiguracionPermisoGlobal.objects.filter(
        clave=clave_tipo,
        activo=True,
    ).first()
    valor_config = ConfiguracionPermisoGlobal.objects.filter(
        clave=clave_valor,
        activo=True,
    ).first()

    tipo = _normalizar_tipo_rango_arqueo(
        tipo_config.get_valor() if tipo_config else defaults['tipo']
    )
    valor = _normalizar_valor_rango_arqueo(
        valor_config.get_valor() if valor_config else defaults['valor'],
        defaults['valor'],
    )
    fecha_minima = calcular_fecha_minima_rango_arqueo(fecha_ref, tipo, valor)

    return {
        'rol': rol,
        'tipo': tipo,
        'valor': valor,
        'label': formatear_rango_arqueo(tipo, valor),
        'fecha_minima': fecha_minima,
        'dias_equivalentes': max((fecha_ref - fecha_minima).days, 0),
        'clave_tipo': clave_tipo,
        'clave_valor': clave_valor,
    }


def guardar_configuracion_rango_arqueo(rol, tipo, valor, usuario=None):
    """
    Persiste la configuración del rango de arqueo para un rol.
    """
    if rol not in dict(PermisoRol.ROLES_CHOICES):
        raise ValueError('Rol no válido para configurar rango de arqueo')

    defaults = ARQUEO_RANGO_DEFAULTS.get(rol, ARQUEO_RANGO_DEFAULTS['cajero'])
    tipo_normalizado = _normalizar_tipo_rango_arqueo(tipo)
    valor_normalizado = _normalizar_valor_rango_arqueo(valor, defaults['valor'])
    label = formatear_rango_arqueo(tipo_normalizado, valor_normalizado)

    ConfiguracionPermisoGlobal.objects.update_or_create(
        clave=_clave_rango_arqueo(rol, 'TIPO'),
        defaults={
            'valor': tipo_normalizado,
            'descripcion': (
                f'Unidad del rango histórico permitido para crear arqueos '
                f'del rol {rol}.'
            ),
            'tipo_dato': 'string',
            'activo': True,
            'usuario_modificacion': usuario,
        },
    )
    ConfiguracionPermisoGlobal.objects.update_or_create(
        clave=_clave_rango_arqueo(rol, 'VALOR'),
        defaults={
            'valor': str(valor_normalizado),
            'descripcion': (
                f'Cantidad del rango histórico permitido para crear arqueos '
                f'del rol {rol}. Configuración actual: {label}.'
            ),
            'tipo_dato': 'integer',
            'activo': True,
            'usuario_modificacion': usuario,
        },
    )

    return obtener_configuracion_rango_arqueo(rol)


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


def puede_cambiar_sucursal(usuario):
    """
    Verifica si un usuario puede cambiar su empresa/sucursal activa.

    Orden de resolución:
    1. Si el rol es 'administrador' -> siempre True (fallback de seguridad).
    2. En caso contrario se consulta el sistema estándar de permisos
       (PermisoUsuario > PermisoRol > PermisoSucursal) sobre la opción
       'cambiar_empresa' usando el flag `puede_ver`.

    Esto permite habilitar o deshabilitar el cambio de sucursal desde
    la interfaz de gestión de permisos (/app/permisos/gestion/) tanto
    a nivel de rol como con overrides por usuario.
    """
    if not usuario or not getattr(usuario, 'is_authenticated', False):
        return False

    if getattr(usuario, 'rol', '') == 'administrador':
        return True

    return PermisoRol.tiene_permiso(
        usuario,
        codigo_opcion='cambiar_empresa',
        tipo_permiso='puede_ver',
    )


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
        'empresa_activa_id': request.session.get('idEmpresaActual'),
        'empresa_activa_nombre': request.session.get('nombreEmpresaActual', ''),
        'puede_ver_todas_sucursales': puede_ver_todas,
    }
