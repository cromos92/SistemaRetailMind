"""
Vistas para el módulo de gestión de permisos

Tres capas deciden si un usuario puede algo (ver PermisoRol.tiene_permiso):
  1. PermisoUsuario  — override individual (SI / NO / usar rol)
  2. PermisoRol      — permiso del rol
  3. PermisoSucursal — restricción de la sucursal activa (solo quita, nunca da)
El rol Maestro se salta las tres: tiene acceso a todo.

Jerarquía para editar (la hace cumplir el servidor, no solo la pantalla):
  - El rol Maestro no se configura (acceso total).
  - El rol Administrador y los usuarios Administrador solo los ajusta el Maestro,
    para que un administrador no pueda devolverse a sí mismo lo que el Maestro
    le bloqueó (p. ej. Nota de Crédito o Conciliación Mercado Pago).
  - Nadie que no sea Maestro edita sus propios overrides.
"""
import json
import logging
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (
    ModuloSistema, OpcionMenu, PermisoRol, PermisoSucursal, PermisoUsuario,
    Sucursal, EmpresaUser, ROL_MAESTRO, es_maestro,
)
from users.models import Usuario
from .decorators import solo_administrador
from .utils_permisos import (
    obtener_configuracion_rango_arqueo,
    guardar_configuracion_rango_arqueo,
)

logger = logging.getLogger('app')


TIPOS_PERMISO = (
    'puede_ver', 'puede_crear', 'puede_editar',
    'puede_eliminar', 'puede_exportar', 'puede_aprobar',
)
# En sucursal, 'puede_ver' se llama 'habilitado'.
TIPOS_PERMISO_SUCURSAL = ('habilitado',) + TIPOS_PERMISO[1:]
# Sin fila de PermisoSucursal no hay restricción: todo en True. Los defaults
# del modelo (eliminar/aprobar en False) NO sirven de valor "sin fila": cada
# "Guardar" de la pantalla creaba filas con esos False y dejaba a la sucursal
# sin poder eliminar ni aprobar nada, ni siquiera el administrador (caso NICK1).
SUCURSAL_SIN_RESTRICCION = {t: True for t in TIPOS_PERMISO_SUCURSAL}

ROLES_VALIDOS = dict(PermisoRol.ROLES_CHOICES)

