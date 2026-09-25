"""
Agente "Cargar desde factura" (verGestionProducto → modal, endpoints
/app/carga-factura/...). La lectura con Claude se simula; la carga es real:
pasa por views.crear_producto_manual como el modal.

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_carga_factura_web
"""
import tempfile
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from app.middleware_permisos import URL_PERMISO_MAP
from app.models import (
    AtributoOpcion, CargaFacturaPdf, Categoria, Dte, GuiaTalla, GuiaTallaItem,
    ModuloSistema, Movimientos_Producto, OpcionMenu, PermisoRol, Producto,
    Producto_Talla, Productos_Atributos,
)
from app.services.carga_factura import web as svc_web
from app.services.carga_factura.precios import precio_por_regla
from app.tests.factories import (
    crear_correlativo, crear_empresa, crear_empresa_user, crear_sucursal, crear_usuario,
)

MEDIA_TMP = tempfile.mkdtemp(prefix='carga_factura_test_')


def _lectura_simulada(**cambios):
    linea = {
        'articulo': 'HQ6034-001', 'descripcion': 'NIKE COURT VISION LO',
        'tallas': [{'talla': '7', 'cantidad': 2}, {'talla': '7.5', 'cantidad': 3}],
        'cantidad': 5, 'precio_unitario': 30000, 'importe': 150000,
        'precio_venta_a_mano': None, 'precio_venta_a_mano_alternativa': None,
        'reparto_a_mano': [], 'marca': 'NIKE', 'genero': 'HOMBRE',
        'categoria': 'Calzado > Zapatillas', 'especialidades': [],
        'confianza': 'alta', 'dudas': '',
    }
    linea.update(cambios)
    factura = {
        'tipo_documento': 'FACTURA ELECTRONICA', 'folio': 555,
        'proveedor_nombre': 'Proveedor Test', 'proveedor_rut': '77.111.111-1',
        'fecha_emision': '2026-07-01', 'marca': 'NIKE', 'total_unidades': 5,
        'total_neto': 150000, 'paginas': [1], 'lineas': [linea], 'observaciones': '',
    }
    return {'lecturas': [{'facturas': [factura]}], 'modo': 'escaneo'}


