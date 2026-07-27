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
from django.contrib.sessions.models import Session
from django.utils import timezone

from decimal import Decimal, InvalidOperation

from .models import Sucursal, Empresa, EmpresaUser
import logging

logger = logging.getLogger('app')


# ==========================================================================
# Helpers
# ==========================================================================

def obtener_empresa_user_activo(request):
    """
    Resuelve la asignación empresa/sucursal vigente del usuario de forma
    DETERMINÍSTICA.

    Antes se usaba `EmpresaUser.objects.filter(user=...).first()`, que:
      - no filtraba `status=True` (podía devolver una asignación dada de baja), y
      - no tenía `order_by`, así que con varias asignaciones el resultado
        dependía del plan de ejecución de Postgres.

    Prioridad:
      1. La empresa que el usuario tiene seleccionada en sesión.
      2. La asignación marcada como `active=True`.
      3. La de menor id (estable).
    """
    asignaciones = EmpresaUser.objects.select_related('empresa', 'sucursal').filter(
        user=request.user,
        status=True,
    ).order_by('-active', 'id')

    empresa_sesion = request.session.get('idEmpresaActual')
    if empresa_sesion:
        try:
            preferida = asignaciones.filter(empresa_id=int(empresa_sesion)).first()
        except (TypeError, ValueError):
            preferida = None
        if preferida:
            return preferida

    return asignaciones.first()


def _parse_bool(valor):
    """Interpreta los valores que manda un formulario HTML como booleano."""
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ('1', 'true', 'on', 'si', 'sí', 'yes')


def _validar_tipo_sucursal(valor):
    """Devuelve (ok, valor_normalizado_o_mensaje_error)."""
    valor = (valor or '').strip().upper()
    validos = [c[0] for c in Sucursal.TIPO_SUCURSAL_CHOICES]
    if valor not in validos:
        return False, f'Tipo de sucursal inválido. Opciones: {", ".join(validos)}'
    return True, valor


def _validar_margen(valor):
    """Devuelve (ok, Decimal_o_mensaje_error). Acepta coma o punto decimal."""
    texto = str(valor).strip().replace(',', '.')
    if texto == '':
        texto = '0'
    try:
        margen = Decimal(texto).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return False, 'El margen de sobreprecio debe ser un número (ej: 12.5)'
    if margen < 0 or margen > 100:
        return False, 'El margen de sobreprecio debe estar entre 0 y 100'
    return True, margen


def _advertencias_coherencia(tipo_sucursal, es_centro_distribucion):
    """
    `es_centro_distribucion` y `tipo_sucursal` se usan en reportes y despachos
    (ver la property `Sucursal.es_compradora`). No se fuerzan entre sí para no
    cambiar datos en silencio, pero sí se avisa cuando quedan incoherentes.
    """
    advertencias = []
    if tipo_sucursal == 'CENTRO_DISTRIBUCION' and not es_centro_distribucion:
        advertencias.append(
            'La sucursal es de tipo "Centro de Distribución" pero NO tiene marcada '
            'la casilla "Es centro de distribución". Varios reportes usan esa casilla.'
        )
    if es_centro_distribucion and tipo_sucursal == 'VENDEDORA':
        advertencias.append(
            'La sucursal está marcada como centro de distribución pero su tipo es '
            '"Sucursal Vendedora". Revisa que sea lo que quieres.'
        )
    return advertencias


