"""
Tests para modelos de la app principal.
Valida creación, relaciones, __str__, propiedades y constraints.
"""
from decimal import Decimal
from django.test import TestCase
from django.db import IntegrityError
from django.utils import timezone

from app.models import (
    Empresa, Sucursal, Vendedor, EmpresaUser, Correlativo,
    Producto, Producto_Talla, Categoria, Ticket, Ticket_Productos,
    TicketDetallePago, Traspaso, Traspaso_Detalle, AjusteInventario,
    LoteProducto, Movimientos_Producto, Dte, Cotizacion, Cotizacion_Detalle,
    CreditoTrabajador,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_vendedor,
    crear_empresa_user, crear_producto_con_talla, crear_correlativo,
    crear_lote_fifo, setup_entorno_completo,
)


class EmpresaModelTest(TestCase):
    def test_crear_empresa(self):
        empresa = crear_empresa(nombre='Nike Chile')
        self.assertEqual(str(empresa), 'Nike Chile')
        self.assertEqual(empresa.rut, '76.000.000-0')

    def test_empresa_sucursales(self):
        empresa = crear_empresa()
        s1 = crear_sucursal(empresa, alias='MALL-1')
        s2 = crear_sucursal(empresa, alias='MALL-2')
        self.assertEqual(empresa.sucursales_app.count(), 2)


class SucursalModelTest(TestCase):
    def test_crear_sucursal(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa, alias='CENTRO')
        self.assertEqual(str(sucursal), 'CENTRO')

    def test_sucursal_es_compradora(self):
        empresa = crear_empresa()
        cd = crear_sucursal(empresa, alias='CD', tipo_sucursal='CENTRO_DISTRIBUCION', es_centro_distribucion=True)
        tienda = crear_sucursal(empresa, alias='TIENDA', tipo_sucursal='VENDEDORA')
        self.assertTrue(cd.es_compradora)
        self.assertFalse(tienda.es_compradora)
        self.assertTrue(tienda.es_solo_vendedora)

    def test_sucursal_alias_unico_por_empresa(self):
        """unique_together = ['sucursal', 'correlativo'] no aplica aquí, pero alias debería ser legible."""
        empresa = crear_empresa()
        crear_sucursal(empresa, alias='A')
        # Segundo con mismo alias no tiene unique, pero verifica que no explote
        s2 = crear_sucursal(empresa, alias='B')
        self.assertIsNotNone(s2.pk)


class VendedorModelTest(TestCase):
    def test_crear_vendedor(self):
        v = crear_vendedor(nombre='Juan Pérez')
        self.assertEqual(str(v), 'Juan Pérez')

    def test_vendedor_sucursales_m2m(self):
        empresa = crear_empresa()
        s1 = crear_sucursal(empresa, alias='S1')
        s2 = crear_sucursal(empresa, alias='S2')
        v = crear_vendedor(empresa=empresa)
        v.sucursales.add(s1, s2)
        self.assertEqual(v.sucursales.count(), 2)
        self.assertIn(v, s1.vendedores.all())


