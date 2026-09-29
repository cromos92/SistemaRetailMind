"""
Ajuste rápido de stock, web (`views.ajuste_stock_rapido`) y móvil
(`api/mobile AjusteStockRapidoView`) — auditoría tomas 29-09-2026, H9 y H10.

- H9: el POST web es una transacción con la talla bloqueada e idempotente por
  `request_id` (referencia 'AJUSTE_STOCK_RAPIDO:<uuid>', como el móvil).
- H10: INGRESO_INICIAL y DEVOLUCION_CLIENTE ya no se ofrecen; si un cliente
  viejo los manda se reclasifican a AJUSTE_POSITIVO.
- Cada ajuste deja stock == lotes == kardex (fachada sobre inventario_service).

Correr en sqlite en memoria:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_ajuste_stock_rapido
"""
import json
import uuid
from unittest import mock

from django.db.models import Sum
from django.test import Client, TestCase
from rest_framework.test import APIClient

from app.models import LoteProducto, Movimientos_Producto

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo,
    crear_producto_con_talla, crear_sucursal, crear_usuario,
)

URL_WEB = '/app/ajuste-stock-rapido/'
URL_MOVIL = '/api/v1/mobile/ajuste-stock-rapido/'


def _permiso_total():
    return mock.patch('app.models.PermisoRol.tiene_permiso', return_value=True)


def _lotes(pt):
    return (
        LoteProducto.objects.filter(
            producto_talla=pt, activo=True, agotado=False, cantidad_disponible__gt=0,
        ).aggregate(s=Sum('cantidad_disponible'))['s'] or 0
    )


def _kardex(pt):
    return (
        Movimientos_Producto.objects.filter(ProductoTalla=pt, estado='COMPLETADO')
        .aggregate(s=Sum('cantidad'))['s'] or 0
    )


def _capas(pt):
    pt.refresh_from_db()
    return pt.stock, _lotes(pt), _kardex(pt)


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = crear_usuario(username='ajustador', rol='administrador')
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(cls.empresa, alias='NICK1')
        cls.otra = crear_sucursal(cls.empresa, alias='NICK2')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)

    def _talla_sana(self, sucursal=None, stock=5, sku=4834237):
        producto, pt = crear_producto_con_talla(sucursal or self.sucursal, sku=sku, stock=stock)
        if stock:
            Movimientos_Producto.objects.create(
                ProductoTalla=pt, cantidad=stock, concepto='INGRESO_INICIAL',
                sucursal_origen=pt.producto.sucursal, sucursal_destino=pt.producto.sucursal,
                responsable='fixture', referencia_externa='FIXTURE')
            crear_lote_fifo(pt, cantidad=stock, costo_unitario=producto.costo)
        return producto, pt


