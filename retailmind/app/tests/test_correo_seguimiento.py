"""
Tests de la bitácora de correo: qué salió, qué falló, qué llegó.

Lo que se cubre acá es justamente lo que antes no existía: que un envío deje
fila, que un FALLO también la deje (antes moría en el log y el requerimiento
quedaba marcado como enviado igual), y que los eventos del proveedor no puedan
retroceder el estado ni escribirse sin firma válida.
"""
import hashlib
import hmac
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import EnvioCorreo, Requerimiento, HistorialRequerimiento
from app.services.correo_service import (
    CorreoError, direccion_respuesta, enviar_correo_trazado,
)
from app.tests.factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
)

WEBHOOK_SECRET = 'secreto-de-prueba-correo'


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sistema@test.cl',
    CORREO_BASE_URL='https://retail.test.cl',
)
class EnvioTrazadoTest(TestCase):
    """El servicio de envío y su bitácora."""

    def setUp(self):
        self.user = crear_usuario(
            username='envia', rol='administrador', email='envia@test.cl')

    def test_envio_exitoso_deja_fila_con_token_y_estado(self):
        envio = enviar_correo_trazado(
            modulo='REQUERIMIENTO', objeto_id=7, asunto='Hola',
            texto='cuerpo', destinatario='destino@proveedor.cl',
            usuario=self.user,
        )
        self.assertEqual(envio.estado, 'ENVIADO')
        self.assertEqual(envio.destinatario, 'destino@proveedor.cl')
        self.assertEqual(envio.objeto_id, 7)
        self.assertEqual(len(envio.token), 32)
        self.assertIsNotNone(envio.enviado_en)
        self.assertEqual(envio.enviado_por, self.user)

    def test_el_pixel_se_inserta_dentro_del_body(self):
        envio = enviar_correo_trazado(
            modulo='OTRO', asunto='x', texto='x',
            destinatario='d@p.cl',
            html='<html><body><p>hola</p></body></html>',
        )
        from django.core import mail
        cuerpo_html = mail.outbox[0].alternatives[0][0]
        self.assertIn(f'/app/c/a/{envio.token}.png', cuerpo_html)
        # Debe quedar ANTES del cierre del body, no pegado después.
        self.assertTrue(cuerpo_html.rstrip().endswith('</body></html>'))

    def test_sin_base_url_no_hay_pixel_pero_el_correo_sale(self):
        with override_settings(CORREO_BASE_URL=''):
            enviar_correo_trazado(
                modulo='OTRO', asunto='x', texto='x', destinatario='d@p.cl',
                html='<html><body>hola</body></html>',
            )
        from django.core import mail
        self.assertNotIn('/app/c/a/', mail.outbox[0].alternatives[0][0])

    @override_settings(CORREO_BUZON_RESPUESTAS='requerimientos@empresa.cl')
    def test_reply_to_lleva_el_token_primero(self):
        envio = enviar_correo_trazado(
            modulo='REQUERIMIENTO', asunto='x', texto='x',
            destinatario='d@p.cl', reply_to=['humano@empresa.cl'],
        )
        from django.core import mail
        esperado = f'requerimientos+{envio.token}@empresa.cl'
        self.assertEqual(mail.outbox[0].reply_to[0], esperado)
        self.assertIn('humano@empresa.cl', mail.outbox[0].reply_to)

    def test_sin_buzon_configurado_no_se_inventa_direccion(self):
        with override_settings(CORREO_BUZON_RESPUESTAS=''):
            self.assertEqual(direccion_respuesta('abc123'), '')

    def test_relay_que_no_acepta_deja_el_envio_como_fallido(self):
        # El backend devuelve 0 sin lanzar excepción: antes eso pasaba como
        # envío exitoso y el sistema mostraba un correo que nunca salió.
        with override_settings(
            EMAIL_BACKEND='app.tests.test_correo_seguimiento.BackendQueNoAcepta'
        ):
            with self.assertRaises(CorreoError):
                enviar_correo_trazado(
                    modulo='REQUERIMIENTO', objeto_id=3, asunto='x',
                    texto='x', destinatario='rechazado@p.cl',
                )
        envio = EnvioCorreo.objects.get(destinatario='rechazado@p.cl')
        self.assertEqual(envio.estado, 'FALLIDO')
        self.assertIsNone(envio.enviado_en)
        self.assertIn('no aceptó', envio.error)

    def test_excepcion_del_relay_tambien_queda_registrada(self):
        with override_settings(
            EMAIL_BACKEND='app.tests.test_correo_seguimiento.BackendQueRevienta'
        ):
            with self.assertRaises(CorreoError):
                enviar_correo_trazado(
                    modulo='REQUERIMIENTO', asunto='x', texto='x',
                    destinatario='explota@p.cl',
                )
        envio = EnvioCorreo.objects.get(destinatario='explota@p.cl')
        self.assertEqual(envio.estado, 'FALLIDO')
        self.assertIn('buzón lleno', envio.error)


