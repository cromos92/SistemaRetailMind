"""
Tests para la compensación factura-contra-factura ("Pagar con Factura") del
módulo Gestión DTE de Compras.

Cuando ya no se puede asociar una Nota de Crédito a una factura de compra antigua,
el proveedor puede pedir saldarla asociándole OTRA factura del mismo proveedor como
instrumento de pago. Esto es una compensación de tesorería / neteo de cuentas por
pagar (NO una relación tributaria SII): se registra en el libro de pagos
(Dte_Detalle_Pago) con metodo_pago=METODO_COMPENSACION, igual que una NC.

Cubre las vistas de views_modulo_compras.py:
- obtener_facturas_compensar_disponibles
- asociar_factura_compensacion
- desasociar_factura_compensacion
"""
import json
from decimal import Decimal

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago, Dte_Incidencia
from app.views_modulo_compras import METODO_COMPENSACION

from .factories import crear_empresa, setup_entorno_completo

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _crear_factura_compra(env, proveedor, numero, monto_con_iva,
                          estado_pago='Pendiente'):
    """Crea una factura electrónica de COMPRA (emisor=proveedor, receptor=empresa)."""
    monto_neto = int(round(monto_con_iva / Decimal('1.19')))
    return Dte.objects.create(
        emisor=proveedor,
        receptor=env['empresa'],
        numero_documento=numero,
        tipo_documento='FACTURA ELECTRONICA',
        monto_con_iva=monto_con_iva,
        monto_neto=monto_neto,
        descuento=0,
        estado_pago=estado_pago,
        estado_dte='RECEPCIONADO_COMPLETO',
        responsable=env['user'].username,
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=30,
        bultos=0,
        unidades_productos=1,
        tipo_transaccion='COMPRA',
        sucursal=env['sucursal'],
        es_nota_credito=False,
        hora=timezone.localtime().time(),
    )


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class CompensacionFacturaTest(TestCase):

    def setUp(self):
        self.env = setup_entorno_completo()
        # Proveedor (emisor de las facturas de compra)
        self.proveedor = crear_empresa(
            nombre='Proveedor Test', rut='77.000.000-7', esProveedor=True,
        )
        # Otro proveedor distinto, para el guard de mismo-proveedor
        self.otro_proveedor = crear_empresa(
            nombre='Otro Proveedor', rut='78.000.000-8', esProveedor=True,
        )

        self.client = Client()
        self.client.login(username='testuser', password='TestPass123!')
        session = self.client.session
        session['idSucursalActual'] = self.env['sucursal'].id
        session['idEmpresaActual'] = self.env['empresa'].id
        session['nombreUsuario'] = 'testuser'
        session.save()

    # ----- helpers -----

    def _post_asociar(self, dte_id, instrumento_id, monto=None):
        body = {'dte_id': dte_id, 'factura_compensadora_id': instrumento_id}
        if monto is not None:
            body['monto'] = monto
        return self.client.post(
            reverse('asociar_factura_compensacion'),
            data=json.dumps(body),
            content_type='application/json',
        )

    def _get_disponibles(self, dte_id):
        resp = self.client.get(
            reverse('obtener_facturas_compensar_disponibles'),
            {'dte_id': dte_id},
        )
        return resp, json.loads(resp.content)

    def _saldo(self, dte):
        total = sum(p.monto for p in Dte_Detalle_Pago.objects.filter(dte=dte))
        return float(dte.monto_con_iva) - total

    # ----- tests -----

    def test_compensacion_total_marca_pagado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1001, 100000)
        instrumento = _crear_factura_compra(self.env, self.proveedor, 1002, 100000)

        resp = self._post_asociar(objetivo.id, instrumento.id, monto=100000)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['monto_aplicado'], 100000)

        pago = Dte_Detalle_Pago.objects.get(dte=objetivo, metodo_pago=METODO_COMPENSACION)
        self.assertEqual(pago.voucher, str(instrumento.numero_documento))
        self.assertEqual(pago.monto, 100000)

        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'PAGADO')

    def test_compensacion_parcial_marca_parcial(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1101, 100000)
        instrumento = _crear_factura_compra(self.env, self.proveedor, 1102, 30000)

        resp = self._post_asociar(objetivo.id, instrumento.id, monto=30000)
        self.assertEqual(resp.status_code, 200, resp.content)

        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'Parcial')
        self.assertEqual(self._saldo(objetivo), 70000)

    def test_monto_se_acota_al_menor_de_los_saldos(self):
        """Pedir más que el saldo del instrumento se acota a ese saldo."""
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1201, 100000)
        instrumento = _crear_factura_compra(self.env, self.proveedor, 1202, 30000)

        # Se pide 90.000 pero el instrumento sólo tiene 30.000 de saldo.
        resp = self._post_asociar(objetivo.id, instrumento.id, monto=90000)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = json.loads(resp.content)
        self.assertEqual(data['monto_aplicado'], 30000)

    def test_auto_compensacion_rechazada(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1301, 50000)
        resp = self._post_asociar(objetivo.id, objetivo.id, monto=10000)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('sí misma', json.loads(resp.content)['error'])

    def test_distinto_proveedor_rechazado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1401, 50000)
        instrumento = _crear_factura_compra(self.env, self.otro_proveedor, 1402, 50000)
        resp = self._post_asociar(objetivo.id, instrumento.id, monto=50000)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('mismo proveedor', json.loads(resp.content)['error'])

    def test_doble_uso_de_instrumento_rechazado(self):
        objetivo_a = _crear_factura_compra(self.env, self.proveedor, 1501, 100000)
        objetivo_c = _crear_factura_compra(self.env, self.proveedor, 1503, 100000)
        instrumento = _crear_factura_compra(self.env, self.proveedor, 1502, 40000)

        # Primer uso del instrumento: OK
        resp1 = self._post_asociar(objetivo_a.id, instrumento.id, monto=40000)
        self.assertEqual(resp1.status_code, 200, resp1.content)

        # Segundo uso del MISMO instrumento contra otra factura: rechazado
        resp2 = self._post_asociar(objetivo_c.id, instrumento.id, monto=40000)
        self.assertEqual(resp2.status_code, 400)
        self.assertIn('ya fue usada', json.loads(resp2.content)['error'])

    def test_incidencia_pendiente_rechaza_compensacion(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1601, 50000)
        instrumento = _crear_factura_compra(self.env, self.proveedor, 1602, 50000)
        Dte_Incidencia.objects.create(
            dte=objetivo, tipo='FACTURACION',
            descripcion='Diferencia de monto', estado='PENDIENTE',
        )
        resp = self._post_asociar(objetivo.id, instrumento.id, monto=50000)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('incidencias', json.loads(resp.content)['error'])

    def test_desasociar_revierte_estado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1701, 100000)
        instrumento = _crear_factura_compra(self.env, self.proveedor, 1702, 100000)

        resp = self._post_asociar(objetivo.id, instrumento.id, monto=100000)
        self.assertEqual(resp.status_code, 200, resp.content)
        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'PAGADO')

        pago = Dte_Detalle_Pago.objects.get(dte=objetivo, metodo_pago=METODO_COMPENSACION)
        resp_des = self.client.post(
            reverse('desasociar_factura_compensacion', args=[pago.id]),
        )
        self.assertEqual(resp_des.status_code, 200, resp_des.content)

        self.assertFalse(
            Dte_Detalle_Pago.objects.filter(
                dte=objetivo, metodo_pago=METODO_COMPENSACION).exists()
        )
        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'Pendiente')

    def test_listado_excluye_self_diff_proveedor_pagadas_y_usadas(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 1801, 100000)
        b = _crear_factura_compra(self.env, self.proveedor, 1802, 50000)
        c = _crear_factura_compra(self.env, self.proveedor, 1803, 60000)
        # Distinto proveedor → no debe aparecer
        _crear_factura_compra(self.env, self.otro_proveedor, 1804, 50000)
        # Misma empresa/proveedor pero sin saldo (ya pagada en efectivo) → no aparece
        pagada = _crear_factura_compra(self.env, self.proveedor, 1805, 40000)
        Dte_Detalle_Pago.objects.create(
            dte=pagada, metodo_pago='EFECTIVO', monto=40000,
        )

        resp, data = self._get_disponibles(objetivo.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(data['success'])
        ids = {f['id'] for f in data['facturas']}
        self.assertEqual(ids, {b.id, c.id})

        # Tras usar B como instrumento, ya no debe ofrecerse
        self._post_asociar(objetivo.id, b.id, monto=50000)
        resp2, data2 = self._get_disponibles(objetivo.id)
        ids2 = {f['id'] for f in data2['facturas']}
        self.assertEqual(ids2, {c.id})
