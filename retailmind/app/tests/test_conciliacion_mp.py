"""
Conciliación Mercado Pago: cobros ↔ documentos, liberaciones → retiros,
cartola del banco y cruce contra la API (mockeada).

Correr en BD local desechable:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_conciliacion_mp
"""
from datetime import timedelta, timezone as dt_timezone
from unittest import mock

from django.core.cache import cache
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
        cache.clear()   # pedidos de reporte y reportes sin retiros quedan en caché
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
             mock.patch.object(conc, 'leer_config_reporte', return_value={'execute_after_withdrawal': False}), \
             mock.patch.object(conc, 'pedir_reporte_liberaciones', return_value={'task_id': None, 'begin_date': '', 'end_date': ''}):
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


def _reporte(file_name, fin, creado=''):
    return {'file_name': file_name, 'begin_date': '', 'end_date': fin, 'creado': creado,
            'origen': 'manual', 'estado': ''}


def _utc_hace(**kw):
    return (timezone.now() - timedelta(**kw)).astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _local_hace(**kw):
    return timezone.localtime(timezone.now() - timedelta(**kw)).isoformat()


@mock.patch.dict('os.environ', ENV_TEST)
class DetectarPidiendoReporteTest(_Base):
    """23-09: retiro de $10.000.000 a las 15:33 y «Detectar retiros» decía «Sin
    retiros nuevos»: solo leía reportes ya generados y MP no genera uno tras el
    retiro si la cuenta no lo tiene activado. Ahora lo pide, la página lo
    espera y lo aplica."""

    PEDIDO = {'task_id': 77, 'begin_date': '2026-07-26T04:00:00Z', 'end_date': '2026-09-23T18:50:00Z',
              'estado': 'pending'}

    def _detectar(self, reportes, archivos=None, completar=None, **kw):
        archivos = archivos or {}
        with mock.patch.object(conc, 'listar_reportes_liberaciones', return_value=reportes), \
             mock.patch.object(conc, 'descargar_reporte_liberaciones', side_effect=lambda c, f: archivos[f]), \
             mock.patch.object(conc, 'leer_config_reporte', return_value={'execute_after_withdrawal': False}), \
             mock.patch.object(conc, 'estado_tarea_liberaciones', return_value={'fallido': False, 'listo': False}), \
             mock.patch.object(conc, 'completar_numeros_mp',
                               return_value=completar or {'dias': 0, 'completados': 0, 'sin_tiempo': False}) as comp, \
             mock.patch.object(conc, 'pedir_reporte_liberaciones',
                               return_value=dict(self.PEDIDO, end_date=_utc_hace(minutes=5))) as pedir:
            return conc.detectar_retiros(**kw), pedir, comp

    def test_pide_reporte_si_el_ultimo_quedo_viejo_y_no_lo_repite(self):
        res, pedir, _c = self._detectar([_reporte('viejo.csv', _utc_hace(hours=3))], {'viejo.csv': _csv().encode()})
        self.assertEqual(pedir.call_count, 1)
        _cfg, desde, hasta = pedir.call_args[0]
        self.assertEqual(hasta, timezone.localdate())
        self.assertEqual(desde, timezone.localdate() - timedelta(days=conc.DIAS_MAX_REPORTE))
        self.assertEqual(res[0]['pedido']['task_id'], 77)
        self.assertTrue(res[0]['pedido']['hasta'])
        self.assertTrue(res[0]['revisado_hasta'])
        # Otra pasada mientras MP lo genera: no se pide otro.
        res2, pedir2, _c = self._detectar([_reporte('viejo.csv', _utc_hace(hours=3))])
        self.assertEqual(pedir2.call_count, 0)
        self.assertEqual(res2[0]['pedido']['task_id'], 77)

    def test_no_pide_si_el_ultimo_reporte_esta_al_dia(self):
        res, pedir, _c = self._detectar([_reporte('nuevo.csv', _utc_hace(minutes=6))],
                                        archivos={'nuevo.csv': _csv().encode()})
        self.assertEqual(pedir.call_count, 0)
        self.assertIsNone(res[0]['pedido'])
        self.assertTrue(res[0]['revisado_hasta'])

    def test_sin_reportes_pide_y_con_pedir_false_no(self):
        _r, pedir, _c = self._detectar([])
        self.assertEqual(pedir.call_count, 1)
        cache.clear()
        _r, pedir, _c = self._detectar([], pedir=False)
        self.assertEqual(pedir.call_count, 0)

    def test_inicio_del_reporte_es_el_dia_del_ultimo_retiro_de_la_cuenta(self):
        hoy = timezone.localdate()
        tope = hoy - timedelta(days=conc.DIAS_MAX_REPORTE)
        self.assertEqual(conc.inicio_reporte_cuenta(self.config), tope)
        RetiroMercadoPago.objects.create(config=self.config, withdrawal_id='W-VIEJO', fecha=hoy - timedelta(days=90), monto=1)
        self.assertEqual(conc.inicio_reporte_cuenta(self.config), tope)
        RetiroMercadoPago.objects.create(config=self.config, withdrawal_id='W-1', fecha=hoy - timedelta(days=3), monto=1)
        self.assertEqual(conc.inicio_reporte_cuenta(self.config), hoy - timedelta(days=3))
        # Un retiro de OTRA cuenta no cuenta.
        ajena = _config(crear_sucursal(empresa=crear_empresa(nombre='Ajena', rut='77.111.111-1'), alias='AJ1'),
                        nombre='Ajena', external_pos_id='POSAJ')
        RetiroMercadoPago.objects.create(config=ajena, withdrawal_id='W-AJENA', fecha=hoy, monto=1)
        self.assertEqual(conc.inicio_reporte_cuenta(self.config), hoy - timedelta(days=3))

    def test_si_falto_tiempo_aplica_pero_no_marca_aplicado(self):
        t = self._cobro(1, monto=40000, external_reference='RM-1-1-c1i01')   # sin N° de operación de MP
        TransaccionMercadoPago.objects.filter(pk=t.pk).update(creado_en=timezone.now() - timedelta(hours=3))
        otro = self._cobro(2, monto=5000, external_reference='RM-1-2-c1i01')  # sigue sin cruzar
        TransaccionMercadoPago.objects.filter(pk=otro.pk).update(creado_en=timezone.now() - timedelta(hours=3))
        archivo = _csv(
            f"{_local_hace(hours=2)},179000000001,RM-1-1-c1i01,release,payment,39000.00,0.00,40000.00",
            f"{_local_hace(hours=1)},W10M,,release,payout,0.00,39000.00,-39000.00",
        ).encode()
        reportes = [_reporte('r.csv', _utc_hace(minutes=2))]
        res, _p, comp = self._detectar(reportes, {'r.csv': archivo},
                                       completar={'dias': 1, 'completados': 0, 'sin_tiempo': True})
        self.assertEqual(comp.call_count, 1)
        self.assertTrue(res[0]['incompleto'])
        self.assertEqual([r['monto'] for r in res[0]['retiros']], [39000])
        self.assertNotIn('r.csv', conc.reportes_aplicados())     # la próxima pasada lo rehace
        t.refresh_from_db()
        self.assertEqual(t.retiro.withdrawal_id, 'W10M')          # pero el retiro ya se ve
        res2, _p, _c = self._detectar(reportes, {'r.csv': archivo})
        self.assertFalse(res2[0]['incompleto'])
        self.assertIn('r.csv', conc.reportes_aplicados())
        self.assertEqual(RetiroMercadoPago.objects.count(), 1)

    def test_cajas_con_cuenta_automatica_son_de_la_misma_cuenta(self):
        from app.models import MercadoPagoCuenta
        MercadoPagoCuenta.objects.create(empresa=self.empresa, activo=True)
        c2 = _config(crear_sucursal(empresa=self.empresa, alias='SUC-2'), nombre='Caja 2', external_pos_id='POS002')
        ajena = _config(crear_sucursal(empresa=crear_empresa(nombre='Ajena', rut='77.111.111-1'), alias='AJ1'),
                        nombre='Ajena', external_pos_id='POSAJ')
        self.assertEqual(sorted(conc._configs_de_la_cuenta(self.config)), sorted([self.config.id, c2.id]))
        self.assertEqual(sorted(conc._configs_de_la_cuenta(c2)), sorted([self.config.id, c2.id]))
        self.assertEqual(conc._configs_de_la_cuenta(ajena), [ajena.id])

    def test_dias_de_cobros_sin_numero(self):
        sin_num = self._cobro(1, monto=5000)
        con_num = self._cobro(2, monto=5000, payment_id_mp='123')
        creado = timezone.now() - timedelta(days=2)
        TransaccionMercadoPago.objects.filter(pk__in=[sin_num.pk, con_num.pk]).update(creado_en=creado)
        resultado = {'rango': [_local_hace(days=1), _local_hace(minutes=1)]}
        dias = conc.dias_cobros_sin_numero(self.config, resultado)
        self.assertIn(timezone.localtime(creado).date(), dias)
        self.assertLessEqual(len(dias), 2)
        self.assertEqual(conc.dias_cobros_sin_numero(self.config, {'rango': []}), [])

    def test_aplicar_guarda_liberacion_y_desglose_y_la_tabla_los_muestra(self):
        t = self._cobro(1, payment_id_mp='611', monto=100000)
        filas = conc.leer_csv(_csv(
            "2026-09-23T09:10:00.000-03:00,611,,release,payment,100000.00,0.00,100000.00",
            "2026-09-23T15:33:00.000-03:00,W1,,release,payout,0.00,100000.00,-100000.00",
        ).encode())
        conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True, archivo='r.csv')
        t.refresh_from_db()
        self.assertEqual(t.money_release_date, conc._instante('2026-09-23T09:10:00.000-03:00'))
        retiro = RetiroMercadoPago.objects.get(withdrawal_id='W1')
        self.assertEqual(retiro.raw_reporte['por_caja'][0]['monto'], 100000)
        admin = crear_usuario(username='adm_ret', rol='administrador')
        c = Client(); c.force_login(admin)
        s = c.session; s['idSucursalActual'] = self.sucursal.id; s.save()
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            r = c.get(reverse('api_dineros_mercadopago'))
        fila = r.json()['retiros'][0]
        self.assertEqual(fila['hora'], '15:33')
        self.assertEqual(fila['transacciones'], 1)
        self.assertEqual(fila['por_caja'][0]['monto'], 100000)
        self.assertEqual(fila['cuenta'], self.empresa.nombre)

    def test_completar_guarda_fecha_de_liberacion(self):
        t = self._cobro(1, monto=40000, external_reference='RM-1-1-c1i01')
        devuelto = self._cobro(2, monto=9000, external_reference='RM-1-2-c1i01')
        pagos = [{'id': 179000000001, 'external_reference': 'RM-1-1-c1i01', 'status': 'approved',
                  'money_release_date': '2026-09-24T10:00:00.000-03:00',
                  'transaction_details': {'net_received_amount': 39000.0}},
                 {'id': 179000000002, 'external_reference': 'RM-1-2-c1i01', 'status': 'refunded',
                  'money_release_date': '2026-09-24T10:00:00.000-03:00',
                  'transaction_details': {'net_received_amount': 8700.0}}]
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos):
            conc.completar_numeros_mp(self.config, [self.hoy])
        t.refresh_from_db()
        devuelto.refresh_from_db()
        self.assertEqual(t.payment_id_mp, '179000000001')
        self.assertEqual(t.money_release_date, conc._instante('2026-09-24T10:00:00.000-03:00'))
        # Devuelto en MP: se completa el N° pero no se da por liberado.
        self.assertEqual(devuelto.payment_id_mp, '179000000002')
        self.assertIsNone(devuelto.money_release_date)


