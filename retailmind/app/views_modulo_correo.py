"""
Seguimiento de correo saliente: píxel de apertura y webhook del proveedor.

Estos dos endpoints son públicos por necesidad (los llaman el cliente de
correo del destinatario y el proveedor de envío), así que no escriben nada que
no esté respaldado por un token imposible de adivinar o por una firma HMAC.

El webhook es UNO SOLO para todo el ERP: se ubica el `EnvioCorreo` por el
identificador del relay y se reparte el evento según su `modulo`. El webhook
viejo de gift cards (`webhook_correo_giftcard`) sigue funcionando aparte
mientras ese módulo no migre a `EnvioCorreo`.
"""
import hashlib
import hmac
import json
import logging
import os

from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .models import (
    EnvioCorreo, Requerimiento, HistorialRequerimiento,
    ESTADOS_ENVIO_PROBLEMA,
)

logger = logging.getLogger('app')

# GIF transparente de 1x1. Se sirve tal cual, sin tocar disco.
_PIXEL_GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D'
    b'\x01\x00;'
)

# Eventos del proveedor → estado nuestro. Mismo mapa que gift cards, que ya
# está probado contra los payloads reales de MailerSend.
EVENTOS_CORREO = {
    'activity.delivered': 'ENTREGADO',
    'activity.opened': 'ABIERTO',
    'activity.opened_unique': 'ABIERTO',
    'activity.clicked': 'CLICK',
    'activity.hard_bounced': 'REBOTADO',
    'activity.soft_bounced': 'REBOTADO',
    'activity.spam_complaint': 'SPAM',
}

# Secret FIJO Y PÚBLICO con el que MailerSend firma las pruebas del panel.
# Sirve para confirmar que el endpoint responde, nunca para escribir datos.
_MAILERSEND_TEST_SECRET = 'test_Am3L1GuOIc4blLUuHqAPxxwkZaJyEk8G'


def _ip_cliente(request):
    reenviada = (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip()
    return reenviada or request.META.get('REMOTE_ADDR') or None


# ========== PÍXEL DE APERTURA ==========

@require_GET
def pixel_apertura(request, token):
    """Registra que se cargaron las imágenes del correo y devuelve un GIF 1x1.

    OJO con la lectura del dato: esto NO prueba que la persona leyó el correo.
    Apple Mail precarga las imágenes de todos los mensajes (falsos positivos) y
    Outlook corporativo las bloquea por defecto (falsos negativos). Sirve como
    semáforo blando, y así hay que mostrarlo en la interfaz.
    """
    respuesta = HttpResponse(_PIXEL_GIF, content_type='image/gif')
    # Sin esto el proxy de imágenes de Gmail cachea el píxel y la segunda
    # apertura nunca llega hasta acá.
    respuesta['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    respuesta['Pragma'] = 'no-cache'

    envio = EnvioCorreo.objects.filter(token=token).first()
    if not envio:
        return respuesta

    ahora = timezone.now()
    campos = ['aperturas', 'ultima_ip', 'ultimo_user_agent']
    envio.aperturas = (envio.aperturas or 0) + 1
    envio.ultima_ip = _ip_cliente(request)
    envio.ultimo_user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:300]
    if not envio.abierto_en:
        envio.abierto_en = ahora
        campos.append('abierto_en')
    if envio.registrar_estado('ABIERTO', cuando=ahora):
        campos += ['estado', 'estado_en']
    envio.save(update_fields=campos)

    _reflejar_en_modulo(envio)
    return respuesta


# ========== WEBHOOK DEL PROVEEDOR ==========

@csrf_exempt
def webhook_correo(request):
    """Eventos de entrega del proveedor de correo, para todo el ERP.

    Autenticado por firma HMAC-SHA256 del cuerpo con `CORREO_WEBHOOK_SECRET`:
    sin firma válida NO se escribe nada. Responde 200 a las verificaciones de
    URL porque el proveedor comprueba el endpoint ANTES de entregar el secret
    de firma (si acá devolviéramos error, no habría forma de dar de alta el
    webhook).
    """
    if request.method != 'POST':
        return JsonResponse({
            'success': True,
            'endpoint': 'webhook de seguimiento de correo',
            'listo': bool(os.environ.get('CORREO_WEBHOOK_SECRET')),
        })

    secret = os.environ.get('CORREO_WEBHOOK_SECRET', '')
    if not secret:
        logger.warning('Webhook de correo sin CORREO_WEBHOOK_SECRET: se acepta '
                       'la llamada para permitir el alta, pero no se procesa.')
        return JsonResponse({
            'success': True,
            'pendiente_configuracion': True,
            'mensaje': ('Endpoint activo. Falta definir CORREO_WEBHOOK_SECRET '
                        'para empezar a registrar estados de entrega.'),
        })

    firma = (request.headers.get('Signature')
             or request.headers.get('X-Mailersend-Signature') or '')

    def _firma_ok(clave):
        esperada = hmac.new(clave.encode('utf-8'), request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(firma.strip().lower(), esperada)

    if not _firma_ok(secret):
        if _firma_ok(_MAILERSEND_TEST_SECRET):
            logger.info('Webhook de correo: petición de PRUEBA recibida OK')
            return JsonResponse({
                'success': True, 'prueba': True,
                'mensaje': 'Conexión verificada. Las pruebas no modifican datos.',
            })
        logger.warning('Webhook de correo con firma inválida (ip=%s)', _ip_cliente(request))
        return JsonResponse({'success': False, 'error': 'Firma inválida.'}, status=401)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'JSON inválido.'}, status=400)

    evento = (payload.get('type') or '').strip()
    nuevo_estado = EVENTOS_CORREO.get(evento)
    if not nuevo_estado:
        # Evento que no nos interesa (queued, sent...): 200 para que el
        # proveedor no lo reintente eternamente.
        return JsonResponse({'success': True, 'ignorado': evento})

    # El payload cambió entre versiones del webhook, así que se leen las dos:
    #   v1: data.email = {message_id, recipient: {email}, reason}
    #   v2: data.email = "destinatario" (texto) y data.message_id
    datos = payload.get('data') or {}
    campo_email = datos.get('email')
    email_obj = campo_email if isinstance(campo_email, dict) else {}

    proveedor_id = (
        email_obj.get('message_id')
        or (email_obj.get('message') or {}).get('id')
        or datos.get('message_id')
        or datos.get('email_id')
        or ''
    )
    destinatario = (
        (campo_email if isinstance(campo_email, str) else '')
        or (email_obj.get('recipient') or {}).get('email')
        or datos.get('recipient')
        or ''
    ).strip()
    detalle = (
        email_obj.get('reason')
        or datos.get('reason')
        or (datos.get('morph') or {}).get('reason')
        or ''
    )[:255]

    envio = _ubicar_envio(proveedor_id, destinatario)
    if not envio:
        # Puede ser un correo de gift cards (que todavía usa su propia tabla) o
        # anterior a la bitácora. No es un error del proveedor.
        return JsonResponse({'success': True, 'sin_envio': True,
                             'destino': destinatario})

    ahora = timezone.now()
    campos = []
    if nuevo_estado == 'ENTREGADO' and not envio.entregado_en:
        envio.entregado_en = ahora
        campos.append('entregado_en')
    if nuevo_estado == 'CLICK':
        envio.clicks = (envio.clicks or 0) + 1
        campos.append('clicks')
        if not envio.click_en:
            envio.click_en = ahora
            campos.append('click_en')
    if nuevo_estado == 'ABIERTO' and not envio.abierto_en:
        envio.abierto_en = ahora
        campos.append('abierto_en')
    if envio.registrar_estado(nuevo_estado, detalle=detalle, cuando=ahora):
        campos += ['estado', 'estado_en', 'estado_detalle']
    if proveedor_id and not envio.proveedor_message_id:
        envio.proveedor_message_id = str(proveedor_id)[:120]
        campos.append('proveedor_message_id')
    if campos:
        envio.save(update_fields=list(dict.fromkeys(campos)))

    _reflejar_en_modulo(envio, detalle=detalle)

    logger.info('Webhook correo evento=%s estado=%s envio=%s destino=%s',
                evento, nuevo_estado, envio.id, destinatario)
    return JsonResponse({'success': True, 'estado': envio.estado, 'envio': envio.id})


