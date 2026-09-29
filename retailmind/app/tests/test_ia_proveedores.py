"""
Varios proveedores de IA para los agentes (app/utils_ia.py, 29-09-2026):
elegir proveedor por el nombre del modelo, traducir la petición de Anthropic
a Chat Completions (imágenes, PDF, herramientas, JSON Schema, esfuerzo) y la
respuesta de vuelta, simplificar ante un 400, cadena de respaldo, costo por
proveedor, búsqueda web de OpenAI y el selector de modelo de la carga por
factura. Las API se simulan (requests.post): no hay llamadas reales.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_ia_proveedores
"""
import json
import os
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from app import utils_ia
from app.models import CargaFacturaPdf, ModuloSistema, OpcionMenu, PermisoRol
from app.services.carga_factura import lectura as svc_lectura
from app.services.carga_factura import web as svc_web
from app.services.carga_factura.facturas import ErrorCarga
from app.tests.factories import crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario

SIN_CLAVES = {k: '' for k in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'DEEPSEEK_API_KEY',
                              'OPENROUTER_API_KEY', 'IA_COMPATIBLE_API_KEY', 'IA_COMPATIBLE_URL',
                              'IA_PRECIOS')}


class _Http:
    """Respuesta HTTP simulada."""

    def __init__(self, status, datos):
        self.status_code = status
        self._datos = datos
        self.text = json.dumps(datos)

    def json(self):
        return self._datos


def _chat(texto=None, llamadas=None, fin='stop', entrada=1000, cacheada=0, salida=50, costo=None):
    mensaje = {'role': 'assistant', 'content': texto}
    if llamadas:
        mensaje['tool_calls'] = llamadas
    uso = {'prompt_tokens': entrada, 'completion_tokens': salida,
           'prompt_tokens_details': {'cached_tokens': cacheada}}
    if costo is not None:
        uso['cost'] = costo
    return _Http(200, {'id': 'x', 'choices': [{'message': mensaje, 'finish_reason': fin}], 'usage': uso})


