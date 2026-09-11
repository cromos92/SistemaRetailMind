"""Cuadratura de caja: ecommerce propio desglosado por medio de pago.

Antes, los canales de ecommerce propio (REALSPORT / PAOLA) no estaban en
`PLATAFORMA_INTERNET_POR_CANAL`: el pago se grababa con el literal ``'Internet'``
en `tipo_tarjeta` y el clasificador de Venta Internet lo mandaba a
`total_mercadopago` por su rama `else`. Resultado: **toda** la venta del sitio
propio se reportaba como Mercado Pago, incluida la cobrada por Webpay.

Estos tests fijan el contrato nuevo:
  * `PedidoEcommerce.medio_pago` decide el `tipo_tarjeta` del pago.
  * `_bucket_venta_internet` separa Webpay / Mercado Pago / sin definir, sin
    dejar plata fuera de ningún bucket.
  * El efectivo expone bruto y neto por separado, para que la NC en efectivo no
    se pueda restar dos veces.
"""
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from app.models import (
    CANAL_ECOMMERCE_CHOICES, PedidoEcommerce, Ticket, TicketDetallePago,
)
from app.tests.factories import crear_empresa, crear_sucursal, crear_vendedor
from app.utils_ventas import canal_desde_plataforma_pago
from app.views_ecommerce import (
    _normalizar_canal,
    normalizar_medio_pago_ecommerce,
    tipo_tarjeta_venta_internet,
)
from app.views_modulo_ventas import _bucket_venta_internet, _calcular_cuadratura_data


# ==================== CLASIFICADOR (sin BD) ====================

class BucketVentaInternetTests(TestCase):
    """`_bucket_venta_internet` es el que decide en qué fila cae cada peso."""

    def test_marketplaces_por_substring(self):
        self.assertEqual(_bucket_venta_internet('Paris'), 'paris')
        self.assertEqual(_bucket_venta_internet('RIPLEY'), 'ripley')
        self.assertEqual(_bucket_venta_internet('Walmart'), 'falabella')
        self.assertEqual(_bucket_venta_internet('Falabella'), 'falabella')
        self.assertEqual(_bucket_venta_internet('Klap'), 'klap')
        self.assertEqual(_bucket_venta_internet('Mercado Pago'), 'mercadopago')
        self.assertEqual(_bucket_venta_internet('Shopify'), 'mercadopago')

    def test_ecommerce_propio_por_prefijo(self):
        self.assertEqual(_bucket_venta_internet('Ecommerce Webpay'), 'ecommerce_webpay')
        self.assertEqual(
            _bucket_venta_internet('Ecommerce Mercado Pago'), 'ecommerce_mercadopago')
        self.assertEqual(
            _bucket_venta_internet('Ecommerce Transferencia'), 'ecommerce_otros')
        self.assertEqual(_bucket_venta_internet('Ecommerce'), 'ecommerce_otros')

    def test_ecommerce_mercado_pago_no_cae_en_el_bucket_marketplace(self):
        """REGRESIÓN — el bug original.

        'Ecommerce Mercado Pago' CONTIENE 'MERCADO': si el prefijo de ecommerce
        no se evaluara primero, volvería a mezclarse con el MP de marketplace y
        el desglose no serviría de nada.
        """
        self.assertNotEqual(
            _bucket_venta_internet('Ecommerce Mercado Pago'), 'mercadopago')

    def test_lo_desconocido_cae_en_otros_y_nunca_en_mercado_pago(self):
        """REGRESIÓN — antes el `else` del loop de tickets mandaba TODO lo no
        reconocido (incluido el literal 'Internet' del ecommerce propio) a
        `total_mercadopago`, inventándole un medio de pago a esa venta."""
        for valor in ('Internet', '', None, 'Plataforma Rara'):
            self.assertEqual(_bucket_venta_internet(valor), 'ecommerce_otros')


