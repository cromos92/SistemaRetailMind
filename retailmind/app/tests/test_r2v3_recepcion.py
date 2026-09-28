"""
Unidad R2V3 (ronda 2, módulo Compras) — recepción, regularización,
notificaciones y trazabilidad de traspasos.

1. Permisos en la vista (SEC e/f): campana del menú exige
   recepcion_dte.puede_ver; trazabilidad acepta cualquiera de las pantallas
   que la abren; los reparadores siguen en recepcion_dte.puede_aprobar. El 403
   es SIEMPRE JSON (también a un fetch sin X-Requested-With).
2. recepciones_pendientes_api: kpis=0, sin `origenes`,
   resumen.documentos_con_problema, despachado_por, unidades_faltantes /
   unidades_sobrantes y filtro exacto dte_id (B9-12 / B9-11 / B9-20).
3. obtener_dtes_rechazados_api (sucursal_id, stock_devuelto) y
   emitidos_pendientes_api (tiene_despacho).
4. obtener_productos_regularizar (misma_empresa, base_precio,
   monto_item_linea, cantidad_linea; sucursal_id en rechazados en frío) y
   obtener_detalle_dte_recepcionado (despachado_por).
5. obtener_dtes_regularizacion_receptor_api: COMPLETO con NC en devolución
   física pendiente visible, EMITIDO sin hijo fuera, sin N+1 (B7-02/B7-10).
6. api_reparar_traspaso_manual (CC-15): camino feliz, idempotencia, 409 con
   NC sin confirmar, 403 no emisora, 400 cantidad inválida, bitácora en hora
   de Chile.
7. CC-08: emitir_dte consume PendienteDespacho en la misma transacción y el
   endpoint viejo consumir_pendientes_despacho queda idempotente.
8. anular_factura_dte, traspaso PRE-recepción: sin TRASPASO_SALIDA vigente
   no se acredita stock al origen (B7-03).
"""
import datetime
import json
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    Dte, Dte_Productos, Producto_Talla, Movimientos_Producto,
    Productos_Recepcionados, PendienteDespacho, NotificacionDTE,
)
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla, otorgar_ver_pantalla,
)


def _permisos():
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True)


class _Base(TestCase):
    SKU = 95001

    def setUp(self):
        self.user = crear_usuario(username='r2v3', rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.origen)
        _, self.t_origen = crear_producto_con_talla(
            self.origen, articulo='Zap R2', sku=self.SKU, stock=20, costo=100)
        _, self.t_destino = crear_producto_con_talla(
            self.destino, articulo='Zap R2', sku=self.SKU, stock=0, costo=100)
        self.client = Client()
        self.client.force_login(self.user)
        self._folio = 70000

    def _traspaso(self, cantidad=5, estado='EMITIDO', tipo_documento='GUIA',
                  con_salida=True, estado_salida='COMPLETADO', responsable='despachador1',
                  talla=None, monto_item=None):
        talla = talla or self.t_origen
        self._folio += 1
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa,
            numero_documento=self._folio, tipo_documento=tipo_documento,
            monto_neto=Decimal(cantidad * 1000), monto_con_iva=Decimal(cantidad * 1190),
            estado_pago='PENDIENTE', estado_dte=estado, responsable='emisor1',
            fecha_emision='2026-09-01', fecha_vencimiento='2026-09-01',
            diasCredito=0, bultos=1, unidades_productos=cantidad,
            tipo_transaccion='TRASPASO', sucursal=self.origen,
        )
        dp = Dte_Productos.objects.create(
            dte=dte, productoTalla=talla, descripcion='Zap R2 - Talla 42',
            costo=100, sobreprecio=0, precio=1000, stock=cantidad, activo=True,
            monto_item=monto_item or 0,
        )
        if con_salida:
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=talla,
                sucursal_origen=self.origen, sucursal_destino=self.destino,
                cantidad=-cantidad, costo=100, concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO', estado=estado_salida, responsable=responsable,
            )
            if estado_salida in ('COMPLETADO', 'PENDIENTE_RECEPCION', 'RECHAZADO'):
                Producto_Talla.objects.filter(id=talla.id).update(
                    stock=Producto_Talla.objects.get(id=talla.id).stock - cantidad)
        return dte, dp

    def _recepcion(self, dte, dp, estado, faltante=0, sobrante=0, arribado=None):
        return Productos_Recepcionados.objects.create(
            dte=dte, dte_producto=dp, producto_talla=dp.productoTalla,
            stockArribado=dp.stock - faltante if arribado is None else arribado,
            cantidad_esperada=dp.stock, cantidad_faltante=faltante,
            cantidad_sobrante=sobrante, cantidad_danada=0, estado=estado,
        )

    def _sesion(self, sucursal, client=None):
        c = client or self.client
        s = c.session
        s['idSucursalActual'] = sucursal.id
        s['idEmpresaActual'] = sucursal.empresa.id
        s['alias'] = sucursal.alias
        s.save()

    def _stock(self, talla):
        return Producto_Talla.objects.get(id=talla.id).stock


