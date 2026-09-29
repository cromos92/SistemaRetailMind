"""
Configuración → Inteligencia Artificial (29-09-2026): claves de API cifradas
en BD y modelo por tarea, con las variables de entorno de respaldo; pantalla
solo del Maestro (permiso `inteligencia_artificial`). Las API de los
proveedores se simulan (requests.get): no hay llamadas reales.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_ia_configuracion
"""
import json
import os
from unittest import mock

from django.test import TestCase, override_settings

from app import utils_ia
from app.models import ClaveProveedorIA, ModeloTareaIA, OpcionMenu, PermisoRol
from app.services.carga_factura import lectura as svc_lectura
from app.tests.factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario
from app.utils_anthropic import opciones_cliente_anthropic

SIN_CLAVES = {k: '' for k in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'DEEPSEEK_API_KEY', 'OPENROUTER_API_KEY',
                              'IA_COMPATIBLE_API_KEY', 'IA_COMPATIBLE_URL', 'ANTHROPIC_WORKSPACE_ID',
                              'CARGA_FACTURA_MODELO_CHAT')}
CLAVE_OPENAI = 'sk-proj-EjemploDePrueba1234567890abcd'
URL = '/app/configuracion/inteligencia-artificial/'


class _Http:
    def __init__(self, status, datos):
        self.status_code = status
        self._datos = datos
        self.text = json.dumps(datos)

    def json(self):
        return self._datos


def _modelos(*ids):
    return _Http(200, {'data': [{'id': i} for i in ids]})


class _Base(TestCase):
    def setUp(self):
        utils_ia.olvidar_config()
        self.addCleanup(utils_ia.olvidar_config)


@mock.patch.dict(os.environ, SIN_CLAVES)
@override_settings(ANTHROPIC_API_KEY='sk-ant-de-entorno')
class TestClavesYModelosEnBD(_Base):

    def _guardar(self, proveedor, clave='', **extra):
        fila = ClaveProveedorIA(proveedor=proveedor, **extra)
        fila.set_clave(clave)
        fila.save()
        utils_ia.olvidar_config()
        return fila

    def test_clave_cifrada_en_reposo(self):
        fila = self._guardar('openai', CLAVE_OPENAI)
        crudo = ClaveProveedorIA.objects.values_list('clave_cifrada', flat=True).get(id=fila.id)
        self.assertTrue(crudo.startswith('enc:'))
        self.assertNotIn(CLAVE_OPENAI, crudo)
        self.assertEqual(ClaveProveedorIA.objects.get(id=fila.id).get_clave(), CLAVE_OPENAI)
        self.assertEqual(fila.ultimos4, 'abcd')
        corta = ClaveProveedorIA(proveedor='deepseek')
        corta.set_clave('abc123')
        self.assertEqual(corta.ultimos4, '')      # en una clave corta no se muestra nada

    def test_la_pantalla_manda_sobre_la_variable(self):
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-de-entorno'}):
            self.assertEqual(utils_ia._clave('openai'), 'sk-de-entorno')
            self.assertEqual(utils_ia.origen_clave('openai'), 'variable')
            fila = self._guardar('openai', CLAVE_OPENAI)
            self.assertEqual(utils_ia._clave('openai'), CLAVE_OPENAI)
            self.assertEqual(utils_ia.origen_clave('openai'), 'pantalla')
            fila.activa = False
            fila.save()
            utils_ia.olvidar_config()
            self.assertEqual(utils_ia._clave('openai'), 'sk-de-entorno')   # apagada: rige la variable
        self.assertEqual(utils_ia.clave_anthropic(), 'sk-ant-de-entorno')
        self._guardar('anthropic', 'sk-ant-de-pantalla-000000000000', workspace_id='wrkspc_123')
        self.assertEqual(utils_ia.clave_anthropic(), 'sk-ant-de-pantalla-000000000000')
        self.assertEqual(opciones_cliente_anthropic(), {'default_headers': {'anthropic-workspace-id': 'wrkspc_123'}})

    def test_api_compatible_por_url_sin_clave(self):
        self.assertFalse(utils_ia.configurado('compatible:llama3.2-vision'))
        self._guardar('compatible', '', url_base='http://localhost:11434/v1')
        self.assertTrue(utils_ia.configurado('compatible:llama3.2-vision'))
        self.assertEqual(utils_ia._url_base('compatible'), 'http://localhost:11434/v1')

    def test_modelo_por_tarea_y_origen(self):
        self.assertEqual(svc_lectura.modelo_lectura(), svc_lectura.MODELO)
        self.assertEqual(utils_ia.origen_modelo('chat'), 'defecto')
        with mock.patch.dict(os.environ, {'CARGA_FACTURA_MODELO_CHAT': 'claude-haiku-4-5'}):
            self.assertEqual(utils_ia.origen_modelo('chat'), 'variable')
        ModeloTareaIA.objects.create(tarea='lectura', modelo='gemini:gemini-3.8-flash,claude-opus-5')
        ModeloTareaIA.objects.create(tarea='lectura_opciones', modelo='openai:gpt-5.4-mini;claude-opus-5')
        ModeloTareaIA.objects.create(tarea='rapido', modelo='gemini:gemini-3.8-flash')
        utils_ia.olvidar_config()
        self.assertEqual(svc_lectura.modelo_lectura(), 'gemini:gemini-3.8-flash,claude-opus-5')
        self.assertEqual(svc_lectura.modelos_opcionales(), ['openai:gpt-5.4-mini', 'claude-opus-5'])
        self.assertEqual(svc_lectura.modelo_rapido(), 'gemini:gemini-3.8-flash')
        self.assertEqual(utils_ia.origen_modelo('lectura'), 'pantalla')

    def test_errores_sin_claves(self):
        texto = utils_ia.sin_secretos(f'Incorrect API key provided: {CLAVE_OPENAI} y sk-proj-****abcd', CLAVE_OPENAI)
        self.assertNotIn(CLAVE_OPENAI, texto)
        self.assertNotIn('abcd', texto)
        self.assertIn('Configuración → Inteligencia Artificial', utils_ia.falta_clave('gemini', 'gemini-3.8-flash'))
        self.assertIn('GEMINI_API_KEY', utils_ia.falta_clave('gemini'))


