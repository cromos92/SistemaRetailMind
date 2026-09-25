"""
Asignar un pago de Mercado Pago a su venta desde «Contra Mercado Pago».

Caso real 21-09-2026: las máquinas estuvieron en modo manual, las ventas se
anotaron como tarjeta y los cobros de MP quedaron «sin registro»; un «MP manual»
se anotó con el N° 18014409060 (le faltaba un 8 al 180148409060).

Cubre: N° parecido, caja de la tienda de la venta y fecha real del cobro al
importar (el cierre por caja de ESE día cuadra), cuenta distinta, devoluciones,
doble registro, retiro amarrado, Transbank integrado real fuera de los
candidatos, efectivo solo para administrador, alcance por tienda, venta
sugerida (pares ordenados por hora), atribución por tienda en el cruce y lote.

Correr en BD desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_asignar_pago_mp
"""
import json
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.models import (MercadoPagoCuenta, PermisoRol, RetiroMercadoPago, Ticket, TicketDetallePago,
                        TransaccionMercadoPago)
from app.services import asociacion_mp_service as asoc
from app.services import conciliacion_mp_service as conc
from app.services import mercadopago_service as mp
from app.tests.factories import (crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
                                 crear_vendedor)
from app.tests.test_mercadopago_pos import ENV_TEST, _config, _transaccion

TZ = timezone.get_current_timezone()
DIA = date(2026, 9, 21)


def _a_las(h, m, dia=DIA):
    return timezone.make_aware(datetime.combine(dia, time(h, m)), TZ)


def _resp(payload, status=200):
    r = mock.MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


def _pago_mp(pid, monto, cuando='2026-09-21T10:55:12.000-04:00', **extra):
    return dict({'id': int(pid), 'status': 'approved', 'status_detail': 'accredited',
                 'transaction_amount': monto, 'payment_type_id': 'debit_card', 'date_created': cuando,
                 'card': {'last_four_digits': '4242'}, 'transaction_details': {'net_received_amount': monto - 100},
                 'fee_details': [{'amount': 100}], 'installments': 1}, **extra)


class NParecidoTest(TestCase):

    def test_errores_de_tipeo(self):
        self.assertEqual(conc.n_parecido('18014409060', '180148409060'), 'falta un 8')
        self.assertEqual(conc.n_parecido('1801484090600', '180148409060'), 'sobra un 0')
        self.assertEqual(conc.n_parecido('180148409061', '180148409060'), 'dice 1 donde va 0')
        self.assertIn('al revés', conc.n_parecido('180148490060', '180148409060'))

    def test_no_confunde(self):
        self.assertEqual(conc.n_parecido('180148409060', '180148409060'), '')   # igual no es «parecido»
        self.assertEqual(conc.n_parecido('79802', '180148409060'), '')          # N° corto
        self.assertEqual(conc.n_parecido('179185652457', '179185881363'), '')   # vecinos de la misma cuenta
        self.assertEqual(conc.n_parecido('', '180148409060'), '')


@mock.patch.dict('os.environ', ENV_TEST)
class _Base(TestCase):
    """Empresa A con cuenta MP compartida por las tiendas A1 y A2; empresa B aparte."""

    def setUp(self):
        mp._breaker_registrar_exito()
        self.emp_a = crear_empresa(nombre='Calzados A', rut='76.111.111-1')
        self.emp_b = crear_empresa(nombre='Deportes B', rut='77.222.222-2')
        MercadoPagoCuenta.objects.create(empresa=self.emp_a, mp_user_id='111')
        MercadoPagoCuenta.objects.create(empresa=self.emp_b, mp_user_id='222')
        self.a1 = crear_sucursal(empresa=self.emp_a, alias='A1')
        self.a2 = crear_sucursal(empresa=self.emp_a, alias='A2')
        self.b1 = crear_sucursal(empresa=self.emp_b, alias='B1')
        self.cfg_a1 = _config(self.a1, nombre='Caja 1', external_pos_id='PA1', modo='POINT')
        self.cfg_a2 = _config(self.a2, nombre='Caja 1', external_pos_id='PA2', modo='POINT')
        self.cfg_b1 = _config(self.b1, nombre='Caja 1', external_pos_id='PB1', modo='POINT')
        self.vendedor = crear_vendedor(empresa=self.emp_a)
        self.admin = crear_usuario(username='adm-asig', rol='administrador')
        self._corr = 5000

    def _venta(self, sucursal, monto, metodo='TBK_DEBITO_POS', origen='MANUAL', voucher='', cuando=None):
        self._corr += 1
        ticket = Ticket.objects.create(vendedor=self.vendedor, sucursal=sucursal, correlativo=self._corr,
                                       estado='PAGADO', subTotal=monto, descuento=0, total=monto, responsable='t')
        pago = TicketDetallePago.objects.create(ticket=ticket, metodo_pago=metodo, monto=monto,
                                                voucher=voucher, origen_pago=origen)
        if cuando is not None:
            TicketDetallePago.objects.filter(pk=pago.pk).update(creado_en=cuando)
            Ticket.objects.filter(pk=ticket.pk).update(fecha=timezone.localtime(cuando).date())
            pago.refresh_from_db()
        return pago