class MercadoLibreVentaInternetTests(SimpleTestCase):
    """MercadoLibre es un MARKETPLACE, no ecommerce propio.

    Regresión de dos bugs distintos que mandaban su venta a filas equivocadas
    del Resumen de Caja:

    1. El canal no estaba en `PLATAFORMA_INTERNET_POR_CANAL`, así que al
       facturar desde Ecommerce → Pedidos el pago se grababa como 'Ecommerce'
       pelado y caía en "OTROS / S/DEF." — pidiéndole además al operador que
       fijara un medio de pago que en un marketplace no aplica.
    2. El histórico migrado de Laravel sí traía `tipo_tarjeta='Mercado Libre'`,
       pero el clasificador lo pescaba con el substring 'MERCADO' y lo sumaba a
       "MERCADO PAGO (marketplace)".
    """

    def test_el_canal_se_graba_con_su_plataforma(self):
        """AllConnected manda 'MERCADOLIBRE'; CANAL_ALIAS lo deja en 'MERCADO'."""
        self.assertEqual(_normalizar_canal('MERCADOLIBRE'), 'MERCADO')
        self.assertEqual(_normalizar_canal('Mercado Libre'), 'MERCADO')
        self.assertEqual(tipo_tarjeta_venta_internet('MERCADO', ''), 'Mercado Libre')

    def test_el_medio_de_pago_no_lo_cambia(self):
        """Como en Paris/Ripley: la plata la liquida el canal."""
        for medio in ('', 'WEBPAY', 'MERCADO_PAGO', 'TRANSFERENCIA'):
            self.assertEqual(
                tipo_tarjeta_venta_internet('MERCADO', medio), 'Mercado Libre', medio)

    def test_cae_en_su_propia_fila_y_no_en_mercado_pago(self):
        for valor in ('Mercado Libre', 'MERCADO LIBRE', 'mercadolibre'):
            self.assertEqual(_bucket_venta_internet(valor), 'mercadolibre', valor)

    def test_no_cae_en_ecommerce_otros(self):
        """REGRESIÓN bug 1: era la fila donde aparecía la venta de ML."""
        self.assertNotEqual(_bucket_venta_internet('Mercado Libre'), 'ecommerce_otros')

    def test_mercado_pago_sigue_en_su_bucket(self):
        """El orden de los `if` no puede robarle la venta a Mercado Pago."""
        self.assertEqual(_bucket_venta_internet('Mercado Pago'), 'mercadopago')
        self.assertEqual(
            _bucket_venta_internet('Ecommerce Mercado Pago'), 'ecommerce_mercadopago')

    def test_es_marketplace(self):
        """El listado de pedidos usa esto para no exigir medio de pago."""
        for canal in ('MERCADO', 'PARIS', 'RIPLEY', 'WALMART', 'SHOPIFY'):
            self.assertTrue(PedidoEcommerce(canal_origen=canal).es_marketplace, canal)
        for canal in ('REALSPORT', 'PAOLA', 'OTRO', ''):
            self.assertFalse(PedidoEcommerce(canal_origen=canal).es_marketplace, canal)

    def test_la_plataforma_vuelve_a_su_canal(self):
        """`canal_desde_plataforma_pago` es el camino inverso: lo usa el aviso de
        factura a AllConnected cuando la boleta se emitió desde el POS."""
        self.assertEqual(canal_desde_plataforma_pago('Mercado Libre'), 'MERCADO')
        # Mercado PAGO es una pasarela, no un canal de AllConnected.
        self.assertEqual(canal_desde_plataforma_pago('Mercado Pago'), 'OTRO')

    def test_el_canal_es_una_opcion_valida(self):
        self.assertIn(('MERCADO', 'Mercado Libre'), CANAL_ECOMMERCE_CHOICES)


class TipoTarjetaVentaInternetTests(TestCase):

    def test_marketplace_manda_sobre_el_medio_de_pago(self):
        """En un marketplace la plata la liquida el canal, no la pasarela."""
        self.assertEqual(tipo_tarjeta_venta_internet('PARIS', 'MERCADO_PAGO'), 'Paris')
        self.assertEqual(tipo_tarjeta_venta_internet('RIPLEY', ''), 'Ripley')

    def test_ecommerce_propio_usa_el_medio_de_pago(self):
        self.assertEqual(
            tipo_tarjeta_venta_internet('REALSPORT', 'WEBPAY'), 'Ecommerce Webpay')
        self.assertEqual(
            tipo_tarjeta_venta_internet('PAOLA', 'MERCADO_PAGO'), 'Ecommerce Mercado Pago')

    def test_sin_medio_definido_queda_ecommerce_pelado(self):
        self.assertEqual(tipo_tarjeta_venta_internet('REALSPORT', ''), 'Ecommerce')
        self.assertEqual(tipo_tarjeta_venta_internet('OTRO', ''), 'Ecommerce')