# Opciones que merecen un aviso visual en la pantalla: mueven dinero, emiten
# documentos al SII o reparten permisos.
CODIGOS_SENSIBLES = {
    'emitir_nota_credito', 'dineros_mercadopago', 'gestion_permisos',
    'gestion_usuarios', 'emision_dte', 'gestion_dte', 'gestion_creditos',
    'revision_arqueos', 'cuadratura_caja', 'modificacion_precios_costos',
    'devolucion_garantia', 'giftcards_emitir', 'ajuste_stock_rapido',
    'gestion_inventarios', 'dte_descargar_txt', 'emitir_nota_credito_traspaso',
    'asociar_pagos_mercadopago', 'dte_eliminar_documento', 'dte_compras_pagos',
    'dte_compras_eliminar', 'dte_editar_pago', 'dte_editar_fecha', 'dte_editar_numero',
    'dte_editar_folio',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_error(mensaje, status=400, **extra):
    return JsonResponse({'error': True, 'success': False, 'mensaje': mensaje, **extra}, status=status)


def _leer_json(request):
    return json.loads(request.body or '{}')


def _bool(valor, default=False):
    if valor is None:
        return default
    if isinstance(valor, str):
        return valor.strip().lower() in ('1', 'true', 'si', 'sí', 'on')
    return bool(valor)


def _motivo_rol_no_editable(usuario_actual, rol):
    """None si `usuario_actual` puede modificar los permisos de `rol`."""
    if rol not in ROLES_VALIDOS:
        return f'Rol no válido: {rol}'
    if rol == ROL_MAESTRO:
        return 'El rol Maestro tiene acceso total a todo: no se configura.'
    if rol == 'administrador' and not es_maestro(usuario_actual):
        return 'Solo el Maestro puede modificar los permisos del rol Administrador.'
    return None


def _motivo_usuario_no_editable(usuario_actual, usuario):
    """None si `usuario_actual` puede modificar los overrides de `usuario`."""
    if usuario.rol == ROL_MAESTRO:
        return 'Un usuario Maestro tiene acceso total: no admite permisos individuales.'
    if es_maestro(usuario_actual):
        return None
    if usuario.pk == usuario_actual.pk:
        return 'No puedes modificar tus propios permisos. Pídeselo al Maestro.'
    if usuario.rol == 'administrador':
        return 'Solo el Maestro puede modificar los permisos de un Administrador.'
    return None


def _tipo_opcion(opcion):
    """'pantalla' si tiene URL propia; 'accion' si es un permiso fino dentro de otra pantalla."""
    return 'pantalla' if (opcion.url_name or opcion.url_path) else 'accion'


def _info_opcion(opcion):
    return {
        'id': opcion.id,
        'codigo': opcion.codigo,
        'nombre': opcion.nombre,
        'url_name': opcion.url_name,
        'url_path': opcion.url_path,
        'icono': opcion.icono,
        'es_submenu': opcion.es_submenu,
        'tipo': _tipo_opcion(opcion),
        'sensible': opcion.codigo in CODIGOS_SENSIBLES,
    }


def _arbol_modulos(construir_opcion):
    """Módulos activos → opciones raíz → subopciones, en 2 consultas.

    `construir_opcion(opcion)` devuelve el dict de cada opción (se le agrega
    `subopciones` si corresponde). Antes cada endpoint hacía una consulta por
    opción y por subopción (~200 por carga).
    """
    modulos = list(ModuloSistema.objects.filter(activo=True).order_by('orden', 'nombre'))
    raiz = defaultdict(list)
    hijos = defaultdict(list)
    for op in OpcionMenu.objects.filter(activo=True).order_by('orden', 'nombre'):
        if op.padre_id:
            hijos[op.padre_id].append(op)
        else:
            raiz[op.modulo_id].append(op)

    data = []
    for modulo in modulos:
        opciones = []
        for op in raiz.get(modulo.id, []):
            info = construir_opcion(op)
            if op.es_submenu:
                info['subopciones'] = [construir_opcion(h) for h in hijos.get(op.id, [])]
            opciones.append(info)
        if not opciones:
            continue
        data.append({
            'id': modulo.id,
            'codigo': modulo.codigo,
            'nombre': modulo.nombre,
            'descripcion': modulo.descripcion,
            'icono': modulo.icono,
            'opciones': opciones,
        })
    return data


def _flags(fila, tipos, default):
    if fila is None:
        return dict(default)
    return {t: bool(getattr(fila, t)) for t in tipos}


def _limite_descuento_rol(rol):
    resultado = PermisoRol.objects.filter(rol=rol).aggregate(max_limite=Max('limite_descuento_porcentaje'))
    return resultado['max_limite'] if resultado['max_limite'] is not None else Decimal('0')


def _resumen_roles(usuario_actual):
    """Tarjetas de rol: usuarios activos y opciones visibles de cada uno."""
    total_opciones = OpcionMenu.objects.filter(activo=True).count()
    usuarios = dict(
        Usuario.objects.filter(es_activo=True, is_active=True)
        .values_list('rol').annotate(n=Count('id'))
    )
    visibles = dict(
        PermisoRol.objects.filter(puede_ver=True, opcion_menu__activo=True)
        .values_list('rol').annotate(n=Count('id'))
    )
    roles = []
    for codigo, nombre in PermisoRol.ROLES_CHOICES:
        motivo = _motivo_rol_no_editable(usuario_actual, codigo)
        roles.append({
            'codigo': codigo,
            'nombre': nombre,
            'usuarios': usuarios.get(codigo, 0),
            'opciones_visibles': total_opciones if codigo == ROL_MAESTRO else visibles.get(codigo, 0),
            'total_opciones': total_opciones,
            'editable': motivo is None,
            'motivo_bloqueo': motivo or '',
        })
    return roles


def _cargar_capas_usuario(usuario, sucursal_id):
    rol = {p.opcion_menu_id: p for p in PermisoRol.objects.filter(rol=usuario.rol)}
    overrides = {p.opcion_menu_id: p for p in PermisoUsuario.objects.filter(usuario=usuario)}
    sucursal = {}
    if sucursal_id:
        sucursal = {p.opcion_menu_id: p for p in PermisoSucursal.objects.filter(sucursal_id=sucursal_id)}
    return rol, overrides, sucursal


def _permiso_efectivo(usuario, opcion_id, tipo, rol, overrides, sucursal):
    """Réplica en memoria de PermisoRol.tiene_permiso, con el motivo del resultado."""
    if es_maestro(usuario):
        return True, 'MAESTRO'
    override = overrides.get(opcion_id)
    valor_override = getattr(override, tipo, None) if override else None
    if valor_override is False:
        return False, 'OVERRIDE_NO'
    if valor_override is True:
        motivo = 'OVERRIDE_SI'
    else:
        fila = rol.get(opcion_id)
        if fila is None:
            return False, 'SIN_FILA_ROL'
        if not getattr(fila, tipo, False):
            return False, 'ROL_NO'
        motivo = 'ROL'
    fila_suc = sucursal.get(opcion_id)
    if fila_suc is not None:
        clave = 'habilitado' if tipo == 'puede_ver' else tipo
        if not getattr(fila_suc, clave, True):
            return False, 'SUCURSAL_BLOQUEA'
    return True, motivo


# ---------------------------------------------------------------------------
# Página
# ---------------------------------------------------------------------------

@login_required
@solo_administrador
def gestion_permisos(request):
    """Pantalla de gestión de permisos (roles, sucursales, usuarios y diagnóstico)."""
    usuarios_activos = (
        Usuario.objects.filter(es_activo=True)
        .order_by('first_name', 'last_name', 'username')
    )
    context = {
        'roles': PermisoRol.ROLES_CHOICES,
        'resumen_roles': _resumen_roles(request.user),
        'total_modulos': ModuloSistema.objects.filter(activo=True).count(),
        'total_opciones': OpcionMenu.objects.filter(activo=True).count(),
        'total_permisos': PermisoRol.objects.count(),
        'total_overrides': PermisoUsuario.objects.values('usuario_id').distinct().count(),
        'total_usuarios': usuarios_activos.count(),
        'usuarios_activos': usuarios_activos,
        'sucursales': Sucursal.objects.all().order_by('alias'),
        'es_maestro_actual': es_maestro(request.user),
        'tab_inicial': request.GET.get('tab', ''),
    }
    return render(request, 'gestion_permisos/index.html', context)


@login_required
@solo_administrador
def gestionar_modulos_opciones(request):
    """Ruta vieja: su template nunca existió (daba error 500). Lleva al diagnóstico."""
    return redirect(f"{reverse('gestion_permisos')}?tab=diagnostico")


@login_required
@solo_administrador
def estadisticas_permisos(request):
    """Ruta vieja: su template nunca existió (daba error 500). Lleva al diagnóstico."""
    return redirect(f"{reverse('gestion_permisos')}?tab=diagnostico")


# ---------------------------------------------------------------------------
# Permisos por rol
# ---------------------------------------------------------------------------

@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_permisos_rol(request):
    """Árbol de permisos de un rol."""
    rol = request.GET.get('rol')
    if rol not in ROLES_VALIDOS:
        return _json_error('Rol no especificado o no válido')

    motivo = _motivo_rol_no_editable(request.user, rol)
    todo = {t: True for t in TIPOS_PERMISO}
    filas = {p.opcion_menu_id: p for p in PermisoRol.objects.filter(rol=rol)}

    def construir(op):
        info = _info_opcion(op)
        if rol == ROL_MAESTRO:
            info['permisos'] = dict(todo)
            info['sin_fila'] = False
        else:
            fila = filas.get(op.id)
            info['permisos'] = _flags(fila, TIPOS_PERMISO, {t: False for t in TIPOS_PERMISO})
            info['sin_fila'] = fila is None
        return info

    return JsonResponse({
        'success': True,
        'rol': rol,
        'rol_nombre': ROLES_VALIDOS[rol],
        'editable': motivo is None,
        'motivo_bloqueo': motivo or '',
        'es_rol_maestro': rol == ROL_MAESTRO,
        'limite_descuento': 100.0 if rol == ROL_MAESTRO else float(_limite_descuento_rol(rol)),
        'configuracion_arqueo': obtener_configuracion_rango_arqueo(rol),
        'modulos': _arbol_modulos(construir),
    }, json_dumps_params={'default': str})


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permiso(request):
    """Guarda UN flag de un rol (API suelta; la pantalla usa el guardado masivo)."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('Error en el formato de los datos')

    rol = data.get('rol')
    tipo_permiso = data.get('tipo_permiso')
    if tipo_permiso not in TIPOS_PERMISO:
        return _json_error(f'Tipo de permiso no válido: {tipo_permiso}')
    motivo = _motivo_rol_no_editable(request.user, rol)
    if motivo:
        return _json_error(motivo, status=403)
    opcion = OpcionMenu.objects.filter(id=data.get('opcion_id')).first()
    if opcion is None:
        return _json_error('Opción no encontrada', status=404)

    valor = _bool(data.get('valor'))
    permiso, created = PermisoRol.objects.get_or_create(
        rol=rol, opcion_menu=opcion,
        defaults={t: False for t in TIPOS_PERMISO} | {'limite_descuento_porcentaje': _limite_descuento_rol(rol)},
    )
    setattr(permiso, tipo_permiso, valor)
    permiso.save(update_fields=[tipo_permiso])
    logger.info('Permisos: %s puso %s.%s=%s al rol %s', request.user.username,
                opcion.codigo, tipo_permiso, valor, rol)
    return JsonResponse({
        'success': True,
        'mensaje': f'Permiso {"creado" if created else "actualizado"} correctamente',
        'permiso': {'rol': rol, 'opcion': opcion.nombre, tipo_permiso: valor},
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permisos_masivos(request):
    """Guarda todos los flags de un rol + límite de descuento + rango de arqueo."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('Error en el formato de los datos')

    rol = data.get('rol')
    motivo = _motivo_rol_no_editable(request.user, rol)
    if motivo:
        return _json_error(motivo, status=403)

    limite_descuento = data.get('limite_descuento')
    if limite_descuento is not None:
        try:
            limite_descuento = Decimal(str(limite_descuento))
        except (ValueError, TypeError, InvalidOperation):
            return _json_error('El límite de descuento debe ser un número válido')
        if limite_descuento < 0 or limite_descuento > 100:
            return _json_error('El límite de descuento debe estar entre 0 y 100')
    limite_efectivo = limite_descuento if limite_descuento is not None else Decimal('0')

    permisos_data = data.get('permisos') or []
    ids = []
    for item in permisos_data:
        try:
            ids.append(int(item.get('opcion_id')))
        except (TypeError, ValueError):
            continue
    opciones = {o.id: o for o in OpcionMenu.objects.filter(id__in=ids)}

    cambios = []
    creados = 0
    actualizados = 0
    try:
        with transaction.atomic():
            existentes = {p.opcion_menu_id: p for p in PermisoRol.objects.select_for_update().filter(rol=rol)}
            # El límite de descuento vive repetido en cada fila del rol.
            PermisoRol.objects.filter(rol=rol).update(limite_descuento_porcentaje=limite_efectivo)

            a_crear, a_actualizar = [], []
            for item in permisos_data:
                try:
                    opcion_id = int(item.get('opcion_id'))
                except (TypeError, ValueError):
                    continue
                opcion = opciones.get(opcion_id)
                if opcion is None:
                    continue
                valores = item.get('permisos') or {}
                permiso = existentes.get(opcion_id)
                nuevo = permiso is None
                if nuevo:
                    permiso = PermisoRol(rol=rol, opcion_menu=opcion)
                for tipo in TIPOS_PERMISO:
                    valor = _bool(valores.get(tipo))
                    anterior = None if nuevo else getattr(permiso, tipo)
                    if anterior is not None and anterior != valor:
                        cambios.append(f'{opcion.codigo}.{tipo}: {anterior}->{valor}')
                    setattr(permiso, tipo, valor)
                permiso.limite_descuento_porcentaje = limite_efectivo
                (a_crear if nuevo else a_actualizar).append(permiso)

            if a_crear:
                PermisoRol.objects.bulk_create(a_crear)
                creados = len(a_crear)
            if a_actualizar:
                PermisoRol.objects.bulk_update(a_actualizar, list(TIPOS_PERMISO) + ['limite_descuento_porcentaje'])
                actualizados = len(a_actualizar)

            # Sin filas, el límite de descuento no tendría dónde guardarse.
            if not PermisoRol.objects.filter(rol=rol).exists():
                primera = OpcionMenu.objects.filter(activo=True).first()
                if primera:
                    PermisoRol.objects.create(
                        rol=rol, opcion_menu=primera, limite_descuento_porcentaje=limite_efectivo,
                        **{t: False for t in TIPOS_PERMISO},
                    )
                    creados += 1

            configuracion_arqueo = data.get('configuracion_arqueo') or {}
            if configuracion_arqueo:
                config_arqueo = guardar_configuracion_rango_arqueo(
                    rol=rol,
                    tipo=configuracion_arqueo.get('tipo'),
                    valor=configuracion_arqueo.get('valor'),
                    usuario=request.user,
                )
            else:
                config_arqueo = obtener_configuracion_rango_arqueo(rol)
    except ValueError as exc:
        return _json_error(str(exc))

    logger.info('Permisos: %s guardó el rol %s (%d cambios)%s', request.user.username, rol,
                len(cambios), (': ' + '; '.join(cambios[:60])) if cambios else '')
    return JsonResponse({
        'success': True,
        'mensaje': (
            f'Permisos del rol {ROLES_VALIDOS[rol]} guardados '
            f'({len(cambios)} cambio{"s" if len(cambios) != 1 else ""}; '
            f'límite descuento {float(limite_efectivo)}%; rango arqueo {config_arqueo["label"]})'
        ),
        'creados': creados,
        'actualizados': actualizados,
        'cambios': len(cambios),
        'limite_descuento_guardado': float(limite_efectivo),
        'configuracion_arqueo_guardada': config_arqueo,
    }, json_dumps_params={'default': str})


@login_required
@solo_administrador
@require_http_methods(["POST"])
def copiar_permisos_rol(request):
    """Copia los permisos de un rol a otro (antes se perdía el flag Aprobar)."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('Error en el formato de los datos')

    rol_origen = data.get('rol_origen')
    rol_destino = data.get('rol_destino')
    sobrescribir = _bool(data.get('sobrescribir'))
    if rol_origen not in ROLES_VALIDOS or not rol_destino:
        return _json_error('Roles origen y destino son requeridos')
    if rol_origen == rol_destino:
        return _json_error('El rol origen y destino no pueden ser el mismo')
    motivo = _motivo_rol_no_editable(request.user, rol_destino)
    if motivo:
        return _json_error(motivo, status=403)

    if rol_origen == ROL_MAESTRO:
        # El Maestro no depende de filas: copiarlo = todo en True.
        origen = {o.id: {t: True for t in TIPOS_PERMISO} for o in OpcionMenu.objects.filter(activo=True)}
        limite_origen = Decimal('100')
    else:
        origen = {
            p.opcion_menu_id: {t: getattr(p, t) for t in TIPOS_PERMISO}
            for p in PermisoRol.objects.filter(rol=rol_origen)
        }
        limite_origen = _limite_descuento_rol(rol_origen)

    creados = actualizados = omitidos = 0
    with transaction.atomic():
        destino = {p.opcion_menu_id: p for p in PermisoRol.objects.select_for_update().filter(rol=rol_destino)}
        a_crear, a_actualizar = [], []
        for opcion_id, flags in origen.items():
            permiso = destino.get(opcion_id)
            if permiso is None:
                a_crear.append(PermisoRol(rol=rol_destino, opcion_menu_id=opcion_id,
                                          limite_descuento_porcentaje=limite_origen, **flags))
            elif sobrescribir:
                for tipo, valor in flags.items():
                    setattr(permiso, tipo, valor)
                permiso.limite_descuento_porcentaje = limite_origen
                a_actualizar.append(permiso)
            else:
                omitidos += 1
        PermisoRol.objects.bulk_create(a_crear)
        if a_actualizar:
            PermisoRol.objects.bulk_update(a_actualizar, list(TIPOS_PERMISO) + ['limite_descuento_porcentaje'])
        creados, actualizados = len(a_crear), len(a_actualizar)

        config_origen = obtener_configuracion_rango_arqueo(rol_origen)
        guardar_configuracion_rango_arqueo(
            rol=rol_destino, tipo=config_origen['tipo'], valor=config_origen['valor'],
            usuario=request.user,
        )

    logger.info('Permisos: %s copió el rol %s -> %s (sobrescribir=%s, %d creados, %d actualizados)',
                request.user.username, rol_origen, rol_destino, sobrescribir, creados, actualizados)
    return JsonResponse({
        'success': True,
        'mensaje': f'Permisos copiados de {ROLES_VALIDOS[rol_origen]} a {ROLES_VALIDOS[rol_destino]}',
        'creados': creados,
        'actualizados': actualizados,
        'omitidos': omitidos,
    })


# ---------------------------------------------------------------------------
# Permisos por sucursal
# ---------------------------------------------------------------------------

@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_sucursales_permisos(request):
    """Sucursales con el conteo de restricciones configuradas."""
    conteos = {
        r['sucursal_id']: r
        for r in PermisoSucursal.objects.values('sucursal_id').annotate(
            total=Count('id'),
            deshabilitadas=Count('id', filter=Q(habilitado=False)),
            restringidas=Count('id', filter=(
                Q(habilitado=False) | Q(puede_crear=False) | Q(puede_editar=False)
                | Q(puede_eliminar=False) | Q(puede_exportar=False) | Q(puede_aprobar=False)
            )),
        )
    }
    sucursales_data = []
    for suc in Sucursal.objects.all().order_by('alias'):
        c = conteos.get(suc.id, {})
        sucursales_data.append({
            'id': suc.id,
            'alias': suc.alias,
            'direccion': suc.direccion,
            'tipo_sucursal': getattr(suc, 'tipo_sucursal', None) or 'N/A',
            'tipo_sucursal_display': suc.get_tipo_sucursal_display() if hasattr(suc, 'get_tipo_sucursal_display') else 'N/A',
            'activa': getattr(suc, 'activa', True),
            'permisos_configurados': c.get('total', 0),
            'opciones_deshabilitadas': c.get('deshabilitadas', 0),
            'opciones_restringidas': c.get('restringidas', 0),
        })
    return JsonResponse({'success': True, 'sucursales': sucursales_data})


@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_permisos_sucursal(request):
    """Árbol de restricciones de una sucursal."""
    sucursal = Sucursal.objects.filter(id=request.GET.get('sucursal_id') or 0).first()
    if sucursal is None:
        return _json_error('Sucursal no encontrada', status=404)

    filas = {p.opcion_menu_id: p for p in PermisoSucursal.objects.filter(sucursal=sucursal)}

    def construir(op):
        info = _info_opcion(op)
        fila = filas.get(op.id)
        info['permisos'] = _flags(fila, TIPOS_PERMISO_SUCURSAL, SUCURSAL_SIN_RESTRICCION)
        info['notas'] = (fila.notas or '') if fila else ''
        info['configurada'] = fila is not None
        return info

    return JsonResponse({
        'success': True,
        'sucursal': {
            'id': sucursal.id,
            'alias': sucursal.alias,
            'direccion': sucursal.direccion,
            'tipo_sucursal': getattr(sucursal, 'tipo_sucursal', None) or 'N/A',
        },
        'modulos': _arbol_modulos(construir),
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permisos_sucursal(request):
    """Guarda las restricciones de una sucursal."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('Error en el formato de los datos')

    sucursal = Sucursal.objects.filter(id=data.get('sucursal_id') or 0).first()
    if sucursal is None:
        return _json_error('Sucursal no encontrada', status=404)

    permisos_data = data.get('permisos') or []
    ids = []
    for item in permisos_data:
        try:
            ids.append(int(item.get('opcion_id')))
        except (TypeError, ValueError):
            continue
    opciones = {o.id: o for o in OpcionMenu.objects.filter(id__in=ids)}

    creados = actualizados = 0
    with transaction.atomic():
        existentes = {p.opcion_menu_id: p for p in PermisoSucursal.objects.select_for_update().filter(sucursal=sucursal)}
        for item in permisos_data:
            try:
                opcion = opciones.get(int(item.get('opcion_id')))
            except (TypeError, ValueError):
                opcion = None
            if opcion is None:
                continue
            valores = item.get('permisos') or {}
            permiso = existentes.get(opcion.id)
            if permiso is None:
                permiso = PermisoSucursal(sucursal=sucursal, opcion_menu=opcion)
                creados += 1
            else:
                actualizados += 1
            for tipo in TIPOS_PERMISO_SUCURSAL:
                # Lo que no llega queda SIN restricción (ver SUCURSAL_SIN_RESTRICCION).
                setattr(permiso, tipo, _bool(valores.get(tipo), default=True))
            permiso.notas = (item.get('notas') or '').strip()
            permiso.save()

    logger.info('Permisos: %s guardó restricciones de la sucursal %s (%d opciones)',
                request.user.username, sucursal.alias, creados + actualizados)
    return JsonResponse({
        'success': True,
        'mensaje': f'Permisos guardados correctamente para {sucursal.alias}',
        'creados': creados,
        'actualizados': actualizados,
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def copiar_permisos_sucursal(request):
    """Copia las restricciones de una sucursal a otra (antes se perdía Aprobar)."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('Error en el formato de los datos')

    origen_id = data.get('sucursal_origen_id')
    destino_id = data.get('sucursal_destino_id')
    sobrescribir = _bool(data.get('sobrescribir'))
    if not origen_id or not destino_id:
        return _json_error('Sucursales origen y destino son requeridas')
    if str(origen_id) == str(destino_id):
        return _json_error('La sucursal origen y destino no pueden ser la misma')
    origen = Sucursal.objects.filter(id=origen_id).first()
    destino = Sucursal.objects.filter(id=destino_id).first()
    if origen is None or destino is None:
        return _json_error('Una de las sucursales no existe', status=404)

    creados = actualizados = omitidos = 0
    with transaction.atomic():
        existentes = {p.opcion_menu_id: p for p in PermisoSucursal.objects.select_for_update().filter(sucursal=destino)}
        for p_origen in PermisoSucursal.objects.filter(sucursal=origen):
            p_destino = existentes.get(p_origen.opcion_menu_id)
            if p_destino is not None and not sobrescribir:
                omitidos += 1
                continue
            if p_destino is None:
                p_destino = PermisoSucursal(sucursal=destino, opcion_menu_id=p_origen.opcion_menu_id)
                creados += 1
            else:
                actualizados += 1
            for tipo in TIPOS_PERMISO_SUCURSAL:
                setattr(p_destino, tipo, getattr(p_origen, tipo))
            p_destino.notas = f"Copiado de {origen.alias}"
            p_destino.save()

    logger.info('Permisos: %s copió restricciones %s -> %s', request.user.username, origen.alias, destino.alias)
    return JsonResponse({
        'success': True,
        'mensaje': f'Permisos copiados de {origen.alias} a {destino.alias}',
        'creados': creados,
        'actualizados': actualizados,
        'omitidos': omitidos,
    })


# Plantillas de restricciones por tipo de sucursal.
# ⚠️ Estos códigos DEBEN existir y estar activos en OpcionMenu. Un código
# inventado no lanza error: el bucle lo saltaría y la respuesta diría
# "plantilla aplicada". Como además se empieza habilitando TODO, una plantilla
# con códigos malos deja a la sucursal con MÁS acceso del que tenía.
# Pasó de verdad: 9 de 15 códigos no existían ('compras_gestion' en vez de
# 'gestion_compras', etc.), así que la plantilla VENDEDORA nunca bloqueó compras.
PLANTILLAS_SUCURSAL = {
    'VENDEDORA': {
        # Sucursal vendedora: NO puede comprar ni recepcionar mercadería.
        # Crear/importar productos no son opciones propias: son acciones de
        # 'gestion_producto', por eso se deja en solo lectura.
        'deshabilitar': ['gestion_compras', 'gestion_dte_compras', 'recepcion_dte'],
        'solo_lectura': ['gestion_producto', 'dashboard_compras_estrategico'],
    },
    'CENTRO_DISTRIBUCION': {
        # Centro de distribución: NO puede hacer ventas POS
        'deshabilitar': ['pos_dashboard', 'ticket_venta', 'cuadratura_caja',
                         'gestion_documentos_ventas', 'cambios_devoluciones'],
        'solo_lectura': ['dashboard_ventas'],
    },
    'MIXTA': {
        # Sucursal mixta: todo habilitado
        'deshabilitar': [],
        'solo_lectura': [],
    },
}


@login_required
@solo_administrador
@require_http_methods(["POST"])
def aplicar_plantilla_tipo_sucursal(request):
    """Aplica una plantilla de restricciones: VENDEDORA, CENTRO_DISTRIBUCION o MIXTA."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('Error en el formato de los datos')

    tipo_plantilla = data.get('tipo_plantilla')
    sucursal = Sucursal.objects.filter(id=data.get('sucursal_id') or 0).first()
    if sucursal is None or not tipo_plantilla:
        return _json_error('Sucursal y tipo de plantilla son requeridos')
    plantilla = PLANTILLAS_SUCURSAL.get(tipo_plantilla)
    if plantilla is None:
        return _json_error(f'Tipo de plantilla no válido: {tipo_plantilla}')

    codigos = plantilla['deshabilitar'] + plantilla['solo_lectura']
    opciones = {o.codigo: o for o in OpcionMenu.objects.filter(codigo__in=codigos, activo=True)}
    no_resueltos = [c for c in codigos if c not in opciones]
    if no_resueltos:
        # Se valida ANTES de tocar nada: el primer paso habilita todo.
        logger.error('Plantilla de permisos "%s" con códigos inexistentes o inactivos: %s. '
                     'No se aplicó nada.', tipo_plantilla, ', '.join(no_resueltos))
        return _json_error(
            f'La plantilla "{tipo_plantilla}" está mal definida y NO se aplicó: '
            f'{len(no_resueltos)} opción(es) no existen o están inactivas '
            f'({", ".join(no_resueltos)}). Aplicarla habría dejado la sucursal '
            f'con más acceso del que tiene ahora.',
            status=409, codigos_no_resueltos=no_resueltos,
        )

    actualizados = 0
    with transaction.atomic():
        # Primero se levanta toda restricción previa (mismo estado que "sin fila").
        PermisoSucursal.objects.filter(sucursal=sucursal).update(**SUCURSAL_SIN_RESTRICCION)
        for codigo in plantilla['deshabilitar']:
            permiso, _ = PermisoSucursal.objects.get_or_create(sucursal=sucursal, opcion_menu=opciones[codigo])
            permiso.habilitado = False
            permiso.puede_crear = False
            permiso.puede_editar = False
            permiso.notas = f"Deshabilitado por plantilla {tipo_plantilla}"
            permiso.save()
            actualizados += 1
        for codigo in plantilla['solo_lectura']:
            permiso, _ = PermisoSucursal.objects.get_or_create(sucursal=sucursal, opcion_menu=opciones[codigo])
            permiso.habilitado = True
            permiso.puede_crear = False
            permiso.puede_editar = False
            permiso.puede_eliminar = False
            permiso.notas = f"Solo lectura por plantilla {tipo_plantilla}"
            permiso.save()
            actualizados += 1

    logger.info('Permisos: %s aplicó plantilla %s a %s', request.user.username, tipo_plantilla, sucursal.alias)
    return JsonResponse({
        'success': True,
        'mensaje': f'Plantilla "{tipo_plantilla}" aplicada a {sucursal.alias}',
        'permisos_actualizados': actualizados,
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def restablecer_permisos_sucursal(request):
    """Elimina todas las restricciones de una sucursal."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('Error en el formato de los datos')
    sucursal = Sucursal.objects.filter(id=data.get('sucursal_id') or 0).first()
    if sucursal is None:
        return _json_error('Sucursal no encontrada', status=404)

    count, _ = PermisoSucursal.objects.filter(sucursal=sucursal).delete()
    logger.info('Permisos: %s restableció la sucursal %s (%d filas)', request.user.username, sucursal.alias, count)
    return JsonResponse({
        'success': True,
        'mensaje': f'Permisos restablecidos para {sucursal.alias}. Se eliminaron {count} configuraciones.',
        'eliminados': count,
    })


# ---------------------------------------------------------------------------
# Exportar / importar
# ---------------------------------------------------------------------------

def _descarga_json(data, filename):
    response = HttpResponse(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                            content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _exportar_filas_rol(rol):
    permisos = PermisoRol.objects.filter(rol=rol).select_related('opcion_menu', 'opcion_menu__modulo')
    return [{
        'opcion_codigo': p.opcion_menu.codigo,
        'opcion_nombre': p.opcion_menu.nombre,
        'modulo_codigo': p.opcion_menu.modulo.codigo if p.opcion_menu.modulo_id else None,
        'permisos': {t: getattr(p, t) for t in TIPOS_PERMISO},
    } for p in permisos]


@login_required
@solo_administrador
@require_http_methods(["GET"])
def exportar_permisos_rol(request):
    """Descarga los permisos de un rol en JSON."""
    rol = request.GET.get('rol')
    if rol not in ROLES_VALIDOS:
        return _json_error('Rol no especificado o no válido')
    filas = _exportar_filas_rol(rol)
    ahora = timezone.localtime()
    return _descarga_json({
        'version': '1.1',
        'tipo': 'permisos_rol',
        'fecha_exportacion': ahora.isoformat(),
        'rol': rol,
        'rol_nombre': ROLES_VALIDOS[rol],
        'limite_descuento': float(_limite_descuento_rol(rol)),
        'total_permisos': len(filas),
        'permisos': filas,
    }, f'permisos_{rol}_{ahora:%Y%m%d_%H%M%S}.json')


@login_required
@solo_administrador
@require_http_methods(["GET"])
def exportar_todos_permisos(request):
    """Descarga los permisos de todos los roles en JSON."""
    roles_data = []
    for rol, nombre in PermisoRol.ROLES_CHOICES:
        if rol == ROL_MAESTRO:
            continue  # acceso total, no se configura ni se importa
        filas = _exportar_filas_rol(rol)
        roles_data.append({
            'rol': rol,
            'rol_nombre': nombre,
            'limite_descuento': float(_limite_descuento_rol(rol)),
            'total_permisos': len(filas),
            'permisos': filas,
        })
    ahora = timezone.localtime()
    return _descarga_json({
        'version': '1.1',
        'tipo': 'permisos_completos',
        'fecha_exportacion': ahora.isoformat(),
        'total_roles': len(roles_data),
        'roles': roles_data,
    }, f'permisos_completos_{ahora:%Y%m%d_%H%M%S}.json')


@login_required
@solo_administrador
@require_http_methods(["POST"])
def importar_permisos(request):
    """Importa permisos de un rol o de todos desde un JSON exportado por esta pantalla."""
    try:
        if request.FILES.get('archivo'):
            data = json.loads(request.FILES['archivo'].read().decode('utf-8'))
        else:
            data = _leer_json(request)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return _json_error(f'Error al leer el archivo JSON: {e}')

    if not data.get('version') or not data.get('tipo'):
        return _json_error('Archivo de importación inválido: falta versión o tipo')

    sobrescribir = _bool(data.get('sobrescribir'), default=True)
    resultados = {'roles_procesados': 0, 'permisos_creados': 0, 'permisos_actualizados': 0,
                  'permisos_omitidos': 0, 'errores': []}

    if data['tipo'] == 'permisos_rol':
        rol_destino = request.POST.get('rol_destino') or data.get('rol_destino') or data.get('rol')
        motivo = _motivo_rol_no_editable(request.user, rol_destino)
        if motivo:
            return _json_error(motivo, status=403)
        lotes = [(rol_destino, data.get('limite_descuento', 0), data.get('permisos') or [])]
    elif data['tipo'] == 'permisos_completos':
        lotes = []
        for rol_data in data.get('roles') or []:
            rol = rol_data.get('rol')
            motivo = _motivo_rol_no_editable(request.user, rol)
            if motivo:
                resultados['errores'].append(f'{rol}: omitido — {motivo}')
                continue
            lotes.append((rol, rol_data.get('limite_descuento', 0), rol_data.get('permisos') or []))
    else:
        return _json_error(f'Tipo de archivo no soportado: {data["tipo"]}')

    with transaction.atomic():
        for rol, limite, permisos_data in lotes:
            try:
                limite = Decimal(str(limite or 0))
            except InvalidOperation:
                limite = Decimal('0')
            r = _importar_permisos_rol(rol, permisos_data, limite, sobrescribir)
            resultados['roles_procesados'] += 1
            resultados['permisos_creados'] += r['creados']
            resultados['permisos_actualizados'] += r['actualizados']
            resultados['permisos_omitidos'] += r['omitidos']
            resultados['errores'].extend(r['errores'])

    logger.info('Permisos: %s importó %d rol(es) (sobrescribir=%s)', request.user.username,
                resultados['roles_procesados'], sobrescribir)
    return JsonResponse({
        'success': True,
        'mensaje': f'Importación completada: {resultados["roles_procesados"]} rol(es) procesado(s)',
        'resultados': resultados,
    })


def _importar_permisos_rol(rol, permisos_data, limite_descuento, sobrescribir):
    """Aplica al rol las filas de un archivo exportado.

    Un flag que no viene en el archivo (p. ej. 'puede_aprobar' en exportaciones
    viejas, versión 1.0) NO se toca en filas existentes: antes se ponía en False
    y una importación de respaldo le quitaba en silencio las aprobaciones al rol.
    """
    resultado = {'creados': 0, 'actualizados': 0, 'omitidos': 0, 'errores': []}
    if sobrescribir:
        PermisoRol.objects.filter(rol=rol).update(limite_descuento_porcentaje=limite_descuento)

    opciones = {o.codigo: o for o in OpcionMenu.objects.filter(activo=True)}
    existentes = {p.opcion_menu_id: p for p in PermisoRol.objects.filter(rol=rol)}
    for item in permisos_data:
        codigo = item.get('opcion_codigo')
        opcion = opciones.get(codigo)
        if opcion is None:
            resultado['errores'].append(f'Opción no encontrada: {codigo}')
            continue
        valores = item.get('permisos') or {}
        permiso = existentes.get(opcion.id)
        if permiso is not None and not sobrescribir:
            resultado['omitidos'] += 1
            continue
        nuevo = permiso is None
        if nuevo:
            permiso = PermisoRol(rol=rol, opcion_menu=opcion, **{t: False for t in TIPOS_PERMISO})
        for tipo in TIPOS_PERMISO:
            if tipo in valores:
                setattr(permiso, tipo, _bool(valores[tipo]))
        permiso.limite_descuento_porcentaje = limite_descuento
        permiso.save()
        resultado['creados' if nuevo else 'actualizados'] += 1
    return resultado


@login_required
@solo_administrador
@require_http_methods(["GET"])
def exportar_permisos_sucursal(request):
    """Descarga las restricciones de una sucursal en JSON."""
    sucursal = Sucursal.objects.filter(id=request.GET.get('sucursal_id') or 0).first()
    if sucursal is None:
        return _json_error('Sucursal no encontrada', status=404)
    permisos = PermisoSucursal.objects.filter(sucursal=sucursal).select_related('opcion_menu', 'opcion_menu__modulo')
    filas = [{
        'opcion_codigo': p.opcion_menu.codigo,
        'opcion_nombre': p.opcion_menu.nombre,
        'modulo_codigo': p.opcion_menu.modulo.codigo if p.opcion_menu.modulo_id else None,
        'permisos': {t: getattr(p, t) for t in TIPOS_PERMISO_SUCURSAL},
        'notas': p.notas,
    } for p in permisos]
    ahora = timezone.localtime()
    return _descarga_json({
        'version': '1.1',
        'tipo': 'permisos_sucursal',
        'fecha_exportacion': ahora.isoformat(),
        'sucursal': {
            'id': sucursal.id,
            'alias': sucursal.alias,
            'direccion': sucursal.direccion,
            'tipo_sucursal': getattr(sucursal, 'tipo_sucursal', None),
        },
        'total_permisos': len(filas),
        'permisos': filas,
    }, f'permisos_sucursal_{(sucursal.alias or "sucursal").replace(" ", "_")}_{ahora:%Y%m%d_%H%M%S}.json')


@login_required
@solo_administrador
@require_http_methods(["POST"])
def importar_permisos_sucursal(request):
    """Importa restricciones de sucursal desde un JSON exportado por esta pantalla."""
    sucursal = Sucursal.objects.filter(
        id=request.POST.get('sucursal_id') or request.GET.get('sucursal_id') or 0
    ).first()
    if sucursal is None:
        return _json_error('Debe especificar una sucursal destino válida')
    try:
        if request.FILES.get('archivo'):
            data = json.loads(request.FILES['archivo'].read().decode('utf-8'))
        else:
            data = _leer_json(request)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return _json_error(f'Error al leer el archivo JSON: {e}')
    if data.get('tipo') != 'permisos_sucursal':
        return _json_error('Tipo de archivo no compatible con permisos de sucursal')

    sobrescribir = _bool(data.get('sobrescribir'), default=True)
    resultados = {'creados': 0, 'actualizados': 0, 'omitidos': 0, 'errores': []}
    opciones = {o.codigo: o for o in OpcionMenu.objects.filter(activo=True)}
    with transaction.atomic():
        existentes = {p.opcion_menu_id: p for p in PermisoSucursal.objects.filter(sucursal=sucursal)}
        for item in data.get('permisos') or []:
            codigo = item.get('opcion_codigo')
            opcion = opciones.get(codigo)
            if opcion is None:
                resultados['errores'].append(f'Opción no encontrada: {codigo}')
                continue
            permiso = existentes.get(opcion.id)
            if permiso is not None and not sobrescribir:
                resultados['omitidos'] += 1
                continue
            nuevo = permiso is None
            if nuevo:
                permiso = PermisoSucursal(sucursal=sucursal, opcion_menu=opcion, **SUCURSAL_SIN_RESTRICCION)
            valores = item.get('permisos') or {}
            for tipo in TIPOS_PERMISO_SUCURSAL:
                if tipo in valores:
                    setattr(permiso, tipo, _bool(valores[tipo], default=True))
            notas = item.get('notas') or ''
            permiso.notas = f"Importado: {notas}" if notas else "Importado desde archivo"
            permiso.save()
            resultados['creados' if nuevo else 'actualizados'] += 1

    logger.info('Permisos: %s importó restricciones a %s', request.user.username, sucursal.alias)
    return JsonResponse({
        'success': True,
        'mensaje': f'Permisos importados a {sucursal.alias}',
        'resultados': resultados,
    })


# ---------------------------------------------------------------------------
# Permisos por usuario (overrides)
# ---------------------------------------------------------------------------

def _sucursales_asignadas(usuario_ids):
    asignadas = defaultdict(list)
    for eu in (EmpresaUser.objects.filter(user_id__in=usuario_ids, status=True, sucursal__isnull=False)
               .values('user_id', 'sucursal_id', 'sucursal__alias', 'active')):
        asignadas[eu['user_id']].append({
            'id': eu['sucursal_id'], 'alias': eu['sucursal__alias'], 'activa': eu['active'],
        })
    return asignadas


@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_usuarios_permisos(request):
    """Usuarios activos con su cantidad de overrides y sucursales asignadas."""
    usuarios = list(Usuario.objects.filter(es_activo=True).order_by('first_name', 'last_name'))
    ids = [u.id for u in usuarios]
    overrides = dict(
        PermisoUsuario.objects.filter(usuario_id__in=ids).values_list('usuario_id').annotate(n=Count('id'))
    )
    ve_todas = set(
        PermisoUsuario.objects.filter(usuario_id__in=ids, puede_ver_todas_sucursales=True)
        .values_list('usuario_id', flat=True)
    )
    asignadas = _sucursales_asignadas(ids)
    return JsonResponse({'success': True, 'usuarios': [{
        'id': u.id,
        'username': u.username,
        'nombre': u.get_full_name() or u.username,
        'rol': u.rol,
        'rol_display': u.get_rol_display(),
        'overrides': overrides.get(u.id, 0),
        've_todas_sucursales': u.id in ve_todas,
        'sucursales_asignadas': [s['alias'] for s in asignadas.get(u.id, [])],
        'editable': _motivo_usuario_no_editable(request.user, u) is None,
    } for u in usuarios]})


@login_required
@solo_administrador
@require_http_methods(["GET"])
def obtener_permisos_usuario(request):
    """Overrides de un usuario + el permiso EFECTIVO resultante en una sucursal.

    `sucursal_id` (opcional) simula la sucursal activa; por defecto se usa la
    sucursal activa del usuario. El efectivo explica el motivo de cada
    resultado (MAESTRO, OVERRIDE_SI/NO, ROL, ROL_NO, SIN_FILA_ROL,
    SUCURSAL_BLOQUEA), igual que resuelve el middleware.
    """
    usuario = Usuario.objects.filter(id=request.GET.get('usuario_id') or 0).first()
    if usuario is None:
        return _json_error('Usuario no encontrado', status=404)

    asignadas = _sucursales_asignadas([usuario.id]).get(usuario.id, [])
    sucursal_id = request.GET.get('sucursal_id')
    if sucursal_id in (None, ''):
        activa = next((s for s in asignadas if s['activa']), asignadas[0] if asignadas else None)
        sucursal_id = activa['id'] if activa else None
    else:
        try:
            sucursal_id = int(sucursal_id) or None
        except (TypeError, ValueError):
            sucursal_id = None

    rol, overrides, sucursal = _cargar_capas_usuario(usuario, sucursal_id)
    motivo = _motivo_usuario_no_editable(request.user, usuario)

    def construir(op):
        info = _info_opcion(op)
        fila_rol = rol.get(op.id)
        override = overrides.get(op.id)
        info['permisos_rol'] = _flags(fila_rol, TIPOS_PERMISO, {t: False for t in TIPOS_PERMISO})
        info['rol_sin_fila'] = fila_rol is None
        info['overrides'] = {t: (getattr(override, t) if override else None) for t in TIPOS_PERMISO}
        # Lo que permite la sucursal simulada (para recalcular el efectivo en
        # la pantalla al tocar un override, sin volver a consultar).
        fila_suc = sucursal.get(op.id)
        info['sucursal_permite'] = {
            t: (True if fila_suc is None else bool(getattr(fila_suc, 'habilitado' if t == 'puede_ver' else t)))
            for t in TIPOS_PERMISO
        }
        info['notas'] = (override.notas or '') if override else ''
        efectivo = {}
        for tipo in TIPOS_PERMISO:
            valor, razon = _permiso_efectivo(usuario, op.id, tipo, rol, overrides, sucursal)
            efectivo[tipo] = {'valor': valor, 'motivo': razon}
        info['efectivo'] = efectivo
        return info

    return JsonResponse({
        'success': True,
        'usuario': {
            'id': usuario.id,
            'username': usuario.username,
            'nombre': usuario.get_full_name() or usuario.username,
            'rol': usuario.rol,
            'rol_display': usuario.get_rol_display(),
            'es_maestro': es_maestro(usuario),
        },
        'editable': motivo is None,
        'motivo_bloqueo': motivo or '',
        'sucursal_simulada': sucursal_id,
        'sucursales_asignadas': asignadas,
        've_todas_sucursales': PermisoUsuario.usuario_ve_todas_sucursales(usuario),
        'modulos': _arbol_modulos(construir),
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def guardar_permisos_usuario(request):
    """Guarda los overrides de un usuario (True = otorgar, False = denegar, None = usar rol)."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('JSON inválido')

    usuario = Usuario.objects.filter(id=data.get('usuario_id') or 0).first()
    if usuario is None:
        return _json_error('Usuario no encontrado', status=404)
    motivo = _motivo_usuario_no_editable(request.user, usuario)
    if motivo:
        return _json_error(motivo, status=403)

    ve_todas = _bool(data.get('ve_todas_sucursales'))
    permisos_data = data.get('permisos') or []
    ids = []
    for item in permisos_data:
        try:
            ids.append(int(item.get('opcion_id')))
        except (TypeError, ValueError):
            continue
    opciones = {o.id: o for o in OpcionMenu.objects.filter(id__in=ids)}

    creados = actualizados = eliminados = 0
    with transaction.atomic():
        existentes = {p.opcion_menu_id: p for p in PermisoUsuario.objects.select_for_update().filter(usuario=usuario)}
        for item in permisos_data:
            try:
                opcion = opciones.get(int(item.get('opcion_id')))
            except (TypeError, ValueError):
                opcion = None
            if opcion is None:
                continue
            valores = item.get('overrides') or {}
            limpio = {t: (None if valores.get(t) is None else _bool(valores.get(t))) for t in TIPOS_PERMISO}
            permiso = existentes.get(opcion.id)
            if all(v is None for v in limpio.values()):
                if permiso is not None:
                    permiso.delete()
                    eliminados += 1
                continue
            if permiso is None:
                permiso = PermisoUsuario(usuario=usuario, opcion_menu=opcion)
                creados += 1
            else:
                actualizados += 1
            for tipo, valor in limpio.items():
                setattr(permiso, tipo, valor)
            permiso.puede_ver_todas_sucursales = ve_todas
            permiso.notas = (item.get('notas') or '').strip()
            permiso.save()

        PermisoUsuario.objects.filter(usuario=usuario).update(puede_ver_todas_sucursales=ve_todas)
        # El flag "ver todas las sucursales" vive en las filas de override: sin
        # ninguna, se crea una neutra (todo None) para poder guardarlo.
        if ve_todas and not PermisoUsuario.objects.filter(usuario=usuario).exists():
            primera = OpcionMenu.objects.filter(activo=True).first()
            if primera:
                PermisoUsuario.objects.create(usuario=usuario, opcion_menu=primera,
                                              puede_ver_todas_sucursales=True)
                creados += 1

    logger.info('Permisos: %s guardó overrides de %s (%d creados, %d actualizados, %d eliminados, ve_todas=%s)',
                request.user.username, usuario.username, creados, actualizados, eliminados, ve_todas)
    return JsonResponse({
        'success': True,
        'mensaje': 'Permisos de usuario guardados',
        'creados': creados,
        'actualizados': actualizados,
        'eliminados': eliminados,
    })


@login_required
@solo_administrador
@require_http_methods(["POST"])
def eliminar_permisos_usuario(request):
    """Elimina todos los overrides de un usuario (vuelve a usar su rol)."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('JSON inválido')
    usuario = Usuario.objects.filter(id=data.get('usuario_id') or 0).first()
    if usuario is None:
        return _json_error('Usuario no encontrado', status=404)
    motivo = _motivo_usuario_no_editable(request.user, usuario)
    if motivo:
        return _json_error(motivo, status=403)

    deleted, _ = PermisoUsuario.objects.filter(usuario=usuario).delete()
    logger.info('Permisos: %s limpió %d overrides de %s', request.user.username, deleted, usuario.username)
    return JsonResponse({'success': True, 'mensaje': f'{deleted} overrides eliminados', 'eliminados': deleted})


@login_required
@solo_administrador
@require_http_methods(["POST"])
def copiar_permisos_usuario(request):
    """Copia los overrides de un usuario a otro (antes se perdía Aprobar)."""
    try:
        data = _leer_json(request)
    except json.JSONDecodeError:
        return _json_error('JSON inválido')
    origen_id = data.get('usuario_origen_id')
    destino = Usuario.objects.filter(id=data.get('usuario_destino_id') or 0).first()
    if not origen_id or destino is None:
        return _json_error('IDs origen y destino requeridos')
    if str(origen_id) == str(destino.id):
        return _json_error('Origen y destino no pueden ser iguales')
    motivo = _motivo_usuario_no_editable(request.user, destino)
    if motivo:
        return _json_error(motivo, status=403)

    creados = actualizados = 0
    with transaction.atomic():
        existentes = {p.opcion_menu_id: p for p in PermisoUsuario.objects.filter(usuario=destino)}
        for p_orig in PermisoUsuario.objects.filter(usuario_id=origen_id):
            p_dest = existentes.get(p_orig.opcion_menu_id)
            if p_dest is None:
                p_dest = PermisoUsuario(usuario=destino, opcion_menu_id=p_orig.opcion_menu_id)
                creados += 1
            else:
                actualizados += 1
            for tipo in TIPOS_PERMISO:
                setattr(p_dest, tipo, getattr(p_orig, tipo))
            p_dest.puede_ver_todas_sucursales = p_orig.puede_ver_todas_sucursales
            p_dest.notas = f"Copiado de usuario ID {origen_id}"
            p_dest.save()

    logger.info('Permisos: %s copió overrides de usuario %s -> %s', request.user.username, origen_id, destino.username)
    return JsonResponse({
        'success': True,
        'mensaje': f'Permisos copiados: {creados} creados, {actualizados} actualizados',
        'creados': creados,
        'actualizados': actualizados,
    })


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------

def _parece_mal_codificado(texto):
    """'Gesti?n', 'MÃ³dulo': acentos rotos por un archivo guardado sin UTF-8."""
    return bool(texto) and ('?' in texto or 'Ã' in texto or '�' in texto)


@login_required
@solo_administrador
@require_http_methods(["GET"])
def diagnostico_permisos(request):
    """Revisión de salud del sistema de permisos, para la pestaña Diagnóstico.

    Solo lectura. Detecta lo que en la práctica deja a alguien sin acceso sin
    que la pantalla de roles lo muestre: opciones sin fila para un rol, URLs
    protegidas por códigos que no existen, sucursales que bloquean incluso al
    administrador, nombres con acentos rotos y usuarios sin sucursal.
    """
    from .middleware_permisos import URL_PERMISO_MAP

    opciones = list(OpcionMenu.objects.filter(activo=True).select_related('modulo').order_by('modulo__orden', 'orden'))
    por_id = {o.id: o for o in opciones}
    codigos_activos = {o.codigo for o in opciones}
    codigos_inactivos = set(OpcionMenu.objects.filter(activo=False).values_list('codigo', flat=True))

    # 1. Por rol: opciones sin fila (= sin acceso) y con acceso.
    filas_rol = defaultdict(dict)
    for p in PermisoRol.objects.filter(opcion_menu__activo=True).values('rol', 'opcion_menu_id', 'puede_ver'):
        filas_rol[p['rol']][p['opcion_menu_id']] = p['puede_ver']
    usuarios_por_rol = dict(
        Usuario.objects.filter(es_activo=True, is_active=True).values_list('rol').annotate(n=Count('id'))
    )
    roles = []
    for rol, nombre in PermisoRol.ROLES_CHOICES:
        filas = filas_rol.get(rol, {})
        sin_fila = [] if rol == ROL_MAESTRO else [
            {'codigo': o.codigo, 'nombre': o.nombre, 'modulo': o.modulo.nombre}
            for o in opciones if o.id not in filas
        ]
        roles.append({
            'rol': rol,
            'nombre': nombre,
            'usuarios': usuarios_por_rol.get(rol, 0),
            'con_acceso': len(opciones) if rol == ROL_MAESTRO else sum(1 for v in filas.values() if v),
            'total': len(opciones),
            'sin_fila': sin_fila,
        })

    # 2. URLs protegidas por códigos que no existen o están inactivos: el
    #    middleware las cierra para todos (salvo el Maestro).
    urls_huerfanas = []
    for url, codigo in sorted(URL_PERMISO_MAP.items()):
        if codigo not in codigos_activos:
            urls_huerfanas.append({
                'url': url, 'codigo': codigo,
                'estado': 'INACTIVA' if codigo in codigos_inactivos else 'NO_EXISTE',
            })

    # 3. Sucursales que restringen (incluso al Administrador).
    restricciones = defaultdict(lambda: {'deshabilitadas': [], 'sin_crear': 0, 'sin_editar': 0,
                                         'sin_eliminar': 0, 'sin_aprobar': 0})
    for p in PermisoSucursal.objects.filter(opcion_menu__activo=True).select_related('sucursal'):
        r = restricciones[p.sucursal_id]
        r['alias'] = p.sucursal.alias
        opcion = por_id.get(p.opcion_menu_id)
        if not p.habilitado and opcion:
            r['deshabilitadas'].append(opcion.nombre)
        r['sin_crear'] += 0 if p.puede_crear else 1
        r['sin_editar'] += 0 if p.puede_editar else 1
        r['sin_eliminar'] += 0 if p.puede_eliminar else 1
        r['sin_aprobar'] += 0 if p.puede_aprobar else 1
    sucursales = sorted(
        ({'sucursal_id': sid, **r} for sid, r in restricciones.items()
         if r['deshabilitadas'] or r['sin_crear'] or r['sin_editar'] or r['sin_eliminar'] or r['sin_aprobar']),
        key=lambda x: x.get('alias') or '',
    )

    # 4. Nombres con acentos rotos.
    mal_codificados = [
        {'tipo': 'Módulo', 'codigo': m.codigo, 'nombre': m.nombre}
        for m in ModuloSistema.objects.filter(activo=True) if _parece_mal_codificado(m.nombre)
    ] + [
        {'tipo': 'Opción', 'codigo': o.codigo, 'nombre': o.nombre}
        for o in opciones if _parece_mal_codificado(o.nombre)
    ]

    # 5. Usuarios.
    activos = Usuario.objects.filter(es_activo=True, is_active=True)
    con_sucursal = set(
        EmpresaUser.objects.filter(status=True, sucursal__isnull=False).values_list('user_id', flat=True)
    )
    sin_sucursal = [
        {'id': u.id, 'nombre': u.get_full_name() or u.username, 'rol': u.get_rol_display()}
        for u in activos if u.id not in con_sucursal
    ]
    con_overrides = [
        {'id': r['usuario_id'], 'nombre': f"{r['usuario__first_name']} {r['usuario__last_name']}".strip()
         or r['usuario__username'], 'rol': r['usuario__rol'], 'overrides': r['n']}
        for r in PermisoUsuario.objects.filter(usuario__es_activo=True)
        .values('usuario_id', 'usuario__first_name', 'usuario__last_name', 'usuario__username', 'usuario__rol')
        .annotate(n=Count('id')).order_by('-n')
    ]
    roles_validos = set(ROLES_VALIDOS)
    rol_invalido = [
        {'id': u.id, 'nombre': u.get_full_name() or u.username, 'rol': u.rol}
        for u in activos if u.rol not in roles_validos
    ]

    alertas = []
    maestros = usuarios_por_rol.get(ROL_MAESTRO, 0)
    if maestros == 0:
        alertas.append({'nivel': 'danger', 'texto': 'Nadie tiene el rol Maestro. Asígnalo a la cuenta del dueño '
                        '(comando configurar_rol_maestro) para poder administrar al rol Administrador.'})
    admin = next((r for r in roles if r['rol'] == 'administrador'), None)
    if admin and admin['sin_fila']:
        alertas.append({'nivel': 'warning', 'texto': f'El rol Administrador no tiene fila en {len(admin["sin_fila"])} '
                        f'opción(es): no las ve. Si no fue a propósito, corre inicializar_permisos.'})
    if urls_huerfanas:
        alertas.append({'nivel': 'danger', 'texto': f'{len(urls_huerfanas)} URL(s) protegidas por códigos que no existen '
                        'o están inactivos: dan "Acceso denegado" a todos menos al Maestro.'})
    if mal_codificados:
        alertas.append({'nivel': 'info', 'texto': f'{len(mal_codificados)} nombre(s) de menú con acentos rotos.'})
    if sin_sucursal:
        alertas.append({'nivel': 'warning', 'texto': f'{len(sin_sucursal)} usuario(s) activo(s) sin sucursal asignada.'})
    if rol_invalido:
        alertas.append({'nivel': 'danger', 'texto': f'{len(rol_invalido)} usuario(s) con un rol que no existe: '
                        'no pasan ningún permiso.'})

    return JsonResponse({
        'success': True,
        'alertas': alertas,
        'roles': roles,
        'urls_huerfanas': urls_huerfanas,
        'sucursales': sucursales,
        'mal_codificados': mal_codificados,
        'usuarios_sin_sucursal': sin_sucursal,
        'usuarios_con_overrides': con_overrides,
        'usuarios_rol_invalido': rol_invalido,
        'maestros': maestros,
    })
