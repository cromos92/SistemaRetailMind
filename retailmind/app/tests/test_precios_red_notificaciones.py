"""
Búsqueda "en toda la red" de la edición rápida de precios y avisos a las
tiendas (22-sep-2026).

Cubren:
1. `buscar_productos?alcance=red` encuentra un producto que sólo tiene ficha
   en OTRA sucursal (el modo histórico, por sucursal, no lo encuentra).
2. En modo red el mismo producto en N sucursales es UNA fila: ficha principal
   la de la sesión, stock/precio por sucursal, `precios_divergentes`, y
   `sucursales_notificar` (todas menos la de la sesión).
3. `actualizar_precio` sobre una ficha de otra sucursal (editada desde la
   sesión de EDEL) AVISA a esa sucursal aunque la ficha esté en 0 unidades.
4. `actualizar_precio` con sincronización avisa a la gemela sin stock y NO a
   la sucursal de la sesión.
5. `crear_producto_manual` sobre un producto existente con precio nuevo avisa
   a la gemela de otra bodega aunque tenga stock 0 (antes se la saltaba), y
   un cambio sólo de costo NO genera aviso.

Ejecutar (nunca contra el .env de producción):
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_precios_red_notificaciones
"""
import json
from unittest import mock

from django.test import Client, TestCase

from app.models import (
    AtributoOpcion, CambioPrecioPendiente, Categoria, Dte,
    NotificacionCambioPrecio, Productos_Atributos,
)
from .factories import (
    crear_correlativo, crear_empresa, crear_empresa_user,
    crear_producto_con_talla, crear_sucursal, crear_usuario,
)


def _patch_permisos():
    """El middleware/decorador consultan permisos que la BD de test no tiene."""
    return mock.patch(
        'app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True
    )


class BaseRed(TestCase):
    """Un holding con 3 sucursales; el usuario trabaja desde EDEL."""

    def setUp(self):
        self.empresa = crear_empresa()
        self.edel = crear_sucursal(self.empresa, alias='EDEL')
        self.nick1 = crear_sucursal(self.empresa, alias='NICK1')
        self.pao1 = crear_sucursal(self.empresa, alias='PAO1')

        self.user = crear_usuario(username='admin_edel', rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.edel)
        self.user_nick = crear_usuario(username='jefe_nick', rol='jefe_local')
        crear_empresa_user(self.user_nick, self.empresa, self.nick1)
        self.user_pao = crear_usuario(username='jefe_pao', rol='jefe_local')
        crear_empresa_user(self.user_pao, self.empresa, self.pao1)

        self.cat = Categoria.objects.create(nombre='Zapatillas')
        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_genero = Productos_Atributos.objects.create(nombre='Genero', descripcion='Genero')
        self.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='NIKE')
        self.color = AtributoOpcion.objects.create(atributo=attr_color, valor='NEGRO')
        self.genero = AtributoOpcion.objects.create(atributo=attr_genero, valor='HOMBRE')

        self.client = Client()
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.edel.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = 'EDEL'
        session['nombreUsuario'] = 'Tester'
        session.save()

    def _ficha(self, sucursal, articulo, precio, sku, stock=5, costo=10000):
        _, talla = crear_producto_con_talla(
            sucursal, articulo=articulo, sku=sku, stock=stock,
            precioventa=precio, costo=costo,
        )
        producto = talla.producto
        producto.categoria = self.cat
        producto.atributo1 = self.marca
        producto.atributo2 = self.color
        producto.atributo3 = self.genero
        producto.precioventa = precio
        producto.save()
        return producto

    def _buscar(self, **params):
        with _patch_permisos():
            resp = self.client.get('/app/gestion-precios/buscar/', params)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        return data