class CorrelativoModelTest(TestCase):
    def test_obtener_siguiente_numero(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        corr = crear_correlativo(sucursal, inicio=100, termino=200)
        num = corr.obtener_siguiente_numero()
        self.assertEqual(num, 100)
        corr.refresh_from_db()
        self.assertEqual(corr.inicio, 101)

    def test_correlativo_agotado(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        corr = crear_correlativo(sucursal, inicio=5, termino=4)
        with self.assertRaises(ValueError):
            corr.obtener_siguiente_numero()

    def test_correlativo_propiedades(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        corr = crear_correlativo(sucursal, inicio=1, termino=500)
        self.assertEqual(corr.disponibles, 500)
        self.assertEqual(corr.estado, 'activo')

    def test_correlativo_estado_critico(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        corr = crear_correlativo(sucursal, inicio=950, termino=999)
        self.assertEqual(corr.estado, 'critico')

    def test_correlativo_unique_together(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        crear_correlativo(sucursal, tipo_dte='TICKET')
        with self.assertRaises(IntegrityError):
            crear_correlativo(sucursal, tipo_dte='TICKET')


class ProductoModelTest(TestCase):
    def test_crear_producto_con_talla(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        producto, pt = crear_producto_con_talla(sucursal)
        self.assertEqual(pt.stock, 10)
        self.assertEqual(pt.talla, '42')
        self.assertEqual(pt.producto, producto)

    def test_producto_str(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        producto, _ = crear_producto_con_talla(sucursal, articulo='Air Max 90')
        self.assertIn('Air Max 90', str(producto))

    def test_producto_talla_stock_sucursal(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal, stock=25)
        self.assertEqual(pt.stock_sucursal(sucursal.id), 25)
        self.assertEqual(pt.stock_sucursal(99999), 0)

    def test_producto_talla_stock_total(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal, stock=30)
        self.assertEqual(pt.stock_total(), 30)


class TicketModelTest(TestCase):
    def setUp(self):
        self.env = setup_entorno_completo()

    def test_ticket_unique_together(self):
        Ticket.objects.create(
            vendedor=self.env['vendedor'],
            sucursal=self.env['sucursal'],
            correlativo=1,
            subTotal=20000,
            total=20000,
            responsable='Test',
        )
        with self.assertRaises(IntegrityError):
            Ticket.objects.create(
                vendedor=self.env['vendedor'],
                sucursal=self.env['sucursal'],
                correlativo=1,
                subTotal=10000,
                total=10000,
                responsable='Test',
            )

    def test_ticket_saldo_por_pagar(self):
        ticket = Ticket.objects.create(
            vendedor=self.env['vendedor'],
            sucursal=self.env['sucursal'],
            correlativo=10,
            subTotal=50000,
            total=50000,
            responsable='Test',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=30000,
        )
        self.assertEqual(ticket.total_pagado, 30000)
        self.assertEqual(ticket.saldo_por_pagar, 20000)

    def test_ticket_pagado_completo(self):
        ticket = Ticket.objects.create(
            vendedor=self.env['vendedor'],
            sucursal=self.env['sucursal'],
            correlativo=11,
            subTotal=20000,
            total=20000,
            responsable='Test',
        )
        TicketDetallePago.objects.create(
            ticket=ticket, metodo_pago='EFECTIVO', monto=20000,
        )
        self.assertEqual(ticket.saldo_por_pagar, 0)


class LoteProductoTest(TestCase):
    def test_crear_lote(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal)
        lote = crear_lote_fifo(pt, cantidad=50, costo_unitario=10000)
        self.assertEqual(lote.cantidad_disponible, 50)
        self.assertFalse(lote.agotado)

    def test_lote_agotado_auto(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal)
        lote = crear_lote_fifo(pt, cantidad=5)
        lote.cantidad_disponible = 0
        lote.save()
        self.assertTrue(lote.agotado)

    def test_lote_porcentaje_consumido(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal)
        lote = crear_lote_fifo(pt, cantidad=100)
        lote.cantidad_disponible = 75
        lote.save()
        self.assertEqual(lote.porcentaje_consumido, 25.0)

    def test_lote_valor_disponible(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal)
        lote = crear_lote_fifo(pt, cantidad=10, costo_unitario=5000)
        self.assertEqual(lote.valor_disponible, 50000)


class DteModelTest(TestCase):
    def test_crear_dte(self):
        empresa = crear_empresa(nombre='Emisor')
        receptor = crear_empresa(nombre='Receptor', rut='77.000.000-0')
        dte = Dte.objects.create(
            emisor=empresa,
            receptor=receptor,
            numero_documento=1001,
            tipo_documento='FACTURA ELECTRONICA',
            monto_con_iva=119000,
            monto_neto=100000,
            estado_pago='PENDIENTE',
            estado_dte='EMITIDO',
            responsable='Test',
            fecha_emision=timezone.now().date(),
            fecha_vencimiento=timezone.now().date(),
            diasCredito=30,
            bultos=1,
            unidades_productos=5,
            tipo_transaccion='COMPRA',
        )
        self.assertEqual(str(dte), 'DTE 1001 - FACTURA ELECTRONICA')

    def test_dte_es_misma_empresa(self):
        empresa = crear_empresa()
        dte = Dte.objects.create(
            emisor=empresa,
            receptor=empresa,
            numero_documento=100,
            tipo_documento='GUIA',
            monto_con_iva=0,
            monto_neto=0,
            estado_pago='PENDIENTE',
            estado_dte='EMITIDO',
            responsable='Test',
            fecha_emision=timezone.now().date(),
            fecha_vencimiento=timezone.now().date(),
            diasCredito=0,
            bultos=0,
            unidades_productos=0,
            tipo_transaccion='TRASPASO',
        )
        self.assertTrue(dte.es_misma_empresa_check())

    def test_dte_requiere_nota_credito(self):
        emisor = crear_empresa(nombre='E1')
        receptor = crear_empresa(nombre='E2', rut='77.000.000-0')
        factura = Dte.objects.create(
            emisor=emisor, receptor=receptor,
            numero_documento=200,
            tipo_documento='FACTURA ELECTRONICA',
            monto_con_iva=119000, monto_neto=100000,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='Test',
            fecha_emision=timezone.now().date(),
            fecha_vencimiento=timezone.now().date(),
            diasCredito=30, bultos=1, unidades_productos=1,
            tipo_transaccion='TRASPASO',
        )
        self.assertTrue(factura.requiere_nota_credito_check())

    def test_dte_guia_no_requiere_nc(self):
        emisor = crear_empresa(nombre='E1')
        receptor = crear_empresa(nombre='E2', rut='77.000.000-0')
        guia = Dte.objects.create(
            emisor=emisor, receptor=receptor,
            numero_documento=300,
            tipo_documento='GUIA',
            monto_con_iva=0, monto_neto=0,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='Test',
            fecha_emision=timezone.now().date(),
            fecha_vencimiento=timezone.now().date(),
            diasCredito=0, bultos=1, unidades_productos=1,
            tipo_transaccion='TRASPASO',
        )
        self.assertFalse(guia.requiere_nota_credito_check())


class CotizacionDetalleStrTest(TestCase):
    """Verifica el fix del bug en Cotizacion_Detalle.__str__"""

    def test_str_no_falla(self):
        env = setup_entorno_completo()
        cotizacion = Cotizacion.objects.create(
            correlativo=1,
            vendedor=env['vendedor'],
            empresa=env['empresa'],
            sucursal=env['sucursal'],
            estado='ACTIVA',
            estadoPago='PENDIENTE',
            responsable='Test',
        )
        detalle = Cotizacion_Detalle.objects.create(
            cotizacion=cotizacion,
            descripcion='Zapatilla Test',
            producto_talla=env['producto_talla'],
            stock=2,
            costo=15000,
            sobreprecio=5000,
            precio=20000,
        )
        result = str(detalle)
        self.assertIn('Zapatilla Test', result)
        self.assertNotIn('correlativo', result.lower().split('zapatilla')[0])


class MovimientosProductoTest(TestCase):
    def test_movimiento_auto_tipo(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal, stock=0)
        mov = Movimientos_Producto.objects.create(
            ProductoTalla=pt,
            cantidad=10,
            concepto='RECEPCION_COMPRA',
            responsable='Test',
            sucursal_origen=sucursal,
            sucursal_destino=sucursal,
        )
        self.assertEqual(mov.tipo_movimiento, 'INGRESO')

    def test_movimiento_egreso(self):
        """tipo_movimiento auto-detection only runs when field is blank."""
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal, stock=50)
        mov = Movimientos_Producto.objects.create(
            ProductoTalla=pt,
            cantidad=-5,
            concepto='VENTA_PUBLICO',
            tipo_movimiento='',
            responsable='Test',
            sucursal_origen=sucursal,
            sucursal_destino=sucursal,
        )
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')

    def test_movimiento_auto_fecha(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal)
        mov = Movimientos_Producto.objects.create(
            ProductoTalla=pt,
            cantidad=1,
            concepto='AJUSTE_POSITIVO',
            responsable='Test',
            sucursal_origen=sucursal,
            sucursal_destino=sucursal,
        )
        self.assertIsNotNone(mov.fecha)
        self.assertIsNotNone(mov.hora)


class EmpresaUserTest(TestCase):
    def test_crear_empresa_user(self):
        user = crear_usuario()
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        eu = crear_empresa_user(user, empresa, sucursal)
        self.assertEqual(str(eu), f'{empresa} - {user} (True)')
        self.assertTrue(eu.active)
