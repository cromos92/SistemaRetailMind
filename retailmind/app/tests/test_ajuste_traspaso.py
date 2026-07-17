"""
Tests de integración para el endpoint unificado `ajustar_dte_emisor_api`
(pre-recepción y post-recepción) y para `api_dte_trazabilidad`.

Cubren los casos del plan `trazabilidad-compras-despachos-nc`:

1. Ajuste PRE-RECEPCIÓN: stock vuelve al origen, TRASPASO_SALIDA se reduce
   o elimina, se crea doc hijo (NC o AJUSTE TRASPASO).
2. Ajuste POST-RECEPCIÓN: stock baja en destino, sube en origen, se crean
   movimientos DEVOLUCION_NC_POST_RECEPCION, y el DTE original no se
   modifica.
3. Ajuste POST-RECEPCIÓN con stock insuficiente en destino: bloqueado.
4. Trazabilidad: padre (compras), hijos (NCs), movimientos por sucursal.
"""
import json
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from app.models import (
    Dte, Dte_Productos, Producto, Producto_Talla, Movimientos_Producto,
    Correlativo,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, crear_correlativo,
)


def _patch_permiso_aprobar():
    """
    `requiere_permiso('recepcion_dte', 'puede_aprobar')` depende de la
    base de datos de permisos. Para los tests lo parchamos a True.
    """
    return mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True)


def _patch_permiso_helper():
    """Parcha también la función que usan las vistas que llaman al helper."""
    return mock.patch(
        'app.decorators.PermisoRol.tiene_permiso',
        return_value=True,
    )


def _crear_traspaso(sucursal_origen, sucursal_destino, talla_origen,
                    cantidad=5, tipo_documento='GUIA'):
    """
    Crea un DTE de TRASPASO con sus Dte_Productos y Movimientos_Producto
    simulando lo que hace `emitir_dte`.
    """
    dte = Dte.objects.create(
        emisor=sucursal_origen.empresa,
        receptor=sucursal_destino.empresa,
        numero_documento=1000,
        tipo_documento=tipo_documento,
        monto_neto=Decimal(cantidad * 1000),
        monto_con_iva=Decimal(cantidad * 1190),
        estado_pago='PENDIENTE',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision='2025-01-01',
        fecha_vencimiento='2025-01-01',
        diasCredito=0,
        bultos=1,
        unidades_productos=cantidad,
        tipo_transaccion='TRASPASO',
        sucursal=sucursal_origen,
    )
    dp = Dte_Productos.objects.create(
        dte=dte,
        productoTalla=talla_origen,
        descripcion='Producto Test',
        costo=100,
        sobreprecio=0,
        precio=1000,
        stock=cantidad,
        activo=True,
    )
    Movimientos_Producto.objects.create(
        dte=dte,
        ProductoTalla=talla_origen,
        sucursal_origen=sucursal_origen,
        sucursal_destino=sucursal_destino,
        cantidad=-cantidad,
        concepto='TRASPASO_SALIDA',
        tipo_movimiento='EGRESO',
        estado='COMPLETADO',
        responsable='tester',
    )
    # Reducir stock en origen (lo que hace emitir_dte con F)
    Producto_Talla.objects.filter(id=talla_origen.id).update(
        stock=talla_origen.stock - cantidad
    )
    talla_origen.refresh_from_db()
    return dte, dp


class AjusteTraspasoPreRecepcionTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        # Producto en origen con 10 unidades.
        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Test', sku=1001, stock=10,
        )
        # Misma SKU en destino con 0 unidades.
        _, self.talla_destino = crear_producto_con_talla(
            self.sucursal_destino, articulo='Zap Test D', sku=1001, stock=0,
        )
        crear_correlativo(self.sucursal_origen, tipo_dte='AJUSTE TRASPASO')

        self.client = Client()
        self.client.force_login(self.user)
        # Session con sucursal actual
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def test_ajuste_pre_recepcion_devuelve_stock_origen(self):
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        self.talla_origen.refresh_from_db()
        stock_origen_antes = self.talla_origen.stock  # 10 - 5 = 5

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                '/app/dte/ajustar_traspaso/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'ajustes': [{'dte_producto_id': dp.id, 'nueva_cantidad': 2}],
                    'motivo': 'Quitamos 3 uds antes de recepción',
                }),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertFalse(data['es_post_recepcion'])

        # Stock en origen subió 3 (5 → 8).
        self.talla_origen.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, stock_origen_antes + 3)

        # Destino no cambia (sigue en 0).
        self.talla_destino.refresh_from_db()
        self.assertEqual(self.talla_destino.stock, 0)

        # Dte_Productos original quedó en 2 (se redujo).
        dp.refresh_from_db()
        self.assertEqual(dp.stock, 2)
        self.assertTrue(dp.activo)

        # Se creó documento hijo.
        hijos = Dte.objects.filter(documento_afectado=dte)
        self.assertEqual(hijos.count(), 1)
        hijo = hijos.first()
        # Para GUIA no factura → AJUSTE TRASPASO (pre).
        self.assertEqual(hijo.tipo_documento, 'AJUSTE TRASPASO')

    def _ajustar_factura_capturando_txt(self, dte, dp, nueva_cantidad):
        """POST al endpoint interceptando los datos con que se arma el TXT."""
        capturado = {}

        def _fake_generar_txt(datos):
            capturado['datos'] = datos
            return 'TXT-TEST'

        with _patch_permiso_aprobar(), _patch_permiso_helper(), \
                mock.patch(
                    'app.views_modulo_documentos.generar_txt_dte_acepta',
                    side_effect=_fake_generar_txt,
                ), \
                self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            resp = self.client.post(
                '/app/dte/ajustar_traspaso/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'ajustes': [{'dte_producto_id': dp.id,
                                 'nueva_cantidad': nueva_cantidad}],
                    'motivo': 'Test razón referencia SII',
                }),
                content_type='application/json',
            )
        return resp, capturado

    def test_nc_parcial_factura_usa_razon_3_y_acredita_lo_devuelto(self):
        crear_correlativo(self.sucursal_origen, tipo_dte='NOTA DE CREDITO')
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5, tipo_documento='FACTURA ELECTRONICA',
        )

        # Devuelve 1 unidad: la línea queda en 4.
        resp, capturado = self._ajustar_factura_capturando_txt(dte, dp, nueva_cantidad=4)

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['doc_trazador']['es_nota_credito'])
        self.assertIsNotNone(data['doc_trazador']['archivo_txt_url'])

        datos = capturado['datos']
        # NC parcial → razón 3 (corrige montos), NUNCA 1 (anula documento).
        self.assertEqual(datos['referencias'][0]['razon'], '3')
        self.assertEqual(datos['referencias'][0]['folio'], str(dte.numero_documento))
        # El TXT acredita exactamente lo devuelto (1 ud), no lo que queda.
        self.assertEqual(len(datos['detalle']), 1)
        self.assertEqual(datos['detalle'][0]['cantidad'], 1)
        self.assertEqual(datos['detalle'][0]['monto_item'], 1000)
        self.assertEqual(datos['totales']['monto_neto'], 1000)

    def test_nc_total_factura_usa_razon_1(self):
        crear_correlativo(self.sucursal_origen, tipo_dte='NOTA DE CREDITO')
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5, tipo_documento='FACTURA ELECTRONICA',
        )

        # Devuelve TODO: el DTE queda sin unidades → razón 1 (anula).
        resp, capturado = self._ajustar_factura_capturando_txt(dte, dp, nueva_cantidad=0)

        self.assertEqual(resp.status_code, 200, resp.content)
        datos = capturado['datos']
        self.assertEqual(datos['referencias'][0]['razon'], '1')
        self.assertEqual(datos['detalle'][0]['cantidad'], 5)
        self.assertEqual(datos['totales']['monto_neto'], 5000)
        dp.refresh_from_db()
        self.assertFalse(dp.activo)

    def test_segunda_nc_que_completa_devolucion_usa_razon_3(self):
        """Razón 1 exige que UNA sola NC cubra el documento completo: si ya
        hay una NC previa viva, la NC que devuelve el resto va con razón 3."""
        crear_correlativo(self.sucursal_origen, tipo_dte='NOTA DE CREDITO')
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5, tipo_documento='FACTURA ELECTRONICA',
        )

        # NC #1: devuelve 2 de 5 → razón 3.
        resp1, cap1 = self._ajustar_factura_capturando_txt(dte, dp, nueva_cantidad=3)
        self.assertEqual(resp1.status_code, 200, resp1.content)
        self.assertEqual(cap1['datos']['referencias'][0]['razon'], '3')

        # NC #2: devuelve las 3 restantes. El DTE queda en 0, pero como ya
        # existe la NC previa, esta solo COMPLETA la devolución → razón 3.
        resp2, cap2 = self._ajustar_factura_capturando_txt(dte, dp, nueva_cantidad=0)
        self.assertEqual(resp2.status_code, 200, resp2.content)
        self.assertEqual(cap2['datos']['referencias'][0]['razon'], '3')
        self.assertEqual(cap2['datos']['detalle'][0]['cantidad'], 3)


class AjusteTraspasoPostRecepcionTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Test', sku=2001, stock=10,
        )
        _, self.talla_destino = crear_producto_con_talla(
            self.sucursal_destino, articulo='Zap Test D', sku=2001, stock=0,
        )
        crear_correlativo(self.sucursal_origen, tipo_dte='AJUSTE TRASPASO POST')

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _simular_recepcion(self, dte, cantidad):
        """Emula la recepción del destino: suma stock al Producto_Talla destino."""
        Producto_Talla.objects.filter(id=self.talla_destino.id).update(
            stock=self.talla_destino.stock + cantidad
        )
        Movimientos_Producto.objects.create(
            dte=dte,
            ProductoTalla=self.talla_destino,
            sucursal_origen=self.sucursal_origen,
            sucursal_destino=self.sucursal_destino,
            cantidad=cantidad,
            concepto='TRASPASO_ENTRADA',
            tipo_movimiento='INGRESO',
            estado='COMPLETADO',
            responsable='destino',
        )
        dte.estado_dte = 'RECEPCIONADO_COMPLETO'
        from django.utils import timezone as tz
        dte.fecha_recepcion = tz.localdate()
        dte.save(update_fields=['estado_dte', 'fecha_recepcion'])

    def test_post_recepcion_descuenta_destino_y_reingresa_origen(self):
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        self._simular_recepcion(dte, 5)

        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        stock_origen_antes = self.talla_origen.stock   # 5
        stock_destino_antes = self.talla_destino.stock  # 5

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                '/app/dte/ajustar_traspaso/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'ajustes': [{'dte_producto_id': dp.id, 'nueva_cantidad': 2}],
                    'motivo': 'Devolución post-recepción',
                }),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['es_post_recepcion'])

        # Origen suma 3, destino resta 3.
        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, stock_origen_antes + 3)
        self.assertEqual(self.talla_destino.stock, stock_destino_antes - 3)

        # dp no se modifica (DTE original preservado).
        dp.refresh_from_db()
        self.assertEqual(dp.stock, 5)

        # Movimientos DEVOLUCION_NC_POST_RECEPCION creados (1 EGRESO destino, 1 INGRESO origen).
        movs = Movimientos_Producto.objects.filter(
            dte=dte, concepto='DEVOLUCION_NC_POST_RECEPCION',
        )
        self.assertEqual(movs.count(), 2)
        egreso = movs.filter(tipo_movimiento='EGRESO').first()
        ingreso = movs.filter(tipo_movimiento='INGRESO').first()
        self.assertIsNotNone(egreso)
        self.assertIsNotNone(ingreso)
        self.assertEqual(abs(egreso.cantidad), 3)
        self.assertEqual(ingreso.cantidad, 3)

        # Doc hijo creado.
        hijo = Dte.objects.filter(documento_afectado=dte).first()
        self.assertIsNotNone(hijo)
        self.assertEqual(hijo.tipo_documento, 'AJUSTE TRASPASO POST')

    def test_post_recepcion_bloqueada_si_stock_destino_insuficiente(self):
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        self._simular_recepcion(dte, 5)
        # Destino vendió 4 → queda con 1.
        Producto_Talla.objects.filter(id=self.talla_destino.id).update(stock=1)

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                '/app/dte/ajustar_traspaso/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'ajustes': [{'dte_producto_id': dp.id, 'nueva_cantidad': 0}],
                    'motivo': 'Intento devolver 5 pero destino sólo tiene 1',
                }),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertTrue(data.get('stock_insuficiente'))
        self.assertEqual(data.get('disponible_destino'), 1)

        # Nada cambió: ni stocks ni doc hijo ni movimientos nuevos.
        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, 5)
        self.assertEqual(self.talla_destino.stock, 1)
        self.assertFalse(
            Dte.objects.filter(documento_afectado=dte).exists()
        )
        self.assertFalse(
            Movimientos_Producto.objects.filter(
                dte=dte, concepto='DEVOLUCION_NC_POST_RECEPCION'
            ).exists()
        )

    def _ajustar_factura_post_capturando_txt(self, dte, dp, nueva_cantidad):
        """POST al endpoint interceptando los datos con que se arma el TXT."""
        capturado = {}

        def _fake_generar_txt(datos):
            capturado['datos'] = datos
            return 'TXT-TEST'

        with _patch_permiso_aprobar(), _patch_permiso_helper(), \
                mock.patch(
                    'app.views_modulo_documentos.generar_txt_dte_acepta',
                    side_effect=_fake_generar_txt,
                ), \
                self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            resp = self.client.post(
                '/app/dte/ajustar_traspaso/',
                data=json.dumps({
                    'dte_id': dte.id,
                    'ajustes': [{'dte_producto_id': dp.id,
                                 'nueva_cantidad': nueva_cantidad}],
                    'motivo': 'Test razón referencia SII post-recepción',
                }),
                content_type='application/json',
            )
        return resp, capturado

    def test_nc_post_recepcion_parcial_usa_razon_3(self):
        crear_correlativo(self.sucursal_origen, tipo_dte='NOTA DE CREDITO')
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5, tipo_documento='FACTURA ELECTRONICA',
        )
        self._simular_recepcion(dte, 5)

        # Devuelve 3 de 5 → NC parcial, razón 3.
        resp, capturado = self._ajustar_factura_post_capturando_txt(dte, dp, nueva_cantidad=2)

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['es_post_recepcion'])
        datos = capturado['datos']
        self.assertEqual(datos['referencias'][0]['razon'], '3')
        self.assertEqual(datos['detalle'][0]['cantidad'], 3)
        self.assertEqual(datos['totales']['monto_neto'], 3000)

    def test_nc_post_recepcion_total_usa_razon_1(self):
        crear_correlativo(self.sucursal_origen, tipo_dte='NOTA DE CREDITO')
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5, tipo_documento='FACTURA ELECTRONICA',
        )
        self._simular_recepcion(dte, 5)

        # Devuelve las 5 → acredita el 100% de lo despachado, razón 1.
        resp, capturado = self._ajustar_factura_post_capturando_txt(dte, dp, nueva_cantidad=0)

        self.assertEqual(resp.status_code, 200, resp.content)
        datos = capturado['datos']
        self.assertEqual(datos['referencias'][0]['razon'], '1')
        self.assertEqual(datos['detalle'][0]['cantidad'], 5)
        # El DTE original NO se modifica en post-recepción.
        dp.refresh_from_db()
        self.assertEqual(dp.stock, 5)
        self.assertTrue(dp.activo)


class TrazabilidadApiTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, sku=3001, stock=10,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def test_trazabilidad_devuelve_padre_hijos_movimientos(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=3,
        )
        # Crear un hijo manual (simulando una NC)
        hijo = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=9001, tipo_documento='NOTA DE CREDITO',
            monto_neto=1000, monto_con_iva=1190,
            estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='x', fecha_emision='2025-01-02',
            fecha_vencimiento='2025-01-02', diasCredito=0,
            bultos=0, unidades_productos=1,
            tipo_transaccion='ANULACION', sucursal=self.sucursal_origen,
            es_nota_credito=True, documento_afectado=dte,
            motivo_nc='Test NC',
        )

        resp = self.client.get(f'/app/api/dte/{dte.id}/trazabilidad/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['dte']['numero_documento'], dte.numero_documento)

        # Hijos encontrados: al menos el NC recién creado.
        hijos_ids = [h['id'] for h in data['hijos']]
        self.assertIn(hijo.id, hijos_ids)

        # Movimientos asociados al menos 1 (TRASPASO_SALIDA del origen).
        grupos = data['movimientos']['por_sucursal']
        total_movs = sum(len(g['items']) for g in grupos)
        self.assertGreaterEqual(total_movs, 1)


# =========================================================================
# Tests para la reparación retroactiva de NCs históricas sin movimientos.
# =========================================================================
class ReparacionNcHistoricaTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='CD')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='MALL-1')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Test', sku=4001, stock=10,
        )
        _, self.talla_destino = crear_producto_con_talla(
            self.sucursal_destino, articulo='Zap Test D', sku=4001, stock=0,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _crear_nc_historica_sin_movimientos(self, dte_padre, cantidad_nc,
                                            numero_nc=9100):
        """
        Simula una NC histórica emitida ANTES del fix: existe el Dte hijo
        con es_nota_credito=True y productoTalla, pero NO existen
        Movimientos_Producto de reversa. Ese es el bug histórico.
        """
        nc = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=numero_nc, tipo_documento='NOTA DE CREDITO',
            monto_neto=Decimal(cantidad_nc * 1000),
            monto_con_iva=Decimal(int(cantidad_nc * 1190)),
            estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='legacy', fecha_emision='2025-01-10',
            fecha_vencimiento='2025-01-10', diasCredito=0,
            bultos=0, unidades_productos=cantidad_nc,
            tipo_transaccion='ANULACION', sucursal=self.sucursal_origen,
            es_nota_credito=True, documento_afectado=dte_padre,
            motivo_nc='NC legacy sin movimientos',
        )
        Dte_Productos.objects.create(
            dte=nc,
            productoTalla=self.talla_origen,
            descripcion='Producto Test NC',
            costo=100, sobreprecio=0, precio=1000,
            stock=cantidad_nc, activo=True,
        )
        return nc

    def test_reparacion_pre_recepcion_sube_stock_origen(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        # Padre sigue EMITIDO sin fecha_recepcion -> pre-recepción.
        nc = self._crear_nc_historica_sin_movimientos(dte, cantidad_nc=3)

        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        stock_origen_antes = self.talla_origen.stock   # 10 - 5 = 5
        stock_destino_antes = self.talla_destino.stock  # 0

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                f'/app/dte/{nc.id}/reparar_stock/',
                data=json.dumps({
                    'lineas': [{'sku': self.talla_origen.sku, 'cantidad': 3}],
                    'motivo': 'reparacion test pre',
                }),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertFalse(data['es_post_recepcion'])
        self.assertEqual(data['total_repuesto'], 3)

        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, stock_origen_antes + 3)
        self.assertEqual(self.talla_destino.stock, stock_destino_antes)  # sin cambio

        # Movimiento creado: INGRESO con concepto REPARACION_STOCK_HISTORICO.
        movs = Movimientos_Producto.objects.filter(
            dte=nc, concepto='REPARACION_STOCK_HISTORICO',
        )
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.tipo_movimiento, 'INGRESO')
        self.assertEqual(mov.cantidad, 3)

        # Tag de idempotencia en referencias.
        nc.refresh_from_db()
        self.assertIn('REPARACION_STOCK_HISTORICO', nc.referencias or '')

    def test_reparacion_post_recepcion_con_stock_aplica_ambos_lados(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        # Simular que el destino ya recepcionó: estado post + stock en destino.
        dte.estado_dte = 'RECEPCIONADO_COMPLETO'
        dte.fecha_recepcion = '2025-01-05'
        dte.save(update_fields=['estado_dte', 'fecha_recepcion'])
        Producto_Talla.objects.filter(id=self.talla_destino.id).update(stock=5)

        nc = self._crear_nc_historica_sin_movimientos(dte, cantidad_nc=3,
                                                     numero_nc=9200)

        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        stock_origen_antes = self.talla_origen.stock
        stock_destino_antes = self.talla_destino.stock

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                f'/app/dte/{nc.id}/reparar_stock/',
                data=json.dumps({
                    'lineas': [{'sku': self.talla_origen.sku, 'cantidad': 3}],
                    'motivo': 'reparacion test post',
                }),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['es_post_recepcion'])

        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, stock_origen_antes + 3)
        self.assertEqual(self.talla_destino.stock, stock_destino_antes - 3)

        movs = Movimientos_Producto.objects.filter(
            dte=nc, concepto='REPARACION_STOCK_HISTORICO',
        )
        self.assertEqual(movs.count(), 2)
        egreso = movs.filter(tipo_movimiento='EGRESO').first()
        ingreso = movs.filter(tipo_movimiento='INGRESO').first()
        self.assertIsNotNone(egreso)
        self.assertIsNotNone(ingreso)
        self.assertEqual(abs(egreso.cantidad), 3)
        self.assertEqual(ingreso.cantidad, 3)

    def test_reparacion_post_recepcion_stock_insuficiente_bloquea(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        dte.estado_dte = 'RECEPCIONADO_COMPLETO'
        dte.fecha_recepcion = '2025-01-05'
        dte.save(update_fields=['estado_dte', 'fecha_recepcion'])
        # Destino vendió parte: sólo 1 disponible.
        Producto_Talla.objects.filter(id=self.talla_destino.id).update(stock=1)

        nc = self._crear_nc_historica_sin_movimientos(dte, cantidad_nc=3,
                                                     numero_nc=9300)

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                f'/app/dte/{nc.id}/reparar_stock/',
                data=json.dumps({
                    'lineas': [{'sku': self.talla_origen.sku, 'cantidad': 3}],
                    'motivo': 'intento sin stock',
                }),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertTrue(data.get('stock_insuficiente'))
        self.assertEqual(data.get('disponible_destino'), 1)

        # Nada cambió: ni stocks ni movimientos.
        self.talla_origen.refresh_from_db()
        self.talla_destino.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, 5)
        self.assertEqual(self.talla_destino.stock, 1)
        self.assertFalse(
            Movimientos_Producto.objects.filter(
                dte=nc, concepto='REPARACION_STOCK_HISTORICO'
            ).exists()
        )

    def test_reparacion_es_idempotente(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        nc = self._crear_nc_historica_sin_movimientos(dte, cantidad_nc=3,
                                                     numero_nc=9400)

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp1 = self.client.post(
                f'/app/dte/{nc.id}/reparar_stock/',
                data=json.dumps({
                    'lineas': [{'sku': self.talla_origen.sku, 'cantidad': 3}],
                    'motivo': 'primera aplicación',
                }),
                content_type='application/json',
            )
        self.assertEqual(resp1.status_code, 200)

        movs_post_1 = Movimientos_Producto.objects.filter(
            dte=nc, concepto='REPARACION_STOCK_HISTORICO',
        ).count()

        # Segunda aplicación: debe ser bloqueada.
        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp2 = self.client.post(
                f'/app/dte/{nc.id}/reparar_stock/',
                data=json.dumps({
                    'lineas': [{'sku': self.talla_origen.sku, 'cantidad': 3}],
                    'motivo': 'intento doble',
                }),
                content_type='application/json',
            )
        self.assertEqual(resp2.status_code, 409, resp2.content)
        data2 = resp2.json()
        self.assertFalse(data2['success'])
        self.assertTrue(data2.get('ya_reparado'))

        # La cantidad de movimientos de reparación no cambió.
        movs_post_2 = Movimientos_Producto.objects.filter(
            dte=nc, concepto='REPARACION_STOCK_HISTORICO',
        ).count()
        self.assertEqual(movs_post_1, movs_post_2)


# =========================================================================
# Tests para `anular_factura_dte` cuando el DTE original es un TRASPASO.
#
# Bug histórico: la NC sobre FACTURA/BOLETA de TRASPASO usaba la lógica de
# venta normal, lo que (a) inflaba stock origen sin descontar destino y
# (b) marcaba el DTE como ANULADO, sacándolo del listado del receptor.
# =========================================================================
class AnularFacturaDteTraspasoTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Test', sku=5001, stock=10,
        )
        crear_correlativo(self.sucursal_origen, tipo_dte='NOTA DE CREDITO')

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _post_anular(self, dte_id, productos_afectados=None, motivo='Test anulación'):
        body = {
            'dte_id': dte_id,
            'tipo_anulacion': 'ANULACION',
            'metodo_devolucion': 'NO_AFECTA_CAJA',
            'motivo': motivo,
        }
        if productos_afectados is not None:
            body['productos_afectados'] = productos_afectados
        return self.client.post(
            '/app/documentos/anular-factura/',
            data=json.dumps(body),
            content_type='application/json',
        )

    def test_nc_traspaso_post_recepcion_descuenta_destino_y_no_anula(self):
        """
        Bugfix: NC sobre TRASPASO post-recepción debe descontar stock del
        destino y reponerlo en origen, SIN marcar el DTE original como
        ANULADO ni desactivar líneas (para que el receptor lo siga viendo).
        """
        # Replicar SKU en destino con 5 unidades (simula recepción).
        _, talla_destino = crear_producto_con_talla(
            self.sucursal_destino, articulo='Zap Test D', sku=5001, stock=5,
        )
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
            tipo_documento='FACTURA ELECTRONICA',
        )
        # Marcar como recepcionado.
        from django.utils import timezone as tz
        dte.estado_dte = 'RECEPCIONADO_COMPLETO'
        dte.fecha_recepcion = tz.localdate()
        dte.save(update_fields=['estado_dte', 'fecha_recepcion'])

        self.talla_origen.refresh_from_db()
        talla_destino.refresh_from_db()
        stock_origen_antes = self.talla_origen.stock   # 5
        stock_destino_antes = talla_destino.stock      # 5

        resp = self._post_anular(
            dte.id,
            productos_afectados=[{'dte_producto_id': dp.id, 'cantidad': 3}],
            motivo='Devolución parcial',
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        # Stock: destino bajó 3, origen subió 3.
        self.talla_origen.refresh_from_db()
        talla_destino.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, stock_origen_antes + 3)
        self.assertEqual(talla_destino.stock, stock_destino_antes - 3)

        # DTE original NO marcado ANULADO; línea sigue activa.
        dte.refresh_from_db()
        dp.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        self.assertNotEqual(dte.estado_dte, 'ANULADO')
        self.assertTrue(dp.activo)
        self.assertEqual(dp.stock, 5)  # post-recepción no modifica stock del DTE

        # Movimientos correctos: EGRESO destino + INGRESO origen.
        nc = Dte.objects.filter(documento_afectado=dte, es_nota_credito=True).first()
        self.assertIsNotNone(nc)
        movs = Movimientos_Producto.objects.filter(
            dte=nc, concepto='DEVOLUCION_NC_POST_RECEPCION',
        )
        self.assertEqual(movs.count(), 2)
        egreso = movs.filter(tipo_movimiento='EGRESO').first()
        ingreso = movs.filter(tipo_movimiento='INGRESO').first()
        self.assertEqual(egreso.ProductoTalla_id, talla_destino.id)
        self.assertEqual(ingreso.ProductoTalla_id, self.talla_origen.id)
        self.assertEqual(abs(egreso.cantidad), 3)
        self.assertEqual(ingreso.cantidad, 3)

    def test_nc_traspaso_post_recepcion_sin_sku_destino_devuelve_409(self):
        """
        Cuando el SKU no existe en sucursal destino, la NC devuelve 409 con
        sku_faltante_destino=True (el frontend ofrece crear_skus_destino).
        """
        # NO se crea Producto_Talla en destino → el SKU sólo existe en origen.
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
            tipo_documento='FACTURA ELECTRONICA',
        )
        from django.utils import timezone as tz
        dte.estado_dte = 'RECEPCIONADO_COMPLETO'
        dte.fecha_recepcion = tz.localdate()
        dte.save(update_fields=['estado_dte', 'fecha_recepcion'])

        resp = self._post_anular(
            dte.id,
            productos_afectados=[{'dte_producto_id': dp.id, 'cantidad': 2}],
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        self.assertTrue(data.get('sku_faltante_destino'))
        self.assertEqual(data.get('sku'), self.talla_origen.sku)

        # Nada se modificó: ni stock, ni DTE, ni NC creada.
        self.talla_origen.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, 5)  # sigue post-traspaso
        dte.refresh_from_db()
        self.assertNotEqual(dte.estado_dte, 'ANULADO')
        self.assertFalse(
            Dte.objects.filter(documento_afectado=dte, es_nota_credito=True).exists()
        )

    def test_nc_traspaso_pre_recepcion_repone_origen_y_no_anula(self):
        """
        NC sobre TRASPASO pre-recepción: stock vuelve a origen, dp.stock se
        reduce, pero el DTE NO se marca como ANULADO (el receptor lo sigue
        viendo si quedan líneas activas).
        """
        dte, dp = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
            tipo_documento='FACTURA ELECTRONICA',
        )
        # Pre-recepción: sin fecha_recepcion, estado EMITIDO.

        self.talla_origen.refresh_from_db()
        stock_origen_antes = self.talla_origen.stock  # 5

        resp = self._post_anular(
            dte.id,
            productos_afectados=[{'dte_producto_id': dp.id, 'cantidad': 2}],
            motivo='Quito 2 antes de recepción',
        )
        self.assertEqual(resp.status_code, 200, resp.content)

        self.talla_origen.refresh_from_db()
        self.assertEqual(self.talla_origen.stock, stock_origen_antes + 2)

        # dp se redujo a 3, sigue activo.
        dp.refresh_from_db()
        self.assertEqual(dp.stock, 3)
        self.assertTrue(dp.activo)

        # DTE NO se marca ANULADO.
        dte.refresh_from_db()
        self.assertNotEqual(dte.estado_dte, 'ANULADO')

        # NC creada con movimiento DEVOLUCION_NC.
        nc = Dte.objects.filter(documento_afectado=dte, es_nota_credito=True).first()
        self.assertIsNotNone(nc)
        movs = Movimientos_Producto.objects.filter(
            dte=nc, concepto='DEVOLUCION_NC',
        )
        self.assertEqual(movs.count(), 1)


