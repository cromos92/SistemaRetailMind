"""Tests del cobro Mercado Pago presencial (QR vía Orders API).

Cubre: creación de orden (mock HTTP), firma de webhooks (manifest exacto,
timing-safe, anti-replay), idempotencia por x-request-id, guard server-side
de registrar_pagos_ticket (consumo de transacciones), transiciones de estado
y bucket propio en la cuadratura (separado del MP marketplace).

Sin red: toda llamada a la API de MP se mockea.
"""
import hashlib
import hmac
import time
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from app.models import (
    MercadoPagoConfig,
    MercadoPagoCuenta,
    MercadoPagoWebhookEvento,
    Ticket,
    TicketDetallePago,
    TransaccionMercadoPago,
)
from app.services import mercadopago_service as mp
from app.tests.factories import crear_empresa, crear_sucursal, crear_vendedor

ENV_TEST = {'MP_TOKEN_TEST': 'token-de-prueba', 'MP_SECRET_TEST': 'secreto-firma'}


def _config(sucursal, **kwargs):
    defaults = dict(
        habilitado=True,
        modo='QR',
        token_env='MP_TOKEN_TEST',
        webhook_secret_env='MP_SECRET_TEST',
        external_pos_id='POS001',
        external_store_id='SUC001',
    )
    defaults.update(kwargs)
    return MercadoPagoConfig.objects.create(sucursal=sucursal, **defaults)


def _transaccion(config, correlativo='100', monto=10000, estado='APROBADA', **kwargs):
    defaults = dict(
        sucursal_id=config.sucursal_id,
        correlativo_ticket=str(correlativo),
        tipo='VENTA',
        canal='QR',
        external_reference=f'RM-{config.sucursal_id}-{correlativo}-{estado[:4].lower()}{monto}',
        monto=monto,
        estado=estado,
    )
    defaults.update(kwargs)
    return TransaccionMercadoPago.objects.create(config=config, **defaults)


class BaseMPTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.config = _config(cls.sucursal)


# ==================== CREACIÓN DE ORDEN ====================

@mock.patch.dict('os.environ', ENV_TEST)
class CrearOrdenTests(BaseMPTest):

    def _mock_resp(self, status=201, payload=None):
        resp = mock.MagicMock()
        resp.status_code = status
        resp.json.return_value = payload if payload is not None else {
            'id': 'ORD-1', 'status': 'created',
            'type_response': {'qr_data': '00020101021243...'},
        }
        return resp

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_crear_orden_qr_ok(self, m_req):
        m_req.return_value = self._mock_resp()
        trx, qr = mp.crear_orden(self.config, '123', 15000)
        self.assertEqual(trx.estado, 'PENDIENTE')
        self.assertEqual(trx.order_id, 'ORD-1')
        self.assertEqual(trx.monto, 15000)
        self.assertEqual(trx.correlativo_ticket, '123')
        self.assertTrue(qr.startswith('000201'))
        # Idempotencia: el header viaja con el external_reference
        headers = m_req.call_args.kwargs['headers']
        self.assertEqual(headers['X-Idempotency-Key'], trx.external_reference)
        self.assertIn('Bearer token-de-prueba', headers['Authorization'])
        body = m_req.call_args.kwargs['json']
        self.assertEqual(body['type'], 'qr')
        # processing_mode NO va: la Orders API presencial lo rechaza con
        # unsupported_properties (comprobado contra producción CL)
        self.assertNotIn('processing_mode', body)
        self.assertEqual(body['config']['qr']['external_pos_id'], 'POS001')

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_orden_sin_qr_data_falla_sin_crear_transaccion(self, m_req):
        m_req.return_value = self._mock_resp(payload={'id': 'ORD-2', 'status': 'created'})
        with self.assertRaises(mp.MercadoPagoError):
            mp.crear_orden(self.config, '124', 5000)
        self.assertFalse(TransaccionMercadoPago.objects.filter(order_id='ORD-2').exists())

    def test_monto_invalido(self):
        with self.assertRaises(mp.MercadoPagoError):
            mp.crear_orden(self.config, '125', 0)

    def test_sin_token_env(self):
        config_malo = _config(self.sucursal, nombre='Otra', token_env='NO_EXISTE_ENV')
        with self.assertRaises(mp.MercadoPagoError):
            mp.crear_orden(config_malo, '126', 1000)

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_reintento_sin_propiedades_no_soportadas(self, m_req):
        """Si MP rechaza propiedades (unsupported_properties), se quitan y se
        reintenta una vez — el caso real fue expiration_time/description."""
        rechazo = mock.MagicMock()
        rechazo.status_code = 400
        rechazo.json.return_value = {
            'errors': [{'code': 'unsupported_properties',
                        'message': 'Properties not supported',
                        'details': ['expiration_time', 'description']}],
        }
        exito = self._mock_resp()
        m_req.side_effect = [rechazo, exito]
        trx, qr = mp.crear_orden(self.config, '127', 5000)
        self.assertEqual(trx.estado, 'PENDIENTE')
        self.assertEqual(m_req.call_count, 2)
        body_reintento = m_req.call_args.kwargs['json']
        self.assertNotIn('expiration_time', body_reintento)
        self.assertNotIn('description', body_reintento)
        self.assertIn('transactions', body_reintento)