def invalidar_sesiones_sucursal(sucursal_id, alias_anterior=None):
    """
    Invalida todas las sesiones activas de usuarios que tienen una sucursal específica.
    
    Args:
        sucursal_id: ID de la sucursal cuyas sesiones se invalidarán
        alias_anterior: Alias anterior de la sucursal (opcional, para logging)
    
    Returns:
        Número de sesiones invalidadas
    """
    try:
        sesiones_invalidadas = 0
        sesiones_activas = Session.objects.filter(expire_date__gte=timezone.now())
        
        for sesion in sesiones_activas:
            try:
                # Decodificar los datos de la sesión
                datos_sesion = sesion.get_decoded()
                
                # Verificar si esta sesión tiene la sucursal que cambió
                sucursal_sesion = datos_sesion.get('idSucursalActual')
                
                if sucursal_sesion and int(sucursal_sesion) == int(sucursal_id):
                    # Eliminar la sesión
                    sesion.delete()
                    sesiones_invalidadas += 1
                    logger.info(
                        f"Sesión invalidada: sucursal_id={sucursal_id}, "
                        f"alias_anterior={alias_anterior}, session_key={sesion.session_key[:8]}..."
                    )
            except Exception as e:
                logger.warning(f"Error al procesar sesión {sesion.session_key}: {str(e)}")
                continue
        
        logger.info(
            f"Total de sesiones invalidadas para sucursal {sucursal_id} "
            f"(alias anterior: {alias_anterior}): {sesiones_invalidadas}"
        )
        return sesiones_invalidadas
        
    except Exception as e:
        logger.error(f"Error al invalidar sesiones: {str(e)}")
        return 0


