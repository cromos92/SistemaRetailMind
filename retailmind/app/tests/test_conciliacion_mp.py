"""
Conciliación Mercado Pago: cobros ↔ documentos, liberaciones → retiros,
cartola del banco y cruce contra la API (mockeada).

Correr en BD local desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_conciliacion_mp
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from app.models import (
    Dte, RetiroMercadoPago, Ticket, TicketDetallePago, TransaccionMercadoPago,
)
from app.services import conciliacion_mp_service as conc

from .factories import crear_empresa, crear_sucursal, crear_usuario, crear_vendedor
from .test_mercadopago_pos import ENV_TEST, _config, _transaccion


class _Base(TestCase):

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.config = _config(self.sucursal, nombre='Caja 1')
        self.hoy = timezone.localdate()

    def _ticket(self, correlativo, estado='PAGADO', folio=None, tipo_dte='TICKET', fallido=False):
        return Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
            estado=estado, subTotal=10000, descuento=0, total=10000, responsable='t',
            folio_dte=folio, tipo_dte=tipo_dte, dte_generacion_fallida=fallido,
        )

    def _cobro(self, correlativo, monto=10000, ticket=None, consumida=True, **kw):
        return _transaccion(self.config, correlativo=str(correlativo), monto=monto,
                            ticket=ticket, consumida=consumida, canal='POINT', **kw)


class CobrosVsDocumentosTest(_Base):

    def test_clasifica_cada_cobro(self):
        t_doc = self._ticket(1, folio=555, tipo_dte='BOLETA_ELECTRONICA')
        Dte.objects.create(
            emisor=self.empresa, numero_documento=555, tipo_documento='BOLETA ELECTRONICA',
            monto_con_iva=10000, monto_neto=8403, estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='t', fecha_emision=self.hoy, fecha_vencimiento=self.hoy, diasCredito=0,
            bultos=1, unidades_productos=1, sucursal=self.sucursal, referencias='TICKET-1',
        )
        self._cobro(1, ticket=t_doc, payment_id_mp='111')
        t_sin = self._ticket(2, tipo_dte='FACTURA_ELECTRONICA', fallido=True)
        self._cobro(2, ticket=t_sin, payment_id_mp='222')
        self._cobro(3, consumida=False, payment_id_mp='333')                 # sin venta
        t_otro = self._ticket(4, folio=556, tipo_dte='BOLETA_ELECTRONICA')   # pagada con otro medio
        self._cobro(4, ticket=None, consumida=False, payment_id_mp='334')
        self._cobro('DIRECTO-2209-101010', consumida=False, payment_id_mp='444')
        t_anul = self._ticket(5, estado='ANULADO')
        self._cobro(5, ticket=t_anul, payment_id_mp='555')
        t_man = self._ticket(6, folio=None)
        TicketDetallePago.objects.create(ticket=t_man, metodo_pago='MP_POINT', monto=7000,
                                         origen_pago='MANUAL', voucher='999')

        data = conc.cobros_vs_documentos(self.hoy, self.hoy)
        por_corr = {f['correlativo']: f for f in data['filas']}
        self.assertEqual(por_corr['1']['categoria'], conc.CAT_CON_DOCUMENTO)
        self.assertIn('555', por_corr['1']['documento'])
        self.assertEqual(por_corr['2']['categoria'], conc.CAT_SIN_DOCUMENTO)
        self.assertTrue(por_corr['2']['puede_reintentar_dte'])
        self.assertTrue(por_corr['2']['dte_fallido'])
        self.assertEqual(por_corr['3']['categoria'], conc.CAT_SIN_VENTA)
        self.assertEqual(por_corr['4']['categoria'], conc.CAT_SIN_VENTA)
        self.assertEqual(por_corr['4']['ticket_estado'], 'PAGADO')
        self.assertEqual(por_corr['DIRECTO-2209-101010']['categoria'], conc.CAT_COBRO_DIRECTO)
        self.assertEqual(por_corr['5']['categoria'], conc.CAT_TICKET_ANULADO)
        self.assertEqual(por_corr['6']['categoria'], conc.CAT_MANUAL)
        self.assertEqual(data['resumen'][conc.CAT_SIN_DOCUMENTO]['monto'], 10000)
        self.assertEqual(data['resumen'][conc.CAT_MANUAL]['monto'], 7000)
        # Lo urgente primero.
        self.assertEqual(data['filas'][0]['categoria'], conc.CAT_SIN_VENTA)

    def test_api_admin(self):
        admin = crear_usuario(username='adm_conc', rol='administrador')
        c = Client(); c.force_login(admin)
        s = c.session; s['idSucursalActual'] = self.sucursal.id; s.save()
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            r = c.get(reverse('api_conciliacion_cobros_mp'))
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()['success'])
            p = c.get(reverse('dineros_mercadopago'))
            self.assertEqual(p.status_code, 200)
            self.assertContains(p, 'Conciliación Mercado Pago')


REPORTE = (
    "DATE;SOURCE_ID;EXTERNAL_REFERENCE;RECORD_TYPE;DESCRIPTION;NET_CREDIT_AMOUNT;NET_DEBIT_AMOUNT\n"
    "2026-09-20T10:00:00.000-03:00;;;initial_available_balance;;0;0\n"
    "2026-09-20T12:00:00.000-03:00;111;RM-x;release;payment;9.700;0\n"
    "2026-09-20T13:00:00.000-03:00;888;;release;payment;4.850;0\n"
    "2026-09-21T09:00:00.000-03:00;W1;;release;payout;0;14.550\n"
    "2026-09-21T12:00:00.000-03:00;222;;release;payment;5.000;0\n"
    "2026-09-22T09:00:00.000-03:00;W2;;release;payout;0;6.000\n"
)


class LiberacionesYCartolaTest(_Base):

    def test_reporte_amarra_cobros_a_retiros(self):
        a = self._cobro(1, payment_id_mp='111')
        b = self._cobro(2, payment_id_mp='222')
        filas = conc.leer_csv(REPORTE.encode('utf-8'))
        previa = conc.procesar_reporte_liberaciones(filas, self.config, aplicar=False)
        self.assertEqual([r['estado'] for r in previa['retiros']], ['CONCILIADO', 'CON_DIFERENCIA'])
        self.assertEqual(previa['pagos_sin_local'], 1)       # el 888 no es del POS
        self.assertFalse(RetiroMercadoPago.objects.exists())  # dry-run

        conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True)
        w1 = RetiroMercadoPago.objects.get(withdrawal_id='W1')
        self.assertEqual((w1.monto, w1.estado), (14550, 'CONCILIADO'))
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=a.pk).retiro_id, w1.id)
        w2 = RetiroMercadoPago.objects.get(withdrawal_id='W2')
        self.assertEqual(w2.estado, 'CON_DIFERENCIA')
        self.assertIn('diferencia', w2.detalle_diferencia)
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=b.pk).retiro_id, w2.id)
        # Idempotente.
        conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True)
        self.assertEqual(RetiroMercadoPago.objects.count(), 2)

    def test_cartola_marca_visto(self):
        ret = RetiroMercadoPago.objects.create(config=self.config, withdrawal_id='W9',
                                               fecha=self.hoy, monto=1234567)
        manana = self.hoy + timedelta(days=1)
        cartola = (f"Fecha;Descripción;Cargos;Abonos;Saldo\n"
                   f"{manana:%d/%m/%Y};TRANSF DE MERCADO PAGO;;1.234.567;9.999.999\n"
                   f"{manana:%d/%m/%Y};OTRO ABONO;;50.000;0\n").encode('latin-1')
        movs = conc.leer_cartola(cartola)
        self.assertEqual(len(movs), 2)
        res = conc.conciliar_cartola(movs, aplicar=False)
        self.assertEqual([c['withdrawal_id'] for c in res['calzados']], ['W9'])
        ret.refresh_from_db(); self.assertFalse(ret.visto_en_cartola)
        conc.conciliar_cartola(movs, aplicar=True)
        ret.refresh_from_db(); self.assertTrue(ret.visto_en_cartola)

    def test_montos_y_fechas(self):
        self.assertEqual(conc._monto('1.234.567'), 1234567)
        self.assertEqual(conc._monto('1234.50'), 1235)
        self.assertEqual(conc._monto('-5.000'), -5000)
        self.assertEqual(conc._monto('$ 12.000'), 12000)
        self.assertEqual(str(conc._fecha_libre('21/09/2026')), '2026-09-21')
        self.assertEqual(str(conc._fecha_libre('2026-09-21T09:00:00.000-03:00')), '2026-09-21')


@mock.patch.dict('os.environ', ENV_TEST)
class ContraMercadoPagoTest(_Base):

    def test_pago_sin_registro_y_manual_sin_pago(self):
        self._cobro(1, payment_id_mp='111', external_reference='RM-1-1-c1i01')
        t_man = self._ticket(7)
        TicketDetallePago.objects.create(ticket=t_man, metodo_pago='MP_POINT_DEBITO', monto=3000,
                                         origen_pago='MANUAL', voucher='777')
        pagos_mp = [
            {'id': 111, 'status': 'approved', 'external_reference': 'RM-1-1-c1i01',
             'transaction_amount': 10000, 'payment_type_id': 'debit_card', 'date_created': '2026-09-22T10:00:00'},
            {'id': 999, 'status': 'approved', 'external_reference': '',
             'transaction_amount': 25000, 'payment_type_id': 'credit_card', 'date_created': '2026-09-22T11:00:00'},
        ]
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos_mp), \
             mock.patch('app.services.mercadopago_service.conciliar_cierre_mp',
                        return_value={'ok': True, 'sistema_total': 10000, 'mp_total': 35000,
                                      'diferencia': -25000, 'cuadra': False,
                                      'sin_registro': [{}], 'sin_confirmar': []}):
            res = conc.diferencias_contra_mp(self.hoy, self.hoy)
        self.assertEqual([p['payment_id'] for p in res['sin_registro']], ['999'])
        self.assertEqual([m['ticket'] for m in res['manuales_sin_pago']], [7])
        self.assertEqual(len(res['cajas']), 1)
        self.assertFalse(res['cajas'][0]['cuadra'])
        self.assertEqual(res['errores'], [])


REPORTE_PARCIAL = (
    "DATE,SOURCE_ID,EXTERNAL_REFERENCE,RECORD_TYPE,DESCRIPTION,NET_CREDIT_AMOUNT,NET_DEBIT_AMOUNT,GROSS_AMOUNT\n"
    "2026-09-22T00:00:00.000-04:00,,,initial_available_balance,,20000.00,0.00,20000.00\n"
    "2026-09-23T09:10:00.000-03:00,601,,release,payment,60000.00,0.00,61000.00\n"
    "2026-09-23T09:40:00.000-03:00,602,,release,payment,70000.00,0.00,71000.00\n"
    "2026-09-23T10:48:00.000-04:00,P-MANUAL,,release,payout,0.00,100000.00,-100000.00\n"
    "2026-09-23T10:48:30.000-04:00,T-1,,release,tax_withholding_payout,0.00,0.00,0.00\n"
    "2026-09-23T15:00:00.000-03:00,603,,release,payment,5000.00,0.00,5100.00\n"
    "2026-09-23T15:30:00.000-03:00,604,,release,refund,0.00,5000.00,-5000.00\n"
    "2026-09-24T02:00:00.000-03:00,P-NOCHE,,release,payout,0.00,50000.00,-50000.00\n"
)


class RetiroParcialTest(_Base):
    """Caso real 23-09: retiro manual de $100.000 (parte del saldo) y después
    el retiro automático de la noche con el resto."""

    def test_fifo_con_saldo_inicial_parcial_y_devolucion(self):
        a = self._cobro(1, payment_id_mp='601', monto=61000)
        b = self._cobro(2, payment_id_mp='602', monto=71000)
        filas = conc.leer_csv(REPORTE_PARCIAL)
        res = conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True, archivo='r1.csv')
        self.assertEqual([r['withdrawal_id'] for r in res['retiros']], ['P-MANUAL', 'P-NOCHE'])
        manual, noche = res['retiros']
        # 20.000 de saldo inicial + 60.000 + 20.000 de la venta 602 = 100.000
        self.assertEqual(manual['estado'], 'CONCILIADO')
        self.assertEqual(manual['quedan_disponibles'], 50000)
        self.assertIn('parcial', manual['detalle'].lower())
        # Quedaban 50.000 de la 602; entra 5.000 y sale 5.000 por devolución: 50.000
        self.assertEqual(noche['estado'], 'CONCILIADO')
        self.assertEqual(res['liberado_sin_retirar'], 0)
        r_manual = RetiroMercadoPago.objects.get(withdrawal_id='P-MANUAL')
        r_noche = RetiroMercadoPago.objects.get(withdrawal_id='P-NOCHE')
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=a.pk).retiro_id, r_manual.id)
        # La venta 602 queda en el retiro que se llevó su último peso.
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=b.pk).retiro_id, r_noche.id)
        self.assertIn('r1.csv', r_manual.raw_reporte['archivos'])
        # La retención sobre el retiro NO es un retiro.
        self.assertFalse(RetiroMercadoPago.objects.filter(withdrawal_id='T-1').exists())


def _resp(status, data=None, contenido=b''):
    r = mock.MagicMock()
    r.status_code = status
    r.content = contenido
    if data is None:
        r.json.side_effect = ValueError('sin json')
    else:
        r.json.return_value = data
    return r


@mock.patch.dict('os.environ', ENV_TEST)
class ApiReportesTest(_Base):

    def test_rango_utc_nunca_en_el_futuro_y_minimo_un_dia(self):
        from datetime import datetime as _dt
        hoy = timezone.localdate()
        begin, end = conc.rango_utc_reporte(hoy, hoy)
        self.assertTrue(begin.endswith('Z') and end.endswith('Z'))
        ahora_utc = timezone.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        self.assertLess(end, ahora_utc)
        d_b = _dt.strptime(begin, '%Y-%m-%dT%H:%M:%SZ')
        d_e = _dt.strptime(end, '%Y-%m-%dT%H:%M:%SZ')
        self.assertGreaterEqual((d_e - d_b).total_seconds(), 86400)

    def test_pedir_202_devuelve_tarea_y_203_es_error(self):
        with mock.patch('app.services.mercadopago_service._request',
                        return_value=_resp(202, {'id': 555, 'status': 'pending'})):
            t = conc.pedir_reporte_liberaciones(self.config, self.hoy, self.hoy)
        self.assertEqual(t['task_id'], 555)
        with mock.patch('app.services.mercadopago_service._request',
                        return_value=_resp(203, {'message': 'dates'})):
            with self.assertRaises(Exception) as ctx:
                conc.pedir_reporte_liberaciones(self.config, self.hoy, self.hoy)
        self.assertIn('NO creó', str(ctx.exception))

    def test_tarea_lista_solo_con_processed_y_file_name(self):
        with mock.patch('app.services.mercadopago_service._request',
                        return_value=_resp(200, {'status': 'processing', 'file_name': None})):
            self.assertFalse(conc.estado_tarea_liberaciones(self.config, 5)['listo'])
        with mock.patch('app.services.mercadopago_service._request',
                        return_value=_resp(200, {'status': 'processed', 'file_name': 'x.csv'})):
            self.assertTrue(conc.estado_tarea_liberaciones(self.config, 5)['listo'])

    def test_listar_usa_search_y_descarga(self):
        search = {'paging': {'total': 2}, 'results': [
            {'file_name': 'release-report-1-2026-09-23-120000.csv', 'begin_date': '2026-09-22T03:00:00Z',
             'end_date': '2026-09-23T14:00:00Z', 'date_created': '2026-09-23T12:00:00Z',
             'created_from': 'manual'},
            {'file_name': None, 'begin_date': 'x'},
        ]}
        with mock.patch('app.services.mercadopago_service._request',
                        return_value=_resp(200, search)) as m:
            reps = conc.listar_reportes_liberaciones(self.config)
        self.assertEqual([r['file_name'] for r in reps], ['release-report-1-2026-09-23-120000.csv'])
        self.assertIn('/search', m.call_args[0][2])
        with mock.patch('app.services.mercadopago_service._request',
                        return_value=_resp(200, None, contenido=b'DATE,SOURCE_ID\n')):
            self.assertEqual(conc.descargar_reporte_liberaciones(self.config, 'a-b_c.csv'),
                             b'DATE,SOURCE_ID\n')
        with self.assertRaises(Exception):
            conc.descargar_reporte_liberaciones(self.config, '../etc/passwd')

    def test_activar_por_retiro_crea_config_si_no_existe(self):
        llamadas = []

        def fake(config, metodo, path, json_body=None, **kw):
            llamadas.append((metodo, path, json_body))
            if metodo == 'GET':
                return _resp(404, {'message': 'not found'})
            return _resp(201, dict(json_body))
        with mock.patch('app.services.mercadopago_service._request', side_effect=fake):
            data = conc.activar_reporte_por_retiro(self.config)
        self.assertTrue(data['execute_after_withdrawal'])
        metodo, path, cuerpo = llamadas[-1]
        self.assertEqual((metodo, path), ('POST', '/v1/account/release_report/config'))
        claves = [c['key'] for c in cuerpo['columns']]
        for c in ('DATE', 'SOURCE_ID', 'RECORD_TYPE', 'DESCRIPTION', 'NET_DEBIT_AMOUNT'):
            self.assertIn(c, claves)
        self.assertTrue(cuerpo['include_withdrawal_at_end'])
        self.assertEqual(cuerpo['report_translation'], 'en')


def _csv(*filas):
    cab = "DATE,SOURCE_ID,EXTERNAL_REFERENCE,RECORD_TYPE,DESCRIPTION,NET_CREDIT_AMOUNT,NET_DEBIT_AMOUNT,GROSS_AMOUNT\n"
    return cab + ''.join(f + "\n" for f in filas)


class RevisionAdversarialTest(_Base):
    """Casos que encontró la revisión adversarial del 23-09."""

    def test_reproceso_desasocia_lo_que_el_retiro_no_se_llevo(self):
        p1 = self._cobro(1, payment_id_mp='701', monto=40000)
        p2 = self._cobro(2, payment_id_mp='702', monto=40000)
        p5 = self._cobro(5, payment_id_mp='705', monto=30000)
        tardio = _csv(
            "2026-09-23T00:00:00.000-03:00,,,initial_available_balance,,0.00,0.00,0.00",
            "2026-09-23T09:00:00.000-03:00,705,,release,payment,30000.00,0.00,30000.00",
            "2026-09-23T10:00:00.000-03:00,W1,,release,payout,0.00,100000.00,-100000.00",
        )
        res = conc.procesar_reporte_liberaciones(conc.leer_csv(tardio), self.config, aplicar=True, archivo='tardio.csv')
        self.assertEqual(res['retiros'][0]['estado'], 'CON_DIFERENCIA')
        self.assertIsNotNone(TransaccionMercadoPago.objects.get(pk=p5.pk).retiro_id)
        completo = _csv(
            "2026-09-22T00:00:00.000-03:00,,,initial_available_balance,,20000.00,0.00,20000.00",
            "2026-09-22T12:00:00.000-03:00,701,,release,payment,40000.00,0.00,40000.00",
            "2026-09-22T13:00:00.000-03:00,702,,release,payment,40000.00,0.00,40000.00",
            "2026-09-23T09:00:00.000-03:00,705,,release,payment,30000.00,0.00,30000.00",
            "2026-09-23T10:00:00.000-03:00,W1,,release,payout,0.00,100000.00,-100000.00",
        )
        res = conc.procesar_reporte_liberaciones(conc.leer_csv(completo), self.config, aplicar=True, archivo='completo.csv')
        self.assertEqual(res['retiros'][0]['estado'], 'CONCILIADO')
        w1 = RetiroMercadoPago.objects.get(withdrawal_id='W1')
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=p1.pk).retiro_id, w1.id)
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=p2.pk).retiro_id, w1.id)
        # La 705 sigue en MP: ya no figura depositada.
        self.assertIsNone(TransaccionMercadoPago.objects.get(pk=p5.pk).retiro_id)
        self.assertEqual(set(w1.raw_reporte['archivos']), {'tardio.csv', 'completo.csv'})
        self.assertEqual(conc.reportes_aplicados(), {'tardio.csv', 'completo.csv'})

    def test_devolucion_descuenta_de_la_misma_venta(self):
        p1 = self._cobro(1, payment_id_mp='801', monto=10000)
        p2 = self._cobro(2, payment_id_mp='802', monto=20000)
        reporte = _csv(
            "2026-09-23T09:00:00.000-03:00,801,,release,payment,10000.00,0.00,10000.00",
            "2026-09-23T09:30:00.000-03:00,802,,release,payment,20000.00,0.00,20000.00",
            "2026-09-23T10:00:00.000-03:00,802,,release,refund,0.00,20000.00,-20000.00",
            "2026-09-23T11:00:00.000-03:00,W2,,release,payout,0.00,10000.00,-10000.00",
        )
        res = conc.procesar_reporte_liberaciones(conc.leer_csv(reporte), self.config, aplicar=True)
        self.assertEqual(res['retiros'][0]['estado'], 'CONCILIADO')
        w2 = RetiroMercadoPago.objects.get(withdrawal_id='W2')
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=p1.pk).retiro_id, w2.id)
        self.assertIsNone(TransaccionMercadoPago.objects.get(pk=p2.pk).retiro_id)   # devuelta

    def test_comision_consume_el_frente_y_la_venta_va_al_retiro_siguiente(self):
        p1 = self._cobro(1, payment_id_mp='901', monto=1000)
        p2 = self._cobro(2, payment_id_mp='902', monto=20000)
        reporte = _csv(
            "2026-09-23T09:00:00.000-03:00,901,,release,payment,1000.00,0.00,1000.00",
            "2026-09-23T09:30:00.000-03:00,902,,release,payment,20000.00,0.00,20000.00",
            "2026-09-23T10:00:00.000-03:00,F1,,release,tax_withholding_payout,0.00,1000.00,-1000.00",
            "2026-09-23T11:00:00.000-03:00,W3,,release,payout,0.00,20000.00,-20000.00",
        )
        res = conc.procesar_reporte_liberaciones(conc.leer_csv(reporte), self.config, aplicar=True)
        self.assertEqual(res['retiros'][0]['estado'], 'CONCILIADO')
        w3 = RetiroMercadoPago.objects.get(withdrawal_id='W3')
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=p1.pk).retiro_id, w3.id)
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=p2.pk).retiro_id, w3.id)

    def test_saldo_inicial_negativo_se_paga_con_los_creditos(self):
        p1 = self._cobro(1, payment_id_mp='951', monto=5000)
        p2 = self._cobro(2, payment_id_mp='952', monto=15000)
        reporte = _csv(
            "2026-09-23T00:00:00.000-03:00,,,initial_available_balance,,0.00,5000.00,-5000.00",
            "2026-09-23T09:00:00.000-03:00,951,,release,payment,5000.00,0.00,5000.00",
            "2026-09-23T09:30:00.000-03:00,952,,release,payment,15000.00,0.00,15000.00",
            "2026-09-23T11:00:00.000-03:00,W4,,release,payout,0.00,15000.00,-15000.00",
        )
        res = conc.procesar_reporte_liberaciones(conc.leer_csv(reporte), self.config, aplicar=True)
        self.assertEqual(res['retiros'][0]['estado'], 'CONCILIADO')
        w4 = RetiroMercadoPago.objects.get(withdrawal_id='W4')
        self.assertIsNone(TransaccionMercadoPago.objects.get(pk=p1.pk).retiro_id)  # pagó la deuda
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=p2.pk).retiro_id, w4.id)

    def test_remanente_de_un_retiro_parcial_pasa_al_reporte_siguiente(self):
        from datetime import datetime as _dt
        a = self._cobro(1, payment_id_mp='611', monto=60000)
        b = self._cobro(2, payment_id_mp='612', monto=70000)
        tz = timezone.get_current_timezone()
        TransaccionMercadoPago.objects.filter(pk=a.pk).update(
            money_release_date=timezone.make_aware(_dt(2026, 9, 23, 9, 10), tz), monto_neto=60000)
        TransaccionMercadoPago.objects.filter(pk=b.pk).update(
            money_release_date=timezone.make_aware(_dt(2026, 9, 23, 9, 40), tz), monto_neto=70000)
        dia = _csv(
            "2026-09-23T09:10:00.000-03:00,611,,release,payment,60000.00,0.00,60000.00",
            "2026-09-23T09:40:00.000-03:00,612,,release,payment,70000.00,0.00,70000.00",
            "2026-09-23T11:48:00.000-03:00,P-MAN,,release,payout,0.00,100000.00,-100000.00",
        )
        conc.procesar_reporte_liberaciones(conc.leer_csv(dia), self.config, aplicar=True, archivo='dia.csv')
        self.assertIsNone(TransaccionMercadoPago.objects.get(pk=b.pk).retiro_id)   # aún en MP
        noche = _csv(
            "2026-09-23T12:00:00.000-03:00,,,initial_available_balance,,30000.00,0.00,30000.00",
            "2026-09-24T02:00:00.000-03:00,P-NOCHE,,release,payout,0.00,30000.00,-30000.00",
        )
        res = conc.procesar_reporte_liberaciones(conc.leer_csv(noche), self.config, aplicar=True, archivo='noche.csv')
        self.assertEqual(res['remanente_previo'], 30000)
        self.assertEqual(res['retiros'][0]['estado'], 'CONCILIADO')
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=b.pk).retiro.withdrawal_id, 'P-NOCHE')
        self.assertEqual(TransaccionMercadoPago.objects.get(pk=a.pk).retiro.withdrawal_id, 'P-MAN')


@mock.patch.dict('os.environ', ENV_TEST)
class CruceYCajaTest(_Base):
    """Primer reporte real (23-09): 174 pagos sin cruzar porque los cobros no
    tenían guardado el N° de operación de MP; y la pregunta «¿de qué caja es?»."""

    def test_completa_numero_de_operacion_y_cruza(self):
        t = self._cobro(1, monto=40000, external_reference='RM-1-1-c1i01')
        self.assertEqual(t.payment_id_mp, '')
        reporte = _csv(
            "2026-09-23T09:00:00.000-03:00,179000000001,,release,payment,39000.00,0.00,40000.00",
            "2026-09-23T11:48:00.000-03:00,W9,,release,payout,0.00,39000.00,-39000.00",
        )
        filas = conc.leer_csv(reporte)
        antes = conc.procesar_reporte_liberaciones(filas, self.config)
        self.assertEqual(antes['pagos_sin_local'], 1)
        self.assertEqual(antes['muestra_sin_local'][0]['source_id'], '179000000001')
        dias = conc.dias_a_completar(antes)
        self.assertIn(timezone.localdate().replace(year=2026, month=9, day=23), dias)
        pagos = [{'id': 179000000001, 'external_reference': 'RM-1-1-c1i01',
                  'transaction_details': {'net_received_amount': 39000.0}}]
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos):
            comp = conc.completar_numeros_mp(self.config, dias)
        self.assertEqual(comp['completados'], 1)
        t.refresh_from_db()
        self.assertEqual((t.payment_id_mp, t.monto_neto, t.fee_mp), ('179000000001', 39000, 1000))
        despues = conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True)
        self.assertEqual(despues['pagos_sin_local'], 0)
        self.assertEqual(despues['retiros'][0]['pagos_pos'], 1)
        self.assertEqual(despues['retiros'][0]['por_caja'][0]['monto'], 39000)
        self.assertIn('Caja 1', despues['retiros'][0]['por_caja'][0]['caja'])
        t.refresh_from_db()
        self.assertEqual(t.retiro.withdrawal_id, 'W9')

    def test_pago_mp_manual_y_caja_por_punto_de_venta(self):
        tk = self._ticket(3)
        TicketDetallePago.objects.create(ticket=tk, metodo_pago='MP_POINT', monto=5000,
                                         origen_pago='MANUAL', voucher='179000000002')
        self.config.external_pos_id = 'PAO4CAJA1'
        self.config.save(update_fields=['external_pos_id'])
        cab = "DATE,SOURCE_ID,EXTERNAL_REFERENCE,RECORD_TYPE,DESCRIPTION,NET_CREDIT_AMOUNT,NET_DEBIT_AMOUNT,EXTERNAL_POS_ID,POS_NAME\n"
        reporte = cab + (
            "2026-09-23T09:00:00.000-03:00,179000000002,,release,payment,5000.00,0.00,,\n"
            "2026-09-23T09:10:00.000-03:00,179000000003,,release,payment,7000.00,0.00,PAO4CAJA1,Caja 1\n"
            "2026-09-23T09:20:00.000-03:00,179000000004,,release,payment,3000.00,0.00,,\n"
            "2026-09-23T11:48:00.000-03:00,W10,,release,payout,0.00,15000.00,,\n"
        )
        res = conc.procesar_reporte_liberaciones(conc.leer_csv(reporte), self.config)
        self.assertEqual(res['pagos_manuales'], 1)
        self.assertEqual(res['pagos_sin_local'], 2)
        cajas = {c['caja']: c['monto'] for c in res['retiros'][0]['por_caja']}
        self.assertEqual(cajas[f'{self.sucursal.alias} · MP manual'], 5000)
        self.assertEqual(cajas[f'{self.sucursal.alias} · Caja 1'], 7000)
        self.assertEqual(cajas['Sin caja (online, link de pago u otro)'], 3000)


@mock.patch.dict('os.environ', ENV_TEST)
class DetectarRetirosTest(_Base):
    """Botón «Detectar retiros»: todas las cuentas, sin elegir reporte."""

    def test_aplica_solo_reportes_nuevos_con_retiros(self):
        t = self._cobro(1, payment_id_mp='611', monto=100000)
        con_retiro = _csv(
            "2026-09-23T09:10:00.000-03:00,611,,release,payment,100000.00,0.00,100000.00",
            "2026-09-23T11:48:00.000-03:00,179492400641,,release,payout,0.00,100000.00,-100000.00",
        ).encode()
        sin_retiro = _csv(
            "2026-09-23T09:10:00.000-03:00,999,,release,payment,5000.00,0.00,5000.00",
        ).encode()
        reportes = [{'file_name': 'b.csv', 'begin_date': '', 'end_date': '', 'creado': '2', 'origen': 'manual', 'estado': ''},
                    {'file_name': 'a.csv', 'begin_date': '', 'end_date': '', 'creado': '1', 'origen': 'manual', 'estado': ''}]
        archivos = {'a.csv': con_retiro, 'b.csv': sin_retiro}
        with mock.patch.object(conc, 'listar_reportes_liberaciones', return_value=reportes), \
             mock.patch.object(conc, 'descargar_reporte_liberaciones', side_effect=lambda c, f: archivos[f]), \
             mock.patch.object(conc, 'leer_config_reporte', return_value={'execute_after_withdrawal': False}):
            res = conc.detectar_retiros()
            self.assertEqual(len(res), 1)
            cuenta = res[0]
            self.assertEqual(cuenta['reportes_revisados'], 2)
            self.assertEqual(cuenta['reportes_aplicados'], 1)
            self.assertEqual([r['monto'] for r in cuenta['retiros']], [100000])
            self.assertFalse(cuenta['por_retiro_activo'])
            t.refresh_from_db()
            self.assertEqual(t.retiro.withdrawal_id, '179492400641')
            # Segunda pasada: el reporte con retiro ya está aplicado.
            res2 = conc.detectar_retiros()
            self.assertEqual(res2[0]['reportes_aplicados'], 0)
        self.assertEqual(RetiroMercadoPago.objects.count(), 1)

    def test_endpoint_solo_admin(self):
        vendedor = crear_usuario(username='vend_det', rol='vendedor')
        c = Client(); c.force_login(vendedor)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            r = c.post(reverse('api_conciliacion_detectar_retiros_mp'))
        self.assertEqual(r.status_code, 403)
