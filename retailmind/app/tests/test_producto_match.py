"""
Tests de identidad/deduplicación de productos (bug: código duplicado al crear).

Cubre:
  - normalizar_articulo: canonización de código (mayúsculas/espacios/acentos).
  - buscar_producto_por_identidad: match por articulo normalizado + atributos.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_producto_match
"""
from datetime import datetime, timezone as dt_timezone

from django.test import TestCase

from app.models import AtributoOpcion, Categoria, Producto, Productos_Atributos
from app.tests.factories import crear_sucursal
from app.utils_producto_match import (
    buscar_producto_por_identidad, fichas_por_identidad, normalizar_articulo,
    variantes_mismo_codigo,
)


class TestNormalizarArticulo(TestCase):
    def test_none_y_vacio(self):
        self.assertEqual(normalizar_articulo(None), '')
        self.assertEqual(normalizar_articulo(''), '')
        self.assertEqual(normalizar_articulo('   '), '')

    def test_mayusculas_y_espacios_extremos(self):
        self.assertEqual(normalizar_articulo(' zap-001 '), 'ZAP-001')
        self.assertEqual(normalizar_articulo('Zap-001'), 'ZAP-001')
        self.assertEqual(normalizar_articulo('ZAP-001'), 'ZAP-001')

    def test_espacios_internos_colapsados(self):
        self.assertEqual(normalizar_articulo('MODELO   A'), 'MODELO A')
        self.assertEqual(normalizar_articulo('MODELO A'), 'MODELO A')

    def test_acentos(self):
        self.assertEqual(normalizar_articulo('Niño'), 'NINO')
        self.assertEqual(normalizar_articulo('CÓDIGO-Á'), 'CODIGO-A')

    def test_espacio_no_separable(self):
        # Caso real de datos migrados: articulo con \xa0 (non-breaking space).
        self.assertEqual(normalizar_articulo('\xa0ZAP-001'), 'ZAP-001')
        self.assertEqual(normalizar_articulo('ZAP-001'),
                         normalizar_articulo('\xa0ZAP-001 '))

    def test_equivalencias(self):
        variantes = [' zap-001 ', 'ZAP-001', 'Zap-001', 'zap-001']
        norms = {normalizar_articulo(v) for v in variantes}
        self.assertEqual(len(norms), 1)


class TestIdentidadProducto(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.suc = crear_sucursal(alias='BODEGA-T')
        cls.cat = Categoria.objects.create(nombre='Zapatillas')
        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_gen = Productos_Atributos.objects.create(nombre='Género', descripcion='Género')
        cls.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='SKECHERS')
        cls.rojo = AtributoOpcion.objects.create(atributo=attr_color, valor='ROJO')
        cls.azul = AtributoOpcion.objects.create(atributo=attr_color, valor='AZUL')
        cls.mujer = AtributoOpcion.objects.create(atributo=attr_gen, valor='MUJER')

        cls.prod = Producto.objects.create(
            articulo='ZAP-001', descripcion='Modelo Uno', sucursal=cls.suc,
            atributo1=cls.marca, atributo2=cls.rojo, atributo3=cls.mujer,
            categoria=cls.cat, costo=10000, sobreprecio=0, precioventa=20000,
        )

    def _buscar(self, articulo, color=None):
        return buscar_producto_por_identidad(
            articulo, self.marca.id, (color or self.rojo).id, self.mujer.id,
            self.cat.id, self.suc.id,
        )

    def test_match_exacto(self):
        self.assertEqual(self._buscar('ZAP-001'), self.prod)

    def test_match_con_tipeo_distinto(self):
        # El bug histórico: estos NO matcheaban y duplicaban el producto.
        self.assertEqual(self._buscar(' zap-001 '), self.prod)
        self.assertEqual(self._buscar('Zap-001'), self.prod)

    def test_color_distinto_no_matchea(self):
        # Otro color = otra variante = producto distinto (se crea nuevo).
        self.assertIsNone(self._buscar('ZAP-001', color=self.azul))

    def test_codigo_distinto_no_matchea(self):
        self.assertIsNone(self._buscar('ZAP-002'))

    def test_variantes_mismo_codigo(self):
        # Segunda variante (color azul) con \xa0 y otro tipeo del mismo código.
        Producto.objects.create(
            articulo='\xa0zap-001 ', descripcion='Modelo Uno', sucursal=self.suc,
            atributo1=self.marca, atributo2=self.azul, atributo3=self.mujer,
            categoria=self.cat, costo=10000, sobreprecio=0, precioventa=20000,
        )
        variantes = variantes_mismo_codigo('ZAP-001', self.suc.id)
        self.assertEqual(len(variantes), 2)
        colores = {v.atributo2.valor for v in variantes}
        self.assertEqual(colores, {'ROJO', 'AZUL'})

    def test_identidad_ignora_espacio_no_separable(self):
        # El match de creación debe reconocer el \xa0 como el mismo producto.
        self.assertEqual(self._buscar('\xa0ZAP-001 '), self.prod)