# =========================================================================
# Tests para `api_crear_skus_destino`: replicación de SKUs en sucursal destino.
# =========================================================================
class CrearSkusDestinoTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Replicar', sku=6001, stock=10,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def test_crear_sku_destino_replica_producto_talla_con_stock_cero(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=3,
        )
        # Antes: sin Producto_Talla en destino.
        self.assertFalse(
            Producto_Talla.objects.filter(
                sku=6001, producto__sucursal=self.sucursal_destino,
            ).exists()
        )

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                f'/app/api/dte/{dte.id}/crear_skus_destino/',
                data=json.dumps({'skus': [6001]}),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['creados']), 1)
        self.assertEqual(data['creados'][0]['sku'], 6001)

        # Después: existe Producto_Talla en destino con stock=0.
        talla_destino = Producto_Talla.objects.filter(
            sku=6001, producto__sucursal=self.sucursal_destino,
        ).first()
        self.assertIsNotNone(talla_destino)
        self.assertEqual(talla_destino.stock, 0)
        # Producto en destino conserva costo / sobreprecio / precio del origen.
        self.assertEqual(talla_destino.producto.articulo, 'Zap Replicar')

    def test_crear_sku_destino_idempotente_si_ya_existe(self):
        # Crear talla en destino previamente.
        crear_producto_con_talla(
            self.sucursal_destino, articulo='Zap Replicar D', sku=6001, stock=2,
        )
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=3,
        )

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self.client.post(
                f'/app/api/dte/{dte.id}/crear_skus_destino/',
                data=json.dumps({'skus': [6001]}),
                content_type='application/json',
            )

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(len(data['creados']), 0)
        self.assertIn(6001, data['existentes'])

        # Stock destino no se tocó.
        talla = Producto_Talla.objects.get(
            sku=6001, producto__sucursal=self.sucursal_destino,
        )
        self.assertEqual(talla.stock, 2)