class _Post:
    """requests.post simulado: devuelve `respuestas` en orden y guarda (url, payload, cabeceras)."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.llamadas.append((url, json, headers))
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _esquema():
    return {'type': 'object', 'properties': {
        'a': {'type': 'integer'},
        'opcional': {'type': 'string'},
        'lista': {'type': 'array', 'items': {'type': 'object', 'properties': {'x': {'type': 'string'}},
                                              'required': ['x'], 'additionalProperties': False}},
    }, 'required': ['a', 'lista'], 'additionalProperties': False}


def _imagen():
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'QUJD'}}


@mock.patch.dict(os.environ, SIN_CLAVES)
class TestConfiguracion(SimpleTestCase):

    def test_proveedor_por_prefijo_o_por_nombre(self):
        self.assertEqual(utils_ia.separar('claude-opus-5'), ('anthropic', 'claude-opus-5'))
        self.assertEqual(utils_ia.separar('anthropic:claude-sonnet-5'), ('anthropic', 'claude-sonnet-5'))
        self.assertEqual(utils_ia.separar('gpt-5.4-mini'), ('openai', 'gpt-5.4-mini'))
        self.assertEqual(utils_ia.separar('gemini-3.8-flash'), ('gemini', 'gemini-3.8-flash'))
        self.assertEqual(utils_ia.separar('openrouter:openai/gpt-5.4-mini:online'),
                         ('openrouter', 'openai/gpt-5.4-mini:online'))
        self.assertEqual(utils_ia.separar('otro-modelo'), ('anthropic', 'otro-modelo'))
        self.assertEqual(utils_ia.cadena(' gemini:x , claude-opus-5 ,'), ['gemini:x', 'claude-opus-5'])

    def test_configurado_segun_claves(self):
        with override_settings(ANTHROPIC_API_KEY=''):
            self.assertFalse(utils_ia.configurado('claude-opus-5'))
            self.assertFalse(utils_ia.configurado('openai:gpt-5.4-mini'))
            with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'sk-x'}):
                self.assertTrue(utils_ia.configurado('openai:gpt-5.4-mini,claude-opus-5'))
            with mock.patch.dict(os.environ, {'IA_COMPATIBLE_URL': 'http://localhost:11434/v1'}):
                self.assertTrue(utils_ia.configurado('compatible:llama3.2-vision'))  # Ollama sin clave
        with override_settings(ANTHROPIC_API_KEY='sk-ant'):
            self.assertTrue(utils_ia.configurado('claude-opus-5'))

    def test_precios_por_prefijo_openrouter_y_variable(self):
        self.assertEqual(utils_ia.precios('gemini:gemini-3.8-flash'), (0.75, 3.75, 0.075, 0.0))
        self.assertEqual(utils_ia.precios('openrouter:openai/gpt-5.4-mini:online'), (0.75, 4.5, 0.075, 0.0))
        self.assertEqual(utils_ia.precios('gpt-5.1'), utils_ia.PRECIOS_USD_POR_MILLON['gpt-5'])
        self.assertIsNone(utils_ia.precios('compatible:llama3.2-vision'))
        with mock.patch.dict(os.environ, {'IA_PRECIOS': '{"llama3.2": [0.1, 0.2]}'}):
            self.assertEqual(utils_ia.precios('compatible:llama3.2-vision'), (0.1, 0.2, 0.01, 0.0))
        # Claude sigue con su tabla y lo desconocido sin prefijo es Claude (Opus).
        self.assertEqual(svc_lectura._precios('gemini:gemini-3.8-flash'), (0.75, 3.75, 0.075, 0.0))
        self.assertEqual(svc_lectura._precios('openai:gpt-9'), utils_ia.PRECIO_DESCONOCIDO)
        self.assertEqual(svc_lectura._precios('anthropic:claude-sonnet-5'),
                         svc_lectura.PRECIOS_USD_POR_MILLON['claude-sonnet-5'])

    def test_etiqueta(self):
        self.assertEqual(utils_ia.etiqueta('gemini:gemini-3.8-flash,claude-opus-5'),
                         'gemini-3.8-flash (Google Gemini) · respaldo: claude-opus-5 (Anthropic)')


@mock.patch.dict(os.environ, {**SIN_CLAVES, 'OPENAI_API_KEY': 'sk-openai', 'GEMINI_API_KEY': 'g-key',
                              'DEEPSEEK_API_KEY': 'd-key', 'OPENROUTER_API_KEY': 'or-key'})
@mock.patch('app.utils_ia.time.sleep', lambda s: None)
class TestTraduccion(SimpleTestCase):

    def test_peticion_openai_completa(self):
        post = _Post(_chat('```json\n{"a": 1, "opcional": "", "lista": []}\n```', entrada=1000, cacheada=800))
        with mock.patch('requests.post', post):
            r = utils_ia.crear_mensaje(
                'openai:gpt-5.4-mini',
                system=[{'type': 'text', 'text': 'Instrucciones', 'cache_control': {'type': 'ephemeral'}}],
                messages=[{'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Página 1:'}, _imagen(),
                    {'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf',
                                                    'data': 'UERG'}}]}],
                tools=[svc_lectura._HERRAMIENTA_ZOOM], max_tokens=64000, cache_control={'type': 'ephemeral'},
                betas=['x'], output_config={'effort': 'max', 'format': {'type': 'json_schema', 'schema': _esquema()}})
        url, p, cab = post.llamadas[0]
        self.assertEqual(url, 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(cab['Authorization'], 'Bearer sk-openai')
        self.assertEqual(p['model'], 'gpt-5.4-mini')
        self.assertEqual(p['max_completion_tokens'], 64000)
        self.assertEqual(p['reasoning_effort'], 'xhigh')
        self.assertNotIn('cache_control', p)
        self.assertNotIn('betas', p)
        self.assertEqual(p['messages'][0], {'role': 'system', 'content': 'Instrucciones'})
        partes = p['messages'][1]['content']
        self.assertEqual(partes[1], {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,QUJD',
                                                                         'detail': 'high'}})
        self.assertEqual(partes[2]['file']['file_data'], 'data:application/pdf;base64,UERG')
        funcion = p['tools'][0]['function']
        self.assertEqual((funcion['name'], funcion['strict']), ('ampliar', True))
        formato = p['response_format']['json_schema']
        self.assertTrue(formato['strict'])
        # Estricto: TODAS las propiedades obligatorias (las opcionales con centinela).
        self.assertEqual(formato['schema']['required'], ['a', 'opcional', 'lista'])
        # Respuesta con la forma de Anthropic: JSON limpio, caché aparte.
        self.assertEqual(r.stop_reason, 'end_turn')
        self.assertEqual(json.loads(r.content[0].text)['a'], 1)
        self.assertEqual((r.usage.input_tokens, r.usage.cache_read_input_tokens, r.usage.output_tokens),
                         (200, 800, 50))
        self.assertEqual(r.model, 'openai:gpt-5.4-mini')

    def test_ida_y_vuelta_de_herramienta_con_imagen(self):
        llamada = [{'id': 'call_1', 'type': 'function',
                    'function': {'name': 'ampliar', 'arguments': '{"pagina": 1, "x0": 0, "y0": 0, "x1": 500, "y1": 500}'}}]
        post = _Post(_chat(None, llamadas=llamada, fin='tool_calls'), _chat('{"a": 2, "lista": []}'))
        mensajes = [{'role': 'user', 'content': [{'type': 'text', 'text': 'Lee'}]}]
        with mock.patch('requests.post', post):
            r1 = utils_ia.crear_mensaje('gpt-5.4-mini', messages=mensajes, tools=[svc_lectura._HERRAMIENTA_ZOOM])
            self.assertEqual(r1.stop_reason, 'tool_use')
            uso = r1.content[0]
            self.assertEqual((uso.type, uso.id, uso.name, uso.input['x1']), ('tool_use', 'call_1', 'ampliar', 500))
            # Como en _una_lectura: se devuelve el contenido tal cual + el resultado con imagen.
            mensajes.append({'role': 'assistant', 'content': r1.content})
            mensajes.append({'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': uso.id, 'content': [_imagen()]}]})
            utils_ia.crear_mensaje('gpt-5.4-mini', messages=mensajes, tools=[svc_lectura._HERRAMIENTA_ZOOM])
        enviados = post.llamadas[1][1]['messages']
        self.assertEqual(enviados[1]['tool_calls'][0]['function']['name'], 'ampliar')
        self.assertEqual(json.loads(enviados[1]['tool_calls'][0]['function']['arguments'])['x1'], 500)
        self.assertEqual((enviados[2]['role'], enviados[2]['tool_call_id']), ('tool', 'call_1'))
        # La imagen del zoom no puede ir en el mensaje 'tool': va en el 'user' siguiente.
        self.assertEqual(enviados[3]['role'], 'user')
        self.assertEqual(enviados[3]['content'][1]['type'], 'image_url')
        # El bloque es un dict: si la conversación sigue con Claude, el SDK lo acepta.
        self.assertIsInstance(uso, dict)

    def test_si_rechaza_el_esquema_reintenta_con_modo_json(self):
        post = _Post(_Http(400, {'error': {'message': "Invalid parameter: 'response_format' of type "
                                                     "'json_schema' is not supported with this model."}}),
                     _chat('Aquí va: {"a": 3, "lista": []} listo'))
        with mock.patch('requests.post', post):
            r = utils_ia.crear_mensaje('openai:gpt-4-turbo', messages=[{'role': 'user', 'content': 'hola'}],
                                       output_config={'format': {'type': 'json_schema', 'schema': _esquema()}})
        segunda = post.llamadas[1][1]
        self.assertEqual(segunda['response_format'], {'type': 'json_object'})
        self.assertIn('JSON Schema', segunda['messages'][0]['content'])
        self.assertEqual(r.content[0].text, '{"a": 3, "lista": []}')

    def test_error_que_no_se_puede_simplificar_no_reintenta(self):
        post = _Post(_Http(400, {'error': {'message': 'image input is not supported'}}))
        with mock.patch('requests.post', post), self.assertRaises(utils_ia.ErrorProveedor):
            utils_ia.crear_mensaje('deepseek:deepseek-chat', messages=[{'role': 'user', 'content': [_imagen()]}])
        self.assertEqual(len(post.llamadas), 1)

    def test_json_invalido_es_error_del_proveedor(self):
        with mock.patch('requests.post', _Post(_chat('no sé'))), self.assertRaises(utils_ia.ErrorProveedor):
            utils_ia.crear_mensaje('gpt-5.4-mini', messages=[{'role': 'user', 'content': 'x'}],
                                   output_config={'format': {'type': 'json_schema', 'schema': _esquema()}})

    def test_gemini_deepseek_y_openrouter(self):
        post = _Post(_chat('{"a": 1, "lista": []}'), _chat('{"a": 1, "lista": []}'),
                     _chat('{"a": 1, "lista": []}', costo=0.0123))
        pedido = dict(messages=[{'role': 'user', 'content': 'x'}], max_tokens=1000,
                      output_config={'effort': 'xhigh', 'format': {'type': 'json_schema', 'schema': _esquema()}})
        with mock.patch('requests.post', post):
            utils_ia.crear_mensaje('gemini:gemini-3.8-flash', **pedido)
            utils_ia.crear_mensaje('deepseek:deepseek-chat', **pedido)
            r = utils_ia.crear_mensaje('openrouter:google/gemini-3.8-flash', **pedido)
        (u_g, gemini, _), (_, deepseek, _), (_, openrouter, cab_or) = post.llamadas
        self.assertTrue(u_g.startswith('https://generativelanguage.googleapis.com/v1beta/openai/'))
        self.assertEqual((gemini['max_tokens'], gemini['reasoning_effort']), (1000, 'high'))
        # Gemini: esquema tal cual (no estricto), el opcional sigue opcional.
        self.assertFalse(gemini['response_format']['json_schema']['strict'])
        self.assertEqual(gemini['response_format']['json_schema']['schema']['required'], ['a', 'lista'])
        self.assertEqual(deepseek['response_format'], {'type': 'json_object'})
        self.assertNotIn('reasoning_effort', deepseek)
        self.assertEqual(openrouter['reasoning'], {'effort': 'high'})
        self.assertEqual(openrouter['usage'], {'include': True})
        self.assertEqual(cab_or['Authorization'], 'Bearer or-key')
        self.assertEqual(r.usage.costo_usd, 0.0123)

    def test_busqueda_web_openai_por_responses(self):
        respuesta = _Http(200, {'status': 'completed', 'output': [
            {'type': 'web_search_call'}, {'type': 'web_search_call'},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '{"a": 5, "lista": []}'}]}],
            'usage': {'input_tokens': 3000, 'output_tokens': 200, 'input_tokens_details': {'cached_tokens': 0}}})
        post = _Post(respuesta)
        with mock.patch('requests.post', post):
            r = utils_ia.crear_mensaje(
                'openai:gpt-5.4-mini', max_tokens=6000,
                messages=[{'role': 'user', 'content': [{'type': 'text', 'text': 'Busca HQ6034-001'}]}],
                tools=[{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 3}],
                output_config={'effort': 'medium', 'format': {'type': 'json_schema', 'schema': _esquema()}})
        url, p, _ = post.llamadas[0]
        self.assertTrue(url.endswith('/responses'))
        self.assertEqual(p['tools'], [{'type': 'web_search'}])
        self.assertEqual(p['text']['format']['type'], 'json_schema')
        self.assertEqual(p['input'][0]['content'][0], {'type': 'input_text', 'text': 'Busca HQ6034-001'})
        self.assertEqual(r.usage.server_tool_use.web_search_requests, 2)
        self.assertEqual(json.loads(r.content[0].text)['a'], 5)

    def test_gemini_no_busca_en_internet(self):
        with self.assertRaises(utils_ia.ErrorProveedor):
            utils_ia.crear_mensaje('gemini:gemini-3.8-flash', messages=[{'role': 'user', 'content': 'x'}],
                                   tools=[{'type': 'web_search_20260209', 'name': 'web_search'}])


class _Stream:
    def __init__(self, mensaje):
        self.mensaje = mensaje

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.mensaje


def _claude_falso(texto='{"a": 9}', modelo='claude-sonnet-5'):
    llamadas = []
    uso = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                          cache_creation_input_tokens=0, server_tool_use=None)

    def stream(**kwargs):
        llamadas.append(kwargs)
        return _Stream(SimpleNamespace(stop_reason='end_turn', stop_details=None, model=modelo, usage=uso,
                                       content=[SimpleNamespace(type='text', text=texto)]))
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream))), llamadas


@mock.patch('app.utils_ia.time.sleep', lambda s: None)
class TestCadenaDeRespaldo(SimpleTestCase):

    @mock.patch.dict(os.environ, SIN_CLAVES)
    @override_settings(ANTHROPIC_API_KEY='sk-ant')
    def test_sin_clave_pasa_al_siguiente(self):
        cliente, llamadas = _claude_falso()
        post = _Post()
        with mock.patch('requests.post', post):
            r = svc_lectura._pedir(cliente, modelo='openai:gpt-5.4-mini,claude-sonnet-5', messages=[])
        self.assertEqual(post.llamadas, [])
        self.assertEqual(llamadas[0]['model'], 'claude-sonnet-5')
        self.assertEqual(r.model, 'claude-sonnet-5')

    @mock.patch.dict(os.environ, {**SIN_CLAVES, 'GEMINI_API_KEY': 'g'})
    @override_settings(ANTHROPIC_API_KEY='sk-ant')
    def test_proveedor_caido_pasa_a_claude_y_se_cobra_lo_de_cada_uno(self):
        cliente, llamadas = _claude_falso()
        post = _Post(_Http(503, {'error': {'message': 'overloaded'}}), _Http(503, {'error': {'message': 'overloaded'}}))
        svc_lectura.uso_iniciar()
        with mock.patch('requests.post', post):
            svc_lectura._pedir(cliente, modelo='gemini:gemini-3.8-flash,claude-sonnet-5', messages=[],
                               cachear=False)
        self.assertEqual(len(post.llamadas), 2)       # 1 reintento y luego el respaldo
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(svc_lectura.uso_actual()['modelo'], 'claude-sonnet-5')

    @mock.patch.dict(os.environ, {**SIN_CLAVES, 'OPENAI_API_KEY': 'k'})
    @override_settings(ANTHROPIC_API_KEY='')
    def test_claude_sin_clave_al_frente_se_salta(self):
        post = _Post(_chat('{"a": 1}', entrada=1000, salida=100))
        svc_lectura.uso_iniciar()
        with mock.patch('requests.post', post):
            r = svc_lectura._pedir(object(), modelo='claude-opus-5,openai:gpt-5.4-mini', messages=[])
        self.assertEqual(r.model, 'openai:gpt-5.4-mini')
        uso = svc_lectura.uso_actual()
        self.assertAlmostEqual(uso['costo_usd'], round((1000 * 0.75 + 100 * 4.5) / 1e6, 4), places=4)
        self.assertEqual(uso['modelo'], 'openai:gpt-5.4-mini')
        self.assertEqual(svc_lectura.ultimo_modelo(), 'openai:gpt-5.4-mini')

    @mock.patch.dict(os.environ, SIN_CLAVES)
    def test_el_ultimo_que_falla_es_error_de_lectura(self):
        with self.assertRaises(svc_lectura.ErrorLectura) as ctx:
            svc_lectura._pedir(object(), modelo='openai:gpt-5.4-mini', messages=[])
        self.assertIn('OPENAI_API_KEY', str(ctx.exception))

    @mock.patch.dict(os.environ, {**SIN_CLAVES, 'OPENROUTER_API_KEY': 'k'})
    def test_costo_real_de_openrouter(self):
        svc_lectura.uso_iniciar()
        with mock.patch('requests.post', _Post(_chat('{}', entrada=5000, salida=500, costo=0.0042))):
            svc_lectura._pedir(object(), modelo='openrouter:qwen/qwen3-vl', messages=[])
        self.assertEqual(svc_lectura.uso_actual()['costo_usd'], 0.0042)

    def test_responder_tambien_sirve_al_asistente(self):
        vistos = []

        def claude(nombre, peticion):
            vistos.append((nombre, peticion))
            return 'ok'
        with override_settings(ANTHROPIC_API_KEY='sk-ant'):
            self.assertEqual(utils_ia.responder('claude-sonnet-4-5', claude, max_tokens=10, messages=[]), 'ok')
        self.assertEqual(vistos[0], ('claude-sonnet-4-5', {'max_tokens': 10, 'messages': []}))


@mock.patch.dict(os.environ, {**SIN_CLAVES, 'GEMINI_API_KEY': 'g'})
@override_settings(ANTHROPIC_API_KEY='sk-ant-test', MEDIA_ROOT=tempfile.mkdtemp(prefix='ia_proveedores_'))
class _BaseSelector(TestCase):
    OPCIONALES = ['gemini:gemini-3.8-flash', 'openai:gpt-5.4-mini', 'claude-opus-5']

    @classmethod
    def setUpTestData(cls):
        cls.user = crear_usuario(username='lector_ia', rol='administrador')
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='EDEL')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)
        modulo = ModuloSistema.objects.create(codigo='existencias_ia', nombre='Existencias', orden=1)
        opcion = OpcionMenu.objects.create(modulo=modulo, codigo='gestion_producto',
                                           nombre='Gestión Producto', activo=True)
        PermisoRol.objects.create(rol='administrador', opcion_menu=opcion, puede_ver=True, puede_crear=True)

    def setUp(self):
        svc_web.SINCRONO = True
        self.addCleanup(setattr, svc_web, 'SINCRONO', False)
        parche = mock.patch.object(svc_lectura, 'MODELOS_OPCIONALES', self.OPCIONALES)
        parche.start()
        self.addCleanup(parche.stop)
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session.save()

    def _subir(self, modelo):
        leidos = []

        def leer(pdf, **kwargs):
            leidos.append(kwargs)
            raise ErrorCarga('lectura simulada')
        with mock.patch.object(svc_lectura, 'leer_pdf', leer):
            data = self.client.post('/app/carga-factura/subir/', {
                'archivo': SimpleUploadedFile('f.pdf', b'%PDF-1.4', content_type='application/pdf'),
                'sucursal': self.sucursal.id, 'lecturas': 1, 'modelo': modelo}).json()
        self.assertTrue(data['success'], data)
        return CargaFacturaPdf.objects.get(id=data['id']), leidos


@mock.patch.dict(os.environ, {**SIN_CLAVES, 'GEMINI_API_KEY': 'g'})
@override_settings(ANTHROPIC_API_KEY='sk-ant-test', MEDIA_ROOT=tempfile.mkdtemp(prefix='ia_proveedores_'))
class TestSelectorDeModelo(_BaseSelector):

    def test_opciones_solo_lista_modelos_con_clave(self):
        ids = [o['id'] for o in svc_lectura.opciones_modelo()]
        # Opus (el de siempre) + Gemini; GPT sin OPENAI_API_KEY no aparece y Opus no se repite.
        self.assertEqual(ids, [svc_lectura.MODELO, 'gemini:gemini-3.8-flash'])
        data = self.client.get('/app/carga-factura/opciones/').json()
        self.assertTrue(data['configurada'])
        self.assertEqual([m['id'] for m in data['modelos']], ids)
        self.assertIn('US$0,75 / 3,75', data['modelos'][1]['nota'])

    def test_subir_con_el_modelo_elegido(self):
        sesion, leidos = self._subir('gemini:gemini-3.8-flash')
        self.assertEqual(leidos[0]['modelo'], 'gemini:gemini-3.8-flash')
        self.assertEqual(sesion.modelo, 'gemini:gemini-3.8-flash')
        envio = next(m for m in sesion.mensajes if m.get('tipo') == 'subida')['envio']
        self.assertEqual(envio['modelo'], 'gemini-3.8-flash (Google Gemini)')

    def test_modelo_no_permitido_usa_el_de_siempre(self):
        sesion, leidos = self._subir('openai:gpt-5.4-mini')      # sin clave en este servidor
        self.assertEqual(leidos[0]['modelo'], svc_lectura.MODELO)
        self.assertEqual(sesion.modelo, svc_lectura.MODELO)

    def test_sin_clave_de_anthropic_igual_lee_con_otro_proveedor(self):
        with override_settings(ANTHROPIC_API_KEY=''), \
                mock.patch.object(svc_lectura, 'MODELO', 'gemini:gemini-3.8-flash,claude-opus-5'):
            sesion, leidos = self._subir('')
        self.assertEqual(sesion.modelo, 'gemini:gemini-3.8-flash,claude-opus-5')

    def test_uso_con_modelo_se_suma_sin_romper(self):
        sesion = CargaFacturaPdf.objects.create(creado_por=self.user, sucursal=self.sucursal,
                                                nombre_archivo='f.pdf', estado='LEIDA')
        uso = svc_web._sumar_uso(sesion.id, 'chat', {'llamadas': 1, 'entrada': 100, 'costo_usd': 0.01,
                                                     'modelo': 'gemini:gemini-3.8-flash'})
        sesion.refresh_from_db()
        self.assertEqual(sesion.uso['entrada'], 100)
        self.assertNotIn('modelo', {k for k in sesion.uso if k != 'pasos'})
        self.assertEqual(sesion.uso['pasos'][0]['modelo'], 'gemini:gemini-3.8-flash')
        self.assertEqual(uso['modelo'], 'gemini:gemini-3.8-flash')


class _HerramientasFalsas:
    """AssistantTools mínimo: una herramienta y contexto de empresa/sucursal."""

    def __init__(self, user):
        self.empresa = SimpleNamespace(nombre='Empresa')
        self.sucursal = SimpleNamespace(alias='EDEL')

    @classmethod
    def get_tools_definitions(cls):
        return [{'name': 'get_mis_ventas', 'description': 'Ventas de la sucursal',
                 'input_schema': {'type': 'object', 'properties': {'dias': {'type': 'integer'}},
                                  'required': []}}]

    def get_mis_ventas(self, dias=1):
        return {'total': 1000 * dias}


@mock.patch.dict(os.environ, {**SIN_CLAVES, 'OPENAI_API_KEY': 'k'})
@override_settings(ANTHROPIC_API_KEY='')
class TestAsistenteConOtroProveedor(SimpleTestCase):

    def test_bucle_de_herramientas_con_gpt(self):
        from assistant import agent as modulo

        llamada = [{'id': 'call_9', 'type': 'function',
                    'function': {'name': 'get_mis_ventas', 'arguments': '{"dias": 7}'}}]
        post = _Post(_chat(None, llamadas=llamada, fin='tool_calls'), _chat('Vendiste $7.000 en 7 días.'))
        with mock.patch.object(modulo, 'AssistantTools', _HerramientasFalsas), \
                mock.patch.object(modulo.AssistantAgent, 'MODEL', 'openai:gpt-5.4-mini'), \
                mock.patch('requests.post', post):
            agente = modulo.AssistantAgent(SimpleNamespace(id=1, rol='administrador'), session_id='s')
            r = agente.chat('¿Cuánto vendí esta semana?')
        self.assertFalse(r['error'], r)
        self.assertEqual(r['response'], 'Vendiste $7.000 en 7 días.')
        self.assertEqual(r['tools_used'], ['get_mis_ventas'])
        segunda = post.llamadas[1][1]['messages']
        self.assertEqual(segunda[0]['role'], 'system')
        self.assertEqual(segunda[3], {'role': 'tool', 'tool_call_id': 'call_9',
                                      'content': json.dumps({'total': 7000})})
        self.assertEqual(agente.get_history()[-1]['content'], 'Vendiste $7.000 en 7 días.')


@mock.patch.dict(os.environ, {**SIN_CLAVES, 'GEMINI_API_KEY': 'g'})
@override_settings(ANTHROPIC_API_KEY='sk-ant-test', MEDIA_ROOT=tempfile.mkdtemp(prefix='ia_proveedores_'))
class TestCadenaLargaAlSubir(_BaseSelector):
    """Una cadena elegida de más de 60 caracteres llega completa al lector
    (CargaFacturaPdf.modelo guarda 60)."""
    LARGA = 'gemini:gemini-3.8-flash,gemini:gemini-2.5-flash,claude-opus-5'
    OPCIONALES = [LARGA]

    def test_la_cadena_llega_completa(self):
        self.assertGreater(len(self.LARGA), 60)
        sesion, leidos = self._subir(self.LARGA)
        self.assertEqual(leidos[0]['modelo'], self.LARGA)
        self.assertEqual(sesion.modelo, 'gemini:gemini-3.8-flash')