@mock.patch.dict(os.environ, SIN_CLAVES)
@override_settings(ANTHROPIC_API_KEY='')
class _BasePantalla(_Base):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='EDEL')
        cls.maestro = crear_usuario(username='dueno', rol='maestro')
        cls.admin = crear_usuario(username='admin_ia', rol='administrador')
        for u in (cls.maestro, cls.admin):
            crear_empresa_user(u, cls.empresa, cls.sucursal)

    def _entrar(self, user):
        self.client.force_login(user)
        s = self.client.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()

    def _post(self, ruta, cuerpo):
        return self.client.post(URL + ruta, data=json.dumps(cuerpo), content_type='application/json',
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')



@mock.patch.dict(os.environ, SIN_CLAVES)
@override_settings(ANTHROPIC_API_KEY='')
class TestPantallaIA(_BasePantalla):

    def test_la_migracion_crea_la_opcion_solo_para_el_maestro(self):
        opcion = OpcionMenu.objects.get(codigo='inteligencia_artificial')
        self.assertEqual(opcion.url_name, 'inteligencia_artificial')
        filas = PermisoRol.objects.filter(opcion_menu=opcion)
        self.assertTrue(filas.exists())
        self.assertFalse(filas.filter(puede_ver=True).exists())

    def test_maestro_entra_y_el_admin_no(self):
        self._entrar(self.maestro)
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Inteligencia Artificial')
        self.assertContains(r, 'href="/app/configuracion/inteligencia-artificial/"')   # entrada del menú
        self._entrar(self.admin)
        self.assertNotEqual(self.client.get(URL).status_code, 200)
        r = self._post('modelos/', {'tareas': {'chat': 'claude-sonnet-5'}})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(ModeloTareaIA.objects.exists())

    def test_admin_con_permiso_de_ver_no_puede_editar(self):
        PermisoRol.objects.filter(rol='administrador', opcion_menu__codigo='inteligencia_artificial') \
            .update(puede_ver=True)
        self._entrar(self.admin)
        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self._post('clave/', {'proveedor': 'openai', 'clave': CLAVE_OPENAI}).status_code, 403)

    def test_guardar_clave_la_prueba_y_nunca_la_devuelve(self):
        self._entrar(self.maestro)
        with mock.patch('requests.get', return_value=_modelos('gpt-5.4-mini', 'gpt-5.4-nano')) as get:
            r = self._post('clave/', {'proveedor': 'openai', 'clave': CLAVE_OPENAI})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn(CLAVE_OPENAI.encode(), r.content)
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], f'Bearer {CLAVE_OPENAI}')
        fila = ClaveProveedorIA.objects.get(proveedor='openai')
        self.assertTrue(fila.prueba_ok)
        self.assertEqual(fila.modelos, ['gpt-5.4-mini', 'gpt-5.4-nano'])
        self.assertEqual(fila.actualizado_por, self.maestro)
        estado = {p['codigo']: p for p in r.json()['estado']['proveedores']}
        self.assertEqual((estado['openai']['origen'], estado['openai']['ultimos4']), ('pantalla', 'abcd'))
        self.assertIn('openai:gpt-5.4-nano', [m['id'] for m in r.json()['estado']['catalogo']])
        self.assertEqual(utils_ia._clave('openai'), CLAVE_OPENAI)
        # La página tampoco la muestra.
        self.assertNotIn(CLAVE_OPENAI, self.client.get(URL).content.decode())

    def test_clave_rechazada_no_se_guarda_salvo_forzar(self):
        self._entrar(self.maestro)
        rechazo = _Http(401, {'error': {'message': f'Incorrect API key provided: {CLAVE_OPENAI}'}})
        with mock.patch('requests.get', return_value=rechazo):
            r = self._post('clave/', {'proveedor': 'openai', 'clave': CLAVE_OPENAI})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json()['puede_forzar'])
        self.assertNotIn(CLAVE_OPENAI, r.json()['error'])
        self.assertFalse(ClaveProveedorIA.objects.exists())
        with mock.patch('requests.get', return_value=rechazo):
            r = self._post('clave/', {'proveedor': 'openai', 'clave': CLAVE_OPENAI, 'forzar': True})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ClaveProveedorIA.objects.get(proveedor='openai').prueba_ok)

    def test_quitar_vuelve_a_la_variable(self):
        self._entrar(self.maestro)
        with mock.patch('requests.get', return_value=_modelos('gemini-3.8-flash')):
            self._post('clave/', {'proveedor': 'gemini', 'clave': 'AIzaEjemploDePrueba0000000000'})
        with mock.patch.dict(os.environ, {'GEMINI_API_KEY': 'g-de-entorno'}):
            r = self._post('clave/', {'proveedor': 'gemini', 'quitar': True})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(ClaveProveedorIA.objects.exists())
            self.assertEqual(utils_ia._clave('gemini'), 'g-de-entorno')

    def test_probar_guarda_resultado_y_modelos(self):
        self._entrar(self.maestro)
        with mock.patch('requests.get', return_value=_modelos('models/gemini-3.8-flash')):
            self._post('clave/', {'proveedor': 'gemini', 'clave': 'AIzaEjemploDePrueba0000000000'})
        with mock.patch('requests.get', return_value=_modelos('models/gemini-3.8-flash', 'models/gemini-3.5-flash')):
            r = self._post('probar/', {'proveedor': 'gemini'})
        self.assertTrue(r.json()['ok'])
        self.assertEqual(r.json()['modelos'], ['gemini-3.5-flash', 'gemini-3.8-flash'])
        self.assertEqual(ClaveProveedorIA.objects.get(proveedor='gemini').modelos,
                         ['gemini-3.5-flash', 'gemini-3.8-flash'])

    def test_anthropic_se_prueba_con_su_cabecera_y_workspace(self):
        self._entrar(self.maestro)
        with mock.patch('requests.get', return_value=_modelos('claude-opus-5')) as get:
            r = self._post('clave/', {'proveedor': 'anthropic', 'clave': 'sk-ant-api03-Ejemplo0000000000',
                                      'workspace_id': 'wrkspc_abc'})
        self.assertEqual(r.status_code, 200, r.content)
        cab = get.call_args.kwargs['headers']
        self.assertEqual((cab['x-api-key'], cab['anthropic-workspace-id']), ('sk-ant-api03-Ejemplo0000000000', 'wrkspc_abc'))
        self.assertTrue(get.call_args.args[0].startswith('https://api.anthropic.com/v1/models'))

    def test_guardar_modelos_valida_y_vacio_restaura(self):
        self._entrar(self.maestro)
        r = self._post('modelos/', {'tareas': {'chat': 'foo:bar', 'rapido': 'modelo con espacios'}})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()['errores']), {'chat', 'rapido'})
        self.assertFalse(ModeloTareaIA.objects.exists())

        r = self._post('modelos/', {'tareas': {
            'chat': 'gemini:gemini-3.8-flash,claude-sonnet-5',
            'lectura_opciones': ' openai:gpt-5.4-mini ; claude-opus-5 ',
            'busqueda': 'gemini:gemini-3.8-flash',
        }})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(ModeloTareaIA.objects.get(tarea='lectura_opciones').modelo, 'openai:gpt-5.4-mini;claude-opus-5')
        avisos = ' '.join(r.json()['avisos'])
        self.assertIn('no tiene clave', avisos)            # sin GEMINI_API_KEY ni clave en pantalla
        self.assertIn('no busca en internet', avisos)
        tareas = {t['codigo']: t for t in r.json()['estado']['tareas']}
        self.assertEqual((tareas['chat']['origen'], tareas['chat']['efectivo']),
                         ('pantalla', 'gemini:gemini-3.8-flash,claude-sonnet-5'))
        from app.services.carga_factura import chat as svc_chat
        self.assertEqual(utils_ia.modelo_tarea('chat', svc_chat.MODELO_CHAT), 'gemini:gemini-3.8-flash,claude-sonnet-5')

        r = self._post('modelos/', {'tareas': {'chat': ''}})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ModeloTareaIA.objects.filter(tarea='chat').exists())
        self.assertEqual(utils_ia.modelo_tarea('chat', svc_chat.MODELO_CHAT), svc_chat.MODELO_CHAT)

    def test_la_carga_por_factura_usa_la_clave_de_la_pantalla(self):
        """Sin ANTHROPIC_API_KEY en el entorno, la clave guardada en la pantalla
        habilita la lectura (opciones de la carga por factura)."""
        self._entrar(self.maestro)
        self.assertEqual(svc_lectura.opciones_modelo(), [])
        with mock.patch('requests.get', return_value=_modelos('claude-opus-5')):
            self._post('clave/', {'proveedor': 'anthropic', 'clave': 'sk-ant-api03-Ejemplo0000000000'})
        self.assertEqual([o['id'] for o in svc_lectura.opciones_modelo()], [svc_lectura.MODELO])