class ImportarEnSuTiendaTest(_Base):

    def test_queda_en_la_caja_de_la_venta_con_la_fecha_real_y_el_cierre_de_ese_dia_cuadra(self):
        pago = self._venta(self.a2, 43980, cuando=_a_las(11, 57))
        payment = _pago_mp(180148409060, 43980)
        # Se lee con la caja de A1 («la primera de la cuenta»): igual debe quedar en A2.
        with mock.patch.object(mp, '_request', return_value=_resp(payment)):
            r = asoc.importar_y_asociar('180148409060', pago.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)
        trx = TransaccionMercadoPago.objects.get(id=r['transaccion_id'])
        self.assertEqual((trx.config_id, trx.sucursal_id), (self.cfg_a2.id, self.a2.id))
        # 10:55 con -04:00 = 11:55 en Chile (horario de verano, -03:00)
        self.assertEqual(timezone.localtime(trx.creado_en).strftime('%Y-%m-%d %H:%M'), '2026-09-21 11:55')
        self.assertEqual(trx.external_reference, 'ASOC-180148409060')
        pago.refresh_from_db()
        self.assertEqual((pago.metodo_pago, pago.voucher), ('MP_POINT_DEBITO', '180148409060'))
        # El «Cierre por caja y día» del 21-09 lo reconoce: A2 cuadra y A1 no lo cuenta.
        cierre_a2 = mp.conciliar_cierre_mp(self.cfg_a2, DIA, pagos=[payment])
        self.assertEqual((cierre_a2['sistema_total'], cierre_a2['sin_registro']), (43980, []))
        self.assertEqual(mp.conciliar_cierre_mp(self.cfg_a1, DIA, pagos=[payment])['sistema_total'], 0)

    def test_pago_de_otra_cuenta_no_puede_ser_de_esa_venta(self):
        pago = self._venta(self.a1, 10000)
        with mock.patch.object(mp, '_request', return_value=_resp(_pago_mp(555000000001, 10000))):
            with self.assertRaisesRegex(asoc.AsociacionError, 'otra cuenta'):
                asoc.importar_y_asociar('555000000001', pago.id, self.cfg_b1.id, self.admin, recalcular_arqueo=False)
        self.assertFalse(TransaccionMercadoPago.objects.exists())

    def test_con_devoluciones_no_se_asigna(self):
        pago = self._venta(self.a1, 10000)
        payment = _pago_mp(555000000002, 10000, transaction_amount_refunded=10000)
        with mock.patch.object(mp, '_request', return_value=_resp(payment)):
            with self.assertRaisesRegex(asoc.AsociacionError, 'devoluciones'):
                asoc.importar_y_asociar('555000000002', pago.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)

    def test_doble_registro_da_error_legible(self):
        pago = self._venta(self.a1, 10000)
        _transaccion(self.cfg_a1, correlativo='X', monto=10000, external_reference='ASOC-555000000003')
        with mock.patch.object(mp, '_request', return_value=_resp(_pago_mp(555000000003, 10000))):
            with self.assertRaisesRegex(asoc.AsociacionError, 'otra persona'):
                asoc.importar_y_asociar('555000000003', pago.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)
        pago.refresh_from_db()
        self.assertEqual(pago.metodo_pago, 'TBK_DEBITO_POS')   # no quedó a medias

    def test_amarra_el_retiro_que_ya_se_llevo_el_pago(self):
        retiro = RetiroMercadoPago.objects.create(config=self.cfg_a1, withdrawal_id='W1', fecha=DIA, monto=9900,
                                                  raw_reporte={'pagos': ['555000000004']})
        pago = self._venta(self.a1, 10000)
        with mock.patch.object(mp, '_request', return_value=_resp(_pago_mp(555000000004, 10000))):
            r = asoc.importar_y_asociar('555000000004', pago.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)
        self.assertEqual(TransaccionMercadoPago.objects.get(id=r['transaccion_id']).retiro_id, retiro.id)


