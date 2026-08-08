"""
Tests de la guía de preparación en PDF térmico 80mm (2026-08-07).

Por qué existe: la guía solo se podía imprimir por ESC/POS con QZ Tray, que hay
que instalar sucursal por sucursal; donde no está, la tienda no imprimía nada.
El PDF replica el formato que ya usan en AllConnected y abre en cualquier
navegador. Es guía de PICKING: sin precios ni totales.

Correr en BD local desechable:
    python manage.py test app.tests.test_guia_pdf_ecommerce
"""
import json

from django.http import Http404
from django.test import RequestFactory, TestCase
from django.utils import timezone

from app.models import ModuloSistema, OpcionMenu, PedidoEcommerce, PermisoRol
from app.services.pdf_guia_preparacion import generar_guia_preparacion_pdf
from app.views_ecommerce import (
    _ctx_guia_pdf, api_guia_preparacion_pdf, api_guias_pdf_sucursal,
)

from .factories import crear_sucursal, crear_usuario


class GeneradorPdfTest(TestCase):
    """El generador es puro: dict → bytes. No toca BD."""

    def _ctx(self, **extra):
        base = {
            'numero_ticket_rm': 'RM-PDF0001',
            'canal_origen': 'Paris',
            'numero_pedido_canal': '313274088',
            'folio_despacho': 'PA3000198',
            'fecha': '07/08/2026 10:12',
            'cliente': 'Marcela Godoy',
            'direccion_envio': 'Av. Siempre Viva 742',
            'sucursal': {'empresa': 'Comercial Nick SpA', 'alias': 'NICK1',
                         'direccion': 'Av. Principal 100'},
            'items': [{'sku': '4831874', 'articulo': 'HP6010',
                       'nombre': 'ZAPATILLAS VS PACE 2', 'marca': 'ADIDAS',
                       'color': 'WHITE', 'talla': '42', 'cantidad': 2,
                       'precio': 44990, 'precio_lista': 59990, 'pct_descuento': 25.0,
                       'stock_disponible': 5, 'encontrado': True}],
        }
        base.update(extra)
        return base

    def test_imprime_marca_color_talla_sku_y_precio(self):
        """Lo que pidió la tienda: identificar el artículo exacto en el estante
        y poder explicar el precio si el cliente pregunta."""
        import reportlab.rl_config as rc
        rc.pageCompression = 0          # sin comprimir para poder leer el texto
        try:
            pdf = generar_guia_preparacion_pdf([self._ctx()]).decode('latin-1')
        finally:
            rc.pageCompression = 1
        for esperado in ('ADIDAS', 'ZAPATILLAS VS PACE 2', 'TALLA', '42',
                         'COLOR', 'WHITE', 'SKU', '4831874', 'HP6010'):
            self.assertIn(esperado, pdf, f'falta {esperado!r} en la guía')
        self.assertIn('44.990', pdf, 'precio unitario pagado')
        self.assertIn('59.990', pdf, 'precio de lista cuando hubo descuento')
        self.assertIn('25%', pdf, 'porcentaje de descuento')
        self.assertIn('Valor del pedido', pdf)

    def test_sin_descuento_no_muestra_precio_lista(self):
        ctx = self._ctx()
        ctx['items'][0].update({'precio_lista': 0, 'pct_descuento': None})
        import reportlab.rl_config as rc
        rc.pageCompression = 0
        try:
            pdf = generar_guia_preparacion_pdf([ctx]).decode('latin-1')
        finally:
            rc.pageCompression = 1
        self.assertIn('44.990', pdf)
        self.assertNotIn('antes', pdf, 'sin descuento no se imprime "antes $X"')

    def test_genera_pdf_valido_de_80mm(self):
        pdf = generar_guia_preparacion_pdf([self._ctx()])
        self.assertTrue(pdf.startswith(b'%PDF-'))
        # 80mm ≈ 226.77pt: el ancho es lo que hace que entre en el papel térmico.
        self.assertIn(b'226.77', pdf[:2000])

    def test_una_pagina_por_pedido(self):
        uno = generar_guia_preparacion_pdf([self._ctx()])
        dos = generar_guia_preparacion_pdf([self._ctx(), self._ctx(numero_ticket_rm='RM-PDF0002')])
        self.assertGreater(len(dos), len(uno), 'el lote debe agregar una página')
        self.assertIn(b'/Count 2', dos)

    def test_sin_pedidos_lanza(self):
        with self.assertRaises(ValueError):
            generar_guia_preparacion_pdf([])

    def test_tolera_datos_incompletos(self):
        """Un pedido sin folio, sin talla y sin dirección igual debe imprimirse."""
        ctx = self._ctx(folio_despacho='', direccion_envio='', cliente='')
        ctx['items'] = [{'sku': '', 'nombre': 'Ítem sin SKU', 'cantidad': 1}]
        pdf = generar_guia_preparacion_pdf([ctx])
        self.assertTrue(pdf.startswith(b'%PDF-'))

    def test_escapa_caracteres_xml(self):
        """Un nombre con & o < rompería el XML de Paragraph si no se escapa."""
        ctx = self._ctx(cliente='Godoy & Cía <SpA>')
        ctx['items'] = [{'sku': 'A<1>', 'nombre': 'CAMISA "M" & CO', 'cantidad': 1}]
        pdf = generar_guia_preparacion_pdf([ctx])
        self.assertTrue(pdf.startswith(b'%PDF-'))


