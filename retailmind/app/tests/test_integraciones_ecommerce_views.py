"""Tests de la pantalla Configuración → Integraciones Ecommerce (vistas).

Cubre el scope multi-empresa del listado y de las acciones JSON, el alta y la
edición con sus validaciones (código duplicado, API key conservada al editar),
la galería paginada de fotos y la sincronización en proceso (comando mockeado).

Correr sin tocar producción (el .env apunta a prod):
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_integraciones_ecommerce_views
"""
import json
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import CredencialesEcommerce, FotoPortadaArticulo
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)

RESULTADO_SYNC_OK = (
    'paginas=1, procesados=10, con_foto=8 (exacto=0, flexible=0, talla=8), sin_match=2'
)


def _cred(empresa, codigo, **kwargs):
    datos = dict(
        nombre=codigo.title(), tipo='realsport', empresa=empresa,
        url_api=f'https://{codigo}.cl', api_key='secreta', activo=True,
    )
    datos.update(kwargs)
    return CredencialesEcommerce.objects.create(codigo=codigo, **datos)


class BaseIntegraciones(TestCase):
    def setUp(self):
        self.e1 = crear_empresa(nombre='Nicol', rut='76.111.111-1')
        self.e2 = crear_empresa(nombre='Paola', rut='76.222.222-2')
        self.s1 = crear_sucursal(empresa=self.e1, alias='Centro')
        self.admin = crear_usuario(username='adm', rol='administrador')
        self.jefe = crear_usuario(username='jefe', rol='jefe_local')
        crear_empresa_user(self.jefe, self.e1, self.s1)
        self.c1 = _cred(self.e1, 'realsport')
        self.c2 = _cred(self.e2, 'paola', tipo='paola')
        FotoPortadaArticulo.objects.create(
            articulo='ART1', url_foto='https://cdn/1.webp', origen=self.c1,
        )
        FotoPortadaArticulo.objects.create(
            articulo='ART2', url_foto='https://cdn/2.webp', origen=self.c1,
        )
        FotoPortadaArticulo.objects.create(
            articulo='PAO1', url_foto='https://cdn/p1.webp', origen=self.c2,
        )

    def _mensajes(self, respuesta):
        return [str(m) for m in respuesta.context['messages']]


class ListadoTest(BaseIntegraciones):
    def test_jefe_solo_ve_sus_empresas(self):
        self.client.force_login(self.jefe)
        r = self.client.get(reverse('integraciones_ecommerce'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c.codigo for c in r.context['credenciales']], ['realsport'])
        self.assertEqual(r.context['kpi_total_fotos'], 2)
        self.assertEqual(r.context['kpi_con_problema'], 1)  # nunca sincronizada
        cred = r.context['credenciales'][0]
        self.assertEqual(cred.estado_sync, 'nunca')
        self.assertEqual(
            {f['articulo'] for f in cred.muestra_fotos}, {'ART1', 'ART2'},
        )
        # La API key nunca viaja al HTML (ni en el json_script del modal).
        self.assertNotIn('secreta', r.content.decode())

    def test_admin_ve_todas_y_estructura_el_resultado_del_sync(self):
        ahora = timezone.now()
        CredencialesEcommerce.objects.filter(pk=self.c1.pk).update(
            ultima_sync_at=ahora,
            ultima_sync_resultado='paginas=3, procesados=1095, con_foto=1087 '
                                  '(exacto=0, flexible=0, talla=1087), sin_match=8',
        )
        CredencialesEcommerce.objects.filter(pk=self.c2.pk).update(
            ultima_sync_at=ahora, ultima_sync_resultado='ERROR: HTTP 500 (page=1): boom',
        )
        self.client.force_login(self.admin)
        r = self.client.get(reverse('integraciones_ecommerce'))
        self.assertEqual(r.status_code, 200)
        por_codigo = {c.codigo: c for c in r.context['credenciales']}
        self.assertEqual(set(por_codigo), {'realsport', 'paola'})
        self.assertEqual(por_codigo['realsport'].estado_sync, 'ok')
        self.assertEqual(
            por_codigo['realsport'].sync_resumen,
            {'procesados': 1095, 'con_foto': 1087, 'sin_match': 8},
        )
        self.assertEqual(por_codigo['paola'].estado_sync, 'error')
        self.assertEqual(por_codigo['paola'].sync_resumen['error'], 'HTTP 500 (page=1): boom')
        self.assertEqual(r.context['kpi_con_problema'], 1)
        self.assertEqual(r.context['kpi_total_fotos'], 3)

    def test_vendedor_es_redirigido(self):
        vendedor = crear_usuario(username='vend', rol='vendedor')
        self.client.force_login(vendedor)
        r = self.client.get(reverse('integraciones_ecommerce'))
        self.assertEqual(r.status_code, 302)


