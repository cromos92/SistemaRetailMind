"""
Tests de la cadena de cambios en Cambios/Devoluciones.

Regresión del caso real del 11-ago-2026 (boleta 287170 de NICK2): tras un cambio
parcial, la pantalla mostraba SOLO el artículo de reemplazo y los demás productos
de la venta quedaban inalcanzables. El `ticket_nuevo` de un cambio es un
comprobante de delta (línea negativa por lo devuelto + positiva por lo entregado),
no un reemplazo de la venta.
"""
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
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


class _VentaConCambioMixin:
    """Arma la venta de 3 pares y sabe ejecutarle un cambio de talla."""

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


class CadenaCambiosTicketTests(_VentaConCambioMixin, TestCase):
    """Comportamiento de los helpers que arman la pantalla."""

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

    def test_no_se_cuelan_lineas_de_una_venta_ajena(self):
        """La cadena acota qué líneas son cambiables: otra venta no entra."""
        otra_venta = self._crear_ticket(correlativo=999001, total=69990)
        self._crear_linea(otra_venta, self.pt_club, 69990)

        tickets, cambios = _cadena_cambios_ticket(self.venta)
        productos, _ = _productos_cambio_data(tickets, cambios)

        self.assertNotIn(otra_venta.id, [t.id for t in tickets])
        self.assertNotIn(otra_venta.id, {p['ticket_id'] for p in productos})

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


