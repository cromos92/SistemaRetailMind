"""
Módulo de Gestión de Inventarios - RetailMind
============================================

Sistema completo de toma de inventario físico con:
- Inventario por segmentos (marca, categoría, atributo)
- Fecha de corte para congelamiento de datos
- Análisis previo antes de aplicar ajustes
- Procesamiento en lotes para grandes volúmenes
- Optimización de queries para evitar N+1

Mejores Prácticas de Logística Implementadas:
- Conteo cíclico y ABC
- Reconteo automático para diferencias significativas
- Trazabilidad completa de ajustes
- Reportes de análisis antes de aprobar
"""

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.db.models import Sum, F, Q, Count, Case, When, Prefetch, Value, CharField, DecimalField, ExpressionWrapper, Max
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator
from django.utils import timezone
from django.db import transaction, connection
from django.core.exceptions import ValidationError, PermissionDenied
import threading
from datetime import datetime, timedelta
from decimal import Decimal
import csv
import io
import json
import logging

from .models import (
    Producto, Producto_Talla, Productos_Atributos, AtributoOpcion, Categoria,
    LoteProducto, Movimientos_Producto, Sucursal, Empresa, EmpresaUser,
    TomaInventario, TomaInventarioDetalle, TomaInventarioLog, TareaAplicacionAjustes
)
from .models.inventario import requiere_reconteo
from .services import informe_toma_inventario as informe_toma
from .utils_permisos import (
    puede_ver_sucursal, obtener_empresas_usuario, obtener_sucursales_usuario
)

logger = logging.getLogger('app')

# ==============================================================================
# CONSTANTES Y CONFIGURACIÓN
# ==============================================================================

BATCH_SIZE = 500  # Tamaño de lote para operaciones masivas
# (los umbrales de reconteo viven en app/models/inventario.py, donde se aplican)

# Estados en los que el inventario todavía se está contando
ESTADOS_EN_PROCESO = ['BORRADOR', 'EN_CONTEO', 'CONTEO_FINALIZADO', 'EN_REVISION']

# Una tarea de aplicación EN_PROCESO sin avance en este lapso se considera huérfana
# (el worker de gunicorn murió a mitad del bucle: deploy, OOM, reinicio).
TAREA_HUERFANA_MINUTOS = 30

# Tolerancia para fechas que manda el navegador (resolución de minuto + reloj del PC)
TOLERANCIA_RELOJ = timedelta(minutes=2)

# SKUs OPERATIVOS: viven en el catálogo de cada tienda pero no son mercadería
# (cuadratura de tarjetas, bolsas de empaque, cobro de envíos). En PAO4 son 4 SKUs
# con 13.173 «unidades» (VISA/DIFER VISA 9.914, 45-1 BOLSA CORPORATIVA 1.249,
# BOLSA CALZADOS/PAPEL 1.138, ENVIOS/COSTO ENVIO 870). Nadie los pasa por la
# pistola, así que en una toma completa saldrían como faltante y el ajuste los
# llevaría a 0: se excluyen del análisis en vez de ajustarse. Se reconocen por
# artículo/descripción y no por Producto.excluir_de_analitica: BOLSA CALZADOS no
# lo tiene marcado, y esa marca también se usa para consignación/exhibición, que
# sí es mercadería que se cuenta.
_ARTICULOS_OPERATIVOS = {'VISA', 'ENVIO', 'ENVIOS'}
_FRASES_SKU_OPERATIVO = (
    'DIFER VISA', 'BOLSA CORPORATIVA', 'BOLSA CALZADO', 'BOLSA GENERO',
    'BOLSA PAPEL', 'REAL PAPEL', 'COSTO ENVIO',
)
MARCA_OBSERVACION_OPERATIVO = 'SKU operativo (no es mercadería): excluido del análisis'


def _es_sku_operativo(articulo, descripcion):
    articulo = _sin_tildes(articulo or '').upper()
    texto = f'{articulo} | {_sin_tildes(descripcion or "").upper()}'
    return articulo in _ARTICULOS_OPERATIVOS or any(f in texto for f in _FRASES_SKU_OPERATIVO)


def _ids_operativos(detalles_qs):
    """Ids de detalle (del queryset dado) que corresponden a SKUs operativos.
    La BD preselecciona (la pantalla de análisis lo pide en cada refresco y hay
    tomas de 335.000 líneas) y _es_sku_operativo confirma sin tildes."""
    articulo = 'producto_talla__producto__articulo'
    descripcion = 'producto_talla__producto__descripcion'
    q = Q()
    for nombre in _ARTICULOS_OPERATIVOS:
        q |= Q(**{f'{articulo}__iexact': nombre})
    for frase in _FRASES_SKU_OPERATIVO:
        q |= Q(**{f'{articulo}__icontains': frase}) | Q(**{f'{descripcion}__icontains': frase})
    return [
        d['id'] for d in detalles_qs.filter(q).values('id', articulo, descripcion)
        if _es_sku_operativo(d[articulo], d[descripcion])
    ]


def _normalizar_sku(valor):
    """
    SKU tal como viene del archivo/escáner → clave comparable con la toma.

    `Producto_Talla.sku` es BigInteger, así que la toma guarda '4805622'. Un CSV
    exportado desde Excel trae '4805622.0' y openpyxl puede devolver 4805622.0:
    sin normalizar caían en no_encontrados y el conteo se perdía en silencio.
    """
    if valor is None:
        return ''
    texto = str(valor).strip()
    if not texto:
        return ''
    try:
        numero = float(texto.replace(',', '.'))
        if numero.is_integer():
            return str(int(numero))
    except ValueError:
        pass
    return texto


def _reconteos_pendientes(inventario):
    """
    Líneas que todavía esperan reconteo. ÚNICO criterio para finalizar, enviar,
    aprobar y el análisis: antes finalizar/enviar no filtraban las excluidas y
    una línea excluida con diferencia grande bloqueaba el flujo sin aparecer en
    ninguna pantalla (deadlock de INV-6).
    """
    return inventario.reconteos_pendientes()


def _parsear_fecha_local(valor):
    """'YYYY-MM-DDTHH:MM' (datetime-local, hora America/Santiago) → aware o None."""
    if not valor:
        return None
    texto = str(valor).strip()
    for formato in ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            naive = datetime.strptime(texto, formato)
            break
        except ValueError:
            continue
    else:
        raise ValidationError(f'Fecha inválida: {texto} (use AAAA-MM-DDTHH:MM)')
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


def _resolver_fecha_conteo(inventario, fecha_conteo_str=None, tienda_cerrada=False):
    """
    Momento hasta el cual se consideran movimientos post-corte (N1).

    - Se declara explícitamente (`fecha_conteo`), o
    - «conté con la tienda cerrada»: el conteo físico ocurrió al corte, así que las
      ventas entre el corte y la CARGA del archivo no deben restarse del sistema
      (antes generaban un sobrante falso por cada venta), o
    - escáner en vivo: ahora.
    Se valida corte <= fecha_conteo <= ahora (con tolerancia de reloj).
    """
    ahora = timezone.now()
    if fecha_conteo_str:
        fecha_conteo = _parsear_fecha_local(fecha_conteo_str)
    elif tienda_cerrada:
        fecha_conteo = inventario.fecha_corte
    else:
        return ahora

    if fecha_conteo < inventario.fecha_corte:
        raise ValidationError(
            'La fecha del conteo físico no puede ser anterior a la fecha de corte '
            f'({timezone.localtime(inventario.fecha_corte):%d/%m/%Y %H:%M})'
        )
    if fecha_conteo > ahora + TOLERANCIA_RELOJ:
        raise ValidationError('La fecha del conteo físico no puede ser futura')
    return min(fecha_conteo, ahora)


def _inventario_del_usuario(request, inventario_id):
    """
    Recupera la toma verificando que pertenezca a una empresa/sucursal del usuario.

    Sin esto cualquier usuario autenticado podía abrir (y aprobar) el inventario de
    otra empresa simplemente cambiando el id en la URL.
    """
    inventario = get_object_or_404(
        TomaInventario.objects.select_related('sucursal', 'empresa'), id=inventario_id
    )
    empresas_ids = set(obtener_empresas_usuario(request.user).values_list('id', flat=True))
    if inventario.empresa_id not in empresas_ids:
        return None
    if not puede_ver_sucursal(request.user, inventario.sucursal_id):
        return None
    return inventario


def _error_sin_acceso():
    return JsonResponse(
        {'success': False, 'error': 'No tiene acceso a este inventario'}, status=403
    )


# ==============================================================================
# VISTAS PRINCIPALES
# ==============================================================================

@login_required
def gestion_inventarios(request):
    """Vista principal del módulo de Gestión de Inventarios"""
    return render(request, 'vistas/modulo_existencias/gestion_inventarios.html')


@login_required
def detalle_inventario(request, inventario_id):
    """Vista de detalle de un inventario específico"""
    inventario = _inventario_del_usuario(request, inventario_id)
    if inventario is None:
        raise PermissionDenied('No tiene acceso a este inventario')
    return render(request, 'vistas/modulo_existencias/detalle_inventario.html', {
        'inventario': inventario,
        'puede_aplicar_ajustes': inventario.estado in ('APROBADO', 'APLICANDO'),
        'conteo_tienda_cerrada': inventario.conteo_tienda_cerrada,
        # Para precargar «¿cuándo se contó?» en el modal de importación (hora local)
        'fecha_corte_local': timezone.localtime(inventario.fecha_corte).strftime('%Y-%m-%dT%H:%M'),
        'ahora_local': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
    })


# ==============================================================================
# API: LISTADO Y FILTROS
# ==============================================================================