class QuePagosSePuedenConvertirTest(_Base):

    def test_transbank_integrado_real_no_es_candidato(self):
        manual = self._venta(self.a1, 16990, cuando=_a_las(11, 30))
        integrado = self._venta(self.a1, 16990, origen='POS_INTEGRADO', cuando=_a_las(11, 31))
        ids = [c['id'] for c in asoc.candidatos_pagos(16990, _a_las(11, 27), self.a1.id)]
        self.assertEqual(ids, [manual.id])
        with self.assertRaises(asoc.AsociacionError):
            asoc._validar_pago(integrado)

    def test_efectivo_se_ve_pero_solo_el_administrador_lo_convierte(self):
        efectivo = self._venta(self.a1, 12990, metodo='EFECTIVO', origen=None, cuando=_a_las(11, 20))
        fila = asoc.candidatos_pagos(12990, _a_las(11, 19), self.a1.id)[0]
        self.assertEqual((fila['id'], fila['asociable'], fila['otro_medio']), (efectivo.id, False, True))
        self.assertIn('administrador', fila['motivo'])
        self.assertTrue(asoc.candidatos_pagos(12990, _a_las(11, 19), self.a1.id, permitir_otros_medios=True)[0]['asociable'])
        with mock.patch.object(mp, '_request', return_value=_resp(_pago_mp(179164362323, 12990))):
            with self.assertRaises(asoc.AsociacionError):
                asoc.importar_y_asociar('179164362323', efectivo.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)
            asoc.importar_y_asociar('179164362323', efectivo.id, self.cfg_a1.id, self.admin,
                                    recalcular_arqueo=False, permitir_otros_medios=True)
        efectivo.refresh_from_db()
        self.assertEqual(efectivo.metodo_pago, 'MP_POINT_DEBITO')

    def test_alcance_por_tienda(self):
        pago_a2 = self._venta(self.a2, 10000)
        with mock.patch.object(mp, '_request', return_value=_resp(_pago_mp(555000000005, 10000))):
            with self.assertRaisesRegex(asoc.AsociacionError, 'otra tienda'):
                asoc.importar_y_asociar('555000000005', pago_a2.id, self.cfg_a1.id, self.admin,
                                        recalcular_arqueo=False, sucursal_permitida=self.a1.id)
        trx = _transaccion(self.cfg_a1, correlativo='DIRECTO-1', monto=10000)
        with self.assertRaisesRegex(asoc.AsociacionError, 'otra tienda'):
            asoc.asociar(trx.id, pago_a2.id, self.admin, recalcular_arqueo=False, sucursal_permitida=self.a1.id)


