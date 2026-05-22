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
             tipo='INGRESO', pt=None):
        return Movimientos_Producto.objects.create(
            ProductoTalla=pt or self.producto_talla,
            tipo_movimiento=tipo,
            concepto=concepto,
            cantidad=cantidad,
            fecha=timezone.localdate() - timedelta(days=dias_atras),
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

    def test_sin_movimientos_ultima_fecha_ingreso_cae_a_fecha_creacion(self):
        # Sin recepción registrada (entró solo por traspaso/ventas o nunca tuvo
        # movimiento), ultima_fecha_ingreso cae a fecha_creacion del producto
        # para que el ecommerce siempre tenga una fecha no-null que mostrar.
        # La antigüedad FIFO sí queda en None (no hay ingresos reales).
        from app.models import Producto
        fecha_alta = timezone.now() - timedelta(days=120)
        Producto.objects.filter(pk=self.producto.pk).update(fecha_creacion=fecha_alta)

        item = self._item()

        self.assertEqual(item['ultima_fecha_ingreso'],
                         timezone.localtime(fecha_alta).date().strftime('%Y-%m-%d'))
        self.assertIsNone(item['fecha_antiguedad_stock'])
        self.assertIsNone(item['dias_antiguedad_stock'])

    def test_rut_obligatorio(self):
        resp = self.client.get(self.url, **self.auth)
        self.assertEqual(resp.status_code, 400)

    def test_sin_api_key_rechaza(self):
        resp = self.client.get(self.url, {'rut_empresa': self.empresa.rut})
        self.assertIn(resp.status_code, (401, 403))