# ---------------------------------------------------------------------------
# 1. Permisos en la vista
# ---------------------------------------------------------------------------
class PermisosCampanaYTrazabilidadTest(_Base):

    def setUp(self):
        super().setUp()
        self.dte, self.dp = self._traspaso(cantidad=3)
        self.vendedor = crear_usuario(username='r2v3_vend', rol='vendedor')
        crear_empresa_user(self.vendedor, self.empresa, self.destino)
        self.cv = Client()
        self.cv.force_login(self.vendedor)
        self._sesion(self.destino, self.cv)
        self.notif = NotificacionDTE.objects.create(
            dte=self.dte, empresa_receptora=self.empresa, sucursal=self.destino,
            tipo='CORRECCION_RECEPCION', titulo='t', mensaje='m',
        )

    def test_campana_sin_recepcion_dte_responde_403_json_vacio(self):
        casos = [
            ('get', '/app/dtes-pendientes-recibir/', None, {'dtes': [], 'total_pendientes': 0}),
            ('get', '/app/dtes-pendientes-regularizar/', None, {'dtes': [], 'total_pendientes': 0}),
            ('get', '/app/notificaciones-dte/?limit=5', None,
             {'notificaciones': [], 'total_no_leidas': 0}),
            ('post', '/app/dtes-pendientes-recibir/descartar/', {'dte_id': self.dte.id}, {}),
            ('post', '/app/notificaciones-dte/marcar-leida/', {'notificacion_id': self.notif.id}, {}),
            ('post', '/app/notificaciones-dte/eliminar/', {'notificacion_id': self.notif.id}, {}),
            ('post', '/app/notificaciones-dte/descartar-todas/', {}, {}),
        ]
        for metodo, url, body, esperado in casos:
            with self.subTest(url=url):
                if metodo == 'get':
                    resp = self.cv.get(url)
                else:
                    resp = self.cv.post(url, data=json.dumps(body), content_type='application/json')
                self.assertEqual(resp.status_code, 403, resp.content)
                data = resp.json()
                self.assertFalse(data['success'])
                self.assertTrue(data['sin_permiso'])
                for k, v in esperado.items():
                    self.assertEqual(data[k], v)
        # Nada se borró ni se marcó.
        self.notif.refresh_from_db()
        self.assertFalse(self.notif.leida)

    def test_campana_con_recepcion_dte_puede_ver_responde(self):
        otorgar_ver_pantalla('vendedor', 'recepcion_dte')
        resp = self.cv.get('/app/dtes-pendientes-recibir/')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual([d['id'] for d in data['dtes']], [self.dte.id])
        resp = self.cv.get('/app/notificaciones-dte/?limit=abc')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

    def test_trazabilidad_acepta_cualquiera_de_las_pantallas_y_403_es_json(self):
        url = f'/app/api/dte/{self.dte.id}/trazabilidad/'
        # Sin ninguna: 403 JSON aunque no venga X-Requested-With (fetch()).
        resp = self.cv.get(url, HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json()['sin_permiso'])
        resp = self.cv.get('/app/api/dte/ncs_sin_stock/')
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['items'], [])
        # Gestión Documentos Compras también abre la trazabilidad.
        otorgar_ver_pantalla('vendedor', 'gestion_dte_compras')
        resp = self.cv.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])

    def test_reparadores_exigen_puede_aprobar_con_403_json(self):
        otorgar_ver_pantalla('vendedor', 'recepcion_dte', 'gestion_dte')  # solo puede_ver
        self._sesion(self.origen, self.cv)
        resp = self.cv.get(f'/app/api/dte/{self.dte.id}/diagnostico_reparacion_traspaso/')
        self.assertEqual(resp.status_code, 403)   # antes: 302 a HTML
        self.assertIn('error', resp.json())
        for url in (f'/app/api/dte/{self.dte.id}/reparar_traspaso_manual/',
                    f'/app/api/dte/{self.dte.id}/crear_skus_destino/',
                    f'/app/api/dte/{self.dte.id}/crear_stock_destino_manual/',
                    f'/app/dte/{self.dte.id}/reparar_stock/'):
            with self.subTest(url=url):
                resp = self.cv.post(url, data='{}', content_type='application/json')
                self.assertEqual(resp.status_code, 403)
                self.assertFalse(resp.json()['success'])
        # Con puede_aprobar pasa el permiso (y cae en la validación normal).
        otorgar_ver_pantalla('vendedor', 'recepcion_dte', puede_aprobar=True)
        resp = self.cv.get(f'/app/api/dte/{self.dte.id}/diagnostico_reparacion_traspaso/')
        self.assertEqual(resp.status_code, 200, resp.content)