@mock.patch.dict('os.environ', ENV_TEST)
class RevisionDetectarTest(_Base):
    """Revisión adversarial del 23-09 sobre «Detectar retiros» (16 confirmados)."""

    R1 = "2026-09-21T01:00:00.000-03:00"

    def _reporte1(self):
        # S se libera el 20 a las 20:00 y el retiro R1 del 21 a la 01:00 se la lleva.
        return conc.leer_csv(_csv(
            "2026-09-20T20:00:00.000-03:00,500,,release,payment,50000.00,0.00,50000.00",
            f"{self.R1},R1,,release,payout,0.00,50000.00,-50000.00",
        ).encode())

    def _reporte2(self):
        # Pedido desde el día de R1: el saldo inicial (50.000) es lo que R1 se llevó.
        return conc.leer_csv(_csv(
            "2026-09-21T00:00:00.000-03:00,,,initial_available_balance,,50000.00,0.00,50000.00",
            f"{self.R1},R1,,release,payout,0.00,50000.00,-50000.00",
            "2026-09-21T10:00:00.000-03:00,611,,release,payment,30000.00,0.00,30000.00",
            "2026-09-21T20:00:00.000-03:00,R2,,release,payout,0.00,30000.00,-30000.00",
        ).encode())

    def _viejas(self):
        viejas = []
        for i, (pid, dias) in enumerate((('600', 10), ('601', 11))):
            v = self._cobro(90 + i, monto=600 + i, payment_id_mp=pid)
            v.money_release_date = conc._instante(self.R1) - timedelta(days=dias)
            v.save(update_fields=['money_release_date'])
            viejas.append(v)
        return viejas

    def test_reprocesar_desde_el_dia_del_retiro_no_le_suma_ventas_ajenas(self):
        s_ = self._cobro(1, monto=50000, payment_id_mp='500')
        t = self._cobro(2, monto=30000, payment_id_mp='611')
        viejas = self._viejas()
        r1 = conc.procesar_reporte_liberaciones(self._reporte1(), self.config, aplicar=True, archivo='a.csv')
        self.assertTrue(r1['retiros'][0]['nuevo'])
        caja_r1 = RetiroMercadoPago.objects.get(withdrawal_id='R1').raw_reporte['por_caja']
        for _ in range(3):   # cada «Detectar» traía de nuevo R1 y le sumaba otra venta vieja
            res = conc.procesar_reporte_liberaciones(self._reporte2(), self.config, aplicar=True, archivo='b.csv')
            self.assertFalse(res['retiros'][0]['nuevo'])
            self.assertTrue(res['retiros'][1]['nuevo'] if _ == 0 else True)
        r1 = RetiroMercadoPago.objects.get(withdrawal_id='R1')
        self.assertEqual(sorted(r1.transacciones.values_list('payment_id_mp', flat=True)), ['500'])
        self.assertEqual(r1.raw_reporte['por_caja'], caja_r1)
        self.assertEqual(r1.raw_reporte['pagos_pos'], 1)
        t.refresh_from_db()
        self.assertEqual(t.retiro.withdrawal_id, 'R2')
        for v in viejas:
            v.refresh_from_db()
            self.assertIsNone(v.retiro_id)
        s_.refresh_from_db()
        self.assertEqual(s_.retiro_id, r1.id)

    def test_si_el_reporte_explica_peor_el_saldo_se_conserva_el_desglose(self):
        s_ = self._cobro(1, monto=50000, payment_id_mp='500')
        conc.procesar_reporte_liberaciones(self._reporte1(), self.config, aplicar=True, archivo='a.csv')
        antes = RetiroMercadoPago.objects.get(withdrawal_id='R1').raw_reporte['por_caja']
        # Sin fecha de liberación la venta no puede rearmar el saldo: quedaría «Saldo anterior».
        TransaccionMercadoPago.objects.filter(pk=s_.pk).update(money_release_date=None)
        res = conc.procesar_reporte_liberaciones(self._reporte2(), self.config, aplicar=True, archivo='b.csv')
        r1 = RetiroMercadoPago.objects.get(withdrawal_id='R1')
        self.assertEqual(r1.raw_reporte['por_caja'], antes)
        self.assertEqual(res['retiros'][0]['por_caja'], antes)

    def test_venta_devuelta_en_el_reporte_no_queda_con_fecha_de_liberacion(self):
        p_ = self._cobro(1, monto=20000, payment_id_mp='801')
        q = self._cobro(2, monto=10000, payment_id_mp='802')
        filas = conc.leer_csv(_csv(
            "2026-09-22T09:00:00.000-03:00,801,,release,payment,20000.00,0.00,20000.00",
            "2026-09-22T09:30:00.000-03:00,802,,release,payment,10000.00,0.00,10000.00",
            "2026-09-22T10:00:00.000-03:00,801,,release,refund,0.00,20000.00,-20000.00",
            "2026-09-22T20:00:00.000-03:00,W1,,release,payout,0.00,10000.00,-10000.00",
        ).encode())
        conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True, archivo='c.csv')
        p_.refresh_from_db()
        q.refresh_from_db()
        self.assertIsNone(p_.money_release_date)
        self.assertIsNone(p_.retiro_id)
        self.assertIsNotNone(q.money_release_date)
        self.assertEqual(q.retiro.withdrawal_id, 'W1')

    def test_rango_nunca_pasa_de_60_dias(self):
        from datetime import datetime as _dt
        hoy = timezone.localdate()
        begin, end = conc.rango_utc_reporte(hoy - timedelta(days=75), hoy)
        largo = _dt.strptime(end, '%Y-%m-%dT%H:%M:%SZ') - _dt.strptime(begin, '%Y-%m-%dT%H:%M:%SZ')
        self.assertLess(largo, timedelta(days=60))

    def test_error_de_red_en_payments_search_no_marca_el_reporte_aplicado(self):
        from app.services.mercadopago_service import MercadoPagoError
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia',
                        side_effect=MercadoPagoError('HTTP 429', red=True)):
            comp = conc.completar_numeros_mp(self.config, [self.hoy, self.hoy - timedelta(days=1),
                                                           self.hoy - timedelta(days=2)])
        self.assertGreaterEqual(comp['fallidos'], 1)
        self.assertFalse(comp['sin_tiempo'])

    def _detectar(self, reportes, archivos=None, estado=None, **kw):
        archivos = archivos or {}
        with mock.patch.object(conc, 'listar_reportes_liberaciones', return_value=reportes), \
             mock.patch.object(conc, 'descargar_reporte_liberaciones', side_effect=lambda c, f: archivos[f]) as bajar, \
             mock.patch.object(conc, 'leer_config_reporte', return_value={'execute_after_withdrawal': True}), \
             mock.patch.object(conc, 'estado_tarea_liberaciones',
                               return_value=estado or {'fallido': False, 'listo': False, 'file_name': ''}), \
             mock.patch.object(conc, 'completar_numeros_mp',
                               return_value={'dias': 0, 'completados': 0, 'sin_tiempo': False, 'fallidos': 0}), \
             mock.patch.object(conc, 'pedir_reporte_liberaciones',
                               return_value={'task_id': 88, 'begin_date': _utc_hace(days=1),
                                             'end_date': _utc_hace(minutes=5)}) as pedir:
            return conc.detectar_retiros(**kw), pedir, bajar

    def test_reporte_terminado_que_search_no_lista_se_aplica_sin_pedir_otro(self):
        t = self._cobro(1, monto=39000, payment_id_mp='700')
        cache.set(conc._CLAVE_PEDIDO.format(self.config.id),
                  {'task_id': 77, 'begin_date': _utc_hace(days=1), 'end_date': _utc_hace(minutes=25)}, 600)
        archivo = _csv(
            f"{_local_hace(hours=2)},700,,release,payment,39000.00,0.00,39000.00",
            f"{_local_hace(minutes=40)},W7,,release,payout,0.00,39000.00,-39000.00",
        ).encode()
        res, pedir, bajar = self._detectar([_reporte('viejo.csv', _utc_hace(hours=5))],
                                           {'viejo.csv': _csv().encode(), 'listo.csv': archivo},
                                           estado={'listo': True, 'fallido': False, 'file_name': 'listo.csv'})
        self.assertEqual(pedir.call_count, 0)       # llegó (aunque ya tenga 25 min): no se pide otro
        self.assertIsNone(res[0]['pedido'])
        self.assertIn('listo.csv', [c.args[1] for c in bajar.call_args_list])
        t.refresh_from_db()
        self.assertEqual(t.retiro.withdrawal_id, 'W7')
        self.assertIsNone(cache.get(conc._CLAVE_PEDIDO.format(self.config.id)))

    def test_pedido_que_ya_aparece_en_la_lista_se_da_por_llegado(self):
        fin = _utc_hace(minutes=30)
        cache.set(conc._CLAVE_PEDIDO.format(self.config.id),
                  {'task_id': 77, 'begin_date': _utc_hace(days=1), 'end_date': fin}, 600)
        res, pedir, _b = self._detectar([_reporte('llego.csv', fin)], {'llego.csv': _csv().encode()})
        self.assertEqual(pedir.call_count, 0)
        self.assertIsNone(res[0]['pedido'])
        self.assertIsNone(cache.get(conc._CLAVE_PEDIDO.format(self.config.id)))
        # En el clic siguiente, con el reporte viejo, sí se pide uno nuevo.
        _r, pedir2, _b = self._detectar([_reporte('llego.csv', fin)], {'llego.csv': _csv().encode()})
        self.assertEqual(pedir2.call_count, 1)

    def test_vuelta_automatica_no_pide_reportes(self):
        admin = crear_usuario(username='adm_det2', rol='administrador')
        c = Client(); c.force_login(admin)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True), \
             mock.patch.object(conc, 'detectar_retiros', return_value=[]) as det:
            c.post(reverse('api_conciliacion_detectar_retiros_mp'), {'pedir': '0'})
            c.post(reverse('api_conciliacion_detectar_retiros_mp'))
        self.assertEqual([k.kwargs['pedir'] for k in det.call_args_list], [False, True])