class AjusteStockRapidoWebTest(_Base):

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s['idSucursalActual'] = self.sucursal.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()

    def _post(self, body):
        with _permiso_total():
            return self.client.post(URL_WEB, data=json.dumps(body), content_type='application/json')

    def test_ingreso_deja_stock_lote_y_kardex(self):
        _, pt = self._talla_sana(stock=3)
        resp = self._post({'sku': pt.sku, 'concepto': 'REGULARIZACION_TRASPASO', 'cantidad': 1})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['nuevo_stock'], 4)
        # T14 de la auditoría: stock 4 / kardex 4 / lotes 3 (sin lote).
        self.assertEqual(_capas(pt), (4, 4, 4))
        mov = Movimientos_Producto.objects.get(id=resp.json()['movimiento_id'])
        self.assertEqual(mov.referencia_externa, 'AJUSTE_STOCK_RAPIDO')
        self.assertEqual((mov.sucursal_origen_id, mov.sucursal_destino_id),
                         (self.sucursal.id, self.sucursal.id))

    def test_egreso_consume_lote(self):
        _, pt = self._talla_sana(stock=5)
        resp = self._post({'sku': pt.sku, 'concepto': 'PERDIDA_DETERIORO', 'cantidad': 2})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['tipo'], 'EGRESO')
        self.assertEqual(_capas(pt), (3, 3, 3))

    def test_doble_post_mismo_request_id_un_movimiento(self):
        _, pt = self._talla_sana(stock=5)
        rid = str(uuid.uuid4())
        body = {'sku': pt.sku, 'concepto': 'AJUSTE_POSITIVO', 'cantidad': 1, 'request_id': rid}
        r1 = self._post(body)
        r2 = self._post(body)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertTrue(r2.json()['idempotente'])
        self.assertEqual(r1.json()['movimiento_id'], r2.json()['movimiento_id'])
        self.assertEqual(Movimientos_Producto.objects.filter(
            referencia_externa=f'AJUSTE_STOCK_RAPIDO:{rid}').count(), 1)
        # Escenario C de la auditoría: dos POST → stock 5→7 y dos lotes.
        self.assertEqual(_capas(pt), (6, 6, 6))
        self.assertEqual(r2.json()['nuevo_stock'], 6)

    def test_sin_request_id_cada_post_ajusta(self):
        """Documentado: sin clave no hay idempotencia (el template debe mandar
        un UUID por formulario; hoy no lo envía)."""
        _, pt = self._talla_sana(stock=5)
        body = {'sku': pt.sku, 'concepto': 'AJUSTE_POSITIVO', 'cantidad': 1}
        self._post(body)
        self._post(body)
        self.assertEqual(_capas(pt), (7, 7, 7))

    def test_concepto_ingreso_inicial_se_reclasifica_a_ajuste_positivo(self):
        _, pt = self._talla_sana(stock=5)
        for concepto in ('INGRESO_INICIAL', 'DEVOLUCION_CLIENTE'):
            with self.subTest(concepto=concepto):
                resp = self._post({'sku': pt.sku, 'concepto': concepto, 'cantidad': 1, 'observaciones': 'x'})
                self.assertEqual(resp.status_code, 200, resp.content)
                mov = Movimientos_Producto.objects.get(id=resp.json()['movimiento_id'])
                self.assertEqual(mov.concepto, 'AJUSTE_POSITIVO')
                self.assertIn(f'concepto {concepto} reclasificado', mov.observaciones)
        self.assertFalse(Movimientos_Producto.objects.filter(
            referencia_externa__startswith='AJUSTE_STOCK_RAPIDO',
            concepto__in=['INGRESO_INICIAL', 'DEVOLUCION_CLIENTE']).exists())
        self.assertEqual(_capas(pt), (7, 7, 7))

    def test_formulario_no_ofrece_los_conceptos_retirados(self):
        with _permiso_total():
            resp = self.client.get(URL_WEB)
        self.assertEqual(resp.status_code, 200)
        codigos = [c[0] for c in resp.context['conceptos_ingreso']]
        self.assertNotIn('INGRESO_INICIAL', codigos)
        self.assertNotIn('DEVOLUCION_CLIENTE', codigos)
        self.assertIn('AJUSTE_POSITIVO', codigos)

    def test_concepto_desconocido_400(self):
        _, pt = self._talla_sana(stock=5)
        resp = self._post({'sku': pt.sku, 'concepto': 'VENTA_PUBLICO', 'cantidad': 1})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(_capas(pt), (5, 5, 5))

    def test_egreso_mayor_al_stock_400_sin_mover(self):
        _, pt = self._talla_sana(stock=2)
        resp = self._post({'sku': pt.sku, 'concepto': 'AJUSTE_NEGATIVO', 'cantidad': 3})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('Stock insuficiente', resp.json()['error'])
        self.assertEqual(_capas(pt), (2, 2, 2))

    def test_sku_solo_en_otra_sucursal_400(self):
        _, pt = self._talla_sana(sucursal=self.otra, stock=5)
        resp = self._post({'sku': pt.sku, 'concepto': 'AJUSTE_POSITIVO', 'cantidad': 1})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(resp.json()['en_sucursal_actual'])
        self.assertEqual(_capas(pt), (5, 5, 5))

    def test_talla_legacy_sin_kardex_queda_cuadrada(self):
        """Talla con stock y sin movimientos (migración): la fachada inyecta el
        saldo inicial y recién después el ajuste."""
        _, pt = crear_producto_con_talla(self.sucursal, sku=4834300, stock=3)
        resp = self._post({'sku': pt.sku, 'concepto': 'AJUSTE_NEGATIVO', 'cantidad': 1})
        self.assertEqual(resp.status_code, 200, resp.content)
        pt.refresh_from_db()
        self.assertEqual((pt.stock, _kardex(pt)), (2, 2))
        self.assertEqual(
            list(Movimientos_Producto.objects.filter(ProductoTalla=pt).order_by('id')
                 .values_list('concepto', flat=True)),
            ['INGRESO_INICIAL', 'AJUSTE_NEGATIVO'])


class AjusteStockRapidoMovilTest(_Base):

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def _post(self, body):
        body.setdefault('sucursal_id', self.sucursal.id)
        return self.api.post(URL_MOVIL, data=body, format='json')

    def test_movil_idempotente_y_tres_capas(self):
        _, pt = self._talla_sana(stock=5)
        body = {'sku': pt.sku, 'concepto': 'AJUSTE_POSITIVO', 'cantidad': 2, 'request_id': 'REQ-PRUEBA-1'}
        r1 = self._post(body)
        r2 = self._post(body)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200), (r1.content, r2.content))
        self.assertTrue(r2.data['idempotente'])
        self.assertEqual(Movimientos_Producto.objects.filter(
            referencia_externa='AJUSTE_STOCK_RAPIDO:REQ-PRUEBA-1').count(), 1)
        self.assertEqual(_capas(pt), (7, 7, 7))

    def test_movil_reclasifica_conceptos_retirados(self):
        _, pt = self._talla_sana(stock=5)
        resp = self._post({'sku': pt.sku, 'concepto': 'DEVOLUCION_CLIENTE', 'cantidad': 1})
        self.assertEqual(resp.status_code, 200, resp.content)
        mov = Movimientos_Producto.objects.get(id=resp.data['movimiento_id'])
        self.assertEqual(mov.concepto, 'AJUSTE_POSITIVO')
        self.assertIn('DEVOLUCION_CLIENTE reclasificado', mov.observaciones)
        self.assertEqual(_capas(pt), (6, 6, 6))

    def test_movil_egreso_consume_lote_y_valida_stock(self):
        _, pt = self._talla_sana(stock=2)
        resp = self._post({'sku': pt.sku, 'concepto': 'PERDIDA_ROBO', 'cantidad': 3})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(_capas(pt), (2, 2, 2))
        resp = self._post({'sku': pt.sku, 'concepto': 'PERDIDA_ROBO', 'cantidad': 1})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(_capas(pt), (1, 1, 1))