# ---------------------------------------------------------------------------
# 2. recepciones_pendientes_api
# ---------------------------------------------------------------------------
class RecepcionesPendientesContratoTest(_Base):

    def setUp(self):
        super().setUp()
        self.pend, _ = self._traspaso(cantidad=2)
        self.parcial, dp = self._traspaso(cantidad=6, estado='RECEPCIONADO_PARCIAL')
        self._recepcion(self.parcial, dp, 'FALTANTE', faltante=2)
        # Un documento con todo regularizado: su estado sigue PARCIAL pero no
        # cuenta como "con problema".
        self.resuelto, dp2 = self._traspaso(cantidad=4, estado='RECEPCIONADO_PARCIAL')
        self._recepcion(self.resuelto, dp2, 'REGULARIZADO', faltante=1)
        self._sesion(self.destino)

    def _get(self, qs=''):
        with _permisos():
            resp = self.client.get('/app/dte/recepciones_pendientes/' + qs)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def test_con_kpis_por_defecto_y_sin_origenes(self):
        data = self._get()
        self.assertNotIn('origenes', data)
        for clave in ('recibidos_hoy', 'productos_con_problemas', 'documentos_con_problema',
                      'pendientes', 'total_unidades_pendientes', 'pendientes_mes'):
            self.assertIn(clave, data['resumen'])
        self.assertIn('conteo_estados', data)
        self.assertEqual(data['conteo_estados'].get('RECEPCIONADO_PARCIAL'), 2)
        # Solo el documento con una línea abierta.
        self.assertEqual(data['resumen']['documentos_con_problema'], 1)

    def test_documentos_con_problema_cuadra_con_por_resolver(self):
        data = self._get()
        with _permisos():
            reg = self.client.get('/app/dte/obtener_productos_regularizar/?tab=pendiente').json()
        self.assertEqual(data['resumen']['documentos_con_problema'],
                         reg['estadisticas']['dtes_con_problemas'])

    def test_kpis_0_omite_los_kpis_y_mantiene_lo_filtrado(self):
        data = self._get('?kpis=0')
        self.assertNotIn('conteo_estados', data)
        for clave in ('recibidos_hoy', 'productos_con_problemas', 'documentos_con_problema'):
            self.assertNotIn(clave, data['resumen'])
        self.assertEqual(data['resumen']['pendientes'], 1)
        self.assertIn('total_unidades_pendientes', data['resumen'])
        self.assertEqual(data['pagination']['total_items'], 3)

    def test_items_traen_despachado_por_y_diferencias(self):
        data = self._get('?kpis=0')
        por_id = {i['id']: i for i in data['items']}
        self.assertEqual(por_id[self.parcial.id]['despachado_por'], 'despachador1')
        self.assertEqual(por_id[self.parcial.id]['unidades_faltantes'], 2)
        self.assertEqual(por_id[self.parcial.id]['unidades_sobrantes'], 0)
        self.assertEqual(por_id[self.pend.id]['unidades_faltantes'], 0)

    def test_filtro_exacto_por_dte_id(self):
        data = self._get(f'?kpis=0&dte_id={self.parcial.id}')
        self.assertEqual([i['id'] for i in data['items']], [self.parcial.id])
        data = self._get('?kpis=0&dte_id=abc')   # inválido: se ignora
        self.assertEqual(data['pagination']['total_items'], 3)