@mock.patch.dict('os.environ', ENV_TEST)
class DistinguirRetiroTest(_Base):
    """23-09: «¿a qué lo concilio? no se distingue». El retiro de $10.000.000
    quedó como «Saldo anterior» y el KPI «Depositado» en $0 aunque la plata de MP
    era casi toda de ventas del POS: las ventas no tenían fecha de liberación."""

    W = "2026-09-23T15:33:00.000-03:00"

    def _reporte(self):
        return conc.leer_csv(_csv(
            "2026-09-23T00:00:00.000-03:00,,,initial_available_balance,,15000.00,0.00,15000.00",
            f"{self.W},W10M,,release,payout,0.00,10000.00,-10000.00",
        ).encode())

    def test_retiro_saldo_anterior_se_reasigna_cuando_las_ventas_tienen_fecha(self):
        ventas = [self._cobro(i + 1, monto=m, payment_id_mp=str(700 + i), monto_neto=m)
                  for i, m in enumerate((6000, 5000, 4000))]
        conc.procesar_reporte_liberaciones(self._reporte(), self.config, aplicar=True, archivo='a.csv')
        w = RetiroMercadoPago.objects.get(withdrawal_id='W10M')
        self.assertEqual(w.raw_reporte['por_caja'], [{'caja': 'Saldo anterior', 'monto': 10000}])
        self.assertEqual(w.transacciones.count(), 0)
        # completar_numeros_mp les trae la fecha de liberación desde MP.
        base = conc._instante(self.W)
        for v, dias in zip(ventas, (5, 4, 3)):
            TransaccionMercadoPago.objects.filter(pk=v.pk).update(money_release_date=base - timedelta(days=dias))
        conc.procesar_reporte_liberaciones(self._reporte(), self.config, aplicar=True, archivo='b.csv')
        w.refresh_from_db()
        self.assertEqual(w.raw_reporte['por_caja'][0]['monto'], 10000)
        self.assertIn('Caja 1', w.raw_reporte['por_caja'][0]['caja'])
        self.assertEqual(list(w.transacciones.values_list('payment_id_mp', flat=True)), ['700'])   # la más antigua

    def test_completar_trae_la_fecha_de_cobros_que_ya_tenian_numero(self):
        t = self._cobro(1, monto=40000, external_reference='RM-1-1-c1i01', payment_id_mp='179000000001')
        otro = self._cobro(2, monto=9000, external_reference='RM-1-2-c1i01', payment_id_mp='555')
        pagos = [{'id': 179000000001, 'external_reference': 'RM-1-1-c1i01', 'status': 'approved',
                  'money_release_date': '2026-09-24T10:00:00.000-03:00',
                  'transaction_details': {'net_received_amount': 39000.0}},
                 {'id': 179000000009, 'external_reference': 'RM-1-2-c1i01', 'status': 'approved',
                  'money_release_date': '2026-09-24T10:00:00.000-03:00'}]
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos):
            comp = conc.completar_numeros_mp(self.config, [self.hoy])
        t.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual(t.money_release_date, conc._instante('2026-09-24T10:00:00.000-03:00'))
        self.assertEqual(t.monto_neto, 39000)
        self.assertEqual(comp['completados'], 0)           # el N° ya estaba
        self.assertIsNone(otro.money_release_date)          # otro pago (reintento): no se mezcla
        self.assertEqual(otro.payment_id_mp, '555')
        # Y el día entra a la lista aunque el cobro ya tenga N°.
        TransaccionMercadoPago.objects.filter(pk=otro.pk).update(creado_en=timezone.now() - timedelta(hours=2))
        dias = conc.dias_cobros_sin_numero(self.config, {'rango': [_local_hace(hours=1), _local_hace(minutes=1)]})
        self.assertTrue(dias)

    def test_detalle_de_un_retiro_muestra_ventas_documento_y_mp_manual(self):
        tk = self._ticket(1, folio=555, tipo_dte='BOLETA_ELECTRONICA')
        Dte.objects.create(
            emisor=self.empresa, numero_documento=555, tipo_documento='BOLETA ELECTRONICA',
            monto_con_iva=10000, monto_neto=8403, estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='t', fecha_emision=self.hoy, fecha_vencimiento=self.hoy, diasCredito=0,
            bultos=1, unidades_productos=1, sucursal=self.sucursal, referencias='TICKET-1',
        )
        retiro = RetiroMercadoPago.objects.create(
            config=self.config, withdrawal_id='W1', fecha=self.hoy, monto=20000, estado='CONCILIADO',
            raw_reporte={'pagos': ['111', '999'], 'netos': {'999': 4850},
                         'por_caja': [{'caja': 'SUC-TEST · Caja 1', 'monto': 9700},
                                      {'caja': 'Saldo anterior', 'monto': 5300}]})
        self._cobro(1, ticket=tk, payment_id_mp='111', monto_neto=9700, retiro=retiro)
        tk_man = self._ticket(2)
        TicketDetallePago.objects.create(ticket=tk_man, metodo_pago='MP_POINT', monto=5000,
                                         origen_pago='MANUAL', voucher='999')
        d = conc.detalle_retiro(retiro)
        self.assertEqual(len(d['filas']), 2)
        pos = next(f for f in d['filas'] if f['origen'] == 'POS')
        self.assertIn('555', pos['documento'])
        self.assertEqual((pos['bruto'], pos['comision'], pos['neto'], pos['ticket']), (10000, 300, 9700, 1))
        manual = next(f for f in d['filas'] if f['origen'] == 'MP manual')
        self.assertEqual((manual['bruto'], manual['neto'], manual['comision']), (5000, 4850, 150))
        self.assertEqual(d['suma_neto'], 14550)
        self.assertEqual(d['sin_venta'], 5300)
        self.assertEqual(d['no_ventas'], [{'caja': 'Saldo anterior', 'monto': 5300}])
        admin = crear_usuario(username='adm_det3', rol='administrador')
        vend = crear_usuario(username='vend_det3', rol='vendedor')
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            c = Client(); c.force_login(admin)
            self.assertEqual(c.get(reverse('api_conciliacion_retiro_detalle_mp', args=['W1'])).json()['suma_neto'], 14550)
            self.assertEqual(c.get(reverse('api_conciliacion_retiro_detalle_mp', args=['NO'])).status_code, 404)
            c2 = Client(); c2.force_login(vend)
            self.assertEqual(c2.get(reverse('api_conciliacion_retiro_detalle_mp', args=['W1'])).status_code, 403)

    def test_cuadre_por_tienda_y_kpis_en_el_mismo_rango(self):
        retiro = RetiroMercadoPago.objects.create(config=self.config, withdrawal_id='W1', fecha=self.hoy, monto=9700)
        self._cobro(1, monto=10000, monto_neto=9700, payment_id_mp='1', retiro=retiro)
        self._cobro(2, monto=5000, monto_neto=4850, payment_id_mp='2',
                    money_release_date=timezone.now() - timedelta(hours=1))
        self._cobro(3, monto=3000, estado='DEVUELTA', payment_id_mp='3')
        self._cobro(4, monto=2000, monto_neto=1940, payment_id_mp='4',
                    money_release_date=timezone.now() + timedelta(days=2))
        viejo = self._cobro(5, monto=99000, payment_id_mp='5')
        TransaccionMercadoPago.objects.filter(pk=viejo.pk).update(creado_en=timezone.now() - timedelta(days=20))
        c = conc.cuadre_por_sucursal(None, None)
        tot = c['total']
        self.assertEqual((tot['cobros'], tot['vendido'], tot['devuelto']), (4, 20000, 3000))
        self.assertEqual((tot['comision'], tot['neto']), (510, 16490))
        self.assertEqual((tot['en_banco'], tot['en_mp'], tot['por_liberar']), (9700, 4850, 1940))
        admin = crear_usuario(username='adm_kpi', rol='administrador')
        cl = Client(); cl.force_login(admin)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            data = cl.get(reverse('api_dineros_mercadopago')).json()
        k = data['kpis']
        self.assertEqual(k['cantidad_cobros'], 3)            # el de hace 20 días queda fuera (7 días, como Cobros)
        self.assertEqual(k['depositado_neto'], 9700)
        self.assertEqual(k['liberado_sin_retirar_neto'], 4850)
        self.assertEqual(k['pendiente_liberacion_neto'], 1940)
        self.assertEqual(data['cuadre']['total']['neto'], 16490)


