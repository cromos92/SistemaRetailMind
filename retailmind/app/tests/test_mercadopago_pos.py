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

    # ── Desglose débito / crédito / otros del MP presencial ──────────────

    def test_sub_bucket_mp_helper(self):
        from app.views_modulo_ventas import _sub_bucket_mp
        # El método Point ya trae el medio resuelto
        self.assertEqual(_sub_bucket_mp('MP_POINT_DEBITO', ''), 'debito')
        self.assertEqual(_sub_bucket_mp('MP_POINT_CREDITO', 'debit_card'), 'credito')
        # QR / Point genérico: se clasifica por el payment_type_id de MP
        # (el loop de tickets lo pasa en MAYÚSCULAS, el de DTE tal cual)
        self.assertEqual(_sub_bucket_mp('MP_QR', 'DEBIT_CARD'), 'debito')
        self.assertEqual(_sub_bucket_mp('MP_QR', 'credit_card'), 'credito')
        self.assertEqual(_sub_bucket_mp('MP_POINT', 'prepaid_card'), 'debito')
        self.assertEqual(_sub_bucket_mp('MP_QR', 'account_money'), 'otros')
        self.assertEqual(_sub_bucket_mp('MP_QR', 'MERCADO PAGO'), 'otros')
        self.assertEqual(_sub_bucket_mp('MP_QR', None), 'otros')

    def test_desglose_mp_debito_credito_otros(self):
        from app.views_modulo_ventas import _calcular_cuadratura_data
        ticket = self._ticket_pagado(9003, 50000)
        # QR pagado con débito (tipo_tarjeta = payment_type_id real)
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_QR', monto=10000,
            tipo_tarjeta='debit_card', origen_pago='POS_INTEGRADO',
        )
        # Point crédito (método ya resuelto por el POS)
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_POINT_CREDITO', monto=20000,
            tipo_tarjeta='credit_card', origen_pago='POS_INTEGRADO',
        )
        # Point prepago → débito (misma convención que TBK_PREPAGO_POS)
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_POINT', monto=5000,
            tipo_tarjeta='prepaid_card', origen_pago='POS_INTEGRADO',
        )
        # QR con dinero en cuenta → otros
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_QR', monto=15000,
            tipo_tarjeta='account_money', origen_pago='POS_INTEGRADO',
        )
        hoy = timezone.localdate().strftime('%Y-%m-%d')
        data = _calcular_cuadratura_data(self.sucursal, hoy)
        self.assertEqual(data['total_mercadopago_pos'], 50000)
        self.assertEqual(data['total_mercadopago_pos_debito'], 15000)
        self.assertEqual(data['total_mercadopago_pos_credito'], 20000)
        self.assertEqual(data['total_mercadopago_pos_otros'], 15000)
        # Invariante: el desglose siempre suma el bucket total
        self.assertEqual(
            data['total_mercadopago_pos_debito']
            + data['total_mercadopago_pos_credito']
            + data['total_mercadopago_pos_otros'],
            data['total_mercadopago_pos'],
        )
        # Sigue sin mezclarse con Transbank
        self.assertEqual(data['total_tarjeta_debito'], 0)
        self.assertEqual(data['total_tarjeta_credito'], 0)

    def test_nc_mp_resta_del_sub_bucket_del_medio_devuelto(self):
        """Una NC devuelta por la API MP (tipo_tarjeta = medio real) resta del
        sub-bucket correcto y el desglose sigue cuadrando con el total."""
        from decimal import Decimal
        from app.models import Dte, Dte_Detalle_Pago
        from app.views_modulo_ventas import _calcular_cuadratura_data
        ticket = self._ticket_pagado(9004, 30000)
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_POINT_CREDITO', monto=30000,
            tipo_tarjeta='credit_card', origen_pago='POS_INTEGRADO',
        )
        hoy_date = timezone.localdate()
        nc = Dte.objects.create(
            emisor=self.empresa,
            receptor=None,
            numero_documento=77001,
            tipo_documento='NOTA DE CREDITO',
            monto_con_iva=Decimal('12000'),
            monto_neto=Decimal('10084'),
            descuento=0,
            estado_pago='PAGADO',
            estado_dte='EMITIDO',
            responsable='test-mp',
            fecha_emision=hoy_date,
            fecha_vencimiento=hoy_date,
            diasCredito=0,
            bultos=0,
            unidades_productos=0,
            tipo_transaccion='DEVOLUCION',
            sucursal=self.sucursal,
            es_nota_credito=True,
            hora=timezone.localtime().time(),
        )
        # Igual que anular_factura_dte con MERCADOPAGO_API: MP_POINT +
        # tipo_tarjeta = medio devuelto por devolver_por_nc()
        Dte_Detalle_Pago.objects.create(
            dte=nc, metodo_pago='MP_POINT', tipo_tarjeta='credit_card',
            monto=12000, fecha_pago=hoy_date,
        )
        data = _calcular_cuadratura_data(self.sucursal, hoy_date.strftime('%Y-%m-%d'))
        self.assertEqual(data['total_nc_mercadopago_pos'], 12000)
        self.assertEqual(data['total_mercadopago_pos'], 18000)
        self.assertEqual(data['total_mercadopago_pos_credito'], 18000)
        self.assertEqual(data['total_mercadopago_pos_debito'], 0)
        self.assertEqual(data['total_mercadopago_pos_otros'], 0)


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

    def test_devolver_por_nc_informa_medio_del_cobro_devuelto(self):
        """`devolver_por_nc` expone el payment_type_id del cobro devuelto
        ('medio') para que la NC lo guarde en tipo_tarjeta y la cuadratura
        reste del sub-bucket MP correcto."""
        trx = _transaccion(self.config, correlativo='403', monto=10000,
                           payment_id='781', metodo_pago_mp='credit_card',
                           canal='POINT')
        dte_fake = mock.MagicMock(numero_documento=123)
        with mock.patch.object(mp, 'transacciones_mp_de_dte', return_value=[trx]), \
             mock.patch.object(mp, 'reembolsar', return_value=None) as m_ref:
            res = mp.devolver_por_nc(dte_fake, 4000)
        m_ref.assert_called_once()
        self.assertEqual(res['medio'], 'credit_card')
        self.assertEqual(res['canal'], 'POINT')
        self.assertEqual(res['total'], 4000)

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