class VentaSugeridaTest(_Base):

    def _fila(self, pid, monto, h, m, sucursal):
        return {'payment_id': str(pid), 'monto': monto, 'instante': _a_las(h, m).isoformat(),
                'sucursal_id': sucursal.id}

    def test_dos_cobros_del_mismo_monto_se_emparejan_en_orden_de_hora(self):
        temprano = self._venta(self.a2, 16990, cuando=_a_las(11, 30))
        tarde = self._venta(self.a2, 16990, cuando=_a_las(13, 52))
        filas = [self._fila(179187451605, 16990, 13, 50, self.a2), self._fila(179163786839, 16990, 11, 27, self.a2)]
        asoc.sugerir_ventas(filas)
        por_pid = {f['payment_id']: f['sugerencia'] for f in filas}
        self.assertEqual(por_pid['179163786839']['pago_id'], temprano.id)
        self.assertEqual(por_pid['179187451605']['pago_id'], tarde.id)
        self.assertEqual(por_pid['179163786839']['confianza'], 'alta')

    def test_sin_par_claro_no_sugiere_y_respeta_la_tienda(self):
        self._venta(self.a2, 24990, cuando=_a_las(12, 0))
        self._venta(self.a2, 24990, cuando=_a_las(12, 5))
        self._venta(self.a1, 34990, cuando=_a_las(11, 25))
        filas = [self._fila(1, 24990, 13, 20, self.a2),      # 1 cobro, 2 ventas: elegir a mano
                 self._fila(2, 34990, 11, 23, self.a2)]      # la venta de ese monto es de A1
        asoc.sugerir_ventas(filas)
        self.assertEqual((filas[0]['sugerencia'], filas[0]['candidatos_n']), (None, 2))
        self.assertEqual((filas[1]['sugerencia'], filas[1]['candidatos_n']), (None, 0))
        # Un usuario de A1 no recibe sugerencias de A2.
        filas = [self._fila(3, 34990, 11, 23, self.a1)]
        asoc.sugerir_ventas(filas, sucursal_permitida=self.a2.id)
        self.assertIsNone(filas[0]['sugerencia'])

    def test_mp_manual_con_n_mal_escrito_sale_sugerido_con_el_error(self):
        manual = self._venta(self.a2, 43980, metodo='MP_POINT', voucher='18014409060', cuando=_a_las(11, 57))
        filas = [self._fila(180148409060, 43980, 11, 55, self.a2)]
        asoc.sugerir_ventas(filas)
        self.assertEqual(filas[0]['sugerencia']['pago_id'], manual.id)
        self.assertEqual(filas[0]['sugerencia']['parecido'], 'falta un 8')