@mock.patch.dict('os.environ', ENV_TEST)
class RevisionFixDetectarTest(_Base):
    """Segunda revisión adversarial (23-09): retiros cerrados no se recalculan,
    piso FIFO, devoluciones, pedidos sin depender de la caché del worker."""

    R1 = "2026-09-21T01:00:00.000-03:00"

    def _viejo(self, correlativo, pid, monto, dias_antes):
        v = self._cobro(correlativo, monto=monto, payment_id_mp=pid)
        TransaccionMercadoPago.objects.filter(pk=v.pk).update(
            money_release_date=conc._instante(self.R1) - timedelta(days=dias_antes))
        return v

    def test_retiro_parcial_cerrado_no_pasa_su_venta_al_siguiente(self):
        a = self._cobro(1, monto=10000, payment_id_mp='701')
        b = self._cobro(2, monto=10000, payment_id_mp='702')
        t = self._cobro(3, monto=30000, payment_id_mp='611')
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            "2026-09-20T10:00:00.000-03:00,701,,release,payment,10000.00,0.00,10000.00",
            "2026-09-20T11:00:00.000-03:00,702,,release,payment,10000.00,0.00,10000.00",
            "2026-09-20T12:00:00.000-03:00,888,,release,payment,5000.00,0.00,5000.00",
            f"{self.R1},R1,,release,payout,0.00,20000.00,-20000.00",
        ).encode()), self.config, aplicar=True, archivo='a.csv')
        antes = RetiroMercadoPago.objects.get(withdrawal_id='R1').raw_reporte['por_caja']
        dia_r1 = conc.leer_csv(_csv(
            "2026-09-21T00:00:00.000-03:00,,,initial_available_balance,,25000.00,0.00,25000.00",
            f"{self.R1},R1,,release,payout,0.00,20000.00,-20000.00",
            "2026-09-21T10:00:00.000-03:00,611,,release,payment,30000.00,0.00,30000.00",
            "2026-09-21T20:00:00.000-03:00,R2,,release,payout,0.00,35000.00,-35000.00",
        ).encode())
        for _ in range(2):
            conc.procesar_reporte_liberaciones(dia_r1, self.config, aplicar=True, archivo='b.csv')
        for x in (a, b, t):
            x.refresh_from_db()
        self.assertEqual((a.retiro.withdrawal_id, b.retiro.withdrawal_id, t.retiro.withdrawal_id), ('R1', 'R1', 'R2'))
        self.assertEqual(RetiroMercadoPago.objects.get(withdrawal_id='R1').raw_reporte['por_caja'], antes)

    def test_hueco_online_no_se_rellena_con_una_venta_vieja(self):
        s_ = self._cobro(1, monto=50000, payment_id_mp='500')
        viejo = self._viejo(9, '600', 5000, 10)
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            "2026-09-20T20:00:00.000-03:00,500,,release,payment,50000.00,0.00,50000.00",
            "2026-09-20T20:30:00.000-03:00,999,,release,payment,5000.00,0.00,5000.00",
            f"{self.R1},R1,,release,payout,0.00,55000.00,-55000.00",
        ).encode()), self.config, aplicar=True, archivo='a.csv')
        antes = RetiroMercadoPago.objects.get(withdrawal_id='R1').raw_reporte['por_caja']
        for _ in range(2):
            conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
                "2026-09-21T00:00:00.000-03:00,,,initial_available_balance,,55000.00,0.00,55000.00",
                f"{self.R1},R1,,release,payout,0.00,55000.00,-55000.00",
            ).encode()), self.config, aplicar=True, archivo='b.csv')
        viejo.refresh_from_db()
        s_.refresh_from_db()
        self.assertIsNone(viejo.retiro_id)
        self.assertEqual(s_.retiro.withdrawal_id, 'R1')
        self.assertEqual(RetiroMercadoPago.objects.get(withdrawal_id='R1').raw_reporte['por_caja'], antes)

    def test_devuelta_en_un_reporte_posterior_pierde_la_fecha(self):
        p_ = self._cobro(1, monto=20000, payment_id_mp='801')
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            "2026-09-21T09:00:00.000-03:00,801,,release,payment,20000.00,0.00,20000.00",
        ).encode()), self.config, aplicar=True, archivo='a.csv')
        p_.refresh_from_db()
        self.assertIsNotNone(p_.money_release_date)
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            # Sin saldo inicial: la venta no entra a la cola y la devolución la encuentra afuera.
            "2026-09-22T10:00:00.000-03:00,801,,release,refund,0.00,20000.00,-20000.00",
        ).encode()), self.config, aplicar=True, archivo='b.csv')
        p_.refresh_from_db()
        self.assertIsNone(p_.money_release_date)
        self.assertIsNone(p_.retiro_id)

    def test_devuelta_dentro_del_reporte_pierde_la_fecha_que_trajo_completar(self):
        p_ = self._cobro(1, monto=20000, payment_id_mp='801')
        TransaccionMercadoPago.objects.filter(pk=p_.pk).update(money_release_date=timezone.now())
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            "2026-09-22T09:00:00.000-03:00,801,,release,payment,20000.00,0.00,20000.00",
            "2026-09-22T10:00:00.000-03:00,801,,release,refund,0.00,20000.00,-20000.00",
            "2026-09-22T20:00:00.000-03:00,W1,,release,payout,0.00,1.00,-1.00",
        ).encode()), self.config, aplicar=True, archivo='c.csv')
        p_.refresh_from_db()
        self.assertIsNone(p_.money_release_date)

    def _detectar(self, reportes, archivos=None, estado=None, completar=None, **kw):
        archivos = archivos or {}
        with mock.patch.object(conc, 'listar_reportes_liberaciones', return_value=reportes), \
             mock.patch.object(conc, 'descargar_reporte_liberaciones', side_effect=lambda c, f: archivos[f]) as bajar, \
             mock.patch.object(conc, 'leer_config_reporte', return_value={'execute_after_withdrawal': True}), \
             mock.patch.object(conc, 'estado_tarea_liberaciones',
                               return_value=estado or {'fallido': False, 'listo': False, 'file_name': ''}), \
             mock.patch.object(conc, 'completar_numeros_mp',
                               return_value=completar or {'dias': 0, 'completados': 0, 'sin_tiempo': False,
                                                          'fallidos': 0, 'actualizados': 0}), \
             mock.patch.object(conc, 'pedir_reporte_liberaciones',
                               return_value={'task_id': 88, 'begin_date': _utc_hace(days=1),
                                             'end_date': _utc_hace(minutes=5)}) as pedir:
            return conc.detectar_retiros(**kw), pedir, bajar

    def test_otro_worker_aplica_el_reporte_que_espera_la_pagina(self):
        t = self._cobro(1, monto=39000, payment_id_mp='700')
        archivo = _csv(
            f"{_local_hace(hours=2)},700,,release,payment,39000.00,0.00,39000.00",
            f"{_local_hace(minutes=40)},W7,,release,payout,0.00,39000.00,-39000.00",
        ).encode()
        pedido = {'task_id': 77, 'begin_date': _utc_hace(days=1), 'end_date': _utc_hace(minutes=25)}
        # Caché vacía (otro worker): el pedido lo manda la página; MP ya lo terminó.
        res, pedir, _b = self._detectar([_reporte('viejo.csv', _utc_hace(hours=5))],
                                        {'viejo.csv': _csv().encode(), 'listo.csv': archivo},
                                        estado={'listo': True, 'fallido': False, 'file_name': 'listo.csv'},
                                        pedir=False, pedidos={self.config.id: pedido})
        t.refresh_from_db()
        self.assertEqual(t.retiro.withdrawal_id, 'W7')
        self.assertIsNone(res[0]['pedido'])
        self.assertEqual(pedir.call_count, 0)
        # Todavía generándose: se sigue esperando (no se pierde).
        cache.clear()
        res, _p, _b = self._detectar([_reporte('viejo.csv', _utc_hace(hours=5))], {'viejo.csv': _csv().encode()},
                                     pedir=False, pedidos={self.config.id: pedido})
        self.assertEqual(res[0]['pedido']['task_id'], 77)

    def test_tarea_fallida_se_avisa(self):
        cache.set(conc._CLAVE_PEDIDO.format(self.config.id),
                  {'task_id': 77, 'begin_date': _utc_hace(days=1), 'end_date': _utc_hace(minutes=25)}, 600)
        res, pedir, _b = self._detectar([_reporte('viejo.csv', _utc_hace(hours=5))], {'viejo.csv': _csv().encode()},
                                        estado={'listo': False, 'fallido': True, 'file_name': ''}, pedir=False)
        self.assertIn('no pudo generar', res[0]['error_pedido'])
        self.assertEqual(pedir.call_count, 0)

    def test_si_falto_tiempo_queda_pedir_pendiente_y_la_vuelta_siguiente_pide(self):
        t = self._cobro(1, monto=40000, external_reference='RM-1-1-c1i01')
        TransaccionMercadoPago.objects.filter(pk=t.pk).update(creado_en=timezone.now() - timedelta(hours=6))
        archivo = _csv(
            f"{_local_hace(hours=5)},179000000001,RM-1-1-c1i01,release,payment,39000.00,0.00,40000.00",
            f"{_local_hace(hours=4)},W3,,release,payout,0.00,39000.00,-39000.00",
        ).encode()
        reportes = [_reporte('r.csv', _utc_hace(hours=3))]
        res, pedir, _b = self._detectar(reportes, {'r.csv': archivo},
                                        completar={'dias': 1, 'completados': 0, 'sin_tiempo': True,
                                                   'fallidos': 0, 'actualizados': 0})
        self.assertTrue(res[0]['incompleto'])
        self.assertTrue(res[0]['pedir_pendiente'])
        self.assertEqual(pedir.call_count, 0)
        res, pedir, _b = self._detectar(reportes, {'r.csv': archivo}, pedir=False, pedir_ids={self.config.id})
        self.assertEqual(pedir.call_count, 1)
        self.assertEqual(res[0]['pedido']['task_id'], 88)

    def test_aplicar_a_mano_con_mp_caido_no_marca_el_reporte(self):
        t = self._cobro(1, monto=40000, external_reference='RM-1-1-c1i01')
        TransaccionMercadoPago.objects.filter(pk=t.pk).update(creado_en=timezone.now() - timedelta(hours=6))
        archivo = _csv(
            f"{_local_hace(hours=5)},179000000001,RM-1-1-c1i01,release,payment,39000.00,0.00,40000.00",
            f"{_local_hace(hours=4)},W4,,release,payout,0.00,39000.00,-39000.00",
        ).encode()
        admin = crear_usuario(username='adm_man', rol='administrador')
        c = Client(); c.force_login(admin)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True), \
             mock.patch.object(conc, 'descargar_reporte_liberaciones', return_value=archivo), \
             mock.patch.object(conc, 'completar_numeros_mp',
                               return_value={'dias': 0, 'completados': 0, 'sin_tiempo': False,
                                             'fallidos': 2, 'actualizados': 0}):
            r = c.post(reverse('api_conciliacion_liberaciones_mp'),
                       {'config_id': self.config.id, 'file_name': 'm.csv', 'aplicar': '1'}).json()
        self.assertTrue(r['aviso'])
        self.assertTrue(RetiroMercadoPago.objects.filter(withdrawal_id='W4').exists())
        self.assertNotIn('m.csv', conc.reportes_aplicados())

    def test_rango_de_60_dias_con_cambio_de_hora(self):
        from datetime import date, datetime as _dt
        ahora = timezone.make_aware(_dt(2027, 5, 1, 23, 30), timezone.get_current_timezone())
        with mock.patch('django.utils.timezone.now', return_value=ahora):
            begin, end = conc.rango_utc_reporte(date(2027, 3, 3), date(2027, 5, 1))
        largo = _dt.strptime(end, '%Y-%m-%dT%H:%M:%SZ') - _dt.strptime(begin, '%Y-%m-%dT%H:%M:%SZ')
        self.assertLess(largo, timedelta(days=60))

    def test_dias_incluye_cobro_con_aviso_perdido_y_no_relee_dias(self):
        pend = self._cobro(1, monto=5000, estado='PENDIENTE', order_id='ORD1')
        rech = self._cobro(2, monto=5000, estado='RECHAZADA', order_id='ORD2')
        dia = timezone.now() - timedelta(days=3)
        TransaccionMercadoPago.objects.filter(pk__in=[pend.pk, rech.pk]).update(creado_en=dia)
        dias = conc.dias_cobros_sin_numero(self.config, {'rango': [_local_hace(days=1), _local_hace(minutes=1)]})
        self.assertIn(timezone.localtime(dia).date(), dias)
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=[]) as buscar:
            conc.completar_numeros_mp(self.config, [timezone.localtime(dia).date()], importar=True)
            conc.completar_numeros_mp(self.config, [timezone.localtime(dia).date()], importar=True)
            conc.completar_numeros_mp(self.config, [timezone.localtime(dia).date()])   # vista previa: lee igual
        self.assertEqual(buscar.call_count, 2)   # al aplicar, un día pasado ya leído no se relee


