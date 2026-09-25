"""
Endpoints del agente "Cargar desde factura" de Gestión de Productos
(verGestionProductos.html → modal #modalCargaFactura, js/carga_factura.js).

Flujo: subir (PDF + bodega) → la lectura corre en segundo plano → estado
(polling) → planificar (vista previa, con las correcciones de la persona) →
cargar (una factura, en segundo plano) → estado.

La lógica está en app/services/carga_factura/web.py; aquí solo permisos,
parseo y JSON. Permiso: el de la pantalla ('gestion_producto', mapeado en
middleware_permisos) y, además, la bodega elegida tiene que ser una de las
del usuario.
"""
import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import CargaFacturaPdf, Sucursal
from .services.carga_factura import chat as svc_chat
from .services.carga_factura import web as svc_web
from .services.carga_factura.facturas import ErrorCarga
from .utils_permisos import obtener_sucursales_usuario

logger = logging.getLogger('app')

TAMANO_MAXIMO_PDF = 30 * 1024 * 1024


def _error(mensaje, status=400):
    return JsonResponse({'success': False, 'error': mensaje}, status=status)


def _cuerpo(request):
    if request.content_type == 'application/json':
        try:
            return json.loads(request.body or b'{}')
        except json.JSONDecodeError:
            raise ErrorCarga('El cuerpo de la petición no es JSON válido')
    return request.POST


def _sesion_del_usuario(request, sesion_id):
    """La sesión, si es de una bodega que el usuario puede ver (None si no)."""
    ids = obtener_sucursales_usuario(request.user).values_list('id', flat=True)
    return (CargaFacturaPdf.objects.select_related('sucursal', 'creado_por')
            .filter(id=sesion_id, sucursal_id__in=ids).first())


def _resumen(sesion):
    return {
        'id': sesion.id, 'estado': sesion.estado, 'progreso': sesion.progreso,
        'error': sesion.error, 'nombre_archivo': sesion.nombre_archivo,
        'sucursal': sesion.sucursal.alias, 'sucursal_id': sesion.sucursal_id,
        'marca': sesion.marca, 'lecturas': sesion.lecturas, 'modelo': sesion.modelo,
        'creado_por': (sesion.creado_por.get_full_name() or sesion.creado_por.username)
                      if sesion.creado_por else '',
        'creado_en': sesion.creado_en.isoformat(timespec='seconds'),
        'facturas': [{'idx': i, 'folio': d.get('folio'), 'proveedor': d.get('proveedor_nombre'),
                      'estado': d.get('_estado', 'PENDIENTE'), 'lineas': len(d.get('lineas') or [])}
                     for i, d in enumerate(sesion.facturas or [])],
    }


@require_GET
@login_required
def api_carga_factura_opciones(request):
    """Listas para los editores de la vista previa + si la lectura está configurada."""
    return JsonResponse({
        'success': True,
        'configurada': bool(getattr(settings, 'ANTHROPIC_API_KEY', '')),
        **svc_web.opciones_catalogo(request.user),
    })


@require_GET
@login_required
def api_carga_factura_lista(request):
    """Últimas sesiones (no cerradas) de las bodegas del usuario."""
    ids = obtener_sucursales_usuario(request.user).values_list('id', flat=True)
    sesiones = (CargaFacturaPdf.objects.select_related('sucursal', 'creado_por')
                .filter(sucursal_id__in=ids).exclude(estado='CERRADA').order_by('-id')[:15])
    return JsonResponse({'success': True, 'sesiones': [_resumen(s) for s in sesiones]})