class GuardarTest(BaseIntegraciones):
    def _post(self, **datos):
        base = {
            'nombre': 'Nueva Tienda', 'codigo': 'nueva-tienda', 'tipo': 'otro',
            'empresa_id': self.e1.id, 'url_api': 'https://nueva.cl/',
            'api_key': 'clave-nueva', 'header_name': 'X-AllConnected-Key',
            'prioridad': '5', 'activo': 'on',
        }
        base.update(datos)
        return self.client.post(reverse('guardar_integracion_ecommerce'), base, follow=True)

    def test_crea_normalizando_codigo_y_url(self):
        self.client.force_login(self.admin)
        r = self._post(codigo='Nueva-Tienda')
        cred = CredencialesEcommerce.objects.get(codigo='nueva-tienda')
        self.assertEqual(cred.url_api, 'https://nueva.cl')
        self.assertEqual(cred.api_key, 'clave-nueva')
        self.assertEqual(cred.prioridad, 5)
        self.assertTrue(cred.activo)
        self.assertTrue(any('creada' in m for m in self._mensajes(r)))

    def test_codigo_duplicado_da_mensaje_no_500(self):
        self.client.force_login(self.admin)
        r = self._post(codigo='paola')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any('Ya existe' in m for m in self._mensajes(r)))
        self.assertEqual(CredencialesEcommerce.objects.count(), 2)

    def test_codigo_invalido(self):
        self.client.force_login(self.admin)
        r = self._post(codigo='Mi Tienda!')
        self.assertTrue(any('código' in m for m in self._mensajes(r)))
        self.assertEqual(CredencialesEcommerce.objects.count(), 2)

    def test_crear_sin_api_key(self):
        self.client.force_login(self.admin)
        r = self._post(api_key='')
        self.assertTrue(any('API key' in m for m in self._mensajes(r)))
        self.assertEqual(CredencialesEcommerce.objects.count(), 2)

    def test_editar_conserva_api_key_si_viene_vacia_o_centinela(self):
        self.client.force_login(self.admin)
        for valor in ('', '__sin_cambio__'):
            self._post(id=self.c1.id, codigo='realsport', nombre='Realsport editada', api_key=valor)
            self.c1.refresh_from_db()
            self.assertEqual(self.c1.api_key, 'secreta')
            self.assertEqual(self.c1.nombre, 'Realsport editada')
        self._post(id=self.c1.id, codigo='realsport', api_key='otra')
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.api_key, 'otra')

    def test_jefe_no_mueve_integracion_a_empresa_ajena(self):
        self.client.force_login(self.jefe)
        r = self._post(id=self.c1.id, codigo='realsport', empresa_id=self.e2.id)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.empresa_id, self.e1.id)
        self.assertTrue(any('Sin acceso' in m for m in self._mensajes(r)))


