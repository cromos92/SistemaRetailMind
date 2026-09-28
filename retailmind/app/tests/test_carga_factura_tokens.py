"""
Ahorro de tokens del agente "Cargar desde factura" (28-09-2026): caché
explícita en instrucciones y páginas, verificación dirigida en vez de segunda
lectura completa, cuadrantes a resolución nativa, /Rotate del PDF, campos
opcionales del esquema, costo estimado por paso, chat con system cacheado y
vista previa compacta, reutilización de búsquedas en internet.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_carga_factura_tokens
"""
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from app.models import ProductoAprendido
from app.services.carga_factura import chat as svc_chat
from app.services.carga_factura import lectura as svc_lectura
from app.services.carga_factura import web as svc_web


# ------------------------------------------------------------- utilidades


class _Stream:
    def __init__(self, mensaje):
        self.mensaje = mensaje

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.mensaje


def _mensaje(texto, modelo='claude-opus-5', entrada=0, salida=0, leida=0, escrita=0):
    uso = SimpleNamespace(input_tokens=entrada, output_tokens=salida,
                          cache_read_input_tokens=leida, cache_creation_input_tokens=escrita,
                          server_tool_use=None)
    return SimpleNamespace(stop_reason='end_turn', stop_details=None, model=modelo, usage=uso,
                           content=[SimpleNamespace(type='text', text=texto)])


def _cliente_falso(respuestas):
    """Cliente cuyo beta.messages.stream devuelve `respuestas` en orden y
    guarda los kwargs de cada petición en la lista devuelta."""
    llamadas = []

    def stream(**kwargs):
        llamadas.append(kwargs)
        return _Stream(respuestas.pop(0))

    cliente = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    return cliente, llamadas


def _linea(**cambios):
    linea = {
        'articulo': 'ART-1', 'descripcion': 'ZAPATILLA', 'color': '',
        'tallas': [{'talla': '7', 'cantidad': 2}, {'talla': '8', 'cantidad': 3}],
        'cantidad': 5, 'precio_unitario': 10000, 'importe': 50000,
        'precio_venta_a_mano': -1, 'genero': 'HOMBRE', 'categoria': '',
        'especialidades': [], 'confianza': 'alta',
    }
    linea.update(cambios)
    return linea


def _lectura(lineas, **factura):
    f = {'tipo_documento': 'FACTURA', 'folio': 100, 'proveedor_nombre': 'P', 'proveedor_rut': '1-9',
         'fecha_emision': '2026-09-28', 'marca': '', 'total_unidades': -1, 'total_neto': -1,
         'paginas': [1], 'lineas': lineas}
    f.update(factura)
    return {'facturas': [f]}


def _imagen(ancho, alto):
    from PIL import Image
    return Image.new('RGB', (ancho, alto), 'white')


# --------------------------------------------------------------- lectura


class TestPeticionesYCosto(SimpleTestCase):

    def test_pedir_cachea_a_nivel_de_peticion_solo_si_se_pide(self):
        cliente, llamadas = _cliente_falso([_mensaje('{}'), _mensaje('{}')])
        svc_lectura._pedir(cliente, messages=[])
        svc_lectura._pedir(cliente, messages=[], cachear=False)
        self.assertEqual(llamadas[0]['cache_control'], svc_lectura.CACHE)
        self.assertNotIn('cache_control', llamadas[1])
        self.assertEqual(llamadas[1]['betas'], [svc_lectura._BETA_FALLBACK])

    def test_registrar_uso_acumula_tokens_y_costo_por_modelo(self):
        cliente, _ = _cliente_falso([
            _mensaje('{}', 'claude-opus-5', entrada=1000, salida=100, leida=42000, escrita=20000),
            _mensaje('{}', 'claude-sonnet-5', entrada=0, salida=500, leida=10000, escrita=0),
        ])
        svc_lectura.uso_iniciar()
        svc_lectura._pedir(cliente, messages=[])
        svc_lectura._pedir(cliente, modelo='claude-sonnet-5', messages=[])
        uso = svc_lectura.uso_actual()
        self.assertEqual((uso['llamadas'], uso['entrada'], uso['salida'], uso['cache_leida'],
                          uso['cache_escrita']), (2, 1000, 600, 52000, 20000))
        opus = (1000 * 5 + 100 * 25 + 42000 * 0.5 + 20000 * 6.25) / 1e6
        sonnet = (500 * 10 + 10000 * 0.2) / 1e6
        self.assertAlmostEqual(uso['costo_usd'], round(opus + sonnet, 4), places=4)

    def test_precios_por_prefijo_y_desconocido(self):
        self.assertEqual(svc_lectura._precios('claude-sonnet-5-20261001'), svc_lectura.PRECIOS_USD_POR_MILLON['claude-sonnet-5'])
        self.assertEqual(svc_lectura._precios('otro-modelo'), svc_lectura.PRECIOS_USD_POR_MILLON['claude-opus-5'])
        self.assertAlmostEqual(svc_lectura.costo_estimado('claude-sonnet-5', busquedas=3), 0.03)

    def test_enderezar_no_cachea(self):
        cliente, llamadas = _cliente_falso([_mensaje('{"giro_horario": 90}', 'claude-sonnet-5')])
        derecha = svc_lectura._enderezar(cliente, _imagen(400, 300))
        self.assertEqual(derecha.size, (300, 400))
        self.assertNotIn('cache_control', llamadas[0])
        self.assertEqual(llamadas[0]['model'], svc_lectura.MODELO_RAPIDO)


