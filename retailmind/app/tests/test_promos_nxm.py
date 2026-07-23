"""
Tests del motor NxM: validador puro de payload y el endpoint de promos
activas que consume el POS. Aislados en SQLite. NO tocan producción.
"""
import json

from django.test import RequestFactory, TestCase
from django.utils import timezone

from app.models import CampanaLiquidacion, CampanaLiquidacionProducto
from app.services.campanas_service import validar_promos_nxm_payload
from app.views_modulo_campanas_liquidacion import obtener_promos_activas
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_producto_con_talla,
    crear_usuario,
)


class PromosNxMTests(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.sucursal = crear_sucursal(empresa=self.empresa, alias='TIENDA-1')
        self.producto, self.talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-1', sku=6000001,
            costo=10000, precioventa=20000, stock=10)
        # Campaña 2x1 activa que incluye al producto.
        self.camp = CampanaLiquidacion.objects.create(
            nombre='2x1 Test', tipo_regla='NXM', nxm_n=2, nxm_m=1,
            fecha_inicio=timezone.now(), estado='ACTIVA')
        self.camp.sucursales.add(self.sucursal)
        CampanaLiquidacionProducto.objects.create(
            campana=self.camp, producto=self.producto, activo=True,
            estado='APLICADO')

    def _payload(self, cant_normal, cant_gratis):
        lineas = [{'sku': 6000001, 'producto_talla_id': self.talla.id,
                   'cantidad': cant_normal}]
        if cant_gratis:
            lineas.append({'sku': 6000001, 'producto_talla_id': self.talla.id,
                           'cantidad': cant_gratis, 'es_promo_nxm': True,
                           'promo_campana_id': self.camp.id})
        return lineas

    def test_linea_gratis_valida_pasa(self):
        # 2x1 con 2 unidades => 1 gratis permitida.
        res = validar_promos_nxm_payload(self._payload(1, 1), self.sucursal)
        self.assertTrue(res['ok'], res['errores'])
        self.assertEqual(len(res['lineas_ok']), 1)

    def test_gratis_excesiva_falla(self):
        # 2x1 con 3 unidades (2 pagas + 1... pero payload declara 2 gratis).
        res = validar_promos_nxm_payload(self._payload(1, 2), self.sucursal)
        self.assertFalse(res['ok'])
        self.assertTrue(res['errores'])

    def test_sin_campana_para_producto_falla(self):
        otro, talla2 = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-2', sku=6000002, stock=5)
        payload = [{'sku': 6000002, 'producto_talla_id': talla2.id, 'cantidad': 2},
                   {'sku': 6000002, 'producto_talla_id': talla2.id, 'cantidad': 1,
                    'es_promo_nxm': True, 'promo_campana_id': self.camp.id}]
        res = validar_promos_nxm_payload(payload, self.sucursal)
        self.assertFalse(res['ok'])

    def test_payload_sin_promos_es_ok(self):
        res = validar_promos_nxm_payload(self._payload(2, 0), self.sucursal)
        self.assertTrue(res['ok'])
        self.assertEqual(res['lineas_ok'], {})

    def test_obtener_promos_activas_devuelve_la_campana(self):
        user = crear_usuario(username='cajero1')
        crear_empresa_user(user, self.empresa, self.sucursal)
        req = RequestFactory().get('/x', data={'sucursal_id': self.sucursal.id})
        req.user = user
        req.session = {'idSucursalActual': self.sucursal.id}
        data = json.loads(obtener_promos_activas(req).content)
        self.assertTrue(data['success'])
        self.assertEqual(len(data['promos']), 1)
        promo = data['promos'][0]
        self.assertEqual(promo['n'], 2)
        self.assertEqual(promo['m'], 1)
        skus = promo['productos'][0]['skus']
        self.assertIn(6000001, skus)

    def test_campana_vencida_no_aparece(self):
        from datetime import timedelta
        self.camp.fecha_fin = timezone.now() - timedelta(hours=1)
        self.camp.save(update_fields=['fecha_fin'])
        user = crear_usuario(username='cajero2')
        crear_empresa_user(user, self.empresa, self.sucursal)
        req = RequestFactory().get('/x', data={'sucursal_id': self.sucursal.id})
        req.user = user
        req.session = {'idSucursalActual': self.sucursal.id}
        data = json.loads(obtener_promos_activas(req).content)
        self.assertEqual(len(data['promos']), 0)
