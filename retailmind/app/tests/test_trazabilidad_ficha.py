"""
Tests de la ficha de trazabilidad por producto (api_trazabilidad_producto).

Cubre los tres huecos que tenía la pantalla `/app/trazabilidad-producto/`:

  1. Los lotes FIFO no exponían el DTE de origen ni el proveedor, pese a que
     `LoteProducto` guarda las FKs `dte` y `movimiento`.
  2. El kardex sumaba TODOS los movimientos (incluidos ANULADO / PENDIENTE),
     lo que producía descuadres falsos en `cuadra` / `diferencia`.
  3. No existía la pata de COMPRA: `Productos_Recepcionados` (esperado vs
     recibido, faltante / dañado / sobrante, quién recepcionó).

Ejecutar (en BD de test, NO producción):
    DATABASE_URL="sqlite:////tmp/tT.sqlite3" python manage.py test app.tests.test_trazabilidad_ficha
"""
import json
from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from app.models import (
    Compras, Compras_Producto, Compras_Producto_Talla,
    Dte, LoteProducto, Movimientos_Producto, Productos_Recepcionados,
)
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)
from app.views_modulo_existencias_nuevo import api_trazabilidad_producto


SKU = 7700001


class BaseTrazabilidad(TestCase):
    """Entorno mínimo: una empresa con una bodega, un SKU y un proveedor."""

    @classmethod
    def setUpTestData(cls):
        cls.proveedor = crear_empresa(nombre='Proveedor Trazable', rut='77.333.333-3',
                                      esProveedor=True)
        cls.empresa = crear_empresa(nombre='Retail Trazable', rut='76.444.444-4')
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='BOD-TRAZA')
        cls.user = crear_usuario(username='traza-tester')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)
        cls.producto, cls.pt = crear_producto_con_talla(
            cls.sucursal, articulo='ZAPATILLA TRAZA', talla='40', sku=SKU, stock=7,
        )

    def setUp(self):
        self.hoy = timezone.localdate()

    # --- helpers -----------------------------------------------------------

    def _dte_compra(self, numero=5001, tipo='FACTURA ELECTRONICA'):
        return Dte.objects.create(
            emisor=self.proveedor, receptor=self.empresa,
            numero_documento=numero, tipo_documento=tipo,
            monto_neto=100000, monto_con_iva=119000,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='Tester', fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy + timedelta(days=30),
            diasCredito=30, bultos=1, unidades_productos=10,
            tipo_transaccion='COMPRA', sucursal=self.sucursal,
        )

    def _movimiento(self, cantidad, estado='COMPLETADO', concepto='INGRESO_COMPRA', **kw):
        return Movimientos_Producto.objects.create(
            ProductoTalla=self.pt, cantidad=cantidad, estado=estado,
            concepto=concepto, fecha=self.hoy, responsable='Tester', **kw
        )

    def _consultar(self, sku=SKU, user=None, sucursal_id=None):
        req = RequestFactory().get('/app/api/trazabilidad-producto/', {'sku': str(sku)})
        req.user = user or self.user
        req.session = {'idSucursalActual': sucursal_id or self.sucursal.id}
        resp = api_trazabilidad_producto(req)
        return resp.status_code, json.loads(resp.content)


