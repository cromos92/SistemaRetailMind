"""
Tests del servicio de campañas de liquidación: aplicar/revertir precios,
piso de costo, conflicto por cambio manual, NxM sin tocar precio, cierre
automático de vencidas y el constraint anti-colisión.

Aislados en SQLite (esquema desde modelos). NO tocan producción.
"""
from datetime import timedelta

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from app.models import (
    CampanaLiquidacion, CampanaLiquidacionProducto, HistorialCambioPrecio,
)
from app.services import campanas_service
from app.tests.factories import crear_sucursal, crear_producto_con_talla


class CampanasLiquidacionTests(TestCase):
    def setUp(self):
        self.sucursal = crear_sucursal(alias='TIENDA-1')
        self.producto, self.talla = crear_producto_con_talla(
            self.sucursal, articulo='ZAP-1', sku=5000001,
            costo=10000, precioventa=30000, stock=8)

    def _campana(self, tipo_regla, **kw):
        defaults = dict(
            nombre='Camp Test', tipo_regla=tipo_regla,
            fecha_inicio=timezone.now(), respetar_piso_costo=True)
        defaults.update(kw)
        c = CampanaLiquidacion.objects.create(**defaults)
        c.sucursales.add(self.sucursal)
        CampanaLiquidacionProducto.objects.create(campana=c, producto=self.producto)
        return c

    def test_aplicar_porcentaje_baja_precio_y_audita(self):
        c = self._campana('PORCENTAJE', valor_porcentaje=30)
        res = campanas_service.aplicar_precios_campana(c)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precioventa, 21000)  # 30000 * 0.7
        self.assertEqual(res['aplicados'], 1)
        c.refresh_from_db()
        self.assertEqual(c.estado, 'ACTIVA')
        item = c.items.get()
        self.assertEqual(item.estado, 'APLICADO')
        self.assertTrue(item.activo)
        self.assertEqual(item.precio_original, 30000)
        self.assertEqual(item.precio_liquidacion, 21000)
        hist = HistorialCambioPrecio.objects.filter(
            producto=self.producto, tipo_cambio='CAMPANA_LIQUIDACION')
        self.assertEqual(hist.count(), 1)

    def test_aplicar_respeta_piso_costo(self):
        # 90% de descuento dejaría el precio en 3000 < costo*1.1 (11000).
        c = self._campana('PORCENTAJE', valor_porcentaje=90)
        res = campanas_service.aplicar_precios_campana(c)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precioventa, 11000)  # piso costo*1.1
        self.assertEqual(res['clamps'], 1)

    def test_precio_fijo(self):
        c = self._campana('PRECIO_FIJO', valor_precio_fijo=15000)
        campanas_service.aplicar_precios_campana(c)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precioventa, 15000)

    def test_nxm_no_toca_precio(self):
        c = self._campana('NXM', nxm_n=2, nxm_m=1)
        campanas_service.aplicar_precios_campana(c)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precioventa, 30000)  # sin cambios
        item = c.items.get()
        self.assertEqual(item.estado, 'APLICADO')
        self.assertIsNone(item.precio_liquidacion)
        self.assertTrue(item.activo)

    def test_revertir_restaura_precio(self):
        c = self._campana('PORCENTAJE', valor_porcentaje=30)
        campanas_service.aplicar_precios_campana(c)
        res = campanas_service.revertir_precios_campana(c)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precioventa, 30000)
        self.assertEqual(res['revertidos'], 1)
        c.refresh_from_db()
        self.assertEqual(c.estado, 'FINALIZADA')
        self.assertFalse(c.items.get().activo)
        self.assertEqual(
            HistorialCambioPrecio.objects.filter(
                producto=self.producto, tipo_cambio='REVERSION_CAMPANA').count(), 1)

    def test_cambio_manual_durante_campana_marca_conflicto(self):
        c = self._campana('PORCENTAJE', valor_porcentaje=30)
        campanas_service.aplicar_precios_campana(c)
        # Alguien cambia el precio a mano (distinto del de liquidación).
        self.producto.precioventa = 25000
        self.producto.save(update_fields=['precioventa'])
        res = campanas_service.revertir_precios_campana(c)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precioventa, 25000)  # NO se pisa
        self.assertEqual(res['conflictos'], 1)
        self.assertEqual(c.items.get().estado, 'CONFLICTO')

    def test_colision_producto_en_dos_campanas_activas(self):
        c1 = self._campana('PORCENTAJE', valor_porcentaje=20)
        campanas_service.aplicar_precios_campana(c1)
        c2 = self._campana('PORCENTAJE', valor_porcentaje=40)
        # El constraint parcial uniq_producto_en_campana_activa debe impedir
        # que el mismo producto quede activo en dos campañas.
        with self.assertRaises(IntegrityError):
            campanas_service.aplicar_precios_campana(c2)

    def test_cerrar_vencidas_idempotente(self):
        c = self._campana('PORCENTAJE', valor_porcentaje=30,
                           fecha_fin=timezone.now() - timedelta(hours=1))
        campanas_service.aplicar_precios_campana(c)
        n1 = campanas_service.cerrar_campanas_vencidas()
        n2 = campanas_service.cerrar_campanas_vencidas()
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0)  # segunda pasada no hace nada
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precioventa, 30000)
        c.refresh_from_db()
        self.assertEqual(c.estado, 'FINALIZADA')
