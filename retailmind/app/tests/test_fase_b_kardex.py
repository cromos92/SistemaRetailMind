"""
Fase B auditoría de Reportes — kardex por talla y kardex agrupado.

Cubre el fix de la apertura sintética de la migración Laravel
(INGRESO_INICIAL + referencia_externa=REF_SALDO_INICIAL_SINTETICO):

1. SKU con kardex legacy + apertura duplicada -> la apertura NO aporta al
   saldo y el saldo final cuadra con Producto_Talla.stock.
2. SKU cuyo ÚNICO movimiento es la apertura (sin legacy detrás) -> la
   apertura se ANCLA como saldo inicial y el saldo final cuadra con stock.
3. Movimiento no COMPLETADO (CANCELADO) -> se lista pero no suma al saldo.
4. Kardex agrupado: mezcla de tallas (una excluida, otra anclada) -> saldo
   final == stock_total.

Ejecutar SIEMPRE contra SQLite desechable (el .env apunta a prod):

    $env:DATABASE_URL="sqlite:///C:/temp/tb3.sqlite3"
    python manage.py test app.tests.test_fase_b_kardex
"""
import datetime
import json

from django.test import RequestFactory, TestCase

from app.constants_kardex import REF_SALDO_INICIAL_SINTETICO
from app.models import Movimientos_Producto, Producto_Talla
from app.views import reporte_kardex_agrupado, reporte_movimientos_kardex
from .factories import crear_empresa, crear_producto_con_talla, crear_sucursal, crear_usuario

FECHA_LEGACY_1 = datetime.date(2025, 5, 1)
FECHA_LEGACY_2 = datetime.date(2025, 6, 1)
FECHA_APERTURA = datetime.date(2026, 1, 22)
FECHA_POST = datetime.date(2026, 3, 1)
HORA = datetime.time(12, 0)


def _mov(pt, fecha, cantidad, concepto='INGRESO_INICIAL', referencia=None,
         estado='COMPLETADO'):
    return Movimientos_Producto.objects.create(
        ProductoTalla=pt,
        cantidad=cantidad,
        fecha=fecha,
        hora=HORA,
        concepto=concepto,
        estado=estado,
        responsable='Test',
        referencia_externa=referencia,
    )