# ==================== CREDENCIALES EN BD (CIFRADAS) ====================

class CredencialesEnBDTests(BaseMPTest):

    def test_cifrado_roundtrip(self):
        from app.services import mp_credenciales as cred
        cifrado = cred.cifrar('mi-token-secreto')
        self.assertTrue(cifrado.startswith('enc:'))
        self.assertNotIn('mi-token-secreto', cifrado)
        self.assertEqual(cred.descifrar(cifrado), 'mi-token-secreto')
        # Compatibilidad: texto plano legacy pasa tal cual; vacío es vacío
        self.assertEqual(cred.descifrar('texto-plano-legacy'), 'texto-plano-legacy')
        self.assertEqual(cred.descifrar(''), '')
        self.assertEqual(cred.cifrar(''), '')

    def test_token_desde_bd_gana_al_env(self):
        cuenta = MercadoPagoCuenta(empresa=self.empresa)
        cuenta.set_access_token('token-guardado-en-bd')
        cuenta.save()
        # El campo en BD queda cifrado, nunca en claro
        cuenta.refresh_from_db()
        self.assertTrue(cuenta.access_token_cifrado.startswith('enc:'))
        self.assertNotIn('token-guardado-en-bd', cuenta.access_token_cifrado)
        with mock.patch.dict('os.environ', ENV_TEST):
            self.assertEqual(mp._token(self.config), 'token-guardado-en-bd')

    def test_fallback_env_sin_cuenta(self):
        with mock.patch.dict('os.environ', ENV_TEST):
            self.assertEqual(mp._token(self.config), 'token-de-prueba')

    def test_cuenta_inactiva_usa_fallback(self):
        cuenta = MercadoPagoCuenta(empresa=self.empresa, activo=False)
        cuenta.set_access_token('token-inactivo')
        cuenta.save()
        with mock.patch.dict('os.environ', ENV_TEST):
            self.assertEqual(mp._token(self.config), 'token-de-prueba')

    def test_secret_webhook_desde_bd_valida_firma(self):
        cuenta = MercadoPagoCuenta(empresa=self.empresa)
        cuenta.set_webhook_secret('secreto-en-bd')
        cuenta.save()
        ts = str(int(time.time()))
        manifest = f'id:1;request-id:req-bd;ts:{ts};'
        v1 = hmac.new(b'secreto-en-bd', manifest.encode(), hashlib.sha256).hexdigest()
        headers = {'x-signature': f'ts={ts},v1={v1}', 'x-request-id': 'req-bd'}
        # Sin env vars: el secreto sale de la BD
        self.assertTrue(mp.validar_firma(headers, '1'))


# ==================== FIRMA DE WEBHOOK ====================

