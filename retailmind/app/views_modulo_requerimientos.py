"""
Módulo de Requerimientos - RetailMind
Gestión completa de requerimientos de garantías, devoluciones y reclamos
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse, Http404, HttpResponseBadRequest, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Count, Q, Avg
from django.core.paginator import Paginator
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import transaction
from django.core.mail import send_mail, EmailMessage
from django.conf import settings
from django.core.files.storage import default_storage
from django.template.loader import render_to_string
import json
import re
import logging
from decimal import Decimal
from datetime import datetime, timedelta

from .models import (
    Producto, Producto_Talla, Sucursal, EmpresaUser, Empresa,
    Requerimiento, FotoRequerimiento, HistorialRequerimiento,
    TipoFotoRequerimiento, MAX_FOTOS_POR_TIPO,
    Ticket, Dte, Dte_Productos
)

logger = logging.getLogger('app')


# ========== SISTEMA DE PERMISOS ==========

def obtener_rol_usuario(user):
    """Obtiene el rol del usuario"""
    if hasattr(user, 'rol'):
        return user.rol

    return 'vendedor'  # Por defecto


def usuario_puede_realizar_accion(user, requerimiento, accion):
    """
    Valida si el usuario puede realizar una acción sobre el requerimiento
    
    Roles:
    - administrador: Puede hacer TODO
    - jefe_local (Supervisor): Puede gestionar su sucursal
    - cajero/vendedor: Solo puede ver y crear
    """
    rol = obtener_rol_usuario(user)
    
    # Administrador puede todo
    if rol == 'administrador':
        return True
    
    # Jefe Local (Supervisor)
    if rol == 'jefe_local':
        # Obtener sucursal del usuario
        empresa_user = EmpresaUser.objects.filter(user=user).first()
        if not empresa_user or not empresa_user.sucursal:
            return False
        
        # Solo puede gestionar requerimientos de su sucursal
        if requerimiento.sucursal != empresa_user.sucursal:
            return False
        
        # Acciones permitidas para supervisor
        acciones_permitidas = [
            'ver', 'revisar', 'aprobar_simple', 'rechazar_simple',
            'comentar', 'escalar', 'asignar'
        ]
        if accion in acciones_permitidas:
            return True
        
        # NO puede enviar a proveedor ni aprobar casos complejos
        if accion in ['enviar_proveedor', 'registrar_respuesta_proveedor']:
            return False
        
        return False
    
    # Cajero/Vendedor
    if rol in ['cajero', 'vendedor']:
        # Solo puede ver sus propios requerimientos y crear nuevos
        if accion == 'crear':
            return True
        if accion == 'ver':
            return requerimiento.usuario_creador == user or requerimiento.sucursal in obtener_sucursales_usuario(user)
        if accion == 'editar' or accion == 'cancelar':
            return requerimiento.usuario_creador == user and requerimiento.estado == 'PENDIENTE'
        
        return False
    
    return False


def obtener_sucursales_usuario(user):
    """Obtiene las sucursales a las que el usuario tiene acceso"""
    return Sucursal.objects.filter(
        empresa__empresauser__user=user
    )


TRANSICIONES_PERMITIDAS = {
    'PENDIENTE': ['EN_REVISION', 'ESPERANDO_RESPUESTA', 'CANCELADO'],
    'EN_REVISION': ['ESPERANDO_RESPUESTA', 'APROBADO', 'RECHAZADO', 'CANCELADO'],
    'ESPERANDO_RESPUESTA': ['APROBADO', 'RECHAZADO'],
    'APROBADO': ['EN_PROCESO', 'COMPLETADO'],
    'EN_PROCESO': ['COMPLETADO'],
    'RECHAZADO': [],
    'COMPLETADO': [],
    'CANCELADO': [],
}


def puede_cambiar_estado(estado_actual, estado_nuevo):
    """Valida si la transición de estado es permitida"""
    if estado_actual == estado_nuevo:
        return True
    return estado_nuevo in TRANSICIONES_PERMITIDAS.get(estado_actual, [])


# ========== VISTAS PRINCIPALES ==========

@login_required
def modulo_requerimientos(request):
    """Vista principal del módulo de requerimientos"""
    # Obtener rol del usuario
    rol_usuario = obtener_rol_usuario(request.user)
    sucursales = Sucursal.objects.filter(empresa__empresauser__user=request.user)
    proveedores = Empresa.objects.filter(esProveedor=True)
    
    context = {
        'rol_usuario': rol_usuario,
        'sucursales': sucursales,
        'proveedores': proveedores,
    }
    
    return render(request, 'vistas/modulo_requerimientos/gestion_requerimientos.html', context)


@login_required
def crear_requerimiento_vista(request):
    """Vista para crear nuevo requerimiento"""
    url_base = reverse('modulo_requerimientos')
    return redirect(f"{url_base}?panel=crear")


@login_required
def detalle_requerimiento_vista(request, requerimiento_id):
    """Vista de detalle de un requerimiento"""
    requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
    
    context = {
        'requerimiento': requerimiento,
    }
    return render(request, 'vistas/modulo_requerimientos/detalle_requerimiento.html', context)


@login_required
def gestionar_requerimientos_vista(request):
    """Vista para gestionar requerimientos (administrador)"""
    return redirect('modulo_requerimientos')


# ========== APIs DE CREACIÓN Y GESTIÓN ==========

@login_required
@require_POST
def crear_requerimiento(request):
    """API para crear nuevo requerimiento"""
    try:
        # Obtener datos del formulario o JSON
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST
        
        # Validar datos requeridos
        campos_requeridos = ['tipo', 'sku', 'nombre_producto', 'cliente_nombre', 'motivo']
        for campo in campos_requeridos:
            if not data.get(campo):
                return JsonResponse({
                    'success': False,
                    'error': f'El campo {campo} es requerido'
                }, status=400)
        
        # Obtener sucursal actual
        sucursal_id = request.session.get('idSucursalActual')
        if not sucursal_id:
            return JsonResponse({
                'success': False,
                'error': 'No se ha seleccionado una sucursal'
            }, status=400)
        
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Buscar producto_talla por SKU
        producto_talla = None
        try:
            # Usar filter().first() para evitar error si hay duplicados
            producto_talla = Producto_Talla.objects.filter(sku=data.get('sku')).first()
        except Exception:
            pass  # El producto puede no existir en el sistema
        
        # Crear requerimiento
        tipo_req = data.get('tipo')
        with transaction.atomic():
            requerimiento = Requerimiento.objects.create(
                tipo=tipo_req,
                subtipo=data.get('subtipo', '') or None,
                sucursal=sucursal,
                usuario_creador=request.user,
                producto_talla=producto_talla,
                sku=data.get('sku'),
                nombre_producto=data.get('nombre_producto'),
                numero_boleta=data.get('numero_boleta', ''),
                tipo_documento=data.get('tipo_documento', ''),
                fecha_compra=data.get('fecha_compra') if data.get('fecha_compra') else None,
                cliente_rut=data.get('cliente_rut', ''),
                cliente_nombre=data.get('cliente_nombre'),
                cliente_telefono=data.get('cliente_telefono', ''),
                cliente_email=data.get('cliente_email', ''),
                motivo=data.get('motivo'),
                descripcion_problema=data.get('descripcion_problema', ''),
                prioridad=data.get('prioridad', 'MEDIA'),
                proveedor_id=data.get('proveedor_id') if data.get('proveedor_id') else None,
                severidad_defecto=data.get('severidad_defecto', '') or None,
                condicion_producto=data.get('condicion_producto', '') or None,
                producto_esperado=data.get('producto_esperado', '') or None,
            )

            # Registrar en historial
            HistorialRequerimiento.objects.create(
                requerimiento=requerimiento,
                accion='CREADO',
                estado_nuevo='PENDIENTE',
                comentario='Requerimiento creado',
                usuario=request.user
            )

            # Procesar fotos por tipo (foto_FOTO_GENERAL, foto_FOTO_DEFECTO, etc.)
            max_fotos = MAX_FOTOS_POR_TIPO.get(tipo_req, 5)
            orden_counter = 1
            if request.FILES:
                # Fotos con tipo definido
                tipos_foto_db = {
                    tf.codigo: tf for tf in TipoFotoRequerimiento.objects.filter(activo=True)
                }
                for key in request.FILES:
                    if orden_counter > max_fotos:
                        break
                    tipo_foto_obj = None
                    if key.startswith('foto_FOTO_'):
                        codigo = key.replace('foto_', '', 1)
                        tipo_foto_obj = tipos_foto_db.get(codigo)
                    elif key.startswith('foto_adicional_'):
                        tipo_foto_obj = tipos_foto_db.get('FOTO_ADICIONAL')
                    elif key.startswith('foto_'):
                        # Retrocompatibilidad: foto_1, foto_2, etc.
                        pass
                    else:
                        continue

                    desc_key = f'descripcion_{key}'
                    FotoRequerimiento.objects.create(
                        requerimiento=requerimiento,
                        imagen=request.FILES[key],
                        tipo_foto=tipo_foto_obj,
                        descripcion=data.get(desc_key, '') or '',
                        orden=orden_counter,
                        usuario=request.user
                    )
                    orden_counter += 1

            # Verificar completitud de fotos obligatorias
            requerimiento.fotos_completas = requerimiento.verificar_fotos_completas()
            requerimiento.save(update_fields=['fotos_completas'])
        
        return JsonResponse({
            'success': True,
            'message': 'Requerimiento creado exitosamente',
            'requerimiento_id': requerimiento.id,
            'numero_requerimiento': requerimiento.numero_requerimiento
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear requerimiento: {str(e)}'
        }, status=500)


@login_required
def listar_requerimientos(request):
    """Listar requerimientos con filtros según rol del usuario"""
    try:
        # Parámetros de filtro
        estado = request.GET.get('estado')
        tipo = request.GET.get('tipo')
        prioridad = request.GET.get('prioridad')
        sucursal_id = request.GET.get('sucursal_id')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        busqueda = request.GET.get('busqueda', '')
        urgencia = request.GET.get('urgencia')  # Nueva: filtro por urgencia
        sin_respuesta = request.GET.get('sin_respuesta')  # Nueva: > 7 días sin respuesta
        
        # Parámetros de paginación
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))
        
        # Query base
        requerimientos = Requerimiento.objects.select_related(
            'sucursal', 'usuario_creador', 'proveedor', 'producto_talla', 'asignado_a'
        ).prefetch_related('fotos')
        
        # Filtrar según rol del usuario
        rol_usuario = obtener_rol_usuario(request.user)
        
        if rol_usuario == 'administrador':
            # Administrador ve TODO
            pass
        elif rol_usuario == 'jefe_local':
            # Supervisor solo ve su sucursal
            empresa_user = EmpresaUser.objects.filter(user=request.user).first()
            if empresa_user and empresa_user.sucursal:
                requerimientos = requerimientos.filter(sucursal=empresa_user.sucursal)
            else:
                requerimientos = requerimientos.none()
        else:
            # Cajero/Vendedor solo ve sus requerimientos y los de su sucursal
            sucursales_usuario = obtener_sucursales_usuario(request.user)
            requerimientos = requerimientos.filter(
                Q(usuario_creador=request.user) | Q(sucursal__in=sucursales_usuario)
            )
        
        # Aplicar filtros
        if estado:
            requerimientos = requerimientos.filter(estado=estado)
        if tipo:
            requerimientos = requerimientos.filter(tipo=tipo)
        if prioridad:
            requerimientos = requerimientos.filter(prioridad=prioridad)
        if sucursal_id:
            requerimientos = requerimientos.filter(sucursal_id=sucursal_id)
        if fecha_inicio:
            try:
                dt_inicio = timezone.make_aware(datetime.strptime(fecha_inicio, '%Y-%m-%d'))
                requerimientos = requerimientos.filter(fecha_creacion__gte=dt_inicio)
            except (ValueError, TypeError):
                pass
        if fecha_fin:
            try:
                dt_fin = timezone.make_aware(datetime.strptime(fecha_fin, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
                requerimientos = requerimientos.filter(fecha_creacion__lte=dt_fin)
            except (ValueError, TypeError):
                pass
        if busqueda:
            requerimientos = requerimientos.filter(
                Q(numero_requerimiento__icontains=busqueda) |
                Q(sku__icontains=busqueda) |
                Q(cliente_nombre__icontains=busqueda) |
                Q(cliente_rut__icontains=busqueda) |
                Q(numero_boleta__icontains=busqueda)
            )
        
        # Filtros especiales de seguimiento
        if sin_respuesta == 'true':
            # Requerimientos esperando proveedor sin respuesta > 7 días
            fecha_limite = timezone.now() - timedelta(days=7)
            requerimientos = requerimientos.filter(
                estado='ESPERANDO_RESPUESTA',
                fecha_envio_proveedor__lt=fecha_limite,
                fecha_respuesta_proveedor__isnull=True
            )
        
        # Paginación
        paginator = Paginator(requerimientos, page_size)
        page_obj = paginator.get_page(page)
        
        # Serializar resultados
        requerimientos_data = []
        for req in page_obj:
            requerimientos_data.append({
                'id': req.id,
                'numero_requerimiento': req.numero_requerimiento,
                'tipo': req.get_tipo_display(),
                'tipo_codigo': req.tipo,
                'subtipo': req.subtipo or '',
                'estado': req.get_estado_display(),
                'estado_codigo': req.estado,
                'prioridad': req.get_prioridad_display(),
                'prioridad_codigo': req.prioridad,
                'sucursal': req.sucursal.alias,
                'sku': req.sku,
                'nombre_producto': req.nombre_producto,
                'cliente_nombre': req.cliente_nombre,
                'fecha_creacion': req.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'dias_transcurridos': req.dias_transcurridos,
                'cantidad_fotos': req.cantidad_fotos,
                'fotos_completas': req.fotos_completas,
                'max_fotos': req.max_fotos,
                'usuario_creador': req.usuario_creador.get_full_name() if req.usuario_creador else '',
                'proveedor': req.proveedor.nombre if req.proveedor else '',
                'asignado_a': req.asignado_a.get_full_name() if req.asignado_a else '',
                'correo_enviado_proveedor': req.correo_enviado_proveedor,
                'dias_sin_respuesta': req.dias_sin_respuesta,
                'requiere_recordatorio': req.requiere_recordatorio,
                'nivel_urgencia': req.nivel_urgencia,
            })
        
        return JsonResponse({
            'success': True,
            'requerimientos': requerimientos_data,
            'pagination': {
                'current_page': page,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous(),
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener requerimientos: {str(e)}'
        }, status=500)


@login_required
def detalle_requerimiento(request, requerimiento_id):
    """Obtener detalles completos de un requerimiento"""
    try:
        requerimiento = get_object_or_404(
            Requerimiento.objects.select_related(
                'sucursal', 'usuario_creador', 'usuario_gestor', 'proveedor', 
                'producto_talla', 'asignado_a'
            ).prefetch_related('fotos', 'historial'),
            id=requerimiento_id
        )
        
        # Validar permisos de visualización
        if not usuario_puede_realizar_accion(request.user, requerimiento, 'ver'):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para ver este requerimiento'
            }, status=403)
        
        # Obtener rol del usuario actual
        rol_usuario = obtener_rol_usuario(request.user)
        
        # Serializar fotos con tipo
        fotos = []
        for foto in requerimiento.fotos.select_related('tipo_foto').all():
            fotos.append({
                'id': foto.id,
                'url': foto.imagen.url if foto.imagen else '',
                'descripcion': foto.descripcion or '',
                'orden': foto.orden,
                'fecha': foto.fecha_subida.strftime('%d/%m/%Y %H:%M'),
                'tipo_foto_codigo': foto.tipo_foto.codigo if foto.tipo_foto else None,
                'tipo_foto_nombre': foto.tipo_foto.nombre if foto.tipo_foto else 'Sin clasificar',
                'tipo_foto_icono': foto.tipo_foto.icono if foto.tipo_foto else 'ri-image-line',
            })
        
        # Serializar historial
        historial = []
        for hist in requerimiento.historial.all():
            historial.append({
                'id': hist.id,
                'accion': hist.accion,
                'estado_anterior': hist.estado_anterior,
                'estado_nuevo': hist.estado_nuevo,
                'comentario': hist.comentario or '',
                'usuario': hist.usuario.get_full_name() if hist.usuario else '',
                'fecha': hist.fecha.strftime('%d/%m/%Y %H:%M')
            })
        
        requerimiento_data = {
            'id': requerimiento.id,
            'numero_requerimiento': requerimiento.numero_requerimiento,
            'tipo': requerimiento.get_tipo_display(),
            'tipo_codigo': requerimiento.tipo,
            'estado': requerimiento.get_estado_display(),
            'estado_codigo': requerimiento.estado,
            'prioridad': requerimiento.get_prioridad_display(),
            'prioridad_codigo': requerimiento.prioridad,
            
            # Sucursal y usuarios
            'sucursal': {
                'id': requerimiento.sucursal.id,
                'nombre': requerimiento.sucursal.alias
            },
            'usuario_creador': requerimiento.usuario_creador.get_full_name() if requerimiento.usuario_creador else '',
            'usuario_gestor': requerimiento.usuario_gestor.get_full_name() if requerimiento.usuario_gestor else '',
            'asignado_a': requerimiento.asignado_a.get_full_name() if requerimiento.asignado_a else '',
            'asignado_a_id': requerimiento.asignado_a.id if requerimiento.asignado_a else None,
            
            # Producto
            'sku': requerimiento.sku,
            'nombre_producto': requerimiento.nombre_producto,
            
            # Documento
            'tipo_documento': requerimiento.tipo_documento or '',
            'numero_boleta': requerimiento.numero_boleta or '',
            'fecha_compra': requerimiento.fecha_compra.strftime('%d/%m/%Y') if requerimiento.fecha_compra else '',
            
            # Cliente
            'cliente_nombre': requerimiento.cliente_nombre,
            'cliente_rut': requerimiento.cliente_rut or '',
            'cliente_telefono': requerimiento.cliente_telefono or '',
            'cliente_email': requerimiento.cliente_email or '',
            
            # Descripcion
            'motivo': requerimiento.motivo,
            'descripcion_problema': requerimiento.descripcion_problema or '',

            # Clasificacion de defecto
            'subtipo': requerimiento.subtipo or '',
            'severidad_defecto': requerimiento.get_severidad_defecto_display() if requerimiento.severidad_defecto else '',
            'severidad_defecto_codigo': requerimiento.severidad_defecto or '',
            'condicion_producto': requerimiento.get_condicion_producto_display() if requerimiento.condicion_producto else '',
            'condicion_producto_codigo': requerimiento.condicion_producto or '',
            'producto_esperado': requerimiento.producto_esperado or '',
            'notas_internas': requerimiento.notas_internas or '' if rol_usuario in ['administrador'] else '',
            'fotos_completas': requerimiento.fotos_completas,
            'max_fotos': requerimiento.max_fotos,

            # Proveedor
            'proveedor': {
                'id': requerimiento.proveedor.id if requerimiento.proveedor else None,
                'nombre': requerimiento.proveedor.nombre if requerimiento.proveedor else ''
            },
            'correo_enviado_proveedor': requerimiento.correo_enviado_proveedor,
            'fecha_envio_proveedor': requerimiento.fecha_envio_proveedor.strftime('%d/%m/%Y %H:%M') if requerimiento.fecha_envio_proveedor else '',
            'correo_proveedor_destino': requerimiento.correo_proveedor_destino or '',
            'intentos_envio': requerimiento.intentos_envio,
            'dias_sin_respuesta': requerimiento.dias_sin_respuesta,
            'requiere_recordatorio': requerimiento.requiere_recordatorio,
            'respuesta_proveedor': requerimiento.respuesta_proveedor or '',
            'fecha_respuesta_proveedor': requerimiento.fecha_respuesta_proveedor.strftime('%d/%m/%Y %H:%M') if requerimiento.fecha_respuesta_proveedor else '',
            'decision_proveedor': requerimiento.decision_proveedor or '',
            
            # Resolución
            'resolucion': requerimiento.resolucion or '',
            'motivo_resolucion': requerimiento.motivo_resolucion or '',
            'fecha_resolucion': requerimiento.fecha_resolucion.strftime('%d/%m/%Y %H:%M') if requerimiento.fecha_resolucion else '',
            
            # Fechas
            'fecha_creacion': requerimiento.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
            'fecha_actualizacion': requerimiento.fecha_actualizacion.strftime('%d/%m/%Y %H:%M'),
            'dias_transcurridos': requerimiento.dias_transcurridos,
            'nivel_urgencia': requerimiento.nivel_urgencia,
            
            # Tipos de foto requeridos para este tipo de requerimiento
            'tipos_foto_requeridos': [
                {
                    'codigo': tf.codigo,
                    'nombre': tf.nombre,
                    'descripcion_guia': tf.descripcion_guia,
                    'icono': tf.icono,
                    'es_obligatorio': tf.es_obligatorio,
                }
                for tf in TipoFotoRequerimiento.objects.filter(
                    activo=True,
                    tipos_requerimiento__contains=[requerimiento.tipo],
                ).order_by('orden')
            ],

            # Relacionados
            'fotos': fotos,
            'historial': historial,
            
            # Permisos del usuario actual
            'permisos': {
                'puede_editar': usuario_puede_realizar_accion(request.user, requerimiento, 'editar'),
                'puede_revisar': usuario_puede_realizar_accion(request.user, requerimiento, 'revisar'),
                'puede_aprobar': usuario_puede_realizar_accion(request.user, requerimiento, 'aprobar_simple'),
                'puede_rechazar': usuario_puede_realizar_accion(request.user, requerimiento, 'rechazar_simple'),
                'puede_enviar_proveedor': usuario_puede_realizar_accion(request.user, requerimiento, 'enviar_proveedor'),
                'puede_registrar_respuesta': usuario_puede_realizar_accion(request.user, requerimiento, 'registrar_respuesta_proveedor'),
                'puede_completar': usuario_puede_realizar_accion(request.user, requerimiento, 'completar'),
                'puede_cancelar': usuario_puede_realizar_accion(request.user, requerimiento, 'cancelar'),
            },
            'rol_usuario': rol_usuario,
        }
        
        return JsonResponse({
            'success': True,
            'requerimiento': requerimiento_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener requerimiento: {str(e)}'
        }, status=500)


@login_required
@require_POST
def actualizar_estado_requerimiento(request, requerimiento_id):
    """Actualizar estado de un requerimiento con validación de permisos"""
    try:
        data = json.loads(request.body)
        
        nuevo_estado = data.get('estado')
        comentario = data.get('comentario', '')
        
        if not nuevo_estado:
            return JsonResponse({
                'success': False,
                'error': 'Nuevo estado es requerido'
            }, status=400)
        
        requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
        estado_anterior = requerimiento.estado
        
        # Validar permisos según estado y rol
        rol_usuario = obtener_rol_usuario(request.user)
        
        # Validar que la transición sea permitida
        if not puede_cambiar_estado(estado_anterior, nuevo_estado):
            return JsonResponse({
                'success': False,
                'error': f'No se puede cambiar de {estado_anterior} a {nuevo_estado}'
            }, status=400)
        
        # Validar permisos por rol
        if rol_usuario == 'jefe_local':
            # Supervisor solo puede aprobar/rechazar casos simples de su sucursal
            empresa_user = EmpresaUser.objects.filter(user=request.user).first()
            if empresa_user and empresa_user.sucursal != requerimiento.sucursal:
                return JsonResponse({
                    'success': False,
                    'error': 'Solo puede gestionar requerimientos de su sucursal'
                }, status=403)
            
            # Supervisor NO puede marcar como ESPERANDO_PROVEEDOR
            if nuevo_estado == 'ESPERANDO_PROVEEDOR':
                return JsonResponse({
                    'success': False,
                    'error': 'Solo administradores pueden enviar a proveedor'
                }, status=403)
        
        elif rol_usuario in ['cajero', 'vendedor']:
            # Vendedores solo pueden cancelar sus propios req pendientes
            if not (requerimiento.usuario_creador == request.user and nuevo_estado == 'CANCELADO'):
                return JsonResponse({
                    'success': False,
                    'error': 'No tiene permisos para cambiar el estado'
                }, status=403)
        
        with transaction.atomic():
            requerimiento.estado = nuevo_estado
            
            # Si cambia a EN_REVISION, asignar al usuario actual
            if nuevo_estado == 'EN_REVISION' and not requerimiento.asignado_a:
                requerimiento.asignado_a = request.user
            
            requerimiento.save()
            
            # Registrar en historial
            HistorialRequerimiento.objects.create(
                requerimiento=requerimiento,
                accion='CAMBIO_ESTADO',
                estado_anterior=estado_anterior,
                estado_nuevo=nuevo_estado,
                comentario=comentario,
                usuario=request.user
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Estado actualizado exitosamente',
            'nuevo_estado': requerimiento.get_estado_display()
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al actualizar estado: {str(e)}'
        }, status=500)


@login_required
@require_POST
def enviar_a_proveedor(request, requerimiento_id):
    """Enviar requerimiento al proveedor por correo con adjuntos"""
    try:
        data = json.loads(request.body)
        
        requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
        
        # Validar permisos (solo administrador)
        if not usuario_puede_realizar_accion(request.user, requerimiento, 'enviar_proveedor'):
            return JsonResponse({
                'success': False,
                'error': 'No tiene permisos para enviar a proveedor'
            }, status=403)
        
        if not requerimiento.proveedor:
            return JsonResponse({
                'success': False,
                'error': 'El requerimiento no tiene proveedor asignado'
            }, status=400)
        
        # Determinar correo destino
        correo_destino = data.get('correo_destino') or requerimiento.proveedor.correoVendedor
        
        if not correo_destino:
            return JsonResponse({
                'success': False,
                'error': 'El proveedor no tiene correo configurado'
            }, status=400)
        
        try:
            # Preparar asunto
            asunto = f'Requerimiento de {requerimiento.get_tipo_display()} - {requerimiento.numero_requerimiento}'
            
            # Preparar contexto para template
            context = {
                'requerimiento': requerimiento,
                'fotos': requerimiento.fotos.all(),
                'usuario': request.user,
                'empresa': requerimiento.sucursal.empresa,
            }
            
            # Renderizar HTML (usaremos template simple si no existe el complejo)
            try:
                html_message = render_to_string('emails/requerimiento_proveedor.html', context)
            except:
                # Fallback a mensaje de texto formateado
                html_message = f"""
                <html>
                <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <div style="background: #405189; color: white; padding: 20px; text-align: center;">
                        <h2>Requerimiento de {requerimiento.get_tipo_display()}</h2>
                        <p style="font-size: 24px; margin: 10px 0;">{requerimiento.numero_requerimiento}</p>
                    </div>
                    
                    <div style="padding: 20px; background: #f8f9fa; margin: 20px 0;">
                        <h3>Estimado proveedor {requerimiento.proveedor.nombre},</h3>
                        <p>Se ha generado un requerimiento que requiere su atención:</p>
                    </div>
                    
                    <div style="padding: 20px;">
                        <h4 style="color: #405189;">Información del Producto</h4>
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr><td style="padding: 8px; border-bottom: 1px solid #dee2e6;"><strong>SKU:</strong></td><td style="padding: 8px; border-bottom: 1px solid #dee2e6;">{requerimiento.sku}</td></tr>
                            <tr><td style="padding: 8px; border-bottom: 1px solid #dee2e6;"><strong>Producto:</strong></td><td style="padding: 8px; border-bottom: 1px solid #dee2e6;">{requerimiento.nombre_producto}</td></tr>
                            <tr><td style="padding: 8px; border-bottom: 1px solid #dee2e6;"><strong>Documento:</strong></td><td style="padding: 8px; border-bottom: 1px solid #dee2e6;">{requerimiento.tipo_documento or 'N/A'} {requerimiento.numero_boleta or ''}</td></tr>
                            <tr><td style="padding: 8px; border-bottom: 1px solid #dee2e6;"><strong>Fecha Compra:</strong></td><td style="padding: 8px; border-bottom: 1px solid #dee2e6;">{requerimiento.fecha_compra or 'N/A'}</td></tr>
                        </table>
                        
                        <h4 style="color: #405189; margin-top: 20px;">Descripción del Problema</h4>
                        <div style="background: #fff3cd; padding: 15px; border-left: 4px solid #ffc107;">
                            <p><strong>Motivo:</strong> {requerimiento.motivo}</p>
                            <p><strong>Descripción:</strong> {requerimiento.descripcion_problema or 'N/A'}</p>
                        </div>
                        
                        <h4 style="color: #405189; margin-top: 20px;">Información del Cliente</h4>
                        <p><strong>Nombre:</strong> {requerimiento.cliente_nombre}</p>
                        <p><strong>RUT:</strong> {requerimiento.cliente_rut or 'N/A'}</p>
                        <p><strong>Contacto:</strong> {requerimiento.cliente_telefono or 'N/A'}</p>
                        
                        <div style="margin-top: 20px; padding: 15px; background: #d1ecf1; border-left: 4px solid #0dcaf0;">
                            <p style="margin: 0;"><strong>📎 Se adjuntan {requerimiento.cantidad_fotos} foto(s) del producto/problema</strong></p>
                        </div>
                        
                        <p style="margin-top: 30px;">Por favor, revise este requerimiento y responda indicando si procede o no.</p>
                        <p style="color: #6c757d; font-size: 12px; margin-top: 30px;">
                            Puede responder directamente a este correo.<br>
                            Contacto: {request.user.get_full_name()}<br>
                            {requerimiento.sucursal.empresa.nombre}<br>
                            Sucursal: {requerimiento.sucursal.alias}
                        </p>
                    </div>
                </body>
                </html>
                """
            
            # Crear email
            email = EmailMessage(
                subject=asunto,
                body=html_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[correo_destino],
                cc=[requerimiento.proveedor.correoAdministrador] if requerimiento.proveedor.correoAdministrador else [],
                reply_to=[request.user.email] if request.user.email else [],
            )
            email.content_subtype = 'html'
            
            # Adjuntar fotos
            for foto in requerimiento.fotos.all():
                if foto.imagen and default_storage.exists(foto.imagen.name):
                    try:
                        email.attach_file(foto.imagen.path)
                    except Exception as e:
                        logger.warning("Error al adjuntar foto de requerimiento %s: %s", requerimiento.id, e)
            
            # Enviar
            email.send(fail_silently=False)
            
            # Actualizar requerimiento
            estado_anterior = requerimiento.estado
            with transaction.atomic():
                requerimiento.correo_enviado_proveedor = True
                requerimiento.fecha_envio_proveedor = timezone.now()
                requerimiento.correo_proveedor_destino = correo_destino
                requerimiento.intentos_envio = (requerimiento.intentos_envio or 0) + 1
                requerimiento.estado = 'ESPERANDO_RESPUESTA'
                requerimiento.save()
                
                # Registrar en historial
                HistorialRequerimiento.objects.create(
                    requerimiento=requerimiento,
                    accion='ENVIADO_A_PROVEEDOR',
                    estado_anterior=estado_anterior,
                    estado_nuevo='ESPERANDO_RESPUESTA',
                    comentario=f'Correo enviado a {requerimiento.proveedor.nombre} ({correo_destino}) - Intento #{requerimiento.intentos_envio}',
                    usuario=request.user
                )
            
            return JsonResponse({
                'success': True,
                'message': f'Requerimiento enviado a {requerimiento.proveedor.nombre}',
                'fecha_envio': requerimiento.fecha_envio_proveedor.strftime('%d/%m/%Y %H:%M'),
                'correo_destino': correo_destino
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error al enviar correo: {str(e)}'
            }, status=500)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error: {str(e)}'
        }, status=500)


@login_required
@require_POST
def registrar_respuesta_proveedor(request, requerimiento_id):
    """Registrar respuesta del proveedor (solo administrador)"""
    try:
        data = json.loads(request.body)
        
        requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
        
        # Validar permisos (solo administrador)
        if not usuario_puede_realizar_accion(request.user, requerimiento, 'registrar_respuesta_proveedor'):
            return JsonResponse({
                'success': False,
                'error': 'Solo administradores pueden registrar respuestas de proveedores'
            }, status=403)
        
        respuesta = data.get('respuesta')
        decision = data.get('decision')  # 'APROBADO' o 'RECHAZADO'
        motivo = data.get('motivo', '')  # Motivo visible al usuario
        
        if not respuesta or not decision:
            return JsonResponse({
                'success': False,
                'error': 'La respuesta y decisión son requeridas'
            }, status=400)
        
        if decision not in ['APROBADO', 'RECHAZADO', 'PARCIAL']:
            return JsonResponse({
                'success': False,
                'error': 'Decisión debe ser APROBADO o RECHAZADO'
            }, status=400)
        
        with transaction.atomic():
            requerimiento.respuesta_proveedor = respuesta
            requerimiento.fecha_respuesta_proveedor = timezone.now()
            requerimiento.decision_proveedor = decision
            requerimiento.estado = 'APROBADO' if decision == 'APROBADO' else 'RECHAZADO'
            requerimiento.fecha_resolucion = timezone.now()
            
            # Motivo visible al usuario
            if motivo:
                requerimiento.motivo_resolucion = motivo
            else:
                requerimiento.motivo_resolucion = f"{decision}: {respuesta[:200]}"
            
            requerimiento.save()
            
            # Registrar en historial
            HistorialRequerimiento.objects.create(
                requerimiento=requerimiento,
                accion='RESPUESTA_PROVEEDOR_REGISTRADA',
                estado_anterior='ESPERANDO_RESPUESTA',
                estado_nuevo=requerimiento.estado,
                comentario=f'Proveedor {requerimiento.proveedor.nombre} respondió: {decision} - {respuesta[:100]}...',
                usuario=request.user
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Respuesta del proveedor registrada exitosamente',
            'decision': decision,
            'nuevo_estado': requerimiento.get_estado_display()
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al registrar respuesta: {str(e)}'
        }, status=500)


@login_required
@require_POST
def completar_requerimiento(request, requerimiento_id):
    """Completar un requerimiento"""
    try:
        data = json.loads(request.body)
        
        resolucion = data.get('resolucion')
        
        if not resolucion:
            return JsonResponse({
                'success': False,
                'error': 'La resolución es requerida'
            }, status=400)
        
        requerimiento = get_object_or_404(Requerimiento, id=requerimiento_id)
        estado_anterior = requerimiento.estado
        
        with transaction.atomic():
            requerimiento.resolucion = resolucion
            requerimiento.fecha_resolucion = timezone.now()
            requerimiento.estado = 'COMPLETADO'
            requerimiento.save()
            
            # Registrar en historial
            HistorialRequerimiento.objects.create(
                requerimiento=requerimiento,
                accion='COMPLETADO',
                estado_anterior=estado_anterior,
                estado_nuevo='COMPLETADO',
                comentario=resolucion,
                usuario=request.user
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Requerimiento completado exitosamente'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al completar requerimiento: {str(e)}'
        }, status=500)


# ========== BÚSQUEDA Y UTILIDADES ==========

@login_required
def buscar_producto_sku(request):
    """Buscar producto por SKU"""
    try:
        sku = request.GET.get('sku', '')
        
        if not sku:
            return JsonResponse({
                'success': False,
                'error': 'SKU es requerido'
            }, status=400)
        
        try:
            from .utils_producto_match import producto_talla_por_sku
            producto_talla = producto_talla_por_sku(
                sku, sucursal_id=request.session.get('idSucursalActual'),
                select_related=['producto'])
            if not producto_talla:
                return JsonResponse({'success': False, 'error': 'Producto no encontrado'}, status=404)

            return JsonResponse({
                'success': True,
                'producto': {
                    'id': producto_talla.id,
                    'sku': producto_talla.sku,
                    'nombre': producto_talla.producto.articulo,
                    'descripcion': producto_talla.producto.descripcion,
                    'talla': producto_talla.talla,
                    'precio': producto_talla.producto.precioventa,
                }
            })
        except Producto_Talla.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Producto no encontrado'
            }, status=404)
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar producto: {str(e)}'
        }, status=500)


@login_required
def buscar_ticket_por_folio(request):
    """Buscar ticket/documento por folio o correlativo en TODAS las sucursales de la DB"""
    try:
        folio = request.GET.get('folio', '').strip()

        if not folio:
            return JsonResponse({
                'success': False,
                'error': 'Folio o correlativo es requerido'
            }, status=400)

        resultados = []

        if folio.isdigit():
            folio_num = int(folio)

            # 1. Buscar en Tickets por folio_dte (TODAS las sucursales)
            for ticket in Ticket.objects.filter(
                folio_dte=folio_num
            ).select_related('vendedor', 'sucursal')[:10]:
                resultados.append(_serializar_ticket(ticket))

            # 2. Buscar en Tickets por correlativo (si no hay resultados por folio_dte)
            if not resultados:
                for ticket in Ticket.objects.filter(
                    correlativo=folio_num
                ).select_related('vendedor', 'sucursal')[:10]:
                    resultados.append(_serializar_ticket(ticket))

            # 3. Buscar en DTEs
            if not resultados:
                for dte in Dte.objects.filter(
                    numero_documento=folio_num,
                    tipo_transaccion__in=['VENTA', 'VENTA_PUBLICO']
                ).select_related('vendedor', 'emisor', 'receptor', 'sucursal')[:10]:
                    resultados.append(_serializar_dte(dte))

        if not resultados:
            return JsonResponse({
                'success': False,
                'error': 'Documento no encontrado'
            }, status=404)

        # Si hay un solo resultado, devolver directo (retrocompatible)
        if len(resultados) == 1:
            return JsonResponse({
                'success': True,
                'documento': resultados[0],
            })

        # Multiples resultados: devolver lista para que el usuario elija
        return JsonResponse({
            'success': True,
            'multiple': True,
            'documentos': resultados,
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar documento: {str(e)}'
        }, status=500)


def _serializar_ticket(ticket):
    """Serializa un Ticket para la respuesta de busqueda"""
    productos = []
    for tp in ticket.ticket_productos.select_related('ProductoTalla__producto').all():
        if tp.ProductoTalla:
            productos.append({
                'sku': tp.ProductoTalla.sku,
                'nombre': tp.ProductoTalla.producto.articulo,
                'talla': tp.ProductoTalla.talla,
                'cantidad': tp.stock,
                'precio': tp.precio,
            })

    return {
        'tipo_fuente': 'ticket',
        'sucursal': ticket.sucursal.alias if ticket.sucursal else '',
        'sucursal_id': ticket.sucursal.id if ticket.sucursal else None,
        'correlativo': ticket.correlativo,
        'folio_dte': ticket.folio_dte,
        'tipo_dte': ticket.get_tipo_dte_display() if ticket.tipo_dte else 'Ticket',
        'tipo_dte_codigo': ticket.tipo_dte or 'TICKET',
        'fecha': ticket.fecha.strftime('%Y-%m-%d'),
        'total': ticket.total,
        'vendedor': ticket.vendedor.nombre if ticket.vendedor else '',
        'cliente_nombre': ticket.cliente_nombre or '',
        'cliente_rut': ticket.cliente_rut or '',
        'cliente_email': ticket.cliente_email or '',
        'cliente_telefono': ticket.cliente_telefono or '',
        'cliente_direccion': ticket.cliente_direccion or '',
        'cliente_comuna': ticket.cliente_comuna or '',
        'productos': productos,
    }


def _serializar_dte(dte):
    """Serializa un DTE para la respuesta de busqueda"""
    productos = []
    for dp in Dte_Productos.objects.filter(dte=dte).select_related('productoTalla__producto'):
        productos.append({
            'sku': dp.productoTalla.sku if dp.productoTalla else '',
            'nombre': dp.productoTalla.producto.articulo if dp.productoTalla else dp.descripcion,
            'talla': dp.productoTalla.talla if dp.productoTalla else '',
            'cantidad': dp.stock,
            'precio': int(dp.precio),
        })

    cliente_nombre = ''
    cliente_rut = ''
    cliente_email = ''
    cliente_direccion = ''
    cliente_comuna = ''
    if dte.receptor:
        cliente_nombre = dte.receptor.nombre
        cliente_rut = dte.receptor.rut
        cliente_email = dte.receptor.correoAdministrador or ''
        cliente_direccion = dte.receptor.direccion or ''
        cliente_comuna = dte.receptor.comuna or ''

    return {
        'tipo_fuente': 'dte',
        'sucursal': dte.sucursal.alias if dte.sucursal else 'N/A',
        'sucursal_id': dte.sucursal.id if dte.sucursal else None,
        'correlativo': dte.numero_documento,
        'folio_dte': dte.numero_documento,
        'tipo_dte': dte.get_tipo_documento_display(),
        'tipo_dte_codigo': dte.tipo_documento,
        'fecha': dte.fecha_emision.strftime('%Y-%m-%d'),
        'total': int(dte.monto_con_iva),
        'vendedor': dte.vendedor.nombre if dte.vendedor else '',
        'cliente_nombre': cliente_nombre,
        'cliente_rut': cliente_rut,
        'cliente_email': cliente_email,
        'cliente_telefono': '',
        'cliente_direccion': cliente_direccion,
        'cliente_comuna': cliente_comuna,
        'productos': productos,
    }


@login_required
def validar_rut_chileno(request):
    """Validar formato y dígito verificador de RUT chileno"""
    try:
        rut = request.GET.get('rut', '').strip()
        
        if not rut:
            return JsonResponse({'success': False, 'error': 'RUT es requerido'}, status=400)
        
        # Limpiar RUT
        rut = rut.replace('.', '').replace('-', '').upper()
        
        if len(rut) < 2:
            return JsonResponse({'success': False, 'error': 'RUT inválido'}, status=400)
        
        # Separar número y dígito verificador
        numero = rut[:-1]
        dv = rut[-1]
        
        # Validar que el número sea numérico
        if not numero.isdigit():
            return JsonResponse({'success': False, 'error': 'RUT inválido'}, status=400)
        
        # Calcular dígito verificador
        suma = 0
        multiplicador = 2
        
        for digito in reversed(numero):
            suma += int(digito) * multiplicador
            multiplicador = multiplicador + 1 if multiplicador < 7 else 2
        
        resto = suma % 11
        dv_calculado = 11 - resto
        
        if dv_calculado == 11:
            dv_calculado = '0'
        elif dv_calculado == 10:
            dv_calculado = 'K'
        else:
            dv_calculado = str(dv_calculado)
        
        # Formatear RUT
        rut_formateado = f"{numero[:-6]}.{numero[-6:-3]}.{numero[-3:]}-{dv}" if len(numero) > 6 else f"{numero}-{dv}"
        
        if dv == dv_calculado:
            return JsonResponse({
                'success': True,
                'valido': True,
                'rut_formateado': rut_formateado,
                'message': 'RUT válido'
            })
        else:
            return JsonResponse({
                'success': True,
                'valido': False,
                'message': f'RUT inválido. DV correcto debería ser: {dv_calculado}'
            })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al validar RUT: {str(e)}'
        }, status=500)


@login_required
def buscar_cliente_por_rut(request):
    """Buscar cliente por RUT en la base de datos"""
    try:
        from app.models import Cliente

        rut = request.GET.get('rut', '').strip()
        
        if not rut:
            return JsonResponse({
                'success': False,
                'error': 'RUT es requerido'
            }, status=400)
        
        # Limpiar RUT (quitar puntos y guiones)
        rut_limpio = rut.replace('.', '').replace('-', '')
        
        # Buscar cliente por RUT (con o sin formato)
        cliente = Cliente.objects.filter(
            Q(rut__icontains=rut_limpio) | Q(rut__icontains=rut)
        ).first()
        
        if not cliente:
            return JsonResponse({
                'success': False,
                'error': 'Cliente no encontrado'
            }, status=404)
        
        return JsonResponse({
            'success': True,
            'cliente': {
                'id': cliente.id,
                'nombre': cliente.nombre_completo,
                'rut': cliente.rut or '',
                'email': cliente.email or '',
                'telefono': cliente.telefono or cliente.celular or '',
                'direccion': cliente.direccion or '',
                'comuna': cliente.comuna or '',
                'ciudad': cliente.ciudad or '',
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al buscar cliente: {str(e)}'
        }, status=500)


@login_required
@require_POST
def crear_cliente_rapido(request):
    """Crear cliente rápido desde formulario de requerimientos"""
    try:
        from app.models import Cliente

        data = json.loads(request.body)
        
        # Validar campos requeridos
        if not data.get('nombre') or not data.get('apellido'):
            return JsonResponse({
                'success': False,
                'error': 'Nombre y apellido son requeridos'
            }, status=400)
        
        rut = data.get('rut', '').strip()
        
        # Validar RUT si se proporciona
        if rut:
            # Verificar que no exista
            if Cliente.objects.filter(rut=rut).exists():
                return JsonResponse({
                    'success': False,
                    'error': 'Ya existe un cliente con este RUT'
                }, status=400)
        
        # Crear cliente
        with transaction.atomic():
            cliente = Cliente.objects.create(
                nombre=data.get('nombre'),
                apellido=data.get('apellido', ''),
                rut=rut if rut else None,
                email=data.get('email', ''),
                telefono=data.get('telefono', ''),
                direccion=data.get('direccion', ''),
                comuna=data.get('comuna', ''),
                ciudad=data.get('ciudad', ''),
                tipo_cliente='INDIVIDUAL',
                created_by=request.user
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Cliente creado exitosamente',
            'cliente': {
                'id': cliente.id,
                'nombre': cliente.nombre_completo,
                'rut': cliente.rut or '',
                'email': cliente.email or '',
                'telefono': cliente.telefono or '',
                'direccion': cliente.direccion or '',
                'comuna': cliente.comuna or '',
                'ciudad': cliente.ciudad or '',
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al crear cliente: {str(e)}'
        }, status=500)


@login_required
def obtener_estadisticas_requerimientos(request):
    """Obtener estadísticas del módulo de requerimientos"""
    try:
        # Filtrar por sucursal si no es admin
        requerimientos = Requerimiento.objects.all()
        
        rol_usuario = obtener_rol_usuario(request.user)
        if rol_usuario != 'administrador':
            sucursales_usuario = Sucursal.objects.filter(
                empresa__empresauser__user=request.user
            )
            requerimientos = requerimientos.filter(sucursal__in=sucursales_usuario)
        
        # Estadísticas generales
        total = requerimientos.count()
        
        # Obtener choices del modelo
        from .models import ESTADO_REQUERIMIENTO_CHOICES, TIPO_REQUERIMIENTO_CHOICES
        
        por_estado = {}
        for estado_code, estado_name in ESTADO_REQUERIMIENTO_CHOICES:
            count = requerimientos.filter(estado=estado_code).count()
            por_estado[estado_code] = {
                'nombre': estado_name,
                'cantidad': count
            }
        
        por_tipo = {}
        for tipo_code, tipo_name in TIPO_REQUERIMIENTO_CHOICES:
            count = requerimientos.filter(tipo=tipo_code).count()
            por_tipo[tipo_code] = {
                'nombre': tipo_name,
                'cantidad': count
            }
        
        # Requerimientos recientes
        recientes = requerimientos.order_by('-fecha_creacion')[:5]
        recientes_data = []
        for req in recientes:
            recientes_data.append({
                'id': req.id,
                'numero': req.numero_requerimiento,
                'tipo': req.get_tipo_display(),
                'estado': req.get_estado_display(),
                'dias': req.dias_transcurridos
            })
        
        # Contadores especiales de seguimiento
        fecha_limite_7dias = timezone.now() - timedelta(days=7)
        sin_respuesta_7dias = requerimientos.filter(
            estado='ESPERANDO_RESPUESTA',
            fecha_envio_proveedor__lt=fecha_limite_7dias,
            fecha_respuesta_proveedor__isnull=True
        ).count()
        
        return JsonResponse({
            'success': True,
            'estadisticas': {
                'total': total,
                'por_estado': por_estado,
                'por_tipo': por_tipo,
                'recientes': recientes_data,
                'sin_respuesta_7dias': sin_respuesta_7dias,
            }
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al obtener estadísticas: {str(e)}'
        }, status=500)


@login_required
def exportar_requerimientos(request):
    """Exportar requerimientos a Excel"""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        
        # Filtros
        estado = request.GET.get('estado')
        tipo = request.GET.get('tipo')
        fecha_inicio = request.GET.get('fecha_inicio')
        fecha_fin = request.GET.get('fecha_fin')
        
        # Query
        requerimientos = Requerimiento.objects.select_related(
            'sucursal', 'usuario_creador', 'proveedor'
        ).all()
        
        # Aplicar filtros
        if estado:
            requerimientos = requerimientos.filter(estado=estado)
        if tipo:
            requerimientos = requerimientos.filter(tipo=tipo)
        if fecha_inicio:
            try:
                dt_inicio = timezone.make_aware(datetime.strptime(fecha_inicio, '%Y-%m-%d'))
                requerimientos = requerimientos.filter(fecha_creacion__gte=dt_inicio)
            except (ValueError, TypeError):
                pass
        if fecha_fin:
            try:
                dt_fin = timezone.make_aware(datetime.strptime(fecha_fin, '%Y-%m-%d').replace(hour=23, minute=59, second=59))
                requerimientos = requerimientos.filter(fecha_creacion__lte=dt_fin)
            except (ValueError, TypeError):
                pass
        
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Requerimientos"
        
        # Encabezados
        headers = [
            'Número', 'Tipo', 'Estado', 'Prioridad', 'Sucursal', 'SKU', 'Producto',
            'Cliente', 'Boleta', 'Fecha Creación', 'Días', 'Proveedor', 'Estado Proveedor'
        ]
        
        # Estilo para encabezados
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        
        # Escribir encabezados
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
        
        # Escribir datos
        for row, req in enumerate(requerimientos, 2):
            ws.cell(row=row, column=1, value=req.numero_requerimiento)
            ws.cell(row=row, column=2, value=req.get_tipo_display())
            ws.cell(row=row, column=3, value=req.get_estado_display())
            ws.cell(row=row, column=4, value=req.get_prioridad_display())
            ws.cell(row=row, column=5, value=req.sucursal.alias)
            ws.cell(row=row, column=6, value=req.sku)
            ws.cell(row=row, column=7, value=req.nombre_producto)
            ws.cell(row=row, column=8, value=req.cliente_nombre)
            ws.cell(row=row, column=9, value=req.numero_boleta or '')
            ws.cell(row=row, column=10, value=req.fecha_creacion.strftime('%d/%m/%Y'))
            ws.cell(row=row, column=11, value=req.dias_transcurridos)
            ws.cell(row=row, column=12, value=req.proveedor.nombre if req.proveedor else '')
            ws.cell(row=row, column=13, value='Enviado' if req.correo_enviado_proveedor else 'No enviado')
        
        # Ajustar ancho de columnas
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="requerimientos_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
        
        wb.save(response)
        return response
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': f'Error al exportar: {str(e)}'
        }, status=500)


# ========== API TIPOS DE FOTO ==========

@login_required
@require_GET
def obtener_tipos_foto(request):
    """Retorna los tipos de foto requeridos/opcionales para un tipo de requerimiento"""
    tipo = request.GET.get('tipo', '')
    if not tipo:
        return JsonResponse({'success': False, 'error': 'Tipo es requerido'}, status=400)

    tipos_foto = TipoFotoRequerimiento.objects.filter(
        activo=True,
        tipos_requerimiento__contains=[tipo],
    ).order_by('orden')

    return JsonResponse({
        'success': True,
        'tipos_foto': [
            {
                'codigo': tf.codigo,
                'nombre': tf.nombre,
                'descripcion_guia': tf.descripcion_guia,
                'icono': tf.icono,
                'es_obligatorio': tf.es_obligatorio,
                'orden': tf.orden,
            }
            for tf in tipos_foto
        ],
        'max_fotos': MAX_FOTOS_POR_TIPO.get(tipo, 5),
    })