@mock.patch.dict('os.environ', ENV_TEST)
class RevisionVerVentasTest(_Base):
    """23-09 noche: revisión de «Ver ventas» + lo visto en producción (PAO3 cobra
    casi todo «MP manual»; el reporte decía «revisado hasta 23:59» y no se pedía otro)."""

    def _pago_manual(self, correlativo, voucher, monto=17980, creado=None):
        tk = self._ticket(correlativo)
        pago = TicketDetallePago.objects.create(ticket=tk, metodo_pago='MP_POINT', monto=monto,
                                                origen_pago='MANUAL', voucher=voucher)
        if creado is not None:
            TicketDetallePago.objects.filter(pk=pago.pk).update(creado_en=creado)
            pago.refresh_from_db()
        return pago

    def test_ver_ventas_no_cuenta_dos_veces_ni_repite_voucher(self):
        retiro = RetiroMercadoPago.objects.create(
            config=self.config, withdrawal_id='W1', fecha=self.hoy, monto=14550, estado='CONCILIADO',
            raw_reporte={'pagos': ['111', '999'], 'netos': {'999': 4850}, 'por_caja': []})
        tk = self._ticket(1)
        pago_pos = TicketDetallePago.objects.create(ticket=tk, metodo_pago='MP_POINT', monto=10000,
                                                    origen_pago='POS_INTEGRADO', voucher='111')
        self._cobro(1, ticket=tk, payment_id_mp='111', monto_neto=9700, retiro=retiro, detalle_pago=pago_pos)
        self._pago_manual(2, '999', monto=5000)
        self._pago_manual(3, '999', monto=5000)          # mismo N° digitado dos veces
        d = conc.detalle_retiro(retiro)
        self.assertEqual(sorted(f['origen'] for f in d['filas']), ['MP manual', 'POS'])
        self.assertEqual(d['suma_neto'], 9700 + 4850)

    def test_reporte_con_fin_23_59_generado_antes_se_considera_viejo(self):
        fin_del_dia = timezone.localtime().replace(hour=23, minute=59, second=59).isoformat()
        rep = {'file_name': 'x.csv', 'begin_date': '', 'end_date': fin_del_dia,
               'creado': _utc_hace(hours=3), 'origen': 'manual', 'estado': ''}
        fin = conc._fin_de_reporte(rep)
        self.assertLess(fin, timezone.now() - timedelta(hours=2))
        with mock.patch.object(conc, 'listar_reportes_liberaciones', return_value=[rep]), \
             mock.patch.object(conc, 'descargar_reporte_liberaciones', return_value=_csv().encode()), \
             mock.patch.object(conc, 'leer_config_reporte', return_value={'execute_after_withdrawal': True}), \
             mock.patch.object(conc, 'pedir_reporte_liberaciones',
                               return_value={'task_id': 5, 'begin_date': '', 'end_date': _utc_hace(minutes=5)}) as pedir:
            conc.detectar_retiros()
        self.assertEqual(pedir.call_count, 1)

    def test_pago_mp_manual_se_registra_como_cobro_y_entra_al_retiro(self):
        ayer = timezone.now() - timedelta(days=1)
        pago = self._pago_manual(5, '179473941930', monto=17980, creado=ayer)
        otro = self._pago_manual(6, '179000000077', monto=9990, creado=ayer)    # el monto no calza en MP
        # Los días de pagos manuales los aporta el reporte que los trae (solo al aplicar).
        res_rep = {'rango': [], 'dias_manuales': [str(timezone.localtime(ayer).date())]}
        dias = conc.dias_para_completar(self.config, res_rep, importar=True)
        self.assertEqual(dias, [timezone.localtime(ayer).date()])
        self.assertEqual(conc.dias_para_completar(self.config, res_rep), [])
        pagos_mp = [
            {'id': 179473941930, 'status': 'approved', 'transaction_amount': 17980.0,
             'date_created': ayer.isoformat(),
             'money_release_date': (ayer + timedelta(hours=1)).isoformat(), 'payment_type_id': 'debit_card',
             'transaction_details': {'net_received_amount': 17710.0}, 'card': {'last_four_digits': '1234'}},
            {'id': 179000000077, 'status': 'approved', 'transaction_amount': 5000.0},
        ]
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos_mp):
            previa = conc.completar_numeros_mp(self.config, dias)           # vista previa: no crea nada
            self.assertEqual(previa['importados'], 0)
            self.assertFalse(TransaccionMercadoPago.objects.filter(payment_id_mp='179473941930').exists())
            comp = conc.completar_numeros_mp(self.config, dias, importar=True)
            cache.clear()
            conc.completar_numeros_mp(self.config, dias, importar=True)     # idempotente
        self.assertEqual(comp['importados'], 1)
        trx = TransaccionMercadoPago.objects.get(payment_id_mp='179473941930')
        self.assertEqual((trx.monto, trx.monto_neto, trx.fee_mp, trx.detalle_pago_id, trx.consumida),
                         (17980, 17710, 270, pago.id, True))
        self.assertEqual(timezone.localtime(trx.creado_en).date(), timezone.localtime(ayer).date())
        self.assertIsNotNone(trx.money_release_date)
        self.assertEqual(TransaccionMercadoPago.objects.filter(payment_id_mp='179473941930').count(), 1)
        self.assertFalse(TransaccionMercadoPago.objects.filter(payment_id_mp='179000000077').exists())
        # En «Cobros y documentos» ya no sale como «Registrado a mano», sino como cobro.
        data = conc.cobros_vs_documentos(timezone.localtime(ayer).date(), self.hoy)
        manuales = [f for f in data['filas'] if f['categoria'] == conc.CAT_MANUAL]
        self.assertEqual([f['monto'] for f in manuales], [9990])
        # Y el reporte lo cruza como venta y lo amarra al retiro.
        filas = conc.leer_csv(_csv(
            f"{_local_hace(hours=2)},179473941930,,release,payment,17710.00,0.00,17980.00",
            f"{_local_hace(hours=1)},W5,,release,payout,0.00,17710.00,-17710.00",
        ).encode())
        res = conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True, archivo='r.csv')
        trx.refresh_from_db()
        self.assertEqual(trx.retiro.withdrawal_id, 'W5')
        self.assertEqual(res['pagos_manuales'], 0)

    def test_retiro_antiguo_sin_desglose_con_ventas_que_lo_cubren_no_se_desarma(self):
        a = self._cobro(1, monto=10000, payment_id_mp='501')
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            "2026-09-18T10:00:00.000-03:00,501,,release,payment,10000.00,0.00,10000.00",
            "2026-09-21T10:00:00.000-03:00,999,,release,payment,10000.00,0.00,10000.00",
            "2026-09-22T10:00:00.000-03:00,W1,,release,payout,0.00,10000.00,-10000.00",
        ).encode()), self.config, aplicar=True, archivo='a.csv')
        w1 = RetiroMercadoPago.objects.get(withdrawal_id='W1')
        w1.raw_reporte = {'archivos': ['a.csv'], 'pagos': ['501']}      # como se guardaba antes
        w1.save(update_fields=['raw_reporte'])
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            "2026-09-22T00:00:00.000-03:00,,,initial_available_balance,,20000.00,0.00,20000.00",
            "2026-09-22T10:00:00.000-03:00,W1,,release,payout,0.00,10000.00,-10000.00",
        ).encode()), self.config, aplicar=True, archivo='b.csv')
        a.refresh_from_db()
        self.assertEqual(a.retiro_id, w1.id)
        self.assertIn('Caja 1', RetiroMercadoPago.objects.get(pk=w1.pk).raw_reporte['por_caja'][0]['caja'])

    def test_cuadre_resta_devolucion_parcial_y_kpi_retirado_en_el_periodo(self):
        venta = self._cobro(1, monto=10000, monto_neto=9700, payment_id_mp='1',
                            money_release_date=timezone.now() - timedelta(hours=1))
        TransaccionMercadoPago.objects.create(
            config=self.config, sucursal=self.sucursal, correlativo_ticket='1', tipo='DEVOLUCION',
            transaccion_origen=venta, external_reference='DEV-1', monto=3000, estado='DEVUELTA')
        tot = conc.cuadre_por_sucursal(None, None)['total']
        self.assertEqual((tot['devuelto'], tot['neto'], tot['en_mp']), (3000, 6700, 6700))
        RetiroMercadoPago.objects.create(config=self.config, withdrawal_id='W1', fecha=self.hoy, monto=10000000)
        RetiroMercadoPago.objects.create(config=self.config, withdrawal_id='W0', fecha=self.hoy - timedelta(days=30),
                                         monto=5)
        admin = crear_usuario(username='adm_ret2', rol='administrador')
        c = Client(); c.force_login(admin)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            k = c.get(reverse('api_dineros_mercadopago')).json()['kpis']
        self.assertEqual(k['retirado_periodo'], 10000000)


