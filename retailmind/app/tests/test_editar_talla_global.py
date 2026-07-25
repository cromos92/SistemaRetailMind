"""
Tests de corrección de talla global (modal Crear Producto Manual).

Endpoint: POST /app/api/editar-talla-global/
Renombra una talla mal registrada (ej. "12" que debió ser "12K") en TODAS
las bodegas donde exista la misma identidad de producto (código + marca +
color + género + categoría), sin tocar los SKUs. Bodegas donde el producto
ya tiene la talla destino se saltan y se reportan como conflicto.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_editar_talla_global
"""
import json

from django.test import TestCase

from app.models import (
    AtributoOpcion, Categoria, Producto, Producto_Talla, Productos_Atributos,
)
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
)

URL = '/app/api/editar-talla-global/'


class TestEditarTallaGlobal(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = crear_usuario(username='corrector', rol='administrador')
        cls.empresa = crear_empresa()
        cls.suc_a = crear_sucursal(empresa=cls.empresa, alias='NICK1')
        cls.suc_b = crear_sucursal(empresa=cls.empresa, alias='EDEL')
        crear_empresa_user(cls.user, cls.empresa, cls.suc_a)

        cls.cat = Categoria.objects.create(nombre='Sandalias y Chalas')
        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_gen = Productos_Atributos.objects.create(nombre='Género', descripcion='Género')
        cls.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='ADIDAS')
        cls.aqua = AtributoOpcion.objects.create(atributo=attr_color, valor='AQUA')
        cls.negro = AtributoOpcion.objects.create(atributo=attr_color, valor='NEGRO')
        cls.hombre = AtributoOpcion.objects.create(atributo=attr_gen, valor='HOMBRE')

    def setUp(self):
        self.client.force_login(self.user)
        self.prod_a = self._crear_producto(self.suc_a)
        self.prod_b = self._crear_producto(self.suc_b)
        # Talla mal registrada "12" en ambas bodegas, con SKU propio por bodega
        self.pt_a = Producto_Talla.objects.create(producto=self.prod_a, talla='12', sku=4802770, stock=3)
        self.pt_b = Producto_Talla.objects.create(producto=self.prod_b, talla='12', sku=5100001, stock=7)

    def _crear_producto(self, sucursal, color=None):
        return Producto.objects.create(
            articulo='F35556', descripcion='SANDALIA ADILETTE AQUA',
            sucursal=sucursal, atributo1=self.marca,
            atributo2=color or self.aqua, atributo3=self.hombre,
            categoria=self.cat, costo=11330, sobreprecio=1473, precioventa=21990,
        )

    def _post(self, **kwargs):
        payload = {'producto_id': self.prod_a.id, 'talla_actual': '12', 'talla_nueva': '12K'}
        payload.update(kwargs)
        return self.client.post(URL, data=json.dumps(payload), content_type='application/json')

    def test_renombra_en_todas_las_bodegas_sin_tocar_sku(self):
        # Producto de OTRA empresa con la misma identidad: fuera del alcance
        otra = crear_empresa(nombre='Ajena', rut='77.000.000-1')
        suc_ajena = crear_sucursal(empresa=otra, alias='AJENA')
        prod_ajeno = self._crear_producto(suc_ajena)
        pt_ajeno = Producto_Talla.objects.create(producto=prod_ajeno, talla='12', sku=7100001, stock=1)

        resp = self._post()
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['filas_actualizadas'], 2)
        self.assertEqual({b['sucursal'] for b in data['bodegas']}, {'NICK1', 'EDEL'})
        self.assertEqual(data['conflictos'], [])

        self.pt_a.refresh_from_db()
        self.pt_b.refresh_from_db()
        pt_ajeno.refresh_from_db()
        self.assertEqual(self.pt_a.talla, '12K')
        self.assertEqual(self.pt_b.talla, '12K')
        self.assertEqual(self.pt_a.sku, 4802770)   # SKU intacto
        self.assertEqual(self.pt_b.sku, 5100001)
        self.assertEqual(pt_ajeno.talla, '12')     # otra empresa: no se toca

    def test_conflicto_en_una_bodega_no_bloquea_las_demas(self):
        # EDEL ya tiene la talla destino: se salta esa bodega y se reporta
        Producto_Talla.objects.create(producto=self.prod_b, talla='12K', sku=5100002, stock=1)
        resp = self._post()
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['filas_actualizadas'], 1)
        self.assertEqual(len(data['conflictos']), 1)
        self.assertEqual(data['conflictos'][0]['sucursal'], 'EDEL')

        self.pt_a.refresh_from_db()
        self.pt_b.refresh_from_db()
        self.assertEqual(self.pt_a.talla, '12K')
        self.assertEqual(self.pt_b.talla, '12')    # intacta por el conflicto

    def test_no_toca_otras_variantes_del_codigo(self):
        prod_negro = self._crear_producto(self.suc_a, color=self.negro)
        pt_negro = Producto_Talla.objects.create(producto=prod_negro, talla='12', sku=6100001, stock=2)
        resp = self._post()
        self.assertTrue(resp.json()['success'])
        pt_negro.refresh_from_db()
        self.assertEqual(pt_negro.talla, '12')     # otra variante (color): no se toca

    def test_talla_igual_es_error(self):
        resp = self._post(talla_nueva='12')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_faltan_datos_es_error(self):
        resp = self._post(talla_nueva='')
        self.assertEqual(resp.status_code, 400)

    def test_talla_inexistente_404(self):
        resp = self._post(talla_actual='99')
        self.assertEqual(resp.status_code, 404)

    def test_bodega_fuera_del_alcance_403(self):
        otra = crear_empresa(nombre='Ajena2', rut='78.000.000-2')
        suc_ajena = crear_sucursal(empresa=otra, alias='AJENA2')
        prod_ajeno = self._crear_producto(suc_ajena)
        Producto_Talla.objects.create(producto=prod_ajeno, talla='12', sku=8100001, stock=1)
        resp = self._post(producto_id=prod_ajeno.id)
        self.assertEqual(resp.status_code, 403)