@mock.patch.dict(os.environ, SIN_CLAVES)
@override_settings(ANTHROPIC_API_KEY='')
class TestArreglosDeLaRevision(_BasePantalla):
    """Hallazgos confirmados en la revisión del 29-09 (claves que viajaban a
    otro servidor, workspace mezclado, sugerencias inválidas, etc.)."""

    def test_cambiar_url_compatible_descarta_la_clave_guardada(self):
        self._entrar(self.maestro)
        with mock.patch('requests.get', return_value=_modelos('llama3')):
            self._post('clave/', {'proveedor': 'compatible', 'clave': 'gsk_SecretoCompatible0000000000',
                                  'url_base': 'https://api.groq.com/openai/v1'})
        with mock.patch('requests.get', return_value=_modelos('llama3')) as get:
            r = self._post('clave/', {'proveedor': 'compatible', 'clave': '', 'url_base': 'http://otro-host:11434/v1'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertNotIn('Authorization', get.call_args.kwargs['headers'])     # la clave vieja no viaja
        fila = ClaveProveedorIA.objects.get(proveedor='compatible')
        self.assertEqual((fila.url_base, fila.clave_cifrada), ('http://otro-host:11434/v1', ''))

    def test_la_clave_del_entorno_no_viaja_a_una_url_de_la_pantalla(self):
        with mock.patch.dict(os.environ, {'IA_COMPATIBLE_API_KEY': 'gsk_ENTORNO', 'IA_COMPATIBLE_URL': 'https://api.groq.com/openai/v1'}):
            self.assertEqual(utils_ia._clave('compatible'), 'gsk_ENTORNO')
            ClaveProveedorIA.objects.create(proveedor='compatible', url_base='http://mi-ollama:11434/v1')
            utils_ia.olvidar_config()
            self.assertEqual(utils_ia._clave('compatible'), '')
            self.assertEqual(utils_ia.origen_clave('compatible'), 'pantalla')
            respuesta = mock.Mock(status_code=200, json=lambda: {'choices': [{'message': {'content': 'hola'},
                                                                              'finish_reason': 'stop'}]})
            with mock.patch('requests.post', return_value=respuesta) as post:
                utils_ia.crear_mensaje('compatible:llama3', messages=[{'role': 'user', 'content': 'x'}])
            self.assertTrue(post.call_args.args[0].startswith('http://mi-ollama:11434/v1'))
            self.assertNotIn('Authorization', post.call_args.kwargs['headers'])

    def test_workspace_sale_de_la_misma_fuente_que_la_clave(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_WORKSPACE_ID': 'wrkspc_env'}):
            self.assertEqual(opciones_cliente_anthropic(), {'default_headers': {'anthropic-workspace-id': 'wrkspc_env'}})
            fila = ClaveProveedorIA(proveedor='anthropic')
            fila.set_clave('sk-ant-api03-Ejemplo0000000000')
            fila.save()
            utils_ia.olvidar_config()
            self.assertEqual(opciones_cliente_anthropic(), {})          # clave de pantalla sin workspace
            fila.activa = False
            fila.save()
            utils_ia.olvidar_config()
            self.assertEqual(opciones_cliente_anthropic(), {'default_headers': {'anthropic-workspace-id': 'wrkspc_env'}})

    def test_cambiar_solo_el_workspace_se_prueba(self):
        self._entrar(self.maestro)
        with mock.patch('requests.get', return_value=_modelos('claude-opus-5')):
            self._post('clave/', {'proveedor': 'anthropic', 'clave': 'sk-ant-api03-Ejemplo0000000000'})
        rechazo = _Http(403, {'error': {'message': 'workspace not found'}})
        with mock.patch('requests.get', return_value=rechazo) as get:
            r = self._post('clave/', {'proveedor': 'anthropic', 'clave': '', 'workspace_id': 'wrkspc_malo'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(get.call_args.kwargs['headers']['x-api-key'], 'sk-ant-api03-Ejemplo0000000000')
        self.assertEqual(ClaveProveedorIA.objects.get(proveedor='anthropic').workspace_id, '')

    def test_probar_con_json_que_no_es_objeto(self):
        self._entrar(self.maestro)
        r = self.client.post(URL + 'probar/', data='[1, 2]', content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_pantalla_muestra_el_modelo_que_responde_de_verdad(self):
        self._entrar(self.maestro)
        with mock.patch('requests.get', return_value=_modelos('claude-opus-5')):
            self._post('clave/', {'proveedor': 'anthropic', 'clave': 'sk-ant-api03-Ejemplo0000000000'})
        r = self._post('modelos/', {'tareas': {'lectura': 'openai:gpt-5.4-mini,claude-opus-5'}})
        lectura = next(t for t in r.json()['estado']['tareas'] if t['codigo'] == 'lectura')
        self.assertEqual((lectura['principales'][0], lectura['usa'][0]), ('openai:gpt-5.4-mini', 'claude-opus-5'))
        self.assertEqual(lectura['precio'], [5.0, 25.0])                 # el precio del que responde
        ids = [m['id'] for m in r.json()['estado']['catalogo']]
        self.assertIn('claude-haiku-4-5', ids)
        self.assertNotIn('claude-opus-4', ids)                            # prefijo de precio, no modelo
