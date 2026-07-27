"""
Tests del flujo de tallas: orden natural en productos-sucursal y flag
`omitir_tallas_sin_stock` de Crear Producto Manual ("Completar desde guía").

Ejecutar (en entorno con BD de test, NO producción):
    python manage.py test app.tests.test_crear_manual_tallas
"""
from django.test import SimpleTestCase, TestCase

from app.models import (
    AtributoOpcion, Categoria, Dte, Producto, Producto_Talla,
    Productos_Atributos,
)
from app.tests.factories import (
    crear_correlativo, crear_empresa, crear_empresa_user, crear_sucursal,
    crear_usuario,
)
from app.utils_tallas import clave_orden_talla


class TestClaveOrdenTalla(SimpleTestCase):
    def test_numericas_primero_en_orden_natural(self):
        desordenadas = ['10', '7,5', 'XL', '2', '1.5', 'S', '36']
        ordenadas = sorted(desordenadas, key=clave_orden_talla)
        self.assertEqual(ordenadas, ['1.5', '2', '7,5', '10', '36', 'S', 'XL'])

    def test_vacias_y_none_no_revientan(self):
        self.assertEqual(sorted(['', None, '5'], key=clave_orden_talla)[-1], '')


class BaseModalManual(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = crear_usuario(username='bodeguero', rol='administrador')
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(empresa=cls.empresa, alias='NICK1')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)

        cls.cat = Categoria.objects.create(nombre='Sandalias y Chalas')
        attr_marca = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        attr_color = Productos_Atributos.objects.create(nombre='Color', descripcion='Color')
        attr_gen = Productos_Atributos.objects.create(nombre='Género', descripcion='Género')
        cls.marca = AtributoOpcion.objects.create(atributo=attr_marca, valor='ADIDAS')
        cls.color = AtributoOpcion.objects.create(atributo=attr_color, valor='MULTI')
        cls.genero = AtributoOpcion.objects.create(atributo=attr_gen, valor='HOMBRE')

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['idSucursalActual'] = self.sucursal.id
        session['nombreUsuario'] = 'Tester'
        session.save()

    def _crear_producto(self, articulo='F35543', tallas=()):
        producto = Producto.objects.create(
            articulo=articulo, descripcion='SANDALIA ADILETTE',
            sucursal=self.sucursal, atributo1=self.marca,
            atributo2=self.color, atributo3=self.genero,
            categoria=self.cat, costo=11330, sobreprecio=1473,
            precioventa=21990, tipo_talla='CL',
        )
        pts = [
            Producto_Talla.objects.create(
                producto=producto, talla=talla, sku=sku, stock=stock)
            for talla, sku, stock in tallas
        ]
        return producto, pts


class TestOrdenTallasProductosSucursal(BaseModalManual):
    URL = '/app/api/productos-sucursal/'

    def test_tallas_salen_en_orden_natural(self):
        # Insertadas a propósito en desorden (así llegan de la BD real)
        self._crear_producto(tallas=[
            ('10', 4713001, 3), ('7,5', 4713002, 1), ('XL', 4713003, 2),
            ('2', 4713004, 5), ('36', 4713005, 0), ('S', 4713006, 4),
        ])
        resp = self.client.get(self.URL, {'sucursal_id': self.sucursal.id})
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertEqual(len(data['productos']), 1)
        tallas = [t['talla'] for t in data['productos'][0]['tallas_stock']]
        self.assertEqual(tallas, ['2', '7,5', '10', '36', 'S', 'XL'])

    def test_orden_por_stock_sigue_funcionando(self):
        self._crear_producto(tallas=[
            ('1', 4714001, 1), ('2', 4714002, 9), ('3', 4714003, 5),
        ])
        resp = self.client.get(self.URL, {
            'sucursal_id': self.sucursal.id, 'ordenar': 'stock_desc'})
        data = resp.json()
        self.assertTrue(data['success'], data)
        stocks = [t['stock'] for t in data['productos'][0]['tallas_stock']]
        self.assertEqual(stocks, sorted(stocks, reverse=True))