@override_settings(MEDIA_ROOT=MEDIA_TMP, ANTHROPIC_API_KEY='sk-ant-test')
class TestAgenteCargaFactura(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = crear_usuario(username='bodeguero', rol='administrador')
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='EDEL')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)
        crear_correlativo(cls.sucursal, tipo_dte='COMPRA')
        # Los endpoints cuelgan del permiso de la pantalla (middleware_permisos).
        modulo = ModuloSistema.objects.create(codigo='existencias_test', nombre='Existencias', orden=1)
        opcion = OpcionMenu.objects.create(modulo=modulo, codigo='gestion_producto',
                                           nombre='Gestión Producto', activo=True)
        for rol in ('administrador', 'vendedor'):
            PermisoRol.objects.create(rol=rol, opcion_menu=opcion, puede_ver=True)

        calzado = Categoria.objects.create(nombre='Calzado')
        cls.cat = Categoria.objects.create(nombre='Zapatillas', padre=calzado)
        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_sexo = Productos_Atributos.objects.create(nombre='Sexo', descripcion='Sexo')
        cls.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='NIKE')
        cls.color = AtributoOpcion.objects.create(atributo=attr_color, valor='MULTI')
        cls.genero = AtributoOpcion.objects.create(atributo=attr_sexo, valor='HOMBRE')
        AtributoOpcion.objects.create(atributo=attr_sexo, valor='MUJER')
        guia = GuiaTalla.objects.create(marca=cls.marca, nombre='NIKE HOMBRE')
        for orden, (cl, us) in enumerate((('39', '7'), ('40', '7.5'), ('41', '8'))):
            GuiaTallaItem.objects.create(guia=guia, cl=cl, us=us, orden=orden)

        cls.proveedor = crear_empresa(nombre='Proveedor Test', rut='77.111.111-1')
        cls.dte = Dte.objects.create(
            emisor=cls.proveedor, receptor=cls.empresa,
            numero_documento=555, tipo_documento='FACTURA',
            monto_neto=150000, monto_con_iva=178500,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='tester', fecha_emision='2026-07-01',
            fecha_vencimiento='2026-07-30', diasCredito=30,
            bultos=1, unidades_productos=5,
            tipo_transaccion='COMPRA', sucursal=cls.sucursal,
        )

    def setUp(self):
        svc_web.SINCRONO = True
        self.addCleanup(setattr, svc_web, 'SINCRONO', False)
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['idEmpresaActual'] = self.empresa.id
        session['alias'] = 'EDEL'
        session.save()

    def _subir(self, lectura=None, marca='NIKE'):
        with mock.patch('app.services.carga_factura.lectura.leer_pdf',
                        return_value=lectura or _lectura_simulada()):
            resp = self.client.post('/app/carga-factura/subir/', {
                'archivo': SimpleUploadedFile('Factura 555.pdf', b'%PDF-1.4 prueba',
                                              content_type='application/pdf'),
                'sucursal': self.sucursal.id, 'marca': marca, 'lecturas': 1,
            })
        data = resp.json()
        self.assertTrue(data['success'], data)
        return data['id']

    def _planificar(self, sesion_id, cuerpo=None):
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/planificar/',
                                data=cuerpo or {}, content_type='application/json')
        data = resp.json()
        self.assertTrue(data['success'], data)
        return data

    # ---------------------------------------------------------------- tests

    def test_permiso_mapeado_a_gestion_producto(self):
        self.assertEqual(URL_PERMISO_MAP['/app/carga-factura/'], 'gestion_producto')

    def test_sin_api_key_no_acepta_pdf(self):
        with override_settings(ANTHROPIC_API_KEY=''):
            resp = self.client.post('/app/carga-factura/subir/', {
                'archivo': SimpleUploadedFile('f.pdf', b'%PDF', content_type='application/pdf'),
                'sucursal': self.sucursal.id,
            })
        self.assertEqual(resp.status_code, 503)
        self.assertIn('ANTHROPIC_API_KEY', resp.json()['error'])

    def test_lectura_deja_la_sesion_en_vista_previa(self):
        sesion_id = self._subir()
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        self.assertEqual(sesion.estado, 'LEIDA')
        self.assertEqual(len(sesion.facturas), 1)
        self.assertEqual(sesion.facturas[0]['folio'], 555)
        self.assertEqual(sesion.facturas[0]['lineas'][0]['tallas'], {'7': 2, '7.5': 3})
        tipos = [m['tipo'] for m in sesion.mensajes]
        self.assertEqual(tipos, ['subida', 'texto', 'lectura'])

        resp = self.client.get(f'/app/carga-factura/{sesion_id}/').json()
        self.assertEqual(resp['sesion']['estado'], 'LEIDA')
        self.assertEqual(resp['sesion']['facturas'][0]['lineas'], 1)

    def test_error_de_lectura_queda_en_la_conversacion(self):
        with mock.patch('app.services.carga_factura.lectura.leer_pdf',
                        side_effect=RuntimeError('API caída')):
            resp = self.client.post('/app/carga-factura/subir/', {
                'archivo': SimpleUploadedFile('f.pdf', b'%PDF', content_type='application/pdf'),
                'sucursal': self.sucursal.id,
            })
        sesion = CargaFacturaPdf.objects.get(id=resp.json()['id'])
        self.assertEqual(sesion.estado, 'ERROR')
        self.assertIn('API caída', sesion.error)
        self.assertEqual(sesion.mensajes[-1]['tipo'], 'error')

    def test_vista_previa_planifica_como_el_comando(self):
        sesion_id = self._subir()
        data = self._planificar(sesion_id)
        item = data['facturas'][0]
        self.assertIsNone(item['error'], item)
        self.assertEqual(item['dte']['id'], self.dte.id)
        plan = item['planes'][0]
        self.assertEqual(plan['estado'], 'NUEVO')
        self.assertEqual(plan['errores'], [])
        self.assertEqual(plan['costo'], 30000)
        self.assertEqual(plan['precioventa'],
                         precio_por_regla(30000, 40000, Decimal('1.85'), Decimal('1.8')))
        self.assertEqual(plan['tipo_talla'], 'US')
        self.assertEqual(plan['guia']['nombre'], 'NIKE HOMBRE')
        self.assertEqual([t['ficha'] for t in plan['tallas']], ['7', '7.5'])
        self.assertEqual(item['totales']['a_cargar'], 1)
        self.assertEqual(item['totales']['bloqueantes'], 0)

    def test_correcciones_se_guardan_y_replanifican(self):
        sesion_id = self._subir(_lectura_simulada(genero=None, categoria=None))
        item = self._planificar(sesion_id)['facturas'][0]
        self.assertEqual(item['totales']['bloqueantes'], 1)   # sin categoría → error

        item = self._planificar(sesion_id, {'facturas': [{
            'idx': 0,
            'lineas': [{'precioventa': '59.990', 'genero': 'HOMBRE',
                        'categoria': 'Calzado > Zapatillas', 'tallas': '7 2\n7.5 3\n8 1'}],
        }]})['facturas'][0]
        plan = item['planes'][0]
        # Tallas corregidas pero la cantidad impresa sigue en 5: es error hasta
        # que se corrija también (mismo criterio que el comando).
        self.assertEqual(plan['errores'], ['las tallas suman 6 pero la factura dice 5'])
        self.assertEqual(plan['precioventa'], 59990)
        self.assertEqual(plan['unidades'], 6)
        self.assertEqual([t['ficha'] for t in plan['tallas']], ['7', '7.5', '8'])
        guardado = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]['lineas'][0]
        self.assertEqual(guardado['precioventa'], 59990)
        self.assertEqual(guardado['tallas'], {'7': 2, '7.5': 3, '8': 1})

        item = self._planificar(sesion_id, {'facturas': [{
            'idx': 0, 'lineas': [{'cantidad': '6', 'importe': '180000'}]}]})['facturas'][0]
        self.assertEqual(item['planes'][0]['errores'], [])
        self.assertEqual(item['totales']['bloqueantes'], 0)

    def test_pantalla_incluye_el_modal_del_agente(self):
        resp = self.client.get('/app/verGestionProducto/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('id="modalCargaFactura"', html)
        self.assertIn('id="btnCargarFacturaIA"', html)
        self.assertIn('js/carga_factura.js', html)
        self.assertIn(f'window.SUCURSAL_ACTUAL_ID = {self.sucursal.id};', html)

    def test_correccion_mal_escrita_no_rompe_nada(self):
        sesion_id = self._subir()
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/planificar/',
                                data={'facturas': [{'idx': 0, 'lineas': [{'tallas': '7 dos'}]}]},
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('no es un número', resp.json()['error'])

    def test_carga_crea_el_producto_por_el_camino_del_modal(self):
        sesion_id = self._subir()
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/cargar/',
                                data={'idx': 0, 'opciones': {}}, content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.json())

        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        self.assertEqual(sesion.estado, 'LEIDA')
        factura = sesion.facturas[0]
        self.assertEqual(factura['_estado'], 'CARGADA', factura.get('_resultado'))
        self.assertEqual(factura['_resultado']['ok'], 1)
        self.assertEqual(factura['_resultado']['unidades'], 5)
        self.assertEqual(sesion.mensajes[-1]['tipo'], 'carga')

        producto = Producto.objects.get(articulo='HQ6034-001', sucursal=self.sucursal)
        self.assertEqual(producto.atributo1_id, self.marca.id)
        self.assertEqual(producto.tipo_talla, 'US')
        self.assertEqual(producto.guia_talla.nombre, 'NIKE HOMBRE')
        tallas = dict(Producto_Talla.objects.filter(producto=producto).values_list('talla', 'stock'))
        self.assertEqual(tallas, {'7': 2, '7.5': 3})
        self.assertTrue(Movimientos_Producto.objects.filter(
            ProductoTalla__producto=producto, dte=self.dte).exists())

        # Segunda vez: ya cargada → no se repite
        item = self._planificar(sesion_id)['facturas'][0]
        self.assertEqual(item['estado'], 'CARGADA')
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/cargar/',
                                data={'idx': 0}, content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_no_carga_con_lineas_en_error(self):
        sesion_id = self._subir(_lectura_simulada(categoria=None))
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/cargar/',
                                data={'idx': 0}, content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('error', resp.json()['error'])
        self.assertFalse(Producto.objects.filter(articulo='HQ6034-001').exists())

    def test_linea_omitida_no_bloquea_ni_se_carga(self):
        sesion_id = self._subir(_lectura_simulada(categoria=None))
        item = self._planificar(sesion_id, {'facturas': [{'idx': 0, 'lineas': [{'_omitir': True}]}]})['facturas'][0]
        self.assertEqual(item['totales']['bloqueantes'], 0)
        self.assertEqual(item['totales']['a_cargar'], 0)
        self.assertTrue(item['planes'][0]['omitida'])

    def test_otra_bodega_no_ve_la_sesion(self):
        sesion_id = self._subir()
        otro = crear_usuario(username='otro', rol='vendedor')
        otra_empresa = crear_empresa(nombre='Otra', rut='76.222.222-2')
        otra_sucursal = crear_sucursal(empresa=otra_empresa, alias='NICK1')
        crear_empresa_user(otro, otra_empresa, otra_sucursal)
        self.client.force_login(otro)
        resp = self.client.get(f'/app/carga-factura/{sesion_id}/')
        self.assertEqual(resp.status_code, 404)

    def test_opciones_catalogo(self):
        data = self.client.get('/app/carga-factura/opciones/').json()
        self.assertTrue(data['configurada'])
        self.assertIn('NIKE', data['marcas'])
        self.assertEqual(data['generos'], ['HOMBRE', 'MUJER'])
        self.assertEqual(data['categorias'], ['Calzado > Zapatillas'])
        self.assertEqual(data['guias']['NIKE'], ['NIKE HOMBRE'])
        self.assertEqual([s['alias'] for s in data['sucursales']], ['EDEL'])

    # ------------------------------------------------ chat e indicaciones

    def test_indicaciones_llegan_al_lector(self):
        with mock.patch('app.services.carga_factura.lectura.leer_pdf',
                        return_value=_lectura_simulada()) as leer:
            resp = self.client.post('/app/carga-factura/subir/', {
                'archivo': SimpleUploadedFile('f.pdf', b'%PDF', content_type='application/pdf'),
                'sucursal': self.sucursal.id, 'indicaciones': 'la marca es NIKE, tallas US',
            })
        self.assertTrue(resp.json()['success'])
        self.assertEqual(leer.call_args.kwargs['pistas'], 'la marca es NIKE, tallas US')
        sesion = CargaFacturaPdf.objects.get(id=resp.json()['id'])
        self.assertIn('indicaciones', [m['tipo'] for m in sesion.mensajes])

    def test_chat_aplica_cambios_y_replanifica(self):
        sesion_id = self._subir(_lectura_simulada(genero=None))
        respuesta_claude = {
            'respuesta': 'Listo, la dejé como hombre con margen 1,9.',
            'cambios': [{
                'idx': 0, 'factor_bajo': 1.9, 'factor_alto': 1.9, 'tipo_talla': 'US',
                'guias_talla': [{'genero': 'HOMBRE', 'guia': 'NIKE HOMBRE'}],
                'lineas': [{'n': 1, 'genero': 'HOMBRE',
                            'tallas': [{'talla': '8', 'cantidad': 5}]}],
            }],
        }
        with mock.patch('app.services.carga_factura.chat._preguntar',
                        return_value=respuesta_claude) as preguntar:
            resp = self.client.post(f'/app/carga-factura/{sesion_id}/conversar/',
                                    data={'texto': 'es de hombre, talla 8 las 5, margen 1,9'},
                                    content_type='application/json')
        data = resp.json()
        self.assertTrue(data['success'], data)
        # Claude recibió la vista previa y las listas del sistema
        catalogo, previa, _hist, texto = preguntar.call_args.args
        self.assertIn('NIKE', catalogo['marcas'])
        self.assertEqual(previa[0]['folio'], 555)
        self.assertEqual(texto, 'es de hombre, talla 8 las 5, margen 1,9')
        # Se aplicaron y la vista previa volvió recalculada
        plan = data['facturas'][0]['planes'][0]
        self.assertEqual(plan['genero']['valor'], 'HOMBRE')
        self.assertEqual([t['ficha'] for t in plan['tallas']], ['8'])
        self.assertEqual(plan['precioventa'],
                         precio_por_regla(30000, 40000, Decimal('1.9'), Decimal('1.9')))
        self.assertEqual(plan['errores'], [])
        guardada = CargaFacturaPdf.objects.get(id=sesion_id)
        self.assertEqual(guardada.facturas[0]['_factor_bajo'], '1.9')
        self.assertEqual(guardada.facturas[0]['guias_talla'], {'HOMBRE': 'NIKE HOMBRE'})
        self.assertEqual([m['tipo'] for m in guardada.mensajes[-2:]], ['chat', 'chat'])
        # El texto guardado va limpio; lo aplicado va aparte (la pantalla lo pinta como fichas).
        self.assertNotIn('Apliqué', guardada.mensajes[-1]['texto'])
        self.assertTrue(guardada.mensajes[-1]['cambios'])
        self.assertIn('Apliqué', data['respuesta'])

    def test_esquema_del_chat_sin_uniones_de_tipo(self):
        """La API rechaza esquemas con más de 16 campos nullable/anyOf (el chat tenía 25):
        ningún campo lleva anyOf ni type-lista; «sin cambio» va como '', -1 o []."""
        from app.services.carga_factura import chat as svc_chat
        catalogo = svc_web.opciones_catalogo(self.user)
        esquema = svc_chat._esquema(catalogo)

        def uniones(nodo):
            if isinstance(nodo, dict):
                n = int('anyOf' in nodo or 'oneOf' in nodo or isinstance(nodo.get('type'), list))
                return n + sum(uniones(v) for v in nodo.values())
            if isinstance(nodo, list):
                return sum(uniones(v) for v in nodo)
            return 0
        self.assertEqual(uniones(esquema), 0)
        linea = esquema['properties']['cambios']['items']['properties']['lineas']['items']
        self.assertEqual(linea['properties']['genero']['enum'][0], '')
        self.assertEqual(linea['properties']['omitir']['enum'], ['', 'si', 'no'])
        self.assertEqual(linea['required'], list(linea['properties']))

    def test_esquema_del_chat_acotado_con_catalogo_grande(self):
        """«Schema is too complex for compilation»: con los ~400 marcas y ~300 colores
        de producción repetidos en factura y línea, la gramática no compila. Las listas
        grandes van como texto libre validado en `_a_correcciones`, no como enum."""
        from app.services.carga_factura import chat as svc_chat
        catalogo = svc_web.opciones_catalogo(self.user)
        catalogo['marcas'] = [f'MARCA {i}' for i in range(400)]
        catalogo['colores'] = [f'COLOR {i}' for i in range(300)]
        catalogo['categorias'] = [f'Cat > Sub {i}' for i in range(40)]
        catalogo['guias'] = {f'M{i}': [f'GUIA {i} HOMBRE', f'GUIA {i} MUJER'] for i in range(40)}
        esquema = svc_chat._esquema(catalogo)

        def valores_enum(nodo):
            if isinstance(nodo, dict):
                return len(nodo.get('enum') or []) + sum(valores_enum(v) for v in nodo.values())
            if isinstance(nodo, list):
                return sum(valores_enum(v) for v in nodo)
            return 0
        self.assertLess(valores_enum(esquema), 80)
        linea = esquema['properties']['cambios']['items']['properties']['lineas']['items']
        self.assertNotIn('enum', linea['properties']['color'])
        self.assertNotIn('enum', linea['properties']['marca'])
        self.assertNotIn('enum', esquema['properties']['cambios']['items']['properties']['marca'])

    def test_chat_rechaza_valores_fuera_del_catalogo(self):
        """Sin enum en el esquema, lo que no existe en el sistema se descarta y se avisa;
        lo que existe se normaliza al valor exacto del catálogo."""
        from app.services.carga_factura import chat as svc_chat
        attr = Productos_Atributos.objects.create(nombre='Especialidad', descripcion='Especialidad')
        AtributoOpcion.objects.create(atributo=attr, valor='running')
        catalogo = svc_web.opciones_catalogo(self.user)
        previa = [{'idx': 0, 'folio': 1, 'n_lineas': 1, 'planes': [{}]}]
        cambios, rechazos = svc_chat._a_correcciones([{
            'idx': 0, 'marca': 'nike', 'color': 'FUCSIA INVENTADO',
            'guias_talla': [{'genero': 'hombre', 'guia': 'nike hombre'},
                            {'genero': 'MUJER', 'guia': 'NO EXISTE'}],
            'lineas': [{'n': 1, 'genero': 'mujer', 'categoria': 'Calzado > Zapatillas',
                        'color': 'multi', 'especialidades': ['running', 'inventada']}],
        }], previa, catalogo)
        self.assertEqual(cambios, [{
            'idx': 0, 'marca': 'NIKE', 'guias_talla': [{'genero': 'HOMBRE', 'guia': 'NIKE HOMBRE'}],
            'lineas': [{'genero': 'MUJER', 'categoria': 'Calzado > Zapatillas', 'color': 'MULTI',
                        'especialidades': ['running']}],
        }])
        self.assertEqual(rechazos, ['color «FUCSIA INVENTADO»', 'guía «NO EXISTE» para MUJER',
                                    'especialidad «inventada»'])

    def test_chat_avisa_lo_que_no_pudo_aplicar(self):
        sesion_id = self._subir()
        respuesta_claude = {'respuesta': 'Le puse el color.', 'cambios': [
            {'idx': 0, 'lineas': [{'n': 1, 'color': 'FUCSIA INVENTADO'}]}]}
        with mock.patch('app.services.carga_factura.chat._preguntar', return_value=respuesta_claude):
            data = self.client.post(f'/app/carga-factura/{sesion_id}/conversar/',
                                    data={'texto': 'color fucsia'},
                                    content_type='application/json').json()
        self.assertTrue(data['success'], data)
        self.assertIn('No apliqué', data['respuesta'])
        self.assertIn('FUCSIA INVENTADO', data['respuesta'])
        self.assertEqual(data['cambios'], [])
        self.assertEqual(data['facturas'][0]['planes'][0]['color']['valor'], 'MULTI')
        guardada = CargaFacturaPdf.objects.get(id=sesion_id)
        self.assertEqual(guardada.mensajes[-1]['rechazos'], ['color «FUCSIA INVENTADO»'])
        self.assertNotIn('cambios', guardada.mensajes[-1])

    def test_subida_guarda_lo_enviado(self):
        """La tarjeta «Factura enviada a leer» del chat sale de estos datos."""
        sesion_id = self._subir()
        primero = CargaFacturaPdf.objects.get(id=sesion_id).mensajes[0]
        self.assertEqual(primero['tipo'], 'subida')
        self.assertEqual(primero['envio'], {
            'archivo': 'Factura 555.pdf', 'bytes': len(b'%PDF-1.4 prueba'), 'bodega': 'EDEL',
            'marca': 'NIKE', 'lecturas': 1})

    def test_chat_recibe_lo_leido_en_cargas_anteriores(self):
        """«¿A cuánto compré el HQ6034-001 antes?»: el agente recibe la línea de la carga
        anterior desde el expediente guardado, sin volver a leer ningún PDF."""
        from app.services.carga_factura import chat as svc_chat
        anterior = self._subir()
        sesion_id = self._subir()
        with mock.patch('app.services.carga_factura.chat._preguntar',
                        return_value={'respuesta': 'Lo compraste a $30.000.', 'cambios': []}) as preguntar:
            data = self.client.post(f'/app/carga-factura/{sesion_id}/conversar/',
                                    data={'texto': '¿a cuánto compré el HQ6034-001 la vez anterior?'},
                                    content_type='application/json').json()
        self.assertTrue(data['success'], data)
        anteriores = preguntar.call_args.kwargs['anteriores']
        self.assertEqual([l['sesion'] for l in anteriores['lineas']], [anterior])
        self.assertEqual(anteriores['lineas'][0]['articulo'], 'HQ6034-001')
        self.assertEqual(anteriores['lineas'][0]['folio'], 555)
        self.assertEqual(anteriores['lineas'][0]['unidades'], 5)
        self.assertNotIn('facturas', anteriores)   # no se nombró proveedor ni folio
        # Por folio / proveedor devuelve la factura completa; sin pistas, nada.
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        por_folio = svc_chat.facturas_anteriores(sesion, self.user, '¿qué traía la factura 555?')
        self.assertEqual([f['sesion'] for f in por_folio['facturas']], [anterior])
        self.assertEqual(svc_chat.facturas_anteriores(sesion, self.user, '¿y ahora?'), {})

    def test_chat_ignora_los_valores_sin_cambio(self):
        from app.services.carga_factura import chat as svc_chat
        catalogo = svc_web.opciones_catalogo(self.user)
        previa = [{'idx': 0, 'folio': 1, 'n_lineas': 2, 'planes': [{}, {}]}]
        cambios, rechazos = svc_chat._a_correcciones([{
            'idx': 0, 'marca': '', 'color': 'MULTI', 'tipo_talla': '', 'guias_talla': [],
            'umbral_costo': -1, 'factor_bajo': -1, 'factor_alto': 1.9, 'margen_sobreprecio': -1,
            'dte_id': -1, 'renombrar_tallas': 'si',
            'lineas': [
                {'n': 1, 'articulo': '', 'descripcion': '', 'costo': -1, 'precioventa': 29990,
                 'cantidad': -1, 'importe': -1, 'genero': 'MUJER', 'categoria': '', 'color': '',
                 'marca': '', 'especialidades': [], 'ficha_id': -1, 'guia': '',
                 'tallas': [{'talla': '37', 'cantidad': 2}], 'omitir': ''},
                {'n': 2, 'articulo': '', 'descripcion': '', 'costo': -1, 'precioventa': -1,
                 'cantidad': -1, 'importe': -1, 'genero': '', 'categoria': '', 'color': '',
                 'marca': '', 'especialidades': [], 'ficha_id': -1, 'guia': '',
                 'tallas': [], 'omitir': 'si'},
            ],
        }], previa, catalogo)
        self.assertEqual(rechazos, [])
        self.assertEqual(cambios, [{
            'idx': 0, 'color': 'MULTI', '_factor_alto': 1.9, '_renombrar_tallas': True,
            'lineas': [{'precioventa': 29990, 'genero': 'MUJER', 'tallas': {'37': 2}},
                       {'_omitir': True}],
        }])
        # Todo «sin cambio» → ningún cambio (ni siquiera la factura).
        self.assertEqual(svc_chat._a_correcciones([{'idx': 0, 'marca': '', 'lineas': [
            {'n': 1, 'omitir': 'no'}]}], previa, catalogo)[0], [{'idx': 0, 'lineas': [{'_omitir': False}, {}]}])
        self.assertEqual(svc_chat._a_correcciones([{'idx': 0, 'marca': '', 'lineas': []}], previa, catalogo)[0], [])

    # ------------------------------------------------ sesión interrumpida

    def test_leyendo_sin_senal_pasa_a_error(self):
        """Si el servidor se reinicia a mitad de la lectura, el hilo muere y la
        sesión quedaría «leyendo» para siempre: al consultarla se cierra en ERROR."""
        from datetime import timedelta
        from django.utils import timezone
        sesion_id = self._subir()
        CargaFacturaPdf.objects.filter(id=sesion_id).update(estado='LEYENDO', progreso='Lectura 1 de 2')
        # Con señal reciente no se toca.
        data = self.client.get(f'/app/carga-factura/{sesion_id}/').json()
        self.assertEqual(data['sesion']['estado'], 'LEYENDO')
        CargaFacturaPdf.objects.filter(id=sesion_id).update(
            actualizado_en=timezone.now() - timedelta(minutes=31))
        data = self.client.get(f'/app/carga-factura/{sesion_id}/').json()
        self.assertEqual(data['sesion']['estado'], 'ERROR')
        self.assertIn('se interrumpió', data['sesion']['error'])
        self.assertEqual(data['mensajes'][-1]['tipo'], 'error')
        self.assertIn('Vuelve a subir', data['mensajes'][-1]['texto'])

    def test_cargando_sin_senal_vuelve_a_vista_previa(self):
        from datetime import timedelta
        from django.utils import timezone
        sesion_id = self._subir()
        sesion = CargaFacturaPdf.objects.get(id=sesion_id)
        facturas = sesion.facturas
        facturas[0]['_estado'] = 'CARGANDO'
        CargaFacturaPdf.objects.filter(id=sesion_id).update(
            estado='CARGANDO', facturas=facturas, actualizado_en=timezone.now() - timedelta(hours=1))
        data = self.client.get(f'/app/carga-factura/{sesion_id}/').json()
        self.assertEqual(data['sesion']['estado'], 'LEIDA')
        self.assertEqual(data['sesion']['facturas'][0]['estado'], 'PARCIAL')
        self.assertIn('Cargar', data['mensajes'][-1]['texto'])
        # Y se puede seguir: la vista previa vuelve a calcularse.
        previa = self._planificar(sesion_id)
        self.assertEqual(previa['facturas'][0]['folio'], 555)

    def test_chat_sin_cambios_solo_responde(self):
        sesion_id = self._subir()
        with mock.patch('app.services.carga_factura.chat._preguntar',
                        return_value={'respuesta': 'La línea 1 está bien.', 'cambios': []}):
            data = self.client.post(f'/app/carga-factura/{sesion_id}/conversar/',
                                    data={'texto': '¿está bien?'},
                                    content_type='application/json').json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['respuesta'], 'La línea 1 está bien.')
        self.assertEqual(data['cambios'], [])

    # ------------------------------------------- descuento, color, precios

    def test_descuento_por_linea_deja_el_costo_neto(self):
        # 24 × 15.990 − 10 % = 345.384: costo real 14.391, no el precio de lista.
        lectura = _lectura_simulada(
            tallas=[{'talla': '7', 'cantidad': 24}], cantidad=24, precio_unitario=15990,
            descuento_pct=10, importe=345384)
        lectura['lecturas'][0]['facturas'][0]['total_neto'] = 345384
        sesion_id = self._subir(lectura)
        linea = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]['lineas'][0]
        self.assertEqual(linea['costo'], 14391)
        self.assertEqual(linea['precio_lista'], 15990)
        self.assertEqual(linea['descuento'], '10%')
        plan = self._planificar(sesion_id)['facturas'][0]['planes'][0]
        self.assertEqual(plan['costo'], 14391)
        self.assertEqual(plan['precio_lista'], 15990)
        self.assertFalse([a for a in plan['avisos'] if 'importe' in a], plan['avisos'])

    def test_descuento_global_se_prorratea(self):
        lectura = _lectura_simulada()
        factura = lectura['lecturas'][0]['facturas'][0]
        factura['descuento_global_pct'] = 20
        factura['total_neto'] = 120000     # 150.000 − 20 %
        sesion_id = self._subir(lectura)
        data = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]
        self.assertEqual(data['lineas'][0]['costo'], 24000)
        self.assertEqual(data['lineas'][0]['importe'], 120000)
        self.assertEqual(data['descuento_global'], '20%')
        self.assertEqual(data['_revisar'], [])

    def _fichas_chalada(self):
        attr_marca = Productos_Atributos.objects.get(nombre='Marca')
        attr_color = Productos_Atributos.objects.get(nombre='Color')
        chalada = AtributoOpcion.objects.create(atributo=attr_marca, valor='CHALADA')
        black = AtributoOpcion.objects.create(atributo=attr_color, valor='BLACK')
        AtributoOpcion.objects.create(atributo=attr_color, valor='PLATA')
        mujer = AtributoOpcion.objects.get(valor='MUJER')
        ficha = Producto.objects.create(
            articulo='12-REBI-1', descripcion='chala', sucursal=self.sucursal,
            atributo1=chalada, atributo2=black, atributo3=mujer, categoria=self.cat,
            costo=15990, sobreprecio=2000, precioventa=34990, tipo_talla='CL')
        Producto_Talla.objects.create(producto=ficha, talla='36', sku=9000001, stock=1)
        return ficha

    def _lectura_chalada(self, color_2='PLATA'):
        lectura = _lectura_simulada(
            articulo='12-REBI-1', descripcion='NEGRO CHALADA', color='BLACK', marca='CHALADA',
            genero='MUJER', tallas=[{'talla': '36', 'cantidad': 3}], cantidad=3,
            precio_unitario=15990, importe=47970)
        factura = lectura['lecturas'][0]['facturas'][0]
        factura['marca'] = 'CHALADA'
        segunda = dict(factura['lineas'][0], descripcion=f'{color_2} CHALADA', color=color_2)
        factura['lineas'].append(segunda)
        factura['total_unidades'], factura['total_neto'] = 6, 95940
        return lectura

    def test_color_es_parte_de_la_identidad_si_el_codigo_no_lo_lleva(self):
        ficha = self._fichas_chalada()
        sesion_id = self._subir(self._lectura_chalada(), marca='CHALADA')
        item = self._planificar(sesion_id)['facturas'][0]
        negro, plata = item['planes']
        self.assertEqual(negro['estado'], 'EXISTE')
        self.assertEqual(negro['destino']['id'], ficha.id)
        self.assertEqual(plata['estado'], 'NUEVO')          # otra variante, no la misma ficha
        self.assertEqual(plata['color']['valor'], 'PLATA')
        self.assertTrue(any('otros colores' in a for a in plata['avisos']), plata['avisos'])
        self.assertEqual(plata['errores'], [])

    def test_venta_menor_sugiere_mantener_la_venta(self):
        self._fichas_chalada()
        sesion_id = self._subir(self._lectura_chalada(), marca='CHALADA')
        negro = self._planificar(sesion_id)['facturas'][0]['planes'][0]
        self.assertLess(negro['precioventa'], negro['vigentes']['precioventa'])
        # Mismo costo que la ficha → no hay [c]; conservar la venta es [t] (solo stock).
        self.assertEqual(negro['opcion_sugerida'], 't')
        # Al cargar sin elegir, se respeta la sugerencia: la venta no baja.
        resp = self.client.post(f'/app/carga-factura/{sesion_id}/cargar/',
                                data={'idx': 0, 'opciones': {}}, content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.json())
        ficha = Producto.objects.get(articulo='12-REBI-1', atributo2__valor='BLACK')
        self.assertEqual(int(ficha.precioventa), 34990)
        self.assertEqual(Producto_Talla.objects.get(producto=ficha, talla='36').stock, 4)
        # La variante PLATA se creó aparte, con sus 3 unidades
        plata = Producto.objects.get(articulo='12-REBI-1', atributo2__valor='PLATA')
        self.assertEqual(Producto_Talla.objects.get(producto=plata, talla='36').stock, 3)
        resultado = CargaFacturaPdf.objects.get(id=sesion_id).facturas[0]['_resultado']
        self.assertEqual([l['opcion'] for l in resultado['lineas'] if l.get('opcion')], ['t', 's'])
