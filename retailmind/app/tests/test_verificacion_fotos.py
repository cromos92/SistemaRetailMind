"""Tests de la verificación de fotos de ecommerce.

Cubre:
  * limpiar_html (saneo de nombres/descripciones con HTML de CKEditor)
  * obtener_empresas_usuario (scope multi-empresa)
  * verificacion_fotos_service: cobertura, liveness (HTTP mockeado) y persistencia.

Correr:  python manage.py test app.tests.test_verificacion_fotos
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from app.models import CredencialesEcommerce, FotoPortadaArticulo
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)
from app.utils_texto import limpiar_html


class LimpiarHtmlTest(TestCase):
    def test_quita_tags_y_entidades(self):
        self.assertEqual(
            limpiar_html('<p>Zapato&nbsp;cuero &amp; gamuza</p>'),
            'Zapato cuero & gamuza',
        )

    def test_vacios(self):
        self.assertEqual(limpiar_html(None), '')
        self.assertEqual(limpiar_html(''), '')
        self.assertEqual(limpiar_html('  texto  '), 'texto')


class ObtenerEmpresasUsuarioTest(TestCase):
    def test_admin_ve_todas(self):
        from app.utils_permisos import obtener_empresas_usuario
        crear_empresa(nombre='E1', rut='76.111.111-1')
        crear_empresa(nombre='E2', rut='76.222.222-2')
        admin = crear_usuario(username='adm', rol='administrador')
        self.assertEqual(obtener_empresas_usuario(admin).count(), 2)

    def test_usuario_ve_solo_asignadas(self):
        from app.utils_permisos import obtener_empresas_usuario
        e1 = crear_empresa(nombre='E1', rut='76.111.111-1')
        crear_empresa(nombre='E2', rut='76.222.222-2')
        s1 = crear_sucursal(empresa=e1, alias='S1')
        user = crear_usuario(username='vend', rol='vendedor')
        crear_empresa_user(user, e1, s1)
        empresas = obtener_empresas_usuario(user)
        self.assertEqual(list(empresas.values_list('id', flat=True)), [e1.id])


class CoberturaTest(TestCase):
    def setUp(self):
        cache.clear()  # resolver_fotos_portada_bulk cachea por articulo
        self.empresa = crear_empresa(nombre='Calzados', rut='78.503.140-7')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='Centro')
        self.cred = CredencialesEcommerce.objects.create(
            codigo='paola', nombre='Paola', tipo='paola', empresa=self.empresa,
            url_api='https://calzadospaola.cl', api_key='k', activo=True,
        )

    def test_cobertura_cuenta_con_y_sin_foto(self):
        from app.services.verificacion_fotos_service import verificar_cobertura_credencial
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=111)
        crear_producto_con_talla(self.sucursal, articulo='ART2', sku=222)
        FotoPortadaArticulo.objects.create(
            articulo='ART1', url_foto='https://cdn/art1.webp', origen=self.cred,
        )
        cob = verificar_cobertura_credencial(self.cred)
        self.assertEqual(cob['articulos'], 2)
        self.assertEqual(cob['con_foto'], 1)
        self.assertEqual(cob['sin_foto'], 1)
        self.assertEqual(len(cob['por_sucursal']), 1)
        self.assertEqual(cob['por_sucursal'][0]['con_foto'], 1)


class LivenessYVerificarTest(TestCase):
    def setUp(self):
        cache.clear()
        self.empresa = crear_empresa(nombre='RealSport', rut='76.104.936-4')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='Local')
        self.cred = CredencialesEcommerce.objects.create(
            codigo='realsport', nombre='RealSport', tipo='realsport',
            empresa=self.empresa, url_api='https://realsport.cl', api_key='k',
            activo=True,
        )
        crear_producto_con_talla(self.sucursal, articulo='OK1', sku=1)
        crear_producto_con_talla(self.sucursal, articulo='DEAD1', sku=2)
        FotoPortadaArticulo.objects.create(
            articulo='OK1', url_foto='https://cdn/ok.webp', origen=self.cred,
        )
        FotoPortadaArticulo.objects.create(
            articulo='DEAD1', url_foto='https://cdn/dead.webp', origen=self.cred,
        )

    def _fake_check(self, url, session, timeout, auth_headers):
        if 'dead' in url:
            return ('http_404', 404, 'text/html')
        return ('ok', 200, 'image/webp')

    def test_verificar_credencial_clasifica_urls(self):
        from app.services import verificacion_fotos_service as svc
        with mock.patch.object(svc, '_check_url', side_effect=self._fake_check):
            resultado = svc.verificar_credencial(self.cred, workers=2)

        self.assertEqual(resultado['urls']['counters']['ok'], 1)
        self.assertEqual(resultado['urls']['counters']['http_404'], 1)
        muertas = resultado['urls']['muertas_ejemplos']
        self.assertEqual(len(muertas), 1)
        self.assertEqual(muertas[0]['articulo'], 'DEAD1')
        self.assertEqual(muertas[0]['motivo'], 'http_404')

    def test_solo_cobertura_omite_liveness(self):
        from app.services import verificacion_fotos_service as svc
        with mock.patch.object(svc, '_check_url') as m:
            resultado = svc.verificar_credencial(self.cred, solo_cobertura=True)
        m.assert_not_called()
        self.assertEqual(resultado['urls']['verificadas'], 0)
        self.assertEqual(resultado['cobertura']['con_foto'], 2)

    def test_persistir_resultado_guarda_campos(self):
        from app.services import verificacion_fotos_service as svc
        with mock.patch.object(svc, '_check_url', side_effect=self._fake_check):
            resultado = svc.verificar_credencial(self.cred, workers=2)
            svc.persistir_resultado(self.cred, resultado)

        self.cred.refresh_from_db()
        self.assertIsNotNone(self.cred.ultima_verif_at)
        self.assertIn('cobertura', self.cred.ultima_verif_resultado)
        self.assertIn('404', self.cred.ultima_verif_resultado)
        self.assertIn('DEAD1', self.cred.ultima_verif_detalle)
