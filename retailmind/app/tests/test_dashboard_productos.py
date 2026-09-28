"""
Dashboard de productos (dashboard_productos_mejorado_api + exportar_dashboard_productos).

Deuda de la auditoría de julio 2026 que estos tests fijan:
- el filtro de sucursal llega a TODO el tablero (costo FIFO, valor a venta,
  ventas, top vendidos, tabla) y "Todas" ('') es de verdad sin filtro (antes
  caía a la sucursal de la sesión);
- ABC por VENTAS de 90 días (antes por valor de stock): el que vende es A, el
  que tiene stock y no vende es C;
- reglas transversales de venta: Ticket.created_at, estado PAGADO, sin
  CAMBIO_DEVOLUCION, sin productos con excluir_de_analitica;
- la exportación usa exactamente el mismo universo que la API.
"""
import csv
import io
import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.models import Ticket, Ticket_Productos, Traspaso, Traspaso_Detalle

from .factories import (
    crear_empresa, crear_lote_fifo, crear_producto_con_talla, crear_sucursal,
    crear_usuario, crear_vendedor,
)

API = '/app/dashboard_productos_mejorado_api/'
EXPORT = '/app/exportar_dashboard_productos/'


class DashboardProductosTests(TestCase):
    _correlativo = 0

    @classmethod
    def _venta(cls, sucursal, pt, unidades, precio, dias, modulo='POS', estado='PAGADO'):
        cls._correlativo += 1
        ticket = Ticket.objects.create(
            vendedor=cls.vendedor, sucursal=sucursal, correlativo=cls._correlativo,
            estado=estado, subTotal=unidades * precio, total=unidades * precio,
            responsable='test', modulo_origen=modulo,
        )
        # created_at es auto_now_add: se retrocede a mano para simular la fecha.
        Ticket.objects.filter(pk=ticket.pk).update(created_at=timezone.now() - timedelta(days=dias))
        Ticket_Productos.objects.create(
            ProductoTalla=pt, idTicket=ticket, stock=unidades, precio=precio, subtotal=unidades * precio,
        )

    @classmethod
    def setUpTestData(cls):
        # 'maestro' pasa el middleware de permisos sin sembrar OpcionMenu.
        cls.user = crear_usuario(username='dp_maestro', rol='maestro')
        empresa = crear_empresa()
        cls.suc1 = crear_sucursal(empresa=empresa, alias='S1')
        cls.suc2 = crear_sucursal(empresa=empresa, alias='S2')
        cls.suc3 = crear_sucursal(empresa=empresa, alias='S3')   # sin nada: universo vacío
        cls.vendedor = crear_vendedor(empresa=empresa)

        # Sucursal 1: A vende mucho, C tiene stock y no vende, X está excluido.
        _, cls.ptA = crear_producto_con_talla(cls.suc1, articulo='A-VENDE', sku=1001, stock=10,
                                              costo=1000, precioventa=5000)
        _, cls.ptC = crear_producto_con_talla(cls.suc1, articulo='C-NO-VENDE', sku=1002, stock=20,
                                              costo=1000, precioventa=3000)
        _, cls.ptX = crear_producto_con_talla(cls.suc1, articulo='X-EXCLUIDO', sku=1003, stock=50,
                                              costo=1000, precioventa=9000, excluir_de_analitica=True)
        # Sucursal 2: B vende poco.
        _, cls.ptB = crear_producto_con_talla(cls.suc2, articulo='B-OTRA-SUC', sku=2001, stock=5,
                                              costo=2000, precioventa=8000)
        crear_lote_fifo(cls.ptA, cantidad=10, costo_unitario=1000)
        crear_lote_fifo(cls.ptC, cantidad=20, costo_unitario=1000)
        crear_lote_fifo(cls.ptX, cantidad=50, costo_unitario=1000)
        crear_lote_fifo(cls.ptB, cantidad=5, costo_unitario=2000)

        cls._venta(cls.suc1, cls.ptA, 8, 5000, dias=10)                                   # 40.000 -> A
        cls._venta(cls.suc2, cls.ptB, 1, 8000, dias=10)                                   # 8.000 en S2
        cls._venta(cls.suc1, cls.ptC, 1, 3000, dias=10, modulo='CAMBIO_DEVOLUCION')       # no es venta
        cls._venta(cls.suc1, cls.ptC, 1, 3000, dias=10, estado='PENDIENTE')               # no pagado
        cls._venta(cls.suc1, cls.ptX, 5, 9000, dias=10)                                   # excluido
        cls._venta(cls.suc1, cls.ptC, 9, 3000, dias=120)                                  # fuera de 90 d

        # Traspaso S1 -> S2 (fecha_solicitud es auto_now_add: entra en el período).
        traspaso = Traspaso.objects.create(
            sucursal_origen=cls.suc1, sucursal_destino=cls.suc2, numero_traspaso=1,
            solicitante='test', estado='EN_TRANSITO',
        )
        Traspaso_Detalle.objects.create(
            traspaso=traspaso, producto_talla=cls.ptA, cantidad_solicitada=2, cantidad_enviada=2,
            costo=1000, precio_venta=5000,
        )

    def setUp(self):
        self.client.force_login(self.user)
        # Sesión con sucursal activa: antes '?sucursal=' caía a este valor.
        sesion = self.client.session
        sesion['idSucursalActual'] = self.suc1.id
        sesion['idEmpresaActual'] = self.suc1.empresa_id
        sesion['alias'] = 'S1'
        sesion.save()

    def _api(self, **params):
        respuesta = self.client.get(API, params)
        self.assertEqual(respuesta.status_code, 200, respuesta.content[:300])
        datos = json.loads(respuesta.content)
        self.assertTrue(datos['success'], datos.get('error'))
        return datos

    def _csv(self, **params):
        respuesta = self.client.get(EXPORT, params)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment; filename="dashboard_productos_', respuesta['Content-Disposition'])
        contenido = b''.join(respuesta.streaming_content).decode('utf-8')
        self.assertTrue(contenido.startswith('﻿'), 'BOM para Excel')
        return list(csv.reader(io.StringIO(contenido.lstrip('﻿')))), respuesta

    # ---------- sucursal: un solo universo ----------

    def test_sucursal_filtra_costo_venta_ventas_y_tabla_del_mismo_universo(self):
        datos = self._api(sucursal=self.suc1.id, periodo=30)
        k = datos['kpis']
        self.assertEqual(k['total_skus'], 2)                    # A y C; X excluido
        self.assertEqual(k['con_stock'], 2)
        self.assertEqual(k['valor_costo'], 30000.0)             # lotes de A y C (no B, no X)
        self.assertEqual(k['valor_venta'], 110000.0)            # 10x5000 + 20x3000
        self.assertEqual(k['margen_potencial'], 80000.0)
        self.assertEqual(k['rotacion'], round(8 / 30, 2))       # 8 vendidas / 30 en stock
        self.assertEqual(k['velocidad_venta'], 0.3)             # 8 u / 30 d
        self.assertEqual(datos['filtros_aplicados']['sucursal_id'], self.suc1.id)

        self.assertEqual([p['id'] for p in datos['top_vendidos']], [self.ptA.id])
        self.assertEqual(datos['top_vendidos'][0]['ventas'], 8)
        self.assertEqual(datos['top_vendidos'][0]['ingresos'], 40000.0)

        tabla = {p['id']: p for p in datos['productos']}
        self.assertEqual(set(tabla), {self.ptA.id, self.ptC.id})
        self.assertEqual(tabla[self.ptA.id]['ventas_periodo'], 8)
        self.assertEqual(tabla[self.ptC.id]['ventas_periodo'], 0)   # cambio, pendiente y 120 d no cuentan

    def test_otra_sucursal_ve_solo_lo_suyo(self):
        datos = self._api(sucursal=self.suc2.id, periodo=30)
        k = datos['kpis']
        self.assertEqual(k['total_skus'], 1)
        self.assertEqual(k['valor_costo'], 10000.0)
        self.assertEqual(k['valor_venta'], 40000.0)
        self.assertEqual([p['id'] for p in datos['top_vendidos']], [self.ptB.id])
        self.assertEqual([p['id'] for p in datos['bajo_stock']], [self.ptB.id])

    def test_traspasos_recientes_solo_donde_participa_la_sucursal(self):
        # El bloque de traspasos está dentro de un try/except que traga todo:
        # si el filtro por sucursal fallara, saldría vacío en silencio.
        for sucursal, esperado in ((self.suc1, 1), (self.suc2, 1), (self.suc3, 0)):
            traspasos = self._api(sucursal=sucursal.id, periodo=30)['traspasos_recientes']
            self.assertEqual(len(traspasos), esperado, sucursal.alias)
        traspasos = self._api(sucursal=self.suc1.id, periodo=30)['traspasos_recientes']
        self.assertEqual((traspasos[0]['origen'], traspasos[0]['destino'], traspasos[0]['unidades']),
                         ('S1', 'S2', 2))
        self.assertEqual(len(self._api(sucursal='', periodo=30)['traspasos_recientes']), 1)

    def test_todas_es_sin_filtro_aunque_la_sesion_tenga_sucursal(self):
        datos = self._api(sucursal='', periodo=30)
        k = datos['kpis']
        self.assertEqual(k['total_skus'], 3)                    # A, C y B
        self.assertEqual(k['valor_costo'], 40000.0)
        self.assertEqual(k['valor_venta'], 150000.0)
        self.assertIsNone(datos['filtros_aplicados']['sucursal_id'])
        self.assertEqual({p['id'] for p in datos['top_vendidos']}, {self.ptA.id, self.ptB.id})

    # ---------- ABC por ventas ----------

    def test_abc_es_por_ventas_de_90_dias_no_por_valor_de_stock(self):
        # Por valor de stock C (60.000) le ganaría a A (50.000); por ventas no.
        datos = self._api(sucursal=self.suc1.id, periodo=30)
        self.assertEqual(datos['abc'], {'a': 1, 'b': 0, 'c': 1})
        self.assertEqual(datos['abc_meta']['skus_con_venta'], 1)
        self.assertEqual(datos['abc_meta']['sin_venta_con_stock'], 1)
        self.assertEqual(datos['abc_meta']['ingresos_total'], 40000)
        self.assertEqual(datos['abc_meta']['dias'], 90)
        tabla = {p['id']: p for p in datos['productos']}
        self.assertEqual(tabla[self.ptA.id]['abc'], 'A')
        self.assertEqual(tabla[self.ptC.id]['abc'], 'C')
        self.assertEqual(datos['top_vendidos'][0]['abc'], 'A')

    def test_abc_toda_la_red_reparte_a_b_c(self):
        # A: 40.000 (83 %) -> A; B: 8.000 (acumulado previo 83 %) -> B; C sin venta -> C.
        datos = self._api(sucursal='', periodo=30)
        self.assertEqual(datos['abc'], {'a': 1, 'b': 1, 'c': 1})
        self.assertEqual(datos['kpis']['abc_a'], 1)
        self.assertEqual(datos['kpis']['abc_c'], 1)

    # ---------- exportación ----------

    def test_exportacion_usa_el_mismo_universo_que_la_api(self):
        filas, respuesta = self._csv(sucursal=self.suc1.id, periodo=30)
        self.assertIn('_S1_', respuesta['Content-Disposition'])
        cabecera, cuerpo = filas[0], filas[1:]
        self.assertEqual(cabecera[:6], ['Producto', 'SKU', 'Talla', 'Categoría', 'Sucursal', 'Stock'])
        self.assertIn('Ventas (u, 30 d)', cabecera)
        self.assertEqual([f[0] for f in cuerpo], ['A-VENDE', 'C-NO-VENDE'])   # sin X ni B
        por_nombre = {f[0]: dict(zip(cabecera, f)) for f in cuerpo}
        a = por_nombre['A-VENDE']
        self.assertEqual(a['Sucursal'], 'S1')
        self.assertEqual(a['Stock'], '10')
        self.assertEqual(a['Ventas (u, 30 d)'], '8')
        self.assertEqual(a['Ingresos (30 d)'], '40000')
        self.assertEqual(a['ABC ventas (90 d)'], 'A')
        self.assertEqual(por_nombre['C-NO-VENDE']['ABC ventas (90 d)'], 'C')

        filas_red, respuesta_red = self._csv(sucursal='', periodo=30)
        self.assertEqual(len(filas_red) - 1, 3)
        self.assertIn('_red_', respuesta_red['Content-Disposition'])

    def test_exportacion_respeta_estado_stock(self):
        filas, _ = self._csv(sucursal='', estado_stock='bajo')   # solo B (stock 5)
        self.assertEqual([f[0] for f in filas[1:]], ['B-OTRA-SUC'])

    # ---------- robustez y página ----------

    def test_parametros_basura_no_rompen(self):
        datos = self._api(sucursal='abc', periodo='xyz', estado_stock='zzz', categoria='')
        self.assertEqual(datos['filtros_aplicados'],
                         {'sucursal_id': None, 'categoria_id': None, 'estado_stock': '', 'periodo_dias': 30})
        self.assertEqual(datos['kpis']['total_skus'], 3)

    def test_pagina_preselecciona_sucursal_de_sesion(self):
        respuesta = self.client.get('/app/dashboard_productos_mejorado/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "let sucursalInicial = '%s';" % self.suc1.id)
        self.assertContains(respuesta, 'ABC por ventas (90 d)')
        # Los botones de la tabla ya no llaman a rutas inexistentes (404) sino a
        # pantallas que existen y aceptan el SKU / la talla por GET.
        self.assertNotContains(respuesta, 'onclick="verDetalle(')
        self.assertNotContains(respuesta, 'onclick="editarProducto(')
        self.assertContains(respuesta, '/app/trazabilidad-producto/?sku=')
        self.assertContains(respuesta, '/app/lotes_producto/')