class BusquedaTodaLaRedTest(BaseRed):

    def test_red_encuentra_ficha_que_solo_existe_en_otra_sucursal(self):
        self._ficha(self.nick1, 'ZAP-SOLO-NICK', 25000, sku=5000001)

        # Modo histórico (una sucursal): no está en EDEL → no aparece
        data = self._buscar(search='ZAP-SOLO-NICK')
        self.assertEqual(data['productos'], [])

        # Toda la red: aparece, con la ficha de NICK1 como principal
        data = self._buscar(search='ZAP-SOLO-NICK', alcance='red')
        self.assertEqual(data['alcance'], 'red')
        self.assertEqual(len(data['productos']), 1)
        p = data['productos'][0]
        self.assertEqual(p['sucursal'], 'NICK1')
        self.assertFalse(p['es_sucursal_sesion'])
        self.assertEqual(p['sucursales_notificar'], ['NICK1'])
        self.assertFalse(p['precios_divergentes'])

    def test_red_agrupa_gemelas_en_una_fila(self):
        f_edel = self._ficha(self.edel, 'ZAP-DUO', 20000, sku=5000010, stock=4)
        self._ficha(self.nick1, 'ZAP-DUO', 25000, sku=5000011, stock=6)
        self._ficha(self.pao1, 'ZAP-DUO', 20000, sku=5000012, stock=0)

        data = self._buscar(search='ZAP-DUO', alcance='red')
        self.assertEqual(len(data['productos']), 1, data['productos'])
        p = data['productos'][0]
        # Ficha principal: la de la sucursal de la sesión
        self.assertEqual(p['id'], f_edel.id)
        self.assertTrue(p['es_sucursal_sesion'])
        self.assertEqual(p['stock'], 4)
        self.assertEqual(p['stock_total_red'], 10)
        self.assertEqual(p['fichas_red'], 3)
        self.assertTrue(p['precios_divergentes'])
        self.assertEqual(p['precio_min_red'], 20000)
        self.assertEqual(p['precio_max_red'], 25000)
        # Se avisa a todas menos a la de la sesión
        self.assertEqual(sorted(p['sucursales_notificar']), ['NICK1', 'PAO1'])
        detalle = {s['alias']: s for s in p['sucursales_detalle']}
        self.assertEqual(detalle['NICK1']['precio'], 25000)
        self.assertEqual(detalle['NICK1']['stock'], 6)
        self.assertTrue(detalle['EDEL']['es_actual'])

    def test_modo_sucursal_sigue_siendo_una_ficha_por_sucursal(self):
        self._ficha(self.edel, 'ZAP-DUO', 20000, sku=5000020)
        self._ficha(self.nick1, 'ZAP-DUO', 25000, sku=5000021)

        data = self._buscar(search='ZAP-DUO')
        self.assertEqual(len(data['productos']), 1)
        p = data['productos'][0]
        self.assertEqual(p['sucursal'], 'EDEL')
        self.assertEqual(p['sucursales_lista'], ['NICK1'])
        self.assertEqual(p['sucursales_notificar'], ['NICK1'])

    def test_coincidencia_exacta_de_codigo_va_primero(self):
        self._ficha(self.edel, 'F35556-1', 20000, sku=5000030)
        self._ficha(self.edel, 'F35556', 20000, sku=5000031)
        self._ficha(self.edel, 'AF35556', 20000, sku=5000032)

        data = self._buscar(search='F35556', alcance='red')
        self.assertEqual(data['productos'][0]['nombre'], 'F35556')


