"""
Tests para el flujo de ventas: crear tickets, validar stock, pagos.
"""
import json

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from app.models import (
    Ticket, Ticket_Productos, TicketDetallePago,
    Movimientos_Producto, Producto_Talla, LoteProducto,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_vendedor,
    crear_empresa_user, crear_producto_con_talla, crear_correlativo,
    crear_lote_fifo, setup_entorno_completo,
)

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class CrearTicketVentaTest(TestCase):
    """Tests para la vista crear_ticket_venta."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.client = Client()
        self.client.login(username='testuser', password='TestPass123!')
        session = self.client.session
        session['idSucursalActual'] = self.env['sucursal'].id
        session['idEmpresaActual'] = self.env['empresa'].id
        session['nombreUsuario'] = 'testuser'
        session.save()

    def _post_venta(self, productos=None, **kwargs):
        """Helper para hacer POST a crear_ticket_venta."""
        if productos is None:
            productos = [{
                'producto_talla_id': self.env['producto_talla'].id,
                'cantidad': 1,
                'precio': 20000,
                'descuento': 0,
            }]
        data = {
            'vendedor_id': self.env['vendedor'].id,
            'productos': productos,
            'cliente_nombre': kwargs.get('cliente_nombre', 'Cliente Test'),
            'cliente_rut': kwargs.get('cliente_rut', ''),
            'metodo_pago': kwargs.get('metodo_pago', 'EFECTIVO'),
            'observaciones': kwargs.get('observaciones', ''),
        }
        return self.client.post(
            reverse('crear_ticket_venta'),
            data=json.dumps(data),
            content_type='application/json',
        )

    def test_crear_ticket_exitoso(self):
        response = self._post_venta()
        data = response.json()
        self.assertTrue(data.get('success'), f"Ticket creation failed: {data.get('error')}")
        self.assertIn('ticket_id', data)
        self.assertIn('correlativo', data)

        ticket = Ticket.objects.get(id=data['ticket_id'])
        self.assertEqual(ticket.total, 20000)
        self.assertEqual(ticket.estado, 'PAGADO')

    def test_crear_ticket_descuenta_stock(self):
        stock_antes = self.env['producto_talla'].stock
        response = self._post_venta()
        data = response.json()
        self.assertTrue(data.get('success'), f"Ticket failed: {data.get('error')}")
        self.env['producto_talla'].refresh_from_db()
        stock_despues = self.env['producto_talla'].stock
        self.assertLess(stock_despues, stock_antes)
        self.assertEqual(stock_despues, stock_antes - 1)

    def test_crear_ticket_genera_movimiento(self):
        response = self._post_venta()
        data = response.json()
        self.assertTrue(data.get('success'), f"Ticket failed: {data.get('error')}")
        movimientos = Movimientos_Producto.objects.filter(
            concepto='VENTA_PUBLICO',
            ProductoTalla=self.env['producto_talla'],
        )
        self.assertTrue(movimientos.exists())
        self.assertEqual(movimientos.first().cantidad, -1)

    def test_crear_ticket_sin_productos_falla(self):
        response = self._post_venta(productos=[])
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

    def test_crear_ticket_stock_insuficiente(self):
        response = self._post_venta(productos=[{
            'producto_talla_id': self.env['producto_talla'].id,
            'cantidad': 9999,
            'precio': 20000,
            'descuento': 0,
        }])
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('Stock insuficiente', data['error'])

    def test_crear_ticket_cantidad_invalida(self):
        response = self._post_venta(productos=[{
            'producto_talla_id': self.env['producto_talla'].id,
            'cantidad': 0,
            'precio': 20000,
            'descuento': 0,
        }])
        self.assertEqual(response.status_code, 400)

    def test_crear_ticket_sin_sucursal_activa(self):
        session = self.client.session
        del session['idSucursalActual']
        session.save()
        response = self._post_venta()
        self.assertEqual(response.status_code, 400)

    def test_crear_ticket_consume_lote_fifo(self):
        lote = self.env['lote']
        stock_lote_antes = lote.cantidad_disponible
        response = self._post_venta()
        data = response.json()
        self.assertTrue(data.get('success'), f"Ticket failed: {data.get('error')}")
        lote.refresh_from_db()
        self.assertEqual(lote.cantidad_disponible, stock_lote_antes - 1)

    def test_crear_ticket_multiples_productos(self):
        _, pt2 = crear_producto_con_talla(
            self.env['sucursal'],
            articulo='Polera Test',
            talla='M',
            sku=2000001,
            stock=20,
        )
        crear_lote_fifo(pt2)

        response = self._post_venta(productos=[
            {
                'producto_talla_id': self.env['producto_talla'].id,
                'cantidad': 2,
                'precio': 20000,
                'descuento': 0,
            },
            {
                'producto_talla_id': pt2.id,
                'cantidad': 3,
                'precio': 15000,
                'descuento': 0,
            },
        ])
        data = response.json()
        self.assertTrue(data.get('success'), f"Ticket failed: {data.get('error')}")
        self.assertEqual(data['total'], 2 * 20000 + 3 * 15000)

    def test_crear_ticket_con_descuento(self):
        response = self._post_venta(productos=[{
            'producto_talla_id': self.env['producto_talla'].id,
            'cantidad': 1,
            'precio': 20000,
            'descuento': 5000,
        }])
        data = response.json()
        self.assertTrue(data.get('success'), f"Ticket failed: {data.get('error')}")
        self.assertEqual(data['total'], 15000)

    def test_crear_ticket_incrementa_correlativo(self):
        corr_antes = self.env['correlativo'].inicio
        response = self._post_venta()
        data = response.json()
        self.assertTrue(data.get('success'), f"Ticket failed: {data.get('error')}")
        self.env['correlativo'].refresh_from_db()
        self.assertEqual(self.env['correlativo'].inicio, corr_antes + 1)


class TicketPagosTest(TestCase):
    """Tests para el sistema de pagos de tickets."""

    def setUp(self):
        self.env = setup_entorno_completo()

    def test_pagos_multiples(self):
        ticket = Ticket.objects.create(
            vendedor=self.env['vendedor'],
            sucursal=self.env['sucursal'],
            correlativo=100,
            subTotal=50000,
            total=50000,
            responsable='Test',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=30000,
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='TBK_DEBITO_POS', monto=20000,
        )
        self.assertEqual(ticket.total_pagado, 50000)
        self.assertEqual(ticket.saldo_por_pagar, 0)

    def test_pago_parcial(self):
        ticket = Ticket.objects.create(
            vendedor=self.env['vendedor'],
            sucursal=self.env['sucursal'],
            correlativo=101,
            subTotal=100000,
            total=100000,
            responsable='Test',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=25000,
        )
        self.assertEqual(ticket.saldo_por_pagar, 75000)
