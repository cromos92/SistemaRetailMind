"""
Tests para inventario: movimientos de stock, traspasos, ajustes, FIFO.
"""
import json

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'

from app.models import (
    Traspaso, Traspaso_Detalle, AjusteInventario, AjusteInventario_Detalle,
    Movimientos_Producto, LoteProducto, Producto_Talla,
)
from app.views import registrar_movimiento_producto
from .factories import (
    crear_empresa, crear_sucursal, crear_producto_con_talla,
    crear_lote_fifo, crear_correlativo, crear_vendedor,
    crear_usuario, crear_empresa_user, setup_entorno_completo,
)


class RegistrarMovimientoProductoTest(TestCase):
    """Tests para la función centralizada registrar_movimiento_producto."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        self.producto, self.pt = crear_producto_con_talla(
            self.sucursal, stock=50,
        )

    def test_ingreso_incrementa_stock(self):
        stock_antes = self.pt.stock
        registrar_movimiento_producto(
            producto_talla=self.pt,
            concepto='RECEPCION_COMPRA',
            cantidad=10,
            responsable='Test',
            crear_lote_fifo=False,
        )
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, stock_antes + 10)

    def test_egreso_decrementa_stock(self):
        stock_antes = self.pt.stock
        registrar_movimiento_producto(
            producto_talla=self.pt,
            concepto='VENTA_PUBLICO',
            cantidad=-5,
            responsable='Test',
            crear_lote_fifo=False,
        )
        self.pt.refresh_from_db()
        self.assertEqual(self.pt.stock, stock_antes - 5)

    def test_movimiento_crea_registro(self):
        mov = registrar_movimiento_producto(
            producto_talla=self.pt,
            concepto='AJUSTE_POSITIVO',
            cantidad=3,
            responsable='Admin',
            observaciones='Ajuste de prueba',
            crear_lote_fifo=False,
        )
        self.assertIsNotNone(mov.pk)
        self.assertEqual(mov.concepto, 'AJUSTE_POSITIVO')
        self.assertEqual(mov.responsable, 'Admin')

    def test_primer_movimiento_crea_saldo_inicial(self):
        """Si no hay movimientos previos y stock > 0, se crea un INGRESO_INICIAL."""
        _, pt_nuevo = crear_producto_con_talla(
            self.sucursal, articulo='Nuevo', sku=9999999, stock=20,
        )
        registrar_movimiento_producto(
            producto_talla=pt_nuevo,
            concepto='VENTA_PUBLICO',
            cantidad=-1,
            responsable='Test',
            crear_lote_fifo=False,
        )
        movimientos = Movimientos_Producto.objects.filter(
            ProductoTalla=pt_nuevo,
        ).order_by('created_at')
        self.assertEqual(movimientos.count(), 2)
        self.assertEqual(movimientos.first().concepto, 'INGRESO_INICIAL')

    def test_ingreso_crea_lote_fifo(self):
        mov = registrar_movimiento_producto(
            producto_talla=self.pt,
            concepto='RECEPCION_COMPRA',
            cantidad=15,
            responsable='Test',
            crear_lote_fifo=True,
        )
        lotes = LoteProducto.objects.filter(
            producto_talla=self.pt,
            movimiento=mov,
        )
        self.assertTrue(lotes.exists())
        self.assertEqual(lotes.first().cantidad_inicial, 15)

    def test_egreso_no_crea_lote_fifo(self):
        lotes_antes = LoteProducto.objects.filter(producto_talla=self.pt).count()
        registrar_movimiento_producto(
            producto_talla=self.pt,
            concepto='VENTA_PUBLICO',
            cantidad=-2,
            responsable='Test',
            crear_lote_fifo=False,
        )
        lotes_despues = LoteProducto.objects.filter(producto_talla=self.pt).count()
        self.assertEqual(lotes_antes, lotes_despues)


class TraspasoModelTest(TestCase):
    """Tests para creación de traspasos entre sucursales."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')

    def test_crear_traspaso(self):
        traspaso = Traspaso.objects.create(
            sucursal_origen=self.sucursal_origen,
            sucursal_destino=self.sucursal_destino,
            numero_traspaso=1,
            solicitante='Test',
        )
        self.assertEqual(traspaso.estado, 'PENDIENTE')
        self.assertIn('ORIGEN', str(traspaso))
        self.assertIn('DESTINO', str(traspaso))

    def test_traspaso_detalle(self):
        _, pt = crear_producto_con_talla(self.sucursal_origen, stock=100)
        traspaso = Traspaso.objects.create(
            sucursal_origen=self.sucursal_origen,
            sucursal_destino=self.sucursal_destino,
            numero_traspaso=2,
            solicitante='Test',
        )
        detalle = Traspaso_Detalle.objects.create(
            traspaso=traspaso,
            producto_talla=pt,
            cantidad_solicitada=10,
            costo=15000,
            precio_venta=20000,
        )
        self.assertEqual(traspaso.detalles.count(), 1)
        self.assertEqual(detalle.cantidad_solicitada, 10)

    def test_traspaso_detalle_auto_costo_destino(self):
        _, pt = crear_producto_con_talla(self.sucursal_origen)
        traspaso = Traspaso.objects.create(
            sucursal_origen=self.sucursal_origen,
            sucursal_destino=self.sucursal_destino,
            numero_traspaso=3,
            solicitante='Test',
        )
        detalle = Traspaso_Detalle.objects.create(
            traspaso=traspaso,
            producto_talla=pt,
            cantidad_solicitada=5,
            costo=10000,
            sobreprecio=2000,
            precio_venta=15000,
        )
        self.assertEqual(detalle.costo_destino, 12000)

    def test_traspaso_unique_together(self):
        Traspaso.objects.create(
            sucursal_origen=self.sucursal_origen,
            sucursal_destino=self.sucursal_destino,
            numero_traspaso=100,
            solicitante='Test',
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Traspaso.objects.create(
                sucursal_origen=self.sucursal_origen,
                sucursal_destino=self.sucursal_destino,
                numero_traspaso=100,
                solicitante='Test2',
            )


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class CrearTraspasoViewTest(TestCase):
    """Tests para la vista crear_traspaso."""

    def setUp(self):
        self.env = setup_entorno_completo()
        self.sucursal_destino = crear_sucursal(
            self.env['empresa'], alias='DESTINO',
        )
        crear_correlativo(self.env['sucursal'], tipo_dte='TRASPASO')
        self.client = Client()
        self.client.login(username='testuser', password='TestPass123!')
        session = self.client.session
        session['idSucursalActual'] = self.env['sucursal'].id
        session['nombreUsuario'] = 'testuser'
        session.save()

    def test_crear_traspaso_exitoso(self):
        response = self.client.post(
            reverse('crear_traspaso'),
            data=json.dumps({
                'sucursal_destino_id': self.sucursal_destino.id,
                'productos': [{
                    'producto_talla_id': self.env['producto_talla'].id,
                    'cantidad': 3,
                }],
                'observaciones': 'Test traspaso',
            }),
            content_type='application/json',
        )
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('traspaso_id', data)

    def test_crear_traspaso_misma_sucursal_falla(self):
        response = self.client.post(
            reverse('crear_traspaso'),
            data=json.dumps({
                'sucursal_destino_id': self.env['sucursal'].id,
                'productos': [{
                    'producto_talla_id': self.env['producto_talla'].id,
                    'cantidad': 1,
                }],
            }),
            content_type='application/json',
        )
        data = response.json()
        self.assertFalse(data['success'])

    def test_crear_traspaso_sin_stock_falla(self):
        response = self.client.post(
            reverse('crear_traspaso'),
            data=json.dumps({
                'sucursal_destino_id': self.sucursal_destino.id,
                'productos': [{
                    'producto_talla_id': self.env['producto_talla'].id,
                    'cantidad': 99999,
                }],
            }),
            content_type='application/json',
        )
        data = response.json()
        self.assertFalse(data['success'])


class AjusteInventarioModelTest(TestCase):
    def test_crear_ajuste(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        ajuste = AjusteInventario.objects.create(
            sucursal=sucursal,
            numero_ajuste=1,
            tipo_ajuste='POSITIVO',
            solicitante='Test',
            motivo='Reconteo físico',
        )
        self.assertEqual(ajuste.estado, 'PENDIENTE')
        self.assertIn('POSITIVO', str(ajuste))

    def test_ajuste_detalle(self):
        empresa = crear_empresa()
        sucursal = crear_sucursal(empresa)
        _, pt = crear_producto_con_talla(sucursal, stock=10)
        ajuste = AjusteInventario.objects.create(
            sucursal=sucursal,
            numero_ajuste=2,
            tipo_ajuste='INVENTARIO_FISICO',
            solicitante='Test',
            motivo='Inventario fin de mes',
        )
        detalle = AjusteInventario_Detalle.objects.create(
            ajuste=ajuste,
            producto_talla=pt,
            stock_sistema=10,
            stock_fisico=8,
            diferencia=-2,
            costo=15000,
            precio_venta=20000,
        )
        self.assertEqual(detalle.diferencia, -2)


class LoteFIFOTest(TestCase):
    """Tests para la lógica FIFO de lotes."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(self.empresa)
        _, self.pt = crear_producto_con_talla(self.sucursal, stock=0)

    def test_fifo_orden_por_fecha(self):
        """Los lotes se consumen en orden FIFO (fecha de ingreso)."""
        lote1 = crear_lote_fifo(self.pt, cantidad=5, costo_unitario=10000)
        lote2 = crear_lote_fifo(self.pt, cantidad=5, costo_unitario=12000)

        lotes = LoteProducto.objects.filter(
            producto_talla=self.pt, agotado=False, activo=True,
        ).order_by('fecha_ingreso')
        self.assertEqual(lotes.first().costo_unitario, 10000)

    def test_lote_se_agota(self):
        lote = crear_lote_fifo(self.pt, cantidad=3)
        lote.cantidad_disponible = 0
        lote.save()
        lote.refresh_from_db()
        self.assertTrue(lote.agotado)
        self.assertEqual(lote.porcentaje_consumido, 100.0)