class TestFichasDuplicadas(TestCase):
    """Dos fichas con la MISMA identidad (duplicado legacy de la migración).

    Sin ORDER BY explícito el motor devolvía una u otra según el orden físico de
    la tabla, así que dos recepciones seguidas del mismo código caían en fichas
    distintas y partían el histórico/los SKUs del artículo (caso real F35542).
    """

    @classmethod
    def setUpTestData(cls):
        cls.suc = crear_sucursal(alias='BODEGA-DUP')
        cls.cat = Categoria.objects.create(nombre='Zapatillas')
        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_gen = Productos_Atributos.objects.create(nombre='Género', descripcion='Género')
        cls.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='ADIDAS')
        cls.azul = AtributoOpcion.objects.create(atributo=attr_color, valor='BLUE')
        cls.hombre = AtributoOpcion.objects.create(atributo=attr_gen, valor='HOMBRE')

        comun = dict(descripcion='ADILETTE', sucursal=cls.suc, atributo1=cls.marca,
                     atributo2=cls.azul, atributo3=cls.hombre, categoria=cls.cat,
                     costo=10000, sobreprecio=0, precioventa=26990)
        # La ficha ANTIGUA se crea con id MENOR para que ordenar por id no
        # pueda pasar por "más reciente" de casualidad.
        cls.antigua = Producto.objects.create(articulo='F35542', **comun)
        cls.reciente = Producto.objects.create(articulo=' f35542 ', **comun)
        # fecha_creacion es auto_now_add: se fija con UPDATE.
        Producto.objects.filter(pk=cls.antigua.pk).update(
            fecha_creacion=datetime(2021, 1, 18, tzinfo=dt_timezone.utc))
        Producto.objects.filter(pk=cls.reciente.pk).update(
            fecha_creacion=datetime(2023, 9, 20, tzinfo=dt_timezone.utc))

    def _fichas(self, articulo='F35542'):
        return fichas_por_identidad(articulo, self.marca.id, self.azul.id,
                                    self.hombre.id, self.cat.id, self.suc.id)

    def test_devuelve_las_dos_fichas(self):
        self.assertEqual(len(self._fichas()), 2)

    def test_orden_mas_reciente_primero(self):
        self.assertEqual([f.pk for f in self._fichas()],
                         [self.reciente.pk, self.antigua.pk])

    def test_match_es_estable_entre_llamadas(self):
        elegidas = {buscar_producto_por_identidad(
            'f35542  ', self.marca.id, self.azul.id, self.hombre.id,
            self.cat.id, self.suc.id).pk for _ in range(5)}
        self.assertEqual(elegidas, {self.reciente.pk})

    def test_fecha_creacion_nula_va_al_final(self):
        Producto.objects.filter(pk=self.antigua.pk).update(fecha_creacion=None)
        self.assertEqual([f.pk for f in self._fichas()],
                         [self.reciente.pk, self.antigua.pk])