class TestLecturaConCache(SimpleTestCase):

    def _contenido(self):
        return [{'type': 'text', 'text': 'Página 1:'},
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'x'},
                 'cache_control': svc_lectura.CACHE}]

    def test_instrucciones_van_como_system_cacheado_y_las_paginas_con_su_breakpoint(self):
        cliente, llamadas = _cliente_falso([_mensaje('{"facturas": []}')])
        esquema = svc_lectura._esquema([], [], [])
        r = svc_lectura._una_lectura(cliente, self._contenido(), [], 'INSTRUCCIONES', esquema, 1)
        self.assertEqual(r, {'facturas': []})
        k = llamadas[0]
        self.assertEqual(k['system'], [{'type': 'text', 'text': 'INSTRUCCIONES', 'cache_control': svc_lectura.CACHE}])
        contenido = k['messages'][0]['content']
        self.assertEqual(contenido[1]['cache_control'], svc_lectura.CACHE)   # última página
        self.assertNotIn('cache_control', contenido[2])                       # el pedido, que cambia
        self.assertIn('Lee la tabla', contenido[2]['text'])
        self.assertEqual(k['cache_control'], svc_lectura.CACHE)               # la cola que crece (zoom)
        self.assertEqual(k['output_config']['effort'], svc_lectura.ESFUERZO)

    def test_pedido_y_esfuerzo_de_la_verificacion(self):
        cliente, llamadas = _cliente_falso([_mensaje('{"facturas": []}')])
        esquema = svc_lectura._esquema([], [], [])
        svc_lectura._una_lectura(cliente, self._contenido(), [], 'I', esquema, 2,
                                 pedido='MIRA SOLO ESTO', esfuerzo='low')
        k = llamadas[0]
        self.assertEqual(k['messages'][0]['content'][-1]['text'], 'MIRA SOLO ESTO')
        self.assertEqual(k['output_config']['effort'], 'low')
        # El prefijo (system + páginas) es byte a byte el de la primera lectura:
        # solo cambia el último bloque, así la caché sirve.
        self.assertEqual(k['messages'][0]['content'][:2], self._contenido())


