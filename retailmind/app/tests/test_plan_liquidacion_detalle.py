"""
Tests del drill-down del Plan de Liquidación: antigüedad FIFO (lote y
fallback a fecha_creacion), buckets de antigüedad, inclusión/exclusión de
bodegas/CD y filtro por especialidad sin duplicar. Aislados en SQLite.
"""
import json
from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from app.models import (
    AtributoOpcion, LoteProducto, Producto, ProductoAtributoValor,
    Productos_Atributos,
)
from app.views_inteligencia_compra import (
    obtener_plan_liquidacion, obtener_plan_liquidacion_detalle,
)
from app.tests.factories import (
    crear_empresa, crear_empresa_user, crear_sucursal, crear_producto_con_talla,
    crear_usuario, crear_lote_fifo,
)


class PlanLiquidacionDetalleTests(TestCase):
    def setUp(self):
        self.empresa = crear_empresa()
        self.tienda = crear_sucursal(empresa=self.empresa, alias='TIENDA-1')
        self.bodega = crear_sucursal(
            empresa=self.empresa, alias='BODEGA-CD',
            es_centro_distribucion=True, tipo_sucursal='CENTRO_DISTRIBUCION')
        self.user = crear_usuario(username='analista')
        crear_empresa_user(self.user, self.empresa, self.tienda)

    def _req(self, **params):
        req = RequestFactory().get('/x', data=params)
        req.user = self.user
        req.session = {'idSucursalActual': self.tienda.id,
                       'idEmpresaActual': self.empresa.id}
        return req

    def _fijar_lote(self, talla, dias_atras, **kw):
        lote = crear_lote_fifo(talla, **kw)
        fecha = timezone.now() - timedelta(days=dias_atras)
        LoteProducto.objects.filter(pk=lote.pk).update(fecha_ingreso=fecha)
        return lote

    def test_antiguedad_desde_lote_y_bucket(self):
        prod, talla = crear_producto_con_talla(
            self.tienda, articulo='VIEJO', sku=7000001, stock=5)
        self._fijar_lote(talla, dias_atras=400)

        data = json.loads(obtener_plan_liquidacion_detalle(self._req()).content)
        self.assertTrue(data['success'])
        fila = next(f for f in data['filas'] if f['articulo'] == 'VIEJO')
        self.assertEqual(fila['antiguedad_fuente'], 'lote')
        self.assertGreaterEqual(fila['dias_antiguedad'], 399)

        # Bucket +1 año lo incluye; 0-90 lo excluye.
        d365 = json.loads(obtener_plan_liquidacion_detalle(
            self._req(antiguedad='365+')).content)
        self.assertTrue(any(f['articulo'] == 'VIEJO' for f in d365['filas']))
        d090 = json.loads(obtener_plan_liquidacion_detalle(
            self._req(antiguedad='0-90')).content)
        self.assertFalse(any(f['articulo'] == 'VIEJO' for f in d090['filas']))

    def test_fallback_fecha_creacion_sin_lote(self):
        prod, talla = crear_producto_con_talla(
            self.tienda, articulo='SINLOTE', sku=7000002, stock=3)
        # Sin lote → antigüedad por fecha_creacion (auto_now_add=hoy).
        data = json.loads(obtener_plan_liquidacion_detalle(self._req()).content)
        fila = next(f for f in data['filas'] if f['articulo'] == 'SINLOTE')
        self.assertEqual(fila['antiguedad_fuente'], 'creacion')
        self.assertIsNotNone(fila['fecha_fifo'])

    def test_cd_excluida_por_defecto_incluida_con_flag(self):
        crear_producto_con_talla(self.tienda, articulo='EN-TIENDA',
                                 sku=7000003, stock=4)
        crear_producto_con_talla(self.bodega, articulo='EN-BODEGA',
                                 sku=7000004, stock=9)

        base = json.loads(obtener_plan_liquidacion_detalle(self._req()).content)
        arts = {f['articulo'] for f in base['filas']}
        self.assertIn('EN-TIENDA', arts)
        self.assertNotIn('EN-BODEGA', arts)  # CD excluida por defecto

        con_cd = json.loads(obtener_plan_liquidacion_detalle(
            self._req(incluir_cd='1')).content)
        arts_cd = {f['articulo'] for f in con_cd['filas']}
        self.assertIn('EN-BODEGA', arts_cd)
        bodega_fila = next(f for f in con_cd['filas'] if f['articulo'] == 'EN-BODEGA')
        self.assertTrue(bodega_fila['es_cd'])

    def test_cd_stock_separado_en_agregado(self):
        crear_producto_con_talla(self.tienda, articulo='T', sku=7000005, stock=4)
        crear_producto_con_talla(self.bodega, articulo='B', sku=7000006, stock=9)
        data = json.loads(obtener_plan_liquidacion(self._req(incluir_cd='1')).content)
        tot = data['data']['totales']
        self.assertEqual(tot['stock_u'], 4)   # solo tiendas
        self.assertEqual(tot['stock_cd'], 9)  # bodega aparte

    def test_filtro_especialidad_no_duplica(self):
        prod, talla = crear_producto_con_talla(
            self.tienda, articulo='CANCHA', sku=7000007, stock=6)
        # Producto con DOS especialidades → no debe contar doble el stock.
        attr = Productos_Atributos.objects.create(
            nombre='Especialidad', descripcion='esp')
        op1 = AtributoOpcion.objects.create(atributo=attr, valor='futbol')
        op2 = AtributoOpcion.objects.create(atributo=attr, valor='pasto')
        ProductoAtributoValor.objects.create(producto=prod, atributo=attr, opcion=op1)
        ProductoAtributoValor.objects.create(producto=prod, atributo=attr, opcion=op2)

        data = json.loads(obtener_plan_liquidacion_detalle(
            self._req(especialidad_id=op1.id)).content)
        filas = [f for f in data['filas'] if f['articulo'] == 'CANCHA']
        self.assertEqual(len(filas), 1)          # una sola fila
        self.assertEqual(filas[0]['stock_u'], 6)  # stock sin duplicar