@mock.patch.dict('os.environ', ENV_TEST)
class FirmaWebhookTests(BaseMPTest):

    def _headers(self, data_id, secret='secreto-firma', ts=None, request_id='req-1'):
        ts = str(ts if ts is not None else int(time.time()))
        manifest = f'id:{str(data_id).lower()};request-id:{request_id};ts:{ts};'
        v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return {'x-signature': f'ts={ts},v1={v1}', 'x-request-id': request_id}

    def test_firma_valida(self):
        self.assertTrue(mp.validar_firma(self._headers('12345'), '12345'))

    def test_firma_con_secret_equivocado(self):
        headers = self._headers('12345', secret='otro-secreto')
        self.assertFalse(mp.validar_firma(headers, '12345'))

    def test_firma_sobre_otro_data_id(self):
        headers = self._headers('12345')
        self.assertFalse(mp.validar_firma(headers, '99999'))

    def test_replay_ts_viejo(self):
        viejo = int(time.time()) - 3600
        headers = self._headers('12345', ts=viejo)
        self.assertFalse(mp.validar_firma(headers, '12345'))

    def test_ts_en_milisegundos_vigente(self):
        ms = int(time.time() * 1000)
        headers = self._headers('12345', ts=ms)
        self.assertTrue(mp.validar_firma(headers, '12345'))

    def test_sin_headers(self):
        self.assertFalse(mp.validar_firma({}, '12345'))


# ==================== WEBHOOK: IDEMPOTENCIA ====================

@mock.patch.dict('os.environ', ENV_TEST)
class WebhookIdempotenciaTests(BaseMPTest):

    def _headers_validos(self, data_id, request_id):
        ts = str(int(time.time()))
        manifest = f'id:{str(data_id).lower()};request-id:{request_id};ts:{ts};'
        v1 = hmac.new(b'secreto-firma', manifest.encode(), hashlib.sha256).hexdigest()
        return {'x-signature': f'ts={ts},v1={v1}', 'x-request-id': request_id}

    @mock.patch('app.services.mercadopago_service._resolver_transaccion_por_payment',
                return_value=(None, None))
    def test_reentrega_no_reprocesa(self, m_resolver):
        headers = self._headers_validos('555', 'req-idem')
        mp.procesar_notificacion('req-idem', 'payment', '555', {}, headers)
        mp.procesar_notificacion('req-idem', 'payment', '555', {}, headers)
        self.assertEqual(
            MercadoPagoWebhookEvento.objects.filter(request_id='req-idem').count(), 1
        )
        self.assertEqual(m_resolver.call_count, 1)

    def test_firma_invalida_no_procesa(self):
        headers = {'x-signature': 'ts=1,v1=basura', 'x-request-id': 'req-mala'}
        evento = mp.procesar_notificacion('req-mala', 'payment', '555', {}, headers)
        self.assertFalse(evento.firma_valida)
        self.assertFalse(evento.procesado)

    def test_webhook_aprueba_transaccion(self):
        trx = _transaccion(self.config, correlativo='200', estado='PENDIENTE',
                           external_reference='RM-X-200-abc', order_id='ORD-9')
        payment = {
            'id': 777, 'status': 'approved', 'status_detail': 'accredited',
            'external_reference': 'RM-X-200-abc',
            'payment_type_id': 'debit_card', 'installments': 1,
            'transaction_details': {'net_received_amount': 9700},
            'card': {'last_four_digits': '1234'},
            'money_release_date': '2026-09-02T10:00:00.000-04:00',
        }
        with mock.patch(
            'app.services.mercadopago_service._resolver_transaccion_por_payment',
            return_value=(trx, payment),
        ):
            headers = self._headers_validos('777', 'req-aprueba')
            mp.procesar_notificacion('req-aprueba', 'payment', '777', {}, headers)
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')
        self.assertEqual(trx.payment_id, '777')
        self.assertEqual(trx.monto_neto, 9700)
        self.assertEqual(trx.fee_mp, trx.monto - 9700)
        self.assertEqual(trx.ultimos_4_digitos, '1234')
        self.assertIsNotNone(trx.money_release_date)
        self.assertIsNotNone(trx.webhook_recibido_en)


# ==================== TRANSICIONES DE ESTADO ====================

class TransicionEstadosTests(BaseMPTest):

    def test_aprobada_no_baja_a_rechazada(self):
        trx = _transaccion(self.config, estado='APROBADA')
        mp._aplicar_estado(trx, 'RECHAZADA', detalle='no debería')
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')

    def test_aprobada_si_pasa_a_devuelta(self):
        trx = _transaccion(self.config, estado='APROBADA')
        mp._aplicar_estado(trx, 'DEVUELTA', detalle='refund')
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'DEVUELTA')

    def test_final_no_vuelve_a_pendiente(self):
        trx = _transaccion(self.config, estado='EXPIRADA')
        mp._aplicar_estado(trx, 'PENDIENTE')
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'EXPIRADA')


