"""
Lectura del buzón de respuestas: de un correo del proveedor a la ficha.

El correo que sale lleva un token en el Reply-To
(`calzadospaolafallados+a1b2c3@gmail.com`). Cuando el proveedor aprieta
"Responder", ese token vuelve en las cabeceras y permite saber A QUÉ
requerimiento corresponde la respuesta, sin que nadie tenga que copiarla a mano.

La identificación va en cascada, de lo más confiable a lo más frágil:

  1. Token en el destinatario (To / Cc / Delivered-To)  ← el caso normal
  2. In-Reply-To / References contra el Message-ID guardado del envío
  3. Número de requerimiento en el asunto (REQ-20260827-0001)

La 3 existe porque hay proveedores que responden desde otra casilla o abren un
correo nuevo citando el número, y esa respuesta igual tiene que llegar a la
ficha.
"""
import email
import logging
import re
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from django.utils import timezone

from ..models import EnvioCorreo, Requerimiento

logger = logging.getLogger('app')

RE_NUMERO_REQUERIMIENTO = re.compile(r'REQ-\d{8}-\d{4}', re.IGNORECASE)

# Separadores con los que los clientes de correo marcan el texto citado. Todo
# lo que viene después es el correo NUESTRO rebotando de vuelta: guardarlo
# haría que cada respuesta arrastrara el hilo completo.
RE_CITA = re.compile(
    r'^\s*(?:'
    r'>.*'
    r'|El .{0,80}escribió:'
    r'|On .{0,80}wrote:'
    r'|-{2,}\s*Mensaje original\s*-{2,}'
    r'|-{2,}\s*Original Message\s*-{2,}'
    r'|_{5,}'
    r'|De:\s.+'
    r'|From:\s.+'
    r')\s*$',
    re.IGNORECASE,
)


def _texto(valor):
    """Decodifica una cabecera MIME (=?UTF-8?B?...?=) a texto legible."""
    if not valor:
        return ''
    try:
        return str(make_header(decode_header(valor)))
    except Exception:
        return str(valor)


def extraer_token(mensaje, buzon):
    """Busca el token de plus-addressing en las cabeceras de destino.

    `buzon` es la casilla base (calzadospaolafallados@gmail.com); solo se
    aceptan tokens de ESA casilla, para no confundirse con otras direcciones
    que aparezcan en el hilo.
    """
    if not buzon or '@' not in buzon:
        return ''
    local, dominio = buzon.rsplit('@', 1)
    local = local.split('+', 1)[0]
    patron = re.compile(
        re.escape(local) + r'\+([0-9a-f]{32})@' + re.escape(dominio),
        re.IGNORECASE)

    for cabecera in ('Delivered-To', 'X-Original-To', 'To', 'Cc', 'X-Forwarded-To'):
        for valor in mensaje.get_all(cabecera, []):
            m = patron.search(_texto(valor))
            if m:
                return m.group(1).lower()
    return ''


def ubicar_envio(mensaje, buzon):
    """Devuelve (EnvioCorreo|None, cómo_se_encontró)."""
    token = extraer_token(mensaje, buzon)
    if token:
        envio = EnvioCorreo.objects.filter(token=token).first()
        if envio:
            return envio, 'token'

    # El proveedor respondió pero el token no llegó (algunos clientes reescriben
    # el To). El Message-ID original sí viaja en In-Reply-To / References.
    referencias = []
    for cabecera in ('In-Reply-To', 'References'):
        valor = mensaje.get(cabecera) or ''
        referencias += re.findall(r'<[^>]+>', valor)
    for referencia in referencias:
        envio = EnvioCorreo.objects.filter(message_id=referencia).first()
        if envio:
            return envio, 'in-reply-to'

    # Último recurso: el número del requerimiento en el asunto.
    asunto = _texto(mensaje.get('Subject'))
    m = RE_NUMERO_REQUERIMIENTO.search(asunto)
    if m:
        requerimiento = Requerimiento.objects.filter(
            numero_requerimiento__iexact=m.group(0)).first()
        if requerimiento:
            envio = (EnvioCorreo.objects
                     .filter(modulo='REQUERIMIENTO', objeto_id=requerimiento.id,
                             es_copia_control=False)
                     .order_by('-creado_en').first())
            if envio:
                return envio, 'asunto'

    return None, ''


def extraer_cuerpo(mensaje):
    """Texto plano de la respuesta, sin el hilo citado."""
    crudo = ''
    if mensaje.is_multipart():
        for parte in mensaje.walk():
            if parte.get_content_type() != 'text/plain':
                continue
            if 'attachment' in (parte.get('Content-Disposition') or ''):
                continue
            try:
                carga = parte.get_payload(decode=True) or b''
                crudo = carga.decode(parte.get_content_charset() or 'utf-8',
                                     errors='replace')
                break
            except Exception:
                continue
    else:
        try:
            carga = mensaje.get_payload(decode=True) or b''
            crudo = carga.decode(mensaje.get_content_charset() or 'utf-8',
                                 errors='replace')
        except Exception:
            crudo = ''

    lineas = []
    for linea in crudo.splitlines():
        if RE_CITA.match(linea):
            break
        lineas.append(linea)
    limpio = '\n'.join(lineas).strip()
    # Si al recortar la cita no quedó nada (respuestas tipo "OK" arriba de todo
    # que el cliente marcó raro), es preferible el texto completo a una ficha
    # que diga que el proveedor contestó en blanco.
    return limpio or crudo.strip()


def extraer_adjuntos(mensaje):
    """Metadatos de los archivos que traía la respuesta.

    Se guarda el inventario, no el archivo: dejar constancia de que el
    proveedor mandó una autorización es más importante que perderla del todo,
    y almacenar los binarios necesita decidir storage aparte.
    """
    adjuntos = []
    if not mensaje.is_multipart():
        return adjuntos
    for parte in mensaje.walk():
        disposicion = parte.get('Content-Disposition') or ''
        if 'attachment' not in disposicion:
            continue
        try:
            contenido = parte.get_payload(decode=True) or b''
        except Exception:
            contenido = b''
        adjuntos.append({
            'nombre': _texto(parte.get_filename()) or 'sin-nombre',
            'tipo': parte.get_content_type(),
            'tamano': len(contenido),
        })
    return adjuntos


def fecha_del_mensaje(mensaje):
    """Fecha real de la respuesta; si viene rota, la de ahora."""
    try:
        fecha = parsedate_to_datetime(mensaje.get('Date'))
    except Exception:
        return timezone.now()
    if not fecha:
        return timezone.now()
    if timezone.is_naive(fecha):
        return timezone.make_aware(fecha, timezone.get_current_timezone())
    return fecha


def datos_respuesta(mensaje, buzon):
    """Todo lo que necesitamos del correo, ya normalizado."""
    envio, via = ubicar_envio(mensaje, buzon)
    _, remitente = parseaddr(_texto(mensaje.get('From')))
    return {
        'envio': envio,
        'via': via,
        'remitente': remitente or _texto(mensaje.get('From'))[:200],
        'asunto': _texto(mensaje.get('Subject'))[:300],
        'cuerpo': extraer_cuerpo(mensaje),
        'recibido_en': fecha_del_mensaje(mensaje),
        'message_id': (mensaje.get('Message-ID') or '').strip()[:255],
        'in_reply_to': (mensaje.get('In-Reply-To') or '').strip()[:255],
        'adjuntos': extraer_adjuntos(mensaje),
    }


def parsear(crudo, buzon):
    """Atajo para tests y para el comando: bytes del correo → datos."""
    return datos_respuesta(email.message_from_bytes(crudo), buzon)