class PrioridadEstadoTest(TestCase):
    """Los eventos llegan desordenados; el estado no puede retroceder."""

    def _envio(self, estado='ENVIADO'):
        return EnvioCorreo.objects.create(
            modulo='OTRO', destinatario='x@y.cl', estado=estado)

    def test_avanza_hacia_adelante(self):
        envio = self._envio()
        self.assertTrue(envio.registrar_estado('ENTREGADO'))
        self.assertEqual(envio.estado, 'ENTREGADO')

    def test_un_delivered_atrasado_no_pisa_un_opened(self):
        envio = self._envio('ABIERTO')
        self.assertFalse(envio.registrar_estado('ENTREGADO'))
        self.assertEqual(envio.estado, 'ABIERTO')

    def test_un_rebote_manda_sobre_todo(self):
        envio = self._envio('ABIERTO')
        self.assertTrue(envio.registrar_estado('REBOTADO', detalle='no existe'))
        self.assertEqual(envio.estado, 'REBOTADO')
        self.assertEqual(envio.estado_detalle, 'no existe')

    def test_evento_desconocido_no_cambia_nada(self):
        envio = self._envio('ENTREGADO')
        self.assertFalse(envio.registrar_estado('INVENTADO'))
        self.assertEqual(envio.estado, 'ENTREGADO')


@override_settings(CORREO_BASE_URL='https://retail.test.cl')
class PixelAperturaTest(TestCase):

    def setUp(self):
        self.envio = EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=1, destinatario='p@prov.cl')
        self.url = f'/app/c/a/{self.envio.token}.png'

    def test_devuelve_un_gif_y_cuenta_la_apertura(self):
        r = self.client.get(self.url, HTTP_USER_AGENT='Mozilla/5.0 Prueba')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/gif')
        self.assertIn('no-store', r['Cache-Control'])
        self.envio.refresh_from_db()
        self.assertEqual(self.envio.aperturas, 1)
        self.assertEqual(self.envio.estado, 'ABIERTO')
        self.assertIsNotNone(self.envio.abierto_en)
        self.assertIn('Prueba', self.envio.ultimo_user_agent)

    def test_segunda_apertura_suma_pero_no_mueve_la_primera_fecha(self):
        self.client.get(self.url)
        self.envio.refresh_from_db()
        primera = self.envio.abierto_en
        self.client.get(self.url)
        self.envio.refresh_from_db()
        self.assertEqual(self.envio.aperturas, 2)
        self.assertEqual(self.envio.abierto_en, primera)

    def test_token_inexistente_igual_devuelve_imagen(self):
        # Si devolviera 404, el cliente de correo mostraría un ícono roto.
        r = self.client.get('/app/c/a/' + ('0' * 32) + '.png')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'image/gif')