@require_GET
@login_required
def obtener_inventarios(request):
    """
    Obtener lista de inventarios con filtros y paginación.
    Optimizado para evitar N+1 queries.
    """
    try:
        sucursal_id = request.session.get('idSucursalActual')

        # Antes se filtraba por `EmpresaUser...first().empresa`: con multi-empresa eso
        # tomaba UNA empresa al azar del usuario (un administrador tiene 1.699) y podía
        # dejar fuera inventarios que sí le corresponden.
        empresas_ids = list(obtener_empresas_usuario(request.user).values_list('id', flat=True))
        if not empresas_ids:
            return JsonResponse({'success': False, 'error': 'Usuario sin empresa asignada'})

        # Parámetros de paginación
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 20))
        
        # Parámetros de filtro
        estado = request.GET.get('estado')
        tipo = request.GET.get('tipo')
        fecha_desde = request.GET.get('fecha_desde')
        fecha_hasta = request.GET.get('fecha_hasta')
        search = request.GET.get('search', '').strip()
        grupo = request.GET.get('grupo', '').strip()  # EN_PROCESO | CON_DIFERENCIAS

        # Construir queryset optimizado
        queryset = TomaInventario.objects.select_related(
            'sucursal', 'empresa', 'creado_por', 'aprobado_por'
        ).filter(empresa_id__in=empresas_ids)

        # Filtrar por sucursal actual (respetando el acceso del usuario)
        if sucursal_id and puede_ver_sucursal(request.user, sucursal_id):
            queryset = queryset.filter(sucursal_id=sucursal_id)
        else:
            sucursales_ids = list(
                obtener_sucursales_usuario(request.user).values_list('id', flat=True)
            )
            queryset = queryset.filter(sucursal_id__in=sucursales_ids)

        # Aplicar filtros
        if estado:
            queryset = queryset.filter(estado=estado)
        
        if tipo:
            queryset = queryset.filter(tipo_inventario=tipo)
        
        if fecha_desde:
            queryset = queryset.filter(fecha_corte__date__gte=fecha_desde)
        
        if fecha_hasta:
            queryset = queryset.filter(fecha_corte__date__lte=fecha_hasta)
        
        if search:
            queryset = queryset.filter(
                Q(numero_inventario__icontains=search) |
                Q(nombre__icontains=search)
            )

        # Los tabs "En Proceso" y "Con Diferencias" antes solo cambiaban una variable
        # de JS que nunca se enviaba: filtraban nada.
        if grupo == 'EN_PROCESO':
            queryset = queryset.filter(estado__in=ESTADOS_EN_PROCESO)
        elif grupo == 'CON_DIFERENCIAS':
            queryset = queryset.exclude(estado__in=['COMPLETADO', 'CANCELADO']).filter(
                Q(total_diferencias_positivas__gt=0) | Q(total_diferencias_negativas__gt=0)
            )

        # Resumen sobre TODO el conjunto filtrado (antes los KPIs contaban solo la
        # página visible: con 20 por página el total nunca podía pasar de 20).
        base_resumen = queryset
        resumen = {
            'total': base_resumen.count(),
            'en_proceso': base_resumen.filter(estado__in=ESTADOS_EN_PROCESO).count(),
            'pendientes_aprobacion': base_resumen.filter(estado='PENDIENTE_APROBACION').count(),
            'completados': base_resumen.filter(estado='COMPLETADO').count(),
            'con_diferencias': base_resumen.exclude(estado__in=['COMPLETADO', 'CANCELADO']).filter(
                Q(total_diferencias_positivas__gt=0) | Q(total_diferencias_negativas__gt=0)
            ).count(),
        }

        # Ordenar y paginar
        queryset = queryset.order_by('-created_at')
        paginator = Paginator(queryset, per_page)
        inventarios_page = paginator.get_page(page)

        # SKUs realmente contados (el campo total_productos_contados del modelo guarda
        # UNIDADES, no líneas: mostrarlo contra total_productos_esperados comparaba
        # peras con manzanas — en INV-6-20260115-001 decía "9.490 / 37.389 productos"
        # cuando lo contado eran 4.301 SKUs).
        skus_map = {
            row['toma_inventario_id']: row
            for row in TomaInventarioDetalle.objects
            .filter(toma_inventario__in=list(inventarios_page.object_list), excluir_de_analisis=False)
            .values('toma_inventario_id')
            .annotate(lineas=Count('id'), contados=Count('id', filter=Q(contado=True)))
        }

        # Tareas de aplicación de las tomas APLICANDO de la página: para ofrecer
        # «Reanudar» solo cuando el hilo se dio por muerto (ver _tarea_huerfana).
        tareas_map = {
            t.inventario_id: t
            for t in TareaAplicacionAjustes.objects.filter(
                inventario__in=[i for i in inventarios_page.object_list if i.estado == 'APLICANDO']
            )
        }

        # Serializar datos
        inventarios_data = []
        for inv in inventarios_page:
            conteo = skus_map.get(inv.id) or {}
            tarea = tareas_map.get(inv.id)
            inventarios_data.append({
                'skus_contados': conteo.get('contados', 0),
                'skus_esperados': conteo.get('lineas', inv.total_productos_esperados),
                'unidades_contadas': inv.total_productos_contados,
                'ajustes_aplicados': inv.ajustes_aplicados().count() if inv.estado in ('APROBADO', 'APLICANDO') else 0,
                'tarea_huerfana': bool(tarea and _tarea_huerfana(tarea, inv)),
                'id': inv.id,
                'numero_inventario': inv.numero_inventario,
                'nombre': inv.nombre,
                'sucursal': inv.sucursal.alias,
                'tipo_inventario': inv.tipo_inventario,
                'tipo_inventario_display': inv.get_tipo_inventario_display(),
                'estado': inv.estado,
                'estado_display': inv.get_estado_display(),
                # DateTimeField llega en UTC: sin localtime() el listado mostraba el
                # corte 3-4 h corrido («16/01 00:27» para una toma del 15/01 21:27).
                'fecha_corte': timezone.localtime(inv.fecha_corte).strftime('%d/%m/%Y %H:%M'),
                'progreso_conteo': float(inv.progreso_conteo),
                'total_productos_esperados': inv.total_productos_esperados,
                'total_productos_contados': inv.total_productos_contados,
                'total_diferencias_positivas': inv.total_diferencias_positivas,
                'total_diferencias_negativas': inv.total_diferencias_negativas,
                'valor_diferencias': float(inv.valor_diferencias_positivas - inv.valor_diferencias_negativas),
                'creado_por': inv.creado_por.get_full_name() if inv.creado_por else '',
                'created_at': timezone.localtime(inv.created_at).strftime('%d/%m/%Y %H:%M')
            })
        
        return JsonResponse({
            'success': True,
            'inventarios': inventarios_data,
            'resumen': resumen,
            'pagination': {
                'current_page': inventarios_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': inventarios_page.has_next(),
                'has_previous': inventarios_page.has_previous(),
            }
        })

    except Exception as e:
        logger.error(f"Error al obtener inventarios: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def obtener_filtros_disponibles(request):
    """
    Obtener opciones de filtros disponibles para crear inventario.
    Devuelve marcas, categorías y atributos activos.
    """
    try:
        # Los filtros dependen de la SUCURSAL activa, no de la primera EmpresaUser
        # del usuario (con multi-empresa eso podía ser una empresa ajena a la tienda).
        sucursal_id = request.session.get('idSucursalActual')

        # Marcas y categorías ACOTADAS a la sucursal activa y con stock: antes se
        # ofrecían las de todo el holding, así que era fácil segmentar por una marca
        # que no existe en la tienda y crear una toma vacía.
        productos_suc = Producto.objects.all()
        if sucursal_id:
            productos_suc = productos_suc.filter(sucursal_id=sucursal_id)
        productos_con_stock = productos_suc.filter(producto_talla__stock__gt=0)

        marcas = AtributoOpcion.objects.filter(
            atributo__nombre__icontains='marca',
            productos_marca__in=productos_con_stock
        ).distinct().values('id', 'valor').order_by('valor')

        categorias = Categoria.objects.filter(
            categoria_productos__in=productos_con_stock
        ).distinct().values('id', 'nombre').order_by('nombre')

        # Obtener atributos disponibles (color, género, etc.)
        atributos = Productos_Atributos.objects.filter(
            opciones__isnull=False
        ).distinct().prefetch_related('opciones')
        
        atributos_data = []
        for attr in atributos:
            if attr.nombre.lower() != 'marca':  # Excluir marca que ya tiene su filtro
                atributos_data.append({
                    'id': attr.id,
                    'nombre': attr.nombre,
                    'opciones': list(attr.opciones.values('id', 'valor').order_by('valor'))
                })
        
        return JsonResponse({
            'success': True,
            'filtros': {
                'marcas': list(marcas),
                'categorias': list(categorias),
                'atributos': atributos_data
            }
        })
        
    except Exception as e:
        logger.error(f"Error al obtener filtros: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


# ==============================================================================
# API: CREACIÓN DE INVENTARIO
# ==============================================================================

@require_POST
@login_required
@transaction.atomic
def crear_inventario(request):
    """
    Crear una nueva toma de inventario.
    
    Proceso:
    1. Validar datos de entrada
    2. Crear encabezado de inventario
    3. Generar snapshot de productos según filtros
    4. Calcular stock del sistema en fecha de corte
    """
    try:
        data = json.loads(request.body)
        
        # Validar datos requeridos
        nombre = data.get('nombre')
        tipo_inventario = data.get('tipo_inventario', 'COMPLETO')
        fecha_corte_str = data.get('fecha_corte')
        filtros = data.get('filtros', {})
        
        if not nombre:
            return JsonResponse({'success': False, 'error': 'El nombre es requerido'})

        # Tipos declarados en el modelo pero sin lógica de segmentación implementada:
        # si se aceptan, generan igual un inventario COMPLETO (335.000 líneas en EDEL)
        # haciendo creer al usuario que contará solo una muestra.
        if tipo_inventario in ('SELECTIVO', 'CICLICO', 'ALEATORIO'):
            return JsonResponse({
                'success': False,
                'error': f'El tipo "{tipo_inventario}" todavía no está implementado. '
                         f'Usa Por Marca, Por Categoría o Por Atributo para una toma parcial.'
            })

        segmentado = {
            'POR_MARCA': 'marcas',
            'POR_CATEGORIA': 'categorias',
        }.get(tipo_inventario)
        if segmentado and not filtros.get(segmentado):
            return JsonResponse({
                'success': False,
                'error': 'Debe seleccionar al menos un valor de segmentación para este tipo de inventario'
            })
        if tipo_inventario == 'POR_ATRIBUTO' and not any((filtros.get('atributos') or {}).values()):
            return JsonResponse({
                'success': False,
                'error': 'Debe seleccionar al menos un atributo para este tipo de inventario'
            })

        sucursal_id = request.session.get('idSucursalActual')
        if not sucursal_id:
            return JsonResponse({'success': False, 'error': 'Debe seleccionar una sucursal'})

        if not puede_ver_sucursal(request.user, sucursal_id):
            return JsonResponse({'success': False, 'error': 'No tiene acceso a la sucursal activa'})

        sucursal = get_object_or_404(Sucursal.objects.select_related('empresa'), id=sucursal_id)

        # La empresa de la toma es la DUEÑA de la sucursal. Antes se tomaba la
        # primera EmpresaUser activa del usuario: una toma de NICK1 (1320) quedaba
        # con empresa EDEL (1802) y un usuario de 1320 sin 1802 no la veía.
        if not sucursal.empresa_id:
            return JsonResponse({'success': False, 'error': 'La sucursal activa no tiene empresa asociada'})
        empresa = sucursal.empresa

        # Procesar fecha de corte (llega en hora local desde el datetime-local)
        ahora = timezone.now()
        fecha_corte = _parsear_fecha_local(fecha_corte_str) if fecha_corte_str else ahora
        corte_recortado = False
        if fecha_corte > ahora + TOLERANCIA_RELOJ:
            # Un corte futuro (el modal viejo mandaba UTC = +3/4 h) deja fuera del
            # post-corte todas las ventas hasta esa hora: cada una era un faltante
            # falso que después se rebajaba dos veces. Se recorta a ahora y se deja
            # constancia en el log en vez de rechazar (compatibilidad con clientes
            # que aún manden el valor antiguo).
            logger.warning(
                'crear_inventario: fecha de corte futura %s recortada a %s (sucursal %s)',
                timezone.localtime(fecha_corte), timezone.localtime(ahora), sucursal_id,
            )
            fecha_corte = ahora
            corte_recortado = True
        elif fecha_corte > ahora:
            fecha_corte = ahora

        # «Conté con la tienda cerrada»: el conteo físico corresponde al corte;
        # queda en filtros_aplicados (sin migración) y lo lee TomaInventario.conteo_tienda_cerrada.
        filtros['conteo_tienda_cerrada'] = bool(data.get('conteo_tienda_cerrada') or filtros.get('conteo_tienda_cerrada'))

        # Crear inventario
        numero_inventario = TomaInventario.generar_numero_inventario(sucursal)

        inventario = TomaInventario.objects.create(
            numero_inventario=numero_inventario,
            nombre=nombre,
            sucursal=sucursal,
            empresa=empresa,
            tipo_inventario=tipo_inventario,
            filtros_aplicados=filtros,
            fecha_corte=fecha_corte,
            estado='BORRADOR',
            observaciones=(data.get('observaciones') or '').strip() or None,
            creado_por=request.user
        )

        # Generar detalles de productos a inventariar
        total_productos = _generar_detalles_inventario(inventario, filtros, sucursal_id)

        if total_productos == 0:
            # Sin líneas la toma es inútil y queda basura en el listado.
            raise ValidationError(
                'Los filtros seleccionados no arrojaron productos con stock en esta sucursal.'
            )

        # Actualizar total esperado
        inventario.total_productos_esperados = total_productos
        inventario.save()

        corte_local = timezone.localtime(fecha_corte).strftime('%d/%m/%Y %H:%M')

        # Registrar log
        _registrar_log(
            inventario=inventario,
            tipo_accion='CREACION',
            descripcion=(
                f'Inventario creado con {total_productos} productos a contar. Corte: {corte_local}'
                + (' (la fecha enviada era futura y se recortó a la hora actual)' if corte_recortado else '')
            ),
            usuario=request.user,
            datos={
                'filtros': filtros, 'total_productos': total_productos,
                'fecha_corte_local': corte_local, 'corte_recortado': corte_recortado,
            }
        )

        return JsonResponse({
            'success': True,
            'message': f'Inventario {numero_inventario} creado exitosamente',
            'inventario_id': inventario.id,
            'numero_inventario': numero_inventario,
            'total_productos': total_productos,
            'fecha_corte': corte_local,
            'corte_recortado': corte_recortado,
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'})
    except ValidationError as e:
        # Capturar la excepción DENTRO de @transaction.atomic cancela el rollback:
        # la toma vacía quedaba igual en BD como BORRADOR. Hay que marcarlo a mano.
        transaction.set_rollback(True)
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)})
    except Exception as e:
        transaction.set_rollback(True)
        logger.error(f"Error al crear inventario: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


def _generar_detalles_inventario(inventario, filtros, sucursal_id):
    """
    Genera los detalles del inventario según los filtros.
    Optimizado para grandes volúmenes usando bulk_create.
    """
    # Construir queryset de productos según filtros
    queryset = Producto_Talla.objects.select_related(
        'producto', 
        'producto__atributo1',  # marca
        'producto__categoria'
    ).filter(
        producto__sucursal_id=sucursal_id
    )
    
    # Aplicar filtros
    marcas = filtros.get('marcas', [])
    categorias = filtros.get('categorias', [])
    atributos = filtros.get('atributos', {})
    productos_ids = filtros.get('productos', [])

    if marcas:
        queryset = queryset.filter(producto__atributo1_id__in=marcas)

    if categorias:
        queryset = queryset.filter(producto__categoria_id__in=categorias)

    if productos_ids:
        queryset = queryset.filter(producto_id__in=productos_ids)

    # Filtros de atributos específicos
    for attr_id, opciones in atributos.items():
        if opciones:
            # Atributo2 = color, Atributo3 = género, etc.
            if attr_id == 'color':
                queryset = queryset.filter(producto__atributo2_id__in=opciones)
            elif attr_id == 'genero':
                queryset = queryset.filter(producto__atributo3_id__in=opciones)

    # "Solo productos con stock" evita generar decenas de miles de líneas en cero.
    # En bodega EDEL hay 341.945 tallas y solo 275 con stock: sin este filtro la
    # toma nace con 335.000 líneas imposibles de contar.
    # Es stock AL CORTE, no el de ahora: con un corte de anoche, lo vendido hoy en
    # la mañana (stock actual 0) existía al contar y debe estar en la toma (PAO4
    # 08-10: 3 SKUs vendidos antes de cargar la pistola). También entran los
    # negativos: un inventario completo es justamente lo que los corrige.
    solo_con_stock = filtros.get('solo_con_stock', True)
    if solo_con_stock:
        corte_date, corte_time = _fecha_hora_local(inventario.fecha_corte)
        movidos_post_corte = Movimientos_Producto.objects.filter(
            Q(sucursal_destino_id=sucursal_id) | Q(sucursal_origen_id=sucursal_id),
            ProductoTalla__producto__sucursal_id=sucursal_id,
        ).filter(
            Q(fecha__gt=corte_date) | Q(fecha=corte_date, hora__gt=corte_time)
        ).values('ProductoTalla_id')
        queryset = queryset.filter(~Q(stock=0) | Q(id__in=movidos_post_corte))

    # Procesar productos en lotes para reducir consultas N+1
    detalles = []
    batch = []

    def _procesar_batch(batch_items):
        if not batch_items:
            return

        ids = [pt.id for pt in batch_items]
        # Movimientos ocurridos DESPUÉS del corte: el stock al corte se reconstruye
        # restándolos del stock actual (ver _obtener_movimientos_desde_corte_batch).
        posteriores_map = _obtener_movimientos_desde_corte_batch(ids, inventario.fecha_corte, sucursal_id)
        costo_map = _obtener_costo_promedio_batch(ids)

        for pt in batch_items:
            posteriores = posteriores_map.get(pt.id, 0)
            if solo_con_stock and not pt.stock and not ((pt.stock or 0) - posteriores):
                continue  # en 0 al corte y en 0 ahora: nada que contar
            detalles.append(_nuevo_detalle_desde_pt(
                inventario, pt, posteriores, costo_map.get(pt.id)
            ))

        if len(detalles) >= BATCH_SIZE:
            TomaInventarioDetalle.objects.bulk_create(detalles, ignore_conflicts=True)
            detalles.clear()

    for pt in queryset.iterator(chunk_size=BATCH_SIZE):
        batch.append(pt)
        if len(batch) >= BATCH_SIZE:
            _procesar_batch(batch)
            batch = []

    if batch:
        _procesar_batch(batch)

    if detalles:
        TomaInventarioDetalle.objects.bulk_create(detalles, ignore_conflicts=True)

    return inventario.detalles.count()


def _nuevo_detalle_desde_pt(inventario, pt, movimientos_posteriores=0, costo_promedio=None):
    """
    Línea de la toma (sin guardar) para un Producto_Talla, con el snapshot al corte.

    Stock del sistema en la fecha de corte.
    OJO: la base SIEMPRE parte del stock plano (Producto_Talla.stock), que es el
    número que usa el resto del ERP (POS, reportes, ecommerce). Antes se usaba la
    suma del kardex y, como kardex y stock plano no cuadran en 126k SKUs, el
    ajuste dejaba el stock distinto de lo contado.
    """
    stock_sistema = (pt.stock or 0) - (movimientos_posteriores or 0)

    # Costo promedio FIFO; si no hay lotes, el costo del producto
    if costo_promedio is None:
        costo_promedio = Decimal(pt.producto.costo or 0)

    marca_nombre = pt.producto.atributo1.valor if pt.producto.atributo1 else ''
    categoria_nombre = pt.producto.categoria.nombre if pt.producto.categoria else ''

    return TomaInventarioDetalle(
        toma_inventario=inventario,
        producto_talla=pt,
        sku=str(pt.sku),
        producto_nombre=pt.producto.articulo,
        talla_nombre=pt.talla if pt.talla else '',
        marca_nombre=marca_nombre,
        categoria_nombre=categoria_nombre,
        stock_sistema=stock_sistema,
        stock_movimientos_post_corte=0,
        stock_sistema_ajustado=stock_sistema,
        costo_unitario_sistema=costo_promedio,
        precio_venta_sistema=Decimal(pt.producto.precioventa or 0)
    )


def _agregar_detalles_al_vuelo(inventario, skus):
    """
    SKUs contados que NO tienen línea en la toma (típicamente stock 0 en sistema,
    fuera de una toma «solo con stock»): si el SKU existe en la sucursal se agrega
    como línea con stock_sistema reconstruido al corte (stock actual − movimientos
    posteriores) y costo FIFO, para que el sobrante se ajuste en vez de perderse
    en no_encontrados. Justo el caso que motiva contar: físico sin sistema.

    Devuelve (detalles_por_sku_agregados, no_encontrados, ambiguos).
    `ambiguos` son SKUs con más de un Producto_Talla en la sucursal (duplicados
    históricos del catálogo, 379 en EDEL): no se puede saber cuál copia se contó.
    """
    agregados, no_encontrados, ambiguos = {}, [], []
    numericos = {}
    for sku in skus:
        clave = _normalizar_sku(sku)
        if clave.isdigit():
            numericos[clave] = sku
        else:
            no_encontrados.append(sku)
    if not numericos:
        return agregados, no_encontrados, ambiguos

    candidatos = list(
        Producto_Talla.objects.filter(
            producto__sucursal_id=inventario.sucursal_id,
            sku__in=[int(k) for k in numericos],
        ).select_related('producto', 'producto__atributo1', 'producto__categoria')
    )
    por_sku = {}
    for pt in candidatos:
        por_sku.setdefault(str(pt.sku), []).append(pt)

    ya_en_toma = set(
        inventario.detalles.filter(
            producto_talla_id__in=[pt.id for pt in candidatos]
        ).values_list('producto_talla_id', flat=True)
    )

    a_crear = []
    for clave, original in numericos.items():
        pts = [pt for pt in por_sku.get(clave, []) if pt.id not in ya_en_toma]
        if not pts:
            no_encontrados.append(original)
        elif len(pts) > 1:
            ambiguos.append(original)
        else:
            a_crear.append(pts[0])

    if a_crear:
        ids = [pt.id for pt in a_crear]
        posteriores = _obtener_movimientos_desde_corte_batch(ids, inventario.fecha_corte, inventario.sucursal_id)
        costos = _obtener_costo_promedio_batch(ids)
        nuevos = [
            _nuevo_detalle_desde_pt(inventario, pt, posteriores.get(pt.id, 0), costos.get(pt.id))
            for pt in a_crear
        ]
        TomaInventarioDetalle.objects.bulk_create(nuevos, ignore_conflicts=True)
        for det in inventario.detalles.filter(producto_talla_id__in=ids).select_related('producto_talla'):
            agregados[_normalizar_sku(det.sku)] = det

    return agregados, no_encontrados, ambiguos


def _fecha_hora_local(momento):
    """
    Movimientos_Producto guarda `fecha`/`hora` en horario local (timezone.localdate()
    y timezone.localtime()). Los DateTimeField llegan en UTC, así que comparar
    `momento.date()` contra `Movimientos_Producto.fecha` corría el corte 3-4 horas
    (todo lo vendido después de las 20:00 caía en el día siguiente).
    """
    local = timezone.localtime(momento) if timezone.is_aware(momento) else momento
    return local.date(), local.time()


def _obtener_movimientos_desde_corte_batch(producto_talla_ids, fecha_corte, sucursal_id):
    """
    Suma neta de movimientos ocurridos DESPUÉS de la fecha de corte y hasta ahora.

    Sirve para reconstruir el stock al corte: stock_al_corte = stock_actual - esta suma.
    Así la base de comparación queda anclada al stock plano (el que ve el POS) y no
    a la suma del kardex, que en producción difiere en 126.455 SKUs.
    """
    corte_date, corte_time = _fecha_hora_local(fecha_corte)

    movimientos = Movimientos_Producto.objects.filter(
        ProductoTalla_id__in=producto_talla_ids
    ).filter(
        Q(sucursal_destino_id=sucursal_id) | Q(sucursal_origen_id=sucursal_id)
    ).filter(
        Q(fecha__gt=corte_date) |
        Q(fecha=corte_date, hora__gt=corte_time)
    ).values('ProductoTalla_id').annotate(
        total_cantidad=Coalesce(Sum('cantidad'), 0)
    )

    return {m['ProductoTalla_id']: m['total_cantidad'] for m in movimientos}


def _obtener_costo_promedio_batch(producto_talla_ids):
    """
    Calcula costo promedio ponderado FIFO por lote de productos.
    Evita N+1 consultando lotes agregados.
    """
    lotes = LoteProducto.objects.filter(
        producto_talla_id__in=producto_talla_ids,
        cantidad_disponible__gt=0,
        activo=True
    ).values('producto_talla_id').annotate(
        total_valor=Coalesce(
            Sum(ExpressionWrapper(
                F('cantidad_disponible') * F('costo_unitario'),
                output_field=DecimalField(max_digits=18, decimal_places=6)
            )),
            Value(0),
            output_field=DecimalField(max_digits=18, decimal_places=6)
        ),
        total_cantidad=Coalesce(Sum('cantidad_disponible'), 0)
    )

    costo_map = {}
    for lote in lotes:
        if lote['total_cantidad'] > 0:
            costo_map[lote['producto_talla_id']] = lote['total_valor'] / lote['total_cantidad']

    return costo_map


def _obtener_movimientos_post_corte_batch(producto_talla_ids, fecha_corte, fecha_conteo, sucursal_id):
    """
    Obtiene la suma neta de movimientos entre fecha de corte y fecha de conteo.
    Considera sucursal origen/destino y respeta fecha/hora.
    """
    fecha_corte_date, fecha_corte_time = _fecha_hora_local(fecha_corte)
    fecha_conteo_date, fecha_conteo_time = _fecha_hora_local(fecha_conteo)

    movimientos = Movimientos_Producto.objects.filter(
        ProductoTalla_id__in=producto_talla_ids
    ).filter(
        Q(sucursal_destino_id=sucursal_id) | Q(sucursal_origen_id=sucursal_id)
    ).filter(
        Q(fecha__gt=fecha_corte_date) |
        Q(fecha=fecha_corte_date, hora__gt=fecha_corte_time)
    ).filter(
        Q(fecha__lt=fecha_conteo_date) |
        Q(fecha=fecha_conteo_date, hora__lte=fecha_conteo_time)
    ).values('ProductoTalla_id').annotate(
        total_cantidad=Coalesce(Sum('cantidad'), 0)
    )

    movimientos_map = {}
    for mov in movimientos:
        movimientos_map[mov['ProductoTalla_id']] = mov['total_cantidad']

    return movimientos_map


# NOTA: se eliminaron _calcular_stock_fecha_corte() y _calcular_costo_promedio_fifo().
# Estaban muertas (nadie las llamaba) y la primera reconstruía el stock desde
# tipo_movimiento='INGRESO'/'EGRESO', clasificación que en este proyecto no es
# confiable (el default del modelo es 'INGRESO'). El stock al corte ahora se
# calcula en _procesar_batch a partir del stock plano.


# ==============================================================================
# API: CONTEO DE PRODUCTOS
# ==============================================================================

@require_GET
@login_required
def obtener_productos_conteo(request, inventario_id):
    """
    Obtener productos para conteo con paginación y filtros.
    Optimizado para grandes volúmenes.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        # Parámetros
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 50))
        estado_conteo = request.GET.get('estado_conteo')  # contado, pendiente, reconteo
        search = request.GET.get('search', '').strip()
        solo_diferencias = request.GET.get('solo_diferencias') == 'true'
        signo = request.GET.get('signo', '')  # positiva | negativa | cero
        marca = request.GET.get('marca')
        categoria = request.GET.get('categoria')
        
        # Construir queryset
        queryset = inventario.detalles.all()
        
        if estado_conteo == 'contado':
            queryset = queryset.filter(contado=True)
        elif estado_conteo == 'pendiente':
            queryset = queryset.filter(contado=False)
        elif estado_conteo == 'reconteo':
            queryset = queryset.filter(reconteo_requerido=True, stock_reconteo__isnull=True)
        
        if search:
            queryset = queryset.filter(
                Q(sku__icontains=search) |
                Q(producto_nombre__icontains=search)
            )
        
        if solo_diferencias:
            queryset = queryset.filter(contado=True).exclude(diferencia=0)

        # Permite que las tarjetas "Sobrantes"/"Faltantes"/"Coinciden" del detalle
        # filtren la tabla en vez de ser sólo decorativas
        if signo == 'positiva':
            queryset = queryset.filter(contado=True, diferencia__gt=0)
        elif signo == 'negativa':
            queryset = queryset.filter(contado=True, diferencia__lt=0)
        elif signo == 'cero':
            queryset = queryset.filter(contado=True, diferencia=0)

        if marca:
            queryset = queryset.filter(marca_nombre__icontains=marca)

        if categoria:
            queryset = queryset.filter(categoria_nombre__icontains=categoria)

        # Ordenar
        queryset = queryset.order_by('producto_nombre', 'talla_nombre')
        
        # Paginar
        paginator = Paginator(queryset, per_page)
        productos_page = paginator.get_page(page)
        
        # Serializar
        productos_data = []
        for det in productos_page:
            productos_data.append({
                'id': det.id,
                'sku': det.sku,
                'producto_nombre': det.producto_nombre,
                'talla_nombre': det.talla_nombre,
                'marca_nombre': det.marca_nombre,
                'categoria_nombre': det.categoria_nombre,
                'stock_sistema': det.stock_sistema,
                'stock_movimientos_post_corte': det.stock_movimientos_post_corte,
                'stock_sistema_ajustado': det.stock_sistema_ajustado,
                'stock_fisico': det.stock_fisico,
                'diferencia': det.diferencia,
                'porcentaje_diferencia': round(det.porcentaje_diferencia, 2),
                'valor_diferencia': float(det.valor_diferencia),
                'contado': det.contado,
                'excluido': det.excluir_de_analisis,
                'fecha_conteo': det.fecha_conteo.strftime('%d/%m/%Y %H:%M') if det.fecha_conteo else None,
                'reconteo_requerido': det.reconteo_requerido,
                'stock_reconteo': det.stock_reconteo,
                'ubicacion': det.ubicacion,
                'observaciones': det.observaciones,
                'costo_unitario': float(det.costo_unitario_sistema),
                'precio_venta': float(det.precio_venta_sistema)
            })
        
        return JsonResponse({
            'success': True,
            'productos': productos_data,
            'pagination': {
                'current_page': productos_page.number,
                'total_pages': paginator.num_pages,
                'total_items': paginator.count,
                'has_next': productos_page.has_next(),
                'has_previous': productos_page.has_previous(),
            }
        })
        
    except Exception as e:
        logger.error(f"Error al obtener productos para conteo: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_POST
@login_required
@transaction.atomic
def registrar_conteo(request, inventario_id):
    """
    Registrar conteo físico de uno o más productos.
    Soporta conteo individual y masivo.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        # Verificar estado
        if inventario.estado not in ['BORRADOR', 'EN_CONTEO']:
            return JsonResponse({
                'success': False, 
                'error': 'El inventario no está en estado de conteo'
            })
        
        data = json.loads(request.body)
        conteos = data.get('conteos', [])

        if not conteos:
            return JsonResponse({'success': False, 'error': 'No hay conteos para registrar'})

        # Momento del conteo físico (N1): por defecto AHORA (escáner / tabla en vivo);
        # `fecha_conteo` explícita o `conteo_tienda_cerrada` → la fecha de corte.
        fecha_conteo = _resolver_fecha_conteo(
            inventario, data.get('fecha_conteo'), bool(data.get('conteo_tienda_cerrada'))
        )

        # Actualizar estado si es el primer conteo
        if inventario.estado == 'BORRADOR':
            inventario.estado = 'EN_CONTEO'
            inventario.fecha_inicio_conteo = timezone.now()
            inventario.save()

        # Procesar conteos
        conteos_realizados = 0
        errores = []
        agregados = []

        conteos_map = {
            c.get('detalle_id'): c for c in conteos if c.get('detalle_id') is not None
        }
        detalle_ids = list(conteos_map.keys())
        detalles = list(inventario.detalles.filter(id__in=detalle_ids).select_related('producto_talla'))
        detalles_por_id = {d.id: d for d in detalles}

        # Conteos por SKU sin línea en la toma (escáner sobre un SKU con stock 0):
        # se agrega la línea al vuelo si el SKU existe en la sucursal (H4).
        por_sku = {
            _normalizar_sku(c.get('sku')): c
            for c in conteos if c.get('detalle_id') is None and _normalizar_sku(c.get('sku'))
        }
        if por_sku:
            existentes = {
                _normalizar_sku(d.sku): d
                for d in inventario.detalles.filter(sku__in=list(por_sku.keys())).select_related('producto_talla')
            }
            faltan = [s for s in por_sku if s not in existentes]
            nuevos, no_encontrados, ambiguos = _agregar_detalles_al_vuelo(inventario, faltan)
            existentes.update(nuevos)
            for sku in no_encontrados:
                errores.append(f'SKU {sku} no existe en esta sucursal (créelo o tráigalo por traspaso)')
            for sku in ambiguos:
                errores.append(f'SKU {sku} ambiguo: hay más de un producto con ese código en la sucursal')
            for sku, conteo in por_sku.items():
                det = existentes.get(sku)
                if det is None:
                    continue
                if sku in nuevos:
                    agregados.append(sku)
                detalles_por_id[det.id] = det
                conteos_map[det.id] = conteo

        producto_talla_ids = [d.producto_talla_id for d in detalles_por_id.values()]
        movimientos_map = _obtener_movimientos_post_corte_batch(
            producto_talla_ids, inventario.fecha_corte, fecha_conteo, inventario.sucursal_id
        )

        for detalle_id, conteo in conteos_map.items():
            detalle = detalles_por_id.get(detalle_id)
            if not detalle:
                errores.append(f"Detalle {detalle_id} no encontrado")
                continue

            stock_fisico = conteo.get('stock_fisico')
            ubicacion = conteo.get('ubicacion', '')
            observaciones = conteo.get('observaciones', '')

            try:
                cantidad = int(stock_fisico)
                if cantidad < 0:
                    # Un −5 tipeado por error producía un faltante mayor que el stock y
                    # después «dejaría el stock en negativo» al aplicar.
                    errores.append(f"SKU {detalle.sku}: cantidad negativa ({cantidad}) no permitida")
                    continue
                movimientos_post_corte = movimientos_map.get(detalle.producto_talla_id, 0)
                detalle.stock_movimientos_post_corte = movimientos_post_corte
                detalle.stock_sistema_ajustado = detalle.stock_sistema + movimientos_post_corte
                detalle.stock_fisico = cantidad
                detalle.contado = True
                detalle.fecha_conteo = fecha_conteo
                detalle.usuario_conteo = request.user
                detalle.ubicacion = ubicacion
                detalle.observaciones = observaciones
                detalle.save()  # El save() calcula diferencia automáticamente
                conteos_realizados += 1
            except (TypeError, ValueError):
                errores.append(f"SKU {detalle.sku}: cantidad inválida ({stock_fisico!r})")
            except Exception as e:
                errores.append(f"Error en detalle {detalle_id}: {str(e)}")

        # Recalcular métricas del inventario
        inventario.calcular_metricas()

        # Registrar log
        _registrar_log(
            inventario=inventario,
            tipo_accion='REGISTRO_CONTEO',
            descripcion=(
                f'{conteos_realizados} productos contados'
                + (f', {len(agregados)} SKU agregados a la toma' if agregados else '')
            ),
            usuario=request.user,
            datos={
                'conteos_realizados': conteos_realizados, 'errores': errores,
                'agregados': agregados,
                'fecha_conteo': timezone.localtime(fecha_conteo).strftime('%d/%m/%Y %H:%M'),
            }
        )

        return JsonResponse({
            'success': True,
            'message': f'{conteos_realizados} conteos registrados',
            'conteos_realizados': conteos_realizados,
            'agregados': agregados,
            'errores': errores if errores else None,
            'progreso': float(inventario.progreso_conteo)
        })

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'})
    except ValidationError as e:
        transaction.set_rollback(True)
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)})
    except Exception as e:
        transaction.set_rollback(True)
        logger.error(f"Error al registrar conteo: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


def _leer_archivo_conteo(archivo, nombre_hoja='', max_rows=None, read_only=False):
    filas = []
    total_leidas = 0

    if archivo.name.lower().endswith('.xlsx'):
        try:
            import openpyxl
        except ImportError:
            return None, 'openpyxl no está instalado'

        wb = openpyxl.load_workbook(archivo, data_only=True, read_only=read_only)
        hoja = wb.active
        if nombre_hoja:
            if nombre_hoja not in wb.sheetnames:
                return None, f'No existe la hoja "{nombre_hoja}"'
            hoja = wb[nombre_hoja]

        for row in hoja.iter_rows(values_only=True):
            fila = [str(c).strip() if c is not None else '' for c in row]
            if any(fila):
                filas.append(fila)
                total_leidas += 1
                if max_rows and total_leidas >= max_rows:
                    break
    else:
        sample = archivo.read(4096)
        archivo.seek(0)
        try:
            sample_text = sample.decode('utf-8-sig')
        except UnicodeDecodeError:
            sample_text = sample.decode('latin-1', errors='replace')

        if not sample_text.strip():
            return None, 'El archivo está vacío'

        try:
            dialect = csv.Sniffer().sniff(sample_text, delimiters=";,|\t,")
        except csv.Error:
            dialect = csv.excel

        text_stream = io.TextIOWrapper(archivo, encoding='utf-8-sig', errors='replace')
        reader = csv.reader(text_stream, dialect)
        for fila in reader:
            if any(c.strip() for c in fila):
                filas.append(fila)
                total_leidas += 1
                if max_rows and total_leidas >= max_rows:
                    break

    if not filas:
        return None, 'No se encontraron filas válidas'

    return filas, None


def _sin_tildes(texto):
    import unicodedata
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn'
    ).lower().strip()


