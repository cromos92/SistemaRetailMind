"""Tests del sync de portadas: las 4 estrategias de match SKU(tienda) ↔ articulo(RM).

Desde que AllConnected publica en las tiendas con clave compuesta
(``codigo||marca||color||genero||categoria``), las 3 estrategias originales
dejaban sin foto a todo producto nuevo. La 4ª toma el primer tramo.

Correr sin tocar producción (el .env apunta a prod):
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_sincronizar_fotos_ecommerce
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from app.models import CredencialesEcommerce, FotoPortadaArticulo
from app.services import realsport_imagenes_service as svc
from app.tests.factories import crear_empresa, crear_producto_con_talla, crear_sucursal


class SincronizarCredencialTest(TestCase):
    def setUp(self):
        cache.clear()
        self.empresa = crear_empresa(nombre='Nicol', rut='76.111.111-1')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='Centro')
        self.cred = CredencialesEcommerce.objects.create(
            codigo='realsport', nombre='Realsport', tipo='realsport',
            empresa=self.empresa, url_api='https://realsport.cl', api_key='k',
            activo=True,
        )
        crear_producto_con_talla(self.sucursal, articulo='NK-AV003-12', sku=4799588)
        crear_producto_con_talla(self.sucursal, articulo='ZM22-1552-90', sku=4760686)
        crear_producto_con_talla(self.sucursal, articulo='ADIBP06', sku=4813031)
        crear_producto_con_talla(self.sucursal, articulo='SP0040-101', sku=139521)

    def _sync(self, pagina):
        with mock.patch.object(svc, 'traer_catalogo_portadas', return_value=iter([pagina])):
            return svc.sincronizar_credencial(self.cred)

    def test_cuatro_estrategias_de_match(self):
        pagina = {
            'NK-AV003-12': 'https://cdn/exacto.webp',                    # 1. exacto
            ' zm22-1552-90 ': 'https://cdn/flexible.webp',               # 2. trim + upper
            '4813031': 'https://cdn/talla.webp',                         # 3. sku de talla
            'SP0040-101||PASSER||NEGRO||MUJER||CHALAS': 'https://cdn/compuesto.webp',  # 4. clave compuesta
            '139521||PASSER||NEGRO||MUJER||CHALAS': 'https://cdn/compuesto_talla.webp',  # 4. compuesta → talla
            '||UNDERARMON||BLUE||HOMBRE||Poleras': 'https://cdn/malformado.webp',  # tramo vacío
            'NOEXISTE': 'https://cdn/no.webp',
            'SINFOTO': '',
        }
        r = self._sync(pagina)

        self.assertEqual(r['procesados'], 8)
        self.assertEqual(r['match_exacto'], 1)
        self.assertEqual(r['match_flexible'], 1)
        self.assertEqual(r['match_por_talla'], 1)
        self.assertEqual(r['match_compuesto'], 2)
        self.assertEqual(r['con_foto'], 5)
        self.assertEqual(r['sin_match_local'], 2)  # NOEXISTE + malformado

        urls = dict(
            FotoPortadaArticulo.objects.filter(origen=self.cred)
            .values_list('articulo', 'url_foto')
        )
        self.assertEqual(urls['NK-AV003-12'], 'https://cdn/exacto.webp')
        self.assertEqual(urls['ZM22-1552-90'], 'https://cdn/flexible.webp')
        self.assertEqual(urls['ADIBP06'], 'https://cdn/talla.webp')
        self.assertIn(
            urls['SP0040-101'],
            ('https://cdn/compuesto.webp', 'https://cdn/compuesto_talla.webp'),
        )
        self.assertEqual(len(urls), 4)

        self.cred.refresh_from_db()
        self.assertIn('compuesto=2', self.cred.ultima_sync_resultado)
        self.assertIn('sin_match=2', self.cred.ultima_sync_resultado)
        self.assertFalse(self.cred.ultima_sync_resultado.startswith('ERROR'))

    def test_error_del_ecommerce_queda_registrado(self):
        def explota(*args, **kwargs):
            raise svc.RealsportImagenesError('HTTP 500 (page=1): boom')
            yield  # pragma: no cover — generador

        with mock.patch.object(svc, 'traer_catalogo_portadas', side_effect=explota):
            with self.assertRaises(svc.RealsportImagenesError):
                svc.sincronizar_credencial(self.cred)
        self.cred.refresh_from_db()
        self.assertTrue(self.cred.ultima_sync_resultado.startswith('ERROR'))
        self.assertIsNotNone(self.cred.ultima_sync_at)
