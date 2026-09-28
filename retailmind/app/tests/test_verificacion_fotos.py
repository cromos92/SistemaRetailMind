"""Tests de la verificación de fotos de ecommerce.

Cubre:
  * limpiar_html (saneo de nombres/descripciones con HTML de CKEditor)
  * obtener_empresas_usuario (scope multi-empresa)
  * verificacion_fotos_service: cobertura, liveness (HTTP mockeado) y persistencia.

Correr:  python manage.py test app.tests.test_verificacion_fotos
"""
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

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


class SniffImagenTest(SimpleTestCase):
    """El CDN de Spaces sirve los WebP de imagekit como ``application/octet-stream``
    (27 de 30 URLs de realsport en producción): la imagen se reconoce por sus
    primeros bytes, no por el Content-Type."""

    def test_reconoce_formatos_por_magic_bytes(self):
        from app.services.verificacion_fotos_service import _sniff_imagen
        self.assertEqual(_sniff_imagen(b'RIFF\x10\x00\x00\x00WEBPVP8 '), 'image/webp')
        self.assertEqual(_sniff_imagen(b'\xff\xd8\xff\xe0\x00\x10JFIF'), 'image/jpeg')
        self.assertEqual(_sniff_imagen(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'), 'image/png')
        self.assertEqual(_sniff_imagen(b'GIF89a\x01\x00'), 'image/gif')

    def test_lo_que_no_es_imagen_devuelve_vacio(self):
        from app.services.verificacion_fotos_service import _sniff_imagen
        self.assertEqual(_sniff_imagen(b'<!DOCTYPE html><html>'), '')
        self.assertEqual(_sniff_imagen(b'<?xml version="1.0"?><Error>'), '')
        self.assertEqual(_sniff_imagen(b''), '')

    def _sesion(self, head_status, head_ct, get_status=None, get_ct=None, cuerpo=b''):
        session = mock.Mock()
        head = mock.Mock(status_code=head_status, headers={'Content-Type': head_ct})
        session.head.return_value = head
        get = mock.Mock(
            status_code=head_status if get_status is None else get_status,
            headers={'Content-Type': head_ct if get_ct is None else get_ct},
        )
        get.iter_content.return_value = iter([cuerpo])
        session.get.return_value = get
        return session

    def test_octet_stream_con_magic_webp_es_ok(self):
        from app.services import verificacion_fotos_service as svc
        session = self._sesion(
            200, 'application/octet-stream', 206, 'application/octet-stream',
            b'RIFF\x10\x00\x00\x00WEBPVP8 ',
        )
        self.assertEqual(
            svc._check_url('https://cdn/x.webp', session, 5, None),
            ('ok', 206, 'image/webp'),
        )
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.kwargs['headers'], {'Range': 'bytes=0-15'})

    def test_content_type_image_no_necesita_get(self):
        from app.services import verificacion_fotos_service as svc
        session = self._sesion(200, 'image/webp')
        self.assertEqual(
            svc._check_url('https://cdn/x.webp', session, 5, None),
            ('ok', 200, 'image/webp'),
        )
        session.get.assert_not_called()

    def test_403_del_cdn_es_http_otro_y_reintenta_con_el_header(self):
        from app.services import verificacion_fotos_service as svc
        session = self._sesion(403, 'application/xml', cuerpo=b'<?xml version="1.0"?>')
        self.assertEqual(
            svc._check_url('https://cdn/paola/x.webp', session, 5, {'X-Key': 'k'}),
            ('http_otro', 403, 'application/xml'),
        )
        self.assertEqual(session.get.call_args.kwargs['headers']['X-Key'], 'k')

    def test_200_con_html_sigue_siendo_no_imagen(self):
        from app.services import verificacion_fotos_service as svc
        session = self._sesion(200, 'text/html', cuerpo=b'<!DOCTYPE html>')
        self.assertEqual(
            svc._check_url('https://cdn/login', session, 5, None),
            ('no_imagen', 200, 'text/html'),
        )


class TareaDiariaTest(TestCase):
    """``sincronizar_y_verificar_todas`` procesa cada integración aislada: un
    ecommerce caído no frena a los demás y la verificación corre igual."""

    def setUp(self):
        cache.clear()
        self.empresa = crear_empresa(nombre='Grupo', rut='76.333.333-3')
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='Local')
        crear_producto_con_talla(self.sucursal, articulo='ART1', sku=1)
        base = dict(
            tipo='realsport', empresa=self.empresa, url_api='https://x.cl',
            api_key='k', activo=True,
        )
        self.c_ok = CredencialesEcommerce.objects.create(codigo='a', nombre='A', **base)
        self.c_fail = CredencialesEcommerce.objects.create(codigo='b', nombre='B', **base)
        base['activo'] = False
        self.c_off = CredencialesEcommerce.objects.create(codigo='c', nombre='C', **base)
        FotoPortadaArticulo.objects.create(
            articulo='ART1', url_foto='https://cdn/a.webp', origen=self.c_ok,
        )

    def test_sincroniza_y_verifica_solo_las_activas(self):
        from app.services import verificacion_fotos_service as svc
        from app.services.realsport_imagenes_service import RealsportImagenesError

        def fake_sync(cred):
            if cred.codigo == 'b':
                raise RealsportImagenesError('ecommerce caído')
            return {'con_foto': 3}

        with mock.patch(
            'app.services.realsport_imagenes_service.sincronizar_credencial',
            side_effect=fake_sync,
        ), mock.patch.object(svc, '_check_url', return_value=('ok', 200, 'image/webp')):
            resumen = svc.sincronizar_y_verificar_todas(muestra=10)

        self.assertEqual([r['codigo'] for r in resumen], ['a', 'b'])
        self.assertTrue(resumen[0]['sync_ok'])
        self.assertEqual(resumen[0]['con_foto'], 3)
        self.assertTrue(resumen[0]['verif_ok'])
        self.assertFalse(resumen[1]['sync_ok'])
        self.assertIn('caído', resumen[1]['sync_error'])
        self.assertTrue(resumen[1]['verif_ok'])
        for cred in (self.c_ok, self.c_fail):
            cred.refresh_from_db()
            self.assertIsNotNone(cred.ultima_verif_at)
        self.c_off.refresh_from_db()
        self.assertIsNone(self.c_off.ultima_verif_at)