@override_settings(CORREO_BASE_URL='https://retail.test.cl')
class WebhookCorreoTest(TestCase):

    def setUp(self):
        self.admin = crear_usuario(username='wh', rol='administrador')
        self.empresa = crear_empresa(nombre='Empresa WH')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='NICKWH')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        self.proveedor = crear_empresa(
            nombre='Prov WH', rut='77.222.222-2', esProveedor=True,
            correoVendedor='ventas@provwh.cl')
        self.req = Requerimiento.objects.create(
            tipo='PRODUCTO_FALLADO', sucursal=self.sucursal,
            usuario_creador=self.admin, sku='999',
            nombre_producto='Producto WH', cliente_nombre='C',
            motivo='falla', proveedor=self.proveedor,
        )
        self.envio = EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='ventas@provwh.cl',
            proveedor_message_id='abc123relay',
        )
        self.url = reverse('webhook_correo')

    def _post(self, payload, secret=WEBHOOK_SECRET, firma=None):
        cuerpo = json.dumps(payload).encode('utf-8')
        if firma is None:
            firma = hmac.new(secret.encode(), cuerpo, hashlib.sha256).hexdigest()
        return self.client.post(self.url, data=cuerpo,
                                content_type='application/json',
                                HTTP_SIGNATURE=firma)

    def test_get_responde_ok_para_la_verificacion_de_url(self):
        # El proveedor comprueba la URL ANTES de entregar el secret de firma.
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['success'])

    @override_settings()
    def test_sin_firma_valida_no_escribe_nada(self):
        import os
        os.environ['CORREO_WEBHOOK_SECRET'] = WEBHOOK_SECRET
        try:
            r = self._post({'type': 'activity.delivered',
                            'data': {'email': {'message_id': 'abc123relay'}}},
                           firma='firma-falsa')
            self.assertEqual(r.status_code, 401)
            self.envio.refresh_from_db()
            self.assertEqual(self.envio.estado, 'ENVIADO')
        finally:
            os.environ.pop('CORREO_WEBHOOK_SECRET', None)

    def test_evento_delivered_actualiza_por_id_del_relay(self):
        import os
        os.environ['CORREO_WEBHOOK_SECRET'] = WEBHOOK_SECRET
        try:
            r = self._post({
                'type': 'activity.delivered',
                'data': {'email': {'message_id': 'abc123relay',
                                   'recipient': {'email': 'ventas@provwh.cl'}}},
            })
            self.assertEqual(r.status_code, 200)
            self.envio.refresh_from_db()
            self.assertEqual(self.envio.estado, 'ENTREGADO')
        finally:
            os.environ.pop('CORREO_WEBHOOK_SECRET', None)

    def test_rebote_queda_anotado_en_el_historial_del_requerimiento(self):
        import os
        os.environ['CORREO_WEBHOOK_SECRET'] = WEBHOOK_SECRET
        try:
            payload = {
                'type': 'activity.hard_bounced',
                'data': {'email': {'message_id': 'abc123relay',
                                   'reason': 'mailbox does not exist'}},
            }
            self._post(payload)
            self.envio.refresh_from_db()
            self.assertEqual(self.envio.estado, 'REBOTADO')
            historial = HistorialRequerimiento.objects.filter(
                requerimiento=self.req, accion='PROBLEMA_ENTREGA_CORREO')
            self.assertEqual(historial.count(), 1)
            self.assertIn('mailbox does not exist', historial.first().comentario)

            # El proveedor reintenta el mismo evento: no debe duplicar la anotación.
            self._post(payload)
            self.assertEqual(HistorialRequerimiento.objects.filter(
                requerimiento=self.req, accion='PROBLEMA_ENTREGA_CORREO').count(), 1)
        finally:
            os.environ.pop('CORREO_WEBHOOK_SECRET', None)

    def test_evento_que_no_interesa_responde_200_sin_tocar_nada(self):
        import os
        os.environ['CORREO_WEBHOOK_SECRET'] = WEBHOOK_SECRET
        try:
            r = self._post({'type': 'activity.queued', 'data': {}})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()['ignorado'])
            self.envio.refresh_from_db()
            self.assertEqual(self.envio.estado, 'ENVIADO')
        finally:
            os.environ.pop('CORREO_WEBHOOK_SECRET', None)


# ===== Backends de mentira para los casos de fallo =====

