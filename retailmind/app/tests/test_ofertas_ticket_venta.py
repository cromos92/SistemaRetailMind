"""
Tests de las ofertas de liquidación en el "ticket vendedor" (ticket-venta):
endpoint de ofertas, persistencia de la línea gratis NxM y del precio de
liquidación en crear_ticket, validaciones, exposición de flags de promo para
la caja, y la búsqueda de boleta del cambio por folio del DTE.

Aislados en SQLite (test_settings_sqlite). NO tocan producción.
"""
import json

from django.test import RequestFactory, TestCase
from django.utils import timezone

from app.models import (
    CampanaLiquidacion, CampanaLiquidacionProducto, Ticket,
)
from app.services import campanas_service
from app.views import crear_ticket
from app.views_modulo_ventas import (
    buscar_ticket_para_cambio, construir_ticket_data as construir_ticket_data_caja,
)
from app.views_modulo_campanas_liquidacion import obtener_ofertas_activas
from app.tests.factories import (
    crear_correlativo, crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario, crear_vendedor,
)

SKU = 9000001


class OfertasTicketVentaTests(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='T1')
        self.vendedor = crear_vendedor(empresa=self.empresa)
        self.vendedor.sucursales.add(self.sucursal)
        self.user = crear_usuario(username='cajero')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        crear_correlativo(self.sucursal, tipo_dte='TICKET')
        self.producto, self.talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAP', sku=SKU,
            costo=10000, precioventa=20000, stock=10)

    def _post(self, payload):
        req = RequestFactory().post('/x', data=json.dumps(payload),
                                    content_type='application/json')
        req.user = self.user
        req.session = {'idSucursalActual': self.sucursal.id}
        return json.loads(crear_ticket(req).content)

    def _campana_nxm(self):
        c = CampanaLiquidacion.objects.create(
            nombre='2x1', tipo_regla='NXM', nxm_n=2, nxm_m=1,
            fecha_inicio=timezone.now(), estado='ACTIVA')
        c.sucursales.add(self.sucursal)
        CampanaLiquidacionProducto.objects.create(
            campana=c, producto=self.producto, activo=True, estado='APLICADO')
        return c

    def _linea(self, cant, precio=20000, promo=None):
        d = {'sku': SKU, 'producto_talla_id': self.talla.id,
             'cantidad': cant, 'precio': precio}
        if promo:
            d.update(descuento_unitario=precio, es_promo_nxm=True, promo_campana_id=promo.id)
        return d

    # ---- endpoint de ofertas ----
    def test_ofertas_activas_incluye_precio_fijo(self):
        c = CampanaLiquidacion.objects.create(
            nombre='Liq', tipo_regla='PORCENTAJE', valor_porcentaje=30,
            fecha_inicio=timezone.now())
        c.sucursales.add(self.sucursal)
        CampanaLiquidacionProducto.objects.create(campana=c, producto=self.producto)
        campanas_service.aplicar_precios_campana(c)  # precioventa 20000 -> 14000
        req = RequestFactory().get('/x')
        req.user = self.user
        req.session = {'idSucursalActual': self.sucursal.id}
        data = json.loads(obtener_ofertas_activas(req).content)
        self.assertTrue(data['success'])
        oferta = data['ofertas'][str(SKU)]
        self.assertEqual(oferta['precio_original'], 20000)
        self.assertEqual(oferta['precio_liquidacion'], 14000)
        self.assertEqual(oferta['tipo'], 'PORCENTAJE')

    # ---- crear_ticket con oferta horneada ----
    def test_crear_ticket_persiste_nxm(self):
        c = self._campana_nxm()
        resp = self._post({'vendedor_id': self.vendedor.id, 'total': 20000,
                           'total_items': 2,
                           'productos': [self._linea(1), self._linea(1, promo=c)]})
        self.assertTrue(resp['success'], resp.get('message'))
        ticket = Ticket.objects.get(correlativo=resp['ticket_id'], sucursal=self.sucursal)
        self.assertEqual(ticket.estado, 'PENDIENTE')
        lineas = list(ticket.ticket_productos.all())
        self.assertEqual(len(lineas), 2)
        gratis = [l for l in lineas if l.promo_campana_id]
        self.assertEqual(len(gratis), 1)
        self.assertEqual(gratis[0].descuento_unitario, gratis[0].precio)
        self.assertEqual(gratis[0].subtotal, 0)
        self.assertEqual(ticket.descuento, 20000)
        self.assertEqual(ticket.total, 20000)   # 1 paga + 1 gratis

    def test_crear_ticket_rechaza_nxm_invalido(self):
        c = self._campana_nxm()
        # 2x1 con 1 unidad total pero declara 1 gratis -> excede el máximo (0).
        resp = self._post({'vendedor_id': self.vendedor.id, 'total': 0,
                           'total_items': 1,
                           'productos': [self._linea(1, promo=c)]})
        self.assertFalse(resp['success'])
        self.assertEqual(Ticket.objects.count(), 0)

    def test_crear_ticket_valida_stock_total_por_sku(self):
        # stock=10; 6+5 del mismo SKU en dos líneas -> 11 > 10 -> falla.
        resp = self._post({'vendedor_id': self.vendedor.id, 'total': 220000,
                           'total_items': 11,
                           'productos': [self._linea(6), self._linea(5)]})
        self.assertFalse(resp['success'])
        self.assertEqual(Ticket.objects.count(), 0)

    def test_crear_ticket_fuerza_precio_liquidacion(self):
        c = CampanaLiquidacion.objects.create(
            nombre='Liq', tipo_regla='PRECIO_FIJO', valor_precio_fijo=12000,
            fecha_inicio=timezone.now())
        c.sucursales.add(self.sucursal)
        CampanaLiquidacionProducto.objects.create(campana=c, producto=self.producto)
        campanas_service.aplicar_precios_campana(c)  # precioventa -> 12000
        # El cliente manda 20000; el servidor debe forzar el precio de liquidación.
        resp = self._post({'vendedor_id': self.vendedor.id, 'total': 20000,
                           'total_items': 1,
                           'productos': [self._linea(1, precio=20000)]})
        self.assertTrue(resp['success'], resp.get('message'))
        ticket = Ticket.objects.get(correlativo=resp['ticket_id'])
        self.assertEqual(ticket.ticket_productos.get().precio, 12000)

    def test_construir_ticket_data_expone_promo(self):
        c = self._campana_nxm()
        resp = self._post({'vendedor_id': self.vendedor.id, 'total': 20000,
                           'total_items': 2,
                           'productos': [self._linea(1), self._linea(1, promo=c)]})
        ticket = Ticket.objects.get(correlativo=resp['ticket_id'])
        data = construir_ticket_data_caja(ticket)
        promo = [p for p in data['productos'] if p['es_promo_nxm']]
        self.assertEqual(len(promo), 1)
        self.assertIsNotNone(promo[0]['promo_campana_id'])
        self.assertTrue(promo[0]['promo_label'])

    # ---- cambio: buscar por folio del DTE ----
    def test_buscar_ticket_cambio_por_folio_dte(self):
        t = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=555,
            subTotal=20000, total=20000, responsable='x', folio_dte=987654,
            estado='PAGADO')
        from app.models import Ticket_Productos
        Ticket_Productos.objects.create(
            idTicket=t, ProductoTalla=self.talla, stock=1, precio=20000, subtotal=20000)
        req = RequestFactory().get('/x', data={'correlativo': '987654'})
        req.user = self.user
        req.session = {'idSucursalActual': self.sucursal.id}
        data = json.loads(buscar_ticket_para_cambio(req).content)
        self.assertTrue(data['success'], data.get('error'))
