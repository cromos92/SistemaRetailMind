"""
Fachada `views.registrar_movimiento_producto` sobre `inventario_service`
(auditoría 29-09-2026, key "caminos", H13 + N4).

El escritor legacy hacía `stock += n; save()` sin lock, creaba lote solo para
una lista fija de conceptos y consumía lotes best-effort. Ahora delega en
`inventario_service.ingresar/egresar` (lock de fila, F(), lote + kardex en la
misma transacción) conservando el contrato que ya usan sus 12 llamadores:

- `crear_lote_fifo=False`  -> no crea lote (el llamador ya lo creó a mano).
- `consumir_lotes=False`   -> no consume (el llamador ya bajó los lotes).
- nunca valida stock (permitir_stock_insuficiente): la validación es de cada vista.
- sucursal_origen/destino por defecto = producto.sucursal (AMBAS, como antes).
- costo/sobreprecio/precio por defecto = ficha del producto.
- el bloque "saldo inicial legacy" sigue existiendo y va ANTES del lock.
- cantidad 0 sigue dejando un kardex documental sin tocar stock.

Correr en sqlite en memoria:
    $env:DATABASE_URL='sqlite://:memory:'; python manage.py test app.tests.test_inventario_service_fachada
"""
from django.db.models import Sum
from django.test import TestCase

from app.models import LoteProducto, Movimientos_Producto, Producto_Talla
from app.views import (
    CONCEPTOS_AJUSTE_RAPIDO_EGRESO, CONCEPTOS_AJUSTE_RAPIDO_INGRESO,
    registrar_movimiento_producto,
)

from .factories import (
    crear_empresa, crear_lote_fifo, crear_producto_con_talla, crear_sucursal,
    crear_usuario,
)


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


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empresa = crear_empresa()
        cls.sucursal = crear_sucursal(cls.empresa, alias='NICK1')
        cls.otra = crear_sucursal(cls.empresa, alias='NICK2')

    def _talla(self, stock=5, con_lote=True, sku=None, **kw):
        """Talla 'sana': stock == lotes == kardex (ingreso inicial de prueba)."""
        sku = sku or (7000000 + Producto_Talla.objects.count())
        producto, pt = crear_producto_con_talla(
            self.sucursal, sku=sku, stock=stock, articulo=f'ART-{sku}', **kw)
        if stock > 0:
            Movimientos_Producto.objects.create(
                ProductoTalla=pt, cantidad=stock, concepto='INGRESO_INICIAL',
                sucursal_origen=self.sucursal, sucursal_destino=self.sucursal,
                responsable='fixture', referencia_externa='FIXTURE',
            )
            if con_lote:
                crear_lote_fifo(pt, cantidad=stock, costo_unitario=producto.costo)
        return producto, pt

    def _tres_capas(self, pt):
        pt.refresh_from_db()
        return pt.stock, _lotes(pt), _kardex(pt)


class FlagsDeLaFachadaTest(_Base):

    def test_ingreso_concepto_fuera_de_la_lista_legacy_crea_lote(self):
        """T14 de la auditoría: REGULARIZACION_TRASPASO por ajuste rápido subía
        stock y kardex sin lote. Ahora todo ingreso con crear_lote_fifo=True deja lote."""
        _, pt = self._talla(stock=3)
        mov = registrar_movimiento_producto(pt, 'REGULARIZACION_TRASPASO', 1, 'tester')
        self.assertEqual(self._tres_capas(pt), (4, 4, 4))
        self.assertTrue(LoteProducto.objects.filter(movimiento=mov).exists())

    def test_ingreso_crear_lote_fifo_false_no_crea_lote(self):
        """gestión-inventarios sobrante / crear_lote_manual / ajustar_lote crean el
        lote a mano y lo enlazan después: la fachada no debe crear otro."""
        _, pt = self._talla(stock=3)
        lotes_antes = LoteProducto.objects.filter(producto_talla=pt).count()
        mov = registrar_movimiento_producto(
            pt, 'AJUSTE_POSITIVO', 2, 'tester', crear_lote_fifo=False)
        self.assertEqual(LoteProducto.objects.filter(producto_talla=pt).count(), lotes_antes)
        pt.refresh_from_db()
        self.assertEqual((pt.stock, _kardex(pt)), (5, 5))
        self.assertEqual(mov.cantidad, 2)

    def test_egreso_consume_lotes_por_defecto(self):
        _, pt = self._talla(stock=5)
        registrar_movimiento_producto(pt, 'AJUSTE_NEGATIVO', -2, 'tester')
        self.assertEqual(self._tres_capas(pt), (3, 3, 3))

    def test_egreso_consumir_lotes_false_no_toca_lotes(self):
        """aplicar_salida_stock_producto / ajustar_lote / consumir_stock_fifo de
        views_modulo_productos ya bajaron los lotes: no consumir dos veces."""
        _, pt = self._talla(stock=5)
        registrar_movimiento_producto(
            pt, 'CORRECCION_STOCK', -2, 'tester', crear_lote_fifo=False, consumir_lotes=False)
        pt.refresh_from_db()
        self.assertEqual((pt.stock, _lotes(pt), _kardex(pt)), (3, 5, 3))

    def test_egreso_con_stock_insuficiente_no_lanza_y_deja_negativo_visible(self):
        """La fachada nunca validó stock (revertir-a-pendiente, ajustar_lote,
        mobile dependen de ello): no debe lanzar, y el negativo queda visible
        (nunca se recorta a 0)."""
        _, pt = self._talla(stock=1)
        registrar_movimiento_producto(pt, 'AJUSTE_NEGATIVO', -3, 'tester')
        pt.refresh_from_db()
        self.assertEqual((pt.stock, _lotes(pt), _kardex(pt)), (-2, 0, -2))

    def test_stock_en_memoria_queda_refrescado(self):
        """Los llamadores leen `producto_talla.stock` después (mensajes, respuesta)."""
        _, pt = self._talla(stock=3)
        registrar_movimiento_producto(pt, 'INGRESO_MANUAL', 4, 'tester')
        self.assertEqual(pt.stock, 7)