@mock.patch.dict('os.environ', ENV_TEST)
class CruceContraMPTest(_Base):

    def _cierre(self, cfg_con_pago, pid):
        def cierre(cfg, dia, pagos=None):
            sin = [{'payment_id': pid, 'atribuible': True}] if cfg.id == cfg_con_pago.id else []
            return {'ok': True, 'sistema_total': 0, 'mp_total': 0, 'diferencia': 0, 'cuadra': not sin,
                    'sin_registro': sin, 'sin_confirmar': []}
        return cierre

    def test_cada_pago_con_su_tienda_hora_local_y_n_mal_digitado(self):
        hoy = timezone.localdate()
        cuando = timezone.localtime().replace(hour=11, minute=55, second=0, microsecond=0)
        manual = self._venta(self.a2, 43980, metodo='MP_POINT', voucher='18014409060',
                             cuando=cuando + timedelta(minutes=2))
        corto = self._venta(self.a2, 400000, metodo='MP_POINT', voucher='79802', cuando=cuando)
        pagos = [_pago_mp(180148409060, 43980, cuando=cuando.isoformat())]
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos), \
             mock.patch('app.services.mercadopago_service.conciliar_cierre_mp',
                        side_effect=self._cierre(self.cfg_a2, '180148409060')):
            res = conc.diferencias_contra_mp(hoy, hoy)
            solo_a1 = conc.diferencias_contra_mp(hoy, hoy, sucursal_id=self.a1.id)
        fila = next(f for f in res['sin_registro'] if f['payment_id'] == '180148409060')
        self.assertEqual((fila['sucursal'], fila['sucursal_id'], fila['atribuida']), ('A2', self.a2.id, True))
        self.assertEqual(fila['fecha'][11:], '11:55')
        self.assertEqual(fila['ultimos_4'], '4242')
        self.assertEqual(fila['n_mal_digitado']['pago_id'], manual.id)
        m = next(x for x in res['manuales_sin_pago'] if x['pago_id'] == manual.id)
        self.assertEqual((m['sugerencia_n']['payment_id'], m['sugerencia_n']['explicacion']), ('180148409060', 'falta un 8'))
        c = next(x for x in res['manuales_sin_pago'] if x['pago_id'] == corto.id)
        self.assertIn('5 dígitos', c['motivo'])
        # Con la tienda A1 elegida, el pago de A2 no aparece.
        self.assertEqual(solo_a1['sin_registro'], [])


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
@mock.patch.dict('os.environ', ENV_TEST)
class EndpointsTest(_Base):

    def _cliente(self, usuario, sucursal):
        crear_empresa_user(usuario, sucursal.empresa, sucursal)
        c = Client()
        c.force_login(usuario)
        s = c.session
        s['idSucursalActual'] = sucursal.id
        s.save()
        return c

    def _post(self, c, url, cuerpo):
        return c.post(url, json.dumps(cuerpo), content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_lote_asigna_los_que_puede_y_explica_los_que_no(self):
        bien = self._venta(self.a1, 12990, cuando=_a_las(11, 20))
        mal = self._venta(self.a1, 34990, cuando=_a_las(11, 25))
        respuestas = {'/v1/payments/179164362323': _resp(_pago_mp(179164362323, 12990)),
                      '/v1/payments/179165030247': _resp(_pago_mp(179165030247, 99990))}
        c = self._cliente(self.admin, self.a1)
        with mock.patch.object(mp, '_request', side_effect=lambda cfg, metodo, ruta, **kw: respuestas[ruta]):
            r = self._post(c, '/app/api/mercadopago/asociar/lote/', {'items': [
                {'payment_id': '179164362323', 'pago_id': bien.id, 'config_id': self.cfg_a1.id},
                {'payment_id': '179165030247', 'pago_id': mal.id, 'config_id': self.cfg_a1.id}]})
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual((d['asignados'], d['fallidos']), (1, 1))
        self.assertIn('no calzan', [x for x in d['resultados'] if not x['ok']][0]['error'])
        bien.refresh_from_db()
        self.assertEqual(bien.metodo_pago, 'MP_POINT_DEBITO')

    def test_jefe_de_local_solo_su_tienda_y_sin_efectivo(self):
        _permiso_jefe()
        jefe = crear_usuario(username='jefe-a1', rol='jefe_local')
        c = self._cliente(jefe, self.a1)
        ajeno = self._venta(self.a2, 10000)
        r = c.get(f'/app/api/mercadopago/asociar/candidatos/?pago_id={ajeno.id}', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 404)
        efectivo = self._venta(self.a1, 12990, metodo='EFECTIVO', origen=None, cuando=_a_las(11, 20))
        with mock.patch.object(mp, '_request', return_value=_resp(_pago_mp(179164362323, 12990))):
            r = self._post(c, '/app/api/mercadopago/asociar/importar/', {
                'payment_id': '179164362323', 'pago_id': efectivo.id, 'config_id': self.cfg_a1.id,
                'confirmar_otro_medio': True, 'recalcular_arqueo': False})
        self.assertEqual(r.status_code, 400)
        self.assertIn('administrador', r.json()['mensaje'])
        efectivo.refresh_from_db()
        self.assertEqual(efectivo.metodo_pago, 'EFECTIVO')

    def test_contra_mp_trae_la_venta_sugerida_a_quien_puede_asignar(self):
        hoy = timezone.localdate()
        cuando = timezone.localtime().replace(hour=13, minute=38, second=0, microsecond=0)
        venta = self._venta(self.a1, 46990, cuando=cuando + timedelta(minutes=1))
        pagos = [_pago_mp(179186819127, 46990, cuando=cuando.isoformat())]

        def cierre(cfg, dia, pagos=None):
            sin = [{'payment_id': '179186819127', 'atribuible': True}] if cfg.id == self.cfg_a1.id else []
            return {'ok': True, 'sistema_total': 0, 'mp_total': 0, 'diferencia': 0, 'cuadra': True,
                    'sin_registro': sin, 'sin_confirmar': []}
        c = self._cliente(self.admin, self.a1)
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos), \
             mock.patch('app.services.mercadopago_service.conciliar_cierre_mp', side_effect=cierre):
            d = c.get('/app/api/mercadopago/conciliacion/contra-mp/', {'desde': str(hoy), 'hasta': str(hoy)}).json()
        self.assertTrue(d['puede_asignar'])
        fila = d['sin_registro'][0]
        self.assertEqual((fila['sucursal'], fila['sugerencia']['pago_id']), ('A1', venta.id))


def _permiso_jefe():
    """Jefe de local con asignación y acceso a Conciliación MP (se lo daría el Maestro en Permisos)."""
    from app.models import OpcionMenu
    for codigo in ('asociar_pagos_mercadopago', 'dineros_mercadopago'):
        PermisoRol.objects.update_or_create(rol='jefe_local', opcion_menu=OpcionMenu.objects.get(codigo=codigo),
                                            defaults={'puede_ver': True, 'puede_editar': True})


