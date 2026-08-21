"""
Tests de la FASE C de la auditoría de Reportes (ago-2026) — P2/P3.

Cubren los 4 fixes con lógica de datos:

1. Tab vendedores: una NC sin vendedor propio se imputa al vendedor del
   `documento_afectado` (cascada de comisiones); solo la NC realmente huérfana
   cae a la fila "Sin vendedor". La plata total del reporte no cambia: solo se
   mueve de fila.
2. productos-origen: la query DISTINCT ON / ROW_NUMBER produce EXACTAMENTE los
   mismos totales que un cálculo naive sobre el fixture, los grupos son
   exhaustivos (suman el total) e INGRESO_MANUAL ya no se marca "irregular".
3. Compras: con una orden de compra SIN DTE presente, la tabla de proveedores
   suma == KPI de inversión (el costo de la OC viaja aparte en
   `costo_ordenes_sin_dte`).
4. ventas-internet: un pedido FACTURADO_EXTERNO sin ticket PAGADO aparece en
   el KPI/fila aparte y NO suma al total principal.

Correr SOLO contra SQLite:
    $env:DATABASE_URL="sqlite:///C:/temp/tc1.sqlite3"
    python manage.py test app.tests.test_fase_c_reportes
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from app.models import (
    Compras, Compras_Producto, Compras_Producto_Talla, Dte,
    Movimientos_Producto, PedidoEcommerce, Ticket, TicketDetallePago,
)
from app.views_modulo_reportes import _queryset_dtes_ventas_vendedor
from .factories import (
    crear_empresa, crear_empresa_user, crear_producto_con_talla,
    crear_sucursal, crear_usuario, crear_vendedor,
)


def _patch_permisos():
    """Fuerza los permisos a True: acá se prueba la lógica de datos, no los gates."""
    return mock.patch(
        'app.middleware_permisos.PermisoRol.tiene_permiso', return_value=True
    )


def _crear_dte(emisor, receptor, sucursal, numero, monto, tipo_transaccion,
               tipo_documento='FACTURA', fecha=None, vendedor=None, **kwargs):
    defaults = dict(
        emisor=emisor,
        receptor=receptor,
        numero_documento=numero,
        tipo_documento=tipo_documento,
        monto_neto=Decimal(monto),
        monto_con_iva=Decimal(monto),
        estado_pago='PENDIENTE',
        estado_dte='EMITIDO',
        responsable='tester',
        fecha_emision=fecha or timezone.localdate(),
        fecha_vencimiento=fecha or timezone.localdate(),
        diasCredito=0,
        bultos=1,
        unidades_productos=1,
        tipo_transaccion=tipo_transaccion,
        sucursal=sucursal,
        vendedor=vendedor,
    )
    defaults.update(kwargs)
    return Dte.objects.create(**defaults)


def _crear_movimiento(producto_talla, sucursal, concepto, cantidad, fecha,
                      costo=1000, tipo_movimiento='INGRESO'):
    return Movimientos_Producto.objects.create(
        ProductoTalla=producto_talla,
        sucursal_origen=sucursal,
        cantidad=cantidad,
        costo=costo,
        precio=costo * 2,
        concepto=concepto,
        tipo_movimiento=tipo_movimiento,
        estado='COMPLETADO',
        fecha=fecha,
    )


class BaseFaseC(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.empresa = crear_empresa(nombre='Empresa C', rut='76.900.900-9')
        self.sucursal = crear_sucursal(self.empresa, alias='TIENDA-C')
        self.user = crear_usuario(username='jefe_c', rol='jefe_local')
        crear_empresa_user(self.user, self.empresa, self.sucursal)
        self.cliente_http = Client()
        self._login(self.user, self.sucursal, self.empresa)

    def _login(self, usuario, sucursal, empresa):
        self.cliente_http.force_login(usuario)
        sesion = self.cliente_http.session
        sesion['idSucursalActual'] = sucursal.id
        sesion['idEmpresaActual'] = empresa.id
        sesion['alias'] = sucursal.alias
        sesion.save()

    def _json(self, url, **params):
        with _patch_permisos():
            resp = self.cliente_http.get(url, params)
        return resp.status_code, resp.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1) Tab vendedores: NC imputada a la persona
# ═══════════════════════════════════════════════════════════════════════════

class NCImputadaAlVendedorTest(BaseFaseC):
    URL = '/app/api/reportes/ventas-por-vendedor/'

    def setUp(self):
        super().setUp()
        self.mes = self.hoy.strftime('%Y-%m')
        cliente_final = crear_empresa(nombre='Cliente final', rut='77.111.222-3')

        self.vend_v = crear_vendedor('Vendedora V', empresa=self.empresa,
                                     codigo_vendedor='V-01', rut='11.111.111-1')
        self.vend_w = crear_vendedor('Vendedor W', empresa=self.empresa,
                                     codigo_vendedor='W-01', rut='22.222.222-2')

        # Venta del mes con vendedor V.
        self.venta_v = _crear_dte(
            self.empresa, cliente_final, self.sucursal, 9001, 100000,
            'VENTA_PUBLICO', tipo_documento='BOLETA ELECTRONICA',
            vendedor=self.vend_v,
        )
        # NC sin vendedor propio pero con documento afectado de V → imputa a V.
        self.nc_v = _crear_dte(
            self.empresa, cliente_final, self.sucursal, 9002, 30000,
            'DEVOLUCION', tipo_documento='NOTA DE CREDITO',
            documento_afectado=self.venta_v, es_nota_credito=True,
        )
        # NC huérfana (sin vendedor NI documento afectado) → fila Sin vendedor.
        self.nc_huerfana = _crear_dte(
            self.empresa, cliente_final, self.sucursal, 9003, 7000,
            'DEVOLUCION', tipo_documento='NOTA DE CREDITO', es_nota_credito=True,
        )
        # Venta del MES PASADO de W + NC de ESTE mes → fila W con neto negativo.
        self.venta_w = _crear_dte(
            self.empresa, cliente_final, self.sucursal, 9004, 50000,
            'VENTA_PUBLICO', tipo_documento='BOLETA ELECTRONICA',
            vendedor=self.vend_w, fecha=self.hoy - timedelta(days=40),
        )
        self.nc_w = _crear_dte(
            self.empresa, cliente_final, self.sucursal, 9005, 5000,
            'DEVOLUCION', tipo_documento='NOTA DE CREDITO',
            documento_afectado=self.venta_w, es_nota_credito=True,
        )

    def _filas(self):
        status, data = self._json(self.URL, mes=self.mes)
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
        return data, {v['nombre']: v for v in data['vendedores']}

    def test_nc_con_documento_afectado_resta_al_vendedor_original(self):
        data, filas = self._filas()
        fila_v = filas['Vendedora V']
        self.assertEqual(fila_v['ventas_brutas'], 100000)
        self.assertEqual(fila_v['devoluciones'], 30000)
        self.assertEqual(fila_v['cantidad_devoluciones'], 1)
        self.assertEqual(fila_v['ventas'], 70000)

    def test_nc_sin_documento_imputable_cae_a_sin_vendedor(self):
        data, filas = self._filas()
        fila_sin = filas['Sin vendedor asignado']
        self.assertTrue(fila_sin['sin_vendedor'])
        # SOLO la huérfana: las otras dos NC ya tienen dueño.
        self.assertEqual(fila_sin['devoluciones'], 7000)
        self.assertEqual(fila_sin['cantidad_devoluciones'], 1)

    def test_nc_de_vendedor_sin_ventas_del_mes_crea_fila_negativa(self):
        data, filas = self._filas()
        fila_w = filas['Vendedor W']
        self.assertFalse(fila_w['sin_vendedor'])
        self.assertEqual(fila_w['ventas_brutas'], 0)
        self.assertEqual(fila_w['devoluciones'], 5000)
        self.assertEqual(fila_w['ventas'], -5000)

    def test_la_plata_total_no_cambia_solo_se_mueve_de_fila(self):
        data, filas = self._filas()
        # Σ devoluciones del mes = 30.000 + 7.000 + 5.000 (ninguna se pierde
        # ni se duplica al imputar).
        self.assertEqual(
            sum(v['devoluciones'] for v in data['vendedores']), 42000)
        self.assertEqual(
            sum(v['ventas_brutas'] for v in data['vendedores']), 100000)
        # KPI = neto imputable (excluye la fila Sin vendedor).
        self.assertEqual(data['kpis']['total_ventas'], 100000 - 30000 - 5000)
        self.assertEqual(data['kpis']['total_devoluciones'], 35000)

    def test_drilldown_del_vendedor_incluye_la_nc_imputada(self):
        rf = RequestFactory()
        req = rf.get('/x')
        req.user = self.user
        req.session = {'idSucursalActual': self.sucursal.id}
        fi = self.hoy.replace(day=1)
        qs = _queryset_dtes_ventas_vendedor(req, fi, self.hoy,
                                            vendedor_id=self.vend_v.id)
        ids = set(qs.values_list('id', flat=True))
        self.assertIn(self.venta_v.id, ids)
        self.assertIn(self.nc_v.id, ids)          # imputada por doc. afectado
        self.assertNotIn(self.nc_huerfana.id, ids)
        self.assertNotIn(self.nc_w.id, ids)


# ═══════════════════════════════════════════════════════════════════════════
# 2) productos-origen: mismos totales que un cálculo naive
# ═══════════════════════════════════════════════════════════════════════════

class ProductosOrigenNaiveTest(BaseFaseC):
    URL = '/app/api/reportes/productos-origen/'

    def setUp(self):
        super().setUp()
        self.anio = self.hoy.year
        d = lambda dias: self.hoy - timedelta(days=dias)

        # t1: nace este año por RECEPCION_COMPRA; el 2º movimiento no cuenta.
        _, self.t1 = crear_producto_con_talla(self.sucursal, articulo='ART-1', sku=3000001)
        _crear_movimiento(self.t1, self.sucursal, 'RECEPCION_COMPRA', 10, d(20), costo=1000)
        _crear_movimiento(self.t1, self.sucursal, 'INGRESO_MANUAL', 4, d(5), costo=999)

        # t2: nació el AÑO PASADO (no cuenta este año aunque tenga reposición ahora).
        _, self.t2 = crear_producto_con_talla(self.sucursal, articulo='ART-2', sku=3000002)
        _crear_movimiento(self.t2, self.sucursal, 'INGRESO_MANUAL', 8, d(400), costo=700)
        _crear_movimiento(self.t2, self.sucursal, 'REPOSICION_STOCK', 3, d(10), costo=700)

        # t3: alta normal por INGRESO_MANUAL (recepción-DTE) este año.
        _, self.t3 = crear_producto_con_talla(self.sucursal, articulo='ART-3', sku=3000003)
        _crear_movimiento(self.t3, self.sucursal, 'INGRESO_MANUAL', 5, d(15), costo=500)

        # t4: nace por ajuste (grupo revisar).
        _, self.t4 = crear_producto_con_talla(self.sucursal, articulo='ART-4', sku=3000004)
        _crear_movimiento(self.t4, self.sucursal, 'AJUSTE_INVENTARIO', 2, d(9), costo=300)

        # t5: concepto fuera del mapa clásico — debe ser visible y agrupado.
        _, self.t5 = crear_producto_con_talla(self.sucursal, articulo='ART-5', sku=3000005)
        _crear_movimiento(self.t5, self.sucursal, 'ANULACION_REGULARIZACION', 1, d(3), costo=200)

    def _naive(self):
        """Cálculo de referencia: primer INGRESO COMPLETADO por SKU, en Python."""
        primeros = {}
        movs = Movimientos_Producto.objects.filter(
            tipo_movimiento='INGRESO', estado='COMPLETADO',
        ).order_by('fecha', 'hora', 'id')
        for m in movs:
            primeros.setdefault(m.ProductoTalla_id, m)
        resumen = {'skus': 0, 'unidades': 0, 'costo': 0.0}
        por_concepto = {}
        for m in primeros.values():
            if not m.fecha or m.fecha.year != self.anio:
                continue
            uds = abs(int(m.cantidad or 0))
            costo = float(m.costo or 0) * uds
            resumen['skus'] += 1
            resumen['unidades'] += uds
            resumen['costo'] += costo
            c = por_concepto.setdefault(m.concepto, {'skus': 0, 'unidades': 0, 'costo': 0.0})
            c['skus'] += 1
            c['unidades'] += uds
            c['costo'] += costo
        return resumen, por_concepto

    def test_totales_identicos_al_calculo_naive(self):
        esperado, por_concepto = self._naive()
        status, data = self._json(self.URL, anio=self.anio)
        self.assertEqual(status, 200)
        r = data['resumen']
        self.assertEqual(r['total_skus'], esperado['skus'])          # 4 SKUs
        self.assertEqual(r['total_unidades'], esperado['unidades'])  # 10+5+2+1
        self.assertEqual(r['total_costo'], round(esperado['costo']))
        filas = {o['concepto']: o for o in data['por_origen']}
        self.assertEqual(set(filas), set(por_concepto))
        for concepto, v in por_concepto.items():
            self.assertEqual(filas[concepto]['skus'], v['skus'], concepto)
            self.assertEqual(filas[concepto]['unidades'], v['unidades'], concepto)
            self.assertEqual(filas[concepto]['costo'], round(v['costo']), concepto)

    def test_sku_nacido_el_anio_pasado_no_cuenta_aunque_reponga_este_anio(self):
        status, data = self._json(self.URL, anio=self.anio)
        conceptos = {o['concepto'] for o in data['por_origen']}
        self.assertNotIn('REPOSICION_STOCK', conceptos)
        self.assertEqual(data['resumen']['total_skus'], 4)

    def test_ingreso_manual_es_flujo_normal_sin_alerta(self):
        status, data = self._json(self.URL, anio=self.anio)
        filas = {o['concepto']: o for o in data['por_origen']}
        self.assertFalse(filas['INGRESO_MANUAL']['alerta'])
        self.assertEqual(filas['INGRESO_MANUAL']['grupo'], 'alta')
        self.assertFalse(filas['RECEPCION_COMPRA']['alerta'])
        self.assertTrue(filas['AJUSTE_INVENTARIO']['alerta'])
        self.assertEqual(filas['AJUSTE_INVENTARIO']['grupo'], 'ajuste')

    def test_grupos_exhaustivos_y_concepto_no_mapeado_visible(self):
        """ANULACION_REGULARIZACION no puede quedar como 'Otro' invisible."""
        status, data = self._json(self.URL, anio=self.anio)
        filas = {o['concepto']: o for o in data['por_origen']}
        fila = filas['ANULACION_REGULARIZACION']
        self.assertEqual(fila['grupo'], 'ajuste')
        self.assertNotEqual(fila['origen'], 'Otro')
        # Los grupos cubren el 100% de los SKU (las barras suman 100).
        self.assertEqual(
            sum(o['skus'] for o in data['por_origen']),
            data['resumen']['total_skus'],
        )
        self.assertTrue(all(o.get('grupo') for o in data['por_origen']))


# ═══════════════════════════════════════════════════════════════════════════
# 3) Compras: tabla == KPI con OC sin DTE presente
# ═══════════════════════════════════════════════════════════════════════════

class ComprasTablaIgualKpiTest(BaseFaseC):
    URL = '/app/api/reporte-compras/'

    def setUp(self):
        super().setUp()
        # La sucursal activa debe ser compradora (centro de distribución).
        self.cd = crear_sucursal(self.empresa, alias='CD-C', es_centro_distribucion=True)
        self._login(self.user, self.cd, self.empresa)

        self.prov_dte = crear_empresa(nombre='Proveedor Facturado',
                                      rut='78.100.100-1', esProveedor=True)
        self.prov_oc = crear_empresa(nombre='Proveedor Solo OC',
                                     rut='78.200.200-2', esProveedor=True)

        # Factura de compra recibida (inversión facturada = 100.000 neto).
        _crear_dte(self.prov_dte, self.empresa, self.cd, 7001, 100000,
                   'COMPRA', estado_dte='ACEPTADO', monto_con_iva=Decimal(119000))

        # Orden de compra SIN DTE: 10 u × $2.000 = $20.000 pedidos.
        oc = Compras.objects.create(
            empresa=self.prov_oc, nombre='OC invierno', correlativo=1,
            responsable='tester', temporada='Invierno', fecha=self.hoy,
            estado='ACTIVA',
        )
        cp = Compras_Producto.objects.create(
            compras=oc, nombre='BOTIN X', atributo1='M', atributo2='N',
            atributo3='C', atributo4='G', costo=2000, precioSugerido=4000,
        )
        Compras_Producto_Talla.objects.create(compra_producto=cp, stock=10, talla='42')

    def test_tabla_de_proveedores_suma_igual_al_kpi(self):
        status, data = self._json(self.URL, anio=self.hoy.year, periodo='anual')
        self.assertEqual(status, 200)
        self.assertTrue(data['success'])
        kpi = data['metricas']['inversion_total']
        tabla = sum(p['inversion'] for p in data['top_proveedores'])
        self.assertEqual(round(tabla), round(kpi))
        self.assertEqual(round(kpi), 100000)

    def test_oc_sin_dte_viaja_aparte_y_no_participa(self):
        status, data = self._json(self.URL, anio=self.hoy.year, periodo='anual')
        filas = {p['nombre']: p for p in data['top_proveedores']}
        solo_oc = filas['Proveedor Solo OC']
        self.assertTrue(solo_oc['solo_ordenes'])
        self.assertEqual(round(solo_oc['costo_ordenes_sin_dte']), 20000)
        self.assertEqual(round(solo_oc['inversion']), 0)
        self.assertEqual(solo_oc['participacion'], 0)
        facturado = filas['Proveedor Facturado']
        self.assertFalse(facturado.get('solo_ordenes'))
        self.assertEqual(facturado['participacion'], 100.0)

    def test_comparativa_usa_la_misma_ventana_desplazada(self):
        """Con periodo relativo, el año anterior NO ancla al 31-dic."""
        from app.views_modulo_reportes import _rango_desplazado_anios
        filtros = {'anio': self.hoy.year,
                   'fecha_inicio': self.hoy - timedelta(days=30),
                   'fecha_fin': self.hoy}
        inicio, fin = _rango_desplazado_anios(filtros, self.hoy.year - 1)
        self.assertEqual(inicio.month, (self.hoy - timedelta(days=30)).month)
        self.assertEqual(inicio.year, (self.hoy - timedelta(days=30)).year - 1)
        self.assertEqual((fin.month, fin.year), (self.hoy.month, self.hoy.year - 1))


# ═══════════════════════════════════════════════════════════════════════════
# 4) ventas-internet: FACTURADO_EXTERNO aparte
# ═══════════════════════════════════════════════════════════════════════════

class VentasInternetFacturadoExternoTest(BaseFaseC):
    URL = '/app/api/reportes/ventas-internet/'

    def setUp(self):
        super().setUp()
        self.vendedor = crear_vendedor('Vend Internet', empresa=self.empresa,
                                       codigo_vendedor='VI-1', rut='33.333.333-3')
        self.vendedor.sucursales.add(self.sucursal)

        # Venta internet normal: ticket PAGADO con pago VENTA_INTERNET.
        self.ticket = Ticket.objects.create(
            vendedor=self.vendedor, sucursal=self.sucursal, correlativo=501,
            estado='PAGADO', subTotal=40000, total=40000, responsable='tester',
            modulo_origen='ECOMMERCE',
        )
        TicketDetallePago.objects.create(
            ticket=self.ticket, metodo_pago='VENTA_INTERNET', monto=40000,
            tipo_tarjeta='Paris',
        )

        # Pedido FACTURADO_EXTERNO SIN ticket → sección aparte.
        self.pedido_fe = PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-FE-1', numero_pedido_canal='CANAL-1',
            canal_origen='RIPLEY', sucursal=self.sucursal,
            cliente_nombre='Cliente FE', total=Decimal('50000'),
            estado='FACTURADO', sub_estado='FACTURADO_EXTERNO',
        )
        # Pedido FACTURADO_EXTERNO pero CON ticket PAGADO: ya está contado en
        # el total principal → NO debe duplicarse en la sección aparte.
        PedidoEcommerce.objects.create(
            numero_ticket_rm='RM-FE-2', numero_pedido_canal='CANAL-2',
            canal_origen='RIPLEY', sucursal=self.sucursal,
            cliente_nombre='Cliente OK', total=Decimal('40000'),
            estado='FACTURADO', sub_estado='FACTURADO_EXTERNO',
            ticket=self.ticket,
        )

    def _params(self):
        inicio = (self.hoy - timedelta(days=7)).isoformat()
        return {'fecha_inicio': inicio, 'fecha_fin': self.hoy.isoformat()}

    def test_facturado_externo_aparece_en_kpi_aparte(self):
        status, data = self._json(self.URL, **self._params())
        self.assertEqual(status, 200)
        fe = data['facturado_externo']
        self.assertEqual(fe['pedidos'], 1)
        self.assertEqual(round(fe['monto']), 50000)
        self.assertEqual(fe['detalle'][0]['numero_ticket_rm'], 'RM-FE-1')
        self.assertEqual(data['resumen']['facturado_externo_pedidos'], 1)
        self.assertEqual(round(data['resumen']['facturado_externo_monto']), 50000)

    def test_facturado_externo_no_suma_al_total_principal(self):
        status, data = self._json(self.URL, **self._params())
        # El total principal solo trae el ticket pagado ($40.000): el pedido
        # externo NO lo infla (rompería el cuadre con los pagos).
        self.assertEqual(round(data['resumen']['venta_internet']), 40000)
        self.assertEqual(data['resumen']['tickets'], 1)

    def test_pedido_externo_con_ticket_pagado_no_se_duplica(self):
        status, data = self._json(self.URL, **self._params())
        rms = [d['numero_ticket_rm'] for d in data['facturado_externo']['detalle']]
        self.assertNotIn('RM-FE-2', rms)

    def test_filtro_origen_pos_vacia_la_seccion(self):
        status, data = self._json(self.URL, origen='POS', **self._params())
        self.assertEqual(data['facturado_externo']['pedidos'], 0)