from django.core.mail.backends.base import BaseEmailBackend  # noqa: E402


class BackendQueNoAcepta(BaseEmailBackend):
    """Acepta la conexión pero rechaza el mensaje sin lanzar excepción."""

    def send_messages(self, email_messages):
        return 0


class BackendQueRevienta(BaseEmailBackend):
    """El relay corta la conexión a mitad del envío."""

    def send_messages(self, email_messages):
        raise OSError('buzón lleno')


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sistema@test.cl',
)
class SeguimientoEnLaFichaTest(TestCase):
    """Lo que la ficha y el listado muestran del estado de entrega."""

    def setUp(self):
        self.admin = crear_usuario(
            username='seg', rol='administrador', email='seg@test.cl')
        self.empresa = crear_empresa(nombre='Empresa Seg')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='NICKSEG')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        self.proveedor = crear_empresa(
            nombre='Prov Seg', rut='77.333.333-3', esProveedor=True,
            correoVendedor='ventas@provseg.cl')
        self.req = Requerimiento.objects.create(
            tipo='PRODUCTO_FALLADO', sucursal=self.sucursal,
            usuario_creador=self.admin, sku='555',
            nombre_producto='Producto Seg', cliente_nombre='C',
            motivo='falla', proveedor=self.proveedor,
        )
        self.client.force_login(self.admin)

    def _detalle(self):
        r = self.client.get(
            reverse('api_detalle_requerimiento', args=[self.req.id]))
        return r.json()['requerimiento']

    def test_caso_sin_envio_no_inventa_seguimiento(self):
        self.assertIsNone(self._detalle()['seguimiento_correo'])

    def test_la_copia_de_control_no_se_confunde_con_el_correo_al_proveedor(self):
        # La copia interna viaja después y a otra dirección: si se tomara como
        # "el correo del caso", la ficha mostraría el estado equivocado.
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='ventas@provseg.cl', estado='ENTREGADO')
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='control@empresa.cl', estado='ABIERTO',
            es_copia_control=True)
        seg = self._detalle()['seguimiento_correo']
        self.assertEqual(seg['destinatario'], 'ventas@provseg.cl')
        self.assertEqual(seg['estado'], 'ENTREGADO')

    def test_rebote_se_marca_como_problema(self):
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='malo@provseg.cl', estado='REBOTADO',
            estado_detalle='mailbox does not exist')
        seg = self._detalle()['seguimiento_correo']
        self.assertTrue(seg['hay_problema'])
        self.assertFalse(seg['llego'])
        self.assertIn('mailbox', seg['detalle'])

    def test_apertura_se_marca_como_indicativa(self):
        from django.utils import timezone as tz
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='ventas@provseg.cl', estado='ABIERTO',
            aperturas=2, abierto_en=tz.now(), enviado_en=tz.now())
        seg = self._detalle()['seguimiento_correo']
        self.assertTrue(seg['es_indicativo'])
        # La línea de tiempo solo dibuja hitos que ocurrieron de verdad.
        titulos = [h['titulo'] for h in seg['linea_tiempo']]
        self.assertIn('Enviado', titulos)
        self.assertIn('Abierto (2 veces)', titulos)
        self.assertNotIn('Entregado en el buzón', titulos)

    def test_filtro_de_correo_con_problema(self):
        otro = Requerimiento.objects.create(
            tipo='GARANTIA', sucursal=self.sucursal, usuario_creador=self.admin,
            sku='556', nombre_producto='Otro', cliente_nombre='C',
            motivo='x', proveedor=self.proveedor)
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='ok@provseg.cl', estado='ENTREGADO')
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=otro.id,
            destinatario='malo@provseg.cl', estado='REBOTADO')

        r = self.client.get(reverse('api_listar_requerimientos'),
                            {'correo': 'problema'})
        ids = [x['id'] for x in r.json()['requerimientos']]
        self.assertEqual(ids, [otro.id])

    def test_el_badge_del_listado_trae_el_estado(self):
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=self.req.id,
            destinatario='ventas@provseg.cl', estado='REBOTADO')
        r = self.client.get(reverse('api_listar_requerimientos'))
        fila = next(x for x in r.json()['requerimientos'] if x['id'] == self.req.id)
        self.assertTrue(fila['correo_estado']['hay_problema'])
        self.assertIn('REBOT', fila['correo_estado']['etiqueta'])


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='sistema@test.cl',
)
class RecordatoriosAutomaticosTest(TestCase):
    """El comando que insiste solo, y a quién NO le insiste."""

    def setUp(self):
        from django.utils import timezone as tz
        from datetime import timedelta as td
        self.admin = crear_usuario(username='rec', rol='administrador')
        self.empresa = crear_empresa(nombre='Empresa Rec')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='NICKREC')
        crear_empresa_user(self.admin, self.empresa, self.sucursal)
        self.proveedor = crear_empresa(
            nombre='Prov Rec', rut='77.444.444-4', esProveedor=True,
            correoVendedor='ventas@provrec.cl')
        self.hace_20_dias = tz.now() - td(days=20)

    def _req(self, sku, correo, intentos_envio=1, **extra):
        req = Requerimiento.objects.create(
            tipo='PRODUCTO_FALLADO', sucursal=self.sucursal,
            usuario_creador=self.admin, sku=sku,
            nombre_producto='P', cliente_nombre='C', motivo='falla',
            proveedor=self.proveedor, estado='ESPERANDO_RESPUESTA',
            correo_enviado_proveedor=True, correo_proveedor_destino=correo,
            intentos_envio=intentos_envio, **extra)
        Requerimiento.objects.filter(pk=req.pk).update(
            fecha_envio_proveedor=self.hace_20_dias)
        req.refresh_from_db()
        return req

    def _correr(self, *args):
        from io import StringIO
        from django.core.management import call_command
        salida = StringIO()
        call_command('enviar_recordatorios_requerimientos', *args, stdout=salida)
        return salida.getvalue()

    def test_por_defecto_solo_simula(self):
        from django.core import mail
        self._req('900', 'ventas@provrec.cl')
        salida = self._correr()
        self.assertIn('SIMULACIÓN', salida)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_le_insiste_a_un_correo_que_reboto(self):
        # Mandarle recordatorios a una dirección inexistente no sirve de nada:
        # lo que hay que hacer es corregir la ficha del proveedor.
        req = self._req('901', 'malo@provrec.cl')
        EnvioCorreo.objects.create(
            modulo='REQUERIMIENTO', objeto_id=req.id,
            destinatario='malo@provrec.cl', estado='REBOTADO',
            estado_detalle='mailbox does not exist')
        salida = self._correr()
        self.assertIn('correo rebotado (NO se insiste) 1', salida)
        self.assertIn('CORREOS QUE NUNCA LLEGARON', salida)
        self.assertIn('se les reenvía ahora ......... 0', salida)

    def test_envia_de_verdad_con_la_bandera(self):
        from django.core import mail
        req = self._req('902', 'ventas@provrec.cl')
        salida = self._correr('--enviar')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('RECORDATORIO', mail.outbox[0].subject)
        req.refresh_from_db()
        self.assertEqual(req.intentos_envio, 2)
        self.assertIsNotNone(req.ultimo_recordatorio)
        self.assertTrue(HistorialRequerimiento.objects.filter(
            requerimiento=req, accion='RECORDATORIO_AUTOMATICO').exists())
        self.assertIn('Recordatorios enviados: 1', salida)

    def test_no_repite_el_recordatorio_al_dia_siguiente(self):
        from django.core import mail
        self._req('903', 'ventas@provrec.cl')
        self._correr('--enviar')
        mail.outbox.clear()
        salida = self._correr('--enviar')   # segunda corrida del cron
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('recordatorio muy reciente .... 1', salida)

    def test_deja_de_insistir_despues_del_tope(self):
        self._req('904', 'ventas@provrec.cl', intentos_envio=9)
        salida = self._correr()
        self.assertIn('veces ...... 1', salida)
        self.assertIn('conviene llamar', salida)
