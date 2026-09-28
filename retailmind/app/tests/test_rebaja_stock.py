"""
Regresión de la verificación «¿Rebaja el inventario?» (26-09-2026) y de su
plan ejecutado el 28-09-2026:

1. El cobro del POS descuenta sobre las líneas FINALES del ticket (antes usaba
   el prefetch de la carga y no veía lo agregado/cambiado/quitado en la caja).
2. Un reingreso por devolución sobre una venta con cambio previo reingresa lo
   que el cliente se llevó en el cambio, con lote FIFO, y no vuelve a
   acreditar lo que otra NC ya devolvió (`services/reingreso_devolucion`).
3. No se puede cambiar dos veces la misma unidad.
4. `emitir_dte` reconoce un reenvío idéntico reciente.
"""
import json
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    CambioDevolucion, CambioDevolucionDetalle, Dte, Dte_Productos, LoteProducto,
    Movimientos_Producto, Ticket, Ticket_Productos,
)

from .factories import (
    crear_empresa, crear_lote_fifo, crear_producto_con_talla, crear_sucursal,
    crear_usuario, crear_vendedor,
)


class _Base(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa)
        cls.vendedor = crear_vendedor(empresa=cls.empresa)
        cls.vendedor.sucursales.add(cls.sucursal)
        cls.usuario = crear_usuario(username='cajero_stock', rol='vendedor')
        _, cls.pt_a = crear_producto_con_talla(
            cls.sucursal, articulo='Zapatilla A', talla='L', sku=8800001,
            stock=10, precioventa=20000)
        _, cls.pt_b = crear_producto_con_talla(
            cls.sucursal, articulo='Zapatilla A', talla='M', sku=8800002,
            stock=10, precioventa=20000)
        _, cls.bolsa = crear_producto_con_talla(
            cls.sucursal, articulo='Bolsa', talla='U', sku=8800003,
            stock=10, precioventa=500)

    def setUp(self):
        for pt in (self.pt_a, self.pt_b, self.bolsa):
            crear_lote_fifo(pt, cantidad=10, costo_unitario=1000)
        self.client = Client()
        self.client.force_login(self.usuario)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session.save()

    def _ticket(self, correlativo, lineas, estado='PENDIENTE'):
        total = sum(pt.producto.precioventa * cant for pt, cant in lineas)
        ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=correlativo,
            estado=estado, subTotal=total, descuento=0, total=total,
            responsable=self.usuario.username,
        )
        for pt, cant in lineas:
            precio = int(pt.producto.precioventa)
            Ticket_Productos.objects.create(
                idTicket=ticket, ProductoTalla=pt, stock=cant, precio=precio,
                precio_original=precio, descuento_unitario=0, subtotal=precio * cant,
            )
        return ticket

    @staticmethod
    def _linea(pt, cant):
        precio = int(pt.producto.precioventa)
        return {'producto_talla_id': pt.id, 'sku': pt.sku, 'cantidad': cant,
                'precio_unitario': precio, 'precio_original': precio,
                'descuento_unitario': 0, 'subtotal': precio * cant}

    def _cobrar(self, ticket, productos, monto):
        return self.client.post(
            reverse('registrar_pagos_ticket', args=[ticket.correlativo]),
            data=json.dumps({
                'estado': 'PAGADO', 'cliente': {},
                'pagos': [{'metodo_pago': 'EFECTIVO', 'monto': monto}],
                'productos': productos,
            }),
            content_type='application/json',
        )

    def _stock(self, pt):
        pt.refresh_from_db()
        return pt.stock


class CobroPosLineasFinalesTest(_Base):

    def test_bolsa_agregada_en_la_caja_se_descuenta(self):
        ticket = self._ticket(1, [(self.pt_a, 1)])
        resp = self._cobrar(ticket, [self._linea(self.pt_a, 1), self._linea(self.bolsa, 1)], 20500)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.pt_a), 9)
        self.assertEqual(self._stock(self.bolsa), 9)
        ticket.refresh_from_db()
        self.assertEqual(ticket.total, 20500)

    def test_cantidad_subida_en_la_caja_descuenta_lo_cobrado(self):
        ticket = self._ticket(2, [(self.pt_a, 1)])
        resp = self._cobrar(ticket, [self._linea(self.pt_a, 2)], 40000)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.pt_a), 8)

    def test_sku_quitado_en_la_caja_no_se_descuenta(self):
        ticket = self._ticket(3, [(self.pt_a, 1), (self.pt_b, 1)])
        resp = self._cobrar(ticket, [self._linea(self.pt_a, 1)], 40000)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.pt_a), 9)
        self.assertEqual(self._stock(self.pt_b), 10)

    def test_nueva_venta_desde_la_caja_valida_el_pago_contra_el_total(self):
        ticket = self._ticket(4, [])
        resp = self._cobrar(ticket, [self._linea(self.pt_a, 1)], 15000)
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.json().get('error_tipo'), 'PAGOS_INSUFICIENTES')
        self.assertEqual(self._stock(self.pt_a), 10)

        resp = self._cobrar(ticket, [self._linea(self.pt_a, 1)], 20000)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.pt_a), 9)


