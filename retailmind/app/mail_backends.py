"""
Backend SMTP que además recuerda qué contestó el relay.

El backend de Django tira a la basura la respuesta del `DATA` final: sus
`send_messages()` solo devuelven cuántos mensajes se aceptaron. Pero ahí viene
el identificador que después viaja en los webhooks del proveedor:

    250 Message queued as 6a9068083f3f7659bff76c97      (MailerSend)
    250 2.0.0 Ok: queued as 4d1f2a3b                    (Postfix)
    250 Ok 0100018f-...                                 (Amazon SES)

Sin ese id hay que adivinar a qué correo se refiere cada evento cruzando
destinatario + fecha (lo que hace hoy el webhook de gift cards, y falla cuando
se le mandan dos correos seguidos a la misma persona).

En vez de reimplementar `sendmail()` entero, se instrumenta el método `data`
de la conexión smtplib para que guarde su última respuesta. Es el punto exacto
donde el servidor entrega el identificador y no altera el flujo de Django.
"""
import re
import logging

from django.core.mail.backends.smtp import EmailBackend as SMTPEmailBackend

logger = logging.getLogger('app')

# `queued as XXX` cubre MailerSend y Postfix; el resto de proveedores suele
# poner el id al final del 250, así que se guarda igual la respuesta completa.
_RE_QUEUED = re.compile(r'queued as ([0-9A-Za-z._@-]+)', re.IGNORECASE)
_RE_ID_SUELTO = re.compile(r'\b([0-9a-f]{16,}|[0-9A-Za-z]{10,}-[0-9A-Za-z-]+)\b')


def _extraer_id(respuesta):
    """Saca el identificador del proveedor de la respuesta 250 del DATA."""
    if not respuesta:
        return ''
    m = _RE_QUEUED.search(respuesta)
    if m:
        return m.group(1)[:120]
    m = _RE_ID_SUELTO.search(respuesta)
    return m.group(1)[:120] if m else ''


def _instrumentar(conexion):
    """Envuelve `conexion.data` para dejar registrada su última respuesta."""
    if getattr(conexion, '_rm_instrumentada', False):
        return
    original = conexion.data

    def data(msg):
        codigo, respuesta = original(msg)
        try:
            texto = (respuesta.decode('utf-8', 'replace')
                     if isinstance(respuesta, bytes) else str(respuesta))
        except Exception:
            texto = ''
        conexion.rm_ultima_respuesta = texto
        return codigo, respuesta

    conexion.data = data
    conexion.rm_ultima_respuesta = ''
    conexion._rm_instrumentada = True


class TrazableEmailBackend(SMTPEmailBackend):
    """SMTP normal de Django + captura del identificador que devuelve el relay.

    Es un reemplazo directo: no cambia el envío en nada. Lo único que agrega es
    que, después de un envío exitoso, el propio objeto del mensaje queda con:

        email.rm_respuesta_relay      texto crudo del 250 final
        email.rm_proveedor_message_id id extraído de ese texto

    `correo_service.enviar_correo_trazado()` los lee para guardarlos en
    `EnvioCorreo`. Si el backend configurado fuera otro (consola en desarrollo,
    por ejemplo), esos atributos sencillamente no aparecen y todo sigue
    funcionando.
    """

    def open(self):
        nuevo = super().open()
        if self.connection is not None:
            try:
                _instrumentar(self.connection)
            except Exception:
                # Instrumentar es un extra: si falla, el correo igual tiene que
                # salir. Perder el id es molesto; perder el envío es grave.
                logger.warning('No se pudo instrumentar la conexión SMTP', exc_info=True)
        return nuevo

    def _send(self, email_message):
        if self.connection is not None:
            self.connection.rm_ultima_respuesta = ''
        enviado = super()._send(email_message)
        if enviado:
            respuesta = getattr(self.connection, 'rm_ultima_respuesta', '') or ''
            email_message.rm_respuesta_relay = respuesta[:255]
            email_message.rm_proveedor_message_id = _extraer_id(respuesta)
        return enviado
