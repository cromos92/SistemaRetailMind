"""
reparar_nc_traspaso_erronea: repone en el destino lo que un ajuste
pre-recepción sacó por error de un traspaso ya recepcionado.

Escenario (calcado del DTE 17172 / NC #981 del 29-sep-2026):
  - Traspaso ORIGEN→DESTINO con una talla de 2 uds.
  - Ajuste pre-recepción «no llegó»: NC con redujo_lineas_documento=True, la
    línea del DTE queda en 0 e inactiva, el origen recupera las 2 uds (stock
    plano + lote), y se recepciona el resto.
  - En el destino alguien sumó 1 a mano (AJUSTE_POSITIVO) y lo vendió.

Se verifica: el dry-run no escribe; --aplicar deja origen 0 / destino 1
(2 llegaron, 1 vendida), revierte el +1 manual, crea la línea de recepción y
es idempotente.
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from app.models import (
    Dte, Dte_Productos, LoteProducto, Movimientos_Producto, Producto_Talla, Productos_Recepcionados,
)
from .factories import crear_empresa, crear_sucursal, crear_producto_con_talla


class RepararNcTraspasoErroneaTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        _, self.t_origen = crear_producto_con_talla(self.origen, articulo='BQ-TEST', talla='9', sku=4758174, stock=2)
        _, self.t_destino = crear_producto_con_talla(self.destino, articulo='BQ-TEST', talla='9', sku=4758174, stock=0)
        # Lote que repuso el ajuste en el origen (como _reponer_lote_traspaso).
        LoteProducto.objects.create(
            producto_talla=self.t_origen, cantidad_inicial=2, cantidad_disponible=2, costo_unitario=100,
            sobreprecio_unitario=10, precio_venta_unitario=1000,
            observaciones='Ajuste emisor pre-recepción DTE #17172',
        )

        self.dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=17172,
            tipo_documento='FACTURA ELECTRONICA', monto_neto=Decimal('0'), monto_con_iva=Decimal('0'),
            estado_pago='PENDIENTE', estado_dte='RECEPCIONADO_COMPLETO', responsable='javier',
            fecha_emision='2026-09-24', fecha_vencimiento='2026-09-24', fecha_recepcion='2026-09-29',
            diasCredito=0, bultos=1, unidades_productos=0, tipo_transaccion='TRASPASO', sucursal=self.origen,
            referencias='Método despacho: interno.',
        )
        # Línea vaciada por el ajuste.
        self.dp = Dte_Productos.objects.create(
            dte=self.dte, productoTalla=self.t_origen, descripcion='BQ-TEST - Talla 9',
            costo=100, sobreprecio=10, precio=1000, stock=0, activo=False,
        )
        # Queda otra salida viva del traspaso (otra talla): de ahí sale el destino.
        _, otra = crear_producto_con_talla(self.origen, articulo='OTRO', talla='8', sku=4758173, stock=0)
        Movimientos_Producto.objects.create(
            dte=self.dte, ProductoTalla=otra, sucursal_origen=self.origen, sucursal_destino=self.destino,
            cantidad=-2, concepto='TRASPASO_SALIDA', tipo_movimiento='EGRESO', estado='COMPLETADO',
            responsable='javier', fecha='2026-09-24', hora='17:06:00',
        )
        self.nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=981, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal('2000'), monto_con_iva=Decimal('2380'), estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='javier', fecha_emision='2026-09-29', hora='10:19:51', fecha_vencimiento='2026-09-29',
            diasCredito=0, bultos=0, unidades_productos=2, tipo_transaccion='ANULACION', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=self.dte, redujo_lineas_documento=True,
            referencias='Ajuste emisor (pre-recepción) sobre DTE #17172.',
        )
        Dte_Productos.objects.create(
            dte=self.nc, productoTalla=self.t_origen, descripcion='[AJUSTE -2] BQ-TEST - Talla 9',
            costo=100, sobreprecio=10, precio=1000, stock=2, activo=True,
        )
        # +1 a mano en el destino DESPUÉS de la NC, y la venta de esa unidad.
        Movimientos_Producto.objects.create(
            ProductoTalla=self.t_destino, sucursal_origen=self.destino, sucursal_destino=self.destino,
            cantidad=1, concepto='AJUSTE_POSITIVO', tipo_movimiento='INGRESO', estado='COMPLETADO',
            responsable='Javier Araya', fecha='2026-09-29', hora='16:01:03',
        )
        Movimientos_Producto.objects.create(
            ProductoTalla=self.t_destino, sucursal_origen=self.destino,
            cantidad=-1, concepto='VENTA_PUBLICO', tipo_movimiento='EGRESO', estado='COMPLETADO',
            responsable='andrybethca', fecha='2026-09-29', hora='16:04:49',
        )

    def _run(self, *extra):
        out = StringIO()
        call_command('reparar_nc_traspaso_erronea', '--nc-id', str(self.nc.id), *extra, stdout=out)
        return out.getvalue()

    def _stocks(self):
        return (
            Producto_Talla.objects.get(id=self.t_origen.id).stock,
            Producto_Talla.objects.get(id=self.t_destino.id).stock,
        )

    def test_dry_run_no_escribe(self):
        salida = self._run()
        self.assertIn('SIMULACI', salida)
        self.assertIn('por reparar', salida)
        self.assertEqual(self._stocks(), (2, 0))
        self.assertFalse(Movimientos_Producto.objects.filter(referencia_externa=f'REPARACION_NC_{self.nc.id}').exists())

    def test_aplicar_repone_y_revierte_el_manual(self):
        salida = self._run('--aplicar')
        self.assertIn('Aplicado', salida)
        # Origen queda sin las 2 (se fueron con el traspaso); destino 2 - 1 vendida.
        self.assertEqual(self._stocks(), (0, 1))

        tag = f'REPARACION_NC_{self.nc.id}'
        movs = Movimientos_Producto.objects.filter(referencia_externa=tag).order_by('id')
        self.assertEqual(
            [(m.concepto, m.cantidad) for m in movs],
            [('TRASPASO_SALIDA', -2), ('TRASPASO_ENTRADA', 2), ('AJUSTE_NEGATIVO', -1)],
        )
        self.assertTrue(all(m.dte_id == self.dte.id for m in movs))
        # Lote del origen consumido; lote nuevo en destino con 1 disponible.
        self.assertEqual(LoteProducto.objects.filter(producto_talla=self.t_origen, cantidad_disponible__gt=0).count(), 0)
        lote_destino = LoteProducto.objects.get(producto_talla=self.t_destino, movimiento__concepto='TRASPASO_ENTRADA')
        self.assertEqual((lote_destino.cantidad_inicial, lote_destino.cantidad_disponible, lote_destino.costo_unitario), (2, 1, 100))
        # Línea de recepción OK enlazada a la línea (inactiva) del DTE.
        rec = Productos_Recepcionados.objects.get(dte=self.dte)
        self.assertEqual((rec.estado, rec.stockArribado, rec.dte_producto_id, rec.sucursal_destino_id),
                         ('RECEPCIONADO_OK', 2, self.dp.id, self.destino.id))
        self.dte.refresh_from_db()
        self.assertIn('REPARACION NC #981', self.dte.referencias)
        # El documento no se toca.
        self.dp.refresh_from_db()
        self.assertEqual((self.dp.stock, self.dp.activo), (0, False))

    def test_segunda_pasada_no_duplica(self):
        self._run('--aplicar')
        salida = self._run('--aplicar')
        self.assertIn('ya estaban reparadas', salida)
        self.assertEqual(self._stocks(), (0, 1))
        self.assertEqual(Movimientos_Producto.objects.filter(referencia_externa=f'REPARACION_NC_{self.nc.id}').count(), 3)

    def test_aborta_si_el_origen_ya_no_tiene_las_unidades(self):
        Producto_Talla.objects.filter(id=self.t_origen.id).update(stock=1)
        with self.assertRaises(CommandError):
            self._run('--aplicar')
        self.assertEqual(self._stocks(), (1, 0))

    def _sku_sin_ficha_en_destino(self):
        """Agrega a la NC una talla que el destino nunca tuvo (el ajuste sacó el
        artículo entero antes de que la recepción creara la ficha)."""
        producto_origen, t_nueva = crear_producto_con_talla(
            self.origen, articulo='SIN-FICHA', talla='5Y', sku=4843195, stock=3,
            costo=200, precioventa=5000,
        )
        LoteProducto.objects.create(
            producto_talla=t_nueva, cantidad_inicial=3, cantidad_disponible=3, costo_unitario=200,
            sobreprecio_unitario=0, precio_venta_unitario=5000,
        )
        Dte_Productos.objects.create(
            dte=self.dte, productoTalla=t_nueva, descripcion='SIN-FICHA - Talla 5Y',
            costo=200, sobreprecio=0, precio=2500, stock=0, activo=False,
        )
        Dte_Productos.objects.create(
            dte=self.nc, productoTalla=t_nueva, descripcion='[AJUSTE -3] SIN-FICHA - Talla 5Y',
            costo=200, sobreprecio=0, precio=2500, stock=3, activo=True,
        )
        return producto_origen, t_nueva

    def test_sku_inexistente_en_destino_aborta_sin_la_opcion(self):
        self._sku_sin_ficha_en_destino()
        with self.assertRaises(CommandError):
            self._run('--aplicar')
        self.assertEqual(self._stocks(), (2, 0))
        self.assertFalse(Producto_Talla.objects.filter(sku=4843195, producto__sucursal=self.destino).exists())

    def test_crear_en_destino_crea_la_ficha_y_repone(self):
        producto_origen, t_nueva = self._sku_sin_ficha_en_destino()
        salida = self._run('--crear-en-destino')
        self.assertIn('nuevo 0→3', salida)
        self.assertFalse(Producto_Talla.objects.filter(sku=4843195, producto__sucursal=self.destino).exists())

        self._run('--crear-en-destino', '--aplicar')
        t_dest = Producto_Talla.objects.select_related('producto').get(sku=4843195, producto__sucursal=self.destino)
        self.assertEqual((t_dest.stock, t_dest.talla), (3, '5Y'))
        self.assertEqual(
            (t_dest.producto.articulo, t_dest.producto.costo, t_dest.producto.precioventa),
            ('SIN-FICHA', 200, 5000),
        )
        self.assertEqual(Producto_Talla.objects.get(id=t_nueva.id).stock, 0)
        # La otra línea (con ficha) también se repuso en la misma pasada.
        self.assertEqual(self._stocks(), (0, 1))

    def test_crear_en_destino_reusa_el_producto_del_articulo(self):
        """Si el destino ya tiene el artículo (otra talla), la talla nueva se
        cuelga de ESE producto: no se duplica la ficha."""
        producto_origen, t_nueva = self._sku_sin_ficha_en_destino()
        producto_dest, _ = crear_producto_con_talla(
            self.destino, articulo='sin-ficha', talla='6Y', sku=4843196, stock=1,
        )
        self._run('--crear-en-destino', '--aplicar')
        t_dest = Producto_Talla.objects.get(sku=4843195, producto__sucursal=self.destino)
        self.assertEqual(t_dest.producto_id, producto_dest.id)

    def test_filtro_por_articulo_repara_solo_esa_parte(self):
        """Conteo físico: llegó SIN-FICHA pero no BQ-TEST. Se repara solo lo que
        apareció; lo otro queda en el origen y se puede reparar después."""
        _, t_nueva = self._sku_sin_ficha_en_destino()
        self._run('--crear-en-destino', '--articulos', 'sin-ficha', '--aplicar')
        # BQ-TEST intacto (sigue en origen, destino en 0); SIN-FICHA repuesto.
        self.assertEqual(self._stocks(), (2, 0))
        self.assertEqual(Producto_Talla.objects.get(sku=4843195, producto__sucursal=self.destino).stock, 3)
        self.dte.refresh_from_db()
        self.assertIn('PARCIAL: SIN-FICHA', self.dte.referencias)
        # Más tarde aparece BQ-TEST: la segunda pasada repara solo lo que faltaba.
        salida = self._run('--crear-en-destino', '--aplicar')
        self.assertIn('ya reparada (se salta)', salida)
        self.assertEqual(self._stocks(), (0, 1))
        self.assertEqual(Producto_Talla.objects.get(sku=4843195, producto__sucursal=self.destino).stock, 3)

    def test_filtro_con_articulo_que_no_esta_en_la_nc_aborta(self):
        with self.assertRaises(CommandError):
            self._run('--articulos', 'NO-EXISTE')
        with self.assertRaises(CommandError):
            self._run('--skus', '999')

    def test_filtro_por_sku(self):
        self._sku_sin_ficha_en_destino()
        self._run('--crear-en-destino', '--skus', '4758174', '--aplicar')
        self.assertEqual(self._stocks(), (0, 1))
        self.assertFalse(Producto_Talla.objects.filter(sku=4843195, producto__sucursal=self.destino).exists())

    def test_aborta_si_la_nc_no_redujo_lineas(self):
        self.nc.redujo_lineas_documento = False
        self.nc.save(update_fields=['redujo_lineas_documento'])
        with self.assertRaises(CommandError):
            self._run()
