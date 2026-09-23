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
