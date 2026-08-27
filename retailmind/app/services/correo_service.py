"""
Envío de correo con bitácora, píxel de apertura y Reply-To con token.

Reemplaza el patrón "armo un EmailMultiAlternatives y llamo a send()" que está
repetido por todo el ERP y que no deja rastro de nada. Lo que agrega:

  1. Una fila `EnvioCorreo` por mensaje, ANTES de mandarlo, para que un fallo
     también quede registrado (hasta ahora el fallo moría en el log).
  2. Verificación de que el servidor aceptó el mensaje: `send()` devuelve
     cuántos aceptó, y 0 significa que NO salió. Sin este chequeo el sistema
     marcaba "enviado" mensajes que nunca existieron.
  3. Captura del identificador del relay (ver `app/mail_backends.py`) para
     poder correlacionar los webhooks de entrega.
  4. Píxel de apertura propio, que funciona aunque se cambie de proveedor.
  5. Reply-To con plus-addressing (`buzon+<token>@dominio`) para poder pegar la
     respuesta en la ficha correcta. Se activa solo cuando existe la variable
     de entorno CORREO_BUZON_RESPUESTAS.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from ..models import EnvioCorreo

logger = logging.getLogger('app')


class CorreoError(Exception):
    """El correo no salió. El llamador decide si aborta o sigue."""


def _buzon_respuestas():
    """Buzón genérico que recibe las respuestas, si está configurado."""
    return (getattr(settings, 'CORREO_BUZON_RESPUESTAS', '') or '').strip()


def _base_url():
    """URL pública del sistema, para el píxel y los enlaces del correo."""
    return (getattr(settings, 'CORREO_BASE_URL', '') or '').strip().rstrip('/')


def direccion_respuesta(token):
    """`requerimientos+<token>@dominio.cl`, o '' si no hay buzón configurado.

    Google Workspace entrega el plus-addressing al buzón base sin configurar
    alias, así que el token viaja gratis en la dirección de respuesta.
    """
    buzon = _buzon_respuestas()
    if not buzon or '@' not in buzon:
        return ''
    usuario, dominio = buzon.rsplit('@', 1)
    # Si el buzón ya trae un +algo, se respeta la parte local base.
    usuario = usuario.split('+', 1)[0]
    return f'{usuario}+{token}@{dominio}'


def url_pixel(token):
    base = _base_url()
    return f'{base}/app/c/a/{token}.png' if base else ''


def url_portal(token):
    base = _base_url()
    return f'{base}/app/c/r/{token}/' if base else ''


def _insertar_pixel(html, token):
    """Mete el píxel de 1x1 al final del cuerpo del correo.

    Va al final a propósito: arriba sobrevive mejor al recorte de Gmail, pero
    se lo comen los clientes que muestran una vista previa del encabezado y
    dispara aperturas falsas antes de que nadie lo lea.
    """
    url = url_pixel(token)
    if not url or not html:
        return html
    tag = (f'<img src="{url}" width="1" height="1" alt="" '
           f'style="display:block;width:1px;height:1px;border:0;opacity:0" />')
    if '</body>' in html.lower():
        idx = html.lower().rindex('</body>')
        return html[:idx] + tag + html[idx:]
    return html + tag


def enviar_correo_trazado(
    *,
    modulo,
    asunto,
    texto,
    destinatario,
    objeto_id=None,
    html=None,
    cc=None,
    reply_to=None,
    adjuntos=None,
    from_email=None,
    usuario=None,
    connection=None,
    tags=None,
    con_pixel=True,
    con_token_respuesta=True,
    es_copia_control=False,
):
    """Manda un correo y devuelve el `EnvioCorreo` que lo registra.

    `adjuntos` es una lista de tuplas (nombre, contenido, mimetype_opcional).
    Levanta `CorreoError` si el mensaje no salió; el `EnvioCorreo` queda igual
    guardado en estado FALLIDO para que el problema sea visible en la ficha.
    """
    cc = [c for c in (cc or []) if c]
    adjuntos = adjuntos or []

    envio = EnvioCorreo.objects.create(
        modulo=modulo,
        objeto_id=objeto_id,
        destinatario=destinatario,
        cc=', '.join(cc),
        from_email=from_email or settings.DEFAULT_FROM_EMAIL,
        asunto=asunto[:300],
        adjuntos=len(adjuntos),
        es_copia_control=es_copia_control,
        enviado_por=usuario if (usuario and usuario.is_authenticated) else None,
        estado='ENVIADO',
    )

    # Reply-To: la casilla genérica con el token va PRIMERO para que sea la que
    # el cliente de correo precarga. Las direcciones que pida el llamador
    # (típicamente el usuario que envía) van detrás.
    destinos_respuesta = []
    if con_token_respuesta:
        con_token = direccion_respuesta(envio.token)
        if con_token:
            destinos_respuesta.append(con_token)
    for direccion in (reply_to or []):
        direccion = (direccion or '').strip()
        if direccion and direccion.lower() not in (d.lower() for d in destinos_respuesta):
            destinos_respuesta.append(direccion)

    cuerpo_html = _insertar_pixel(html, envio.token) if con_pixel else html

    cabeceras = {'X-Entity-Ref-ID': envio.token}
    if tags:
        # MailerSend acepta hasta 5 tags por esta cabecera y las devuelve en el
        # webhook, lo que da una segunda vía de correlación además del id.
        cabeceras['X-MailerSend-Tags'] = ','.join(tags[:5])

    mensaje = EmailMultiAlternatives(
        subject=asunto,
        body=texto,
        from_email=envio.from_email,
        to=[destinatario],
        cc=cc or None,
        reply_to=destinos_respuesta or None,
        connection=connection,
        headers=cabeceras,
    )
    if cuerpo_html:
        mensaje.attach_alternative(cuerpo_html, 'text/html')
    for adjunto in adjuntos:
        nombre, contenido = adjunto[0], adjunto[1]
        tipo = adjunto[2] if len(adjunto) > 2 else None
        mensaje.attach(nombre, contenido, tipo)

    try:
        aceptados = mensaje.send(fail_silently=False)
    except Exception as e:
        envio.estado = 'FALLIDO'
        envio.estado_en = timezone.now()
        envio.error = str(e)[:2000]
        envio.save(update_fields=['estado', 'estado_en', 'error'])
        logger.exception('Correo %s NO enviado a %s (envio=%s)',
                         modulo, destinatario, envio.id)
        raise CorreoError(str(e)) from e

    if not aceptados:
        # El servidor no lanzó excepción pero tampoco aceptó el mensaje. Antes
        # esto pasaba como envío exitoso y el requerimiento quedaba marcado
        # como "enviado" sin que nadie hubiera recibido nada.
        envio.estado = 'FALLIDO'
        envio.estado_en = timezone.now()
        envio.error = 'El servidor de correo no aceptó el mensaje (0 aceptados).'
        envio.save(update_fields=['estado', 'estado_en', 'error'])
        logger.error('Correo %s rechazado por el relay destino=%s (envio=%s)',
                     modulo, destinatario, envio.id)
        raise CorreoError('El servidor de correo rechazó el mensaje. '
                          'Revisa la dirección del destinatario.')

    envio.enviado_en = timezone.now()
    envio.estado_en = envio.enviado_en
    envio.reply_to = ', '.join(destinos_respuesta)
    envio.message_id = str((mensaje.extra_headers or {}).get('Message-ID', ''))[:255]
    if not envio.message_id:
        try:
            envio.message_id = str(mensaje.message()['Message-ID'] or '')[:255]
        except Exception:
            envio.message_id = ''
    envio.proveedor_message_id = getattr(mensaje, 'rm_proveedor_message_id', '') or ''
    envio.save(update_fields=[
        'enviado_en', 'estado_en', 'reply_to', 'message_id', 'proveedor_message_id',
    ])

    logger.info('Correo %s enviado destino=%s envio=%s relay_id=%s',
                modulo, destinatario, envio.id, envio.proveedor_message_id or '-')
    return envio