class NormalizarMedioPagoTests(TestCase):
    """La ingesta es tolerante a propósito: cada tienda nombra su pasarela
    distinto y el contrato no se renegocia por un nombre nuevo."""

    def test_alias_conocidos(self):
        for entrada in ('webpay', 'WEBPAY_PLUS', 'Transbank', 'tbk', 'oneclick'):
            self.assertEqual(normalizar_medio_pago_ecommerce(entrada), 'WEBPAY', entrada)
        for entrada in ('mercadopago', 'Mercado Pago', 'MP', 'checkout_pro'):
            self.assertEqual(
                normalizar_medio_pago_ecommerce(entrada), 'MERCADO_PAGO', entrada)
        self.assertEqual(normalizar_medio_pago_ecommerce('khipu'), 'TRANSFERENCIA')

    def test_gateways_de_la_tienda(self):
        """Los 4 valores que manda el ecommerce propio (`OrderPayment.GATEWAY_*`)
        tienen que mapear TODOS: si uno cae a '' vuelve el trabajo manual."""
        esperado = {
            'transbank': 'WEBPAY',
            'mercadopago': 'MERCADO_PAGO',
            'bank_transfer': 'TRANSFERENCIA',
            'stripe': 'OTRO',
        }
        for crudo, codigo in esperado.items():
            self.assertEqual(normalizar_medio_pago_ecommerce(crudo), codigo, crudo)

    def test_no_adivina(self):
        """Lo que no matchea vuelve '' (sin definir), NO se asume un medio."""
        for entrada in ('', None, 'pasarela-nueva-2027', 'xyz'):
            self.assertEqual(normalizar_medio_pago_ecommerce(entrada), '')


# ==================== CUADRATURA (con BD) ====================

class CuadraturaEcommerceTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    def _ticket_internet(self, correlativo, monto, tipo_tarjeta):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal,
            correlativo=correlativo, estado='PAGADO',
            subTotal=monto, descuento=0, total=monto, responsable='test-ecom',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='VENTA_INTERNET',
            tipo_tarjeta=tipo_tarjeta, monto=monto,
        )
        return ticket

    def _cuadratura(self):
        return _calcular_cuadratura_data(
            self.sucursal, timezone.localdate().strftime('%Y-%m-%d'))

    def test_webpay_no_se_cuenta_como_mercado_pago(self):
        """REGRESIÓN — el motivo del cambio: un pedido del sitio propio pagado
        con Webpay se reportaba en la fila MERCADO PAGO del Resumen de Caja."""
        self._ticket_internet(8101, 50000, 'Ecommerce Webpay')
        data = self._cuadratura()
        self.assertEqual(data['total_ecommerce_webpay'], 50000)
        self.assertEqual(data['total_mercadopago'], 0)
        self.assertEqual(data['total_venta_internet'], 50000)

    def test_desglose_ecommerce_suma_el_total_del_bloque(self):
        self._ticket_internet(8102, 10000, 'Ecommerce Webpay')
        self._ticket_internet(8103, 20000, 'Ecommerce Mercado Pago')
        self._ticket_internet(8104, 5000, 'Ecommerce')          # sin definir
        data = self._cuadratura()
        self.assertEqual(data['total_ecommerce_webpay'], 10000)
        self.assertEqual(data['total_ecommerce_mercadopago'], 20000)
        self.assertEqual(data['total_ecommerce_otros'], 5000)
        self.assertEqual(data['total_ecommerce_propio'], 35000)

    def test_ningun_peso_de_internet_queda_fuera_de_un_sub_bucket(self):
        """Invariante: los sub-buckets suman exactamente `total_venta_internet`.

        Antes el loop de DTEs no tenía rama `else`, así que una plataforma no
        reconocida sumaba al total del bloque sin aparecer en ninguna fila: el
        VENTA TOTAL y el detalle no cuadraban y nadie sabía por qué.
        """
        self._ticket_internet(8201, 11000, 'Paris')
        self._ticket_internet(8202, 12000, 'Ripley')
        self._ticket_internet(8203, 13000, 'Walmart')
        self._ticket_internet(8204, 14000, 'Mercado Pago')
        self._ticket_internet(8205, 15000, 'Klap')
        self._ticket_internet(8206, 16000, 'Ecommerce Webpay')
        self._ticket_internet(8207, 17000, 'Plataforma Rara Sin Mapear')
        data = self._cuadratura()
        suma = (
            data['total_paris'] + data['total_ripley'] + data['total_falabella']
            + data['total_mercadopago'] + data['total_klap']
            + data['total_ecommerce_propio']
        )
        self.assertEqual(suma, data['total_venta_internet'])
        self.assertEqual(data['total_venta_internet'], 98000)

    def test_mercado_pago_consolidado_suma_pos_marketplace_y_ecommerce(self):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=8301,
            estado='PAGADO', subTotal=30000, descuento=0, total=30000,
            responsable='test-ecom',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='MP_POINT_DEBITO', monto=30000)
        self._ticket_internet(8302, 7000, 'Mercado Pago')
        self._ticket_internet(8303, 4000, 'Ecommerce Mercado Pago')
        data = self._cuadratura()
        self.assertEqual(data['total_mercadopago_consolidado'], 41000)
        # Y sigue sin mezclarse: cada bucket conserva su monto.
        self.assertEqual(data['total_mercadopago_pos'], 30000)
        self.assertEqual(data['total_mercadopago'], 7000)
        self.assertEqual(data['total_ecommerce_mercadopago'], 4000)