# Encabezados que identifican la columna del CONTEO FÍSICO, en orden de prioridad
# («fisico contado» gana a «cantidad»; «stock» solo si no dice «sistema»).
_CLAVES_CONTEO = ['fisico', 'contado', 'conteo', 'pistola', 'cantidad', 'cant', 'qty', 'unidades']
_CLAVES_SKU = ['sku', 'codigo_barra', 'codigo de barra', 'codigo', 'barcode', 'barra', 'ean']
# Un encabezado con estas palabras es el stock del SISTEMA, nunca el conteo.
_CLAVES_SISTEMA = ['sistema', 'teorico', 'erp', 'diferencia', 'dif']


def _detectar_indices_conteo(filas, sku_col=None, cantidad_col=None):
    """
    Decide qué columna es el SKU y cuál el conteo físico.

    Devuelve (sku_idx, cantidad_idx, tiene_encabezado, encabezados, error).

    El Excel de la tienda es «Sucursal | SKU | Artículo | ... | Stock sistema |
    ... | Físico contado». El detector antiguo solo casaba claves EXACTAS
    ('cantidad', 'stock', ...) y, al no casar ninguna, tomaba la columna 1: el
    conteo se importaba desde «Stock sistema», la toma salía perfecta y no
    ajustaba nada. Ahora se busca por «contiene», se descarta todo lo que diga
    «sistema» y, si no se identifica la columna de conteo, se devuelve error en
    vez de adivinar. `sku_col` / `cantidad_col` (índice 0-based o nombre de la
    columna) los fija el usuario desde el modal y mandan sobre la heurística.
    """
    encabezado_raw = [str(c).strip() for c in filas[0]]
    encabezado = [_sin_tildes(c) for c in encabezado_raw]
    tiene_encabezado = any(
        any(k in c for k in ('sku', 'codigo', 'cantidad', 'stock', 'fisico', 'conteo', 'articulo'))
        for c in encabezado
    )

    def _resolver_col(valor):
        """Índice explícito (0-based) o nombre de columna → índice, o None."""
        if valor is None or str(valor).strip() == '':
            return None
        texto = str(valor).strip()
        if texto.lstrip('-').isdigit():
            idx = int(texto)
            return idx if 0 <= idx < len(encabezado_raw) else None
        buscado = _sin_tildes(texto)
        for i, c in enumerate(encabezado):
            if c == buscado:
                return i
        return None

    sku_idx = _resolver_col(sku_col)
    cantidad_idx = _resolver_col(cantidad_col)

    if not tiene_encabezado:
        # Formato de pistola sin encabezado: sku,cantidad
        return (0 if sku_idx is None else sku_idx), (1 if cantidad_idx is None else cantidad_idx), False, encabezado_raw, None

    if sku_idx is None:
        for key in _CLAVES_SKU:
            sku_idx = next((i for i, c in enumerate(encabezado) if key in c), None)
            if sku_idx is not None:
                break
        if sku_idx is None:
            sku_idx = 0

    if cantidad_idx is None:
        candidatas = [
            i for i, c in enumerate(encabezado)
            if i != sku_idx and not any(s in c for s in _CLAVES_SISTEMA)
        ]
        for key in _CLAVES_CONTEO:
            encontrados = [i for i in candidatas if key in encabezado[i]]
            if len(encontrados) == 1:
                cantidad_idx = encontrados[0]
                break
            if len(encontrados) > 1:
                nombres = ', '.join(f'"{encabezado_raw[i]}"' for i in encontrados)
                return sku_idx, None, True, encabezado_raw, (
                    f'Varias columnas podrían ser el conteo físico ({nombres}). '
                    f'Seleccione cuál usar.'
                )
        if cantidad_idx is None:
            # Solo «stock» a secas (sin «sistema»), y solo si es única
            solo_stock = [i for i in candidatas if 'stock' in encabezado[i]]
            if len(solo_stock) == 1:
                cantidad_idx = solo_stock[0]
        if cantidad_idx is None:
            return sku_idx, None, True, encabezado_raw, (
                'No se identificó la columna del conteo físico en el encabezado '
                f'({", ".join(encabezado_raw)}). Seleccione la columna a importar.'
            )

    if cantidad_idx == sku_idx:
        return sku_idx, None, True, encabezado_raw, 'La columna de SKU y la de conteo no pueden ser la misma'

    return sku_idx, cantidad_idx, True, encabezado_raw, None