# ==================== GUARD: CONSUMO DE TRANSACCIONES ====================

class GuardConsumoTests(BaseMPTest):

    def test_consume_y_no_permite_doble_uso(self):
        _transaccion(self.config, correlativo='300', monto=10000)
        consumida = mp.consumir_transaccion_aprobada(self.sucursal.id, '300', 10000)
        self.assertIsNotNone(consumida)
        self.assertTrue(consumida.consumida)
        # El mismo cobro no respalda un segundo pago
        self.assertIsNone(mp.consumir_transaccion_aprobada(self.sucursal.id, '300', 10000))

    def test_monto_insuficiente_no_respalda(self):
        _transaccion(self.config, correlativo='301', monto=5000)
        self.assertIsNone(mp.consumir_transaccion_aprobada(self.sucursal.id, '301', 9000))

    def test_pendiente_no_respalda(self):
        _transaccion(self.config, correlativo='302', monto=5000, estado='PENDIENTE')
        self.assertIsNone(mp.consumir_transaccion_aprobada(self.sucursal.id, '302', 5000))

    def test_otra_sucursal_no_respalda(self):
        otra = crear_sucursal(empresa=self.empresa, alias='OTRA-SUC')
        _transaccion(self.config, correlativo='303', monto=5000)
        self.assertIsNone(mp.consumir_transaccion_aprobada(otra.id, '303', 5000))


# ==================== CUADRATURA: BUCKET PROPIO ====================

class CuadraturaMPTests(BaseMPTest):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    def _ticket_pagado(self, correlativo, total):
        return Ticket.objects.create(
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            correlativo=correlativo,
            estado='PAGADO',
            subTotal=total,
            descuento=0,
            total=total,
            responsable='test-mp',
        )

    def test_mp_qr_cae_en_bucket_propio(self):
        from app.views_modulo_ventas import _calcular_cuadratura_data
        ticket = self._ticket_pagado(9001, 20000)
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_QR', monto=15000,
            tipo_tarjeta='debit_card', voucher='777', origen_pago='POS_INTEGRADO',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=5000,
        )
        hoy = timezone.localdate().strftime('%Y-%m-%d')
        data = _calcular_cuadratura_data(self.sucursal, hoy)
        self.assertEqual(data['total_mercadopago_pos'], 15000)
        self.assertEqual(data['total_efectivo'], 5000)
        # NO se mezcla con el MP marketplace ni con Transbank
        self.assertEqual(data['total_mercadopago'], 0)
        self.assertEqual(data['total_transbank'], 0)

    def test_mp_point_tambien_va_al_bucket(self):
        from app.views_modulo_ventas import _calcular_cuadratura_data
        ticket = self._ticket_pagado(9002, 8000)
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_POINT_CREDITO', monto=8000,
        )
        hoy = timezone.localdate().strftime('%Y-%m-%d')
        data = _calcular_cuadratura_data(self.sucursal, hoy)
        self.assertEqual(data['total_mercadopago_pos'], 8000)
        self.assertEqual(data['total_tarjeta_credito'], 0)

    def test_mapeo_teoricos_incluye_mp(self):
        from app.views_modulo_ventas import _MAPEO_TEORICOS_ARQUEO
        pares = dict(_MAPEO_TEORICOS_ARQUEO)
        self.assertEqual(
            pares.get('total_mercadopago_pos_teorico'), 'total_mercadopago_pos'
        )

    def test_categoria_es_tarjetas(self):
        from app.views_modulo_ventas import _categoria_metodo_pago
        for metodo in ('MP_QR', 'MP_POINT', 'MP_POINT_DEBITO', 'MP_POINT_CREDITO'):
            self.assertEqual(_categoria_metodo_pago(metodo), 'tarjetas')


# ==================== PESTAÑA DE GESTIÓN (render + endpoints) ====================