# ==================== COBROS VIVOS / HUÉRFANOS ====================
# Caso NICK2 05-09-2026: el cobro se mandó a la Point, la máquina pidió
# REINTENTE, se cerró la ventana de espera y el cliente pagó igual; la venta
# se cerró con crédito manual. La plata entró por MP y la cuadratura la mostró
# en VISA-MC-AMEX, con la transacción MP huérfana.

class CobrosVivosTests(BaseMPTest):

    def test_aprobada_sin_consumir_esta_viva(self):
        trx = _transaccion(self.config, correlativo='500', monto=7000)
        vivos = mp.cobros_vivos_de_ticket(self.sucursal.id, '500')
        self.assertEqual([t.id for t in vivos], [trx.id])

    def test_aprobada_consumida_no_esta_viva(self):
        _transaccion(self.config, correlativo='501', monto=7000, consumida=True)
        self.assertEqual(mp.cobros_vivos_de_ticket(self.sucursal.id, '501'), [])

    def test_pendiente_esta_viva(self):
        _transaccion(self.config, correlativo='502', monto=7000, estado='PENDIENTE')
        vivos = mp.cobros_vivos_de_ticket(self.sucursal.id, '502')
        self.assertEqual(len(vivos), 1)
        self.assertEqual(vivos[0].estado, 'PENDIENTE')

    def test_estados_finales_no_estan_vivos(self):
        for i, estado in enumerate(('CANCELADA', 'EXPIRADA', 'RECHAZADA', 'DEVUELTA')):
            _transaccion(self.config, correlativo=f'51{i}', monto=1000, estado=estado)
            self.assertEqual(mp.cobros_vivos_de_ticket(self.sucursal.id, f'51{i}'), [])

    def test_correlativos_de_prueba_y_directo_nunca_bloquean(self):
        _transaccion(self.config, correlativo='PRUEBA-0101', monto=100)
        _transaccion(self.config, correlativo='DIRECTO-0101', monto=100)
        self.assertEqual(mp.cobros_vivos_de_ticket(self.sucursal.id, 'PRUEBA-0101'), [])
        self.assertEqual(mp.cobros_vivos_de_ticket(self.sucursal.id, 'DIRECTO-0101'), [])

    def test_pago_mp_del_payload_explica_el_cobro(self):
        _transaccion(self.config, correlativo='520', monto=7000)
        # La venta se cierra CON el pago MP: no queda nada huérfano
        self.assertEqual(
            mp.cobros_no_respaldados(self.sucursal.id, '520', [7000], refrescar=False), [])

    def test_cobro_sin_pago_mp_queda_sin_respaldo(self):
        # El caso del bug: la venta se cierra con tarjeta manual (0 pagos MP)
        _transaccion(self.config, correlativo='521', monto=114980)
        sobrantes = mp.cobros_no_respaldados(self.sucursal.id, '521', [], refrescar=False)
        self.assertEqual(len(sobrantes), 1)
        self.assertEqual(sobrantes[0].monto, 114980)

    def test_segundo_cobro_no_respaldado_se_denuncia(self):
        _transaccion(self.config, correlativo='522', monto=5000)
        _transaccion(self.config, correlativo='522', monto=8000,
                     external_reference='RM-dup-522')
        sobrantes = mp.cobros_no_respaldados(self.sucursal.id, '522', [5000],
                                             refrescar=False)
        self.assertEqual([t.monto for t in sobrantes], [8000])

    def test_metodo_pago_ticket_segun_medio_real(self):
        qr = _transaccion(self.config, correlativo='530', monto=1000, canal='QR')
        self.assertEqual(mp.metodo_pago_ticket_de(qr), 'MP_QR')
        deb = _transaccion(self.config, correlativo='531', monto=1000, canal='POINT',
                           metodo_pago_mp='debit_card', external_reference='RM-p-531')
        self.assertEqual(mp.metodo_pago_ticket_de(deb), 'MP_POINT_DEBITO')
        cred = _transaccion(self.config, correlativo='532', monto=1000, canal='POINT',
                            metodo_pago_mp='credit_card', external_reference='RM-p-532')
        self.assertEqual(mp.metodo_pago_ticket_de(cred), 'MP_POINT_CREDITO')
        otro = _transaccion(self.config, correlativo='533', monto=1000, canal='POINT',
                            metodo_pago_mp='account_money', external_reference='RM-p-533')
        self.assertEqual(mp.metodo_pago_ticket_de(otro), 'MP_POINT')

    def test_resumen_cobro_es_serializable(self):
        trx = _transaccion(self.config, correlativo='540', monto=9990,
                           canal='POINT', metodo_pago_mp='debit_card', payment_id='999')
        d = mp.resumen_cobro(trx)
        self.assertEqual(d['monto'], 9990)
        self.assertTrue(d['aprobada'])
        self.assertEqual(d['metodo_pago_ticket'], 'MP_POINT_DEBITO')
        self.assertEqual(d['payment_id'], '999')