class AccionesJsonTest(BaseIntegraciones):
    def test_fotos_paginadas_con_descripcion_y_busqueda(self):
        crear_producto_con_talla(self.s1, articulo='ART1', sku=11)
        self.client.force_login(self.admin)
        url = reverse('fotos_integracion_ecommerce', args=[self.c1.id])

        data = self.client.get(url).json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['pages'], 1)
        art1 = next(i for i in data['items'] if i['articulo'] == 'ART1')
        self.assertEqual(art1['url'], 'https://cdn/1.webp')
        self.assertEqual(art1['descripcion'], 'ART1 - Descripción')

        data = self.client.get(url, {'q': 'art2'}).json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['articulo'], 'ART2')

        data = self.client.get(url, {'page_size': 1, 'page': 2}).json()
        self.assertEqual(data['pages'], 2)
        self.assertEqual(data['page'], 2)
        self.assertEqual(len(data['items']), 1)

    def test_fotos_solo_urls_muertas_de_la_ultima_verificacion(self):
        CredencialesEcommerce.objects.filter(pk=self.c1.pk).update(
            ultima_verif_detalle=json.dumps([
                {'articulo': 'ART2', 'url': 'https://cdn/2.webp', 'motivo': 'http_404', 'status': 404},
            ]),
        )
        self.client.force_login(self.admin)
        url = reverse('fotos_integracion_ecommerce', args=[self.c1.id])
        data = self.client.get(url, {'solo': 'muertas'}).json()
        self.assertEqual(data['solo'], 'muertas')
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['motivo'], 'http_404')
        self.assertEqual(data['items'][0]['status'], 404)

    def test_fotos_respeta_scope_de_empresa(self):
        self.client.force_login(self.jefe)
        r = self.client.get(reverse('fotos_integracion_ecommerce', args=[self.c2.id]))
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.json()['ok'])

    def test_sincronizar_corre_el_comando_en_proceso_y_refresca_la_fila(self):
        def fake_call_command(nombre, **kwargs):
            self.assertEqual(nombre, 'sincronizar_fotos_ecommerce')
            self.assertEqual(kwargs['codigo'], 'realsport')
            kwargs['stdout'].write('>>> Realsport\n  paginas=1 procesados=10\n')
            CredencialesEcommerce.objects.filter(pk=self.c1.pk).update(
                ultima_sync_at=timezone.now(), ultima_sync_resultado=RESULTADO_SYNC_OK,
            )

        self.client.force_login(self.admin)
        with mock.patch(
            'app.views_modulo_configuracion.call_command', side_effect=fake_call_command,
        ) as m:
            r = self.client.post(reverse('sincronizar_integracion_ecommerce', args=[self.c1.id]))
        m.assert_called_once()
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['estado_sync'], 'ok')
        self.assertEqual(data['resumen']['con_foto'], 8)
        self.assertEqual(data['resumen']['sin_match'], 2)
        self.assertEqual(data['total_fotos'], 2)
        self.assertEqual(data['kpi_total_fotos'], 3)
        self.assertIn('paginas=1', data['stdout'])
        self.assertEqual({f['articulo'] for f in data['muestra']}, {'ART1', 'ART2'})

    def test_sincronizar_reporta_error_del_ecommerce(self):
        def fake_call_command(nombre, **kwargs):
            CredencialesEcommerce.objects.filter(pk=self.c1.pk).update(
                ultima_sync_at=timezone.now(),
                ultima_sync_resultado='ERROR: conexión fallida (page=1): timeout',
            )

        self.client.force_login(self.admin)
        with mock.patch('app.views_modulo_configuracion.call_command', side_effect=fake_call_command):
            data = self.client.post(
                reverse('sincronizar_integracion_ecommerce', args=[self.c1.id]),
            ).json()
        self.assertFalse(data['ok'])
        self.assertEqual(data['estado_sync'], 'error')
        self.assertIn('timeout', data['resumen']['error'])

    def test_sincronizar_inactiva_no_corre_nada(self):
        CredencialesEcommerce.objects.filter(pk=self.c1.pk).update(activo=False)
        self.client.force_login(self.admin)
        with mock.patch('app.views_modulo_configuracion.call_command') as m:
            data = self.client.post(
                reverse('sincronizar_integracion_ecommerce', args=[self.c1.id]),
            ).json()
        m.assert_not_called()
        self.assertFalse(data['ok'])
        self.assertIn('inactiva', data['stderr'])

    def test_sincronizar_respeta_scope_de_empresa(self):
        self.client.force_login(self.jefe)
        with mock.patch('app.views_modulo_configuracion.call_command') as m:
            r = self.client.post(reverse('sincronizar_integracion_ecommerce', args=[self.c2.id]))
        m.assert_not_called()
        self.assertEqual(r.status_code, 403)

    def test_verificar_acota_muestra_y_timeout_para_la_ui(self):
        resultado_falso = {
            'codigo': 'realsport', 'empresa': 'Nicol',
            'cobertura': {
                'empresa': 'Nicol', 'empresa_id': self.e1.id,
                'articulos': 2, 'con_foto': 2, 'sin_foto': 0, 'por_sucursal': [],
            },
            'urls': {
                'total_urls': 2, 'verificadas': 2, 'muestra': False,
                'counters': {'ok': 2, 'http_404': 0, 'no_imagen': 0, 'http_otro': 0, 'error_red': 0},
                'muertas_ejemplos': [],
            },
        }
        self.client.force_login(self.admin)
        with mock.patch(
            'app.services.verificacion_fotos_service.verificar_credencial',
            return_value=resultado_falso,
        ) as m:
            r = self.client.post(
                reverse('verificar_integracion_ecommerce', args=[self.c1.id]),
                {'muestra': '9999'},
            )
        kwargs = m.call_args.kwargs
        self.assertEqual(kwargs['muestra'], 300)   # tope, aunque pidan más
        self.assertEqual(kwargs['timeout'], 5)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['verif_resumen']['tiene_urls'])
        self.assertEqual(data['verif_resumen']['urls_ok'], 2)
        self.assertEqual(data['verif_muertas'], 0)
        self.c1.refresh_from_db()
        self.assertIsNotNone(self.c1.ultima_verif_at)