@mock.patch.dict('os.environ', ENV_TEST)
class GestionTabMPTests(BaseMPTest):
    """Smoke end-to-end de /app/pos/transbank/ (pestaña MP) y sus endpoints:
    el template renderiza de verdad y los gates de rol funcionan."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from app.tests.factories import crear_usuario
        cls.admin = crear_usuario(username='admin_mp', rol='administrador')
        cls.cajero = crear_usuario(username='cajero_mp', rol='cajero')

    def test_pagina_renderiza_para_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get('/app/pos/transbank/')
        self.assertEqual(resp.status_code, 200)
        contenido = resp.content.decode('utf-8')
        self.assertIn('tab-mp', contenido)
        self.assertIn('Buscar cajas creadas en Mercado Pago', contenido)
        self.assertIn('/app/pos/mercadopago/webhook/', contenido)
        self.assertIn('MP_ES_ADMIN = true', contenido)

    def test_pagina_renderiza_para_cajero_solo_lectura(self):
        self.client.force_login(self.cajero)
        resp = self.client.get('/app/pos/transbank/')
        self.assertEqual(resp.status_code, 200)
        contenido = resp.content.decode('utf-8')
        self.assertIn('MP_ES_ADMIN = false', contenido)
        self.assertIn('Solo lectura', contenido)
        self.assertNotIn('Buscar cajas creadas en Mercado Pago', contenido)

    def test_guardar_cuenta_admin_ok_y_cifrada(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/app/pos/mercadopago/gestion/cuenta/', {
            'empresa_id': self.empresa.id,
            'mp_user_id': '757112306794',
            'access_token': 'APP_USR-token-prueba',
            'webhook_secret': 'clave-firma',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        cuenta = MercadoPagoCuenta.objects.get(empresa=self.empresa)
        self.assertTrue(cuenta.access_token_cifrado.startswith('enc:'))
        self.assertEqual(cuenta.get_access_token(), 'APP_USR-token-prueba')
        self.assertEqual(cuenta.get_webhook_secret(), 'clave-firma')

    def test_guardar_cuenta_cajero_403(self):
        self.client.force_login(self.cajero)
        resp = self.client.post('/app/pos/mercadopago/gestion/cuenta/', {
            'empresa_id': self.empresa.id, 'access_token': 'x',
        })
        self.assertEqual(resp.status_code, 403)

    def test_guardar_config_admin_ok(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/app/pos/mercadopago/gestion/config/', {
            'sucursal_id': self.sucursal.id,
            'nombre': 'Caja test',
            'external_store_id': 'NICK2',
            'external_pos_id': 'NICK2CAJA1',
            'habilitado': '1',
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        cfg = MercadoPagoConfig.objects.get(sucursal=self.sucursal, nombre='Caja test')
        self.assertTrue(cfg.habilitado)
        self.assertTrue(cfg.es_principal)
        self.assertEqual(cfg.external_pos_id, 'NICK2CAJA1')

    def test_datos_endpoint(self):
        self.client.force_login(self.admin)
        resp = self.client.get('/app/pos/mercadopago/gestion/datos/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertIn('cuentas', data)
        self.assertIn('configs', data)


# ==================== REEMBOLSOS ====================

@mock.patch.dict('os.environ', ENV_TEST)
class ReembolsoTests(BaseMPTest):

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_refund_total(self, m_req):
        resp = mock.MagicMock()
        resp.status_code = 201
        resp.json.return_value = {'id': 888, 'amount': 10000}
        m_req.return_value = resp
        trx = _transaccion(self.config, correlativo='400', monto=10000,
                           payment_id='777')
        devolucion = mp.reembolsar(trx)
        self.assertEqual(devolucion.tipo, 'DEVOLUCION')
        self.assertEqual(devolucion.monto, 10000)
        self.assertEqual(devolucion.transaccion_origen_id, trx.id)
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'DEVUELTA')

    def test_refund_sin_aprobar_falla(self):
        trx = _transaccion(self.config, correlativo='401', monto=10000,
                           estado='PENDIENTE', payment_id='779')
        with self.assertRaises(mp.MercadoPagoError):
            mp.reembolsar(trx)

    @mock.patch('app.services.mercadopago_service.requests.request')
    def test_refund_parcial_no_marca_devuelta(self, m_req):
        resp = mock.MagicMock()
        resp.status_code = 201
        resp.json.return_value = {'id': 889, 'amount': 4000}
        m_req.return_value = resp
        trx = _transaccion(self.config, correlativo='402', monto=10000,
                           payment_id='780')
        mp.reembolsar(trx, monto=4000)
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')
