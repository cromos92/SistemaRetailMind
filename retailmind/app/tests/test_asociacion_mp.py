"""
Asociar cobros de Mercado Pago con su venta (services/asociacion_mp_service.py
y /app/api/mercadopago/asociar/...).

Cubre:
1. Cobro MP sin venta ↔ pago con tarjeta manual del MISMO monto: el pago pasa
   al método MP, el cobro queda consumido y la cuadratura cambia de medio sin
   mover la venta total.
2. Pago «MP manual» ↔ cobro directo sin venta.
3. Montos distintos, cobros ya consumidos y pagos no asociables se rechazan.
4. Importar desde la API (mockeada) un pago que el sistema no tenía.
5. Permisos: solo quien tenga `asociar_pagos_mercadopago` (por defecto el
   Maestro) usa los endpoints; el administrador recibe 403.
6. Alerta de caja (resumen_alerta_caja).
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.models import PermisoRol, Ticket, TicketDetallePago, TransaccionMercadoPago
from app.services import asociacion_mp_service as asoc
from app.services import mercadopago_service as mp
from app.tests.factories import crear_empresa_user, crear_usuario, crear_vendedor
from app.tests.test_mercadopago_pos import ENV_TEST, BaseMPTest, _transaccion


class _Base(BaseMPTest):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.vendedor = crear_vendedor(empresa=cls.empresa)
        cls.maestro = crear_usuario(username='maestro-mp', rol='maestro')
        cls.admin = crear_usuario(username='admin-mp', rol='administrador')
        crear_empresa_user(cls.maestro, cls.empresa, cls.sucursal)
        crear_empresa_user(cls.admin, cls.empresa, cls.sucursal)

    def _venta(self, correlativo, monto, metodo='TBK_CREDITO_POS', origen='MANUAL', voucher='000123'):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
            estado='PAGADO', subTotal=monto, descuento=0, total=monto, responsable='test-mp',
        )
        pago = TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago=metodo, monto=monto, voucher=voucher, origen_pago=origen,
        )
        return ticket, pago


class AsociarServicioTests(_Base):
    def test_cobro_sin_venta_a_tarjeta_manual(self):
        from app.views_modulo_ventas import _calcular_cuadratura_data
        _t, pago = self._venta(8100, 45990)
        trx = _transaccion(self.config, correlativo='DIRECTO-1', monto=45990, canal='POINT',
                           metodo_pago_mp='debit_card', payment_id_mp='123456789')
        hoy = timezone.localdate().strftime('%Y-%m-%d')
        antes = _calcular_cuadratura_data(self.sucursal, hoy)

        candidatos = asoc.candidatos_para_cobro(trx)
        self.assertEqual([c['id'] for c in candidatos], [pago.id])

        r = asoc.asociar(trx.id, pago.id, self.maestro, recalcular_arqueo=False)
        pago.refresh_from_db()
        trx.refresh_from_db()
        self.assertEqual(r['metodo_nuevo'], 'MP_POINT_DEBITO')
        self.assertEqual(pago.metodo_pago, 'MP_POINT_DEBITO')
        self.assertEqual(pago.voucher, '123456789')
        self.assertEqual(pago.origen_pago, 'POS_INTEGRADO')
        self.assertIn('Asociado', pago.notas)
        self.assertTrue(trx.consumida)
        self.assertEqual(trx.detalle_pago_id, pago.id)

        despues = _calcular_cuadratura_data(self.sucursal, hoy)
        self.assertEqual(despues['total_mercadopago_pos'] - antes['total_mercadopago_pos'], 45990)
        self.assertEqual(antes['venta_total'], despues['venta_total'])
        self.assertEqual(asoc.cobros_sin_venta(self.sucursal.id).count(), 0)

    def test_mp_manual_a_cobro_directo(self):
        _t, pago = self._venta(8101, 20000, metodo='MP_POINT', voucher='999')
        self.assertEqual(list(asoc.pagos_manuales_sin_respaldo(self.sucursal.id)), [pago])
        trx = _transaccion(self.config, correlativo='DIRECTO-2', monto=20000, canal='POINT',
                           metodo_pago_mp='credit_card')
        self.assertEqual([c['id'] for c in asoc.candidatos_para_pago(pago)], [trx.id])
        asoc.asociar(trx.id, pago.id, self.maestro, recalcular_arqueo=False)
        pago.refresh_from_db()
        self.assertEqual(pago.metodo_pago, 'MP_POINT_CREDITO')
        self.assertEqual(list(asoc.pagos_manuales_sin_respaldo(self.sucursal.id)), [])

    def test_rechaza_monto_distinto_y_dobles(self):
        _t, pago = self._venta(8102, 10000)
        trx = _transaccion(self.config, correlativo='8102', monto=9990)
        with self.assertRaises(asoc.AsociacionError):
            asoc.asociar(trx.id, pago.id, self.maestro, recalcular_arqueo=False)
        trx2 = _transaccion(self.config, correlativo='8102b', monto=10000)
        asoc.asociar(trx2.id, pago.id, self.maestro, recalcular_arqueo=False)
        trx3 = _transaccion(self.config, correlativo='8102c', monto=10000)
        with self.assertRaises(asoc.AsociacionError):   # el pago ya tiene cobro
            asoc.asociar(trx3.id, pago.id, self.maestro, recalcular_arqueo=False)
        with self.assertRaises(asoc.AsociacionError):   # el cobro ya está consumido
            _t2, otro = self._venta(8103, 10000)
            asoc.asociar(trx2.id, otro.id, self.maestro, recalcular_arqueo=False)

    def test_no_asocia_efectivo(self):
        _t, pago = self._venta(8104, 5000, metodo='EFECTIVO', origen=None, voucher='')
        trx = _transaccion(self.config, correlativo='8104', monto=5000)
        with self.assertRaises(asoc.AsociacionError):
            asoc.asociar(trx.id, pago.id, self.maestro, recalcular_arqueo=False)

    @mock.patch.dict('os.environ', ENV_TEST)
    def test_importar_pago_desde_api(self):
        _t, pago = self._venta(8105, 31990, metodo='MP_POINT_DEBITO', voucher='11111')
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            'id': 987654321, 'status': 'approved', 'status_detail': 'accredited',
            'transaction_amount': 31990, 'payment_type_id': 'debit_card',
            'card': {'last_four_digits': '4242'}, 'authorization_code': 'A1',
            'transaction_details': {'net_received_amount': 31000},
            'fee_details': [{'amount': 990}], 'installments': 1,
        }
        with mock.patch.object(mp, '_request', return_value=resp):
            r = asoc.importar_y_asociar('987654321', pago.id, self.config.id, self.maestro,
                                        recalcular_arqueo=False)
        trx = TransaccionMercadoPago.objects.get(id=r['transaccion_id'])
        pago.refresh_from_db()
        self.assertTrue(trx.consumida)
        self.assertEqual(trx.payment_id_mp, '987654321')
        self.assertEqual(trx.monto_neto, 31000)
        self.assertEqual(trx.ultimos_4_digitos, '4242')
        self.assertEqual(pago.voucher, '987654321')   # corrige el N° digitado
        self.assertEqual(pago.metodo_pago, 'MP_POINT_DEBITO')

    @mock.patch.dict('os.environ', ENV_TEST)
    def test_importar_rechaza_monto_distinto(self):
        _t, pago = self._venta(8106, 10000, metodo='MP_POINT', voucher='')
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'id': 5, 'status': 'approved', 'transaction_amount': 12000}
        with mock.patch.object(mp, '_request', return_value=resp):
            with self.assertRaises(asoc.AsociacionError):
                asoc.importar_y_asociar('5', pago.id, self.config.id, self.maestro, recalcular_arqueo=False)
        self.assertFalse(TransaccionMercadoPago.objects.filter(payment_id_mp='5').exists())

    def test_alerta_caja(self):
        self._venta(8107, 7000, metodo='MP_QR', voucher='1')
        _transaccion(self.config, correlativo='DIRECTO-9', monto=3000)
        r = asoc.resumen_alerta_caja(self.sucursal.id, timezone.localdate())
        self.assertTrue(r['hay_alerta'])
        self.assertEqual((r['cobros_sin_venta'], r['cobros_sin_venta_monto'], r['cobros_directos']), (1, 3000, 1))
        self.assertEqual((r['manuales_sin_respaldo'], r['manuales_sin_respaldo_monto']), (1, 7000))


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class AsociarEndpointsTests(_Base):
    def _cliente(self, usuario):
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = self.sucursal.id
        s.save()
        return c

    def test_solo_maestro_por_defecto(self):
        _t, pago = self._venta(8200, 15000)
        trx = _transaccion(self.config, correlativo='8200', monto=15000)
        cuerpo = json.dumps({'trx_id': trx.id, 'pago_id': pago.id, 'recalcular_arqueo': False})

        r = self._cliente(self.admin).post('/app/api/mercadopago/asociar/cobro/', cuerpo,
                                           content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)
        trx.refresh_from_db()
        self.assertFalse(trx.consumida)
        # La migración 0234 deja la fila del administrador explícita y apagada.
        self.assertFalse(PermisoRol.objects.get(rol='administrador',
                                                opcion_menu__codigo='asociar_pagos_mercadopago').puede_editar)

        c = self._cliente(self.maestro)
        r = c.get('/app/api/mercadopago/asociar/pendientes/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['resumen']['cobros_cantidad'], 1)
        r = c.get(f'/app/api/mercadopago/asociar/candidatos/?trx_id={trx.id}', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual([x['id'] for x in r.json()['candidatos']], [pago.id])
        r = c.post('/app/api/mercadopago/asociar/cobro/', cuerpo,
                   content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content)
        trx.refresh_from_db()
        self.assertTrue(trx.consumida)

    def test_error_de_negocio_es_400_legible(self):
        _t, pago = self._venta(8201, 15000)
        trx = _transaccion(self.config, correlativo='8201', monto=14000)
        r = self._cliente(self.maestro).post(
            '/app/api/mercadopago/asociar/cobro/',
            json.dumps({'trx_id': trx.id, 'pago_id': pago.id}),
            content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 400)
        self.assertIn('no calzan', r.json()['mensaje'])

    def test_candidatos_por_monto_para_pago_solo_en_mp(self):
        _t, pago = self._venta(8202, 22990, metodo='MP_POINT', voucher='555')
        fecha = timezone.localtime().strftime('%Y-%m-%d %H:%M')
        r = self._cliente(self.maestro).get(
            f'/app/api/mercadopago/asociar/candidatos/?monto=22990&fecha={fecha}&sucursal_id={self.sucursal.id}&payment_id=555',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        candidatos = r.json()['candidatos']
        self.assertEqual([c['id'] for c in candidatos], [pago.id])
        self.assertTrue(candidatos[0]['mismo_voucher'])