# ---------------------------------------------------------------------------
# 3. Rechazados y emitidos pendientes
# ---------------------------------------------------------------------------
class RechazadosYEmitidosTest(_Base):

    def test_rechazados_exponen_sucursal_id_y_stock_devuelto(self):
        nuevo, _ = self._traspaso(cantidad=2, estado='RECHAZADO', estado_salida='CANCELADO')
        viejo, _ = self._traspaso(cantidad=2, estado='RECHAZADO', estado_salida='RECHAZADO')
        self._sesion(self.origen)
        with _permisos():
            data = self.client.get('/app/dte/obtener_rechazados/').json()
        por_id = {i['id']: i for i in data['items']}
        self.assertEqual(por_id[nuevo.id]['sucursal_id'], self.origen.id)
        self.assertTrue(por_id[nuevo.id]['stock_devuelto'])
        self.assertFalse(por_id[viejo.id]['stock_devuelto'])

    def test_emitidos_pendientes_expone_tiene_despacho(self):
        con, _ = self._traspaso(cantidad=2)
        legacy, _ = self._traspaso(cantidad=2, estado='ACEPTADO', con_salida=False)
        self._sesion(self.origen)
        with _permisos():
            resp = self.client.get('/app/dte/emitidos_pendientes/?pagina=abc')
        self.assertEqual(resp.status_code, 200, resp.content)
        por_id = {i['id']: i for i in resp.json()['items']}
        self.assertTrue(por_id[con.id]['tiene_despacho'])
        self.assertFalse(por_id[legacy.id]['tiene_despacho'])


# ---------------------------------------------------------------------------
# 4. Por resolver y detalle
# ---------------------------------------------------------------------------
class ProductosRegularizarYDetalleTest(_Base):

    def test_lineas_traen_datos_de_la_nc(self):
        # Cabecera neta 4.000 = Σ monto_item de las líneas → base NETO.
        dte, dp = self._traspaso(cantidad=4, estado='RECEPCIONADO_PARCIAL',
                                 tipo_documento='FACTURA ELECTRONICA', monto_item=4000)
        self._recepcion(dte, dp, 'FALTANTE', faltante=1)
        self._sesion(self.origen)
        with _permisos():
            data = self.client.get('/app/dte/obtener_productos_regularizar/?tab=pendiente').json()
        linea = data['productos'][0]
        self.assertTrue(linea['misma_empresa'])
        self.assertEqual(linea['base_precio'], 'NETO')
        self.assertEqual(linea['monto_item_linea'], 4000)
        self.assertEqual(linea['cantidad_linea'], 4)

    def test_rechazados_en_frio_traen_sucursal_id(self):
        dte, _ = self._traspaso(cantidad=2, estado='RECHAZADO', estado_salida='CANCELADO')
        self._sesion(self.origen)
        with _permisos():
            data = self.client.get('/app/dte/obtener_productos_regularizar/?tab=rechazado').json()
        fila = next(r for r in data['dtes_rechazados_sin_recepcion'] if r['dte_id'] == dte.id)
        self.assertEqual(fila['sucursal_id'], self.origen.id)

    def test_detalle_trae_despachado_por(self):
        dte, dp = self._traspaso(cantidad=2, estado='RECEPCIONADO_COMPLETO')
        self._recepcion(dte, dp, 'RECEPCIONADO_OK')
        self._sesion(self.destino)
        with _permisos():
            data = self.client.get(
                f'/app/dte/obtener_detalle_dte_recepcionado/?dte_id={dte.id}').json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['dte']['despachado_por'], 'despachador1')