class TestVerificacionDirigida(TestCase):   # leer_pdf lee listas del sistema (BD)

    def test_lineas_dudosas_por_cuadre_confianza_y_a_mano(self):
        limpia = svc_lectura._limpiar_lectura(_lectura([
            _linea(),
            _linea(articulo='ART-2', cantidad=6),                       # tallas suman 5
            _linea(articulo='ART-3', confianza='media'),
            _linea(articulo='ART-4', precio_venta_a_mano=29990),
            _linea(articulo='ART-5', dudas='el 8 puede ser 3'),
        ]))
        dudosas = svc_lectura._lineas_dudosas(limpia)
        self.assertEqual([d[1] for d in dudosas], ['ART-2', 'ART-3', 'ART-4', 'ART-5'])
        motivos = {d[1]: d[2] for d in dudosas}
        self.assertIn('tallas suman 5', motivos['ART-2'])
        self.assertIn('confianza media', motivos['ART-3'])
        self.assertIn('escrito a mano', motivos['ART-4'])
        self.assertIn('el 8 puede ser 3', motivos['ART-5'])
        self.assertFalse(svc_lectura._con_dudas(svc_lectura._limpiar_lectura(_lectura([_linea()]))))

    def test_totales_que_no_calzan_sin_culpable_piden_todas_las_lineas(self):
        limpia = svc_lectura._limpiar_lectura(_lectura([_linea(), _linea(articulo='ART-2')],
                                                       total_neto=90000))
        dudosas = svc_lectura._lineas_dudosas(limpia)
        self.assertEqual([d[1] for d in dudosas], ['ART-1', 'ART-2'])
        self.assertTrue(all('totales no calzan' in d[2] for d in dudosas))

    def test_pedido_de_verificacion_nombra_cada_linea(self):
        texto = svc_lectura._pedido_verificacion([(100, 'ART-2', 'tallas suman 5, la línea dice 6')])
        self.assertIn('SOLO a estas 1 línea(s)', texto)
        self.assertIn('Factura 100, línea ART-2: tallas suman 5', texto)

    def test_esquema_de_verificacion_es_chico(self):
        completo = svc_lectura._esquema(['Calzado > Zapatillas'], ['running'], ['BLACK'])
        chico = svc_lectura._esquema_verificacion(completo)
        factura = chico['properties']['facturas']['items']
        self.assertEqual(sorted(factura['properties']), ['folio', 'lineas'])
        self.assertEqual(factura['properties']['lineas']['items'],
                         completo['properties']['facturas']['items']['properties']['lineas']['items'])
        self.assertNotIn('anyOf', json.dumps(chico))

    def test_leer_pdf_verifica_solo_lo_dudoso_reusando_el_prefijo(self):
        primera = _lectura([_linea(), _linea(articulo='ART-2', confianza='baja')])
        segunda = {'facturas': [{'folio': 100, 'lineas': [_linea(articulo='ART-2', confianza='alta')]}]}
        respuestas = [copy.deepcopy(primera), copy.deepcopy(segunda)]
        llamadas = []

        def una_lectura(cliente, contenido, paginas, instrucciones, esquema, orden, **kw):
            llamadas.append({'contenido': contenido, 'esquema': esquema, 'orden': orden, **kw})
            return respuestas.pop(0)

        with mock.patch.object(svc_lectura, '_cliente', return_value=object()), \
             mock.patch.object(svc_lectura, '_imagenes_de_pagina', return_value=None), \
             mock.patch.object(svc_lectura, '_una_lectura', side_effect=una_lectura):
            r = svc_lectura.leer_pdf(b'%PDF-1.4', lecturas=2)
        self.assertEqual((r['segunda'], r['verificadas'], len(r['lecturas'])), ('verificación', 1, 2))
        self.assertEqual(len(llamadas), 2)
        self.assertIn('ART-2', llamadas[1]['pedido'])
        self.assertNotIn('ART-1:', llamadas[1]['pedido'])
        self.assertEqual(llamadas[1]['esfuerzo'], svc_lectura.ESFUERZO_VERIFICACION)
        self.assertNotIn('proveedor_rut', llamadas[1]['esquema']['properties']['facturas']['items']['properties'])
        # El mismo objeto de páginas, con el breakpoint en el último bloque.
        self.assertIs(llamadas[0]['contenido'], llamadas[1]['contenido'])
        self.assertEqual(llamadas[0]['contenido'][-1]['cache_control'], svc_lectura.CACHE)
        self.assertTrue(r['lecturas'][1]['_parcial'])
        combinada = svc_lectura.combinar_lecturas(r['lecturas'])
        lineas = combinada['facturas'][0]['lineas']
        self.assertNotIn('_revisar', lineas[0])          # no la pidió: no es «no la vio»
        self.assertNotIn('_revisar', combinada['facturas'][0])
        self.assertNotIn('_revisar', lineas[1])          # coincidió
        self.assertEqual(lineas[1]['confianza'], 'alta') # …y la duda quedó resuelta
        data = svc_lectura.a_json_de_carga(combinada['facturas'][0], 'EDEL', marca='NIKE')
        self.assertNotIn('_revisar', data['lineas'][1])

    def test_combinar_con_parcial_toma_lo_que_cuadra_y_anota_diferencias(self):
        base = svc_lectura._limpiar_lectura(_lectura([
            _linea(cantidad=6),                                   # no cuadra (tallas 5)
            _linea(articulo='ART-2', precio_unitario=10000, importe=50000),
        ]))
        parcial = svc_lectura._limpiar_lectura({'_parcial': True, 'facturas': [{'folio': 100, 'lineas': [
            _linea(cantidad=5),                                   # cuadra
            _linea(articulo='ART-2', precio_unitario=11000, importe=50000),   # no cuadra
        ]}]})
        combinada = svc_lectura.combinar_lecturas([base, parcial])
        l1, l2 = combinada['facturas'][0]['lineas']
        self.assertEqual(l1['cantidad'], 5)
        self.assertIn('la verificación no coincide (cantidad: 6 / 5)', l1['_revisar'][0])
        self.assertEqual(l2['precio_unitario'], 10000)   # la base cuadraba: se queda
        self.assertIn('precio_unitario: 10000 / 11000', l2['_revisar'][0])


