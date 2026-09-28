"""
R2FG — humo de las plantillas tocadas en la ronda 2 de Compras.

Renderiza cada pantalla con el camino real (middleware + vista) y comprueba
que los ganchos nuevos llegan al navegador. El comportamiento JS se probó
aparte en Chrome headless (arnés del scratchpad r2fg/harness.py):

  * verGestionProductos: talla_origen_<nueva> al corregir talla (B2-01),
    sucursales destino al crear desde "Editar recepción" (B2-01), Swal
    "Crear igual" ante el 409 de ingreso previo (B15-03), ?compra_id= (B15-04),
    ?v= nuevo de carga_factura.js y 409 de reuso de factura en Crear Manual.
  * lotes_producto: mensaje del 403 del middleware (`mensaje`, A3-01/ui).
  * inteligencia_compra: "Ya pedido en OC abiertas" (B15-07 paso 3).
  * reporte_compras: enlace de pagos pendientes a Gestión DTE Compras.

Ejecutar en la base aislada (NO producción):
    DATABASE_URL=postgres://postgres:admin@localhost:5432/retail_r2fg \
        python manage.py test app.tests.test_r2fg_plantillas --keepdb
"""
from django.test import Client, TestCase

from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)


class PlantillasR2FGTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa(nombre='Retail R2FG', rut='76.888.888-8')
        # Gestión de productos está restringida a EDEL/GILD/IMP/PA00.
        cls.cd = crear_sucursal(empresa=cls.empresa, alias='EDEL',
                                es_centro_distribucion=True)
        cls.maestro = crear_usuario(username='maestro-r2fg', rol='maestro')
        crear_empresa_user(cls.maestro, cls.empresa, cls.cd)
        _, cls.pt = crear_producto_con_talla(cls.cd, articulo='R2FG-1', sku=7799001, stock=3)
        crear_lote_fifo(cls.pt, cantidad=3, costo_unitario=1000)

    def setUp(self):
        self.c = Client()
        self.c.force_login(self.maestro)
        s = self.c.session
        s['idSucursalActual'] = self.cd.id
        s['idEmpresaActual'] = self.empresa.id
        s['alias'] = self.cd.alias
        s.save()

    def _html(self, url):
        r = self.c.get(url)
        self.assertEqual(r.status_code, 200, f'{url} -> {r.status_code}')
        return r.content.decode('utf-8')

    def test_ver_gestion_productos(self):
        html = self._html('/app/verGestionProducto/?compra_id=14')
        # (a) B2-01: talla original al corregir
        self.assertIn('talla_origen_${tallaNueva}', html)
        # (b) B2-01: sucursales destino de las recepciones cargadas
        self.assertIn('data-sucursal-destino-id=', html)
        self.assertIn('abrirModalCrearProducto(productoId, sucIds', html)
        # (c) B15-03: reenvío con confirmación
        self.assertIn('requiere_confirmacion_ingreso_previo', html)
        self.assertIn("name: 'confirmar_ingreso_previo', value: '1'", html)
        # (d) B15-04: filtro por compra desde la URL
        self.assertIn("get('compra_id')", html)
        self.assertIn('function quitarFiltroCompraUrl', html)
        self.assertIn('filtros.compra_id = window._compraIdFiltroUrl', html)
        # (e) versión nueva del JS del agente
        self.assertIn('js/carga_factura.js?v=202609270100', html)
        self.assertNotIn('carga_factura.js?v=202609260100', html)
        # (f) 409 de reuso de factura en Crear Manual
        self.assertIn("name: 'confirmar_exceso_factura', value: 'true'", html)
        self.assertIn('r.needs_confirmation', html)

    def test_lotes_producto(self):
        html = self._html(f'/app/lotes_producto/{self.pt.id}/')
        self.assertIn('function mensajeErrorXhrLotes', html)
        self.assertIn('data.mensaje', html)
        self.assertNotIn("xhr.responseJSON?.error || 'Error inesperado'", html)

    def test_inteligencia_compra(self):
        html = self._html('/app/reportes/inteligencia-compra/')
        self.assertIn('function htmlOcAbiertas', html)
        self.assertIn('Ya pedido en OC abiertas (sin recepción registrada)', html)
        self.assertIn('htmlOcAbiertas(r.en_oc_abiertas)', html)

    def test_reporte_compras(self):
        html = self._html('/app/reportes/compras/')
        self.assertIn('function urlGestionDtePago', html)
        self.assertIn('/app/verGestionDteCompras/?buscar=', html)