# ---------------------------------------------------------------------------
# 5. Mis Regularizaciones (receptor)
# ---------------------------------------------------------------------------
class RegularizacionReceptorTest(_Base):

    def _hijo(self, padre, confirmado=False):
        self._folio += 1
        return Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=self._folio,
            tipo_documento='AJUSTE TRASPASO POST', monto_neto=Decimal('0'),
            monto_con_iva=Decimal('0'), estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='tester', fecha_emision='2026-09-02', fecha_vencimiento='2026-09-02',
            diasCredito=0, bultos=0, unidades_productos=1, tipo_transaccion='TRASPASO',
            sucursal=self.origen, documento_afectado=padre, requiere_devolucion_fisica=True,
            fecha_confirmacion_devolucion=timezone.now() if confirmado else None,
        )

    def _items(self):
        self._sesion(self.destino)
        with _permisos():
            resp = self.client.get('/app/dte/obtener_regularizacion_receptor/')
        self.assertEqual(resp.status_code, 200, resp.content)
        return {i['id']: i for i in resp.json()['items']}

    def test_completo_con_devolucion_pendiente_aparece_y_emitido_no(self):
        completo, _ = self._traspaso(estado='RECEPCIONADO_COMPLETO')
        hijo = self._hijo(completo)
        completo_cerrado, _ = self._traspaso(estado='RECEPCIONADO_COMPLETO')
        self._hijo(completo_cerrado, confirmado=True)
        emitido, _ = self._traspaso(estado='EMITIDO')
        parcial, _ = self._traspaso(estado='RECEPCIONADO_PARCIAL')

        items = self._items()
        self.assertIn(completo.id, items)
        self.assertEqual(items[completo.id]['hijo_devolucion_pendiente']['id'], hijo.id)
        self.assertIn('debes despachar', items[completo.id]['label'])
        self.assertNotIn(completo_cerrado.id, items)
        self.assertNotIn(emitido.id, items)
        self.assertIn(parcial.id, items)
        self.assertIsNone(items[parcial.id]['hijo_devolucion_pendiente'])

    def test_sin_n_mas_1_por_hijo(self):
        for _ in range(2):
            padre, _dp = self._traspaso(estado='RECEPCIONADO_PARCIAL')
            self._hijo(padre)
        self._sesion(self.destino)
        with _permisos():
            self.client.get('/app/dte/obtener_regularizacion_receptor/')   # calienta sesión
        with _permisos(), CaptureQueriesContext(connection) as pocos:
            self.client.get('/app/dte/obtener_regularizacion_receptor/')
        for _ in range(4):
            padre, _dp = self._traspaso(estado='RECEPCIONADO_PARCIAL')
            self._hijo(padre)
        with _permisos(), CaptureQueriesContext(connection) as muchos:
            data = self.client.get('/app/dte/obtener_regularizacion_receptor/').json()
        self.assertEqual(len(data['items']), 6)
        self.assertEqual(len(muchos), len(pocos))


# ---------------------------------------------------------------------------
# 6. api_reparar_traspaso_manual (CC-15)
# ---------------------------------------------------------------------------
class RepararTraspasoManualTest(_Base):

    def setUp(self):
        super().setUp()
        self.dte, self.dp = self._traspaso(cantidad=5)
        self._sesion(self.origen)

    def _post(self, cantidad=5, **extra):
        item = {'dte_producto_id': self.dp.id, 'producto_talla_id': self.t_origen.id,
                'cantidad_recepcionada': cantidad}
        body = {'items': [item], 'motivo': 'Recepción física confirmada por teléfono'}
        body.update(extra)
        with _permisos():
            return self.client.post(
                f'/app/api/dte/{self.dte.id}/reparar_traspaso_manual/',
                data=json.dumps(body), content_type='application/json')

    def test_camino_feliz_y_segunda_pasada_no_duplica(self):
        resp = self._post(cantidad=5)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['dte_estado'], 'RECEPCIONADO_COMPLETO')
        self.assertEqual(self._stock(self.t_destino), 5)
        rec = Productos_Recepcionados.objects.get(dte=self.dte)
        self.assertEqual((rec.estado, rec.stockArribado, rec.sucursal_destino_id),
                         ('RECEPCIONADO_OK', 5, self.destino.id))
        entradas = Movimientos_Producto.objects.filter(dte=self.dte, concepto='TRASPASO_ENTRADA')
        self.assertEqual(sum(m.cantidad for m in entradas), 5)
        self.dte.refresh_from_db()
        self.assertIsNotNone(self.dte.fecha_recepcion)

        # Mismo envío otra vez: la recepción ya tiene 5, delta 0.
        resp = self._post(cantidad=5)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self._stock(self.t_destino), 5)
        self.assertEqual(Movimientos_Producto.objects.filter(
            dte=self.dte, concepto='TRASPASO_ENTRADA').count(), 1)

    def test_parcial_deja_estado_parcial(self):
        resp = self._post(cantidad=3)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['dte_estado'], 'RECEPCIONADO_PARCIAL')
        rec = Productos_Recepcionados.objects.get(dte=self.dte)
        self.assertEqual((rec.estado, rec.cantidad_faltante), ('RECEPCIONADO_PARCIAL', 2))
        self.assertEqual(self._stock(self.t_destino), 3)

    def test_nc_asociada_exige_confirmacion(self):
        self._folio += 1
        Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=self._folio,
            tipo_documento='NOTA DE CREDITO', monto_neto=Decimal('1000'),
            monto_con_iva=Decimal('1190'), estado_pago='PAGADO', estado_dte='EMITIDO',
            responsable='t', fecha_emision='2026-09-02', fecha_vencimiento='2026-09-02',
            diasCredito=0, bultos=0, unidades_productos=1, tipo_transaccion='ANULACION',
            sucursal=self.origen, es_nota_credito=True, documento_afectado=self.dte,
        )
        resp = self._post()
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(resp.json()['requiere_confirmar_nc'])
        self.assertFalse(Productos_Recepcionados.objects.filter(dte=self.dte).exists())
        resp = self._post(confirmar_nc=True)
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_solo_la_emisora_y_cantidad_valida(self):
        self._sesion(self.destino)
        self.assertEqual(self._post().status_code, 403)
        self._sesion(self.origen)
        resp = self._post(cantidad=9)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Productos_Recepcionados.objects.filter(dte=self.dte).exists())
        self.assertEqual(self._stock(self.t_destino), 0)
        self.dte.refresh_from_db()
        self.assertEqual(self.dte.estado_dte, 'EMITIDO')

    def test_bitacora_en_hora_de_chile(self):
        utc = datetime.datetime(2026, 9, 27, 2, 30, tzinfo=datetime.timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=utc):
            resp = self._post()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.dte.refresh_from_db()
        local = timezone.localtime(utc).strftime('%Y-%m-%d %H:%M')
        self.assertIn(f'[REPARACION TRAZABILIDAD TRASPASO] {local}', self.dte.referencias)
        self.assertNotIn('2026-09-27 02:30', self.dte.referencias)


