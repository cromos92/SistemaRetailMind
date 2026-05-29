"""
Tests para el endpoint externo GET /api/skus/ (SkusPorEmpresaView) y para el
resolver de portadas en bloque que lo hace rápido.

Foco de las optimizaciones cubiertas:
  1. Bulk de fotos de portada: resolver_fotos_portada_bulk() resuelve TODAS las
     portadas en 1-2 queries en vez de 1-2 por producto (eliminación del N+1).
  2. Anti-stampede: cuando expira la caché fresca, solo un worker recalcula y
     el resto sirve la copia "stale".
"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from app.models import CredencialesEcommerce, FotoPortadaArticulo
from app.services.realsport_imagenes_service import resolver_fotos_portada_bulk
from .factories import crear_empresa, crear_sucursal, crear_producto_con_talla

API_KEY = 'test-api-key-skus'
STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


def _crear_credencial(empresa, codigo, prioridad=0, activo=True):
    return CredencialesEcommerce.objects.create(
        codigo=codigo,
        nombre=f'Ecommerce {codigo}',
        tipo='otro',
        empresa=empresa,
        url_api='https://example.test',
        api_key='x',
        prioridad=prioridad,
        activo=activo,
    )


def _crear_foto(articulo, origen, url, es_principal=True):
    return FotoPortadaArticulo.objects.create(
        articulo=articulo, origen=origen, url_foto=url, es_principal=es_principal,
    )


@override_settings(
    RETAILMIND_API_KEY=API_KEY,
    STATICFILES_STORAGE=STATICFILES_STORAGE_TEST,
)
class SkusExternaEndpointTest(TestCase):
    def setUp(self):
        cache.clear()  # evita bleed de la caché LocMem entre tests
        self.empresa = crear_empresa(rut='76.111.222-3')
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.url = reverse('external-skus')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {API_KEY}'}

    def _get(self, rut=None):
        return self.client.get(
            self.url, {'rut_empresa': rut or self.empresa.rut}, **self.auth,
        )

    # ── Estructura básica ──────────────────────────────────────────────

    def test_responde_200_y_agrupa_por_producto(self):
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=5001, talla='40')
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=5002, talla='41')
        crear_producto_con_talla(self.sucursal, articulo='ART2', sku=5003, talla='42')

        # OJO: cada crear_producto_con_talla crea un Producto distinto. ART1 sale
        # dos veces como producto separado salvo que compartan identidad. Aquí
        # validamos que la respuesta es 200 y trae los SKUs creados.
        resp = self._get()
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body['success'])
        skus = {t['sku'] for p in body['data'] for t in p['tallas']}
        self.assertEqual(skus, {'5001', '5002', '5003'})

    def test_rut_obligatorio(self):
        resp = self.client.get(self.url, **self.auth)
        self.assertEqual(resp.status_code, 400)

    def test_sin_api_key_rechaza(self):
        resp = self.client.get(self.url, {'rut_empresa': self.empresa.rut})
        self.assertIn(resp.status_code, (401, 403))

    # ── Fotos de portada en la respuesta ───────────────────────────────

    def test_foto_portada_en_respuesta(self):
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=5001, talla='40')
        cred = _crear_credencial(self.empresa, 'ec-prop')
        _crear_foto('ART1', cred, 'https://cdn.test/art1.webp')

        resp = self._get()
        self.assertEqual(resp.status_code, 200, resp.content)
        prod = next(p for p in resp.json()['data'] if p['articulo'] == 'ART1')
        self.assertEqual(prod['foto_portada_url'], 'https://cdn.test/art1.webp')

    def test_producto_sin_foto_devuelve_string_vacio(self):
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=5001, talla='40')
        resp = self._get()
        prod = next(p for p in resp.json()['data'] if p['articulo'] == 'ART1')
        self.assertEqual(prod['foto_portada_url'], '')

    # ── El punto central: SIN N+1 en las portadas ──────────────────────

    def test_numero_de_queries_constante_sin_importar_cantidad_productos(self):
        """Con bulk, las queries NO crecen con el número de productos.

        Antes (N+1) cada producto disparaba 1-2 queries de foto. Ahora son
        2 queries totales (catálogo + portadas) sin importar cuántos productos.
        """
        # Empresa A: 2 productos, cada uno con foto de su propia empresa.
        cred_a = _crear_credencial(self.empresa, 'ec-a')
        for i in range(2):
            art = f'A{i}'
            crear_producto_con_talla(self.sucursal, articulo=art, sku=6000 + i, talla='40')
            _crear_foto(art, cred_a, f'https://cdn.test/{art}.webp')

        # Empresa B: 6 productos, mismo patrón.
        empresa_b = crear_empresa(nombre='Empresa B', rut='77.333.444-5')
        suc_b = crear_sucursal(empresa=empresa_b, alias='SUC-B')
        cred_b = _crear_credencial(empresa_b, 'ec-b')
        for i in range(6):
            art = f'B{i}'
            crear_producto_con_talla(suc_b, articulo=art, sku=7000 + i, talla='40')
            _crear_foto(art, cred_b, f'https://cdn.test/{art}.webp')

        cache.clear()
        with CaptureQueriesContext(connection) as q_a:
            self._get(rut=self.empresa.rut)
        n_a = len(q_a)

        cache.clear()
        with CaptureQueriesContext(connection) as q_b:
            self._get(rut=empresa_b.rut)
        n_b = len(q_b)

        # Mismo número de queries pese a 2 vs 6 productos → no hay N+1.
        self.assertEqual(n_a, n_b, f'A={n_a} B={n_b} queries (debería ser constante)')
        # Y debe ser un número chico (catálogo + portadas), no proporcional.
        self.assertLessEqual(n_a, 3, f'demasiadas queries: {n_a}')

    # ── Caché de respuesta ─────────────────────────────────────────────

    def test_segunda_llamada_es_cache_hit_sin_queries(self):
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=5001, talla='40')
        primera = self._get()
        self.assertEqual(primera.status_code, 200)

        with CaptureQueriesContext(connection) as q:
            segunda = self._get()
        self.assertEqual(segunda.status_code, 200)
        self.assertEqual(len(q), 0, 'la segunda llamada debería salir de caché sin queries')
        self.assertEqual(segunda.json(), primera.json())

    # ── Anti-stampede / stale ──────────────────────────────────────────

    def test_sirve_stale_cuando_otro_worker_tiene_el_lock(self):
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=5001, talla='40')
        # Primera llamada: calcula y deja copia fresca + stale.
        primera = self._get()
        self.assertEqual(primera.status_code, 200)

        rut = self.empresa.rut
        cache_key = f'external_skus_v1:{rut}'
        # Simulamos: la caché fresca expiró y otro worker está recalculando
        # (tiene el lock tomado).
        cache.delete(cache_key)
        cache.add(f'{cache_key}:lock', '1', timeout=120)

        with CaptureQueriesContext(connection) as q:
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(q), 0, 'debería servir stale sin recalcular (0 queries)')
        self.assertEqual(resp.json(), primera.json())


@override_settings(STATICFILES_STORAGE=STATICFILES_STORAGE_TEST)
class ResolverFotosBulkTest(TestCase):
    """Tests directos del resolver en bloque."""

    def setUp(self):
        cache.clear()
        self.empresa = crear_empresa(rut='76.555.666-7')

    def test_preferencia_empresa_propia_sobre_otra_con_mas_prioridad(self):
        # La empresa propia gana aunque otra tenga mayor prioridad.
        cred_propia = _crear_credencial(self.empresa, 'ec-propia', prioridad=1)
        otra = crear_empresa(nombre='Otra', rut='77.000.111-2')
        cred_ajena = _crear_credencial(otra, 'ec-ajena', prioridad=99)
        _crear_foto('ART1', cred_propia, 'https://cdn.test/propia.webp')
        _crear_foto('ART1', cred_ajena, 'https://cdn.test/ajena.webp')

        res = resolver_fotos_portada_bulk(['ART1'], self.empresa.id)
        self.assertEqual(res['ART1'], 'https://cdn.test/propia.webp')

    def test_fallback_mayor_prioridad_cuando_no_hay_de_empresa_propia(self):
        otra = crear_empresa(nombre='Otra', rut='77.000.111-2')
        cred_baja = _crear_credencial(otra, 'ec-baja', prioridad=1)
        cred_alta = _crear_credencial(otra, 'ec-alta', prioridad=50)
        _crear_foto('ART1', cred_baja, 'https://cdn.test/baja.webp')
        _crear_foto('ART1', cred_alta, 'https://cdn.test/alta.webp')

        res = resolver_fotos_portada_bulk(['ART1'], self.empresa.id)
        self.assertEqual(res['ART1'], 'https://cdn.test/alta.webp')

    def test_articulo_sin_foto_da_string_vacio(self):
        res = resolver_fotos_portada_bulk(['NOEXISTE'], self.empresa.id)
        self.assertEqual(res['NOEXISTE'], '')

    def test_credencial_inactiva_se_ignora(self):
        cred = _crear_credencial(self.empresa, 'ec-off', activo=False)
        _crear_foto('ART1', cred, 'https://cdn.test/off.webp')
        res = resolver_fotos_portada_bulk(['ART1'], self.empresa.id)
        self.assertEqual(res['ART1'], '')

    def test_resultado_queda_cacheado_sin_segunda_query(self):
        cred = _crear_credencial(self.empresa, 'ec-cache')
        _crear_foto('ART1', cred, 'https://cdn.test/art1.webp')

        resolver_fotos_portada_bulk(['ART1'], self.empresa.id)  # calienta caché
        with CaptureQueriesContext(connection) as q:
            res = resolver_fotos_portada_bulk(['ART1'], self.empresa.id)
        self.assertEqual(res['ART1'], 'https://cdn.test/art1.webp')
        self.assertEqual(len(q), 0, 'segunda resolución debería salir de caché')

    def test_lista_vacia_no_rompe(self):
        self.assertEqual(resolver_fotos_portada_bulk([], self.empresa.id), {})
        self.assertEqual(resolver_fotos_portada_bulk([None, ''], self.empresa.id), {})