@mock.patch.dict('os.environ', ENV_TEST)
class RevisionImportarManualTest(_Base):
    """Revisión de la importación de pagos «MP manual» (23-09 noche)."""

    def _pago_manual(self, correlativo, voucher, monto=17980, estado='PAGADO', creado=None):
        tk = self._ticket(correlativo, estado=estado)
        pago = TicketDetallePago.objects.create(ticket=tk, metodo_pago='MP_POINT', monto=monto,
                                                origen_pago='MANUAL', voucher=voucher)
        TicketDetallePago.objects.filter(pk=pago.pk).update(creado_en=creado or timezone.now() - timedelta(days=1))
        pago.refresh_from_db()
        return pago

    def _mp(self, pid, monto=17980, **extra):
        return dict({'id': int(pid), 'status': 'approved', 'transaction_amount': float(monto),
                     'date_created': (timezone.now() - timedelta(days=1)).isoformat(),
                     'money_release_date': timezone.now().isoformat(), 'payment_type_id': 'debit_card',
                     'transaction_details': {'net_received_amount': float(monto) - 270}}, **extra)

    def _completar(self, pagos_mp):
        dia = timezone.localtime(timezone.now() - timedelta(days=1)).date()
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos_mp):
            return conc.completar_numeros_mp(self.config, [dia], importar=True)

    def test_no_duplica_el_cobro_del_pos_que_aun_no_tiene_numero(self):
        directo = self._cobro('DIRECTO-2209-101010', monto=17980, consumida=False,
                              payment_id='PAY01ABC', external_reference='RM-1-DIRECTO-2209-101010-c1i01')
        self._pago_manual(5, '179473941930')
        comp = self._completar([self._mp('179473941930', external_reference='RM-1-DIRECTO-2209-101010-c1i01')])
        self.assertEqual(comp['importados'], 0)
        self.assertEqual(TransaccionMercadoPago.objects.filter(payment_id_mp='179473941930').count(), 1)
        directo.refresh_from_db()
        self.assertEqual(directo.payment_id_mp, '179473941930')   # se completó su N°: queda para «Asociar»

    def test_no_importa_ticket_pendiente_voucher_repetido_ni_devuelto(self):
        self._pago_manual(5, '179000000001', estado='PENDIENTE')
        self._pago_manual(6, '179000000002')
        self._pago_manual(7, '179000000002')                      # mismo N° en dos ventas
        self._pago_manual(8, '179000000003')
        comp = self._completar([self._mp('179000000001'), self._mp('179000000002'),
                                self._mp('179000000003', transaction_amount_refunded=5000.0)])
        self.assertEqual(comp['importados'], 0)
        self.assertFalse(TransaccionMercadoPago.objects.filter(external_reference__startswith='MANUAL-').exists())

    def test_importado_despues_de_cerrado_el_retiro_se_amarra_al_reprocesar(self):
        self._pago_manual(5, '179473941930')
        filas = conc.leer_csv(_csv(
            f"{_local_hace(hours=5)},179473941930,,release,payment,17710.00,0.00,17980.00",
            f"{_local_hace(hours=4)},W5,,release,payout,0.00,17710.00,-17710.00",
        ).encode())
        conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True, archivo='a.csv')
        w5 = RetiroMercadoPago.objects.get(withdrawal_id='W5')
        self.assertEqual(conc._no_explicado(w5.raw_reporte['por_caja']), 0)   # cerrado (MP manual)
        self.assertEqual(w5.raw_reporte['netos'], {'179473941930': 17710})
        d = conc.detalle_retiro(w5)
        self.assertEqual((d['filas'][0]['origen'], d['filas'][0]['comision']), ('MP manual', 270))
        self._completar([self._mp('179473941930')])
        trx = TransaccionMercadoPago.objects.get(payment_id_mp='179473941930')
        self.assertEqual(len(conc.detalle_retiro(w5)['filas']), 1)      # antes de reprocesar ya se ve
        conc.procesar_reporte_liberaciones(filas, self.config, aplicar=True, archivo='a.csv')
        trx.refresh_from_db()
        self.assertEqual(trx.retiro_id, w5.id)

    def test_cierre_mp_reconoce_el_pago_manual_importado(self):
        from app.services import mercadopago_service as mp
        self._pago_manual(5, '179473941930')
        self._completar([self._mp('179473941930')])
        trx = TransaccionMercadoPago.objects.get(payment_id_mp='179473941930')
        dia = timezone.localtime(trx.creado_en).date()
        res = mp.conciliar_cierre_mp(self.config, dia, pagos=[self._mp('179473941930')])
        self.assertEqual(res['sin_registro'], [])
        self.assertEqual(res['sin_confirmar'], [])

    def test_aviso_de_mp_encuentra_el_pago_manual_y_anular_no_lo_devuelve(self):
        from app.services import mercadopago_service as mp
        pago = self._pago_manual(5, '179473941930')
        self._completar([self._mp('179473941930')])
        trx = TransaccionMercadoPago.objects.get(payment_id_mp='179473941930')
        with mock.patch('app.services.mercadopago_service._request',
                        return_value=_resp(200, self._mp('179473941930', status='refunded'))):
            encontrada, _payment = mp._resolver_transaccion_por_payment('179473941930')
        self.assertEqual(encontrada.id, trx.id)
        with mock.patch('app.services.mercadopago_service.reembolsar') as reembolsar:
            mp.reembolsar_pagos_de_ticket(pago.ticket)
        reembolsar.assert_not_called()

    def test_devuelto_entero_en_el_reporte_queda_devuelta(self):
        self._pago_manual(5, '179473941930')
        self._completar([self._mp('179473941930')])
        trx = TransaccionMercadoPago.objects.get(payment_id_mp='179473941930')
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            f"{_local_hace(hours=5)},179473941930,,release,payment,17710.00,0.00,17980.00",
            f"{_local_hace(hours=4)},179473941930,,release,refund,0.00,17710.00,-17710.00",
        ).encode()), self.config, aplicar=True, archivo='d.csv')
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'DEVUELTA')

    def test_ver_ventas_mismo_numero_en_dos_lineas_no_da_comision_negativa(self):
        retiro = RetiroMercadoPago.objects.create(
            config=self.config, withdrawal_id='W1', fecha=self.hoy, monto=49500, estado='CONCILIADO',
            raw_reporte={'pagos': ['179000000009'], 'netos': {'179000000009': 49500}, 'por_caja': []})
        tk = self._ticket(9)
        for monto in (30000, 20980):
            TicketDetallePago.objects.create(ticket=tk, metodo_pago='MP_POINT', monto=monto,
                                             origen_pago='MANUAL', voucher='179000000009')
        fila = conc.detalle_retiro(retiro)['filas'][0]
        self.assertEqual((fila['bruto'], fila['neto'], fila['comision']), (50980, 49500, 1480))

    def test_cuadre_devolucion_antes_del_retiro_sale_del_banco(self):
        retiro = RetiroMercadoPago.objects.create(
            config=self.config, withdrawal_id='W9', fecha=self.hoy, monto=6700,
            raw_reporte={'instante': timezone.now().isoformat()})
        venta = self._cobro(1, monto=10000, monto_neto=9700, payment_id_mp='1', retiro=retiro)
        dv = TransaccionMercadoPago.objects.create(
            config=self.config, sucursal=self.sucursal, correlativo_ticket='1', tipo='DEVOLUCION',
            transaccion_origen=venta, external_reference='DEV-9', monto=3000, estado='DEVUELTA')
        TransaccionMercadoPago.objects.filter(pk=dv.pk).update(creado_en=timezone.now() - timedelta(hours=1))
        tot = conc.cuadre_por_sucursal(None, None)['total']
        self.assertEqual((tot['neto'], tot['en_banco'], tot['en_mp']), (6700, 6700, 0))

    def test_kpi_retirado_dice_de_que_cuenta_es(self):
        RetiroMercadoPago.objects.create(config=self.config, withdrawal_id='W1', fecha=self.hoy, monto=100)
        admin = crear_usuario(username='adm_cta', rol='administrador')
        c = Client(); c.force_login(admin)
        with mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True):
            k = c.get(reverse('api_dineros_mercadopago'), {'sucursal_id': self.sucursal.id}).json()['kpis']
        self.assertEqual(k['retirado_periodo'], 100)
        self.assertEqual(k['retirado_cuenta'], self.empresa.nombre)