@require_POST
@login_required
def api_carga_factura_subir(request):
    """Recibe el PDF y arranca la lectura en segundo plano."""
    if not getattr(settings, 'ANTHROPIC_API_KEY', ''):
        return _error('La lectura de facturas no está configurada en este servidor: falta '
                      'ANTHROPIC_API_KEY en las variables de entorno.', status=503)
    archivo = request.FILES.get('archivo')
    if archivo is None:
        return _error('Adjunta la factura en PDF.')
    if not str(archivo.name).lower().endswith('.pdf'):
        return _error('Solo se aceptan archivos PDF.')
    if archivo.size > TAMANO_MAXIMO_PDF:
        return _error('El PDF pesa más de 30 MB; divídelo y súbelo en partes.')
    sucursal = None
    valor = (request.POST.get('sucursal') or '').strip()
    permitidas = obtener_sucursales_usuario(request.user)
    if valor.isdigit():
        sucursal = permitidas.filter(id=int(valor)).first()
    elif valor:
        sucursal = permitidas.filter(alias__iexact=valor).first()
    if sucursal is None:
        return _error('Elige la bodega donde entra la mercadería.')
    try:
        lecturas = max(1, min(3, int(request.POST.get('lecturas') or 2)))
    except ValueError:
        lecturas = 2
    marca = (request.POST.get('marca') or '').strip().upper()[:100]
    indicaciones = (request.POST.get('indicaciones') or '').strip()[:2000]

    sesion = CargaFacturaPdf.objects.create(
        creado_por=request.user, sucursal=sucursal, marca=marca, archivo=archivo,
        nombre_archivo=str(archivo.name)[:255], lecturas=lecturas,
        estado='LEYENDO', progreso='En cola…',
    )
    sesion.agregar_mensaje(
        svc_web.USUARIO,
        f'Factura «{sesion.nombre_archivo}» para la bodega {sucursal.alias}'
        + (f', marca {marca}' if marca else '') + '.', tipo='subida',
        # Lo que se envió, tal cual, para que la pantalla lo muestre como tarjeta.
        envio={'archivo': sesion.nombre_archivo, 'bytes': archivo.size, 'bodega': sucursal.alias,
               'marca': marca, 'lecturas': lecturas,
               **({'indicaciones': indicaciones} if indicaciones else {})})
    if indicaciones:
        # Van al lector como pistas (ver web.leer_en_segundo_plano).
        sesion.agregar_mensaje(svc_web.USUARIO, indicaciones, tipo='indicaciones')
    sesion.agregar_mensaje(
        svc_web.AGENTE,
        f'Recibí el PDF. Lo estoy leyendo con {lecturas} lectura(s) '
        f'{"independientes que después comparo" if lecturas > 1 else "rápida"}; '
        f'suele tardar unos minutos. Te aviso aquí cuando tenga la vista previa.',
        tipo='texto')
    svc_web.iniciar_lectura(sesion.id)
    return JsonResponse({'success': True, 'id': sesion.id, 'sesion': _resumen(sesion)})


@require_GET
@login_required
def api_carga_factura_estado(request, sesion_id):
    sesion = _sesion_del_usuario(request, sesion_id)
    if sesion is None:
        return _error('No existe esa sesión o no es de tus bodegas.', status=404)
    # Un hilo que murió con un reinicio del servidor dejaría la sesión
    # «leyendo» para siempre: se cierra para que la persona pueda seguir.
    svc_web.revisar_interrumpida(sesion)
    return JsonResponse({'success': True, 'sesion': _resumen(sesion), 'mensajes': sesion.mensajes})


@require_POST
@login_required
def api_carga_factura_planificar(request, sesion_id):
    """Guarda las correcciones (si vienen) y devuelve la vista previa."""
    sesion = _sesion_del_usuario(request, sesion_id)
    if sesion is None:
        return _error('No existe esa sesión o no es de tus bodegas.', status=404)
    if sesion.estado not in ('LEIDA', 'CARGANDO'):
        return _error(f'La sesión está {sesion.get_estado_display().lower()}; '
                      f'todavía no hay vista previa.')
    try:
        cuerpo = _cuerpo(request)
        cambios = cuerpo.get('facturas') if isinstance(cuerpo, dict) else None
        if cambios:
            svc_web.aplicar_correcciones(sesion, cambios)
        solo = cuerpo.get('idx') if isinstance(cuerpo, dict) else None
        solo = int(solo) if solo not in (None, '') else None
        facturas = svc_web.planificar(sesion, request.user, solo_idx=solo)
    except ErrorCarga as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.exception('carga_factura: error planificando la sesión %s', sesion_id)
        return _error(f'No pude armar la vista previa: {type(exc).__name__}: {exc}', status=500)
    return JsonResponse({'success': True, 'sesion': _resumen(sesion), 'facturas': facturas})