class TestEsquemaEImagenes(TestCase):       # leer_pdf lee listas del sistema (BD)

    def test_campos_raros_son_opcionales(self):
        esquema = svc_lectura._esquema(['Calzado > Zapatillas'], ['running'], ['BLACK'])
        factura = esquema['properties']['facturas']['items']
        linea = factura['properties']['lineas']['items']
        for k in svc_lectura._OPCIONALES_LINEA:
            self.assertIn(k, linea['properties'])
            self.assertNotIn(k, linea['required'])
        for k in ('articulo', 'tallas', 'cantidad', 'precio_unitario', 'importe', 'precio_venta_a_mano', 'confianza'):
            self.assertIn(k, linea['required'])
        for k in svc_lectura._OPCIONALES_FACTURA:
            self.assertNotIn(k, factura['required'])
        self.assertIn('folio', factura['required'])

    def test_limpiar_tolera_opcionales_ausentes_y_a_json_no_falla(self):
        cruda = _lectura([_linea()])
        for k in svc_lectura._OPCIONALES_LINEA:
            cruda['facturas'][0]['lineas'][0].pop(k, None)
        for k in svc_lectura._OPCIONALES_FACTURA:
            cruda['facturas'][0].pop(k, None)
        limpia = svc_lectura._limpiar_lectura(cruda, colores=['BLACK'])
        l = limpia['facturas'][0]['lineas'][0]
        self.assertIsNone(l['descuento_pct'])
        self.assertIsNone(l['marca'])
        self.assertIsNone(limpia['facturas'][0]['descuento_global_pct'])
        data = svc_lectura.a_json_de_carga(limpia['facturas'][0], 'EDEL', marca='NIKE')
        self.assertEqual(data['lineas'][0]['costo'], 10000)
        self.assertNotIn('descuento', data['lineas'][0])

    def test_ajustar_para_api_respeta_lado_y_megapixeles(self):
        grande = svc_lectura._ajustar_para_api(_imagen(2209, 2878))
        self.assertLessEqual(max(grande.size), svc_lectura._LADO_MAX)
        self.assertLessEqual(grande.width * grande.height, svc_lectura._PIXELES_MAX)
        chica = _imagen(800, 600)
        self.assertIs(svc_lectura._ajustar_para_api(chica), chica)

    def test_cuadrantes_con_solape_casi_a_resolucion_nativa(self):
        img = _imagen(2209, 2878)     # escaneo típico (200 dpi)
        cuadrantes = svc_lectura._cuadrantes(img)
        self.assertEqual([n for n, _ in cuadrantes],
                         ['superior izquierdo', 'superior derecho', 'inferior izquierdo', 'inferior derecho'])
        escala = lambda im: svc_lectura._ajustar_para_api(im).width / im.width
        entera = escala(img)
        self.assertLess(entera, 0.5)          # la página entera llega a menos de la mitad
        for _n, recorte in cuadrantes:
            self.assertGreater(recorte.width, 2209 // 2)     # trae solape
            self.assertGreater(recorte.height, 2878 // 2)
            self.assertGreater(escala(recorte), 0.75)        # el cuadrante, casi nativo
            self.assertGreater(escala(recorte), 1.6 * entera)

    def test_bloques_pagina_con_y_sin_cuadrantes(self):
        img = _imagen(2209, 2878)
        con = svc_lectura._bloques_pagina(1, img, cuadrantes=True)
        self.assertEqual(len(con), 10)
        self.assertEqual([b['type'] for b in con], ['text', 'image'] * 5)
        self.assertIn('cuadrante superior izquierdo', con[2]['text'])
        sin = svc_lectura._bloques_pagina(1, img, cuadrantes=False)
        self.assertEqual(len(sin), 2)
        # Una página chica ya va entera a resolución nativa: cuadrantes de más.
        self.assertEqual(len(svc_lectura._bloques_pagina(1, _imagen(900, 1000), cuadrantes=True)), 2)

    def test_rotaciones_del_pdf(self):
        pdf = (b'%PDF-1.4\n1 0 obj\n<< /Type /Page /Parent 2 0 R /Rotate 90 /Contents 4 0 R >>\nendobj\n'
               b'2 0 obj\n<< /Type /Pages /Kids [1 0 R 3 0 R] /Count 2 >>\nendobj\n'
               b'3 0 obj\n<< /Type /Page /Parent 2 0 R >>\nendobj\n')
        self.assertEqual(svc_lectura._rotaciones_pdf(pdf), [90, None])
        self.assertEqual(svc_lectura._rotaciones_pdf(b'nada'), [])

    def test_leer_pdf_no_pregunta_el_giro_si_el_pdf_lo_dice(self):
        def correr(rotaciones):
            with mock.patch.object(svc_lectura, '_cliente', return_value=object()), \
                 mock.patch.object(svc_lectura, '_imagenes_de_pagina', return_value=[_imagen(1200, 1600)]), \
                 mock.patch.object(svc_lectura, '_rotaciones_pdf', return_value=rotaciones), \
                 mock.patch.object(svc_lectura, '_enderezar', side_effect=lambda c, i: i) as enderezar, \
                 mock.patch.object(svc_lectura, '_una_lectura', return_value=_lectura([_linea()])):
                svc_lectura.leer_pdf(b'%PDF-1.4', lecturas=1)
            return enderezar.call_count
        self.assertEqual(correr([90]), 0)
        self.assertEqual(correr([None]), 1)
        self.assertEqual(correr([0]), 1)


# ------------------------------------------------------------------ chat


class TestChatCompacto(SimpleTestCase):

    def test_system_y_vista_previa_cacheados_sin_cache_al_final(self):
        catalogo = {'marcas': ['NIKE'], 'colores': ['BLACK'], 'generos': ['HOMBRE'],
                    'categorias': ['Calzado > Zapatillas'], 'especialidades': ['running'], 'guias': {}}
        cliente, llamadas = _cliente_falso([_mensaje(json.dumps(
            {'respuesta': 'ok', 'cambios': [], 'investigar': [], 'recordar': ''}), 'claude-sonnet-5')])
        with mock.patch.object(svc_lectura, '_cliente', return_value=cliente):
            r = svc_chat._preguntar(catalogo, [], [{'quien': 'usuario', 'texto': 'hola'}], 'la marca es NIKE')
        self.assertEqual(r['respuesta'], 'ok')
        k = llamadas[0]
        self.assertNotIn('cache_control', k)
        self.assertEqual(k['system'][0]['cache_control'], svc_lectura.CACHE)
        self.assertIn('listas_del_sistema', k['system'][0]['text'])
        bloques = k['messages'][0]['content']
        self.assertEqual(len(bloques), 2)
        self.assertEqual(bloques[0]['cache_control'], svc_lectura.CACHE)
        self.assertIn('vista_previa', bloques[0]['text'])
        self.assertNotIn('cache_control', bloques[1])
        self.assertIn('la marca es NIKE', bloques[1]['text'])
        self.assertEqual(k['model'], svc_chat.MODELO_CHAT)

    def test_resumen_de_linea_sin_repeticiones(self):
        plan = {
            'n': 1, 'articulo': 'ART-1', 'descripcion': 'D', 'estado': 'NUEVO', 'omitida': False,
            'unidades': 5, 'tallas': [{'factura': '7', 'ficha': '7', 'cantidad': 2, 'existe': False},
                                      {'factura': '7.5', 'ficha': '7,5', 'cantidad': 3, 'existe': False}],
            'tipo_talla': 'US', 'guia': {'id': 1, 'nombre': 'NIKE HOMBRE'},
            'costo': 10000, 'precioventa': 18990, 'fuente_pv': 'regla', 'precio_lista': None,
            'descuento': None, 'vigentes': None, 'opciones': None, 'opcion_sugerida': None,
            'marca': {'id': 1, 'valor': 'NIKE'}, 'color': None, 'genero': None, 'categoria': None,
            'especialidades': [], 'destino': None, 'candidatas': [], 'errores': [], 'avisos': [],
            'json': {'articulo': 'ART-1', 'tallas': {'7': 2, '7.5': 3}, 'costo': None},
        }
        r = svc_chat._resumen_linea(plan)
        self.assertNotIn('valores_actuales_json', r)
        self.assertNotIn('vigentes', r)
        self.assertNotIn('fichas_candidatas', r)
        self.assertNotIn('errores', r)
        self.assertEqual(r['tallas'], {'7': 2, '7.5': 3})
        self.assertEqual(r['tallas_como_quedan_en_la_ficha'], {'7.5': '7,5'})
        self.assertEqual(r['marca'], 'NIKE')
        plan.update(vigentes=(9000, 0, 17990), opciones=['s', 'c', 't'], opcion_sugerida='c',
                    errores=['x'], avisos=['a', 'b', 'c', 'd'])
        r = svc_chat._resumen_linea(plan)
        self.assertEqual(r['opcion_sugerida'], 'c')
        self.assertEqual(r['avisos'], ['a', 'b', 'c'])
        self.assertEqual(r['errores'], ['x'])


# ------------------------------------------------------ web (con BD)


class TestWebAhorro(TestCase):

    def test_hallazgo_previo_reusa_busquedas_recientes(self):
        fila = ProductoAprendido.objects.create(
            marca='NIKE', articulo='HQ6034-001', nombre_internet='Nike Court Vision',
            que_es='zapatilla urbana', color_internet='BLACK', fuente_url='https://nike.com/x',
            fuente='internet')
        r = svc_web._hallazgo_previo('Nike', 'hq6034-001')
        self.assertEqual((r['encontrado'], r['nombre'], r['color_primario']), (True, 'Nike Court Vision', 'BLACK'))
        self.assertEqual(r['_fecha'], timezone.now().date().isoformat())
        self.assertIsNone(svc_web._hallazgo_previo('NIKE', 'OTRO'))
        self.assertIsNone(svc_web._hallazgo_previo('', 'HQ6034-001'))
        # Sin datos de internet (solo de carga) no hay nada que reutilizar.
        ProductoAprendido.objects.filter(id=fila.id).update(nombre_internet='', color_internet='')
        self.assertIsNone(svc_web._hallazgo_previo('NIKE', 'HQ6034-001'))
        # Vencido: se vuelve a buscar.
        ProductoAprendido.objects.filter(id=fila.id).update(
            nombre_internet='X', actualizado_en=timezone.now() - timedelta(days=svc_web.DIAS_VIGENCIA_BUSQUEDA + 1))
        self.assertIsNone(svc_web._hallazgo_previo('NIKE', 'HQ6034-001'))

    def test_texto_de_busqueda_marca_lo_reutilizado(self):
        texto = svc_web._texto_busqueda([{'articulo': 'A', 'ok': True, 'nombre': 'N', 'reusado': '2026-09-01',
                                          'aplicado': ['color'], 'color': 'BLACK'}])
        self.assertIn('ya lo había buscado el 2026-09-01', texto)

    def test_texto_de_lectura_explica_la_verificacion(self):
        datos = [{'folio': 1, 'proveedor_nombre': 'P', 'fecha_emision': '2026-09-28',
                  'lineas': [{'tallas': {'7': 2}, 'importe': 20000}]}]
        texto = svc_web._texto_lectura(datos, 'escaneo', 'verificación', 3)
        self.assertIn('dudas en 3 línea(s)', texto)
