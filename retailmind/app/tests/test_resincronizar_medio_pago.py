"""Comando `resincronizar_medio_pago_ecommerce`: correctitud y COSTO.

El comando corre contra la BD de producción (DigitalOcean), así que el número
de consultas no es un detalle de performance: es la diferencia entre segundos y
minutos. La primera versión hacía una consulta de `TicketDetallePago` **por cada
pedido facturado** (N+1) — con miles de pedidos eran miles de round-trips.

`test_no_escala_con_la_cantidad_de_pedidos` es el test que impide que vuelva:
duplica los datos y exige el MISMO número de consultas.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from app.models import PedidoEcommerce, Ticket, TicketDetallePago
from app.tests.factories import crear_empresa, crear_sucursal, crear_vendedor


class BaseResincronizarTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)

    _seq = 0

    def _pedido_facturado(self, canal, medio_pago='', tipo_tarjeta_pago='Ecommerce',
                          dias_atras=0):
        """Pedido FACTURADO + su ticket + el pago con el tipo_tarjeta indicado."""
        BaseResincronizarTest._seq += 1
        n = BaseResincronizarTest._seq
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=90000 + n,
            estado='PAGADO', subTotal=10000, descuento=0, total=10000,
            responsable='test-resync',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='VENTA_INTERNET',
            tipo_tarjeta=tipo_tarjeta_pago, monto=10000,
        )
        pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-{n:05d}',
            numero_pedido_canal=f'{canal}-{n}',
            canal_origen=canal, sucursal=self.sucursal,
            cliente_nombre='Cliente Test', medio_pago=medio_pago,
            total=10000, ticket=ticket,
        )
        if dias_atras:
            PedidoEcommerce.objects.filter(pk=pedido.pk).update(
                fecha_recepcion=timezone.now() - timedelta(days=dias_atras))
        return pedido

    def _correr(self, **kwargs):
        out = StringIO()
        call_command('resincronizar_medio_pago_ecommerce', stdout=out, **kwargs)
        return out.getvalue()

    def _tipo_tarjeta(self, pedido):
        return (TicketDetallePago.objects
                .get(ticket_id=pedido.ticket_id).tipo_tarjeta)


class ResincronizarCorrectitudTests(BaseResincronizarTest):

    def test_marketplace_mal_clasificado_se_repara(self):
        """El caso MercadoLibre: se facturó como ecommerce propio antes de que
        el canal se declarara marketplace, y quedó cayendo en OTROS / S-DEF."""
        pedido = self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        self._correr(apply=True)
        self.assertEqual(self._tipo_tarjeta(pedido), 'Mercado Libre')

    def test_ecommerce_propio_con_medio_se_repara(self):
        pedido = self._pedido_facturado(
            'REALSPORT', medio_pago='WEBPAY', tipo_tarjeta_pago='Internet')
        self._correr(apply=True)
        self.assertEqual(self._tipo_tarjeta(pedido), 'Ecommerce Webpay')

    def test_ecommerce_propio_sin_medio_no_se_toca(self):
        """No se adivina: sin `medio_pago` no sabemos con qué se pagó."""
        pedido = self._pedido_facturado('PAOLA', tipo_tarjeta_pago='Internet')
        salida = self._correr(apply=True)
        self.assertEqual(self._tipo_tarjeta(pedido), 'Internet')
        self.assertIn('Nada que resincronizar', salida)

    def test_preview_no_escribe(self):
        pedido = self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        salida = self._correr()
        self.assertIn('PREVIEW', salida)
        self.assertEqual(self._tipo_tarjeta(pedido), 'Ecommerce')

    def test_es_idempotente(self):
        pedido = self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        self._correr(apply=True)
        salida = self._correr(apply=True)
        self.assertIn('Nada que resincronizar', salida)
        self.assertEqual(self._tipo_tarjeta(pedido), 'Mercado Libre')

    def test_canal_acepta_alias(self):
        """--canal MERCADOLIBRE tiene que encontrar los guardados como MERCADO.

        REGRESIÓN: sin normalizar el alias el comando devolvía 0 cambios y
        parecía que no había nada que reparar.
        """
        pedido = self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        self._correr(canal='MERCADOLIBRE', apply=True)
        self.assertEqual(self._tipo_tarjeta(pedido), 'Mercado Libre')

    def test_canal_acota_de_verdad(self):
        ml = self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        rs = self._pedido_facturado(
            'REALSPORT', medio_pago='WEBPAY', tipo_tarjeta_pago='Internet')
        self._correr(canal='MERCADO', apply=True)
        self.assertEqual(self._tipo_tarjeta(ml), 'Mercado Libre')
        self.assertEqual(self._tipo_tarjeta(rs), 'Internet')   # fuera del scope

    def test_dias_acota_por_fecha(self):
        """MercadoLibre es todo de septiembre: no hay que barrer el año."""
        viejo = self._pedido_facturado(
            'MERCADO', tipo_tarjeta_pago='Ecommerce', dias_atras=90)
        nuevo = self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        self._correr(dias=30, apply=True)
        self.assertEqual(self._tipo_tarjeta(nuevo), 'Mercado Libre')
        self.assertEqual(self._tipo_tarjeta(viejo), 'Ecommerce')

    def test_resumen_por_canal(self):
        self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        self._pedido_facturado(
            'REALSPORT', medio_pago='WEBPAY', tipo_tarjeta_pago='Internet')
        salida = self._correr()
        self.assertIn('Por canal:', salida)
        self.assertRegex(salida, r'MERCADO\s+2')
        self.assertRegex(salida, r'REALSPORT\s+1')

    def test_ticket_compartido_con_plataformas_en_conflicto_se_omite(self):
        """Dos pedidos apuntando al mismo ticket y pidiendo plataformas
        distintas: reescribir cualquiera de las dos sería inventar."""
        pedido = self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-CONFLICTO', numero_pedido_canal='PARIS-X',
            canal_origen='PARIS', sucursal=self.sucursal,
            cliente_nombre='Cliente Test', total=10000, ticket=pedido.ticket,
        )
        salida = self._correr(apply=True)
        self.assertIn('plataformas', salida)
        self.assertEqual(self._tipo_tarjeta(pedido), 'Ecommerce')


class ResincronizarCostoTests(BaseResincronizarTest):
    """El comando corre contra producción: el costo en consultas es el punto."""

    def test_no_escala_con_la_cantidad_de_pedidos(self):
        """REGRESIÓN N+1 — el motivo de la reescritura.

        La versión anterior hacía 1 consulta de pagos POR PEDIDO. Acá se corre
        el preview con 3 pedidos y con 12: el número de consultas tiene que ser
        EXACTAMENTE el mismo (1 de pedidos + 1 de pagos, más el savepoint que
        envuelve a cada test).
        """
        for _ in range(3):
            self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        with self.assertNumQueries(2):
            self._correr()

        for _ in range(9):
            self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        with self.assertNumQueries(2):
            self._correr()

    def test_escribe_por_grupo_no_por_pago(self):
        """8 pagos que van a 2 plataformas distintas = 2 UPDATE, no 8 saves.

        Consultas: 1 pedidos + 1 pagos + 2 updates + el par SAVEPOINT/RELEASE
        del `atomic()` = 6. Lo que importa es el 2: la versión anterior hacía
        un `save()` por pago (8 UPDATE) además del N+1 de lectura.
        """
        for _ in range(4):
            self._pedido_facturado('MERCADO', tipo_tarjeta_pago='Ecommerce')
        for _ in range(4):
            self._pedido_facturado(
                'REALSPORT', medio_pago='WEBPAY', tipo_tarjeta_pago='Internet')
        with self.assertNumQueries(6):
            self._correr(apply=True)
        self.assertEqual(
            TicketDetallePago.objects.filter(tipo_tarjeta='Mercado Libre').count(), 4)
        self.assertEqual(
            TicketDetallePago.objects.filter(tipo_tarjeta='Ecommerce Webpay').count(), 4)

    def test_el_backlog_sin_medio_no_viaja_por_la_red(self):
        """Los pedidos de ecommerce propio sin medio se descartan EN SQL.

        Son la mayor parte del histórico (todo lo anterior al campo
        `medio_pago`): traerlos para saltarlos en Python es gratis en CPU y
        carísimo en latencia. Con sólo esos, el comando ni siquiera consulta
        los pagos.
        """
        for _ in range(5):
            self._pedido_facturado('PAOLA', tipo_tarjeta_pago='Internet')
        with self.assertNumQueries(1):
            salida = self._correr()
        self.assertIn('Nada que resincronizar', salida)