class TestLotesExponenOrigen(BaseTrazabilidad):
    """FIX 1: cada lote debe decir de qué documento y proveedor entró."""

    def test_lote_con_dte_expone_folio_tipo_fecha_proveedor_y_link(self):
        dte = self._dte_compra(numero=5010)
        mov = self._movimiento(10)
        LoteProducto.objects.create(
            producto_talla=self.pt, dte=dte, movimiento=mov,
            cantidad_inicial=10, cantidad_disponible=7,
            costo_unitario=15000, sobreprecio_unitario=5000,
            precio_venta_unitario=20000, numero_lote='L-001',
        )

        status, data = self._consultar()
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
        self.assertEqual(len(data['lotes']), 1)

        lote = data['lotes'][0]
        self.assertEqual(lote['dte_id'], dte.id)
        self.assertEqual(lote['dte_folio'], 5010)
        self.assertEqual(lote['dte_tipo'], 'Factura Electrónica')
        self.assertEqual(lote['dte_fecha'], self.hoy.strftime('%d/%m/%Y'))
        self.assertEqual(lote['dte_url'], f'/app/detalle_dte/{dte.id}/')
        self.assertEqual(lote['proveedor'], 'Proveedor Trazable')
        self.assertEqual(lote['proveedor_rut'], '77.333.333-3')
        self.assertEqual(lote['movimiento_id'], mov.id)

    def test_lote_sin_dte_no_rompe_y_se_cuenta_en_meta(self):
        LoteProducto.objects.create(
            producto_talla=self.pt, cantidad_inicial=5, cantidad_disponible=5,
            costo_unitario=1000, sobreprecio_unitario=0, precio_venta_unitario=2000,
        )
        status, data = self._consultar()
        self.assertEqual(status, 200)
        lote = data['lotes'][0]
        self.assertIsNone(lote['dte_id'])
        self.assertIsNone(lote['dte_url'])
        self.assertEqual(lote['proveedor'], '')
        self.assertEqual(data['lotes_meta']['sin_origen'], 1)
        self.assertEqual(data['lotes_meta']['total'], 1)
        self.assertFalse(data['lotes_meta']['truncado'])

    def _lote(self, numero):
        LoteProducto.objects.create(
            producto_talla=self.pt, dte=self._dte_compra(numero=numero),
            cantidad_inicial=2, cantidad_disponible=2,
            costo_unitario=1000, sobreprecio_unitario=0, precio_venta_unitario=2000,
        )

    def _queries_de_la_ficha(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            self._consultar()
        return len(ctx)

    def test_serializar_lotes_no_genera_n_mas_1(self):
        """El DTE y su emisor vienen por select_related, no una query por lote."""
        self._lote(6000)
        con_un_lote = self._queries_de_la_ficha()
        for i in range(1, 6):
            self._lote(6000 + i)
        con_seis_lotes = self._queries_de_la_ficha()
        self.assertEqual(
            con_un_lote, con_seis_lotes,
            'Los lotes están generando queries por fila (N+1) al leer dte/emisor',
        )


class TestSaldoIgnoraNoCompletados(BaseTrazabilidad):
    """FIX 2: ANULADO / PENDIENTE no pueden mover el saldo del kardex."""

    def setUp(self):
        super().setUp()
        # Realidad: entraron 10 y salieron 3 → saldo 7, igual al stock del SKU.
        self._movimiento(10, concepto='INGRESO_COMPRA')
        self._movimiento(-3, concepto='VENTA')
        # Ruido que NO tocó stock y que antes se sumaba igual.
        self._movimiento(100, estado='ANULADO', concepto='INGRESO_COMPRA')
        self._movimiento(-50, estado='PENDIENTE', concepto='TRASPASO_SALIDA')

    def test_saldo_final_solo_suma_completados(self):
        status, data = self._consultar()
        self.assertEqual(status, 200)
        meta = data['movimientos_meta']
        self.assertEqual(meta['saldo_final'], 7)
        self.assertEqual(meta['stock_actual'], 7)
        self.assertEqual(meta['diferencia'], 0)
        self.assertTrue(meta['cuadra'], 'El kardex debía cuadrar: el ruido es ANULADO/PENDIENTE')

    def test_meta_desglosa_computados_y_no_computados(self):
        _, data = self._consultar()
        meta = data['movimientos_meta']
        self.assertEqual(meta['total'], 4)            # se siguen contando todos
        self.assertEqual(meta['total_computados'], 2)
        self.assertEqual(meta['no_computados'], 2)
        self.assertEqual(meta['estado_computado'], 'COMPLETADO')

    def test_no_completados_se_listan_pero_sin_saldo(self):
        """No se esconden: se ven, marcados y sin avanzar el acumulado."""
        _, data = self._consultar()
        por_estado = {m['estado']: m for m in data['movimientos']}
        self.assertEqual(len(data['movimientos']), 4)

        self.assertTrue(por_estado['COMPLETADO']['computa_saldo'])
        for estado in ('ANULADO', 'PENDIENTE'):
            self.assertIn(estado, por_estado, f'{estado} no debe ocultarse')
            self.assertFalse(por_estado[estado]['computa_saldo'])
            self.assertIsNone(por_estado[estado]['saldo'])

        # El saldo acumulado de los que sí computan nunca pasa por 100 ni -50.
        saldos = [m['saldo'] for m in data['movimientos'] if m['computa_saldo']]
        self.assertEqual(sorted(saldos), [7, 10])

    def test_saldo_apertura_no_arrastra_movimientos_anulados(self):
        _, data = self._consultar()
        meta = data['movimientos_meta']
        # Toda la historia cabe en la ventana → la apertura debe ser 0.
        self.assertEqual(meta['saldo_apertura'], 0)

    def test_timeline_marca_los_que_no_afectan_el_saldo(self):
        _, data = self._consultar()
        detalles = ' '.join(e['detalle'] for e in data['timeline'])
        self.assertIn('ANULADO (no afecta el saldo)', detalles)
        self.assertIn('PENDIENTE (no afecta el saldo)', detalles)


class TestRecepcionesDeCompra(BaseTrazabilidad):
    """FIX 3: la ficha debe mostrar la pata de compra (recepciones)."""

    def _compra_con_talla(self, nombre='Compra Invierno', correlativo=77, stock=10):
        compra = Compras.objects.create(
            empresa=self.proveedor, nombre=nombre, correlativo=correlativo,
            responsable='Comprador', temporada='INVIERNO 2026', fecha=self.hoy,
            estado='COMPLETADA',
        )
        cp = Compras_Producto.objects.create(
            compras=compra, nombre='ZAPATILLA TRAZA', descripcion='',
            atributo1='NIKE', atributo2='NEGRO', atributo3='', atributo4='',
            costo=15000, precioSugerido=20000,
        )
        return compra, Compras_Producto_Talla.objects.create(
            compra_producto=cp, stock=stock, talla='40', producto_talla=self.pt,
            unidades_recibidas=stock, estado_item='recibido_completo',
        )

    def test_recepcion_de_compra_aparece_con_proveedor_y_cantidades(self):
        dte = self._dte_compra(numero=5020)
        mov = self._movimiento(8)
        compra, cpt = self._compra_con_talla(stock=10)
        Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, dte=dte, producto_talla=self.pt,
            stockArribado=8, cantidad_esperada=10,
            cantidad_faltante=1, cantidad_danada=1, cantidad_sobrante=0,
            estado='RECEPCIONADO_PARCIAL', sucursal_destino=self.sucursal,
            recepcionado_por='Bodeguero Juan', fecha_recepcion=timezone.now(),
            movimiento_ingreso=mov,
        )

        status, data = self._consultar()
        self.assertEqual(status, 200)
        self.assertIn('recepciones', data)
        self.assertEqual(len(data['recepciones']), 1)

        r = data['recepciones'][0]
        self.assertEqual(r['origen'], 'COMPRA')
        self.assertEqual(r['proveedor'], 'Proveedor Trazable')
        self.assertEqual(r['documento'], 'Factura Electrónica #5020')
        self.assertEqual(r['dte_url'], f'/app/detalle_dte/{dte.id}/')
        self.assertEqual(r['orden_compra'], f'{compra.nombre} (#{compra.correlativo})')
        self.assertEqual(r['esperado'], 10)
        self.assertEqual(r['recibido'], 8)
        self.assertEqual(r['faltante'], 1)
        self.assertEqual(r['danado'], 1)
        self.assertEqual(r['sobrante'], 0)
        self.assertEqual(r['diferencia'], -2)
        self.assertEqual(r['estado'], 'RECEPCIONADO_PARCIAL')
        self.assertEqual(r['recepcionado_por'], 'Bodeguero Juan')
        self.assertEqual(r['destino'], 'BOD-TRAZA')
        self.assertEqual(r['movimiento_id'], mov.id)
        self.assertFalse(r['sin_movimiento'])
        self.assertTrue(r['tiene_problemas'])

    def test_meta_totaliza_toda_la_historia_y_cuenta_sin_movimiento(self):
        dte = self._dte_compra(numero=5030)
        _, cpt = self._compra_con_talla(correlativo=78)
        Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, dte=dte, producto_talla=self.pt,
            stockArribado=6, cantidad_esperada=10, cantidad_faltante=4,
            estado='RECEPCIONADO_PARCIAL', recepcionado_por='Ana',
        )
        Productos_Recepcionados.objects.create(
            producto_talla=self.pt, stockArribado=5, cantidad_esperada=4,
            cantidad_sobrante=1, estado='RECEPCIONADO_SOBRANTE', recepcionado_por='Ana',
        )

        _, data = self._consultar()
        meta = data['recepciones_meta']
        self.assertEqual(meta['total'], 2)
        self.assertEqual(meta['total_esperado'], 14)
        self.assertEqual(meta['total_recibido'], 11)
        self.assertEqual(meta['total_faltante'], 4)
        self.assertEqual(meta['total_sobrante'], 1)
        # Ninguna quedó enganchada a un movimiento de stock: es el eslabón roto.
        self.assertEqual(meta['sin_movimiento'], 2)
        self.assertFalse(meta['truncado'])

    def test_recepcion_vinculada_solo_por_compra_encuentra_el_sku(self):
        """
        Recepciones legacy con `producto_talla` NULL: el vínculo vive en
        `compra_producto_talla.producto_talla` y debe encontrarse igual.
        """
        _, cpt = self._compra_con_talla(correlativo=79, stock=4)
        Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, producto_talla=None,
            stockArribado=4, cantidad_esperada=4,
            estado='RECEPCIONADO_OK', recepcionado_por='Legacy',
        )
        _, data = self._consultar()
        self.assertEqual(len(data['recepciones']), 1)
        self.assertEqual(data['recepciones'][0]['recepcionado_por'], 'Legacy')
        # Sin DTE, el proveedor se resuelve por la empresa de la orden de compra.
        self.assertEqual(data['recepciones'][0]['proveedor'], 'Proveedor Trazable')

    def test_recepcion_sin_movimiento_entra_a_la_timeline(self):
        _, cpt = self._compra_con_talla(correlativo=80)
        Productos_Recepcionados.objects.create(
            compra_producto_talla=cpt, producto_talla=self.pt,
            stockArribado=10, cantidad_esperada=10,
            estado='RECEPCIONADO_OK', recepcionado_por='Bodeguero',
            fecha_recepcion=timezone.now(),
        )
        _, data = self._consultar()
        tipos = [e['tipo'] for e in data['timeline']]
        self.assertIn('recepcion', tipos)

    def test_sku_sin_recepciones_devuelve_lista_vacia(self):
        _, data = self._consultar()
        self.assertEqual(data['recepciones'], [])
        self.assertEqual(data['recepciones_meta']['total'], 0)
        self.assertEqual(data['recepciones_meta']['sin_movimiento'], 0)


class TestScopingSeMantiene(BaseTrazabilidad):
    """Las claves nuevas no deben debilitar el control de acceso por empresa."""

    def test_usuario_de_otra_empresa_recibe_403(self):
        otra = crear_empresa(nombre='Ajena SpA', rut='76.999.999-9')
        suc_otra = crear_sucursal(empresa=otra, alias='BOD-AJENA')
        intruso = crear_usuario(username='intruso-traza')
        crear_empresa_user(intruso, otra, suc_otra)

        dte = self._dte_compra(numero=5040)
        LoteProducto.objects.create(
            producto_talla=self.pt, dte=dte, cantidad_inicial=1, cantidad_disponible=1,
            costo_unitario=1, sobreprecio_unitario=0, precio_venta_unitario=1,
        )

        status, data = self._consultar(user=intruso, sucursal_id=suc_otra.id)
        self.assertEqual(status, 403)
        self.assertFalse(data['success'])
        self.assertNotIn('lotes', data)
