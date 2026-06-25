"""
Tests para la compensación con FACTURA EMITIDA ("Compensar con factura emitida") del
módulo Gestión DTE de Compras.

Cuando el proveedor ya no puede emitir/cargar una Nota de Crédito sobre una factura de
compra antigua, EDEL o nosotros le emitimos una factura (un cargo) al proveedor y la
asociamos a su factura de compra para rebajar el saldo a pagar. El instrumento es un DTE
tipo_transaccion='VENTA' cuyo receptor es ese mismo proveedor (mismo RUT). Es un neteo de
tesorería (NO una relación tributaria SII): se registra en el libro de pagos
(Dte_Detalle_Pago) con metodo_pago=METODO_COMPENSACION_EMITIDA y FK documento_compensacion
al DTE emitido.

Cubre las vistas de views_modulo_compras.py:
- obtener_documentos_emitidos_compensar_disponibles
- asociar_documento_emitido_compensacion
- desasociar_documento_emitido_compensacion
"""
import json
from decimal import Decimal

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import Dte, Dte_Detalle_Pago, Dte_Incidencia
from app.views_modulo_compras import METODO_COMPENSACION_EMITIDA

from .factories import crear_empresa, setup_entorno_completo

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _crear_factura_compra(env, proveedor, numero, monto_con_iva, estado_pago='Pendiente'):
    """Factura electrónica de COMPRA (emisor=proveedor, receptor=empresa)."""
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