@login_required
def gestion_sucursales(request):
    """Vista principal para gestión de sucursales"""
    try:
        # Obtener empresa del usuario actual (resolución determinística)
        empresa_user = obtener_empresa_user_activo(request)

        if not empresa_user:
            messages.error(request, 'No tienes una empresa asignada')
            return redirect('verHome')

        empresa = empresa_user.empresa

        # Obtener TODAS las empresas para el selector
        empresas = Empresa.objects.all().order_by('nombre')

        context = {
            'empresa': empresa,
            'empresas': empresas,
            'titulo': 'Gestión de Sucursales',
            'puede_crear': True,  # Aquí podrías agregar permisos específicos
            # Para el <select> de tipo de sucursal del formulario
            'tipos_sucursal': Sucursal.TIPO_SUCURSAL_CHOICES,
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
        # Obtener empresa del usuario (resolución determinística)
        empresa_user = obtener_empresa_user_activo(request)

        if not empresa_user:
            return JsonResponse({
                'success': False,
                'error': 'No tienes una empresa asignada'
            })

        # Obtener parámetros de búsqueda
        search = request.GET.get('search', '').strip()
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 10))
        filtro_tipo = request.GET.get('tipo', '').strip().upper()
        filtro_estado = request.GET.get('estado', '').strip().lower()
        filtro_cd = request.GET.get('centro_distribucion', '').strip().lower()

        # Construir query - TODAS las sucursales
        sucursales = Sucursal.objects.all().select_related('empresa')

        # Aplicar búsqueda (alias, dirección y empresa)
        if search:
            sucursales = sucursales.filter(
                Q(alias__icontains=search) |
                Q(direccion__icontains=search) |
                Q(empresa__nombre__icontains=search)
            )

        # Filtros opcionales (si no vienen, el comportamiento es el de siempre)
        if filtro_tipo in [c[0] for c in Sucursal.TIPO_SUCURSAL_CHOICES]:
            sucursales = sucursales.filter(tipo_sucursal=filtro_tipo)

        if filtro_estado == 'activa':
            sucursales = sucursales.filter(activa=True)
        elif filtro_estado == 'inactiva':
            sucursales = sucursales.filter(activa=False)

        if filtro_cd in ('1', 'true', 'si'):
            sucursales = sucursales.filter(es_centro_distribucion=True)
        elif filtro_cd in ('0', 'false', 'no'):
            sucursales = sucursales.filter(es_centro_distribucion=False)

        # Ordenar por alias (con id como desempate: sin él la paginación puede
        # repetir o saltarse filas cuando hay alias iguales en distintas empresas)
        sucursales = sucursales.order_by('alias', 'id')

        # Paginar
        paginator = Paginator(sucursales, page_size)
        page_obj = paginator.get_page(page)

        # Preparar datos
        data = []
        for sucursal in page_obj:
            data.append({
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
                'empresa_id': sucursal.empresa_id,
                'empresa_nombre': sucursal.empresa.nombre if sucursal.empresa else 'N/A',
                'tipo_sucursal': sucursal.tipo_sucursal,
                'tipo_sucursal_display': sucursal.get_tipo_sucursal_display(),
                'es_centro_distribucion': sucursal.es_centro_distribucion,
                'es_compradora': sucursal.es_compradora,
                'activa': sucursal.activa,
                'margen_sobreprecio_default': float(sucursal.margen_sobreprecio_default or 0),
                'usar_qz_tray': sucursal.usar_qz_tray,
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
        # Obtener datos del formulario
        data = request.POST

        # Validar campos obligatorios
        alias = (data.get('alias') or '').strip()
        direccion = (data.get('direccion') or '').strip()

        if not alias:
            return JsonResponse({
                'success': False,
                'error': 'El alias es obligatorio'
            })

        if not direccion:
            return JsonResponse({
                'success': False,
                'error': 'La dirección es obligatoria'
            })

        # --- Empresa ---
        # El formulario ya trae un selector de empresa: hay que respetarlo.
        # Si no viene, se cae a la empresa vigente del usuario (determinística).
        empresa_id = (data.get('empresa_id') or '').strip()
        if empresa_id:
            try:
                empresa_destino = Empresa.objects.get(id=int(empresa_id))
            except (Empresa.DoesNotExist, TypeError, ValueError):
                return JsonResponse({
                    'success': False,
                    'error': 'La empresa seleccionada no existe'
                })
        else:
            empresa_user = obtener_empresa_user_activo(request)
            if not empresa_user:
                return JsonResponse({
                    'success': False,
                    'error': 'No tienes una empresa asignada. Selecciona una empresa para la sucursal.'
                })
            empresa_destino = empresa_user.empresa

        # Verificar si ya existe una sucursal con ese alias en esa empresa
        # (hay unique_together ['empresa', 'alias'] en el modelo)
        if Sucursal.objects.filter(empresa=empresa_destino, alias=alias).exists():
            return JsonResponse({
                'success': False,
                'error': f'Ya existe una sucursal con el alias "{alias}" en {empresa_destino.nombre}'
            })

        # --- Campos operativos opcionales ---
        tipo_sucursal = 'VENDEDORA'
        if data.get('tipo_sucursal'):
            ok, resultado = _validar_tipo_sucursal(data.get('tipo_sucursal'))
            if not ok:
                return JsonResponse({'success': False, 'error': resultado})
            tipo_sucursal = resultado

        margen = Decimal('0.00')
        if 'margen_sobreprecio_default' in data:
            ok, resultado = _validar_margen(data.get('margen_sobreprecio_default'))
            if not ok:
                return JsonResponse({'success': False, 'error': resultado})
            margen = resultado

        es_cd = _parse_bool(data.get('es_centro_distribucion'))
        activa = _parse_bool(data.get('activa')) if 'activa' in data else True

        with transaction.atomic():
            # Crear sucursal
            sucursal = Sucursal.objects.create(
                empresa=empresa_destino,
                alias=alias,
                direccion=direccion,
                tipo_sucursal=tipo_sucursal,
                es_centro_distribucion=es_cd,
                activa=activa,
                margen_sobreprecio_default=margen,
                nombre_impresora_boleta=data.get('nombre_impresora_boleta', 'boleta') or 'boleta',
                nombre_impresora_factura=data.get('nombre_impresora_factura', 'factura') or 'factura',
                usar_qz_tray=_parse_bool(data.get('usar_qz_tray')),
                nombre_impresora_termica=data.get('nombre_impresora_termica') or 'EPSON TM-T20II',
            )

            logger.info(
                "Sucursal creada: %s (ID: %s, empresa: %s, tipo: %s, CD: %s) por usuario %s",
                sucursal.alias, sucursal.id, empresa_destino.nombre,
                sucursal.tipo_sucursal, sucursal.es_centro_distribucion,
                request.user.username
            )

            return JsonResponse({
                'success': True,
                'message': f'Sucursal "{sucursal.alias}" creada exitosamente en {empresa_destino.nombre}',
                'advertencias': _advertencias_coherencia(tipo_sucursal, es_cd),
                'sucursal': {
                    'id': sucursal.id,
                    'alias': sucursal.alias,
                    'direccion': sucursal.direccion,
                    'empresa_id': sucursal.empresa_id,
                    'empresa_nombre': empresa_destino.nombre,
                    'tipo_sucursal': sucursal.tipo_sucursal,
                    'es_centro_distribucion': sucursal.es_centro_distribucion,
                    'activa': sucursal.activa,
                    'margen_sobreprecio_default': float(sucursal.margen_sobreprecio_default or 0),
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
        # Obtener sucursal (sin restricción por empresa)
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        return JsonResponse({
            'success': True,
            'sucursal': {
                'id': sucursal.id,
                'alias': sucursal.alias,
                'direccion': sucursal.direccion,
                'empresa_id': sucursal.empresa_id,
                'empresa_nombre': sucursal.empresa.nombre if sucursal.empresa else 'N/A',
                'tipo_sucursal': sucursal.tipo_sucursal,
                'tipo_sucursal_display': sucursal.get_tipo_sucursal_display(),
                'es_centro_distribucion': sucursal.es_centro_distribucion,
                'es_compradora': sucursal.es_compradora,
                'activa': sucursal.activa,
                'margen_sobreprecio_default': float(sucursal.margen_sobreprecio_default or 0),
                'nombre_impresora_boleta': getattr(sucursal, 'nombre_impresora_boleta', 'boleta') or 'boleta',
                'nombre_impresora_factura': getattr(sucursal, 'nombre_impresora_factura', 'factura') or 'factura',
                'usar_qz_tray': getattr(sucursal, 'usar_qz_tray', False),
                'nombre_impresora_termica': getattr(sucursal, 'nombre_impresora_termica', 'EPSON TM-T20II') or 'EPSON TM-T20II',
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
        # Obtener sucursal (sin restricción por empresa)
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
        # Guardar el alias anterior para comparar
        alias_anterior = sucursal.alias
        
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
        
        # Si se proporciona empresa_id, obtener la nueva empresa
        nueva_empresa_id = data.get('empresa_id')
        if nueva_empresa_id:
            nueva_empresa = get_object_or_404(Empresa, id=nueva_empresa_id)
        else:
            nueva_empresa = sucursal.empresa
        
        # Verificar si el alias ya existe en otra sucursal de la misma empresa
        if Sucursal.objects.filter(
            empresa=nueva_empresa,
            alias=data.get('alias')
        ).exclude(id=sucursal_id).exists():
            return JsonResponse({
                'success': False,
                'error': f'Ya existe otra sucursal con el alias "{data.get("alias")}" en esa empresa'
            })
        
        # Detectar si hubo cambio de alias
        nuevo_alias = data.get('alias')
        alias_cambio = alias_anterior != nuevo_alias

        # --- Campos operativos: solo se tocan si el formulario los envía ---
        # (así un formulario antiguo que no los manda no los pisa con vacío)
        cd_anterior = sucursal.es_centro_distribucion
        tipo_anterior = sucursal.tipo_sucursal

        nuevo_tipo = sucursal.tipo_sucursal
        if 'tipo_sucursal' in data:
            ok, resultado = _validar_tipo_sucursal(data.get('tipo_sucursal'))
            if not ok:
                return JsonResponse({'success': False, 'error': resultado})
            nuevo_tipo = resultado

        nuevo_margen = sucursal.margen_sobreprecio_default
        if 'margen_sobreprecio_default' in data:
            ok, resultado = _validar_margen(data.get('margen_sobreprecio_default'))
            if not ok:
                return JsonResponse({'success': False, 'error': resultado})
            nuevo_margen = resultado

        nuevo_cd = sucursal.es_centro_distribucion
        if 'es_centro_distribucion' in data:
            nuevo_cd = _parse_bool(data.get('es_centro_distribucion'))

        nueva_activa = sucursal.activa
        if 'activa' in data:
            nueva_activa = _parse_bool(data.get('activa'))

        with transaction.atomic():
            # Actualizar sucursal
            sucursal.alias = nuevo_alias
            sucursal.direccion = data.get('direccion')
            sucursal.empresa = nueva_empresa
            sucursal.tipo_sucursal = nuevo_tipo
            sucursal.es_centro_distribucion = nuevo_cd
            sucursal.activa = nueva_activa
            sucursal.margen_sobreprecio_default = nuevo_margen
            sucursal.nombre_impresora_boleta = data.get('nombre_impresora_boleta', 'boleta') or 'boleta'
            sucursal.nombre_impresora_factura = data.get('nombre_impresora_factura', 'factura') or 'factura'
            sucursal.usar_qz_tray = data.get('usar_qz_tray') == 'on'
            sucursal.nombre_impresora_termica = data.get('nombre_impresora_termica', 'EPSON TM-T20II') or 'EPSON TM-T20II'
            sucursal.save()

            logger.info(f"Sucursal actualizada: {sucursal.alias} (ID: {sucursal.id}) por usuario {request.user.username}")

            # El flag de centro de distribución cambia el comportamiento de
            # reportes y despachos: queda trazado en el log.
            if cd_anterior != nuevo_cd or tipo_anterior != nuevo_tipo:
                logger.warning(
                    "Sucursal %s (ID %s): tipo '%s' -> '%s', centro_distribucion %s -> %s. Usuario: %s",
                    sucursal.alias, sucursal.id, tipo_anterior, nuevo_tipo,
                    cd_anterior, nuevo_cd, request.user.username
                )

            # 🔐 INVALIDAR SESIONES SI CAMBIÓ EL ALIAS (SEGURIDAD)
            sesiones_cerradas = 0
            if alias_cambio:
                sesiones_cerradas = invalidar_sesiones_sucursal(sucursal_id, alias_anterior)
                logger.warning(
                    f"⚠️ CAMBIO DE ALIAS DETECTADO: '{alias_anterior}' → '{nuevo_alias}'. "
                    f"Sesiones cerradas: {sesiones_cerradas}"
                )

            # Preparar mensaje de respuesta
            mensaje = f'Sucursal "{sucursal.alias}" actualizada exitosamente'
            if alias_cambio and sesiones_cerradas > 0:
                mensaje += f' (Se cerraron {sesiones_cerradas} sesión(es) activa(s) por seguridad)'

            return JsonResponse({
                'success': True,
                'message': mensaje,
                'alias_cambio': alias_cambio,
                'sesiones_cerradas': sesiones_cerradas,
                'cd_cambio': cd_anterior != nuevo_cd,
                'advertencias': _advertencias_coherencia(nuevo_tipo, nuevo_cd),
                'sucursal': {
                    'id': sucursal.id,
                    'alias': sucursal.alias,
                    'direccion': sucursal.direccion,
                    'empresa_id': sucursal.empresa_id,
                    'tipo_sucursal': sucursal.tipo_sucursal,
                    'es_centro_distribucion': sucursal.es_centro_distribucion,
                    'activa': sucursal.activa,
                    'margen_sobreprecio_default': float(sucursal.margen_sobreprecio_default or 0),
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
        # Obtener sucursal (sin restricción por empresa)
        sucursal = get_object_or_404(Sucursal, id=sucursal_id)
        
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