@mock.patch.dict('os.environ', ENV_TEST)
class RevisionV7Test(_Base):
    """4ª revisión: caja del pago, retiro que ya lo contó, revalidar bajo lock,
    devolución parcial, neto total, líneas repartidas, «sin venta» ya explicado."""

    def _pago_manual(self, correlativo, voucher, monto=17980):
        tk = self._ticket(correlativo)
        pago = TicketDetallePago.objects.create(ticket=tk, metodo_pago='MP_POINT', monto=monto,
                                                origen_pago='MANUAL', voucher=voucher)
        TicketDetallePago.objects.filter(pk=pago.pk).update(creado_en=timezone.now() - timedelta(days=1))
        pago.refresh_from_db()
        return pago

    def _mp(self, pid, monto=17980, **extra):
        return dict({'id': int(pid), 'status': 'approved', 'transaction_amount': float(monto),
                     'date_created': (timezone.now() - timedelta(days=1)).isoformat(),
                     'money_release_date': timezone.now().isoformat(),
                     'transaction_details': {'net_received_amount': float(monto) - 270}}, **extra)

    def _completar(self, pagos_mp):
        dia = timezone.localtime(timezone.now() - timedelta(days=1)).date()
        with mock.patch('app.services.mercadopago_service.buscar_pagos_dia', return_value=pagos_mp):
            return conc.completar_numeros_mp(self.config, [dia], importar=True)

    def test_caja_del_pago_por_punto_de_venta(self):
        caja2 = _config(self.sucursal, nombre='Caja 2', external_pos_id='POS222', pos_id='222')
        self._pago_manual(5, '179473941930')
        self._completar([self._mp('179473941930', pos_id='222')])
        self.assertEqual(TransaccionMercadoPago.objects.get(payment_id_mp='179473941930').config_id, caja2.id)

    def test_se_amarra_al_retiro_que_ya_lo_conto(self):
        retiro = RetiroMercadoPago.objects.create(
            config=self.config, withdrawal_id='WOLD', fecha=self.hoy - timedelta(days=1), monto=17710,
            raw_reporte={'pagos': ['179473941930'], 'netos': {'179473941930': 17710}, 'por_caja': []})
        self._pago_manual(5, '179473941930')
        self._completar([self._mp('179473941930')])
        self.assertEqual(TransaccionMercadoPago.objects.get(payment_id_mp='179473941930').retiro_id, retiro.id)

    def test_no_importa_si_el_pago_cambio_mientras_corria(self):
        pago = self._pago_manual(5, '179473941930')
        TicketDetallePago.objects.filter(pk=pago.pk).update(voucher='179000000555')
        self.assertIsNone(conc._importar_pago_manual(pago, self._mp('179473941930'), self.config.id))

    def test_devolucion_parcial_no_marca_devuelta(self):
        self._pago_manual(5, '179473941930')
        self._completar([self._mp('179473941930')])
        trx = TransaccionMercadoPago.objects.get(payment_id_mp='179473941930')
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            f"{_local_hace(hours=6)},179473941930,,release,payment,17710.00,0.00,17980.00",
            f"{_local_hace(hours=5)},WP,,release,payout,0.00,15000.00,-15000.00",
            f"{_local_hace(hours=4)},179473941930,,release,refund,0.00,5000.00,-5000.00",
        ).encode()), self.config, aplicar=True, archivo='p.csv')
        trx.refresh_from_db()
        self.assertEqual(trx.estado, 'APROBADA')

    def test_neto_total_no_descuenta_la_deuda(self):
        self._pago_manual(5, '179473941930')
        conc.procesar_reporte_liberaciones(conc.leer_csv(_csv(
            "2026-09-22T00:00:00.000-03:00,,,initial_available_balance,,0.00,1000.00,-1000.00",
            "2026-09-22T09:00:00.000-03:00,179473941930,,release,payment,17710.00,0.00,17980.00",
            "2026-09-22T10:00:00.000-03:00,WE,,release,payout,0.00,16710.00,-16710.00",
        ).encode()), self.config, aplicar=True, archivo='e.csv')
        raw = RetiroMercadoPago.objects.get(withdrawal_id='WE').raw_reporte
        self.assertEqual(raw['netos'], {'179473941930': 17710})

    def test_detalle_lineas_iguales_repartidas_y_sin_venta_ya_explicada(self):
        retiro = RetiroMercadoPago.objects.create(
            config=self.config, withdrawal_id='W1', fecha=self.hoy, monto=29400, estado='CONCILIADO',
            raw_reporte={'pagos': ['179000000010', '5551'], 'netos': {'179000000010': 19400},
                         'sin_venta': 10000, 'sin_local': {'5551': 10000}, 'por_caja': []})
        tk = self._ticket(9)
        for _ in range(2):
            TicketDetallePago.objects.create(ticket=tk, metodo_pago='MP_POINT', monto=10000,
                                             origen_pago='MANUAL', voucher='179000000010')
        self._cobro(1, payment_id_mp='5551', monto=10000)       # su cobro apareció después
        d = conc.detalle_retiro(retiro)
        manual = next(f for f in d['filas'] if f['origen'] == 'MP manual')
        self.assertEqual((manual['bruto'], manual['neto'], manual['comision']), (20000, 19400, 600))
        self.assertEqual(d['bruto_sin_neto'], 0)
        self.assertEqual(d['sin_venta'], 0)

    def test_cierre_no_aprende_caja_de_una_fila_manual(self):
        from app.services import mercadopago_service as mp
        caja2 = _config(self.sucursal, nombre='Caja 2', external_pos_id='POS222', pos_id='222')
        self._pago_manual(5, '179473941930')
        self._completar([self._mp('179473941930')])                 # sin pos: queda en la caja principal
        trx = TransaccionMercadoPago.objects.get(payment_id_mp='179473941930')
        dia = timezone.localtime(trx.creado_en).date()
        otro = self._mp('179999999999', pos_id='222')
        res = mp.conciliar_cierre_mp(caja2, dia, pagos=[self._mp('179473941930', pos_id='222'), otro])
        self.assertIn('179999999999', [x['payment_id'] for x in res['sin_registro']])