def _crear_factura_emitida(env, receptor, numero, monto_con_iva, emisor=None,
                           tipo_documento='FACTURA ELECTRONICA', estado_dte='EMITIDO'):
    """Factura electrónica EMITIDA (tipo_transaccion='VENTA', receptor=proveedor)."""
    monto_neto = int(round(monto_con_iva / Decimal('1.19')))
    return Dte.objects.create(
        emisor=emisor or env['empresa'],
        receptor=receptor,
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_con_iva=monto_con_iva,
        monto_neto=monto_neto,
        descuento=0,
        estado_pago='Pendiente',
        estado_dte=estado_dte,
        responsable=env['user'].username,
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=30,
        bultos=0,
        unidades_productos=1,
        tipo_transaccion='VENTA',
        sucursal=env['sucursal'],
        es_nota_credito=False,
        hora=timezone.localtime().time(),
    )


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class CompensacionEmitidaTest(TestCase):

    def setUp(self):
        self.env = setup_entorno_completo()
        # Proveedor (emisor de las facturas de compra / receptor de las emitidas)
        self.proveedor = crear_empresa(
            nombre='Proveedor Test', rut='77.000.000-7', esProveedor=True,
        )
        # Otro proveedor distinto, para el guard de mismo-RUT
        self.otro_proveedor = crear_empresa(
            nombre='Otro Proveedor', rut='78.000.000-8', esProveedor=True,
        )
        # "EDEL" como emisor alternativo de la factura emitida
        self.edel = crear_empresa(
            nombre='EDEL', rut='76.337.843-8', esProveedor=False,
        )

        self.client = Client()
        self.client.login(username='testuser', password='TestPass123!')
        session = self.client.session
        session['idSucursalActual'] = self.env['sucursal'].id
        session['idEmpresaActual'] = self.env['empresa'].id
        session['nombreUsuario'] = 'testuser'
        session.save()

    # ----- helpers -----

    def _post_asociar(self, body):
        return self.client.post(
            reverse('asociar_documento_emitido_compensacion'),
            data=json.dumps(body),
            content_type='application/json',
        )

    def _get_disponibles(self, dte_id):
        resp = self.client.get(
            reverse('obtener_documentos_emitidos_compensar_disponibles'),
            {'dte_id': dte_id},
        )
        return resp, json.loads(resp.content)

    def _saldo(self, dte):
        total = sum(p.monto for p in Dte_Detalle_Pago.objects.filter(dte=dte))
        return float(dte.monto_con_iva) - total

    # ----- listado -----

    def test_listado_solo_ventas_mismo_rut(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2001, 100000)
        emitida_ok = _crear_factura_emitida(self.env, self.proveedor, 5001, 50000)
        emitida_edel = _crear_factura_emitida(self.env, self.proveedor, 5002, 30000, emisor=self.edel)
        # Emitida a OTRO proveedor → no debe aparecer
        _crear_factura_emitida(self.env, self.otro_proveedor, 5003, 40000)
        # Una factura de COMPRA del proveedor → no debe aparecer (no es VENTA)
        _crear_factura_compra(self.env, self.proveedor, 2002, 40000)

        resp, data = self._get_disponibles(objetivo.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(data['success'])
        ids = {d['id'] for d in data['documentos']}
        self.assertEqual(ids, {emitida_ok.id, emitida_edel.id})

    def test_listado_excluye_anuladas_y_sin_saldo(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2101, 100000)
        viva = _crear_factura_emitida(self.env, self.proveedor, 5101, 50000)
        _crear_factura_emitida(self.env, self.proveedor, 5102, 50000, estado_dte='ANULADO')
        # Emitida ya compensada completamente → sin saldo disponible
        usada = _crear_factura_emitida(self.env, self.proveedor, 5103, 20000)
        Dte_Detalle_Pago.objects.create(
            dte=objetivo, metodo_pago=METODO_COMPENSACION_EMITIDA,
            voucher=str(usada.numero_documento), monto=20000,
            documento_compensacion=usada,
        )

        resp, data = self._get_disponibles(objetivo.id)
        ids = {d['id'] for d in data['documentos']}
        self.assertEqual(ids, {viva.id})

    # ----- asociar (modo existente) -----

    def test_asociar_total_marca_pagado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2201, 100000)
        emitida = _crear_factura_emitida(self.env, self.proveedor, 5201, 100000)

        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'existente',
            'documento_emitido_id': emitida.id, 'monto': 100000,
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        data = json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['monto_aplicado'], 100000)

        pago = Dte_Detalle_Pago.objects.get(dte=objetivo, metodo_pago=METODO_COMPENSACION_EMITIDA)
        self.assertEqual(pago.voucher, str(emitida.numero_documento))
        self.assertEqual(pago.documento_compensacion_id, emitida.id)

        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'PAGADO')

    def test_asociar_parcial_marca_parcial(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2301, 100000)
        emitida = _crear_factura_emitida(self.env, self.proveedor, 5301, 30000)

        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'existente',
            'documento_emitido_id': emitida.id, 'monto': 30000,
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'Parcial')
        self.assertEqual(self._saldo(objetivo), 70000)

    def test_monto_se_acota_al_menor_saldo(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2401, 100000)
        emitida = _crear_factura_emitida(self.env, self.proveedor, 5401, 30000)
        # Se pide 90.000 pero la emitida sólo tiene 30.000 de saldo.
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'existente',
            'documento_emitido_id': emitida.id, 'monto': 90000,
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(json.loads(resp.content)['monto_aplicado'], 30000)

    def test_multiuso_misma_emitida_varias_compras(self):
        """Una misma factura emitida puede compensar varias compras hasta agotar su saldo."""
        emitida = _crear_factura_emitida(self.env, self.proveedor, 5501, 100000)
        objetivo_a = _crear_factura_compra(self.env, self.proveedor, 2501, 60000)
        objetivo_b = _crear_factura_compra(self.env, self.proveedor, 2502, 60000)
        objetivo_c = _crear_factura_compra(self.env, self.proveedor, 2503, 60000)

        r1 = self._post_asociar({'dte_id': objetivo_a.id, 'modo': 'existente',
                                 'documento_emitido_id': emitida.id, 'monto': 60000})
        self.assertEqual(r1.status_code, 200, r1.content)

        # Queda 40.000 de saldo disponible en la emitida → pedir 50.000 se acota a 40.000
        r2 = self._post_asociar({'dte_id': objetivo_b.id, 'modo': 'existente',
                                 'documento_emitido_id': emitida.id, 'monto': 50000})
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(json.loads(r2.content)['monto_aplicado'], 40000)

        # Ya no queda saldo en la emitida → rechazado
        r3 = self._post_asociar({'dte_id': objetivo_c.id, 'modo': 'existente',
                                 'documento_emitido_id': emitida.id, 'monto': 10000})
        self.assertEqual(r3.status_code, 400)
        self.assertIn('saldo disponible', json.loads(r3.content)['error'])

    def test_distinto_rut_rechazado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2601, 50000)
        # Emitida dirigida a OTRO proveedor
        emitida = _crear_factura_emitida(self.env, self.otro_proveedor, 5601, 50000)
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'existente',
            'documento_emitido_id': emitida.id, 'monto': 50000,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('proveedor', json.loads(resp.content)['error'])

    def test_doble_asociacion_misma_objetivo_rechazada(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2701, 100000)
        emitida = _crear_factura_emitida(self.env, self.proveedor, 5701, 100000)
        r1 = self._post_asociar({'dte_id': objetivo.id, 'modo': 'existente',
                                 'documento_emitido_id': emitida.id, 'monto': 40000})
        self.assertEqual(r1.status_code, 200, r1.content)
        r2 = self._post_asociar({'dte_id': objetivo.id, 'modo': 'existente',
                                 'documento_emitido_id': emitida.id, 'monto': 40000})
        self.assertEqual(r2.status_code, 400)
        self.assertIn('ya está asociada', json.loads(r2.content)['error'])

    def test_incidencia_pendiente_rechaza(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2801, 50000)
        emitida = _crear_factura_emitida(self.env, self.proveedor, 5801, 50000)
        Dte_Incidencia.objects.create(
            dte=objetivo, tipo='FACTURACION',
            descripcion='Diferencia de monto', estado='PENDIENTE',
        )
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'existente',
            'documento_emitido_id': emitida.id, 'monto': 50000,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn('incidencias', json.loads(resp.content)['error'])

    # ----- asociar (modo manual) -----

    def test_modo_manual_crea_fila_sin_fk(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 2901, 100000)
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'manual',
            'numero': '9999', 'monto': 40000, 'emisor_label': 'EDEL',
            'fecha': '2026-01-15',
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        pago = Dte_Detalle_Pago.objects.get(dte=objetivo, metodo_pago=METODO_COMPENSACION_EMITIDA)
        self.assertEqual(pago.voucher, '9999')
        self.assertIsNone(pago.documento_compensacion_id)
        self.assertEqual(pago.monto, 40000)
        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'Parcial')

    def test_modo_manual_acota_a_saldo_objetivo(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 3001, 50000)
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'manual',
            'numero': '8888', 'monto': 90000, 'emisor_label': 'Nosotros',
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(json.loads(resp.content)['monto_aplicado'], 50000)

    def test_modo_manual_sin_numero_rechazado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 3101, 50000)
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'manual', 'numero': '', 'monto': 10000,
        })
        self.assertEqual(resp.status_code, 400)

    def test_monto_cero_rechazado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 3201, 50000)
        emitida = _crear_factura_emitida(self.env, self.proveedor, 6201, 50000)
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'existente',
            'documento_emitido_id': emitida.id, 'monto': 0,
        })
        self.assertEqual(resp.status_code, 400)

    # ----- desasociar -----

    def test_desasociar_revierte_estado(self):
        objetivo = _crear_factura_compra(self.env, self.proveedor, 3301, 100000)
        emitida = _crear_factura_emitida(self.env, self.proveedor, 6301, 100000)
        resp = self._post_asociar({
            'dte_id': objetivo.id, 'modo': 'existente',
            'documento_emitido_id': emitida.id, 'monto': 100000,
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'PAGADO')

        pago = Dte_Detalle_Pago.objects.get(dte=objetivo, metodo_pago=METODO_COMPENSACION_EMITIDA)
        resp_des = self.client.post(
            reverse('desasociar_documento_emitido_compensacion', args=[pago.id]),
        )
        self.assertEqual(resp_des.status_code, 200, resp_des.content)
        self.assertFalse(
            Dte_Detalle_Pago.objects.filter(
                dte=objetivo, metodo_pago=METODO_COMPENSACION_EMITIDA).exists()
        )
        objetivo.refresh_from_db()
        self.assertEqual(objetivo.estado_pago, 'Pendiente')