@mock.patch.dict('os.environ', ENV_TEST)
class CrearOrdenConCobroVivoTests(BaseMPTest):
    """El "Reintentar" del POS no puede dejar dos cobros vivos por el mismo
    ticket: con la orden anterior en la pantalla del terminal, el cliente podía
    pagar las dos."""

    def test_cobro_aprobado_previo_bloquea_nueva_orden(self):
        _transaccion(self.config, correlativo='600', monto=5000, payment_id='123')
        with self.assertRaises(mp.MercadoPagoError) as ctx:
            mp.crear_orden(self.config, '600', 5000)
        self.assertIn('APROBADO', ctx.exception.mensaje)

    def test_cobro_en_vuelo_previo_se_cancela_antes_de_crear(self):
        previa = _transaccion(self.config, correlativo='601', monto=5000,
                              estado='PENDIENTE')
        with mock.patch.object(mp, 'cancelar') as m_cancel, \
             mock.patch.object(mp, 'consultar_estado', side_effect=lambda t, **k: t), \
             mock.patch('app.services.mercadopago_service.requests.request') as m_req:
            resp = mock.MagicMock()
            resp.status_code = 201
            resp.json.return_value = {'id': 'ORD-9', 'status': 'created',
                                      'type_response': {'qr_data': 'abc'}}
            m_req.return_value = resp
            mp.crear_orden(self.config, '601', 5000)
        m_cancel.assert_called_once()
        self.assertEqual(m_cancel.call_args[0][0].id, previa.id)

    def test_prueba_no_dispara_el_guard(self):
        _transaccion(self.config, correlativo='PRUEBA-0202', monto=100)
        with mock.patch('app.services.mercadopago_service.requests.request') as m_req:
            resp = mock.MagicMock()
            resp.status_code = 201
            resp.json.return_value = {'id': 'ORD-10', 'status': 'created',
                                      'type_response': {'qr_data': 'abc'}}
            m_req.return_value = resp
            trx, _qr = mp.crear_orden(self.config, 'PRUEBA-0202', 100)
        self.assertEqual(trx.estado, 'PENDIENTE')


