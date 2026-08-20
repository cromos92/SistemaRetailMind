"""
Fase B de fixes de la auditoría de Reportes (ago-2026) — P1.4 y P1.5.

1. MERCADERÍA EN TRÁNSITO — el neteo de entradas ahora cuenta también los
   cierres administrativos contra el MISMO DTE:
   * `ANULACION_REGULARIZACION` (botón "Llegó todo", `anular_regularizacion_dte`)
   * `CORRECCION_STOCK`
   ambos SOLO en tipo INGRESO + estado COMPLETADO — la reversa de "cancelar
   regularización" es un EGRESO con el mismo concepto y NO puede sumar (el
   reporte suma valor absoluto, así que sin el filtro la reversa sumaba).
   Antes del fix, un documento cerrado con "Llegó todo" quedaba SIN_RECIBIR
   para siempre (falso positivo permanente: folio 17098 en prod).

2. DETALLE valorizado con el MISMO costo promedio del documento que el listado
   (`_costo_unitario_por_dte`): antes el detalle usaba Max(costo) por SKU y el
   mismo despacho mostraba dos platas distintas según la pantalla.

3. QUIEBRE DE TALLA — el KPI de cabecera `unidades_perdidas_estimadas` es
   ahora CONSERVADOR: excluye celdas `reconstruccion_dudosa` y celdas con
   menos de `MIN_DIAS_AGREGADO` días de disponibilidad, y capea el
   multiplicador de extrapolación a `CAP_MULTIPLICADOR_AGREGADO`. Lo excluido
   se reporta en `unidades_perdidas_excluidas`. Las FILAS no cambian: siguen
   mostrando la extrapolación completa con sus banderas.

Correr SOLO contra una BD desechable:
    $env:DATABASE_URL="sqlite:///C:/temp/tb4.sqlite3"
    python manage.py test app.tests.test_fase_b_transito_tallas
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase
from django.utils import timezone

from app.models import Dte, Dte_Productos, Movimientos_Producto
from app.views_modulo_reportes_tallas import (
    CAP_MULTIPLICADOR_AGREGADO, MIN_DIAS_AGREGADO,
)

from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario,
)


def _patch_permisos():
    """El decorador de permisos pega a la BD de permisos; en tests va a True."""
    return mock.patch('app.decorators.PermisoRol.tiene_permiso', return_value=True)


def _crear_dte(emisor, receptor, sucursal, numero, unidades, **kwargs):
    defaults = dict(
        emisor=emisor,
        receptor=receptor,
        numero_documento=numero,
        tipo_documento='GUIA',
        monto_neto=Decimal(unidades * 1000),
        monto_con_iva=Decimal(unidades * 1190),
        estado_pago='PENDIENTE',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision=timezone.localdate(),
        fecha_vencimiento=timezone.localdate(),
        diasCredito=0,
        bultos=1,
        unidades_productos=unidades,
        tipo_transaccion='TRASPASO',
        sucursal=sucursal,
    )
    defaults.update(kwargs)
    return Dte.objects.create(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# 1 + 2. Mercadería en tránsito: cierres administrativos y costo unificado
# ══════════════════════════════════════════════════════════════════════════

class BaseTransito(TestCase):
    URL = '/app/api/reportes/mercaderia-transito/'
    URL_DETALLE = '/app/api/reportes/mercaderia-transito/detalle/'

    def setUp(self):
        self.user = crear_usuario(username='reportero', rol='administrador')
        self.empresa = crear_empresa(nombre='Mi Empresa')
        self.bodega = crear_sucursal(self.empresa, alias='EDEL')
        self.tienda = crear_sucursal(self.empresa, alias='NICK1')
        crear_empresa_user(self.user, self.empresa, self.bodega)

        # Mismo SKU en ambas puntas: el cruce del detalle va por SKU.
        _, self.talla_origen = crear_producto_con_talla(
            self.bodega, articulo='Zapatilla A', sku=7001, stock=500,
        )
        _, self.talla_destino = crear_producto_con_talla(
            self.tienda, articulo='Zapatilla A', sku=7001, stock=0,
        )

        self.client = Client()
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.bodega.id
        sesion['idEmpresaActual'] = self.empresa.id
        sesion.save()

    def _json(self, url, **params):
        with _patch_permisos():
            resp = self.client.get(url, params)
        return resp.status_code, resp.json()

    def _despacho(self, numero, enviadas, dias_atras=30, costo=1000):
        """DTE de traspaso con su TRASPASO_SALIDA y una línea al costo dado."""
        fecha = timezone.localdate() - timedelta(days=dias_atras)
        dte = _crear_dte(self.empresa, self.empresa, self.bodega, numero, enviadas)
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.talla_origen, descripcion='Zapatilla A',
            costo=costo, sobreprecio=0, precio=2000, stock=enviadas, activo=True,
        )
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=self.talla_origen,
            sucursal_origen=self.bodega, sucursal_destino=self.tienda,
            cantidad=-enviadas, concepto='TRASPASO_SALIDA',
            tipo_movimiento='EGRESO', estado='COMPLETADO',
            responsable='tester', fecha=fecha,
        )
        return dte

    def _entrada(self, dte, cantidad, concepto='TRASPASO_ENTRADA',
                 tipo_movimiento='INGRESO', estado='COMPLETADO', talla=None):
        return Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=talla or self.talla_destino,
            sucursal_origen=self.bodega, sucursal_destino=self.tienda,
            cantidad=cantidad, concepto=concepto,
            tipo_movimiento=tipo_movimiento, estado=estado,
            responsable='tester', fecha=timezone.localdate(),
        )

    def _fila(self, data, folio):
        for d in data['despachos']:
            if d['folio'] == folio:
                return d
        return None


class CierreAdministrativoTransitoTest(BaseTransito):

    def test_llego_todo_cierra_el_pendiente(self):
        """
        Réplica del folio 17098 de prod: 144 enviadas, 132 por TRASPASO_ENTRADA
        y las 12 restantes ingresadas por el botón "Llegó todo"
        (ANULACION_REGULARIZACION, tipo INGRESO). Antes del fix el reporte lo
        mostraba SIN_RECIBIR pend=12 para siempre.
        """
        dte = self._despacho(17098, enviadas=144)
        self._entrada(dte, 132, concepto='TRASPASO_ENTRADA')
        self._entrada(dte, 12, concepto='ANULACION_REGULARIZACION')

        _, data = self._json(self.URL, situacion='')
        fila = self._fila(data, 17098)
        self.assertEqual(fila['recibidas'], 144)
        self.assertEqual(fila['pendientes'], 0)
        self.assertEqual(fila['situacion'], 'RECIBIDO')

        self.assertEqual(data['resumen']['docs_sin_recibir'], 0)
        self.assertEqual(data['resumen']['unidades_sin_recibir'], 0)
        self.assertEqual(data['resumen']['valor_sin_recibir'], 0)

    def test_correccion_stock_ingreso_cuenta_como_entrada(self):
        dte = self._despacho(400, enviadas=10)
        self._entrada(dte, 6, concepto='TRASPASO_ENTRADA')
        self._entrada(dte, 4, concepto='CORRECCION_STOCK')

        _, data = self._json(self.URL, situacion='')
        fila = self._fila(data, 400)
        self.assertEqual(fila['recibidas'], 10)
        self.assertEqual(fila['pendientes'], 0)
        self.assertEqual(fila['situacion'], 'RECIBIDO')

    def test_reversa_egreso_no_suma_como_recibida(self):
        """
        "Cancelar regularización" escribe ANULACION_REGULARIZACION con tipo
        EGRESO y cantidad negativa. Como el reporte suma valor ABSOLUTO, sin el
        filtro de tipo la reversa habría cerrado (¡o sobre-cerrado!) el doc.
        """
        dte = self._despacho(401, enviadas=10)
        self._entrada(dte, -10, concepto='ANULACION_REGULARIZACION',
                      tipo_movimiento='EGRESO')

        _, data = self._json(self.URL, situacion='')
        fila = self._fila(data, 401)
        self.assertEqual(fila['recibidas'], 0)
        self.assertEqual(fila['pendientes'], 10)
        self.assertEqual(fila['situacion'], 'SIN_RECIBIR')

    def test_cierre_anulado_no_cuenta(self):
        dte = self._despacho(402, enviadas=10)
        self._entrada(dte, 10, concepto='ANULACION_REGULARIZACION',
                      estado='ANULADO')

        _, data = self._json(self.URL, situacion='')
        fila = self._fila(data, 402)
        self.assertEqual(fila['recibidas'], 0)
        self.assertEqual(fila['pendientes'], 10)

    def test_neteo_de_nc_sigue_intacto_y_sin_doble_conteo(self):
        """
        La NC reingresa por SU PROPIO dte (DEVOLUCION_NC) y se netea aparte:
        el fix no puede contarla dos veces ni dejar de contarla.
        """
        dte = self._despacho(403, enviadas=10)
        self._entrada(dte, 4, concepto='TRASPASO_ENTRADA')
        self._entrada(dte, 2, concepto='ANULACION_REGULARIZACION')
        nc = _crear_dte(
            self.empresa, self.empresa, self.bodega, 990, 4,
            tipo_documento='NOTA DE CREDITO', es_nota_credito=True,
            documento_afectado=dte,
        )
        Movimientos_Producto.objects.create(
            dte=nc, ProductoTalla=self.talla_origen,
            sucursal_origen=self.tienda, sucursal_destino=self.bodega,
            cantidad=4, concepto='DEVOLUCION_NC',
            tipo_movimiento='INGRESO', estado='COMPLETADO', responsable='tester',
        )

        _, data = self._json(self.URL, situacion='')
        fila = self._fila(data, 403)
        self.assertEqual(fila['recibidas'], 6)        # 4 + 2 (cierre)
        self.assertEqual(fila['devueltas_nc'], 4)     # la NC, una sola vez
        self.assertEqual(fila['pendientes'], 0)
        self.assertEqual(fila['situacion'], 'RECIBIDO')

    def test_detalle_cuenta_los_mismos_conceptos_que_el_listado(self):
        """Listado y detalle por SKU tienen que dar el MISMO pendiente."""
        dte = self._despacho(404, enviadas=144)
        self._entrada(dte, 132, concepto='TRASPASO_ENTRADA')
        self._entrada(dte, 12, concepto='ANULACION_REGULARIZACION')

        _, listado = self._json(self.URL, situacion='')
        _, detalle = self._json(self.URL_DETALLE, dte_id=dte.id)

        fila = self._fila(listado, 404)
        self.assertEqual(detalle['totales']['recibidas'], fila['recibidas'])
        self.assertEqual(detalle['totales']['pendientes'], fila['pendientes'])
        self.assertEqual(detalle['totales']['pendientes'], 0)


class CostoUnificadoDetalleTest(BaseTransito):

    def test_detalle_valoriza_al_costo_promedio_del_documento(self):
        """
        Documento con dos líneas de costos distintos (1.000 y 3.000, mitad y
        mitad ⇒ promedio 2.000). Sólo queda pendiente el SKU barato: con el
        Max(costo) por SKU de antes, el detalle decía $5.000 mientras el
        listado decía $10.000 para el MISMO despacho. Ahora ambos usan el
        promedio del documento.
        """
        _, talla_origen_b = crear_producto_con_talla(
            self.bodega, articulo='Zapatilla B', sku=7002, stock=500, talla='39',
        )
        _, talla_destino_b = crear_producto_con_talla(
            self.tienda, articulo='Zapatilla B', sku=7002, stock=0, talla='39',
        )

        fecha = timezone.localdate() - timedelta(days=30)
        dte = _crear_dte(self.empresa, self.empresa, self.bodega, 500, 10)
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.talla_origen, descripcion='Zapatilla A',
            costo=1000, sobreprecio=0, precio=2000, stock=5, activo=True,
        )
        Dte_Productos.objects.create(
            dte=dte, productoTalla=talla_origen_b, descripcion='Zapatilla B',
            costo=3000, sobreprecio=0, precio=5000, stock=5, activo=True,
        )
        for talla, cant in ((self.talla_origen, 5), (talla_origen_b, 5)):
            Movimientos_Producto.objects.create(
                dte=dte, ProductoTalla=talla,
                sucursal_origen=self.bodega, sucursal_destino=self.tienda,
                cantidad=-cant, concepto='TRASPASO_SALIDA',
                tipo_movimiento='EGRESO', estado='COMPLETADO',
                responsable='tester', fecha=fecha,
            )
        # Llega COMPLETO el SKU caro; el barato queda pendiente entero.
        self._entrada(dte, 5, talla=talla_destino_b)

        _, listado = self._json(self.URL, situacion='')
        _, detalle = self._json(self.URL_DETALLE, dte_id=dte.id)

        fila = self._fila(listado, 500)
        self.assertEqual(fila['pendientes'], 5)
        self.assertEqual(fila['costo_unitario'], 2000)      # promedio del doc
        self.assertEqual(fila['valor_pendiente'], 10000)

        self.assertEqual(detalle['totales']['pendientes'], 5)
        self.assertEqual(
            detalle['totales']['valor_pendiente'], fila['valor_pendiente'],
            'listado y detalle deben valorizar el mismo despacho igual',
        )
        for linea in detalle['detalle']:
            self.assertEqual(linea['costo_unitario'], 2000)


# ══════════════════════════════════════════════════════════════════════════
# 3. Quiebre de talla: KPI de cabecera conservador
# ══════════════════════════════════════════════════════════════════════════

class KpiConservadorQuiebreTest(TestCase):
    URL = '/app/api/reportes/quiebre-talla/'

    def setUp(self):
        self.hoy = timezone.localdate()
        self.empresa = crear_empresa(nombre='Empresa Calzado', rut='76.111.111-1')
        self.tienda = crear_sucursal(self.empresa, alias='TIENDA-1',
                                     tipo_sucursal='VENDEDORA')
        self.user = crear_usuario(username='jefe_tienda', rol='administrador')
        crear_empresa_user(self.user, self.empresa, self.tienda)

        self._sku = 9100000
        self.client = Client()
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['idSucursalActual'] = self.tienda.id
        sesion['idEmpresaActual'] = self.empresa.id
        sesion.save()

    def _talla_nueva(self, articulo, talla, stock=0):
        self._sku += 1
        _, producto_talla = crear_producto_con_talla(
            self.tienda, articulo=articulo, sku=self._sku, stock=stock,
            talla=talla,
        )
        return producto_talla

    def _mov(self, producto_talla, cantidad, dias_atras, concepto):
        return Movimientos_Producto.objects.create(
            ProductoTalla=producto_talla,
            sucursal_origen=self.tienda,
            cantidad=cantidad, costo=10000, precio=25000,
            concepto=concepto, estado='COMPLETADO',
            fecha=self.hoy - timedelta(days=dias_atras),
        )

    def _json(self, **params):
        params.setdefault('sucursal_id', self.tienda.id)
        params.setdefault('dias', 90)
        with _patch_permisos():
            resp = self.client.get(self.URL, params)
        return resp.status_code, resp.json()

    @staticmethod
    def _celda(data, articulo, talla):
        for estilo in data['estilos']:
            if estilo['articulo_normalizado'] == articulo.upper():
                for fila in estilo['curva']:
                    if fila['talla'] == talla:
                        return estilo, fila
        return None, None

    def test_celda_de_un_dia_no_domina_el_kpi(self):
        """
        El caso BALON CAFU de la auditoría: 300 vendidas con UN día de
        disponibilidad extrapolaban 26.700 u "perdidas" (×90) y eran el 90%
        del KPI del trimestre. La celda se excluye del agregado (dias < 7)
        pero su fila conserva la extrapolación completa.
        """
        # Celda extrema: disponible SOLO ayer, 300 vendidas hoy, stock 0.
        cafu = self._talla_nueva('BALON-CAFU', '5')
        self._mov(cafu, +300, 1, 'RECEPCION_COMPRA')
        self._mov(cafu, -300, 0, 'VENTA_PUBLICO')

        # Celda defendible del mismo estilo: 10 vendidas, 44 días disponible.
        normal = self._talla_nueva('BALON-CAFU', '4')
        self._mov(normal, +10, 44, 'RECEPCION_COMPRA')
        self._mov(normal, -10, 0, 'VENTA_PUBLICO')

        _, data = self._json()
        estilo, celda = self._celda(data, 'BALON-CAFU', '5')
        self.assertIsNotNone(celda)
        self.assertTrue(celda['quiebre'])
        self.assertEqual(celda['dias_disponible'], 1)
        # La FILA no cambia: extrapolación completa, con su % de disponibilidad.
        self.assertAlmostEqual(celda['unidades_perdidas'], 26700.0, places=1)

        _, celda_normal = self._celda(data, 'BALON-CAFU', '4')
        self.assertEqual(celda_normal['dias_disponible'], 44)
        self.assertAlmostEqual(celda_normal['unidades_perdidas'], 10.5, places=1)

        # El estilo (fila de la lista) tampoco cambia: sigue sumando todo.
        self.assertAlmostEqual(estilo['unidades_perdidas'], 26710.5, places=1)

        # El KPI de cabecera SÓLO suma la celda defendible (mult 90/44 < 3x).
        r = data['resumen']
        self.assertAlmostEqual(r['unidades_perdidas_estimadas'], 10.5, delta=0.2)
        ex = r['unidades_perdidas_excluidas']
        self.assertAlmostEqual(ex['baja_disponibilidad'], 26700.0, places=1)
        self.assertEqual(ex['celdas_excluidas'], 1)
        self.assertAlmostEqual(ex['total'], 26700.0, places=1)
        self.assertEqual(ex['min_dias_disponible'], MIN_DIAS_AGREGADO)

    def test_multiplicador_se_capea_a_3x_en_el_agregado(self):
        """
        30 vendidas con 8 días disponibles: la fila extrapola ×11,25 (307,5
        perdidas) pero el agregado capea a 3x (60) y reporta el recorte.
        """
        talla = self._talla_nueva('ZAP-CAP', '40')
        self._mov(talla, +30, 8, 'RECEPCION_COMPRA')
        self._mov(talla, -30, 0, 'VENTA_PUBLICO')

        _, data = self._json()
        _estilo, celda = self._celda(data, 'ZAP-CAP', '40')
        self.assertEqual(celda['dias_disponible'], 8)
        self.assertAlmostEqual(celda['unidades_perdidas'], 307.5, places=1)

        r = data['resumen']
        self.assertAlmostEqual(r['unidades_perdidas_estimadas'], 60.0, places=1)
        ex = r['unidades_perdidas_excluidas']
        self.assertAlmostEqual(ex['cap_multiplicador'], 247.5, places=1)
        self.assertEqual(ex['celdas_capeadas'], 1)
        self.assertEqual(ex['cap'], CAP_MULTIPLICADOR_AGREGADO)

    def test_celda_dudosa_queda_fuera_del_kpi_pero_visible_en_su_fila(self):
        """
        Reconstrucción que pasa por saldo negativo (kardex↔stock descuadrado):
        la fila muestra su estimación con la bandera; el KPI no se la cree.
        """
        talla = self._talla_nueva('ZAP-DUDOSO', '41')
        self._mov(talla, -2, 20, 'VENTA_PUBLICO')       # venta sin respaldo
        self._mov(talla, +10, 10, 'RECEPCION_COMPRA')   # entran 10
        self._mov(talla, -4, 0, 'VENTA_PUBLICO')        # se venden 4, stock 0

        _, data = self._json()
        _estilo, celda = self._celda(data, 'ZAP-DUDOSO', '41')
        self.assertIsNotNone(celda)
        self.assertTrue(celda['quiebre'])
        self.assertTrue(celda['reconstruccion_dudosa'])
        self.assertEqual(celda['dias_disponible'], 10)
        self.assertAlmostEqual(celda['unidades_perdidas'], 48.0, places=1)

        r = data['resumen']
        self.assertEqual(r['unidades_perdidas_estimadas'], 0.0)
        ex = r['unidades_perdidas_excluidas']
        self.assertAlmostEqual(ex['reconstruccion_dudosa'], 48.0, places=1)
        self.assertEqual(ex['celdas_excluidas'], 1)

    def test_celda_sana_no_se_toca(self):
        """Disponibilidad plena y mult < 3x: KPI == fila, nada excluido."""
        talla = self._talla_nueva('ZAP-SANO', '42')
        self._mov(talla, +10, 60, 'RECEPCION_COMPRA')
        self._mov(talla, -10, 0, 'VENTA_PUBLICO')

        _, data = self._json()
        _estilo, celda = self._celda(data, 'ZAP-SANO', '42')
        self.assertEqual(celda['dias_disponible'], 60)

        r = data['resumen']
        self.assertAlmostEqual(
            r['unidades_perdidas_estimadas'], celda['unidades_perdidas'],
            delta=0.2,
        )
        self.assertEqual(r['unidades_perdidas_excluidas']['total'], 0.0)

    def test_resumen_incluye_el_desglose_aunque_no_haya_exclusiones(self):
        """El contrato JSON siempre trae `unidades_perdidas_excluidas`."""
        talla = self._talla_nueva('ZAP-VACIO', '43')
        self._mov(talla, +5, 30, 'RECEPCION_COMPRA')
        self._mov(talla, -5, 0, 'VENTA_PUBLICO')

        _, data = self._json()
        ex = data['resumen']['unidades_perdidas_excluidas']
        for clave in ('total', 'reconstruccion_dudosa', 'baja_disponibilidad',
                      'cap_multiplicador', 'celdas_excluidas', 'celdas_capeadas',
                      'min_dias_disponible', 'cap'):
            self.assertIn(clave, ex)