# ---------------------------------------------------------------------------
# 7. CC-08: PendienteDespacho en la misma transacción que el despacho
# ---------------------------------------------------------------------------
class PendientesDespachoEmisionTest(_Base):

    def setUp(self):
        super().setUp()
        self._sesion(self.origen)

    def _pendiente(self, cantidad, dias_atras=1):
        p = PendienteDespacho.objects.create(
            producto_talla=self.t_origen, sucursal_origen=self.origen,
            sucursal_destino=self.destino, cantidad=cantidad,
        )
        PendienteDespacho.objects.filter(id=p.id).update(
            created_at=timezone.now() - datetime.timedelta(days=dias_atras))
        return p

    def _emitir(self, cantidad):
        body = {
            'metodo_despacho': 'interno', 'tipo_documento': 'GUIA',
            'fecha_emision': timezone.localdate().isoformat(),
            'observaciones': '', 'sucursal_destino_id': self.destino.id,
            'detalle_productos': [{'talla_id': self.t_origen.id, 'cantidad': cantidad, 'precio': 100}],
        }
        with _permisos():
            return self.client.post('/app/emitir_dte/', data=json.dumps(body),
                                    content_type='application/json')

    def _consumir_viejo(self, pend, cantidad):
        with _permisos():
            return self.client.post(
                '/app/consumir_pendientes_despacho/',
                data=json.dumps({'consumos': [{'id': pend.id, 'cantidad': cantidad}]}),
                content_type='application/json')

    def test_emitir_consume_y_el_post_viejo_no_descuenta_dos_veces(self):
        pend = self._pendiente(3)
        resp = self._emitir(2)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data['pendientes_despacho_consumidos'][0]['id'], pend.id)
        self.assertEqual(data['pendientes_despacho_consumidos'][0]['cantidad'], 2)
        pend.refresh_from_db()
        self.assertEqual((pend.cantidad_despachada, pend.estado), (2, 'PARCIAL'))

        # emisionDTE sigue haciendo el POST fire-and-forget: idempotente.
        resp = self._consumir_viejo(pend, 2)
        self.assertEqual(resp.status_code, 200, resp.content)
        act = resp.json()['actualizados'][0]
        self.assertEqual(act['cantidad_descontada'], 0)
        self.assertTrue(act['ya_consumido'])
        pend.refresh_from_db()
        self.assertEqual((pend.cantidad_despachada, pend.estado), (2, 'PARCIAL'))

    def test_fifo_entre_pendientes_del_mismo_destino(self):
        viejo = self._pendiente(1, dias_atras=3)
        nuevo = self._pendiente(2, dias_atras=1)
        self.assertEqual(self._emitir(2).status_code, 200)
        viejo.refresh_from_db()
        nuevo.refresh_from_db()
        self.assertEqual((viejo.cantidad_despachada, viejo.estado), (1, 'DESPACHADO'))
        self.assertEqual((nuevo.cantidad_despachada, nuevo.estado), (1, 'PARCIAL'))

    def test_emision_fallida_no_toca_pendientes(self):
        pend = self._pendiente(3)
        resp = self._emitir(999)   # stock insuficiente
        self.assertEqual(resp.status_code, 400)
        pend.refresh_from_db()
        self.assertEqual(pend.cantidad_despachada, 0)

    def test_post_viejo_sin_despacho_real_no_descuenta(self):
        pend = self._pendiente(3)
        resp = self._consumir_viejo(pend, 3)
        self.assertEqual(resp.status_code, 200)
        pend.refresh_from_db()
        self.assertEqual((pend.cantidad_despachada, pend.estado), (0, 'PENDIENTE'))

    def test_post_viejo_con_despacho_sin_imputar_si_descuenta(self):
        # Despacho hecho por un flujo que no consumía la cola (legacy).
        pend = self._pendiente(3)
        self._traspaso(cantidad=2)
        resp = self._consumir_viejo(pend, 3)
        self.assertEqual(resp.json()['actualizados'][0]['cantidad_descontada'], 2)
        pend.refresh_from_db()
        self.assertEqual((pend.cantidad_despachada, pend.estado), (2, 'PARCIAL'))
        # Y repetirlo ya no descuenta.
        self._consumir_viejo(pend, 3)
        pend.refresh_from_db()
        self.assertEqual(pend.cantidad_despachada, 2)


