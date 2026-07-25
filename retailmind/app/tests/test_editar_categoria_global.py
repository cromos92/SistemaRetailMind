"""
Tests de corrección de categoría global (modal Crear Producto Manual).

Cubre:
  - POST /app/api/editar-categoria-global/ (api_editar_categoria_producto_global):
    replica el cambio a TODAS las bodegas de la misma variante, aunque cada
    ficha tenga una categoría (mal) distinta; SKUs/tallas no cambian.
  - verificar_producto_existente: cuando la ficha existe con OTRA categoría,
    devuelve `producto_categoria_distinta` (guardia anti-duplicado) en vez de
    tratarlo como producto nuevo.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_editar_categoria_global
"""
import json

from django.test import TestCase

from app.models import (
    AtributoOpcion, Categoria, Producto, Producto_Talla, Productos_Atributos,
)
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
)

URL = '/app/api/editar-categoria-global/'
URL_VERIFICAR = '/app/verificar_producto_existente/'


class BaseCategoriaGlobal(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = crear_usuario(username='corrector_cat', rol='administrador')
        cls.empresa = crear_empresa()
        cls.suc_a = crear_sucursal(empresa=cls.empresa, alias='NICK1')
        cls.suc_b = crear_sucursal(empresa=cls.empresa, alias='EDEL')
        crear_empresa_user(cls.user, cls.empresa, cls.suc_a)

        cls.cat_correcta = Categoria.objects.create(nombre='Sandalias y Chalas')
        cls.cat_mala_a = Categoria.objects.create(nombre='Zapatillas')
        cls.cat_mala_b = Categoria.objects.create(nombre='Botines')

        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_gen = Productos_Atributos.objects.create(nombre='Género', descripcion='Género')
        cls.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='ADIDAS')
        cls.aqua = AtributoOpcion.objects.create(atributo=attr_color, valor='AQUA')
        cls.negro = AtributoOpcion.objects.create(atributo=attr_color, valor='NEGRO')
        cls.hombre = AtributoOpcion.objects.create(atributo=attr_gen, valor='HOMBRE')

    def setUp(self):
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.suc_a.id
        sesion.save()
        # Misma variante en 2 bodegas, cada una con una categoría (mala) distinta
        self.prod_a = self._crear_producto(self.suc_a, self.cat_mala_a)
        self.prod_b = self._crear_producto(self.suc_b, self.cat_mala_b)
        self.pt_a = Producto_Talla.objects.create(producto=self.prod_a, talla='12', sku=4802770, stock=3)

    def _crear_producto(self, sucursal, categoria, color=None):
        return Producto.objects.create(
            articulo='F35556', descripcion='SANDALIA ADILETTE AQUA',
            sucursal=sucursal, atributo1=self.marca,
            atributo2=color or self.aqua, atributo3=self.hombre,
            categoria=categoria, costo=11330, sobreprecio=1473, precioventa=21990,
        )


class TestEditarCategoriaGlobal(BaseCategoriaGlobal):
    def _post(self, **kwargs):
        payload = {'producto_id': self.prod_a.id, 'categoria_id': self.cat_correcta.id}
        payload.update(kwargs)
        return self.client.post(URL, data=json.dumps(payload), content_type='application/json')

    def test_corrige_en_todas_las_bodegas_aunque_tengan_categorias_distintas(self):
        resp = self._post()
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['fichas_actualizadas'], 2)
        anteriores = {b['sucursal']: b['categoria_anterior'] for b in data['bodegas']}
        self.assertEqual(anteriores, {'NICK1': 'Zapatillas', 'EDEL': 'Botines'})

        self.prod_a.refresh_from_db()
        self.prod_b.refresh_from_db()
        self.assertEqual(self.prod_a.categoria_id, self.cat_correcta.id)
        self.assertEqual(self.prod_b.categoria_id, self.cat_correcta.id)
        # SKU/talla intactos
        self.pt_a.refresh_from_db()
        self.assertEqual(self.pt_a.sku, 4802770)
        self.assertEqual(self.pt_a.talla, '12')

    def test_no_toca_otras_variantes_del_codigo(self):
        prod_negro = self._crear_producto(self.suc_a, self.cat_mala_a, color=self.negro)
        self._post()
        prod_negro.refresh_from_db()
        self.assertEqual(prod_negro.categoria_id, self.cat_mala_a.id)

    def test_todas_ya_correctas_es_error(self):
        Producto.objects.filter(id__in=[self.prod_a.id, self.prod_b.id]).update(categoria=self.cat_correcta)
        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_faltan_datos_es_error(self):
        resp = self._post(categoria_id=None)
        self.assertEqual(resp.status_code, 400)

    def test_bodega_fuera_del_alcance_403(self):
        otra = crear_empresa(nombre='Ajena', rut='79.000.000-3')
        suc_ajena = crear_sucursal(empresa=otra, alias='AJENA')
        prod_ajeno = self._crear_producto(suc_ajena, self.cat_mala_a)
        resp = self._post(producto_id=prod_ajeno.id)
        self.assertEqual(resp.status_code, 403)


class TestVerificarCategoriaDistinta(BaseCategoriaGlobal):
    def _get(self, categoria_id):
        return self.client.get(URL_VERIFICAR, {
            'articulo': 'F35556',
            'marca': self.marca.id,
            'color': self.aqua.id,
            'genero': self.hombre.id,
            'categoria': categoria_id,
        })

    def test_categoria_distinta_devuelve_guardia_anti_duplicado(self):
        # El usuario elige la categoría CORRECTA; la ficha está con Zapatillas
        data = self._get(self.cat_correcta.id).json()
        self.assertFalse(data['existe'])
        pcd = data.get('producto_categoria_distinta')
        self.assertIsNotNone(pcd, data)
        self.assertEqual(pcd['id'], self.prod_a.id)
        self.assertEqual(pcd['categoria'], 'Zapatillas')
        self.assertEqual(pcd['categoria_form_id'], self.cat_correcta.id)
        self.assertEqual(pcd['categoria_form_nombre'], 'Sandalias y Chalas')
        self.assertEqual(pcd['n_tallas'], 1)

    def test_categoria_igual_matchea_normal(self):
        data = self._get(self.cat_mala_a.id).json()
        self.assertTrue(data['existe'])
        self.assertIsNone(data.get('producto_categoria_distinta'))