# =========================================================================
# Tests para `detalle_dte` enriquecido: documentos_hijos, sucursal_destino,
# skus_faltantes_destino, totales_nc.
# =========================================================================
class DetalleDteEnriquecidoTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Detalle', sku=7001, stock=10,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def test_detalle_dte_traspaso_marca_skus_faltantes_destino(self):
        # SKU sólo existe en origen — destino no tiene Producto_Talla.
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=3,
        )

        resp = self.client.get(f'/app/documentos/api/dte/{dte.id}/')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['dte']['es_traspaso'])
        self.assertEqual(data['dte']['sucursal_destino']['alias'], 'DESTINO')

        # SKU 7001 aparece como faltante en destino.
        faltantes = data['skus_faltantes_destino']
        self.assertEqual(len(faltantes), 1)
        self.assertEqual(faltantes[0]['sku'], 7001)

        # Producto en lista marcado existe_en_destino=False.
        prod = data['productos'][0]
        self.assertFalse(prod['existe_en_destino'])

    def test_detalle_dte_padre_lista_documentos_hijos(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=3,
        )
        # Crear un hijo NC manual (igual que en TrazabilidadApiTest).
        hijo = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=9501, tipo_documento='NOTA DE CREDITO',
            monto_neto=1000, monto_con_iva=1190,
            estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='x', fecha_emision='2025-01-02',
            fecha_vencimiento='2025-01-02', diasCredito=0,
            bultos=0, unidades_productos=1,
            tipo_transaccion='ANULACION', sucursal=self.sucursal_origen,
            es_nota_credito=True, documento_afectado=dte,
            motivo_nc='NC test detalle',
        )

        resp = self.client.get(f'/app/documentos/api/dte/{dte.id}/')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        hijos_ids = [h['id'] for h in data['documentos_hijos']]
        self.assertIn(hijo.id, hijos_ids)


