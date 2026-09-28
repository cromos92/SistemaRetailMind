"""
B15-10: si la factura leída del PDF no está registrada como DTE, la vista
previa dice qué hacer en la pantalla (no "pon dte_id en el JSON") y trae lo
leído para abrir Gestión Documentos Compras con esos datos.

Ejecutar (BD de test, NO producción):
    python manage.py test app.tests.test_x_carga_factura_registrar_dte
"""
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings

from app.services.carga_factura.facturas import DteNoEncontrado, ErrorCarga, resolver_dte
from app.tests import test_carga_factura_web as base


@override_settings(MEDIA_ROOT=base.MEDIA_TMP, ANTHROPIC_API_KEY='sk-ant-test')
class TestRegistrarDteDesdeLaVistaPrevia(TestCase):

    @classmethod
    def setUpTestData(cls):
        base.TestAgenteCargaFactura.setUpTestData.__func__(cls)

    setUp = base.TestAgenteCargaFactura.setUp
    _subir = base.TestAgenteCargaFactura._subir
    _planificar = base.TestAgenteCargaFactura._planificar

    def _lectura_otro_folio(self):
        lectura = base._lectura_simulada()
        lectura['lecturas'][0]['facturas'][0]['folio'] = 99887
        return lectura

    def test_factura_sin_dte_ofrece_registrarla(self):
        sesion_id = self._subir(self._lectura_otro_folio())
        item = self._planificar(sesion_id)['facturas'][0]
        self.assertIn('no está registrada', item['error'])
        self.assertNotIn('JSON', item['error'])
        registrar = item['registrar_dte']
        self.assertEqual(registrar['folio'], 99887)
        self.assertEqual(registrar['rut'], '77.111.111-1')
        self.assertEqual(registrar['neto'], 150000)
        url = urlparse(registrar['url'])
        self.assertEqual(url.path, '/app/verGestionDteCompras/')
        params = parse_qs(url.query)
        self.assertEqual(params['nuevo'], ['1'])
        self.assertEqual(params['folio'], ['99887'])
        self.assertEqual(params['fecha'], ['2026-07-01'])

    def test_factura_con_dte_no_trae_registrar(self):
        sesion_id = self._subir()
        item = self._planificar(sesion_id)['facturas'][0]
        self.assertIsNone(item['error'])
        self.assertIsNone(item['registrar_dte'])

    def test_el_comando_sigue_recibiendo_su_mensaje(self):
        """resolver_dte lo usa también cargar_productos_factura: mismo texto,
        ahora con una subclase de ErrorCarga."""
        with self.assertRaises(DteNoEncontrado) as ctx:
            resolver_dte({'folio': 99887, 'proveedor_rut': '77.111.111-1'}, nombre='f.json')
        self.assertIsInstance(ctx.exception, ErrorCarga)
        self.assertIn('pon su "dte_id" en el JSON', str(ctx.exception))
