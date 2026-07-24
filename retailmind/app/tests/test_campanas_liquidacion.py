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

import json

from django.test import RequestFactory

from app.models import (
    AtributoOpcion, CampanaLiquidacion, CampanaLiquidacionProducto,
    HistorialCambioPrecio, ModuloSistema, OpcionMenu, PermisoRol,
    Productos_Atributos,
)
from app.services import campanas_service
from app.views_modulo_campanas_liquidacion import crear_campana_liquidacion
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_sucursal,
    crear_producto_con_talla, crear_usuario,
)


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

    def _grant_permiso(self, rol, codigo, **flags):
        """Siembra el permiso de rol que exige la vista gateada."""
        modulo, _ = ModuloSistema.objects.get_or_create(
            codigo='liquidacion', defaults={'nombre': 'Liquidacion', 'orden': 11})
        opcion, _ = OpcionMenu.objects.get_or_create(
            codigo=codigo, defaults={'modulo': modulo, 'nombre': codigo, 'orden': 1})
        defaults = {'puede_ver': True}
        defaults.update(flags)
        PermisoRol.objects.update_or_create(
            rol=rol, opcion_menu=opcion, defaults=defaults)

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

    def test_crear_expande_articulo_a_varias_sucursales(self):
        # Mismo artículo (marca+color) en 3 sucursales; el usuario selecciona
        # solo la fila de una tienda pero pide alcance "todas".
        empresa = crear_empresa(nombre='Emp Expand', rut='77.111.111-1')
        marca_attr = Productos_Atributos.objects.create(nombre='Marca', descripcion='m')
        color_attr = Productos_Atributos.objects.create(nombre='Color', descripcion='c')
        nike = AtributoOpcion.objects.create(atributo=marca_attr, valor='Nike')
        azul = AtributoOpcion.objects.create(atributo=color_attr, valor='Azul')
        t1 = crear_sucursal(empresa=empresa, alias='T1')
        t2 = crear_sucursal(empresa=empresa, alias='T2')
        cd = crear_sucursal(empresa=empresa, alias='CD', es_centro_distribucion=True,
                            tipo_sucursal='CENTRO_DISTRIBUCION')
        p1, _ = crear_producto_con_talla(t1, articulo='AIR-1', sku=5100001,
                                         atributo1=nike, atributo2=azul, precioventa=40000)
        crear_producto_con_talla(t2, articulo='AIR-1', sku=5100002,
                                 atributo1=nike, atributo2=azul, precioventa=40000)
        crear_producto_con_talla(cd, articulo='AIR-1', sku=5100003,
                                 atributo1=nike, atributo2=azul, precioventa=40000)
        user = crear_usuario(username='jefe')
        crear_empresa_user(user, empresa, t1)
        self._grant_permiso(user.rol, 'campanas_liquidacion', puede_crear=True)

        req = RequestFactory().post(
            '/x', data=json.dumps({
                'nombre': 'Liq AIR-1', 'tipo_regla': 'PORCENTAJE',
                'valor_porcentaje': 25, 'producto_ids': [p1.id],
                'alcance_sucursales': 'todas', 'incluir_cd': False,
            }), content_type='application/json')
        req.user = user
        req.session = {'idSucursalActual': t1.id}
        res = json.loads(crear_campana_liquidacion(req).content)
        self.assertTrue(res['success'], res.get('error'))
        # Expandió a T1 y T2 (2 tiendas), NO al CD (incluir_cd=False).
        self.assertEqual(res['n_items'], 2)
        self.assertEqual(res['n_sucursales'], 2)

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
