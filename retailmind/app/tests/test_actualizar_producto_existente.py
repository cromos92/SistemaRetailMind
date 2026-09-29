"""
Regresión de los dos caminos de stock que FALLABAN en views.py (auditoría
29-09-2026, key "caminos"):

- H1 `actualizar_producto_existente` (modal "producto existente" de Gestión
  Producto): duplicaba el stock de la talla existente y creaba tallas nuevas
  con stock sin kardex ni lote cuando no había DTE.
- H2 `asignar_guia_talla_producto` con migrar_desde_00: SUMABA a la talla 00
  en vez de restar (kardex AJUSTE_NEGATIVO registrado como INGRESO +n).

Correr en sqlite en memoria:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_actualizar_producto_existente
"""
import json
from decimal import Decimal
from unittest import mock

from django.db.models import Sum
from django.test import Client, TestCase

from app.models import (
    AtributoOpcion, Dte, GuiaTalla, GuiaTallaItem, LoteProducto,
    Movimientos_Producto, Producto_Talla, Productos_Atributos,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_lote_fifo,
    crear_producto_con_talla, crear_sucursal, crear_usuario,
)

URL_ACTUALIZAR = '/app/actualizar_producto_existente/'
URL_GUIA = '/app/api/asignar-guia-talla-producto/'


def _permiso_total():
    """Las URLs pasan por el middleware de permisos por rol: se parcha
    `PermisoRol.tiene_permiso` como en test_ajuste_traspaso."""
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
        cls.user = crear_usuario(username='bodeguero', rol='administrador')
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(cls.empresa, alias='NICK1')
        cls.otra = crear_sucursal(cls.empresa, alias='NICK2')
        crear_empresa_user(cls.user, cls.empresa, cls.sucursal)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)
        self._sesion(self.sucursal)

    def _sesion(self, sucursal):
        s = self.client.session
        s['idSucursalActual'] = sucursal.id
        s['idEmpresaActual'] = sucursal.empresa_id
        s['nombreUsuario'] = 'Tester'
        s.save()

    def _post(self, url, body):
        with _permiso_total():
            return self.client.post(url, data=json.dumps(body), content_type='application/json')

    def _talla_sana(self, sucursal=None, stock=3, sku=4834237, talla='42', **kw):
        """Talla con stock == lotes == kardex (como el SKU 4834237 de la auditoría)."""
        producto, pt = crear_producto_con_talla(
            sucursal or self.sucursal, sku=sku, stock=stock, talla=talla, **kw)
        if stock:
            Movimientos_Producto.objects.create(
                ProductoTalla=pt, cantidad=stock, concepto='INGRESO_INICIAL',
                sucursal_origen=pt.producto.sucursal, sucursal_destino=pt.producto.sucursal,
                responsable='fixture', referencia_externa='FIXTURE')
            crear_lote_fifo(pt, cantidad=stock, costo_unitario=producto.costo)
        return producto, pt