class RepararCobroHuerfanoTests(BaseMPTest):
    """Comando `reparar_cobro_mp_huerfano`: pasa el pago de tarjeta manual a
    Mercado Pago y consume la transacción huérfana."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    def _venta_con_credito_manual(self, correlativo, monto):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
            estado='PAGADO', subTotal=monto, descuento=0, total=monto,
            responsable='test-mp',
        )
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='TBK_CREDITO_POS', monto=monto,
            tipo_tarjeta='VISA', voucher='000123', origen_pago='MANUAL',
        )
        trx = _transaccion(self.config, correlativo=correlativo, monto=monto,
                           canal='POINT', metodo_pago_mp='credit_card',
                           payment_id='PAY-1')
        return ticket, pago, trx

    def _correr(self, **kwargs):
        from io import StringIO

        from django.core.management import call_command
        salida = StringIO()
        call_command('reparar_cobro_mp_huerfano', stdout=salida, stderr=salida, **kwargs)
        return salida.getvalue()

    def test_dry_run_no_escribe(self):
        _t, pago, trx = self._venta_con_credito_manual(7100, 114980)
        salida = self._correr(sucursal=self.sucursal.alias)
        pago.refresh_from_db()
        trx.refresh_from_db()
        self.assertEqual(pago.metodo_pago, 'TBK_CREDITO_POS')
        self.assertFalse(trx.consumida)
        self.assertIn('Reparables: 1', salida)

    def test_apply_pasa_el_pago_a_mercado_pago(self):
        _t, pago, trx = self._venta_con_credito_manual(7101, 114980)
        self._correr(sucursal=self.sucursal.alias, apply=True)
        pago.refresh_from_db()
        trx.refresh_from_db()
        self.assertEqual(pago.metodo_pago, 'MP_POINT_CREDITO')
        self.assertEqual(pago.tipo_tarjeta, 'credit_card')
        self.assertEqual(pago.voucher, 'PAY-1')
        self.assertEqual(pago.origen_pago, 'POS_INTEGRADO')
        self.assertIn('Corregido', pago.notas)
        self.assertTrue(trx.consumida)
        self.assertEqual(trx.detalle_pago_id, pago.id)

    def test_apply_corrige_la_cuadratura(self):
        from app.views_modulo_ventas import _calcular_cuadratura_data
        self._venta_con_credito_manual(7102, 114980)
        hoy = timezone.localdate().strftime('%Y-%m-%d')
        antes = _calcular_cuadratura_data(self.sucursal, hoy)
        self.assertEqual(antes['total_visa_mc_amex'], 114980)
        self.assertEqual(antes['total_mercadopago_pos'], 0)

        self._correr(sucursal=self.sucursal.alias, apply=True)

        despues = _calcular_cuadratura_data(self.sucursal, hoy)
        self.assertEqual(despues['total_visa_mc_amex'], 0)
        self.assertEqual(despues['total_mercadopago_pos'], 114980)
        self.assertEqual(despues['total_mercadopago_pos_credito'], 114980)
        # La venta total no se mueve: cambia el medio, no la plata
        self.assertEqual(antes['venta_total'], despues['venta_total'])

    def test_no_toca_ventas_sin_contraparte_manual(self):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=7103,
            estado='PAGADO', subTotal=5000, descuento=0, total=5000,
            responsable='test-mp',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=5000)
        trx = _transaccion(self.config, correlativo=7103, monto=5000)
        salida = self._correr(sucursal=self.sucursal.alias, apply=True)
        trx.refresh_from_db()
        self.assertFalse(trx.consumida)
        self.assertIn('SIN CONTRAPARTE', salida)

    def test_no_toca_montos_distintos(self):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=7104,
            estado='PAGADO', subTotal=9000, descuento=0, total=9000,
            responsable='test-mp',
        )
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='TBK_CREDITO_POS', monto=9000)
        trx = _transaccion(self.config, correlativo=7104, monto=5000)
        self._correr(sucursal=self.sucursal.alias, apply=True)
        pago.refresh_from_db()
        trx.refresh_from_db()
        self.assertEqual(pago.metodo_pago, 'TBK_CREDITO_POS')
        self.assertFalse(trx.consumida)


class GuardCierreConCobroMPTests(BaseMPTest):
    """`registrar_pagos_ticket` no deja cerrar la venta con tarjeta manual
    mientras Mercado Pago tiene un cobro vivo para ese mismo ticket."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from app.tests.factories import crear_usuario
        cls.vendedor = crear_vendedor(empresa=cls.empresa)
        cls.cajero = crear_usuario(username='cajero_guard_mp', rol='cajero')

    def setUp(self):
        self.client.force_login(self.cajero)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.sucursal.id
        sesion.save()

    def _ticket(self, correlativo, total):
        return Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
            estado='PENDIENTE', subTotal=total, descuento=0, total=total,
            responsable='cajero_guard_mp',
        )

    def _cerrar(self, ticket, pagos, extra=None):
        cuerpo = {
            'correlativo': ticket.correlativo,
            'pagos': pagos,
            'productos': [],
            'tipo_documento': 'TICKET',
            'estado': 'PAGADO',
        }
        cuerpo.update(extra or {})
        return self.client.post(
            f'/app/api/tickets/{ticket.correlativo}/pagos/',
            data=cuerpo, content_type='application/json',
        )

    def test_cobro_aprobado_sin_usar_bloquea_el_cierre(self):
        ticket = self._ticket(8100, 114980)
        _transaccion(self.config, correlativo=8100, monto=114980, canal='POINT',
                     metodo_pago_mp='credit_card', payment_id='PAY-9')
        resp = self._cerrar(ticket, [{'metodo_pago': 'TBK_CREDITO_POS',
                                      'monto': 114980, 'origen_pago': 'MANUAL'}])
        self.assertEqual(resp.status_code, 400, resp.content)
        data = resp.json()
        self.assertEqual(data['error_tipo'], 'MP_COBRO_SIN_USAR')
        self.assertEqual(data['cobros_mp'][0]['monto'], 114980)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PENDIENTE')
        self.assertEqual(ticket.pagos.count(), 0)

    def test_cobro_aprobado_no_se_puede_forzar(self):
        """La plata existe en MP: la confirmación del cajero no lo salta."""
        ticket = self._ticket(8101, 50000)
        _transaccion(self.config, correlativo=8101, monto=50000)
        resp = self._cerrar(ticket, [{'metodo_pago': 'TBK_CREDITO_POS', 'monto': 50000}],
                            extra={'mp_confirmado_sin_cobro': True})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error_tipo'], 'MP_COBRO_SIN_USAR')

    def test_cobro_en_curso_bloquea_pero_se_puede_confirmar(self):
        ticket = self._ticket(8102, 30000)
        _transaccion(self.config, correlativo=8102, monto=30000, estado='PENDIENTE',
                     canal='POINT')
        pago_manual = [{'metodo_pago': 'TBK_DEBITO_POS', 'monto': 30000}]
        with mock.patch.object(mp, 'consultar_estado', side_effect=lambda t, **k: t):
            resp = self._cerrar(ticket, pago_manual)
            self.assertEqual(resp.status_code, 400, resp.content)
            self.assertEqual(resp.json()['error_tipo'], 'MP_COBRO_EN_CURSO')

            # El cajero canceló el cobro en la máquina y confirma
            resp2 = self._cerrar(ticket, pago_manual,
                                 extra={'mp_confirmado_sin_cobro': True})
        self.assertEqual(resp2.status_code, 200, resp2.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PAGADO')

    def test_venta_con_pago_mp_correcto_no_se_bloquea(self):
        ticket = self._ticket(8103, 20000)
        _transaccion(self.config, correlativo=8103, monto=20000, canal='POINT',
                     metodo_pago_mp='debit_card', payment_id='PAY-10')
        resp = self._cerrar(ticket, [{
            'metodo_pago': 'MP_POINT_DEBITO', 'monto': 20000,
            'tipo_tarjeta': 'debit_card', 'voucher': 'PAY-10',
            'origen_pago': 'POS_INTEGRADO',
        }])
        self.assertEqual(resp.status_code, 200, resp.content)
        trx = TransaccionMercadoPago.objects.get(correlativo_ticket='8103')
        self.assertTrue(trx.consumida)

    def test_venta_sin_mercado_pago_no_consulta_nada(self):
        ticket = self._ticket(8104, 12000)
        resp = self._cerrar(ticket, [{'metodo_pago': 'EFECTIVO', 'monto': 12000}])
        self.assertEqual(resp.status_code, 200, resp.content)
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'PAGADO')
