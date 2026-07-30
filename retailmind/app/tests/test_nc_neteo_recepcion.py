"""
Tests del neteo de notas de crédito en la pantalla de recepción de traspasos.

El problema que cubren: hasta ahora la lista de `/app/recepcion-dte/` mostraba
el total del documento con un badge "-N uds" al lado, sin decir si esas N
unidades ya estaban restadas del total o no. Y no lo estaban siempre:

  * NC pre-recepción (`ajustar_traspaso`) → reduce `Dte_Productos.stock` del
    original. El total YA viene neteado.
  * NC por monto desde Gestión DTE (`anular-factura` sin `productos_afectados`)
    → no toca las líneas. Las unidades acreditadas SIGUEN dentro del total, y
    al recepcionar entraban a stock igual.

`Dte.redujo_lineas_documento` distingue los dos casos y el endpoint expone el
neto por línea y por documento.

Casos cubiertos:

1. NC pre-recepción marca `redujo_lineas_documento=True` y NO se vuelve a
   descontar (si no, se contaría dos veces la misma devolución).
2. NC por monto marca `redujo_lineas_documento=False` y el endpoint reporta
   el neto correcto por línea y por documento.
3. `confirmar_recepcion_api` reporta cuántas unidades ya acreditadas
   entraron a stock cuando el operador recepciona igual.
4. El comando de backfill deduce bien la fase de los documentos históricos.
"""
import json
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, Client

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, crear_correlativo,
)


def _patch_permisos():
    """Los decoradores de permiso pegan a la BD de permisos; en tests van a True."""
    return (
        mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True),
        mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True),
    )


def _crear_traspaso(sucursal_origen, sucursal_destino, tallas_cant,
                    numero=2000, tipo_documento='FACTURA ELECTRONICA'):
    """Crea un traspaso EMITIDO con una línea por (talla, cantidad)."""
    total = sum(c for _, c in tallas_cant)
    dte = Dte.objects.create(
        emisor=sucursal_origen.empresa,
        receptor=sucursal_destino.empresa,
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_neto=Decimal(total * 1000),
        monto_con_iva=Decimal(total * 1190),
        estado_pago='PENDIENTE',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision='2026-07-01',
        fecha_vencimiento='2026-07-01',
        diasCredito=0,
        bultos=1,
        unidades_productos=total,
        tipo_transaccion='TRASPASO',
        sucursal=sucursal_origen,
    )
    lineas = []
    for talla, cantidad in tallas_cant:
        lineas.append(Dte_Productos.objects.create(
            dte=dte, productoTalla=talla, descripcion='Producto Test',
            costo=100, sobreprecio=0, precio=1000, stock=cantidad, activo=True,
        ))
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=talla,
            sucursal_origen=sucursal_origen, sucursal_destino=sucursal_destino,
            cantidad=-cantidad, concepto='TRASPASO_SALIDA',
            tipo_movimiento='EGRESO', estado='COMPLETADO', responsable='tester',
        )
        Producto_Talla.objects.filter(id=talla.id).update(
            stock=Producto_Talla.objects.get(id=talla.id).stock - cantidad
        )
    return dte, lineas


class NeteoNCEnRecepcionTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.origen, articulo='Zap Test', sku=2001, stock=20,
        )
        _, self.talla_destino = crear_producto_con_talla(
            self.destino, articulo='Zap Test D', sku=2001, stock=0,
        )
        crear_correlativo(self.origen, tipo_dte='NOTA DE CREDITO')
        crear_correlativo(self.origen, tipo_dte='AJUSTE TRASPASO')

        self.client = Client()
        self.client.force_login(self.user)

    def _sesion(self, sucursal):
        session = self.client.session
        session['idSucursalActual'] = sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = sucursal.alias
        session.save()

    def _item_del_endpoint(self, dte):
        """Llama a recepciones_pendientes_api y devuelve el item del DTE."""
        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.get('/app/dte/recepciones_pendientes/')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        items = [i for i in data['items'] if i['id'] == dte.id]
        self.assertEqual(len(items), 1, f'DTE {dte.id} no vino en el listado')
        return items[0]

    # ------------------------------------------------------------------
    # 1. NC pre-recepción: ya descontada, no se debe restar dos veces
    # ------------------------------------------------------------------
    def test_nc_pre_recepcion_no_se_descuenta_dos_veces(self):
        self._sesion(self.origen)
        dte, lineas = _crear_traspaso(
            self.origen, self.destino, [(self.talla_origen, 10)], numero=2100,
        )

        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.post(
                '/app/dte/ajustar_traspaso/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'ajustes': [{'dte_producto_id': lineas[0].id, 'nueva_cantidad': 6}],
                    'motivo': 'error de bodega',
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200, resp.content)

        nc = Dte.objects.get(documento_afectado=dte)
        self.assertTrue(
            nc.redujo_lineas_documento,
            'la NC pre-recepción sí reduce las líneas: debe quedar marcada',
        )

        # La línea quedó en 6 y el documento vale 6, no 2 (10 - 4 - 4).
        lineas[0].refresh_from_db()
        self.assertEqual(lineas[0].stock, 6)

        self._sesion(self.destino)
        item = self._item_del_endpoint(dte)
        self.assertEqual(item['total_unidades'], 6)
        self.assertEqual(item['total_unidades_nc_pendiente'], 0)
        self.assertEqual(item['total_unidades_neto'], 6)
        self.assertEqual(item['detalle'][0]['cantidad_neta'], 6)
        self.assertTrue(item['ajustes_previos'][0]['ya_descontada'])

    # ------------------------------------------------------------------
    # 2. NC que NO tocó las líneas: el endpoint entrega el neto
    # ------------------------------------------------------------------
    def test_nc_sin_reducir_lineas_reporta_neto(self):
        dte, lineas = _crear_traspaso(
            self.origen, self.destino, [(self.talla_origen, 10)], numero=2200,
        )
        # NC emitida "por monto": no toca las líneas del original.
        nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=9001, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal('4000'), monto_con_iva=Decimal('4760'),
            estado_pago='PAGADO', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-07-02', fecha_vencimiento='2026-07-02',
            diasCredito=0, bultos=0, unidades_productos=4,
            tipo_transaccion='ANULACION', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=dte,
            motivo_nc='error despacho', redujo_lineas_documento=False,
        )
        Dte_Productos.objects.create(
            dte=nc, productoTalla=self.talla_origen, descripcion='NC',
            costo=100, sobreprecio=0, precio=1000, stock=4, activo=True,
        )

        self._sesion(self.destino)
        item = self._item_del_endpoint(dte)

        # El documento sigue diciendo 10, pero al stock deben entrar 6.
        self.assertEqual(item['total_unidades'], 10)
        self.assertEqual(item['total_unidades_nc_pendiente'], 4)
        self.assertEqual(item['total_unidades_neto'], 6)

        linea = item['detalle'][0]
        self.assertEqual(linea['cantidad'], 10)
        self.assertEqual(linea['cantidad_nc_pendiente'], 4)
        self.assertEqual(linea['cantidad_neta'], 6)
        self.assertFalse(item['ajustes_previos'][0]['ya_descontada'])

    def test_nc_pendiente_se_reparte_entre_lineas_de_la_misma_talla(self):
        """Dos líneas con la misma productoTalla no pueden imputar la misma
        unidad acreditada cada una: el saldo se consume, no se duplica."""
        dte, lineas = _crear_traspaso(
            self.origen, self.destino,
            [(self.talla_origen, 3), (self.talla_origen, 5)], numero=2300,
        )
        nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=9002, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal('4000'), monto_con_iva=Decimal('4760'),
            estado_pago='PAGADO', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-07-02', fecha_vencimiento='2026-07-02',
            diasCredito=0, bultos=0, unidades_productos=4,
            tipo_transaccion='ANULACION', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=dte,
            redujo_lineas_documento=False,
        )
        Dte_Productos.objects.create(
            dte=nc, productoTalla=self.talla_origen, descripcion='NC',
            costo=100, sobreprecio=0, precio=1000, stock=4, activo=True,
        )

        self._sesion(self.destino)
        item = self._item_del_endpoint(dte)

        # 4 acreditadas sobre 8 documentadas → neto 4, no 0 (que sería el
        # resultado de restar 4 a cada una de las dos líneas).
        self.assertEqual(item['total_unidades'], 8)
        self.assertEqual(item['total_unidades_nc_pendiente'], 4)
        self.assertEqual(item['total_unidades_neto'], 4)
        repartidas = sum(l['cantidad_nc_pendiente'] for l in item['detalle'])
        self.assertEqual(repartidas, 4)

    def test_nc_anulada_no_descuenta(self):
        """Una NC anulada no acredita nada: no debe netear el documento."""
        dte, lineas = _crear_traspaso(
            self.origen, self.destino, [(self.talla_origen, 10)], numero=2400,
        )
        nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=9003, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal('4000'), monto_con_iva=Decimal('4760'),
            estado_pago='PAGADO', estado_dte='ANULADO', responsable='tester',
            fecha_emision='2026-07-02', fecha_vencimiento='2026-07-02',
            diasCredito=0, bultos=0, unidades_productos=4,
            tipo_transaccion='ANULACION', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=dte,
            redujo_lineas_documento=False,
        )
        Dte_Productos.objects.create(
            dte=nc, productoTalla=self.talla_origen, descripcion='NC',
            costo=100, sobreprecio=0, precio=1000, stock=4, activo=True,
        )

        self._sesion(self.destino)
        item = self._item_del_endpoint(dte)
        self.assertEqual(item['total_unidades_nc_pendiente'], 0)
        self.assertEqual(item['total_unidades_neto'], 10)

    # ------------------------------------------------------------------
    # 3. Confirmar recepción avisa si entraron unidades ya acreditadas
    # ------------------------------------------------------------------
    def test_confirmar_recepcion_reporta_unidades_con_nc_ingresadas(self):
        dte, lineas = _crear_traspaso(
            self.origen, self.destino, [(self.talla_origen, 10)], numero=2500,
        )
        nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=9004, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal('4000'), monto_con_iva=Decimal('4760'),
            estado_pago='PAGADO', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-07-02', fecha_vencimiento='2026-07-02',
            diasCredito=0, bultos=0, unidades_productos=4,
            tipo_transaccion='ANULACION', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=dte,
            redujo_lineas_documento=False,
        )
        Dte_Productos.objects.create(
            dte=nc, productoTalla=self.talla_origen, descripcion='NC',
            costo=100, sobreprecio=0, precio=1000, stock=4, activo=True,
        )

        self._sesion(self.destino)
        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.post(
                '/app/dte/confirmar_recepcion/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'productos': [{
                        'dte_producto_id': lineas[0].id,
                        'cantidad_esperada': 10,
                        'cantidad_recepcionada': 10,
                        'cantidad_danada': 0,
                        'estado': 'RECEPCIONADO_OK',
                        'observaciones': '',
                    }],
                }),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        # No bloquea (la mercadería pudo venir igual), pero deja el rastro.
        self.assertEqual(data['unidades_con_nc_ingresadas'], 4)

    def test_recepcion_sin_nc_no_reporta_unidades_acreditadas(self):
        dte, lineas = _crear_traspaso(
            self.origen, self.destino, [(self.talla_origen, 7)], numero=2600,
        )
        self._sesion(self.destino)
        p1, p2 = _patch_permisos()
        with p1, p2:
            resp = self.client.post(
                '/app/dte/confirmar_recepcion/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'productos': [{
                        'dte_producto_id': lineas[0].id,
                        'cantidad_esperada': 7,
                        'cantidad_recepcionada': 7,
                        'cantidad_danada': 0,
                        'estado': 'RECEPCIONADO_OK',
                        'observaciones': '',
                    }],
                }),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['unidades_con_nc_ingresadas'], 0)