class ReingresoConCambioPrevioTest(_Base):
    """Venta de L (1 u) cambiada por M; después se acredita la venta."""

    def setUp(self):
        super().setUp()
        self.venta = self._ticket(10, [(self.pt_a, 1)], estado='PAGADO')
        self.dte = Dte.objects.create(
            emisor=self.empresa, numero_documento=5010,
            tipo_documento='BOLETA ELECTRONICA', monto_con_iva=20000,
            monto_neto=16807, descuento=0, estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='t', fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(), diasCredito=0, bultos=0,
            unidades_productos=1, tipo_transaccion='VENTA_PUBLICO',
            sucursal=self.sucursal, referencias=f'TICKET-{self.venta.correlativo}',
        )
        self.dp = Dte_Productos.objects.create(
            dte=self.dte, productoTalla=self.pt_a, descripcion='Zapatilla A L',
            costo=1000, sobreprecio=0, precio=20000, stock=1, activo=True)

    def _cambiar(self, estado='COMPLETADO'):
        ticket_nuevo = self._ticket(11, [(self.pt_b, 1)], estado='PAGADO')
        cambio = CambioDevolucion.objects.create(
            ticket_original=self.venta, ticket_nuevo=ticket_nuevo,
            sucursal=self.sucursal, tipo_operacion='CAMBIO_SIMPLE', estado=estado,
            monto_original=20000, monto_nuevo=20000, motivo_principal='TALLA_INCORRECTA',
            solicitado_por=self.usuario, fecha_limite_cambio=timezone.localdate(),
            fecha_ejecucion=timezone.now(),
        )
        CambioDevolucionDetalle.objects.create(
            cambio_devolucion=cambio,
            producto_original=self.venta.ticket_productos.get(),
            cantidad_original=1, producto_nuevo=self.pt_b, cantidad_nueva=1,
            precio_nuevo=20000, precio_original_unitario=20000,
            condicion_producto='PERFECTO', apto_para_venta=True,
        )
        return cambio

    def test_reingresa_lo_entregado_en_el_cambio_con_lote(self):
        from app.services.reingreso_devolucion import ReingresoVenta
        cambio = self._cambiar()
        lotes_b = LoteProducto.objects.filter(producto_talla=self.pt_b).count()

        reingreso = ReingresoVenta(self.dte)
        reingreso.reingresar(self.pt_a.id, 1, 'DEVOLUCION_NC', 'test')

        self.assertEqual(self._stock(self.pt_a), 10)   # L ya volvió con el cambio
        self.assertEqual(self._stock(self.pt_b), 11)   # vuelve la M del cliente
        self.assertEqual(LoteProducto.objects.filter(producto_talla=self.pt_b).count(), lotes_b + 1)
        self.assertEqual(reingreso.resumen['reingresos'][0]['cambio'], cambio.numero_operacion)
        self.assertTrue(reingreso.avisos())

    def test_sin_cambio_reingresa_la_talla_vendida(self):
        from app.services.reingreso_devolucion import ReingresoVenta
        reingreso = ReingresoVenta(self.dte)
        reingreso.reingresar(self.pt_a.id, 1, 'DEVOLUCION_NC', 'test')
        self.assertEqual(self._stock(self.pt_a), 11)
        self.assertEqual(self._stock(self.pt_b), 10)
        self.assertEqual(reingreso.avisos(), [])

    def test_cambio_revertido_no_cuenta(self):
        from app.services.reingreso_devolucion import ReingresoVenta
        self._cambiar(estado='REVERTIDO')
        ReingresoVenta(self.dte).reingresar(self.pt_a.id, 1, 'DEVOLUCION_NC', 'test')
        self.assertEqual(self._stock(self.pt_a), 11)
        self.assertEqual(self._stock(self.pt_b), 10)

    def test_lo_acreditado_por_una_nc_previa_no_vuelve_a_entrar(self):
        from app.services.reingreso_devolucion import ReingresoVenta
        nc = Dte.objects.create(
            emisor=self.empresa, numero_documento=90, tipo_documento='NOTA DE CREDITO',
            monto_con_iva=20000, monto_neto=16807, descuento=0, estado_pago='PAGADO',
            estado_dte='EMITIDO', responsable='t', fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(), diasCredito=0, bultos=0,
            unidades_productos=1, tipo_transaccion='DEVOLUCION', sucursal=self.sucursal,
            es_nota_credito=True, documento_afectado=self.dte,
        )
        Dte_Productos.objects.create(
            dte=nc, productoTalla=self.pt_a, descripcion='dev', costo=0, sobreprecio=0,
            precio=20000, stock=1, activo=True)
        reingreso = ReingresoVenta(self.dte)
        self.assertEqual(reingreso.pendientes(self.pt_a.id, 1), 0)
        self.assertEqual(reingreso.reingresar(self.pt_a.id, 0, 'ANULACION', 'test'), 0)
        self.assertEqual(self._stock(self.pt_a), 10)


