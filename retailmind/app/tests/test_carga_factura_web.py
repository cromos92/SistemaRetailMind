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

    def _subir(self, lectura=None):
        with mock.patch('app.services.carga_factura.lectura.leer_pdf',
                        return_value=lectura or _lectura_simulada()):
            resp = self.client.post('/app/carga-factura/subir/', {
                'archivo': SimpleUploadedFile('Factura 555.pdf', b'%PDF-1.4 prueba',
                                              content_type='application/pdf'),
                'sucursal': self.sucursal.id, 'marca': 'NIKE', 'lecturas': 1,
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