# =========================================================================
# Tests para `eliminar_producto_todas_sucursales`: bloqueos de seguridad.
#
# Antes del fix, borrar un producto desde gestionProductos arrastraba en
# CASCADE los movimientos TRASPASO_SALIDA, los Ticket_Productos y los
# LoteProducto, dejando los DTE huérfanos. Ahora bloquea con 409 si hay
# datos operativos vivos.
# =========================================================================
class EliminarProductoBloqueosTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        self.producto, self.talla = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Eliminar', sku=8001, stock=0,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _post_eliminar(self, producto_id):
        return self.client.post(
            '/app/eliminar_producto_todas_sucursales/',
            data=json.dumps({'producto_id': producto_id}),
            content_type='application/json',
        )

    def test_bloquea_si_traspaso_pendiente_de_recepcion(self):
        # Crear DTE TRASPASO con movimiento TRASPASO_SALIDA, sin recepcionar.
        # Stock arranca en 0 para evitar disparar el bloqueo STOCK_NO_NULO.
        Producto_Talla.objects.filter(id=self.talla.id).update(stock=10)
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla, cantidad=5,
        )
        # _crear_traspaso baja stock a 5 → forzamos a 0 para aislar el test.
        Producto_Talla.objects.filter(id=self.talla.id).update(stock=0)

        resp = self._post_eliminar(self.producto.id)
        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        self.assertTrue(data['bloqueado'])
        tipos = [b['tipo'] for b in data['bloqueos']]
        self.assertIn('TRASPASO_EN_TRANSITO', tipos)

        # No se borró el producto.
        self.assertTrue(Producto.objects.filter(id=self.producto.id).exists())
        self.assertTrue(Producto_Talla.objects.filter(id=self.talla.id).exists())

    def test_bloquea_si_stock_mayor_a_cero(self):
        Producto_Talla.objects.filter(id=self.talla.id).update(stock=3)

        resp = self._post_eliminar(self.producto.id)
        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        tipos = [b['tipo'] for b in data['bloqueos']]
        self.assertIn('STOCK_NO_NULO', tipos)

        # Producto y talla intactos.
        self.assertTrue(Producto_Talla.objects.filter(id=self.talla.id).exists())

    def test_permite_borrar_sin_movimientos_ni_stock(self):
        # Stock 0, sin DTE ni tickets ni pendientes.
        Producto_Talla.objects.filter(id=self.talla.id).update(stock=0)

        resp = self._post_eliminar(self.producto.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])

        # Borrado efectivo.
        self.assertFalse(Producto.objects.filter(id=self.producto.id).exists())
        self.assertFalse(Producto_Talla.objects.filter(id=self.talla.id).exists())