# ---------------------------------------------------------------------------
# 8. anular_factura_dte: traspaso PRE-recepción sin despacho vigente
# ---------------------------------------------------------------------------
class AnularTraspasoPreRecepcionSinDespachoTest(_Base):

    def setUp(self):
        super().setUp()
        self._sesion(self.origen)

    def _nc(self, dte, dp, cantidad):
        body = {
            'dte_id': dte.id, 'tipo_anulacion': 'ANULACION',
            'metodo_devolucion': 'NO_AFECTA_CAJA', 'motivo': 'test R2V3',
            'productos_afectados': [{'dte_producto_id': dp.id, 'cantidad': cantidad}],
            'return_json': True,
        }
        with _permisos():
            return self.client.post('/app/documentos/anular-factura/', data=json.dumps(body),
                                    content_type='application/json')

    def test_legacy_sin_traspaso_salida_no_acredita_origen(self):
        dte, dp = self._traspaso(cantidad=4, tipo_documento='FACTURA ELECTRONICA',
                                 estado='ACEPTADO', con_salida=False)
        stock0 = self._stock(self.t_origen)
        resp = self._nc(dte, dp, 2)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual([ln['dte_producto_id'] for ln in data['lineas_sin_reversa_stock']], [dp.id])
        self.assertEqual(self._stock(self.t_origen), stock0)
        nc = Dte.objects.get(id=data['nc_id'])
        self.assertFalse(Movimientos_Producto.objects.filter(dte=nc).exists())
        dp.refresh_from_db()
        self.assertEqual(dp.stock, 2)   # el documento sí se reduce

    def test_rechazado_con_stock_ya_devuelto_no_acredita_de_nuevo(self):
        dte, dp = self._traspaso(cantidad=4, tipo_documento='FACTURA ELECTRONICA',
                                 estado='RECHAZADO', estado_salida='CANCELADO')
        stock0 = self._stock(self.t_origen)
        resp = self._nc(dte, dp, 4)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(len(resp.json()['lineas_sin_reversa_stock']), 1)
        self.assertEqual(self._stock(self.t_origen), stock0)

    def test_con_despacho_vigente_sigue_acreditando(self):
        dte, dp = self._traspaso(cantidad=4, tipo_documento='FACTURA ELECTRONICA')
        stock0 = self._stock(self.t_origen)
        resp = self._nc(dte, dp, 2)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['lineas_sin_reversa_stock'], [])
        self.assertEqual(self._stock(self.t_origen), stock0 + 2)
        salida = Movimientos_Producto.objects.get(dte=dte, concepto='TRASPASO_SALIDA')
        self.assertEqual(salida.cantidad, -2)
