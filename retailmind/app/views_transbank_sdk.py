"""
API REST para integración Transbank POS con Web Serial API
El POS se conecta desde el navegador, el backend solo guarda transacciones
"""

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .services.transbank_simple_service import TransbankPersistenceService
from .models import ConfiguracionPOS
import logging

logger = logging.getLogger(__name__)

# Servicio de persistencia
persistence_service = TransbankPersistenceService()


@login_required
def gestion_transbank_pos_sdk(request):
    """
    Vista principal de gestión POS Integrado (pestañas Transbank y Mercado Pago).
    La conexión al POS Transbank se hace desde el navegador; la pestaña MP
    gestiona credenciales/webhook (solo admin) y asociación de cajas QR.
    """
    context = {}

    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')

        if sucursal_id:
            # Obtener configuración guardada si existe
            config_guardada = ConfiguracionPOS.objects.filter(
                sucursal_id=sucursal_id,
                tipo_pos='SDK_SERIAL',
                activo=True
            ).first()
            context['config_guardada'] = config_guardada
    except Exception as e:
        logger.warning(f"No se pudo cargar configuración: {e}")
        context['config_guardada'] = None

    # ── Pestaña Mercado Pago ──
    # Todos ven el estado; el ingreso de credenciales/webhook y la asociación
    # de cajas queda restringido a roles administrativos (el guard duro está
    # en los endpoints de views_mercadopago, esto solo controla la UI).
    from .models import Empresa, Sucursal
    context['es_admin_mp'] = getattr(request.user, 'rol', '') in ('administrador', 'administracion')
    # Sucursal de la sesión: preselecciona su caja en los selectores MP y es
    # la caja que usa el cierre de un usuario no-admin
    try:
        context['sucursal_sesion_id'] = int(
            request.session.get('idSucursalActual')
            or request.session.get('sucursalActual') or 0
        ) or None
    except (TypeError, ValueError):
        context['sucursal_sesion_id'] = None
    # Empresas candidatas a cuenta MP: las que tienen sucursales (dueñas de
    # tiendas). NO filtrar por esProveedor: las empresas madre de las cadenas
    # (ej. Nicole Andrea) también operan como CD/proveedoras y ese flag las
    # dejaba fuera del select. Estas tablas existen siempre — se cargan FUERA
    # del try de las tablas MP para que el select no quede vacío si las
    # migraciones 0222-0226 aún no están aplicadas.
    context['empresas_mp'] = (
        Empresa.objects.filter(sucursales_app__isnull=False)
        .distinct().order_by('nombre')
    )
    context['sucursales_mp'] = Sucursal.objects.order_by('alias')
    context['cuentas_mp'] = []
    context['configs_mp'] = []
    context['mp_migraciones_pendientes'] = False
    try:
        from .models import MercadoPagoConfig, MercadoPagoCuenta
        context['cuentas_mp'] = [{
            'empresa_id': c.empresa_id,
            'empresa_nombre': c.empresa.nombre or c.empresa.razon_social,
            'empresa_rut': c.empresa.rut,
            'mp_user_id': c.mp_user_id,
            'tiene_token': bool(c.access_token_cifrado),
            'tiene_secret': bool(c.webhook_secret_cifrado),
            'activo': c.activo,
        } for c in MercadoPagoCuenta.objects.select_related('empresa').all()]
        context['configs_mp'] = [{
            'id': cfg.id,
            'sucursal_id': cfg.sucursal_id,
            'sucursal_alias': cfg.sucursal.alias,
            'nombre': cfg.nombre,
            'external_store_id': cfg.external_store_id,
            'external_pos_id': cfg.external_pos_id,
            'device_id': cfg.device_id,
            'habilitado': cfg.habilitado,
            'es_principal': cfg.es_principal,
        } for cfg in MercadoPagoConfig.objects.select_related('sucursal').order_by('sucursal__alias', 'nombre')]
    except Exception as e:
        # Tablas MP inexistentes (migraciones sin aplicar) u otro error de BD:
        # la pestaña avisa en vez de mostrar selects vacíos sin explicación.
        logger.warning(f"No se pudo cargar datos Mercado Pago (¿migraciones pendientes?): {e}")
        context['mp_migraciones_pendientes'] = True

    return render(request, 'vistas/transbank_pos_sdk_oficial.html', context)


@login_required  
def gestion_transbank_pos_manual(request):
    """
    Vista con implementación manual (desarrollo)
    """
    context = {}
    
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if sucursal_id:
            config_guardada = ConfiguracionPOS.objects.filter(
                sucursal_id=sucursal_id,
                tipo_pos='SDK_SERIAL',
                activo=True
            ).first()
            context['config_guardada'] = config_guardada
    except Exception as e:
        logger.warning(f"No se pudo cargar configuración: {e}")
        context['config_guardada'] = None
    
    return render(request, 'vistas/transbank_pos_simple.html', context)


