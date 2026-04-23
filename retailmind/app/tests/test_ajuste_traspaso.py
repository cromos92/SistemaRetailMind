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