@require_POST
@login_required
def api_carga_factura_cargar(request, sesion_id):
    """Arranca la carga de UNA factura en segundo plano."""
    sesion = _sesion_del_usuario(request, sesion_id)
    if sesion is None:
        return _error('No existe esa sesión o no es de tus bodegas.', status=404)
    if sesion.estado == 'CARGANDO':
        return _error('Ya hay una carga en curso en esta sesión; espera a que termine.')
    if sesion.estado != 'LEIDA':
        return _error('La sesión no está en vista previa.')
    try:
        cuerpo = _cuerpo(request)
        idx = int(cuerpo.get('idx'))
        data = sesion.facturas[idx]
    except (ErrorCarga, TypeError, ValueError, IndexError):
        return _error('Indica qué factura cargar.')
    if data.get('_estado') == 'CARGADA':
        return _error(f'La factura {data.get("folio")} ya se cargó.')
    opciones = cuerpo.get('opciones') if isinstance(cuerpo.get('opciones'), dict) else {}
    try:
        previa = svc_web.planificar(sesion, request.user, solo_idx=idx)[0]
    except Exception as exc:
        logger.exception('carga_factura: error validando la factura %s de la sesión %s', idx, sesion_id)
        return _error(f'No pude validar la factura: {type(exc).__name__}: {exc}', status=500)
    if previa['error']:
        return _error(previa['error'])
    if previa['totales']['bloqueantes']:
        return _error(f'La factura tiene {previa["totales"]["bloqueantes"]} línea(s) con error; '
                      f'corrígelas u omítelas antes de cargar.')
    if not previa['totales']['a_cargar']:
        return _error('No hay nada que cargar en esta factura.')

    existentes = [p for p in previa['planes'] if p['opciones'] and not p['omitida']]
    resumen = (f'Cargar la factura N° {data.get("folio")} en {sesion.sucursal.alias}: '
               f'{previa["totales"]["a_cargar"]} línea(s)')
    if existentes:
        textos = {'s': 'stock + costo + venta', 'c': 'stock + costo (venta sigue)',
                  't': 'solo stock', 'n': 'saltar'}
        # Opciones por N° de línea (dos líneas pueden compartir código: dos colores).
        resumen += ' · existentes: ' + ', '.join(
            f'línea {p["n"]} {p["articulo"]} → '
            f'{textos.get(str(opciones.get(str(p["n"]), p["opcion_sugerida"])).lower(), textos["s"])}'
            for p in existentes)
    sesion.estado = 'CARGANDO'
    sesion.progreso = 'Iniciando la carga…'
    sesion.save(update_fields=['estado', 'progreso', 'actualizado_en'])
    sesion.agregar_mensaje(svc_web.USUARIO, resumen + '.', tipo='carga', factura=idx)
    svc_web.iniciar_carga(sesion.id, idx, opciones, request.user.id)
    return JsonResponse({'success': True, 'sesion': _resumen(sesion)})


@require_POST
@login_required
def api_carga_factura_conversar(request, sesion_id):
    """Un mensaje al agente sobre la vista previa: Claude lo traduce a
    correcciones (mismos campos que la tarjeta) y responde."""
    sesion = _sesion_del_usuario(request, sesion_id)
    if sesion is None:
        return _error('No existe esa sesión o no es de tus bodegas.', status=404)
    try:
        cuerpo = _cuerpo(request)
        texto = cuerpo.get('texto') if isinstance(cuerpo, dict) else None
        salida = svc_chat.conversar(sesion, request.user, texto)
    except ErrorCarga as exc:
        return _error(str(exc))
    except Exception as exc:
        logger.exception('carga_factura: error en el chat de la sesión %s', sesion_id)
        return _error(f'No pude procesar el mensaje: {type(exc).__name__}: {exc}', status=500)
    sesion.refresh_from_db()
    return JsonResponse({'success': True, 'sesion': _resumen(sesion), **salida})


@require_POST
@login_required
def api_carga_factura_cerrar(request, sesion_id):
    """Saca la sesión de la lista de recientes (no borra nada)."""
    sesion = _sesion_del_usuario(request, sesion_id)
    if sesion is None:
        return _error('No existe esa sesión o no es de tus bodegas.', status=404)
    if sesion.estado in ('LEYENDO', 'CARGANDO'):
        return _error('Espera a que termine lo que está haciendo.')
    sesion.estado = 'CERRADA'
    sesion.save(update_fields=['estado', 'actualizado_en'])
    return JsonResponse({'success': True})