class KardexAperturaSinteticaBase(TestCase):
    """Infraestructura común: empresa/sucursal/admin + helper de invocación."""

    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(cls.empresa)
        # rol administrador pasa puede_ver_sucursal sin EmpresaUser.
        cls.admin = crear_usuario(username='admin_kardex', rol='administrador')

    def _get_kardex(self, pt, **params):
        request = RequestFactory().get(
            '/app/reporte_movimientos_kardex/',
            data={'producto_talla_id': pt.id, **params},
        )
        request.user = self.admin
        response = reporte_movimientos_kardex(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        return data

    def _get_agrupado(self, producto, **params):
        request = RequestFactory().get(
            '/app/reporte_kardex_agrupado/',
            data={'producto_id': producto.id, **params},
        )
        request.user = self.admin
        response = reporte_kardex_agrupado(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        return data


class KardexPorTallaAperturaTest(KardexAperturaSinteticaBase):

    def test_legacy_mas_apertura_no_duplica_saldo(self):
        """La apertura sintética sobre kardex legacy migrado no suma: el
        saldo final debe ser el stock real, no stock*2."""
        _, pt = crear_producto_con_talla(self.sucursal, sku=9001001, stock=10)
        _mov(pt, FECHA_LEGACY_1, 12, referencia='MIG:1')
        _mov(pt, FECHA_LEGACY_2, -2, concepto='VENTA_PUBLICO', referencia='MIG:2')
        _mov(pt, FECHA_APERTURA, 10, referencia=REF_SALDO_INICIAL_SINTETICO)

        data = self._get_kardex(pt)
        items = data['items']
        self.assertEqual(len(items), 3)
        # Acumulado: 12 -> 10 -> 10 (la apertura no salta el saldo).
        self.assertEqual([i['saldo'] for i in items], [12, 10, 10])
        self.assertEqual(items[-1]['saldo'], pt.stock)
        # La fila de apertura sigue visible, marcada y con su entrada intacta.
        fila_apertura = items[2]
        self.assertTrue(fila_apertura['es_apertura'])
        self.assertEqual(fila_apertura['entrada'], 10)
        self.assertFalse(items[0]['es_apertura'])
        self.assertFalse(items[1]['es_apertura'])

    def test_solo_apertura_ancla_saldo_inicial(self):
        """SKU sin kardex legacy: excluir la apertura dejaría saldo 0 vs
        stock real -> se ancla la apertura como saldo inicial."""
        _, pt = crear_producto_con_talla(self.sucursal, sku=9001002, stock=7)
        _mov(pt, FECHA_APERTURA, 7, referencia=REF_SALDO_INICIAL_SINTETICO)

        data = self._get_kardex(pt)
        items = data['items']
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['es_apertura'])
        self.assertEqual(items[0]['saldo'], 7)
        self.assertEqual(items[0]['saldo'], pt.stock)

    def test_apertura_anclada_con_movimientos_posteriores(self):
        """Anclada la apertura, los movimientos post-migración siguen
        acumulando encima de ella."""
        _, pt = crear_producto_con_talla(self.sucursal, sku=9001003, stock=4)
        _mov(pt, FECHA_APERTURA, 6, referencia=REF_SALDO_INICIAL_SINTETICO)
        _mov(pt, FECHA_POST, -2, concepto='VENTA_PUBLICO')

        data = self._get_kardex(pt)
        self.assertEqual([i['saldo'] for i in data['items']], [6, 4])
        self.assertEqual(data['items'][-1]['saldo'], pt.stock)

    def test_movimiento_cancelado_no_suma(self):
        """Un movimiento no COMPLETADO se lista pero no mueve el saldo."""
        _, pt = crear_producto_con_talla(self.sucursal, sku=9001004, stock=5)
        _mov(pt, FECHA_POST, 5, concepto='RECEPCION_COMPRA')
        _mov(pt, datetime.date(2026, 3, 2), -3, concepto='VENTA_PUBLICO',
             estado='CANCELADO')

        data = self._get_kardex(pt)
        items = data['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['saldo'], 5)
        # La fila cancelada muestra su salida pero el saldo no cambia.
        self.assertEqual(items[1]['estado'], 'CANCELADO')
        self.assertEqual(items[1]['salida'], 3)
        self.assertEqual(items[1]['saldo'], 5)
        self.assertEqual(items[-1]['saldo'], pt.stock)


class KardexAgrupadoAperturaTest(KardexAperturaSinteticaBase):

    def test_agrupado_mezcla_tallas_cuadra_con_stock_total(self):
        """Producto con una talla legacy+apertura (se excluye) y otra
        solo-apertura (se ancla): el saldo final debe igualar stock_total."""
        producto, pt_a = crear_producto_con_talla(
            self.sucursal, articulo='Mix Test', sku=9002001, stock=10, talla='40',
        )
        pt_b = Producto_Talla.objects.create(
            producto=producto, sku=9002002, stock=3, talla='41',
        )
        # Talla A: legacy + apertura duplicada.
        _mov(pt_a, FECHA_LEGACY_1, 12, referencia='MIG:10')
        _mov(pt_a, FECHA_LEGACY_2, -2, concepto='VENTA_PUBLICO', referencia='MIG:11')
        _mov(pt_a, FECHA_APERTURA, 10, referencia=REF_SALDO_INICIAL_SINTETICO)
        # Talla B: solo apertura (sin legacy) -> anclada.
        _mov(pt_b, FECHA_APERTURA, 3, referencia=REF_SALDO_INICIAL_SINTETICO)

        data = self._get_agrupado(producto)
        self.assertEqual(data['producto']['stock_total'], 13)
        items = data['items']
        self.assertEqual(items[-1]['saldo'], data['producto']['stock_total'])
        # El grupo de la apertura queda marcado y su entrada muestra el
        # movimiento completo (10 excluidos + 3 anclados = 13), pero al saldo
        # solo aportan los 3 de la talla anclada.
        grupos_apertura = [i for i in items if i['es_apertura']]
        self.assertEqual(len(grupos_apertura), 1)
        self.assertEqual(grupos_apertura[0]['entrada'], 13)

    def test_agrupado_cancelado_no_suma(self):
        producto, pt = crear_producto_con_talla(
            self.sucursal, articulo='Cancel Test', sku=9002003, stock=8, talla='42',
        )
        _mov(pt, FECHA_POST, 8, concepto='RECEPCION_COMPRA')
        _mov(pt, datetime.date(2026, 3, 5), -4, concepto='VENTA_PUBLICO',
             estado='CANCELADO')

        data = self._get_agrupado(producto)
        items = data['items']
        self.assertEqual(items[-1]['saldo'], 8)
        self.assertEqual(data['producto']['stock_total'], 8)