class CambioSobreReemplazoViaHttpTests(_VentaConCambioMixin, TestCase):
    """Recorre las vistas HTTP reales, no solo los helpers.

    Es el camino que importa: crear un cambio sobre el artículo que entró como
    reemplazo exige que la validación acepte líneas de otro ticket de la cadena.
    """

    def setUp(self):
        super().setUp()
        from app.models import EmpresaUser

        EmpresaUser.objects.get_or_create(
            user=self.user, empresa=self.empresa,
            defaults={'sucursal': self.sucursal, 'status': True, 'active': True},
        )
        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

        self.cambio_previo, self.ticket_delta = self._cambiar_talla(
            self.linea_league, self.pt_league_95, 96990)
        self.linea_reemplazo = self.ticket_delta.ticket_productos.get(
            ProductoTalla=self.pt_league_95)

    def _buscar(self):
        respuesta = self.client.get(
            reverse('buscar_documento_cambio'),
            {'numero': self.venta.correlativo, 'tipo_documento': 'ticket'},
        )
        self.assertEqual(respuesta.status_code, 200)
        return json.loads(respuesta.content)

    def _crear(self, ticket_producto_id, producto_nuevo=None, cantidad=1,
               tipo_operacion=None):
        """POST a la vista real. Sin `producto_nuevo` es una devolución pura."""
        item = {
            'ticket_producto_id': ticket_producto_id,
            'cantidad': cantidad,
            'condicion_producto': 'PERFECTO',
        }
        if producto_nuevo is not None:
            item.update({
                'producto_nuevo_id': producto_nuevo.id,
                'cantidad_nueva': 1,
                'precio_nuevo': producto_nuevo.producto.precioventa,
            })

        if tipo_operacion is None:
            tipo_operacion = 'CAMBIO_SIMPLE' if producto_nuevo is not None else 'DEVOLUCION_TOTAL'

        return self.client.post(
            reverse('crear_cambio_devolucion'),
            data=json.dumps({
                'documento_numero': self.venta.correlativo,
                'documento_tipo': 'TICKET',
                'tipo_operacion': tipo_operacion,
                'motivo_principal': 'TALLA_INCORRECTA',
                'productos': [item],
            }),
            content_type='application/json',
        )

    def test_la_busqueda_devuelve_la_venta_completa_y_el_historial(self):
        data = self._buscar()

        self.assertTrue(data['success'])
        documento = data['documento']
        self.assertEqual(documento['correlativo'], self.venta.correlativo)
        self.assertEqual(len(documento['productos']), 4)
        self.assertEqual(documento['productos_disponibles'], 3)
        self.assertTrue(documento['tiene_cambios_previos'])
        self.assertEqual(documento['cambios_anteriores'][0]['detalles'][0]['a'], 'JR7874 T9,5')

    def test_buscar_el_comprobante_de_cambio_avisa_y_carga_la_venta(self):
        respuesta = self.client.get(
            reverse('buscar_documento_cambio'),
            {'numero': self.ticket_delta.correlativo, 'tipo_documento': 'ticket'},
        )
        documento = json.loads(respuesta.content)['documento']

        self.assertTrue(documento['fue_redirigido'])
        self.assertEqual(documento['correlativo'], self.venta.correlativo)
        self.assertEqual(documento['correlativo_original'], self.ticket_delta.correlativo)

    def test_se_puede_cambiar_un_par_que_nadie_toco(self):
        """Lo que el bug bloqueaba: el resto de la venta seguía cambiable."""
        _, pt_otra_talla = crear_producto_con_talla(
            self.sucursal, articulo='JR5912', talla='9.0', sku=4833548, precioventa=69990)

        respuesta = self._crear(self.linea_club.id, pt_otra_talla)
        cuerpo = json.loads(respuesta.content)

        self.assertTrue(cuerpo.get('success'), cuerpo)
        detalle = CambioDevolucionDetalle.objects.exclude(
            cambio_devolucion=self.cambio_previo).get()
        self.assertEqual(detalle.producto_original_id, self.linea_club.id)

    def test_se_puede_volver_a_cambiar_el_articulo_del_cambio_previo(self):
        """La línea vive en el ticket del cambio, no en la venta."""
        _, pt_league_100 = crear_producto_con_talla(
            self.sucursal, articulo='JR7874', talla='10,0', sku=4831785, precioventa=96990)

        respuesta = self._crear(self.linea_reemplazo.id, pt_league_100)
        cuerpo = json.loads(respuesta.content)

        self.assertTrue(cuerpo.get('success'), cuerpo)
        nuevo = CambioDevolucion.objects.exclude(id=self.cambio_previo.id).get()
        self.assertEqual(nuevo.ticket_original_id, self.venta.id)
        self.assertEqual(nuevo.detalles.get().producto_original_id, self.linea_reemplazo.id)

    def test_una_linea_de_otra_venta_sigue_rechazada(self):
        """Ampliar a la cadena no puede volverse una puerta abierta."""
        otra_venta = self._crear_ticket(correlativo=999002, total=69990)
        linea_ajena = self._crear_linea(otra_venta, self.pt_copa, 69990)

        respuesta = self._crear(linea_ajena.id, self.pt_league_95)

        self.assertNotEqual(respuesta.status_code, 500)
        self.assertFalse(json.loads(respuesta.content).get('success'))
        self.assertEqual(CambioDevolucion.objects.count(), 1)  # solo el previo

    def test_buscando_por_folio_del_dte_tambien_sale_la_venta_completa(self):
        """El camino real del vendedor: teclea el folio de la boleta, no el ticket."""
        from app.models import Dte, Dte_Productos, Empresa

        receptor = Empresa.objects.create(
            nombre='Cliente', rut='11.111.111-1', razon_social='Cliente',
            nombre_fantasia='Cliente', giro='Particular', direccion='Calle 1',
            comuna='Santiago', ciudad='Santiago', esProveedor=False,
        )
        boleta = Dte.objects.create(
            emisor=self.empresa, receptor=receptor, numero_documento='287170',
            tipo_documento='BOLETA ELECTRONICA', monto_con_iva=236970, monto_neto=199134,
            descuento=0, estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable=self.user.username, fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(), diasCredito=0, bultos=0,
            unidades_productos=3, tipo_transaccion='VENTA_PUBLICO', sucursal=self.sucursal,
            es_nota_credito=False, hora=timezone.localtime().time(),
            vendedor=self.vendedor,
            referencias=f'TICKET-{self.venta.correlativo}',
        )
        for pt, precio in ((self.pt_club, 69990), (self.pt_league_90, 96990),
                           (self.pt_copa, 69990)):
            Dte_Productos.objects.create(
                dte=boleta, productoTalla=pt, descripcion=pt.producto.articulo,
                costo=0, sobreprecio=0, precio=precio, stock=1, activo=True,
            )

        respuesta = self.client.get(
            reverse('buscar_documento_cambio'),
            {'numero': '287170', 'tipo_documento': 'dte'},
        )
        documento = json.loads(respuesta.content)['documento']

        # Antes salía 1 sola línea: la talla de reemplazo del cambio anterior
        self.assertEqual(len(documento['productos']), 4)
        self.assertEqual(documento['productos_disponibles'], 3)
        self.assertTrue(documento['tiene_cambios_previos'])
        self.assertEqual(documento['correlativo'], self.venta.correlativo)
        reemplazo = next(p for p in documento['productos'] if p['sku'] == 4831784)
        self.assertTrue(reemplazo['es_reemplazo'])
        self.assertEqual(reemplazo['origen_cambio'], 'CD-TEST-0001')

    def test_no_se_puede_crear_una_operacion_sin_producto_de_salida(self):
        """En este módulo no se devuelve dinero: siempre tiene que salir algo.

        Es el error que dejó CD-7-202608-0014 atascada: el vendedor registró lo que
        entraba y nada que saliera, así que la operación nació debiéndole plata al
        cliente y bloqueó la venta entera.
        """
        respuesta = self._crear(self.linea_club.id, producto_nuevo=None)
        cuerpo = json.loads(respuesta.content)

        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(cuerpo.get('success'))
        self.assertEqual(cuerpo.get('code'), 'SIN_PRODUCTO_SALIDA')
        self.assertEqual(CambioDevolucion.objects.count(), 1)  # solo el cambio previo

    def test_el_bloqueo_no_depende_de_la_etiqueta_que_mande_el_front(self):
        """Aunque el front diga CAMBIO_SIMPLE, sin salida no pasa."""
        respuesta = self._crear(self.linea_club.id, producto_nuevo=None,
                                tipo_operacion='CAMBIO_SIMPLE')

        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(json.loads(respuesta.content).get('code'), 'SIN_PRODUCTO_SALIDA')

    def test_un_cambio_normal_sigue_funcionando(self):
        """El bloqueo no puede estorbar la operación de todos los días."""
        _, pt_otra_talla = crear_producto_con_talla(
            self.sucursal, articulo='JR5912', talla='9.0', sku=4833548, precioventa=69990)

        respuesta = self._crear(self.linea_club.id, pt_otra_talla)

        self.assertTrue(json.loads(respuesta.content).get('success'))
        self.assertEqual(CambioDevolucion.objects.count(), 2)

    def test_un_cobro_pendiente_en_la_cadena_bloquea_un_cambio_nuevo(self):
        """El guard financiero debe mirar toda la cadena, no solo la venta.

        El cobro quedó pendiente sobre el ticket del cambio anterior: si el guard
        solo mirara la venta, no lo vería y dejaría encadenar cambios impagos.
        """
        _, pt_cara = crear_producto_con_talla(
            self.sucursal, articulo='JR7874', talla='11,0', sku=4831786, precioventa=120000)
        self._cambiar_talla(self.linea_reemplazo, pt_cara, 120000,
                            estado='EJECUTADO_COBRO_PENDIENTE', numero='CD-TEST-PEND')

        respuesta = self._crear(self.linea_club.id, self.pt_league_95)
        cuerpo = json.loads(respuesta.content)

        self.assertFalse(cuerpo.get('success'))
        self.assertIn('NO se cobró', cuerpo.get('error', ''))
