"""Tests de «Liberar máquina» (órdenes encoladas en un terminal Point).

Mercado Pago admite UNA operación encolada por terminal. Estas pruebas cubren
que ordenes_en_terminal pregunta a MP por cada orden Point de la cuenta,
filtra por terminal y por status vivo, que liberar_terminal cancela SOLO las
`created` (las at_terminal se cancelan EN la máquina), que una orden PAGADA
en MP se refleja como APROBADA, y los gates de las vistas (solo admin;
reasignar bloqueado con cobro en curso).

Sin red: toda llamada a la API de MP se mockea (requests.request).
"""
import datetime as _dt
from unittest import mock

import requests

from django.test import TestCase
from django.utils import timezone

from app.models import MercadoPagoConfig, MercadoPagoCuenta, TransaccionMercadoPago
from app.services import mercadopago_service as mp
from app.tests.factories import crear_empresa, crear_sucursal, crear_usuario
from app.views_mercadopago import _pendiente_en_terminal

ENV_TEST = {'MP_TOKEN_TEST': 'token-de-prueba'}
DEVICE = 'N950NCD400023750'
OTRO_DEVICE = 'N950NCD400099999'


def _envejecer(trx, segundos=120):
    """Mueve creado_en hacia atrás (auto_now_add no admite asignación directa).
    Una orden con menos de MP_RESERVA_MAX_SEG de vida nunca se cancela
    (cinturón contra matar un cobro que recién va a la pantalla)."""
    TransaccionMercadoPago.objects.filter(pk=trx.pk).update(
        creado_en=timezone.now() - _dt.timedelta(seconds=segundos))
    trx.refresh_from_db()
    return trx


def _config(sucursal, **kwargs):
    defaults = dict(
        habilitado=True,
        modo='POINT',
        token_env='MP_TOKEN_TEST',
        external_pos_id='POS001',
        device_id=DEVICE,
    )
    defaults.update(kwargs)
    return MercadoPagoConfig.objects.create(sucursal=sucursal, **defaults)


def _transaccion(config, correlativo, monto=10000, canal='POINT', estado='PENDIENTE',
                 order_id=None, device_rm=None, **kwargs):
    defaults = dict(
        sucursal_id=config.sucursal_id,
        correlativo_ticket=str(correlativo),
        tipo='VENTA',
        canal=canal,
        external_reference=f'RM-{config.sucursal_id}-{correlativo}-c{config.id}i01',
        order_id=f'ORD-{correlativo}' if order_id is None else order_id,
        monto=monto,
        estado=estado,
    )
    if device_rm:
        defaults['raw_response'] = {'_rm': {'fase': 'RESUELTA', 'device_id': device_rm}}
    defaults.update(kwargs)
    return TransaccionMercadoPago.objects.create(config=config, **defaults)


def _resp(status_code, payload):
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _orden(status, terminal=DEVICE, con_pago=False, sin_config=False):
    """Respuesta de GET /v1/orders/{id} tal como la documenta la Orders API:
    config.point.terminal_id + transactions.payments[]."""
    payload = {'id': 'X', 'type': 'point', 'status': status, 'status_detail': status}
    if not sin_config:
        payload['config'] = {'point': {'terminal_id': terminal, 'print_on_terminal': 'no_ticket'}}
    if con_pago:
        payload['transactions'] = {'payments': [{
            'id': 'PAY01ABC', 'status': 'processed', 'amount': '10000',
            'payment_method': {'id': 'debit_card', 'type': 'debit_card'},
        }]}
    return _resp(200, payload)


def _api_falsa(ordenes, cancel=None):
    """side_effect para requests.request: GET /v1/orders/{id} → ordenes[id];
    POST /v1/orders/{id}/cancel → cancel(id) o 200."""
    def fake(metodo, url, **kwargs):
        if metodo == 'POST' and url.endswith('/cancel'):
            oid = url.rsplit('/', 2)[-2]
            return cancel(oid) if cancel else _resp(200, {'id': oid, 'status': 'canceled'})
        if metodo == 'GET':
            oid = url.rsplit('/', 1)[-1]
            return ordenes[oid]
        raise AssertionError(f'llamada inesperada {metodo} {url}')
    return fake


def _llamadas(m_req, metodo):
    return [c.args[1] for c in m_req.call_args_list if c.args[0] == metodo]