class CambioMismaUnidadTest(_Base):

    def test_no_se_ejecuta_un_segundo_cambio_sobre_la_misma_unidad(self):
        from app.views_modulo_ventas import (
            ConflictoInventarioCambio, _validar_unidades_libres_cambio,
        )
        venta = self._ticket(20, [(self.pt_a, 1)], estado='PAGADO')
        linea = venta.ticket_productos.get()

        def cambio(estado, correlativo):
            c = CambioDevolucion.objects.create(
                ticket_original=venta, sucursal=self.sucursal,
                tipo_operacion='CAMBIO_SIMPLE', estado=estado, monto_original=20000,
                motivo_principal='TALLA_INCORRECTA', solicitado_por=self.usuario,
                fecha_limite_cambio=timezone.localdate(),
                numero_operacion=f'CD-T-{correlativo}',
            )
            d = CambioDevolucionDetalle.objects.create(
                cambio_devolucion=c, producto_original=linea, cantidad_original=1,
                producto_nuevo=self.pt_b, cantidad_nueva=1, precio_nuevo=20000,
                precio_original_unitario=20000, condicion_producto='PERFECTO')
            return c, d

        primero, d1 = cambio('SOLICITADO', 1)
        _validar_unidades_libres_cambio([d1], primero.id)  # libre: no lanza
        CambioDevolucion.objects.filter(id=primero.id).update(estado='COMPLETADO')

        segundo, d2 = cambio('SOLICITADO', 2)
        with self.assertRaises(ConflictoInventarioCambio):
            _validar_unidades_libres_cambio([d2], segundo.id)


class EmisionDteDuplicadaTest(_Base):

    def test_detecta_el_mismo_detalle_recien_emitido(self):
        from app.views import _dte_emitido_duplicado
        receptor = crear_empresa(nombre='Cliente SA', rut='76543210-3')
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=receptor, numero_documento=700,
            tipo_documento='FACTURA ELECTRONICA', monto_con_iva=Decimal('23800'),
            monto_neto=Decimal('20000'), estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable=self.usuario.username, fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(), diasCredito=0, bultos=1,
            unidades_productos=2, tipo_transaccion='VENTA', sucursal=self.sucursal,
        )
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.pt_a, descripcion='x', costo=0, sobreprecio=0,
            precio=10000, stock=2, activo=True)
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=self.pt_a, sucursal_origen=self.sucursal,
            cantidad=-2, concepto='VENTA_MAYORISTA', estado='COMPLETADO',
            responsable=self.usuario.username)

        detalle = [{'talla_id': self.pt_a.id, 'cantidad': 2, 'precio': 10000}]
        dup = _dte_emitido_duplicado(
            self.sucursal, 'FACTURA ELECTRONICA', receptor, None,
            self.usuario.username, detalle)
        self.assertEqual(dup, dte)

        otro = [{'talla_id': self.pt_a.id, 'cantidad': 3, 'precio': 10000}]
        self.assertIsNone(_dte_emitido_duplicado(
            self.sucursal, 'FACTURA ELECTRONICA', receptor, None,
            self.usuario.username, otro))
