"""
R2FG / A3-02 — la trazabilidad respeta la copia EXACTA del SKU que se pinchó.

El dashboard de productos enlaza `/app/trazabilidad-producto/?sku=X&pt=<id>`.
Con el SKU repetido en varias bodegas (99,8 % de los SKU con stock), la API
elegía siempre la copia de la sucursal de la sesión: el botón abría el kardex
de OTRA bodega. Ahora `pt` manda antes del fallback por sesión, sin ampliar el
alcance (la búsqueda sigue acotada por SKU y por las bodegas del usuario).

Ejecutar en la base aislada (NO producción):
    DATABASE_URL=postgres://postgres:admin@localhost:5432/retail_r2fg \
        python manage.py test app.tests.test_r2fg_trazabilidad_pt --keepdb
"""
import json

from django.test import Client, RequestFactory, TestCase

from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)
from app.views_modulo_existencias_nuevo import api_trazabilidad_producto

SKU = 7788001


class TrazabilidadPorPtTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Retail PT', rut='76.555.555-5')
        cls.suc_a = crear_sucursal(empresa=cls.empresa, alias='EDEL-PT')
        cls.suc_b = crear_sucursal(empresa=cls.empresa, alias='PAO3-PT')
        cls.user = crear_usuario(username='traza-pt', rol='administrador')
        crear_empresa_user(cls.user, cls.empresa, cls.suc_a)
        # Mismo SKU en dos bodegas de la empresa del usuario, con stock distinto.
        _, cls.pt_a = crear_producto_con_talla(cls.suc_a, articulo='VISA PT', talla='40',
                                               sku=SKU, stock=0)
        _, cls.pt_b = crear_producto_con_talla(cls.suc_b, articulo='VISA PT', talla='40',
                                               sku=SKU, stock=9955)
        # Otra talla (otro SKU) dentro del alcance.
        _, cls.pt_otro_sku = crear_producto_con_talla(cls.suc_b, articulo='OTRO PT', talla='41',
                                                      sku=SKU + 1, stock=3)
        # Mismo SKU en una empresa AJENA (fuera del alcance del usuario).
        cls.empresa_x = crear_empresa(nombre='Ajena PT', rut='76.666.666-6')
        cls.suc_x = crear_sucursal(empresa=cls.empresa_x, alias='AJENA-PT')
        _, cls.pt_ajeno = crear_producto_con_talla(cls.suc_x, articulo='VISA PT', talla='40',
                                                   sku=SKU, stock=77)
        # SKU único (una sola copia).
        _, cls.pt_unico = crear_producto_con_talla(cls.suc_a, articulo='UNICO PT', talla='42',
                                                   sku=SKU + 2, stock=5)

    def _consultar(self, params, sucursal_id=None):
        req = RequestFactory().get('/app/api/trazabilidad-producto/', params)
        req.user = self.user
        req.session = {'idSucursalActual': sucursal_id or self.suc_a.id}
        resp = api_trazabilidad_producto(req)
        return resp.status_code, json.loads(resp.content)

    def test_sin_pt_mantiene_la_copia_de_la_sesion(self):
        status, data = self._consultar({'sku': str(SKU)})
        self.assertEqual(status, 200, data)
        p = data['producto']
        self.assertEqual(p['sucursal'], 'EDEL-PT')
        self.assertEqual(p['stock_actual'], 0)
        self.assertEqual(p['producto_talla_id'], self.pt_a.id)
        self.assertEqual(p['seleccion'], 'sesion')
        self.assertTrue(p['sku_duplicado'])
        self.assertEqual(p['sku_ocurrencias'], 2)   # la copia ajena no cuenta

    def test_pt_de_la_otra_bodega_abre_esa_copia(self):
        status, data = self._consultar({'sku': str(SKU), 'pt': str(self.pt_b.id)})
        self.assertEqual(status, 200, data)
        p = data['producto']
        self.assertEqual(p['sucursal'], 'PAO3-PT')
        self.assertEqual(p['stock_actual'], 9955)
        self.assertEqual(p['producto_talla_id'], self.pt_b.id)
        self.assertEqual(p['seleccion'], 'pt')
        # El kardex/lotes que se devuelven son los de ESA copia
        self.assertEqual(data['movimientos_meta'].get('stock_actual', 9955), 9955)

    def test_pt_de_otra_empresa_no_amplia_el_alcance(self):
        status, data = self._consultar({'sku': str(SKU), 'pt': str(self.pt_ajeno.id)})
        self.assertEqual(status, 200, data)
        p = data['producto']
        self.assertNotEqual(p['producto_talla_id'], self.pt_ajeno.id)
        self.assertNotEqual(p['stock_actual'], 77)
        self.assertEqual(p['sucursal'], 'EDEL-PT')
        self.assertEqual(p['seleccion'], 'sesion')

    def test_pt_de_otro_sku_se_ignora(self):
        status, data = self._consultar({'sku': str(SKU), 'pt': str(self.pt_otro_sku.id)})
        self.assertEqual(status, 200, data)
        self.assertEqual(data['producto']['producto_talla_id'], self.pt_a.id)

    def test_pt_invalido_no_revienta(self):
        for valor in ('abc', '-1', '1 OR 1', '²', '9' * 40, ''):
            with self.subTest(pt=valor):
                status, data = self._consultar({'sku': str(SKU), 'pt': valor})
                self.assertEqual(status, 200, data)
                self.assertEqual(data['producto']['producto_talla_id'], self.pt_a.id)

    def test_sin_sesion_valida_cae_a_una_copia_del_alcance(self):
        status, data = self._consultar({'sku': str(SKU)}, sucursal_id=self.suc_x.id)
        self.assertEqual(status, 200, data)
        p = data['producto']
        self.assertIn(p['producto_talla_id'], (self.pt_a.id, self.pt_b.id))
        self.assertEqual(p['seleccion'], 'primera')

    def test_sku_unico(self):
        status, data = self._consultar({'sku': str(SKU + 2), 'pt': str(self.pt_unico.id)})
        self.assertEqual(status, 200, data)
        self.assertFalse(data['producto']['sku_duplicado'])
        self.assertEqual(data['producto']['seleccion'], 'pt')
        status, data = self._consultar({'sku': str(SKU + 2)})
        self.assertEqual(data['producto']['seleccion'], 'unico')


