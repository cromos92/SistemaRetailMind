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

from .factories import (
    setup_entorno_completo, crear_empresa, crear_sucursal, crear_vendedor,
    crear_producto_con_talla, crear_lote_fifo,
)


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


class DistribuirAjusteEcommerceTest(TestCase):
    """La diferencia entre el total del canal y la suma de ítems se reparte ENTRE
    las líneas de producto (sin línea 'AJUSTE' sin producto), manteniendo la suma
    EXACTA = total del pedido."""

    def setUp(self):
        self.empresa = crear_empresa(rut='78.503.140-7')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='PAO2')
        self.vendedor = crear_vendedor(empresa=self.empresa)
        # Dos productos con distinto precio RM para verificar la ponderación.
        self.prod1, self.pt1 = crear_producto_con_talla(
            self.sucursal, articulo='A', sku=1001, stock=10, precioventa=30000)
        self.prod2, self.pt2 = crear_producto_con_talla(
            self.sucursal, articulo='B', sku=1002, stock=10, precioventa=20000)
        crear_lote_fifo(self.pt1)
        crear_lote_fifo(self.pt2)

    def _pedido(self, items, total, costo_envio=0, num='1'):
        return PedidoEcommerce.objects.create(
            numero_ticket_rm=f'RM-AJ-{num}',
            numero_pedido_canal=f'PC-AJ-{num}',
            canal_origen='PARIS',
            sucursal=self.sucursal,
            rut_empresa='78503140-7',
            cliente_nombre='Cliente Test',
            total=total,
            costo_envio=costo_envio,
            items=items,
        )

    def _suma_lineas(self, ticket):
        return sum(int(tp.precio) * int(tp.stock) for tp in ticket.ticket_productos.all())

    def _descripciones(self, ticket):
        return [tp.descripcion_linea for tp in ticket.ticket_productos.all()]

    def test_paris_precio_cero_distribuye_sin_ajuste(self):
        """Paris manda total pero ítems con precio 0 → se distribuye, sin AJUSTE."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 1, 'precio_unitario': 0},
            {'sku': '1002', 'nombre': 'B', 'cantidad': 1, 'precio_unitario': 0},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 50000, costo_envio=3000, num='1'),
            self.vendedor, 1, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertIn('DESPACHO', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 50000)
        montos_prod = sorted(
            int(tp.precio) * int(tp.stock)
            for tp in ticket.ticket_productos.all() if tp.ProductoTalla)
        self.assertEqual(sum(montos_prod), 47000)          # total − envío
        self.assertEqual(montos_prod, [18800, 28200])       # ponderado 30k:20k = 3:2

    def test_qty_mayor_uno_suma_exacta(self):
        """Con cantidades > 1 e indivisibilidad, la suma sigue siendo exacta."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 3, 'precio_unitario': 0},
            {'sku': '1002', 'nombre': 'B', 'cantidad': 1, 'precio_unitario': 0},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 49999, num='2'), self.vendedor, 2, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 49999)
        for tp in ticket.ticket_productos.all():
            self.assertGreaterEqual(int(tp.precio), 0)

    def test_diff_cero_no_distribuye(self):
        """Si los ítems ya suman el total, no hay AJUSTE ni cambios."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 1, 'precio_unitario': 25000},
            {'sku': '1002', 'nombre': 'B', 'cantidad': 1, 'precio_unitario': 25000},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 50000, num='3'), self.vendedor, 3, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        self.assertEqual(self._suma_lineas(ticket), 50000)

    def test_diff_negativo_no_crea_lineas_negativas(self):
        """Ítems > total (fuera de contrato): no se emiten líneas negativas ni AJUSTE."""
        items = [
            {'sku': '1001', 'nombre': 'A', 'cantidad': 1, 'precio_unitario': 40000},
        ]
        ticket = _crear_ticket_desde_pedido(
            self._pedido(items, 30000, num='4'), self.vendedor, 4, sucursal=self.sucursal)

        self.assertNotIn('AJUSTE', self._descripciones(ticket))
        for tp in ticket.ticket_productos.all():
            self.assertGreaterEqual(int(tp.precio), 0)
