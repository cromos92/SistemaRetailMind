"""
Tests de la captura de respuestas del proveedor desde el buzón.

Lo frágil acá es el parseo de correos reales: cada cliente cita el hilo a su
manera, codifica los acentos distinto y reescribe las cabeceras. Estos tests
usan correos crudos como los que manda Gmail y Outlook.
"""
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import EnvioCorreo, Requerimiento
from app.services import captura_respuestas
from app.tests.factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
)

BUZON = 'calzadospaolafallados@gmail.com'


def correo(token=None, *, remitente='ventas@proveedor.cl', asunto='Re: REQ',
           cuerpo='Aprobamos el cambio.', in_reply_to='', para=None):
    destino = para or (f'calzadospaolafallados+{token}@gmail.com' if token
                       else BUZON)
    cabeceras = [
        f'From: Proveedor <{remitente}>',
        f'To: {destino}',
        f'Subject: {asunto}',
        'Date: Wed, 27 Aug 2026 15:04:05 -0400',
        'Message-ID: <respuesta-001@proveedor.cl>',
    ]
    if in_reply_to:
        cabeceras.append(f'In-Reply-To: {in_reply_to}')
    cabeceras.append('Content-Type: text/plain; charset="utf-8"')
    return ('\r\n'.join(cabeceras) + '\r\n\r\n' + cuerpo).encode('utf-8')


class ParseoDeRespuestasTest(TestCase):
    """El parser, sin tocar la base."""

    def test_saca_el_token_del_destinatario(self):
        token = 'a' * 32
        info = captura_respuestas.parsear(correo(token), BUZON)
        self.assertEqual(info['remitente'], 'ventas@proveedor.cl')
        self.assertEqual(info['asunto'], 'Re: REQ')
        self.assertIn('Aprobamos', info['cuerpo'])

    def test_ignora_tokens_de_otra_casilla(self):
        # Si el hilo arrastra la dirección de otro sistema, no es nuestro token.
        crudo = correo(para='otracosa+' + ('b' * 32) + '@gmail.com')
        self.assertEqual(captura_respuestas.extraer_token(
            __import__('email').message_from_bytes(crudo), BUZON), '')

    def test_recorta_el_hilo_citado_de_gmail(self):
        cuerpo = ('Lo aprobamos, manden el producto.\r\n'
                  '\r\n'
                  'El mié, 27 ago 2026 a las 12:04, Requerimientos escribió:\r\n'
                  '> Estimados, adjuntamos el requerimiento REQ-20260827-0001\r\n'
                  '> con las fotos del defecto.\r\n')
        info = captura_respuestas.parsear(correo('c' * 32, cuerpo=cuerpo), BUZON)
        self.assertEqual(info['cuerpo'], 'Lo aprobamos, manden el producto.')
        self.assertNotIn('fotos del defecto', info['cuerpo'])

    def test_recorta_el_hilo_citado_de_outlook(self):
        cuerpo = ('Rechazado por uso indebido.\r\n'
                  '\r\n'
                  '________________________________\r\n'
                  'De: Requerimientos <requerimientos@webappsolutions.cl>\r\n'
                  'Enviado: miercoles, 27 de agosto de 2026\r\n')
        info = captura_respuestas.parsear(correo('d' * 32, cuerpo=cuerpo), BUZON)
        self.assertEqual(info['cuerpo'], 'Rechazado por uso indebido.')

    def test_una_respuesta_de_una_sola_linea_no_queda_vacia(self):
        # "OK" arriba de todo, sin separador: recortar de más dejaría la ficha
        # diciendo que el proveedor contestó en blanco.
        info = captura_respuestas.parsear(correo('e' * 32, cuerpo='OK'), BUZON)
        self.assertEqual(info['cuerpo'], 'OK')

    def test_decodifica_asuntos_con_acentos(self):
        crudo = correo('f' * 32, asunto='=?UTF-8?B?UmU6IEdhcmFudMOtYQ==?=')
        info = captura_respuestas.parsear(crudo, BUZON)
        self.assertEqual(info['asunto'], 'Re: Garantía')

    def test_fecha_del_correo_no_la_de_hoy(self):
        info = captura_respuestas.parsear(correo('a' * 32), BUZON)
        self.assertEqual(info['recibido_en'].year, 2026)
        self.assertEqual(info['recibido_en'].month, 8)


@override_settings(CORREO_BUZON_RESPUESTAS=BUZON)
class UbicarElEnvioTest(TestCase):
    """Las tres vías de identificación, en cascada."""

    def setUp(self):
        self.admin = crear_usuario(username='cap', rol='administrador')
        self.empresa = crear_empresa(nombre='Empresa Cap')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='NICKCAP')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        self.proveedor = crear_empresa(
            nombre='Prov Cap', rut='77.555.555-5', esProveedor=True)
        self.req = Requerimiento.objects.create(
            tipo='PRODUCTO_FALLADO', sucursal=self.sucursal,
            usuario_creador=self.admin, sku='777', nombre_producto='P',
            cliente_nombre='C', motivo='falla', proveedor=self.proveedor,
            estado='ESPERANDO_RESPUESTA', correo_enviado_proveedor=True,
            fecha_envio_proveedor=timezone.now())
        self.envio = EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='ventas@provcap.cl',
            message_id='<envio-original@retailmind>',
            enviado_en=timezone.now())

    def test_via_token(self):
        info = captura_respuestas.parsear(correo(self.envio.token), BUZON)
        self.assertEqual(info['envio'], self.envio)
        self.assertEqual(info['via'], 'token')

    def test_via_in_reply_to_cuando_el_token_no_vuelve(self):
        crudo = correo(para=BUZON, in_reply_to='<envio-original@retailmind>')
        info = captura_respuestas.parsear(crudo, BUZON)
        self.assertEqual(info['envio'], self.envio)
        self.assertEqual(info['via'], 'in-reply-to')

    def test_via_numero_en_el_asunto(self):
        # El proveedor abre un correo nuevo citando el número.
        crudo = correo(para=BUZON,
                       asunto=f'Consulta {self.req.numero_requerimiento}')
        info = captura_respuestas.parsear(crudo, BUZON)
        self.assertEqual(info['envio'], self.envio)
        self.assertEqual(info['via'], 'asunto')

    def test_correo_ajeno_no_se_imputa_a_nadie(self):
        info = captura_respuestas.parsear(
            correo(para=BUZON, asunto='Promoción de zapatillas'), BUZON)
        self.assertIsNone(info['envio'])

    def test_la_copia_de_control_no_se_usa_para_el_asunto(self):
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='control@empresa.cl', es_copia_control=True,
            enviado_en=timezone.now())
        crudo = correo(para=BUZON, asunto=self.req.numero_requerimiento)
        info = captura_respuestas.parsear(crudo, BUZON)
        self.assertEqual(info['envio'], self.envio)