def _ubicar_envio(proveedor_id, destinatario):
    """Encuentra el envío por id del relay, y si no, por el último a ese destino."""
    if proveedor_id:
        limpio = str(proveedor_id).strip().strip('<>')
        envio = EnvioCorreo.objects.filter(
            Q(proveedor_message_id=limpio) | Q(message_id__icontains=limpio)
        ).order_by('-enviado_en').first()
        if envio:
            return envio
    if destinatario:
        return (EnvioCorreo.objects
                .filter(destinatario__iexact=destinatario, enviado_en__isnull=False)
                .order_by('-enviado_en').first())
    return None


def _reflejar_en_modulo(envio, detalle=''):
    """Deja el evento anotado donde el usuario lo va a ver.

    El estado vive en `EnvioCorreo`, pero un rebote tiene que aparecer en la
    ficha del requerimiento: si solo queda en la bitácora, nadie se entera de
    que el proveedor jamás recibió el reclamo.
    """
    if envio.modulo != 'REQUERIMIENTO' or not envio.objeto_id:
        return
    if envio.estado not in ESTADOS_ENVIO_PROBLEMA:
        return
    requerimiento = Requerimiento.objects.filter(pk=envio.objeto_id).first()
    if not requerimiento:
        return
    # Una sola anotación por envío problemático: el proveedor puede reenviar el
    # mismo evento varias veces y no queremos 20 filas iguales en el historial.
    comentario = (f'PROBLEMA DE ENTREGA ({envio.estado}) del correo a '
                  f'{envio.destinatario}' + (f': {detalle}' if detalle else ''))
    ya_registrado = HistorialRequerimiento.objects.filter(
        requerimiento=requerimiento,
        accion='PROBLEMA_ENTREGA_CORREO',
        comentario=comentario,
    ).exists()
    if not ya_registrado:
        HistorialRequerimiento.objects.create(
            requerimiento=requerimiento,
            accion='PROBLEMA_ENTREGA_CORREO',
            comentario=comentario,
        )
