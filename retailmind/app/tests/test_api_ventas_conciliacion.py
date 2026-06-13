"""
Tests para GET /api/ventas/ (VentasView) — clasificación de venta internet.

Foco: el campo `origen` debe marcar 'ecommerce' con CUALQUIERA de las 3
señales de venta internet (misma definición que el Reporte de Ventas
Internet de views_modulo_reportes):
  1. PedidoEcommerce con FK al DTE,
  2. Ticket con modulo_origen='ECOMMERCE',
  3. pago "Venta por Internet" (Dte_Detalle_Pago o TicketDetallePago) —
     cubre las boletas emitidas a mano por el POS dashboard (vendedor 1000)
     y la data histórica importada de Laravel, que no tienen ni 1 ni 2.

Caso real que motivó esto: boletas migradas de la tabla `sale` legacy
(ej. BOL 410967-410971, "Venta por Internet (Paris)") no aparecían en la
conciliación diaria de AllConnected porque solo se evaluaban señales 1 y 2.
"""
from datetime import date, time

from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import PedidoEcommerce, Ticket, TicketDetallePago
from app.models.dte import Dte, Dte_Detalle_Pago
from .factories import crear_empresa, crear_sucursal, crear_vendedor

API_KEY = 'test-api-key-ventas'
STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(
    RETAILMIND_API_KEY=API_KEY,
    STATICFILES_STORAGE=STATICFILES_STORAGE_TEST,
)
class VentasConciliacionOrigenTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(nombre='Realsport Test', rut='76.104.936-4')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='MATTA')
        self.vendedor = crear_vendedor(
            nombre='Venta Internet', empresa=self.empresa, codigo_vendedor='1000',
        )
        self.fecha = date(2026, 6, 11)
        self.url = reverse('external-ventas')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {API_KEY}'}

    # ── Helpers ──────────────────────────────────────────────────────────

    def _dte(self, numero, monto=49980, tipo='BOLETA ELECTRONICA', fecha=None):
        return Dte.objects.create(
            emisor=self.empresa,
            numero_documento=numero,
            tipo_documento=tipo,
            monto_con_iva=monto,
            monto_neto=int(monto / 1.19),
            estado_pago='PAGADO',
            estado_dte='EMITIDO',
            responsable='caja1',
            fecha_emision=fecha or self.fecha,
            fecha_vencimiento=fecha or self.fecha,
            diasCredito=0,
            bultos=0,
            unidades_productos=1,
            sucursal=self.sucursal,
            tipo_transaccion='VENTA_PUBLICO',
            hora=time(12, 0),
        )

    def _pago_dte(self, dte, metodo='VENTA_INTERNET', plataforma='Paris',
                  voucher='', monto=None):
        return Dte_Detalle_Pago.objects.create(
            dte=dte,
            metodo_pago=metodo,
            tipo_tarjeta=plataforma,
            voucher=voucher,
            monto=monto if monto is not None else int(dte.monto_con_iva),
        )

    def _ticket(self, dte, correlativo=1, modulo_origen='POS'):
        return Ticket.objects.create(
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            correlativo=correlativo,
            estado='PAGADO',
            subTotal=int(dte.monto_con_iva),
            descuento=0,
            total=int(dte.monto_con_iva),
            responsable='caja1',
            modulo_origen=modulo_origen,
            dte_generado=True,
            folio_dte=dte.numero_documento,
        )

    def _pedido(self, dte, numero='1'):
        return PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-{numero}',
            numero_pedido_canal=f'CANAL-{numero}',
            correlativo=f'PA300019{numero}',
            canal_origen='PARIS',
            sucursal=self.sucursal,
            rut_empresa=self.empresa.rut,
            cliente_nombre='Cliente Web',
            items=[],
            estado='FACTURADO',
            sub_estado='FACTURADO_OK',
            dte=dte,
        )

    def _get(self, **params):
        params.setdefault('rut_empresa', self.empresa.rut)
        params.setdefault('fecha', self.fecha.isoformat())
        resp = self.client.get(self.url, params, **self.auth)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    # ── Señal 3: pago "Venta por Internet" (boletas POS / data migrada) ──

    def test_boleta_migrada_con_pago_internet_es_ecommerce(self):
        """Boleta sin Ticket ni PedidoEcommerce (caso data migrada de Laravel):
        el pago VENTA_INTERNET basta para clasificarla origen=ecommerce."""
        dte = self._dte(410971)
        self._pago_dte(dte, plataforma='Paris', voucher='ORD-PARIS-123')

        body = self._get(origen='ecommerce')
        self.assertEqual(body['total'], 1, body)
        doc = body['data'][0]
        self.assertEqual(doc['origen'], 'ecommerce')
        self.assertEqual(doc['via_emision'], 'pos')           # emitida a mano
        self.assertEqual(doc['plataforma_pago'], 'Paris')
        self.assertEqual(doc['canal_origen'], 'PARIS')
        self.assertEqual(doc['referencia_externa'], 'ORD-PARIS-123')
        self.assertIsNone(doc['numero_ticket_rm'])

    def test_pago_internet_variantes_legibles_tambien_clasifican(self):
        """En BD conviven 'VENTA_INTERNET', 'Venta por Internet' (display
        copiado al DTE) y 'Venta Internet' (migración). Todas clasifican."""
        for numero, metodo in (
            (410901, 'Venta por Internet'),
            (410902, 'Venta Internet'),
            (410903, 'VENTA_INTERNET'),
        ):
            self._pago_dte(self._dte(numero), metodo=metodo)

        body = self._get(origen='ecommerce')
        self.assertEqual(body['total'], 3, body)

    def test_pago_internet_en_ticket_es_respaldo(self):
        """Si el detalle de pago no se copió al DTE, el pago VENTA_INTERNET
        del Ticket (vía folio) igualmente clasifica como ecommerce."""
        dte = self._dte(410972)
        ticket = self._ticket(dte, correlativo=77, modulo_origen='POS')
        TicketDetallePago.objects.create(
            ticket=ticket,
            metodo_pago='VENTA_INTERNET',
            tipo_tarjeta='Ripley',
            voucher='ORD-RIPLEY-9',
            monto=int(dte.monto_con_iva),
        )

        body = self._get(origen='ecommerce')
        self.assertEqual(body['total'], 1, body)
        doc = body['data'][0]
        self.assertEqual(doc['canal_origen'], 'RIPLEY')
        self.assertEqual(doc['referencia_externa'], 'ORD-RIPLEY-9')

    def test_plataforma_sin_canal_mapeado_cae_a_otro(self):
        dte = self._dte(410973)
        self._pago_dte(dte, plataforma='Falabella')

        doc = self._get(origen='ecommerce')['data'][0]
        self.assertEqual(doc['canal_origen'], 'OTRO')
        self.assertEqual(doc['plataforma_pago'], 'Falabella')

    # ── Señales 1 y 2 siguen funcionando igual ──────────────────────────

    def test_pedido_ecommerce_sigue_clasificando(self):
        dte = self._dte(410974)
        self._pedido(dte, numero='4')

        doc = self._get(origen='ecommerce')['data'][0]
        self.assertEqual(doc['origen'], 'ecommerce')
        self.assertEqual(doc['via_emision'], 'ecommerce')
        self.assertEqual(doc['numero_ticket_rm'], 'RM-4')
        self.assertEqual(doc['referencia_externa'], 'CANAL-4')
        self.assertEqual(doc['folio_despacho'], 'PA3000194')
        self.assertEqual(doc['canal_origen'], 'PARIS')

    def test_ticket_modulo_ecommerce_sigue_clasificando(self):
        dte = self._dte(410975)
        self._ticket(dte, correlativo=88, modulo_origen='ECOMMERCE')

        doc = self._get(origen='ecommerce')['data'][0]
        self.assertEqual(doc['origen'], 'ecommerce')
        self.assertEqual(doc['via_emision'], 'ecommerce')

    # ── Las ventas presenciales NO se contaminan ─────────────────────────

    def test_venta_presencial_sigue_siendo_pos(self):
        dte = self._dte(410976)
        self._pago_dte(dte, metodo='EFECTIVO', plataforma='')

        self.assertEqual(self._get(origen='ecommerce')['total'], 0)
        body = self._get(origen='pos')
        self.assertEqual(body['total'], 1)
        doc = body['data'][0]
        self.assertEqual(doc['origen'], 'pos')
        self.assertIsNone(doc['plataforma_pago'])
        self.assertIsNone(doc['canal_origen'])

    def test_sin_filtro_origen_trae_ambas(self):
        self._pago_dte(self._dte(410977), plataforma='Paris')
        self._pago_dte(self._dte(410978), metodo='EFECTIVO', plataforma='')

        body = self._get()
        self.assertEqual(body['total'], 2)
        origenes = sorted(d['origen'] for d in body['data'])
        self.assertEqual(origenes, ['ecommerce', 'pos'])

    # ── Búsqueda por referencia vía voucher del pago ─────────────────────

    def test_referencia_encuentra_boleta_por_voucher_sin_fecha(self):
        dte = self._dte(410979)
        self._pago_dte(dte, plataforma='Paris', voucher='ORD-HISTORICA-55')

        resp = self.client.get(
            self.url,
            {'rut_empresa': self.empresa.rut, 'referencia': 'ord-historica-55'},
            **self.auth,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['total'], 1, body)
        self.assertEqual(body['data'][0]['numero_documento'], '410979')

    def test_referencia_no_matchea_voucher_de_pago_no_internet(self):
        """El voucher de un pago TBK/EFECTIVO (código de autorización numérico,
        parecido a un N° de pedido) NO debe cruzar con la referencia: solo el
        voucher de un pago 'Venta por Internet' identifica un pedido."""
        dte = self._dte(410980)
        # Boleta presencial cuyo voucher TBK coincide con la referencia buscada.
        self._pago_dte(dte, metodo='TBK_CREDITO_POS', plataforma='', voucher='778899')

        resp = self.client.get(
            self.url,
            {'rut_empresa': self.empresa.rut, 'referencia': '778899'},
            **self.auth,
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['total'], 0, resp.content)