class DefaultsDeLaFachadaTest(_Base):

    def test_sucursales_por_defecto_son_la_duena_en_ingreso_y_egreso(self):
        """N4: reportes cortan por sucursal_origen (egresos) y sucursal_destino
        (ingresos). El legacy rellenaba AMBAS con producto.sucursal."""
        _, pt = self._talla(stock=3)
        ing = registrar_movimiento_producto(pt, 'INGRESO_MANUAL', 1, 'tester')
        egr = registrar_movimiento_producto(pt, 'AJUSTE_NEGATIVO', -1, 'tester')
        for mov in (ing, egr):
            self.assertEqual(mov.sucursal_origen_id, self.sucursal.id)
            self.assertEqual(mov.sucursal_destino_id, self.sucursal.id)

    def test_sucursal_explicita_se_respeta(self):
        _, pt = self._talla(stock=3)
        mov = registrar_movimiento_producto(
            pt, 'TRASPASO_ENTRADA', 1, 'tester',
            sucursal_origen=self.otra, sucursal_destino=self.sucursal)
        self.assertEqual((mov.sucursal_origen_id, mov.sucursal_destino_id),
                         (self.otra.id, self.sucursal.id))

    def test_costo_por_defecto_es_el_del_producto_en_kardex_y_lote(self):
        producto, pt = self._talla(stock=0, costo=12345, sobreprecio=100, precioventa=19990)
        mov = registrar_movimiento_producto(pt, 'INGRESO_MANUAL', 2, 'tester')
        self.assertEqual((mov.costo, mov.sobreprecio, mov.precio), (12345, 100, 19990))
        lote = LoteProducto.objects.get(movimiento=mov)
        self.assertEqual((lote.costo_unitario, lote.precio_venta_unitario), (12345, 19990))

    def test_costo_explicito_se_respeta_en_kardex_y_lote(self):
        """Nuevo kwarg retrocompatible: actualizar_producto_existente / reparación
        de traspasos pasan el costo del DTE en vez del de la ficha."""
        _, pt = self._talla(stock=0, costo=12345)
        mov = registrar_movimiento_producto(
            pt, 'TRASPASO_ENTRADA', 2, 'tester', costo_unitario=999, precio_unitario=5000)
        self.assertEqual((mov.costo, mov.precio), (999, 5000))
        lote = LoteProducto.objects.get(movimiento=mov)
        self.assertEqual((lote.costo_unitario, lote.precio_venta_unitario), (999, 5000))

    def test_responsable_texto_se_conserva_y_usuario_se_guarda_por_username(self):
        _, pt = self._talla(stock=3)
        mov = registrar_movimiento_producto(pt, 'INGRESO_MANUAL', 1, 'Juan Pérez')
        self.assertEqual(mov.responsable, 'Juan Pérez')
        user = crear_usuario(username='bodega1', first_name='Nombre Muy Largo')
        mov2 = registrar_movimiento_producto(pt, 'INGRESO_MANUAL', 1, user)
        self.assertEqual(mov2.responsable, 'bodega1')

    def test_dte_ticket_referencia_y_observaciones_pasan_al_kardex(self):
        from .factories import crear_correlativo  # noqa: F401 (solo por simetría)
        _, pt = self._talla(stock=3)
        mov = registrar_movimiento_producto(
            pt, 'INGRESO_MANUAL', 1, 'tester',
            observaciones='obs de prueba', referencia_externa='REF-1')
        self.assertEqual((mov.observaciones, mov.referencia_externa), ('obs de prueba', 'REF-1'))
        self.assertEqual(mov.estado, 'COMPLETADO')


