"""Tests del costeo FIFO persistido en la línea de venta.

`consumir_stock_fifo()` calcula de qué lotes salió cada unidad vendida, pero el
POS descartaba ese retorno: `Ticket_Productos.costo_fifo` quedaba en 0 y
`lotes_utilizados` vacío en el 100% del histórico, así que era imposible saber a
qué costo real y desde qué DTE de compra se vendió algo.

Estos tests cubren el contrato del helper que persiste ese dato.
"""
import json

from django.test import TestCase

from app.models import Ticket, Ticket_Productos
from app.utils_ventas import persistir_costeo_fifo

from .factories import (
    crear_correlativo,
    crear_empresa,
    crear_producto_con_talla,
    crear_sucursal,
    crear_vendedor,
)


class PersistirCosteoFifoTests(TestCase):

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.vendedor.sucursales.add(self.sucursal)
        crear_correlativo(self.sucursal)
        self.producto, self.producto_talla = crear_producto_con_talla(self.sucursal)

        self.ticket = Ticket.objects.create(
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            correlativo=1,
            estado='PAGADO',
            subTotal=20000,
            descuento=0,
            total=20000,
            responsable='Test',
        )

    def _linea(self, cantidad):
        return Ticket_Productos.objects.create(
            ProductoTalla=self.producto_talla,
            idTicket=self.ticket,
            stock=cantidad,
            precio=20000,
            subtotal=20000 * cantidad,
        )

    def test_guarda_costo_unitario_no_el_total(self):
        """Los reportes calculan stock * costo_fifo, así que debe ser UNITARIO."""
        linea = self._linea(cantidad=3)
        lotes = [{'lote_id': 1, 'cantidad_consumida': 3, 'costo_unitario': 5000,
                  'costo_total': 15000, 'dte_origen': 991}]

        self.assertTrue(persistir_costeo_fifo(linea, 15000, lotes))

        linea.refresh_from_db()
        self.assertEqual(linea.costo_fifo, 5000)
        # El costo total de la línea se reconstruye como en los reportes.
        self.assertEqual(linea.costo_fifo * linea.stock, 15000)

    def test_guarda_json_valido_con_el_dte_de_origen(self):
        """El campo debe ser JSON parseable, no un repr() de Python."""
        linea = self._linea(cantidad=2)
        lotes = [
            {'lote_id': 7, 'cantidad_consumida': 1, 'costo_unitario': 4000,
             'costo_total': 4000, 'dte_origen': 12345},
            {'lote_id': 8, 'cantidad_consumida': 1, 'costo_unitario': 6000,
             'costo_total': 6000, 'dte_origen': 12346},
        ]

        persistir_costeo_fifo(linea, 10000, lotes)
        linea.refresh_from_db()

        datos = json.loads(linea.lotes_utilizados)
        self.assertEqual(len(datos), 2)
        # La pregunta que el sistema no podía responder: de qué factura vino.
        self.assertEqual([d['dte_origen'] for d in datos], [12345, 12346])
        self.assertEqual(linea.costo_fifo, 5000)

    def test_serializa_fechas_sin_reventar(self):
        """`fecha_ingreso_lote` viene como datetime desde consumir_stock_fifo."""
        from django.utils import timezone

        linea = self._linea(cantidad=1)
        lotes = [{'lote_id': 3, 'cantidad_consumida': 1, 'costo_unitario': 8000,
                  'costo_total': 8000, 'fecha_ingreso_lote': timezone.now(),
                  'dte_origen': None}]

        self.assertTrue(persistir_costeo_fifo(linea, 8000, lotes))
        linea.refresh_from_db()
        self.assertEqual(len(json.loads(linea.lotes_utilizados)), 1)

    def test_cantidad_cero_no_divide_por_cero(self):
        linea = self._linea(cantidad=0)
        self.assertTrue(persistir_costeo_fifo(linea, 0, []))
        linea.refresh_from_db()
        self.assertEqual(linea.costo_fifo, 0)

    def test_nunca_propaga_excepciones(self):
        """Un fallo guardando trazabilidad no puede voltear un cobro ya hecho."""
        self.assertFalse(persistir_costeo_fifo(None, 1000, []))
