"""
Tests de los filtros nuevos de `/app/dte/recepciones_pendientes/`:
búsqueda por número de documento o contraparte, atajo "con problemas" y
atajo pendientes / recepcionados.

El caso que motiva la búsqueda: `Dte.numero_documento` es IntegerField, así
que escribir "711" no encuentra el folio 17117 salvo que se castee a texto.
"""
import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase, Client

from app.models import Dte, Dte_Productos, Movimientos_Producto, Producto_Talla
from .factories import (
    crear_usuario, crear_empresa, crear_sucursal, crear_empresa_user,
    crear_producto_con_talla,
)

URL = '/app/dte/recepciones_pendientes/'


class FiltrosRecepcionDteTest(TestCase):
    def setUp(self):
        self.user = crear_usuario(rol='administrador')
        self.empresa = crear_empresa()
        self.origen = crear_sucursal(self.empresa, alias='ORIGEN')
        self.destino = crear_sucursal(self.empresa, alias='DESTINO')
        crear_empresa_user(self.user, self.empresa, self.destino)

        _, self.talla = crear_producto_con_talla(
            self.origen, articulo='Zapatilla Test', talla='40', sku=4001, stock=100,
        )

        self.client = Client()
        self.client.force_login(self.user)
        s = self.client.session
        s['idSucursalActual'] = self.destino.id
        s['idEmpresaActual'] = self.empresa.id
        s.save()

        # Tres documentos con folios y estados distintos.
        self.dte_pendiente = self._crear_dte(17117, 'EMITIDO')
        self.dte_ok = self._crear_dte(17118, 'RECEPCIONADO_COMPLETO')
        self.dte_problema = self._crear_dte(29999, 'RECEPCIONADO_PARCIAL')

    def _crear_dte(self, numero, estado):
        dte = Dte.objects.create(
            emisor=self.empresa,
            receptor=self.empresa,
            numero_documento=numero,
            tipo_documento='FACTURA ELECTRONICA',
            monto_neto=Decimal('10000'),
            monto_con_iva=Decimal('11900'),
            estado_pago='PENDIENTE',
            estado_dte=estado,
            responsable='tester',
            fecha_emision='2026-08-24',
            fecha_vencimiento='2026-08-24',
            diasCredito=0,
            bultos=1,
            unidades_productos=10,
            tipo_transaccion='TRASPASO',
            sucursal=self.origen,
        )
        Dte_Productos.objects.create(
            dte=dte, productoTalla=self.talla, descripcion='Zapatilla Test - Talla 40',
            costo=100, sobreprecio=0, precio=1000, stock=10, activo=True,
        )
        Movimientos_Producto.objects.create(
            dte=dte, ProductoTalla=self.talla,
            sucursal_origen=self.origen, sucursal_destino=self.destino,
            cantidad=-10, concepto='TRASPASO_SALIDA', tipo_movimiento='EGRESO',
            estado='COMPLETADO', responsable='tester', fecha='2026-08-24',
        )
        return dte

    def _get(self, **params):
        with mock.patch('app.views.PermisoRol.tiene_permiso', return_value=True):
            resp = self.client.get(URL, params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return json.loads(resp.content)

    def _folios(self, data):
        return sorted(int(i['numero_documento']) for i in data.get('items', []))

    # ---------------------------------------------------------------- búsqueda
    def test_busqueda_por_folio_exacto(self):
        data = self._get(buscar='17117')
        self.assertEqual(self._folios(data), [17117])

    def test_busqueda_parcial_por_el_medio_del_folio(self):
        """El caso que obliga al cast: 711 está DENTRO de 17117."""
        data = self._get(buscar='711')
        self.assertIn(17117, self._folios(data))
        self.assertNotIn(29999, self._folios(data))

    def test_busqueda_ignora_el_gato_inicial(self):
        data = self._get(buscar='#17118')
        self.assertEqual(self._folios(data), [17118])

    def test_busqueda_por_nombre_de_la_contraparte(self):
        data = self._get(buscar=self.empresa.nombre[:6])
        self.assertEqual(len(self._folios(data)), 3)

    def test_busqueda_sin_resultados_no_revienta(self):
        data = self._get(buscar='NOEXISTE-999')
        self.assertEqual(self._folios(data), [])

    def test_busqueda_vacia_no_filtra(self):
        self.assertEqual(len(self._folios(self._get(buscar='   '))), 3)

    # ---------------------------------------------------------------- atajos
    def test_filtro_con_problemas(self):
        data = self._get(con_problemas='1')
        self.assertEqual(self._folios(data), [29999])

    def test_filtro_fase_pendientes(self):
        data = self._get(fase='pendientes')
        self.assertEqual(self._folios(data), [17117])

    def test_filtro_fase_recepcionados(self):
        """Incluye parcial y sobrante: 'recepcionado' es que llegó, no que
        haya llegado perfecto."""
        data = self._get(fase='recepcionados')
        self.assertEqual(self._folios(data), [17118, 29999])

    def test_los_filtros_se_componen_con_la_busqueda(self):
        data = self._get(con_problemas='1', buscar='17117')
        self.assertEqual(self._folios(data), [],
                         'con_problemas y buscar deben intersectarse, no sumarse')