class TestOmitirTallasSinStock(BaseModalManual):
    URL = '/app/crear_producto_manual/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.proveedor = crear_empresa(nombre='Proveedor Test', rut='77.111.111-1')
        crear_correlativo(cls.sucursal, tipo_dte='COMPRA')
        cls.dte = Dte.objects.create(
            emisor=cls.proveedor, receptor=cls.empresa,
            numero_documento=555, tipo_documento='FACTURA',
            monto_neto=10000, monto_con_iva=11900,
            estado_pago='PENDIENTE', estado_dte='EMITIDO',
            responsable='tester', fecha_emision='2026-07-01',
            fecha_vencimiento='2026-07-30', diasCredito=30,
            bultos=1, unidades_productos=3,
            tipo_transaccion='COMPRA', sucursal=cls.sucursal,
        )

    def _post(self, tallas, stocks, **extra):
        payload = {
            'es_manual': 'true',
            'proveedor': self.proveedor.id,
            'dte_manual': self.dte.id,
            'articulo': 'F35543',
            'atributo1': self.marca.id,
            'atributo2': self.color.id,
            'atributo3': self.genero.id,
            'categoria': self.cat.id,
            'tipo_talla': 'CL',
            'costo': '11330',
            'sobreprecio': '1473',
            'precioventa': '21990',
            'talla[]': tallas,
            'stock[]': stocks,
            'sku[]': ['' for _ in tallas],
        }
        payload.update(extra)
        return self.client.post(self.URL, payload)

    def test_con_flag_no_crea_tallas_nuevas_en_cero(self):
        # Producto existente con tallas 1 y 2; llega la 1.5. "Completar desde
        # guía" manda la curva completa: 1 y 2 en 0 (existentes, se reutilizan)
        # y también una talla nueva '3' en 0 que NO debe crearse.
        producto, _ = self._crear_producto(tallas=[
            ('1', 4713093, 4), ('2', 4713091, 2),
        ])
        resp = self._post(
            ['1', '1.5', '2', '3'], ['0', '3', '0', '0'],
            omitir_tallas_sin_stock='true',
        )
        data = resp.json()
        self.assertTrue(data['success'], data)

        tallas = set(Producto_Talla.objects.filter(
            producto=producto).values_list('talla', flat=True))
        self.assertIn('1.5', tallas)       # la que llegó con stock: creada
        self.assertNotIn('3', tallas)      # nueva en 0: omitida
        self.assertEqual(tallas, {'1', '1.5', '2'})
        pt_nueva = Producto_Talla.objects.get(producto=producto, talla='1.5')
        self.assertEqual(pt_nueva.stock, 3)  # el movimiento sumó las unidades

    def test_sin_flag_mantiene_comportamiento_historico(self):
        # Sin el flag, una talla nueva en 0 SÍ se crea (curva completa manual)
        producto, _ = self._crear_producto(tallas=[('1', 4713095, 4)])
        resp = self._post(['1.5', '3'], ['2', '0'])
        data = resp.json()
        self.assertTrue(data['success'], data)
        tallas = set(Producto_Talla.objects.filter(
            producto=producto).values_list('talla', flat=True))
        self.assertEqual(tallas, {'1', '1.5', '3'})
        self.assertEqual(Producto_Talla.objects.get(
            producto=producto, talla='3').stock, 0)

    def test_flag_con_todo_en_cero_no_crea_nada(self):
        # Guard previo del backend: total 0 → error claro, sin producto nuevo
        resp = self._post(['1', '2'], ['0', '0'], omitir_tallas_sin_stock='true')
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertFalse(Producto.objects.filter(articulo='F35543').exists())


class TestActividadCreacionManual(BaseModalManual):
    """Smoke test del endpoint que alimenta los KPIs/tabla de verGestionProducto."""
    URL = '/app/api/actividad-creacion-manual/'

    def test_kpis_y_agrupacion(self):
        from datetime import timedelta
        from django.utils import timezone
        from app.models import Movimientos_Producto

        producto, pts = self._crear_producto(tallas=[
            ('1', 4715001, 0), ('2', 4715002, 0),
        ])
        hoy = timezone.localdate()
        # Dos tallas ingresadas hoy (mismo evento) + un ingreso fuera de la
        # ventana de 30 días que NO debe contar
        for pt, cant in [(pts[0], 3), (pts[1], 2)]:
            Movimientos_Producto.objects.create(
                ProductoTalla=pt, concepto='INGRESO_MANUAL',
                tipo_movimiento='INGRESO', cantidad=cant, costo=1000,
                fecha=hoy, responsable='Tester',
                sucursal_origen=self.sucursal, sucursal_destino=self.sucursal,
            )
        Movimientos_Producto.objects.create(
            ProductoTalla=pts[0], concepto='INGRESO_MANUAL',
            tipo_movimiento='INGRESO', cantidad=9, costo=1000,
            fecha=hoy - timedelta(days=40), responsable='Tester',
            sucursal_origen=self.sucursal, sucursal_destino=self.sucursal,
        )

        resp = self.client.get(self.URL)
        data = resp.json()
        self.assertTrue(data['success'], data)
        self.assertEqual(data['kpis']['hoy_productos'], 1)
        self.assertEqual(data['kpis']['hoy_unidades'], 5)
        self.assertEqual(data['kpis']['hoy_valor'], 5000)
        self.assertEqual(data['kpis']['mes_unidades'], 5)  # el viejo queda fuera

        self.assertEqual(len(data['actividad']), 1)  # 2 tallas = 1 evento
        evento = data['actividad'][0]
        self.assertEqual(evento['unidades'], 5)
        self.assertEqual(evento['tallas'], ['1', '2'])
        self.assertTrue(evento['es_nuevo'])  # la ficha se creó hoy (en el test)