class ComportamientoLegacyConservadoTest(_Base):

    def test_saldo_inicial_legacy_se_inyecta_antes_y_cuadra_las_tres_capas_del_kardex(self):
        """Talla con stock y sin kardex (migración Laravel): el primer movimiento
        inyecta INGRESO_INICIAL 'SALDO_INICIAL' fechado con la creación del
        producto y recién después el movimiento real."""
        _, pt = crear_producto_con_talla(self.sucursal, sku=7100001, stock=20, articulo='LEGACY')
        registrar_movimiento_producto(pt, 'VENTA_PUBLICO', -1, 'tester', crear_lote_fifo=False)
        movs = list(Movimientos_Producto.objects.filter(ProductoTalla=pt).order_by('id'))
        self.assertEqual([m.concepto for m in movs], ['INGRESO_INICIAL', 'VENTA_PUBLICO'])
        self.assertEqual(movs[0].referencia_externa, 'SALDO_INICIAL')
        pt.refresh_from_db()
        self.assertEqual((pt.stock, _kardex(pt)), (19, 19))

    def test_talla_nueva_con_stock_cero_no_inyecta_saldo_inicial(self):
        """crear_producto_manual / desde_recepcion crean la talla con stock=0 y
        dependen de que NO se inyecte nada."""
        _, pt = crear_producto_con_talla(self.sucursal, sku=7100002, stock=0, articulo='NUEVA')
        registrar_movimiento_producto(pt, 'INGRESO_MANUAL', 3, 'tester')
        self.assertEqual(
            list(Movimientos_Producto.objects.filter(ProductoTalla=pt).values_list('concepto', flat=True)),
            ['INGRESO_MANUAL'])
        self.assertEqual(self._tres_capas(pt), (3, 3, 3))

    def test_cantidad_cero_deja_kardex_documental_sin_tocar_stock(self):
        """ajustar_lote sin diferencia llamaba con cantidad=0; el servicio exige
        cantidad > 0, la fachada conserva el registro documental."""
        _, pt = self._talla(stock=3)
        mov = registrar_movimiento_producto(pt, 'AJUSTE_NEGATIVO', 0, 'tester')
        self.assertEqual(mov.cantidad, 0)
        self.assertEqual(self._tres_capas(pt), (3, 3, 3))

    def test_concepto_invalido_lanza_y_no_escribe(self):
        _, pt = self._talla(stock=3)
        n = Movimientos_Producto.objects.count()
        with self.assertRaises(ValueError):
            registrar_movimiento_producto(pt, 'AJUSTE_ENTRADA', 1, 'tester')
        self.assertEqual(Movimientos_Producto.objects.count(), n)
        self.assertEqual(self._tres_capas(pt), (3, 3, 3))


class ConceptosAjusteRapidoTest(_Base):
    """Los conceptos que expone el ajuste rápido (web y móvil; 12 antes de H10,
    10 después) dejan stock == lotes == kardex al pasar por la fachada."""

    def test_listas_sin_ingreso_inicial_ni_devolucion_cliente(self):
        self.assertNotIn('INGRESO_INICIAL', CONCEPTOS_AJUSTE_RAPIDO_INGRESO)
        self.assertNotIn('DEVOLUCION_CLIENTE', CONCEPTOS_AJUSTE_RAPIDO_INGRESO)
        # Eran 12 (6+6); tras H10 quedan 4 de ingreso + 6 de egreso.
        self.assertEqual(len(CONCEPTOS_AJUSTE_RAPIDO_INGRESO), 4)
        self.assertEqual(len(CONCEPTOS_AJUSTE_RAPIDO_EGRESO), 6)

    def test_cada_concepto_de_ingreso_deja_lote_y_kardex(self):
        for i, concepto in enumerate(CONCEPTOS_AJUSTE_RAPIDO_INGRESO):
            with self.subTest(concepto=concepto):
                _, pt = self._talla(stock=2, sku=7200000 + i)
                registrar_movimiento_producto(pt, concepto, 3, 'tester',
                                              referencia_externa='AJUSTE_STOCK_RAPIDO')
                self.assertEqual(self._tres_capas(pt), (5, 5, 5))

    def test_cada_concepto_de_egreso_consume_lote_y_baja_kardex(self):
        for i, concepto in enumerate(CONCEPTOS_AJUSTE_RAPIDO_EGRESO):
            with self.subTest(concepto=concepto):
                _, pt = self._talla(stock=5, sku=7300000 + i)
                mov = registrar_movimiento_producto(pt, concepto, -2, 'tester',
                                                    referencia_externa='AJUSTE_STOCK_RAPIDO')
                self.assertEqual(mov.tipo_movimiento, 'EGRESO')
                self.assertEqual(self._tres_capas(pt), (3, 3, 3))