class TrazabilidadPantallaPtTest(TestCase):
    """La pantalla lee `pt` del deep-link y el endpoint real (middleware +
    decoradores) lo respeta."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Retail PT2', rut='76.777.777-7')
        cls.suc_a = crear_sucursal(empresa=cls.empresa, alias='A-PT2')
        cls.suc_b = crear_sucursal(empresa=cls.empresa, alias='B-PT2')
        cls.maestro = crear_usuario(username='maestro-pt2', rol='maestro')
        crear_empresa_user(cls.maestro, cls.empresa, cls.suc_a)
        _, cls.pt_a = crear_producto_con_talla(cls.suc_a, articulo='PT2', talla='40', sku=SKU + 10, stock=1)
        _, cls.pt_b = crear_producto_con_talla(cls.suc_b, articulo='PT2', talla='40', sku=SKU + 10, stock=2)

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.maestro)
        s = self.c.session
        s['idSucursalActual'] = self.suc_a.id
        s['idEmpresaActual'] = self.empresa.id
        s['alias'] = self.suc_a.alias
        s.save()

    def test_pantalla_lee_pt_del_deep_link(self):
        r = self.c.get(f'/app/trazabilidad-producto/?sku={SKU + 10}&pt={self.pt_b.id}')
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn("params.get('pt')", html)
        self.assertIn('_ptEnlazado', html)
        self.assertIn('&pt=', html)

    def test_endpoint_real_respeta_pt(self):
        r = self.c.get('/app/api/trazabilidad-producto/', {'sku': SKU + 10, 'pt': self.pt_b.id},
                       HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()['producto']['stock_actual'], 2)
        self.assertEqual(r.json()['producto']['sucursal'], 'B-PT2')
