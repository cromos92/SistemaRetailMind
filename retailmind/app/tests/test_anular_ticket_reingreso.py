"""
Auditoría de caminos 29-09-2026 (H3): `anular_ticket_pendiente` reingresa lo
que de verdad SALIÓ por el ticket (egreso ligado: flujos legacy / sync /
egresos manuales) por el servicio canónico de inventario — stock plano +
lote FIFO + kardex ANULACION_TICKET en la misma transacción. Antes era un
create suelto al kardex que dejaba stock y lotes rebajados para siempre.
Sin egreso previo no escribe nada (un PENDIENTE del POS no descuenta stock).
"""
import json
from unittest import mock

from django.test import Client, TestCase

from app.models import LoteProducto, Movimientos_Producto, Ticket, Ticket_Productos
from app.services import inventario_service

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo, crear_producto_con_talla,
    crear_sucursal, crear_usuario, crear_vendedor,
)


def _patch_permisos():
    return mock.patch('app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True)


class AnulacionTicketReingresoTest(TestCase):

    URL = '/app/api/tickets/anular/'

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA')
        self.user = crear_usuario(rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.producto, self.pt = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-1', sku=1000001, stock=5, costo=12000)
        crear_lote_fifo(self.pt, cantidad=5, costo_unitario=12000)
        self.client = Client()
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.sucursal.id
        sesion.save()

    def _ticket(self, correlativo, cantidad=2):
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
            estado='PENDIENTE', subTotal=20000 * cantidad, total=20000 * cantidad,
            responsable='tester',
        )
        Ticket_Productos.objects.create(
            idTicket=ticket, ProductoTalla=self.pt, stock=cantidad, precio=20000,
            subtotal=20000 * cantidad,
        )
        return ticket

    def _egreso(self, ticket, cantidad):
        """Simula la salida real ligada al ticket (legacy / sync): stock 5->3, lote 5->3."""
        return inventario_service.egresar(
            self.pt, cantidad, 'VENTA_PUBLICO', 'legacy', sucursal_origen=self.sucursal,
            ticket=ticket, precio_unitario=20000, referencia_externa=f'TICKET_{ticket.correlativo}',
        )

    def _anular(self, correlativo):
        with _patch_permisos():
            return self.client.post(
                self.URL, data=json.dumps({'correlativo': correlativo, 'motivo': 'test'}),
                content_type='application/json',
            )

    def _stock(self):
        self.pt.refresh_from_db()
        return self.pt.stock

    def _lotes(self):
        return sum(LoteProducto.objects.filter(
            producto_talla=self.pt, activo=True, agotado=False).values_list('cantidad_disponible', flat=True))

    def test_con_egreso_previo_repone_stock_lote_y_kardex(self):
        ticket = self._ticket(101, cantidad=2)
        self._egreso(ticket, 2)
        self.assertEqual((self._stock(), self._lotes()), (3, 3))

        resp = self._anular(101)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get('success'), resp.json())
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'ANULADO')

        self.assertEqual((self._stock(), self._lotes()), (5, 5))
        mov = Movimientos_Producto.objects.get(concepto='ANULACION_TICKET')
        self.assertEqual(mov.cantidad, 2)
        self.assertEqual(mov.tipo_movimiento, 'INGRESO')
        self.assertEqual(mov.ticket_id, ticket.id)
        self.assertEqual(mov.sucursal_destino_id, self.sucursal.id)
        self.assertEqual(mov.costo, 12000)
        self.assertEqual(mov.referencia_externa, 'ANULACION_TICKET_101')
        lote = LoteProducto.objects.get(movimiento=mov)
        self.assertEqual((lote.cantidad_inicial, lote.cantidad_disponible, lote.costo_unitario), (2, 2, 12000))
        # Kardex neto del SKU vuelve a 0 (egreso -2, reingreso +2).
        total = sum(Movimientos_Producto.objects.filter(ProductoTalla=self.pt).values_list('cantidad', flat=True))
        self.assertEqual(total, 0)

    def test_sin_egreso_previo_no_escribe_nada(self):
        ticket = self._ticket(202, cantidad=2)
        resp = self._anular(202)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get('success'), resp.json())
        ticket.refresh_from_db()
        self.assertEqual(ticket.estado, 'ANULADO')
        self.assertEqual((self._stock(), self._lotes()), (5, 5))
        self.assertFalse(Movimientos_Producto.objects.exists())
        self.assertEqual(LoteProducto.objects.filter(producto_talla=self.pt).count(), 1)

    def test_reingreso_previo_del_mismo_ticket_no_se_duplica(self):
        """Reintento tras un fallo a medias: si ya volvió por este ticket, no
        se vuelve a sumar (ni stock ni lote)."""
        ticket = self._ticket(303, cantidad=2)
        self._egreso(ticket, 2)
        inventario_service.ingresar(
            self.pt, 2, 'ANULACION_TICKET', 'reintento', sucursal_destino=self.sucursal,
            ticket=ticket, costo_unitario=12000, referencia_externa='ANULACION_TICKET_303',
        )
        self.assertEqual((self._stock(), self._lotes()), (5, 5))
        resp = self._anular(303)
        self.assertTrue(resp.json().get('success'), resp.json())
        self.assertEqual((self._stock(), self._lotes()), (5, 5))
        self.assertEqual(Movimientos_Producto.objects.filter(concepto='ANULACION_TICKET').count(), 1)

    def test_reingresa_como_maximo_lo_del_ticket(self):
        """Egreso ligado mayor que la línea del ticket (datos raros): se repone
        lo que dice el ticket, no más."""
        ticket = self._ticket(404, cantidad=1)
        self._egreso(ticket, 2)
        self.assertEqual(self._stock(), 3)
        self._anular(404)
        self.assertEqual((self._stock(), self._lotes()), (4, 4))
        self.assertEqual(Movimientos_Producto.objects.get(concepto='ANULACION_TICKET').cantidad, 1)