def _parsear_cantidad(cantidad_raw):
    """'4', '4.0', '4,0' → 4. Lanza ValueError si no es número o es negativa."""
    cantidad = int(float(str(cantidad_raw).strip().replace(',', '.')))
    if cantidad < 0:
        raise ValueError('cantidad negativa')
    return cantidad


def _extraer_preview_conteo(filas, sku_idx, cantidad_idx, limite=20):
    preview = []
    errores = []

    for fila in filas:
        if len(preview) >= limite:
            break
        if len(fila) <= max(sku_idx, cantidad_idx):
            continue
        sku = _normalizar_sku(fila[sku_idx])
        cantidad_raw = str(fila[cantidad_idx]).strip()
        if not sku:
            continue
        try:
            cantidad = _parsear_cantidad(cantidad_raw)
            preview.append({'sku': sku, 'cantidad': cantidad, 'valido': True})
        except ValueError:
            preview.append({'sku': sku, 'cantidad': cantidad_raw, 'valido': False})
            errores.append(f"Cantidad inválida para SKU {sku}: {cantidad_raw}")

    return preview, errores


@require_POST
@login_required
@transaction.atomic
def importar_conteo_pistola(request, inventario_id):
    """
    Importa conteos desde archivo de pistola (CSV/TXT/XLSX).
    Formato esperado: sku,cantidad (con o sin encabezados).
    """
    inventario = _inventario_del_usuario(request, inventario_id)
    if inventario is None:
        return _error_sin_acceso()

    archivo = request.FILES.get('archivo')
    if not archivo:
        return JsonResponse({'success': False, 'error': 'Debe adjuntar un archivo'})

    # Sin esta guarda se podían importar conteos sobre un inventario ya APROBADO o
    # COMPLETADO, cambiando las diferencias después de la aprobación.
    if inventario.estado not in ['BORRADOR', 'EN_CONTEO']:
        return JsonResponse({
            'success': False,
            'error': f'El inventario está en estado {inventario.get_estado_display()} y ya no admite conteos'
        })

    try:
        nombre_hoja = request.POST.get('nombre_hoja', '').strip()

        # Momento del conteo físico (N1). Para un archivo el default es el corte si
        # la toma (o este envío) declara «conté con la tienda cerrada»; si no, ahora.
        tienda_cerrada = (
            str(request.POST.get('conteo_tienda_cerrada', '')).lower() in ('1', 'true', 'on', 'si', 'sí')
            or (not request.POST.get('fecha_conteo') and inventario.conteo_tienda_cerrada)
        )
        fecha_conteo = _resolver_fecha_conteo(inventario, request.POST.get('fecha_conteo'), tienda_cerrada)

        filas, error = _leer_archivo_conteo(archivo, nombre_hoja)
        if error:
            return JsonResponse({'success': False, 'error': error})

        sku_idx, cantidad_idx, tiene_encabezado, encabezados, error_cols = _detectar_indices_conteo(
            filas, request.POST.get('sku_col'), request.POST.get('cantidad_col')
        )
        if error_cols:
            return JsonResponse({'success': False, 'error': error_cols, 'encabezados': encabezados})
        if tiene_encabezado:
            filas = filas[1:]

        conteos_por_sku = {}
        errores = []
        for fila in filas:
            if len(fila) <= max(sku_idx, cantidad_idx):
                continue
            sku = _normalizar_sku(fila[sku_idx])
            cantidad_raw = str(fila[cantidad_idx]).strip()
            if not sku:
                continue
            try:
                cantidad = _parsear_cantidad(cantidad_raw)
            except ValueError:
                errores.append(f"Cantidad inválida para SKU {sku}: {cantidad_raw}")
                continue
            conteos_por_sku[sku] = conteos_por_sku.get(sku, 0) + cantidad

        if not conteos_por_sku:
            return JsonResponse({'success': False, 'error': 'No se encontraron conteos válidos'})

        # Actualizar estado si es el primer conteo (después de validar el archivo:
        # un archivo inválido no debe mover la toma de BORRADOR)
        if inventario.estado == 'BORRADOR':
            inventario.estado = 'EN_CONTEO'
            inventario.fecha_inicio_conteo = inventario.fecha_inicio_conteo or timezone.now()
            inventario.save()

        detalles = inventario.detalles.filter(sku__in=conteos_por_sku.keys()).select_related('producto_talla')
        detalles_por_sku = {}
        skus_repetidos = set()
        for d in detalles:
            clave = _normalizar_sku(d.sku)
            if clave in detalles_por_sku:
                skus_repetidos.add(clave)  # dos Producto_Talla con el mismo sku (N8)
            detalles_por_sku[clave] = d

        # SKUs contados que no están en la toma: se agregan al vuelo si existen en
        # la sucursal (sobrantes de SKUs con stock 0), H4.
        faltan = [s for s in conteos_por_sku if s not in detalles_por_sku]
        nuevos, no_encontrados, ambiguos = _agregar_detalles_al_vuelo(inventario, faltan)
        detalles_por_sku.update(nuevos)
        agregados = sorted(nuevos.keys())
        for sku in skus_repetidos:
            ambiguos.append(sku)
            detalles_por_sku.pop(sku, None)
        for sku in ambiguos:
            errores.append(f'SKU {sku} ambiguo: hay más de un producto con ese código en la sucursal; cuéntelo desde la tabla')

        movimientos_map = _obtener_movimientos_post_corte_batch(
            [d.producto_talla_id for d in detalles_por_sku.values()],
            inventario.fecha_corte,
            fecha_conteo,
            inventario.sucursal_id
        )

        actualizados = 0
        sobreescritos = []
        a_guardar = []
        for sku, cantidad in conteos_por_sku.items():
            detalle = detalles_por_sku.get(sku)
            if not detalle:
                continue

            if detalle.contado and detalle.stock_fisico != cantidad:
                sobreescritos.append({'sku': sku, 'anterior': detalle.stock_fisico, 'nuevo': cantidad})
            movimientos_post_corte = movimientos_map.get(detalle.producto_talla_id, 0)
            detalle.stock_movimientos_post_corte = movimientos_post_corte
            detalle.stock_sistema_ajustado = detalle.stock_sistema + movimientos_post_corte
            detalle.stock_fisico = cantidad
            detalle.contado = True
            detalle.fecha_conteo = fecha_conteo
            detalle.usuario_conteo = request.user
            detalle.recalcular_diferencia()  # lo mismo que haría save()
            a_guardar.append(detalle)
            actualizados += 1
        # En lotes: una tienda completa (PAO4: 8.175 SKUs) eran 8.175 UPDATE de a
        # uno y la carga podía pasar el timeout de 60 s de gunicorn y perderse.
        TomaInventarioDetalle.objects.bulk_update(a_guardar, [
            'stock_movimientos_post_corte', 'stock_sistema_ajustado', 'stock_fisico',
            'contado', 'fecha_conteo', 'usuario_conteo', 'diferencia', 'reconteo_requerido',
        ], batch_size=BATCH_SIZE)

        inventario.calcular_metricas()

        # Con cantidad: el informe final los lista en «No cargados» (mercadería que
        # está en la tienda pero no en el sistema de la sucursal).
        no_encontrados_detalle = [
            {'sku': sku, 'cantidad': conteos_por_sku.get(sku)} for sku in no_encontrados
        ]
        unidades_leidas = sum(conteos_por_sku.values())
        fecha_conteo_local = timezone.localtime(fecha_conteo).strftime('%d/%m/%Y %H:%M')
        _registrar_log(
            inventario=inventario,
            tipo_accion='REGISTRO_CONTEO',
            descripcion=(
                f'Importación de archivo: {actualizados} productos actualizados'
                f' ({len(conteos_por_sku)} SKU / {unidades_leidas} u. leídas)'
                + (f', {len(agregados)} SKU agregados a la toma' if agregados else '')
                + (f', {len(no_encontrados)} códigos no existen en la sucursal' if no_encontrados else '')
                + f'. Conteo físico al {fecha_conteo_local}'
                + f' (columnas: SKU={encabezados[sku_idx] if tiene_encabezado else sku_idx}, '
                  f'conteo={encabezados[cantidad_idx] if tiene_encabezado else cantidad_idx})'
            ),
            usuario=request.user,
            datos={
                'actualizados': actualizados, 'agregados': agregados,
                'no_encontrados': no_encontrados, 'no_encontrados_detalle': no_encontrados_detalle,
                'ambiguos': ambiguos, 'errores': errores,
                'sobreescritos': sobreescritos[:200], 'fecha_conteo': fecha_conteo_local,
                'archivo': archivo.name, 'skus_leidos': len(conteos_por_sku),
                'unidades_leidas': unidades_leidas,
            }
        )

        return JsonResponse({
            'success': True,
            'actualizados': actualizados,
            'agregados': agregados,
            'no_encontrados': no_encontrados,
            'no_encontrados_unidades': sum(d['cantidad'] or 0 for d in no_encontrados_detalle),
            'ambiguos': ambiguos,
            'sobreescritos': sobreescritos,
            'fecha_conteo': fecha_conteo_local,
            'skus_leidos': len(conteos_por_sku),
            'unidades_leidas': unidades_leidas,
            'errores': errores if errores else None,
            'progreso': float(inventario.progreso_conteo)
        })
    except ValidationError as e:
        transaction.set_rollback(True)
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)})
    except Exception as e:
        transaction.set_rollback(True)
        logger.error(f"Error al importar conteo: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_POST
@login_required
@transaction.atomic
def actualizar_exclusion_detalle(request, inventario_id, detalle_id):
    """
    Marca un detalle como excluido/incluido del análisis.
    """
    inventario = _inventario_del_usuario(request, inventario_id)
    if inventario is None:
        return _error_sin_acceso()
    # Excluir/incluir después de aprobar cambia lo que se ajusta al stock
    if inventario.estado not in ['BORRADOR', 'EN_CONTEO', 'CONTEO_FINALIZADO', 'EN_REVISION']:
        return JsonResponse({
            'success': False,
            'error': f'El inventario está en estado {inventario.get_estado_display()}: '
                     f'ya no se pueden cambiar exclusiones'
        })

    try:
        data = json.loads(request.body)
        excluir = bool(data.get('excluir'))
        detalle = inventario.detalles.get(id=detalle_id)
        detalle.excluir_de_analisis = excluir
        # Una línea excluida no se recuenta: save() limpia reconteo_requerido (y lo
        # vuelve a evaluar si se reincluye). Antes quedaba marcada y bloqueaba
        # finalizar/enviar sin aparecer en ninguna pantalla.
        detalle.save()
        inventario.calcular_metricas()

        _registrar_log(
            inventario=inventario,
            tipo_accion='MODIFICACION',
            descripcion=f'Detalle {detalle.sku} {"excluido" if excluir else "incluido"} del análisis',
            usuario=request.user,
            datos={'detalle_id': detalle_id, 'excluir': excluir}
        )

        return JsonResponse({
            'success': True,
            'excluido': detalle.excluir_de_analisis,
            'progreso': float(inventario.progreso_conteo)
        })
    except TomaInventarioDetalle.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Detalle no encontrado'})
    except Exception as e:
        logger.error(f"Error al excluir detalle: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_POST
@login_required
def preview_conteo_pistola(request, inventario_id):
    """
    Previsualiza los primeros registros del archivo de conteo.

    Devuelve además los encabezados y las columnas detectadas (o elegidas con
    sku_col/cantidad_col) para que el modal deje elegirlas, y compara las
    cantidades con el stock del sistema de la toma: si TODAS coinciden lo más
    probable es que se esté leyendo la columna «Stock sistema» y no el conteo.
    """
    inventario = _inventario_del_usuario(request, inventario_id)
    if inventario is None:
        return _error_sin_acceso()

    archivo = request.FILES.get('archivo')
    if not archivo:
        return JsonResponse({'success': False, 'error': 'Debe adjuntar un archivo'})

    nombre_hoja = request.POST.get('nombre_hoja', '').strip()
    filas, error = _leer_archivo_conteo(archivo, nombre_hoja, max_rows=101, read_only=True)
    if error:
        return JsonResponse({'success': False, 'error': error})

    sku_idx, cantidad_idx, tiene_encabezado, encabezados, error_cols = _detectar_indices_conteo(
        filas, request.POST.get('sku_col'), request.POST.get('cantidad_col')
    )
    columnas = {
        'encabezados': encabezados,
        'tiene_encabezado': tiene_encabezado,
        'sku_col': sku_idx,
        'cantidad_col': cantidad_idx,
    }
    if error_cols:
        return JsonResponse({'success': False, 'error': error_cols, 'columnas': columnas})

    filas_data = filas[1:] if tiene_encabezado else filas
    preview, errores = _extraer_preview_conteo(filas_data, sku_idx, cantidad_idx, limite=100)

    # Comparación contra el sistema (solo las filas del preview)
    sistema_por_sku = {
        _normalizar_sku(d['sku']): d['stock_sistema_ajustado']
        for d in inventario.detalles.filter(
            sku__in=[p['sku'] for p in preview]
        ).values('sku', 'stock_sistema_ajustado')
    }
    coincidencias = 0
    en_toma = 0
    for p in preview:
        sistema = sistema_por_sku.get(p['sku'])
        p['en_toma'] = sistema is not None
        p['stock_sistema'] = sistema
        if sistema is not None:
            en_toma += 1
            if p['valido'] and p['cantidad'] == sistema:
                coincidencias += 1

    advertencia = None
    if en_toma >= 5 and coincidencias == en_toma:
        advertencia = (
            f'Las {en_toma} cantidades del preview coinciden exactamente con el stock del '
            f'sistema. ¿Seguro que la columna "{encabezados[cantidad_idx] if tiene_encabezado else cantidad_idx}" '
            f'es el conteo físico y no el stock del sistema?'
        )

    return JsonResponse({
        'success': True,
        'preview': preview,
        'errores': errores[:10],
        'total_filas': len(filas_data),
        'columnas': columnas,
        'coincidencias_sistema': coincidencias,
        'en_toma': en_toma,
        'advertencia': advertencia,
        'conteo_tienda_cerrada': inventario.conteo_tienda_cerrada,
        'fecha_corte_local': timezone.localtime(inventario.fecha_corte).strftime('%Y-%m-%dT%H:%M'),
    })


@require_POST
@login_required
@transaction.atomic
def registrar_reconteo(request, inventario_id):
    """
    Registrar reconteo de productos con diferencias significativas.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        if inventario.estado not in ['EN_CONTEO', 'CONTEO_FINALIZADO', 'EN_REVISION']:
            return JsonResponse({
                'success': False, 
                'error': 'El inventario no está en un estado válido para reconteo'
            })
        
        data = json.loads(request.body)
        reconteos = data.get('reconteos', [])

        reconteos_realizados = 0
        errores = []

        for rec in reconteos:
            detalle_id = rec.get('detalle_id')
            stock_reconteo = rec.get('stock_reconteo')
            observaciones = rec.get('observaciones', '')
            
            try:
                detalle = inventario.detalles.get(id=detalle_id, reconteo_requerido=True)

                cantidad = int(stock_reconteo)
                if cantidad < 0:
                    errores.append(f'SKU {detalle.sku}: el reconteo no puede ser negativo ({cantidad})')
                    continue
                detalle.stock_reconteo = cantidad
                detalle.fecha_reconteo = timezone.now()
                detalle.usuario_reconteo = request.user
                
                # Si el reconteo confirma el conteo original, usar ese valor
                # Si es diferente, usar el reconteo
                if detalle.stock_reconteo != detalle.stock_fisico:
                    detalle.stock_fisico = detalle.stock_reconteo
                    base_stock = detalle.stock_sistema_ajustado if detalle.stock_sistema_ajustado is not None else detalle.stock_sistema
                    detalle.diferencia = detalle.stock_fisico - base_stock
                    if observaciones:
                        detalle.observaciones = f"{detalle.observaciones or ''}\nReconteo: {observaciones}".strip()
                
                detalle.reconteo_requerido = False
                detalle.save()
                
                reconteos_realizados += 1

            except TomaInventarioDetalle.DoesNotExist:
                errores.append(f'Detalle {detalle_id} no requiere reconteo o no existe')
            except (TypeError, ValueError):
                errores.append(f'Detalle {detalle_id}: cantidad inválida ({stock_reconteo!r})')

        # Recalcular métricas
        inventario.calcular_metricas()

        # Registrar log
        _registrar_log(
            inventario=inventario,
            tipo_accion='RECONTEO',
            descripcion=f'{reconteos_realizados} productos recontados',
            usuario=request.user,
            datos={'errores': errores}
        )

        return JsonResponse({
            'success': True,
            'message': f'{reconteos_realizados} reconteos registrados',
            'reconteos_realizados': reconteos_realizados,
            'errores': errores if errores else None,
        })
        
    except Exception as e:
        logger.error(f"Error al registrar reconteo: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


# ==============================================================================
# API: ANÁLISIS Y REPORTES
# ==============================================================================

@require_GET
@login_required
def obtener_analisis_inventario(request, inventario_id):
    """
    Obtener análisis completo del inventario antes de aprobar.
    Incluye métricas, tendencias y alertas.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        # Obtener detalles contados (solo los considerados en análisis)
        detalles = inventario.detalles.filter(contado=True, excluir_de_analisis=False)
        
        # === ANÁLISIS DE DIFERENCIAS ===
        diferencias_positivas = detalles.filter(diferencia__gt=0)
        diferencias_negativas = detalles.filter(diferencia__lt=0)
        sin_diferencia = detalles.filter(diferencia=0)
        
        # Top 10 mayores faltantes
        top_faltantes = diferencias_negativas.order_by('diferencia')[:10]
        top_faltantes_data = [
            {
                'sku': d.sku,
                'producto': d.producto_nombre,
                'talla': d.talla_nombre,
                'diferencia': d.diferencia,
                'valor': float(d.valor_diferencia),
                'porcentaje': round(d.porcentaje_diferencia, 2)
            }
            for d in top_faltantes
        ]
        
        # Top 10 mayores sobrantes
        top_sobrantes = diferencias_positivas.order_by('-diferencia')[:10]
        top_sobrantes_data = [
            {
                'sku': d.sku,
                'producto': d.producto_nombre,
                'talla': d.talla_nombre,
                'diferencia': d.diferencia,
                'valor': float(d.valor_diferencia),
                'porcentaje': round(d.porcentaje_diferencia, 2)
            }
            for d in top_sobrantes
        ]
        
        # === ANÁLISIS POR CATEGORÍA ===
        analisis_categorias = detalles.values('categoria_nombre').annotate(
            total_productos=Count('id'),
            productos_con_diferencia=Count('id', filter=~Q(diferencia=0)),
            suma_diferencias=Sum('diferencia'),
            valor_diferencias=Sum(F('diferencia') * F('costo_unitario_sistema'))
        ).order_by('-valor_diferencias')
        
        # === ANÁLISIS POR MARCA ===
        analisis_marcas = detalles.values('marca_nombre').annotate(
            total_productos=Count('id'),
            productos_con_diferencia=Count('id', filter=~Q(diferencia=0)),
            suma_diferencias=Sum('diferencia'),
            valor_diferencias=Sum(F('diferencia') * F('costo_unitario_sistema'))
        ).order_by('-valor_diferencias')
        
        # === PRODUCTOS QUE REQUIEREN RECONTEO (mismo criterio que finalizar/enviar/aprobar) ===
        requieren_reconteo = _reconteos_pendientes(inventario).count()
        
        # === INDICADORES DE PRECISIÓN ===
        total_contados = detalles.count()
        precision_inventario = (sin_diferencia.count() / total_contados * 100) if total_contados > 0 else 0

        # === UNIDADES (panel de comparación físico vs sistema) ===
        detalles_analisis = inventario.detalles.filter(excluir_de_analisis=False)
        unidades = detalles.aggregate(
            fisicas=Coalesce(Sum('stock_fisico'), 0),
            sistema=Coalesce(Sum('stock_sistema_ajustado'), 0),
        )
        total_lineas = detalles_analisis.count()
        pendientes_contar = detalles_analisis.filter(contado=False).count()
        pendientes_con_stock = detalles_analisis.filter(contado=False, stock_sistema__gt=0).count()
        ajustes_aplicados = inventario.ajustes_aplicados().count()
        # «Unid. en sistema» de TODA la toma (el inventario antiguo), no solo de lo
        # contado: con 8.000 SKUs pendientes la tarjeta decía 0 y no se entendía.
        unidades_sistema_total = detalles_analisis.aggregate(
            t=Coalesce(Sum('stock_sistema_ajustado'), 0)
        )['t']
        unidades_pendientes = detalles_analisis.filter(contado=False, stock_sistema__gt=0).aggregate(
            t=Coalesce(Sum('stock_sistema'), 0)
        )['t']
        operativos_pendientes = (
            detalles_analisis.filter(contado=False, id__in=_ids_operativos(detalles_analisis.filter(contado=False)))
            .aggregate(lineas=Count('id'), unidades=Coalesce(Sum('stock_sistema'), 0))
            if pendientes_contar else {'lineas': 0, 'unidades': 0}
        )

        # === SEGMENTOS (para los filtros del detalle) ===
        segmentos_marcas = list(
            detalles_analisis.values('marca_nombre').annotate(n=Count('id')).order_by('-n')[:40]
        )
        segmentos_categorias = list(
            detalles_analisis.values('categoria_nombre').annotate(n=Count('id')).order_by('-n')[:40]
        )

        # === RESUMEN FINANCIERO ===
        resumen_financiero = {
            'valor_inventario_sistema': float(inventario.valor_inventario_sistema),
            'valor_inventario_fisico': float(inventario.valor_inventario_fisico),
            'diferencia_total': float(inventario.valor_inventario_fisico - inventario.valor_inventario_sistema),
            'valor_faltantes': float(inventario.valor_diferencias_negativas),
            'valor_sobrantes': float(inventario.valor_diferencias_positivas),
            'impacto_neto': float(inventario.valor_diferencias_positivas - inventario.valor_diferencias_negativas)
        }
        
        # === ALERTAS ===
        alertas = []
        
        if requieren_reconteo > 0:
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'{requieren_reconteo} productos requieren reconteo por diferencias significativas'
            })
        
        if precision_inventario < 90:
            alertas.append({
                'tipo': 'error',
                'mensaje': f'Precisión del inventario ({precision_inventario:.1f}%) está por debajo del 90% recomendado'
            })
        
        if abs(resumen_financiero['impacto_neto']) > 1000000:  # > 1 millón
            alertas.append({
                'tipo': 'warning',
                'mensaje': f'Impacto financiero significativo: ${abs(resumen_financiero["impacto_neto"]):,.0f}'
            })
        
        if inventario.total_productos_contados < inventario.total_productos_esperados:
            faltantes = inventario.total_productos_esperados - inventario.total_productos_contados
            alertas.append({
                'tipo': 'info',
                'mensaje': f'{faltantes} productos aún no han sido contados'
            })
        
        analisis = {
            'resumen': {
                # OJO: total_contados son LÍNEAS/SKUs contados. Las unidades van aparte.
                'total_esperados': total_lineas,
                'total_contados': total_contados,
                'pendientes_contar': pendientes_contar,
                'pendientes_con_stock': pendientes_con_stock,
                'pendientes_sin_stock': pendientes_contar - pendientes_con_stock,
                'pendientes_unidades': unidades_pendientes or 0,
                'operativos_pendientes': operativos_pendientes['lineas'] or 0,
                'operativos_pendientes_unidades': operativos_pendientes['unidades'] or 0,
                'ajustes_aplicados': ajustes_aplicados,
                'unidades_fisicas': unidades['fisicas'] or 0,
                # solo de lo contado (comparable con unidades_fisicas)
                'unidades_sistema': unidades['sistema'] or 0,
                'unidades_sistema_total': unidades_sistema_total or 0,
                'sobrantes_unidades': inventario.total_diferencias_positivas,
                'faltantes_unidades': inventario.total_diferencias_negativas,
                'progreso': float(inventario.progreso_conteo),
                'excluidos': inventario.detalles.filter(excluir_de_analisis=True).count(),
                'con_diferencia': diferencias_positivas.count() + diferencias_negativas.count(),
                'sin_diferencia': sin_diferencia.count(),
                'sobrantes': diferencias_positivas.count(),
                'faltantes': diferencias_negativas.count(),
                'requieren_reconteo': requieren_reconteo,
                'precision_inventario': round(precision_inventario, 2)
            },
            'resumen_financiero': resumen_financiero,
            'top_faltantes': top_faltantes_data,
            'top_sobrantes': top_sobrantes_data,
            'analisis_categorias': [
                {
                    'categoria': a['categoria_nombre'] or 'Sin categoría',
                    'total_productos': a['total_productos'],
                    'productos_con_diferencia': a['productos_con_diferencia'],
                    'suma_diferencias': a['suma_diferencias'] or 0,
                    'valor_diferencias': float(a['valor_diferencias'] or 0)
                }
                for a in analisis_categorias
            ],
            'analisis_marcas': [
                {
                    'marca': a['marca_nombre'] or 'Sin marca',
                    'total_productos': a['total_productos'],
                    'productos_con_diferencia': a['productos_con_diferencia'],
                    'suma_diferencias': a['suma_diferencias'] or 0,
                    'valor_diferencias': float(a['valor_diferencias'] or 0)
                }
                for a in analisis_marcas
            ],
            'alertas': alertas,
            'segmentos': {
                'marcas': [
                    {'valor': s['marca_nombre'] or 'Sin marca', 'total': s['n']}
                    for s in segmentos_marcas if s['marca_nombre']
                ],
                'categorias': [
                    {'valor': s['categoria_nombre'] or 'Sin categoría', 'total': s['n']}
                    for s in segmentos_categorias if s['categoria_nombre']
                ],
            },
            'estado': inventario.estado,
            'estado_display': inventario.get_estado_display(),
            'tipo_inventario': inventario.tipo_inventario,
            'numero_inventario': inventario.numero_inventario,
            'conteo_tienda_cerrada': inventario.conteo_tienda_cerrada,
            'fecha_corte_local': timezone.localtime(inventario.fecha_corte).strftime('%Y-%m-%dT%H:%M'),
            # Mismo criterio que TomaInventario.puede_aprobar(): nada pendiente de
            # contar ni de recontar (líneas excluidas fuera).
            'puede_aprobar': inventario.puede_aprobar(),
            'puede_enviar_aprobacion': (
                inventario.estado in ('CONTEO_FINALIZADO', 'EN_REVISION') and
                requieren_reconteo == 0
            ),
            'puede_finalizar': (
                inventario.estado == 'EN_CONTEO' or
                (inventario.estado == 'BORRADOR' and total_contados > 0)
            ),
            'puede_rechazar': inventario.estado == 'PENDIENTE_APROBACION',
            # Con ajustes aplicados ya hay stock movido con referencia a esta toma:
            # cancelarla la dejaría «Cancelada» con kardex vigente (N3).
            'puede_cancelar': (
                inventario.estado not in ('COMPLETADO', 'APLICANDO', 'CANCELADO') and
                ajustes_aplicados == 0
            ),
            'puede_aplicar_ajustes': inventario.estado == 'APROBADO',
        }
        
        return JsonResponse({
            'success': True,
            'analisis': analisis
        })
        
    except Exception as e:
        logger.error(f"Error al obtener análisis: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def exportar_inventario(request, inventario_id):
    """
    Exportar inventario a Excel.

    Se genera en modo `write_only`: openpyxl va escribiendo las filas al vuelo
    en vez de mantener todas las celdas en memoria. Las tomas 4 y 5 de
    producción tienen 335.216 líneas cada una; con el Workbook normal esa
    exportación mataba al worker antes de responder.
    """
    try:
        import openpyxl
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()

        # Crear workbook en modo streaming (sin hoja activa por defecto)
        wb = openpyxl.Workbook(write_only=True)

        # === HOJA DE RESUMEN ===
        ws_resumen = wb.create_sheet("Resumen")

        # Estilos
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4A90D9", end_color="4A90D9", fill_type="solid")
        
        # Información del inventario
        ws_resumen.append(['TOMA DE INVENTARIO'])
        ws_resumen.append(['Número:', inventario.numero_inventario])
        ws_resumen.append(['Nombre:', inventario.nombre])
        ws_resumen.append(['Sucursal:', inventario.sucursal.alias])
        ws_resumen.append(['Fecha Corte:', timezone.localtime(inventario.fecha_corte).strftime('%d/%m/%Y %H:%M')])
        ws_resumen.append(['Estado:', inventario.get_estado_display()])
        ws_resumen.append([])
        ws_resumen.append(['MÉTRICAS'])
        ws_resumen.append(['Total Productos:', inventario.total_productos_esperados])
        ws_resumen.append(['Productos Contados:', inventario.total_productos_contados])
        ws_resumen.append(['Progreso:', f'{inventario.progreso_conteo}%'])
        ws_resumen.append(['Diferencias Positivas:', inventario.total_diferencias_positivas])
        ws_resumen.append(['Diferencias Negativas:', inventario.total_diferencias_negativas])
        ws_resumen.append(['Valor Sistema:', f'${inventario.valor_inventario_sistema:,.0f}'])
        ws_resumen.append(['Valor Físico:', f'${inventario.valor_inventario_fisico:,.0f}'])
        
        # === HOJA DE DETALLE ===
        ws_detalle = wb.create_sheet("Detalle")
        
        headers = [
            'SKU', 'Producto', 'Talla', 'Marca', 'Categoría',
            'Stock Sistema', 'Mov. Post Corte', 'Stock Ajustado', 'Stock Físico', 'Diferencia', '% Diferencia',
            'Costo Unit.', 'Valor Diferencia', 'Contado', 'Reconteo Req.',
            'Ubicación', 'Observaciones', 'Excluido'
        ]
        
        # Anchos de columna: en modo write_only hay que fijarlos ANTES de
        # escribir filas, porque después la hoja ya no es direccionable.
        for col_num in range(1, len(headers) + 1):
            ws_detalle.column_dimensions[get_column_letter(col_num)].width = 15

        # Encabezados con estilo: en write_only no se puede volver a la celda
        # con ws.cell(row=1, ...), así que el estilo va en la propia celda.
        celdas_encabezado = []
        for header in headers:
            celda = WriteOnlyCell(ws_detalle, value=header)
            celda.font = header_font
            celda.fill = header_fill
            celda.alignment = Alignment(horizontal='center')
            celdas_encabezado.append(celda)
        ws_detalle.append(celdas_encabezado)

        # Agregar datos. `.iterator(chunk_size=2000)` evita materializar los
        # 335.216 detalles en una lista de Python.
        detalles_qs = (
            inventario.detalles.all()
            .order_by('producto_nombre', 'talla_nombre', 'id')
            .iterator(chunk_size=2000)
        )
        for det in detalles_qs:
            ws_detalle.append([
                det.sku,
                det.producto_nombre,
                det.talla_nombre or '',
                det.marca_nombre or '',
                det.categoria_nombre or '',
                det.stock_sistema,
                det.stock_movimientos_post_corte,
                det.stock_sistema_ajustado,
                det.stock_fisico,
                det.diferencia,
                round(det.porcentaje_diferencia, 2),
                float(det.costo_unitario_sistema),
                float(det.valor_diferencia),
                'Sí' if det.contado else 'No',
                'Sí' if det.reconteo_requerido else 'No',
                det.ubicacion or '',
                det.observaciones or '',
                'Sí' if det.excluir_de_analisis else 'No'
            ])

        # (los anchos de columna ya se fijaron antes de escribir las filas:
        #  en modo write_only no se pueden tocar después)

        # Preparar respuesta
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="inventario_{inventario.numero_inventario}.xlsx"'

        wb.save(response)
        return response

    except Exception as e:
        logger.error(f"Error al exportar inventario: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def exportar_diferencias_inventario(request, inventario_id):
    """
    Exporta solo productos con diferencias (excluidos fuera).
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        detalles = inventario.detalles.filter(
            contado=True,
            excluir_de_analisis=False
        ).exclude(diferencia=0).order_by('producto_nombre', 'talla_nombre')

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Diferencias"

        headers = [
            'SKU', 'Producto', 'Talla', 'Marca', 'Categoría',
            'Stock Sistema', 'Mov. Post Corte', 'Stock Ajustado', 'Stock Físico', 'Diferencia', '% Diferencia',
            'Costo Unit.', 'Valor Diferencia'
        ]
        ws.append(headers)

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4A90D9", end_color="4A90D9", fill_type="solid")
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center')

        for det in detalles:
            ws.append([
                det.sku,
                det.producto_nombre,
                det.talla_nombre or '',
                det.marca_nombre or '',
                det.categoria_nombre or '',
                det.stock_sistema,
                det.stock_movimientos_post_corte,
                det.stock_sistema_ajustado,
                det.stock_fisico,
                det.diferencia,
                round(det.porcentaje_diferencia, 2),
                float(det.costo_unitario_sistema),
                float(det.valor_diferencia),
            ])

        for col_num in range(1, len(headers) + 1):
            ws.column_dimensions[get_column_letter(col_num)].width = 16

        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = f'attachment; filename="inventario_{inventario.numero_inventario}_diferencias.xlsx"'

        wb.save(response)
        return response

    except Exception as e:
        logger.error(f"Error al exportar diferencias: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def obtener_informe_marcas(request, inventario_id):
    """
    Resultado por marca para la pantalla: inventario antiguo (sistema) vs nuevo
    (pistola) con Stock, P COSTO y P VENTA, como el informe «Inventario General».
    Mismo cálculo que el Excel (services/informe_toma_inventario.py).
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        analisis = informe_toma.analizar(informe_toma.filas_desde_toma(inventario))
        return JsonResponse({
            'success': True,
            'titulo': informe_toma.titulo_informe(informe_toma.cabecera_desde_toma(inventario)),
            'marcas': analisis['marcas'],
            'total': analisis['total'],
            'resumen': analisis['resumen'],
            'no_cargados': informe_toma.no_cargados_desde_logs(inventario),
        })
    except Exception as e:
        logger.error(f"Error al obtener informe por marca: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def exportar_informe_final(request, inventario_id):
    """
    Informe final en Excel: «Por marca» (inventario antiguo | DIF | nuevo),
    «Diferencias» por SKU (id, sku, art, marca, costo, costo2, stk, pistola, mov,
    final, pvp, ttcosto1, ttpvp1, ttcosto2, ttpvp2), «No cargados», «Excluidos»
    y «Resumen». Sirve en cualquier estado: antes de aprobar es la vista previa
    de lo que se ajustará.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        cabecera = informe_toma.cabecera_desde_toma(inventario)
        analisis = informe_toma.analizar(informe_toma.filas_desde_toma(inventario))
        wb = informe_toma.construir_workbook(
            cabecera, analisis, informe_toma.no_cargados_desde_logs(inventario)
        )
        response = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = (
            f'attachment; filename="informe_{inventario.numero_inventario}_{inventario.sucursal.alias}.xlsx"'
        )
        wb.save(response)
        return response
    except Exception as e:
        logger.error(f"Error al exportar informe final: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


# ==============================================================================
# API: FLUJO DE APROBACIÓN
# ==============================================================================

@require_POST
@login_required
@transaction.atomic
def finalizar_conteo(request, inventario_id):
    """
    Finalizar el conteo y pasar a revisión.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        # Una toma BORRADOR con conteos (importados por una versión anterior, que no
        # cambiaba el estado) mostraba «Finalizar» y el backend la rechazaba: es la
        # trampa en que cayó INV-6 (4.301 contados). Si tiene conteos, se acepta.
        if inventario.estado == 'BORRADOR' and inventario.detalles.filter(contado=True).exists():
            inventario.estado = 'EN_CONTEO'
            inventario.fecha_inicio_conteo = inventario.fecha_inicio_conteo or timezone.now()
            inventario.save(update_fields=['estado', 'fecha_inicio_conteo', 'updated_at'])

        if inventario.estado not in ['EN_CONTEO']:
            return JsonResponse({
                'success': False,
                'error': 'El inventario no está en estado de conteo'
            })

        # Verificar que se hayan contado todas las LÍNEAS (no unidades: el campo
        # total_productos_contados del modelo acumula stock_fisico, así que una tienda
        # con más unidades que SKUs podía cerrar un conteo a medias).
        detalles_analisis = inventario.detalles.filter(excluir_de_analisis=False)
        pendientes = detalles_analisis.filter(contado=False).count()
        if pendientes > 0:
            # Desglose para que la pantalla ofrezca «Resolver no contados» con criterio
            con_stock = detalles_analisis.filter(contado=False, stock_sistema__gt=0).aggregate(
                lineas=Count('id'), unidades=Coalesce(Sum('stock_sistema'), 0)
            )
            return JsonResponse({
                'success': False,
                'error': f'Faltan {pendientes} productos por contar',
                'pendientes': pendientes,
                'pendientes_con_stock': con_stock['lineas'],
                'pendientes_sin_stock': pendientes - con_stock['lineas'],
                'unidades_sin_contar': con_stock['unidades'],
            })

        # Verificar si hay reconteos pendientes (sin las líneas excluidas)
        reconteos_pendientes = _reconteos_pendientes(inventario).count()

        if reconteos_pendientes > 0:
            inventario.estado = 'EN_REVISION'
            mensaje = f'Inventario en revisión. {reconteos_pendientes} productos requieren reconteo.'
        else:
            inventario.estado = 'CONTEO_FINALIZADO'
            mensaje = 'Conteo finalizado exitosamente.'
        
        inventario.fecha_fin_conteo = timezone.now()
        inventario.save()
        
        _registrar_log(
            inventario=inventario,
            tipo_accion='CAMBIO_ESTADO',
            descripcion=f'Estado cambiado a {inventario.get_estado_display()}',
            usuario=request.user
        )
        
        return JsonResponse({
            'success': True,
            'message': mensaje,
            'estado': inventario.estado,
            'reconteos_pendientes': reconteos_pendientes
        })
        
    except Exception as e:
        logger.error(f"Error al finalizar conteo: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_POST
@login_required
@transaction.atomic
def enviar_aprobacion(request, inventario_id):
    """
    Enviar inventario para aprobación.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        if inventario.estado not in ['CONTEO_FINALIZADO', 'EN_REVISION']:
            return JsonResponse({
                'success': False, 
                'error': 'El inventario no está en un estado válido para enviar a aprobación'
            })
        
        # Verificar que no haya reconteos pendientes (sin las líneas excluidas)
        reconteos_pendientes = _reconteos_pendientes(inventario).count()

        if reconteos_pendientes > 0:
            return JsonResponse({
                'success': False,
                'error': f'Hay {reconteos_pendientes} productos pendientes de reconteo'
            })
        
        inventario.estado = 'PENDIENTE_APROBACION'
        inventario.save()
        
        _registrar_log(
            inventario=inventario,
            tipo_accion='ENVIO_APROBACION',
            descripcion='Inventario enviado para aprobación',
            usuario=request.user
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Inventario enviado para aprobación'
        })
        
    except Exception as e:
        logger.error(f"Error al enviar a aprobación: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_POST
@login_required
@transaction.atomic
def aprobar_inventario(request, inventario_id):
    """
    Aprobar inventario. Solo actualiza el estado, no aplica ajustes.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        if inventario.estado != 'PENDIENTE_APROBACION':
            return JsonResponse({
                'success': False, 
                'error': 'El inventario no está pendiente de aprobación'
            })
        
        # No se aprueba un conteo incompleto: si quedan líneas sin contar el ajuste
        # posterior no toca ese stock y el inventario se cierra en falso.
        pendientes = inventario.detalles.filter(excluir_de_analisis=False, contado=False).count()
        if pendientes > 0:
            return JsonResponse({
                'success': False,
                'error': f'No se puede aprobar: quedan {pendientes} productos sin contar. '
                         f'Cuéntelos o exclúyalos del análisis.'
            })

        reconteos_pendientes = _reconteos_pendientes(inventario).count()
        if reconteos_pendientes > 0:
            return JsonResponse({
                'success': False,
                'error': f'No se puede aprobar: {reconteos_pendientes} productos esperan reconteo'
            })

        data = json.loads(request.body) if request.body else {}
        observaciones = data.get('observaciones', '')

        inventario.estado = 'APROBADO'
        inventario.aprobado_por = request.user
        inventario.fecha_aprobacion = timezone.now()
        if observaciones:
            inventario.observaciones = f"{inventario.observaciones or ''}\nAprobación: {observaciones}".strip()
        inventario.save()
        
        _registrar_log(
            inventario=inventario,
            tipo_accion='APROBACION',
            descripcion='Inventario aprobado',
            usuario=request.user,
            datos={'observaciones': observaciones}
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Inventario aprobado. Puede proceder a aplicar los ajustes.'
        })
        
    except Exception as e:
        logger.error(f"Error al aprobar inventario: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_POST
@login_required
@transaction.atomic
def rechazar_inventario(request, inventario_id):
    """
    Rechazar inventario y devolverlo a conteo.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        if inventario.estado != 'PENDIENTE_APROBACION':
            return JsonResponse({
                'success': False, 
                'error': 'El inventario no está pendiente de aprobación'
            })
        
        data = json.loads(request.body)
        motivo = data.get('motivo', '')
        
        if not motivo:
            return JsonResponse({
                'success': False,
                'error': 'Debe indicar el motivo del rechazo'
            })
        
        inventario.estado = 'EN_CONTEO'
        inventario.observaciones = f"{inventario.observaciones or ''}\nRechazo: {motivo}".strip()
        inventario.save()
        
        _registrar_log(
            inventario=inventario,
            tipo_accion='RECHAZO',
            descripcion=f'Inventario rechazado: {motivo}',
            usuario=request.user
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Inventario rechazado y devuelto a conteo'
        })
        
    except Exception as e:
        logger.error(f"Error al rechazar inventario: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


# ==============================================================================
# API: APLICACIÓN DE AJUSTES (BACKGROUND THREAD + PROGRESS TRACKING)
# ==============================================================================

def _ultimo_ajuste_aplicado_en(inventario):
    """Fecha del último detalle ajustado (heartbeat implícito del worker)."""
    return inventario.detalles.filter(ajuste_aplicado=True).aggregate(
        ultimo=Max('fecha_ajuste')
    )['ultimo']


def _tarea_huerfana(tarea, inventario, ahora=None):
    """
    ¿La tarea EN_PROCESO se quedó sin worker? No hay heartbeat en el modelo (sería
    una migración), así que se usa lo que ya existe: `iniciada_en` y la fecha del
    último ajuste aplicado. Huérfana = EN_PROCESO hace más de TAREA_HUERFANA_MINUTOS
    y sin ningún detalle aplicado en ese mismo lapso. Un worker vivo pero lento
    aplica al menos un detalle cada pocos segundos, así que 30 min sin avance es
    un hilo muerto (deploy, OOM, reinicio del contenedor).
    """
    if tarea is None or tarea.estado != 'EN_PROCESO':
        return False
    ahora = ahora or timezone.now()
    limite = ahora - timedelta(minutes=TAREA_HUERFANA_MINUTOS)
    if tarea.iniciada_en and tarea.iniciada_en > limite:
        return False
    ultimo = _ultimo_ajuste_aplicado_en(inventario)
    if ultimo and ultimo > limite:
        return False
    return True


def _iniciar_tarea_ajustes(inventario, usuario, reanudar=False):
    """
    Toma el «lock» de la aplicación de ajustes de una toma (N2/H6).

    Devuelve (tarea, iniciada). Si `iniciada` es False la tarea ya estaba EN_PROCESO
    (y no huérfana, o no se pidió reanudar): el llamador NO debe lanzar un worker.

    Con gunicorn 2 workers × 2 threads, dos clics que llegaran antes de que el
    primero grabara EN_PROCESO lanzaban dos hilos con la misma lista de detalles y
    cada uno registraba kardex + lote + stock otra vez. Ahora la fila de la tarea
    se bloquea con select_for_update y el cambio a EN_PROCESO es un UPDATE
    condicional: solo un llamador ve rows == 1. La guarda de segundo nivel está en
    _aplicar_ajuste_individual (relee el detalle bajo lock y sale si ya se aplicó).
    """
    with transaction.atomic():
        tarea = (
            TareaAplicacionAjustes.objects.select_for_update()
            .filter(inventario=inventario).first()
        )
        if tarea is None:
            tarea = TareaAplicacionAjustes.objects.create(inventario=inventario, creada_por=usuario)
            tarea = TareaAplicacionAjustes.objects.select_for_update().get(pk=tarea.pk)

        ahora = timezone.now()
        if tarea.estado == 'EN_PROCESO':
            if not (reanudar and _tarea_huerfana(tarea, inventario, ahora)):
                return tarea, False
            # Reanudar una tarea huérfana: UPDATE condicional sobre iniciada_en para
            # que dos «Reanudar» simultáneos no ganen los dos.
            filas = TareaAplicacionAjustes.objects.filter(
                pk=tarea.pk, estado='EN_PROCESO', iniciada_en=tarea.iniciada_en,
            ).update(iniciada_en=ahora, finalizada_en=None, creada_por=usuario)
        else:
            filas = TareaAplicacionAjustes.objects.filter(
                pk=tarea.pk,
            ).exclude(estado='EN_PROCESO').update(
                estado='EN_PROCESO', procesados=0, total=0, errores=[],
                iniciada_en=ahora, finalizada_en=None, creada_por=usuario,
            )
        if filas != 1:
            return tarea, False

        TomaInventario.objects.filter(pk=inventario.pk).update(estado='APLICANDO')
        inventario.estado = 'APLICANDO'
        tarea.refresh_from_db()
        return tarea, True


def _ejecutar_ajustes_background(inventario_id, usuario_id, cerrar_conexion=True):
    """
    Worker que aplica los ajustes de una toma (en un thread desde la vista, o de
    forma síncrona desde el command `aplicar_ajustes_toma`).
    Actualiza TareaAplicacionAjustes cada PROGRESS_UPDATE_INTERVAL SKUs para
    que el frontend pueda hacer polling del progreso.
    `cerrar_conexion`: el thread cierra su conexión al terminar (evita leaks); el
    command y los tests, que comparten la conexión del llamador, pasan False.
    """
    PROGRESS_UPDATE_INTERVAL = 25

    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        usuario = User.objects.get(pk=usuario_id)
        inventario = TomaInventario.objects.get(pk=inventario_id)
        tarea = TareaAplicacionAjustes.objects.get(inventario=inventario)

        detalles_pendientes = list(
            inventario.detalles.filter(
                contado=True,
                ajuste_aplicado=False,
                excluir_de_analisis=False  # lo excluido del análisis NO debe ajustar stock
            ).exclude(diferencia=0)
        )

        tarea.total = len(detalles_pendientes)
        tarea.procesados = 0
        tarea.save(update_fields=['total', 'procesados'])

        if not detalles_pendientes:
            inventario.estado = 'COMPLETADO'
            inventario.save()
            tarea.estado = 'COMPLETADO'
            tarea.finalizada_en = timezone.now()
            tarea.save(update_fields=['estado', 'finalizada_en'])
            return

        ajustes_aplicados = 0
        omitidos = 0  # ya aplicados por otro worker (guarda N2)
        errores = []

        for i, detalle in enumerate(detalles_pendientes):
            try:
                if _aplicar_ajuste_individual(detalle, inventario, usuario):
                    ajustes_aplicados += 1
                else:
                    omitidos += 1
            except Exception as e:
                errores.append({'sku': detalle.sku, 'error': str(e)})
                logger.error(f"Error al aplicar ajuste para {detalle.sku}: {str(e)}")

            # Actualizar progreso periódicamente
            if (i + 1) % PROGRESS_UPDATE_INTERVAL == 0:
                tarea.procesados = i + 1
                tarea.save(update_fields=['procesados'])

        # El inventario solo se cierra si TODOS los ajustes entraron. Si alguno falló,
        # queda en APROBADO para reintentar (el filtro ajuste_aplicado=False hace que
        # el reintento sea idempotente) en vez de darse por completado a medias.
        inventario.estado = 'COMPLETADO' if not errores else 'APROBADO'
        inventario.save()

        # Registrar log
        _registrar_log(
            inventario=inventario,
            tipo_accion='APLICACION_AJUSTES',
            descripcion=(
                f'{ajustes_aplicados} ajustes aplicados de {len(detalles_pendientes)} esperados'
                + (f', {omitidos} ya estaban aplicados' if omitidos else '')
                + (f', {len(errores)} con error (inventario queda en Aprobado para reintentar)'
                   if errores else '')
            ),
            usuario=usuario,
            datos={
                'ajustes_aplicados': ajustes_aplicados, 'esperados': len(detalles_pendientes),
                'omitidos': omitidos, 'errores': errores,
            }
        )

        tarea.procesados = ajustes_aplicados + omitidos
        tarea.errores = errores
        tarea.estado = 'COMPLETADO' if not errores else 'ERROR'
        tarea.finalizada_en = timezone.now()
        tarea.save(update_fields=['procesados', 'errores', 'estado', 'finalizada_en'])

    except Exception as e:
        logger.error(f"Error crítico en background task de ajustes: {str(e)}")
        try:
            tarea = TareaAplicacionAjustes.objects.get(inventario_id=inventario_id)
            tarea.estado = 'ERROR'
            tarea.errores = [{'error': str(e)}]
            tarea.finalizada_en = timezone.now()
            tarea.save(update_fields=['estado', 'errores', 'finalizada_en'])
            inventario = TomaInventario.objects.get(pk=inventario_id)
            if inventario.estado == 'APLICANDO':
                inventario.estado = 'APROBADO'
                inventario.save()
        except Exception:
            pass
    finally:
        if cerrar_conexion:
            connection.close()


@require_POST
@login_required
def aplicar_ajustes_inventario(request, inventario_id):
    """
    Inicia la aplicación de ajustes de inventario en un thread background.
    Retorna inmediatamente con el task_id para que el frontend haga polling
    al endpoint estado_tarea_ajustes.

    Body opcional {"reanudar": true}: relanza una tarea EN_PROCESO solo si está
    huérfana (ver _tarea_huerfana); los detalles ya aplicados se saltan.

    IMPORTANTE: Esta función modifica el stock real del sistema.
    Solo debe ejecutarse después de la aprobación.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()

        if inventario.estado not in ('APROBADO', 'APLICANDO'):
            return JsonResponse({
                'success': False,
                'error': 'El inventario debe estar aprobado para aplicar ajustes'
            })

        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            data = {}
        reanudar = bool(data.get('reanudar'))

        tarea, iniciada = _iniciar_tarea_ajustes(inventario, request.user, reanudar=reanudar)
        if not iniciada:
            return JsonResponse({
                'success': True,
                'task_id': tarea.id,
                'already_running': True,
                'huerfana': _tarea_huerfana(tarea, inventario),
                'message': 'El proceso ya está en ejecución'
            })

        # El hilo arranca SOLO después del commit: si se lanzara antes podría leer
        # la tarea/toma sin el estado nuevo (otra conexión) o correr sobre datos que
        # luego se revierten.
        usuario_id = request.user.id

        def _lanzar():
            threading.Thread(
                target=_ejecutar_ajustes_background,
                args=(inventario_id, usuario_id),
                daemon=True
            ).start()

        transaction.on_commit(_lanzar)

        return JsonResponse({
            'success': True,
            'task_id': tarea.id,
            'reanudada': reanudar,
            'message': 'Proceso de ajustes iniciado. Usa el endpoint de estado para monitorear el progreso.'
        })

    except Exception as e:
        logger.error(f"Error al iniciar aplicación de ajustes: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_GET
@login_required
def estado_tarea_ajustes(request, inventario_id):
    """
    Endpoint de polling para monitorear el progreso de aplicación de ajustes.
    El frontend consulta este endpoint cada ~2s para actualizar la barra de progreso.
    """
    try:
        # Antes no verificaba pertenencia: cualquier usuario del módulo podía leer el
        # progreso y los SKUs con error de la toma de otra empresa (H13).
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()

        tarea = TareaAplicacionAjustes.objects.filter(inventario_id=inventario.id).first()
        if not tarea:
            return JsonResponse({
                'success': False,
                'error': 'No se encontró tarea para este inventario'
            })

        ultimo_ajuste = _ultimo_ajuste_aplicado_en(inventario)
        return JsonResponse({
            'success': True,
            'estado': tarea.estado,
            'total': tarea.total,
            'procesados': tarea.procesados,
            'porcentaje': tarea.porcentaje,
            'errores': tarea.errores,
            'iniciada_en': tarea.iniciada_en.isoformat() if tarea.iniciada_en else None,
            'finalizada_en': tarea.finalizada_en.isoformat() if tarea.finalizada_en else None,
            'ultimo_ajuste_en': ultimo_ajuste.isoformat() if ultimo_ajuste else None,
            'huerfana': _tarea_huerfana(tarea, inventario),
            'estado_inventario': inventario.estado,
        })
    except Exception as e:
        logger.error(f"Error al obtener estado de tarea: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})

def _aplicar_ajuste_individual(detalle, inventario, usuario):
    """
    Aplica el ajuste de un producto individual: kardex + stock plano + lotes FIFO.

    SOBRANTE (diferencia > 0): lote FIFO nuevo al costo del corte + movimiento
    AJUSTE_INVENTARIO_ENTRADA.
    FALTANTE (diferencia < 0): movimiento AJUSTE_INVENTARIO_SALIDA; registrar_movimiento_producto
    descuenta el stock plano y consume los lotes FIFO best-effort.

    Cambios respecto de la versión anterior (todos por bugs reales):
    - Antes el faltante iba por consumir_stock_fifo(), que LEVANTA ValidationError si no
      hay lotes suficientes. La excepción se tragaba con un logger.warning y el detalle
      igual quedaba `ajuste_aplicado=True`: el stock NO se corregía y la pantalla decía
      "ajustes aplicados exitosamente". En la toma INV-6-20260115-001, 19 de 61 faltantes
      (31%) caen en ese caso (16 sin lotes, 3 con lotes insuficientes).
    - Además ese camino grababa concepto AJUSTE_NEGATIVO y sin referencia_externa, con lo
      que el movimiento no se podía rastrear hasta la toma que lo originó.
    - Se toma lock de fila sobre el Producto_Talla: el stock se actualiza con un
      read-modify-write y el proceso corre en un thread en paralelo con las ventas del POS.
    - Guarda contra doble aplicación (N2): el detalle se RELEE bajo select_for_update
      y, si otro worker ya lo aplicó (dos POST casi simultáneos, «reanudar» con el
      hilo original todavía vivo), se sale sin tocar nada. La idempotencia ya no
      depende de que un solo hilo haya leído `ajuste_aplicado=False` en memoria.

    Devuelve True si aplicó el ajuste, False si no había nada que hacer.
    """
    from .views import registrar_movimiento_producto
    from .views_modulo_productos import crear_lote_producto

    if detalle.diferencia == 0:
        return False

    referencia = inventario.numero_inventario
    observaciones = f'Ajuste inventario {referencia}'

    with transaction.atomic():
        det = TomaInventarioDetalle.objects.select_for_update().get(pk=detalle.pk)
        if det.ajuste_aplicado:
            detalle.ajuste_aplicado = True
            detalle.fecha_ajuste = det.fecha_ajuste
            return False
        diferencia = det.diferencia
        if diferencia == 0 or det.excluir_de_analisis:
            return False

        producto_talla = (
            Producto_Talla.objects.select_for_update()
            .select_related('producto')
            .get(pk=det.producto_talla_id)
        )

        if diferencia > 0:
            # El lote se crea primero: si falla, no se toca el stock y el detalle
            # queda pendiente para reintentar.
            lote = crear_lote_producto(
                producto_talla=producto_talla,
                cantidad=diferencia,
                costo_unitario=detalle.costo_unitario_sistema,
                sobreprecio_unitario=0,
                precio_venta_unitario=detalle.precio_venta_sistema,
                observaciones=f'{observaciones} - Sobrante'
            )
            movimiento = registrar_movimiento_producto(
                producto_talla=producto_talla,
                concepto='AJUSTE_INVENTARIO_ENTRADA',
                cantidad=diferencia,
                responsable=usuario,
                sucursal_destino=inventario.sucursal,
                observaciones=f'{observaciones} - Sobrante',
                referencia_externa=referencia,
                crear_lote_fifo=False,  # el lote ya se creó arriba
            )
            if lote is not None and movimiento is not None:
                lote.movimiento = movimiento
                lote.save(update_fields=['movimiento'])
        else:
            # Un ajuste no puede dejar el stock en negativo: si eso pasa la diferencia
            # se calculó contra una base que ya no existe (o hubo ventas después del
            # conteo). Se rechaza y queda listado como error para revisión manual en
            # vez de dejar stock negativo circulando por el POS.
            stock_resultante = (producto_talla.stock or 0) + diferencia
            if stock_resultante < 0:
                raise ValidationError(
                    f'El ajuste dejaría el stock en {stock_resultante} '
                    f'(actual {producto_talla.stock}, diferencia {diferencia}). '
                    f'Recontar el SKU antes de aplicar.'
                )

            # Salida: actualiza stock plano SIEMPRE y consume lotes best-effort.
            registrar_movimiento_producto(
                producto_talla=producto_talla,
                concepto='AJUSTE_INVENTARIO_SALIDA',
                cantidad=-abs(diferencia),
                responsable=usuario,
                sucursal_origen=inventario.sucursal,
                observaciones=f'{observaciones} - Faltante',
                referencia_externa=referencia,
                consumir_lotes=True,
            )

        # Marcar como aplicado solo si el ajuste efectivamente se registró (sobre la
        # fila bloqueada; el objeto del llamador se actualiza para que no la reintente)
        ahora = timezone.now()
        TomaInventarioDetalle.objects.filter(pk=det.pk).update(ajuste_aplicado=True, fecha_ajuste=ahora)
        detalle.ajuste_aplicado = True
        detalle.fecha_ajuste = ahora
        return True


# ==============================================================================
# API: CANCELACIÓN
# ==============================================================================

@require_POST
@login_required
@transaction.atomic
def cancelar_inventario(request, inventario_id):
    """
    Cancelar un inventario.
    Solo se puede cancelar si no se han aplicado ajustes.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        if inventario.estado == 'COMPLETADO':
            return JsonResponse({
                'success': False, 
                'error': 'No se puede cancelar un inventario completado'
            })
        
        if inventario.estado == 'APLICANDO':
            return JsonResponse({
                'success': False,
                'error': 'No se puede cancelar mientras se aplican ajustes'
            })

        # Tras una aplicación con errores la toma vuelve a APROBADO con parte de los
        # ajustes ya en el kardex/stock. Cancelarla dejaría movimientos vigentes
        # referenciando una toma «Cancelada», sin reversa (N3).
        aplicados = inventario.ajustes_aplicados().count()
        if aplicados > 0:
            return JsonResponse({
                'success': False,
                'error': f'La toma ya aplicó {aplicados} ajuste(s) al stock y no se puede cancelar. '
                         f'Reintente la aplicación para completar los pendientes.'
            })

        data = json.loads(request.body)
        motivo = data.get('motivo', '')
        
        if not motivo:
            return JsonResponse({
                'success': False,
                'error': 'Debe indicar el motivo de cancelación'
            })
        
        inventario.estado = 'CANCELADO'
        inventario.motivo_cancelacion = motivo
        inventario.save()
        
        _registrar_log(
            inventario=inventario,
            tipo_accion='CANCELACION',
            descripcion=f'Inventario cancelado: {motivo}',
            usuario=request.user
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Inventario cancelado'
        })
        
    except Exception as e:
        logger.error(f"Error al cancelar inventario: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


# ==============================================================================
# API: NO CONTADOS EN BLOQUE (cerrar tomas parciales)
# ==============================================================================

# SKUs que se listan por marca en el modal de no contados (el resto se decide por marca)
LIMITE_SKUS_POR_MARCA = 100


def _pendientes_no_contados(inventario, solo_stock_cero=False, marcas=None, detalle_ids=None):
    """Líneas sin contar y no excluidas, opcionalmente acotadas a marcas o a ids.
    La marca '' (o 'SIN MARCA') toma las líneas sin marca."""
    pendientes = inventario.detalles.filter(excluir_de_analisis=False, contado=False)
    if solo_stock_cero:
        pendientes = pendientes.filter(stock_sistema__lte=0)
    if detalle_ids is not None:
        pendientes = pendientes.filter(id__in=[int(i) for i in detalle_ids])
    if marcas is not None:
        nombres = [str(m).strip() for m in marcas]
        q = Q(marca_nombre__in=[m for m in nombres if m])
        if any(not m or m == informe_toma.SIN_MARCA for m in nombres):
            q |= Q(marca_nombre__isnull=True) | Q(marca_nombre='')
        pendientes = pendientes.filter(q)
    return pendientes


def _agrupar_no_contados(inventario):
    """
    Lo que no apareció en el conteo, agrupado por marca para decidir en la
    pantalla (mantener / faltante / excluir) y la lista de SKUs operativos, que
    siempre se excluyen. Cada marca trae sus primeros LIMITE_SKUS_POR_MARCA SKUs
    para poder decidir de a uno.
    """
    pendientes = inventario.detalles.filter(excluir_de_analisis=False, contado=False)
    ids_operativos = set(_ids_operativos(pendientes))
    grupos, operativos = {}, []
    filas = pendientes.values(
        'id', 'sku', 'producto_nombre', 'talla_nombre', 'marca_nombre', 'stock_sistema', 'costo_unitario_sistema'
    ).order_by('marca_nombre', 'producto_nombre', 'talla_nombre')
    for d in filas.iterator(chunk_size=2000):
        if d['id'] in ids_operativos:
            operativos.append({
                'id': d['id'], 'sku': d['sku'], 'articulo': d['producto_nombre'], 'stock': d['stock_sistema'],
            })
            continue
        marca = (d['marca_nombre'] or '').strip()
        g = grupos.get(marca)
        if g is None:
            g = grupos[marca] = {
                'marca': marca, 'etiqueta': marca or informe_toma.SIN_MARCA,
                'lineas': 0, 'unidades': 0, 'valor_costo': 0.0, 'skus': [],
            }
        g['lineas'] += 1
        stock = d['stock_sistema'] or 0
        if stock > 0:
            g['unidades'] += stock
            g['valor_costo'] += float(stock * (d['costo_unitario_sistema'] or 0))
        if len(g['skus']) < LIMITE_SKUS_POR_MARCA:
            g['skus'].append({
                'id': d['id'], 'sku': d['sku'], 'articulo': d['producto_nombre'],
                'talla': d['talla_nombre'] or '', 'stock': stock,
                'costo': float(d['costo_unitario_sistema'] or 0),
            })
    for g in grupos.values():
        g['skus_completos'] = len(g['skus']) == g['lineas']
    return (
        sorted(grupos.values(), key=lambda g: (-g['unidades'], g['etiqueta'])),
        sorted(operativos, key=lambda o: -(o['stock'] or 0)),
    )


def _resolver_no_contados(inventario, usuario, accion, solo_stock_cero=False, previsualizar=False,
                          marcas=None, detalle_ids=None):
    """
    Resuelve en bloque las líneas sin contar (no excluidas) para poder cerrar una
    toma parcial. Antes solo se podían excluir de a una (INV-6: 33.070 requests).

    accion:
      'excluir'        → excluir_de_analisis=True (no se ajustan, no bloquean).
      'sin_diferencia' → «MANTENER EL STOCK DEL SISTEMA»: contado=True con
                         stock_fisico = stock del sistema, diferencia 0, no mueven
                         stock. Para lo que no se pistolea a propósito (o una toma
                         parcial): el inventario nuevo toma la cantidad del antiguo.
      'faltante'       → contado=True con stock_fisico = 0: lo que no apareció en
                         la pistola falta y al aplicar se descuenta (igual que el
                         informe antiguo: pistola vacía = 0). Las líneas con
                         diferencia grande quedan para reconteo («búsquelo antes de
                         darlo por perdido»).
    En 'sin_diferencia' y 'faltante' los SKUs operativos (VISA, bolsas, envíos) se
    EXCLUYEN: su stock queda igual y no inflan el informe con miles de unidades
    que no son mercadería.
    marcas / detalle_ids: acotan a esas marcas / líneas (decisión por marca o por
      SKU desde la pantalla; ver «plan» en resolver_no_contados).
    solo_stock_cero: limitar a las líneas con stock_sistema <= 0 (las que más
      abundan en una toma creada sin filtro de stock; un negativo tampoco es
      «algo que contar»).
    previsualizar: solo devuelve el impacto (líneas, unidades, $ a costo; las
      unidades y el valor consideran solo stock > 0).
    """
    if accion not in ('excluir', 'sin_diferencia', 'faltante', 'operativos'):
        raise ValidationError('Acción no válida: use "excluir", "sin_diferencia", "faltante" u "operativos"')

    pendientes = _pendientes_no_contados(inventario, solo_stock_cero, marcas, detalle_ids)

    operativos = []
    if accion in ('faltante', 'sin_diferencia', 'operativos'):
        ids_operativos = _ids_operativos(pendientes)
        if ids_operativos:
            operativos = list(
                inventario.detalles.filter(id__in=ids_operativos)
                .values('id', 'sku', 'producto_nombre', 'stock_sistema').order_by('-stock_sistema')
            )
            pendientes = pendientes.exclude(id__in=ids_operativos)
    if accion == 'operativos':
        # Solo dejar fuera los operativos (primer paso del plan de la pantalla:
        # así quedan excluidos aunque no se decida nada sobre su marca).
        pendientes = pendientes.none()

    impacto = pendientes.aggregate(
        lineas=Count('id'),
        unidades=Coalesce(Sum('stock_sistema', filter=Q(stock_sistema__gt=0)), 0),
        valor=Coalesce(
            Sum(ExpressionWrapper(
                F('stock_sistema') * F('costo_unitario_sistema'),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            ), filter=Q(stock_sistema__gt=0)),
            Value(0), output_field=DecimalField(max_digits=18, decimal_places=2)
        ),
    )
    resultado = {
        'accion': accion,
        'solo_stock_cero': bool(solo_stock_cero),
        'lineas': impacto['lineas'],
        'unidades': impacto['unidades'],
        'valor_costo': float(impacto['valor'] or 0),
        'excluidas_por_stock_negativo': 0,
        'operativos': [
            {'id': o['id'], 'sku': o['sku'], 'articulo': o['producto_nombre'], 'stock': o['stock_sistema']}
            for o in operativos
        ],
        'operativos_unidades': sum(max(o['stock_sistema'], 0) for o in operativos),
    }
    if accion == 'faltante':
        # Misma regla que el modelo con físico 0: |diferencia| = |stock| (sin
        # contar el post-corte, que solo se conoce al aplicar).
        resultado['reconteo_estimado'] = pendientes.filter(
            Q(stock_sistema__gte=2) | Q(stock_sistema__lte=-2)
        ).count()
    if previsualizar or (impacto['lineas'] == 0 and not operativos):
        return resultado

    ahora = timezone.now()
    if operativos:
        # Operativos: excluidos (el stock de bolsas/VISA/envíos queda igual)
        lineas_operativas = list(inventario.detalles.filter(id__in=[o['id'] for o in operativos]))
        for d in lineas_operativas:
            d.excluir_de_analisis = True
            d.reconteo_requerido = False
            d.observaciones = ((d.observaciones or '') + '\n' + MARCA_OBSERVACION_OPERATIVO).strip()
        TomaInventarioDetalle.objects.bulk_update(
            lineas_operativas, ['excluir_de_analisis', 'reconteo_requerido', 'observaciones']
        )
    # El momento del conteo es el del archivo: el corte si se contó con la tienda
    # cerrada (post-corte 0); si no, ahora. Así «faltante» y «mantener» quedan en la
    # misma foto que lo que vino en la pistola (el informe compara todo al corte).
    fecha_conteo = (
        _resolver_fecha_conteo(inventario, None, inventario.conteo_tienda_cerrada)
        if accion in ('faltante', 'sin_diferencia') else ahora
    )
    if accion == 'excluir':
        pendientes.update(excluir_de_analisis=True, reconteo_requerido=False)
    elif accion == 'faltante':
        ids = list(pendientes.values_list('id', flat=True))
        for inicio in range(0, len(ids), BATCH_SIZE):
            lote = list(inventario.detalles.filter(id__in=ids[inicio:inicio + BATCH_SIZE]))
            pt_ids = [d.producto_talla_id for d in lote]
            movimientos = _obtener_movimientos_post_corte_batch(
                pt_ids, inventario.fecha_corte, fecha_conteo, inventario.sucursal_id
            )
            # Lo vendido DESPUÉS del conteo existía al contar (si no, no se habría
            # vendido): esas unidades no son faltante. Sin esto, un SKU no
            # pistoleado y vendido hoy dejaba el stock en negativo al aplicar y la
            # toma quedaba trabada en «Aprobado» con error.
            despues = (
                _obtener_movimientos_post_corte_batch(pt_ids, fecha_conteo, ahora, inventario.sucursal_id)
                if fecha_conteo < ahora else {}
            )
            for d in lote:
                post = movimientos.get(d.producto_talla_id, 0)
                base = d.stock_sistema + post
                vendido_despues = max(0, -despues.get(d.producto_talla_id, 0))
                fisico = min(vendido_despues, max(base, 0))
                d.stock_movimientos_post_corte = post
                d.stock_sistema_ajustado = base
                d.stock_fisico = fisico
                d.diferencia = fisico - base
                d.reconteo_requerido = requiere_reconteo(d.diferencia, base) and d.stock_reconteo is None
                d.contado = True
                d.fecha_conteo = fecha_conteo
                d.usuario_conteo = usuario
                d.observaciones = (
                    (d.observaciones or '') + '\n' + informe_toma.OBSERVACION_NO_APARECIO
                    + (f' (se cuentan {fisico} u. vendidas después del conteo)' if fisico else '')
                ).strip()
            TomaInventarioDetalle.objects.bulk_update(lote, [
                'stock_movimientos_post_corte', 'stock_sistema_ajustado', 'stock_fisico',
                'diferencia', 'reconteo_requerido', 'contado', 'fecha_conteo',
                'usuario_conteo', 'observaciones',
            ], batch_size=BATCH_SIZE)
        if inventario.estado == 'BORRADOR':
            inventario.estado = 'EN_CONTEO'
            inventario.fecha_inicio_conteo = inventario.fecha_inicio_conteo or ahora
            inventario.save(update_fields=['estado', 'fecha_inicio_conteo', 'updated_at'])
    elif accion == 'sin_diferencia':
        # Mantener el stock del sistema: hay que fijar el post-corte por línea,
        # como hace registrar_conteo, para que diferencia quede en 0 de verdad.
        ids = list(pendientes.values_list('id', flat=True))
        negativas = []
        for inicio in range(0, len(ids), BATCH_SIZE):
            lote = list(
                inventario.detalles.filter(id__in=ids[inicio:inicio + BATCH_SIZE])
            )
            movimientos = _obtener_movimientos_post_corte_batch(
                [d.producto_talla_id for d in lote], inventario.fecha_corte, fecha_conteo, inventario.sucursal_id
            )
            a_contar = []
            for d in lote:
                post = movimientos.get(d.producto_talla_id, 0)
                ajustado = d.stock_sistema + post
                if ajustado < 0:
                    # Un físico negativo no existe: la línea con stock del sistema
                    # bajo cero se excluye (queda para revisión) en vez de «contarse».
                    negativas.append(d.id)
                    continue
                d.stock_movimientos_post_corte = post
                d.stock_sistema_ajustado = ajustado
                d.stock_fisico = ajustado
                d.diferencia = 0
                d.reconteo_requerido = False
                d.contado = True
                d.fecha_conteo = fecha_conteo
                d.usuario_conteo = usuario
                d.observaciones = ((d.observaciones or '') + '\n' + informe_toma.OBSERVACION_MANTENIDO).strip()
                a_contar.append(d)
            TomaInventarioDetalle.objects.bulk_update(a_contar, [
                'stock_movimientos_post_corte', 'stock_sistema_ajustado', 'stock_fisico',
                'diferencia', 'reconteo_requerido', 'contado', 'fecha_conteo',
                'usuario_conteo', 'observaciones',
            ], batch_size=BATCH_SIZE)
        if negativas:
            TomaInventarioDetalle.objects.filter(id__in=negativas).update(
                excluir_de_analisis=True, reconteo_requerido=False
            )
            resultado['excluidas_por_stock_negativo'] = len(negativas)

        if inventario.estado == 'BORRADOR':
            inventario.estado = 'EN_CONTEO'
            inventario.fecha_inicio_conteo = inventario.fecha_inicio_conteo or ahora
            inventario.save(update_fields=['estado', 'fecha_inicio_conteo', 'updated_at'])

    inventario.calcular_metricas()
    descripcion_accion = {
        'excluir': 'excluidas del análisis',
        'sin_diferencia': 'conservan el stock del sistema (no se pistolearon)',
        'faltante': 'contadas en 0 (faltante: no aparecieron en el conteo)',
        'operativos': 'resueltas (solo SKU operativos)',
    }[accion]
    alcance = ''
    if marcas is not None:
        alcance = f' [marcas: {", ".join(str(m) or informe_toma.SIN_MARCA for m in marcas)[:300]}]'
    elif detalle_ids is not None:
        alcance = f' [{len(detalle_ids)} SKU elegidos]'
    _registrar_log(
        inventario=inventario,
        tipo_accion='MODIFICACION',
        descripcion=(
            f'{impacto["lineas"]} líneas sin contar {descripcion_accion}{alcance}'
            + (' (solo stock 0)' if solo_stock_cero else '')
            + f'; {impacto["unidades"]} u. / ${float(impacto["valor"] or 0):,.0f} a costo'
            + (f'. {len(operativos)} SKU operativos excluidos ({resultado["operativos_unidades"]} u.)'
               if operativos else '')
        ),
        usuario=usuario,
        datos=resultado,
    )
    return resultado


@require_POST
@login_required
@transaction.atomic
def resolver_no_contados(request, inventario_id):
    """
    POST gestion-inventarios/api/no-contados/<id>/

    Tres formas de body:
    - {"agrupar": true}: lo que no apareció en el conteo agrupado por marca (con
      sus SKUs) + los SKUs operativos, para decidir en la pantalla. No escribe.
    - {"plan": [{"accion": ..., "marcas": [...]} | {"accion": ..., "detalle_ids": [...]}, ...],
       "previsualizar": bool}: decisiones por marca y por SKU en UNA transacción.
      Los pasos se aplican en orden y cada uno solo toca lo que sigue sin
      resolver, así que las decisiones por SKU van primero y la de su marca
      después cubre el resto. Un paso sin marcas ni ids toma todo lo pendiente.
    - {"accion": "excluir"|"sin_diferencia"|"faltante", "solo_stock_cero": bool,
       "previsualizar": bool}: una sola acción para todo lo pendiente.
    """
    inventario = _inventario_del_usuario(request, inventario_id)
    if inventario is None:
        return _error_sin_acceso()
    if inventario.estado not in ['BORRADOR', 'EN_CONTEO']:
        return JsonResponse({
            'success': False,
            'error': f'El inventario está en estado {inventario.get_estado_display()} y ya no admite conteos'
        })
    try:
        data = json.loads(request.body) if request.body else {}
        if data.get('agrupar'):
            grupos, operativos = _agrupar_no_contados(inventario)
            return JsonResponse({
                'success': True,
                'grupos': grupos,
                'operativos': operativos,
                'tipo_inventario': inventario.tipo_inventario,
                'tienda_cerrada': inventario.conteo_tienda_cerrada,
            })

        plan = data.get('plan')
        if plan is not None:
            if not isinstance(plan, list) or not plan:
                return JsonResponse({'success': False, 'error': 'El plan está vacío'})
            pasos = []
            for paso in plan:
                pasos.append(_resolver_no_contados(
                    inventario, request.user,
                    accion=paso.get('accion'),
                    marcas=paso.get('marcas'),
                    detalle_ids=paso.get('detalle_ids'),
                    previsualizar=bool(data.get('previsualizar')),
                ))
            return JsonResponse({
                'success': True,
                'pasos': pasos,
                'estado': inventario.estado,
                'progreso': float(inventario.progreso_conteo),
                'pendientes': inventario.detalles.filter(excluir_de_analisis=False, contado=False).count(),
            })

        resultado = _resolver_no_contados(
            inventario, request.user,
            accion=data.get('accion', 'excluir'),
            solo_stock_cero=bool(data.get('solo_stock_cero')),
            previsualizar=bool(data.get('previsualizar')),
        )
        return JsonResponse({
            'success': True,
            'resultado': resultado,
            'estado': inventario.estado,
            'progreso': float(inventario.progreso_conteo),
        })
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Datos JSON inválidos'})
    except ValidationError as e:
        transaction.set_rollback(True)
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)})
    except Exception as e:
        transaction.set_rollback(True)
        logger.error(f"Error al resolver no contados: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


# ==============================================================================
# UTILIDADES
# ==============================================================================

def _registrar_log(inventario, tipo_accion, descripcion, usuario, datos=None):
    """
    Registra una entrada en el log de auditoría.
    """
    TomaInventarioLog.objects.create(
        toma_inventario=inventario,
        tipo_accion=tipo_accion,
        descripcion=descripcion,
        usuario=usuario,
        datos_adicionales=datos or {}
    )


@require_GET
@login_required
def obtener_historial_inventario(request, inventario_id):
    """
    Obtener historial de cambios del inventario.
    """
    try:
        inventario = _inventario_del_usuario(request, inventario_id)
        if inventario is None:
            return _error_sin_acceso()
        
        logs = inventario.logs.select_related('usuario').order_by('-created_at')
        
        logs_data = [
            {
                'tipo_accion': log.tipo_accion,
                'tipo_accion_display': log.get_tipo_accion_display(),
                'descripcion': log.descripcion,
                'usuario': log.usuario.get_full_name() if log.usuario else 'Sistema',
                'fecha': log.created_at.strftime('%d/%m/%Y %H:%M:%S'),
                'datos': log.datos_adicionales
            }
            for log in logs
        ]
        
        return JsonResponse({
            'success': True,
            'historial': logs_data
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# NOTA: se eliminó el wrapper `registrar_movimiento_producto` que vivía al final
# de este archivo. Estaba muerto (la aplicación de ajustes importa el de views.py,
# que además actualiza stock plano y lotes) y era un tercer camino de escritura
# del kardex que NO tocaba el stock.