# ==================== ENDPOINTS ACTIVOS ====================

@api_view(['POST'])
@login_required
def autoconectar(request):
    """
    POST /app/pos/transbank/autoconectar/
    
    GUARDA la configuración del POS detectado desde el navegador
    
    Body:
        {
            "port": "COM9",
            "baudrate": 115200,
            "descripcion": "VX 520 GPRS Terminal"
        }
    """
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not sucursal_id:
            return Response({
                'success': False,
                'error': 'No hay sucursal en sesión'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Guardar configuración usando el servicio
        data = {
            'port': request.data.get('port', ''),
            'baudrate': request.data.get('baudrate', 115200),
            'nombre': f'POS-{request.data.get("port", "USB")}',
            'observaciones': f'Auto-detectado: {request.data.get("descripcion", "")}'
        }
        
        resultado = persistence_service.guardar_configuracion_pos(data, sucursal_id)
        
        return Response(resultado)
        
    except Exception as e:
        logger.error(f"Error en autoconectar: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@login_required
def venta(request):
    """
    POST /app/pos/transbank/venta/
    
    GUARDA una transacción procesada desde el navegador
    La venta se procesa en el navegador con Web Serial API,
    este endpoint solo guarda el resultado en la base de datos
    
    Body:
        {
            "amount": 25000,
            "ticket": "TKT123",
            "ticket_id": 456,
            "successful": true,
            "authorizationCode": "123456",
            "responseCode": 0,
            "operationNumber": "789",
            "cardType": "DB",
            "last4Digits": "1234",
            "cardBrand": "VISA",
            "sharesNumber": 0,
            "commerceCode": "597020000541",
            "terminalId": "ABC123",
            "accountingDate": "20260127",
            "realDate": "2026-01-27",
            "realTime": "15:30:45"
        }
    """
    try:
        sucursal_id = request.session.get('idSucursalActual') or request.session.get('sucursalActual')
        
        if not sucursal_id:
            return Response({
                'success': False,
                'error': 'No hay sucursal en sesión'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Guardar transacción usando el servicio
        resultado = persistence_service.guardar_transaccion(
            data=request.data,
            sucursal_id=sucursal_id,
            user=request.user
        )
        
        if resultado['success']:
            return Response(resultado)
        else:
            return Response(resultado, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        logger.error(f"Error guardando venta: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==================== ENDPOINTS DEPRECADOS ====================
# Los siguientes endpoints ya NO se usan porque la comunicación
# con el POS se hace desde JavaScript con Web Serial API

@api_view(['GET'])
def listar_puertos(request):
    """DEPRECATED: Los puertos se listan desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Los puertos se obtienen con Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def conectar(request):
    """DEPRECATED: La conexión se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def conectar_con_reintentos(request):
    """DEPRECATED: La conexión se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def desconectar(request):
    """DEPRECATED: La desconexión se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def verificar(request):
    """DEPRECATED: La verificación (POLL) se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def obtener_info_puerto(request):
    """DEPRECATED: La info del puerto se obtiene desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def cargar_llaves(request):
    """DEPRECATED: Cargar llaves se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def venta_multicodigo(request):
    """DEPRECATED: Venta multicodigo se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def ultima_venta(request):
    """DEPRECATED: Última venta se consulta desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def anular(request):
    """DEPRECATED: Anulación se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def totales(request):
    """DEPRECATED: Totales se consultan desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['GET'])
def detalles(request):
    """DEPRECATED: Detalles se consultan desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


@api_view(['POST'])
def cerrar_dia(request):
    """DEPRECATED: Cierre de día se hace desde JavaScript"""
    return Response({
        'success': False,
        'error': 'Endpoint deprecado. Use Web Serial API desde el navegador'
    }, status=status.HTTP_410_GONE)


# ══════════════════════════════════════════════════════════════════════════════
# Modo de cobro por sucursal: INTEGRADO (el POS habla con el terminal por el SDK
# Web Serial) vs MANUAL (el cajero digita el voucher con F6/F7).
#
# El "modo" NO es un campo propio: es el mismo criterio que usa el POS para
# decidir si autoconecta la máquina — una ConfiguracionPOS con tipo_pos
# 'SDK_SERIAL' y activo=True (ver pos_dashboard en views_modulo_ventas.py). Se
# expone tal cual para que lo que muestra esta tabla sea exactamente lo que hará
# la caja, sin inventar un estado paralelo que pueda desincronizarse.
# ══════════════════════════════════════════════════════════════════════════════

TIPO_POS_SDK = 'SDK_SERIAL'


def _es_admin_pos(user):
    return getattr(user, 'rol', '') in ('administrador', 'administracion')


@login_required
def listar_modos_pos(request):
    """Sucursales con su terminal Transbank y en qué modo cobra cada una."""
    from django.http import JsonResponse
    from .models import Sucursal

    try:
        sucursal_sesion = int(
            request.session.get('idSucursalActual')
            or request.session.get('sucursalActual') or 0
        ) or None
    except (TypeError, ValueError):
        sucursal_sesion = None

    configs = {}
    for c in ConfiguracionPOS.objects.select_related('sucursal').order_by('sucursal__alias', '-es_principal', 'id'):
        configs.setdefault(c.sucursal_id, []).append(c)

    filas = []
    for suc in Sucursal.objects.order_by('alias'):
        propias = configs.get(suc.id, [])
        # Misma condición que el POS: si no la cumple, la caja cobra a mano.
        integrada = next(
            (c for c in propias if c.tipo_pos == TIPO_POS_SDK and c.activo), None
        )
        principal = integrada or (propias[0] if propias else None)
        filas.append({
            'sucursal_id': suc.id,
            'sucursal': suc.alias or suc.nombre or f'Sucursal {suc.id}',
            'modo': 'INTEGRADO' if integrada else 'MANUAL',
            'config_id': principal.id if principal else None,
            'terminal': (principal.nombre if principal else ''),
            'tipo_pos': (principal.get_tipo_pos_display() if principal else ''),
            'puerto': (principal.puerto_conexion if principal else ''),
            'numero_serie': (principal.numero_serie or '') if principal else '',
            'estado_conexion': (principal.estado_conexion or '') if principal else '',
            'ultima_conexion': (
                principal.ultima_conexion.strftime('%d/%m/%Y %H:%M')
                if principal and principal.ultima_conexion else ''
            ),
            # Sin ninguna ConfiguracionPOS no se puede pasar a integrado desde
            # aquí: falta el puerto del terminal, que se configura en la caja.
            'puede_integrar': bool(propias),
            'es_sesion': suc.id == sucursal_sesion,
        })

    return JsonResponse({
        'success': True,
        'sucursal_sesion_id': sucursal_sesion,
        'puede_editar': _es_admin_pos(request.user),
        'filas': filas,
    })


@login_required
def cambiar_modo_pos(request):
    """Alterna el modo de cobro de una sucursal (solo administradores)."""
    import json
    from django.http import JsonResponse

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método no permitido'}, status=405)
    if not _es_admin_pos(request.user):
        return JsonResponse({
            'success': False,
            'error': 'Solo un administrador puede cambiar el modo de cobro de una caja.',
        }, status=403)

    try:
        datos = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Datos inválidos'}, status=400)

    try:
        sucursal_id = int(datos.get('sucursal_id') or 0)
    except (TypeError, ValueError):
        sucursal_id = 0
    modo = str(datos.get('modo') or '').strip().upper()

    if not sucursal_id or modo not in ('INTEGRADO', 'MANUAL'):
        return JsonResponse({'success': False, 'error': 'Sucursal o modo inválido'}, status=400)

    propias = list(ConfiguracionPOS.objects.filter(sucursal_id=sucursal_id))
    if not propias:
        return JsonResponse({
            'success': False,
            'error': ('Esta sucursal no tiene ningún terminal configurado: primero hay que '
                      'registrarlo con su puerto desde la caja.'),
        }, status=400)

    if modo == 'MANUAL':
        # Basta con que ninguna quede activa como SDK: el POS deja de autoconectar
        # y los cobros pasan por Débito/Crédito manual (F6/F7).
        ConfiguracionPOS.objects.filter(
            sucursal_id=sucursal_id, tipo_pos=TIPO_POS_SDK
        ).update(activo=False)
    else:
        # Se promueve la principal (o la primera) a terminal SDK activo y se
        # desactiva el resto, para que no haya dos candidatas a autoconectar.
        elegida = next((c for c in propias if c.es_principal), propias[0])
        ConfiguracionPOS.objects.filter(sucursal_id=sucursal_id).exclude(id=elegida.id).update(activo=False)
        elegida.tipo_pos = TIPO_POS_SDK
        elegida.activo = True
        elegida.save(update_fields=['tipo_pos', 'activo'])

    logger.info(
        "Modo de cobro POS cambiado a %s en sucursal=%s por %s",
        modo, sucursal_id, request.user.username,
    )
    return JsonResponse({'success': True, 'modo': modo})
