"""
Tests para el endpoint externo GET /api/precios-actuales/ (PreciosActualesView).

Foco: el cálculo de antigüedad de stock. El endpoint deriva las fechas de
Movimientos_Producto (no de los lotes), y `fecha_antiguedad_stock` /
`dias_antiguedad_stock` se calculan por FIFO (la entrada más vieja aún en mano),
no como la primera ni la última recepción.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import Movimientos_Producto
from .factories import crear_empresa, crear_sucursal, crear_producto_con_talla

API_KEY = 'test-api-key-precios'
STATICFILES_STORAGE_TEST = 'django.contrib.staticfiles.storage.StaticFilesStorage'


@override_settings(
    RETAILMIND_API_KEY=API_KEY,
    STATICFILES_STORAGE=STATICFILES_STORAGE_TEST,
)
class PreciosActualesFechasTest(TestCase):
    def setUp(self):
        self.empresa = crear_empresa(rut='76.111.222-3')
        self.sucursal = crear_sucursal(empresa=self.empresa)
        self.producto, self.producto_talla = crear_producto_con_talla(
            self.sucursal, sku=5550001, stock=10, costo=15000, precioventa=20000,
        )
        self.url = reverse('external-precios-actuales')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {API_KEY}'}

    def _get(self, **params):
        params.setdefault('rut_empresa', self.empresa.rut)
        return self.client.get(self.url, params, **self.auth)

    def _mov(self, dias_atras, cantidad, concepto='INGRESO_INICIAL',
             tipo='INGRESO', pt=None, ref=None):
        return Movimientos_Producto.objects.create(
            ProductoTalla=pt or self.producto_talla,
            tipo_movimiento=tipo,
            concepto=concepto,
            cantidad=cantidad,
            fecha=timezone.localdate() - timedelta(days=dias_atras),
            referencia_externa=ref,
        )

    def _item(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()['data'][0]

    def test_responde_200(self):
        self._mov(dias_atras=10, cantidad=10)
        body = self._get().json()
        self.assertTrue(body['success'])
        self.assertEqual(body['total'], 1)

    def test_dias_antiguedad_es_fifo_no_primera_ni_ultima(self):
        # stock=10. Ingresos: 6u hace 5 días + 100u hace 40 días.
        # FIFO (vende lo viejo primero) → el stock en mano son los 6 recientes
        # + 4 del lote viejo → el más viejo en mano es el de hace 40 días.
        self._mov(dias_atras=5, cantidad=6)
        self._mov(dias_atras=40, cantidad=100)

        item = self._item()

        self.assertIsInstance(item['dias_antiguedad_stock'], int)
        self.assertEqual(item['dias_antiguedad_stock'], 40)            # FIFO
        self.assertEqual(item['fecha_antiguedad_stock'],
                         (timezone.localdate() - timedelta(days=40)).strftime('%Y-%m-%d'))

    def test_ultima_fecha_ingreso_es_la_mas_reciente(self):
        self._mov(dias_atras=5, cantidad=6)
        self._mov(dias_atras=40, cantidad=100)

        item = self._item()

        self.assertEqual(item['ultima_fecha_ingreso'],
                         (timezone.localdate() - timedelta(days=5)).strftime('%Y-%m-%d'))

    def test_fifo_cubierto_por_un_solo_ingreso_reciente(self):
        # Si la última recepción ya cubre todo el stock, antigüedad = esa fecha.
        self._mov(dias_atras=3, cantidad=50)
        self._mov(dias_atras=200, cantidad=100)

        item = self._item()

        self.assertEqual(item['dias_antiguedad_stock'], 3)

    def test_stock_actual_y_ultima_venta(self):
        self._mov(dias_atras=10, cantidad=10)
        self._mov(dias_atras=2, cantidad=-1, concepto='VENTA_PUBLICO', tipo='EGRESO')

        item = self._item()

        self.assertEqual(item['stock_actual'], 10)
        self.assertEqual(item['ultima_fecha_venta'],
                         (timezone.localdate() - timedelta(days=2)).strftime('%Y-%m-%d'))

    def test_dias_sin_venta_desde_ultima_venta(self):
        self._mov(dias_atras=200, cantidad=10, concepto='INGRESO_INICIAL')
        self._mov(dias_atras=15, cantidad=-1, concepto='VENTA_PUBLICO', tipo='EGRESO')

        item = self._item()

        self.assertEqual(item['dias_sin_venta'], 15)

    def test_dias_sin_venta_sin_ventas_usa_fecha_creacion(self):
        # Nunca vendió → estancamiento se mide desde la creación del producto.
        self._mov(dias_atras=200, cantidad=10, concepto='INGRESO_INICIAL')
        fecha_alta = timezone.now() - timedelta(days=400)
        # fecha_creacion es auto_now_add; se fija por queryset para el test.
        from app.models import Producto
        Producto.objects.filter(pk=self.producto.pk).update(fecha_creacion=fecha_alta)

        item = self._item()

        self.assertIsNone(item['ultima_fecha_venta'])
        self.assertEqual(item['dias_sin_venta'], 400)

    def test_traspaso_y_venta_no_cuentan_como_recepcion(self):
        # Solo una recepción real (vieja). Un TRASPASO_ENTRADA y una
        # VENTA_MAYORISTA recientes NO deben tomarse como "llegada de stock".
        self._mov(dias_atras=300, cantidad=10, concepto='INGRESO_INICIAL')
        self._mov(dias_atras=5, cantidad=10, concepto='TRASPASO_ENTRADA')
        # VENTA_MAYORISTA mal tipada como INGRESO (caso real de datos migrados)
        self._mov(dias_atras=3, cantidad=5, concepto='VENTA_MAYORISTA', tipo='INGRESO')

        item = self._item()

        # La antigüedad debe salir de la recepción de hace 300 días, no del
        # traspaso (5) ni de la venta (3).
        self.assertEqual(item['dias_antiguedad_stock'], 300)
        self.assertEqual(item['ultima_fecha_ingreso'],
                         (timezone.localdate() - timedelta(days=300)).strftime('%Y-%m-%d'))
        # La VENTA_MAYORISTA sí debe contar como última venta (por concepto).
        self.assertEqual(item['ultima_fecha_venta'],
                         (timezone.localdate() - timedelta(days=3)).strftime('%Y-%m-%d'))

    def test_traspaso_sucursal_cuenta_como_ultima_fecha_ingreso(self):
        # El despacho interno bodega→tienda del legado (VentaXInterna) se migró
        # como concepto TRASPASO_SUCURSAL / tipo INGRESO. Esa ES la definición de
        # negocio de `ultima_fecha_ingreso`, así que debe contar como la última
        # entrada de mercadería (a diferencia de TRASPASO_ENTRADA del flujo nuevo).
        self._mov(dias_atras=300, cantidad=10, concepto='INGRESO_INICIAL')
        self._mov(dias_atras=12, cantidad=10, concepto='TRASPASO_SUCURSAL',
                  tipo='INGRESO', ref='MIG:99999')

        item = self._item()

        self.assertEqual(item['ultima_fecha_ingreso'],
                         (timezone.localdate() - timedelta(days=12)).strftime('%Y-%m-%d'))
        # Pero la antigüedad FIFO de stock COMPRADO no se mueve por el traspaso.
        self.assertEqual(item['dias_antiguedad_stock'], 300)

    def test_traspaso_entrada_no_cuenta_como_ultima_fecha_ingreso(self):
        # TRASPASO_ENTRADA (traspaso inter-tienda del flujo NUEVO) NO es llegada
        # de mercadería nueva: no debe mover `ultima_fecha_ingreso`.
        self._mov(dias_atras=200, cantidad=10, concepto='RECEPCION_COMPRA')
        self._mov(dias_atras=4, cantidad=10, concepto='TRASPASO_ENTRADA',
                  tipo='INGRESO')

        item = self._item()

        self.assertEqual(item['ultima_fecha_ingreso'],
                         (timezone.localdate() - timedelta(days=200)).strftime('%Y-%m-%d'))

    def test_traspaso_sucursal_para_sku_sin_stock(self):
        # El legado devuelve ultima_fecha_ingreso para TODO SKU, también con
        # stock 0. La nueva agregación no se acota a stock>0.
        self.producto_talla.stock = 0
        self.producto_talla.save(update_fields=['stock'])
        self._mov(dias_atras=30, cantidad=10, concepto='TRASPASO_SUCURSAL',
                  tipo='INGRESO', ref='MIG:88888')

        item = self._item()

        self.assertEqual(item['ultima_fecha_ingreso'],
                         (timezone.localdate() - timedelta(days=30)).strftime('%Y-%m-%d'))

    def test_sin_stock_antiguedad_none(self):
        Movimientos_Producto.objects.create(
            ProductoTalla=self.producto_talla, tipo_movimiento='INGRESO',
            cantidad=10, fecha=timezone.localdate() - timedelta(days=30),
        )
        # stock = 0 → no se calcula antigüedad
        self.producto_talla.stock = 0
        self.producto_talla.save(update_fields=['stock'])

        item = self._item()

        self.assertIsNone(item['fecha_antiguedad_stock'])
        self.assertIsNone(item['dias_antiguedad_stock'])

    def test_sin_movimientos_cae_a_fecha_creacion(self):
        # Sin recepción registrada (entró solo por traspaso/ventas o nunca tuvo
        # movimiento) pero CON stock: tanto ultima_fecha_ingreso como la
        # antigüedad caen a fecha_creacion (antigüedad real), nunca null.
        from app.models import Producto
        fecha_alta = timezone.now() - timedelta(days=120)
        Producto.objects.filter(pk=self.producto.pk).update(fecha_creacion=fecha_alta)

        item = self._item()

        esperado = timezone.localtime(fecha_alta).date().strftime('%Y-%m-%d')
        self.assertEqual(item['ultima_fecha_ingreso'], esperado)
        self.assertEqual(item['fecha_antiguedad_stock'], esperado)
        self.assertEqual(item['dias_antiguedad_stock'], 120)

    def test_saldo_inicial_sintetico_no_cuenta_como_recepcion(self):
        # El saldo de apertura sintético de la migración (ref MIGRACION_LARAVEL)
        # tiene fecha de la carga, no la recepción real. Debe ignorarse y la
        # antigüedad caer a fecha_creacion (= antigüedad real).
        from app.models import Producto
        fecha_alta = timezone.now() - timedelta(days=300)
        Producto.objects.filter(pk=self.producto.pk).update(fecha_creacion=fecha_alta)
        # Único ingreso = saldo sintético reciente (hace 5 días).
        self._mov(dias_atras=5, cantidad=10, concepto='INGRESO_INICIAL',
                  ref='MIGRACION_LARAVEL')

        item = self._item()

        esperado = timezone.localtime(fecha_alta).date().strftime('%Y-%m-%d')
        self.assertEqual(item['ultima_fecha_ingreso'], esperado)   # no la de hace 5 días
        self.assertEqual(item['fecha_antiguedad_stock'], esperado)
        self.assertEqual(item['dias_antiguedad_stock'], 300)

    def test_recepcion_real_prevalece_sobre_saldo_sintetico(self):
        # Recepción real (hace 20 días) + saldo sintético (hace 2 días).
        # Debe ganar la recepción real, no el saldo sintético.
        self._mov(dias_atras=2, cantidad=10, concepto='INGRESO_INICIAL',
                  ref='MIGRACION_LARAVEL')
        self._mov(dias_atras=20, cantidad=10, concepto='RECEPCION_COMPRA')

        item = self._item()

        self.assertEqual(item['ultima_fecha_ingreso'],
                         (timezone.localdate() - timedelta(days=20)).strftime('%Y-%m-%d'))

    def test_rut_obligatorio(self):
        resp = self.client.get(self.url, **self.auth)
        self.assertEqual(resp.status_code, 400)

    def test_sin_api_key_rechaza(self):
        resp = self.client.get(self.url, {'rut_empresa': self.empresa.rut})
        self.assertIn(resp.status_code, (401, 403))