class BackfillNCRedujoLineasTest(TestCase):
    """El comando debe deducir la fase de los documentos ya emitidos."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        _, self.talla = crear_producto_con_talla(
            self.origen, articulo='Zap', sku=3001, stock=50,
        )
        self.dte, _ = _crear_traspaso(
            self.origen, self.destino, [(self.talla, 10)], numero=3100,
        )

    def _nc(self, numero, referencias='', conceptos=()):
        nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=numero, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal('1000'), monto_con_iva=Decimal('1190'),
            estado_pago='PAGADO', estado_dte='EMITIDO', responsable='tester',
            fecha_emision='2026-07-02', fecha_vencimiento='2026-07-02',
            diasCredito=0, bultos=0, unidades_productos=1,
            tipo_transaccion='ANULACION', sucursal=self.origen,
            es_nota_credito=True, documento_afectado=self.dte,
            referencias=referencias, redujo_lineas_documento=False,
        )
        for concepto in conceptos:
            Movimientos_Producto.objects.create(
                dte=nc, ProductoTalla=self.talla,
                sucursal_origen=None, sucursal_destino=self.origen,
                cantidad=1, concepto=concepto, tipo_movimiento='INGRESO',
                estado='COMPLETADO', responsable='tester',
            )
        return nc

    def test_backfill_clasifica_las_cuatro_reglas(self):
        pre_texto = self._nc(
            8001, referencias='Ajuste emisor (pre-recepción) sobre DTE #3100',
        )
        post_texto = self._nc(
            8002, referencias='Ajuste emisor (post-recepción) sobre DTE #3100',
        )
        por_linea = self._nc(8003, conceptos=['DEVOLUCION_NC'])
        post_mov = self._nc(8004, conceptos=['DEVOLUCION_NC_POST_RECEPCION'])
        por_monto = self._nc(8005)

        out = StringIO()
        call_command('backfill_nc_redujo_lineas', '--aplicar', stdout=out)

        for nc in (pre_texto, post_texto, por_linea, post_mov, por_monto):
            nc.refresh_from_db()

        self.assertTrue(pre_texto.redujo_lineas_documento)
        self.assertTrue(por_linea.redujo_lineas_documento)
        self.assertFalse(post_texto.redujo_lineas_documento)
        self.assertFalse(post_mov.redujo_lineas_documento)
        self.assertFalse(por_monto.redujo_lineas_documento)

    def test_dry_run_no_escribe(self):
        pre_texto = self._nc(
            8010, referencias='Ajuste emisor (pre-recepción) sobre DTE #3100',
        )
        out = StringIO()
        call_command('backfill_nc_redujo_lineas', stdout=out)
        pre_texto.refresh_from_db()
        self.assertFalse(pre_texto.redujo_lineas_documento)
        self.assertIn('DRY-RUN', out.getvalue())
