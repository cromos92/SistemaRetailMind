"""
Tests de la facturación de pedidos de internet (ecommerce).

Foco: que la facturación replique una venta normal y que los Movimientos_Producto
queden bien etiquetados como EGRESO / VENTA_PUBLICO (regresión del bug donde el
FIFO de ecommerce los dejaba como tipo_movimiento='INGRESO').

Correr en BD local desechable:
    python manage.py test app.tests.test_ecommerce_facturacion
"""
from django.test import TestCase

from app.models import Movimientos_Producto, TicketDetallePago, Dte, PedidoEcommerce
from app.views import obtener_siguiente_correlativo
from app.views_ecommerce import _crear_ticket_desde_pedido
from app.views_modulo_ventas import generar_dte_desde_ticket

from .factories import setup_entorno_completo


class FacturacionEcommerceTest(TestCase):
    """El flujo de facturación de internet debe rebajar stock y registrar
    movimientos EGRESO igual que una venta del POS."""

    def setUp(self):
        self.entorno = setup_entorno_completo()
        self.sucursal = self.entorno['sucursal']
        self.vendedor = self.entorno['vendedor']
        self.user = self.entorno['user']
        self.producto_talla = self.entorno['producto_talla']  # stock=10
        self.lote = self.entorno['lote']                      # cantidad=10

        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-TEST0001',
            numero_pedido_canal='SHOP-9001',
            canal_origen='SHOPIFY',
            sucursal=self.sucursal,
            cliente_nombre='Cliente Internet',
            subtotal=40000,
            total=40000,
            items=[{
                'sku': str(self.producto_talla.sku),
                'nombre': 'Zapatilla Test',
                'cantidad': 2,
                'precio_unitario': 20000,
            }],
        )

    def _facturar_ticket(self):
        correlativo = obtener_siguiente_correlativo(self.sucursal, 'TICKET')
        return _crear_ticket_desde_pedido(
            self.pedido, self.vendedor, correlativo,
            responsable=self.user.username, sucursal=self.sucursal,
        )

    def test_fifo_genera_movimiento_egreso(self):
        """Con lote FIFO disponible: el movimiento es EGRESO/VENTA_PUBLICO,
        el stock y el lote bajan en la cantidad vendida."""
        ticket = self._facturar_ticket()

        movs = Movimientos_Producto.objects.filter(
            ticket=ticket, ProductoTalla=self.producto_talla,
        )
        self.assertEqual(movs.count(), 1, 'Debe crearse exactamente un movimiento')
        mov = movs.first()
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
        self.assertEqual(mov.concepto, 'VENTA_PUBLICO')
        self.assertEqual(mov.cantidad, -2)

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 8)

        self.lote.refresh_from_db(fields=['cantidad_disponible'])
        self.assertEqual(self.lote.cantidad_disponible, 8)

    def test_sin_lote_usa_fallback_egreso(self):
        """Sin lote activo (stock legacy): el fallback manual igual deja
        un movimiento EGRESO/VENTA_PUBLICO y rebaja el stock."""
        self.lote.activo = False
        self.lote.save(update_fields=['activo'])

        ticket = self._facturar_ticket()

        movs = Movimientos_Producto.objects.filter(
            ticket=ticket, ProductoTalla=self.producto_talla,
        )
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
        self.assertEqual(mov.concepto, 'VENTA_PUBLICO')
        self.assertEqual(mov.cantidad, -2)

        self.producto_talla.refresh_from_db(fields=['stock'])
        self.assertEqual(self.producto_talla.stock, 8)

    def test_no_genera_movimientos_ingreso(self):
        """Ninguna venta de internet debe quedar marcada como INGRESO."""
        ticket = self._facturar_ticket()
        self.assertFalse(
            Movimientos_Producto.objects.filter(
                ticket=ticket, tipo_movimiento='INGRESO',
            ).exists(),
            'Las ventas de internet no deben generar movimientos INGRESO',
        )

    def test_facturacion_completa_genera_dte_y_vincula_movimiento(self):
        """End-to-end: tras generar el DTE se crea el documento y el movimiento
        de stock queda vinculado al DTE y sigue siendo EGRESO.

        (El formato del TXT Acepta está cubierto aparte en test_txt_dte.py.)"""
        ticket = self._facturar_ticket()
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='VENTA_INTERNET',
            monto=int(self.pedido.total), notas='Pago SHOPIFY',
        )
        ticket.estado = 'PAGADO'
        ticket.save(update_fields=['estado'])

        dte = generar_dte_desde_ticket(ticket, 'BOLETA_ELECTRONICA', self.user)

        self.assertIsInstance(dte, Dte)
        self.assertTrue(dte.numero_documento)

        mov = Movimientos_Producto.objects.filter(ticket=ticket).first()
        self.assertIsNotNone(mov)
        self.assertEqual(mov.dte_id, dte.id, 'El movimiento debe quedar ligado al DTE')
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