class ActualizarProductoExistenteTest(_Base):
    """H1: una sola suma, tres capas siempre."""

    def test_talla_existente_suma_una_vez_con_lote_y_kardex(self):
        producto, pt = self._talla_sana(stock=3)
        resp = self._post(URL_ACTUALIZAR, {
            'producto_id': producto.id, 'agregar_tallas': True,
            'tallas': [{'talla': '42', 'stock': 2}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])
        # T1 de la auditoría daba 7/5/5; esperado 5/5/5.
        self.assertEqual(_capas(pt), (5, 5, 5))
        self.assertIn('total: 5', ' '.join(resp.json()['cambios']))
        mov = Movimientos_Producto.objects.filter(ProductoTalla=pt, concepto='INGRESO_MANUAL').get()
        self.assertEqual((mov.cantidad, mov.sucursal_origen_id, mov.sucursal_destino_id),
                         (2, self.sucursal.id, self.sucursal.id))
        self.assertEqual(mov.costo, producto.costo)
        self.assertTrue(LoteProducto.objects.filter(movimiento=mov, cantidad_inicial=2).exists())

    def test_talla_nueva_sin_dte_deja_stock_lote_y_kardex(self):
        """Antes: stock 2 / kardex 0 / lote 0 (sin dte_id no se registraba nada)."""
        producto, _ = self._talla_sana(stock=3)
        resp = self._post(URL_ACTUALIZAR, {
            'producto_id': producto.id, 'agregar_tallas': True,
            'tallas': [{'talla': 'ZZ2', 'stock': 2}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        pt_new = Producto_Talla.objects.get(producto=producto, talla='ZZ2')
        self.assertEqual(_capas(pt_new), (2, 2, 2))
        conceptos = list(Movimientos_Producto.objects.filter(ProductoTalla=pt_new)
                         .values_list('concepto', flat=True))
        # Nace con stock=0: NO se inyecta un INGRESO_INICIAL 'saldo legacy'.
        self.assertEqual(conceptos, ['INGRESO_MANUAL'])

    def test_talla_nueva_con_dte_enlaza_dte_en_kardex_y_lote(self):
        producto, _ = self._talla_sana(stock=3)
        dte = Dte.objects.create(
            emisor=self.empresa, receptor=self.empresa, numero_documento=777,
            tipo_documento='FACTURA', monto_neto=Decimal('1000'), monto_con_iva=Decimal('1190'),
            estado_pago='PENDIENTE', estado_dte='EMITIDO', responsable='t',
            fecha_emision='2026-09-01', fecha_vencimiento='2026-09-01', diasCredito=0,
            bultos=1, unidades_productos=2, tipo_transaccion='COMPRA', sucursal=self.sucursal,
        )
        resp = self._post(URL_ACTUALIZAR, {
            'producto_id': producto.id, 'agregar_tallas': True, 'dte_id': dte.id,
            'tallas': [{'talla': 'ZZ1', 'stock': 2}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        pt_new = Producto_Talla.objects.get(producto=producto, talla='ZZ1')
        # T1 de la auditoría daba stock 4 / kardex 4 (INGRESO_INICIAL 2 + INGRESO_MANUAL 2).
        self.assertEqual(_capas(pt_new), (2, 2, 2))
        mov = Movimientos_Producto.objects.get(ProductoTalla=pt_new)
        self.assertEqual((mov.concepto, mov.dte_id, mov.referencia_externa),
                         ('INGRESO_MANUAL', dte.id, 'Manual-777'))
        self.assertEqual(LoteProducto.objects.get(movimiento=mov).dte_id, dte.id)

    def test_talla_nueva_con_stock_cero_no_registra_movimiento(self):
        producto, _ = self._talla_sana(stock=3)
        resp = self._post(URL_ACTUALIZAR, {
            'producto_id': producto.id, 'agregar_tallas': True,
            'tallas': [{'talla': '43', 'stock': 0}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        pt_new = Producto_Talla.objects.get(producto=producto, talla='43')
        self.assertEqual(_capas(pt_new), (0, 0, 0))
        self.assertFalse(Movimientos_Producto.objects.filter(ProductoTalla=pt_new).exists())

    def test_tallas_duplicadas_en_el_payload_se_agrupan(self):
        producto, pt = self._talla_sana(stock=3)
        resp = self._post(URL_ACTUALIZAR, {
            'producto_id': producto.id, 'agregar_tallas': True,
            'tallas': [{'talla': '42', 'stock': 1}, {'talla': '42', 'stock': 2}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(_capas(pt), (6, 6, 6))
        self.assertEqual(Movimientos_Producto.objects.filter(
            ProductoTalla=pt, concepto='INGRESO_MANUAL').count(), 1)

    def test_producto_de_otra_sucursal_400_sin_mover_nada(self):
        producto, pt = self._talla_sana(sucursal=self.otra, stock=3)
        n_mov = Movimientos_Producto.objects.count()
        resp = self._post(URL_ACTUALIZAR, {
            'producto_id': producto.id, 'agregar_tallas': True, 'actualizar_precios': True,
            'costo': 1, 'sobreprecio': 1, 'precioventa': 1,
            'tallas': [{'talla': '42', 'stock': 2}, {'talla': '44', 'stock': 1}],
        })
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('NICK2', resp.json()['error'])
        self.assertEqual(_capas(pt), (3, 3, 3))
        self.assertEqual(Movimientos_Producto.objects.count(), n_mov)
        self.assertFalse(Producto_Talla.objects.filter(producto=producto, talla='44').exists())
        producto.refresh_from_db()
        self.assertNotEqual(producto.costo, 1)

    def test_doble_post_suma_dos_veces_pero_cuadrado(self):
        """No es idempotente por diseño (el modal deshabilita el botón); lo que
        se garantiza es que cada POST deja las tres capas iguales."""
        producto, pt = self._talla_sana(stock=3)
        body = {'producto_id': producto.id, 'agregar_tallas': True,
                'tallas': [{'talla': '42', 'stock': 2}]}
        self._post(URL_ACTUALIZAR, body)
        self._post(URL_ACTUALIZAR, body)
        self.assertEqual(_capas(pt), (7, 7, 7))

    def test_error_a_mitad_no_deja_escrituras_parciales(self):
        """@transaction.atomic + set_rollback en el except: si la segunda talla
        revienta, ni la primera talla ni su stock quedan comprometidos."""
        producto, pt = self._talla_sana(stock=3)
        from app.services import inventario_service
        original = inventario_service.ingresar
        llamadas = {'n': 0}

        def _explota_en_la_segunda(*a, **kw):
            llamadas['n'] += 1
            if llamadas['n'] == 2:
                raise RuntimeError('falla simulada')
            return original(*a, **kw)

        with mock.patch.object(inventario_service, 'ingresar', side_effect=_explota_en_la_segunda):
            resp = self._post(URL_ACTUALIZAR, {
                'producto_id': producto.id, 'agregar_tallas': True,
                'tallas': [{'talla': '42', 'stock': 2}, {'talla': '45', 'stock': 1}],
            })
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(_capas(pt), (3, 3, 3))
        self.assertFalse(Producto_Talla.objects.filter(producto=producto, talla='45').exists())


class AsignarGuiaMigrarDesde00Test(_Base):
    """H2: la talla 00 RESTA (egreso con lote consumido) y las tallas destino
    suman con lote."""

    def _guia(self, tallas=('40', '41', '42')):
        attr = Productos_Atributos.objects.create(nombre='Marca', descripcion='Marca')
        marca = AtributoOpcion.objects.create(atributo=attr, valor='NIKE')
        guia = GuiaTalla.objects.create(marca=marca, nombre='Guía test')
        for i, t in enumerate(tallas):
            GuiaTallaItem.objects.create(guia=guia, cl=t, orden=i)
        return guia

    def test_migrar_resta_a_la_00_y_suma_a_las_destino_con_lotes(self):
        producto, pt00 = self._talla_sana(stock=4, sku=137600, talla='00', tipo_talla='CL')
        guia = self._guia()
        resp = self._post(URL_GUIA, {
            'producto_id': producto.id, 'guia_talla_id': guia.id, 'migrar_desde_00': True,
            'distribucion': [{'talla': '40', 'stock': 2}, {'talla': '41', 'stock': 1}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['movimientos_creados'], 3)
        # T2 de la auditoría: 00 quedaba en 7 con lotes intactos (4).
        self.assertEqual(_capas(pt00), (1, 1, 1))
        pt40 = Producto_Talla.objects.get(producto=producto, talla='40')
        pt41 = Producto_Talla.objects.get(producto=producto, talla='41')
        pt42 = Producto_Talla.objects.get(producto=producto, talla='42')
        self.assertEqual(_capas(pt40), (2, 2, 2))
        self.assertEqual(_capas(pt41), (1, 1, 1))
        self.assertEqual(_capas(pt42), (0, 0, 0))
        egreso = Movimientos_Producto.objects.get(
            ProductoTalla=pt00, referencia_externa=f'REASIGNAR_GUIA_{producto.id}')
        self.assertEqual((egreso.concepto, egreso.tipo_movimiento, egreso.cantidad),
                         ('AJUSTE_NEGATIVO', 'EGRESO', -3))
        self.assertEqual((egreso.sucursal_origen_id, egreso.sucursal_destino_id),
                         (self.sucursal.id, self.sucursal.id))
        # Las tallas nuevas heredan la antigüedad FIFO del lote de la 00.
        lote00 = LoteProducto.objects.filter(producto_talla=pt00).earliest('fecha_ingreso')
        lote40 = LoteProducto.objects.get(producto_talla=pt40)
        self.assertEqual(lote40.fecha_ingreso, lote00.fecha_ingreso)
        self.assertEqual(lote40.costo_unitario, lote00.costo_unitario)
        producto.refresh_from_db()
        self.assertEqual(producto.guia_talla_id, guia.id)

    def test_stock_insuficiente_en_00_es_400_sin_mover(self):
        producto, pt00 = self._talla_sana(stock=2, sku=137601, talla='00', tipo_talla='CL')
        guia = self._guia()
        resp = self._post(URL_GUIA, {
            'producto_id': producto.id, 'guia_talla_id': guia.id, 'migrar_desde_00': True,
            'distribucion': [{'talla': '40', 'stock': 3}],
        })
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(_capas(pt00), (2, 2, 2))
        self.assertFalse(Movimientos_Producto.objects.filter(
            referencia_externa=f'REASIGNAR_GUIA_{producto.id}').exists())

    def test_carrera_stock_bajo_lock_devuelve_400_y_revierte_todo(self):
        """El chequeo previo pasa pero egresar() ve menos stock (venta entre
        medio): 400 y ni la guía ni las tallas quedan escritas."""
        producto, pt00 = self._talla_sana(stock=4, sku=137602, talla='00', tipo_talla='CL')
        guia = self._guia()
        from app.services import inventario_service
        with mock.patch.object(inventario_service, 'egresar',
                               side_effect=ValueError('Stock insuficiente para SKU 137602')):
            resp = self._post(URL_GUIA, {
                'producto_id': producto.id, 'guia_talla_id': guia.id, 'migrar_desde_00': True,
                'distribucion': [{'talla': '40', 'stock': 2}],
            })
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(_capas(pt00), (4, 4, 4))
        producto.refresh_from_db()
        self.assertIsNone(producto.guia_talla_id)
        self.assertFalse(Producto_Talla.objects.filter(producto=producto, talla='40').exists())

    def test_sin_migrar_solo_crea_tallas_en_cero(self):
        producto, pt00 = self._talla_sana(stock=4, sku=137603, talla='00', tipo_talla='CL')
        guia = self._guia()
        resp = self._post(URL_GUIA, {'producto_id': producto.id, 'guia_talla_id': guia.id})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['movimientos_creados'], 0)
        self.assertEqual(_capas(pt00), (4, 4, 4))
        self.assertEqual(Producto_Talla.objects.filter(producto=producto).count(), 4)