class EndpointGuiaPdfTest(TestCase):

    def setUp(self):
        self.sucursal = crear_sucursal()
        self.user = crear_usuario(rol='administrador')
        modulo = ModuloSistema.objects.create(codigo='ecommerce', nombre='Ecommerce')
        opcion = OpcionMenu.objects.create(
            modulo=modulo, codigo='ecommerce_pedidos_todos', nombre='Pedidos Ecommerce')
        PermisoRol.objects.create(rol=self.user.rol, opcion_menu=opcion,
                                  puede_ver=True, puede_editar=True)
        self.pedido = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-PDFEP1', numero_pedido_canal='ORD-PDF1',
            canal_origen='PARIS', sucursal=self.sucursal, cliente_nombre='Cliente PDF',
            sub_estado='ASIGNADO', fecha_asignacion=timezone.now(), total=10000,
            items=[{'sku': '111', 'nombre': 'Zapatilla', 'cantidad': 2,
                    'precio_unitario': 5000}],
        )

    def _pdf(self, pedido_id=None):
        request = RequestFactory().post('/x/')
        request.user = self.user
        request.session = {'idSucursalActual': self.sucursal.id}
        if pedido_id is None:
            return api_guias_pdf_sucursal(request)
        return api_guia_preparacion_pdf(request, pedido_id)

    def test_devuelve_pdf_y_registra_la_impresion(self):
        response = self._pdf(self.pedido.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))

        self.pedido.refresh_from_db()
        self.assertEqual(self.pedido.sub_estado, 'EN_PREPARACION',
                         'pedir el PDF ES el inicio del picking, igual que con QZ')
        self.assertIsNotNone(self.pedido.fecha_impresion_guia)
        self.assertEqual(self.pedido.guia_impresa_por, self.user)

    def test_bloqueado_por_sin_stock_da_409(self):
        self.pedido.sub_estado = 'SIN_STOCK'
        self.pedido.save(update_fields=['sub_estado'])
        response = self._pdf(self.pedido.id)
        self.assertEqual(response.status_code, 409)

    def test_bloqueado_por_estado_del_canal_da_409(self):
        self.pedido.estado_canal = 'PENDIENTE'   # sin pago confirmado
        self.pedido.save(update_fields=['estado_canal'])
        response = self._pdf(self.pedido.id)
        self.assertEqual(response.status_code, 409)

    def test_facturado_da_404(self):
        self.pedido.estado = 'FACTURADO'
        self.pedido.save(update_fields=['estado'])
        with self.assertRaises(Http404):
            self._pdf(self.pedido.id)

    def test_sin_permiso_da_403(self):
        self.user = crear_usuario(username='vend_pdf', rol='vendedor')
        self.assertEqual(self._pdf(self.pedido.id).status_code, 403)

    def test_masiva_devuelve_un_pdf_con_todos(self):
        PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-PDFEP2', numero_pedido_canal='ORD-PDF2',
            canal_origen='PARIS', sucursal=self.sucursal, cliente_nombre='Cliente 2',
            sub_estado='ASIGNADO', fecha_asignacion=timezone.now(), total=5000,
            items=[{'sku': '222', 'nombre': 'Polera', 'cantidad': 1, 'precio_unitario': 5000}],
        )
        response = self._pdf()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'/Count 2', response.content)

        for p in PedidoEcommerce.objects.all():
            self.assertEqual(p.sub_estado, 'EN_PREPARACION')

    def test_masiva_sin_pedidos_da_404_con_mensaje(self):
        self.pedido.sub_estado = 'LISTO_DESPACHO'
        self.pedido.save(update_fields=['sub_estado'])
        response = self._pdf()
        self.assertEqual(response.status_code, 404)
        self.assertIn('No hay pedidos', json.loads(response.content)['error'])

    def test_contexto_toma_talla_y_stock_del_erp(self):
        """La guía muestra el dato del ERP, no el del canal: es lo que la
        vendedora va a buscar al estante."""
        ctx = _ctx_guia_pdf(self.pedido)
        self.assertEqual(ctx['numero_ticket_rm'], 'RM-PDFEP1')
        self.assertEqual(len(ctx['items']), 1)
        item = ctx['items'][0]
        self.assertEqual(item['cantidad'], 2)
        self.assertIn('stock_disponible', item)
        self.assertIn('encontrado', item)