class ActualizarPrecioAvisaTest(BaseRed):
    URL = '/app/gestion-precios/actualizar-precio/'

    def _post(self, producto_id, precio, sync=True):
        with _patch_permisos():
            resp = self.client.post(
                self.URL,
                data=json.dumps({'producto_id': producto_id, 'nuevo_precio': precio,
                                 'sincronizar_sucursales': sync}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        return data

    def test_ficha_de_otra_sucursal_avisa_a_esa_sucursal_aunque_este_en_cero(self):
        f_nick = self._ficha(self.nick1, 'ZAP-SOLO-NICK', 25000, sku=5001001, stock=0)

        data = self._post(f_nick.id, 22990)

        f_nick.refresh_from_db()
        self.assertEqual(f_nick.precioventa, 22990)
        self.assertTrue(data['ficha_de_otra_sucursal'])
        self.assertEqual(data['sucursal_ficha'], 'NICK1')
        self.assertEqual(data['sucursales_notificadas_lista'], ['NICK1'])
        self.assertEqual(data['notificaciones_creadas'], 1)

        cambio = CambioPrecioPendiente.objects.get(sucursal=self.nick1)
        self.assertEqual(cambio.estado, 'APLICADO')
        self.assertEqual((cambio.precio_anterior, cambio.precio_nuevo), (25000, 22990))
        notif = NotificacionCambioPrecio.objects.get(usuario=self.user_nick)
        self.assertEqual(notif.cambio_precio_id, cambio.id)
        self.assertIn('desde EDEL', notif.mensaje)
        self.assertIn('sin stock', notif.mensaje)

    def test_sync_avisa_a_gemela_sin_stock_y_no_a_la_sesion(self):
        f_edel = self._ficha(self.edel, 'ZAP-DUO', 20000, sku=5001010, stock=4)
        f_pao = self._ficha(self.pao1, 'ZAP-DUO', 20000, sku=5001011, stock=0)

        data = self._post(f_edel.id, 17990)

        f_pao.refresh_from_db()
        self.assertEqual(f_pao.precioventa, 17990)
        self.assertFalse(data['ficha_de_otra_sucursal'])
        self.assertEqual(data['productos_sincronizados'], 1)
        self.assertEqual(data['sucursales_notificadas_lista'], ['PAO1'])
        self.assertEqual(data['notificaciones_creadas'], 1)
        self.assertTrue(NotificacionCambioPrecio.objects.filter(usuario=self.user_pao).exists())
        # Quien edita desde EDEL no se avisa a sí mismo
        self.assertFalse(NotificacionCambioPrecio.objects.filter(usuario=self.user).exists())
        self.assertFalse(CambioPrecioPendiente.objects.filter(sucursal=self.edel).exists())

    def test_gemela_con_el_mismo_precio_no_recibe_aviso(self):
        f_edel = self._ficha(self.edel, 'ZAP-DUO', 20000, sku=5001020)
        self._ficha(self.pao1, 'ZAP-DUO', 17990, sku=5001021)

        data = self._post(f_edel.id, 17990)
        self.assertEqual(data['productos_sincronizados'], 0)
        self.assertEqual(data['notificaciones_creadas'], 0)


class CrearManualAvisaTest(BaseRed):
    URL = '/app/crear_producto_manual/'

    def setUp(self):
        super().setUp()
        self.proveedor = crear_empresa(nombre='Proveedor Test', rut='77.111.111-1')
        crear_correlativo(self.edel, tipo_dte='COMPRA')
        self.dte = Dte.objects.create(
            emisor=self.proveedor, receptor=self.empresa,
            numero_documento=555, tipo_documento='FACTURA',
            monto_neto=10000, monto_con_iva=11900,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='tester', fecha_emision='2026-07-01',
            fecha_vencimiento='2026-07-30', diasCredito=30,
            bultos=1, unidades_productos=3,
            tipo_transaccion='COMPRA', sucursal=self.edel,
        )

    def _post(self, articulo, precioventa, costo='10000', sobreprecio='0', **extra):
        payload = {
            'es_manual': 'true',
            'proveedor': self.proveedor.id,
            'dte_manual': self.dte.id,
            'articulo': articulo,
            'atributo1': self.marca.id,
            'atributo2': self.color.id,
            'atributo3': self.genero.id,
            'categoria': self.cat.id,
            'tipo_talla': 'CL',
            'costo': str(costo),
            'sobreprecio': str(sobreprecio),
            'precioventa': str(precioventa),
            'talla[]': ['42'],
            'stock[]': ['2'],
            'sku[]': [''],
        }
        payload.update(extra)
        with _patch_permisos():
            resp = self.client.post(self.URL, payload)
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data['success'], data)
        return data

    def test_precio_nuevo_avisa_a_gemela_sin_stock(self):
        self._ficha(self.edel, 'ZAP-DUO', 20000, sku=5002001, stock=3)
        f_nick = self._ficha(self.nick1, 'ZAP-DUO', 20000, sku=5002002, stock=0)

        data = self._post('ZAP-DUO', precioventa=24990, actualizar_precios='true')

        f_nick.refresh_from_db()
        self.assertEqual(int(f_nick.precioventa), 24990)
        self.assertEqual(data['productos_sincronizados'], 1)
        self.assertEqual(data['notificaciones_creadas'], 1)
        det = {d['sucursal']: d for d in data['sync_detalle']}
        self.assertTrue(det['NICK1']['notificado'])
        self.assertEqual(det['NICK1']['precio_anterior'], 20000)
        self.assertEqual(det['NICK1']['precio_nuevo'], 24990)
        self.assertIn('Aviso de precio enviado a NICK1', data['mensaje'])
        # El "precio anterior" del mensaje es el de la ficha LOCAL (antes lo
        # pisaba el loop de sincronización con el de la última gemela)
        self.assertIn('$20,000 → $24,990', data['mensaje'])

        notif = NotificacionCambioPrecio.objects.get(usuario=self.user_nick)
        self.assertIn('creación manual', notif.mensaje)
        self.assertIn('sin stock', notif.mensaje)
        cambio = CambioPrecioPendiente.objects.get(sucursal=self.nick1)
        self.assertEqual(cambio.estado, 'APLICADO')

    def test_producto_nuevo_local_con_gemela_en_otra_bodega_avisa(self):
        # No existe en EDEL; sí en PAO1 a otro precio → se crea en EDEL y PAO1
        # recibe el precio nuevo + aviso.
        f_pao = self._ficha(self.pao1, 'ZAP-NUEVO-EDEL', 30000, sku=5002010, stock=2)

        data = self._post('ZAP-NUEVO-EDEL', precioventa=27990)

        f_pao.refresh_from_db()
        self.assertEqual(int(f_pao.precioventa), 27990)
        self.assertEqual(data['notificaciones_creadas'], 1)
        self.assertTrue(NotificacionCambioPrecio.objects.filter(usuario=self.user_pao).exists())

    def test_solo_cambio_de_costo_sincroniza_sin_avisar(self):
        self._ficha(self.edel, 'ZAP-DUO', 20000, sku=5002020, stock=3, costo=9000)
        f_nick = self._ficha(self.nick1, 'ZAP-DUO', 20000, sku=5002021, stock=1, costo=9000)

        data = self._post('ZAP-DUO', precioventa=20000, costo='11000', actualizar_precios='true')

        f_nick.refresh_from_db()
        self.assertEqual(int(f_nick.costo), 11000)
        self.assertEqual(data['productos_sincronizados'], 1)
        self.assertEqual(data['notificaciones_creadas'], 0)
        self.assertFalse(NotificacionCambioPrecio.objects.exists())
        det = data['sync_detalle'][0]
        self.assertFalse(det['notificado'])
        self.assertFalse(det['venta_cambio'])