class EfectivoBrutoNetoTests(TestCase):
    """La fila EFECTIVO siempre mostró el NETO de la NC, pero al lado se listaba
    la NC sin decirlo: el cajero la restaba otra vez al contar. Ahora el bruto
    viaja aparte para poder imprimir `bruto − NC = neto`."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    def test_bruto_menos_nc_es_el_neto(self):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=8401,
            estado='PAGADO', subTotal=25000, descuento=0, total=25000,
            responsable='test-efe',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=25000)
        data = _calcular_cuadratura_data(
            self.sucursal, timezone.localdate().strftime('%Y-%m-%d'))
        self.assertEqual(data['total_efectivo_bruto'], 25000)
        self.assertEqual(
            data['total_efectivo'],
            data['total_efectivo_bruto'] - data['total_nc_efectivo'],
        )


class PedidoMedioPagoTests(TestCase):
    """El `tipo_tarjeta` del pago sale del pedido, que es donde el operador
    corrige el medio cuando el canal no lo informa."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)

    def _pedido(self, canal='REALSPORT', medio_pago=''):
        return PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-{canal}-{medio_pago or "SD"}',
            numero_pedido_canal=f'{canal}-1',
            canal_origen=canal,
            sucursal=self.sucursal,
            cliente_nombre='Cliente Test',
            medio_pago=medio_pago,
            total=19990,
        )

    def test_pago_del_pedido_refleja_el_medio(self):
        from app.views_ecommerce import _crear_pago_ecommerce
        ticket = Ticket.objects.create(
            vendedor=crear_vendedor(empresa=self.empresa), sucursal=self.sucursal,
            correlativo=8501, estado='PAGADO', subTotal=19990, descuento=0,
            total=19990, responsable='test-ped',
        )
        pago = _crear_pago_ecommerce(ticket, self._pedido(medio_pago='WEBPAY'))
        self.assertEqual(pago.metodo_pago, 'VENTA_INTERNET')
        self.assertEqual(pago.tipo_tarjeta, 'Ecommerce Webpay')
        self.assertEqual(_bucket_venta_internet(pago.tipo_tarjeta), 'ecommerce_webpay')

    def test_pedido_sin_medio_queda_marcado_como_sin_definir(self):
        from app.views_ecommerce import _crear_pago_ecommerce
        ticket = Ticket.objects.create(
            vendedor=crear_vendedor(empresa=self.empresa, nombre='V2'),
            sucursal=self.sucursal, correlativo=8502, estado='PAGADO',
            subTotal=19990, descuento=0, total=19990, responsable='test-ped',
        )
        pago = _crear_pago_ecommerce(ticket, self._pedido())
        self.assertEqual(pago.tipo_tarjeta, 'Ecommerce')
        self.assertEqual(_bucket_venta_internet(pago.tipo_tarjeta), 'ecommerce_otros')