@mock.patch.dict('os.environ', ENV_TEST)
class RevisionAdversarialTest(_Base):
    """Hallazgos confirmados por la revisión del 25-09."""

    def test_no_pisa_el_n_correcto_de_un_mp_manual_aun_sin_importar(self):
        # La venta A tiene su N° correcto (pago real de MP del mismo monto, aún sin registrar);
        # otro cobro del mismo monto cuya venta quedó en efectivo NO se le puede sugerir ni asignar.
        a = self._venta(self.a1, 16990, metodo='MP_POINT_DEBITO', voucher='179160000001', cuando=_a_las(10, 5))
        filas = [{'payment_id': '179160055555', 'monto': 16990, 'instante': _a_las(10, 30).isoformat(),
                  'sucursal_id': self.a1.id}]
        asoc.sugerir_ventas(filas, vouchers_calzados=['179160000001'])
        self.assertIsNone(filas[0]['sugerencia'])
        respuestas = {'/v1/payments/179160055555': _resp(_pago_mp(179160055555, 16990)),
                      '/v1/payments/179160000001': _resp(_pago_mp(179160000001, 16990))}
        with mock.patch.object(mp, '_request', side_effect=lambda cfg, metodo, ruta, **kw: respuestas[ruta]):
            with self.assertRaisesRegex(asoc.AsociacionError, 'ya tiene su N°'):
                asoc.importar_y_asociar('179160055555', a.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)
        a.refresh_from_db()
        self.assertEqual(a.voucher, '179160000001')

    def test_empareja_primero_por_n(self):
        # Dos ventas del mismo monto: el «MP manual» con el N° casi igual va con su cobro
        # aunque el orden por hora diría lo contrario.
        tipeo = self._venta(self.a1, 43980, metodo='MP_POINT', voucher='18014409060', cuando=_a_las(13, 0))
        otra = self._venta(self.a1, 43980, metodo='TBK_DEBITO_POS', cuando=_a_las(11, 0))
        filas = [{'payment_id': '180148409060', 'monto': 43980, 'instante': _a_las(10, 58).isoformat(), 'sucursal_id': self.a1.id},
                 {'payment_id': '179000000002', 'monto': 43980, 'instante': _a_las(12, 58).isoformat(), 'sucursal_id': self.a1.id}]
        asoc.sugerir_ventas(filas)
        self.assertEqual(filas[0]['sugerencia']['pago_id'], tipeo.id)
        self.assertEqual(filas[1]['sugerencia']['pago_id'], otra.id)

    def test_venta_con_su_propio_cobro_sin_consumir_no_se_sugiere(self):
        venta = self._venta(self.a1, 24990, cuando=_a_las(12, 0))
        propio = _transaccion(self.cfg_a1, correlativo=str(venta.ticket.correlativo), monto=24990, consumida=False)
        TransaccionMercadoPago.objects.filter(pk=propio.pk).update(creado_en=_a_las(11, 58))
        filas = [{'payment_id': '179185652457', 'monto': 24990, 'instante': _a_las(12, 1).isoformat(), 'sucursal_id': self.a1.id}]
        asoc.sugerir_ventas(filas)
        self.assertIsNone(filas[0]['sugerencia'])

    def test_cobro_del_pos_ya_registrado_por_referencia_no_se_duplica(self):
        venta = self._venta(self.a1, 30000)
        local = _transaccion(self.cfg_a1, correlativo='DIRECTO-7', monto=30000, consumida=False,
                             external_reference='RM-1-777-abc')
        payment = _pago_mp(555000000010, 30000, external_reference='RM-1-777-abc')
        with mock.patch.object(mp, '_request', return_value=_resp(payment)):
            asoc.importar_y_asociar('555000000010', venta.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)
        local.refresh_from_db()
        self.assertTrue(local.consumida)
        self.assertFalse(TransaccionMercadoPago.objects.filter(external_reference__startswith='ASOC-').exists())

    def test_comision_es_monto_menos_neto(self):
        venta = self._venta(self.a1, 10000)
        payment = _pago_mp(555000000011, 10000, fee_details=[{'amount': 300, 'fee_payer': 'collector'},
                                                              {'amount': 900, 'fee_payer': 'payer'}])
        payment['transaction_details'] = {'net_received_amount': 9700}
        with mock.patch.object(mp, '_request', return_value=_resp(payment)):
            r = asoc.importar_y_asociar('555000000011', venta.id, self.cfg_a1.id, self.admin, recalcular_arqueo=False)
        trx = TransaccionMercadoPago.objects.get(id=r['transaccion_id'])
        self.assertEqual((trx.monto_neto, trx.fee_mp), (9700, 300))

    def test_no_admin_no_consulta_la_api_con_otra_cuenta(self):
        venta = self._venta(self.a1, 10000)
        with mock.patch.object(mp, '_request') as api:
            with self.assertRaisesRegex(asoc.AsociacionError, 'otra cuenta'):
                asoc.importar_y_asociar('555000000012', venta.id, self.cfg_b1.id, self.admin,
                                        recalcular_arqueo=False, sucursal_permitida=self.a1.id)
        api.assert_not_called()

    def test_no_admin_no_se_queda_con_un_cobro_de_otra_tienda_de_la_cuenta(self):
        venta = self._venta(self.a1, 46990, cuando=_a_las(13, 40))
        payment = _pago_mp(179186819127, 46990, cuando=_a_las(13, 38).isoformat())

        def cierre_de(tienda_cfg):
            def cierre(cfg, dia, pagos=None):
                sin = [{'payment_id': '179186819127', 'atribuible': True}] if cfg.id == tienda_cfg.id else []
                return {'ok': True, 'sin_registro': sin}
            return cierre
        with mock.patch.object(mp, '_request', return_value=_resp(payment)), \
             mock.patch.object(mp, 'buscar_pagos_dia', return_value=[payment]), \
             mock.patch.object(mp, 'conciliar_cierre_mp', side_effect=cierre_de(self.cfg_a2)):
            with self.assertRaisesRegex(asoc.AsociacionError, 'administrador'):
                asoc.importar_y_asociar('179186819127', venta.id, self.cfg_a1.id, self.admin,
                                        recalcular_arqueo=False, sucursal_permitida=self.a1.id)
        with mock.patch.object(mp, '_request', return_value=_resp(payment)), \
             mock.patch.object(mp, 'buscar_pagos_dia', return_value=[payment]), \
             mock.patch.object(mp, 'conciliar_cierre_mp', side_effect=cierre_de(self.cfg_a1)):
            asoc.importar_y_asociar('179186819127', venta.id, self.cfg_a1.id, self.admin,
                                    recalcular_arqueo=False, sucursal_permitida=self.a1.id)
        venta.refresh_from_db()
        self.assertEqual(venta.voucher, '179186819127')

    def test_asoc_antigua_con_otra_fecha_no_calza_el_dia_del_cobro(self):
        venta = self._venta(self.a1, 10000)
        viejo = _transaccion(self.cfg_a1, correlativo=str(venta.ticket.correlativo), monto=10000,
                             external_reference='ASOC-555000000013', payment_id_mp='555000000013', consumida=True)
        TransaccionMercadoPago.objects.filter(pk=viejo.pk).update(creado_en=_a_las(15, 0, DIA + timedelta(days=4)))
        payment = _pago_mp(555000000013, 10000)
        res = mp.conciliar_cierre_mp(self.cfg_a1, DIA, pagos=[payment])
        # El día del cobro no queda una diferencia sin fila: el pago sale como «sin registro».
        self.assertEqual([x['payment_id'] for x in res['sin_registro']], ['555000000013'])
        self.assertEqual(res['sin_registro'][0]['hora'], '11:55')   # hora de Chile, no la -04:00 de MP

    def test_comando_repara_fecha_y_caja_de_asoc_antiguas(self):
        from django.core.management import call_command
        venta = self._venta(self.a2, 10000)
        viejo = _transaccion(self.cfg_a1, correlativo=str(venta.ticket.correlativo), monto=10000, ticket=venta.ticket,
                             external_reference='ASOC-555000000014', payment_id_mp='555000000014', consumida=True,
                             raw_response=_pago_mp(555000000014, 10000))
        call_command('reparar_asociaciones_mp_fecha')          # vista previa: no toca
        viejo.refresh_from_db()
        self.assertEqual(viejo.config_id, self.cfg_a1.id)
        call_command('reparar_asociaciones_mp_fecha', aplicar=True)
        viejo.refresh_from_db()
        self.assertEqual((viejo.config_id, viejo.sucursal_id), (self.cfg_a2.id, self.a2.id))
        self.assertEqual(timezone.localtime(viejo.creado_en).strftime('%Y-%m-%d %H:%M'), '2026-09-21 11:55')

    def test_pago_en_dos_cajas_de_la_misma_tienda_queda_en_esa_tienda(self):
        cfg_a1b = _config(self.a1, nombre='Caja 2', external_pos_id='PA1B', modo='POINT')
        hoy = timezone.localdate()
        cuando = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
        pagos = [_pago_mp(179000000099, 5000, cuando=cuando.isoformat())]

        def cierre(cfg, dia, pagos=None):
            sin = [{'payment_id': '179000000099', 'atribuible': True}] if cfg.sucursal_id == self.a1.id else []
            return {'ok': True, 'sistema_total': 0, 'mp_total': 0, 'diferencia': 0, 'cuadra': True,
                    'sin_registro': sin, 'sin_confirmar': []}
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos), \
             mock.patch('app.services.mercadopago_service.conciliar_cierre_mp', side_effect=cierre):
            res = conc.diferencias_contra_mp(hoy, hoy, sucursal_id=self.a1.id)
        self.assertEqual([(f['payment_id'], f['sucursal']) for f in res['sin_registro']], [('179000000099', 'A1')])
        self.assertIn(res['sin_registro'][0]['config_id'], (self.cfg_a1.id, cfg_a1b.id))

    def test_migracion_0235_solo_a_roles_con_conciliacion(self):
        import importlib
        from django.apps import apps as registro
        migracion = importlib.import_module('app.migrations.0235_asociar_pagos_mp_admin')
        PermisoRol.objects.filter(opcion_menu__codigo='asociar_pagos_mercadopago').update(puede_ver=False, puede_editar=False)
        PermisoRol.objects.filter(rol='administracion', opcion_menu__codigo='dineros_mercadopago').update(puede_ver=False)
        migracion.encender(registro, None)
        fila = dict(PermisoRol.objects.filter(opcion_menu__codigo='asociar_pagos_mercadopago')
                    .values_list('rol', 'puede_editar'))
        self.assertTrue(fila.get('administrador'))
        self.assertFalse(fila.get('administracion'))


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
@mock.patch.dict('os.environ', ENV_TEST)
class RevisionEndpointsTest(_Base):

    def _cliente(self, usuario, sucursal=None):
        if sucursal is not None:
            crear_empresa_user(usuario, sucursal.empresa, sucursal)
        c = Client()
        c.force_login(usuario)
        if sucursal is not None:
            s = c.session
            s['idSucursalActual'] = sucursal.id
            s.save()
        return c

    def test_lote_con_item_invalido_es_400(self):
        venta = self._venta(self.a1, 12990)
        c = self._cliente(self.admin, self.a1)
        r = c.post('/app/api/mercadopago/asociar/lote/',
                   json.dumps({'items': [{'payment_id': '1', 'pago_id': venta.id, 'config_id': self.cfg_a1.id}, 'x']}),
                   content_type='application/json', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 400)

    def test_sin_acceso_a_la_pantalla_no_usa_las_apis(self):
        from app.models import OpcionMenu
        PermisoRol.objects.update_or_create(
            rol='jefe_local', opcion_menu=OpcionMenu.objects.get(codigo='asociar_pagos_mercadopago'),
            defaults={'puede_ver': True, 'puede_editar': True})
        c = self._cliente(crear_usuario(username='jefe-sin-pantalla', rol='jefe_local'), self.a1)
        r = c.get('/app/api/mercadopago/asociar/pendientes/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)

    def test_no_admin_sin_tienda_en_la_sesion_no_ve_todas(self):
        from django.test import RequestFactory
        from app.views_mercadopago import _sucursal_filtro_conciliacion
        req = RequestFactory().get('/x')
        req.user = crear_usuario(username='jefe-sin-tienda', rol='jefe_local')
        req.session = {}
        self.assertEqual(_sucursal_filtro_conciliacion(req), -1)