class BaseLiberarTest(TestCase):
    """El token de prueba se inyecta en setUp (un patch.dict de clase no
    alcanza a los métodos de las subclases)."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='PAO3')
        cls.otra_sucursal = crear_sucursal(empresa=cls.empresa, alias='PAO1')
        # Caja que HOY tiene la máquina.
        cls.config = _config(cls.sucursal, nombre='Caja principal')
        # Caja de otra sucursal de la misma cuenta que la tenía antes y la perdió.
        cls.config_vieja = _config(cls.otra_sucursal, nombre='Caja principal', device_id='')

    def setUp(self):
        super().setUp()
        env = mock.patch.dict('os.environ', ENV_TEST)
        env.start()
        self.addCleanup(env.stop)
        mp._breaker_registrar_exito()


class OrdenesEnTerminalTests(BaseLiberarTest):

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_filtra_por_terminal_y_status_no_final(self, m_req):
        viva = _transaccion(self.config, '1001')                              # PENDIENTE, at_terminal
        cerrada_local = _transaccion(self.config_vieja, '1002', estado='ERROR')  # otra caja, cerrada local
        otra_maquina = _transaccion(self.config, '1003')
        cancelada_mp = _transaccion(self.config, '1004')
        created = _transaccion(self.config, '1005', estado='CANCELADA')
        _transaccion(self.config, '1006', order_id='')                        # sin order_id: no se consulta
        _transaccion(self.config, '1007', canal='QR')                         # QR: no ocupa terminal
        por_rm = _transaccion(self.config, '1008', device_rm=OTRO_DEVICE)     # sin config en la respuesta
        desconocida = _transaccion(self.config, '1009')                       # sin config ni _rm

        m_req.side_effect = _api_falsa({
            'ORD-1001': _orden('at_terminal'),
            'ORD-1002': _orden('at_terminal'),
            'ORD-1003': _orden('created', terminal=OTRO_DEVICE),
            'ORD-1004': _orden('canceled'),
            'ORD-1005': _orden('created'),
            'ORD-1008': _orden('created', sin_config=True),
            'ORD-1009': _orden('created', sin_config=True),
        })
        informe = {}
        encontradas = mp.ordenes_en_terminal(self.config, dias=10, informe=informe)

        ids = {o['transaccion_id'] for o in encontradas}
        self.assertEqual(ids, {viva.id, cerrada_local.id, created.id, desconocida.id})
        self.assertNotIn(otra_maquina.id, ids)
        self.assertNotIn(cancelada_mp.id, ids)
        self.assertNotIn(por_rm.id, ids)
        por_id = {o['transaccion_id']: o for o in encontradas}
        self.assertFalse(por_id[viva.id]['cancelable'])            # at_terminal
        self.assertTrue(por_id[created.id]['cancelable'])          # created + terminal coincide
        self.assertEqual(por_id[cerrada_local.id]['estado_local'], 'ERROR')
        self.assertEqual(por_id[cerrada_local.id]['sucursal'], 'PAO1')
        self.assertEqual(por_id[desconocida.id]['terminal_id'], 'desconocido')
        self.assertFalse(por_id[desconocida.id]['terminal_coincide'])
        self.assertFalse(por_id[desconocida.id]['cancelable'])
        # Se consultaron solo las POINT con order_id (6: por_rm no se consulta,
        # sus metadatos ya dicen que fue a OTRA máquina) y ninguna se canceló.
        self.assertEqual(len(_llamadas(m_req, 'GET')), 6)
        self.assertNotIn('ORD-1008', [u.rsplit('/', 1)[-1] for u in _llamadas(m_req, 'GET')])
        self.assertEqual(informe['total_candidatas'], 6)
        self.assertEqual(informe['omitidas'], 0)
        self.assertEqual(informe['consultadas'], 6)
        self.assertEqual(_llamadas(m_req, 'POST'), [])
        self.assertEqual(informe['errores'], [])
        # Más nueva primero.
        self.assertEqual(encontradas[0]['transaccion_id'], desconocida.id)

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_otra_cuenta_no_se_consulta(self, m_req):
        ajena = crear_sucursal(empresa=crear_empresa(nombre='Otra', rut='77.000.000-1'), alias='NICK1')
        config_ajena = _config(ajena, token_env='MP_TOKEN_TEST')
        _transaccion(config_ajena, '2001')
        propia = _transaccion(self.config, '2002')
        m_req.side_effect = _api_falsa({'ORD-2002': _orden('created')})
        encontradas = mp.ordenes_en_terminal(self.config)
        self.assertEqual([o['transaccion_id'] for o in encontradas], [propia.id])
        self.assertEqual(len(_llamadas(m_req, 'GET')), 1)

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_error_de_red_en_una_no_corta_el_barrido(self, m_req):
        t1 = _transaccion(self.config, '3001')
        t2 = _transaccion(self.config, '3002')
        ordenes = {'ORD-3001': _orden('created')}

        def fake(metodo, url, **kwargs):
            oid = url.rsplit('/', 1)[-1]
            if oid == 'ORD-3002':
                raise requests.exceptions.ReadTimeout('read timeout')
            return ordenes[oid]
        m_req.side_effect = fake
        informe = {}
        encontradas = mp.ordenes_en_terminal(self.config, informe=informe)
        self.assertEqual([o['transaccion_id'] for o in encontradas], [t1.id])
        self.assertEqual(len(informe['errores']), 1)
        self.assertEqual(informe['errores'][0]['transaccion_id'], t2.id)

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_processed_marca_aprobada_y_va_a_pagadas(self, m_req):
        trx = _transaccion(self.config, '4001', estado='ERROR')
        m_req.side_effect = _api_falsa({'ORD-4001': _orden('processed', con_pago=True)})
        informe = {}
        encontradas = mp.ordenes_en_terminal(self.config, informe=informe)
        self.assertEqual(encontradas, [])
        self.assertEqual([o['transaccion_id'] for o in informe['pagadas_detectadas']], [trx.id])
        self.assertEqual(informe['pagadas_detectadas'][0]['estado_anterior'], 'ERROR')
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')
        self.assertEqual(trx.payment_id, 'PAY01ABC')
        self.assertEqual(trx.metodo_pago_mp, 'debit_card')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_final_local_no_se_reabre_a_pendiente(self, m_req):
        """Una fila cerrada que MP reporta viva se LISTA, pero su estado local
        no vuelve a PENDIENTE (esa transición está prohibida)."""
        trx = _transaccion(self.config, '5001', estado='EXPIRADA')
        m_req.side_effect = _api_falsa({'ORD-5001': _orden('at_terminal')})
        encontradas = mp.ordenes_en_terminal(self.config)
        self.assertEqual(len(encontradas), 1)
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'EXPIRADA')


class LiberarTerminalTests(BaseLiberarTest):

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_sin_aplicar_no_cancela(self, m_req):
        _transaccion(self.config, '6001')
        m_req.side_effect = _api_falsa({'ORD-6001': _orden('created')})
        informe = mp.liberar_terminal(self.config, aplicar=False)
        self.assertFalse(informe['aplicado'])
        self.assertEqual(informe['device_id'], DEVICE)
        self.assertEqual(len(informe['encontradas']), 1)
        self.assertTrue(informe['encontradas'][0]['cancelable'])
        self.assertEqual(informe['canceladas'], [])
        self.assertEqual(_llamadas(m_req, 'POST'), [])
        self.assertEqual(TransaccionMercadoPago.objects.get(correlativo_ticket='6001').estado, 'PENDIENTE')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_aplicar_cancela_solo_created(self, m_req):
        created = _envejecer(_transaccion(self.config, '7001', estado='ERROR'))
        en_pantalla = _transaccion(self.config, '7002')
        m_req.side_effect = _api_falsa({
            'ORD-7001': _orden('created'),
            'ORD-7002': _orden('at_terminal'),
        })
        usuario = crear_usuario(username='admin_lib', rol='administrador')
        informe = mp.liberar_terminal(self.config, aplicar=True, usuario=usuario)

        self.assertTrue(informe['aplicado'])
        self.assertEqual([o['transaccion_id'] for o in informe['canceladas']], [created.id])
        self.assertEqual([o['transaccion_id'] for o in informe['no_cancelables']], [en_pantalla.id])
        self.assertEqual(informe['errores'], [])
        posts = [c for c in m_req.call_args_list if c.args[0] == 'POST']
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].args[1].endswith('/v1/orders/ORD-7001/cancel'))
        self.assertEqual(posts[0].kwargs['headers']['X-Idempotency-Key'],
                         created.external_reference + '-cancel')
        created.refresh_from_db()
        en_pantalla.refresh_from_db()
        self.assertEqual(created.estado, 'CANCELADA')
        self.assertIn('admin_lib', created.estado_detalle)
        self.assertEqual(en_pantalla.estado, 'PENDIENTE')   # NO se toca

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_409_cannot_cancel_va_a_no_cancelables_y_no_cambia_local(self, m_req):
        trx = _envejecer(_transaccion(self.config, '8001'))
        m_req.side_effect = _api_falsa(
            {'ORD-8001': _orden('created')},
            cancel=lambda oid: _resp(409, {'errors': [{'code': 'cannot_cancel_order',
                                                       'message': 'order at terminal'}]}),
        )
        informe = mp.liberar_terminal(self.config, aplicar=True)
        self.assertEqual(informe['canceladas'], [])
        self.assertEqual([o['transaccion_id'] for o in informe['no_cancelables']], [trx.id])
        self.assertIn('motivo', informe['no_cancelables'][0])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_aplicar_con_processed_lo_marca_aprobada(self, m_req):
        trx = _transaccion(self.config, '9001', estado='CANCELADA')
        m_req.side_effect = _api_falsa({'ORD-9001': _orden('processed', con_pago=True)})
        informe = mp.liberar_terminal(self.config, aplicar=True)
        self.assertEqual([o['transaccion_id'] for o in informe['pagadas_detectadas']], [trx.id])
        self.assertEqual(informe['canceladas'], [])
        self.assertEqual(_llamadas(m_req, 'POST'), [])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')

    def test_sin_device_id_falla(self):
        with self.assertRaises(mp.MercadoPagoError):
            mp.liberar_terminal(self.config_vieja)


class DeviceIdEnMetadatosTests(BaseLiberarTest):

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_crear_orden_point_recuerda_la_maquina(self, m_req):
        m_req.return_value = _resp(201, {'id': 'ORD-NEW', 'status': 'created'})
        trx, _ = mp.crear_orden(self.config, '10001', 5000, canal='POINT')
        self.assertEqual(mp._rm(trx)['device_id'], DEVICE)
        # Y sobrevive a la transición de estado (raw pisado por la respuesta).
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')
        self.assertEqual(mp._rm(trx)['device_id'], DEVICE)


class PendienteEnTerminalTests(BaseLiberarTest):

    def test_vacio_si_no_hay_nada(self):
        self.assertEqual(_pendiente_en_terminal(self.config), '')

    def test_cobro_vivo(self):
        _transaccion(self.config, '11001', monto=12990)
        msg = _pendiente_en_terminal(self.config)
        self.assertIn('Pendiente en esa máquina', msg)
        self.assertIn('del ticket 11001', msg)
        self.assertIn('$12.990', msg)
        self.assertIn('Liberar máquina', msg)

    def test_ultimo_enviado_aunque_este_cerrado(self):
        _transaccion(self.config, '11002', monto=5000, estado='ERROR')
        msg = _pendiente_en_terminal(self.config)
        self.assertIn('Lo último enviado a esa máquina', msg)
        self.assertIn('estado local ERROR', msg)
        self.assertIn('Liberar máquina', msg)


class VistaLiberarTerminalTests(BaseLiberarTest):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = crear_usuario(username='admin_mp', rol='administrador')
        cls.cajero = crear_usuario(username='cajero_mp', rol='cajero')

    URL = '/app/pos/mercadopago/gestion/terminal/liberar/'

    def test_cajero_403(self):
        self.client.force_login(self.cajero)
        resp = self.client.post(self.URL, {'config_id': self.config.id})
        self.assertEqual(resp.status_code, 403)

    def test_get_no_permitido(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 405)

    def test_caja_sin_maquina_400(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.URL, {'config_id': self.config_vieja.id})
        self.assertEqual(resp.status_code, 400)

    def test_falta_config_400(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.URL, {})
        self.assertEqual(resp.status_code, 400)

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_admin_solo_lectura(self, m_req):
        trx = _transaccion(self.config, '12001')
        m_req.side_effect = _api_falsa({'ORD-12001': _orden('created')})
        self.client.force_login(self.admin)
        resp = self.client.post(self.URL, {'config_id': self.config.id, 'aplicar': '0', 'dias': '99'})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['dias'], 30)          # tope
        self.assertFalse(data['aplicado'])
        self.assertEqual([o['transaccion_id'] for o in data['encontradas']], [trx.id])
        self.assertEqual(_llamadas(m_req, 'POST'), [])

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_admin_aplicar(self, m_req):
        trx = _envejecer(_transaccion(self.config, '12002'))
        m_req.side_effect = _api_falsa({'ORD-12002': _orden('created')})
        self.client.force_login(self.admin)
        resp = self.client.post(self.URL, {'config_id': self.config.id, 'aplicar': '1',
                                           'ids': f'{trx.id},abc,'})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual([o['transaccion_id'] for o in data['canceladas']], [trx.id])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'CANCELADA')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_admin_aplicar_sin_ids_no_cancela_nada(self, m_req):
        """Sin la lista de lo que el admin vio, no se cancela a ciegas."""
        trx = _envejecer(_transaccion(self.config, '12003'))
        m_req.side_effect = _api_falsa({'ORD-12003': _orden('created')})
        self.client.force_login(self.admin)
        resp = self.client.post(self.URL, {'config_id': self.config.id, 'aplicar': '1'})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data['canceladas'], [])
        self.assertEqual([o['transaccion_id'] for o in data['no_cancelables']], [trx.id])
        self.assertIn('vuelve a consultar', data['no_cancelables'][0]['motivo'])
        self.assertEqual(_llamadas(m_req, 'POST'), [])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')


class ReasignarBloqueadoTests(BaseLiberarTest):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = crear_usuario(username='admin_mp2', rol='administrador')
        cuenta = MercadoPagoCuenta(empresa=cls.empresa, mp_user_id='1', activo=True)
        cuenta.set_access_token('APP_USR-prueba')
        cuenta.save()
        cls.cuenta = cuenta

    URL = '/app/pos/mercadopago/gestion/devices/reasignar/'

    def _post(self):
        return self.client.post(self.URL, {
            'empresa_id': self.empresa.id,
            'device_id': DEVICE,
            'sucursal_id': self.otra_sucursal.id,
            'nombre': 'Point nueva',
        })

    def test_bloquea_con_cobro_en_curso_y_no_modifica_nada(self):
        _transaccion(self.config, '13001', monto=19990)
        self.client.force_login(self.admin)
        resp = self._post()
        self.assertEqual(resp.status_code, 400, resp.content)
        error = resp.json()['error']
        self.assertIn('PAO3 · Caja principal', error)
        self.assertIn('ticket 13001', error)
        self.assertIn('$19.990', error)
        self.assertIn('Liberar máquina', error)
        self.config.refresh_from_db()
        self.assertEqual(self.config.device_id, DEVICE)      # sigue con la máquina
        self.assertEqual(self.config.modo, 'POINT')
        self.assertFalse(MercadoPagoConfig.objects.filter(sucursal=self.otra_sucursal,
                                                          nombre='Point nueva').exists())

    def test_sin_cobro_en_curso_si_mueve(self):
        _transaccion(self.config, '13002', estado='ERROR')   # cerrada: no bloquea
        self.client.force_login(self.admin)
        resp = self._post()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.config.refresh_from_db()
        self.assertEqual(self.config.device_id, '')
        nueva = MercadoPagoConfig.objects.get(sucursal=self.otra_sucursal, nombre='Point nueva')
        self.assertEqual(nueva.device_id, DEVICE)

    def test_reserva_vencida_no_bloquea_y_se_cierra(self):
        """CREADA fase RESERVADA sin order_id y vieja: el worker murió antes del
        POST, nunca existió en MP. No bloquea y se cierra como ERROR."""
        muerta = _envejecer(_transaccion(self.config, '13003', estado='CREADA', order_id='',
                                         raw_response={'_rm': {'fase': 'RESERVADA'}}))
        self.client.force_login(self.admin)
        resp = self._post()
        self.assertEqual(resp.status_code, 200, resp.content)
        muerta.refresh_from_db()
        self.assertEqual(muerta.estado, 'ERROR')
        self.config.refresh_from_db()
        self.assertEqual(self.config.device_id, '')

    def test_reserva_fresca_e_incierta_siguen_bloqueando(self):
        _transaccion(self.config, '13004', estado='CREADA', order_id='',
                     raw_response={'_rm': {'fase': 'RESERVADA'}})     # fresca: cobro en curso
        self.client.force_login(self.admin)
        self.assertEqual(self._post().status_code, 400)
        TransaccionMercadoPago.objects.filter(correlativo_ticket='13004').delete()
        _envejecer(_transaccion(self.config, '13005', estado='CREADA', order_id='',
                                raw_response={'_rm': {'fase': 'ENVIADA'}}))  # incierta: PUEDE existir en MP
        self.assertEqual(self._post().status_code, 400)

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_expirada_en_mp_se_sincroniza_y_deja_de_bloquear(self, m_req):
        """Fila PENDIENTE que MP ya expiró: «Liberar máquina» la cierra local
        (no aparece en encontradas) y reasignar deja de dar 400."""
        trx = _transaccion(self.config, '13006')
        m_req.side_effect = _api_falsa({'ORD-13006': _orden('expired')})
        informe = mp.liberar_terminal(self.config, aplicar=False)
        self.assertEqual(informe['encontradas'], [])
        self.assertEqual([o['transaccion_id'] for o in informe['cerradas_local']], [trx.id])
        self.assertEqual(informe['cerradas_local'][0]['estado_anterior'], 'PENDIENTE')
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'EXPIRADA')
        self.assertIn('sincronizado al liberar terminal', trx.estado_detalle)
        self.client.force_login(self.admin)
        self.assertEqual(self._post().status_code, 200)


class GuardsDelBarridoTests(BaseLiberarTest):
    """Los efectos laterales del barrido solo van hacia estados más cerrados
    o hacia plata real; nunca reabren ni resucitan plata devuelta."""

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_devuelta_y_contracargo_no_vuelven_a_aprobada(self, m_req):
        devuelta = _transaccion(self.config, '14001', estado='DEVUELTA')
        disputada = _transaccion(self.config, '14002', estado='CONTRACARGO')
        m_req.side_effect = _api_falsa({
            'ORD-14001': _orden('processed', con_pago=True),
            'ORD-14002': _orden('processed', con_pago=True),
        })
        informe = {}
        encontradas = mp.ordenes_en_terminal(self.config, informe=informe)
        self.assertEqual(encontradas, [])
        self.assertEqual(informe['pagadas_detectadas'], [])
        devuelta.refresh_from_db()
        disputada.refresh_from_db()
        self.assertEqual(devuelta.estado, 'DEVUELTA')
        self.assertEqual(disputada.estado, 'CONTRACARGO')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_refunded_no_cierra_una_pendiente(self, m_req):
        """'refunded' es final en MP pero no se sincroniza a ciegas sobre una
        fila que nunca pasó por APROBADA."""
        trx = _transaccion(self.config, '14003')
        m_req.side_effect = _api_falsa({'ORD-14003': _orden('refunded')})
        informe = {}
        mp.ordenes_en_terminal(self.config, informe=informe)
        self.assertEqual(informe['cerradas_local'], [])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_cancel_202_no_cierra_local(self, m_req):
        """La Orders API responde 202 cuando la orden ya pasó a la pantalla:
        "aceptada, efectiva por notificación". No se marca CANCELADA local."""
        trx = _envejecer(_transaccion(self.config, '14004'))
        m_req.side_effect = _api_falsa(
            {'ORD-14004': _orden('created')},
            cancel=lambda oid: _resp(202, {'id': oid, 'status': 'at_terminal'}),
        )
        informe = mp.liberar_terminal(self.config, aplicar=True, solo_ids=[trx.id])
        self.assertEqual(informe['canceladas'], [])
        self.assertEqual([o['transaccion_id'] for o in informe['no_cancelables']], [trx.id])
        self.assertIn('notificación', informe['no_cancelables'][0]['motivo'])
        self.assertEqual(informe['errores'], [])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_cancel_2xx_con_status_canceled_si_cierra(self, m_req):
        trx = _envejecer(_transaccion(self.config, '14005'))
        m_req.side_effect = _api_falsa(
            {'ORD-14005': _orden('created')},
            cancel=lambda oid: _resp(202, {'id': oid, 'status': 'canceled'}),
        )
        informe = mp.liberar_terminal(self.config, aplicar=True, solo_ids=[trx.id])
        self.assertEqual([o['transaccion_id'] for o in informe['canceladas']], [trx.id])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'CANCELADA')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_solo_ids_no_cancela_lo_que_aparecio_despues(self, m_req):
        vista = _envejecer(_transaccion(self.config, '14006'))
        nueva = _envejecer(_transaccion(self.config, '14007'))   # no estaba en la tabla confirmada
        m_req.side_effect = _api_falsa({'ORD-14006': _orden('created'),
                                        'ORD-14007': _orden('created')})
        informe = mp.liberar_terminal(self.config, aplicar=True, solo_ids=[vista.id])
        self.assertEqual([o['transaccion_id'] for o in informe['canceladas']], [vista.id])
        self.assertEqual([o['transaccion_id'] for o in informe['no_cancelables']], [nueva.id])
        self.assertIn('Apareció después', informe['no_cancelables'][0]['motivo'])
        posts = _llamadas(m_req, 'POST')
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0].endswith('/ORD-14006/cancel'))
        nueva.refresh_from_db()
        self.assertEqual(nueva.estado, 'PENDIENTE')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_recien_enviada_no_se_cancela(self, m_req):
        """Menos de MP_RESERVA_MAX_SEG de vida: puede ser un cobro pasando a la
        pantalla en este instante."""
        trx = _transaccion(self.config, '14008')
        m_req.side_effect = _api_falsa({'ORD-14008': _orden('created')})
        informe = mp.liberar_terminal(self.config, aplicar=True, solo_ids=[trx.id])
        self.assertEqual(informe['canceladas'], [])
        self.assertIn('Recién enviada', informe['no_cancelables'][0]['motivo'])
        self.assertEqual(_llamadas(m_req, 'POST'), [])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')


class BarridoAcotadoTests(BaseLiberarTest):
    """El barrido es un diagnóstico de admin: no puede abrir el circuit
    breaker de las cajas ni pasarse del presupuesto de tiempo del worker."""

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_dos_timeouts_seguidos_cortan_sin_abrir_el_breaker(self, m_req):
        _transaccion(self.config, '15001')
        _transaccion(self.config, '15002')
        _transaccion(self.config, '15003')
        m_req.side_effect = requests.exceptions.ReadTimeout('read timeout')
        informe = {}
        encontradas = mp.ordenes_en_terminal(self.config, informe=informe)
        self.assertEqual(encontradas, [])
        self.assertEqual(len(_llamadas(m_req, 'GET')), mp.MP_BREAKER_FALLOS)   # la 3ª no se consulta
        self.assertFalse(mp._breaker_abierto())
        self.assertEqual(mp._mp_fallos_red, 0)                                # no sumó al breaker
        self.assertTrue(any('está lento' in e['error'] for e in informe['errores']))
        # Y una caja sigue pudiendo cobrar: _request no está bloqueado.
        m_req.side_effect = None
        m_req.return_value = _resp(200, {'id': 'X', 'status': 'created'})
        mp._request(self.config, 'GET', '/v1/orders/X')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_breaker_abierto_por_otros_corta_el_barrido(self, m_req):
        _transaccion(self.config, '15004')
        _transaccion(self.config, '15005')
        for _ in range(mp.MP_BREAKER_FALLOS):
            mp._breaker_registrar_fallo()
        self.addCleanup(mp._breaker_registrar_exito)
        informe = {}
        mp.ordenes_en_terminal(self.config, informe=informe)
        self.assertEqual(_llamadas(m_req, 'GET'), [])
        self.assertTrue(any('no responde' in e['error'] for e in informe['errores']))

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_presupuesto_agotado_corta_y_avisa(self, m_req):
        t1 = _transaccion(self.config, '15006')
        t2 = _transaccion(self.config, '15007')
        t3 = _transaccion(self.config, '15008')
        reloj = {'t': 1000.0}

        def monotonic():
            return reloj['t']

        def fake(metodo, url, **kwargs):
            reloj['t'] += 20.0          # cada GET "tarda" 20 s
            return _api_falsa({f'ORD-{c}': _orden('created') for c in ('15006', '15007', '15008')})(metodo, url, **kwargs)
        m_req.side_effect = fake
        with mock.patch('app.services.mercadopago_service.time.monotonic', monotonic):
            informe = mp.liberar_terminal(self.config, aplicar=False, presupuesto_seg=35)
        # 35 s: entra la 1ª (t=1000), la 2ª (t=1020, quedan 15), la 3ª no (t=1040).
        self.assertEqual(len(_llamadas(m_req, 'GET')), 2)
        self.assertEqual(informe['consultadas'], 2)
        self.assertEqual({o['transaccion_id'] for o in informe['encontradas']}, {t3.id, t2.id})
        self.assertNotIn(t1.id, {o['transaccion_id'] for o in informe['encontradas']})
        self.assertTrue(any('Se agotó el tiempo' in e['error'] and '2 de 3' in e['error']
                            for e in informe['errores']))

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_tope_prioriza_esta_maquina_y_cuenta_omitidas(self, m_req):
        # Más nueva primero a nivel global sería la de la caja SIN máquina;
        # con el tope 2 tienen que entrar las 2 de esta máquina.
        propia_vieja = _envejecer(_transaccion(self.config, '15009'), 300)
        propia_nueva = _envejecer(_transaccion(self.config, '15010'), 200)
        ajena = _envejecer(_transaccion(self.config_vieja, '15011'), 100)
        m_req.side_effect = _api_falsa({f'ORD-{c}': _orden('created') for c in ('15009', '15010', '15011')})
        informe = {}
        encontradas = mp.ordenes_en_terminal(self.config, max_ordenes=2, informe=informe)
        self.assertEqual([o['transaccion_id'] for o in encontradas], [propia_nueva.id, propia_vieja.id])
        self.assertEqual(informe['total_candidatas'], 3)
        self.assertEqual(informe['omitidas'], 1)
        self.assertNotIn('ORD-15011', [u.rsplit('/', 1)[-1] for u in _llamadas(m_req, 'GET')])
        self.assertIsNotNone(ajena.id)


class ExpirarManualTests(BaseLiberarTest):
    """«Marcar expirado en el sistema» (POST /app/pos/mercadopago/expirar/<id>/).

    Caso real (PAO4, 21-09): cobro Point de $3.333 PENDIENTE en la pantalla de
    la máquina; MP no lo deja cancelar por API y reiniciar la máquina no sirve.
    La fila se marca EXPIRADA a mano (deja de bloquear el ticket) sin tocar la
    orden en MP, y sigue siendo reversible si el cliente paga igual.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cajero = crear_usuario(username='cajero_exp', rol='cajero')
        cls.cajero_otra = crear_usuario(username='cajero_exp_otra', rol='cajero')

    def _login(self, usuario, sucursal):
        self.client.force_login(usuario)
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session.save()

    def _post(self, trx):
        return self.client.post(f'/app/pos/mercadopago/expirar/{trx.id}/')

    def test_pendiente_con_order_id_queda_expirada(self):
        trx = _transaccion(self.config, '16001', monto=3333)
        self._login(self.cajero, self.sucursal)
        with mock.patch.object(mp, 'consultar_estado', side_effect=lambda t, **k: t) as m_cons:
            resp = self._post(trx)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['estado'], 'EXPIRADA')
        m_cons.assert_called_once()
        self.assertTrue(m_cons.call_args.kwargs.get('forzar'))
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'EXPIRADA')
        self.assertIn('cajero_exp', trx.estado_detalle)
        self.assertIn('máquina', trx.estado_detalle)
        # Deja de contar como cobro vivo del ticket.
        self.assertEqual(mp.cobros_vivos_de_ticket(self.sucursal.id, '16001'), [])

    def test_si_mp_dice_aprobada_no_se_expira(self):
        trx = _transaccion(self.config, '16002', monto=3333)
        self._login(self.cajero, self.sucursal)
        aprobar = lambda t, **k: mp._aplicar_estado(t, 'APROBADA', payment={'id': '177000'})  # noqa: E731
        with mock.patch.object(mp, 'consultar_estado', side_effect=aprobar):
            resp = self._post(trx)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['estado'], 'APROBADA')
        self.assertIn('devolución', resp.json()['error'])
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')

    def test_aprobada_da_400_sin_consultar(self):
        trx = _transaccion(self.config, '16003', monto=3333, estado='APROBADA')
        self._login(self.cajero, self.sucursal)
        with mock.patch.object(mp, 'consultar_estado') as m_cons:
            resp = self._post(trx)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('devolución', resp.json()['error'])
        m_cons.assert_not_called()
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')

    def test_ya_final_responde_200_con_el_estado(self):
        trx = _transaccion(self.config, '16004', monto=3333, estado='CANCELADA')
        self._login(self.cajero, self.sucursal)
        resp = self._post(trx)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['estado'], 'CANCELADA')

    def test_incierta_sin_order_id_no_se_puede_expirar(self):
        """Protección del 13-09: sin order_id no se sabe si existe en MP."""
        trx = _transaccion(self.config, '16005', monto=3333, order_id='',
                           raw_response={'_rm': {'fase': mp.FASE_ENVIADA}})
        self.assertTrue(mp.es_incierta(trx))
        self._login(self.cajero, self.sucursal)
        with mock.patch.object(mp, 'consultar_estado') as m_cons:
            resp = self._post(trx)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Liberar máquina', resp.json()['error'])
        m_cons.assert_not_called()
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')

    def test_cajero_de_otra_sucursal_404(self):
        trx = _transaccion(self.config, '16006', monto=3333)
        self._login(self.cajero_otra, self.otra_sucursal)
        resp = self._post(trx)
        self.assertEqual(resp.status_code, 404)
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'PENDIENTE')

    def test_sin_red_igual_se_expira_con_lo_de_bd(self):
        trx = _transaccion(self.config, '16007', monto=3333)
        self._login(self.cajero, self.sucursal)
        with mock.patch.object(mp, 'consultar_estado', side_effect=RuntimeError('sin red')):
            resp = self._post(trx)
        self.assertEqual(resp.status_code, 200, resp.content)
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'EXPIRADA')

    def test_expirada_a_mano_revive_si_el_cliente_paga(self):
        """Como haría el webhook: FINAL→APROBADA está permitido."""
        trx = _transaccion(self.config, '16008', monto=3333)
        self._login(self.cajero, self.sucursal)
        with mock.patch.object(mp, 'consultar_estado', side_effect=lambda t, **k: t):
            self._post(trx)
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'EXPIRADA')
        trx = mp._aplicar_estado(trx, 'APROBADA', detalle='Pagada igual',
                                 payment={'id': '177422093000',
                                          'payment_method': {'id': 'debit_card', 'type': 'debit_card'}})
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')
        self.assertEqual(trx.payment_id_mp, '177422093000')
        self.assertEqual(len(mp.cobros_vivos_de_ticket(self.sucursal.id, '16008')), 1)