# =========================================================================
# Tests para `api_crear_stock_destino_manual`: recepción manual de TRASPASO
# sin pasar por confirmar_recepcion_api ni emitir NC.
# =========================================================================
class CrearStockDestinoManualTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.sucursal_origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.sucursal_destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.sucursal_origen)

        _, self.talla_origen = crear_producto_con_talla(
            self.sucursal_origen, articulo='Zap Manual', sku=9001, stock=10,
        )

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal_origen.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _post(self, dte_id, items, motivo='Recepción manual test'):
        return self.client.post(
            f'/app/api/dte/{dte_id}/crear_stock_destino_manual/',
            data=json.dumps({'items': items, 'motivo': motivo}),
            content_type='application/json',
        )

    def test_crea_talla_destino_aplica_stock_y_marca_recepcionado(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        # Destino sin Producto_Talla del SKU.
        self.assertFalse(
            Producto_Talla.objects.filter(
                sku=9001, producto__sucursal=self.sucursal_destino,
            ).exists()
        )

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self._post(dte.id, [{'sku': 9001, 'stock_final': 5}])

        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['dte_estado'], 'RECEPCIONADO_COMPLETO')
        self.assertEqual(data['sucursal_destino']['alias'], 'DESTINO')

        # Talla creada en destino con stock=5.
        talla_dest = Producto_Talla.objects.get(
            sku=9001, producto__sucursal=self.sucursal_destino,
        )
        self.assertEqual(talla_dest.stock, 5)

        # Movimiento AJUSTE_STOCK_MANUAL_DESTINO INGRESO con cantidad=5.
        movs = Movimientos_Producto.objects.filter(
            dte=dte, concepto='AJUSTE_STOCK_MANUAL_DESTINO',
        )
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.tipo_movimiento, 'INGRESO')
        self.assertEqual(mov.cantidad, 5)

        # DTE actualizado.
        dte.refresh_from_db()
        self.assertEqual(dte.estado_dte, 'RECEPCIONADO_COMPLETO')
        self.assertIsNotNone(dte.fecha_recepcion)
        self.assertIn('STOCK MANUAL DESTINO', dte.referencias or '')

    def test_bloquea_si_dte_ya_tiene_nc(self):
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )
        # Crear NC hija (cualquier estado vivo).
        Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=9999, tipo_documento='NOTA DE CREDITO',
            monto_neto=1000, monto_con_iva=1190,
            estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='x', fecha_emision='2025-01-02',
            fecha_vencimiento='2025-01-02', diasCredito=0,
            bultos=0, unidades_productos=1,
            tipo_transaccion='ANULACION', sucursal=self.sucursal_origen,
            es_nota_credito=True, documento_afectado=dte,
            motivo_nc='NC previa',
        )

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self._post(dte.id, [{'sku': 9001, 'stock_final': 5}])

        self.assertEqual(resp.status_code, 409, resp.content)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertIn('NC', data['error'])

        # No se creó talla en destino ni movimiento manual.
        self.assertFalse(
            Producto_Talla.objects.filter(
                sku=9001, producto__sucursal=self.sucursal_destino,
            ).exists()
        )
        self.assertFalse(
            Movimientos_Producto.objects.filter(
                dte=dte, concepto='AJUSTE_STOCK_MANUAL_DESTINO',
            ).exists()
        )

    def test_decremento_genera_movimiento_egreso(self):
        # Talla destino existe con stock=10.
        _, talla_dest = crear_producto_con_talla(
            self.sucursal_destino, articulo='Zap Manual D', sku=9001, stock=10,
        )
        dte, _ = _crear_traspaso(
            self.sucursal_origen, self.sucursal_destino,
            self.talla_origen, cantidad=5,
        )

        with _patch_permiso_aprobar(), _patch_permiso_helper():
            resp = self._post(dte.id, [{'sku': 9001, 'stock_final': 4}])

        self.assertEqual(resp.status_code, 200, resp.content)

        talla_dest.refresh_from_db()
        self.assertEqual(talla_dest.stock, 4)

        movs = Movimientos_Producto.objects.filter(
            dte=dte, concepto='AJUSTE_STOCK_MANUAL_DESTINO',
        )
        self.assertEqual(movs.count(), 1)
        mov = movs.first()
        self.assertEqual(mov.tipo_movimiento, 'EGRESO')
        self.assertEqual(mov.cantidad, -6)  # 4 - 10 = -6
