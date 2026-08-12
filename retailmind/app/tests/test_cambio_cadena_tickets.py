"""
Tests de la cadena de cambios en Cambios/Devoluciones.

Regresión del caso real del 11-ago-2026 (boleta 287170 de NICK2): tras un cambio
parcial, la pantalla mostraba SOLO el artículo de reemplazo y los demás productos
de la venta quedaban inalcanzables. El `ticket_nuevo` de un cambio es un
comprobante de delta (línea negativa por lo devuelto + positiva por lo entregado),
no un reemplazo de la venta.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import (
    CambioDevolucion,
    CambioDevolucionDetalle,
    Ticket,
    Ticket_Productos,
)
from app.tests.factories import (
    crear_empresa,
    crear_producto_con_talla,
    crear_sucursal,
    crear_usuario,
    crear_vendedor,
)
from app.views_modulo_ventas import (
    _cadena_cambios_ticket,
    _historial_cambios_data,
    _productos_cambio_data,
    _ticket_raiz_cambio,
)


class CadenaCambiosTicketTests(TestCase):
    """Reproduce la venta de 3 pares con un cambio de talla sobre uno de ellos."""

    def setUp(self):
        self.user = crear_usuario(username='vendedor_cambios')
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.vendedor.sucursales.add(self.sucursal)

        # 3 artículos vendidos + la talla de reemplazo del que se cambió
        _, self.pt_club = crear_producto_con_talla(
            self.sucursal, articulo='JR5912', talla='8.5', sku=4833547, precioventa=69990)
        _, self.pt_league_90 = crear_producto_con_talla(
            self.sucursal, articulo='JR7874', talla='9,0', sku=4831783, precioventa=96990)
        _, self.pt_copa = crear_producto_con_talla(
            self.sucursal, articulo='JR6180', talla='8,0', sku=4829231, precioventa=69990)
        _, self.pt_league_95 = crear_producto_con_talla(
            self.sucursal, articulo='JR7874', talla='9,5', sku=4831784, precioventa=96990)

        self.venta = self._crear_ticket(correlativo=184985, total=236970)
        self.linea_club = self._crear_linea(self.venta, self.pt_club, 69990)
        self.linea_league = self._crear_linea(self.venta, self.pt_league_90, 96990)
        self.linea_copa = self._crear_linea(self.venta, self.pt_copa, 69990)

    # ---------- helpers ----------

    def _crear_ticket(self, correlativo, total):
        return Ticket.objects.create(
            correlativo=correlativo,
            vendedor=self.vendedor,
            sucursal=self.sucursal,
            subTotal=total,
            total=total,
            estado='PAGADO',
            responsable='Tester',
        )

    def _crear_linea(self, ticket, producto_talla, precio, cantidad=1):
        return Ticket_Productos.objects.create(
            idTicket=ticket,
            ProductoTalla=producto_talla,
            stock=cantidad,
            precio=precio,
            precio_original=precio,
            descuento_unitario=0,
            subtotal=precio * cantidad,
        )

    def _cambiar_talla(self, linea_original, producto_nuevo, precio_nuevo,
                       estado='COMPLETADO', numero='CD-TEST-0001'):
        """Ejecuta un cambio como lo hace el sistema: ticket_nuevo con el delta."""
        ticket_delta = self._crear_ticket(correlativo=linea_original.idTicket.correlativo + 1, total=0)
        self._crear_linea(ticket_delta, linea_original.ProductoTalla, -linea_original.precio)
        self._crear_linea(ticket_delta, producto_nuevo, precio_nuevo)

        cambio = CambioDevolucion.objects.create(
            ticket_original=linea_original.idTicket,
            ticket_nuevo=ticket_delta,
            sucursal=self.sucursal,
            numero_operacion=numero,
            tipo_operacion='CAMBIO_SIMPLE',
            estado=estado,
            fecha_limite_cambio=timezone.localdate() + timedelta(days=30),
            monto_original=linea_original.precio,
            monto_nuevo=precio_nuevo,
            diferencia_monto=precio_nuevo - linea_original.precio,
            motivo_principal='TALLA_INCORRECTA',
            solicitado_por=self.user,
        )
        CambioDevolucionDetalle.objects.create(
            cambio_devolucion=cambio,
            producto_original=linea_original,
            cantidad_original=1,
            producto_nuevo=producto_nuevo,
            cantidad_nueva=1,
            precio_nuevo=precio_nuevo,
            precio_original_unitario=linea_original.precio,
            condicion_producto='PERFECTO',
        )
        return cambio, ticket_delta

    # ---------- tests ----------

    def test_venta_sin_cambios_muestra_todos_sus_productos(self):
        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, disponibles = _productos_cambio_data(tickets, cambios)

        self.assertEqual(len(productos), 3)
        self.assertEqual(disponibles, 3)
        self.assertEqual(cambios, [])
        self.assertTrue(all(p['origen'] == 'VENTA' for p in productos))

    def test_cambio_parcial_no_esconde_el_resto_de_la_venta(self):
        """El bug original: quedaba visible solo el artículo de reemplazo."""
        self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)

        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, disponibles = _productos_cambio_data(tickets, cambios)

        skus = {p['sku'] for p in productos}
        self.assertEqual(skus, {4833547, 4831783, 4829231, 4831784})

        # Los 2 pares que nadie tocó siguen cambiables, y el reemplazo también
        self.assertEqual(disponibles, 3)

    def test_el_articulo_ya_cambiado_queda_sin_disponibilidad(self):
        self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)

        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, _ = _productos_cambio_data(tickets, cambios)
        league_90 = next(p for p in productos if p['sku'] == 4831783)

        self.assertEqual(league_90['cantidad_ya_cambiada'], 1)
        self.assertEqual(league_90['cantidad_disponible'], 0)
        self.assertTrue(league_90['ya_cambiado'])
        self.assertEqual(league_90['cambios_linea'][0]['reemplazo'], 'JR7874 T9,5')

    def test_el_reemplazo_se_marca_como_venido_de_un_cambio(self):
        self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)

        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, _ = _productos_cambio_data(tickets, cambios)
        reemplazo = next(p for p in productos if p['sku'] == 4831784)

        self.assertTrue(reemplazo['es_reemplazo'])
        self.assertEqual(reemplazo['origen'], 'CAMBIO')
        self.assertEqual(reemplazo['origen_cambio'], 'CD-TEST-0001')
        self.assertEqual(reemplazo['cantidad_disponible'], 1)

    def test_la_linea_negativa_del_delta_no_se_ofrece(self):
        """La línea en negativo del ticket de cambio no es un producto cambiable."""
        self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)

        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, _ = _productos_cambio_data(tickets, cambios)

        self.assertTrue(all(p['precio_lista'] > 0 for p in productos))
        self.assertEqual(sum(1 for p in productos if p['sku'] == 4831783), 1)

    def test_buscar_el_comprobante_del_cambio_lleva_a_la_venta(self):
        _, ticket_delta = self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)

        self.assertEqual(_ticket_raiz_cambio(ticket_delta).id, self.venta.id)
        self.assertEqual(_ticket_raiz_cambio(self.venta).id, self.venta.id)

    def test_segundo_cambio_sobre_el_reemplazo_mantiene_la_cadena(self):
        """Se puede volver a cambiar lo que entró por un cambio anterior."""
        _, ticket_delta = self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)
        linea_reemplazo = ticket_delta.ticket_productos.get(ProductoTalla=self.pt_league_95)

        _, pt_league_100 = crear_producto_con_talla(
            self.sucursal, articulo='JR7874', talla='10,0', sku=4831785, precioventa=96990)
        self._cambiar_talla(linea_reemplazo, pt_league_100, 96990, numero='CD-TEST-0002')

        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, disponibles = _productos_cambio_data(tickets, cambios)

        self.assertEqual(len(cambios), 2)
        self.assertEqual({p['sku'] for p in productos},
                         {4833547, 4831783, 4829231, 4831784, 4831785})
        # Los 2 originales intactos + la última talla entregada
        self.assertEqual(disponibles, 3)

    def test_cambio_solicitado_reserva_la_unidad_pero_no_agrega_reemplazo(self):
        """Un cambio aún sin ejecutar no tiene ticket_nuevo que mostrar."""
        cambio, ticket_delta = self._cambiar_talla(
            self.linea_league, self.pt_league_95, 96990, estado='SOLICITADO')

        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, disponibles = _productos_cambio_data(tickets, cambios)

        self.assertNotIn(ticket_delta.id, [t.id for t in tickets])
        self.assertEqual(len(productos), 3)
        self.assertEqual(disponibles, 2)
        self.assertEqual(cambios, [cambio])

    def test_historial_dice_que_se_entrego_a_cambio_de_que(self):
        self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)

        _, cambios = _cadena_cambios_ticket(self.venta)
        historial = _historial_cambios_data(cambios)

        self.assertEqual(len(historial), 1)
        registro = historial[0]
        self.assertEqual(registro['numero_operacion'], 'CD-TEST-0001')
        self.assertEqual(registro['estado'], 'Completado')
        self.assertEqual(registro['detalles'][0]['de'], 'JR7874 T9,0')
        self.assertEqual(registro['detalles'][0]['a'], 'JR7874 T9,5')

    def test_cadena_no_se_cuelga_con_referencias_circulares(self):
        """Un ticket_nuevo que apunta de vuelta al original no debe colgar el bucle."""
        cambio, ticket_delta = self._cambiar_talla(self.linea_league, self.pt_league_95, 96990)
        CambioDevolucion.objects.create(
            ticket_original=ticket_delta,
            ticket_nuevo=self.venta,
            sucursal=self.sucursal,
            numero_operacion='CD-TEST-CICLO',
            tipo_operacion='CAMBIO_SIMPLE',
            estado='COMPLETADO',
            fecha_limite_cambio=timezone.localdate() + timedelta(days=30),
            monto_original=0,
            monto_nuevo=0,
            diferencia_monto=0,
            motivo_principal='OTRO',
            solicitado_por=self.user,
        )

        tickets, cambios = _cadena_cambios_ticket(self.venta)

        self.assertEqual({t.id for t in tickets}, {self.venta.id, ticket_delta.id})
        self.assertEqual(len(cambios), 2)
        self.assertEqual(_ticket_raiz_cambio(ticket_delta).id, self.venta.id)
